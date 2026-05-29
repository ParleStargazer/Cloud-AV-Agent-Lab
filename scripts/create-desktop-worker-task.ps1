# Create-StartWorkerTask.ps1
# Run this script as Administrator.

param(
    [string]$WorkspaceRoot = "C:\CloudAvAgentLab",
    [string]$TargetUser = "Administrator",
    [int]$Port = 8001
)

$TaskName = "Start-Worker"

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

Write-Host "Preparing scheduled task configuration..."

$BinDirectory = Join-Path $WorkspaceRoot "bin"
$DesktopWorkerExe = Join-Path $BinDirectory "desktop-worker.exe"

if (-not (Test-Path -LiteralPath $DesktopWorkerExe)) {
    Write-Error "Desktop Worker executable not found: $DesktopWorkerExe"
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute $DesktopWorkerExe `
    -Argument "--host 127.0.0.1 --port $Port --workdir `"$WorkspaceRoot`"" `
    -WorkingDirectory $BinDirectory

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $TargetUser

# Run only when Administrator is logged on, so the worker starts in the interactive desktop session.
$Principal = New-ScheduledTaskPrincipal `
    -UserId $TargetUser `
    -LogonType Interactive `
    -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Write-Host "Removing existing task if it already exists..."

try {
    Unregister-ScheduledTask `
        -TaskName $TaskName `
        -Confirm:$false `
        -ErrorAction Stop

    Write-Host "Existing task removed."
} catch {
    Write-Host "No existing task found."
}

Write-Host "Registering scheduled task '$TaskName'..."

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Start Desktop Worker when $TargetUser logs on."

Write-Host "Scheduled task '$TaskName' created successfully."
