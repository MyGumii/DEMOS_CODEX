from __future__ import annotations

import json
import sqlite3

import pytest

from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    WorkspaceStore,
    WorkspaceSubstrateUnavailable,
    catalog_path_for_user_data_root,
)


def _store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(tmp_path))


def _workspace(store: WorkspaceStore, actor: str = "owner") -> dict:
    return store.create_workspace(
        actor, name="P1 workspace", operation_id=f"create-{actor}"
    )["workspace"]


def _proof(actor: str, owner: str, page: str, marker: str = "a") -> LegacyPageSourceProof:
    return LegacyPageSourceProof(
        actor_subject_id=actor,
        owner_subject_id=owner,
        page_id=page,
        revision=1,
        updated_at=100,
        content_sha256="sha256:" + marker * 64,
    )


def test_request_readiness_is_bounded_and_deep_health_is_explicit(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    traced: list[str] = []
    original = store._open_read_connection

    def traced_connection():
        con = original()
        con.set_trace_callback(traced.append)
        return con

    store._open_read_connection = traced_connection
    try:
        assert store.get_workspace("owner", workspace["id"])["id"] == workspace["id"]
        assert not any("foreign_key_check" in sql.lower() for sql in traced)
        assert not any("integrity_check" in sql.lower() for sql in traced)

        traced.clear()
        report = store.verify_deep_health()
        assert report == {
            "healthy": True,
            "schema_version": report["schema_version"],
            "integrity_ok": True,
            "foreign_key_violation_count": 0,
            "issues": [],
            "incarnation_attestation_readiness": {
                "binding_count": 0,
                "active_binding_count": 0,
                "pending_binding_count": 0,
                "quarantined_binding_count": 0,
                "attestation_count": 0,
                "current_attestation_count": 0,
                "historical_attestation_count": 0,
                "attested_active_binding_count": 0,
                "unattested_active_binding_count": 0,
                "attested_pending_binding_count": 0,
                "unattested_pending_binding_count": 0,
                "ready_for_resolver_cutover": True,
            },
            "navigation_counts": {
                "state_count": 0,
                "section_count": 0,
                "favorite_count": 0,
            },
            "page_tree_counts": {
                "state_count": 1,
                "node_count": 0,
                "command_count": 0,
                "subtree_manifest_count": 0,
                "subtree_effect_count": 0,
                "subtree_mapping_count": 0,
                "complete_workspace_count": 0,
            },
        }
        assert any("foreign_key_check" in sql.lower() for sql in traced)
        assert any("integrity_check" in sql.lower() for sql in traced)
    finally:
        store._open_read_connection = original


def test_deep_health_detects_data_corruption_without_request_path_full_scan(tmp_path) -> None:
    store = _store(tmp_path)
    _workspace(store)
    con = sqlite3.connect(store.db_path)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute(
            """
            INSERT INTO workspace_audit_events(
              id,workspace_id,actor_subject_id,action,target_type,target_id,
              operation_id,payload_json,created_at
            ) VALUES('waud_corrupt','ws_missing','owner','corrupt','x','x',
                     'corrupt-op','{}',1)
            """
        )
        con.commit()
    finally:
        con.close()

    # Bounded schema readiness remains cheap; the explicit gate finds data damage.
    assert store.capabilities()["workspace_v2"] is True
    report = store.deep_health()
    assert report["healthy"] is False
    assert report["foreign_key_violation_count"] == 1
    assert "foreign_key_violation" in report["issues"]
    with pytest.raises(WorkspaceSubstrateUnavailable, match="deep health"):
        store.verify_deep_health()


def test_pre_p1_schema_marker_remains_fail_closed_without_request_time_migration(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    con = sqlite3.connect(store.db_path)
    try:
        con.execute(
            """
            UPDATE workspace_schema_migrations
            SET version=2026081301,name='notion_workspace_v2_np2a'
            """
        )
        con.commit()
    finally:
        con.close()

    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.get_workspace("owner", workspace["id"])
    with pytest.raises(RuntimeError, match="explicit migration or restore"):
        store.bootstrap()


def test_raw_updates_cannot_overlap_guest_member_or_detach_active_children(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    ws_id = workspace["id"]
    version = store.set_guest(
        "owner", ws_id, "guest", base_version=1, operation_id="guest"
    )["workspace"]["version"]
    version = store.set_member(
        "owner",
        ws_id,
        "member",
        role="member",
        base_version=version,
        operation_id="member",
    )["workspace"]["version"]
    group = store.create_group(
        "owner", ws_id, name="Group", base_version=version, operation_id="group"
    )
    version = store.set_group_member(
        "owner",
        ws_id,
        group["group"]["id"],
        "member",
        base_version=group["workspace"]["version"],
        operation_id="group-member",
    )["workspace"]["version"]
    teamspace = store.create_teamspace(
        "owner",
        ws_id,
        name="Teamspace",
        visibility="closed",
        base_version=version,
        operation_id="teamspace",
    )
    store.set_teamspace_member(
        "owner",
        ws_id,
        teamspace["teamspace"]["id"],
        "member",
        role="member",
        base_version=teamspace["workspace"]["version"],
        operation_id="teamspace-member",
    )

    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="cannot also be a guest"):
            con.execute(
                """
                UPDATE workspace_guests SET subject_id='owner'
                WHERE workspace_id=? AND subject_id='guest'
                """,
                (ws_id,),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="dependencies"):
            con.execute(
                """
                UPDATE workspace_memberships SET revoked_at=1
                WHERE workspace_id=? AND subject_id='member'
                """,
                (ws_id,),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="group requires"):
            con.execute(
                """
                UPDATE workspace_group_members SET subject_id='missing'
                WHERE group_id=? AND subject_id='member'
                """,
                (group["group"]["id"],),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="teamspace requires"):
            con.execute(
                """
                UPDATE teamspace_memberships SET subject_id='missing'
                WHERE teamspace_id=? AND subject_id='member'
                """,
                (teamspace["teamspace"]["id"],),
            )
        con.rollback()
    finally:
        con.close()


def test_raw_updates_and_deletes_cannot_remove_sole_owners(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    ws_id = workspace["id"]
    teamspace = store.create_teamspace(
        "owner",
        ws_id,
        name="Owned",
        visibility="private",
        base_version=1,
        operation_id="owned",
    )
    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="workspace must retain"):
            con.execute(
                """
                UPDATE workspace_memberships SET role='member'
                WHERE workspace_id=? AND subject_id='owner'
                """,
                (ws_id,),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="workspace must retain"):
            con.execute(
                "DELETE FROM workspace_memberships WHERE workspace_id=? AND subject_id='owner'",
                (ws_id,),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="teamspace must retain"):
            con.execute(
                """
                UPDATE teamspace_memberships SET role='member'
                WHERE teamspace_id=? AND subject_id='owner'
                """,
                (teamspace["teamspace"]["id"],),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="teamspace must retain"):
            con.execute(
                "DELETE FROM teamspace_memberships WHERE teamspace_id=? AND subject_id='owner'",
                (teamspace["teamspace"]["id"],),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="owner pointer"):
            con.execute(
                "UPDATE workspaces SET owner_subject_id='missing' WHERE id=?", (ws_id,)
            )
        con.rollback()
    finally:
        con.close()


def test_replay_uses_original_ledger_capability_not_incoming_closure(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store, actor="owner_one")
    ws_id = workspace["id"]
    version = store.set_member(
        "owner_one",
        ws_id,
        "owner_two",
        role="owner",
        base_version=1,
        operation_id="owner-two",
    )["workspace"]["version"]
    demoted = store.set_member(
        "owner_one",
        ws_id,
        "owner_two",
        role="member",
        base_version=version,
        operation_id="demote-owner-two",
    )
    original_version = version
    version = demoted["workspace"]["version"]
    version = store.set_member(
        "owner_one",
        ws_id,
        "owner_three",
        role="owner",
        base_version=version,
        operation_id="owner-three",
    )["workspace"]["version"]
    store.set_member(
        "owner_three",
        ws_id,
        "owner_one",
        role="membership_admin",
        base_version=version,
        operation_id="demote-owner-one",
    )

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        ledger = con.execute(
            """
            SELECT required_capability,authorization_strength,authorization_context_json
            FROM workspace_operations
            WHERE actor_subject_id='owner_one' AND operation_id='demote-owner-two'
            """
        ).fetchone()
    finally:
        con.close()
    assert ledger["required_capability"] == "workspace.owner"
    assert ledger["authorization_strength"] == 50
    assert json.loads(ledger["authorization_context_json"]) == {"workspace_id": ws_id}

    # The legacy replay closure would accept membership_admin for role='member'.
    # The immutable original ledger requirement correctly still requires owner.
    with pytest.raises(PermissionError, match="permission denied"):
        store.set_member(
            "owner_one",
            ws_id,
            "owner_two",
            role="member",
            base_version=original_version,
            operation_id="demote-owner-two",
        )


def test_raw_workspace_operation_ledger_update_and_delete_are_blocked(tmp_path) -> None:
    store = _store(tmp_path)
    original = store.create_workspace(
        "owner", name="P1 workspace", operation_id="create-owner"
    )
    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ledger is immutable"):
            con.execute(
                """
                UPDATE workspace_operations SET result_json='{}'
                WHERE actor_subject_id='owner' AND operation_id='create-owner'
                """
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="ledger is immutable"):
            con.execute(
                """
                DELETE FROM workspace_operations
                WHERE actor_subject_id='owner' AND operation_id='create-owner'
                """
            )
        con.rollback()
    finally:
        con.close()

    replayed = store.create_workspace(
        "owner", name="P1 workspace", operation_id="create-owner"
    )
    assert replayed == original
    assert store.audit_counts() == {
        "operations": 1,
        "audit_events": 1,
        "outbox_events": 1,
    }
    report = store.deep_health()
    assert report["healthy"] is True


def test_source_pending_activation_cas_visibility_and_stale_quarantine(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    ws_id = workspace["id"]
    version = store.set_member(
        "owner",
        ws_id,
        "page_owner",
        role="member",
        base_version=1,
        operation_id="page-owner",
    )["workspace"]["version"]
    proof = _proof("owner", "page_owner", "p_pending")
    staged = store.stage_page_source_binding(
        "owner",
        ws_id,
        proof,
        base_version=version,
        operation_id="stage-pending",
    )
    assert staged["binding"]["state"] == "pending"
    assert store.list_page_source_bindings("page_owner", ws_id) == []

    callback_calls = []
    activated = store.activate_page_source_binding(
        "owner",
        ws_id,
        proof,
        source_revalidator=lambda candidate: callback_calls.append(candidate) or True,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-pending",
    )
    assert callback_calls == [proof]
    assert activated["binding"]["version"] == staged["binding"]["version"]
    assert len(store.list_page_source_bindings("page_owner", ws_id)) == 1
    replayed_activation = store.activate_page_source_binding(
        "owner",
        ws_id,
        proof,
        source_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("replay must not cross into the legacy authority")
        ),
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-pending",
    )
    assert replayed_activation == activated

    quarantine_calls = []
    quarantined = store.quarantine_page_source_if_stale(
        "owner",
        ws_id,
        "page_owner",
        "p_pending",
        source_revalidator=lambda candidate: quarantine_calls.append(candidate) or False,
        expected_binding_version=activated["binding"]["version"],
        base_version=activated["workspace"]["version"],
        operation_id="quarantine-stale",
    )
    assert quarantined["quarantined"] is True
    assert len(quarantine_calls) == 1
    assert store.list_page_source_bindings("page_owner", ws_id) == []
    replayed_quarantine = store.quarantine_page_source_if_stale(
        "owner",
        ws_id,
        "page_owner",
        "p_pending",
        source_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("replay must not cross into the legacy authority")
        ),
        expected_binding_version=activated["binding"]["version"],
        base_version=activated["workspace"]["version"],
        operation_id="quarantine-stale",
    )
    assert replayed_quarantine == quarantined

    restaged_proof = LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="page_owner",
        page_id="p_pending",
        revision=2,
        updated_at=101,
        content_sha256="sha256:" + "f" * 64,
    )
    restaged = store.stage_page_source_binding(
        "owner",
        ws_id,
        restaged_proof,
        base_version=quarantined["workspace"]["version"],
        operation_id="restage-after-quarantine",
    )
    assert restaged["binding"]["state"] == "pending"

    # The immutable operation ledger, rather than the current binding row, is
    # authoritative for exact retries.  Restaging must neither revalidate nor
    # change the original response.
    replayed_after_restage = store.quarantine_page_source_if_stale(
        "owner",
        ws_id,
        "page_owner",
        "p_pending",
        source_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("restaged replay must not invoke the legacy authority")
        ),
        expected_binding_version=activated["binding"]["version"],
        base_version=activated["workspace"]["version"],
        operation_id="quarantine-stale",
    )
    assert replayed_after_restage == quarantined

    con = sqlite3.connect(store.db_path)
    try:
        con.execute(
            """
            DELETE FROM workspace_page_sources
            WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
            """,
            (ws_id, "page_owner", "p_pending"),
        )
        con.commit()
    finally:
        con.close()
    replayed_after_delete = store.quarantine_page_source_if_stale(
        "owner",
        ws_id,
        "page_owner",
        "p_pending",
        source_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("deleted replay must not invoke the legacy authority")
        ),
        expected_binding_version=activated["binding"]["version"],
        base_version=activated["workspace"]["version"],
        operation_id="quarantine-stale",
    )
    assert replayed_after_delete == quarantined
    assert len(quarantine_calls) == 1


