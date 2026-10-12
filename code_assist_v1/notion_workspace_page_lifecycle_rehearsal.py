"""Exact-sentinel NP2C-3d rename rehearsal.

This module combines the already-frozen legacy-v3 rename adapter and the
workspace-1313 catalog finalizer.  It is deliberately absent from routes,
startup, workers, CLIs, and feature flags.  New effects require an explicit
rehearsal sentinel and source quiescence.  A finalized catalog replay is
resolved and source-request-validated before any sentinel, filesystem, or
legacy database access.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_contract import CHANGE_PAGE
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameOperationConflict,
    LegacyPageRenameSourceAdapter,
)
from code_assist_v1.notion_legacy_page_lifecycle_contract import (
    LegacyPageRenamePrestate,
    LegacyPageRenameReceipt,
    build_rename_receipt,
    legacy_page_rename_request_sha256,
    validate_rename_prestate,
    validate_rename_receipt,
)
from code_assist_v1.notion_workspace_incarnation_orchestration import (
    _capture_one_source_snapshot,
)
from code_assist_v1.notion_workspace_inventory import resolve_inventory_paths
from code_assist_v1.notion_workspace_rehearsal import (
    _rehearsal_root,
    _resolved_existing,
)
from code_assist_v1.notion_workspace_store import (
    LegacyPageLocatorProjection,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleRenameCandidate,
    WorkspacePageLifecycleSourceReceipt,
    WorkspaceStore,
    _page_projection_sha256,
    page_source_proof_actor_on_connection,
)


OFFLINE_PAGE_LIFECYCLE_RENAME_REPORT_CONTRACT = (
    "np2c_1313_offline_page_lifecycle_rename_v1"
)


class WorkspacePageLifecycleRehearsalRefused(Exception):
    """Sanitized fail-closed refusal for the offline rename rehearsal."""


FaultHook = Callable[[str], None]


def _paths(
    store: WorkspaceStore,
    *,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
) -> dict[str, Path]:
    # This must remain the first filesystem gate for every new effect.
    if source_quiesced_confirmed is not True:
        raise WorkspacePageLifecycleRehearsalRefused(
            "offline page lifecycle rehearsal requires source quiescence"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspacePageLifecycleRehearsalRefused(
            "exact workspace store is required"
        )
    try:
        sentinel = _rehearsal_root(rehearsal_root)
        _resolved_existing(
            user_data_root,
            root=sentinel,
            kind="data root",
            allow_root=True,
        )
        paths = resolve_inventory_paths(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
            expected_catalog_db_path=expected_catalog_db_path,
        )
        catalog = _resolved_existing(paths["catalog"], root=sentinel, kind="catalog")
        source = _resolved_existing(
            paths["source"], root=sentinel, kind="legacy source"
        )
        if Path(store.db_path).resolve(strict=True) != catalog:
            raise WorkspacePageLifecycleRehearsalRefused(
                "workspace store does not match the rehearsal catalog"
            )
        return {**paths, "catalog": catalog, "source": source}
    except WorkspacePageLifecycleRehearsalRefused:
        raise
    except Exception as exc:
        raise WorkspacePageLifecycleRehearsalRefused(
            "offline page lifecycle rehearsal paths are unavailable"
        ) from exc


def _source_adapter(
    paths: dict[str, Path],
    *,
    source_owner_id: str,
    trusted_clock: Callable[[], int] | None,
) -> tuple[LegacyPageRenameSourceAdapter, sqlite3.Connection]:
    try:
        con = sqlite3.connect(str(paths["source"]))
    except sqlite3.Error as exc:
        raise WorkspacePageLifecycleRehearsalRefused(
            "legacy rename source is unavailable"
        ) from exc
    try:
        adapter = LegacyPageRenameSourceAdapter(
            con,
            source_owner_id=source_owner_id,
            expected_source_db_path=paths["source"],
            trusted_clock=trusted_clock,
        )
        return adapter, con
    except Exception:
        con.close()
        raise


def _assert_prestate_catalog_match(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    prestate: LegacyPageRenamePrestate,
    proof: Any,
    evidence: Any,
    projection: LegacyPageLocatorProjection | None,
) -> None:
    if projection is None:
        raise WorkspacePageLifecycleRehearsalRefused(
            "active locator projection is required"
        )
    con = store._read_connection()
    try:
        workspace = con.execute(
            "SELECT row_version FROM workspaces WHERE id=?",
            (request.workspace_id,),
        ).fetchone()
        binding = con.execute(
            "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id=? AND source_page_id=?",
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
            ),
        ).fetchone()
        attestation = con.execute(
            "SELECT * FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id=? "
            "AND source_page_id=? AND binding_version=?",
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
                request.expected_binding_version,
            ),
        ).fetchone()
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (request.workspace_id,),
        ).fetchone()
        node = con.execute(
            "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id=? AND source_page_id=?",
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
            ),
        ).fetchone()
        summary = con.execute(
            "SELECT * FROM workspace_page_source_projections "
            "WHERE workspace_id=? AND source_owner_subject_id=? "
            "AND source_page_id=? AND binding_version=?",
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
                request.expected_binding_version,
            ),
        ).fetchone()
        file_ids = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT source_file_id FROM workspace_file_sources "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "AND source_page_id=? AND binding_version=? "
                "ORDER BY source_file_id",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    request.source_page_id,
                    request.expected_binding_version,
                ),
            ).fetchall()
        )
        public = tuple(
            (str(row[0]), bool(row[1]))
            for row in con.execute(
                "SELECT token_sha256,allow_duplicate FROM workspace_public_sources "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "AND source_page_id=? AND binding_version=? "
                "ORDER BY token_sha256",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    request.source_page_id,
                    request.expected_binding_version,
                ),
            ).fetchall()
        )
        if (
            workspace is None
            or binding is None
            or attestation is None
            or state is None
            or node is None
            or summary is None
            or int(workspace["row_version"]) != request.base_version
            or str(binding["state"]) != "active"
            or int(binding["row_version"]) != request.expected_binding_version
            or page_source_proof_actor_on_connection(con, binding)
            != request.source_owner_subject_id
            or int(binding["source_revision"]) != proof.revision
            or int(binding["source_updated_at"]) != proof.updated_at
            or str(binding["source_sha256"]) != proof.content_sha256
            or int(state["tree_version"]) != request.expected_tree_version
            or str(state["materialization_state"]) != "complete"
            or int(node["binding_version"]) != request.expected_binding_version
            or str(node["source_incarnation_id"]) != evidence.incarnation_id
            or int(node["source_incarnation_generation"])
            != evidence.incarnation_generation
            or int(node["source_change_generation"]) != evidence.change_generation
            or str(node["source_receipt_sha256"]) != evidence.receipt_sha256
        ):
            raise WorkspacePageLifecycleRehearsalRefused(
                "page lifecycle catalog prestate changed"
            )
        attestation_values = (
            str(attestation["source_incarnation_id"]),
            int(attestation["source_incarnation_generation"]),
            int(attestation["source_epoch"]),
            int(attestation["full_sweep_generation"]),
            int(attestation["source_device"]),
            int(attestation["source_inode"]),
            int(attestation["source_link_count"]),
            int(attestation["source_change_generation"]),
            int(attestation["source_change_mask"]),
            str(attestation["source_receipt_sha256"]),
        )
        evidence_values = (
            evidence.incarnation_id,
            evidence.incarnation_generation,
            evidence.source_epoch,
            evidence.full_sweep_generation,
            evidence.source_device,
            evidence.source_inode,
            evidence.source_link_count,
            evidence.change_generation,
            evidence.change_mask,
            evidence.receipt_sha256,
        )
        if attestation_values != evidence_values:
            raise WorkspacePageLifecycleRehearsalRefused(
                "page lifecycle source incarnation prestate changed"
            )
        projection_files = tuple(item.source_file_id for item in projection.file_sources)
        projection_public = tuple(
            (item.token_sha256, item.allow_duplicate)
            for item in projection.public_sources
        )
        projection_sha256 = _page_projection_sha256(
            workspace_id=request.workspace_id,
            source_owner_subject_id=request.source_owner_subject_id,
            source_page_id=request.source_page_id,
            binding_version=request.expected_binding_version,
            file_sources=projection_files,
            public_sources=projection_public,
        )
        if (
            projection.binding_version != request.expected_binding_version
            or projection_files != file_ids
            or projection_public != public
            or str(summary["projection_sha256"]) != projection_sha256
            or int(summary["file_source_count"]) != len(file_ids)
            or int(summary["public_source_count"]) != len(public)
        ):
            raise WorkspacePageLifecycleRehearsalRefused(
                "page lifecycle locator prestate changed"
            )
        if (
            prestate.revision != proof.revision
            or prestate.updated_at != proof.updated_at
            or prestate.incarnation_id != evidence.incarnation_id
            or prestate.incarnation_generation != evidence.incarnation_generation
            or prestate.source_epoch != evidence.source_epoch
            or prestate.full_sweep_generation != evidence.full_sweep_generation
            or prestate.source_device != evidence.source_device
            or prestate.source_inode != evidence.source_inode
            or prestate.source_link_count != evidence.source_link_count
            or prestate.change_generation != evidence.change_generation
            or prestate.change_mask != evidence.change_mask
            or prestate.incarnation_receipt_sha256 != evidence.receipt_sha256
        ):
            raise WorkspacePageLifecycleRehearsalRefused(
                "page lifecycle source prestate changed"
            )
    finally:
        con.close()


def capture_isolated_page_lifecycle_rename_prestate(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    source_trusted_clock: Callable[[], int] | None = None,
) -> LegacyPageRenamePrestate:
    """Capture the exact source+catalog prestate used by a future rename."""
    if not isinstance(request, WorkspacePageLifecycleCommandRequest):
        raise WorkspacePageLifecycleRehearsalRefused(
            "page lifecycle request is required"
        )
    paths = _paths(
        store,
        rehearsal_root=rehearsal_root,
        user_data_root=user_data_root,
        source_owner_id=request.source_owner_subject_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        source_quiesced_confirmed=source_quiesced_confirmed,
    )
    adapter, con = _source_adapter(
        paths,
        source_owner_id=request.source_owner_subject_id,
        trusted_clock=source_trusted_clock,
    )
    try:
        prestate = adapter.capture_prestate(
            page_id=request.source_page_id,
            source_quiesced_confirmed=True,
        )
    finally:
        con.close()
    proof, evidence, projection = _capture_one_source_snapshot(
        paths=paths,
        source_owner_id=request.source_owner_subject_id,
        source_actor_subject_id=request.source_owner_subject_id,
        source_page_id=request.source_page_id,
        workspace_id=request.workspace_id,
        binding_state="active",
        binding_version=request.expected_binding_version,
    )
    if prestate.incarnation_generation != request.expected_incarnation_generation:
        raise WorkspacePageLifecycleRehearsalRefused(
            "page lifecycle source incarnation prestate changed"
        )
    _assert_prestate_catalog_match(
        store,
        request,
        prestate=prestate,
        proof=proof,
        evidence=evidence,
        projection=projection,
    )
    return prestate


def _candidate_from_receipt(
    *,
    paths: dict[str, Path],
    request: WorkspacePageLifecycleCommandRequest,
    receipt: LegacyPageRenameReceipt,
) -> WorkspacePageLifecycleRenameCandidate:
    binding_version = request.expected_binding_version + (1 if receipt.changed else 0)
    proof, evidence, projection = _capture_one_source_snapshot(
        paths=paths,
        source_owner_id=request.source_owner_subject_id,
        source_actor_subject_id=request.source_owner_subject_id,
        source_page_id=request.source_page_id,
        workspace_id=request.workspace_id,
        binding_state="active",
        binding_version=binding_version,
    )
    if projection is None:
        raise WorkspacePageLifecycleRehearsalRefused(
            "active locator projection is required"
        )
    receipt_values = (
        receipt.source_owner_subject_id,
        receipt.source_page_id,
        receipt.incarnation_id,
        receipt.incarnation_generation,
        receipt.source_epoch,
        receipt.full_sweep_generation,
        receipt.source_device,
        receipt.source_inode,
        receipt.source_link_count,
        receipt.change_generation,
        receipt.change_mask,
        receipt.incarnation_receipt_sha256,
        receipt.revision,
        receipt.updated_at,
    )
    capture_values = (
        evidence.owner_subject_id,
        evidence.page_id,
        evidence.incarnation_id,
        evidence.incarnation_generation,
        evidence.source_epoch,
        evidence.full_sweep_generation,
        evidence.source_device,
        evidence.source_inode,
        evidence.source_link_count,
        evidence.change_generation,
        evidence.change_mask,
        evidence.receipt_sha256,
        proof.revision,
        proof.updated_at,
    )
    if receipt_values != capture_values:
        raise WorkspacePageLifecycleRehearsalRefused(
            "legacy rename receipt no longer matches the source"
        )
    return WorkspacePageLifecycleRenameCandidate(
        changed=receipt.changed,
        source_operation_receipt_sha256=(
            receipt.source_operation_receipt_sha256
        ),
        source_proof=proof,
        source_incarnation=evidence,
        locator_projection=projection,
    )


def _validate_finalized_source_result(
    result: dict[str, Any],
    *,
    request: WorkspacePageLifecycleCommandRequest,
    operation_id: str,
    mutation_actor_subject_id: str,
    requested_title: object,
    expected_prestate: LegacyPageRenamePrestate,
) -> LegacyPageRenameReceipt:
    changed = result["changed"]
    delta = 1 if changed else 0
    expected = validate_rename_prestate(expected_prestate)
    request_sha256 = legacy_page_rename_request_sha256(
        mutation_actor_subject_id=mutation_actor_subject_id,
        normalized_title=requested_title,
        catalog_command_id=result["command_id"],
        caller_operation_id=operation_id,
        expected_prestate=expected,
    )
    post = validate_rename_prestate(
        {
            "source_device": expected.source_device,
            "source_inode": expected.source_inode,
            "source_link_count": expected.source_link_count,
            "source_epoch": expected.source_epoch + delta,
            "full_sweep_generation": expected.full_sweep_generation + delta,
            "source_owner_subject_id": expected.source_owner_subject_id,
            "source_page_id": expected.source_page_id,
            "incarnation_id": expected.incarnation_id,
            "incarnation_generation": expected.incarnation_generation,
            "revision": expected.revision + delta,
            "updated_at": result["source_updated_at"],
            "change_generation": expected.change_generation + delta,
            "change_mask": expected.change_mask | (CHANGE_PAGE if changed else 0),
            "source_exists": True,
            "source_active": True,
            "incarnation_receipt_sha256": result[
                "source_incarnation_receipt_sha256"
            ],
        }
    )
    receipt = validate_rename_receipt(
        build_rename_receipt(
            changed=changed,
            source_request_sha256=request_sha256,
            poststate=post,
        ),
        expected_request_sha256=request_sha256,
        expected_prestate=expected,
    )
    if (
        receipt.source_operation_receipt_sha256
        != result["source_receipt_sha256"]
        or receipt.revision != result["source_revision"]
        or receipt.updated_at != result["source_updated_at"]
        or receipt.incarnation_generation
        != result["source_incarnation_generation"]
        or receipt.change_generation != result["source_change_generation"]
        or receipt.incarnation_receipt_sha256
        != result["source_incarnation_receipt_sha256"]
        or result["previous_binding_version"] != request.expected_binding_version
        or result["tree_version"] != request.expected_tree_version
    ):
        raise LegacyPageRenameOperationConflict(operation_id)
    return receipt


def _report(result: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    status = str(result["status"])
    finalized = status == "finalized"
    return {
        "contract": OFFLINE_PAGE_LIFECYCLE_RENAME_REPORT_CONTRACT,
        "status": status,
        "replayed": bool(replayed),
        "finalized": finalized,
        "failed": status == "failed",
        "manual_audit": status == "manual_audit",
        "changed": bool(result.get("changed", False)) if finalized else False,
        "full_sweep_required": (
            bool(result.get("full_sweep_required", False)) if finalized else False
        ),
        "command_version": int(result["command_version"]),
        "fencing_generation": int(result["fencing_generation"]),
        "binding_version": int(result.get("binding_version", 0)),
        "workspace_version": int(result.get("workspace_version", 0)),
        "tree_state_version": int(result.get("tree_state_version", 0)),
        "affected_node_count": int(result.get("affected_node_count", 0)),
        "file_work_count": int(result.get("file_work_count", 0)),
        "file_source_count": int(result.get("file_source_count", 0)),
        "public_source_count": int(result.get("public_source_count", 0)),
    }


def run_isolated_page_lifecycle_rename(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    actor_subject_id: str,
    mutation_actor_subject_id: str,
    requested_title: object,
    expected_source_prestate: LegacyPageRenamePrestate,
    operation_id: str,
    worker: str,
    lease_token: str,
    lease_seconds: int,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    source_trusted_clock: Callable[[], int] | None = None,
    fault_hook: FaultHook | None = None,
) -> dict[str, Any]:
    """Converge one exact offline legacy rename and 1313 catalog finalize."""
    if not isinstance(store, WorkspaceStore):
        raise WorkspacePageLifecycleRehearsalRefused(
            "exact workspace store is required"
        )
    if not isinstance(request, WorkspacePageLifecycleCommandRequest):
        raise WorkspacePageLifecycleRehearsalRefused(
            "page lifecycle request is required"
        )
    expected = validate_rename_prestate(expected_source_prestate)
    if (
        expected.source_owner_subject_id != request.source_owner_subject_id
        or expected.source_page_id != request.source_page_id
        or expected.incarnation_generation
        != request.expected_incarnation_generation
    ):
        raise WorkspacePageLifecycleRehearsalRefused(
            "page lifecycle source prestate does not match the command"
        )

    # Catalog response-loss replay is deliberately the first external read.
    finalized_replay = store.get_page_lifecycle_rename_finalized_replay(
        actor_subject_id,
        request,
        operation_id=operation_id,
    )
    if finalized_replay is not None:
        _validate_finalized_source_result(
            finalized_replay,
            request=request,
            operation_id=operation_id,
            mutation_actor_subject_id=mutation_actor_subject_id,
            requested_title=requested_title,
            expected_prestate=expected,
        )
        return _report(finalized_replay, replayed=True)

    command = store.get_page_lifecycle_command_replay(
        actor_subject_id,
        request,
        operation_id=operation_id,
    )
    if command is not None and command["status"] in {"failed", "manual_audit"}:
        return _report(command, replayed=True)

    paths = _paths(
        store,
        rehearsal_root=rehearsal_root,
        user_data_root=user_data_root,
        source_owner_id=request.source_owner_subject_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
        source_quiesced_confirmed=source_quiesced_confirmed,
    )
    if command is None:
        current = capture_isolated_page_lifecycle_rename_prestate(
            store,
            request,
            rehearsal_root=rehearsal_root,
            user_data_root=user_data_root,
            expected_source_db_path=paths["source"],
            expected_catalog_db_path=paths["catalog"],
            source_quiesced_confirmed=True,
            source_trusted_clock=source_trusted_clock,
        )
        if current != expected:
            raise WorkspacePageLifecycleRehearsalRefused(
                "page lifecycle source prestate changed"
            )
        command = store.prepare_page_lifecycle_command(
            actor_subject_id,
            request,
            operation_id=operation_id,
        )

    if command["status"] not in {"prepared", "source_applied"}:
        raise WorkspacePageLifecycleRehearsalRefused(
            "page lifecycle command status is invalid"
        )
    command = store.claim_page_lifecycle_command(
        actor_subject_id,
        request.workspace_id,
        str(command["command_id"]),
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        expected_command_version=int(command["command_version"]),
    )

    adapter, source_con = _source_adapter(
        paths,
        source_owner_id=request.source_owner_subject_id,
        trusted_clock=source_trusted_clock,
    )
    try:
        receipt = adapter.rename(
            mutation_actor_subject_id=mutation_actor_subject_id,
            requested_title=requested_title,
            catalog_command_id=str(command["command_id"]),
            caller_operation_id=operation_id,
            expected_prestate=expected,
            source_quiesced_confirmed=True,
            fault_hook=fault_hook,
        )
        source_receipt = WorkspacePageLifecycleSourceReceipt(
            source_receipt_sha256=receipt.source_operation_receipt_sha256,
            affected_node_count=1,
            file_work_count=0,
        )
        if command["status"] == "prepared":
            command = store.record_page_lifecycle_source_applied(
                actor_subject_id,
                request.workspace_id,
                str(command["command_id"]),
                source_receipt,
                operation_id=operation_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=int(command["fencing_generation"]),
                expected_command_version=int(command["command_version"]),
                source_revalidator=lambda value: (
                    value == source_receipt
                    and adapter.rename(
                        mutation_actor_subject_id=mutation_actor_subject_id,
                        requested_title=requested_title,
                        catalog_command_id=str(command["command_id"]),
                        caller_operation_id=operation_id,
                        expected_prestate=expected,
                        source_quiesced_confirmed=False,
                    )
                    == receipt
                ),
            )
        candidate = _candidate_from_receipt(
            paths=paths,
            request=request,
            receipt=receipt,
        )

        def revalidate_candidate(
            value: WorkspacePageLifecycleRenameCandidate,
        ) -> bool:
            replayed_source = adapter.rename(
                mutation_actor_subject_id=mutation_actor_subject_id,
                requested_title=requested_title,
                catalog_command_id=str(command["command_id"]),
                caller_operation_id=operation_id,
                expected_prestate=expected,
                source_quiesced_confirmed=False,
            )
            if replayed_source != receipt:
                return False
            return value == _candidate_from_receipt(
                paths=paths,
                request=request,
                receipt=replayed_source,
            )

        finalized = store.finalize_page_lifecycle_rename_catalog(
            actor_subject_id,
            request.workspace_id,
            str(command["command_id"]),
            candidate,
            operation_id=operation_id,
            worker=worker,
            lease_token=lease_token,
            fencing_generation=int(command["fencing_generation"]),
            expected_command_version=int(command["command_version"]),
            source_revalidator=revalidate_candidate,
        )
        if fault_hook is not None:
            fault_hook("after_catalog_finalize")
        return _report(finalized, replayed=False)
    finally:
        source_con.close()


__all__ = [
    "OFFLINE_PAGE_LIFECYCLE_RENAME_REPORT_CONTRACT",
    "WorkspacePageLifecycleRehearsalRefused",
    "capture_isolated_page_lifecycle_rename_prestate",
    "run_isolated_page_lifecycle_rename",
]
