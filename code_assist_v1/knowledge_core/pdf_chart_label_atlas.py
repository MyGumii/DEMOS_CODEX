"""Bounded, unscaled label-reading atlas; geometry observations, not calibration.

No axis numbers, series names, chart classification, source ownership, model
calls or numerical measurements are produced here. Neutral long lines may be
grids, not ticks. The spatial reference is not a proven legend. Synthetic RGB
chips are sampled from the source but are NOT copied source image regions.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
from math import ceil, floor, isfinite

from PIL import Image, ImageChops, ImageDraw, ImageFont

VERSION = 'pdf-chart-label-atlas-geometry-v1'


class PdfChartLabelAtlasError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfChartLabelAtlasLimits:
    source_pixels: int = 16_000_000
    atlas_pixels: int = 16_000_000
    png_bytes: int = 16_777_216
    panels: int = 64
    colors: int = 64
    anchors_per_axis: int = 256
    color_search_matches: int = 65_536
    metadata_bytes: int = 4_194_304

    def __post_init__(self):
        ceilings = (16_000_000, 16_000_000, 16_777_216, 64, 64, 256, 65_536, 4_194_304)
        if any(type(v) is not int or not 1 <= v <= cap
               for v, cap in zip(asdict(self).values(), ceilings)):
            raise PdfChartLabelAtlasError('Invalid atlas limits')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


class _BoundedPng(BytesIO):
    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum

    def write(self, value):
        if self.tell() + len(value) > self.maximum:
            raise PdfChartLabelAtlasError('Complete PNG exceeds byte budget')
        return super().write(value)


def _png(image, maximum):
    with _BoundedPng(maximum) as output:
        image.save(output, format='PNG', compress_level=6, optimize=False)
        return output.getvalue()


def _box(value, size):
    if (type(value) not in (tuple, list) or len(value) != 4
            or any(type(v) is not int for v in value)
            or not 0 <= value[0] < value[2] <= size[0]
            or not 0 <= value[1] < value[3] <= size[1]):
        raise PdfChartLabelAtlasError('Invalid source rectangle')
    return list(value)


def _gray_mask(source):
    """PIL C operations, bounded by source pixels; no colour palette assumptions."""
    red, green, blue = source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    spread = ImageChops.difference(maximum, minimum)
    try:
        # Exclude black frames/text and white canvas, retain neutral line pixels.
        neutral = spread.point([255 if v <= 8 else 0 for v in range(256)])
        shade = maximum.point([255 if 100 <= v <= 245 else 0 for v in range(256)])
        try:
            return ImageChops.multiply(neutral, shade)
        finally:
            neutral.close()
            shade.close()
    finally:
        for image in (red, green, blue, maximum, minimum, spread):
            image.close()


def _line_bands(mask, box, direction, maximum):
    left, top, right, bottom = box
    inset = max(2, min(8, (right-left)//100, (bottom-top)//100))
    interior = [left+inset, top+inset, right-inset, bottom-inset]
    if interior[0] >= interior[2] or interior[1] >= interior[3]:
        return []
    with mask.crop(interior) as cropped:
        view = cropped.transpose(Image.Transpose.TRANSPOSE) if direction == 'x' else cropped.copy()
        try:
            width, height = view.size
            data = view.tobytes()
            runs, active = [], []
            for index in range(height):
                row = data[index*width:(index+1)*width]
                present = width >= 8 and row.count(255)*10 >= width*7
                if present:
                    active.append(index)
                elif active:
                    runs.append(active)
                    active = []
            if active:
                runs.append(active)
        finally:
            view.close()
    # A neutral filled background is not a thin long-line observation.
    runs = [run for run in runs if len(run) <= max(2, min(8, height//20))]
    if len(runs) > maximum:
        raise PdfChartLabelAtlasError('Complete anchor candidates exceed budget')
    result = []
    for run in runs:
        low, high = run[0], run[-1]+1
        coordinate = (low+high-1)/2 + interior[0 if direction == 'x' else 1]
        source_box = ([interior[0]+low, interior[1], interior[0]+high, interior[3]]
                      if direction == 'x' else
                      [interior[0], interior[1]+low, interior[2], interior[1]+high])
        result.append({'direction': direction, 'source_coordinate': coordinate,
            'source_pixel_bbox': source_box, 'classification': 'neutral_long_line_candidate',
            'tick_binding_verified': False, 'numeric_value': None})
    return result


def _sample_colors(raw, size, candidates, limits):
    if type(candidates) not in (tuple, list) or len(candidates) > limits.colors:
        raise PdfChartLabelAtlasError('Complete RGB candidates exceed budget')
    seen, result = set(), []
    for rgb in candidates:
        if (type(rgb) not in (list, tuple) or len(rgb) != 3
                or any(type(v) is not int or not 0 <= v <= 255 for v in rgb)
                or tuple(rgb) in seen):
            raise PdfChartLabelAtlasError('Invalid or repeated exact RGB candidate')
        seen.add(tuple(rgb))
        offset, attempts = -1, 0
        while True:
            offset = raw.find(bytes(rgb), offset+1)
            if offset < 0:
                raise PdfChartLabelAtlasError('RGB candidate absent from exact source pixels')
            attempts += 1
            if attempts > limits.color_search_matches:
                raise PdfChartLabelAtlasError('Complete RGB candidate search exceeds budget')
            if offset % 3 == 0:
                break
        pixel = offset//3
        result.append({'swatch_id': 'S'+str(len(result)+1).zfill(3), 'rgb': list(rgb),
            'source_pixel_xy': [pixel % size[0], pixel//size[0]],
            'source_pixel_sha256': sha256(bytes(rgb)).hexdigest(),
            'synthetic_source_derived': True, 'legend_binding_verified': False})
    return result


@dataclass(frozen=True, slots=True)
class PdfChartLabelAtlas:
    metadata_json: str
    png: bytes

    def identity(self):
        return {'version': VERSION, 'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest(),
                'png_sha256': sha256(self.png).hexdigest()}


def build_chart_label_atlas(image, panels, candidate_rgbs=(), *,
                           limits=PdfChartLabelAtlasLimits(), reference_panel_index=None):
    """Pure PIL atlas of ALL candidate strips, one full reference and RGB chips.

    Rectangles use the native detector's axes_pixel_bbox/crop_pixel_bbox. Without
    an explicit reference index, the lower-left spatial panel is selected; this
    never establishes legend ownership. Source rectangles are pasted unchanged.
    All budgets apply to complete candidates, before sheet allocation. No
    marker filtering, numerical calibration or label interpretation occurs.
    """
    if (type(limits) is not PdfChartLabelAtlasLimits or not isinstance(image, Image.Image)
            or image.mode != 'RGB' or min(image.size) <= 0
            or image.width*image.height > limits.source_pixels):
        raise PdfChartLabelAtlasError('Bounded exact RGB source required')
    if type(panels) not in (tuple, list) or not 1 <= len(panels) <= limits.panels:
        raise PdfChartLabelAtlasError('Complete measured panel candidates required')
    measured, seen = [], set()
    for index, panel in enumerate(panels):
        if type(panel) is not dict:
            raise PdfChartLabelAtlasError('Invalid measured panel record')
        axes = _box(panel.get('axes_pixel_bbox'), image.size)
        crop = _box(panel.get('crop_pixel_bbox'), image.size)
        if not (crop[0] <= axes[0] < axes[2] <= crop[2]
                and crop[1] <= axes[1] < axes[3] <= crop[3]) or tuple(axes) in seen:
            raise PdfChartLabelAtlasError('Panel crop must contain unique measured axes')
        seen.add(tuple(axes))
        measured.append({'panel_id': 'P'+str(index+1).zfill(3),
            'axes_pixel_bbox': axes, 'crop_pixel_bbox': crop})
    if reference_panel_index is None:
        reference_panel_index = min(range(len(measured)), key=lambda n:
            (-measured[n]['axes_pixel_bbox'][1], measured[n]['axes_pixel_bbox'][0]))
    if type(reference_panel_index) is not int or not 0 <= reference_panel_index < len(measured):
        raise PdfChartLabelAtlasError('Invalid spatial reference index')
    with image.copy() as source:
        source_png = _png(source, limits.png_bytes)
        raw = source.tobytes()
        swatches = _sample_colors(raw, source.size, candidate_rgbs, limits)
        font = ImageFont.load_default(size=14)
        title_height, gutter, label_height = 26, 170, 20
        width, height = 360, 0
        with _gray_mask(source) as mask:
            for panel in measured:
                axes, crop = panel['axes_pixel_bbox'], panel['crop_pixel_bbox']
                # Midpoint native columns may bisect long decimal tick labels.
                # Retain the whole inter-frame margin as unowned label context.
                neighbours = [p['axes_pixel_bbox'][2] for p in measured
                    if p['axes_pixel_bbox'][2] <= axes[0]
                    and min(p['axes_pixel_bbox'][3], axes[3]) > max(p['axes_pixel_bbox'][1], axes[1])]
                ybox = [max(neighbours, default=0), crop[1], axes[0], crop[3]]
                xbox = [crop[0], axes[3], crop[2], crop[3]]
                panel['y_label_source_bbox'] = ybox if ybox[0] < ybox[2] else None
                panel['x_condition_source_bbox'] = xbox if xbox[1] < xbox[3] else None
                panel['anchors'] = []
                panel['unresolved'] = []
                for direction in ('y', 'x'):
                    anchors = _line_bands(mask, axes, direction, limits.anchors_per_axis)
                    if not anchors:
                        panel['unresolved'].append('no_'+direction+'_neutral_long_line_candidates')
                    for ordinal, anchor in enumerate(anchors, 1):
                        anchor['anchor_id'] = panel['panel_id']+'-'+direction.upper()+str(ordinal).zfill(3)
                        with source.crop(anchor['source_pixel_bbox']) as pixels:
                            anchor['source_pixel_sha256'] = sha256(pixels.tobytes()).hexdigest()
                        panel['anchors'].append(anchor)
                yw = (ybox[2]-ybox[0]) if panel['y_label_source_bbox'] else 0
                yh = (ybox[3]-ybox[1]) if panel['y_label_source_bbox'] else 0
                xw = (xbox[2]-xbox[0]) if panel['x_condition_source_bbox'] else 0
                xh = (xbox[3]-xbox[1]) if panel['x_condition_source_bbox'] else 0
                if not yw:
                    panel['unresolved'].append('empty_y_label_strip')
                if not xh:
                    panel['unresolved'].append('empty_x_condition_strip')
                panel['_y_width'] = yw
                next_y = 0
                lanes = []
                for anchor in panel['anchors']:
                    text = 'SERVER '+anchor['anchor_id']
                    tw = font.getbbox(text)[2]+4
                    if anchor['direction'] == 'y':
                        sy = anchor['source_coordinate']-ybox[1]
                        y = max(next_y, int(sy)-label_height//2, 0)
                        anchor['_label_local_bbox'] = [yw+8, y, yw+gutter-4, y+label_height]
                        next_y = y+label_height
                    else:
                        sx = anchor['source_coordinate']-crop[0]
                        left = max(0, min(int(sx)-tw//2, max(0, xw-tw)))
                        lane = next((n for n, end in enumerate(lanes) if end+4 <= left), len(lanes))
                        if lane == len(lanes):
                            lanes.append(left+tw)
                        else:
                            lanes[lane] = left+tw
                        y = xh+8+lane*label_height
                        anchor['_label_local_bbox'] = [left, y, left+tw, y+label_height]
                panel['_y_card_width'] = yw+gutter
                panel['_y_card_height'] = title_height+max(yh, next_y)+12
                panel['_x_card_width'] = max(360, xw)
                panel['_x_card_height'] = title_height+xh+8+len(lanes)*label_height+12
                width = max(width, panel['_y_card_width'], panel['_x_card_width'])
        reference = measured[reference_panel_index]
        box = reference['crop_pixel_bbox']
        width = max(width, box[2]-box[0])
        # Shelf-pack narrow y cards and then wide x cards separately. This
        # avoids large whitespace reservations without resizing any pixels.
        for direction in ('y', 'x'):
            cursor_x, cursor_y, row_height = 0, height, 0
            for panel in measured:
                card_width = panel['_'+direction+'_card_width']
                card_height = panel['_'+direction+'_card_height']
                if cursor_x and cursor_x+card_width > width:
                    cursor_x, cursor_y, row_height = 0, cursor_y+row_height, 0
                panel['_'+direction+'_left'] = cursor_x
                panel['_'+direction+'_top'] = cursor_y
                cursor_x += card_width
                row_height = max(row_height, card_height)
            height = cursor_y+row_height+12
        reference_top = height+title_height
        height = reference_top+box[3]-box[1]+12
        swatch_top = height+title_height
        height = swatch_top+len(swatches)*36+12
        if width*height > limits.atlas_pixels:
            raise PdfChartLabelAtlasError('Complete unscaled atlas exceeds pixel budget')
        crops, synthetic = [], []
        with Image.new('RGB', (width, height), 'white') as sheet:
            draw = ImageDraw.Draw(sheet)
            def label(text, box):
                draw.text((box[0], box[1]), text, fill='black', font=font)
                synthetic.append({'text': text, 'atlas_bbox': box,
                    'server_added': True, 'outside_original_source_crops': True})
            def paste(kind, source_box, left, top, panel_id):
                with source.crop(source_box) as pixels:
                    sheet.paste(pixels, (left, top))
                    row = {'kind': kind, 'panel_id': panel_id, 'source_pixel_bbox': source_box,
                        'atlas_pixel_bbox': [left, top, left+pixels.width, top+pixels.height],
                        'source_pixel_sha256': sha256(pixels.tobytes()).hexdigest(),
                        'unscaled_exact_source_crop': True}
                    crops.append(row)
                    return row
            for panel in measured:
                yl, yt = panel['_y_left'], panel['_y_top']
                xl, xt = panel['_x_left'], panel['_x_top']
                yorigin, xorigin = yt+title_height, xt+title_height
                label('SERVER '+panel['panel_id']+' Y LABELS', [yl, yt, yl+panel['_y_card_width'], yorigin])
                label('SERVER '+panel['panel_id']+' X CONDITIONS', [xl, xt, xl+panel['_x_card_width'], xorigin])
                if panel['y_label_source_bbox']:
                    paste('y_label_strip', panel['y_label_source_bbox'], yl, yorigin, panel['panel_id'])
                if panel['x_condition_source_bbox']:
                    paste('x_condition_strip', panel['x_condition_source_bbox'], xl, xorigin, panel['panel_id'])
                for anchor in panel['anchors']:
                    local = anchor.pop('_label_local_bbox')
                    origin_left, origin_top = (yl, yorigin) if anchor['direction'] == 'y' else (xl, xorigin)
                    box = [origin_left+local[0], origin_top+local[1], origin_left+local[2], origin_top+local[3]]
                    label('SERVER '+anchor['anchor_id'], box)
                    anchor['server_label_atlas_bbox'] = box
                    if anchor['direction'] == 'y' and panel['y_label_source_bbox']:
                        sy = yorigin+anchor['source_coordinate']-panel['y_label_source_bbox'][1]
                        draw.line((yl+panel['_y_width']+1, sy, box[0]-1, box[1]+label_height//2), fill=(90,90,90))
                    elif anchor['direction'] == 'x' and panel['x_condition_source_bbox']:
                        sx = panel['_x_left']+anchor['source_coordinate']-panel['crop_pixel_bbox'][0]
                        bottom = xorigin+panel['x_condition_source_bbox'][3]-panel['x_condition_source_bbox'][1]
                        draw.line((sx, bottom+1, sx, box[1]-1), fill=(90,90,90))
                for key in tuple(panel):
                    if key.startswith('_'):
                        del panel[key]
            label('SERVER SPATIAL REFERENCE ONLY', [0, reference_top-title_height, width, reference_top])
            reference_crop = paste('full_spatial_reference', reference['crop_pixel_bbox'], 0,
                                   reference_top, reference['panel_id'])
            label('SERVER SYNTHETIC SOURCE-DERIVED RGB CHIPS', [0, swatch_top-title_height, width, swatch_top])
            for ordinal, swatch in enumerate(swatches):
                top = swatch_top+ordinal*36
                draw.rectangle((0, top, 47, top+23), fill=tuple(swatch['rgb']))
                swatch['synthetic_atlas_bbox'] = [0, top, 48, top+24]
                label('SERVER '+swatch['swatch_id']+' SYNTHETIC', [58, top, width, top+24])
            encoded = _png(sheet, limits.png_bytes)
            metadata = {'version': VERSION, 'source': {'mode': 'RGB', 'dimensions': list(source.size),
                'pixel_sha256': sha256(raw).hexdigest(), 'rgb_reencoded_png_sha256': sha256(source_png).hexdigest()},
                'limits': asdict(limits), 'panels': measured, 'exact_source_crops': crops,
                'reference': {**reference_crop, 'context_only': True, 'legend_ownership_proven': False},
                'swatches': swatches, 'synthetic_labels': synthetic,
                'atlas': {'dimensions': [width, height], 'png_bytes': len(encoded),
                    'png_sha256': sha256(encoded).hexdigest(), 'pixel_sha256': sha256(sheet.tobytes()).hexdigest()},
                'axes_calibrated': False, 'semantic_ownership_proven': False,
                'chart_classification_proven': False, 'numerical_calibration_performed': False,
                'marker_candidates_filtered': False, 'x_condition_binding_verified': False,
                'anchor_detection_exhaustive': False, 'strip_label_binding_verified': False}
            metadata_json = _json(metadata)
            if len(metadata_json.encode('utf-8')) > limits.metadata_bytes:
                raise PdfChartLabelAtlasError('Complete atlas metadata exceeds byte budget')
            return PdfChartLabelAtlas(metadata_json, encoded)


COMPACT_VERSION = 'pdf-chart-label-atlas-compact-geometry-v1'


@dataclass(frozen=True, slots=True)
class PdfCompactChartLabelAtlas:
    metadata_json: str
    pngs: tuple[bytes, bytes]

    def identity(self):
        return {'version': COMPACT_VERSION,
                'metadata_sha256': sha256(self.metadata_json.encode()).hexdigest(),
                'png_sha256': [sha256(png).hexdigest() for png in self.pngs]}


def _tile_layout(cards, columns, font, top=40):
    """Only move exact crops; common cells and variable row heights, no scaling."""
    padding, label_height = 8, 36
    width = max([font.getbbox(card['server_label'])[2]+2*padding for card in cards]
                + [card['source_pixel_bbox'][2]-card['source_pixel_bbox'][0]+2*padding
                   for card in cards] + [240])
    columns = min(columns, max(1, len(cards)))
    left, row_top, row_height = 0, top, 0
    for ordinal, card in enumerate(cards):
        if ordinal and ordinal % columns == 0:
            left, row_top, row_height = 0, row_top+row_height, 0
        box = card['source_pixel_bbox']
        height = box[3]-box[1]+label_height+2*padding
        card['tile_pixel_bbox'] = [left, row_top, left+width, row_top+height]
        card['server_label_atlas_bbox'] = [left+padding, row_top+padding,
                                         left+width-padding, row_top+label_height]
        card['atlas_pixel_bbox'] = [left+padding, row_top+label_height+padding,
                                   left+padding+box[2]-box[0],
                                   row_top+label_height+padding+box[3]-box[1]]
        left += width
        row_height = max(row_height, height)
    return [width*columns, row_top+row_height+8]


def build_compact_chart_label_atlas(image, base_atlas, *,
                                   limits=PdfChartLabelAtlasLimits(), columns=5):
    """Optional two-image diagnostic, retaining every observed base anchor.

    Tick fragments use 40% of the nearest measured spacing on either side.
    Condition fragments use adjacent-anchor midpoints, with a symmetric nearest
    spacing at the ends. Neither rule knows glyph widths or missing ticks: source
    fragment clipping and visible-label ownership remain explicitly unknown.
    The base atlas is immutable; this function performs no reading/calibration.
    """
    if (type(limits) is not PdfChartLabelAtlasLimits or not isinstance(image, Image.Image)
            or image.mode != 'RGB' or min(image.size) <= 0
            or image.width*image.height > limits.source_pixels):
        raise PdfChartLabelAtlasError('Bounded exact RGB source required')
    if type(columns) is not int or not 4 <= columns <= 6:
        raise PdfChartLabelAtlasError('Compact tick columns must be between four and six')
    if (type(base_atlas) is not PdfChartLabelAtlas
            or type(base_atlas.metadata_json) is not str or type(base_atlas.png) is not bytes
            or len(base_atlas.metadata_json.encode()) > limits.metadata_bytes
            or len(base_atlas.png) > limits.png_bytes):
        raise PdfChartLabelAtlasError('Bounded immutable base atlas required')
    try:
        base = json.loads(base_atlas.metadata_json)
    except (ValueError, TypeError) as error:
        raise PdfChartLabelAtlasError('Invalid base atlas metadata') from error
    raw = image.tobytes()
    if (type(base) is not dict or base.get('version') != VERSION
            or type(base.get('source')) is not dict or type(base.get('atlas')) is not dict
            or base.get('source', {}).get('dimensions') != list(image.size)
            or base.get('source', {}).get('mode') != 'RGB'
            or base.get('source', {}).get('pixel_sha256') != sha256(raw).hexdigest()
            or base.get('atlas', {}).get('png_sha256') != sha256(base_atlas.png).hexdigest()):
        raise PdfChartLabelAtlasError('Base atlas source or PNG identity mismatch')
    panels = base.get('panels')
    if type(panels) is not list or not 1 <= len(panels) <= limits.panels:
        raise PdfChartLabelAtlasError('Complete measured panels exceed budget')
    font = ImageFont.load_default(size=24)
    cards = [[], []]
    seen = set()
    unresolved = []
    for panel in panels:
        if type(panel) is not dict or type(panel.get('panel_id')) is not str:
            raise PdfChartLabelAtlasError('Invalid base panel')
        axes = _box(panel.get('axes_pixel_bbox'), image.size)
        crop = _box(panel.get('crop_pixel_bbox'), image.size)
        if not (crop[0] <= axes[0] < axes[2] <= crop[2]
                and crop[1] <= axes[1] < axes[3] <= crop[3]):
            raise PdfChartLabelAtlasError('Panel crop must contain measured axes')
        anchors = panel.get('anchors')
        if type(anchors) is not list or len(anchors) > limits.anchors_per_axis*2:
            raise PdfChartLabelAtlasError('Complete anchors exceed budget')
        for direction, image_index in (('y', 0), ('x', 1)):
            if any(type(a) is not dict or a.get('direction') not in ('x', 'y') for a in anchors):
                raise PdfChartLabelAtlasError('Invalid source anchor')
            if any(type(a.get('source_coordinate')) not in (int,float)
                   or not isfinite(a['source_coordinate']) for a in anchors):
                raise PdfChartLabelAtlasError('Finite source anchors required')
            selected = sorted([a for a in anchors if a['direction'] == direction],
                              key=lambda a: a.get('source_coordinate', -1))
            if len(selected) > limits.anchors_per_axis:
                raise PdfChartLabelAtlasError('Complete anchors exceed budget')
            strip = panel.get('y_label_source_bbox' if direction == 'y' else 'x_condition_source_bbox')
            if strip is None or not selected:
                unresolved.append({'panel_id': panel['panel_id'], 'direction': direction,
                                   'reason': 'no_observed_anchor_or_source_strip'})
                if strip is None and selected:
                    raise PdfChartLabelAtlasError('Observed anchor has no source strip')
                continue
            strip = _box(strip, image.size)
            positions = [a.get('source_coordinate') for a in selected]
            if (any(type(v) not in (int, float) or not isfinite(v) for v in positions)
                    or any(a >= b for a, b in zip(positions, positions[1:]))):
                raise PdfChartLabelAtlasError('Distinct finite source anchors required')
            for ordinal, anchor in enumerate(selected):
                coordinate = positions[ordinal]
                anchor_id = anchor.get('anchor_id')
                box = _box(anchor.get('source_pixel_bbox'), image.size)
                axis_index = 1 if direction == 'y' else 0
                if (type(anchor_id) is not str or not anchor_id or len(anchor_id) > 64
                        or not all(c.isalnum() or c in '-_' for c in anchor_id)
                        or anchor_id in seen or not axes[axis_index] <= coordinate < axes[axis_index+2]
                        or not box[axis_index] <= coordinate < box[axis_index+2]):
                    raise PdfChartLabelAtlasError('Invalid or repeated observed anchor ID')
                seen.add(anchor_id)
                with image.crop(box) as pixels:
                    if sha256(pixels.tobytes()).hexdigest() != anchor.get('source_pixel_sha256'):
                        raise PdfChartLabelAtlasError('Source anchor pixels mismatch')
                distances = [abs(coordinate-v) for v in positions[max(0,ordinal-1):ordinal+2]
                             if v != coordinate]
                spacing = min(distances) if distances else (axes[axis_index+2]-axes[axis_index])/4
                if direction == 'y':
                    radius = max(1, spacing*.4)
                    # Keep neighbouring glyph context AND this axis's right edge.
                    edge = max(2, min(8, (axes[2]-axes[0])//100))
                    fragment = [strip[0], max(strip[1], floor(coordinate-radius)),
                                min(image.width, axes[0]+edge),
                                min(strip[3], ceil(coordinate+radius))]
                else:
                    left = (positions[ordinal-1]+coordinate)/2 if ordinal else coordinate-spacing/2
                    right = (coordinate+positions[ordinal+1])/2 if ordinal+1 < len(positions) else coordinate+spacing/2
                    fragment = [max(strip[0], floor(left)), strip[1],
                                min(strip[2], ceil(right)), strip[3]]
                fragment = _box(fragment, image.size)
                cards[image_index].append({'kind': direction+'_anchor_label_fragment',
                    'panel_id': panel['panel_id'], 'anchor_id': anchor_id,
                    'source_coordinate': coordinate, 'source_anchor_pixel_bbox': box,
                    'source_anchor_pixel_sha256': anchor['source_pixel_sha256'],
                    'source_pixel_bbox': fragment, 'image_index': image_index,
                    'server_label': 'SERVER '+anchor_id,
                    'source_fragment_clipping_possible': True,
                    'visible_label_limit_verified': False, 'label_binding_verified': False})
    dimensions = [_tile_layout(cards[0], columns, font), _tile_layout(cards[1], 3, font)]
    reference = base.get('reference', {})
    if type(reference) is not dict:
        raise PdfChartLabelAtlasError('Invalid full spatial reference')
    reference_box = _box(reference.get('source_pixel_bbox'), image.size)
    reference_top = dimensions[1][1]+36
    reference_width, reference_height = reference_box[2]-reference_box[0], reference_box[3]-reference_box[1]
    dimensions[1][0] = max(dimensions[1][0], reference_width+16)
    dimensions[1][1] = reference_top+reference_height+8
    base_swatches = base.get('swatches')
    if type(base_swatches) is not list or any(type(s) is not dict for s in base_swatches):
        raise PdfChartLabelAtlasError('Complete exact RGB swatches required')
    swatches = _sample_colors(raw, image.size, [s.get('rgb') for s in base_swatches], limits)
    chip_width, chip_height = 220, 104
    chip_columns = max(1, dimensions[1][0]//chip_width)
    chip_top = dimensions[1][1]+36
    dimensions[1][1] = chip_top+ceil(len(swatches)/chip_columns)*chip_height+8
    if sum(w*h for w,h in dimensions) > limits.atlas_pixels:
        raise PdfChartLabelAtlasError('Complete compact pair exceeds pixel budget')
    crops, labels, atlas_rows, encoded = [], [], [], []
    def label(draw, text, box, image_index):
        draw.text((box[0],box[1]), text, fill='black', font=font)
        labels.append({'text':text, 'atlas_bbox':box, 'image_index':image_index,
                       'font_pixels':24, 'server_added':True, 'outside_original_source_crops':True})
    def paste(sheet, row):
        with image.crop(row['source_pixel_bbox']) as pixels:
            sheet.paste(pixels, tuple(row['atlas_pixel_bbox'][:2]))
            crops.append({**row, 'source_pixel_sha256':sha256(pixels.tobytes()).hexdigest(),
                          'unscaled_exact_source_crop':True})
    for image_index, size in enumerate(dimensions):
        with Image.new('RGB', tuple(size), 'white') as sheet:
            draw = ImageDraw.Draw(sheet)
            label(draw, 'SERVER LABEL FRAGMENTS - UNVERIFIED', [8,0,size[0],36], image_index)
            for card in cards[image_index]:
                label(draw, card['server_label'], card['server_label_atlas_bbox'], image_index)
                paste(sheet, card)
            if image_index == 1:
                label(draw, 'SERVER SPATIAL REFERENCE ONLY', [8,reference_top-36,size[0],reference_top],1)
                paste(sheet, {'kind':'full_spatial_reference','panel_id':reference.get('panel_id'),
                    'source_pixel_bbox':reference_box,'image_index':1,
                    'atlas_pixel_bbox':[8,reference_top,8+reference_width,reference_top+reference_height],
                    'context_only':True,'legend_ownership_proven':False})
                label(draw, 'SERVER SYNTHETIC SOURCE-DERIVED RGB CHIPS', [8,chip_top-36,size[0],chip_top],1)
                for ordinal, swatch in enumerate(swatches):
                    left, top = (ordinal % chip_columns)*chip_width+8, chip_top+(ordinal//chip_columns)*chip_height
                    label(draw, 'SERVER '+swatch['swatch_id'], [left,top,left+chip_width-8,top+36],1)
                    draw.rectangle((left,top+40,left+95,top+95), fill=tuple(swatch['rgb']))
                    swatch.update({'image_index':1,'synthetic_atlas_bbox':[left,top+40,left+96,top+96]})
            png = _png(sheet, limits.png_bytes-sum(map(len,encoded)))
            encoded.append(png)
            atlas_rows.append({'image_index':image_index,'dimensions':size,'png_bytes':len(png),
                               'png_sha256':sha256(png).hexdigest(),
                               'pixel_sha256':sha256(sheet.tobytes()).hexdigest()})
    metadata = {'version':COMPACT_VERSION,'source':base['source'], 'base_atlas_identity':base_atlas.identity(),
        'limits':asdict(limits),'atlases':atlas_rows,'exact_source_crops':crops,
        'synthetic_labels':labels,'swatches':swatches,'unresolved':unresolved,
        'observed_anchor_count':len(seen),'synthetic_server_font_pixels':24,
        'source_fragment_clipping_status':'unknown','visible_condition_limit_verified':False,
        'anchor_detection_exhaustive':False,'missing_anchors_inferred':False,
        'axes_calibrated':False,'semantic_ownership_proven':False,'strip_label_binding_verified':False,
        'numerical_calibration_performed':False,'marker_candidates_filtered':False,
        'chart_classification_proven':False,'x_condition_binding_verified':False}
    metadata_json = _json(metadata)
    if len(metadata_json.encode()) > limits.metadata_bytes:
        raise PdfChartLabelAtlasError('Complete compact metadata exceeds byte budget')
    return PdfCompactChartLabelAtlas(metadata_json, tuple(encoded))
