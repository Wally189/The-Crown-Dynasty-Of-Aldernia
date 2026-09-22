from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from aldernia_runtime.canonical_programme_board import parse_machine_contract
from aldernia_runtime.opportunity_resolver import SourceFact, stable_hash

SCHEMA_VERSION = 1
TRANSPORT = "PRIVATE_GITHUB_REPOSITORY"


class PrivateGitHubCanonicalError(RuntimeError):
    pass


def validate_repository_context(repository: str, visibility: str, expected_repository: str | None = None) -> None:
    if visibility.lower() != "private":
        raise PrivateGitHubCanonicalError("runtime canonical repository must be private")
    if "/" not in repository:
        raise PrivateGitHubCanonicalError("runtime repository identity is missing")
    if expected_repository and repository != expected_repository:
        raise PrivateGitHubCanonicalError("runtime repository identity mismatch")


def validate_manifest(manifest: Mapping[str, object], require_active: bool = False) -> None:
    if int(manifest.get("schema_version") or 0) != SCHEMA_VERSION:
        raise PrivateGitHubCanonicalError("unsupported manifest schema")
    if manifest.get("required_visibility") != "private":
        raise PrivateGitHubCanonicalError("manifest must require private visibility")
    if manifest.get("authoritative_home_rule") != "EXACTLY_ONE_LIVE_HOME_PER_RECORD":
        raise PrivateGitHubCanonicalError("one-home rule missing")
    if require_active and manifest.get("cutover_state") != "ACTIVE":
        raise PrivateGitHubCanonicalError("cutover is not active")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise PrivateGitHubCanonicalError("manifest records missing")
    ids: set[str] = set()
    homes: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise PrivateGitHubCanonicalError("manifest record malformed")
        record_id = str(raw.get("record_id") or "")
        home = str(raw.get("private_home") or "")
        if not record_id or not home or not raw.get("owner") or not raw.get("current_home"):
            raise PrivateGitHubCanonicalError("manifest record incomplete")
        if record_id in ids or home in homes:
            raise PrivateGitHubCanonicalError("duplicate record identity or home")
        if raw.get("after_cutover_old_home") != "FROZEN_PROVENANCE":
            raise PrivateGitHubCanonicalError("old home must freeze at cutover")
        ids.add(record_id)
        homes.add(home)


def load_manifest(path: Path, require_active: bool = False) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PrivateGitHubCanonicalError("manifest must contain an object")
    validate_manifest(value, require_active=require_active)
    return value


def validate_programme_board(record: Mapping[str, object], require_active: bool = False) -> None:
    if int(record.get("schema_version") or 0) != SCHEMA_VERSION:
        raise PrivateGitHubCanonicalError("unsupported programme-board schema")
    if record.get("record_id") != "ROYAL_PROGRAMME_BOARD_MACHINE":
        raise PrivateGitHubCanonicalError("programme-board identity mismatch")
    if require_active and record.get("cutover_state") != "ACTIVE":
        raise PrivateGitHubCanonicalError("programme-board cutover is not active")
    rows = record.get("rows")
    if not isinstance(rows, list):
        raise PrivateGitHubCanonicalError("programme-board rows missing")
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise PrivateGitHubCanonicalError("programme-board row malformed")
        entry_id = str(raw.get("entry_id") or "")
        if not entry_id or entry_id in seen:
            raise PrivateGitHubCanonicalError("programme-board entry identity invalid")
        if any(not isinstance(raw.get(k), str) for k in ("mission", "narrative", "outcome")):
            raise PrivateGitHubCanonicalError("programme-board fields must be text")
        seen.add(entry_id)


def canonical_facts_from_record(
    record: Mapping[str, object],
    repository: str,
    commit_sha: str,
    acknowledged: Mapping[str, object] | None = None,
) -> tuple[SourceFact, ...]:
    validate_programme_board(record)
    acknowledged = acknowledged or {}
    facts: list[SourceFact] = []
    rows = record["rows"]
    assert isinstance(rows, list)
    for raw in rows:
        assert isinstance(raw, Mapping)
        contract = parse_machine_contract(str(raw["outcome"]))
        if contract is None:
            continue
        evidence_hash = stable_hash({
            "entry_id": str(raw["entry_id"]),
            "mission": str(raw["mission"]),
            "narrative": str(raw["narrative"]),
            "outcome": str(raw["outcome"]),
            "contract": contract,
        })
        candidate_id = str(contract["candidate_id"])
        prior = acknowledged.get(candidate_id)
        ack_hash = str(prior.get("evidence_hash") or "") if isinstance(prior, Mapping) else str(prior or "")
        completed = bool(ack_hash and ack_hash == evidence_hash)
        facts.append(SourceFact(
            fact_id=f"programme-board:{raw['entry_id']}:{candidate_id}",
            source_ref=f"github:{repository}@{commit_sha}:records/royal-programme-board.json#entry={raw['entry_id']}",
            owner="Computer of Series",
            proposition=str(raw["mission"]),
            current=True,
            evidence_hash=evidence_hash,
            kind="COMPLETED_NO_CONSEQUENCE" if completed else "AUTHORISED_INTERNAL_WORK",
            status="ACKNOWLEDGED" if completed else "AUTHORISED",
            objective_id=candidate_id,
            authority_ref=f"Royal Programme Board {raw['entry_id']}",
            metadata={
                "reversible": True,
                "resource_cost": "NONE",
                "required_profiles": list(contract["profiles"]),
                "requested_effects": [str(contract["effect"])],
                "needs_model": False,
                "provider_available": True,
                "budget_available": True,
                "execution_class": str(contract["execution_class"]),
                "canonical_transport": TRANSPORT,
                "consequence": str(contract["consequence"]),
            },
        ))
    return tuple(facts)
