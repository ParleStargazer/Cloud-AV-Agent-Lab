# Security Product Readiness

This document describes the first MVP for checking whether a security product is
minimally observable before a case enters the upload and collection stages.

## Scope

Security product readiness is a case-scoped, pre-delivery observation stage. It
runs after `guest-prepare-case` and before `guest-upload-sample`.

It is intentionally separate from collectors:

- `security_product_readiness`: checks whether the product environment appears
  observable enough to continue testing.
- `collectors`: read and normalize product logs after delivery and optional
  controlled execution.

Readiness does not produce product-log evidence and does not own verdict
semantics. A `ready` result is only an environment observation, not proof that a
product will detect or block a sample.

New products should follow the onboarding checklist in
`docs/PRODUCT_ONBOARDING.md`: implement a low-risk readiness probe, register it
in `security_product_readiness/registry.py`, and keep product-specific logic out
of orchestration and evaluation.

## Safety Boundary

Readiness probes must stay low-risk and read-only:

- no real malware samples;
- no sample upload, download, parsing, or execution;
- no shell, cmd, PowerShell, exec, or run-command behavior;
- no service start/stop/repair or product configuration changes;
- no token, cloud key, environment variable, or local real config output;
- no `configs/real.toml` access;
- no Tencent Cloud API calls.

## Supported Products

Readiness probes are resolved by explicit `product_id`. Current supported IDs
are:

- `huorong`
- `windows-defender`

`guest-prepare-case` stores the selected product in `case_state.json`; later
readiness requests should use the same product. A request for a different
product is rejected rather than silently checking a different security product
baseline.

## Huorong Probe

The Huorong probe uses:

```text
C:\ProgramData\Huorong\sysdiag
  log.db
  log.db-shm
  log.db-wal
```

The probe checks that the sysdiag directory and `log.db` exist, copies live
`log.db` into the current case workspace, and reads only copied-file metadata.
It does not open the live SQLite database and does not parse Huorong
interception logs.

Snapshot output:

```text
<workdir>\cases\<case_id>\security-product-readiness\huorong\log.db
```

The snapshot directory is a guest-side working area only. Files under
`security-product-readiness\`, including `log.db`, `log.db-wal`, and
`log.db-shm`, are raw product log snapshots and are not included in the default
redacted evidence bundle.

State output:

```text
<workdir>\cases\<case_id>\case_security_product_readiness.json
```

The result is also summarized in:

- `case_state.json`
- `case_report.json`
- `events.jsonl`

## State Semantics

- `ready`: core log observability checks passed.
- `partial`: core checks passed, but optional WAL/SHM snapshot checks had
  warnings.
- `not_ready`: the log directory or `log.db` is missing.
- `unknown`: core copy/stat checks failed or there was not enough information.
- `unsupported`: no readiness probe exists for the requested product.

For both current probes, `scope = "log_observability"` and `protection_state =
"unknown"`. This is deliberate: readiness does not confirm real-time protection
state.

## Windows Defender Probe

The Windows Defender probe checks observability of:

```text
Microsoft-Windows-Windows Defender/Operational
```

It uses the Windows Event Log reader abstraction. Unit tests use injected fake
readers and do not read the local machine's real Event Log. The real pywin32
reader is only attempted inside the Windows Guest Agent when the probe runs on a
Windows host with pywin32 available. No PowerShell, `wevtutil`, `cmd`, shell
runner, or service/configuration changes are used.

## CLI

Run after preparing a case:

```powershell
python -m cloud_av_agent_lab guest-check-security-product-readiness `
  --config configs/lab.local.toml `
  --vm-id win10-huorong `
  --case-id eicar-001__huorong `
  --product huorong
```

For Windows Defender, use the matching VM profile and product ID:

```powershell
python -m cloud_av_agent_lab guest-check-security-product-readiness `
  --config configs/lab.local.toml `
  --vm-id win10-windows-defender `
  --case-id eicar-001__windows-defender `
  --product windows-defender
```

Load the last result without rerunning the probe:

```powershell
python -m cloud_av_agent_lab guest-security-product-readiness-status `
  --config configs/lab.local.toml `
  --vm-id win10-huorong `
  --case-id eicar-001__huorong
```

Expected concise output:

```text
Security product readiness:
  case_id: eicar-001__huorong
  product_id: huorong
  state: ready
  confidence: medium
  scope: log_observability
  protection_state: unknown
```

## Single-Run Integration

The readiness check is now part of `single-run` in warning-only mode. The
orchestrator calls it after `prepare-case` succeeds and before
`upload-sample`. This ordering is required because readiness is case-scoped and
writes into the prepared case workspace.

`single-run` records the result in `run_state.stages.security_product_readiness`
and logs a concise message:

- `ready`: status `ok`, continue.
- `partial`, `not_ready`, `unknown`, `unsupported`: status `warning`, continue.
- API or network failure: status `warning`, continue.

This stage must not block delivery. It does not enable strict mode or a
`--require-security-product-readiness` flag.

## Evaluator Gating

Readiness is used only as a conservative gate before the evaluator emits the
optimistic verdict `no_detection_observed`. The centralized helper
`allows_no_detection_observed()` currently allows that verdict only when:

```text
security_product_readiness.state == "ready"
```

Missing readiness, `partial`, `not_ready`, `unknown`, and `unsupported` all keep
the final verdict conservative, usually `inconclusive`, if the evaluator would
otherwise have returned `no_detection_observed`.

Readiness never creates a detection verdict and never overrides stronger
evidence. Product-log evidence that supports `detected_or_blocked`, or delivery
states such as `removed_after_save` with their own conservative verdicts, remain
prioritized.

## Evidence Bundle Boundary

The redacted evidence bundle includes only the text metadata file
`case_security_product_readiness.json` and marks it as
`security_product_readiness_metadata` in `manifest.json`; the copied readiness
snapshot directory remains excluded.

It is not yet wired into strict blocking mode.
