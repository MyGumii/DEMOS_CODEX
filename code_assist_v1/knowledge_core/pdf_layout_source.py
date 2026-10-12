"""Process-local layout capability bound to an existing prepared visual source.

Only the isolated original-PDF worker creates the catalog. Crops derive from
already-attested full page pixels; there is no hash-only public constructor.
This forward capability is not an archive format or publication permission.
"""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
from io import BytesIO
import json
import math
from pathlib import Path
import secrets
import struct

from ..document_pdf_layout import VERSION, PdfLayoutLimits, PdfLayoutError, canonical
from ..document_pdf_process import _run_worker, PdfProcessPolicy
from ..llm_images import ModelImageInput
from .pdf_visual_source import PreparedPdfVisualSource, checked_visual_source
from .pdf_native_figure_source import PreparedPdfNativeFigures, checked_native_figures

_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class PreparedPdfLayout:
    source: PreparedPdfVisualSource = field(repr=False)
    catalog_json: str = field(repr=False)
    crops: tuple[ModelImageInput, ...] = field(repr=False)
    limits: PdfLayoutLimits
    seal: str = field(repr=False)
    native_figures: PreparedPdfNativeFigures | None = field(default=None,repr=False)

    def identity(self):
        result = {'version':VERSION,'source':self.source.identity(),
                'catalog_sha256':sha256(self.catalog_json.encode()).hexdigest(),
                'limits':asdict(self.limits),
                'crops':[{'source_id':c.source_id,'sha256':sha256(c.data).hexdigest(),
                          'bytes':len(c.data),'media_type':c.media_type} for c in self.crops]}
        if self.native_figures is not None:
            result['native_figures']=self.native_figures.identity()
        return result


def _seal(value):
    return hmac.digest(_KEY, canonical(value.identity()).encode(), 'sha256').hex()


def checked_layout(value):
    if (type(value) is not PreparedPdfLayout or type(value.limits) is not PdfLayoutLimits
            or type(value.catalog_json) is not str or type(value.crops) is not tuple
            or (value.native_figures is not None and type(value.native_figures) is not PreparedPdfNativeFigures)
            or type(value.seal) is not str or not hmac.compare_digest(value.seal, _seal(value))):
        raise PdfLayoutError('An unchanged process-local layout preparation is required')
    checked_visual_source(value.source)
    if value.native_figures is not None:
        checked_native_figures(value.native_figures)
        if value.native_figures.source is not value.source:
            raise PdfLayoutError('Native details and layout must own the same checked source')
    catalog = json.loads(value.catalog_json)
    if catalog['raw_sha256'] != value.source.geometry.raw_sha256 or catalog['version'] != VERSION:
        raise PdfLayoutError('Layout catalog differs from its original source')
    return catalog


def prepare_layout(source, raw, *, python_executable, limits=PdfLayoutLimits(),
                   process_policy=PdfProcessPolicy(), cancel_event=None, native_figures=None):
    checked_visual_source(source)
    if native_figures is not None:
        checked_native_figures(native_figures)
        if native_figures.source is not source:
            raise PdfLayoutError('Native details and layout must own the same checked source')
    if (type(raw) is not bytes or len(raw) != source.geometry.raw_bytes
            or sha256(raw).hexdigest() != source.geometry.raw_sha256):
        raise PdfLayoutError('Layout extraction requires the same retained PDF bytes')
    header = canonical({'version':1,'raw_bytes':len(raw),'raw_sha256':source.geometry.raw_sha256,
                        'limits':asdict(limits)}).encode()
    result, _, _, _ = _run_worker(Path(python_executable),
        Path(__file__).resolve().parents[1]/'_document_pdf_layout_worker.py',
        (struct.pack('!I',len(header)),header,raw), policy=process_policy,
        output_bytes=limits.manifest_bytes,cancel_event=cancel_event)
    catalog = json.loads(result)
    document = checked_visual_source(source)
    if (catalog.get('version') != VERSION or catalog.get('raw_sha256') != source.geometry.raw_sha256
            or len(catalog.get('pages',[])) != len(source.pages)):
        raise PdfLayoutError('Layout worker identity differs')
    from PIL import Image
    crops, total_bytes, total_pixels = [], 0, 0
    for metadata, page, geometry in zip(catalog['pages'],source.pages,document.pages,strict=True):
        if (metadata['page'] != page.page or metadata['width'] != geometry.width
                or metadata['height'] != geometry.height):
            raise PdfLayoutError('Layout and rendered page canvases differ')
        with Image.open(BytesIO(page.image.data)) as image:
            for unit in metadata['units']:
                if cancel_event is not None and cancel_event.is_set():
                    from ..document_pdf_process import PdfProcessError
                    raise PdfProcessError('CANCELLED')
                if unit['kind'] == 'table':
                    continue
                box = unit['bbox']
                pixels = (max(0,math.floor(box[0]*page.width/geometry.width)),
                          max(0,math.floor(box[1]*page.height/geometry.height)),
                          min(page.width,math.ceil(box[2]*page.width/geometry.width)),
                          min(page.height,math.ceil(box[3]*page.height/geometry.height)))
                if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
                    raise PdfLayoutError('Empty visual crop')
                unit['crop_pixel_bbox'] = list(pixels)
                unit['crop_dimensions'] = [pixels[2]-pixels[0],pixels[3]-pixels[1]]
                out = BytesIO()
                with image.crop(pixels) as crop:
                    crop.save(out,format='PNG',compress_level=6,optimize=False)
                content = out.getvalue()
                total_bytes += len(content)
                total_pixels += unit['crop_dimensions'][0]*unit['crop_dimensions'][1]
                if (len(content) > source.render_policy.maximum_page_bytes
                        or total_bytes > source.render_policy.maximum_total_bytes
                        or total_pixels > source.render_policy.maximum_total_pixels):
                    raise PdfLayoutError('Complete visual crops exceed their budget')
                unit['crop_sha256'] = sha256(content).hexdigest()
                crops.append(ModelImageInput(unit['unit_id'],'image/png',content))
    encoded = canonical(catalog)
    if len(encoded.encode()) > limits.manifest_bytes:
        raise PdfLayoutError('Complete prepared layout exceeds its budget')
    value = PreparedPdfLayout(source,encoded,tuple(crops),limits,'',native_figures)
    value = replace(value,seal=_seal(value))
    checked_layout(value)
    return value
