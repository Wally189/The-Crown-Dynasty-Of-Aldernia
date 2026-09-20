from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.world_descriptors import (
    AUTHORITY_REF, SCHEMA_VERSION,
    SOURCE_FACT, GENERATED_DETAIL, EXPERIMENTAL_PARAMETER,
    EXPERIMENT_NON_CANONICAL, DYNAMIC_WORLD, CANON_CANDIDATE, CANONICAL_OR_RESERVED,
    PLACE_LAND, BUILDING_FACILITY, NETWORK_EDGE, NETWORK_NODE_CONTROL,
    INSTITUTION_ORGANISATION, VEHICLE_MOVING_ENTITY,
    AuthorityRequired, ConflictError, ReplayError, StaleDescriptor, ValidationError,
    DescriptorWorldStore, materialise_transport_objects_into_world, validate_descriptor,
)
from aldernia_runtime.world_state import PLACE_ST_BRIGIDS, TEST_FERRY_ID, WorldRuntime

SOURCE = "source-state:test-v1"
T0 = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 20, 14, 20, tzinfo=timezone.utc)


def prov(path, cls=EXPERIMENTAL_PARAMETER, source_ref=None):
    row = {"path": path, "classification": cls}
    if source_ref: row["source_ref"] = source_ref
    return row


def obj(key, archetype, *, family=None, requested_id=None, properties=None, initial=None,
        relationships=None, parent_id=None, resolution=0, classifications=None):
    properties, initial, relationships = properties or {}, initial or {}, relationships or []
    classifications = classifications or {}
    rows = [prov("archetype", classifications.get("archetype", EXPERIMENTAL_PARAMETER))]
    rows += [prov(f"properties.{k}", classifications.get(f"properties.{k}", EXPERIMENTAL_PARAMETER)) for k in properties]
    rows += [prov(f"initial_state.{k}", classifications.get(f"initial_state.{k}", EXPERIMENTAL_PARAMETER)) for k in initial]
    if relationships: rows.append(prov("relationships", classifications.get("relationships", EXPERIMENTAL_PARAMETER)))
    if parent_id is not None: rows.append(prov("parent_id", classifications.get("parent_id", EXPERIMENTAL_PARAMETER)))
    if resolution: rows.append(prov("resolution_level", classifications.get("resolution_level", EXPERIMENTAL_PARAMETER)))
    out = {
        "key": key, "archetype": archetype, "properties": properties, "initial_state": initial,
        "relationships": relationships, "resolution_level": resolution, "provenance": rows,
    }
    if family: out["family"] = family
    if requested_id: out["requested_id"] = requested_id
    if parent_id is not None: out["parent_id"] = parent_id
    return out


def desc(did, objects, *, authority=EXPERIMENT_NON_CANONICAL, source=SOURCE):
    return {
        "schema_version": SCHEMA_VERSION, "descriptor_id": did, "source_state_ref": source,
        "authority_class": authority, "authority_ref": AUTHORITY_REF, "generation_seed": None,
        "objects": objects,
    }


