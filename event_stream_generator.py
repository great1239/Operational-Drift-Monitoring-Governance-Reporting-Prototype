import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import random
import time
from pathlib import Path


SERVICES = [
    "Checkout",
    "Payment",
    "Search",
    "Auth",
    "Billing",
    "Inventory",
    "Notification",
]

DEPENDENCIES = [
    "auth",
    "payments",
    "vendor",
    "database",
    "downstream",
    "upstream",
]

ALIGNED_UPDATE_TEMPLATES = [
    {
        "label": "aligned",
        "text": "{service} service is healthy. Work is flowing normally. Trace, evidence, and decision log are present.",
    },
    {
        "label": "aligned",
        "text": "{service} workflow is stable. Runbook action matched the expected action. Evidence is attached.",
    },
    {
        "label": "aligned",
        "text": "{service} queue is normal. The team completed the check and the decision log is present.",
    },
]

RISK_UPDATE_TEMPLATES = [
    {
        "label": "integration-risk",
        "text": "{service} service is slow. Team is blocked by {dependency} API errors and waiting for database confirmation.",
    },
    {
        "label": "integration-risk",
        "text": "{service} deployment is stuck. It depends on the {dependency} integration and cannot proceed.",
    },
    {
        "label": "replay-risk",
        "text": "{service} rollback completed, but replay produced a different result. Expected action mismatch and rerun failed.",
    },
    {
        "label": "replay-risk",
        "text": "{service} mitigation cannot reproduce the same result on replay. Rerun failed during validation.",
    },
    {
        "label": "observability-risk",
        "text": "{service} service is degraded. Missing trace and missing evidence for the decision. No log was attached.",
    },
    {
        "label": "observability-risk",
        "text": "{service} alert is noisy. Trace missing and no evidence was attached to the update.",
    },
    {
        "label": "authority-risk",
        "text": "{service} access change used manual override. Actor was unauthorized and approval missing.",
    },
    {
        "label": "authority-risk",
        "text": "{service} policy exception was applied without approval. Approval not recorded before execution.",
    },
    {
        "label": "unclear/incomplete",
        "text": "Looking into it.",
    },
    {
        "label": "unclear/incomplete",
        "text": "Checking now.",
    },
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


def utc_timestamp(sequence):
    timestamp = datetime.now(timezone.utc) + timedelta(microseconds=sequence)
    return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_existing_updates(path):
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and isinstance(data.get("updates"), list):
        return data["updates"]

    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data["events"]

    raise ValueError("stream file must be a JSON list or an object with an updates list")


def write_updates(path, updates):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(updates, file, indent=2)
        file.write("\n")

    for attempt in range(5):
        try:
            temporary_path.replace(path)
            return
        except PermissionError as error:
            if attempt == 4:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

                raise PermissionError(
                    f"Could not update {path}. Close the JSON file if it is open, "
                    "pause OneDrive sync if needed, or use a different --output file."
                ) from error

            time.sleep(0.25)


def next_sequence(existing_events):
    highest = 0

    for event in existing_events:
        event_id = str(event.get("update_id", event.get("event_id", "")))
        prefix, separator, suffix = event_id.partition("-")

        if prefix in ("UPD", "EVT") and separator and suffix.isdigit():
            highest = max(highest, int(suffix))

    return highest + 1


def render_template(template, rng):
    return template["text"].format(
        service=rng.choice(SERVICES),
        dependency=rng.choice(DEPENDENCIES),
    )


def build_update(sequence, rng, drift_rate):
    if rng.random() < drift_rate:
        template = rng.choice(RISK_UPDATE_TEMPLATES)
    else:
        template = rng.choice(ALIGNED_UPDATE_TEMPLATES)

    update = {
        "update_id": f"UPD-{sequence:06d}",
        "timestamp": utc_timestamp(sequence),
        "text": render_template(template, rng),
    }
    return update, template["label"]


def stream_updates(args):
    output_path = Path(args.output)
    rng = random.Random(args.seed)
    existing_updates = [] if args.reset else load_existing_updates(output_path)
    next_update_number = next_sequence(existing_updates)
    emitted = 0

    if args.reset or not output_path.exists():
        write_updates(output_path, existing_updates)

    print(f"Streaming raw updates into {output_path}")
    print("Press Ctrl+C to stop.")

    while args.max_events is None or emitted < args.max_events:
        burst_size = 1
        if args.max_burst_events > 1 and rng.random() < args.burst_chance:
            burst_size = rng.randint(2, args.max_burst_events)

        for _ in range(burst_size):
            if args.max_events is not None and emitted >= args.max_events:
                break

            update, label = build_update(next_update_number, rng, args.drift_rate)
            existing_updates.append(update)
            write_updates(output_path, existing_updates)

            emitted += 1
            next_update_number += 1

            if not args.quiet:
                print(
                    f"{update['timestamp']} {update['update_id']} "
                    f"class={label} text={update['text']}"
                )

        if args.max_events is not None and emitted >= args.max_events:
            break

        delay = args.interval_seconds
        if args.jitter_seconds:
            delay += rng.uniform(-args.jitter_seconds, args.jitter_seconds)

        time.sleep(max(delay, 0))

    print(f"Generated {emitted} raw update(s).")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Continuously generate raw operational updates into a JSON file."
    )
    parser.add_argument(
        "--output",
        default="data/stream_updates.json",
        help="JSON file to create or append to",
    )
    parser.add_argument(
        "--interval-seconds",
        type=non_negative_float,
        default=1.0,
        help="Seconds to wait between update batches",
    )
    parser.add_argument(
        "--jitter-seconds",
        type=non_negative_float,
        default=0.2,
        help="Random timing variation added around the interval",
    )
    parser.add_argument(
        "--max-events",
        "--max-updates",
        dest="max_events",
        type=positive_int,
        help="Stop after this many new updates; omit to run until Ctrl+C",
    )
    parser.add_argument(
        "--drift-rate",
        type=probability,
        default=0.15,
        help="Probability that a generated update contains a risk or incomplete condition",
    )
    parser.add_argument(
        "--burst-chance",
        type=probability,
        default=0.2,
        help="Probability that an interval emits a burst instead of one update",
    )
    parser.add_argument(
        "--max-burst-events",
        type=positive_int,
        default=5,
        help="Largest number of updates generated during a burst",
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
        help="Write updates without printing each update",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        stream_updates(args)
    except KeyboardInterrupt:
        print("\nStopped update stream.")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Could not generate update stream: {error}")


if __name__ == "__main__":
    main()
