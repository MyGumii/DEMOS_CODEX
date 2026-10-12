"""Isolated source-pixel bands for one supplied RGB and x coordinate.

The caller supplies an original RGB Pillow image and a measured, integer,
half-open axes rectangle. A fractional x selects its containing pixel column
(floor(x)); only that column +/- the supplied half-width is read, clipped to
the axes rectangle. Every consecutive row containing the exact RGB is kept.
No averaging, noise removal, interpolation, color merging or band selection
occurs. A band may be a line, marker, legend, axis or other painted graphic.

Hashes describe raw row-major RGB bytes, not encoded files. Band rectangles
cover the full sample width and their occupied row interval, not a segmented
object. None of these observations proves chart semantics or source ownership.
This prototype is not connected to an existing authoring workflow.
"""
from dataclasses import dataclass
from collections import deque
from hashlib import sha256
import json
import math

from PIL import Image


VERSION = 'pdf-chart-rgb-samples-prototype-v1'
_PAINT_AMBIGUITY = 'painted_pixels_may_be_line_marker_legend_axis_or_other_graphic'


class PdfChartRgbSampleError(ValueError):
    pass


class PdfChartRgbSampleLimitError(PdfChartRgbSampleError):
    pass


@dataclass(frozen=True, slots=True)
class PdfChartRgbSampleLimits:
    maximum_source_pixels: int = 16_000_000
    maximum_source_rgb_bytes: int = 48_000_000
    maximum_dimension: int = 16_384
    maximum_crop_bytes: int = 442_368  # 9 columns * 16384 rows * RGB
    maximum_bands: int = 128
    maximum_result_bytes: int = 65_536

    def __post_init__(self):
        ceilings = {
            'maximum_source_pixels': 16_000_000,
            'maximum_source_rgb_bytes': 48_000_000,
            'maximum_dimension': 16_384,
            'maximum_crop_bytes': 442_368,
            'maximum_bands': 128,
            'maximum_result_bytes': 65_536,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise PdfChartRgbSampleError('Invalid bounded RGB-sample limits')


def _checked_inputs(source, axes_pixel_bbox, x_source_pixel, exact_rgb,
                    column_half_width, limits):
    if type(limits) is not PdfChartRgbSampleLimits:
        raise PdfChartRgbSampleError('Exact RGB-sample limits required')
    if (not isinstance(source, Image.Image) or source.mode != 'RGB'
            or getattr(source, 'n_frames', 1) != 1):
        raise PdfChartRgbSampleError('An original single-frame RGB Pillow image is required')
    width, height = source.size
    if (width <= 0 or height <= 0 or max(width, height) > limits.maximum_dimension
            or width * height > limits.maximum_source_pixels
            or width * height * 3 > limits.maximum_source_rgb_bytes):
        raise PdfChartRgbSampleLimitError('Source RGB pixel, byte or dimension limit exceeded')
    if (type(axes_pixel_bbox) not in (list, tuple) or len(axes_pixel_bbox) != 4
            or any(type(edge) is not int for edge in axes_pixel_bbox)):
        raise PdfChartRgbSampleError('Measured axes bbox must have four integer pixel edges')
    left, top, right, bottom = axes_pixel_bbox
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise PdfChartRgbSampleError('Measured axes bbox must be nonempty and inside the source')
    if (type(x_source_pixel) not in (int, float)
            or (type(x_source_pixel) is float and not math.isfinite(x_source_pixel))
            or not left <= x_source_pixel < right):
        raise PdfChartRgbSampleError('Finite source x must lie inside the measured axes bbox')
    if (type(exact_rgb) not in (list, tuple) or len(exact_rgb) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255
                   for channel in exact_rgb)):
        raise PdfChartRgbSampleError('Exact RGB must have three integer channels in 0..255')
    if type(column_half_width) is not int or not 0 <= column_half_width <= 4:
        raise PdfChartRgbSampleError('Column half-width must be an integer in 0..4')
    column = math.floor(x_source_pixel)
    crop_bbox = (max(left, column - column_half_width), top,
                 min(right, column + column_half_width + 1), bottom)
    crop_bytes = (crop_bbox[2] - crop_bbox[0]) * (bottom - top) * 3
    if crop_bytes > limits.maximum_crop_bytes:
        raise PdfChartRgbSampleLimitError('Source crop byte limit exceeded')
    return column, crop_bbox


