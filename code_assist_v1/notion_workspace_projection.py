"""Exact-owner legacy locator capture and synchronous shadow projection.

This module is intentionally path-injected and is not registered by either
application.  It opens one explicitly named legacy owner shard read-only,
captures only opaque file IDs and a one-way public-token digest, and asks the
workspace catalog to CAS-replace one active page generation.  The legacy read
and catalog write are separate transactions; callers must not describe the
sequence as cross-database atomic.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from code_assist_v1.notion_workspace_inventory import (
    LegacyInventoryBoundsExceeded,
    WorkspaceInventoryError,
    _exact_subject,
    _inherited_share_role,
    _open_read_only,
    _page_proof,
    logical_database_fingerprint,
    resolve_inventory_paths,
)
from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageHealingCandidate,
    LegacyPageQuarantineCandidate,
    LegacyPageSourceProof,
    LegacyPageLocatorProjection,
    LegacyPublicSourceLocator,
    SOURCE_HEALING_QUARANTINE_REASONS,
    WorkspaceStore,
    _page_projection_sha256,
    page_source_proof_actor_on_connection,
)
from code_assist_v1.notion_legacy_change_contract import (
    CHANGE_PAGE,
    LegacyChangeContractError,
    assert_legacy_rollback_journal,
    inspect_legacy_change_contract,
    legacy_source_file_identity,
    open_exact_legacy_change_source,
    resolve_legacy_change_paths,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyChangeReadError,
    LegacyOwnerIntegrityAttestation,
    LegacyOwnerIntegrityAttestationMismatch,
    _authenticate_owner_snapshot,
    _receipt_from_snapshot,
)


_FILE_ID_RE = re.compile(r"^f_[0-9a-f]{32}$")
_PAGE_ID_MAX = 160
_MAX_SHADOW_BLOCKS = 10000
_MAX_SHADOW_FILES = 5000
_MAX_SHADOW_PAGE_TEXT_BYTES = 16 * 1024 * 1024
_MAX_SHADOW_PAGE_METADATA_BYTES = 1024 * 1024
_MAX_SHADOW_FILE_METADATA_BYTES = 4 * 1024 * 1024
_MAX_SHADOW_PUBLIC_METADATA_BYTES = 64 * 1024
_MAX_SHADOW_PERMISSION_HOPS = 256
_MAX_SHADOW_PERMISSION_QUERIES = 4096
_MAX_SHADOW_LINKED_REFERENCES = 512
_MAX_SHADOW_LINKED_METADATA_BYTES = 2 * 1024 * 1024
_SHADOW_STATES = (
    "match",
    "safe_miss",
    "stale",
    "superseded",
    "ambiguous",
    "permission_mismatch",
    "unsafe_path",
    "oversize",
    "catalog_unavailable",
)

def _sqlite_int64_identity(value: int) -> int:
    """Encode one unsigned OS identity losslessly in SQLite's signed INTEGER."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkspaceProjectionError("legacy source identity is invalid")
    if -9_223_372_036_854_775_808 <= value <= 9_223_372_036_854_775_807:
        return value
    if 9_223_372_036_854_775_807 < value <= 18_446_744_073_709_551_615:
        return value - 18_446_744_073_709_551_616
    raise WorkspaceProjectionError("legacy source identity is invalid")


class WorkspaceProjectionError(WorkspaceInventoryError):
    """Fail-closed legacy projection error with no source details."""


class UnsafeLegacyFileError(WorkspaceProjectionError):
    """The legacy attachment locator is not a safe regular file."""


class LegacyProjectionPermissionError(WorkspaceProjectionError):
    """The projection actor cannot read the exact legacy page."""


class LegacyProjectionAmbiguousError(WorkspaceProjectionError):
    """The exact legacy source contains an ambiguous locator set."""


class LegacyProjectionStaleError(WorkspaceProjectionError):
    """The catalog generation no longer describes the exact legacy page."""


class LegacyProjectionSupersededError(LegacyProjectionStaleError):
    """The activation event names a permanently superseded generation."""


class LegacyProjectionOversizeError(WorkspaceProjectionError):
    """The exact legacy page exceeds the bounded shadow-read contract."""


_PERMANENT_HEALING_QUARANTINE_REASONS = {
    LegacyProjectionPermissionError: "legacy_permission_revoked",
    UnsafeLegacyFileError: "unsafe_path",
    LegacyProjectionAmbiguousError: "ambiguous",
    LegacyProjectionOversizeError: "oversize",
    LegacyInventoryBoundsExceeded: "oversize",
}


