"""Forward capability: bounded PDF appearance ROI, not native pixels.

Only a uniquely caption-bound figure in an unchanged prepared layout supplies
the rectangle. The original PDF is rendered again in an isolated process;
existing PNGs are never resized. This is not chart interpretation, publication
authority, or permission to bypass the native image decompression limit.
"""
import base64
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
from io import BytesIO
import importlib.metadata
import json
import math
from pathlib import Path
import re
import secrets
import struct

from ..document_pdf_process import _run_worker, PdfProcessError, PdfProcessPolicy
from ..llm_images import ModelImageInput, ModelImagePolicy, validate_image_inputs
from .pdf_layout_source import PreparedPdfLayout, checked_layout
from .pdf_native_figure_source import checked_native_figures
from .pdf_visual_source import checked_visual_source

VERSION = 'pdf-caption-bound-appearance-roi-v1'
_KEY = secrets.token_bytes(32)
_HASH = re.compile(r'[0-9a-f]{64}\Z')


class PdfFigureRoiError(ValueError):
    pass


_INELIGIBLE_REASONS = {
    'unsupported_page_rotation': 'Unsupported ROI page rotation',
    'unsupported_page_coordinate_box': 'Unsupported shifted or cropped ROI page coordinate origin',
}


class PdfFigureRoiIneligible(PdfFigureRoiError):
    """Only an explicitly unsupported coordinate system may retain old input."""

    def __init__(self, reason):
        if reason not in _INELIGIBLE_REASONS:
            raise PdfFigureRoiError('Unknown ROI ineligibility reason')
        self.reason = reason
        super().__init__(_INELIGIBLE_REASONS[reason])


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class PdfFigureRoiLimits:
    source_bytes: int = 67_108_864
    pages: int = 1000
    dpi: int = 288
    image_pixels: int = 16_000_000
    image_bytes: int = 16_777_216
    manifest_bytes: int = 16_384

    def __post_init__(self):
        caps = (67_108_864, 1000, 288, 16_000_000, 16_777_216, 16_384)
        if (any(type(v) is not int or not 1 <= v <= cap for v, cap in zip(asdict(self).values(), caps))
                or self.dpi < 72):
            raise PdfFigureRoiError('Invalid bounded ROI limits')


def _process_policy(policy):
    if (type(policy) is not PdfProcessPolicy or policy.memory_bytes > 512 * 1024 * 1024
            or policy.timeout_seconds > 60 or policy.stderr_bytes > 64 * 1024
            or policy.max_processes > 4 or policy.cleanup_seconds > 5):
        raise PdfFigureRoiError('ROI isolation cannot exceed the existing process limits')


def _placement(width, height, bbox, dpi, limits):
    if (type(limits) is not PdfFigureRoiLimits or type(dpi) is not int or not 72 <= dpi <= limits.dpi
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (width, height))
            or type(bbox) not in (list, tuple) or len(bbox) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in bbox)
            or not 0 <= bbox[0] < bbox[2] <= width or not 0 <= bbox[1] < bbox[3] <= height):
        raise PdfFigureRoiError('Invalid complete figure placement')
    scale = dpi / 72
    canvas = (math.ceil(width * scale), math.ceil(height * scale))
    if max(canvas) > 2_147_483_647:
        raise PdfFigureRoiError('ROI canvas exceeds renderer coordinate range')
    # Outward pixel rounding covers the complete owned rectangle, never a
    # passing prefix or an inward-clipped edge of the original figure.
    box = (math.floor(bbox[0] * scale), math.floor(bbox[1] * scale),
           math.ceil(bbox[2] * scale), math.ceil(bbox[3] * scale))
    dimensions = (box[2] - box[0], box[3] - box[1])
    if min(dimensions) < 1 or math.prod(dimensions) > limits.image_pixels:
        raise PdfFigureRoiError('Complete ROI pixels exceed their budget')
    # PdfPage.render ceil-rounds each crop margin. Choosing a point strictly
    # inside its rounding bin avoids losing an edge through float roundoff.
    margins = (box[0], canvas[1] - box[3], canvas[0] - box[2], box[1])
    crop = tuple(0.0 if n == 0 else (n - .25) / scale for n in margins)
    if tuple(math.ceil(n * scale) for n in crop) != margins:
        raise PdfFigureRoiError('ROI pixel rounding is not representable')
    return {'scale': scale, 'page_pixel_dimensions': list(canvas), 'page_pixel_bbox': list(box),
            'width': dimensions[0], 'height': dimensions[1], 'crop_margins': list(crop)}


