import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aldernia_runtime.execution_contracts import (
    ContractError,
    PROFILE_GOVERNMENT_PULSE,
    PROFILE_MODEL_INTERNAL,
    PROFILE_MODEL_PUBLIC_READ,
    parse_contract_cell,
)
from aldernia_runtime.scheduler import run_scheduler
from aldernia_runtime.unattended_patrol import BudgetLedger, ControlledStop
from aldernia_runtime.unattended_worker import (
    BUDGET_GUARD_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    run_once,
)

HERE = Path(__file__).resolve().parents[1]
TIMETABLE = HERE / "aldernia" / "schedule" / "timetable.json"
EVENING = "aldernia.evening-commons:2026-09-21"
GOV = "government.daily-pulse:2026-09-21"
DAWN = "catholic.dawn-offices:2026-09-21"


def contract(
    duty_id,
    profile,
    *,
    engine_mode="DECLARED",
    catchup="SAME_DAY_ALLOWED",
):
    return json.dumps(
        {
            "v": 1,
            "duty_id": duty_id,
            "profile": profile,
            "engine_mode": engine_mode,
            "catchup": catchup,
        },
        separators=(",", ":"),
    )


def task_row(assigned_engine, execution_contract):
    row = ["test"] * 12
    row[3] = assigned_engine
    row[11] = execution_contract
    return row


class FakeDrive:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
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
        return list(self.rows.get(row_number, ["test"] * 11 + [""]))

    def write_run_state(self, *, row_number, last_run, outcome, evidence):
        self.writes.append((row_number, last_run, outcome, evidence))
        return f"Drive:test-row-{row_number}"


class GenericModel:
    def __init__(self, result=None):
        self.calls = []
        self.selector_calls = []
        self.result = result or {
            "outcome": "NO_ACTION",
            "result_summary": "No material action in controlled generic test.",
            "evidence_refs": ["test:generic"],
            "domain_terminal_state": None,
            "red_box_content": None,
            "crown_action_required": False,
            "_runtime_usage": {"input_tokens": 1000, "output_tokens": 100},
        }

    def execute(
        self,
        prompt,
        *,
        public_web_read=False,
        profile_instruction="",
    ):
        self.calls.append((prompt, public_web_read, profile_instruction))
        return dict(self.result)

    def select_engine(self, prompt):
        self.selector_calls.append(prompt)
        return (
            {
                "selector": "COS-SEL-001",
                "status": "AUTHORISED",
                "machine_engines": ["COS-SEL-001"],
                "human_route": "House of Josie — bounded selected route",
            },
            {"input_tokens": 500, "output_tokens": 50},
        )


