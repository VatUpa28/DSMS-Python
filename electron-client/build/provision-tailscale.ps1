#Requires -RunAsAdministrator
#Requires -Version 5.1

param(
    [Parameter(Mandatory = $true)]
    [string]$AuthKeyFile
)

$ErrorActionPreference = "Stop"

# Ensure downloads work reliably in Windows PowerShell 5.1.
[Net.ServicePointManager]::SecurityProtocol = (
    [Net.ServicePointManager]::SecurityProtocol -bor
    [Net.SecurityProtocolType]::Tls12
)

$packagePageUrl = "https://pkgs.tailscale.com/stable/"
$temporaryDirectory = Join-Path `
    $env:TEMP `
    "DSMS-Tailscale-Setup"

$tailscalePath = Join-Path `
    $env:ProgramFiles `
    "Tailscale\tailscale.exe"


function Get-TailscaleStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath
    )

    if (-not (Test-Path -LiteralPath $ExecutablePath)) {
        return $null
    }

    try {
        $statusOutput = & $ExecutablePath status --json 2>$null

        if ($LASTEXITCODE -ne 0 -or -not $statusOutput) {
            return $null
        }

        return $statusOutput | ConvertFrom-Json
    }
    catch {
        return $null
    }
}


function Resolve-AuthKeyFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedPath
    )

    if ([string]::IsNullOrWhiteSpace($RequestedPath)) {
        throw "The Tailscale one-time key file was not provided."
    }

    if (
        -not (
            Test-Path `
                -LiteralPath $RequestedPath `
                -PathType Leaf
        )
    ) {
        throw (
            "The Tailscale one-time key file does not exist: " +
            $RequestedPath
        )
    }

    $resolvedPath = (
        Resolve-Path -LiteralPath $RequestedPath
    ).Path

    $keyValue = $null

    try {
        $keyValue = (
            Get-Content `
                -LiteralPath $resolvedPath `
                -Raw
        ).Trim()

        if ([string]::IsNullOrWhiteSpace($keyValue)) {
            throw "The Tailscale one-time key file is empty."
        }

        if (
            -not $keyValue.StartsWith(
                "tskey-",
                [StringComparison]::Ordinal
            )
        ) {
            throw "The Tailscale one-time setup key is invalid."
        }
    }
    finally {
        Remove-Variable keyValue `
            -ErrorAction SilentlyContinue
    }

    return $resolvedPath
}


function Get-LatestTailscaleInstaller {
    New-Item `
        -ItemType Directory `
        -Force `
        -Path $temporaryDirectory |
        Out-Null

    Write-Host "Finding the latest stable Tailscale installer..."

    $packagePage = Invoke-WebRequest `
        -Uri $packagePageUrl `
        -UseBasicParsing

    $installerMatches = [regex]::Matches(
        $packagePage.Content,
        'href="(?<href>[^"]*tailscale-setup-(?<version>[0-9.]+)-amd64\.msi)"',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    if ($installerMatches.Count -eq 0) {
        throw "The latest 64-bit Tailscale MSI could not be located."
    }

    $installerCandidates = foreach (
        $installerMatch in $installerMatches
    ) {
        $versionText = $installerMatch.Groups["version"].Value

        try {
            $version = [version]$versionText
        }
        catch {
            continue
        }

        [PSCustomObject]@{
            Version = $version
            Href = $installerMatch.Groups["href"].Value
        }
    }

    $latestInstaller = $installerCandidates |
        Sort-Object Version -Descending |
        Select-Object -First 1

    if (-not $latestInstaller) {
        throw "A valid Tailscale MSI version could not be determined."
    }

    $installerUrl = [Uri]::new(
        [Uri]$packagePageUrl,
        $latestInstaller.Href
    ).AbsoluteUri

    $installerName = Split-Path `
        ([Uri]$installerUrl).AbsolutePath `
        -Leaf

    $installerPath = Join-Path `
        $temporaryDirectory `
        $installerName

    Write-Host (
        "Downloading Tailscale " +
        "$($latestInstaller.Version)..."
    )

    Invoke-WebRequest `
        -Uri $installerUrl `
        -OutFile $installerPath `
        -UseBasicParsing

    if (
        -not (
            Test-Path `
                -LiteralPath $installerPath `
                -PathType Leaf
        )
    ) {
        throw "The Tailscale installer was not downloaded."
    }

    Write-Host "Verifying the installer checksum..."

    $checksumResponse = Invoke-WebRequest `
        -Uri "$installerUrl.sha256" `
        -UseBasicParsing

    $expectedChecksum = (
        $checksumResponse.Content -split "\s+"
    )[0].Trim().ToUpperInvariant()

    $actualChecksum = (
        Get-FileHash `
            -LiteralPath $installerPath `
            -Algorithm SHA256
    ).Hash.ToUpperInvariant()

    if ([string]::IsNullOrWhiteSpace($expectedChecksum)) {
        throw "The expected Tailscale checksum was empty."
    }

    if ($actualChecksum -ne $expectedChecksum) {
        throw (
            "The downloaded Tailscale installer failed " +
            "SHA-256 checksum verification."
        )
    }

    Write-Host "Verifying the installer digital signature..."

    $signature = Get-AuthenticodeSignature `
        -LiteralPath $installerPath

    if ($signature.Status -ne "Valid") {
        throw (
            "The Tailscale installer signature is not valid: " +
            $signature.Status
        )
    }

    return $installerPath
}


function Install-Tailscale {
    $installerPath = Get-LatestTailscaleInstaller

    Write-Host "Installing Tailscale..."

    $installerArguments = @(
        "/i"
        "`"$installerPath`""
        "/qn"
        "/norestart"
        "TS_NOLAUNCH=1"
        "TS_INSTALLUPDATES=always"
        "TS_ADMINCONSOLE=hide"
        "TS_NETWORKDEVICES=hide"
        "TS_EXITNODEMENU=hide"
        "TS_ADVERTISEEXITNODE=never"
        "TS_ALLOWINCOMINGCONNECTIONS=never"
        "TS_UNATTENDEDMODE=always"
        "TS_ENABLESUBNETS=never"
        "TS_ENABLEDNS=always"
        "TS_ONBOARDING_FLOW=hide"
    ) -join " "

    $installerProcess = Start-Process `
        -FilePath "msiexec.exe" `
        -ArgumentList $installerArguments `
        -Wait `
        -PassThru `
        -WindowStyle Hidden

    if ($installerProcess.ExitCode -notin @(0, 3010)) {
        throw (
            "Tailscale installation failed with exit code " +
            "$($installerProcess.ExitCode)."
        )
    }

    $executableFound = $false

    for ($attempt = 1; $attempt -le 20; $attempt++) {
        if (Test-Path -LiteralPath $tailscalePath) {
            $executableFound = $true
            break
        }

        Start-Sleep -Seconds 1
    }

    if (-not $executableFound) {
        throw (
            "Tailscale was installed, but tailscale.exe " +
            "could not be found."
        )
    }

    $service = Get-Service `
        -Name "Tailscale" `
        -ErrorAction SilentlyContinue

    if (-not $service) {
        throw "The Tailscale Windows service was not created."
    }

    if ($service.Status -ne "Running") {
        Start-Service -Name "Tailscale"

        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )
    }

    Write-Host "Tailscale installed successfully."
}


function Start-TailscaleService {
    $service = Get-Service `
        -Name "Tailscale" `
        -ErrorAction SilentlyContinue

    if (-not $service) {
        throw "The Tailscale Windows service was not found."
    }

    if ($service.Status -ne "Running") {
        Write-Host "Starting the Tailscale service..."

        Start-Service -Name "Tailscale"

        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )
    }
}


