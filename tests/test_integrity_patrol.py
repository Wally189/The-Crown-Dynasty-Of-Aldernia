import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.patrol import REVIEW_TRIGGER, stable_conflict_id
from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.session import halt
from aldernia_runtime.worker import (
    MODEL_RUNTIME_CLASS,
    WorkerError,
    acknowledge_event,
    claim_event,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
HEARTBEAT_0600 = "dynasty.heartbeat:2026-09-21T06:00"
HEARTBEAT_0700 = "dynasty.heartbeat:2026-09-21T07:00"


class IntegrityPatrolTests(unittest.TestCase):
    def paths(self, root: Path):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
        }

    def seed_heartbeat(self, root: Path):
        run_scheduler(
            **self.paths(root),
            now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),  # 06:01 BST
        )

    def claim_heartbeat(self, root: Path, event_id: str = HEARTBEAT_0600):
        return claim_event(
            **self.paths(root),
            event_id=event_id,
            worker_id="connected-governed-test-runtime",
            runtime_class=MODEL_RUNTIME_CLASS,
            now=datetime(2026, 9, 21, 5, 2, tzinfo=timezone.utc),
        )

    def result_for(self, claim, findings=None):
        plan = claim["integrity_patrol"]
        return {
            "outcome": "ACTION",
            "result_summary": "Heartbeat and one bounded integrity patrol slice executed.",
            "retrieved_source_ids": list(claim["required_source_ids"]),
            "evidence_refs": ["test:integrity-patrol"],
            "writes": ["test:patrol-receipt"],
            "readback_verified": True,
            "resource_classes_used": ["ALDERNIA_INTERNAL"],
            "external_effect": "NONE",
            "new_model_provider_access_granted": False,
            "integrity_patrol": {
                "registry_version": plan["registry_version"],
                "source_row": plan["source_row"],
                "estate_id": plan["estate_id"],
                "visit_index": plan["visit_index"],
                "cursor_after": plan["cursor_after"],
                "current_authority_retrieved": True,
                "royal_household_accessed": False,
                "readback_verified": True,
                "findings": list(findings or []),
            },
            "balcony_pulse": {
                "institutions_checked": [
                    "Royal Palace",
                    "House of Carol",
                    "House of Marianne",
                ],
                "material_changes": [],
                "crown_attention": [],
                "readback_verified": True,
            },
        }

    def test_scheduler_marks_native_heartbeat_with_patrol_obligation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            payload = state["events"][HEARTBEAT_0600]["payload"]
            self.assertEqual(payload["integrity_patrol"]["source_row"], 21)
            self.assertEqual(
                payload["integrity_patrol"]["mode"],
                "ONE_BOUNDED_SLICE_PER_HEARTBEAT",
            )

    def test_first_claim_selects_carol_and_requires_current_authorities(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            plan = claim["integrity_patrol"]
            self.assertEqual(plan["estate_id"], "house-of-carol")
            self.assertEqual(plan["visit_index"], 1)
            self.assertEqual(plan["root_folder_ids"], ["10Qn-cOnkSVc8rCAZohWYhcjw6xPNa0Uo"])
            self.assertEqual(plan["cursor_after"], "house-of-tony")
            self.assertFalse(plan["royal_household_access_permitted"])
            self.assertIn(
                "15sUjJSU9z5YaXgMecV-2PeQw2x-2JfmDiTGXoM-T6S8",
                claim["required_source_ids"],
            )
            self.assertIn(
                "1XdilhHRYu5OQHqHs7uPvLKV5TlZ9vwxVqd3gFqJw4XU",
                claim["required_source_ids"],
            )
            self.assertIn(
                "1gS-V40twMOT29dBiXrNrNlo87dUD49u9t2e9yHGOS98",
                claim["required_source_ids"],
            )

    def test_ack_advances_durable_cursor_to_next_estate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            first = self.claim_heartbeat(root)
            acknowledge_event(
                **self.paths(root),
                event_id=HEARTBEAT_0600,
                claim_id=first["claim_id"],
                execution_result=self.result_for(first),
                now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
            )
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),  # 07:01 BST
            )
            second = claim_event(
                **self.paths(root),
                event_id=HEARTBEAT_0700,
                worker_id="connected-governed-test-runtime",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(second["integrity_patrol"]["estate_id"], "house-of-tony")
            self.assertEqual(second["integrity_patrol"]["visit_index"], 1)

    def test_archive_requires_provenance_readback_and_no_delete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            finding = {
                "classification": "STALE/SUPERSEDED",
                "source_refs": ["Drive:test-stale-record"],
                "disposition": "ARCHIVED",
                "provenance_preserved": True,
                "readback_verified": True,
                "destructive_delete": False,
            }
            ack = acknowledge_event(
                **self.paths(root),
                event_id=HEARTBEAT_0600,
                claim_id=claim["claim_id"],
                execution_result=self.result_for(claim, [finding]),
                now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
            )
            self.assertEqual(ack["status"], "ACKNOWLEDGED_ACTION")

    def test_conflict_requires_stable_deduplicated_king_queue_route(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            refs = ["Drive:authority-A", "Drive:authority-B"]
            proposition = "Two current controlling records assign different owners."
            conflict_id = stable_conflict_id(proposition, refs)
            finding = {
                "classification": "CONFLICTING CURRENT MATERIAL",
                "source_refs": refs,
                "proposition": proposition,
                "conflict_id": conflict_id,
                "disposition": "QUEUED_FOR_CROWN",
                "queue_review_trigger": REVIEW_TRIGGER,
                "queue_ref": "KQ-PATROL-ACCEPTANCE-001",
                "queue_deduplicated": True,
                "readback_verified": True,
            }
            acknowledge_event(
                **self.paths(root),
                event_id=HEARTBEAT_0600,
                claim_id=claim["claim_id"],
                execution_result=self.result_for(claim, [finding]),
                now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
            )
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            receipt = state["events"][HEARTBEAT_0600]["receipt"]["integrity_patrol"]
            self.assertEqual(receipt["findings"][0]["conflict_id"], conflict_id)
            self.assertEqual(receipt["findings"][0]["queue_review_trigger"], REVIEW_TRIGGER)

    def test_heartbeat_ack_fails_without_balcony_pulse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            result = self.result_for(claim)
            del result["balcony_pulse"]
            with self.assertRaises(WorkerError):
                acknowledge_event(
                    **self.paths(root),
                    event_id=HEARTBEAT_0600,
                    claim_id=claim["claim_id"],
                    execution_result=result,
                    now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
                )

    def test_heartbeat_ack_fails_without_patrol_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            result = self.result_for(claim)
            del result["integrity_patrol"]
            with self.assertRaises(WorkerError):
                acknowledge_event(
                    **self.paths(root),
                    event_id=HEARTBEAT_0600,
                    claim_id=claim["claim_id"],
                    execution_result=result,
                    now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
                )

    def test_royal_household_access_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            claim = self.claim_heartbeat(root)
            result = self.result_for(claim)
            result["integrity_patrol"]["royal_household_accessed"] = True
            with self.assertRaises(WorkerError):
                acknowledge_event(
                    **self.paths(root),
                    event_id=HEARTBEAT_0600,
                    claim_id=claim["claim_id"],
                    execution_result=result,
                    now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
                )

    def test_halt_still_dominates_patrol_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_heartbeat(root)
            halt(
                root / "bus-session.json",
                now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),
                reason="TEST HALT",
            )
            with self.assertRaises(WorkerError):
                self.claim_heartbeat(root)


if __name__ == "__main__":
    unittest.main()
