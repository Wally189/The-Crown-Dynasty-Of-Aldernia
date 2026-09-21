from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib import error, parse, request

from aldernia_runtime.patrol import REVIEW_TRIGGER, stable_conflict_id
from aldernia_runtime.worker import (
    PERSISTENT_MODEL_RUNTIME_CLASS,
    acknowledge_event,
    claim_event,
)

PALACE_SPREADSHEET_ID = "1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA"
ROYAL_HOUSEHOLD_FOLDER_ID = "1PPTSZY9fhe9ouZ7fLj9YD10qQ-HqIYMj"
HOUSE_OF_SERIES_ROOT_ID = "1wig72YKrArZdn389JvI1szV5HVMGJ1Qb"
COMPUTER_OF_SERIES_ROOT_ID = "1ahf2KAJBMm-qatUYRtPvsSQod5rfjJU3"
COMMON_ARCHIVE_FOLDER_ID = "1hhxQzNa4UrNYmKPgkZD9xS37X17ZuWct"
NATHANIEL_ARCHIVE_FOLDER_ID = "1G5jOiJNMt33xwHGDoko9MJZYPqanWxO6"
FIXTURE_ACTIVE_FOLDER_ID = "1wjl65KRZaKQEsJvt19CoXPGCeYqCxIO-"
FIXTURE_ARCHIVE_FOLDER_ID = "14m4dHLnB5qMyextQ9jW5bE4HTYY7QibD"
FIXTURE_STALE_FILE_ID = "1aj_N1WvnwHV5r5zjb7LhyFgnKiECJBRkLanhSIT4LUY"
FIXTURE_CONFLICT_A_FILE_ID = "1pe-g7IYCua1QvVz0gAP_A0Id16AgBbdVWkJC8QoyvYU"
FIXTURE_CONFLICT_B_FILE_ID = "1QoYDN_Dxm7BMoNrvlzSo3D0gzsl3B3GpggPrWj36NK8"

MODEL = os.environ.get("ALDERNIA_PATROL_MODEL", "gpt-5.6-luna")
SOFTWARE_HARD_CAP_USD = 3.00
MAX_RESERVED_REQUEST_COST_USD = 0.05
INPUT_USD_PER_MILLION = 0.20
OUTPUT_USD_PER_MILLION = 1.20
MAX_OUTPUT_TOKENS = 1800
MAX_MODEL_INPUT_CHARS = 60000
MAX_SCAN_DOCUMENTS = 24
MAX_TRAVERSED_ITEMS = 2000

ESTATE_RUNTIME_BOUNDS: dict[str, dict[str, Any]] = {
    "house-of-carol": {"root_specs": [("10Qn-cOnkSVc8rCAZohWYhcjw6xPNa0Uo", True)], "archive_folder_id": None},
    "house-of-tony": {"root_specs": [("1-OiNqw5nFu0HOE-P6Bm7sR2yg9OnMSlw", True)], "archive_folder_id": None},
    "house-of-marianne": {"root_specs": [("1RmSnSgq2m4FRMQV9_vPF3mTcCvxV1ois", True)], "archive_folder_id": None},
    "house-of-nathaniel": {"root_specs": [("12ZBEhoGEa0FbD_u5QbhnQyxHo-7VSAP3", True)], "archive_folder_id": NATHANIEL_ARCHIVE_FOLDER_ID},
    "house-of-vivienne": {"root_specs": [("1jSnWKplkNITTqMl6jsq5TVHiLSnrq72T", True)], "archive_folder_id": None},
    "house-of-catholic": {"root_specs": [("1anvYNAC1z-fUVw90WNYqs-D_j1ULSyxZ", True)], "archive_folder_id": None},
    "house-of-josie": {"root_specs": [("1vm6vJi9qDDKqXCO_1fmzFgS8oToL9855", True)], "archive_folder_id": None},
    "teach-antaine": {"root_specs": [("1eIJcQfx5afjFq66gSP1-K-HZWRMTBqlg", True)], "archive_folder_id": None},
    "hmdf": {"root_specs": [("10gARPVBY4OeISrBB7gTXyXnEHEDGYq6-", True)], "archive_folder_id": None},
    "royal-palace": {"root_specs": [], "archive_folder_id": None},
    "common-machinery": {
        "root_specs": [(COMPUTER_OF_SERIES_ROOT_ID, True), (HOUSE_OF_SERIES_ROOT_ID, False)],
        "archive_folder_id": COMMON_ARCHIVE_FOLDER_ID,
    },
}

SKIP_TITLE_MARKERS = (
    "ARCHIVED",
    "ARCHIVE & SUPERSEDED",
    "CLOSED / HISTORICAL",
    "TEST /",
    "RECOVERY",
)


class PatrolRuntimeError(RuntimeError):
    pass


