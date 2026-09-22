from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from aldernia_runtime.canonical_programme_board import canonical_facts_from_packet
from aldernia_runtime.opportunity_resolver import (
    CapabilityProfile,
    Resolver,
    ResolverState,
    SourceFact,
    discover_candidates,
    stable_hash,
)

SHADOW_SCHEMA_VERSION = 1
ADAPTER_VERSION = 1

# Shadow mode can observe and write reconstructible derived state only. These
# profiles do not grant execution authority; they merely prove the existing
# registered capability names are available to a candidate classification.
PROFILES = {
    "COS-SEL-001": CapabilityProfile(
        "COS-SEL-001",
        "Computer of Series",
        frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
        True,
    ),
    "COS-AUTH-001": CapabilityProfile(
        "COS-AUTH-001",
        "Computer of Series",
        frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
        True,
    ),
    "COS-BUS-001": CapabilityProfile(
        "COS-BUS-001",
        "Computer of Series",
        frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
        True,
    ),
    "COS-WORKER-001": CapabilityProfile(
        "COS-WORKER-001",
        "Computer of Series",
        frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
        True,
    ),
}

PROGRAMME_BOARD_FACTS = (
    SourceFact(
        fact_id="programme-board-e23",
        source_ref="Royal Programme Board:E-23",
        owner="Royal Palace / Computer of Series",
        proposition="Self-progression computing commission finding and local prototype are already absorbed.",
        current=True,
        evidence_hash=stable_hash(
            {
                "entry": "E-23",
                "result": "PASS — COMMISSION FINDING + PROTOTYPE / OPPORTUNITY RESOLVER GAP CANONICALLY ABSORBED / NO NEW CROWN DECISION",
            }
        ),
        kind="COMPLETED_NO_CONSEQUENCE",
        objective_id="E-23",
        authority_ref="Royal Programme Board E-23",
    ),
    SourceFact(
        fact_id="programme-board-e24",
        source_ref="Royal Programme Board:E-24",
        owner="Royal Palace / Computer of Series",
        proposition="Stage-1 pure Opportunity Resolver candidate is complete; production integration was open.",
        current=True,
        evidence_hash=stable_hash(
            {
                "entry": "E-24",
                "result": "PASS — STAGE-1 PURE RESOLVER CANDIDATE / 22/22 TESTS / CURRENT-STATE SHADOW FIXTURE PASS / PRODUCTION INTEGRATION OPEN",
            }
        ),
        kind="COMPLETED_NO_CONSEQUENCE",
        objective_id="E-24",
        authority_ref="Royal Programme Board E-24",
    ),
)


class ShadowStateError(RuntimeError):
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
        raise ShadowStateError(f"{path} must contain a JSON object")
    return value


def load_shadow_state(path: Path) -> dict[str, object]:
    value = _load_object(
        path,
        default={
            "schema_version": SHADOW_SCHEMA_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "resolver_state": ResolverState().to_json(),
            "last_source_fingerprint": None,
            "last_decision_fingerprint": None,
            "run_count": 0,
            "last_observed_at": None,
        },
    )
    if int(value.get("schema_version") or 0) != SHADOW_SCHEMA_VERSION:
        raise ShadowStateError("unsupported opportunity shadow state schema")
    resolver_state = value.get("resolver_state")
    if resolver_state is not None and not isinstance(resolver_state, Mapping):
        raise ShadowStateError("resolver_state must be an object")
    # Parse once here so malformed derived state fails closed before overwrite.
    ResolverState.from_json(resolver_state if isinstance(resolver_state, Mapping) else None)
    return value


def provider_state(health: Mapping[str, object]) -> tuple[bool, bool]:
    status = str(health.get("status") or "NOT_YET_CHECKED")
    provider_available = status == "READY"
    budget_available = status != "BUDGET_STOP"
    return provider_available, budget_available


