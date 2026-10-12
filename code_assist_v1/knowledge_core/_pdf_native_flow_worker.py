"""One native word-flow request inside the existing bounded PDF process job."""
import sys


def derive(raw, document, geometry, limits):
    from collections import Counter
    from hashlib import sha256
    from io import BytesIO
    import pdfplumber
    from pdfplumber.utils.text import WordExtractor
    if sha256(raw).hexdigest() != document.source_sha256:
        raise ValueError('Native original identity differs')
    if pdfplumber.__version__ != geometry.PDF_ENGINE_VERSION:
        raise ValueError('Pinned native extraction engine required')
    symbols = lambda text:Counter(c for c in text if not c.isspace())
    if not any(isinstance(b,geometry.PdfTextBlock) for p in document.pages for b in p.blocks):
        # Visual-only/table-only geometry has no owned prose to reorder. Do not
        # demand glyph extraction that its original contract intentionally lacks.
        return dict(version='pdf-native-word-flow-owned-block-v1',source_raw_sha256=document.source_sha256,
            engine=geometry.PDF_ENGINE_VERSION,derivation='WordExtractor(use_text_flow=True,expand_ligatures=False); space iff centerY delta<=3 and x0>=previous x0-3, otherwise newline',
            semantic_reading_order_proven=False,pages=[dict(page=p.number,glyph_count=p.glyph_count,
                glyph_sha256=p.glyph_sha256,blocks=[]) for p in document.pages])
    pages = []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        if len(pdf.pages) != len(document.pages):
            raise ValueError('Native page inventory differs')
        total_glyphs = total_text = 0
        for original, page in zip(document.pages,pdf.pages,strict=True):
            chars = list(page.chars)
            total_glyphs += len(chars)
            if total_glyphs > limits.max_glyphs:
                raise ValueError('Native glyph budget exceeded')
            glyphs = [(geometry._text(c['text']),geometry._bbox((c['x0'],c['top'],c['x1'],c['bottom']))) for c in chars]
            if len(chars) != original.glyph_count or geometry._hash(geometry._json(glyphs)) != original.glyph_sha256:
                raise ValueError('Native original page glyph identity differs')
            by_identity = {id(c):i for i,c in enumerate(chars)}
            inventory = [i for block in original.blocks for i in (block.glyph_indices
                if isinstance(block,geometry.PdfTextBlock) else [i for cell in block.cells for i in cell.glyph_indices])]
            ambiguous = len(inventory) != len(set(inventory)) or any(type(i) is not int or not 0 <= i < len(chars) for i in inventory)
            blocks = []
            for block in original.blocks:
                if not isinstance(block,geometry.PdfTextBlock):
                    continue
                projected, word_indices, nonwhite, available = block.text, [], [], False
                if not ambiguous:
                    selected = [chars[i] for i in block.glyph_indices]
                    wordmap = WordExtractor(use_text_flow=True,expand_ligatures=False).extract_wordmap(selected)
                    parts, previous = [], None
                    for word, word_chars in wordmap.tuples:
                        separator = ''
                        if previous is not None:
                            same_line = abs((word['top']+word['bottom'])/2-(previous['top']+previous['bottom'])/2) <= 3
                            separator = ' ' if same_line and word['x0'] >= previous['x0']-3 else '\n'
                        parts.extend((separator,word['text'])); previous = word
                        word_indices.append([by_identity[id(c)] for c in word_chars if symbols(c['text'])])
                    candidate = ''.join(parts)
                    nonwhite = [i for i in block.glyph_indices if symbols(chars[i]['text'])]
                    available = (symbols(candidate) == symbols(block.text) == symbols(''.join(c['text'] for c in selected))
                        and Counter(i for word in word_indices for i in word) == Counter(nonwhite))
                    if available:
                        projected = candidate
                    else:
                        word_indices, nonwhite = [], []
                total_text += len(projected.encode('utf-8'))
                if total_text > limits.max_text_bytes:
                    raise ValueError('Native text budget exceeded')
                blocks.append(dict(block_id=block.block_id,source_text_sha256=sha256(block.text.encode()).hexdigest(),
                    glyph_indices=list(block.glyph_indices),nonwhitespace_glyph_indices=nonwhite,
                    native_word_glyph_indices=word_indices,projection_available=available,
                    projected_text=projected,projected_text_sha256=sha256(projected.encode()).hexdigest()))
            pages.append(dict(page=original.number,glyph_count=original.glyph_count,
                glyph_sha256=original.glyph_sha256,blocks=blocks))
    return dict(version='pdf-native-word-flow-owned-block-v1',source_raw_sha256=document.source_sha256,
        engine=geometry.PDF_ENGINE_VERSION,derivation='WordExtractor(use_text_flow=True,expand_ligatures=False); space iff centerY delta<=3 and x0>=previous x0-3, otherwise newline',
        semantic_reading_order_proven=False,pages=pages)


def main():
    import ctypes
    import importlib.util
    from dataclasses import asdict
    from hashlib import sha256
    import json
    from pathlib import Path
    import struct
    import types
    ctypes.WinDLL('kernel32').SetErrorMode(0x1|0x2|0x8000)
    def read(size):
        value=sys.stdin.buffer.read(size)
        if len(value)!=size: raise ValueError('Truncated native input')
        return value
    size=struct.unpack('!I',read(4))[0]
    if not 0<size<=8192: raise ValueError('Invalid native header size')
    header=json.loads(read(size).decode('utf-8'))
    if type(header) is not dict or set(header)!={'version','raw_bytes','raw_sha256','manifest_bytes','manifest_sha256','limits','maximum_output_bytes'} or header['version']!='pdf-native-word-flow-owned-block-v1':
        raise ValueError('Invalid native protocol')
    package=types.ModuleType('_native_flow_isolated');package.__path__=[str(Path(__file__).parent.parent)]
    sys.modules[package.__name__]=package
    for name in ('document_pdf_geometry','document_pdf_geometry_codec'):
        spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(__file__).parent.parent/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);setattr(package,name,module)
    geometry,codec=package.document_pdf_geometry,package.document_pdf_geometry_codec
    defaults=asdict(geometry.PdfGeometryLimits())
    if type(header['limits']) is not dict or set(header['limits'])!=set(defaults): raise ValueError('Invalid native limits')
    limits=geometry.PdfGeometryLimits(**header['limits'])
    if any(value>defaults[key] for key,value in asdict(limits).items()): raise ValueError('Native limits exceed ceiling')
    if (type(header['raw_bytes']) is not int or not 0<header['raw_bytes']<=limits.max_source_bytes
            or type(header['manifest_bytes']) is not int or not 0<header['manifest_bytes']<=limits.max_manifest_bytes
            or type(header['maximum_output_bytes']) is not int or not 0<header['maximum_output_bytes']<=limits.max_manifest_bytes):
        raise ValueError('Invalid native sizes')
    raw,manifest=read(header['raw_bytes']),read(header['manifest_bytes'])
    if sys.stdin.buffer.read(1) or sha256(raw).hexdigest()!=header['raw_sha256'] or sha256(manifest).hexdigest()!=header['manifest_sha256']:
        raise ValueError('Native retained inputs differ')
    document=codec._decode(manifest,limits)
    result=derive(raw,document,geometry,limits)
    result['source_geometry_sha256']=sha256(manifest).hexdigest()
    output=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    if len(output)>header['maximum_output_bytes']: raise ValueError('Native output budget exceeded')
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush()


if __name__=='__main__':
    try: main()
    except Exception:
        sys.stderr.write('PDF_NATIVE_FLOW_WORKER_FAILED\n');raise SystemExit(2)
