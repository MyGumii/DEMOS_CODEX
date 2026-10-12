"""Read-only legacy inventory and guarded NP-2A locator backfill.

The module is deliberately path-injected.  It never imports ``USER_DATA_DIR``,
enumerates user directories, bootstraps a schema, or treats a cross-database
sequence as atomic.  A dry-run is a deterministic approval artifact.  Apply
rebuilds and verifies that artifact, takes a verified catalog backup, and then
uses the workspace store's pending -> source revalidation -> CAS activation
protocol one page at a time.  Resume always requires a newly generated dry-run
and approval of its new checksum/version; an old plan is never rebased silently.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import uuid

from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    WorkspaceStore,
    catalog_path_for_user_data_root,
    logical_database_fingerprint_on_connection,
)


PLAN_VERSION = "np2a.workspace-catalog-backfill.v1"
_ROLE_LEVELS = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
_SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_LEGACY_COLUMNS = {
    "pages": {
        "id", "owner_user_id", "parent_id", "title", "icon", "cover",
        "settings_json", "position", "archived", "archived_at", "created_at",
        "updated_at",
    },
    "blocks": {
        "id", "page_id", "owner_user_id", "position", "block_type", "content",
        "props_json", "created_at", "updated_at",
    },
    "files": {
        "id", "page_id", "owner_user_id", "uploader_user_id", "original_name",
        "stored_name", "rel_path", "mime_type", "size", "created_at",
    },
    "page_shares": {
        "id", "page_id", "owner_user_id", "shared_user_id", "role",
        "created_at", "updated_at", "revoked_at",
    },
    "session_pages": {"session_id", "page_id", "owner_user_id", "created_at"},
    "public_links": {
        "page_id", "owner_user_id", "token", "allow_duplicate", "created_at",
        "updated_at", "revoked_at",
    },
    "page_versions": {
        "id", "page_id", "owner_user_id", "title", "icon", "cover",
        "settings_json", "blocks_json", "created_at",
    },
    "comments": {
        "id", "page_id", "block_id", "owner_user_id", "author_user_id",
        "content", "anchor_json", "parent_id", "reactions_json", "resolved",
        "created_at", "updated_at",
    },
}


class LegacyInventoryBoundsExceeded(Exception):
    """Raised only when an explicitly bounded inventory read exceeds its cap."""

    pass


class WorkspaceInventoryError(RuntimeError):
    """A fail-closed inventory or approval validation error."""


class WorkspaceBackfillFailure(WorkspaceInventoryError):
    """A guarded apply stopped after zero or more catalog-only commits."""

    def __init__(self, message: str, report: dict[str, Any]):
        self.report = report
        super().__init__(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _exact_subject(value: str, field: str) -> str:
    subject = value.strip().lower() if isinstance(value, str) else ""
    if not _SUBJECT_ID_RE.match(subject):
        subject = ""
    if not subject or subject != value:
        raise WorkspaceInventoryError(f"{field} must be an exact sanitized subject ID")
    return subject


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def resolve_inventory_paths(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
) -> dict[str, Path]:
    """Resolve and pin both databases without creating or enumerating anything."""
    owner = _exact_subject(source_owner_id, "source_owner_id")
    raw_root = Path(os.fspath(user_data_root))
    if not raw_root.is_absolute():
        raise WorkspaceInventoryError("user_data_root must be absolute")
    root = raw_root.resolve(strict=True)
    if not root.is_dir():
        raise WorkspaceInventoryError("user_data_root must be an existing directory")

    expected_source_raw = Path(os.fspath(expected_source_db_path))
    expected_catalog_raw = Path(os.fspath(expected_catalog_db_path))
    if not expected_source_raw.is_absolute() or not expected_catalog_raw.is_absolute():
        raise WorkspaceInventoryError("expected database paths must be absolute")

    derived_source = (root / owner / "notion" / "notion.db").resolve(strict=False)
    derived_catalog = Path(catalog_path_for_user_data_root(root)).resolve(strict=False)
    expected_source = expected_source_raw.resolve(strict=False)
    expected_catalog = expected_catalog_raw.resolve(strict=False)
    if not _same_path(derived_source, expected_source):
        raise WorkspaceInventoryError("expected_source_db_path does not match the exact owner shard")
    if not _same_path(derived_catalog, expected_catalog):
        raise WorkspaceInventoryError("expected_catalog_db_path does not match the exact catalog")
    if not _inside(expected_source, root) or not _inside(expected_catalog, root):
        raise WorkspaceInventoryError("database path escapes user_data_root")
    if not expected_source.is_file():
        raise WorkspaceInventoryError("legacy source database does not exist")
    if not expected_catalog.is_file():
        raise WorkspaceInventoryError("workspace catalog database does not exist")
    # Resolve existing files again to fence symlink/junction targets.
    source = expected_source.resolve(strict=True)
    catalog = expected_catalog.resolve(strict=True)
    if not _inside(source, root) or not _inside(catalog, root):
        raise WorkspaceInventoryError("resolved database target escapes user_data_root")
    if not _same_path(source, expected_source) or not _same_path(catalog, expected_catalog):
        raise WorkspaceInventoryError("expected database path is not its exact resolved target")
    return {"root": root, "source": source, "catalog": catalog}


def _open_read_only(path: Path) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA query_only=ON")
        return con
    except sqlite3.Error as exc:
        raise WorkspaceInventoryError("database could not be opened read-only") from exc


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def logical_database_fingerprint(con: sqlite3.Connection) -> str:
    """Hash logical schema and typed row content, not unstable file bytes."""
    return logical_database_fingerprint_on_connection(con)


def _validate_legacy_schema(con: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table, required in _LEGACY_COLUMNS.items():
        if table not in tables:
            raise WorkspaceInventoryError("legacy source schema is not ready")
        columns = {
            str(row["name"])
            for row in con.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        }
        if not required.issubset(columns):
            raise WorkspaceInventoryError("legacy source schema is not ready")
    if [str(row[0]) for row in con.execute("PRAGMA quick_check").fetchall()] != ["ok"]:
        raise WorkspaceInventoryError("legacy source quick_check failed")
    if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise WorkspaceInventoryError("legacy source foreign-key check failed")


def _json_load(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _inherited_share_role(
    con: sqlite3.Connection,
    page_id: str,
    actor: str,
    cache: dict[str, str] | None = None,
) -> str:
    memo = cache if cache is not None else {}
    current = page_id
    trail: list[str] = []
    visited: set[str] = set()
    role = ""
    while current and current not in visited:
        if current in memo:
            role = memo[current]
            break
        visited.add(current)
        trail.append(current)
        row = con.execute(
            """
            SELECT p.parent_id,s.role FROM pages p
            LEFT JOIN page_shares s
              ON s.page_id=p.id AND s.shared_user_id=? AND s.revoked_at IS NULL
            WHERE p.id=? AND p.archived=0
            """,
            (actor, current),
        ).fetchone()
        if not row:
            break
        if row["role"]:
            role = str(row["role"])
            break
        current = str(row["parent_id"] or "")
    for item in trail:
        memo[item] = role
    return role


def _page_proof(
    con: sqlite3.Connection,
    *,
    actor: str,
    owner: str,
    page: sqlite3.Row,
    role: str,
    reference_limit: int | None = None,
    linked_metadata_byte_limit: int | None = None,
    role_resolver: Callable[
        [sqlite3.Connection, str, str, dict[str, str]], str
    ] | None = None,
) -> LegacyPageSourceProof:
    page_row = dict(page)
    page_id = str(page_row["id"])
    settings = _json_load(page_row.get("settings_json"), {})
    page_summary: dict[str, Any] = {
        "id": page_id,
        "owner_user_id": owner,
        "parent_id": page_row.get("parent_id") or "",
        "title": page_row.get("title") or "Untitled",
        "icon": page_row.get("icon") or "",
        "cover": page_row.get("cover") or "",
        "settings": settings if isinstance(settings, dict) else {},
        "position": int(page_row.get("position") or 0),
        "created_at": int(page_row.get("created_at") or 0),
        "updated_at": int(page_row.get("updated_at") or 0),
        "archived_at": int(page_row.get("archived_at") or 0),
        "role": role,
        "scope": "own" if actor == owner else "shared",
        "can_edit": role in {"owner", "edit"},
        "can_share": role == "owner",
    }
    if "revision" in page_row:
        page_summary["revision"] = max(1, int(page_row.get("revision") or 1))

    blocks = []
    for row in con.execute(
        "SELECT * FROM blocks WHERE page_id=? ORDER BY position", (page_id,)
    ).fetchall():
        raw = dict(row)
        blocks.append({
            "id": raw["id"],
            "type": raw.get("block_type") or "paragraph",
            "content": raw.get("content") or "",
            "props": _json_load(raw.get("props_json"), {}),
            "position": int(raw.get("position") or 0),
            "created_at": int(raw.get("created_at") or 0),
            "updated_at": int(raw.get("updated_at") or 0),
        })
    _hydrate_references(
        con,
        blocks,
        actor=actor,
        owner=owner,
        reference_limit=reference_limit,
        linked_metadata_byte_limit=linked_metadata_byte_limit,
        role_resolver=role_resolver,
    )

    files = []
    for row in con.execute(
        "SELECT * FROM files WHERE page_id=? ORDER BY created_at DESC", (page_id,)
    ).fetchall():
        raw = dict(row)
        files.append({
            "id": raw["id"],
            "page_id": raw["page_id"],
            "owner_user_id": owner,
            "uploader_user_id": raw.get("uploader_user_id") or owner,
            "original_name": raw.get("original_name") or raw.get("stored_name") or "",
            "stored_name": raw.get("stored_name") or "",
            "rel_path": raw.get("rel_path") or "",
            "mime_type": raw.get("mime_type") or "",
            "size": int(raw.get("size") or 0),
            "created_at": int(raw.get("created_at") or 0),
            "url": f"/api/notion/files/{raw['id']}",
        })

    revision = max(1, int(page_summary.get("revision") or 1))
    updated_at = max(0, int(page_summary.get("updated_at") or 0))
    identity = {
        "actor_subject_id": actor,
        "owner_subject_id": owner,
        "page_id": page_id,
        "revision": revision,
        "updated_at": updated_at,
        "page": {
            key: page_summary.get(key)
            for key in (
                "id", "parent_id", "title", "icon", "cover", "settings",
                "position", "created_at", "updated_at", "revision",
            )
        },
        "blocks": blocks,
        "files": [
            {
                key: item.get(key)
                for key in (
                    "id", "page_id", "original_name", "mime_type", "size",
                    "created_at",
                )
            }
            for item in files
        ],
    }
    return LegacyPageSourceProof(
        actor_subject_id=actor,
        owner_subject_id=owner,
        page_id=page_id,
        revision=revision,
        updated_at=updated_at,
        content_sha256=_sha256_json(identity),
    )


def _hydrate_references(
    con: sqlite3.Connection,
    blocks: list[dict[str, Any]],
    *,
    actor: str,
    owner: str,
    reference_limit: int | None = None,
    linked_metadata_byte_limit: int | None = None,
    role_resolver: Callable[
        [sqlite3.Connection, str, str, dict[str, str]], str
    ] | None = None,
) -> None:
    referenced: set[str] = set()
    for block in blocks:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        if props.get("page_id"):
            referenced.add(str(props["page_id"]))
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        referenced.update(
            str(item["page_id"])
            for item in rows
            if isinstance(item, dict) and item.get("page_id")
        )
    if not referenced:
        return
    if reference_limit is not None and len(referenced) > reference_limit:
        raise LegacyInventoryBoundsExceeded(
            "legacy linked reference count exceeds the bounded contract"
        )
    placeholders = ",".join("?" for _ in referenced)
    ordered_references = tuple(sorted(referenced))
    if linked_metadata_byte_limit is not None:
        linked_stats = con.execute(
            f"""
            SELECT COALESCE(SUM(
              length(CAST(COALESCE(id,'') AS BLOB)) +
              length(CAST(COALESCE(title,'') AS BLOB)) +
              length(CAST(COALESCE(icon,'') AS BLOB))),0)
            FROM pages WHERE id IN ({placeholders})
            """,
            ordered_references,
        ).fetchone()
        if int(linked_stats[0] or 0) > linked_metadata_byte_limit:
            raise LegacyInventoryBoundsExceeded(
                "legacy linked metadata exceeds the bounded contract"
            )
    linked = {
        str(row["id"]): dict(row)
        for row in con.execute(
            f"SELECT id,title,icon,updated_at,archived FROM pages WHERE id IN ({placeholders})",
            ordered_references,
        ).fetchall()
    }
    role_cache: dict[str, str] = {}

    def discoverable(page_id: str) -> bool:
        if not page_id or page_id not in linked:
            return False
        resolver = role_resolver or _inherited_share_role
        return actor == owner or bool(resolver(con, page_id, actor, role_cache))

    def redact(item: dict[str, Any], title_field: str) -> None:
        item[title_field] = "Unavailable page"
        item["title"] = "Unavailable page"
        item["icon"] = ""
        item.pop("archived", None)
        item["inaccessible"] = True

    for block in blocks:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        properties = props.get("properties") if isinstance(props.get("properties"), list) else []
        title_field = next(
            (str(item.get("id")) for item in properties
             if isinstance(item, dict) and item.get("type") == "title" and item.get("id")),
            "title",
        )
        page_ref = str(props.get("page_id") or "")
        linked_page = linked.get(page_ref)
        allowed = discoverable(page_ref)
        if linked_page and allowed and block.get("type") == "child_page":
            block["content"] = linked_page.get("title") or "Untitled"
            props["title"] = block["content"]
            props["icon"] = linked_page.get("icon") or ""
            props["archived"] = bool(linked_page.get("archived"))
            props.pop("inaccessible", None)
        elif page_ref and block.get("type") in {"child_page", "link_to_page"} and not allowed:
            block["content"] = "Unavailable page"
            props["title"] = "Unavailable page"
            props["icon"] = ""
            props.pop("archived", None)
            props["inaccessible"] = True
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        for item in rows:
            if not isinstance(item, dict):
                continue
            row_ref = str(item.get("page_id") or "")
            linked_page = linked.get(row_ref)
            if linked_page and discoverable(row_ref):
                item[title_field] = linked_page.get("title") or "Untitled"
                item["title"] = linked_page.get("title") or "Untitled"
                item["icon"] = linked_page.get("icon") or ""
                item["archived"] = bool(linked_page.get("archived"))
                item.pop("inaccessible", None)
            elif row_ref:
                redact(item, title_field)


def inspect_legacy_source(path: Path, *, actor: str, owner: str) -> dict[str, Any]:
    con = _open_read_only(path)
    try:
        con.execute("BEGIN")
        _validate_legacy_schema(con)
        owner_mismatch = 0
        for table in (
            "pages", "blocks", "files", "page_shares", "session_pages",
            "public_links", "page_versions", "comments",
        ):
            owner_mismatch += int(con.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)} WHERE owner_user_id<>?",
                (owner,),
            ).fetchone()[0])
        if owner_mismatch:
            raise WorkspaceInventoryError("legacy source contains rows for another owner")
        fingerprint = logical_database_fingerprint(con)
        active_rows = con.execute(
            "SELECT * FROM pages WHERE archived=0 ORDER BY id"
        ).fetchall()
        archived_count = int(
            con.execute("SELECT COUNT(*) FROM pages WHERE archived=1").fetchone()[0]
        )
        role_cache: dict[str, str] = {}
        pages: list[dict[str, Any]] = []
        for page in active_rows:
            page_id = str(page["id"])
            role = "owner" if actor == owner else _inherited_share_role(
                con, page_id, actor, role_cache
            )
            if not role or _ROLE_LEVELS.get(role, 0) < _ROLE_LEVELS["view"]:
                pages.append({"page_id": page_id, "access": "blocked_legacy_access"})
                continue
            proof = _page_proof(con, actor=actor, owner=owner, page=page, role=role)
            pages.append({"page_id": page_id, "access": "authorized", "proof": proof.descriptor()})
        con.rollback()
        return {
            "database_fingerprint": fingerprint,
            "quick_check": "ok",
            "foreign_key_violation_count": 0,
            "active_page_count": len(active_rows),
            "archived_page_count": archived_count,
            "authorized_active_page_count": sum(item["access"] == "authorized" for item in pages),
            "blocked_active_page_count": sum(item["access"] != "authorized" for item in pages),
            "pages": pages,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _catalog_snapshot(path: Path, *, actor: str, owner: str, workspace_id: str) -> dict[str, Any]:
    store = WorkspaceStore(path)
    health = store.verify_deep_health()
    con = _open_read_only(path)
    try:
        con.execute("BEGIN")
        workspace = con.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not workspace:
            raise WorkspaceInventoryError("workspace does not exist")
        actor_membership = con.execute(
            """
            SELECT role,membership_epoch FROM workspace_memberships
            WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL
            """,
            (workspace_id, actor),
        ).fetchone()
        if not actor_membership:
            raise WorkspaceInventoryError("actor is not an active workspace member")
        owner_membership = con.execute(
            """
            SELECT role,membership_epoch FROM workspace_memberships
            WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL
            """,
            (workspace_id, owner),
        ).fetchone()
        if not owner_membership:
            raise WorkspaceInventoryError("source owner is not an active workspace member")
        fingerprint = logical_database_fingerprint(con)
        bindings = {
            str(row["source_page_id"]): dict(row)
            for row in con.execute(
                """
                SELECT * FROM workspace_page_sources
                WHERE source_owner_subject_id=? ORDER BY source_page_id
                """,
                (owner,),
            ).fetchall()
        }
        con.rollback()
        return {
            "database_fingerprint": fingerprint,
            "workspace_version": int(workspace["row_version"]),
            "workspace_security_epoch": int(workspace["security_epoch"]),
            "actor_membership_epoch": int(actor_membership["membership_epoch"]),
            "actor_role": str(actor_membership["role"]),
            "source_owner_membership_epoch": int(owner_membership["membership_epoch"]),
            "health": health,
            "bindings": bindings,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _binding_action(
    binding: dict[str, Any] | None,
    *,
    workspace_id: str,
    proof: dict[str, Any] | None,
) -> str:
    if proof is None:
        return "blocked_legacy_access"
    if binding is None:
        return "stage_activate"
    if str(binding["workspace_id"]) != workspace_id:
        return "blocked_other_workspace"
    matches = (
        str(binding["placement"]) == "private"
        and binding["teamspace_id"] is None
        and int(binding["source_revision"]) == int(proof["revision"])
        and int(binding["source_updated_at"]) == int(proof["updated_at"])
        and str(binding["source_sha256"]) == str(proof["content_sha256"])
    )
    state = str(binding["state"])
    if matches and state == "active":
        return "already_active"
    if matches and state == "pending":
        return "activate_pending"
    return "restage_activate"


def build_backfill_plan(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    actor_subject_id: str,
    workspace_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a deterministic, secret-free, strictly read-only plan."""
    owner = _exact_subject(source_owner_id, "source_owner_id")
    actor = _exact_subject(actor_subject_id, "actor_subject_id")
    if not re.fullmatch(r"ws_[A-Za-z0-9_-]{1,93}", str(workspace_id or "")):
        raise WorkspaceInventoryError("workspace_id is invalid")
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=owner,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    source = inspect_legacy_source(paths["source"], actor=actor, owner=owner)
    catalog = _catalog_snapshot(
        paths["catalog"], actor=actor, owner=owner, workspace_id=workspace_id
    )
    catalog_permission = actor == owner or catalog["actor_role"] in {
        "membership_admin", "owner"
    }
    pages: list[dict[str, Any]] = []
    for number, source_page in enumerate(source["pages"], start=1):
        page_id = str(source_page["page_id"])
        proof = source_page.get("proof")
        binding = catalog["bindings"].get(page_id)
        action = (
            _binding_action(binding, workspace_id=workspace_id, proof=proof)
            if catalog_permission
            else "blocked_catalog_permission"
        )
        item: dict[str, Any] = {
            "number": number,
            "page_id": page_id,
            "action": action,
        }
        if proof is not None:
            item["proof"] = proof
        if binding is not None and str(binding["workspace_id"]) == workspace_id:
            item["existing_binding"] = {
                "state": str(binding["state"]),
                "version": int(binding["row_version"]),
            }
        pages.append(item)
    blocked = sum(item["action"].startswith("blocked_") for item in pages)
    base = {
        "plan_version": PLAN_VERSION,
        "user_data_root": os.fspath(paths["root"]),
        "source_owner_id": owner,
        "actor_subject_id": actor,
        "workspace_id": workspace_id,
        "source_db_path": os.fspath(paths["source"]),
        "catalog_db_path": os.fspath(paths["catalog"]),
        "source": {
            key: source[key]
            for key in (
                "database_fingerprint", "quick_check", "foreign_key_violation_count",
                "active_page_count", "archived_page_count",
                "authorized_active_page_count", "blocked_active_page_count",
            )
        },
        "catalog": {
            "database_fingerprint": catalog["database_fingerprint"],
            "workspace_version": catalog["workspace_version"],
            "workspace_security_epoch": catalog["workspace_security_epoch"],
            "actor_membership_epoch": catalog["actor_membership_epoch"],
            "source_owner_membership_epoch": catalog["source_owner_membership_epoch"],
            "deep_health": catalog["health"],
        },
        "placement": {"scope": "private", "teamspace_id": ""},
        "pages": pages,
        "summary": {
            "apply_ready": blocked == 0,
            "blocked_count": blocked,
            "already_active_count": sum(item["action"] == "already_active" for item in pages),
            "would_change_count": sum(
                item["action"] not in {"already_active"} and not item["action"].startswith("blocked_")
                for item in pages
            ),
        },
        "atomicity": "catalog commands are atomic; the legacy/catalog sequence is resumable, not cross-database atomic",
        "resume_policy": "generate a new dry-run and approve its new checksum/version",
    }
    return {**base, "plan_checksum": _sha256_json(base)}


