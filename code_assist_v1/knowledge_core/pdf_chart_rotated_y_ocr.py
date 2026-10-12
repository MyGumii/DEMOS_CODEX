"""Optional lossless rotated-strip OCR observations; never metric ownership.

Every base-atlas y strip is transposed in both directions, without resize or
synthetic text, into one bounded OCR input. The untouched derived-input worker
transcript is retained separately; it is NOT the original PNG's OCR report.
Opposite-direction readings remain separate, including contradictory literals.
No original transcript, default reader, pipeline, model or database is changed.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import ceil, floor, isfinite
import re

from PIL import Image

from .pdf_chart_label_atlas import (
    PdfChartLabelAtlas, VERSION as ATLAS_VERSION, _BoundedPng, _box, _png,
)
from .pdf_native_chart_input import (
    PdfNativeChartInputError, PdfNativeChartInputLimits, _report,
    read_native_chart_ocr,
)

from .pdf_chart_ocr_bindings import PdfChartOcrBindings
from .pdf_page_label_ocr import _source_box

AXIS_FRAGMENT_RECIPE_VERSION = 'pdf-chart-secondary-source-x-fragments-v1'


class PdfRotatedYOcrError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfRotatedYOcrLimits:
    source_pixels: int = 16_000_000
    mosaic_pixels: int = 16_000_000
    png_bytes: int = 16_777_216
    panels: int = 64
    words: int = 4096
    ocr_stdout_bytes: int = 1_048_576
    metadata_bytes: int = 4_194_304
    timeout_seconds: int = 10

    def __post_init__(self):
        caps = (16_000_000, 16_000_000, 16_777_216, 64, 4096,
                1_048_576, 4_194_304, 30)
        if any(type(v) is not int or not 1 <= v <= cap
               for v, cap in zip(asdict(self).values(), caps)):
            raise PdfRotatedYOcrError('Invalid complete rotated OCR limits')


@dataclass(frozen=True, slots=True)
class PdfRotatedYOcr:
    metadata_json: str
    raw_mosaic_ocr_stdout: bytes

    def identity(self):
        return {'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest(),
                'raw_mosaic_ocr_stdout_sha256': sha256(self.raw_mosaic_ocr_stdout).hexdigest()}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def read_rotated_y_strip_ocr(source_png, base_atlas, *, expected_source_sha256,
                            expected_atlas_metadata_sha256, language,
                            limits=PdfRotatedYOcrLimits(), ocr_reader=None,
                            cancel_event=None, bindings=None, axis_fragment_recipe_version=None):
    """One optional worker call, using all generic full y strips, or none.

    Both rotation frames and every raw word survive. A word is source-mapped
    only when its entire bbox belongs to exactly one tile; crossing/gap words
    remain unknown. These are text candidates, not corrected ticks, calibrated
    values, selected metrics or permission to broadcast a label to other panels.
    """
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise PdfRotatedYOcrError('cancelled')

    cancelled()
    if (axis_fragment_recipe_version is not None and
            (type(axis_fragment_recipe_version) is not str
             or axis_fragment_recipe_version != AXIS_FRAGMENT_RECIPE_VERSION
             or type(bindings) is not PdfChartOcrBindings)):
        raise PdfRotatedYOcrError('Explicit supported source-fragment recipe required')
    if (type(limits) is not PdfRotatedYOcrLimits
            or type(source_png) is not bytes or not 0 < len(source_png) <= limits.png_bytes
            or type(expected_source_sha256) is not str
            or re.fullmatch('[0-9a-f]{64}', expected_source_sha256) is None
            or sha256(source_png).hexdigest() != expected_source_sha256
            or type(base_atlas) is not PdfChartLabelAtlas
            or type(base_atlas.metadata_json) is not str
            or len(base_atlas.metadata_json.encode()) > limits.metadata_bytes
            or type(base_atlas.png) is not bytes or not 0 < len(base_atlas.png) <= limits.png_bytes
            or type(expected_atlas_metadata_sha256) is not str
            or sha256(base_atlas.metadata_json.encode()).hexdigest() != expected_atlas_metadata_sha256
            or type(language) is not str
            or re.fullmatch('[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8}){0,3}', language) is None
            or (ocr_reader is not None and not callable(ocr_reader))):
        raise PdfRotatedYOcrError('Bounded unchanged source/base atlas required')
    try:
        base = json.loads(base_atlas.metadata_json)
        image = Image.open(BytesIO(source_png))
    except (ValueError, OSError, Image.DecompressionBombError) as error:
        raise PdfRotatedYOcrError('Invalid source/base atlas') from error
    with image:
        if (image.format != 'PNG' or image.mode != 'RGB'
                or image.width * image.height > limits.source_pixels
                or type(base) is not dict
                or base.get('version') != ATLAS_VERSION
                or type(base.get('source')) is not dict
                or type(base.get('atlas')) is not dict
                or base.get('source', {}).get('dimensions') != list(image.size)
                or base['source'].get('mode') != 'RGB'
                or base['source'].get('pixel_sha256') != sha256(image.tobytes()).hexdigest()
                or base.get('atlas', {}).get('png_sha256') != sha256(base_atlas.png).hexdigest()):
            raise PdfRotatedYOcrError('Original pixels or base atlas identity differ')
        panels = base.get('panels')
        if type(panels) is not list or not 1 <= len(panels) <= limits.panels:
            raise PdfRotatedYOcrError('Complete panel budget exceeded')
        strips, unresolved, seen = [], [], set()
        for panel in panels:
            cancelled()
            if (type(panel) is not dict or type(panel.get('panel_id')) is not str
                    or not 0 < len(panel['panel_id']) <= 64 or panel['panel_id'] in seen):
                raise PdfRotatedYOcrError('Distinct source panels required')
            seen.add(panel['panel_id'])
            try:
                axes = _box(panel.get('axes_pixel_bbox'), image.size)
                strip = panel.get('y_label_source_bbox')
                if strip is not None:
                    strip = _box(strip, image.size)
            except ValueError as error:
                raise PdfRotatedYOcrError('Invalid complete source strip geometry') from error
            if strip is None:
                unresolved.append({'panel_id': panel['panel_id'], 'reason': 'missing_base_y_strip'})
                continue
            if strip[2] != axes[0] or strip[1] > axes[1] or strip[3] < axes[3]:
                raise PdfRotatedYOcrError('Source y strip differs from measured axes')
            strips.append((panel['panel_id'], strip))
        # Preflight the entire mosaic before allocation, never retain a prefix.
        column_width = max((box[3]-box[1] for _, box in strips), default=0) + 16
        dimensions = [2*column_width, sum(box[2]-box[0]+16 for _, box in strips)]
        if strips and (dimensions[0]*dimensions[1] > limits.mosaic_pixels
                       or max(dimensions) > 10_000):
            raise PdfRotatedYOcrError('Complete unscaled mosaic exceeds OCR budget')
        tiles, observations, raw = [], [], b''
        axis_input, excluded = None, []
        mosaic_record = None
        if strips:
            with Image.new('RGB', tuple(dimensions), 'white') as mosaic:
                top = 0
                for panel_id, box in strips:
                    cancelled()
                    with image.crop(box) as crop:
                        source_crop_sha = sha256(crop.tobytes()).hexdigest()
                        for column, rotation in enumerate((90, 270)):
                            transform = Image.Transpose.ROTATE_90 if rotation == 90 else Image.Transpose.ROTATE_270
                            with crop.transpose(transform) as rotated:
                                left = column*column_width+8
                                mosaic.paste(rotated, (left, top+8))
                                tiles.append({'panel_id': panel_id, 'rotation_degrees_ccw': rotation,
                                    'source_pixel_bbox': list(box), 'source_pixel_sha256': source_crop_sha,
                                    'mosaic_pixel_bbox': [left, top+8, left+rotated.width, top+8+rotated.height],
                                    'rotated_pixel_sha256': sha256(rotated.tobytes()).hexdigest()})
                        top += crop.width+16
                with _BoundedPng(limits.png_bytes) as output:
                    mosaic.save(output, format='PNG', compress_level=6, optimize=False)
                    png = output.getvalue()
                mosaic_sha = sha256(png).hexdigest()
                mosaic_record = {'dimensions': dimensions, 'png_sha256': mosaic_sha,
                                 'pixel_sha256': sha256(mosaic.tobytes()).hexdigest()}
                if axis_fragment_recipe_version is not None:
                    original = json.loads(bindings.metadata_json)
                    if original['source']['dimensions'] != list(image.size) or original['source']['pixel_sha256'] != sha256(image.tobytes()).hexdigest():
                        raise PdfRotatedYOcrError('Original binding pixels differ')
                    fragments, excluded = _source_x_fragments(image, original, base,
                        cancelled=cancelled, maximum_pixel_tests=limits.source_pixels)
                    png, axis_input = _combined_axis_mosaic(image, png, tiles, fragments, limits)
                    dimensions, tiles = axis_input['dimensions'], axis_input['tiles']
                    mosaic_sha = axis_input['png_sha256']
                    mosaic_record = {k:axis_input[k] for k in ('dimensions','png_sha256','pixel_sha256')}
                native_limits = PdfNativeChartInputLimits(source_pixels=limits.mosaic_pixels,
                    image_bytes=limits.png_bytes, words=limits.words, panels=limits.panels,
                    ocr_stdout_bytes=limits.ocr_stdout_bytes, timeout_seconds=limits.timeout_seconds)
                cancelled()
                reader = read_native_chart_ocr if ocr_reader is None else ocr_reader
                try:
                    raw = reader(png, language=language, limits=native_limits, cancel_event=cancel_event)
                    cancelled()
                    report = _report(raw, mosaic_sha, dimensions, language, native_limits)
                except PdfNativeChartInputError as error:
                    raise PdfRotatedYOcrError('Derived-input worker report rejected') from error
            for line_index, line in enumerate(report['lines'] if axis_fragment_recipe_version is None else ()):
                for word_index, word in enumerate(line['words']):
                    cancelled()
                    box = word['bbox']
                    if (type(box) not in (list, tuple) or len(box) != 4
                            or any(type(v) not in (int, float) or not 0 <= v <= max(dimensions)
                                   or not isfinite(v) for v in box)
                            or box[0] < 0 or box[1] < 0 or box[2] <= 0 or box[3] <= 0
                            or box[0]+box[2] > dimensions[0] or box[1]+box[3] > dimensions[1]):
                        raise PdfRotatedYOcrError('Invalid derived-input word bbox')
                    u, v, width, height = box
                    owners = [t for t in tiles if t['mosaic_pixel_bbox'][0] <= u
                              and t['mosaic_pixel_bbox'][1] <= v
                              and u+width <= t['mosaic_pixel_bbox'][2]
                              and v+height <= t['mosaic_pixel_bbox'][3]]
                    row = {'raw_line_index': line_index, 'raw_word_index': word_index,
                           'literal': word['text'], 'mosaic_bbox_xywh': list(box),
                           'status': 'unresolved_not_one_complete_source_tile',
                           'panel_id': None, 'rotation_degrees_ccw': None,
                           'source_bbox_xywh': None, 'source_pixel_bbox': None,
                           'source_pixel_sha256': None, 'label_accuracy_verified': False}
                    if len(owners) == 1:
                        tile = owners[0]
                        x0, y0, x1, y1 = tile['source_pixel_bbox']
                        u -= tile['mosaic_pixel_bbox'][0]
                        v -= tile['mosaic_pixel_bbox'][1]
                        mapped = ([x1-v-height, y0+u, height, width]
                                  if tile['rotation_degrees_ccw'] == 90
                                  else [x0+v, y1-u-width, height, width])
                        pixel_box = [floor(mapped[0]), floor(mapped[1]),
                                     ceil(mapped[0]+mapped[2]), ceil(mapped[1]+mapped[3])]
                        with image.crop(pixel_box) as pixels:
                            pixel_sha = sha256(pixels.tobytes()).hexdigest()
                        row.update(status='source_mapped_literal_candidate', panel_id=tile['panel_id'],
                            rotation_degrees_ccw=tile['rotation_degrees_ccw'],
                            source_bbox_xywh=mapped, source_pixel_bbox=pixel_box,
                            source_pixel_sha256=pixel_sha)
                    observations.append(row)
            if axis_fragment_recipe_version is not None:
                observations = _map_axis_observations(report, axis_input, image)
                for observation in observations:
                    cancelled()
                    if observation['status'] == 'source_mapped_y_candidate':
                        observation['status'] = 'source_mapped_literal_candidate'
                        tile = next(t for t in tiles if t['kind'] == 'existing_y_strip'
                            and t['panel_id'] == observation['panel_id']
                            and t['mosaic_pixel_bbox'][0] < observation['mosaic_bbox_xywh'][0]
                            and observation['mosaic_bbox_xywh'][0]+observation['mosaic_bbox_xywh'][2] < t['mosaic_pixel_bbox'][2])
                        observation['rotation_degrees_ccw'] = tile['rotation_degrees_ccw']
        record = {'source_png_sha256': expected_source_sha256,
            'source_dimensions': list(image.size), 'base_atlas_identity': base_atlas.identity(),
            'limits': asdict(limits), 'panel_count': len(panels), 'tiles': tiles,
            'unresolved_panels': unresolved, 'mosaic': mosaic_record,
            'raw_mosaic_ocr_stdout_sha256': sha256(raw).hexdigest(),
            'observations': observations, 'ocr_calls': int(bool(strips)),
            'all_raw_word_observations_retained': True, 'both_rotation_readings_retained': True,
            'original_ocr_replaced': False, 'source_resized': False,
            'source_pixels_modified': False, 'synthetic_text_added': False,
            'labels_corrected': False, 'metric_selected': False, 'missing_values_filled': False,
            'semantic_accuracy_verified': False, 'metric_ownership_verified': False,
            'normal_workflow_integrated': False, 'publication_authority': False,
            'model_calls': 0, 'database_calls': 0}
        if axis_fragment_recipe_version is not None:
            record.update(axis_fragment_recipe_version=AXIS_FRAGMENT_RECIPE_VERSION,
                primary_bindings_identity=bindings.identity(), axis_fragment_input=axis_input,
                excluded_x_fragments=excluded, language=language)
            if axis_input is None:
                record['axis_fragment_unresolved_reason'] = 'no_source_y_strip'
        encoded = _json(record)
        if len(encoded.encode()) > limits.metadata_bytes:
            raise PdfRotatedYOcrError('Complete rotated OCR metadata exceeds budget')
        cancelled()
        return PdfRotatedYOcr(encoded, raw)


def _fragment_sha(value):
    return sha256(value).hexdigest()

def _fragment_white(image, box):
    with image.crop(box) as crop:
        return crop.tobytes() == b'\xff' * (crop.width * crop.height * 3)


def _source_x_fragments(image, bindings, atlas, *, cancelled=lambda: None, maximum_pixel_tests=16_000_000):
    """Neighbour windows use observed tick coordinates, never word characters.

    Cuts must have two exact-white source columns. The observed shared-word y
    band is extended only outward to the first original white row inside the
    retained x strip. Nonwhite/ambiguous boundaries are refused, not clipped.
    """
    tested = 0
    def white(box):
        nonlocal tested
        cancelled()
        tested += (box[2]-box[0])*(box[3]-box[1])
        if tested > maximum_pixel_tests:
            raise PdfRotatedYOcrError('Complete source-fragment scan exceeds existing pixel budget')
        return _fragment_white(image, box)
    words = {w['word_id']: w for w in bindings['words']}
    panels = {p['panel_id']: p for p in atlas['panels']}
    result, refused = [], []
    for panel in bindings['panels']:
        cancelled()
        xanchors = [a for a in panel['anchors'] if a['direction'] == 'x']
        xanchors.sort(key=lambda a: a['source_coordinate'])
        strip = panels[panel['panel_id']]['x_condition_source_bbox']
        shared = {}
        for index, anchor in enumerate(xanchors):
            if anchor['status'] != 'unresolved_shared_ocr_word':
                continue
            candidates = anchor['ocr_line_candidates']
            if len(candidates) != 1 or len(candidates[0]['word_ids']) != 1:
                refused.append({'anchor_id': anchor['anchor_id'], 'reason': 'ambiguous_primary_word'})
                continue
            wid = candidates[0]['word_ids'][0]
            shared.setdefault(wid, []).append((index, anchor))
        for wid, anchors in shared.items():
            word = words[wid]
            wb = word['source_pixel_bbox']
            if _fragment_sha(image.crop(wb).tobytes()) != word['source_pixel_sha256']:
                raise ValueError('Original primary word pixels changed')
            if len(anchors) < 2:
                # A globally shared raw word may have only one tick in
                # each overlapping panel. That is valid ambiguity, not
                # a corrupt input and not enough geometry to split.
                refused.extend({'panel_id':panel['panel_id'],
                    'anchor_id':anchor['anchor_id'],'primary_word_id':wid,
                    'reason':'insufficient_same_panel_shared_anchors'}
                    for _,anchor in anchors)
                continue
            for index, anchor in anchors:
                ab = anchor['source_pixel_bbox']
                if _fragment_sha(image.crop(ab).tobytes()) != anchor['source_pixel_sha256']:
                    raise ValueError('Original tick observation pixels changed')
                x = anchor['source_coordinate']
                if not isfinite(x) or len(xanchors) < 2:
                    raise ValueError('Observed neighbour coordinates required')
                previous = xanchors[index-1]['source_coordinate'] if index else None
                following = xanchors[index+1]['source_coordinate'] if index+1 < len(xanchors) else None
                left = floor((previous+x)/2 if previous is not None else x-(following-x)/2)
                right = floor((x+following)/2 if following is not None else x+(x-previous)/2)
                left, right = max(strip[0]+1, left), min(strip[2]-1, right)
                row = {'panel_id': panel['panel_id'], 'anchor_id': anchor['anchor_id'],
                       'anchor': deepcopy(anchor), 'primary_word_id': wid,
                       'primary_word_bbox': wb, 'primary_word_literal': word['literal'],
                       'cut_geometry': {'left': left, 'right': right,
                           'previous_observed_tick': previous, 'observed_tick': x,
                           'following_observed_tick': following}}
                if not left < x < right:
                    refused.append(dict(row, reason='invalid_neighbour_window'))
                    continue
                # Find actual outward whitespace, not expected character bounds.
                y0 = next((y for y in range(wb[1]-1, strip[1]-1, -1)
                           if white([left-1,y,right+1,y+1])), None)
                y1 = next((y+1 for y in range(wb[3], strip[3])
                           if white([left-1,y,right+1,y+1])), None)
                if y0 is None or y1 is None:
                    refused.append(dict(row, reason='no_outward_white_y_gutter'))
                    continue
                cuts = [[left-1,y0,left+1,y1], [right-1,y0,right+1,y1]]
                if not all(white(box) for box in cuts):
                    refused.append(dict(row, reason='neighbour_cut_intersects_source_ink'))
                    continue
                box = [left,y0,right,y1]
                row.update(source_pixel_bbox=box,
                           source_pixel_sha256=_fragment_sha(image.crop(box).tobytes()),
                           white_gutters=[{'source_pixel_bbox': b,
                               'source_pixel_sha256': _fragment_sha(image.crop(b).tobytes())} for b in cuts],
                           source_resized=False, rotation_degrees_ccw=0)
                result.append(row)
    return result, refused


def _combined_axis_mosaic(source, old_y_png, old_y_tiles, fragments,
                    limits=PdfRotatedYOcrLimits()):
    """Keep every old y-strip pixel; paste one exact x crop per new padded row."""
    with Image.open(BytesIO(old_y_png)) as y:
        width = max(y.width, max((f['source_pixel_bbox'][2]-f['source_pixel_bbox'][0]+16
                                 for f in fragments), default=0))
        height = y.height + sum(f['source_pixel_bbox'][3]-f['source_pixel_bbox'][1]+16
                                for f in fragments)
        if width*height > limits.mosaic_pixels or max(width,height) > 10_000:
            raise ValueError('Whole combined mosaic exceeds existing budget')
        with Image.new('RGB', (width,height), 'white') as out:
            out.paste(y,(0,0))
            tiles = [dict(t, kind='existing_y_strip') for t in old_y_tiles]
            top = y.height
            for fragment in fragments:
                box = fragment['source_pixel_bbox']
                with source.crop(box) as pixels:
                    if _fragment_sha(pixels.tobytes()) != fragment['source_pixel_sha256']:
                        raise ValueError('Fragment pixels changed before paste')
                    out.paste(pixels, (8,top+8))
                    tiles.append(dict(fragment, kind='x_fragment',
                        mosaic_pixel_bbox=[8,top+8,8+pixels.width,top+8+pixels.height]))
                    top += pixels.height+16
            png = _png(out, limits.png_bytes)
            record = {'dimensions':[width,height], 'png_sha256':_fragment_sha(png),
                      'pixel_sha256':_fragment_sha(out.tobytes()), 'old_y_top_pixel_sha256':
                      _fragment_sha(out.crop([0,0,y.width,y.height]).tobytes()),
                      'old_y_pixel_sha256':_fragment_sha(y.tobytes()), 'tiles':tiles}
            if len(_json(record).encode()) > limits.metadata_bytes:
                raise ValueError('Whole combined metadata exceeds existing budget')
            return png, record


def _map_axis_observations(report, metadata, source):
    """Raw literal is a candidate only for exactly one whole tile/tick owner."""
    observations = []
    for li, line in enumerate(report['lines']):
        for wi, word in enumerate(line['words']):
            x,y,w,h = word['bbox']
            if (any(type(v) not in (int,float) or not isfinite(v) for v in (x,y,w,h))
                    or min(x,y) < 0 or min(w,h) <= 0
                    or x+w > metadata['dimensions'][0] or y+h > metadata['dimensions'][1]):
                raise ValueError('Invalid secondary raw bbox')
            row = {'raw_line_index':li, 'raw_word_index':wi, 'literal':word['text'],
                   'mosaic_bbox_xywh':list(word['bbox']), 'status':'unknown_not_one_tile',
                   'label_accuracy_verified':False, 'label_binding_verified':False,
                   'tick_classification_verified':False, 'publication_authority':False}
            owners = [t for t in metadata['tiles'] if t['mosaic_pixel_bbox'][0] < x
                      and t['mosaic_pixel_bbox'][1] < y
                      and x+w < t['mosaic_pixel_bbox'][2]
                      and y+h < t['mosaic_pixel_bbox'][3]]
            if len(owners) == 1:
                tile = owners[0]
                sx,sy,sr,sb = tile['source_pixel_bbox']
                tx,ty,_,_ = tile['mosaic_pixel_bbox']
                mapped = _source_box([x-tx,y-ty,w,h],[sr-sx,sb-sy],
                                     tile['rotation_degrees_ccw'])
                mapped[0] += sx
                mapped[1] += sy
                pb = [floor(mapped[0]),floor(mapped[1]),ceil(mapped[0]+mapped[2]),ceil(mapped[1]+mapped[3])]
                row.update(kind=tile['kind'], panel_id=tile['panel_id'],
                           source_bbox_xywh=mapped, source_pixel_bbox=pb,
                           source_pixel_sha256=_fragment_sha(source.crop(pb).tobytes()),
                           status='source_mapped_y_candidate')
                if tile['kind'] == 'x_fragment':
                    row['anchor_id'] = tile['anchor_id']
                    anchor = tile['anchor']
                    gap = pb[1]-anchor['source_pixel_bbox'][3]
                    one_tick = (pb[0] <= anchor['source_coordinate'] <= pb[2]
                                and 0 <= gap <= max(2,(pb[3]-pb[1])/2))
                    row['status'] = ('source_mapped_x_literal_candidate' if one_tick
                                     else 'unknown_word_not_proximate_to_observed_tick')
            observations.append(row)
    owners = Counter(r.get('anchor_id') for r in observations
                     if r.get('kind') == 'x_fragment')
    # A clipped/cross-tile second word is still an ambiguity for every tile it
    # touches; counting only fully owned words would silently admit the first.
    intersected = Counter()
    for row in observations:
        x,y,w,h = row['mosaic_bbox_xywh']
        for tile in metadata['tiles']:
            if tile['kind'] != 'x_fragment':
                continue
            l,t,r,b = tile['mosaic_pixel_bbox']
            if x < r and x+w > l and y < b and y+h > t:
                intersected[tile['anchor_id']] += 1
    for row in observations:
        if row.get('kind') == 'x_fragment' and (owners[row['anchor_id']] != 1
                                               or intersected[row['anchor_id']] != 1):
            row['status'] = 'unknown_multiple_words_for_one_tick'
    return observations


def _derive_secondary_x_binding(original, raw, metadata, image, language, *, expected_raw_sha):
    """Preserve all primary records; consume only unique retained raw x words.

    Each effective x anchor keeps its entire primary binding alongside the raw
    secondary word reference. Axis coordinates/pixels, y records, primary words
    and the discovered raw legend inventory are never changed.
    """
    if _fragment_sha(raw) != expected_raw_sha:
        raise ValueError('Retained raw secondary bytes changed')
    if (original['source']['dimensions'] != list(image.size)
            or original['source']['pixel_sha256'] != _fragment_sha(image.tobytes())):
        raise ValueError('Original binding source pixels differ')
    observed = _report(raw,metadata['png_sha256'],metadata['dimensions'],language,
                              PdfNativeChartInputLimits(timeout_seconds=10))
    rows = _map_axis_observations(observed,metadata,image)
    derived = deepcopy(original)
    anchors = {a['anchor_id']:a for p in derived['panels'] for a in p['anchors']}
    tiles = {t['anchor_id']:t for t in metadata['tiles'] if t['kind']=='x_fragment'}
    if len(tiles) != sum(t['kind']=='x_fragment' for t in metadata['tiles']):
        raise ValueError('Distinct retained tile anchors required')
    selected = []
    for row in rows:
        if row['status'] != 'source_mapped_x_literal_candidate':
            continue
        anchor = anchors.get(row['anchor_id'])
        tile = tiles[row['anchor_id']]
        if anchor is None or anchor != tile['anchor'] or anchor['direction'] != 'x':
            raise ValueError('Secondary tile no longer matches the original source tick')
        if anchor['status'] != 'unresolved_shared_ocr_word':
            raise ValueError('A secondary candidate cannot replace a nonshared primary binding')
        if (_fragment_sha(image.crop(tile['source_pixel_bbox']).tobytes()) != tile['source_pixel_sha256']
                or _fragment_sha(image.crop(anchor['source_pixel_bbox']).tobytes()) != anchor['source_pixel_sha256']):
            raise ValueError('Retained source fragment/tick pixels differ')
        primary = deepcopy(anchor)
        new_id = 'SX'+str(len(selected)+1).zfill(4)
        word = {'word_id':new_id,'line_id':'SX-L'+str(row['raw_line_index']),
                'literal':row['literal'],'source_bbox_xywh':row['source_bbox_xywh'],
                'source_pixel_bbox':row['source_pixel_bbox'],
                'source_pixel_sha256':row['source_pixel_sha256'],
                'ocr_text_accuracy_verified':False,
                'secondary_raw_line_index':row['raw_line_index'],
                'secondary_raw_word_index':row['raw_word_index']}
        derived['words'].append(word)
        anchor.update(status='single_literal_coordinate_candidate',literal=row['literal'],
            finite_tick_literal_value=None,label_binding_verified=False,
            primary_ocr_binding=primary,
            secondary_raw_word_id=new_id,
            ocr_line_candidates=[{'line_id':word['line_id'],'word_ids':[new_id],
                'literals':[row['literal']],'source_pixel_bbox':row['source_pixel_bbox']}])
        selected.append({'anchor_id':anchor['anchor_id'],'word_id':new_id,
            'literal':row['literal'],'source_bbox_xywh':row['source_bbox_xywh'],
            'source_pixel_sha256':row['source_pixel_sha256']})
    derived['secondary_x_binding_provenance'] = {
        'diagnostic_version':AXIS_FRAGMENT_RECIPE_VERSION,
        'original_bindings_sha256':_fragment_sha(_json(original).encode()),
        'raw_secondary_sha256':expected_raw_sha,
        'combined_mosaic_sha256':metadata['png_sha256'],
        'original_primary_ocr_replaced':False,'raw_words_split_or_corrected':False,
        'coordinates_inferred_from_strings':False,'label_accuracy_verified':False,
        'publication_authority':False,'selected':selected,'observations':rows}
    return PdfChartOcrBindings(_json(derived)),selected


def effective_secondary_x_bindings(source_png, bindings, rotated, *, cancel_event=None):
    """Derive only uniquely owned raw words; original primary remains untouched."""
    if cancel_event is not None and cancel_event.is_set():
        raise PdfRotatedYOcrError('cancelled')
    if type(bindings) is not PdfChartOcrBindings or type(rotated) is not PdfRotatedYOcr:
        raise PdfRotatedYOcrError('Typed original bindings and secondary transcript required')
    record = json.loads(rotated.metadata_json)
    if (record.get('axis_fragment_recipe_version') != AXIS_FRAGMENT_RECIPE_VERSION
            or record.get('primary_bindings_identity') != bindings.identity()
            or sha256(source_png).hexdigest() != record.get('source_png_sha256')
            or sha256(rotated.raw_mosaic_ocr_stdout).hexdigest() != record.get('raw_mosaic_ocr_stdout_sha256')):
        raise PdfRotatedYOcrError('Unchanged original source/bindings/secondary bytes required')
    with Image.open(BytesIO(source_png)) as image:
        if record['axis_fragment_input'] is None:
            original = json.loads(bindings.metadata_json)
            if (record.get('axis_fragment_unresolved_reason') != 'no_source_y_strip'
                    or record.get('ocr_calls') != 0 or record.get('mosaic') is not None
                    or record.get('tiles') != [] or record.get('observations') != []
                    or rotated.raw_mosaic_ocr_stdout != b''
                    or original['source']['dimensions'] != list(image.size)
                    or original['source']['pixel_sha256'] != sha256(image.tobytes()).hexdigest()):
                raise PdfRotatedYOcrError('Complete unchanged no-secondary-input provenance required')
            # Do not invent an x-only OCR call where the historical path had
            # none. Original tick/literal unknowns and original bindings survive.
            return bindings
        result, _ = _derive_secondary_x_binding(json.loads(bindings.metadata_json),
            rotated.raw_mosaic_ocr_stdout, record['axis_fragment_input'], image, record['language'],
            expected_raw_sha=record['raw_mosaic_ocr_stdout_sha256'])
    if len(result.metadata_json.encode()) > record['limits']['metadata_bytes']:
        raise PdfRotatedYOcrError('Complete effective binding metadata exceeds existing budget')
    if cancel_event is not None and cancel_event.is_set():
        raise PdfRotatedYOcrError('cancelled')
    return result
