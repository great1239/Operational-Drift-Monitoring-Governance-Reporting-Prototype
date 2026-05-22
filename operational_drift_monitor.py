import argparse
from datetime import datetime
import json
import sys
import time
from pathlib import Path


STRUCTURE_FIELDS = [
    "event_id",
    "timestamp",
    "classification",
    "action",
    "owner",
]

EVIDENCE_FIELDS = [
    "trace_id",
    "evidence_id",
]

CHECK_NAMES = [
    "structured",
    "observable",
    "deterministic",
    "governance_safe",
]


def present(value):
    return value not in (None, "", [])


def parse_timestamp(value):
    if not present(value):
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def empty_failures():
    return {name: 0 for name in CHECK_NAMES}


def pass_percent(processed_count, failure_count):
    if processed_count == 0:
        return None
    return round(((processed_count - failure_count) / processed_count) * 100, 2)


def metric_percentages(processed_count, failures):
    return {
        name: pass_percent(processed_count, failures[name])
        for name in CHECK_NAMES
    }


def format_percent(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def score_event(event):
    score = 100
    issues = []
    checks = {
        "structured": True,
        "observable": True,
        "deterministic": True,
        "governance_safe": True,
    }

    missing_structure = [
        field for field in STRUCTURE_FIELDS
        if not present(event.get(field))
    ]
    if missing_structure:
        checks["structured"] = False
        score -= len(missing_structure) * 5
        issues.append(f"missing required fields: {', '.join(missing_structure)}")

    missing_evidence = [
        field for field in EVIDENCE_FIELDS
        if not present(event.get(field))
    ]
    if missing_evidence:
        checks["observable"] = False
        score -= len(missing_evidence) * 10
        issues.append(f"missing evidence fields: {', '.join(missing_evidence)}")

    expected_action = event.get("replay_expected_action")
    actual_action = event.get("action")
    if present(expected_action) and expected_action != actual_action:
        checks["deterministic"] = False
        score -= 25
        issues.append("replay action does not match actual action")

    if event.get("approval_required") is True and event.get("approval_status") != "approved":
        checks["governance_safe"] = False
        score -= 25
        issues.append("approval was required but not approved")

    if event.get("authorized_actor") is False:
        checks["governance_safe"] = False
        score -= 25
        issues.append("actor was not authorized")

    score = max(score, 0)

    if score >= 90:
        rating = "stable"
    elif score >= 75:
        rating = "minor drift"
    elif score >= 50:
        rating = "operational drift"
    else:
        rating = "unsafe"

    return {
        "event_id": event.get("event_id", "unknown"),
        "score": score,
        "rating": rating,
        "checks": checks,
        "issues": issues,
    }


def load_events(path):
    input_path = Path(path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        return data.get("events", [])

    return data


def load_state(path):
    state_path = Path(path)

    if not state_path.exists():
        return {}

    with state_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(path, state):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with state_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)
        file.write("\n")


def default_state_file(input_path):
    path = Path(input_path)
    return str(path.with_name(f"{path.name}.state.json"))


def sort_events_by_time(events):
    timed_events = []

    for event in events:
        event_time = parse_timestamp(event.get("timestamp"))
        timed_events.append((event_time, event))

    timed_events.sort(key=lambda item: item[0])
    return timed_events


def filter_new_events(events, last_analyzed_at):
    if not last_analyzed_at:
        return events

    last_seen_time = parse_timestamp(last_analyzed_at)
    new_events = []

    for event in events:
        event_time = parse_timestamp(event.get("timestamp"))
        if event_time > last_seen_time:
            new_events.append(event)

    return new_events


def latest_timestamp(events):
    timed_events = sort_events_by_time(events)
    return timed_events[-1][1]["timestamp"]


def compare_with_previous(report, previous_run):
    if not previous_run:
        return {
            "has_previous_run": False,
            "message": "No previous run found. This run becomes the baseline.",
        }

    previous_events = previous_run.get("event_count", 0)
    current_events = report["event_count"]
    pressure_increased = current_events > previous_events

    metric_deltas = {}
    metrics_stable = True
    previous_metrics = previous_run.get("metric_percentages", {})

    for name in CHECK_NAMES:
        previous_value = previous_metrics.get(name)
        current_value = report["metric_percentages"][name]

        if previous_value is None or current_value is None:
            delta = None
        else:
            delta = round(current_value - previous_value, 2)
            if pressure_increased and delta < 0:
                metrics_stable = False

        metric_deltas[name] = {
            "previous": previous_value,
            "current": current_value,
            "delta": delta,
        }

    if current_events > previous_events:
        pressure_trend = "increased"
    elif current_events < previous_events:
        pressure_trend = "decreased"
    else:
        pressure_trend = "same"

    if pressure_increased:
        stable_under_pressure = "yes" if metrics_stable else "no"
    else:
        stable_under_pressure = "not tested because pressure did not increase"

    return {
        "has_previous_run": True,
        "previous_events_checked": previous_events,
        "current_events_checked": current_events,
        "pressure_trend": pressure_trend,
        "pressure_increased": pressure_increased,
        "metric_deltas": metric_deltas,
        "stable_under_increased_pressure": stable_under_pressure,
    }


def build_state_snapshot(report):
    return {
        "last_analyzed_at": report["latest_event_timestamp"],
        "last_run": {
            "event_count": report["event_count"],
            "events_checked": report["events_processed"],
            "metric_percentages": report["metric_percentages"],
            "average_score": report["average_score"],
            "final_answer": report["final_answer"],
        },
    }


def build_report(
    events,
    previous_run=None,
    last_analyzed_at=None,
    use_state=False,
):
    start_clock = time.perf_counter()
    all_results = []
    total_score = 0
    total_processed = 0
    total_drift = 0
    total_failures = empty_failures()

    for event in events:
        result = score_event(event)
        all_results.append(result)

        total_processed += 1
        total_score += result["score"]

        if result["rating"] != "stable":
            total_drift += 1

        for check_name, passed in result["checks"].items():
            if not passed:
                total_failures[check_name] += 1

    average_score = 0
    if total_processed:
        average_score = round(total_score / total_processed, 2)

    percentages = metric_percentages(total_processed, total_failures)
    intelligence_held = all(count == 0 for count in total_failures.values())
    elapsed_seconds = round(time.perf_counter() - start_clock, 4)

    report = {
        "input_events": len(events),
        "last_analyzed_at": last_analyzed_at,
        "latest_event_timestamp": latest_timestamp(events),
        "event_count": total_processed,
        "events_processed": total_processed,
        "average_score": average_score,
        "check_failures": total_failures,
        "metric_percentages": percentages,
        "operational_intelligence_held": intelligence_held,
        "final_answer": "yes" if intelligence_held else "no",
        "drift_events": total_drift,
        "results": all_results,
        "elapsed_seconds": elapsed_seconds,
    }
    report["comparison"] = (
        compare_with_previous(report, previous_run)
        if use_state
        else None
    )
    return report


def print_report(report):
    print("Operational Intelligence Check")
    print("------------------------------")
    print(f"Can it remain structured, observable, deterministic, and governance-safe? {report['final_answer']}")
    print()
    print("Stream context")
    print(f"- Input events: {report['input_events']}")
    print(f"- Latest event timestamp: {report['latest_event_timestamp']}")
    print(f"- Events checked: {report['events_processed']}")
    print()
    print("Four checks")
    print(
        "- Structured: "
        f"{format_percent(report['metric_percentages']['structured'])} pass "
        f"({report['check_failures']['structured']} failures)"
    )
    print(
        "- Observable: "
        f"{format_percent(report['metric_percentages']['observable'])} pass "
        f"({report['check_failures']['observable']} failures)"
    )
    print(
        "- Deterministic: "
        f"{format_percent(report['metric_percentages']['deterministic'])} pass "
        f"({report['check_failures']['deterministic']} failures)"
    )
    print(
        "- Governance-safe: "
        f"{format_percent(report['metric_percentages']['governance_safe'])} pass "
        f"({report['check_failures']['governance_safe']} failures)"
    )
    print()
    comparison = report["comparison"]
    if comparison:
        print("Compared with previous run")
        if not comparison["has_previous_run"]:
            print(f"- {comparison['message']}")
        else:
            print(f"- Pressure trend: {comparison['pressure_trend']}")
            print(
                f"- Events checked: {comparison['previous_events_checked']} -> "
                f"{comparison['current_events_checked']}"
            )
            print(
                "- Stable under increased pressure: "
                f"{comparison['stable_under_increased_pressure']}"
            )
            for name in CHECK_NAMES:
                delta = comparison["metric_deltas"][name]
                if delta["delta"] is None:
                    delta_text = "n/a"
                else:
                    delta_text = f"{delta['delta']:+.2f}%"

                print(
                    f"  {name}: "
                    f"{format_percent(delta['previous'])} -> "
                    f"{format_percent(delta['current'])} "
                    f"({delta_text})"
                )
        print()
    print("Other details")
    print(f"- Time taken: {report['elapsed_seconds']} seconds")
    print(f"- Average score: {report['average_score']}")
    print(f"- Drift events: {report['drift_events']}")
    print()

    for result in report["results"]:
        print(f"{result['event_id']}: {result['score']} ({result['rating']})")
        for issue in result["issues"]:
            print(f"  - {issue}")


def main():
    if len(sys.argv) == 1:
        print("This script needs an input JSON file.")
        print()
        print("Run it like this:")
        print("python operational_drift_monitor.py --input events.json")
        print("python operational_drift_monitor.py --input data/stream_events.json")
        print()
        print("See README.md for the expected input format.")
        return

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to events JSON file")
    parser.add_argument("--output", help="Optional path to save the report")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Analyze the full file and do not update saved state",
    )
    parser.add_argument(
        "--state-file",
        help="File used to remember the previous run",
    )
    args = parser.parse_args()

    try:
        events = load_events(args.input)
    except FileNotFoundError as error:
        print(error)
        return
    except json.JSONDecodeError:
        print(f"Could not read JSON from: {args.input}")
        return

    if not events:
        print("No events found in the input file.")
        return

    previous_run = None
    last_analyzed_at = None
    use_state = not args.full
    state_file = args.state_file or default_state_file(args.input)

    if use_state:
        try:
            state = load_state(state_file)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Could not read state file: {error}")
            return

        previous_run = state.get("last_run")
        last_analyzed_at = state.get("last_analyzed_at")

        try:
            events = filter_new_events(events, last_analyzed_at)
        except ValueError as error:
            print(f"Could not filter new events: {error}")
            return

        if not events:
            print("No new events since the last run.")
            return

    try:
        report = build_report(
            events,
            previous_run,
            last_analyzed_at,
            use_state,
        )
    except ValueError as error:
        print(f"Could not calculate event stream: {error}")
        return

    print_report(report)

    if use_state:
        try:
            save_state(state_file, build_state_snapshot(report))
        except OSError as error:
            print(f"Could not save state file: {error}")
            return

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)


if __name__ == "__main__":
    main()
