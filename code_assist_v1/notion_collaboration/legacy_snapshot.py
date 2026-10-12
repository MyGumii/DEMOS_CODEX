"""Deterministic, marker-bound legacy Notion snapshot import for A0B-P1.

Only canonical JSON snapshots inside a newly created, explicitly sanitized
temporary root are accepted.  The importer has no API for a live user-data
root or per-user database and never discovers owners by enumerating a
directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from typing import Any, Mapping, Sequence

from .canonical_authority import (
    CanonicalAuthority,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _clean_blocks,
    _content_descriptor,
    _identifier,
    _operation_id,
    _request_sha256,
    _sha256,
)


SNAPSHOT_CONTRACT = "code_assist.notion.sanitized_legacy_snapshot.v1"
MARKER_CONTRACT = "code_assist.notion.sanitized_legacy_snapshot_marker.v1"
MARKER_NAME = ".notion-sanitized-legacy-snapshot.json"
MANIFEST_NAME = "manifest.json"
PAGES_NAME = "pages.json"
EXPECTED_FILES = frozenset({MARKER_NAME, MANIFEST_NAME, PAGES_NAME})
FORBIDDEN_COMPONENTS = {"knowledge", "user_data", ".runtime", ".notion-workspace-v2"}
_SNAPSHOT_ID_RE = re.compile(r"^snap_[a-z0-9]{16,64}$", re.ASCII)


class SnapshotBoundaryError(ValueError):
    """A snapshot root is not a sanitized disposable boundary."""


class SnapshotContractError(ValueError):
    """Snapshot bytes or semantic content do not match the manifest."""


class SnapshotImportConflict(RuntimeError):
    """The snapshot ID or imported content conflicts with canonical state."""


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_exclusive(path: Path, value: Any) -> None:
    with path.open("xb") as output:
        output.write(_canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validated_parent(path: Path) -> Path:
    if not path.is_absolute():
        raise SnapshotBoundaryError("absolute allowed_parent required")
    parent = path.resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    if not parent.is_dir() or (parent != temporary and not _is_within(parent, temporary)):
        raise SnapshotBoundaryError("allowed_parent must be under the OS temporary root")
    if parent.is_symlink() or any(part.lower() in FORBIDDEN_COMPONENTS for part in parent.parts):
        raise SnapshotBoundaryError("sanitized snapshot parent is forbidden")
    return parent


def _validated_root(root: Path, allowed_parent: Path, *, must_exist: bool) -> Path:
    parent = _validated_parent(allowed_parent)
    if not root.is_absolute():
        raise SnapshotBoundaryError("absolute snapshot root required")
    candidate = root.resolve(strict=must_exist)
    if candidate.parent != parent or candidate == parent:
        raise SnapshotBoundaryError("snapshot root must be a direct child of allowed_parent")
    if any(part.lower() in FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise SnapshotBoundaryError("snapshot root resembles a protected runtime path")
    if must_exist and (not candidate.is_dir() or candidate.is_symlink()):
        raise SnapshotBoundaryError("snapshot root is unavailable")
    return candidate


def _json_value(value: Any, *, field: str) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise SnapshotContractError(f"valid {field} JSON required") from exc


def _normalize_comment(raw: Mapping[str, Any], *, page_id: str, index: int) -> dict[str, Any]:
    comment_id = _identifier(str(raw.get("id", "")), field="comment_id")
    author = _identifier(str(raw.get("author_subject_id", raw.get("author_user_id", ""))), field="author_subject_id")
    content = str(raw.get("content", "")).strip()
    if not content:
        raise SnapshotContractError("comment content required")
    block_id = raw.get("block_id")
    parent_id = raw.get("parent_comment_id", raw.get("parent_id"))
    return {
        "id": comment_id,
        "page_id": page_id,
        "block_id": None if block_id in (None, "") else _identifier(str(block_id), field="block_id"),
        "author_subject_id": author,
        "content": content,
        "anchor": _json_value(raw.get("anchor", {}), field="anchor"),
        "parent_comment_id": None if parent_id in (None, "") else _identifier(str(parent_id), field="parent_comment_id"),
        "reactions": _json_value(raw.get("reactions", {}), field="reactions"),
        "resolved": bool(raw.get("resolved", False)),
        "created_at": int(raw.get("created_at", index + 1)),
        "updated_at": int(raw.get("updated_at", raw.get("created_at", index + 1))),
    }


def _normalize_page(raw: Mapping[str, Any], *, owner_subject_id: str, index: int) -> dict[str, Any]:
    page_id = _identifier(str(raw.get("id", "")), field="page_id")
    owner = _identifier(str(raw.get("owner_subject_id", raw.get("owner_user_id", owner_subject_id))), field="owner_subject_id")
    if owner != owner_subject_id:
        raise SnapshotContractError("every legacy page must match the manifest owner")
    title = str(raw.get("title", "")).strip()
    if not title:
        raise SnapshotContractError("page title required")
    parent_id = raw.get("parent_id")
    blocks = _clean_blocks(list(raw.get("blocks", [])))
    comments_raw = raw.get("comments", [])
    if isinstance(comments_raw, (str, bytes)) or not isinstance(comments_raw, Sequence):
        raise SnapshotContractError("comments array required")
    comments = [
        _normalize_comment(item, page_id=page_id, index=comment_index)
        for comment_index, item in enumerate(comments_raw)
        if isinstance(item, Mapping)
    ]
    if len(comments) != len(comments_raw):
        raise SnapshotContractError("comment object required")
    shares_raw = raw.get("shares", [])
    if isinstance(shares_raw, (str, bytes)) or not isinstance(shares_raw, Sequence):
        raise SnapshotContractError("shares array required")
    shares: list[dict[str, str]] = []
    for share in shares_raw:
        if not isinstance(share, Mapping):
            raise SnapshotContractError("share object required")
        role = str(share.get("role", "view")).lower()
        if role not in {"view", "comment", "edit"}:
            raise SnapshotContractError("valid share role required")
        shares.append(
            {
                "subject_id": _identifier(
                    str(share.get("subject_id", share.get("shared_user_id", ""))),
                    field="shared_subject_id",
                ),
                "role": role,
            }
        )
    shares.sort(key=lambda item: item["subject_id"])
    if len({item["subject_id"] for item in shares}) != len(shares):
        raise SnapshotContractError("duplicate page share")
    created_at = int(raw.get("created_at", index + 1))
    updated_at = int(raw.get("updated_at", created_at))
    revision = int(raw.get("revision", 1))
    if revision < 1 or created_at < 0 or updated_at < created_at:
        raise SnapshotContractError("invalid page revision or timestamp")
    return {
        "id": page_id,
        "owner_subject_id": owner,
        "parent_id": None if parent_id in (None, "") else _identifier(str(parent_id), field="parent_id"),
        "title": title,
        "icon": str(raw.get("icon", "")),
        "cover": str(raw.get("cover", "")),
        "settings": _json_value(raw.get("settings", {}), field="settings"),
        "position": int(raw.get("position", index)),
        "archived": bool(raw.get("archived", False)),
        "archived_at": raw.get("archived_at"),
        "revision": revision,
        "created_at": created_at,
        "updated_at": updated_at,
        "blocks": blocks,
        "comments": comments,
        "shares": shares,
    }


def _topological_pages(pages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    remaining = {str(page["id"]): dict(page) for page in pages}
    if len(remaining) != len(pages):
        raise SnapshotContractError("duplicate page id")
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    while remaining:
        ready = sorted(
            page_id
            for page_id, page in remaining.items()
            if page.get("parent_id") is None or str(page["parent_id"]) in emitted
        )
        if not ready:
            raise SnapshotContractError("missing or cyclic page parent")
        for page_id in ready:
            ordered.append(remaining.pop(page_id))
            emitted.add(page_id)
    return ordered


def _content_set(pages: Sequence[Mapping[str, Any]]) -> list[dict[str, str | int]]:
    result: list[dict[str, str | int]] = []
    for page in pages:
        descriptor = _content_descriptor(
            title=str(page["title"]),
            icon=str(page["icon"]),
            cover=str(page["cover"]),
            settings=dict(page["settings"]),
            blocks=list(page["blocks"]),
        )
        result.append(
            {
                "page_id": str(page["id"]),
                "revision": int(page["revision"]),
                "content_sha256": _sha256(_canonical_json(descriptor)),
            }
        )
    return sorted(result, key=lambda item: str(item["page_id"]))


def write_sanitized_snapshot(
    root: str | os.PathLike[str],
    *,
    allowed_parent: str | os.PathLike[str],
    snapshot_id: str,
    workspace_id: str,
    owner_subject_id: str,
    pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create deterministic sanitized bytes; no source path or database is accepted."""

    snapshot = str(snapshot_id)
    if _SNAPSHOT_ID_RE.fullmatch(snapshot) is None:
        raise SnapshotContractError("valid snapshot_id required")
    workspace = _identifier(workspace_id, field="workspace_id")
    owner = _identifier(owner_subject_id, field="owner_subject_id")
    parent = _validated_parent(Path(allowed_parent))
    candidate = _validated_root(Path(root), parent, must_exist=False)
    if candidate.exists():
        raise SnapshotBoundaryError("snapshot root must not already exist")
    normalized = [
        _normalize_page(page, owner_subject_id=owner, index=index)
        for index, page in enumerate(pages)
        if isinstance(page, Mapping)
    ]
    if len(normalized) != len(pages):
        raise SnapshotContractError("page object required")
    normalized = _topological_pages(normalized)
    pages_document = {"schema_version": 1, "pages": normalized}
    pages_bytes = _canonical_bytes(pages_document)
    content_set = _content_set(normalized)
    high_water = {
        "page_count": len(normalized),
        "block_count": sum(len(page["blocks"]) for page in normalized),
        "comment_count": sum(len(page["comments"]) for page in normalized),
        "max_updated_at": max((int(page["updated_at"]) for page in normalized), default=0),
        "revision_set_sha256": _digest(_canonical_bytes(content_set)),
    }
    manifest = {
        "schema_version": 1,
        "contract": SNAPSHOT_CONTRACT,
        "snapshot_id": snapshot,
        "workspace_id": workspace,
        "owner_subject_id": owner,
        "sanitized": True,
        "live_source": False,
        "files": [{"path": PAGES_NAME, "sha256": _digest(pages_bytes), "size": len(pages_bytes)}],
        "source_high_water": high_water,
        "content_set_sha256": _digest(_canonical_bytes(content_set)),
    }
    manifest_bytes = _canonical_bytes(manifest)
    marker = {
        "schema_version": 1,
        "contract": MARKER_CONTRACT,
        "snapshot_id": snapshot,
        "manifest_sha256": _digest(manifest_bytes),
        "sanitized": True,
        "disposable": True,
    }
    os.mkdir(candidate)
    try:
        _write_exclusive(candidate / PAGES_NAME, pages_document)
        _write_exclusive(candidate / MANIFEST_NAME, manifest)
        _write_exclusive(candidate / MARKER_NAME, marker)
    except Exception:
        # The caller owns cleanup of a failed newly-created temporary root.
        raise
    return verify_sanitized_snapshot(candidate, allowed_parent=parent)


