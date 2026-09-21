from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from aldernia_runtime.patrol import PatrolError, plan_slice, validate_patrol_result
from aldernia_runtime.scheduler import load_scheduler_state, load_timetable
from aldernia_runtime.session import load_session
from aldernia_runtime.transport import pulse_completion_event

WORKER_STATE_VERSION = 1
CLAIM_LEASE = timedelta(minutes=30)
MAX_ATTEMPTS = 2

COMMON_REQUIRED_SOURCE_IDS = (
    "14rxhnw7MK5LLnj8QbrokDAeKBV_PlOg4ffuCUsV7ghI",  # CAT-123
    "1wh9C9IGxMxRtvenxLnknFvEWQgN7aI_8qaf8ES-SyiY",  # Computer FIRST READ
    "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA",  # Palace Scheduled Tasks
    "1NumS4_cKvgJLQfUIG45Nug7clbv2zuxU2EZ9bvSJdXQ",  # Engine Manifest / COS-BUS
    "1f7CvUz3abW0WuuPkoDRdwnLDs4Bgd-K0yfcNszQ9Crk",  # HOS execution control
)

ALLOWED_RESOURCE_CLASSES = {"ALDERNIA_INTERNAL", "PUBLIC_READ_ONLY"}
MODEL_RUNTIME_CLASS = "CONNECTED_GOVERNED_MODEL_RUNTIME"
TERMINAL_STATUSES = {
    "ACKNOWLEDGED_ACTION",
    "ACKNOWLEDGED_NO_ACTION",
    "ACKNOWLEDGED_STOP",
    "FAILED_CLOSED",
}


