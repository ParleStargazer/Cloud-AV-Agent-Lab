# Guest Agent MVP

Guest Agent runs inside the cloud-isolated Windows Lighthouse instance. The local
host only calls it over HTTP through the existing control plane.

Current safety boundary: this development phase does not touch real malware
samples. The local control plane may only read a user-explicit EICAR or harmless
test file path for upload. It must not read, receive, upload, download, save,
open, extract, scan, parse, or execute real malware samples.

## Boundary

The Guest Agent is a guest-side helper, not a general remote shell. It is
intended to expose narrowly scoped workflow endpoints that the local orchestrator
can call after the cloud VM has been restored to a clean snapshot and started.

The MVP supports harmless connectivity, workspace preparation, and a safe upload
chain for EICAR or harmless test files:

- `GET /health`: check that the agent process is reachable.
- `GET /system-info`: return basic host and environment metadata.
- `POST /prepare-case`: create or reset a per-case workspace directory in the
  guest.
- `POST /cases/{case_id}/sample`: save an uploaded EICAR or harmless test file
  and metadata under the prepared case workspace, then return immediately.
- `GET /cases/{case_id}/status`: read case status, sample metadata, and recent
  events without reading sample contents; this endpoint performs a current
  `Path.exists` / `Path.stat` metadata check.
- `GET /cases/{case_id}/report`: generate and return `case_report.json`, a
  delivery-stage summary built only from metadata, case state, and events.
- `POST /cases/{case_id}/collection/{product_id}`: collect product logs for the
  prepared case. The first supported collector is `huorong`.
- `GET /cases/{case_id}/collection/status`: return the latest
  `case_collection.json` summary without reading sample bytes.
- `GET /cases/{case_id}/summary`: generate the conservative
  `case_summary.json` evaluator result.
- `GET /cases/{case_id}/evidence-bundle`: return a metadata-only evidence zip
  that excludes sample bytes and secrets.
- `GET /cases/{case_id}/execution-status`: observe only the current case root
  PID and child process metadata.
- `POST /cases/{case_id}/actions`: controlled action skeleton. It is not a
  command execution interface; real execution is disabled in this stage.

The server implementation lives in `src/cloud_av_agent_lab/guest_agent_server/`.
Windows packaging and Lighthouse deployment notes live in
`docs/GUEST_AGENT_DEPLOYMENT.md`.

Future phases may add guest-side sample staging from cloud object storage,
bounded test execution, AV log collection, screenshot/artifact upload, and
structured result reporting. Those future operations must remain inside the
cloud-isolated guest and must never download samples to the local host.

## Required Security Rules

- Local code must not download samples.
- Local code must not execute samples.
- Local code must not touch real malware samples in this phase.
- Local upload code may only read a user-explicit EICAR or harmless file path.
- Guest Agent upload code must only save bytes and metadata; it must not open,
  analyze, scan, unpack, or execute the uploaded file.
- Guest Agent must not expose an arbitrary command execution endpoint.
- Guest Agent must not allow unauthenticated access.
- Tokens must not be written into TOML config files or logs.
- Development proxy support must continue to flow through `NetworkClient`; Guest
  Agent business code must not read proxy settings directly.

The client sends `Authorization: Bearer <token>`, where the agent token is read
from the environment variable named by `[guest_agent].token_env`. Uploads also
require `X-Upload-Token: <upload_token>`, read from
`CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN`. Tokens are never returned in responses.

## Configuration

Example:

