from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_page_tree as page_tree
from code_assist_v1.notion_legacy_change_consumer_migration import (
    apply_plan as apply_v3_plan,
    build_plan as build_v3_plan,
)
from code_assist_v1.notion_legacy_changes import (
    read_legacy_page_incarnation_receipt_v2,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore import (
    LegacyPageDuplicateTrashRestoreSourceAdapter,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    derive_lifecycle_source_operation_id,
)
from code_assist_v1.notion_workspace_inventory import _page_proof
from code_assist_v1.notion_workspace_page_lifecycle_subtree_finalize import (
    WorkspacePageLifecycleSubtreeCandidate,
    WorkspacePageLifecycleSubtreePageCandidate,
    finalize_page_lifecycle_subtree_catalog,
)
from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageLocatorProjection,
    LegacyPublicSourceLocator,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleSourceReceipt,
    sqlite_source_identity,
)
from code_assist_v1.tests.test_notion_workspace_page_tree import _prepare, _run_args


LEASE_TOKEN = "pllease_" + "c" * 32


def _binding(store, workspace_id: str, page_id: str):
    con = store._read_connection()
    try:
        row = con.execute(
            "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id=?",
            (workspace_id, page_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def _upgrade_source_v3(root: Path, source: Path, backups: Path) -> None:
    plan = build_v3_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_v3_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v3.db",
    )


def _candidate(
    prepared: dict[str, object], receipt
) -> WorkspacePageLifecycleSubtreeCandidate:
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    pages: list[WorkspacePageLifecycleSubtreePageCandidate] = []
    for effect in receipt.effects:
        page_id = effect.result_page_id
        source_receipt = read_legacy_page_incarnation_receipt_v2(
            user_data_root=prepared["root"],
            source_owner_id="owner",
            page_id=page_id,
            expected_source_db_path=prepared["source"],
            source_quiesced_confirmed=True,
        )
        con = notion_store._connect("owner")
        try:
            row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
            proof = _page_proof(
                con, actor="owner", owner="owner", page=row, role="owner"
            )
            file_ids = tuple(
                str(item[0])
                for item in con.execute(
                    "SELECT id FROM files WHERE page_id=? ORDER BY id", (page_id,)
                )
            )
            public = tuple(
                (
                    "sha256:" + hashlib.sha256(str(item[0]).encode()).hexdigest(),
                    bool(item[1]),
                )
                for item in con.execute(
                    "SELECT token,allow_duplicate FROM public_links "
                    "WHERE page_id=? ORDER BY token",
                    (page_id,),
                )
            )
        finally:
            con.close()
        binding = _binding(store, ws_id, page_id)
        version = 1 if binding is None else int(binding["row_version"]) + 1
        projection = (
            LegacyPageLocatorProjection(
                actor_subject_id="owner",
                owner_subject_id="owner",
                page_id=page_id,
                binding_version=version,
                file_sources=tuple(LegacyFileSourceLocator(item) for item in file_ids),
                public_sources=tuple(
                    LegacyPublicSourceLocator(digest, allow)
                    for digest, allow in public
                ),
            )
            if source_receipt.source_active
            else None
        )
        pages.append(
            WorkspacePageLifecycleSubtreePageCandidate(
                page_id=page_id,
                source_revision=proof.revision,
                source_updated_at=proof.updated_at,
                page_sha256=proof.content_sha256,
                incarnation_id=source_receipt.incarnation_id,
                incarnation_generation=source_receipt.incarnation_generation,
                incarnation_retired=source_receipt.incarnation_retired,
                source_epoch=source_receipt.fence.source_epoch,
                full_sweep_generation=source_receipt.fence.full_sweep_generation,
                source_device=sqlite_source_identity(source_receipt.fence.device),
                source_inode=sqlite_source_identity(source_receipt.fence.inode),
                source_link_count=source_receipt.fence.link_count,
                change_generation=source_receipt.generation,
                change_mask=source_receipt.mask,
                source_exists=source_receipt.source_exists,
                source_active=source_receipt.source_active,
                receipt_sha256=source_receipt.receipt_sha256,
                source_proof=proof if source_receipt.source_active else None,
                locator_projection=projection,
            )
        )
    return WorkspacePageLifecycleSubtreeCandidate(
        receipt=receipt,
        pages=tuple(sorted(pages, key=lambda item: item.page_id)),
    )


def _apply_full_lifecycle(
    prepared: dict[str, object],
    *,
    action: str,
    tag: str,
    trash_source_operation_id: str = "",
    placement_mode: str = "",
    candidate_observer=None,
    fault_hook=None,
):
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    root = str(prepared["root_a"])
    binding = _binding(store, ws_id, root)
    con = store._read_connection()
    try:
        tree = dict(
            con.execute(
                "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
                (ws_id,),
            ).fetchone()
        )
        node = dict(
            con.execute(
                "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
                "AND source_owner_subject_id='owner' AND source_page_id=?",
                (ws_id, root),
            ).fetchone()
        )
    finally:
        con.close()
    workspace = store.get_workspace("reviewer", ws_id)
    operation_id = f"catalog-{tag}"
    command = store.prepare_page_lifecycle_command(
        "reviewer",
        WorkspacePageLifecycleCommandRequest(
            workspace_id=ws_id,
            action=action,
            source_owner_subject_id="owner",
            source_page_id=root,
            expected_tree_version=int(tree["tree_version"]),
            expected_binding_version=int(binding["row_version"]),
            expected_incarnation_generation=int(
                node["source_incarnation_generation"]
            ),
            base_version=int(workspace["version"]),
        ),
        operation_id=operation_id,
    )
    claimed = store.claim_page_lifecycle_command(
        "reviewer", ws_id, command["command_id"], operation_id=operation_id,
        worker="worker.1", lease_token=LEASE_TOKEN, lease_seconds=120,
        expected_command_version=1,
    )
    source_action = "trash" if action == "archive" else action
    source_caller = f"source-{tag}"
    source_con = notion_store._configure_connection(
        sqlite3.connect(prepared["source"]), query_only=False
    )
    try:
        next_source_time = int(
            source_con.execute("SELECT MAX(updated_at) FROM pages").fetchone()[0]
        ) + 10
        adapter = LegacyPageDuplicateTrashRestoreSourceAdapter(
            source_con,
            source_owner_id="owner",
            expected_source_db_path=prepared["source"],
            expected_source_workspace_root=Path(prepared["source"]).parent,
            trusted_clock=lambda: next_source_time,
        )
        source_arguments = {
            "action": source_action,
            "mutation_actor_subject_id": "owner",
            "root_source_page_id": root,
            "catalog_command_id": command["command_id"],
            "caller_operation_id": source_caller,
            "source_quiesced_confirmed": True,
            "trash_source_operation_id": trash_source_operation_id,
            "placement_mode": placement_mode,
        }
        precondition = adapter.capture_precondition(**source_arguments)
        receipt = adapter.apply_lifecycle(
            **source_arguments, expected_precondition_sha256=precondition
        )
    finally:
        source_con.close()
    transitioned = sum(int(item.transitioned) for item in receipt.effects)
    file_work_count = sum(int(item.kind == "file") for item in receipt.mappings)
    source_applied = store.record_page_lifecycle_source_applied(
        "reviewer", ws_id, command["command_id"],
        WorkspacePageLifecycleSourceReceipt(
            source_receipt_sha256=receipt.source_operation_receipt_sha256,
            affected_node_count=transitioned,
            file_work_count=file_work_count,
        ),
        operation_id=operation_id, worker="worker.1", lease_token=LEASE_TOKEN,
        fencing_generation=int(claimed["fencing_generation"]),
        expected_command_version=int(claimed["command_version"]),
        source_revalidator=lambda _value: True,
    )
    candidate = _candidate(prepared, receipt)
    if candidate_observer is not None:
        candidate_observer(candidate)
    result = finalize_page_lifecycle_subtree_catalog(
        store, "reviewer", ws_id, command["command_id"], candidate,
        operation_id=operation_id, worker="worker.1", lease_token=LEASE_TOKEN,
        fencing_generation=int(source_applied["fencing_generation"]),
        expected_command_version=int(source_applied["command_version"]),
        source_revalidator=lambda value: value == candidate,
        fault_hook=fault_hook,
    )
    source_operation_id = derive_lifecycle_source_operation_id(
        action=source_action,
        catalog_command_id=command["command_id"],
        caller_operation_id=source_caller,
    )
    return result, receipt, source_operation_id


def test_trash_finalizer_commits_full_effect_set_and_replays_before_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    page_tree.run_offline_page_tree_materialization(
        prepared["store"], **_run_args(prepared)
    )
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    root = str(prepared["root_a"])
    binding = _binding(store, ws_id, root)
    con = store._read_connection()
    try:
        tree = dict(
            con.execute(
                "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
                (ws_id,),
            ).fetchone()
        )
    finally:
        con.close()
    workspace = store.get_workspace("reviewer", ws_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=ws_id,
        action="archive",
        source_owner_subject_id="owner",
        source_page_id=root,
        expected_tree_version=int(tree["tree_version"]),
        expected_binding_version=int(binding["row_version"]),
        expected_incarnation_generation=int(
            next(
                item["source_incarnation_generation"]
                for item in prepared["context"]["bindings"]
                if item["source_page_id"] == root
            )
        ),
        base_version=int(workspace["version"]),
    )
    operation_id = "catalog-trash-subtree-1"
    command = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    claimed = store.claim_page_lifecycle_command(
        "reviewer", ws_id, command["command_id"], operation_id=operation_id,
        worker="worker.1", lease_token=LEASE_TOKEN, lease_seconds=120,
        expected_command_version=1,
    )

    source_con = notion_store._configure_connection(
        sqlite3.connect(prepared["source"]), query_only=False
    )
    try:
        adapter = LegacyPageDuplicateTrashRestoreSourceAdapter(
            source_con,
            source_owner_id="owner",
            expected_source_db_path=prepared["source"],
            expected_source_workspace_root=Path(prepared["source"]).parent,
            trusted_clock=lambda: 2_000_000_001,
        )
        precondition = adapter.capture_precondition(
            action="trash", mutation_actor_subject_id="owner",
            root_source_page_id=root, catalog_command_id=command["command_id"],
            caller_operation_id="source-trash-subtree-1",
            source_quiesced_confirmed=True,
        )
        receipt = adapter.apply_lifecycle(
            action="trash", mutation_actor_subject_id="owner",
            root_source_page_id=root, catalog_command_id=command["command_id"],
            caller_operation_id="source-trash-subtree-1",
            expected_precondition_sha256=precondition,
            source_quiesced_confirmed=True,
        )
    finally:
        source_con.close()
    transitioned = sum(int(item.transitioned) for item in receipt.effects)
    source_applied = store.record_page_lifecycle_source_applied(
        "reviewer", ws_id, command["command_id"],
        WorkspacePageLifecycleSourceReceipt(
            source_receipt_sha256=receipt.source_operation_receipt_sha256,
            affected_node_count=transitioned,
            file_work_count=0,
        ),
        operation_id=operation_id, worker="worker.1", lease_token=LEASE_TOKEN,
        fencing_generation=int(claimed["fencing_generation"]),
        expected_command_version=int(claimed["command_version"]),
        source_revalidator=lambda _value: True,
    )
    candidate = _candidate(prepared, receipt)
    result = finalize_page_lifecycle_subtree_catalog(
        store, "reviewer", ws_id, command["command_id"], candidate,
        operation_id=operation_id, worker="worker.1", lease_token=LEASE_TOKEN,
        fencing_generation=int(source_applied["fencing_generation"]),
        expected_command_version=int(source_applied["command_version"]),
        source_revalidator=lambda value: value == candidate,
    )
    assert result["canonical_action"] == "trash"
    assert result["transitioned_count"] == transitioned == 2
    assert result["effect_count"] > transitioned
    assert result["archived_node_count"] == transitioned
    assert store.verify_deep_health()["healthy"] is True

    source_calls = 0

    def no_source(_value):
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("source callback")

    assert finalize_page_lifecycle_subtree_catalog(
        store, "reviewer", ws_id, command["command_id"], candidate,
        operation_id=operation_id, worker="worker.1", lease_token=LEASE_TOKEN,
        fencing_generation=int(source_applied["fencing_generation"]),
        expected_command_version=int(source_applied["command_version"]),
        source_revalidator=no_source,
    ) == result
    assert source_calls == 0


def test_duplicate_finalizer_creates_bindings_and_dense_tree_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    page_tree.run_offline_page_tree_materialization(
        prepared["store"], **_run_args(prepared)
    )
    result, receipt, _source_operation_id = _apply_full_lifecycle(
        prepared, action="duplicate", tag="duplicate-subtree-1"
    )
    transitioned = [item for item in receipt.effects if item.transitioned]
    assert result["canonical_action"] == "duplicate"
    assert result["transitioned_count"] == len(transitioned) == 2
    assert result["effect_count"] > len(transitioned)
    assert result["mapping_count"] >= len(transitioned)
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    con = store._read_connection()
    try:
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (ws_id,),
        ).fetchone()
        result_ids = tuple(item.result_page_id for item in transitioned)
        assert int(state["active_node_count"]) == 5
        assert int(state["archived_node_count"]) == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id IN (?,?) "
            "AND state='active' AND row_version=1",
            (ws_id, *result_ids),
        ).fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id IN (?,?) "
            "AND lifecycle_state='active' AND canonical_lifecycle_state='active'",
            (ws_id, *result_ids),
        ).fetchone()[0] == 2
    finally:
        con.close()
    assert store.verify_deep_health()["healthy"] is True


