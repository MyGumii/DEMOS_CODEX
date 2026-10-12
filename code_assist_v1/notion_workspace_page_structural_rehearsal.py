"""Exact-sentinel NP2C-3e3 structural lifecycle rehearsal.

The module is offline-only and route-unregistered.  A finalized catalog replay
is validated before sentinel, path, or source access.  Every new effect requires
explicit source quiescence and exact source/catalog paths beneath the rehearsal
sentinel.  The legacy mutation receipt and catalog manifest are independently
immutable and converge through one atomic 1315 catalog finalizer.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_contract import (
    assert_legacy_rollback_journal,
    legacy_source_file_identity,
    open_exact_legacy_change_source,
    resolve_legacy_change_paths,
)
from code_assist_v1.notion_legacy_changes import (
    _receipt_from_snapshot,
    inspect_legacy_incarnation_source_contract,
    legacy_incarnation_receipt_sha256,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    require_legacy_page_incarnation_id,
)
from code_assist_v1.notion_legacy_page_structural import (
    LegacyPageStructuralReceipt,
    LegacyPageStructuralSourceAdapter,
    structural_request_sha256,
)
from code_assist_v1.notion_workspace_page_lifecycle_rehearsal import (
    WorkspacePageLifecycleRehearsalRefused,
    _paths,
)
from code_assist_v1.notion_workspace_projection import (
    _capture_bounded_fresh_page,
    _sqlite_int64_identity,
)
from code_assist_v1.notion_workspace_store import (
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleSourceReceipt,
    WorkspacePageLifecycleStructuralCandidate,
    WorkspacePageLifecycleStructuralEffectCandidate,
    WorkspacePageTreeSourceFence,
    WorkspaceStore,
    _page_projection_sha256,
)


OFFLINE_PAGE_LIFECYCLE_STRUCTURAL_REPORT_CONTRACT = (
    "np2c_1315_offline_page_lifecycle_structural_v1"
)


def _source_adapter(
    paths: dict[str, Path],
    *,
    source_owner_id: str,
    trusted_clock: Callable[[], int] | None,
) -> tuple[LegacyPageStructuralSourceAdapter, sqlite3.Connection]:
    try:
        con = sqlite3.connect(str(paths["source"]))
    except sqlite3.Error as exc:
        raise WorkspacePageLifecycleRehearsalRefused(
            "legacy structural source is unavailable"
        ) from exc
    try:
        adapter = LegacyPageStructuralSourceAdapter(
            con,
            source_owner_id=source_owner_id,
            expected_source_db_path=paths["source"],
            trusted_clock=trusted_clock,
        )
        return adapter, con
    except Exception:
        con.close()
        raise


def capture_isolated_page_lifecycle_structural_precondition(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    source_trusted_clock: Callable[[], int] | None = None,
) -> str:
    if not isinstance(request, WorkspacePageLifecycleCommandRequest) or request.action not in {
        "create", "move", "reorder"
    }:
        raise WorkspacePageLifecycleRehearsalRefused(
            "structural page lifecycle request is required"
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
        return adapter.capture_precondition(
            action=request.action,
            target_source_page_id=request.source_page_id,
            new_parent_source_page_id=request.parent_source_page_id,
            relative_source_page_id=request.relative_source_page_id,
            source_quiesced_confirmed=True,
        )
    finally:
        con.close()


def _catalog_before(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    receipt: LegacyPageStructuralReceipt,
) -> dict[str, dict[str, Any]]:
    con = store._read_connection()
    try:
        result: dict[str, dict[str, Any]] = {}
        for effect in receipt.effects:
            page_id = effect.source_page_id
            binding = con.execute(
                "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
                "AND source_owner_subject_id=? AND source_page_id=?",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    page_id,
                ),
            ).fetchone()
            node = con.execute(
                "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
                "AND source_owner_subject_id=? AND source_page_id=?",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    page_id,
                ),
            ).fetchone()
            if binding is None:
                if request.action != "create" or page_id != request.source_page_id or node is not None:
                    raise WorkspacePageLifecycleRehearsalRefused(
                        "structural catalog effect set changed"
                    )
                result[page_id] = {
                    "binding": None,
                    "node": None,
                    "attestation": None,
                    "files": (),
                    "public": (),
                    "projection_sha256": None,
                }
                continue
            if str(binding["state"]) != "active" or node is None or str(
                node["lifecycle_state"]
            ) != "active":
                raise WorkspacePageLifecycleRehearsalRefused(
                    "structural catalog binding changed"
                )
            version = int(binding["row_version"])
            attestation = con.execute(
                "SELECT * FROM workspace_page_source_incarnation_attestations "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "AND source_page_id=? AND binding_version=?",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    page_id,
                    version,
                ),
            ).fetchone()
            projection = con.execute(
                "SELECT * FROM workspace_page_source_projections "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "AND source_page_id=? AND binding_version=?",
                (
                    request.workspace_id,
                    request.source_owner_subject_id,
                    page_id,
                    version,
                ),
            ).fetchone()
            if attestation is None or projection is None:
                raise WorkspacePageLifecycleRehearsalRefused(
                    "structural catalog evidence changed"
                )
            files = tuple(
                str(row[0])
                for row in con.execute(
                    "SELECT source_file_id FROM workspace_file_sources "
                    "WHERE workspace_id=? AND source_owner_subject_id=? "
                    "AND source_page_id=? AND binding_version=? "
                    "ORDER BY source_file_id",
                    (
                        request.workspace_id,
                        request.source_owner_subject_id,
                        page_id,
                        version,
                    ),
                )
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
                        page_id,
                        version,
                    ),
                )
            )
            if (
                str(node["parent_source_page_id"] or "")
                != effect.old_parent_source_page_id
                or int(node["sibling_ordinal"])
                != effect.old_sibling_ordinal
                or int(node["depth"]) != effect.old_depth
                or int(node["binding_version"]) != version
            ):
                raise WorkspacePageLifecycleRehearsalRefused(
                    "structural catalog before-tree changed"
                )
            result[page_id] = {
                "binding": binding,
                "node": node,
                "attestation": attestation,
                "files": files,
                "public": public,
                "projection_sha256": str(projection["projection_sha256"]),
            }
        return result
    finally:
        con.close()


def _candidate_from_source(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    receipt: LegacyPageStructuralReceipt,
    *,
    paths: dict[str, Path],
) -> WorkspacePageLifecycleStructuralCandidate:
    before = _catalog_before(store, request, receipt)
    legacy_paths = resolve_legacy_change_paths(
        user_data_root=paths["root"],
        source_owner_id=request.source_owner_subject_id,
        expected_source_db_path=paths["source"],
    )
    assert_legacy_rollback_journal(legacy_paths["source"])
    identity = legacy_source_file_identity(legacy_paths["source"])
    con = open_exact_legacy_change_source(
        legacy_paths["source"],
        root=legacy_paths["root"],
        expected_resolved=legacy_paths["source"],
        expected_identity=identity,
    )
    try:
        con.execute("BEGIN")
        inspected = inspect_legacy_incarnation_source_contract(
            con, verify_integrity=True
        )
        if inspected.get("installed") is not True:
            raise WorkspacePageLifecycleRehearsalRefused(
                "legacy incarnation tracking is unavailable"
            )
        effects: list[WorkspacePageLifecycleStructuralEffectCandidate] = []
        for ordinal, structural in enumerate(receipt.effects, start=1):
            page_id = structural.source_page_id
            current = before[page_id]
            binding = current["binding"]
            old_version = int(binding["row_version"]) if binding is not None else 0
            version = old_version + int(structural.source_mutated)
            proof, files, public = _capture_bounded_fresh_page(
                con,
                paths=paths,
                source_owner_id=request.source_owner_subject_id,
                actor_subject_id=request.source_owner_subject_id,
                page_id=page_id,
            )
            v1_receipt = _receipt_from_snapshot(
                con,
                source_owner_id=request.source_owner_subject_id,
                page_id=page_id,
                source_identity=identity,
            )
            history = con.execute(
                "SELECT incarnation_generation,incarnation_id,retired_at "
                "FROM notion_page_incarnation_history WHERE page_id=? "
                "ORDER BY incarnation_generation DESC LIMIT 1",
                (page_id,),
            ).fetchone()
            if history is None or history["retired_at"] is not None:
                raise WorkspacePageLifecycleRehearsalRefused(
                    "legacy structural incarnation changed"
                )
            projection = LegacyPageLocatorProjection(
                actor_subject_id=request.source_owner_subject_id,
                owner_subject_id=request.source_owner_subject_id,
                page_id=page_id,
                binding_version=version,
                file_sources=files,
                public_sources=public,
            )
            if structural.source_mutated:
                incarnation_id = require_legacy_page_incarnation_id(
                    history["incarnation_id"]
                )
                incarnation_generation = int(
                    history["incarnation_generation"]
                )
                evidence = LegacyPageIncarnationEvidence(
                    owner_subject_id=request.source_owner_subject_id,
                    page_id=page_id,
                    incarnation_id=incarnation_id,
                    incarnation_generation=incarnation_generation,
                    incarnation_retired=False,
                    source_epoch=v1_receipt.fence.source_epoch,
                    full_sweep_generation=(
                        v1_receipt.fence.full_sweep_generation
                    ),
                    source_device=_sqlite_int64_identity(
                        v1_receipt.fence.device
                    ),
                    source_inode=_sqlite_int64_identity(v1_receipt.fence.inode),
                    source_link_count=v1_receipt.fence.link_count,
                    change_generation=v1_receipt.generation,
                    change_mask=v1_receipt.mask,
                    source_exists=True,
                    source_active=True,
                    receipt_sha256=legacy_incarnation_receipt_sha256(
                        source_owner_id=request.source_owner_subject_id,
                        page_id=page_id,
                        incarnation_id=incarnation_id,
                        incarnation_generation=incarnation_generation,
                        incarnation_retired=False,
                        change_generation=v1_receipt.generation,
                        change_mask=v1_receipt.mask,
                        source_exists=True,
                        source_active=True,
                        source_epoch=v1_receipt.fence.source_epoch,
                        full_sweep_generation=(
                            v1_receipt.fence.full_sweep_generation
                        ),
                        source_device=v1_receipt.fence.device,
                        source_inode=v1_receipt.fence.inode,
                        source_link_count=v1_receipt.fence.link_count,
                    ),
                )
            else:
                attestation = current["attestation"]
                if (
                    attestation is None
                    or proof.revision != int(binding["source_revision"])
                    or proof.updated_at != int(binding["source_updated_at"])
                    or proof.content_sha256 != str(binding["source_sha256"])
                    or require_legacy_page_incarnation_id(
                        history["incarnation_id"]
                    ) != str(attestation["source_incarnation_id"])
                    or int(history["incarnation_generation"])
                    != int(attestation["source_incarnation_generation"])
                    or v1_receipt.generation
                    != int(attestation["source_change_generation"])
                    or v1_receipt.mask != int(attestation["source_change_mask"])
                ):
                    raise WorkspacePageLifecycleRehearsalRefused(
                        "unmutated structural source evidence changed"
                    )
                file_ids = tuple(item.source_file_id for item in files)
                public_ids = tuple(
                    (item.token_sha256, item.allow_duplicate) for item in public
                )
                expected_projection = _page_projection_sha256(
                    workspace_id=request.workspace_id,
                    source_owner_subject_id=request.source_owner_subject_id,
                    source_page_id=page_id,
                    binding_version=version,
                    file_sources=file_ids,
                    public_sources=public_ids,
                )
                if (
                    file_ids != current["files"]
                    or public_ids != current["public"]
                    or expected_projection != current["projection_sha256"]
                ):
                    raise WorkspacePageLifecycleRehearsalRefused(
                        "unmutated structural locator evidence changed"
                    )
                evidence = LegacyPageIncarnationEvidence(
                    owner_subject_id=request.source_owner_subject_id,
                    page_id=page_id,
                    incarnation_id=str(attestation["source_incarnation_id"]),
                    incarnation_generation=int(
                        attestation["source_incarnation_generation"]
                    ),
                    incarnation_retired=False,
                    source_epoch=int(attestation["source_epoch"]),
                    full_sweep_generation=int(
                        attestation["full_sweep_generation"]
                    ),
                    source_device=int(attestation["source_device"]),
                    source_inode=int(attestation["source_inode"]),
                    source_link_count=int(attestation["source_link_count"]),
                    change_generation=int(
                        attestation["source_change_generation"]
                    ),
                    change_mask=int(attestation["source_change_mask"]),
                    source_exists=True,
                    source_active=True,
                    receipt_sha256=str(attestation["source_receipt_sha256"]),
                )
            effects.append(
                WorkspacePageLifecycleStructuralEffectCandidate(
                    effect_ordinal=ordinal,
                    effect_roles=structural.effect_roles,
                    source_mutated=structural.source_mutated,
                    source_proof=proof,
                    source_incarnation=evidence,
                    locator_projection=projection,
                    old_parent_source_page_id=(
                        structural.old_parent_source_page_id
                    ),
                    new_parent_source_page_id=(
                        structural.new_parent_source_page_id
                    ),
                    old_sibling_ordinal=structural.old_sibling_ordinal,
                    new_sibling_ordinal=structural.new_sibling_ordinal,
                    old_depth=structural.old_depth,
                    new_depth=structural.new_depth,
                )
            )
        con.rollback()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
    assert_legacy_rollback_journal(legacy_paths["source"])
    if legacy_source_file_identity(legacy_paths["source"]) != identity:
        raise WorkspacePageLifecycleRehearsalRefused(
            "legacy structural source identity changed"
        )
    target = next(
        effect
        for effect in effects
        if effect.source_proof.page_id == request.source_page_id
    )
    source_fence = WorkspacePageTreeSourceFence(
        source_owner_subject_id=request.source_owner_subject_id,
        source_epoch=receipt.source_epoch,
        full_sweep_generation=receipt.full_sweep_generation,
        source_device=receipt.source_device,
        source_inode=receipt.source_inode,
        source_link_count=receipt.source_link_count,
    )
    return WorkspacePageLifecycleStructuralCandidate(
        action=request.action,
        source_request_sha256=receipt.source_request_sha256,
        source_operation_receipt_sha256=(
            receipt.source_operation_receipt_sha256
        ),
        post_manifest_sha256=receipt.post_manifest_sha256,
        source_changed=True,
        structure_changed=any(
            effect.old_parent_source_page_id
            != effect.new_parent_source_page_id
            or effect.old_sibling_ordinal != effect.new_sibling_ordinal
            or effect.old_depth != effect.new_depth
            for effect in effects
        ),
        source_fence=source_fence,
        target_incarnation_id=target.source_incarnation.incarnation_id,
        target_incarnation_generation=(
            target.source_incarnation.incarnation_generation
        ),
        target_change_generation=target.source_incarnation.change_generation,
        target_change_mask=target.source_incarnation.change_mask,
        target_source_receipt_sha256=(
            target.source_incarnation.receipt_sha256
        ),
        old_parent_source_page_id=target.old_parent_source_page_id,
        new_parent_source_page_id=target.new_parent_source_page_id,
        target_dense_ordinal=target.new_sibling_ordinal,
        subtree_count=receipt.subtree_count,
        old_scope_count=receipt.old_scope_count,
        new_scope_count=receipt.new_scope_count,
        effects=tuple(effects),
    )


def _report(result: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "contract": OFFLINE_PAGE_LIFECYCLE_STRUCTURAL_REPORT_CONTRACT,
        "status": str(result["status"]),
        "replayed": bool(replayed),
        "finalized": result["status"] == "finalized",
        "action": str(result.get("action") or ""),
        "source_changed": bool(result.get("source_changed", False)),
        "structure_changed": bool(result.get("structure_changed", False)),
        "full_sweep_required": bool(
            result.get("full_sweep_required", False)
        ),
        "affected_page_count": int(result.get("affected_page_count", 0)),
        "source_mutated_page_count": int(
            result.get("source_mutated_page_count", 0)
        ),
        "subtree_count": int(result.get("subtree_count", 0)),
        "old_scope_count": int(result.get("old_scope_count", 0)),
        "new_scope_count": int(result.get("new_scope_count", 0)),
        "command_version": int(result.get("command_version", 0)),
        "fencing_generation": int(result.get("fencing_generation", 0)),
        "workspace_version": int(result.get("workspace_version", 0)),
        "tree_version": int(result.get("tree_version", 0)),
        "tree_state_version": int(result.get("tree_state_version", 0)),
    }


def run_isolated_page_lifecycle_structural(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    actor_subject_id: str,
    mutation_actor_subject_id: str,
    requested_title: object,
    expected_source_precondition_sha256: str,
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
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, WorkspacePageLifecycleCommandRequest) or request.action not in {
        "create", "move", "reorder"
    }:
        raise WorkspacePageLifecycleRehearsalRefused(
            "structural page lifecycle request is required"
        )

    finalized_replay = store.get_page_lifecycle_structural_finalized_replay(
        actor_subject_id,
        request,
        operation_id=operation_id,
    )
    if finalized_replay is not None:
        expected_request = structural_request_sha256(
            action=request.action,
            mutation_actor_subject_id=mutation_actor_subject_id,
            source_owner_subject_id=request.source_owner_subject_id,
            target_source_page_id=request.source_page_id,
            new_parent_source_page_id=request.parent_source_page_id,
            relative_source_page_id=request.relative_source_page_id,
            relative_position=request.relative_position,
            requested_title=requested_title,
            catalog_command_id=finalized_replay["command_id"],
            caller_operation_id=operation_id,
            expected_precondition_sha256=expected_source_precondition_sha256,
        )
        if expected_request != finalized_replay["source_request_sha256"]:
            raise WorkspacePageLifecycleRehearsalRefused(
                "structural source request replay conflict"
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
    adapter, source_con = _source_adapter(
        paths,
        source_owner_id=request.source_owner_subject_id,
        trusted_clock=source_trusted_clock,
    )
    try:
        if command is None:
            current_precondition = adapter.capture_precondition(
                action=request.action,
                target_source_page_id=request.source_page_id,
                new_parent_source_page_id=request.parent_source_page_id,
                relative_source_page_id=request.relative_source_page_id,
                source_quiesced_confirmed=True,
            )
            if current_precondition != expected_source_precondition_sha256:
                raise WorkspacePageLifecycleRehearsalRefused(
                    "structural source precondition changed"
                )
            command = store.prepare_page_lifecycle_command(
                actor_subject_id,
                request,
                operation_id=operation_id,
            )
        if command["status"] not in {"prepared", "source_applied"}:
            raise WorkspacePageLifecycleRehearsalRefused(
                "structural lifecycle command status is invalid"
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
        receipt = adapter.apply_structural(
            action=request.action,
            mutation_actor_subject_id=mutation_actor_subject_id,
            target_source_page_id=request.source_page_id,
            new_parent_source_page_id=request.parent_source_page_id,
            relative_source_page_id=request.relative_source_page_id,
            relative_position=request.relative_position,
            requested_title=requested_title,
            catalog_command_id=str(command["command_id"]),
            caller_operation_id=operation_id,
            expected_precondition_sha256=expected_source_precondition_sha256,
            source_quiesced_confirmed=True,
            fault_hook=fault_hook,
        )
        source_receipt = WorkspacePageLifecycleSourceReceipt(
            source_receipt_sha256=receipt.source_operation_receipt_sha256,
            affected_node_count=receipt.subtree_count,
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
                    and adapter.apply_structural(
                        action=request.action,
                        mutation_actor_subject_id=mutation_actor_subject_id,
                        target_source_page_id=request.source_page_id,
                        new_parent_source_page_id=request.parent_source_page_id,
                        relative_source_page_id=request.relative_source_page_id,
                        relative_position=request.relative_position,
                        requested_title=requested_title,
                        catalog_command_id=str(command["command_id"]),
                        caller_operation_id=operation_id,
                        expected_precondition_sha256=(
                            expected_source_precondition_sha256
                        ),
                        source_quiesced_confirmed=False,
                    )
                    == receipt
                ),
            )
        candidate = _candidate_from_source(
            store, request, receipt, paths=paths
        )

        def revalidate_candidate(
            value: WorkspacePageLifecycleStructuralCandidate,
        ) -> bool:
            replayed_source = adapter.apply_structural(
                action=request.action,
                mutation_actor_subject_id=mutation_actor_subject_id,
                target_source_page_id=request.source_page_id,
                new_parent_source_page_id=request.parent_source_page_id,
                relative_source_page_id=request.relative_source_page_id,
                relative_position=request.relative_position,
                requested_title=requested_title,
                catalog_command_id=str(command["command_id"]),
                caller_operation_id=operation_id,
                expected_precondition_sha256=(
                    expected_source_precondition_sha256
                ),
                source_quiesced_confirmed=False,
            )
            return replayed_source == receipt and value == _candidate_from_source(
                store, request, replayed_source, paths=paths
            )

        finalized = store.finalize_page_lifecycle_structural_catalog(
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
    "OFFLINE_PAGE_LIFECYCLE_STRUCTURAL_REPORT_CONTRACT",
    "capture_isolated_page_lifecycle_structural_precondition",
    "run_isolated_page_lifecycle_structural",
]
