$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$backend = Join-Path $projectRoot "backend"
$logDirectory = Join-Path $projectRoot "logs"
$logPath = Join-Path $logDirectory "dsms-backup.log"

if (-not (Test-Path $python)) {
    throw "DSMS virtual environment was not found at: $python"
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

try {
    Add-Content $logPath "$timestamp | INFO | Starting DSMS database backup"

    Push-Location $backend

    try {
        $output = & $python -m database.backup_db 2>&1

        if ($LASTEXITCODE -ne 0) {
            throw "Backup command failed with exit code $LASTEXITCODE. $output"
        }

        foreach ($line in $output) {
            Add-Content $logPath "$timestamp | INFO | $line"
            Write-Host $line
        }
    }
    finally {
        Pop-Location
    }

    Add-Content $logPath "$timestamp | INFO | Backup completed successfully"
}
catch {
    Add-Content $logPath "$timestamp | ERROR | $($_.Exception.Message)"
    throw
}