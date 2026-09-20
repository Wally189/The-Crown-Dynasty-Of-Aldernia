from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Callable, Mapping

# Small canonical vocabulary. These names are transport signals, not new authority.
CLOCK_CHIME = "clock.chime"
GOVERNMENT_PULSE_DUE = "government.pulse.due"
GOVERNMENT_PULSE_CLOSED = "government.pulse.closed"
GOVERNMENT_PULSE_FAILED_CLOSED = "government.pulse.failed_closed"
DEPENDENCY_REQUESTED = "dependency.requested"
DEPENDENCY_COMPLETED = "dependency.completed"
CANONICAL_STATE_CHANGED = "canonical.state.changed"
CROWN_DECISION_REQUIRED = "crown.decision.required"
MISSION_VERIFIED_COMPLETE = "mission.verified_complete"

# Vehicle class and departure mode are separate. Priority never creates authority.
BUS = "BUS"
BROADCAST_BUS = "BROADCAST_BUS"
TRAM = "TRAM"
EXPRESS_TRAM = "EXPRESS_TRAM"
HEARTBEAT = "HEARTBEAT"

SERVICE_CLASSES = {BUS, BROADCAST_BUS, TRAM, EXPRESS_TRAM, HEARTBEAT}
DEPARTURE_MODES = {"TIMETABLE", "EVENT", "COMPLETION", "CATCH_UP"}
SERVICE_PRIORITY = {
    HEARTBEAT: 5,
    BUS: 10,
    BROADCAST_BUS: 10,
    TRAM: 20,
    EXPRESS_TRAM: 100,
}

RESPONSE_CLASSES = {
    "ACCEPT",
    "INFORMATION",
    "REFER",
    "REFUSE_OUTSIDE_REMIT",
    "STOP",
    "ESCALATE",
    "NO_ACTION",
    "COMPLETE",
}

HEARTBEAT_TRANSITIONS = {
    "verified_completion",
    "failed_closed",
    "blocker_created",
    "blocker_cleared",
    "capability_state_changed",
    "new_dependency",
    "accepted_release",
    "crown_reserved_decision",
    "scheduled_duty_due",
}


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteSpec:
    event_type: str
    emitting_owner: str
    departure_condition: str
    destination: str
    required_packet_fields: tuple[str, ...]
    permitted_effects: tuple[str, ...]
    prohibited_effects: tuple[str, ...]
    acceptance: str
    ack_destination: str
    max_attempts: int
    escalation: str
    terminal_states: tuple[str, ...]
    dynamic_destination: bool = False