class WorkerError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise WorkerError("worker time must be timezone-aware")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise WorkerError("persisted worker time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    os.replace(temp_name, path)


def _duty_map(timetable_path: Path) -> dict[str, dict[str, Any]]:
    timetable = load_timetable(timetable_path)
    return {str(duty["id"]): dict(duty) for duty in timetable["duties"]}


def _event_and_duty(
    *,
    state: dict[str, Any],
    timetable_path: Path,
    event_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event = state.get("events", {}).get(event_id)
    if not isinstance(event, dict):
        raise WorkerError(f"unknown scheduled-duty event: {event_id}")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise WorkerError("event payload is missing")
    duty_id = payload.get("duty_id")
    if not isinstance(duty_id, str) or not duty_id:
        raise WorkerError("event duty_id is missing")
    duty = _duty_map(timetable_path).get(duty_id)
    if duty is None:
        raise WorkerError(f"duty {duty_id} is not present in the current timetable")
    owner = duty.get("owner")
    if not isinstance(owner, str) or not owner:
        raise WorkerError(f"duty {duty_id} has no accountable owner")
    projected_owner = payload.get("accountable_owner")
    if projected_owner is not None and projected_owner != owner:
        raise WorkerError("event owner conflicts with the current timetable owner")
    return event, duty


def _claim_id(event_id: str, worker_id: str, attempt: int, now: datetime) -> str:
    raw = f"{event_id}|{worker_id}|{attempt}|{_iso(now)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _recover_expired_claim_in_memory(
    event: dict[str, Any],
    *,
    now: datetime,
) -> str:
    if event.get("status") != "CLAIMED_RUNTIME":
        return "NOT_CLAIMED"
    claim = event.get("claim")
    if not isinstance(claim, dict):
        raise WorkerError("claimed event has no valid claim record")
    expiry = claim.get("lease_expires_at")
    if not isinstance(expiry, str):
        raise WorkerError("claim lease expiry is missing")
    if now.astimezone(timezone.utc) < _dt(expiry):
        return "CLAIM_ACTIVE"

    attempt = int(claim.get("attempt") or 0)
    event.setdefault("claim_history", []).append(dict(claim))
    if attempt >= MAX_ATTEMPTS:
        event["status"] = "FAILED_CLOSED"
        event["receipt"] = {
            "outcome": "STOP",
            "result_summary": "Claim lease expired after the maximum safe recovery attempts.",
            "completed_at": _iso(now),
            "readback_verified": True,
            "external_effect": "NONE",
            "resource_classes_used": ["ALDERNIA_INTERNAL"],
            "failure_class": "DELIVERY_UNCONFIRMED",
        }
        return "FAILED_CLOSED"

    event["status"] = "PENDING_RUNTIME"
    event["claim"] = None
    event["last_recovery"] = {
        "at": _iso(now),
        "reason": "EXPIRED CLAIM RETURNED TO PENDING_RUNTIME",
        "next_attempt": attempt + 1,
    }
    return "RECOVERED_TO_PENDING"



def _create_government_red_box_child(
    *,
    state: dict[str, Any],
    timetable_path: Path,
    parent_event: dict[str, Any],
    parent_event_id: str,
    parent_receipt: dict[str, Any],
    domain_terminal_state: str,
    now: datetime,
) -> str:
    """Create exactly one completion-triggered Red Box child for one Government pulse."""
    if domain_terminal_state not in {"VERIFIED_CLOSED", "FAILED_CLOSED"}:
        raise WorkerError("Government pulse must terminate VERIFIED_CLOSED or FAILED_CLOSED")
    red_box_duty = _duty_map(timetable_path).get("government.red-box")
    if red_box_duty is None:
        raise WorkerError("government.red-box event-child duty is missing from the current timetable")

    date_key = parent_event_id.rsplit(":", 1)[-1]
    child_id = f"government.red-box:{date_key}"
    existing = state.get("events", {}).get(child_id)
    if isinstance(existing, dict):
        if (existing.get("payload") or {}).get("parent_event_id") != parent_event_id:
            raise WorkerError("Government Red Box stable child identity conflicts with another parent")
        parent_receipt["domain_terminal_state"] = domain_terminal_state
        parent_receipt["red_box_child_event_id"] = child_id
        return child_id

    event_type = pulse_completion_event(domain_terminal_state)
    scheduled_raw = parent_event.get("scheduled_for")
    if not isinstance(scheduled_raw, str):
        raise WorkerError("Government parent scheduled_for is missing")
    child_scheduled = _iso(_dt(scheduled_raw) + timedelta(seconds=1))
    source_row = int(red_box_duty.get("source_row") or 0)
    state.setdefault("events", {})[child_id] = {
        "event_id": child_id,
        "event_type": event_type,
        "emitted_at": _iso(now),
        "emitting_owner": "House of Marianne — Centre of Government & Cabinet Coordination",
        "authority_ref": (
            "Royal Palace Scheduled Tasks Register "
            "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA, "
            f"Scheduled Tasks row {source_row}"
        ),
        "mission_ref": f"government-pulse:{date_key}",
        "departure_mode": "COMPLETION",
        "service_class": "TRAM",
        "destination": "Government Red Box Tram → Royal Palace / King's Balcony",
        "acceptance": (
            "Red Box is delivered to the King's Balcony and durable ACK/readback "
            "is recorded for the same parent pulse."
        ),
        "ack_to": "Daily Government Decision Red Box ledger / parent pulse",
        "scheduled_for": child_scheduled,
        "status": "PENDING_RUNTIME",
        "payload": {
            "duty_id": "government.red-box",
            "label": red_box_duty.get("label"),
            "source_row": source_row,
            "accountable_owner": red_box_duty.get("owner"),
            "required_source_ids": list(red_box_duty.get("required_source_ids") or []),
            "requires_model_runtime": True,
            "evaluation_only": False,
            "parent_event_id": parent_event_id,
            "parent_case_id": f"government-pulse:{date_key}",
            "parent_terminal_state": domain_terminal_state,
        },
        "permitted_effects": [
            "build decision/accountability Red Box from parent evidence",
            "deliver user-visible Balcony return",
            "write delivery ACK",
        ],
        "prohibited_effects": [
            "run before parent closure",
            "make a second Government decision",
            "invent decisions or scrutiny",
        ],
    }
    parent_receipt["domain_terminal_state"] = domain_terminal_state
    parent_receipt["red_box_child_event_id"] = child_id
    return child_id

def recover_expired_claim(
    *,
    state_path: Path,
    timetable_path: Path,
    session_path: Path,
    event_id: str,
    now: datetime,
) -> dict[str, Any]:
    session = load_session(session_path)
    if session.get("operating_state") == "HALTED":
        raise WorkerError("HALT dominates recovery")

    state = load_scheduler_state(state_path)
    event, duty = _event_and_duty(
        state=state, timetable_path=timetable_path, event_id=event_id
    )
    result = _recover_expired_claim_in_memory(event, now=now)
    if result == "FAILED_CLOSED" and duty["id"] == "government.daily-pulse":
        receipt = event.get("receipt")
        if isinstance(receipt, dict):
            _create_government_red_box_child(
                state=state,
                timetable_path=timetable_path,
                parent_event=event,
                parent_event_id=event_id,
                parent_receipt=receipt,
                domain_terminal_state="FAILED_CLOSED",
                now=now,
            )
    _write_state(state_path, state)
    return {
        "event_id": event_id,
        "status": event.get("status"),
        "recovery": result,
        "accountable_owner": duty["owner"],
    }


def claim_event(
    *,
    state_path: Path,
    timetable_path: Path,
    session_path: Path,
    event_id: str,
    worker_id: str,
    runtime_class: str,
    now: datetime,
) -> dict[str, Any]:
    if not worker_id.strip():
        raise WorkerError("worker_id is required")
    session = load_session(session_path)
    if session.get("operating_state") == "HALTED":
        raise WorkerError("HALT dominates claim and dispatch")

    state = load_scheduler_state(state_path)
    event, duty = _event_and_duty(
        state=state, timetable_path=timetable_path, event_id=event_id
    )

    if event.get("status") in TERMINAL_STATUSES:
        return {
            "event_id": event_id,
            "status": "DUPLICATE_TERMINAL",
            "terminal_status": event.get("status"),
            "accountable_owner": duty["owner"],
        }

    if event.get("status") == "CLAIMED_RUNTIME":
        recovery = _recover_expired_claim_in_memory(event, now=now)
        if recovery == "CLAIM_ACTIVE":
            claim = event["claim"]
            if claim.get("worker_id") != worker_id:
                raise WorkerError("event already has an active claim by another runtime")
            _write_state(state_path, state)
            return {
                "event_id": event_id,
                "status": "RESUMED_CLAIM",
                "claim_id": claim["claim_id"],
                "attempt": claim["attempt"],
                "accountable_owner": duty["owner"],
                "lease_expires_at": claim["lease_expires_at"],
                "integrity_patrol": claim.get("integrity_patrol"),
            }
        if recovery == "FAILED_CLOSED":
            if duty["id"] == "government.daily-pulse":
                receipt = event.get("receipt")
                if isinstance(receipt, dict):
                    _create_government_red_box_child(
                        state=state,
                        timetable_path=timetable_path,
                        parent_event=event,
                        parent_event_id=event_id,
                        parent_receipt=receipt,
                        domain_terminal_state="FAILED_CLOSED",
                        now=now,
                    )
            _write_state(state_path, state)
            return {
                "event_id": event_id,
                "status": "FAILED_CLOSED",
                "accountable_owner": duty["owner"],
            }

    if event.get("status") != "PENDING_RUNTIME":
        raise WorkerError(f"event is not claimable from status {event.get('status')!r}")

    requires_model = bool(event.get("payload", {}).get("requires_model_runtime"))
    if requires_model and runtime_class != MODEL_RUNTIME_CLASS:
        raise WorkerError("model-bearing duty requires a governed connected model runtime")

    prior_claims = event.get("claim_history") or []
    if not isinstance(prior_claims, list):
        raise WorkerError("claim_history must be a list")
    attempt = len(prior_claims) + 1
    if attempt > MAX_ATTEMPTS:
        raise WorkerError("safe claim attempt limit exceeded")

    patrol_plan = plan_slice(state) if duty["id"] == "dynasty.heartbeat" else None
    claim_id = _claim_id(event_id, worker_id, attempt, now)
    claim = {
        "schema_version": WORKER_STATE_VERSION,
        "claim_id": claim_id,
        "worker_id": worker_id,
        "runtime_class": runtime_class,
        "accountable_owner": duty["owner"],
        "claimed_at": _iso(now),
        "lease_expires_at": _iso(now + CLAIM_LEASE),
        "attempt": attempt,
        "estate_scope": "ALDERNIA",
        "new_model_provider_access_granted": False,
        "external_access_grants": [],
        "mission_ref": event.get("mission_ref"),
        "authority_ref": event.get("authority_ref"),
    }
    if patrol_plan is not None:
        claim["integrity_patrol"] = patrol_plan

    required_source_ids = list(COMMON_REQUIRED_SOURCE_IDS) + list(
        duty.get("required_source_ids") or []
    )
    if patrol_plan is not None:
        required_source_ids.extend(patrol_plan["required_source_ids"])
    required_source_ids = list(dict.fromkeys(required_source_ids))

    event["status"] = "CLAIMED_RUNTIME"
    event["claim"] = claim
    event["payload"]["accountable_owner"] = duty["owner"]
    event["payload"]["required_source_ids"] = list(duty.get("required_source_ids") or [])
    if patrol_plan is not None:
        event["payload"]["integrity_patrol"] = patrol_plan
    _write_state(state_path, state)

    result = {
        "event_id": event_id,
        "status": "CLAIMED_RUNTIME",
        "claim_id": claim_id,
        "attempt": attempt,
        "accountable_owner": duty["owner"],
        "lease_expires_at": claim["lease_expires_at"],
        "required_source_ids": required_source_ids,
    }
    if patrol_plan is not None:
        result["integrity_patrol"] = patrol_plan
    return result


def acknowledge_event(
    *,
    state_path: Path,
    timetable_path: Path,
    session_path: Path,
    event_id: str,
    claim_id: str,
    execution_result: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    session = load_session(session_path)
    state = load_scheduler_state(state_path)
    event, duty = _event_and_duty(
        state=state, timetable_path=timetable_path, event_id=event_id
    )

    if event.get("status") in TERMINAL_STATUSES:
        return {
            "event_id": event_id,
            "status": "DUPLICATE_TERMINAL",
            "terminal_status": event.get("status"),
            "accountable_owner": duty["owner"],
        }

    if event.get("status") != "CLAIMED_RUNTIME":
        raise WorkerError("event must be claimed before acknowledgement")
    claim = event.get("claim")
    if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
        raise WorkerError("claim_id does not match the active claim")

    if now.astimezone(timezone.utc) >= _dt(claim["lease_expires_at"]):
        raise WorkerError("claim lease expired before acknowledgement")

    outcome = execution_result.get("outcome")
    if outcome not in {"ACTION", "NO_ACTION", "STOP"}:
        raise WorkerError("outcome must be ACTION, NO_ACTION or STOP")
    if session.get("operating_state") == "HALTED" and outcome != "STOP":
        raise WorkerError("HALT permits only a STOP acknowledgement")

    if execution_result.get("external_effect") != "NONE":
        raise WorkerError("worker completion cannot claim or perform an external effect")
    if execution_result.get("new_model_provider_access_granted") not in {False, None}:
        raise WorkerError("worker may not grant model/provider access")

    resource_classes = execution_result.get("resource_classes_used")
    if not isinstance(resource_classes, list) or not resource_classes:
        raise WorkerError("resource_classes_used must be a non-empty list")
    invalid_resources = set(resource_classes) - ALLOWED_RESOURCE_CLASSES
    if invalid_resources:
        raise WorkerError(
            "resource use escaped the Aldernia/public-read-only boundary: "
            + ", ".join(sorted(invalid_resources))
        )

    retrieved = execution_result.get("retrieved_source_ids")
    if not isinstance(retrieved, list):
        raise WorkerError("retrieved_source_ids must be a list")
    retrieved_set = {str(value) for value in retrieved}
    required = set(COMMON_REQUIRED_SOURCE_IDS)
    required.update(str(value) for value in duty.get("required_source_ids") or [])
    patrol_plan = claim.get("integrity_patrol") if duty["id"] == "dynasty.heartbeat" else None
    if duty["id"] == "dynasty.heartbeat" and not isinstance(patrol_plan, Mapping):
        raise WorkerError("heartbeat claim is missing its required integrity patrol slice")
    if isinstance(patrol_plan, Mapping):
        required.update(str(value) for value in patrol_plan.get("required_source_ids") or [])
    missing = sorted(required - retrieved_set)
    if missing:
        raise WorkerError(
            "source retrieval acceptance failed; missing required source ids: "
            + ", ".join(missing)
        )

    result_summary = execution_result.get("result_summary")
    if not isinstance(result_summary, str) or not result_summary.strip():
        raise WorkerError("result_summary is required")
    if execution_result.get("readback_verified") is not True:
        raise WorkerError("durable readback must be explicitly verified")

    evidence_refs = execution_result.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise WorkerError("at least one evidence_ref is required")

    patrol_receipt = None
    if isinstance(patrol_plan, Mapping):
        try:
            patrol_receipt = validate_patrol_result(
                plan=patrol_plan,
                patrol_result=execution_result.get("integrity_patrol"),
                retrieved_source_ids=retrieved_set,
            )
        except PatrolError as exc:
            raise WorkerError(str(exc)) from exc

    balcony_pulse = None
    if duty["id"] == "dynasty.heartbeat":
        raw_balcony = execution_result.get("balcony_pulse")
        if not isinstance(raw_balcony, Mapping):
            raise WorkerError(
                "heartbeat completion requires a durable balcony_pulse result"
            )
        institutions_checked = raw_balcony.get("institutions_checked")
        material_changes = raw_balcony.get("material_changes")
        crown_attention = raw_balcony.get("crown_attention")
        if not isinstance(institutions_checked, list) or not institutions_checked:
            raise WorkerError(
                "heartbeat balcony_pulse must identify institutions checked"
            )
        if not isinstance(material_changes, list):
            raise WorkerError(
                "heartbeat balcony_pulse material_changes must be a list"
            )
        if not isinstance(crown_attention, list):
            raise WorkerError(
                "heartbeat balcony_pulse crown_attention must be a list"
            )
        if raw_balcony.get("readback_verified") is not True:
            raise WorkerError(
                "heartbeat balcony_pulse requires durable readback verification"
            )
        balcony_pulse = {
            "institutions_checked": [
                str(value) for value in institutions_checked
            ],
            "material_changes": [
                str(value) for value in material_changes
            ],
            "crown_attention": [
                str(value) for value in crown_attention
            ],
            "readback_verified": True,
        }

    terminal = {
        "ACTION": "ACKNOWLEDGED_ACTION",
        "NO_ACTION": "ACKNOWLEDGED_NO_ACTION",
        "STOP": "ACKNOWLEDGED_STOP",
    }[outcome]
    receipt = {
        "schema_version": WORKER_STATE_VERSION,
        "outcome": outcome,
        "result_summary": result_summary.strip(),
        "completed_at": _iso(now),
        "worker_id": claim["worker_id"],
        "runtime_class": claim["runtime_class"],
        "accountable_owner": duty["owner"],
        "claim_id": claim_id,
        "attempt": claim["attempt"],
        "retrieved_source_ids": sorted(retrieved_set),
        "evidence_refs": list(evidence_refs),
        "writes": list(execution_result.get("writes") or []),
        "readback_verified": True,
        "estate_scope": "ALDERNIA",
        "resource_classes_used": list(resource_classes),
        "external_effect": "NONE",
        "new_model_provider_access_granted": False,
        "authority_unchanged": True,
        "ownership_unchanged": True,
        "mission_created_by_worker": False,
    }
    if patrol_receipt is not None:
        receipt["integrity_patrol"] = patrol_receipt
    if balcony_pulse is not None:
        receipt["balcony_pulse"] = balcony_pulse

    if duty["id"] == "government.daily-pulse":
        domain_terminal_state = execution_result.get("domain_terminal_state")
        if outcome == "STOP" and domain_terminal_state != "FAILED_CLOSED":
            raise WorkerError("Government STOP must terminate FAILED_CLOSED")
        if outcome in {"ACTION", "NO_ACTION"} and domain_terminal_state != "VERIFIED_CLOSED":
            raise WorkerError("completed Government pulse must terminate VERIFIED_CLOSED")
        _create_government_red_box_child(
            state=state,
            timetable_path=timetable_path,
            parent_event=event,
            parent_event_id=event_id,
            parent_receipt=receipt,
            domain_terminal_state=str(domain_terminal_state),
            now=now,
        )
        event["domain_terminal_state"] = domain_terminal_state

    if duty["id"] == "government.red-box":
        red_box_content = execution_result.get("red_box_content")
        if not isinstance(red_box_content, str) or not red_box_content.strip():
            raise WorkerError("Government Red Box completion requires actual Red Box content")
        if execution_result.get("balcony_ack") is not True:
            raise WorkerError(
                "Government Red Box cannot close without durable King's Balcony ACK/readback"
            )
        parent_event_id = (event.get("payload") or {}).get("parent_event_id")
        if not isinstance(parent_event_id, str):
            raise WorkerError("Government Red Box is missing its parent event identity")
        parent_event = state.get("events", {}).get(parent_event_id)
        if not isinstance(parent_event, dict) or not isinstance(parent_event.get("receipt"), dict):
            raise WorkerError("Government Red Box parent receipt is unavailable")
        receipt["red_box_content"] = red_box_content.strip()
        receipt["balcony_ack"] = True
        parent_event["receipt"]["red_box_delivery"] = {
            "event_id": event_id,
            "status": terminal,
            "balcony_ack": True,
            "readback_verified": True,
            "completed_at": _iso(now),
            "red_box_content": red_box_content.strip(),
        }

    event["status"] = terminal
    event["receipt"] = receipt
    event.setdefault("claim_history", []).append(dict(claim))
    event["claim"] = None
    _write_state(state_path, state)

    return {
        "event_id": event_id,
        "status": terminal,
        "accountable_owner": duty["owner"],
        "readback_verified": True,
    }


def next_claimable(
    *,
    state_path: Path,
    timetable_path: Path,
    session_path: Path,
) -> dict[str, Any] | None:
    session = load_session(session_path)
    if session.get("operating_state") == "HALTED":
        return None
    state = load_scheduler_state(state_path)
    duties = _duty_map(timetable_path)
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for event_id, event in state.get("events", {}).items():
        if not isinstance(event, dict) or event.get("status") != "PENDING_RUNTIME":
            continue
        duty_id = (event.get("payload") or {}).get("duty_id")
        duty = duties.get(str(duty_id))
        if duty is None:
            continue
        candidates.append((str(event.get("scheduled_for") or ""), event_id, duty))
    if not candidates:
        return None
    candidates.sort()
    scheduled_for, event_id, duty = candidates[0]
    return {
        "event_id": event_id,
        "scheduled_for": scheduled_for,
        "accountable_owner": duty["owner"],
        "requires_model_runtime": True,
        "integrity_patrol_required": duty["id"] == "dynasty.heartbeat",
    }


def _read_result(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkerError("execution result JSON must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Governed claim/ACK worker for Dynasty scheduled-duty events."
    )
    parser.add_argument("--state", default="state/scheduled-duty-queue.json")
    parser.add_argument("--timetable", default="aldernia/schedule/timetable.json")
    parser.add_argument("--session", default="state/bus-session.json")
    sub = parser.add_subparsers(dest="command", required=True)

    nxt = sub.add_parser("next")
    claim = sub.add_parser("claim")
    claim.add_argument("--event-id", required=True)
    claim.add_argument("--worker-id", required=True)
    claim.add_argument("--runtime-class", default=MODEL_RUNTIME_CLASS)

    ack = sub.add_parser("ack")
    ack.add_argument("--event-id", required=True)
    ack.add_argument("--claim-id", required=True)
    ack.add_argument("--result-json", required=True)

    recover = sub.add_parser("recover")
    recover.add_argument("--event-id", required=True)

    args = parser.parse_args(argv)
    state_path = Path(args.state)
    timetable_path = Path(args.timetable)
    session_path = Path(args.session)
    now = datetime.now(timezone.utc)

    if args.command == "next":
        result = next_claimable(
            state_path=state_path,
            timetable_path=timetable_path,
            session_path=session_path,
        )
    elif args.command == "claim":
        result = claim_event(
            state_path=state_path,
            timetable_path=timetable_path,
            session_path=session_path,
            event_id=args.event_id,
            worker_id=args.worker_id,
            runtime_class=args.runtime_class,
            now=now,
        )
    elif args.command == "ack":
        result = acknowledge_event(
            state_path=state_path,
            timetable_path=timetable_path,
            session_path=session_path,
            event_id=args.event_id,
            claim_id=args.claim_id,
            execution_result=_read_result(args.result_json),
            now=now,
        )
    else:
        result = recover_expired_claim(
            state_path=state_path,
            timetable_path=timetable_path,
            session_path=session_path,
            event_id=args.event_id,
            now=now,
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
