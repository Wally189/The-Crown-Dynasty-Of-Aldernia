import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from aldernia_runtime.canonical_programme_board import build_packet_from_values
from aldernia_runtime.opportunity_shadow import run_shadow
from aldernia_runtime.opportunity_stage4 import load_state, run_stage4


OUTCOME = (
    "AUTHORISED — STAGE-4 LIVE CANONICAL DELTA ACK / MACHINE "
    "kind=AUTHORISED_INTERNAL_WORK; candidate_id=PB-E28-LIVE-DELTA-ACK; "
    "profile=COS-SEL-001,COS-BUS-001,COS-WORKER-001; "
    "execution_class=COS-WORKER-001::CANONICAL_DELTA_ACK; "
    "effect=INTERNAL_DERIVED_STATE_WRITE; reversible=true; model=false; consequence=NONE"
)


def write_inputs(root: Path):
    packet = build_packet_from_values(
        [["E-28", "CROWN INFRASTRUCTURE / SELF-PROGRESSION STAGE 4 AUTHORITY", "bounded", OUTCOME]],
        modified_time="2026-09-22T16:49:08.476Z",
        transport="CONNECTED_DRIVE_BRIDGE",
        start_row=155,
    )
    (root / "canonical.json").write_text(json.dumps(packet), encoding="utf-8")
    (root / "queue.json").write_text(json.dumps({"events": {}}), encoding="utf-8")
    (root / "health.json").write_text(
        json.dumps(
            {
                "status": "PROVIDER_ACCESS_NOT_CONFIGURED",
                "detail": "Missing provider configuration: Google WIF/Drive access token",
            }
        ),
        encoding="utf-8",
    )


def run_resolver(root: Path, run_id: str):
    return run_shadow(
        queue_path=root / "queue.json",
        health_path=root / "health.json",
        state_path=root / "resolver-state.json",
        trace_path=root / "resolver-trace.json",
        canonical_packet_path=root / "canonical.json",
        stage4_state_path=root / "stage4-state.json",
        observed_at=datetime(2026, 9, 22, 16, 50, tzinfo=timezone.utc),
        run_id=run_id,
    )


class Stage4Tests(unittest.TestCase):
    def test_live_canonical_delta_executes_one_bounded_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            shadow = run_resolver(root, "run-1")
            self.assertEqual(shadow["selected_candidate_id"], "PB-E28-LIVE-DELTA-ACK")
            trace = run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-1",
                observed_at=datetime(2026, 9, 22, 16, 51, tzinfo=timezone.utc),
            )
            self.assertTrue(trace["execution_performed"])
            self.assertTrue(trace["durable_ack"])
            self.assertFalse(trace["consequence_emitted"])
            self.assertEqual(trace["external_effect"], "NONE")

    def test_later_invocation_is_no_action_after_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            run_resolver(root, "run-1")
            run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-1",
            )
            second_shadow = run_resolver(root, "run-2")
            self.assertIsNone(second_shadow["selected_candidate_id"])
            decision = next(
                d for d in second_shadow["decisions"]
                if d["candidate_id"] == "PB-E28-LIVE-DELTA-ACK"
            )
            self.assertEqual(decision["state_class"], "NO_ACTION")
            trace = run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-2",
            )
            self.assertFalse(trace["execution_performed"])
            self.assertEqual(trace["termination"], "NO_ACTION")

    def test_receipt_records_connector_transport_not_unattended_drive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            run_resolver(root, "run-1")
            trace = run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-1",
            )
            self.assertEqual(trace["canonical_transport"], "CONNECTED_DRIVE_BRIDGE")

    def test_state_contains_exact_evidence_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            run_resolver(root, "run-1")
            run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-1",
            )
            state = load_state(root / "stage4-state.json")
            ack = state["acknowledged"]["PB-E28-LIVE-DELTA-ACK"]
            self.assertTrue(ack["evidence_hash"])
            self.assertTrue(ack["receipt_hash"])

    def test_changed_canonical_row_becomes_actionable_again(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            run_resolver(root, "run-1")
            run_stage4(
                shadow_trace_path=root / "resolver-trace.json",
                canonical_packet_path=root / "canonical.json",
                state_path=root / "stage4-state.json",
                trace_path=root / "stage4-trace.json",
                run_id="run-1",
            )
            packet = json.loads((root / "canonical.json").read_text())
            packet["rows"][0]["narrative"] = "bounded material change"
            (root / "canonical.json").write_text(json.dumps(packet), encoding="utf-8")
            shadow = run_resolver(root, "run-2")
            self.assertEqual(shadow["selected_candidate_id"], "PB-E28-LIVE-DELTA-ACK")

    def test_provider_blocker_does_not_freeze_non_model_canonical_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            shadow = run_resolver(root, "run-1")
            self.assertEqual(shadow["selected_candidate_id"], "PB-E28-LIVE-DELTA-ACK")
            kq = next(d for d in shadow["decisions"] if d["candidate_id"] == "KQ-009")
            self.assertEqual(kq["state_class"], "BLOCKED")

    def test_unverified_shadow_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_inputs(root)
            (root / "resolver-trace.json").write_text(
                json.dumps({"state_readback_verified": False, "external_effect": "NONE"}),
                encoding="utf-8",
            )
            with self.assertRaises(Exception):
                run_stage4(
                    shadow_trace_path=root / "resolver-trace.json",
                    canonical_packet_path=root / "canonical.json",
                    state_path=root / "stage4-state.json",
                    trace_path=root / "stage4-trace.json",
                    run_id="run-1",
                )


if __name__ == "__main__":
    unittest.main()
