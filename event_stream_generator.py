import argparse
from datetime import datetime, timezone
import json
import random
import time
from pathlib import Path


EVENT_TEMPLATES = [
    {
        "classification": "warning",
        "action": "restart_service",
        "owner": "ops-team",
        "approval_required": False,
    },
    {
        "classification": "warning",
        "action": "scale_workers",
        "owner": "ops-team",
        "approval_required": False,
    },
    {
        "classification": "review",
        "action": "open_ticket",
        "owner": "support-team",
        "approval_required": False,
    },
    {
        "classification": "critical",
        "action": "rollback_release",
        "owner": "release-team",
        "approval_required": True,
    },
    {
        "classification": "critical",
        "action": "grant_access",
        "owner": "security-team",
        "approval_required": True,
    },
    {
        "classification": "critical",
        "action": "manual_override",
        "owner": "ops-team",
        "approval_required": True,
    },
    {
        "classification": "critical",
        "action": "disable_alerts",
        "owner": "ops-team",
        "approval_required": True,
    },
]

DRIFT_TYPES = [
    "missing_trace",
    "missing_evidence",
    "replay_mismatch",
    "approval_pending",
    "unauthorized_actor",
    "missing_owner",
]


def probability(value):
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1") from error

    if parsed < 0 or parsed > 1:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1")

    return parsed


def positive_int(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer greater than 0") from error

    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer greater than 0")

    return parsed


def non_negative_float(value):
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number greater than or equal to 0") from error

    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a number greater than or equal to 0")

    return parsed


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_existing_events(path):
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data["events"]

    raise ValueError("stream file must be a JSON list or an object with an events list")


def write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(events, file, indent=2)
        file.write("\n")

    temporary_path.replace(path)


def next_sequence(existing_events):
    highest = 0

    for event in existing_events:
        event_id = str(event.get("event_id", ""))
        prefix, separator, suffix = event_id.partition("-")

        if prefix == "EVT" and separator and suffix.isdigit():
            highest = max(highest, int(suffix))

    return highest + 1


def base_event(sequence, rng):
    template = rng.choice(EVENT_TEMPLATES)
    approval_required = template["approval_required"]

    return {
        "event_id": f"EVT-{sequence:06d}",
        "timestamp": utc_timestamp(),
        "classification": template["classification"],
        "action": template["action"],
        "owner": template["owner"],
        "trace_id": f"TRACE-{sequence:06d}",
        "evidence_id": f"EVID-{sequence:06d}",
        "approval_required": approval_required,
        "approval_status": "approved" if approval_required else "not_required",
        "authorized_actor": True,
        "replay_expected_action": template["action"],
    }


def inject_drift(event, rng):
    drift_type = rng.choice(DRIFT_TYPES)

    if drift_type == "missing_trace":
        event["trace_id"] = ""
    elif drift_type == "missing_evidence":
        event["evidence_id"] = ""
    elif drift_type == "replay_mismatch":
        event["replay_expected_action"] = "hold_action"
    elif drift_type == "approval_pending":
        event["approval_required"] = True
        event["approval_status"] = rng.choice(["pending", "rejected", "missing"])
    elif drift_type == "unauthorized_actor":
        event["authorized_actor"] = False
    elif drift_type == "missing_owner":
        event["owner"] = ""

    event["simulated_drift"] = drift_type


def build_event(sequence, rng, drift_rate):
    event = base_event(sequence, rng)

    if rng.random() < drift_rate:
        inject_drift(event, rng)

    return event


def stream_events(args):
    output_path = Path(args.output)
    rng = random.Random(args.seed)
    existing_events = [] if args.reset else load_existing_events(output_path)
    next_event_number = next_sequence(existing_events)
    emitted = 0

    if args.reset or not output_path.exists():
        write_events(output_path, existing_events)

    print(f"Streaming events into {output_path}")
    print("Press Ctrl+C to stop.")

    while args.max_events is None or emitted < args.max_events:
        burst_size = 1
        if args.max_burst_events > 1 and rng.random() < args.burst_chance:
            burst_size = rng.randint(2, args.max_burst_events)

        for _ in range(burst_size):
            if args.max_events is not None and emitted >= args.max_events:
                break

            event = build_event(next_event_number, rng, args.drift_rate)
            existing_events.append(event)
            write_events(output_path, existing_events)

            emitted += 1
            next_event_number += 1

            if not args.quiet:
                drift_label = event.get("simulated_drift", "none")
                print(
                    f"{event['timestamp']} {event['event_id']} "
                    f"{event['classification']} {event['action']} drift={drift_label}"
                )

        if args.max_events is not None and emitted >= args.max_events:
            break

        delay = args.interval_seconds
        if args.jitter_seconds:
            delay += rng.uniform(-args.jitter_seconds, args.jitter_seconds)

        time.sleep(max(delay, 0))

    print(f"Generated {emitted} event(s).")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Continuously generate operational events into a JSON file."
    )
    parser.add_argument(
        "--output",
        default="data/stream_events.json",
        help="JSON file to create or append to",
    )
    parser.add_argument(
        "--interval-seconds",
        type=non_negative_float,
        default=1.0,
        help="Seconds to wait between event batches",
    )
    parser.add_argument(
        "--jitter-seconds",
        type=non_negative_float,
        default=0.2,
        help="Random timing variation added around the interval",
    )
    parser.add_argument(
        "--max-events",
        type=positive_int,
        help="Stop after this many new events; omit to run until Ctrl+C",
    )
    parser.add_argument(
        "--drift-rate",
        type=probability,
        default=0.15,
        help="Probability that a generated event contains one drift condition",
    )
    parser.add_argument(
        "--burst-chance",
        type=probability,
        default=0.2,
        help="Probability that an interval emits a burst instead of one event",
    )
    parser.add_argument(
        "--max-burst-events",
        type=positive_int,
        default=5,
        help="Largest number of events generated during a burst",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Optional random seed for repeatable simulation runs",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Overwrite the output file before streaming",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write events without printing each event",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        stream_events(args)
    except KeyboardInterrupt:
        print("\nStopped event stream.")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Could not generate event stream: {error}")


if __name__ == "__main__":
    main()
