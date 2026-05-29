# Guest Agent Windows Deployment

This document describes the current MVP packaging path for a Windows Lighthouse
guest that does not have Python installed.

## Build Locally

Use the existing conda environment, but install optional dependencies with pip,
not `conda install`.

```powershell
conda activate cloud-av-agent-lab
.\scripts\build-agent-suite.ps1
```

The script:

1. Runs `python -m pip install -e ".[guest-agent,guest-agent-build,desktop-worker,desktop-worker-build]"`.
2. Removes old `scripts\pack\build` and `scripts\pack\dist\agent-suite` output.
3. Runs PyInstaller in `--onedir` mode from `scripts\agent-suite.spec`.
4. Produces `scripts\pack\dist\agent-suite\bin\guest-agent.exe` and `desktop-worker.exe` with a shared `_internal` directory.

`--onedir` is preferred for this phase because it is easier to inspect and debug
than a single-file executable.

## Source Checkout Update Pitfall

If the cloud instance or platform machine runs from a git checkout instead of a
freshly uploaded `scripts\pack\dist\agent-suite\bin` directory, update the
checkout and reinstall the current working tree:

```powershell
git pull
python -m pip install -e ".[guest-agent,desktop-worker]"
```

If you use `requirements.txt`, run it from the repository root so its local
editable entry points at the current checkout:

```powershell
python -m pip install -r requirements.txt
```

Do not keep an editable install pinned to a fixed commit, for example:

```text
-e git+https://github.com/.../Cloud-AV-Agent-Lab.git@<commit>#egg=cloud_av_agent_lab
```

That pattern can leave the running Guest Agent, Desktop Worker, or control-plane
CLI loading old code even after the repository has been pulled to a newer
revision. The symptom is usually confusing: local source files show the new
logic, but `run_state.json`, `case_summary.json`, or the evidence bundle still
look like they came from an older flow.

Before debugging behavior, verify the loaded module path from the same terminal
and environment that starts the agent:

```powershell
python -c "import cloud_av_agent_lab; print(cloud_av_agent_lab.__file__)"
python -c "import cloud_av_agent_lab.guest_agent_server.app as a; print(a.__file__)"
python -c "import cloud_av_agent_lab.desktop_worker.app as w; print(w.__file__)"
```

## Upload To Lighthouse

Suggested cloud-side directory:

```text
C:\CloudAvAgentLab\
  bin\
    guest-agent.exe
    desktop-worker.exe
    _internal\
  cases\
  StartAgent.ps1
  StartDesktopWorker.ps1
```

Manual deployment steps:

1. Build locally.
2. Run `.\scripts\setup-cloud-av-workspace.ps1` on the cloud VM.
3. Compress or copy `scripts\pack\dist\agent-suite\bin`.
4. Upload the archive to the Lighthouse Windows instance over RDP.
5. Extract or copy every file to `C:\CloudAvAgentLab\bin\`.
6. Review `C:\CloudAvAgentLab\StartAgent.ps1` and
   `C:\CloudAvAgentLab\StartDesktopWorker.ps1`; configure VM-local environment
   variables there. `StartAgent.ps1` should define
   `CLOUD_AV_GUEST_AGENT_TOKEN`, `CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN`,
   `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN`, and
   `CLOUD_AV_DESKTOP_WORKER_TOKEN`. `StartDesktopWorker.ps1` should define the
   same `CLOUD_AV_DESKTOP_WORKER_TOKEN` value.
7. Set machine-level token environment variables on the cloud instance if you
   do not configure them in the startup scripts:

```powershell
[Environment]::SetEnvironmentVariable(
  "CLOUD_AV_GUEST_AGENT_TOKEN",
  "replace-with-a-strong-random-token",
  "Machine"
)
[Environment]::SetEnvironmentVariable(
  "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN",
  "replace-with-a-second-strong-random-token",
  "Machine"
)
```

Open a new terminal after setting the machine-level variable so the process can
see it.

The generated `StartAgent.ps1` enables controlled execution actions by default
for single-run. Keep a separate execution token configured on the cloud guest;
use `-DisableExecutionActions` only for diagnostic runs that must not execute
uploaded samples. If you prefer machine-level variables, set a third token:

```powershell
[Environment]::SetEnvironmentVariable(
  "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN",
  "replace-with-a-third-strong-random-token",
  "Machine"
)
```

Do not reuse the bearer token or upload token as the execution token.

## Firewall And Bind Address

For local-only development inside the VM, `--host 127.0.0.1` is fine. For remote
control-plane access from your local workstation, the agent must listen on the
cloud network interface:

```powershell
C:\CloudAvAgentLab\StartAgent.ps1
```

Before remote access, allow the port in Windows Firewall, for example:

```powershell
New-NetFirewallRule `
  -DisplayName "Cloud AV Guest Agent 8080" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 8080 `
  -Action Allow
```

Also make sure the Lighthouse security group or firewall policy only exposes
the port to the expected control-plane source IP.

## Foreground Verification

Run in the foreground first:

```powershell
cd C:\CloudAvAgentLab
.\StartAgent.ps1
```

That command starts Control Agent with controlled execution actions enabled by
default. For a diagnostic no-execute run, use:

```powershell
cd C:\CloudAvAgentLab
.\StartAgent.ps1 -DisableExecutionActions
```

From the local control plane, configure:

```toml
[guest_agent]
enabled = true
base_url = "http://<lighthouse-public-ip>:8080"
token_env = "CLOUD_AV_GUEST_AGENT_TOKEN"
timeout_seconds = 10

