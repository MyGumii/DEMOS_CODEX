from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from code_assist_v1.notion_legacy_change_contract import CHANGE_ALL
from code_assist_v1.notion_legacy_changes import legacy_incarnation_receipt_sha256
from code_assist_v1.notion_workspace_store import (
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    PAGE_LIFECYCLE_EFFECT_NEW_PARENT,
    PAGE_LIFECYCLE_EFFECT_NEW_SCOPE,
    PAGE_LIFECYCLE_EFFECT_OLD_PARENT,
    PAGE_LIFECYCLE_EFFECT_OLD_SCOPE,
    PAGE_LIFECYCLE_EFFECT_SUBTREE,
    PAGE_LIFECYCLE_EFFECT_TARGET,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleLeaseConflict,
    WorkspacePageLifecycleSourceReceipt,
    WorkspacePageLifecycleStructuralCandidate,
    WorkspacePageLifecycleStructuralEffectCandidate,
    WorkspacePageTreeSourceFence,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    LEASE_TOKEN,
    _materialized,
)


SHA_SOURCE_REQUEST = "sha256:" + "c" * 64
SHA_SOURCE_RECEIPT = "sha256:" + "d" * 64
SHA_POST_MANIFEST = "sha256:" + "e" * 64


def _current_effect(
    store, workspace_id: str, page_id: str, *, ordinal: int, new_ordinal: int
):
    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id=?",
            (workspace_id, page_id),
        ).fetchone()
        node = con.execute(
            "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id=?",
            (workspace_id, page_id),
        ).fetchone()
        attestation = con.execute(
            "SELECT * FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id='owner' "
            "AND source_page_id=? AND binding_version=?",
            (workspace_id, page_id, int(binding["row_version"])),
        ).fetchone()
    finally:
        con.close()
    evidence = LegacyPageIncarnationEvidence(
        owner_subject_id="owner",
        page_id=page_id,
        incarnation_id=str(attestation["source_incarnation_id"]),
        incarnation_generation=int(attestation["source_incarnation_generation"]),
        incarnation_retired=False,
        source_epoch=int(attestation["source_epoch"]),
        full_sweep_generation=int(attestation["full_sweep_generation"]),
        source_device=int(attestation["source_device"]),
        source_inode=int(attestation["source_inode"]),
        source_link_count=int(attestation["source_link_count"]),
        change_generation=int(attestation["source_change_generation"]),
        change_mask=int(attestation["source_change_mask"]),
        source_exists=True,
        source_active=True,
        receipt_sha256=str(attestation["source_receipt_sha256"]),
    )
    return WorkspacePageLifecycleStructuralEffectCandidate(
        effect_ordinal=ordinal,
        effect_roles=PAGE_LIFECYCLE_EFFECT_NEW_SCOPE,
        source_mutated=False,
        source_proof=LegacyPageSourceProof(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=page_id,
            revision=int(binding["source_revision"]),
            updated_at=int(binding["source_updated_at"]),
            content_sha256=str(binding["source_sha256"]),
        ),
        source_incarnation=evidence,
        locator_projection=LegacyPageLocatorProjection(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=page_id,
            binding_version=int(binding["row_version"]),
        ),
        old_parent_source_page_id=str(node["parent_source_page_id"] or ""),
        new_parent_source_page_id="",
        old_sibling_ordinal=int(node["sibling_ordinal"]),
        new_sibling_ordinal=new_ordinal,
        old_depth=int(node["depth"]),
        new_depth=0,
    )


