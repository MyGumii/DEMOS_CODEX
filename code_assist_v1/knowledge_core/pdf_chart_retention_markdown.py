"""Readable source-observation tables; no authoring or publication authority.

Keep all observed conditions and legends, including unreadable points. Raster
estimates are never represented as printed experiment measurements. Rendering
does not repair OCR, infer labels, certify a curve reader, or call a model.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json
import re

from .pdf_chart_source_reading import PdfChartSourceReading, VERSION as READING_VERSION

VERSION = 'pdf-chart-retention-markdown-v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class PdfChartRetentionMarkdownError(ValueError):
    pass


def _cell(value):
    if value is None:
        return '판독 불가'
    if (type(value) is not str or not 0 < len(value) <= 256
            or any(ord(c) < 32 for c in value)):
        raise PdfChartRetentionMarkdownError('Bounded single-line label required')
    # Source/model strings are data, including apparent links and HTML.
    return ''.join('\\'+c if c in '\\|`*_[]<>#!' else c for c in value)


def _number(value):
    if type(value) is not str or not 0 < len(value) <= 256:
        raise PdfChartRetentionMarkdownError('Bounded decimal observation required')
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise PdfChartRetentionMarkdownError('Decimal observation required') from error
    if not number.is_finite() or (number and not -100 <= number.adjusted() <= 100):
        raise PdfChartRetentionMarkdownError('Bounded finite observation required')
    return number


def _approximation(row, calibration):
    value = _number(row['approximate_value'])
    if type(calibration) is not dict or calibration.get('scale') != 'linear':
        raise PdfChartRetentionMarkdownError('Explicit calibrated scale required')
    slope = abs(_number(calibration.get('slope')))
    if not slope:
        raise PdfChartRetentionMarkdownError('Nonzero observed scale required')
    # Display resolution, NOT a confidence interval or bound on total error.
    # Avoid displaying 96 arithmetic digits as measurement precision.
    places = min(8, max(0, -(slope*2).adjusted()))
    with localcontext() as context:
        context.prec = 128
        displayed = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return '약 '+format(displayed, 'f')


def render_source_chart_reading(reading, *, expected_reading_sha256,
                                maximum_markdown_bytes=262_144):
    """Render a complete, byte-bound reading without turning it into authority."""
    if (type(reading) is not PdfChartSourceReading
            or type(expected_reading_sha256) is not str
            or _SHA.fullmatch(expected_reading_sha256) is None
            or sha256(reading.metadata_json.encode()).hexdigest() != expected_reading_sha256
            or type(maximum_markdown_bytes) is not int
            or not 1 <= maximum_markdown_bytes <= 262_144
            or len(reading.metadata_json.encode()) > 4_194_304):
        raise PdfChartRetentionMarkdownError('Bounded unchanged source reading required')
    try:
        record = json.loads(reading.metadata_json)
    except (ValueError, RecursionError) as error:
        raise PdfChartRetentionMarkdownError('Source reading JSON required') from error
    if (record.get('version') != READING_VERSION
            or record.get('numbers_are_printed_measurements') is not False
            or record.get('missing_values_filled') is not False
            or record.get('all_observed_conditions_retained') is not True
            or type(record.get('legend_readings')) is not list
            or not 1 <= len(record['legend_readings']) <= 32
            or type(record.get('panels')) is not list
            or not 1 <= len(record['panels']) <= 64):
        raise PdfChartRetentionMarkdownError('Complete source observation inventory required')
    legends = record['legend_readings']
    legend_ids = [legend['source_word_id'] for legend in legends]
    if len(set(legend_ids)) != len(legend_ids):
        raise PdfChartRetentionMarkdownError('Distinct observed legends required')
    sections = ['### 그림의 조건별 수치 판독',
        '아래 값은 원본 그림의 픽셀 좌표에서 읽은 **근삿값**입니다. '
        '인쇄된 실험 측정값과 구분하며, 표시 자릿수는 정확도 보증이 아닙니다. '
        '가려지거나 판독하지 못한 점은 값을 채우지 않고 표시합니다.']
    panel_ids, total = set(), 0
    for ordinal, panel in enumerate(record['panels'], 1):
        if panel['panel_id'] in panel_ids:
            raise PdfChartRetentionMarkdownError('Distinct source panels required')
        panel_ids.add(panel['panel_id'])
        declaration = panel['declaration']
        labels = [declaration.get('subject'), declaration.get('metric')]
        title = ' · '.join(_cell(label) for label in labels if label is not None)
        sections.append('#### '+(title or f'원본 패널 {ordinal}'))
        conditions = panel['conditions']
        if type(conditions) is not list or len(conditions) > 256:
            raise PdfChartRetentionMarkdownError('Bounded full condition list required')
        condition_ids = [condition['anchor_id'] for condition in conditions]
        if len(set(condition_ids)) != len(condition_ids):
            raise PdfChartRetentionMarkdownError('Distinct source condition anchors required')
        if not conditions:
            if panel['readings']:
                raise PdfChartRetentionMarkdownError('Values without source conditions')
            sections.append('원본 조건의 위치를 판독하지 못했습니다. 조건이나 수치를 추정해 추가하지 않았습니다.')
            continue
        expected = {(condition_id, legend_id) for condition_id in condition_ids for legend_id in legend_ids}
        rows = {}
        for row in panel['readings']:
            key = (row['condition_anchor_id'], row['legend_source_word_id'])
            if key not in expected or key in rows or row.get('printed_measurement') is not False:
                raise PdfChartRetentionMarkdownError('Exact once-only source sample ownership required')
            if row.get('status') not in ('unresolved', 'source_shape_approximation_candidate'):
                raise PdfChartRetentionMarkdownError('Known source observation state required')
            if ((row['status'] == 'unresolved' and row['approximate_value'] is not None)
                    or (row['status'] != 'unresolved' and row['approximate_value'] is None)):
                raise PdfChartRetentionMarkdownError('Unresolved observations cannot acquire values')
            rows[key] = row
        if set(rows) != expected:
            raise PdfChartRetentionMarkdownError('A source condition or legend reading is missing')
        total += len(rows)
        sections.append('| 계열 | '+' | '.join(_cell(condition.get('literal')) for condition in conditions)+' |\n'
            '| --- | '+' | '.join('---' for _ in conditions)+' |')
        table = []
        unresolved = 0
        for legend in legends:
            cells = []
            for condition in conditions:
                row = rows[(condition['anchor_id'], legend['source_word_id'])]
                if row['status'] == 'unresolved':
                    cells.append('판독 불가')
                    unresolved += 1
                else:
                    cells.append(_approximation(row, panel['calibration']))
            table.append('| '+_cell(legend['label_literal'])+' | '+' | '.join(cells)+' |')
        sections.append('\n'.join(table))
        if unresolved:
            sections.append(f'이 패널의 판독 불가 항목 {unresolved}개는 원본 위치를 유지했습니다. '
                '다른 조건의 값으로 보간하거나 겹친 곡선의 값을 대신 넣지 않았습니다.')
    if total != record['sample_count']:
        raise PdfChartRetentionMarkdownError('Complete source sample count changed')
    markdown = '\n\n'.join(sections)+'\n'
    if len(markdown.encode()) > maximum_markdown_bytes:
        raise PdfChartRetentionMarkdownError('Complete Markdown exceeds its budget')
    return markdown
