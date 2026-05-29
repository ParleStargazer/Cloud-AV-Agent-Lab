# Create-StartWorkerTask.ps1
# Run this script as Administrator.

$TaskName = "Start-Worker"
$TargetUser = "Administrator"

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

Write-Host "Preparing scheduled task configuration..."

$Action = New-ScheduledTaskAction `
    -Execute "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument '-ExecutionPolicy Bypass -File "C:\Users\Administrator\desktop-worker\Start-worker.ps1"' `
    -WorkingDirectory "C:\Users\Administrator\desktop-worker"

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
    -Description "Start Desktop Worker when Administrator logs on."

Write-Host "Scheduled task '$TaskName' created successfully."
