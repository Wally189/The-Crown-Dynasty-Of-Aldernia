import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.execution_contracts import (
    PROFILE_BALCONY_RETURN,
    PROFILE_GOVERNMENT_PULSE,
    PROFILE_HEARTBEAT_PATROL,
)
from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.unattended_worker import run_once

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
GOV_EVENT = "government.daily-pulse:2026-09-21"
RED_BOX_EVENT = "government.red-box:2026-09-21"
HEARTBEAT = "dynasty.heartbeat:2026-09-21T06:00"


def encoded(value):
    return json.dumps(value, separators=(",", ":"))


HEARTBEAT_CONTRACT = encoded(
    {
        "v": 1,
        "duty_id": "dynasty.heartbeat",
        "profile": PROFILE_HEARTBEAT_PATROL,
        "engine_mode": "SPECIALIST",
        "catchup": "LATEST_SUPERSEDES_PRIOR",
    }
)
GOV_CONTRACT = encoded(
    {
        "v": 1,
        "duty_id": "government.daily-pulse",
        "profile": PROFILE_GOVERNMENT_PULSE,
        "engine_mode": "DECLARED",
        "catchup": "SAME_DAY_ALLOWED",
        "successor": {
            "duty_id": "government.red-box",
            "parent_case_prefix": "government-pulse",
            "terminal_map": {
                "VERIFIED_CLOSED": "government.pulse.closed",
                "FAILED_CLOSED": "government.pulse.failed_closed",
            },
        },
    }
)
RED_CONTRACT = encoded(
    {
        "v": 1,
        "duty_id": "government.red-box",
        "profile": PROFILE_BALCONY_RETURN,
        "engine_mode": "DECLARED",
        "catchup": "SAME_DAY_ALLOWED",
    }
)


def task_row(assigned_engine, contract):
    row = ["test"] * 12
    row[3] = assigned_engine
    row[11] = contract
    return row


class FakeDrive:
    def __init__(self):
        self.rows = {
            2: task_row("", HEARTBEAT_CONTRACT),
            12: task_row(
                "House of Marianne — Centre of Government & Cabinet Coordination",
                GOV_CONTRACT,
            ),
            13: task_row(
                "House of Marianne — Centre of Government → Royal Palace",
                RED_CONTRACT,
            ),
        }
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
        return list(self.rows[row_number])

    def write_run_state(self, *, row_number, last_run, outcome, evidence):
        self.writes.append((row_number, last_run, outcome, evidence))
        return f"Drive:test-row-{row_number}"


class FakeModel:
    def __init__(self, fail_government=False):
        self.fail_government = fail_government
        self.calls = []

    def execute(
        self,
        prompt,
        *,
        public_web_read=False,
        profile_instruction="",
    ):
        self.calls.append(prompt)
        self.public_web_flags = getattr(self, "public_web_flags", [])
        self.public_web_flags.append(public_web_read)
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
        raise AssertionError("unexpected model duty")

    def select_engine(self, prompt):
        raise AssertionError("Government regression uses DECLARED routing")


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
        state_path = root / "scheduled-duty-queue.json"
        state = json.loads(state_path.read_text())
        state["events"] = {
            event_id: event
            for event_id, event in state["events"].items()
            if event_id in {GOV_EVENT, HEARTBEAT}
        }
        state_path.write_text(json.dumps(state))

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
            model = FakeModel()
            result = run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=model,
                worker_id="github-clock-runtime-test",
                max_events=2,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 2)
            self.assertIn(
                "1XdilhHRYu5OQHqHs7uPvLKV5TlZ9vwxVqd3gFqJw4XU",
                model.calls[0],
            )
            self.assertIn(
                "1Z6jkA6SuaPDAQYWAAYEkjjRGg00wP8KF3odrf-fktxU",
                model.calls[0],
            )
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            gov = state["events"][GOV_EVENT]
            red = state["events"][RED_BOX_EVENT]
            self.assertEqual(gov["status"], "ACKNOWLEDGED_ACTION")
            self.assertEqual(
                gov["domain_terminal_state"], "VERIFIED_CLOSED"
            )
            self.assertEqual(
                gov["receipt"]["domain_terminal_state"], "VERIFIED_CLOSED"
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
            self.assertEqual(
                gov["receipt"]["execution_profile"],
                PROFILE_GOVERNMENT_PULSE,
            )
            self.assertEqual(
                red["receipt"]["execution_profile"],
                PROFILE_BALCONY_RETURN,
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

    def test_successor_creation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            drive = FakeDrive()
            model = FakeModel()
            run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="github-clock-runtime-test",
                max_events=2,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="github-clock-runtime-test",
                max_events=2,
                now=datetime(2026, 9, 21, 6, 3, tzinfo=timezone.utc),
            )
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            red_ids = [
                event_id
                for event_id in state["events"]
                if event_id.startswith("government.red-box:")
            ]
            self.assertEqual(red_ids, [RED_BOX_EVENT])

    def test_public_web_is_explicitly_gated_by_projection(self):
        timetable = json.loads(TIMETABLE.read_text())
        duties = {duty["id"]: duty for duty in timetable["duties"]}
        self.assertTrue(duties["josie.morning-news"]["public_web_read"])
        self.assertTrue(duties["catholic.mass-readings"]["public_web_read"])
        self.assertFalse(
            bool(duties["government.daily-pulse"].get("public_web_read"))
        )
        self.assertFalse(
            bool(duties["carol.business-opening"].get("public_web_read"))
        )

    def test_ordinary_worker_skips_specialist_profile_not_duty_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_government(root)
            run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=FakeModel(),
                worker_id="github-clock-runtime-test",
                max_events=1,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            self.assertEqual(
                state["events"][HEARTBEAT]["status"], "PENDING_RUNTIME"
            )
            self.assertEqual(
                state["events"][GOV_EVENT]["status"], "ACKNOWLEDGED_ACTION"
            )


if __name__ == "__main__":
    unittest.main()
