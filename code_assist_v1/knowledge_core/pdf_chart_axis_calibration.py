"""Isolated linear-axis consistency calibration, not semantic verification.

Only caller-supplied source coordinates and explicitly read decimal tick
literals are used. Endpoint fitting is checked against every interior tick in
source pixels. Unknown/nonlinear scales and incomplete/inconsistent bindings
raise an unresolved error; no labels, missing values or fallback are invented.
No model, pipeline, database or publication operation is performed here.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import math
import re

VERSION = 'pdf-chart-linear-axis-calibration-candidate-v1'
MAXIMUM_TICKS = 128
MAXIMUM_SOURCE_PIXELS = 16_000_000
MAXIMUM_RESIDUAL_SOURCE_PIXELS = Fraction(2)
MAXIMUM_LITERAL_CHARACTERS = 128
MAXIMUM_DECIMAL_EXPONENT = 4096
DECIMAL_OUTPUT_PRECISION = 96
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


class PdfChartAxisCalibrationError(ValueError):
    """An explicitly unresolved binding, never permission to repair it."""
    status = 'unresolved'

    def __init__(self, reason):
        self.reason = reason
        super().__init__('Unresolved linear-axis calibration: '+reason)


def _reject(reason):
    raise PdfChartAxisCalibrationError(reason)


def _bounded_decimal(value, reason):
    if (not value.is_finite() or len(value.as_tuple().digits) > MAXIMUM_LITERAL_CHARACTERS
            or abs(value.as_tuple().exponent) > MAXIMUM_DECIMAL_EXPONENT):
        _reject(reason)
    return value


def _tick_number(literal):
    if (type(literal) is not str or not 0 < len(literal) <= MAXIMUM_LITERAL_CHARACTERS
            or _NUMBER.fullmatch(literal) is None):
        _reject('tick_requires_strict_decimal_literal')
    try:
        value = Decimal(literal)
    except InvalidOperation:
        _reject('tick_requires_strict_decimal_literal')
    return _bounded_decimal(value, 'tick_decimal_exceeds_finite_numeric_bounds')


def _coordinate(value):
    if type(value) not in (int, float, Decimal):
        _reject('source_coordinate_requires_finite_number')
    # Source dimensions are at most 16M pixels; avoid constructing an
    # unbounded integer string before the original-coordinate bounds check.
    if type(value) is int and value.bit_length() > 64:
        _reject('source_coordinate_requires_finite_bounded_number')
    if type(value) is float and not math.isfinite(value):
        _reject('source_coordinate_requires_finite_number')
    try:
        coordinate = value if type(value) is Decimal else Decimal(str(value))
    except InvalidOperation:
        _reject('source_coordinate_requires_finite_number')
    return _bounded_decimal(coordinate, 'source_coordinate_requires_finite_bounded_number')


def _decimal(ratio):
    with localcontext() as context:
        context.prec = DECIMAL_OUTPUT_PRECISION
        context.Emax, context.Emin = 100_000, -100_000
        return Decimal(ratio.numerator) / Decimal(ratio.denominator)


@dataclass(frozen=True, slots=True)
class CheckedAxisTick:
    anchor_id: str
    source_coordinate: Decimal
    tick_literal: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class LinearAxisCalibration:
    """Numerical consistency only; decimal coefficients can be rounded.

    The exact rational coefficients are retained for mapping observed source
    coordinates. This operation does not fill missing tick labels or certify
    that a tick was read correctly, matched to the right anchor, or belongs to
    a particular metric, series or panel.
    """
    coordinate_axis: str
    source_dimensions: tuple[int, int]
    ticks: tuple[CheckedAxisTick, ...]
    _slope: Fraction
    _intercept: Fraction
    _maximum_residual_pixels: Fraction

    @property
    def slope(self):
        return _decimal(self._slope)

    @property
    def intercept(self):
        return _decimal(self._intercept)

    @property
    def source_coordinate_interval(self):
        return self.ticks[0].source_coordinate, self.ticks[-1].source_coordinate

    @property
    def maximum_residual_pixels(self):
        return _decimal(self._maximum_residual_pixels)

    def map_source_coordinate(self, coordinate):
        """Map an observed coordinate inside checked tick support, never extrapolate."""
        value = _coordinate(coordinate)
        lower, upper = self.source_coordinate_interval
        if not lower <= value <= upper:
            _reject('observed_coordinate_outside_checked_tick_support')
        return _decimal(self._slope*Fraction(value)+self._intercept)

    def map_measured_frame_coordinate(self, coordinate, measured_axes_pixel_bbox):
        """Extend linear pixel-to-value mapping only inside a measured source frame.

        This is NOT data-curve extrapolation or repaired ticks. A frame can
        contain real markers beyond its highest/lowest printed tick; retain an
        explicit tick-support flag. Frame verification is geometric only.
        """
        box = measured_axes_pixel_bbox
        width, height = self.source_dimensions
        if (type(box) not in (tuple, list) or len(box) != 4
                or any(type(value) is not int for value in box)
                or not 0 <= box[0] < box[2] <= width
                or not 0 <= box[1] < box[3] <= height):
            _reject('invalid_measured_source_frame_bounds')
        index = 0 if self.coordinate_axis == 'x' else 1
        lower, upper = box[index], box[index+2]
        if any(not lower <= tick.source_coordinate < upper for tick in self.ticks):
            _reject('measured_source_frame_excludes_read_ticks')
        value = _coordinate(coordinate)
        if not lower <= value < upper:
            _reject('observed_coordinate_outside_measured_source_frame')
        tick_lower, tick_upper = self.source_coordinate_interval
        return {'value': _decimal(self._slope*Fraction(value)+self._intercept),
            'tick_support_extrapolated': not tick_lower <= value <= tick_upper,
            'measured_frame_bound_verified': True, 'semantic_accuracy_verified': False}

    def report(self):
        lower, upper = self.source_coordinate_interval
        return {'version': VERSION, 'scale': 'linear',
            'coordinate_axis': self.coordinate_axis,
            'source_dimensions': list(self.source_dimensions),
            'slope': str(self.slope), 'intercept': str(self.intercept),
            'source_coordinate_interval': {'min': str(lower), 'max': str(upper)},
            'maximum_residual_source_pixels': str(self.maximum_residual_pixels),
            'maximum_allowed_residual_source_pixels': '2',
            'ticks': [{'anchor_id': tick.anchor_id,
                'source_coordinate': str(tick.source_coordinate),
                'tick_literal': tick.tick_literal, 'value': str(tick.value)} for tick in self.ticks],
            'fit': 'endpoints_only_all_interior_ticks_independently_checked',
            'guard_arithmetic': 'exact_rational_from_strict_decimal_literals',
            'decimal_output_precision': DECIMAL_OUTPUT_PRECISION,
            'decimal_coefficients_may_be_rounded': True,
            'numeric_consistency_verified': True, 'semantic_accuracy_verified': False,
            'tick_reading_accuracy_verified': False, 'anchor_binding_accuracy_verified': False,
            'series_binding_accuracy_verified': False, 'source_authority_verified': False,
            'missing_labels_interpolated': False, 'fallback_used': False,
            'normal_workflow_integrated': False, 'publication_authority': False}


def calibrate_linear_axis(bindings, *, scale, coordinate_axis, source_dimensions):
    """Validate at least three distinct, finite source-coordinate/tick pairs.

    Each binding has exactly anchor_id/source_coordinate/tick_literal. Coordinates
    use the original source pixel frame, not a resized atlas. Bindings are sorted
    geometrically; labels remain attached to their supplied anchor. Source order
    can be increasing or decreasing numerically, but must be strictly monotonic.
    Caller must explicitly declare a linear axis; scale is never inferred.
    """
    if scale != 'linear' or type(scale) is not str:
        _reject('unsupported_or_unknown_scale')
    if coordinate_axis not in ('x', 'y') or type(coordinate_axis) is not str:
        _reject('unknown_source_coordinate_axis')
    if (type(source_dimensions) not in (tuple, list) or len(source_dimensions) != 2
            or any(type(value) is not int or value <= 0 for value in source_dimensions)
            or source_dimensions[0]*source_dimensions[1] > MAXIMUM_SOURCE_PIXELS):
        _reject('invalid_or_over_budget_source_pixel_bounds')
    if type(bindings) not in (tuple, list):
        _reject('complete_tick_bindings_required')
    if len(bindings) < 3:
        _reject('at_least_three_read_ticks_required')
    if len(bindings) > MAXIMUM_TICKS:
        _reject('complete_tick_count_exceeds_budget')
    extent = source_dimensions[0 if coordinate_axis == 'x' else 1]
    ticks, ids, coordinates, values = [], set(), set(), set()
    for binding in bindings:
        if type(binding) is not dict or set(binding) != {'anchor_id', 'source_coordinate', 'tick_literal'}:
            _reject('exact_source_anchor_and_literal_binding_required')
        anchor_id = binding['anchor_id']
        if (type(anchor_id) is not str or not anchor_id.strip() or len(anchor_id) > 128
                or anchor_id in ids):
            _reject('distinct_nonblank_source_anchor_ids_required')
        coordinate, value = _coordinate(binding['source_coordinate']), _tick_number(binding['tick_literal'])
        if not 0 <= coordinate < extent:
            _reject('source_coordinate_outside_original_pixel_bounds')
        if coordinate in coordinates or value in values:
            _reject('duplicate_source_coordinate_or_tick_value')
        ids.add(anchor_id)
        coordinates.add(coordinate)
        values.add(value)
        ticks.append(CheckedAxisTick(anchor_id, coordinate, binding['tick_literal'], value))
    ticks.sort(key=lambda tick: tick.source_coordinate)
    rising = ticks[-1].value > ticks[0].value
    if any((right.value > left.value) != rising for left, right in zip(ticks, ticks[1:])):
        _reject('nonmonotonic_tick_values_in_source_order')
    first, last = ticks[0], ticks[-1]
    x0, x1, y0, y1 = map(Fraction, (first.source_coordinate, last.source_coordinate, first.value, last.value))
    slope = (y1-y0)/(x1-x0)
    intercept = y0-slope*x0
    residuals = [abs((Fraction(tick.value)-intercept)/slope-Fraction(tick.source_coordinate))
        for tick in ticks[1:-1]]
    maximum = max(residuals)
    if maximum > MAXIMUM_RESIDUAL_SOURCE_PIXELS:
        _reject('interior_tick_residual_exceeds_two_source_pixels')
    return LinearAxisCalibration(coordinate_axis, tuple(source_dimensions), tuple(ticks),
        slope, intercept, maximum)
