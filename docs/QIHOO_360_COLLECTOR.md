# Qihoo 360 Collector Model

This document tracks the 360 Security Guard / 360safe onboarding work. The
current implementation covers stage 1 parser support, stage 2 baseline / delta
helpers, a stage 3 readiness probe, a stage 4 collector MVP for
`360safe.Summary.dat` snapshots, stage 5 product binding for CLI,
configuration validation, and `single-run`, plus a cloud-isolated `single-run`
smoke validation.

## Product ID

Use the explicit product id:

```text
qihoo-360
```

The raw product name observed inside the log is kept as evidence metadata:

```text
360safe
```

The Python package name is `qihoo_360` because module names cannot start with a
digit.

## Parser Stage

The parser reads only a copied or fixture `360safe.Summary.dat` SQLite snapshot.
It does not discover live product logs, copy live files, register a collector,
or call Guest Agent endpoints.

Supported parser files:

```text
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/schema.py
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/parser.py
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/sqlite_reader.py
```

The SQLite reader verifies the `SQLite format 3` header, checks the expected
`CF`, `FI`, and `FQ` tables, reads `FI` file-index rows, reads `FQ` event rows,
and best-effort joins them by `ID`.

The TLV parser handles `FQ.FC` fields such as:

```text
@200 event_source
@201 raw_product
@203 threat_name
@204 threat_category
@205 raw_action_text
@206 event_time_raw
@208/@209 related_paths
@500 file_path
@501 file_size
@502 quarantine_path
@510 md5
@512 sha1
@513 sha256
@514 raw_category_hint
```

Unknown fields are preserved as short decoded and hex previews. The parser does
not store the full raw BLOB in evidence structures. Duplicate fields are
preserved as lists.

`@205` is deliberately stored as `raw_action_text`; the parser does not infer
that the file was restored, allowed, blocked, or quarantined from that text.

`@206` is stored as `event_time_raw`; best-effort `observed_at_utc` is filled
when the value looks parseable. Current field evidence indicates Qihoo 360
stores FILETIME-like values as local wall-clock time. The parser normalizes
those values from UTC+8 to UTC, marks time confidence as `medium`, and keeps the
warning `field_206_filetime_local_utc_plus_8_assumed` so later review can see
the timezone assumption. Time-window attribution remains supporting evidence;
case path, hash, baseline delta, and product action evidence are still used for
the conservative verdict.

## Baseline And Delta Stage

Stage 2 adds internal helpers only:

```text
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/baseline.py
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/attribution.py
```

`baseline.py` can read a `360safe.Summary.dat` snapshot and record:

```text
summary_dat_size
summary_dat_mtime_utc
max_fq_id
known_fq_ids
```

Delta filtering selects only `FQ.ID > baseline.max_fq_id`. If the current
maximum FQ ID is lower than the baseline maximum, the helper records
`summary_db_reset_or_rotated` and marks baseline delta as unusable instead of
making a confident attribution.

`attribution.py` provides conservative event attribution for later collector
use. A current case sample path match can produce `strong` attribution. A hash
match without a case path is limited to `medium`; the known EICAR SHA-256 is
explicitly downweighted with `eicar_hash_is_reused_across_cases` when the case
path is not matched.

## Readiness Probe Stage

Stage 3 adds:

```text
src/cloud_av_agent_lab/guest_agent_server/security_product_readiness/qihoo_360.py
```

The readiness probe is registered for product id `qihoo-360`. It only verifies
log observability:

- non-Windows platforms return `unsupported`;
- Windows with no discoverable `360safe.Summary.dat` returns `not_ready` with
  `summary_dat_not_found`; this can simply mean no quarantine/detection summary
  has been created yet, not that 360 is definitely absent or disabled;
- readable SQLite with queryable `FI` and `FQ` tables returns `ready`;
- an empty `FQ` table still returns `ready` with `summary_records_empty`;
- missing optional `360safe.Summary.union1` still returns `ready` with
  `union_metadata_missing`;
- SQLite/header/schema/query/access errors return `unknown`.

The probe does not determine whether 360 real-time protection is enabled.
`protection_state` remains `unknown`.

Readiness does not copy raw artifacts into the case workspace. Snapshot copying
and stability metadata remain collector-stage responsibilities.

## Collector MVP Stage

Stage 4 adds:

```text
src/cloud_av_agent_lab/guest_agent_server/collectors/qihoo_360/collector.py
```

The collector is registered for product id `qihoo-360`. It performs a
best-effort snapshot copy into:

```text
<case_workspace>/collection/qihoo-360/raw/360safe.Summary.dat
<case_workspace>/collection/qihoo-360/raw/360safe.Summary.union1  # optional
```

