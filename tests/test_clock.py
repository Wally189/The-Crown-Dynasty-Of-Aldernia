import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from central_aldernia_clock.clock import CentralAlderniaClock, ClockStateError

class ClockTests(unittest.TestCase):
    def test_utc_snapshot_is_aware_and_unambiguous(self):
        s = CentralAlderniaClock().snapshot(datetime(2026, 9, 20, 18, 45, tzinfo=timezone.utc))
        self.assertEqual(s.utc_timestamp, "2026-09-20T18:45:00+00:00")
        self.assertEqual(s.timezone, "Europe/London")

    def test_amt_bst_projection(self):
        s = CentralAlderniaClock().snapshot(datetime(2026, 9, 20, 18, 45, tzinfo=timezone.utc))
        self.assertEqual(s.amt_timestamp, "2026-09-20T19:45:00+01:00")
        self.assertEqual((s.utc_offset, s.civil_regime), ("+01:00", "BST"))

    def test_amt_gmt_projection(self):
        s = CentralAlderniaClock().snapshot(datetime(2026, 12, 20, 18, 45, tzinfo=timezone.utc))
        self.assertEqual(s.amt_timestamp, "2026-12-20T18:45:00+00:00")
        self.assertEqual((s.utc_offset, s.civil_regime), ("+00:00", "GMT"))

    def test_snapshot_does_not_advance_tick(self):
        c = CentralAlderniaClock()
        c.snapshot(); c.snapshot()
        self.assertEqual(c.dynasty_tick, 0)

    def test_tick_advances_monotonically(self):
        c = CentralAlderniaClock()
        self.assertEqual(c.advance_tick(), (1, 0))
        self.assertEqual(c.advance_tick(), (2, 1))

    def test_persistence_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            c = CentralAlderniaClock(p); c.advance_tick(); c.advance_tick()
            c2 = CentralAlderniaClock(p)
            self.assertEqual((c2.dynasty_tick, c2.previous_dynasty_tick), (2, 1))

    def test_corrupt_or_backwards_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text(json.dumps({"state_version": 1, "dynasty_tick": 2, "previous_dynasty_tick": 3, "last_event_id": None}))
            with self.assertRaises(ClockStateError):
                CentralAlderniaClock(p)

    def test_elapsed_uses_monotonic_values(self):
        self.assertEqual(CentralAlderniaClock.elapsed_seconds(10.0, 12.5), 2.5)
        with self.assertRaises(ValueError):
            CentralAlderniaClock.elapsed_seconds(12.5, 10.0)

    def test_due_and_compare_are_timezone_safe(self):
        now = datetime.fromisoformat("2026-09-20T19:00:00+01:00")
        due = datetime.fromisoformat("2026-09-20T18:30:00+00:00")
        self.assertFalse(CentralAlderniaClock.is_due(now, due))
        later = datetime.fromisoformat("2026-09-20T20:00:00+01:00")
        self.assertTrue(CentralAlderniaClock.is_due(later, due))
        self.assertEqual(CentralAlderniaClock.compare(now, due), -1)

if __name__ == "__main__":
    unittest.main()
