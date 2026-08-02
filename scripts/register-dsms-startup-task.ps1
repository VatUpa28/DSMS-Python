#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$startupScript = Join-Path $PSScriptRoot "start-dsms.ps1"
$taskName = "DSMS Server"

if (-not (Test-Path $startupScript)) {
    throw "Startup script was not found: $startupScript"
}

$powerShellArguments = @(
    "-NoProfile"
    "-ExecutionPolicy Bypass"
    "-File `"$startupScript`""
) -join " "

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $powerShellArguments `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)

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
    -Description "Starts the DSMS Waitress server automatically when Windows starts." `
    -Force

Write-Host "Scheduled task registered successfully: $taskName"
Write-Host "DSMS will start automatically when Windows starts."