```toml
[guest_agent]
enabled = false
base_url = "http://127.0.0.1:8080"
token_env = "CLOUD_AV_GUEST_AGENT_TOKEN"
timeout_seconds = 10

[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

The default is disabled. When `enabled = true`, the token environment variable
must be present before any Guest Agent command runs.

## MVP CLI Flow

Current harmless commands:

```powershell
python -m cloud_av_agent_lab guest-health --config configs/lab.local.toml --vm-id sg-win10
python -m cloud_av_agent_lab guest-prepare-case --config configs/lab.local.toml --sample-id case-001 --vm-id sg-win10
```

`guest-prepare-case` sends case metadata, VM metadata, product metadata, and the
sample cloud object URI. It does not send a local file path and does not trigger
sample download or execution.

For EICAR or harmless upload flow:

```powershell
$env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN="replace-with-upload-token"
python -m cloud_av_agent_lab guest-upload-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager --file C:\Temp\eicar.txt --sha256 <optional-sha256>
```

`guest-upload-sample` reads only the explicit `--file` path and sends it as an
opaque byte stream. The server stores it under
`<workdir>\cases\<safe_case_id>\sample\` with `sample.json` metadata containing
`sample_id`, `sha256`, `size`, and `original_filename`. The upload endpoint does
not execute or inspect the file contents. It does not run a synchronous
post-upload heartbeat; after the file is written and metadata is saved, it
returns `200 OK` with `upload_state = "uploaded"`.

Upload success means the HTTP transport succeeded and the Guest Agent accepted
the request. It does not mean the uploaded file will still exist after the
security product inspects it. EICAR is commonly removed immediately by AV
software; that is an expected product signal, not a transport failure.

The server records the post-upload state in `case_state.json` and appends events
to `events.jsonl`:

- `transport_ok`: the local HTTP request reached the Guest Agent and received a
  success response.
- `saved_once`: the Guest Agent wrote the uploaded bytes to disk at least once.
- `metadata_saved`: `sample.json` and case state metadata were written.
- `post_write_exists`: the latest status query still saw the file after writing.
- `removed_after_save`: the file was saved once and later disappeared, likely
  because security software handled it.
- `locked_or_busy`: the file exists, but a status-query metadata check such as
  `stat` failed temporarily, likely because security software is processing it.
- `stable`: a status query saw the file with expected metadata.

Time-consuming observation is deliberately kept out of `POST /sample`. After a
successful upload, the CLI waits 10 seconds, then polls
`GET /cases/{case_id}/status` every 2 seconds until either
`removed_after_save` appears or the 30 second observation window ends. If a
sample is `stable` throughout the window, the CLI reports it as still alive.
Manual `guest-case-status` calls can be repeated later if Defender or another AV
product processes EICAR more slowly. Guest Agent logs the successful write and
each status refresh at INFO level. Logs do not include tokens or sample
contents.

Recent status can be queried without reading sample content:

```powershell
python -m cloud_av_agent_lab guest-case-status --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
```

`removed_after_save` and `locked_or_busy` are not treated as upload failures.
They are recorded as observations for the current case. `locked_or_busy` should
be followed by a later `guest-case-status` query.

Delivery-stage reports can be generated without reading sample bytes:

```powershell
python -m cloud_av_agent_lab guest-case-report --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
```

The report is stored as `case_report.json` under the case workspace and contains
only metadata fields such as `case_id`, `sample_id`, `vm_id`, `product_id`,
`upload_state`, saved/removed/locked/stable flags, original filename, hash,
size, timestamps, and recent events. It does not read Defender logs or any other
AV product logs; evaluation-stage evidence collection remains a later phase.

Execution observation can be queried separately after a real controlled trigger:

```powershell
python -m cloud_av_agent_lab guest-execution-status --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__tencent-pc-manager
```

`GET /cases/{case_id}/execution-status` is read-only. It observes only the
`root_pid` that was started by the current case action and any child process
metadata visible from that root. It does not accept arbitrary PIDs, paths,
commands, shell arguments, or client-supplied process names. The query is
low-intrusion: `psutil` process objects are created only for a short metadata
snapshot and are not cached or held across requests, so the Agent should not
interfere with Windows Defender or another security product terminating the
sample process.

## Controlled Action Skeleton

CLI entrypoint:

```powershell
python -m cloud_av_agent_lab guest-execute-sample --config configs/lab.local.toml --vm-id sg-win10 --sample-id case-001 --case-id case-001__tencent-pc-manager
```

By default this command sends `dry_run_execute_uploaded_sample`. It validates
the registered sample metadata, expected sha256, and case-owned sample path, then
records `execution_dry_run_checked`. It does not start a process.

Passing `--real-action` requests `execute_uploaded_sample`. Real execution only
starts when the cloud-side Guest Agent was launched with execution enabled and
the request includes the correct execution token from
`CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN`. The server then re-validates the current
case metadata, verifies that the uploaded file still exists with `os.path.exists`,
and starts only that registered file with:

```python
subprocess.Popen(
    [str(sample_path)],
    cwd=str(sample_dir),
    shell=False,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW,
    close_fds=True,
)
```

The working directory is the case-owned `sample` directory, so harmless test
programs that write relative files write inside the case workspace. The process
PID and start time are recorded in `case_state.json` and `events.jsonl` as
`execution_started`.

After `--real-action`, the CLI no longer checks proof files. It polls
`execution-status` every 2 seconds for up to 60 seconds and reports states such
as `running`, `exited_cleanly`, `exited_with_error`,
`terminated_or_disappeared`, or `timeout_still_running`. These are execution
phenomena only. A vanished process is not automatically labeled as an AV
interception; later evaluation must combine process observation with delivery
state and product log evidence.

`POST /cases/{case_id}/actions` currently accepts only a small whitelist:

- `generate_report`
- `observe_case`
- `dry_run_execute_uploaded_sample`
- `execute_uploaded_sample`

The endpoint rejects client-provided paths, shell/cmd/PowerShell fields,
commands, executable names, and arbitrary arguments. `execute_uploaded_sample`
returns `execution_disabled` unless execution is explicitly enabled on the cloud
Guest Agent. The dry-run action checks only metadata, path ownership under the
current case sample directory, and optional `expected_sha256`; it records
`execution_dry_run_checked` and does not start a process.

Current manual validation may use EICAR or a harmless command exe such as a
program that writes `execution_proof.txt`. That proof file is only an early
smoke-test aid; the durable observation model is now process metadata and exit
state. This still does not permit harmful samples. The local control plane must
not execute the uploaded file; only the cloud-isolated Guest Agent may trigger
the registered sample when execution is explicitly enabled for the manual test.

The full future trigger model is documented in `docs/EXECUTION_MODEL.md`.

## Collection Stage

The collection stage reads only security product logs and case metadata inside
the cloud-isolated guest. It does not read sample contents and does not infer an
AV verdict from upload or process observations alone.

Current CLI:

```powershell
python -m cloud_av_agent_lab guest-collect-logs --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__huorong --product huorong
python -m cloud_av_agent_lab guest-case-summary --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__huorong
python -m cloud_av_agent_lab guest-export-evidence --config configs/lab.local.toml --vm-id sg-win10 --case-id case-001__huorong --output .\artifacts\case_evidence_case-001__huorong.zip
```

For Huorong, the Guest Agent copies `log.db`, `log.db-shm`, and `log.db-wal`
from `C:\ProgramData\Huorong\sysdiag\` into
`<workdir>\cases\<case_id>\collection\huorong\`, then opens the copied SQLite
database in read-only mode. It discovers the latest `HrLogV3_*` table, parses
the JSON payload column by known name or by the fifth-column convention used by
the helper export script, and normalizes matching rows into product-log events.
The observed Huorong schema uses a database column named `detail`, whose JSON
payload also contains a nested `detail` object.

The result is written to `case_collection.json` and includes:

- `collection_state`: `collected`, `partial`, `failed`, or `not_collected`.
- `verdict`: `intercepted`, `not_intercepted`, or `unknown`.
- `events`: normalized product-log evidence.
- `timeline`: Guest Agent events and product-log events sorted by UTC time.
- `errors`: non-fatal collection or parse errors.

Verdict handling is conservative. `removed_after_save` and process disappearance
are useful observations, but they are not automatically treated as AV
interception without product-log evidence. Full design details are in
`docs/COLLECTION_MODEL.md`.

## Evaluation And Evidence Export

After collection, the project keeps three responsibilities separate:

- collector: product-specific log copy, parse, and normalization;
- evaluator: conservative verdict generation from delivery, execution, and
  collection evidence;
- exporter: metadata-only archive creation for review and long-term storage.

`GET /cases/{case_id}/summary` generates and returns `case_summary.json`.
The summary is intentionally compact: product, sample, verdict, confidence,
short summary, key reasons, and a compact timeline. Repeated polling events are
not copied into the summary timeline; it keeps only important state changes,
collection boundaries, and product-log evidence. The full audit trail remains
available in `events.jsonl` and in the evidence bundle. The CLI mirrors this:
`guest-case-summary` prints concise text by default and prints the full JSON
only when `--json` is provided. The evaluator prefers
`detected_or_blocked` only when product-log evidence matches the current case.
If the file was removed after save without product-log evidence, the verdict is
`suspiciously_removed`; if execution did not happen or could not be observed,
the verdict stays conservative rather than claiming a clean miss.

`GET /cases/{case_id}/evidence-bundle` returns
`case_evidence_<case_id>.zip`. The bundle contains `manifest.json`,
`case_state.json`, `case_report.json`, `case_collection.json`,
`case_summary.json`, `events.jsonl`, and normalized collector evidence. It does
not include the `sample/` directory, uploaded sample bytes, token values,
environment variables, cloud credentials, raw collector database copies, or real
cloud configuration files. The manifest records SHA-256 hashes for every file
included in the zip so the bundle can be archived and later verified.

## Status Observation Notes

2026-05-13 EICAR testing with Microsoft Defender showed that single status
queries can produce false `stable` results: Defender may alert first and remove
the file several seconds later. Server-side sleeps or synchronous heartbeat
loops also caused HTTP timeouts and risked changing the timing being observed.

For that reason, `POST /cases/{case_id}/sample` stays thin and returns as soon
as the file and metadata are saved. Observation is a client-side concern:
`guest-upload-sample` waits 10 seconds, polls `/status` every 2 seconds for up
to 30 seconds, and stops early if `removed_after_save` appears. A `stable`
result is only treated as final after that observation window ends.

## End-To-End Shape

The next safe end-to-end loop should stay plan-oriented until the Guest Agent
server exists:

1. Restore the baseline snapshot.
2. Start the instance.
3. Wait until the instance is stable `RUNNING`.
4. Call Guest Agent `/health`.
5. Call Guest Agent `/prepare-case`.
6. Upload an EICAR or harmless test file to `/cases/{case_id}/sample`.
7. Call Guest Agent `/cases/{case_id}/status`.
8. Generate or update the report with preparation/upload status.
9. Optionally trigger the controlled action only when execution is explicitly
   enabled for harmless validation.
10. Query `/cases/{case_id}/execution-status` to observe root PID and child
   process metadata.
11. Collect product logs with `/cases/{case_id}/collection/{product_id}`.
12. Restore the baseline snapshot.

Arbitrary `execute_sample` behavior remains out of scope. The only real trigger
implemented here is the default-off, token-protected, case-bound
`execute_uploaded_sample` action for EICAR or harmless validation files.

## Single-Instance Serial Lock

The current Lighthouse layout reuses one instance across multiple AV baseline
snapshots. Because restoring one snapshot changes the whole instance state, all
work for the same `instance_id` must run serially.

A future lightweight lock can live at `state/lighthouse-instance.lock`. Suggested
JSON fields:

```json
{
  "instance_id": "lhins-xxxxxxxx",
  "vm_id": "sg-win10",
  "product_id": "tencent-pc-manager",
  "baseline_snapshot": "snap-xxxxxxxx",
  "case_id": "case-001__tencent-pc-manager",
  "started_at": "2026-05-13T10:00:00Z"
}
```

The lock exists to prevent one case from preparing or running while another case
triggers `cloud-restore-snapshot` on the same Lighthouse instance.
