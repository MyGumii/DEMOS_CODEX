"""Complete source chart inventory with explicit, conditional geometric bindings.

This companion is NOT a normal authoring path. Exact OCR/caption literals stay
separate from raster estimates. A shared row title, shared legend palette and
linear scale are hypotheses, never semantic ownership or printed measurements.
The caller freshly checks the same-PDF association of every pinned input.
"""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil, floor
from time import monotonic
import json

from PIL import Image

from .pdf_chart_axis_calibration import PdfChartAxisCalibrationError, calibrate_linear_axis
from .pdf_chart_literal_support import PdfChartLiteralSupport
from .pdf_chart_ocr_bindings import PdfChartOcrBindings
from .pdf_chart_retention_markdown import _approximation, _cell, _number
from .pdf_chart_source_reading import _box, _json, _legend_color, _literal, _pixel_hash
from .pdf_chart_visible_marker_fit import fit_visible_rgb_marker
from .pdf_layout_subject_binding import _checked_column, collect_subject_bindings
from .pdf_native_figure_source import _column_partition

VERSION = 'pdf-chart-complete-conditional-reading-v1'
VISIBLE_RANGES_VERSION = 'pdf-chart-complete-visible-color-ranges-v2'
SOURCE_GEOMETRY_INPUT_VERSION = 'pdf-chart-source-thin-components-v1'


