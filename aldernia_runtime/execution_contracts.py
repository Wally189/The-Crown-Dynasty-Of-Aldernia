from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

CONTRACT_VERSION = 1
CONTRACT_COLUMN_INDEX = 11
ENGINE_MANIFEST_ID = "1NumS4_cKvgJLQfUIG45Nug7clbv2zuxU2EZ9bvSJdXQ"

PROFILE_MODEL_INTERNAL = "MODEL_INTERNAL_V1"
PROFILE_MODEL_PUBLIC_READ = "MODEL_PUBLIC_READ_V1"
PROFILE_GOVERNMENT_PULSE = "GOVERNMENT_PULSE_V1"
PROFILE_BALCONY_RETURN = "BALCONY_RETURN_V1"
PROFILE_HEARTBEAT_PATROL = "HEARTBEAT_PATROL_V1"

ADAPTER_MODEL = "MODEL"
ADAPTER_PATROL = "PATROL"

CATCHUP_POINT_IN_TIME = "POINT_IN_TIME_EXPIRES"
CATCHUP_SAME_DAY = "SAME_DAY_ALLOWED"
CATCHUP_LATEST = "LATEST_SUPERSEDES_PRIOR"
CATCHUP_NONE = "NO_AUTOMATIC_CATCHUP"
CATCHUP_24H = "WITHIN_24H"

POINT_IN_TIME_WINDOW = timedelta(minutes=60)
NO_AUTOMATIC_WINDOW = timedelta(minutes=15)
MAX_LATEST_AGE = timedelta(hours=24)

ALLOWED_SUCCESSOR_EVENT_TYPES = {
    "government.pulse.closed",
    "government.pulse.failed_closed",
}
ALLOWED_TERMINAL_STATES = {"VERIFIED_CLOSED", "FAILED_CLOSED"}

PROFILE_SPECS: dict[str, dict[str, Any]] = {
    PROFILE_MODEL_INTERNAL: {
        "adapter": ADAPTER_MODEL,
        "requires_model": True,
        "public_web_read": False,
        "resource_classes": ("ALDERNIA_INTERNAL",),
        "instruction": (
            "Execute the current authorised scheduled-task instruction within the declared "
            "engine route. Do not widen authority, create external effects, or invent missing evidence."
        ),
    },
    PROFILE_MODEL_PUBLIC_READ: {
        "adapter": ADAPTER_MODEL,
        "requires_model": True,
        "public_web_read": True,
        "resource_classes": ("ALDERNIA_INTERNAL", "PUBLIC_READ_ONLY"),
        "instruction": (
            "Execute the current authorised scheduled-task instruction within the declared "
            "engine route. Public web access is read-only evidence gathering and may not create "
            "contact, publication, authentication, spend, or another external effect."
        ),
    },
    PROFILE_GOVERNMENT_PULSE: {
        "adapter": ADAPTER_MODEL,
        "requires_model": True,
        "public_web_read": False,
        "resource_classes": ("ALDERNIA_INTERNAL",),
        "instruction": (
            "Execute exactly one bounded same-date Government Pulse from current post-reset "
            "authority. Do not revive archived programmes, appointments or synthetic mandate. "
            "A completed pulse must terminate VERIFIED_CLOSED; a STOP must terminate FAILED_CLOSED."
        ),
    },
    PROFILE_BALCONY_RETURN: {
        "adapter": ADAPTER_MODEL,
        "requires_model": True,
        "public_web_read": False,
        "resource_classes": ("ALDERNIA_INTERNAL",),
        "instruction": (
            "Execute only from the genuine terminal parent receipt. Build the concise "
            "accountability return from parent evidence, do not conduct a second Government "
            "decision round, and require durable King's Balcony ACK/readback."
        ),
    },
    PROFILE_HEARTBEAT_PATROL: {
        "adapter": ADAPTER_PATROL,
        "requires_model": True,
        "public_web_read": False,
        "resource_classes": ("ALDERNIA_INTERNAL",),
        "instruction": (
            "Execute the existing bounded Dynasty Heartbeat and one integrity-patrol slice. "
            "Royal Household access remains prohibited and routine institutional state stays institution-owned."
        ),
    },
}

ALLOWED_ENGINE_MODES = {"DECLARED", "SELECT", "SPECIALIST"}
ALLOWED_CATCHUP = {
    CATCHUP_POINT_IN_TIME,
    CATCHUP_SAME_DAY,
    CATCHUP_LATEST,
    CATCHUP_NONE,
    CATCHUP_24H,
}


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionContract:
    v: int
    duty_id: str
    profile: str
    engine_mode: str
    catchup: str
    successor: Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "v": self.v,
            "duty_id": self.duty_id,
            "profile": self.profile,
            "engine_mode": self.engine_mode,
            "catchup": self.catchup,
        }
        if self.successor is not None:
            value["successor"] = dict(self.successor)
        return value