class HTTPFailure(PatrolRuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body
        self.url = url


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=f".{path.name}."
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def _json_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    api_key: str | None = None,
    body: Mapping[str, Any] | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as response:
            raw = response.read()
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HTTPFailure(exc.code, raw, url) from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _bytes_request(method: str, url: str, *, token: str) -> bytes:
    req = request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            return response.read()
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HTTPFailure(exc.code, raw, url) from exc


class GoogleWorkspaceGateway:
    def __init__(self, access_token: str):
        if not access_token.strip():
            raise PatrolRuntimeError("Google OAuth access token is required")
        self.token = access_token.strip()

    def file_metadata(self, file_id: str) -> dict[str, Any]:
        fields = "id,name,mimeType,parents,trashed,modifiedTime,createdTime,webViewLink"
        url = (
            f"https://www.googleapis.com/drive/v3/files/{parse.quote(file_id)}"
            f"?supportsAllDrives=true&fields={parse.quote(fields, safe=',')}"
        )
        value = _json_request("GET", url, token=self.token)
        if not isinstance(value, dict):
            raise PatrolRuntimeError(f"invalid metadata response for {file_id}")
        return value

    def list_children(self, parent_id: str) -> list[dict[str, Any]]:
        if parent_id == ROYAL_HOUSEHOLD_FOLDER_ID:
            raise PatrolRuntimeError("Royal Household folder is outside the patrol boundary")
        files: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            query = f"'{parent_id}' in parents and trashed = false"
            params = {
                "q": query,
                "pageSize": "1000",
                "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime,createdTime,trashed)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            url = "https://www.googleapis.com/drive/v3/files?" + parse.urlencode(params)
            value = _json_request("GET", url, token=self.token)
            batch = value.get("files") if isinstance(value, dict) else None
            if not isinstance(batch, list):
                raise PatrolRuntimeError(f"invalid child listing for {parent_id}")
            files.extend(item for item in batch if isinstance(item, dict))
            page_token = value.get("nextPageToken") if isinstance(value, dict) else None
            if not page_token:
                break
        return files

    def _sheet_text(self, spreadsheet_id: str) -> str:
        meta_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{parse.quote(spreadsheet_id)}"
            "?fields=sheets.properties(title,gridProperties(rowCount,columnCount))"
        )
        meta = _json_request("GET", meta_url, token=self.token)
        sheets = meta.get("sheets") if isinstance(meta, dict) else None
        if not isinstance(sheets, list):
            raise PatrolRuntimeError(f"invalid spreadsheet metadata for {spreadsheet_id}")
        parts: list[str] = []
        for sheet in sheets[:8]:
            props = sheet.get("properties") if isinstance(sheet, dict) else None
            title = props.get("title") if isinstance(props, dict) else None
            if not isinstance(title, str) or not title:
                continue
            a1 = f"'{title.replace(chr(39), chr(39)*2)}'!A1:Z120"
            url = (
                f"https://sheets.googleapis.com/v4/spreadsheets/{parse.quote(spreadsheet_id)}"
                f"/values/{parse.quote(a1, safe='')}?majorDimension=ROWS"
            )
            value = _json_request("GET", url, token=self.token)
            rows = value.get("values") if isinstance(value, dict) else None
            if not isinstance(rows, list):
                continue
            rendered = ["\t".join(str(cell) for cell in row) for row in rows if isinstance(row, list)]
            parts.append(f"## SHEET: {title}\n" + "\n".join(rendered))
            if sum(len(part) for part in parts) >= 30000:
                break
        return "\n\n".join(parts)[:30000]

    def file_text(self, metadata: Mapping[str, Any]) -> str:
        file_id = str(metadata.get("id") or "")
        mime = str(metadata.get("mimeType") or "")
        if not file_id:
            return ""
        if mime == "application/vnd.google-apps.document":
            params = parse.urlencode({"mimeType": "text/plain"})
            url = f"https://www.googleapis.com/drive/v3/files/{parse.quote(file_id)}/export?{params}"
            return _bytes_request("GET", url, token=self.token).decode("utf-8", errors="replace")[:30000]
        if mime == "application/vnd.google-apps.spreadsheet":
            return self._sheet_text(file_id)
        if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
            url = f"https://www.googleapis.com/drive/v3/files/{parse.quote(file_id)}?alt=media"
            return _bytes_request("GET", url, token=self.token).decode("utf-8", errors="replace")[:30000]
        return ""

    def move_file(self, file_id: str, *, old_parent: str, new_parent: str) -> dict[str, Any]:
        if new_parent == ROYAL_HOUSEHOLD_FOLDER_ID or old_parent == ROYAL_HOUSEHOLD_FOLDER_ID:
            raise PatrolRuntimeError("Royal Household move is prohibited")
        params = parse.urlencode(
            {
                "addParents": new_parent,
                "removeParents": old_parent,
                "supportsAllDrives": "true",
                "fields": "id,name,parents,mimeType",
            }
        )
        url = f"https://www.googleapis.com/drive/v3/files/{parse.quote(file_id)}?{params}"
        value = _json_request("PATCH", url, token=self.token, body={})
        if not isinstance(value, dict):
            raise PatrolRuntimeError(f"invalid move response for {file_id}")
        return value

    def values_get(self, range_a1: str) -> list[list[Any]]:
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{PALACE_SPREADSHEET_ID}"
            f"/values/{parse.quote(range_a1, safe='')}?majorDimension=ROWS"
        )
        value = _json_request("GET", url, token=self.token)
        rows = value.get("values") if isinstance(value, dict) else None
        return rows if isinstance(rows, list) else []

    def values_update(self, range_a1: str, rows: list[list[Any]]) -> None:
        params = parse.urlencode({"valueInputOption": "RAW"})
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{PALACE_SPREADSHEET_ID}"
            f"/values/{parse.quote(range_a1, safe='')}?{params}"
        )
        _json_request(
            "PUT",
            url,
            token=self.token,
            body={"range": range_a1, "majorDimension": "ROWS", "values": rows},
        )

    def values_append(self, range_a1: str, row: list[Any]) -> None:
        params = parse.urlencode({"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"})
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{PALACE_SPREADSHEET_ID}"
            f"/values/{parse.quote(range_a1, safe='')}:append?{params}"
        )
        _json_request(
            "POST",
            url,
            token=self.token,
            body={"range": range_a1, "majorDimension": "ROWS", "values": [row]},
        )


