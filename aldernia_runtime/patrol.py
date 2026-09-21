from __future__ import annotations

import hashlib
from typing import Any, Mapping

PATROL_SOURCE_ROW = 21
PATROL_REGISTRY_VERSION = 1
REVIEW_TRIGGER = "NEXT KING SESSION / KING'S BALCONY OPENING"
CLASSIFICATIONS = {
    "CURRENT",
    "STALE/SUPERSEDED",
    "DUPLICATE-CURRENT",
    "CONFLICTING CURRENT MATERIAL",
}

PATROL_COMMON_SOURCE_IDS = (
    "14rxhnw7MK5LLnj8QbrokDAeKBV_PlOg4ffuCUsV7ghI",  # CAT-123
    "1XdilhHRYu5OQHqHs7uPvLKV5TlZ9vwxVqd3gFqJw4XU",  # House of Series START HERE
    "1wh9C9IGxMxRtvenxLnknFvEWQgN7aI_8qaf8ES-SyiY",  # Computer FIRST READ
    "1NumS4_cKvgJLQfUIG45Nug7clbv2zuxU2EZ9bvSJdXQ",  # Engine Manifest
    "1iJU__qY2P-LSNJDqIaG5qZH2Dw6p2DnNZ35LUzGTkVg",  # House OS
    "1gS-V40twMOT29dBiXrNrNlo87dUD49u9t2e9yHGOS98",  # Queen Barbara Admin FIRST READ
    "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA",  # Palace Scheduled Tasks
)

ESTATES: tuple[dict[str, Any], ...] = (
    {
        "id": "house-of-carol",
        "label": "House of Carol — Business",
        "required_source_ids": (
            "15sUjJSU9z5YaXgMecV-2PeQw2x-2JfmDiTGXoM-T6S8",
            "1wxAoiO4kLqzK8_jrrzP8uI0EbU46PtA4-vXxCE8Eh_M",
            "1pMaDtUgcCGaRn1LaCk2JYMdr1Hqe-BY-auDQm1lTUqI",
        ),
    },
    {
        "id": "house-of-tony",
        "label": "House of Tony — Education",
        "required_source_ids": (
            "1GXGtQaRJlCn9Pdl4jL4oWGNGywFeeYxWcjjiSZ2zfEA",
            "1RwwJX4yzsgTFLCoKel1geMNz0RqRje1ljfPhQHQrVSs",
        ),
    },
    {
        "id": "house-of-marianne",
        "label": "House of Marianne — Government",
        "required_source_ids": (
            "1Z6jkA6SuaPDAQYWAAYEkjjRGg00wP8KF3odrf-fktxU",
            "1fZ7_rv8nMkPgJIHCHOH45fYPmNdKKhVlUkDoyoIfKoU",
        ),
    },
    {
        "id": "house-of-nathaniel",
        "label": "House of Nathaniel — Health",
        "required_source_ids": (
            "1u7UjJFMr7rQaAO1rQGDeYxbvkgeTksjNZCCabyvn9-Q",
            "1RCbdhSLeU-pDgrRfZDkd2e7lcCVX46uti5RK9SvkTno",
        ),
    },
    {
        "id": "house-of-vivienne",
        "label": "House of Vivienne — Banking",
        "required_source_ids": (
            "13hKi_Z32Zej1vcDLhhUgCjHqOSWuNhks2T4X1cqyXrw",
            "1KiH8nsUBO8FA3HzcJSYnko_heuDiEQSpMe-np0-e7Sk",
        ),
    },
    {
        "id": "house-of-catholic",
        "label": "House of Catholic — Religion",
        "required_source_ids": (
            "1tUVJfn1r5joQEgGTmG8frxmQXLuOmyNzUY8gqL1oSVk",
            "1X4fjKOe7dM95RlDstT7DAUbwR8eyZwkGvqdJ44OVOz0",
        ),
    },
    {
        "id": "house-of-josie",
        "label": "House of Josie — Media",
        "required_source_ids": (
            "1ulQ0QDZLveGRvT_zJt5eUXDXgdIKntnwEliNK98zAc0",
            "1ffQZMgXnQPe0tlI_RUdYhnAKOASGi_6yRXlEprVUSiw",
        ),
    },
    {
        "id": "teach-antaine",
        "label": "Teach Antaine — Government Institutions",
        "required_source_ids": (
            "1iyPMszZukxm9rnQeRHkHf1v6EC9kMDy_zyRZPVRbe3A",
            "1zIHd_1DxTeJpJRsgDjZdT3GLvzdskl5oJjcJ9chEr_E",
            "1XYpIpSnXjn3Ks6Xv4-pkSx4ypoUkB7eBGXm5xZh4Flk",
        ),
    },
    {
        "id": "hmdf",
        "label": "His Majesty's Dynastic Defence Forces — HMDF",
        "required_source_ids": (
            "1E7br4kX1OsusS29q0TBrtwBAPwavlB_st_9jwEQXEZA",
            "13ZW5khEzz_IPL3bR_DXtRY-s4z8qpKKQmUz7Lu06nWw",
        ),
    },
    {
        "id": "royal-palace",
        "label": "The Royal Palace",
        "required_source_ids": (
            "17urYyFWqc1ezykTpp8LHjuGqywBXmmr_O0hs_89QXGE",
            "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA",
        ),
    },
    {
        "id": "common-machinery",
        "label": "House of Series / Computer of Series common machinery",
        "required_source_ids": (
            "1XdilhHRYu5OQHqHs7uPvLKV5TlZ9vwxVqd3gFqJw4XU",
            "1wh9C9IGxMxRtvenxLnknFvEWQgN7aI_8qaf8ES-SyiY",
            "1NumS4_cKvgJLQfUIG45Nug7clbv2zuxU2EZ9bvSJdXQ",
            "1f7CvUz3abW0WuuPkoDRdwnLDs4Bgd-K0yfcNszQ9Crk",
            "1f9ehuX67WRwYMLK0JD7s1q-NEN9BGqdzNC3kfxyLl7s",
            "1iJU__qY2P-LSNJDqIaG5qZH2Dw6p2DnNZ35LUzGTkVg",
            "1gS-V40twMOT29dBiXrNrNlo87dUD49u9t2e9yHGOS98",
        ),
    },
)

