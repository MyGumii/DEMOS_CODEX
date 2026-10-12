[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$Config,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$ConfigSha256,

    [Parameter(Mandatory = $true)]
    [System.Management.Automation.PSCredential]$ServiceCredential
)

$ErrorActionPreference = "Stop"
$serviceIds = @(
    "CodeAssist-Migrator",
    "CodeAssist-Web",
    "CodeAssist-Notion",
    "CodeAssist-DurableWorker",
    "CodeAssist-Projector"
)

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "PRODUCTION_INSTALL_REQUIRES_ADMINISTRATOR"
    }
}

function Invoke-IcaclsExact {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string[]]$Grant,
        [switch]$Tree
    )
    $arguments = @($LiteralPath, "/inheritance:r", "/grant:r") + $Grant
    if ($Tree) {
        $arguments += @("/T", "/C")
    }
    & "$env:SystemRoot\System32\icacls.exe" @arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "PRODUCTION_ACL_APPLY_FAILED"
    }
}

function Wait-ServiceInstalled {
    param([Parameter(Mandatory = $true)][string]$Name)
    $service = Get-CimInstance Win32_Service -Filter "Name='$Name'"
    if ($null -eq $service) {
        throw "PRODUCTION_SERVICE_INSTALL_FAILED"
    }
    return $service
}

Assert-Administrator
$configPath = [System.IO.Path]::GetFullPath($Config)
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "PRODUCTION_CONFIG_MISSING"
}
$observedConfigHash = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($observedConfigHash -cne $ConfigSha256) {
    throw "PRODUCTION_CONFIG_HASH_MISMATCH"
}
$manifest = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$account = $ServiceCredential.UserName
if (-not [string]::Equals($account, [string]$manifest.service_account, [StringComparison]::OrdinalIgnoreCase)) {
    throw "PRODUCTION_SERVICE_ACCOUNT_MISMATCH"
}
$accountSid = ([Security.Principal.NTAccount]::new($account)).Translate(
    [Security.Principal.SecurityIdentifier]
).Value
$systemGrant = "*S-1-5-18:(OI)(CI)F"
$adminGrant = "*S-1-5-32-544:(OI)(CI)F"
$serviceReadGrant = "*${accountSid}:(OI)(CI)RX"
$serviceModifyGrant = "*${accountSid}:(OI)(CI)M"
$fileSystemGrant = "*S-1-5-18:F"
$fileAdminGrant = "*S-1-5-32-544:F"
$fileServiceReadGrant = "*${accountSid}:R"
$serviceDirectory = Split-Path -Parent ([string]$manifest.paths.service_wrapper_executable)

foreach ($serviceId in $serviceIds) {
    if ($null -ne (Get-Service -Name $serviceId -ErrorAction SilentlyContinue)) {
        throw "PRODUCTION_SERVICE_ALREADY_INSTALLED"
    }
}

$python = [string]$manifest.paths.python_executable
if ($PSCmdlet.ShouldProcess("CodeAssist production services", "Render, ACL, and install")) {
    & $python -E -m code_assist_v1.tools.production_service `
        --config $configPath `
        --config-sha256 $ConfigSha256 `
        --service CodeAssist-Migrator `
        render
    if ($LASTEXITCODE -ne 0) {
        throw "PRODUCTION_SERVICE_RENDER_FAILED"
    }

    Invoke-IcaclsExact -LiteralPath ([string]$manifest.paths.working_directory) `
        -Grant @($systemGrant, $adminGrant, $serviceReadGrant) -Tree
    foreach ($mutableRoot in @(
        [string]$manifest.paths.data_root,
        [string]$manifest.paths.log_root,
        [string]$manifest.paths.backup_root
    )) {
        Invoke-IcaclsExact -LiteralPath $mutableRoot `
            -Grant @($systemGrant, $adminGrant, $serviceModifyGrant) -Tree
    }
    foreach ($protectedFile in @($configPath, [string]$manifest.paths.environment_file)) {
        Invoke-IcaclsExact -LiteralPath $protectedFile `
            -Grant @($fileSystemGrant, $fileAdminGrant, $fileServiceReadGrant)
    }

    $installed = [System.Collections.Generic.List[string]]::new()
    $bstr = [IntPtr]::Zero
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
            $ServiceCredential.Password
        )
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        foreach ($serviceId in $serviceIds) {
            $wrapper = Join-Path -Path $serviceDirectory `
                -ChildPath "$serviceId.exe"
            & $wrapper install | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "PRODUCTION_SERVICE_INSTALL_FAILED"
            }
            $installed.Add($serviceId)
            $service = Wait-ServiceInstalled -Name $serviceId
            $change = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{
                StartName = $account
                StartPassword = $plainPassword
            }
            if ($null -eq $change -or [int]$change.ReturnValue -ne 0) {
                throw "PRODUCTION_SERVICE_CREDENTIAL_FAILED"
            }
            $verified = Wait-ServiceInstalled -Name $serviceId
            if (-not [string]::Equals([string]$verified.StartName, $account, [StringComparison]::OrdinalIgnoreCase)) {
                throw "PRODUCTION_SERVICE_CREDENTIAL_MISMATCH"
            }
        }
    }
    catch {
        $rollback = $installed.ToArray()
        [Array]::Reverse($rollback)
        foreach ($serviceId in $rollback) {
            $wrapper = Join-Path -Path $serviceDirectory `
                -ChildPath "$serviceId.exe"
            & $wrapper uninstall | Out-Null
        }
        throw
    }
    finally {
        $plainPassword = $null
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
    }
}

[Console]::Out.WriteLine(
    '{"config_sha256":"' + $ConfigSha256 + '","service_count":5,"status":"installed"}'
)