def _load_exact_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=lambda pairs: _reject_duplicates(pairs),
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotContractError(f"invalid snapshot JSON: {path.name}") from exc
    if raw != _canonical_bytes(value):
        raise SnapshotContractError(f"noncanonical snapshot JSON: {path.name}")
    return value


def _reject_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def verify_sanitized_snapshot(
    root: str | os.PathLike[str], *, allowed_parent: str | os.PathLike[str]
) -> dict[str, Any]:
    candidate = _validated_root(Path(root), Path(allowed_parent), must_exist=True)
    observed = {item.name for item in candidate.iterdir()}
    if observed != EXPECTED_FILES:
        raise SnapshotContractError("snapshot file set mismatch")
    for name in EXPECTED_FILES:
        path = candidate / name
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            raise SnapshotBoundaryError("snapshot files must be regular unlinked files")
    marker = _load_exact_json(candidate / MARKER_NAME)
    manifest = _load_exact_json(candidate / MANIFEST_NAME)
    pages_document = _load_exact_json(candidate / PAGES_NAME)
    if not isinstance(marker, dict) or set(marker) != {
        "schema_version", "contract", "snapshot_id", "manifest_sha256", "sanitized", "disposable"
    }:
        raise SnapshotContractError("snapshot marker shape mismatch")
    if marker.get("schema_version") != 1 or marker.get("contract") != MARKER_CONTRACT or marker.get("sanitized") is not True or marker.get("disposable") is not True:
        raise SnapshotContractError("snapshot marker contract mismatch")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("contract") != SNAPSHOT_CONTRACT or manifest.get("sanitized") is not True or manifest.get("live_source") is not False:
        raise SnapshotContractError("snapshot manifest contract mismatch")
    manifest_sha256 = _digest((candidate / MANIFEST_NAME).read_bytes())
    if marker.get("snapshot_id") != manifest.get("snapshot_id") or marker.get("manifest_sha256") != manifest_sha256:
        raise SnapshotContractError("snapshot marker binding mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or files != [{
        "path": PAGES_NAME,
        "sha256": _digest((candidate / PAGES_NAME).read_bytes()),
        "size": (candidate / PAGES_NAME).stat().st_size,
    }]:
        raise SnapshotContractError("snapshot file manifest mismatch")
    if not isinstance(pages_document, dict) or set(pages_document) != {"schema_version", "pages"} or pages_document.get("schema_version") != 1 or not isinstance(pages_document.get("pages"), list):
        raise SnapshotContractError("pages document shape mismatch")
    owner = _identifier(str(manifest.get("owner_subject_id", "")), field="owner_subject_id")
    normalized = [
        _normalize_page(page, owner_subject_id=owner, index=index)
        for index, page in enumerate(pages_document["pages"])
        if isinstance(page, Mapping)
    ]
    if len(normalized) != len(pages_document["pages"]):
        raise SnapshotContractError("page object required")
    normalized = _topological_pages(normalized)
    if normalized != pages_document["pages"]:
        raise SnapshotContractError("pages must be canonical parent-first order")
    content_set = _content_set(normalized)
    high_water = {
        "page_count": len(normalized),
        "block_count": sum(len(page["blocks"]) for page in normalized),
        "comment_count": sum(len(page["comments"]) for page in normalized),
        "max_updated_at": max((int(page["updated_at"]) for page in normalized), default=0),
        "revision_set_sha256": _digest(_canonical_bytes(content_set)),
    }
    if manifest.get("source_high_water") != high_water or manifest.get("content_set_sha256") != _digest(_canonical_bytes(content_set)):
        raise SnapshotContractError("snapshot high-water or content hash mismatch")
    return {
        "status": "pass",
        "snapshot_id": str(manifest["snapshot_id"]),
        "workspace_id": _identifier(str(manifest.get("workspace_id", "")), field="workspace_id"),
        "owner_subject_id": owner,
        "manifest_sha256": manifest_sha256,
        "content_set_sha256": str(manifest["content_set_sha256"]),
        "source_high_water": high_water,
        "pages": normalized,
        "live_source_open_count": 0,
        "directory_discovery_count": 0,
    }


