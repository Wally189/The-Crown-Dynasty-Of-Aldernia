import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.patrol import validate_patrol_result
from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.unattended_patrol import (
    ESTATE_RUNTIME_BOUNDS,
    ROYAL_HOUSEHOLD_FOLDER_ID,
    SOFTWARE_HARD_CAP_USD,
    _current_month_cost_usd,
    _select_candidate_window,
)
from aldernia_runtime.worker import (
    PERSISTENT_MODEL_RUNTIME_CLASS,
    acknowledge_event,
    claim_event,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
HB_0600 = "dynasty.heartbeat:2026-09-21T06:00"
HB_0700 = "dynasty.heartbeat:2026-09-21T07:00"


class UnattendedPatrolTests(unittest.TestCase):
    def paths(self, root: Path):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
        }

    def seed(self, root: Path, *, hour_utc: int):
        run_scheduler(
            **self.paths(root),
            now=datetime(2026, 9, 21, hour_utc, 1, tzinfo=timezone.utc),
        )

    def claim(self, root: Path, event_id: str, *, hour_utc: int):
        return claim_event(
            **self.paths(root),
            event_id=event_id,
            worker_id="github-central-clock-option-b-test",
            runtime_class=PERSISTENT_MODEL_RUNTIME_CLASS,
            now=datetime(2026, 9, 21, hour_utc, 2, tzinfo=timezone.utc),
        )

    def patrol_result(self, claim, *, findings=None):
        plan = claim["integrity_patrol"]
        return {
            "registry_version": plan["registry_version"],
            "source_row": plan["source_row"],
            "estate_id": plan["estate_id"],
            "cursor_after": plan["cursor_after"],
            "current_authority_retrieved": True,
            "royal_household_accessed": False,
            "readback_verified": True,
            "findings": list(findings or []),
            "scan_cursor_before": None,
            "scan_cursor_after": "candidate-24",
            "scan_truncated": True,
        }

    def execution_result(self, claim, *, outcome="NO_ACTION", findings=None):
        return {
            "outcome": outcome,
            "result_summary": "Bounded persistent patrol acceptance result.",
            "retrieved_source_ids": list(claim["required_source_ids"]),
            "evidence_refs": ["test:persistent-patrol"],
            "writes": ["test:durable-receipt"],
            "readback_verified": True,
            "resource_classes_used": ["ALDERNIA_INTERNAL"],
            "external_effect": "NONE",
            "new_model_provider_access_granted": False,
            "integrity_patrol": self.patrol_result(claim, findings=findings),
            "model_usage": {
                "model": "gpt-5.6-luna",
                "input_tokens": 1000,
                "output_tokens": 200,
                "conservative_cost_usd": 0.00044,
                "software_hard_cap_usd": SOFTWARE_HARD_CAP_USD,
            },
        }

    def test_persistent_runtime_can_claim_model_bearing_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root, hour_utc=5)  # 06:01 BST
            claim = self.claim(root, HB_0600, hour_utc=5)
            self.assertEqual(claim["status"], "CLAIMED_RUNTIME")
            self.assertEqual(claim["integrity_patrol"]["estate_id"], "house-of-carol")
            self.assertIn("required_source_ids", claim)

    def test_stop_receipt_does_not_advance_estate_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root, hour_utc=5)
            first = self.claim(root, HB_0600, hour_utc=5)
            ack = acknowledge_event(
                **self.paths(root),
                event_id=HB_0600,
                claim_id=first["claim_id"],
                execution_result=self.execution_result(first, outcome="STOP"),
                now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
            )
            self.assertEqual(ack["status"], "ACKNOWLEDGED_STOP")

            self.seed(root, hour_utc=6)  # 07:01 BST
            second = self.claim(root, HB_0700, hour_utc=6)
            self.assertEqual(second["integrity_patrol"]["estate_id"], "house-of-carol")

    def test_model_usage_is_persisted_in_existing_worker_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root, hour_utc=5)
            claim = self.claim(root, HB_0600, hour_utc=5)
            acknowledge_event(
                **self.paths(root),
                event_id=HB_0600,
                claim_id=claim["claim_id"],
                execution_result=self.execution_result(claim),
                now=datetime(2026, 9, 21, 5, 3, tzinfo=timezone.utc),
            )
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            usage = state["events"][HB_0600]["receipt"]["model_usage"]
            self.assertEqual(usage["model"], "gpt-5.6-luna")
            self.assertAlmostEqual(usage["conservative_cost_usd"], 0.00044)

    def test_archive_permission_gap_is_a_valid_bounded_stop_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root, hour_utc=5)
            claim = self.claim(root, HB_0600, hour_utc=5)
            plan = claim["integrity_patrol"]
            finding = {
                "classification": "STALE/SUPERSEDED",
                "source_refs": ["Drive:stale-candidate"],
                "disposition": "STOP_NO_SAFE_ARCHIVE",
                "provenance_preserved": True,
                "readback_verified": True,
                "destructive_delete": False,
                "archive_error": "no competent writable archive surface is configured for this estate",
            }
            receipt = validate_patrol_result(
                plan=plan,
                patrol_result=self.patrol_result(claim, findings=[finding]),
                retrieved_source_ids=set(claim["required_source_ids"]),
            )
            self.assertEqual(
                receipt["findings"][0]["disposition"],
                "STOP_NO_SAFE_ARCHIVE",
            )

    def test_spend_guard_sums_only_current_month_existing_receipts(self):
        state = {
            "events": {
                "a": {
                    "receipt": {
                        "completed_at": "2026-09-01T10:00:00+00:00",
                        "model_usage": {"conservative_cost_usd": 0.9},
                    }
                },
                "b": {
                    "receipt": {
                        "completed_at": "2026-09-21T10:00:00+00:00",
                        "model_usage": {"conservative_cost_usd": 1.1},
                    }
                },
                "old": {
                    "receipt": {
                        "completed_at": "2026-08-31T23:59:59+00:00",
                        "model_usage": {"conservative_cost_usd": 99},
                    }
                },
            }
        }
        total = _current_month_cost_usd(
            state, datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(total, 2.0)
        self.assertLess(total, SOFTWARE_HARD_CAP_USD)

    def test_candidate_window_resumes_from_durable_file_cursor(self):
        candidates = [{"id": f"f{i}", "name": f"File {i}"} for i in range(30)]
        selected, cursor_after, truncated = _select_candidate_window(candidates, "f23")
        self.assertTrue(truncated)
        self.assertEqual(selected[0]["id"], "f24")
        self.assertEqual(len(selected), 24)
        self.assertEqual(cursor_after, selected[-1]["id"])

    def test_royal_household_is_not_an_estate_scan_root(self):
        for estate_id, bounds in ESTATE_RUNTIME_BOUNDS.items():
            roots = [root_id for root_id, _recursive in bounds.get("root_specs") or []]
            self.assertNotIn(
                ROYAL_HOUSEHOLD_FOLDER_ID,
                roots,
                msg=f"Royal Household leaked into {estate_id}",
            )


if __name__ == "__main__":
    unittest.main()
