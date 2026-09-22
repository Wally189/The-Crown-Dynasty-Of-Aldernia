from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from aldernia_runtime.opportunity_resolver import (
    CapabilityProfile,
    Candidate,
    Resolver,
    StateClass,
    stable_hash,
)

SCHEMA_VERSION = 1
AUTHORITY_REF = "Royal Programme Board E-26"
SEED_CANDIDATE_ID = "STAGE3::bounded-derived-state-proof"
CONSEQUENCE_CANDIDATE_ID = "STAGE3::verify-derived-state-consequence"
EXECUTION_CLASS = "COS-WORKER-001::INTERNAL_DERIVED_STATE_TRANSITION"
INHERITED_EFFECTS = frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"})
PROFILES = {
    "COS-SEL-001": CapabilityProfile(
        "COS-SEL-001", "Computer of Series", INHERITED_EFFECTS, True
    ),
    "COS-WORKER-001": CapabilityProfile(
        "COS-WORKER-001", "Computer of Series", INHERITED_EFFECTS, True
    ),
}


class Stage3Error(RuntimeError):
    pass


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def _load_object(path: Path, *, default: Mapping[str, object]) -> dict[str, object]:
    if not path.exists():
        return dict(default)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Stage3Error(f"{path} must contain a JSON object")
    return value


def _default_state() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "READY",
        "first_receipt": None,
        "first_ack": None,
        "consequence": None,
        "second_receipt": None,
        "second_ack": None,
        "completed_at": None,
        "run_ids": [],
    }


def load_state(path: Path) -> dict[str, object]:
    state = _load_object(path, default=_default_state())
    if int(state.get("schema_version") or 0) != SCHEMA_VERSION:
        raise Stage3Error("unsupported Stage-3 state schema")
    phase = str(state.get("phase") or "")
    if phase not in {
        "READY",
        "FIRST_EXECUTED_UNACKED",
        "FIRST_ACKED",
        "SECOND_EXECUTED_UNACKED",
        "COMPLETE",
    }:
        raise Stage3Error(f"unsupported Stage-3 phase: {phase!r}")
    return state


def _receipt_hash(receipt: Mapping[str, object]) -> str:
    return stable_hash(dict(receipt))


def _verify_receipt(receipt: object, *, action_id: str) -> dict[str, object]:
    if not isinstance(receipt, Mapping):
        raise Stage3Error("missing durable execution receipt")
    value = dict(receipt)
    if value.get("action_id") != action_id:
        raise Stage3Error("execution receipt action identity mismatch")
    if value.get("execution_class") != EXECUTION_CLASS:
        raise Stage3Error("execution receipt class mismatch")
    if value.get("authority_ref") != AUTHORITY_REF:
        raise Stage3Error("execution receipt authority mismatch")
    if value.get("external_effect") != "NONE" or value.get("canonical_record_written") is not False:
        raise Stage3Error("Stage-3 receipt claims an unauthorised effect")
    if value.get("model_called") is not False or value.get("reversible") is not True:
        raise Stage3Error("Stage-3 receipt violates bounded execution contract")
    return value


def _write_and_verify(path: Path, state: Mapping[str, object]) -> dict[str, object]:
    _atomic_write_json(path, state)
    readback = load_state(path)
    if readback != dict(state):
        raise Stage3Error("Stage-3 durable state readback mismatch")
    return readback


def _candidate(
    *, candidate_id: str, evidence_hash: str, causal_parent: str | None, causal_depth: int
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        title=(
            "Write and verify the bounded Stage-3 internal derived-state receipt"
            if candidate_id == SEED_CANDIDATE_ID
            else "Verify and close the previously ACKed Stage-3 derived-state consequence"
        ),
        owner="Computer of Series",
        source_refs=[AUTHORITY_REF],
        evidence_hash=evidence_hash,
        authority_ref=AUTHORITY_REF,
        current=True,
        accepted_objective=True,
        evidence_sufficient=True,
        owner_priority="HIGH",
        urgency="HIGH",
        enabling_value="HIGH",
        user_value="MEDIUM",
        evidence_gain="HIGH",
        confidence="HIGH",
        reversible=True,
        resource_cost="NONE",
        required_profiles=("COS-SEL-001", "COS-WORKER-001"),
        requested_effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
        causal_parent=causal_parent,
        causal_depth=causal_depth,
    )