def import_sanitized_snapshot(
    authority: CanonicalAuthority,
    snapshot_root: str | os.PathLike[str],
    *,
    allowed_parent: str | os.PathLike[str],
    actor_subject_id: str,
    operation_id: str,
    fault: str | None = None,
) -> dict[str, Any]:
    verified = verify_sanitized_snapshot(snapshot_root, allowed_parent=allowed_parent)
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    op_id = _operation_id(operation_id)
    workspace_id = str(verified["workspace_id"])
    action = "collaboration.legacy_snapshot_imported"
    request = {
        "snapshot_id": verified["snapshot_id"],
        "workspace_id": workspace_id,
        "manifest_sha256": verified["manifest_sha256"],
        "source_high_water": verified["source_high_water"],
        "content_set_sha256": verified["content_set_sha256"],
    }
    request_hash = _request_sha256(actor, action, request)
    pages = list(verified["pages"])
    with authority._transaction() as con:
        replay = authority._replay(
            con,
            actor=actor,
            action=action,
            operation_id=op_id,
            request_sha256=request_hash,
            authorize=lambda: authority._require_admin(con, actor, workspace_id),
        )
        if replay is not None:
            return replay
        workspace = authority._require_admin(con, actor, workspace_id)
        if str(workspace["owner_subject_id"]) != str(verified["owner_subject_id"]):
            raise SnapshotImportConflict("snapshot owner does not own the target workspace")
        existing = con.execute(
            "SELECT manifest_sha256,actor_subject_id,operation_id "
            "FROM workspace_collaboration_imports WHERE snapshot_id=?",
            (str(verified["snapshot_id"]),),
        ).fetchone()
        if existing is not None:
            raise SnapshotImportConflict("snapshot_id already imported under another operation")
        page_ids = [str(page["id"]) for page in pages]
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            if con.execute(
                f"SELECT 1 FROM workspace_collaboration_documents WHERE id IN ({placeholders}) LIMIT 1",
                tuple(page_ids),
            ).fetchone():
                raise SnapshotImportConflict("snapshot page already exists in canonical state")
        now = int(time.time())
        block_count = 0
        comment_count = 0
        for page in pages:
            descriptor = _content_descriptor(
                title=str(page["title"]),
                icon=str(page["icon"]),
                cover=str(page["cover"]),
                settings=dict(page["settings"]),
                blocks=list(page["blocks"]),
            )
            content_hash = _sha256(_canonical_json(descriptor))
            con.execute(
                "INSERT INTO workspace_collaboration_documents("
                "id,workspace_id,legacy_owner_subject_id,parent_document_id,title,icon,cover,"
                "settings_json,position,archived,archived_at,revision,document_sequence,"
                "access_epoch,row_version,content_sha256,imported_snapshot_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,1,1,?,?,?,?)",
                (
                    str(page["id"]),
                    workspace_id,
                    str(page["owner_subject_id"]),
                    page["parent_id"],
                    str(page["title"]),
                    str(page["icon"]),
                    str(page["cover"]),
                    _canonical_json(page["settings"]),
                    int(page["position"]),
                    1 if page["archived"] else 0,
                    page["archived_at"],
                    int(page["revision"]),
                    content_hash,
                    str(verified["snapshot_id"]),
                    int(page["created_at"]),
                    int(page["updated_at"]),
                ),
            )
            authority._insert_blocks(con, str(page["id"]), list(page["blocks"]), now=now)
            block_count += len(page["blocks"])
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,?,1,?,?,?,?,?)",
                (
                    str(page["id"]),
                    int(page["revision"]),
                    content_hash,
                    _canonical_json(descriptor),
                    actor,
                    op_id,
                    now,
                ),
            )
            for comment in page["comments"]:
                con.execute(
                    "INSERT INTO workspace_collaboration_comments("
                    "id,document_id,block_id,author_subject_id,content,anchor_json,"
                    "parent_comment_id,reactions_json,resolved,row_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                    (
                        str(comment["id"]),
                        str(page["id"]),
                        comment["block_id"],
                        str(comment["author_subject_id"]),
                        str(comment["content"]),
                        _canonical_json(comment["anchor"]),
                        comment["parent_comment_id"],
                        _canonical_json(comment["reactions"]),
                        1 if comment["resolved"] else 0,
                        int(comment["created_at"]),
                        int(comment["updated_at"]),
                    ),
                )
                comment_count += 1
            for share in page["shares"]:
                subject = str(share["subject_id"])
                if authority._workspace_role(con, workspace_id, subject) is None:
                    existing_guest = con.execute(
                        "SELECT guest_epoch FROM workspace_guests WHERE workspace_id=? AND subject_id=?",
                        (workspace_id, subject),
                    ).fetchone()
                    guest_epoch = 1 if existing_guest is None else int(existing_guest["guest_epoch"]) + 1
                    con.execute(
                        "INSERT INTO workspace_guests(workspace_id,subject_id,guest_epoch,"
                        "invited_by_subject_id,created_at,updated_at,revoked_at) VALUES(?,?,?,?,?,?,NULL) "
                        "ON CONFLICT(workspace_id,subject_id) DO UPDATE SET guest_epoch=excluded.guest_epoch,"
                        "invited_by_subject_id=excluded.invited_by_subject_id,updated_at=excluded.updated_at,revoked_at=NULL",
                        (workspace_id, subject, guest_epoch, actor, now, now),
                    )
                con.execute(
                    "INSERT INTO workspace_collaboration_page_grants("
                    "document_id,subject_id,role,active,grant_epoch,granted_by_subject_id,"
                    "created_at,updated_at,revoked_at) VALUES(?,?,?,1,1,?,?,?,NULL)",
                    (str(page["id"]), subject, str(share["role"]), actor, now, now),
                )
        canonical_set = _content_set(pages)
        canonical_hash = _digest(_canonical_bytes(canonical_set))
        if canonical_hash != verified["content_set_sha256"]:
            raise SnapshotContractError("canonical import hash differs from frozen source")
        con.execute(
            "INSERT INTO workspace_collaboration_imports("
            "snapshot_id,manifest_sha256,workspace_id,actor_subject_id,operation_id,"
            "source_high_water_json,imported_page_count,imported_block_count,"
            "imported_comment_count,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(verified["snapshot_id"]),
                str(verified["manifest_sha256"]),
                workspace_id,
                actor,
                op_id,
                _canonical_json(verified["source_high_water"]),
                len(pages),
                block_count,
                comment_count,
                now,
            ),
        )
        result = {
            "snapshot_id": str(verified["snapshot_id"]),
            "workspace_id": workspace_id,
            "manifest_sha256": str(verified["manifest_sha256"]),
            "source_high_water": dict(verified["source_high_water"]),
            "source_content_set_sha256": str(verified["content_set_sha256"]),
            "canonical_content_set_sha256": canonical_hash,
            "imported_page_count": len(pages),
            "imported_block_count": block_count,
            "imported_comment_count": comment_count,
            "legacy_source_write_count": 0,
            "request_time_directory_scan_count": 0,
        }
        return authority._finish(
            con,
            actor=actor,
            action=action,
            operation_id=op_id,
            request_sha256=request_hash,
            workspace_id=workspace_id,
            document_id=None,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=None,
            document_sequence=None,
            event_type="legacy.snapshot_imported",
            event_payload={
                "snapshot_id": str(verified["snapshot_id"]),
                "manifest_sha256": str(verified["manifest_sha256"]),
                "source_high_water": dict(verified["source_high_water"]),
                "content_set_sha256": canonical_hash,
            },
            result=result,
            fault=fault,
        )


__all__ = [
    "MARKER_NAME",
    "SNAPSHOT_CONTRACT",
    "SnapshotBoundaryError",
    "SnapshotContractError",
    "SnapshotImportConflict",
    "import_sanitized_snapshot",
    "verify_sanitized_snapshot",
    "write_sanitized_snapshot",
]