ESTATE_INDEX = {estate["id"]: index for index, estate in enumerate(ESTATES)}


class PatrolError(RuntimeError):
    pass


def _next_estate_id(estate_id: str) -> str:
    try:
        index = ESTATE_INDEX[estate_id]
    except KeyError as exc:
        raise PatrolError(f"unknown patrol estate {estate_id!r}") from exc
    return str(ESTATES[(index + 1) % len(ESTATES)]["id"])


def next_estate_id(state: Mapping[str, Any]) -> str:
    completed: list[tuple[str, Mapping[str, Any]]] = []
    events = state.get("events")
    if isinstance(events, Mapping):
        for event in events.values():
            if not isinstance(event, Mapping):
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or payload.get("duty_id") != "dynasty.heartbeat":
                continue
            receipt = event.get("receipt")
            if not isinstance(receipt, Mapping):
                continue
            if receipt.get("outcome") == "STOP":
                continue
            patrol = receipt.get("integrity_patrol")
            if not isinstance(patrol, Mapping) or patrol.get("readback_verified") is not True:
                continue
            next_id = patrol.get("cursor_after")
            if next_id not in ESTATE_INDEX:
                continue
            completed.append((str(event.get("scheduled_for") or ""), patrol))
    if not completed:
        return "house-of-carol"
    completed.sort(key=lambda item: item[0])
    return str(completed[-1][1]["cursor_after"])


def plan_slice(state: Mapping[str, Any]) -> dict[str, Any]:
    estate_id = next_estate_id(state)
    estate = ESTATES[ESTATE_INDEX[estate_id]]
    required = list(dict.fromkeys(PATROL_COMMON_SOURCE_IDS + tuple(estate["required_source_ids"])))
    return {
        "registry_version": PATROL_REGISTRY_VERSION,
        "source_row": PATROL_SOURCE_ROW,
        "estate_id": estate_id,
        "estate_label": estate["label"],
        "cursor_after": _next_estate_id(estate_id),
        "required_source_ids": required,
        "review_trigger": REVIEW_TRIGGER,
        "royal_household_access_permitted": False,
        "mode": "ONE_BOUNDED_SLICE_PER_HEARTBEAT",
    }


def stable_conflict_id(proposition: str, source_refs: list[str] | tuple[str, ...]) -> str:
    if not isinstance(proposition, str) or not proposition.strip():
        raise PatrolError("conflicting proposition is required")
    if not isinstance(source_refs, (list, tuple)) or len(source_refs) < 2:
        raise PatrolError("a conflict requires at least two source references")
    normalized_refs = sorted(str(ref).strip() for ref in source_refs if str(ref).strip())
    if len(normalized_refs) < 2:
        raise PatrolError("a conflict requires at least two non-empty source references")
    normalized = " ".join(proposition.split()).casefold() + "|" + "|".join(normalized_refs)
    return "PATROL-CONFLICT-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20].upper()


