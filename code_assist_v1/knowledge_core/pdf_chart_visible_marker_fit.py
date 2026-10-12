"""Unintegrated, approximate circle hypotheses from bounded source RGB paint.

This is geometry inference under an explicit filled-circular-paint assumption,
not recovery of printed measurements, chart semantics or guaranteed centers.
Every observed exact-RGB run/boundary remains in the result. A finite sampled
hypothesis search is not exhaustive: its interval is heuristic, not a confidence
interval or a proven bound on all feasible circles. Unknown never means absent.
"""
from collections import Counter
from itertools import combinations
import json
import math

from .pdf_chart_rgb_samples import (
    PdfChartRgbSampleError, PdfChartRgbSampleLimitError, PdfChartRgbSampleLimits,
    observe_rgb_marker_cross_sections, observe_circular_rgb_shape,
)


def _circle_from_three(points):
    (x, y), (bx, by), (cx, cy) = points
    ux, uy, vx, vy = bx - x, by - y, cx - x, cy - y
    determinant = 2 * (ux * vy - uy * vx)
    if abs(determinant) < 1e-8:
        return None
    u2, v2 = ux * ux + uy * uy, vx * vx + vy * vy
    center = (x + (u2 * vy - v2 * uy) / determinant,
              y + (ux * v2 - vx * u2) / determinant)
    radius = math.hypot(center[0] - x, center[1] - y)
    return (*center, radius) if all(math.isfinite(v) for v in (*center, radius)) else None


def _arc_evidence(points, center):
    """Cyclic observed-angle support; no interpolated arc points."""
    angles = sorted(math.atan2(y - center[1], x - center[0]) % math.tau
                    for x, y in points)
    if not angles:
        return {'coverage_degrees': 0., 'occupied_bins': 0, 'quadrants': 0}
    gaps = [b - a for a, b in zip(angles, angles[1:])]
    gaps.append(angles[0] + math.tau - angles[-1])
    return {'coverage_degrees': math.degrees(math.tau - max(gaps)),
            'occupied_bins': len({int(a / math.tau * 24) for a in angles}),
            'quadrants': len({int(a / math.tau * 4) for a in angles})}


def _paint_run_lengths(painted):
    """Direct row/column thicknesses, without deleting connected paint."""
    lengths = {point: [0, 0] for point in painted}
    for axis in (0, 1):
        groups = {}
        for point in painted:
            groups.setdefault(point[1-axis], []).append(point[axis])
        for fixed, values in groups.items():
            run = []
            for value in sorted(values):
                if run and value != run[-1] + 1:
                    for coordinate in run:
                        point = (coordinate, fixed) if axis == 0 else (fixed, coordinate)
                        lengths[point][axis] = len(run)
                    run = []
                run.append(value)
            for coordinate in run:
                point = (coordinate, fixed) if axis == 0 else (fixed, coordinate)
                lengths[point][axis] = len(run)
    return lengths


