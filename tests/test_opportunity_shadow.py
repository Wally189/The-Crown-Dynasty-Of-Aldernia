import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from aldernia_runtime.opportunity_shadow import (
    PROGRAMME_BOARD_FACTS,
    normalized_runtime_facts,
    run_shadow,
)
from aldernia_runtime.opportunity_resolver import StateClass


def pending(event_id="dynasty.heartbeat:2026-09-21T20:00", *, model=True):
    return {
        "event_id": event_id,
        "status": "PENDING_RUNTIME",
        "authority_ref": "Royal Palace Scheduled Tasks Register row 2",
        "mission_ref": "NONE — DUE EVALUATION ONLY",
        "scheduled_for": "2026-09-21T20:00:00+01:00",
        "emitting_owner": "Central Aldernia Clock / Scheduled Tasks",
        "payload": {
            "duty_id": "dynasty.heartbeat",
            "accountable_owner": "Royal Palace",
            "requires_model_runtime": model,
            "source_row": 2,
        },
    }


class OpportunityShadowTests(unittest.TestCase):
    def test_programme_board_facts_are_completed_not_new_work(self):
        self.assertEqual({f.objective_id for f in PROGRAMME_BOARD_FACTS}, {"E-23", "E-24"})
        self.assertTrue(all(f.kind == "COMPLETED_NO_CONSEQUENCE" for f in PROGRAMME_BOARD_FACTS))

    def test_kq009_fingerprint_ignores_unrelated_pending_event(self):
        health = {"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "Missing provider configuration: Google WIF/Drive access token"}
        a = normalized_runtime_facts(queue={"events": {"a": pending("a")}}, health=health)
        b = normalized_runtime_facts(queue={"events": {"b": pending("b")}}, health=health)
        ah = next(f.evidence_hash for f in a if f.objective_id == "KQ-009")
        bh = next(f.evidence_hash for f in b if f.objective_id == "KQ-009")
        self.assertEqual(ah, bh)

    def test_changed_provider_detail_changes_blocker_fingerprint(self):
        queue = {"events": {}}
        a = normalized_runtime_facts(queue=queue, health={"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "A"})
        b = normalized_runtime_facts(queue=queue, health={"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "B"})
        ah = next(f.evidence_hash for f in a if f.objective_id == "KQ-009")
        bh = next(f.evidence_hash for f in b if f.objective_id == "KQ-009")
        self.assertNotEqual(ah, bh)

    def test_only_existing_pending_or_claimed_events_become_runtime_candidates(self):
        queue = {
            "events": {
                "pending": pending("pending"),
                "acked": {**pending("acked"), "status": "ACKNOWLEDGED_ACTION"},
                "garbage": "not-an-event",
            }
        }
        facts = normalized_runtime_facts(queue=queue, health={"status": "READY", "detail": "ready"})
        runtime = [f for f in facts if f.objective_id and f.objective_id.startswith("DUE::")]
        self.assertEqual([f.objective_id for f in runtime], ["DUE::pending"])

    def test_provider_loss_blocks_due_model_work_and_terminates_no_action(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "queue.json").write_text(json.dumps({"events": {"pending": pending("pending")}}))
            (root / "health.json").write_text(json.dumps({"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "Missing provider configuration: Google WIF/Drive access token"}))
            trace = run_shadow(
                queue_path=root / "queue.json",
                health_path=root / "health.json",
                state_path=root / "state.json",
                trace_path=root / "trace.json",
                observed_at=datetime(2026, 9, 21, 19, 5, tzinfo=timezone.utc),
                run_id="1",
            )
            self.assertEqual(trace["resolver_termination"], "NO_ACTION")
            self.assertIsNone(trace["selected_candidate_id"])
            due = next(d for d in trace["decisions"] if d["candidate_id"] == "DUE::pending")
            self.assertEqual(due["state_class"], StateClass.BLOCKED.value)
            self.assertFalse(trace["execution_performed"])
            self.assertTrue(trace["state_readback_verified"])

    def test_repeat_invocation_suppresses_unchanged_kq009_and_deduplicates_decision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            queue = {"events": {"pending": pending("pending")}}
            health = {"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "Missing provider configuration: Google WIF/Drive access token"}
            (root / "queue.json").write_text(json.dumps(queue))
            (root / "health.json").write_text(json.dumps(health))
            first = run_shadow(
                queue_path=root / "queue.json", health_path=root / "health.json",
                state_path=root / "state.json", trace_path=root / "trace.json",
                observed_at=datetime(2026, 9, 21, 19, 5, tzinfo=timezone.utc), run_id="1")
            second = run_shadow(
                queue_path=root / "queue.json", health_path=root / "health.json",
                state_path=root / "state.json", trace_path=root / "trace.json",
                observed_at=datetime(2026, 9, 21, 19, 10, tzinfo=timezone.utc), run_id="2")
            self.assertTrue(first["material_source_change"])
            self.assertFalse(second["material_source_change"])
            self.assertFalse(second["decision_changed"])
            kq = next(d for d in second["decisions"] if d["candidate_id"] == "KQ-009")
            self.assertTrue(kq["unchanged"])
            self.assertIn("suppress", kq["reasons"][0])
            self.assertEqual(second["run_count"], 2)

    def test_new_due_event_is_detected_as_material_change_without_changing_kq009(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            health = {"status": "PROVIDER_ACCESS_NOT_CONFIGURED", "detail": "Missing provider configuration: Google WIF/Drive access token"}
            (root / "health.json").write_text(json.dumps(health))
            (root / "queue.json").write_text(json.dumps({"events": {"a": pending("a")}}))
            first = run_shadow(queue_path=root/"queue.json", health_path=root/"health.json", state_path=root/"state.json", trace_path=root/"trace.json", run_id="1")
            (root / "queue.json").write_text(json.dumps({"events": {"a": pending("a"), "b": pending("b")}}))
            second = run_shadow(queue_path=root/"queue.json", health_path=root/"health.json", state_path=root/"state.json", trace_path=root/"trace.json", run_id="2")
            self.assertTrue(second["material_source_change"])
            self.assertIn("DUE::b", second["candidate_ids"])
            kq = next(d for d in second["decisions"] if d["candidate_id"] == "KQ-009")
            self.assertTrue(kq["unchanged"])

    def test_ready_provider_makes_existing_due_event_actionable_but_shadow_does_not_execute(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "queue.json").write_text(json.dumps({"events": {"pending": pending("pending")}}))
            (root / "health.json").write_text(json.dumps({"status": "READY", "detail": "ready"}))
            trace = run_shadow(queue_path=root/"queue.json", health_path=root/"health.json", state_path=root/"state.json", trace_path=root/"trace.json", run_id="1")
            self.assertEqual(trace["selected_candidate_id"], "DUE::pending")
            self.assertEqual(trace["resolver_termination"], "EXECUTE_ONE")
            self.assertFalse(trace["execution_performed"])
            self.assertFalse(trace["canonical_record_written"])


if __name__ == "__main__":
    unittest.main()