@dataclass(frozen=True)
class CatchupDecision:
    disposition: str
    detail: str


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value.strip()


def parse_contract_mapping(
    value: Mapping[str, Any],
    *,
    expected_duty_id: str | None = None,
) -> ExecutionContract:
    if not isinstance(value, Mapping):
        raise ContractError("execution contract must be an object")
    allowed = {"v", "duty_id", "profile", "engine_mode", "catchup", "successor"}
    unknown = set(value) - allowed
    if unknown:
        raise ContractError(
            "execution contract contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    if value.get("v") != CONTRACT_VERSION:
        raise ContractError(f"execution contract v must equal {CONTRACT_VERSION}")
    duty_id = _nonempty(value.get("duty_id"), "duty_id")
    if expected_duty_id is not None and duty_id != expected_duty_id:
        raise ContractError(
            f"execution contract duty_id {duty_id!r} does not match current event {expected_duty_id!r}"
        )
    profile = _nonempty(value.get("profile"), "profile")
    if profile not in PROFILE_SPECS:
        raise ContractError(f"unknown execution profile {profile!r}")
    engine_mode = _nonempty(value.get("engine_mode"), "engine_mode")
    if engine_mode not in ALLOWED_ENGINE_MODES:
        raise ContractError(f"unsupported engine_mode {engine_mode!r}")
    catchup = _nonempty(value.get("catchup"), "catchup")
    if catchup not in ALLOWED_CATCHUP:
        raise ContractError(f"unsupported catchup policy {catchup!r}")

    spec = PROFILE_SPECS[profile]
    if spec["adapter"] == ADAPTER_PATROL and engine_mode != "SPECIALIST":
        raise ContractError("specialist patrol profile requires engine_mode SPECIALIST")
    if spec["adapter"] == ADAPTER_MODEL and engine_mode == "SPECIALIST":
        raise ContractError("model execution profiles cannot use SPECIALIST engine mode")

    successor = value.get("successor")
    parsed_successor: Mapping[str, Any] | None = None
    if successor is not None:
        if not isinstance(successor, Mapping):
            raise ContractError("successor must be an object")
        allowed_successor = {"duty_id", "parent_case_prefix", "terminal_map"}
        unknown_successor = set(successor) - allowed_successor
        if unknown_successor:
            raise ContractError(
                "successor contains unsupported fields: "
                + ", ".join(sorted(unknown_successor))
            )
        successor_duty = _nonempty(successor.get("duty_id"), "successor.duty_id")
        prefix = _nonempty(
            successor.get("parent_case_prefix"), "successor.parent_case_prefix"
        )
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", prefix):
            raise ContractError("successor.parent_case_prefix has an unsafe format")
        terminal_map = successor.get("terminal_map")
        if not isinstance(terminal_map, Mapping) or not terminal_map:
            raise ContractError("successor.terminal_map must be a non-empty object")
        clean_map: dict[str, str] = {}
        for terminal, event_type in terminal_map.items():
            if terminal not in ALLOWED_TERMINAL_STATES:
                raise ContractError(f"unsupported successor terminal state {terminal!r}")
            if event_type not in ALLOWED_SUCCESSOR_EVENT_TYPES:
                raise ContractError(f"unsupported successor event type {event_type!r}")
            clean_map[str(terminal)] = str(event_type)
        parsed_successor = {
            "duty_id": successor_duty,
            "parent_case_prefix": prefix,
            "terminal_map": clean_map,
        }

    return ExecutionContract(
        v=CONTRACT_VERSION,
        duty_id=duty_id,
        profile=profile,
        engine_mode=engine_mode,
        catchup=catchup,
        successor=parsed_successor,
    )


def parse_contract_cell(
    raw: Any,
    *,
    expected_duty_id: str | None = None,
) -> ExecutionContract:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError("execution contract is missing from Scheduled Tasks")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("execution contract is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ContractError("execution contract JSON must be an object")

    if "duties" not in value:
        return parse_contract_mapping(value, expected_duty_id=expected_duty_id)

    if set(value) != {"v", "duties"}:
        raise ContractError(
            "execution contract bundle may contain only v and duties"
        )
    if value.get("v") != CONTRACT_VERSION:
        raise ContractError(
            f"execution contract bundle v must equal {CONTRACT_VERSION}"
        )
    duties = value.get("duties")
    if not isinstance(duties, Mapping) or not duties:
        raise ContractError("execution contract bundle duties must be a non-empty object")
    if expected_duty_id is None:
        raise ContractError(
            "execution contract bundle requires an expected duty_id"
        )
    selected = duties.get(expected_duty_id)
    if not isinstance(selected, Mapping):
        raise ContractError(
            f"execution contract bundle has no entry for duty {expected_duty_id!r}"
        )
    allowed_entry = {"profile", "engine_mode", "catchup", "successor"}
    unknown = set(selected) - allowed_entry
    if unknown:
        raise ContractError(
            "execution contract bundle entry contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    return parse_contract_mapping(
        {
            "v": CONTRACT_VERSION,
            "duty_id": expected_duty_id,
            **dict(selected),
        },
        expected_duty_id=expected_duty_id,
    )


def contract_from_row(
    row: list[str],
    *,
    expected_duty_id: str | None = None,
) -> ExecutionContract:
    if len(row) <= CONTRACT_COLUMN_INDEX:
        raise ContractError("Scheduled Tasks row has no Execution Contract column")
    return parse_contract_cell(
        row[CONTRACT_COLUMN_INDEX], expected_duty_id=expected_duty_id
    )


def profile_spec(contract: ExecutionContract) -> Mapping[str, Any]:
    return PROFILE_SPECS[contract.profile]


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContractError("scheduled_for must be timezone-aware")
    return parsed


def catchup_decision(
    contract: ExecutionContract,
    *,
    event_id: str,
    event: Mapping[str, Any],
    state: Mapping[str, Any],
    now: datetime,
) -> CatchupDecision:
    scheduled_raw = event.get("scheduled_for")
    if not isinstance(scheduled_raw, str):
        raise ContractError("event scheduled_for is missing")
    scheduled = _aware(scheduled_raw).astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    if current < scheduled:
        return CatchupDecision("WAIT", "event is not due yet")
    elapsed = current - scheduled
    zone = ZoneInfo("Europe/London")

    if contract.catchup == CATCHUP_SAME_DAY:
        if current.astimezone(zone).date() == scheduled.astimezone(zone).date():
            return CatchupDecision("EXECUTE", "same-day catch-up is authorised")
        return CatchupDecision("EXPIRE", "same-day catch-up window has closed")

    if contract.catchup == CATCHUP_POINT_IN_TIME:
        if elapsed <= POINT_IN_TIME_WINDOW:
            return CatchupDecision(
                "EXECUTE",
                f"point-in-time duty remains within {int(POINT_IN_TIME_WINDOW.total_seconds() // 60)} minutes",
            )
        return CatchupDecision("EXPIRE", "point-in-time duty window has expired")

    if contract.catchup == CATCHUP_NONE:
        if elapsed <= NO_AUTOMATIC_WINDOW:
            return CatchupDecision("EXECUTE", "normal scheduler-delay tolerance")
        return CatchupDecision("EXPIRE", "automatic catch-up is not authorised")

    if contract.catchup == CATCHUP_24H:
        if elapsed <= timedelta(hours=24):
            return CatchupDecision("EXECUTE", "within authorised 24-hour catch-up window")
        return CatchupDecision("EXPIRE", "24-hour catch-up window has closed")

    if contract.catchup == CATCHUP_LATEST:
        duty_id = (event.get("payload") or {}).get("duty_id")
        later_exists = False
        for other_id, other in (state.get("events") or {}).items():
            if str(other_id) == event_id or not isinstance(other, Mapping):
                continue
            if (other.get("payload") or {}).get("duty_id") != duty_id:
                continue
            other_scheduled = other.get("scheduled_for")
            if not isinstance(other_scheduled, str):
                continue
            if _aware(other_scheduled).astimezone(timezone.utc) > scheduled:
                later_exists = True
                break
        if later_exists:
            return CatchupDecision(
                "EXPIRE_SUPERSEDED", "a later occurrence of the same duty already exists"
            )
        if elapsed > MAX_LATEST_AGE:
            return CatchupDecision("EXPIRE", "latest occurrence is older than 24 hours")
        return CatchupDecision("EXECUTE", "this is the latest unsuperseded occurrence")

    raise ContractError(f"unhandled catchup policy {contract.catchup!r}")


def _explicit_house_names(value: str) -> set[str]:
    return {
        match.group(0).casefold()
        for match in re.finditer(r"House of [A-Za-z]+", value, flags=re.IGNORECASE)
    }


def build_declared_engine_plan(
    contract: ExecutionContract,
    *,
    duty: Mapping[str, Any],
    row: list[str],
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    if contract.engine_mode != "DECLARED":
        raise ContractError("declared engine plan requested for non-DECLARED contract")
    assigned = row[3].strip() if len(row) > 3 and isinstance(row[3], str) else ""
    if not assigned:
        raise ContractError("DECLARED engine routing requires Assigned Engine in current task row")

    owner = _nonempty(duty.get("owner"), "duty.owner")
    owner_houses = _explicit_house_names(owner)
    engine_houses = _explicit_house_names(assigned)
    if owner_houses and engine_houses and owner_houses.isdisjoint(engine_houses):
        raise ContractError(
            "Assigned Engine explicitly names a different House from the accountable owner"
        )

    manifest = next(
        (item for item in sources if item.get("id") == ENGINE_MANIFEST_ID), None
    )
    if manifest is None or not str(manifest.get("text") or "").strip():
        raise ContractError("current Engine Manifest was not retrieved")

    machine_ids = sorted(
        set(re.findall(r"\b[A-Z]{2,}(?:-[A-Z0-9]+){1,4}\b", assigned))
    )
    manifest_text = str(manifest.get("text") or "")
    missing = [machine_id for machine_id in machine_ids if machine_id not in manifest_text]
    if missing:
        raise ContractError(
            "Assigned Engine references machine IDs absent from current manifest: "
            + ", ".join(missing)
        )

    return {
        "selector": "COS-SEL-001",
        "status": "AUTHORISED",
        "mode": "DECLARED",
        "human_route": assigned,
        "machine_engines": machine_ids,
        "accountable_owner": owner,
        "authority_basis": "CURRENT_SCHEDULED_TASK_ASSIGNED_ENGINE",
    }


def validate_selected_engine_plan(
    value: Mapping[str, Any],
    *,
    sources: list[dict[str, str]],
    accountable_owner: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("COS-SEL-001 plan must be an object")
    allowed = {"selector", "status", "machine_engines", "human_route"}
    unknown = set(value) - allowed
    if unknown:
        raise ContractError(
            "COS-SEL-001 plan contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    if value.get("selector") != "COS-SEL-001" or value.get("status") != "AUTHORISED":
        raise ContractError("COS-SEL-001 plan must be explicitly AUTHORISED")
    engines = value.get("machine_engines")
    if not isinstance(engines, list) or not engines:
        raise ContractError("COS-SEL-001 plan must select at least one machine engine")
    clean_engines = [_nonempty(item, "machine_engines[]") for item in engines]
    human_route = _nonempty(value.get("human_route"), "human_route")
    manifest = next(
        (item for item in sources if item.get("id") == ENGINE_MANIFEST_ID), None
    )
    if manifest is None:
        raise ContractError("current Engine Manifest was not retrieved")
    manifest_text = str(manifest.get("text") or "")
    missing = [engine for engine in clean_engines if engine not in manifest_text]
    if missing:
        raise ContractError(
            "COS-SEL-001 selected engines absent from current manifest: "
            + ", ".join(missing)
        )
    return {
        "selector": "COS-SEL-001",
        "status": "AUTHORISED",
        "mode": "SELECT",
        "human_route": human_route,
        "machine_engines": clean_engines,
        "accountable_owner": accountable_owner,
        "authority_basis": "CURRENT_COS_SEL_001_PLAN",
    }


def validate_execution_result(
    contract: ExecutionContract,
    result: Mapping[str, Any],
) -> None:
    outcome = result.get("outcome")
    if outcome not in {"ACTION", "NO_ACTION", "STOP"}:
        raise ContractError("execution result outcome must be ACTION, NO_ACTION or STOP")

    domain = result.get("domain_terminal_state")
    red_box = result.get("red_box_content")

    if contract.profile == PROFILE_GOVERNMENT_PULSE:
        if outcome == "STOP" and domain != "FAILED_CLOSED":
            raise ContractError("Government STOP must return FAILED_CLOSED")
        if outcome in {"ACTION", "NO_ACTION"} and domain != "VERIFIED_CLOSED":
            raise ContractError("completed Government pulse must return VERIFIED_CLOSED")
        if red_box is not None:
            raise ContractError("Government Pulse may not return Red Box content")
        return

    if contract.profile == PROFILE_BALCONY_RETURN:
        if domain is not None:
            raise ContractError("Balcony return may not set domain_terminal_state")
        if not isinstance(red_box, str) or not red_box.strip():
            raise ContractError("Balcony return requires non-empty Red Box content")
        return

    if domain is not None:
        raise ContractError("this execution profile may not set domain_terminal_state")
    if red_box is not None:
        raise ContractError("this execution profile may not return Red Box content")
