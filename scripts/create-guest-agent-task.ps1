# Create-StartCloudAvAgentLabTask.ps1
# Run this script as Administrator.

param(
    [string]$WorkspaceRoot = "C:\CloudAvAgentLab",
    [switch]$EnableExecutionActions,
    [switch]$DisableExecutionActions
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

$StartAgentScript = Join-Path $WorkspaceRoot "StartAgent.ps1"

$StartAgentArguments = "-ExecutionPolicy Bypass -File `"$StartAgentScript`""

if ($DisableExecutionActions) {
    $StartAgentArguments = "$StartAgentArguments -DisableExecutionActions"
} elseif ($EnableExecutionActions) {
    Write-Host "Execution actions are enabled by StartAgent.ps1 by default; -EnableExecutionActions is accepted for compatibility."
}

if (-not (Test-Path -LiteralPath $StartAgentScript)) {
    Write-Error "Guest Agent startup script not found: $StartAgentScript"
    Write-Error "Run scripts\setup-cloud-av-workspace.ps1 first, then edit StartAgent.ps1 if needed."
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument $StartAgentArguments `
    -WorkingDirectory $WorkspaceRoot

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