def verify_plan_checksum(plan: dict[str, Any]) -> bool:
    if not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION:
        return False
    body = {key: value for key, value in plan.items() if key != "plan_checksum"}
    return str(plan.get("plan_checksum") or "") == _sha256_json(body)


def _proof_from_descriptor(value: dict[str, Any]) -> LegacyPageSourceProof:
    return LegacyPageSourceProof(
        actor_subject_id=str(value["actor_subject_id"]),
        owner_subject_id=str(value["owner_subject_id"]),
        page_id=str(value["page_id"]),
        revision=int(value["revision"]),
        updated_at=int(value["updated_at"]),
        content_sha256=str(value["content_sha256"]),
    )


def _revalidate_exact_source(path: Path, proof: LegacyPageSourceProof) -> bool:
    try:
        snapshot = inspect_legacy_source(
            path, actor=proof.actor_subject_id, owner=proof.owner_subject_id
        )
    except Exception:
        return False
    current = next(
        (item.get("proof") for item in snapshot["pages"] if item["page_id"] == proof.page_id),
        None,
    )
    return current == proof.descriptor()


def _operation_id(kind: str, plan_checksum: str, page: dict[str, Any], version: int) -> str:
    payload = {
        "kind": kind,
        "plan_checksum": plan_checksum,
        "page_id": page["page_id"],
        "proof": page.get("proof"),
        "base_version": version,
    }
    return f"np2a.backfill.{kind}." + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()[:48]


