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

## Safety Boundary

Readiness probes must stay low-risk and read-only:

- no real malware samples;
- no sample upload, download, parsing, or execution;
- no shell, cmd, PowerShell, exec, or run-command behavior;
- no service start/stop/repair or product configuration changes;
- no token, cloud key, environment variable, or local real config output;
- no `configs/real.toml` access;
- no Tencent Cloud API calls.

## Huorong MVP

The first probe supports `huorong` and uses:

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

For Huorong MVP, `scope = "log_observability"` and
`protection_state = "unknown"`. This is deliberate: readiness does not confirm
real-time protection state.

## CLI

Run after preparing a case:

```powershell
python -m cloud_av_agent_lab guest-check-security-product-readiness `
  --config configs/lab.local.toml `
  --vm-id win10-huorong `
  --case-id eicar-001__huorong `
  --product huorong
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

## Current Integration Boundary

The first MVP is exposed through API, client, CLI, workspace state, and
`case_report.json`.

It is not yet wired into:

- `single-run`;
- evaluator gating;
- evidence bundle inclusion;
- strict blocking mode.

The next intended steps are to include the redacted
`case_security_product_readiness.json` in evidence bundles, then call readiness
from `single-run` in warning-only mode, and finally use it as conservative
gating for `no_detection_observed`.
