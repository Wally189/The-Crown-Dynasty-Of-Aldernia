import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aldernia_runtime.session import (
    activate_command,
    activate_timed,
    complete,
    evaluate,
    halt,
    load_session,
)


class BusSessionTests(unittest.TestCase):
    def test_default_is_dormant(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.json"
            state, status = evaluate(p, now=datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc))
            self.assertEqual((state["operating_state"], status), ("DORMANT", "DORMANT"))

    def test_timed_lease_expires_at_thirty_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.json"
            start = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
            activate_timed(p, now=start)
            self.assertEqual(evaluate(p, now=start + timedelta(minutes=29))[1], "ACTIVE_TIMED")
            state, status = evaluate(p, now=start + timedelta(minutes=30))
            self.assertEqual(status, "TIMED_EXPIRED")
            self.assertEqual(state["operating_state"], "DORMANT")

    def test_command_lease_continues_past_thirty_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.json"
            start = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
            activate_command(
                p,
                now=start,
                mission_id="royal-palace-build",
                command_ref="Build the Royal Palace",
                accountable_owner="The Royal Palace",
                acceptance=["Palace build verified complete and recorded"],
            )
            state, status = evaluate(p, now=start + timedelta(minutes=31))
            self.assertEqual(status, "ACTIVE_COMMAND_CHECKPOINT")
            self.assertEqual(state["operating_state"], "ACTIVE_COMMAND")
            self.assertEqual(state["mission_id"], "royal-palace-build")

    def test_halt_overrides_command_session(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.json"
            start = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
            activate_command(
                p,
                now=start,
                mission_id="royal-palace-build",
                command_ref="Build the Royal Palace",
                accountable_owner="The Royal Palace",
                acceptance=["Palace build verified complete and recorded"],
            )
            halt(p, now=start + timedelta(minutes=5))
            self.assertEqual(evaluate(p, now=start + timedelta(minutes=6))[1], "HALTED")

    def test_verified_complete_returns_dormant(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.json"
            start = datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
            activate_command(
                p,
                now=start,
                mission_id="royal-palace-build",
                command_ref="Build the Royal Palace",
                accountable_owner="The Royal Palace",
                acceptance=["Palace build verified complete and recorded"],
            )
            complete(p, now=start + timedelta(hours=2))
            state = load_session(p)
            self.assertEqual(state["operating_state"], "DORMANT")
            self.assertEqual(state["mission_id"], "royal-palace-build")


if __name__ == "__main__":
    unittest.main()
