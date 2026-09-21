import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.unattended_worker import _next_non_patrol, run_once

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
GOV_EVENT = "government.daily-pulse:2026-09-21"
RED_BOX_EVENT = "government.red-box:2026-09-21"


class FakeDrive:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def read_text(self, file_id, *, max_chars):
        return (
            {
                "id": file_id,
                "name": f"Source {file_id}",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"Current controlled source content for {file_id}",
        )

    def scheduled_row(self, row_number):
        return self.rows.setdefault(row_number, ["test"] * 11)

    def write_run_state(self, *, row_number, last_run, outcome, evidence):
        self.writes.append((row_number, last_run, outcome, evidence))
        return f"Drive:test-row-{row_number}"


class FakeModel:
    def __init__(self, fail_government=False):
        self.fail_government = fail_government
        self.calls = []

    def execute(self, prompt):
        self.calls.append(prompt)
        if "DUTY_ID: government.daily-pulse" in prompt:
            if self.fail_government:
                return {
                    "outcome": "STOP",
                    "result_summary": (
                        "Government pulse stopped on a controlled test dependency."
                    ),
                    "evidence_refs": ["test:government-stop"],
                    "domain_terminal_state": "FAILED_CLOSED",
                    "red_box_content": None,
                    "crown_action_required": False,
                }
            return {
                "outcome": "ACTION",
                "result_summary": (
                    "Government pulse completed from current controlled truth."
                ),
                "evidence_refs": ["test:government-complete"],
                "domain_terminal_state": "VERIFIED_CLOSED",
                "red_box_content": None,
                "crown_action_required": False,
            }
        if "DUTY_ID: government.red-box" in prompt:
            is_exception = "FAILED_CLOSED" in prompt
            return {
                "outcome": "ACTION",
                "result_summary": (
                    "Government Red Box delivered to the King's Balcony."
                ),
                "evidence_refs": ["test:red-box"],
                "domain_terminal_state": None,
                "red_box_content": (
                    "DAILY ALDERNIAN GOVERNMENT RED BOX — EXCEPTION — controlled test stop."
                    if is_exception
                    else (
                        "DAILY ALDERNIAN GOVERNMENT RED BOX — Government pulse "
                        "completed; no Crown action required."
                    )
                ),
                "crown_action_required": False,
            }
        return {
            "outcome": "NO_ACTION",
            "result_summary": "No material action due in this controlled test.",
            "evidence_refs": ["test:no-action"],
            "domain_terminal_state": None,
            "red_box_content": None,
            "crown_action_required": False,
        }


class ClockToRuntimeTests(unittest.TestCase):
    def paths(self, root):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
        }

    def seed_government(self, root):
        run_scheduler(
            **self.paths(root),
            now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),
        )

    def test_scheduler_does_not_emit_event_child_from_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            self.assertIn(GOV_EVENT, state["events"])
            self.assertNotIn(RED_BOX_EVENT, state["events"])

    def test_clock_event_runs_without_manual_claim_and_red_box_acks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            result = run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=FakeModel(),
                worker_id="github-clock-runtime-test",
                max_events=2,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 2)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            gov = state["events"][GOV_EVENT]
            red = state["events"][RED_BOX_EVENT]
            self.assertEqual(gov["status"], "ACKNOWLEDGED_ACTION")
            self.assertEqual(
                gov["domain_terminal_state"],
                "VERIFIED_CLOSED",
            )
            self.assertEqual(
                gov["receipt"]["domain_terminal_state"],
                "VERIFIED_CLOSED",
            )
            self.assertEqual(red["event_type"], "government.pulse.closed")
            self.assertEqual(red["status"], "ACKNOWLEDGED_ACTION")
            self.assertTrue(red["receipt"]["balcony_ack"])
            self.assertTrue(
                gov["receipt"]["red_box_delivery"]["balcony_ack"]
            )
            self.assertIn(
                "DAILY ALDERNIAN GOVERNMENT RED BOX",
                red["receipt"]["red_box_content"],
            )

    def test_failed_government_emits_exception_child(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=FakeModel(fail_government=True),
                worker_id="github-clock-runtime-test",
                max_events=2,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            self.assertEqual(
                state["events"][GOV_EVENT]["domain_terminal_state"],
                "FAILED_CLOSED",
            )
            self.assertEqual(
                state["events"][RED_BOX_EVENT]["event_type"],
                "government.pulse.failed_closed",
            )
            self.assertIn(
                "EXCEPTION",
                state["events"][RED_BOX_EVENT]["receipt"][
                    "red_box_content"
                ],
            )

    def test_generic_worker_skips_heartbeat_for_specialist_patrol_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            candidate = _next_non_patrol(**self.paths(root))
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate["event_id"], GOV_EVENT)


if __name__ == "__main__":
    unittest.main()
