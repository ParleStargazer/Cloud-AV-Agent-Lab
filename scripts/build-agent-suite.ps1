param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "Installing Guest Agent and Desktop Worker optional dependencies with $Python"
& $Python -m pip install -e ".[guest-agent,guest-agent-build,desktop-worker,desktop-worker-build]"

$PackRoot = Join-Path $PSScriptRoot "pack"
$BuildRoot = Join-Path $PackRoot "build"
$DistRoot = Join-Path $PackRoot "dist"
$DistAgentSuite = Join-Path $DistRoot "agent-suite"

if (Test-Path $BuildRoot) {
    Remove-Item -Recurse -Force $BuildRoot
}
if (Test-Path $DistAgentSuite) {
    Remove-Item -Recurse -Force $DistAgentSuite
}

Write-Host "Building unified agent suite into $DistAgentSuite\bin"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistAgentSuite `
    --workpath $BuildRoot `
    scripts\agent-suite.spec

Write-Host "Done:"
Write-Host "  $DistAgentSuite\bin\guest-agent.exe"
Write-Host "  $DistAgentSuite\bin\desktop-worker.exe"
