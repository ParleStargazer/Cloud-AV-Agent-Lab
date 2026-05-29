# Create-AdministratorUser.ps1
# Run this script as Administrator.

param(
    [string]$Username = "Administrator",

    [Parameter(Mandatory = $true)]
    [string]$Password
)

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

Write-Host "Creating secure password object..."

$SecurePassword = ConvertTo-SecureString $Password -AsPlainText -Force

Write-Host "Checking whether user '$Username' already exists..."

$existingUser = Get-LocalUser -Name $Username -ErrorAction SilentlyContinue

if ($existingUser) {
    Write-Host "User '$Username' already exists. Updating password and enabling account..."

    Set-LocalUser -Name $Username -Password $SecurePassword -ErrorAction Stop
    Enable-LocalUser -Name $Username -ErrorAction Stop
} else {
    Write-Host "Creating local user '$Username'..."

    New-LocalUser `
        -Name $Username `
        -Password $SecurePassword `
        -FullName "Cloud AV Administrator" `
        -Description "Cloud AV Agent Lab administrator account" `
        -ErrorAction Stop
}

Write-Host "Ensuring password does not expire..."

Set-LocalUser -Name $Username -PasswordNeverExpires $true -ErrorAction Stop

Write-Host "Ensuring user is an administrator..."

try {
    Add-LocalGroupMember -Group "Users" -Member $Username -ErrorAction Stop
    Write-Host "User '$Username' added to Users group."
} catch {
    Write-Host "User '$Username' may already be in Users group."
}

try {
    Add-LocalGroupMember -Group "Administrators" -Member $Username -ErrorAction Stop
    Write-Host "User '$Username' added to Administrators group."
} catch {
    Write-Host "User '$Username' may already be in Administrators group."
}

Write-Host "Done. Administrator account '$Username' is ready."
