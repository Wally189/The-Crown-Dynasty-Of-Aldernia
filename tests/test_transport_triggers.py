import unittest
from datetime import date, time

from aldernia_runtime.transport import (
    CANONICAL_STATE_CHANGED,
    CROWN_DECISION_REQUIRED,
    DEPENDENCY_COMPLETED,
    DEPENDENCY_REQUESTED,
    GOVERNMENT_PULSE_CLOSED,
    GOVERNMENT_PULSE_DUE,
    MISSION_VERIFIED_COMPLETE,
    EXPRESS_TRAM,
    HEARTBEAT,
    TRAM,
    DeliveryResponse,
    TransportError,
    TransportPacket,
    TransportRouter,
    balcony_event_allowed,
    dynasty_sleeping,
    government_pulse_due,
    heartbeat_should_fire,
    pulse_completion_event,
)


AUTH = "Crown Commission — Operationalise Aldernian Computational Bus Triggers — 20 September 2026"


def packet(event_type, *, event_id="evt-1", parent="case-1", destination=None, transition=None, payload=None, service_class=None, departure_mode="EVENT"):
    defaults = {
        GOVERNMENT_PULSE_DUE: "House of Marianne — Centre of Government & Cabinet Coordination",
        GOVERNMENT_PULSE_CLOSED: "Government Red Box Tram → Royal Palace / King's Balcony",
        DEPENDENCY_REQUESTED: "House of Tony — Education",
        DEPENDENCY_COMPLETED: "House of Marianne — Government",
        CANONICAL_STATE_CHANGED: "Dynasty Institutional Heartbeat impact discovery",
        CROWN_DECISION_REQUIRED: "Royal Palace / King's Balcony",
        MISSION_VERIFIED_COMPLETE: "Royal Palace / King's Balcony",
    }
    return TransportPacket(
        event_id=event_id,
        event_type=event_type,
        emitting_owner="Test accountable owner",
        authority_ref=AUTH,
        mission_ref="transport-trigger-regression",
        departure_condition="fixture condition satisfied",
        destination=destination or defaults[event_type],
        acceptance="fixture acceptance",
        ack_to="fixture origin ledger",
        parent_case_id=parent,
        requested_contribution="bounded contribution",
        transition_class=transition,
        payload=payload or {},
        service_class=service_class,
        departure_mode=departure_mode,
    )


