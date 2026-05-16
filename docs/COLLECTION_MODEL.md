# Collection Model

This document defines the collection stage and the collector plugin model for
Cloud AV Agent Lab.

## Scope

The collection stage runs after delivery and optional controlled execution. It
is responsible for reading security product logs inside the cloud-isolated
Windows guest, normalizing product-specific evidence, and writing
`case_collection.json`.

The collector must not read sample bytes. It must not execute samples. It must
not expose arbitrary path reads, shell commands, PowerShell, cmd, exec, or
run-command behavior. Local control still only calls the Guest Agent through
HTTP via `NetworkClient`; the local host does not execute or inspect samples.

## Collector Plugin Model

Each security product has its own collector package under the Guest Agent
server:

```text
src/cloud_av_agent_lab/guest_agent_server/collectors/
  base.py
  huorong/
    collector.py
    parser.py
    schema.py
```

`collectors/base.py` defines the stable schema:

- `CollectionWindow`: the time window used to attribute product logs to the
  current case.
- `NormalizedSecurityEvent`: a product-independent event record for evidence.
- `CollectorResult`: the product collector result written into
  `case_collection.json`.
- `ProductLogCollector`: the base interface implemented by product collectors.

Product collectors own only product-specific details: log locations, copy
strategy, raw field parsing, and conservative evidence matching. The rest of
the system consumes normalized events and collector results; it should not need
to know raw product database columns.

## Huorong MVP

The Huorong collector reads:

```text
C:\ProgramData\Huorong\sysdiag\
  log.db
  log.db-shm
  log.db-wal
```

The collector first copies the SQLite WAL file set into the current case
artifact directory:

```text
<workdir>\cases\<case_id>\collection\huorong\
```

It then opens the copied `log.db` in read-only mode and queries the Huorong
event table. The first observed table is `HrLogV3_60`, but the implementation
discovers the latest `HrLogV3_*` table to tolerate product schema/version
rotation. The JSON payload column is also schema-tolerant: the collector first
looks for known names such as `detail`, `raw_json`, `payload`, `data`, or
`event_json`; if none exists, it follows the export helper convention and treats
the fifth column as the raw JSON payload. In the observed Huorong schema, the
database column is named `detail`, and the JSON stored in that column also has a
nested `detail` object. The parser reads that nested `detail` object for fields
such as `recname`, `description`, `risk`, `action`, `treatment`, `result`, path
fields, PID fields, and hashes. Single-row parse failures are recorded as
collector errors and do not abort the entire collection. SQLite-level failures
include a safe diagnostic message, such as missing table names and available
tables, without returning sample contents or tokens.

## Unified Event Timeline

`case_collection.json` contains a unified event timeline. It merges Guest Agent
case events with normalized product-log events and sorts them by
`timestamp_utc`.

Unified timeline records use this shape:

```json
{
  "timestamp_utc": "...",
  "source": "guest_agent | upload_observer | execution_observer | product_log",
  "event_type": "sample_saved | execution_started | av_detected",
  "case_id": "...",
  "sample_id": "...",
  "product_id": "...",
  "confidence": "high | medium | low",
  "message": "...",
  "evidence": {},
  "raw_ref": "..."
}
```

Delivery and execution observations remain facts, not AV evidence. For example,
`removed_after_save` and `terminated_or_disappeared` are useful facts, but they
do not by themselves prove that a security product intercepted the sample.

## Time Window

Every collector receives a time window. The first MVP anchors it to the case
metadata already present in the workspace:

- `case_prepared_at_utc`
- `uploaded_at_utc`
- `execution_started_at_utc`
- `collection_started_at_utc`
- `collection_finished_at_utc`

The current window starts with a small buffer before upload or preparation and
ends at collection time. Product-log rows outside the window are ignored when a
parseable timestamp is available. This prevents old product detections from
being attributed to the current case.

Future versions may tune per-product windows, but all times must stay
normalized to UTC and should keep the raw product timestamp available in
evidence when useful.

## Conservative Verdict

Collector verdicts are intentionally conservative and product-local:

- `intercepted`: product logs contain case-attributable evidence such as a hash,
  case sample path, or current-case PID match plus detection, quarantine,
  deletion, or blocking fields.
- `not_intercepted`: collection completed and no product-log evidence matched
  within the time window.
- `unknown`: logs could not be read, the window is unreliable, the case cannot
  be attributed safely, or collection was incomplete and produced no evidence.

Important non-rules:

- `removed_after_save` without product-log evidence is not automatically
  `intercepted`; it should remain `unknown` or a separate reason such as
  `removed_without_product_log` in later result aggregation.
- A process exiting or disappearing is not automatically an AV block.
- A collector should prefer `unknown` over a confident false negative when logs
  are missing or unreadable.

This keeps batch interception-rate statistics conservative and reviewable.

## Evaluator And Exporter

The collection stage does not own the final user-facing conclusion. It only
produces normalized evidence. The next layer, `cloud_av_agent_lab.evaluation`,
combines:

- delivery state from `case_state.json` / `case_report.json`;
- execution observation state from the controlled action model;
- product evidence from `case_collection.json`;
- recent Guest Agent events from `events.jsonl`.

The evaluator writes `case_summary.json` and may also render
`case_summary.md`. These user-facing summaries contain a compact timeline,
not the full polling stream. Repeated `sample_post_upload_check` and
unchanged `execution_observed` polling events are collapsed so a reader sees
only key state changes. The full audit stream remains in `events.jsonl`.
Its verdict vocabulary is broader than a collector verdict:

- `detected_or_blocked`: product evidence clearly matched the case.
- `suspiciously_removed`: the uploaded file was saved once and later removed,
  but product evidence is missing.
- `no_detection_observed`: the observation window completed without product
  evidence, and execution was observable enough for a cautious statement.
- `execution_not_observed`: execution did not happen or was not observable, so a
  no-detection claim would be too strong.
- `inconclusive` / `unknown`: evidence is incomplete or contradictory.

The exporter then creates a metadata-only evidence bundle. It includes
`manifest.json`, case metadata, normalized product evidence, summaries, and
events. It excludes the uploaded sample body, `sample/` directories, tokens,
environment variables, cloud credentials, raw copied product databases, and real
cloud configuration files. The manifest stores file-level SHA-256 hashes so the
archive is easy to verify without needing to include unsafe artifacts.
