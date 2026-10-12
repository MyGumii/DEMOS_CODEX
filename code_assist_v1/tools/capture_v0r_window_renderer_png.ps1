param(
    [Parameter(Mandatory = $true)]
    [long]$WindowHandle,

    [Parameter(Mandatory = $true)]
    [ValidateSet(1280, 1440)]
    [int]$Width,

    [Parameter(Mandatory = $true)]
    [ValidateSet(800, 900)]
    [int]$Height,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$MaskRectsJson = "[]"
)

$ErrorActionPreference = "Stop"

if (($Width -eq 1280 -and $Height -ne 800) -or
    ($Width -eq 1440 -and $Height -ne 900)) {
    throw "V0R_CAPTURE_VIEWPORT_PAIR_INVALID:$Width`x$Height"
}

$resolvedParent = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::GetDirectoryName($OutputPath)
)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "V0R_CAPTURE_IMMUTABLE_OUTPUT_EXISTS:$resolvedOutput"
}
[System.IO.Directory]::CreateDirectory($resolvedParent) | Out-Null

try {
    $decodedMaskRects = $MaskRectsJson | ConvertFrom-Json
}
catch {
    throw "V0R_CAPTURE_MASK_RECTS_JSON_INVALID"
}
$maskRects = @($decodedMaskRects)
foreach ($maskRect in $maskRects) {
    if ($null -eq $maskRect -or
        -not ($maskRect.x -is [int] -or $maskRect.x -is [long]) -or
        -not ($maskRect.y -is [int] -or $maskRect.y -is [long]) -or
        -not ($maskRect.width -is [int] -or
            $maskRect.width -is [long]) -or
        -not ($maskRect.height -is [int] -or
            $maskRect.height -is [long]) -or
        $maskRect.x -lt 0 -or
        $maskRect.y -lt 0 -or
        $maskRect.width -le 0 -or
        $maskRect.height -le 0 -or
        $maskRect.x + $maskRect.width -gt $Width -or
        $maskRect.y + $maskRect.height -gt $Height) {
        throw "V0R_CAPTURE_MASK_RECT_INVALID"
    }
}

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class V0RRendererCaptureNative
{
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct WINDOWPLACEMENT
    {
        public int Length;
        public int Flags;
        public int ShowCmd;
        public POINT MinPosition;
        public POINT MaxPosition;
        public RECT NormalPosition;
    }

    [DllImport("user32.dll")]
    public static extern bool EnumChildWindows(
        IntPtr parent,
        EnumWindowsProc callback,
        IntPtr lParam
    );

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(
        IntPtr hWnd,
        StringBuilder text,
        int maxCount
    );

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool GetWindowPlacement(
        IntPtr hWnd,
        ref WINDOWPLACEMENT placement
    );

    [DllImport("user32.dll")]
    public static extern bool SetWindowPlacement(
        IntPtr hWnd,
        ref WINDOWPLACEMENT placement
    );

    [DllImport("user32.dll")]
    public static extern bool PrintWindow(
        IntPtr hWnd,
        IntPtr hdcBlt,
        uint flags
    );

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int command);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr insertAfter,
        int x,
        int y,
        int cx,
        int cy,
        uint flags
    );

    [DllImport("user32.dll")]
    public static extern IntPtr SetProcessDpiAwarenessContext(IntPtr value);

    public static RECT LargestRenderer(IntPtr parent)
    {
        RECT best = new RECT();
        long bestArea = -1;
        EnumChildWindows(
            parent,
            (h, l) =>
            {
                var className = new StringBuilder(256);
                GetClassName(h, className, className.Capacity);
                if (className.ToString() == "Chrome_RenderWidgetHostHWND")
                {
                    RECT candidate;
                    if (GetWindowRect(h, out candidate))
                    {
                        long area =
                            (long)(candidate.Right - candidate.Left) *
                            (candidate.Bottom - candidate.Top);
                        if (area > bestArea)
                        {
                            best = candidate;
                            bestArea = area;
                        }
                    }
                }
                return true;
            },
            IntPtr.Zero
        );
        if (bestArea < 0)
        {
            throw new InvalidOperationException(
                "V0R_CAPTURE_RENDERER_WINDOW_NOT_FOUND"
            );
        }
        return best;
    }
}
'@

[V0RRendererCaptureNative]::SetProcessDpiAwarenessContext([IntPtr](-4)) |
    Out-Null

