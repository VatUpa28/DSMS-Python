#Requires -RunAsAdministrator
#Requires -Version 5.1

param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$installDirectory = "C:\Cloudflared\bin"
$cloudflaredPath = Join-Path $installDirectory "cloudflared.exe"

$downloadUrl = (
    "https://github.com/cloudflare/cloudflared/" +
    "releases/latest/download/cloudflared-windows-amd64.exe"
)

if ((Test-Path $cloudflaredPath) -and -not $Force) {
    Write-Host "cloudflared is already installed:"
    Write-Host "  $cloudflaredPath"

    & $cloudflaredPath --version

    if ($LASTEXITCODE -ne 0) {
        throw "The existing cloudflared executable could not be verified."
    }

    exit 0
}

New-Item `
    -ItemType Directory `
    -Force `
    -Path $installDirectory |
    Out-Null

$temporaryPath = Join-Path `
    $env:TEMP `
    "cloudflared-download.exe"

try {
    Write-Host "Downloading the latest 64-bit cloudflared release..."

    Invoke-WebRequest `
        -Uri $downloadUrl `
        -OutFile $temporaryPath `
        -UseBasicParsing

    if (-not (Test-Path $temporaryPath)) {
        throw "The cloudflared download was not created."
    }

    $signature = Get-AuthenticodeSignature $temporaryPath

    if ($signature.Status -ne "Valid") {
        throw "The cloudflared digital signature is not valid: $($signature.Status)"
    }

    if (
        -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch "Cloudflare"
    ) {
        throw "The downloaded executable was not signed by Cloudflare."
    }

    Copy-Item `
        -Path $temporaryPath `
        -Destination $cloudflaredPath `
        -Force

    Write-Host "Verifying cloudflared..."

    & $cloudflaredPath --version

    if ($LASTEXITCODE -ne 0) {
        throw "cloudflared installation verification failed."
    }
}
finally {
    Remove-Item `
        -Path $temporaryPath `
        -Force `
        -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloudflared installed successfully:"
Write-Host "  $cloudflaredPath"
Write-Host ""
Write-Host "Note: cloudflared must be updated manually on Windows."