def cross_domain():
    return desc("descriptor:test:cross-domain", [
        obj("place-a", "PLACE", family=PLACE_LAND, requested_id="test:place:town", properties={"name": "Test Town"}),
        obj("place-b", "PLACE", family=PLACE_LAND, requested_id="test:place:quay", properties={"name": "Test Quay"}),
        obj("road", "ROAD", family=NETWORK_EDGE, requested_id="test:road:01", properties={"bidirectional": True},
            relationships=[{"type": "CONNECTS", "target": "place-a"}, {"type": "CONNECTS", "target": "place-b"}]),
        obj("junction", "JUNCTION", family=NETWORK_NODE_CONTROL, requested_id="test:junction:01",
            relationships=[{"type": "CONNECTED_TO", "target": "road"}]),
        obj("signal", "TRAFFIC_LIGHT", family=NETWORK_NODE_CONTROL, requested_id="test:signal:01",
            initial={"signal": "RED"}, relationships=[{"type": "CONTROLS", "target": "junction"}]),
        obj("building", "BUILDING", family=BUILDING_FACILITY, requested_id="test:building:school",
            relationships=[{"type": "LOCATED_IN", "target": "place-a"}]),
        obj("school", "SCHOOL", family=INSTITUTION_ORGANISATION, requested_id="test:school:01",
            properties={"capacity": 900}, relationships=[{"type": "OCCUPIES", "target": "building"}]),
        obj("farm", "FARM", family=PLACE_LAND, requested_id="test:farm:01", properties={"land_use": "MIXED_TEST"}),
        obj("field", "FIELD", family=PLACE_LAND, requested_id="test:field:01",
            relationships=[{"type": "PART_OF", "target": "farm"}]),
        obj("factory", "FACTORY", family=BUILDING_FACILITY, requested_id="test:factory:01",
            initial={"operating_state": "IDLE"},
            relationships=[{"type": "LOCATED_IN", "target": "place-b"}, {"type": "DEPENDS_ON", "target": "road"}]),
        obj("port", "PORT", family=BUILDING_FACILITY, requested_id="test:port:01",
            relationships=[{"type": "CONNECTED_TO", "target": "road"}]),
        obj("vehicle", "VEHICLE", family=VEHICLE_MOVING_ENTITY, requested_id="test:vehicle:01",
            initial={"movement_state": "PARKED"}, relationships=[{"type": "LOCATED_IN", "target": "place-a"}]),
    ])


