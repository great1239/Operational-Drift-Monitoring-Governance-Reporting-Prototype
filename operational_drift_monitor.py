import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import shutil
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from operational_update_parser import parse_update


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

DRIFT_CLASSES = [
    "aligned",
    "replay-risk",
    "authority-risk",
    "observability-risk",
    "integration-risk",
    "unclear/incomplete",
]

def present(value):
    return value not in (None, "", [])


def parse_timestamp(value):
    if not present(value):
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def positive_int(value):
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer greater than 0") from error

    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer greater than 0")

    return parsed


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


def empty_taxonomy_counts():
    return {name: 0 for name in DRIFT_CLASSES}


def format_percent(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def display_name(name):
    return name.replace("_", "-")


def status_class(final_answer):
    return "ok" if final_answer == "yes" else "bad"


def metric_class(value):
    if value is None:
        return "neutral"
    if value >= 95:
        return "ok"
    if value >= 80:
        return "warn"
    return "bad"


def metric_label(value):
    labels = {
        "ok": "stable",
        "warn": "watch",
        "bad": "risk",
        "neutral": "n/a",
    }
    return labels[metric_class(value)]


def delta_class(value):
    if value is None or value == 0:
        return "neutral"
    if value > 0:
        return "ok"
    return "bad"


def taxonomy_class(name):
    classes = {
        "aligned": "ok",
        "replay-risk": "warn",
        "authority-risk": "bad",
        "observability-risk": "blue",
        "integration-risk": "purple",
        "unclear/incomplete": "neutral",
    }
    return classes.get(name, "neutral")


def badge(text, class_name):
    return f"<span class=\"badge {class_name}\">{html.escape(text)}</span>"


def metric_bar(value):
    if value is None:
        progress_value = 0
        class_name = "neutral"
    else:
        progress_value = max(0, min(value, 100))
        class_name = metric_class(value)

    return (
        f"<progress class=\"metric-progress {class_name}\" "
        f"value=\"{progress_value:.2f}\" max=\"100\">"
        f"{format_percent(value)}</progress>"
    )


def is_parsed_update(event):
    return "raw_update" in event or "system_status" in event


def is_raw_update(event):
    if isinstance(event, str):
        return True

    if not isinstance(event, dict):
        return False

    return (
        "text" in event
        and "raw_update" not in event
        and "classification" not in event
    )


def normalize_events(records):
    if not records:
        return records

    return [
        parse_update(record, index) if is_raw_update(record) else record
        for index, record in enumerate(records, start=1)
    ]


def fallback_timestamp(index):
    timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def log_lines_to_updates(lines, prefix):
    updates = []

    for index, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            continue

        updates.append({
            "update_id": f"{prefix}-{index:06d}",
            "timestamp": fallback_timestamp(index),
            "text": text,
        })

    return updates


def required_structure_fields(event):
    if is_parsed_update(event):
        return [
            "event_id",
            "timestamp",
            "raw_update",
            "system_status",
            "drift_classification",
        ]

    return STRUCTURE_FIELDS


def classify_event(event, checks):
    if is_parsed_update(event):
        parsed_classification = event.get("drift_classification")
        if parsed_classification in DRIFT_CLASSES:
            return parsed_classification

    if not checks["structured"]:
        return "unclear/incomplete"
    if not checks["governance_safe"]:
        return "authority-risk"
    if not checks["observable"]:
        return "observability-risk"
    if not checks["deterministic"]:
        return "replay-risk"
    if event.get("blockers") or event.get("dependencies"):
        return "integration-risk"

    return "aligned"


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
        field for field in required_structure_fields(event)
        if not present(event.get(field))
    ]
    if missing_structure:
        checks["structured"] = False
        score -= len(missing_structure) * 5
        issues.append(f"missing required fields: {', '.join(missing_structure)}")

    if is_parsed_update(event):
        if event.get("drift_classification") == "unclear/incomplete":
            checks["structured"] = False
            score -= 25
            issues.append("update is unclear or incomplete")

        if event.get("blockers") or event.get("dependencies"):
            score -= 15
            issues.append("blocker or dependency risk found in update")

        if event.get("observability_risks"):
            checks["observable"] = False
            score -= 25
            issues.append("observability risk found in update")

        if event.get("replay_risks"):
            checks["deterministic"] = False
            score -= 25
            issues.append("replay risk found in update")

        if event.get("governance_risks"):
            checks["governance_safe"] = False
            score -= 25
            issues.append("governance risk found in update")
    else:
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
    drift_classification = classify_event(event, checks)

    return {
        "event_id": event.get("event_id", "unknown"),
        "score": score,
        "drift_classification": drift_classification,
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
        if "events" in data:
            return normalize_events(data["events"])
        if "updates" in data:
            return normalize_events(data["updates"])
        return []

    return normalize_events(data)


def load_events_from_url(url, limit_lines):
    lines = []
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "operational-drift-monitor"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)

            if len(lines) >= limit_lines:
                break

    return normalize_events(log_lines_to_updates(lines, "URL"))


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
    if str(input_path).startswith(("http://", "https://")):
        return ".monitor_url_state.json"

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
            "taxonomy_counts": report["taxonomy_counts"],
            "average_score": report["average_score"],
            "final_answer": report["final_answer"],
        },
    }


