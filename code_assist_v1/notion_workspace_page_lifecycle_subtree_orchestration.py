"""Exact-sentinel offline duplicate/Trash/restore orchestration on schema 1317.

This module is deliberately absent from application startup, routes, workers,
and CLIs.  Exact catalog replay is authorized before any sentinel or source
path access.  A new effect requires explicit quiescence, exact paths beneath a
rehearsal sentinel, one source mutation receipt, a single-snapshot capture of
every result page, a second exact source replay/snapshot revalidation, and one
atomic catalog finalization.  Reports contain counters only.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_contract import (
    legacy_source_file_identity,
)
from code_assist_v1.notion_legacy_change_consumer_contract import (
    inspect_legacy_change_consumer_contract,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyPageIncarnationReceipt,
    _positive_generation,
    _receipt_from_snapshot,
    inspect_legacy_incarnation_source_contract,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    require_legacy_page_incarnation_id,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore import (
    LegacyPageDuplicateTrashRestoreSourceAdapter,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    LegacyPageLifecycleReceipt,
)
from code_assist_v1.notion_workspace_inventory import (
    _page_proof,
    resolve_inventory_paths,
)
from code_assist_v1.notion_workspace_page_lifecycle_subtree_finalize import (
    WorkspacePageLifecycleSubtreeCandidate,
    WorkspacePageLifecycleSubtreePageCandidate,
    finalize_page_lifecycle_subtree_catalog,
    normalize_subtree_candidate,
)
from code_assist_v1.notion_workspace_projection import (
    _MAX_SHADOW_LINKED_METADATA_BYTES,
    _MAX_SHADOW_LINKED_REFERENCES,
    _assert_bounded_shadow_page,
    _capture_bounded_fresh_page,
)
from code_assist_v1.notion_workspace_rehearsal import (
    _rehearsal_root,
    _resolved_existing,
)
from code_assist_v1 import notion_workspace_store as catalog


OFFLINE_SUBTREE_REPORT_CONTRACT = "np2c_1317_offline_lifecycle_subtree_v1"


class WorkspacePageLifecycleSubtreeOrchestrationRefused(Exception):
    """Identifier-free, fail-closed offline orchestration refusal."""


def _source_action(catalog_action: str) -> str:
    return "trash" if catalog_action == "archive" else catalog_action


def _open_source_writer(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "exact legacy lifecycle source is unavailable"
        ) from exc


def _receipt_from_snapshot_without_reinspection(
    con: sqlite3.Connection,
    *,
    owner: str,
    page_id: str,
    source_identity: tuple[int, int, int, int, int],
) -> LegacyPageIncarnationReceipt:
    v1 = _receipt_from_snapshot(
        con,
        source_owner_id=owner,
        page_id=page_id,
        source_identity=source_identity,
    )
    row = con.execute(
        "SELECT incarnation_generation,incarnation_id,retired_at "
        "FROM notion_page_incarnation_history WHERE page_id=? "
        "ORDER BY incarnation_generation DESC LIMIT 1",
        (page_id,),
    ).fetchone()
    if row is None:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "legacy page incarnation receipt is unavailable"
        )
    generation = _positive_generation(
        row["incarnation_generation"], field="incarnation_generation"
    )
    incarnation_id = require_legacy_page_incarnation_id(row["incarnation_id"])
    retired_at = row["retired_at"]
    if retired_at is not None and (
        type(retired_at) is not int or retired_at < 0
    ):
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "legacy page incarnation history is invalid"
        )
    retired = retired_at is not None
    if retired is v1.source_exists:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "legacy page incarnation history is invalid"
        )
    return LegacyPageIncarnationReceipt(
        source_owner_id=owner,
        page_id=page_id,
        incarnation_id=incarnation_id,
        incarnation_generation=generation,
        incarnation_retired=retired,
        generation=v1.generation,
        mask=v1.mask,
        source_exists=v1.source_exists,
        source_active=v1.source_active,
        fence=v1.fence,
    )


def _catalog_binding_versions(
    store: catalog.WorkspaceStore,
    *,
    workspace_id: str,
    owner: str,
    receipt: LegacyPageLifecycleReceipt,
) -> dict[str, int]:
    con = store._read_connection()
    try:
        versions: dict[str, int] = {}
        for effect in receipt.effects:
            row = con.execute(
                "SELECT row_version FROM workspace_page_sources "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "AND source_page_id=?",
                (workspace_id, owner, effect.result_page_id),
            ).fetchone()
            if effect.transitioned and receipt.action == "duplicate":
                if row is not None:
                    raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                        "duplicate lifecycle destination already exists"
                    )
                versions[effect.result_page_id] = 1
            else:
                if row is None:
                    raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                        "lifecycle catalog page is unavailable"
                    )
                versions[effect.result_page_id] = int(row["row_version"]) + 1
        return versions
    finally:
        con.close()


def _capture_candidate_on_connection(
    con: sqlite3.Connection,
    *,
    store: catalog.WorkspaceStore,
    paths: dict[str, Path],
    workspace_id: str,
    receipt: LegacyPageLifecycleReceipt,
) -> WorkspacePageLifecycleSubtreeCandidate:
    if con.in_transaction:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "idle legacy lifecycle source required"
        )
    owner = receipt.source_owner_subject_id
    source_identity = legacy_source_file_identity(paths["source"])
    expected_identity = (
        catalog.sqlite_source_identity(receipt.source_device),
        catalog.sqlite_source_identity(receipt.source_inode),
        int(receipt.source_link_count),
    )
    if (
        catalog.sqlite_source_identity(source_identity[0]),
        catalog.sqlite_source_identity(source_identity[1]),
        int(source_identity[2]),
    ) != expected_identity:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "legacy lifecycle source identity changed"
        )
    versions = _catalog_binding_versions(
        store,
        workspace_id=workspace_id,
        owner=owner,
        receipt=receipt,
    )
    pages: list[WorkspacePageLifecycleSubtreePageCandidate] = []
    total_file_sources = 0
    total_public_sources = 0
    try:
        con.execute("BEGIN")
        v3 = inspect_legacy_change_consumer_contract(con, verify_integrity=True)
        v2 = inspect_legacy_incarnation_source_contract(
            con, verify_integrity=False
        )
        if v3.get("installed") is not True or v2.get("installed") is not True:
            raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                "legacy lifecycle source contract is unavailable"
            )
        for effect in receipt.effects:
            page_id = effect.result_page_id
            incarnation = _receipt_from_snapshot_without_reinspection(
                con,
                owner=owner,
                page_id=page_id,
                source_identity=source_identity,
            )
            if (
                not incarnation.source_exists
                or incarnation.incarnation_retired
                or incarnation.incarnation_id != effect.result_incarnation_id
                or incarnation.incarnation_generation
                != effect.result_incarnation_generation
                or incarnation.generation != effect.result_change_generation
                or incarnation.source_active
                is not (effect.after_lifecycle == "active")
                or incarnation.fence.source_epoch != receipt.source_epoch
                or incarnation.fence.full_sweep_generation
                != receipt.full_sweep_generation
            ):
                raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                    "legacy lifecycle page receipt changed"
                )
            if incarnation.source_active:
                proof, files, public = _capture_bounded_fresh_page(
                    con,
                    paths=paths,
                    source_owner_id=owner,
                    actor_subject_id=owner,
                    page_id=page_id,
                )
                projection = catalog.LegacyPageLocatorProjection(
                    actor_subject_id=owner,
                    owner_subject_id=owner,
                    page_id=page_id,
                    binding_version=versions[page_id],
                    file_sources=files,
                    public_sources=public,
                )
            else:
                _assert_bounded_shadow_page(con, page_id)
                page_row = con.execute(
                    "SELECT * FROM pages WHERE id=? AND owner_user_id=?",
                    (page_id, owner),
                ).fetchone()
                if page_row is None or not bool(page_row["archived"]):
                    raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                        "trashed lifecycle page is unavailable"
                    )
                proof = _page_proof(
                    con,
                    actor=owner,
                    owner=owner,
                    page=page_row,
                    role="owner",
                    reference_limit=_MAX_SHADOW_LINKED_REFERENCES,
                    linked_metadata_byte_limit=_MAX_SHADOW_LINKED_METADATA_BYTES,
                )
                files = ()
                public = ()
                projection = None
            if proof.revision != effect.result_revision:
                raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                    "legacy lifecycle page proof changed"
                )
            total_file_sources += len(files)
            total_public_sources += len(public)
            if (
                total_file_sources > catalog.MAX_PAGE_LIFECYCLE_SUBTREE_MAPPINGS
                or total_public_sources > len(receipt.effects)
            ):
                raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
                    "legacy lifecycle locator set exceeds the aggregate bound"
                )
            pages.append(
                WorkspacePageLifecycleSubtreePageCandidate(
                    page_id=page_id,
                    source_revision=proof.revision,
                    source_updated_at=proof.updated_at,
                    page_sha256=proof.content_sha256,
                    incarnation_id=incarnation.incarnation_id,
                    incarnation_generation=incarnation.incarnation_generation,
                    incarnation_retired=False,
                    source_epoch=incarnation.fence.source_epoch,
                    full_sweep_generation=incarnation.fence.full_sweep_generation,
                    source_device=catalog.sqlite_source_identity(
                        incarnation.fence.device
                    ),
                    source_inode=catalog.sqlite_source_identity(
                        incarnation.fence.inode
                    ),
                    source_link_count=incarnation.fence.link_count,
                    change_generation=incarnation.generation,
                    change_mask=incarnation.mask,
                    source_exists=True,
                    source_active=incarnation.source_active,
                    receipt_sha256=incarnation.receipt_sha256,
                    source_proof=proof if incarnation.source_active else None,
                    locator_projection=projection,
                )
            )
        candidate = WorkspacePageLifecycleSubtreeCandidate(
            receipt=receipt,
            pages=tuple(sorted(pages, key=lambda item: item.page_id)),
        )
        normalized = normalize_subtree_candidate(
            candidate,
            workspace_id=workspace_id,
            expected_action=(
                "archive" if receipt.action == "trash" else receipt.action
            ),
        )
        con.rollback()
        return normalized
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise


def _report(result: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "contract": OFFLINE_SUBTREE_REPORT_CONTRACT,
        "offline_only": True,
        "quiescence_confirmed": True,
        "replayed": replayed,
        "canonical_action": str(result["canonical_action"]),
        "effect_count": int(result["effect_count"]),
        "transitioned_count": int(result["transitioned_count"]),
        "mapping_count": int(result["mapping_count"]),
        "file_mapping_count": int(result["file_mapping_count"]),
        "active_node_count": int(result["active_node_count"]),
        "archived_node_count": int(result["archived_node_count"]),
        "runtime_commands_enabled": False,
        "resolver_cutover_enabled": False,
        "source_watermarks_acknowledged": False,
    }


def run_isolated_page_lifecycle_subtree(
    store: catalog.WorkspaceStore,
    actor_subject_id: str | None,
    request: catalog.WorkspacePageLifecycleCommandRequest,
    *,
    operation_id: str,
    source_caller_operation_id: str,
    worker: str,
    lease_token: str,
    lease_seconds: int,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_source_db_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    trash_source_operation_id: str = "",
    placement_mode: str = "",
    fallback_parent_source_page_id: str = "",
    relative_source_page_id: str = "",
    relative_position: str = "",
    source_trusted_clock: Callable[[], int] | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one exact offline subtree command without registering runtime IO."""
    if not isinstance(store, catalog.WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    normalized_request = catalog._normalize_page_lifecycle_request(request)
    if normalized_request.action not in catalog.PAGE_LIFECYCLE_SUBTREE_ACTION_MAP:
        raise ValueError("subtree page lifecycle request required")
    replay = store.get_page_lifecycle_subtree_finalized_replay(
        actor_subject_id, normalized_request, operation_id=operation_id
    )
    if replay is not None:
        return _report(replay, replayed=True)

    actor = catalog._subject(actor_subject_id, field="actor_subject_id")
    con = store._read_connection()
    try:
        store._require_workspace_admin(con, normalized_request.workspace_id, actor)
    finally:
        con.close()
    if source_quiesced_confirmed is not True:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "offline lifecycle subtree requires source quiescence"
        )
    root = _rehearsal_root(rehearsal_root)
    paths = resolve_inventory_paths(
        user_data_root=user_data_root,
        source_owner_id=normalized_request.source_owner_subject_id,
        expected_source_db_path=expected_source_db_path,
        expected_catalog_db_path=expected_catalog_db_path,
    )
    if paths["root"] != root:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "offline lifecycle paths must use the exact sentinel root"
        )
    catalog_path = _resolved_existing(
        store.db_path, root=root, kind="workspace catalog"
    )
    if catalog_path != paths["catalog"]:
        raise WorkspacePageLifecycleSubtreeOrchestrationRefused(
            "offline lifecycle catalog identity changed"
        )

    command = store.prepare_page_lifecycle_command(
        actor, normalized_request, operation_id=operation_id
    )
    claimed = store.claim_page_lifecycle_command(
        actor,
        normalized_request.workspace_id,
        command["command_id"],
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        expected_command_version=int(command["command_version"]),
    )
    source_action = _source_action(normalized_request.action)
    source_arguments = {
        "action": source_action,
        "mutation_actor_subject_id": normalized_request.source_owner_subject_id,
        "root_source_page_id": normalized_request.source_page_id,
        "catalog_command_id": command["command_id"],
        "caller_operation_id": source_caller_operation_id,
        "trash_source_operation_id": trash_source_operation_id,
        "placement_mode": placement_mode,
        "fallback_parent_source_page_id": fallback_parent_source_page_id,
        "relative_source_page_id": relative_source_page_id,
        "relative_position": relative_position,
    }
    source_con = _open_source_writer(paths["source"])
    try:
        adapter = LegacyPageDuplicateTrashRestoreSourceAdapter(
            source_con,
            source_owner_id=normalized_request.source_owner_subject_id,
            expected_source_db_path=paths["source"],
            expected_source_workspace_root=paths["source"].parent,
            trusted_clock=source_trusted_clock,
        )
        receipt = adapter.get_lifecycle_replay(**source_arguments)
        if receipt is None:
            precondition = adapter.capture_precondition(
                **source_arguments, source_quiesced_confirmed=True
            )
            receipt = adapter.apply_lifecycle(
                **source_arguments,
                expected_precondition_sha256=precondition,
                source_quiesced_confirmed=True,
            )
        candidate = _capture_candidate_on_connection(
            source_con,
            store=store,
            paths=paths,
            workspace_id=normalized_request.workspace_id,
            receipt=receipt,
        )
    finally:
        source_con.close()

    transitioned_count = sum(int(item.transitioned) for item in receipt.effects)
    file_mapping_count = sum(int(item.kind == "file") for item in receipt.mappings)
    source_applied = store.record_page_lifecycle_source_applied(
        actor,
        normalized_request.workspace_id,
        command["command_id"],
        catalog.WorkspacePageLifecycleSourceReceipt(
            source_receipt_sha256=receipt.source_operation_receipt_sha256,
            affected_node_count=transitioned_count,
            file_work_count=file_mapping_count,
        ),
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=int(claimed["fencing_generation"]),
        expected_command_version=(
            int(claimed["command_version"]) - 1
            if claimed["status"] == "source_applied"
            else int(claimed["command_version"])
        ),
        source_revalidator=lambda value: (
            value.source_receipt_sha256
            == receipt.source_operation_receipt_sha256
        ),
    )

    def revalidate_source(
        expected: WorkspacePageLifecycleSubtreeCandidate,
    ) -> bool:
        recheck_con = _open_source_writer(paths["source"])
        try:
            recheck_adapter = LegacyPageDuplicateTrashRestoreSourceAdapter(
                recheck_con,
                source_owner_id=normalized_request.source_owner_subject_id,
                expected_source_db_path=paths["source"],
                expected_source_workspace_root=paths["source"].parent,
                trusted_clock=source_trusted_clock,
            )
            replayed_receipt = recheck_adapter.get_lifecycle_replay(
                **source_arguments
            )
            if replayed_receipt is None:
                return False
            recaptured = _capture_candidate_on_connection(
                recheck_con,
                store=store,
                paths=paths,
                workspace_id=normalized_request.workspace_id,
                receipt=replayed_receipt,
            )
            return recaptured == expected
        finally:
            recheck_con.close()

    result = finalize_page_lifecycle_subtree_catalog(
        store,
        actor,
        normalized_request.workspace_id,
        command["command_id"],
        candidate,
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=int(source_applied["fencing_generation"]),
        expected_command_version=int(source_applied["command_version"]),
        source_revalidator=revalidate_source,
        fault_hook=fault_hook,
    )
    return _report(result, replayed=False)


__all__ = [
    "OFFLINE_SUBTREE_REPORT_CONTRACT",
    "WorkspacePageLifecycleSubtreeOrchestrationRefused",
    "run_isolated_page_lifecycle_subtree",
]
