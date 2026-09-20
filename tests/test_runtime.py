import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.runtime import BusEnvelope, BusValidationError, process_one_cycle

HERE = Path(__file__).resolve().parents[1]
EVENT = HERE / "aldernia" / "events" / "inbox" / "ald-first-living-cycle-2026-09-20.json"


class RuntimeTests(unittest.TestCase):
    def raw(self):
        return json.loads(EVENT.read_text(encoding="utf-8"))

    def test_envelope_has_stable_idempotency_and_real_selector(self):
        event = BusEnvelope.from_mapping(self.raw())
        self.assertEqual(event.engine_plan["selector"], "COS-SEL-001")
        self.assertEqual(
            event.idempotency_key,
            "urn:aldernia:crown:living-build-commission#ald-first-living-cycle-2026-09-20",
        )

    def test_shadow_selector_fails_closed(self):
        raw = copy.deepcopy(self.raw())
        raw["engine_plan"]["selector"] = "shadow-selector"
        with self.assertRaises(BusValidationError):
            BusEnvelope.from_mapping(raw)

    def test_first_event_processes_once_then_deduplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "event.json").write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
            kwargs = dict(
                state_path=root / "bus-state.json",
                inbox_dir=inbox,
                public_state_path=root / "public-state.json",
                releases_dir=root / "releases",
                now=datetime(2026, 9, 20, 19, 20, tzinfo=timezone.utc),
            )
            self.assertEqual(process_one_cycle(**kwargs)["status"], "ACCEPTED")
            self.assertEqual(process_one_cycle(**kwargs)["status"], "NO_OP")
            public = json.loads((root / "public-state.json").read_text())
            self.assertTrue(public["human_moment"]["enabled"])

    def test_existing_receipt_recovers_lost_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "event.json").write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
            state = root / "bus-state.json"
            kwargs = dict(
                state_path=state,
                inbox_dir=inbox,
                public_state_path=root / "public-state.json",
                releases_dir=root / "releases",
                now=datetime(2026, 9, 20, 19, 20, tzinfo=timezone.utc),
            )
            process_one_cycle(**kwargs)
            state.unlink()
            self.assertEqual(process_one_cycle(**kwargs)["status"], "RECOVERED_ACK")


if __name__ == "__main__":
    unittest.main()
