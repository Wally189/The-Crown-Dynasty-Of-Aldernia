import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from aldernia_runtime.opportunity_stage3 import (
    AUTHORITY_REF,
    CONSEQUENCE_CANDIDATE_ID,
    EXECUTION_CLASS,
    SEED_CANDIDATE_ID,
    Stage3Error,
    load_state,
    run_stage3,
)


def write_shadow(path: Path, **overrides):
    value = {
        "state_readback_verified": True,
        "external_effect": "NONE",
        "source_fingerprint": "source-a",
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")


class Stage3Tests(unittest.TestCase):
    def run_once(self, root: Path, run_id: str):
        return run_stage3(
            shadow_trace_path=root / "shadow.json",
            state_path=root / "stage3.json",
            trace_path=root / "trace.json",
            run_id=run_id,
            observed_at=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        )

    def test_first_invocation_executes_one_action_and_emits_pending_consequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            trace = self.run_once(root, "run-1")
            state = load_state(root / "stage3.json")
            self.assertEqual(trace["selected_candidate_id"], SEED_CANDIDATE_ID)
            self.assertTrue(trace["execution_performed"])
            self.assertTrue(trace["durable_ack"])
            self.assertTrue(trace["consequence_emitted"])
            self.assertFalse(trace["consequence_consumed"])
            self.assertEqual(state["phase"], "FIRST_ACKED")
            self.assertEqual(
                state["consequence"]["status"], "PENDING_LATER_INVOCATION"
            )

    def test_same_workflow_run_cannot_consume_its_own_consequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            self.run_once(root, "run-1")
            trace = self.run_once(root, "run-1")
            self.assertFalse(trace["execution_performed"])
            self.assertEqual(trace["termination"], "WAIT_FOR_LATER_INVOCATION")
            self.assertEqual(load_state(root / "stage3.json")["phase"], "FIRST_ACKED")

    def test_later_invocation_consumes_consequence_and_completes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            self.run_once(root, "run-1")
            trace = self.run_once(root, "run-2")
            state = load_state(root / "stage3.json")
            self.assertEqual(
                trace["selected_candidate_id"], CONSEQUENCE_CANDIDATE_ID
            )
            self.assertTrue(trace["execution_performed"])
            self.assertTrue(trace["consequence_consumed"])
            self.assertEqual(trace["causal_depth"], 1)
            self.assertEqual(state["phase"], "COMPLETE")
            self.assertEqual(state["consequence"]["status"], "ACKNOWLEDGED")

    def test_third_invocation_is_idempotent_no_action(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            self.run_once(root, "run-1")
            self.run_once(root, "run-2")
            trace = self.run_once(root, "run-3")
            self.assertFalse(trace["execution_performed"])
            self.assertEqual(trace["termination"], "NO_ACTION")
            self.assertEqual(load_state(root / "stage3.json")["phase"], "COMPLETE")

    def test_unverified_shadow_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json", state_readback_verified=False)
            with self.assertRaises(Stage3Error):
                self.run_once(root, "run-1")
            self.assertFalse((root / "stage3.json").exists())

    def test_shadow_external_effect_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json", external_effect="PUBLICATION")
            with self.assertRaises(Stage3Error):
                self.run_once(root, "run-1")

    def test_corrupted_first_receipt_blocks_consequence_consumption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            self.run_once(root, "run-1")
            state = load_state(root / "stage3.json")
            state["first_receipt"]["external_effect"] = "EXTERNAL"
            (root / "stage3.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(Stage3Error):
                self.run_once(root, "run-2")

    def test_receipts_are_single_class_and_exact_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            self.run_once(root, "run-1")
            self.run_once(root, "run-2")
            state = load_state(root / "stage3.json")
            self.assertEqual(
                state["first_receipt"]["execution_class"], EXECUTION_CLASS
            )
            self.assertEqual(
                state["second_receipt"]["execution_class"], EXECUTION_CLASS
            )
            self.assertEqual(state["first_receipt"]["authority_ref"], AUTHORITY_REF)
            self.assertEqual(state["second_receipt"]["authority_ref"], AUTHORITY_REF)

    def test_no_model_external_or_canonical_effect_is_claimed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            first = self.run_once(root, "run-1")
            second = self.run_once(root, "run-2")
            for trace in (first, second):
                self.assertFalse(trace["model_called"])
                self.assertEqual(trace["external_effect"], "NONE")
                self.assertFalse(trace["canonical_record_written"])
                self.assertFalse(trace["authority_widened"])

    def test_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_shadow(root / "shadow.json")
            (root / "stage3.json").write_text(
                json.dumps({"schema_version": 9, "phase": "READY"}),
                encoding="utf-8",
            )
            with self.assertRaises(Stage3Error):
                self.run_once(root, "run-1")


if __name__ == "__main__":
    unittest.main()
