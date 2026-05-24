import argparse
from collections import Counter
import json
from pathlib import Path


BLOCKER_KEYWORDS = [
    "blocked",
    "blocker",
    "waiting",
    "stuck",
    "pending",
    "cannot proceed",
]

DEPENDENCY_KEYWORDS = [
    "dependency",
    "depends on",
    "blocked by",
    "waiting for",
    "api",
    "database",
    "integration",
    "upstream",
    "downstream",
    "vendor",
]

REPLAY_KEYWORDS = [
    "replay",
    "non-repeatable",
    "mismatch",
    "different result",
    "expected action mismatch",
    "cannot reproduce",
    "rerun failed",
]

OBSERVABILITY_KEYWORDS = [
    "missing trace",
    "trace missing",
    "missing evidence",
    "evidence missing",
    "missing log",
    "decision log missing",
    "no trace",
    "no evidence",
    "no log",
    "no decision log",
    "untracked",
]

GOVERNANCE_KEYWORDS = [
    "approval pending",
    "approval missing",
    "approval bypass",
    "approval not recorded",
    "approval required but not approved",
    "without approval",
    "not approved",
    "unauthorized",
    "policy exception",
    "access granted",
    "manual override",
]


def load_updates(path):
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        if "updates" in data:
            return data["updates"]
        if "events" in data:
            return data["events"]
        return []

    return data


def write_json(path, data):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")


def find_matches(text, keywords):
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword in lowered]


def detect_status(text):
    lowered = text.lower()

    if any(word in lowered for word in ["outage", "down", "failed", "failing"]):
        return "outage"
    if any(word in lowered for word in ["degraded", "slow", "latency", "error"]):
        return "degraded"
    if any(word in lowered for word in ["recovering", "mitigated", "rollback complete", "rollback completed"]):
        return "recovering"
    if any(word in lowered for word in ["healthy", "stable", "green", "normal"]):
        return "healthy"

    return "unknown"


def classify_update(parsed):
    text = parsed["raw_update"].strip()

    if len(text) < 20:
        return "unclear/incomplete"
    if parsed["system_status"] == "unknown" and not any([
        parsed["blockers"],
        parsed["dependencies"],
        parsed["replay_risks"],
        parsed["observability_risks"],
        parsed["governance_risks"],
    ]):
        return "unclear/incomplete"
    if parsed["governance_risks"]:
        return "authority-risk"
    if parsed["observability_risks"]:
        return "observability-risk"
    if parsed["replay_risks"]:
        return "replay-risk"
    if parsed["blockers"] or parsed["dependencies"]:
        return "integration-risk"

    return "aligned"


def parse_update(update, index):
    if isinstance(update, str):
        raw_update = update
        update_id = f"UPD-{index:03d}"
        timestamp = ""
    else:
        raw_update = update.get("text", "")
        update_id = update.get("update_id", f"UPD-{index:03d}")
        timestamp = update.get("timestamp", "")

    parsed = {
        "event_id": update_id,
        "timestamp": timestamp,
        "raw_update": raw_update,
        "system_status": detect_status(raw_update),
        "blockers": find_matches(raw_update, BLOCKER_KEYWORDS),
        "dependencies": find_matches(raw_update, DEPENDENCY_KEYWORDS),
        "replay_risks": find_matches(raw_update, REPLAY_KEYWORDS),
        "observability_risks": find_matches(raw_update, OBSERVABILITY_KEYWORDS),
        "governance_risks": find_matches(raw_update, GOVERNANCE_KEYWORDS),
    }
    parsed["drift_classification"] = classify_update(parsed)
    return parsed


def parse_updates(updates):
    return [
        parse_update(update, index)
        for index, update in enumerate(updates, start=1)
    ]


def print_summary(parsed_updates):
    counts = Counter(update["drift_classification"] for update in parsed_updates)

    print("Parsed operational updates")
    print("--------------------------")
    print(f"Updates parsed: {len(parsed_updates)}")
    for classification, count in sorted(counts.items()):
        print(f"- {classification}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse operational updates into structured drift fields.",
    )
    parser.add_argument("--input", required=True, help="Path to updates JSON file")
    parser.add_argument(
        "--output",
        default="parsed_updates.json",
        help="Path to write parsed update JSON",
    )
    args = parser.parse_args()

    updates = load_updates(args.input)
    parsed_updates = parse_updates(updates)
    write_json(args.output, parsed_updates)
    print_summary(parsed_updates)


if __name__ == "__main__":
    main()
