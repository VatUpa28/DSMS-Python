$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$backend = Join-Path $projectRoot "backend"

if (-not (Test-Path $python)) {
    Write-Error "DSMS virtual environment was not found at: $python"
}

if (-not $env:DSMS_SECRET_KEY) {
    Write-Error "DSMS_SECRET_KEY has not been configured."
}

if (-not $env:DSMS_ENV) {
    $env:DSMS_ENV = "development"
}

if (-not $env:DSMS_HOST) {
    $env:DSMS_HOST = "127.0.0.1"
}

if (-not $env:DSMS_PORT) {
    $env:DSMS_PORT = "8000"
}

if (-not $env:DSMS_THREADS) {
    $env:DSMS_THREADS = "8"
}

Write-Host "Starting DSMS..."
Write-Host "Environment: $env:DSMS_ENV"
Write-Host "Address: http://$($env:DSMS_HOST):$($env:DSMS_PORT)"

Push-Location $backend

try {
    & $python server.py
}
finally {
    Pop-Location
}