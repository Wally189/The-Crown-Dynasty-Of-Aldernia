import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.unattended_patrol import BudgetLedger, ControlledStop
from aldernia_runtime.unattended_worker import (
    BUDGET_GUARD_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    SUPPORTED_DUTY_IDS,
    run_once,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"


class FakeDrive:
    def __init__(self):
        self.writes = []

    def read_text(self, file_id, *, max_chars):
        return (
            {
                "id": file_id,
                "name": f"Source {file_id}",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"Current controlled source content for {file_id}",
        )

    def scheduled_row(self, row_number):
        return ["test"] * 11

    def write_run_state(self, *, row_number, last_run, outcome, evidence):
        self.writes.append((row_number, last_run, outcome, evidence))
        return f"Drive:test-row-{row_number}"


class NeverModel:
    def __init__(self):
        self.calls = []

    def execute(self, prompt, *, public_web_read=False):
        self.calls.append((prompt, public_web_read))
        raise AssertionError("unsupported duty must not call the model")


class GovernmentModel:
    def __init__(self):
        self.calls = []

    def execute(self, prompt, *, public_web_read=False):
        self.calls.append((prompt, public_web_read))
        return {
            "outcome": "NO_ACTION",
            "result_summary": "No material Government action in controlled test.",
            "evidence_refs": ["test:government"],
            "domain_terminal_state": "VERIFIED_CLOSED",
            "red_box_content": None,
            "crown_action_required": False,
            "_runtime_usage": {"input_tokens": 1000, "output_tokens": 100},
        }


class FakeBudget:
    def __init__(self):
        self.guards = []
        self.records = []

    def guard(self, estimated_input_tokens, *, max_output_tokens):
        self.guards.append((estimated_input_tokens, max_output_tokens))

    def record(self, usage):
        self.records.append(dict(usage))


class UnattendedWorkerControlTests(unittest.TestCase):
    def paths(self, root):
        return {
            "state_path": root / "scheduled-duty-queue.json",
            "timetable_path": TIMETABLE,
            "session_path": root / "bus-session.json",
        }

    def test_only_encoded_government_contracts_are_model_executable(self):
        self.assertEqual(
            SUPPORTED_DUTY_IDS,
            frozenset({"government.daily-pulse", "government.red-box"}),
        )

    def test_unsupported_due_duty_fails_closed_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 4, 1, tzinfo=timezone.utc),
            )
            result = run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=None,
                worker_id="runtime-control-test",
                max_events=1,
                now=datetime(2026, 9, 21, 4, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 1)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            event = state["events"]["catholic.dawn-offices:2026-09-21"]
            self.assertEqual(event["status"], "ACKNOWLEDGED_STOP")
            self.assertIn(
                "unattended execution contract is not encoded",
                event["receipt"]["result_summary"],
            )
    def test_supported_due_duty_stays_pending_without_openai_provider(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),
            )
            state_path = root / "scheduled-duty-queue.json"
            state = json.loads(state_path.read_text())
            gov_id = "government.daily-pulse:2026-09-21"
            state["events"] = {gov_id: state["events"][gov_id]}
            state_path.write_text(json.dumps(state))
            result = run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=None,
                worker_id="runtime-provider-stop-test",
                max_events=1,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(result["status"], "PROVIDER_ACCESS_NOT_CONFIGURED")
            state = json.loads(state_path.read_text())
            self.assertEqual(state["events"][gov_id]["status"], "PENDING_RUNTIME")
            self.assertIsNone(state["events"][gov_id].get("claim"))

    def test_supported_government_call_is_guarded_before_claim_and_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),
            )
            state_path = root / "scheduled-duty-queue.json"
            state = json.loads(state_path.read_text())
            gov_id = "government.daily-pulse:2026-09-21"
            state["events"] = {gov_id: state["events"][gov_id]}
            state_path.write_text(json.dumps(state))
            budget = FakeBudget()
            model = GovernmentModel()
            run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=model,
                worker_id="runtime-budget-test",
                max_events=1,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
                budget=budget,
            )
            self.assertEqual(
                budget.guards,
                [(BUDGET_GUARD_INPUT_TOKENS, MAX_OUTPUT_TOKENS)],
            )
            self.assertEqual(
                budget.records,
                [{"input_tokens": 1000, "output_tokens": 100}],
            )
            self.assertEqual(len(model.calls), 1)

    def test_shared_monthly_budget_stops_before_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "budget.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "estimated_usd": 3.99,
                        "calls": 0,
                    }
                )
            )
            ledger = BudgetLedger(path)
            with self.assertRaises(ControlledStop):
                ledger.guard(
                    BUDGET_GUARD_INPUT_TOKENS,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )


if __name__ == "__main__":
    unittest.main()
