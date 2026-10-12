from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_healing as healing
from code_assist_v1 import notion_workspace_reconciliation as reconciliation
from code_assist_v1.notion_workspace_store import (
    ActiveLegacyPageBindingCursor,
    WorkspaceHealingCounters,
    WorkspaceHealingLeaseConflict,
    WorkspaceOperationConflict,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


TOKEN_A = "hlease_" + "a" * 32
TOKEN_B = "hlease_" + "b" * 32


class _TrustedClock:
    def __init__(self, value: int = 100):
        self.value = value

    def __call__(self) -> int:
        return self.value


def _fixture(tmp_path):
    clock = _TrustedClock()
    store = WorkspaceStore(
        catalog_path_for_user_data_root(tmp_path), trusted_clock=clock
    )
    workspace = store.create_workspace(
        "owner", name="Healing", operation_id="create-healing"
    )["workspace"]
    return store, workspace, clock


def _start(store, workspace, **overrides):
    values = {
        "worker": "worker.one",
        "lease_token": TOKEN_A,
        "lease_seconds": 20,
        "operation_id": "start-healing",
    }
    values.update(overrides)
    return store.start_healing_run("owner", workspace["id"], **values)["run"]


def test_start_is_admin_scoped_exact_replay_and_secret_free(tmp_path) -> None:
    store, workspace, _clock = _fixture(tmp_path)
    member = store.set_member(
        "owner",
        workspace["id"],
        "member-one",
        role="member",
        base_version=workspace["version"],
        operation_id="add-member",
    )
    with pytest.raises(PermissionError):
        store.start_healing_run(
            "member-one",
            workspace["id"],
            worker="worker.one",
            lease_token=TOKEN_A,
            lease_seconds=20,
            operation_id="member-start",
        )
    with pytest.raises(FileNotFoundError):
        store.start_healing_run(
            "outsider",
            workspace["id"],
            worker="worker.one",
            lease_token=TOKEN_A,
            lease_seconds=20,
            operation_id="outsider-start",
        )

    first = _start(store, member["workspace"])
    replay = _start(store, member["workspace"])
    assert replay == first
    assert first["status"] == "running"
    assert first["version"] == 1
    assert first["lease"] == {
        "worker": "worker.one",
        "expires_at": 120,
        "fencing_generation": 1,
    }
    assert first["counters"] == WorkspaceHealingCounters().descriptor()

    con = sqlite3.connect(store.db_path)
    try:
        serialized = json.dumps(
            [
                row
                for table, column in (
                    ("workspace_operations", "result_json"),
                    ("workspace_audit_events", "payload_json"),
                    ("workspace_outbox", "payload_json"),
                )
                for row in con.execute(f"SELECT {column} FROM {table}").fetchall()
            ]
        )
        digest = con.execute(
            "SELECT lease_token_sha256 FROM workspace_healing_runs WHERE id=?",
            (first["id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert TOKEN_A not in serialized
    assert TOKEN_A not in str(digest)


def test_checkpoint_resume_completion_and_exact_replay_do_not_double_count(
    tmp_path,
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    cursor = ActiveLegacyPageBindingCursor(workspace["id"], "owner", "page-b")
    delta = WorkspaceHealingCounters(scanned=2, visited=1, safe_miss=1)
    clock.value = 101
    first = store.checkpoint_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=1,
        counters=delta,
        next_cursor=cursor,
        has_more=True,
        operation_id="checkpoint-one",
    )["run"]
    replay = store.checkpoint_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=1,
        counters=delta,
        next_cursor=cursor,
        has_more=True,
        operation_id="checkpoint-one",
    )["run"]
    assert replay == first
    assert first["version"] == 2
    assert first["cursor"] == {
        "workspace_id": workspace["id"],
        "source_owner_subject_id": "owner",
        "source_page_id": "page-b",
    }
    assert first["counters"]["scanned"] == 2
    assert first["counters"]["safe_miss"] == 1

    clock.value = 102
    completed = store.checkpoint_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=2,
        counters=WorkspaceHealingCounters(scanned=1, visited=1, match=1),
        next_cursor=None,
        has_more=False,
        operation_id="checkpoint-complete",
    )["run"]
    assert completed["status"] == "completed"
    assert completed["version"] == 3
    assert completed["counters"]["scanned"] == 3
    assert completed["counters"]["visited"] == 2
    assert completed["counters"]["match"] == 1
    assert completed["counters"]["safe_miss"] == 1
    assert completed["lease"]["worker"] == ""
    assert store.get_healing_run("owner", workspace["id"], run["id"]) == completed


def test_expired_reclaim_rotates_fence_and_old_lease_is_zero_write(tmp_path) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace, lease_seconds=5)
    clock.value = 104
    with pytest.raises(WorkspaceHealingLeaseConflict):
        store.reclaim_healing_run(
            "owner",
            workspace["id"],
            run["id"],
            worker="worker.two",
            lease_token=TOKEN_B,
            lease_seconds=10,
            expected_version=1,
            operation_id="early-reclaim",
        )
    clock.value = 105
    reclaimed = store.reclaim_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.two",
        lease_token=TOKEN_B,
        lease_seconds=10,
        expected_version=1,
        operation_id="expired-reclaim",
    )["run"]
    assert reclaimed["version"] == 2
    assert reclaimed["lease"] == {
        "worker": "worker.two",
        "expires_at": 115,
        "fencing_generation": 2,
    }
    baseline = store.audit_counts()
    clock.value = 106
    with pytest.raises(WorkspaceHealingLeaseConflict):
        store.checkpoint_healing_run(
            "owner",
            workspace["id"],
            run["id"],
            worker="worker.one",
            lease_token=TOKEN_A,
            fencing_generation=1,
            expected_version=1,
            counters=WorkspaceHealingCounters(),
            next_cursor=None,
            has_more=False,
            operation_id="stale-worker",
        )
    assert store.audit_counts() == baseline
    failed = store.fail_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.two",
        lease_token=TOKEN_B,
        fencing_generation=2,
        expected_version=2,
        failure_code="source_unavailable",
        operation_id="fail-run",
    )["run"]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "source_unavailable"
    assert failed["version"] == 3