def _prepare_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prepared, rename_request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    workspace_id = rename_request.workspace_id
    con = store._read_connection()
    try:
        root = con.execute(
            "SELECT source_page_id FROM workspace_page_tree_nodes "
            "WHERE workspace_id=? AND lifecycle_state='active' "
            "AND parent_resolution='root' ORDER BY sibling_ordinal",
            (workspace_id,),
        ).fetchall()
        assert root
        root_pages = tuple(str(row[0]) for row in root)
        root_page = root_pages[0]
        attestation = con.execute(
            "SELECT * FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id='owner' "
            "AND source_page_id=? ORDER BY binding_version DESC LIMIT 1",
            (workspace_id, root_page),
        ).fetchone()
    finally:
        con.close()
    target_page = "p_" + "9" * 32
    workspace = store.get_workspace("reviewer", workspace_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="create",
        source_owner_subject_id="owner",
        source_page_id=target_page,
        expected_tree_version=1,
        expected_binding_version=0,
        expected_incarnation_generation=0,
        base_version=int(workspace["version"]),
    )
    operation_id = "structural-create-finalize"
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    store.claim_page_lifecycle_command(
        "reviewer",
        workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    )
    source_receipt = WorkspacePageLifecycleSourceReceipt(
        source_receipt_sha256=SHA_SOURCE_RECEIPT,
        affected_node_count=1,
        file_work_count=0,
    )
    store.record_page_lifecycle_source_applied(
        "reviewer",
        workspace_id,
        created["command_id"],
        source_receipt,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=lambda value: value == source_receipt,
    )

    current_epoch = int(attestation["source_epoch"]) + 1
    current_sweep = int(attestation["full_sweep_generation"]) + 1
    target_receipt = legacy_incarnation_receipt_sha256(
        source_owner_id="owner",
        page_id=target_page,
        incarnation_id="inc_" + "8" * 32,
        incarnation_generation=1,
        incarnation_retired=False,
        change_generation=1,
        change_mask=CHANGE_ALL,
        source_exists=True,
        source_active=True,
        source_epoch=current_epoch,
        full_sweep_generation=current_sweep,
        source_device=int(attestation["source_device"]),
        source_inode=int(attestation["source_inode"]),
        source_link_count=int(attestation["source_link_count"]),
    )
    target = WorkspacePageLifecycleStructuralEffectCandidate(
        effect_ordinal=len(root_pages) + 1,
        effect_roles=(
            PAGE_LIFECYCLE_EFFECT_TARGET
            | PAGE_LIFECYCLE_EFFECT_NEW_SCOPE
            | PAGE_LIFECYCLE_EFFECT_SUBTREE
        ),
        source_mutated=True,
        source_proof=LegacyPageSourceProof(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=target_page,
            revision=1,
            updated_at=int(attestation["created_at"]) + 1,
            content_sha256="sha256:" + hashlib.sha256(b"created-page").hexdigest(),
        ),
        source_incarnation=LegacyPageIncarnationEvidence(
            owner_subject_id="owner",
            page_id=target_page,
            incarnation_id="inc_" + "8" * 32,
            incarnation_generation=1,
            incarnation_retired=False,
            source_epoch=current_epoch,
            full_sweep_generation=current_sweep,
            source_device=int(attestation["source_device"]),
            source_inode=int(attestation["source_inode"]),
            source_link_count=int(attestation["source_link_count"]),
            change_generation=1,
            change_mask=CHANGE_ALL,
            source_exists=True,
            source_active=True,
            receipt_sha256=target_receipt,
        ),
        locator_projection=LegacyPageLocatorProjection(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=target_page,
            binding_version=1,
        ),
        old_parent_source_page_id="",
        new_parent_source_page_id="",
        old_sibling_ordinal=None,
        new_sibling_ordinal=len(root_pages) + 1,
        old_depth=None,
        new_depth=0,
    )
    siblings = tuple(
        _current_effect(
            store,
            workspace_id,
            page_id,
            ordinal=index,
            new_ordinal=index,
        )
        for index, page_id in enumerate(root_pages, start=1)
    )
    candidate = WorkspacePageLifecycleStructuralCandidate(
        action="create",
        source_request_sha256=SHA_SOURCE_REQUEST,
        source_operation_receipt_sha256=SHA_SOURCE_RECEIPT,
        post_manifest_sha256=SHA_POST_MANIFEST,
        source_changed=True,
        structure_changed=True,
        source_fence=WorkspacePageTreeSourceFence(
            source_owner_subject_id="owner",
            source_epoch=current_epoch,
            full_sweep_generation=current_sweep,
            source_device=int(attestation["source_device"]),
            source_inode=int(attestation["source_inode"]),
            source_link_count=int(attestation["source_link_count"]),
        ),
        target_incarnation_id=target.source_incarnation.incarnation_id,
        target_incarnation_generation=1,
        target_change_generation=1,
        target_change_mask=CHANGE_ALL,
        target_source_receipt_sha256=target_receipt,
        old_parent_source_page_id="",
        new_parent_source_page_id="",
        target_dense_ordinal=len(root_pages) + 1,
        subtree_count=1,
        old_scope_count=0,
        new_scope_count=len(root_pages) + 1,
        effects=(*siblings, target),
    )
    return prepared, request, created, operation_id, candidate


