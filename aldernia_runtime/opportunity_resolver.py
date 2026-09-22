from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Iterable, Mapping, Sequence


class StateClass(str, Enum):
    ACTIONABLE_NOW = "ACTIONABLE_NOW"
    WAITING_REAL_WORLD_EVENT = "WAITING_ON_REAL_WORLD_EVENT"
    RESERVED_FOR_KING = "RESERVED_FOR_KING"
    EXTERNAL_NOT_AUTHORISED = "EXTERNAL_ACTION_NOT_AUTHORISED"
    BLOCKED = "BLOCKED"
    NO_ACTION = "NO_ACTION"
    IDEA_ONLY = "IDEA_ONLY"
    HISTORICAL_SUPERSEDED = "HISTORICAL_SUPERSEDED"


LEVEL = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
COST = {"NONE": 0, "LOW": 1, "MATERIAL": 2}


def stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SourceFact:
    """A normalized current-state fact emitted by an existing adapter.

    The resolver does not retrieve Drive/GitHub itself. Adapters remain responsible for
    source access and currentness. This makes the resolver pure, testable and unable to
    widen tool authority.
    """

    fact_id: str
    source_ref: str
    owner: str
    proposition: str
    current: bool
    evidence_hash: str
    kind: str
    status: str = ""
    objective_id: str | None = None
    authority_ref: str | None = None
    next_trigger: str | None = None
    dependency_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


SAFE_SYNTHESIS_ROUTES = frozenset(
    {
        "DIRECT",
        "REUSE",
        "COMPOSE",
        "TRANSFORM",
        "SMALL_CODE",
        "ADAPTER",
        "WORKAROUND",
        "DECOMPOSE",
        "INTERNAL_BUILD",
        "EXTERNAL_TOOL",
    }
)
HIGH_RISK_OPERATIONS = frozenset(
    {
        "CRYPTOGRAPHY",
        "AUTHENTICATION_PROTOCOL",
        "PAYMENT_SECURITY",
        "SAFETY_CRITICAL",
    }
)


@dataclass(frozen=True)
class CapabilityProfile:
    profile_id: str
    owner: str
    effects: frozenset[str]
    deterministic: bool
    available: bool = True
    operations: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ImplementationOption:
    route: str
    operations: frozenset[str]
    profile_ids: tuple[str, ...] = ()
    effects: frozenset[str] = field(default_factory=frozenset)
    requires_model: bool = False
    available: bool = True
    authorised: bool = True
    bypasses_security: bool = False
    uses_maintained_standard: bool = False
    note: str = ""


@dataclass
class Candidate:
    candidate_id: str
    title: str
    owner: str
    source_refs: list[str]
    evidence_hash: str
    authority_ref: str | None = None
    current: bool = True
    accepted_objective: bool = False
    evidence_sufficient: bool = True
    real_world_wait: bool = False
    reserved_for_king: bool = False
    external_effect_required: bool = False
    external_effect_authorised: bool = False
    blocked: bool = False
    blocker_id: str | None = None
    blocker_evidence_hash: str | None = None
    specialist_stop: bool = False
    idea_only: bool = False
    already_satisfied: bool = False
    owner_priority: str = "MEDIUM"
    urgency: str = "LOW"
    enabling_value: str = "LOW"
    evidence_gain: str = "LOW"
    user_value: str = "LOW"
    risk: str = "LOW"
    confidence: str = "MEDIUM"
    reversible: bool = True
    resource_cost: str = "NONE"
    required_profiles: tuple[str, ...] = ()
    required_operations: frozenset[str] = field(default_factory=frozenset)
    implementation_options: tuple[ImplementationOption, ...] = ()
    requested_effects: frozenset[str] = field(default_factory=frozenset)
    conflicts_with: tuple[str, ...] = ()
    needs_model: bool = False
    budget_available: bool = True
    provider_available: bool = True
    next_review_trigger: str | None = None
    causal_parent: str | None = None
    causal_depth: int = 0


