from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.world_state import (
    AUTHORITY_REF,
    EFFECT_DYNAMIC_WORLD,
    EXPERIMENT_SCOPE,
    PLACE_KESTRELS,
    PLACE_ST_BRIGIDS,
    TEST_FERRY_ID,
    EffectGateRejected,
    TransitionRejected,
    WorldRuntime,
    assert_effect_allowed,
    ordered_scheduled_events,
    read_journal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = [
    REPO_ROOT / "index.html",
    REPO_ROOT / "aldernia" / "public-state.json",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_proof() -> dict:
    public_before = {str(path.relative_to(REPO_ROOT)): digest(path) for path in PUBLIC_FILES}

    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    t_arrive = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
    t_before = datetime(2026, 9, 20, 12, 44, 59, tzinfo=timezone.utc)
    command_id = "proof-journey-001"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        clock_path = root / "clock-state.json"
        world_root = root / "world"

        clock = CentralAlderniaClock(clock_path)
        runtime = WorldRuntime(world_root, clock)
        initial = runtime.current_state()

        assert initial["entities"][TEST_FERRY_ID]["location"] == PLACE_ST_BRIGIDS
        assert initial["entities"][TEST_FERRY_ID]["movement_state"] == "DOCKED"
        assert runtime.can_depart(PLACE_ST_BRIGIDS, PLACE_KESTRELS)
        return_before = runtime.can_depart(PLACE_KESTRELS, PLACE_ST_BRIGIDS)
        assert return_before is False

        invalid_rejection = None
        try:
            runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination="place:aldernia:merrow",
                effective_time=t0,
                arrival_time=t_arrive,
                command_id="invalid-no-route",
            )
        except TransitionRejected as exc:
            invalid_rejection = str(exc)
        assert invalid_rejection == "no valid route exists between origin and destination"

        departure = runtime.depart(
            origin=PLACE_ST_BRIGIDS,
            destination=PLACE_KESTRELS,
            effective_time=t0,
            arrival_time=t_arrive,
            command_id=command_id,
        )
        in_transit = runtime.current_state()
        assert in_transit["entities"][TEST_FERRY_ID]["location"] is None
        assert in_transit["entities"][TEST_FERRY_ID]["movement_state"] == "IN_TRANSIT"
        assert len(in_transit["scheduled_events"]) == 1

        journal_after_departure = (world_root / "world-journal.jsonl").read_bytes()
        duplicate = runtime.depart(
            origin=PLACE_ST_BRIGIDS,
            destination=PLACE_KESTRELS,
            effective_time=t0,
            arrival_time=t_arrive,
            command_id=command_id,
        )
        assert duplicate["event_id"] == departure["event_id"]
        assert (world_root / "world-journal.jsonl").read_bytes() == journal_after_departure

        del runtime
        del clock

        restarted_clock = CentralAlderniaClock(clock_path)
        restarted = WorldRuntime(world_root, restarted_clock)
        reconstructed_in_transit = restarted.current_state()
        assert reconstructed_in_transit == in_transit
        assert restarted.process_due(now=t_before) == []

        arrivals = restarted.process_due(now=t_arrive)
        assert len(arrivals) == 1
        arrival = arrivals[0]
        final_state = restarted.current_state()
        ferry = final_state["entities"][TEST_FERRY_ID]
        assert ferry["location"] == PLACE_KESTRELS
        assert ferry["movement_state"] == "DOCKED"
        assert ferry["journey"] is None
        return_after = restarted.can_depart(PLACE_KESTRELS, PLACE_ST_BRIGIDS)
        assert return_after is True
        assert restarted.can_depart(PLACE_ST_BRIGIDS, PLACE_KESTRELS) is False

        journal_after_arrival = (world_root / "world-journal.jsonl").read_bytes()
        replayed = WorldRuntime.replay(world_root / "world-journal.jsonl")
        assert replayed == final_state
        assert (world_root / "world-journal.jsonl").read_bytes() == journal_after_arrival

        del restarted
        del restarted_clock

        second_restart_clock = CentralAlderniaClock(clock_path)
        second_restart = WorldRuntime(world_root, second_restart_clock)
        final_after_second_restart = second_restart.current_state()
        assert final_after_second_restart == final_state
        assert second_restart.can_depart(PLACE_KESTRELS, PLACE_ST_BRIGIDS)

        same_time = "2026-09-20T13:00:00+00:00"
        ordered = ordered_scheduled_events([
            {"event_id": "event-seq-9", "effective_time": same_time, "sequence": 9},
            {"event_id": "event-seq-4", "effective_time": same_time, "sequence": 4},
        ])
        same_time_order = [item["event_id"] for item in ordered]
        assert same_time_order == ["event-seq-4", "event-seq-9"]

        gate_rejection = None
        try:
            assert_effect_allowed("CANONICAL_OR_RESERVED", EXPERIMENT_SCOPE)
        except EffectGateRejected as exc:
            gate_rejection = str(exc)
        assert gate_rejection is not None

        journal = read_journal(world_root / "world-journal.jsonl")
        assert [item["event_type"] for item in journal] == ["DEPARTED", "ARRIVED"]
        assert [item["sequence"] for item in journal] == [1, 2]
        assert all(item["effect_class"] == EFFECT_DYNAMIC_WORLD for item in journal)
        assert all(item["scope"] == EXPERIMENT_SCOPE for item in journal)
        assert all(item["authority_ref"] == AUTHORITY_REF for item in journal)

        public_after = {str(path.relative_to(REPO_ROOT)): digest(path) for path in PUBLIC_FILES}
        assert public_before == public_after

        return {
            "result": "PASS — PERSISTENT CAUSAL WORLD STATE PROVED",
            "authority_ref": AUTHORITY_REF,
            "experiment_scope": EXPERIMENT_SCOPE,
            "entity_id": TEST_FERRY_ID,
            "places": [PLACE_ST_BRIGIDS, PLACE_KESTRELS],
            "initial_state": initial,
            "invalid_transition_rejection": invalid_rejection,
            "return_journey_eligible_before": return_before,
            "departure_event": departure,
            "persistent_in_transit_state_before_restart": in_transit,
            "reconstructed_in_transit_state_after_restart": reconstructed_in_transit,
            "arrival_event": arrival,
            "final_state": final_state,
            "final_state_after_second_restart": final_after_second_restart,
            "return_journey_eligible_after": return_after,
            "replay_matches_live_state": replayed == final_state,
            "duplicate_command_added_no_event": duplicate["event_id"] == departure["event_id"],
            "journal_unchanged_by_duplicate_and_replay": True,
            "same_time_deterministic_order": same_time_order,
            "authority_gate_rejection": gate_rejection,
            "journal": journal,
            "clock_state_after_proof": json.loads(clock_path.read_text(encoding="utf-8")),
            "public_artifact_hashes_before": public_before,
            "public_artifact_hashes_after": public_after,
            "public_artifact_unchanged": public_before == public_after,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = run_proof()
    rendered = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    print(rendered)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