class TransportTriggerTests(unittest.TestCase):
    def test_due_government_pulse_is_machine_decidable(self):
        self.assertTrue(government_pulse_due(
            current_aldernian_date=date(2026, 9, 21),
            last_verified_completed_date=date(2026, 9, 20),
            pulse_active_for_current_date=False,
            bus_operating_state="DORMANT",
        ))
        self.assertFalse(government_pulse_due(
            current_aldernian_date=date(2026, 9, 20),
            last_verified_completed_date=date(2026, 9, 20),
            pulse_active_for_current_date=False,
            bus_operating_state="DORMANT",
        ))

    def test_completed_pulse_emits_red_box_route_and_acks(self):
        self.assertEqual(pulse_completion_event("VERIFIED_CLOSED"), GOVERNMENT_PULSE_CLOSED)
        router = TransportRouter()
        p = packet(GOVERNMENT_PULSE_CLOSED, event_id="pulse-2026-09-21-closed", parent="pulse-2026-09-21")
        result = router.dispatch(p, lambda _p, _a: DeliveryResponse("COMPLETE", True, "Balcony delivery ACK read back"))
        self.assertEqual(result.status, "ACKNOWLEDGED")
        self.assertTrue(balcony_event_allowed(p))

    def test_cross_institution_dependency_request_and_return(self):
        router = TransportRouter()
        request = packet(DEPENDENCY_REQUESTED, event_id="dep-req-1", parent="policy-case-7", destination="House of Tony — Education")
        accepted = router.dispatch(request, lambda _p, _a: DeliveryResponse("ACCEPT", True, "bounded education contribution accepted"))
        self.assertEqual(accepted.response_status, "ACCEPT")
        returned = packet(DEPENDENCY_COMPLETED, event_id="dep-ret-1", parent="policy-case-7", destination="House of Marianne — Government")
        completed = router.dispatch(returned, lambda _p, _a: DeliveryResponse("COMPLETE", True, "specialist result returned to originating owner"))
        self.assertEqual(completed.status, "ACKNOWLEDGED")

    def test_heartbeat_event_can_ack_no_action(self):
        self.assertTrue(heartbeat_should_fire(CANONICAL_STATE_CHANGED, "capability_state_changed"))
        router = TransportRouter()
        p = packet(CANONICAL_STATE_CHANGED, event_id="state-7", parent="capability-7", transition="capability_state_changed")
        result = router.dispatch(p, lambda _p, _a: DeliveryResponse("NO_ACTION", True, "event relevant enough to inspect; no downstream action justified"))
        self.assertEqual((result.status, result.response_status), ("ACKNOWLEDGED", "NO_ACTION"))

    def test_duplicate_replay_is_suppressed_by_parent_identity(self):
        router = TransportRouter()
        calls = []
        p1 = packet(DEPENDENCY_REQUESTED, event_id="dep-1", parent="same-parent", destination="House of Tony — Education")
        p2 = packet(DEPENDENCY_REQUESTED, event_id="dep-replayed-new-id", parent="same-parent", destination="House of Tony — Education")
        def handler(_p, attempt):
            calls.append(attempt)
            return DeliveryResponse("COMPLETE", True, "done")
        self.assertEqual(router.dispatch(p1, handler).status, "ACKNOWLEDGED")
        self.assertEqual(router.dispatch(p2, handler).status, "DUPLICATE_SUPPRESSED")
        self.assertEqual(calls, [1])

    def test_missing_ack_gets_one_retry_then_fails_closed(self):
        router = TransportRouter()
        calls = []
        p = packet(DEPENDENCY_REQUESTED, event_id="lost-ack", parent="lost-ack", destination="House of Tony — Education")
        def no_ack(_p, attempt):
            calls.append(attempt)
            return DeliveryResponse("ACCEPT", False, "receipt channel unavailable")
        result = router.dispatch(p, no_ack)
        self.assertEqual(result.status, "FAILED_CLOSED_DELIVERY_UNCONFIRMED")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(calls, [1, 2])

    def test_halt_dominates_dispatch_and_retry(self):
        router = TransportRouter()
        router.halt()
        p = packet(DEPENDENCY_REQUESTED, event_id="halted", parent="halted", destination="House of Tony — Education")
        result = router.dispatch(p, lambda _p, _a: self.fail("handler must not run while HALTED"))
        self.assertEqual((result.status, result.attempts), ("HALTED", 0))

    def test_balcony_filter_rejects_routine_operational_traffic(self):
        routine = packet(DEPENDENCY_REQUESTED, destination="House of Tony — Education")
        self.assertFalse(balcony_event_allowed(routine))
        reserved = packet(CROWN_DECISION_REQUIRED, event_id="crown-1", parent="crown-1")
        self.assertTrue(balcony_event_allowed(reserved))
        closure_without_crown = packet(MISSION_VERIFIED_COMPLETE, event_id="m1", parent="m1", payload={"crown_receipt_required": False})
        self.assertFalse(balcony_event_allowed(closure_without_crown))

    def test_vehicle_class_is_separate_from_departure_mode(self):
        red_box = packet(GOVERNMENT_PULSE_CLOSED, departure_mode="COMPLETION")
        self.assertEqual(red_box.resolved_service_class, TRAM)
        self.assertEqual(red_box.departure_mode, "COMPLETION")
        heartbeat = packet(CANONICAL_STATE_CHANGED, transition="scheduled_duty_due", departure_mode="TIMETABLE")
        self.assertEqual(heartbeat.resolved_service_class, HEARTBEAT)

    def test_dynasty_sleep_boundaries(self):
        self.assertFalse(dynasty_sleeping(time(23, 29)))
        self.assertTrue(dynasty_sleeping(time(23, 30)))
        self.assertTrue(dynasty_sleeping(time(4, 59)))
        self.assertFalse(dynasty_sleeping(time(5, 0)))

    def test_ordinary_bus_is_deferred_during_sleep(self):
        router = TransportRouter()
        p = packet(DEPENDENCY_REQUESTED, event_id="sleeping-bus", parent="sleeping-bus", destination="House of Tony — Education")
        result = router.dispatch(p, lambda _p, _a: self.fail("ordinary handler must not run during sleep"), local_time=time(23, 45))
        self.assertEqual((result.status, result.attempts), ("SLEEP_DEFERRED", 0))

    def test_express_tram_may_break_sleep_only_with_explicit_override(self):
        router = TransportRouter()
        reserved = packet(CROWN_DECISION_REQUIRED, event_id="night-stop", parent="night-stop")
        deferred = router.dispatch(reserved, lambda _p, _a: self.fail("no sleep override yet"), local_time=time(2, 0))
        self.assertEqual(deferred.status, "SLEEP_DEFERRED")

        authorised = packet(
            CROWN_DECISION_REQUIRED,
            event_id="night-stop-authorised",
            parent="night-stop-authorised",
            payload={"sleep_override_authorised": True, "binding_stop_or_emergency": True},
        )
        result = router.dispatch(
            authorised,
            lambda _p, _a: DeliveryResponse("STOP", True, "binding constitutional STOP surfaced"),
            local_time=time(2, 0),
        )
        self.assertEqual((result.status, result.response_status), ("ACKNOWLEDGED", "STOP"))
        self.assertEqual(authorised.resolved_service_class, EXPRESS_TRAM)

    def test_priority_does_not_widen_authority(self):
        bad = packet(
            GOVERNMENT_PULSE_DUE,
            event_id="priority-is-not-authority",
            parent="priority-is-not-authority",
            service_class=EXPRESS_TRAM,
        )
        with self.assertRaises(TransportError):
            TransportRouter().dispatch(bad, lambda _p, _a: DeliveryResponse("COMPLETE", True, "must not happen"))


if __name__ == "__main__":
    unittest.main()