@dataclass(frozen=True)
class BlockerMemory:
    blocker_id: str
    evidence_hash: str
    last_classification: str
    next_review_trigger: str | None = None


@dataclass(frozen=True)
class CandidateMemory:
    candidate_id: str
    evidence_hash: str
    terminal_classification: str
    next_review_trigger: str | None = None


@dataclass
class ResolverState:
    schema_version: int = 1
    blockers: dict[str, BlockerMemory] = field(default_factory=dict)
    candidates: dict[str, CandidateMemory] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "blockers": {k: asdict(v) for k, v in sorted(self.blockers.items())},
            "candidates": {k: asdict(v) for k, v in sorted(self.candidates.items())},
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object] | None) -> "ResolverState":
        if not value:
            return cls()
        blockers_raw = value.get("blockers") if isinstance(value, Mapping) else None
        candidates_raw = value.get("candidates") if isinstance(value, Mapping) else None
        blockers: dict[str, BlockerMemory] = {}
        candidates: dict[str, CandidateMemory] = {}
        if isinstance(blockers_raw, Mapping):
            for key, item in blockers_raw.items():
                if isinstance(item, Mapping):
                    blockers[str(key)] = BlockerMemory(
                        blocker_id=str(item.get("blocker_id") or key),
                        evidence_hash=str(item.get("evidence_hash") or ""),
                        last_classification=str(item.get("last_classification") or ""),
                        next_review_trigger=(
                            str(item.get("next_review_trigger"))
                            if item.get("next_review_trigger") is not None
                            else None
                        ),
                    )
        if isinstance(candidates_raw, Mapping):
            for key, item in candidates_raw.items():
                if isinstance(item, Mapping):
                    candidates[str(key)] = CandidateMemory(
                        candidate_id=str(item.get("candidate_id") or key),
                        evidence_hash=str(item.get("evidence_hash") or ""),
                        terminal_classification=str(item.get("terminal_classification") or ""),
                        next_review_trigger=(
                            str(item.get("next_review_trigger"))
                            if item.get("next_review_trigger") is not None
                            else None
                        ),
                    )
        return cls(schema_version=int(value.get("schema_version") or 1), blockers=blockers, candidates=candidates)


@dataclass(frozen=True)
class Decision:
    candidate_id: str
    state_class: StateClass
    reasons: tuple[str, ...]
    priority_key: tuple[int, ...] | None = None
    unchanged: bool = False
    implementation_route: str | None = None
    implementation_profiles: list[str] = field(default_factory=list)
    implementation_operations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Resolution:
    selected_candidate_id: str | None
    decisions: tuple[Decision, ...]
    conflict_candidate_ids: tuple[str, ...]
    termination: str
    next_state: Mapping[str, object]


class AuthorityError(RuntimeError):
    pass


