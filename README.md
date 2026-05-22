# Operational Drift Monitoring

## Problem

The goal is to check whether operational intelligence can stay:

- structured
- observable
- deterministic
- governance-safe

when execution pressure changes.

## Approach

Each scheduled run is treated as one batch of events.

For each event, the monitor checks:

- are the important fields present?
- is there evidence for the decision?
- does the replayed action match the actual action?
- were approvals and authorization followed?

The basic flow is:

event -> decision -> action -> evidence -> outcome

If information is missing, decisions are inconsistent, or approvals are skipped, the event is counted as drift.


## How Pressure Is Compared

The monitor remembers the previous run.

Example:

```text
11 AM run: 50 events
12 PM run: 150 new events
```

Since the new run has more events than the previous run, execution pressure increased.

The monitor then compares:

- structured %
- observable %
- deterministic %
- governance-safe %

If event count increased and any of those percentages dropped, operational intelligence did not remain stable under pressure.

## Scoring

Each event gets a score out of 100.

- missing required field: minus 5
- missing evidence field: minus 10
- replay action mismatch: minus 25
- missing approval: minus 25
- unauthorized actor: minus 25

Rating:

- 90-100: stable
- 75-89: minor drift
- 50-74: operational drift
- below 50: unsafe

## How To Run

Analyze the sample file:

```bash
python operational_drift_monitor.py --input events.json
```

Analyze a generated event stream:

```bash
python operational_drift_monitor.py --input data/stream_events.json
```

By default, the monitor saves state beside the input file. This lets the next run analyze only new events and compare the new metrics with the previous run.

For a one-off full-file analysis:

```bash
python operational_drift_monitor.py --input events.json --full
```

## Generate A Stream

`event_stream_generator.py` is only a testing helper.

It creates fake operational events so the monitor can be tested without connecting to a real system.

For actual use, you do not need this generator. Use your own JSON file or your own program that keeps writing operational events in the expected format.

Generate a growing event file:

```bash
python event_stream_generator.py --output data/stream_events.json --reset --max-events 25
```

Generate a continuous stream:

```bash
python event_stream_generator.py --output data/stream_events.json
```

Stop the continuous stream with `Ctrl+C`.

Useful generator options:

- `--interval-seconds 1`: average delay between event batches
- `--jitter-seconds 0.2`: random variation around that delay
- `--drift-rate 0.15`: chance that an event contains a drift condition
- `--burst-chance 0.2`: chance that one interval emits several events
- `--max-burst-events 5`: largest burst size
- `--seed 42`: repeat the same simulated sequence
- `--reset`: overwrite the output file before generating new events



## Scheduled Runs

Use Windows Task Scheduler or another scheduler to run the monitor regularly.

Example scheduled command:

```bash
python operational_drift_monitor.py --input data/stream_events.json
```

How it works:

- first scheduled run stores a baseline
- next scheduled run checks only new events
- it compares the new event count with the previous event count
- it compares the four metric percentages
- it reports whether the metrics stayed stable when pressure increased

## Input Format

Each event should look roughly like this:

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

The input file can be a list of events, or an object with an `events` list.

## Conclusion

If event count increases and the four percentages stay stable, operational intelligence is holding up under pressure.

If event count increases and any of the four percentages drop, operational drift is happening.