def normalized_runtime_facts(
    *,
    queue: Mapping[str, object],
    health: Mapping[str, object],
    canonical_packet: Mapping[str, object] | None = None,
    acknowledged: Mapping[str, object] | None = None,
) -> tuple[SourceFact, ...]:
    """Normalize only explicit current runtime contracts into SourceFact values.

    No prose inference or model call occurs here. Unknown queue records are ignored.
    The KQ-009 blocker fingerprint is deliberately independent of pending-event IDs so
    unrelated Clock activity cannot make an unchanged provider blocker look new.
    """

    facts: list[SourceFact] = list(
        canonical_facts_from_packet(
            canonical_packet or {}, acknowledged=acknowledged or {}
        )
    )
    status = str(health.get("status") or "NOT_YET_CHECKED")
    detail = str(health.get("detail") or "")
    provider_available, budget_available = provider_state(health)

    if status == "PROVIDER_ACCESS_NOT_CONFIGURED":
        facts.append(
            SourceFact(
                fact_id="kq009-provider-gate",
                source_ref="GitHub runtime-health + existing KQ-009 authority",
                owner="Computer of Series",
                proposition="Complete the existing KQ-009 provider-administration gate.",
                current=True,
                evidence_hash=stable_hash({"status": status, "detail": detail}),
                kind="BLOCKER",
                status=status,
                objective_id="KQ-009",
                authority_ref="Existing KQ-009 provider-administration authority; not widened by shadow mode",
                dependency_id="google-wif-drive-provider-access",
                next_trigger="PROVIDER_STATE_CHANGE",
                metadata={
                    "owner_priority": "HIGH",
                    "urgency": "HIGH",
                    "enabling_value": "HIGH",
                },
            )
        )

    events = queue.get("events")
    if not isinstance(events, Mapping):
        return tuple(facts)

    for event_id in sorted(str(k) for k in events):
        raw = events.get(event_id)
        if not isinstance(raw, Mapping):
            continue
        event_status = str(raw.get("status") or "")
        if event_status not in {"PENDING_RUNTIME", "CLAIMED"}:
            continue
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            continue
        duty_id = str(payload.get("duty_id") or "")
        owner = str(payload.get("accountable_owner") or raw.get("emitting_owner") or "")
        authority_ref = str(raw.get("authority_ref") or "")
        if not duty_id or not owner or not authority_ref:
            continue
        requires_model = bool(payload.get("requires_model_runtime", False))
        evidence = {
            "event_id": event_id,
            "event_status": event_status,
            "authority_ref": authority_ref,
            "mission_ref": raw.get("mission_ref"),
            "scheduled_for": raw.get("scheduled_for"),
            "duty_id": duty_id,
            "requires_model_runtime": requires_model,
            "source_row": payload.get("source_row"),
        }
        facts.append(
            SourceFact(
                fact_id=f"runtime-event:{event_id}",
                source_ref=f"GitHub:clock-state/state/scheduled-duty-queue.json#{event_id}",
                owner=owner,
                proposition=f"Evaluate already-authorised due duty {duty_id} from existing COS-BUS event {event_id}.",
                current=True,
                evidence_hash=stable_hash(evidence),
                kind="AUTHORISED_INTERNAL_WORK",
                status=event_status,
                objective_id=f"DUE::{event_id}",
                authority_ref=authority_ref,
                metadata={
                    "owner_priority": "HIGH",
                    "urgency": "HIGH",
                    "enabling_value": "MEDIUM",
                    "user_value": "MEDIUM",
                    "evidence_gain": "LOW",
                    "confidence": "HIGH",
                    "reversible": True,
                    "resource_cost": "LOW",
                    "required_profiles": ["COS-BUS-001", "COS-WORKER-001"],
                    "requested_effects": [],
                    "needs_model": requires_model,
                    "provider_available": provider_available,
                    "budget_available": budget_available,
                },
            )
        )
    return tuple(facts)


def _decision_json(decision: object) -> dict[str, object]:
    value = asdict(decision)
    state_class = value.get("state_class")
    value["state_class"] = getattr(state_class, "value", str(state_class))
    if value.get("priority_key") is not None:
        value["priority_key"] = list(value["priority_key"])
    value["reasons"] = list(value.get("reasons") or [])
    return value