def _permanent_healing_quarantine_reason(exc: BaseException) -> str | None:
    for error_type, reason in _PERMANENT_HEALING_QUARANTINE_REASONS.items():
        if isinstance(exc, error_type):
            if reason not in SOURCE_HEALING_QUARANTINE_REASONS:
                raise WorkspaceProjectionError(
                    "source healing quarantine contract is invalid"
                )
            return reason
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _safe_regular_file(
    *,
    user_data_root: Path,
    source_owner_id: str,
    rel_path: str,
) -> None:
    try:
        raw = str(rel_path or "")
        relative = Path(raw)
        if (
            not raw
            or relative.is_absolute()
            or os.path.splitdrive(raw)[0]
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise UnsafeLegacyFileError("legacy attachment path is unsafe")
        owner_root = (user_data_root / source_owner_id / "notion").resolve(strict=True)
        files_root = (owner_root / "files").resolve(strict=True)
        lexical = owner_root / relative
        if not _inside(lexical, owner_root):
            raise UnsafeLegacyFileError("legacy attachment path is unsafe")
        # Inspect the unresolved lexical chain.  Resolving first would erase
        # evidence that a component was a symlink or Windows junction.
        current = owner_root
        for part in relative.parts:
            current = current / part
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or is_junction():
                raise UnsafeLegacyFileError("legacy attachment path is unsafe")
        candidate = lexical.resolve(strict=True)
        if not _inside(candidate, files_root) or not candidate.is_file():
            raise UnsafeLegacyFileError("legacy attachment path is unsafe")
    except UnsafeLegacyFileError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeLegacyFileError("legacy attachment path is unsafe") from exc


def _catalog_binding(
    catalog_path: Path,
    *,
    workspace_id: str,
    owner: str,
    page_id: str,
    expected_binding_version: int | None = None,
    allowed_states: tuple[str, ...] = ("active",),
) -> dict[str, Any]:
    con = _open_read_only(catalog_path)
    try:
        row = con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   state,row_version,source_revision,source_updated_at,
                   source_sha256,bound_by_subject_id
            FROM workspace_page_sources
            WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
            """,
            (workspace_id, owner, page_id),
        ).fetchone()
        if not row or str(row["state"]) not in allowed_states:
            raise LegacyProjectionStaleError("legacy page binding is stale")
        binding_version = int(row["row_version"])
        if (
            expected_binding_version is not None
            and binding_version != expected_binding_version
        ):
            # Fence the activation event generation before consulting the
            # legacy source or even reading the workspace version.
            raise LegacyProjectionSupersededError(
                "legacy page binding generation is superseded"
            )
        workspace = con.execute(
            "SELECT row_version FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
        if not workspace:
            raise WorkspaceProjectionError("active page binding is unavailable")
        proof_actor = page_source_proof_actor_on_connection(con, row)
        return {
            "binding_version": binding_version,
            "binding_state": str(row["state"]),
            "workspace_version": int(workspace["row_version"]),
            "source_revision": int(row["source_revision"]),
            "source_updated_at": int(row["source_updated_at"]),
            "source_sha256": str(row["source_sha256"]),
            "bound_by_subject_id": str(row["bound_by_subject_id"]),
            "proof_actor_subject_id": proof_actor,
        }
    except sqlite3.Error as exc:
        raise WorkspaceProjectionError("workspace catalog is unavailable") from exc
    finally:
        con.close()


def _optional_binding_version(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkspaceProjectionError("valid expected_binding_version required")
    return value


def _assert_bounded_shadow_page(con: sqlite3.Connection, page_id: str) -> None:
    """Bound page-local materialization before the legacy proof helper runs."""
    page_stats = con.execute(
        """
        SELECT COALESCE(
          length(CAST(COALESCE(parent_id,'') AS BLOB)) +
          length(CAST(COALESCE(title,'') AS BLOB)) +
          length(CAST(COALESCE(icon,'') AS BLOB)) +
          length(CAST(COALESCE(cover,'') AS BLOB)) +
          length(CAST(COALESCE(settings_json,'') AS BLOB)),0)
          AS metadata_bytes
        FROM pages WHERE id=?
        """,
        (page_id,),
    ).fetchone()
    block_stats = con.execute(
        """
        SELECT COUNT(*) AS item_count,
               COALESCE(SUM(
                            length(CAST(COALESCE(id,'') AS BLOB)) +
                            length(CAST(COALESCE(page_id,'') AS BLOB)) +
                            length(CAST(COALESCE(owner_user_id,'') AS BLOB)) +
                            length(CAST(COALESCE(block_type,'') AS BLOB)) +
                            length(CAST(COALESCE(content,'') AS BLOB)) +
                            length(CAST(COALESCE(props_json,'') AS BLOB))),0)
                 AS text_bytes
        FROM (
          SELECT id,page_id,owner_user_id,block_type,content,props_json FROM blocks
          WHERE page_id=? ORDER BY position LIMIT ?
        )
        """,
        (page_id, _MAX_SHADOW_BLOCKS + 1),
    ).fetchone()
    file_stats = con.execute(
        """
        SELECT COUNT(*) AS item_count,
               COALESCE(SUM(
                 length(CAST(COALESCE(id,'') AS BLOB)) +
                 length(CAST(COALESCE(owner_user_id,'') AS BLOB)) +
                 length(CAST(COALESCE(uploader_user_id,'') AS BLOB)) +
                 length(CAST(COALESCE(original_name,'') AS BLOB)) +
                 length(CAST(COALESCE(stored_name,'') AS BLOB)) +
                 length(CAST(COALESCE(rel_path,'') AS BLOB)) +
                 length(CAST(COALESCE(mime_type,'') AS BLOB))),0)
                 AS metadata_bytes
        FROM (
          SELECT * FROM files WHERE page_id=? ORDER BY created_at DESC LIMIT ?
        )
        """,
        (page_id, _MAX_SHADOW_FILES + 1),
    ).fetchone()
    public_stats = con.execute(
        """
        SELECT COUNT(*) AS item_count,
               COALESCE(SUM(length(CAST(COALESCE(token,'') AS BLOB))),0)
                 AS metadata_bytes
        FROM (
          SELECT token FROM public_links
          WHERE page_id=? AND revoked_at IS NULL LIMIT 2
        )
        """,
        (page_id,),
    ).fetchone()
    if (
        (page_stats and int(page_stats[0] or 0) > _MAX_SHADOW_PAGE_METADATA_BYTES)
        or
        int(block_stats["item_count"] or 0) > _MAX_SHADOW_BLOCKS
        or int(block_stats["text_bytes"] or 0) > _MAX_SHADOW_PAGE_TEXT_BYTES
        or int(file_stats["item_count"] or 0) > _MAX_SHADOW_FILES
        or int(file_stats["metadata_bytes"] or 0)
        > _MAX_SHADOW_FILE_METADATA_BYTES
        or int(public_stats["item_count"] or 0) > 1
        or int(public_stats["metadata_bytes"] or 0)
        > _MAX_SHADOW_PUBLIC_METADATA_BYTES
    ):
        raise LegacyProjectionOversizeError(
            "legacy page exceeds the bounded projection contract"
        )


def _bounded_inherited_share_role(
    con: sqlite3.Connection,
    page_id: str,
    actor_subject_id: str,
    cache: dict[str, str] | None = None,
    query_budget: dict[str, int] | None = None,
) -> str:
    memo = cache if cache is not None else {}
    budget = query_budget if query_budget is not None else {
        "remaining": _MAX_SHADOW_PERMISSION_QUERIES
    }
    current = page_id
    trail: list[str] = []
    visited: set[str] = set()
    for _hop in range(_MAX_SHADOW_PERMISSION_HOPS):
        if not current or current in visited:
            break
        if current in memo:
            role = memo[current]
            for item in trail:
                memo[item] = role
            return role
        visited.add(current)
        trail.append(current)
        if int(budget.get("remaining", 0)) <= 0:
            raise LegacyProjectionOversizeError(
                "legacy permission query budget exceeds the bounded contract"
            )
        budget["remaining"] = int(budget["remaining"]) - 1
        row = con.execute(
            """
            SELECT p.parent_id,s.role FROM pages p
            LEFT JOIN page_shares s
              ON s.page_id=p.id AND s.shared_user_id=? AND s.revoked_at IS NULL
            WHERE p.id=? AND p.archived=0
            """,
            (actor_subject_id, current),
        ).fetchone()
        if not row:
            break
        if row["role"]:
            role = str(row["role"])
            for item in trail:
                memo[item] = role
            return role
        current = str(row["parent_id"] or "")
        if len(current.encode("utf-8")) > _PAGE_ID_MAX:
            raise LegacyProjectionOversizeError(
                "legacy permission ancestry exceeds the bounded contract"
            )
    if current:
        raise LegacyProjectionOversizeError(
            "legacy permission ancestry exceeds the bounded contract"
        )
    for item in trail:
        memo[item] = ""
    return ""


def _capture_bounded_fresh_page(
    con: sqlite3.Connection,
    *,
    paths: dict[str, Path],
    source_owner_id: str,
    actor_subject_id: str,
    page_id: str,
) -> tuple[
    LegacyPageSourceProof,
    tuple[LegacyFileSourceLocator, ...],
    tuple[LegacyPublicSourceLocator, ...],
]:
    """Capture fresh proof and secret-free locators under the shadow bounds."""
    _assert_bounded_shadow_page(con, page_id)
    page = con.execute(
        "SELECT * FROM pages WHERE id=? AND owner_user_id=? AND archived=0",
        (page_id, source_owner_id),
    ).fetchone()
    if not page:
        # A watermark can say that a physical row exists while the row is
        # archived or no longer belongs to this exact owner.  Neither case is
        # a tombstone and both fail closed.
        raise WorkspaceProjectionError("legacy page is unavailable")
    permission_query_budget = {"remaining": _MAX_SHADOW_PERMISSION_QUERIES}

    def bounded_role_resolver(
        role_con: sqlite3.Connection,
        role_page: str,
        role_actor: str,
        role_cache: dict[str, str],
    ) -> str:
        return _bounded_inherited_share_role(
            role_con,
            role_page,
            role_actor,
            role_cache,
            permission_query_budget,
        )

    role = (
        "owner"
        if actor_subject_id == source_owner_id
        else bounded_role_resolver(con, page_id, actor_subject_id, {})
    )
    if role not in {"owner", "edit", "comment", "view"}:
        raise LegacyProjectionPermissionError("legacy page is unavailable")
    proof = _page_proof(
        con,
        actor=actor_subject_id,
        owner=source_owner_id,
        page=page,
        role=role,
        reference_limit=_MAX_SHADOW_LINKED_REFERENCES,
        linked_metadata_byte_limit=_MAX_SHADOW_LINKED_METADATA_BYTES,
        role_resolver=bounded_role_resolver,
    )

    file_rows = con.execute(
        "SELECT id,rel_path FROM files "
        "WHERE page_id=? AND owner_user_id=? ORDER BY id",
        (page_id, source_owner_id),
    ).fetchall()
    file_ids: list[str] = []
    for row in file_rows:
        file_id = str(row["id"] or "")
        if not _FILE_ID_RE.fullmatch(file_id):
            raise WorkspaceProjectionError("legacy attachment ID is invalid")
        _safe_regular_file(
            user_data_root=paths["root"],
            source_owner_id=source_owner_id,
            rel_path=str(row["rel_path"] or ""),
        )
        file_ids.append(file_id)
    if len(file_ids) != len(set(file_ids)):
        raise LegacyProjectionAmbiguousError("legacy locator set is ambiguous")

    public_rows = con.execute(
        "SELECT token,allow_duplicate FROM public_links "
        "WHERE page_id=? AND owner_user_id=? AND revoked_at IS NULL",
        (page_id, source_owner_id),
    ).fetchall()
    if len(public_rows) > 1:
        raise LegacyProjectionAmbiguousError("legacy locator set is ambiguous")
    public_sources: tuple[LegacyPublicSourceLocator, ...] = ()
    if public_rows:
        raw_token = str(public_rows[0]["token"] or "")
        if not raw_token:
            raise WorkspaceProjectionError("legacy public locator is invalid")
        digest = "sha256:" + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        raw_token = ""
        public_sources = (
            LegacyPublicSourceLocator(
                digest, bool(public_rows[0]["allow_duplicate"])
            ),
        )
    return (
        proof,
        tuple(LegacyFileSourceLocator(item) for item in sorted(file_ids)),
        public_sources,
    )


def _capture_exact_owner_healing(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int,
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
    quarantine_permanent: bool,
) -> LegacyPageHealingCandidate | LegacyPageQuarantineCandidate:
    """Capture one outcome from one authenticated catalog/source snapshot."""
    # Operator quiescence is the first gate, before even resolving a path.
    if source_quiesced_confirmed is not True:
        raise WorkspaceProjectionError(
            "healing candidate requires source_quiesced_confirmed"
        )
    page_id = str(source_page_id or "")
    if not page_id or page_id != page_id.strip() or len(page_id) > _PAGE_ID_MAX:
        raise WorkspaceProjectionError("valid source_page_id required")
    generation = _optional_binding_version(expected_binding_version)
    if generation is None:
        raise WorkspaceProjectionError("valid expected_binding_version required")
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    binding = _catalog_binding(
        paths["catalog"],
        workspace_id=workspace_id,
        owner=source_owner_id,
        page_id=page_id,
        expected_binding_version=generation,
        allowed_states=("active", "quarantined"),
    )
    proof_actor = _exact_subject(
        binding["proof_actor_subject_id"], "proof_actor_subject_id"
    )
    try:
        source_paths = resolve_legacy_change_paths(
            user_data_root=paths["root"],
            source_owner_id=source_owner_id,
            expected_source_db_path=paths["source"],
        )
        assert_legacy_rollback_journal(source_paths["source"])
        source_identity = legacy_source_file_identity(source_paths["source"])
        con = open_exact_legacy_change_source(
            source_paths["source"],
            root=source_paths["root"],
            expected_resolved=source_paths["source"],
            expected_identity=source_identity,
        )
        try:
            con.execute("BEGIN")
            _authenticate_owner_snapshot(
                con,
                source_owner_id=source_paths["owner"].name,
                source_identity=source_identity,
                integrity_attestation=integrity_attestation,
            )
            receipt = _receipt_from_snapshot(
                con,
                source_owner_id=source_owner_id,
                page_id=page_id,
                source_identity=source_identity,
            )
            source_active = receipt.source_active
            quarantine_reason = None
            if source_active:
                try:
                    proof, file_sources, public_sources = _capture_bounded_fresh_page(
                        con,
                        paths=paths,
                        source_owner_id=source_owner_id,
                        actor_subject_id=proof_actor,
                        page_id=page_id,
                    )
                except Exception as exc:
                    quarantine_reason = _permanent_healing_quarantine_reason(exc)
                    if not quarantine_permanent or quarantine_reason is None:
                        raise
                    proof = None
                    file_sources = ()
                    public_sources = ()
                if (
                    quarantine_permanent
                    and quarantine_reason is None
                    and str(binding["binding_state"]) == "active"
                    and receipt.mask & CHANGE_PAGE
                    and proof is not None
                    and (
                        proof.revision < int(binding["source_revision"])
                        or proof.updated_at < int(binding["source_updated_at"])
                    )
                ):
                    # A normal page mutation never moves revision or update
                    # time backwards.  A fresh active row with a regressed
                    # proof can be a delete + same-ID recreation.  Equal
                    # revision is deliberately allowed because linked-page
                    # hydration can change this proof without touching the
                    # referencing page's own revision.  A future incarnation
                    # nonce is needed to distinguish that equality case.
                    quarantine_reason = "ambiguous"
                    proof = None
                    file_sources = ()
                    public_sources = ()
            else:
                proof = None
                file_sources = ()
                public_sources = ()
            con.rollback()
            # Revalidate the pathname and sidecar contract while the exact
            # source handle and directory chain remain pinned by ``con``.
            assert_legacy_rollback_journal(source_paths["source"])
            if legacy_source_file_identity(source_paths["source"]) != source_identity:
                raise WorkspaceProjectionError(
                    "legacy change source changed during capture"
                )
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except LegacyOwnerIntegrityAttestationMismatch:
        raise
    except (
        LegacyChangeContractError,
        LegacyChangeReadError,
        OSError,
        sqlite3.Error,
    ) as exc:
        raise WorkspaceProjectionError(
            "legacy healing source is unavailable"
        ) from exc

    if quarantine_reason is not None:
        return LegacyPageQuarantineCandidate(
            workspace_id=workspace_id,
            workspace_version=int(binding["workspace_version"]),
            owner_subject_id=source_owner_id,
            page_id=page_id,
            binding_version=generation,
            binding_state=str(binding["binding_state"]),
            bound_by_subject_id=str(binding["bound_by_subject_id"]),
            proof_actor_subject_id=proof_actor,
            source_epoch=receipt.fence.source_epoch,
            full_sweep_generation=receipt.fence.full_sweep_generation,
            source_device=_sqlite_int64_identity(receipt.fence.device),
            source_inode=_sqlite_int64_identity(receipt.fence.inode),
            source_link_count=receipt.fence.link_count,
            change_generation=receipt.generation,
            change_mask=receipt.mask,
            source_exists=receipt.source_exists,
            source_active=source_active,
            quarantine_reason=quarantine_reason,
        )
    return LegacyPageHealingCandidate(
        workspace_id=workspace_id,
        workspace_version=int(binding["workspace_version"]),
        owner_subject_id=source_owner_id,
        page_id=page_id,
        binding_version=generation,
        binding_state=str(binding["binding_state"]),
        bound_by_subject_id=str(binding["bound_by_subject_id"]),
        proof_actor_subject_id=proof_actor,
        source_epoch=receipt.fence.source_epoch,
        full_sweep_generation=receipt.fence.full_sweep_generation,
        source_device=_sqlite_int64_identity(receipt.fence.device),
        source_inode=_sqlite_int64_identity(receipt.fence.inode),
        source_link_count=receipt.fence.link_count,
        change_generation=receipt.generation,
        change_mask=receipt.mask,
        source_exists=receipt.source_exists,
        source_active=source_active,
        source_proof=proof,
        file_sources=file_sources,
        public_sources=public_sources,
    )


def capture_exact_owner_healing_candidate(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int,
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> LegacyPageHealingCandidate:
    """Capture a fresh bounded candidate without comparing the stale proof."""
    outcome = _capture_exact_owner_healing(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        workspace_id=workspace_id,
        source_page_id=source_page_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        expected_binding_version=expected_binding_version,
        source_quiesced_confirmed=source_quiesced_confirmed,
        integrity_attestation=integrity_attestation,
        quarantine_permanent=False,
    )
    if not isinstance(outcome, LegacyPageHealingCandidate):
        raise WorkspaceProjectionError("legacy healing outcome is invalid")
    return outcome


def capture_exact_owner_healing_outcome(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int,
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> LegacyPageHealingCandidate | LegacyPageQuarantineCandidate:
    """Capture one source-fenced healing or permanent-quarantine outcome."""
    return _capture_exact_owner_healing(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        workspace_id=workspace_id,
        source_page_id=source_page_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        expected_binding_version=expected_binding_version,
        source_quiesced_confirmed=source_quiesced_confirmed,
        integrity_attestation=integrity_attestation,
        quarantine_permanent=True,
    )


def revalidate_exact_owner_healing_candidate(
    candidate: LegacyPageHealingCandidate,
    *,
    user_data_root: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> bool:
    """Revalidate one candidate.

    Historical callers without an attestation retain complete catalog/source
    recapture semantics.  Offline healing supplies an owner attestation, so
    this callback re-reads only source state.  Catalog/workspace/binding CAS is
    owned by the store transaction and must not be mislabeled as source drift.
    """
    if not isinstance(candidate, LegacyPageHealingCandidate):
        return False
    if integrity_attestation is not None:
        if source_quiesced_confirmed is not True:
            raise WorkspaceProjectionError(
                "healing candidate requires source_quiesced_confirmed"
            )
        con = None
        try:
            source_paths = resolve_legacy_change_paths(
                user_data_root=user_data_root,
                source_owner_id=candidate.owner_subject_id,
                expected_source_db_path=expected_source_db_path,
            )
            assert_legacy_rollback_journal(source_paths["source"])
            source_identity = legacy_source_file_identity(source_paths["source"])
            con = open_exact_legacy_change_source(
                source_paths["source"],
                root=source_paths["root"],
                expected_resolved=source_paths["source"],
                expected_identity=source_identity,
            )
            con.execute("BEGIN")
            _authenticate_owner_snapshot(
                con,
                source_owner_id=source_paths["owner"].name,
                source_identity=source_identity,
                integrity_attestation=integrity_attestation,
            )
            receipt = _receipt_from_snapshot(
                con,
                source_owner_id=candidate.owner_subject_id,
                page_id=candidate.page_id,
                source_identity=source_identity,
            )
            if receipt.source_active:
                proof, file_sources, public_sources = _capture_bounded_fresh_page(
                    con,
                    paths={"root": source_paths["root"]},
                    source_owner_id=candidate.owner_subject_id,
                    actor_subject_id=candidate.proof_actor_subject_id,
                    page_id=candidate.page_id,
                )
            else:
                proof = None
                file_sources = ()
                public_sources = ()
            con.rollback()
            assert_legacy_rollback_journal(source_paths["source"])
            if legacy_source_file_identity(source_paths["source"]) != source_identity:
                return False
        except LegacyOwnerIntegrityAttestationMismatch:
            if con is not None:
                con.rollback()
            return False
        except (
            LegacyProjectionAmbiguousError,
            LegacyProjectionOversizeError,
            LegacyProjectionPermissionError,
            LegacyProjectionStaleError,
            LegacyProjectionSupersededError,
            UnsafeLegacyFileError,
        ):
            if con is not None:
                con.rollback()
            raise
        except (LegacyChangeContractError, LegacyChangeReadError, OSError, sqlite3.Error) as exc:
            if con is not None:
                con.rollback()
            raise WorkspaceProjectionError(
                "legacy healing source is unavailable"
            ) from exc
        finally:
            if con is not None:
                con.close()
        # ``expected_catalog_db_path`` intentionally remains untouched here.
        # It is retained only for API compatibility with historical callers.
        return (
            candidate.source_epoch == receipt.fence.source_epoch
            and candidate.full_sweep_generation
            == receipt.fence.full_sweep_generation
            and candidate.source_device
            == _sqlite_int64_identity(receipt.fence.device)
            and candidate.source_inode
            == _sqlite_int64_identity(receipt.fence.inode)
            and candidate.source_link_count == receipt.fence.link_count
            and candidate.change_generation == receipt.generation
            and candidate.change_mask == receipt.mask
            and candidate.source_exists is receipt.source_exists
            and candidate.source_active is receipt.source_active
            and candidate.source_proof == proof
            and candidate.file_sources == file_sources
            and candidate.public_sources == public_sources
        )
    try:
        current = capture_exact_owner_healing_candidate(
            user_data_root=user_data_root,
            source_owner_id=candidate.owner_subject_id,
            workspace_id=candidate.workspace_id,
            source_page_id=candidate.page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            expected_binding_version=candidate.binding_version,
            source_quiesced_confirmed=source_quiesced_confirmed,
            integrity_attestation=None,
        )
    except Exception:
        return False
    return current == candidate


def revalidate_exact_owner_healing_outcome(
    outcome: LegacyPageHealingCandidate | LegacyPageQuarantineCandidate,
    *,
    user_data_root: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> bool:
    """Revalidate an exact healing outcome without masking transient errors.

    Supplying an owner integrity attestation makes this a source-only check;
    the store remains responsible for catalog, binding, workspace, and IAM
    compare-and-swap checks.
    """
    if not isinstance(
        outcome, (LegacyPageHealingCandidate, LegacyPageQuarantineCandidate)
    ):
        return False
    if integrity_attestation is None:
        current = capture_exact_owner_healing_outcome(
            user_data_root=user_data_root,
            source_owner_id=outcome.owner_subject_id,
            workspace_id=outcome.workspace_id,
            source_page_id=outcome.page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            expected_binding_version=outcome.binding_version,
            source_quiesced_confirmed=source_quiesced_confirmed,
            integrity_attestation=None,
        )
        return current == outcome
    if isinstance(outcome, LegacyPageHealingCandidate):
        return revalidate_exact_owner_healing_candidate(
            outcome,
            user_data_root=user_data_root,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            source_quiesced_confirmed=source_quiesced_confirmed,
            integrity_attestation=integrity_attestation,
        )
    if source_quiesced_confirmed is not True:
        raise WorkspaceProjectionError(
            "healing outcome requires source_quiesced_confirmed"
        )

    con = None
    try:
        source_paths = resolve_legacy_change_paths(
            user_data_root=user_data_root,
            source_owner_id=outcome.owner_subject_id,
            expected_source_db_path=expected_source_db_path,
        )
        assert_legacy_rollback_journal(source_paths["source"])
        source_identity = legacy_source_file_identity(source_paths["source"])
        con = open_exact_legacy_change_source(
            source_paths["source"],
            root=source_paths["root"],
            expected_resolved=source_paths["source"],
            expected_identity=source_identity,
        )
        con.execute("BEGIN")
        _authenticate_owner_snapshot(
            con,
            source_owner_id=source_paths["owner"].name,
            source_identity=source_identity,
            integrity_attestation=integrity_attestation,
        )
        receipt = _receipt_from_snapshot(
            con,
            source_owner_id=outcome.owner_subject_id,
            page_id=outcome.page_id,
            source_identity=source_identity,
        )
        current_reason = None
        if receipt.source_active:
            try:
                _capture_bounded_fresh_page(
                    con,
                    paths={"root": source_paths["root"]},
                    source_owner_id=outcome.owner_subject_id,
                    actor_subject_id=outcome.proof_actor_subject_id,
                    page_id=outcome.page_id,
                )
            except Exception as exc:
                current_reason = _permanent_healing_quarantine_reason(exc)
                if current_reason is None:
                    raise
            if current_reason is None and outcome.quarantine_reason == "ambiguous":
                # Capture can classify a non-monotone page incarnation even
                # when page materialization itself succeeds.  Exact receipt
                # and owner-attestation equality below ensure this is the same
                # source observation, not a blanket acceptance of ambiguity.
                current_reason = "ambiguous"
        con.rollback()
        assert_legacy_rollback_journal(source_paths["source"])
        if legacy_source_file_identity(source_paths["source"]) != source_identity:
            return False
    except LegacyOwnerIntegrityAttestationMismatch:
        if con is not None:
            con.rollback()
        return False
    except WorkspaceProjectionError:
        if con is not None:
            con.rollback()
        raise
    except (LegacyChangeContractError, LegacyChangeReadError, OSError, sqlite3.Error) as exc:
        if con is not None:
            con.rollback()
        raise WorkspaceProjectionError(
            "legacy healing source is unavailable"
        ) from exc
    finally:
        if con is not None:
            con.close()

    if current_reason is None:
        return False
    current = LegacyPageQuarantineCandidate(
        workspace_id=outcome.workspace_id,
        workspace_version=outcome.workspace_version,
        owner_subject_id=outcome.owner_subject_id,
        page_id=outcome.page_id,
        binding_version=outcome.binding_version,
        binding_state=outcome.binding_state,
        bound_by_subject_id=outcome.bound_by_subject_id,
        proof_actor_subject_id=outcome.proof_actor_subject_id,
        source_epoch=receipt.fence.source_epoch,
        full_sweep_generation=receipt.fence.full_sweep_generation,
        source_device=_sqlite_int64_identity(receipt.fence.device),
        source_inode=_sqlite_int64_identity(receipt.fence.inode),
        source_link_count=receipt.fence.link_count,
        change_generation=receipt.generation,
        change_mask=receipt.mask,
        source_exists=receipt.source_exists,
        source_active=receipt.source_active,
        quarantine_reason=current_reason,
    )
    return current == outcome


def capture_exact_owner_projection(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    actor_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int | None = None,
    _capture_database_fingerprint: bool = True,
) -> dict[str, Any]:
    """Capture one sanitized projection in one legacy read snapshot."""
    if not isinstance(_capture_database_fingerprint, bool):
        raise WorkspaceProjectionError("invalid projection capture mode")
    page_id = str(source_page_id or "")
    if not page_id or page_id != page_id.strip() or len(page_id) > _PAGE_ID_MAX:
        raise WorkspaceProjectionError("valid source_page_id required")
    expected_generation = _optional_binding_version(expected_binding_version)
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    binding = _catalog_binding(
        paths["catalog"],
        workspace_id=workspace_id,
        owner=source_owner_id,
        page_id=page_id,
        expected_binding_version=expected_generation,
    )
    con = _open_read_only(paths["source"])
    try:
        con.execute("BEGIN")
        permission_query_budget = {"remaining": _MAX_SHADOW_PERMISSION_QUERIES}
        if not _capture_database_fingerprint:
            _assert_bounded_shadow_page(con, page_id)
        page = con.execute(
            "SELECT * FROM pages WHERE id=? AND owner_user_id=? AND archived=0",
            (page_id, source_owner_id),
        ).fetchone()
        if not page:
            raise WorkspaceProjectionError("legacy page is unavailable")
        bounded_role_resolver = lambda role_con, role_page, role_actor, role_cache: (
            _bounded_inherited_share_role(
                role_con,
                role_page,
                role_actor,
                role_cache,
                permission_query_budget,
            )
        )
        role = "owner" if actor_subject_id == source_owner_id else (
            _inherited_share_role(con, page_id, actor_subject_id)
            if _capture_database_fingerprint
            else bounded_role_resolver(con, page_id, actor_subject_id, {})
        )
        if role not in {"owner", "edit", "comment", "view"}:
            raise LegacyProjectionPermissionError("legacy page is unavailable")
        proof = _page_proof(
            con,
            actor=actor_subject_id,
            owner=source_owner_id,
            page=page,
            role=role,
            reference_limit=(
                None
                if _capture_database_fingerprint
                else _MAX_SHADOW_LINKED_REFERENCES
            ),
            linked_metadata_byte_limit=(
                None
                if _capture_database_fingerprint
                else _MAX_SHADOW_LINKED_METADATA_BYTES
            ),
            role_resolver=(
                None if _capture_database_fingerprint else bounded_role_resolver
            ),
        )
        if (
            proof.revision != binding["source_revision"]
            or proof.updated_at != binding["source_updated_at"]
            or proof.content_sha256 != binding["source_sha256"]
        ):
            raise LegacyProjectionStaleError("legacy page binding is stale")
        file_rows = con.execute(
            """
            SELECT id,rel_path FROM files
            WHERE page_id=? AND owner_user_id=? ORDER BY id
            """,
            (page_id, source_owner_id),
        ).fetchall()
        file_ids: list[str] = []
        for row in file_rows:
            file_id = str(row["id"] or "")
            if not _FILE_ID_RE.fullmatch(file_id):
                raise WorkspaceProjectionError("legacy attachment ID is invalid")
            _safe_regular_file(
                user_data_root=paths["root"],
                source_owner_id=source_owner_id,
                rel_path=str(row["rel_path"] or ""),
            )
            file_ids.append(file_id)
        if len(file_ids) != len(set(file_ids)):
            raise LegacyProjectionAmbiguousError("legacy locator set is ambiguous")

        public_rows = con.execute(
            """
            SELECT token,allow_duplicate FROM public_links
            WHERE page_id=? AND owner_user_id=? AND revoked_at IS NULL
            """,
            (page_id, source_owner_id),
        ).fetchall()
        if len(public_rows) > 1:
            raise LegacyProjectionAmbiguousError("legacy locator set is ambiguous")
        public_sources: tuple[LegacyPublicSourceLocator, ...] = ()
        if public_rows:
            raw_token = str(public_rows[0]["token"] or "")
            if not raw_token:
                raise WorkspaceProjectionError("legacy public locator is invalid")
            digest = "sha256:" + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            raw_token = ""  # do not retain or return the bearer credential
            public_sources = (
                LegacyPublicSourceLocator(
                    digest,
                    bool(public_rows[0]["allow_duplicate"]),
                ),
            )
        source_fingerprint = (
            logical_database_fingerprint(con)
            if _capture_database_fingerprint
            else ""
        )
        con.rollback()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    projection = LegacyPageLocatorProjection(
        actor_subject_id=actor_subject_id,
        owner_subject_id=source_owner_id,
        page_id=page_id,
        binding_version=binding["binding_version"],
        file_sources=tuple(LegacyFileSourceLocator(item) for item in sorted(file_ids)),
        public_sources=public_sources,
    )
    return {
        "projection": projection,
        "source_database_fingerprint": source_fingerprint,
        "workspace_version": binding["workspace_version"],
    }


def revalidate_exact_owner_projection(
    projection: LegacyPageLocatorProjection,
    *,
    user_data_root: str | os.PathLike[str],
    workspace_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_source_database_fingerprint: str,
    expected_binding_version: int | None = None,
) -> bool:
    """Re-capture the exact owner/page and compare the full sanitized value."""
    expected_generation = _optional_binding_version(expected_binding_version)
    try:
        current = capture_exact_owner_projection(
            user_data_root=user_data_root,
            source_owner_id=projection.owner_subject_id,
            actor_subject_id=projection.actor_subject_id,
            workspace_id=workspace_id,
            source_page_id=projection.page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            expected_binding_version=(
                expected_generation
                if expected_generation is not None
                else projection.binding_version
            ),
        )
    except Exception:
        return False
    return (
        current["projection"] == projection
        and current["source_database_fingerprint"]
        == expected_source_database_fingerprint
    )


def project_exact_owner_page(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    actor_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    operation_id: str,
    source_quiesced_confirmed: bool,
    expected_binding_version: int | None = None,
) -> dict[str, Any]:
    """Synchronously project one page; no outbox delivery is claimed here."""
    if source_quiesced_confirmed is not True:
        raise WorkspaceProjectionError(
            "synchronous projection requires source_quiesced_confirmed"
        )
    expected_generation = _optional_binding_version(expected_binding_version)
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    captured = capture_exact_owner_projection(
        user_data_root=paths["root"],
        source_owner_id=source_owner_id,
        actor_subject_id=actor_subject_id,
        workspace_id=workspace_id,
        source_page_id=source_page_id,
        expected_source_db_path=paths["source"],
        expected_catalog_db_path=paths["catalog"],
        expected_binding_version=expected_generation,
    )
    projection = captured["projection"]
    store = WorkspaceStore(paths["catalog"])
    return store.replace_page_locator_projection(
        actor_subject_id,
        workspace_id,
        projection,
        projection_revalidator=lambda candidate: revalidate_exact_owner_projection(
            candidate,
            user_data_root=paths["root"],
            workspace_id=workspace_id,
            expected_source_db_path=paths["source"],
            expected_catalog_db_path=paths["catalog"],
            expected_source_database_fingerprint=captured[
                "source_database_fingerprint"
            ],
            expected_binding_version=(
                expected_generation
                if expected_generation is not None
                else projection.binding_version
            ),
        ),
        base_version=int(captured["workspace_version"]),
        operation_id=operation_id,
    )


def empty_shadow_counts() -> dict[str, int]:
    """Return the identifier-free stable reconciliation envelope."""
    return {state: 0 for state in _SHADOW_STATES}


def shadow_reconcile_exact_owner_page(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    actor_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int | None = None,
) -> dict[str, int]:
    """Compare one exact source to the catalog and return counters only."""
    counts = empty_shadow_counts()
    try:
        captured = capture_exact_owner_projection(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            actor_subject_id=actor_subject_id,
            workspace_id=workspace_id,
            source_page_id=source_page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            expected_binding_version=expected_binding_version,
            _capture_database_fingerprint=False,
        )
        projection = captured["projection"]
        con = _open_read_only(Path(expected_catalog_db_path).resolve(strict=True))
        try:
            summary = con.execute(
                """
                SELECT projection_sha256,file_source_count,public_source_count
                FROM workspace_page_source_projections
                WHERE workspace_id=? AND source_owner_subject_id=?
                  AND source_page_id=? AND binding_version=?
                """,
                (
                    workspace_id,
                    source_owner_id,
                    source_page_id,
                    projection.binding_version,
                ),
            ).fetchone()
            if not summary:
                counts["safe_miss"] = 1
                return counts
            files = tuple(
                str(row[0])
                for row in con.execute(
                    """
                    SELECT source_file_id FROM workspace_file_sources
                    WHERE workspace_id=? AND source_owner_subject_id=?
                      AND source_page_id=? AND binding_version=?
                    ORDER BY source_file_id
                    """,
                    (
                        workspace_id,
                        source_owner_id,
                        source_page_id,
                        projection.binding_version,
                    ),
                ).fetchall()
            )
            public = tuple(
                (str(row[0]), bool(row[1]))
                for row in con.execute(
                    """
                    SELECT token_sha256,allow_duplicate
                    FROM workspace_public_sources
                    WHERE workspace_id=? AND source_owner_subject_id=?
                      AND source_page_id=? AND binding_version=?
                    ORDER BY token_sha256
                    """,
                    (
                        workspace_id,
                        source_owner_id,
                        source_page_id,
                        projection.binding_version,
                    ),
                ).fetchall()
            )
        finally:
            con.close()
        expected_files = tuple(item.source_file_id for item in projection.file_sources)
        expected_public = tuple(
            (item.token_sha256, item.allow_duplicate)
            for item in projection.public_sources
        )
        expected_commitment = _page_projection_sha256(
            workspace_id=workspace_id,
            source_owner_subject_id=source_owner_id,
            source_page_id=source_page_id,
            binding_version=projection.binding_version,
            file_sources=expected_files,
            public_sources=expected_public,
        )
        matches = (
            files == expected_files
            and public == expected_public
            and int(summary["file_source_count"]) == len(expected_files)
            and int(summary["public_source_count"]) == len(expected_public)
            and str(summary["projection_sha256"]) == expected_commitment
        )
        counts["match" if matches else "stale"] = 1
    except LegacyProjectionSupersededError:
        counts["superseded"] = 1
    except LegacyProjectionOversizeError:
        counts["oversize"] = 1
    except LegacyInventoryBoundsExceeded:
        counts["oversize"] = 1
    except UnsafeLegacyFileError:
        counts["unsafe_path"] = 1
    except LegacyProjectionPermissionError:
        counts["permission_mismatch"] = 1
    except LegacyProjectionAmbiguousError:
        counts["ambiguous"] = 1
    except LegacyProjectionStaleError:
        counts["stale"] = 1
    except WorkspaceProjectionError:
        counts["catalog_unavailable"] = 1
    except (OSError, sqlite3.Error, ValueError):
        counts["catalog_unavailable"] = 1
    return counts
