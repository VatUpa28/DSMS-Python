#Requires -RunAsAdministrator
#Requires -Version 5.1

param(
    [int]$Port = 8000,
    [int]$Threads = 8,
    [int]$BackupRetentionDays = 30,
    [string]$BackupTime = "02:00"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

$requiredScripts = @(
    "install-dsms-dependencies.ps1"
    "configure-dsms-server.ps1"
    "register-dsms-startup-task.ps1"
    "register-dsms-backup-task.ps1"
    "install-cloudflared.ps1"
    "register-cloudflare-tunnel.ps1"
)

Write-Host ""
Write-Host "========================================"
Write-Host "        DSMS Windows Server Setup"
Write-Host "========================================"
Write-Host ""
Write-Host "Project folder: $projectRoot"
Write-Host "Server port: $Port"
Write-Host "Waitress threads: $Threads"
Write-Host "Backup time: $BackupTime"
Write-Host "Backup retention: $BackupRetentionDays days"
Write-Host ""

foreach ($scriptName in $requiredScripts) {
    $scriptPath = Join-Path $PSScriptRoot $scriptName

    if (-not (Test-Path $scriptPath)) {
        throw "Required setup script was not found: $scriptPath"
    }
}

$operatingSystem = Get-CimInstance Win32_OperatingSystem

Write-Host "Operating system: $($operatingSystem.Caption)"
Write-Host "Windows version: $($operatingSystem.Version)"
Write-Host ""

$confirmation = Read-Host `
    "Type SETUP to configure this computer as the DSMS server"

if ($confirmation -cne "SETUP") {
    Write-Host "DSMS server setup cancelled."
    exit 0
}

Write-Host ""
Write-Host "[1/6] Installing Python dependencies..."

& (Join-Path $PSScriptRoot "install-dsms-dependencies.ps1")

Write-Host ""
Write-Host "[2/6] Configuring server settings..."

& (Join-Path $PSScriptRoot "configure-dsms-server.ps1") `
    -Port $Port `
    -Threads $Threads `
    -BackupRetentionDays $BackupRetentionDays

Write-Host ""
Write-Host "[3/6] Registering automatic server startup..."

& (Join-Path $PSScriptRoot "register-dsms-startup-task.ps1")

Write-Host ""
Write-Host "[4/6] Registering automatic database backups..."

& (Join-Path $PSScriptRoot "register-dsms-backup-task.ps1") `
    -BackupTime $BackupTime

Write-Host ""
Write-Host "[5/6] Installing Cloudflare Tunnel software..."

& (Join-Path $PSScriptRoot "install-cloudflared.ps1")

Write-Host ""
Write-Host "[6/6] Registering Cloudflare Tunnel service..."

& (Join-Path $PSScriptRoot "register-cloudflare-tunnel.ps1")

Write-Host ""
Write-Host "========================================"
Write-Host "     DSMS server setup completed"
Write-Host "========================================"
Write-Host ""
Write-Host "The following have been configured:"
Write-Host "  - Python virtual environment"
Write-Host "  - DSMS dependencies"
Write-Host "  - Machine-level production settings"
Write-Host "  - Local-only DSMS server binding"
Write-Host "  - Automatic DSMS startup task"
Write-Host "  - Daily database backup task"
Write-Host "  - Cloudflare Tunnel software"
Write-Host "  - Cloudflare Tunnel Windows service"
Write-Host ""
Write-Host "Restart Windows before testing DSMS."
Write-Host ""
Write-Host "After restarting, verify DSMS locally at:"
Write-Host "  http://127.0.0.1:$Port/health"
Write-Host ""
Write-Host "Remote access will use the hostname configured in Cloudflare."
Write-Host ""