def _begin_structural_command(store, request, *, operation_id: str):
    created = store.prepare_page_lifecycle_command(
        "reviewer", request, operation_id=operation_id
    )
    store.claim_page_lifecycle_command(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        expected_command_version=1,
    )
    receipt = WorkspacePageLifecycleSourceReceipt(
        source_receipt_sha256=SHA_SOURCE_RECEIPT,
        affected_node_count=1,
        file_work_count=0,
    )
    store.record_page_lifecycle_source_applied(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        receipt,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=2,
        source_revalidator=lambda value: value == receipt,
    )
    return created


def _next_source_fence(store, workspace_id: str) -> WorkspacePageTreeSourceFence:
    con = store._read_connection()
    try:
        row = con.execute(
            "SELECT source_device,source_inode,source_link_count,"
            "MAX(source_epoch),MAX(full_sweep_generation) "
            "FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id='owner'",
            (workspace_id,),
        ).fetchone()
    finally:
        con.close()
    return WorkspacePageTreeSourceFence(
        source_owner_subject_id="owner",
        source_epoch=int(row[3]) + 1,
        full_sweep_generation=int(row[4]) + 1,
        source_device=int(row[0]),
        source_inode=int(row[1]),
        source_link_count=int(row[2]),
    )


def _mutated_effect(
    store,
    workspace_id: str,
    page_id: str,
    *,
    source_fence: WorkspacePageTreeSourceFence,
    effect_ordinal: int,
    effect_roles: int,
    old_parent: str,
    new_parent: str,
    old_ordinal: int,
    new_ordinal: int,
    old_depth: int,
    new_depth: int,
) -> WorkspacePageLifecycleStructuralEffectCandidate:
    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id=?",
            (workspace_id, page_id),
        ).fetchone()
        attestation = con.execute(
            "SELECT * FROM workspace_page_source_incarnation_attestations "
            "WHERE workspace_id=? AND source_owner_subject_id='owner' "
            "AND source_page_id=? AND binding_version=?",
            (workspace_id, page_id, int(binding["row_version"])),
        ).fetchone()
    finally:
        con.close()
    change_generation = int(attestation["source_change_generation"]) + 1
    receipt_sha256 = legacy_incarnation_receipt_sha256(
        source_owner_id="owner",
        page_id=page_id,
        incarnation_id=str(attestation["source_incarnation_id"]),
        incarnation_generation=int(attestation["source_incarnation_generation"]),
        incarnation_retired=False,
        change_generation=change_generation,
        change_mask=int(attestation["source_change_mask"]),
        source_exists=True,
        source_active=True,
        source_epoch=source_fence.source_epoch,
        full_sweep_generation=source_fence.full_sweep_generation,
        source_device=source_fence.source_device,
        source_inode=source_fence.source_inode,
        source_link_count=source_fence.source_link_count,
    )
    return WorkspacePageLifecycleStructuralEffectCandidate(
        effect_ordinal=effect_ordinal,
        effect_roles=effect_roles,
        source_mutated=True,
        source_proof=LegacyPageSourceProof(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=page_id,
            revision=int(binding["source_revision"]) + 1,
            updated_at=int(binding["source_updated_at"]) + 1,
            content_sha256="sha256:" + hashlib.sha256(
                f"{page_id}:{int(binding['source_revision']) + 1}".encode("utf-8")
            ).hexdigest(),
        ),
        source_incarnation=LegacyPageIncarnationEvidence(
            owner_subject_id="owner",
            page_id=page_id,
            incarnation_id=str(attestation["source_incarnation_id"]),
            incarnation_generation=int(attestation["source_incarnation_generation"]),
            incarnation_retired=False,
            source_epoch=source_fence.source_epoch,
            full_sweep_generation=source_fence.full_sweep_generation,
            source_device=source_fence.source_device,
            source_inode=source_fence.source_inode,
            source_link_count=source_fence.source_link_count,
            change_generation=change_generation,
            change_mask=int(attestation["source_change_mask"]),
            source_exists=True,
            source_active=True,
            receipt_sha256=receipt_sha256,
        ),
        locator_projection=LegacyPageLocatorProjection(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id=page_id,
            binding_version=int(binding["row_version"]) + 1,
        ),
        old_parent_source_page_id=old_parent,
        new_parent_source_page_id=new_parent,
        old_sibling_ordinal=old_ordinal,
        new_sibling_ordinal=new_ordinal,
        old_depth=old_depth,
        new_depth=new_depth,
    )