class Resolver:
    """Pure governed next-action resolver.

    Invariant: candidates may narrow inherited authority but can never create/widen it.
    The resolver itself has no connector, model or execution authority.
    """

    def __init__(
        self,
        profiles: Mapping[str, CapabilityProfile],
        *,
        causal_depth_limit: int = 2,
    ) -> None:
        self.profiles = dict(profiles)
        self.causal_depth_limit = causal_depth_limit

    def _blocker_unchanged(self, c: Candidate, state: ResolverState) -> bool:
        if not c.blocker_id or not c.blocker_evidence_hash:
            return False
        prior = state.blockers.get(c.blocker_id)
        return bool(prior and prior.evidence_hash == c.blocker_evidence_hash)

    def _candidate_unchanged(self, c: Candidate, state: ResolverState) -> bool:
        prior = state.candidates.get(c.candidate_id)
        return bool(prior and prior.evidence_hash == c.evidence_hash)

    def _preferred_implementation(self, c: Candidate) -> ImplementationOption:
        return ImplementationOption(
            route="REUSE" if c.required_profiles or c.needs_model else "DIRECT",
            operations=c.required_operations,
            profile_ids=c.required_profiles,
            effects=c.requested_effects,
            requires_model=c.needs_model,
            note="declared preferred implementation substrate",
        )

    def _derived_profile_options(self, c: Candidate) -> tuple[ImplementationOption, ...]:
        if not c.required_operations:
            return ()
        eligible = [
            profile
            for profile in self.profiles.values()
            if profile.available and profile.operations
        ]
        singles = [
            profile
            for profile in eligible
            if c.required_operations.issubset(profile.operations)
        ]
        if singles:
            profile = sorted(
                singles,
                key=lambda item: (0 if item.deterministic else 1, item.profile_id),
            )[0]
            return (
                ImplementationOption(
                    route="REUSE",
                    operations=profile.operations,
                    profile_ids=(profile.profile_id,),
                    effects=c.requested_effects,
                    requires_model=not profile.deterministic,
                    note="derived from an available registered capability",
                ),
            )

        # Bounded smallest-complete coalition search. Four profiles is deliberately
        # enough for the selector's fallback synthesis; larger coalitions must be
        # declared explicitly rather than guessed into existence.
        ordered = sorted(eligible, key=lambda item: item.profile_id)
        max_size = min(4, len(ordered))
        for size in range(2, max_size + 1):
            from itertools import combinations

            for group in combinations(ordered, size):
                operations = frozenset().union(*(profile.operations for profile in group))
                if c.required_operations.issubset(operations):
                    return (
                        ImplementationOption(
                            route="COMPOSE",
                            operations=operations,
                            profile_ids=tuple(profile.profile_id for profile in group),
                            effects=c.requested_effects,
                            requires_model=any(not profile.deterministic for profile in group),
                            note="derived smallest registered capability coalition",
                        ),
                    )
        return ()

    def _implementation_rejection(
        self,
        c: Candidate,
        option: ImplementationOption,
    ) -> str | None:
        if option.route not in SAFE_SYNTHESIS_ROUTES:
            return f"unknown implementation route {option.route!r}"
        if not option.authorised:
            return "route is not authorised"
        if not option.available:
            return "route is unavailable"
        if option.bypasses_security:
            return "route would bypass authentication, permission or security controls"
        if not option.effects.issubset(c.requested_effects):
            return "route would widen requested effects"
        if c.required_operations and not c.required_operations.issubset(option.operations):
            missing = sorted(c.required_operations - option.operations)
            return "route does not cover required operations: " + ", ".join(missing)

        high_risk = (c.required_operations or option.operations) & HIGH_RISK_OPERATIONS
        if option.route in {"SMALL_CODE", "INTERNAL_BUILD"} and high_risk and not option.uses_maintained_standard:
            return (
                "high-risk solved primitive may not be hand-built without a maintained "
                "recognised standard/library: " + ", ".join(sorted(high_risk))
            )

        if option.requires_model and not c.provider_available:
            return "required model provider unavailable"
        if option.requires_model and not c.budget_available:
            return "model budget exhausted"

        unavailable = [
            profile_id
            for profile_id in option.profile_ids
            if profile_id not in self.profiles or not self.profiles[profile_id].available
        ]
        if unavailable:
            return "registered capability unavailable: " + ", ".join(unavailable)
        return None

    def synthesize_implementation(
        self,
        c: Candidate,
    ) -> tuple[ImplementationOption | None, tuple[str, ...]]:
        options = (
            self._preferred_implementation(c),
            *self._derived_profile_options(c),
            *c.implementation_options,
        )
        seen: set[tuple[object, ...]] = set()
        rejections: list[str] = []
        for option in options:
            identity = (
                option.route,
                option.profile_ids,
                tuple(sorted(option.operations)),
                tuple(sorted(option.effects)),
                option.requires_model,
                option.available,
                option.authorised,
                option.bypasses_security,
                option.uses_maintained_standard,
            )
            if identity in seen:
                continue
            seen.add(identity)
            rejection = self._implementation_rejection(c, option)
            if rejection is None:
                return option, tuple(rejections)
            rejections.append(f"{option.route}: {rejection}")
        return None, tuple(rejections)

    def classify(self, c: Candidate, state: ResolverState | None = None) -> Decision:
        state = state or ResolverState()
        unchanged = self._candidate_unchanged(c, state)
        if not c.current:
            return Decision(c.candidate_id, StateClass.HISTORICAL_SUPERSEDED, ("source is not current",), unchanged=unchanged)
        if c.causal_depth > self.causal_depth_limit:
            return Decision(c.candidate_id, StateClass.BLOCKED, ("causal continuation depth limit reached",), unchanged=unchanged)
        if c.specialist_stop:
            return Decision(c.candidate_id, StateClass.BLOCKED, ("competent specialist STOP dominates this candidate",), unchanged=unchanged)
        if c.reserved_for_king:
            return Decision(c.candidate_id, StateClass.RESERVED_FOR_KING, ("decision is expressly Crown-reserved",), unchanged=unchanged)
        if c.external_effect_required and not c.external_effect_authorised:
            return Decision(c.candidate_id, StateClass.EXTERNAL_NOT_AUTHORISED, ("external effect lacks exact authority",), unchanged=unchanged)
        if c.real_world_wait:
            return Decision(c.candidate_id, StateClass.WAITING_REAL_WORLD_EVENT, ("next evidence depends on a real-world trigger",), unchanged=unchanged)
        if c.blocked:
            blocker_unchanged = self._blocker_unchanged(c, state)
            if blocker_unchanged:
                return Decision(
                    c.candidate_id,
                    StateClass.BLOCKED,
                    ("blocker unchanged; suppress from competitive shortlist until trigger/evidence changes",),
                    unchanged=True,
                )
            return Decision(c.candidate_id, StateClass.BLOCKED, ("current blocker prevents execution",), unchanged=False)
        if c.idea_only or not c.evidence_sufficient:
            return Decision(c.candidate_id, StateClass.IDEA_ONLY, ("insufficient evidence for executable work",), unchanged=unchanged)
        if c.already_satisfied:
            return Decision(c.candidate_id, StateClass.NO_ACTION, ("accepted outcome already satisfied; no new consequence",), unchanged=unchanged)
        if not c.accepted_objective or not c.authority_ref:
            return Decision(c.candidate_id, StateClass.IDEA_ONLY, ("no accepted objective with explicit authority basis",), unchanged=unchanged)
        implementation, synthesis_rejections = self.synthesize_implementation(c)
        if implementation is None:
            detail = "; ".join(synthesis_rejections) or "no authorised implementation route exists"
            return Decision(
                c.candidate_id,
                StateClass.BLOCKED,
                ("implementation synthesis exhausted authorised routes: " + detail,),
                unchanged=unchanged,
            )
        reason = (
            "current accepted objective with explicit authority, evidence and executable "
            f"implementation route {implementation.route}"
        )
        if synthesis_rejections:
            reason += "; earlier substrate(s) rejected before fallback synthesis"
        return Decision(
            c.candidate_id,
            StateClass.ACTIONABLE_NOW,
            (reason,),
            priority_key=self.priority_key(c),
            unchanged=unchanged,
            implementation_route=implementation.route,
            implementation_profiles=list(implementation.profile_ids),
            implementation_operations=sorted(implementation.operations),
        )

    def priority_key(self, c: Candidate) -> tuple[int, ...]:
        # Deliberately lexicographic: policy bands, not a fake weighted objective score.
        return (
            LEVEL[c.owner_priority],
            LEVEL[c.urgency],
            LEVEL[c.enabling_value],
            LEVEL[c.user_value],
            LEVEL[c.evidence_gain],
            1 if c.reversible else 0,
            -COST[c.resource_cost],
            LEVEL[c.confidence],
        )

    def compose_profiles(
        self,
        c: Candidate,
        *,
        inherited_effects: frozenset[str],
        decision: Decision | None = None,
    ) -> tuple[CapabilityProfile, ...]:
        if not c.requested_effects.issubset(inherited_effects):
            raise AuthorityError("candidate requested effects exceed inherited authority")
        chosen: list[CapabilityProfile] = []
        profile_ids = c.required_profiles if decision is None else decision.implementation_profiles
        for profile_id in profile_ids:
            profile = self.profiles.get(profile_id)
            if profile is None:
                raise AuthorityError(f"unregistered execution profile: {profile_id}")
            if not profile.available:
                raise AuthorityError(f"execution profile unavailable: {profile_id}")
            if not profile.effects.issubset(inherited_effects):
                raise AuthorityError(f"profile {profile_id} would widen inherited authority")
            chosen.append(profile)
        return tuple(chosen)

    def resolve(self, candidates: Sequence[Candidate], state: ResolverState | None = None) -> Resolution:
        state = state or ResolverState()
        decisions = tuple(self.classify(c, state) for c in candidates)
        by_id = {c.candidate_id: c for c in candidates}
        actionables = [d for d in decisions if d.state_class == StateClass.ACTIONABLE_NOW]
        actionables.sort(key=lambda d: d.priority_key or (), reverse=True)

        actionable_ids = {d.candidate_id for d in actionables}
        conflicted: set[str] = set()
        for d in actionables:
            c = by_id[d.candidate_id]
            for other in c.conflicts_with:
                if other in actionable_ids:
                    conflicted.add(c.candidate_id)
                    conflicted.add(other)

        selected: str | None = None
        for d in actionables:
            if d.candidate_id not in conflicted:
                selected = d.candidate_id
                break

        next_state = self._next_state(candidates, decisions, state)
        if selected is not None:
            termination = "EXECUTE_ONE"
        elif conflicted:
            termination = "STOP_CONFLICTING_ACTIONABLES"
        else:
            termination = "NO_ACTION"
        return Resolution(
            selected_candidate_id=selected,
            decisions=decisions,
            conflict_candidate_ids=tuple(sorted(conflicted)),
            termination=termination,
            next_state=next_state.to_json(),
        )

    def _next_state(
        self,
        candidates: Sequence[Candidate],
        decisions: Sequence[Decision],
        state: ResolverState,
    ) -> ResolverState:
        blockers = dict(state.blockers)
        memories = dict(state.candidates)
        by_id = {c.candidate_id: c for c in candidates}
        for d in decisions:
            c = by_id[d.candidate_id]
            memories[c.candidate_id] = CandidateMemory(
                candidate_id=c.candidate_id,
                evidence_hash=c.evidence_hash,
                terminal_classification=d.state_class.value,
                next_review_trigger=c.next_review_trigger,
            )
            if d.state_class == StateClass.BLOCKED and c.blocker_id and c.blocker_evidence_hash:
                blockers[c.blocker_id] = BlockerMemory(
                    blocker_id=c.blocker_id,
                    evidence_hash=c.blocker_evidence_hash,
                    last_classification=d.state_class.value,
                    next_review_trigger=c.next_review_trigger,
                )
        return ResolverState(schema_version=1, blockers=blockers, candidates=memories)