def test_source_activation_conflict_is_detected_before_external_revalidation(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    ws_id = workspace["id"]
    proof_one = _proof("owner", "owner", "p_cas", "b")
    stage_one = store.stage_page_source_binding(
        "owner",
        ws_id,
        proof_one,
        base_version=1,
        operation_id="stage-one",
    )
    proof_two = LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="owner",
        page_id="p_cas",
        revision=2,
        updated_at=101,
        content_sha256="sha256:" + "c" * 64,
    )
    stage_two = store.stage_page_source_binding(
        "owner",
        ws_id,
        proof_two,
        base_version=1,
        operation_id="stage-two",
    )
    assert stage_two["binding"]["version"] > stage_one["binding"]["version"]
    called = False

    def should_not_run(_proof):
        nonlocal called
        called = True
        return True

    with pytest.raises(ValueError, match="pending binding is stale"):
        store.activate_page_source_binding(
            "owner",
            ws_id,
            proof_one,
            source_revalidator=should_not_run,
            expected_binding_version=stage_one["binding"]["version"],
            base_version=1,
            operation_id="activate-old",
        )
    assert called is False
    assert store.list_page_source_bindings("owner", ws_id) == []


@pytest.mark.parametrize("initial_state", ["pending", "active", "quarantined"])
def test_authorized_restage_preserves_source_identity_and_provenance(
    tmp_path, initial_state
) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    ws_id = workspace["id"]
    version = store.set_member(
        "owner",
        ws_id,
        "admin",
        role="membership_admin",
        base_version=1,
        operation_id=f"restage-admin-{initial_state}",
    )["workspace"]["version"]
    teamspace = store.create_teamspace(
        "owner",
        ws_id,
        name=f"Restage {initial_state}",
        visibility="private",
        base_version=version,
        operation_id=f"restage-teamspace-{initial_state}",
    )
    version = store.set_teamspace_member(
        "owner",
        ws_id,
        teamspace["teamspace"]["id"],
        "admin",
        role="owner",
        base_version=teamspace["workspace"]["version"],
        operation_id=f"restage-teamspace-admin-{initial_state}",
    )["workspace"]["version"]

    original_proof = _proof("owner", "owner", f"p_restage_{initial_state}", "7")
    current = store.stage_page_source_binding(
        "owner",
        ws_id,
        original_proof,
        base_version=version,
        operation_id=f"restage-original-{initial_state}",
    )
    if initial_state in {"active", "quarantined"}:
        current = store.activate_page_source_binding(
            "owner",
            ws_id,
            original_proof,
            source_revalidator=lambda _proof: True,
            expected_binding_version=current["binding"]["version"],
            base_version=current["workspace"]["version"],
            operation_id=f"restage-original-activate-{initial_state}",
        )
    if initial_state == "quarantined":
        current = store.quarantine_page_source_if_stale(
            "owner",
            ws_id,
            "owner",
            original_proof.page_id,
            source_revalidator=lambda _proof: False,
            expected_binding_version=current["binding"]["version"],
            base_version=current["workspace"]["version"],
            operation_id=f"restage-original-quarantine-{initial_state}",
        )

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        identity_before = con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   bound_by_subject_id,created_at,row_version
            FROM workspace_page_sources
            WHERE workspace_id=? AND source_page_id=?
            """,
            (ws_id, original_proof.page_id),
        ).fetchone()
    finally:
        con.close()

    restaged_proof = LegacyPageSourceProof(
        actor_subject_id="admin",
        owner_subject_id="owner",
        page_id=original_proof.page_id,
        revision=2,
        updated_at=200,
        content_sha256="sha256:" + "8" * 64,
    )
    restaged = store.stage_page_source_binding(
        "admin",
        ws_id,
        restaged_proof,
        teamspace_id=teamspace["teamspace"]["id"],
        base_version=current["workspace"]["version"],
        operation_id=f"restage-new-proof-{initial_state}",
    )
    assert restaged["binding"]["state"] == "pending"
    assert restaged["binding"]["placement"] == "teamspace"
    assert restaged["binding"]["teamspace_id"] == teamspace["teamspace"]["id"]
    assert restaged["binding"]["source_revision"] == 2
    assert restaged["binding"]["source_sha256"] == restaged_proof.content_sha256

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        identity_after = con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   bound_by_subject_id,created_at,row_version
            FROM workspace_page_sources
            WHERE workspace_id=? AND source_page_id=?
            """,
            (ws_id, original_proof.page_id),
        ).fetchone()
    finally:
        con.close()
    assert dict(identity_after) == {
        **dict(identity_before),
        "row_version": int(identity_before["row_version"]) + 1,
    }
    assert identity_after["bound_by_subject_id"] == "owner"

    # A different currently-authorized actor can activate the new proof, while
    # the immutable row provenance continues to identify its original binder.
    activated = store.activate_page_source_binding(
        "admin",
        ws_id,
        restaged_proof,
        source_revalidator=lambda _proof: True,
        expected_binding_version=restaged["binding"]["version"],
        teamspace_id=teamspace["teamspace"]["id"],
        base_version=restaged["workspace"]["version"],
        operation_id=f"restage-activate-new-{initial_state}",
    )
    assert activated["binding"]["version"] == restaged["binding"]["version"]

    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        for column, value in (
            ("workspace_id", "ws_relocated"),
            ("source_owner_subject_id", "admin"),
            ("source_page_id", "p_relocated"),
            ("bound_by_subject_id", "admin"),
            ("created_at", int(identity_after["created_at"]) + 1),
        ):
            with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                con.execute(
                    f"""
                    UPDATE workspace_page_sources SET {column}=?
                    WHERE workspace_id=? AND source_page_id=?
                    """,
                    (value, ws_id, original_proof.page_id),
                )
            con.rollback()
    finally:
        con.close()