def _resolve_action(candidate: Candidate) -> str:
    resolver = Resolver(PROFILES, causal_depth_limit=1)
    resolution = resolver.resolve([candidate])
    decision = resolution.decisions[0]
    if decision.state_class != StateClass.ACTIONABLE_NOW:
        raise Stage3Error(
            f"Stage-3 candidate failed authority classification: {decision.state_class.value}"
        )
    if resolution.selected_candidate_id != candidate.candidate_id:
        raise Stage3Error("Stage-3 actionable candidate was not selected")
    resolver.compose_profiles(candidate, inherited_effects=INHERITED_EFFECTS)
    return resolution.termination


def _base_trace(*, run_id: str, phase_before: str, phase_after: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "authority_ref": AUTHORITY_REF,
        "execution_class": EXECUTION_CLASS,
        "run_id": run_id,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "model_called": False,
        "external_effect": "NONE",
        "canonical_record_written": False,
        "authority_widened": False,
        "derived_state_only": True,
        "reversible": True,
    }


def _first_ack(
    *, state: dict[str, object], state_path: Path, run_id: str, now: datetime
) -> dict[str, object]:
    first = _verify_receipt(state.get("first_receipt"), action_id=SEED_CANDIDATE_ID)
    first_hash = _receipt_hash(first)
    state["first_ack"] = {
        "action_id": SEED_CANDIDATE_ID,
        "receipt_hash": first_hash,
        "acked_at": now.isoformat(),
        "acked_run_id": run_id,
    }
    state["consequence"] = {
        "candidate_id": CONSEQUENCE_CANDIDATE_ID,
        "causal_parent": SEED_CANDIDATE_ID,
        "causal_depth": 1,
        "evidence_hash": stable_hash(
            {"first_receipt_hash": first_hash, "first_ack": state["first_ack"]}
        ),
        "status": "PENDING_LATER_INVOCATION",
        "emitted_at": now.isoformat(),
        "emitted_run_id": run_id,
    }
    state["phase"] = "FIRST_ACKED"
    return _write_and_verify(state_path, state)


def _second_ack(
    *, state: dict[str, object], state_path: Path, run_id: str, now: datetime
) -> dict[str, object]:
    second = _verify_receipt(
        state.get("second_receipt"), action_id=CONSEQUENCE_CANDIDATE_ID
    )
    consequence = state.get("consequence")
    if not isinstance(consequence, Mapping):
        raise Stage3Error("second-hop recovery lacks its consequence")
    state["second_ack"] = {
        "action_id": CONSEQUENCE_CANDIDATE_ID,
        "receipt_hash": _receipt_hash(second),
        "acked_at": now.isoformat(),
        "acked_run_id": run_id,
    }
    state["consequence"] = {
        **dict(consequence),
        "status": "ACKNOWLEDGED",
        "consumed_at": now.isoformat(),
        "consumed_run_id": run_id,
    }
    state["phase"] = "COMPLETE"
    state["completed_at"] = now.isoformat()
    return _write_and_verify(state_path, state)


