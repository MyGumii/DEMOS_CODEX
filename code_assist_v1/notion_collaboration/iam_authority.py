"""Canonical I0-P1 workspace and page authorization service.

This service is deliberately bound to the exact 2026082403 catalog.  It keeps
the Agent account registry as the login authority, resolves workspace, direct,
inherited, group, and teamspace access in one SQLite snapshot, and commits IAM
state, idempotency, event, audit, and outbox evidence in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping, Sequence

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_iam_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import (
    AccessBinding,
    AuthorityUnavailable,
    CanonicalAuthority,
    DeliveryResult,
    NotFoundOrDenied,
    QueuedFrame,
    _identifier,
    _operation_id,
    _request_sha256,
)
from code_assist_v1.notion_collaboration.durable_transport import (
    ForwardCompatibleCanonicalAuthority,
)


IAM_AUTHORITY_CONTRACT = "code_assist.notion.collaborative_iam_authority.v1"
PAGE_ROLE_LEVEL = {
    "view": 1,
    "comment": 2,
    "edit_content": 3,
    "edit": 4,
    "full_access": 5,
}
PAGE_ROLE_CAPABILITIES = {
    "view": ("read",),
    "comment": ("read", "comment"),
    "edit_content": ("read", "comment", "edit_content"),
    "edit": ("read", "comment", "edit_content", "edit_schema"),
    "full_access": (
        "read",
        "comment",
        "edit_content",
        "edit_schema",
        "share",
    ),
}
WORKSPACE_ROLES = {"restricted_member", "member", "membership_admin", "owner"}
WORKSPACE_ADMIN_ROLES = {"membership_admin", "owner"}
PRINCIPAL_TYPES = {"subject", "group", "teamspace"}
CANONICAL_ROLE = {
    "view": "view",
    "comment": "comment",
    "edit_content": "comment",
    "edit": "edit",
    "full_access": "owner",
}


class IdentityUnavailable(RuntimeError):
    """The Agent-side active-human decision cannot be obtained."""


class InMemoryIdentityDirectory:
    """Thread-safe active-human snapshot used by adapters and isolated tests.

    Authentication credentials are intentionally absent.  A runtime adapter may
    atomically replace the snapshot after reading the Agent registry outside a
    database transaction.
    """

    def __init__(self, active_human_subject_ids: Sequence[str]) -> None:
        self._lock = threading.RLock()
        self._generation = 1
        self._active = frozenset(
            _identifier(item, field="subject_id") for item in active_human_subject_ids
        )

    def is_active_human(self, subject_id: str) -> bool:
        subject = _identifier(subject_id, field="subject_id")
        with self._lock:
            return subject in self._active

    def replace(self, active_human_subject_ids: Sequence[str]) -> int:
        replacement = frozenset(
            _identifier(item, field="subject_id") for item in active_human_subject_ids
        )
        with self._lock:
            self._active = replacement
            self._generation += 1
            return self._generation

    def descriptor(self) -> dict[str, int]:
        with self._lock:
            return {
                "generation": self._generation,
                "active_human_count": len(self._active),
            }


@dataclass(frozen=True, slots=True)
class ResolvedAccess:
    subject_id: str
    workspace_id: str
    document_id: str
    role: str
    capabilities: tuple[str, ...]
    workspace_epoch: int
    document_epoch: int
    sources: tuple[Mapping[str, Any], ...]

    def descriptor(self) -> dict[str, Any]:
        return {
            "contract": IAM_AUTHORITY_CONTRACT,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "document_id": self.document_id,
            "role": self.role,
            "capabilities": list(self.capabilities),
            "workspace_epoch": self.workspace_epoch,
            "document_epoch": self.document_epoch,
            "sources": [dict(item) for item in self.sources],
        }


def _page_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in PAGE_ROLE_LEVEL:
        raise ValueError("valid page role required")
    return role


def _workspace_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in WORKSPACE_ROLES:
        raise ValueError("valid workspace role required")
    return role


def _principal_type(value: str) -> str:
    principal = str(value or "").strip().lower()
    if principal not in PRINCIPAL_TYPES:
        raise ValueError("valid principal_type required")
    return principal


def _expected_epoch(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"valid {field} required")
    return value


class CollaborativeIAMAuthority(ForwardCompatibleCanonicalAuthority):
    """Exact 2403 authority with one effective page-role resolver."""

    def __init__(
        self,
        catalog_path: Path,
        user_data_root: Path,
        identity_directory: InMemoryIdentityDirectory,
    ) -> None:
        super().__init__(catalog_path, user_data_root)
        self.identity_directory = identity_directory

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
    ) -> "CollaborativeIAMAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
                if migration_core._inspect(con, migration._contract())["state"] != "already_current":
                    raise AuthorityUnavailable("collaborative IAM schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative IAM authority is unavailable") from exc
        return cls(catalog, root, identity_directory)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            if query_only:
                con = sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                con = sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            marker = con.execute(
                "SELECT name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations WHERE version=?",
                (migration.TARGET_VERSION,),
            ).fetchone()
            if marker is None or tuple(marker) != (
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
            ):
                con.close()
                raise AuthorityUnavailable("collaborative IAM schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("collaborative IAM authority is unavailable") from exc

    def _require_active(self, subject_id: str) -> str:
        subject = _identifier(subject_id, field="subject_id")
        try:
            active = self.identity_directory.is_active_human(subject)
        except Exception as exc:
            raise IdentityUnavailable("identity authority unavailable") from exc
        if not active:
            raise NotFoundOrDenied("not found or permission denied")
        return subject

    @staticmethod
    def _resolution_on_connection(
        con: sqlite3.Connection,
        subject_id: str,
        workspace_id: str,
        document_id: str,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        document = con.execute(
            "SELECT * FROM workspace_collaboration_documents "
            "WHERE workspace_id=? AND id=?",
            (workspace_id, document_id),
        ).fetchone()
        if document is None:
            return None, []
        sources: list[dict[str, Any]] = []
        if str(document["legacy_owner_subject_id"]) == subject_id:
            sources.append(
                {
                    "kind": "document_owner",
                    "role": "full_access",
                    "source_document_id": document_id,
                    "inherited": False,
                }
            )
        membership = con.execute(
            "SELECT role FROM workspace_memberships "
            "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
            (workspace_id, subject_id),
        ).fetchone()
        if membership is not None:
            workspace_role = str(membership["role"])
            # Workspace administration and page authorship are independent.
            # Only a workspace owner has an implicit content grant; every
            # other member reaches content through ownership or an explicit
            # subject/group/teamspace grant.
            mapped = {"owner": "full_access"}.get(workspace_role)
            if mapped is not None:
                sources.append(
                    {
                        "kind": "workspace_role",
                        "role": mapped,
                        "workspace_role": workspace_role,
                        "inherited": True,
                    }
                )
        compatibility = con.execute(
            "SELECT role FROM workspace_collaboration_page_grants "
            "WHERE document_id=? AND subject_id=? AND active=1",
            (document_id, subject_id),
        ).fetchone()
        if compatibility is not None:
            sources.append(
                {
                    "kind": "compatibility_subject_grant",
                    "role": str(compatibility["role"]),
                    "source_document_id": document_id,
                    "inherited": False,
                }
            )
        rows = con.execute(
            """
            WITH RECURSIVE lineage(id,parent_document_id,depth) AS (
              SELECT id,parent_document_id,0
              FROM workspace_collaboration_documents
              WHERE workspace_id=? AND id=?
              UNION ALL
              SELECT parent.id,parent.parent_document_id,lineage.depth+1
              FROM workspace_collaboration_documents parent
              JOIN lineage ON lineage.parent_document_id=parent.id
              WHERE parent.workspace_id=? AND lineage.depth<100
            )
            SELECT grant.*,lineage.depth
            FROM lineage
            JOIN workspace_collaboration_access_grants grant
              ON grant.document_id=lineage.id AND grant.workspace_id=?
            WHERE grant.active=1
              AND (lineage.depth=0 OR grant.inherit_to_descendants=1)
              AND (
                (grant.principal_type='subject' AND grant.principal_id=?)
                OR
                (grant.principal_type='group' AND EXISTS (
                  SELECT 1
                  FROM workspace_group_members gm
                  JOIN workspace_memberships wm
                    ON wm.workspace_id=gm.workspace_id
                   AND wm.subject_id=gm.subject_id
                   AND wm.revoked_at IS NULL
                  WHERE gm.workspace_id=? AND gm.group_id=grant.principal_id
                    AND gm.subject_id=?
                ))
                OR
                (grant.principal_type='teamspace' AND EXISTS (
                  SELECT 1
                  FROM teamspace_memberships tm
                  JOIN teamspaces ts
                    ON ts.workspace_id=tm.workspace_id
                   AND ts.id=tm.teamspace_id
                   AND ts.archived_at IS NULL
                  JOIN workspace_memberships wm
                    ON wm.workspace_id=tm.workspace_id
                   AND wm.subject_id=tm.subject_id
                   AND wm.revoked_at IS NULL
                  WHERE tm.workspace_id=? AND tm.teamspace_id=grant.principal_id
                    AND tm.subject_id=? AND tm.revoked_at IS NULL
                ))
              )
            ORDER BY lineage.depth,grant.principal_type,grant.principal_id
            """,
            (
                workspace_id,
                document_id,
                workspace_id,
                workspace_id,
                subject_id,
                workspace_id,
                subject_id,
                workspace_id,
                subject_id,
            ),
        ).fetchall()
        for row in rows:
            sources.append(
                {
                    "kind": f"{row['principal_type']}_grant",
                    "principal_id": str(row["principal_id"]),
                    "role": str(row["role"]),
                    "source_document_id": str(row["document_id"]),
                    "inherited": int(row["depth"]) > 0,
                }
            )
        if not sources:
            return None, []
        effective = max(
            (str(item["role"]) for item in sources), key=PAGE_ROLE_LEVEL.__getitem__
        )
        return effective, sources

    @classmethod
    def _effective_role(
        cls,
        con: sqlite3.Connection,
        workspace_id: str,
        document_id: str,
        subject_id: str,
    ) -> str | None:
        effective, _sources = cls._resolution_on_connection(
            con, subject_id, workspace_id, document_id
        )
        return None if effective is None else CANONICAL_ROLE[effective]

    def resolve_access(self, subject_id: str, document_id: str) -> ResolvedAccess:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            row = con.execute(
                "SELECT workspace_id,access_epoch FROM workspace_collaboration_documents "
                "WHERE id=?",
                (document,),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            workspace_id = str(row["workspace_id"])
            role, sources = self._resolution_on_connection(
                con, subject, workspace_id, document
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
            workspace = con.execute(
                "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise NotFoundOrDenied("not found or permission denied")
            return ResolvedAccess(
                subject,
                workspace_id,
                document,
                role,
                PAGE_ROLE_CAPABILITIES[role],
                int(workspace["security_epoch"]),
                int(row["access_epoch"]),
                tuple(sources),
            )

    def binding(
        self, subject_id: str, document_id: str, *, minimum_role: str = "view"
    ) -> AccessBinding:
        self._require_active(subject_id)
        return super().binding(subject_id, document_id, minimum_role=minimum_role)

    def read_document(self, subject_id: str, document_id: str, **kwargs: Any) -> dict[str, Any]:
        self._require_active(subject_id)
        return super().read_document(subject_id, document_id, **kwargs)

    def create_document(self, actor_subject_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._require_active(actor_subject_id)
        return super().create_document(actor_subject_id, *args, **kwargs)

    def save_document(self, actor_subject_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._require_active(actor_subject_id)
        return super().save_document(actor_subject_id, *args, **kwargs)

    def add_comment(self, actor_subject_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._require_active(actor_subject_id)
        return super().add_comment(actor_subject_id, *args, **kwargs)

    def grant_page_access(
        self,
        actor_subject_id: str,
        document_id: str,
        subject_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._require_active(actor_subject_id)
        self._require_active(subject_id)
        return super().grant_page_access(
            actor_subject_id, document_id, subject_id, *args, **kwargs
        )

    def revoke_subject(self, actor_subject_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._require_active(actor_subject_id)
        return super().revoke_subject(actor_subject_id, *args, **kwargs)

    def authorize_frame_send(self, frame: QueuedFrame) -> DeliveryResult:
        try:
            self._require_active(frame.subject_id)
        except (IdentityUnavailable, NotFoundOrDenied, ValueError):
            return DeliveryResult(False, "denied_or_authority_unavailable", None, None)
        return super().authorize_frame_send(frame)

    @staticmethod
    def _workspace_admin(
        con: sqlite3.Connection,
        actor_subject_id: str,
        workspace_id: str,
        *,
        owner_only: bool = False,
    ) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_memberships "
            "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
            (workspace_id, actor_subject_id),
        ).fetchone()
        allowed = {"owner"} if owner_only else WORKSPACE_ADMIN_ROLES
        if row is None or str(row["role"]) not in allowed:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _assert_workspace_epoch(
        con: sqlite3.Connection, workspace_id: str, expected_workspace_epoch: int
    ) -> sqlite3.Row:
        row = con.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None or int(row["security_epoch"]) != expected_workspace_epoch:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _descendants(
        con: sqlite3.Connection, workspace_id: str, document_id: str, *, inherited: bool
    ) -> list[str]:
        if not inherited:
            row = con.execute(
                "SELECT id FROM workspace_collaboration_documents "
                "WHERE workspace_id=? AND id=?",
                (workspace_id, document_id),
            ).fetchone()
            return [] if row is None else [str(row["id"])]
        return [
            str(row["id"])
            for row in con.execute(
                """
                WITH RECURSIVE subtree(id,depth) AS (
                  SELECT id,0 FROM workspace_collaboration_documents
                  WHERE workspace_id=? AND id=?
                  UNION ALL
                  SELECT child.id,subtree.depth+1
                  FROM workspace_collaboration_documents child
                  JOIN subtree ON child.parent_document_id=subtree.id
                  WHERE child.workspace_id=? AND subtree.depth<100
                )
                SELECT id FROM subtree ORDER BY depth,id
                """,
                (workspace_id, document_id, workspace_id),
            ).fetchall()
        ]

    @staticmethod
    def _bump_document_epochs(con: sqlite3.Connection, document_ids: Sequence[str]) -> None:
        ids = tuple(dict.fromkeys(document_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        con.execute(
            f"UPDATE workspace_collaboration_documents "
            f"SET access_epoch=access_epoch+1,row_version=row_version+1,updated_at=? "
            f"WHERE id IN ({placeholders})",
            (int(time.time()), *ids),
        )

    @staticmethod
    def _bump_workspace(con: sqlite3.Connection, workspace_id: str) -> int:
        now = int(time.time())
        con.execute(
            "UPDATE workspaces SET security_epoch=security_epoch+1,"
            "row_version=row_version+1,updated_at=? WHERE id=?",
            (now, workspace_id),
        )
        row = con.execute(
            "SELECT security_epoch FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return int(row["security_epoch"])

    @classmethod
    def _require_full_access(
        cls,
        con: sqlite3.Connection,
        actor_subject_id: str,
        workspace_id: str,
        document_id: str,
    ) -> None:
        role, _sources = cls._resolution_on_connection(
            con, actor_subject_id, workspace_id, document_id
        )
        if role != "full_access":
            raise NotFoundOrDenied("not found or permission denied")

    def set_workspace_member(
        self,
        actor_subject_id: str,
        workspace_id: str,
        target_subject_id: str,
        *,
        role: str,
        expected_workspace_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        target = self._require_active(target_subject_id)
        workspace = _identifier(workspace_id, field="workspace_id")
        normalized_role = _workspace_role(role)
        expected = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        request = {
            "workspace_id": workspace,
            "target_subject_id": target,
            "role": normalized_role,
            "expected_workspace_epoch": expected,
        }
        request_hash = _request_sha256(actor, "workspace.member_set", request)
        with self._transaction() as con:
            owner_only = normalized_role in {"owner", "membership_admin"}

            def authorize_replay() -> None:
                self._require_active(actor)
                if actor != target:
                    self._workspace_admin(con, actor, workspace, owner_only=owner_only)

            replay = self._replay(
                con,
                actor=actor,
                action="workspace.member_set",
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            actor_membership = self._workspace_admin(con, actor, workspace)
            if owner_only and str(actor_membership["role"]) != "owner":
                raise NotFoundOrDenied("not found or permission denied")
            self._assert_workspace_epoch(con, workspace, expected)
            previous = con.execute(
                "SELECT * FROM workspace_memberships "
                "WHERE workspace_id=? AND subject_id=?",
                (workspace, target),
            ).fetchone()
            if previous is not None and previous["revoked_at"] is None:
                previous_role = str(previous["role"])
                if previous_role in {"owner", "membership_admin"} and str(actor_membership["role"]) != "owner":
                    raise NotFoundOrDenied("not found or permission denied")
                if previous_role == "owner" and normalized_role != "owner":
                    owner_count = int(
                        con.execute(
                            "SELECT COUNT(*) FROM workspace_memberships "
                            "WHERE workspace_id=? AND role='owner' AND revoked_at IS NULL",
                            (workspace,),
                        ).fetchone()[0]
                    )
                    if owner_count <= 1:
                        raise ValueError("workspace must retain at least one owner")
            now = int(time.time())
            con.execute(
                "UPDATE workspace_guests SET revoked_at=?,updated_at=?,guest_epoch=guest_epoch+1 "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            next_epoch = 1 if previous is None else int(previous["membership_epoch"]) + 1
            con.execute(
                """
                INSERT INTO workspace_memberships(
                  workspace_id,subject_id,role,membership_epoch,invited_by_subject_id,
                  joined_at,updated_at,revoked_at
                ) VALUES(?,?,?,?,?,?,?,NULL)
                ON CONFLICT(workspace_id,subject_id) DO UPDATE SET
                  role=excluded.role,membership_epoch=excluded.membership_epoch,
                  invited_by_subject_id=excluded.invited_by_subject_id,
                  updated_at=excluded.updated_at,revoked_at=NULL
                """,
                (workspace, target, normalized_role, next_epoch, actor, now, now),
            )
            if (
                previous is not None
                and previous["revoked_at"] is None
                and str(previous["role"]) == "owner"
                and normalized_role != "owner"
            ):
                primary = con.execute(
                    "SELECT owner_subject_id FROM workspaces WHERE id=?", (workspace,)
                ).fetchone()
                if primary is not None and str(primary["owner_subject_id"]) == target:
                    replacement = con.execute(
                        "SELECT subject_id FROM workspace_memberships "
                        "WHERE workspace_id=? AND role='owner' AND revoked_at IS NULL "
                        "ORDER BY joined_at,subject_id LIMIT 1",
                        (workspace,),
                    ).fetchone()
                    if replacement is None:
                        raise ValueError("workspace must retain at least one owner")
                    con.execute(
                        "UPDATE workspaces SET owner_subject_id=? WHERE id=?",
                        (str(replacement["subject_id"]), workspace),
                    )
            document_ids = [
                str(row["id"])
                for row in con.execute(
                    "SELECT id FROM workspace_collaboration_documents WHERE workspace_id=?",
                    (workspace,),
                ).fetchall()
            ]
            self._bump_document_epochs(con, document_ids)
            workspace_epoch = self._bump_workspace(con, workspace)
            self._require_active(actor)
            self._require_active(target)
            result = {
                "contract": IAM_AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "target_subject_id": target,
                "role": normalized_role,
                "membership_epoch": next_epoch,
                "workspace_epoch": workspace_epoch,
                "affected_document_ids": document_ids,
            }
            return self._finish(
                con,
                actor=actor,
                action="workspace.member_set",
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=None,
                workspace_epoch=workspace_epoch,
                document_epoch=None,
                document_sequence=None,
                event_type="workspace.member_set",
                event_payload={
                    "target_subject_id": target,
                    "role": normalized_role,
                    "affected_document_count": len(document_ids),
                },
                result=result,
                fault=fault,
            )

    def revoke_workspace_member(
        self,
        actor_subject_id: str,
        workspace_id: str,
        target_subject_id: str,
        *,
        expected_workspace_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        target = _identifier(target_subject_id, field="target_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        expected = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        request = {
            "workspace_id": workspace,
            "target_subject_id": target,
            "expected_workspace_epoch": expected,
        }
        request_hash = _request_sha256(actor, "workspace.member_revoke", request)
        with self._transaction() as con:
            target_row = con.execute(
                "SELECT * FROM workspace_memberships "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (workspace, target),
            ).fetchone()
            self_leave = actor == target
            if not self_leave:
                actor_row = self._workspace_admin(con, actor, workspace)
                if target_row is not None and str(target_row["role"]) in {"owner", "membership_admin"} and str(actor_row["role"]) != "owner":
                    raise NotFoundOrDenied("not found or permission denied")

            def authorize_replay() -> None:
                self._require_active(actor)
                if not self_leave:
                    self._workspace_admin(con, actor, workspace)

            replay = self._replay(
                con,
                actor=actor,
                action="workspace.member_revoke",
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            self._assert_workspace_epoch(con, workspace, expected)
            if target_row is None:
                raise NotFoundOrDenied("not found or permission denied")
            if str(target_row["role"]) == "owner":
                owner_count = int(
                    con.execute(
                        "SELECT COUNT(*) FROM workspace_memberships "
                        "WHERE workspace_id=? AND role='owner' AND revoked_at IS NULL",
                        (workspace,),
                    ).fetchone()[0]
                )
                if owner_count <= 1:
                    raise ValueError("workspace must retain at least one owner")
            now = int(time.time())
            con.execute(
                "DELETE FROM workspace_group_members WHERE workspace_id=? AND subject_id=?",
                (workspace, target),
            )
            con.execute(
                "UPDATE teamspace_memberships "
                "SET revoked_at=?,updated_at=?,membership_epoch=membership_epoch+1 "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            con.execute(
                "UPDATE workspace_collaboration_access_grants "
                "SET active=0,grant_epoch=grant_epoch+1,revoked_at=?,updated_at=? "
                "WHERE workspace_id=? AND principal_type='subject' AND principal_id=? AND active=1",
                (now, now, workspace, target),
            )
            con.execute(
                "UPDATE workspace_collaboration_page_grants "
                "SET active=0,grant_epoch=grant_epoch+1,revoked_at=?,updated_at=? "
                "WHERE subject_id=? AND active=1 AND document_id IN ("
                "SELECT id FROM workspace_collaboration_documents WHERE workspace_id=?)",
                (now, now, target, workspace),
            )
            con.execute(
                "UPDATE workspace_guests SET revoked_at=?,updated_at=?,guest_epoch=guest_epoch+1 "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            con.execute(
                "UPDATE workspace_memberships "
                "SET revoked_at=?,updated_at=?,membership_epoch=membership_epoch+1 "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            primary = con.execute(
                "SELECT owner_subject_id FROM workspaces WHERE id=?", (workspace,)
            ).fetchone()
            if primary is not None and str(primary["owner_subject_id"]) == target:
                replacement = con.execute(
                    "SELECT subject_id FROM workspace_memberships "
                    "WHERE workspace_id=? AND role='owner' AND revoked_at IS NULL "
                    "ORDER BY joined_at,subject_id LIMIT 1",
                    (workspace,),
                ).fetchone()
                if replacement is None:
                    raise ValueError("workspace must retain at least one owner")
                con.execute(
                    "UPDATE workspaces SET owner_subject_id=? WHERE id=?",
                    (str(replacement["subject_id"]), workspace),
                )
            document_ids = [
                str(row["id"])
                for row in con.execute(
                    "SELECT id FROM workspace_collaboration_documents WHERE workspace_id=?",
                    (workspace,),
                ).fetchall()
            ]
            self._bump_document_epochs(con, document_ids)
            workspace_epoch = self._bump_workspace(con, workspace)
            self._require_active(actor)
            result = {
                "contract": IAM_AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "revoked_subject_id": target,
                "workspace_epoch": workspace_epoch,
                "affected_document_ids": document_ids,
            }
            return self._finish(
                con,
                actor=actor,
                action="workspace.member_revoke",
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=None,
                workspace_epoch=workspace_epoch,
                document_epoch=None,
                document_sequence=None,
                event_type="workspace.member_revoked",
                event_payload={
                    "target_subject_id": target,
                    "affected_document_count": len(document_ids),
                },
                result=result,
                fault=fault,
            )

    def set_page_grant(
        self,
        actor_subject_id: str,
        document_id: str,
        principal_type: str,
        principal_id: str,
        *,
        role: str,
        inherit_to_descendants: bool,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        kind = _principal_type(principal_type)
        principal = _identifier(principal_id, field="principal_id")
        normalized_role = _page_role(role)
        expected_workspace = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
        expected_document = _expected_epoch(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        if kind == "subject":
            self._require_active(principal)
        request = {
            "document_id": document,
            "principal_type": kind,
            "principal_id": principal,
            "role": normalized_role,
            "inherit_to_descendants": bool(inherit_to_descendants),
            "expected_workspace_epoch": expected_workspace,
            "expected_document_epoch": expected_document,
        }
        request_hash = _request_sha256(actor, "page.grant_set", request)
        with self._transaction() as con:
            page = self._document(con, document)
            workspace = str(page["workspace_id"])
            self._require_full_access(con, actor, workspace, document)

            def authorize_replay() -> None:
                self._require_active(actor)
                self._require_full_access(con, actor, workspace, document)

            replay = self._replay(
                con,
                actor=actor,
                action="page.grant_set",
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            workspace_row = self._assert_workspace_epoch(con, workspace, expected_workspace)
            if int(page["access_epoch"]) != expected_document:
                raise NotFoundOrDenied("not found or permission denied")
            now = int(time.time())
            if kind == "subject":
                membership = con.execute(
                    "SELECT 1 FROM workspace_memberships "
                    "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                    (workspace, principal),
                ).fetchone()
                if membership is None:
                    guest = con.execute(
                        "SELECT * FROM workspace_guests WHERE workspace_id=? AND subject_id=?",
                        (workspace, principal),
                    ).fetchone()
                    guest_epoch = 1 if guest is None else int(guest["guest_epoch"]) + 1
                    con.execute(
                        """
                        INSERT INTO workspace_guests(
                          workspace_id,subject_id,guest_epoch,invited_by_subject_id,
                          created_at,updated_at,revoked_at
                        ) VALUES(?,?,?,?,?,?,NULL)
                        ON CONFLICT(workspace_id,subject_id) DO UPDATE SET
                          guest_epoch=excluded.guest_epoch,
                          invited_by_subject_id=excluded.invited_by_subject_id,
                          updated_at=excluded.updated_at,revoked_at=NULL
                        """,
                        (workspace, principal, guest_epoch, actor, now, now),
                    )
            elif kind == "group":
                row = con.execute(
                    "SELECT 1 FROM workspace_groups WHERE workspace_id=? AND id=?",
                    (workspace, principal),
                ).fetchone()
                if row is None:
                    raise NotFoundOrDenied("not found or permission denied")
            else:
                row = con.execute(
                    "SELECT 1 FROM teamspaces "
                    "WHERE workspace_id=? AND id=? AND archived_at IS NULL",
                    (workspace, principal),
                ).fetchone()
                if row is None:
                    raise NotFoundOrDenied("not found or permission denied")
            previous = con.execute(
                "SELECT grant_epoch,created_at FROM workspace_collaboration_access_grants "
                "WHERE document_id=? AND principal_type=? AND principal_id=?",
                (document, kind, principal),
            ).fetchone()
            grant_epoch = 1 if previous is None else int(previous["grant_epoch"]) + 1
            created_at = now if previous is None else int(previous["created_at"])
            con.execute(
                """
                INSERT INTO workspace_collaboration_access_grants(
                  workspace_id,document_id,principal_type,principal_id,role,
                  inherit_to_descendants,active,grant_epoch,granted_by_subject_id,
                  created_at,updated_at,revoked_at
                ) VALUES(?,?,?,?,?,?,1,?,?,?,?,NULL)
                ON CONFLICT(document_id,principal_type,principal_id) DO UPDATE SET
                  role=excluded.role,
                  inherit_to_descendants=excluded.inherit_to_descendants,
                  active=1,grant_epoch=excluded.grant_epoch,
                  granted_by_subject_id=excluded.granted_by_subject_id,
                  updated_at=excluded.updated_at,revoked_at=NULL
                """,
                (
                    workspace,
                    document,
                    kind,
                    principal,
                    normalized_role,
                    int(bool(inherit_to_descendants)),
                    grant_epoch,
                    actor,
                    created_at,
                    now,
                ),
            )
            affected = self._descendants(
                con, workspace, document, inherited=bool(inherit_to_descendants)
            )
            self._bump_document_epochs(con, affected)
            current_document_epoch = int(
                con.execute(
                    "SELECT access_epoch FROM workspace_collaboration_documents WHERE id=?",
                    (document,),
                ).fetchone()[0]
            )
            self._require_active(actor)
            if kind == "subject":
                self._require_active(principal)
            result = {
                "contract": IAM_AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "document_id": document,
                "principal_type": kind,
                "principal_id": principal,
                "role": normalized_role,
                "inherit_to_descendants": bool(inherit_to_descendants),
                "grant_epoch": grant_epoch,
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "document_epoch": current_document_epoch,
                "affected_document_ids": affected,
            }
            return self._finish(
                con,
                actor=actor,
                action="page.grant_set",
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=document,
                workspace_epoch=int(workspace_row["security_epoch"]),
                document_epoch=current_document_epoch,
                document_sequence=None,
                event_type="page.grant_set",
                event_payload={
                    "principal_type": kind,
                    "principal_id": principal,
                    "role": normalized_role,
                    "inherit_to_descendants": bool(inherit_to_descendants),
                    "affected_document_count": len(affected),
                },
                result=result,
                fault=fault,
            )

    def revoke_page_grant(
        self,
        actor_subject_id: str,
        document_id: str,
        principal_type: str,
        principal_id: str,
        *,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        kind = _principal_type(principal_type)
        principal = _identifier(principal_id, field="principal_id")
        expected_workspace = _expected_epoch(expected_workspace_epoch, field="workspace_epoch")
        expected_document = _expected_epoch(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        request = {
            "document_id": document,
            "principal_type": kind,
            "principal_id": principal,
            "expected_workspace_epoch": expected_workspace,
            "expected_document_epoch": expected_document,
        }
        request_hash = _request_sha256(actor, "page.grant_revoke", request)
        with self._transaction() as con:
            page = self._document(con, document)
            workspace = str(page["workspace_id"])
            self._require_full_access(con, actor, workspace, document)

            def authorize_replay() -> None:
                self._require_active(actor)
                self._require_full_access(con, actor, workspace, document)

            replay = self._replay(
                con,
                actor=actor,
                action="page.grant_revoke",
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            workspace_row = self._assert_workspace_epoch(con, workspace, expected_workspace)
            if int(page["access_epoch"]) != expected_document:
                raise NotFoundOrDenied("not found or permission denied")
            grant = con.execute(
                "SELECT * FROM workspace_collaboration_access_grants "
                "WHERE document_id=? AND principal_type=? AND principal_id=? AND active=1",
                (document, kind, principal),
            ).fetchone()
            if grant is None:
                raise NotFoundOrDenied("not found or permission denied")
            now = int(time.time())
            con.execute(
                "UPDATE workspace_collaboration_access_grants "
                "SET active=0,grant_epoch=grant_epoch+1,revoked_at=?,updated_at=? "
                "WHERE document_id=? AND principal_type=? AND principal_id=? AND active=1",
                (now, now, document, kind, principal),
            )
            inherited = bool(grant["inherit_to_descendants"])
            affected = self._descendants(con, workspace, document, inherited=inherited)
            self._bump_document_epochs(con, affected)
            if kind == "subject":
                still_member = con.execute(
                    "SELECT 1 FROM workspace_memberships "
                    "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                    (workspace, principal),
                ).fetchone()
                still_granted = con.execute(
                    "SELECT 1 FROM workspace_collaboration_access_grants "
                    "WHERE workspace_id=? AND principal_type='subject' "
                    "AND principal_id=? AND active=1 LIMIT 1",
                    (workspace, principal),
                ).fetchone()
                legacy_granted = con.execute(
                    "SELECT 1 FROM workspace_collaboration_page_grants legacy "
                    "JOIN workspace_collaboration_documents page ON page.id=legacy.document_id "
                    "WHERE page.workspace_id=? AND legacy.subject_id=? AND legacy.active=1 LIMIT 1",
                    (workspace, principal),
                ).fetchone()
                if still_member is None and still_granted is None and legacy_granted is None:
                    con.execute(
                        "UPDATE workspace_guests "
                        "SET revoked_at=?,updated_at=?,guest_epoch=guest_epoch+1 "
                        "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                        (now, now, workspace, principal),
                    )
            current_document_epoch = int(
                con.execute(
                    "SELECT access_epoch FROM workspace_collaboration_documents WHERE id=?",
                    (document,),
                ).fetchone()[0]
            )
            self._require_active(actor)
            result = {
                "contract": IAM_AUTHORITY_CONTRACT,
                "workspace_id": workspace,
                "document_id": document,
                "principal_type": kind,
                "principal_id": principal,
                "state": "revoked",
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "document_epoch": current_document_epoch,
                "affected_document_ids": affected,
            }
            return self._finish(
                con,
                actor=actor,
                action="page.grant_revoke",
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=document,
                workspace_epoch=int(workspace_row["security_epoch"]),
                document_epoch=current_document_epoch,
                document_sequence=None,
                event_type="page.grant_revoked",
                event_payload={
                    "principal_type": kind,
                    "principal_id": principal,
                    "affected_document_count": len(affected),
                },
                result=result,
                fault=fault,
            )

    def integrity(self) -> dict[str, Any]:
        with self._reader() as con:
            quick = str(con.execute("PRAGMA quick_check").fetchone()[0])
            foreign_keys = len(con.execute("PRAGMA foreign_key_check").fetchall())
            marker = con.execute(
                "SELECT version,name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations"
            ).fetchall()
            return {
                "contract": IAM_AUTHORITY_CONTRACT,
                "quick_check": quick,
                "foreign_key_error_count": foreign_keys,
                "schema_version": int(marker[0]["version"]) if len(marker) == 1 else None,
                "schema_marker_count": len(marker),
                "identity_directory": self.identity_directory.descriptor(),
                "request_time_directory_scan_count": 0,
                "legacy_source_database_open_count": 0,
                "dual_write_count": 0,
            }


def execute_iam_command(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise ValueError("command object required")
    active_subjects = document.get("active_subject_ids")
    if not isinstance(active_subjects, list) or not all(
        isinstance(item, str) for item in active_subjects
    ):
        raise ValueError("active_subject_ids array required")
    authority = CollaborativeIAMAuthority.open(
        str(document.get("catalog_path", "")),
        str(document.get("user_data_root", "")),
        InMemoryIdentityDirectory(active_subjects),
    )
    command = str(document.get("command", ""))
    arguments = document.get("arguments")
    if not isinstance(arguments, Mapping):
        raise ValueError("command arguments object required")
    if command == "resolve_access":
        return authority.resolve_access(**dict(arguments)).descriptor()
    if command == "set_workspace_member":
        return authority.set_workspace_member(**dict(arguments))
    if command == "revoke_workspace_member":
        return authority.revoke_workspace_member(**dict(arguments))
    if command == "set_page_grant":
        return authority.set_page_grant(**dict(arguments))
    if command == "revoke_page_grant":
        return authority.revoke_page_grant(**dict(arguments))
    if command == "integrity":
        return authority.integrity()
    raise ValueError("unsupported IAM command")


__all__ = [
    "CollaborativeIAMAuthority",
    "IAM_AUTHORITY_CONTRACT",
    "IdentityUnavailable",
    "InMemoryIdentityDirectory",
    "PAGE_ROLE_CAPABILITIES",
    "PAGE_ROLE_LEVEL",
    "ResolvedAccess",
    "execute_iam_command",
]
