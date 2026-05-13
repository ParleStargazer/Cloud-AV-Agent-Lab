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

From the local control plane, configure:

```toml
[guest_agent]
enabled = true
base_url = "http://<lighthouse-public-ip>:8080"
token_env = "CLOUD_AV_GUEST_AGENT_TOKEN"
timeout_seconds = 10
```

Then set the matching local token environment variable and run:

```powershell
$env:CLOUD_AV_GUEST_AGENT_TOKEN="replace-with-the-same-token"
$env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN="replace-with-the-same-upload-token"
python -m cloud_av_agent_lab guest-health --config configs/lab.local.toml --vm-id sg-win10
```

Do not put the token in TOML, logs, screenshots, or test fixtures.

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

It does not download samples, execute samples, expose arbitrary command
execution, or start shells such as `cmd` or PowerShell.
