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
- `POST /cases/{case_id}/security-product-readiness/{product_id}`: run a
  low-intrusion, read-only pre-delivery product readiness check for the prepared
  case. The first supported probe is `huorong`.
- `GET /cases/{case_id}/security-product-readiness/status`: return the latest
  `case_security_product_readiness.json` result.
- `GET /cases/{case_id}/summary`: generate the conservative
  `case_summary.json` evaluator result.
- `GET /cases/{case_id}/evidence-bundle`: return a redacted guest-reported
  evidence zip that excludes sample bytes and secrets.
- `GET /cases/{case_id}/execution-status`: observe only the current case root
  PID and child process metadata.
- `POST /cases/{case_id}/actions`: controlled case action endpoint. It is not a
  command execution interface; real execution is default-off and, when enabled,
  is forwarded to Desktop Worker with a short-lived execution lease.

The server implementation lives in `src/cloud_av_agent_lab/guest_agent_server/`.
Windows packaging and Lighthouse deployment notes live in
`docs/GUEST_AGENT_DEPLOYMENT.md`.

Future phases may add guest-side sample staging from cloud object storage,
screenshot/artifact upload, richer product readiness probes, and structured
multi-run reporting. Those future operations must remain inside the
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

Security product readiness can be checked after `guest-prepare-case` and before
upload:

```powershell
python -m cloud_av_agent_lab guest-check-security-product-readiness --config configs/lab.local.toml --vm-id win10-huorong --case-id case-001__huorong --product huorong
```

This endpoint is deliberately separate from collection. It does not parse
product interception logs, does not read sample bytes, and does not start,
stop, repair, or modify a security product. The Huorong MVP only verifies
minimum log observability: the sysdiag directory and `log.db` exist, the live
`log.db` can be copied into
`<workdir>\cases\<case_id>\security-product-readiness\huorong\`, and the copied
database metadata can be read. Optional WAL/SHM copy problems are warnings. The
result is written to `case_security_product_readiness.json`, mirrored into
`case_state.json` / `case_report.json`, and recorded in `events.jsonl`. A
`ready` result means the Huorong log observation path appears usable; it does
not prove real-time protection is enabled, and `protection_state` remains
`unknown`. The standalone model and manual CLI flow are documented in
`docs/SECURITY_PRODUCT_READINESS.md`.

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
size, timestamps, and recent events. Product log collection remains a separate
collector stage and is exposed through the collection endpoints.

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
starts when the cloud-side Guest Agent was launched with execution enabled, the
request includes the correct execution token from
`CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN`, Desktop Worker is enabled and ready, and
Control Agent can sign a short-lived execution lease for the current case. The
Control Agent re-validates the current case metadata, then forwards only
`case_id`, `sample_id`, `run_id`, `expected_sha256`, and `execution_lease` to
Desktop Worker. Worker derives the sample path from shared case metadata,
verifies that the uploaded file still exists, and starts only that registered
`.exe` with:

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
programs that write relative files write inside the case workspace. The Worker
records its local `worker_execution_state.json`; Control Agent syncs the process
PID, start time, `run_id`, and observation state into `case_state.json`,
`events.jsonl`, and reports.

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

The full trigger model is documented in `docs/EXECUTION_MODEL.md`.

## Single-Run Integration

`single-run` wraps the Guest Agent CLI sequence into one controlled run. It
generates a temporary non-sensitive config under `runs/<run_id>/`, waits for
Lighthouse lifecycle readiness, then requires Guest Agent `/health` to succeed
twice. For real runs, the generated config also enables the Desktop Worker
gate, so Control Agent `/worker/status` must report an interactive desktop
Worker ready before delivery continues. Only after that does it apply the
settling cooldown, call `prepare-case`, run security product readiness in
warning-only mode, upload the explicit EICAR or harmless file, poll case status,
request the controlled action, collect Huorong logs, fetch the summary, and
download the redacted guest-reported evidence bundle. The command defaults to a
real run after one runtime risk confirmation; use `--dry-run` to keep cloud
lifecycle, Desktop Worker gate, and controlled action requests in dry-run mode.

The single-run readiness stage records
`run_state.stages.security_product_readiness`. `ready` is stored as `ok`;
`partial`, `not_ready`, `unknown`, `unsupported`, and API failures are stored as
warnings and do not block upload. This is still not strict mode, but the
evaluator later uses readiness as a conservative gate for
`no_detection_observed`: only `ready` allows that verdict.

Desktop Worker is now the real execution layer when enabled. Control Agent keeps
the stable HTTP control plane in Session 0, signs a short-TTL single-use
execution lease, and forwards `execute_uploaded_sample` to Worker over
localhost. Worker derives the sample path from case metadata, accepts only the
current case's registered `.exe`, verifies sha256, launches it with
`shell=False` and a minimal environment, then reports execution status back to
Control Agent. See `docs/DESKTOP_WORKER.md`.

The controlled action is conditional on the post-upload observation. If the
polling window ends with `stable`, single-run may request execution. If it sees
`removed_after_save`, `locked_or_busy`, or another non-stable state, it records
the action as skipped and continues to collection and evidence export. A remote
business failure during execution, such as the uploaded file disappearing
between the status poll and the action request, is also recorded as observation
data instead of being treated as an infrastructure failure.

The normal output files are:

- `lab.generated.toml`
- `run_state.json`
- `run.log`
- `case_summary.json`
- `case_summary.md`
- `case_evidence_<case_id>.zip`

The generated config does not contain Guest Agent tokens, upload tokens,
execution tokens, cloud secrets, or sample bytes. Tokens are still loaded from
environment variables by `GuestAgentClient`. Evidence export happens before the
cleanup snapshot restore; if the main flow fails after the case starts,
single-run tries a short-timeout evidence salvage and records the result without
blocking cleanup.

## Collection Stage

The collection stage reads only security product logs and case metadata inside
the cloud-isolated guest. It does not read sample contents and does not infer an
AV verdict from upload or process observations alone.

Current CLI:

```powershell
python -m cloud_av_agent_lab guest-worker-status --config configs/lab.local.toml --vm-id sg-win10
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
- `artifacts`: collector artifact policy metadata. Raw Huorong SQLite files are
  declared as `raw_product_log` and blocked from the default redacted evidence
  bundle, while normalized evidence is declared as a text artifact.

