"""Isolated source-resolution PDF raster details, not chart semantic proof.

These are uncomposited image-resource pixels, supplementary to the checked
full-page appearance. Masks, overlays and clipping may make resource details
different from the page. Never replace that context with a hash-only image.
"""
import base64
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
from io import BytesIO
import json
import math
from pathlib import Path
import re
import secrets
import struct

from ..document_pdf_process import _run_worker, PdfProcessPolicy, PdfProcessError
from ..llm_images import ModelImageInput, ModelImagePolicy, validate_image_inputs
from .pdf_visual_source import checked_visual_source, PreparedPdfVisualSource

VERSION = 'pdf-native-raster-detail-candidate-v1'
_KEY = secrets.token_bytes(32)


class PdfNativeFigureError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class PdfNativeFigureLimits:
    source_bytes: int = 67_108_864
    pages: int = 1000
    objects: int = 4096
    image_pixels: int = 16_000_000
    total_pixels: int = 128_000_000
    image_bytes: int = 16_777_216
    total_bytes: int = 67_108_864
    manifest_bytes: int = 4_194_304
    panels: int = 64

    def __post_init__(self):
        ceilings = (67_108_864, 1000, 4096, 16_000_000, 128_000_000,
                    16_777_216, 67_108_864, 4_194_304, 64)
        if any(type(v) is not int or not 1 <= v <= maximum
               for v, maximum in zip(asdict(self).values(), ceilings)):
            raise PdfNativeFigureError('Invalid native image limits')


