"""Owned raw native-raster worker; bounded Windows job, no files/model calls."""
import sys


def main():
    import json
    from hashlib import sha256
    from pathlib import Path
    import struct
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    from code_assist_v1.knowledge_core.pdf_native_figure_source import (
        PdfNativeFigureLimits, extract_native_catalog, _json)
    def read(size):
        value=sys.stdin.buffer.read(size)
        if len(value)!=size:
            raise ValueError('Truncated native raster input')
        return value
    length=struct.unpack('!I',read(4))[0]
    if not 0 < length <= 16_384:
        raise ValueError('Invalid native raster header')
    header=json.loads(read(length))
    if (set(header)!={'version','raw_bytes','raw_sha256','pages','limits'}
            or type(header['version']) is not int or header['version']!=1):
        raise ValueError('Invalid native raster protocol')
    limits=PdfNativeFigureLimits(**header['limits'])
    if type(header['raw_bytes']) is not int or not 0 < header['raw_bytes'] <= limits.source_bytes:
        raise ValueError('Invalid native source size')
    raw=read(header['raw_bytes'])
    if sys.stdin.buffer.read(1) or sha256(raw).hexdigest()!=header['raw_sha256']:
        raise ValueError('Native source identity differs')
    if type(header['pages']) is not list:
        raise ValueError('Invalid native selected pages')
    value=extract_native_catalog(raw,limits=limits,pages=tuple(header['pages']))
    sys.stdout.buffer.write(_json(value).encode())
    sys.stdout.buffer.flush()


if __name__=='__main__':
    try:
        main()
    except Exception:
        sys.stderr.write('PDF_NATIVE_FIGURE_WORKER_FAILED\n')
        raise SystemExit(2)
