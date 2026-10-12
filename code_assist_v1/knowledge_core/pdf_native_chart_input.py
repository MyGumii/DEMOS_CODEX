"""Optional original-pixel OCR input preparation, not a publication policy.

OCR text/geometry and adjacent paint are untrusted observations. The caller must
obtain the appearance result from the bounded PDF checker again on cold restore.
No model, numerical calibration, chart/legend semantic proof or service is used.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import time

from PIL import Image

from .pdf_native_figure_source import detect_rectangular_panels, PdfNativeFigureError
from .pdf_chart_label_atlas import build_chart_label_atlas, PdfChartLabelAtlasError, PdfChartLabelAtlasLimits
from .pdf_chart_ocr_bindings import bind_chart_ocr_geometry, PdfChartOcrBindings, PdfChartOcrBindingsError, PdfChartOcrBindingsLimits
from .pdf_chart_source_reading import _legend_color

VERSION = 'pdf-native-chart-input-candidate-v1'
OCR_VERSION = 'pdf-native-source-winrt-ocr-v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_LANGUAGE = re.compile(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,3}\Z')
_APPEARANCE_VERSION = 'pdf-native-opaque-appearance-check-v1'
CONFIGURATION_BYTES = 65_536


class PdfNativeChartInputError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        self.requires_full_page_context = True
        super().__init__('Native chart preparation fallback: ' + reason)


@dataclass(frozen=True, slots=True)
class PdfNativeChartInputLimits:
    image_bytes: int = 16_777_216
    source_pixels: int = 16_000_000
    ocr_stdout_bytes: int = 1_048_576
    words: int = 4096
    panels: int = 64
    legend_scan_pixels: int = 4_000_000
    descriptor_bytes: int = 65_536
    context_bytes: int = 32_768
    timeout_seconds: int = 30

    def __post_init__(self):
        caps = (16_777_216,16_000_000,1_048_576,4096,64,4_000_000,65_536,32_768,60)
        if any(type(v) is not int or not 1 <= v <= cap
               for v,cap in zip(asdict(self).values(),caps)):
            raise PdfNativeChartInputError('invalid_limits')


@dataclass(frozen=True, slots=True)
class PreparedNativeChartInput:
    descriptor_json: str
    bindings: PdfChartOcrBindings
    legend_word_ids: tuple[str, ...]
    legend_candidates_json: str
    source_context: str
    canonical_ocr_report_json: str
    reserved_configuration_bytes: int

    def descriptor(self):
        return _load(self.descriptor_json)

    def identity(self):
        return {'version':VERSION,'descriptor_sha256':sha256(self.descriptor_json.encode()).hexdigest(),
            'descriptor_bytes':len(self.descriptor_json.encode()),
            'configuration_reserved_bytes':self.reserved_configuration_bytes,
            'configuration_accounted_bytes':len(self.descriptor_json.encode())+self.reserved_configuration_bytes,
            'bindings_sha256':self.bindings.identity()['metadata_sha256'],
            'semantic_accuracy_verified':False,'legend_ownership_proven':False,
            'candidate_discovery_exhaustive':False,'normal_workflow_integrated':False,
            'publication_authority':False}


def _fail(reason):
    raise PdfNativeChartInputError(reason)


def _json(value):
    try:
        return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError,UnicodeError,RecursionError) as error:
        raise PdfNativeChartInputError('invalid_json_data') from error


def _load(value):
    def pairs(rows):
        result = {}
        for key,item in rows:
            if key in result:
                _fail('duplicate_json_key')
            result[key] = item
        return result
    def constant(_):
        _fail('nonfinite_json_number')
    try:
        return json.loads(value,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,TypeError,UnicodeError,RecursionError) as error:
        if isinstance(error,PdfNativeChartInputError):
            raise
        raise PdfNativeChartInputError('invalid_json') from error


def _bounded_tree(value, maximum, *, depth=0, counter=None):
    """Bound OCR auxiliary data too; no flags in that data grant authority."""
    counter = [0] if counter is None else counter
    counter[0] += 1
    if depth > 12 or counter[0] > maximum:
        _fail('ocr_structure_budget')
    if value is None or type(value) in (str,int,bool):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail('nonfinite_json_number')
        return
    if type(value) not in (list,dict):
        _fail('invalid_json_data')
    items = value.values() if type(value) is dict else value
    for item in items:
        _bounded_tree(item,maximum,depth=depth+1,counter=counter)


def _budget(descriptor, reserved, limits):
    # Descriptor is stored as an OBJECT, not an escaped JSON string. The caller
    # supplies ALL other serialized configuration bytes, including field names,
    # commas, brackets and enclosing-object framing. Multiple figures share it.
    if type(reserved) is not int or not 2 <= reserved < CONFIGURATION_BYTES:
        _fail('invalid_configuration_reservation')
    if len(descriptor) > limits.descriptor_bytes:
        _fail('complete_configuration_budget')
    try:
        size = len(descriptor.encode())
    except UnicodeError as error:
        raise PdfNativeChartInputError('invalid_descriptor_utf8') from error
    if size > limits.descriptor_bytes or size+reserved > CONFIGURATION_BYTES:
        _fail('complete_configuration_budget')


def _source(png, limits):
    if type(png) is not bytes or not 0 < len(png) <= limits.image_bytes:
        _fail('invalid_source_png_budget')
    try:
        image = Image.open(BytesIO(png))
        if image.format != 'PNG' or image.mode != 'RGB' or image.width*image.height > limits.source_pixels:
            image.close()
            _fail('unsupported_source_png')
        image.load()
        return image
    except (OSError,ValueError,Image.DecompressionBombError) as error:
        if isinstance(error,PdfNativeChartInputError):
            raise
        raise PdfNativeChartInputError('invalid_source_png') from error


def _appearance(value, png, image, limits):
    if (type(value) is not dict or value.get('version') != _APPEARANCE_VERSION
            or value.get('eligibility') is not True or value.get('requires_full_page_context') is not False
            or value.get('complete_page_text_retained') is not True
            or value.get('semantic_accuracy_verified') is not False):
        _fail('unsupported_or_unchecked_appearance')
    if (value.get('png_sha256') != sha256(png).hexdigest()
            or value.get('pixel_sha256') != sha256(image.tobytes()).hexdigest()
            or value.get('dimensions') != list(image.size)
            or type(value.get('raw_pdf_sha256')) is not str or not _SHA.fullmatch(value['raw_pdf_sha256'])
            or type(value.get('page')) is not int or value['page'] < 1
            or type(value.get('object_ordinal')) is not int or value['object_ordinal'] < 1):
        _fail('appearance_source_identity_mismatch')
    for name,size in (('matrix',6),('page_bbox',4)):
        row = value.get(name)
        if (type(row) is not list or len(row) != size
                or any(type(n) not in (int,float) or not math.isfinite(n) for n in row)):
            _fail('invalid_appearance_geometry')
    text = value.get('source_context')
    if (type(text) is not str or len(text.encode()) > limits.context_bytes
            or value.get('source_context_sha256') != sha256(text.encode()).hexdigest()):
        _fail('complete_source_context_mismatch_or_budget')
    if len(_json(value).encode()) > limits.ocr_stdout_bytes:
        _fail('appearance_metadata_budget')
    return _load(_json(value))


def _report(raw, source_sha, size, language, limits):
    if type(language) is not str or not _LANGUAGE.fullmatch(language):
        _fail('invalid_original_ocr_language')
    if type(raw) is not bytes or not 0 < len(raw) <= limits.ocr_stdout_bytes:
        _fail('ocr_stdout_budget')
    value = _load(raw)
    _bounded_tree(value,limits.words*24+4096)
    if (type(value) is not dict or value.get('source_sha256') != source_sha
            or value.get('source_dimensions') != list(size) or value.get('source_resized') is not False
            or value.get('language') != language or type(value.get('lines')) is not list
            or len(value['lines']) > limits.words or type(value.get('word_count')) is not int):
        _fail('ocr_original_identity_or_language_mismatch')
    count = 0
    for line in value['lines']:
        if (type(line) is not dict or set(line) != {'text','words'}
                or type(line['text']) is not str or len(line['text']) > 4096
                or type(line['words']) is not list):
            _fail('invalid_original_ocr_line')
        for word in line['words']:
            if type(word) is not dict or set(word) != {'text','bbox'}:
                _fail('invalid_original_ocr_word')
            count += 1
            if count > limits.words:
                _fail('complete_ocr_word_budget')
    if count != value['word_count']:
        _fail('complete_ocr_word_count_mismatch')
    return value


def _compact_report(report):
    fixed = {'lines','source_sha256','source_dimensions','source_resized','word_count','language'}
    return {'language':report['language'],
        'lines':[[line['text'],[[w['text'],w['bbox']] for w in line['words']]] for line in report['lines']],
        'extra':{key:value for key,value in report.items() if key not in fixed}}


def _expand_report(compact, source_sha, size, limits):
    if type(compact) is not dict or set(compact) != {'language','lines','extra'}:
        _fail('invalid_compact_ocr_fields')
    if type(compact['extra']) is not dict or type(compact['lines']) is not list:
        _fail('invalid_compact_ocr_rows')
    fixed = {'lines','source_sha256','source_dimensions','source_resized','word_count','language'}
    if fixed & set(compact['extra']):
        _fail('compact_ocr_extra_overrides_source')
    lines = []
    for row in compact['lines']:
        if type(row) is not list or len(row) != 2 or type(row[1]) is not list:
            _fail('invalid_compact_ocr_line')
        words = []
        for word in row[1]:
            if type(word) is not list or len(word) != 2:
                _fail('invalid_compact_ocr_word')
            words.append({'text':word[0],'bbox':word[1]})
        lines.append({'text':row[0],'words':words})
    report = {**compact['extra'],'language':compact['language'],'lines':lines,
        'source_sha256':source_sha,'source_dimensions':list(size),'source_resized':False,
        'word_count':sum(len(line['words']) for line in lines)}
    encoded = _json(report)
    return _report(encoded.encode(),source_sha,size,compact['language'],limits)


def _pipeline(png, image, report, limits, cancel_event, *, allow_antialiased_axes=False):
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            _fail('cancelled')
    try:
        cancelled()
        options = {} if allow_antialiased_axes is False else {'allow_antialiased_axes':allow_antialiased_axes}
        panels = detect_rectangular_panels(image,maximum=limits.panels, **options)
        if not panels:
            _fail('no_complete_rectangular_panel_candidates')
        cancelled()
        atlas = build_chart_label_atlas(image,panels,limits=PdfChartLabelAtlasLimits(
            source_pixels=limits.source_pixels,panels=limits.panels,png_bytes=limits.image_bytes))
        cancelled()
        report_json = _json(report)
        binding_options = {} if allow_antialiased_axes is False else {
            'allow_antialiased_ticks':allow_antialiased_axes,
            'individual_x_labels':allow_antialiased_axes}
        bindings = bind_chart_ocr_geometry(png,report_json.encode(),atlas.metadata_json.encode(),
            expected_source_sha256=sha256(png).hexdigest(),
            expected_ocr_sha256=sha256(report_json.encode()).hexdigest(),
            expected_atlas_metadata_sha256=sha256(atlas.metadata_json.encode()).hexdigest(),
            limits=PdfChartOcrBindingsLimits(source_pixels=limits.source_pixels,
                input_bytes=limits.image_bytes,words=limits.words,panels=limits.panels),
            **binding_options)
        metadata = _load(bindings.metadata_json)
        if any(not any(a['direction'] == 'x' for a in p['anchors']) for p in metadata['panels']):
            _fail('unresolved_original_condition_anchors')
        words = {w['word_id']:w for w in metadata['words']}
        observations, seen_pixels = [],0
        for word_id in metadata['unassigned_word_ids']:
            cancelled()
            word = words[word_id]
            x,y,width,height = word['source_bbox_xywh']
            left,right = max(0,int(x-4*height)),min(image.width,int(x-.8*height))
            top,bottom = int(y),min(image.height,int(y+height)+1)
            seen_pixels += max(0,right-left)*max(0,bottom-top)
            if seen_pixels > limits.legend_scan_pixels:
                _fail('complete_legend_source_scan_budget')
            observations.append({'word_id':word_id,'paint':_legend_color(image,word)})
        legend_ids = tuple(row['word_id'] for row in observations
            if row['paint']['status'] == 'dominant_source_color_candidate')
        if not legend_ids:
            _fail('unresolved_or_grayscale_legend_candidates')
        cancelled()
        return bindings,atlas,observations,legend_ids,report_json
    except (PdfNativeFigureError,PdfChartLabelAtlasError,PdfChartOcrBindingsError) as error:
        raise PdfNativeChartInputError('source_geometry_or_ocr_binding_rejected') from error


def _descriptor(png, image, appearance, report, raw_sha, bindings, atlas, legends, limits):
    # All omitted crop hashes/derived fields are recomputed from the original
    # PNG and kept in the returned bindings; complete derived SHA pins detect
    # detector/atlas/binding/paint behavior drift during cold replay.
    omitted = {'png_sha256','pixel_sha256','dimensions','source_context','source_context_sha256'}
    metadata = _load(bindings.metadata_json)
    return {'version':VERSION,'limits':asdict(limits),
        'source':{'png_sha256':sha256(png).hexdigest(),
            'pixel_sha256':sha256(image.tobytes()).hexdigest(),'dimensions':list(image.size)},
        'appearance':{k:v for k,v in appearance.items() if k not in omitted},
        'source_context':appearance['source_context'],'ocr':_compact_report(report),
        'raw_ocr_stdout_sha256':raw_sha,
        'canonical_ocr_report_sha256':sha256(_json(report).encode()).hexdigest(),
        'panels':[[p['panel_id'],p['axes_pixel_bbox'],p['crop_pixel_bbox']] for p in metadata['panels']],
        'derived':{'bindings_sha256':bindings.identity()['metadata_sha256'],
            'atlas_metadata_sha256':atlas.identity()['metadata_sha256'],
            'legend_candidates_sha256':sha256(_json(legends).encode()).hexdigest()},
        'semantic_accuracy_verified':False,'legend_ownership_proven':False,
        'candidate_discovery_exhaustive':False,'normal_workflow_integrated':False}


def prepare_native_chart_input(source_png, appearance, *, language, reserved_configuration_bytes,
        ocr_reader=None, limits=PdfNativeChartInputLimits(), cancel_event=None):
    """Observe original PNG once; store every OCR line/word/raw bbox without correction.

    ``ocr_reader(png, *, language, limits, cancel_event) -> bytes`` is injectable.
    The default uses a pinned, bounded WinRT worker; unsupported environments
    fail for preparation fallback. The descriptor must be stored as a JSON
    OBJECT with actual all-other-configuration-byte reservation, never a string.
    """
    if type(limits) is not PdfNativeChartInputLimits or type(language) is not str or not _LANGUAGE.fullmatch(language):
        _fail('invalid_language_or_limits')
    _budget('{}',reserved_configuration_bytes,limits)
    if ocr_reader is not None and not callable(ocr_reader):
        _fail('invalid_ocr_reader')
    if cancel_event is not None and cancel_event.is_set():
        _fail('cancelled')
    with _source(source_png,limits) as image:
        checked = _appearance(appearance,source_png,image,limits)
        reader = read_native_chart_ocr if ocr_reader is None else ocr_reader
        raw = reader(source_png,language=language,limits=limits,cancel_event=cancel_event)
        report = _report(raw,sha256(source_png).hexdigest(),image.size,language,limits)
        bindings,atlas,legends,legend_ids,report_json = _pipeline(source_png,image,report,limits,cancel_event)
        descriptor = _json(_descriptor(source_png,image,checked,report,sha256(raw).hexdigest(),
            bindings,atlas,legends,limits))
        _budget(descriptor,reserved_configuration_bytes,limits)
        return PreparedNativeChartInput(descriptor,bindings,legend_ids,_json(legends),
            checked['source_context'],report_json,reserved_configuration_bytes)


def restore_native_chart_input(source_png, descriptor_json, appearance, *, expected_descriptor_sha256,
        reserved_configuration_bytes, limits=PdfNativeChartInputLimits(), cancel_event=None):
    """Rebind retained OCR observations to freshly checked original pixels; OCR calls=0."""
    if (type(limits) is not PdfNativeChartInputLimits or type(descriptor_json) is not str
            or type(expected_descriptor_sha256) is not str or not _SHA.fullmatch(expected_descriptor_sha256)):
        _fail('descriptor_identity_mismatch')
    _budget(descriptor_json,reserved_configuration_bytes,limits)
    if sha256(descriptor_json.encode()).hexdigest() != expected_descriptor_sha256:
        _fail('descriptor_identity_mismatch')
    descriptor = _load(descriptor_json)
    if _json(descriptor) != descriptor_json:
        _fail('noncanonical_descriptor')
    expected_fields = {'version','limits','source','appearance','source_context','ocr',
        'raw_ocr_stdout_sha256','canonical_ocr_report_sha256','panels','derived',
        'semantic_accuracy_verified','legend_ownership_proven','candidate_discovery_exhaustive',
        'normal_workflow_integrated'}
    if (type(descriptor) is not dict or set(descriptor) != expected_fields
            or descriptor['version'] != VERSION or descriptor['limits'] != asdict(limits)
            or type(descriptor['raw_ocr_stdout_sha256']) is not str
            or not _SHA.fullmatch(descriptor['raw_ocr_stdout_sha256'])):
        _fail('unsupported_descriptor_contract')
    with _source(source_png,limits) as image:
        checked = _appearance(appearance,source_png,image,limits)
        if type(descriptor['source']) is not dict:
            _fail('invalid_descriptor_source')
        report = _expand_report(descriptor['ocr'],sha256(source_png).hexdigest(),image.size,limits)
        bindings,atlas,legends,legend_ids,report_json = _pipeline(source_png,image,report,limits,cancel_event)
        actual = _descriptor(source_png,image,checked,report,descriptor['raw_ocr_stdout_sha256'],
            bindings,atlas,legends,limits)
        if _json(actual) != descriptor_json:
            _fail('cold_replay_source_observations_changed')
        return PreparedNativeChartInput(descriptor_json,bindings,legend_ids,_json(legends),
            checked['source_context'],report_json,reserved_configuration_bytes)


def read_native_chart_ocr(source_png, *, language, limits=PdfNativeChartInputLimits(), cancel_event=None):
    """Windows-only bounded stdout worker; temporary original PNG, no report file.

    Existing machine recognizers/policy are read only. No language installation,
    persistent execution-policy change, resize or profile loading is performed.
    Worker paths are code-owned; caller text never becomes shell source.
    """
    if os.name != 'nt':
        _fail('winrt_ocr_unsupported_platform')
    if type(limits) is not PdfNativeChartInputLimits or type(language) is not str or not _LANGUAGE.fullmatch(language):
        _fail('invalid_language_or_limits')
    with _source(source_png,limits):
        pass
    from .._document_pdf_windows import WindowsPdfProcess
    executable = Path(os.environ.get('SystemRoot','C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    worker = Path(__file__).with_name('pdf_native_chart_ocr_worker.ps1')
    if not executable.is_absolute() or not executable.is_file() or not worker.is_file():
        _fail('winrt_ocr_worker_unavailable')
    if cancel_event is not None and cancel_event.is_set():
        _fail('cancelled')
    with tempfile.TemporaryDirectory(prefix='pdf-native-chart-ocr-') as temporary:
        source_path = Path(temporary) / 'original.png'
        source_path.write_bytes(source_png)
        command = [str(executable),'-NoLogo','-NoProfile','-NonInteractive','-File',str(worker),
            '-SourcePath',str(source_path),'-SourceSha256',sha256(source_png).hexdigest(),
            '-Language',language,'-TimeoutMs',str(limits.timeout_seconds*1000),
            '-OutputBytes',str(limits.ocr_stdout_bytes),'-WordLimit',str(limits.words),
            '-SourcePixels',str(limits.source_pixels)]
        started = time.monotonic()
        try:
            process = WindowsPdfProcess(command,cwd=str(worker.parent),memory_bytes=512*1024*1024,
                max_processes=4,cleanup_seconds=5)
        except OSError as error:
            raise PdfNativeChartInputError('winrt_ocr_start_failed') from error
        errors, buffers = queue.Queue(),[bytearray(),bytearray()]
        def read(stream, target, maximum):
            try:
                while chunk := stream.read(32768):
                    if len(target)+len(chunk) > maximum:
                        errors.put('winrt_ocr_stdout_or_stderr_budget')
                        return
                    target.extend(chunk)
            except OSError:
                errors.put('winrt_ocr_pipe_failed')
        threads = [threading.Thread(target=read,args=(process.stdout,buffers[0],limits.ocr_stdout_bytes),daemon=True),
            threading.Thread(target=read,args=(process.stderr,buffers[1],8192),daemon=True)]
        try:
            process.stdin.close()
            for thread in threads:
                thread.start()
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    _fail('cancelled')
                if time.monotonic()-started > limits.timeout_seconds:
                    _fail('winrt_ocr_timeout')
                if not errors.empty():
                    _fail(errors.get_nowait())
                code = process.poll()
                if code is not None and not any(t.is_alive() for t in threads):
                    if not errors.empty():
                        _fail(errors.get_nowait())
                    if code != 0:
                        _fail('winrt_ocr_unsupported_or_failed')
                    return bytes(buffers[0])
                time.sleep(.01)
        except OSError as error:
            raise PdfNativeChartInputError('winrt_ocr_transport_failed') from error
        finally:
            cleanup_until = time.monotonic()+5
            cleanup_failed = False
            try:
                try:
                    process.stop()
                    process.wait_stopped(max(0,cleanup_until-time.monotonic()))
                except OSError:
                    cleanup_failed = True
                try:
                    process.close_job()
                except OSError:
                    cleanup_failed = True
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(max(0,cleanup_until-time.monotonic()))
            finally:
                try:
                    process.close()
                except OSError:
                    cleanup_failed = True
            if cleanup_failed or any(thread.is_alive() for thread in threads):
                _fail('winrt_ocr_cleanup_failed')