def sample_chart_rgb_bands(source, axes_pixel_bbox, x_source_pixel, exact_rgb,
                           *, column_half_width=2, limits=PdfChartRgbSampleLimits()):
    """Return ALL exact-color y bands, with inclusive ymin/ymax pixel indices.

    ``ycenter`` is (ymin + ymax) / 2, not a weighted centroid or data value.
    Zero bands is unresolved; multiple bands is ambiguous without selecting
    the largest or pretending they belong to one series. Even a single band
    is only a painted-band candidate. A limit failure returns no partial data.
    The image is neither modified nor converted/resized.
    """
    column, crop_bbox = _checked_inputs(source, axes_pixel_bbox, x_source_pixel,
                                       exact_rgb, column_half_width, limits)
    left, top, right, bottom = crop_bbox
    crop_width, crop_height = right - left, bottom - top
    with source.crop(crop_bbox) as crop:
        raw = crop.tobytes()
    stride = crop_width * 3
    if len(raw) != stride * crop_height:
        raise PdfChartRgbSampleError('Source crop RGB length differs from measured dimensions')
    target = bytes(exact_rgb)
    bands, active_start, active_count = [], None, 0
    matched_pixels = 0

    def finish(end_row):
        if len(bands) >= limits.maximum_bands:
            raise PdfChartRgbSampleLimitError('Painted band count limit exceeded')
        ymin, ymax = top + active_start, top + end_row
        band_bytes = raw[active_start * stride:(end_row + 1) * stride]
        bands.append({
            'status': 'painted_band_candidate',
            'ymin': ymin, 'ymax': ymax, 'ycenter': (ymin + ymax) / 2,
            'source_pixel_bbox': [left, ymin, right, ymax + 1],
            'source_rgb_sha256': sha256(band_bytes).hexdigest(),
            'pixel_count': active_count,
            'marker_membership_proven': False, 'series_membership_proven': False,
        })

    # Only this at-most-nine-column crop is traversed in Python. Source-image
    # hashing below uses Pillow's bounded native byte copy, not a pixel loop.
    for row in range(crop_height):
        start = row * stride
        count = sum(raw[offset:offset + 3] == target
                    for offset in range(start, start + stride, 3))
        matched_pixels += count
        if count:
            if active_start is None:
                active_start = row
            active_count += count
        elif active_start is not None:
            finish(row - 1)
            active_start, active_count = None, 0
    if active_start is not None:
        finish(crop_height - 1)

    source_bytes = source.tobytes()
    if len(source_bytes) != source.width * source.height * 3:
        raise PdfChartRgbSampleError('Source RGB length differs from source dimensions')
    source_hash = sha256(source_bytes).hexdigest()
    del source_bytes
    status = ('unresolved' if not bands else 'painted_band_candidate'
              if len(bands) == 1 else 'ambiguous')
    ambiguities = [_PAINT_AMBIGUITY] if bands else []
    if len(bands) > 1:
        ambiguities.append('multiple_painted_bands_no_band_selected')
    result = {
        'version': VERSION, 'status': status,
        'source_dimensions': [source.width, source.height],
        'source_pixel_sha256': source_hash, 'hash_basis': 'raw_row_major_rgb_bytes',
        'axes_pixel_bbox': list(axes_pixel_bbox),
        'x_source_pixel': x_source_pixel, 'sample_column_index': column,
        'column_half_width': column_half_width, 'exact_rgb': list(exact_rgb),
        'crop_pixel_bbox': list(crop_bbox), 'crop_dimensions': [crop_width, crop_height],
        'crop_pixel_sha256': sha256(raw).hexdigest(),
        'sample_width_clipped': crop_width != 2 * column_half_width + 1,
        'matched_pixel_count': matched_pixels, 'band_count': len(bands), 'bands': bands,
        'ambiguities': ambiguities,
        'unresolved_reason': 'no_exact_rgb_pixels_in_sample_crop' if not bands else None,
        'all_matched_bands_returned': True,
        'axes_calibrated': False, 'numeric_axis_values_produced': False,
        'labels_produced': False, 'marker_membership_proven': False,
        'series_membership_proven': False, 'semantic_accuracy_verified': False,
    }
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')) > limits.maximum_result_bytes:
        raise PdfChartRgbSampleLimitError('RGB-sample result metadata limit exceeded')
    return result


