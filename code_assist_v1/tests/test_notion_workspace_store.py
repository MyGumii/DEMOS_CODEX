from __future__ import annotations

import os
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_store as workspace_store_module
from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    WorkspaceOperationConflict,
    WorkspaceRevisionConflict,
    WorkspaceStore,
    WorkspaceSubstrateUnavailable,
    catalog_path_for_user_data_root,
)


def _new_store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(tmp_path / "user_data"))


def _create(store: WorkspaceStore, actor: str = "owner", op: str = "create-1") -> dict:
    return store.create_workspace(
        actor,
        name="Engineering",
        icon="E",
        operation_id=op,
    )


def test_reads_do_not_create_or_repair_missing_workspace_catalog(tmp_path) -> None:
    store = _new_store(tmp_path)
    parent = os.path.dirname(store.db_path)

    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.list_workspaces("owner")
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.get_workspace("owner", "ws_missing")

    assert not os.path.exists(parent)
    assert not os.path.exists(store.db_path)


def test_bootstrap_ddl_failure_never_publishes_partial_catalog(
    tmp_path, monkeypatch
) -> None:
    store = _new_store(tmp_path)
    monkeypatch.setattr(
        workspace_store_module,
        "_SCHEMA_SQL",
        "CREATE TABLE partial_table(id TEXT); THIS IS INVALID SQL;",
    )

    with pytest.raises(sqlite3.Error):
        store.bootstrap()

    assert not os.path.exists(store.db_path)
    parent = os.path.dirname(store.db_path)
    assert not [name for name in os.listdir(parent) if name.endswith((".tmp", "-wal", "-shm"))]


def test_workspace_create_is_actor_scoped_idempotent_and_audited(tmp_path) -> None:
    store = _new_store(tmp_path)
    first = _create(store)
    replay = _create(store)

    assert replay == first
    assert first["workspace"]["access"] == {"role": "owner", "epoch": 1}
    assert first["workspace"]["version"] == 1
    assert store.capabilities()["workspace_v2"] is True
    assert store.audit_counts() == {
        "operations": 1,
        "audit_events": 1,
        "outbox_events": 1,
    }

    with pytest.raises(WorkspaceOperationConflict):
        store.create_workspace(
            "owner",
            name="Different request",
            operation_id="create-1",
        )
    assert store.audit_counts()["operations"] == 1

    items = store.list_workspaces("owner")
    assert items == [first["workspace"]]
    assert store.list_workspaces("unrelated") == []


def test_operation_replay_reauthorizes_actor_after_membership_revoke(tmp_path) -> None:
    store = _new_store(tmp_path)
    created = _create(store)
    workspace_id = created["workspace"]["id"]
    added = store.set_member(
        "owner",
        workspace_id,
        "owner_two",
        role="owner",
        base_version=1,
        operation_id="add-owner-two",
    )
    store.revoke_member(
        "owner_two",
        workspace_id,
        "owner",
        base_version=added["workspace"]["version"],
        operation_id="revoke-original-owner",
    )

    with pytest.raises(FileNotFoundError):
        _create(store)
    assert store.list_workspaces("owner") == []


def test_same_operation_id_is_independent_for_two_workspace_actors(tmp_path) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    added = store.set_member(
        "owner",
        workspace_id,
        "owner_two",
        role="owner",
        base_version=1,
        operation_id="add-owner-two",
    )
    first = store.create_group(
        "owner",
        workspace_id,
        name="First group",
        base_version=added["workspace"]["version"],
        operation_id="same-caller-key",
    )
    second = store.create_group(
        "owner_two",
        workspace_id,
        name="Second group",
        base_version=first["workspace"]["version"],
        operation_id="same-caller-key",
    )

    assert second["workspace"]["version"] == 4
    assert store.audit_counts() == {
        "operations": 4,
        "audit_events": 4,
        "outbox_events": 4,
    }