def top_risk_labels(report):
    risks = [
        name
        for name in DRIFT_CLASSES
        if name != "aligned" and report["taxonomy_counts"].get(name, 0) > 0
    ]
    return risks or ["none"]


ACTION_BY_RISK = {
    "authority-risk": "lock approval and authorization checks before execution",
    "observability-risk": "require trace IDs, evidence IDs, and decision logs",
    "replay-risk": "add replay checks before accepting the action",
    "integration-risk": "resolve blockers and dependency ownership",
    "unclear/incomplete": "reject incomplete updates until required fields are present",
}

RISK_IMPORTANCE_ORDER = [
    "authority-risk",
    "observability-risk",
    "replay-risk",
    "integration-risk",
    "unclear/incomplete",
]


def highest_importance_risk(counts):
    for risk in RISK_IMPORTANCE_ORDER:
        if counts[risk]:
            return risk

    return None


def highest_frequency_risk(counts):
    active_risks = [
        risk for risk in RISK_IMPORTANCE_ORDER
        if counts[risk] > 0
    ]

    if not active_risks:
        return None

    return max(
        active_risks,
        key=lambda risk: (counts[risk], -RISK_IMPORTANCE_ORDER.index(risk)),
    )


def recommended_actions(report):
    counts = report["taxonomy_counts"]
    importance_risk = highest_importance_risk(counts)
    frequency_risk = highest_frequency_risk(counts)

    if not importance_risk:
        return {
            "importance": "continue monitoring; no immediate remediation required",
            "frequency": "continue monitoring; no repeated risk pattern found",
        }

    return {
        "importance": (
            f"{importance_risk}: {ACTION_BY_RISK[importance_risk]}"
        ),
        "frequency": (
            f"{frequency_risk}: {ACTION_BY_RISK[frequency_risk]}"
        ),
    }