def test_structural_create_finalizes_atomically_and_replays_before_source_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, created, operation_id, candidate = _prepare_create(
        tmp_path, monkeypatch
    )
    store = prepared["store"]
    result = store.finalize_page_lifecycle_structural_catalog(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        candidate,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=3,
        source_revalidator=lambda value: value == candidate,
    )
    assert result["contract"] == "np2c_page_lifecycle_structural_finalize_v4"
    assert result["action"] == "create"
    assert result["affected_page_count"] == candidate.new_scope_count
    assert result["source_mutated_page_count"] == 1
    assert result["tree_version"] == result["previous_tree_version"] + 1
    assert result["materialization_state"] == "partial"

    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT state,row_version,source_revision FROM workspace_page_sources "
            "WHERE workspace_id=? AND source_owner_subject_id='owner' "
            "AND source_page_id=?",
            (request.workspace_id, request.source_page_id),
        ).fetchone()
        assert tuple(binding) == ("active", 1, 1)
        nodes = con.execute(
            "SELECT source_page_id,sibling_ordinal,order_generation "
            "FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND lifecycle_state='active' AND parent_resolution='root' "
            "ORDER BY sibling_ordinal",
            (request.workspace_id,),
        ).fetchall()
        assert [(row[1], row[2]) for row in nodes] == [
            (index, 2) for index in range(1, candidate.new_scope_count + 1)
        ]
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_structural_effects "
            "WHERE command_id=?",
            (created["command_id"],),
        ).fetchone()[0] == candidate.new_scope_count
    finally:
        con.close()
    health = store.deep_health()
    assert health["healthy"] is True, health["issues"]

    replay = store.finalize_page_lifecycle_structural_catalog(
        "reviewer",
        request.workspace_id,
        created["command_id"],
        candidate,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=3,
        source_revalidator=lambda _value: (_ for _ in ()).throw(
            AssertionError("source revalidation ran during replay")
        ),
    )
    assert replay == result