def _spec(layout, page, unit_id, dpi, limits):
    catalog = checked_layout(layout)
    document = checked_visual_source(layout.source)
    if (type(page) is not int or not 1 <= page <= len(document.pages)
            or len(document.pages) > limits.pages or type(unit_id) is not str):
        raise PdfFigureRoiError('Unknown checked source figure')
    candidates = [u for p in catalog['pages'] for u in p['units'] if u['unit_id'] == unit_id]
    if len(candidates) != 1:
        raise PdfFigureRoiError('Unknown or repeated checked source figure')
    unit = candidates[0]
    if (unit['page'] != page or unit['kind'] != 'figure' or unit['status'] != 'region'
            or unit['association'] != 'spatial_only' or unit['reason'] != 'preceding_native_image'
            or not isinstance(unit.get('caption_context', unit.get('text')), str)
            or not unit.get('caption_context', unit.get('text', '')).strip()):
        raise PdfFigureRoiError('A checked caption-bound figure region is required')
    same_box = [u for u in catalog['pages'][page - 1]['units']
                if u['kind'] != 'table' and u['bbox'] == unit['bbox']]
    if len(same_box) != 1:
        raise PdfFigureRoiError('Ambiguous figure rectangle is not guessed')
    geometry = document.pages[page - 1]
    placement = _placement(geometry.width, geometry.height, unit['bbox'], dpi, limits)
    return {'version': VERSION, 'raw_bytes': layout.source.geometry.raw_bytes,
            'raw_sha256': layout.source.geometry.raw_sha256, 'page_count': len(document.pages),
            'page': page, 'unit_id': unit_id, 'unit_sha256': sha256(_json(unit).encode()).hexdigest(),
            'layout_catalog_sha256': sha256(layout.catalog_json.encode()).hexdigest(),
            'coordinate_domain': 'zero-origin-uncropped-unrotated-page-canvas',
            'page_dimensions': [geometry.width, geometry.height], 'bbox': list(unit['bbox']),
            'dpi': dpi, 'limits': asdict(limits), **placement}


def _checked_request(request, raw, limits):
    keys = {'version', 'raw_bytes', 'raw_sha256', 'page_count', 'page', 'unit_id', 'unit_sha256',
            'layout_catalog_sha256', 'coordinate_domain', 'page_dimensions', 'bbox', 'dpi', 'limits', 'scale',
            'page_pixel_dimensions', 'page_pixel_bbox', 'width', 'height', 'crop_margins'}
    if (type(request) is not dict or set(request) != keys or type(limits) is not PdfFigureRoiLimits
            or request['version'] != VERSION or request['limits'] != asdict(limits)
            or request['coordinate_domain'] != 'zero-origin-uncropped-unrotated-page-canvas'
            or type(raw) is not bytes or not 0 < len(raw) <= limits.source_bytes
            or type(request['raw_bytes']) is not int or request['raw_bytes'] != len(raw)
            or sha256(raw).hexdigest() != request['raw_sha256']
            or type(request['page_count']) is not int or not 1 <= request['page_count'] <= limits.pages
            or type(request['page']) is not int or not 1 <= request['page'] <= request['page_count']
            or type(request['unit_id']) is not str or not 1 <= len(request['unit_id']) <= 128
            or any(type(request[k]) is not str or not _HASH.fullmatch(request[k])
                   for k in ('raw_sha256', 'unit_sha256', 'layout_catalog_sha256'))
            or type(request['page_dimensions']) is not list or len(request['page_dimensions']) != 2):
        raise PdfFigureRoiError('Invalid checked ROI worker request')
    expected = _placement(*request['page_dimensions'], request['bbox'], request['dpi'], limits)
    if any(request[k] != v for k, v in expected.items()):
        raise PdfFigureRoiError('ROI rounding or geometry changed')