def build_governance_summary(report):
    comparison = report["comparison"]
    actions = recommended_actions(report)

    if comparison and comparison["has_previous_run"]:
        pressure_line = (
            f"Pressure: {comparison['previous_events_checked']} -> "
            f"{comparison['current_events_checked']} events "
            f"({comparison['pressure_trend']})."
        )
    elif comparison:
        pressure_line = "Pressure: first run, baseline created."
    else:
        pressure_line = f"Pressure: {report['event_count']} events in this run."

    taxonomy = report["taxonomy_counts"]
    drift_line = (
        "Drift: "
        f"aligned={taxonomy['aligned']}, "
        f"replay-risk={taxonomy['replay-risk']}, "
        f"authority-risk={taxonomy['authority-risk']}, "
        f"observability-risk={taxonomy['observability-risk']}, "
        f"integration-risk={taxonomy['integration-risk']}, "
        f"unclear/incomplete={taxonomy['unclear/incomplete']}."
    )

    return [
        f"Status: operational intelligence held = {report['final_answer']}.",
        pressure_line,
        drift_line,
        f"Risk: primary risk labels = {', '.join(top_risk_labels(report))}.",
        (
            "Action: "
            f"importance -> {actions['importance']}; "
            f"frequency -> {actions['frequency']}."
        ),
    ]


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
    taxonomy_counts = empty_taxonomy_counts()

    for event in events:
        result = score_event(event)
        all_results.append(result)

        total_processed += 1
        total_score += result["score"]

        taxonomy_counts[result["drift_classification"]] += 1

        if result["drift_classification"] != "aligned":
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
        "taxonomy_counts": taxonomy_counts,
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
    report["governance_summary"] = build_governance_summary(report)
    return report


STYLESHEET_FILENAME = "dashboard.css"


def dashboard_css_path(dashboard_path):
    return Path(dashboard_path).with_name(STYLESHEET_FILENAME)


def write_dashboard_css(dashboard_path):
    source_path = Path(__file__).with_name(STYLESHEET_FILENAME)
    css_path = dashboard_css_path(dashboard_path)
    css_path.parent.mkdir(parents=True, exist_ok=True)

    if not source_path.exists():
        raise FileNotFoundError(f"Stylesheet not found: {source_path}")

    if source_path.resolve() != css_path.resolve():
        shutil.copyfile(source_path, css_path)

    return css_path


def dashboard_page(title, body, css_filename):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{html.escape(css_filename)}">
</head>
<body>
<main class="page">
  {body}
</main>
</body>
</html>
"""


def write_status_dashboard(path, title, message):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    css_path = write_dashboard_css(output_path)
    body = f"""
  <h1>Operational Drift Dashboard</h1>
  <h2>{html.escape(title)}</h2>
  <p class="note">{html.escape(message)}</p>
"""
    output_path.write_text(
        dashboard_page(title, body, css_path.name),
        encoding="utf-8",
    )


def open_dashboard(path):
    dashboard_uri = Path(path).resolve().as_uri()
    opened = webbrowser.open(dashboard_uri)

    if not opened:
        print(f"Dashboard written to {path}, but it could not be opened automatically.")


def comparison_html(report):
    comparison = report["comparison"]

    if not comparison:
        return "<p class=\"note\">Full-file run. No previous-run comparison was used.</p>"

    if not comparison["has_previous_run"]:
        return f"<p class=\"note\">{html.escape(comparison['message'])}</p>"

    rows = "\n".join(
        f"<tr><td>{html.escape(display_name(name))}</td>"
        f"<td>{format_percent(comparison['metric_deltas'][name]['previous'])}</td>"
        f"<td>{format_percent(comparison['metric_deltas'][name]['current'])}</td>"
        f"<td>{badge(format_delta(comparison['metric_deltas'][name]['delta']), delta_class(comparison['metric_deltas'][name]['delta']))}</td></tr>"
        for name in CHECK_NAMES
    )

    return f"""
  <p class="note">
    Pressure trend: {html.escape(comparison['pressure_trend'])}.
    Events checked: {comparison['previous_events_checked']} -> {comparison['current_events_checked']}.
    Stable under increased pressure: {html.escape(comparison['stable_under_increased_pressure'])}.
  </p>
  <div class="table-wrap">
    <table>
      <tr><th>Metric</th><th>Previous</th><th>Current</th><th>Delta</th></tr>
      {rows}
    </table>
  </div>
