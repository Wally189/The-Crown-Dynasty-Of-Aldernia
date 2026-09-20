from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import os
import re
import tempfile

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.world_state import WorldRuntime

SCHEMA_VERSION = 1
STORE_VERSION = 1
AUTHORITY_REF = "Crown Commission — Aldernia World Descriptor & Instantiation Layer — 20 September 2026"

SOURCE_FACT = "SOURCE_FACT"
DERIVED_FACT = "DERIVED_FACT"
GENERATED_DETAIL = "GENERATED_DETAIL"
EXPERIMENTAL_PARAMETER = "EXPERIMENTAL_PARAMETER"
UNKNOWN_REQUIRED_DECISION = "UNKNOWN_REQUIRED_DECISION"
FACT_CLASSES = {SOURCE_FACT, DERIVED_FACT, GENERATED_DETAIL, EXPERIMENTAL_PARAMETER, UNKNOWN_REQUIRED_DECISION}

EXPERIMENT_NON_CANONICAL = "EXPERIMENT_NON_CANONICAL"
DYNAMIC_WORLD = "DYNAMIC_WORLD"
CANON_CANDIDATE = "CANON_CANDIDATE"
INSTITUTIONAL_OR_POLICY_PROPOSAL = "INSTITUTIONAL_OR_POLICY_PROPOSAL"
CANONICAL_OR_RESERVED = "CANONICAL_OR_RESERVED"
AUTHORITY_CLASSES = {
    EXPERIMENT_NON_CANONICAL, DYNAMIC_WORLD, CANON_CANDIDATE,
    INSTITUTIONAL_OR_POLICY_PROPOSAL, CANONICAL_OR_RESERVED,
}
AUTO_CLASSES = {EXPERIMENT_NON_CANONICAL, DYNAMIC_WORLD}

PLACE_LAND = "PLACE_LAND"
BUILDING_FACILITY = "BUILDING_FACILITY"
NETWORK_EDGE = "NETWORK_EDGE"
NETWORK_NODE_CONTROL = "NETWORK_NODE_CONTROL"
INSTITUTION_ORGANISATION = "INSTITUTION_ORGANISATION"
VEHICLE_MOVING_ENTITY = "VEHICLE_MOVING_ENTITY"
FAMILIES = {
    PLACE_LAND, BUILDING_FACILITY, NETWORK_EDGE, NETWORK_NODE_CONTROL,
    INSTITUTION_ORGANISATION, VEHICLE_MOVING_ENTITY,
}

RELATIONS = {
    "LOCATED_IN", "CONTAINS", "CONNECTED_TO", "CONNECTS", "CONTROLS",
    "PART_OF", "SERVED_BY", "DEPENDS_ON", "OCCUPIES",
}