def _implementation_option_from_mapping(value: Mapping[str, object]) -> ImplementationOption:
    return ImplementationOption(
        route=str(value.get("route") or ""),
        operations=frozenset(str(x) for x in (value.get("operations") or ())),
        profile_ids=tuple(str(x) for x in (value.get("profile_ids") or ())),
        effects=frozenset(str(x) for x in (value.get("effects") or ())),
        requires_model=bool(value.get("requires_model", False)),
        available=bool(value.get("available", True)),
        authorised=bool(value.get("authorised", True)),
        bypasses_security=bool(value.get("bypasses_security", False)),
        uses_maintained_standard=bool(value.get("uses_maintained_standard", False)),
        note=str(value.get("note") or ""),
    )


def candidate_from_fact(fact: SourceFact) -> Candidate | None:
    """Deterministic adapter from explicit fact kinds to bounded candidate classes.

    This function intentionally recognizes only encoded fact kinds. Unknown material is
    not silently transformed into work; a future semantic extractor may propose facts,
    but execution still requires one of these explicit contracts or a separately
    registered contract.
    """

    m = dict(fact.metadata)
    common = dict(
        owner=fact.owner,
        source_refs=[fact.source_ref],
        evidence_hash=fact.evidence_hash,
        authority_ref=fact.authority_ref,
        current=fact.current,
        accepted_objective=bool(fact.objective_id and fact.authority_ref),
        next_review_trigger=fact.next_trigger,
    )
    if fact.kind == "BLOCKER":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            blocked=True,
            blocker_id=fact.dependency_id or fact.fact_id,
            blocker_evidence_hash=fact.evidence_hash,
            owner_priority=str(m.get("owner_priority") or "HIGH"),
            urgency=str(m.get("urgency") or "MEDIUM"),
            enabling_value=str(m.get("enabling_value") or "HIGH"),
            **common,
        )
    if fact.kind == "WAITING_TRIGGER":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            real_world_wait=True,
            **common,
        )
    if fact.kind == "RESERVED_DECISION":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            reserved_for_king=True,
            **common,
        )
    if fact.kind == "COMPLETED_NO_CONSEQUENCE":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            already_satisfied=True,
            **common,
        )
    if fact.kind == "AUTHORISED_INTERNAL_WORK":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            owner_priority=str(m.get("owner_priority") or "MEDIUM"),
            urgency=str(m.get("urgency") or "LOW"),
            enabling_value=str(m.get("enabling_value") or "LOW"),
            user_value=str(m.get("user_value") or "LOW"),
            evidence_gain=str(m.get("evidence_gain") or "LOW"),
            confidence=str(m.get("confidence") or "MEDIUM"),
            reversible=bool(m.get("reversible", True)),
            resource_cost=str(m.get("resource_cost") or "NONE"),
            required_profiles=tuple(str(x) for x in (m.get("required_profiles") or ())),
            required_operations=frozenset(str(x) for x in (m.get("required_operations") or ())),
            implementation_options=tuple(
                _implementation_option_from_mapping(item)
                for item in (m.get("implementation_options") or ())
                if isinstance(item, Mapping)
            ),
            requested_effects=frozenset(str(x) for x in (m.get("requested_effects") or ())),
            conflicts_with=tuple(str(x) for x in (m.get("conflicts_with") or ())),
            needs_model=bool(m.get("needs_model", False)),
            provider_available=bool(m.get("provider_available", True)),
            budget_available=bool(m.get("budget_available", True)),
            causal_parent=(str(m.get("causal_parent")) if m.get("causal_parent") else None),
            causal_depth=int(m.get("causal_depth") or 0),
            **common,
        )
    if fact.kind == "EXTERNAL_WORK":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            external_effect_required=True,
            external_effect_authorised=bool(m.get("external_effect_authorised", False)),
            **common,
        )
    if fact.kind == "IDEA":
        return Candidate(
            candidate_id=fact.objective_id or fact.fact_id,
            title=fact.proposition,
            idea_only=True,
            **common,
        )
    return None


def discover_candidates(facts: Iterable[SourceFact]) -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for fact in facts:
        candidate = candidate_from_fact(fact)
        if candidate is not None:
            candidates.append(candidate)
    # Stable identity dedup; conflicting duplicate evidence is intentionally retained as
    # a non-executable idea instead of last-write-wins authority guessing.
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.candidate_id, []).append(candidate)
    out: list[Candidate] = []
    for candidate_id, group in grouped.items():
        hashes = {c.evidence_hash for c in group}
        if len(hashes) == 1:
            out.append(group[0])
            continue
        refs = sorted({ref for c in group for ref in c.source_refs})
        out.append(
            Candidate(
                candidate_id=candidate_id,
                title=f"Conflicting current evidence for {candidate_id}",
                owner=group[0].owner,
                source_refs=refs,
                evidence_hash=stable_hash(sorted(hashes)),
                authority_ref=group[0].authority_ref,
                current=all(c.current for c in group),
                accepted_objective=False,
                evidence_sufficient=False,
                idea_only=True,
            )
        )
    return tuple(sorted(out, key=lambda c: c.candidate_id))