[guest_agent.execution]
enabled = false
token_env = "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
timeout_seconds = 30
```

Then set the matching local token environment variables and run:

```powershell
$env:CLOUD_AV_GUEST_AGENT_TOKEN="replace-with-the-same-token"
$env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN="replace-with-the-same-upload-token"
python -m cloud_av_agent_lab guest-health --config configs/lab.local.toml --vm-id sg-win10
```

Do not put the token in TOML, logs, screenshots, or test fixtures.

## Desktop Worker Execution MVP

Desktop Worker is built into the same `agent-suite` onedir bundle as Guest
Agent and must run in the interactive desktop user session. It only binds to
loopback. Control Agent uses it as both a readiness gate and the real execution
layer for `execute_uploaded_sample` after signing a short-lived single-use
execution lease.

Build locally:

```powershell
conda activate cloud-av-agent-lab
.\scripts\build-agent-suite.ps1
```

On the cloud instance, set a separate worker token:

```powershell
[Environment]::SetEnvironmentVariable(
  "CLOUD_AV_DESKTOP_WORKER_TOKEN",
  "replace-with-a-worker-token",
  "Machine"
)
```

Start Desktop Worker from the interactive test administrator account session
(recommended account: `AvTester-Admin`):

```powershell
cd C:\CloudAvAgentLab
.\StartDesktopWorker.ps1
```

Then start Control Agent with Worker status proxy enabled:

```powershell
cd C:\CloudAvAgentLab
.\StartAgent.ps1
```

From the local control plane, enable `[guest_agent.desktop_worker]` and query:

```powershell
python -m cloud_av_agent_lab guest-worker-status `
  --config configs/lab.local.toml `
  --vm-id sg-win10
```

Desktop Worker must not be started with `--host 0.0.0.0`, and the worker token
must not enter config files, logs, reports, or evidence bundles.

## Controlled Execution Verification

The local control plane never executes samples. It can only ask the cloud-side
Guest Agent to act on the current case's registered upload. The normal sequence
for a harmless command executable is:

```powershell
$env:CLOUD_AV_GUEST_AGENT_TOKEN="replace-with-the-same-token"
$env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN="replace-with-the-same-upload-token"

python -m cloud_av_agent_lab guest-prepare-case `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id>

python -m cloud_av_agent_lab guest-upload-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --sample-id <sample-id> `
  --case-id <case-id> `
  --file C:\Temp\harmless-proof.exe

python -m cloud_av_agent_lab guest-case-report `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --case-id <case-id>

python -m cloud_av_agent_lab guest-execute-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --case-id <case-id> `
  --sample-id <sample-id>
```

The final command above is the default dry-run action. It validates metadata and
path ownership but does not start a process.

To request a real cloud-side execution of the registered harmless upload, all of
the following must be true:

- the cloud agent was started by the generated `StartAgent.ps1` without
  `-DisableExecutionActions`;
- the cloud instance has `CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN` set;
- local config has `[guest_agent.execution].enabled = true`;
- the local shell has the matching execution token environment variable.

Then run:

```powershell
$env:CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN="replace-with-the-same-execution-token"
python -m cloud_av_agent_lab guest-execute-sample `
  --config configs/lab.local.toml `
  --vm-id <vm-id> `
  --case-id <case-id> `
  --sample-id <sample-id> `
  --real-action
```

The Control Agent resolves the target metadata, signs a short-lived execution
lease, and forwards only `case_id`, `sample_id`, `run_id`, `expected_sha256`,
and `execution_lease` to Desktop Worker over localhost. Desktop Worker derives
the target file from `sample.json`, verifies it is under
`<workdir>\cases\<case_id>\sample\`, checks that it still exists and matches
the expected hash, and starts it with
`subprocess.Popen([sample_path], cwd=sample_dir, shell=False)`. Standard input,
output, and error are redirected to `DEVNULL`; Windows runs with
`CREATE_NO_WINDOW`; inherited handles are closed. Worker writes
`worker_execution_state.json`; Control Agent syncs the returned root PID,
`execution_started` time, expected hash metadata, run id, and path ownership
result into case state. After a real trigger, the local CLI polls
`/cases/{case_id}/execution-status`, which is forwarded to Worker for root and
child process metadata observation; it does not check proof files as the
long-term success signal.

## Later Service Work

Running as a Windows Service is intentionally not required for the MVP. After
foreground validation is stable, a later phase can add a service wrapper, log
rotation, and controlled upgrades.

## Security Boundary

The MVP server only exposes:

- `GET /health`
- `GET /system-info`
- `POST /prepare-case`
- `POST /cases/{case_id}/sample`
- `GET /cases/{case_id}/status`
- `GET /cases/{case_id}/report`
- `POST /cases/{case_id}/collection/{product_id}`
- `GET /cases/{case_id}/collection/status`
- `GET /cases/{case_id}/summary`
- `GET /cases/{case_id}/evidence-bundle`
- `GET /cases/{case_id}/execution-status`
- `POST /cases/{case_id}/actions`
- `GET /worker/status`

It does not download real malware, expose arbitrary command execution, accept
client-supplied guest paths, or start shells such as `cmd` or PowerShell.
Execution is a default-disabled controlled action. When explicitly enabled for
manual cloud validation, it can only start the current case's registered
uploaded EICAR or harmless file.