ROUTES: dict[str, RouteSpec] = {
    CLOCK_CHIME: RouteSpec(
        CLOCK_CHIME,
        "Central Aldernia Clock",
        "An authorised scheduler activation stamps one clock event; the clock does not create a mission.",
        "Schedule / due evaluator",
        ("event_id", "authority_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("evaluate already-authorised due conditions", "stamp ordering/time"),
        ("create a mission", "widen authority", "perform unrelated work"),
        "Due/not-due evaluation is acknowledged without creating an implicit mission.",
        "Central Aldernia Clock / transport ledger",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to transport owner",
        ("COMPLETE", "NO_ACTION", "STOP"),
    ),
    GOVERNMENT_PULSE_DUE: RouteSpec(
        GOVERNMENT_PULSE_DUE,
        "Government schedule evaluator",
        "Current Aldernian date is later than the last verified completed pulse date, no pulse for that date is active, and the bus is not HALTED.",
        "House of Marianne — Centre of Government & Cabinet Coordination",
        ("event_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("open one ACTIVE_TIMED Government Pulse", "execute already-authorised internal Government business"),
        ("revive archived authority", "invent Government business", "bypass Crown-reserved gates"),
        "Pulse reaches VERIFIED_CLOSED or FAILED_CLOSED and records its parent pulse identity.",
        "Daily Aldernian Government Pulse ledger",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to Centre of Government control",
        ("COMPLETE", "STOP", "NO_ACTION"),
    ),
    GOVERNMENT_PULSE_CLOSED: RouteSpec(
        GOVERNMENT_PULSE_CLOSED,
        "House of Marianne — Centre of Government & Cabinet Coordination",
        "The identified parent Government Pulse has reached VERIFIED_CLOSED.",
        "Government Red Box Tram → Royal Palace / King's Balcony",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("build decision/accountability Red Box from parent evidence", "deliver user-visible Balcony return", "write delivery ACK"),
        ("run before parent closure", "make a second Government decision", "invent decisions/scrutiny"),
        "Red Box is delivered and durable ACK/readback is recorded for the same parent pulse ID.",
        "Daily Government Decision Red Box ledger / parent pulse",
        2,
        "RED BOX EXCEPTION / DELIVERY_UNCONFIRMED to Palace transport control",
        ("COMPLETE", "STOP"),
    ),
    GOVERNMENT_PULSE_FAILED_CLOSED: RouteSpec(
        GOVERNMENT_PULSE_FAILED_CLOSED,
        "House of Marianne — Centre of Government & Cabinet Coordination",
        "The identified parent Government Pulse has reached FAILED_CLOSED.",
        "Government Red Box Tram → Royal Palace / King's Balcony",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("deliver a Red Box Exception", "record exact stopping point", "write delivery ACK"),
        ("invent decisions", "resume failed work without authority", "hide the STOP"),
        "Red Box Exception is delivered and durable ACK/readback is recorded for the same parent pulse ID.",
        "Daily Government Decision Red Box ledger / parent pulse",
        2,
        "DELIVERY_UNCONFIRMED to Palace transport control",
        ("COMPLETE", "STOP"),
    ),
    DEPENDENCY_REQUESTED: RouteSpec(
        DEPENDENCY_REQUESTED,
        "Originating accountable institution",
        "A genuine bounded dependency is recorded and the competent originating route cannot complete it internally.",
        "Competent destination from current EnginePlan / capability route",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "requested_contribution", "acceptance", "ack_to"),
        ("perform bounded requested specialist contribution", "return ACCEPT/INFORMATION/REFER/REFUSE/STOP/ESCALATE"),
        ("transfer originating decision ownership", "edit another institution's specialist truth without authority", "widen the mission"),
        "Receiving institution acknowledges classification and returns contribution or evidenced terminal state to the originating owner.",
        "Originating accountable institution",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to originating owner; escalate only if authority requires",
        ("COMPLETE", "NO_ACTION", "STOP", "REFER", "REFUSE_OUTSIDE_REMIT", "ESCALATE"),
        dynamic_destination=True,
    ),
    DEPENDENCY_COMPLETED: RouteSpec(
        DEPENDENCY_COMPLETED,
        "Receiving specialist institution",
        "A bounded dependency request has reached an attributable returned contribution or terminal response.",
        "Originating accountable institution",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("return specialist contribution/evidence", "restore control to originating owner"),
        ("silently mutate originating decision", "retain transferred ownership"),
        "Originating owner acknowledges receipt and integrates, rejects, refers or closes under its own authority.",
        "Receiving specialist institution / dependency ledger",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to dependency owner",
        ("COMPLETE", "NO_ACTION", "STOP"),
        dynamic_destination=True,
    ),
    CANONICAL_STATE_CHANGED: RouteSpec(
        CANONICAL_STATE_CHANGED,
        "Canonical state owner",
        "A controlled state transition in the recognised heartbeat transition classes has been verified and recorded.",
        "Dynasty Institutional Heartbeat impact discovery",
        ("event_id", "parent_case_id", "authority_ref", "emitting_owner", "destination", "transition_class", "acceptance", "ack_to"),
        ("discover affected subscribers", "return ACTION/NO_ACTION/STOP", "route second-order consequence only from a new material state"),
        ("invent impact", "recursively schedule without new state", "overwrite specialist truth"),
        "Heartbeat records ACTION, NO_ACTION or STOP for the event and acknowledges the originating state owner.",
        "Canonical state owner / heartbeat ledger",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to common transport control",
        ("COMPLETE", "NO_ACTION", "STOP"),
    ),
    CROWN_DECISION_REQUIRED: RouteSpec(
        CROWN_DECISION_REQUIRED,
        "Competent accountable institution",
        "An existing genuinely Crown-reserved decision or constitutional/binding STOP is identified and cannot be lawfully resolved below the Crown.",
        "Royal Palace / King's Balcony",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "requested_contribution", "acceptance", "ack_to"),
        ("surface exact reserved decision/STOP", "provide evidence and options without deciding for the Crown"),
        ("send routine operations to the King", "manufacture a Crown gate", "bypass specialist ownership"),
        "The Palace/King acknowledges receipt or routes the matter to the proper constitutional decision surface.",
        "Originating accountable institution",
        2,
        "DELIVERY_UNCONFIRMED recorded at Palace/common control",
        ("COMPLETE", "STOP"),
    ),
    MISSION_VERIFIED_COMPLETE: RouteSpec(
        MISSION_VERIFIED_COMPLETE,
        "Accountable mission owner",
        "Mission acceptance/readback/record duties are verified complete and Crown receipt is required by the mission/commission.",
        "Royal Palace / King's Balcony when Crown receipt required; otherwise owning closure route",
        ("event_id", "parent_case_id", "authority_ref", "mission_ref", "emitting_owner", "destination", "acceptance", "ack_to"),
        ("return closure evidence", "close the mission/session when authorised"),
        ("promote unverified work to complete", "disturb the Crown when Crown receipt is not required"),
        "Required closure recipient acknowledges verified completion and closure/readback is recorded.",
        "Accountable mission owner",
        2,
        "FAILED_CLOSED / DELIVERY_UNCONFIRMED to mission owner",
        ("COMPLETE", "STOP"),
        dynamic_destination=True,
    ),
}


