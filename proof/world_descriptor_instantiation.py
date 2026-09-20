from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.world_descriptors import (
    AUTHORITY_REF, SCHEMA_VERSION, SOURCE_FACT, EXPERIMENTAL_PARAMETER,
    EXPERIMENT_NON_CANONICAL, PLACE_LAND,
    DescriptorWorldStore, materialise_transport_objects_into_world, validate_descriptor,
)
from aldernia_runtime.world_state import PLACE_ST_BRIGIDS, TEST_FERRY_ID, WorldRuntime

SOURCE = "source-state:world-descriptor-proof-v1"
T0 = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 20, 15, 25, tzinfo=timezone.utc)
PUBLIC_FILES = [ROOT / "index.html", ROOT / "aldernia" / "public-state.json"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return "UNAVAILABLE"


def p(path: str, cls: str = EXPERIMENTAL_PARAMETER, source_ref: str | None = None):
    row = {"path": path, "classification": cls}
    if source_ref:
        row["source_ref"] = source_ref
    return row


def o(key, archetype, *, requested_id=None, properties=None, initial=None,
      relationships=None, parent_id=None, resolution=0):
    properties, initial, relationships = properties or {}, initial or {}, relationships or []
    provenance = [p("archetype")]
    provenance += [p(f"properties.{k}") for k in properties]
    provenance += [p(f"initial_state.{k}") for k in initial]
    if relationships:
        provenance.append(p("relationships"))
    if parent_id:
        provenance.append(p("parent_id"))
    if resolution:
        provenance.append(p("resolution_level"))
    out = {
        "key": key, "archetype": archetype, "properties": properties,
        "initial_state": initial, "relationships": relationships,
        "resolution_level": resolution, "provenance": provenance,
    }
    if requested_id:
        out["requested_id"] = requested_id
    if parent_id:
        out["parent_id"] = parent_id
    return out


def d(did, objects):
    return {
        "schema_version": SCHEMA_VERSION,
        "descriptor_id": did,
        "source_state_ref": SOURCE,
        "authority_class": EXPERIMENT_NON_CANONICAL,
        "authority_ref": AUTHORITY_REF,
        "generation_seed": None,
        "objects": objects,
    }


def cross_domain_descriptor():
    return d("descriptor:proof:cross-domain", [
        o("place-a", "PLACE", requested_id="test:proof:place:a", properties={"name": "Proof Town"}),
        o("place-b", "PLACE", requested_id="test:proof:place:b", properties={"name": "Proof Quay"}),
        o("road", "ROAD", requested_id="test:proof:road:01", properties={"bidirectional": True},
          relationships=[{"type": "CONNECTS", "target": "place-a"}, {"type": "CONNECTS", "target": "place-b"}]),
        o("junction", "JUNCTION", requested_id="test:proof:junction:01",
          relationships=[{"type": "CONNECTED_TO", "target": "road"}]),
        o("signal", "TRAFFIC_LIGHT", requested_id="test:proof:signal:01", initial={"signal": "RED"},
          relationships=[{"type": "CONTROLS", "target": "junction"}]),
        o("building", "BUILDING", requested_id="test:proof:building:01",
          relationships=[{"type": "LOCATED_IN", "target": "place-a"}]),
        o("school", "SCHOOL", requested_id="test:proof:school:01", properties={"capacity": 600},
          relationships=[{"type": "OCCUPIES", "target": "building"}]),
        o("farm", "FARM", requested_id="test:proof:farm:01", properties={"land_use": "TEST_MIXED"}),
        o("field", "FIELD", requested_id="test:proof:field:01",
          relationships=[{"type": "PART_OF", "target": "farm"}]),
        o("factory", "FACTORY", requested_id="test:proof:factory:01", initial={"operating_state": "IDLE"},
          relationships=[{"type": "LOCATED_IN", "target": "place-b"}, {"type": "DEPENDS_ON", "target": "road"}]),
        o("port", "PORT", requested_id="test:proof:port:01",
          relationships=[{"type": "CONNECTED_TO", "target": "road"}]),
        o("vehicle", "VEHICLE", requested_id="test:proof:vehicle:01", initial={"movement_state": "PARKED"},
          relationships=[{"type": "LOCATED_IN", "target": "place-a"}]),
    ])


def settlement_block():
    return d("descriptor:proof:settlement-block", [
        o("district", "HOUSING_ESTATE", requested_id="test:proof:district:01", properties={"aggregate_units": 48}),
        o("gate", "PLACE", requested_id="test:proof:gate:01", parent_id="district", properties={"name": "Test Gate"}),
        o("street", "ROAD", requested_id="test:proof:street:01", parent_id="district", properties={"bidirectional": True},
          relationships=[{"type": "CONNECTS", "target": "district"}, {"type": "CONNECTS", "target": "gate"}]),
        o("junction", "JUNCTION", requested_id="test:proof:settlement-junction", parent_id="district",
          relationships=[{"type": "CONNECTED_TO", "target": "street"}]),
        o("light", "TRAFFIC_LIGHT", requested_id="test:proof:settlement-light", parent_id="district",
          initial={"signal": "GREEN"}, relationships=[{"type": "CONTROLS", "target": "junction"}]),
        o("school-building", "BUILDING", requested_id="test:proof:settlement-school-building", parent_id="district",
          relationships=[{"type": "PART_OF", "target": "district"}]),
        o("school", "SCHOOL", requested_id="test:proof:settlement-school", parent_id="district",
          relationships=[{"type": "OCCUPIES", "target": "school-building"}]),
        o("workplace", "WORKPLACE", requested_id="test:proof:settlement-workplace", parent_id="district",
          relationships=[{"type": "PART_OF", "target": "district"}]),
    ])


def causal_descriptor():
    return d("descriptor:proof:causal-road", [
        o("destination", "PLACE", requested_id="test:proof:causal-destination", properties={"name": "Causal Destination"}),
        o("road", "ROAD", requested_id="test:proof:causal-road", properties={"bidirectional": True},
          relationships=[{"type": "CONNECTS", "target": PLACE_ST_BRIGIDS}, {"type": "CONNECTS", "target": "destination"}]),
    ])


def run() -> dict:
    public_before = {str(path.relative_to(ROOT)): sha(path) for path in PUBLIC_FILES}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        clock = CentralAlderniaClock(root / "clock.json")
        store = DescriptorWorldStore(root / "descriptors", clock)

        cross = cross_domain_descriptor()
        validated = validate_descriptor(cross, accepted_source_state_ref=SOURCE)
        cross_result = store.instantiate(
            cross, command_id="proof-cross-domain", accepted_source_state_ref=SOURCE, effective_time=T0
        )
        cross_families = sorted({item["family"] for item in validated["objects"]})

        composition = settlement_block()
        composition_result = store.instantiate(
            composition, command_id="proof-settlement", accepted_source_state_ref=SOURCE, effective_time=T0
        )
        district = store.get_object("test:proof:district:01")
        connected_children = [
            oid for oid in composition_result["object_ids"] if oid != "test:proof:district:01"
        ]
        assert len(connected_children) == 7

        refinement = d("descriptor:proof:district-refine", [
            o("housing-block", "BUILDING", requested_id="test:proof:housing-block:01",
              parent_id="test:proof:district:01", resolution=1,
              relationships=[{"type": "PART_OF", "target": "test:proof:district:01"}])
        ])
        refine_result = store.refine(
            parent_id="test:proof:district:01", descriptor=refinement,
            command_id="proof-refine", accepted_source_state_ref=SOURCE, effective_time=T1
        )
        district_after = store.get_object("test:proof:district:01")
        assert district_after["id"] == district["id"]
        assert district_after["source_object"] == district["source_object"]
        assert refine_result["parent_identity_stable"]

        world = WorldRuntime(root / "world", clock)
        external = {PLACE_ST_BRIGIDS: {
            "id": PLACE_ST_BRIGIDS, "key": "st-brigids", "family": PLACE_LAND, "archetype": "PLACE",
            "properties": {"name": "St Brigid's Isle"}, "initial_state": {}, "relationships": [],
            "resolution_level": 0,
            "provenance": [
                p("archetype", SOURCE_FACT, "Royal Palace — Aldernia Public World Blueprint"),
                p("properties.name", SOURCE_FACT, "Royal Palace — Aldernia Public World Blueprint"),
            ],
        }}
        causal = store.instantiate(
            causal_descriptor(), command_id="proof-causal-descriptor",
            accepted_source_state_ref=SOURCE, effective_time=T0, external_objects=external
        )
        bridge_events = materialise_transport_objects_into_world(
            store=store, runtime=world, object_ids=causal["object_ids"],
            effective_time=T0, command_prefix="proof-bridge"
        )
        destination = "test:proof:causal-destination"
        eligible_before = world.can_depart(PLACE_ST_BRIGIDS, destination, TEST_FERRY_ID)
        assert eligible_before
        world.depart(
            origin=PLACE_ST_BRIGIDS, destination=destination, effective_time=T0, arrival_time=T1,
            command_id="proof-descriptor-road-journey", entity_id=TEST_FERRY_ID
        )
        world.process_due(now=T1)
        world_live = world.current_state()
        assert world_live["entities"][TEST_FERRY_ID]["location"] == destination
        world_replay = WorldRuntime.replay(root / "world" / "world-journal.jsonl")
        assert world_replay == world_live

        descriptor_live = store.current_state()
        descriptor_replay = DescriptorWorldStore.replay(store.journal_path)
        assert descriptor_replay == descriptor_live

        del world
        restarted_world = WorldRuntime(root / "world", clock)
        assert restarted_world.current_state() == world_live

        public_after = {str(path.relative_to(ROOT)): sha(path) for path in PUBLIC_FILES}
        assert public_before == public_after

        return {
            "result": "PASS — DESCRIPTORS CAN BECOME WORLD",
            "branch_head": git("rev-parse", "HEAD"),
            "observed_origin_main": git("rev-parse", "origin/main"),
            "authority_ref": AUTHORITY_REF,
            "source_state_ref": SOURCE,
            "cross_domain": {
                "descriptor_digest": cross_result["descriptor_digest"],
                "object_count": len(cross_result["object_ids"]),
                "families": cross_families,
                "object_ids": cross_result["object_ids"],
            },
            "composition": {
                "descriptor_digest": composition_result["descriptor_digest"],
                "parent_id": district["id"],
                "child_count": len(connected_children),
                "parent_identity_stable_after_refinement": refine_result["parent_identity_stable"],
                "resolution_level_after_refinement": district_after["resolution_level"],
            },
            "causal_integration": {
                "descriptor_digest": causal["descriptor_digest"],
                "bridge_event_types": [event["event_type"] for event in bridge_events],
                "journey_eligible_via_descriptor_road": eligible_before,
                "final_ferry_location": world_live["entities"][TEST_FERRY_ID]["location"],
                "world_replay_matches_live": world_replay == world_live,
                "restart_matches_live": restarted_world.current_state() == world_live,
            },
            "descriptor_replay_matches_live": descriptor_replay == descriptor_live,
            "generated_status": "NON_CANONICAL",
            "public_artifact_hashes_before": public_before,
            "public_artifact_hashes_after": public_after,
            "public_artifact_unchanged": public_before == public_after,
        }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = run()
    rendered = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    print(rendered)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
