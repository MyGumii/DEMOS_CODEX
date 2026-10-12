"""Isolated source-coordinate OCR candidates, not OCR correction/calibration.

Only neutral outward protrusions connected to measured axes are observed.
Default pixels are dark; opt-in accepts lighter neutral pixels. Neither proves
ticks. OCR words remain literal, including mistakes, fragments,
unassigned words and ambiguities. No expected conditions, scale, series, axis
names, point values, OCR engine, model, database or service is used here.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import ceil, floor, isfinite
import re

from PIL import Image, ImageChops


VERSION = 'pdf-chart-ocr-source-coordinate-candidates-v1'


class PdfChartOcrBindingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfChartOcrBindingsLimits:
    source_pixels: int = 16_000_000
    input_bytes: int = 16_777_216
    panels: int = 64
    words: int = 4096
    anchors_per_axis: int = 256
    scan_pixels: int = 4_000_000
    metadata_bytes: int = 4_194_304

    def __post_init__(self):
        caps = (16_000_000,16_777_216,64,4096,256,4_000_000,4_194_304)
        if any(type(v) is not int or not 1 <= v <= cap
               for v,cap in zip(asdict(self).values(),caps)):
            raise PdfChartOcrBindingsError('Invalid bounded OCR geometry limits')


@dataclass(frozen=True, slots=True)
class PdfChartOcrBindings:
    metadata_json: str

    def identity(self):
        return {'version':VERSION,'metadata_sha256':sha256(self.metadata_json.encode()).hexdigest()}


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _input(value, expected, maximum):
    if (type(value) is not bytes or not value or len(value) > maximum
            or type(expected) is not str or not re.fullmatch('[0-9a-f]{64}',expected)
            or sha256(value).hexdigest() != expected):
        raise PdfChartOcrBindingsError('Bounded input SHA identity mismatch')


def _rectangle(box,size):
    if (type(box) not in (tuple,list) or len(box) != 4
            or any(type(v) is not int for v in box)
            or not 0 <= box[0] < box[2] <= size[0]
            or not 0 <= box[1] < box[3] <= size[1]):
        raise PdfChartOcrBindingsError('Invalid source pixel rectangle')
    return list(box)


def _runs(values):
    result, start = [], None
    for index, present in enumerate(values):
        if present and start is None:
            start = index
        if not present and start is not None:
            result.append((start,index))
            start = None
    if start is not None:
        result.append((start,len(values)))
    return result


def _black_mask(image,*,allow_antialiased_ticks=False):
    channels = image.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2])
    minimum = ImageChops.darker(ImageChops.darker(channels[0],channels[1]),channels[2])
    spread = ImageChops.difference(maximum,minimum)
    neutral = spread.point([255 if n <= 8 else 0 for n in range(256)])
    threshold = 245 if allow_antialiased_ticks else 70
    dark = maximum.point([255 if n <= threshold else 0 for n in range(256)])
    try:
        return ImageChops.multiply(neutral,dark)
    finally:
        for item in (*channels,maximum,minimum,spread,neutral,dark):
            item.close()


def _scan_box(axes,crop,direction):
    left,top,right,bottom = axes
    margin = max(4,min(16,min(right-left,bottom-top)//40))
    if direction == 'x':
        return [left+2,max(crop[1],bottom-margin),right-2,min(crop[3],bottom+margin)]
    return [max(crop[0],left-margin),top+2,min(crop[2],left+margin),bottom-2]


def _outer_ticks(source,axes,scan,direction,maximum,*,allow_antialiased_ticks=False):
    """Observe thin neutral protrusions immediately outside a long neutral frame."""
    if scan[0] >= scan[2] or scan[1] >= scan[3]:
        return [], 'no_outside_axis_scan_region'
    with source.crop(scan) as pixels, _black_mask(pixels,
            allow_antialiased_ticks=allow_antialiased_ticks) as mask:
        view = mask.transpose(Image.Transpose.TRANSPOSE) if direction == 'y' else mask.copy()
        try:
            width,height = view.size
            raw = view.tobytes()
        finally:
            view.close()
    bands = _runs([width >= 8 and raw[n*width:(n+1)*width].count(255)*10 >= width*7
                   for n in range(height)])
    edge = axes[3]-scan[1] if direction == 'x' else axes[0]-scan[0]
    bands = [band for band in bands if band[1]-band[0] <= 16]
    if not bands:
        return [], 'no_long_neutral_dark_frame'
    band = min(bands,key=lambda b:abs((b[0]+b[1]-1)/2-edge))
    start,step = (band[1],1) if direction == 'x' else (band[0]-1,-1)
    outer = []
    row = start
    while 0 <= row < height and len(outer) < 16:
        if not raw[row*width:(row+1)*width].count(255):
            break
        outer.append(row)
        row += step
    if not outer:
        return [], 'no_connected_neutral_dark_protrusions'
    initial = raw[outer[0]*width:(outer[0]+1)*width]
    groups = _runs([value == 255 for value in initial])
    groups = [g for g in groups if g[1]-g[0] <= max(4,band[1]-band[0])]
    if len(groups) > maximum:
        raise PdfChartOcrBindingsError('Complete black axis candidates exceed budget')
    result = []
    for low,high in groups:
        if direction == 'x':
            box = [scan[0]+low,scan[1]+min(outer),scan[0]+high,scan[1]+max(outer)+1]
            coordinate = scan[0]+(low+high-1)/2
        else:
            box = [scan[0]+min(outer),scan[1]+low,scan[0]+max(outer)+1,scan[1]+high]
            coordinate = scan[1]+(low+high-1)/2
        with source.crop(box) as pixels:
            result.append({'direction':direction,'source_coordinate':coordinate,
                'source_pixel_bbox':box,'source_pixel_sha256':sha256(pixels.tobytes()).hexdigest(),
                'classification':'neutral_dark_axis_protrusion_candidate','tick_classification_verified':False})
    return result, None if result else 'no_thin_connected_neutral_dark_protrusions'


def _words(report,source,limits):
    lines = report.get('lines')
    if type(lines) is not list or len(lines) > limits.words:
        raise PdfChartOcrBindingsError('Complete OCR lines exceed budget')
    words, grouped = [], []
    for line_index,line in enumerate(lines,1):
        if (type(line) is not dict or type(line.get('text')) is not str
                or type(line.get('words')) is not list or len(line['text']) > 4096):
            raise PdfChartOcrBindingsError('Invalid literal OCR line')
        line_words = []
        for item in line['words']:
            if len(words) >= limits.words:
                raise PdfChartOcrBindingsError('Complete OCR words exceed budget')
            if (type(item) is not dict or type(item.get('text')) is not str
                    or not item['text'] or len(item['text']) > 256):
                raise PdfChartOcrBindingsError('Invalid literal OCR word')
            box = item.get('bbox')
            if (type(box) not in (tuple,list) or len(box) != 4
                    or any(type(v) not in (int,float) or not isfinite(v) for v in box)
                    or box[0] < 0 or box[1] < 0 or box[2] <= 0 or box[3] <= 0
                    or box[0]+box[2] > source.width or box[1]+box[3] > source.height):
                raise PdfChartOcrBindingsError('Invalid original OCR source bbox')
            pixel_box = [floor(box[0]),floor(box[1]),ceil(box[0]+box[2]),ceil(box[1]+box[3])]
            with source.crop(pixel_box) as pixels:
                word = {'word_id':'W'+str(len(words)+1).zfill(4),
                    'line_id':'L'+str(line_index).zfill(4),'literal':item['text'],
                    'source_bbox_xywh':list(box),'source_pixel_bbox':pixel_box,
                    'source_pixel_sha256':sha256(pixels.tobytes()).hexdigest(),
                    'ocr_text_accuracy_verified':False}
            words.append(word)
            line_words.append(word)
        grouped.append({'line_id':'L'+str(line_index).zfill(4),'literal':line['text'],'words':line_words})
    if type(report.get('word_count')) is not int or report['word_count'] != len(words):
        raise PdfChartOcrBindingsError('OCR complete word count mismatch')
    return words,grouped


def _candidate_lines(lines,anchor,strip,axes,*,individual_x_labels=False):
    candidates = []
    coordinate,direction = anchor['source_coordinate'],anchor['direction']
    for line in lines:
        words = [w for w in line['words'] if strip[0] <= w['source_pixel_bbox'][0]
                 and w['source_pixel_bbox'][2] <= strip[2] and strip[1] <= w['source_pixel_bbox'][1]
                 and w['source_pixel_bbox'][3] <= strip[3]]
        if individual_x_labels and direction == 'x':
            words = [w for w in words
                if w['source_pixel_bbox'][0] <= coordinate <= w['source_pixel_bbox'][2]
                and 0 <= w['source_pixel_bbox'][1]-anchor['source_pixel_bbox'][3]
                    <= max(2,(w['source_pixel_bbox'][3]-w['source_pixel_bbox'][1])/2)]
        if not words:
            continue
        boxes = [w['source_pixel_bbox'] for w in words]
        box = [min(b[0] for b in boxes),min(b[1] for b in boxes),
               max(b[2] for b in boxes),max(b[3] for b in boxes)]
        if direction == 'x':
            gap = box[1]-anchor['source_pixel_bbox'][3]
            present = box[0] <= coordinate <= box[2] and 0 <= gap <= max(2,(box[3]-box[1])/2)
        else:
            gap = axes[0]-box[2]
            present = box[1] <= coordinate <= box[3] and 0 <= gap <= max(2,box[3]-box[1])
        if present:
            candidates.append({'line_id':line['line_id'],'word_ids':[w['word_id'] for w in words],
                               'literals':[w['literal'] for w in words],'source_pixel_bbox':box})
    return candidates


def bind_chart_ocr_geometry(source_png,ocr_report_json,atlas_metadata_json,*,
                            expected_source_sha256,expected_ocr_sha256,
                            expected_atlas_metadata_sha256,limits=PdfChartOcrBindingsLimits(),
                            individual_x_labels=False,allow_antialiased_ticks=False):
    """Validate exact inputs; return all source-only coordinate candidates.

    Expected hashes identify caller-selected bytes, not semantic authority.
    Single finite y literals may be parsed as tick-label *candidates*, never as
    point values. Split words are NOT joined. x literals are never evaluated.
    Scale/names are not inferred; unmatched words and candidates are retained.
    Opt-in individual x labels filter original words by the same proximity
    bounds before grouping each line. They never create anchors or repair OCR.
    Opt-in antialiased ticks admit lighter neutral source pixels, retaining
    outward frame connectivity and thin-run guards, not proving tick identity.
    """
    if type(limits) is not PdfChartOcrBindingsLimits:
        raise PdfChartOcrBindingsError('Invalid limits')
    if type(individual_x_labels) is not bool:
        raise PdfChartOcrBindingsError('A strict boolean individual-x-label mode is required')
    if type(allow_antialiased_ticks) is not bool:
        raise PdfChartOcrBindingsError('A strict boolean antialiased-tick mode is required')
    for value,expected in ((source_png,expected_source_sha256),(ocr_report_json,expected_ocr_sha256),
                           (atlas_metadata_json,expected_atlas_metadata_sha256)):
        _input(value,expected,limits.input_bytes)
    try:
        report = json.loads(ocr_report_json)
        atlas = json.loads(atlas_metadata_json)
    except (ValueError,UnicodeDecodeError) as error:
        raise PdfChartOcrBindingsError('Invalid input JSON') from error
    with Image.open(BytesIO(source_png)) as source:
        if (source.format != 'PNG' or source.mode != 'RGB'
                or source.width*source.height > limits.source_pixels):
            raise PdfChartOcrBindingsError('Bounded original RGB PNG required')
        raw_sha = sha256(source.tobytes()).hexdigest()
        if (type(report) is not dict or type(atlas) is not dict
                or report.get('source_sha256') != expected_source_sha256
                or type(report.get('source_dimensions')) is not list
                or any(type(v) is not int for v in report['source_dimensions'])
                or report.get('source_dimensions') != list(source.size)
                or report.get('source_resized') is not False
                or type(atlas.get('source')) is not dict
                or type(atlas['source'].get('dimensions')) is not list
                or any(type(v) is not int for v in atlas['source']['dimensions'])
                or atlas['source'].get('dimensions') != list(source.size)
                or atlas['source'].get('mode') != 'RGB' or atlas['source'].get('pixel_sha256') != raw_sha):
            raise PdfChartOcrBindingsError('OCR/atlas original source identity mismatch')
        words,lines = _words(report,source,limits)
        panels = atlas.get('panels')
        if type(panels) is not list or not 1 <= len(panels) <= limits.panels:
            raise PdfChartOcrBindingsError('Complete measured panels exceed budget')
        prepared,seen,panel_ids,scan_pixels = [],set(),set(),0
        for panel in panels:
            if (type(panel) is not dict or type(panel.get('panel_id')) is not str
                    or not panel['panel_id'] or len(panel['panel_id']) > 64
                    or not all(c.isalnum() or c in '-_' for c in panel['panel_id'])
                    or panel['panel_id'] in panel_ids):
                raise PdfChartOcrBindingsError('Invalid measured panel ID')
            panel_ids.add(panel['panel_id'])
            axes = _rectangle(panel.get('axes_pixel_bbox'),source.size)
            crop = _rectangle(panel.get('crop_pixel_bbox'),source.size)
            if (tuple(axes) in seen or not crop[0] <= axes[0] < axes[2] <= crop[2]
                    or not crop[1] <= axes[1] < axes[3] <= crop[3]):
                raise PdfChartOcrBindingsError('Unique measured axes inside source crop required')
            seen.add(tuple(axes))
            strips = [panel.get('y_label_source_bbox'),panel.get('x_condition_source_bbox')]
            strips = [_rectangle(s,source.size) if s is not None else None for s in strips]
            if ((strips[0] is not None and (strips[0][2] != axes[0]
                    or strips[0][1] > axes[1] or strips[0][3] < axes[3]))
                    or (strips[1] is not None and strips[1] != [crop[0],axes[3],crop[2],crop[3]])):
                raise PdfChartOcrBindingsError('Source label strips must retain measured axis relationship')
            scans = [_scan_box(axes,crop,d) for d in ('y','x')]
            scan_pixels += sum(max(0,s[2]-s[0])*max(0,s[3]-s[1]) for s in scans)
            prepared.append((panel,axes,crop,strips,scans))
        if scan_pixels > limits.scan_pixels:
            raise PdfChartOcrBindingsError('Complete source axis scan exceeds budget')
        output,assigned = [],set()
        for panel,axes,crop,strips,scans in prepared:
            row = {'panel_id':panel['panel_id'],'axes_pixel_bbox':axes,'crop_pixel_bbox':crop,
                   'anchors':[],'unresolved':[],'axis_names':None,'declared_scale':None}
            for direction,strip,scan in zip(('y','x'),strips,scans):
                observed,reason = _outer_ticks(source,axes,scan,direction,limits.anchors_per_axis,
                    allow_antialiased_ticks=allow_antialiased_ticks)
                if reason:
                    row['unresolved'].append({'direction':direction,'reason':reason})
                for ordinal,anchor in enumerate(observed,1):
                    anchor['anchor_id'] = panel['panel_id']+'-B'+direction.upper()+str(ordinal).zfill(3)
                    candidates = _candidate_lines(lines,anchor,strip,axes,
                        individual_x_labels=individual_x_labels) if strip else []
                    anchor.update({'ocr_line_candidates':candidates,'literal':None,
                        'finite_tick_literal_value':None,'label_binding_verified':False,
                        'status':'unresolved_missing_ocr_line'})
                    if len(candidates) > 1:
                        anchor['status'] = 'unresolved_multiple_ocr_lines'
                    elif len(candidates) == 1:
                        candidate = candidates[0]
                        assigned.update(candidate['word_ids'])
                        if len(candidate['word_ids']) != 1:
                            anchor['status'] = 'unresolved_multiple_ocr_words'
                        else:
                            literal = candidate['literals'][0]
                            anchor['literal'] = literal
                            anchor['status'] = 'single_literal_coordinate_candidate'
                            if direction == 'y':
                                if re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',literal):
                                    value = float(literal)
                                    if isfinite(value):
                                        anchor['finite_tick_literal_value'] = value
                                    else:
                                        anchor['status'] = 'unresolved_non_finite_numeric_literal'
                                else:
                                    anchor['status'] = 'unresolved_non_numeric_literal'
                    row['anchors'].append(anchor)
            output.append(row)
        owners = {}
        for panel in output:
            for anchor in panel['anchors']:
                if len(anchor['ocr_line_candidates']) == 1:
                    for word_id in anchor['ocr_line_candidates'][0]['word_ids']:
                        owners.setdefault(word_id,[]).append(anchor)
        for targets in owners.values():
            if len(targets) > 1:
                for anchor in targets:
                    anchor.update({'status':'unresolved_shared_ocr_word','literal':None,
                                   'finite_tick_literal_value':None})
        metadata = {'version':VERSION,'input_sha256':{'source_png':expected_source_sha256,
            'ocr_report_json':expected_ocr_sha256,'atlas_metadata_json':expected_atlas_metadata_sha256},
            'source':{'mode':'RGB','dimensions':list(source.size),'pixel_sha256':raw_sha},
            'limits':asdict(limits),'scan_pixels':scan_pixels,'words':words,'panels':output,
            'unassigned_word_ids':[w['word_id'] for w in words if w['word_id'] not in assigned],
            'ocr_text_corrected':False,'split_ocr_words_merged':False,'missing_conditions_inferred':False,
            'axis_names_inferred':False,'declared_scale_inferred':False,'source_resized':False,
            'anchor_detection_exhaustive':False,'label_binding_verified':False,
            'tick_classification_verified':False,'numerical_calibration_performed':False,
            'point_values_computed':False,'published':False,'production_contract_adopted':False}
        if individual_x_labels:
            metadata['individual_x_labels'] = True
        if allow_antialiased_ticks:
            metadata['allow_antialiased_ticks'] = True
        encoded = _json(metadata)
        if len(encoded.encode()) > limits.metadata_bytes:
            raise PdfChartOcrBindingsError('Complete bindings metadata exceeds budget')
        return PdfChartOcrBindings(encoded)
