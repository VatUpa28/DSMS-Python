#Requires -RunAsAdministrator
#Requires -Version 5.1

param(
    [string]$CloudflaredPath = ""
)

$ErrorActionPreference = "Stop"

function Find-Cloudflared {
    param(
        [string]$RequestedPath
    )

    if ($RequestedPath) {
        if (-not (Test-Path $RequestedPath)) {
            throw "cloudflared was not found at: $RequestedPath"
        }

        return (Resolve-Path $RequestedPath).Path
    }

    $command = Get-Command "cloudflared.exe" `
        -ErrorAction SilentlyContinue

    if ($command) {
        return $command.Source
    }

    $knownPaths = @(
        "C:\Program Files\cloudflared\cloudflared.exe"
        "C:\Program Files (x86)\cloudflared\cloudflared.exe"
        "C:\Cloudflared\bin\cloudflared.exe"
    )

    foreach ($path in $knownPaths) {
        if (Test-Path $path) {
            return $path
        }
    }

    $serviceRecord = Get-CimInstance Win32_Service `
        -Filter "Name='cloudflared'" `
        -ErrorAction SilentlyContinue

    if ($serviceRecord -and $serviceRecord.PathName) {
        $executablePath = $null

        if ($serviceRecord.PathName -match '^"([^"]+\.exe)"') {
            $executablePath = $matches[1]
        }
        elseif ($serviceRecord.PathName -match '^(\S+\.exe)') {
            $executablePath = $matches[1]
        }

        if ($executablePath -and (Test-Path $executablePath)) {
            return $executablePath
        }
    }

    throw "cloudflared.exe could not be found."
}

$existingService = Get-Service `
    -Name "cloudflared" `
    -ErrorAction SilentlyContinue

if (-not $existingService) {
    Write-Host "The Cloudflare Tunnel service is not installed."
    exit 0
}

Write-Host "Cloudflare Tunnel service found."
Write-Host "Current status: $($existingService.Status)"
Write-Host ""

$confirmation = Read-Host `
    "Remove the Cloudflare Tunnel service? Type YES to continue"

if ($confirmation -cne "YES") {
    Write-Host "Cloudflare Tunnel removal cancelled."
    exit 0
}

$cloudflared = Find-Cloudflared `
    -RequestedPath $CloudflaredPath

Write-Host ""
Write-Host "Removing the Cloudflare Tunnel service..."

& $cloudflared service uninstall

if ($LASTEXITCODE -ne 0) {
    throw "Cloudflare Tunnel removal failed with exit code $LASTEXITCODE."
}

$remainingService = Get-Service `
    -Name "cloudflared" `
    -ErrorAction SilentlyContinue

if ($remainingService) {
    throw "The cloudflared service still exists after the uninstall command."
}

Write-Host ""
Write-Host "Cloudflare Tunnel service removed successfully."
Write-Host "The DSMS database, application files, and Cloudflare dashboard tunnel were not deleted."