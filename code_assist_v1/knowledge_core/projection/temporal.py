"""Deterministic temporal presentation projection and trusted re-import."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..domain import TemporalAssertionKind
from ..errors import KnowledgeCoreError
from ..legacy.blob_stager import FilesystemLegacyBlobStager
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..migrations.runner import verify_canonical_integrity_before_projection_rebuild
from ..repositories.temporal import TemporalRepository
from ..services.temporal import _event_head, temporal_summary_sha256
from .materializer import (
    ProjectionCanonicalDriftError,
    ProjectionMaterializationError,
    _child_anchor,
    _read_relative,
    _write_relative,
)


TEMPORAL_PROJECTION_NAME = "markdown_v2"
TEMPORAL_MANIFEST_SCHEMA_VERSION = 2
FOOTER_SCHEMA_VERSION = 2
GRAPH_GENERATION = 0
_KST = ZoneInfo("Asia/Seoul")
_LEDGER_NAMESPACE = UUID("16124ba8-6d7b-491a-bf24-da983ac5a1c7")
_GENERATION_RE = re.compile(r"^[0-9]+-0-[0-9]+$", re.ASCII)
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
    "temporal.assert",
    "temporal.revoke",
    "verification.attest",
    "verification.revoke",
)


class ProjectionManifestUntrustedError(KnowledgeCoreError):
    code = "PROJECTION_MANIFEST_UNTRUSTED"
    http_status = 422
    retryable = False


@dataclass(frozen=True, slots=True)
class TemporalFooterMetadata:
    registered_at_ms: int
    content_modified_at_ms: int
    revision_no: int
    source_modified_at: Mapping[str, object] | None = None
    information_effective_at: Mapping[str, object] | None = None
    last_verified_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TemporalProjectionResult:
    space_id: str
    content_generation: int
    graph_generation: int
    temporal_generation: int
    canonical_high_water: int
    revision_count: int
    manifest_sha256: str
    projection_root_identity_sha256: str

    @property
    def rendered_generation(self) -> int:
        return self.content_generation + self.temporal_generation


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    revision_id: str
    revision_no: int
    title: str
    registered_at_ms: int
    content_modified_at_ms: int
    raw_content_sha256: str
    normalized_content_sha256: str
    normalizer_version: str
    body_content_md: str | None
    body_blob_sha256: str | None
    body_size_bytes: int
    body_sha256: str
    blob_size_bytes: int | None
    blob_media_type: str | None
    blob_relative_path: str | None
    blob_verification_status: str | None
    source_modified_at: Mapping[str, object] | None
    information_effective_at: Mapping[str, object] | None
    last_verified_at_ms: int | None
    temporal_summary_sha256: str


@dataclass(frozen=True, slots=True)
class _Snapshot:
    space_id: str
    content_generation: int
    temporal_generation: int
    space_row_version: int
    status: str
    items: tuple[_Item, ...]
    audit_rows: tuple[dict[str, object], ...]
    canonical_high_water: int
    snapshot_as_of_at_ms: int
    binding_sha256: str


@dataclass(frozen=True, slots=True)
class _Prepared:
    snapshot: _Snapshot
    files: tuple[tuple[str, bytes], ...]
    manifest: dict[str, object]
    manifest_json: str
    manifest_bytes: bytes
    manifest_sha256: str
    root_identity_sha256: str


def _canonical_json(value: object) -> bytes:
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
            "Temporal projection data is not canonical JSON."
        ) from exc


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_body(value: bytes | str) -> bytes:
    if isinstance(value, str):
        if "\x00" in value:
            raise ProjectionMaterializationError("Markdown contains a forbidden NUL.")
        raw = value.encode("utf-8")
    elif isinstance(value, bytes):
        raw = value
    else:
        raise TypeError("body must be bytes or text")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProjectionMaterializationError("Markdown must be valid UTF-8.") from exc
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _kst_instant(milliseconds: int) -> str:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or milliseconds < 0:
        raise ProjectionMaterializationError("A footer instant is invalid.")
    seconds, millis = divmod(milliseconds, 1000)
    try:
        value = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=millis * 1000
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise ProjectionMaterializationError("A footer instant is out of range.") from exc
    return value.astimezone(_KST).isoformat(timespec="milliseconds")


def _typed_footer_value(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise ProjectionMaterializationError("A footer temporal value is invalid.")
    if set(value) == {"precision", "value"} and value.get("precision") == "instant":
        raw = value.get("value")
        if not isinstance(raw, str):
            raise ProjectionMaterializationError("A footer instant is invalid.")
        source = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(source)
        except ValueError as exc:
            raise ProjectionMaterializationError("A footer instant is invalid.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond % 1000:
            raise ProjectionMaterializationError("A footer instant is invalid.")
        milliseconds = int(parsed.timestamp() * 1000)
        return _kst_instant(milliseconds)
    if (
        set(value) == {"precision", "timezone", "value"}
        and value.get("precision") == "date"
        and value.get("timezone") == "Asia/Seoul"
        and isinstance(value.get("value"), str)
    ):
        raw_date = str(value["value"])
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ProjectionMaterializationError("A footer date is invalid.") from exc
        if parsed_date.isoformat() == raw_date:
            return raw_date
    raise ProjectionMaterializationError("A footer temporal value is invalid.")


def render_temporal_page(
    body: bytes | str,
    metadata: TemporalFooterMetadata,
) -> bytes:
    """Render the frozen footer grammar after an LF-normalized body prefix."""

    if not isinstance(metadata, TemporalFooterMetadata):
        raise TypeError("metadata must be TemporalFooterMetadata")
    if (
        isinstance(metadata.revision_no, bool)
        or not isinstance(metadata.revision_no, int)
        or metadata.revision_no < 1
    ):
        raise ProjectionMaterializationError("Revision number must be positive.")
    lines = [
        "<!-- knowledge-footer:v2 -->",
        "---",
        "> **문서 정보**  ",
        f"> 생성: {_kst_instant(metadata.registered_at_ms)}  ",
        f"> 마지막 내용 수정: {_kst_instant(metadata.content_modified_at_ms)}  ",
        f"> Revision: {metadata.revision_no}  ",
    ]
    if metadata.source_modified_at is not None:
        lines.append(f"> 원문 수정: {_typed_footer_value(metadata.source_modified_at)}  ")
    if metadata.information_effective_at is not None:
        lines.append(
            f"> 정보 기준: {_typed_footer_value(metadata.information_effective_at)}  "
        )
    if metadata.last_verified_at_ms is not None:
        lines.append(f"> 마지막 검증: {_kst_instant(metadata.last_verified_at_ms)}  ")
    lines.append("<!-- /knowledge-footer:v2 -->")
    return _normalize_body(body) + b"\n\n" + ("\n".join(lines) + "\n").encode("utf-8")


def projection_root_identity_sha256(root: Path) -> str:
    try:
        resolved = root.resolve(strict=True)
        facts = resolved.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProjectionMaterializationError(
            "The projection root identity is unavailable."
        ) from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise ProjectionMaterializationError("The projection root is unsafe.")
    identity = {
        "resolved_path": os.path.normcase(str(resolved)),
        "st_dev": int(facts.st_dev),
        "st_ino": int(facts.st_ino),
    }
    return _sha(_canonical_json(identity))


def _utc_instant(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    try:
        value = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=millis * 1000
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise ProjectionMaterializationError("A snapshot instant is invalid.") from exc
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def _capture(factory: ConnectionFactory, space_id: str) -> _Snapshot:
    repository = TemporalRepository()
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            space = connection.execute(
                """SELECT id,status,generation,temporal_generation,row_version,
                          created_at,updated_at
                     FROM knowledge_space WHERE id=?""",
                (space_id,),
            ).fetchone()
            if space is None:
                raise ProjectionMaterializationError(
                    "The requested temporal projection space is unavailable."
                )
            rows = connection.execute(
                """SELECT i.id AS item_id,i.current_revision_id AS revision_id,
                          i.created_at AS registered_at,r.created_at AS content_modified_at,
                          r.revision_no,r.title,r.raw_content_sha256,
                          r.normalized_content_sha256,r.normalizer_version,
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
                    WHERE i.space_id=? AND i.lifecycle_status='active'
                      AND i.curation_status='published'
                    ORDER BY i.id""",
                (space_id,),
            ).fetchall()
            items: list[_Item] = []
            binding_heads: list[dict[str, object]] = []
            for row in rows:
                revision_id = str(row["revision_id"])
                source_pair = repository.get_assertion_head(
                    connection,
                    revision_id=revision_id,
                    kind=TemporalAssertionKind.SOURCE_MODIFIED,
                )
                effective_pair = repository.get_assertion_head(
                    connection,
                    revision_id=revision_id,
                    kind=TemporalAssertionKind.INFORMATION_EFFECTIVE,
                )
                source_record = None if source_pair is None else source_pair[1]
                effective_record = None if effective_pair is None else effective_pair[1]
                summary = temporal_summary_sha256(
                    revision_id=revision_id,
                    source_modified=source_record,
                    information_effective=effective_record,
                )
                source_value = (
                    None
                    if source_pair is None or source_pair[0].state != "asserted"
                    else _event_head(source_record)["value"]
                )
                effective_value = (
                    None
                    if effective_pair is None or effective_pair[0].state != "asserted"
                    else _event_head(effective_record)["value"]
                )
                verification_pair = repository.get_verification_head(
                    connection, revision_id=revision_id
                )
                last_verified = None
                if (
                    verification_pair is not None
                    and verification_pair[0].state == "attested"
                    and verification_pair[1].temporal_summary_sha256 == summary
                ):
                    last_verified = verification_pair[1].attested_at_ms
                item = _Item(
                    item_id=str(row["item_id"]),
                    revision_id=revision_id,
                    revision_no=int(row["revision_no"]),
                    title=str(row["title"]),
                    registered_at_ms=int(row["registered_at"]),
                    content_modified_at_ms=int(row["content_modified_at"]),
                    raw_content_sha256=str(row["raw_content_sha256"]),
                    normalized_content_sha256=str(row["normalized_content_sha256"]),
                    normalizer_version=str(row["normalizer_version"]),
                    body_content_md=(
                        None if row["body_content_md"] is None else str(row["body_content_md"])
                    ),
                    body_blob_sha256=(
                        None
                        if row["body_blob_sha256"] is None
                        else str(row["body_blob_sha256"])
                    ),
                    body_size_bytes=int(row["body_size_bytes"]),
                    body_sha256=str(row["body_sha256"]),
                    blob_size_bytes=(
                        None if row["blob_size_bytes"] is None else int(row["blob_size_bytes"])
                    ),
                    blob_media_type=(
                        None if row["blob_media_type"] is None else str(row["blob_media_type"])
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
                    source_modified_at=source_value,
                    information_effective_at=effective_value,
                    last_verified_at_ms=last_verified,
                    temporal_summary_sha256=summary,
                )
                items.append(item)
                binding_heads.append(
                    {
                        "effective_event_id": (
                            None if effective_pair is None else effective_pair[0].head_event_id
                        ),
                        "item_id": item.item_id,
                        "revision_id": item.revision_id,
                        "source_event_id": (
                            None if source_pair is None else source_pair[0].head_event_id
                        ),
                        "summary": summary,
                        "verification_event_id": (
                            None
                            if verification_pair is None
                            else verification_pair[0].head_event_id
                        ),
                    }
                )
            placeholders = ",".join("?" for _ in _PROJECTED_AUDIT_ACTIONS)
            audit_rows = tuple(
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
                }
                for row in connection.execute(
                    f"""SELECT id,event_uuid,occurred_at,action,outcome,actor_kind,
                               item_id,base_revision_id,new_revision_id
                          FROM audit_event
                         WHERE space_id=? AND action IN ({placeholders})
                         ORDER BY occurred_at,id""",
                    (space_id, *_PROJECTED_AUDIT_ACTIONS),
                ).fetchall()
            )
            high_water_row = connection.execute(
                """SELECT id,occurred_at FROM audit_event
                    WHERE space_id=? ORDER BY id DESC LIMIT 1""",
                (space_id,),
            ).fetchone()
            high_water = 0 if high_water_row is None else int(high_water_row["id"])
            snapshot_at = (
                max(
                    [int(space["created_at"])]
                    + [item.registered_at_ms for item in items]
                    + [item.content_modified_at_ms for item in items]
                )
                if high_water_row is None
                else int(high_water_row["occurred_at"])
            )
            binding = {
                "audit": list(audit_rows),
                "content_generation": int(space["generation"]),
                "heads": binding_heads,
                "items": [
                    {
                        "body_blob_sha256": item.body_blob_sha256,
                        "body_sha256": item.body_sha256,
                        "body_size_bytes": item.body_size_bytes,
                        "item_id": item.item_id,
                        "normalized_content_sha256": item.normalized_content_sha256,
                        "revision_id": item.revision_id,
                        "revision_no": item.revision_no,
                    }
                    for item in items
                ],
                "space_id": space_id,
                "space_row_version": int(space["row_version"]),
                "status": str(space["status"]),
                "temporal_generation": int(space["temporal_generation"]),
            }
            return _Snapshot(
                space_id=space_id,
                content_generation=int(space["generation"]),
                temporal_generation=int(space["temporal_generation"]),
                space_row_version=int(space["row_version"]),
                status=str(space["status"]),
                items=tuple(items),
                audit_rows=audit_rows,
                canonical_high_water=high_water,
                snapshot_as_of_at_ms=snapshot_at,
                binding_sha256=_sha(_canonical_json(binding)),
            )
    finally:
        connection.close()


def _prepare(
    snapshot: _Snapshot,
    *,
    stager: FilesystemLegacyBlobStager,
    root_identity_sha256: str,
) -> tuple[_Prepared, tuple[object, ...]]:
    files: list[tuple[str, bytes]] = []
    entries: list[dict[str, object]] = []
    item_rows: list[dict[str, object]] = []
    leases: list[object] = []
    try:
        for item in snapshot.items:
            if (item.body_content_md is None) == (item.body_blob_sha256 is None):
                raise ProjectionMaterializationError(
                    "A temporal projection body has an invalid storage shape."
                )
            if item.body_content_md is not None:
                raw = item.body_content_md.encode("utf-8")
            else:
                if (
                    item.body_blob_sha256 != item.body_sha256
                    or item.blob_size_bytes != item.body_size_bytes
                    or item.blob_media_type is None
                    or item.blob_verification_status != "verified"
                ):
                    raise ProjectionMaterializationError(
                        "A temporal projection CAS binding is invalid."
                    )
                observation, raw = stager.verify_existing(
                    expected_sha256=str(item.body_blob_sha256),
                    expected_size=item.body_size_bytes,
                    media_type=item.blob_media_type,
                    normalized_content_sha256=item.normalized_content_sha256,
                    normalizer_version=item.normalizer_version,
                )
                leases.append(observation.lease)
            if len(raw) != item.body_size_bytes or _sha(raw) != item.body_sha256:
                raise ProjectionMaterializationError(
                    "A temporal projection body differs from canonical metadata."
                )
            if item.normalizer_version != "markdown_lf_v1":
                raise ProjectionMaterializationError(
                    "A temporal projection normalizer is unsupported."
                )
            prefix = _normalize_body(raw)
            if _sha(prefix) != item.normalized_content_sha256:
                raise ProjectionMaterializationError(
                    "A temporal projection normalized digest is invalid."
                )
            metadata = TemporalFooterMetadata(
                registered_at_ms=item.registered_at_ms,
                content_modified_at_ms=item.content_modified_at_ms,
                revision_no=item.revision_no,
                source_modified_at=item.source_modified_at,
                information_effective_at=item.information_effective_at,
                last_verified_at_ms=item.last_verified_at_ms,
            )
            page = render_temporal_page(prefix, metadata)
            source_path = f"sources/{item.item_id}.md"
            page_path = f"pages/{item.item_id}.md"
            files.extend(((source_path, prefix), (page_path, page)))
            item_rows.append(
                {
                    "body_encoding": "utf-8",
                    "body_normalizer_version": "markdown_lf_v1",
                    "canonical_normalized_sha256": item.normalized_content_sha256,
                    "canonical_raw_sha256": item.raw_content_sha256,
                    "footer_metadata": {
                        "content_modified_at_ms": item.content_modified_at_ms,
                        "information_effective_at": item.information_effective_at,
                        "last_verified_at_ms": item.last_verified_at_ms,
                        "registered_at_ms": item.registered_at_ms,
                        "revision_no": item.revision_no,
                        "source_modified_at": item.source_modified_at,
                    },
                    "footer_schema_version": FOOTER_SCHEMA_VERSION,
                    "item_id": item.item_id,
                    "page_relative_path": page_path,
                    "projected_body_sha256": _sha(prefix),
                    "projected_body_size_bytes": len(prefix),
                    "rendered_sha256": _sha(page),
                    "rendered_size_bytes": len(page),
                    "revision_id": item.revision_id,
                    "revision_no": item.revision_no,
                    "source_relative_path": source_path,
                    "temporal_summary_sha256": item.temporal_summary_sha256,
                    "title": item.title,
                }
            )
        index_lines = ["# Knowledge index", ""]
        for item in snapshot.items:
            index_lines.append(
                f"- [{_markdown_label(item.title)}](pages/{item.item_id}.md) "
                f"`{item.item_id}`"
            )
        files.append(("index.md", ("\n".join(index_lines) + "\n").encode("utf-8")))
        log_lines = ["# Activity log", ""]
        for row in snapshot.audit_rows:
            projected = {
                "action": row["action"],
                "actor_kind": row["actor_kind"],
                "base_revision_id": row["base_revision_id"],
                "event_uuid": row["event_uuid"],
                "item_id": row["item_id"],
                "new_revision_id": row["new_revision_id"],
                "occurred_at": _utc_instant(int(row["occurred_at"])),
                "outcome": row["outcome"],
            }
            log_lines.append("    " + _canonical_json(projected).decode("utf-8"))
        files.append(("log.md", ("\n".join(log_lines) + "\n").encode("utf-8")))
        files.sort(key=lambda pair: pair[0])
        for path, content in files:
            entries.append(
                {
                    "media_type": (
                        "text/markdown" if path.endswith(".md") else "application/octet-stream"
                    ),
                    "relative_path": path,
                    "sha256": _sha(content),
                    "size_bytes": len(content),
                }
            )
        manifest: dict[str, object] = {
            "body_encoding": "utf-8",
            "body_normalizer_version": "markdown_lf_v1",
            "content_generation": snapshot.content_generation,
            "entries": entries,
            "entries_sha256": _sha(_canonical_json(entries)),
            "footer_schema_version": FOOTER_SCHEMA_VERSION,
            "graph_generation": GRAPH_GENERATION,
            "items": item_rows,
            "projection_name": TEMPORAL_PROJECTION_NAME,
            "projection_root_identity_sha256": root_identity_sha256,
            "schema_version": TEMPORAL_MANIFEST_SCHEMA_VERSION,
            "snapshot_as_of_at": _utc_instant(snapshot.snapshot_as_of_at_ms),
            "snapshot_as_of_event_id": snapshot.canonical_high_water,
            "space_id": snapshot.space_id,
            "temporal_generation": snapshot.temporal_generation,
        }
        manifest_json = _canonical_json(manifest).decode("utf-8")
        manifest_bytes = manifest_json.encode("utf-8") + b"\n"
        return (
            _Prepared(
                snapshot=snapshot,
                files=tuple(files),
                manifest=manifest,
                manifest_json=manifest_json,
                manifest_bytes=manifest_bytes,
                manifest_sha256=_sha(manifest_bytes),
                root_identity_sha256=root_identity_sha256,
            ),
            tuple(leases),
        )
    except Exception:
        for lease in reversed(leases):
            lease.close()  # type: ignore[attr-defined]
        raise


def _relative_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if (
        not parts
        or any(not part or part in {".", ".."} for part in parts)
        or len(parts) > 2
        or (len(parts) == 2 and parts[0] not in {"sources", "pages"})
    ):
        raise ProjectionMaterializationError(
            "A temporal projection entry path is invalid."
        )
    return parts


def _verify_generation(anchor: object, prepared: _Prepared) -> None:
    actual_root = {value.name for value in anchor.path.iterdir()}  # type: ignore[attr-defined]
    if actual_root != {"index.md", "log.md", "manifest-v2.json", "sources", "pages"}:
        raise ProjectionMaterializationError(
            "A temporal projection generation has unexpected entries."
        )
    for directory in ("sources", "pages"):
        expected = {
            Path(path).name
            for path, _content in prepared.files
            if path.startswith(directory + "/")
        }
        with _child_anchor(anchor, directory) as child:
            actual = {value.name for value in child.path.iterdir()}  # type: ignore[attr-defined]
            if actual != expected:
                raise ProjectionMaterializationError(
                    "A temporal projection generation has an invalid file set."
                )
            for path, content in prepared.files:
                parts = _relative_parts(path)
                if len(parts) != 2 or parts[0] != directory:
                    continue
                observed = _read_relative(
                    child,
                    parts[1],
                    expected_path=child.path / parts[1],  # type: ignore[attr-defined]
                )
                if observed != content:
                    raise ProjectionMaterializationError(
                        "A temporal projection file differs from its manifest."
                    )
    root_files = dict(prepared.files)
    root_files["manifest-v2.json"] = prepared.manifest_bytes
    for name in ("index.md", "log.md", "manifest-v2.json"):
        observed = _read_relative(
            anchor,
            name,
            expected_path=anchor.path / name,  # type: ignore[attr-defined]
        )
        if observed != root_files[name]:
            raise ProjectionMaterializationError(
                "A temporal projection root file differs from its manifest."
            )


def _cleanup_staging(anchor: object) -> None:
    try:
        for directory in ("sources", "pages"):
            child_path = anchor.path / directory  # type: ignore[attr-defined]
            if not child_path.exists():
                continue
            with _child_anchor(anchor, directory, delete_access=True) as child:
                for value in list(child.path.iterdir()):  # type: ignore[attr-defined]
                    if not value.is_file() or value.is_symlink():
                        raise ProjectionMaterializationError(
                            "Temporal projection staging contains an unsafe entry."
                        )
                    value.unlink()
                child.dispose()  # type: ignore[attr-defined]
        for name in ("index.md", "log.md", "manifest-v2.json"):
            value = anchor.path / name  # type: ignore[attr-defined]
            if value.exists():
                if not value.is_file() or value.is_symlink():
                    raise ProjectionMaterializationError(
                        "Temporal projection staging contains an unsafe file."
                    )
                value.unlink()
        anchor.dispose()  # type: ignore[attr-defined]
    except Exception as exc:
        raise ProjectionMaterializationError(
            "Temporal projection staging could not be cleaned safely."
        ) from exc


def _cleanup_abandoned(anchor: object) -> None:
    from ...tools.legacy_inventory import _WindowsDirectoryAnchor

    for path in sorted(anchor.path.iterdir(), key=lambda value: value.name):  # type: ignore[attr-defined]
        if re.fullmatch(r"\.temporal-generation-[0-9a-f]{32}", path.name) is None:
            continue
        if not path.is_dir() or path.is_symlink():
            raise ProjectionMaterializationError(
                "An abandoned temporal projection entry is unsafe."
            )
        staging = _WindowsDirectoryAnchor.open(path, writable=True, delete_access=True)
        try:
            _cleanup_staging(staging)
        finally:
            staging.close()


class _TemporalWriter:
    __slots__ = ("_boundary", "_layout", "_root")

    def __init__(
        self,
        layout: MigrationStorageLayout,
        boundary: MigrationStorageBoundary,
    ) -> None:
        if os.name != "nt":
            raise ProjectionMaterializationError(
                "Temporal projection publication requires the approved Windows host."
            )
        self._layout = layout
        self._boundary = boundary
        self._root = layout.projection_root.resolve(strict=True)

    def _verify_layout(self) -> None:
        verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )

    def stage(self, prepared: _Prepared) -> None:
        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _nt_rename_relative,
            _pinned_directory,
        )

        self._verify_layout()
        generation_name = (
            f"{prepared.snapshot.content_generation}-{GRAPH_GENERATION}-"
            f"{prepared.snapshot.temporal_generation}"
        )
        if _GENERATION_RE.fullmatch(generation_name) is None:
            raise ProjectionMaterializationError(
                "A temporal projection generation name is invalid."
            )
        with _pinned_directory(self._root) as root:
            with _child_anchor(root, prepared.snapshot.space_id) as space:
                with _child_anchor(space, "temporal-generations") as generations:
                    _cleanup_abandoned(generations)
                    target = generations.path / generation_name  # type: ignore[attr-defined]
                    if target.exists():
                        existing = _WindowsDirectoryAnchor.open(target)
                        try:
                            _verify_generation(existing, prepared)
                        finally:
                            existing.close()
                    else:
                        staging_name = ".temporal-generation-" + secrets.token_hex(16)
                        staging = _WindowsDirectoryAnchor.create_relative_directory(
                            generations, staging_name
                        )
                        committed = False
                        try:
                            with _child_anchor(staging, "sources") as sources:
                                with _child_anchor(staging, "pages") as pages:
                                    for path, content in prepared.files:
                                        parts = _relative_parts(path)
                                        target_anchor = staging
                                        name = parts[0]
                                        if len(parts) == 2:
                                            target_anchor = (
                                                sources if parts[0] == "sources" else pages
                                            )
                                            name = parts[1]
                                        _write_relative(
                                            target_anchor, name, content, replace=False
                                        )
                            _write_relative(
                                staging,
                                "manifest-v2.json",
                                prepared.manifest_bytes,
                                replace=False,
                            )
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
                _write_relative(
                    space,
                    "manifest-v2.json",
                    prepared.manifest_bytes,
                    replace=True,
                )
        self._verify_layout()


class TemporalProjectionMaterializer:
    """Build one immutable content/graph/temporal presentation generation."""

    __slots__ = ("_boundary", "_factory", "_layout", "_stager", "_writer")

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be MigrationStorageLayout")
        self._factory = factory
        self._layout = layout
        self._boundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self._stager = FilesystemLegacyBlobStager(layout)
        self._writer = _TemporalWriter(layout, self._boundary)

    def rebuild_space(self, space_id: str) -> TemporalProjectionResult:
        try:
            if str(UUID(space_id)) != space_id:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("space_id must be a canonical UUID string") from exc
        verify_migration_storage_layout(
            self._layout, require_database=True, expected=self._boundary
        )
        connection = self._factory.open(read_only=True)
        try:
            verify_canonical_integrity_before_projection_rebuild(
                connection, self._factory.migration_catalog
            )
        finally:
            connection.close()
        snapshot = _capture(self._factory, space_id)
        if snapshot.status != "active":
            raise ProjectionMaterializationError(
                "Only an active space can publish temporal pages."
            )
        root_identity = projection_root_identity_sha256(self._layout.projection_root)
        prepared, leases = _prepare(
            snapshot,
            stager=self._stager,
            root_identity_sha256=root_identity,
        )
        primary_error: BaseException | None = None
        try:
            self._writer.stage(prepared)
            observed = _capture(self._factory, space_id)
            if observed.binding_sha256 != snapshot.binding_sha256:
                raise ProjectionCanonicalDriftError(
                    "Canonical temporal state changed during projection."
                )
            self._commit(prepared)
            return TemporalProjectionResult(
                space_id=space_id,
                content_generation=snapshot.content_generation,
                graph_generation=GRAPH_GENERATION,
                temporal_generation=snapshot.temporal_generation,
                canonical_high_water=snapshot.canonical_high_water,
                revision_count=len(snapshot.items),
                manifest_sha256=prepared.manifest_sha256,
                projection_root_identity_sha256=root_identity,
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            close_error: Exception | None = None
            for lease in reversed(leases):
                try:
                    lease.close()  # type: ignore[attr-defined]
                except Exception as exc:
                    close_error = close_error or exc
            if close_error is not None:
                if primary_error is not None:
                    primary_error.add_note(
                        "A temporal projection source lease also failed to close."
                    )
                else:
                    raise close_error

    def _commit(self, prepared: _Prepared) -> None:
        snapshot = prepared.snapshot
        connection = self._factory.open()
        try:
            with transaction(connection):
                now_ms = db_now_ms(connection)
                current = connection.execute(
                    """SELECT generation,temporal_generation,row_version,status
                         FROM knowledge_space WHERE id=?""",
                    (snapshot.space_id,),
                ).fetchone()
                if current is None or not (
                    str(current["status"]) == "active"
                    and int(current["generation"]) == snapshot.content_generation
                    and int(current["temporal_generation"]) == snapshot.temporal_generation
                    and int(current["row_version"]) == snapshot.space_row_version
                ):
                    raise ProjectionCanonicalDriftError(
                        "Canonical temporal state changed before ledger commit."
                    )
                existing = connection.execute(
                    """SELECT manifest_sha256,manifest_json,
                              projection_root_identity_sha256
                         FROM projection_manifest_v2_ledger
                        WHERE space_id=? AND content_generation=?
                          AND graph_generation=? AND temporal_generation=?""",
                    (
                        snapshot.space_id,
                        snapshot.content_generation,
                        GRAPH_GENERATION,
                        snapshot.temporal_generation,
                    ),
                ).fetchone()
                if existing is None:
                    ledger_id = str(
                        uuid5(
                            _LEDGER_NAMESPACE,
                            f"{snapshot.space_id}:{snapshot.content_generation}:"
                            f"{GRAPH_GENERATION}:{snapshot.temporal_generation}:"
                            f"{prepared.manifest_sha256}",
                        )
                    )
                    connection.execute(
                        """INSERT INTO projection_manifest_v2_ledger(
                             id,space_id,projection_name,content_generation,
                             graph_generation,temporal_generation,snapshot_as_of_at,
                             snapshot_as_of_event_id,revision_count,manifest_sha256,
                             manifest_json,projection_root_identity_sha256,created_at
                           ) VALUES (?,?,'markdown_v2',?,?,?,?,?,?,?,?,?,?)""",
                        (
                            ledger_id,
                            snapshot.space_id,
                            snapshot.content_generation,
                            GRAPH_GENERATION,
                            snapshot.temporal_generation,
                            snapshot.snapshot_as_of_at_ms,
                            snapshot.canonical_high_water,
                            len(snapshot.items),
                            prepared.manifest_sha256,
                            prepared.manifest_json,
                            prepared.root_identity_sha256,
                            now_ms,
                        ),
                    )
                elif not (
                    str(existing["manifest_sha256"]) == prepared.manifest_sha256
                    and str(existing["manifest_json"]) == prepared.manifest_json
                    and str(existing["projection_root_identity_sha256"])
                    == prepared.root_identity_sha256
                ):
                    raise ProjectionCanonicalDriftError(
                        "The immutable temporal projection ledger differs."
                    )
                rendered_generation = (
                    snapshot.content_generation + snapshot.temporal_generation
                )
                state = connection.execute(
                    """SELECT row_version FROM projection_state
                        WHERE projection_name='markdown_v2' AND space_id=?""",
                    (snapshot.space_id,),
                ).fetchone()
                if state is None:
                    connection.execute(
                        """INSERT INTO projection_state(
                             projection_name,space_id,desired_generation,
                             built_generation,checksum_sha256,status,last_error_json,
                             updated_at,built_at,row_version
                           ) VALUES ('markdown_v2',?,?,?,?,'ready',NULL,?,?,1)""",
                        (
                            snapshot.space_id,
                            rendered_generation,
                            rendered_generation,
                            prepared.manifest_sha256,
                            now_ms,
                            now_ms,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE projection_state
                              SET desired_generation=?,built_generation=?,
                                  checksum_sha256=?,status='ready',last_error_json=NULL,
                                  updated_at=?,built_at=?,row_version=row_version+1
                            WHERE projection_name='markdown_v2' AND space_id=?
                              AND row_version=?""",
                        (
                            rendered_generation,
                            rendered_generation,
                            prepared.manifest_sha256,
                            now_ms,
                            now_ms,
                            snapshot.space_id,
                            int(state["row_version"]),
                        ),
                    )
        finally:
            connection.close()


