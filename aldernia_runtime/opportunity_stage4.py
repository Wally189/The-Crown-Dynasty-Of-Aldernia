from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from aldernia_runtime.canonical_programme_board import (
    ALLOWED_EFFECT,
    ALLOWED_EXECUTION_CLASS,
    ALLOWED_PROFILES,
    canonical_facts_from_packet,
    load_packet,
    machine_rows,
)
from aldernia_runtime.opportunity_resolver import (
    CapabilityProfile,
    Resolver,
    StateClass,
    discover_candidates,
    stable_hash,
)

SCHEMA_VERSION = 1
INHERITED_EFFECTS = frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"})
PROFILES = {
    profile_id: CapabilityProfile(
        profile_id,
        "Computer of Series",
        INHERITED_EFFECTS,
        True,
    )
    for profile_id in ALLOWED_PROFILES
}


class Stage4Error(RuntimeError):
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
        raise Stage4Error(f"{path} must contain a JSON object")
    return value


def _default_state() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pending_receipt": None,
        "acknowledged": {},
        "run_ids": [],
    }


def load_state(path: Path) -> dict[str, object]:
    state = _load_object(path, default=_default_state())
    if int(state.get("schema_version") or 0) != SCHEMA_VERSION:
        raise Stage4Error("unsupported Stage-4 state schema")
    if not isinstance(state.get("acknowledged"), Mapping):
        raise Stage4Error("Stage-4 acknowledged state must be an object")
    pending = state.get("pending_receipt")
    if pending is not None and not isinstance(pending, Mapping):
        raise Stage4Error("Stage-4 pending receipt must be an object or null")
    return state


def _write_and_verify(path: Path, state: Mapping[str, object]) -> dict[str, object]:
    _atomic_write_json(path, state)
    readback = load_state(path)
    if readback != dict(state):
        raise Stage4Error("Stage-4 durable state readback mismatch")
    return readback


def _receipt_hash(receipt: Mapping[str, object]) -> str:
    return stable_hash(dict(receipt))


def _verify_receipt(receipt: object) -> dict[str, object]:
    if not isinstance(receipt, Mapping):
        raise Stage4Error("Stage-4 durable receipt is missing")
    value = dict(receipt)
    if value.get("execution_class") != ALLOWED_EXECUTION_CLASS:
        raise Stage4Error("Stage-4 execution class mismatch")
    if value.get("effect") != ALLOWED_EFFECT:
        raise Stage4Error("Stage-4 effect mismatch")
    if value.get("external_effect") != "NONE":
        raise Stage4Error("Stage-4 receipt contains an external effect")
    if value.get("model_called") is not False:
        raise Stage4Error("Stage-4 proof may not call a model")
    if value.get("canonical_record_written") is not False:
        raise Stage4Error("Stage-4 worker may not write canonical records")
    if value.get("authority_widened") is not False:
        raise Stage4Error("Stage-4 receipt widens authority")
    if value.get("reversible") is not True:
        raise Stage4Error("Stage-4 execution must remain reversible")
    if not str(value.get("candidate_id") or ""):
        raise Stage4Error("Stage-4 receipt lacks candidate identity")
    if not str(value.get("evidence_hash") or ""):
        raise Stage4Error("Stage-4 receipt lacks evidence identity")
    if not str(value.get("authority_ref") or "").startswith("Royal Programme Board E-"):
        raise Stage4Error("Stage-4 receipt authority is not the canonical Programme Board")
    return value


def _ack_pending(
    *, state: dict[str, object], state_path: Path, run_id: str, now: datetime
) -> tuple[dict[str, object], str]:
    receipt = _verify_receipt(state.get("pending_receipt"))
    candidate_id = str(receipt["candidate_id"])
    receipt_hash = _receipt_hash(receipt)
    acknowledged = dict(state.get("acknowledged") or {})
    acknowledged[candidate_id] = {
        "candidate_id": candidate_id,
        "evidence_hash": str(receipt["evidence_hash"]),
        "receipt_hash": receipt_hash,
        "authority_ref": str(receipt["authority_ref"]),
        "source_ref": str(receipt.get("source_ref") or ""),
        "acked_at": now.isoformat(),
        "acked_run_id": run_id,
    }
    state["acknowledged"] = acknowledged
    state["pending_receipt"] = None
    return _write_and_verify(state_path, state), receipt_hash


