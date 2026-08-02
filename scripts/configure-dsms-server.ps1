#Requires -RunAsAdministrator

param(
    [int]$Port = 8000,
    [int]$Threads = 8,
    [int]$BackupRetentionDays = 30
)

$ErrorActionPreference = "Stop"

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

if ($Threads -lt 1) {
    throw "Threads must be at least 1."
}

if ($BackupRetentionDays -lt 1) {
    throw "Backup retention must be at least 1 day."
}

Write-Host "Enter the permanent DSMS secret key."
Write-Host "It must contain at least 32 characters."

$secureSecret = Read-Host `
    "DSMS secret key" `
    -AsSecureString

$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
    $secureSecret
)

try {
    $secretKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        $pointer
    )

    if ([string]::IsNullOrWhiteSpace($secretKey)) {
        throw "The secret key cannot be empty."
    }

    if ($secretKey.Length -lt 32) {
        throw "The secret key must contain at least 32 characters."
    }

    $settings = @{
        "DSMS_ENV"                    = "pilot"
        "DSMS_HOST"                   = "0.0.0.0"
        "DSMS_PORT"                   = $Port.ToString()
        "DSMS_THREADS"                = $Threads.ToString()
        "DSMS_SESSION_COOKIE_SECURE"  = "false"
        "DSMS_BACKUP_RETENTION_DAYS"  = $BackupRetentionDays.ToString()
        "DSMS_SECRET_KEY"             = $secretKey
    }

    foreach ($setting in $settings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $setting.Key,
            $setting.Value,
            "Machine"
        )
    }
}
finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }

    Remove-Variable secureSecret, secretKey, pointer `
        -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "DSMS server configuration completed."
Write-Host "Environment: pilot"
Write-Host "Listening address: 0.0.0.0:$Port"
Write-Host "Waitress threads: $Threads"
Write-Host "Backup retention: $BackupRetentionDays days"
Write-Host ""
Write-Host "Restart Windows before starting DSMS."