"""


def format_delta(value):
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def summary_line_html(line):
    heading, separator, detail = line.partition(":")

    if not separator:
        return html.escape(line)

    return (
        f"<strong class=\"summary-heading\">{html.escape(heading)}:</strong> "
        f"{html.escape(detail.strip())}"
    )


def drift_summary_html(report):
    drift_lines = "\n".join(
        f"<span>{html.escape(name)}={report['taxonomy_counts'][name]}</span>"
        for name in DRIFT_CLASSES
    )

    return (
        "<strong class=\"summary-heading\">Drift:</strong>"
        f"<div class=\"summary-lines\">{drift_lines}</div>"
    )


def action_summary_html(report):
    actions = recommended_actions(report)
    return (
        "<strong class=\"summary-heading\">Action:</strong>"
        "<div class=\"summary-lines\">"
        f"<span><strong>importance</strong> {html.escape(actions['importance'])}</span>"
        f"<span><strong>frequency</strong> {html.escape(actions['frequency'])}</span>"
        "</div>"
    )


def governance_summary_items_html(report):
    items = []

    for index, line in enumerate(report["governance_summary"]):
        if index == 2:
            items.append(f"<li>{drift_summary_html(report)}</li>")
        elif index == 4:
            items.append(f"<li>{action_summary_html(report)}</li>")
        else:
            items.append(f"<li>{summary_line_html(line)}</li>")

    return "\n".join(items)


def write_dashboard(path, report):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    css_path = write_dashboard_css(output_path)
    metric_rows = "\n".join(
        f"<tr><td>{html.escape(display_name(name))}</td>"
        f"<td class=\"metric-cell\"><div class=\"metric-top\">"
        f"<strong>{format_percent(report['metric_percentages'][name])}</strong>"
        f"{badge(metric_label(report['metric_percentages'][name]), metric_class(report['metric_percentages'][name]))}"
        f"</div>{metric_bar(report['metric_percentages'][name])}</td>"
        f"<td>{report['check_failures'][name]}</td></tr>"
        for name in CHECK_NAMES
    )
    taxonomy_rows = "\n".join(
        f"<tr><td>{badge(name, taxonomy_class(name))}</td>"
        f"<td>{report['taxonomy_counts'][name]}</td></tr>"
        for name in DRIFT_CLASSES
    )
    summary_items = governance_summary_items_html(report)
    event_rows = "\n".join(
        f"<tr><td>{html.escape(str(result['event_id']))}</td>"
        f"<td>{badge(str(result['score']), metric_class(result['score']))}</td>"
        f"<td>{badge(result['drift_classification'], taxonomy_class(result['drift_classification']))}</td>"
        f"<td class=\"issues\">{html.escape('; '.join(result['issues']) or 'none')}</td></tr>"
        for result in report["results"]
    )

    body = f"""
  <div class="header">
    <div>
      <div class="eyebrow">Operational Intelligence</div>
      <h1>Drift Dashboard</h1>
      <p class="subtle">Latest event: {html.escape(str(report['latest_event_timestamp']))}</p>
    </div>
    {badge(f"Operational intelligence held: {report['final_answer']}", status_class(report['final_answer']))}
  </div>

  <div class="grid">
    <div class="card"><div class="label">Events Checked</div><div class="value">{report['events_processed']}</div></div>
    <div class="card"><div class="label">Drift Events</div><div class="value">{report['drift_events']}</div></div>
    <div class="card"><div class="label">Average Score</div><div class="value">{report['average_score']}</div></div>
    <div class="card"><div class="label">Runtime</div><div class="value">{report['elapsed_seconds']}s</div></div>
  </div>

  <h2>5-Line Governance Summary</h2>
  <ol class="summary">{summary_items}</ol>

  <h2>Pressure Comparison</h2>
  {comparison_html(report)}

  <h2>Metric Pass Rates</h2>
  <div class="table-wrap">
    <table>
      <tr><th>Metric</th><th>Pass Rate</th><th>Failures</th></tr>
      {metric_rows}
    </table>
  </div>

  <h2>Drift Taxonomy</h2>
  <div class="table-wrap">
    <table>
      <tr><th>Classification</th><th>Count</th></tr>
      {taxonomy_rows}
    </table>
  </div>

  <h2>Event Findings</h2>
  <div class="table-wrap">
    <table>
      <tr><th>Event</th><th>Score</th><th>Classification</th><th>Issues</th></tr>
      {event_rows}
    </table>
  </div>
