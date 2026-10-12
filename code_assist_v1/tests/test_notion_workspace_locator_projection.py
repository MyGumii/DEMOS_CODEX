from __future__ import annotations

import json
import sqlite3

import pytest

from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    LegacyPublicSourceLocator,
    WorkspaceOperationConflict,
    WorkspaceRevisionConflict,
    WorkspaceStore,
    WorkspaceSubstrateUnavailable,
    catalog_path_for_user_data_root,
)


FILE_A = "f_" + "a" * 32
FILE_B = "f_" + "b" * 32
FILE_C = "f_" + "c" * 32
FILE_Z = "f_" + "f" * 32
FILE_WRONG_GENERATION = "f_" + "e" * 32


def _store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(tmp_path))


def _workspace(store: WorkspaceStore) -> dict:
    return store.create_workspace(
        "owner", name="Locator projection", operation_id="create"
    )["workspace"]


def _active_binding(
    store: WorkspaceStore,
    workspace_id: str,
    *,
    page_id: str = "page_one",
    owner: str = "owner",
    actor: str = "owner",
    base_version: int = 1,
    operation_suffix: str = "one",
) -> dict:
    proof = LegacyPageSourceProof(
        actor_subject_id=actor,
        owner_subject_id=owner,
        page_id=page_id,
        revision=1,
        updated_at=100,
        content_sha256="sha256:" + "a" * 64,
    )
    return store.bind_page_source(
        actor,
        workspace_id,
        proof,
        source_revalidator=lambda candidate: candidate == proof,
        base_version=base_version,
        operation_id=f"bind-{operation_suffix}",
    )


def _projection(
    binding: dict,
    *,
    actor: str = "owner",
    owner: str = "owner",
    files: tuple[str, ...] = (FILE_A, FILE_B),
    token_hash: str = "sha256:" + "b" * 64,
    allow_duplicate: bool = False,
) -> LegacyPageLocatorProjection:
    return LegacyPageLocatorProjection(
        actor_subject_id=actor,
        owner_subject_id=owner,
        page_id=binding["binding"]["source_page_id"],
        binding_version=binding["binding"]["version"],
        file_sources=tuple(LegacyFileSourceLocator(item) for item in files),
        public_sources=(
            LegacyPublicSourceLocator(token_hash, allow_duplicate),
        ),
    )


def _rows(store: WorkspaceStore) -> tuple[list[tuple], list[tuple]]:
    con = sqlite3.connect(store.db_path)
    try:
        files = con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   binding_version,source_file_id
            FROM workspace_file_sources ORDER BY source_file_id
            """
        ).fetchall()
        public = con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   binding_version,token_sha256,allow_duplicate
            FROM workspace_public_sources ORDER BY token_sha256
            """
        ).fetchall()
        return files, public
    finally:
        con.close()


def _summary_rows(store: WorkspaceStore) -> list[tuple]:
    con = sqlite3.connect(store.db_path)
    try:
        return con.execute(
            """
            SELECT workspace_id,source_owner_subject_id,source_page_id,
                   binding_version,projection_sha256,file_source_count,
                   public_source_count,projected_at
            FROM workspace_page_source_projections
            ORDER BY workspace_id,source_page_id
            """
        ).fetchall()
    finally:
        con.close()


def test_fresh_schema_1305_and_1303_marker_fail_closed(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    health = store.verify_deep_health()
    assert health["schema_version"] == 2026081317
    con = sqlite3.connect(store.db_path)
    try:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "workspace_page_source_projections",
            "workspace_file_sources",
            "workspace_public_sources",
        } <= tables
        con.execute(
            """
            UPDATE workspace_schema_migrations
            SET version=2026081303,name='notion_workspace_v2_np2a_p1_closeout'
            """
        )
        con.commit()
    finally:
        con.close()
    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(WorkspaceSubstrateUnavailable):
        store.get_workspace("owner", workspace["id"])


