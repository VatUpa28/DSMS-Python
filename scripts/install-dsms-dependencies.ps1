#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $requirementsPath)) {
    throw "requirements.txt was not found: $requirementsPath"
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating the DSMS Python virtual environment..."

    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv $venvPath
    }
    elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
        $pythonVersion = & python -c `
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"

        if ($pythonVersion -ne "3.12") {
            throw "Python 3.12 is required. Detected Python $pythonVersion."
        }

        & python -m venv $venvPath
    }
    else {
        throw "Python 3.12 was not found. Install it before continuing."
    }

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw "The DSMS virtual environment could not be created."
    }
}
else {
    Write-Host "Existing DSMS virtual environment found."
}

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip

if ($LASTEXITCODE -ne 0) {
    throw "pip could not be upgraded."
}

Write-Host "Installing DSMS dependencies..."
& $venvPython -m pip install -r $requirementsPath

if ($LASTEXITCODE -ne 0) {
    throw "DSMS dependencies could not be installed."
}

Write-Host "Verifying the installation..."
& $venvPython -c `
    "import flask, waitress; print('Flask and Waitress imported successfully.')"

if ($LASTEXITCODE -ne 0) {
    throw "The DSMS dependency verification failed."
}

Write-Host ""
Write-Host "DSMS dependencies installed successfully."
Write-Host "Python: $venvPython"