def test_structural_reorder_advances_the_complete_sibling_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _rename_request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    root_a = str(prepared["root_a"])
    root_b = str(prepared["root_b"])
    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT ps.row_version,a.source_incarnation_generation "
            "FROM workspace_page_sources ps "
            "JOIN workspace_page_source_incarnation_attestations a "
            "ON a.workspace_id=ps.workspace_id "
            "AND a.source_owner_subject_id=ps.source_owner_subject_id "
            "AND a.source_page_id=ps.source_page_id "
            "AND a.binding_version=ps.row_version "
            "WHERE ps.workspace_id=? AND ps.source_owner_subject_id='owner' "
            "AND ps.source_page_id=?",
            (workspace_id, root_b),
        ).fetchone()
    finally:
        con.close()
    workspace = store.get_workspace("reviewer", workspace_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="reorder",
        source_owner_subject_id="owner",
        source_page_id=root_b,
        expected_tree_version=1,
        expected_binding_version=int(binding["row_version"]),
        expected_incarnation_generation=int(
            binding["source_incarnation_generation"]
        ),
        base_version=int(workspace["version"]),
        relative_source_owner_subject_id="owner",
        relative_source_page_id=root_a,
        relative_position="before",
    )
    operation_id = "structural-reorder-finalize"
    created = _begin_structural_command(
        store, request, operation_id=operation_id
    )
    source_fence = _next_source_fence(store, workspace_id)
    other = replace(
        _current_effect(
            store, workspace_id, root_a, ordinal=1, new_ordinal=2
        ),
        effect_roles=(
            PAGE_LIFECYCLE_EFFECT_OLD_SCOPE
            | PAGE_LIFECYCLE_EFFECT_NEW_SCOPE
        ),
    )
    target = _mutated_effect(
        store,
        workspace_id,
        root_b,
        source_fence=source_fence,
        effect_ordinal=2,
        effect_roles=(
            PAGE_LIFECYCLE_EFFECT_TARGET
            | PAGE_LIFECYCLE_EFFECT_OLD_SCOPE
            | PAGE_LIFECYCLE_EFFECT_NEW_SCOPE
            | PAGE_LIFECYCLE_EFFECT_SUBTREE
        ),
        old_parent="",
        new_parent="",
        old_ordinal=2,
        new_ordinal=1,
        old_depth=0,
        new_depth=0,
    )
    candidate = WorkspacePageLifecycleStructuralCandidate(
        action="reorder",
        source_request_sha256=SHA_SOURCE_REQUEST,
        source_operation_receipt_sha256=SHA_SOURCE_RECEIPT,
        post_manifest_sha256=SHA_POST_MANIFEST,
        source_changed=True,
        structure_changed=True,
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
        old_parent_source_page_id="",
        new_parent_source_page_id="",
        target_dense_ordinal=1,
        subtree_count=1,
        old_scope_count=2,
        new_scope_count=2,
        effects=(other, target),
    )
    result = store.finalize_page_lifecycle_structural_catalog(
        "reviewer",
        workspace_id,
        created["command_id"],
        candidate,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=3,
        source_revalidator=lambda value: value == candidate,
    )
    assert result["action"] == "reorder"
    con = store._read_connection()
    try:
        rows = con.execute(
            "SELECT source_page_id,sibling_ordinal,order_generation "
            "FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND parent_resolution='root' ORDER BY sibling_ordinal",
            (workspace_id,),
        ).fetchall()
        assert [(str(row[0]), int(row[1]), int(row[2])) for row in rows] == [
            (root_b, 1, 2),
            (root_a, 2, 2),
        ]
    finally:
        con.close()
    assert store.deep_health()["healthy"] is True


