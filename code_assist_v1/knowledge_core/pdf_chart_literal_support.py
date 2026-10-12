"""Exact source-text support for competing OCR literals, not metric ownership.

The caller supplies freshly checked complete same-PDF page text and appearance
association. Hashes identify inputs; this pure helper cannot prove that relation.
No normalization, dictionary, OCR correction, row propagation or model is used.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata

from .pdf_chart_rotated_y_ocr import PdfRotatedYOcr


class PdfChartLiteralSupportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfChartLiteralSupport:
    metadata_json: str
    raw_mosaic_ocr_stdout: bytes

    def identity(self):
        return {'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest(),
                'raw_mosaic_ocr_stdout_sha256': sha256(self.raw_mosaic_ocr_stdout).hexdigest()}


def _word_character(char):
    return char == '_' or char.isalnum() or unicodedata.category(char).startswith('M')


def resolve_rotated_literal_support(reading, complete_source_pages, *, source_pdf_sha256,
        expected_page_count, expected_reading_sha256, maximum_text_bytes=8_388_608,
        maximum_work=32_000_000, maximum_result_bytes=4_194_304, cancel_event=None):
    """Retain every observation; select only one distinct supported literal.

    Page offsets are Unicode code points in the supplied unchanged extracted
    text. One exact occurrence suffices as evidence; occurrence counts are not
    claimed. Absence from extracted text is not absence from source pixels.
    """
    def check(value, reason):
        if not value:
            raise PdfChartLiteralSupportError(reason)
    def cancelled():
        check(cancel_event is None or not cancel_event.is_set(), 'cancelled')
    cancelled()
    check(all(type(v) is int and 1 <= v <= cap for v, cap in
        ((maximum_text_bytes, 8_388_608), (maximum_work, 32_000_000),
         (maximum_result_bytes, 4_194_304))), 'Invalid complete limits')
    check(type(reading) is PdfRotatedYOcr and type(reading.metadata_json) is str
        and len(reading.metadata_json.encode()) <= 4_194_304
        and type(reading.raw_mosaic_ocr_stdout) is bytes
        and len(reading.raw_mosaic_ocr_stdout) <= 1_048_576
        and type(expected_reading_sha256) is str
        and sha256(reading.metadata_json.encode()).hexdigest() == expected_reading_sha256,
        'Unchanged complete rotated reading required')
    check(type(source_pdf_sha256) is str and re.fullmatch('[0-9a-f]{64}', source_pdf_sha256)
        and type(expected_page_count) is int and 1 <= expected_page_count <= 4096
        and type(complete_source_pages) is tuple and len(complete_source_pages) == expected_page_count
        and all(type(text) is str for text in complete_source_pages), 'Complete ordered source pages required')
    check(sum(len(text.encode()) for text in complete_source_pages) <= maximum_text_bytes,
          'Complete source text exceeds budget')
    try:
        original = json.loads(reading.metadata_json)
    except (ValueError, RecursionError) as error:
        raise PdfChartLiteralSupportError('Invalid complete rotated metadata') from error
    check(type(original) is dict and original.get('raw_mosaic_ocr_stdout_sha256') ==
        sha256(reading.raw_mosaic_ocr_stdout).hexdigest(), 'Raw derived-input transcript identity differs')
    rows = original.get('observations')
    check(type(rows) is list and len(rows) <= 4096, 'Complete observation budget exceeded')
    groups, unknown = {}, []
    for index, row in enumerate(rows):
        cancelled()
        check(type(row) is dict and type(row.get('literal')) is str and len(row['literal']) <= 256,
              'Literal observation required')
        if row.get('status') != 'source_mapped_literal_candidate':
            unknown.append(index)
            continue
        box, pixels, pin = row.get('source_bbox_xywh'), row.get('source_pixel_bbox'), row.get('source_pixel_sha256')
        check(type(box) is list and len(box) == 4 and all(type(v) in (int, float) and 0 <= v <= 16384 for v in box)
            and box[2] > 0 and box[3] > 0 and type(pixels) is list and len(pixels) == 4
            and all(type(v) is int and 0 <= v <= 16384 for v in pixels)
            and pixels[0] < pixels[2] and pixels[1] < pixels[3]
            and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin), 'Exact original bbox/pixel identity required')
        group = groups.setdefault((tuple(box), tuple(pixels), pin), {})
        group.setdefault(row['literal'], []).append(index)
    literals = {literal for group in groups.values() for literal in group}
    check(len(literals)*sum(len(text) for text in complete_source_pages) <= maximum_work,
          'Complete literal matching work exceeds budget')
    support = {}
    for literal in literals:
        support[literal] = None
        for page, text in enumerate(complete_source_pages, 1):
            cancelled()
            start = text.find(literal) if literal else -1
            while start >= 0:
                cancelled()
                end = start+len(literal)
                if ((start == 0 or not _word_character(text[start-1]))
                        and (end == len(text) or not _word_character(text[end]))):
                    support[literal] = {'page': page, 'start': start, 'end': end, 'literal': literal}
                    break
                start = text.find(literal, start+1)
            if support[literal] is not None:
                break
    resolved = []
    for (box, pixels, pin), candidates in groups.items():
        supported = [literal for literal in candidates if support[literal] is not None]
        resolved.append({'source_bbox_xywh': list(box), 'source_pixel_bbox': list(pixels),
            'source_pixel_sha256': pin, 'candidates': [{'literal': literal,
                'observation_indices': indices, 'source_match': support[literal]} for literal, indices in candidates.items()],
            'selected_literal': supported[0] if len(supported) == 1 else None,
            'status': 'unique_supported_literal_candidate' if len(supported) == 1 else 'unresolved',
            'reason': None if len(supported) == 1 else ('no_source_support' if not supported else 'multiple_supported_literals')})
    result = {'source_pdf_sha256': source_pdf_sha256, 'source_pages': [{'page': n,
        'characters': len(text), 'text_sha256': sha256(text.encode()).hexdigest()} for n, text in enumerate(complete_source_pages, 1)],
        'rotated_reading_identity': reading.identity(), 'original_reading': original,
        'groups': resolved, 'unmapped_observation_indices': unknown,
        'source_occurrences_exhaustive': False, 'ocr_corrected': False,
        'source_association_verified': False, 'metric_ownership_verified': False,
        'semantic_accuracy_verified': False, 'row_propagation_performed': False,
        'model_calls': 0, 'ocr_calls': 0, 'database_calls': 0, 'publication_authority': False}
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    check(len(encoded.encode()) <= maximum_result_bytes, 'Complete support result exceeds budget')
    cancelled()
    return PdfChartLiteralSupport(encoded, reading.raw_mosaic_ocr_stdout)