Verdict handling is conservative. `removed_after_save` and process disappearance
are useful observations, but they are not automatically treated as AV
interception without product-log evidence. Full design details are in
`docs/COLLECTION_MODEL.md`.

## Evaluation And Evidence Export

After collection, the project keeps three responsibilities separate:

- collector: product-specific log copy, parse, and normalization;
- evaluator: conservative verdict generation from delivery, execution, and
  collection evidence;
- exporter: redacted guest-reported archive creation for review and long-term
  storage.

`GET /cases/{case_id}/summary` generates and returns `case_summary.json`.
The summary is intentionally compact: product, sample, verdict, confidence,
short summary, key reasons, and a compact timeline. Repeated polling events are
not copied into the summary timeline; it keeps only important state changes,
collection boundaries, and product-log evidence. The full audit trail remains
available in `events.jsonl` and in the evidence bundle. The CLI mirrors this:
`guest-case-summary` prints concise text by default and prints the full JSON
only when `--json` is provided. The evaluator prefers
`detected_or_blocked` only when product-log evidence matches the current case.
If the file was removed after save and collection failed or was not run, the
verdict is now `inconclusive`; if collection completed but still found no
matching product evidence, the verdict is `suspiciously_removed`. If execution
did not happen, was skipped, or failed as a controlled action state such as
Worker busy, sample missing, sha256 mismatch, unsupported file type, or launch
failure, the verdict stays conservative rather than claiming a clean miss.
`no_detection_observed` is only allowed when delivery stayed stable, execution
was actually observed, collection completed successfully in a window covering
execution, no matching product evidence or collector errors were present, and
`security_product_readiness.state == ready`. If readiness is missing, partial,
not ready, unknown, or unsupported, the evaluator records the readiness reason
and keeps the verdict conservative. This gate only limits a no-detection claim;
it does not override `detected_or_blocked`.

`GET /cases/{case_id}/evidence-bundle` returns
`case_evidence_<case_id>.zip`. The bundle is a Redaction MVP archive, not a raw
forensic archive. It contains `manifest.json`, redacted case metadata,
`case_security_product_readiness.json` when present, `case_summary.json`,
`case_summary.md`, `events.jsonl`, `sample/sample.json`, Worker state, and
redacted normalized collector evidence. JSON / JSONL / Markdown / TXT entries
are redacted in the archive copy only; the original case workspace files are
not modified.

The bundle does not include uploaded sample bytes, token values, environment
variables, cloud credentials, recursive evidence zips, real cloud configuration
files, symlinks, junctions, unknown workspace roots, raw SQLite databases,
WAL/SHM files, executables, DLLs, or other unredacted binary product logs. Raw
Huorong `collection/huorong/log.db*` files may exist in the guest workspace for
parsing, but the default evidence bundle only records their guest-reported
metadata and exclusion reason in `manifest.json`. Readiness snapshot files under
`security-product-readiness/`, including Huorong `log.db*`, are also excluded;
only the redacted readiness metadata JSON can enter the bundle.

The manifest records the v2 collection policy, `trust_model =
dirty_instance_untrusted`, `source_trust = guest_reported`, `forensic_grade =
false`, `raw_binary_included = false`, redaction policy, redacted files,
redaction warnings, included paths, excluded path details, categories, and
SHA-256 hashes for every file included in the zip so the bundle can be archived
and later verified.

The `redaction_policy` object is intentionally self-describing: it records that
redaction is enabled, text files are redacted, binary files are not redacted,
hash fields are preserved, and the active file count / size limits came from
code constants. It also records the split of responsibility: collectors own
product-semantic redaction metadata, while the evidence exporter owns the
global fallback redaction and bundle safety veto.

Once a sample has been delivered or triggered, files and tools inside the test
instance are not treated as trusted. The Guest Agent and Desktop Worker provide
guest-reported observations for review; they are not a forensic trust root. If
future work needs raw product logs, it should use a separate offline workflow:
stop the instance, create a cloud snapshot or cloned disk, attach it read-only
to a clean forensic environment, run a trusted collector/redactor, and export
redacted text evidence from there.

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
