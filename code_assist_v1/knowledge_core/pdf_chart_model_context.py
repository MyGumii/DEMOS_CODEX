"""Complete compact chart source context, not semantic or publication authority.

The caller supplies a freshly checked reading and its exact byte identity. Raw
labels stay raw; rounded raster estimates remain conditional, and unresolved
points remain null. This helper does no OCR, fitting, source I/O or model work.
"""
import json
import re

from ..document_pdf_process import PdfProcessError
from .pdf_chart_complete_reading import (
    PdfCompleteChartError, PdfCompleteChartReading, render_complete_source_chart,
)
from .pdf_chart_retention_markdown import PdfChartRetentionMarkdownError, _approximation


VERSION = 'pdf-chart-model-context-v1'
MAXIMUM_CONTEXT_BYTES = 8192
MAXIMUM_COMPLETE_CONTEXT_BYTES = 33_554_432
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class PdfChartModelContextError(ValueError):
    pass


def render_chart_model_context(reading, *, expected_reading_sha256,
                               maximum_context_bytes=MAXIMUM_CONTEXT_BYTES, cancel_event=None):
    """Return every panel × observed condition × raw series, or fail whole.

    A ``~decimal`` cell explicitly denotes a conditional raster approximation;
    ``~!decimal`` additionally denotes extrapolation beyond read tick support.
    Null is an unresolved point, never zero, an interpolated point or a value
    recovered from visible color ranges. Series-major matrix positions retain
    duplicate raw labels and condition literals without merging them.
    """
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise PdfProcessError('CANCELLED')
    check()
    if (type(reading) is not PdfCompleteChartReading
            or type(expected_reading_sha256) is not str
            or _SHA.fullmatch(expected_reading_sha256) is None
            or type(maximum_context_bytes) is not int
            or not 1 <= maximum_context_bytes <= MAXIMUM_COMPLETE_CONTEXT_BYTES):
        raise PdfChartModelContextError('Bounded unchanged complete reading required')
    try:
        # Reuse the complete inventory/state validator, not its verbose text.
        # It does not call OCR or the chart fitter.
        try:
            render_complete_source_chart(reading, expected_reading_sha256=expected_reading_sha256,
                cancel_event=cancel_event)
        finally:
            check()
        record = json.loads(reading.metadata_json)
        if any(record[key] is not False for key in (
                'semantic_accuracy_verified', 'series_ownership_verified',
                'numerical_accuracy_verified', 'source_text_corrected')):
            raise PdfChartModelContextError('Unverified derived source observations required')
        legends = record['legend_readings']
        panels, condition_sets, assumption_sets = [], [], []
        def shared_index(sets, value):
            if value not in sets:
                sets.append(value)
            return sets.index(value)
        for panel in record['panels']:
            check()
            conditions = panel['conditions']
            rows = {(row['condition_anchor_id'], row['legend_source_word_id']): row
                for row in panel['readings']}
            points = []
            for legend in legends:
                check()
                values = []
                for condition in conditions:
                    check()
                    row = rows[(condition['anchor_id'], legend['source_word_id'])]
                    if row['status'] == 'unresolved':
                        values.append(None)
                    else:
                        value = _approximation(row, panel['calibration']).removeprefix('약 ')
                        values.append(('~!' if row['tick_support_extrapolated'] else '~') + value)
                points.append(values)
            subject = panel['subject_binding']
            metric = panel['metric_binding']
            # Column-labelled rows avoid repeating long field/assumption names
            # for every panel, without dropping any observation or binding.
            panels.append([panel['panel_id'], subject['selected_subject'] if subject else None,
                metric['selected_literal'], shared_index(condition_sets, [condition['literal'] for condition in conditions]), points,
                shared_index(assumption_sets, [subject is not None,
                    metric.get('binding_basis') == 'conditional_shared_row_axis_title',
                    True, panel['scale_hypothesis'] == 'linear'])])
        context = dict(version=VERSION, reading_sha256=expected_reading_sha256,
            source_kind='raw_OCR_conditional_raster_not_semantic_truth_or_printed_measurements',
            inventory='all_observed_points_only_not_all_visible_color_evidence',
            point_encoding={'~': 'conditional_approximation',
                '~!': 'conditional_approximation_outside_tick_support', 'null': 'unresolved_point_unknown'},
            matrix_order='series_then_conditions', raw_series=[legend['label_literal'] for legend in legends],
            panel_columns=['id', 'subject_candidate', 'metric_candidate', 'condition_set', 'points', 'assumption_set'],
            assumption_columns=['caption_order', 'shared_row', 'shared_palette', 'linear_scale'],
            condition_sets=condition_sets, assumption_sets=assumption_sets,
            panels=panels, panel_count=len(panels), condition_count=record['observed_condition_count'],
            sample_count=record['sample_count'])
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        check()
    except PdfProcessError:
        raise
    except PdfChartModelContextError:
        raise
    except (PdfCompleteChartError, PdfChartRetentionMarkdownError, KeyError, TypeError,
            ValueError, UnicodeError, RecursionError) as error:
        raise PdfChartModelContextError('Complete unchanged chart source inventory required') from error
    if len(encoded.encode('utf-8')) > maximum_context_bytes:
        raise PdfChartModelContextError('Complete chart model context exceeds its byte budget')
    check()
    return encoded
