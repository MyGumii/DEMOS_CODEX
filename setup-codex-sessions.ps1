# Run once on each Windows PC from the external-drive workspace.
$ErrorActionPreference = 'Stop'
$workspaceRoot = $PSScriptRoot
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pythonCommand) { throw 'Install Python 3.9 or later and add it to PATH.' }
$nativeCodex = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nativeCodex) { throw 'Install Codex CLI first: npm install -g @openai/codex' }
$env:CODEX_PORTABLE_COMMAND = $nativeCodex.Source
$profileBlock = @'
# BEGIN CODEX WORKSPACE SESSION SYNC
function global:codex {
    $native = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $native) { throw 'Codex CLI is not installed.' }
    $targetDirectory = (Get-Location).Path
    for ($i = 0; $i -lt $args.Count; $i++) {
        if ($args[$i] -in @('-C', '--cd') -and $i + 1 -lt $args.Count) {
            $cdValue = $args[$i + 1]
            $targetDirectory = if ([IO.Path]::IsPathRooted($cdValue)) { [IO.Path]::GetFullPath($cdValue) } else { [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $cdValue)) }
            break
        }
        if ($args[$i] -like '--cd=*') {
            $cdValue = $args[$i].Substring(5)
            $targetDirectory = if ([IO.Path]::IsPathRooted($cdValue)) { [IO.Path]::GetFullPath($cdValue) } else { [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $cdValue)) }
            break
        }
    }
    $folder = [IO.DirectoryInfo]::new($targetDirectory)
    while ($folder) {
        $marker = Join-Path $folder.FullName '.codex-session-store\workspace.json'
        $launcher = Join-Path $folder.FullName 'codex-portable.ps1'
        if ((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $launcher)) {
            & $launcher @args
            return
        }
        $folder = $folder.Parent
    }
    & $native.Source @args
}
# END CODEX WORKSPACE SESSION SYNC
'@
$documentsRoot = [Environment]::GetFolderPath('MyDocuments')
if ([string]::IsNullOrWhiteSpace($documentsRoot)) {
    $documentsRoot = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::MyDocuments,
        [Environment+SpecialFolderOption]::DoNotVerify
    )
}
if ([string]::IsNullOrWhiteSpace($documentsRoot)) {
    $userRoot = [Environment]::GetFolderPath('UserProfile')
    if ([string]::IsNullOrWhiteSpace($userRoot)) {
        throw 'Cannot determine the user profile directory.'
    }
    $documentsRoot = Join-Path $userRoot 'Documents'
}
$profilePaths = @(
    $PROFILE.CurrentUserAllHosts,
    (Join-Path $documentsRoot 'WindowsPowerShell\profile.ps1'),
    (Join-Path $documentsRoot 'PowerShell\profile.ps1')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
$utf8Bom = [Text.UTF8Encoding]::new($true)
foreach ($profilePath in $profilePaths) {
    $parent = Split-Path -Parent $profilePath
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $text = if (Test-Path -LiteralPath $profilePath) { [IO.File]::ReadAllText($profilePath) } else { '' }
    $pattern = '(?ms)^# BEGIN CODEX WORKSPACE SESSION SYNC\r?\n.*?^# END CODEX WORKSPACE SESSION SYNC\r?\n?'
    $updated = [regex]::Replace($text, $pattern, '').TrimEnd() + "`r`n`r`n" + $profileBlock + "`r`n"
    if ($updated -ne $text) {
        if (Test-Path -LiteralPath $profilePath) {
            Copy-Item -LiteralPath $profilePath -Destination ($profilePath + '.codex-sessions-backup-' + (Get-Date -Format 'yyyyMMddHHmmss'))
        }
        [IO.File]::WriteAllText($profilePath, $updated, $utf8Bom)
    }
    Write-Host ('Configured: ' + $profilePath)
}
Invoke-Expression $profileBlock
& $pythonCommand.Source (Join-Path $workspaceRoot '.tools\codex-portable\session_sync.py') sync --workspace $workspaceRoot
if ($LASTEXITCODE -ne 0) { throw 'Session sync failed.' }
Write-Host 'Ready. Open a new PowerShell window and run codex resume in this workspace.'
