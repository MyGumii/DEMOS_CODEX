"""
Account-scoped Notion-like workspace store.

This module intentionally keeps the workspace local and dependency-free:
SQLite for metadata, files under user_data/<user>/notion/files, and markdown
as the interchange format for chat context and Second Brain publication.
"""
from __future__ import annotations

import hashlib
import csv
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from code_assist_v1.auth import safe_user_id
from code_assist_v1.config import USER_DATA_DIR
from code_assist_v1.notion_update_preview import page_change_preview

_bound_user_data_dir = ContextVar("notion_store_user_data_dir", default=None)
_bound_temporary_unlock = ContextVar("notion_store_temporary_unlock", default=None)


@contextmanager
def bind_user_data_root(root):
    """Server-configured per-call store binding; never change shared globals."""
    chosen = Path(root)
    if not chosen.is_absolute():
        raise ValueError("Notion user data root must be absolute")
    chosen = chosen.resolve(strict=True)
    if not chosen.is_dir() or chosen.parent == chosen:
        raise ValueError("Notion user data root must be an existing directory")
    token = _bound_user_data_dir.set(os.fspath(chosen))
    try:
        yield
    finally:
        _bound_user_data_dir.reset(token)


def _user_data_dir():
    return _bound_user_data_dir.get() or USER_DATA_DIR


@contextmanager
def bind_temporary_unlock(grant):
    """Bind a route-verified, tab-local page unlock to this request only."""
    token = _bound_temporary_unlock.set(grant)
    try:
        yield
    finally:
        _bound_temporary_unlock.reset(token)


def _page_write_locked(settings, requester_user_id: str, page_id: str) -> bool:
    if not isinstance(settings, dict) or not settings.get("locked"):
        return False
    grant = _bound_temporary_unlock.get()
    return not (
        isinstance(grant, dict)
        and grant.get("user_id") == requester_user_id
        and grant.get("page_id") == page_id
        and grant.get("lock_nonce") == str(settings.get("_np2b_lock_nonce") or "")
    )


def temporary_unlock_nonce(user_id: str | None, page_id: str) -> str:
    """Read the current lock generation after checking current edit access."""
    uid = _uid(user_id)
    _owner, role, page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    settings = _json_load(page.get("settings_json"), {})
    if not isinstance(settings, dict) or not settings.get("locked"):
        raise ValueError("page is not locked")
    return str(settings.get("_np2b_lock_nonce") or "")


ROLE_LEVELS = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
EDIT_ROLES = {"owner", "edit"}
BLOCK_TYPES = {
    "paragraph",
    "heading1",
    "heading2",
    "heading3",
    "heading4",
    "toggle_heading1",
    "toggle_heading2",
    "toggle_heading3",
    "toggle_heading4",
    "todo",
    "toggle",
    "callout",
    "quote",
    "code",
    "equation",
    "ai_notes",
    "bullet",
    "numbered",
    "divider",
    "image",
    "video",
    "audio",
    "pdf",
    "file",
    "bookmark",
    "embed",
    "table_of_contents",
    "child_page",
    "table",
    "board",
    "list",
    "gallery",
    "calendar",
    "timeline",
    "chart",
    "dashboard",
    "form",
    "feed",
    "map",
    "breadcrumb",
    "link_to_page",
    "simple_table",
    "button",
    "synced_block",
    "tabs",
}
IMAGE_MIME_PREFIX = "image/"
MAX_CONTEXT_CHARS = 12000
FORM_WRITABLE_PROPERTY_TYPES = {
    "title", "text", "number", "select", "status", "multi_select", "date",
    "person", "url", "email", "phone", "checkbox", "place", "relation",
}
ATOMIC_SAVE_MIGRATION = (
    2026081201,
    "notion_atomic_page_save_v1",
    "sha256:1cb4db2215eac0b8bd160e823b40b375bb8e2a33c7c281691b877b1fd90bc0f6",
)


class PageRevisionConflict(Exception):
    """Raised when a compare-and-swap save targets an old page revision."""

    def __init__(self, current_revision: int):
        self.current_revision = int(current_revision)
        super().__init__(f"page revision conflict; current revision is {self.current_revision}")


class OperationIdConflict(Exception):
    """Raised when an idempotency key is reused for a different mutation."""

    def __init__(self, operation_id: str, current_revision: int):
        self.operation_id = str(operation_id)
        self.current_revision = int(current_revision)
        super().__init__("operation_id was already used for a different request")


class AtomicSaveUnavailable(Exception):
    """Raised until an existing workspace receives the explicit atomic-save migration."""

    pass


def _now() -> int:
    return int(time.time())


def _uid(user_id: str | None) -> str:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("authenticated user required")
    return uid


def _workspace_root(user_id: str | None, *, create: bool = False) -> str:
    uid = _uid(user_id)
    root = os.path.join(_user_data_dir(), uid, "notion")
    if create:
        os.makedirs(os.path.join(root, "files"), exist_ok=True)
    return root


def _db_path(user_id: str | None) -> str:
    return os.path.join(_workspace_root(user_id), "notion.db")


def _files_root(owner_user_id: str | None, *, create: bool = False) -> str:
    path = os.path.join(_workspace_root(owner_user_id), "files")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _database_exists(user_id: str | None) -> bool:
    db_path = _db_path(user_id)
    return os.path.isfile(db_path) and os.path.getsize(db_path) > 0


def _new_incarnation_id() -> str:
    """Issue one opaque legacy-page incarnation ID for v2 source triggers."""
    return "inc_" + uuid.uuid4().hex


def _configure_connection(con: sqlite3.Connection, *, query_only: bool) -> sqlite3.Connection:
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    # Legacy incarnation-v2 triggers call this function.  Register it on every
    # application connection so a migrated shard can keep ordinary page
    # creation atomic; unconfigured/raw writers fail closed with no such
    # function instead of manufacturing an identity.
    con.create_function("notion_new_incarnation_id", 0, _new_incarnation_id)
    # The frozen v2 history triggers additionally require an explicitly
    # configured application connection.  A raw sqlite3 connection has no
    # ambient write context and therefore cannot insert, retire, or forge
    # incarnation history rows.
    con.create_function("notion_incarnation_write_context", 0, lambda: 1)
    if query_only:
        con.execute("PRAGMA query_only=ON")
    return con


def _connect(user_id: str | None) -> sqlite3.Connection:
    """Open an existing workspace read-only without filesystem/schema side effects."""
    db_path = _db_path(user_id)
    if not _database_exists(user_id):
        raise FileNotFoundError("workspace database does not exist")
    read_uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    return _configure_connection(sqlite3.connect(read_uri, uri=True), query_only=True)


def bootstrap_workspace(user_id: str | None) -> dict[str, Any]:
    """Explicitly create the latest schema for a brand-new Notion workspace.

    For an existing database this explicit write/bootstrap boundary may fill in
    only the legacy compatibility schema. Atomic-save schema changes remain the
    responsibility of the separate checksummed migrator.
    """
    uid = _uid(user_id)
    db_path = _db_path(uid)
    if _database_exists(uid):
        with _connection(uid) as con:
            legacy_ready = _legacy_schema_ready(con)
            atomic_save = _atomic_schema_ready(con)
        if legacy_ready:
            return {"user_id": uid, "created": False, "atomic_save": atomic_save}

        con = _configure_connection(sqlite3.connect(db_path), query_only=False)
        try:
            _ensure_schema(con, bootstrap_atomic=False)
            atomic_save = _atomic_schema_ready(con)
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return {"user_id": uid, "created": False, "atomic_save": atomic_save}

    _workspace_root(uid, create=True)
    con = sqlite3.connect(db_path)
    _configure_connection(con, query_only=False)
    try:
        _ensure_schema(con, bootstrap_atomic=True)
        if not _atomic_schema_ready(con):
            raise RuntimeError("fresh workspace bootstrap did not install the latest schema")
        from code_assist_v1.notion_personal_workspace_schema import install_new
        con.execute("BEGIN IMMEDIATE")
        install_new(con, uid)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"user_id": uid, "created": True, "atomic_save": True}


def _connect_write(
    user_id: str | None,
    *,
    bootstrap_if_missing: bool = False,
) -> sqlite3.Connection:
    """Open a mutation connection without running DDL, migration, or repair."""
    uid = _uid(user_id)
    if bootstrap_if_missing:
        bootstrap_workspace(uid)
    elif not _database_exists(uid):
        raise FileNotFoundError("workspace database does not exist")
    return _configure_connection(sqlite3.connect(_db_path(uid)), query_only=False)


@contextmanager
def _connection(user_id: str | None):
    con = _connect(user_id)
    try:
        yield con
    finally:
        con.close()


@contextmanager
def _write_connection(user_id: str | None, *, bootstrap_if_missing: bool = False):
    con = _connect_write(user_id, bootstrap_if_missing=bootstrap_if_missing)
    try:
        yield con
    finally:
        con.close()


def repair_workspace_tree(user_id: str | None) -> dict[str, Any]:
    """Mutation-only maintenance operation for an explicitly selected workspace."""
    uid = _uid(user_id)
    with _write_connection(uid) as con:
        _repair_page_tree(con)
        rows = con.execute("SELECT COUNT(*) AS c FROM pages WHERE archived=0").fetchone()
        count = int(rows["c"] if rows else 0)
    return {"user_id": uid, "page_count": count, "status": "ok"}


