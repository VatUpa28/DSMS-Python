#Requires -RunAsAdministrator

param(
    [string]$BackupTime = "02:00"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backupScript = Join-Path $PSScriptRoot "backup-dsms.ps1"
$taskName = "DSMS Database Backup"

if (-not (Test-Path $backupScript)) {
    throw "Backup script was not found: $backupScript"
}

try {
    $scheduledTime = [datetime]::ParseExact(
        $BackupTime,
        "HH:mm",
        [Globalization.CultureInfo]::InvariantCulture
    )
}
catch {
    throw "BackupTime must use 24-hour HH:mm format, such as 02:00 or 23:30."
}

$powerShellArguments = @(
    "-NoProfile"
    "-ExecutionPolicy Bypass"
    "-File `"$backupScript`""
) -join " "

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $powerShellArguments `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $scheduledTime

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Creates and verifies a daily backup of the DSMS SQLite database." `
    -Force

Write-Host "Scheduled task registered successfully: $taskName"
Write-Host "Daily backup time: $BackupTime"