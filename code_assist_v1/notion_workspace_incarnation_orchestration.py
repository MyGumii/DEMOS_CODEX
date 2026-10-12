"""Offline-only capture foundation for exact legacy-v2 incarnation review.

This module is deliberately not registered with application startup, routes,
or a CLI.  It captures the legacy-v2 receipt, page proof, and complete
sanitized locator set from one pinned immutable SQLite snapshot beneath an
explicit rehearsal sentinel.  Both attestation and full-rebind orchestrators
resolve an authorized exact ledger replay before any sentinel or source path
access.  Nothing in this module is a runtime worker or resolver.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from typing import Any

from code_assist_v1.notion_legacy_change_contract import (
    LegacyChangeContractError,
    assert_legacy_rollback_journal,
    legacy_source_file_identity,
    open_exact_legacy_change_source,
    resolve_legacy_change_paths,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyChangeReadError,
    LegacyPageIncarnationReceipt,
    _positive_generation,
    _receipt_from_snapshot,
    inspect_legacy_incarnation_source_contract,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    LegacyIncarnationContractError,
    require_legacy_page_incarnation_id,
)
from code_assist_v1.notion_workspace_inventory import (
    WorkspaceInventoryError,
    resolve_inventory_paths,
)
from code_assist_v1.notion_workspace_projection import (
    LegacyProjectionStaleError,
    WorkspaceProjectionError,
    _capture_bounded_fresh_page,
    _catalog_binding,
    _open_read_only,
    _sqlite_int64_identity,
)
from code_assist_v1.notion_workspace_rehearsal import (
    WorkspaceRehearsalRefused,
    _rehearsal_root,
    _resolved_existing,
)
from code_assist_v1.notion_workspace_store import (
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    WorkspaceStore,
)


OFFLINE_INCARNATION_CAPTURE_CONTRACT = (
    "np2a_1309_offline_incarnation_capture_v1"
)
OFFLINE_INCARNATION_ATTESTATION_REPORT_CONTRACT = (
    "np2a_1309_offline_incarnation_attestation_v1"
)
OFFLINE_INCARNATION_REBIND_REPORT_CONTRACT = (
    "np2a_1309_offline_incarnation_rebind_v1"
)


class WorkspaceIncarnationOrchestrationRefused(Exception):
    """Sanitized fail-closed offline orchestration refusal."""


@dataclass(frozen=True)
class OfflineIncarnationCapture:
    """Internal exact-snapshot handoff; never serialize as an operator report."""

    source_proof: LegacyPageSourceProof
    source_incarnation: LegacyPageIncarnationEvidence
    locator_projection: LegacyPageLocatorProjection | None
    binding_state: str
    binding_version: int
    workspace_version: int


@dataclass(frozen=True)
class OfflineIncarnationRebindCapture:
    """Exact owner-canonical source tuple for one quarantined generation."""

    source_proof: LegacyPageSourceProof
    source_incarnation: LegacyPageIncarnationEvidence
    locator_projection: LegacyPageLocatorProjection
    quarantined_binding_version: int
    rebound_binding_version: int
    workspace_version: int


def _page_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise WorkspaceIncarnationOrchestrationRefused(
            "valid source page identity required"
        )
    if len(value) > 160:
        raise WorkspaceIncarnationOrchestrationRefused(
            "valid source page identity required"
        )
    return value


def _receipt_from_incarnation_snapshot(
    con: sqlite3.Connection,
    *,
    source_owner_id: str,
    source_page_id: str,
    source_identity: tuple[int, int, int, int, int],
) -> LegacyPageIncarnationReceipt:
    inspected = inspect_legacy_incarnation_source_contract(
        con, verify_integrity=True
    )
    if inspected.get("installed") is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy incarnation tracking is unavailable"
        )
    v1_receipt = _receipt_from_snapshot(
        con,
        source_owner_id=source_owner_id,
        page_id=source_page_id,
        source_identity=source_identity,
    )
    row = con.execute(
        """
        SELECT incarnation_generation,incarnation_id,retired_at
        FROM notion_page_incarnation_history
        WHERE page_id=? ORDER BY incarnation_generation DESC LIMIT 1
        """,
        (source_page_id,),
    ).fetchone()
    if row is None:
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy page incarnation receipt is unavailable"
        )
    incarnation_generation = _positive_generation(
        row["incarnation_generation"], field="incarnation_generation"
    )
    incarnation_id = require_legacy_page_incarnation_id(row["incarnation_id"])
    retired_at = row["retired_at"]
    if retired_at is not None and (
        type(retired_at) is not int or retired_at < 0
    ):
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy incarnation history is invalid"
        )
    retired = retired_at is not None
    if retired is v1_receipt.source_exists:
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy incarnation history is invalid"
        )
    return LegacyPageIncarnationReceipt(
        source_owner_id=source_owner_id,
        page_id=source_page_id,
        incarnation_id=incarnation_id,
        incarnation_generation=incarnation_generation,
        incarnation_retired=retired,
        generation=v1_receipt.generation,
        mask=v1_receipt.mask,
        source_exists=v1_receipt.source_exists,
        source_active=v1_receipt.source_active,
        fence=v1_receipt.fence,
    )


def _capture_one_source_snapshot(
    *,
    paths: dict[str, Path],
    source_owner_id: str,
    source_actor_subject_id: str,
    source_page_id: str,
    workspace_id: str,
    binding_state: str,
    binding_version: int,
) -> tuple[
    LegacyPageSourceProof,
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection | None,
]:
    """Capture receipt, proof and locators from exactly one source connection."""
    legacy_paths = resolve_legacy_change_paths(
        user_data_root=paths["root"],
        source_owner_id=source_owner_id,
        expected_source_db_path=paths["source"],
    )
    assert_legacy_rollback_journal(legacy_paths["source"])
    source_identity = legacy_source_file_identity(legacy_paths["source"])
    con = open_exact_legacy_change_source(
        legacy_paths["source"],
        root=legacy_paths["root"],
        expected_resolved=legacy_paths["source"],
        expected_identity=source_identity,
    )
    try:
        con.execute("BEGIN")
        receipt = _receipt_from_incarnation_snapshot(
            con,
            source_owner_id=source_owner_id,
            source_page_id=source_page_id,
            source_identity=source_identity,
        )
        if (
            receipt.incarnation_retired
            or not receipt.source_exists
            or not receipt.source_active
        ):
            raise WorkspaceIncarnationOrchestrationRefused(
                "only an active legacy incarnation can be reviewed"
            )
        proof, file_sources, public_sources = _capture_bounded_fresh_page(
            con,
            paths=paths,
            source_owner_id=source_owner_id,
            actor_subject_id=source_actor_subject_id,
            page_id=source_page_id,
        )
        projection = (
            LegacyPageLocatorProjection(
                actor_subject_id=source_actor_subject_id,
                owner_subject_id=source_owner_id,
                page_id=source_page_id,
                binding_version=binding_version,
                file_sources=file_sources,
                public_sources=public_sources,
            )
            if binding_state == "active"
            else None
        )
        evidence = LegacyPageIncarnationEvidence(
            owner_subject_id=source_owner_id,
            page_id=source_page_id,
            incarnation_id=receipt.incarnation_id,
            incarnation_generation=receipt.incarnation_generation,
            incarnation_retired=False,
            source_epoch=receipt.fence.source_epoch,
            full_sweep_generation=receipt.fence.full_sweep_generation,
            source_device=_sqlite_int64_identity(receipt.fence.device),
            source_inode=_sqlite_int64_identity(receipt.fence.inode),
            source_link_count=receipt.fence.link_count,
            change_generation=receipt.generation,
            change_mask=receipt.mask,
            source_exists=True,
            source_active=True,
            receipt_sha256=receipt.receipt_sha256,
        )
        con.rollback()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
    assert_legacy_rollback_journal(legacy_paths["source"])
    if legacy_source_file_identity(legacy_paths["source"]) != source_identity:
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy source changed during incarnation capture"
        )
    return proof, evidence, projection


def capture_isolated_page_incarnation_candidate(
    store: WorkspaceStore,
    *,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int,
    source_quiesced_confirmed: bool,
) -> OfflineIncarnationCapture:
    """Capture one store-ready candidate under the offline sentinel.

    Quiescence and the sentinel are checked before any user-data, catalog, or
    source path is converted, resolved, statted, or opened.
    """
    if source_quiesced_confirmed is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation capture requires source quiescence"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspaceIncarnationOrchestrationRefused(
            "exact workspace store is required"
        )
    root = _rehearsal_root(rehearsal_root)
    _resolved_existing(
        user_data_root, root=root, kind="data root", allow_root=True
    )
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    catalog_path = _resolved_existing(
        paths["catalog"], root=root, kind="catalog"
    )
    source_path = _resolved_existing(
        paths["source"], root=root, kind="legacy source"
    )
    try:
        if Path(store.db_path).resolve(strict=True) != catalog_path:
            raise WorkspaceIncarnationOrchestrationRefused(
                "workspace store does not match the rehearsal catalog"
            )
    except OSError as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "workspace catalog is unavailable"
        ) from exc
    page_id = _page_id(source_page_id)
    binding = _catalog_binding(
        catalog_path,
        workspace_id=workspace_id,
        owner=source_owner_id,
        page_id=page_id,
        expected_binding_version=expected_binding_version,
        allowed_states=("pending", "active"),
    )
    source_actor = str(binding["proof_actor_subject_id"])
    proof, evidence, projection = _capture_one_source_snapshot(
        paths={**paths, "source": source_path, "catalog": catalog_path},
        source_owner_id=source_owner_id,
        source_actor_subject_id=source_actor,
        source_page_id=page_id,
        workspace_id=workspace_id,
        binding_state=str(binding["binding_state"]),
        binding_version=int(binding["binding_version"]),
    )
    stored_proof = (
        int(binding["source_revision"]),
        int(binding["source_updated_at"]),
        str(binding["source_sha256"]),
    )
    captured_proof = (proof.revision, proof.updated_at, proof.content_sha256)
    if stored_proof != captured_proof:
        raise WorkspaceIncarnationOrchestrationRefused(
            "legacy page source proof is stale"
        )
    return OfflineIncarnationCapture(
        source_proof=proof,
        source_incarnation=evidence,
        locator_projection=projection,
        binding_state=str(binding["binding_state"]),
        binding_version=int(binding["binding_version"]),
        workspace_version=int(binding["workspace_version"]),
    )


def capture_isolated_page_incarnation_rebind_candidate(
    store: WorkspaceStore,
    *,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_quarantined_binding_version: int,
    source_quiesced_confirmed: bool,
) -> OfflineIncarnationRebindCapture:
    """Capture an owner-canonical replacement for one exact quarantine row."""
    if source_quiesced_confirmed is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation rebind capture requires source quiescence"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspaceIncarnationOrchestrationRefused(
            "exact workspace store is required"
        )
    if (
        isinstance(expected_quarantined_binding_version, bool)
        or not isinstance(expected_quarantined_binding_version, int)
        or expected_quarantined_binding_version < 1
        or expected_quarantined_binding_version >= (1 << 63) - 1
    ):
        raise WorkspaceIncarnationOrchestrationRefused(
            "valid quarantined binding generation required"
        )

    root = _rehearsal_root(rehearsal_root)
    _resolved_existing(
        user_data_root, root=root, kind="data root", allow_root=True
    )
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    catalog_path = _resolved_existing(paths["catalog"], root=root, kind="catalog")
    source_path = _resolved_existing(
        paths["source"], root=root, kind="legacy source"
    )
    try:
        if Path(store.db_path).resolve(strict=True) != catalog_path:
            raise WorkspaceIncarnationOrchestrationRefused(
                "workspace store does not match the rehearsal catalog"
            )
    except OSError as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "workspace catalog is unavailable"
        ) from exc

    page_id = _page_id(source_page_id)
    catalog = _open_read_only(catalog_path)
    try:
        binding = catalog.execute(
            """
            SELECT state,row_version FROM workspace_page_sources
            WHERE workspace_id=? AND source_owner_subject_id=?
              AND source_page_id=?
            """,
            (workspace_id, source_owner_id, page_id),
        ).fetchone()
        workspace = catalog.execute(
            "SELECT row_version FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
        if (
            binding is None
            or str(binding["state"]) != "quarantined"
            or int(binding["row_version"])
            != expected_quarantined_binding_version
            or workspace is None
        ):
            raise WorkspaceIncarnationOrchestrationRefused(
                "page source quarantined binding is stale"
            )
        workspace_version = int(workspace["row_version"])
    except sqlite3.Error as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "workspace catalog is unavailable"
        ) from exc
    finally:
        catalog.close()

    rebound_version = expected_quarantined_binding_version + 1
    proof, evidence, projection = _capture_one_source_snapshot(
        paths={**paths, "source": source_path, "catalog": catalog_path},
        source_owner_id=source_owner_id,
        source_actor_subject_id=source_owner_id,
        source_page_id=page_id,
        workspace_id=workspace_id,
        binding_state="active",
        binding_version=rebound_version,
    )
    if (
        proof.actor_subject_id != source_owner_id
        or proof.owner_subject_id != source_owner_id
        or projection is None
    ):
        raise WorkspaceIncarnationOrchestrationRefused(
            "owner-canonical incarnation rebind capture is invalid"
        )
    return OfflineIncarnationRebindCapture(
        source_proof=proof,
        source_incarnation=evidence,
        locator_projection=projection,
        quarantined_binding_version=expected_quarantined_binding_version,
        rebound_binding_version=rebound_version,
        workspace_version=workspace_version,
    )


def revalidate_isolated_page_incarnation_candidate(
    candidate: OfflineIncarnationCapture,
    store: WorkspaceStore,
    *,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
) -> bool:
    """Re-capture only source evidence and compare the complete tuple.

    Catalog CAS belongs to the store immediately after this callback.  This
    function therefore authenticates the sentinel and exact paths/store
    identity but deliberately does not reread binding or workspace state.
    """
    if not isinstance(candidate, OfflineIncarnationCapture):
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation capture required"
        )
    if source_quiesced_confirmed is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation revalidation requires source quiescence"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspaceIncarnationOrchestrationRefused(
            "exact workspace store is required"
        )
    root = _rehearsal_root(rehearsal_root)
    _resolved_existing(
        user_data_root, root=root, kind="data root", allow_root=True
    )
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    catalog_path = _resolved_existing(
        paths["catalog"], root=root, kind="catalog"
    )
    source_path = _resolved_existing(
        paths["source"], root=root, kind="legacy source"
    )
    try:
        if Path(store.db_path).resolve(strict=True) != catalog_path:
            raise WorkspaceIncarnationOrchestrationRefused(
                "workspace store does not match the rehearsal catalog"
            )
    except OSError as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "workspace catalog is unavailable"
        ) from exc
    proof, evidence, projection = _capture_one_source_snapshot(
        paths={**paths, "source": source_path, "catalog": catalog_path},
        source_owner_id=source_owner_id,
        source_actor_subject_id=candidate.source_proof.actor_subject_id,
        source_page_id=_page_id(source_page_id),
        workspace_id=workspace_id,
        binding_state=candidate.binding_state,
        binding_version=candidate.binding_version,
    )
    return (
        proof == candidate.source_proof
        and evidence == candidate.source_incarnation
        and projection == candidate.locator_projection
    )


def revalidate_isolated_page_incarnation_rebind_candidate(
    candidate: OfflineIncarnationRebindCapture,
    store: WorkspaceStore,
    *,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
) -> bool:
    """Revalidate only the exact owner source tuple for a pending rebind."""
    if not isinstance(candidate, OfflineIncarnationRebindCapture):
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation rebind capture required"
        )
    comparable = OfflineIncarnationCapture(
        source_proof=candidate.source_proof,
        source_incarnation=candidate.source_incarnation,
        locator_projection=candidate.locator_projection,
        binding_state="active",
        binding_version=candidate.rebound_binding_version,
        workspace_version=candidate.workspace_version,
    )
    return revalidate_isolated_page_incarnation_candidate(
        comparable,
        store,
        workspace_id=workspace_id,
        rehearsal_root=rehearsal_root,
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        source_page_id=source_page_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        source_quiesced_confirmed=source_quiesced_confirmed,
    )


def _identifier_free_attestation_report(
    result: dict[str, Any], *, replayed: bool
) -> dict[str, Any]:
    """Reduce a committed catalog result to aggregate, identifier-free facts."""
    try:
        binding = result["binding"]
        state = str(binding["state"])
        if state not in {"pending", "active"}:
            raise ValueError
        projection = result.get("projection")
        if projection is None:
            projection_report = {
                "present": False,
                "changed": False,
                "file_source_count": 0,
                "public_source_count": 0,
            }
        else:
            projection_report = {
                "present": True,
                "changed": bool(projection["changed"]),
                "file_source_count": int(projection["file_source_count"]),
                "public_source_count": int(projection["public_source_count"]),
            }
            if (
                projection_report["file_source_count"] < 0
                or projection_report["public_source_count"] < 0
            ):
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "incarnation attestation result is invalid"
        ) from exc
    return {
        "contract": OFFLINE_INCARNATION_ATTESTATION_REPORT_CONTRACT,
        "offline_only": True,
        "committed": True,
        "ledger_replay": replayed,
        "source_io_bypassed": replayed,
        "source_snapshot_revalidated": not replayed,
        "binding_state": state,
        "locator_projection": projection_report,
    }


def _identifier_free_rebind_report(
    result: dict[str, Any], *, replayed: bool
) -> dict[str, Any]:
    try:
        previous = result["previous_binding"]
        if (
            not isinstance(previous, dict)
            or previous.get("state") != "quarantined"
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceIncarnationOrchestrationRefused(
            "incarnation rebind result is invalid"
        ) from exc
    report = _identifier_free_attestation_report(result, replayed=replayed)
    return {
        **report,
        "contract": OFFLINE_INCARNATION_REBIND_REPORT_CONTRACT,
        "previous_binding_state": "quarantined",
    }


def run_isolated_page_incarnation_attestation(
    store: WorkspaceStore,
    *,
    reviewer_subject_id: str,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_binding_version: int,
    operation_id: str,
    source_quiesced_confirmed: bool,
) -> dict[str, Any]:
    """Attest one active/pending binding with response-loss-safe ordering.

    The catalog replay lookup is intentionally the first operation.  It both
    authenticates the current reviewer and returns an exact committed result
    without consulting the sentinel, resolver, filesystem, or legacy source.
    New effects then require quiescence and a second explicit admin gate before
    any source-related path access.
    """
    replay = store.get_page_source_incarnation_attestation_replay(
        reviewer_subject_id,
        workspace_id,
        source_owner_id,
        source_page_id,
        expected_binding_version=expected_binding_version,
        operation_id=operation_id,
    )
    if replay is not None:
        return _identifier_free_attestation_report(replay, replayed=True)

    if source_quiesced_confirmed is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation attestation requires source quiescence"
        )
    # The replay helper already authenticates unknown operations, but retain an
    # explicit pre-source admin gate as defense in depth and as the public
    # no-source-oracle contract of this offline wrapper.
    store.incarnation_attestation_readiness(reviewer_subject_id, workspace_id)

    candidate = capture_isolated_page_incarnation_candidate(
        store,
        workspace_id=workspace_id,
        rehearsal_root=rehearsal_root,
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        source_page_id=source_page_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        expected_binding_version=expected_binding_version,
        source_quiesced_confirmed=True,
    )

    def source_revalidator(
        proof: LegacyPageSourceProof,
        evidence: LegacyPageIncarnationEvidence,
        projection: LegacyPageLocatorProjection | None,
    ) -> bool:
        if (
            proof != candidate.source_proof
            or evidence != candidate.source_incarnation
            or projection != candidate.locator_projection
        ):
            return False
        return revalidate_isolated_page_incarnation_candidate(
            candidate,
            store,
            workspace_id=workspace_id,
            rehearsal_root=rehearsal_root,
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            source_page_id=source_page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            source_quiesced_confirmed=True,
        )

    result = store.attest_page_source_incarnation(
        reviewer_subject_id,
        workspace_id,
        candidate.source_proof,
        candidate.source_incarnation,
        source_revalidator=source_revalidator,
        expected_binding_version=candidate.binding_version,
        base_version=candidate.workspace_version,
        operation_id=operation_id,
        source_quiesced_confirmed=True,
        locator_projection=candidate.locator_projection,
    )
    return _identifier_free_attestation_report(result, replayed=False)


def run_isolated_page_incarnation_rebind(
    store: WorkspaceStore,
    *,
    reviewer_subject_id: str,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    source_page_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_quarantined_binding_version: int,
    operation_id: str,
    source_quiesced_confirmed: bool,
) -> dict[str, Any]:
    """Atomically rebind one quarantined source under the offline sentinel."""
    replay = store.get_page_source_incarnation_rebind_replay(
        reviewer_subject_id,
        workspace_id,
        source_owner_id,
        source_page_id,
        expected_quarantined_binding_version=expected_quarantined_binding_version,
        operation_id=operation_id,
    )
    if replay is not None:
        return _identifier_free_rebind_report(replay, replayed=True)

    if source_quiesced_confirmed is not True:
        raise WorkspaceIncarnationOrchestrationRefused(
            "offline incarnation rebind requires source quiescence"
        )
    # Unknown operations are already admin-authenticated by the replay probe.
    # Repeat an explicit catalog-only gate before any sentinel or source path.
    store.incarnation_attestation_readiness(reviewer_subject_id, workspace_id)

    candidate = capture_isolated_page_incarnation_rebind_candidate(
        store,
        workspace_id=workspace_id,
        rehearsal_root=rehearsal_root,
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        source_page_id=source_page_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        expected_quarantined_binding_version=expected_quarantined_binding_version,
        source_quiesced_confirmed=True,
    )

    def source_revalidator(
        proof: LegacyPageSourceProof,
        evidence: LegacyPageIncarnationEvidence,
        projection: LegacyPageLocatorProjection,
    ) -> bool:
        if (
            proof != candidate.source_proof
            or evidence != candidate.source_incarnation
            or projection != candidate.locator_projection
        ):
            return False
        return revalidate_isolated_page_incarnation_rebind_candidate(
            candidate,
            store,
            workspace_id=workspace_id,
            rehearsal_root=rehearsal_root,
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            source_page_id=source_page_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
            source_quiesced_confirmed=True,
        )

    result = store.rebind_quarantined_page_source_incarnation(
        reviewer_subject_id,
        workspace_id,
        candidate.source_proof,
        candidate.source_incarnation,
        candidate.locator_projection,
        source_revalidator=source_revalidator,
        expected_quarantined_binding_version=(
            candidate.quarantined_binding_version
        ),
        base_version=candidate.workspace_version,
        operation_id=operation_id,
        source_quiesced_confirmed=True,
    )
    return _identifier_free_rebind_report(result, replayed=False)


__all__ = [
    "OFFLINE_INCARNATION_CAPTURE_CONTRACT",
    "OFFLINE_INCARNATION_ATTESTATION_REPORT_CONTRACT",
    "OFFLINE_INCARNATION_REBIND_REPORT_CONTRACT",
    "OfflineIncarnationCapture",
    "OfflineIncarnationRebindCapture",
    "WorkspaceIncarnationOrchestrationRefused",
    "capture_isolated_page_incarnation_candidate",
    "capture_isolated_page_incarnation_rebind_candidate",
    "revalidate_isolated_page_incarnation_candidate",
    "revalidate_isolated_page_incarnation_rebind_candidate",
    "run_isolated_page_incarnation_attestation",
    "run_isolated_page_incarnation_rebind",
]