def _reserve_backup_path(catalog: Path, plan_checksum: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = (
        f"{catalog.name}.np2a-backfill.{timestamp}."
        f"{plan_checksum.removeprefix('sha256:')[:12]}.{uuid.uuid4().hex[:8]}.bak"
    )
    target = catalog.with_name(name)
    descriptor = os.open(os.fspath(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    return target


def _raw_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _backup_catalog(catalog: Path, plan_checksum: str) -> dict[str, Any]:
    backup = _reserve_backup_path(catalog, plan_checksum)
    try:
        source = _open_read_only(catalog)
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        health = WorkspaceStore(backup).verify_deep_health()
        con = _open_read_only(backup)
        try:
            logical = logical_database_fingerprint(con)
        finally:
            con.close()
        return {
            "created": True,
            "path": os.fspath(backup),
            "sha256": _raw_file_sha256(backup),
            "database_fingerprint": logical,
            "deep_health": health,
        }
    except Exception:
        try:
            backup.unlink()
        except OSError:
            pass
        raise


def _fresh_plan_from(plan: dict[str, Any]) -> dict[str, Any]:
    return build_backfill_plan(
        user_data_root=plan["user_data_root"],
        source_owner_id=plan["source_owner_id"],
        actor_subject_id=plan["actor_subject_id"],
        workspace_id=plan["workspace_id"],
        expected_source_db_path=plan["source_db_path"],
        expected_catalog_db_path=plan["catalog_db_path"],
    )


def _safe_health(store: WorkspaceStore) -> dict[str, Any]:
    try:
        return store.deep_health()
    except Exception as exc:
        return {"healthy": False, "issues": ["health_check_failed"], "error_type": type(exc).__name__}


def apply_backfill_plan(
    plan: dict[str, Any],
    *,
    backup_confirmed: bool,
    source_quiesced_confirmed: bool,
    confirm_checksum: str,
    confirm_workspace_version: int,
    failure_injector: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Apply a verified plan with resumable per-page catalog transactions."""
    if not verify_plan_checksum(plan):
        raise WorkspaceInventoryError("plan checksum is invalid")
    if not backup_confirmed:
        raise WorkspaceInventoryError("apply requires backup_confirmed")
    if not source_quiesced_confirmed:
        raise WorkspaceInventoryError("apply requires source_quiesced_confirmed")
    if confirm_checksum != plan["plan_checksum"]:
        raise WorkspaceInventoryError("confirm_checksum does not match the plan")
    if isinstance(confirm_workspace_version, bool) or not isinstance(confirm_workspace_version, int):
        raise WorkspaceInventoryError("confirm_workspace_version must be an integer")
    if confirm_workspace_version != int(plan["catalog"]["workspace_version"]):
        raise WorkspaceInventoryError("confirm_workspace_version does not match the plan")
    if not bool(plan["summary"]["apply_ready"]):
        raise WorkspaceInventoryError("plan contains blocked pages")

    fresh = _fresh_plan_from(plan)
    if fresh["plan_checksum"] != plan["plan_checksum"]:
        raise WorkspaceInventoryError("source or catalog changed after the approved dry-run")

    catalog_path = Path(plan["catalog_db_path"])
    source_path = Path(plan["source_db_path"])
    actor = str(plan["actor_subject_id"])
    owner = str(plan["source_owner_id"])
    workspace_id = str(plan["workspace_id"])
    store = WorkspaceStore(catalog_path)
    backup = _backup_catalog(catalog_path, str(plan["plan_checksum"]))
    if backup["database_fingerprint"] != plan["catalog"]["database_fingerprint"]:
        raise WorkspaceInventoryError("catalog changed while the verified backup was taken")

    page_reports = [
        {"number": item["number"], "page_id": item["page_id"], "status": "not_attempted"}
        for item in plan["pages"]
    ]
    report: dict[str, Any] = {
        "mode": "apply",
        "status": "running",
        "plan_checksum": plan["plan_checksum"],
        "workspace_id": workspace_id,
        "source_owner_id": owner,
        "atomicity": plan["atomicity"],
        "resume_policy": plan["resume_policy"],
        "backup": backup,
        "pages": page_reports,
    }
    expected_catalog_fingerprint = str(plan["catalog"]["database_fingerprint"])
    expected_workspace_version = int(plan["catalog"]["workspace_version"])
    expected_source_fingerprint = str(plan["source"]["database_fingerprint"])

    try:
        for index, item in enumerate(plan["pages"]):
            page_report = page_reports[index]
            if item["action"] == "already_active":
                page_report["status"] = "already_active"
                continue
            source_now = inspect_legacy_source(source_path, actor=actor, owner=owner)
            if source_now["database_fingerprint"] != expected_source_fingerprint:
                raise WorkspaceInventoryError("legacy source changed during apply")
            descriptor = next(
                (candidate.get("proof") for candidate in source_now["pages"]
                 if candidate["page_id"] == item["page_id"]),
                None,
            )
            if descriptor != item.get("proof"):
                raise WorkspaceInventoryError("legacy page proof changed during apply")
            proof = _proof_from_descriptor(item["proof"])

            catalog_now = _catalog_snapshot(
                catalog_path, actor=actor, owner=owner, workspace_id=workspace_id
            )
            if catalog_now["database_fingerprint"] != expected_catalog_fingerprint:
                raise WorkspaceInventoryError("workspace catalog changed during apply")
            if catalog_now["workspace_version"] != expected_workspace_version:
                raise WorkspaceInventoryError("workspace version changed during apply")

            if item["action"] == "activate_pending":
                binding_version = int(item["existing_binding"]["version"])
            else:
                stage = store.stage_page_source_binding(
                    actor,
                    workspace_id,
                    proof,
                    base_version=expected_workspace_version,
                    operation_id=_operation_id(
                        "stage", str(plan["plan_checksum"]), item, expected_workspace_version
                    ),
                    expected_catalog_fingerprint=expected_catalog_fingerprint,
                )
                binding_version = int(stage["binding"]["version"])
                expected_workspace_version = int(stage["workspace"]["version"])
                page_report.update(status="pending", binding_version=binding_version)
                staged_snapshot = _catalog_snapshot(
                    catalog_path, actor=actor, owner=owner, workspace_id=workspace_id
                )
                if staged_snapshot["workspace_version"] != expected_workspace_version:
                    raise WorkspaceInventoryError("workspace changed after pending stage")
                expected_catalog_fingerprint = str(staged_snapshot["database_fingerprint"])
                if failure_injector:
                    failure_injector("after_stage", dict(page_report))

            activated = store.activate_page_source_binding(
                actor,
                workspace_id,
                proof,
                source_revalidator=lambda candidate: _revalidate_exact_source(
                    source_path, candidate
                ),
                expected_binding_version=binding_version,
                base_version=expected_workspace_version,
                operation_id=_operation_id(
                    "activate", str(plan["plan_checksum"]), item, expected_workspace_version
                ),
                expected_catalog_fingerprint=expected_catalog_fingerprint,
            )
            expected_workspace_version = int(activated["workspace"]["version"])
            page_report.update(status="active", binding_version=binding_version)
            activated_snapshot = _catalog_snapshot(
                catalog_path, actor=actor, owner=owner, workspace_id=workspace_id
            )
            if activated_snapshot["workspace_version"] != expected_workspace_version:
                raise WorkspaceInventoryError("workspace changed after binding activation")
            expected_catalog_fingerprint = str(activated_snapshot["database_fingerprint"])
            if inspect_legacy_source(
                source_path, actor=actor, owner=owner
            )["database_fingerprint"] != expected_source_fingerprint:
                raise WorkspaceInventoryError("legacy source changed after binding activation")
            if failure_injector:
                failure_injector("after_activate", dict(page_report))

        final_catalog = _catalog_snapshot(
            catalog_path, actor=actor, owner=owner, workspace_id=workspace_id
        )
        final_source = inspect_legacy_source(source_path, actor=actor, owner=owner)
        if final_catalog["database_fingerprint"] != expected_catalog_fingerprint:
            raise WorkspaceInventoryError("workspace catalog changed before postcondition")
        if final_catalog["workspace_version"] != expected_workspace_version:
            raise WorkspaceInventoryError("workspace version changed before postcondition")
        if final_source["database_fingerprint"] != expected_source_fingerprint:
            raise WorkspaceInventoryError("legacy source changed before postcondition")
        for item in plan["pages"]:
            binding = final_catalog["bindings"].get(str(item["page_id"]))
            proof = item.get("proof") or {}
            if not binding or not (
                str(binding["workspace_id"]) == workspace_id
                and str(binding["state"]) == "active"
                and int(binding["source_revision"]) == int(proof["revision"])
                and int(binding["source_updated_at"]) == int(proof["updated_at"])
                and str(binding["source_sha256"]) == str(proof["content_sha256"])
            ):
                raise WorkspaceInventoryError("backfill postcondition failed")
        report.update(
            status="completed",
            changed_count=sum(item["status"] == "active" for item in page_reports),
            already_active_count=sum(item["status"] == "already_active" for item in page_reports),
            postcondition={
                "all_planned_pages_active": True,
                "workspace_version": final_catalog["workspace_version"],
                "workspace_security_epoch": final_catalog["workspace_security_epoch"],
                "catalog_database_fingerprint": final_catalog["database_fingerprint"],
                "source_database_fingerprint": final_source["database_fingerprint"],
                "deep_health": final_catalog["health"],
            },
        )
        return report
    except Exception as exc:
        current_bindings: dict[str, dict[str, Any]] = {}
        try:
            current_bindings = _catalog_snapshot(
                catalog_path, actor=actor, owner=owner, workspace_id=workspace_id
            )["bindings"]
        except Exception:
            pass
        for page_report in page_reports:
            if page_report["status"] == "not_attempted":
                binding = current_bindings.get(str(page_report["page_id"]))
                if binding and str(binding["workspace_id"]) == workspace_id:
                    page_report["status"] = f"existing_{binding['state']}"
            elif page_report["status"] == "pending":
                binding = current_bindings.get(str(page_report["page_id"]))
                page_report["status"] = (
                    f"{binding['state']}_after_failure" if binding else "missing_after_failure"
                )
        report.update(
            status="partial_failure",
            error={"type": type(exc).__name__, "code": "backfill_stopped"},
            postcondition={
                "all_planned_pages_active": False,
                "deep_health": _safe_health(store),
                "resume": "run a new dry-run and approve its new checksum/version",
            },
        )
        raise WorkspaceBackfillFailure("guarded backfill stopped", report) from exc


def dry_run_report(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        **plan,
        "backup": {"created": False},
        "apply_requirements": {
            "apply": True,
            "backup_confirmed": True,
            "source_quiesced_confirmed": True,
            "confirm_checksum": plan["plan_checksum"],
            "confirm_workspace_version": plan["catalog"]["workspace_version"],
        },
    }
