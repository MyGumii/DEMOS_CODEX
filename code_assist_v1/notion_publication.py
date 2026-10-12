"""Stable publication boundary between the Notion workspace and Second Brain.

The Notion workspace remains the source of truth for pages.  This module captures
one immutable, content-addressed page snapshot and owns the legacy publication
delegate used by both web applications.  It deliberately does not import the
Knowledge Core or choose a future knowledge target; those concerns live behind a
separate compatibility adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Mapping, Protocol


SNAPSHOT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PageSourceSnapshotConflict(RuntimeError):
    """The approved preview no longer identifies the current page snapshot."""

    def __init__(self, expected_sha256: str, snapshot: "PageSourceSnapshot") -> None:
        super().__init__("page changed after the Second Brain publish preview")
        self.expected_sha256 = expected_sha256
        self.snapshot = snapshot


class NotionSourceStore(Protocol):
    def export_page_markdown(
        self,
        user_id: str | None,
        page_id: str,
        *,
        include_assets: bool = False,
    ) -> dict[str, Any]: ...

    def get_file(self, user_id: str | None, file_id: str) -> dict[str, Any]: ...


class LegacySecondBrainTarget(Protocol):
    def import_source_markdown(self, **kwargs: Any) -> dict[str, Any]: ...


WriteLock = Callable[..., ContextManager[Any]]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(str(value).encode("utf-8"))


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    """Recursively freeze JSON-like importer payloads, retaining immutable bytes."""
    if isinstance(value, Mapping):
        return (
            "mapping",
            tuple((str(key), _freeze(item)) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))),
        )
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze(item) for item in value))
    if isinstance(value, bytearray):
        return bytes(value)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    return str(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "mapping":
        return {key: _thaw(item) for key, item in value[1]}
    if isinstance(value, tuple) and len(value) == 2 and value[0] in {"list", "tuple"}:
        return [_thaw(item) for item in value[1]]
    return value


@dataclass(frozen=True)
class PageAssetSnapshot:
    """Hash-bearing description of one file or image reference in a page export."""

    role: str
    logical_name: str
    media_type: str
    size_bytes: int
    content_sha256: str | None
    available: bool
    file_id: str | None = None

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "content_sha256": self.content_sha256,
            "file_id": self.file_id,
            "logical_name": self.logical_name,
            "media_type": self.media_type,
            "role": self.role,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class PageSourceSnapshot:
    """Immutable snapshot captured after page access has been authorized."""

    actor_user_id: str
    page_id: str
    owner_user_id: str
    title: str
    revision: int | None
    updated_at: int | None
    can_edit: bool
    markdown: str
    content_sha256: str
    asset_manifest_sha256: str
    source_snapshot_sha256: str
    assets: tuple[PageAssetSnapshot, ...]
    page_payload_json: str
    frozen_images: tuple[Any, ...]
    captured_at: int
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    def page_payload(self) -> dict[str, Any]:
        value = json.loads(self.page_payload_json)
        return value if isinstance(value, dict) else {}

    def legacy_images(self) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        for frozen in self.frozen_images:
            item = _thaw(frozen)
            if isinstance(item, dict):
                images.append(item)
        return images

    def legacy_source_metadata(self) -> dict[str, Any]:
        """Keep the existing Second Brain metadata contract byte-for-byte stable."""
        return {
            "page_id": self.page_id,
            "owner_user_id": self.owner_user_id,
            "title": self.title,
        }

    def descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "page_id": self.page_id,
            "owner_user_id": self.owner_user_id,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "content_sha256": self.content_sha256,
            "asset_manifest_sha256": self.asset_manifest_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "asset_count": len(self.assets),
        }


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_asset(
    actor_user_id: str,
    file_item: Mapping[str, Any],
    source_store: NotionSourceStore,
) -> PageAssetSnapshot:
    file_id = str(file_item.get("id") or "").strip() or None
    logical_name = str(file_item.get("original_name") or file_item.get("stored_name") or file_id or "file")
    media_type = str(file_item.get("mime_type") or "application/octet-stream")
    declared_size = _coerce_optional_int(file_item.get("size")) or 0
    path = ""
    if file_id:
        try:
            resolved = source_store.get_file(actor_user_id, file_id)
            path = str(resolved.get("path") or "") if isinstance(resolved, Mapping) else ""
        except (FileNotFoundError, PermissionError, ValueError):
            path = ""
    if path and os.path.isfile(path):
        return PageAssetSnapshot(
            role="file",
            file_id=file_id,
            logical_name=logical_name,
            media_type=media_type,
            size_bytes=os.path.getsize(path),
            content_sha256=_sha256_file(path),
            available=True,
        )
    return PageAssetSnapshot(
        role="file",
        file_id=file_id,
        logical_name=logical_name,
        media_type=media_type,
        size_bytes=declared_size,
        content_sha256=None,
        available=False,
    )


def _image_asset(image: Mapping[str, Any]) -> PageAssetSnapshot:
    logical_name = str(image.get("filename") or image.get("label") or "image")
    media_type = str(image.get("mime_type") or "image/*")
    raw = image.get("raw")
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        return PageAssetSnapshot(
            role="image_reference",
            logical_name=logical_name,
            media_type=media_type,
            size_bytes=len(raw),
            content_sha256=_sha256_bytes(raw),
            available=True,
        )
    path = str(image.get("path") or "")
    if path and os.path.isfile(path):
        return PageAssetSnapshot(
            role="image_reference",
            logical_name=logical_name,
            media_type=media_type,
            size_bytes=os.path.getsize(path),
            content_sha256=_sha256_file(path),
            available=True,
        )
    return PageAssetSnapshot(
        role="image_reference",
        logical_name=logical_name,
        media_type=media_type,
        size_bytes=0,
        content_sha256=None,
        available=False,
    )


def capture_page_source_snapshot(
    actor_user_id: str,
    page_id: str,
    *,
    source_store: NotionSourceStore,
    captured_at: int | None = None,
) -> PageSourceSnapshot:
    """Authorize/export a page and capture deterministic content/asset identities."""
    actor = str(actor_user_id or "").strip()
    source_page_id = str(page_id or "").strip()
    if not actor:
        raise ValueError("authenticated user required")
    if not source_page_id:
        raise ValueError("page_id required")

    exported = source_store.export_page_markdown(actor, source_page_id, include_assets=True)
    page = exported.get("page") if isinstance(exported.get("page"), Mapping) else {}
    resolved_page_id = str(page.get("id") or source_page_id)
    owner_user_id = str(page.get("owner_user_id") or "")
    title = str(page.get("title") or "Untitled")
    markdown = str(exported.get("markdown") or "")
    files = [item for item in (exported.get("files") or []) if isinstance(item, Mapping)]
    images = [item for item in (exported.get("images") or []) if isinstance(item, Mapping)]

    assets = [_file_asset(actor, item, source_store) for item in files]
    assets.extend(_image_asset(item) for item in images)
    assets.sort(
        key=lambda item: (
            item.role,
            item.file_id or "",
            item.logical_name,
            item.content_sha256 or "",
        )
    )
    manifest = [item.manifest_entry() for item in assets]
    content_sha256 = _sha256_text(markdown)
    asset_manifest_sha256 = _sha256_text(_canonical_json(manifest))
    revision = _coerce_optional_int(page.get("revision"))
    updated_at = _coerce_optional_int(page.get("updated_at"))
    source_identity = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "page_id": resolved_page_id,
        "owner_user_id": owner_user_id,
        "revision": revision,
        "updated_at": updated_at,
        "content_sha256": content_sha256,
        "asset_manifest_sha256": asset_manifest_sha256,
    }
    return PageSourceSnapshot(
        actor_user_id=actor,
        page_id=resolved_page_id,
        owner_user_id=owner_user_id,
        title=title,
        revision=revision,
        updated_at=updated_at,
        can_edit=bool(page.get("can_edit")),
        markdown=markdown,
        content_sha256=content_sha256,
        asset_manifest_sha256=asset_manifest_sha256,
        source_snapshot_sha256=_sha256_text(_canonical_json(source_identity)),
        assets=tuple(assets),
        page_payload_json=_canonical_json(dict(page)),
        frozen_images=tuple(_freeze(dict(item)) for item in images),
        captured_at=int(time.time() if captured_at is None else captured_at),
    )


def publish_snapshot_to_legacy_second_brain(
    snapshot: PageSourceSnapshot,
    *,
    target_store: LegacySecondBrainTarget,
    write_lock: WriteLock,
) -> dict[str, Any]:
    """Delegate through the preserved legacy Second Brain publication contract."""
    if not snapshot.can_edit:
        raise PermissionError("edit permission required to publish")
    filename = f"notion-{snapshot.title}-{snapshot.page_id}.md"
    with write_lock("second_brain", snapshot.actor_user_id):
        return target_store.import_source_markdown(
            filename=filename,
            markdown=snapshot.markdown,
            user_id=snapshot.actor_user_id,
            images=snapshot.legacy_images(),
            source_type="notion",
            source_metadata=snapshot.legacy_source_metadata(),
        )


def publish_page_to_legacy_second_brain(
    actor_user_id: str,
    page_id: str,
    *,
    source_store: NotionSourceStore,
    target_store: LegacySecondBrainTarget,
    write_lock: WriteLock,
    expected_source_snapshot_sha256: str = "",
) -> tuple[PageSourceSnapshot, dict[str, Any]]:
    expected = str(expected_source_snapshot_sha256 or "").strip()
    if expected and not _SHA256_RE.fullmatch(expected):
        raise ValueError(
            "expected_source_snapshot_sha256 must be a lowercase SHA-256 digest"
        )
    snapshot = capture_page_source_snapshot(
        actor_user_id,
        page_id,
        source_store=source_store,
    )
    if expected and expected != snapshot.source_snapshot_sha256:
        raise PageSourceSnapshotConflict(expected, snapshot)
    result = publish_snapshot_to_legacy_second_brain(
        snapshot,
        target_store=target_store,
        write_lock=write_lock,
    )
    return snapshot, result
