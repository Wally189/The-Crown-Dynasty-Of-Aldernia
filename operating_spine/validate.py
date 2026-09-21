from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"
PROJECTION = ROOT / "projection"

SCHEMA_FILES = {
    "state": SCHEMAS / "aldernia-state-object.schema.json",
    "event": SCHEMAS / "aldernia-event.schema.json",
    "source": SCHEMAS / "source-record.schema.json",
}

REALITY_CLASSES = {
    "REAL_EXTERNAL_FACT",
    "REAL_REPORTED_CLAIM",
    "ALDERNIAN_CONSTITUTIONAL_CANON",
    "ALDERNIAN_IN_WORLD_FACT",
    "SIMULATED_DERIVED_STATE",
    "FICTIONAL_EDITORIAL_DETAIL",
    "ANALYSIS",
    "HYPOTHESIS",
    "UNKNOWN",
}


class SpineValidationError(ValueError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _required(schema: dict[str, Any]) -> set[str]:
    return set(schema.get("required", []))


def _enum(schema: dict[str, Any], field: str) -> set[str]:
    return set(schema["properties"][field].get("enum", []))


def validate_required(record: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    missing = sorted(_required(schema) - set(record))
    if missing:
        raise SpineValidationError(f"{label}: missing required fields {missing}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(record) - set(schema["properties"]))
        if extra:
            raise SpineValidationError(f"{label}: unexpected fields {extra}")


def validate_projection() -> dict[str, int]:
    state_schema = load_json(SCHEMA_FILES["state"])
    event_schema = load_json(SCHEMA_FILES["event"])
    source_schema = load_json(SCHEMA_FILES["source"])

    manifest = load_json(PROJECTION / "manifest.json")
    owners_doc = load_json(PROJECTION / "canonical-owners.json")
    states = load_json(PROJECTION / "state-objects.json")
    events = load_json(PROJECTION / "events.json")
    sources = load_json(PROJECTION / "source-records.json")

    if manifest.get("projection_authoritative") is not False:
        raise SpineValidationError("manifest must be explicitly non-authoritative")
    if owners_doc.get("projection_authoritative") is not False:
        raise SpineValidationError("owner map must be explicitly non-authoritative")

    owner_ids = {owner["owner_id"] for owner in owners_doc["owners"]}
    if len(owner_ids) != len(owners_doc["owners"]):
        raise SpineValidationError("duplicate owner_id")

    nonowners = {item["system"]: item["reason"] for item in owners_doc["non_owners"]}
    if "House of Carol" not in nonowners:
        raise SpineValidationError("House of Carol support-only boundary missing")
    if "No Aldernia governance or content ownership" not in nonowners["House of Carol"]:
        raise SpineValidationError("House of Carol boundary is not explicit")

    source_ids: set[str] = set()
    for source in sources:
        validate_required(source, source_schema, source.get("source_id", "source"))
        if source["schema_version"] != "1.0.0" or source["projection_authoritative"] is not False:
            raise SpineValidationError(f"{source['source_id']}: schema/projection authority invalid")
        if source["source_type"] not in _enum(source_schema, "source_type"):
            raise SpineValidationError(f"{source['source_id']}: invalid source_type")
        if source["authority_class"] not in _enum(source_schema, "authority_class"):
            raise SpineValidationError(f"{source['source_id']}: invalid authority_class")
        if source["freshness_class"] not in _enum(source_schema, "freshness_class"):
            raise SpineValidationError(f"{source['source_id']}: invalid freshness_class")
        if source["status"] not in _enum(source_schema, "status"):
            raise SpineValidationError(f"{source['source_id']}: invalid status")
        if source["source_id"] in source_ids:
            raise SpineValidationError(f"duplicate source_id {source['source_id']}")
        source_ids.add(source["source_id"])

    state_ids: set[str] = set()
    for state in states:
        validate_required(state, state_schema, state.get("entity_id", "state"))
        if state["schema_version"] != "1.0.0" or state["projection_authoritative"] is not False:
            raise SpineValidationError(f"{state['entity_id']}: schema/projection authority invalid")
        if not state["canonical_record_ref"].startswith("drive:"):
            raise SpineValidationError(f"{state['entity_id']}: canonical record must point to Drive")
        if state["owning_institution"] not in owner_ids:
            raise SpineValidationError(f"{state['entity_id']}: unknown owner {state['owning_institution']}")
        if state["reality_class"] not in REALITY_CLASSES:
            raise SpineValidationError(f"{state['entity_id']}: invalid reality_class")
        if state["publication_status"] not in _enum(state_schema, "publication_status"):
            raise SpineValidationError(f"{state['entity_id']}: invalid publication_status")
        if state["freshness_class"] not in _enum(state_schema, "freshness_class"):
            raise SpineValidationError(f"{state['entity_id']}: invalid freshness_class")
        unknown_sources = sorted(set(state["source_refs"] + state["verification_refs"]) - source_ids)
        if unknown_sources:
            raise SpineValidationError(f"{state['entity_id']}: unknown source refs {unknown_sources}")
        if state["entity_id"] in state_ids:
            raise SpineValidationError(f"duplicate entity_id {state['entity_id']}")
        state_ids.add(state["entity_id"])

    event_ids: set[str] = set()
    for event in events:
        validate_required(event, event_schema, event.get("event_id", "event"))
        if event["schema_version"] != "1.0.0" or event["projection_authoritative"] is not False:
            raise SpineValidationError(f"{event['event_id']}: schema/projection authority invalid")
        if event["owning_institution"] not in owner_ids:
            raise SpineValidationError(f"{event['event_id']}: unknown owner")
        if event["reality_class"] not in REALITY_CLASSES:
            raise SpineValidationError(f"{event['event_id']}: invalid reality_class")
        if event["decision"] not in _enum(event_schema, "decision"):
            raise SpineValidationError(f"{event['event_id']}: invalid decision")
        missing_entities = sorted(set(event["affected_entity_ids"]) - state_ids)
        if missing_entities:
            raise SpineValidationError(f"{event['event_id']}: unknown affected entities {missing_entities}")
        unknown_sources = sorted(set(event["source_refs"] + event["evidence_refs"]) - source_ids)
        if unknown_sources:
            raise SpineValidationError(f"{event['event_id']}: unknown source/evidence refs {unknown_sources}")
        if event["event_id"] in event_ids:
            raise SpineValidationError(f"duplicate event_id {event['event_id']}")
        event_ids.add(event["event_id"])

    for state in states:
        if state["event_head"] is not None and state["event_head"] not in event_ids:
            raise SpineValidationError(f"{state['entity_id']}: event_head does not resolve")

    release = next((s for s in states if s["entity_id"] == "aldernia:public-release"), None)
    if not release:
        raise SpineValidationError("current public release state is missing")
    accepted = manifest.get("accepted_public_release", {})
    if release["canonical_name"] != "ALD-CROWN-FOUNDING-09":
        raise SpineValidationError("release build identity is not ALD-CROWN-FOUNDING-09")
    if accepted.get("build_id") != release["canonical_name"]:
        raise SpineValidationError("manifest/release build identity mismatch")
    if accepted.get("commit_sha") != "81f8af9942881738977b0913eed2f96d1c0c78d2":
        raise SpineValidationError("manifest commit SHA mismatch")

    return {
        "owners": len(owner_ids),
        "sources": len(source_ids),
        "state_objects": len(state_ids),
        "events": len(event_ids),
    }


def main() -> int:
    counts = validate_projection()
    print("Aldernia Operating Spine Phase 1 validation PASS")
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
