from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid
from typing import Any

SESSION_VERSION = 1
STATES = {"DORMANT", "ACTIVE_TIMED", "ACTIVE_COMMAND", "HALTED"}


class BusSessionError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise BusSessionError("session time must be timezone-aware")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise BusSessionError("persisted session time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def dormant_state(reason: str = "DORMANT BY DEFAULT") -> dict[str, Any]:
    return {
        "schema_version": SESSION_VERSION,
        "operating_state": "DORMANT",
        "session_mode": None,
        "activation_id": None,
        "mission_id": None,
        "command_ref": None,
        "accountable_owner": None,
        "activated_at": None,
        "timed_expires_at": None,
        "last_30_minute_checkpoint": None,
        "acceptance": [],
        "halt_at": None,
        "complete_at": None,
        "last_transition_reason": reason,
    }


def _validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != SESSION_VERSION:
        raise BusSessionError("unsupported or malformed bus session state")
    state = data.get("operating_state")
    if state not in STATES:
        raise BusSessionError("invalid bus operating_state")
    if state == "ACTIVE_TIMED" and data.get("session_mode") != "TIMED":
        raise BusSessionError("ACTIVE_TIMED requires TIMED session_mode")
    if state == "ACTIVE_COMMAND":
        if data.get("session_mode") != "COMMAND":
            raise BusSessionError("ACTIVE_COMMAND requires COMMAND session_mode")
        if not data.get("mission_id") or not data.get("command_ref"):
            raise BusSessionError("ACTIVE_COMMAND requires mission_id and command_ref")
    return dict(data)


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}.") as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    os.replace(temp_name, path)


def load_session(path: Path) -> dict[str, Any]:
    if not path.exists():
        state = dormant_state()
        _write(path, state)
        return state
    return _validate(json.loads(path.read_text(encoding="utf-8")))


def activate_timed(path: Path, *, now: datetime, reason: str = "CROWN TIMED ACTIVATION") -> dict[str, Any]:
    state = dormant_state(reason)
    state.update({
        "operating_state": "ACTIVE_TIMED",
        "session_mode": "TIMED",
        "activation_id": f"bus-{uuid.uuid4()}",
        "activated_at": _iso(now),
        "timed_expires_at": _iso(now + timedelta(minutes=30)),
        "last_30_minute_checkpoint": _iso(now),
    })
    _write(path, state)
    return state


def activate_command(
    path: Path,
    *,
    now: datetime,
    mission_id: str,
    command_ref: str,
    accountable_owner: str,
    acceptance: list[str],
) -> dict[str, Any]:
    if not mission_id.strip() or not command_ref.strip() or not accountable_owner.strip() or not acceptance:
        raise BusSessionError("command session requires bounded mission, owner, command and acceptance")
    state = dormant_state("CROWN COMMAND ACTIVATION")
    state.update({
        "operating_state": "ACTIVE_COMMAND",
        "session_mode": "COMMAND",
        "activation_id": f"bus-{uuid.uuid4()}",
        "mission_id": mission_id.strip(),
        "command_ref": command_ref.strip(),
        "accountable_owner": accountable_owner.strip(),
        "activated_at": _iso(now),
        "last_30_minute_checkpoint": _iso(now),
        "acceptance": list(acceptance),
    })
    _write(path, state)
    return state


def halt(path: Path, *, now: datetime, reason: str = "HALT ALL BUSES — KING") -> dict[str, Any]:
    state = load_session(path)
    state["operating_state"] = "HALTED"
    state["halt_at"] = _iso(now)
    state["last_transition_reason"] = reason
    _write(path, state)
    return state


def complete(path: Path, *, now: datetime, reason: str = "VERIFIED COMPLETE") -> dict[str, Any]:
    state = load_session(path)
    previous_mission = state.get("mission_id")
    closed = dormant_state(reason)
    closed["mission_id"] = previous_mission
    closed["complete_at"] = _iso(now)
    _write(path, closed)
    return closed


def evaluate(path: Path, *, now: datetime) -> tuple[dict[str, Any], str]:
    state = load_session(path)
    current = now.astimezone(timezone.utc)
    operating = state["operating_state"]

    if operating == "HALTED":
        return state, "HALTED"
    if operating == "DORMANT":
        return state, "DORMANT"

    if operating == "ACTIVE_TIMED":
        expiry = _dt(state.get("timed_expires_at"))
        if expiry is None or current >= expiry:
            state = dormant_state("30-MINUTE TIMED LEASE EXPIRED")
            _write(path, state)
            return state, "TIMED_EXPIRED"
        return state, "ACTIVE_TIMED"

    checkpoint = _dt(state.get("last_30_minute_checkpoint")) or _dt(state.get("activated_at"))
    if checkpoint is None:
        raise BusSessionError("command session missing checkpoint time")
    if current >= checkpoint + timedelta(minutes=30):
        state["last_30_minute_checkpoint"] = _iso(current)
        state["last_transition_reason"] = "30-MINUTE COMMAND CHECKPOINT — MISSION CONTINUES"
        _write(path, state)
        return state, "ACTIVE_COMMAND_CHECKPOINT"
    return state, "ACTIVE_COMMAND"