@pytest.mark.parametrize(
    "counters",
    [
        WorkspaceHealingCounters(scanned=33),
        WorkspaceHealingCounters(scanned=1, visited=2),
        WorkspaceHealingCounters(scanned=1, visited=1),
    ],
)
def test_invalid_counter_envelopes_fail_before_catalog_open(
    tmp_path, monkeypatch, counters
) -> None:
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    monkeypatch.setattr(
        store,
        "_write_connection",
        lambda: (_ for _ in ()).throw(AssertionError("catalog must not open")),
    )
    with pytest.raises(ValueError):
        store.checkpoint_healing_run(
            "owner",
            "ws_invalid",
            "wheal_invalid",
            worker="worker.one",
            lease_token=TOKEN_A,
            fencing_generation=1,
            expected_version=1,
            counters=counters,
            next_cursor=None,
            has_more=False,
            operation_id="invalid-counters",
        )


def test_core_persists_shadow_checkpoint_and_replay_is_counter_idempotent(
    tmp_path, monkeypatch
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    cursor = ActiveLegacyPageBindingCursor(workspace["id"], "owner", "page-a")
    observed = reconciliation.WorkspaceReconciliationBatchResult(
        report={
            "scanned": 1,
            "visited": 1,
            "match": 0,
            "safe_miss": 1,
            "stale": 0,
            "superseded": 0,
            "ambiguous": 0,
            "permission_mismatch": 0,
            "unsafe_path": 0,
            "catalog_unavailable": 0,
        },
        next_cursor=cursor,
        has_more=True,
    )
    monkeypatch.setattr(
        healing,
        "reconcile_active_page_binding_batch",
        lambda *_args, **_kwargs: observed,
    )
    kwargs = {
        "actor_subject_id": "owner",
        "workspace_id": workspace["id"],
        "run_id": run["id"],
        "worker": "worker.one",
        "lease_token": TOKEN_A,
        "fencing_generation": 1,
        "expected_version": 1,
        "user_data_root": tmp_path,
        "expected_catalog_db_path": store.db_path,
        "source_db_for_owner": lambda _owner: tmp_path / "source.db",
        "source_quiesced_confirmed": True,
        "limit": 1,
    }
    clock.value = 101
    first = healing.checkpoint_one_shadow_batch(store, **kwargs)
    clock.value = 120
    replay = healing.checkpoint_one_shadow_batch(store, **kwargs)
    assert replay == first
    assert replay["run"]["version"] == 2
    assert replay["run"]["counters"]["safe_miss"] == 1

    with pytest.raises(WorkspaceOperationConflict):
        store.checkpoint_healing_run(
            "owner",
            workspace["id"],
            run["id"],
            worker="worker.one",
            lease_token=TOKEN_A,
            fencing_generation=1,
            expected_version=1,
            counters=WorkspaceHealingCounters(scanned=1, visited=1, match=1),
            next_cursor=cursor,
            has_more=True,
            operation_id=healing.healing_checkpoint_operation_id(
                run_id=run["id"], fencing_generation=1, expected_version=1
            ),
        )


def test_shadow_oversize_outcome_is_durably_checkpointed(tmp_path, monkeypatch) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    observed = reconciliation.WorkspaceReconciliationBatchResult(
        report={
            "scanned": 1,
            "visited": 1,
            "match": 0,
            "safe_miss": 0,
            "stale": 0,
            "superseded": 0,
            "ambiguous": 0,
            "permission_mismatch": 0,
            "unsafe_path": 0,
            "catalog_unavailable": 0,
            "oversize": 1,
        },
        next_cursor=None,
        has_more=False,
    )
    monkeypatch.setattr(
        healing,
        "reconcile_active_page_binding_batch",
        lambda *_args, **_kwargs: observed,
    )
    clock.value = 101
    result = healing.checkpoint_one_shadow_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=1,
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda _owner: tmp_path / "source.db",
        source_quiesced_confirmed=True,
        limit=1,
    )
    assert result["run"]["status"] == "completed"
    assert result["run"]["counters"]["oversize"] == 1
    assert result["shadow_report"]["oversize"] == 1


