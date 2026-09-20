from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from .clock import CentralAlderniaClock

def _aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone/offset")
    return dt

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aldernia-clock")
    parser.add_argument("--state", default="state/clock-state.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("tick")
    stamp = sub.add_parser("stamp")
    stamp.add_argument("event_type")
    stamp.add_argument("--source", required=True)
    stamp.add_argument("--provenance", required=True)
    stamp.add_argument("--no-advance", action="store_true")
    due = sub.add_parser("due")
    due.add_argument("due_at", type=_aware)
    due.add_argument("--now", type=_aware)
    compare = sub.add_parser("compare")
    compare.add_argument("first", type=_aware)
    compare.add_argument("second", type=_aware)
    return parser

def main() -> int:
    args = build_parser().parse_args()
    clock = CentralAlderniaClock(Path(args.state))
    if args.command == "snapshot":
        print(json.dumps(clock.snapshot().to_dict(), indent=2))
    elif args.command == "tick":
        tick, previous = clock.advance_tick()
        print(json.dumps({"dynasty_tick": tick, "previous_dynasty_tick": previous}, indent=2))
    elif args.command == "stamp":
        event = clock.stamp_event(args.event_type, source=args.source, provenance=args.provenance, advance=not args.no_advance)
        print(json.dumps(event.to_dict(), indent=2))
    elif args.command == "due":
        print(json.dumps({"due": clock.is_due(args.now or clock.utc_now(), args.due_at)}, indent=2))
    elif args.command == "compare":
        print(json.dumps({"order": clock.compare(args.first, args.second)}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
