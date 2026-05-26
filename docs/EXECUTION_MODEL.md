# Controlled Execution Model

This document defines the trigger-stage model for Cloud AV Agent Lab. Execution
is default-off. The current implementation supports dry-run metadata checks and
a tightly controlled `execute_uploaded_sample` action for cloud-side manual
validation with EICAR or harmless test binaries only. A Desktop Worker readiness
and execution MVP has been added so real process launch is routed from Control
Agent to Desktop Worker instead of being started from Windows Session 0.

## Scope

The project is intentionally split into four stages:

1. Delivery: prepare a case, upload an EICAR or harmless test file, and record
   whether it was saved, removed by security software, locked, or stable.
2. Trigger: a bounded action may trigger only the uploaded sample already
   registered in the current case, and only when execution is explicitly enabled.
3. Execution observation: read-only observation of the root PID started for the
   current case and its child process metadata. This records process state,
   exit code, children, and timestamps, but it does not read sample contents or
   security product logs.
4. Evaluation: collectors normalize product logs and the evaluator/exporter
   generate conservative summaries and evidence bundles. Defender or vendor-log
   reading stays out of delivery and trigger endpoints.

## Hard Prohibitions

Guest Agent must not expose arbitrary command execution. It must not accept or
run shell, cmd, PowerShell, exec, run-command, command line arguments, or
client-supplied cloud-side file paths.

The Guest Agent must not accept or run shell commands in any action payload.

The client must not send an arbitrary path to execute. Trigger requests are
based only on the current case metadata and the uploaded sample metadata
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

Desktop Worker readiness uses its own token:

```toml
[guest_agent.desktop_worker]
enabled = false
base_url = "http://127.0.0.1:8001"
token_env = "CLOUD_AV_DESKTOP_WORKER_TOKEN"
required_for_execution = true
require_interactive_session = true
```

This token only protects the local Worker status channel. It is not sufficient
for real execution authorization. Real Worker execution also requires a
short-TTL, single-use execution lease bound to `case_id`, `sample_id`, `run_id`,
and `expected_sha256`. Control Agent signs the lease immediately before
forwarding the action; Worker verifies the HMAC payload, expiry, request
binding, nonce, and busy state before launching anything.

## Required Validation

Execution-stage requests validate all of the following before doing
anything state-changing:

- `case_id` is path-safe and maps to an existing prepared case.
- `sample_id` matches the sample registered in that case.
- `expected_sha256` matches the uploaded sample metadata.
- The resolved target file is below `<workdir>\cases\<case_id>\sample\`.
- No client-provided path, command, shell, PowerShell, cmd, exec, or arguments
  are accepted.
- The action is recorded in `events.jsonl` and summarized in case metadata.
- Failures are explicit and do not fall back to arbitrary command execution.
- The Desktop Worker receives only `case_id`, `sample_id`, `run_id`,
  `expected_sha256`, `handler_id`, and `execution_lease`.
- Worker derives the execution handler from the registered `sample.json`
  filename and rejects mismatches.
- Current handlers are `pe_executable` for `.exe`, `batch_script` for
  `.bat`/`.cmd`, disabled `powershell_script` for `.ps1`, and
  `unsupported` for other suffixes.
- Worker uses a minimal allowlisted environment and strips project tokens,
  cloud secrets, proxy variables, and real config paths before launching the
  child process.

## Current Actions

The current `POST /cases/{case_id}/actions` endpoint is intentionally narrow.
Allowed action names are:

- `generate_report`: generate a delivery-stage `case_report.json`.
- `observe_case`: refresh and return delivery-stage case status.
- `dry_run_execute_uploaded_sample`: validate metadata and path ownership only;
  it does not start a process.
- `execute_uploaded_sample`: returns `execution_disabled` in the default
  configuration. When execution is explicitly enabled and the execution token is
  valid, Control Agent signs a short-lived execution lease and forwards the
  action to Desktop Worker.

`dry_run_execute_uploaded_sample` records `execution_dry_run_checked` and
returns whether metadata checks passed. It does not open, parse, scan, unpack,
or execute the sample.

`execute_uploaded_sample` still performs Control Agent metadata checks and
records `execution_requested`, but the actual `subprocess.Popen` happens inside
Desktop Worker. Worker derives the registered sample path from shared case
metadata, verifies the file still exists, confirms sha256, resolves the handler,
ensures the file is below `<workdir>\cases\<case_id>\sample\`, and starts it
with a fixed handler template. `.exe` uses `subprocess.Popen([sample_path],
shell=False, cwd=sample_dir)`. `.bat`/`.cmd` uses the fixed interpreter
`C:\Windows\System32\cmd.exe` with fixed arguments `/d /c call <sample_path>`;
the client still cannot supply a command, arguments, interpreter, shell flag, or
path. Standard input, standard output, and standard error are redirected to
`subprocess.DEVNULL`; on Windows the process is started with `CREATE_NO_WINDOW`,
and file descriptors are closed. Worker writes
`worker-state/worker_execution_state.json`; Control Agent syncs the returned
status into `case_state.json`, `events.jsonl`, and reports.

After a real trigger, `guest-execute-sample --real-action` polls
`GET /cases/{case_id}/execution-status` every 2 seconds by default for up to 60
seconds. The Control Agent endpoint forwards observation to Desktop Worker when
Worker integration is enabled. Worker observes only the recorded `root_pid` for
the current case and its descendants. It does not accept paths, commands, shell
arguments, or arbitrary PIDs from the client.

Process observation is deliberately low-intrusion. The Worker takes short
read-only metadata snapshots with `psutil` when available, records only fields
such as PID, parent PID, name, status, and creation time, and does not keep
`psutil.Process` objects beyond a single request. Once the root process reaches
a terminal state, the in-memory `Popen` handle is removed from the registry so
the Worker does not hold process handles longer than needed. Observation must
not block Windows Defender or another security product from terminating a
process.

Observation states are facts, not verdicts:

- `running`: root or child process is still observable.
- `exited_cleanly`: root exit code is 0 and no live child process is observed.
- `exited_with_error`: root exit code is non-zero.
- `launch_failed`: `Popen` failed before a root process was created.
- `terminated_or_disappeared`: a process was started but is no longer
  observable and no exit code is available.
- `timeout_still_running`: the CLI observation window ended while the process
  tree was still running.
- `unknown`: the Agent does not have enough information.

The disappearance of a process is not treated as proof of AV blocking. That
judgment belongs to the later evaluation stage, where delivery state, execution
state, product logs, and snapshot rollback evidence can be combined.

Manual trigger validation may use a harmless command exe that writes a proof
file such as `execution_proof.txt` inside the case sample directory. That proof
file is only an early smoke-test aid, not the long-term evaluation model. The
durable model is process-tree observation plus later evaluation evidence. This
does not relax the harmful-sample boundary: no harmful samples are introduced,
and the local control plane must never execute uploaded files.

## Execution State Names

The controlled trigger stage uses explicit state names:

- `execution_disabled`
- `execution_requested`
- `sample_missing_before_execution`
- `execution_started`
- `execution_launch_failed`
- `execution_observed`
- `execution_child_observed`
- `execution_exited`
- `execution_timeout_still_running`
- `execution_recorded`

After any execution-stage action, orchestration should move to the
evaluation stage and then restore the Lighthouse snapshot. Snapshot rollback and
single-instance serial locking remain mandatory for reusable AV baselines.
