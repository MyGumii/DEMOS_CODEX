param(
    [int]$Port = 10011,
    [int]$NotionPort = 10012,
    [switch]$Codex
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$python = Join-Path $projectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    $python = "python"
}

Set-Location $projectRoot
$env:CODE_ASSIST_PORT = "$Port"

$notionArgs = @("-u", "-m", "code_assist_v1.app_notion", "--port", "$NotionPort")
if ($Codex) {
    $notionArgs += "--codex"
}

$notionRunning = $false
$notionHealth = $null
try {
    $notionHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$NotionPort/healthz" -TimeoutSec 2
    $notionRunning = $notionHealth.status -eq "ok"
} catch {
    $notionRunning = $false
}

if (-not $notionRunning) {
    Start-Process -FilePath $python -ArgumentList $notionArgs -WorkingDirectory $projectRoot -WindowStyle Hidden
    Write-Host "Notion Workspace started at http://localhost:$NotionPort"
} else {
    Write-Host "Notion Workspace is already running at http://localhost:$NotionPort"
    if ($null -eq $notionHealth.ai_provider_order) {
        throw "Cannot verify the existing Notion Codex launch policy. Restart Notion with the same -Codex setting before launching the pair."
    }
    $notionCodexEnabled = @($notionHealth.ai_provider_order) -contains "codex"
    if ($notionCodexEnabled -ne [bool]$Codex) {
        throw "Notion is already running with a different Codex launch policy. Restart Notion with the same -Codex setting before launching the pair."
    }
}

$codeArgs = @("-u", "-m", "code_assist_v1.app_code", "--port", "$Port")
if ($Codex) {
    $codeArgs += "--codex"
}

& $python @codeArgs
