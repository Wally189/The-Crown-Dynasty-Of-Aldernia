import json
import unittest

from aldernia_runtime.opportunity_resolver import (
    AuthorityError,
    Candidate,
    CapabilityProfile,
    Resolver,
    ResolverState,
    SourceFact,
    StateClass,
    discover_candidates,
    stable_hash,
)


PROFILES = {
    "COS-SEL-001": CapabilityProfile("COS-SEL-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True),
    "COS-AUTH-001": CapabilityProfile("COS-AUTH-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True),
    "COS-BUS-001": CapabilityProfile("COS-BUS-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True),
    "COS-WORKER-001": CapabilityProfile("COS-WORKER-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True),
}


def candidate(cid, **kwargs):
    base = dict(
        candidate_id=cid,
        title=cid,
        owner="Royal Palace / Computer of Series",
        source_refs=["source"],
        evidence_hash=stable_hash(cid),
        authority_ref="Crown Commission 2026-09-21",
        current=True,
        accepted_objective=True,
        requested_effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
        required_profiles=("COS-AUTH-001",),
    )
    base.update(kwargs)
    return Candidate(**base)


class Stage1ResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = Resolver(PROFILES, causal_depth_limit=2)

    def test_unchanged_blocker_is_suppressed_not_selected(self):
        blocker_hash = stable_hash({"status": "PROVIDER_ACCESS_NOT_CONFIGURED"})
        first = candidate("KQ-009", blocked=True, blocker_id="google-wif", blocker_evidence_hash=blocker_hash, owner_priority="CRITICAL")
        first_resolution = self.resolver.resolve([first])
        state = ResolverState.from_json(first_resolution.next_state)
        second = candidate("KQ-009", blocked=True, blocker_id="google-wif", blocker_evidence_hash=blocker_hash, owner_priority="CRITICAL")
        useful = candidate("USEFUL", enabling_value="HIGH")
        second_resolution = self.resolver.resolve([second, useful], state)
        self.assertEqual(second_resolution.selected_candidate_id, "USEFUL")
        kq = next(d for d in second_resolution.decisions if d.candidate_id == "KQ-009")
        self.assertTrue(kq.unchanged)
        self.assertIn("suppress", kq.reasons[0])

    def test_new_blocker_evidence_is_reconsidered(self):
        old = candidate("KQ-009", blocked=True, blocker_id="google-wif", blocker_evidence_hash="old")
        state = ResolverState.from_json(self.resolver.resolve([old]).next_state)
        new = candidate("KQ-009", blocked=True, blocker_id="google-wif", blocker_evidence_hash="new")
        decision = self.resolver.classify(new, state)
        self.assertFalse(decision.unchanged)
        self.assertEqual(decision.state_class, StateClass.BLOCKED)

    def test_conflicting_actionables_stop_only_the_conflict_set(self):
        a = candidate("A", conflicts_with=("B",), urgency="HIGH")
        b = candidate("B", conflicts_with=("A",), urgency="MEDIUM")
        r = self.resolver.resolve([a, b])
        self.assertIsNone(r.selected_candidate_id)
        self.assertEqual(r.termination, "STOP_CONFLICTING_ACTIONABLES")
        self.assertEqual(set(r.conflict_candidate_ids), {"A", "B"})

    def test_unrelated_action_can_execute_while_other_candidate_is_stopped(self):
        stopped = candidate("STOPPED", specialist_stop=True, owner_priority="CRITICAL")
        useful = candidate("USEFUL", enabling_value="HIGH")
        r = self.resolver.resolve([stopped, useful])
        self.assertEqual(r.selected_candidate_id, "USEFUL")

    def test_reserved_decision_never_executes(self):
        d = self.resolver.classify(candidate("RESERVED", reserved_for_king=True))
        self.assertEqual(d.state_class, StateClass.RESERVED_FOR_KING)

    def test_external_effect_without_exact_authority_never_executes(self):
        d = self.resolver.classify(candidate("EXT", external_effect_required=True, external_effect_authorised=False))
        self.assertEqual(d.state_class, StateClass.EXTERNAL_NOT_AUTHORISED)

    def test_provider_loss_blocks_model_work_only(self):
        model = candidate("MODEL", needs_model=True, provider_available=False, owner_priority="CRITICAL")
        deterministic = candidate("DET")
        r = self.resolver.resolve([model, deterministic])
        self.assertEqual(r.selected_candidate_id, "DET")

    def test_budget_stop_blocks_model_work_only(self):
        model = candidate("MODEL", needs_model=True, budget_available=False, owner_priority="CRITICAL")
        deterministic = candidate("DET")
        r = self.resolver.resolve([model, deterministic])
        self.assertEqual(r.selected_candidate_id, "DET")

    def test_candidate_cannot_widen_inherited_effects(self):
        evil = candidate("EVIL", requested_effects=frozenset({"EXTERNAL_SEND"}))
        with self.assertRaises(AuthorityError):
            self.resolver.compose_profiles(evil, inherited_effects=frozenset({"INTERNAL_READ"}))

    def test_profile_cannot_widen_inherited_effects(self):
        c = candidate("X", required_profiles=("COS-WORKER-001",))
        with self.assertRaises(AuthorityError):
            self.resolver.compose_profiles(c, inherited_effects=frozenset({"INTERNAL_READ"}))

    def test_unknown_profile_blocks_candidate(self):
        c = candidate("UNKNOWN", required_profiles=("NOT-REGISTERED",))
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)

    def test_causal_depth_limit_stops_runaway_continuation(self):
        c = candidate("DEEP", causal_depth=3)
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)

    def test_hallucinated_problem_without_authority_is_idea_only(self):
        c = candidate("HALLUCINATED", authority_ref=None, accepted_objective=False, urgency="CRITICAL")
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.IDEA_ONLY)

    def test_historical_record_never_executes(self):
        d = self.resolver.classify(candidate("OLD", current=False))
        self.assertEqual(d.state_class, StateClass.HISTORICAL_SUPERSEDED)

    def test_already_satisfied_is_no_action(self):
        d = self.resolver.classify(candidate("DONE", already_satisfied=True))
        self.assertEqual(d.state_class, StateClass.NO_ACTION)

    def test_waiting_trigger_is_not_competitive(self):
        wait = candidate("WAIT", real_world_wait=True, owner_priority="CRITICAL")
        useful = candidate("USEFUL")
        r = self.resolver.resolve([wait, useful])
        self.assertEqual(r.selected_candidate_id, "USEFUL")

    def test_lexicographic_priority_allows_new_high_priority_work_to_overtake_fifo(self):
        old = candidate("OLD", owner_priority="MEDIUM", urgency="LOW")
        new = candidate("NEW", owner_priority="HIGH", urgency="HIGH")
        r = self.resolver.resolve([old, new])
        self.assertEqual(r.selected_candidate_id, "NEW")

    def test_no_action_is_successful_termination(self):
        r = self.resolver.resolve([candidate("DONE", already_satisfied=True)])
        self.assertEqual(r.termination, "NO_ACTION")
        self.assertIsNone(r.selected_candidate_id)

    def test_duplicate_current_fact_with_conflicting_evidence_does_not_last_write_win(self):
        facts = [
            SourceFact("f1", "Drive:A", "Owner", "Do work", True, "hash-a", "AUTHORISED_INTERNAL_WORK", objective_id="OBJ", authority_ref="AUTH"),
            SourceFact("f2", "Drive:B", "Owner", "Do work", True, "hash-b", "AUTHORISED_INTERNAL_WORK", objective_id="OBJ", authority_ref="AUTH"),
        ]
        candidates = discover_candidates(facts)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].idea_only)
        self.assertFalse(candidates[0].accepted_objective)

    def test_unknown_fact_kind_creates_no_workhself):
        facts = [SourceFact("f1", "Drive:A", "Owner", "Unknown", True, "h", "SOMETHING_NEW", objective_id="OBJ", authority_ref="AUTH")]
        self.assertEqual(discover_candidates(facts), ())

    def test_real_current_fixture_selects_stage1_not_kq009(self):
        blocker_hash = stable_hash({
            "main_sha": "85cf82645f920ddd477138f48fc674fbea23c531",
            "runtime_status": "PROVIDER_ACCESS_NOT_CONFIGURED",
            "pending_event": "dynasty.heartbeat:2026-09-21T19:00",
        })
        facts = [
            SourceFact(
                "kq009",
                "Drive:KQ-009/GitHub:runtime-health",
                "Computer of Series",
                "Complete provider administration",
                True,
                blocker_hash,
                "BLOCKER",
                status="PROVIDER_ACCESS_NOT_CONFIGURED",
                objective_id="KQ-009",
                authority_ref="KQ-009 existing provider-administration authority",
                dependency_id="google-wif-drive",
                next_trigger="PROVIDER_STATE_CHANGE",
                metadata={"owner_priority": "HIGH", "urgency": "HIGH", "enabling_value": "HIGH"},
            ),
            SourceFact(
                "e22",
                "Royal Programme Board:E-22",
                "Royal Palace",
                "Repeat E-22 without new evidence",
                True,
                stable_hash("E-22"),
                "COMPLETED_NO_CONSEQUENCE",
                objective_id="E-22-REPEAT",
                authority_ref="Crown infrastructure commission",
            ),
            SourceFact(
                "stage1",
                "Crown Commission:Self-Progressing Aldernia",
                "Royal Palace / Computer of Series",
                "Implement pure Opportunity Resolver beside existing runtime",
                True,
                stable_hash("stage1-current"),
                "AUTHORISED_INTERNAL_WORK",
                objective_id="SELF-PROGRESS-STAGE-1",
                authority_ref="Crown Commission — Computing Solutions for a Self-Progressing Aldernia",
                metadata={
                    "owner_priority": "HIGH",
                    "urgency": "MEDIUM",
                    "enabling_value": "CRITICAL",
                    "user_value": "HIGH",
                    "evidence_gain": "HIGH",
                    "confidence": "HIGH",
                    "required_profiles": ["COS-SEL-001", "COS-AUTH-001", "COS-BUS-001", "COS-WORKER-001"],
                    "requested_effects": ["INTERNAL_DERIVED_STATE_WRITE"],
                },
            ),
            SourceFact(
                "voice",
                "Scheduled Tasks:Website Voice",
                "House of Josie",
                "21:00 website voice review",
                True,
                stable_hash("voice-2026-09-21"),
                "WAITING_TRIGGER",
                objective_id="WEBSITE-VOICE-21H00",
                authority_ref="Scheduled Tasks Register",
                next_trigger="2026-09-21T21:00:00+01:00",
            ),
        ]
        candidates = discover_candidates(facts)
        first = self.resolver.resolve(candidates)
        state = ResolverState.from_json(first.next_state)
        second = self.resolver.resolve(candidates, state)
        self.assertEqual(second.selected_candidate_id, "SELF-PROGRESS-STAGE-1")
        kq = next(d for d in second.decisions if d.candidate_id == "KQ-009")
        self.assertTrue(kq.unchanged)

    def test_state_round_trip_is_deterministic(self):
        r = self.resolver.resolve([candidate("BLOCK", blocked=True, blocker_id="b", blocker_evidence_hash="h")])
        value = json.loads(json.dumps(r.next_state, sort_keys=True))
        state = ResolverState.from_json(value)
        self.assertEqual(state.to_json(), value)


if __name__ == "__main__":
    unittest.main()