def validate_patrol_result(
    *,
    plan: Mapping[str, Any],
    patrol_result: Any,
    retrieved_source_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(patrol_result, Mapping):
        raise PatrolError("heartbeat acknowledgement requires an integrity_patrol result")
    if patrol_result.get("registry_version") != PATROL_REGISTRY_VERSION:
        raise PatrolError("patrol registry version mismatch")
    if patrol_result.get("source_row") != PATROL_SOURCE_ROW:
        raise PatrolError("patrol source row mismatch")
    if patrol_result.get("estate_id") != plan.get("estate_id"):
        raise PatrolError("patrol estate does not match the durable cursor")
    if patrol_result.get("cursor_after") != plan.get("cursor_after"):
        raise PatrolError("patrol cursor_after does not match the estate registry")
    if patrol_result.get("current_authority_retrieved") is not True:
        raise PatrolError("current authority must be retrieved before patrol classification")
    if patrol_result.get("royal_household_accessed") is not False:
        raise PatrolError("private Royal Household access is prohibited")
    if patrol_result.get("readback_verified") is not True:
        raise PatrolError("patrol readback must be verified")

    expected = {str(value) for value in plan.get("required_source_ids") or []}
    missing = sorted(expected - retrieved_source_ids)
    if missing:
        raise PatrolError(
            "patrol source retrieval failed; missing required source ids: " + ", ".join(missing)
        )

    findings = patrol_result.get("findings")
    if not isinstance(findings, list):
        raise PatrolError("patrol findings must be a list")

    normalized_findings: list[dict[str, Any]] = []
    seen_conflicts: set[str] = set()
    for raw in findings:
        if not isinstance(raw, Mapping):
            raise PatrolError("each patrol finding must be an object")
        classification = raw.get("classification")
        if classification not in CLASSIFICATIONS:
            raise PatrolError(f"unsupported patrol classification {classification!r}")
        source_refs = raw.get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            raise PatrolError("each patrol finding requires source_refs")
        finding = dict(raw)

        if classification == "CURRENT":
            if raw.get("disposition") not in {"NO_ACTION", None}:
                raise PatrolError("CURRENT material may not be mutated by the patrol")

        if classification in {"STALE/SUPERSEDED", "DUPLICATE-CURRENT"}:
            disposition = raw.get("disposition")
            if disposition not in {"ARCHIVED", "SUPERSEDED", "STOP_NO_SAFE_ARCHIVE"}:
                raise PatrolError("stale or duplicate-current material requires controlled archive/supersession or a bounded STOP")
            if raw.get("provenance_preserved") is not True:
                raise PatrolError("archive/supersession must preserve provenance")
            if raw.get("readback_verified") is not True:
                raise PatrolError("archive/supersession requires readback verification")
            if raw.get("destructive_delete") is not False:
                raise PatrolError("patrol archive may not destructively delete material")
            if disposition == "STOP_NO_SAFE_ARCHIVE":
                if not isinstance(raw.get("archive_error"), str) or not raw["archive_error"].strip():
                    raise PatrolError("bounded archive STOP requires archive_error evidence")

        if classification == "CONFLICTING CURRENT MATERIAL":
            proposition = raw.get("proposition")
            expected_conflict_id = stable_conflict_id(str(proposition or ""), source_refs)
            if raw.get("conflict_id") != expected_conflict_id:
                raise PatrolError("conflict_id is not the stable conflict identity")
            if expected_conflict_id in seen_conflicts:
                raise PatrolError("duplicate conflict appears twice in one patrol slice")
            seen_conflicts.add(expected_conflict_id)
            if raw.get("disposition") != "QUEUED_FOR_CROWN":
                raise PatrolError("current conflict must be queued rather than silently resolved")
            if raw.get("queue_review_trigger") != REVIEW_TRIGGER:
                raise PatrolError("conflict queue review trigger is incorrect")
            if not isinstance(raw.get("queue_ref"), str) or not raw["queue_ref"].strip():
                raise PatrolError("conflict requires a King's Consideration Queue reference")
            if raw.get("queue_deduplicated") is not True:
                raise PatrolError("conflict queue write must be deduplicated")
            if raw.get("readback_verified") is not True:
                raise PatrolError("conflict queue write requires readback verification")

        normalized_findings.append(finding)

    receipt = {
        "registry_version": PATROL_REGISTRY_VERSION,
        "source_row": PATROL_SOURCE_ROW,
        "estate_id": plan["estate_id"],
        "estate_label": plan["estate_label"],
        "cursor_after": plan["cursor_after"],
        "current_authority_retrieved": True,
        "findings": normalized_findings,
        "review_trigger": REVIEW_TRIGGER,
        "royal_household_accessed": False,
        "readback_verified": True,
    }
    scan_before = patrol_result.get("scan_cursor_before")
    scan_after = patrol_result.get("scan_cursor_after")
    scan_truncated = patrol_result.get("scan_truncated")
    if scan_before is not None and not isinstance(scan_before, str):
        raise PatrolError("scan_cursor_before must be a string or null")
    if scan_after is not None and not isinstance(scan_after, str):
        raise PatrolError("scan_cursor_after must be a string or null")
    if scan_truncated is not None and not isinstance(scan_truncated, bool):
        raise PatrolError("scan_truncated must be a boolean when provided")
    receipt["scan_cursor_before"] = scan_before
    receipt["scan_cursor_after"] = scan_after
    receipt["scan_truncated"] = bool(scan_truncated)
    return receipt
