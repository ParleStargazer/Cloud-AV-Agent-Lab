# Qihoo 360 Collector Model

This document tracks the 360 Security Guard / 360safe onboarding work. The
current implementation is stage 1 only: a parser MVP for `360safe.Summary.dat`
snapshots.

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

Supported stage 1 files:

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

## Safety Boundary

The stage 1 parser:

- reads only SQLite snapshot metadata;
- does not read uploaded sample bytes;
- does not execute samples;
- does not modify 360 configuration;
- does not add allowlists or exclusions;
- does not use PowerShell, `cmd`, `wevtutil`, external SQLite CLIs, or shell
  commands;
- does not register a live collector or readiness probe;
- does not touch `configs/real.toml`;
- does not trigger Tencent Cloud APIs.

## Next Stages

Next stages should add baseline / delta support, then a read-only readiness
probe, then a collector that copies `360safe.Summary.dat` into the case
workspace before parsing it. Raw SQLite snapshots, WAL/SHM files, quarantine
files, and uploaded sample bytes must remain excluded from the default redacted
evidence bundle.
