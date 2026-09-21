import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.session import halt
from aldernia_runtime.unattended_patrol import (
    BudgetLedger,
    ControlledStop,
    DriveClient,
    run_once,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"


class FakeDrive:
    def __init__(self):
        self.queue_calls = 0
        self.archive_calls = 0
        self.task_writes = []

    def read_text(self, file_id, *, max_chars):
        return (
            {
                "id": file_id,
                "name": f"Record {file_id}",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"Current controlled content for {file_id}",
        )

    def bounded_tree(self, root_ids):
        return (
            [
                {
                    "id": "candidate-1",
                    "name": "Candidate One",
                    "mimeType": "application/vnd.google-apps.document",
                    "patrol_parent": root_ids[0],
                    "capabilities": {"canEdit": True, "canMoveItemWithinDrive": True},
                }
            ],
            [
                {
                    "id": "archive-1",
                    "name": "99 - Archive & Superseded",
                    "mimeType": "application/vnd.google-apps.folder",
                    "patrol_depth": 1,
                }
            ],
        )

    def sheet_values(self, a1):
        if a1 == "Scheduled Tasks!A2:K2":
            return [["Governance", "Dynasty House Pulse"] + ["test"] * 9]
        return []

    def write_task_run_state(self, *, row_number, last_run, outcome, evidence):
        self.task_writes.append((row_number, last_run, outcome, evidence))
        return f"Drive:test-task-row-{row_number}"

    def move_to_archive(self, *, file_id, archive_folder_id, source_parent_id):
        self.archive_calls += 1
        return {
            "id": file_id,
            "name": "ARCHIVED - Candidate One",
            "parents": [archive_folder_id],
            "trashed": False,
        }

    def ensure_conflict_queue(
        self,
        *,
        conflict_id,
        proposition,
        rationale,
        estate_label,
        source_refs,
        exact_question,
    ):
        self.queue_calls += 1
        return "KQ-PATROL-TEST"


class FakeModel:
    def __init__(self, findings):
        self.findings = findings
        self.calls = 0
        self.heartbeat_calls = 0

    def classify(self, prompt):
        self.calls += 1
        self.assertable_prompt = prompt
        return (
            {"summary": "bounded synthetic classification", "findings": self.findings},
            {"input_tokens": 1000, "output_tokens": 200},
        )

    def heartbeat(self, prompt):
        self.heartbeat_calls += 1
        self.heartbeat_prompt = prompt
        return (
            {
                "outcome": "NO_ACTION",
                "result_summary": "All principal governed institutions reconciled; no Crown attention required.",
                "institutions_checked": [
                    "Royal Palace",
                    "House of Carol",
                    "House of Marianne",
                    "House of Catholic",
                    "House of Josie",
                ],
                "material_changes": [],
                "crown_attention": [],
            },
            {"input_tokens": 2000, "output_tokens": 250},
        )


class UnattendedPatrolTests(unittest.TestCase):
    def paths(self, root: Path):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
            "budget_path": root / "patrol-budget.json",
        }

    def seed(self, root: Path):
        run_scheduler(
            state_path=root / "scheduled-duty-queue.json",
            timetable_path=TIMETABLE,
            session_path=root / "bus-session.json",
            now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),
        )

    def test_budget_stops_before_new_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "budget.json"
            ledger = BudgetLedger(path)
            ledger.value["estimated_usd"] = 4.0
            with self.assertRaises(ControlledStop):
                ledger.guard(estimated_input_tokens=70000)

    def test_one_pending_heartbeat_executes_one_carol_slice_and_acks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root)
            drive = FakeDrive()
            model = FakeModel([])
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="test-unattended-runtime",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(result["status"], "ACKNOWLEDGED_NO_ACTION")
            self.assertEqual(result["estate_id"], "house-of-carol")
            self.assertEqual(result["visit_index"], 1)
            self.assertEqual(model.calls, 1)
            self.assertEqual(model.heartbeat_calls, 1)
            self.assertEqual(len(drive.task_writes), 1)
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            event = state["events"]["dynasty.heartbeat:2026-09-21T06:00"]
            patrol = event["receipt"]["integrity_patrol"]
            self.assertEqual(patrol["estate_id"], "house-of-carol")
            self.assertEqual(patrol["cursor_after"], "house-of-tony")
            self.assertTrue(patrol["current_authority_retrieved"])
            self.assertFalse(patrol["royal_household_accessed"])
            balcony = event["receipt"]["balcony_pulse"]
            self.assertTrue(balcony["readback_verified"])
            self.assertEqual(balcony["crown_attention"], [])

    def test_unambiguous_stale_candidate_archives_reversibly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root)
            drive = FakeDrive()
            model = FakeModel(
                [
                    {
                        "classification": "STALE/SUPERSEDED",
                        "file_id": "candidate-1",
                        "source_refs": ["Drive:candidate-1"],
                        "authority_evidence_refs": [
                            "15sUjJSU9z5YaXgMecV-2PeQw2x-2JfmDiTGXoM-T6S8"
                        ],
                        "rationale": "Current authority explicitly supersedes the candidate.",
                        "proposition": None,
                        "exact_question": None,
                        "confidence": "UNAMBIGUOUS",
                    }
                ]
            )
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="test-unattended-runtime",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(result["status"], "ACKNOWLEDGED_ACTION")
            self.assertEqual(drive.archive_calls, 1)
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            finding = state["events"]["dynasty.heartbeat:2026-09-21T06:00"]["receipt"][
                "integrity_patrol"
            ]["findings"][0]
            self.assertEqual(finding["disposition"], "ARCHIVED")
            self.assertTrue(finding["provenance_preserved"])
            self.assertFalse(finding["destructive_delete"])

    def test_conflict_routes_once_to_king_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root)
            drive = FakeDrive()
            model = FakeModel(
                [
                    {
                        "classification": "CONFLICTING CURRENT MATERIAL",
                        "file_id": "candidate-1",
                        "source_refs": [
                            "Drive:candidate-1",
                            "Drive:15sUjJSU9z5YaXgMecV-2PeQw2x-2JfmDiTGXoM-T6S8",
                        ],
                        "authority_evidence_refs": [],
                        "rationale": "Two current references disagree on the same owner.",
                        "proposition": "The same controlled resource has two current owners.",
                        "exact_question": "Which current ownership record governs?",
                        "confidence": "MATERIAL_CONFLICT",
                    }
                ]
            )
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="test-unattended-runtime",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(result["status"], "ACKNOWLEDGED_ACTION")
            self.assertEqual(drive.queue_calls, 1)
            state = json.loads((root / "scheduled-duty-queue.json").read_text())
            finding = state["events"]["dynasty.heartbeat:2026-09-21T06:00"]["receipt"][
                "integrity_patrol"
            ]["findings"][0]
            self.assertEqual(finding["queue_ref"], "KQ-PATROL-TEST")
            self.assertTrue(finding["queue_deduplicated"])

    def test_halt_prevents_model_and_drive_work(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.seed(root)
            halt(
                root / "bus-session.json",
                now=datetime(2026, 9, 21, 5, 1, tzinfo=timezone.utc),
                reason="TEST HALT",
            )
            drive = FakeDrive()
            model = FakeModel([])
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="test-unattended-runtime",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(result["status"], "HALTED")
            self.assertEqual(model.calls, 0)
            self.assertEqual(model.heartbeat_calls, 0)

    def test_drive_tree_excludes_royal_household_by_name(self):
        client = object.__new__(DriveClient)
        client.list_children = lambda folder_id: [
            {
                "id": "household-private",
                "name": "The Royal Household - Private",
                "mimeType": "application/vnd.google-apps.folder",
            },
            {
                "id": "allowed-file",
                "name": "Allowed Record",
                "mimeType": "application/vnd.google-apps.document",
            },
        ]
        files, _ = client.bounded_tree(["allowed-root"], max_items=10, max_depth=2)
        ids = {item["id"] for item in files}
        self.assertIn("allowed-file", ids)
        self.assertNotIn("household-private", ids)


if __name__ == "__main__":
    unittest.main()
