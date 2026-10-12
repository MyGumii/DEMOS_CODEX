param(
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [Parameter(Mandatory=$true)][string]$SourceSha256,
    [Parameter(Mandatory=$true)][string]$Language,
    [Parameter(Mandatory=$true)][int]$TimeoutMs,
    [Parameter(Mandatory=$true)][int]$OutputBytes,
    [Parameter(Mandatory=$true)][int]$WordLimit,
    [Parameter(Mandatory=$true)][int]$SourcePixels
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ($SourceSha256 -notmatch '^[0-9a-f]{64}$' -or
    $Language -notmatch '^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8}){0,3}$' -or
    $TimeoutMs -lt 1 -or $TimeoutMs -gt 60000 -or
    $OutputBytes -lt 1 -or $OutputBytes -gt 1048576 -or
    $WordLimit -lt 1 -or $WordLimit -gt 4096 -or
    $SourcePixels -lt 1 -or $SourcePixels -gt 16000000) { throw 'Invalid bounded OCR input' }
$sourceFull = [IO.Path]::GetFullPath($SourcePath)
if (-not [IO.Path]::IsPathRooted($sourceFull) -or
    [IO.Path]::GetExtension($sourceFull) -ne '.png' -or
    (Get-Item -LiteralPath $sourceFull).Length -gt 16777216 -or
    (Get-FileHash -LiteralPath $sourceFull -Algorithm SHA256).Hash.ToLowerInvariant() -ne $SourceSha256) {
    throw 'Original PNG identity differs'
}
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
    $_.GetGenericArguments().Length -eq 1 -and $_.GetParameters().Length -eq 1 -and
    $_.GetParameters()[0].ParameterType.IsGenericType -and
    $_.GetParameters()[0].ParameterType.GetGenericTypeDefinition().FullName -eq 'Windows.Foundation.IAsyncOperation`1'
} | Select-Object -First 1
if ($null -eq $taskMethod) { throw 'WinRT task adapter unavailable' }
$timer = [Diagnostics.Stopwatch]::StartNew()
function Read-WinRtResult($Operation, [Type]$ResultType) {
    $remaining = $TimeoutMs - [int]$timer.ElapsedMilliseconds
    if ($remaining -le 0) { throw 'OCR deadline reached' }
    $task = $taskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait($remaining)) { throw 'OCR deadline reached' }
    return $task.Result
}
$selected = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Where-Object {
    $_.LanguageTag -ceq $Language
})
if ($selected.Count -ne 1) { throw 'Explicit installed OCR language unavailable' }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($selected[0])
if ($null -eq $engine) { throw 'Explicit OCR recognizer unavailable' }
$stream = $null
$bitmap = $null
try {
    $file = Read-WinRtResult ([Windows.Storage.StorageFile]::GetFileFromPathAsync($sourceFull)) ([Windows.Storage.StorageFile])
    $stream = Read-WinRtResult ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Read-WinRtResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if ($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
        $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
        [long]$decoder.PixelWidth*$decoder.PixelHeight -gt $SourcePixels) { throw 'Unscaled source exceeds OCR bounds' }
    $bitmap = Read-WinRtResult ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Read-WinRtResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    $wordCount = 0
    foreach ($line in $result.Lines) {
        if ($lines.Count -ge $WordLimit -or $line.Text.Length -gt 4096) { throw 'Complete OCR line bound exceeded' }
        $words = @()
        foreach ($word in $line.Words) {
            $wordCount += 1
            if ($wordCount -gt $WordLimit -or $word.Text.Length -gt 256) { throw 'Complete OCR word bound exceeded' }
            $r = $word.BoundingRect
            $words += [ordered]@{ text=$word.Text; bbox=@($r.X,$r.Y,$r.Width,$r.Height) }
        }
        $lines += [ordered]@{ text=$line.Text; words=$words }
    }
    if ((Get-FileHash -LiteralPath $sourceFull -Algorithm SHA256).Hash.ToLowerInvariant() -ne $SourceSha256) {
        throw 'Original source changed during OCR'
    }
    $rawAngle = $result.TextAngle
    $angle = if ($null -eq $rawAngle) { $null } elseif ($null -ne $rawAngle.PSObject.Properties['Value']) {
        [double]$rawAngle.Value
    } else { [double]$rawAngle }
    $report = [ordered]@{ version='pdf-native-source-winrt-ocr-v1'; engine='Windows.Media.Ocr';
        source_sha256=$SourceSha256; source_dimensions=@($decoder.PixelWidth,$decoder.PixelHeight);
        source_resized=$false; language=$engine.RecognizerLanguage.LanguageTag;
        text=$result.Text; text_angle=$angle; word_count=$wordCount; lines=$lines }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 10 -Compress))
    if ($bytes.Length -gt $OutputBytes) { throw 'Complete OCR stdout bound exceeded' }
    $output = [Console]::OpenStandardOutput()
    $output.Write($bytes,0,$bytes.Length)
    $output.Flush()
} finally {
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
