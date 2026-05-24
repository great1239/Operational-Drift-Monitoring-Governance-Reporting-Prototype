# Operational Drift Monitoring

## Problem

The goal is to check whether operational intelligence can stay:

- structured
- observable
- deterministic
- governance-safe

when execution pressure changes.

## What This Project Does

This project has two main parts:

1. `operational_update_parser.py`
   - parses operational updates into structured fields
   - extracts system status, blockers, dependencies, replay risks, observability risks, and governance risks
   - assigns the required drift classification

2. `operational_drift_monitor.py`
   - evaluates raw updates, parsed updates, or structured event JSON
   - compares each scheduled run with the previous run
   - checks whether the four metrics stay stable when event count increases
   - generates a simple HTML dashboard

## Drift Taxonomy

The monitor uses these classifications:

- `aligned`
- `replay-risk`
- `authority-risk`
- `observability-risk`
- `integration-risk`
- `unclear/incomplete`

## How Pressure Is Compared

Each scheduled run is treated as one batch.

Example:

```text
11 AM run: 50 events
12 PM run: 150 new events
```

Since the second run has more events, execution pressure increased.

The monitor compares:

- structured %
- observable %
- deterministic %
- governance-safe %

If event count increases and any percentage drops, operational intelligence did not remain stable under pressure.

## Run The Parser

You do not have to run the parser separately for the normal demo.
The monitor can read `updates.json` directly

IMPORTANT: 'updates.json' is a placeholder file name. To use self generated logs/datastream, replace 'updates.json' with the respective json file :

```bash
python operational_drift_monitor.py --input updates.json --full
```

This writes the dashboard to `dashboard.html` and opens it automatically.

If you want to generate the dashboard without opening the browser:

```bash
python operational_drift_monitor.py --input updates.json --full --no-open
```

Run the parser separately only if you want to inspect the parsed fields:

```bash
python operational_update_parser.py --input updates.json --output parsed_updates.json
```

Then you can monitor that parsed output:

```bash
python operational_drift_monitor.py --input parsed_updates.json --full
```

## Run The Monitor

Analyze the sample event file:

```bash
python operational_drift_monitor.py --input events.json --full
```

During a full demo run, it opens the dashboard automatically.

Use `--full` only when you want a one-off full-file analysis.
Scheduled runs should usually omit `--full`, so they update the dashboard without opening a browser every time.


Use a different dashboard filename if needed:

```bash
python operational_drift_monitor.py --input parsed_updates.json --full --dashboard my_dashboard.html
```

## Scheduled Runs

For actual monitoring, run the monitor regularly with Windows Task Scheduler or another scheduler:

```bash
python operational_drift_monitor.py --input data/stream_updates.json
```

By default, the monitor saves state beside the input file. The next run only checks events newer than the last analyzed timestamp and compares them with the previous run.



## 5-Line Governance Summary

The dashboard includes a compressed 5-line governance summary:

1. Status
2. Pressure
3. Drift taxonomy counts
4. Primary risk labels
5. Recommended action


## Test Event Generator

`event_stream_generator.py` is only a testing helper.

It creates fake raw operational updates so the parser and monitor can be tested without connecting to a real system.

For actual use, you do not need this generator. Use your own JSON file or your own program that keeps writing operational updates in the expected format.


Generate a test stream:

```bash
python event_stream_generator.py --output data/stream_updates.json --reset --max-events 25 --interval-seconds 0 --jitter-seconds 0
```

Then generate a dashboard from that stream:

```bash
python operational_drift_monitor.py --input data/stream_updates.json
```

Generate a continuous test stream:

```bash
python event_stream_generator.py --output data/stream_updates.json
```

Stop the continuous stream with `Ctrl+C`.

One-command generated raw update demo:

```bash
python event_stream_generator.py --output data/stream_updates_demo.json --reset --max-events 50 --quiet --interval-seconds 0 --jitter-seconds 0; python operational_drift_monitor.py --input data/stream_updates_demo.json --full
```

## Test With A Raw Log URL

The monitor can read a limited number of raw text lines directly from a URL.
Each line is treated as one raw operational update.
This is generic URL input; there is no dataset-specific parser.

Rootly Apache error log example:

```bash
python operational_drift_monitor.py --input-url https://raw.githubusercontent.com/Rootly-AI-Labs/logs-dataset/main/apache/apache_error.log --limit-lines 300 --full
```

Apache access log example:

```bash
python operational_drift_monitor.py --input-url https://raw.githubusercontent.com/Rootly-AI-Labs/logs-dataset/main/apache/apache_access.log --limit-lines 300 --full
```

The program reads only the requested number of lines, converts each line into a raw update, parses it with the same rule-based update parser, and writes the dashboard.

## Input Format

The monitor accepts raw operational updates, parsed updates, or structured events.

Raw operational update example:

```json
{
  "update_id": "UPD-001",
  "timestamp": "2026-05-24T10:00:00Z",
  "text": "Checkout latency is degraded. Missing trace and approval pending."
}
```

Structured event example:

```json
{
  "event_id": "EVT-001",
  "timestamp": "2026-05-20T10:00:00Z",
  "classification": "critical",
  "action": "rollback_release",
  "owner": "ops-team",
  "trace_id": "TRACE-001",
  "evidence_id": "EVID-001",
  "approval_required": true,
  "approval_status": "approved",
  "authorized_actor": true,
  "replay_expected_action": "rollback_release"
}
```

Parsed operational update example:

```json
{
  "event_id": "UPD-001",
  "timestamp": "2026-05-24T10:00:00Z",
  "raw_update": "Checkout latency is degraded. Missing trace and approval pending.",
  "system_status": "degraded",
  "blockers": ["pending"],
  "dependencies": [],
  "replay_risks": [],
  "observability_risks": ["missing trace"],
  "governance_risks": ["approval pending"],
  "drift_classification": "authority-risk"
}
```

## Files

- `operational_update_parser.py`: parses raw updates
- `operational_drift_monitor.py`: evaluates drift and writes the HTML dashboard
- `event_stream_generator.py`: fake raw update stream generator for testing only
- `dashboard.html`: generated dashboard evidence
- `dashboard.css`: generated dashboard styling
- `REVIEW_PACKET.md`: architecture, limitations, and reflection

## Conclusion

If event count increases and the four metric percentages stay stable, operational intelligence is holding up under pressure.

If event count increases and any of the four percentages drop, operational drift is happening.
