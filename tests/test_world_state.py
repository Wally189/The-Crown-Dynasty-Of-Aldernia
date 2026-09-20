from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from central_aldernia_clock import CentralAlderniaClock
from aldernia_runtime.world_state import (
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


class WorldStateTests(unittest.TestCase):
    def make_runtime(self, root: Path):
        clock = CentralAlderniaClock(root / "clock-state.json")
        runtime = WorldRuntime(root / "world", clock)
        return clock, runtime

    def test_stable_place_and_entity_ids_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _clock, runtime = self.make_runtime(root)
            state = runtime.current_state()
            self.assertIn(PLACE_ST_BRIGIDS, state["places"])
            self.assertIn(PLACE_KESTRELS, state["places"])
            self.assertIn(TEST_FERRY_ID, state["entities"])
            self.assertEqual(state["entities"][TEST_FERRY_ID]["location"], PLACE_ST_BRIGIDS)
            self.assertEqual(state["entities"][TEST_FERRY_ID]["movement_state"], "DOCKED")
            persisted = json.loads((root / "world" / "world-state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, state)

    def test_no_route_and_wrong_origin_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _clock, runtime = self.make_runtime(root)
            t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            t1 = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
            with self.assertRaises(TransitionRejected):
                runtime.depart(
                    origin=PLACE_ST_BRIGIDS,
                    destination="place:aldernia:merrow",
                    effective_time=t0,
                    arrival_time=t1,
                    command_id="bad-route",
                )
            with self.assertRaises(TransitionRejected):
                runtime.depart(
                    origin=PLACE_KESTRELS,
                    destination=PLACE_ST_BRIGIDS,
                    effective_time=t0,
                    arrival_time=t1,
                    command_id="wrong-origin",
                )
            self.assertEqual(read_journal(root / "world" / "world-journal.jsonl"), [])

    def test_departure_survives_runtime_restart_and_arrival_changes_future(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clock, runtime = self.make_runtime(root)
            t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            t1 = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
            self.assertFalse(runtime.can_depart(PLACE_KESTRELS, PLACE_ST_BRIGIDS))
            departure = runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination=PLACE_KESTRELS,
                effective_time=t0,
                arrival_time=t1,
                command_id="journey-1",
            )
            expected_in_transit = runtime.current_state()
            self.assertEqual(departure["event_type"], "DEPARTED")
            del runtime
            del clock

            restarted_clock = CentralAlderniaClock(root / "clock-state.json")
            restarted = WorldRuntime(root / "world", restarted_clock)
            self.assertEqual(restarted.current_state(), expected_in_transit)
            self.assertEqual(
                restarted.process_due(now=datetime(2026, 9, 20, 12, 44, 59, tzinfo=timezone.utc)),
                [],
            )
            arrivals = restarted.process_due(now=t1)
            self.assertEqual(len(arrivals), 1)
            self.assertEqual(arrivals[0]["event_type"], "ARRIVED")
            self.assertTrue(restarted.can_depart(PLACE_KESTRELS, PLACE_ST_BRIGIDS))
            self.assertFalse(restarted.can_depart(PLACE_ST_BRIGIDS, PLACE_KESTRELS))

    def test_duplicate_departure_command_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _clock, runtime = self.make_runtime(root)
            t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            t1 = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
            first = runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination=PLACE_KESTRELS,
                effective_time=t0,
                arrival_time=t1,
                command_id="journey-1",
            )
            journal_path = root / "world" / "world-journal.jsonl"
            before = journal_path.read_bytes()
            second = runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination=PLACE_KESTRELS,
                effective_time=t0,
                arrival_time=t1,
                command_id="journey-1",
            )
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertEqual(journal_path.read_bytes(), before)
            self.assertEqual(len(runtime.current_state()["scheduled_events"]), 1)

    def test_replay_reconstructs_same_final_state_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _clock, runtime = self.make_runtime(root)
            t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            t1 = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
            runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination=PLACE_KESTRELS,
                effective_time=t0,
                arrival_time=t1,
                command_id="journey-1",
            )
            runtime.process_due(now=t1)
            journal_path = root / "world" / "world-journal.jsonl"
            before = journal_path.read_bytes()
            replayed = WorldRuntime.replay(journal_path)
            self.assertEqual(replayed, runtime.current_state())
            self.assertEqual(journal_path.read_bytes(), before)

    def test_journal_has_provenance_effect_class_time_and_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _clock, runtime = self.make_runtime(root)
            t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            t1 = datetime(2026, 9, 20, 12, 45, tzinfo=timezone.utc)
            runtime.depart(
                origin=PLACE_ST_BRIGIDS,
                destination=PLACE_KESTRELS,
                effective_time=t0,
                arrival_time=t1,
                command_id="journey-1",
            )
            runtime.process_due(now=t1)
            events = read_journal(root / "world" / "world-journal.jsonl")
            self.assertEqual([event["sequence"] for event in events], [1, 2])
            for event in events:
                self.assertEqual(event["effect_class"], EFFECT_DYNAMIC_WORLD)
                self.assertEqual(event["scope"], EXPERIMENT_SCOPE)
                self.assertEqual(event["clock_id"], "central-aldernia-clock")
                self.assertIsInstance(event["dynasty_tick"], int)
                self.assertTrue(event["effective_time"])
                self.assertTrue(event["recorded_time"])
                self.assertTrue(event["provenance"])
                self.assertTrue(event["authority_ref"])

    def test_same_time_order_is_sequence_deterministic(self):
        same = "2026-09-20T13:00:00+00:00"
        ordered = ordered_scheduled_events([
            {"event_id": "later-sequence", "effective_time": same, "sequence": 7},
            {"event_id": "earlier-sequence", "effective_time": same, "sequence": 3},
        ])
        self.assertEqual(
            [event["event_id"] for event in ordered],
            ["earlier-sequence", "later-sequence"],
        )

    def test_authority_gate_rejects_reserved_effects(self):
        assert_effect_allowed(EFFECT_DYNAMIC_WORLD, EXPERIMENT_SCOPE)
        with self.assertRaises(EffectGateRejected):
            assert_effect_allowed("CANONICAL_OR_RESERVED", EXPERIMENT_SCOPE)
        with self.assertRaises(EffectGateRejected):
            assert_effect_allowed(EFFECT_DYNAMIC_WORLD, "PUBLIC_CANON")


if __name__ == "__main__":
    unittest.main()
