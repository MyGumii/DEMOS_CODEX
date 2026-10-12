"""Server-owned, one-request PDF page renderer; launched in a bounded Windows job."""
import sys


def main():
    import base64
    import ctypes
    from hashlib import sha256
    import importlib.metadata
    from io import BytesIO
    import json
    import math
    import struct
    import pypdfium2 as pdfium

    ctypes.WinDLL("kernel32").SetErrorMode(0x1 | 0x2 | 0x8000)
    stream = sys.stdin.buffer
    def read(size):
        value = stream.read(size)
        if len(value) != size:
            raise ValueError("Truncated renderer input")
        return value
    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= 8192:
        raise ValueError("Invalid renderer header")
    header = json.loads(read(size).decode("utf-8"))
    if (type(header) is not dict or set(header) != {"version", "raw_bytes", "raw_sha256", "pages", "policy"}
            or type(header["version"]) is not int or header["version"] != 1):
        raise ValueError("Invalid renderer protocol")
    policy = header["policy"]
    ceilings = {"dpi": 288, "maximum_page_pixels": 16_000_000, "maximum_total_pixels": 256_000_000,
                "maximum_page_bytes": 16_777_216, "maximum_total_bytes": 67_108_864}
    if (type(policy) is not dict or set(policy) != set(ceilings)
            or any(type(v) is not int or not 1 <= v <= ceilings[k] for k, v in policy.items())
            or policy["dpi"] < 72 or type(header["raw_bytes"]) is not int
            or not 0 < header["raw_bytes"] <= 67_108_864
            or type(header["pages"]) is not int or not 0 < header["pages"] <= 1000):
        raise ValueError("Invalid renderer limits")
    raw = read(header["raw_bytes"])
    if stream.read(1) or sha256(raw).hexdigest() != header["raw_sha256"]:
        raise ValueError("Invalid renderer source")
    version = importlib.metadata.version("pypdfium2")
    if (version != "5.13.0" or str(pdfium.PDFIUM_INFO) != "153.0.7999.0"
            or importlib.metadata.version("Pillow") != "12.3.0"):
        raise ValueError("Unsupported renderer version")
    render_identity = {"pdfium_version": str(pdfium.PDFIUM_INFO), "png_encoder": "Pillow",
        "png_encoder_version": "12.3.0", "png_compress_level": 6, "png_optimize": False,
        "rotation": 0, "crop": [0, 0, 0, 0], "fill_color": [255, 255, 255, 255],
        "grayscale": False, "rev_byteorder": True, "draw_annots": True, "may_draw_forms": True,
        "coverage": "static-current-page-appearance", "scale": "dpi/72-canvas-units"}
    rows, total_bytes, total_pixels = [], 0, 0
    with pdfium.PdfDocument(raw) as pdf:
        if len(pdf) != header["pages"]:
            raise ValueError("Geometry and renderer page counts differ")
        pdf.init_forms()
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                width, height = page.get_size()
                scale = policy["dpi"] / 72
                if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                    raise ValueError("Invalid page canvas")
                expected = (math.ceil(width * scale), math.ceil(height * scale))
                pixels = expected[0] * expected[1]
                total_pixels += pixels
                if pixels > policy["maximum_page_pixels"] or total_pixels > policy["maximum_total_pixels"]:
                    raise ValueError("Page pixels exceed their budget")
                bitmap = page.render(scale=scale, rotation=0, crop=(0, 0, 0, 0),
                    fill_color=(255, 255, 255, 255), grayscale=False,
                    rev_byteorder=True, draw_annots=True, may_draw_forms=True)
                try:
                    with bitmap.to_pil() as image:
                        if image.size != expected:
                            raise ValueError("Rendered page dimensions differ")
                        output = BytesIO()
                        image.save(output, format="PNG", compress_level=6, optimize=False)
                        png = output.getvalue()
                finally:
                    bitmap.close()
                total_bytes += len(png)
                if len(png) > policy["maximum_page_bytes"] or total_bytes > policy["maximum_total_bytes"]:
                    raise ValueError("Rendered pages exceed their byte budget")
                rows.append({"page": index + 1, "width": expected[0], "height": expected[1],
                    "sha256": sha256(png).hexdigest(), "png": base64.b64encode(png).decode("ascii")})
            finally:
                page.close()
    result = {"version": 1, "raw_sha256": header["raw_sha256"], "renderer": "pypdfium2",
              "renderer_version": version, "render_identity": render_identity, "policy": policy, "pages": rows}
    sys.stdout.buffer.write(json.dumps(result, sort_keys=True, separators=(",", ":")).encode())
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("PDF_VISUAL_WORKER_FAILED\n")
        raise SystemExit(2)