def _is_text_bearing(mime_type: str) -> bool:
    return mime_type in {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "text/plain",
        "text/csv",
        "application/json",
        "application/xml",
    }


def _title_is_inactive(title: str) -> bool:
    upper = title.upper()
    return any(marker in upper for marker in SKIP_TITLE_MARKERS)


def _collect_candidate_metadata(
    gateway: GoogleWorkspaceGateway,
    root_specs: list[tuple[str, bool]],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    traversed = 0
    for root_id, recursive in root_specs:
        if root_id == ROYAL_HOUSEHOLD_FOLDER_ID:
            raise PatrolRuntimeError("Royal Household root cannot be scanned")
        queue: list[str] = [root_id]
        while queue and traversed < MAX_TRAVERSED_ITEMS:
            parent_id = queue.pop(0)
            for item in gateway.list_children(parent_id):
                traversed += 1
                file_id = str(item.get("id") or "")
                title = str(item.get("name") or "")
                mime = str(item.get("mimeType") or "")
                if file_id == ROYAL_HOUSEHOLD_FOLDER_ID:
                    continue
                if mime == "application/vnd.google-apps.folder":
                    if recursive and not _title_is_inactive(title):
                        queue.append(file_id)
                    continue
                if _is_text_bearing(mime) and not _title_is_inactive(title):
                    collected.append(item)
                if traversed >= MAX_TRAVERSED_ITEMS:
                    break
    collected.sort(key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("id") or "")))
    return collected


def _select_candidate_window(
    candidates: list[dict[str, Any]],
    scan_cursor_before: str | None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    if not candidates:
        return [], None, False
    start = 0
    if scan_cursor_before:
        ids = [str(item.get("id") or "") for item in candidates]
        if scan_cursor_before in ids:
            start = (ids.index(scan_cursor_before) + 1) % len(candidates)
    ordered = candidates[start:] + candidates[:start]
    selected = ordered[:MAX_SCAN_DOCUMENTS]
    truncated = len(candidates) > len(selected)
    cursor_after = str(selected[-1].get("id") or "") if selected and truncated else None
    return selected, cursor_after or None, truncated


def _find_previous_scan_cursor(state: Mapping[str, Any], estate_id: str) -> str | None:
    hits: list[tuple[str, str | None]] = []
    events = state.get("events")
    if not isinstance(events, Mapping):
        return None
    for event in events.values():
        if not isinstance(event, Mapping):
            continue
        receipt = event.get("receipt")
        patrol = receipt.get("integrity_patrol") if isinstance(receipt, Mapping) else None
        if not isinstance(patrol, Mapping) or patrol.get("estate_id") != estate_id:
            continue
        hits.append((str(event.get("scheduled_for") or ""), patrol.get("scan_cursor_after")))
    if not hits:
        return None
    hits.sort()
    value = hits[-1][1]
    return str(value) if value else None


def _current_month_cost_usd(state: Mapping[str, Any], now: datetime) -> float:
    month = now.astimezone(timezone.utc).strftime("%Y-%m")
    total = 0.0
    events = state.get("events")
    if not isinstance(events, Mapping):
        return total
    for event in events.values():
        if not isinstance(event, Mapping):
            continue
        receipt = event.get("receipt")
        if not isinstance(receipt, Mapping):
            continue
        completed_at = str(receipt.get("completed_at") or "")
        if not completed_at.startswith(month):
            continue
        usage = receipt.get("model_usage")
        if isinstance(usage, Mapping):
            try:
                total += float(usage.get("conservative_cost_usd") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(total, 8)


def _model_usage(response: Mapping[str, Any]) -> dict[str, Any]:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise PatrolRuntimeError("OpenAI response omitted usage")
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cost = (input_tokens / 1_000_000) * INPUT_USD_PER_MILLION + (
        output_tokens / 1_000_000
    ) * OUTPUT_USD_PER_MILLION
    return {
        "model": MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "conservative_cost_usd": round(cost, 8),
        "pricing_basis": {
            "input_usd_per_million": INPUT_USD_PER_MILLION,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION,
            "cached_input_discount_ignored_for_guard": True,
        },
        "software_hard_cap_usd": SOFTWARE_HARD_CAP_USD,
    }


def _extract_output_text(response: Mapping[str, Any]) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        raise PatrolRuntimeError("OpenAI response output is missing")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise PatrolRuntimeError("OpenAI response produced no output_text")
    return "".join(chunks)


def _classification_schema() -> dict[str, Any]:
    finding = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "classification": {
                "type": "string",
                "enum": [
                    "CURRENT",
                    "STALE/SUPERSEDED",
                    "DUPLICATE-CURRENT",
                    "CONFLICTING CURRENT MATERIAL",
                ],
            },
            "file_id": {"type": "string"},
            "source_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "proposition": {"type": "string"},
            "reason": {"type": "string"},
            "recommended_action": {
                "type": "string",
                "enum": ["NO_ACTION", "ARCHIVE", "QUEUE_CONFLICT"],
            },
        },
        "required": [
            "classification",
            "file_id",
            "source_refs",
            "proposition",
            "reason",
            "recommended_action",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": finding, "maxItems": 12},
        },
        "required": ["summary", "findings"],
    }