class TrustedTemporalProjectionImporter:
    """Recover only a manifest-bound LF-normalized canonical body prefix."""

    __slots__ = ("_factory", "_layout")

    def __init__(self, factory: ConnectionFactory, layout: MigrationStorageLayout) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be MigrationStorageLayout")
        self._factory = factory
        self._layout = layout

    def extract_body(
        self,
        *,
        rendered: bytes,
        manifest_bytes: bytes,
        item_id: str,
    ) -> bytes:
        if not isinstance(rendered, bytes) or not isinstance(manifest_bytes, bytes):
            raise TypeError("rendered and manifest_bytes must be bytes")
        try:
            if str(UUID(item_id)) != item_id:
                raise ValueError
            document = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProjectionManifestUntrustedError(
                "The projection manifest is invalid."
            ) from exc
        if not isinstance(document, dict):
            raise ProjectionManifestUntrustedError("The projection manifest is invalid.")
        canonical_json = _canonical_json(document).decode("utf-8")
        if canonical_json.encode("utf-8") + b"\n" != manifest_bytes:
            raise ProjectionManifestUntrustedError(
                "The projection manifest is not canonical."
            )
        manifest_sha = _sha(manifest_bytes)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                ledger = connection.execute(
                    """SELECT manifest_json,projection_root_identity_sha256
                         FROM projection_manifest_v2_ledger
                        WHERE manifest_sha256=?""",
                    (manifest_sha,),
                ).fetchone()
        finally:
            connection.close()
        if ledger is None or str(ledger["manifest_json"]) != canonical_json:
            raise ProjectionManifestUntrustedError(
                "The projection manifest is not anchored in the canonical ledger."
            )
        root_identity = projection_root_identity_sha256(self._layout.projection_root)
        if (
            str(ledger["projection_root_identity_sha256"]) != root_identity
            or document.get("projection_root_identity_sha256") != root_identity
        ):
            raise ProjectionManifestUntrustedError(
                "The projection root identity does not match the ledger."
            )
        items = document.get("items")
        if not isinstance(items, list):
            raise ProjectionManifestUntrustedError("The projection manifest is invalid.")
        selected = [row for row in items if isinstance(row, dict) and row.get("item_id") == item_id]
        if len(selected) != 1:
            raise ProjectionManifestUntrustedError(
                "The projection manifest does not bind exactly one item."
            )
        row = selected[0]
        size = row.get("projected_body_size_bytes")
        prefix_sha = row.get("projected_body_sha256")
        rendered_size = row.get("rendered_size_bytes")
        rendered_sha = row.get("rendered_sha256")
        metadata = row.get("footer_metadata")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(prefix_sha, str)
            or rendered_size != len(rendered)
            or rendered_sha != _sha(rendered)
            or not isinstance(metadata, dict)
        ):
            raise ProjectionManifestUntrustedError(
                "The rendered page does not match its trusted manifest."
            )
        prefix = rendered[:size]
        if _sha(prefix) != prefix_sha:
            raise ProjectionManifestUntrustedError(
                "The canonical prefix does not match its trusted manifest."
            )
        try:
            expected = render_temporal_page(
                prefix,
                TemporalFooterMetadata(
                    registered_at_ms=metadata["registered_at_ms"],
                    content_modified_at_ms=metadata["content_modified_at_ms"],
                    revision_no=metadata["revision_no"],
                    source_modified_at=metadata["source_modified_at"],
                    information_effective_at=metadata["information_effective_at"],
                    last_verified_at_ms=metadata["last_verified_at_ms"],
                ),
            )
        except (KeyError, TypeError, ProjectionMaterializationError) as exc:
            raise ProjectionManifestUntrustedError(
                "The trusted footer metadata is invalid."
            ) from exc
        if expected != rendered:
            raise ProjectionManifestUntrustedError(
                "The deterministic footer suffix does not match."
            )
        return prefix


__all__ = [
    "FOOTER_SCHEMA_VERSION",
    "GRAPH_GENERATION",
    "ProjectionManifestUntrustedError",
    "TEMPORAL_MANIFEST_SCHEMA_VERSION",
    "TEMPORAL_PROJECTION_NAME",
    "TemporalFooterMetadata",
    "TemporalProjectionMaterializer",
    "TemporalProjectionResult",
    "TrustedTemporalProjectionImporter",
    "projection_root_identity_sha256",
    "render_temporal_page",
]
