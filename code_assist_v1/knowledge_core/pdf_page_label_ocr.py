"""Lossless full-page OCR observations, without label or relationship authority.

Every accepted reader line/word and exact stdout is retained, including
duplicates and contradictory rotations. This does not classify figures,
normalize labels, or certify semantic ownership. No authoring call is made.
"""
from dataclasses import dataclass
import base64
from hashlib import sha256
from io import BytesIO
import math

from PIL import Image

from .pdf_native_chart_input import (
    PdfNativeChartInputLimits, _fail, _json, _LANGUAGE, _report, _SHA, _source,
    read_native_chart_ocr,
)


VERSION = 'pdf-original-page-label-observations-v1'


@dataclass(frozen=True, slots=True)
class PdfPageLabelObservations:
    metadata_json: str
    raw_ocr_stdout: tuple[bytes, ...]


def _box(box, size):
    if (type(box) is not list or len(box) != 4
            or any(type(n) not in (int, float) or type(n) is float and not math.isfinite(n) for n in box)):
        _fail('invalid_page_label_word_bounds')
    x, y, width, height = box
    if not (0 <= x <= size[0] and 0 <= y <= size[1] and 0 < width <= size[0]
            and 0 < height <= size[1] and x+width <= size[0] and y+height <= size[1]):
        _fail('page_label_word_outside_source')
    return box


def _source_box(box, size, rotation):
    x, y, width, height = box
    if rotation == 0:
        return list(box)
    if rotation == 90:
        return [size[0]-y-height, x, height, width]
    return [y, size[1]-x-width, height, width]


def prepare_page_label_ocr(source_png, *, expected_source_sha256, language,
        ocr_reader=None, limits=PdfNativeChartInputLimits(), cancel_event=None):
    """Retain raw 0/90/270-degree OCR of one exact, unscaled RGB source PNG.

    The injected reader has the existing ``read_native_chart_ocr`` signature;
    its existing limits own timeout/worker cleanup. Reports retain every
    accepted top-level auxiliary field. The returned strings/bytes/tuple are
    immutable data, not a publication capability or a semantic proof.
    """
    def check():
        if cancel_event is not None and cancel_event.is_set():
            _fail('cancelled')
    if (type(limits) is not PdfNativeChartInputLimits or type(language) is not str
            or not _LANGUAGE.fullmatch(language) or type(expected_source_sha256) is not str
            or not _SHA.fullmatch(expected_source_sha256)
            or ocr_reader is not None and not callable(ocr_reader)):
        _fail('invalid_page_label_source_language_or_limits')
    check()
    reader = read_native_chart_ocr if ocr_reader is None else ocr_reader
    orientations, transcripts = [], []
    with _source(source_png, limits) as image:
        if sha256(source_png).hexdigest() != expected_source_sha256:
            _fail('page_label_source_identity_mismatch')
        pixels = image.tobytes()
        size = list(image.size)
        for rotation in (0, 90, 270):
            check()
            transform = {0: None, 90: Image.Transpose.ROTATE_90, 270: Image.Transpose.ROTATE_270}[rotation]
            with image.copy() if transform is None else image.transpose(transform) as oriented:
                inverse = {0: None, 90: Image.Transpose.ROTATE_270, 270: Image.Transpose.ROTATE_90}[rotation]
                with oriented.copy() if inverse is None else oriented.transpose(inverse) as restored:
                    if restored.size != image.size or restored.tobytes() != pixels:
                        _fail('page_label_pixel_roundtrip_mismatch')
                if rotation == 0:
                    derived = source_png
                else:
                    with BytesIO() as stream:
                        oriented.save(stream, format='PNG', compress_level=6, optimize=False)
                        derived = stream.getvalue()
                with _source(derived, limits) as encoded:
                    if encoded.size != oriented.size or encoded.tobytes() != oriented.tobytes():
                        _fail('page_label_png_roundtrip_mismatch')
                check()
                derived_sha = sha256(derived).hexdigest()
                raw = reader(derived, language=language, limits=limits, cancel_event=cancel_event)
                check()
                report = _report(raw, derived_sha, oriented.size, language, limits)
                if any(type(n) is not int for n in report['source_dimensions']):
                    _fail('invalid_page_label_report_dimensions')
                source_boxes = []
                for line in report['lines']:
                    check()
                    boxes = []
                    for word in line['words']:
                        check()
                        if type(word['text']) is not str:
                            _fail('invalid_page_label_word_text')
                        box = _box(word['bbox'], oriented.size)
                        boxes.append(_box(_source_box(box, size, rotation), size))
                    source_boxes.append(boxes)
                orientations.append(dict(rotation_degrees_ccw=rotation,
                    derived_png_sha256=derived_sha, derived_pixel_sha256=sha256(oriented.tobytes()).hexdigest(),
                    derived_dimensions=list(oriented.size), raw_ocr_stdout_sha256=sha256(raw).hexdigest(),
                    report=report, source_word_bbox_xywh=source_boxes))
                transcripts.append(raw)
        check()
        metadata = _json(dict(version=VERSION, source_png_sha256=expected_source_sha256,
            source_pixel_sha256=sha256(pixels).hexdigest(), source_dimensions=size,
            language=language, orientations=orientations, source_resized=False, source_clipped=False,
            labels_corrected=False, relationship_ownership_verified=False,
            semantic_accuracy_verified=False, publication_authority=False))
        try:
            metadata_bytes = len(metadata.encode('utf-8'))
        except UnicodeError:
            _fail('invalid_page_label_utf8')
        if metadata_bytes > 3*(limits.ocr_stdout_bytes+limits.words*256+2048):
            _fail('complete_page_label_metadata_budget')
        check()
        return PdfPageLabelObservations(metadata, tuple(transcripts))


