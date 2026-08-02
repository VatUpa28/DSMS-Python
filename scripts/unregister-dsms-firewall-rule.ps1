#Requires -RunAsAdministrator

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

$ruleName = "DSMS Server TCP $Port"

$existingRule = Get-NetFirewallRule `
    -DisplayName $ruleName `
    -ErrorAction SilentlyContinue

if (-not $existingRule) {
    Write-Host "Firewall rule does not exist: $ruleName"
    exit 0
}

$confirmation = Read-Host `
    "Remove the '$ruleName' firewall rule? Type YES to continue"

if ($confirmation -cne "YES") {
    Write-Host "Firewall rule removal cancelled."
    exit 0
}

Remove-NetFirewallRule -DisplayName $ruleName

Write-Host "Firewall rule removed successfully: $ruleName"