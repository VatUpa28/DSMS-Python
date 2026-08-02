$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$backend = Join-Path $projectRoot "backend"

if (-not (Test-Path $python)) {
    Write-Error "DSMS virtual environment was not found at: $python"
}

Write-Host "Starting DSMS database backup..."

Push-Location $backend

try {
    & $python -m database.backup_db

    if ($LASTEXITCODE -ne 0) {
        throw "DSMS backup failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}