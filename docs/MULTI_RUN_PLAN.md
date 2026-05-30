# Multi-Run Development Plan

`multi-run` is the next orchestration layer after the current `single-run`
MVP. It should schedule many single-case runs without duplicating the core
cloud lifecycle, Guest Agent, Desktop Worker, collection, evaluation, or
evidence export logic.

## Goals

- Run a matrix of samples, products, and VM profiles with minimal user input.
- Reuse `single-run` as the only case execution primitive.
- Keep one `instance_id` serialized through the existing lock model.
- Allow parallelism only across different Lighthouse instances.
- Produce one batch state file and a compact aggregate report.
- Keep every per-case `run_state.json`, summary, and evidence bundle under its
  own run directory.
- Preserve existing safety boundaries: no local sample execution, no arbitrary
  guest command, no `configs/real.toml` in artifacts, no raw product logs or
  uploaded samples in default evidence bundles.

## Proposed CLI Shape

```powershell
python -m cloud_av_agent_lab multi-run --plan runs\plans\batch.toml
```

Current MVP CLI also supports an interactive guided mode:

```powershell
python -m cloud_av_agent_lab multi-run
```

When required fields are omitted in an interactive terminal, the CLI prompts for
product, Lighthouse instance, baseline snapshot, region, Guest Agent URL,
Desktop Worker URL, and the cloud platform sample directory. The default sample
directory is `runs\raw_sample`, and the default selection is `--all`.

On the cloud platform host, sample directory indexing can be written either as:

```powershell
python -m cloud_av_agent_lab multi-run --sample-dir runs\raw_sample --platform-sample-dir
```

or with the shorthand:

```powershell
python -m cloud_av_agent_lab multi-run --platform-sample-dir runs\raw_sample
```

Development hosts should continue to use `--manifest` and must not scan a local
sample directory. Real execution requires an interactive confirmation unless
`--dry-run`, `--plan-only`, or explicit `--yes` is used.

The first version can also accept a generated CSV/JSON plan, but TOML should
stay close to the existing lab profile model:

```toml
[[targets]]
vm_id = "win10-huorong"
product = "huorong"

[[targets]]
vm_id = "win10-tencent-manager"
product = "tencent-pc-manager"

[[samples]]
name = "eicar"
path = "C:\\Temp\\eicar.txt"

[run]
dry_run = false
max_parallel_instances = 1
continue_on_case_failure = true
```

## Artifacts

`multi-run` should create a batch directory:

```text
runs/
  batch_20260530-210000/
    multi_run_state.json
    aggregate_summary.json
    aggregate_summary.md
    cases/
      20260530-210101_eicar__huorong/
      20260530-211305_eicar__tencent-pc-manager/
```

`multi_run_state.json` should record:

- batch id and start/end timestamps;
- selected samples, products, VM profiles, and resolved instance ids;
- per-case run id, case id, final status, verdict, confidence, cleanup status,
  evidence path, and errors;
- scheduler decisions such as skipped cases, retries, stale lock handling, and
  instance-level serialization.

## Scheduler Rules

- Group planned cases by resolved `instance_id`.
- Run cases for the same `instance_id` strictly serially.
- Permit future parallel execution only when instance ids differ.
- Never bypass `single-run` confirmation and lock semantics for real cloud
  writes.
- Prefer resumable execution: if a case run directory already contains a final
  `run_state.json`, record it as reused or skipped instead of rerunning unless
  an explicit rerun option is provided.

## Aggregate Report

The aggregate report should read only per-run summaries and states:

- `case_summary.json`
- `run_state.json`
- evidence bundle metadata/path

It should not read uploaded samples, raw product logs, `configs/real.toml`, or
cloud credentials. Initial columns:

- sample name / sample id;
- product id;
- vm id;
- verdict / confidence;
- delivery state;
- execution state;
- collection state;
- readiness state;
- cleanup status;
- evidence bundle path;
- warnings/errors.

## Implementation Phases

1. Define a batch plan schema and parser with validation tests.
2. Add `MultiRunState` read/write helpers.
3. Add a serial scheduler that calls the existing single-run API for each case.
4. Add aggregate JSON and Markdown report generation.
5. Add resume/skip behavior for completed run directories.
6. Add optional parallel scheduling across distinct `instance_id` groups.
7. Add manual validation flow for one sample across the four currently
   supported products.

The first implementation should stay conservative and single-threaded by
default. Parallelism is an optimization, not a prerequisite for the batch MVP.
