"""Offline-only NP2C duplicate, Trash, and restore source-v3 adapter.

This module is intentionally absent from application startup, routes, workers,
and CLIs.  It accepts one caller-owned exact SQLite connection and one exact
owner workspace root.  New effects require explicit source quiescence;
committed response-loss replay is checked before page, permission, or file I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_consumer_contract import (
    inspect_legacy_change_consumer_contract,
)
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameManualAuditRequired,
    LegacyPageRenameOperationConflict,
    LegacyPageRenameSourceConflict,
)
from code_assist_v1.notion_legacy_page_structural import (
    LegacyPageStructuralSourceAdapter,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    LIFECYCLE_CONSUMER,
    LIFECYCLE_FILE_MANIFEST_DOMAIN,
    LIFECYCLE_MANIFEST_DOMAIN,
    LIFECYCLE_PRECONDITION_DOMAIN,
    LIFECYCLE_RECEIPT_DOMAIN,
    LIFECYCLE_RESULT_CONTRACTS,
    LegacyPageLifecycleContractError,
    LegacyPageLifecycleEffect,
    LegacyPageLifecycleObjectMapping,
    LegacyPageLifecycleReceipt,
    MAX_LIFECYCLE_BLOCKS,
    MAX_LIFECYCLE_DEPTH,
    MAX_LIFECYCLE_EFFECTS,
    MAX_LIFECYCLE_FILES,
    MAX_LIFECYCLE_FILE_BYTES,
    MAX_LIFECYCLE_METADATA_BYTES,
    MAX_LIFECYCLE_PARENT_BLOCKS,
    MAX_LIFECYCLE_SIBLINGS,
    MAX_LIFECYCLE_SUBTREE,
    canonical_json,
    derive_duplicate_object_id,
    derive_lifecycle_source_operation_id,
    digest,
    lifecycle_request_sha256,
    normalize_action,
    normalize_name,
    normalize_object_id,
    normalize_operation_id,
    normalize_sha256,
    validate_lifecycle_receipt,
)


_REFERENCE_KEYS = frozenset(
    {
        "page_id",
        "source_page_id",
        "block_id",
        "source_block_id",
        "file_id",
        "source_file_id",
    }
)
_KNOWN_ID_RE = re.compile(r"^(?:p|b|f)_[0-9a-f]{32}$")
_SAFE_NAME_RE = re.compile(r"[\x00-\x1f\x7f<>:\"|?*\\/]+")


class LegacyPageLifecycleSourceConflict(LegacyPageRenameSourceConflict):
    """The exact source precondition, lifecycle, or placement changed."""


class LegacyPageLifecycleBoundsExceeded(Exception):
    """The requested lifecycle mutation exceeds a frozen source bound."""


class LegacyPageLifecycleUnsafePath(Exception):
    """A source, staged, or result file path is not exact and contained."""


class LegacyPageLifecycleManualAuditRequired(LegacyPageRenameManualAuditRequired):
    """A file or source commit outcome cannot be proven automatically."""


FaultHook = Callable[[str], None]


@dataclass(frozen=True)
class _FilePlan:
    source_file_id: str
    result_file_id: str
    source_page_id: str
    result_page_id: str
    source_relative_path: str
    result_relative_path: str
    original_name: str
    stored_name: str
    mime_type: str
    uploader_user_id: str
    byte_size: int
    content_sha256: str
    source_identity: tuple[int, int, int, int, int]

    def private_descriptor(self) -> dict[str, Any]:
        return {
            "source_file_id": self.source_file_id,
            "result_file_id": self.result_file_id,
            "source_page_id": self.source_page_id,
            "result_page_id": self.result_page_id,
            "source_relative_path": self.source_relative_path,
            "result_relative_path": self.result_relative_path,
            "original_name": self.original_name,
            "stored_name": self.stored_name,
            "mime_type": self.mime_type,
            "uploader_user_id": self.uploader_user_id,
            "byte_size": self.byte_size,
            "content_sha256": self.content_sha256,
            "source_identity": list(self.source_identity),
        }


@dataclass
class _PreparedLifecycle:
    action: str
    root_source_page_id: str
    result_root_source_page_id: str
    source_operation_id: str
    source_request_sha256: str
    payload: dict[str, Any]
    page_rows: dict[str, dict[str, Any]]
    page_states: dict[str, dict[str, Any]]
    blocks_by_page: dict[str, list[dict[str, Any]]]
    mappings: tuple[LegacyPageLifecycleObjectMapping, ...]
    file_plans: tuple[_FilePlan, ...]
    active_scope_before: tuple[str, ...]
    transitioned_page_ids: tuple[str, ...]
    trash_receipt: LegacyPageLifecycleReceipt | None
    destination_parent_id: str
    destination_ordinal: int
    placement_mode: str


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(junction())


def _safe_name(value: object) -> str:
    try:
        name = PurePosixPath(str(value or "file").replace("\\", "/")).name
        name = _SAFE_NAME_RE.sub("-", name).strip(". ")[:120] or "file"
        name.encode("utf-8")
        return name
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyPageLifecycleContractError(
            "legacy duplicate file metadata is invalid"
        ) from exc


def _utf8_len(value: object) -> int:
    try:
        return len(str(value or "").encode("utf-8"))
    except UnicodeError as exc:
        raise LegacyPageLifecycleContractError(
            "legacy lifecycle source text is invalid"
        ) from exc


def _file_digest(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LIFECYCLE_FILE_BYTES:
                raise LegacyPageLifecycleBoundsExceeded(
                    "legacy duplicate file exceeds the adapter bound"
                )
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest(), total


def _remap_reference_value(
    value: Any,
    *,
    page_map: dict[str, str],
    block_map: dict[str, str],
    file_map: dict[str, str],
    key_hint: str = "",
) -> Any:
    mappings = {**page_map, **block_map, **file_map}
    if isinstance(value, list):
        return [
            _remap_reference_value(
                item,
                page_map=page_map,
                block_map=block_map,
                file_map=file_map,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): _remap_reference_value(
                item,
                page_map=page_map,
                block_map=block_map,
                file_map=file_map,
                key_hint=str(key),
            )
            for key, item in value.items()
        }
    if isinstance(value, str):
        if value in mappings:
            return mappings[value]
        if key_hint in _REFERENCE_KEYS and value:
            raise LegacyPageLifecycleSourceConflict(
                "external legacy reference is not supported by this adapter"
            )
        if _KNOWN_ID_RE.fullmatch(value) is not None:
            raise LegacyPageLifecycleSourceConflict(
                "external legacy reference is not supported by this adapter"
            )
    return value


def _remap_reference_text(
    value: object,
    *,
    page_map: dict[str, str],
    block_map: dict[str, str],
    file_map: dict[str, str],
) -> str:
    text = str(value or "")
    mappings = {**page_map, **block_map, **file_map}
    for match in tuple(_KNOWN_ID_RE.finditer(text)):
        source_id = match.group(0)
        if source_id not in mappings:
            raise LegacyPageLifecycleSourceConflict(
                "external legacy reference is not supported by this adapter"
            )
    for source_id in sorted(mappings, key=len, reverse=True):
        text = text.replace(source_id, mappings[source_id])
    return text


class LegacyPageDuplicateTrashRestoreSourceAdapter(LegacyPageStructuralSourceAdapter):
    """One exact owner shard with replay-stable lifecycle source effects."""

    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        source_owner_id: str,
        expected_source_db_path: str | os.PathLike[str],
        expected_source_workspace_root: str | os.PathLike[str],
        trusted_clock: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(
            con,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
            trusted_clock=trusted_clock,
        )
        root = Path(expected_source_workspace_root)
        if not root.is_absolute() or _is_link(root):
            raise LegacyPageLifecycleUnsafePath("exact source workspace root required")
        try:
            self._workspace_root = root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LegacyPageLifecycleUnsafePath(
                "exact source workspace root required"
            ) from exc
        if not self._workspace_root.is_dir() or _is_link(self._workspace_root):
            raise LegacyPageLifecycleUnsafePath("exact source workspace root required")
        if self._path != (self._workspace_root / "notion.db").resolve(strict=True):
            raise LegacyPageLifecycleUnsafePath(
                "source database is outside the exact workspace root"
            )
        self._files_root = self._workspace_root / "files"
        if not self._files_root.exists():
            raise LegacyPageLifecycleUnsafePath(
                "exact source files root required"
            )
        self._assert_exact_directory(self._files_root)
        self._staging_root = self._workspace_root / ".np2c-lifecycle-staging"

    def _assert_exact_directory(self, path: Path) -> Path:
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_dir() or _is_link(path) or _is_link(resolved):
                raise LegacyPageLifecycleUnsafePath(
                    "legacy lifecycle directory is unsafe"
                )
            resolved.relative_to(self._workspace_root)
            current = resolved
            while current != self._workspace_root:
                if _is_link(current):
                    raise LegacyPageLifecycleUnsafePath(
                        "legacy lifecycle directory is unsafe"
                    )
                current = current.parent
            return resolved
        except LegacyPageLifecycleUnsafePath:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise LegacyPageLifecycleUnsafePath(
                "legacy lifecycle directory is unsafe"
            ) from exc

    def _ensure_exact_directory(self, path: Path) -> Path:
        try:
            relative = path.relative_to(self._workspace_root)
        except ValueError as exc:
            raise LegacyPageLifecycleUnsafePath(
                "legacy lifecycle directory is outside the workspace root"
            ) from exc
        current = self._workspace_root
        for component in relative.parts:
            if component in {"", ".", ".."}:
                raise LegacyPageLifecycleUnsafePath(
                    "legacy lifecycle directory is unsafe"
                )
            current = current / component
            if current.exists():
                self._assert_exact_directory(current)
            else:
                current.mkdir(mode=0o700)
                self._assert_exact_directory(current)
        return self._assert_exact_directory(path)

    def _contained_file(
        self, relative_path: object, *, must_exist: bool
    ) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise LegacyPageLifecycleUnsafePath("valid contained file path required")
        normalized = relative_path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or normalized != pure.as_posix()
        ):
            raise LegacyPageLifecycleUnsafePath("valid contained file path required")
        candidate = self._workspace_root.joinpath(*pure.parts)
        try:
            candidate.parent.resolve(strict=True).relative_to(self._workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise LegacyPageLifecycleUnsafePath(
                "legacy lifecycle file is outside the workspace root"
            ) from exc
        self._assert_exact_directory(candidate.parent)
        if must_exist:
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self._workspace_root)
                info = candidate.lstat()
                if (
                    _is_link(candidate)
                    or not stat.S_ISREG(info.st_mode)
                    or int(info.st_nlink) != 1
                ):
                    raise LegacyPageLifecycleUnsafePath(
                        "legacy lifecycle file identity is unsafe"
                    )
                if resolved != candidate.absolute():
                    raise LegacyPageLifecycleUnsafePath(
                        "legacy lifecycle file identity is unsafe"
                    )
            except LegacyPageLifecycleUnsafePath:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise LegacyPageLifecycleUnsafePath(
                    "legacy lifecycle file is unavailable"
                ) from exc
        return candidate

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
                raise LegacyPageLifecycleUnsafePath(
                    "legacy lifecycle file identity is unsafe"
                )
            return (
                int(info.st_dev),
                int(info.st_ino),
                int(info.st_nlink),
                int(info.st_size),
                int(info.st_mtime_ns),
            )
        except LegacyPageLifecycleUnsafePath:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise LegacyPageLifecycleUnsafePath(
                "legacy lifecycle file identity is unavailable"
            ) from exc

    def _lifecycle_scope(self, parent_page_id: str) -> tuple[str, ...]:
        if parent_page_id:
            rows = self._con.execute(
                "SELECT id FROM pages WHERE owner_user_id=? AND archived=0 "
                "AND parent_id=? ORDER BY position,updated_at DESC,id LIMIT ?",
                (self._owner, parent_page_id, MAX_LIFECYCLE_SIBLINGS + 1),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT id FROM pages WHERE owner_user_id=? AND archived=0 "
                "AND parent_id IS NULL ORDER BY position,updated_at DESC,id LIMIT ?",
                (self._owner, MAX_LIFECYCLE_SIBLINGS + 1),
            ).fetchall()
        if len(rows) > MAX_LIFECYCLE_SIBLINGS:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle sibling scope exceeds the adapter bound"
            )
        return tuple(str(row[0]) for row in rows)

    def _active_subtree(self, root_page_id: str) -> tuple[str, ...]:
        rows = self._con.execute(
            "WITH RECURSIVE tree(id,parent_id,position,depth) AS ("
            "SELECT id,parent_id,position,0 FROM pages "
            "WHERE id=? AND owner_user_id=? AND archived=0 "
            "UNION ALL SELECT p.id,p.parent_id,p.position,tree.depth+1 "
            "FROM pages p JOIN tree ON p.parent_id=tree.id "
            "WHERE p.owner_user_id=? AND p.archived=0 AND tree.depth<?) "
            "SELECT id,parent_id,position,depth FROM tree "
            "ORDER BY depth,parent_id,position,id LIMIT ?",
            (
                root_page_id,
                self._owner,
                self._owner,
                MAX_LIFECYCLE_DEPTH,
                MAX_LIFECYCLE_SUBTREE + 1,
            ),
        ).fetchall()
        if not rows:
            raise LegacyPageLifecycleSourceConflict(
                "legacy lifecycle source root is unavailable"
            )
        if len(rows) > MAX_LIFECYCLE_SUBTREE:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle subtree exceeds the adapter bound"
            )
        if any(int(row["depth"]) >= MAX_LIFECYCLE_DEPTH for row in rows):
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle subtree depth exceeds the adapter bound"
            )
        return tuple(str(row["id"]) for row in rows)

    def _page_state(
        self,
        page_id: str,
        *,
        scope_cache: dict[str, tuple[str, ...]] | None = None,
    ) -> dict[str, Any]:
        page = self._con.execute(
            "SELECT * FROM pages WHERE id=? AND owner_user_id=?",
            (page_id, self._owner),
        ).fetchone()
        watermark = self._con.execute(
            "SELECT change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?",
            (page_id,),
        ).fetchone()
        incarnation = self._con.execute(
            "SELECT incarnation_id,incarnation_generation,retired_at "
            "FROM notion_page_incarnation_history WHERE page_id=? "
            "ORDER BY incarnation_generation DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        if (
            page is None
            or watermark is None
            or incarnation is None
            or incarnation["retired_at"] is not None
        ):
            raise LegacyPageLifecycleSourceConflict(
                "legacy lifecycle page generation changed"
            )
        lifecycle = "trashed" if int(page["archived"]) == 1 else "active"
        expected_source_exists = 1 if lifecycle == "active" else 0
        if int(watermark["source_exists"]) != expected_source_exists:
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle watermark is invalid"
            )
        parent = str(page["parent_id"] or "")
        ordinal: int | None = None
        if lifecycle == "active":
            if scope_cache is None:
                scope = self._lifecycle_scope(parent)
            else:
                scope = scope_cache.get(parent)
                if scope is None:
                    scope = self._lifecycle_scope(parent)
                    scope_cache[parent] = scope
            try:
                ordinal = scope.index(page_id) + 1
            except ValueError as exc:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy lifecycle sibling scope changed"
                ) from exc
        revision = page["revision"]
        updated_at = page["updated_at"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or isinstance(updated_at, bool)
            or not isinstance(updated_at, int)
            or updated_at < 0
        ):
            raise LegacyPageLifecycleContractError(
                "atomic source revision capability is required"
            )
        return {
            "page": dict(page),
            "source_page_id": page_id,
            "lifecycle": lifecycle,
            "parent_source_page_id": parent,
            "sibling_ordinal": ordinal,
            "revision": int(revision),
            "updated_at": int(updated_at),
            "change_generation": int(watermark["change_generation"]),
            "change_mask": int(watermark["change_mask"]),
            "incarnation_id": str(incarnation["incarnation_id"]),
            "incarnation_generation": int(incarnation["incarnation_generation"]),
        }

    @staticmethod
    def _public_state_descriptor(state: dict[str, Any]) -> dict[str, Any]:
        return {
            key: state[key]
            for key in (
                "source_page_id",
                "lifecycle",
                "parent_source_page_id",
                "sibling_ordinal",
                "revision",
                "updated_at",
                "change_generation",
                "change_mask",
                "incarnation_id",
                "incarnation_generation",
            )
        }

    def _source_fence(self) -> dict[str, int]:
        state = self._con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        if state is None:
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle tracking state is unavailable"
            )
        device, inode, links = self._assert_identity()
        return {
            "source_device": device,
            "source_inode": inode,
            "source_link_count": links,
            "source_epoch": int(state["source_epoch"]),
            "full_sweep_generation": int(state["full_sweep_generation"]),
        }

    def _read_blocks(self, page_ids: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        count = 0
        for page_id in page_ids:
            rows = self._con.execute(
                "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id LIMIT ?",
                (page_id, MAX_LIFECYCLE_BLOCKS + 1),
            ).fetchall()
            count += len(rows)
            if len(rows) > MAX_LIFECYCLE_BLOCKS or count > MAX_LIFECYCLE_BLOCKS:
                raise LegacyPageLifecycleBoundsExceeded(
                    "legacy duplicate block set exceeds the adapter bound"
                )
            result[page_id] = [dict(row) for row in rows]
        return result

    def _read_files(
        self,
        page_ids: tuple[str, ...],
        *,
        page_map: dict[str, str],
        file_map: dict[str, str],
    ) -> tuple[_FilePlan, ...]:
        plans: list[_FilePlan] = []
        total_bytes = 0
        seen_identities: set[tuple[int, int]] = set()
        for page_id in page_ids:
            rows = self._con.execute(
                "SELECT * FROM files WHERE page_id=? ORDER BY created_at,id LIMIT ?",
                (page_id, MAX_LIFECYCLE_FILES + 1),
            ).fetchall()
            if len(rows) > MAX_LIFECYCLE_FILES:
                raise LegacyPageLifecycleBoundsExceeded(
                    "legacy duplicate file set exceeds the adapter bound"
                )
            for raw in rows:
                if len(plans) >= MAX_LIFECYCLE_FILES:
                    raise LegacyPageLifecycleBoundsExceeded(
                        "legacy duplicate file set exceeds the adapter bound"
                    )
                row = dict(raw)
                source_file_id = normalize_object_id(
                    row["id"], field="source_file_id"
                )
                if source_file_id not in file_map:
                    raise LegacyPageLifecycleContractError(
                        "legacy duplicate file mapping is unavailable"
                    )
                source_path = self._contained_file(row["rel_path"], must_exist=True)
                identity = self._file_identity(source_path)
                identity_key = (identity[0], identity[1])
                if identity_key in seen_identities:
                    raise LegacyPageLifecycleUnsafePath(
                        "legacy duplicate source files share one identity"
                    )
                seen_identities.add(identity_key)
                content_sha256, size = _file_digest(source_path)
                declared_size = row["size"]
                if (
                    isinstance(declared_size, bool)
                    or not isinstance(declared_size, int)
                    or declared_size != size
                ):
                    raise LegacyPageLifecycleSourceConflict(
                        "legacy duplicate source file size changed"
                    )
                total_bytes += size
                if total_bytes > MAX_LIFECYCLE_FILE_BYTES:
                    raise LegacyPageLifecycleBoundsExceeded(
                        "legacy duplicate aggregate file bytes exceed the adapter bound"
                    )
                result_file_id = file_map[source_file_id]
                result_page_id = page_map[page_id]
                safe_name = _safe_name(row.get("original_name") or row.get("stored_name"))
                stored_name = f"{result_file_id}-{safe_name}"
                result_relative_path = PurePosixPath(
                    "files", result_page_id, stored_name
                ).as_posix()
                plans.append(
                    _FilePlan(
                        source_file_id=source_file_id,
                        result_file_id=result_file_id,
                        source_page_id=page_id,
                        result_page_id=result_page_id,
                        source_relative_path=str(row["rel_path"]),
                        result_relative_path=result_relative_path,
                        original_name=safe_name,
                        stored_name=stored_name,
                        mime_type=str(row.get("mime_type") or "application/octet-stream"),
                        uploader_user_id=str(row.get("uploader_user_id") or self._owner),
                        byte_size=size,
                        content_sha256=content_sha256,
                        source_identity=identity,
                    )
                )
        return tuple(plans)

    def _raw_file_ids(self, page_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for page_id in page_ids:
            rows = self._con.execute(
                "SELECT id FROM files WHERE page_id=? ORDER BY created_at,id LIMIT ?",
                (page_id, MAX_LIFECYCLE_FILES + 1),
            ).fetchall()
            values.extend((page_id, str(row["id"])) for row in rows)
            if len(values) > MAX_LIFECYCLE_FILES:
                raise LegacyPageLifecycleBoundsExceeded(
                    "legacy duplicate file set exceeds the adapter bound"
                )
        return tuple(values)

    def _duplicate_maps(
        self,
        *,
        actor: str,
        source_operation_id: str,
        root_page_id: str,
        root_state: dict[str, Any],
        page_ids: tuple[str, ...],
        blocks_by_page: dict[str, list[dict[str, Any]]],
        parent_reference_blocks: tuple[dict[str, Any], ...],
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        dict[str, str],
        dict[str, str],
    ]:
        common = {
            "source_operation_id": source_operation_id,
            "source_owner_subject_id": self._owner,
            "mutation_actor_subject_id": actor,
            "root_source_page_id": root_page_id,
            "root_incarnation_id": root_state["incarnation_id"],
            "root_incarnation_generation": root_state["incarnation_generation"],
        }
        page_map = {
            page_id: derive_duplicate_object_id(
                kind="page", prefix="p_", source_object_id=page_id, **common
            )
            for page_id in page_ids
        }
        block_map: dict[str, str] = {}
        for page_id in page_ids:
            for row in blocks_by_page.get(page_id, []):
                source_id = normalize_object_id(row["id"], field="source_block_id")
                block_map[source_id] = derive_duplicate_object_id(
                    kind="block", prefix="b_", source_object_id=source_id, **common
                )
        file_map = {
            source_file_id: derive_duplicate_object_id(
                kind="file", prefix="f_", source_object_id=source_file_id, **common
            )
            for _page_id, source_file_id in self._raw_file_ids(page_ids)
        }
        parent_reference_map: dict[str, str] = {}
        for row in parent_reference_blocks:
            source_id = normalize_object_id(row["id"], field="source_block_id")
            parent_reference_map[source_id] = derive_duplicate_object_id(
                kind="parent_reference_block",
                prefix="b_",
                source_object_id=source_id,
                **common,
            )
        all_result_ids = [
            *page_map.values(),
            *block_map.values(),
            *file_map.values(),
            *parent_reference_map.values(),
        ]
        if len(all_result_ids) != len(set(all_result_ids)):
            raise LegacyPageLifecycleContractError(
                "legacy duplicate deterministic mapping collided"
            )
        return page_map, block_map, file_map, parent_reference_map

    @staticmethod
    def _decode_json_object(value: object, *, field: str) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageLifecycleContractError(
                f"legacy duplicate {field} is invalid"
            ) from exc
        if not isinstance(parsed, dict):
            raise LegacyPageLifecycleContractError(
                f"legacy duplicate {field} is invalid"
            )
        return parsed

    def _parent_reference_blocks(
        self, parent_page_id: str, root_page_id: str
    ) -> tuple[dict[str, Any], ...]:
        if not parent_page_id:
            return ()
        rows = self._con.execute(
            "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id LIMIT ?",
            (parent_page_id, MAX_LIFECYCLE_PARENT_BLOCKS + 1),
        ).fetchall()
        if len(rows) > MAX_LIFECYCLE_PARENT_BLOCKS:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle parent blocks exceed the adapter bound"
            )
        references: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            props = self._decode_json_object(row.get("props_json"), field="block props")
            if (
                str(row.get("block_type") or "") == "child_page"
                and str(props.get("page_id") or "") == root_page_id
            ):
                references.append(row)
        return tuple(references)

    def _bounded_parent_blocks(self, parent_page_id: str) -> tuple[dict[str, Any], ...]:
        if not parent_page_id:
            return ()
        rows = self._con.execute(
            "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id LIMIT ?",
            (parent_page_id, MAX_LIFECYCLE_PARENT_BLOCKS + 1),
        ).fetchall()
        if len(rows) > MAX_LIFECYCLE_PARENT_BLOCKS:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle parent blocks exceed the adapter bound"
            )
        return tuple(dict(row) for row in rows)

    def _assert_duplicate_references(
        self,
        *,
        page_ids: tuple[str, ...],
        page_rows: dict[str, dict[str, Any]],
        blocks_by_page: dict[str, list[dict[str, Any]]],
        page_map: dict[str, str],
        block_map: dict[str, str],
        file_map: dict[str, str],
    ) -> None:
        for page_id in page_ids:
            row = page_rows[page_id]
            settings = self._decode_json_object(
                row.get("settings_json"), field="page settings"
            )
            _remap_reference_value(
                settings,
                page_map=page_map,
                block_map=block_map,
                file_map=file_map,
            )
            for field in ("icon", "cover"):
                _remap_reference_text(
                    row.get(field),
                    page_map=page_map,
                    block_map=block_map,
                    file_map=file_map,
                )
            for block in blocks_by_page.get(page_id, []):
                props = self._decode_json_object(
                    block.get("props_json"), field="block props"
                )
                _remap_reference_value(
                    props,
                    page_map=page_map,
                    block_map=block_map,
                    file_map=file_map,
                )
                _remap_reference_text(
                    block.get("content"),
                    page_map=page_map,
                    block_map=block_map,
                    file_map=file_map,
                )

    def _assert_duplicate_destinations_absent(
        self,
        *,
        page_map: dict[str, str],
        block_map: dict[str, str],
        file_map: dict[str, str],
        parent_reference_map: dict[str, str],
    ) -> None:
        for table, values in (
            ("pages", tuple(page_map.values())),
            ("blocks", tuple([*block_map.values(), *parent_reference_map.values()])),
            ("files", tuple(file_map.values())),
        ):
            if not values:
                continue
            for offset in range(0, len(values), 500):
                batch = values[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                if self._con.execute(
                    f"SELECT 1 FROM {table} WHERE id IN ({placeholders}) LIMIT 1",
                    batch,
                ).fetchone() is not None:
                    raise LegacyPageLifecycleSourceConflict(
                        "legacy duplicate deterministic destination already exists"
                    )
        for page_id in page_map.values():
            if self._con.execute(
                "SELECT 1 FROM notion_page_incarnation_history WHERE page_id=? LIMIT 1",
                (page_id,),
            ).fetchone() is not None:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy duplicate deterministic destination already exists"
                )

    def _bounded_private_snapshot(self, value: dict[str, Any]) -> str:
        encoded = canonical_json(value).encode("utf-8")
        if len(encoded) > MAX_LIFECYCLE_METADATA_BYTES:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle metadata exceeds the adapter bound"
            )
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _duplicate_precondition(
        self,
        *,
        actor: str,
        root_page_id: str,
        source_operation_id: str,
    ) -> _PreparedLifecycle:
        page_ids = self._active_subtree(root_page_id)
        scope_cache: dict[str, tuple[str, ...]] = {}
        page_states = {
            page_id: self._page_state(page_id, scope_cache=scope_cache)
            for page_id in page_ids
        }
        root_state = page_states[root_page_id]
        if any(self._settings_locked(page_states[page_id]["page"]) for page_id in page_ids):
            raise PermissionError("owner source page is locked")
        root_parent = str(root_state["parent_source_page_id"])
        active_scope = self._lifecycle_scope(root_parent)
        if root_page_id not in active_scope:
            raise LegacyPageLifecycleSourceConflict(
                "legacy duplicate sibling scope changed"
            )
        context_ids = set(active_scope)
        if root_parent:
            context_ids.add(root_parent)
        for page_id in sorted(context_ids):
            page_states.setdefault(
                page_id, self._page_state(page_id, scope_cache=scope_cache)
            )
        page_rows = {page_id: page_states[page_id]["page"] for page_id in page_ids}
        blocks_by_page = self._read_blocks(page_ids)
        parent_blocks = self._bounded_parent_blocks(root_parent)
        parent_references = tuple(
            row
            for row in parent_blocks
            if str(row.get("block_type") or "") == "child_page"
            and str(
                self._decode_json_object(
                    row.get("props_json"), field="block props"
                ).get("page_id")
                or ""
            )
            == root_page_id
        )
        page_map, block_map, file_map, parent_reference_map = self._duplicate_maps(
            actor=actor,
            source_operation_id=source_operation_id,
            root_page_id=root_page_id,
            root_state=root_state,
            page_ids=page_ids,
            blocks_by_page=blocks_by_page,
            parent_reference_blocks=parent_references,
        )
        self._assert_duplicate_references(
            page_ids=page_ids,
            page_rows=page_rows,
            blocks_by_page=blocks_by_page,
            page_map=page_map,
            block_map=block_map,
            file_map=file_map,
        )
        self._assert_duplicate_destinations_absent(
            page_map=page_map,
            block_map=block_map,
            file_map=file_map,
            parent_reference_map=parent_reference_map,
        )
        file_plans = self._read_files(
            page_ids, page_map=page_map, file_map=file_map
        )
        mappings: list[LegacyPageLifecycleObjectMapping] = []
        for source_page_id, result_page_id in page_map.items():
            mappings.append(
                LegacyPageLifecycleObjectMapping(
                    kind="page",
                    source_object_id=source_page_id,
                    result_object_id=result_page_id,
                    source_page_id=source_page_id,
                    result_page_id=result_page_id,
                    byte_size=0,
                    content_sha256="",
                )
            )
        for source_page_id, rows in blocks_by_page.items():
            for row in rows:
                source_id = str(row["id"])
                mappings.append(
                    LegacyPageLifecycleObjectMapping(
                        kind="block",
                        source_object_id=source_id,
                        result_object_id=block_map[source_id],
                        source_page_id=source_page_id,
                        result_page_id=page_map[source_page_id],
                        byte_size=0,
                        content_sha256="",
                    )
                )
        for plan in file_plans:
            mappings.append(
                LegacyPageLifecycleObjectMapping(
                    kind="file",
                    source_object_id=plan.source_file_id,
                    result_object_id=plan.result_file_id,
                    source_page_id=plan.source_page_id,
                    result_page_id=plan.result_page_id,
                    byte_size=plan.byte_size,
                    content_sha256=plan.content_sha256,
                )
            )
        for row in parent_references:
            source_id = str(row["id"])
            mappings.append(
                LegacyPageLifecycleObjectMapping(
                    kind="parent_reference_block",
                    source_object_id=source_id,
                    result_object_id=parent_reference_map[source_id],
                    source_page_id=root_parent,
                    result_page_id=root_parent,
                    byte_size=0,
                    content_sha256="",
                )
            )
        mappings.sort(
            key=lambda item: (item.kind, item.source_object_id, item.result_object_id)
        )
        private_pages = []
        for page_id in page_ids:
            row = page_rows[page_id]
            private_pages.append(
                {
                    "state": self._public_state_descriptor(page_states[page_id]),
                    "title": str(row.get("title") or "Untitled"),
                    "icon": str(row.get("icon") or ""),
                    "cover": str(row.get("cover") or ""),
                    "settings_json": str(row.get("settings_json") or "{}"),
                    "position": int(row.get("position") or 0),
                    "created_at": int(row.get("created_at") or 0),
                    "blocks": [
                        {
                            key: block.get(key)
                            for key in (
                                "id",
                                "position",
                                "block_type",
                                "content",
                                "props_json",
                                "created_at",
                                "updated_at",
                            )
                        }
                        for block in blocks_by_page[page_id]
                    ],
                }
            )
        private_snapshot = {
            "pages": private_pages,
            "files": [plan.private_descriptor() for plan in file_plans],
            "parent_reference_blocks": [
                {
                    key: row.get(key)
                    for key in (
                        "id",
                        "page_id",
                        "position",
                        "block_type",
                        "content",
                        "props_json",
                        "created_at",
                        "updated_at",
                    )
                }
                for row in parent_references
            ],
            "parent_blocks": [
                {
                    key: row.get(key)
                    for key in (
                        "id",
                        "page_id",
                        "position",
                        "block_type",
                        "content",
                        "props_json",
                        "created_at",
                        "updated_at",
                    )
                }
                for row in parent_blocks
            ],
        }
        fence = self._source_fence()
        result_root = page_map[root_page_id]
        payload = {
            "domain": LIFECYCLE_PRECONDITION_DOMAIN,
            "action": "duplicate",
            "source_owner_subject_id": self._owner,
            "root_source_page_id": root_page_id,
            "result_root_source_page_id": result_root,
            "source_operation_id": source_operation_id,
            "source_fence": fence,
            "root_parent_source_page_id": root_parent,
            "active_scope": list(active_scope),
            "source_pages": [
                self._public_state_descriptor(page_states[page_id])
                for page_id in page_ids
            ],
            "context_pages": [
                self._public_state_descriptor(page_states[page_id])
                for page_id in sorted(page_states)
            ],
            "mappings": [mapping.descriptor() for mapping in mappings],
            "private_snapshot_sha256": self._bounded_private_snapshot(private_snapshot),
        }
        destination_ordinal = active_scope.index(root_page_id) + 2
        if len(mappings) > (
            MAX_LIFECYCLE_SUBTREE
            + MAX_LIFECYCLE_BLOCKS
            + MAX_LIFECYCLE_FILES
            + MAX_LIFECYCLE_PARENT_BLOCKS
        ):
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy duplicate mapping set exceeds the adapter bound"
            )
        return _PreparedLifecycle(
            action="duplicate",
            root_source_page_id=root_page_id,
            result_root_source_page_id=result_root,
            source_operation_id=source_operation_id,
            source_request_sha256="",
            payload=payload,
            page_rows=page_rows,
            page_states=page_states,
            blocks_by_page=blocks_by_page,
            mappings=tuple(mappings),
            file_plans=file_plans,
            active_scope_before=active_scope,
            transitioned_page_ids=page_ids,
            trash_receipt=None,
            destination_parent_id=root_parent,
            destination_ordinal=destination_ordinal,
            placement_mode="same_parent_after_source",
        )

    def _trash_precondition(
        self, *, root_page_id: str, source_operation_id: str
    ) -> _PreparedLifecycle:
        cohort = self._active_subtree(root_page_id)
        page_states = {page_id: self._page_state(page_id) for page_id in cohort}
        if any(self._settings_locked(page_states[page_id]["page"]) for page_id in cohort):
            raise PermissionError("owner source page is locked")
        root_parent = str(page_states[root_page_id]["parent_source_page_id"])
        scope = self._lifecycle_scope(root_parent)
        context_ids = set(cohort) | set(scope)
        if root_parent:
            context_ids.add(root_parent)
        for page_id in sorted(context_ids):
            page_states.setdefault(page_id, self._page_state(page_id))
        fence = self._source_fence()
        payload = {
            "domain": LIFECYCLE_PRECONDITION_DOMAIN,
            "action": "trash",
            "source_owner_subject_id": self._owner,
            "root_source_page_id": root_page_id,
            "result_root_source_page_id": root_page_id,
            "source_operation_id": source_operation_id,
            "source_fence": fence,
            "cohort": list(cohort),
            "root_parent_source_page_id": root_parent,
            "active_scope": list(scope),
            "pages": [
                self._public_state_descriptor(page_states[page_id])
                for page_id in sorted(page_states)
            ],
        }
        return _PreparedLifecycle(
            action="trash",
            root_source_page_id=root_page_id,
            result_root_source_page_id=root_page_id,
            source_operation_id=source_operation_id,
            source_request_sha256="",
            payload=payload,
            page_rows={
                page_id: state["page"] for page_id, state in page_states.items()
            },
            page_states=page_states,
            blocks_by_page={},
            mappings=(),
            file_plans=(),
            active_scope_before=scope,
            transitioned_page_ids=cohort,
            trash_receipt=None,
            destination_parent_id=root_parent,
            destination_ordinal=page_states[root_page_id]["sibling_ordinal"],
            placement_mode="",
        )

    def _load_lifecycle_receipt(
        self, *, source_operation_id: str, action: str
    ) -> LegacyPageLifecycleReceipt:
        row = self._con.execute(
            "SELECT action,request_sha256,result_json "
            "FROM notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (LIFECYCLE_CONSUMER, source_operation_id),
        ).fetchone()
        if row is None or str(row["action"]) != f"legacy.page_lifecycle.{action}":
            raise LegacyPageLifecycleSourceConflict(
                "legacy lifecycle source receipt is unavailable"
            )
        try:
            parsed = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle source ledger is invalid"
            ) from exc
        if str(row["result_json"]) != canonical_json(parsed):
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle source ledger is invalid"
            )
        return validate_lifecycle_receipt(
            parsed,
            expected_action=action,
            expected_request_sha256=str(row["request_sha256"]),
        )

    def _restore_precondition(
        self,
        *,
        root_page_id: str,
        source_operation_id: str,
        trash_source_operation_id: str,
        placement_mode: str,
        fallback_parent_source_page_id: str,
        relative_source_page_id: str,
        relative_position: str,
    ) -> _PreparedLifecycle:
        trash_receipt = self._load_lifecycle_receipt(
            source_operation_id=trash_source_operation_id,
            action="trash",
        )
        if (
            trash_receipt.source_owner_subject_id != self._owner
            or trash_receipt.root_source_page_id != root_page_id
            or trash_receipt.result_root_source_page_id != root_page_id
        ):
            raise LegacyPageLifecycleSourceConflict(
                "legacy restore Trash receipt changed"
            )
        transitioned = tuple(
            effect.result_page_id
            for effect in trash_receipt.effects
            if effect.transitioned
        )
        if not transitioned or root_page_id not in transitioned:
            raise LegacyPageLifecycleContractError(
                "legacy restore Trash cohort is invalid"
            )
        page_states: dict[str, dict[str, Any]] = {}
        trash_effects = {
            effect.result_page_id: effect
            for effect in trash_receipt.effects
            if effect.transitioned
        }
        for page_id in transitioned:
            current = self._page_state(page_id)
            prior = trash_effects[page_id]
            if (
                current["lifecycle"] != "trashed"
                or current["incarnation_id"] != prior.result_incarnation_id
                or current["incarnation_generation"]
                != prior.result_incarnation_generation
                or current["revision"] != prior.result_revision
                or current["change_generation"]
                != prior.result_change_generation
            ):
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore Trash cohort changed"
                )
            page_states[page_id] = current
        if any(self._settings_locked(page_states[page_id]["page"]) for page_id in transitioned):
            raise PermissionError("owner source page is locked")
        root_prior = trash_effects[root_page_id]
        old_parent = str(page_states[root_page_id]["parent_source_page_id"])
        if placement_mode == "original":
            destination_parent = root_prior.before_parent_source_page_id
            relative_page = ""
            position = ""
        else:
            destination_parent = fallback_parent_source_page_id
            relative_page = relative_source_page_id
            position = relative_position
        if destination_parent:
            parent_state = self._page_state(destination_parent)
            if parent_state["lifecycle"] != "active" or destination_parent in transitioned:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore destination parent changed"
                )
            if self._settings_locked(parent_state["page"]):
                raise PermissionError("restore destination parent is locked")
            prior_parent = next(
                (
                    effect
                    for effect in trash_receipt.effects
                    if effect.result_page_id == destination_parent
                ),
                None,
            )
            if placement_mode == "original" and (
                prior_parent is None
                or parent_state["incarnation_id"] != prior_parent.result_incarnation_id
                or parent_state["incarnation_generation"]
                != prior_parent.result_incarnation_generation
            ):
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore original parent changed"
                )
            page_states[destination_parent] = parent_state
        if old_parent:
            page_states.setdefault(old_parent, self._page_state(old_parent))
        scope = self._lifecycle_scope(destination_parent)
        if any(page_id in transitioned for page_id in scope):
            raise LegacyPageLifecycleSourceConflict(
                "legacy restore destination scope is invalid"
            )
        for page_id in scope:
            page_states.setdefault(page_id, self._page_state(page_id))
        if relative_page:
            if relative_page not in scope:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore relative page changed"
                )
            destination_ordinal = scope.index(relative_page) + 1
            if position == "after":
                destination_ordinal += 1
        elif placement_mode == "original":
            if root_prior.before_sibling_ordinal is None:
                raise LegacyPageLifecycleContractError(
                    "legacy restore original placement is invalid"
                )
            destination_ordinal = min(
                root_prior.before_sibling_ordinal, len(scope) + 1
            )
        else:
            destination_ordinal = len(scope) + 1
        fence = self._source_fence()
        blocks_by_page: dict[str, list[dict[str, Any]]] = {}
        mappings: list[LegacyPageLifecycleObjectMapping] = []
        parent_block_digest = digest(
            {
                "domain": LIFECYCLE_PRECONDITION_DOMAIN,
                "kind": "restore_parent_blocks",
                "parents": [],
            }
        )
        if old_parent != destination_parent:
            parent_block_payload: list[dict[str, Any]] = []
            for parent_id in sorted({item for item in (old_parent, destination_parent) if item}):
                rows = list(self._bounded_parent_blocks(parent_id))
                blocks_by_page[parent_id] = rows
                parent_block_payload.append(
                    {
                        "parent_source_page_id": parent_id,
                        "blocks": [
                            {
                                key: row.get(key)
                                for key in (
                                    "id",
                                    "position",
                                    "block_type",
                                    "content",
                                    "props_json",
                                    "created_at",
                                    "updated_at",
                                )
                            }
                            for row in rows
                        ],
                    }
                )
            parent_block_digest = self._bounded_private_snapshot(
                {
                    "domain": LIFECYCLE_PRECONDITION_DOMAIN,
                    "kind": "restore_parent_blocks",
                    "parents": parent_block_payload,
                }
            )
            if destination_parent:
                root_state = page_states[root_page_id]
                source_identity = root_page_id + ":restore-reference"
                result_block_id = derive_duplicate_object_id(
                    kind="parent_reference_block",
                    prefix="b_",
                    source_operation_id=source_operation_id,
                    source_owner_subject_id=self._owner,
                    mutation_actor_subject_id=self._owner,
                    root_source_page_id=root_page_id,
                    root_incarnation_id=root_state["incarnation_id"],
                    root_incarnation_generation=root_state["incarnation_generation"],
                    source_object_id=source_identity,
                )
                if self._con.execute(
                    "SELECT 1 FROM blocks WHERE id=?", (result_block_id,)
                ).fetchone() is not None:
                    raise LegacyPageLifecycleSourceConflict(
                        "legacy restore deterministic parent reference already exists"
                    )
                mappings.append(
                    LegacyPageLifecycleObjectMapping(
                        kind="parent_reference_block",
                        source_object_id=source_identity,
                        result_object_id=result_block_id,
                        source_page_id=old_parent or root_page_id,
                        result_page_id=destination_parent,
                        byte_size=0,
                        content_sha256="",
                    )
                )
        payload = {
            "domain": LIFECYCLE_PRECONDITION_DOMAIN,
            "action": "restore",
            "source_owner_subject_id": self._owner,
            "root_source_page_id": root_page_id,
            "result_root_source_page_id": root_page_id,
            "source_operation_id": source_operation_id,
            "source_fence": fence,
            "trash_source_operation_id": trash_source_operation_id,
            "trash_receipt_sha256": trash_receipt.source_operation_receipt_sha256,
            "cohort": list(transitioned),
            "placement_mode": placement_mode,
            "destination_parent_source_page_id": destination_parent,
            "old_parent_source_page_id": old_parent,
            "destination_ordinal": destination_ordinal,
            "relative_source_page_id": relative_page,
            "relative_position": position,
            "active_scope": list(scope),
            "pages": [
                self._public_state_descriptor(page_states[page_id])
                for page_id in sorted(page_states)
            ],
            "parent_blocks_sha256": parent_block_digest,
            "mappings": [mapping.descriptor() for mapping in mappings],
        }
        return _PreparedLifecycle(
            action="restore",
            root_source_page_id=root_page_id,
            result_root_source_page_id=root_page_id,
            source_operation_id=source_operation_id,
            source_request_sha256="",
            payload=payload,
            page_rows={
                page_id: state["page"] for page_id, state in page_states.items()
            },
            page_states=page_states,
            blocks_by_page=blocks_by_page,
            mappings=tuple(mappings),
            file_plans=(),
            active_scope_before=scope,
            transitioned_page_ids=transitioned,
            trash_receipt=trash_receipt,
            destination_parent_id=destination_parent,
            destination_ordinal=destination_ordinal,
            placement_mode=placement_mode,
        )

    def _build_precondition(
        self,
        *,
        action: str,
        actor: str,
        root_page_id: str,
        source_operation_id: str,
        trash_source_operation_id: str,
        placement_mode: str,
        fallback_parent_source_page_id: str,
        relative_source_page_id: str,
        relative_position: str,
    ) -> _PreparedLifecycle:
        if action == "duplicate":
            return self._duplicate_precondition(
                actor=actor,
                root_page_id=root_page_id,
                source_operation_id=source_operation_id,
            )
        if action == "trash":
            return self._trash_precondition(
                root_page_id=root_page_id,
                source_operation_id=source_operation_id,
            )
        return self._restore_precondition(
            root_page_id=root_page_id,
            source_operation_id=source_operation_id,
            trash_source_operation_id=trash_source_operation_id,
            placement_mode=placement_mode,
            fallback_parent_source_page_id=fallback_parent_source_page_id,
            relative_source_page_id=relative_source_page_id,
            relative_position=relative_position,
        )

    def capture_precondition(
        self,
        *,
        action: str,
        mutation_actor_subject_id: str,
        root_source_page_id: str,
        catalog_command_id: str,
        caller_operation_id: str,
        source_quiesced_confirmed: bool,
        trash_source_operation_id: str = "",
        placement_mode: str = "",
        fallback_parent_source_page_id: str = "",
        relative_source_page_id: str = "",
        relative_position: str = "",
    ) -> str:
        exact_action = normalize_action(action)
        actor = normalize_name(
            mutation_actor_subject_id, field="mutation_actor_subject_id"
        )
        if actor != self._owner:
            raise PermissionError("owner permission required")
        root = normalize_object_id(
            root_source_page_id, field="root_source_page_id"
        )
        source_operation_id = derive_lifecycle_source_operation_id(
            action=exact_action,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        trash_operation = ""
        mode = ""
        parent = ""
        relative = ""
        position = ""
        if exact_action == "restore":
            trash_operation = normalize_operation_id(
                trash_source_operation_id, field="trash_source_operation_id"
            )
            mode = str(placement_mode or "")
            if mode not in {"original", "reviewed_fallback"}:
                raise ValueError("valid restore placement_mode required")
            parent = normalize_object_id(
                fallback_parent_source_page_id,
                field="fallback_parent_source_page_id",
                allow_empty=True,
            )
            relative = normalize_object_id(
                relative_source_page_id,
                field="relative_source_page_id",
                allow_empty=True,
            )
            position = str(relative_position or "")
            if bool(relative) != bool(position) or position not in {"", "before", "after"}:
                raise ValueError("valid restore relative target required")
            if mode == "original" and (parent or relative or position):
                raise ValueError("original restore does not accept fallback placement")
        elif any(
            value
            for value in (
                trash_source_operation_id,
                placement_mode,
                fallback_parent_source_page_id,
                relative_source_page_id,
                relative_position,
            )
        ):
            raise ValueError("non-restore lifecycle action has restore-only input")
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy lifecycle mutation requires source quiescence")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        self._con.execute("BEGIN")
        try:
            prepared = self._build_precondition(
                action=exact_action,
                actor=actor,
                root_page_id=root,
                source_operation_id=source_operation_id,
                trash_source_operation_id=trash_operation,
                placement_mode=mode,
                fallback_parent_source_page_id=parent,
                relative_source_page_id=relative,
                relative_position=position,
            )
            result = digest(prepared.payload)
            self._con.rollback()
            return result
        except BaseException:
            if self._con.in_transaction:
                self._con.rollback()
            raise

    def get_lifecycle_replay(
        self,
        *,
        action: str,
        mutation_actor_subject_id: str,
        root_source_page_id: str,
        catalog_command_id: str,
        caller_operation_id: str,
        trash_source_operation_id: str = "",
        placement_mode: str = "",
        fallback_parent_source_page_id: str = "",
        relative_source_page_id: str = "",
        relative_position: str = "",
    ) -> LegacyPageLifecycleReceipt | None:
        """Return one committed exact-operation receipt without page/file reads.

        The operation id binds the catalog command and caller id.  The receipt
        then binds the owner, root, action, and restore mode.  This probe is
        used only after catalog authorization/quiescence so a source commit can
        converge when its catalog acknowledgement or finalization was lost.
        """
        exact_action = normalize_action(action)
        actor = normalize_name(
            mutation_actor_subject_id, field="mutation_actor_subject_id"
        )
        if actor != self._owner:
            raise PermissionError("owner permission required")
        root = normalize_object_id(
            root_source_page_id, field="root_source_page_id"
        )
        source_operation_id = derive_lifecycle_source_operation_id(
            action=exact_action,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        trash_operation = ""
        mode = ""
        if exact_action == "restore":
            trash_operation = normalize_operation_id(
                trash_source_operation_id, field="trash_source_operation_id"
            )
            mode = str(placement_mode or "")
            if mode not in {"original", "reviewed_fallback"}:
                raise ValueError("valid restore placement_mode required")
            parent = normalize_object_id(
                fallback_parent_source_page_id,
                field="fallback_parent_source_page_id",
                allow_empty=True,
            )
            relative = normalize_object_id(
                relative_source_page_id,
                field="relative_source_page_id",
                allow_empty=True,
            )
            position = str(relative_position or "")
            if bool(relative) != bool(position) or position not in {
                "",
                "before",
                "after",
            }:
                raise ValueError("valid restore relative target required")
            if mode == "original" and (parent or relative or position):
                raise ValueError("original restore does not accept fallback placement")
        elif any(
            value
            for value in (
                trash_source_operation_id,
                placement_mode,
                fallback_parent_source_page_id,
                relative_source_page_id,
                relative_position,
            )
        ):
            raise ValueError("non-restore lifecycle action has restore-only input")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        row = self._con.execute(
            "SELECT 1 FROM notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (LIFECYCLE_CONSUMER, source_operation_id),
        ).fetchone()
        if row is None:
            return None
        receipt = self._load_lifecycle_receipt(
            source_operation_id=source_operation_id,
            action=exact_action,
        )
        if (
            receipt.source_owner_subject_id != self._owner
            or receipt.root_source_page_id != root
            or receipt.trash_source_operation_id != trash_operation
            or receipt.placement_mode != mode
        ):
            raise LegacyPageRenameOperationConflict(source_operation_id)
        return receipt

    @staticmethod
    def _mapping_dicts(
        mappings: tuple[LegacyPageLifecycleObjectMapping, ...],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
        page_map: dict[str, str] = {}
        block_map: dict[str, str] = {}
        file_map: dict[str, str] = {}
        parent_reference_map: dict[str, str] = {}
        for mapping in mappings:
            target = {
                "page": page_map,
                "block": block_map,
                "file": file_map,
                "parent_reference_block": parent_reference_map,
            }[mapping.kind]
            target[mapping.source_object_id] = mapping.result_object_id
        return page_map, block_map, file_map, parent_reference_map

    def _verify_source_file_plan(self, plan: _FilePlan) -> Path:
        source = self._contained_file(plan.source_relative_path, must_exist=True)
        if self._file_identity(source) != plan.source_identity:
            raise LegacyPageLifecycleSourceConflict(
                "legacy duplicate source file identity changed"
            )
        content_sha256, size = _file_digest(source)
        if content_sha256 != plan.content_sha256 or size != plan.byte_size:
            raise LegacyPageLifecycleSourceConflict(
                "legacy duplicate source file content changed"
            )
        if self._file_identity(source) != plan.source_identity:
            raise LegacyPageLifecycleSourceConflict(
                "legacy duplicate source file identity changed"
            )
        return source

    def _verify_result_file_plan(self, plan: _FilePlan) -> Path:
        result = self._contained_file(plan.result_relative_path, must_exist=True)
        content_sha256, size = _file_digest(result)
        if content_sha256 != plan.content_sha256 or size != plan.byte_size:
            raise LegacyPageLifecycleManualAuditRequired(
                "legacy duplicate published file content is indeterminate"
            )
        return result

    @staticmethod
    def _copy_exact_file(source: Path, destination: Path, plan: _FilePlan) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                os.fspath(destination),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            hasher = hashlib.sha256()
            total = 0
            with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                descriptor = None
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_LIFECYCLE_FILE_BYTES:
                        raise LegacyPageLifecycleBoundsExceeded(
                            "legacy duplicate file exceeds the adapter bound"
                        )
                    hasher.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if (
                total != plan.byte_size
                or "sha256:" + hasher.hexdigest() != plan.content_sha256
            ):
                raise LegacyPageLifecycleSourceConflict(
                    "legacy duplicate source file content changed"
                )
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _atomic_no_overwrite_publish(self, staged: Path, final: Path) -> bool:
        """Publish one same-volume file atomically without replacing a leaf.

        A hard-link create is the portable no-overwrite primitive used here;
        the staging name is removed immediately, and the accepted final leaf is
        required to have one link before it may be referenced by SQLite.
        """
        if int(staged.stat().st_dev) != int(final.parent.stat().st_dev):
            raise LegacyPageLifecycleUnsafePath(
                "legacy duplicate staging and result paths cross filesystems"
            )
        try:
            if os.name == "nt":
                # Windows MoveFile semantics are atomic on one volume and do
                # not replace an existing destination when no replace flag is
                # requested.
                os.rename(staged, final)
                return True
            os.link(staged, final)
        except FileExistsError:
            return False
        except OSError as exc:
            if final.exists():
                return False
            raise LegacyPageLifecycleManualAuditRequired(
                "legacy duplicate file publish failed"
            ) from exc
        try:
            staged.unlink()
        except OSError as exc:
            # The final leaf may already be visible.  Preserve both names and
            # require manual audit instead of guessing which one to delete.
            raise LegacyPageLifecycleManualAuditRequired(
                "legacy duplicate staging cleanup is indeterminate"
            ) from exc
        return True

    def _stage_and_publish_files(
        self,
        prepared: _PreparedLifecycle,
        *,
        fault_hook: FaultHook | None,
    ) -> tuple[Path | None, tuple[Path, ...]]:
        if not prepared.file_plans:
            return None, ()
        self._ensure_exact_directory(self._staging_root)
        operation_leaf = hashlib.sha256(
            prepared.source_operation_id.encode("utf-8")
        ).hexdigest()
        stage_directory = self._ensure_exact_directory(
            self._staging_root / operation_leaf
        )
        published: list[Path] = []
        try:
            if fault_hook is not None:
                fault_hook("before_file_staging")
            for index, plan in enumerate(prepared.file_plans):
                source = self._verify_source_file_plan(plan)
                staged = stage_directory / f"{plan.result_file_id}.part"
                if staged.exists():
                    self._contained_file(
                        PurePosixPath(
                            staged.relative_to(self._workspace_root).as_posix()
                        ).as_posix(),
                        must_exist=True,
                    )
                    digest_value, size = _file_digest(staged)
                    if digest_value != plan.content_sha256 or size != plan.byte_size:
                        raise LegacyPageLifecycleManualAuditRequired(
                            "legacy duplicate staged file content is indeterminate"
                        )
                else:
                    self._copy_exact_file(source, staged, plan)
                if fault_hook is not None:
                    fault_hook(f"after_file_stage:{index}")
                result = self._workspace_root.joinpath(
                    *PurePosixPath(plan.result_relative_path).parts
                )
                self._ensure_exact_directory(result.parent)
                created = False
                if result.exists():
                    self._verify_result_file_plan(plan)
                    if staged.exists():
                        staged.unlink()
                else:
                    created = self._atomic_no_overwrite_publish(staged, result)
                    self._verify_result_file_plan(plan)
                if created:
                    published.append(result)
                if fault_hook is not None:
                    fault_hook(f"after_file_publish:{index}")
            if fault_hook is not None:
                fault_hook("after_file_publish")
            return stage_directory, tuple(published)
        except LegacyPageLifecycleManualAuditRequired:
            raise
        except BaseException:
            self._cleanup_precommit_files(
                prepared,
                stage_directory=stage_directory,
                published=tuple(published),
            )
            raise

    def _cleanup_precommit_files(
        self,
        prepared: _PreparedLifecycle,
        *,
        stage_directory: Path | None,
        published: tuple[Path, ...],
    ) -> None:
        if prepared.action != "duplicate":
            return
        result_file_ids = tuple(plan.result_file_id for plan in prepared.file_plans)
        if result_file_ids:
            placeholders = ",".join("?" for _ in result_file_ids)
            if self._con.execute(
                f"SELECT 1 FROM files WHERE id IN ({placeholders}) LIMIT 1",
                result_file_ids,
            ).fetchone() is not None:
                return
        plans_by_path = {
            self._workspace_root.joinpath(
                *PurePosixPath(plan.result_relative_path).parts
            ): plan
            for plan in prepared.file_plans
        }
        for path in published:
            plan = plans_by_path.get(path)
            if plan is None or not path.exists():
                continue
            try:
                verified = self._verify_result_file_plan(plan)
                if self._file_identity(verified)[2] != 1:
                    continue
                verified.unlink()
            except (OSError, LegacyPageLifecycleUnsafePath):
                continue
        for parent in sorted(
            {
                self._workspace_root.joinpath(
                    *PurePosixPath(plan.result_relative_path).parts
                ).parent
                for plan in prepared.file_plans
            },
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                self._assert_exact_directory(parent)
                parent.rmdir()
            except (OSError, LegacyPageLifecycleUnsafePath):
                pass
        if stage_directory is not None and stage_directory.exists():
            for plan in prepared.file_plans:
                staged = stage_directory / f"{plan.result_file_id}.part"
                if not staged.exists():
                    continue
                try:
                    digest_value, size = _file_digest(staged)
                    if digest_value == plan.content_sha256 and size == plan.byte_size:
                        staged.unlink()
                except OSError:
                    continue
            try:
                stage_directory.rmdir()
                self._staging_root.rmdir()
            except OSError:
                pass

    def _coalesce_watermarks(
        self,
        *,
        before_generations: dict[str, int],
        page_ids: set[str],
    ) -> None:
        for page_id in sorted(page_ids):
            row = self._con.execute(
                "SELECT change_generation FROM notion_page_change_watermarks "
                "WHERE page_id=?",
                (page_id,),
            ).fetchone()
            if row is None:
                raise LegacyPageLifecycleContractError(
                    "legacy lifecycle watermark is unavailable"
                )
            old_generation = int(before_generations.get(page_id, 0))
            observed = int(row["change_generation"])
            if observed <= old_generation:
                continue
            changed = self._con.execute(
                "UPDATE notion_page_change_watermarks SET change_generation=? "
                "WHERE page_id=? AND change_generation=?",
                (old_generation + 1, page_id, observed),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy lifecycle watermark changed"
                )

    def _insert_current_version(
        self,
        *,
        page_id: str,
        source_identity: str,
        page: dict[str, Any],
        blocks: list[dict[str, Any]],
        actor: str,
        source_operation_id: str,
        root_state: dict[str, Any],
        now: int,
    ) -> None:
        version_id = derive_duplicate_object_id(
            kind="version",
            prefix="v_",
            source_operation_id=source_operation_id,
            source_owner_subject_id=self._owner,
            mutation_actor_subject_id=actor,
            root_source_page_id=root_state["source_page_id"],
            root_incarnation_id=root_state["incarnation_id"],
            root_incarnation_generation=root_state["incarnation_generation"],
            source_object_id=source_identity,
        )
        if self._con.execute(
            "SELECT 1 FROM page_versions WHERE id=?", (version_id,)
        ).fetchone() is not None:
            raise LegacyPageLifecycleSourceConflict(
                "legacy duplicate deterministic version already exists"
            )
        version_blocks = [
            {
                "id": str(block["id"]),
                "type": str(block["block_type"]),
                "content": str(block.get("content") or ""),
                "props": self._decode_json_object(
                    block.get("props_json"), field="block props"
                ),
                "position": int(block["position"]),
            }
            for block in blocks
        ]
        blocks_json = json.dumps(
            version_blocks, ensure_ascii=False, sort_keys=True
        )
        self._con.execute(
            "INSERT INTO page_versions(id,page_id,owner_user_id,title,icon,cover,"
            "settings_json,blocks_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                version_id,
                page_id,
                self._owner,
                str(page.get("title") or "Untitled"),
                str(page.get("icon") or ""),
                str(page.get("cover") or ""),
                str(page.get("settings_json") or "{}"),
                blocks_json,
                now,
            ),
        )

    def _mutate_duplicate_source(
        self,
        prepared: _PreparedLifecycle,
        *,
        actor: str,
        now: int,
    ) -> set[str]:
        page_map, block_map, _file_map, parent_reference_map = self._mapping_dicts(
            prepared.mappings
        )
        root_state = prepared.page_states[prepared.root_source_page_id]
        source_pages = prepared.transitioned_page_ids
        source_set = set(source_pages)
        children: dict[str, list[str]] = {}
        for source_page_id in source_pages:
            parent = str(prepared.page_rows[source_page_id].get("parent_id") or "")
            if source_page_id == prepared.root_source_page_id:
                continue
            if parent not in source_set:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy duplicate internal parent changed"
                )
            children.setdefault(parent, []).append(source_page_id)
        for parent, values in children.items():
            values.sort(
                key=lambda page_id: (
                    int(prepared.page_rows[page_id].get("position") or 0),
                    int(prepared.page_rows[page_id].get("updated_at") or 0) * -1,
                    page_id,
                )
            )
        new_scope = list(prepared.active_scope_before)
        insert_at = new_scope.index(prepared.root_source_page_id) + 1
        new_scope.insert(insert_at, prepared.result_root_source_page_id)
        changed_existing: set[str] = set()
        self._incarnation_write_context = 1
        try:
            for source_page_id in source_pages:
                source = prepared.page_rows[source_page_id]
                result_page_id = page_map[source_page_id]
                if source_page_id == prepared.root_source_page_id:
                    result_parent = prepared.destination_parent_id
                    position = prepared.destination_ordinal
                    title = (str(source.get("title") or "Untitled") + " copy")[:160]
                else:
                    source_parent = str(source.get("parent_id") or "")
                    result_parent = page_map[source_parent]
                    position = children[source_parent].index(source_page_id) + 1
                    title = str(source.get("title") or "Untitled")[:160]
                settings = _remap_reference_value(
                    self._decode_json_object(
                        source.get("settings_json"), field="page settings"
                    ),
                    page_map=page_map,
                    block_map=block_map,
                    file_map={
                        mapping.source_object_id: mapping.result_object_id
                        for mapping in prepared.mappings
                        if mapping.kind == "file"
                    },
                )
                exact_file_map = {
                    mapping.source_object_id: mapping.result_object_id
                    for mapping in prepared.mappings
                    if mapping.kind == "file"
                }
                self._con.execute(
                    "INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,"
                    "settings_json,position,archived,archived_at,revision,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,0,NULL,1,?,?)",
                    (
                        result_page_id,
                        self._owner,
                        result_parent or None,
                        title,
                        _remap_reference_text(
                            source.get("icon"),
                            page_map=page_map,
                            block_map=block_map,
                            file_map=exact_file_map,
                        )[:64],
                        _remap_reference_text(
                            source.get("cover"),
                            page_map=page_map,
                            block_map=block_map,
                            file_map=exact_file_map,
                        )[:1000],
                        canonical_json(settings),
                        position,
                        now,
                        now,
                    ),
                )
        finally:
            self._incarnation_write_context = 0
        file_map = {
            mapping.source_object_id: mapping.result_object_id
            for mapping in prepared.mappings
            if mapping.kind == "file"
        }
        result_blocks_by_page: dict[str, list[dict[str, Any]]] = {}
        for source_page_id in source_pages:
            result_page_id = page_map[source_page_id]
            result_blocks: list[dict[str, Any]] = []
            for ordinal, source in enumerate(
                prepared.blocks_by_page.get(source_page_id, []), start=1
            ):
                props = _remap_reference_value(
                    self._decode_json_object(
                        source.get("props_json"), field="block props"
                    ),
                    page_map=page_map,
                    block_map=block_map,
                    file_map=file_map,
                )
                result = {
                    "id": block_map[str(source["id"])],
                    "page_id": result_page_id,
                    "owner_user_id": self._owner,
                    "position": ordinal,
                    "block_type": str(source.get("block_type") or "paragraph"),
                    "content": _remap_reference_text(
                        source.get("content"),
                        page_map=page_map,
                        block_map=block_map,
                        file_map=file_map,
                    ),
                    "props_json": canonical_json(props),
                    "created_at": now,
                    "updated_at": now,
                }
                self._con.execute(
                    "INSERT INTO blocks(id,page_id,owner_user_id,position,block_type,"
                    "content,props_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    tuple(result[key] for key in (
                        "id", "page_id", "owner_user_id", "position", "block_type",
                        "content", "props_json", "created_at", "updated_at",
                    )),
                )
                result_blocks.append(result)
            result_blocks_by_page[result_page_id] = result_blocks
        for plan in prepared.file_plans:
            self._verify_result_file_plan(plan)
            self._con.execute(
                "INSERT INTO files(id,page_id,owner_user_id,uploader_user_id,"
                "original_name,stored_name,rel_path,mime_type,size,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    plan.result_file_id,
                    plan.result_page_id,
                    self._owner,
                    actor,
                    plan.original_name,
                    plan.stored_name,
                    plan.result_relative_path,
                    plan.mime_type,
                    plan.byte_size,
                    now,
                ),
            )
        parent_id = prepared.destination_parent_id
        parent_references = {
            mapping.source_object_id: mapping.result_object_id
            for mapping in prepared.mappings
            if mapping.kind == "parent_reference_block"
        }
        if parent_id and parent_references:
            rows = [dict(row) for row in self._con.execute(
                "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id",
                (parent_id,),
            ).fetchall()]
            rebuilt: list[dict[str, Any]] = []
            result_root = prepared.result_root_source_page_id
            result_root_row = self._con.execute(
                "SELECT title,icon FROM pages WHERE id=?", (result_root,)
            ).fetchone()
            for row in rows:
                rebuilt.append(row)
                source_id = str(row["id"])
                if source_id not in parent_references:
                    continue
                props = self._decode_json_object(
                    row.get("props_json"), field="block props"
                )
                if str(props.get("page_id") or "") != prepared.root_source_page_id:
                    raise LegacyPageLifecycleSourceConflict(
                        "legacy duplicate parent reference changed"
                    )
                rebuilt.append(
                    {
                        "id": parent_references[source_id],
                        "page_id": parent_id,
                        "owner_user_id": self._owner,
                        "position": 0,
                        "block_type": "child_page",
                        "content": str(result_root_row["title"] or "Untitled"),
                        "props_json": canonical_json(
                            {
                                "page_id": result_root,
                                "title": str(result_root_row["title"] or "Untitled"),
                                "icon": str(result_root_row["icon"] or ""),
                            }
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            self._con.execute("DELETE FROM blocks WHERE page_id=?", (parent_id,))
            for ordinal, row in enumerate(rebuilt, start=1):
                self._con.execute(
                    "INSERT INTO blocks(id,page_id,owner_user_id,position,block_type,"
                    "content,props_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"], parent_id, self._owner, ordinal,
                        row["block_type"], row.get("content") or "",
                        row.get("props_json") or "{}", int(row.get("created_at") or now), now,
                    ),
                )
            self._con.execute(
                "UPDATE pages SET revision=revision+1,updated_at=? WHERE id=? AND archived=0",
                (now, parent_id),
            )
            changed_existing.add(parent_id)
        for ordinal, page_id in enumerate(new_scope, start=1):
            if page_id == prepared.result_root_source_page_id:
                continue
            before = prepared.page_states[page_id]
            if int(before["sibling_ordinal"]) == ordinal:
                continue
            changed = self._con.execute(
                "UPDATE pages SET position=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND archived=0 AND revision=?",
                (ordinal, now, page_id, before["revision"]),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy duplicate sibling scope changed"
                )
            changed_existing.add(page_id)
        for source_page_id in source_pages:
            result_page_id = page_map[source_page_id]
            page = dict(
                self._con.execute(
                    "SELECT * FROM pages WHERE id=?", (result_page_id,)
                ).fetchone()
            )
            self._insert_current_version(
                page_id=result_page_id,
                source_identity=source_page_id + ":current",
                page=page,
                blocks=result_blocks_by_page[result_page_id],
                actor=actor,
                source_operation_id=prepared.source_operation_id,
                root_state=root_state,
                now=now,
            )
        if parent_id and parent_references:
            parent_page = dict(
                self._con.execute("SELECT * FROM pages WHERE id=?", (parent_id,)).fetchone()
            )
            parent_blocks = [dict(row) for row in self._con.execute(
                "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id", (parent_id,)
            ).fetchall()]
            self._insert_current_version(
                page_id=parent_id,
                source_identity="parent-reference:" + parent_id,
                page=parent_page,
                blocks=parent_blocks,
                actor=actor,
                source_operation_id=prepared.source_operation_id,
                root_state=root_state,
                now=now,
            )
        affected = set(page_map.values()) | changed_existing
        before_generations = {
            page_id: int(state["change_generation"])
            for page_id, state in prepared.page_states.items()
        }
        self._coalesce_watermarks(
            before_generations=before_generations,
            page_ids=affected,
        )
        return affected

    def _mutate_trash_source(
        self, prepared: _PreparedLifecycle, *, now: int
    ) -> set[str]:
        cohort = set(prepared.transitioned_page_ids)
        affected: set[str] = set(cohort)
        for page_id in sorted(cohort):
            before = prepared.page_states[page_id]
            changed = self._con.execute(
                "UPDATE pages SET archived=1,archived_at=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND owner_user_id=? AND archived=0 AND revision=?",
                (now, now, page_id, self._owner, before["revision"]),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy Trash cohort changed"
                )
        remaining_scope = [
            page_id
            for page_id in prepared.active_scope_before
            if page_id not in cohort
        ]
        for ordinal, page_id in enumerate(remaining_scope, start=1):
            before = prepared.page_states[page_id]
            if before["sibling_ordinal"] == ordinal:
                continue
            changed = self._con.execute(
                "UPDATE pages SET position=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND archived=0 AND revision=?",
                (ordinal, now, page_id, before["revision"]),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy Trash sibling scope changed"
                )
            affected.add(page_id)
        before_generations = {
            page_id: int(state["change_generation"])
            for page_id, state in prepared.page_states.items()
        }
        self._coalesce_watermarks(
            before_generations=before_generations,
            page_ids=affected,
        )
        return affected

    def _rewrite_restore_parent_blocks(
        self,
        prepared: _PreparedLifecycle,
        *,
        actor: str,
        now: int,
    ) -> set[str]:
        root = prepared.root_source_page_id
        old_parent = str(prepared.page_states[root]["parent_source_page_id"])
        new_parent = prepared.destination_parent_id
        if old_parent == new_parent:
            return set()
        changed_pages: set[str] = set()
        root_row = self._con.execute(
            "SELECT title,icon FROM pages WHERE id=?", (root,)
        ).fetchone()
        if root_row is None:
            raise LegacyPageLifecycleSourceConflict(
                "legacy restore root changed"
            )
        parent_reference_map = {
            mapping.source_object_id: mapping.result_object_id
            for mapping in prepared.mappings
            if mapping.kind == "parent_reference_block"
        }
        for parent_id in sorted({item for item in (old_parent, new_parent) if item}):
            current = [
                dict(row)
                for row in self._con.execute(
                    "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id",
                    (parent_id,),
                ).fetchall()
            ]
            rebuilt: list[dict[str, Any]] = []
            changed = False
            for row in current:
                props = self._decode_json_object(
                    row.get("props_json"), field="block props"
                )
                if (
                    parent_id == old_parent
                    and str(row.get("block_type") or "") == "child_page"
                    and str(props.get("page_id") or "") == root
                ):
                    changed = True
                    continue
                rows_value = props.get("rows")
                if parent_id == old_parent and isinstance(rows_value, list):
                    filtered = [
                        item
                        for item in rows_value
                        if not (
                            isinstance(item, dict)
                            and str(item.get("page_id") or "") == root
                        )
                    ]
                    if len(filtered) != len(rows_value):
                        props["rows"] = filtered
                        row = {**row, "props_json": canonical_json(props)}
                        changed = True
                rebuilt.append(row)
            if parent_id == new_parent:
                if any(
                    str(row.get("block_type") or "") == "child_page"
                    and str(
                        self._decode_json_object(
                            row.get("props_json"), field="block props"
                        ).get("page_id")
                        or ""
                    )
                    == root
                    for row in rebuilt
                ):
                    raise LegacyPageLifecycleSourceConflict(
                        "legacy restore destination reference changed"
                    )
                source_identity = root + ":restore-reference"
                result_id = parent_reference_map.get(source_identity)
                if not result_id:
                    raise LegacyPageLifecycleContractError(
                        "legacy restore parent reference mapping is unavailable"
                    )
                relative = str(prepared.payload.get("relative_source_page_id") or "")
                position = str(prepared.payload.get("relative_position") or "")
                insert_at = len(rebuilt)
                if relative:
                    for index, row in enumerate(rebuilt):
                        props = self._decode_json_object(
                            row.get("props_json"), field="block props"
                        )
                        if (
                            str(row.get("block_type") or "") == "child_page"
                            and str(props.get("page_id") or "") == relative
                        ):
                            insert_at = index + (1 if position == "after" else 0)
                            break
                rebuilt.insert(
                    insert_at,
                    {
                        "id": result_id,
                        "page_id": parent_id,
                        "owner_user_id": self._owner,
                        "position": 0,
                        "block_type": "child_page",
                        "content": str(root_row["title"] or "Untitled"),
                        "props_json": canonical_json(
                            {
                                "page_id": root,
                                "title": str(root_row["title"] or "Untitled"),
                                "icon": str(root_row["icon"] or ""),
                            }
                        ),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                changed = True
            if not changed:
                continue
            self._con.execute("DELETE FROM blocks WHERE page_id=?", (parent_id,))
            for ordinal, row in enumerate(rebuilt, start=1):
                self._con.execute(
                    "INSERT INTO blocks(id,page_id,owner_user_id,position,block_type,"
                    "content,props_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"], parent_id, self._owner, ordinal,
                        row["block_type"], str(row.get("content") or ""),
                        str(row.get("props_json") or "{}"),
                        int(row.get("created_at") or now), now,
                    ),
                )
            before = prepared.page_states[parent_id]
            updated = self._con.execute(
                "UPDATE pages SET revision=revision+1,updated_at=? "
                "WHERE id=? AND revision=?",
                (now, parent_id, before["revision"]),
            )
            if updated.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore parent changed"
                )
            parent_page = dict(
                self._con.execute("SELECT * FROM pages WHERE id=?", (parent_id,)).fetchone()
            )
            parent_blocks = [
                dict(row)
                for row in self._con.execute(
                    "SELECT * FROM blocks WHERE page_id=? ORDER BY position,id",
                    (parent_id,),
                ).fetchall()
            ]
            root_state = prepared.page_states[root]
            self._insert_current_version(
                page_id=parent_id,
                source_identity="restore-parent:" + parent_id,
                page=parent_page,
                blocks=parent_blocks,
                actor=actor,
                source_operation_id=prepared.source_operation_id,
                root_state=root_state,
                now=now,
            )
            changed_pages.add(parent_id)
        return changed_pages

    def _mutate_restore_source(
        self,
        prepared: _PreparedLifecycle,
        *,
        actor: str,
        now: int,
    ) -> set[str]:
        cohort = set(prepared.transitioned_page_ids)
        root = prepared.root_source_page_id
        affected: set[str] = set(cohort)
        for page_id in sorted(cohort):
            before = prepared.page_states[page_id]
            parent = (
                prepared.destination_parent_id
                if page_id == root
                else str(before["parent_source_page_id"])
            )
            position = (
                prepared.destination_ordinal
                if page_id == root
                else int(before["page"].get("position") or 0)
            )
            changed = self._con.execute(
                "UPDATE pages SET archived=0,archived_at=NULL,parent_id=?,position=?,"
                "updated_at=?,revision=revision+1 "
                "WHERE id=? AND owner_user_id=? AND archived=1 AND revision=?",
                (
                    parent or None,
                    position,
                    now,
                    page_id,
                    self._owner,
                    before["revision"],
                ),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore cohort changed"
                )
        destination_scope = list(prepared.active_scope_before)
        destination_scope.insert(
            prepared.destination_ordinal - 1, root
        )
        for ordinal, page_id in enumerate(destination_scope, start=1):
            if page_id == root:
                continue
            before = prepared.page_states[page_id]
            if before["sibling_ordinal"] == ordinal:
                continue
            changed = self._con.execute(
                "UPDATE pages SET position=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND archived=0 AND revision=?",
                (ordinal, now, page_id, before["revision"]),
            )
            if changed.rowcount != 1:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy restore sibling scope changed"
                )
            affected.add(page_id)
        affected.update(
            self._rewrite_restore_parent_blocks(
                prepared, actor=actor, now=now
            )
        )
        before_generations = {
            page_id: int(state["change_generation"])
            for page_id, state in prepared.page_states.items()
        }
        self._coalesce_watermarks(
            before_generations=before_generations,
            page_ids=affected,
        )
        return affected

    def _post_states(self, page_ids: set[str]) -> dict[str, dict[str, Any]]:
        scope_cache: dict[str, tuple[str, ...]] = {}
        return {
            page_id: self._page_state(page_id, scope_cache=scope_cache)
            for page_id in sorted(page_ids)
        }

    def _build_effects(
        self,
        prepared: _PreparedLifecycle,
        *,
        affected: set[str],
    ) -> tuple[LegacyPageLifecycleEffect, ...]:
        effects: list[LegacyPageLifecycleEffect] = []
        if prepared.action == "duplicate":
            page_map, _block_map, _file_map, _parent_map = self._mapping_dicts(
                prepared.mappings
            )
            result_states = self._post_states(set(page_map.values()))
            for source_page_id in prepared.transitioned_page_ids:
                source = prepared.page_states[source_page_id]
                result_page_id = page_map[source_page_id]
                result = result_states[result_page_id]
                effects.append(
                    LegacyPageLifecycleEffect(
                        source_page_id=source_page_id,
                        result_page_id=result_page_id,
                        source_incarnation_id=source["incarnation_id"],
                        source_incarnation_generation=source["incarnation_generation"],
                        source_revision=source["revision"],
                        source_change_generation=source["change_generation"],
                        result_incarnation_id=result["incarnation_id"],
                        result_incarnation_generation=result["incarnation_generation"],
                        result_revision=result["revision"],
                        result_change_generation=result["change_generation"],
                        before_lifecycle="absent",
                        after_lifecycle="active",
                        before_parent_source_page_id="",
                        after_parent_source_page_id=result["parent_source_page_id"],
                        before_sibling_ordinal=None,
                        after_sibling_ordinal=result["sibling_ordinal"],
                        transitioned=True,
                    )
                )
            context_ids = (
                set(prepared.active_scope_before)
                | ({prepared.destination_parent_id} if prepared.destination_parent_id else set())
            )
            context_states = self._post_states(context_ids)
            for page_id in sorted(context_ids):
                before = prepared.page_states[page_id]
                after = context_states[page_id]
                effects.append(
                    LegacyPageLifecycleEffect(
                        source_page_id=page_id,
                        result_page_id=page_id,
                        source_incarnation_id=before["incarnation_id"],
                        source_incarnation_generation=before["incarnation_generation"],
                        source_revision=before["revision"],
                        source_change_generation=before["change_generation"],
                        result_incarnation_id=after["incarnation_id"],
                        result_incarnation_generation=after["incarnation_generation"],
                        result_revision=after["revision"],
                        result_change_generation=after["change_generation"],
                        before_lifecycle=before["lifecycle"],
                        after_lifecycle=after["lifecycle"],
                        before_parent_source_page_id=before["parent_source_page_id"],
                        after_parent_source_page_id=after["parent_source_page_id"],
                        before_sibling_ordinal=before["sibling_ordinal"],
                        after_sibling_ordinal=after["sibling_ordinal"],
                        transitioned=False,
                    )
                )
        else:
            context_ids = set(prepared.page_states)
            post = self._post_states(context_ids)
            transitioned = set(prepared.transitioned_page_ids)
            for page_id in sorted(context_ids):
                before = prepared.page_states[page_id]
                after = post[page_id]
                effects.append(
                    LegacyPageLifecycleEffect(
                        source_page_id=page_id,
                        result_page_id=page_id,
                        source_incarnation_id=before["incarnation_id"],
                        source_incarnation_generation=before["incarnation_generation"],
                        source_revision=before["revision"],
                        source_change_generation=before["change_generation"],
                        result_incarnation_id=after["incarnation_id"],
                        result_incarnation_generation=after["incarnation_generation"],
                        result_revision=after["revision"],
                        result_change_generation=after["change_generation"],
                        before_lifecycle=before["lifecycle"],
                        after_lifecycle=after["lifecycle"],
                        before_parent_source_page_id=before["parent_source_page_id"],
                        after_parent_source_page_id=after["parent_source_page_id"],
                        before_sibling_ordinal=before["sibling_ordinal"],
                        after_sibling_ordinal=after["sibling_ordinal"],
                        transitioned=page_id in transitioned,
                    )
                )
        effects.sort(key=lambda item: (item.result_page_id, item.source_page_id))
        if len(effects) > MAX_LIFECYCLE_EFFECTS:
            raise LegacyPageLifecycleBoundsExceeded(
                "legacy lifecycle effect set exceeds the adapter bound"
            )
        return tuple(effects)

    def _build_receipt(
        self,
        prepared: _PreparedLifecycle,
        *,
        affected: set[str],
        source_request_sha256: str,
        trash_source_operation_id: str,
    ) -> LegacyPageLifecycleReceipt:
        effects = self._build_effects(prepared, affected=affected)
        fence = self._source_fence()
        file_manifest_sha256 = digest(
            {
                "domain": LIFECYCLE_FILE_MANIFEST_DOMAIN,
                "action": prepared.action,
                "files": [
                    mapping.descriptor()
                    for mapping in prepared.mappings
                    if mapping.kind == "file"
                ],
            }
        )
        manifest_body = {
            "domain": LIFECYCLE_MANIFEST_DOMAIN,
            "action": prepared.action,
            "source_owner_subject_id": self._owner,
            "root_source_page_id": prepared.root_source_page_id,
            "result_root_source_page_id": prepared.result_root_source_page_id,
            **fence,
            "trash_source_operation_id": (
                trash_source_operation_id if prepared.action == "restore" else ""
            ),
            "placement_mode": (
                prepared.placement_mode if prepared.action == "restore" else ""
            ),
            "file_manifest_sha256": file_manifest_sha256,
            "effects": [effect.descriptor() for effect in effects],
            "mappings": [mapping.descriptor() for mapping in prepared.mappings],
        }
        provisional = LegacyPageLifecycleReceipt(
            action=prepared.action,
            source_owner_subject_id=self._owner,
            root_source_page_id=prepared.root_source_page_id,
            result_root_source_page_id=prepared.result_root_source_page_id,
            source_request_sha256=source_request_sha256,
            source_operation_receipt_sha256="sha256:" + "0" * 64,
            post_manifest_sha256=digest(manifest_body),
            file_manifest_sha256=file_manifest_sha256,
            source_device=fence["source_device"],
            source_inode=fence["source_inode"],
            source_link_count=fence["source_link_count"],
            source_epoch=fence["source_epoch"],
            full_sweep_generation=fence["full_sweep_generation"],
            trash_source_operation_id=(
                trash_source_operation_id if prepared.action == "restore" else ""
            ),
            placement_mode=(
                prepared.placement_mode if prepared.action == "restore" else ""
            ),
            effects=effects,
            mappings=prepared.mappings,
        )
        receipt = LegacyPageLifecycleReceipt(
            **{
                **provisional.__dict__,
                "source_operation_receipt_sha256": digest(
                    {
                        "domain": LIFECYCLE_RECEIPT_DOMAIN,
                        "result": provisional.descriptor(include_receipt=False),
                    }
                ),
            }
        )
        return validate_lifecycle_receipt(
            receipt,
            expected_action=prepared.action,
            expected_request_sha256=source_request_sha256,
        )

    def _replay_lifecycle(
        self,
        *,
        source_operation_id: str,
        action: str,
        source_request_sha256: str,
    ) -> LegacyPageLifecycleReceipt | None:
        row = self._con.execute(
            "SELECT action,request_sha256,result_json "
            "FROM notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (LIFECYCLE_CONSUMER, source_operation_id),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["action"]) != f"legacy.page_lifecycle.{action}"
            or str(row["request_sha256"]) != source_request_sha256
        ):
            raise LegacyPageRenameOperationConflict(source_operation_id)
        try:
            parsed = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle source ledger is invalid"
            ) from exc
        if str(row["result_json"]) != canonical_json(parsed):
            raise LegacyPageLifecycleContractError(
                "legacy lifecycle source ledger is invalid"
            )
        return validate_lifecycle_receipt(
            parsed,
            expected_action=action,
            expected_request_sha256=source_request_sha256,
        )

    def apply_lifecycle(
        self,
        *,
        action: str,
        mutation_actor_subject_id: str,
        root_source_page_id: str,
        catalog_command_id: str,
        caller_operation_id: str,
        expected_precondition_sha256: str,
        source_quiesced_confirmed: bool,
        trash_source_operation_id: str = "",
        placement_mode: str = "",
        fallback_parent_source_page_id: str = "",
        relative_source_page_id: str = "",
        relative_position: str = "",
        fault_hook: FaultHook | None = None,
    ) -> LegacyPageLifecycleReceipt:
        exact_action = normalize_action(action)
        actor = normalize_name(
            mutation_actor_subject_id, field="mutation_actor_subject_id"
        )
        if actor != self._owner:
            raise PermissionError("owner permission required")
        root = normalize_object_id(
            root_source_page_id, field="root_source_page_id"
        )
        expected = normalize_sha256(
            expected_precondition_sha256, field="expected_precondition_sha256"
        )
        source_operation_id = derive_lifecycle_source_operation_id(
            action=exact_action,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        source_request_sha256 = lifecycle_request_sha256(
            action=exact_action,
            mutation_actor_subject_id=actor,
            source_owner_subject_id=self._owner,
            root_source_page_id=root,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
            expected_precondition_sha256=expected,
            trash_source_operation_id=trash_source_operation_id,
            placement_mode=placement_mode,
            fallback_parent_source_page_id=fallback_parent_source_page_id,
            relative_source_page_id=relative_source_page_id,
            relative_position=relative_position,
        )
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        replay = self._replay_lifecycle(
            source_operation_id=source_operation_id,
            action=exact_action,
            source_request_sha256=source_request_sha256,
        )
        if replay is not None:
            return replay
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy lifecycle mutation requires source quiescence")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        trash_operation = (
            normalize_operation_id(
                trash_source_operation_id, field="trash_source_operation_id"
            )
            if exact_action == "restore"
            else ""
        )
        mode = str(placement_mode or "") if exact_action == "restore" else ""
        parent = (
            normalize_object_id(
                fallback_parent_source_page_id,
                field="fallback_parent_source_page_id",
                allow_empty=True,
            )
            if exact_action == "restore"
            else ""
        )
        relative = (
            normalize_object_id(
                relative_source_page_id,
                field="relative_source_page_id",
                allow_empty=True,
            )
            if exact_action == "restore"
            else ""
        )
        position = str(relative_position or "") if exact_action == "restore" else ""
        stage_directory: Path | None = None
        published: tuple[Path, ...] = ()
        prepared: _PreparedLifecycle | None = None
        source_committed = False
        outcome_unknown = False
        try:
            if exact_action == "duplicate":
                self._con.execute("BEGIN")
                try:
                    prepared = self._build_precondition(
                        action=exact_action,
                        actor=actor,
                        root_page_id=root,
                        source_operation_id=source_operation_id,
                        trash_source_operation_id="",
                        placement_mode="",
                        fallback_parent_source_page_id="",
                        relative_source_page_id="",
                        relative_position="",
                    )
                    if digest(prepared.payload) != expected:
                        raise LegacyPageLifecycleSourceConflict(
                            "legacy lifecycle source precondition changed"
                        )
                    self._con.rollback()
                except BaseException:
                    if self._con.in_transaction:
                        self._con.rollback()
                    raise
                stage_directory, published = self._stage_and_publish_files(
                    prepared, fault_hook=fault_hook
                )
            now = self._now()
            self._now_value = now
            self._con.execute("BEGIN IMMEDIATE")
            replay = self._replay_lifecycle(
                source_operation_id=source_operation_id,
                action=exact_action,
                source_request_sha256=source_request_sha256,
            )
            if replay is not None:
                self._con.rollback()
                return replay
            current = self._build_precondition(
                action=exact_action,
                actor=actor,
                root_page_id=root,
                source_operation_id=source_operation_id,
                trash_source_operation_id=trash_operation,
                placement_mode=mode,
                fallback_parent_source_page_id=parent,
                relative_source_page_id=relative,
                relative_position=position,
            )
            if digest(current.payload) != expected:
                raise LegacyPageLifecycleSourceConflict(
                    "legacy lifecycle source precondition changed"
                )
            prepared = current
            if fault_hook is not None:
                fault_hook("before_source_mutation")
            if exact_action == "duplicate":
                for plan in prepared.file_plans:
                    self._verify_source_file_plan(plan)
                    self._verify_result_file_plan(plan)
                affected = self._mutate_duplicate_source(
                    prepared, actor=actor, now=now
                )
            elif exact_action == "trash":
                affected = self._mutate_trash_source(prepared, now=now)
            else:
                affected = self._mutate_restore_source(
                    prepared, actor=actor, now=now
                )
            if fault_hook is not None:
                fault_hook("after_source_mutation")
            receipt = self._build_receipt(
                prepared,
                affected=affected,
                source_request_sha256=source_request_sha256,
                trash_source_operation_id=trash_operation,
            )
            if fault_hook is not None:
                fault_hook("before_operation_insert")
            self._write_context = 1
            try:
                self._con.execute(
                    "INSERT INTO notion_legacy_change_consumer_operations("
                    "consumer,operation_id,action,request_sha256,result_json,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        LIFECYCLE_CONSUMER,
                        source_operation_id,
                        f"legacy.page_lifecycle.{exact_action}",
                        source_request_sha256,
                        canonical_json(receipt.descriptor()),
                        now,
                    ),
                )
            finally:
                self._write_context = 0
            if fault_hook is not None:
                fault_hook("after_operation_insert")
                fault_hook("before_commit")
            try:
                self._con.commit()
                source_committed = True
            except BaseException as exc:
                if self._con.in_transaction:
                    self._con.rollback()
                    raise
                recovered = self._replay_lifecycle(
                    source_operation_id=source_operation_id,
                    action=exact_action,
                    source_request_sha256=source_request_sha256,
                )
                if recovered is not None:
                    source_committed = True
                    return recovered
                outcome_unknown = True
                raise LegacyPageLifecycleManualAuditRequired(
                    "legacy lifecycle commit outcome is indeterminate"
                ) from exc
            self._assert_identity()
            if fault_hook is not None:
                fault_hook("after_commit")
            return receipt
        except BaseException:
            self._write_context = 0
            self._incarnation_write_context = 0
            if self._con.in_transaction:
                self._con.rollback()
            if (
                prepared is not None
                and exact_action == "duplicate"
                and not source_committed
                and not outcome_unknown
            ):
                self._cleanup_precommit_files(
                    prepared,
                    stage_directory=stage_directory,
                    published=published,
                )
            raise


__all__ = [
    "LegacyPageDuplicateTrashRestoreSourceAdapter",
    "LegacyPageLifecycleBoundsExceeded",
    "LegacyPageLifecycleManualAuditRequired",
    "LegacyPageLifecycleSourceConflict",
    "LegacyPageLifecycleUnsafePath",
]