def _call_model(api_key: str, packet: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    packet_text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    packet_text = packet_text[:MAX_MODEL_INPUT_CHARS]
    instructions = (
        "You are Queen Barbara Admin acting through COS-BUS-001 for the bounded Dynasty State & Conflict Patrol. "
        "Use ONLY the supplied packet. Current authority documents outrank candidate material. Never infer precedence from timestamps alone. "
        "Do not treat TEST, RECOVERY, HISTORICAL, ARCHIVED or explicitly superseded material as current authority unless fixture_mode=true, "
        "in which case fixture titles are controlled test facts only. Report only material findings with identifiable source refs. "
        "STALE/SUPERSEDED means competent current authority unambiguously supersedes the candidate. DUPLICATE-CURRENT means two active copies "
        "purport to be current without a justified canonical reason. CONFLICTING CURRENT MATERIAL means two competent current records make materially "
        "incompatible claims. Never resolve a genuine conflict. Do not propose any action outside NO_ACTION, ARCHIVE, or QUEUE_CONFLICT."
    )
    body = {
        "model": MODEL,
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": instructions}]},
            {"role": "user", "content": [{"type": "input_text", "text": packet_text}]},
        ],
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "aldernia_patrol_classification",
                "strict": True,
                "schema": _classification_schema(),
            },
        },
    }
    response = _json_request("POST", "https://api.openai.com/v1/responses", api_key=api_key, body=body)
    if not isinstance(response, dict):
        raise PatrolRuntimeError("invalid OpenAI response")
    parsed = json.loads(_extract_output_text(response))
    if not isinstance(parsed, dict):
        raise PatrolRuntimeError("structured model output is not an object")
    return parsed, _model_usage(response)


def _queue_conflict(
    gateway: GoogleWorkspaceGateway,
    *,
    conflict_id: str,
    proposition: str,
    source_refs: list[str],
    estate_label: str,
    synthetic: bool = False,
) -> tuple[str, bool]:
    rows = gateway.values_get("'King''s Consideration Queue'!A1:L500")
    matches: list[str] = []
    for row in rows:
        if any(conflict_id in str(cell) for cell in row):
            matches.append(str(row[0]) if row else "")
    if len(matches) > 1:
        raise PatrolRuntimeError(f"conflict dedup invariant broken for {conflict_id}")
    if matches:
        return matches[0] or f"KQ-PATROL-{conflict_id[-12:]}", True

    queue_id = f"KQ-PATROL-{conflict_id[-12:]}"
    today = datetime.now(timezone.utc).date().isoformat()
    prefix = "SYNTHETIC ACCEPTANCE TEST — " if synthetic else ""
    row = [
        queue_id,
        today,
        f"{prefix}Dynasty State & Conflict Patrol / {estate_label}",
        f"{prefix}{proposition}",
        "Two controlled current-material claims are materially incompatible; the patrol is forbidden to silently resolve them.",
        "Computer of Series / Queen Barbara Admin → Royal Palace / King's Balcony",
        f"Stable conflict ID {conflict_id}; sources: " + "; ".join(source_refs),
        "CLOSED" if synthetic else "READY FOR KING",
        REVIEW_TRIGGER,
        "",
        "Synthetic dedup acceptance evidence only." if synthetic else "Await Crown consideration; no automatic resolution.",
        "SYNTHETIC / NON-DECISION" if synthetic else "OPEN — patrol conflict; current authority unchanged pending decision.",
    ]
    gateway.values_append("'King''s Consideration Queue'!A:L", row)
    verify = gateway.values_get("'King''s Consideration Queue'!A1:L500")
    hits = [row for row in verify if any(conflict_id in str(cell) for cell in row)]
    if len(hits) != 1:
        raise PatrolRuntimeError(f"conflict queue readback expected one row for {conflict_id}, saw {len(hits)}")
    return queue_id, True


