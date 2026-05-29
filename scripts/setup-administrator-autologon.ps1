# Setup-AutoLogon.ps1
# Run this script as Administrator.

param(
    [string]$Username = "AvTester-Admin"
)

$DownloadUrl = "https://download.sysinternals.com/files/AutoLogon.zip"
$WorkingDirectory = "C:\AutoLogon"
$ZipFile = Join-Path $WorkingDirectory "AutoLogon.zip"
$Executable = Join-Path $WorkingDirectory "AutoLogon64.exe"

Write-Host "Checking administrator privileges..."

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)

if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator."
    exit 1
}

Write-Host "Creating working directory..."

if (-not (Test-Path $WorkingDirectory)) {
    New-Item -ItemType Directory -Path $WorkingDirectory | Out-Null
    Write-Host "Directory created: $WorkingDirectory"
} else {
    Write-Host "Directory already exists: $WorkingDirectory"
}

Set-Location $WorkingDirectory

Write-Host "Downloading AutoLogon package..."

Invoke-WebRequest `
    -Uri $DownloadUrl `
    -OutFile $ZipFile

Write-Host "Download completed."

Write-Host "Extracting archive..."

tar -xf $ZipFile

Write-Host "Extraction completed."

if (-not (Test-Path $Executable)) {
    Write-Error "AutoLogon64.exe was not found after extraction."
    exit 1
}

Write-Host "Launching AutoLogon64.exe..."

Start-Process -FilePath $Executable

Write-Host "AutoLogon opened. Enter account '$Username' and its password manually."
Write-Host "Done."