def test_restore_finalizer_reactivates_exact_prior_trash_cohort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    page_tree.run_offline_page_tree_materialization(
        prepared["store"], **_run_args(prepared)
    )
    trash_result, trash_receipt, trash_source_operation_id = _apply_full_lifecycle(
        prepared, action="archive", tag="trash-before-restore"
    )
    restore_result, restore_receipt, _restore_source_operation_id = (
        _apply_full_lifecycle(
            prepared,
            action="restore",
            tag="restore-subtree-1",
            trash_source_operation_id=trash_source_operation_id,
            placement_mode="original",
        )
    )
    trashed = {
        item.result_page_id for item in trash_receipt.effects if item.transitioned
    }
    restored = {
        item.result_page_id for item in restore_receipt.effects if item.transitioned
    }
    assert trash_result["canonical_action"] == "trash"
    assert restore_result["canonical_action"] == "restore"
    assert restored == trashed
    assert restore_result["transitioned_count"] == len(restored) == 2
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    con = store._read_connection()
    try:
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (ws_id,),
        ).fetchone()
        assert int(state["active_node_count"]) == 3
        assert int(state["archived_node_count"]) == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id IN (?,?) "
            "AND state='active' AND quarantine_reason IS NULL",
            (ws_id, *tuple(sorted(restored))),
        ).fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id IN (?,?) "
            "AND lifecycle_state='active' AND canonical_lifecycle_state='active'",
            (ws_id, *tuple(sorted(restored))),
        ).fetchone()[0] == 2
    finally:
        con.close()
    assert store.verify_deep_health()["healthy"] is True


