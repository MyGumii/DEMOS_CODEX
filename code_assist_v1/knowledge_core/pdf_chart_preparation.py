"""Fresh bounded chart observations, not publication or numerical authority.

Only initial preparation invokes this producer. Restoration consumes the retained
aggregate; it must not call this function to re-OCR. Original full-page/model
images remain unchanged. Unsupported figures stay in the complete inventory.
"""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import struct
from time import monotonic

from ..document_pdf_process import PdfProcessError, PdfProcessPolicy, _run_worker
from .pdf_layout_source import checked_layout
from .pdf_visual_source import checked_visual_source
from .pdf_native_figure_source import (PdfNativeFigureLimits, checked_native_figures,
    prepare_native_figures, find_native_figure, native_figure_views)
from . import pdf_native_chart_input as native_input
from .pdf_chart_rotated_y_ocr import (PdfRotatedYOcrLimits, read_rotated_y_strip_ocr,
    AXIS_FRAGMENT_RECIPE_VERSION, effective_secondary_x_bindings)
from .pdf_chart_literal_support import resolve_rotated_literal_support
from .pdf_chart_complete_reading import read_complete_source_chart, SOURCE_GEOMETRY_INPUT_VERSION
from .pdf_chart_supplement import (PdfChartSupplement, checked_chart_supplement, _load_bound_chart_supplement,
    prepare_chart_supplement, WRITER_INPUT_VERSION, CONDITIONAL_WRITER_INPUT_VERSION,
    CURVE_FITTING_NOT_REQUESTED, LITERAL_CONTEXT_VERSION, LITERAL_CONTEXT_WRITER_VERSION, LITERAL_CONTEXT_TABLE_VERSION,
    LITERAL_CONTEXT_NATIVE_VERSION, NATIVE_TEXT_PRESENTATION_VERSION, LITERAL_CONTEXT_KEY_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION, LITERAL_CONTEXT_RELATION_VERSION, LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION, LITERAL_CONTEXT_BOUNDED_OWNED_VERSION, LITERAL_CONTEXT_ADMITTED_OWNED_VERSION,
    KEY_INFORMATION_REASON, KEY_INFORMATION_SOURCE_VERSION)
from .pdf_page_label_ocr import prepare_page_label_ocr, retain_page_label_ocr, restore_page_label_ocr
from .pdf_figure_roi_source import (prepare_figure_roi, checked_figure_roi,
    PdfFigureRoiLimits, PdfFigureRoiIneligible)


VERSION = 'pdf-chart-fresh-preparation-v1'


class PdfChartPreparationError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise PdfChartPreparationError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False)


def _hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True, slots=True)
class PdfChartPreparationLimits:
    maximum_input_bytes: int = 268_435_456
    maximum_figures: int = 128
    maximum_samples: int = 8192
    maximum_seconds: int = 120
    maximum_supplement_bytes: int = 33_554_432
    maximum_reading_bytes: int = 4_194_304
    maximum_pixel_tests: int = 64_000_000
    maximum_reading_seconds: int = 30
    native: PdfNativeFigureLimits = field(default_factory=PdfNativeFigureLimits)
    ocr: native_input.PdfNativeChartInputLimits = field(default_factory=native_input.PdfNativeChartInputLimits)
    rotated: PdfRotatedYOcrLimits = field(default_factory=PdfRotatedYOcrLimits)

    def __post_init__(self):
        caps = (268_435_456, 4096, 8192, 300, 33_554_432,
            4_194_304, 64_000_000, 60)
        values = [getattr(self, name) for name in (
            'maximum_input_bytes', 'maximum_figures', 'maximum_samples', 'maximum_seconds',
            'maximum_supplement_bytes', 'maximum_reading_bytes', 'maximum_pixel_tests',
            'maximum_reading_seconds')]
        _require(all(type(v) is int and 1 <= v <= cap for v, cap in zip(values, caps)),
            'Invalid complete chart preparation limits')
        _require(type(self.native) is PdfNativeFigureLimits
            and type(self.ocr) is native_input.PdfNativeChartInputLimits
            and type(self.rotated) is PdfRotatedYOcrLimits, 'Typed preparation limits required')