def _base_trace(*, run_id: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "execution_class": ALLOWED_EXECUTION_CLASS,
        "effect": ALLOWED_EFFECT,
        "model_called": False,
        "external_effect": "NONE",
        "canonical_record_written": False,
        "authority_widened": False,
        "derived_state_only": True,
        "reversible": True,
        "consequence_emitted": False,
        "causal_depth": 0,
    }


def run_stage4(
    *,
    shadow_trace_path: Path,
    canonical_packet_path: Path,
    state_path: Path,
    trace_path: Path,
    run_id: str,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    if not run_id:
        raise Stage4Error("run_id is required")
    shadow = _load_object(shadow_trace_path, default={})
    if shadow.get("state_readback_verified") is not True:
        raise Stage4Error("Stage-4 requires verified resolver readback")
    if shadow.get("external_effect") != "NONE":
        raise Stage4Error("Stage-4 refuses resolver input with external effects")
    packet = load_packet(canonical_packet_path)
    state = load_state(state_path)
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_ids = [str(x) for x in (state.get("run_ids") or [])]
    if run_id not in run_ids:
        run_ids.append(run_id)
    state["run_ids"] = run_ids

    if state.get("pending_receipt") is not None:
        state, receipt_hash = _ack_pending(
            state=state, state_path=state_path, run_id=run_id, now=now
        )
        trace = _base_trace(run_id=run_id)
        trace.update(
            selected_candidate_id=None,
            execution_performed=False,
            recovery_ack_performed=True,
            durable_ack=True,
            receipt_hash=receipt_hash,
            termination="NO_ACTION_AFTER_RECOVERED_ACK",
            state_readback_verified=True,
            canonical_transport=str(packet.get("transport") or ""),
        )
        _atomic_write_json(trace_path, trace)
        return trace

    selected = shadow.get("selected_candidate_id")
    if not selected:
        state = _write_and_verify(state_path, state)
        trace = _base_trace(run_id=run_id)
        trace.update(
            selected_candidate_id=None,
            execution_performed=False,
            durable_ack=True,
            termination="NO_ACTION",
            state_readback_verified=True,
            canonical_transport=str(packet.get("transport") or ""),
        )
        _atomic_write_json(trace_path, trace)
        return trace

    selected = str(selected)
    row = next(
        (
            item
            for item in machine_rows(packet)
            if isinstance(item.get("contract"), Mapping)
            and str(item["contract"].get("candidate_id") or "") == selected
        ),
        None,
    )
    if row is None:
        state = _write_and_verify(state_path, state)
        trace = _base_trace(run_id=run_id)
        trace.update(
            selected_candidate_id=selected,
            execution_performed=False,
            durable_ack=False,
            termination="NO_ACTION_NOT_STAGE4_PROFILE",
            state_readback_verified=True,
            canonical_transport=str(packet.get("transport") or ""),
        )
        _atomic_write_json(trace_path, trace)
        return trace

    contract = row["contract"]
    assert isinstance(contract, Mapping)
    if str(contract.get("execution_class") or "") != ALLOWED_EXECUTION_CLASS:
        raise Stage4Error("selected canonical contract names a different execution class")
    if str(contract.get("effect") or "") != ALLOWED_EFFECT:
        raise Stage4Error("selected canonical contract requests a different effect")
    if tuple(contract.get("profiles") or ()) != ALLOWED_PROFILES:
        raise Stage4Error("selected canonical contract changes registered profile composition")
    if contract.get("consequence") != "NONE":
        raise Stage4Error("Stage-4 proof does not authorise a causal child")

    acknowledged = state.get("acknowledged")
    assert isinstance(acknowledged, Mapping)
    prior = acknowledged.get(selected)
    if isinstance(prior, Mapping) and prior.get("evidence_hash") == row.get("evidence_hash"):
        state = _write_and_verify(state_path, state)
        trace = _base_trace(run_id=run_id)
        trace.update(
            selected_candidate_id=selected,
            execution_performed=False,
            durable_ack=True,
            termination="NO_ACTION_ALREADY_ACKNOWLEDGED",
            state_readback_verified=True,
            canonical_transport=str(packet.get("transport") or ""),
        )
        _atomic_write_json(trace_path, trace)
        return trace

    facts = canonical_facts_from_packet(packet, acknowledged={})
    fact = next((f for f in facts if f.objective_id == selected), None)
    if fact is None:
        raise Stage4Error("selected canonical candidate has no normalized SourceFact")
    candidates = discover_candidates([fact])
    if len(candidates) != 1:
        raise Stage4Error("selected canonical candidate did not normalize uniquely")
    candidate = candidates[0]
    resolver = Resolver(PROFILES, causal_depth_limit=1)
    resolution = resolver.resolve([candidate])
    decision = resolution.decisions[0]
    if decision.state_class != StateClass.ACTIONABLE_NOW:
        raise Stage4Error(
            f"Stage-4 selected candidate is not actionable: {decision.state_class.value}"
        )
    if resolution.selected_candidate_id != selected:
        raise Stage4Error("Stage-4 resolver did not select the canonical candidate")
    resolver.compose_profiles(candidate, inherited_effects=INHERITED_EFFECTS)

    receipt = {
        "candidate_id": selected,
        "evidence_hash": str(row["evidence_hash"]),
        "authority_ref": f"Royal Programme Board {row['entry_id']}",
        "source_ref": str(row["source_ref"]),
        "canonical_transport": str(row["transport"]),
        "source_modified_time": str(row["source_modified_time"]),
        "execution_class": ALLOWED_EXECUTION_CLASS,
        "effect": ALLOWED_EFFECT,
        "operation": "ACK_LIVE_CANONICAL_DELTA",
        "performed_at": now.isoformat(),
        "performed_run_id": run_id,
        "external_effect": "NONE",
        "model_called": False,
        "canonical_record_written": False,
        "authority_widened": False,
        "reversible": True,
    }
    state["pending_receipt"] = receipt
    state = _write_and_verify(state_path, state)
    _verify_receipt(state.get("pending_receipt"))
    state, receipt_hash = _ack_pending(
        state=state, state_path=state_path, run_id=run_id, now=now
    )

    trace = _base_trace(run_id=run_id)
    trace.update(
        selected_candidate_id=selected,
        resolver_termination=resolution.termination,
        execution_performed=True,
        verification_performed=True,
        durable_ack=True,
        receipt_hash=receipt_hash,
        authority_ref=f"Royal Programme Board {row['entry_id']}",
        evidence_hash=str(row["evidence_hash"]),
        canonical_transport=str(row["transport"]),
        termination="NO_ACTION_AFTER_VERIFIED_ACK",
        state_readback_verified=True,
    )
    _atomic_write_json(trace_path, trace)
    return trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute the single Stage-4 canonical-delta acknowledgement profile."
    )
    parser.add_argument("--shadow-trace", default="state/opportunity-resolver-trace.json")
    parser.add_argument("--canonical-packet", default="state/canonical-programme-board.json")
    parser.add_argument("--state", default="state/opportunity-stage4-state.json")
    parser.add_argument("--trace", default="state/opportunity-stage4-trace.json")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    trace = run_stage4(
        shadow_trace_path=Path(args.shadow_trace),
        canonical_packet_path=Path(args.canonical_packet),
        state_path=Path(args.state),
        trace_path=Path(args.trace),
        run_id=args.run_id or os.environ.get("GITHUB_RUN_ID") or "",
    )
    print(json.dumps(trace, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
