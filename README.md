# Central Aldernia Clock

Production implementation of the **Central Aldernia Clock & Aldernian Mean Time (AMT)** commissioned by King Alan on 20 September 2026.

## Time model

- Canonical machine time: runtime UTC.
- **AMT:** human-readable Dynasty projection through IANA `Europe/London`; it therefore follows GMT/BST correctly.
- **Dynasty Tick:** explicit monotonic logical sequence for authorised material events.
- Elapsed durations use monotonic time.
- Wall-clock reads do not advance logical time.

AMT is an Aldernian display/conceptual name, not a new internationally recognised timezone.

## Constitutional invariant

`CLOCK SIGNAL != MISSION`

Time passing does not create authority or work. A valid authorised timetable/trigger and all other Dynasty controls remain necessary.

## Interfaces

- `snapshot()`
- `advance_tick()`
- `stamp_event()`
- `elapsed_seconds()`
- `is_due()`
- `compare()`

## Run locally

Python 3.11+:

```bash
python -m unittest discover -s tests -v
python -m central_aldernia_clock --state state/clock-state.json snapshot
python -m central_aldernia_clock --state state/clock-state.json tick
```

Logical state is atomically persisted when a state path is supplied. Malformed/backwards state fails closed.

## Clock vs scheduler

The clock reads deterministic system time and does not require an LLM to keep time.

GitHub Actions is the current wake-up scheduler. It runs hourly at minute 17 UTC, first runs all deterministic clock tests, then performs one bounded chime/timetable proof. It also runs on production-`main` updates as a health/regression check.

Scheduler runs serialise through a single concurrency group. Operational logical state is restored from and written back to the dedicated `clock-state` branch, so successive authorised chimes advance one shared Dynasty tick rather than restarting at zero. The `clock-state` branch is runtime state only; production code remains on `main`.

The workflow may write only that clock-state branch. It does not select Dynasty missions.

## Commissioning evidence

Production scheduler run `35531449032` passed the deterministic acceptance suite, restored the state branch, emitted the bounded clock event, completed DUE/NOT-DUE timetable checks, and successfully persisted the resulting logical state. Readback of `clock-state/state/clock-state.json` showed Dynasty tick `1`, previous tick `0`, proving the first durable scheduler/state round trip.

A further production wake-up is used to prove that the persisted tick is restored and advances rather than restarting from zero.

## Acceptance coverage

The tests cover UTC, BST/GMT projection, read-without-tick, monotonic logical ticks, restart persistence, fail-closed state, monotonic elapsed time, and timezone-safe due/ordering.

`proof/first_chime.py` proves:

`CLOCK -> authorised test timetable -> event -> ACK -> STOP`

including both DUE and NOT-DUE conditions without implicit mission creation.

## Boundary

This commissions the Clock layer only. It is not yet the full Aldernian event bus, durable national work queue, capability router or multi-House workflow orchestrator.

Institutional authority and human-readable canonical records remain in the controlled Drive estate.