def test_core_derives_scan_cursor_from_durable_run_and_exposes_no_skip_input(
    tmp_path, monkeypatch
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    persisted = ActiveLegacyPageBindingCursor(
        workspace["id"], "owner", "page-a"
    )
    clock.value = 101
    run = store.checkpoint_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=1,
        counters=WorkspaceHealingCounters(scanned=1),
        next_cursor=persisted,
        has_more=True,
        operation_id="seed-cursor",
    )["run"]
    seen = []

    def observe(*_args, **kwargs):
        seen.append(kwargs["after"])
        return reconciliation.WorkspaceReconciliationBatchResult(
            report={
                "scanned": 0,
                "visited": 0,
                "match": 0,
                "safe_miss": 0,
                "stale": 0,
                "superseded": 0,
                "ambiguous": 0,
                "permission_mismatch": 0,
                "unsafe_path": 0,
                "catalog_unavailable": 0,
            },
            next_cursor=None,
            has_more=False,
        )

    monkeypatch.setattr(healing, "reconcile_active_page_binding_batch", observe)
    clock.value = 102
    result = healing.checkpoint_one_shadow_batch(
        store,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=2,
        user_data_root=tmp_path,
        expected_catalog_db_path=store.db_path,
        source_db_for_owner=lambda _owner: tmp_path / "source.db",
        source_quiesced_confirmed=True,
        limit=1,
    )
    assert seen == [persisted]
    assert result["run"]["status"] == "completed"
    with pytest.raises(TypeError):
        healing.checkpoint_one_shadow_batch(
            store,
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            run_id=run["id"],
            worker="worker.one",
            lease_token=TOKEN_A,
            fencing_generation=1,
            expected_version=2,
            user_data_root=tmp_path,
            expected_catalog_db_path=store.db_path,
            source_db_for_owner=lambda _owner: tmp_path / "source.db",
            source_quiesced_confirmed=True,
            after=ActiveLegacyPageBindingCursor(
                workspace["id"], "zz", "skipped-page"
            ),
            limit=1,
        )