"""

    output_path.write_text(
        dashboard_page("Operational Drift Dashboard", body, css_path.name),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Path to events or updates JSON file")
    parser.add_argument("--input-url", help="URL to a raw text log file")
    parser.add_argument(
        "--limit-lines",
        type=positive_int,
        default=200,
        help="Maximum number of URL log lines to read",
    )
    parser.add_argument(
        "--dashboard",
        default="dashboard.html",
        help="Path to write the HTML dashboard",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Analyze the full file and do not update saved state",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the dashboard automatically after a full run",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the dashboard after the run, even when using saved state",
    )
    parser.add_argument(
        "--state-file",
        help="File used to remember the previous run",
    )
    args = parser.parse_args()

    if bool(args.input) == bool(args.input_url):
        print("Provide exactly one input source: --input or --input-url.")
        return

    try:
        if args.input_url:
            events = load_events_from_url(args.input_url, args.limit_lines)
            input_source = args.input_url
        else:
            events = load_events(args.input)
            input_source = args.input
    except FileNotFoundError as error:
        write_status_dashboard(args.dashboard, "Input file not found", str(error))
        print(error)
        return
    except urllib.error.URLError as error:
        message = f"Could not read URL: {error}"
        write_status_dashboard(args.dashboard, "URL error", message)
        print(message)
        return
    except json.JSONDecodeError:
        message = f"Could not read JSON from: {args.input}"
        write_status_dashboard(args.dashboard, "Invalid JSON", message)
        print(message)
        return

    if not events:
        write_status_dashboard(
            args.dashboard,
            "No events found",
            f"No events were found in {input_source}.",
        )
        print(f"Dashboard written to {args.dashboard}")
        return

    previous_run = None
    last_analyzed_at = None
    use_state = not args.full
    state_file = args.state_file or default_state_file(input_source)

    if use_state:
        try:
            state = load_state(state_file)
        except (OSError, json.JSONDecodeError) as error:
            message = f"Could not read state file: {error}"
            write_status_dashboard(args.dashboard, "State file error", message)
            print(message)
            return

        previous_run = state.get("last_run")
        last_analyzed_at = state.get("last_analyzed_at")

        try:
            events = filter_new_events(events, last_analyzed_at)
        except ValueError as error:
            message = f"Could not filter new events: {error}"
            write_status_dashboard(args.dashboard, "Timestamp error", message)
            print(message)
            return

        if not events:
            write_status_dashboard(
                args.dashboard,
                "No new events",
                "No events were newer than the last analyzed timestamp.",
            )
            print(f"Dashboard written to {args.dashboard}")
            if args.open:
                open_dashboard(args.dashboard)
            return

    try:
        report = build_report(
            events,
            previous_run,
            last_analyzed_at,
            use_state,
        )
    except ValueError as error:
        message = f"Could not calculate event stream: {error}"
        write_status_dashboard(args.dashboard, "Event calculation error", message)
        print(message)
        return

    try:
        write_dashboard(args.dashboard, report)
    except OSError as error:
        print(f"Could not write dashboard: {error}")
        return

    if use_state:
        try:
            save_state(state_file, build_state_snapshot(report))
        except OSError as error:
            print(f"Could not save state file: {error}")
            return

    print(f"Dashboard written to {args.dashboard}")

    if args.open or (args.full and not args.no_open):
        open_dashboard(args.dashboard)


if __name__ == "__main__":
    main()
