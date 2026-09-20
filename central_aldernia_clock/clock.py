from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo
import json
import os
import tempfile
import time
import uuid

CLOCK_ID = "central-aldernia-clock"
AMT_ZONE_NAME = "Europe/London"
AMT_ZONE = ZoneInfo(AMT_ZONE_NAME)
STATE_VERSION = 1

class ClockStateError(RuntimeError):
    """Raised when persisted logical clock state is absent, malformed or unsafe."""

@dataclass(frozen=True)
class ClockSnapshot:
    clock_id: str
    utc_timestamp: str
    amt_timestamp: str
    timezone: str
    utc_offset: str
    civil_regime: str
    calendar_date: str
    day_of_week: str
    unix_timestamp: int
    dynasty_tick: int
    previous_dynasty_tick: int | None
    state_version: int
    generated_at: str
    clock_status: str
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class StampedEvent:
    event_id: str
    event_type: str
    source: str
    provenance: str
    utc_timestamp: str
    amt_timestamp: str
    dynasty_tick: int
    payload: Mapping[str, Any]
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

class CentralAlderniaClock:
    """Deterministic Central Aldernia clock.

    Wall-clock time comes from the runtime system clock and IANA Europe/London.
    Logical Dynasty time advances only on an explicit authorised call.
    """
    def __init__(self, state_path: str | os.PathLike[str] | None = None) -> None:
        self.state_path = Path(state_path) if state_path else None
        self._state = {"state_version": STATE_VERSION, "dynasty_tick": 0, "previous_dynasty_tick": None, "last_event_id": None}
        if self.state_path:
            self._load_or_initialise_state()

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def monotonic_now() -> float:
        return time.monotonic()

    @staticmethod
    def to_amt(instant: datetime) -> datetime:
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        return instant.astimezone(AMT_ZONE)

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    @staticmethod
    def _offset_string(dt: datetime) -> str:
        offset = dt.utcoffset()
        if offset is None:
            raise ValueError("timezone-aware datetime required")
        seconds = int(offset.total_seconds())
        sign = "+" if seconds >= 0 else "-"
        seconds = abs(seconds)
        hours, rem = divmod(seconds, 3600)
        return f"{sign}{hours:02d}:{rem // 60:02d}"

    def _validate_state(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ClockStateError("clock state must be a JSON object")
        if data.get("state_version") != STATE_VERSION:
            raise ClockStateError("unsupported clock state version")
        tick, previous = data.get("dynasty_tick"), data.get("previous_dynasty_tick")
        if not isinstance(tick, int) or tick < 0:
            raise ClockStateError("dynasty_tick must be a non-negative integer")
        if previous is not None and (not isinstance(previous, int) or previous < 0):
            raise ClockStateError("previous_dynasty_tick must be null or non-negative integer")
        if previous is not None and previous > tick:
            raise ClockStateError("logical clock would move backwards")
        return {"state_version": STATE_VERSION, "dynasty_tick": tick, "previous_dynasty_tick": previous, "last_event_id": data.get("last_event_id")}

    def _load_or_initialise_state(self) -> None:
        assert self.state_path is not None
        if not self.state_path.exists():
            self._persist_state()
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ClockStateError(f"could not read clock state safely: {exc}") from exc
        self._state = self._validate_state(data)

    def _persist_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._state, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent, prefix=f".{self.state_path.name}.", delete=False) as tmp:
            tmp.write(payload)
            temp_name = tmp.name
        os.replace(temp_name, self.state_path)

    @property
    def dynasty_tick(self) -> int:
        return int(self._state["dynasty_tick"])

    @property
    def previous_dynasty_tick(self) -> int | None:
        value = self._state["previous_dynasty_tick"]
        return int(value) if value is not None else None

    def snapshot(self, instant: datetime | None = None) -> ClockSnapshot:
        utc = instant.astimezone(timezone.utc) if instant else self.utc_now()
        amt = self.to_amt(utc)
        return ClockSnapshot(
            clock_id=CLOCK_ID, utc_timestamp=self._iso(utc), amt_timestamp=self._iso(amt),
            timezone=AMT_ZONE_NAME, utc_offset=self._offset_string(amt), civil_regime=amt.tzname() or "UNKNOWN",
            calendar_date=amt.date().isoformat(), day_of_week=amt.strftime("%A"), unix_timestamp=int(utc.timestamp()),
            dynasty_tick=self.dynasty_tick, previous_dynasty_tick=self.previous_dynasty_tick,
            state_version=STATE_VERSION, generated_at=self._iso(self.utc_now()), clock_status="OPERATING")

    def advance_tick(self) -> tuple[int, int | None]:
        old, new = self.dynasty_tick, self.dynasty_tick + 1
        self._state["previous_dynasty_tick"] = old
        self._state["dynasty_tick"] = new
        self._persist_state()
        return new, old

    def stamp_event(self, event_type: str, *, source: str, provenance: str, payload: Mapping[str, Any] | None = None, advance: bool = True, instant: datetime | None = None) -> StampedEvent:
        tick = self.advance_tick()[0] if advance else self.dynasty_tick
        snap = self.snapshot(instant)
        event_id = f"ald-{uuid.uuid4()}"
        self._state["last_event_id"] = event_id
        self._persist_state()
        return StampedEvent(event_id, event_type, source, provenance, snap.utc_timestamp, snap.amt_timestamp, tick, dict(payload or {}))

    @staticmethod
    def elapsed_seconds(start_monotonic: float, end_monotonic: float | None = None) -> float:
        end = time.monotonic() if end_monotonic is None else end_monotonic
        if end < start_monotonic:
            raise ValueError("monotonic end precedes start")
        return end - start_monotonic

    @staticmethod
    def is_due(now: datetime, due_at: datetime) -> bool:
        if now.tzinfo is None or due_at.tzinfo is None:
            raise ValueError("now and due_at must be timezone-aware")
        return now.astimezone(timezone.utc) >= due_at.astimezone(timezone.utc)

    @staticmethod
    def compare(first: datetime, second: datetime) -> int:
        if first.tzinfo is None or second.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware")
        a, b = first.astimezone(timezone.utc), second.astimezone(timezone.utc)
        return -1 if a < b else (1 if a > b else 0)