def _archive_finding(
    gateway: GoogleWorkspaceGateway,
    *,
    file_id: str,
    archive_folder_id: str | None,
    protected_source_ids: set[str],
) -> dict[str, Any]:
    if not file_id:
        return {
            "disposition": "STOP_NO_SAFE_ARCHIVE",
            "provenance_preserved": True,
            "readback_verified": True,
            "destructive_delete": False,
            "archive_error": "finding did not identify a file_id",
        }
    if file_id in protected_source_ids:
        return {
            "disposition": "STOP_NO_SAFE_ARCHIVE",
            "provenance_preserved": True,
            "readback_verified": True,
            "destructive_delete": False,
            "archive_error": "candidate is a required current-authority source and may not be archived",
        }
    if not archive_folder_id:
        return {
            "disposition": "STOP_NO_SAFE_ARCHIVE",
            "provenance_preserved": True,
            "readback_verified": True,
            "destructive_delete": False,
            "archive_error": "no competent writable archive surface is configured for this estate",
        }
    meta = gateway.file_metadata(file_id)
    parents = meta.get("parents") if isinstance(meta.get("parents"), list) else []
    if archive_folder_id in parents:
        return {
            "disposition": "ARCHIVED",
            "provenance_preserved": True,
            "readback_verified": True,
            "destructive_delete": False,
            "archive_file_id": file_id,
            "archive_folder_id": archive_folder_id,
        }
    if len(parents) != 1:
        return {
            "disposition": "STOP_NO_SAFE_ARCHIVE",
            "provenance_preserved": True,
            "readback_verified": True,
            "destructive_delete": False,
            "archive_error": "candidate does not have one unambiguous current parent",
        }
    try:
        moved = gateway.move_file(file_id, old_parent=str(parents[0]), new_parent=archive_folder_id)
    except HTTPFailure as exc:
        if exc.status in {401, 403, 404}:
            return {
                "disposition": "STOP_NO_SAFE_ARCHIVE",
                "provenance_preserved": True,
                "readback_verified": True,
                "destructive_delete": False,
                "archive_error": f"bounded archive permission unavailable: HTTP {exc.status}",
            }
        raise
    readback = gateway.file_metadata(file_id)
    readback_parents = readback.get("parents") if isinstance(readback.get("parents"), list) else []
    if moved.get("id") != file_id or readback.get("id") != file_id or archive_folder_id not in readback_parents:
        raise PatrolRuntimeError("archive move readback failed")
    return {
        "disposition": "ARCHIVED",
        "provenance_preserved": True,
        "readback_verified": True,
        "destructive_delete": False,
        "archive_file_id": file_id,
        "archive_folder_id": archive_folder_id,
    }