def run_stage3(
    *,
    shadow_trace_path: Path,
    state_path: Path,
    trace_path: Path,
    run_id: str,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    if not run_id:
        raise Stage3Error("run_id is required")
    shadow_trace = _load_object(shadow_trace_path, default={})
    if shadow_trace.get("state_readback_verified") is not True:
        raise Stage3Error(
            "Stage-3 requires a verified Opportunity Resolver shadow readback"
        )
    if shadow_trace.get("external_effect") != "NONE":
        raise Stage3Error("Stage-3 refuses shadow input with external effects")

    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_state(state_path)
    phase_before = str(state["phase"])
    run_ids = [str(x) for x in (state.get("run_ids") or [])]
    if run_id not in run_ids:
        run_ids.append(run_id)
    state["run_ids"] = run_ids

    if phase_before == "FIRST_EXECUTED_UNACKED":
        state = _first_ack(state=state, state_path=state_path, run_id=run_id, now=now)
        trace = _base_trace(
            run_id=run_id, phase_before=phase_before, phase_after="FIRST_ACKED"
        )
        trace.update(
            selected_candidate_id=None,
            execution_performed=False,
            recovery_ack_performed=True,
            durable_ack=True,
            consequence_emitted=True,
            consequence_consumed=False,
            termination="WAIT_FOR_LATER_INVOCATION",
            state_readback_verified=True,
        )
        _atomic_write_json(trace_path, trace)
        return trace

    if phase_before == "SECOND_EXECUTED_UNACKED":
        state = _second_ack(state=state, state_path=state_path, run_id=run_id, now=now)
        trace = _base_trace(
            run_id=run_id, phase_before=phase_before, phase_after="COMPLETE"
        )
        trace.update(
            selected_candidate_id=None,
            execution_performed=False,
            recovery_ack_performed=True,
            durable_ack=True,
            consequence_emitted=False,
            consequence_consumed=True,
            termination="NO_ACTION_AFTER_VERIFIED_CONSEQUENCE",
            state_readback_verified=True,
        )
        _atomic_write_json(trace_path, trace)
        return trace

    if phase_before == "READY":
        shadow_fp = str(shadow_trace.get("source_fingerprint") or "")
        candidate = _candidate(
            candidate_id=SEED_CANDIDATE_ID,
            evidence_hash=stable_hash(
                {"authority": AUTHORITY_REF, "shadow_source_fingerprint": shadow_fp}
            ),
            causal_parent=None,
            causal_depth=0,
        )
        resolver_termination = _resolve_action(candidate)
        receipt = {
            "action_id": SEED_CANDIDATE_ID,
            "execution_class": EXECUTION_CLASS,
            "authority_ref": AUTHORITY_REF,
            "operation": "DERIVED_STATE_ACCEPTANCE_RECEIPT",
            "performed_at": now.isoformat(),
            "performed_run_id": run_id,
            "shadow_source_fingerprint": shadow_fp,
            "external_effect": "NONE",
            "canonical_record_written": False,
            "model_called": False,
            "reversible": True,
        }
        state["first_receipt"] = receipt
        state["phase"] = "FIRST_EXECUTED_UNACKED"
        state = _write_and_verify(state_path, state)
        verified = _verify_receipt(
            state.get("first_receipt"), action_id=SEED_CANDIDATE_ID
        )
        receipt_hash = _receipt_hash(verified)
        state = _first_ack(state=state, state_path=state_path, run_id=run_id, now=now)
        trace = _base_trace(
            run_id=run_id, phase_before=phase_before, phase_after="FIRST_ACKED"
        )
        trace.update(
            selected_candidate_id=SEED_CANDIDATE_ID,
            resolver_termination=resolver_termination,
            execution_performed=True,
            verification_performed=True,
            durable_ack=True,
            first_receipt_hash=receipt_hash,
            consequence_emitted=True,
            consequence_consumed=False,
            consequence_status="PENDING_LATER_INVOCATION",
            termination="WAIT_FOR_LATER_INVOCATION",
            state_readback_verified=True,
        )
        _atomic_write_json(trace_path, trace)
        return trace

    if phase_before == "FIRST_ACKED":
        consequence = state.get("consequence")
        if not isinstance(consequence, Mapping):
            raise Stage3Error("ACKed first hop has no durable consequence")
        if consequence.get("status") != "PENDING_LATER_INVOCATION":
            raise Stage3Error("unexpected Stage-3 consequence state")
        if str(consequence.get("emitted_run_id") or "") == run_id:
            trace = _base_trace(
                run_id=run_id, phase_before=phase_before, phase_after=phase_before
            )
            trace.update(
                selected_candidate_id=None,
                execution_performed=False,
                durable_ack=True,
                consequence_emitted=False,
                consequence_consumed=False,
                termination="WAIT_FOR_LATER_INVOCATION",
                state_readback_verified=True,
            )
            _atomic_write_json(trace_path, trace)
            return trace

        first = _verify_receipt(
            state.get("first_receipt"), action_id=SEED_CANDIDATE_ID
        )
        first_ack = state.get("first_ack")
        if (
            not isinstance(first_ack, Mapping)
            or first_ack.get("receipt_hash") != _receipt_hash(first)
        ):
            raise Stage3Error("first-hop durable ACK does not match its receipt")
        candidate = _candidate(
            candidate_id=CONSEQUENCE_CANDIDATE_ID,
            evidence_hash=str(consequence.get("evidence_hash") or ""),
            causal_parent=SEED_CANDIDATE_ID,
            causal_depth=1,
        )
        resolver_termination = _resolve_action(candidate)
        receipt = {
            "action_id": CONSEQUENCE_CANDIDATE_ID,
            "execution_class": EXECUTION_CLASS,
            "authority_ref": AUTHORITY_REF,
            "operation": "VERIFY_PRIOR_DERIVED_RECEIPT",
            "performed_at": now.isoformat(),
            "performed_run_id": run_id,
            "verified_first_receipt_hash": _receipt_hash(first),
            "external_effect": "NONE",
            "canonical_record_written": False,
            "model_called": False,
            "reversible": True,
        }
        state["second_receipt"] = receipt
        state["phase"] = "SECOND_EXECUTED_UNACKED"
        state = _write_and_verify(state_path, state)
        _verify_receipt(
            state.get("second_receipt"), action_id=CONSEQUENCE_CANDIDATE_ID
        )
        state = _second_ack(state=state, state_path=state_path, run_id=run_id, now=now)
        trace = _base_trace(
            run_id=run_id, phase_before=phase_before, phase_after="COMPLETE"
        )
        trace.update(
            selected_candidate_id=CONSEQUENCE_CANDIDATE_ID,
            resolver_termination=resolver_termination,
            execution_performed=True,
            verification_performed=True,
            durable_ack=True,
            consequence_emitted=False,
            consequence_consumed=True,
            causal_depth=1,
            termination="NO_ACTION_AFTER_VERIFIED_CONSEQUENCE",
            state_readback_verified=True,
        )
        _atomic_write_json(trace_path, trace)
        return trace

    if phase_before == "COMPLETE":
        _verify_receipt(
            state.get("first_receipt"), action_id=SEED_CANDIDATE_ID
        )
        _verify_receipt(
            state.get("second_receipt"), action_id=CONSEQUENCE_CANDIDATE_ID
        )
        consequence = state.get("consequence")
        if (
            not isinstance(consequence, Mapping)
            or consequence.get("status") != "ACKNOWLEDGED"
        ):
            raise Stage3Error("complete Stage-3 state lacks an ACKed consequence")
        trace = _base_trace(
            run_id=run_id, phase_before=phase_before, phase_after=phase_before
        )
        trace.update(
            selected_candidate_id=None,
            execution_performed=False,
            durable_ack=True,
            consequence_emitted=False,
            consequence_consumed=False,
            termination="NO_ACTION",
            state_readback_verified=True,
        )
        _atomic_write_json(trace_path, trace)
        return trace

    raise Stage3Error(f"unhandled Stage-3 phase {phase_before}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the single bounded Stage-3 internal derived-state execution profile."
        )
    )
    parser.add_argument(
        "--shadow-trace", default="state/opportunity-resolver-trace.json"
    )
    parser.add_argument("--state", default="state/opportunity-stage3-state.json")
    parser.add_argument("--trace", default="state/opportunity-stage3-trace.json")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    run_id = args.run_id or os.environ.get("GITHUB_RUN_ID") or ""
    trace = run_stage3(
        shadow_trace_path=Path(args.shadow_trace),
        state_path=Path(args.state),
        trace_path=Path(args.trace),
        run_id=run_id,
    )
    print(json.dumps(trace, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
