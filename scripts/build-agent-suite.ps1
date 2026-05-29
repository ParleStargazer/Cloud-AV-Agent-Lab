param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$PackRoot = Join-Path $PSScriptRoot "pack"
$BuildRoot = Join-Path $PackRoot "build"
$DistRoot = Join-Path $PackRoot "dist"
$DistAgentSuite = Join-Path $DistRoot "agent-suite"
$PythonUserBase = Join-Path $PackRoot "python-userbase"

New-Item -ItemType Directory -Force -Path $PythonUserBase | Out-Null

$PythonPrefix = & $Python -c "import sys; print(sys.prefix)"
if ($LASTEXITCODE -ne 0) {
    throw "failed to resolve Python prefix with exit code $LASTEXITCODE"
}

$CondaLibraryBin = Join-Path $PythonPrefix "Library\bin"
$PythonDlls = Join-Path $PythonPrefix "DLLs"
$env:PATH = "$CondaLibraryBin;$PythonDlls;$PythonPrefix;$env:PATH"

Write-Host "Using build-local Python user base: $PythonUserBase"
Write-Host "Using Python prefix: $PythonPrefix"
$env:PYTHONUSERBASE = $PythonUserBase
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
Remove-Item Env:PYTHONNOUSERSITE -ErrorAction SilentlyContinue

Write-Host "Installing Guest Agent and Desktop Worker optional dependencies with $Python"
& $Python -m pip install --user --no-warn-script-location -e ".[guest-agent,guest-agent-build,desktop-worker,desktop-worker-build]"
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE"
}

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
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

Write-Host "Done:"
Write-Host "  $DistAgentSuite\bin\guest-agent.exe"
Write-Host "  $DistAgentSuite\bin\desktop-worker.exe"
