## Architecture

operational updates/logs
        |
        v
operational_update_parser.py
        |
        v
parsed structured updates
        |
        v
operational_drift_monitor.py
        |
        v
HTML dashboard with 5-line governance summary

## What Is Being Tested

The project checks whether operational intelligence remains:

- structured
- observable
- deterministic
- governance-safe

The comparison is made across scheduled runs. If a later run has more new events than the previous run, execution pressure increased. The monitor then checks whether the four metric percentages stayed stable or dropped.

## Drift Taxonomy

The required drift classifications are:

- `aligned`
- `replay-risk`
- `authority-risk`
- `observability-risk`
- `integration-risk`
- `unclear/incomplete`

If an update has multiple risk signals, the monitor assigns one primary classification. Authority risk takes priority because approval and authorization failures are usually the highest governance concern.


## Demo Commands

Generate dashboard from operational updates:

```bash
python operational_drift_monitor.py --input updates.json --full
```

The monitor parses the raw updates internally, creates `dashboard.html`, and opens it automatically during the demo run.
`dashboard.html` is generated output, so it should be opened after `operational_drift_monitor.py` has been run at least once with data.

Run the parser separately only if the parsed fields need to be inspected:

```bash
python operational_update_parser.py --input updates.json --output parsed_updates.json
```

Then run the monitor on parsed updates:

```bash
python operational_drift_monitor.py --input parsed_updates.json --full
```

This creates `dashboard.html`, which is the simple dashboard artifact for review.

Use a custom dashboard filename if needed:

```bash
python operational_drift_monitor.py --input parsed_updates.json --full --dashboard review_dashboard.html
```

Generate test stream data:

```bash
python event_stream_generator.py --output data/stream_updates.json --reset --max-events 25 --interval-seconds 0 --jitter-seconds 0
```

Run scheduled-style monitoring:

```bash
python operational_drift_monitor.py --input data/stream_updates.json
```

Test with a raw log URL without downloading the full dataset:

```bash
python operational_drift_monitor.py --input-url https://raw.githubusercontent.com/Rootly-AI-Labs/logs-dataset/main/apache/apache_error.log --limit-lines 300 --full
```

## Limitations

- The parser is rule-based, so it depends on keywords and simple patterns.
- It does not understand every possible wording of an operational update.
- The event generator is only fake raw update data for testing.
- In real use, the monitor should read an actual operational log or update JSON produced by another system.
- URL input reads a limited number of raw text lines and converts them into update records in memory. It does not use dataset-specific parsing.
- The dashboard is a simple generated HTML file, not a live web app.
- Dashboard styling is kept in the separate `dashboard.css` stylesheet.
- The dashboard is the reporting surface; the terminal only confirms where the HTML file was written.
- The dashboard files are generated output and may be missing or stale until the monitor is run with input data.
- Full demo runs open the dashboard automatically. Scheduled runs should usually omit `--full` so they do not open a browser every time. Manual stateful runs can use `--open`.
- The scheduler is not coded into the project; Windows Task Scheduler or another scheduler should run the monitor.

## Failure Cases

- Missing timestamps prevent correct state tracking.
- Vague updates can become `unclear/incomplete`.
- If a real system writes malformed JSON, the monitor cannot analyze it.
- If the same event timestamp is reused incorrectly, the state filter may skip or duplicate analysis.
- Keyword-only parsing may miss risks written in unusual language.

## Final Reflection

Repeated operational patterns usually show up as missing evidence, pending approvals, replay mismatches, or dependency blockers.

Ambiguity matters because vague updates cannot be safely classified. If the update does not say what changed, who owns it, or what evidence exists, the monitor should treat it as incomplete instead of guessing.

Deterministic reporting requires fixed rules. The same update should receive the same classification every time, which is why the parser uses explicit keyword checks instead of random or model-generated labels.

Drift frequency is useful because a single bad update may be noise, but repeated observability-risk or authority-risk updates show that the workflow is breaking under pressure.

## README Confirmation

The README explains:

- how to parse updates
- how to run the monitor
- how to generate a dashboard
- how scheduled runs compare pressure over time
- how the test generator is only used for fake data