def _ensure_schema(con: sqlite3.Connection, *, bootstrap_atomic: bool = False) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS pages (
          id TEXT PRIMARY KEY,
          owner_user_id TEXT NOT NULL,
          parent_id TEXT,
          title TEXT NOT NULL,
          icon TEXT,
          cover TEXT,
          settings_json TEXT,
          position INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          archived_at INTEGER,
          revision INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blocks (
          id TEXT PRIMARY KEY,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          position INTEGER NOT NULL,
          block_type TEXT NOT NULL,
          content TEXT,
          props_json TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          uploader_user_id TEXT NOT NULL,
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL,
          rel_path TEXT NOT NULL,
          mime_type TEXT,
          size INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL,
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS page_shares (
          id TEXT PRIMARY KEY,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          shared_user_id TEXT NOT NULL,
          role TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          revoked_at INTEGER,
          UNIQUE(page_id, shared_user_id),
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS session_pages (
          session_id TEXT NOT NULL,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          PRIMARY KEY(session_id, page_id)
        );

        CREATE TABLE IF NOT EXISTS public_links (
          page_id TEXT PRIMARY KEY,
          owner_user_id TEXT NOT NULL,
          token TEXT NOT NULL UNIQUE,
          allow_duplicate INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          revoked_at INTEGER,
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS page_versions (
          id TEXT PRIMARY KEY,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          title TEXT NOT NULL,
          icon TEXT,
          cover TEXT,
          settings_json TEXT,
          blocks_json TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS comments (
          id TEXT PRIMARY KEY,
          page_id TEXT NOT NULL,
          block_id TEXT,
          owner_user_id TEXT NOT NULL,
          author_user_id TEXT NOT NULL,
          content TEXT NOT NULL,
          anchor_json TEXT,
          parent_id TEXT,
          reactions_json TEXT,
          resolved INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pages_parent ON pages(parent_id);
        CREATE INDEX IF NOT EXISTS idx_blocks_page ON blocks(page_id, position);
        CREATE INDEX IF NOT EXISTS idx_files_page ON files(page_id);
        CREATE INDEX IF NOT EXISTS idx_shares_user ON page_shares(shared_user_id, revoked_at);
        CREATE INDEX IF NOT EXISTS idx_session_pages_session ON session_pages(session_id);
        CREATE INDEX IF NOT EXISTS idx_public_links_token ON public_links(token, revoked_at);
        CREATE INDEX IF NOT EXISTS idx_page_versions_page ON page_versions(page_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_comments_page ON comments(page_id, resolved, created_at);
        """
    )
    _ensure_column(con, "pages", "cover", "TEXT")
    _ensure_column(con, "pages", "settings_json", "TEXT")
    _ensure_column(con, "pages", "archived_at", "INTEGER")
    _ensure_column(con, "comments", "anchor_json", "TEXT")
    _ensure_column(con, "comments", "parent_id", "TEXT")
    _ensure_column(con, "comments", "reactions_json", "TEXT")
    _ensure_column(con, "public_links", "allow_duplicate", "INTEGER NOT NULL DEFAULT 1")
    con.execute("CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id, created_at)")
    if bootstrap_atomic:
        _bootstrap_atomic_save_schema(con)
    con.commit()


def _ensure_column(con: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(con, table):
        return False
    return column in {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _legacy_schema_ready(con: sqlite3.Connection) -> bool:
    required_columns = {
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
    for table, columns in required_columns.items():
        if not _table_exists(con, table):
            return False
        existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if not columns.issubset(existing):
            return False
    return True


def _create_atomic_save_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS notion_schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          checksum TEXT NOT NULL,
          applied_at INTEGER NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS page_save_operations (
          requester_user_id TEXT NOT NULL,
          operation_id TEXT NOT NULL,
          page_id TEXT NOT NULL,
          owner_user_id TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          result_revision INTEGER NOT NULL,
          result_json TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          PRIMARY KEY(requester_user_id, operation_id),
          FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_save_operations_page ON page_save_operations(page_id, created_at)"
    )


def _record_atomic_save_migration(con: sqlite3.Connection) -> None:
    version, name, checksum = ATOMIC_SAVE_MIGRATION
    con.execute(
        """
        INSERT OR IGNORE INTO notion_schema_migrations(version, name, checksum, applied_at)
        VALUES(?,?,?,?)
        """,
        (version, name, checksum, _now()),
    )
    applied = con.execute(
        "SELECT name, checksum FROM notion_schema_migrations WHERE version=?",
        (version,),
    ).fetchone()
    if not applied or (str(applied["name"]), str(applied["checksum"])) != (name, checksum):
        raise RuntimeError(f"Notion schema migration checksum mismatch at version {version}")


def _bootstrap_atomic_save_schema(con: sqlite3.Connection) -> None:
    """Install the latest schema only while creating a brand-new workspace DB."""
    _ensure_column(con, "pages", "revision", "INTEGER NOT NULL DEFAULT 1")
    _create_atomic_save_tables(con)
    _record_atomic_save_migration(con)


def _atomic_schema_ready(con: sqlite3.Connection) -> bool:
    if not _column_exists(con, "pages", "revision"):
        return False
    if not _table_exists(con, "page_save_operations") or not _table_exists(con, "notion_schema_migrations"):
        return False
    operation_columns = {
        str(row[1]) for row in con.execute("PRAGMA table_info(page_save_operations)").fetchall()
    }
    if "requester_user_id" not in operation_columns:
        return False
    version, name, checksum = ATOMIC_SAVE_MIGRATION
    applied = con.execute(
        "SELECT name, checksum FROM notion_schema_migrations WHERE version=?",
        (version,),
    ).fetchone()
    return bool(applied and (str(applied["name"]), str(applied["checksum"])) == (name, checksum))


def migrate_atomic_save_schema(user_id: str | None) -> dict[str, Any]:
    """Explicit, forward-only deploy migrator; never called by routes or startup."""
    uid = _uid(user_id)
    # A deploy migration must never manufacture a workspace as a side effect of
    # a mistyped target. Ordinary workspace creation remains owned by
    # bootstrap_workspace() and explicit create operations.
    db_path = _db_path(uid)
    if not os.path.isfile(db_path):
        raise FileNotFoundError("workspace database does not exist")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        required_tables = {"pages", "blocks", "files", "page_versions"}
        existing_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise RuntimeError(
                "workspace database is missing required legacy tables: "
                + ", ".join(missing_tables)
            )
        if _table_exists(con, "notion_schema_migrations"):
            version, name, checksum = ATOMIC_SAVE_MIGRATION
            applied = con.execute(
                "SELECT name, checksum FROM notion_schema_migrations WHERE version=?",
                (version,),
            ).fetchone()
            if applied and (str(applied["name"]), str(applied["checksum"])) != (name, checksum):
                raise RuntimeError(f"Notion schema migration checksum mismatch at version {version}")
        _ensure_column(con, "pages", "revision", "INTEGER NOT NULL DEFAULT 1")
        _create_atomic_save_tables(con)
        _record_atomic_save_migration(con)
        if not _atomic_schema_ready(con):
            raise RuntimeError("atomic save schema migration did not satisfy its readiness checks")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"user_id": uid, "version": ATOMIC_SAVE_MIGRATION[0], "atomic_save": True}


def _repair_page_tree(con: sqlite3.Connection) -> None:
    rows = con.execute("SELECT id, parent_id FROM pages WHERE archived=0").fetchall()
    parent_by_id = {str(row["id"]): str(row["parent_id"] or "") for row in rows}
    valid_ids = set(parent_by_id)
    reset_ids: set[str] = set()

    for page_id, parent_id in parent_by_id.items():
        if not parent_id:
            continue
        if parent_id == page_id or parent_id not in valid_ids:
            reset_ids.add(page_id)
            parent_by_id[page_id] = ""

    while True:
        changed = False
        for page_id, parent_id in parent_by_id.items():
            if not parent_id:
                continue
            seen = {page_id}
            current = parent_id
            while current:
                if current in seen:
                    reset_ids.add(page_id)
                    parent_by_id[page_id] = ""
                    changed = True
                    break
                seen.add(current)
                current = parent_by_id.get(current, "")
            if changed:
                break
        if not changed:
            break

    if not reset_ids:
        return
    now = _now()
    for page_id in sorted(reset_ids):
        con.execute(
            "UPDATE pages SET parent_id=NULL, updated_at=? WHERE id=?",
            (now, page_id),
        )
    _bump_page_revisions(con, reset_ids, now=now, update_timestamp=False)
    con.commit()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _safe_filename(name: str) -> str:
    value = Path(name or "file").name
    value = re.sub(r"[\x00-\x1f\x7f<>:\"|?*\\/]+", "-", value).strip(". ")
    return value[:120] or "file"


def _page_id() -> str:
    return "p_" + uuid.uuid4().hex


def _block_id() -> str:
    return "b_" + uuid.uuid4().hex


def _file_id() -> str:
    return "f_" + uuid.uuid4().hex


def _comment_id() -> str:
    return "c_" + uuid.uuid4().hex


def _public_token() -> str:
    return "np_" + secrets.token_urlsafe(24).replace("-", "").replace("_", "")


def _share_id(page_id: str, shared_user_id: str) -> str:
    digest = hashlib.sha1(f"{page_id}:{shared_user_id}".encode("utf-8")).hexdigest()[:16]
    return "s_" + digest


def _role_ok(role: str, minimum: str) -> bool:
    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(minimum, 1)


def _known_workspace_users() -> list[str]:
    users: set[str] = set()
    if os.path.isdir(_user_data_dir()):
        for name in os.listdir(_user_data_dir()):
            uid = safe_user_id(name)
            if uid:
                users.add(uid)
    return sorted(users)


def _page_row(owner_user_id: str, page_id: str) -> dict[str, Any] | None:
    try:
        with _connection(owner_user_id) as con:
            return _row(con.execute(
                "SELECT * FROM pages WHERE id=? AND archived=0",
                (page_id,),
            ).fetchone())
    except Exception:
        return None


def _inherited_share_role(
    con: sqlite3.Connection,
    page_id: str,
    shared_user_id: str,
    cache: dict[str, str] | None = None,
) -> str:
    """Return the nearest explicit share, inheriting access from ancestors."""
    memo = cache if cache is not None else {}
    current = str(page_id or "")
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
            SELECT p.parent_id, s.role
            FROM pages p
            LEFT JOIN page_shares s
              ON s.page_id=p.id AND s.shared_user_id=? AND s.revoked_at IS NULL
            WHERE p.id=? AND p.archived=0
            """,
            (shared_user_id, current),
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


def resolve_page(
    user_id: str | None,
    page_id: str,
    *,
    minimum_role: str = "view",
) -> tuple[str, str, dict[str, Any]] | None:
    uid = _uid(user_id)
    page_id = str(page_id or "").strip()
    if not page_id:
        return None

    own = _page_row(uid, page_id)
    if own and _role_ok("owner", minimum_role):
        return uid, "owner", own

    for owner in _known_workspace_users():
        if owner == uid:
            continue
        page = _page_row(owner, page_id)
        if not page:
            continue
        try:
            with _connection(owner) as con:
                role = _inherited_share_role(con, page_id, uid)
        except Exception:
            role = ""
        if role and _role_ok(role, minimum_role):
            return owner, role, page
    return None


def _require_page(user_id: str | None, page_id: str, minimum_role: str = "view") -> tuple[str, str, dict[str, Any]]:
    resolved = resolve_page(user_id, page_id, minimum_role=minimum_role)
    if not resolved:
        raise FileNotFoundError("page not found or permission denied")
    return resolved


def _page_summary(page: dict[str, Any], owner: str, role: str, scope: str) -> dict[str, Any]:
    settings = _json_load(page.get("settings_json"), {})
    if isinstance(settings, dict):
        settings = {
            key: value
            for key, value in settings.items()
            if not str(key).startswith("_np2b_")
        }
    summary = {
        "id": page["id"],
        "owner_user_id": owner,
        "parent_id": page.get("parent_id") or "",
        "title": page.get("title") or "Untitled",
        "icon": page.get("icon") or "",
        "cover": page.get("cover") or "",
        "settings": settings if isinstance(settings, dict) else {},
        "position": int(page.get("position") or 0),
        "created_at": int(page.get("created_at") or 0),
        "updated_at": int(page.get("updated_at") or 0),
        "archived_at": int(page.get("archived_at") or 0),
        "role": role,
        "scope": scope,
        "can_edit": role in EDIT_ROLES,
        "can_share": role == "owner",
    }
    if "revision" in page:
        summary["revision"] = max(1, int(page.get("revision") or 1))
    return summary


def _bump_page_revisions(
    con: sqlite3.Connection,
    page_ids: set[str] | list[str] | tuple[str, ...],
    *,
    now: int | None = None,
    update_timestamp: bool = True,
) -> None:
    changed_ids = sorted({str(page_id or "") for page_id in page_ids if str(page_id or "")})
    if not changed_ids:
        return
    if not _column_exists(con, "pages", "revision"):
        return
    changed_at = now or _now()
    if update_timestamp:
        con.executemany(
            "UPDATE pages SET revision=revision+1, updated_at=? WHERE id=?",
            [(changed_at, page_id) for page_id in changed_ids],
        )
    else:
        con.executemany(
            "UPDATE pages SET revision=revision+1 WHERE id=?",
            [(page_id,) for page_id in changed_ids],
        )


def _page_payload_in_connection(
    con: sqlite3.Connection,
    requester_user_id: str,
    owner: str,
    role: str,
    page_id: str,
    *,
    reference_page_ids: set[str] | None = None,
) -> dict[str, Any]:
    page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
    if not page_row:
        raise FileNotFoundError("page not found or permission denied")
    block_rows = con.execute(
        "SELECT * FROM blocks WHERE page_id=? ORDER BY position",
        (page_id,),
    ).fetchall()
    file_rows = con.execute(
        "SELECT * FROM files WHERE page_id=? ORDER BY created_at DESC",
        (page_id,),
    ).fetchall()
    blocks = [_block_from_row(dict(row)) for row in block_rows]
    _hydrate_page_references(
        con,
        blocks,
        requester_user_id=requester_user_id,
        owner=owner,
        reference_page_ids=reference_page_ids,
    )
    payload = {
        "page": _page_summary(
            dict(page_row),
            owner,
            role,
            "own" if owner == requester_user_id else "shared",
        ),
        "blocks": blocks,
        "files": [_file_from_row(dict(row), owner) for row in file_rows],
        "capabilities": {"atomic_save": _atomic_schema_ready(con), "personal_linked_database": True},
    }
    if payload["capabilities"]["atomic_save"]:
        payload["revision"] = int(payload["page"]["revision"])
    return payload


def list_pages(user_id: str | None, query: str = "") -> list[dict[str, Any]]:
    uid = _uid(user_id)
    q = str(query or "").strip().lower()
    items: list[dict[str, Any]] = []

    if _database_exists(uid):
        with _connection(uid) as con:
            for row in con.execute(
                "SELECT * FROM pages WHERE archived=0 ORDER BY position, updated_at DESC"
            ).fetchall():
                page = dict(row)
                items.append(_page_summary(page, uid, "owner", "own"))

    for owner in _known_workspace_users():
        if owner == uid:
            continue
        try:
            with _connection(owner) as con:
                rows = con.execute(
                    "SELECT * FROM pages WHERE archived=0 ORDER BY position, updated_at DESC"
                ).fetchall()
                role_cache: dict[str, str] = {}
                shared_rows = [
                    (dict(row), _inherited_share_role(con, str(row["id"]), uid, role_cache))
                    for row in rows
                ]
        except Exception:
            continue
        for page, share_role in shared_rows:
            if share_role:
                items.append(_page_summary(page, owner, share_role, "shared"))

    if q:
        items = [
            item for item in items
            if q in (item.get("title") or "").lower()
            or q in (item.get("owner_user_id") or "").lower()
        ]
    items.sort(key=lambda item: (item["scope"] != "own", item.get("position", 0), -int(item.get("updated_at") or 0)))
    return items


def list_meeting_pages(
    user_id: str | None,
    *,
    query: str = "",
    limit: int = 100,
    offset: int = 0,
    allowed_pages: list[dict[str, Any]] | None = None,
    owner_filter: str = "",
) -> dict[str, Any]:
    """Return note metadata without loading transcripts or walking page bodies.

    Workspace callers supply their authorized catalog. Each owner shard still
    revalidates the current legacy grant before returning a meeting summary.
    """
    uid = _uid(user_id)
    summaries = list_pages(uid) if allowed_pages is None else allowed_pages
    owners: dict[str, set[str]] = {}
    for summary in summaries:
        owner, page_id = str(summary.get("owner_user_id") or ""), str(summary.get("id") or "")
        if owner and page_id and (not owner_filter or owner == owner_filter):
            owners.setdefault(owner, set()).add(page_id)
    found: list[dict[str, Any]] = []
    degraded = False
    folded = str(query or "").strip().casefold()
    for owner, ids in owners.items():
        if not _database_exists(owner):
            degraded = True
            continue
        try:
            with _connection(owner) as con:
                con.execute("BEGIN")
                role_cache: dict[str, str] = {}
                ordered_ids = sorted(ids)
                for start in range(0, len(ordered_ids), 500):
                    chunk = ordered_ids[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = con.execute(
                        "SELECT p.*,COUNT(b.id) AS note_count,"
                        "(SELECT first.id FROM blocks first WHERE first.page_id=p.id "
                        "AND first.block_type='ai_notes' ORDER BY first.position,first.id LIMIT 1) AS first_note_id "
                        "FROM pages p JOIN blocks b ON b.page_id=p.id AND b.block_type='ai_notes' "
                        f"WHERE p.archived=0 AND p.id IN ({placeholders}) GROUP BY p.id",
                        chunk,
                    ).fetchall()
                    for row in rows:
                        role = "owner" if owner == uid else _inherited_share_role(con, str(row["id"]), uid, role_cache)
                        if not role or not _role_ok(role, "view"):
                            continue
                        page = _page_summary(dict(row), owner, role, "own" if owner == uid else "shared")
                        if folded and folded not in page["title"].casefold():
                            continue
                        metadata = {key: page[key] for key in ("id", "title", "icon", "owner_user_id", "updated_at", "role", "can_edit")}
                        found.append({**metadata, "note_count": int(row["note_count"]), "block_id": str(row["first_note_id"])})
        except (FileNotFoundError, sqlite3.Error):
            degraded = True
    found.sort(key=lambda item: (-int(item["updated_at"]), item["id"]))
    size, skip = max(1, min(100, int(limit))), max(0, int(offset))
    return {
        "items": found[skip:skip + size], "count": len(found),
        "has_more": skip + size < len(found), "offset": skip,
        "degraded": degraded, "degraded_sources": ["meeting_metadata"] if degraded else [],
    }


def list_calendar_events(
    user_id: str | None,
    *,
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    today = date.today()
    start = str(date_from or today.isoformat())
    end = str(date_to or (today + timedelta(days=7)).isoformat())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise ValueError("date range must use YYYY-MM-DD")
    items: list[dict[str, Any]] = []
    for summary in list_pages(user_id):
        try:
            data = get_page(user_id, str(summary.get("id") or ""))
        except Exception:
            continue
        for block in data.get("blocks") or []:
            if block.get("type") not in {"table", "board", "calendar", "list", "gallery", "timeline", "chart", "dashboard", "form", "feed", "map"}:
                continue
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            rows = props.get("rows") if isinstance(props.get("rows"), list) else []
            properties = props.get("properties") if isinstance(props.get("properties"), list) else []
            date_fields = [str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "date" and item.get("id")]
            title_field = next((str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "title" and item.get("id")), "title")
            status_field = next((str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") in {"status", "select"} and item.get("id")), "status")
            tags_field = next((str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "multi_select" and item.get("id")), "tags")
            if "date" not in date_fields:
                date_fields.append("date")
            for row in rows:
                if not isinstance(row, dict) or row.get("archived"):
                    continue
                event_date = ""
                for field in date_fields:
                    raw_date = row.get(field)
                    if not raw_date:
                        continue
                    if isinstance(raw_date, dict):
                        event_date = str(raw_date.get("start") or "")[:10]
                    else:
                        event_date = str(raw_date)[:10]
                    if event_date:
                        break
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date) or not (start <= event_date <= end):
                    continue
                items.append({
                    "date": event_date,
                    "title": str(row.get(title_field) or row.get("title") or "Untitled"),
                    "status": str(row.get(status_field) or row.get("status") or ""),
                    "tags": str(row.get(tags_field) or row.get("tags") or ""),
                    "page_id": str(row.get("page_id") or ""),
                    "source_page_id": str(summary.get("id") or ""),
                    "source_page_title": str(summary.get("title") or "Untitled"),
                    "database_block_id": str(block.get("id") or ""),
                })
    items.sort(key=lambda item: (item["date"], item["title"].lower()))
    return items[: max(1, min(500, int(limit or 100)))]


class _InlineNotificationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "span":
            return
        values = {str(name).lower(): str(value or "") for name, value in attrs}
        if values.get("data-notion-user") or values.get("data-notion-reminder"):
            self.items.append(values)


def list_inbox_items(user_id: str | None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Derive accessible mention and reminder notifications from rich-text spans."""
    uid = _uid(user_id)
    now = int(time.time())
    items: list[dict[str, Any]] = []
    for summary in list_pages(uid):
        page_id = str(summary.get("id") or "")
        try:
            data = get_page(uid, page_id)
        except Exception:
            continue
        page = data.get("page") or summary
        for block in data.get("blocks") or []:
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            rich_html = str(props.get("rich_text_html") or "")
            parser = _InlineNotificationParser()
            if "data-notion-" in rich_html:
                try:
                    parser.feed(rich_html)
                except Exception:
                    parser.items = []
            for notification_index, attrs in enumerate(parser.items):
                block_id = str(block.get("id") or "")
                mentioned_user = safe_user_id(attrs.get("data-notion-user"))
                if mentioned_user == uid:
                    mention_id = re.sub(r"[^a-zA-Z0-9_-]", "", attrs.get("data-notion-mention-id") or "")
                    if not mention_id:
                        raw_id = f"{page_id}:{block_id}:{uid}:{notification_index}"
                        mention_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:20]
                    items.append({
                        "id": f"mention_{mention_id}",
                        "kind": "mention",
                        "page_id": page_id,
                        "page_title": str(page.get("title") or "Untitled"),
                        "block_id": block_id,
                        "message": str(block.get("content") or "").strip()[:500],
                        "created_at": int(block.get("updated_at") or page.get("updated_at") or 0),
                        "due": True,
                        "past_due": False,
                    })

                reminder_value = str(attrs.get("data-notion-reminder") or "")
                reminder_user = safe_user_id(attrs.get("data-notion-reminder-user"))
                if not reminder_value or reminder_user != uid:
                    continue
                try:
                    remind_at = datetime.fromisoformat(reminder_value)
                except ValueError:
                    continue
                try:
                    lead_minutes = max(0, min(525600, int(attrs.get("data-notion-reminder-lead") or 0)))
                except (TypeError, ValueError):
                    lead_minutes = 0
                trigger_at = remind_at - timedelta(minutes=lead_minutes)
                reminder_id = re.sub(r"[^a-zA-Z0-9_-]", "", attrs.get("data-notion-reminder-id") or "")
                if not reminder_id:
                    raw_id = f"{page_id}:{block_id}:{reminder_value}:{notification_index}"
                    reminder_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:20]
                trigger_timestamp = int(trigger_at.timestamp())
                remind_timestamp = int(remind_at.timestamp())
                items.append({
                    "id": f"reminder_{reminder_id}",
                    "kind": "reminder",
                    "page_id": page_id,
                    "page_title": str(page.get("title") or "Untitled"),
                    "block_id": block_id,
                    "message": str(block.get("content") or "").strip()[:500],
                    "remind_at": reminder_value,
                    "trigger_at": trigger_timestamp,
                    "created_at": int(block.get("updated_at") or page.get("updated_at") or 0),
                    "due": now >= trigger_timestamp,
                    "past_due": now > remind_timestamp,
                })

            properties = props.get("properties") if isinstance(props.get("properties"), list) else []
            rows = props.get("rows") if isinstance(props.get("rows"), list) else []
            title_field = next((
                str(prop.get("id")) for prop in properties
                if isinstance(prop, dict) and prop.get("type") == "title" and prop.get("id")
            ), "title")
            date_fields = [
                str(prop.get("id")) for prop in properties
                if isinstance(prop, dict) and prop.get("type") == "date" and prop.get("id")
            ]
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict) or row.get("archived"):
                    continue
                for date_field in date_fields:
                    reminder = row.get(f"{date_field}_reminder")
                    if not isinstance(reminder, dict) or safe_user_id(reminder.get("user_id")) != uid:
                        continue
                    reminder_value = str(reminder.get("at") or "")
                    try:
                        remind_at = datetime.fromisoformat(reminder_value)
                        lead_minutes = max(0, min(525600, int(reminder.get("lead") or 0)))
                    except (TypeError, ValueError):
                        continue
                    trigger_at = remind_at - timedelta(minutes=lead_minutes)
                    reminder_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(reminder.get("id") or ""))
                    if not reminder_id:
                        raw_id = f"{page_id}:{block.get('id')}:{row_index}:{date_field}:{reminder_value}"
                        reminder_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:20]
                    trigger_timestamp = int(trigger_at.timestamp())
                    remind_timestamp = int(remind_at.timestamp())
                    items.append({
                        "id": f"reminder_{reminder_id}",
                        "kind": "reminder",
                        "page_id": str(row.get("page_id") or page_id),
                        "page_title": str(row.get(title_field) or row.get("title") or page.get("title") or "Untitled"),
                        "source_page_id": page_id,
                        "block_id": str(block.get("id") or ""),
                        "message": str(row.get(title_field) or row.get("title") or block.get("content") or "리마인더")[:500],
                        "remind_at": reminder_value,
                        "trigger_at": trigger_timestamp,
                        "created_at": int(block.get("updated_at") or page.get("updated_at") or 0),
                        "due": now >= trigger_timestamp,
                        "past_due": now > remind_timestamp,
                    })
    unique = {item["id"]: item for item in items}
    output = list(unique.values())
    output.sort(key=lambda item: (
        0 if item.get("due") else 1,
        -int(item.get("trigger_at") or item.get("created_at") or 0) if item.get("due") else int(item.get("trigger_at") or item.get("created_at") or 0),
    ))
    return output[: max(1, min(500, int(limit or 200)))]


def list_backlinks(user_id: str | None, page_id: str) -> list[dict[str, Any]]:
    _require_page(user_id, page_id, "view")
    target = str(page_id or "")
    items: list[dict[str, Any]] = []
    for summary in list_pages(user_id):
        source_id = str(summary.get("id") or "")
        if not source_id or source_id == target:
            continue
        try:
            data = get_page(user_id, source_id)
        except Exception:
            continue
        matching_blocks: list[str] = []
        for block in data.get("blocks") or []:
            props_json = json.dumps(block.get("props") or {}, ensure_ascii=False)
            content = str(block.get("content") or "")
            if target in props_json or f"/page/{target}" in content:
                matching_blocks.append(str(block.get("id") or ""))
        if matching_blocks:
            items.append({
                **summary,
                "matching_block_ids": matching_blocks,
                "match_count": len(matching_blocks),
            })
    items.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
    return items


def page_id_for_operation(
    user_id: str | None,
    *,
    namespace: str,
    operation_id: str,
) -> str:
    """Return the canonical self-owned page ID for one create operation."""

    uid = _uid(user_id)
    namespace = str(namespace or "").strip()
    operation_id = str(operation_id or "").strip()
    if not namespace or len(namespace) > 160:
        raise ValueError("namespace must contain 1 to 160 characters")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    identity = json.dumps(
        {
            "actor": uid,
            "namespace": namespace,
            "operation_id": operation_id,
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "p_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def create_page_idempotent(
    user_id: str | None,
    *,
    namespace: str,
    operation_id: str,
    title: str = "Untitled",
    parent_id: str = "",
) -> dict[str, Any]:
    """Create one self-owned page with a deterministic, replay-safe identity.

    This narrow primitive exists for the default-off canonical workspace UI.
    The generated page ID commits the authenticated actor, workspace namespace,
    and caller operation ID.  An exact retry returns the original page without
    appending another child or version; reusing the operation for a different
    title or parent fails closed.
    """

    uid = _uid(user_id)
    namespace = str(namespace or "").strip()
    operation_id = str(operation_id or "").strip()
    page_id = page_id_for_operation(
        uid,
        namespace=namespace,
        operation_id=operation_id,
    )
    normalized_title = (
        " ".join(str(title or "Untitled").strip().split())[:160] or "Untitled"
    )
    normalized_parent = str(parent_id or "").strip()
    if len(normalized_parent) > 160:
        raise ValueError("parent_id must be at most 160 characters")
    if normalized_parent:
        owner, role, parent_page = _require_page(uid, normalized_parent, "edit")
        if owner != uid or role not in EDIT_ROLES:
            raise PermissionError("self-owned parent page edit permission required")
        parent_settings = _json_load(parent_page.get("settings_json"), {})
        if _page_write_locked(parent_settings, uid, normalized_parent):
            raise PermissionError("parent page is locked")

    now = _now()
    with _write_connection(uid, bootstrap_if_missing=True) as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT * FROM pages WHERE id=? LIMIT 1", (page_id,)
        ).fetchone()
        if existing is not None:
            current = dict(existing)
            if (
                str(current.get("owner_user_id") or "") != uid
                or str(current.get("parent_id") or "") != normalized_parent
                or str(current.get("title") or "") != normalized_title
                or type(current.get("archived")) is not int
                or int(current["archived"]) != 0
            ):
                current_revision = max(1, int(current.get("revision") or 1))
                con.rollback()
                raise OperationIdConflict(operation_id, current_revision)
            con.rollback()
            return get_page(uid, page_id)

        if normalized_parent:
            parent = con.execute(
                "SELECT owner_user_id,archived,settings_json FROM pages WHERE id=?",
                (normalized_parent,),
            ).fetchone()
            parent_settings = (
                _json_load(parent["settings_json"], {}) if parent is not None else {}
            )
            if (
                parent is None
                or str(parent["owner_user_id"]) != uid
                or int(parent["archived"]) != 0
                or _page_write_locked(parent_settings, uid, normalized_parent)
            ):
                con.rollback()
                raise PermissionError("self-owned parent page edit permission required")
            pos_row = con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos "
                "FROM pages WHERE archived=0 AND parent_id=?",
                (normalized_parent,),
            ).fetchone()
        else:
            pos_row = con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos "
                "FROM pages WHERE archived=0 AND parent_id IS NULL"
            ).fetchone()
        position = int(pos_row["pos"] if pos_row else 1)
        con.execute(
            """
            INSERT INTO pages(
              id,owner_user_id,parent_id,title,icon,cover,settings_json,
              position,archived,archived_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                page_id,
                uid,
                normalized_parent or None,
                normalized_title,
                "",
                "",
                _json_dump({}),
                position,
                0,
                None,
                now,
                now,
            ),
        )
        _replace_blocks_in_connection(
            con,
            uid,
            page_id,
            [{"type": "paragraph", "content": "", "props": {}}],
            now=now,
        )
        _record_page_version(con, uid, page_id, now=now)
        con.commit()
    return get_page(uid, page_id)


def create_page(
    user_id: str | None,
    *,
    title: str = "Untitled",
    parent_id: str = "",
    icon: str = "",
    cover: str = "",
    settings: dict[str, Any] | None = None,
    markdown: str = "",
    add_to_parent_blocks: bool = False,
) -> dict[str, Any]:
    uid = _uid(user_id)
    now = _now()
    title = " ".join(str(title or "Untitled").strip().split())[:160] or "Untitled"
    parent_id = str(parent_id or "").strip()
    workspace_owner = uid
    if parent_id:
        owner, role, parent_page = _require_page(uid, parent_id, "edit")
        if role not in EDIT_ROLES:
            raise PermissionError("edit permission required")
        workspace_owner = owner
        parent_settings = _json_load(parent_page.get("settings_json"), {})
        if _page_write_locked(parent_settings, uid, parent_id):
            raise PermissionError("parent page is locked")

    pid = _page_id()
    with _write_connection(workspace_owner, bootstrap_if_missing=True) as con:
        con.execute("BEGIN IMMEDIATE")
        if parent_id:
            _effective_edit_role(con, uid, workspace_owner, parent_id)
            parent_row = con.execute(
                "SELECT archived, settings_json FROM pages WHERE id=?", (parent_id,)
            ).fetchone()
            parent_settings = _json_load(parent_row["settings_json"], {}) if parent_row else {}
            if not parent_row or parent_row["archived"] or _page_write_locked(parent_settings, uid, parent_id):
                raise PermissionError("parent page is locked or unavailable")
            pos_row = con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id=?",
                (parent_id,),
            ).fetchone()
        else:
            pos_row = con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id IS NULL"
            ).fetchone()
        position = int(pos_row["pos"] if pos_row else 1)
        con.execute(
            """
            INSERT INTO pages(id, owner_user_id, parent_id, title, icon, cover, settings_json, position, archived, archived_at, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (pid, workspace_owner, parent_id or None, title, icon[:64], str(cover or "")[:1000], _json_dump(settings or {}), position, 0, None, now, now),
        )
        blocks = markdown_to_blocks(markdown) if markdown.strip() else [
            {"type": "paragraph", "content": "", "props": {}}
        ]
        _replace_blocks_in_connection(con, workspace_owner, pid, blocks, now=now)
        _record_page_version(con, workspace_owner, pid, now=now)
        if parent_id and add_to_parent_blocks:
            parent_rows = con.execute(
                "SELECT * FROM blocks WHERE page_id=? ORDER BY position",
                (parent_id,),
            ).fetchall()
            parent_blocks = [_block_from_row(dict(row)) for row in parent_rows]
            parent_blocks.append({
                "type": "child_page",
                "content": title,
                "props": {"page_id": pid, "title": title, "icon": icon[:64]},
            })
            _replace_blocks_in_connection(con, workspace_owner, parent_id, parent_blocks, now=now)
            _bump_page_revisions(con, {parent_id}, now=now)
            _record_page_version(con, workspace_owner, parent_id, now=now)
        con.commit()
    return get_page(uid, pid)


def get_page(user_id: str | None, page_id: str) -> dict[str, Any]:
    owner, role, page = _require_page(user_id, page_id, "view")
    requester = _uid(user_id)
    with _connection(owner) as con:
        return _page_payload_in_connection(con, requester, owner, role, page_id)


def resolve_page_from_owner(
    user_id: str | None,
    owner_user_id: str | None,
    page_id: str,
    *,
    minimum_role: str = "view",
) -> tuple[str, str, dict[str, Any]] | None:
    """Resolve one known owner shard without enumerating USER_DATA_DIR.

    NP-2A locator-backed reads must use this boundary.  It intentionally has
    no directory-scan fallback: a wrong or stale locator is a closed miss.
    """
    requester = _uid(user_id)
    owner = _uid(owner_user_id)
    pid = str(page_id or "").strip()
    if not pid or not _database_exists(owner):
        return None
    try:
        with _connection(owner) as con:
            page = _row(
                con.execute(
                    "SELECT * FROM pages WHERE id=? AND archived=0",
                    (pid,),
                ).fetchone()
            )
            if not page:
                return None
            role = "owner" if requester == owner else _inherited_share_role(
                con, pid, requester
            )
            if not role or not _role_ok(role, minimum_role):
                return None
            return owner, role, page
    except (FileNotFoundError, sqlite3.Error):
        return None


def get_page_from_owner(
    user_id: str | None,
    owner_user_id: str | None,
    page_id: str,
    *,
    minimum_role: str = "view",
) -> dict[str, Any]:
    requester = _uid(user_id)
    owner = _uid(owner_user_id)
    pid = str(page_id or "").strip()
    if not pid or not _database_exists(owner):
        raise FileNotFoundError("page not found or permission denied")
    try:
        with _connection(owner) as con:
            row = con.execute(
                "SELECT 1 FROM pages WHERE id=? AND archived=0", (pid,)
            ).fetchone()
            if not row:
                raise FileNotFoundError("page not found or permission denied")
            role = "owner" if requester == owner else _inherited_share_role(
                con, pid, requester
            )
            if not role or not _role_ok(role, minimum_role):
                raise FileNotFoundError("page not found or permission denied")
            return _page_payload_in_connection(con, requester, owner, role, pid)
    except sqlite3.Error as exc:
        raise FileNotFoundError("page not found or permission denied") from exc


def capture_legacy_page_source_proof(
    user_id: str | None,
    owner_user_id: str | None,
    page_id: str,
):
    """Capture an immutable, actor-bound proof for an explicit owner locator."""
    from code_assist_v1.notion_workspace_store import LegacyPageSourceProof

    requester = _uid(user_id)
    owner = _uid(owner_user_id)
    payload = get_page_from_owner(requester, owner, page_id, minimum_role="view")
    page = dict(payload.get("page") or {})
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    revision = max(1, int(page.get("revision") or payload.get("revision") or 1))
    updated_at = max(0, int(page.get("updated_at") or 0))
    identity = {
        "actor_subject_id": requester,
        "owner_subject_id": owner,
        "page_id": str(page.get("id") or page_id),
        "revision": revision,
        "updated_at": updated_at,
        "page": {
            key: page.get(key)
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
            if isinstance(item, dict)
        ],
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return LegacyPageSourceProof(
        actor_subject_id=requester,
        owner_subject_id=owner,
        page_id=str(identity["page_id"]),
        revision=revision,
        updated_at=updated_at,
        content_sha256="sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def revalidate_legacy_page_source_proof(proof: Any) -> bool:
    """Re-read the known shard and compare every proof field; never scan."""
    try:
        current = capture_legacy_page_source_proof(
            proof.actor_subject_id,
            proof.owner_subject_id,
            proof.page_id,
        )
    except Exception:
        return False
    return current == proof


def list_pages_from_owner_by_ids(
    user_id: str | None,
    owner_user_id: str | None,
    page_ids: list[str] | tuple[str, ...] | set[str],
) -> list[dict[str, Any]]:
    """List accessible summaries from one explicit owner shard, without scan."""
    requester = _uid(user_id)
    owner = _uid(owner_user_id)
    ordered_ids = sorted(
        {str(page_id or "").strip() for page_id in page_ids if str(page_id or "").strip()}
    )
    if not ordered_ids or not _database_exists(owner):
        return []
    items: list[dict[str, Any]] = []
    try:
        with _connection(owner) as con:
            for page_id in ordered_ids:
                row = con.execute(
                    "SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)
                ).fetchone()
                if not row:
                    continue
                role = "owner" if requester == owner else _inherited_share_role(
                    con, page_id, requester
                )
                if not role or not _role_ok(role, "view"):
                    continue
                items.append(
                    _page_summary(
                        dict(row),
                        owner,
                        role,
                        "own" if requester == owner else "shared",
                    )
                )
    except (FileNotFoundError, sqlite3.Error):
        return []
    return items


def _hydrate_page_references(
    con: sqlite3.Connection,
    blocks: list[dict[str, Any]],
    *,
    requester_user_id: str,
    owner: str,
    reference_page_ids: set[str] | None = None,
) -> None:
    """Hydrate only page references the current subject may discover.

    Stored row/title hints are never trusted: inaccessible or missing targets are
    replaced with a stable placeholder so relations cannot disclose private
    sibling metadata.  Public rendering supplies an explicit projected subtree.
    """
    referenced_ids: set[str] = set()
    for block in blocks:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        page_ref = str(props.get("page_id") or "")
        if page_ref:
            referenced_ids.add(page_ref)
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        for item in rows:
            if isinstance(item, dict) and item.get("page_id"):
                referenced_ids.add(str(item["page_id"]))
    if not referenced_ids:
        return
    placeholders = ",".join("?" for _ in referenced_ids)
    linked = {
        str(row["id"]): dict(row)
        for row in con.execute(
            f"SELECT id, title, icon, updated_at, archived FROM pages WHERE id IN ({placeholders})",
            tuple(sorted(referenced_ids)),
        ).fetchall()
    }
    role_cache: dict[str, str] = {}

    def may_discover(page_ref: str) -> bool:
        if not page_ref or page_ref not in linked:
            return False
        if reference_page_ids is not None:
            return page_ref in reference_page_ids
        if requester_user_id == owner:
            return True
        return bool(_inherited_share_role(con, page_ref, requester_user_id, role_cache))

    def redact_reference(item: dict[str, Any], title_field: str) -> None:
        item[title_field] = "Unavailable page"
        item["title"] = "Unavailable page"
        item["icon"] = ""
        item.pop("archived", None)
        item["inaccessible"] = True

    for block in blocks:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        properties = props.get("properties") if isinstance(props.get("properties"), list) else []
        title_field = next(
            (str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "title" and item.get("id")),
            "title",
        )
        page_ref = str(props.get("page_id") or "")
        page_item = linked.get(page_ref)
        page_accessible = may_discover(page_ref)
        if page_item and page_accessible and block.get("type") == "child_page":
            block["content"] = page_item.get("title") or "Untitled"
            props["title"] = block["content"]
            props["icon"] = page_item.get("icon") or ""
            props["archived"] = bool(page_item.get("archived"))
            props.pop("inaccessible", None)
        elif page_ref and block.get("type") in {"child_page", "link_to_page"} and not page_accessible:
            block["content"] = "Unavailable page"
            props["title"] = "Unavailable page"
            props["icon"] = ""
            props.pop("archived", None)
            props["inaccessible"] = True
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        for item in rows:
            if not isinstance(item, dict):
                continue
            page_item = linked.get(str(item.get("page_id") or ""))
            row_page_ref = str(item.get("page_id") or "")
            if page_item and may_discover(row_page_ref):
                item[title_field] = page_item.get("title") or "Untitled"
                item["title"] = page_item.get("title") or "Untitled"
                item["icon"] = page_item.get("icon") or ""
                item["archived"] = bool(page_item.get("archived"))
                item.pop("inaccessible", None)
            elif row_page_ref:
                redact_reference(item, title_field)


def _effective_edit_role(
    con: sqlite3.Connection,
    requester_user_id: str,
    owner: str,
    page_id: str,
) -> str:
    role = "owner" if requester_user_id == owner else _inherited_share_role(con, page_id, requester_user_id)
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    return role


def _page_metadata_changes(page: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "title" in updates:
        title = " ".join(str(updates.get("title") or "Untitled").strip().split())[:160] or "Untitled"
        if title != str(page.get("title") or "Untitled"):
            changes["title"] = title
    if "icon" in updates:
        icon = str(updates.get("icon") or "")[:64]
        if icon != str(page.get("icon") or ""):
            changes["icon"] = icon
    if "cover" in updates:
        cover = str(updates.get("cover") or "")[:1000]
        if cover != str(page.get("cover") or ""):
            changes["cover"] = cover
    if isinstance(updates.get("settings"), dict):
        current_settings = _json_load(page.get("settings_json"), {})
        if not isinstance(current_settings, dict):
            current_settings = {}
        next_settings = dict(current_settings)
        for key, value in updates["settings"].items():
            if key in {"font", "small_text", "full_width", "show_outline", "show_page_comments", "inline_comments", "locked"}:
                if key == "inline_comments" and (not isinstance(value, str) or value not in {"default", "minimized"}):
                    continue
                if key == "show_page_comments" and type(value) is not bool:
                    continue
                next_settings[key] = value
        if bool(next_settings.get("locked")) != bool(current_settings.get("locked")):
            if next_settings["locked"]:
                next_settings["_np2b_lock_nonce"] = secrets.token_urlsafe(18)
            else:
                next_settings.pop("_np2b_lock_nonce", None)
        if next_settings != current_settings:
            changes["settings_json"] = _json_dump(next_settings)
    return changes


def _copied_page_settings(raw_settings: Any) -> dict[str, Any]:
    settings = _json_load(raw_settings, {})
    if not isinstance(settings, dict):
        return {}
    settings = dict(settings)
    if settings.get("locked"):
        settings["_np2b_lock_nonce"] = secrets.token_urlsafe(18)
    else:
        settings.pop("_np2b_lock_nonce", None)
    return settings


def update_page(user_id: str | None, page_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    if not isinstance(updates, dict):
        raise ValueError("page updates must be an object")
    settings_update = updates.get("settings") if isinstance(updates.get("settings"), dict) else None
    defer_version = bool(updates.get("_defer_version"))
    parent_id = updates.get("parent_id")
    if parent_id is not None:
        raise ValueError("move pages with move_page so hierarchy references stay consistent")
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        role = _effective_edit_role(con, uid, owner, page_id)
        page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if not page_row:
            raise FileNotFoundError("page not found or permission denied")
        current_page = dict(page_row)
        current_settings = _json_load(current_page.get("settings_json"), {})
        if not isinstance(current_settings, dict):
            current_settings = {}
        if _page_write_locked(current_settings, uid, page_id) and not (settings_update is not None and settings_update.get("locked") is False):
            raise PermissionError("page is locked")
        changes = _page_metadata_changes(current_page, updates)
        if not changes:
            con.rollback()
            return get_page(uid, page_id)
        now = _now()
        fields = [f"{column}=?" for column in changes]
        values = [*changes.values(), now, page_id]
        con.execute(
            f"UPDATE pages SET {', '.join(fields)}, updated_at=? WHERE id=?",
            tuple(values),
        )
        _bump_page_revisions(con, {page_id}, now=now, update_timestamp=False)
        # Autosave sends metadata and blocks as one logical edit. The block write
        # records the combined snapshot, avoiding a transient title-only version.
        if not defer_version:
            _record_page_version(con, owner, page_id, now=now)
        con.commit()
    return get_page(uid, page_id)


def _page_descendants(con, page_id: str, *, include_archived: bool = False) -> set[str]:
    descendants: set[str] = set()
    pending = [page_id]
    while pending:
        cur = pending.pop()
        query = "SELECT id FROM pages WHERE parent_id=?" if include_archived else "SELECT id FROM pages WHERE archived=0 AND parent_id=?"
        rows = con.execute(query, (cur,)).fetchall()
        for row in rows:
            child_id = str(row["id"])
            if child_id not in descendants:
                descendants.add(child_id)
                pending.append(child_id)
    return descendants


def _page_ancestors(con: sqlite3.Connection, page_id: str, *, include_archived: bool = False) -> set[str]:
    ancestors: set[str] = set()
    current = str(page_id or "")
    while current:
        query = "SELECT parent_id FROM pages WHERE id=?" if include_archived else "SELECT parent_id FROM pages WHERE id=? AND archived=0"
        row = con.execute(query, (current,)).fetchone()
        parent_id = str(row["parent_id"] or "") if row else ""
        if not parent_id or parent_id in ancestors or parent_id == page_id:
            break
        ancestors.add(parent_id)
        current = parent_id
    return ancestors


def _page_blocks_in_connection(con: sqlite3.Connection, page_id: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT * FROM blocks WHERE page_id=? ORDER BY position", (page_id,)).fetchall()
    return [_block_from_row(dict(row)) for row in rows]


def _remove_child_references(blocks: list[dict[str, Any]], child_page_id: str) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    changed = False
    for block in blocks:
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        if block.get("type") == "child_page" and str(props.get("page_id") or "") == child_page_id:
            changed = True
            continue
        rows = props.get("rows") if isinstance(props.get("rows"), list) else None
        if rows is not None:
            filtered = [item for item in rows if not (isinstance(item, dict) and str(item.get("page_id") or "") == child_page_id)]
            if len(filtered) != len(rows):
                props["rows"] = filtered
                changed = True
        output.append(block)
    return output, changed


def _position_child_page_reference(
    blocks: list[dict[str, Any]],
    page: dict[str, Any],
    *,
    before_id: str = "",
    after_id: str = "",
    create: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    page_id = str(page.get("id") or "")
    output = list(blocks)
    current_index = next((
        index for index, block in enumerate(output)
        if block.get("type") == "child_page"
        and str((block.get("props") or {}).get("page_id") or "") == page_id
    ), -1)
    if current_index >= 0:
        reference = output.pop(current_index)
    elif create:
        reference = {
            "type": "child_page",
            "content": str(page.get("title") or "Untitled"),
            "props": {
                "page_id": page_id,
                "title": str(page.get("title") or "Untitled"),
                "icon": str(page.get("icon") or ""),
            },
        }
    else:
        return blocks, False

    target_id = str(before_id or after_id or "")
    target_index = next((
        index for index, block in enumerate(output)
        if block.get("type") == "child_page"
        and str((block.get("props") or {}).get("page_id") or "") == target_id
    ), -1)
    if target_index >= 0:
        insert_at = target_index + (1 if after_id else 0)
    elif current_index >= 0 and not create:
        insert_at = min(current_index, len(output))
    else:
        insert_at = len(output)
    output.insert(insert_at, reference)
    changed = create or current_index != insert_at
    return output, changed


def move_page(
    user_id: str | None,
    page_id: str,
    *,
    parent_id: str = "",
    before_id: str = "",
    after_id: str = "",
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    parent_id = str(parent_id or "").strip()
    before_id = str(before_id or "").strip()
    after_id = str(after_id or "").strip()
    if before_id and after_id:
        raise ValueError("use either before_id or after_id")
    if page_id in {parent_id, before_id, after_id}:
        raise ValueError("page cannot be moved onto itself")

    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        source = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if source is None or str(source["owner_user_id"] or "") != uid:
            raise FileNotFoundError("page not found or permission denied")
        _page = dict(source)
        if _page_write_locked(_json_load(_page.get("settings_json"), {}), uid, page_id):
            raise PermissionError("page is locked")
        old_parent_id = str(_page.get("parent_id") or "")
        descendants = _page_descendants(con, page_id)
        target_id = before_id or after_id
        if target_id:
            target = con.execute(
                "SELECT id, parent_id FROM pages WHERE id=? AND archived=0",
                (target_id,),
            ).fetchone()
            if not target:
                raise FileNotFoundError(target_id)
            parent_id = str(target["parent_id"] or "")
            if parent_id == page_id:
                raise ValueError("page cannot be moved relative to its own child")
        if old_parent_id and old_parent_id != parent_id:
            old_parent = con.execute(
                "SELECT settings_json FROM pages WHERE id=? AND archived=0",
                (old_parent_id,),
            ).fetchone()
            old_settings = _json_load(old_parent["settings_json"], {}) if old_parent else {}
            if _page_write_locked(old_settings, uid, old_parent_id):
                raise PermissionError("parent page is locked")
        if parent_id:
            if parent_id in descendants:
                raise ValueError("page cannot be moved under its descendant")
            parent = con.execute(
                "SELECT id, settings_json FROM pages WHERE id=? AND archived=0",
                (parent_id,),
            ).fetchone()
            if not parent:
                raise FileNotFoundError(parent_id)
            parent_settings = _json_load(parent["settings_json"], {})
            if _page_write_locked(parent_settings, uid, parent_id):
                raise PermissionError("parent page is locked")

        if parent_id:
            rows = con.execute(
                "SELECT id FROM pages WHERE archived=0 AND parent_id=? AND id<>? ORDER BY position, updated_at DESC",
                (parent_id, page_id),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id FROM pages WHERE archived=0 AND parent_id IS NULL AND id<>? ORDER BY position, updated_at DESC",
                (page_id,),
            ).fetchall()
        sibling_ids = [str(row["id"]) for row in rows]
        insert_at = len(sibling_ids)
        if before_id and before_id in sibling_ids:
            insert_at = sibling_ids.index(before_id)
        elif after_id and after_id in sibling_ids:
            insert_at = sibling_ids.index(after_id) + 1
        sibling_ids.insert(insert_at, page_id)

        now = _now()
        con.execute("UPDATE pages SET parent_id=?, updated_at=? WHERE id=?", (parent_id or None, now, page_id))
        changed_page_ids = {page_id, *sibling_ids}
        for pos, sibling_id in enumerate(sibling_ids, start=1):
            con.execute("UPDATE pages SET position=? WHERE id=?", (pos, sibling_id))
        if old_parent_id and old_parent_id != parent_id:
            old_blocks, changed = _remove_child_references(_page_blocks_in_connection(con, old_parent_id), page_id)
            if changed:
                _replace_blocks_in_connection(con, owner, old_parent_id, old_blocks, now=now)
                changed_page_ids.add(old_parent_id)
                _record_page_version(con, owner, old_parent_id, now=now)
        if parent_id and old_parent_id != parent_id:
            new_blocks, _ = _remove_child_references(_page_blocks_in_connection(con, parent_id), page_id)
            new_blocks, changed = _position_child_page_reference(
                new_blocks,
                _page,
                before_id=before_id,
                after_id=after_id,
                create=True,
            )
            if changed:
                _replace_blocks_in_connection(con, owner, parent_id, new_blocks, now=now)
                changed_page_ids.add(parent_id)
                _record_page_version(con, owner, parent_id, now=now)
        elif parent_id and (before_id or after_id):
            parent_blocks, changed = _position_child_page_reference(
                _page_blocks_in_connection(con, parent_id),
                _page,
                before_id=before_id,
                after_id=after_id,
            )
            if changed:
                _replace_blocks_in_connection(con, owner, parent_id, parent_blocks, now=now)
                changed_page_ids.add(parent_id)
                _record_page_version(con, owner, parent_id, now=now)
        _bump_page_revisions(con, changed_page_ids, now=now, update_timestamp=False)
        con.commit()
    return get_page(uid, page_id)


def duplicate_page(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    now = _now()
    copied_paths: list[str] = []
    root_new_id = ""
    try:
        with _write_connection(owner) as con:
            con.execute("BEGIN IMMEDIATE")
            source = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
            if source is None or str(source["owner_user_id"] or "") != uid:
                raise FileNotFoundError("page not found or permission denied")
            page = dict(source)
            if _page_write_locked(_json_load(page.get("settings_json"), {}), uid, page_id):
                raise PermissionError("page is locked")
            ordered_pages: list[dict[str, Any]] = []
            visited: set[str] = set()

            def collect(current_id: str) -> None:
                if current_id in visited:
                    return
                visited.add(current_id)
                row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (current_id,)).fetchone()
                if not row:
                    return
                ordered_pages.append(dict(row))
                children = con.execute(
                    "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position, updated_at DESC",
                    (current_id,),
                ).fetchall()
                for child in children:
                    collect(str(child["id"]))

            collect(page_id)
            if not ordered_pages:
                raise FileNotFoundError("page not found")
            page_map = {str(item["id"]): _page_id() for item in ordered_pages}
            blocks_by_page: dict[str, list[dict[str, Any]]] = {}
            block_map: dict[str, str] = {}
            for item in ordered_pages:
                old_page_id = str(item["id"])
                block_rows = con.execute(
                    "SELECT * FROM blocks WHERE page_id=? ORDER BY position",
                    (old_page_id,),
                ).fetchall()
                blocks_by_page[old_page_id] = [_block_from_row(dict(row)) for row in block_rows]
                for block in blocks_by_page[old_page_id]:
                    block_map[str(block["id"])] = _block_id()
            root_new_id = page_map[page_id]
            root_parent_id = str(page.get("parent_id") or "")
            if root_parent_id:
                pos_row = con.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id=?",
                    (root_parent_id,),
                ).fetchone()
            else:
                pos_row = con.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id IS NULL"
                ).fetchone()
            root_position = int(pos_row["pos"] if pos_row else 1)

            for item in ordered_pages:
                old_id = str(item["id"])
                old_parent = str(item.get("parent_id") or "")
                new_parent = page_map.get(old_parent, root_parent_id if old_id == page_id else "")
                title = str(item.get("title") or "Untitled")
                if old_id == page_id:
                    title = f"{title} copy"
                con.execute(
                    """
                    INSERT INTO pages(id, owner_user_id, parent_id, title, icon, cover, settings_json, position, archived, archived_at, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        page_map[old_id], owner, new_parent or None, title[:160],
                        str(item.get("icon") or "")[:64], str(item.get("cover") or "")[:1000],
                        _json_dump(_copied_page_settings(item.get("settings_json"))),
                        root_position if old_id == page_id else int(item.get("position") or 0),
                        0, None, now, now,
                    ),
                )

            file_map: dict[str, str] = {}
            for item in ordered_pages:
                old_page_id = str(item["id"])
                new_page_id = page_map[old_page_id]
                file_rows = con.execute("SELECT * FROM files WHERE page_id=? ORDER BY created_at", (old_page_id,)).fetchall()
                for file_row_raw in file_rows:
                    file_row = dict(file_row_raw)
                    old_file_id = str(file_row["id"])
                    new_file_id = _file_id()
                    file_map[old_file_id] = new_file_id
                    safe_name = _safe_filename(file_row.get("original_name") or file_row.get("stored_name") or "file")
                    stored_name = f"{new_file_id}-{safe_name}"
                    source_path = os.path.join(_workspace_root(owner), str(file_row.get("rel_path") or ""))
                    destination = os.path.join(_page_file_dir(owner, new_page_id, create=True), stored_name)
                    if os.path.isfile(source_path):
                        shutil.copy2(source_path, destination)
                        copied_paths.append(destination)
                    rel_path = os.path.relpath(destination, _workspace_root(owner)).replace("\\", "/")
                    con.execute(
                        """
                        INSERT INTO files(id, page_id, owner_user_id, uploader_user_id, original_name, stored_name, rel_path, mime_type, size, created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            new_file_id, new_page_id, owner, uid, safe_name, stored_name, rel_path,
                            file_row.get("mime_type") or "application/octet-stream",
                            int(file_row.get("size") or 0), now,
                        ),
                    )

            def remap_refs(value: Any) -> Any:
                if isinstance(value, list):
                    return [remap_refs(item) for item in value]
                if isinstance(value, dict):
                    output = {key: remap_refs(item) for key, item in value.items()}
                    if str(output.get("page_id") or "") in page_map:
                        output["page_id"] = page_map[str(output["page_id"])]
                    if str(output.get("file_id") or "") in file_map:
                        output["file_id"] = file_map[str(output["file_id"])]
                    return output
                if isinstance(value, str):
                    if value in page_map:
                        return page_map[value]
                    if value in file_map:
                        return file_map[value]
                    if value in block_map:
                        return block_map[value]
                return value

            for item in ordered_pages:
                old_page_id = str(item["id"])
                new_page_id = page_map[old_page_id]
                blocks: list[dict[str, Any]] = []
                for source_block in blocks_by_page.get(old_page_id, []):
                    block = json.loads(json.dumps(source_block, ensure_ascii=False))
                    block["id"] = block_map[str(block["id"])]
                    block.pop("created_at", None)
                    block.pop("updated_at", None)
                    block["props"] = remap_refs(block.get("props") or {})
                    blocks.append(block)
                _replace_blocks_in_connection(con, owner, new_page_id, blocks, now=now)
                _record_page_version(con, owner, new_page_id, now=now)
            con.commit()
    except Exception:
        for path in copied_paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        raise
    return get_page(uid, root_new_id)


_IDEMPOTENT_DUPLICATE_MARKER = "_np2b_duplicate_request_sha256"
_IDEMPOTENT_ARCHIVE_MARKER = "_np2b_archive_request_sha256"
_IDEMPOTENT_RESTORE_MARKER = "_np2b_restore_request_sha256"
_MAX_IDEMPOTENT_DUPLICATE_PAGES = 128
_MAX_IDEMPOTENT_DUPLICATE_BLOCKS = 10_000
_MAX_IDEMPOTENT_DUPLICATE_FILES = 5_000
_MAX_IDEMPOTENT_DUPLICATE_FILE_BYTES = 128 * 1024 * 1024


def _duplicate_operation_identity(
    user_id: str,
    *,
    namespace: str,
    operation_id: str,
    source_page_id: str,
) -> tuple[str, str]:
    namespace = str(namespace or "").strip()
    operation_id = str(operation_id or "").strip()
    source_page_id = str(source_page_id or "").strip()
    if not namespace or len(namespace) > 160:
        raise ValueError("namespace must contain 1 to 160 characters")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    if not source_page_id or len(source_page_id) > 160:
        raise ValueError("source_page_id must contain 1 to 160 characters")
    envelope = json.dumps(
        {
            "actor": user_id,
            "namespace": namespace,
            "operation_id": operation_id,
            "source_page_id": source_page_id,
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    root_id = page_id_for_operation(
        user_id,
        namespace=namespace,
        operation_id=operation_id,
    )
    return "sha256:" + digest, root_id


def _duplicate_entity_id(prefix: str, request_sha256: str, kind: str, source_id: str) -> str:
    digest = hashlib.sha256(
        f"code_assist.notion.workspace_duplicate_entity.v1\0{request_sha256}\0{kind}\0{source_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return prefix + digest[:32]


def get_duplicate_page_idempotent_replay(
    user_id: str | None,
    page_id: str,
    *,
    namespace: str,
    operation_id: str,
) -> dict[str, Any] | None:
    """Return an exact committed duplicate replay without mutating the shard."""

    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    request_sha256, root_new_id = _duplicate_operation_identity(
        uid,
        namespace=namespace,
        operation_id=operation_id,
        source_page_id=page_id,
    )
    page_ids: list[str] = []
    with _connection(uid) as con:
        root = con.execute(
            "SELECT * FROM pages WHERE id=? LIMIT 1", (root_new_id,)
        ).fetchone()
        if root is None:
            return None
        current = dict(root)
        settings = _json_load(current.get("settings_json"), {})
        marker = (
            str(settings.get(_IDEMPOTENT_DUPLICATE_MARKER) or "")
            if isinstance(settings, dict)
            else ""
        )
        if (
            str(current.get("owner_user_id") or "") != uid
            or int(current.get("archived") or 0) != 0
            or marker != request_sha256
        ):
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        page_ids = [root_new_id]
        cursor = 0
        while cursor < len(page_ids):
            if len(page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
                raise ValueError("duplicate subtree exceeds page limit")
            children = con.execute(
                "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position,id",
                (page_ids[cursor],),
            ).fetchall()
            page_ids.extend(str(row["id"]) for row in children)
            cursor += 1
    return {
        "page": get_page(uid, root_new_id)["page"],
        "page_ids": page_ids,
        "replayed": True,
    }


def duplicate_page_idempotent(
    user_id: str | None,
    page_id: str,
    *,
    namespace: str,
    operation_id: str,
) -> dict[str, Any]:
    """Duplicate one self-owned subtree with a deterministic replay identity.

    The root ID commits the actor, workspace namespace, and operation ID.  A
    private digest in the copied root settings additionally commits the source
    root, so reusing an operation for a different source fails closed.  The
    source transaction creates every page/block/file row before commit; a
    response-loss retry returns the already committed subtree without copying
    it again.
    """

    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    request_sha256, root_new_id = _duplicate_operation_identity(
        uid,
        namespace=namespace,
        operation_id=operation_id,
        source_page_id=page_id,
    )
    now = _now()
    copied_paths: list[str] = []
    result_page_ids: list[str] = []
    replayed = False
    try:
        with _write_connection(owner) as con:
            con.execute("BEGIN IMMEDIATE")
            existing_root = con.execute(
                "SELECT * FROM pages WHERE id=? LIMIT 1", (root_new_id,)
            ).fetchone()
            if existing_root is not None:
                current = dict(existing_root)
                settings = _json_load(current.get("settings_json"), {})
                marker = (
                    str(settings.get(_IDEMPOTENT_DUPLICATE_MARKER) or "")
                    if isinstance(settings, dict)
                    else ""
                )
                if (
                    str(current.get("owner_user_id") or "") != uid
                    or int(current.get("archived") or 0) != 0
                    or marker != request_sha256
                ):
                    current_revision = max(1, int(current.get("revision") or 1))
                    con.rollback()
                    raise OperationIdConflict(operation_id, current_revision)
                result_page_ids = [root_new_id]
                cursor = 0
                while cursor < len(result_page_ids):
                    if len(result_page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
                        con.rollback()
                        raise ValueError("duplicate subtree exceeds page limit")
                    children = con.execute(
                        "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position,id",
                        (result_page_ids[cursor],),
                    ).fetchall()
                    result_page_ids.extend(str(row["id"]) for row in children)
                    cursor += 1
                con.rollback()
                replayed = True
            else:
                source = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
                if source is None or str(source["owner_user_id"] or "") != uid:
                    raise FileNotFoundError("page not found or permission denied")
                _page = dict(source)
                if _page_write_locked(_json_load(_page.get("settings_json"), {}), uid, page_id):
                    raise PermissionError("page is locked")
                ordered_pages: list[dict[str, Any]] = []
                visited: set[str] = set()

                def collect(current_id: str) -> None:
                    if current_id in visited:
                        return
                    visited.add(current_id)
                    row = con.execute(
                        "SELECT * FROM pages WHERE id=? AND archived=0", (current_id,)
                    ).fetchone()
                    if not row:
                        return
                    ordered_pages.append(dict(row))
                    if len(ordered_pages) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
                        raise ValueError("duplicate subtree exceeds page limit")
                    children = con.execute(
                        "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position,updated_at DESC,id",
                        (current_id,),
                    ).fetchall()
                    for child in children:
                        collect(str(child["id"]))

                collect(page_id)
                if not ordered_pages:
                    con.rollback()
                    raise FileNotFoundError("page not found")
                page_map = {
                    str(item["id"]): (
                        root_new_id
                        if str(item["id"]) == page_id
                        else _duplicate_entity_id(
                            "p_", request_sha256, "page", str(item["id"])
                        )
                    )
                    for item in ordered_pages
                }
                target_ids = sorted(page_map.values())
                placeholders = ",".join("?" for _ in target_ids)
                collisions = con.execute(
                    f"SELECT id,revision FROM pages WHERE id IN ({placeholders})",
                    tuple(target_ids),
                ).fetchall()
                if collisions:
                    current_revision = max(
                        1, max(int(row["revision"] or 1) for row in collisions)
                    )
                    con.rollback()
                    raise OperationIdConflict(operation_id, current_revision)

                blocks_by_page: dict[str, list[dict[str, Any]]] = {}
                block_map: dict[str, str] = {}
                total_blocks = 0
                for item in ordered_pages:
                    old_page_id = str(item["id"])
                    block_rows = con.execute(
                        "SELECT * FROM blocks WHERE page_id=? ORDER BY position",
                        (old_page_id,),
                    ).fetchall()
                    blocks = [_block_from_row(dict(row)) for row in block_rows]
                    total_blocks += len(blocks)
                    if total_blocks > _MAX_IDEMPOTENT_DUPLICATE_BLOCKS:
                        raise ValueError("duplicate subtree exceeds block limit")
                    blocks_by_page[old_page_id] = blocks
                    for block in blocks:
                        old_block_id = str(block["id"])
                        block_map[old_block_id] = _duplicate_entity_id(
                            "b_", request_sha256, "block", old_block_id
                        )

                root_parent_id = str(_page.get("parent_id") or "")
                if root_parent_id:
                    pos_row = con.execute(
                        "SELECT COALESCE(MAX(position),0)+1 AS pos FROM pages WHERE archived=0 AND parent_id=?",
                        (root_parent_id,),
                    ).fetchone()
                else:
                    pos_row = con.execute(
                        "SELECT COALESCE(MAX(position),0)+1 AS pos FROM pages WHERE archived=0 AND parent_id IS NULL"
                    ).fetchone()
                root_position = int(pos_row["pos"] if pos_row else 1)

                for item in ordered_pages:
                    old_id = str(item["id"])
                    old_parent = str(item.get("parent_id") or "")
                    new_parent = page_map.get(
                        old_parent, root_parent_id if old_id == page_id else ""
                    )
                    title = str(item.get("title") or "Untitled")
                    settings = _copied_page_settings(item.get("settings_json"))
                    if old_id == page_id:
                        title = f"{title} copy"
                        settings = dict(settings)
                        settings[_IDEMPOTENT_DUPLICATE_MARKER] = request_sha256
                    con.execute(
                        """
                        INSERT INTO pages(
                          id,owner_user_id,parent_id,title,icon,cover,settings_json,
                          position,archived,archived_at,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?)
                        """,
                        (
                            page_map[old_id],
                            owner,
                            new_parent or None,
                            title[:160],
                            str(item.get("icon") or "")[:64],
                            str(item.get("cover") or "")[:1000],
                            _json_dump(settings),
                            root_position
                            if old_id == page_id
                            else int(item.get("position") or 0),
                            now,
                            now,
                        ),
                    )

                file_map: dict[str, str] = {}
                file_rows_by_page: dict[str, list[dict[str, Any]]] = {}
                total_files = 0
                total_file_bytes = 0
                workspace_root = os.path.abspath(_workspace_root(owner))
                for item in ordered_pages:
                    old_page_id = str(item["id"])
                    rows = [
                        dict(row)
                        for row in con.execute(
                            "SELECT * FROM files WHERE page_id=? ORDER BY created_at,id",
                            (old_page_id,),
                        ).fetchall()
                    ]
                    total_files += len(rows)
                    total_file_bytes += sum(max(0, int(row.get("size") or 0)) for row in rows)
                    if total_files > _MAX_IDEMPOTENT_DUPLICATE_FILES:
                        raise ValueError("duplicate subtree exceeds file limit")
                    if total_file_bytes > _MAX_IDEMPOTENT_DUPLICATE_FILE_BYTES:
                        raise ValueError("duplicate subtree exceeds file byte limit")
                    file_rows_by_page[old_page_id] = rows
                    for file_row in rows:
                        old_file_id = str(file_row["id"])
                        file_map[old_file_id] = _duplicate_entity_id(
                            "f_", request_sha256, "file", old_file_id
                        )

                for item in ordered_pages:
                    old_page_id = str(item["id"])
                    new_page_id = page_map[old_page_id]
                    for file_row in file_rows_by_page[old_page_id]:
                        old_file_id = str(file_row["id"])
                        new_file_id = file_map[old_file_id]
                        safe_name = _safe_filename(
                            file_row.get("original_name")
                            or file_row.get("stored_name")
                            or "file"
                        )
                        stored_name = f"{new_file_id}-{safe_name}"
                        source_path = os.path.abspath(
                            os.path.join(workspace_root, str(file_row.get("rel_path") or ""))
                        )
                        if os.path.commonpath([workspace_root, source_path]) != workspace_root:
                            raise ValueError("duplicate source file path is unsafe")
                        destination = os.path.abspath(
                            os.path.join(
                                _page_file_dir(owner, new_page_id, create=True), stored_name
                            )
                        )
                        if os.path.commonpath([workspace_root, destination]) != workspace_root:
                            raise ValueError("duplicate destination file path is unsafe")
                        if os.path.isfile(source_path):
                            shutil.copy2(source_path, destination)
                            copied_paths.append(destination)
                        rel_path = os.path.relpath(destination, workspace_root).replace(
                            "\\", "/"
                        )
                        con.execute(
                            """
                            INSERT INTO files(
                              id,page_id,owner_user_id,uploader_user_id,original_name,
                              stored_name,rel_path,mime_type,size,created_at
                            ) VALUES(?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                new_file_id,
                                new_page_id,
                                owner,
                                uid,
                                safe_name,
                                stored_name,
                                rel_path,
                                file_row.get("mime_type")
                                or "application/octet-stream",
                                int(file_row.get("size") or 0),
                                now,
                            ),
                        )

                def remap_refs(value: Any) -> Any:
                    if isinstance(value, list):
                        return [remap_refs(item) for item in value]
                    if isinstance(value, dict):
                        output = {key: remap_refs(item) for key, item in value.items()}
                        if str(output.get("page_id") or "") in page_map:
                            output["page_id"] = page_map[str(output["page_id"])]
                        if str(output.get("file_id") or "") in file_map:
                            output["file_id"] = file_map[str(output["file_id"])]
                        return output
                    if isinstance(value, str):
                        if value in page_map:
                            return page_map[value]
                        if value in file_map:
                            return file_map[value]
                        if value in block_map:
                            return block_map[value]
                    return value

                for item in ordered_pages:
                    old_page_id = str(item["id"])
                    new_page_id = page_map[old_page_id]
                    blocks: list[dict[str, Any]] = []
                    for source_block in blocks_by_page.get(old_page_id, []):
                        block = json.loads(json.dumps(source_block, ensure_ascii=False))
                        block["id"] = block_map[str(block["id"])]
                        block.pop("created_at", None)
                        block.pop("updated_at", None)
                        block["props"] = remap_refs(block.get("props") or {})
                        blocks.append(block)
                    _replace_blocks_in_connection(
                        con, owner, new_page_id, blocks, now=now
                    )
                    _record_page_version(con, owner, new_page_id, now=now)
                result_page_ids = [page_map[str(item["id"])] for item in ordered_pages]
                con.commit()
    except Exception:
        for path in copied_paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        raise
    return {
        "page": get_page(uid, root_new_id)["page"],
        "page_ids": result_page_ids,
        "replayed": replayed,
    }


def self_owned_page_lifecycle_scope(
    user_id: str | None,
    page_id: str,
    *,
    include_archived: bool = False,
    include_ancestors: bool = False,
) -> dict[str, Any]:
    """Return one exact self-owned lifecycle scope without enumerating shards."""

    uid = _uid(user_id)
    page_id = str(page_id or "").strip()
    if not page_id or len(page_id) > 160:
        raise ValueError("valid page_id required")
    with _connection(uid) as con:
        row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if (
            row is None
            or str(row["owner_user_id"] or "") != uid
            or (not include_archived and int(row["archived"] or 0) != 0)
        ):
            raise FileNotFoundError("page not found")
        page_ids = {
            page_id,
            *_page_descendants(con, page_id, include_archived=include_archived),
        }
        if include_ancestors:
            page_ids.update(
                _page_ancestors(con, page_id, include_archived=include_archived)
            )
        if len(page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
            raise ValueError("page lifecycle subtree exceeds limit")
        return {
            "page_id": page_id,
            "archived": bool(int(row["archived"] or 0)),
            "page_ids": sorted(page_ids),
        }


def _page_lifecycle_request_digest(
    user_id: str,
    *,
    action: str,
    namespace: str,
    operation_id: str,
    page_id: str,
) -> str:
    action = str(action or "").strip()
    namespace = str(namespace or "").strip()
    operation_id = str(operation_id or "").strip()
    page_id = str(page_id or "").strip()
    if action not in {"archive", "restore"}:
        raise ValueError("unsupported page lifecycle action")
    if not namespace or len(namespace) > 160:
        raise ValueError("namespace must contain 1 to 160 characters")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    if not page_id or len(page_id) > 160:
        raise ValueError("valid page_id required")
    envelope = json.dumps(
        {
            "action": action,
            "actor": user_id,
            "namespace": namespace,
            "operation_id": operation_id,
            "page_id": page_id,
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def get_page_lifecycle_idempotent_replay(
    user_id: str | None,
    page_id: str,
    *,
    action: str,
    namespace: str,
    operation_id: str,
) -> dict[str, Any] | None:
    """Probe one exact committed archive/restore effect without mutation."""

    uid = _uid(user_id)
    digest = _page_lifecycle_request_digest(
        uid,
        action=action,
        namespace=namespace,
        operation_id=operation_id,
        page_id=page_id,
    )
    marker_name = (
        _IDEMPOTENT_ARCHIVE_MARKER
        if action == "archive"
        else _IDEMPOTENT_RESTORE_MARKER
    )
    with _connection(uid) as con:
        row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if row is None or str(row["owner_user_id"] or "") != uid:
            raise FileNotFoundError("page not found")
        current = dict(row)
        settings = _json_load(current.get("settings_json"), {})
        marker = (
            str(settings.get(marker_name) or "")
            if isinstance(settings, dict)
            else ""
        )
        expected_archived = action == "archive"
        is_archived = bool(int(current.get("archived") or 0))
        if marker == digest and is_archived == expected_archived:
            page_ids = {
                page_id,
                *_page_descendants(con, page_id, include_archived=True),
            }
            if len(page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
                raise ValueError("page lifecycle subtree exceeds limit")
            return {
                "action": action,
                "page_id": page_id,
                "page_ids": sorted(page_ids),
                "replayed": True,
            }
        if marker == digest:
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        return None


def archive_page_idempotent(
    user_id: str | None,
    page_id: str,
    *,
    namespace: str,
    operation_id: str,
) -> dict[str, Any]:
    """Archive one self-owned subtree exactly once for a workspace command."""

    uid = _uid(user_id)
    digest = _page_lifecycle_request_digest(
        uid,
        action="archive",
        namespace=namespace,
        operation_id=operation_id,
        page_id=page_id,
    )
    replay = get_page_lifecycle_idempotent_replay(
        uid,
        page_id,
        action="archive",
        namespace=namespace,
        operation_id=operation_id,
    )
    if replay is not None:
        return {
            "id": page_id,
            "archived": True,
            "archived_count": len(replay["page_ids"]),
            "page_ids": replay["page_ids"],
            "replayed": True,
        }
    with _write_connection(uid) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if row is None or str(row["owner_user_id"] or "") != uid:
            raise FileNotFoundError("page not found")
        current = dict(row)
        settings = _json_load(current.get("settings_json"), {})
        if not isinstance(settings, dict):
            settings = {}
        if int(current.get("archived") or 0) != 0:
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        if _page_write_locked(settings, uid, page_id):
            raise PermissionError("page is locked")
        previous_marker = str(settings.get(_IDEMPOTENT_ARCHIVE_MARKER) or "")
        if previous_marker == digest:
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        settings = dict(settings)
        settings[_IDEMPOTENT_ARCHIVE_MARKER] = digest
        page_ids = {page_id, *_page_descendants(con, page_id)}
        if len(page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
            raise ValueError("page lifecycle subtree exceeds limit")
        now = _now()
        con.execute(
            "UPDATE pages SET settings_json=? WHERE id=?",
            (_json_dump(settings), page_id),
        )
        placeholders = ",".join("?" for _ in page_ids)
        con.execute(
            f"UPDATE pages SET archived=1,archived_at=?,updated_at=? WHERE id IN ({placeholders})",
            (now, now, *sorted(page_ids)),
        )
        _bump_page_revisions(con, page_ids, now=now, update_timestamp=False)
        con.commit()
    return {
        "id": page_id,
        "archived": True,
        "archived_count": len(page_ids),
        "page_ids": sorted(page_ids),
        "replayed": False,
    }


def restore_page_idempotent(
    user_id: str | None,
    page_id: str,
    *,
    namespace: str,
    operation_id: str,
) -> dict[str, Any]:
    """Restore one self-owned archived subtree exactly once."""

    uid = _uid(user_id)
    digest = _page_lifecycle_request_digest(
        uid,
        action="restore",
        namespace=namespace,
        operation_id=operation_id,
        page_id=page_id,
    )
    replay = get_page_lifecycle_idempotent_replay(
        uid,
        page_id,
        action="restore",
        namespace=namespace,
        operation_id=operation_id,
    )
    if replay is not None:
        return {
            "page": get_page(uid, page_id)["page"],
            "page_ids": replay["page_ids"],
            "replayed": True,
        }
    with _write_connection(uid) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if row is None or str(row["owner_user_id"] or "") != uid:
            raise FileNotFoundError("archived page not found")
        current = dict(row)
        settings = _json_load(current.get("settings_json"), {})
        if not isinstance(settings, dict):
            settings = {}
        if int(current.get("archived") or 0) != 1:
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        previous_marker = str(settings.get(_IDEMPOTENT_RESTORE_MARKER) or "")
        if previous_marker == digest:
            raise OperationIdConflict(
                operation_id, max(1, int(current.get("revision") or 1))
            )
        settings = dict(settings)
        settings[_IDEMPOTENT_RESTORE_MARKER] = digest
        page_ids = {
            page_id,
            *_page_descendants(con, page_id, include_archived=True),
            *_page_ancestors(con, page_id, include_archived=True),
        }
        if len(page_ids) > _MAX_IDEMPOTENT_DUPLICATE_PAGES:
            raise ValueError("page lifecycle subtree exceeds limit")
        now = _now()
        con.execute(
            "UPDATE pages SET settings_json=? WHERE id=?",
            (_json_dump(settings), page_id),
        )
        placeholders = ",".join("?" for _ in page_ids)
        con.execute(
            f"UPDATE pages SET archived=0,archived_at=NULL,updated_at=? WHERE id IN ({placeholders})",
            (now, *sorted(page_ids)),
        )
        _bump_page_revisions(con, page_ids, now=now, update_timestamp=False)
        con.commit()
    return {
        "page": get_page(uid, page_id)["page"],
        "page_ids": sorted(page_ids),
        "replayed": False,
    }


def list_archived_pages_from_owner_by_ids(
    requester_user_id: str | None,
    owner_user_id: str | None,
    page_ids: set[str] | list[str] | tuple[str, ...],
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    """Read only explicitly catalog-authorized archived locators."""

    requester = _uid(requester_user_id)
    owner = _uid(owner_user_id)
    if requester != owner:
        raise PermissionError("workspace trash requires source owner")
    normalized_ids = sorted(
        {str(page_id or "").strip() for page_id in page_ids if str(page_id or "").strip()}
    )
    if len(normalized_ids) > 512:
        raise ValueError("archived page locator batch exceeds limit")
    if not normalized_ids or not _database_exists(owner):
        return []
    q = str(query or "").strip().casefold()
    if len(q) > 200:
        raise ValueError("query must be at most 200 characters")
    placeholders = ",".join("?" for _ in normalized_ids)
    with _connection(owner) as con:
        rows = con.execute(
            f"SELECT * FROM pages WHERE archived=1 AND id IN ({placeholders}) ORDER BY updated_at DESC,id",
            tuple(normalized_ids),
        ).fetchall()
    items = [
        _page_summary(dict(row), owner, "owner", "own") for row in rows
    ]
    if q:
        items = [
            item for item in items if q in str(item.get("title") or "").casefold()
        ]
    for item in items:
        item["last_editor_user_id"] = owner
        item["workspace_scope"] = "workspace"
        item["deleted_at"] = int(
            item.get("archived_at") or item.get("updated_at") or 0
        )
        item["days_left"] = max(
            0, 30 - int((_now() - int(item["deleted_at"] or 0)) / 86400)
        )
    return items


def archive_page(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        _effective_edit_role(con, uid, owner, page_id)
        source = con.execute("SELECT settings_json FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if source is None:
            raise FileNotFoundError("page not found or permission denied")
        if _page_write_locked(_json_load(source["settings_json"], {}), uid, page_id):
            raise PermissionError("page is locked")
        now = _now()
        page_ids = {page_id, *_page_descendants(con, page_id)}
        placeholders = ",".join("?" for _ in page_ids)
        con.execute(
            f"UPDATE pages SET archived=1, archived_at=?, updated_at=? WHERE id IN ({placeholders})",
            (now, now, *sorted(page_ids)),
        )
        _bump_page_revisions(con, page_ids, now=now, update_timestamp=False)
        con.commit()
    return {"id": page_id, "archived": True, "archived_count": len(page_ids)}


def list_archived_pages(
    user_id: str | None,
    query: str = "",
    *,
    editor: str = "",
    scope: str = "all",
) -> list[dict[str, Any]]:
    uid = _uid(user_id)
    q = str(query or "").strip().lower()
    editor = str(editor or "").strip().lower()
    scope = str(scope or "all").strip().lower()
    rows: list[sqlite3.Row] = []
    shared_page_ids: set[str] = set()
    if _database_exists(uid):
        with _connection(uid) as con:
            rows = con.execute(
                "SELECT * FROM pages WHERE archived=1 ORDER BY updated_at DESC"
            ).fetchall()
            shared_page_ids = {
                str(row["page_id"])
                for row in con.execute(
                    "SELECT DISTINCT page_id FROM page_shares WHERE revoked_at IS NULL"
                ).fetchall()
            }
    items = [_page_summary(dict(row), uid, "owner", "own") for row in rows]
    if q:
        items = [
            item for item in items
            if q in (item.get("title") or "").lower()
            or q in (item.get("owner_user_id") or "").lower()
        ]
    if editor and editor not in {"me", uid}:
        items = []
    if scope in {"workspace", "teamspace"}:
        items = [item for item in items if item["id"] in shared_page_ids]
    elif scope in {"private", "personal"}:
        items = [item for item in items if item["id"] not in shared_page_ids]
    for item in items:
        item["last_editor_user_id"] = uid
        item["workspace_scope"] = "workspace" if item["id"] in shared_page_ids else "private"
        item["deleted_at"] = int(item.get("archived_at") or item.get("updated_at") or 0)
        item["days_left"] = max(0, 30 - int((_now() - int(item["deleted_at"] or 0)) / 86400))
    return items


def restore_page(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("page_id required")
    with _write_connection(uid) as con:
        row = con.execute(
            "SELECT * FROM pages WHERE id=? AND archived=1",
            (page_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("archived page not found")
        now = _now()
        page_ids = {
            page_id,
            *_page_descendants(con, page_id, include_archived=True),
            *_page_ancestors(con, page_id, include_archived=True),
        }
        placeholders = ",".join("?" for _ in page_ids)
        con.execute(
            f"UPDATE pages SET archived=0, archived_at=NULL, updated_at=? WHERE id IN ({placeholders})",
            (now, *sorted(page_ids)),
        )
        _bump_page_revisions(con, page_ids, now=now, update_timestamp=False)
        con.commit()
    return get_page(uid, page_id)


def delete_page_permanent(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("page_id required")
    with _write_connection(uid) as con:
        row = con.execute(
            "SELECT id FROM pages WHERE id=? AND archived=1",
            (page_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("archived page not found")
        page_ids = {page_id, *_page_descendants(con, page_id, include_archived=True)}
        placeholders = ",".join("?" for _ in page_ids)
        file_rows = con.execute(
            f"SELECT rel_path FROM files WHERE page_id IN ({placeholders})",
            tuple(sorted(page_ids)),
        ).fetchall()
        con.execute(f"DELETE FROM pages WHERE id IN ({placeholders})", tuple(sorted(page_ids)))
        con.commit()
    workspace = os.path.abspath(_workspace_root(uid))
    deleted_files = 0
    for row in file_rows:
        path = os.path.abspath(os.path.join(workspace, str(row["rel_path"] or "")))
        try:
            if os.path.commonpath([workspace, path]) != workspace:
                continue
            if os.path.isfile(path):
                os.remove(path)
                deleted_files += 1
        except (OSError, ValueError):
            continue
    return {"id": page_id, "deleted": True, "deleted_count": len(page_ids), "deleted_file_count": deleted_files}


def _block_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row.get("block_type") or "paragraph",
        "content": row.get("content") or "",
        "props": _json_load(row.get("props_json"), {}),
        "position": int(row.get("position") or 0),
        "created_at": int(row.get("created_at") or 0),
        "updated_at": int(row.get("updated_at") or 0),
    }


def _file_from_row(row: dict[str, Any], owner: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "page_id": row["page_id"],
        "owner_user_id": owner,
        "uploader_user_id": row.get("uploader_user_id") or owner,
        "original_name": row.get("original_name") or row.get("stored_name") or "",
        "stored_name": row.get("stored_name") or "",
        "rel_path": row.get("rel_path") or "",
        "mime_type": row.get("mime_type") or "",
        "size": int(row.get("size") or 0),
        "created_at": int(row.get("created_at") or 0),
        "url": f"/api/notion/files/{row['id']}",
    }


def _clean_block(block: dict[str, Any], position: int) -> dict[str, Any]:
    btype = str(block.get("type") or block.get("block_type") or "paragraph").strip()
    if btype not in BLOCK_TYPES:
        raise ValueError(f"unsupported block type: {btype}")
    if "props" in block and not isinstance(block.get("props"), dict):
        raise ValueError(f"block {position} props must be an object")
    props = block.get("props") or {}
    if len(_json_dump(props).encode("utf-8")) > 2_000_000:
        raise ValueError("block properties exceed 2MB")
    return {
        "id": str(block.get("id") or _block_id())[:80],
        "type": btype,
        "content": str(block.get("content") or "")[:200000],
        "props": props,
        "position": position,
    }


def _prepare_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        raise ValueError("blocks must be a list")
    if len(blocks) > 5000:
        raise ValueError("a page can contain at most 5000 blocks")
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(blocks, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"block {index} must be an object")
        prepared.append(_clean_block(raw, index))
    return prepared


def _block_content_signature(blocks: list[dict[str, Any]]) -> str:
    comparable = [
        {
            "id": str(block.get("id") or ""),
            "type": str(block.get("type") or block.get("block_type") or "paragraph"),
            "content": str(block.get("content") or ""),
            "props": block.get("props") if isinstance(block.get("props"), dict) else {},
            "position": int(block.get("position") or index),
        }
        for index, block in enumerate(blocks, start=1)
    ]
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _replace_blocks_in_connection(
    con: sqlite3.Connection,
    owner: str,
    page_id: str,
    blocks: list[dict[str, Any]],
    *,
    now: int | None = None,
) -> None:
    now = now or _now()
    con.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
    for idx, raw in enumerate(blocks or [], start=1):
        block = _clean_block(raw if isinstance(raw, dict) else {}, idx)
        con.execute(
            """
            INSERT INTO blocks(id, page_id, owner_user_id, position, block_type, content, props_json, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                block["id"],
                page_id,
                owner,
                block["position"],
                block["type"],
                block["content"],
                _json_dump(block["props"]),
                now,
                now,
            ),
        )
    con.execute("UPDATE pages SET updated_at=? WHERE id=?", (now, page_id))


def _propagate_synced_blocks(con: sqlite3.Connection, page_id: str, *, now: int | None = None) -> set[str]:
    now = now or _now()
    source_rows = con.execute(
        "SELECT * FROM blocks WHERE page_id=? AND block_type='synced_block' ORDER BY position",
        (page_id,),
    ).fetchall()
    sources: dict[str, dict[str, Any]] = {}
    for row_raw in source_rows:
        row = dict(row_raw)
        props = _json_load(row.get("props_json"), {})
        sync_id = str(props.get("sync_id") or "") if isinstance(props, dict) else ""
        if sync_id:
            sources[sync_id] = {"row": row, "props": props}
    if not sources:
        return set()
    affected: set[str] = set()
    target_rows = con.execute("SELECT * FROM blocks WHERE block_type='synced_block'").fetchall()
    for target_raw in target_rows:
        target = dict(target_raw)
        target_props = _json_load(target.get("props_json"), {})
        sync_id = str(target_props.get("sync_id") or "") if isinstance(target_props, dict) else ""
        source = sources.get(sync_id)
        if not source or target.get("id") == source["row"].get("id"):
            continue
        source_props = json.loads(json.dumps(source["props"], ensure_ascii=False))
        if isinstance(target_props, dict) and "indent" in target_props:
            source_props["indent"] = target_props["indent"]
        source_content = str(source["row"].get("content") or "")
        if str(target.get("content") or "") == source_content and target_props == source_props:
            continue
        con.execute(
            "UPDATE blocks SET content=?, props_json=?, updated_at=? WHERE id=?",
            (source_content, _json_dump(source_props), now, target["id"]),
        )
        affected.add(str(target.get("page_id") or ""))
    return affected


def _record_page_version(con: sqlite3.Connection, owner: str, page_id: str, *, now: int | None = None) -> str:
    now = now or _now()
    page_row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    if not page_row:
        return ""
    page = dict(page_row)
    block_rows = con.execute("SELECT * FROM blocks WHERE page_id=? ORDER BY position", (page_id,)).fetchall()
    blocks = [_block_from_row(dict(row)) for row in block_rows]
    for block in blocks:
        block.pop("created_at", None)
        block.pop("updated_at", None)
    blocks_json = json.dumps(blocks, ensure_ascii=False, sort_keys=True)
    previous = con.execute(
        "SELECT title, icon, cover, settings_json, blocks_json FROM page_versions WHERE page_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (page_id,),
    ).fetchone()
    signature = (
        str(page.get("title") or "Untitled"),
        str(page.get("icon") or ""),
        str(page.get("cover") or ""),
        str(page.get("settings_json") or "{}"),
        blocks_json,
    )
    if previous and signature == tuple(str(previous[key] or "") for key in ("title", "icon", "cover", "settings_json", "blocks_json")):
        return ""
    version_id = "v_" + uuid.uuid4().hex
    con.execute(
        """
        INSERT INTO page_versions(id, page_id, owner_user_id, title, icon, cover, settings_json, blocks_json, created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (version_id, page_id, owner, *signature, now),
    )
    stale = con.execute(
        "SELECT id FROM page_versions WHERE page_id=? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET 100",
        (page_id,),
    ).fetchall()
    if stale:
        con.executemany("DELETE FROM page_versions WHERE id=?", [(row["id"],) for row in stale])
    return version_id


def replace_blocks(user_id: str | None, page_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    settings = _json_load(page.get("settings_json"), {})
    if _page_write_locked(settings, uid, page_id):
        raise PermissionError("page is locked")
    prepared_blocks = _prepare_blocks(blocks)
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        role = _effective_edit_role(con, uid, owner, page_id)
        page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if not page_row:
            raise FileNotFoundError("page not found or permission denied")
        current_settings = _json_load(dict(page_row).get("settings_json"), {})
        if _page_write_locked(current_settings, uid, page_id):
            raise PermissionError("page is locked")
        current_blocks = _page_blocks_in_connection(con, page_id)
        if _block_content_signature(current_blocks) == _block_content_signature(prepared_blocks):
            con.rollback()
            return get_page(uid, page_id)
        now = _now()
        _replace_blocks_in_connection(con, owner, page_id, prepared_blocks, now=now)
        affected = _propagate_synced_blocks(con, page_id, now=now)
        _bump_page_revisions(con, {page_id, *affected}, now=now)
        for affected_page_id in {page_id, *affected}:
            _record_page_version(con, owner, affected_page_id, now=now)
        con.commit()
    return get_page(uid, page_id)


def _atomic_save_request_hash(
    requester_user_id: str,
    page_id: str,
    base_revision: int,
    page_updates: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> str:
    canonical = json.dumps(
        {
            "requester_user_id": requester_user_id,
            "page_id": page_id,
            "base_revision": base_revision,
            "page": page_updates,
            "blocks": blocks,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_page_atomic(
    user_id: str | None,
    page_id: str,
    *,
    base_revision: int,
    operation_id: str,
    page: dict[str, Any] | None,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically save page metadata and blocks with CAS and durable idempotency."""
    uid = _uid(user_id)
    page_id = str(page_id or "").strip()
    if not page_id:
        raise ValueError("page_id required")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        raise ValueError("base_revision must be a positive integer")
    operation_id = str(operation_id or "").strip()
    if not operation_id or len(operation_id) > 128:
        raise ValueError("operation_id must contain 1 to 128 characters")
    if page is not None and not isinstance(page, dict):
        raise ValueError("page must be an object")
    page_updates = dict(page or {})
    unsupported_page_fields = sorted(set(page_updates) - {"title", "icon", "cover", "settings"})
    if unsupported_page_fields:
        raise ValueError(f"unsupported atomic page fields: {', '.join(unsupported_page_fields)}")
    prepared_blocks = _prepare_blocks(blocks)
    request_hash = _atomic_save_request_hash(uid, page_id, base_revision, page_updates, blocks)

    owner, _role, _page = _require_page(uid, page_id, "edit")
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        if not _atomic_schema_ready(con):
            raise AtomicSaveUnavailable("atomic save schema migration is required for this workspace")
        page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if not page_row:
            raise FileNotFoundError("page not found or permission denied")
        current_page = dict(page_row)
        role = _effective_edit_role(con, uid, owner, page_id)
        current_revision = max(1, int(current_page.get("revision") or 1))

        prior = con.execute(
            """
            SELECT request_hash, result_json
            FROM page_save_operations
            WHERE requester_user_id=? AND operation_id=?
            """,
            (uid, operation_id),
        ).fetchone()
        if prior:
            if str(prior["request_hash"]) != request_hash:
                raise OperationIdConflict(operation_id, current_revision)
            replay = _json_load(prior["result_json"], {})
            if not isinstance(replay, dict):
                raise RuntimeError("stored atomic save result is invalid")
            con.rollback()
            return replay

        if current_revision != base_revision:
            raise PageRevisionConflict(current_revision)

        current_settings = _json_load(current_page.get("settings_json"), {})
        if not isinstance(current_settings, dict):
            current_settings = {}
        settings_update = page_updates.get("settings") if isinstance(page_updates.get("settings"), dict) else None
        if _page_write_locked(current_settings, uid, page_id) and not (settings_update is not None and settings_update.get("locked") is False):
            raise PermissionError("page is locked")

        metadata_changes = _page_metadata_changes(current_page, page_updates)
        current_blocks = _page_blocks_in_connection(con, page_id)
        blocks_changed = _block_content_signature(current_blocks) != _block_content_signature(prepared_blocks)
        now = _now()
        affected: set[str] = set()
        if metadata_changes:
            fields = [f"{column}=?" for column in metadata_changes]
            con.execute(
                f"UPDATE pages SET {', '.join(fields)} WHERE id=?",
                (*metadata_changes.values(), page_id),
            )
        if blocks_changed:
            _replace_blocks_in_connection(con, owner, page_id, prepared_blocks, now=now)
            affected = _propagate_synced_blocks(con, page_id, now=now)

        if metadata_changes or blocks_changed:
            _bump_page_revisions(con, {page_id, *affected}, now=now)
            _record_page_version(con, owner, page_id, now=now)
            for affected_page_id in affected - {page_id}:
                _record_page_version(con, owner, affected_page_id, now=now)

        result = _page_payload_in_connection(con, uid, owner, role, page_id)
        result["revision"] = int(result["page"]["revision"])
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        con.execute(
            """
            INSERT INTO page_save_operations(
              requester_user_id, operation_id, page_id, owner_user_id,
              request_hash, result_revision, result_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (uid, operation_id, page_id, owner, request_hash, result["revision"], result_json, now),
        )
        con.commit()
    return result


def list_page_versions(user_id: str | None, page_id: str, limit: int = 30) -> list[dict[str, Any]]:
    owner, _role, _page = _require_page(user_id, page_id, "view")
    safe_limit = max(1, min(100, int(limit or 30)))
    with _connection(owner) as con:
        rows = con.execute(
            """
            SELECT id, page_id, owner_user_id, title, icon, cover, settings_json, created_at,
                   length(blocks_json) AS snapshot_size
            FROM page_versions
            WHERE page_id=?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (page_id, safe_limit),
        ).fetchall()
    return [
        {
            **dict(row),
            "settings": _json_load(row["settings_json"], {}),
        }
        for row in rows
    ]


def get_page_version(user_id: str | None, page_id: str, version_id: str) -> dict[str, Any]:
    """Read one authorized historical page without changing the current page."""
    owner, _role, _page = _require_page(user_id, page_id, "view")
    version_id = str(version_id or "").strip()
    if not version_id:
        raise ValueError("version_id required")
    with _connection(owner) as con:
        row = con.execute(
            "SELECT id,title,icon,cover,settings_json,blocks_json,created_at "
            "FROM page_versions WHERE id=? AND page_id=?",
            (version_id, page_id),
        ).fetchone()
        latest = con.execute(
            "SELECT id FROM page_versions WHERE page_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (page_id,),
        ).fetchone()
    _require_page(user_id, page_id, "view")
    if row is None:
        raise FileNotFoundError("page version not found")
    blocks = _json_load(row["blocks_json"], None)
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        raise ValueError("page version snapshot is invalid")
    settings = _json_load(row["settings_json"], {})
    if not isinstance(settings, dict):
        raise ValueError("page version settings are invalid")
    return {
        "id": row["id"], "page_id": page_id, "title": row["title"],
        "icon": row["icon"], "cover": row["cover"], "settings": settings,
        "blocks": blocks, "created_at": row["created_at"],
        "current": bool(latest and latest["id"] == row["id"]),
    }


def list_page_updates(user_id: str | None, page_id: str, *, limit: int = 20,
                      before_version_id: str | None = None) -> dict[str, Any]:
    """Page a small, authorized activity feed with bounded snapshot previews."""
    owner, _role, _page = _require_page(user_id, page_id, "view")
    safe_limit = max(1, min(30, int(limit)))
    with _connection(owner) as con:
        boundary = ""
        params: list[Any] = [page_id]
        if before_version_id:
            cursor = con.execute(
                "SELECT rowid,created_at FROM page_versions WHERE id=? AND page_id=?",
                (before_version_id, page_id),
            ).fetchone()
            if cursor is None:
                raise FileNotFoundError("page version not found")
            boundary = "AND (created_at<? OR (created_at=? AND rowid<?)) "
            params.extend((cursor["created_at"], cursor["created_at"], cursor["rowid"]))
        params.append(safe_limit + 1)
        rows = con.execute(
            "SELECT id,title,icon,cover,settings_json,created_at,"
            "length(CAST(blocks_json AS BLOB)) AS snapshot_size,"
            "CASE WHEN length(CAST(blocks_json AS BLOB))<=2097152 THEN blocks_json END AS blocks_json "
            "FROM page_versions WHERE page_id=? " + boundary +
            "ORDER BY created_at DESC,rowid DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    # A share may be revoked while the snapshot query is in flight.
    _require_page(user_id, page_id, "view")

    def snapshot(row):
        if row is None or row["blocks_json"] is None:
            return None
        try:
            blocks = json.loads(str(row["blocks_json"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("page version snapshot is invalid") from exc
        if not isinstance(blocks, list):
            raise ValueError("page version snapshot is invalid")
        return {"title": row["title"], "icon": row["icon"],
                "cover": row["cover"], "settings": _json_load(row["settings_json"], {}),
                "blocks": blocks}

    visible = rows[:safe_limit]
    items = []
    for index, row in enumerate(visible):
        older = rows[index + 1] if index + 1 < len(rows) else None
        after = snapshot(row)
        before = snapshot(older) if older is not None else None
        items.append({
            "id": row["id"], "title": row["title"], "created_at": row["created_at"],
            "preview": page_change_preview(before, after) if after is not None
                and (older is None or before is not None) else None,
        })
    has_more = len(rows) > safe_limit
    return {"items": items, "has_more": has_more,
            "next_before_id": visible[-1]["id"] if has_more and visible else None}


def restore_page_version(
    user_id: str | None, page_id: str, version_id: str, *, expected_revision: int,
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    settings = _json_load(page.get("settings_json"), {})
    if _page_write_locked(settings, uid, page_id):
        raise PermissionError("page is locked")
    version_id = str(version_id or "").strip()
    if not version_id:
        raise ValueError("version_id required")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        _effective_edit_role(con, uid, owner, page_id)
        current_page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if not current_page_row:
            raise FileNotFoundError("page not found or permission denied")
        current_page = dict(current_page_row)
        current_revision = max(1, int(current_page.get("revision") or 1))
        if current_revision != expected_revision:
            raise PageRevisionConflict(current_revision)
        current_settings = _json_load(current_page.get("settings_json"), {})
        if _page_write_locked(current_settings, uid, page_id):
            raise PermissionError("page is locked")
        row = con.execute(
            "SELECT * FROM page_versions WHERE id=? AND page_id=?",
            (version_id, page_id),
        ).fetchone()
        if not row:
            raise FileNotFoundError("page version not found")
        version = dict(row)
        blocks = _json_load(version.get("blocks_json"), [])
        if not isinstance(blocks, list):
            raise ValueError("invalid page version")
        restored_settings = _json_load(version.get("settings_json"), {})
        if not isinstance(restored_settings, dict):
            restored_settings = {}
        if restored_settings.get("locked"):
            restored_settings["_np2b_lock_nonce"] = secrets.token_urlsafe(18)
        now = _now()
        con.execute(
            """
            UPDATE pages
            SET title=?, icon=?, cover=?, settings_json=?, updated_at=?
            WHERE id=?
            """,
            (
                version.get("title") or "Untitled",
                version.get("icon") or "",
                version.get("cover") or "",
                _json_dump(restored_settings),
                now,
                page_id,
            ),
        )
        _replace_blocks_in_connection(con, owner, page_id, blocks, now=now)
        affected = _propagate_synced_blocks(con, page_id, now=now)
        _bump_page_revisions(con, {page_id, *affected}, now=now)
        for affected_page_id in {page_id, *affected}:
            _record_page_version(con, owner, affected_page_id, now=now)
        con.commit()
    return get_page(uid, page_id)


def list_comments(user_id: str | None, page_id: str, *, include_resolved: bool = True) -> list[dict[str, Any]]:
    owner, _role, _page = _require_page(user_id, page_id, "view")
    where = "page_id=?" if include_resolved else "page_id=? AND resolved=0"
    with _connection(owner) as con:
        rows = con.execute(
            f"SELECT * FROM comments WHERE {where} ORDER BY created_at",
            (page_id,),
        ).fetchall()
    items = [_comment_from_row(row) for row in rows]
    roots = [item for item in items if not item.get("parent_id")]
    replies_by_parent: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        parent_id = str(item.get("parent_id") or "")
        if parent_id:
            replies_by_parent.setdefault(parent_id, []).append(item)
    roots.sort(
        key=lambda item: max(
            [int(item.get("updated_at") or 0), *[int(reply.get("updated_at") or 0) for reply in replies_by_parent.get(item["id"], [])]],
        ),
        reverse=True,
    )
    output: list[dict[str, Any]] = []
    for root in roots:
        replies = sorted(replies_by_parent.get(root["id"], []), key=lambda item: int(item.get("created_at") or 0))
        root["reply_count"] = len(replies)
        output.append(root)
        output.extend(replies)
    return output


def _comment_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["resolved"] = bool(item.get("resolved"))
    item["anchor"] = _json_load(item.pop("anchor_json", None), {})
    raw_reactions = _json_load(item.pop("reactions_json", None), {})
    item["reactions"] = {
        str(emoji)[:16]: sorted({safe_user_id(user_id) for user_id in users if safe_user_id(user_id)})
        for emoji, users in raw_reactions.items()
        if isinstance(users, list) and str(emoji).strip()
    } if isinstance(raw_reactions, dict) else {}
    item["parent_id"] = str(item.get("parent_id") or "")
    return item


def add_comment(
    user_id: str | None,
    page_id: str,
    *,
    content: str,
    block_id: str = "",
    anchor: dict[str, Any] | None = None,
    parent_id: str = "",
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, _role, _page = _require_page(uid, page_id, "comment")
    value = str(content or "").strip()
    if not value:
        raise ValueError("comment content required")
    now = _now()
    safe_anchor = anchor if isinstance(anchor, dict) else {}
    safe_anchor = {
        "start": max(0, int(safe_anchor.get("start") or 0)),
        "end": max(0, int(safe_anchor.get("end") or 0)),
        "text": str(safe_anchor.get("text") or "")[:1000],
        "context_before": str(safe_anchor.get("context_before") or "")[-48:],
        "context_after": str(safe_anchor.get("context_after") or "")[:48],
    } if safe_anchor else {}
    parent_id = str(parent_id or "").strip()
    with _connection(owner) as con:
        if parent_id:
            parent = con.execute("SELECT * FROM comments WHERE id=? AND page_id=?", (parent_id, page_id)).fetchone()
            if not parent:
                raise FileNotFoundError("parent comment not found")
            parent = dict(parent)
            parent_id = str(parent.get("parent_id") or parent.get("id") or "")
            block_id = str(parent.get("block_id") or block_id or "")
            parent_anchor = _json_load(parent.get("anchor_json"), {})
            if isinstance(parent_anchor, dict):
                safe_anchor = parent_anchor
    item = {
        "id": _comment_id(),
        "page_id": page_id,
        "block_id": str(block_id or "")[:80],
        "owner_user_id": owner,
        "author_user_id": uid,
        "content": value[:10000],
        "anchor": safe_anchor,
        "parent_id": parent_id,
        "reactions": {},
        "resolved": False,
        "created_at": now,
        "updated_at": now,
    }
    with _write_connection(owner) as con:
        con.execute(
            """
            INSERT INTO comments(id, page_id, block_id, owner_user_id, author_user_id, content, anchor_json, parent_id, reactions_json, resolved, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item["id"], page_id, item["block_id"] or None, owner, uid,
                item["content"], _json_dump(safe_anchor), parent_id or None, _json_dump({}), 0, now, now,
            ),
        )
        con.commit()
    return item


def update_comment(user_id: str | None, page_id: str, comment_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "comment")
    with _write_connection(owner) as con:
        row = con.execute("SELECT * FROM comments WHERE id=? AND page_id=?", (comment_id, page_id)).fetchone()
        if not row:
            raise FileNotFoundError("comment not found")
        comment = dict(row)
        if uid != comment.get("author_user_id") and role != "owner":
            raise PermissionError("comment author or page owner required")
        fields: list[str] = []
        values: list[Any] = []
        if "content" in updates:
            content = str(updates.get("content") or "").strip()
            if not content:
                raise ValueError("comment content required")
            fields.append("content=?")
            values.append(content[:10000])
        if "resolved" in updates:
            if comment.get("parent_id"):
                raise ValueError("reply comments cannot resolve a thread")
            fields.append("resolved=?")
            values.append(1 if updates.get("resolved") else 0)
        if fields:
            fields.append("updated_at=?")
            values.append(_now())
            values.extend((comment_id, page_id))
            con.execute(f"UPDATE comments SET {', '.join(fields)} WHERE id=? AND page_id=?", tuple(values))
            con.commit()
        updated = con.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    return _comment_from_row(updated)


def delete_comment(user_id: str | None, page_id: str, comment_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "comment")
    with _write_connection(owner) as con:
        row = con.execute("SELECT author_user_id FROM comments WHERE id=? AND page_id=?", (comment_id, page_id)).fetchone()
        if not row:
            raise FileNotFoundError("comment not found")
        if uid != row["author_user_id"] and role != "owner":
            raise PermissionError("comment author or page owner required")
        con.execute("DELETE FROM comments WHERE page_id=? AND (id=? OR parent_id=?)", (page_id, comment_id, comment_id))
        con.commit()
    return {"id": comment_id, "deleted": True}


def toggle_comment_reaction(
    user_id: str | None,
    page_id: str,
    comment_id: str,
    emoji: str,
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, _role, _page = _require_page(uid, page_id, "comment")
    value = str(emoji or "").strip()[:16]
    if not value or any(char in value for char in "<>\r\n"):
        raise ValueError("valid reaction emoji required")
    with _write_connection(owner) as con:
        row = con.execute("SELECT * FROM comments WHERE id=? AND page_id=?", (comment_id, page_id)).fetchone()
        if not row:
            raise FileNotFoundError("comment not found")
        reactions = _json_load(dict(row).get("reactions_json"), {})
        if not isinstance(reactions, dict):
            reactions = {}
        users = {safe_user_id(item) for item in reactions.get(value, []) if safe_user_id(item)}
        if uid in users:
            users.remove(uid)
        else:
            users.add(uid)
        if users:
            reactions[value] = sorted(users)
        else:
            reactions.pop(value, None)
        con.execute(
            "UPDATE comments SET reactions_json=?, updated_at=? WHERE id=? AND page_id=?",
            (_json_dump(reactions), _now(), comment_id, page_id),
        )
        con.commit()
        updated = con.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    return _comment_from_row(updated)


def _page_file_dir(owner: str, page_id: str, *, create: bool = False) -> str:
    path = os.path.join(_files_root(owner, create=create), page_id)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def upload_file(
    user_id: str | None,
    page_id: str,
    *,
    filename: str,
    raw: bytes,
    mime_type: str = "",
    insert_block: bool = True,
    block_type: str = "",
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, page = _require_page(uid, page_id, "edit")
    if role not in EDIT_ROLES:
        raise PermissionError("edit permission required")
    settings = _json_load(page.get("settings_json"), {})
    if _page_write_locked(settings, uid, page_id):
        raise PermissionError("page is locked")
    safe_name = _safe_filename(filename)
    mime_type = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    fid = _file_id()
    stored_name = f"{fid}-{safe_name}"
    file_dir = _page_file_dir(owner, page_id, create=True)
    path = os.path.join(file_dir, stored_name)
    with open(path, "wb") as f:
        f.write(raw or b"")
    rel_path = os.path.relpath(path, _workspace_root(owner)).replace("\\", "/")
    now = _now()
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            _effective_edit_role(con, uid, owner, page_id)
            current_page = con.execute(
                "SELECT settings_json FROM pages WHERE id=? AND archived=0", (page_id,)
            ).fetchone()
            current_settings = _json_load(current_page["settings_json"], {}) if current_page else {}
            if current_page is None or _page_write_locked(current_settings, uid, page_id):
                raise PermissionError("page is locked or unavailable")
        except Exception:
            os.unlink(path)
            raise
        con.execute(
            """
            INSERT INTO files(id, page_id, owner_user_id, uploader_user_id, original_name, stored_name, rel_path, mime_type, size, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (fid, page_id, owner, uid, safe_name, stored_name, rel_path, mime_type, len(raw or b""), now),
        )
        if insert_block:
            pos = con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM blocks WHERE page_id=?",
                (page_id,),
            ).fetchone()["pos"]
            upload_types = {"image", "file", "video", "audio", "pdf"}
            if block_type in upload_types:
                btype = block_type
            elif mime_type.startswith(IMAGE_MIME_PREFIX):
                btype = "image"
            elif mime_type.startswith("video/"):
                btype = "video"
            elif mime_type.startswith("audio/"):
                btype = "audio"
            elif mime_type == "application/pdf":
                btype = "pdf"
            else:
                btype = "file"
            con.execute(
                """
                INSERT INTO blocks(id, page_id, owner_user_id, position, block_type, content, props_json, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    _block_id(),
                    page_id,
                    owner,
                    int(pos),
                    btype,
                    safe_name,
                    _json_dump({"file_id": fid, "name": safe_name, "mime_type": mime_type}),
                    now,
                    now,
                ),
            )
        con.execute("UPDATE pages SET updated_at=? WHERE id=?", (now, page_id))
        _bump_page_revisions(con, {page_id}, now=now, update_timestamp=False)
        if insert_block:
            _record_page_version(con, owner, page_id, now=now)
        con.commit()
    return get_file(user_id, fid)


def _file_row(owner: str, file_id: str) -> dict[str, Any] | None:
    try:
        with _connection(owner) as con:
            row = con.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            return _row(row)
    except Exception:
        return None


def get_file(user_id: str | None, file_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    file_id = str(file_id or "").strip()
    for owner in [uid] + [u for u in _known_workspace_users() if u != uid]:
        row = _file_row(owner, file_id)
        if not row:
            continue
        if resolve_page(uid, row["page_id"], minimum_role="view"):
            item = _file_from_row(row, owner)
            item["path"] = os.path.join(_workspace_root(owner), item["rel_path"])
            return item
    raise FileNotFoundError("file not found or permission denied")


def list_shares(user_id: str | None, page_id: str) -> list[dict[str, Any]]:
    owner, role, _page = _require_page(user_id, page_id, "view")
    if owner != _uid(user_id) and role != "owner":
        return []
    with _connection(owner) as con:
        rows = con.execute(
            """
            SELECT * FROM page_shares
            WHERE page_id=? AND revoked_at IS NULL
            ORDER BY updated_at DESC
            """,
            (page_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def share_page(user_id: str | None, page_id: str, shared_user_id: str, role: str = "view") -> dict[str, Any]:
    uid = _uid(user_id)
    owner, page_role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or page_role != "owner":
        raise PermissionError("owner permission required")
    target = safe_user_id(shared_user_id)
    if not target:
        raise ValueError("valid target user required")
    if target == uid:
        raise ValueError("cannot share a page with its owner")
    if role not in {"view", "comment", "edit"}:
        raise ValueError("role must be one of: comment, edit, view")
    now = _now()
    sid = _share_id(page_id, target)
    with _write_connection(uid) as con:
        con.execute(
            """
            INSERT INTO page_shares(id, page_id, owner_user_id, shared_user_id, role, created_at, updated_at, revoked_at)
            VALUES(?,?,?,?,?,?,?,NULL)
            ON CONFLICT(page_id, shared_user_id) DO UPDATE SET
              role=excluded.role,
              updated_at=excluded.updated_at,
              revoked_at=NULL
            """,
            (sid, page_id, uid, target, role, now, now),
        )
        con.commit()
    return {"id": sid, "page_id": page_id, "shared_user_id": target, "role": role}


def revoke_share(user_id: str | None, page_id: str, shared_user_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    target = safe_user_id(shared_user_id)
    if not target:
        raise ValueError("valid target user required")
    with _write_connection(uid) as con:
        con.execute(
            "UPDATE page_shares SET revoked_at=?, updated_at=? WHERE page_id=? AND shared_user_id=?",
            (_now(), _now(), page_id, target),
        )
        con.commit()
    return {"page_id": page_id, "shared_user_id": target, "revoked": True}


def _public_link_payload(row: dict[str, Any] | None, page_id: str) -> dict[str, Any]:
    if not row or row.get("revoked_at"):
        return {"enabled": False, "page_id": page_id, "token": "", "allow_duplicate": True}
    item = dict(row)
    item["enabled"] = True
    item["allow_duplicate"] = bool(item.get("allow_duplicate", 1))
    return item


def get_public_link(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    with _connection(uid) as con:
        row = con.execute(
            "SELECT * FROM public_links WHERE page_id=?",
            (page_id,),
        ).fetchone()
    return _public_link_payload(_row(row), page_id)


def enable_public_link(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    now = _now()
    with _write_connection(uid) as con:
        row = con.execute(
            "SELECT * FROM public_links WHERE page_id=? AND revoked_at IS NULL",
            (page_id,),
        ).fetchone()
        if row:
            return _public_link_payload(_row(row), page_id)
        token = _public_token()
        con.execute(
            """
            INSERT INTO public_links(page_id, owner_user_id, token, created_at, updated_at, revoked_at)
            VALUES(?,?,?,?,?,NULL)
            ON CONFLICT(page_id) DO UPDATE SET
              token=excluded.token,
              updated_at=excluded.updated_at,
              revoked_at=NULL
            """,
            (page_id, uid, token, now, now),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM public_links WHERE page_id=?",
            (page_id,),
        ).fetchone()
    return _public_link_payload(_row(row), page_id)


def update_public_link_options(
    user_id: str | None,
    page_id: str,
    *,
    allow_duplicate: bool,
) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    now = _now()
    with _write_connection(uid) as con:
        row = con.execute(
            "SELECT * FROM public_links WHERE page_id=? AND revoked_at IS NULL",
            (page_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("public link not found")
        con.execute(
            "UPDATE public_links SET allow_duplicate=?, updated_at=? WHERE page_id=?",
            (1 if allow_duplicate else 0, now, page_id),
        )
        con.commit()
        row = con.execute("SELECT * FROM public_links WHERE page_id=?", (page_id,)).fetchone()
    return _public_link_payload(_row(row), page_id)


def disable_public_link(user_id: str | None, page_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, role, _page = _require_page(uid, page_id, "owner")
    if owner != uid or role != "owner":
        raise PermissionError("owner permission required")
    now = _now()
    with _write_connection(uid) as con:
        con.execute(
            "UPDATE public_links SET revoked_at=?, updated_at=? WHERE page_id=?",
            (now, now, page_id),
        )
        con.commit()
    return {"enabled": False, "page_id": page_id, "revoked": True}


def resolve_public_page(token: str) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        raise FileNotFoundError("public page not found")
    for owner in _known_workspace_users():
        try:
            with _connection(owner) as con:
                row = con.execute(
                    """
                    SELECT * FROM public_links
                    WHERE token=? AND revoked_at IS NULL
                    """,
                    (token,),
                ).fetchone()
                if not row:
                    continue
                link = dict(row)
                root_page_id = str(link["page_id"])
                public_projection = {
                    root_page_id,
                    *_page_descendants(con, root_page_id),
                }
                data = _page_payload_in_connection(
                    con,
                    owner,
                    owner,
                    "owner",
                    root_page_id,
                    reference_page_ids=public_projection,
                )
        except Exception:
            continue
        data["public_link"] = link
        data["page"]["public_read_only"] = True
        data["page"]["can_edit"] = False
        data["page"]["can_share"] = False
        return data
    raise FileNotFoundError("public page not found")


def duplicate_public_page(user_id: str | None, token: str) -> dict[str, Any]:
    """Copy a published page tree into the authenticated user's private root."""
    target = _uid(user_id)
    public_data = resolve_public_page(token)
    public_link = public_data.get("public_link") or {}
    if not bool(public_link.get("allow_duplicate", 1)):
        raise PermissionError("public page duplication is disabled")
    source_owner = safe_user_id((public_data.get("page") or {}).get("owner_user_id"))
    root_page_id = str((public_data.get("page") or {}).get("id") or "")
    if not source_owner or not root_page_id:
        raise FileNotFoundError("public page not found")

    ordered_pages: list[dict[str, Any]] = []
    blocks_by_page: dict[str, list[dict[str, Any]]] = {}
    files_by_page: dict[str, list[dict[str, Any]]] = {}
    source_known_page_ids: set[str] = set()
    with _connection(source_owner) as source_con:
        pending = [root_page_id]
        visited: set[str] = set()
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                continue
            visited.add(current_id)
            row = source_con.execute(
                "SELECT * FROM pages WHERE id=? AND archived=0",
                (current_id,),
            ).fetchone()
            if not row:
                continue
            ordered_pages.append(dict(row))
            if len(ordered_pages) > 1000:
                raise ValueError("public page tree exceeds 1000 pages")
            children = source_con.execute(
                "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position, updated_at DESC",
                (current_id,),
            ).fetchall()
            pending.extend(reversed([str(child["id"]) for child in children]))

        if not ordered_pages or str(ordered_pages[0]["id"]) != root_page_id:
            raise FileNotFoundError("public page not found")
        public_projection = {str(page["id"]) for page in ordered_pages}
        source_known_page_ids = {
            str(row["id"])
            for row in source_con.execute("SELECT id FROM pages").fetchall()
        }
        total_blocks = 0
        for page in ordered_pages:
            source_page_id = str(page["id"])
            block_rows = source_con.execute(
                "SELECT * FROM blocks WHERE page_id=? ORDER BY position",
                (source_page_id,),
            ).fetchall()
            blocks = [_block_from_row(dict(row)) for row in block_rows]
            _hydrate_page_references(
                source_con,
                blocks,
                requester_user_id=source_owner,
                owner=source_owner,
                reference_page_ids=public_projection,
            )
            total_blocks += len(blocks)
            if total_blocks > 50000:
                raise ValueError("public page tree exceeds 50000 blocks")
            blocks_by_page[source_page_id] = blocks
            files_by_page[source_page_id] = [
                dict(row)
                for row in source_con.execute(
                    "SELECT * FROM files WHERE page_id=? ORDER BY created_at",
                    (source_page_id,),
                ).fetchall()
            ]

    now = _now()
    page_map = {str(page["id"]): _page_id() for page in ordered_pages}
    block_map = {
        str(block["id"]): _block_id()
        for blocks in blocks_by_page.values()
        for block in blocks
    }
    file_map: dict[str, str] = {}
    copied_paths: list[str] = []
    source_workspace = os.path.abspath(_workspace_root(source_owner))

    redacted_reference = object()

    def remap_refs(value: Any) -> Any:
        if isinstance(value, list):
            remapped = [remap_refs(item) for item in value]
            return [item for item in remapped if item is not redacted_reference]
        if isinstance(value, dict):
            remapped: dict[str, Any] = {}
            for key, item in value.items():
                mapped = remap_refs(item)
                if mapped is not redacted_reference:
                    remapped[key] = mapped
            return remapped
        if isinstance(value, str):
            if value in page_map:
                return page_map[value]
            if value in source_known_page_ids:
                return redacted_reference
            if value in file_map:
                return file_map[value]
            if value in block_map:
                return block_map[value]
        return value

    try:
        with _write_connection(target, bootstrap_if_missing=True) as target_con:
            position_row = target_con.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id IS NULL"
            ).fetchone()
            root_position = int(position_row["pos"] if position_row else 1)
            for source_page in ordered_pages:
                source_page_id = str(source_page["id"])
                source_parent_id = str(source_page.get("parent_id") or "")
                settings = _json_load(source_page.get("settings_json"), {})
                if not isinstance(settings, dict):
                    settings = {}
                settings["locked"] = False
                target_con.execute(
                    """
                    INSERT INTO pages(id, owner_user_id, parent_id, title, icon, cover, settings_json, position, archived, archived_at, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        page_map[source_page_id],
                        target,
                        page_map.get(source_parent_id) or None,
                        str(source_page.get("title") or "Untitled")[:160],
                        str(source_page.get("icon") or "")[:64],
                        str(source_page.get("cover") or "")[:1000],
                        _json_dump(settings),
                        root_position if source_page_id == root_page_id else int(source_page.get("position") or 0),
                        0,
                        None,
                        now,
                        now,
                    ),
                )

            for source_page in ordered_pages:
                source_page_id = str(source_page["id"])
                target_page_id = page_map[source_page_id]
                for file_row in files_by_page.get(source_page_id, []):
                    source_path = os.path.abspath(
                        os.path.join(source_workspace, str(file_row.get("rel_path") or ""))
                    )
                    try:
                        if os.path.commonpath([source_workspace, source_path]) != source_workspace:
                            continue
                    except ValueError:
                        continue
                    if not os.path.isfile(source_path):
                        continue
                    old_file_id = str(file_row["id"])
                    new_file_id = _file_id()
                    file_map[old_file_id] = new_file_id
                    safe_name = _safe_filename(file_row.get("original_name") or file_row.get("stored_name") or "file")
                    stored_name = f"{new_file_id}-{safe_name}"
                    destination = os.path.join(_page_file_dir(target, target_page_id, create=True), stored_name)
                    shutil.copy2(source_path, destination)
                    copied_paths.append(destination)
                    rel_path = os.path.relpath(destination, _workspace_root(target)).replace("\\", "/")
                    target_con.execute(
                        """
                        INSERT INTO files(id, page_id, owner_user_id, uploader_user_id, original_name, stored_name, rel_path, mime_type, size, created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            new_file_id,
                            target_page_id,
                            target,
                            target,
                            safe_name,
                            stored_name,
                            rel_path,
                            file_row.get("mime_type") or "application/octet-stream",
                            os.path.getsize(destination),
                            now,
                        ),
                    )

            for source_page in ordered_pages:
                source_page_id = str(source_page["id"])
                target_page_id = page_map[source_page_id]
                copied_blocks: list[dict[str, Any]] = []
                for source_block in blocks_by_page.get(source_page_id, []):
                    block = json.loads(json.dumps(source_block, ensure_ascii=False))
                    block["id"] = block_map[str(block["id"])]
                    block.pop("created_at", None)
                    block.pop("updated_at", None)
                    block["props"] = remap_refs(block.get("props") or {})
                    copied_blocks.append(block)
                _replace_blocks_in_connection(target_con, target, target_page_id, copied_blocks, now=now)
                _record_page_version(target_con, target, target_page_id, now=now)
            target_con.commit()
    except Exception:
        for path in copied_paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        raise
    return get_page(target, page_map[root_page_id])


def _clean_form_value(property_item: dict[str, Any], raw: Any) -> Any:
    property_type = str(property_item.get("type") or "text")
    if property_type == "checkbox":
        return raw is True or str(raw or "").strip().lower() in {"1", "true", "yes", "on", "checked"}
    value = str(raw or "").strip()
    if not value:
        return ""
    if property_type == "number":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{property_item.get('name') or 'number'} must be a number") from exc
        if not (-1e100 < number < 1e100):
            raise ValueError(f"{property_item.get('name') or 'number'} is out of range")
        return int(number) if number.is_integer() else number
    if property_type == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{property_item.get('name') or 'date'} must use YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{property_item.get('name') or 'date'} is invalid") from exc
        return value
    if property_type in {"select", "status"}:
        defaults = ["시작 전", "진행 중", "완료"] if property_type == "status" else []
        options = {str(item) for item in [*(property_item.get("options") or []), *defaults] if str(item).strip()}
        if options and value not in options:
            raise ValueError(f"{property_item.get('name') or 'option'} has an invalid option")
    if property_type == "multi_select":
        value = ", ".join(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if property_type == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError(f"{property_item.get('name') or 'email'} is invalid")
    if property_type == "url" and not re.match(r"^https?://", value, flags=re.I):
        raise ValueError(f"{property_item.get('name') or 'URL'} must start with http:// or https://")
    return value[:5000]


def _submit_form_to_owner(
    owner: str,
    page_id: str,
    block_id: str,
    values: dict[str, Any],
    *,
    submitter_user_id: str,
    public_submission: bool = False,
) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("form values must be an object")
    block_id = str(block_id or "").strip()
    if not block_id:
        raise ValueError("form block required")
    now = _now()
    with _write_connection(owner) as con:
        con.execute("BEGIN IMMEDIATE")
        page_row = con.execute("SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)).fetchone()
        if not page_row:
            raise FileNotFoundError("form page not found")
        block_row = con.execute("SELECT * FROM blocks WHERE id=? AND page_id=?", (block_id, page_id)).fetchone()
        if not block_row:
            raise FileNotFoundError("form not found")
        stored_block = dict(block_row)
        props = _json_load(stored_block.get("props_json"), {})
        if not isinstance(props, dict):
            props = {}
        if stored_block.get("block_type") != "form" and str(props.get("view") or "") != "form":
            raise ValueError("the selected database view is not a form")
        allowed_types = FORM_WRITABLE_PROPERTY_TYPES - ({"person", "relation"} if public_submission else set())
        properties = [
            item for item in (props.get("properties") if isinstance(props.get("properties"), list) else [])
            if isinstance(item, dict) and item.get("id") and item.get("type") in allowed_types
        ]
        if not properties:
            raise ValueError("form has no writable properties")
        visibility = props.get("property_visibility") if isinstance(props.get("property_visibility"), dict) else {}
        fields = [
            item for item in properties
            if item.get("type") == "title" or visibility.get(str(item.get("id")), not item.get("hidden")) is not False
        ]
        required = {str(item) for item in (props.get("form_required_properties") or []) if str(item)}
        cleaned: dict[str, Any] = {}
        for property_item in fields:
            property_id = str(property_item["id"])
            raw = values.get(property_id, False if property_item.get("type") == "checkbox" else "")
            value = _clean_form_value(property_item, raw)
            if property_id in required and (value is False or value == ""):
                raise ValueError(f"{property_item.get('name') or property_id} is required")
            cleaned[property_id] = value
        title_field = next((str(item["id"]) for item in properties if item.get("type") == "title"), "title")
        title = " ".join(str(cleaned.get(title_field) or "폼 응답").split())[:160] or "폼 응답"
        child_id = _page_id()
        position_row = con.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM pages WHERE archived=0 AND parent_id=?",
            (page_id,),
        ).fetchone()
        con.execute(
            """
            INSERT INTO pages(id, owner_user_id, parent_id, title, icon, cover, settings_json, position, archived, archived_at, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (child_id, owner, page_id, title, "", "", "{}", int(position_row["pos"] if position_row else 1), 0, None, now, now),
        )
        _replace_blocks_in_connection(con, owner, child_id, [{"type": "paragraph", "content": "", "props": {}}], now=now)
        row = {
            **cleaned,
            title_field: title,
            "page_id": child_id,
            "_created_time": datetime.now().isoformat(timespec="seconds"),
            "_last_edited_time": datetime.now().isoformat(timespec="seconds"),
            "_created_by": submitter_user_id,
            "_last_edited_by": submitter_user_id,
        }
        rows = props.get("rows") if isinstance(props.get("rows"), list) else []
        if len(rows) >= 10000:
            raise ValueError("form response limit reached")
        props["rows"] = [*rows, row]
        if len(_json_dump(props).encode("utf-8")) > 2_000_000:
            raise ValueError("database properties exceed 2MB")
        con.execute(
            "UPDATE blocks SET props_json=?, updated_at=? WHERE id=? AND page_id=?",
            (_json_dump(props), now, block_id, page_id),
        )
        con.execute("UPDATE pages SET updated_at=? WHERE id=?", (now, page_id))
        _bump_page_revisions(con, {page_id}, now=now, update_timestamp=False)
        _record_page_version(con, owner, child_id, now=now)
        _record_page_version(con, owner, page_id, now=now)
        con.commit()
    return {
        "status": "submitted",
        "page_id": page_id,
        "database_page_id": child_id,
        "row": row,
        "message": str(props.get("form_success_message") or "응답이 제출되었습니다.")[:200],
    }


def submit_form(user_id: str | None, page_id: str, block_id: str, values: dict[str, Any]) -> dict[str, Any]:
    uid = _uid(user_id)
    owner, _role, _page = _require_page(uid, page_id, "view")
    return _submit_form_to_owner(owner, page_id, block_id, values, submitter_user_id=uid)


def submit_public_form(token: str, block_id: str, values: dict[str, Any]) -> dict[str, Any]:
    data = resolve_public_page(token)
    page = data.get("page") or {}
    owner = str(page.get("owner_user_id") or "")
    page_id = str(page.get("id") or "")
    if not owner or not page_id:
        raise FileNotFoundError("public form not found")
    public_submitter = "public_" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]
    return _submit_form_to_owner(
        owner,
        page_id,
        block_id,
        values,
        submitter_user_id=public_submitter,
        public_submission=True,
    )


def get_public_file(token: str, file_id: str) -> dict[str, Any]:
    data = resolve_public_page(token)
    page = data.get("page") or {}
    page_id = page.get("id") or ""
    file_id = str(file_id or "").strip()
    if not file_id:
        raise FileNotFoundError("file not found")
    for item in data.get("files") or []:
        if item.get("id") != file_id or item.get("page_id") != page_id:
            continue
        path = os.path.join(_workspace_root(item["owner_user_id"]), item["rel_path"])
        if not os.path.isfile(path):
            break
        item = dict(item)
        item["path"] = path
        return item
    raise FileNotFoundError("file not found")


def blocks_to_markdown(page: dict[str, Any], blocks: list[dict[str, Any]], files_by_id: dict[str, dict[str, Any]] | None = None) -> str:
    files_by_id = files_by_id or {}
    parts = [f"# {page.get('title') or 'Untitled'}"]
    for block in blocks:
        btype = block.get("type") or "paragraph"
        content = str(block.get("content") or "").rstrip()
        props = block.get("props") if isinstance(block.get("props"), dict) else {}
        if props.get("tab_group"):
            payload = json.dumps({"type": btype, "content": content, "props": props}, ensure_ascii=False).replace("<", "\\u003c")
            parts.append(f"<notion_tab_member>{payload}</notion_tab_member>")
            continue
        if btype == "heading1":
            parts.append(f"# {content}")
        elif btype == "heading2":
            parts.append(f"## {content}")
        elif btype == "heading3":
            parts.append(f"### {content}")
        elif btype == "heading4":
            parts.append(f"#### {content}")
        elif btype in {"toggle_heading1", "toggle_heading2", "toggle_heading3", "toggle_heading4"}:
            level = btype[-1]
            opened = " open" if props.get("open", True) else ""
            parts.append(f'<details data-heading="{level}"{opened}><summary>{content}</summary></details>')
        elif btype == "todo":
            checked = "x" if props.get("checked") else " "
            indent = "  " * max(0, min(6, int(props.get("indent") or 0)))
            parts.append(f"{indent}- [{checked}] {content}")
        elif btype == "toggle":
            parts.append(f"<details{' open' if props.get('open', True) else ''}><summary>{content}</summary></details>")
        elif btype == "callout":
            parts.append(f"<callout icon=\"{props.get('icon') or '💡'}\">{content}</callout>")
        elif btype == "quote":
            lines = content.splitlines() or [""]
            parts.append("\n".join("> " + line for line in lines))
        elif btype == "code":
            language = str(props.get("language") or "").strip()
            parts.append(f"```{language}\n{content}\n```")
        elif btype == "equation":
            parts.append(f"$$\n{content}\n$$")
        elif btype == "bullet":
            indent = "  " * max(0, min(6, int(props.get("indent") or 0)))
            parts.append(f"{indent}- {content}")
        elif btype == "numbered":
            indent = "  " * max(0, min(6, int(props.get("indent") or 0)))
            parts.append(f"{indent}1. {content}")
        elif btype == "divider":
            parts.append("---")
        elif btype in {"image", "file", "video", "audio", "pdf"}:
            file_id = str(props.get("file_id") or "").strip()
            file_item = files_by_id.get(file_id) if file_id else None
            label = content or (file_item or {}).get("original_name") or "attachment"
            ref = f"notion-files/{(file_item or {}).get('stored_name') or file_id or label}"
            if btype == "image":
                parts.append(f"![{label}]({ref})")
            elif btype in {"video", "audio", "pdf"}:
                parts.append(f"<{btype} src=\"{ref}\">{label}</{btype}>")
            else:
                parts.append(f"[{label}]({ref})")
        elif btype == "bookmark":
            url = str(props.get("url") or props.get("link_url") or "").strip()
            parts.append(f"[{content or url}]({url})" if url else content)
        elif btype == "embed":
            url = str(props.get("url") or "").strip()
            parts.append(f"<embed src=\"{url}\">{content}</embed>" if url else content)
        elif btype == "table_of_contents":
            parts.append("<table_of_contents/>")
        elif btype == "breadcrumb":
            parts.append("<breadcrumb/>")
        elif btype == "link_to_page":
            linked_id = str(props.get("page_id") or "").strip()
            label = content or str(props.get("title") or "").strip() or "Linked page"
            parts.append(f'<link_to_page id="{linked_id}">{label}</link_to_page>')
        elif btype == "button":
            payload = json.dumps(props, ensure_ascii=False).replace("<", "\\u003c")
            parts.append(f"<notion_button>{payload}</notion_button>")
        elif btype == "simple_table":
            payload = json.dumps(props, ensure_ascii=False).replace("<", "\\u003c")
            parts.append(f"<notion_simple_table>{payload}</notion_simple_table>")
        elif btype == "synced_block":
            sync_id = str(props.get("sync_id") or "")
            parts.append(f'<synced_block id="{sync_id}">{content}</synced_block>')
        elif btype == "tabs":
            payload = json.dumps({"source_id": str(block.get("id") or ""), "props": props}, ensure_ascii=False).replace("<", "\\u003c")
            parts.append(f"<notion_tabs>{payload}</notion_tabs>")
        elif btype == "child_page":
            child_id = str(props.get("page_id") or "").strip()
            label = content or str(props.get("title") or "").strip() or "Untitled child page"
            parts.append(f"- Child page: {label} ({child_id})")
        elif btype in {"table", "board", "list", "gallery", "timeline", "chart", "dashboard", "form", "feed", "map"}:
            rows = props.get("rows") if isinstance(props.get("rows"), list) else []
            properties = props.get("properties") if isinstance(props.get("properties"), list) else []
            title_field = next(
                (str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "title" and item.get("id")),
                "title",
            )
            date_field = next(
                (str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "date" and item.get("id")),
                "date",
            )
            tags_field = next(
                (str(item.get("id")) for item in properties if isinstance(item, dict) and item.get("type") == "multi_select" and item.get("id")),
                "tags",
            )
            parts.append(content or btype.title())
            if rows:
                parts.append("| Name | Date | Tags |")
                parts.append("| --- | --- | --- |")
                for row in rows[:100]:
                    if not isinstance(row, dict):
                        continue
                    parts.append(
                        f"| {row.get(title_field) or row.get('title') or 'Untitled'} | {row.get(date_field) or ''} | {row.get(tags_field) or ''} |"
                    )
        elif btype == "calendar":
            rows = props.get("rows") if isinstance(props.get("rows"), list) else (props.get("events") if isinstance(props.get("events"), list) else [])
            parts.append(content or "Calendar")
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                parts.append(f"- {row.get('date') or ''}: {row.get('title') or 'Untitled'}")
        elif content:
            parts.append(content)
    return "\n\n".join(part for part in parts if str(part).strip()).rstrip() + "\n"


def export_page_markdown(user_id: str | None, page_id: str, *, include_assets: bool = False) -> dict[str, Any]:
    data = get_page(user_id, page_id)
    page = data["page"]
    files = data.get("files") or []
    files_by_id = {item["id"]: item for item in files}
    markdown = blocks_to_markdown(page, data.get("blocks") or [], files_by_id)
    header = [
        "---",
        "source_type: notion_workspace",
        f"page_id: {page['id']}",
        f"owner_user_id: {page['owner_user_id']}",
        f"updated_at: {page['updated_at']}",
        "---",
        "",
    ]
    images: list[dict[str, Any]] = []
    if include_assets:
        for block in data.get("blocks") or []:
            if block.get("type") != "image":
                continue
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            file_id = str(props.get("file_id") or "").strip()
            file_item = files_by_id.get(file_id)
            if not file_item:
                continue
            path = os.path.join(_workspace_root(file_item["owner_user_id"]), file_item["rel_path"])
            if os.path.isfile(path):
                images.append({
                    "path": path,
                    "filename": file_item["original_name"],
                    "label": block.get("content") or file_item["original_name"],
                    "kind": "notion-image",
                })
    return {
        "page": page,
        "markdown": "\n".join(header) + markdown,
        "images": images,
        "files": files,
    }


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    text = str(markdown or "").replace("\r\n", "\n").strip()
    if not text:
        return [{"type": "paragraph", "content": "", "props": {}}]
    blocks: list[dict[str, Any]] = []
    imported_tab_groups: dict[str, str] = {}
    lines = text.splitlines()
    idx = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append({"type": "paragraph", "content": "\n".join(paragraph).strip(), "props": {}})
            paragraph = []

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            idx += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            language = stripped[3:].strip()
            idx += 1
            code_lines: list[str] = []
            while idx < len(lines) and not lines[idx].strip().startswith("```"):
                code_lines.append(lines[idx])
                idx += 1
            idx += 1 if idx < len(lines) else 0
            blocks.append({"type": "code", "content": "\n".join(code_lines), "props": {"language": language}})
            continue
        if stripped == "$$":
            flush_paragraph()
            idx += 1
            equation_lines: list[str] = []
            while idx < len(lines) and lines[idx].strip() != "$$":
                equation_lines.append(lines[idx])
                idx += 1
            idx += 1 if idx < len(lines) else 0
            blocks.append({"type": "equation", "content": "\n".join(equation_lines).strip(), "props": {}})
            continue
        if stripped == "---":
            flush_paragraph()
            blocks.append({"type": "divider", "content": "", "props": {}})
        elif stripped.startswith("#### "):
            flush_paragraph()
            blocks.append({"type": "heading4", "content": stripped[5:].strip(), "props": {}})
        elif stripped.startswith("### "):
            flush_paragraph()
            blocks.append({"type": "heading3", "content": stripped[4:].strip(), "props": {}})
        elif stripped.startswith("## "):
            flush_paragraph()
            blocks.append({"type": "heading2", "content": stripped[3:].strip(), "props": {}})
        elif stripped.startswith("# "):
            flush_paragraph()
            blocks.append({"type": "heading1", "content": stripped[2:].strip(), "props": {}})
        elif re.match(r'^<details\s+data-heading=["\'][1234]["\'](?:\s+open)?><summary>.*</summary></details>$', stripped, flags=re.I):
            flush_paragraph()
            level_match = re.search(r'data-heading=["\']([1234])["\']', stripped, flags=re.I)
            content = re.sub(r'^<details[^>]*><summary>|</summary></details>$', "", stripped, flags=re.I)
            level = (level_match.group(1) if level_match else "1")
            blocks.append({"type": f"toggle_heading{level}", "content": content, "props": {"open": " open" in stripped.lower()}})
        elif re.match(r"^<details(?:\s+open)?><summary>.*</summary></details>$", stripped, flags=re.I):
            flush_paragraph()
            content = re.sub(r"^<details(?:\s+open)?><summary>|</summary></details>$", "", stripped, flags=re.I)
            blocks.append({"type": "toggle", "content": content, "props": {"open": " open" in stripped.lower()}})
        elif stripped.lower().startswith("<callout") and stripped.lower().endswith("</callout>"):
            flush_paragraph()
            icon_match = re.search(r'icon=["\']([^"\']+)["\']', stripped, flags=re.I)
            content = re.sub(r"^<callout[^>]*>|</callout>$", "", stripped, flags=re.I)
            blocks.append({"type": "callout", "content": content, "props": {"icon": icon_match.group(1) if icon_match else "💡"}})
        elif stripped.lower() == "<table_of_contents/>":
            flush_paragraph()
            blocks.append({"type": "table_of_contents", "content": "", "props": {}})
        elif stripped.lower() == "<breadcrumb/>":
            flush_paragraph()
            blocks.append({"type": "breadcrumb", "content": "", "props": {}})
        elif re.match(r"^<link_to_page\s+id=[\"'][^\"']*[\"']>.*</link_to_page>$", stripped, flags=re.I):
            flush_paragraph()
            page_match = re.search(r'id=["\']([^"\']*)["\']', stripped, flags=re.I)
            content = re.sub(r"^<link_to_page[^>]*>|</link_to_page>$", "", stripped, flags=re.I)
            blocks.append({"type": "link_to_page", "content": content, "props": {"page_id": page_match.group(1) if page_match else ""}})
        elif stripped.lower().startswith("<notion_button>") and stripped.lower().endswith("</notion_button>"):
            flush_paragraph()
            payload = re.sub(r"^<notion_button>|</notion_button>$", "", stripped, flags=re.I)
            props = _json_load(payload, {})
            blocks.append({"type": "button", "content": str(props.get("label") or "버튼") if isinstance(props, dict) else "버튼", "props": props if isinstance(props, dict) else {}})
        elif stripped.lower().startswith("<notion_simple_table>") and stripped.lower().endswith("</notion_simple_table>"):
            flush_paragraph()
            payload = re.sub(r"^<notion_simple_table>|</notion_simple_table>$", "", stripped, flags=re.I)
            props = _json_load(payload, {})
            blocks.append({"type": "simple_table", "content": "", "props": props if isinstance(props, dict) else {}})
        elif stripped.lower().startswith("<notion_tabs>") and stripped.lower().endswith("</notion_tabs>"):
            flush_paragraph()
            payload = re.sub(r"^<notion_tabs>|</notion_tabs>$", "", stripped, flags=re.I)
            item = _json_load(payload, {})
            source_id = str(item.get("source_id") or "") if isinstance(item, dict) else ""
            props = item.get("props") if isinstance(item, dict) and isinstance(item.get("props"), dict) else item
            imported_id = _block_id()
            if source_id:
                imported_tab_groups[source_id] = imported_id
            blocks.append({"id": imported_id, "type": "tabs", "content": "", "props": props if isinstance(props, dict) else {}})
        elif stripped.lower().startswith("<notion_tab_member>") and stripped.lower().endswith("</notion_tab_member>"):
            flush_paragraph()
            payload = re.sub(r"^<notion_tab_member>|</notion_tab_member>$", "", stripped, flags=re.I)
            item = _json_load(payload, {})
            if isinstance(item, dict):
                props = item.get("props") if isinstance(item.get("props"), dict) else {}
                source_group = str(props.get("tab_group") or "")
                if source_group in imported_tab_groups:
                    props = {**props, "tab_group": imported_tab_groups[source_group]}
                blocks.append({
                    "type": str(item.get("type") or "paragraph"),
                    "content": str(item.get("content") or ""),
                    "props": props,
                })
        elif re.match(r"^<synced_block\s+id=[\"'][^\"']*[\"']>.*</synced_block>$", stripped, flags=re.I):
            flush_paragraph()
            sync_match = re.search(r'id=["\']([^"\']*)["\']', stripped, flags=re.I)
            content = re.sub(r"^<synced_block[^>]*>|</synced_block>$", "", stripped, flags=re.I)
            blocks.append({"type": "synced_block", "content": content, "props": {"sync_id": sync_match.group(1) if sync_match else ""}})
        elif stripped.startswith("- [ ] ") or stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
            flush_paragraph()
            checked = stripped[3].lower() == "x"
            indent = min(6, max(0, (len(line) - len(line.lstrip())) // 2))
            blocks.append({"type": "todo", "content": stripped[6:].strip(), "props": {"checked": checked, "indent": indent}})
        elif stripped.startswith("> "):
            flush_paragraph()
            blocks.append({"type": "quote", "content": stripped[2:].strip(), "props": {}})
        elif stripped.startswith("- "):
            flush_paragraph()
            indent = min(6, max(0, (len(line) - len(line.lstrip())) // 2))
            blocks.append({"type": "bullet", "content": stripped[2:].strip(), "props": {"indent": indent}})
        elif re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            indent = min(6, max(0, (len(line) - len(line.lstrip())) // 2))
            blocks.append({"type": "numbered", "content": re.sub(r"^\d+\.\s+", "", stripped).strip(), "props": {"indent": indent}})
        else:
            paragraph.append(line)
        idx += 1
    flush_paragraph()
    return blocks or [{"type": "paragraph", "content": "", "props": {}}]


def attach_page_to_session(user_id: str | None, page_id: str, session_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id required")
    owner, _role, page = _require_page(uid, page_id, "view")
    with _write_connection(uid, bootstrap_if_missing=True) as con:
        con.execute(
            """
            INSERT INTO session_pages(session_id, page_id, owner_user_id, created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(session_id, page_id) DO UPDATE SET owner_user_id=excluded.owner_user_id
            """,
            (session_id, page_id, owner, _now()),
        )
        con.commit()
    return {"session_id": session_id, "page_id": page_id, "owner_user_id": owner, "title": page.get("title") or ""}


def detach_page_from_session(user_id: str | None, page_id: str, session_id: str) -> dict[str, Any]:
    uid = _uid(user_id)
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id required")
    if not _database_exists(uid):
        return {"session_id": session_id, "page_id": page_id, "detached": True}
    with _write_connection(uid) as con:
        con.execute("DELETE FROM session_pages WHERE session_id=? AND page_id=?", (session_id, page_id))
        con.commit()
    return {"session_id": session_id, "page_id": page_id, "detached": True}


def list_session_pages(user_id: str | None, session_id: str) -> list[dict[str, Any]]:
    uid = _uid(user_id)
    session_id = str(session_id or "").strip()
    if not session_id:
        return []
    if not _database_exists(uid):
        return []
    with _connection(uid) as con:
        rows = con.execute(
            "SELECT * FROM session_pages WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            page = get_page(uid, row["page_id"])["page"]
        except Exception:
            continue
        items.append({
            "session_id": session_id,
            "page_id": row["page_id"],
            "owner_user_id": row["owner_user_id"],
            "created_at": row["created_at"],
            "page": page,
        })
    return items


def build_session_context(user_id: str | None, session_id: str) -> tuple[str, list[str]]:
    pages = list_session_pages(user_id, session_id)
    if not pages:
        return "", []
    parts = ["=== Attached Notion Workspace Pages ==="]
    used: list[str] = []
    used_chars = len(parts[0])
    for item in pages:
        page_id = item["page_id"]
        try:
            exported = export_page_markdown(user_id, page_id)
        except Exception:
            continue
        page = exported["page"]
        chunk = (
            f"\n--- Notion page: {page.get('title')} ({page_id}, owner={page.get('owner_user_id')}) ---\n"
            f"{exported['markdown']}\n"
        )
        remaining = MAX_CONTEXT_CHARS - used_chars
        if remaining <= 500:
            break
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip() + "\n... (attached Notion page truncated)\n"
        parts.append(chunk)
        used.append(page_id)
        used_chars += len(chunk)
    return ("\n".join(parts), used) if used else ("", [])


def create_page_from_markdown(
    user_id: str | None,
    *,
    title: str,
    markdown: str,
    parent_id: str = "",
) -> dict[str, Any]:
    return create_page(user_id, title=title, parent_id=parent_id, markdown=markdown)


def create_page_from_csv(
    user_id: str | None,
    *,
    title: str,
    csv_text: str,
    parent_id: str = "",
) -> dict[str, Any]:
    text = str(csv_text or "").lstrip("\ufeff")
    if not text.strip():
        raise ValueError("csv required")
    if len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("csv exceeds 2MB limit")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    raw_rows = list(csv.reader(io.StringIO(text), dialect))
    if not raw_rows:
        raise ValueError("csv has no rows")
    width = max(len(row) for row in raw_rows)
    if width < 1 or width > 500:
        raise ValueError("csv must contain between 1 and 500 columns")
    if len(raw_rows) - 1 > 2000:
        raise ValueError("csv can contain at most 2000 data rows")
    header_row = raw_rows[0]
    headers: list[str] = []
    seen_names: dict[str, int] = {}
    for index in range(width):
        base = " ".join(str(header_row[index] if index < len(header_row) else "").split())[:100] or f"Column {index + 1}"
        seen_names[base] = seen_names.get(base, 0) + 1
        headers.append(base if seen_names[base] == 1 else f"{base} {seen_names[base]}")
    data_rows = [(row + [""] * width)[:width] for row in raw_rows[1:] if any(str(cell).strip() for cell in row)]

    def infer_type(index: int) -> str:
        values = [str(row[index]).strip() for row in data_rows[:200] if str(row[index]).strip()]
        if not values:
            return "text"
        lowered = [value.lower() for value in values]
        if all(value in {"true", "false", "yes", "no", "y", "n", "1", "0", "checked", "unchecked"} for value in lowered):
            return "checkbox"
        try:
            [float(value.replace(",", "")) for value in values]
            return "number"
        except ValueError:
            pass
        try:
            [date.fromisoformat(value[:10]) for value in values if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10])]
            if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]) for value in values):
                return "date"
        except ValueError:
            pass
        if all(re.match(r"^https?://", value, flags=re.I) for value in values):
            return "url"
        if all(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) for value in values):
            return "email"
        return "text"

    property_types = ["title", *(infer_type(index) for index in range(1, width))]
    properties = [
        {"id": "title" if index == 0 else f"csv_{index}_{hashlib.sha1(headers[index].encode('utf-8')).hexdigest()[:8]}", "name": headers[index], "type": property_types[index]}
        for index in range(width)
    ]
    database_page = create_page(user_id, title=title, parent_id=parent_id)
    page_id = database_page["page"]["id"]
    now_iso = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for raw_row in data_rows:
        row_title = " ".join(str(raw_row[0] or "Untitled").split())[:160] or "Untitled"
        child = create_page(user_id, title=row_title, parent_id=page_id)["page"]
        item: dict[str, Any] = {
            "page_id": child["id"],
            "_created_time": now_iso,
            "_last_edited_time": now_iso,
            "_created_by": _uid(user_id),
            "_last_edited_by": _uid(user_id),
        }
        for index, property_item in enumerate(properties):
            raw_value = str(raw_row[index]).strip()
            property_type = property_item["type"]
            if property_type == "checkbox":
                value: Any = raw_value.lower() in {"true", "yes", "y", "1", "checked"}
            elif property_type == "number" and raw_value:
                number = float(raw_value.replace(",", ""))
                value = int(number) if number.is_integer() else number
            else:
                value = raw_value
            item[property_item["id"]] = value
        rows.append(item)
    blocks = [{
        "type": "table",
        "content": title,
        "props": {
            "view": "table",
            "rows": rows,
            "properties": properties,
            "filters": [],
            "sorts": [],
            "property_visibility": {item["id"]: True for item in properties},
        },
    }]
    return replace_blocks(user_id, page_id, blocks)


def _find_root_page_id(user_id: str, title: str) -> str:
    if not _database_exists(user_id):
        return ""
    with _connection(user_id) as con:
        row = con.execute(
            """
            SELECT id FROM pages
            WHERE archived=0 AND parent_id IS NULL AND lower(title)=lower(?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (title,),
        ).fetchone()
    return str(row["id"]) if row else ""


def create_second_brain_template(user_id: str | None) -> dict[str, Any]:
    """Create an opinionated Notion/PARA workspace for shared agent work."""
    uid = _uid(user_id)
    existing = _find_root_page_id(uid, "Second Brain Home")
    if existing:
        pages = [item for item in list_pages(uid) if item["id"] == existing or item.get("parent_id") == existing]
        return {"status": "existing", "home_page_id": existing, "pages": pages}

    home_md = """
## 이 페이지의 역할

Second Brain Home은 문서를 모아두는 페이지가 아니라 작업 대시보드입니다.
대화, 논문, PDF, PPTX, 회의 메모에서 나온 내용을 Inbox에 임시로 모으고, Projects/Areas/Resources에서 정제한 뒤, 검토가 끝난 지식만 Second Brain으로 업로드합니다.

## 빠른 사용 흐름

1. 대화나 문서에서 나온 raw 내용을 Inbox 또는 Chat Captures에 넣습니다.
2. 현재 진행 중인 일은 Projects에, 계속 유지해야 하는 책임 영역은 Areas에 정리합니다.
3. 재사용 가능한 개념, 수식, 설명, 코드 조각은 Resources로 옮깁니다.
4. 업로드 전에는 페이지 우측 상단 Second Brain 버튼으로 Markdown preview를 확인합니다.
5. 승인하면 source Markdown으로 들어가고, Second Brain의 graph/diff 검토 흐름을 탑니다.

## Weekly review

- [ ] Inbox와 Chat Captures를 비웁니다.
- [ ] 중복 페이지를 합치고, 오래된 임시 메모는 Archive로 옮깁니다.
- [ ] 안정된 지식만 Second Brain으로 업로드합니다.
- [ ] 공유 페이지와 연결된 채팅 세션 context를 점검합니다.

## Page map

- Inbox: raw capture
- Projects: active outcome
- Areas: ongoing responsibility
- Resources: reusable knowledge
- Sources: papers, docs, files, links
- Chat Captures: useful agent answers before curation
- Decisions: decision log
- Review: weekly cleanup queue
- Archive: inactive material
""".strip()
    child_specs = [
        (
            "Inbox",
            """
## Capture queue

- Drop raw ideas, copied chat answers, meeting notes, and document extracts here.
- Keep items lightweight until review.
- Move durable items into Projects, Areas, Resources, or Sources.
""",
        ),
        (
            "Projects",
            """
## Active projects

Use one child page per active outcome.

- Goal:
- Owner:
- Status:
- Next action:
- Related sources:
""",
        ),
        (
            "Areas",
            """
## Areas of responsibility

Areas are ongoing standards or responsibilities, not one-time outcomes.

- Operating principles
- Recurring checks
- Important links
- Open questions
""",
        ),
        (
            "Resources",
            """
## Knowledge libraries

Use this space for reusable topic pages, explanations, formulas, snippets, and references.

- Topic:
- Summary:
- When to use:
- Related pages:
""",
        ),
        (
            "Archive",
            """
## Inactive material

Move finished, paused, or superseded pages here so the workspace stays clean.
""",
        ),
        (
            "Sources",
            """
## Source documents

Track papers, PDFs, PPTX/DOCX imports, links, datasets, and image-heavy references.

- Source:
- Why it matters:
- Key extracts:
- Publish status:
""",
        ),
        (
            "Chat Captures",
            """
## Conversation captures

Save high-value agent answers here before turning them into durable notes.

- Original session:
- User intent:
- Useful answer:
- Follow-up needed:
""",
        ),
        (
            "Decisions",
            """
## Decision log

- Decision:
- Date:
- Context:
- Options considered:
- Rationale:
- Revisit trigger:
""",
        ),
        (
            "Review",
            """
## Review queue

- [ ] Check new Inbox notes.
- [ ] Merge duplicates.
- [ ] Promote stable notes to Second Brain.
- [ ] Remove stale shared context from chat sessions.
""",
        ),
    ]

    created: list[dict[str, Any]] = []
    home = create_page(uid, title="Second Brain Home", markdown=home_md)
    created.append(home["page"])
    home_id = home["page"]["id"]
    for title, markdown in child_specs:
        page = create_page(uid, title=title, parent_id=home_id, markdown=markdown.strip())
        created.append(page["page"])
    return {"status": "created", "home_page_id": home_id, "pages": created}


def chat_messages_to_markdown(messages: list[dict[str, Any]], title: str = "Chat Summary", session_id: str = "") -> str:
    lines = [
        f"# {title or 'Chat Summary'}",
        "",
        "> Source: Second Brain Agent chat session",
        f"> Session: {session_id or 'current'}",
        "",
    ]
    for idx, msg in enumerate(messages or [], start=1):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "message")
        content = _message_text(msg.get("content")).strip()
        if not content:
            continue
        lines.extend([f"## {idx}. {role}", "", content[:5000], ""])
    return "\n".join(lines).strip() + "\n"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text"}:
                    parts.append(str(item.get("text") or ""))
                elif item.get("text"):
                    parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part.strip())
    return str(content or "")
