param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "Installing guest-agent optional dependencies with $Python"
& $Python -m pip install -e ".[guest-agent,guest-agent-build]"

$PackRoot = Join-Path $PSScriptRoot "pack"
$BuildRoot = Join-Path $PackRoot "build"
$DistRoot = Join-Path $PackRoot "dist"
$DistGuestAgent = Join-Path $DistRoot "guest-agent"

if (Test-Path $BuildRoot) {
    Remove-Item -Recurse -Force $BuildRoot
}
if (Test-Path $DistGuestAgent) {
    Remove-Item -Recurse -Force $DistGuestAgent
}

Write-Host "Building $DistGuestAgent\guest-agent.exe"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name guest-agent `
    --paths src `
    --distpath $DistRoot `
    --workpath $BuildRoot `
    src\cloud_av_agent_lab\guest_agent_server\main.py

Write-Host "Done: $DistGuestAgent\guest-agent.exe"