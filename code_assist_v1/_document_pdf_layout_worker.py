"""Owned, resource-isolated forward layout extractor. No files or provider calls."""
import sys


def main():
    from hashlib import sha256
    import json
    from pathlib import Path
    import struct
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from document_pdf_layout import extract_layout, canonical, PdfLayoutLimits
    def read(size):
        value = sys.stdin.buffer.read(size)
        if len(value) != size:
            raise ValueError('Truncated layout input')
        return value
    size = struct.unpack('!I', read(4))[0]
    if not 0 < size <= 8192:
        raise ValueError('Invalid layout header')
    header = json.loads(read(size))
    if set(header) != {'version','raw_bytes','raw_sha256','limits'} or type(header['version']) is not int or header['version'] != 1:
        raise ValueError('Invalid layout protocol')
    limits = PdfLayoutLimits(**header['limits'])
    if type(header['raw_bytes']) is not int or not 0 < header['raw_bytes'] <= limits.source_bytes:
        raise ValueError('Invalid layout source size')
    raw = read(header['raw_bytes'])
    if sys.stdin.buffer.read(1) or sha256(raw).hexdigest() != header['raw_sha256']:
        raise ValueError('Layout source differs')
    sys.stdout.buffer.write(canonical(extract_layout(raw, limits=limits)).encode())
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stderr.write('PDF_LAYOUT_WORKER_FAILED\n')
        raise SystemExit(2)
