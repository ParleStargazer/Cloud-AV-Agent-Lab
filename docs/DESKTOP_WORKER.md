# Desktop Worker MVP

Desktop Worker is the next execution-layer split for Cloud AV Agent Lab. The
Control Agent / Guest Agent stays in Windows Session 0 and remains the stable
HTTP control plane. Desktop Worker runs in the interactive desktop session and
owns controlled sample launch and nearby process observation.

The current MVP implements:

- Desktop Worker server module with authenticated `GET /health`.
- Authenticated `POST /execute` for the current case's registered uploaded
  `.exe` and controlled `.bat` / `.cmd` samples.
- Authenticated `GET /execution-status/{case_id}` for low-intrusion process
  observation.
- Worker binds to `127.0.0.1` / `localhost` only.
- Worker reports pid, session id, interactive-session flag, desktop session
  state, username, version, bind host, and busy state.
- Control Agent exposes authenticated `GET /worker/status`.
- Local CLI can query `guest-worker-status`.
- `single-run` can require the Desktop Worker readiness gate before delivery.
- Control Agent signs a short-TTL execution lease before forwarding
  `execute_uploaded_sample` to Worker.

## Safety Boundary

Desktop Worker must never expose arbitrary command execution. It must not accept
shell, cmd, PowerShell, arguments, arbitrary paths, file browsing, sample
download, or sample content return APIs.

Worker token is a misuse-prevention control, not the final execution
authorization. Real execution also requires a short-TTL, single-use execution
lease bound to `case_id`, `sample_id`, `run_id`, and `expected_sha256`. Worker
rejects expired, mismatched, reused, or concurrent leases fail-closed.

Worker derives the sample path from shared case metadata; the caller never
passes a path. Worker resolves a narrow execution handler from the registered
`stored_filename`: `.exe` uses `pe_executable`, `.bat`/`.cmd` uses
`batch_script`, `.ps1` is recognized as `powershell_script` but disabled by
default, and unknown suffixes are rejected as `unsupported_file_type`. It
verifies metadata and sha256, confirms the path is under
`<workdir>\cases\<case_id>\sample\`, and starts only the resolved handler.
`.exe` uses `subprocess.Popen([sample_path], shell=False, cwd=sample_dir)`;
`.bat`/`.cmd` uses the fixed `C:\Windows\System32\cmd.exe /d /c call
<sample_path>` template with `shell=False`. Clients cannot provide cmd, shell,
arguments, interpreter, or path fields. Standard streams are redirected to
`DEVNULL`, Windows uses `CREATE_NO_WINDOW`, `close_fds=True` is set, and a
minimal allowlisted environment is passed so tokens, cloud secrets, proxy
variables, and real config paths are not inherited by the child process.

The Worker must not be exposed to the network:

```powershell
C:\CloudAvAgentLab\StartDesktopWorker.ps1
```

`--host 0.0.0.0` is intentionally rejected.

Use the same shared workdir as Control Agent:

```powershell
C:\CloudAvAgentLab\StartDesktopWorker.ps1
```

The recommended deployment layout is:

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

Both executables live under `C:\CloudAvAgentLab\bin` and share the PyInstaller
`_internal` directory. `C:\CloudAvAgentLab` remains the shared workdir. Startup
scripts in the workspace root launch the executables and are the recommended
place to configure VM-local environment variables.

## Environment Variables

```powershell
$env:CLOUD_AV_DESKTOP_WORKER_TOKEN = "replace-with-worker-token"
```

Configure the same worker token in both generated startup scripts:

- `C:\CloudAvAgentLab\StartAgent.ps1`, so Control Agent can call Worker over
  localhost;
- `C:\CloudAvAgentLab\StartDesktopWorker.ps1`, so Worker can verify incoming
  requests.

Do not write this token to configs, logs, reports, events, summary, or evidence
bundles.

## Control Agent Startup

Control Agent can proxy Worker readiness by enabling:

```powershell
C:\CloudAvAgentLab\StartAgent.ps1
```

`/worker/status` returns `desktop_worker_ready=false` when the Worker is
disabled, unreachable, busy, running in Session 0, not in an active desktop
session, or running under an unexpected user.

## Baseline Snapshot

The future baseline snapshot should contain:

- a dedicated test administrator account, recommended as `AvTester-Admin` to
  avoid mixing with the built-in Windows `Administrator` account;
- automatic login for that test administrator account;
- Desktop Worker startup on user login;
- Control Agent startup as Session 0 service or scheduled task;
- target security product initialized in the desktop session.

Automatic-login credentials and baseline setup scripts are sensitive assets and
must not enter the repository or evidence bundles.