Before and after copying `Summary.dat`, the collector records source size and
mtime. If those values change, it records `snapshot_may_be_changing` as a
warning and continues with the copied snapshot rather than failing.

Collection semantics:

- missing `Summary.dat` returns `collection_state = not_collected` and
  `reason = product_log_not_found`;
- unreadable/corrupt/schema-invalid Summary snapshots return
  `collection_state = failed`;
- readable Summary snapshots with no current-case evidence return
  `collection_state = collected`, `verdict = unknown`, and
  `reason = no_relevant_qihoo360_events_for_case`;
- strong/medium attribution plus a quarantine path under `C:\$360Section`
  returns `verdict = intercepted`;
- strong/medium attribution plus `threat_name` without confirmed quarantine
  returns `verdict = detected`;
- weak or unattributed evidence remains `verdict = unknown`.

Qihoo 360 may show a user prompt during batch-script execution and only write
`Summary.dat` after the default action, such as quarantine, is selected or
times out. For `single-run`, collection is therefore delayed until after the
controlled execution observer has seen the root process exit, then waits 45
seconds by default before calling the collector. This delay belongs to
orchestration timing; the collector itself still only performs a point-in-time
read-only snapshot and parsing pass.

`@205` remains `raw_action_text`; the collector does not interpret it as
restored, allowed, blocked, or quarantined.

Raw `Summary.dat`, optional `union1`, WAL/SHM, quarantine files, and uploaded
sample bytes are not included in the default redacted evidence bundle. The
collector declares raw snapshots as artifacts with `include_in_evidence=false`
and `redaction_state=raw_blocked`.

## Product Binding Stage

Stage 5 wires the existing product model without adding new collector behavior.
`qihoo-360` is now selectable through the same product resolution path used by
Huorong and Windows Defender:

- `guest-prepare-case --product qihoo-360` resolves the configured product and
  writes `case_state.product_id = qihoo-360` through the normal prepare-case
  payload;
- `guest-check-security-product-readiness --product qihoo-360` and
  `guest-security-product-readiness-status --product qihoo-360` route through
  the readiness registry;
- `guest-collect-logs --product qihoo-360` routes through the collector
  registry;
- `single-run --product qihoo-360` generates a non-sensitive temporary config
  with the Qihoo 360 product profile and carries the same product id through
  prepare, readiness, upload, collection, summary, and evidence export.

The existing local product guards still apply: unknown products, disabled
products, and explicit products that conflict with the selected VM profile are
rejected before collection or readiness calls are made. The generated
`lab.generated.toml` contains product metadata only; tokens and cloud secrets
remain environment-variable only.

## Safety Boundary

The current stage 1/2/3/4/5 implementation:

- reads only SQLite snapshot metadata;
- does not read uploaded sample bytes;
- does not execute samples;
- does not modify 360 configuration;
- does not add allowlists or exclusions;
- does not use PowerShell, `cmd`, `wevtutil`, external SQLite CLIs, or shell
  commands;
- registers only the read-only metadata `qihoo-360` collector;
- registers only the read-only `qihoo-360` readiness probe;
- adds product binding for existing Guest Agent CLI endpoints without adding
  new endpoint behavior;
- does not touch `configs/real.toml`;
- does not trigger Tencent Cloud APIs.

## Smoke Validation Status

2026-05-29: Qihoo 360 MVP is considered integrated and smoke-validated in a
cloud-isolated Lighthouse Windows environment. The final validation used the
administrator Desktop Worker account and confirmed:

- `product_id = qihoo-360` flows through `single-run`;
- Desktop Worker runs under the dedicated administrator account
  `AvTester-Admin`;
- controlled execution can start the current case sample without exposing
  arbitrary path, command, argument, shell, or PowerShell input;
- Qihoo 360 writes a `Summary.dat` quarantine summary after execution;
- normalized evidence includes `av_quarantined` for
  `木马:Win32/TrojanDownloader.Generic.HoMAUHAA`;
- the unified timeline shows execution observation, product-log quarantine,
  collection start, and collection finish in chronological order after UTC+8
  timestamp normalization;
- attribution is strong through the current case sample path and threat name;
- summary verdict is `detected_or_blocked` with high confidence;
- cleanup restore completes;
- raw SQLite snapshots, WAL/SHM files, q3q quarantine files, uploaded sample
  bytes, tokens, cloud secrets, and `configs/real.toml` remain excluded from
  the default redacted evidence bundle.

Future Qihoo 360 work should be treated as hardening rather than MVP
enablement: broader 360 version coverage, product-specific collection delay
tuning, more sample-type smoke tests, and additional evidence fields if future
logs require them. Raw SQLite snapshots, WAL/SHM files, quarantine files, and
uploaded sample bytes must remain excluded from the default redacted evidence
bundle.
