# Create-StartCloudAvAgentLabTask.ps1
# Run this script as Administrator.

param(
    [string]$WorkspaceRoot = "C:\CloudAvAgentLab",
    [int]$Port = 8080,
    [switch]$EnableExecutionActions
)

$TaskName = "Start-CloudAvAgentLab"

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

Write-Host "Preparing scheduled task configuration..."

$BinDirectory = Join-Path $WorkspaceRoot "bin"
$GuestAgentExe = Join-Path $BinDirectory "guest-agent.exe"

$GuestAgentArguments = "--host 0.0.0.0 --port $Port --workdir `"$WorkspaceRoot`" --enable-desktop-worker --desktop-worker-url http://127.0.0.1:8001 --desktop-worker-expected-user Administrator"

if ($EnableExecutionActions) {
    $GuestAgentArguments = "$GuestAgentArguments --enable-execution-actions --execution-token-env CLOUD_AV_GUEST_AGENT_EXECUTION_TOKEN"
}

if (-not (Test-Path -LiteralPath $GuestAgentExe)) {
    Write-Error "Guest Agent executable not found: $GuestAgentExe"
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute $GuestAgentExe `
    -Argument $GuestAgentArguments `
    -WorkingDirectory $BinDirectory

$Trigger = New-ScheduledTaskTrigger -AtStartup

# Run whether user is logged in or not.
# Run with highest privileges.
$Principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
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
    -Description "Start Cloud AV Agent Lab automatically at system startup."

Write-Host "Scheduled task '$TaskName' created successfully."
