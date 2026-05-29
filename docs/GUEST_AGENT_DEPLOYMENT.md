# Guest Agent Windows Deployment

This document describes the current MVP packaging path for a Windows Lighthouse
guest that does not have Python installed.

## Build Locally

Use the existing conda environment, but install optional dependencies with pip,
not `conda install`.

```powershell
conda activate cloud-av-agent-lab
.\scripts\build-guest-agent.ps1
```

The script:

1. Runs `python -m pip install -e ".[guest-agent,guest-agent-build]"`.
2. Removes old `build\pyinstaller` and `dist\guest-agent` output.
3. Runs PyInstaller in `--onedir` mode.
4. Produces `dist\guest-agent\guest-agent.exe`.

`--onedir` is preferred for this phase because it is easier to inspect and debug
than a single-file executable.

## Source Checkout Update Pitfall

If the cloud instance or platform machine runs from a git checkout instead of a
freshly uploaded `dist\guest-agent` directory, update the checkout and reinstall
the current working tree:

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
C:\CloudAvAgentLab\guest-agent\
```

Manual deployment steps:

1. Build locally.
2. Compress `dist\guest-agent`.
3. Upload the archive to the Lighthouse Windows instance over RDP.
4. Extract it to `C:\CloudAvAgentLab\guest-agent\`.
5. Set a machine-level token environment variable on the cloud instance:

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

Controlled execution is optional and must stay disabled unless you are manually
validating a harmless uploaded executable in the cloud guest. To enable that
path, set a third machine-level token:

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
.\guest-agent.exe --host 0.0.0.0 --port 8080 --workdir C:\CloudAvAgentLab
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
cd C:\CloudAvAgentLab\guest-agent
.\guest-agent.exe --host 0.0.0.0 --port 8080 --workdir C:\CloudAvAgentLab
```

That command keeps controlled execution disabled. For a cloud-side manual
validation with an EICAR or harmless executable only, start the agent with the
explicit execution switch:

```powershell
cd C:\CloudAvAgentLab\guest-agent
.\guest-agent.exe `
  --host 0.0.0.0 `
  --port 8080 `
  --workdir C:\CloudAvAgentLab `
  --enable-execution-actions `
  --execution-token-env CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN `
  --execution-timeout-seconds 30
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

Desktop Worker is packaged separately and must run in the interactive desktop
user session. It only binds to loopback. Control Agent uses it as both a
readiness gate and the real execution layer for `execute_uploaded_sample` after
signing a short-lived single-use execution lease.

Build locally:

```powershell
conda activate cloud-av-agent-lab
.\scripts\build-desktop-worker.ps1
```

On the cloud instance, set a separate worker token:

```powershell
[Environment]::SetEnvironmentVariable(
  "CLOUD_AV_DESKTOP_WORKER_TOKEN",
  "replace-with-a-worker-token",
  "Machine"
)
```

Start Desktop Worker from the interactive administrator account session:

```powershell
cd C:\CloudAvAgentLab\desktop-worker
.\desktop-worker.exe --host 127.0.0.1 --port 8001 --workdir C:\CloudAvAgentLab
```

Then start Control Agent with Worker status proxy enabled:

```powershell
cd C:\CloudAvAgentLab\guest-agent
.\guest-agent.exe `
  --host 0.0.0.0 `
  --port 8080 `
  --workdir C:\CloudAvAgentLab `
  --enable-desktop-worker `
  --desktop-worker-url http://127.0.0.1:8001 `
  --desktop-worker-expected-user Administrator
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

- the cloud agent was started with `--enable-execution-actions`;
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