class NeverModel(GenericModel):
    def execute(self, *args, **kwargs):
        raise AssertionError("model execution must not occur")


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

    def keep_only(self, root, event_id):
        path = root / "scheduled-duty-queue.json"
        state = json.loads(path.read_text())
        state["events"] = {event_id: state["events"][event_id]}
        path.write_text(json.dumps(state))

    def test_contract_parser_rejects_arbitrary_handler_injection(self):
        with self.assertRaises(ContractError):
            parse_contract_cell(
                json.dumps(
                    {
                        "v": 1,
                        "duty_id": "x",
                        "profile": PROFILE_MODEL_INTERNAL,
                        "engine_mode": "DECLARED",
                        "catchup": "SAME_DAY_ALLOWED",
                        "handler": "os.system",
                    }
                ),
                expected_duty_id="x",
            )

    def test_missing_contract_fails_closed_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 4, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, DAWN)
            result = run_once(
                **self.paths(root),
                drive=FakeDrive(),
                model=None,
                worker_id="runtime-contract-stop-test",
                max_events=1,
                now=datetime(2026, 9, 21, 4, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 1)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            event = state["events"][DAWN]
            self.assertEqual(event["status"], "ACKNOWLEDGED_STOP")
            self.assertIn(
                "execution contract failed closed",
                event["receipt"]["result_summary"],
            )

    def test_generic_non_government_duty_executes_by_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 17, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, EVENING)
            drive = FakeDrive(
                {
                    19: task_row(
                        "House of Josie — editorial curation",
                        contract(
                            "aldernia.evening-commons",
                            PROFILE_MODEL_PUBLIC_READ,
                            catchup="POINT_IN_TIME_EXPIRES",
                        ),
                    )
                }
            )
            model = GenericModel()
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="runtime-generic-profile-test",
                max_events=1,
                now=datetime(2026, 9, 21, 17, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 1)
            self.assertEqual(len(model.calls), 1)
            self.assertTrue(model.calls[0][1])
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            receipt = state["events"][EVENING]["receipt"]
            self.assertEqual(receipt["execution_profile"], PROFILE_MODEL_PUBLIC_READ)
            self.assertEqual(receipt["outcome"], "NO_ACTION")
            self.assertEqual(receipt["engine_plan"]["mode"], "DECLARED")

    def test_model_profile_stays_pending_without_openai_provider(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, GOV)
            drive = FakeDrive(
                {
                    12: task_row(
                        "House of Marianne — Centre of Government",
                        contract(
                            "government.daily-pulse",
                            PROFILE_GOVERNMENT_PULSE,
                        ),
                    )
                }
            )
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=None,
                worker_id="runtime-provider-stop-test",
                max_events=1,
                now=datetime(2026, 9, 21, 6, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(
                result["status"], "PROVIDER_ACCESS_NOT_CONFIGURED"
            )
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            self.assertEqual(state["events"][GOV]["status"], "PENDING_RUNTIME")
            self.assertIsNone(state["events"][GOV].get("claim"))

    def test_owner_engine_mismatch_fails_closed_before_model_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 17, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, EVENING)
            drive = FakeDrive(
                {
                    19: task_row(
                        "House of Carol — wrong explicit owner route",
                        contract(
                            "aldernia.evening-commons",
                            PROFILE_MODEL_PUBLIC_READ,
                            catchup="POINT_IN_TIME_EXPIRES",
                        ),
                    )
                }
            )
            model = NeverModel()
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="runtime-owner-mismatch-test",
                max_events=1,
                now=datetime(2026, 9, 21, 17, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 1)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            self.assertEqual(
                state["events"][EVENING]["status"], "ACKNOWLEDGED_STOP"
            )
            self.assertIn(
                "EnginePlan validation failed closed",
                state["events"][EVENING]["receipt"]["result_summary"],
            )

    def test_point_in_time_catchup_expires_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 4, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, DAWN)
            bundle = json.dumps(
                {
                    "v": 1,
                    "duties": {
                        "catholic.dawn-offices": {
                            "profile": PROFILE_MODEL_PUBLIC_READ,
                            "engine_mode": "DECLARED",
                            "catchup": "POINT_IN_TIME_EXPIRES",
                        }
                    },
                },
                separators=(",", ":"),
            )
            drive = FakeDrive(
                {
                    16: task_row(
                        "House of Catholic — Luke / Cecilia",
                        bundle,
                    )
                }
            )
            result = run_once(
                **self.paths(root),
                drive=drive,
                model=None,
                worker_id="runtime-catchup-test",
                max_events=1,
                now=datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc),
            )
            self.assertEqual(len(result["processed"]), 1)
            state = json.loads(
                (root / "scheduled-duty-queue.json").read_text()
            )
            event = state["events"][DAWN]
            self.assertEqual(event["status"], "ACKNOWLEDGED_NO_ACTION")
            self.assertIn("EXPIRED / NO_ACTION", event["receipt"]["result_summary"])

    def test_model_call_is_budget_guarded_before_claim_and_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_scheduler(
                **self.paths(root),
                now=datetime(2026, 9, 21, 17, 1, tzinfo=timezone.utc),
            )
            self.keep_only(root, EVENING)
            drive = FakeDrive(
                {
                    19: task_row(
                        "House of Josie — editorial curation",
                        contract(
                            "aldernia.evening-commons",
                            PROFILE_MODEL_PUBLIC_READ,
                            catchup="POINT_IN_TIME_EXPIRES",
                        ),
                    )
                }
            )
            budget = FakeBudget()
            model = GenericModel()
            run_once(
                **self.paths(root),
                drive=drive,
                model=model,
                worker_id="runtime-budget-test",
                max_events=1,
                now=datetime(2026, 9, 21, 17, 2, tzinfo=timezone.utc),
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