def test_membership_cas_roles_last_owner_and_anti_enumeration(tmp_path) -> None:
    store = _new_store(tmp_path)
    created = _create(store)
    workspace_id = created["workspace"]["id"]

    owner_two = store.set_member(
        "owner",
        workspace_id,
        "owner_two",
        role="owner",
        base_version=1,
        operation_id="member-owner-two",
    )
    assert owner_two["workspace"]["version"] == 2
    assert owner_two["member"]["role"] == "owner"

    member = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=2,
        operation_id="member-one",
    )
    assert member["workspace"]["version"] == 3

    with pytest.raises(WorkspaceRevisionConflict) as stale:
        store.set_member(
            "owner",
            workspace_id,
            "late_member",
            role="member",
            base_version=2,
            operation_id="stale-member",
        )
    assert stale.value.current_version == 3

    with pytest.raises(FileNotFoundError):
        store.set_member(
            "outsider",
            workspace_id,
            "late_member",
            role="member",
            base_version=1,
            operation_id="outsider-member",
        )
    assert store.get_workspace("owner", workspace_id)["version"] == 3
    with pytest.raises(FileNotFoundError):
        store.get_workspace("outsider", workspace_id)

    with pytest.raises(PermissionError):
        store.set_member(
            "member_one",
            workspace_id,
            "late_member",
            role="member",
            base_version=3,
            operation_id="member-cannot-admin",
        )

    demoted = store.set_member(
        "owner",
        workspace_id,
        "owner",
        role="member",
        base_version=3,
        operation_id="owner-demote",
    )
    assert demoted["workspace"]["owner_subject_id"] == "owner_two"
    assert demoted["workspace"]["version"] == 4

    only_owner_store = _new_store(tmp_path / "other")
    only = _create(only_owner_store, actor="solo", op="solo-create")
    with pytest.raises(ValueError, match="retain at least one owner"):
        only_owner_store.set_member(
            "solo",
            only["workspace"]["id"],
            "solo",
            role="member",
            base_version=1,
            operation_id="last-owner-demote",
        )


def test_guests_are_not_members_and_cannot_enter_groups_or_teamspaces(tmp_path) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]

    guest = store.set_guest(
        "owner",
        workspace_id,
        "guest_one",
        base_version=1,
        operation_id="guest-one",
    )
    assert guest["guest"]["role"] == "guest"
    assert guest["workspace"]["version"] == 2

    with pytest.raises(ValueError, match="revoked"):
        store.set_member(
            "owner",
            workspace_id,
            "guest_one",
            role="member",
            base_version=2,
            operation_id="guest-to-member",
        )

    member = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=2,
        operation_id="member-one",
    )
    assert member["workspace"]["version"] == 3
    group = store.create_group(
        "owner",
        workspace_id,
        name="Platform",
        base_version=3,
        operation_id="group-platform",
    )
    group_id = group["group"]["id"]
    assert group["workspace"]["version"] == 4

    with pytest.raises(ValueError, match="active workspace members only"):
        store.set_group_member(
            "owner",
            workspace_id,
            group_id,
            "guest_one",
            base_version=4,
            operation_id="guest-group",
        )

    group_member = store.set_group_member(
        "owner",
        workspace_id,
        group_id,
        "member_one",
        base_version=4,
        operation_id="member-group",
    )
    assert group_member["group"]["member_subject_ids"] == ["member_one"]
    assert group_member["workspace"]["version"] == 5

    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Secret launch",
        visibility="private",
        base_version=5,
        operation_id="teamspace-secret",
    )
    teamspace_id = teamspace["teamspace"]["id"]
    assert teamspace["workspace"]["version"] == 6
    assert store.get_workspace("guest_one", workspace_id)["teamspaces"] == []
    assert store.get_workspace("member_one", workspace_id)["teamspaces"] == []

    with pytest.raises(ValueError, match="active workspace members only"):
        store.set_teamspace_member(
            "owner",
            workspace_id,
            teamspace_id,
            "guest_one",
            role="member",
            base_version=6,
            operation_id="guest-teamspace",
        )

    joined = store.set_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "member_one",
        role="member",
        base_version=6,
        operation_id="member-teamspace",
    )
    assert joined["workspace"]["version"] == 7
    visible = store.get_workspace("member_one", workspace_id)["teamspaces"]
    assert [item["id"] for item in visible] == [teamspace_id]


def test_revoke_member_fences_epoch_and_removes_group_and_teamspace_access(tmp_path) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    version = 1
    version = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="member-one",
    )["workspace"]["version"]
    group = store.create_group(
        "owner",
        workspace_id,
        name="Team",
        base_version=version,
        operation_id="group-team",
    )
    version = group["workspace"]["version"]
    version = store.set_group_member(
        "owner",
        workspace_id,
        group["group"]["id"],
        "member_one",
        base_version=version,
        operation_id="group-member",
    )["workspace"]["version"]
    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Open team",
        visibility="open",
        base_version=version,
        operation_id="open-teamspace",
    )
    version = teamspace["workspace"]["version"]
    version = store.set_teamspace_member(
        "owner",
        workspace_id,
        teamspace["teamspace"]["id"],
        "member_one",
        role="member",
        base_version=version,
        operation_id="teamspace-member",
    )["workspace"]["version"]

    before = store.get_workspace("member_one", workspace_id)
    old_epoch = before["access"]["epoch"]
    revoked = store.revoke_member(
        "owner",
        workspace_id,
        "member_one",
        base_version=version,
        operation_id="revoke-member",
    )
    assert revoked["workspace"]["security_epoch"] > before["security_epoch"]
    assert old_epoch >= 1
    with pytest.raises(FileNotFoundError):
        store.get_workspace("member_one", workspace_id)

    owner_state = store.get_workspace("owner", workspace_id)
    assert owner_state["groups"][0]["member_subject_ids"] == []