def _finalize_findings(
    gateway: GoogleWorkspaceGateway,
    *,
    model_result: Mapping[str, Any],
    plan: Mapping[str, Any],
    archive_folder_id: str | None,
    synthetic_conflicts: bool = False,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    raw_findings = model_result.get("findings")
    if not isinstance(raw_findings, list):
        raise PatrolRuntimeError("model findings missing")
    finalized: list[dict[str, Any]] = []
    writes: list[str] = []
    stopped = False
    protected = {str(value) for value in plan.get("required_source_ids") or []}
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            raise PatrolRuntimeError("model returned a non-object finding")
        classification = str(raw.get("classification") or "")
        source_refs = [str(value) for value in raw.get("source_refs") or []]
        base = {
            "classification": classification,
            "source_refs": source_refs,
            "reason": str(raw.get("reason") or ""),
        }
        if classification == "CURRENT":
            base["disposition"] = "NO_ACTION"
            finalized.append(base)
            continue
        if classification in {"STALE/SUPERSEDED", "DUPLICATE-CURRENT"}:
            archive = _archive_finding(
                gateway,
                file_id=str(raw.get("file_id") or ""),
                archive_folder_id=archive_folder_id,
                protected_source_ids=protected,
            )
            base.update(archive)
            if archive["disposition"] == "ARCHIVED":
                writes.append(f"Drive archive move:{archive['archive_file_id']}->{archive_folder_id}")
            else:
                stopped = True
            finalized.append(base)
            continue
        if classification == "CONFLICTING CURRENT MATERIAL":
            proposition = str(raw.get("proposition") or "").strip()
            if not proposition or len(source_refs) < 2:
                raise PatrolRuntimeError("conflict finding lacks proposition or source refs")
            conflict_id = stable_conflict_id(proposition, source_refs)
            queue_ref, deduped = _queue_conflict(
                gateway,
                conflict_id=conflict_id,
                proposition=proposition,
                source_refs=source_refs,
                estate_label=str(plan.get("estate_label") or plan.get("estate_id") or "Dynasty"),
                synthetic=synthetic_conflicts,
            )
            base.update(
                {
                    "proposition": proposition,
                    "conflict_id": conflict_id,
                    "disposition": "QUEUED_FOR_CROWN",
                    "queue_review_trigger": REVIEW_TRIGGER,
                    "queue_ref": queue_ref,
                    "queue_deduplicated": deduped,
                    "readback_verified": True,
                }
            )
            writes.append(f"King's Consideration Queue:{queue_ref}")
            finalized.append(base)
            continue
        raise PatrolRuntimeError(f"unsupported classification {classification!r}")
    return finalized, writes, stopped


def _next_pending_heartbeat(state: Mapping[str, Any]) -> str | None:
    candidates: list[tuple[str, str]] = []
    events = state.get("events")
    if not isinstance(events, Mapping):
        return None
    for event_id, event in events.items():
        if not isinstance(event, Mapping) or event.get("status") != "PENDING_RUNTIME":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("duty_id") != "dynasty.heartbeat":
            continue
        candidates.append((str(event.get("scheduled_for") or ""), str(event_id)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _source_packet(gateway: GoogleWorkspaceGateway, ids: list[str]) -> list[dict[str, Any]]:
    packet: list[dict[str, Any]] = []
    for file_id in ids:
        if file_id == ROYAL_HOUSEHOLD_FOLDER_ID:
            raise PatrolRuntimeError("Royal Household source injection is prohibited")
        meta = gateway.file_metadata(file_id)
        packet.append(
            {
                "id": file_id,
                "name": meta.get("name"),
                "mimeType": meta.get("mimeType"),
                "modifiedTime": meta.get("modifiedTime"),
                "text": gateway.file_text(meta),
            }
        )
    return packet


def _candidate_packet(
    gateway: GoogleWorkspaceGateway,
    *,
    estate_id: str,
    scan_cursor_before: str | None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    bounds = ESTATE_RUNTIME_BOUNDS.get(estate_id)
    if not bounds:
        raise PatrolRuntimeError(f"no runtime bounds configured for {estate_id}")
    metadata = _collect_candidate_metadata(gateway, list(bounds.get("root_specs") or []))
    selected, cursor_after, truncated = _select_candidate_window(metadata, scan_cursor_before)
    packet: list[dict[str, Any]] = []
    for meta in selected:
        packet.append(
            {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "mimeType": meta.get("mimeType"),
                "modifiedTime": meta.get("modifiedTime"),
                "parents": meta.get("parents"),
                "text": gateway.file_text(meta),
            }
        )
    return packet, cursor_after, truncated


def _update_row21(
    gateway: GoogleWorkspaceGateway,
    *,
    event_id: str,
    outcome: str,
    estate_label: str,
    cursor_after_estate: str,
    scan_cursor_after: str | None,
    model_usage: Mapping[str, Any],
    findings: list[dict[str, Any]],
    writes: list[str],
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence = (
        f"UNATTENDED EVENT {event_id}; estate={estate_label}; scan_cursor_after={scan_cursor_after or 'SECTOR COMPLETE'}; "
        f"model={model_usage.get('model')}; input_tokens={model_usage.get('input_tokens')}; output_tokens={model_usage.get('output_tokens')}; "
        f"conservative_cost_usd={model_usage.get('conservative_cost_usd')}; writes={'; '.join(writes) if writes else 'NONE'}; "
        f"Royal Household folder {ROYAL_HOUSEHOLD_FOLDER_ID} excluded by code boundary."
    )
    rows = [[
        "ACTIVE — OPTION B FULL UNATTENDED PATROL / NATIVE HEARTBEAT / KEYLESS GOOGLE WIF / BOUNDED MODEL RUNTIME / NO CHATGPT SCHEDULER",
        now,
        f"{outcome} — one bounded unattended patrol slice completed for {estate_label}; findings={len(findings)}.",
        evidence,
        f"NEXT ESTATE: {cursor_after_estate}; scan continuation={scan_cursor_after or 'fresh sector on next estate cycle'}.",
    ]]
    gateway.values_update("'Scheduled Tasks'!G21:K21", rows)
    readback = gateway.values_get("'Scheduled Tasks'!G21:K21")
    if not readback or readback[0][:5] != rows[0]:
        raise PatrolRuntimeError("Scheduled Tasks row 21 readback failed")


def run_once(
    *,
    state_path: Path,
    timetable_path: Path,
    session_path: Path,
    google_access_token: str,
    openai_api_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    event_id = _next_pending_heartbeat(state)
    if event_id is None:
        return {"status": "NO_PENDING_HEARTBEAT"}

    spend = _current_month_cost_usd(state, current)
    if spend + MAX_RESERVED_REQUEST_COST_USD > SOFTWARE_HARD_CAP_USD:
        return {
            "status": "STOP_COST_CAP",
            "event_id": event_id,
            "month_cost_usd": spend,
            "software_hard_cap_usd": SOFTWARE_HARD_CAP_USD,
        }

    claim = claim_event(
        state_path=state_path,
        timetable_path=timetable_path,
        session_path=session_path,
        event_id=event_id,
        worker_id="github-central-clock-option-b",
        runtime_class=PERSISTENT_MODEL_RUNTIME_CLASS,
        now=current,
    )
    plan = claim.get("integrity_patrol")
    if not isinstance(plan, Mapping):
        raise PatrolRuntimeError("heartbeat claim did not return an integrity patrol plan")
    estate_id = str(plan.get("estate_id") or "")
    bounds = ESTATE_RUNTIME_BOUNDS.get(estate_id)
    if bounds is None:
        raise PatrolRuntimeError(f"unknown estate runtime bounds {estate_id!r}")

    gateway = GoogleWorkspaceGateway(google_access_token)
    required_ids = [str(value) for value in claim.get("required_source_ids") or []]
    authorities = _source_packet(gateway, required_ids)
    state_after_claim = json.loads(state_path.read_text(encoding="utf-8"))
    scan_cursor_before = _find_previous_scan_cursor(state_after_claim, estate_id)
    candidates, scan_cursor_after, scan_truncated = _candidate_packet(
        gateway,
        estate_id=estate_id,
        scan_cursor_before=scan_cursor_before,
    )
    packet = {
        "fixture_mode": False,
        "patrol_plan": dict(plan),
        "scan_cursor_before": scan_cursor_before,
        "scan_cursor_after_if_completed": scan_cursor_after,
        "scan_truncated": scan_truncated,
        "current_authorities": authorities,
        "candidate_material": candidates,
    }
    model_result, usage = _call_model(openai_api_key, packet)
    if spend + float(usage["conservative_cost_usd"]) > SOFTWARE_HARD_CAP_USD:
        raise PatrolRuntimeError("model response would breach the software hard spend cap")

    findings, writes, stopped = _finalize_findings(
        gateway,
        model_result=model_result,
        plan=plan,
        archive_folder_id=bounds.get("archive_folder_id"),
    )
    outcome = "STOP" if stopped else ("ACTION" if writes else "NO_ACTION")
    patrol_result = {
        "registry_version": plan["registry_version"],
        "source_row": plan["source_row"],
        "estate_id": plan["estate_id"],
        "cursor_after": plan["cursor_after"],
        "current_authority_retrieved": True,
        "royal_household_accessed": False,
        "readback_verified": True,
        "findings": findings,
        "scan_cursor_before": scan_cursor_before,
        "scan_cursor_after": scan_cursor_after,
        "scan_truncated": scan_truncated,
    }
    _update_row21(
        gateway,
        event_id=event_id,
        outcome=outcome,
        estate_label=str(plan.get("estate_label") or estate_id),
        cursor_after_estate=str(plan.get("cursor_after") or ""),
        scan_cursor_after=scan_cursor_after,
        model_usage=usage,
        findings=findings,
        writes=writes,
    )
    execution_result = {
        "outcome": outcome,
        "result_summary": str(model_result.get("summary") or "Bounded unattended patrol slice completed."),
        "retrieved_source_ids": required_ids,
        "evidence_refs": [
            f"GitHub:{event_id}",
            f"Drive:estate:{estate_id}",
            "Drive:Scheduled Tasks row 21 readback",
        ],
        "writes": writes + ["Drive:Scheduled Tasks row 21"],
        "readback_verified": True,
        "resource_classes_used": ["ALDERNIA_INTERNAL"],
        "external_effect": "NONE",
        "new_model_provider_access_granted": False,
        "integrity_patrol": patrol_result,
        "model_usage": usage,
    }
    ack = acknowledge_event(
        state_path=state_path,
        timetable_path=timetable_path,
        session_path=session_path,
        event_id=event_id,
        claim_id=str(claim["claim_id"]),
        execution_result=execution_result,
        now=datetime.now(timezone.utc),
    )
    readback_state = json.loads(state_path.read_text(encoding="utf-8"))
    receipt = readback_state["events"][event_id].get("receipt")
    if not isinstance(receipt, Mapping) or receipt.get("readback_verified") is not True:
        raise PatrolRuntimeError("durable worker receipt readback failed")
    return {
        "status": ack["status"],
        "event_id": event_id,
        "estate_id": estate_id,
        "cursor_after": plan.get("cursor_after"),
        "scan_cursor_after": scan_cursor_after,
        "findings": len(findings),
        "model_usage": usage,
        "writes": writes,
    }


def run_fixture_test(*, google_access_token: str, openai_api_key: str) -> dict[str, Any]:
    gateway = GoogleWorkspaceGateway(google_access_token)
    try:
        gateway.file_metadata(ROYAL_HOUSEHOLD_FOLDER_ID)
    except HTTPFailure as exc:
        if exc.status not in {403, 404}:
            raise
        royal_household_denied = True
    else:
        royal_household_denied = False
    if not royal_household_denied:
        raise PatrolRuntimeError("Royal Household denial acceptance failed")

    stale_meta = gateway.file_metadata(FIXTURE_STALE_FILE_ID)
    a_meta = gateway.file_metadata(FIXTURE_CONFLICT_A_FILE_ID)
    b_meta = gateway.file_metadata(FIXTURE_CONFLICT_B_FILE_ID)
    fixture_plan = {
        "registry_version": 1,
        "source_row": 21,
        "estate_id": "common-machinery",
        "estate_label": "Computer of Series acceptance fixtures",
        "cursor_after": "house-of-carol",
        "required_source_ids": [],
    }
    packet = {
        "fixture_mode": True,
        "patrol_plan": fixture_plan,
        "current_authorities": [],
        "candidate_material": [
            {"id": stale_meta["id"], "name": stale_meta["name"], "text": "Controlled stale fixture; safe to archive."},
            {"id": a_meta["id"], "name": a_meta["name"], "text": "PATROL-FIXTURE-OWNER = HOUSE OF CAROL"},
            {"id": b_meta["id"], "name": b_meta["name"], "text": "PATROL-FIXTURE-OWNER = HOUSE OF TONY"},
        ],
    }
    model_result, usage = _call_model(openai_api_key, packet)

    classifications = [
        str(item.get("classification") or "")
        for item in model_result.get("findings") or []
        if isinstance(item, Mapping)
    ]
    if "STALE/SUPERSEDED" not in classifications or "CONFLICTING CURRENT MATERIAL" not in classifications:
        raise PatrolRuntimeError("fixture model classification did not surface both required test conditions")

    findings, writes, stopped = _finalize_findings(
        gateway,
        model_result=model_result,
        plan=fixture_plan,
        archive_folder_id=FIXTURE_ARCHIVE_FOLDER_ID,
        synthetic_conflicts=True,
    )
    if stopped:
        raise PatrolRuntimeError("fixture archive unexpectedly stopped")
    archived = gateway.file_metadata(FIXTURE_STALE_FILE_ID)
    if FIXTURE_ARCHIVE_FOLDER_ID not in (archived.get("parents") or []):
        raise PatrolRuntimeError("fixture archive readback failed")
    gateway.move_file(
        FIXTURE_STALE_FILE_ID,
        old_parent=FIXTURE_ARCHIVE_FOLDER_ID,
        new_parent=FIXTURE_ACTIVE_FOLDER_ID,
    )
    restored = gateway.file_metadata(FIXTURE_STALE_FILE_ID)
    if FIXTURE_ACTIVE_FOLDER_ID not in (restored.get("parents") or []):
        raise PatrolRuntimeError("fixture archive rollback readback failed")

    conflict_findings = [f for f in findings if f.get("classification") == "CONFLICTING CURRENT MATERIAL"]
    if len(conflict_findings) != 1:
        raise PatrolRuntimeError("fixture conflict did not resolve to exactly one controlled finding")
    conflict = conflict_findings[0]
    _queue_conflict(
        gateway,
        conflict_id=str(conflict["conflict_id"]),
        proposition=str(conflict["proposition"]),
        source_refs=list(conflict["source_refs"]),
        estate_label="Computer of Series acceptance fixtures",
        synthetic=True,
    )
    rows = gateway.values_get("'King''s Consideration Queue'!A1:L500")
    count = sum(1 for row in rows if any(str(conflict["conflict_id"]) in str(cell) for cell in row))
    if count != 1:
        raise PatrolRuntimeError("fixture replay/dedup acceptance failed")
    return {
        "status": "PASS",
        "royal_household_denied": True,
        "archive_reversible": True,
        "conflict_deduplicated": True,
        "conflict_id": conflict["conflict_id"],
        "model_usage": usage,
        "writes": writes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent bounded Option-B Dynasty State & Conflict Patrol runtime")
    parser.add_argument("--state", default="state/scheduled-duty-queue.json")
    parser.add_argument("--timetable", default="aldernia/schedule/timetable.json")
    parser.add_argument("--session", default="state/bus-session.json")
    parser.add_argument("--fixture-test", action="store_true")
    args = parser.parse_args(argv)

    google_token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not google_token or not openai_key:
        raise PatrolRuntimeError("GOOGLE_OAUTH_ACCESS_TOKEN and OPENAI_API_KEY are required")
    if args.fixture_test:
        result = run_fixture_test(google_access_token=google_token, openai_api_key=openai_key)
    else:
        result = run_once(
            state_path=Path(args.state),
            timetable_path=Path(args.timetable),
            session_path=Path(args.session),
            google_access_token=google_token,
            openai_api_key=openai_key,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
