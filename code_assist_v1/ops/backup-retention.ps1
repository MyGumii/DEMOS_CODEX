[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$BlobRoot,
    [Parameter(Mandatory = $true)][string]$ProjectionRoot,
    [Parameter(Mandatory = $true)][string]$BackupRoot
)

$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$python = Join-Path $workspaceRoot "venv\Scripts\python.exe"
$arguments = @(
    "-E", "-m", "code_assist_v1.tools.backup_restore", "prune",
    "--data-root", $DataRoot,
    "--database", $Database,
    "--blob-root", $BlobRoot,
    "--projection-root", $ProjectionRoot,
    "--backup-root", $BackupRoot
)
$processExitCode = 2
Push-Location -LiteralPath $workspaceRoot
try {
    & $python @arguments
    if ($null -ne $LASTEXITCODE) { $processExitCode = [int]$LASTEXITCODE }
}
finally { Pop-Location }
exit $processExitCode
