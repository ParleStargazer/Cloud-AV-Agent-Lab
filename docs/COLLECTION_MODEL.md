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

## Security Product Readiness

Security product readiness is a separate pre-delivery stage, implemented under
`guest_agent_server/security_product_readiness/`. It is intentionally not a
collector. Readiness probes run after `prepare-case` and before upload, do only
low-intrusion read-only environment checks, and write
`case_security_product_readiness.json`.

Current probes support `huorong`, `windows-defender`, `qihoo-360`, and
`tencent-pc-manager`. Huorong, Windows Defender, and Qihoo 360 are scoped to
log observability. Tencent PC Manager is scoped to
`quarantine_metadata_observability`: it records whether QQPCMgr quarantine
metadata paths and `TAVCacheFullEx.db` are visible, and records a baseline for
the current case sample MD5. These probes do not parse interception logs, do
not read sample content, and do not start, stop, repair, or modify the security
product. `protection_state` is therefore `unknown` even when readiness is
`ready`; readiness means the observation path has minimum prerequisites, not
that real-time protection is confirmed.

Readiness and collection share `product_id`, but not verdict semantics.
Unsupported, unknown, partial, missing, or not-ready readiness states must not
be interpreted as evidence of detection or no detection. Product-log evidence
remains the responsibility of collectors. The evaluator uses readiness only as
a conservative gate before emitting `no_detection_observed`: only
`state == ready` allows that optimistic verdict, and explicit product-log
detections are not downgraded by readiness.

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
  windows_defender/
    collector.py
    parser.py
    reader.py
    schema.py
  qihoo_360/
    collector.py
    parser.py
    schema.py
  tencent_pc_manager/
    collector.py
    quarantine.py
    schema.py