def fit_visible_rgb_marker(source, axes_pixel_bbox, x_source_pixel, exact_rgb,
                           *, shape_window_half_width=40,
                           allow_fragmented_band=False,
                           allow_thin_component_bands=False,
                           maximum_pixel_tests=2_000_000,
                           limits=PdfChartRgbSampleLimits()):
    """Return only an approximate tested-center interval or unresolved.

    At most 24 observed boundary points seed 2,024 circumcircle hypotheses.
    All crop paint is retained; seed selection/outlier membership are explicit.
    Required evidence includes >=180 degrees, >=3 quadrants, >=12 occupied
    angular bins, <=1px inlier residual and an unoccluded exact-color core.
    Connected line boundaries outside a circle are retained, not erased.
    Similar tested centers do not prove uniqueness in continuous geometry.
    ``allow_fragmented_band`` only opts into an all-band, bounded 2D observation;
    the same circle evidence, exact-color core and ambiguity guards still apply.
    """
    if type(allow_thin_component_bands) is not bool:
        raise PdfChartRgbSampleError('Thin-component observation flag must be a bool')
    if type(allow_fragmented_band) is not bool:
        raise PdfChartRgbSampleError('Fragmented-band observation flag must be a bool')
    if type(maximum_pixel_tests) is not int or not 1 <= maximum_pixel_tests <= 2_000_000:
        raise PdfChartRgbSampleError('Visible-marker work limit must be an integer in 1..2000000')
    observed = observe_rgb_marker_cross_sections(
        source, axes_pixel_bbox, x_source_pixel, exact_rgb,
        shape_window_half_width=shape_window_half_width,
        allow_fragmented_band=allow_fragmented_band, limits=limits)
    result = {
        'version': 'source-visible-rgb-marker-fit-prototype-v1',
        'status': 'unresolved', 'reasons': [], 'source_observation': observed,
        'source_pixel_sha256': observed['band_observation']['source_pixel_sha256'],
        'crop_pixel_bbox': observed['shape_pixel_bbox'],
        'crop_pixel_sha256': observed['shape_pixel_sha256'],
        'boundary_points': [], 'all_observed_boundary_points_returned': False,
        'seed_boundary_point_indices': [], 'hypotheses_tested': 0,
        'maximum_pixel_tests': maximum_pixel_tests, 'work_pixel_tests': 0,
        'accepted_hypotheses': 0, 'hypothesis_rejections': {},
        'representative_fit': None, 'inlier_boundary_point_indices': [],
        'tested_center_range_pixels': None, 'center_interval_pixels': None,
        'interval_margin_pixels': 1.5,
        'uncertainty_basis': 'tested_center_span_plus_1px_residual_and_0.5px_raster_margin',
        'interval_is_accuracy_guarantee': False, 'hypothesis_search_exhaustive': False,
        'unique_center_proven': False, 'unique_tested_center_cluster': False,
        'source_curve_circle_assumption': True, 'filled_exact_rgb_core_assumption': True,
        'geometry_inferred': False, 'approximate': True, 'not_printed_measurement': True,
        'source_pixels_modified': False, 'observed_paint_removed': False,
        'marker_membership_proven': False, 'series_membership_proven': False,
        'semantic_accuracy_verified': False, 'numerical_accuracy_verified': False,
        'numeric_axis_values_produced': False, 'axes_calibrated': False,
        'ambiguities': ['circular_paint_may_be_legend_or_other_graphic',
                        'finite_circle_search_is_not_complete_geometry_proof',
                        'residual_tolerance_cannot_rule_out_subpixel_or_thin_rim_loss'],
    }
    if allow_fragmented_band:
        result['version'] = 'source-visible-rgb-fragmented-band-fit-prototype-v1'
        result['allow_fragmented_band'] = True

    def finish(reason=None):
        if reason:
            result['reasons'].append(reason)
        if len(json.dumps(result, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':')).encode()) > limits.maximum_result_bytes:
            raise PdfChartRgbSampleLimitError('Visible-marker fit metadata limit exceeded')
        return result

    if not observed['all_shape_exact_rgb_runs_returned']:
        # Distant same-colour graphics must not be unioned into a marker. Test
        # every bounded observed group with the existing circle evidence, not
        # the largest band or an expected ordinate. All original bands remain.
        if (allow_fragmented_band and observed['reasons'] ==
                ['fragmented_band_envelope_or_gap_exceeds_observation_limit']):
            bands = observed['band_observation']['bands']
            groups = []
            for band in bands:
                if not groups or band['ymin'] - groups[-1][-1]['ymax'] - 1 > 1:
                    groups.append([])
                groups[-1].append(band)
            if (2 <= len(groups) <= 3
                    and all(group[-1]['ymax'] - group[0]['ymin'] + 1
                            >= (2 if allow_thin_component_bands else 3)
                            for group in groups)):
                thin_group = any(group[-1]['ymax'] - group[0]['ymin'] + 1 == 2
                    for group in groups)
                left, top, right, bottom = axes_pixel_bbox
                candidates, summaries, work = [], [], 0
                complete = True
                for ordinal, group in enumerate(groups):
                    remaining = maximum_pixel_tests - work
                    if remaining <= 0:
                        complete = False
                        break
                    ymin, ymax = group[0]['ymin'], group[-1]['ymax']
                    local_top = max(top, ymin - shape_window_half_width)
                    local_bottom = min(bottom, ymax + shape_window_half_width + 1)
                    if ordinal:
                        local_top = max(local_top,
                            (groups[ordinal-1][-1]['ymax'] + ymin + 1) // 2)
                    if ordinal + 1 < len(groups):
                        local_bottom = min(local_bottom,
                            (ymax + groups[ordinal+1][0]['ymin'] + 1) // 2)
                    local_axes = [left, local_top, right, local_bottom]
                    candidate = fit_visible_rgb_marker(source, local_axes,
                        x_source_pixel, exact_rgb,
                        shape_window_half_width=shape_window_half_width,
                        allow_fragmented_band=True, maximum_pixel_tests=remaining,
                        **({'allow_thin_component_bands': True}
                            if allow_thin_component_bands else {}),
                        limits=limits)
                    work += candidate['work_pixel_tests']
                    summaries.append(dict(source_y_range=[ymin, ymax],
                        local_axes_pixel_bbox=local_axes, status=candidate['status'],
                        reasons=candidate['reasons'],
                        center_interval_pixels=candidate['center_interval_pixels'],
                        work_pixel_tests=candidate['work_pixel_tests']))
                    if any('work_limit_exceeded' in reason for reason in candidate['reasons']):
                        complete = False
                        break
                    if candidate['status'] == 'approximate_source_geometry_candidate':
                        candidates.append(candidate)
                    elif thin_group:
                        shape, outside_circle_model = None, False
                        if candidate['source_observation']['reasons'] == [
                                'observed_vertical_diameter_outside_7_to_65_pixels']:
                            shape = observe_circular_rgb_shape(source, local_axes,
                                x_source_pixel, exact_rgb,
                                shape_window_half_width=shape_window_half_width, limits=limits)
                            box, view = shape['observed_component_bbox'], shape['shape_pixel_bbox']
                            outside_circle_model = (shape['observed_component_count'] == 1
                                and box is not None and view is not None
                                and box[2] - box[0] >= 7 and box[3] - box[1] < 7
                                and box[1] >= view[1] + 2 and box[3] <= view[3] - 2)
                        summaries[-1]['existing_component_observation'] = shape
                        summaries[-1]['conditionally_outside_circle_model'] = outside_circle_model
                        if not outside_circle_model:
                            complete = False
                result['work_pixel_tests'] = work
                result['distant_band_candidate_summaries'] = summaries
                if complete and len(candidates) == 1:
                    result = candidates[0]
                    result['maximum_pixel_tests'] = maximum_pixel_tests
                    result['work_pixel_tests'] = work
                    result['distant_band_source_observation'] = observed
                    result['distant_band_candidate_summaries'] = summaries
                    result['ambiguities'].append(
                        'unique_bounded_candidate_does_not_prove_marker_or_series_ownership')
                    return finish()
                return finish('distant_band_candidates_ambiguous_or_work_limit_exceeded')
        return finish('no_bounded_2d_observation_or_single_adequate_source_band')
    painted = {(x, y) for y, runs in observed['row_exact_rgb_runs']
               for start, end in runs for x in range(start, end + 1)}
    boundary = sorted((p for p in painted
                       if any((p[0] + dx, p[1] + dy) not in painted
                              for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))),
                      key=lambda p: (p[1], p[0]))
    if len(boundary) > 2048:
        return finish('observed_boundary_count_exceeds_2048_work_limit')
    result['boundary_points'] = [list(p) for p in boundary]
    result['all_observed_boundary_points_returned'] = True
    if len(boundary) < 16:
        return finish('insufficient_independent_observed_boundary')
    run_lengths = _paint_run_lengths(painted)
    provisional = (x_source_pixel, observed['band_midpoint_pixel'])
    band = (observed['sample_band_envelope']
            if observed.get('fragmented_band_observation_used')
            else observed['band_observation']['bands'][0])
    observed_half_height = (band['ymax'] - band['ymin'] + 1) / 2
    bins = {}
    for index, (x, y) in enumerate(boundary):
        angle = math.atan2(y - provisional[1], x - provisional[0]) % math.tau
        key = int(angle / math.tau * 24)
        distance = abs(math.hypot(x - provisional[0], y - provisional[1])
                       - observed_half_height)
        if key not in bins or (distance, index) < bins[key]:
            bins[key] = (distance, index)
    seeds = [bins[key][1] for key in sorted(bins)]
    result['seed_boundary_point_indices'] = seeds
    accepted, rejected, seen = [], Counter(), set()
    left, top, right, bottom = axes_pixel_bbox
    roi_left, roi_top, roi_right, roi_bottom = observed['shape_pixel_bbox']
    for indices in combinations(seeds, 3):
        result['hypotheses_tested'] += 1
        circle = _circle_from_three([boundary[i] for i in indices])
        if circle is None:
            rejected['degenerate_triple'] += 1
            continue
        cx, cy, radius = circle
        if not 3 <= radius <= 32.5 or abs(cx - x_source_pixel) > 2:
            rejected['radius_or_supplied_x_mismatch'] += 1
            continue
        # Quantized deduplication bounds repeated tests; raw seed points remain.
        key = tuple(round(v * 4) for v in circle)
        if key in seen:
            continue
        seen.add(key)
        if (cx - radius < max(left, roi_left) + 1
                or cx + radius >= min(right, roi_right) - 1
                or cy - radius < max(top, roi_top) + 1
                or cy + radius >= min(bottom, roi_bottom) - 1):
            rejected['circle_extent_clipped_or_unobserved'] += 1
            continue
        # Conservative upper bound for residual, angle, inner-boundary and
        # near-outer-boundary traversals, including gates that exit earlier.
        result['work_pixel_tests'] += 4 * len(boundary)
        if result['work_pixel_tests'] > maximum_pixel_tests:
            return finish('pixel_hypothesis_work_limit_exceeded_no_partial_fit')
        residuals = [abs(math.hypot(x - cx, y - cy) - radius) for x, y in boundary]
        inliers = [i for i, residual in enumerate(residuals) if residual <= 1]
        evidence = _arc_evidence([boundary[i] for i in inliers], (cx, cy))
        if (len(inliers) < max(16, math.ceil(radius * 2))
                or evidence['coverage_degrees'] < 180 - 1e-9
                or evidence['quadrants'] < 3 or evidence['occupied_bins'] < 12):
            rejected['inadequate_independent_visible_arc'] += 1
            continue
        if sum(math.hypot(x - cx, y - cy) < radius - 1 for x, y in boundary) > 1:
            rejected['internal_boundary_cap_or_overlap_ambiguity'] += 1
            continue
        # Attached thin lines can produce narrow outgoing sectors; an extended
        # unmatched outer rim instead contradicts this tested circular model.
        near_outer_bins = {int((math.atan2(y - cy, x - cx) % math.tau) / math.tau * 24)
                           for x, y in boundary
                           if radius + 1 < math.hypot(x - cx, y - cy) <= radius + 3}
        if len(near_outer_bins) > 4:
            rejected['unexplained_near_outer_rim_not_a_narrow_line_sector'] += 1
            continue
        result['work_pixel_tests'] += len(painted)
        if result['work_pixel_tests'] > maximum_pixel_tests:
            return finish('pixel_hypothesis_work_limit_exceeded_no_partial_fit')
        maximum_line_thickness = 2 * math.ceil(radius / 3) + 1
        if any((x - cx) ** 2 + (y - cy) ** 2 > (radius + 2) ** 2
               and min(run_lengths[(x, y)]) > maximum_line_thickness
               for x, y in painted):
            rejected['unexplained_outside_thick_paint_cap_or_overlap'] += 1
            continue
        core_radius = radius - 1.5
        result['work_pixel_tests'] += ((math.floor(cx + core_radius) - math.ceil(cx - core_radius) + 1)
                                       * (math.floor(cy + core_radius) - math.ceil(cy - core_radius) + 1))
        if result['work_pixel_tests'] > maximum_pixel_tests:
            return finish('pixel_hypothesis_work_limit_exceeded_no_partial_fit')
        missing_core = any((x, y) not in painted
                           for y in range(math.ceil(cy - core_radius), math.floor(cy + core_radius) + 1)
                           for x in range(math.ceil(cx - core_radius), math.floor(cx + core_radius) + 1)
                           if (x - cx) ** 2 + (y - cy) ** 2 <= core_radius ** 2)
        if missing_core:
            rejected['missing_exact_color_core_or_occlusion'] += 1
            continue
        accepted.append({'center_pixel': [cx, cy], 'radius_pixels': radius,
                         'inlier_indices': inliers, 'arc_evidence': evidence,
                         'maximum_inlier_residual_pixels': max(residuals[i] for i in inliers),
                         'rms_inlier_residual_pixels': math.sqrt(
                             sum(residuals[i] ** 2 for i in inliers) / len(inliers))})
        if len(accepted) > 256:
            return finish('accepted_hypothesis_count_limit_exceeded_no_partial_fit')
    result['hypothesis_rejections'] = dict(rejected)
    result['accepted_hypotheses'] = len(accepted)
    if not accepted:
        return finish('no_source_supported_visible_circle_hypothesis')
    xs, ys = ([item['center_pixel'][axis] for item in accepted] for axis in (0, 1))
    ranges = [[min(xs), max(xs)], [min(ys), max(ys)]]
    result['tested_center_range_pixels'] = ranges
    if any(hi - lo > 2 for lo, hi in ranges):
        return finish('multiple_tested_centers_are_ambiguous')
    # This representative is descriptive, not a largest-paint/series selection.
    representative = min(accepted, key=lambda h: h['rms_inlier_residual_pixels'])
    result['representative_fit'] = {k: v for k, v in representative.items() if k != 'inlier_indices'}
    result['inlier_boundary_point_indices'] = representative['inlier_indices']
    result['center_interval_pixels'] = [[lo - 1.5, hi + 1.5] for lo, hi in ranges]
    result['unique_tested_center_cluster'] = True
    result['geometry_inferred'] = True
    result['status'] = 'approximate_source_geometry_candidate'
    return finish()
