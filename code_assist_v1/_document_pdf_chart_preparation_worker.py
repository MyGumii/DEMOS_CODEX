"""Code-owned bounded native appearance action; no OCR or model calls."""
import sys


def main():
    from dataclasses import asdict
    from hashlib import sha256
    import json
    from pathlib import Path
    import struct
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from code_assist_v1.knowledge_core.pdf_native_figure_source import (
        PdfNativeFigureLimits, checked_native_figure_appearance)
    from code_assist_v1.knowledge_core.pdf_native_chart_input import _load
    stream = sys.stdin.buffer
    framing = stream.read(4)
    if len(framing) != 4:
        raise ValueError('Missing bounded header')
    length = struct.unpack('>I', framing)[0]
    if not 0 < length <= 4_194_304:
        raise ValueError('Invalid complete header budget')
    header = _load(stream.read(length))
    fields = {'version', 'action', 'raw_bytes', 'raw_sha256', 'png_bytes', 'png_sha256',
        'page', 'native_metadata', 'limits'}
    if type(header) is not dict or set(header) != fields or header['version'] != 1 or header['action'] != 'appearance':
        raise ValueError('Invalid appearance action')
    limits = PdfNativeFigureLimits(**header['limits'])
    if (header['limits'] != asdict(limits) or length > limits.manifest_bytes
            or type(header['raw_bytes']) is not int or not 0 < header['raw_bytes'] <= limits.source_bytes
            or type(header['png_bytes']) is not int or not 0 < header['png_bytes'] <= limits.image_bytes
            or type(header['page']) is not int or not 1 <= header['page'] <= limits.pages
            or type(header['native_metadata']) is not dict):
        raise ValueError('Invalid complete appearance input')
    raw, png = stream.read(header['raw_bytes']), stream.read(header['png_bytes'])
    if (len(raw) != header['raw_bytes'] or len(png) != header['png_bytes'] or stream.read(1)
            or sha256(raw).hexdigest() != header['raw_sha256']
            or sha256(png).hexdigest() != header['png_sha256']):
        raise ValueError('Original appearance bytes changed')
    result = checked_native_figure_appearance(raw, header['page'], header['native_metadata'], png, limits=limits)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > limits.manifest_bytes:
        raise ValueError('Complete appearance output budget')
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stderr.buffer.write(b'PDF_CHART_PREPARATION_WORKER_FAILED\n')
        sys.stderr.buffer.flush()
        raise SystemExit(2)
