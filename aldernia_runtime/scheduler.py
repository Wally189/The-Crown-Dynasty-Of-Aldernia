from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from aldernia_runtime.session import load_session
from aldernia_runtime.transport import (
    CANONICAL_STATE_CHANGED,
    HEARTBEAT,
    TransportPacket,
    validate_packet,
)

SCHEDULER_STATE_VERSION = 1
DEFAULT_ZONE = "Europe/London"
MAX_CATCHUP = timedelta(hours=24)
DESTINATION = "Dynasty Institutional Heartbeat impact discovery"


class SchedulerError(RuntimeError):
    pass


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    os.replace(temp_name, path)


def _parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SchedulerError("scheduler timestamps must be timezone-aware")
    return parsed


def load_timetable(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SchedulerError("unsupported timetable schema")
    zone_name = data.get("timezone")
    if not isinstance(zone_name, str) or not zone_name:
        raise SchedulerError("timetable timezone is required")
    ZoneInfo(zone_name)
    duties = data.get("duties")
    if not isinstance(duties, list) or not duties:
        raise SchedulerError("timetable must contain duties")
    seen: set[str] = set()
    for duty in duties:
        if not isinstance(duty, dict):
            raise SchedulerError("each duty must be an object")
        duty_id = duty.get("id")
        if not isinstance(duty_id, str) or not duty_id.strip() or duty_id in seen:
            raise SchedulerError("duty ids must be unique non-empty strings")
        seen.add(duty_id)
        schedule = duty.get("schedule")
        if not isinstance(schedule, dict):
            raise SchedulerError(f"{duty_id} schedule is required")
        kind = schedule.get("kind")
        if kind == "daily":
            _validate_clock_fields(schedule, duty_id, hourly=False)
        elif kind == "hourly_window":
            _validate_clock_fields(schedule, duty_id, hourly=True)
        else:
            raise SchedulerError(f"{duty_id} has unsupported schedule kind {kind!r}")
    return data


def _validate_clock_fields(schedule: Mapping[str, Any], duty_id: str, *, hourly: bool) -> None:
    minute = schedule.get("minute")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise SchedulerError(f"{duty_id} minute must be 0..59")
    if hourly:
        start_hour, end_hour = schedule.get("start_hour"), schedule.get("end_hour")
        if not isinstance(start_hour, int) or not isinstance(end_hour, int):
            raise SchedulerError(f"{duty_id} hourly window requires integer start/end hour")
        if not 0 <= start_hour <= end_hour <= 23:
            raise SchedulerError(f"{duty_id} hourly window is invalid")
    else:
        hour = schedule.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise SchedulerError(f"{duty_id} hour must be 0..23")


def load_scheduler_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEDULER_STATE_VERSION,
            "last_checked_at": None,
            "events": {},
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEDULER_STATE_VERSION:
        raise SchedulerError("unsupported or malformed scheduler state")
    events = data.get("events")
    if not isinstance(events, dict):
        raise SchedulerError("scheduler events must be an object")
    last_checked = data.get("last_checked_at")
    if last_checked is not None:
        _parse_aware(last_checked)
    return {
        "schema_version": SCHEDULER_STATE_VERSION,
        "last_checked_at": last_checked,
        "events": dict(events),
    }


def _occurrence_id(duty: Mapping[str, Any], local_due: datetime) -> str:
    granularity = duty.get("identity_granularity", "minute")
    if granularity == "date":
        suffix = local_due.strftime("%Y-%m-%d")
    elif granularity == "minute":
        suffix = local_due.strftime("%Y-%m-%dT%H:%M")
    else:
        raise SchedulerError(f"{duty['id']} has unsupported identity granularity")
    return f"{duty['id']}:{suffix}"


def _daily_occurrences(
    *,
    start_local: datetime,
    end_local: datetime,
    hour: int,
    minute: int,
    zone: ZoneInfo,
) -> Iterable[datetime]:
    current_date = start_local.date()
    end_date = end_local.date()
    while current_date <= end_date:
        due = datetime.combine(current_date, time(hour, minute), tzinfo=zone)
        if start_local < due <= end_local:
            yield due
        current_date += timedelta(days=1)


def _hourly_occurrences(
    *,
    start_local: datetime,
    end_local: datetime,
    start_hour: int,
    end_hour: int,
    minute: int,
    zone: ZoneInfo,
) -> Iterable[datetime]:
    current_date = start_local.date()
    end_date = end_local.date()
    while current_date <= end_date:
        for hour in range(start_hour, end_hour + 1):
            due = datetime.combine(current_date, time(hour, minute), tzinfo=zone)
            if start_local < due <= end_local:
                yield due
        current_date += timedelta(days=1)


def _duty_occurrences(
    duty: Mapping[str, Any],
    *,
    start_local: datetime,
    end_local: datetime,
    zone: ZoneInfo,
) -> Iterable[datetime]:
    schedule = duty["schedule"]
    if schedule["kind"] == "daily":
        yield from _daily_occurrences(
            start_local=start_local,
            end_local=end_local,
            hour=schedule["hour"],
            minute=schedule["minute"],
            zone=zone,
        )
        return
    yield from _hourly_occurrences(
        start_local=start_local,
        end_local=end_local,
        start_hour=schedule["start_hour"],
        end_hour=schedule["end_hour"],
        minute=schedule["minute"],
        zone=zone,
    )


def _packet_for(
    duty: Mapping[str, Any],
    *,
    event_id: str,
    local_due: datetime,
    source_register_id: str,
) -> TransportPacket:
    source_row = duty.get("source_row")
    authority_ref = (
        f"Royal Palace Scheduled Tasks Register {source_register_id}, "
        f"Scheduled Tasks row {source_row}"
    )
    payload = {
        "duty_id": duty["id"],
        "label": duty.get("label"),
        "scheduled_for": local_due.isoformat(),
        "source_row": source_row,
        "accountable_owner": duty.get("owner"),
        "required_source_ids": list(duty.get("required_source_ids") or []),
        "requires_model_runtime": bool(duty.get("requires_model_runtime", False)),
        "evaluation_only": bool(duty.get("evaluation_only", False)),
    }
    if duty["id"] == "dynasty.heartbeat":
        payload["integrity_patrol"] = {
            "source_row": 21,
            "registry_version": 1,
            "mode": "ONE_BOUNDED_SLICE_PER_HEARTBEAT",
        }

    return TransportPacket(
        event_id=event_id,
        event_type=CANONICAL_STATE_CHANGED,
        emitting_owner="Central Aldernia Clock / Scheduled Tasks",
        authority_ref=authority_ref,
        mission_ref="NONE — DUE EVALUATION ONLY",
        departure_condition=(
            f"Machine timetable occurrence reached at {local_due.isoformat()}; "
            "time creates no mission."
        ),
        destination=DESTINATION,
        acceptance=(
            "Retrieve the current canonical task row and required current controls; "
            "return ACTION, NO_ACTION or STOP. No implicit mission or authority widening."
        ),
        ack_to="Central Aldernia Clock / scheduled-duty queue",
        transition_class="scheduled_duty_due",
        permitted_effects=(
            "evaluate already-authorised recurring duty",
            "route through COS-BUS-001 when current due/authority gates pass",
            "record ACK/NO_ACTION/STOP",
        ),
        prohibited_effects=(
            "create a mission from time alone",
            "widen authority",
            "treat this machine projection as task source of truth",
        ),
        payload=payload,
        service_class=HEARTBEAT,
        departure_mode="TIMETABLE",
    )


def run_scheduler(
    *,
    timetable_path: Path,
    state_path: Path,
    session_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    timetable = load_timetable(timetable_path)
    state = load_scheduler_state(state_path)
    session = load_session(session_path)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise SchedulerError("now must be timezone-aware")
    current_utc = current.astimezone(timezone.utc)
    zone = ZoneInfo(timetable.get("timezone", DEFAULT_ZONE))
    current_local = current_utc.astimezone(zone)

    if state["last_checked_at"]:
        start_utc = _parse_aware(state["last_checked_at"]).astimezone(timezone.utc)
        if current_utc < start_utc:
            raise SchedulerError("scheduler time moved backwards")
        if current_utc - start_utc > MAX_CATCHUP:
            start_utc = current_utc - MAX_CATCHUP
    else:
        start_local = datetime.combine(current_local.date(), time.min, tzinfo=zone)
        start_utc = start_local.astimezone(timezone.utc)

    start_local = start_utc.astimezone(zone)
    halted = session.get("operating_state") == "HALTED"
    source_register_id = timetable["source_of_truth"]["drive_file_id"]

    due: list[tuple[datetime, Mapping[str, Any]]] = []
    for duty in timetable["duties"]:
        for occurrence in _duty_occurrences(
            duty, start_local=start_local, end_local=current_local, zone=zone
        ):
            due.append((occurrence, duty))
    due.sort(key=lambda item: (item[0], item[1]["id"]))

    created: list[str] = []
    suppressed: list[str] = []
    for local_due, duty in due:
        event_id = _occurrence_id(duty, local_due)
        if event_id in state["events"]:
            continue

        packet = _packet_for(
            duty,
            event_id=event_id,
            local_due=local_due,
            source_register_id=source_register_id,
        )
        validate_packet(packet)
        status = "SUPPRESSED_HALTED" if halted else "PENDING_RUNTIME"
        record = {
            "event_id": packet.event_id,
            "event_type": packet.event_type,
            "transition_class": packet.transition_class,
            "service_class": packet.resolved_service_class,
            "departure_mode": packet.departure_mode,
            "emitting_owner": packet.emitting_owner,
            "authority_ref": packet.authority_ref,
            "mission_ref": packet.mission_ref,
            "destination": packet.destination,
            "acceptance": packet.acceptance,
            "ack_to": packet.ack_to,
            "permitted_effects": list(packet.permitted_effects),
            "prohibited_effects": list(packet.prohibited_effects),
            "payload": dict(packet.payload),
            "scheduled_for": local_due.isoformat(),
            "emitted_at": current_utc.isoformat(timespec="seconds"),
            "status": status,
        }
        state["events"][event_id] = record
        (suppressed if halted else created).append(event_id)

    state["last_checked_at"] = current_utc.isoformat(timespec="seconds")
    _atomic_write_json(state_path, state)

    pending_count = sum(
        1 for event in state["events"].values() if event.get("status") == "PENDING_RUNTIME"
    )
    return {
        "status": "HALTED" if halted else "OPERATING",
        "checked_at": state["last_checked_at"],
        "new_pending_events": created,
        "new_suppressed_events": suppressed,
        "pending_count": pending_count,
        "implicit_mission_created": False,
        "source_of_truth": source_register_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the Dynasty-native timetable and persist due COS-BUS events."
    )
    parser.add_argument("--timetable", default="aldernia/schedule/timetable.json")
    parser.add_argument("--state", default="state/scheduled-duty-queue.json")
    parser.add_argument("--session", default="state/bus-session.json")
    args = parser.parse_args(argv)

    result = run_scheduler(
        timetable_path=Path(args.timetable),
        state_path=Path(args.state),
        session_path=Path(args.session),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
