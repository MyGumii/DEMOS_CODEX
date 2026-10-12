[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("development", "production")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$DataRoot,

    [Parameter(Mandatory = $true)]
    [string]$Database,

    [Parameter(Mandatory = $true)]
    [string]$BlobRoot,

    [Parameter(Mandatory = $true)]
    [string]$ProjectionRoot,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeLock,

    [Parameter(Mandatory = $true)]
    [string]$PartWriteRolloutRecord
)

$workspaceRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..\..")
)
$python = Join-Path -Path $workspaceRoot -ChildPath "venv\Scripts\python.exe"
$moduleArguments = @(
    "-E",
    "-m", "code_assist_v1.tools.runtime_preflight",
    "--mode", $Mode,
    "--data-root", $DataRoot,
    "--database", $Database,
    "--blob-root", $BlobRoot,
    "--projection-root", $ProjectionRoot,
    "--runtime-lock", $RuntimeLock,
    "--part-write-rollout-record", $PartWriteRolloutRecord
)

$processExitCode = 2
Push-Location -LiteralPath $workspaceRoot
try {
    & $python @moduleArguments
    if ($null -ne $LASTEXITCODE) {
        $processExitCode = [int]$LASTEXITCODE
    }
}
finally {
    Pop-Location
}
exit $processExitCode
