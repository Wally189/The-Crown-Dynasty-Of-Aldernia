from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from aldernia_runtime.opportunity_resolver import SourceFact, stable_hash

SCHEMA_VERSION = 1
PROGRAMME_BOARD_SPREADSHEET_ID = "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA"
PROGRAMME_BOARD_SHEET = "Royal Programme Board"
PROGRAMME_BOARD_A1 = "'Royal Programme Board'!A5:D220"
MACHINE_MARKER = " / MACHINE "
ALLOWED_TRANSPORTS = {"LIVE_GOOGLE_DRIVE_API", "CONNECTED_DRIVE_BRIDGE"}
ALLOWED_PROFILES = ("COS-SEL-001", "COS-BUS-001", "COS-WORKER-001")
ALLOWED_EFFECT = "INTERNAL_DERIVED_STATE_WRITE"
ALLOWED_EXECUTION_CLASS = "COS-WORKER-001::CANONICAL_DELTA_ACK"
_REQUIRED_KEYS = {
    "kind",
    "candidate_id",
    "profile",
    "execution_class",
    "effect",
    "reversible",
    "model",
    "consequence",
}


class CanonicalPacketError(RuntimeError):
    pass


class CanonicalAccessUnavailable(CanonicalPacketError):
    pass


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def _http_json(url: str, token: str) -> dict[str, object]:
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CanonicalAccessUnavailable(f"canonical Google read failed: {exc}") from exc
    if not isinstance(value, dict):
        raise CanonicalAccessUnavailable("canonical Google read returned a non-object")
    return value


def build_packet_from_values(
    values: Sequence[Sequence[object]],
    *,
    modified_time: str,
    transport: str,
    start_row: int = 5,
    transport_note: str | None = None,
) -> dict[str, object]:
    if transport not in ALLOWED_TRANSPORTS:
        raise CanonicalPacketError(f"unsupported canonical transport: {transport}")
    rows: list[dict[str, object]] = []
    for offset, raw in enumerate(values):
        row = list(raw[:4]) + [""] * max(0, 4 - len(raw))
        entry_id, mission, narrative, outcome = (str(x) for x in row[:4])
        if MACHINE_MARKER not in outcome:
            continue
        rows.append(
            {
                "source_row": start_row + offset,
                "entry_id": entry_id,
                "mission": mission,
                "narrative": narrative,
                "outcome": outcome,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "spreadsheet_id": PROGRAMME_BOARD_SPREADSHEET_ID,
            "sheet_name": PROGRAMME_BOARD_SHEET,
            "range": PROGRAMME_BOARD_A1,
            "modified_time": modified_time,
        },
        "transport": transport,
        "transport_note": transport_note,
        "rows": rows,
    }


def fetch_live_packet(token: str) -> dict[str, object]:
    if not token:
        raise CanonicalAccessUnavailable("GOOGLE_DRIVE_ACCESS_TOKEN is not configured")
    metadata = _http_json(
        "https://www.googleapis.com/drive/v3/files/"
        + PROGRAMME_BOARD_SPREADSHEET_ID
        + "?fields=id,modifiedTime",
        token,
    )
    encoded_range = quote(PROGRAMME_BOARD_A1, safe="")
    values = _http_json(
        "https://sheets.googleapis.com/v4/spreadsheets/"
        + PROGRAMME_BOARD_SPREADSHEET_ID
        + "/values/"
        + encoded_range
        + "?majorDimension=ROWS",
        token,
    )
    raw_values = values.get("values")
    if raw_values is None:
        raw_values = []
    if not isinstance(raw_values, list):
        raise CanonicalAccessUnavailable("Google Sheets values payload is malformed")
    return build_packet_from_values(
        raw_values,
        modified_time=str(metadata.get("modifiedTime") or ""),
        transport="LIVE_GOOGLE_DRIVE_API",
        transport_note="Fetched by the existing Central Aldernia Clock identity; no provider or IAM mutation performed.",
    )


def validate_packet(packet: Mapping[str, object]) -> None:
    if int(packet.get("schema_version") or 0) != SCHEMA_VERSION:
        raise CanonicalPacketError("unsupported canonical packet schema")
    source = packet.get("source")
    if not isinstance(source, Mapping):
        raise CanonicalPacketError("canonical packet source is missing")
    if source.get("spreadsheet_id") != PROGRAMME_BOARD_SPREADSHEET_ID:
        raise CanonicalPacketError("canonical packet spreadsheet identity mismatch")
    if source.get("sheet_name") != PROGRAMME_BOARD_SHEET:
        raise CanonicalPacketError("canonical packet sheet identity mismatch")
    transport = str(packet.get("transport") or "")
    if transport not in ALLOWED_TRANSPORTS:
        raise CanonicalPacketError("canonical packet transport is not recognised")
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise CanonicalPacketError("canonical packet rows must be a list")
    for row in rows:
        if not isinstance(row, Mapping):
            raise CanonicalPacketError("canonical packet row must be an object")
        if int(row.get("source_row") or 0) < 5:
            raise CanonicalPacketError("canonical packet row number is invalid")
        for key in ("entry_id", "mission", "narrative", "outcome"):
            if not isinstance(row.get(key), str):
                raise CanonicalPacketError(f"canonical packet row field {key} must be text")