class DescriptorTests(unittest.TestCase):
    def store(self, root):
        clock = CentralAlderniaClock(root / "clock.json")
        return clock, DescriptorWorldStore(root / "descriptor", clock)

    def test_cross_domain_families_and_relationships(self):
        v = validate_descriptor(cross_domain(), accepted_source_state_ref=SOURCE)
        families = {o["family"] for o in v["objects"]}
        self.assertTrue({PLACE_LAND, BUILDING_FACILITY, NETWORK_EDGE, NETWORK_NODE_CONTROL,
                         INSTITUTION_ORGANISATION, VEHICLE_MOVING_ENTITY}.issubset(families))
        by_id = {o["id"]: o for o in v["objects"]}
        self.assertEqual(by_id["test:signal:01"]["relationships"][0]["target"], "test:junction:01")
        self.assertEqual(by_id["test:field:01"]["relationships"][0]["target"], "test:farm:01")

    def test_deterministic_generated_ids_and_digest(self):
        d = desc("descriptor:test:determinism", [obj("place", "PLACE", properties={"name": "Stable"})])
        a = validate_descriptor(d, accepted_source_state_ref=SOURCE)
        b = validate_descriptor(d, accepted_source_state_ref=SOURCE)
        self.assertEqual(a["objects"][0]["id"], b["objects"][0]["id"])
        self.assertEqual(a["descriptor_digest"], b["descriptor_digest"])

    def test_unknown_schema_stale_and_duplicate_ids_fail(self):
        d = desc("descriptor:test:unknown", [obj("x", "PLACE")]); d["objects"][0]["archetype"] = "STARPORT"
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = cross_domain(); d["schema_version"] = 999
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = cross_domain(); d["source_state_ref"] = "old"
        with self.assertRaises(StaleDescriptor): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = desc("descriptor:test:dup", [obj("a", "PLACE", requested_id="test:same:id"), obj("b", "PLACE", requested_id="test:same:id")])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)

    def test_source_fact_needs_source_and_material_fields_need_provenance(self):
        d = desc("descriptor:test:bad-source", [{
            "key": "place", "archetype": "PLACE", "properties": {"name": "Claimed"},
            "initial_state": {}, "relationships": [], "resolution_level": 0,
            "provenance": [{"path": "archetype", "classification": SOURCE_FACT}, prov("properties.name", GENERATED_DETAIL)],
        }])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = desc("descriptor:test:missing-prov", [{
            "key": "place", "archetype": "PLACE", "properties": {"name": "Missing"},
            "initial_state": {}, "relationships": [], "resolution_level": 0, "provenance": [prov("archetype")],
        }])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)

    def test_parliament_reserved_semantics_cannot_be_invented(self):
        d = desc("descriptor:test:parliament", [
            obj("place", "PLACE", properties={"name": "Synthetic Civic Place"}),
            obj("parliament", "PARLIAMENT_BUILDING", properties={"constitutional_powers": ["invented veto"]},
                relationships=[{"type": "LOCATED_IN", "target": "place"}]),
        ])
        with self.assertRaises(AuthorityRequired): validate_descriptor(d, accepted_source_state_ref=SOURCE)

    def test_relationship_integrity_and_containment_cycles(self):
        d = desc("descriptor:test:road", [obj("road", "ROAD", relationships=[
            {"type": "CONNECTS", "target": "missing-a"}, {"type": "CONNECTS", "target": "missing-b"}])])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = desc("descriptor:test:signal", [
            obj("place", "PLACE"),
            obj("signal", "TRAFFIC_LIGHT", relationships=[{"type": "CONTROLS", "target": "place"}]),
        ])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)
        d = desc("descriptor:test:cycle", [obj("a", "PLACE", parent_id="b"), obj("b", "PLACE", parent_id="a")])
        with self.assertRaises(ValidationError): validate_descriptor(d, accepted_source_state_ref=SOURCE)

    def test_canon_candidate_and_reserved_preview_only(self):
        for authority in (CANON_CANDIDATE, CANONICAL_OR_RESERVED):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); _clock, store = self.store(root)
                d = desc(f"descriptor:test:{authority.lower()}", [obj("place", "PLACE")], authority=authority)
                self.assertEqual(store.preview(d, accepted_source_state_ref=SOURCE)["validation_status"], "WORLD_VALID")
                with self.assertRaises(AuthorityRequired):
                    store.instantiate(d, command_id="blocked", accepted_source_state_ref=SOURCE, effective_time=T0)
                self.assertEqual(store.current_state()["objects"], {})

    def test_dynamic_world_field_does_not_self_authorise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _clock, store = self.store(root)
            d = desc("descriptor:test:dynamic", [obj("place", "PLACE")], authority=DYNAMIC_WORLD)
            with self.assertRaises(AuthorityRequired):
                store.instantiate(d, command_id="no-grant", accepted_source_state_ref=SOURCE, effective_time=T0)
            result = store.instantiate(d, command_id="with-grant", accepted_source_state_ref=SOURCE,
                                       effective_time=T0, permitted_authority_classes={DYNAMIC_WORLD})
            self.assertEqual(result["canonical_status"], "DYNAMIC_WORLD")

    def test_persistence_idempotency_command_conflict_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); clock, store = self.store(root); d = cross_domain()
            first = store.instantiate(d, command_id="cross-1", accepted_source_state_ref=SOURCE, effective_time=T0)
            before = store.journal_path.read_bytes()
            self.assertEqual(first, store.instantiate(d, command_id="cross-1", accepted_source_state_ref=SOURCE, effective_time=T0))
            self.assertEqual(before, store.journal_path.read_bytes())
            changed = cross_domain(); changed["objects"][0]["properties"]["name"] = "Changed"
            with self.assertRaises(ConflictError):
                store.instantiate(changed, command_id="cross-1", accepted_source_state_ref=SOURCE, effective_time=T0)
            live = store.current_state(); del store; del clock
            restarted = DescriptorWorldStore(root / "descriptor", CentralAlderniaClock(root / "clock.json"))
            self.assertEqual(restarted.current_state(), live)
            self.assertEqual(DescriptorWorldStore.replay(restarted.journal_path), live)

    def test_journal_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _clock, store = self.store(root)
            d = desc("descriptor:test:integrity", [{
                "key": "place", "archetype": "PLACE", "properties": {"name": "Source Place"},
                "initial_state": {}, "relationships": [], "resolution_level": 0,
                "provenance": [prov("archetype", SOURCE_FACT, "Royal Palace source"),
                               prov("properties.name", SOURCE_FACT, "Royal Palace source")],
            }])
            store.instantiate(d, command_id="integrity", accepted_source_state_ref=SOURCE, effective_time=T0)
            event = json.loads(store.journal_path.read_text(encoding="utf-8").splitlines()[0])
            event["objects"][0]["source_object"]["properties"]["name"] = "Tampered"
            store.journal_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with self.assertRaises(ReplayError): DescriptorWorldStore.replay(store.journal_path)

    def test_progressive_resolution_preserves_parent_identity_and_replays(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _clock, store = self.store(root)
            parent = desc("descriptor:test:district", [
                obj("district", "HOUSING_ESTATE", requested_id="test:housing:01", properties={"aggregate_units": 100})
            ])
            store.instantiate(parent, command_id="district", accepted_source_state_ref=SOURCE, effective_time=T0)
            before = store.get_object("test:housing:01")
            refinement = desc("descriptor:test:district-refine", [
                obj("block", "BUILDING", requested_id="test:building:block-01", parent_id="test:housing:01", resolution=1,
                    relationships=[{"type": "PART_OF", "target": "test:housing:01"}])
            ])
            result = store.refine(parent_id="test:housing:01", descriptor=refinement, command_id="refine-1",
                                  accepted_source_state_ref=SOURCE, effective_time=T1)
            after = store.get_object("test:housing:01")
            self.assertTrue(result["parent_identity_stable"])
            self.assertEqual(before["id"], after["id"]); self.assertEqual(before["source_object"], after["source_object"])
            self.assertEqual(after["resolution_level"], 1)
            self.assertEqual(DescriptorWorldStore.replay(store.journal_path), store.current_state())

    def test_descriptor_road_enters_proved_causal_runtime_and_replays(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); clock = CentralAlderniaClock(root / "clock.json")
            store = DescriptorWorldStore(root / "descriptor", clock); world = WorldRuntime(root / "world", clock)
            external = {PLACE_ST_BRIGIDS: {
                "id": PLACE_ST_BRIGIDS, "key": "st-brigids", "family": PLACE_LAND, "archetype": "PLACE",
                "properties": {"name": "St Brigid's Isle"}, "initial_state": {}, "relationships": [],
                "resolution_level": 0,
                "provenance": [prov("archetype", SOURCE_FACT, "Royal Palace — Aldernia Public World Blueprint"),
                               prov("properties.name", SOURCE_FACT, "Royal Palace — Aldernia Public World Blueprint")],
            }}
            d = desc("descriptor:test:causal-road", [
                obj("destination", "PLACE", requested_id="test:place:descriptor-destination",
                    properties={"name": "Descriptor Test Destination"}),
                obj("road", "ROAD", requested_id="test:road:causal", properties={"bidirectional": True},
                    relationships=[{"type": "CONNECTS", "target": PLACE_ST_BRIGIDS}, {"type": "CONNECTS", "target": "destination"}]),
            ])
            result = store.instantiate(d, command_id="causal-descriptor", accepted_source_state_ref=SOURCE,
                                       effective_time=T0, external_objects=external)
            materialise_transport_objects_into_world(store=store, runtime=world, object_ids=result["object_ids"],
                                                     effective_time=T0, command_prefix="materialise")
            destination = "test:place:descriptor-destination"
            self.assertTrue(world.can_depart(PLACE_ST_BRIGIDS, destination, TEST_FERRY_ID))
            world.depart(origin=PLACE_ST_BRIGIDS, destination=destination, effective_time=T0, arrival_time=T1,
                         command_id="descriptor-road-journey", entity_id=TEST_FERRY_ID)
            world.process_due(now=T1)
            live = world.current_state()
            self.assertEqual(live["entities"][TEST_FERRY_ID]["location"], destination)
            self.assertEqual(WorldRuntime.replay(root / "world" / "world-journal.jsonl"), live)
            restarted = WorldRuntime(root / "world", clock)
            self.assertEqual(restarted.current_state(), live)


if __name__ == "__main__":
    unittest.main()
