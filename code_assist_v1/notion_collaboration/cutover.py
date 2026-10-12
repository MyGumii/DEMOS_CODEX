"""Default-off development cutover and legacy-compatible read projections.

The resolver accepts an already parsed K0 configuration; it never reads the
environment.  It has no legacy fallback and no owner-directory enumeration.
When disabled, the composition root remains responsible for selecting the
unchanged legacy application.  When enabled for an exact development canary,
all content and access decisions use the canonical authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Mapping, Sequence

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_store as workspace_store

from .canonical_authority import CanonicalAuthority
from .capability_switches import (
    CapabilitySwitchLoad,
    RequestScope,
    RuntimeScope,
)


PROJECTION_CONTRACT = "code_assist.notion.legacy_compatibility_projection.v1"
PROJECTION_MARKER = ".notion-compatibility-projection.json"
FORBIDDEN_COMPONENTS = {"knowledge", "user_data", ".runtime", ".notion-workspace-v2"}


class CutoverDisabled(RuntimeError):
    """The aggregate, capability, scope, realm, or schema fence is closed."""


class ProjectionBoundaryError(ValueError):
    """A compatibility projection target is unsafe or not create-only."""


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_exclusive(path: Path, value: Any) -> None:
    with path.open("xb") as output:
        output.write(_canonical_bytes(value))
        output.flush()
        os.fsync(output.fileno())


def _safe_projection_root(root: Path, allowed_parent: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not allowed_parent.is_absolute():
        raise ProjectionBoundaryError("absolute projection paths required")
    parent = allowed_parent.resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        parent.relative_to(temporary)
    except ValueError:
        if parent != temporary:
            raise ProjectionBoundaryError("projection parent must be temporary") from None
    candidate = root.resolve(strict=False)
    if candidate.parent != parent or candidate == parent or candidate.exists():
        raise ProjectionBoundaryError("projection root must be a new direct child")
    if parent.is_symlink() or any(part.lower() in FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise ProjectionBoundaryError("projection path is forbidden")
    return candidate, parent


def _forward_schema_ready(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "WHERE version=?",
        (migration.TARGET_VERSION,),
    ).fetchone()
    if marker is None or tuple(marker) != (
        migration.TARGET_NAME,
        migration.TARGET_CHECKSUM,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    ):
        return False
    required = {
        "workspaces",
        "workspace_memberships",
        "workspace_guests",
        "workspace_operations",
        "workspace_audit_events",
        "workspace_outbox",
        "workspace_collaboration_documents",
        "workspace_collaboration_operations",
        "workspace_collaboration_events",
    }
    observed = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return required.issubset(observed)


class AdditiveForwardCompatibleWorkspaceStore(workspace_store.WorkspaceStore):
    """Existing workspace API over the additive successor schema.

    The base ``WorkspaceStore`` intentionally recognizes only its frozen 1317
    marker.  This explicit adapter is the compatible-code half of rollback: it
    keeps the aggregate capability off while allowing unchanged workspace
    tables to remain usable without a schema downgrade.
    """

    def _read_connection(self) -> sqlite3.Connection:
        if not self.exists():
            raise workspace_store.WorkspaceSubstrateUnavailable(
                "workspace substrate is unavailable"
            )
        con = self._open_read_connection()
        try:
            if not _forward_schema_ready(con):
                raise workspace_store.WorkspaceSubstrateUnavailable(
                    "workspace substrate is unavailable"
                )
            return con
        except Exception:
            con.close()
            raise

    def _write_connection(self) -> sqlite3.Connection:
        if not self.exists():
            raise workspace_store.WorkspaceSubstrateUnavailable(
                "workspace substrate is unavailable"
            )
        con = self._configure(sqlite3.connect(self.db_path), query_only=False)
        try:
            if not _forward_schema_ready(con):
                raise workspace_store.WorkspaceSubstrateUnavailable(
                    "workspace substrate is unavailable"
                )
            return con
        except Exception:
            con.close()
            raise

    def capabilities(self) -> dict[str, Any]:
        if not self.exists():
            return {"workspace_v2": False, "schema_version": None}
        try:
            con = self._open_read_connection()
            try:
                ready = _forward_schema_ready(con)
            finally:
                con.close()
        except sqlite3.Error:
            ready = False
        return {
            "workspace_v2": ready,
            "schema_version": migration.TARGET_VERSION if ready else None,
            "additive_forward_compatible": ready,
            "schema_downgrade_required": False,
        }

    def bootstrap(self) -> dict[str, Any]:
        if not self.exists() or not self.capabilities()["workspace_v2"]:
            raise RuntimeError(
                "forward-compatible adapter requires an explicitly migrated catalog"
            )
        return {
            "created": False,
            "schema_version": migration.TARGET_VERSION,
            "workspace_v2": True,
            "additive_forward_compatible": True,
        }


class DevelopmentCanonicalResolver:
    """Exact-scope resolver for the default-off development candidate."""

    def __init__(
        self,
        authority: CanonicalAuthority,
        switch_load: CapabilitySwitchLoad,
        runtime_scope: RuntimeScope,
    ) -> None:
        if (
            not switch_load.enabled
            or switch_load.configuration is None
            or switch_load.configuration.realm != "development"
            or runtime_scope.realm != "development"
        ):
            raise CutoverDisabled("development canonical cutover is disabled")
        self.authority = authority
        self.configuration = switch_load.configuration
        self.runtime_scope = runtime_scope

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        *,
        switch_load: CapabilitySwitchLoad,
        runtime_scope: RuntimeScope,
    ) -> "DevelopmentCanonicalResolver":
        authority = CanonicalAuthority.open(catalog_path, user_data_root)
        return cls(authority, switch_load, runtime_scope)

    def _require(self, capability: str, subject_id: str, workspace_id: str) -> None:
        if not self.configuration.allows(
            capability,
            runtime=self.runtime_scope,
            request=RequestScope(subject_id=subject_id, workspace_id=workspace_id),
        ):
            raise CutoverDisabled("canonical capability or exact canary scope is disabled")

    def read_document(
        self,
        subject_id: str,
        workspace_id: str,
        document_id: str,
        *,
        expected_workspace_epoch: int | None = None,
        expected_document_epoch: int | None = None,
    ) -> dict[str, Any]:
        self._require("canonical_authority", subject_id, workspace_id)
        result = self.authority.read_document(
            subject_id,
            document_id,
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_document_epoch,
        )
        if result["workspace_id"] != workspace_id:
            raise CutoverDisabled("resolved document escaped exact workspace scope")
        return result

    def save_document(
        self,
        actor_subject_id: str,
        workspace_id: str,
        document_id: str,
        *,
        title: str,
        blocks: Sequence[Mapping[str, Any]],
        expected_revision: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        icon: str = "",
        cover: str = "",
        settings: Mapping[str, Any] | None = None,
        fault: str | None = None,
    ) -> dict[str, Any]:
        self._require("canonical_authority", actor_subject_id, workspace_id)
        result = self.authority.save_document(
            actor_subject_id,
            document_id,
            title=title,
            blocks=blocks,
            expected_revision=expected_revision,
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_document_epoch,
            operation_id=operation_id,
            icon=icon,
            cover=cover,
            settings=settings,
            fault=fault,
            runtime_generation_id=self.runtime_scope.deployment_generation_id,
        )
        if result["workspace_id"] != workspace_id:
            raise CutoverDisabled("written document escaped exact workspace scope")
        return result

    def compatibility_projection(
        self,
        subject_id: str,
        workspace_id: str,
        document_id: str,
        output_root: str | os.PathLike[str],
        *,
        allowed_parent: str | os.PathLike[str],
    ) -> dict[str, Any]:
        page = self.read_document(subject_id, workspace_id, document_id)
        candidate, _ = _safe_projection_root(Path(output_root), Path(allowed_parent))
        legacy_page = {
            "page": {
                "id": page["document_id"],
                "owner_user_id": subject_id if page["role"] == "owner" else None,
                "parent_id": page["parent_document_id"],
                "title": page["title"],
                "icon": page["icon"],
                "cover": page["cover"],
                "settings_json": json.dumps(page["settings"], ensure_ascii=False, sort_keys=True),
                "position": page["position"],
                "archived": page["archived"],
                "revision": page["revision"],
                "created_at": page["created_at"],
                "updated_at": page["updated_at"],
            },
            "blocks": [
                {
                    "id": block["id"],
                    "page_id": page["document_id"],
                    "position": block["position"],
                    "block_type": block["type"],
                    "content": block["content"],
                    "props_json": json.dumps(block["props"], ensure_ascii=False, sort_keys=True),
                }
                for block in page["blocks"]
            ],
            "comments": page["comments"],
        }
        projection = {
            "schema_version": 1,
            "contract": PROJECTION_CONTRACT,
            "workspace_id": workspace_id,
            "document_id": document_id,
            "canonical_revision": page["revision"],
            "canonical_content_sha256": page["content_sha256"],
            "workspace_epoch": page["workspace_epoch"],
            "document_epoch": page["document_epoch"],
            "generated_at": int(time.time()),
            "legacy": legacy_page,
        }
        page_bytes = _canonical_bytes(projection)
        marker = {
            "schema_version": 1,
            "contract": PROJECTION_CONTRACT,
            "workspace_id": workspace_id,
            "document_id": document_id,
            "projection_file": "page.json",
            "projection_sha256": _sha256(page_bytes),
            "source": "canonical_workspace_database",
            "rebuildable": True,
            "legacy_write_authority": False,
        }
        os.mkdir(candidate)
        _write_exclusive(candidate / "page.json", projection)
        _write_exclusive(candidate / PROJECTION_MARKER, marker)
        return {
            "status": "pass",
            "projection_sha256": marker["projection_sha256"],
            "canonical_content_sha256": page["content_sha256"],
            "canonical_revision": page["revision"],
            "file_count": 2,
            "legacy_write_count": 0,
            "request_time_directory_scan_count": 0,
        }

    def rollback_state(self, workspace_id: str) -> dict[str, Any]:
        return self.authority.cutover_state(
            self.runtime_scope.deployment_generation_id, workspace_id
        )


__all__ = [
    "AdditiveForwardCompatibleWorkspaceStore",
    "CutoverDisabled",
    "DevelopmentCanonicalResolver",
    "PROJECTION_CONTRACT",
    "PROJECTION_MARKER",
    "ProjectionBoundaryError",
]
