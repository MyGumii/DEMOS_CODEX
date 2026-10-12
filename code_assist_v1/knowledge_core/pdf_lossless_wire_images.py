"""Opt-in PNG wire derivation; original source capabilities remain untouched.

Only static, metadata-free RGB PNGs are accepted. Re-encoding never converts,
resizes, crops, repairs or replaces a source. The complete original-to-wire
relation is local evidence, not semantic accuracy or publication authority.
No source I/O, global cache, worker, OCR or model call is performed here.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
import warnings

import PIL
from PIL import Image

from ..document_pdf_process import PdfProcessError
from ..llm_images import ModelImageInput, ModelImagePolicy, validate_image_inputs
from ..llm_response import ModelResponseError


VERSION = 'pdf-lossless-png-wire-v1'
_POLICY_CAPS = dict(maximum_images=1000, maximum_image_bytes=16_777_216,
    maximum_total_bytes=67_108_864, maximum_image_pixels=16_000_000,
    maximum_total_pixels=256_000_000)


class PdfLosslessWireImageError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise PdfLosslessWireImageError(message)


def _check(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED')


class _CandidateLarger(Exception):
    pass


class _BoundedBuffer(BytesIO):
    """Stop an unhelpful candidate before allocating more than source bytes."""
    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum

    def write(self, data):
        if self.tell()+len(data) > self.maximum:
            raise _CandidateLarger()
        return super().write(data)


@dataclass(frozen=True, slots=True)
class PdfLosslessWireImages:
    images: tuple[ModelImageInput, ...]
    metadata_json: str


def prepare_lossless_png_wire_images(images, *, image_policy, cancel_event=None):
    """Derive bounded wire PNGs and retain exact source/representation identities.

    Source IDs remain page ownership identifiers; PNG hashes distinguish the
    original and transmitted representations explicitly. Same-size or larger
    candidates keep the original typed image and bytes. The actual encoder
    version is recorded, rather than imposing a global Pillow version policy.
    """
    _check(cancel_event)
    _require(type(image_policy) is ModelImagePolicy
        and all(type(value) is int and 1 <= value <= _POLICY_CAPS[name]
            for name, value in asdict(image_policy).items()),
        'Bounded typed image policy required')
    _require(type(images) is tuple and bool(images), 'Complete typed source images required')
    try:
        original_rows = validate_image_inputs(images, image_policy)
        _check(cancel_event)
        wire_images, relations = [], []
        for original, identity in zip(images, original_rows, strict=True):
            _check(cancel_event)
            _require(original.media_type == 'image/png', 'Only original PNG wire derivation supported')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(original.data)) as source:
                    _require(source.format == 'PNG' and source.mode == 'RGB'
                        and getattr(source, 'n_frames', 1) == 1 and not source.info,
                        'Static metadata-free RGB PNG required')
                    source.load()
                    dimensions, mode, pixels = source.size, source.mode, source.tobytes()
                    _check(cancel_event)
                    candidate = None
                    with _BoundedBuffer(len(original.data)) as output:
                        try:
                            source.save(output, format='PNG', compress_level=9, optimize=False)
                            candidate = output.getvalue()
                        except _CandidateLarger:
                            pass
                    _check(cancel_event)
                    if candidate is not None and len(candidate) < len(original.data):
                        packed = ModelImageInput(original.source_id, 'image/png', candidate)
                        validate_image_inputs((packed,), image_policy)
                        with Image.open(BytesIO(candidate)) as restored:
                            _require(restored.format == 'PNG' and restored.mode == mode
                                and restored.size == dimensions and getattr(restored, 'n_frames', 1) == 1
                                and not restored.info, 'Packed PNG mode or dimensions changed')
                            restored.load()
                            _require(restored.tobytes() == pixels, 'Packed PNG pixels changed')
                        selected = packed
                    else:
                        selected = original
                    _check(cancel_event)
                    relations.append(dict(ordinal=identity['ordinal'], source_id=original.source_id,
                        media_type='image/png', original_png_sha256=identity['sha256'],
                        original_bytes=len(original.data), wire_png_sha256=sha256(selected.data).hexdigest(),
                        wire_bytes=len(selected.data), pixel_sha256=sha256(pixels).hexdigest(),
                        mode=mode, dimensions=list(dimensions), reencoded=selected is not original))
                    wire_images.append(selected)
        result = tuple(wire_images)
        validate_image_inputs(result, image_policy)
        _check(cancel_event)
        metadata = json.dumps(dict(version=VERSION, encoder=dict(name='Pillow', version=PIL.__version__,
            compress_level=9, optimize=False), policy=asdict(image_policy), images=relations,
            source_replaced=False, source_resized=False, source_clipped=False,
            semantic_accuracy_verified=False, publication_authority=False),
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        _check(cancel_event)
        return PdfLosslessWireImages(result, metadata)
    except PdfProcessError:
        raise
    except PdfLosslessWireImageError:
        raise
    except (ModelResponseError, OSError, ValueError, TypeError, UnicodeError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise PdfLosslessWireImageError('Complete bounded lossless PNG derivation required') from error
