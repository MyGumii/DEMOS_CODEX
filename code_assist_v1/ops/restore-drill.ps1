[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$BlobRoot,
    [Parameter(Mandatory = $true)][string]$ProjectionRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot,
    [Parameter(Mandatory = $true)][string]$BackupRunId,
    [Parameter(Mandatory = $true)][string]$IsolatedDataRoot,
    [Parameter(Mandatory = $true)][string]$IsolatedDatabase,
    [Parameter(Mandatory = $true)][string]$IsolatedBlobRoot,
    [Parameter(Mandatory = $true)][string]$IsolatedProjectionRoot,
    [int]$MaxRpoMinutes = 60,
    [int]$MaxRtoMinutes = 240
)

$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = Join-Path $workspaceRoot "venv\Scripts\python.exe"
$arguments = @(
    "-E", "-m", "code_assist_v1.tools.backup_restore", "restore-drill",
    "--data-root", $DataRoot,
    "--database", $Database,
    "--blob-root", $BlobRoot,
    "--projection-root", $ProjectionRoot,
    "--backup-root", $BackupRoot,
    "--backup-run-id", $BackupRunId,
    "--target-data-root", $IsolatedDataRoot,
    "--target-database", $IsolatedDatabase,
    "--target-blob-root", $IsolatedBlobRoot,
    "--target-projection-root", $IsolatedProjectionRoot,
    "--max-rpo-minutes", $MaxRpoMinutes,
    "--max-rto-minutes", $MaxRtoMinutes
)
$processExitCode = 2
Push-Location -LiteralPath $workspaceRoot
try {
    & $python @arguments
    if ($null -ne $LASTEXITCODE) { $processExitCode = [int]$LASTEXITCODE }
}
finally { Pop-Location }
exit $processExitCode