class PdfCompleteChartError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfCompleteChartReading:
    metadata_json: str

    def identity(self):
        return {'version': json.loads(self.metadata_json)['version'],
            'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest()}


def _require(ok, reason):
    if not ok:
        raise PdfCompleteChartError(reason)


def _pinned(value, expected, maximum=4_194_304):
    _require(type(value) is str and len(value.encode()) <= maximum
        and type(expected) is str and sha256(value.encode()).hexdigest() == expected,
        'Unchanged bounded input metadata required')
    return json.loads(value)


def _word_pixels(image, word):
    box = word['source_bbox_xywh']
    _require(type(box) is list and len(box) == 4
        and all(type(n) in (int, float) for n in box)
        and 0 <= box[0] < box[0]+box[2] <= image.width
        and 0 <= box[1] < box[1]+box[3] <= image.height,
        'Original word bounds required')
    pixels = [floor(box[0]), floor(box[1]), ceil(box[0]+box[2]), ceil(box[1]+box[3])]
    _require(word['source_pixel_bbox'] == pixels
        and _pixel_hash(image, _box(pixels, image.size)) == word['source_pixel_sha256'],
        'Original word pixels differ')


def _legend_blocks(legends):
    """Conservative adjacency only; an adjacent block is not proven a legend."""
    remaining, blocks = set(range(len(legends))), []
    while remaining:
        block, pending = [], [min(remaining)]
        while pending:
            index = pending.pop()
            if index not in remaining:
                continue
            remaining.remove(index); block.append(index)
            x, y, width, height = legends[index]['source_bbox_xywh']
            for other in sorted(remaining):
                ox, oy, ow, oh = legends[other]['source_bbox_xywh']
                h = max(height, oh)
                if ((abs(x-ox) <= 4*h and max(0, oy-y-height, y-oy-oh) <= 3*h)
                        or (abs(y-oy) <= 2*h and max(0, ox-x-width, x-ox-ow) <= 4*h)):
                    pending.append(other)
        blocks.append(sorted(block))
    return blocks


def _subjects(image, panels, scope, source_object_id, expected_scope_sha256):
    columns = _column_partition({'width': image.width, 'height': image.height, 'panels': panels})
    result = {'columns': [list(box) for box in columns], 'bindings': [],
        'reason': 'missing_checked_native_column_scope', 'association_verified': False}
    if scope is None:
        return result
    _require(type(scope) is dict and sha256(_json(scope).encode()).hexdigest() == expected_scope_sha256
        and type(source_object_id) is str, 'Unchanged original visual scope required')
    bindings = collect_subject_bindings(scope)
    units = [unit for unit in scope['visual_units'] if type(unit) is dict
        and unit.get('native_view_metadata', {}).get('source_object_id') == source_object_id]
    _require(bool(columns) and len(units) == len(columns), 'Complete same-source column coverage required')
    seen = set()
    for unit in units:
        view = _checked_column(unit)
        _require(view is not None and view['count'] == len(columns)
            and view['source_dimensions'] == list(image.size)
            and view['ordinal'] not in seen, 'Complete distinct original columns required')
        seen.add(view['ordinal']); box = columns[view['ordinal']-1]
        _require(view['pixel_bbox'] == list(box)
            and unit['native_view_metadata']['pixel_sha256'] == _pixel_hash(image, box),
            'Original column partition pixels differ')
    selected = [binding for binding in bindings if binding['source_object_id'] == source_object_id]
    if (len(selected) == len(columns) and len({b['caption_sha256'] for b in selected}) == 1
            and {b['ordinal'] for b in selected} == set(range(1, len(columns)+1))):
        result.update(bindings=selected, reason=None)
    else:
        result['reason'] = 'caption_order_unresolved_or_ambiguous'
    return result


def _visible_ranges(row, calibration, axes, dimensions, source_pixel_sha256, rgb, x,
                    *, image=None, check=lambda: None):
    """Map ALL already observed bands; never infer/select a point or midpoint."""
    fit = row['visible_marker_fit']
    if row['status'] != 'unresolved' or fit is None or calibration is None:
        return []
    sample = fit['source_observation']['band_observation']
    bands = sample['bands']; half = sample['column_half_width']; column = floor(x)
    crop = [max(axes[0], column-half), axes[1], min(axes[2], column+half+1), axes[3]]
    _require(type(bands) is list and len(bands) <= 128 and type(sample['band_count']) is int
        and sample['band_count'] == len(bands) and sample['all_matched_bands_returned'] is True
        and type(half) is int and 0 <= half <= 4 and sample['crop_pixel_bbox'] == crop
        and sample['axes_pixel_bbox'] == axes and sample['source_dimensions'] == dimensions
        and sample['source_pixel_sha256'] == source_pixel_sha256
        and sample['exact_rgb'] == list(rgb) and sample['x_source_pixel'] == x,
        'Complete unchanged source band observation required')
    ranges, previous_end, pixels = [], axes[1]-2, 0
    for ordinal, band in enumerate(bands, 1):
        check(); low, high = band['ymin'], band['ymax']
        box = [crop[0], low, crop[2], high+1]
        pin = band['source_rgb_sha256']
        _require(type(low) is int and type(high) is int and previous_end+1 < low <= high < axes[3]
            and band['source_pixel_bbox'] == box and type(pin) is str and len(pin) == 64
            and all(c in '0123456789abcdef' for c in pin)
            and type(band['pixel_count']) is int and 0 < band['pixel_count'] <= (high-low+1)*(crop[2]-crop[0])
            and band['marker_membership_proven'] is False and band['series_membership_proven'] is False,
            'Ordered complete original painted bands required')
        if image is not None:
            _require(_pixel_hash(image, _box(box, image.size)) == pin, 'Original band pixels differ')
        mapped = [calibration.map_measured_frame_coordinate(y, axes) for y in (low, high)]
        ranges.append({'band_ordinal': ordinal, 'source_pixel_bbox': box, 'source_rgb_sha256': pin,
            'exact_rgb_pixel_count': band['pixel_count'], 'ordinate_pixel_range_inclusive': [low, high],
            'visible_color_value_range': [str(value) for value in sorted(v['value'] for v in mapped)],
            'tick_support_extrapolated': any(v['tick_support_extrapolated'] for v in mapped),
            'point_value_unknown': True, 'is_marker_center_interval': False,
            'is_printed_measurement': False, 'accuracy_verified': False,
            'partial_caps_or_lines_may_not_contain_real_center': True})
        previous_end = high; pixels += band['pixel_count']
    _require(pixels == sample['matched_pixel_count'], 'Complete original band pixel count differs')
    return ranges


def _neighbour_marker_window(anchor, conditions, width):
    """Bound an opt-in shape search by observed x geometry, never tick labels."""
    coordinates = [condition['source_coordinate'] for condition in conditions]
    _require(all(type(value) in (int, float) and 0 <= value < width for value in coordinates),
        'Original bounded condition coordinates required')
    distances = [abs(anchor['source_coordinate']-condition['source_coordinate'])
        for condition in conditions if condition['anchor_id'] != anchor['anchor_id']]
    # Keep the historical window for a lone anchor. Distinct anchors at the same
    # position fail closed; do not discard that conflicting original observation.
    return min(40, floor(min(distances)/2)-1) if distances else 40


def read_complete_source_chart(source_png, bindings, literal_support, *,
        expected_source_sha256, expected_bindings_sha256, expected_literal_support_sha256,
        expected_source_pdf_sha256, visual_scope=None, source_object_id=None,
        expected_visual_scope_sha256=None, scale_hypothesis=None, maximum_samples=8192,
        maximum_pixel_tests=64_000_000, maximum_seconds=30, maximum_metadata_bytes=4_194_304,
        cancel_event=None, retain_visible_color_ranges=False, panel_local_metrics=False,
        source_local_legend_paint=False, limit_marker_window_to_neighbours=False,
        allow_thin_component_bands=False):
    """Read every observed condition × discovered raw series once, or fail whole.

    No OCR/model is called. ``visual_scope`` must be an already checked original
    native-column scope, not newly invented captions/metadata. Missing semantic
    bindings remain explicit unknowns, even when geometry yields an estimate.
    The time limit is cooperative between bounded fitter calls, not a hard kill.
    """
    started = monotonic()
    def budget():
        _require(cancel_event is None or not cancel_event.is_set(), 'cancelled')
        _require(monotonic()-started <= maximum_seconds, 'Complete chart time budget exceeded')
    _require(type(source_png) is bytes and 0 < len(source_png) <= 16_777_216
        and sha256(source_png).hexdigest() == expected_source_sha256
        and type(bindings) is PdfChartOcrBindings and type(literal_support) is PdfChartLiteralSupport
        and type(retain_visible_color_ranges) is bool
        and type(panel_local_metrics) is bool
        and type(source_local_legend_paint) is bool
        and type(limit_marker_window_to_neighbours) is bool
        and type(allow_thin_component_bands) is bool
        and (not allow_thin_component_bands or (limit_marker_window_to_neighbours
            and retain_visible_color_ranges))
        and scale_hypothesis in (None, 'linear')
        and all(type(v) is int and 1 <= v <= cap for v, cap in (
            (maximum_samples, 8192), (maximum_pixel_tests, 64_000_000),
            (maximum_seconds, 60), (maximum_metadata_bytes, 4_194_304))), 'Bounded original inputs required')
    budget()
    metadata = _pinned(bindings.metadata_json, expected_bindings_sha256)
    support = _pinned(literal_support.metadata_json, expected_literal_support_sha256)
    original = support['original_reading']
    _require(metadata['input_sha256']['source_png'] == expected_source_sha256
        and original['source_png_sha256'] == expected_source_sha256
        and support['source_pdf_sha256'] == expected_source_pdf_sha256
        and original['raw_mosaic_ocr_stdout_sha256'] == sha256(literal_support.raw_mosaic_ocr_stdout).hexdigest()
        and type(metadata['panels']) is list and 1 <= len(metadata['panels']) <= 64
        and type(metadata['words']) is list and len(metadata['words']) <= 4096
        and type(support['groups']) is list and len(support['groups']) <= 4096,
        'Same-source complete chart inputs required')
    with Image.open(BytesIO(source_png)) as image:
        _require(image.format == 'PNG' and image.mode == 'RGB' and image.width*image.height <= 16_000_000
            and metadata['source']['dimensions'] == original['source_dimensions'] == list(image.size)
            and metadata['source']['pixel_sha256'] == sha256(image.tobytes()).hexdigest(),
            'Original RGB source pixels differ')
        words = {word['word_id']: word for word in metadata['words']}
        _require(len(words) == len(metadata['words']), 'Distinct source words required')
        original_lines = {}
        if source_local_legend_paint:
            for word in words.values():
                budget(); _word_pixels(image, word)
                line_id = word.get('line_id')
                _require(type(line_id) is str and 0 < len(line_id) <= 64,
                    'Original OCR line IDs required for local paint exclusion')
                original_lines.setdefault(line_id, []).append(word)
        candidates, legends = [], []
        ids = metadata['unassigned_word_ids']
        _require(type(ids) is list and len(ids) == len(set(ids)) and set(ids) <= set(words),
            'Complete unassigned source words required')
        for word_id in ids:
            budget(); word = words[word_id]; _word_pixels(image, word)
            paint = _legend_color(image, word)
            if source_local_legend_paint and paint['status'] == 'dominant_source_color_candidate':
                box = paint['source_pixel_bbox']
                crossing = []
                for earlier in original_lines[word['line_id']]:
                    budget(); other = earlier['source_pixel_bbox']
                    if (earlier['word_id'] != word_id and other[2] <= word['source_pixel_bbox'][0]
                            and any(c.isalnum() for c in earlier['literal'])
                            and max(box[0], other[0]) < min(box[2], other[2])
                            and max(box[1], other[1]) < min(box[3], other[3])):
                        crossing.append(earlier['word_id'])
                if crossing:
                    # A scan reaching through an earlier label fragment is not
                    # independent swatch evidence. Keep the entire observation;
                    # do not merge fragments, choose a color or invent a name.
                    paint = dict(status='unresolved',
                        reason='paint_region_crosses_original_line_word',
                        exact_rgb=None, binding_verified=False,
                        crossing_source_word_ids=crossing, original_paint_observation=paint)
            candidates.append({'source_word_id': word_id, 'literal': word['literal'], 'paint': paint})
            if paint['status'] == 'dominant_source_color_candidate' and _literal(word['literal']):
                legends.append({'source_word_id': word_id, 'label_literal': word['literal'],
                    'source_bbox_xywh': word['source_bbox_xywh'], 'source_pixel_bbox': word['source_pixel_bbox'],
                    'source_pixel_sha256': word['source_pixel_sha256'], 'paint': paint,
                    'label_accuracy_verified': False, 'series_ownership_verified': False})
        _require(len(legends) <= 32, 'Complete legend inventory exceeds budget')
        blocks = _legend_blocks(legends)
        colors = Counter(tuple(legend['paint']['exact_rgb']) for legend in legends)
        panels = metadata['panels']
        _require(len({p['panel_id'] for p in panels}) == len(panels), 'Distinct source panels required')
        subjects = _subjects(image, panels, visual_scope, source_object_id, expected_visual_scope_sha256)
        rows = []
        for panel in sorted(panels, key=lambda p: (p['axes_pixel_bbox'][1], p['axes_pixel_bbox'][0])):
            axes = _box(panel['axes_pixel_bbox'], image.size)
            matched = [row for row in rows if abs(row[0]['axes_pixel_bbox'][1]-axes[1]) <= 5
                and abs(row[0]['axes_pixel_bbox'][3]-axes[3]) <= 5]
            if len(matched) == 1:
                matched[0].append(panel)
            else:
                rows.append([panel])
        for group in support['groups']:
            _word_pixels(image, group)
            selected = [candidate['literal'] for candidate in group['candidates'] if candidate['source_match'] is not None]
            _require(group['selected_literal'] == (selected[0] if len(selected) == 1 else None),
                'Literal-support selection differs')
        titles = {}
        for row_index, row in enumerate(rows):
            leftmost = min(row, key=lambda p: p['axes_pixel_bbox'][0])
            left, top, _, bottom = leftmost['axes_pixel_bbox']
            found = [group for group in support['groups']
                if group['source_pixel_bbox'][2] <= left
                and top <= group['source_pixel_bbox'][1] < group['source_pixel_bbox'][3] <= bottom
                and group['source_bbox_xywh'][3] >= 2*group['source_bbox_xywh'][2]
                and any(c.isalpha() for candidate in group['candidates'] for c in candidate['literal'])]
            titles[row_index] = {'source_title_candidates': found,
                'selected_literal': found[0]['selected_literal'] if len(found) == 1 else None,
                'reason': None if len(found) == 1 and found[0]['selected_literal'] is not None else 'missing_or_ambiguous_row_title',
                'source_panel_id': leftmost['panel_id'], 'binding_basis': 'conditional_shared_row_axis_title',
                'metric_ownership_verified': False}
        count = sum(a['direction'] == 'x' for p in panels for a in p['anchors'])
        _require(count*len(legends) <= maximum_samples, 'Complete chart sample inventory exceeds budget')
        output, work = [], 0
        for panel in panels:
            budget(); axes = panel['axes_pixel_bbox']
            for anchor in panel['anchors']:
                _require(_pixel_hash(image, _box(anchor['source_pixel_bbox'], image.size)) == anchor['source_pixel_sha256'],
                    'Original anchor pixels differ')
            row_index = next(index for index, row in enumerate(rows) if panel in row)
            metric = titles[row_index]
            if panel_local_metrics:
                original_crop = _box(panel.get('crop_pixel_bbox'), image.size)
                _require(original_crop[0] <= axes[0] < axes[2] <= original_crop[2]
                    and original_crop[1] <= axes[1] < axes[3] <= original_crop[3],
                    'Original panel crop must contain its measured axes')
                # The atlas already retains the complete inter-frame y-label
                # margin: a midpoint column crop can cut a rotated axis title.
                neighbours = [p['axes_pixel_bbox'][2] for p in panels
                    if p['axes_pixel_bbox'][2] <= axes[0]
                    and min(p['axes_pixel_bbox'][3], axes[3]) > max(p['axes_pixel_bbox'][1], axes[1])]
                label_left = max(neighbours, default=0)
                found = [group for group in support['groups']
                    if label_left <= group['source_pixel_bbox'][0]
                    and group['source_pixel_bbox'][2] <= axes[0]
                    and axes[1] <= group['source_pixel_bbox'][1] < group['source_pixel_bbox'][3] <= axes[3]
                    and group['source_bbox_xywh'][3] >= 2*group['source_bbox_xywh'][2]
                    and any(c.isalpha() for candidate in group['candidates'] for c in candidate['literal'])]
                supported = {candidate['literal'] for group in found for candidate in group['candidates']
                    if candidate['source_match'] is not None}
                selected = next(iter(supported)) if len(supported) == 1 else None
                metric = {'source_title_candidates': found,
                    'selected_literal': selected,
                    'reason': None if selected is not None else 'missing_or_ambiguous_panel_title',
                    'source_panel_id': panel['panel_id'], 'binding_basis': 'conditional_panel_local_axis_title',
                    'metric_ownership_verified': False}
            columns = subjects['columns']
            column = next((i+1 for i, box in enumerate(columns) if box[0] <= axes[0] < axes[2] <= box[2]), None)
            subject = next((b for b in subjects['bindings'] if b['ordinal'] == column), None)
            ticks = [dict(anchor_id=a['anchor_id'], source_coordinate=a['source_coordinate'], tick_literal=a['literal'])
                for a in panel['anchors'] if a['direction'] == 'y' and a['finite_tick_literal_value'] is not None]
            calibration, calibration_reason = None, None
            try:
                calibration = calibrate_linear_axis(ticks, scale=scale_hypothesis, coordinate_axis='y', source_dimensions=image.size)
            except PdfChartAxisCalibrationError as error:
                calibration_reason = error.reason
            with image.crop(axes) as crop:
                palette = {rgb: n for n, rgb in crop.getcolors(crop.width*crop.height)}
            conditions = [dict(a) for a in panel['anchors'] if a['direction'] == 'x']
            _require(len(conditions) <= 256 and len({a['anchor_id'] for a in conditions}) == len(conditions),
                'Distinct complete source conditions required')
            readings = []
            for anchor in conditions:
                window = (_neighbour_marker_window(anchor, conditions, image.width)
                    if limit_marker_window_to_neighbours else 40)
                for legend in legends:
                    budget(); rgb = tuple(legend['paint']['exact_rgb'])
                    observation = {'condition_anchor_id': anchor['anchor_id'], 'condition_literal': anchor['literal'],
                        'legend_source_word_id': legend['source_word_id'], 'series_label': legend['label_literal'],
                        'status': 'unresolved', 'reason': None, 'approximate_value': None, 'value_interval': None,
                        'visible_marker_fit': None, 'printed_measurement': False, 'numeric_accuracy_verified': False,
                        'series_ownership_verified': False, 'shared_palette_assumption': True,
                        'linear_scale_assumption': scale_hypothesis == 'linear', 'target_exact_rgb_pixels': palette.get(rgb, 0)}
                    if len(blocks) != 1:
                        observation['reason'] = 'missing_or_multiple_source_legend_blocks'
                    elif colors[rgb] != 1:
                        observation['reason'] = 'duplicate_source_legend_color'
                    elif not palette.get(rgb):
                        observation['reason'] = 'no_exact_source_palette_in_target_panel'
                    elif anchor['status'] != 'single_literal_coordinate_candidate':
                        observation['reason'] = 'unresolved_source_condition'
                    elif calibration is None:
                        observation['reason'] = 'unresolved_axis_calibration'
                    elif window < 8:
                        observation['reason'] = 'source_condition_spacing_below_bounded_shape_window'
                    else:
                        fit = fit_visible_rgb_marker(image, axes, anchor['source_coordinate'], rgb,
                            allow_fragmented_band=True,
                            **({'shape_window_half_width': window} if limit_marker_window_to_neighbours else {}),
                            **({'allow_thin_component_bands': True} if allow_thin_component_bands else {}))
                        observation['visible_marker_fit'] = fit
                        work += fit['work_pixel_tests']
                        _require(work <= maximum_pixel_tests, 'Complete chart pixel-test budget exceeded')
                        if fit['status'] == 'approximate_source_geometry_candidate':
                            mapped = calibration.map_measured_frame_coordinate(fit['representative_fit']['center_pixel'][1], axes)
                            interval = sorted(calibration.map_measured_frame_coordinate(y, axes)['value']
                                for y in fit['center_interval_pixels'][1])
                            observation.update(status='conditional_source_geometry_estimate', approximate_value=str(mapped['value']),
                                value_interval=[str(value) for value in interval], tick_support_extrapolated=mapped['tick_support_extrapolated'],
                                interval_is_accuracy_guarantee=False)
                        else:
                            observation['reason'] = 'source_marker_geometry_unresolved'
                    if retain_visible_color_ranges and observation['status'] == 'unresolved':
                        observation['point_value_unknown'] = True
                        observation['visible_source_color_ranges'] = _visible_ranges(observation, calibration, axes,
                            list(image.size), metadata['source']['pixel_sha256'], rgb, anchor['source_coordinate'],
                            image=image, check=budget)
                    readings.append(observation)
            output.append({'panel_id': panel['panel_id'], 'axes_pixel_bbox': axes, 'row_ordinal': row_index+1,
                'column_ordinal': column, 'subject_binding': subject, 'subject_unresolved_reason': subjects['reason'],
                'metric_binding': metric, 'conditions': conditions, 'all_observed_anchors': panel['anchors'],
                'scale_hypothesis': scale_hypothesis, 'calibration': calibration.report() if calibration else None,
                'calibration_unresolved_reason': calibration_reason, 'readings': readings})
        record = {'version': VERSION, 'source_png_sha256': expected_source_sha256,
            'source_pdf_sha256': expected_source_pdf_sha256, 'source_dimensions': list(image.size),
            'bindings_identity': bindings.identity(), 'literal_support_identity': literal_support.identity(),
            'visual_scope_sha256': expected_visual_scope_sha256, 'source_object_id': source_object_id,
            'raw_literal_support': support, 'legend_candidate_observations': candidates, 'legend_readings': legends,
            'legend_blocks': blocks, 'legend_inventory_unresolved_reason': None if legends else 'no_source_legend_candidates',
            'subject_inventory': subjects, 'panels': output, 'observed_condition_count': count,
            'sample_count': count*len(legends), 'sample_states': dict(Counter(r['status'] for p in output for r in p['readings'])),
            'work_pixel_tests': work, 'all_observed_conditions_retained': True, 'missing_values_filled': False,
            'source_text_corrected': False, 'numbers_are_printed_measurements': False,
            'semantic_accuracy_verified': False, 'series_ownership_verified': False, 'numerical_accuracy_verified': False,
            'model_calls': 0, 'ocr_calls': 0, 'database_writes': 0, 'published': False, 'normal_workflow_integrated': False}
        if allow_thin_component_bands:
            record['source_geometry_input_version'] = SOURCE_GEOMETRY_INPUT_VERSION
        if panel_local_metrics:
            record['panel_local_metrics'] = True
        if source_local_legend_paint:
            record['source_local_legend_paint'] = True
        if retain_visible_color_ranges:
            unresolved = [row for panel in output for row in panel['readings'] if row['status'] == 'unresolved']
            record.update(version=VISIBLE_RANGES_VERSION, source_pixel_sha256=metadata['source']['pixel_sha256'],
                visible_source_color_range_basis='all_retained_exact_rgb_bands_near_condition',
                point_estimates_recovered_from_ranges=False, unresolved_point_count=len(unresolved),
                unresolved_positions_with_visible_source_color_ranges=sum(bool(row['visible_source_color_ranges']) for row in unresolved),
                visible_source_color_range_count=sum(len(row['visible_source_color_ranges']) for row in unresolved))
        encoded = _json(record)
        _require(len(encoded.encode()) <= maximum_metadata_bytes, 'Complete chart metadata exceeds budget')
        budget()
        return PdfCompleteChartReading(encoded)


def render_complete_source_chart(reading, *, expected_reading_sha256, maximum_markdown_bytes=262_144, cancel_event=None):
    """Render exact literal inventory and visibly conditional raster estimates."""
    def check():
        _require(cancel_event is None or not cancel_event.is_set(), 'cancelled')
    check()
    _require(type(reading) is PdfCompleteChartReading and type(maximum_markdown_bytes) is int
        and 1 <= maximum_markdown_bytes <= 262_144, 'Bounded complete reading required')
    record = _pinned(reading.metadata_json, expected_reading_sha256)
    _require(record['version'] in (VERSION, VISIBLE_RANGES_VERSION) and record['numbers_are_printed_measurements'] is False
        and record['missing_values_filled'] is False and record['all_observed_conditions_retained'] is True,
        'Complete unfilled source inventory required')
    geometry_version = record.get('source_geometry_input_version')
    _require('source_geometry_input_version' not in record or (type(geometry_version) is str
        and geometry_version == SOURCE_GEOMETRY_INPUT_VERSION), 'Explicit supported source geometry required')
    visible_ranges = record['version'] == VISIBLE_RANGES_VERSION
    _require(geometry_version is None or visible_ranges, 'Forward source geometry requires retained ranges')
    if visible_ranges:
        _require(record['visible_source_color_range_basis'] == 'all_retained_exact_rgb_bands_near_condition'
            and record['point_estimates_recovered_from_ranges'] is False, 'Visible ranges cannot recover point estimates')
    legends = record['legend_readings']; legend_ids = [l['source_word_id'] for l in legends]
    _require(len(legend_ids) <= 32 and len(set(legend_ids)) == len(legend_ids), 'Distinct complete legend inventory required')
    eligible = [(candidate['source_word_id'], candidate['literal']) for candidate in record['legend_candidate_observations']
        if candidate['paint']['status'] == 'dominant_source_color_candidate' and _literal(candidate['literal'])]
    _require([(legend['source_word_id'], legend['label_literal']) for legend in legends] == eligible,
        'Complete legends must match retained raw source candidates')
    sections = ['### 원본 그림의 조건별 판독',
        '조건·계열 표기는 원본 OCR 리터럴 그대로이며 OCR 정확도는 검증되지 않았습니다. '
        '제목은 캡션 순서와 같은 행의 축 제목을 연결한 후보입니다. 공통 행·공통 팔레트·선형 축을 가정한 '
        '곡선 값은 **조건부 근삿값**이며 인쇄된 측정값이 아닙니다. 구간은 기하 가설의 휴리스틱 범위로, '
        '신뢰구간이나 정확도 보장이 아닙니다. 판독 불가 값은 채우지 않았습니다.']
    if visible_ranges:
        sections.append('점의 값은 여전히 판독 불가입니다. 추가된 원본색 범위는 해당 조건 근처에서 보이는 '
            '모든 색 띠의 관측 범위이며 마커 중심 구간이 아닙니다. 부분 캡이나 연결선의 범위는 실제 중심을 '
            '포함하지 않을 수 있습니다. 띠를 합치거나 하나를 선택하거나 중간값을 점의 값으로 넣지 않았습니다.')
    total, observed_conditions, panel_ids = 0, 0, set()
    unresolved_count, range_positions, range_count = 0, 0, 0
    for panel in record['panels']:
        check()
        _require(panel['panel_id'] not in panel_ids, 'Distinct original panels required'); panel_ids.add(panel['panel_id'])
        subject = panel['subject_binding']['selected_subject'] if panel['subject_binding'] else None
        metric = panel['metric_binding']['selected_literal']
        if visible_ranges:
            title = _cell(subject) if subject is not None else '행 '+str(panel['row_ordinal'])+' · 열 '+str(panel['column_ordinal'] or '판독 불가')
            sections.append('#### '+title+' · '+_cell(metric)+' (연결 후보)')
        else:
            sections.append('#### '+_cell(panel['panel_id'])+' · '+_cell(subject)+' · '+_cell(metric)+' (연결 후보)')
        conditions = panel['conditions']; ids = [c['anchor_id'] for c in conditions]
        _require(conditions == [anchor for anchor in panel['all_observed_anchors'] if anchor['direction'] == 'x'],
            'Complete conditions must match retained source anchors')
        observed_conditions += len(conditions)
        _require(len(ids) <= 256 and len(set(ids)) == len(ids), 'Distinct complete conditions required')
        calibration = None
        if visible_ranges and panel['calibration'] is not None:
            ticks = [dict(anchor_id=a['anchor_id'], source_coordinate=a['source_coordinate'], tick_literal=a['literal'])
                for a in panel['all_observed_anchors'] if a['direction'] == 'y' and a['finite_tick_literal_value'] is not None]
            calibration = calibrate_linear_axis(ticks, scale=panel['scale_hypothesis'], coordinate_axis='y',
                source_dimensions=record['source_dimensions'])
            _require(calibration.report() == panel['calibration'], 'Original source calibration changed')
        expected = {(c, l) for c in ids for l in legend_ids}; rows = {}
        for row in panel['readings']:
            check()
            key = (row['condition_anchor_id'], row['legend_source_word_id'])
            _require(key in expected and key not in rows and row['printed_measurement'] is False,
                'Exact once-only condition-series positions required')
            if row['status'] == 'unresolved':
                _require(row['approximate_value'] is None and row['value_interval'] is None and bool(row['reason']),
                    'Unresolved positions cannot acquire values')
                if visible_ranges:
                    anchor = next(c for c in conditions if c['anchor_id'] == key[0])
                    legend = next(l for l in legends if l['source_word_id'] == key[1])
                    expected_ranges = _visible_ranges(row, calibration, panel['axes_pixel_bbox'], record['source_dimensions'],
                        record['source_pixel_sha256'], legend['paint']['exact_rgb'], anchor['source_coordinate'], check=check)
                    _require(row['point_value_unknown'] is True and row['visible_source_color_ranges'] == expected_ranges,
                        'All original visible source-color ranges must remain unchanged and ordered')
                    unresolved_count += 1; range_count += len(expected_ranges); range_positions += bool(expected_ranges)
            else:
                fit = row['visible_marker_fit']
                _require(row['status'] == 'conditional_source_geometry_estimate' and row['reason'] is None
                    and row['shared_palette_assumption'] is True and row['linear_scale_assumption'] is True
                    and row['numeric_accuracy_verified'] is False and row['series_ownership_verified'] is False
                    and row['interval_is_accuracy_guarantee'] is False and type(row['tick_support_extrapolated']) is bool
                    and type(fit) is dict and fit['status'] == 'approximate_source_geometry_candidate'
                    and fit['interval_is_accuracy_guarantee'] is False and fit['series_membership_proven'] is False
                    and type(row['value_interval']) is list and len(row['value_interval']) == 2,
                    'Conditional estimate requires retained unverified geometry and assumptions')
                low, high = map(_number, row['value_interval']); value = _number(row['approximate_value'])
                _require(low <= value <= high, 'Estimate must remain within its heuristic interval')
                if geometry_version is not None:
                    _require(calibration is not None, 'Forward estimate requires original axis calibration')
                    mapped = calibration.map_measured_frame_coordinate(
                        fit['representative_fit']['center_pixel'][1], panel['axes_pixel_bbox'])
                    interval = sorted(calibration.map_measured_frame_coordinate(y,
                        panel['axes_pixel_bbox'])['value'] for y in fit['center_interval_pixels'][1])
                    _require(row['approximate_value'] == str(mapped['value'])
                        and row['value_interval'] == [str(v) for v in interval]
                        and row['tick_support_extrapolated'] is mapped['tick_support_extrapolated'],
                        'Forward estimate cannot alter source mapping or erase tick-support uncertainty')
            rows[key] = row
        _require(set(rows) == expected, 'Complete condition-series inventory required'); total += len(rows)
        if not legends:
            sections.append('계열 표기를 판독하지 못했습니다. 관측 조건: '+', '.join(_cell(c['literal']) for c in conditions))
            continue
        if not conditions:
            sections.append('조건 위치를 판독하지 못했습니다. 조건이나 값을 추가하지 않았습니다.')
            continue
        sections.append('| 계열 | '+' | '.join(_cell(c['literal']) for c in conditions)+' |\n| --- | '
            +' | '.join('---' for _ in conditions)+' |')
        for legend in legends:
            cells = []
            for condition in conditions:
                row = rows[(condition['anchor_id'], legend['source_word_id'])]
                if row['status'] == 'unresolved':
                    cell = '점값 미확정' if visible_ranges else '판독 불가 ('+_cell(row['reason'])+')'
                    if visible_ranges:
                        for band in row['visible_source_color_ranges']:
                            span = '–'.join(_approximation(dict(approximate_value=v), panel['calibration']).removeprefix('약 ')
                                for v in band['visible_color_value_range'])
                            cell += '; 관측색 범위 약 '+span+' (띠 '+str(band['band_ordinal'])+')'
                            if band['tick_support_extrapolated']:
                                cell += ' [눈금 지지 범위 밖]'
                    cells.append(cell)
                else:
                    interval = '–'.join(_approximation(dict(approximate_value=v), panel['calibration']).removeprefix('약 ')
                        for v in row['value_interval'])
                    cells.append('조건부 '+_approximation(row, panel['calibration'])+' (휴리스틱 '+interval+')'
                        + (' [눈금 지지 범위 밖]' if row['tick_support_extrapolated'] else ''))
            table_row = '| '+_cell(legend['label_literal'])+' | '+' | '.join(cells)+' |'
            if visible_ranges:
                sections[-1] += '\n'+table_row
            else:
                sections.append(table_row)
    _require(observed_conditions == record['observed_condition_count'] and total == record['sample_count'],
        'Complete source condition or sample count changed')
    if visible_ranges:
        _require((unresolved_count, range_positions, range_count) == (record['unresolved_point_count'],
            record['unresolved_positions_with_visible_source_color_ranges'], record['visible_source_color_range_count']),
            'Complete unresolved point or visible range counts changed')
    markdown = '\n\n'.join(sections)+'\n'
    _require(len(markdown.encode()) <= maximum_markdown_bytes, 'Complete chart Markdown exceeds budget')
    check()
    return markdown
