import copy
import json
from pathlib import Path
import tempfile
import unittest

from aldernia_runtime.private_github_canonical import (
    PrivateGitHubCanonicalError,
    TRANSPORT,
    canonical_facts_from_record,
    load_manifest,
    validate_manifest,
    validate_programme_board,
    validate_repository_context,
)


OUTCOME = (
    "AUTHORISED — STAGE-4 LIVE CANONICAL DELTA ACK / MACHINE "
    "kind=AUTHORISED_INTERNAL_WORK; candidate_id=PB-E28-LIVE-DELTA-ACK; "
    "profile=COS-SEL-001,COS-BUS-001,COS-WORKER-001; "
    "execution_class=COS-WORKER-001::CANONICAL_DELTA_ACK; "
    "effect=INTERNAL_DERIVED_STATE_WRITE; reversible=true; model=false; consequence=NONE"
)


def record():
    return {
        "schema_version": 1,
        "record_id": "ROYAL_PROGRAMME_BOARD_MACHINE",
        "cutover_state": "ACTIVE",
        "rows": [
            {
                "entry_id": "E-28",
                "mission": "CROWN INFRASTRUCTURE / SELF-PROGRESSION STAGE 4 AUTHORITY",
                "narrative": "bounded",
                "outcome": OUTCOME,
            }
        ],
    }


class PrivateGitHubCanonicalTests(unittest.TestCase):
    def test_repository_must_be_private(self):
        with self.assertRaises(PrivateGitHubCanonicalError):
            validate_repository_context("Wally189/aldernia-runtime", "public")
        validate_repository_context("Wally189/aldernia-runtime", "private")

    def test_repository_identity_can_be_pinned(self):
        with self.assertRaises(PrivateGitHubCanonicalError):
            validate_repository_context(
                "Wally189/wrong",
                "private",
                expected_repository="Wally189/aldernia-runtime",
            )

    def test_review_template_cannot_be_activated_implicitly(self):
        manifest = {
            "schema_version": 1,
            "cutover_state": "REVIEW_ONLY",
            "required_visibility": "private",
            "authoritative_home_rule": "EXACTLY_ONE_LIVE_HOME_PER_RECORD",
            "records": [
                {
                    "record_id": "X",
                    "owner": "Computer of Series",
                    "current_home": "drive:x",
                    "private_home": "records/x.json",
                    "after_cutover_old_home": "FROZEN_PROVENANCE",
                }
            ],
        }
        with self.assertRaises(PrivateGitHubCanonicalError):
            validate_manifest(manifest, require_active=True)

    def test_manifest_rejects_duplicate_private_home(self):
        manifest = {
            "schema_version": 1,
            "cutover_state": "ACTIVE",
            "required_visibility": "private",
            "authoritative_home_rule": "EXACTLY_ONE_LIVE_HOME_PER_RECORD",
            "records": [
                {
                    "record_id": "A",
                    "owner": "A",
                    "current_home": "drive:a",
                    "private_home": "records/same.json",
                    "after_cutover_old_home": "FROZEN_PROVENANCE",
                },
                {
                    "record_id": "B",
                    "owner": "B",
                    "current_home": "drive:b",
                    "private_home": "records/same.json",
                    "after_cutover_old_home": "FROZEN_PROVENANCE",
                },
            ],
        }
        with self.assertRaises(PrivateGitHubCanonicalError):
            validate_manifest(manifest)

    def test_template_manifest_file_is_review_only(self):
        path = Path("templates/private-runtime/record-manifest.template.json")
        manifest = load_manifest(path)
        self.assertEqual(manifest["cutover_state"], "REVIEW_ONLY")
        with self.assertRaises(PrivateGitHubCanonicalError):
            load_manifest(path, require_active=True)

    def test_private_transport_preserves_exact_contract(self):
        facts = canonical_facts_from_record(
            record(),
            "Wally189/aldernia-runtime",
            "a" * 40,
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].metadata["canonical_transport"], TRANSPORT)
        self.assertEqual(facts[0].objective_id, "PB-E28-LIVE-DELTA-ACK")

    def test_unrelated_commit_churn_does_not_retrigger_evidence(self):
        a = canonical_facts_from_record(record(), "Wally189/aldernia-runtime", "a" * 40)[0]
        b = canonical_facts_from_record(record(), "Wally189/aldernia-runtime", "b" * 40)[0]
        self.assertEqual(a.evidence_hash, b.evidence_hash)
        ack = {a.objective_id: {"evidence_hash": a.evidence_hash}}
        after = canonical_facts_from_record(
            record(), "Wally189/aldernia-runtime", "b" * 40, acknowledged=ack
        )[0]
        self.assertEqual(after.kind, "COMPLETED_NO_CONSEQUENCE")

    def test_material_row_change_retriggers(self):
        before = canonical_facts_from_record(record(), "Wally189/aldernia-runtime", "a" * 40)[0]
        changed = copy.deepcopy(record())
        changed["rows"][0]["narrative"] = "bounded material change"
        after = canonical_facts_from_record(changed, "Wally189/aldernia-runtime", "b" * 40)[0]
        self.assertNotEqual(before.evidence_hash, after.evidence_hash)

    def test_authority_widening_is_not_actionable(self):
        widened = record()
        widened["rows"][0]["outcome"] = OUTCOME.replace(
            "INTERNAL_DERIVED_STATE_WRITE", "PUBLICATION"
        )
        self.assertEqual(
            canonical_facts_from_record(widened, "Wally189/aldernia-runtime", "a" * 40),
            (),
        )

    def test_programme_board_requires_explicit_active_cutover(self):
        review = record()
        review["cutover_state"] = "REVIEW_ONLY"
        with self.assertRaises(PrivateGitHubCanonicalError):
            validate_programme_board(review, require_active=True)


if __name__ == "__main__":
    unittest.main()