def test_page_source_binding_is_single_workspace_and_never_copies_page_body(tmp_path) -> None:
    store = _new_store(tmp_path)
    first = _create(store, op="create-first")
    first_id = first["workspace"]["id"]
    first_version = store.set_member(
        "owner",
        first_id,
        "page_owner",
        role="member",
        base_version=1,
        operation_id="first-page-owner",
    )["workspace"]["version"]
    proof = LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="page_owner",
        page_id="p_legacy_1",
        revision=7,
        updated_at=100,
        content_sha256="sha256:" + "a" * 64,
    )
    bound = store.bind_page_source(
        "owner",
        first_id,
        proof,
        source_revalidator=lambda candidate: candidate == proof,
        base_version=first_version,
        operation_id="bind-first",
    )
    assert bound["binding"] == {
        "workspace_id": first_id,
        "source_owner_subject_id": "page_owner",
        "source_page_id": "p_legacy_1",
        "teamspace_id": "",
        "placement": "private",
        "source_revision": 7,
        "source_updated_at": 100,
        "source_sha256": "sha256:" + "a" * 64,
        "version": 1,
    }

    second = store.create_workspace(
        "owner", name="Second", operation_id="create-second"
    )
    second_id = second["workspace"]["id"]
    second_version = store.set_member(
        "owner",
        second_id,
        "page_owner",
        role="member",
        base_version=1,
        operation_id="second-page-owner",
    )["workspace"]["version"]
    with pytest.raises(ValueError, match="already bound"):
        store.bind_page_source(
            "owner",
            second_id,
            proof,
            source_revalidator=lambda candidate: candidate == proof,
            base_version=second_version,
            operation_id="bind-second",
        )

    stale_proof = LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="page_owner",
        page_id="p_missing",
        revision=1,
        updated_at=101,
        content_sha256="sha256:" + "b" * 64,
    )
    with pytest.raises(ValueError, match="invalid or stale"):
        store.bind_page_source(
            "owner",
            first_id,
            stale_proof,
            source_revalidator=lambda _candidate: False,
            base_version=bound["workspace"]["version"],
            operation_id="bind-stale",
        )

    revoked = store.revoke_member(
        "owner",
        first_id,
        "page_owner",
        base_version=bound["workspace"]["version"],
        operation_id="revoke-page-owner",
    )
    assert revoked["workspace"]["version"] == bound["workspace"]["version"] + 1
    con = sqlite3.connect(store.db_path)
    try:
        state = con.execute(
            """
            SELECT state,quarantined_at FROM workspace_page_sources
            WHERE workspace_id=? AND source_owner_subject_id=? AND source_page_id=?
            """,
            (first_id, "page_owner", "p_legacy_1"),
        ).fetchone()
    finally:
        con.close()
    assert state[0] == "quarantined"
    assert state[1] is not None


@pytest.mark.parametrize(
    "failure_table",
    ["workspace_audit_events", "workspace_outbox", "workspace_operations"],
)
def test_mutation_ledger_failpoints_roll_back_every_effect(tmp_path, failure_table) -> None:
    store = _new_store(tmp_path)
    created = _create(store)
    workspace_id = created["workspace"]["id"]
    original_connection = store._write_connection

    def fault_connection():
        fault_con = original_connection()
        fault_con.execute(
            f"""
            CREATE TEMP TRIGGER fail_workspace_audit
            BEFORE INSERT ON main.{failure_table}
            BEGIN
              SELECT RAISE(ABORT, 'injected ledger failure');
            END
            """
        )
        return fault_con

    store._write_connection = fault_connection
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected ledger failure"):
            store.set_member(
                "owner",
                workspace_id,
                "member_one",
                role="member",
                base_version=1,
                operation_id="fault-member",
            )
    finally:
        store._write_connection = original_connection

    state = store.get_workspace("owner", workspace_id)
    assert state["version"] == 1
    assert [item["subject_id"] for item in state["members"]] == ["owner"]
    assert store.audit_counts() == {
        "operations": 1,
        "audit_events": 1,
        "outbox_events": 1,
    }

    saved = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=1,
        operation_id="fault-member",
    )
    assert saved["workspace"]["version"] == 2
    assert store.audit_counts() == {
        "operations": 2,
        "audit_events": 2,
        "outbox_events": 2,
    }