@pytest.mark.parametrize(
    ("lease_token", "clock_time"),
    [
        (TOKEN_B, 101),
        (TOKEN_A, 105),
    ],
    ids=["wrong-token", "expired-lease"],
)
def test_core_rejects_non_live_lease_before_source_or_catalog_effect(
    tmp_path, monkeypatch, lease_token, clock_time
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace, lease_seconds=5)
    clock.value = clock_time
    calls = {"reconcile": 0, "resolver": 0}

    def unexpected_reconcile(*_args, **_kwargs):
        calls["reconcile"] += 1
        raise AssertionError("reconciliation must not run for a non-live lease")

    def unexpected_resolver(_owner):
        calls["resolver"] += 1
        raise AssertionError("source resolver must not run for a non-live lease")

    monkeypatch.setattr(
        healing, "reconcile_active_page_binding_batch", unexpected_reconcile
    )
    baseline = store.audit_counts()
    with pytest.raises(WorkspaceHealingLeaseConflict):
        healing.checkpoint_one_shadow_batch(
            store,
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            run_id=run["id"],
            worker="worker.one",
            lease_token=lease_token,
            fencing_generation=1,
            expected_version=1,
            user_data_root=tmp_path,
            expected_catalog_db_path=store.db_path,
            source_db_for_owner=unexpected_resolver,
            source_quiesced_confirmed=True,
            limit=1,
        )

    assert calls == {"reconcile": 0, "resolver": 0}
    assert store.audit_counts() == baseline
    unchanged = store.get_healing_run("owner", workspace["id"], run["id"])
    assert unchanged["version"] == 1
    assert unchanged["counters"] == WorkspaceHealingCounters().descriptor()


def test_healing_time_is_constructor_scoped_and_has_no_per_call_override() -> None:
    for method_name in (
        "assert_live_healing_run_lease",
        "get_healing_checkpoint_replay",
        "start_healing_run",
        "reclaim_healing_run",
        "checkpoint_healing_run",
        "fail_healing_run",
    ):
        assert "now" not in inspect.signature(
            getattr(WorkspaceStore, method_name)
        ).parameters
    assert "now" not in inspect.signature(
        healing.checkpoint_one_shadow_batch
    ).parameters


@pytest.mark.parametrize(
    ("set_clause", "parameters"),
    [
        (
            "row_version=2,updated_at=?,scanned_count=?,"
            "cursor_owner_subject_id=?,cursor_page_id=?",
            (101, 0.5, "owner", "page-a"),
        ),
        (
            "row_version=2,updated_at=?,scanned_count=1,"
            "cursor_owner_subject_id=?,cursor_page_id=?",
            (101, "owner", "page-a "),
        ),
        (
            "row_version=2,updated_at=?,scanned_count=1,"
            "cursor_owner_subject_id=?,cursor_page_id=?",
            (120, "owner", "page-a"),
        ),
        (
            "row_version=2,updated_at=?,fencing_generation=2,"
            "lease_worker=?,lease_token_sha256=?,lease_expires_at=?",
            (
                120,
                "worker.two",
                "sha256:" + hashlib.sha256(TOKEN_B.encode("utf-8")).hexdigest(),
                120 + 3601,
            ),
        ),
    ],
    ids=[
        "fractional-counter",
        "invalid-cursor-grammar",
        "expired-checkpoint",
        "oversized-reclaimed-lease",
    ],
)
def test_raw_sql_rejects_invalid_healing_transitions(
    tmp_path, set_clause, parameters
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    clock.value = int(parameters[0])
    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        before = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                f"UPDATE workspace_healing_runs SET {set_clause} WHERE id=?",
                (*parameters, run["id"]),
            )
        after = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
    finally:
        con.close()
    assert after == before
    assert store.verify_deep_health()["healthy"] is True


def test_raw_sql_rejects_per_batch_counter_gap_reallocation(tmp_path) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace)
    clock.value = 101
    seeded = store.checkpoint_healing_run(
        "owner",
        workspace["id"],
        run["id"],
        worker="worker.one",
        lease_token=TOKEN_A,
        fencing_generation=1,
        expected_version=1,
        counters=WorkspaceHealingCounters(scanned=32),
        next_cursor=ActiveLegacyPageBindingCursor(
            workspace["id"], "owner", "page-a"
        ),
        has_more=True,
        operation_id="seed-counter-gap",
    )["run"]
    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        before = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="invalid healing run transition"):
            con.execute(
                """
                UPDATE workspace_healing_runs
                SET row_version=3,updated_at=102,scanned_count=33,
                    visited_count=32,safe_miss_count=32,
                    cursor_owner_subject_id='owner',cursor_page_id='page-b'
                WHERE id=?
                """,
                (run["id"],),
            )
        after = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
    finally:
        con.close()
    assert seeded["counters"]["scanned"] == 32
    assert after == before
    assert store.verify_deep_health()["healthy"] is True