ARCHETYPES: dict[str, dict[str, Any]] = {
    "PLACE": {"family": PLACE_LAND},
    "FIELD": {"family": PLACE_LAND, "requires": {"PART_OF": 1}},
    "FARM": {"family": PLACE_LAND},
    "HOUSING_ESTATE": {"family": PLACE_LAND},
    "BUILDING": {"family": BUILDING_FACILITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
    "FACTORY": {"family": BUILDING_FACILITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
    "WORKPLACE": {"family": BUILDING_FACILITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
    "PORT": {"family": BUILDING_FACILITY, "requires": {"CONNECTED_TO": 1}},
    "RAIL_STATION": {"family": BUILDING_FACILITY, "requires": {"CONNECTED_TO": 1}},
    "PARLIAMENT_BUILDING": {"family": BUILDING_FACILITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
    "ROAD": {"family": NETWORK_EDGE, "requires": {"CONNECTS": 2}},
    "MOTORWAY": {"family": NETWORK_EDGE, "requires": {"CONNECTS": 2}},
    "RAILWAY": {"family": NETWORK_EDGE, "requires": {"CONNECTS": 2}},
    "JUNCTION": {"family": NETWORK_NODE_CONTROL, "requires": {"CONNECTED_TO": 1}},
    "TRAFFIC_LIGHT": {"family": NETWORK_NODE_CONTROL, "requires": {"CONTROLS": 1}},
    "SCHOOL": {"family": INSTITUTION_ORGANISATION, "requires_any": {"LOCATED_IN", "OCCUPIES"}},
    "UNIVERSITY": {"family": INSTITUTION_ORGANISATION, "requires_any": {"LOCATED_IN", "OCCUPIES"}},
    "INSTITUTION": {"family": INSTITUTION_ORGANISATION},
    "VEHICLE": {"family": VEHICLE_MOVING_ENTITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
    "FERRY": {"family": VEHICLE_MOVING_ENTITY, "requires_any": {"LOCATED_IN", "PART_OF"}},
}

RESERVED_PROPERTIES = {
    "constitutional_powers", "statutory_powers", "institutional_authority",
    "church_status", "ecclesial_status", "public_publication_state",
    "crown_reserved_power",
}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{2,127}$")


class DescriptorError(RuntimeError): pass
class ValidationError(DescriptorError): pass
class AuthorityRequired(DescriptorError): pass
class StaleDescriptor(DescriptorError): pass
class ConflictError(DescriptorError): pass
class ReplayError(DescriptorError): pass


def clone(v: Any) -> Any:
    return json.loads(json.dumps(v, sort_keys=True, ensure_ascii=False))


def digest(v: Any) -> str:
    raw = json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}.") as f:
        f.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        f.flush(); os.fsync(f.fileno()); tmp = f.name
    os.replace(tmp, path)


def append_event(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(event), sort_keys=True, ensure_ascii=False) + "\n")
        f.flush(); os.fsync(f.fileno())


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: item = json.loads(line)
        except Exception as exc: raise ReplayError(f"descriptor journal line {n} is malformed") from exc
        if not isinstance(item, dict): raise ReplayError(f"descriptor journal line {n} must be an object")
        out.append(item)
    return out


def deterministic_id(descriptor_id: str, key: str, family: str, archetype: str, source_state_ref: str, seed: str | None) -> str:
    token = digest({
        "schema": SCHEMA_VERSION, "descriptor": descriptor_id, "key": key,
        "family": family, "archetype": archetype, "source": source_state_ref, "seed": seed,
    })[:20]
    return f"world:{family.lower()}:{token}"


def _provenance(obj: Mapping[str, Any], properties: Mapping[str, Any], initial: Mapping[str, Any]) -> None:
    rows = obj.get("provenance")
    if not isinstance(rows, list) or not rows: raise ValidationError("each object requires non-empty provenance")
    by_path: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping): raise ValidationError("provenance rows must be objects")
        path, cls = row.get("path"), row.get("classification")
        if not isinstance(path, str) or not path: raise ValidationError("provenance path must be non-empty")
        if cls not in FACT_CLASSES: raise ValidationError(f"unsupported provenance classification for {path}")
        if cls == SOURCE_FACT and not str(row.get("source_ref", "")).strip():
            raise ValidationError(f"SOURCE_FACT {path} requires source_ref")
        if path in by_path: raise ValidationError(f"duplicate provenance path {path}")
        by_path[path] = row
    required = {"archetype"}
    required.update(f"properties.{k}" for k in properties)
    required.update(f"initial_state.{k}" for k in initial)
    if obj.get("relationships"): required.add("relationships")
    if obj.get("parent_id") is not None: required.add("parent_id")
    if obj.get("resolution_level", 0): required.add("resolution_level")
    missing = required.difference(by_path)
    if missing: raise ValidationError(f"missing provenance for {sorted(missing)}")
    if any(row["classification"] == UNKNOWN_REQUIRED_DECISION for row in by_path.values()):
        raise AuthorityRequired("descriptor contains UNKNOWN_REQUIRED_DECISION")


def _no_cycles(objects: list[Mapping[str, Any]]) -> None:
    ids = {str(o["id"]) for o in objects}
    parents: dict[str, set[str]] = {i: set() for i in ids}
    for obj in objects:
        oid = str(obj["id"])
        if obj.get("parent_id") in ids: parents[oid].add(str(obj["parent_id"]))
        for rel in obj["relationships"]:
            if rel["target"] not in ids: continue
            if rel["type"] == "PART_OF": parents[oid].add(rel["target"])
            elif rel["type"] == "CONTAINS": parents[rel["target"]].add(oid)
    visiting, done = set(), set()
    def visit(node: str) -> None:
        if node in visiting: raise ValidationError("containment cycle detected")
        if node in done: return
        visiting.add(node)
        for parent in parents[node]: visit(parent)
        visiting.remove(node); done.add(node)
    for node in ids: visit(node)


def validate_descriptor(
    descriptor: Mapping[str, Any], *, accepted_source_state_ref: str,
    external_objects: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(descriptor, Mapping): raise ValidationError("descriptor must be an object")
    d = clone(descriptor)
    if d.get("schema_version") != SCHEMA_VERSION: raise ValidationError("unsupported descriptor schema version")
    did = d.get("descriptor_id")
    if not isinstance(did, str) or not ID_RE.match(did): raise ValidationError("invalid descriptor_id")
    source = d.get("source_state_ref")
    if not isinstance(source, str) or not source: raise ValidationError("source_state_ref is required")
    if source != accepted_source_state_ref: raise StaleDescriptor(f"descriptor source {source!r} != accepted {accepted_source_state_ref!r}")
    if d.get("authority_class") not in AUTHORITY_CLASSES: raise ValidationError("unsupported authority_class")
    if not str(d.get("authority_ref", "")).strip(): raise ValidationError("authority_ref is required")
    if d.get("generation_seed") is not None and not isinstance(d["generation_seed"], str):
        raise ValidationError("generation_seed must be string or null")
    if not isinstance(d.get("objects"), list) or not d["objects"]: raise ValidationError("objects must be non-empty list")

    external = {str(k): dict(v) for k, v in (external_objects or {}).items()}
    resolved, keys, ids = [], set(), set()
    for raw in d["objects"]:
        if not isinstance(raw, Mapping): raise ValidationError("descriptor objects must be objects")
        obj = clone(raw)
        key, archetype = obj.get("key"), obj.get("archetype")
        if not isinstance(key, str) or not ID_RE.match(key): raise ValidationError("invalid object key")
        if key in keys: raise ValidationError(f"duplicate object key {key}")
        keys.add(key)
        if archetype not in ARCHETYPES: raise ValidationError(f"unknown archetype {archetype!r}")
        family = obj.get("family", ARCHETYPES[archetype]["family"])
        if family != ARCHETYPES[archetype]["family"]: raise ValidationError(f"wrong family for {archetype}")
        obj["family"] = family
        requested = obj.get("requested_id")
        oid = requested or deterministic_id(did, key, family, archetype, source, d.get("generation_seed"))
        if not isinstance(oid, str) or not ID_RE.match(oid): raise ValidationError(f"invalid object id for {key}")
        if oid in ids: raise ValidationError(f"duplicate world object id {oid}")
        ids.add(oid); obj["id"] = oid
        props, initial = obj.get("properties", {}), obj.get("initial_state", {})
        if not isinstance(props, dict) or not isinstance(initial, dict): raise ValidationError("properties/initial_state must be objects")
        reserved = RESERVED_PROPERTIES.intersection(props)
        if reserved: raise AuthorityRequired(f"reserved semantic fields require competent authority: {sorted(reserved)}")
        level = obj.get("resolution_level", 0)
        if not isinstance(level, int) or level < 0: raise ValidationError("resolution_level must be non-negative integer")
        obj["resolution_level"] = level
        _provenance(obj, props, initial)
        rels = obj.get("relationships", [])
        if not isinstance(rels, list): raise ValidationError("relationships must be a list")
        clean = []
        for rel in rels:
            if not isinstance(rel, Mapping) or rel.get("type") not in RELATIONS or not isinstance(rel.get("target"), str):
                raise ValidationError("invalid relationship")
            clean.append({"type": rel["type"], "target": rel["target"]})
        obj["relationships"] = clean
        if obj.get("parent_id") is not None and not isinstance(obj["parent_id"], str): raise ValidationError("parent_id must be string")
        resolved.append(obj)

    key_id = {o["key"]: o["id"] for o in resolved}
    by_id = {o["id"]: o for o in resolved}
    for obj in resolved:
        obj["relationships"] = [{"type": r["type"], "target": key_id.get(r["target"], r["target"])} for r in obj["relationships"]]
        if obj.get("parent_id") in key_id: obj["parent_id"] = key_id[obj["parent_id"]]
        refs = [r["target"] for r in obj["relationships"]]
        if obj.get("parent_id"): refs.append(obj["parent_id"])
        missing = [ref for ref in refs if ref not in by_id and ref not in external]
        if missing: raise ValidationError(f"{obj['key']} references nonexistent target {missing[0]}")

    all_objects = {**external, **by_id}
    for obj in resolved:
        cfg = ARCHETYPES[obj["archetype"]]
        counts: dict[str, int] = {}
        for rel in obj["relationships"]: counts[rel["type"]] = counts.get(rel["type"], 0) + 1
        for rel, minimum in cfg.get("requires", {}).items():
            if counts.get(rel, 0) < minimum: raise ValidationError(f"{obj['archetype']} requires {minimum} {rel}")
        any_req = cfg.get("requires_any")
        if any_req and not any(counts.get(r, 0) for r in any_req): raise ValidationError(f"{obj['archetype']} requires one of {sorted(any_req)}")
        if obj["archetype"] == "TRAFFIC_LIGHT":
            if any(all_objects[r["target"]].get("archetype") != "JUNCTION" for r in obj["relationships"] if r["type"] == "CONTROLS"):
                raise ValidationError("TRAFFIC_LIGHT may control only JUNCTION")
        if obj["archetype"] == "FIELD":
            if any(all_objects[r["target"]].get("archetype") != "FARM" for r in obj["relationships"] if r["type"] == "PART_OF"):
                raise ValidationError("FIELD must be PART_OF FARM")
        if obj["archetype"] in {"ROAD", "MOTORWAY", "RAILWAY"}:
            targets = [r["target"] for r in obj["relationships"] if r["type"] == "CONNECTS"]
            if len(set(targets)) < 2: raise ValidationError(f"{obj['archetype']} must connect two distinct targets")
    _no_cycles(resolved)
    d["objects"] = resolved
    d["validation_status"] = "WORLD_VALID"
    d["descriptor_digest"] = digest({k: v for k, v in d.items() if k != "descriptor_digest"})
    return d


def authority_route(validated: Mapping[str, Any]) -> dict[str, str]:
    cls = validated["authority_class"]
    if cls in AUTO_CLASSES: return {"decision": "AUTO_PERSIST_ALLOWED", "route": "descriptor world store"}
    if cls == CANON_CANDIDATE: return {"decision": "PREVIEW_ONLY", "route": "competent canon owner"}
    if cls == INSTITUTIONAL_OR_POLICY_PROPOSAL: return {"decision": "ROUTE_REQUIRED", "route": "competent institution"}
    return {"decision": "CROWN_OR_COMPETENT_AUTHORITY_REQUIRED", "route": "reserved authority"}


def initial_store() -> dict[str, Any]:
    return {"schema_version": STORE_VERSION, "objects": {}, "processed_commands": {}, "next_sequence": 1}


class DescriptorWorldStore:
    def __init__(self, root: Path, clock: CentralAlderniaClock) -> None:
        self.root = Path(root); self.state_path = self.root / "descriptor-world.json"; self.journal_path = self.root / "descriptor-journal.jsonl"; self.clock = clock
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8")); self._check()
        else:
            self.state = initial_store(); atomic_write(self.state_path, self.state); self.journal_path.touch(exist_ok=True)

    def _check(self) -> None:
        s = self.state
        if not isinstance(s, dict) or s.get("schema_version") != STORE_VERSION or not isinstance(s.get("objects"), dict) or not isinstance(s.get("processed_commands"), dict) or not isinstance(s.get("next_sequence"), int):
            raise ReplayError("malformed descriptor store")

    def _persist(self) -> None: self._check(); atomic_write(self.state_path, self.state)
    def current_state(self) -> dict[str, Any]: return clone(self.state)
    def get_object(self, oid: str) -> dict[str, Any] | None: return clone(self.state["objects"][oid]) if oid in self.state["objects"] else None
    def _seq(self) -> int: n = self.state["next_sequence"]; self.state["next_sequence"] += 1; return n

    def preview(self, descriptor: Mapping[str, Any], *, accepted_source_state_ref: str, external_objects: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        v = validate_descriptor(descriptor, accepted_source_state_ref=accepted_source_state_ref, external_objects=external_objects)
        return {"validation_status": "WORLD_VALID", "descriptor_digest": v["descriptor_digest"], "authority": authority_route(v), "objects": clone(v["objects"])}

    def instantiate(
        self, descriptor: Mapping[str, Any], *, command_id: str, accepted_source_state_ref: str,
        effective_time: datetime, external_objects: Mapping[str, Mapping[str, Any]] | None = None,
        permitted_authority_classes: set[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(command_id, str) or not command_id.strip(): raise ValidationError("command_id must be non-empty")
        command_id = command_id.strip()
        v = validate_descriptor(descriptor, accepted_source_state_ref=accepted_source_state_ref, external_objects=external_objects)
        route = authority_route(v)
        if route["decision"] != "AUTO_PERSIST_ALLOWED": raise AuthorityRequired(f"{v['authority_class']} may not auto-persist; route={route['route']}")
        if v["authority_class"] not in (permitted_authority_classes or {EXPERIMENT_NON_CANONICAL}):
            raise AuthorityRequired(f"caller authority does not permit {v['authority_class']}")
        prior = self.state["processed_commands"].get(command_id)
        if prior:
            if prior["descriptor_digest"] != v["descriptor_digest"]: raise ConflictError("command_id already used for different descriptor")
            return clone(prior["result"])
        for obj in v["objects"]:
            existing = self.state["objects"].get(obj["id"])
            if existing and digest(existing["source_object"]) != digest(obj): raise ConflictError(f"world object id {obj['id']} conflicts")
        created = []
        for obj in v["objects"]:
            if obj["id"] in self.state["objects"]: continue
            record = {
                "id": obj["id"], "family": obj["family"], "archetype": obj["archetype"],
                "authority_class": v["authority_class"],
                "canonical_status": "NON_CANONICAL" if v["authority_class"] == EXPERIMENT_NON_CANONICAL else "DYNAMIC_WORLD",
                "validation_status": "WORLD_VALID", "descriptor_id": v["descriptor_id"],
                "descriptor_digest": v["descriptor_digest"], "source_state_ref": v["source_state_ref"],
                "authority_ref": v["authority_ref"], "resolution_level": obj["resolution_level"],
                "resolved_children": [], "source_object": obj,
            }
            record["object_digest"] = digest({k: val for k, val in record.items() if k != "object_digest"})
            self.state["objects"][obj["id"]] = record; created.append(obj["id"])
        instant = effective_time.astimezone(timezone.utc); tick, _ = self.clock.advance_tick(); snap = self.clock.snapshot(instant)
        event = {
            "event_type": "OBJECT_GRAPH_INSTANTIATED", "event_id": f"descriptor:{v['descriptor_id']}:{command_id}",
            "sequence": self._seq(), "clock_id": snap.clock_id, "dynasty_tick": tick,
            "effective_time": snap.utc_timestamp, "recorded_time": snap.utc_timestamp,
            "command_id": command_id, "descriptor_id": v["descriptor_id"], "descriptor_digest": v["descriptor_digest"],
            "authority_class": v["authority_class"], "authority_ref": v["authority_ref"], "source_state_ref": v["source_state_ref"],
            "created_ids": created, "objects": [self.state["objects"][obj["id"]] for obj in v["objects"]],
        }
        event["event_digest"] = digest({k: val for k, val in event.items() if k != "event_digest"}); append_event(self.journal_path, event)
        result = {
            "event_id": event["event_id"], "descriptor_digest": v["descriptor_digest"], "created_ids": created,
            "object_ids": [obj["id"] for obj in v["objects"]], "validation_status": "WORLD_VALID",
            "canonical_status": "NON_CANONICAL" if v["authority_class"] == EXPERIMENT_NON_CANONICAL else "DYNAMIC_WORLD",
        }
        self.state["processed_commands"][command_id] = {"descriptor_digest": v["descriptor_digest"], "result": result}; self._persist()
        return clone(result)

    def refine(
        self, *, parent_id: str, descriptor: Mapping[str, Any], command_id: str,
        accepted_source_state_ref: str, effective_time: datetime,
        permitted_authority_classes: set[str] | None = None,
    ) -> dict[str, Any]:
        parent = self.state["objects"].get(parent_id)
        if not parent: raise ValidationError("refinement parent does not exist")
        prior = self.state["processed_commands"].get(command_id)
        if prior and prior.get("kind") == "REFINEMENT": return clone(prior["result"])
        v = validate_descriptor(descriptor, accepted_source_state_ref=accepted_source_state_ref, external_objects={parent_id: parent["source_object"]})
        if v["authority_class"] not in AUTO_CLASSES: raise AuthorityRequired("refinement may not auto-persist")
        permitted = permitted_authority_classes or {EXPERIMENT_NON_CANONICAL}
        if v["authority_class"] not in permitted: raise AuthorityRequired("caller authority does not permit refinement")
        if any(o["id"] == parent_id for o in v["objects"]): raise ValidationError("refinement may not redefine parent")
        for obj in v["objects"]:
            attachments = {obj.get("parent_id")} | {r["target"] for r in obj["relationships"] if r["type"] == "PART_OF"}
            if parent_id not in attachments: raise ValidationError("every refinement object must attach to parent")
        before_id, before_source = parent["id"], digest(parent["source_object"])
        child = self.instantiate(v, command_id=f"{command_id}:children", accepted_source_state_ref=accepted_source_state_ref, effective_time=effective_time, external_objects={parent_id: parent["source_object"]}, permitted_authority_classes=permitted)
        parent = self.state["objects"][parent_id]
        parent["resolution_level"] = max(parent["resolution_level"], max(o["resolution_level"] for o in v["objects"]))
        parent["resolved_children"] = sorted(set(parent["resolved_children"]) | set(child["object_ids"]))
        parent["object_digest"] = digest({k: val for k, val in parent.items() if k != "object_digest"})
        if parent["id"] != before_id or digest(parent["source_object"]) != before_source: raise ConflictError("refinement rewrote parent identity/source")
        instant = effective_time.astimezone(timezone.utc); tick, _ = self.clock.advance_tick(); snap = self.clock.snapshot(instant)
        event = {
            "event_type": "OBJECT_GRAPH_REFINED", "event_id": f"descriptor-refine:{v['descriptor_id']}:{command_id}",
            "sequence": self._seq(), "clock_id": snap.clock_id, "dynasty_tick": tick,
            "effective_time": snap.utc_timestamp, "recorded_time": snap.utc_timestamp,
            "command_id": command_id, "descriptor_id": v["descriptor_id"], "descriptor_digest": v["descriptor_digest"],
            "authority_class": v["authority_class"], "authority_ref": v["authority_ref"], "source_state_ref": v["source_state_ref"],
            "parent_id": parent_id, "parent_resolution_level": parent["resolution_level"],
            "resolved_children": parent["resolved_children"], "child_created_ids": child["created_ids"],
            "child_object_ids": child["object_ids"], "child_canonical_status": child["canonical_status"],
            "parent_object_digest": parent["object_digest"],
        }
        event["event_digest"] = digest({k: val for k, val in event.items() if k != "event_digest"}); append_event(self.journal_path, event)
        result = {**child, "event_id": event["event_id"], "parent_id": parent_id, "parent_identity_stable": True, "parent_resolution_level": parent["resolution_level"]}
        self.state["processed_commands"][command_id] = {"kind": "REFINEMENT", "descriptor_digest": v["descriptor_digest"], "result": result}; self._persist()
        return clone(result)

    @staticmethod
    def replay(journal_path: Path) -> dict[str, Any]:
        state = initial_store()
        for event in read_journal(Path(journal_path)):
            if event.get("event_digest") != digest({k: v for k, v in event.items() if k != "event_digest"}): raise ReplayError("descriptor journal integrity failure")
            typ = event.get("event_type")
            if typ == "OBJECT_GRAPH_INSTANTIATED":
                for record in event.get("objects", []):
                    if record.get("object_digest") != digest({k: v for k, v in record.items() if k != "object_digest"}): raise ReplayError("world object integrity failure")
                    existing = state["objects"].get(record["id"])
                    if existing and digest(existing) != digest(record): raise ReplayError("conflicting duplicate object during replay")
                    state["objects"][record["id"]] = clone(record)
                result = {
                    "event_id": event["event_id"], "descriptor_digest": event["descriptor_digest"],
                    "created_ids": list(event.get("created_ids", [])), "object_ids": [r["id"] for r in event.get("objects", [])],
                    "validation_status": "WORLD_VALID",
                    "canonical_status": "NON_CANONICAL" if event["authority_class"] == EXPERIMENT_NON_CANONICAL else "DYNAMIC_WORLD",
                }
                state["processed_commands"][str(event["command_id"])] = {"descriptor_digest": event["descriptor_digest"], "result": result}
            elif typ == "OBJECT_GRAPH_REFINED":
                parent = state["objects"].get(str(event["parent_id"]))
                if not parent: raise ReplayError("refinement references missing parent")
                parent["resolution_level"] = int(event["parent_resolution_level"]); parent["resolved_children"] = list(event["resolved_children"])
                parent["object_digest"] = digest({k: v for k, v in parent.items() if k != "object_digest"})
                if parent["object_digest"] != event["parent_object_digest"]: raise ReplayError("refinement parent integrity failure")
                result = {
                    "event_id": event["event_id"], "descriptor_digest": event["descriptor_digest"],
                    "created_ids": list(event["child_created_ids"]), "object_ids": list(event["child_object_ids"]),
                    "validation_status": "WORLD_VALID", "canonical_status": event["child_canonical_status"],
                    "parent_id": event["parent_id"], "parent_identity_stable": True,
                    "parent_resolution_level": int(event["parent_resolution_level"]),
                }
                state["processed_commands"][str(event["command_id"])] = {"kind": "REFINEMENT", "descriptor_digest": event["descriptor_digest"], "result": result}
            else: raise ReplayError(f"unsupported descriptor event {typ}")
            state["next_sequence"] = max(state["next_sequence"], int(event["sequence"]) + 1)
        return state


def materialise_transport_objects_into_world(
    *, store: DescriptorWorldStore, runtime: WorldRuntime, object_ids: list[str],
    effective_time: datetime, command_prefix: str,
) -> list[dict[str, Any]]:
    records = [store.get_object(oid) for oid in object_ids]
    if any(record is None for record in records): raise ValidationError("bridge references unknown descriptor object")
    emitted = []
    for record in records:
        if record["family"] == PLACE_LAND and record["archetype"] == "PLACE":
            source = record["source_object"]
            emitted.append(runtime.register_place(
                place_id=record["id"], canonical_identity=str(source["properties"].get("name", source["key"])),
                effective_time=effective_time, command_id=f"{command_prefix}:place:{record['id']}",
                provenance=f"descriptor:{record['descriptor_id']}:{record['descriptor_digest']}",
            ))
    for record in records:
        if record["family"] == NETWORK_EDGE and record["archetype"] in {"ROAD", "MOTORWAY"}:
            source = record["source_object"]
            targets = [r["target"] for r in source["relationships"] if r["type"] == "CONNECTS"]
            if len(targets) != 2: raise ValidationError("bridge road requires exactly two CONNECTS relationships")
            emitted.append(runtime.register_route(
                route_id=record["id"], a=targets[0], b=targets[1],
                bidirectional=bool(source["properties"].get("bidirectional", True)),
                effective_time=effective_time, command_id=f"{command_prefix}:route:{record['id']}",
                provenance=f"descriptor:{record['descriptor_id']}:{record['descriptor_digest']}",
            ))
    return emitted
