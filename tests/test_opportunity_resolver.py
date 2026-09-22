import json
import unittest

from aldernia_runtime.opportunity_resolver import (
    AuthorityError,
    Candidate,
    CapabilityProfile,
    ImplementationOption,
    Resolver,
    ResolverState,
    SourceFact,
    StateClass,
    discover_candidates,
    stable_hash,
)


PROFILES = {
    "COS-SEL-001": CapabilityProfile("COS-SEL-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True, frozenset({"SELECT_METHOD"})),
    "COS-AUTH-001": CapabilityProfile("COS-AUTH-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True, frozenset({"CHECK_AUTHORITY"})),
    "COS-BUS-001": CapabilityProfile("COS-BUS-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True, frozenset({"TRANSPORT_WORK"})),
    "COS-WORKER-001": CapabilityProfile("COS-WORKER-001", "Computer of Series", frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}), True, frozenset({"EXECUTE_INTERNAL"})),
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

    def test_missing_plugin_falls_back_to_bounded_small_code(self):
        c = candidate(
            "NO-PLUGIN",
            required_profiles=("PLUGIN-NOT-INSTALLED",),
            required_operations=frozenset({"PARSE_RECORDS"}),
            implementation_options=(
                ImplementationOption(
                    route="SMALL_CODE",
                    operations=frozenset({"PARSE_RECORDS"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)
        self.assertEqual(d.implementation_route, "SMALL_CODE")
        self.assertEqual(d.implementation_profiles, ())

    def test_provider_loss_can_fall_back_to_non_model_transformation(self):
        c = candidate(
            "MODEL-FALLBACK",
            needs_model=True,
            provider_available=False,
            required_operations=frozenset({"NORMALIZE_RECORDS"}),
            implementation_options=(
                ImplementationOption(
                    route="TRANSFORM",
                    operations=frozenset({"NORMALIZE_RECORDS"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)
        self.assertEqual(d.implementation_route, "TRANSFORM")

    def test_budget_loss_can_fall_back_to_deterministic_decomposition(self):
        c = candidate(
            "BUDGET-FALLBACK",
            needs_model=True,
            budget_available=False,
            required_operations=frozenset({"PARTITION_WORK"}),
            implementation_options=(
                ImplementationOption(
                    route="DECOMPOSE",
                    operations=frozenset({"PARTITION_WORK"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)
        self.assertEqual(d.implementation_route, "DECOMPOSE")

    def test_available_registered_operation_reuse_replaces_missing_preferred_profile(self):
        profiles = dict(PROFILES)
        profiles["LOCAL-PARSER"] = CapabilityProfile(
            "LOCAL-PARSER",
            "Computer of Series",
            frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
            True,
            frozenset({"PARSE_RECORDS"}),
        )
        resolver = Resolver(profiles)
        c = candidate(
            "REUSE-ALT",
            required_profiles=("MISSING-SAAS",),
            required_operations=frozenset({"PARSE_RECORDS"}),
        )
        d = resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)
        self.assertEqual(d.implementation_route, "REUSE")
        self.assertEqual(d.implementation_profiles, ("LOCAL-PARSER",))

    def test_smallest_complete_registered_coalition_is_synthesized(self):
        profiles = dict(PROFILES)
        profiles["A"] = CapabilityProfile(
            "A", "Computer of Series",
            frozenset({"INTERNAL_DERIVED_STATE_WRITE"}), True,
            frozenset({"READ_BATCH"}),
        )
        profiles["B"] = CapabilityProfile(
            "B", "Computer of Series",
            frozenset({"INTERNAL_DERIVED_STATE_WRITE"}), True,
            frozenset({"WRITE_CHECKPOINT"}),
        )
        resolver = Resolver(profiles)
        c = candidate(
            "COMPOSE-ALT",
            required_profiles=("MISSING-PLATFORM",),
            required_operations=frozenset({"READ_BATCH", "WRITE_CHECKPOINT"}),
        )
        d = resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)
        self.assertEqual(d.implementation_route, "COMPOSE")
        self.assertEqual(set(d.implementation_profiles), {"A", "B"})

    def test_safe_adapter_and_workaround_routes_remain_available(self):
        for route in ("ADAPTER", "WORKAROUND"):
            with self.subTest(route=route):
                c = candidate(
                    route,
                    required_profiles=("MISSING",),
                    required_operations=frozenset({"BRIDGE_FORMAT"}),
                    implementation_options=(
                        ImplementationOption(
                            route=route,
                            operations=frozenset({"BRIDGE_FORMAT"}),
                            effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                        ),
                    ),
                )
                self.assertEqual(self.resolver.classify(c).state_class, StateClass.ACTIONABLE_NOW)

    def test_security_bypassing_workaround_is_rejected(self):
        c = candidate(
            "BYPASS",
            required_profiles=("MISSING",),
            required_operations=frozenset({"READ_PROTECTED_DATA"}),
            implementation_options=(
                ImplementationOption(
                    route="WORKAROUND",
                    operations=frozenset({"READ_PROTECTED_DATA"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                    bypasses_security=True,
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)
        self.assertIn("bypass authentication", d.reasons[0])

    def test_hand_built_high_risk_primitive_is_rejected(self):
        c = candidate(
            "HAND-ROLLED-CRYPTO",
            required_profiles=("MISSING",),
            required_operations=frozenset({"CRYPTOGRAPHY"}),
            implementation_options=(
                ImplementationOption(
                    route="SMALL_CODE",
                    operations=frozenset({"CRYPTOGRAPHY"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                    uses_maintained_standard=False,
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)
        self.assertIn("may not be hand-built", d.reasons[0])

    def test_maintained_standard_can_satisfy_high_risk_primitive(self):
        c = candidate(
            "STANDARD-CRYPTO",
            required_profiles=("MISSING",),
            required_operations=frozenset({"CRYPTOGRAPHY"}),
            implementation_options=(
                ImplementationOption(
                    route="SMALL_CODE",
                    operations=frozenset({"CRYPTOGRAPHY"}),
                    effects=frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
                    uses_maintained_standard=True,
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.ACTIONABLE_NOW)

    def test_synthesis_route_cannot_widen_effects(self):
        c = candidate(
            "WIDEN",
            required_profiles=("MISSING",),
            required_operations=frozenset({"PARSE_RECORDS"}),
            implementation_options=(
                ImplementationOption(
                    route="SMALL_CODE",
                    operations=frozenset({"PARSE_RECORDS"}),
                    effects=frozenset({"EXTERNAL_SEND"}),
                ),
            ),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)
        self.assertIn("widen requested effects", d.reasons[0])

    def test_irreducible_missing_primitive_blocks_after_synthesis(self):
        c = candidate(
            "IRREDUCIBLE",
            required_profiles=("MISSING-HARDWARE",),
            required_operations=frozenset({"PHYSICAL_HARDWARE_ATTESTATION"}),
        )
        d = self.resolver.classify(c)
        self.assertEqual(d.state_class, StateClass.BLOCKED)
        self.assertIn("implementation synthesis exhausted authorised routes", d.reasons[0])
        self.assertIn("MISSING-HARDWARE", d.reasons[0])

    def test_selected_fallback_profiles_can_be_composed_without_preferred_profile(self):
        profiles = dict(PROFILES)
        profiles["LOCAL-PARSER"] = CapabilityProfile(
            "LOCAL-PARSER",
            "Computer of Series",
            frozenset({"INTERNAL_DERIVED_STATE_WRITE"}),
            True,
            frozenset({"PARSE_RECORDS"}),
        )
        resolver = Resolver(profiles)
        c = candidate(
            "COMPOSE-DECISION",
            required_profiles=("MISSING",),
            required_operations=frozenset({"PARSE_RECORDS"}),
        )
        d = resolver.classify(c)
        chosen = resolver.compose_profiles(
            c,
            inherited_effects=frozenset({"INTERNAL_READ", "INTERNAL_DERIVED_STATE_WRITE"}),
            decision=d,
        )
        self.assertEqual(tuple(p.profile_id for p in chosen), ("LOCAL-PARSER",))

    def test_candidate_from_fact_keeps_operations_separate_from_effects(self):
        fact = SourceFact(
            "f-op",
            "Drive:X",
            "Owner",
            "Transform records",
            True,
            "h",
            "AUTHORISED_INTERNAL_WORK",
            objective_id="OBJ-OP",
            authority_ref="AUTH",
            metadata={
                "required_profiles": ["MISSING"],
                "required_operations": ["NORMALIZE_RECORDS"],
                "requested_effects": ["INTERNAL_DERIVED_STATE_WRITE"],
                "implementation_options": [
                    {
                        "route": "TRANSFORM",
                        "operations": ["NORMALIZE_RECORDS"],
                        "effects": ["INTERNAL_DERIVED_STATE_WRITE"],
                    }
                ],
            },
        )
        c = discover_candidates([fact])[0]
        self.assertEqual(c.required_operations, frozenset({"NORMALIZE_RECORDS"}))
        self.assertEqual(c.requested_effects, frozenset({"INTERNAL_DERIVED_STATE_WRITE"}))
        self.assertEqual(self.resolver.classify(c).implementation_route, "TRANSFORM")

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

    def test_unknown_fact_kind_creates_no_work(self):
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
