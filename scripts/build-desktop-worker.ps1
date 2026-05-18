param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

Write-Host "Installing desktop-worker optional dependencies with $Python"
& $Python -m pip install -e ".[desktop-worker,desktop-worker-build]"

$PackRoot = Join-Path $PSScriptRoot "pack"
$BuildRoot = Join-Path $PackRoot "build"
$DistRoot = Join-Path $PackRoot "dist"
$DistDesktopWorker = Join-Path $DistRoot "desktop-worker"

if (Test-Path $BuildRoot) {
    Remove-Item -Recurse -Force $BuildRoot
}
if (Test-Path $DistDesktopWorker) {
    Remove-Item -Recurse -Force $DistDesktopWorker
}

Write-Host "Building $DistDesktopWorker\desktop-worker.exe"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name desktop-worker `
    --paths src `
    --distpath $DistRoot `
    --workpath $BuildRoot `
    src\cloud_av_agent_lab\desktop_worker\main.py

Write-Host "Done: $DistDesktopWorker\desktop-worker.exe"