def test_projection_replace_is_atomic_secret_free_and_replayable(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    projection = _projection(bound)
    calls = []
    result = store.replace_page_locator_projection(
        "owner",
        workspace["id"],
        projection,
        projection_revalidator=lambda candidate: calls.append(candidate) or True,
        base_version=bound["workspace"]["version"],
        operation_id="replace-one",
    )
    assert calls == [projection]
    assert result["projection"] == {
        "workspace_id": workspace["id"],
        "source_owner_subject_id": "owner",
        "source_page_id": "page_one",
        "binding_version": bound["binding"]["version"],
        "file_source_count": 2,
        "public_source_count": 1,
        "changed": True,
    }
    files, public = _rows(store)
    assert [row[-1] for row in files] == [FILE_A, FILE_B]
    assert public[0][-2:] == ("sha256:" + "b" * 64, 0)
    summary = _summary_rows(store)
    assert len(summary) == 1
    assert summary[0][5:7] == (2, 1)
    assert summary[0][4].startswith("sha256:")

    replay = store.replace_page_locator_projection(
        "owner",
        workspace["id"],
        projection,
        projection_revalidator=lambda _candidate: (_ for _ in ()).throw(
            AssertionError("replay must not call the external authority")
        ),
        base_version=bound["workspace"]["version"],
        operation_id="replace-one",
    )
    assert replay == result

    con = sqlite3.connect(store.db_path)
    try:
        columns = {
            row[1]
            for table in ("workspace_file_sources", "workspace_public_sources")
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        forbidden = {"token", "filename", "original_name", "stored_name", "rel_path"}
        assert columns.isdisjoint(forbidden)
        payloads = [
            value
            for row in con.execute(
                """
                SELECT result_json FROM workspace_operations
                WHERE operation_id='replace-one'
                UNION ALL
                SELECT payload_json FROM workspace_audit_events
                WHERE operation_id='replace-one'
                UNION ALL
                SELECT payload_json FROM workspace_outbox
                WHERE operation_id='replace-one'
                """
            ).fetchall()
            for value in row
        ]
    finally:
        con.close()
    for payload in payloads:
        assert FILE_A not in payload
        assert FILE_B not in payload
        assert "sha256:" + "b" * 64 not in payload
        assert "token" not in payload.lower()
        assert "filename" not in payload.lower()
        assert "rel_path" not in payload.lower()
        assert "stored_name" not in payload.lower()


def test_exact_same_projection_is_version_noop_but_has_ledger(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    projection = _projection(bound, files=(FILE_A,))
    first = store.replace_page_locator_projection(
        "owner",
        workspace["id"],
        projection,
        projection_revalidator=lambda _candidate: True,
        base_version=bound["workspace"]["version"],
        operation_id="replace-first",
    )
    projected_at = _summary_rows(store)[0][-1]
    second = store.replace_page_locator_projection(
        "owner",
        workspace["id"],
        projection,
        projection_revalidator=lambda _candidate: True,
        base_version=first["workspace"]["version"],
        operation_id="replace-same",
    )
    assert second["projection"]["changed"] is False
    assert second["workspace"]["version"] == first["workspace"]["version"]
    assert second["workspace"]["security_epoch"] == first["workspace"]["security_epoch"]
    assert _summary_rows(store)[0][-1] == projected_at
    assert store.audit_counts() == {
        "operations": 5,
        "audit_events": 5,
        "outbox_events": 5,
    }


@pytest.mark.parametrize("case", ["stale_workspace", "stale_binding", "callback_false"])
def test_rejected_projection_has_zero_partial_writes(tmp_path, case) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    projection = _projection(bound, files=(FILE_A,))
    version = bound["workspace"]["version"]
    callback = lambda _candidate: True
    if case == "stale_workspace":
        version -= 1
        expected_error = WorkspaceRevisionConflict
    elif case == "stale_binding":
        projection = LegacyPageLocatorProjection(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id="page_one",
            binding_version=bound["binding"]["version"] + 1,
            file_sources=(LegacyFileSourceLocator(FILE_A),),
        )
        expected_error = ValueError
    else:
        callback = lambda _candidate: False
        expected_error = ValueError
    before = store.audit_counts()
    with pytest.raises(expected_error):
        store.replace_page_locator_projection(
            "owner",
            workspace["id"],
            projection,
            projection_revalidator=callback,
            base_version=version,
            operation_id=f"reject-{case}",
        )
    assert _rows(store) == ([], [])
    assert _summary_rows(store) == []
    assert store.audit_counts() == before


def test_permission_revocation_and_operation_hash_conflict_write_nothing(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    version = store.set_member(
        "owner",
        workspace["id"],
        "admin",
        role="membership_admin",
        base_version=1,
        operation_id="add-admin",
    )["workspace"]["version"]
    bound = _active_binding(
        store,
        workspace["id"],
        actor="admin",
        base_version=version,
    )
    projection = _projection(bound, actor="admin")
    first = store.replace_page_locator_projection(
        "admin",
        workspace["id"],
        projection,
        projection_revalidator=lambda _candidate: True,
        base_version=bound["workspace"]["version"],
        operation_id="admin-projection",
    )
    changed_projection = _projection(
        bound,
        actor="admin",
        files=(FILE_A, FILE_C),
    )
    with pytest.raises(WorkspaceOperationConflict):
        store.replace_page_locator_projection(
            "admin",
            workspace["id"],
            changed_projection,
            projection_revalidator=lambda _candidate: True,
            base_version=bound["workspace"]["version"],
            operation_id="admin-projection",
        )
    rows_before = _rows(store)
    store.revoke_member(
        "owner",
        workspace["id"],
        "admin",
        base_version=first["workspace"]["version"],
        operation_id="revoke-admin",
    )
    with pytest.raises((FileNotFoundError, PermissionError)):
        store.replace_page_locator_projection(
            "admin",
            workspace["id"],
            projection,
            projection_revalidator=lambda _candidate: (_ for _ in ()).throw(
                AssertionError("revoked replay must not call external authority")
            ),
            base_version=bound["workspace"]["version"],
            operation_id="admin-projection",
        )
    assert _rows(store) == rows_before


def test_normalization_requires_sorted_unique_bounded_secret_free_descriptors(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    base = bound["workspace"]["version"]

    invalid = [
        LegacyPageLocatorProjection(
            "owner", "owner", "page_one", 1,
            (LegacyFileSourceLocator(FILE_Z), LegacyFileSourceLocator(FILE_A)), (),
        ),
        LegacyPageLocatorProjection(
            "owner", "owner", "page_one", 1,
            (LegacyFileSourceLocator(FILE_A), LegacyFileSourceLocator(FILE_A)), (),
        ),
        LegacyPageLocatorProjection(
            "owner", "owner", "page_one", 1,
            (LegacyFileSourceLocator("payroll.pdf"),), (),
        ),
        LegacyPageLocatorProjection(
            "owner", "owner", "page_one", 1, (),
            (
                LegacyPublicSourceLocator("sha256:" + "1" * 64, False),
                LegacyPublicSourceLocator("sha256:" + "2" * 64, False),
            ),
        ),
        LegacyPageLocatorProjection(
            "owner", "owner", "page_one", 1, (),
            (LegacyPublicSourceLocator("raw-public-token", False),),
        ),
    ]
    for index, projection in enumerate(invalid):
        with pytest.raises(ValueError):
            store.replace_page_locator_projection(
                "owner",
                workspace["id"],
                projection,
                projection_revalidator=lambda _candidate: True,
                base_version=base,
                operation_id=f"invalid-{index}",
            )
    assert _rows(store) == ([], [])
    assert _summary_rows(store) == []


@pytest.mark.parametrize(
    ("stale_base", "stale_binding"),
    [(True, False), (False, True), (True, True)],
)
def test_nonmember_cannot_probe_workspace_version_or_binding_generation(
    tmp_path, stale_base, stale_binding
) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    projection = LegacyPageLocatorProjection(
        actor_subject_id="intruder",
        owner_subject_id="owner",
        page_id="page_one",
        binding_version=(
            bound["binding"]["version"] + 99
            if stale_binding
            else bound["binding"]["version"]
        ),
        file_sources=(LegacyFileSourceLocator(FILE_A),),
    )
    callback_called = False

    def callback(_candidate):
        nonlocal callback_called
        callback_called = True
        return True

    base_version = (
        bound["workspace"]["version"] - 1
        if stale_base
        else bound["workspace"]["version"]
    )
    with pytest.raises(FileNotFoundError) as error:
        store.replace_page_locator_projection(
            "intruder",
            workspace["id"],
            projection,
            projection_revalidator=callback,
            base_version=base_version,
            operation_id=f"intruder-{stale_base}-{stale_binding}",
        )
    assert str(error.value) == "workspace not found"
    assert not hasattr(error.value, "current_version")
    assert callback_called is False
    assert _rows(store) == ([], [])
    assert _summary_rows(store) == []


def test_public_digest_is_globally_unique_and_collision_rolls_back_replace(tmp_path) -> None:
    store = _store(tmp_path)
    first_workspace = _workspace(store)
    first = _active_binding(store, first_workspace["id"], operation_suffix="first")
    token_hash = "sha256:" + "9" * 64
    first_projection = _projection(first, files=(FILE_A,), token_hash=token_hash)
    first_result = store.replace_page_locator_projection(
        "owner",
        first_workspace["id"],
        first_projection,
        projection_revalidator=lambda _candidate: True,
        base_version=first["workspace"]["version"],
        operation_id="project-first",
    )

    second_workspace = store.create_workspace(
        "owner", name="Second", operation_id="create-second"
    )["workspace"]
    second = _active_binding(
        store,
        second_workspace["id"],
        page_id="page_two",
        operation_suffix="second",
    )
    second_projection = _projection(
        second,
        files=(FILE_Z,),
        token_hash=token_hash,
    )
    before = store.audit_counts()
    with pytest.raises(ValueError, match="already projected"):
        store.replace_page_locator_projection(
            "owner",
            second_workspace["id"],
            second_projection,
            projection_revalidator=lambda _candidate: True,
            base_version=second["workspace"]["version"],
            operation_id="project-second-collision",
        )
    files, public = _rows(store)
    assert files == [
        (
            first_workspace["id"],
            "owner",
            "page_one",
            first["binding"]["version"],
            FILE_A,
        )
    ]
    assert len(public) == 1
    assert store.audit_counts() == before
    assert first_result["projection"]["changed"] is True


def test_page_restage_quarantine_and_raw_delete_invalidate_projection(tmp_path) -> None:
    for mode in ("restage", "quarantine", "delete"):
        store = _store(tmp_path / mode)
        workspace = _workspace(store)
        bound = _active_binding(store, workspace["id"])
        result = store.replace_page_locator_projection(
            "owner",
            workspace["id"],
            _projection(bound),
            projection_revalidator=lambda _candidate: True,
            base_version=bound["workspace"]["version"],
            operation_id=f"projection-{mode}",
        )
        assert all(_rows(store))
        if mode == "restage":
            proof = LegacyPageSourceProof(
                "owner", "owner", "page_one", 2, 200,
                "sha256:" + "c" * 64,
            )
            store.stage_page_source_binding(
                "owner",
                workspace["id"],
                proof,
                base_version=result["workspace"]["version"],
                operation_id="restage",
            )
        elif mode == "quarantine":
            store.quarantine_page_source_if_stale(
                "owner",
                workspace["id"],
                "owner",
                "page_one",
                source_revalidator=lambda _proof: False,
                expected_binding_version=bound["binding"]["version"],
                base_version=result["workspace"]["version"],
                operation_id="quarantine",
            )
        else:
            con = sqlite3.connect(store.db_path)
            con.execute("PRAGMA foreign_keys=ON")
            try:
                con.execute(
                    """
                    DELETE FROM workspace_page_sources
                    WHERE workspace_id=? AND source_page_id='page_one'
                    """,
                    (workspace["id"],),
                )
                con.commit()
            finally:
                con.close()
        assert _rows(store) == ([], [])
        assert _summary_rows(store) == []


def test_raw_child_cross_generation_update_insert_and_public_mutation_rejected(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    result = store.replace_page_locator_projection(
        "owner",
        workspace["id"],
        _projection(bound, files=(FILE_A,)),
        projection_revalidator=lambda _candidate: True,
        base_version=bound["workspace"]["version"],
        operation_id="projection",
    )
    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO workspace_file_sources(
                  workspace_id,source_owner_subject_id,source_page_id,
                  binding_version,source_file_id,created_at
                ) VALUES(?,?,?,?,?,1)
                """,
                (
                    workspace["id"], "owner", "page_one",
                    bound["binding"]["version"] + 1, FILE_WRONG_GENERATION,
                ),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                UPDATE workspace_file_sources SET workspace_id='ws_other'
                WHERE source_file_id=?
                """
                , (FILE_A,)
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            con.execute(
                """
                UPDATE workspace_public_sources SET allow_duplicate=1
                WHERE source_page_id='page_one'
                """
            )
        con.rollback()
    finally:
        con.close()
    assert result["projection"]["changed"] is True
    assert store.verify_deep_health()["healthy"] is True


@pytest.mark.parametrize(
    "failure_table",
    ["workspace_audit_events", "workspace_outbox", "workspace_operations"],
)
def test_projection_ledger_failures_roll_back_replace(tmp_path, failure_table) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    bound = _active_binding(store, workspace["id"])
    original = store._write_connection

    def fault_connection():
        con = original()
        con.execute(
            f"""
            CREATE TEMP TRIGGER fail_projection_ledger
            BEFORE INSERT ON main.{failure_table}
            BEGIN SELECT RAISE(ABORT, 'projection ledger failure'); END
            """
        )
        return con

    store._write_connection = fault_connection
    try:
        with pytest.raises(sqlite3.IntegrityError, match="projection ledger failure"):
            store.replace_page_locator_projection(
                "owner",
                workspace["id"],
                _projection(bound),
                projection_revalidator=lambda _candidate: True,
                base_version=bound["workspace"]["version"],
                operation_id="projection-fault",
            )
    finally:
        store._write_connection = original
    assert _rows(store) == ([], [])
    assert _summary_rows(store) == []


def test_callback_side_effect_permission_revoke_is_rechecked_and_writes_nothing(tmp_path) -> None:
    store = _store(tmp_path)
    workspace = _workspace(store)
    version = store.set_member(
        "owner", workspace["id"], "admin", role="membership_admin",
        base_version=1, operation_id="admin",
    )["workspace"]["version"]
    bound = _active_binding(
        store, workspace["id"], actor="admin", base_version=version,
    )

    def revoke_during_callback(_projection):
        con = sqlite3.connect(store.db_path)
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute(
                """
                UPDATE workspace_memberships
                SET revoked_at=1,membership_epoch=membership_epoch+1
                WHERE workspace_id=? AND subject_id='admin'
                """,
                (workspace["id"],),
            )
            con.commit()
        finally:
            con.close()
        return True

    before = store.audit_counts()
    with pytest.raises((FileNotFoundError, PermissionError)):
        store.replace_page_locator_projection(
            "admin",
            workspace["id"],
            _projection(bound, actor="admin"),
            projection_revalidator=revoke_during_callback,
            base_version=bound["workspace"]["version"],
            operation_id="revoked-during-callback",
        )
    assert _rows(store) == ([], [])
    assert _summary_rows(store) == []
    assert store.audit_counts() == before
