[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$BlobRoot,
    [Parameter(Mandatory = $true)][string]$ProjectionRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$BackupRunId,
    [Parameter(Mandatory = $true)][string]$AppVersion,
    [Parameter(Mandatory = $true)][string]$BuildRevision,
    [Parameter(Mandatory = $true)][string]$ConfigurationVersion,
    [Parameter(Mandatory = $true)][string]$LeaseHmacKeySetVersion
)

$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = Join-Path $workspaceRoot "venv\Scripts\python.exe"
$arguments = @(
    "-E", "-m", "code_assist_v1.tools.backup_restore", "create",
    "--data-root", $DataRoot,
    "--database", $Database,
    "--blob-root", $BlobRoot,
    "--projection-root", $ProjectionRoot,
    "--backup-root", $BackupRoot,
    "--backup-run-id", $BackupRunId,
    "--app-version", $AppVersion,
    "--build-revision", $BuildRevision,
    "--configuration-version", $ConfigurationVersion,
    "--lease-hmac-key-set-version", $LeaseHmacKeySetVersion
)
$processExitCode = 2
Push-Location -LiteralPath $workspaceRoot
try {
    & $python @arguments
    if ($null -ne $LASTEXITCODE) { $processExitCode = [int]$LASTEXITCODE }
}
finally { Pop-Location }
exit $processExitCode
