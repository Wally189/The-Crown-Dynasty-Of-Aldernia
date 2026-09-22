import unittest
from unittest.mock import patch

from aldernia_runtime.canonical_programme_board import (
    ALLOWED_EXECUTION_CLASS,
    CanonicalAccessUnavailable,
    build_packet_from_values,
    canonical_facts_from_packet,
    fetch_live_packet,
    machine_rows,
    parse_machine_contract,
)
from aldernia_runtime.opportunity_resolver import StateClass, Resolver, CapabilityProfile, discover_candidates


OUTCOME = (
    "AUTHORISED — STAGE-4 LIVE CANONICAL DELTA ACK / MACHINE "
    "kind=AUTHORISED_INTERNAL_WORK; candidate_id=PB-E28-LIVE-DELTA-ACK; "
    "profile=COS-SEL-001,COS-BUS-001,COS-WORKER-001; "
    "execution_class=COS-WORKER-001::CANONICAL_DELTA_ACK; "
    "effect=INTERNAL_DERIVED_STATE_WRITE; reversible=true; model=false; consequence=NONE"
)


def packet(modified="2026-09-22T16:49:08.476Z"):
    values = [
        ["E-27", "old", "old", "PASS"],
        ["E-28", "CROWN INFRASTRUCTURE / SELF-PROGRESSION STAGE 4 AUTHORITY", "bounded", OUTCOME],
    ]
    return build_packet_from_values(
        values,
        modified_time=modified,
        transport="CONNECTED_DRIVE_BRIDGE",
        start_row=154,
    )


class CanonicalProgrammeBoardTests(unittest.TestCase):
    def test_packet_filters_to_machine_rows(self):
        p = packet()
        self.assertEqual(len(p["rows"]), 1)
        self.assertEqual(p["rows"][0]["entry_id"], "E-28")
        self.assertEqual(p["rows"][0]["source_row"], 155)

    def test_exact_contract_parses(self):
        c = parse_machine_contract(OUTCOME)
        self.assertIsNotNone(c)
        self.assertEqual(c["execution_class"], ALLOWED_EXECUTION_CLASS)
        self.assertEqual(c["candidate_id"], "PB-E28-LIVE-DELTA-ACK")

    def test_malformed_or_widened_contract_is_not_actionable(self):
        bad = OUTCOME.replace("INTERNAL_DERIVED_STATE_WRITE", "PUBLICATION")
        p = build_packet_from_values(
            [["E-28", "mission", "bounded", bad]],
            modified_time="x",
            transport="CONNECTED_DRIVE_BRIDGE",
            start_row=155,
        )
        self.assertEqual(canonical_facts_from_packet(p), ())

    def test_canonical_fact_is_actionable_without_model_provider(self):
        facts = canonical_facts_from_packet(packet())
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        candidates = discover_candidates(facts)
        profiles = {
            pid: CapabilityProfile(
                pid,
                "Computer of Series",
                frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
                True,
            )
            for pid in ("COS-SEL-001", "COS-BUS-001", "COS-WORKER-001")
        }
        result = Resolver(profiles).resolve(candidates)
        self.assertEqual(result.selected_candidate_id, "PB-E28-LIVE-DELTA-ACK")
        self.assertEqual(result.decisions[0].state_class, StateClass.ACTIONABLE_NOW)

    def test_acknowledged_same_evidence_becomes_no_action(self):
        fact = canonical_facts_from_packet(packet())[0]
        ack = {fact.objective_id: {"evidence_hash": fact.evidence_hash}}
        after = canonical_facts_from_packet(packet(), acknowledged=ack)[0]
        self.assertEqual(after.kind, "COMPLETED_NO_CONSEQUENCE")

    def test_unrelated_file_modified_time_does_not_change_row_evidence(self):
        a = machine_rows(packet("a"))[0]["evidence_hash"]
        b = machine_rows(packet("b"))[0]["evidence_hash"]
        self.assertEqual(a, b)

    def test_missing_runtime_token_fails_closed(self):
        with self.assertRaises(CanonicalAccessUnavailable):
            fetch_live_packet("")

    def test_live_fetch_uses_existing_token_without_provider_mutation(self):
        metadata = {"id": "x", "modifiedTime": "2026-09-22T16:49:08.476Z"}
        values = {"values": [["E-28", "mission", "bounded", OUTCOME]]}
        with patch(
            "aldernia_runtime.canonical_programme_board._http_json",
            side_effect=[metadata, values],
        ):
            p = fetch_live_packet("existing-token")
        self.assertEqual(p["transport"], "LIVE_GOOGLE_DRIVE_API")
        self.assertEqual(len(p["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
