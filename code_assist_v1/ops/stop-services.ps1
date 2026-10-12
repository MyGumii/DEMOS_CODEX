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
$stopOrder = @(
    "CodeAssist-Projector",
    "CodeAssist-DurableWorker",
    "CodeAssist-Notion",
    "CodeAssist-Web"
)
if ($PSCmdlet.ShouldProcess("CodeAssist production services", "Gracefully drain and stop")) {
    foreach ($serviceId in $stopOrder) {
        $service = Get-Service -Name $serviceId -ErrorAction Stop
        if ($service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
            Stop-Service -Name $serviceId -ErrorAction Stop
            $service.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds(50)
            )
        }
    }
}

[Console]::Out.WriteLine(
    '{"config_sha256":"' + $ConfigSha256 + '","service_count":4,"status":"stopped"}'
)