class _Budget:
    def __init__(self, seconds, cancel_event):
        self.deadline, self.cancel_event = monotonic()+seconds, cancel_event

    def is_set(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise PdfProcessError('CANCELLED')
        _require(monotonic() < self.deadline, 'Complete chart preparation time budget exceeded')
        return False

    def remaining(self, ceiling):
        self.is_set()
        return min(ceiling, self.deadline-monotonic())


def chart_figure_inventory(layout):
    """Every layout figure, including ineligible/non-chart figures, in source order."""
    return _chart_figure_inventory(layout, checked_layout(layout))


def _chart_figure_inventory(layout, catalog):
    """Internal synchronous derivation from this call's checked layout catalog."""
    crops = {image.source_id: image for image in layout.crops}
    result = []
    for page in catalog['pages']:
        for unit in page['units']:
            if unit['kind'] != 'figure':
                continue
            _require(unit['page'] == page['page'] and unit['unit_id'] in crops,
                'Every figure requires its original checked layout crop')
            _require(unit.get('crop_sha256', sha256(crops[unit['unit_id']].data).hexdigest())
                == sha256(crops[unit['unit_id']].data).hexdigest(), 'Original layout figure crop changed')
            result.append(dict(unit_id=unit['unit_id'], page=unit['page'], label=unit['label'],
                bbox=list(unit['bbox']), crop_sha256=sha256(crops[unit['unit_id']].data).hexdigest()))
    _require(len({row['unit_id'] for row in result}) == len(result), 'Duplicate layout figure ownership')
    return tuple(result)


def _page_label_bounds(layout, document, unit):
    page = layout.source.pages[unit['page']-1]
    geometry = document.pages[unit['page']-1]
    box = unit['bbox']
    pixels = [max(0, math.floor(box[0]*page.width/geometry.width)),
        max(0, math.floor(box[1]*page.height/geometry.height)),
        min(page.width, math.ceil(box[2]*page.width/geometry.width)),
        min(page.height, math.ceil(box[3]*page.height/geometry.height))]
    _require(unit.get('crop_pixel_bbox', pixels) == pixels,
        'Full-page label scope differs from the original layout crop')
    return page, pixels


def _appearance(raw, page, metadata, png, python, limits, policy, budget):
    header = _json(dict(version=1, action='appearance', raw_bytes=len(raw),
        raw_sha256=sha256(raw).hexdigest(), png_bytes=len(png), png_sha256=sha256(png).hexdigest(),
        page=page, native_metadata=metadata, limits=asdict(limits))).encode('utf-8')
    _require(len(header) <= limits.manifest_bytes, 'Complete appearance header exceeds budget')
    script = Path(__file__).resolve().parents[1] / '_document_pdf_chart_preparation_worker.py'
    output, _, _, _ = _run_worker(python, script,
        (struct.pack('>I', len(header)), header, raw, png),
        policy=replace(policy, timeout_seconds=budget.remaining(policy.timeout_seconds)),
        output_bytes=limits.manifest_bytes, cancel_event=budget)
    try:
        value = native_input._load(output)
    except native_input.PdfNativeChartInputError as error:
        raise PdfChartPreparationError('Appearance worker protocol rejected') from error
    _require(type(value) is dict and value.get('version') == 'pdf-native-opaque-appearance-check-v1'
        and type(value.get('eligibility')) is bool
        and value.get('semantic_accuracy_verified') is False, 'Appearance worker protocol rejected')
    if value['eligibility']:
        _require(value.get('raw_pdf_sha256') == sha256(raw).hexdigest()
            and value.get('page') == page and value.get('object_ordinal') == metadata['object_ordinal']
            and value.get('png_sha256') == metadata['png_sha256'] == sha256(png).hexdigest()
            and value.get('pixel_sha256') == metadata['pixel_sha256']
            and type(value.get('page_bbox')) is list and len(value['page_bbox']) == 4
            and all(abs(a-b) <= .001 for a, b in zip(value['page_bbox'], metadata['page_bbox']))
            and value.get('dimensions') == [metadata['width'], metadata['height']],
            'Appearance worker source pins changed')
    else:
        _require(type(value.get('reason')) is str and 0 < len(value['reason']) <= 256,
            'Explicit native appearance fallback required')
    budget.is_set()
    return value


def _scope(native, unit, metadata, budget):
    rows = []
    for view, target, context in native_figure_views(native, page=unit['page'], bbox=unit['bbox']):
        budget.is_set()
        _require(sha256(target.data).hexdigest() == view['encoded_sha256'], 'Native column pixels changed')
        rows.append(dict(alias='V'+str(view['ordinal']), unit_id=unit['unit_id'],
            view_id=view['view_id'], kind=unit['kind'], page=unit['page'], label=unit['label'],
            caption=unit.get('caption_context', unit['text']), caption_association=unit['association'],
            image_mode='native_source_column_with_context',
            source_view=dict(ordinal=view['ordinal'], count=view['count'], pixel_bbox=view['pixel_bbox'],
                source_dimensions=[metadata['width'], metadata['height']], target_image_index=0,
                context_image_index=1, context_only=True, axes_calibrated=False, legend_ownership_proven=False),
            native_view_metadata=view))
    return dict(visual_units=rows) if rows else None


def _checked_page_crop(layout, document, unit, limits, budget):
    """Check an exact appearance crop; it is never a native image resource."""
    from io import BytesIO
    from PIL import Image
    budget.is_set()
    page, box = _page_label_bounds(layout, document, unit)
    image = next((c for c in layout.crops if c.source_id == unit['unit_id']), None)
    _require(image is not None and sha256(image.data).hexdigest()
        == unit.get('crop_sha256', sha256(image.data).hexdigest()),
        'Checked page crop identity changed')
    with native_input._source(image.data, limits.ocr) as pixels:
        _require(list(pixels.size) == [box[2]-box[0], box[3]-box[1]],
            'Checked page crop dimensions changed')
        with Image.open(BytesIO(page.image.data)) as full, full.crop(box) as original:
            _require(pixels.mode == original.mode and pixels.tobytes() == original.tobytes(),
                'Checked page crop differs from original page pixels')
        pixel_sha = sha256(pixels.tobytes()).hexdigest()
    budget.is_set()
    return image, dict(version='pdf-checked-page-chart-crop-v1', unit_id=unit['unit_id'],
        page=page.page, page_source=page.identity(), page_bbox=list(unit['bbox']),
        page_pixel_bbox=box, crop_png_sha256=sha256(image.data).hexdigest(),
        crop_pixel_sha256=pixel_sha, dimensions=[box[2]-box[0], box[3]-box[1]],
        source_pdf_sha256=layout.source.geometry.raw_sha256, coordinate_frame='crop-local',
        allow_antialiased_axes=True, native_resource=False, semantic_accuracy_verified=False)


def _roi_metadata_reason(layout, unit):
    # The existing ROI primitive's eligibility is deliberately narrower than
    # arbitrary page crops. Do not guess a caption or borrow a nearby rectangle.
    if (unit.get('status') != 'region' or unit.get('association') != 'spatial_only'
            or unit.get('reason') != 'preceding_native_image'
            or type(unit.get('caption_context', unit.get('text'))) is not str
            or not unit.get('caption_context', unit.get('text', '')).strip()):
        return 'unsupported_checked_caption_region'
    catalog = checked_layout(layout)
    same_box = [u for u in catalog['pages'][unit['page']-1]['units']
        if u['kind'] != 'table' and u['bbox'] == unit['bbox']]
    return 'ambiguous_checked_figure_region' if len(same_box) != 1 else None


def _roi_unsupported(layout, unit, reason):
    return dict(version='pdf-direct-page-chart-roi-ineligible-v1', dpi=288,
        unit_id=unit['unit_id'], page=unit['page'], page_bbox=list(unit['bbox']),
        unit_sha256=_hash(_json(unit)), layout_catalog_sha256=_hash(layout.catalog_json),
        source_pdf_sha256=layout.source.geometry.raw_sha256, reason=reason)


def _checked_direct_roi(layout, raw_pdf, unit, python, limits, policy, budget):
    budget.is_set()
    reason = _roi_metadata_reason(layout, unit)
    if reason is not None:
        return None, _roi_unsupported(layout, unit, reason)
    roi_limits = PdfFigureRoiLimits(source_bytes=min(67_108_864, limits.maximum_input_bytes),
        image_pixels=limits.ocr.source_pixels, image_bytes=limits.ocr.image_bytes)
    try:
        roi = prepare_figure_roi(layout, raw_pdf, page=unit['page'], unit_id=unit['unit_id'],
            python_executable=python, dpi=288, limits=roi_limits,
            process_policy=replace(policy, timeout_seconds=budget.remaining(min(60, policy.timeout_seconds))),
            cancel_event=budget)
    except PdfFigureRoiIneligible as error:
        return None, _roi_unsupported(layout, unit, error.reason)
    descriptor = checked_figure_roi(roi)
    _require(roi.layout is layout and descriptor['dpi'] == 288
        and descriptor['unit_id'] == unit['unit_id'] and descriptor['page'] == unit['page']
        and descriptor['bbox'] == unit['bbox'], 'Checked direct ROI ownership changed')
    budget.input_bytes += len(roi.image.data)
    _require(budget.input_bytes <= limits.maximum_input_bytes,
        'Complete direct ROI preparation input exceeds budget')
    budget.is_set()
    return roi.image, dict(version='pdf-checked-direct-chart-roi-v1', unit_id=unit['unit_id'],
        page=unit['page'], page_source=layout.source.pages[unit['page']-1].identity(),
        page_bbox=list(unit['bbox']), page_pixel_bbox=descriptor['page_pixel_bbox'],
        crop_png_sha256=descriptor['encoded_sha256'], crop_pixel_sha256=descriptor['pixel_sha256'],
        dimensions=[descriptor['width'], descriptor['height']],
        source_pdf_sha256=layout.source.geometry.raw_sha256, coordinate_frame='roi-local',
        allow_antialiased_axes=True, native_resource=False, semantic_accuracy_verified=False,
        roi_source_id=roi.image.source_id, roi_descriptor=descriptor)


def _observation_descriptor(raw, report, limits, source_kind=None):
    descriptor = dict(raw_ocr_stdout_sha256=sha256(raw).hexdigest(),
        ocr=native_input._compact_report(report))
    if source_kind is not None:
        descriptor.update(source_kind=source_kind, allow_antialiased_axes=True)
    _require(len(_json(descriptor).encode('utf-8')) <= limits.ocr.descriptor_bytes,
        'Complete retained observation OCR descriptor exceeds budget')
    return descriptor


def _prepare_page_crop(layout, document, unit, row, source_pages, language, limits, budget, samples,
        *, raw_pdf=None, python=None, policy=None, dpi=144):
    if dpi == 144:
        image, relation = _checked_page_crop(layout, document, unit, limits, budget)
    else:
        image, relation = _checked_direct_roi(layout, raw_pdf, unit, python, limits, policy, budget)
        if image is None:
            row['provenance'].update(page_appearance_roi_unsupported=relation,
                native_fallback_reason=row['reason'])
            return 0
    row['provenance'].update(page_appearance_crop=relation, native_fallback_reason=row['reason'])
    with native_input._source(image.data, limits.ocr) as pixels:
        raw = native_input.read_native_chart_ocr(image.data, language=language,
            limits=limits.ocr, cancel_event=budget)
        row['raw_ocr_stdout'] = raw
        report = native_input._report(raw, relation['crop_png_sha256'], pixels.size, language, limits.ocr)
        if row['provenance'].get('writer_input_version') == WRITER_INPUT_VERSION:
            budget.is_set()
            row['provenance']['ocr_descriptor'] = _observation_descriptor(raw, report, limits,
                'checked_page_appearance_crop' if dpi == 144 else 'direct_pdf_appearance_roi')
            row['reason'] = CURVE_FITTING_NOT_REQUESTED
            return 0
        try:
            bindings, atlas, _, _, _ = native_input._pipeline(image.data, pixels, report,
                limits.ocr, budget, allow_antialiased_axes=True)
        except native_input.PdfNativeChartInputError as error:
            if error.reason not in {'no_complete_rectangular_panel_candidates',
                    'unresolved_original_condition_anchors', 'unresolved_or_grayscale_legend_candidates'}:
                raise
            row['provenance']['crop_unsupported_reason'] = error.reason
            return 0
    source_kind = 'checked_page_appearance_crop' if dpi == 144 else 'direct_pdf_appearance_roi'
    row['provenance'].update(ocr_descriptor=dict(source_kind=source_kind,
        allow_antialiased_axes=True, raw_ocr_stdout_sha256=sha256(raw).hexdigest(),
        ocr=native_input._compact_report(report)), bindings_metadata=json.loads(bindings.metadata_json),
        atlas_metadata=json.loads(atlas.metadata_json))
    _require(len(_json(row['provenance']['ocr_descriptor']).encode('utf-8')) <= limits.ocr.descriptor_bytes,
        'Complete retained crop OCR descriptor exceeds budget')
    rotated = read_rotated_y_strip_ocr(image.data, atlas,
        expected_source_sha256=relation['crop_png_sha256'],
        expected_atlas_metadata_sha256=atlas.identity()['metadata_sha256'], language=language,
        limits=limits.rotated, cancel_event=budget,
        **({'bindings':bindings, 'axis_fragment_recipe_version':AXIS_FRAGMENT_RECIPE_VERSION}
            if row['provenance'].get('axis_fragment_recipe_version') == AXIS_FRAGMENT_RECIPE_VERSION else {}))
    row['raw_mosaic_ocr_stdout'] = rotated.raw_mosaic_ocr_stdout
    support = resolve_rotated_literal_support(rotated, source_pages,
        source_pdf_sha256=layout.source.geometry.raw_sha256, expected_page_count=len(source_pages),
        expected_reading_sha256=rotated.identity()['metadata_sha256'], cancel_event=budget)
    row['provenance'].update(rotated_ocr_metadata=json.loads(rotated.metadata_json),
        literal_support_metadata=json.loads(support.metadata_json),
        literal_support_text_kind='all_sealed_geometry_text_and_table_cells_not_semantic_completeness')
    _require(samples < limits.maximum_samples, 'Complete sample inventory exceeds budget')
    effective = _effective_for_row(image.data, bindings, rotated, row, budget)
    reading = read_complete_source_chart(image.data, effective, support,
        expected_source_sha256=relation['crop_png_sha256'],
        expected_bindings_sha256=effective.identity()['metadata_sha256'],
        expected_literal_support_sha256=support.identity()['metadata_sha256'],
        expected_source_pdf_sha256=layout.source.geometry.raw_sha256, visual_scope=None,
        source_object_id=None, expected_visual_scope_sha256=None, scale_hypothesis='linear',
        panel_local_metrics=True, source_local_legend_paint=True,
        maximum_samples=limits.maximum_samples-samples, maximum_pixel_tests=limits.maximum_pixel_tests,
        maximum_seconds=limits.maximum_reading_seconds, maximum_metadata_bytes=limits.maximum_reading_bytes,
        cancel_event=budget, retain_visible_color_ranges=True,
        **({'limit_marker_window_to_neighbours': True}
            if row['provenance'].get('writer_input_version') == CONDITIONAL_WRITER_INPUT_VERSION else {}),
        **({'allow_thin_component_bands': True}
            if row['provenance'].get('source_geometry_input_version') == SOURCE_GEOMETRY_INPUT_VERSION else {}))
    row.update(status='read', reason=None, reading=reading)
    return json.loads(reading.metadata_json)['sample_count']



def _prepare_native_text_presentation(layout,raw_pdf,python,limits,policy,budget):
    """Fresh only; existing isolated process memory/deadline/output controls."""
    geometry=layout.source.geometry
    _require(type(raw_pdf) is bytes and len(raw_pdf)==geometry.raw_bytes
        and sha256(raw_pdf).hexdigest()==geometry.raw_sha256,'Native original bytes differ')
    maximum=min(limits.maximum_supplement_bytes,geometry.limits.max_manifest_bytes)
    header=_json(dict(version=NATIVE_TEXT_PRESENTATION_VERSION,raw_bytes=len(raw_pdf),
        raw_sha256=geometry.raw_sha256,manifest_bytes=len(geometry.geometry_json),
        manifest_sha256=sha256(geometry.geometry_json).hexdigest(),limits=asdict(geometry.limits),
        maximum_output_bytes=maximum)).encode()
    _require(len(header)<=8192,'Native header exceeds its process budget')
    output,_,_,_=_run_worker(python,Path(__file__).with_name('_pdf_native_flow_worker.py'),
        (struct.pack('!I',len(header)),header,raw_pdf,geometry.geometry_json),
        policy=replace(policy,timeout_seconds=budget.remaining(policy.timeout_seconds)),
        output_bytes=maximum,cancel_event=budget)
    budget.is_set()
    value=json.loads(output.decode('utf-8'))
    _require(_json(value).encode()==output,'Canonical native worker output required')
    from .pdf_chart_supplement import _native_text_record
    return _native_text_record(value,layout.source.identity(),budget)


def prepare_pdf_chart_supplement(layout, raw_pdf, *, python_executable, language,
        limits=PdfChartPreparationLimits(), process_policy=PdfProcessPolicy(), cancel_event=None,
        allow_page_appearance_crops=False, page_appearance_dpi=144, writer_input_version=None,
        source_geometry_input_version=None, axis_fragment_recipe_version=None, literal_context_version=None):
    """One fresh original/rotated OCR and complete reading per eligible figure.

    Missing chart geometry, ambiguous placement and unsupported appearance yield
    explicit fallback. Corrupt protocols, source mismatch, cancellation and work
    budgets fail the whole preparation, never a successful partial inventory.
    Exact checked-page crops are a distinct, explicitly opt-in reading lane.
    The existing source-observations writer mode retains original OCR instead
    of computing curve candidates; this is not a geometry failure or accuracy.
    The explicit forward conditional-fit mode uses the same bounded reader,
    retaining approximations and nulls separately from raw OCR ownership.
    """
    _require(type(allow_page_appearance_crops) is bool
        and type(page_appearance_dpi) is int and page_appearance_dpi in (144, 288)
        and type(limits) is PdfChartPreparationLimits and type(process_policy) is PdfProcessPolicy
        and type(language) is str and native_input._LANGUAGE.fullmatch(language) is not None,
        'Typed limits/process policy and explicit OCR language required')
    _require(writer_input_version is None or (type(writer_input_version) is str
        and writer_input_version in (WRITER_INPUT_VERSION, CONDITIONAL_WRITER_INPUT_VERSION)),
        'Explicit supported writer input version required')
    _require(axis_fragment_recipe_version is None or (type(axis_fragment_recipe_version) is str
        and axis_fragment_recipe_version == AXIS_FRAGMENT_RECIPE_VERSION
        and source_geometry_input_version == SOURCE_GEOMETRY_INPUT_VERSION
        and writer_input_version == CONDITIONAL_WRITER_INPUT_VERSION),
        'Explicit supported conditional source-fragment recipe required')
    _require(source_geometry_input_version is None or (type(source_geometry_input_version) is str
        and source_geometry_input_version == SOURCE_GEOMETRY_INPUT_VERSION
        and writer_input_version == CONDITIONAL_WRITER_INPUT_VERSION),
        'Explicit supported conditional source geometry input required')
    _require(literal_context_version is None or (type(literal_context_version) is str
        and literal_context_version in (LITERAL_CONTEXT_VERSION, LITERAL_CONTEXT_WRITER_VERSION, LITERAL_CONTEXT_TABLE_VERSION, LITERAL_CONTEXT_NATIVE_VERSION, LITERAL_CONTEXT_KEY_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)
        and (writer_input_version == CONDITIONAL_WRITER_INPUT_VERSION
            if literal_context_version not in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION) else writer_input_version is None)),
        'Explicit supported literal context selection required')
    budget = _Budget(limits.maximum_seconds, cancel_event)
    budget.is_set()
    catalog = checked_layout(layout)
    document = checked_visual_source(layout.source)
    geometry = layout.source.geometry
    _require(type(raw_pdf) is bytes and len(raw_pdf) == geometry.raw_bytes
        and sha256(raw_pdf).hexdigest() == geometry.raw_sha256, 'Original complete PDF bytes changed')
    inventory = chart_figure_inventory(layout)
    _require(len(inventory) <= limits.maximum_figures, 'Complete figure inventory exceeds budget')
    source_identity, layout_identity = layout.source.identity(), layout.identity()
    input_bytes = len(raw_pdf)+len(geometry.geometry_json)+len(layout.catalog_json.encode('utf-8'))
    input_bytes += sum(len(page.image.data) for page in layout.source.pages)+sum(len(c.data) for c in layout.crops)
    _require(input_bytes <= limits.maximum_input_bytes, 'Complete preparation input exceeds budget')
    native_text_presentation = (_prepare_native_text_presentation(layout,raw_pdf,python_executable,limits,process_policy,budget)
        if literal_context_version in (LITERAL_CONTEXT_NATIVE_VERSION,LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION) else None)
    if literal_context_version in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION):
        _require(writer_input_version is None and source_geometry_input_version is None
            and axis_fragment_recipe_version is None and allow_page_appearance_crops is False,
            'Key-information cannot also request OCR/point fitting or ROI fallback')
        rows = [dict(unit_id=original['unit_id'],status='source_images',reason=KEY_INFORMATION_REASON,
            reading=None,raw_ocr_stdout=b'',raw_mosaic_ocr_stdout=b'',
            provenance=dict(preparation_version=VERSION,generation_limits=asdict(limits),
                key_information_source=dict(version=KEY_INFORMATION_SOURCE_VERSION,
                    source_pdf_sha256=source_identity['geometry']['raw_sha256'],
                    layout_catalog_sha256=layout_identity['catalog_sha256'],
                    unit_id=original['unit_id'],page=original['page'],
                    page_source=source_identity['pages'][original['page']-1],
                    figure_inventory_sha256=sha256(_json(original).encode()).hexdigest(),
                    exhaustive_point_recovery_requested=False,
                    semantic_accuracy_verified=False,numerical_accuracy_verified=False)))
            for original in inventory]
        budget.is_set()
        _require(layout.source.identity() == source_identity and layout.identity() == layout_identity,
            'Fresh key-information source/layout changed')
        checked_layout(layout)
        return prepare_chart_supplement(source_identity=source_identity,layout_identity=layout_identity,
            figure_inventory=inventory,figures=tuple(rows),maximum_input_bytes=limits.maximum_supplement_bytes,
            cancel_event=budget,literal_context_version=literal_context_version,
            native_text_presentation=native_text_presentation)
    # All retained text and inferred-table cells, across every sealed source page.
    # No normalization; these are extracted lexical support, never absence proof.
    source_pages = tuple('\n'.join(block.text if hasattr(block, 'text') else
        '\n'.join(cell.text for cell in block.cells) for block in page.blocks) for page in document.pages)
    rows, samples, page_labels, retained_label_bytes = [], 0, {}, 0
    if inventory:
        native = layout.native_figures
        figure_pages = tuple(sorted({row['page'] for row in inventory}))
        if native is None or not set(figure_pages).issubset(
                {page['page'] for page in checked_native_figures(native)['pages']}):
            native = prepare_native_figures(layout.source, raw_pdf, python_executable=python_executable,
                pages=figure_pages, limits=limits.native,
                process_policy=replace(process_policy, timeout_seconds=budget.remaining(process_policy.timeout_seconds)),
                cancel_event=budget)
        checked_native_figures(native)
        _require(native.source is layout.source, 'Native detail source ownership changed')
        input_bytes += len(native.catalog_json.encode('utf-8'))+sum(len(image.data) for image in native.images)
        _require(input_bytes <= limits.maximum_input_bytes, 'Complete native preparation input exceeds budget')
        budget.input_bytes = input_bytes
        units = {unit['unit_id']: unit for page in catalog['pages'] for unit in page['units']}
        for original in inventory:
            budget.is_set()
            row = dict(unit_id=original['unit_id'], status='unsupported', reason=None, reading=None,
                provenance=dict(preparation_version=VERSION, generation_limits=asdict(limits)),
                raw_ocr_stdout=b'', raw_mosaic_ocr_stdout=b'')
            if writer_input_version is not None:
                row['provenance']['writer_input_version'] = writer_input_version
            if source_geometry_input_version is not None:
                row['provenance']['source_geometry_input_version'] = source_geometry_input_version
            if axis_fragment_recipe_version is not None:
                row['provenance']['axis_fragment_recipe_version'] = axis_fragment_recipe_version
            rows.append(row)
            unit = units[original['unit_id']]
            match = find_native_figure(native, page=original['page'], bbox=original['bbox'])
            if match is None:
                row['reason'] = 'no_unique_same_source_native_placement'
                if allow_page_appearance_crops:
                    samples += _prepare_page_crop(layout, document, unit, row, source_pages,
                        language, limits, budget, samples, raw_pdf=raw_pdf, python=python_executable,
                        policy=process_policy, dpi=page_appearance_dpi)
                continue
            metadata, image = match
            row['provenance'].update(native_identity=native.identity(), native_metadata=metadata)
            if not metadata.get('panels'):
                row['reason'] = 'no_complete_rectangular_chart_panel_candidates'
                if allow_page_appearance_crops:
                    samples += _prepare_page_crop(layout, document, unit, row, source_pages,
                        language, limits, budget, samples, raw_pdf=raw_pdf, python=python_executable,
                        policy=process_policy, dpi=page_appearance_dpi)
                continue
            appearance = _appearance(raw_pdf, original['page'], metadata, image.data,
                python_executable, limits.native, process_policy, budget)
            row['provenance']['appearance'] = appearance
            if not appearance['eligibility']:
                row['reason'] = 'native_appearance_unsupported:'+appearance['reason']
                _require(len(row['reason']) <= 256, 'Complete unsupported reason exceeds budget')
                if allow_page_appearance_crops:
                    samples += _prepare_page_crop(layout, document, unit, row, source_pages,
                        language, limits, budget, samples, raw_pdf=raw_pdf, python=python_executable,
                        policy=process_policy, dpi=page_appearance_dpi)
                    if row['status'] == 'read' or page_appearance_dpi == 288:
                        continue
                page, pixels = _page_label_bounds(layout, document, unit)
                if page.page not in page_labels:
                    observations = prepare_page_label_ocr(page.image.data,
                        expected_source_sha256=page.identity()['sha256'], language=language,
                        limits=limits.ocr, cancel_event=budget)
                    page_labels[page.page] = retain_page_label_ocr(observations)
                node = dict(page_label_source=page.identity(),
                    page_label_figure_pixel_bbox=pixels, page_label_ocr=page_labels[page.page])
                retained_label_bytes += len(_json(node).encode('utf-8'))
                _require(retained_label_bytes <= limits.maximum_supplement_bytes,
                    'Complete retained page observations exceed supplement budget')
                row['provenance'].update(node)
                continue
            scope = _scope(native, unit, metadata, budget)
            row['provenance']['visual_scope'] = scope
            with native_input._source(image.data, limits.ocr) as pixels:
                checked = native_input._appearance(appearance, image.data, pixels, limits.ocr)
                raw = native_input.read_native_chart_ocr(image.data, language=language,
                    limits=limits.ocr, cancel_event=budget)
                row['raw_ocr_stdout'] = raw
                report = native_input._report(raw, sha256(image.data).hexdigest(), pixels.size, language, limits.ocr)
                if writer_input_version == WRITER_INPUT_VERSION:
                    budget.is_set()
                    row['provenance']['ocr_descriptor'] = _observation_descriptor(raw, report, limits)
                    row['reason'] = CURVE_FITTING_NOT_REQUESTED
                    continue
                try:
                    bindings, atlas, legends, _, _ = native_input._pipeline(image.data, pixels, report, limits.ocr, budget)
                except native_input.PdfNativeChartInputError as error:
                    if error.reason not in {'no_complete_rectangular_panel_candidates',
                            'unresolved_original_condition_anchors', 'unresolved_or_grayscale_legend_candidates'}:
                        raise
                    row['reason'] = error.reason
                    continue
                descriptor = native_input._descriptor(image.data, pixels, checked, report,
                    sha256(raw).hexdigest(), bindings, atlas, legends, limits.ocr)
                _require(len(_json(descriptor).encode('utf-8')) <= limits.ocr.descriptor_bytes,
                    'Complete retained OCR descriptor exceeds budget')
            row['provenance'].update(ocr_descriptor=descriptor,
                bindings_metadata=json.loads(bindings.metadata_json), atlas_metadata=json.loads(atlas.metadata_json))
            rotated = read_rotated_y_strip_ocr(image.data, atlas,
                expected_source_sha256=sha256(image.data).hexdigest(),
                expected_atlas_metadata_sha256=atlas.identity()['metadata_sha256'], language=language,
                limits=limits.rotated, cancel_event=budget,
                **({'bindings':bindings, 'axis_fragment_recipe_version':AXIS_FRAGMENT_RECIPE_VERSION}
                    if row['provenance'].get('axis_fragment_recipe_version') == AXIS_FRAGMENT_RECIPE_VERSION else {}))
            row['raw_mosaic_ocr_stdout'] = rotated.raw_mosaic_ocr_stdout
            support = resolve_rotated_literal_support(rotated, source_pages, source_pdf_sha256=geometry.raw_sha256,
                expected_page_count=len(source_pages), expected_reading_sha256=rotated.identity()['metadata_sha256'],
                cancel_event=budget)
            row['provenance'].update(rotated_ocr_metadata=json.loads(rotated.metadata_json),
                literal_support_metadata=json.loads(support.metadata_json),
                literal_support_text_kind='all_sealed_geometry_text_and_table_cells_not_semantic_completeness')
            _require(samples < limits.maximum_samples, 'Complete sample inventory exceeds budget')
            effective = _effective_for_row(image.data, bindings, rotated, row, budget)
            reading = read_complete_source_chart(image.data, effective, support,
                expected_source_sha256=sha256(image.data).hexdigest(),
                expected_bindings_sha256=effective.identity()['metadata_sha256'],
                expected_literal_support_sha256=support.identity()['metadata_sha256'],
                expected_source_pdf_sha256=geometry.raw_sha256, visual_scope=scope,
                source_object_id=metadata['source_id'], expected_visual_scope_sha256=_hash(_json(scope)) if scope else None,
                scale_hypothesis='linear', maximum_samples=limits.maximum_samples-samples,
                maximum_pixel_tests=limits.maximum_pixel_tests, maximum_seconds=limits.maximum_reading_seconds,
                maximum_metadata_bytes=limits.maximum_reading_bytes, cancel_event=budget,
                retain_visible_color_ranges=True,
                **({'limit_marker_window_to_neighbours': True}
                    if writer_input_version == CONDITIONAL_WRITER_INPUT_VERSION else {}),
                **({'allow_thin_component_bands': True}
                    if source_geometry_input_version == SOURCE_GEOMETRY_INPUT_VERSION else {}))
            samples += json.loads(reading.metadata_json)['sample_count']
            row.update(status='read', reason=None, reading=reading)
    budget.is_set()
    _require(layout.source.identity() == source_identity and layout.identity() == layout_identity,
        'Fresh source/layout changed during chart preparation')
    checked_layout(layout)
    result = prepare_chart_supplement(source_identity=source_identity, layout_identity=layout_identity,
        figure_inventory=inventory, figures=tuple(rows), maximum_input_bytes=limits.maximum_supplement_bytes,
        cancel_event=budget, literal_context_version=literal_context_version,
        native_text_presentation=native_text_presentation)
    budget.is_set()
    return result


