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
  README_WORKSPACE.txt

Deployment notes:

- Copy the full contents of scripts\pack\dist\agent-suite\bin into C:\CloudAvAgentLab\bin.
- Start Guest Agent with --workdir C:\CloudAvAgentLab.
- Start Desktop Worker with --workdir C:\CloudAvAgentLab.
- Keep tokens in Windows environment variables, not in this directory.
- Do not place samples, secrets, configs\real.toml, or evidence bundles in this workspace root.
"@

Set-Content -Path $ReadmePath -Value $ReadmeContent -Encoding UTF8

Write-Host "Workspace guide written: $ReadmePath"
Write-Host "Done. Workspace is ready: $WorkspaceRoot"