def detect_rectangular_panels(image, *, maximum=64, allow_antialiased_axes=False):
    """Geometry-only bordered rectangles. No axis values, legend or semantics.

    Regex scans a bounded monochrome byte map; it does not resize source pixels.
    Frames on a shared rectangular grid get partitioned, non-overlapping zooms
    that retain surrounding labels. A diagram/table frame is not a proven plot.
    The opt-in fallback observes thin neutral-gray frames only when the historical
    detector finds none; it never changes a successful historical inventory.
    """
    from PIL import Image
    if type(maximum) is not int or not 1 <= maximum <= 64:
        raise PdfNativeFigureError('Invalid panel limit')
    if type(allow_antialiased_axes) is not bool:
        raise PdfNativeFigureError('Invalid antialiased axis mode')
    width, height = image.size
    if width * height > 16_000_000:
        raise PdfNativeFigureError('Panel detector pixel budget exceeded')
    if min(width, height) < 64:
        return []
    dark = image.convert('L').point([1 if i < 70 else 0 for i in range(256)])

    def bands(bitmap, minimum, minimum_rows=2):
        w, h = bitmap.size
        raw = bitmap.tobytes()
        pattern = re.compile(b'\x01{' + str(minimum).encode() + b',}')
        groups = []
        for y in range(h):
            for match in pattern.finditer(raw, y*w, (y+1)*w):
                span = (match.start()-y*w, match.end()-y*w)
                found = next((g for g in reversed(groups[-16:]) if y-g[-1][0] == 1
                              and abs(g[-1][1][0]-span[0]) <= 3
                              and abs(g[-1][1][1]-span[1]) <= 3), None)
                if found is None:
                    groups.append([(y, span)])
                else:
                    found.append((y, span))
                if len(groups) > 4096:
                    raise PdfNativeFigureError('Panel line budget exceeded')
        return [(g[len(g)//2][0], *g[len(g)//2][1]) for g in groups if len(g) >= minimum_rows]

    def rectangles(bitmap, minimum_rows, *, check_corners=False):
        horizontal = bands(bitmap, max(32, width//12), minimum_rows)
        vertical = bands(bitmap.transpose(Image.Transpose.TRANSPOSE), max(32, height//8), minimum_rows)
        pixels = bitmap.load()
        frames = []
        for top, left, right in horizontal:
            for bottom, l2, r2 in horizontal:
                if bottom-top < max(32, height//8) or abs(left-l2)>4 or abs(right-r2)>4:
                    continue
                side_left = [x for x, y0, y1 in vertical if abs(x-left)<=4
                             and abs(y0-top)<=4 and abs(y1-bottom)<=5]
                side_right = [x for x, y0, y1 in vertical if abs(x-right)<=5
                              and abs(y0-top)<=4 and abs(y1-bottom)<=5]
                if side_left and side_right:
                    box = [min(side_left), top, max(side_right), bottom]
                    if check_corners and not all(any(pixels[x, y] == 1
                            for x in range(max(0, cx-1), min(width, cx+2))
                            for y in range(max(0, cy-1), min(height, cy+2)))
                            for cx, cy in ((box[0], top), (box[2], top),
                                           (box[0], bottom), (box[2], bottom))):
                        continue
                    if not any(max(abs(a-b) for a,b in zip(box,old)) <= 5 for old in frames):
                        frames.append(box)
                        if len(frames) > maximum:
                            raise PdfNativeFigureError('Panel count budget exceeded')
        return frames

    frames = rectangles(dark, 2)
    if not frames and allow_antialiased_axes:
        from PIL import ImageChops
        red, green, blue = image.convert('RGB').split()
        light = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        shade = ImageChops.darker(ImageChops.darker(red, green), blue)
        neutral = ImageChops.subtract(light, shade).point([1 if i <= 12 else 0 for i in range(256)])
        gray = light.point([1 if i < 160 else 0 for i in range(256)])
        frames = rectangles(ImageChops.darker(neutral, gray), 1, check_corners=True)
    frames.sort(key=lambda b:(b[1],b[0]))
    result = []
    for box in frames:
        left,top,right,bottom=box
        neighbours_left=[b[2] for b in frames if b[2]<left and abs(b[1]-top)<=5 and abs(b[3]-bottom)<=5]
        neighbours_right=[b[0] for b in frames if b[0]>right and abs(b[1]-top)<=5 and abs(b[3]-bottom)<=5]
        neighbours_top=[b[3] for b in frames if b[3]<top and abs(b[0]-left)<=5 and abs(b[2]-right)<=5]
        neighbours_bottom=[b[1] for b in frames if b[1]>bottom and abs(b[0]-left)<=5 and abs(b[2]-right)<=5]
        crop=[(max(neighbours_left)+left)//2 if neighbours_left else max(0,left-(right-left)//3),
              (max(neighbours_top)+top)//2 if neighbours_top else max(0,top-(bottom-top)//5),
              (min(neighbours_right)+right)//2 if neighbours_right else min(width,right+(right-left)//5),
              (min(neighbours_bottom)+bottom)//2 if neighbours_bottom else min(height,bottom+(bottom-top)//4)]
        result.append({'axes_pixel_bbox':box,'crop_pixel_bbox':crop,
                       'classification':'rectangular_panel_candidate',
                       'axes_calibrated':False,'semantic_ownership_proven':False})
    return result


def extract_native_catalog(raw, *, limits=PdfNativeFigureLimits(), pages=None):
    """Worker-only native decoding. Dimension checks precede bitmap allocation."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as api
    if (type(limits) is not PdfNativeFigureLimits or type(raw) is not bytes
            or not 0 < len(raw) <= limits.source_bytes):
        raise PdfNativeFigureError('Invalid native source')
    if str(pdfium.PYPDFIUM_INFO) != '5.13.0' or str(pdfium.PDFIUM_INFO) != '153.0.7999.0':
        raise PdfNativeFigureError('Unpinned native renderer')
    result={'version':VERSION,'raw_sha256':sha256(raw).hexdigest(),'raw_bytes':len(raw),
            'renderer':'pypdfium2','renderer_version':'5.13.0',
            'pdfium_version':'153.0.7999.0','limits':asdict(limits),'pages':[]}
    pixels=encoded_bytes=objects=0
    with closing(pdfium.PdfDocument(raw)) as document:
        if api.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            raise PdfNativeFigureError('Encrypted source is not eligible')
        if not 0 < len(document) <= limits.pages:
            raise PdfNativeFigureError('Native source page limit exceeded')
        selected=tuple(range(1,len(document)+1)) if pages is None else pages
        if (type(selected) is not tuple or not selected or sorted(set(selected)) != list(selected)
                or any(type(p) is not int or not 1 <= p <= len(document) for p in selected)):
            raise PdfNativeFigureError('Invalid selected pages')
        result['selected_pages']=list(selected)
        for number in selected:
            with closing(document[number-1]) as page:
                width,height=page.get_size()
                row={'page':number,'width':width,'height':height,'images':[], 'unresolved_forms':[]}
                result['pages'].append(row)
                for ordinal,obj in enumerate(page.get_objects(max_depth=1),1):
                    objects+=1
                    if objects > limits.objects:
                        raise PdfNativeFigureError('Native source object limit exceeded')
                    if obj.type == api.FPDF_PAGEOBJ_FORM:
                        row['unresolved_forms'].append({'object_ordinal':ordinal,'reason':'nested_form_not_extracted'})
                        continue
                    if obj.type != api.FPDF_PAGEOBJ_IMAGE:
                        continue
                    size=obj.get_px_size()
                    l,b,r,t=obj.get_bounds()
                    matrix=obj.get_matrix()
                    transform=[getattr(matrix,n) for n in ('a','b','c','d','e','f')]
                    item={'object_ordinal':ordinal,'page_bbox':[l,height-t,r,height-b],
                          'matrix':transform,'width':size[0],'height':size[1],
                          'appearance_relation':'uncomposited-image-resource-detail',
                          'page_appearance_proven':False,'status':'unavailable'}
                    row['images'].append(item)
                    if not all(math.isfinite(v) for v in (*item['page_bbox'],*transform)):
                        raise PdfNativeFigureError('Nonfinite source geometry')
                    if (page.get_rotation()!=0 or abs(matrix.b)>1e-6 or abs(matrix.c)>1e-6
                            or matrix.a<=0 or matrix.d<=0 or l<0 or b<0 or r>width+.001 or t>height+.001):
                        item['reason']='non_axis_aligned_or_outside_page'
                        continue
                    count=size[0]*size[1]
                    if not size[0] or not size[1] or count > limits.image_pixels:
                        item['reason']='native_image_pixel_limit'
                        continue
                    if pixels+count > limits.total_pixels:
                        raise PdfNativeFigureError('Complete native pixels exceed budget')
                    with closing(obj.get_bitmap(render=False)) as bitmap:
                        if (bitmap.width,bitmap.height) != size:
                            raise PdfNativeFigureError('Native bitmap dimensions changed')
                        with bitmap.to_pil() as native:
                            image=native.convert('RGB')
                            try:
                                out=BytesIO()
                                image.save(out,format='PNG',compress_level=6,optimize=False)
                                data=out.getvalue()
                                if len(data)>limits.image_bytes or encoded_bytes+len(data)>limits.total_bytes:
                                    raise PdfNativeFigureError('Complete native PNG bytes exceed budget')
                                item.update(status='native',png_sha256=sha256(data).hexdigest(),
                                    pixel_sha256=sha256(image.tobytes()).hexdigest(),pixel_mode='RGB',
                                    byte_count=len(data),panels=detect_rectangular_panels(image,maximum=limits.panels),
                                    png=base64.b64encode(data).decode('ascii'))
                            finally:
                                image.close()
                    pixels+=count
                    encoded_bytes+=len(data)
    metadata={**result,'pages':[{**p,'images':[{k:v for k,v in im.items() if k!='png'} for im in p['images']]} for p in result['pages']]}
    if len(_json(metadata).encode()) > limits.manifest_bytes:
        raise PdfNativeFigureError('Complete native manifest exceeds budget')
    return result


@dataclass(frozen=True, slots=True)
class PreparedPdfNativeFigures:
    source: PreparedPdfVisualSource = field(repr=False)
    catalog_json: str = field(repr=False)
    images: tuple[ModelImageInput,...] = field(repr=False)
    limits: PdfNativeFigureLimits
    seal: str = field(repr=False)

    def identity(self):
        return {'version':VERSION,'source':self.source.identity(),
                'catalog_sha256':sha256(self.catalog_json.encode()).hexdigest(),
                'limits':asdict(self.limits),
                'images':[{'source_id':i.source_id,'sha256':sha256(i.data).hexdigest(),
                           'bytes':len(i.data),'media_type':i.media_type} for i in self.images]}


def _seal(value):
    return hmac.digest(_KEY,_json(value.identity()).encode(),'sha256').hex()


def checked_native_figures(value):
    if (type(value) is not PreparedPdfNativeFigures or type(value.limits) is not PdfNativeFigureLimits
            or type(value.catalog_json) is not str or type(value.images) is not tuple
            or type(value.seal) is not str or not hmac.compare_digest(value.seal,_seal(value))):
        raise PdfNativeFigureError('Unchanged process-local native figure preparation required')
    checked_visual_source(value.source)
    catalog=json.loads(value.catalog_json)
    if catalog['raw_sha256'] != value.source.geometry.raw_sha256 or catalog['version'] != VERSION:
        raise PdfNativeFigureError('Native figure source differs')
    return catalog


def prepare_native_figures(source, raw, *, python_executable, pages=None,
                           limits=PdfNativeFigureLimits(), process_policy=PdfProcessPolicy(), cancel_event=None):
    document=checked_visual_source(source)
    if (type(raw) is not bytes or len(raw)!=source.geometry.raw_bytes
            or sha256(raw).hexdigest()!=source.geometry.raw_sha256 or type(limits) is not PdfNativeFigureLimits):
        raise PdfNativeFigureError('Native extraction requires the same retained PDF')
    selected=tuple(range(1,len(document.pages)+1)) if pages is None else pages
    if (type(selected) is not tuple or not selected or sorted(set(selected)) != list(selected)
            or any(type(p) is not int or not 1 <= p <= len(document.pages) for p in selected)):
        raise PdfNativeFigureError('Invalid selected native pages')
    header=_json({'version':1,'raw_bytes':len(raw),'raw_sha256':source.geometry.raw_sha256,
                  'pages':list(selected),'limits':asdict(limits)}).encode()
    result,pid,_,_=_run_worker(Path(python_executable),
        Path(__file__).resolve().parents[1]/'_document_pdf_native_figure_worker.py',
        (struct.pack('!I',len(header)),header,raw),policy=process_policy,
        output_bytes=4*((limits.total_bytes+2)//3)+limits.manifest_bytes,cancel_event=cancel_event)
    try:
        def unique(pairs):
            value={}
            for name,item in pairs:
                if name in value:
                    raise ValueError('Repeated native worker JSON field')
                value[name]=item
            return value
        def nonfinite(value):
            raise ValueError('Nonfinite native worker value')
        catalog=json.loads(result,object_pairs_hook=unique,parse_constant=nonfinite)
        if (type(catalog) is not dict or set(catalog)!={'version','raw_sha256','raw_bytes',
                'renderer','renderer_version','pdfium_version','limits','pages','selected_pages'}
                or catalog['version']!=VERSION or catalog['raw_sha256']!=source.geometry.raw_sha256
                or catalog['raw_bytes']!=len(raw) or catalog['selected_pages']!=list(selected)
                or catalog['renderer']!='pypdfium2' or catalog['renderer_version']!='5.13.0'
                or catalog['pdfium_version']!='153.0.7999.0' or catalog['limits']!=asdict(limits)
                or [p['page'] for p in catalog['pages']]!=list(selected)):
            raise ValueError('Native worker identity differs')
        images=[]
        for page in catalog['pages']:
            if (type(page) is not dict or set(page)!={'page','width','height','images','unresolved_forms'}
                    or any(type(page[k]) not in (int,float) or not math.isfinite(page[k]) or page[k]<=0
                           for k in ('width','height')) or type(page['images']) is not list):
                raise ValueError('Invalid native page record')
            geometry=document.pages[page['page']-1]
            if abs(page['width']-geometry.width)>.001 or abs(page['height']-geometry.height)>.001:
                raise ValueError('Native and retained canvases differ')
            for item in page['images']:
                if type(item) is not dict or item.get('status') not in ('native','unavailable'):
                    raise ValueError('Invalid native resource status')
                if item['status']!='native':
                    if 'png' in item or item.get('reason') not in ('non_axis_aligned_or_outside_page','native_image_pixel_limit'):
                        raise ValueError('Unavailable resource contains untrusted pixels')
                    continue
                data=base64.b64decode(item.pop('png'),validate=True)
                if len(data)!=item['byte_count'] or sha256(data).hexdigest()!=item['png_sha256']:
                    raise ValueError('Native pixel transport changed')
                image_id='PN_'+sha256(_json([catalog['raw_sha256'],page['page'],
                    item['object_ordinal'],item['page_bbox'],item['pixel_sha256']]).encode()).hexdigest()
                item['source_id']=image_id
                images.append(ModelImageInput(image_id,'image/png',data))
        if images:
            metadata=validate_image_inputs(tuple(images),ModelImagePolicy(limits.objects,limits.image_bytes,
                limits.total_bytes,limits.image_pixels,limits.total_pixels))
            native=[i for p in catalog['pages'] for i in p['images'] if i['status']=='native']
            if any((m['width'],m['height'])!=(i['width'],i['height']) for m,i in zip(metadata,native,strict=True)):
                raise ValueError('Native image dimensions differ')
        encoded=_json(catalog)
        if len(encoded.encode())>limits.manifest_bytes:
            raise ValueError('Native metadata budget exceeded')
        value=PreparedPdfNativeFigures(source,encoded,tuple(images),limits,'')
        value=replace(value,seal=_seal(value))
        checked_native_figures(value)
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError) as error:
        raise PdfProcessError('NATIVE_FIGURE_PROTOCOL_ERROR',pid=pid) from error
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED',pid=pid)
    return value


def find_native_figure(value, *, page, bbox, tolerance=.01):
    """Unique near-identical source placement only; no inferred figure ownership."""
    catalog=checked_native_figures(value)
    if (type(page) is not int or type(bbox) not in (tuple,list) or len(bbox)!=4
            or any(type(v) not in (float,int) or not math.isfinite(v) for v in bbox)
            or type(tolerance) not in (float,int) or not 0 <= tolerance <= .05):
        raise PdfNativeFigureError('Invalid native placement query')
    matches=[i for p in catalog['pages'] if p['page']==page for i in p['images']
             if i['status']=='native' and all(abs(a-b)<=tolerance for a,b in zip(i['page_bbox'],bbox))]
    if len(matches)!=1:
        return None
    item=matches[0]
    image=next(i for i in value.images if i.source_id==item['source_id'])
    return item,image


def checked_native_figure_appearance(raw_pdf, page, native_metadata, png, *,
                                     limits=PdfNativeFigureLimits()):
    """Conservative *new-lane* image appearance proof; not chart-reading proof.

    Nothing in the existing native catalog/column-view lane calls this function.
    Only a singly placed, opaque RGB/Flate image on a simple, unclipped page is
    supported. The complete PDFium page text is returned, never a caption guess
    or hash-only substitute. Other figures still require their own processing.
    Unknowns preserve the full-page fallback. Invoke inside the existing bounded
    PDF process boundary in production: parser construction is not a sandbox.
    """
    version='pdf-native-opaque-appearance-check-v1'
    failure={'version':version,'eligibility':False,'requires_full_page_context':True,
             'semantic_accuracy_verified':False}
    def reject(reason):
        raise PdfNativeFigureError(reason)
    def finite(values):
        return all(type(v) in (int,float) and math.isfinite(v) for v in values)
    def overlap(a,b):
        return max(a[0],b[0]) < min(a[2],b[2]) and max(a[1],b[1]) < min(a[3],b[3])
    def inflate(data,maximum):
        import zlib
        if type(data) is not bytes or len(data)>limits.source_bytes:
            reject('compressed_stream_budget')
        decoder=zlib.decompressobj()
        result=decoder.decompress(data,maximum+1)
        if len(result)>maximum or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            reject('decoded_stream_budget_or_trailing_data')
        return result
    def stream_bytes(stream,maximum):
        # Do not call get_data() on an unbounded, compressed document stream.
        data=stream._data
        if stream.get('/Filter') == '/FlateDecode' and '/DecodeParms' not in stream:
            return inflate(data,maximum)
        if '/Filter' not in stream and '/DecodeParms' not in stream and len(data)<=maximum:
            return data
        reject('unsupported_stream_filter')
    def multiply(a,b):
        return [a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],
                a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],
                a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]]
    try:
        if (type(limits) is not PdfNativeFigureLimits or type(raw_pdf) is not bytes
                or not 0<len(raw_pdf)<=limits.source_bytes or type(png) is not bytes
                or not 0<len(png)<=min(limits.image_bytes,limits.total_bytes)
                or type(page) is not int or page<1 or type(native_metadata) is not dict):
            reject('invalid_input_or_budget')
        if len(_json(native_metadata).encode())>limits.manifest_bytes:
            reject('native_metadata_budget')
        required=('object_ordinal','page_bbox','matrix','width','height','pixel_sha256',
                  'png_sha256','byte_count','pixel_mode','status')
        if any(k not in native_metadata for k in required):
            reject('incomplete_native_metadata')
        ordinal=native_metadata['object_ordinal']
        size=(native_metadata['width'],native_metadata['height'])
        bbox=native_metadata['page_bbox']
        matrix=native_metadata['matrix']
        if (type(ordinal) is not int or not 1<=ordinal<=limits.objects
                or any(type(v) is not int or v<1 for v in size)
                or size[0]*size[1]>min(limits.image_pixels,limits.total_pixels)
                or type(bbox) not in (list,tuple) or len(bbox)!=4 or not finite(bbox)
                or type(matrix) not in (list,tuple) or len(matrix)!=6 or not finite(matrix)
                or native_metadata['status']!='native' or native_metadata['pixel_mode']!='RGB'
                or native_metadata['byte_count']!=len(png)
                or native_metadata['png_sha256']!=sha256(png).hexdigest()):
            reject('native_metadata_or_png_identity')
        from PIL import Image
        import pypdf
        from pypdf import PdfReader
        from pypdf.generic import ContentStream, DecodedStreamObject
        import pypdfium2 as pdfium
        import pypdfium2.raw as api
        if (pypdf.__version__!='6.12.2' or str(pdfium.PYPDFIUM_INFO)!='5.13.0'
                or str(pdfium.PDFIUM_INFO)!='153.0.7999.0'):
            reject('unsupported_parser_version')
        with Image.open(BytesIO(png)) as image:
            if (image.format!='PNG' or image.mode!='RGB' or image.size!=size
                    or getattr(image,'n_frames',1)!=1
                    or any(k in image.info for k in ('transparency','icc_profile','gamma','chromaticity','srgb'))):
                reject('unsupported_png_appearance')
            pixels=image.tobytes()
        pixel_sha=sha256(pixels).hexdigest()
        if pixel_sha!=native_metadata['pixel_sha256']:
            reject('native_pixel_identity')
        reader=PdfReader(BytesIO(raw_pdf),strict=True)
        if reader.is_encrypted or not 0<len(reader.pages)<=limits.pages or page>len(reader.pages):
            reject('encrypted_or_invalid_page')
        root=reader.trailer['/Root']
        if any(k in root for k in ('/OutputIntents','/OCProperties','/NeedsRendering','/OpenAction','/AA')):
            reject('unsupported_catalog_appearance')
        pdf_page=reader.pages[page-1]
        media=[float(v) for v in pdf_page.mediabox]
        crop=[float(v) for v in pdf_page.cropbox]
        if (not finite(media+crop) or media[:2]!=[0,0] or crop!=media
                or media[2]<=0 or media[3]<=0 or pdf_page.get('/Rotate',0)!=0
                or pdf_page.get('/UserUnit',1)!=1
                or any(k in pdf_page for k in ('/Group','/Annots','/AA','/PresSteps'))):
            reject('unsupported_page_appearance')
        resources=pdf_page['/Resources']
        if any(resources.get(k) for k in ('/ExtGState','/ColorSpace','/Properties')):
            reject('unsupported_page_resources')
        content=pdf_page['/Contents']
        streams=list(content) if isinstance(content,list) else [content]
        decoded=[]
        remaining=limits.manifest_bytes
        for stream in streams:
            part=stream_bytes(stream.get_object(),remaining)
            remaining-=len(part)+1
            if remaining<0:
                reject('page_content_budget')
            decoded.append(part)
        plain=DecodedStreamObject()
        plain.set_data(b'\n'.join(decoded))
        operations=ContentStream(plain,reader).operations
        if len(operations)>limits.objects:
            reject('page_operation_budget')
        supported=set(b'q Q cm Do g G rg RG k K BT ET Tf Td TD Tm Tj TJ T* Tc Tw Tz TL Ts m l c v y h re S s f F f* B B* b b* n w J j M d'.split())
        placements=[]
        state=[1,0,0,1,0,0]
        stack=[]
        for args,operator in operations:
            if operator not in supported:
                reject('unsupported_content_operator')
            if operator==b'q':
                stack.append(state[:])
            elif operator==b'Q':
                if not stack:
                    reject('unbalanced_graphics_state')
                state=stack.pop()
            elif operator==b'cm':
                values=[float(v) for v in args]
                if len(values)!=6 or not finite(values):
                    reject('invalid_content_transform')
                state=multiply(state,values)
            elif operator==b'Do':
                if len(args)!=1:
                    reject('invalid_image_placement')
                resource=resources['/XObject'][args[0]].get_object()
                if resource.get('/Subtype')!='/Image':
                    reject('unsupported_form_or_xobject')
                placements.append((str(args[0]),resource,state[:]))
        if stack:
            reject('unbalanced_graphics_state')
        target=[v for v in placements if all(abs(a-b)<=.001 for a,b in zip(v[2],matrix))]
        if len(target)!=1:
            reject('ambiguous_native_placement')
        name,resource,transform=target[0]
        if sum(p[0]==name for p in placements)!=1:
            reject('repeated_native_resource')
        allowed={'/Type','/Subtype','/Width','/Height','/BitsPerComponent','/ColorSpace','/Filter','/Length'}
        if (set(resource)-allowed or resource.get('/Type')!='/XObject'
                or resource.get('/Subtype')!='/Image' or resource.get('/ColorSpace')!='/DeviceRGB'
                or resource.get('/BitsPerComponent')!=8
                or (resource.get('/Width'),resource.get('/Height'))!=size):
            reject('masked_or_unsupported_native_resource')
        if stream_bytes(resource,len(pixels))!=pixels:
            reject('pdf_png_pixels_differ')
        if (not finite(transform) or transform[0]<=0 or transform[3]<=0
                or abs(transform[1])>1e-9 or abs(transform[2])>1e-9):
            reject('non_axis_aligned_native_placement')
        actual=[transform[4],media[3]-transform[5]-transform[3],
                transform[4]+transform[0],media[3]-transform[5]]
        if (not 0<=actual[0]<actual[2]<=media[2] or not 0<=actual[1]<actual[3]<=media[3]
                or any(abs(a-b)>.001 for a,b in zip(actual,bbox))):
            reject('incomplete_or_changed_native_placement')
        with closing(pdfium.PdfDocument(raw_pdf)) as document:
            if api.FPDF_GetSecurityHandlerRevision(document.raw)!=-1 or len(document)!=len(reader.pages):
                reject('parser_document_disagreement')
            with closing(document[page-1]) as rendered_page:
                if (rendered_page.get_rotation()!=0
                        or any(abs(a-b)>.001 for a,b in zip(rendered_page.get_bbox(),media))):
                    reject('parser_page_disagreement')
                objects=[]
                for obj in rendered_page.get_objects(max_depth=1):
                    objects.append(obj)
                    if len(objects)>limits.objects:
                        reject('page_object_budget')
                if ordinal>len(objects) or sum(o.type==api.FPDF_PAGEOBJ_IMAGE for o in objects)!=len(placements):
                    reject('parser_object_disagreement')
                pdf_bbox=[actual[0],media[3]-actual[3],actual[2],media[3]-actual[1]]
                for number,obj in enumerate(objects,1):
                    bounds=list(obj.get_bounds())
                    if not finite(bounds) or bounds[0]>bounds[2] or bounds[1]>bounds[3]:
                        reject('unknown_object_bounds')
                    if obj.type not in (api.FPDF_PAGEOBJ_TEXT,api.FPDF_PAGEOBJ_PATH,api.FPDF_PAGEOBJ_IMAGE):
                        reject('unsupported_page_object')
                    if number==ordinal:
                        m=obj.get_matrix()
                        observed=[getattr(m,k) for k in ('a','b','c','d','e','f')]
                        if (obj.type!=api.FPDF_PAGEOBJ_IMAGE or obj.get_px_size()!=size
                                or not finite(observed)
                                or any(abs(a-b)>.001 for a,b in zip(bounds,pdf_bbox))
                                or any(abs(a-b)>.001 for a,b in zip(observed,transform))):
                            reject('parser_native_placement_disagreement')
                    elif overlap(bounds,pdf_bbox):
                        reject('intersecting_page_object')
                with closing(rendered_page.get_textpage()) as text_page:
                    count=text_page.count_chars()
                    if not 0<=count<=limits.objects:
                        reject('page_text_budget')
                    for index in range(count):
                        bounds=list(text_page.get_charbox(index))
                        if not finite(bounds) or bounds[0]>bounds[2] or bounds[1]>bounds[3]:
                            reject('unknown_glyph_bounds')
                        if overlap(bounds,pdf_bbox):
                            reject('intersecting_page_glyph')
                    context=text_page.get_text_range()
                    if type(context) is not str or '\ufffd' in context or len(context.encode())>limits.manifest_bytes:
                        reject('unknown_or_oversized_page_text')
        result={'version':version,'eligibility':True,'requires_full_page_context':False,
                'semantic_accuracy_verified':False,'raw_pdf_sha256':sha256(raw_pdf).hexdigest(),
                'png_sha256':sha256(png).hexdigest(),'pixel_sha256':pixel_sha,
                'page':page,'object_ordinal':ordinal,'resource_name':name,'page_bbox':actual,
                'matrix':transform,'dimensions':list(size),'source_context':context,
                'source_context_sha256':sha256(context.encode()).hexdigest(),
                'page_objects':len(objects),'page_characters':count,
                'other_page_images':len(placements)-1,'complete_page_text_retained':True,
                'parser_versions':{'pypdf':'6.12.2','pypdfium2':'5.13.0','pdfium':'153.0.7999.0'}}
        if len(_json(result).encode())>limits.manifest_bytes:
            reject('appearance_result_budget')
        return result
    except PdfNativeFigureError as error:
        return {**failure,'reason':str(error)}
    except Exception:
        # Invalid/unsupported parsers, streams or glyph APIs never authorize a
        # narrower context. The original retained page remains the fallback.
        return {**failure,'reason':'unsupported_or_invalid_source'}


def _column_partition(item):
    """Require a complete rectangular grid before treating its columns as views."""
    frames=[p['axes_pixel_bbox'] for p in item.get('panels',[])]
    if len(frames)<2:
        return ()
    rows=[]
    for box in sorted(frames,key=lambda b:(b[1],b[0])):
        if (type(box) is not list or len(box)!=4 or any(type(v) is not int for v in box)
                or not 0<=box[0]<box[2]<=item['width'] or not 0<=box[1]<box[3]<=item['height']):
            raise PdfNativeFigureError('Invalid sealed panel geometry')
        matches=[r for r in rows if abs(r[0][1]-box[1])<=5 and abs(r[0][3]-box[3])<=5]
        if len(matches)>1:
            return ()
        if matches:
            matches[0].append(box)
        else:
            rows.append([box])
    rows=[sorted(row,key=lambda b:b[0]) for row in rows]
    count=len(rows[0])
    if count<2 or any(len(row)!=count for row in rows):
        return ()
    first=rows[0]
    if (any(a[2]>=b[0] for row in rows for a,b in zip(row,row[1:]))
            or any(a[3]>=b[1] for a,b in zip([r[0] for r in rows],[r[0] for r in rows][1:]))
            or any(abs(box[0]-reference[0])>5 or abs(box[2]-reference[2])>5
                   for row in rows for box,reference in zip(row,first))):
        return ()
    boundaries=[0]+[(a[2]+b[0])//2 for a,b in zip(first,first[1:])]+[item['width']]
    return tuple((boundaries[n],0,boundaries[n+1],item['height']) for n in range(count))


def native_figure_views(value, *, page, bbox):
    """Source-owned column details and supplementary, unscaled pixel context.

    Returned tuples are (metadata, target ModelImageInput, context ModelImageInput).
    The targets partition ALL native image pixels once. Context deliberately
    repeats checked-page overview + spatial lower-left reference panel, not a
    claimed legend. It is not assigned new prose or figure ownership.
    No valid multi-column rectangular grid means (), never a guessed split.
    """
    matched=find_native_figure(value,page=page,bbox=bbox)
    if matched is None:
        return ()
    item,original=matched
    columns=_column_partition(item)
    if not columns:
        return ()
    from PIL import Image, ImageDraw, ImageFont
    document=checked_visual_source(value.source)
    geometry=document.pages[page-1]
    rendered=value.source.pages[page-1]
    overview_box=(max(0,math.floor(bbox[0]*rendered.width/geometry.width)),
                  max(0,math.floor(bbox[1]*rendered.height/geometry.height)),
                  min(rendered.width,math.ceil(bbox[2]*rendered.width/geometry.width)),
                  min(rendered.height,math.ceil(bbox[3]*rendered.height/geometry.height)))
    if overview_box[0]>=overview_box[2] or overview_box[1]>=overview_box[3]:
        raise PdfNativeFigureError('Empty checked page overview')
    # The spatial reference is source-derived; it is NOT a detected legend.
    panels=item['panels']
    reference=min(panels,key=lambda p:(-p['axes_pixel_bbox'][1],p['axes_pixel_bbox'][0]))
    reference_box=tuple(reference['crop_pixel_bbox'])
    def png(image):
        out=BytesIO()
        image.save(out,format='PNG',compress_level=6,optimize=False)
        return out.getvalue()
    def image_identity(data,box,kind):
        return 'PNV_'+sha256(_json([VERSION,original.source_id,box,kind,sha256(data).hexdigest()]).encode()).hexdigest()
    targets=[]
    with Image.open(BytesIO(original.data)) as source_pixels:
        if source_pixels.size!=(item['width'],item['height']):
            raise PdfNativeFigureError('Sealed native dimensions changed')
        with Image.open(BytesIO(rendered.image.data)) as full_page:
            overview=full_page.crop(overview_box).convert('RGB')
        native_reference=source_pixels.crop(reference_box).convert('RGB')
        try:
            context_width=max(overview.width,native_reference.width)
            context_height=overview.height+native_reference.height+88
            if context_width*context_height>value.limits.image_pixels:
                raise PdfNativeFigureError('Complete native context pixels exceed budget')
            with Image.new('RGB',(context_width,context_height),'white') as sheet:
                draw=ImageDraw.Draw(sheet)
                font=ImageFont.load_default(size=24)
                draw.text((0,0),'Checked page overview (context only)',fill='black',font=font)
                sheet.paste(overview,(0,36))
                reference_y=overview.height+52
                draw.text((0,reference_y),'Native reference panel (context only)',fill='black',font=font)
                sheet.paste(native_reference,(0,reference_y+36))
                context_data=png(sheet)
            context_id=image_identity(context_data,[list(overview_box),list(reference_box)],'context')
            context_image=ModelImageInput(context_id,'image/png',context_data)
            context_metadata={'source_id':context_id,'context_only':True,'legend_ownership_proven':False,
                'checked_page_image_sha256':sha256(rendered.image.data).hexdigest(),
                'page_overview_pixel_bbox':list(overview_box),
                'native_reference_pixel_bbox':list(reference_box),
                'overview_contact_sheet_bbox':[0,36,overview.width,36+overview.height],
                'reference_contact_sheet_bbox':[0,reference_y+36,native_reference.width,
                                                reference_y+36+native_reference.height],
                'overview_pixel_sha256':sha256(overview.tobytes()).hexdigest(),
                'reference_pixel_sha256':sha256(native_reference.tobytes()).hexdigest(),
                'width':context_width,'height':context_height,'encoded_sha256':sha256(context_data).hexdigest()}
        finally:
            overview.close()
            native_reference.close()
        for ordinal,box in enumerate(columns,1):
            with source_pixels.crop(box) as pixels:
                data=png(pixels)
                view_id=image_identity(data,list(box),'column')
                metadata={'view_id':view_id,'source_object_id':original.source_id,
                    'source_object_ordinal':item['object_ordinal'],'page':page,'page_bbox':list(bbox),
                    'pixel_bbox':list(box),'ordinal':ordinal,'count':len(columns),
                    'width':pixels.width,'height':pixels.height,
                    'pixel_sha256':sha256(pixels.tobytes()).hexdigest(),
                    'encoded_sha256':sha256(data).hexdigest(),'axes_calibrated':False,
                    'semantic_ownership_proven':False,'appearance_relation':item['appearance_relation'],
                    'context':context_metadata}
                targets.append((metadata,ModelImageInput(view_id,'image/png',data),context_image))
    # Count the shared supplementary context once in complete-work reservations.
    validate_image_inputs(tuple(t[1] for t in targets)+(context_image,),
        ModelImagePolicy(value.limits.objects,value.limits.image_bytes,value.limits.total_bytes,
                         value.limits.image_pixels,value.limits.total_pixels))
    return tuple(targets)