def _restore_page_crop(layout, document, unit, row, language, limits, budget,
        *, raw_pdf=None, python=None, policy=None):
    import base64
    provenance = row['provenance']
    direct = 'page_appearance_roi_unsupported' in provenance or (
        provenance['page_appearance_crop']['version'] == 'pdf-checked-direct-chart-roi-v1')
    if direct:
        image, relation = _checked_direct_roi(layout, raw_pdf, unit, python, limits, policy, budget)
        if image is None:
            _require(row['status'] == 'unsupported'
                and provenance.get('page_appearance_roi_unsupported') == relation,
                'Retained direct ROI ineligibility changed')
            return
        _require('page_appearance_roi_unsupported' not in provenance,
            'Retained direct ROI eligibility changed')
    else:
        image, relation = _checked_page_crop(layout, document, unit, limits, budget)
    _require(_json(provenance['page_appearance_crop']) == _json(relation),
        'Retained checked page crop relation changed')
    if row['status'] == 'read':
        _require(len(_json(provenance['ocr_descriptor']).encode('utf-8')) <= limits.ocr.descriptor_bytes,
            'Complete retained crop OCR descriptor exceeds budget')
    raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
    with native_input._source(image.data, limits.ocr) as pixels:
        report = native_input._report(raw, relation['crop_png_sha256'], pixels.size, language, limits.ocr)
        if row['reason'] == CURVE_FITTING_NOT_REQUESTED:
            _require(provenance.get('writer_input_version') == WRITER_INPUT_VERSION
                and provenance['ocr_descriptor'] == _observation_descriptor(raw, report, limits,
                    'direct_pdf_appearance_roi' if direct else 'checked_page_appearance_crop'),
                'Retained uninterpreted crop OCR changed')
            budget.is_set()
            return
        try:
            bindings, atlas, _, _, _ = native_input._pipeline(image.data, pixels, report,
                limits.ocr, budget, allow_antialiased_axes=True)
        except native_input.PdfNativeChartInputError as error:
            _require(row['status'] == 'unsupported'
                and provenance.get('crop_unsupported_reason') == error.reason,
                'Retained unsupported crop geometry changed')
            return
    _require(row['status'] == 'read' and 'crop_unsupported_reason' not in provenance
        and provenance['ocr_descriptor'] == dict(source_kind=(
            'direct_pdf_appearance_roi' if direct else 'checked_page_appearance_crop'),
            allow_antialiased_axes=True, raw_ocr_stdout_sha256=sha256(raw).hexdigest(),
            ocr=native_input._compact_report(report))
        and provenance['bindings_metadata'] == json.loads(bindings.metadata_json)
        and provenance['atlas_metadata'] == json.loads(atlas.metadata_json),
        'Retained crop OCR geometry changed')
    raw_mosaic = base64.b64decode(row['raw_mosaic_ocr_stdout_base64'], validate=True)
    rotated = read_rotated_y_strip_ocr(image.data, atlas,
        expected_source_sha256=relation['crop_png_sha256'],
        expected_atlas_metadata_sha256=atlas.identity()['metadata_sha256'], language=language,
        limits=limits.rotated, ocr_reader=lambda *a, **k: raw_mosaic, cancel_event=budget,
        **({'bindings':bindings, 'axis_fragment_recipe_version':AXIS_FRAGMENT_RECIPE_VERSION}
            if row['provenance'].get('axis_fragment_recipe_version') == AXIS_FRAGMENT_RECIPE_VERSION else {}))
    source_pages = tuple('\n'.join(block.text if hasattr(block, 'text') else
        '\n'.join(cell.text for cell in block.cells) for block in page.blocks) for page in document.pages)
    support = resolve_rotated_literal_support(rotated, source_pages,
        source_pdf_sha256=layout.source.geometry.raw_sha256, expected_page_count=len(source_pages),
        expected_reading_sha256=rotated.identity()['metadata_sha256'], cancel_event=budget)
    _require(rotated.raw_mosaic_ocr_stdout == raw_mosaic
        and provenance['rotated_ocr_metadata'] == json.loads(rotated.metadata_json)
        and provenance['literal_support_metadata'] == json.loads(support.metadata_json),
        'Retained crop rotated observations changed')
    _effective_for_row(image.data, bindings, rotated, row, budget, restoring=True)


