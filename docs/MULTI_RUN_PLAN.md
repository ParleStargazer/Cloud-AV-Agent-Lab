# Multi-Run Orchestration

`multi-run` is the batch orchestration layer above `single-run`. The first MVP
is complete: it plans a serial batch, validates the selected sample manifest,
runs each case through the existing `single-run` primitive, writes resumable
state and event logs, and generates aggregate reports.

## Current Scope

- Serial execution for one resolved Lighthouse `instance_id`.
- Future parallelism only across different instance ids.
- `single-run` remains the only case execution primitive.
- `multi-run` does not reimplement cloud lifecycle, Guest Agent calls, Desktop
  Worker execution, collection, evaluation, evidence export, or cleanup.
- Development hosts should use a pre-generated `--manifest`.
- Cloud platform hosts may index a controlled sample directory with
  `--platform-sample-dir`.
- Evidence bundles continue to exclude uploaded sample bytes, raw product logs,
  tokens, cloud credentials, and `configs/real.toml`.

## CLI

Interactive guided mode:

```powershell
python -m cloud_av_agent_lab multi-run
```

The prompt asks for product, Lighthouse instance id, baseline snapshot id,
region, Guest Agent URL, Desktop Worker URL, and the cloud platform sample
directory. The default sample directory is the project-root `runs\raw_sample`
even if the command is launched from another working directory. The default
batch root is the project-root `runs`.

Cloud platform sample indexing can be invoked with either form:

```powershell
python -m cloud_av_agent_lab multi-run --sample-dir runs\raw_sample --platform-sample-dir
python -m cloud_av_agent_lab multi-run --platform-sample-dir runs\raw_sample
```

`--sample-dir` without `--platform-sample-dir` is rejected so a development host
does not accidentally scan or touch a real sample directory. Real execution
requires an interactive confirmation unless `--dry-run`, `--plan-only`, or
`--yes` is supplied.

## Batch Artifacts

`multi-run` creates one batch directory:

```text
runs/
  batch_20260530-235629_tencent-pc-manager/
    batch_plan.json
    multi_run.generated.toml
    sample_manifest.jsonl
    sample_manifest.sha256
    preflight_report.json
    multi_run_state.json
    multi_run_events.jsonl
    aggregate_summary.json
    aggregate_summary.md
    sample_index/
      indexed/
    cases/
      0001_<sha16>/
        <single-run-id>/
          run_state.json
          case_summary.json
          case_summary.md
          case_evidence_<case-id>.zip
```

The aggregate layer reads only per-case metadata:

- `run_state.json`
- `case_summary.json`
- evidence bundle metadata/path

It does not read uploaded samples, raw product logs, `configs/real.toml`, or
cloud credentials.

## Sample Indexing

On a cloud platform host, the indexer scans the selected sample directory,
hashes regular files by raw bytes, groups duplicates by SHA-256, and writes an
immutable `sample_manifest.jsonl`. It also creates an indexed mirror:

```text
sample_index/indexed/0001_<sha16>.exe
```

The real runner currently requires non-dry-run `sample_ref` values to point to
this indexed mirror and verifies SHA-256, MD5, and size before invoking
`single-run`. Placeholder and generated files such as `.gitkeep`, `.gitignore`,
README files, nested `runs/`, `sample_index/`, and `indexed/` directories are
ignored during indexing.

This indexed mirror is a batch input artifact, not evidence. It must not be
placed into evidence bundles.

## Preflight

Before scheduling cases, `multi-run` writes `preflight_report.json`. The default
static preflight validates:

- batch directory writability;
- manifest digest consistency;
- selected index availability;
- runner callable;
- product profile availability;
- instance id, snapshot id, region, Guest Agent URL, and Desktop Worker URL
  shape;
- sibling running/stopping batch conflicts for the same instance;
- evidence output writability;
- generated config contains no sensitive-looking keys;
- batch plan SHA-256 presence.

Network reachability remains skipped in static preflight; real Guest Agent and
Desktop Worker health are still handled by `single-run`.

## State, Events, Resume, And Rerun

`multi_run_state.json` records batch metadata, selected indexes, per-case status,
verdict, confidence, cleanup status, summary/evidence status, paths, warnings,
and stop conditions. `multi_run_events.jsonl` records append-only events with a
monotonic `seq`.

Supported execution modes:

- normal run;
- resume;
- rerun failed case failures;
- force rerun.

Environment failures, cleanup restore failures, unsafe-to-continue states, and
manual-intervention states stop the batch and are not automatically rerun as
ordinary case failures.

## Aggregate Report

`aggregate_summary.json` and `aggregate_summary.md` summarize:

- batch id and product;
- final status;
- selected samples and evaluable cases;
- case failure and environment failure counts;
- verdict breakdown;
- readiness / cleanup / summary / evidence status breakdown;
- observed detection rate when results are real.

Dry-run or fake-run results must be marked as simulated and must not be confused
with real detection rates.

## Verified Smoke

The first real cloud batch smoke was analyzed from:

```text
runs/batch_20260530-235629_tencent-pc-manager
```

Result:

- `product_id = tencent-pc-manager`
- 94 selected samples
- final status `completed_with_warnings`
- 94/94 cases completed
- 94/94 readiness ok
- 94/94 cleanup restored
- 94/94 summary collected
- 94/94 evidence exported
- 0 environment failures
- 0 case failures
- verdicts: 68 `detected_or_blocked`, 26 `inconclusive`
- evidence bundles contained redacted metadata only and excluded indexed sample
  bytes, raw TAV artifacts, tokens, cloud secrets, and `configs/real.toml`.

## Next Optimization Work

The next stage is performance and storage optimization, not another feature
rewrite.

Planned work:

1. Add timing aggregation so batches report where time is spent.
2. Add an opt-in indexed sample burn-after-case mode.
3. Add an opt-in deferred cleanup strategy:
   - every case still starts with an initial snapshot restore;
   - middle cases may skip the end cleanup restore;
   - the next case's initial restore cleans the previous case state;
   - the last case and any batch failure path must still perform final cleanup;
   - environment failures and unsafe states must stop the batch.
4. Keep these optimizations off by default until a small real smoke confirms the
   safety and recovery behavior.

Detailed planning lives in
`reference-doc/current/multi-run/performance-optimization-plan.md`.