def test_deep_health_detects_pristine_raw_run_without_ledger(tmp_path) -> None:
    store, workspace, clock = _fixture(tmp_path)
    token_digest = "sha256:" + hashlib.sha256(TOKEN_A.encode("utf-8")).hexdigest()
    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        con.execute(
            """
            INSERT INTO workspace_healing_runs(
              id,workspace_id,started_by_subject_id,status,has_more,
              lease_worker,lease_token_sha256,lease_expires_at,
              started_at,updated_at
            ) VALUES(?,?,?,'running',1,?,?,120,100,100)
            """,
            (
                "wheal_" + "c" * 32,
                workspace["id"],
                "owner",
                "worker.one",
                token_digest,
            ),
        )
        con.commit()
    finally:
        con.close()
    report = store.deep_health()
    assert report["healthy"] is False
    assert "healing_run_ledger_invalid" in report["issues"]


def test_raw_sql_cannot_forge_old_timestamp_after_real_lease_expiry(tmp_path) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace, lease_seconds=5)
    clock.value = 105

    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        before = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="invalid healing run transition"):
            con.execute(
                """
                UPDATE workspace_healing_runs
                SET row_version=2,updated_at=101,scanned_count=1,
                    cursor_owner_subject_id='owner',cursor_page_id='page-a'
                WHERE id=?
                """,
                (run["id"],),
            )
        after = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
    finally:
        con.close()
    assert after == before


@pytest.mark.parametrize(
    ("clock_time", "lease_seconds", "set_clause", "parameters"),
    [
        (
            101,
            20,
            "row_version=2,updated_at=110,scanned_count=1,"
            "cursor_owner_subject_id='owner',cursor_page_id='page-a'",
            (),
        ),
        (
            101,
            20,
            "status='completed',has_more=0,row_version=2,updated_at=110,"
            "lease_worker=NULL,lease_token_sha256=NULL,lease_expires_at=NULL,"
            "completed_at=110",
            (),
        ),
        (
            101,
            20,
            "status='failed',row_version=2,updated_at=110,"
            "lease_worker=NULL,lease_token_sha256=NULL,lease_expires_at=NULL,"
            "failure_code='source_unavailable',failed_at=110",
            (),
        ),
        (
            105,
            5,
            "row_version=2,updated_at=110,fencing_generation=2,"
            "lease_worker='worker.two',lease_token_sha256=?,lease_expires_at=115",
            (
                "sha256:"
                + hashlib.sha256(TOKEN_B.encode("utf-8")).hexdigest(),
            ),
        ),
    ],
    ids=["checkpoint", "completion", "failure", "reclaim"],
)
def test_raw_sql_cannot_write_future_healing_timestamps(
    tmp_path, clock_time, lease_seconds, set_clause, parameters
) -> None:
    store, workspace, clock = _fixture(tmp_path)
    run = _start(store, workspace, lease_seconds=lease_seconds)
    clock.value = clock_time
    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        before = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="invalid healing run transition"):
            con.execute(
                f"UPDATE workspace_healing_runs SET {set_clause} WHERE id=?",
                (*parameters, run["id"]),
            )
        after = con.execute(
            "SELECT * FROM workspace_healing_runs WHERE id=?", (run["id"],)
        ).fetchone()
    finally:
        con.close()
    assert after == before
    assert store.verify_deep_health()["healthy"] is True


def test_raw_sql_cannot_start_healing_run_in_trusted_clock_future(tmp_path) -> None:
    store, workspace, clock = _fixture(tmp_path)
    token_digest = "sha256:" + hashlib.sha256(TOKEN_A.encode("utf-8")).hexdigest()
    con = sqlite3.connect(store.db_path)
    con.create_function("workspace_healing_now", 0, clock)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="pristine live lease"):
            con.execute(
                """
                INSERT INTO workspace_healing_runs(
                  id,workspace_id,started_by_subject_id,status,has_more,
                  lease_worker,lease_token_sha256,lease_expires_at,
                  started_at,updated_at
                ) VALUES(?,?,?,'running',1,?,?,130,110,110)
                """,
                (
                    "wheal_" + "f" * 32,
                    workspace["id"],
                    "owner",
                    "worker.one",
                    token_digest,
                ),
            )
    finally:
        con.close()


def test_unregistered_raw_writer_fails_closed_on_healing_mutation(tmp_path) -> None:
    store, workspace, _clock = _fixture(tmp_path)
    run = _start(store, workspace)
    con = sqlite3.connect(store.db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="workspace_healing_now"):
            con.execute(
                "UPDATE workspace_healing_runs SET row_version=2 WHERE id=?",
                (run["id"],),
            )
    finally:
        con.close()