```

`collectors/base.py` defines the stable schema:

- `CollectionWindow`: the time window used to attribute product logs to the
  current case.
- `NormalizedSecurityEvent`: a product-independent event record for evidence.
- `CollectorArtifact`: product-specific artifact metadata. It declares a
  relative workspace path, category, whether the collector suggests inclusion in
  the default redacted evidence bundle, redaction owner/state, sensitivity, and
  reason.
- `CollectorResult`: the product collector result written into
  `case_collection.json`.
- `ProductLogCollector`: the base interface implemented by product collectors.

`collectors/registry.py` is the only place that maps `product_id` values to
collector implementations. Workspace collection code calls the registry and
must not import individual product collectors directly. The product onboarding
checklist, including evidence attribution downgrade rules for missing fields,
is documented in `docs/PRODUCT_ONBOARDING.md`.

Product collectors own only product-specific details: log locations, copy
strategy, raw field parsing, artifact sensitivity, and conservative evidence
matching. The rest of the system consumes normalized events and collector
results; it should not need to know raw product database columns.

The selected `product_id` is resolved once at the CLI/orchestration entrypoint.
Product-aware commands require explicit `--product`, and interactive
`single-run` prompts for the product before generating its temporary config,
with `huorong` shown as the default suggestion. `guest-prepare-case` writes the
resolved product into `case_state.json`; later readiness and collection calls
must match that case-bound product. If a caller passes a different product, the
server rejects the request instead of silently collecting logs for another
baseline.

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

The copied Huorong SQLite files are raw product logs. They are useful as
guest-side working inputs for parsing, but they are marked as
`raw_product_log`, `include_in_evidence=false`, `redaction_state=raw_blocked`,
and `sensitivity=high`. The default redacted evidence bundle records their
existence, size, and best-effort guest-reported source hash in `manifest.json`,
but does not include `log.db`, `log.db-shm`, or `log.db-wal` bytes.

Readiness snapshots follow the same raw-artifact boundary. The text metadata
file `case_security_product_readiness.json` may enter the redacted evidence
bundle after global text redaction and is categorized as
`security_product_readiness_metadata`. The copied readiness snapshot files under
`security-product-readiness/`, such as Huorong `log.db`, `log.db-wal`, and
`log.db-shm`, remain excluded and are not treated as default evidence.

## Qihoo 360 Onboarding

Qihoo 360 now follows the same product onboarding path as Huorong and Windows
Defender. It uses the stable `product_id` `qihoo-360`, has a low-risk readiness
probe, has a product collector registered through the existing registry, and is
selectable through the shared CLI / `single-run` product resolution flow. The
MVP has been smoke-validated in a cloud-isolated Windows Lighthouse run using
the administrator Desktop Worker account: Qihoo 360 produced a `Summary.dat`
quarantine record, the collector emitted normalized `av_quarantined` evidence,
and evaluation produced a conservative `detected_or_blocked / high` summary.

Its implementation shape is closer to Huorong than Windows Defender: copy the
product log snapshot into the current case workspace, parse the copied file
read-only, emit normalized evidence, and keep raw product logs out of the
default redacted evidence bundle. The collector keeps Qihoo-specific table /
record schema, timestamp fields, action semantics, and attribution rules inside
`collectors/qihoo_360/`; Huorong assumptions are not hard-coded for 360.
FILETIME-like Qihoo timestamps are normalized from UTC+8 local wall-clock time
to UTC while preserving an explicit parser warning, so time-window attribution
can align with the unified case timeline without hiding the assumption.

## Tencent PC Manager Onboarding

Tencent PC Manager uses the stable product id `tencent-pc-manager`. The first
collector MVP is intentionally metadata-only because the observed Tencent TAV
artifact is an encrypted quarantine container, not a plain log database. The
collector does not decrypt, parse, copy, or include the container bytes in the
default evidence bundle.

Readiness runs after `prepare-case` and records the current case sample MD5
from case metadata, then checks:

```text
C:\ProgramData\Tencent\QQPCMgr\Quarantine
C:\ProgramData\Tencent\QQPCMgr\TAVWfsDB\TAVCacheFullEx.db
```

The readiness scope is `quarantine_metadata_observability`. It also records
product-presence signals such as whether the QQPCMgr root, quarantine
directory, and TAV cache file are visible. Process and service signals are
currently informational placeholders and are not used as blocking gates.

Collection observes only metadata for:

- `Quarantine\<sample_md5>`: quarantine container, the only strong quarantine
  artifact;
- `Quarantine\<sample_md5>.ico`: sidecar icon, auxiliary only and never strong
  by itself;
- `TAVWfsDB\TAVCacheFullEx.db`: product activity signal compared with the
  readiness baseline.

The collector uses a small time tolerance around the case window to absorb VM
clock drift and asynchronous product writes. A newly created or modified
container for the current case MD5 can produce `intercepted`; TAV cache activity
without a matching container remains `unknown` with a normalized
`product_log_activity_observed` event. Raw TAV artifacts are declared with
`include_in_evidence=false` and `redaction_state=raw_blocked`.

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
  evidence, execution was observable enough for a cautious statement, and
  security product readiness was confirmed as `ready`.
- `execution_not_observed`: execution did not happen or was not observable, so a
  no-detection claim would be too strong.
- `inconclusive` / `unknown`: evidence is incomplete or contradictory.

The exporter then creates an evidence bundle using the v2 redacted text artifact
policy. It includes `manifest.json`, case metadata, `sample/sample.json`,
`case_security_product_readiness.json`, normalized product evidence, summaries,
events, and Worker state after applying global text redaction to JSON / JSONL /
Markdown / TXT entries. The exporter does not know where Huorong or any future
product stores logs on the live system; it only considers explicit metadata and
collector-produced artifacts already inside the case workspace and then makes
the final include/exclude safety decision.

The bundle excludes the uploaded sample body, uploaded sample bytes, everything
under `sample/` except `sample/sample.json`, recursive evidence zip files,
token-like files, cloud credentials, real cloud configs such as
`configs/real.toml`, environment dumps, symlinks, junctions, files outside the
explicit allowlisted roots, and raw/binary product logs such as SQLite DB,
WAL, SHM, executable, or DLL files. JSON / JSONL parse failures fall back to
plain text redaction; true decode/redaction failures fail closed and exclude the
entry rather than returning unredacted content.

The manifest records `trust_model = dirty_instance_untrusted`,
`source_trust = guest_reported`, `forensic_grade = false`,
`raw_binary_included = false`, redaction policy, redacted files, redaction
warnings, included paths, excluded path details, and archive SHA-256 hashes so
the archive is reviewable without including unsafe or secret material.
The `redaction_policy` object is structured and self-describing: it records
that redaction is enabled, text files are redacted, binary files are not
redacted, hash fields are preserved, and the active file count / size limits are
enforced by the exporter. Collector plugins own product-semantic artifact
metadata, while the exporter owns global fallback redaction and bundle safety
vetoes.

## Dirty Instance Trust Boundary

After a sample has been delivered or triggered in the test VM, the guest
workspace, static files, Guest Agent process, Desktop Worker process, Python
runtime, temporary directories, and local guest-side tools are treated as
untrusted observations. Redaction MVP evidence is therefore
`guest-reported redacted evidence`; it is useful for development, EICAR or
harmless sample validation, coursework delivery, and first-pass review, but it
is not forensic-grade evidence.

Default export must not move raw binary artifacts from the dirty instance back
to the local host. Password-protected archives can reduce accidental opening,
but they do not make a dirty guest a trusted packager and are not the primary
safety boundary.

If future work needs raw product logs, use an offline forensic workflow instead:
stop the test instance, create a cloud snapshot or cloned disk, attach it
read-only to a clean temporary forensic environment, run a trusted
collector/redactor there, export redacted text artifacts, and destroy the
temporary environment. Raw artifact retention must be an explicit high-risk
workflow, not the default evidence bundle.
