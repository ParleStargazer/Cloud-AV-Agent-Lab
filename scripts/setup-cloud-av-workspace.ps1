param(
    [string]$WorkspaceRoot = "C:\CloudAvAgentLab"
)

$ErrorActionPreference = "Stop"

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

$BinDirectory = Join-Path $WorkspaceRoot "bin"
$CasesDirectory = Join-Path $WorkspaceRoot "cases"
$StartAgentScript = Join-Path $WorkspaceRoot "StartAgent.ps1"
$StartDesktopWorkerScript = Join-Path $WorkspaceRoot "StartDesktopWorker.ps1"

Write-Host "Creating Cloud AV Agent Lab workspace..."

foreach ($directory in @($WorkspaceRoot, $BinDirectory, $CasesDirectory)) {
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
        Write-Host "Directory created: $directory"
    } else {
        Write-Host "Directory already exists: $directory"
    }
}

$ReadmePath = Join-Path $WorkspaceRoot "README_WORKSPACE.txt"
$ReadmeContent = @"
Cloud AV Agent Lab workspace

Recommended layout:

C:\CloudAvAgentLab\
  bin\
    guest-agent.exe
    desktop-worker.exe
    _internal\
  cases\
  StartAgent.ps1
  StartDesktopWorker.ps1
  README_WORKSPACE.txt

Deployment notes:

- Copy the full contents of scripts\pack\dist\agent-suite\bin into C:\CloudAvAgentLab\bin.
- Scheduled tasks call StartAgent.ps1 and StartDesktopWorker.ps1.
- Edit the startup scripts on this VM if you need to set environment variables
  before launching guest-agent.exe or desktop-worker.exe.
- Keep real tokens out of source control, logs, reports, and evidence bundles.
- Do not place samples, secrets, configs\real.toml, or evidence bundles in this workspace root.
"@

Set-Content -Path $ReadmePath -Value $ReadmeContent -Encoding UTF8

Write-Host "Workspace guide written: $ReadmePath"

function Write-StartupScriptIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    if (Test-Path -LiteralPath $Path) {
        Write-Host "Startup script already exists, leaving it unchanged: $Path"
        return
    }

    Set-Content -Path $Path -Value $Content -Encoding UTF8
    Write-Host "Startup script created: $Path"
}

$AgentScriptContent = @'
param(
    [switch]$EnableExecutionActions
)

$ErrorActionPreference = "Stop"

# Optional: configure VM-local environment variables here before starting the
# service. Do not copy real tokens back into the repository.
#
# $env:CLOUD_AV_GUEST_AGENT_TOKEN = "replace-on-cloud-vm"
# $env:CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN = "replace-on-cloud-vm"
# $env:CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN = "replace-on-cloud-vm"
# $env:CLOUD_AV_DESKTOP_WORKER_TOKEN = "replace-on-cloud-vm"

$WorkspaceRoot = $PSScriptRoot
$BinDirectory = Join-Path $WorkspaceRoot "bin"
$GuestAgentExe = Join-Path $BinDirectory "guest-agent.exe"

if (-not (Test-Path -LiteralPath $GuestAgentExe)) {
    Write-Error "Guest Agent executable not found: $GuestAgentExe"
    exit 1
}

$Arguments = @(
    "--host", "0.0.0.0",
    "--port", "8080",
    "--workdir", $WorkspaceRoot,
    "--enable-desktop-worker",
    "--desktop-worker-url", "http://127.0.0.1:8001",
    "--desktop-worker-expected-user", "Administrator"
)

if ($EnableExecutionActions) {
    $Arguments += @(
        "--enable-execution-actions",
        "--execution-token-env", "CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
    )
}

& $GuestAgentExe @Arguments
exit $LASTEXITCODE
'@

$DesktopWorkerScriptContent = @'
$ErrorActionPreference = "Stop"

# Optional: configure VM-local environment variables here before starting the
# worker. Do not copy real tokens back into the repository.
#
# $env:CLOUD_AV_DESKTOP_WORKER_TOKEN = "replace-on-cloud-vm"

$WorkspaceRoot = $PSScriptRoot
$BinDirectory = Join-Path $WorkspaceRoot "bin"
$DesktopWorkerExe = Join-Path $BinDirectory "desktop-worker.exe"

if (-not (Test-Path -LiteralPath $DesktopWorkerExe)) {
    Write-Error "Desktop Worker executable not found: $DesktopWorkerExe"
    exit 1
}

& $DesktopWorkerExe --host 127.0.0.1 --port 8001 --workdir $WorkspaceRoot
exit $LASTEXITCODE
'@

Write-StartupScriptIfMissing -Path $StartAgentScript -Content $AgentScriptContent
Write-StartupScriptIfMissing -Path $StartDesktopWorkerScript -Content $DesktopWorkerScriptContent

Write-Host "Done. Workspace is ready: $WorkspaceRoot"