def parse_machine_contract(outcome: str) -> dict[str, object] | None:
    if MACHINE_MARKER not in outcome:
        return None
    raw = outcome.split(MACHINE_MARKER, 1)[1].strip()
    pairs: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            return None
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in pairs:
            return None
        pairs[key] = value
    if set(pairs) != _REQUIRED_KEYS:
        return None
    candidate_id = pairs["candidate_id"]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9:_-]{2,127}", candidate_id):
        return None
    profiles = tuple(x.strip() for x in pairs["profile"].split(",") if x.strip())
    if profiles != ALLOWED_PROFILES:
        return None
    if pairs["kind"] != "AUTHORISED_INTERNAL_WORK":
        return None
    if pairs["execution_class"] != ALLOWED_EXECUTION_CLASS:
        return None
    if pairs["effect"] != ALLOWED_EFFECT:
        return None
    if pairs["reversible"].lower() != "true":
        return None
    if pairs["model"].lower() != "false":
        return None
    if pairs["consequence"] != "NONE":
        return None
    return {
        "kind": pairs["kind"],
        "candidate_id": candidate_id,
        "profiles": profiles,
        "execution_class": pairs["execution_class"],
        "effect": pairs["effect"],
        "reversible": True,
        "model": False,
        "consequence": "NONE",
    }


def machine_rows(packet: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    validate_packet(packet)
    source = packet["source"]
    assert isinstance(source, Mapping)
    out: list[dict[str, object]] = []
    rows = packet["rows"]
    assert isinstance(rows, list)
    for raw in rows:
        assert isinstance(raw, Mapping)
        contract = parse_machine_contract(str(raw["outcome"]))
        if contract is None:
            continue
        evidence_hash = stable_hash(
            {
                "source_row": int(raw["source_row"]),
                "entry_id": str(raw["entry_id"]),
                "mission": str(raw["mission"]),
                "narrative": str(raw["narrative"]),
                "outcome": str(raw["outcome"]),
                "contract": contract,
            }
        )
        out.append(
            {
                "source_row": int(raw["source_row"]),
                "entry_id": str(raw["entry_id"]),
                "mission": str(raw["mission"]),
                "narrative": str(raw["narrative"]),
                "outcome": str(raw["outcome"]),
                "contract": contract,
                "evidence_hash": evidence_hash,
                "source_ref": (
                    "Google Drive:"
                    + PROGRAMME_BOARD_SPREADSHEET_ID
                    + "/"
                    + PROGRAMME_BOARD_SHEET
                    + f"!A{int(raw['source_row'])}:D{int(raw['source_row'])}"
                ),
                "transport": str(packet.get("transport") or ""),
                "source_modified_time": str(source.get("modified_time") or ""),
            }
        )
    return tuple(out)


def canonical_facts_from_packet(
    packet: Mapping[str, object] | None,
    *,
    acknowledged: Mapping[str, object] | None = None,
) -> tuple[SourceFact, ...]:
    if not packet:
        return ()
    acknowledged = acknowledged or {}
    facts: list[SourceFact] = []
    for row in machine_rows(packet):
        contract = row["contract"]
        assert isinstance(contract, Mapping)
        candidate_id = str(contract["candidate_id"])
        prior = acknowledged.get(candidate_id)
        ack_hash = (
            str(prior.get("evidence_hash") or "")
            if isinstance(prior, Mapping)
            else str(prior or "")
        )
        evidence_hash = str(row["evidence_hash"])
        completed = bool(ack_hash and ack_hash == evidence_hash)
        facts.append(
            SourceFact(
                fact_id=f"programme-board:{row['entry_id']}:{candidate_id}",
                source_ref=str(row["source_ref"]),
                owner="Computer of Series",
                proposition=str(row["mission"]),
                current=True,
                evidence_hash=evidence_hash,
                kind="COMPLETED_NO_CONSEQUENCE" if completed else "AUTHORISED_INTERNAL_WORK",
                status="ACKNOWLEDGED" if completed else "AUTHORISED",
                objective_id=candidate_id,
                authority_ref=f"Royal Programme Board {row['entry_id']}",
                metadata={
                    "owner_priority": "HIGH",
                    "urgency": "HIGH",
                    "enabling_value": "HIGH",
                    "user_value": "MEDIUM",
                    "evidence_gain": "HIGH",
                    "confidence": "HIGH",
                    "reversible": True,
                    "resource_cost": "NONE",
                    "required_profiles": list(contract["profiles"]),
                    "requested_effects": [str(contract["effect"])],
                    "needs_model": False,
                    "provider_available": True,
                    "budget_available": True,
                    "execution_class": str(contract["execution_class"]),
                    "canonical_transport": str(row["transport"]),
                    "source_modified_time": str(row["source_modified_time"]),
                    "consequence": str(contract["consequence"]),
                },
            )
        )
    return tuple(facts)


def load_packet(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CanonicalPacketError("canonical packet file must contain an object")
    validate_packet(value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the current machine-authorised Royal Programme Board rows using an already-configured Drive token."
    )
    parser.add_argument("--output", default="state/canonical-programme-board.json")
    args = parser.parse_args(argv)
    packet = fetch_live_packet(os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", ""))
    _atomic_write_json(Path(args.output), packet)
    print(json.dumps(packet, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