def observe_circular_rgb_shape(source, axes_pixel_bbox, x_source_pixel, exact_rgb,
                               *, column_half_width=2, shape_window_half_width=40,
                               limits=PdfChartRgbSampleLimits()):
    """Optional fail-closed 2D shape observation; never restore hidden centers.

    The existing band API is unchanged. This function accepts only one fully
    observed, isolated exact-RGB component with bounded, nearly circular,
    contiguous and symmetric row/column profiles. Its center is the midpoint
    of OBSERVED component bounds, never a fitted circle or an occluded center.

    Connected curve lines, antialias gaps and occlusion can reject a real marker.
    A complete circular legend symbol can pass the shape checks: appearance
    cannot establish marker/series ownership. No point values are produced.
    """
    if (type(shape_window_half_width) is not int
            or not 8 <= shape_window_half_width <= 64):
        raise PdfChartRgbSampleError('Shape window half-width must be an integer in 8..64')
    observation = sample_chart_rgb_bands(
        source, axes_pixel_bbox, x_source_pixel, exact_rgb,
        column_half_width=column_half_width, limits=limits)
    result = {
        'version': 'pdf-circular-rgb-shape-prototype-v1',
        'status': 'unresolved', 'candidate_center_pixel': None,
        'shape_checks_passed': False, 'reasons': [],
        'band_observation': observation,
        'shape_pixel_bbox': None, 'shape_pixel_sha256': None,
        'observed_component_bbox': None, 'observed_component_pixels': 0,
        'observed_component_count': None,
        'row_pixel_counts': [], 'column_pixel_counts': [],
        'reflection_missing_fraction': None,
        'ambiguities': ['circular_paint_may_be_legend_or_other_graphic'],
        'hidden_center_reconstructed': False, 'circle_fit_used': False,
        'marker_membership_proven': False, 'series_membership_proven': False,
        'semantic_accuracy_verified': False, 'axes_calibrated': False,
        'numeric_axis_values_produced': False,
    }

    def checked_result():
        if len(json.dumps(result, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')) > limits.maximum_result_bytes:
            raise PdfChartRgbSampleLimitError('Circular-shape result metadata limit exceeded')
        return result

    if observation['band_count'] != 1:
        result['reasons'].append('exactly_one_painted_band_required_without_selection')
        return checked_result()
    band = observation['bands'][0]
    if band['ymax'] - band['ymin'] + 1 > 65:
        result['reasons'].append('observed_band_exceeds_shape_dimension_limit')
        return checked_result()
    left, top, right, bottom = axes_pixel_bbox
    column, pad = observation['sample_column_index'], shape_window_half_width
    box = (max(left, column - pad), max(top, band['ymin'] - pad),
           min(right, column + pad + 1), min(bottom, band['ymax'] + pad + 1))
    with source.crop(box) as crop:
        raw = crop.tobytes()
        width, height = crop.size
    # At most 129 * 193 RGB pixels: no source-sized Python pixel traversal.
    if width * height > 24_897:
        raise PdfChartRgbSampleLimitError('Circular-shape crop work limit exceeded')
    result['shape_pixel_bbox'] = list(box)
    result['shape_pixel_sha256'] = sha256(raw).hexdigest()
    target, stride = bytes(exact_rgb), width * 3
    painted = {(x, y) for y in range(height) for x in range(width)
               if raw[y * stride + x * 3:y * stride + x * 3 + 3] == target}
    strip_left, _, strip_right, _ = observation['crop_pixel_bbox']
    seeds = {(x, y) for x, y in painted
             if strip_left <= box[0] + x < strip_right
             and band['ymin'] <= box[1] + y <= band['ymax']}
    remaining, components = set(painted), []
    while remaining:
        seed = remaining.pop()
        queue, component = deque([seed]), {seed}
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbor = (x + dx, y + dy)
                    if (dx or dy) and neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        queue.append(neighbor)
        if component & seeds:
            components.append(component)
    result['observed_component_count'] = len(components)
    if len(components) != 1:
        result['reasons'].append('painted_strip_has_multiple_or_missing_components')
        return checked_result()
    component = components[0]
    xmin, xmax = min(x for x, _ in component), max(x for x, _ in component)
    ymin, ymax = min(y for _, y in component), max(y for _, y in component)
    shape_width, shape_height = xmax - xmin + 1, ymax - ymin + 1
    result['observed_component_bbox'] = [box[0] + xmin, box[1] + ymin,
                                          box[0] + xmax + 1, box[1] + ymax + 1]
    result['observed_component_pixels'] = len(component)
    if xmin < 2 or ymin < 2 or xmax >= width - 2 or ymax >= height - 2:
        result['reasons'].append('component_or_connected_paint_reaches_crop_or_axes_margin')
    if (not 7 <= shape_width <= 65 or not 7 <= shape_height <= 65
            or abs(shape_width - shape_height) > 1):
        result['reasons'].append('complete_nearly_equal_observed_width_and_height_required')
    # Only bounded observed extents are considered below, never guessed radii.
    if shape_width > 65 or shape_height > 65:
        return checked_result()
    rows = [sorted(x for x, cy in component if cy == y) for y in range(ymin, ymax + 1)]
    columns = [sorted(y for cx, y in component if cx == x) for x in range(xmin, xmax + 1)]
    result['row_pixel_counts'] = [len(row) for row in rows]
    result['column_pixel_counts'] = [len(col) for col in columns]
    if any(not line or len(line) != line[-1] - line[0] + 1 for line in rows + columns):
        result['reasons'].append('interior_missing_exact_color_or_occlusion')
    fill = len(component) / (shape_width * shape_height)
    if not .70 <= fill <= .88:
        result['reasons'].append('observed_fill_is_not_a_complete_circular_paint_candidate')
    missing_x = sum((xmin + xmax - x, y) not in component for x, y in component)
    missing_y = sum((x, ymin + ymax - y) not in component for x, y in component)
    reflection_missing = max(missing_x, missing_y) / len(component)
    result['reflection_missing_fraction'] = reflection_missing
    if reflection_missing > .025:
        result['reasons'].append('observed_paint_is_not_sufficiently_symmetric')
    for profile in (result['row_pixel_counts'], result['column_pixel_counts']):
        midpoint = (len(profile) - 1) // 2
        if (any(profile[i] > profile[i + 1] + 1 for i in range(midpoint))
                or any(profile[i] + 1 < profile[i + 1]
                       for i in range((len(profile) + 1) // 2, len(profile) - 1))
                or max(profile[0], profile[-1]) > max(profile) / 2):
            result['reasons'].append('observed_profiles_do_not_have_complete_round_caps')
            break
    center = [box[0] + (xmin + xmax) / 2, box[1] + (ymin + ymax) / 2]
    if abs(center[0] - x_source_pixel) > 1:
        result['reasons'].append('observed_shape_center_does_not_match_supplied_x')
    if not result['reasons']:
        result['status'] = 'complete_circular_paint_candidate'
        result['candidate_center_pixel'] = center
        result['shape_checks_passed'] = True
    return checked_result()


def observe_rgb_marker_cross_sections(source, axes_pixel_bbox, x_source_pixel, exact_rgb,
                                      *, shape_window_half_width=40,
                                      allow_fragmented_band=False,
                                      limits=PdfChartRgbSampleLimits()):
    """Observe full-diameter-like source sections, not a complete marker.

    The unchanged five-column band midpoint is only a provisional ordinate.
    Integer rows bracketing it and five central columns are measured directly.
    Caps and adjacent-column runs must also be observed, aligned and tapered.
    All exact-RGB row runs in the bounded crop are retained, including attached
    lines; nothing is erased, fitted, filled, reconstructed or selected by size.
    Matching sections can still belong to a legend or another noncircular
    graphic. Neither marker ownership nor the unobserved whole shape is proved.

    The explicit opt-in permits a bounded 2D OBSERVATION around the envelope of
    ALL two or three sample bands, only when each intervening gap is one row
    and the inclusive envelope is at most 65 rows. Original bands and gaps are
    retained, never filled or selected. The envelope cannot supply a center;
    a caller fitting geometry must independently check the observed 2D paint.
    """
    if type(allow_fragmented_band) is not bool:
        raise PdfChartRgbSampleError('Fragmented-band observation flag must be a bool')
    if (type(shape_window_half_width) is not int
            or not 8 <= shape_window_half_width <= 64):
        raise PdfChartRgbSampleError('Shape window half-width must be an integer in 8..64')
    observation = sample_chart_rgb_bands(source, axes_pixel_bbox, x_source_pixel,
                                         exact_rgb, limits=limits)
    result = {
        'version': VERSION, 'observation_kind': 'marker_cross_sections',
        'status': 'unresolved', 'candidate_ordinate_pixel': None,
        'cross_section_checks_passed': False, 'reasons': [],
        'band_observation': observation, 'band_midpoint_pixel': None,
        'shape_pixel_bbox': None, 'shape_pixel_sha256': None,
        'shape_exact_rgb_pixel_count': 0, 'row_exact_rgb_runs': [],
        'middle_row_sections': [], 'central_column_sections': [],
        'cap_row_sections': [], 'adjacent_column_pairs': [],
        'observed_diameter_pixel_bbox': None,
        'observed_bbox_basis': 'envelope_of_observed_cross_sections_not_object_bounds',
        'all_shape_exact_rgb_runs_returned': False,
        'ambiguities': ['cross_sections_may_be_legend_or_other_graphic',
                        'matching_sections_do_not_prove_the_complete_shape'],
        'connected_paint_removed': False, 'hidden_center_reconstructed': False,
        'circle_fit_used': False, 'marker_center_proven': False,
        'complete_circular_shape_proven': False,
        'candidate_ordinate_is_only_band_midpoint': True,
        'marker_membership_proven': False, 'series_membership_proven': False,
        'semantic_accuracy_verified': False, 'axes_calibrated': False,
        'numeric_axis_values_produced': False,
    }
    if allow_fragmented_band:
        result['version'] = 'pdf-chart-rgb-fragmented-band-observation-prototype-v1'
        result['allow_fragmented_band'] = True
        result['fragmented_band_observation_used'] = False
        result['sample_band_envelope'] = None

    def checked_result():
        if len(json.dumps(result, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')) > limits.maximum_result_bytes:
            raise PdfChartRgbSampleLimitError('Cross-section result metadata limit exceeded')
        return result

    if observation['band_count'] == 1:
        band = observation['bands'][0]
    elif allow_fragmented_band and 2 <= observation['band_count'] <= 3:
        bands = observation['bands']
        ymin, ymax = bands[0]['ymin'], bands[-1]['ymax']
        gaps = [[a['ymax'] + 1, b['ymin'] - 1] for a, b in zip(bands, bands[1:])]
        counts = [end - start + 1 for start, end in gaps]
        result['sample_band_envelope'] = {
            'ymin': ymin, 'ymax': ymax, 'ycenter': (ymin + ymax) / 2,
            'inclusive_extent_rows': ymax - ymin + 1,
            'original_band_count': len(bands), 'all_original_bands_included': True,
            'intervening_unpainted_row_ranges': gaps,
            'intervening_unpainted_row_counts': counts,
            'total_intervening_unpainted_rows': sum(counts),
            'maximum_intervening_unpainted_rows': max(counts),
            'paint_filled': False, 'band_selected': False,
            'marker_membership_proven': False,
        }
        if ymax - ymin + 1 > 65 or max(counts) > 1:
            result['reasons'].append('fragmented_band_envelope_or_gap_exceeds_observation_limit')
            return checked_result()
        result['fragmented_band_observation_used'] = True
        result['ambiguities'].append('sample_band_envelope_does_not_prove_one_marker')
        # Provisional crop/seed geometry only, not a replacement band or center.
        band = result['sample_band_envelope']
    else:
        result['reasons'].append('exactly_one_painted_band_required_without_selection')
        return checked_result()
    ordinate, diameter = band['ycenter'], band['ymax'] - band['ymin'] + 1
    result['band_midpoint_pixel'] = ordinate
    if not 7 <= diameter <= 65:
        result['reasons'].append('observed_vertical_diameter_outside_7_to_65_pixels')
        return checked_result()
    left, top, right, bottom = axes_pixel_bbox
    column, pad = observation['sample_column_index'], shape_window_half_width
    box = (max(left, column - pad), max(top, band['ymin'] - pad),
           min(right, column + pad + 1), min(bottom, band['ymax'] + pad + 1))
    if (box[2] - box[0]) * (box[3] - box[1]) * 3 > limits.maximum_crop_bytes:
        raise PdfChartRgbSampleLimitError('Cross-section crop byte limit exceeded')
    with source.crop(box) as crop:
        raw, (width, height) = crop.tobytes(), crop.size
    if width * height > 24_897:
        raise PdfChartRgbSampleLimitError('Cross-section crop work limit exceeded')
    result['shape_pixel_bbox'] = list(box)
    result['shape_pixel_sha256'] = sha256(raw).hexdigest()
    target, stride = bytes(exact_rgb), width * 3
    rows, columns = {}, {x: [] for x in range(box[0], box[2])}

    def runs(values):
        intervals = []
        for value in values:
            if not intervals or value > intervals[-1][1] + 1:
                intervals.append([value, value])
            else:
                intervals[-1][1] = value
        return intervals

    for y in range(height):
        painted_x = []
        for x in range(width):
            offset = y * stride + x * 3
            if raw[offset:offset + 3] == target:
                painted_x.append(box[0] + x)
                columns[box[0] + x].append(box[1] + y)
        rows[box[1] + y] = runs(painted_x)
        result['shape_exact_rgb_pixel_count'] += len(painted_x)
    result['row_exact_rgb_runs'] = [[y, intervals] for y, intervals in rows.items()]
    result['all_shape_exact_rgb_runs_returned'] = True
    columns = {x: runs(values) for x, values in columns.items()}

    def row_section(y):
        intervals = rows[y]
        containing = [r for r in intervals if r[0] <= column <= r[1]]
        return {'source_y': y, 'exact_rgb_runs': intervals,
                'anchor_containing_run': containing[0] if len(containing) == 1 else None}

    def column_section(x):
        return {'source_x': x, 'exact_rgb_runs': columns.get(x, [])}

    middle_rows = sorted({math.floor(ordinate), math.ceil(ordinate)})
    result['middle_row_sections'] = [row_section(y) for y in middle_rows]
    result['central_column_sections'] = [column_section(x)
                                         for x in range(column - 2, column + 3)]
    result['cap_row_sections'] = [row_section(band['ymin']), row_section(band['ymax'])]
    if (column - 2 < box[0] or column + 2 >= box[2]
            or band['ymin'] < box[1] + 2 or band['ymax'] >= box[3] - 2):
        result['reasons'].append('five_columns_and_vertical_endpoints_must_be_unclipped')
    central_runs = [section['exact_rgb_runs'] for section in result['central_column_sections']]
    if any(len(intervals) != 1 for intervals in central_runs):
        result['reasons'].append('each_central_column_requires_one_uninterrupted_run')
    elif (max(r[0][0] for r in central_runs) - min(r[0][0] for r in central_runs) > 2
          or max(r[0][1] for r in central_runs) - min(r[0][1] for r in central_runs) > 2
          or any(r[0][1] - r[0][0] + 1 < diameter - 2 for r in central_runs)):
        result['reasons'].append('central_vertical_runs_must_have_aligned_similar_heights')
    horizontal = [s['anchor_containing_run'] for s in result['middle_row_sections']]
    if any(r is None for r in horizontal):
        result['reasons'].append('middle_rows_require_an_observed_anchor_containing_run')
        return checked_result()
    xmin, xmax = min(r[0] for r in horizontal), max(r[1] for r in horizontal)
    result['observed_diameter_pixel_bbox'] = [xmin, band['ymin'], xmax + 1, band['ymax'] + 1]
    if (any(r[0] < box[0] + 2 or r[1] >= box[2] - 2 for r in horizontal)
            or any(abs((r[1] - r[0] + 1) - diameter) > 2 for r in horizontal)):
        result['reasons'].append('direct_horizontal_and_vertical_diameters_must_match_and_be_unclipped')
    if any(abs((r[0] + r[1]) / 2 - x_source_pixel) > 1 for r in horizontal):
        result['reasons'].append('direct_horizontal_midpoint_does_not_match_supplied_x')
    caps = [s['anchor_containing_run'] for s in result['cap_row_sections']]
    if (any(r is None for r in caps)
            or any(r[1] - r[0] + 1 > diameter / 2 for r in caps if r is not None)
            or (all(r is not None for r in caps)
                and (abs((caps[0][1] - caps[0][0]) - (caps[1][1] - caps[1][0])) > 2
                     or any(abs((r[0] + r[1]) / 2 - x_source_pixel) > 2 for r in caps)))):
        result['reasons'].append('both_direct_caps_must_be_narrow_aligned_and_similar')
    # Compare explicitly supplied-x-adjacent columns, not fitted/reflected points.
    # Two-pixel endpoint tolerance accounts for observed raster edges; it does
    # not restore a missing pixel or discard attached paint from the metadata.
    inner_offset = max(3, diameter // 3)
    previous_heights = [diameter, diameter]
    for offset in range(3, inner_offset + 1):
        pair = [column_section(column - offset), column_section(column + offset)]
        result['adjacent_column_pairs'].append({'offset': offset, 'sections': pair})
        intervals = [s['exact_rgb_runs'] for s in pair]
        if any(len(r) != 1 for r in intervals):
            result['reasons'].append('adjacent_columns_require_single_observed_runs')
            continue
        pair_runs = [r[0] for r in intervals]
        heights = [r[1] - r[0] + 1 for r in pair_runs]
        if (abs(pair_runs[0][0] - pair_runs[1][0]) > 2
                or abs(pair_runs[0][1] - pair_runs[1][1]) > 2
                or any(abs((r[0] + r[1]) / 2 - ordinate) > 2 for r in pair_runs)
                or any(h > previous + 2 for h, previous in zip(heights, previous_heights))):
            result['reasons'].append('adjacent_observed_extents_must_be_aligned_symmetric_and_tapered')
        if offset == inner_offset and any(not diameter / 2 <= h < diameter for h in heights):
            result['reasons'].append('adjacent_columns_require_observed_round_taper_not_a_thin_cross')
        previous_heights = heights
    result['reasons'] = list(dict.fromkeys(result['reasons']))
    if allow_fragmented_band and result['fragmented_band_observation_used']:
        result['reasons'].append('fragmented_band_envelope_is_not_marker_center_evidence')
    if not result['reasons']:
        result['status'] = 'full_diameter_source_shape_candidate'
        result['candidate_ordinate_pixel'] = ordinate
        result['cross_section_checks_passed'] = True
    return checked_result()
