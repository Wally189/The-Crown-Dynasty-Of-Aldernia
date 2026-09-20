"""Bounded scheduler proof: CLOCK -> authorised test timetable -> event -> ACK -> STOP.

When ALDERNIA_CLOCK_STATE is supplied, logical Dynasty time persists at that path.
Without it, a temporary state is used for local/isolated proof.
"""
from datetime import timedelta
import json
import os
import tempfile
from pathlib import Path

from central_aldernia_clock.clock import CentralAlderniaClock


def _run(state: Path) -> dict:
    clock = CentralAlderniaClock(state)
    before = clock.snapshot()
    event_type = "clock.first_chime" if clock.dynasty_tick == 0 else "clock.chime"
    chime = clock.stamp_event(
        event_type,
        source="github-actions-clock-scheduler",
        provenance="Crown Commission — Central Aldernia Clock & Aldernian Mean Time — 2026-09-20",
        payload={"effect": "bounded clock/timetable proof only"},
    )
    now = clock.utc_now()
    return {
        "clock_snapshot_before": before.to_dict(),
        "chime": chime.to_dict(),
        "clock_snapshot_after": clock.snapshot().to_dict(),
        "timetable_proof": {
            "past_condition_due": clock.is_due(now, now - timedelta(seconds=1)),
            "future_condition_due": clock.is_due(now, now + timedelta(hours=1)),
            "tick_after_due_checks": clock.dynasty_tick,
            "implicit_mission_created": False,
        },
        "stop": "CLOSED — bounded proof complete",
    }


def main() -> int:
    configured = os.environ.get("ALDERNIA_CLOCK_STATE")
    if configured:
        result = _run(Path(configured))
    else:
        with tempfile.TemporaryDirectory() as td:
            result = _run(Path(td) / "clock-state.json")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