def render_figure_roi(raw, request, *, limits=PdfFigureRoiLimits()):
    """Worker-only direct appearance render; no native bitmap extraction."""
    _checked_request(request, raw, limits)
    import pypdfium2 as pdfium
    import pypdfium2.raw as api
    if (str(pdfium.PYPDFIUM_INFO) != '5.13.0' or str(pdfium.PDFIUM_INFO) != '153.0.7999.0'
            or importlib.metadata.version('Pillow') != '12.3.0'):
        raise PdfFigureRoiError('Unpinned ROI appearance renderer')
    with closing(pdfium.PdfDocument(raw)) as document:
        if api.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            raise PdfFigureRoiError('Encrypted source is not eligible for this ROI lane')
        if len(document) != request['page_count']:
            raise PdfFigureRoiError('ROI document page count changed')
        document.init_forms()
        with closing(document[request['page'] - 1]) as page:
            dimensions = page.get_size()
            if page.get_rotation() != 0:
                raise PdfFigureRoiIneligible('unsupported_page_rotation')
            if any(abs(a-b) > .001 for a, b in zip(dimensions, request['page_dimensions'], strict=True)):
                raise PdfFigureRoiError('ROI page canvas changed')
            # The layout extractor uses MediaBox-relative positions; PDFium
            # rendering re-bases the effective page box. Same dimensions alone
            # cannot prove they share an origin. Do not guess a translation.
            # get_bbox inherits effective boxes, unlike PDFium's individual
            # box getters. Undefined/inherited MediaBox is conservatively
            # unsupported by this new lane rather than guessed from ANSI A.
            boxes = (page.get_mediabox(fallback_ok=False), page.get_cropbox(), page.get_bbox())
            if any(type(box) not in (tuple, list) or len(box) != 4
                   or any(type(v) not in (float, int) or not math.isfinite(v) for v in box)
                   or box[0] != 0 or box[1] != 0
                   or any(abs(a-b) > .001 for a,b in zip(box[2:], dimensions, strict=True))
                   for box in boxes):
                raise PdfFigureRoiIneligible('unsupported_page_coordinate_box')
            with closing(page.render(scale=request['scale'], rotation=0, crop=tuple(request['crop_margins']),
                    fill_color=(255, 255, 255, 255), grayscale=False, rev_byteorder=True,
                    draw_annots=True, may_draw_forms=True, limit_image_cache=True)) as bitmap:
                if (bitmap.width, bitmap.height) != (request['width'], request['height']):
                    raise PdfFigureRoiError('ROI rendered dimensions changed')
                with bitmap.to_pil() as original:
                    with original.convert('RGB') as pixels:
                        pixel_sha = sha256(pixels.tobytes()).hexdigest()
                        output = BytesIO()
                        pixels.save(output, format='PNG', compress_level=6, optimize=False)
                        data = output.getvalue()
    if len(data) > limits.image_bytes:
        raise PdfFigureRoiError('Complete ROI encoded bytes exceed their budget')
    return {'version': VERSION, 'request_sha256': sha256(_json(request).encode()).hexdigest(),
            'width': request['width'], 'height': request['height'], 'pixel_mode': 'RGB',
            'pixel_sha256': pixel_sha, 'encoded_sha256': sha256(data).hexdigest(),
            'encoded_bytes': len(data), 'png': base64.b64encode(data).decode('ascii')}


def _pixel_identity(data, width, height, limits):
    validate_image_inputs((ModelImageInput('roi-validation', 'image/png', data),),
        ModelImagePolicy(1, limits.image_bytes, limits.image_bytes, limits.image_pixels, limits.image_pixels))
    from PIL import Image
    with Image.open(BytesIO(data)) as pixels:
        if pixels.format != 'PNG' or pixels.mode != 'RGB' or pixels.size != (width, height):
            raise PdfFigureRoiError('ROI PNG pixel format or dimensions changed')
        return sha256(pixels.tobytes()).hexdigest()


