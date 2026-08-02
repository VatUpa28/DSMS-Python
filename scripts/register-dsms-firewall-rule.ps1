#Requires -RunAsAdministrator

param(
    [int]$Port = 8000,
    [string[]]$AllowedRemoteAddress = @(
        "LocalSubnet",
        "100.64.0.0/10"
    )
)

$ErrorActionPreference = "Stop"

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

$ruleName = "DSMS Server TCP $Port"

$existingRule = Get-NetFirewallRule `
    -DisplayName $ruleName `
    -ErrorAction SilentlyContinue

if ($existingRule) {
    Remove-NetFirewallRule -DisplayName $ruleName
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress $AllowedRemoteAddress `
    -Profile Any `
    -Description "Allows DSMS access from the local network and Tailscale devices."

Write-Host "Windows Firewall rule created successfully."
Write-Host "Rule: $ruleName"
Write-Host "Allowed sources: $($AllowedRemoteAddress -join ', ')"