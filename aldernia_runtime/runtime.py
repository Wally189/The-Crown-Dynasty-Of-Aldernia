from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import tempfile
from typing import Any, Mapping

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.session import evaluate

BUS_STATE_VERSION = 1
EVENT_TYPE_HUMAN_MOMENT = "org.aldernia.public.human-moment.v1"
PUBLIC_STATE_SCHEMA_VERSION = 1


class BusValidationError(ValueError):
    pass


class HandlerRefused(RuntimeError):
    pass


def _aware_datetime(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise BusValidationError(f"{field} must be RFC3339/ISO-8601") from exc
    if dt.tzinfo is None:
        raise BusValidationError(f"{field} must include a timezone")
    return dt


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise BusValidationError(f"{field} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
    result: list[str] = []
    for item in value:
        result.append(_nonempty_string(item, field))
    return result


@dataclass(frozen=True)
class BusEnvelope:
    specversion: str
    event_id: str
    source: str
    event_type: str
    time: datetime
    subject: str
    clock_cycle: int
    originating_institution: str
    accountable_owner: str
    requested_outcome: str
    authority: list[str]
    constraints: list[str]
    state_refs: list[str]
    evidence_refs: list[str]
    permitted_actions: list[str]
    expected_output: str
    acceptance: list[str]
    due_at: datetime
    retry: Mapping[str, Any]
    next_destination: str
    engine_plan: Mapping[str, Any]
    data: Mapping[str, Any]

    @property
    def idempotency_key(self) -> str:
        return f"{self.source}#{self.event_id}"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BusEnvelope":
        if not isinstance(raw, Mapping):
            raise BusValidationError("event must be a JSON object")
        specversion = _nonempty_string(raw.get("specversion"), "specversion")
        if specversion != "1.0":
            raise BusValidationError("only CloudEvents specversion 1.0 is accepted")
        event_id = _nonempty_string(raw.get("id"), "id")
        source = _nonempty_string(raw.get("source"), "source")
        event_type = _nonempty_string(raw.get("type"), "type")
        time = _aware_datetime(_nonempty_string(raw.get("time"), "time"), "time")
        subject = _nonempty_string(raw.get("subject"), "subject")
        cycle = raw.get("clock_cycle")
        if not isinstance(cycle, int) or cycle < 0:
            raise BusValidationError("clock_cycle must be a non-negative integer")
        retry = raw.get("retry")
        if not isinstance(retry, Mapping):
            raise BusValidationError("retry must be an object")
        max_attempts = retry.get("max_attempts")
        if not isinstance(max_attempts, int) or max_attempts < 1 or max_attempts > 5:
            raise BusValidationError("retry.max_attempts must be an integer from 1 to 5")
        token = _nonempty_string(retry.get("idempotency_key"), "retry.idempotency_key")
        expected_token = f"{source}#{event_id}"
        if token != expected_token:
            raise BusValidationError("retry.idempotency_key must equal source#id")
        plan = raw.get("engine_plan")
        if not isinstance(plan, Mapping):
            raise BusValidationError("engine_plan must be an object")
        if plan.get("selector") != "COS-SEL-001":
            raise BusValidationError("engine_plan.selector must be COS-SEL-001")
        if plan.get("status") != "AUTHORISED":
            raise BusValidationError("engine_plan must be explicitly AUTHORISED")
        engines = _string_list(plan.get("machine_engines"), "engine_plan.machine_engines")
        if "HOC-WEB-001" not in engines or "RS-RCSS-001" not in engines:
            raise BusValidationError("public website events require HOC-WEB-001 and RS-RCSS-001")
        return cls(
            specversion=specversion,
            event_id=event_id,
            source=source,
            event_type=event_type,
            time=time,
            subject=subject,
            clock_cycle=cycle,
            originating_institution=_nonempty_string(raw.get("originating_institution"), "originating_institution"),
            accountable_owner=_nonempty_string(raw.get("accountable_owner"), "accountable_owner"),
            requested_outcome=_nonempty_string(raw.get("requested_outcome"), "requested_outcome"),
            authority=_string_list(raw.get("authority"), "authority"),
            constraints=_string_list(raw.get("constraints"), "constraints", allow_empty=True),
            state_refs=_string_list(raw.get("state_refs"), "state_refs"),
            evidence_refs=_string_list(raw.get("evidence_refs"), "evidence_refs"),
            permitted_actions=_string_list(raw.get("permitted_actions"), "permitted_actions"),
            expected_output=_nonempty_string(raw.get("expected_output"), "expected_output"),
            acceptance=_string_list(raw.get("acceptance"), "acceptance"),
            due_at=_aware_datetime(_nonempty_string(raw.get("due_at"), "due_at"), "due_at"),
            retry=dict(retry),
            next_destination=_nonempty_string(raw.get("next_destination"), "next_destination"),
            engine_plan=dict(plan),
            data=dict(raw.get("data") or {}),
        )


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}.") as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    os.replace(temp_name, path)


def load_bus_state(path: Path) -> dict[str, Any]:
    data = _read_json(path, {"schema_version": BUS_STATE_VERSION, "processed": {}})
    if not isinstance(data, dict) or data.get("schema_version") != BUS_STATE_VERSION:
        raise BusValidationError("unsupported or malformed bus state")
    processed = data.get("processed")
    if not isinstance(processed, dict):
        raise BusValidationError("bus state processed must be an object")
    return {"schema_version": BUS_STATE_VERSION, "processed": dict(processed)}


def _human_moment_state(event: BusEnvelope) -> dict[str, Any]:
    if "update Aldernia public-state human_moment" not in event.permitted_actions:
        raise HandlerRefused("event lacks the exact public-state write permission")
    mode = event.data.get("mode")
    zone = event.data.get("timezone")
    if mode != "weekday-daypart" or zone != "Europe/London":
        raise HandlerRefused("unsupported human moment configuration")
    return {
        "schema_version": PUBLIC_STATE_SCHEMA_VERSION,
        "release": {
            "event_id": event.event_id,
            "idempotency_key": event.idempotency_key,
            "clock_cycle": event.clock_cycle,
            "event_type": event.event_type,
        },
        "human_moment": {
            "enabled": True,
            "mode": "weekday-daypart",
            "timezone": "Europe/London",
            "suffix": "in Aldernia.",
        },
    }


def apply_event(event: BusEnvelope, current_public_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if event.event_type == EVENT_TYPE_HUMAN_MOMENT:
        return _human_moment_state(event)
    raise HandlerRefused(f"no deterministic handler is registered for {event.event_type}")


def receipt_for(event: BusEnvelope, public_state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "ACCEPTED",
        "event_id": event.event_id,
        "idempotency_key": event.idempotency_key,
        "event_type": event.event_type,
        "clock_cycle": event.clock_cycle,
        "originating_institution": event.originating_institution,
        "accountable_owner": event.accountable_owner,
        "selector": event.engine_plan.get("selector"),
        "coalition": event.engine_plan.get("machine_engines"),
        "acceptance": event.acceptance,
        "resulting_public_state": public_state,
        "next_destination": event.next_destination,
        "verification_state": "DETERMINISTIC_HANDLER_ACCEPTED; HOSTING/BROWSER VERIFICATION SEPARATE",
    }


def process_one_cycle(
    *,
    state_path: Path,
    session_path: Path,
    inbox_dir: Path,
    public_state_path: Path,
    releases_dir: Path,
    interval_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    clock = CentralAlderniaClock()
    if not isinstance(interval_seconds, int) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be a positive integer")
    cycle = clock.snapshot(now).unix_timestamp // interval_seconds
    session, session_status = evaluate(session_path, now=now)
    if session_status not in {"ACTIVE_TIMED", "ACTIVE_COMMAND", "ACTIVE_COMMAND_CHECKPOINT"}:
        return {"cycle": cycle, "status": f"BUS_{session_status}", "event_id": None}

    state = load_bus_state(state_path)
    files = sorted(inbox_dir.glob("*.json")) if inbox_dir.exists() else []

    for event_path in files:
        event = BusEnvelope.from_mapping(_read_json(event_path))
        if event.clock_cycle > cycle:
            continue
        if now.astimezone(timezone.utc) < event.due_at.astimezone(timezone.utc):
            continue
        key = event.idempotency_key
        if key in state["processed"]:
            continue

        receipt_path = releases_dir / f"{event.event_id}.json"
        existing_receipt = _read_json(receipt_path)
        if isinstance(existing_receipt, dict) and existing_receipt.get("status") == "ACCEPTED" and existing_receipt.get("idempotency_key") == key:
            state["processed"][key] = {
                "event_id": event.event_id,
                "clock_cycle": event.clock_cycle,
                "recovered_from_receipt": True,
            }
            _atomic_write_json(state_path, state)
            return {"cycle": cycle, "status": "RECOVERED_ACK", "event_id": event.event_id}

        current_public = _read_json(public_state_path, {})
        new_public = apply_event(event, current_public)
        _atomic_write_json(public_state_path, new_public)
        receipt = receipt_for(event, new_public)
        _atomic_write_json(receipt_path, receipt)
        state["processed"][key] = {
            "event_id": event.event_id,
            "clock_cycle": event.clock_cycle,
            "status": "ACCEPTED",
        }
        _atomic_write_json(state_path, state)
        return {"cycle": cycle, "status": "ACCEPTED", "event_id": event.event_id}

    return {"cycle": cycle, "status": "NO_OP", "event_id": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Aldernia build opportunity.")
    sub = parser.add_subparsers(dest="command", required=True)
    cycle = sub.add_parser("cycle")
    cycle.add_argument("--state", required=True)
    cycle.add_argument("--session", required=True)
    cycle.add_argument("--inbox", required=True)
    cycle.add_argument("--public-state", required=True)
    cycle.add_argument("--releases", required=True)
    cycle.add_argument("--interval", type=int, default=300)
    args = parser.parse_args(argv)

    result = process_one_cycle(
        state_path=Path(args.state),
        session_path=Path(args.session),
        inbox_dir=Path(args.inbox),
        public_state_path=Path(args.public_state),
        releases_dir=Path(args.releases),
        interval_seconds=args.interval,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