def _image_id(spec, pixel_sha, encoded_sha):
    return 'PROI_' + sha256(_json([VERSION, spec, pixel_sha, encoded_sha]).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedPdfFigureRoi:
    layout: PreparedPdfLayout = field(repr=False)
    descriptor_json: str = field(repr=False)
    image: ModelImageInput = field(repr=False)
    limits: PdfFigureRoiLimits
    seal: str = field(repr=False)

    def identity(self):
        return {'version': VERSION, 'layout': self.layout.identity(), 'descriptor_json': self.descriptor_json,
                'limits': asdict(self.limits), 'image': {'source_id': self.image.source_id,
                'media_type': self.image.media_type, 'bytes': len(self.image.data),
                'encoded_sha256': sha256(self.image.data).hexdigest()}}


def _seal(value):
    return hmac.digest(_KEY, _json(value.identity()).encode(), 'sha256').hex()


def checked_figure_roi(value):
    if (type(value) is not PreparedPdfFigureRoi or type(value.limits) is not PdfFigureRoiLimits
            or type(value.descriptor_json) is not str or type(value.image) is not ModelImageInput
            or type(value.seal) is not str or not hmac.compare_digest(value.seal, _seal(value))):
        raise PdfFigureRoiError('An unchanged process-local figure ROI is required')
    row = json.loads(value.descriptor_json)
    spec = _spec(value.layout, row['page'], row['unit_id'], row['dpi'], value.limits)
    expected_keys = set(spec) | {'encoded_sha256', 'encoded_bytes', 'pixel_sha256', 'pixel_mode',
        'appearance_relation', 'caption_ownership_proven', 'axes_calibrated', 'semantic_accuracy_verified'}
    if (set(row) != expected_keys or any(row[k] != v for k, v in spec.items())
            or row['appearance_relation'] != 'direct-pdf-static-figure-region-appearance'
            or any(row[k] is not False for k in ('caption_ownership_proven', 'axes_calibrated', 'semantic_accuracy_verified'))
            or row['pixel_mode'] != 'RGB' or row['encoded_bytes'] != len(value.image.data)
            or row['encoded_sha256'] != sha256(value.image.data).hexdigest()
            or row['pixel_sha256'] != _pixel_identity(value.image.data, row['width'], row['height'], value.limits)
            or value.image.media_type != 'image/png'
            or value.image.source_id != _image_id(spec, row['pixel_sha256'], row['encoded_sha256'])):
        raise PdfFigureRoiError('ROI source, geometry or pixels changed')
    return row


def prepare_figure_roi(layout, raw, *, page, unit_id, python_executable, dpi=288,
                       limits=PdfFigureRoiLimits(), process_policy=PdfProcessPolicy(), cancel_event=None):
    """Prepare ONE complete figure, without model/provider/storage side effects.

    Caller authorization is not replaced by this capability. Worker/render
    failures propagate; no low-resolution substitute or smaller prefix is made.
    This module is deliberately not imported by any historical production lane.
    """
    _process_policy(process_policy)
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED')
    if type(limits) is not PdfFigureRoiLimits:
        raise PdfFigureRoiError('Bounded ROI limits are required')
    spec = _spec(layout, page, unit_id, dpi, limits)
    _checked_request(spec, raw, limits)
    header = _json(spec).encode()
    if len(header) > limits.manifest_bytes:
        raise PdfFigureRoiError('Complete ROI request exceeds its manifest budget')
    output, pid, _, _ = _run_worker(Path(python_executable),
        Path(__file__).resolve().parents[1] / '_document_pdf_figure_roi_worker.py',
        (struct.pack('!I', len(header)), header, raw), policy=process_policy,
        output_bytes=4 * ((limits.image_bytes + 2) // 3) + limits.manifest_bytes, cancel_event=cancel_event)
    try:
        def unique(pairs):
            row = {}
            for key, value in pairs:
                if key in row:
                    raise ValueError('Repeated ROI output field')
                row[key] = value
            return row
        def nonfinite(value):
            raise ValueError('Nonfinite ROI output')
        row = json.loads(output, object_pairs_hook=unique, parse_constant=nonfinite)
        if type(row) is dict and row.get('status') == 'ineligible':
            if (set(row) != {'version', 'request_sha256', 'status', 'reason'}
                    or row['version'] != VERSION or row['request_sha256'] != sha256(header).hexdigest()
                    or type(row['reason']) is not str or row['reason'] not in _INELIGIBLE_REASONS):
                raise ValueError('ROI ineligibility response changed')
            if cancel_event is not None and cancel_event.is_set():
                raise PdfProcessError('CANCELLED', pid=pid)
            raise PdfFigureRoiIneligible(row['reason'])
        fields = {'version', 'request_sha256', 'width', 'height', 'pixel_mode', 'pixel_sha256',
                  'encoded_sha256', 'encoded_bytes', 'png'}
        if (type(row) is not dict or set(row) != fields or row['version'] != VERSION
                or row['request_sha256'] != sha256(header).hexdigest()
                or any(type(row[k]) is not int or row[k] != spec[k] for k in ('width', 'height'))
                or row['pixel_mode'] != 'RGB' or type(row['encoded_bytes']) is not int
                or not 0 < row['encoded_bytes'] <= limits.image_bytes or type(row['png']) is not str
                or len(row['png']) > 4 * ((limits.image_bytes + 2) // 3)
                or any(type(row[k]) is not str or not _HASH.fullmatch(row[k])
                       for k in ('encoded_sha256', 'pixel_sha256'))):
            raise ValueError('ROI output identity changed')
        data = base64.b64decode(row['png'], validate=True)
        if len(data) != row['encoded_bytes'] or sha256(data).hexdigest() != row['encoded_sha256']:
            raise ValueError('ROI encoded transport changed')
        descriptor = {**spec, **{k: row[k] for k in ('encoded_sha256', 'encoded_bytes', 'pixel_sha256', 'pixel_mode')},
            'appearance_relation': 'direct-pdf-static-figure-region-appearance',
            'caption_ownership_proven': False, 'axes_calibrated': False, 'semantic_accuracy_verified': False}
        image = ModelImageInput(_image_id(spec, row['pixel_sha256'], row['encoded_sha256']), 'image/png', data)
        value = PreparedPdfFigureRoi(layout, _json(descriptor), image, limits, '')
        value = replace(value, seal=_seal(value))
        checked_figure_roi(value)
    except (PdfFigureRoiIneligible, PdfProcessError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        raise PdfProcessError('FIGURE_ROI_PROTOCOL_ERROR', pid=pid) from error
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED', pid=pid)
    return value


def figure_roi_group_identity(layout, rois):
    """Compact durable descriptor; only fresh private capabilities issue it.

    This public JSON is not admission authority. It records supplied appearance
    pixels, not caption ownership, calibrated axes or correct chart readings.
    """
    checked_layout(layout)
    checked_visual_source(layout.source)
    if type(rois) is not tuple or len(rois) > layout.limits.units:
        raise PdfFigureRoiError('Bounded ROI tuple required')
    rows, unit_ids, source_ids = [], set(), set()
    encoded_bytes = pixels = 0
    limits = PdfFigureRoiLimits()
    dpi = limits.dpi
    for value in rois:
        row = checked_figure_roi(value)
        if (value.layout is not layout or value.limits != limits or row['dpi'] != dpi
                or row['unit_id'] in unit_ids or value.image.source_id in source_ids):
            raise PdfFigureRoiError('ROI group source ownership or unique placement changed')
        unit_ids.add(row['unit_id']); source_ids.add(value.image.source_id)
        encoded_bytes += row['encoded_bytes']; pixels += row['width'] * row['height']
        if (encoded_bytes > layout.source.render_policy.maximum_total_bytes
                or pixels > layout.source.render_policy.maximum_total_pixels):
            raise PdfFigureRoiError('Complete ROI group exceeds existing source render budget')
        rows.append({**{k: row[k] for k in ('page', 'unit_id', 'unit_sha256', 'bbox', 'page_pixel_bbox',
            'width', 'height', 'pixel_sha256', 'encoded_sha256', 'encoded_bytes')},
            'source_id': value.image.source_id,
            'descriptor_sha256': sha256(value.descriptor_json.encode()).hexdigest()})
    if [(r['page'], r['unit_id']) for r in rows] != sorted((r['page'], r['unit_id']) for r in rows):
        raise PdfFigureRoiError('ROI group source order changed')
    return {'version': VERSION, 'source_raw_sha256': layout.source.geometry.raw_sha256,
        'layout_catalog_sha256': sha256(layout.catalog_json.encode()).hexdigest(),
        'dpi': dpi, 'limits': asdict(limits), 'rois': rows}


def prepare_figure_rois(layout, raw, *, python_executable, dpi=288,
                        limits=PdfFigureRoiLimits(), process_policy=PdfProcessPolicy(), cancel_event=None):
    """Forward-only complete appearance group for pixel-limited native images.

    Native resources remain rejected by their own unchanged limit. A distinct
    bounded PDF appearance render may cover their unique caption-bound region.
    Other unavailable/ambiguous resources retain ordinary checked page input.
    Only explicitly unsupported page coordinates are ineligible; cancellation,
    render, protocol and resource failures propagate, without a passing prefix.
    """
    _process_policy(process_policy)
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED')
    catalog = checked_layout(layout)
    checked_visual_source(layout.source)
    if (type(limits) is not PdfFigureRoiLimits or limits != PdfFigureRoiLimits() or dpi != limits.dpi
            or type(dpi) is not int or type(raw) is not bytes
            or len(raw) != layout.source.geometry.raw_bytes or len(raw) > limits.source_bytes
            or sha256(raw).hexdigest() != layout.source.geometry.raw_sha256):
        raise PdfFigureRoiError('Exact bounded group source and policy required')
    native = layout.native_figures
    if native is None:
        return ()
    native_catalog = checked_native_figures(native)
    if native.source is not layout.source:
        raise PdfFigureRoiError('ROI native catalog belongs to another source')
    selected = []
    for page in catalog['pages']:
        native_pages = [p for p in native_catalog['pages'] if p['page'] == page['page']]
        if len(native_pages) != 1:
            continue
        for unit in page['units']:
            if (unit['kind'] != 'figure' or unit['status'] != 'region'
                    or unit['association'] != 'spatial_only' or unit['reason'] != 'preceding_native_image'
                    or not unit.get('caption_context', unit.get('text', '')).strip()
                    or sum(u['kind'] != 'table' and u['bbox'] == unit['bbox'] for u in page['units']) != 1):
                continue
            matches = [i for i in native_pages[0]['images']
                if all(abs(a-b) <= .01 for a,b in zip(i['page_bbox'],unit['bbox'],strict=True))]
            if (len(matches) == 1 and matches[0]['status'] == 'unavailable'
                    and matches[0].get('reason') == 'native_image_pixel_limit'):
                selected.append((unit['page'], unit['unit_id']))
    selected.sort()
    if len(selected) > layout.limits.units or len(set(selected)) != len(selected):
        raise PdfFigureRoiError('ROI group selection exceeds its unique source bound')
    values = []
    for page, unit_id in selected:
        if cancel_event is not None and cancel_event.is_set():
            raise PdfProcessError('CANCELLED')
        try:
            values.append(prepare_figure_roi(layout, raw, page=page, unit_id=unit_id,
                python_executable=python_executable, dpi=dpi, limits=limits,
                process_policy=process_policy, cancel_event=cancel_event))
        except PdfFigureRoiIneligible:
            # Ordinary archived page appearance remains owned and delivered;
            # do not issue an ROI with a guessed translation or rotation.
            continue
        # Complete group limits are checked after each child; an over-budget
        # failure propagates rather than returning an accepted prefix.
        figure_roi_group_identity(layout, tuple(values))
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED')
    return tuple(values)
