"""Bounded raw-PDF ROI child; no storage/provider/native-raster extraction."""
import sys


def main():
    import ctypes
    import json
    from pathlib import Path
    import struct
    ctypes.WinDLL('kernel32').SetErrorMode(0x1 | 0x2 | 0x8000)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from code_assist_v1.knowledge_core.pdf_figure_roi_source import (
        PdfFigureRoiLimits, PdfFigureRoiIneligible, render_figure_roi, _json)
    from hashlib import sha256

    def read(size):
        data = sys.stdin.buffer.read(size)
        if len(data) != size:
            raise ValueError('Truncated ROI worker input')
        return data

    length = struct.unpack('!I', read(4))[0]
    if not 0 < length <= 16_384:
        raise ValueError('ROI header exceeds bound')
    request = json.loads(read(length))
    limits = PdfFigureRoiLimits(**request['limits'])
    size = request['raw_bytes']
    if type(size) is not int or not 0 < size <= limits.source_bytes:
        raise ValueError('ROI source exceeds bound')
    raw = read(size)
    if sys.stdin.buffer.read(1):
        raise ValueError('Unexpected ROI input suffix')
    try:
        result = render_figure_roi(raw, request, limits=limits)
    except PdfFigureRoiIneligible as error:
        result = {'version': request['version'], 'request_sha256': sha256(_json(request).encode()).hexdigest(),
            'status': 'ineligible', 'reason': error.reason}
    sys.stdout.buffer.write(_json(result).encode())
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stderr.write('PDF_FIGURE_ROI_WORKER_FAILED\n')
        raise SystemExit(2)