@dataclass(frozen=True)
class TransportPacket:
    event_id: str
    event_type: str
    emitting_owner: str
    authority_ref: str
    mission_ref: str
    departure_condition: str
    destination: str
    acceptance: str
    ack_to: str
    parent_case_id: str | None = None
    requested_contribution: str | None = None
    transition_class: str | None = None
    permitted_effects: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    service_class: str | None = None
    departure_mode: str = "EVENT"

    @property
    def resolved_service_class(self) -> str:
        return self.service_class or default_service_class(self.event_type)

    @property
    def priority(self) -> int:
        return SERVICE_PRIORITY[self.resolved_service_class]

    @property
    def dedup_key(self) -> str:
        stable_parent = self.parent_case_id or self.event_id
        return f"{self.event_type}|{stable_parent}|{self.destination}"


def default_service_class(event_type: str) -> str:
    if event_type in {GOVERNMENT_PULSE_CLOSED, GOVERNMENT_PULSE_FAILED_CLOSED}:
        return TRAM
    if event_type == CROWN_DECISION_REQUIRED:
        return EXPRESS_TRAM
    if event_type == CANONICAL_STATE_CHANGED:
        return HEARTBEAT
    return BUS


def dynasty_sleeping(local_clock_time: time) -> bool:
    """Ordinary Aldernian traffic sleeps 23:30–05:00 Europe/London."""
    return local_clock_time >= time(23, 30) or local_clock_time < time(5, 0)


@dataclass(frozen=True)
class DeliveryResponse:
    status: str
    ack: bool
    detail: str = ""


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    attempts: int
    event_id: str
    dedup_key: str
    response_status: str | None
    detail: str


class TransportRouter:
    """Small deterministic transport controller.

    It routes already-authorised events; it never creates authority or specialist truth.
    """

    def __init__(self) -> None:
        self._halted = False
        self._receipts: dict[str, DeliveryResult] = {}

    def halt(self) -> None:
        self._halted = True

    def resume(self) -> None:
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    def dispatch(
        self,
        packet: TransportPacket,
        handler: Callable[[TransportPacket, int], DeliveryResponse | None],
        *,
        local_time: time | None = None,
    ) -> DeliveryResult:
        spec = validate_packet(packet)
        key = packet.dedup_key
        if self._halted:
            return DeliveryResult("HALTED", 0, packet.event_id, key, None, "HALT dominates dispatch and retry")
        if local_time is not None and dynasty_sleeping(local_time):
            sleep_override = bool(packet.payload.get("sleep_override_authorised"))
            if packet.resolved_service_class != EXPRESS_TRAM or not sleep_override:
                return DeliveryResult(
                    "SLEEP_DEFERRED",
                    0,
                    packet.event_id,
                    key,
                    None,
                    "ordinary Dynasty traffic sleeps 23:30–05:00; only an expressly authorised Express Tram may break sleep",
                )
        if key in self._receipts:
            prior = self._receipts[key]
            return DeliveryResult("DUPLICATE_SUPPRESSED", 0, packet.event_id, key, prior.response_status, "stable event/parent identity already has a terminal receipt")

        last_detail = "ACK missing"
        last_status: str | None = None
        for attempt in range(1, spec.max_attempts + 1):
            if self._halted:
                return DeliveryResult("HALTED", attempt - 1, packet.event_id, key, None, "HALT dominates dispatch and retry")
            try:
                response = handler(packet, attempt)
            except Exception as exc:
                response = None
                last_detail = f"handler error: {exc}"
            if response is not None:
                if response.status not in RESPONSE_CLASSES:
                    raise TransportError(f"unsupported response class {response.status}")
                last_status = response.status
                last_detail = response.detail
                if response.ack:
                    result_status = "RECOVERED_ACK" if attempt > 1 else "ACKNOWLEDGED"
                    result = DeliveryResult(result_status, attempt, packet.event_id, key, response.status, response.detail)
                    self._receipts[key] = result
                    return result
            if attempt >= spec.max_attempts:
                break

        result = DeliveryResult(
            "FAILED_CLOSED_DELIVERY_UNCONFIRMED",
            spec.max_attempts,
            packet.event_id,
            key,
            last_status,
            last_detail,
        )
        self._receipts[key] = result
        return result


