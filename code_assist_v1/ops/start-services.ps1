[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$ConfigSha256
)

$ErrorActionPreference = "Stop"
$configPath = [System.IO.Path]::GetFullPath($Config)
$observed = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($observed -cne $ConfigSha256) {
    throw "PRODUCTION_CONFIG_HASH_MISMATCH"
}
$manifest = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedOrder = @(
    "CodeAssist-Migrator",
    "CodeAssist-Web",
    "CodeAssist-Notion",
    "CodeAssist-DurableWorker",
    "CodeAssist-Projector"
)
if (($manifest.startup_order -join "|") -cne ($expectedOrder -join "|")) {
    throw "PRODUCTION_START_ORDER_MISMATCH"
}

function Wait-State {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$Name'"
        if ($null -eq $service) {
            throw "PRODUCTION_SERVICE_MISSING"
        }
        if ([string]$service.State -ceq $State) {
            return $service
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "PRODUCTION_SERVICE_STATE_TIMEOUT"
}

function Assert-ProductionHealth {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        $response = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/healthz" `
            -TimeoutSec 15
    }
    catch {
        throw "PRODUCTION_WEB_READINESS_FAILED"
    }
    if (
        [string]$response.report_schema -cne "code-assist.production-health" -or
        [int]$response.schema_version -ne 1 -or
        [string]$response.gate.status -cne "ready" -or
        @($response.gate.reason_codes).Count -ne 0
    ) {
        throw "PRODUCTION_WEB_READINESS_FAILED"
    }
}

if ($PSCmdlet.ShouldProcess("CodeAssist production services", "Migrate and start in reviewed order")) {
    Start-Service -Name "CodeAssist-Migrator"
    $migrator = Wait-State -Name "CodeAssist-Migrator" -State "Stopped" -TimeoutSeconds 180
    if ([int]$migrator.ExitCode -ne 0 -or [int]$migrator.ServiceSpecificExitCode -ne 0) {
        throw "PRODUCTION_MIGRATOR_FAILED"
    }

    Start-Service -Name "CodeAssist-Web"
    Wait-State -Name "CodeAssist-Web" -State "Running" -TimeoutSeconds 60 | Out-Null
    Assert-ProductionHealth -Port ([int]$manifest.network.code_port)

    Start-Service -Name "CodeAssist-Notion"
    Wait-State -Name "CodeAssist-Notion" -State "Running" -TimeoutSeconds 60 | Out-Null
    Assert-ProductionHealth -Port ([int]$manifest.network.notion_port)

    Start-Service -Name "CodeAssist-DurableWorker"
    Wait-State -Name "CodeAssist-DurableWorker" -State "Running" -TimeoutSeconds 60 | Out-Null
    Start-Service -Name "CodeAssist-Projector"
    Wait-State -Name "CodeAssist-Projector" -State "Running" -TimeoutSeconds 60 | Out-Null
}

[Console]::Out.WriteLine(
    '{"config_sha256":"' + $ConfigSha256 + '","service_count":4,"status":"running"}'
)
