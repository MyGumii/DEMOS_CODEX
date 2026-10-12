"""Deterministic full projection rebuild from canonical Knowledge Core state.

This module owns the P2.6 one-shot rebuild primitive.  It is deliberately not
the P7 outbox worker: no event is claimed or acknowledged here.  A rebuild
captures one read-consistent canonical snapshot, verifies every published
Markdown body, writes an immutable generation, atomically replaces only the
small per-space manifest, and finally advances ``projection_state`` in a short
transaction after revalidating the canonical snapshot.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Iterator
import uuid

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..legacy.blob_stager import FilesystemLegacyBlobStager
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..migrations.runner import verify_canonical_integrity_before_projection_rebuild


PROJECTION_NAME = "markdown"
PROJECTION_MANIFEST_SCHEMA_VERSION = 1

# ``log.md`` is the compatibility activity projection for canonical file-visible
# changes, not a second copy of every security/lease/conversation audit event.
# Every listed action is tied to a space-generation change (legacy imports are
# completed offline before projection traffic is enabled).  Keeping this set
# explicit is what makes one numeric space generation immutable even when
# unrelated audit rows are appended later.
_PROJECTED_AUDIT_ACTIONS = (
    "change_proposal.apply",
    "knowledge_item.deactivate",
    "knowledge_item.edit",
    "knowledge_item.merge",
    "knowledge_item.purge",
    "knowledge_item.restore",
    "legacy_migration.graph_edge",
    "legacy_migration.graph_node",
    "legacy_migration.personal_item",
    "publication.created",
    "semantic_edge.apply",
)


class ProjectionMaterializationError(KnowledgeCoreError):
    code = "KNOWLEDGE_CORE_PROJECTION_REBUILD_FAILED"


class ProjectionCanonicalDriftError(ProjectionMaterializationError):
    code = "KNOWLEDGE_CORE_PROJECTION_CANONICAL_DRIFT"


@dataclass(frozen=True, slots=True)
class ProjectionSpaceResult:
    space_id: str
    generation: int
    canonical_high_water: int
    entry_count: int
    entries_sha256: str
    manifest_sha256: str

    def report_row(self) -> dict[str, object]:
        return {
            "canonical_high_water": self.canonical_high_water,
            "entries_sha256": self.entries_sha256,
            "entry_count": self.entry_count,
            "generation": self.generation,
            "manifest_sha256": self.manifest_sha256,
            "space_id": self.space_id,
        }


@dataclass(frozen=True, slots=True)
class FullProjectionRebuildResult:
    projection_name: str
    spaces: tuple[ProjectionSpaceResult, ...]
    spaces_sha256: str
    entry_count: int

    @property
    def space_count(self) -> int:
        return len(self.spaces)


@dataclass(frozen=True, slots=True)
class _ItemRow:
    item_id: str
    revision_id: str
    title: str
    raw_content_sha256: str
    normalized_content_sha256: str
    normalizer_version: str
    content_size_bytes: int
    body_content_md: str | None
    body_blob_sha256: str | None
    body_size_bytes: int
    body_sha256: str
    blob_size_bytes: int | None
    blob_media_type: str | None
    blob_relative_path: str | None
    blob_verification_status: str | None
    item_updated_at: int
    revision_created_at: int

    def binding_row(self) -> dict[str, object]:
        inline_sha = None
        if self.body_content_md is not None:
            inline_sha = hashlib.sha256(
                self.body_content_md.encode("utf-8")
            ).hexdigest()
        return {
            "blob_media_type": self.blob_media_type,
            "blob_relative_path": self.blob_relative_path,
            "blob_size_bytes": self.blob_size_bytes,
            "blob_verification_status": self.blob_verification_status,
            "body_blob_sha256": self.body_blob_sha256,
            "body_inline_sha256": inline_sha,
            "body_sha256": self.body_sha256,
            "body_size_bytes": self.body_size_bytes,
            "content_size_bytes": self.content_size_bytes,
            "item_id": self.item_id,
            "item_updated_at": self.item_updated_at,
            "normalized_content_sha256": self.normalized_content_sha256,
            "normalizer_version": self.normalizer_version,
            "raw_content_sha256": self.raw_content_sha256,
            "revision_created_at": self.revision_created_at,
            "revision_id": self.revision_id,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class _SpaceSnapshot:
    space_id: str
    space_kind: str
    status: str
    generation: int
    space_updated_at: int
    items: tuple[_ItemRow, ...]
    edges: tuple[dict[str, object], ...]
    audit_rows: tuple[dict[str, object], ...]
    canonical_high_water: int
    created_at_ms: int
    binding_sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedGeneration:
    snapshot: _SpaceSnapshot
    files: tuple[tuple[str, str, bytes], ...]
    manifest: dict[str, object]
    manifest_bytes: bytes
    result: ProjectionSpaceResult


class _PinnedGeneration:
    """Hold verified generations against write/delete until publication."""

    __slots__ = ("_anchors", "_closed", "_handles", "_space_anchors")

    def __init__(
        self,
        handles: list[int],
        anchors: list[object],
        space_anchors: Mapping[str, object] | None = None,
    ) -> None:
        self._handles = handles
        self._anchors = anchors
        self._space_anchors = dict(space_anchors or {})
        self._closed = False

    def space_anchor(self, space_id: str) -> object:
        if self._closed:
            raise ProjectionMaterializationError(
                "A closed projection-generation lease cannot publish a manifest."
            )
        try:
            return self._space_anchors[space_id]
        except KeyError as exc:
            raise ProjectionMaterializationError(
                "The projection-generation lease does not cover the requested space."
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        from ...tools.legacy_inventory import _close_windows_handle

        first_error: Exception | None = None
        for handle in reversed(self._handles):
            try:
                _close_windows_handle(handle)
            except Exception as exc:  # pragma: no cover - OS cleanup failure
                first_error = first_error or exc
        for anchor in reversed(self._anchors):
            try:
                anchor.close()  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover - OS cleanup failure
                first_error = first_error or exc
        if first_error is not None:
            raise ProjectionMaterializationError(
                "A pinned projection generation could not be released."
            ) from first_error


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProjectionMaterializationError(
            "Projection data is not canonical JSON."
        ) from exc


def _sha256_canonical(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _row_dict(row: object) -> dict[str, object]:
    try:
        return {str(key): row[key] for key in row.keys()}  # type: ignore[attr-defined,index]
    except (AttributeError, KeyError, TypeError) as exc:
        raise ProjectionMaterializationError(
            "A canonical projection row is invalid."
        ) from exc


def _utc_from_ms(value: int) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProjectionMaterializationError(
            "A canonical projection timestamp is invalid."
        )
    try:
        return (
            datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise ProjectionMaterializationError(
            "A canonical projection timestamp is outside the supported range."
        ) from exc


def _normalize_markdown(content: bytes, normalizer_version: str) -> bytes:
    if normalizer_version != "markdown_lf_v1":
        raise ProjectionMaterializationError(
            "A published Markdown normalizer is unsupported."
        )
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _capture_rows(connection: ManagedConnection) -> tuple[_SpaceSnapshot, ...]:
    spaces = [
        _row_dict(row)
        for row in connection.execute(
            """SELECT id,space_kind,status,generation,updated_at,row_version
                 FROM knowledge_space ORDER BY id"""
        ).fetchall()
    ]
    item_rows = [
        _row_dict(row)
        for row in connection.execute(
            """SELECT i.space_id,i.id AS item_id,i.current_revision_id AS revision_id,
                      i.updated_at AS item_updated_at,r.title,
                      r.raw_content_sha256,r.normalized_content_sha256,
                      r.content_size_bytes,r.normalizer_version,
                      r.created_at AS revision_created_at,
                      b.content_md AS body_content_md,
                      b.content_blob_hash AS body_blob_sha256,
                      b.content_size_bytes AS body_size_bytes,
                      b.content_sha256 AS body_sha256,
                      blob.size_bytes AS blob_size_bytes,
                      blob.media_type AS blob_media_type,
                      blob.relative_path AS blob_relative_path,
                      blob.verification_status AS blob_verification_status
                 FROM knowledge_item AS i
                 JOIN knowledge_revision AS r ON r.id=i.current_revision_id
                 JOIN knowledge_revision_body AS b
                   ON b.revision_id=r.id AND b.item_id=i.id
                 LEFT JOIN blob_object AS blob ON blob.sha256=b.content_blob_hash
                WHERE i.lifecycle_status='active'
                  AND i.curation_status='published'
                ORDER BY i.space_id,i.id"""
        ).fetchall()
    ]
    edge_rows = [
        _row_dict(row)
        for row in connection.execute(
            """SELECT e.space_id,e.id AS edge_id,e.source_item_id,
                      e.target_item_id,e.edge_type,e.origin,
                      e.needs_revalidation,e.updated_at,e.row_version
                 FROM knowledge_edge AS e
                 JOIN knowledge_item AS source ON source.id=e.source_item_id
                 JOIN knowledge_item AS target ON target.id=e.target_item_id
                WHERE e.edge_status='active'
                  AND source.lifecycle_status='active'
                  AND source.curation_status='published'
                  AND target.lifecycle_status='active'
                  AND target.curation_status='published'
                ORDER BY e.space_id,e.id"""
        ).fetchall()
    ]
    audit_placeholders = ",".join("?" for _ in _PROJECTED_AUDIT_ACTIONS)
    audit_rows = [
        _row_dict(row)
        for row in connection.execute(
            f"""SELECT id,space_id,event_uuid,occurred_at,action,outcome,
                      actor_kind,item_id,base_revision_id,new_revision_id,
                      schema_version
                 FROM audit_event
                WHERE space_id IS NOT NULL
                  AND action IN ({audit_placeholders})
                ORDER BY space_id,id""",
            _PROJECTED_AUDIT_ACTIONS,
        ).fetchall()
    ]

    items_by_space: dict[str, list[_ItemRow]] = defaultdict(list)
    for row in item_rows:
        try:
            item = _ItemRow(
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                title=str(row["title"]),
                raw_content_sha256=str(row["raw_content_sha256"]),
                normalized_content_sha256=str(row["normalized_content_sha256"]),
                normalizer_version=str(row["normalizer_version"]),
                content_size_bytes=int(row["content_size_bytes"]),
                body_content_md=(
                    None
                    if row["body_content_md"] is None
                    else str(row["body_content_md"])
                ),
                body_blob_sha256=(
                    None
                    if row["body_blob_sha256"] is None
                    else str(row["body_blob_sha256"])
                ),
                body_size_bytes=int(row["body_size_bytes"]),
                body_sha256=str(row["body_sha256"]),
                blob_size_bytes=(
                    None
                    if row["blob_size_bytes"] is None
                    else int(row["blob_size_bytes"])
                ),
                blob_media_type=(
                    None
                    if row["blob_media_type"] is None
                    else str(row["blob_media_type"])
                ),
                blob_relative_path=(
                    None
                    if row["blob_relative_path"] is None
                    else str(row["blob_relative_path"])
                ),
                blob_verification_status=(
                    None
                    if row["blob_verification_status"] is None
                    else str(row["blob_verification_status"])
                ),
                item_updated_at=int(row["item_updated_at"]),
                revision_created_at=int(row["revision_created_at"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProjectionMaterializationError(
                "A published item projection row is invalid."
            ) from exc
        items_by_space[str(row["space_id"])].append(item)

    edges_by_space: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in edge_rows:
        edges_by_space[str(row["space_id"])].append(
            {
                "edge_id": str(row["edge_id"]),
                "edge_type": str(row["edge_type"]),
                "needs_revalidation": int(row["needs_revalidation"]),
                "origin": str(row["origin"]),
                "row_version": int(row["row_version"]),
                "source_item_id": str(row["source_item_id"]),
                "target_item_id": str(row["target_item_id"]),
                "updated_at": int(row["updated_at"]),
            }
        )

    audits_by_space: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in audit_rows:
        audits_by_space[str(row["space_id"])].append(
            {
                "action": str(row["action"]),
                "actor_kind": str(row["actor_kind"]),
                "base_revision_id": row["base_revision_id"],
                "event_uuid": str(row["event_uuid"]),
                "id": int(row["id"]),
                "item_id": row["item_id"],
                "new_revision_id": row["new_revision_id"],
                "occurred_at": int(row["occurred_at"]),
                "outcome": str(row["outcome"]),
                "schema_version": int(row["schema_version"]),
            }
        )

    result: list[_SpaceSnapshot] = []
    for row in spaces:
        try:
            space_id = str(row["id"])
            items = tuple(items_by_space.pop(space_id, ()))
            edges = tuple(edges_by_space.pop(space_id, ()))
            audits = tuple(audits_by_space.pop(space_id, ()))
            generation = int(row["generation"])
            space_updated_at = int(row["updated_at"])
            high_water = max((int(value["id"]) for value in audits), default=0)
            created_at = max(
                [space_updated_at]
                + [
                    max(value.item_updated_at, value.revision_created_at)
                    for value in items
                ]
                + [int(value["updated_at"]) for value in edges]
                + [int(value["occurred_at"]) for value in audits]
            )
            binding_payload = {
                "audit": list(audits),
                "canonical_high_water": high_water,
                "edges": list(edges),
                "generation": generation,
                "items": [value.binding_row() for value in items],
                "space": {
                    "id": space_id,
                    "row_version": int(row["row_version"]),
                    "space_kind": str(row["space_kind"]),
                    "status": str(row["status"]),
                    "updated_at": space_updated_at,
                },
            }
            result.append(
                _SpaceSnapshot(
                    space_id=space_id,
                    space_kind=str(row["space_kind"]),
                    status=str(row["status"]),
                    generation=generation,
                    space_updated_at=space_updated_at,
                    items=items,
                    edges=edges,
                    audit_rows=audits,
                    canonical_high_water=high_water,
                    created_at_ms=created_at,
                    binding_sha256=_sha256_canonical(binding_payload),
                )
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProjectionMaterializationError(
                "A canonical knowledge-space projection row is invalid."
            ) from exc
    if items_by_space or edges_by_space or audits_by_space:
        raise ProjectionMaterializationError(
            "Projection rows reference a missing knowledge space."
        )
    return tuple(result)


def _capture_database(factory: ConnectionFactory) -> tuple[_SpaceSnapshot, ...]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return _capture_rows(connection)
    finally:
        connection.close()


def _verify_database_integrity(factory: ConnectionFactory) -> None:
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity_before_projection_rebuild(
            connection, factory.migration_catalog
        )
    finally:
        connection.close()


def _snapshot_binding(snapshots: Sequence[_SpaceSnapshot]) -> str:
    return _sha256_canonical(
        [[value.space_id, value.binding_sha256] for value in snapshots]
    )


def _matching_snapshots(
    current: Sequence[_SpaceSnapshot],
    expected: Sequence[_SpaceSnapshot],
    *,
    exact_space_set: bool,
) -> tuple[_SpaceSnapshot, ...]:
    current_by_id = {value.space_id: value for value in current}
    expected_ids = tuple(value.space_id for value in expected)
    if len(current_by_id) != len(current):
        raise ProjectionCanonicalDriftError(
            "Canonical state contains duplicate knowledge-space identities."
        )
    if exact_space_set and set(current_by_id) != set(expected_ids):
        raise ProjectionCanonicalDriftError(
            "The canonical knowledge-space set changed during projection rebuild."
        )
    try:
        return tuple(current_by_id[space_id] for space_id in expected_ids)
    except KeyError as exc:
        raise ProjectionCanonicalDriftError(
            "A projected knowledge space disappeared during rebuild."
        ) from exc


def _markdown_label(value: str) -> str:
    result: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character in "\r\n":
            result.append(" ")
        elif codepoint < 32 or codepoint == 127:
            result.append(f"\\u{codepoint:04x}")
        elif character in "\\[]":
            result.append("\\" + character)
        else:
            result.append(character)
    return "".join(result)


def _prepare_generation(
    snapshot: _SpaceSnapshot,
    *,
    stager: FilesystemLegacyBlobStager,
    blob_cache: dict[str, tuple[object, bytes]],
) -> _PreparedGeneration:
    files: list[tuple[str, str, bytes]] = []
    item_rows: list[dict[str, object]] = []
    for item in snapshot.items:
        if (item.body_content_md is None) == (item.body_blob_sha256 is None):
            raise ProjectionMaterializationError(
                "A published Markdown body has an invalid storage shape."
            )
        if item.body_content_md is not None:
            if "\x00" in item.body_content_md:
                raise ProjectionMaterializationError(
                    "A published Markdown body contains a forbidden NUL."
                )
            raw = item.body_content_md.encode("utf-8")
        else:
            if (
                item.body_blob_sha256 != item.body_sha256
                or item.blob_size_bytes is None
                or item.blob_media_type is None
                or item.blob_verification_status != "verified"
                or item.blob_size_bytes != item.body_size_bytes
            ):
                raise ProjectionMaterializationError(
                    "A published Markdown CAS binding is invalid."
                )
            key = str(item.body_blob_sha256)
            if key not in blob_cache:
                try:
                    blob_cache[key] = stager.verify_existing(
                        expected_sha256=key,
                        expected_size=item.body_size_bytes,
                        media_type=item.blob_media_type,
                        normalized_content_sha256=item.normalized_content_sha256,
                        normalizer_version=item.normalizer_version,
                    )
                except (KnowledgeCoreError, OSError) as exc:
                    raise ProjectionMaterializationError(
                        "A published Markdown CAS object failed verification."
                    ) from exc
            raw = blob_cache[key][1]
        if (
            len(raw) != item.body_size_bytes
            or item.content_size_bytes != item.body_size_bytes
            or hashlib.sha256(raw).hexdigest() != item.body_sha256
            or item.raw_content_sha256 != item.body_sha256
        ):
            raise ProjectionMaterializationError(
                "A published Markdown body differs from canonical metadata."
            )
        normalized = _normalize_markdown(raw, item.normalizer_version)
        if hashlib.sha256(normalized).hexdigest() != item.normalized_content_sha256:
            raise ProjectionMaterializationError(
                "A published Markdown normalized digest is invalid."
            )
        path = f"sources/{item.item_id}.md"
        files.append((path, "text/markdown", normalized))
        item_rows.append(
            {
                "item_id": item.item_id,
                "revision_id": item.revision_id,
                "title": item.title,
            }
        )

    index_lines = ["# Knowledge index", ""]
    for item in snapshot.items:
        index_lines.append(
            f"- [{_markdown_label(item.title)}](sources/{item.item_id}.md) "
            f"`{item.item_id}`"
        )
    index_bytes = ("\n".join(index_lines) + "\n").encode("utf-8")
    files.append(("index.md", "text/markdown", index_bytes))

    log_lines = ["# Activity log", ""]
    for row in snapshot.audit_rows:
        projected = {
            "action": row["action"],
            "actor_kind": row["actor_kind"],
            "base_revision_id": row["base_revision_id"],
            "event_uuid": row["event_uuid"],
            "item_id": row["item_id"],
            "new_revision_id": row["new_revision_id"],
            "occurred_at": _utc_from_ms(int(row["occurred_at"])),
            "outcome": row["outcome"],
        }
        log_lines.append("    " + _canonical_json_bytes(projected).decode("utf-8"))
    log_bytes = ("\n".join(log_lines) + "\n").encode("utf-8")
    files.append(("log.md", "text/markdown", log_bytes))

    graph_edges = [
        {
            "edge_id": row["edge_id"],
            "edge_type": row["edge_type"],
            "needs_revalidation": bool(row["needs_revalidation"]),
            "origin": row["origin"],
            "source_item_id": row["source_item_id"],
            "target_item_id": row["target_item_id"],
        }
        for row in snapshot.edges
    ]
    item_ids = {item.item_id for item in snapshot.items}
    if any(
        row["source_item_id"] not in item_ids
        or row["target_item_id"] not in item_ids
        for row in graph_edges
    ):
        raise ProjectionMaterializationError(
            "An effective graph edge is missing a projected endpoint."
        )
    graph = {
        "edges": graph_edges,
        "generation": snapshot.generation,
        "nodes": item_rows,
        "schema_version": 1,
        "space_id": snapshot.space_id,
    }
    graph_bytes = _canonical_json_bytes(graph) + b"\n"
    files.append(("graph/graph.json", "application/json", graph_bytes))

    incoming: dict[str, list[dict[str, object]]] = {
        item.item_id: [] for item in snapshot.items
    }
    for row in graph_edges:
        incoming[str(row["target_item_id"])].append(
            {
                "edge_id": row["edge_id"],
                "edge_type": row["edge_type"],
                "source_item_id": row["source_item_id"],
            }
        )
    backlinks = {
        "backlinks": [
            {"incoming": incoming[item_id], "item_id": item_id}
            for item_id in sorted(incoming)
        ],
        "generation": snapshot.generation,
        "schema_version": 1,
        "space_id": snapshot.space_id,
    }
    backlinks_bytes = _canonical_json_bytes(backlinks) + b"\n"
    files.append(("graph/backlinks.json", "application/json", backlinks_bytes))

    files.sort(key=lambda value: value[0])
    entries = [
        {
            "media_type": media_type,
            "relative_path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
        for path, media_type, content in files
    ]
    entries_sha256 = _sha256_canonical(entries)
    manifest: dict[str, object] = {
        "canonical_high_water": snapshot.canonical_high_water,
        "created_at": _utc_from_ms(snapshot.created_at_ms),
        "entries": entries,
        "entries_sha256": entries_sha256,
        "generation": snapshot.generation,
        "projection_name": PROJECTION_NAME,
        "schema_version": PROJECTION_MANIFEST_SCHEMA_VERSION,
        "space_id": snapshot.space_id,
    }
    manifest_bytes = _canonical_json_bytes(manifest) + b"\n"
    return _PreparedGeneration(
        snapshot=snapshot,
        files=tuple(files),
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        result=ProjectionSpaceResult(
            space_id=snapshot.space_id,
            generation=snapshot.generation,
            canonical_high_water=snapshot.canonical_high_water,
            entry_count=len(entries),
            entries_sha256=entries_sha256,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        ),
    )


@contextmanager
def _child_anchor(
    parent: object,
    name: str,
    *,
    delete_access: bool = False,
) -> Iterator[object]:
    from ...tools.legacy_inventory import _WindowsDirectoryAnchor

    child_path = parent.path / name  # type: ignore[attr-defined]
    child = None
    if os.path.lexists(child_path):
        child = _WindowsDirectoryAnchor.open(
            child_path, delete_access=delete_access
        )
    else:
        try:
            child = _WindowsDirectoryAnchor.create_relative_directory(parent, name)
        except OSError:
            if not os.path.lexists(child_path):
                raise
            child = _WindowsDirectoryAnchor.open(
                child_path, delete_access=delete_access
            )
    try:
        yield child
    finally:
        child.close()


def _read_relative(anchor: object, name: str, *, expected_path: Path) -> bytes:
    from ..legacy.blob_stager import (
        _WindowsBlobLease,
        _file_information,
        _nt_open_relative,
        _read_handle,
    )
    from ...tools.legacy_inventory import _windows_final_path

    handle = _nt_open_relative(anchor.handle, name)  # type: ignore[attr-defined]
    try:
        final = _windows_final_path(handle)
        if os.path.normcase(str(final)) != os.path.normcase(
            str(expected_path.resolve(strict=True))
        ):
            raise ProjectionMaterializationError(
                "A projection file escaped its pinned generation."
            )
        attributes, links, size = _file_information(handle)
        if attributes & 0x400 or links != 1:
            raise ProjectionMaterializationError(
                "A projection file is reparse-backed or multiply linked."
            )
        content = _read_handle(handle)
        if len(content) != size:
            raise ProjectionMaterializationError(
                "A projection file changed while it was read."
            )
        return content
    finally:
        _WindowsBlobLease(handle).close()


def _pin_relative(
    anchor: object,
    name: str,
    *,
    expected_path: Path,
    expected_content: bytes,
) -> int:
    from ..legacy.blob_stager import (
        _file_information,
        _nt_open_relative,
        _read_handle,
    )
    from ...tools.legacy_inventory import _windows_final_path

    handle = _nt_open_relative(anchor.handle, name)  # type: ignore[attr-defined]
    try:
        if os.path.normcase(str(_windows_final_path(handle))) != os.path.normcase(
            str(expected_path.resolve(strict=True))
        ):
            raise ProjectionMaterializationError(
                "A pinned projection file escaped its generation."
            )
        attributes, links, size = _file_information(handle)
        content = _read_handle(handle)
        if (
            attributes & 0x400
            or links != 1
            or size != len(expected_content)
            or content != expected_content
        ):
            raise ProjectionMaterializationError(
                "A pinned projection file is unsafe or differs."
            )
        return handle
    except Exception:
        from ...tools.legacy_inventory import _close_windows_handle

        _close_windows_handle(handle)
        raise


def _write_relative(anchor: object, name: str, content: bytes, *, replace: bool) -> None:
    from ...tools.legacy_inventory import (
        _close_windows_handle,
        _nt_clear_disposition,
        _nt_rename_relative,
        _nt_set_disposition,
        _validate_simple_windows_component,
        _windows_final_path,
        _write_windows_handle,
    )

    final_name = _validate_simple_windows_component(name)
    final_path = anchor.path / final_name  # type: ignore[attr-defined]
    temporary = ".projection-" + secrets.token_hex(16) + ".tmp"
    handle = anchor.create_relative_file(temporary)  # type: ignore[attr-defined]
    committed = False
    try:
        _nt_set_disposition(handle)
        _write_windows_handle(handle, content)
        _nt_clear_disposition(handle)
        _nt_rename_relative(handle, anchor.handle, final_name, replace=replace)  # type: ignore[attr-defined]
        committed = True
        if os.path.normcase(str(_windows_final_path(handle))) != os.path.normcase(
            str(final_path.resolve(strict=True))
        ):
            raise ProjectionMaterializationError(
                "A projection file final path changed during publication."
            )
    except Exception as original:
        cleanup_error: Exception | None = None
        if not committed:
            try:
                _nt_set_disposition(handle)
            except Exception as exc:
                cleanup_error = exc
        try:
            _close_windows_handle(handle)
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise ProjectionMaterializationError(
                "A failed projection write could not be cleaned safely."
            ) from original
        raise
    else:
        _close_windows_handle(handle)


def _relative_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if (
        not parts
        or any(not part or part in {".", ".."} for part in parts)
        or len(parts) > 2
    ):
        raise ProjectionMaterializationError(
            "A projection entry path is invalid."
        )
    return parts


def _verify_generation(anchor: object, prepared: _PreparedGeneration) -> None:
    expected_root_files = {"index.md", "log.md"}
    expected_sources = {
        Path(path).name for path, _media, _content in prepared.files
        if path.startswith("sources/")
    }
    expected_graph = {"graph.json", "backlinks.json"}
    actual_root = {value.name for value in anchor.path.iterdir()}  # type: ignore[attr-defined]
    if actual_root != expected_root_files | {"sources", "graph"}:
        raise ProjectionMaterializationError(
            "An immutable projection generation has unexpected entries."
        )
    with _child_anchor(anchor, "sources") as sources:
        if {value.name for value in sources.path.iterdir()} != expected_sources:  # type: ignore[attr-defined]
            raise ProjectionMaterializationError(
                "An immutable projection source set differs."
            )
        for path, _media, content in prepared.files:
            parts = _relative_parts(path)
            if parts[0] != "sources":
                continue
            observed = _read_relative(
                sources,
                parts[1],
                expected_path=sources.path / parts[1],  # type: ignore[attr-defined]
            )
            if observed != content:
                raise ProjectionMaterializationError(
                    "An immutable projection source differs."
                )
    with _child_anchor(anchor, "graph") as graph:
        if {value.name for value in graph.path.iterdir()} != expected_graph:  # type: ignore[attr-defined]
            raise ProjectionMaterializationError(
                "An immutable projection graph set differs."
            )
        for path, _media, content in prepared.files:
            parts = _relative_parts(path)
            if parts[0] != "graph":
                continue
            observed = _read_relative(
                graph,
                parts[1],
                expected_path=graph.path / parts[1],  # type: ignore[attr-defined]
            )
            if observed != content:
                raise ProjectionMaterializationError(
                    "An immutable projection graph differs."
                )
    for path, _media, content in prepared.files:
        parts = _relative_parts(path)
        if len(parts) != 1:
            continue
        observed = _read_relative(
            anchor,
            parts[0],
            expected_path=anchor.path / parts[0],  # type: ignore[attr-defined]
        )
        if observed != content:
            raise ProjectionMaterializationError(
                "An immutable projection generation differs."
            )


def _cleanup_staging(anchor: object) -> None:
    cleanup_error: Exception | None = None
    try:
        for child_name in ("sources", "graph"):
            child_path = anchor.path / child_name  # type: ignore[attr-defined]
            if not child_path.exists():
                continue
            with _child_anchor(anchor, child_name, delete_access=True) as child:
                for value in list(child.path.iterdir()):  # type: ignore[attr-defined]
                    if not value.is_file() or value.is_symlink():
                        raise ProjectionMaterializationError(
                            "A projection staging directory contains an unsafe entry."
                        )
                    value.unlink()
                child.dispose()  # type: ignore[attr-defined]
        for child_name in ("index.md", "log.md"):
            child_path = anchor.path / child_name  # type: ignore[attr-defined]
            if child_path.exists():
                child_path.unlink()
        anchor.dispose()  # type: ignore[attr-defined]
    except Exception as exc:
        cleanup_error = exc
    if cleanup_error is not None:
        raise ProjectionMaterializationError(
            "A projection staging directory could not be cleaned safely."
        ) from cleanup_error


def _cleanup_abandoned_staging(anchor: object) -> None:
    """Remove only scanner-owned incomplete generation directories."""

    from ...tools.legacy_inventory import _WindowsDirectoryAnchor

    for path in sorted(anchor.path.iterdir(), key=lambda value: value.name):  # type: ignore[attr-defined]
        if re.fullmatch(r"\.generation-[0-9a-f]{32}", path.name) is None:
            continue
        if not path.is_dir() or path.is_symlink():
            raise ProjectionMaterializationError(
                "An abandoned projection staging entry is unsafe."
            )
        staging = _WindowsDirectoryAnchor.open(
            path, writable=True, delete_access=True
        )
        try:
            _cleanup_staging(staging)
        finally:
            staging.close()
        if path.exists():
            raise ProjectionMaterializationError(
                "An abandoned projection staging directory remains."
            )


class _ProjectionWriter:
    __slots__ = ("_boundary", "_failpoint", "_layout", "_root")

    def __init__(
        self,
        layout: MigrationStorageLayout,
        boundary: MigrationStorageBoundary,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise ProjectionMaterializationError(
                "Projection publication requires the approved Windows host."
            )
        self._layout = layout
        self._boundary = boundary
        self._failpoint = failpoint
        self._root = layout.projection_root.resolve(strict=True)

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _verify_layout(self) -> None:
        verify_migration_storage_layout(
            self._layout,
            require_database=True,
            expected=self._boundary,
        )

    def stage_generation(self, prepared: _PreparedGeneration) -> None:
        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _nt_rename_relative,
            _pinned_directory,
        )

        self._verify_layout()
        generation_name = str(prepared.snapshot.generation)
        with _pinned_directory(self._root) as root:
            with _child_anchor(root, prepared.snapshot.space_id) as space:
                with _child_anchor(space, "generations") as generations:
                    _cleanup_abandoned_staging(generations)
                    target = generations.path / generation_name  # type: ignore[attr-defined]
                    if target.exists():
                        existing = _WindowsDirectoryAnchor.open(target)
                        try:
                            _verify_generation(existing, prepared)
                        finally:
                            existing.close()
                        return
                    staging_name = ".generation-" + secrets.token_hex(16)
                    staging = _WindowsDirectoryAnchor.create_relative_directory(
                        generations, staging_name
                    )
                    committed = False
                    try:
                        with _child_anchor(staging, "sources") as sources:
                            with _child_anchor(staging, "graph") as graph:
                                wrote_markdown = False
                                for path, _media, content in prepared.files:
                                    parts = _relative_parts(path)
                                    target_anchor = staging
                                    name = parts[0]
                                    if len(parts) == 2:
                                        target_anchor = (
                                            sources if parts[0] == "sources" else graph
                                        )
                                        name = parts[1]
                                    _write_relative(
                                        target_anchor, name, content, replace=False
                                    )
                                    if path == "graph/graph.json":
                                        self._hit("projector.after_graph_replace")
                                    elif path == "index.md":
                                        self._hit("projector.after_index_replace")
                                    elif path.startswith("sources/"):
                                        wrote_markdown = True
                                if wrote_markdown:
                                    self._hit("projector.after_markdown_replace")
                        self._hit("projector.after_staging_write")
                        _verify_generation(staging, prepared)
                        _nt_rename_relative(
                            staging.handle,  # type: ignore[attr-defined]
                            generations.handle,  # type: ignore[attr-defined]
                            generation_name,
                            replace=False,
                        )
                        committed = True
                    except OSError:
                        if target.exists():
                            _cleanup_staging(staging)
                            existing = _WindowsDirectoryAnchor.open(target)
                            try:
                                _verify_generation(existing, prepared)
                            finally:
                                existing.close()
                        else:
                            _cleanup_staging(staging)
                            raise
                    except Exception:
                        if not committed:
                            _cleanup_staging(staging)
                        raise
                    finally:
                        staging.close()
        self._verify_layout()

    def publish_manifest(
        self,
        prepared: _PreparedGeneration,
        pinned: _PinnedGeneration | None = None,
    ) -> None:
        from ...tools.legacy_inventory import _pinned_directory

        self._verify_layout()
        if pinned is not None:
            space = pinned.space_anchor(prepared.snapshot.space_id)
            expected_space = self._root / prepared.snapshot.space_id
            if os.path.normcase(str(space.path)) != os.path.normcase(  # type: ignore[attr-defined]
                str(expected_space)
            ):
                raise ProjectionMaterializationError(
                    "The pinned projection space differs from the manifest target."
                )
            _write_relative(
                space,
                "manifest.json",
                prepared.manifest_bytes,
                replace=True,
            )
            observed = _read_relative(
                space,
                "manifest.json",
                expected_path=space.path / "manifest.json",  # type: ignore[attr-defined]
            )
            if observed != prepared.manifest_bytes:
                raise ProjectionMaterializationError(
                    "The published projection manifest differs."
                )
            self._verify_layout()
            return
        with _pinned_directory(self._root) as root:
            with _child_anchor(root, prepared.snapshot.space_id) as space:
                _write_relative(
                    space,
                    "manifest.json",
                    prepared.manifest_bytes,
                    replace=True,
                )
                observed = _read_relative(
                    space,
                    "manifest.json",
                    expected_path=space.path / "manifest.json",  # type: ignore[attr-defined]
                )
                if observed != prepared.manifest_bytes:
                    raise ProjectionMaterializationError(
                        "The published projection manifest differs."
                    )
        self._verify_layout()

    def pin_generations(
        self, prepared_values: Sequence[_PreparedGeneration]
    ) -> _PinnedGeneration:
        from ...tools.legacy_inventory import _WindowsDirectoryAnchor

        self._verify_layout()
        anchors: list[object] = []
        handles: list[int] = []
        space_anchors: dict[str, object] = {}
        try:
            root = _WindowsDirectoryAnchor.open(self._root)
            anchors.append(root)
            for prepared in prepared_values:
                if prepared.snapshot.space_id in space_anchors:
                    raise ProjectionMaterializationError(
                        "A projection rebuild prepared the same space more than once."
                    )
                space = _WindowsDirectoryAnchor.open(
                    root.path / prepared.snapshot.space_id
                )
                anchors.append(space)
                space_anchors[prepared.snapshot.space_id] = space
                generations = _WindowsDirectoryAnchor.open(
                    space.path / "generations", writable=False
                )
                anchors.append(generations)
                generation = _WindowsDirectoryAnchor.open(
                    generations.path / str(prepared.snapshot.generation),
                    writable=False,
                )
                anchors.append(generation)
                sources = _WindowsDirectoryAnchor.open(
                    generation.path / "sources", writable=False
                )
                anchors.append(sources)
                graph = _WindowsDirectoryAnchor.open(
                    generation.path / "graph", writable=False
                )
                anchors.append(graph)
                for path, _media, content in prepared.files:
                    parts = _relative_parts(path)
                    target_anchor = generation
                    name = parts[0]
                    if len(parts) == 2:
                        target_anchor = sources if parts[0] == "sources" else graph
                        name = parts[1]
                    handles.append(
                        _pin_relative(
                            target_anchor,
                            name,
                            expected_path=target_anchor.path / name,
                            expected_content=content,
                        )
                    )
            self._verify_layout()
            return _PinnedGeneration(handles, anchors, space_anchors)
        except Exception as exc:
            try:
                _PinnedGeneration(handles, anchors, space_anchors).close()
            except Exception:
                exc.add_note(
                    "The partially acquired projection-generation lease also "
                    "failed to close."
                )
            raise


def _update_projection_state(
    factory: ConnectionFactory,
    *,
    expected_snapshots: Sequence[_SpaceSnapshot],
    prepared: Sequence[_PreparedGeneration],
    exact_space_set: bool = True,
) -> None:
    expected_binding = _snapshot_binding(expected_snapshots)
    connection = factory.open()
    try:
        with transaction(connection):
            current = _matching_snapshots(
                _capture_rows(connection),
                expected_snapshots,
                exact_space_set=exact_space_set,
            )
            if _snapshot_binding(current) != expected_binding:
                raise ProjectionCanonicalDriftError(
                    "Canonical state changed before projection publication completed."
                )
            now_ms = db_now_ms(connection)
            prepared_by_space = {
                value.snapshot.space_id: value for value in prepared
            }
            for snapshot in current:
                value = prepared_by_space.get(snapshot.space_id)
                if value is None:
                    raise ProjectionCanonicalDriftError(
                        "A canonical space is missing from the prepared projection."
                    )
                existing = connection.execute(
                    """SELECT desired_generation,built_generation,checksum_sha256,
                              status,last_error_json,updated_at,built_at,row_version
                         FROM projection_state
                        WHERE projection_name=? AND space_id=?""",
                    (PROJECTION_NAME, snapshot.space_id),
                ).fetchone()
                checksum = value.result.manifest_sha256
                if existing is None:
                    connection.execute(
                        """INSERT INTO projection_state(
                               projection_name,space_id,desired_generation,
                               built_generation,checksum_sha256,status,last_error_json,
                               updated_at,built_at,row_version
                           ) VALUES (?,?,?,?,?,'ready',NULL,?,?,1)""",
                        (
                            PROJECTION_NAME,
                            snapshot.space_id,
                            snapshot.generation,
                            snapshot.generation,
                            checksum,
                            now_ms,
                            now_ms,
                        ),
                    )
                    continue
                if int(existing["desired_generation"]) != snapshot.generation:
                    raise ProjectionCanonicalDriftError(
                        "Projection desired generation differs from canonical state."
                    )
                exact = (
                    int(existing["built_generation"]) == snapshot.generation
                    and existing["checksum_sha256"] == checksum
                    and existing["status"] == "ready"
                    and existing["last_error_json"] is None
                )
                if exact:
                    continue
                connection.execute(
                    """UPDATE projection_state
                          SET built_generation=?,checksum_sha256=?,status='ready',
                              last_error_json=NULL,updated_at=?,built_at=?,
                              row_version=row_version+1
                        WHERE projection_name=? AND space_id=? AND row_version=?""",
                    (
                        snapshot.generation,
                        checksum,
                        now_ms,
                        now_ms,
                        PROJECTION_NAME,
                        snapshot.space_id,
                        int(existing["row_version"]),
                    ),
                )
    finally:
        connection.close()


class FullProjectionMaterializer:
    """Build every per-space file projection from one canonical snapshot."""

    __slots__ = (
        "_boundary",
        "_factory",
        "_failpoint",
        "_layout",
        "_stager",
        "_writer",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be a MigrationStorageLayout.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        self._factory = factory
        self._failpoint = failpoint
        self._layout = layout
        self._boundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self._stager = FilesystemLegacyBlobStager(layout)
        self._writer = _ProjectionWriter(
            layout, self._boundary, failpoint=self._failpoint
        )

    def rebuild_all(self) -> FullProjectionRebuildResult:
        verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )
        _verify_database_integrity(self._factory)
        snapshots = _capture_database(self._factory)
        results = self._rebuild_snapshots(snapshots, exact_space_set=True)
        rows = [value.report_row() for value in results]
        return FullProjectionRebuildResult(
            projection_name=PROJECTION_NAME,
            spaces=results,
            spaces_sha256=_sha256_canonical(rows),
            entry_count=sum(value.entry_count for value in results),
        )

    def rebuild_space(self, space_id: str) -> ProjectionSpaceResult:
        """Rebuild one current space generation without coupling other spaces.

        The selected space is still captured from a read-consistent database
        snapshot.  Unrelated-space writes may proceed without invalidating this
        event, while any selected-space drift before manifest/state publication
        remains a hard retry boundary.
        """

        if not isinstance(space_id, str):
            raise TypeError("space_id must be a canonical UUID string.")
        try:
            canonical = str(uuid.UUID(space_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("space_id must be a canonical UUID string.") from exc
        if canonical != space_id:
            raise ValueError("space_id must be a canonical UUID string.")
        verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )
        _verify_database_integrity(self._factory)
        all_snapshots = _capture_database(self._factory)
        selected = tuple(
            value for value in all_snapshots if value.space_id == space_id
        )
        if len(selected) != 1:
            raise ProjectionMaterializationError(
                "The requested canonical knowledge space is unavailable."
            )
        return self._rebuild_snapshots(
            selected, exact_space_set=False
        )[0]

    def _rebuild_snapshots(
        self,
        snapshots: Sequence[_SpaceSnapshot],
        *,
        exact_space_set: bool,
    ) -> tuple[ProjectionSpaceResult, ...]:
        binding = _snapshot_binding(snapshots)
        blob_cache: dict[str, tuple[object, bytes]] = {}
        generation_lease: _PinnedGeneration | None = None
        primary_error: BaseException | None = None
        try:
            prepared = tuple(
                _prepare_generation(
                    snapshot,
                    stager=self._stager,
                    blob_cache=blob_cache,
                )
                for snapshot in snapshots
            )
            for generation in prepared:
                self._writer.stage_generation(generation)
            generation_lease = self._writer.pin_generations(prepared)
            observed = _matching_snapshots(
                _capture_database(self._factory),
                snapshots,
                exact_space_set=exact_space_set,
            )
            if _snapshot_binding(observed) != binding:
                raise ProjectionCanonicalDriftError(
                    "Canonical state changed while projections were staged."
                )
            for generation in prepared:
                self._writer.publish_manifest(generation, generation_lease)
            _update_projection_state(
                self._factory,
                expected_snapshots=snapshots,
                prepared=prepared,
                exact_space_set=exact_space_set,
            )
            _verify_database_integrity(self._factory)
            verify_migration_storage_layout(
                self._layout, require_database=True, expected=self._boundary
            )
            return tuple(value.result for value in prepared)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            lease_error: Exception | None = None
            for lease in (() if generation_lease is None else (generation_lease,)):
                try:
                    lease.close()
                except Exception as exc:
                    lease_error = lease_error or exc
            for observation, _content in blob_cache.values():
                try:
                    observation.lease.close()  # type: ignore[attr-defined]
                except Exception as exc:  # pragma: no cover - OS cleanup failure
                    lease_error = lease_error or exc
            if lease_error is not None:
                if primary_error is not None:
                    primary_error.add_note(
                        "A projection generation or source blob handle also failed "
                        "to close during error cleanup."
                    )
                else:
                    raise lease_error


__all__ = [
    "FullProjectionMaterializer",
    "FullProjectionRebuildResult",
    "PROJECTION_MANIFEST_SCHEMA_VERSION",
    "PROJECTION_NAME",
    "ProjectionCanonicalDriftError",
    "ProjectionMaterializationError",
    "ProjectionSpaceResult",
]