def retain_page_label_ocr(observations):
    """Serialize exact prepared evidence; cold restoration owns revalidation."""
    limits = PdfNativeChartInputLimits()
    if (type(observations) is not PdfPageLabelObservations or type(observations.metadata_json) is not str
            or type(observations.raw_ocr_stdout) is not tuple or len(observations.raw_ocr_stdout) != 3
            or any(type(raw) is not bytes or not 0 < len(raw) <= limits.ocr_stdout_bytes
                for raw in observations.raw_ocr_stdout)):
        _fail('invalid_retained_page_label_observations')
    maximum = 3*(limits.ocr_stdout_bytes+limits.words*256+2048)
    try:
        if not 0 < len(observations.metadata_json) <= maximum or len(observations.metadata_json.encode('utf-8')) > maximum:
            _fail('retained_page_label_metadata_budget')
    except UnicodeError:
        _fail('invalid_page_label_utf8')
    return dict(metadata_json=observations.metadata_json,
        raw_ocr_stdout_base64=[base64.b64encode(raw).decode('ascii') for raw in observations.raw_ocr_stdout])


def restore_page_label_ocr(source_png, record, *, expected_source_sha256, language,
        limits=PdfNativeChartInputLimits(), cancel_event=None):
    """Revalidate retained transcripts against exact pixels, with OCR workers=0."""
    if (type(limits) is not PdfNativeChartInputLimits or type(record) is not dict
            or set(record) != {'metadata_json', 'raw_ocr_stdout_base64'}
            or type(record['metadata_json']) is not str or type(record['raw_ocr_stdout_base64']) is not list
            or len(record['raw_ocr_stdout_base64']) != 3):
        _fail('invalid_retained_page_label_record')
    if cancel_event is not None and cancel_event.is_set():
        _fail('cancelled')
    maximum = 3*(limits.ocr_stdout_bytes+limits.words*256+2048)
    try:
        if not 0 < len(record['metadata_json']) <= maximum or len(record['metadata_json'].encode('utf-8')) > maximum:
            _fail('retained_page_label_metadata_budget')
    except UnicodeError:
        _fail('invalid_page_label_utf8')
    transcripts = []
    for encoded in record['raw_ocr_stdout_base64']:
        if cancel_event is not None and cancel_event.is_set():
            _fail('cancelled')
        if type(encoded) is not str or not 0 < len(encoded) <= 4*((limits.ocr_stdout_bytes+2)//3):
            _fail('retained_page_label_stdout_budget')
        try:
            raw = base64.b64decode(encoded.encode('ascii'), validate=True)
        except (ValueError, UnicodeError):
            _fail('invalid_retained_page_label_base64')
        if not 0 < len(raw) <= limits.ocr_stdout_bytes or base64.b64encode(raw).decode('ascii') != encoded:
            _fail('invalid_retained_page_label_base64_or_budget')
        transcripts.append(raw)
    retained = tuple(transcripts)
    replay = iter(retained)
    restored = prepare_page_label_ocr(source_png, expected_source_sha256=expected_source_sha256,
        language=language, limits=limits, cancel_event=cancel_event,
        ocr_reader=lambda png, **kwargs: next(replay))
    if restored.metadata_json != record['metadata_json'] or restored.raw_ocr_stdout != retained:
        _fail('retained_page_label_observations_changed')
    return restored
