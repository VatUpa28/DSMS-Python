#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$taskName = "DSMS Database Backup"

$existingTask = Get-ScheduledTask `
    -TaskName $taskName `
    -ErrorAction SilentlyContinue

if (-not $existingTask) {
    Write-Host "Scheduled task does not exist: $taskName"
    exit 0
}

$confirmation = Read-Host `
    "Remove the '$taskName' scheduled task? Type YES to continue"

if ($confirmation -cne "YES") {
    Write-Host "Scheduled task removal cancelled."
    exit 0
}

Stop-ScheduledTask `
    -TaskName $taskName `
    -ErrorAction SilentlyContinue

Unregister-ScheduledTask `
    -TaskName $taskName `
    -Confirm:$false

Write-Host "Scheduled task removed successfully: $taskName"