def test_schema_checksum_mismatch_is_not_silently_repaired(tmp_path) -> None:
    store = _new_store(tmp_path)
    store.bootstrap()
    con = sqlite3.connect(store.db_path)
    try:
        con.execute(
            "UPDATE workspace_schema_migrations SET checksum='sha256:tampered'"
        )
        con.commit()
    finally:
        con.close()

    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        store.bootstrap()

    missing_table_store = _new_store(tmp_path / "missing-table")
    missing_table_store.bootstrap()
    con = sqlite3.connect(missing_table_store.db_path)
    try:
        con.execute("DROP TABLE workspace_groups")
        con.commit()
    finally:
        con.close()
    with pytest.raises(RuntimeError, match="explicit migration or restore"):
        missing_table_store.bootstrap()
    con = sqlite3.connect(missing_table_store.db_path)
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_groups'"
        ).fetchone() is None
    finally:
        con.close()


def test_workspace_revoke_requires_teamspace_owner_transfer(tmp_path) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    version = store.set_member(
        "owner",
        workspace_id,
        "member_owner",
        role="member",
        base_version=1,
        operation_id="member-owner",
    )["workspace"]["version"]
    teamspace = store.create_teamspace(
        "member_owner",
        workspace_id,
        name="Owned teamspace",
        visibility="closed",
        base_version=version,
        operation_id="owned-teamspace",
    )
    version = teamspace["workspace"]["version"]

    with pytest.raises(ValueError, match="transfer teamspace ownership"):
        store.revoke_member(
            "owner",
            workspace_id,
            "member_owner",
            base_version=version,
            operation_id="revoke-before-transfer",
        )
    assert store.get_workspace("member_owner", workspace_id)["access"]["role"] == "member"


def test_last_teamspace_owner_cannot_be_demoted(tmp_path) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Critical",
        visibility="private",
        base_version=1,
        operation_id="critical-teamspace",
    )
    with pytest.raises(ValueError, match="retain at least one owner"):
        store.set_teamspace_member(
            "owner",
            workspace_id,
            teamspace["teamspace"]["id"],
            "owner",
            role="member",
            base_version=teamspace["workspace"]["version"],
            operation_id="demote-last-teamspace-owner",
        )


def test_store_rejects_boolean_base_version_and_empty_data_root(tmp_path) -> None:
    with pytest.raises(ValueError, match="user_data_root required"):
        catalog_path_for_user_data_root("")
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    with pytest.raises(ValueError, match="base_version"):
        store.set_member(
            "owner",
            workspace_id,
            "member_one",
            role="member",
            base_version=True,
            operation_id="boolean-version",
        )


def test_schema_table_index_trigger_or_fingerprint_damage_fails_closed(tmp_path) -> None:
    for label, damage in (
        ("table", "DROP TABLE workspace_groups"),
        ("index", "DROP INDEX idx_workspace_guests_subject"),
        ("trigger", "DROP TRIGGER trg_workspace_guest_excludes_member_insert"),
        (
            "fingerprint",
            "UPDATE workspace_schema_migrations SET actual_schema_sha256='sha256:bad'",
        ),
    ):
        store = _new_store(tmp_path / label)
        workspace_id = _create(store, op=f"create-{label}")["workspace"]["id"]
        con = sqlite3.connect(store.db_path)
        try:
            con.execute(damage)
            con.commit()
        finally:
            con.close()
        assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
        with pytest.raises(WorkspaceSubstrateUnavailable):
            store.get_workspace("owner", workspace_id)
        with pytest.raises(WorkspaceSubstrateUnavailable):
            store.set_member(
                "owner",
                workspace_id,
                "member_one",
                role="member",
                base_version=1,
                operation_id=f"damaged-{label}",
            )


def test_database_constraints_reject_guest_member_overlap_and_invalid_placement(
    tmp_path,
) -> None:
    store = _new_store(tmp_path)
    workspace_id = _create(store)["workspace"]["id"]
    con = store._write_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="cannot also be a guest"):
            con.execute(
                """
                INSERT INTO workspace_guests(
                  workspace_id,subject_id,guest_epoch,invited_by_subject_id,
                  created_at,updated_at,revoked_at
                ) VALUES(?,?,1,?,?,?,NULL)
                """,
                (workspace_id, "owner", "owner", 1, 1),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO workspace_page_sources(
                  workspace_id,source_owner_subject_id,source_page_id,teamspace_id,
                  placement,state,quarantined_at,source_revision,source_updated_at,
                  source_sha256,bound_by_subject_id,row_version,created_at,updated_at
                ) VALUES(?,?,?,'ts_missing','private','active',NULL,1,1,?,?,1,1,1)
                """,
                (
                    workspace_id,
                    "owner",
                    "p_invalid",
                    "sha256:" + "a" * 64,
                    "owner",
                ),
            )
        con.rollback()
    finally:
        con.close()
