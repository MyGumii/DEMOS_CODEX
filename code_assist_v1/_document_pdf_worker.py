"""Private one-request PDF worker. Started only inside the parent's bounded job."""
import sys


def main():
    import ctypes
    import importlib.util
    import json
    from pathlib import Path
    import struct
    import types
    # Suppress modal crash/error boxes; parent observes exit status or deadline.
    ctypes.WinDLL("kernel32").SetErrorMode(0x1 | 0x2 | 0x8000)
    stream = sys.stdin.buffer

    def read(size):
        value = stream.read(size)
        if len(value) != size:
            raise ValueError("Truncated PDF worker input")
        return value

    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= 8192:
        raise ValueError("Invalid PDF worker header")
    header = json.loads(read(size).decode("utf-8"))
    old_fields = {"version", "filename", "raw_bytes", "manifest_bytes", "limits"}
    if (type(header) is not dict or
            not ((header.get("version") == 1 and set(header) == old_fields)
                or (header.get("version") == 2 and set(header) == old_fields | {"allow_visual_only"}
                    and header["allow_visual_only"] is True)
                or (header.get("version") == 3 and set(header) == old_fields | {"allow_visual_only","allow_ambiguous_grid"}
                    and header["allow_visual_only"] is True and header["allow_ambiguous_grid"] is True))):
        raise ValueError("Invalid PDF worker protocol")
    if type(header["version"]) is not int or header["version"] not in (1, 2, 3):
        raise ValueError("Invalid PDF worker version")
    # Neutral namespace avoids application startup/configuration side effects.
    package = types.ModuleType("_pdf_worker_candidate")
    package.__path__ = [str(Path(__file__).parent)]
    sys.modules[package.__name__] = package
    for name in ("document_pdf_geometry", "document_pdf_geometry_codec"):
        spec = importlib.util.spec_from_file_location(package.__name__ + "." + name,
            Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)
    geometry, codec = package.document_pdf_geometry, package.document_pdf_geometry_codec
    from dataclasses import asdict
    if type(header["limits"]) is not dict or set(header["limits"]) != set(asdict(geometry.PdfGeometryLimits())):
        raise ValueError("Invalid PDF geometry policy")
    limits = geometry.PdfGeometryLimits(**header["limits"])
    if any(value > asdict(geometry.PdfGeometryLimits())[key] for key, value in asdict(limits).items()):
        raise ValueError("PDF geometry policy exceeds worker ceiling")
    raw_size, manifest_size = header["raw_bytes"], header["manifest_bytes"]
    if (type(raw_size) is not int or not 0 < raw_size <= limits.max_source_bytes
            or type(manifest_size) is not int or not 0 <= manifest_size <= limits.max_manifest_bytes):
        raise ValueError("Invalid PDF worker input sizes")
    raw, saved = read(raw_size), read(manifest_size)
    if stream.read(1):
        raise ValueError("Trailing PDF worker input")
    document = (codec.load_pdf_geometry(saved, header["filename"], raw, limits=limits)
                if manifest_size else geometry.extract_pdf_geometry(header["filename"], raw,
                    limits=limits, allow_visual_only=header["version"] in (2,3),
                    allow_ambiguous_grid=header["version"] == 3))
    if header["version"] == 1 and document.version != geometry.PDF_GEOMETRY_VERSION:
        raise ValueError("Visual-only PDF geometry needs its explicit worker policy")
    if header["version"] != 3 and document.version == geometry.PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION:
        raise ValueError("Ambiguous-grid geometry needs its explicit forward worker policy")
    sys.stdout.buffer.write(document.canonical_json(limits=limits).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No document text, paths or provider credentials in worker diagnostics.
        sys.stderr.write("PDF_WORKER_FAILED\n")
        raise SystemExit(2)
