"""I0-P2 runtime adapters for the exact collaborative IAM catalog.

The I0-P1 authority deliberately owns only the canonical IAM transaction.
This module connects that authority to the existing workspace and editor hosts
without weakening the exact 2026082403 schema gate.  The old page store is a
temporary compatibility projection; the collaborative catalog remains the
authorization source of truth.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping, Sequence

from code_assist_v1 import notion_workspace_iam_migration as iam_migration
from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
from code_assist_v1 import notion_workspace_editing_authority_migration as editing_migration
from code_assist_v1 import notion_workspace_second_brain_publication_migration as publication_migration
from code_assist_v1 import notion_workspace_database_features_migration as database_migration
from code_assist_v1 import notion_workspace_file_authority_migration as file_migration
from code_assist_v1 import notion_workspace_history_authority_migration as history_migration
from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    CanonicalAuthority,
    NotFoundOrDenied,
)
from code_assist_v1.notion_collaboration.durable_transport import (
    DurableTransportAuthority,
)
from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority
from code_assist_v1.notion_collaboration.history_authority import CollaborativeHistoryAuthority
from code_assist_v1.notion_collaboration.search_authority import CollaborativeSearchAuthority
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
    PAGE_ROLE_LEVEL,
)


RUNTIME_ENABLE_ENV = "CODE_ASSIST_I0_COLLABORATIVE_SHARING"
RUNTIME_KILL_SWITCH_ENV = "CODE_ASSIST_I0_COLLABORATIVE_SHARING_KILL_SWITCH"
CATALOG_ENV = "CODE_ASSIST_COLLABORATION_CATALOG"
USER_DATA_ROOT_ENV = "CODE_ASSIST_COLLABORATION_USER_DATA_ROOT"
GATEWAY_URL_ENV = "CODE_ASSIST_COLLABORATION_GATEWAY_URL"
RUNTIME_CONTRACT = "code_assist.notion.collaborative_iam_runtime.v1"


def runtime_requested(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(RUNTIME_ENABLE_ENV, "") == "1" and values.get(
        RUNTIME_KILL_SWITCH_ENV, ""
    ) in {"", "0"}


def configured_paths(
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    values = os.environ if environ is None else environ
    if not runtime_requested(values):
        raise AuthorityUnavailable("collaborative IAM runtime is disabled")
    raw_catalog = str(values.get(CATALOG_ENV, "") or "").strip()
    raw_root = str(values.get(USER_DATA_ROOT_ENV, "") or "").strip()
    if not raw_catalog or not raw_root:
        raise AuthorityUnavailable("collaborative IAM runtime paths are unavailable")
    root = Path(raw_root).resolve(strict=True)
    catalog = Path(raw_catalog).resolve(strict=True)
    expected = Path(nws.catalog_path_for_user_data_root(root)).resolve(strict=False)
    if catalog != expected or not catalog.is_file():
        raise AuthorityUnavailable("collaborative IAM runtime path binding mismatch")
    return catalog, root


def _target_marker_ready(con: sqlite3.Connection) -> bool:
    current = _current_target(con)
    return current is not None


def _current_target(con: sqlite3.Connection) -> tuple[int, str, str, str] | None:
    latest = con.execute(
        "SELECT MAX(version) FROM workspace_schema_migrations"
    ).fetchone()
    if not latest or latest[0] is None:
        return None
    version = int(latest[0])
    targets = {
        iam_migration.TARGET_VERSION: (
            iam_migration.TARGET_NAME,
            iam_migration.TARGET_CHECKSUM,
            iam_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        live_migration.TARGET_VERSION: (
            live_migration.TARGET_NAME,
            live_migration.TARGET_CHECKSUM,
            live_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        editing_migration.TARGET_VERSION: (
            editing_migration.TARGET_NAME,
            editing_migration.TARGET_CHECKSUM,
            editing_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        publication_migration.TARGET_VERSION: (
            publication_migration.TARGET_NAME,
            publication_migration.TARGET_CHECKSUM,
            publication_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        database_migration.TARGET_VERSION: (
            database_migration.TARGET_NAME,
            database_migration.TARGET_CHECKSUM,
            database_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        file_migration.TARGET_VERSION: (
            file_migration.TARGET_NAME,
            file_migration.TARGET_CHECKSUM,
            file_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        history_migration.TARGET_VERSION: (
            history_migration.TARGET_NAME,
            history_migration.TARGET_CHECKSUM,
            history_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
        search_migration.TARGET_VERSION: (
            search_migration.TARGET_NAME,
            search_migration.TARGET_CHECKSUM,
            search_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ),
    }
    expected = targets.get(version)
    if expected is None:
        return None
    row = con.execute(
        "SELECT name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations WHERE version=?",
        (version,),
    ).fetchone()
    if row is None or tuple(row) != expected:
        return None
    return (version, *expected)


class CollaborativeWorkspaceStore(nws.WorkspaceStore):
    """Forward-compatible NP-2 workspace facade over exact schema 2403."""

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
    ) -> None:
        super().__init__(db_path)
        self.user_data_root = Path(user_data_root).resolve(strict=True)
        self.identity_directory = identity_directory
        con = self._open_read_connection()
        try:
            target = _current_target(con)
        finally:
            con.close()
        if target is not None and target[0] == search_migration.TARGET_VERSION:
            self.iam = CollaborativeSearchAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )
        elif target is not None and target[0] == history_migration.TARGET_VERSION:
            self.iam = CollaborativeHistoryAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )
        elif target is not None and target[0] == file_migration.TARGET_VERSION:
            self.iam = CollaborativeFileAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )
        elif target is not None and target[0] in {
            editing_migration.TARGET_VERSION,
            publication_migration.TARGET_VERSION,
            database_migration.TARGET_VERSION,
        }:
            from code_assist_v1.notion_collaboration.editing_authority import (
                CollaborativeEditingAuthority,
            )

            self.iam = CollaborativeEditingAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )
        elif target is not None and target[0] == live_migration.TARGET_VERSION:
            from code_assist_v1.notion_collaboration.live_authority import (
                CollaborativeLiveAuthority,
            )

            self.iam = CollaborativeLiveAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )
        else:
            self.iam = CollaborativeIAMAuthority.open(
                self.db_path,
                self.user_data_root,
                identity_directory,
            )

    def _read_connection(self) -> sqlite3.Connection:
        con = self._open_read_connection()
        try:
            if not _target_marker_ready(con):
                raise nws.WorkspaceSubstrateUnavailable(
                    "workspace substrate is unavailable"
                )
            return con
        except Exception:
            con.close()
            raise

    def _write_connection(self) -> sqlite3.Connection:
        if not self.exists():
            raise nws.WorkspaceSubstrateUnavailable(
                "workspace substrate is unavailable"
            )
        con = self._configure(
            sqlite3.connect(self.db_path),
            query_only=False,
            healing_clock=self._healing_now,
        )
        try:
            if not _target_marker_ready(con):
                raise nws.WorkspaceSubstrateUnavailable(
                    "workspace substrate is unavailable"
                )
            return con
        except Exception:
            con.close()
            raise

    def capabilities(self) -> dict[str, Any]:
        if not self.exists():
            return {
                "workspace_v2": False,
                "schema_version": None,
                "collaborative_iam_sharing": False,
            }
        try:
            con = self._open_read_connection()
            try:
                target = _current_target(con)
                ready = target is not None
            finally:
                con.close()
        except sqlite3.Error:
            ready = False
            target = None
        return {
            "workspace_v2": ready,
            "schema_version": target[0] if target is not None else None,
            "collaborative_iam_sharing": ready,
            "live_collaboration": bool(
                target is not None
                and target[0]
                in {
                    live_migration.TARGET_VERSION,
                    editing_migration.TARGET_VERSION,
                    publication_migration.TARGET_VERSION,
                    database_migration.TARGET_VERSION,
                    file_migration.TARGET_VERSION,
                    history_migration.TARGET_VERSION,
                    search_migration.TARGET_VERSION,
                }
            ),
            "collaborative_editing": bool(
                target is not None
                and target[0]
                in {
                    editing_migration.TARGET_VERSION,
                    publication_migration.TARGET_VERSION,
                    database_migration.TARGET_VERSION,
                    file_migration.TARGET_VERSION,
                    history_migration.TARGET_VERSION,
                    search_migration.TARGET_VERSION,
                }
            ),
            "second_brain_publication_intents": bool(
                target is not None
                and target[0]
                in {
                    publication_migration.TARGET_VERSION,
                    database_migration.TARGET_VERSION,
                    file_migration.TARGET_VERSION,
                    history_migration.TARGET_VERSION,
                    search_migration.TARGET_VERSION,
                }
            ),
            "database_features": bool(
                target is not None
                and target[0] in {
                    database_migration.TARGET_VERSION,
                    file_migration.TARGET_VERSION,
                    history_migration.TARGET_VERSION,
                    search_migration.TARGET_VERSION,
                }
            ),
            "collaborative_files": bool(
                target is not None
                and target[0] in {
                    file_migration.TARGET_VERSION,
                    history_migration.TARGET_VERSION,
                    search_migration.TARGET_VERSION,
                }
            ),
            "collaborative_history": bool(
                target is not None
                and target[0]
                in {history_migration.TARGET_VERSION, search_migration.TARGET_VERSION}
            ),
            "collaborative_search": bool(
                target is not None and target[0] == search_migration.TARGET_VERSION
            ),
        }

    def get_navigation_state(
        self, subject_id: str | None, workspace_id: str
    ) -> dict[str, Any]:
        """Return ordinary member state or identifier-safe guest defaults."""

        try:
            return super().get_navigation_state(subject_id, workspace_id)
        except PermissionError:
            raise
        except FileNotFoundError as original:
            subject = str(subject_id or "").strip()
            workspace = str(workspace_id or "").strip()
            con = self._read_connection()
            try:
                guest = con.execute(
                    "SELECT guest_epoch FROM workspace_guests "
                    "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                    (workspace, subject),
                ).fetchone()
                if guest is None:
                    raise original
                sections = [
                    {
                        "section": section,
                        "display_order": index,
                        "hidden": False,
                        "collapsed": False,
                        "item_limit": 10,
                    }
                    for index, section in enumerate(nws.NAVIGATION_SECTIONS)
                ]
                return {
                    "workspace_id": workspace,
                    "subject_id": subject,
                    "membership_epoch": int(guest["guest_epoch"]),
                    "preference_version": 0,
                    "favorite_version": 0,
                    "sidebar": {
                        "width_px": nws.DEFAULT_SIDEBAR_WIDTH_PX,
                        "collapsed": False,
                    },
                    "sections": sections,
                    "favorites": [],
                }
            finally:
                con.close()

    def list_page_source_bindings(
        self, subject_id: str | None, workspace_id: str
    ) -> list[dict[str, Any]]:
        """Project only source bindings allowed by the I0 effective resolver."""

        subject = str(subject_id or "").strip()
        workspace = str(workspace_id or "").strip()
        self.iam._require_active(subject)
        con = self._read_connection()
        try:
            membership = con.execute(
                "SELECT 1 FROM workspace_memberships "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (workspace, subject),
            ).fetchone()
            guest = con.execute(
                "SELECT 1 FROM workspace_guests "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (workspace, subject),
            ).fetchone()
            if membership is None and guest is None:
                raise FileNotFoundError("workspace not found")
            rows = con.execute(
                "SELECT * FROM workspace_page_sources "
                "WHERE workspace_id=? AND state='active' "
                "ORDER BY source_owner_subject_id,source_page_id",
                (workspace,),
            ).fetchall()
            visible: list[dict[str, Any]] = []
            for row in rows:
                page_id = str(row["source_page_id"])
                role, _sources = self.iam._resolution_on_connection(
                    con, subject, workspace, page_id
                )
                if role is None:
                    continue
                visible.append(
                    {
                        "workspace_id": workspace,
                        "source_owner_subject_id": str(
                            row["source_owner_subject_id"]
                        ),
                        "source_page_id": page_id,
                        "teamspace_id": str(row["teamspace_id"] or ""),
                        "placement": str(row["placement"]),
                        "state": str(row["state"]),
                        "source_revision": int(row["source_revision"]),
                        "source_updated_at": int(row["source_updated_at"]),
                        "source_sha256": str(row["source_sha256"]),
                        "version": int(row["row_version"]),
                    }
                )
            return visible
        finally:
            con.close()


class CollaborativeDurableTransportAuthority(DurableTransportAuthority):
    """T0 durable transport facade that accepts only exact additive 2403."""

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
    ) -> "CollaborativeDurableTransportAuthority":
        catalog = Path(catalog_path).resolve(strict=True)
        root = Path(user_data_root).resolve(strict=True)
        expected = Path(nws.catalog_path_for_user_data_root(root)).resolve(
            strict=False
        )
        if catalog != expected:
            raise AuthorityUnavailable(
                "collaborative durable transport path binding mismatch"
            )
        con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            if not _target_marker_ready(con):
                raise AuthorityUnavailable(
                    "collaborative durable transport schema is not current"
                )
        finally:
            con.close()
        return cls(catalog, root)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        if query_only:
            con = sqlite3.connect(
                f"{self.catalog_path.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
        else:
            con = sqlite3.connect(
                self.catalog_path, timeout=5.0, isolation_level=None
            )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        if not _target_marker_ready(con):
            con.close()
            raise AuthorityUnavailable(
                "collaborative durable transport schema marker mismatch"
            )
        return con

    def canonical_authority(
        self,
    ) -> "CollaborativeForwardCompatibleCanonicalAuthority":
        return CollaborativeForwardCompatibleCanonicalAuthority(
            self.catalog_path, self.user_data_root
        )


class CollaborativeForwardCompatibleCanonicalAuthority(CanonicalAuthority):
    """A0B document adapter over the exact additive 2403 catalog."""

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        return CollaborativeDurableTransportAuthority(
            self.catalog_path, self.user_data_root
        )._connect(query_only=query_only)

    @classmethod
    def _effective_role(
        cls,
        con: sqlite3.Connection,
        workspace_id: str,
        document_id: str,
        subject_id: str,
    ) -> str | None:
        exact, _sources = CollaborativeIAMAuthority._resolution_on_connection(
            con, subject_id, workspace_id, document_id
        )
        if exact is None:
            return None
        # The durable transport carries content CRDT updates only.  Its legacy
        # four-role adapter must therefore admit exact-IAM ``edit_content`` as
        # ``edit`` for a write ticket.  Schema/share commands still pass through
        # their exact typed authorities and retain the stronger ``edit`` or
        # ``full_access`` checks.
        return {
            "view": "view",
            "comment": "comment",
            "edit_content": "edit",
            "edit": "edit",
            "full_access": "owner",
        }[exact]

    def replay_domain_events(
        self,
        subject_id: str,
        document_id: str,
        *,
        after_document_sequence: int,
        limit: int,
    ):
        from code_assist_v1.notion_collaboration.live_authority import (
            CollaborativeLiveAuthority,
        )

        directory = InMemoryIdentityDirectory([subject_id])
        authority = CollaborativeLiveAuthority(
            self.catalog_path, self.user_data_root, directory
        )
        return authority.replay_domain_events(
            subject_id,
            document_id,
            after_document_sequence=after_document_sequence,
            limit=limit,
        )


def open_runtime_store(
    active_human_subject_ids: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> CollaborativeWorkspaceStore:
    catalog, root = configured_paths(environ)
    directory = InMemoryIdentityDirectory(active_human_subject_ids)
    return CollaborativeWorkspaceStore(
        catalog,
        user_data_root=root,
        identity_directory=directory,
    )


def gateway_url(environ: Mapping[str, str] | None = None) -> str:
    from urllib.parse import urlsplit

    values = os.environ if environ is None else environ
    value = str(values.get(GATEWAY_URL_ENV, "") or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "ws"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.path != "/realtime"
        or parsed.query
        or parsed.fragment
    ):
        raise AuthorityUnavailable("collaboration gateway URL is unavailable")
    return value


def issue_realtime_ticket(
    store: CollaborativeWorkspaceStore,
    subject_id: str,
    workspace_id: str,
    document_id: str,
    *,
    client_id: str,
    session_binding_sha256: str,
    origin: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    actor = str(subject_id or "").strip()
    access = store.iam.resolve_access(actor, document_id)
    if access.workspace_id != workspace_id:
        raise NotFoundOrDenied("not found or permission denied")
    capabilities = ["read", "presence"]
    if PAGE_ROLE_LEVEL[access.role] >= PAGE_ROLE_LEVEL["comment"]:
        capabilities.append("comment")
    if PAGE_ROLE_LEVEL[access.role] >= PAGE_ROLE_LEVEL["edit_content"]:
        capabilities.append("write")
    transport = CollaborativeDurableTransportAuthority.open(
        store.db_path, store.user_data_root
    )
    issued_at = int(time.time() * 1000) if now_ms is None else int(now_ms)
    issued = transport.issue_ticket(
        csrf_valid=True,
        subject_id=actor,
        workspace_id=workspace_id,
        document_id=document_id,
        client_id=client_id,
        session_binding_sha256=session_binding_sha256,
        origin=origin,
        capabilities=tuple(capabilities),
        now_ms=issued_at,
    )
    binding = store.iam.binding(actor, document_id)
    return {
        "contract": RUNTIME_CONTRACT,
        "token": issued.token,
        "gateway_url": gateway_url(),
        "subject_id": actor,
        "workspace_id": workspace_id,
        "document_id": document_id,
        "client_id": client_id,
        "session_binding_sha256": session_binding_sha256,
        "capabilities": capabilities,
        "workspace_epoch": access.workspace_epoch,
        "document_epoch": access.document_epoch,
        "last_document_sequence": binding.document_sequence,
        "expires_at_ms": issued.claims.expires_at_ms,
    }


def _stable_operation(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"i0p2-{prefix}-{digest}"


def ensure_personal_workspace(
    store: CollaborativeWorkspaceStore,
    subject_id: str,
    *,
    display_name: str,
) -> list[dict[str, Any]]:
    subject = str(subject_id or "").strip()
    items = store.list_workspaces(subject)
    if any(
        item.get("owner_subject_id") == subject
        and item.get("access", {}).get("role") == "owner"
        for item in items
    ):
        return items
    label = str(display_name or subject).strip()[:120] or subject
    store.create_workspace(
        subject,
        name=f"{label} Workspace",
        icon=label[:1].upper(),
        operation_id=_stable_operation("personal-workspace", subject),
    )
    return store.list_workspaces(subject)


def ensure_collaborative_document(
    store: CollaborativeWorkspaceStore,
    actor_subject_id: str,
    workspace_id: str,
    page: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    *,
    source_operation_id: str,
) -> dict[str, Any]:
    actor = str(actor_subject_id or "").strip()
    document_id = str(page.get("id") or "").strip()
    try:
        return store.iam.read_document(actor, document_id)
    except NotFoundOrDenied:
        pass
    workspace = store.get_workspace(actor, workspace_id)
    parent = str(page.get("parent_id") or "").strip() or None
    if parent:
        try:
            store.iam.read_document(actor, parent)
        except NotFoundOrDenied:
            parent = None
    normalized_blocks = [
        {
            "id": str(item.get("id") or ""),
            "type": str(item.get("type") or "paragraph"),
            "content": str(item.get("content") or ""),
            "props": dict(item.get("props") or {}),
        }
        for item in blocks
    ]
    store.iam.create_document(
        actor,
        workspace_id,
        document_id,
        title=str(page.get("title") or "Untitled"),
        icon=str(page.get("icon") or ""),
        cover=str(page.get("cover") or ""),
        settings=dict(page.get("settings") or {}),
        blocks=normalized_blocks,
        parent_document_id=parent,
        expected_workspace_epoch=int(workspace["security_epoch"]),
        operation_id=_stable_operation(
            "document-mirror", workspace_id, document_id, source_operation_id
        ),
    )
    return store.iam.read_document(actor, document_id)


def collaborative_page_payload(
    authority: CollaborativeIAMAuthority,
    subject_id: str,
    document_id: str,
) -> dict[str, Any]:
    exact = authority.resolve_access(subject_id, document_id)
    document = authority.read_document(subject_id, document_id)
    can_edit = PAGE_ROLE_LEVEL[exact.role] >= PAGE_ROLE_LEVEL["edit_content"]
    can_share = exact.role == "full_access"
    legacy_role = {
        "full_access": "owner",
        "edit": "edit",
        "edit_content": "edit",
        "comment": "comment",
        "view": "view",
    }[exact.role]
    page = {
        "id": document["document_id"],
        "workspace_id": document["workspace_id"],
        "owner_user_id": _document_owner(authority, document_id),
        "parent_id": str(document.get("parent_document_id") or ""),
        "title": document["title"],
        "icon": document["icon"],
        "cover": document["cover"],
        "settings": document["settings"],
        "scope": "own"
        if _document_owner(authority, document_id) == subject_id
        else "shared",
        "role": legacy_role,
        "iam_role": exact.role,
        "can_edit": can_edit,
        "can_share": can_share,
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
        "revision": document["revision"],
        "document_sequence": document["document_sequence"],
        "workspace_epoch": exact.workspace_epoch,
        "document_epoch": exact.document_epoch,
        "access_sources": [dict(item) for item in exact.sources],
    }
    blocks = [
        {
            "id": item["id"],
            "type": item["type"],
            "content": item["content"],
            "props": item["props"],
        }
        for item in document["blocks"]
    ]
    databases: list[dict[str, Any]] = []
    if isinstance(authority, CollaborativeDatabaseAuthority):
        databases = authority.read_databases(subject_id, document_id)
    files: list[dict[str, Any]] = []
    if isinstance(authority, CollaborativeFileAuthority):
        files = [
            {
                **item,
                "url": f"/api/notion/collaboration/files/{item['id']}",
                "size": item["size_bytes"],
            }
            for item in authority.list_files(subject_id, document_id)["files"]
        ]
    return {
        "contract": RUNTIME_CONTRACT,
        "page": page,
        "blocks": blocks,
        "databases": databases,
        "files": files,
        "revision": document["revision"],
        "capabilities": {
            "atomic_save": True,
            "collaborative_iam": True,
            "collaborative_databases": isinstance(
                authority, CollaborativeDatabaseAuthority
            ),
            "collaborative_files": isinstance(authority, CollaborativeFileAuthority),
            "collaborative_history": isinstance(authority, CollaborativeHistoryAuthority),
            "collaborative_search": isinstance(authority, CollaborativeSearchAuthority),
        },
    }


def list_page_grants(
    authority: CollaborativeIAMAuthority,
    actor_subject_id: str,
    document_id: str,
) -> dict[str, Any]:
    access = authority.resolve_access(actor_subject_id, document_id)
    if access.role != "full_access":
        raise NotFoundOrDenied("not found or permission denied")
    with authority._reader() as con:
        rows = con.execute(
            "SELECT principal_type,principal_id,role,inherit_to_descendants,"
            "grant_epoch,granted_by_subject_id,created_at,updated_at "
            "FROM workspace_collaboration_access_grants "
            "WHERE document_id=? AND active=1 "
            "ORDER BY principal_type,principal_id",
            (document_id,),
        ).fetchall()
    return {
        "contract": RUNTIME_CONTRACT,
        "workspace_id": access.workspace_id,
        "document_id": access.document_id,
        "workspace_epoch": access.workspace_epoch,
        "document_epoch": access.document_epoch,
        "items": [
            {
                "principal_type": str(row["principal_type"]),
                "principal_id": str(row["principal_id"]),
                "role": str(row["role"]),
                "inherit_to_descendants": bool(row["inherit_to_descendants"]),
                "grant_epoch": int(row["grant_epoch"]),
                "granted_by_subject_id": str(row["granted_by_subject_id"]),
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ],
        "effective_access": access.descriptor(),
    }


def _document_owner(
    authority: CollaborativeIAMAuthority, document_id: str
) -> str:
    with authority._reader() as con:
        row = con.execute(
            "SELECT legacy_owner_subject_id "
            "FROM workspace_collaboration_documents WHERE id=?",
            (document_id,),
        ).fetchone()
    if row is None:
        raise NotFoundOrDenied("not found or permission denied")
    return str(row["legacy_owner_subject_id"])


def document_owner(
    authority: CollaborativeIAMAuthority, document_id: str
) -> str:
    return _document_owner(authority, document_id)


def workspace_document_ids(
    authority: CollaborativeIAMAuthority, workspace_id: str
) -> list[str]:
    with authority._reader() as con:
        rows = con.execute(
            "SELECT id FROM workspace_collaboration_documents "
            "WHERE workspace_id=? ORDER BY id",
            (str(workspace_id or "").strip(),),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def legacy_projection_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    mapping = {
        "view": "view",
        "comment": "comment",
        "edit_content": "edit",
        "edit": "edit",
        "full_access": "edit",
    }
    if normalized not in mapping:
        raise ValueError("valid page role required")
    return mapping[normalized]


__all__ = [
    "CATALOG_ENV",
    "CollaborativeDurableTransportAuthority",
    "CollaborativeForwardCompatibleCanonicalAuthority",
    "CollaborativeWorkspaceStore",
    "RUNTIME_CONTRACT",
    "RUNTIME_ENABLE_ENV",
    "RUNTIME_KILL_SWITCH_ENV",
    "USER_DATA_ROOT_ENV",
    "collaborative_page_payload",
    "configured_paths",
    "document_owner",
    "ensure_collaborative_document",
    "gateway_url",
    "issue_realtime_ticket",
    "ensure_personal_workspace",
    "legacy_projection_role",
    "list_page_grants",
    "open_runtime_store",
    "runtime_requested",
    "workspace_document_ids",
]