function Get-DsmsDeviceHostname {
    $computerName = $env:COMPUTERNAME

    if ([string]::IsNullOrWhiteSpace($computerName)) {
        $computerName = "windows-client"
    }

    $hostname = (
        "dsms-" + $computerName.ToLowerInvariant()
    ) -replace "[^a-z0-9-]", "-"

    $hostname = $hostname.Trim("-")

    while ($hostname.Contains("--")) {
        $hostname = $hostname.Replace("--", "-")
    }

    if ($hostname.Length -gt 63) {
        $hostname = $hostname.Substring(0, 63).TrimEnd("-")
    }

    if ([string]::IsNullOrWhiteSpace($hostname)) {
        return "dsms-windows-client"
    }

    return $hostname
}


function Connect-Tailscale {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAuthKeyFile
    )

    $deviceHostname = Get-DsmsDeviceHostname

    Write-Host ""
    Write-Host "Connecting this computer to the DSMS network..."
    Write-Host "Device name: $deviceHostname"

    $tailscaleArguments = @(
        "up"
        "--auth-key=file:$ResolvedAuthKeyFile"
        "--hostname=$deviceHostname"
        "--unattended=true"
        "--accept-dns=true"
        "--accept-routes=false"
        "--shields-up=true"
        "--timeout=60s"
    )

    & $tailscalePath @tailscaleArguments

    if ($LASTEXITCODE -ne 0) {
        throw (
            "Tailscale could not connect this computer " +
            "to the DSMS network."
        )
    }

    $connectedStatus = $null

    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $connectedStatus = Get-TailscaleStatus `
            -ExecutablePath $tailscalePath

        if (
            $connectedStatus -and
            $connectedStatus.BackendState -eq "Running"
        ) {
            break
        }

        Start-Sleep -Seconds 1
    }

    if (
        -not $connectedStatus -or
        $connectedStatus.BackendState -ne "Running"
    ) {
        throw "Tailscale did not report a connected state."
    }

    Write-Host ""
    Write-Host "Tailscale provisioning completed successfully."
    Write-Host "Device name: $deviceHostname"

    if ($connectedStatus.TailscaleIPs) {
        Write-Host (
            "Tailscale IP: " +
            ($connectedStatus.TailscaleIPs -join ", ")
        )
    }
}


$resolvedAuthKeyFile = $null
$tailscaleArguments = $null

try {
    $existingStatus = Get-TailscaleStatus `
        -ExecutablePath $tailscalePath

    if (
        $existingStatus -and
        $existingStatus.BackendState -eq "Running"
    ) {
        Write-Host "Tailscale is already installed and connected."
        Write-Host (
            "The existing Tailscale account and settings " +
            "were not changed."
        )

        exit 0
    }

    $resolvedAuthKeyFile = Resolve-AuthKeyFile `
        -RequestedPath $AuthKeyFile

    if (-not (Test-Path -LiteralPath $tailscalePath)) {
        Install-Tailscale
    }
    else {
        Write-Host "An existing Tailscale installation was found."
        Start-TailscaleService
    }

    Connect-Tailscale `
        -ResolvedAuthKeyFile $resolvedAuthKeyFile
}
finally {
    Remove-Variable `
        resolvedAuthKeyFile,
        tailscaleArguments `
        -ErrorAction SilentlyContinue

    Remove-Item `
        -Path $temporaryDirectory `
        -Recurse `
        -Force `
        -ErrorAction SilentlyContinue
}