def test_structural_move_keeps_unchanged_parent_order_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _rename_request = _materialized(tmp_path, monkeypatch)
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    root_a = str(prepared["root_a"])
    root_b = str(prepared["root_b"])
    child = str(prepared["child"])
    con = store._read_connection()
    try:
        binding = con.execute(
            "SELECT ps.row_version,a.source_incarnation_generation "
            "FROM workspace_page_sources ps "
            "JOIN workspace_page_source_incarnation_attestations a "
            "ON a.workspace_id=ps.workspace_id "
            "AND a.source_owner_subject_id=ps.source_owner_subject_id "
            "AND a.source_page_id=ps.source_page_id "
            "AND a.binding_version=ps.row_version "
            "WHERE ps.workspace_id=? AND ps.source_owner_subject_id='owner' "
            "AND ps.source_page_id=?",
            (workspace_id, child),
        ).fetchone()
    finally:
        con.close()
    workspace = store.get_workspace("reviewer", workspace_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="move",
        source_owner_subject_id="owner",
        source_page_id=child,
        expected_tree_version=1,
        expected_binding_version=int(binding["row_version"]),
        expected_incarnation_generation=int(
            binding["source_incarnation_generation"]
        ),
        base_version=int(workspace["version"]),
        parent_source_owner_subject_id="owner",
        parent_source_page_id=root_b,
    )
    operation_id = "structural-move-finalize"
    created = _begin_structural_command(
        store, request, operation_id=operation_id
    )
    source_fence = _next_source_fence(store, workspace_id)
    old_parent = _mutated_effect(
        store,
        workspace_id,
        root_a,
        source_fence=source_fence,
        effect_ordinal=1,
        effect_roles=PAGE_LIFECYCLE_EFFECT_OLD_PARENT,
        old_parent="",
        new_parent="",
        old_ordinal=1,
        new_ordinal=1,
        old_depth=0,
        new_depth=0,
    )
    new_parent = _mutated_effect(
        store,
        workspace_id,
        root_b,
        source_fence=source_fence,
        effect_ordinal=2,
        effect_roles=PAGE_LIFECYCLE_EFFECT_NEW_PARENT,
        old_parent="",
        new_parent="",
        old_ordinal=2,
        new_ordinal=2,
        old_depth=0,
        new_depth=0,
    )
    target = _mutated_effect(
        store,
        workspace_id,
        child,
        source_fence=source_fence,
        effect_ordinal=3,
        effect_roles=(
            PAGE_LIFECYCLE_EFFECT_TARGET
            | PAGE_LIFECYCLE_EFFECT_OLD_SCOPE
            | PAGE_LIFECYCLE_EFFECT_NEW_SCOPE
            | PAGE_LIFECYCLE_EFFECT_SUBTREE
        ),
        old_parent=root_a,
        new_parent=root_b,
        old_ordinal=1,
        new_ordinal=1,
        old_depth=1,
        new_depth=1,
    )
    candidate = WorkspacePageLifecycleStructuralCandidate(
        action="move",
        source_request_sha256=SHA_SOURCE_REQUEST,
        source_operation_receipt_sha256=SHA_SOURCE_RECEIPT,
        post_manifest_sha256=SHA_POST_MANIFEST,
        source_changed=True,
        structure_changed=True,
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
        old_parent_source_page_id=root_a,
        new_parent_source_page_id=root_b,
        target_dense_ordinal=1,
        subtree_count=1,
        old_scope_count=1,
        new_scope_count=1,
        effects=(old_parent, new_parent, target),
    )
    result = store.finalize_page_lifecycle_structural_catalog(
        "reviewer",
        workspace_id,
        created["command_id"],
        candidate,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        fencing_generation=1,
        expected_command_version=3,
        source_revalidator=lambda value: value == candidate,
    )
    assert result["action"] == "move"
    con = store._read_connection()
    try:
        target_row = con.execute(
            "SELECT parent_source_page_id,sibling_ordinal,order_generation "
            "FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_page_id=?",
            (workspace_id, child),
        ).fetchone()
        assert tuple(target_row) == (root_b, 1, 2)
        parents = con.execute(
            "SELECT source_page_id,order_generation FROM workspace_page_tree_nodes "
            "WHERE workspace_id=? AND source_page_id IN (?,?) "
            "ORDER BY source_page_id",
            (workspace_id, root_a, root_b),
        ).fetchall()
        assert {str(row[0]): int(row[1]) for row in parents} == {
            root_a: 1,
            root_b: 1,
        }
    finally:
        con.close()
    assert store.deep_health()["healthy"] is True


def test_structural_finalize_rolls_back_every_catalog_effect_on_late_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, created, operation_id, candidate = _prepare_create(
        tmp_path, monkeypatch
    )
    store = prepared["store"]

    def fail_workspace_bump(*_args, **_kwargs):
        raise RuntimeError("injected late structural failure")

    monkeypatch.setattr(store, "_bump_workspace", fail_workspace_bump)
    with pytest.raises(RuntimeError, match="injected late structural failure"):
        store.finalize_page_lifecycle_structural_catalog(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            candidate,
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=lambda value: value == candidate,
        )
    con = store._read_connection()
    try:
        command = con.execute(
            "SELECT status,row_version FROM workspace_page_lifecycle_commands "
            "WHERE id=?", (created["command_id"],)
        ).fetchone()
        assert tuple(command) == ("source_applied", 3)
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_structural_manifests "
            "WHERE command_id=?", (created["command_id"],)
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_page_id=?",
            (request.workspace_id, request.source_page_id),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_structural_finalize_rejects_expired_lease_before_source_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, created, operation_id, candidate = _prepare_create(
        tmp_path, monkeypatch
    )
    store = prepared["store"]
    monkeypatch.setattr(store, "_trusted_clock", lambda: 2_000_000_120)
    source_calls = 0

    def source_revalidator(_candidate):
        nonlocal source_calls
        source_calls += 1
        return True

    with pytest.raises(WorkspacePageLifecycleLeaseConflict):
        store.finalize_page_lifecycle_structural_catalog(
            "reviewer",
            request.workspace_id,
            created["command_id"],
            candidate,
            operation_id=operation_id,
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            fencing_generation=1,
            expected_command_version=3,
            source_revalidator=source_revalidator,
        )
    assert source_calls == 0
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_structural_manifests "
            "WHERE command_id=?", (created["command_id"],)
        ).fetchone()[0] == 0
    finally:
        con.close()
