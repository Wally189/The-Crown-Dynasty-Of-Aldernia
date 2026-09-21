import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.session import halt, load_session

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"


class NativeSchedulerTests(unittest.TestCase):
    def paths(self, root: Path):
        return {
            "timetable_path": TIMETABLE,
            "state_path": root / "scheduled-duty-queue.json",
            "session_path": root / "bus-session.json",
        }

    def test_initial_boot_uses_current_local_day_not_historic_backlog(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 4, 1, tzinfo=timezone.utc),  # 05:01 BST
            )
            self.assertEqual(
                result["new_pending_events"],
                ["catholic.dawn-offices:2026-09-21"],
            )
            self.assertFalse(result["implicit_mission_created"])

    def test_waking_heartbeat_and_morning_news_are_native_due_events(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 4, 1, tzinfo=timezone.utc),
            )
            result = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),  # 06:01 BST
            )
            self.assertEqual(
                result["new_pending_events"],
                [
                    "dynasty.heartbeat:2026-09-21T06:00",
                    "josie.morning-news:2026-09-21",
                ],
            )

    def test_half_hour_mass_readings_and_government_exact_wake(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),
            )
            mass = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),  # 06:31 BST
            )
            self.assertEqual(
                mass["new_pending_events"],
                ["catholic.mass-readings:2026-09-21"],
            )
            government = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),  # 07:01 BST
            )
            self.assertEqual(
                government["new_pending_events"],
                [
                    "dynasty.heartbeat:2026-09-21T07:00",
                    "government.daily-pulse:2026-09-21",
                ],
            )

    def test_repeat_evaluation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),
            )
            second = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),
            )
            self.assertGreater(len(first["new_pending_events"]), 0)
            self.assertEqual(second["new_pending_events"], [])

    def test_halt_suppresses_due_events_without_creating_missions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session_path = root / "bus-session.json"
            halt(
                session_path,
                now=datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc),
                reason="TEST HALT",
            )
            result = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),
            )
            self.assertEqual(result["status"], "HALTED")
            self.assertEqual(result["new_pending_events"], [])
            self.assertGreater(len(result["new_suppressed_events"]), 0)
            session = load_session(session_path)
            self.assertEqual(session["operating_state"], "HALTED")
            self.assertIsNone(session["mission_id"])

    def test_22_hour_uses_palace_timetable_not_stray_2215_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 20, 1, tzinfo=timezone.utc),  # 21:01 BST
            )
            result = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 21, 1, tzinfo=timezone.utc),  # 22:01 BST
            )
            self.assertEqual(
                result["new_pending_events"],
                [
                    "carol.availability-gate:2026-09-21",
                    "dynasty.heartbeat:2026-09-21T22:00",
                ],
            )

    def test_compline_is_due_before_dynasty_sleep(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 21, 1, tzinfo=timezone.utc),  # 22:01 BST
            )
            result = run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 22, 1, tzinfo=timezone.utc),  # 23:01 BST
            )
            self.assertEqual(
                result["new_pending_events"],
                ["catholic.compline:2026-09-21"],
            )

    def test_queue_records_drive_authority_and_model_runtime_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),
            )
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            event = state["events"]["catholic.mass-readings:2026-09-21"]
            self.assertEqual(event["status"], "PENDING_RUNTIME")
            self.assertEqual(event["transition_class"], "scheduled_duty_due")
            self.assertEqual(event["mission_ref"], "NONE — DUE EVALUATION ONLY")
            self.assertTrue(event["payload"]["requires_model_runtime"])
            self.assertIn("Scheduled Tasks row 14", event["authority_ref"])


if __name__ == "__main__":
    unittest.main()
