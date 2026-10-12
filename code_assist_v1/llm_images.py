"""Opt-in image transport. This is neither source authorization nor vision QA.

Callers retain their source lease and supply a fresh invocation guard. No URLs,
caller file paths, implicit conversions, OCR, or model calls are performed here.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import warnings

from code_assist_v1.llm_response import ModelResponseError


IMAGE_INPUT_CAPABILITY = "image-input-v1"
_FORMATS = {"image/png": ("PNG", ".png"), "image/jpeg": ("JPEG", ".jpg"),
            "image/webp": ("WEBP", ".webp")}


@dataclass(frozen=True, slots=True)
class ModelImageInput:
    source_id: str
    media_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ModelImagePolicy:
    maximum_images: int
    maximum_image_bytes: int
    maximum_total_bytes: int
    maximum_image_pixels: int
    maximum_total_pixels: int


def _invalid():
    raise ModelResponseError("MODEL_IMAGE_INVALID", "Invalid or over-budget image input.") from None


def validate_image_inputs(images, policy) -> tuple[dict, ...]:
    if (type(images) is not tuple or not images or type(policy) is not ModelImagePolicy
            or any(type(v) is not int or v <= 0 for v in asdict(policy).values())
            or len(images) > policy.maximum_images):
        _invalid()
    seen, total, pixels, metadata = set(), 0, 0, []
    for ordinal, item in enumerate(images, 1):
        if (type(item) is not ModelImageInput or type(item.data) is not bytes or not item.data
                or type(item.source_id) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", item.source_id)
                or item.source_id in seen or type(item.media_type) is not str
                or item.media_type not in _FORMATS or len(item.data) > policy.maximum_image_bytes):
            _invalid()
        seen.add(item.source_id)
        total += len(item.data)
        if total > policy.maximum_total_bytes:
            _invalid()
        try:
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(item.data)) as image:
                    width, height = image.size
                    pixels += width * height
                    if (image.format != _FORMATS[item.media_type][0] or getattr(image, "n_frames", 1) != 1
                            or width <= 0 or height <= 0 or width * height > policy.maximum_image_pixels
                            or pixels > policy.maximum_total_pixels):
                        _invalid()
                    image.verify()
        except Exception:
            _invalid()
        metadata.append({"ordinal": ordinal, "source_id": item.source_id,
            "media_type": item.media_type, "raw_bytes": len(item.data),
            "sha256": sha256(item.data).hexdigest(), "width": width, "height": height})
    return tuple(metadata)


def prepare_image_messages(messages, images, *, inline: bool):
    # The new lane accepts text plus typed bytes only; never import remote or
    # previously omitted image parts through an unrelated message field.
    if (not isinstance(messages, list) or not messages
            or any(type(m) is not dict or set(m) != {"role", "content"}
                or m["role"] not in {"system", "user", "assistant"}
                or type(m["content"]) is not str for m in messages)):
        _invalid()
    copied = [dict(message) for message in messages]
    parts, labels = [], []
    for ordinal, item in enumerate(images, 1):
        label = f"Image {ordinal}: source_id={item.source_id}; sha256={sha256(item.data).hexdigest()}"
        labels.append(label)
        if inline:
            parts.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {
                "url": f"data:{item.media_type};base64,{base64.b64encode(item.data).decode('ascii')}"}}])
    copied.append({"role": "user", "content": parts if inline else "\n".join(labels)})
    return copied


def image_input_receipt(images, policy, *, transport):
    if transport not in {"codex-cli-image-files-v1", "chat-completions-data-uri-v1"}:
        _invalid()
    rows = validate_image_inputs(images, policy)
    return {"schema_version": 1, "format": IMAGE_INPUT_CAPABILITY, "transport": transport,
        "image_count": len(rows), "raw_bytes": sum(row["raw_bytes"] for row in rows),
        "pixels": sum(row["width"] * row["height"] for row in rows),
        "images": list(rows), "policy": asdict(policy), "semantic_accuracy_verified": False}


def bind_image_receipt(receipt, *, request_sha256, provider, model_id, text):
    """Local invocation evidence, not a signature or a provider attestation."""
    binding = {"request_sha256": request_sha256, "provider": provider, "model_id": model_id,
               "response_text_sha256": sha256(text.encode("utf-8")).hexdigest()}
    value = {**receipt, "binding": binding}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**value, "invocation_sha256": sha256(encoded).hexdigest()}


@contextmanager
def staged_image_files(images, policy, guard, *, attempt=None):
    """Owned per-call files; deletion is not a forensic secure-erase guarantee."""
    validate_image_inputs(images, policy)
    guard()
    owned = None
    try:
        try:
            owned = tempfile.TemporaryDirectory(prefix="sb-model-images-")
            directory = owned.name
            paths = []
            for ordinal, item in enumerate(images, 1):
                guard()
                path = Path(directory) / f"image-{ordinal:04d}{_FORMATS[item.media_type][1]}"
                with path.open("xb") as stream:
                    if stream.write(item.data) != len(item.data):
                        raise OSError("incomplete image write")
                if path.read_bytes() != item.data:
                    raise OSError("image staging mismatch")
                paths.append(str(path))
                guard()
        except OSError:
            raise ModelResponseError("MODEL_IMAGE_STAGING_FAILED", "Image staging did not complete.") from None
        yield tuple(paths)
    finally:
        try:
            if owned is not None:
                try:
                    owned.cleanup()
                except OSError:
                    raise ModelResponseError("MODEL_IMAGE_CLEANUP_FAILED", "Image cleanup did not complete.") from None
                if attempt is not None:
                    attempt.update(owned_temp_removed=True)
        finally:
            guard.raise_if_failed()
    # No success receipt if cleanup failed. A failure inside the context still
    # records successful cleanup before the original error is propagated.
    guard()
