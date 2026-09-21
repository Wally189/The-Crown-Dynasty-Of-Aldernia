import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.session import halt
from aldernia_runtime.worker import (
    COMMON_REQUIRED_SOURCE_IDS,
    MODEL_RUNTIME_CLASS,
    WorkerError,
    acknowledge_event,
    claim_event,
    recover_expired_claim,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
MASS_EVENT = "catholic.mass-readings:2026-09-21"
CATHOLIC_FIRST_READ = "1tUVJfn1r5joQEgGTmG8frxmQXLuOmyNzUY8gqL1oSVk"


class GovernedWorkerTests(unittest.TestCase):
    def paths(self, root: Path):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
        }

    def seed_mass(self, root: Path):
        run_scheduler(
            **self.paths(root),
            now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),  # 06:31 BST
        )

    def valid_result(self):
        return {
            "outcome": "ACTION",
            "result_summary": "Bounded task executed and verified.",
            "retrieved_source_ids": list(COMMON_REQUIRED_SOURCE_IDS)
            + [CATHOLIC_FIRST_READ],
            "evidence_refs": ["test:bounded-execution"],
            "writes": ["test:durable-result"],
            "readback_verified": True,
            "resource_classes_used": ["ALDERNIA_INTERNAL", "PUBLIC_READ_ONLY"],
            "external_effect": "NONE",
            "new_model_provider_access_granted": False,
        }

    def test_model_runtime_boundary_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            with self.assertRaises(WorkerError):
                claim_event(
                    **self.paths(root),
                    event_id=MASS_EVENT,
                    worker_id="deterministic-runner",
                    runtime_class="DETERMINISTIC_INTERNAL_RUNTIME",
                    now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
                )

    def test_claim_preserves_accountable_owner_and_does_not_create_mission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            claim = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
            )
            self.assertEqual(claim["accountable_owner"], "House of Catholic")
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            event = state["events"][MASS_EVENT]
            self.assertEqual(event["claim"]["mission_ref"], "NONE — DUE EVALUATION ONLY")
            self.assertFalse(event["claim"]["new_model_provider_access_granted"])
            self.assertEqual(event["claim"]["estate_scope"], "ALDERNIA")

    def test_source_retrieval_is_acceptance_critical(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            claim = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
            )
            result = self.valid_result()
            result["retrieved_source_ids"].remove(CATHOLIC_FIRST_READ)
            with self.assertRaises(WorkerError):
                acknowledge_event(
                    **self.paths(root),
                    event_id=MASS_EVENT,
                    claim_id=claim["claim_id"],
                    execution_result=result,
                    now=datetime(2026, 9, 21, 5, 33, tzinfo=timezone.utc),
                )

    def test_closed_estate_rejects_outside_resource_class(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            claim = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
            )
            result = self.valid_result()
            result["resource_classes_used"].append("OUTSIDE_ALDERNIA")
            with self.assertRaises(WorkerError):
                acknowledge_event(
                    **self.paths(root),
                    event_id=MASS_EVENT,
                    claim_id=claim["claim_id"],
                    execution_result=result,
                    now=datetime(2026, 9, 21, 5, 33, tzinfo=timezone.utc),
                )

    def test_halt_dominates_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            halt(
                root / "bus-session.json",
                now=datetime(2026, 9, 21, 5, 31, tzinfo=timezone.utc),
                reason="TEST HALT",
            )
            with self.assertRaises(WorkerError):
                claim_event(
                    **self.paths(root),
                    event_id=MASS_EVENT,
                    worker_id="chatgpt-helm-current-session",
                    runtime_class=MODEL_RUNTIME_CLASS,
                    now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
                )

    def test_ack_is_durable_and_duplicate_terminal_is_suppressed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            claim = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
            )
            ack = acknowledge_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                claim_id=claim["claim_id"],
                execution_result=self.valid_result(),
                now=datetime(2026, 9, 21, 5, 33, tzinfo=timezone.utc),
            )
            self.assertEqual(ack["status"], "ACKNOWLEDGED_ACTION")
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            self.assertTrue(state["events"][MASS_EVENT]["receipt"]["readback_verified"])
            duplicate = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=datetime(2026, 9, 21, 5, 34, tzinfo=timezone.utc),
            )
            self.assertEqual(duplicate["status"], "DUPLICATE_TERMINAL")

    def test_restart_resumes_unexpired_claim_then_recovers_expired_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            first_time = datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc)
            claim = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=first_time,
            )
            resumed = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=first_time + timedelta(minutes=5),
            )
            self.assertEqual(resumed["status"], "RESUMED_CLAIM")
            self.assertEqual(resumed["claim_id"], claim["claim_id"])

            recovered = recover_expired_claim(
                **self.paths(root),
                event_id=MASS_EVENT,
                now=first_time + timedelta(minutes=31),
            )
            self.assertEqual(recovered["status"], "PENDING_RUNTIME")
            self.assertEqual(recovered["recovery"], "RECOVERED_TO_PENDING")

            second = claim_event(
                **self.paths(root),
                event_id=MASS_EVENT,
                worker_id="chatgpt-helm-current-session",
                runtime_class=MODEL_RUNTIME_CLASS,
                now=first_time + timedelta(minutes=32),
            )
            self.assertEqual(second["attempt"], 2)
            failed = recover_expired_claim(
                **self.paths(root),
                event_id=MASS_EVENT,
                now=first_time + timedelta(minutes=63),
            )
            self.assertEqual(failed["status"], "FAILED_CLOSED")
            self.assertEqual(failed["recovery"], "FAILED_CLOSED")

    def test_owner_conflict_in_event_projection_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed_mass(root)
            state_path = root / "scheduled-duty-queue.json"
            state = json.loads(state_path.read_text())
            state["events"][MASS_EVENT]["payload"]["accountable_owner"] = "Wrong House"
            state_path.write_text(json.dumps(state))
            with self.assertRaises(WorkerError):
                claim_event(
                    **self.paths(root),
                    event_id=MASS_EVENT,
                    worker_id="chatgpt-helm-current-session",
                    runtime_class=MODEL_RUNTIME_CLASS,
                    now=datetime(2026, 9, 21, 5, 32, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