def validate_packet(packet: TransportPacket) -> RouteSpec:
    spec = ROUTES.get(packet.event_type)
    if spec is None:
        raise TransportError(f"unknown event type: {packet.event_type}")
    for field_name in ("event_id", "emitting_owner", "authority_ref", "mission_ref", "departure_condition", "destination", "acceptance", "ack_to"):
        value = getattr(packet, field_name)
        if not isinstance(value, str) or not value.strip():
            raise TransportError(f"{field_name} is required")
    if not spec.dynamic_destination and packet.destination != spec.destination:
        raise TransportError(f"{packet.event_type} must route to {spec.destination}")
    if packet.event_type == CANONICAL_STATE_CHANGED and packet.transition_class not in HEARTBEAT_TRANSITIONS:
        raise TransportError("canonical.state.changed requires a recognised heartbeat transition_class")
    if packet.resolved_service_class not in SERVICE_CLASSES:
        raise TransportError(f"unsupported service_class {packet.resolved_service_class}")
    if packet.departure_mode not in DEPARTURE_MODES:
        raise TransportError(f"unsupported departure_mode {packet.departure_mode}")
    if (
        packet.resolved_service_class == EXPRESS_TRAM
        and packet.event_type != CROWN_DECISION_REQUIRED
        and not bool(packet.payload.get("binding_stop_or_emergency"))
    ):
        raise TransportError("Express Tram is reserved to Crown-reserved or evidenced binding STOP/emergency traffic")
    return spec


def government_pulse_due(
    *,
    current_aldernian_date: date,
    last_verified_completed_date: date | None,
    pulse_active_for_current_date: bool,
    bus_operating_state: str,
) -> bool:
    """Mechanical DUE test. Time may wake evaluation; it does not invent business."""
    if bus_operating_state == "HALTED":
        return False
    if pulse_active_for_current_date:
        return False
    if last_verified_completed_date is not None and last_verified_completed_date >= current_aldernian_date:
        return False
    return True


def pulse_completion_event(parent_status: str) -> str:
    if parent_status == "VERIFIED_CLOSED":
        return GOVERNMENT_PULSE_CLOSED
    if parent_status == "FAILED_CLOSED":
        return GOVERNMENT_PULSE_FAILED_CLOSED
    raise TransportError("Red Box child event may be emitted only from VERIFIED_CLOSED or FAILED_CLOSED")


def heartbeat_should_fire(event_type: str, transition_class: str | None = None) -> bool:
    if event_type in {MISSION_VERIFIED_COMPLETE, GOVERNMENT_PULSE_FAILED_CLOSED, DEPENDENCY_REQUESTED, CROWN_DECISION_REQUIRED}:
        return True
    if event_type == CANONICAL_STATE_CHANGED:
        return transition_class in HEARTBEAT_TRANSITIONS
    return False


def balcony_event_allowed(packet: TransportPacket) -> bool:
    if packet.event_type in {GOVERNMENT_PULSE_CLOSED, GOVERNMENT_PULSE_FAILED_CLOSED, CROWN_DECISION_REQUIRED}:
        return True
    if packet.event_type == MISSION_VERIFIED_COMPLETE:
        return bool(packet.payload.get("crown_receipt_required"))
    return False