@pytest.mark.parametrize(
    "fault_phase", ("after_evidence", "after_bindings", "after_tree")
)
def test_duplicate_finalizer_fault_rolls_back_all_catalog_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_phase: str
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    con = store._read_connection()
    try:
        before_workspace = tuple(
            con.execute(
                "SELECT row_version,security_epoch FROM workspaces WHERE id=?",
                (ws_id,),
            ).fetchone()
        )
        before_tree = tuple(
            con.execute(
                "SELECT tree_version,row_version,active_node_count,"
                "archived_node_count,source_fence_set_sha256 "
                "FROM workspace_page_tree_states WHERE workspace_id=?",
                (ws_id,),
            ).fetchone()
        )
        before_bindings = tuple(
            tuple(row)
            for row in con.execute(
                "SELECT source_page_id,row_version,state FROM workspace_page_sources "
                "WHERE workspace_id=? ORDER BY source_page_id",
                (ws_id,),
            )
        )
    finally:
        con.close()
    observed: dict[str, WorkspacePageLifecycleSubtreeCandidate] = {}

    def stop_at_catalog_boundary(phase: str) -> None:
        if phase == fault_phase:
            raise RuntimeError("catalog fault")

    with pytest.raises(RuntimeError, match="catalog fault"):
        _apply_full_lifecycle(
            prepared,
            action="duplicate",
            tag="duplicate-fault-1",
            candidate_observer=lambda candidate: observed.setdefault(
                "candidate", candidate
            ),
            fault_hook=stop_at_catalog_boundary,
        )
    candidate = observed["candidate"]
    duplicate_ids = tuple(
        effect.result_page_id
        for effect in candidate.receipt.effects
        if effect.transitioned
    )
    con = store._read_connection()
    try:
        assert tuple(
            con.execute(
                "SELECT row_version,security_epoch FROM workspaces WHERE id=?",
                (ws_id,),
            ).fetchone()
        ) == before_workspace
        assert tuple(
            con.execute(
                "SELECT tree_version,row_version,active_node_count,"
                "archived_node_count,source_fence_set_sha256 "
                "FROM workspace_page_tree_states WHERE workspace_id=?",
                (ws_id,),
            ).fetchone()
        ) == before_tree
        assert tuple(
            tuple(row)
            for row in con.execute(
                "SELECT source_page_id,row_version,state FROM workspace_page_sources "
                "WHERE workspace_id=? ORDER BY source_page_id",
                (ws_id,),
            )
        ) == before_bindings
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_page_id IN (?,?)",
            (ws_id, *duplicate_ids),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_subtree_manifests"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_subtree_effects"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_object_mappings"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_commands "
            "WHERE status='source_applied' AND action='duplicate'"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations "
            "WHERE action='workspace.page_lifecycle.finalized'"
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert store.verify_deep_health()["healthy"] is True


def test_unconfigured_or_unscoped_raw_writer_cannot_enter_subtree_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch)
    store = prepared["store"]
    ws_id = str(prepared["workspace_id"])
    page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    raw = sqlite3.connect(store.db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such function"):
            raw.execute(
                "UPDATE workspace_page_tree_nodes "
                "SET order_generation=order_generation+1,row_version=row_version+1,"
                "updated_at=updated_at+1,last_actor_subject_id='reviewer',"
                "last_operation_id='raw-subtree-write' "
                "WHERE workspace_id=? AND source_page_id=?",
                (ws_id, prepared["root_a"]),
            )
        raw.rollback()
    finally:
        raw.close()
    configured = store._write_connection()
    try:
        configured.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            configured.execute(
                "UPDATE workspace_page_tree_nodes "
                "SET order_generation=order_generation+1,row_version=row_version+1,"
                "updated_at=updated_at+1,last_actor_subject_id='reviewer',"
                "last_operation_id='raw-subtree-write' "
                "WHERE workspace_id=? AND source_page_id=?",
                (ws_id, prepared["root_a"]),
            )
        configured.rollback()
    finally:
        configured.close()
    assert store.verify_deep_health()["healthy"] is True