def restore_pdf_chart_supplement(layout, raw_pdf, retained_bytes, *, expected_identity,
        python_executable, language, limits=PdfChartPreparationLimits(),
        process_policy=PdfProcessPolicy(), cancel_event=None):
    """Bind unchanged retained observations to fresh original appearance, not truth.

    Native extraction/appearance and complete layout/scope association are fresh.
    Retained OCR and complete reading bytes are never rerun, repaired or refitted.
    Unsupported figures are checked against the same complete source inventory.
    """
    _require(type(limits) is PdfChartPreparationLimits and type(process_policy) is PdfProcessPolicy
        and type(language) is str and native_input._LANGUAGE.fullmatch(language) is not None,
        'Typed limits/process policy and explicit retained OCR language required')
    budget = _Budget(limits.maximum_seconds, cancel_event)
    budget.is_set()
    catalog = checked_layout(layout)
    geometry = layout.source.geometry
    _require(type(raw_pdf) is bytes and len(raw_pdf) == geometry.raw_bytes
        and sha256(raw_pdf).hexdigest() == geometry.raw_sha256, 'Original complete PDF bytes changed')
    _require(type(retained_bytes) is bytes and 1 <= len(retained_bytes) <= limits.maximum_supplement_bytes
        and type(expected_identity) is dict and expected_identity.get('bytes') == len(retained_bytes)
        and expected_identity.get('sha256') == sha256(retained_bytes).hexdigest(),
        'Complete retained chart bytes differ from their archive descriptor')
    try:
        supplement = PdfChartSupplement(retained_bytes.decode('utf-8'))
        inventory = _chart_figure_inventory(layout, catalog)
        _require(len(inventory) <= limits.maximum_figures, 'Complete retained figure inventory exceeds budget')
        source_identity, layout_identity = layout.source.identity(), layout.identity()
        record, retained_identity = _load_bound_chart_supplement(supplement,
            source_identity=source_identity, layout_identity=layout_identity, figure_inventory=inventory,
            expected_sha256=expected_identity['sha256'],
            maximum_input_bytes=limits.maximum_supplement_bytes, cancel_event=budget)
        _require(_json(retained_identity) == _json(expected_identity),
            'Complete retained chart identity changed')
    except PdfProcessError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise PdfChartPreparationError('Complete unchanged retained chart source required') from error
    input_bytes = len(raw_pdf)+len(geometry.geometry_json)+len(layout.catalog_json.encode('utf-8'))
    input_bytes += sum(len(page.image.data) for page in layout.source.pages)+sum(len(c.data) for c in layout.crops)
    _require(input_bytes <= limits.maximum_input_bytes, 'Complete retained preparation input exceeds budget')
    if record.get('literal_context_marker',{}).get('version') in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION):
        budget.is_set()
        checked_layout(layout)
        _require(all(_json(row['provenance']['generation_limits']) == _json(asdict(limits))
            for row in record['figures']), 'Retained key-information preparation limits changed')
        _require(layout.source.identity() == source_identity and layout.identity() == layout_identity,
            'Retained key-information source/layout changed')
        return supplement
    if inventory:
        # The key-information branch above needs no decoded document here.
        # Legacy appearance replay still performs its existing document check.
        document = checked_visual_source(layout.source)
        native = layout.native_figures
        figure_pages = tuple(sorted({row['page'] for row in inventory}))
        if native is None or not set(figure_pages).issubset(
                {page['page'] for page in checked_native_figures(native)['pages']}):
            native = prepare_native_figures(layout.source, raw_pdf, python_executable=python_executable,
                pages=figure_pages, limits=limits.native,
                process_policy=replace(process_policy, timeout_seconds=budget.remaining(process_policy.timeout_seconds)),
                cancel_event=budget)
        checked_native_figures(native)
        _require(native.source is layout.source, 'Retained native detail source ownership changed')
        input_bytes += len(native.catalog_json.encode('utf-8'))+sum(len(image.data) for image in native.images)
        _require(input_bytes <= limits.maximum_input_bytes, 'Complete retained native input exceeds budget')
        budget.input_bytes = input_bytes
        native_identity = native.identity()
        units = {unit['unit_id']: unit for page in catalog['pages'] for unit in page['units']}
        page_labels = {}
        for original, row in zip(inventory, record['figures'], strict=True):
            budget.is_set()
            provenance = row['provenance']
            _require(provenance.get('preparation_version') == VERSION
                and _json(provenance.get('generation_limits')) == _json(asdict(limits)),
                'Retained generation policy differs from current server policy')
            if 'page_label_ocr' in provenance:
                _require(provenance.get('preparation_version') == VERSION
                    and row['status'] == 'unsupported'
                    and row['reason'].startswith('native_appearance_unsupported:')
                    and provenance.get('appearance', {}).get('eligibility') is False,
                    'Full-page observations require the original unsupported appearance')
                page, pixels = _page_label_bounds(layout, document, units[original['unit_id']])
                _require(provenance.get('page_label_source') == page.identity()
                    and provenance.get('page_label_figure_pixel_bbox') == pixels,
                    'Retained full-page observations changed original source/scope')
                retained_labels = provenance['page_label_ocr']
                key = (page.page, _hash(_json(retained_labels)))
                if key not in page_labels:
                    restore_page_label_ocr(page.image.data, retained_labels,
                        expected_source_sha256=page.identity()['sha256'], language=language,
                        limits=limits.ocr, cancel_event=budget)
                    page_labels[key] = True
            match = find_native_figure(native, page=original['page'], bbox=original['bbox'])
            retained_native = provenance.get('native_metadata')
            if 'page_appearance_crop' in provenance or 'page_appearance_roi_unsupported' in provenance:
                fallback = provenance.get('native_fallback_reason')
                if retained_native is None:
                    _require(match is None and fallback == 'no_unique_same_source_native_placement',
                        'Retained crop native fallback changed')
                else:
                    _require(match is not None and provenance.get('native_identity') == native_identity
                        and _json(match[0]) == _json(retained_native),
                        'Retained crop native source changed')
                    if 'appearance' not in provenance:
                        _require(not match[0].get('panels')
                            and fallback == 'no_complete_rectangular_chart_panel_candidates',
                            'Retained crop native panels changed')
                    else:
                        appearance = _appearance(raw_pdf, original['page'], match[0], match[1].data,
                            python_executable, limits.native, process_policy, budget)
                        _require(appearance['eligibility'] is False
                            and _json(appearance) == _json(provenance['appearance'])
                            and fallback == 'native_appearance_unsupported:'+appearance['reason'],
                            'Retained crop native appearance changed')
                _restore_page_crop(layout, document, units[original['unit_id']], row,
                    language, limits, budget, raw_pdf=raw_pdf, python=python_executable,
                    policy=process_policy)
                continue
            if retained_native is None:
                _require(row['status'] == 'unsupported' and match is None
                    and row['reason'] == 'no_unique_same_source_native_placement',
                    'Retained unsupported native association changed')
                continue
            _require(match is not None and provenance.get('native_identity') == native_identity,
                'Retained complete native identity changed')
            metadata, image = match
            _require(_json(metadata) == _json(retained_native), 'Retained original native figure changed')
            if 'appearance' not in provenance:
                _require(row['status'] == 'unsupported' and not metadata.get('panels')
                    and row['reason'] == 'no_complete_rectangular_chart_panel_candidates',
                    'Retained unsupported panel inventory changed')
                continue
            appearance = _appearance(raw_pdf, original['page'], metadata, image.data,
                python_executable, limits.native, process_policy, budget)
            _require(_json(appearance) == _json(provenance['appearance']),
                'Retained original figure appearance changed')
            if 'visual_scope' in provenance:
                scope = _scope(native, units[original['unit_id']], metadata, budget)
                _require(_json(scope) == _json(provenance['visual_scope']),
                    'Retained complete original visual scope changed')
            else:
                _require(row['status'] == 'unsupported' and appearance['eligibility'] is False,
                    'Retained eligible original lost its visual scope')
            if row['reason'] == CURVE_FITTING_NOT_REQUESTED:
                import base64
                raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
                with native_input._source(image.data, limits.ocr) as pixels:
                    native_input._appearance(appearance, image.data, pixels, limits.ocr)
                    report = native_input._report(raw, sha256(image.data).hexdigest(),
                        pixels.size, language, limits.ocr)
                _require(provenance.get('writer_input_version') == WRITER_INPUT_VERSION
                    and provenance['ocr_descriptor'] == _observation_descriptor(raw, report, limits),
                    'Retained uninterpreted native OCR changed')
                budget.is_set()
            elif row['status'] == 'read' and provenance.get('axis_fragment_recipe_version') is not None:
                import base64
                raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
                with native_input._source(image.data, limits.ocr) as pixels:
                    checked = native_input._appearance(appearance, image.data, pixels, limits.ocr)
                    report = native_input._report(raw, sha256(image.data).hexdigest(), pixels.size, language, limits.ocr)
                    bindings, atlas, legends, _, _ = native_input._pipeline(image.data, pixels, report, limits.ocr, budget)
                    descriptor = native_input._descriptor(image.data, pixels, checked, report,
                        sha256(raw).hexdigest(), bindings, atlas, legends, limits.ocr)
                _require(provenance['ocr_descriptor'] == descriptor
                    and provenance['bindings_metadata'] == json.loads(bindings.metadata_json)
                    and provenance['atlas_metadata'] == json.loads(atlas.metadata_json),
                    'Retained native primary OCR geometry changed')
                raw_mosaic = base64.b64decode(row['raw_mosaic_ocr_stdout_base64'], validate=True)
                rotated = read_rotated_y_strip_ocr(image.data, atlas,
                    expected_source_sha256=sha256(image.data).hexdigest(),
                    expected_atlas_metadata_sha256=atlas.identity()['metadata_sha256'], language=language,
                    limits=limits.rotated, ocr_reader=lambda *a, **k:raw_mosaic, cancel_event=budget,
                    bindings=bindings, axis_fragment_recipe_version=provenance['axis_fragment_recipe_version'])
                source_pages = tuple('\n'.join(block.text if hasattr(block, 'text') else
                    '\n'.join(cell.text for cell in block.cells) for block in page.blocks) for page in document.pages)
                support = resolve_rotated_literal_support(rotated, source_pages,
                    source_pdf_sha256=geometry.raw_sha256, expected_page_count=len(source_pages),
                    expected_reading_sha256=rotated.identity()['metadata_sha256'], cancel_event=budget)
                _require(provenance['rotated_ocr_metadata'] == json.loads(rotated.metadata_json)
                    and provenance['literal_support_metadata'] == json.loads(support.metadata_json),
                    'Retained native secondary observations changed')
                _effective_for_row(image.data, bindings, rotated, row, budget, restoring=True)
    budget.is_set()
    checked_layout(layout)
    _require(layout.source.identity() == source_identity and layout.identity() == layout_identity,
        'Fresh source/layout changed during retained chart checks')
    checked_chart_supplement(supplement, source_identity=source_identity, layout_identity=layout_identity,
        figure_inventory=inventory, expected_sha256=expected_identity['sha256'],
        maximum_input_bytes=limits.maximum_supplement_bytes, cancel_event=budget)
    budget.is_set()
    return supplement


def _effective_for_row(source_png, bindings, rotated, row, budget, *, restoring=False):
    provenance = row['provenance']
    if provenance.get('axis_fragment_recipe_version') is None:
        _require('effective_bindings_metadata' not in provenance,
            'Unmarked primary archive cannot acquire secondary binding')
        return bindings
    effective = effective_secondary_x_bindings(source_png, bindings, rotated, cancel_event=budget)
    metadata = json.loads(effective.metadata_json)
    if restoring:
        _require(provenance.get('effective_bindings_metadata') == metadata
            and json.loads(row['reading_json'])['bindings_identity'] == effective.identity(),
            'Retained effective secondary bindings changed')
    else:
        provenance['effective_bindings_metadata'] = metadata
    return effective

