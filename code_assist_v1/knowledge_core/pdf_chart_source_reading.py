"""Source-bound chart observations, not printed measurements or authority.

Keep every observed condition and unresolved reading. Caller declarations of
axis scale, subject, metric and legend labels remain explicit, unverified
inputs; hashes bind bytes, not their meaning. No model, database, review loop,
source repair, interpolation or publication is performed in this module.
"""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil, floor
import json
import re

from PIL import Image

from .pdf_chart_axis_calibration import (
    PdfChartAxisCalibrationError, calibrate_linear_axis,
)
from .pdf_chart_ocr_bindings import PdfChartOcrBindings
from . import pdf_chart_rgb_samples as samples


VERSION = 'pdf-chart-source-reading-candidate-v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class PdfChartSourceReadingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfChartSourceReading:
    metadata_json: str

    def identity(self):
        return {'version': VERSION,
            'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest()}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


def _literal(value):
    return (type(value) is str and 0 < len(value) <= 256
        and value.strip() == value
        and all(ord(c) >= 32 for c in value))


def _box(value, size):
    if (type(value) not in (list, tuple) or len(value) != 4
            or any(type(n) is not int for n in value)
            or not 0 <= value[0] < value[2] <= size[0]
            or not 0 <= value[1] < value[3] <= size[1]):
        raise PdfChartSourceReadingError('Exact source pixel rectangle required')
    return list(value)


def _pixel_hash(image, box):
    with image.crop(box) as crop:
        return sha256(crop.tobytes()).hexdigest()


def _legend_color(image, word):
    """Observe dominant chromatic paint immediately left of one source word.

    This source-derived region can contain a line/symbol or unrelated paint.
    It is not a verified legend-to-series assignment; no colors are merged.
    """
    x, y, width, height = word['source_bbox_xywh']
    left, right = max(0, int(x-4*height)), min(image.width, int(x-.8*height))
    top, bottom = int(y), min(image.height, int(y+height)+1)
    if left >= right or top >= bottom:
        return {'status': 'unresolved', 'reason': 'no_source_region_left_of_label',
            'exact_rgb': None, 'binding_verified': False}
    box = [left, top, right, bottom]
    with image.crop(box) as crop:
        counts = crop.getcolors(crop.width*crop.height)
        source_hash = sha256(crop.tobytes()).hexdigest()
    chromatic = sorted(((count, rgb) for count, rgb in counts
        if max(rgb)-min(rgb) >= 12), reverse=True)
    total = sum(count for count, _ in chromatic)
    result = {'status': 'unresolved', 'reason': 'ambiguous_or_missing_chromatic_paint',
        'exact_rgb': None, 'source_pixel_bbox': box,
        'source_pixel_sha256': source_hash, 'chromatic_pixels': total,
        'binding_verified': False}
    if chromatic:
        count, rgb = chromatic[0]
        runner_up = chromatic[1][0] if len(chromatic) > 1 else 0
        if count >= 2 and count*2 >= total and count >= runner_up*2:
            result.update(status='dominant_source_color_candidate', reason=None,
                exact_rgb=list(rgb), dominant_pixel_count=count)
    return result


def read_source_chart(source_png, bindings, *, expected_source_sha256,
                      legend_labels, panel_declarations, maximum_samples=8192,
                      maximum_metadata_bytes=4_194_304):
    """Read source-shape candidates without silently promoting band midpoints.

    ``legend_labels`` contains source_word_id/label_literal/read_basis plus a
    model_response_sha256 only for model-read labels. ``panel_declarations``
    contains panel_id/scale/subject/metric. Unknown declarations remain unknown.
    Every x candidate produces a row for EVERY supplied legend, including
    missing labels, ambiguous colors, failed calibration and occluded paint.
    This is a pure forward record; it grants no workflow/publication authority.
    """
    if (type(source_png) is not bytes or not 0 < len(source_png) <= 16_777_216
            or type(expected_source_sha256) is not str
            or _SHA.fullmatch(expected_source_sha256) is None
            or sha256(source_png).hexdigest() != expected_source_sha256
            or type(bindings) is not PdfChartOcrBindings
            or type(maximum_samples) is not int or not 1 <= maximum_samples <= 8192
            or type(maximum_metadata_bytes) is not int
            or not 1 <= maximum_metadata_bytes <= 4_194_304):
        raise PdfChartSourceReadingError('Bounded original source/bindings required')
    metadata = json.loads(bindings.metadata_json)
    if (metadata.get('input_sha256', {}).get('source_png') != expected_source_sha256
            or type(metadata.get('words')) is not list
            or type(metadata.get('panels')) is not list
            or not 1 <= len(metadata['panels']) <= 64):
        raise PdfChartSourceReadingError('Original source identity and complete geometry required')
    if (type(legend_labels) not in (list, tuple) or not 1 <= len(legend_labels) <= 32
            or type(panel_declarations) not in (list, tuple)):
        raise PdfChartSourceReadingError('Complete explicit label/declaration inventory required')
    words = {word['word_id']: word for word in metadata['words']}
    if len(words) != len(metadata['words']):
        raise PdfChartSourceReadingError('Distinct original source word IDs required')
    declared = {}
    for declaration in panel_declarations:
        if (type(declaration) is not dict
                or set(declaration) != {'panel_id', 'scale', 'subject', 'metric'}
                or declaration['panel_id'] in declared
                or declaration['scale'] not in (None, 'linear', 'log10')
                or any(value is not None and not _literal(value)
                    for value in (declaration['subject'], declaration['metric']))):
            raise PdfChartSourceReadingError('Distinct explicit unverified panel declarations required')
        declared[declaration['panel_id']] = dict(declaration)
    if set(declared) != {panel['panel_id'] for panel in metadata['panels']}:
        raise PdfChartSourceReadingError('Every measured panel needs its own explicit declaration')
    condition_count = sum(anchor['direction'] == 'x' for panel in metadata['panels']
        for anchor in panel['anchors'])
    if condition_count*len(legend_labels) > maximum_samples:
        raise PdfChartSourceReadingError('Complete chart sample inventory exceeds budget')
    with Image.open(BytesIO(source_png)) as image:
        if (image.format != 'PNG' or image.mode != 'RGB'
                or image.width*image.height > 16_000_000
                or metadata.get('source', {}).get('dimensions') != list(image.size)
                or metadata['source'].get('pixel_sha256') != sha256(image.tobytes()).hexdigest()):
            raise PdfChartSourceReadingError('Original RGB source pixels differ')
        legends, seen = [], set()
        for label in legend_labels:
            required = {'source_word_id', 'label_literal', 'read_basis'}
            expected = required | ({'model_response_sha256'}
                if label.get('read_basis') == 'model_source_label' else set()) if type(label) is dict else set()
            if (type(label) is not dict or set(label) != expected
                    or label.get('source_word_id') not in words
                    or label['source_word_id'] in seen or not _literal(label.get('label_literal'))
                    or label['read_basis'] not in ('ocr_literal', 'model_source_label')):
                raise PdfChartSourceReadingError('Exact source label bindings required')
            word = words[label['source_word_id']]
            word_box = word.get('source_bbox_xywh')
            if (type(word_box) not in (list, tuple) or len(word_box) != 4
                    or any(type(n) not in (int, float) for n in word_box)
                    or not 0 <= word_box[0] < word_box[0]+word_box[2] <= image.width
                    or not 0 <= word_box[1] < word_box[1]+word_box[3] <= image.height
                    or word['source_pixel_bbox'] != [floor(word_box[0]), floor(word_box[1]),
                        ceil(word_box[0]+word_box[2]), ceil(word_box[1]+word_box[3])]):
                raise PdfChartSourceReadingError('Original OCR word and pixel bounds differ')
            if ((label['read_basis'] == 'ocr_literal' and label['label_literal'] != word['literal'])
                    or (label['read_basis'] == 'model_source_label'
                        and (type(label['model_response_sha256']) is not str
                            or _SHA.fullmatch(label['model_response_sha256']) is None))
                    or _pixel_hash(image, _box(word['source_pixel_bbox'], image.size)) != word['source_pixel_sha256']):
                raise PdfChartSourceReadingError('Original label reading/pixels differ')
            seen.add(label['source_word_id'])
            legends.append({**label, 'source_ocr_literal': word['literal'],
                'source_word_pixel_bbox': word['source_pixel_bbox'],
                'source_word_pixel_sha256': word['source_pixel_sha256'],
                'paint': _legend_color(image, word), 'label_accuracy_verified': False})
        output = []
        for panel in metadata['panels']:
            axes = _box(panel['axes_pixel_bbox'], image.size)
            declaration = declared[panel['panel_id']]
            for anchor in panel['anchors']:
                if _pixel_hash(image, _box(anchor['source_pixel_bbox'], image.size)) != anchor['source_pixel_sha256']:
                    raise PdfChartSourceReadingError('Original axis anchor pixels differ')
            ticks = [dict(anchor_id=a['anchor_id'], source_coordinate=a['source_coordinate'],
                tick_literal=a['literal']) for a in panel['anchors']
                if a['direction'] == 'y' and a['finite_tick_literal_value'] is not None]
            calibration, calibration_error = None, None
            try:
                calibration = calibrate_linear_axis(ticks, scale=declaration['scale'],
                    coordinate_axis='y', source_dimensions=image.size)
            except PdfChartAxisCalibrationError as error:
                calibration_error = error.reason
            conditions, readings = [], []
            for anchor in panel['anchors']:
                if anchor['direction'] != 'x':
                    continue
                if _pixel_hash(image, _box(anchor['source_pixel_bbox'], image.size)) != anchor['source_pixel_sha256']:
                    raise PdfChartSourceReadingError('Original condition anchor pixels differ')
                conditions.append(dict(anchor))
                for legend in legends:
                    row = {'condition_anchor_id': anchor['anchor_id'],
                        'condition_literal': anchor['literal'],
                        'legend_source_word_id': legend['source_word_id'],
                        'series_label': legend['label_literal'], 'status': 'unresolved',
                        'approximate_value': None, 'shape_observation': None,
                        'reason': 'unresolved_condition_or_legend',
                        'printed_measurement': False, 'numeric_accuracy_verified': False}
                    if (anchor['status'] == 'single_literal_coordinate_candidate'
                            and legend['paint']['exact_rgb'] is not None):
                        shape = samples.observe_circular_rgb_shape(image, axes,
                            anchor['source_coordinate'], legend['paint']['exact_rgb'])
                        row['shape_observation'] = shape
                        row['reason'] = 'source_shape_unresolved'
                        if shape['shape_checks_passed'] and calibration is not None:
                            value = calibration.map_measured_frame_coordinate(
                                shape['candidate_center_pixel'][1], axes)
                            row.update(status='source_shape_approximation_candidate',
                                approximate_value=str(value['value']), reason=None,
                                tick_support_extrapolated=value['tick_support_extrapolated'])
                        elif calibration is None:
                            row['reason'] = 'unresolved_axis_calibration'
                    readings.append(row)
            output.append({'panel_id': panel['panel_id'], 'declaration': declaration,
                'declaration_accuracy_verified': False, 'axes_pixel_bbox': axes,
                'all_observed_anchors': panel['anchors'], 'conditions': conditions,
                'calibration': calibration.report() if calibration is not None else None,
                'calibration_unresolved_reason': calibration_error, 'readings': readings})
        result = {'version': VERSION, 'source_png_sha256': expected_source_sha256,
            'source_dimensions': list(image.size), 'bindings_identity': bindings.identity(),
            'legend_readings': legends, 'panels': output,
            'observed_condition_count': condition_count,
            'sample_count': sum(len(panel['readings']) for panel in output),
            'sample_states': dict(Counter(row['status'] for panel in output for row in panel['readings'])),
            'all_observed_conditions_retained': True, 'missing_values_filled': False,
            'source_text_corrected': False, 'numbers_are_printed_measurements': False,
            'decimal_arithmetic_precision_is_not_measurement_precision': True,
            'numerical_accuracy_verified': False, 'semantic_accuracy_verified': False,
            'series_ownership_verified': False, 'model_calls': 0, 'database_writes': 0,
            'published': False, 'normal_workflow_integrated': False,
            'production_contract_adopted': False}
        encoded = _json(result)
        if len(encoded.encode()) > maximum_metadata_bytes:
            raise PdfChartSourceReadingError('Complete chart reading metadata exceeds budget')
        return PdfChartSourceReading(encoded)
