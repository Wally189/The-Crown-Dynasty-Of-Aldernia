from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import json
import os
import tempfile

from central_aldernia_clock import CentralAlderniaClock

WORLD_SCHEMA_VERSION = 1
EXPERIMENT_ID = "one-ferry-two-places"
EXPERIMENT_SCOPE = "EXPERIMENT_NON_CANONICAL"
EFFECT_DYNAMIC_WORLD = "DYNAMIC_WORLD"

PLACE_ST_BRIGIDS = "place:aldernia:st-brigids-isle"
PLACE_KESTRELS = "place:aldernia:kestrels"
TEST_FERRY_ID = "test:ferry:one-ferry-two-places"
AUTHORITY_REF = "Crown Commission — Aldernia World State: One Ferry, Two Places — 20 September 2026"


class WorldStateError(RuntimeError):
    pass


class TransitionRejected(WorldStateError):
    pass


class EffectGateRejected(WorldStateError):
    pass


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise WorldStateError(f"{field} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WorldStateError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _append_json_line(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception as exc:
            raise WorldStateError(f"journal line {line_number} is malformed") from exc
        if not isinstance(event, dict):
            raise WorldStateError(f"journal line {line_number} must be an object")
        result.append(event)
    return result


def ordered_scheduled_events(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def key(event: Mapping[str, Any]) -> tuple[datetime, int, str]:
        effective = _parse_time(str(event.get("effective_time")), "effective_time")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence < 1:
            raise WorldStateError("scheduled event sequence must be a positive integer")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise WorldStateError("scheduled event_id must be a non-empty string")
        return effective, sequence, event_id

    return [dict(item) for item in sorted(events, key=key)]


def initial_world_state() -> dict[str, Any]:
    return {
        "schema_version": WORLD_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "scope": EXPERIMENT_SCOPE,
        "authority_ref": AUTHORITY_REF,
        "places": {
            PLACE_ST_BRIGIDS: {
                "id": PLACE_ST_BRIGIDS,
                "canonical_identity": "St Brigid's Isle",
                "experiment_mutates_canon": False,
            },
            PLACE_KESTRELS: {
                "id": PLACE_KESTRELS,
                "canonical_identity": "The Kestrels",
                "experiment_mutates_canon": False,
            },
        },
        "routes": [
            {
                "route_id": "test-route:st-brigids-kestrels",
                "a": PLACE_ST_BRIGIDS,
                "b": PLACE_KESTRELS,
                "bidirectional": True,
                "status": "TEST_NON_CANONICAL_PARAMETER",
            }
        ],
        "entities": {
            TEST_FERRY_ID: {
                "id": TEST_FERRY_ID,
                "entity_type": "FERRY",
                "status": "TEST_NON_CANONICAL",
                "location": PLACE_ST_BRIGIDS,
                "movement_state": "DOCKED",
                "journey": None,
            }
        },
        "scheduled_events": [],
        "processed_commands": {},
        "next_sequence": 1,
    }


def assert_effect_allowed(effect_class: str, scope: str) -> None:
    if effect_class != EFFECT_DYNAMIC_WORLD or scope != EXPERIMENT_SCOPE:
        raise EffectGateRejected(
            "world experiment may emit only DYNAMIC_WORLD events inside EXPERIMENT_NON_CANONICAL scope"
        )


@dataclass(frozen=True)
class WorldPaths:
    root: Path
    state: Path
    journal: Path

    @classmethod
    def under(cls, root: Path) -> "WorldPaths":
        return cls(root=root, state=root / "world-state.json", journal=root / "world-journal.jsonl")


class WorldRuntime:
    def __init__(self, root: Path, clock: CentralAlderniaClock) -> None:
        self.paths = WorldPaths.under(Path(root))
        self.clock = clock
        if self.paths.state.exists():
            self.state = self._load_state()
        else:
            self.state = initial_world_state()
            _atomic_write_json(self.paths.state, self.state)
            self.paths.journal.parent.mkdir(parents=True, exist_ok=True)
            self.paths.journal.touch(exist_ok=True)

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.paths.state.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorldStateError(f"could not read world state: {exc}") from exc
        self._validate_state(data)
        return data

    @staticmethod
    def _validate_state(state: Any) -> None:
        if not isinstance(state, dict):
            raise WorldStateError("world state must be a JSON object")
        if state.get("schema_version") != WORLD_SCHEMA_VERSION:
            raise WorldStateError("unsupported world state schema")
        if state.get("experiment_id") != EXPERIMENT_ID:
            raise WorldStateError("unexpected experiment identity")
        if state.get("scope") != EXPERIMENT_SCOPE:
            raise WorldStateError("world state escaped experimental scope")
        if not isinstance(state.get("places"), dict):
            raise WorldStateError("places must be an object")
        if not isinstance(state.get("entities"), dict):
            raise WorldStateError("entities must be an object")
        if not isinstance(state.get("routes"), list):
            raise WorldStateError("routes must be a list")
        if not isinstance(state.get("scheduled_events"), list):
            raise WorldStateError("scheduled_events must be a list")
        if not isinstance(state.get("processed_commands"), dict):
            raise WorldStateError("processed_commands must be an object")
        sequence = state.get("next_sequence")
        if not isinstance(sequence, int) or sequence < 1:
            raise WorldStateError("next_sequence must be a positive integer")

    def current_state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.state))

    def _persist(self) -> None:
        self._validate_state(self.state)
        _atomic_write_json(self.paths.state, self.state)

    def _allocate_sequence(self) -> int:
        sequence = int(self.state["next_sequence"])
        self.state["next_sequence"] = sequence + 1
        return sequence

    def _route_exists(self, origin: str, destination: str) -> bool:
        for route in self.state["routes"]:
            if route.get("a") == origin and route.get("b") == destination:
                return True
            if route.get("bidirectional") and route.get("a") == destination and route.get("b") == origin:
                return True
        return False


    def register_place(
        self,
        *,
        place_id: str,
        canonical_identity: str,
        effective_time: datetime,
        command_id: str,
        provenance: str,
    ) -> dict[str, Any]:
        if not isinstance(place_id, str) or not place_id.strip():
            raise ValueError("place_id must be non-empty")
        if not isinstance(canonical_identity, str) or not canonical_identity.strip():
            raise ValueError("canonical_identity must be non-empty")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("command_id must be non-empty")
        command_id = command_id.strip()

        prior_event_id = self.state["processed_commands"].get(command_id)
        if prior_event_id:
            existing = self._find_event(str(prior_event_id))
            if existing is None:
                raise WorldStateError("processed command points to a missing journal event")
            return existing

        if place_id in self.state["places"]:
            raise TransitionRejected("place identity already exists")

        effective = _aware(effective_time, "effective_time")
        sequence = self._allocate_sequence()
        place = {
            "id": place_id,
            "canonical_identity": canonical_identity.strip(),
            "experiment_mutates_canon": False,
            "status": "TEST_NON_CANONICAL_DESCRIPTOR",
        }
        self.state["places"][place_id] = place
        event_id = f"world:{EXPERIMENT_ID}:{command_id}:place-registered"
        self.state["processed_commands"][command_id] = event_id
        event = {
            "event_id": event_id,
            "event_type": "PLACE_REGISTERED",
            "place_id": place_id,
            "place": json.loads(json.dumps(place)),
            "sequence": sequence,
            "effect_class": EFFECT_DYNAMIC_WORLD,
            "scope": EXPERIMENT_SCOPE,
            "authority_ref": AUTHORITY_REF,
            "provenance": provenance,
            "command_id": command_id,
            **self._clock_fields(effective_time=effective, recorded_time=effective),
        }
        self._journal_event(event)
        self._persist()
        return event

    def register_route(
        self,
        *,
        route_id: str,
        a: str,
        b: str,
        bidirectional: bool,
        effective_time: datetime,
        command_id: str,
        provenance: str,
    ) -> dict[str, Any]:
        if not isinstance(route_id, str) or not route_id.strip():
            raise ValueError("route_id must be non-empty")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("command_id must be non-empty")
        command_id = command_id.strip()

        prior_event_id = self.state["processed_commands"].get(command_id)
        if prior_event_id:
            existing = self._find_event(str(prior_event_id))
            if existing is None:
                raise WorldStateError("processed command points to a missing journal event")
            return existing

        if a == b:
            raise TransitionRejected("route endpoints must be distinct")
        if a not in self.state["places"] or b not in self.state["places"]:
            raise TransitionRejected("route references unknown place")
        if any(route.get("route_id") == route_id for route in self.state["routes"]):
            raise TransitionRejected("route identity already exists")

        effective = _aware(effective_time, "effective_time")
        sequence = self._allocate_sequence()
        route = {
            "route_id": route_id,
            "a": a,
            "b": b,
            "bidirectional": bool(bidirectional),
            "status": "TEST_NON_CANONICAL_DESCRIPTOR",
        }
        self.state["routes"].append(route)
        event_id = f"world:{EXPERIMENT_ID}:{command_id}:route-registered"
        self.state["processed_commands"][command_id] = event_id
        event = {
            "event_id": event_id,
            "event_type": "ROUTE_REGISTERED",
            "route_id": route_id,
            "route": json.loads(json.dumps(route)),
            "sequence": sequence,
            "effect_class": EFFECT_DYNAMIC_WORLD,
            "scope": EXPERIMENT_SCOPE,
            "authority_ref": AUTHORITY_REF,
            "provenance": provenance,
            "command_id": command_id,
            **self._clock_fields(effective_time=effective, recorded_time=effective),
        }
        self._journal_event(event)
        self._persist()
        return event

    def can_depart(self, origin: str, destination: str, entity_id: str = TEST_FERRY_ID) -> bool:
        entity = self.state["entities"].get(entity_id)
        return bool(
            entity
            and entity.get("movement_state") == "DOCKED"
            and entity.get("location") == origin
            and self._route_exists(origin, destination)
        )

    def _clock_fields(self, *, effective_time: datetime, recorded_time: datetime) -> dict[str, Any]:
        effective = _aware(effective_time, "effective_time")
        recorded = _aware(recorded_time, "recorded_time")
        tick, _previous = self.clock.advance_tick()
        effective_snapshot = self.clock.snapshot(effective)
        recorded_snapshot = self.clock.snapshot(recorded)
        return {
            "clock_id": effective_snapshot.clock_id,
            "dynasty_tick": tick,
            "effective_time": effective_snapshot.utc_timestamp,
            "recorded_time": recorded_snapshot.utc_timestamp,
        }

    def _journal_event(self, event: Mapping[str, Any]) -> None:
        assert_effect_allowed(str(event.get("effect_class")), str(event.get("scope")))
        _append_json_line(self.paths.journal, event)

    def _find_event(self, event_id: str) -> dict[str, Any] | None:
        for event in read_journal(self.paths.journal):
            if event.get("event_id") == event_id:
                return event
        return None

    def depart(
        self,
        *,
        origin: str,
        destination: str,
        effective_time: datetime,
        arrival_time: datetime,
        command_id: str,
        entity_id: str = TEST_FERRY_ID,
        provenance: str = "Crown-authorised non-production world-state experiment",
    ) -> dict[str, Any]:
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("command_id must be a non-empty string")
        command_id = command_id.strip()

        prior_event_id = self.state["processed_commands"].get(command_id)
        if prior_event_id:
            existing = self._find_event(str(prior_event_id))
            if existing is None:
                raise WorldStateError("processed command points to a missing journal event")
            return existing

        effective = _aware(effective_time, "effective_time")
        arrival = _aware(arrival_time, "arrival_time")
        if arrival <= effective:
            raise TransitionRejected("arrival must occur after departure")

        entity = self.state["entities"].get(entity_id)
        if not entity:
            raise TransitionRejected("unknown entity")
        if entity.get("movement_state") != "DOCKED":
            raise TransitionRejected("ferry must be DOCKED before departure")
        if entity.get("location") != origin:
            raise TransitionRejected("ferry is not at the requested origin")
        if not self._route_exists(origin, destination):
            raise TransitionRejected("no valid route exists between origin and destination")

        departure_sequence = self._allocate_sequence()
        arrival_sequence = self._allocate_sequence()
        departure_event_id = f"world:{EXPERIMENT_ID}:{command_id}:depart"
        arrival_event_id = f"world:{EXPERIMENT_ID}:{command_id}:arrive"

        scheduled_arrival = {
            "event_id": arrival_event_id,
            "event_type": "ARRIVE",
            "entity_id": entity_id,
            "origin": origin,
            "destination": destination,
            "effective_time": arrival.isoformat(timespec="seconds"),
            "sequence": arrival_sequence,
            "effect_class": EFFECT_DYNAMIC_WORLD,
            "scope": EXPERIMENT_SCOPE,
            "authority_ref": AUTHORITY_REF,
            "provenance": provenance,
            "source_command_id": command_id,
        }

        entity["location"] = None
        entity["movement_state"] = "IN_TRANSIT"
        entity["journey"] = {
            "origin": origin,
            "destination": destination,
            "departed_at": effective.isoformat(timespec="seconds"),
            "arrival_due_at": arrival.isoformat(timespec="seconds"),
            "departure_event_id": departure_event_id,
            "arrival_event_id": arrival_event_id,
        }
        self.state["scheduled_events"].append(scheduled_arrival)
        self.state["scheduled_events"] = ordered_scheduled_events(self.state["scheduled_events"])
        self.state["processed_commands"][command_id] = departure_event_id

        event = {
            "event_id": departure_event_id,
            "event_type": "DEPARTED",
            "entity_id": entity_id,
            "origin": origin,
            "destination": destination,
            "sequence": departure_sequence,
            "effect_class": EFFECT_DYNAMIC_WORLD,
            "scope": EXPERIMENT_SCOPE,
            "authority_ref": AUTHORITY_REF,
            "provenance": provenance,
            "command_id": command_id,
            "scheduled_event": scheduled_arrival,
            "resulting_entity_state": json.loads(json.dumps(entity)),
            **self._clock_fields(effective_time=effective, recorded_time=effective),
        }

        self._journal_event(event)
        self._persist()
        return event

    def process_due(self, *, now: datetime) -> list[dict[str, Any]]:
        current = _aware(now, "now")
        accepted: list[dict[str, Any]] = []
        for scheduled in ordered_scheduled_events(self.state["scheduled_events"]):
            due = _parse_time(str(scheduled["effective_time"]), "effective_time")
            if not CentralAlderniaClock.is_due(current, due):
                break
            if scheduled.get("event_type") != "ARRIVE":
                raise TransitionRejected("unsupported scheduled transition")
            accepted.append(self._arrive(scheduled, recorded_time=current))
        return accepted

    def _arrive(self, scheduled: Mapping[str, Any], *, recorded_time: datetime) -> dict[str, Any]:
        entity_id = str(scheduled["entity_id"])
        entity = self.state["entities"].get(entity_id)
        if not entity:
            raise TransitionRejected("arrival references unknown entity")
        journey = entity.get("journey")
        if entity.get("movement_state") != "IN_TRANSIT" or not isinstance(journey, dict):
            raise TransitionRejected("arrival requires matching IN_TRANSIT journey")
        if journey.get("arrival_event_id") != scheduled.get("event_id"):
            raise TransitionRejected("arrival event does not match active journey")
        if journey.get("destination") != scheduled.get("destination"):
            raise TransitionRejected("arrival destination does not match active journey")

        destination = str(scheduled["destination"])
        entity["location"] = destination
        entity["movement_state"] = "DOCKED"
        entity["journey"] = None

        scheduled_id = str(scheduled["event_id"])
        self.state["scheduled_events"] = [
            item for item in self.state["scheduled_events"] if item.get("event_id") != scheduled_id
        ]

        event = {
            "event_id": scheduled_id,
            "event_type": "ARRIVED",
            "entity_id": entity_id,
            "origin": scheduled.get("origin"),
            "destination": destination,
            "sequence": int(scheduled["sequence"]),
            "effect_class": EFFECT_DYNAMIC_WORLD,
            "scope": EXPERIMENT_SCOPE,
            "authority_ref": AUTHORITY_REF,
            "provenance": scheduled.get("provenance"),
            "source_command_id": scheduled.get("source_command_id"),
            "resulting_entity_state": json.loads(json.dumps(entity)),
            **self._clock_fields(
                effective_time=_parse_time(str(scheduled["effective_time"]), "effective_time"),
                recorded_time=recorded_time,
            ),
        }

        self._journal_event(event)
        self._persist()
        return event

    @staticmethod
    def replay(journal_path: Path) -> dict[str, Any]:
        state = initial_world_state()
        for event in sorted(
            read_journal(Path(journal_path)),
            key=lambda item: (
                _parse_time(str(item["effective_time"]), "effective_time"),
                int(item["sequence"]),
                str(item["event_id"]),
            ),
        ):
            assert_effect_allowed(str(event.get("effect_class")), str(event.get("scope")))
            entity_id = str(event.get("entity_id"))
            entity = state["entities"].get(entity_id)
            if entity is None:
                raise WorldStateError("journal references unknown entity")
            sequence = int(event["sequence"])
            state["next_sequence"] = max(int(state["next_sequence"]), sequence + 1)

            event_type = event.get("event_type")
            if event_type == "PLACE_REGISTERED":
                place = dict(event["place"])
                place_id = str(place["id"])
                if place_id in state["places"] and state["places"][place_id] != place:
                    raise WorldStateError("replay found conflicting place identity")
                state["places"][place_id] = place
                command_id = event.get("command_id")
                if command_id:
                    state["processed_commands"][str(command_id)] = event["event_id"]
            elif event_type == "ROUTE_REGISTERED":
                route = dict(event["route"])
                if route.get("a") not in state["places"] or route.get("b") not in state["places"]:
                    raise WorldStateError("replay route references unknown place")
                route_id = str(route["route_id"])
                existing_routes = [item for item in state["routes"] if item.get("route_id") == route_id]
                if existing_routes and existing_routes[0] != route:
                    raise WorldStateError("replay found conflicting route identity")
                if not existing_routes:
                    state["routes"].append(route)
                command_id = event.get("command_id")
                if command_id:
                    state["processed_commands"][str(command_id)] = event["event_id"]
            elif event_type == "DEPARTED":
                scheduled = dict(event["scheduled_event"])
                entity["location"] = None
                entity["movement_state"] = "IN_TRANSIT"
                entity["journey"] = {
                    "origin": event.get("origin"),
                    "destination": event.get("destination"),
                    "departed_at": event.get("effective_time"),
                    "arrival_due_at": scheduled.get("effective_time"),
                    "departure_event_id": event.get("event_id"),
                    "arrival_event_id": scheduled.get("event_id"),
                }
                state["scheduled_events"].append(scheduled)
                state["scheduled_events"] = ordered_scheduled_events(state["scheduled_events"])
                command_id = event.get("command_id")
                if command_id:
                    state["processed_commands"][str(command_id)] = event["event_id"]
                state["next_sequence"] = max(int(state["next_sequence"]), int(scheduled["sequence"]) + 1)
            elif event_type == "ARRIVED":
                entity["location"] = event.get("destination")
                entity["movement_state"] = "DOCKED"
                entity["journey"] = None
                event_id = event.get("event_id")
                state["scheduled_events"] = [
                    item for item in state["scheduled_events"] if item.get("event_id") != event_id
                ]
            else:
                raise WorldStateError(f"unsupported journal event type {event_type}")
        return state
