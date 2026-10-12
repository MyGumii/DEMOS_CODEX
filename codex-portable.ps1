# All arguments are passed to the installed Codex CLI without rebinding flags.
$ErrorActionPreference = 'Stop'
$workspaceRoot = $PSScriptRoot
$syncScript = Join-Path $workspaceRoot '.tools\codex-portable\session_sync.py'
$nativeCodex = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nativeCodex) { throw 'Install Codex CLI first: npm install -g @openai/codex' }
$codexArguments = @($args)
$syncArguments = @('--workspace', $workspaceRoot)
# An explicit resume must not import every conversation in the workspace.
# Prefer a dedicated handover bundle when it contains the requested session.
$resumePosition = [Array]::IndexOf($codexArguments, 'resume')
if ($resumePosition -ge 0 -and $resumePosition + 1 -lt $codexArguments.Count) {
    $resumeArguments = @($codexArguments[($resumePosition + 1)..($codexArguments.Count - 1)])
    foreach ($storeName in @('.codex-notion-handover', '.codex-session-store')) {
        $candidateStore = Join-Path $workspaceRoot $storeName
        $candidateIndex = Join-Path $candidateStore 'threads.json'
        if (-not (Test-Path -LiteralPath $candidateIndex)) { continue }
        $index = [IO.File]::ReadAllText($candidateIndex) | ConvertFrom-Json
        $matches = @($index.threads.PSObject.Properties | Where-Object {
            $resumeArguments -contains $_.Name -or
            ($_.Value.row.name -and $resumeArguments -contains $_.Value.row.name)
        })
        if ($matches.Count -gt 1) { throw 'Resume arguments identify more than one portable session.' }
        if ($matches.Count -eq 1) {
            $syncArguments += @('--store', $candidateStore, '--session', $matches[0].Name)
            break
        }
    }
}
# The picker and --last operate on this PC's existing Codex history. Do not
# block them on importing or exporting unrelated portable conversations.
if ($resumePosition -ge 0 -and -not ($syncArguments -contains '--session')) {
    & $nativeCodex.Source -c 'tui.resume_cwd="current"' @codexArguments
    $global:LASTEXITCODE = $LASTEXITCODE
    return
}
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pythonCommand) { throw 'Install Python 3.9 or later and add it to PATH.' }
$previousCommand = $env:CODEX_PORTABLE_COMMAND
$watcher = $null
$syncSucceeded = $false
$codexExitCode = 1
$stopFile = Join-Path ([IO.Path]::GetTempPath()) ('codex-session-stop-' + [Guid]::NewGuid().ToString('N'))
$env:CODEX_PORTABLE_COMMAND = $nativeCodex.Source
try {
    & $pythonCommand.Source $syncScript sync @syncArguments
    if ($LASTEXITCODE -ne 0) { throw 'Session sync failed; Codex was not started.' }
    $syncSucceeded = $true
    $watchArgs = '-u "' + $syncScript + '" watch --workspace "' + $workspaceRoot + '" --parent-pid ' + $PID + ' --stop-file "' + $stopFile + '"'
    if ($syncArguments -contains '--session') {
        $storePosition = [Array]::IndexOf($syncArguments, '--store')
        $sessionPosition = [Array]::IndexOf($syncArguments, '--session')
        $watchArgs += ' --store "' + $syncArguments[$storePosition + 1] + '" --session ' + $syncArguments[$sessionPosition + 1]
    }
    $watcher = Start-Process -FilePath $pythonCommand.Source -ArgumentList $watchArgs -WindowStyle Hidden -PassThru
    # Prefer this PC's current directory when the external drive letter changes.
    # Explicit -C supplied by the user still takes precedence.
    & $nativeCodex.Source -c 'tui.resume_cwd="current"' @codexArguments
    $codexExitCode = $LASTEXITCODE
} finally {
    if ($watcher) {
        [IO.File]::WriteAllText($stopFile, 'stop')
        if (-not $watcher.WaitForExit(25000)) { $watcher.Kill() }
    }
    if ($syncSucceeded) {
        & $pythonCommand.Source $syncScript export @syncArguments
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Session export failed. Run the launcher again before moving the drive.' }
    }
    Remove-Item -LiteralPath $stopFile -ErrorAction SilentlyContinue
    $env:CODEX_PORTABLE_COMMAND = $previousCommand
}
$global:LASTEXITCODE = $codexExitCode
