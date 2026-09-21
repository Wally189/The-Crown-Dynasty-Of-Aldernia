from __future__ import annotations

import json
from pathlib import Path
import unittest

from operating_spine.validate import ROOT, SpineValidationError, validate_projection


class OperatingSpinePhase1Tests(unittest.TestCase):
    def test_projection_validates_end_to_end(self):
        counts = validate_projection()
        self.assertGreaterEqual(counts["owners"], 7)
        self.assertGreaterEqual(counts["sources"], 10)
        self.assertGreaterEqual(counts["state_objects"], 8)
        self.assertGreaterEqual(counts["events"], 1)

    def test_projection_is_explicitly_non_authoritative(self):
        manifest = json.loads((ROOT / "projection" / "manifest.json").read_text(encoding="utf-8"))
        owners = json.loads((ROOT / "projection" / "canonical-owners.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["projection_authoritative"])
        self.assertFalse(owners["projection_authoritative"])

    def test_canonical_state_pointers_are_drive_only(self):
        states = json.loads((ROOT / "projection" / "state-objects.json").read_text(encoding="utf-8"))
        self.assertTrue(states)
        for state in states:
            self.assertTrue(state["canonical_record_ref"].startswith("drive:"))

    def test_exact_live_release_is_frozen_into_phase1_snapshot(self):
        manifest = json.loads((ROOT / "projection" / "manifest.json").read_text(encoding="utf-8"))
        release = manifest["accepted_public_release"]
        self.assertEqual(release["build_id"], "ALD-CROWN-FOUNDING-09")
        self.assertEqual(release["commit_sha"], "81f8af9942881738977b0913eed2f96d1c0c78d2")
        self.assertEqual(release["pages_run"], 35560455677)
        self.assertEqual(release["public_smoke_run"], 35560456216)
        self.assertEqual(release["browser_assurance_run"], 35560456252)

    def test_carol_is_support_only_not_owner(self):
        owners = json.loads((ROOT / "projection" / "canonical-owners.json").read_text(encoding="utf-8"))
        owner_names = {item["canonical_name"] for item in owners["owners"]}
        self.assertNotIn("House of Carol", owner_names)
        carol = next(item for item in owners["non_owners"] if item["system"] == "House of Carol")
        self.assertIn("No Aldernia governance or content ownership", carol["reason"])

    def test_release_event_resolves_to_current_release(self):
        events = json.loads((ROOT / "projection" / "events.json").read_text(encoding="utf-8"))
        states = json.loads((ROOT / "projection" / "state-objects.json").read_text(encoding="utf-8"))
        ids = {state["entity_id"] for state in states}
        release_event = next(event for event in events if event["event_type"] == "accepted_release")
        self.assertEqual(release_event["decision"], "ACCEPTED_CHANGE")
        self.assertEqual(release_event["public_consequence"], "PUBLISHED")
        self.assertTrue(set(release_event["affected_entity_ids"]).issubset(ids))
        self.assertEqual(release_event["transport_state"], "ACKED")

    def test_aeo_stays_armed_until_one_shot_acceptance(self):
        states = json.loads((ROOT / "projection" / "state-objects.json").read_text(encoding="utf-8"))
        aeo = next(state for state in states if state["entity_id"] == "aldernia:experiment:aeo-def-001")
        self.assertIn("ARMED", aeo["status"])
        self.assertIn("NOT YET ACTIVE", aeo["status"])

    def test_jekyll_excludes_operating_spine_from_public_pages(self):
        config = (ROOT.parent / "_config.yml").read_text(encoding="utf-8")
        self.assertIn("exclude:", config)
        self.assertIn("- operating_spine", config)


if __name__ == "__main__":
    unittest.main()
