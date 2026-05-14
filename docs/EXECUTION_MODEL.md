# Controlled Execution Model

This document defines the trigger-stage model for Cloud AV Agent Lab. Execution
is default-off. The current implementation supports dry-run metadata checks and
a tightly controlled `execute_uploaded_sample` action for cloud-side manual
validation with EICAR or harmless test binaries only.

## Scope

The project is intentionally split into three stages:

1. Delivery: prepare a case, upload an EICAR or harmless test file, and record
   whether it was saved, removed by security software, locked, or stable.
2. Trigger: a bounded action may trigger only the uploaded sample already
   registered in the current case, and only when execution is explicitly enabled.
3. Evaluation: a separate later phase may collect product logs and generate
   detection reports. Defender or other AV log reading is not part of this
   stage.

## Hard Prohibitions

Guest Agent must not expose arbitrary command execution. It must not accept or
run shell, cmd, PowerShell, exec, run-command, command line arguments, or
client-supplied cloud-side file paths.

The Guest Agent must not accept or run shell commands in any action payload.

The client must not send an arbitrary path to execute. Future trigger requests
must be based only on the current case metadata and the uploaded sample metadata
stored under:

```text
<workdir>\cases\<case_id>\sample\
```

The Guest Agent must not return sample contents, token values, sensitive
environment variables, or host secrets.

## Tokens And Default State

Execution capability is default-off:

```toml
[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

When controlled execution is enabled, it requires a separate execution token
from `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN` or the configured `token_env`. The
normal agent bearer token and upload token are not sufficient for
execution-stage actions.

## Required Validation

Future execution-stage requests must validate all of the following before doing
anything state-changing:

- `case_id` is path-safe and maps to an existing prepared case.
- `sample_id` matches the sample registered in that case.
- `expected_sha256` matches the uploaded sample metadata.
- The resolved target file is below `<workdir>\cases\<case_id>\sample\`.
- No client-provided path, command, shell, PowerShell, cmd, exec, or arguments
  are accepted.
- The action is recorded in `events.jsonl` and summarized in case metadata.
- Failures are explicit and do not fall back to arbitrary command execution.

## Current Action Skeleton

The current `POST /cases/{case_id}/actions` endpoint is intentionally narrow.
Allowed action names are:

- `generate_report`: generate a delivery-stage `case_report.json`.
- `observe_case`: refresh and return delivery-stage case status.
- `dry_run_execute_uploaded_sample`: validate metadata and path ownership only;
  it does not start a process.
- `execute_uploaded_sample`: returns `execution_disabled` in the default
  configuration. When execution is explicitly enabled and the execution token is
  valid, it starts only the current case's registered uploaded file.

`dry_run_execute_uploaded_sample` records `execution_dry_run_checked` and
returns whether metadata checks passed. It does not open, parse, scan, unpack,
or execute the sample.

`execute_uploaded_sample` performs the same metadata checks, verifies that the
registered file still exists with `os.path.exists`, confirms the file is below
`<workdir>\cases\<case_id>\sample\`, and starts it with `subprocess.Popen`
using `shell=False` and `cwd` set to that sample directory. Standard input,
standard output, and standard error are redirected to `subprocess.DEVNULL`; on
Windows the process is started with `CREATE_NO_WINDOW`, and file descriptors are
closed to avoid inheriting Guest Agent server handles. It records
`execution_started` with PID and start time in `events.jsonl`, and stores the
same PID metadata in `case_state.json`.

Manual trigger validation may use a harmless command exe that writes a proof
file such as `execution_proof.txt` inside the case sample directory. This does
not relax the harmful-sample boundary: no harmful samples are introduced, and
the local control plane must never execute uploaded files.

## Future State Machine

The future controlled trigger stage should use explicit states:

- `execution_disabled`
- `execution_requested`
- `sample_missing_before_execution`
- `execution_started`
- `execution_blocked_or_failed`
- `execution_exited`
- `execution_timeout`
- `execution_recorded`

After any future execution-stage action, orchestration should move to the
evaluation stage and then restore the Lighthouse snapshot. Snapshot rollback and
single-instance serial locking remain mandatory for reusable AV baselines.