def run_shadow(
    *,
    queue_path: Path,
    health_path: Path,
    state_path: Path,
    trace_path: Path,
    canonical_packet_path: Path | None = None,
    stage4_state_path: Path | None = None,
    observed_at: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    queue = _load_object(queue_path, default={"schema_version": 1, "events": {}})
    health = _load_object(
        health_path,
        default={
            "schema_version": 1,
            "status": "NOT_YET_CHECKED",
            "detail": "No governed runtime health result is available.",
        },
    )
    canonical_packet = (
        _load_object(canonical_packet_path, default={})
        if canonical_packet_path is not None
        else {}
    )
    stage4_state = (
        _load_object(stage4_state_path, default={"schema_version": 1, "acknowledged": {}})
        if stage4_state_path is not None
        else {"schema_version": 1, "acknowledged": {}}
    )
    acknowledged = stage4_state.get("acknowledged")
    if not isinstance(acknowledged, Mapping):
        raise ShadowStateError("Stage-4 acknowledged state must be an object")
    prior = load_shadow_state(state_path)
    resolver_state_raw = prior.get("resolver_state")
    resolver_state = ResolverState.from_json(
        resolver_state_raw if isinstance(resolver_state_raw, Mapping) else None
    )

    facts = normalized_runtime_facts(
        queue=queue,
        health=health,
        canonical_packet=canonical_packet,
        acknowledged=acknowledged,
    )
    candidates = discover_candidates(facts)
    resolver = Resolver(PROFILES, causal_depth_limit=2)
    resolution = resolver.resolve(candidates, resolver_state)

    source_fingerprint = stable_hash(
        [
            {
                "fact_id": f.fact_id,
                "source_ref": f.source_ref,
                "evidence_hash": f.evidence_hash,
                "kind": f.kind,
                "status": f.status,
                "objective_id": f.objective_id,
            }
            for f in facts
        ]
    )
    decision_values = [_decision_json(d) for d in resolution.decisions]
    semantic_decisions = [
        {
            "candidate_id": decision.get("candidate_id"),
            "state_class": decision.get("state_class"),
            "priority_key": decision.get("priority_key"),
        }
        for decision in decision_values
    ]
    decision_fingerprint = stable_hash(
        {
            "selected_candidate_id": resolution.selected_candidate_id,
            "termination": resolution.termination,
            "conflict_candidate_ids": list(resolution.conflict_candidate_ids),
            "decisions": semantic_decisions,
        }
    )
    material_change = source_fingerprint != prior.get("last_source_fingerprint")
    decision_changed = decision_fingerprint != prior.get("last_decision_fingerprint")
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    run_count = int(prior.get("run_count") or 0) + 1

    next_state: dict[str, object] = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "resolver_state": resolution.next_state,
        "last_source_fingerprint": source_fingerprint,
        "last_decision_fingerprint": decision_fingerprint,
        "run_count": run_count,
        "last_observed_at": now.isoformat(),
    }
    trace: dict[str, object] = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "shadow_mode": True,
        "observed_at": now.isoformat(),
        "run_id": run_id or os.environ.get("GITHUB_RUN_ID") or "local-shadow-run",
        "run_count": run_count,
        "source_fingerprint": source_fingerprint,
        "material_source_change": material_change,
        "decision_fingerprint": decision_fingerprint,
        "decision_changed": decision_changed,
        "fact_count": len(facts),
        "candidate_count": len(candidates),
        "fact_ids": [f.fact_id for f in facts],
        "candidate_ids": [c.candidate_id for c in candidates],
        "selected_candidate_id": resolution.selected_candidate_id,
        "resolver_termination": resolution.termination,
        "conflict_candidate_ids": list(resolution.conflict_candidate_ids),
        "decisions": decision_values,
        "execution_performed": False,
        "model_called": False,
        "external_effect": "NONE",
        "authority_widened": False,
        "canonical_record_written": False,
        "derived_state_only": True,
    }

    _atomic_write_json(state_path, next_state)
    # Durable readback is part of shadow acceptance. Parse and compare exactly before
    # publishing the trace as the run result.
    readback = load_shadow_state(state_path)
    if readback != next_state:
        raise ShadowStateError("derived resolver state readback mismatch")
    trace["state_readback_verified"] = True
    _atomic_write_json(trace_path, trace)
    trace_readback = _load_object(trace_path, default={})
    if trace_readback != trace:
        raise ShadowStateError("derived resolver trace readback mismatch")
    return trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Observe current Aldernian runtime state through the Opportunity Resolver in non-executing shadow mode."
    )
    parser.add_argument("--queue", default="state/scheduled-duty-queue.json")
    parser.add_argument("--health", default="state/runtime-health.json")
    parser.add_argument("--state", default="state/opportunity-resolver-state.json")
    parser.add_argument("--trace", default="state/opportunity-resolver-trace.json")
    parser.add_argument("--canonical-packet", default="state/canonical-programme-board.json")
    parser.add_argument("--stage4-state", default="state/opportunity-stage4-state.json")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    trace = run_shadow(
        queue_path=Path(args.queue),
        health_path=Path(args.health),
        state_path=Path(args.state),
        trace_path=Path(args.trace),
        canonical_packet_path=Path(args.canonical_packet),
        stage4_state_path=Path(args.stage4_state),
        run_id=args.run_id,
    )
    print(json.dumps(trace, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