@pytest.mark.parametrize(
    "failure_table",
    ["workspace_audit_events", "workspace_outbox", "workspace_operations"],
)
def test_source_activation_ledger_failure_rolls_back_to_pending(
    tmp_path, failure_table
) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    proof = _proof("owner", "owner", f"p_fault_{failure_table}", "d")
    staged = store.stage_page_source_binding(
        "owner",
        workspace["id"],
        proof,
        base_version=1,
        operation_id=f"stage-{failure_table}",
    )
    original = store._write_connection

    def fault_connection():
        con = original()
        con.execute(
            f"""
            CREATE TEMP TRIGGER fail_source_activation
            BEFORE INSERT ON main.{failure_table}
            BEGIN SELECT RAISE(ABORT, 'source activation ledger failure'); END
            """
        )
        return con

    store._write_connection = fault_connection
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ledger failure"):
            store.activate_page_source_binding(
                "owner",
                workspace["id"],
                proof,
                source_revalidator=lambda _proof: True,
                expected_binding_version=staged["binding"]["version"],
                base_version=1,
                operation_id=f"activate-{failure_table}",
            )
    finally:
        store._write_connection = original

    con = sqlite3.connect(store.db_path)
    try:
        state = con.execute(
            """
            SELECT state,row_version FROM workspace_page_sources
            WHERE workspace_id=? AND source_page_id=?
            """,
            (workspace["id"], proof.page_id),
        ).fetchone()
    finally:
        con.close()
    assert state == ("pending", staged["binding"]["version"])
    assert store.list_page_source_bindings("owner", workspace["id"]) == []


def test_source_constraints_reject_invalid_hash_state_and_parent_revocation(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    version = store.set_member(
        "owner",
        workspace["id"],
        "page_owner",
        role="member",
        base_version=1,
        operation_id="constraints-page-owner",
    )["workspace"]["version"]
    proof = _proof("owner", "page_owner", "p_constraints", "e")
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=lambda _proof: True,
        base_version=version,
        operation_id="bind-constraints",
    )
    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            con.execute(
                """
                UPDATE workspace_page_sources SET source_sha256=?
                WHERE workspace_id=? AND source_page_id=?
                """,
                ("sha256:" + "G" * 64, workspace["id"], proof.page_id),
            )
        con.rollback()
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            con.execute(
                """
                UPDATE workspace_page_sources
                SET state='quarantined',quarantined_at=NULL
                WHERE workspace_id=? AND source_page_id=?
                """,
                (workspace["id"], proof.page_id),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="dependencies"):
            con.execute(
                """
                UPDATE workspace_memberships SET revoked_at=1
                WHERE workspace_id=? AND subject_id='page_owner'
                """,
                (workspace["id"],),
            )
        con.rollback()
    finally:
        con.close()
    assert bound["binding"]["source_sha256"] == proof.content_sha256