$handle = [IntPtr]$WindowHandle
$placement = New-Object V0RRendererCaptureNative+WINDOWPLACEMENT
$placement.Length = [Runtime.InteropServices.Marshal]::SizeOf($placement)
if (-not [V0RRendererCaptureNative]::GetWindowPlacement(
    $handle,
    [ref]$placement
)) {
    throw "V0R_CAPTURE_GET_WINDOW_PLACEMENT_FAILED"
}

$capturedAt = (Get-Date).ToString("o")
$metadata = $null
$fullBitmap = $null
$pageBitmap = $null

try {
    [V0RRendererCaptureNative]::ShowWindow($handle, 9) | Out-Null
    Start-Sleep -Milliseconds 250

    $provisionalOuterWidth = $Width + 16
    # Keep the probing window inside the desktop work area. A probe taller
    # than the work area can make Chrome maximize itself again before the
    # renderer rectangle is measured.
    $provisionalOuterHeight = 1000
    if (-not [V0RRendererCaptureNative]::SetWindowPos(
        $handle,
        [IntPtr]::Zero,
        0,
        0,
        $provisionalOuterWidth,
        $provisionalOuterHeight,
        4
    )) {
        throw "V0R_CAPTURE_PROVISIONAL_RESIZE_FAILED"
    }
    $provisionalDeadline = (Get-Date).AddSeconds(5)
    $provisionalSettled = $false
    do {
        Start-Sleep -Milliseconds 250
        $provisionalWindow =
            New-Object V0RRendererCaptureNative+RECT
        if (-not [V0RRendererCaptureNative]::GetWindowRect(
            $handle,
            [ref]$provisionalWindow
        )) {
            throw "V0R_CAPTURE_GET_PROVISIONAL_WINDOW_RECT_FAILED"
        }
        $provisionalRenderer =
            [V0RRendererCaptureNative]::LargestRenderer($handle)
        $provisionalRendererWidth =
            $provisionalRenderer.Right - $provisionalRenderer.Left
        $provisionalSettled =
            $provisionalRendererWidth -eq $Width -and
            $provisionalRenderer.Left -ge $provisionalWindow.Left -and
            $provisionalRenderer.Right -le $provisionalWindow.Right -and
            $provisionalRenderer.Top -ge $provisionalWindow.Top -and
            $provisionalRenderer.Bottom -le $provisionalWindow.Bottom
    } while (-not $provisionalSettled -and
        (Get-Date) -lt $provisionalDeadline)
    if (-not $provisionalSettled) {
        throw "V0R_CAPTURE_PROVISIONAL_RENDERER_SETTLE_TIMEOUT"
    }
    $provisionalWindowHeight =
        $provisionalWindow.Bottom - $provisionalWindow.Top
    $provisionalRendererHeight =
        $provisionalRenderer.Bottom - $provisionalRenderer.Top
    $verticalOverhead =
        $provisionalWindowHeight - $provisionalRendererHeight

    if ($verticalOverhead -lt 100 -or $verticalOverhead -gt 300) {
        throw "V0R_CAPTURE_VERTICAL_OVERHEAD_DRIFT:$verticalOverhead"
    }

    $finalOuterHeight = $Height + $verticalOverhead
    if (-not [V0RRendererCaptureNative]::SetWindowPos(
        $handle,
        [IntPtr]::Zero,
        0,
        0,
        $Width + 16,
        $finalOuterHeight,
        4
    )) {
        throw "V0R_CAPTURE_FINAL_RESIZE_FAILED"
    }
    $finalDeadline = (Get-Date).AddSeconds(5)
    $finalSettled = $false
    do {
        Start-Sleep -Milliseconds 250
        $windowRect = New-Object V0RRendererCaptureNative+RECT
        if (-not [V0RRendererCaptureNative]::GetWindowRect(
            $handle,
            [ref]$windowRect
        )) {
            throw "V0R_CAPTURE_GET_FINAL_WINDOW_RECT_FAILED"
        }
        $rendererRect =
            [V0RRendererCaptureNative]::LargestRenderer($handle)
        $rendererWidth = $rendererRect.Right - $rendererRect.Left
        $rendererHeight = $rendererRect.Bottom - $rendererRect.Top
        $finalSettled =
            $rendererWidth -eq $Width -and
            $rendererHeight -eq $Height -and
            $rendererRect.Left -ge $windowRect.Left -and
            $rendererRect.Right -le $windowRect.Right -and
            $rendererRect.Top -ge $windowRect.Top -and
            $rendererRect.Bottom -le $windowRect.Bottom
    } while (-not $finalSettled -and (Get-Date) -lt $finalDeadline)
    if (-not $finalSettled) {
        throw (
            "V0R_CAPTURE_RENDERER_GEOMETRY_MISMATCH:" +
            "$rendererWidth`x$rendererHeight"
        )
    }

    $outerWidth = $windowRect.Right - $windowRect.Left
    $outerHeight = $windowRect.Bottom - $windowRect.Top
    $cropX = $rendererRect.Left - $windowRect.Left
    $cropY = $rendererRect.Top - $windowRect.Top

    $fullBitmap = New-Object System.Drawing.Bitmap(
        $outerWidth,
        $outerHeight,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($fullBitmap)
    $deviceContext = $graphics.GetHdc()
    try {
        $printSucceeded = [V0RRendererCaptureNative]::PrintWindow(
            $handle,
            $deviceContext,
            2
        )
    }
    finally {
        $graphics.ReleaseHdc($deviceContext)
        $graphics.Dispose()
    }
    if (-not $printSucceeded) {
        throw "V0R_CAPTURE_PRINT_WINDOW_FAILED"
    }

    $crop = New-Object System.Drawing.Rectangle(
        $cropX,
        $cropY,
        $Width,
        $Height
    )
    $pageBitmap = $fullBitmap.Clone(
        $crop,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    if ($maskRects.Count -gt 0) {
        $redactionGraphics =
            [System.Drawing.Graphics]::FromImage($pageBitmap)
        try {
            foreach ($maskRect in $maskRects) {
                $redactionGraphics.FillRectangle(
                    [System.Drawing.Brushes]::Black,
                    $maskRect.x,
                    $maskRect.y,
                    $maskRect.width,
                    $maskRect.height
                )
            }
        }
        finally {
            $redactionGraphics.Dispose()
        }
    }
    $pageBitmap.Save(
        $resolvedOutput,
        [System.Drawing.Imaging.ImageFormat]::Png
    )
    $pageBitmap.Dispose()
    $pageBitmap = $null
    $fullBitmap.Dispose()
    $fullBitmap = $null

    $payload = [System.IO.File]::ReadAllBytes($resolvedOutput)
    $signature = (($payload[0..7] | ForEach-Object {
        $_.ToString("x2")
    }) -join "")
    if ($signature -ne "89504e470d0a1a0a") {
        throw "V0R_CAPTURE_PNG_SIGNATURE_INVALID:$signature"
    }

    $metadata = [ordered]@{
        schema_version = 1
        contract = "code_assist.notion.v0r_p1.windows_renderer_capture.v1"
        captured_at = $capturedAt
        output_path = $resolvedOutput
        viewport = [ordered]@{
            width = $Width
            height = $Height
        }
        window_handle = $WindowHandle
        renderer_class = "Chrome_RenderWidgetHostHWND"
        renderer_rect = [ordered]@{
            screen_x = $rendererRect.Left
            screen_y = $rendererRect.Top
            width = $rendererWidth
            height = $rendererHeight
        }
        crop_rect = [ordered]@{
            x = $cropX
            y = $cropY
            width = $Width
            height = $Height
        }
        capture_api = "PrintWindow(PW_RENDERFULLCONTENT=2)"
        source_codec = "png"
        png_signature = $signature
        byte_length = $payload.Length
        sha256 = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedOutput
        ).Hash.ToLowerInvariant()
        source_bytes_written_directly = $true
        browser_chrome_excluded = $true
        debugger_banner_excluded = $true
        privacy_redaction_applied_before_source_write =
            $maskRects.Count -gt 0
        privacy_redaction_rect_count = $maskRects.Count
        privacy_redaction_rects = @($maskRects | ForEach-Object {
            [ordered]@{
                x = $_.x
                y = $_.y
                width = $_.width
                height = $_.height
            }
        })
        vertical_overhead = $verticalOverhead
    }
}
finally {
    if ($null -ne $pageBitmap) {
        $pageBitmap.Dispose()
    }
    if ($null -ne $fullBitmap) {
        $fullBitmap.Dispose()
    }
    [V0RRendererCaptureNative]::SetWindowPlacement(
        $handle,
        [ref]$placement
    ) | Out-Null
}

$metadata | ConvertTo-Json -Depth 5 -Compress
