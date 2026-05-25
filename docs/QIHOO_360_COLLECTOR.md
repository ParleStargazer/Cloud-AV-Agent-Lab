# Qihoo 360 Collector Model

This document tracks the 360 Security Guard / 360safe onboarding work. The
current implementation covers stage 1 parser support and stage 2 baseline /
delta helpers for `360safe.Summary.dat` snapshots.

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
when the value looks parseable. FILETIME-like values are marked with low time
confidence because the reference material shows an offset ambiguity.

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

## Safety Boundary

The current stage 1/2 implementation:

- reads only SQLite snapshot metadata;
- does not read uploaded sample bytes;
- does not execute samples;
- does not modify 360 configuration;
- does not add allowlists or exclusions;
- does not use PowerShell, `cmd`, `wevtutil`, external SQLite CLIs, or shell
  commands;
- does not register a live collector or readiness probe;
- does not call Guest Agent endpoints;
- does not touch `configs/real.toml`;
- does not trigger Tencent Cloud APIs.

## Next Stages

Next stages should add a read-only readiness probe, then a collector that copies
`360safe.Summary.dat` into the case
workspace before parsing it. Raw SQLite snapshots, WAL/SHM files, quarantine
files, and uploaded sample bytes must remain excluded from the default redacted
evidence bundle.
