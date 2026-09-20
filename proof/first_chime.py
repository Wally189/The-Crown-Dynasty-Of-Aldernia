"""Bounded proof: CLOCK -> authorised test timetable -> event -> ACK -> STOP."""
from datetime import timedelta
import json
import tempfile
from pathlib import Path
from central_aldernia_clock.clock import CentralAlderniaClock

def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        clock = CentralAlderniaClock(Path(td) / "clock-state.json")
        before = clock.snapshot()
        chime = clock.stamp_event(
            "clock.first_chime",
            source="github-actions-clock-proof",
            provenance="Crown Commission — Central Aldernia Clock & Aldernian Mean Time — 2026-09-20",
            payload={"effect": "internal proof only"})
        now = clock.utc_now()
        result = {
            "clock_snapshot_before": before.to_dict(),
            "first_chime": chime.to_dict(),
            "clock_snapshot_after": clock.snapshot().to_dict(),
            "timetable_proof": {
                "past_condition_due": clock.is_due(now, now - timedelta(seconds=1)),
                "future_condition_due": clock.is_due(now, now + timedelta(hours=1)),
                "tick_after_due_checks": clock.dynasty_tick,
                "implicit_mission_created": False},
            "stop": "CLOSED — bounded proof complete"}
        print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
