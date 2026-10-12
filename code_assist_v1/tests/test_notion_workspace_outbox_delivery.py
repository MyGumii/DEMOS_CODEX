from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    WorkspaceOutboxLeaseConflict,
    WorkspaceStore,
    WorkspaceSubstrateUnavailable,
    _OUTBOX_CLAIM_CANDIDATES_SQL,
    catalog_path_for_user_data_root,
)


def _store_with_bound_event(tmp_path) -> WorkspaceStore:
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="delivery", operation_id="create-workspace"
    )["workspace"]
    proof = LegacyPageSourceProof(
        actor_subject_id="owner",
        owner_subject_id="owner",
        page_id="p_delivery",
        revision=1,
        updated_at=100,
        content_sha256="sha256:" + "a" * 64,
    )
    staged = store.stage_page_source_binding(
        "owner", workspace["id"], proof,
        base_version=workspace["version"], operation_id="stage-page",
    )
    store.activate_page_source_binding(
        "owner", workspace["id"], proof,
        source_revalidator=lambda _proof: True,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-page",
    )
    return store


def _claim(store: WorkspaceStore, worker: str = "worker.one", **kwargs):
    rows = store.claim_pending_outbox(
        "page.projector", worker, now=1000, lease_seconds=10, **kwargs
    )
    assert len(rows) == 1
    return rows[0]


def _delivery_row(store: WorkspaceStore, consumer: str = "page.projector"):
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM workspace_outbox_deliveries WHERE consumer=?",
            (consumer,),
        ).fetchone())
    finally:
        con.close()


def test_claim_allowlist_and_two_workers_never_share_a_live_lease(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    first = _claim(store)
    assert first["event_type"] == "workspace.page_source_bound"
    assert first["aggregate_type"] == "legacy_page_source"
    assert first["actor_subject_id"] == "owner"
    assert first["attempt_count"] == 1
    assert first["lease_token"].startswith("wlease_")
    assert store.claim_pending_outbox(
        "page.projector", "worker.two", now=1000, lease_seconds=10
    ) == []
    # Workspace creation and staging events remain unaffected and unclaimed.
    assert store.audit_counts()["outbox_events"] == 3


def test_claim_query_plan_uses_allowlisted_pending_parent_index(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    con = sqlite3.connect(store.db_path)
    try:
        details = [
            str(row[3])
            for row in con.execute(
                "EXPLAIN QUERY PLAN " + _OUTBOX_CLAIM_CANDIDATES_SQL,
                ("page.projector", 1000, 1000, 32),
            ).fetchall()
        ]
    finally:
        con.close()
    assert any(
        "USING INDEX idx_workspace_outbox_delivery_parent_claim" in detail
        for detail in details
    ), details
    assert not any("USE TEMP B-TREE FOR ORDER BY" in detail for detail in details), details


def test_claim_is_exact_and_bounded_with_many_unrelated_parents(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    con = sqlite3.connect(store.db_path)
    try:
        workspace_id = str(con.execute(
            "SELECT workspace_id FROM workspace_outbox LIMIT 1"
        ).fetchone()[0])
        con.executemany(
            """INSERT INTO workspace_outbox(
                 id,workspace_id,actor_subject_id,event_type,aggregate_type,
                 aggregate_id,operation_id,payload_json,created_at,delivered_at
               ) VALUES(?,?,?,'workspace.created','workspace',?,?,?, ?,NULL)""",
            [
                (
                    f"wout_noise_{index:04d}", workspace_id, "bulk.actor",
                    f"ws_noise_{index:04d}", f"noise-{index:04d}", "{}", index,
                )
                for index in range(5000)
            ],
        )
        con.executemany(
            """INSERT INTO workspace_outbox(
                 id,workspace_id,actor_subject_id,event_type,aggregate_type,
                 aggregate_id,operation_id,payload_json,created_at,delivered_at
               ) VALUES(?,?,?,'workspace.page_source_bound','legacy_page_source',
                        ?,?,?,?,NULL)""",
            [
                (
                    f"wout_allowed_{index:04d}", workspace_id, "bulk.actor",
                    f"p_allowed_{index:04d}", f"allowed-{index:04d}", "{}",
                    100 + index,
                )
                for index in range(40)
            ],
        )
        con.commit()
    finally:
        con.close()

    first = store.claim_pending_outbox(
        "page.projector", "worker.one", limit=7, now=1000, lease_seconds=10
    )
    second = store.claim_pending_outbox(
        "page.projector", "worker.two", limit=7, now=1000, lease_seconds=10
    )
    assert [item["outbox_id"] for item in first] == [
        f"wout_allowed_{index:04d}" for index in range(7)
    ]
    assert [item["outbox_id"] for item in second] == [
        f"wout_allowed_{index:04d}" for index in range(7, 14)
    ]
    assert all(
        item["event_type"] == "workspace.page_source_bound"
        and item["aggregate_type"] == "legacy_page_source"
        for item in first + second
    )
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            """SELECT COUNT(*) FROM workspace_outbox_deliveries d
               JOIN workspace_outbox o ON o.id=d.outbox_id
               WHERE o.event_type='workspace.created'"""
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox_deliveries"
        ).fetchone()[0] == 14
    finally:
        con.close()


def test_expiry_crash_reclaim_rotates_token_and_increments_attempt(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    first = _claim(store)
    assert store.claim_pending_outbox(
        "page.projector", "worker.two", now=1009, lease_seconds=10
    ) == []
    second = store.claim_pending_outbox(
        "page.projector", "worker.two", now=1010, lease_seconds=20
    )[0]
    assert second["outbox_id"] == first["outbox_id"]
    assert second["attempt_count"] == 2
    assert second["lease_token"] != first["lease_token"]
    with pytest.raises(WorkspaceOutboxLeaseConflict, match="lease unavailable"):
        store.ack_outbox_delivery(
            "page.projector", "worker.one", first["lease_token"], now=1011
        )


def test_wrong_consumer_worker_and_stale_token_are_zero_write(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    before = _delivery_row(store)
    for consumer, worker, token in (
        ("other.consumer", "worker.one", claimed["lease_token"]),
        ("page.projector", "worker.two", claimed["lease_token"]),
        ("page.projector", "worker.one", "wlease_" + "0" * 32),
    ):
        with pytest.raises(WorkspaceOutboxLeaseConflict, match="lease unavailable"):
            store.ack_outbox_delivery(consumer, worker, token, now=1001)
    assert _delivery_row(store) == before


def test_retry_availability_reclaim_and_error_code_is_sanitized(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    first = _claim(store)
    retried = store.retry_outbox_delivery(
        "page.projector", "worker.one", first["lease_token"], "source_stale",
        now=1001, backoff_seconds=30,
    )
    assert retried["status"] == "retry"
    assert retried["available_at"] == 1031
    assert retried["last_error_code"] == "source_stale"
    assert store.claim_pending_outbox(
        "page.projector", "worker.two", now=1030, lease_seconds=10
    ) == []
    reclaimed = store.claim_pending_outbox(
        "page.projector", "worker.two", now=1031, lease_seconds=10
    )[0]
    assert reclaimed["attempt_count"] == 2
    assert "source_stale" not in str(reclaimed)
    before = _delivery_row(store)
    secret = "private token=do-not-store"
    with pytest.raises(ValueError, match="valid error_code required") as exc:
        store.retry_outbox_delivery(
            "page.projector", "worker.two", reclaimed["lease_token"], secret,
            now=1032,
        )
    assert secret not in str(exc.value)
    assert _delivery_row(store) == before


def test_max_attempt_retry_dead_letters_and_replay_is_noop(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    result = store.retry_outbox_delivery(
        "page.projector", "worker.one", claimed["lease_token"], "transient",
        now=1001, backoff_seconds=1, max_attempts=1,
    )
    assert result["status"] == "dead_letter"
    assert result["last_error_code"] == "transient"
    version = result["row_version"]
    replay = store.retry_outbox_delivery(
        "page.projector", "worker.one", claimed["lease_token"], "different_code",
        now=1002, backoff_seconds=99, max_attempts=1,
    )
    assert replay == result
    assert replay["row_version"] == version
    assert store.claim_pending_outbox(
        "page.projector", "worker.two", now=9999, lease_seconds=10
    ) == []


def test_lowered_max_attempts_dead_letters_an_already_retrying_row(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    retried = store.retry_outbox_delivery(
        "page.projector", "worker.one", claimed["lease_token"], "transient",
        now=1001, backoff_seconds=1, max_attempts=8,
    )
    assert retried["status"] == "retry"
    assert store.claim_pending_outbox(
        "page.projector", "worker.two", now=1002, lease_seconds=10,
        max_attempts=1,
    ) == []
    terminal = _delivery_row(store)
    assert terminal["status"] == "dead_letter"
    assert terminal["last_error_code"] == "max_attempts"


def test_ack_and_dead_letter_token_replay_are_semantic_noops(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    delivered = store.ack_outbox_delivery(
        "page.projector", "worker.one", claimed["lease_token"], now=1001
    )
    assert delivered["status"] == "delivered"
    assert store.ack_outbox_delivery(
        "page.projector", "worker.one", claimed["lease_token"], now=2000
    ) == delivered

    other = store.claim_pending_outbox(
        "audit.consumer", "worker.one", now=1000, lease_seconds=10
    )[0]
    dead = store.dead_letter_outbox_delivery(
        "audit.consumer", "worker.one", other["lease_token"], "permanent",
        now=1001,
    )
    assert dead["status"] == "dead_letter"
    assert store.dead_letter_outbox_delivery(
        "audit.consumer", "worker.one", other["lease_token"], "other",
        now=2000,
    ) == dead


def test_consumers_have_independent_delivery_state(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    one = _claim(store)
    two = store.claim_pending_outbox(
        "second.consumer", "worker.two", now=1000, lease_seconds=10
    )[0]
    assert one["outbox_id"] == two["outbox_id"]
    assert one["lease_token"] != two["lease_token"]
    store.ack_outbox_delivery(
        "page.projector", "worker.one", one["lease_token"], now=1001
    )
    assert _delivery_row(store, "second.consumer")["status"] == "leased"


def test_expired_unreclaimed_lease_rejects_late_ack(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    before = _delivery_row(store)
    with pytest.raises(WorkspaceOutboxLeaseConflict, match="lease unavailable"):
        store.ack_outbox_delivery(
            "page.projector", "worker.one", claimed["lease_token"], now=1010
        )
    assert _delivery_row(store) == before


def test_claim_failure_rolls_back_pending_delivery_creation(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    original = store._write_connection

    def fault_connection():
        con = original()
        con.execute(
            """CREATE TEMP TRIGGER fail_delivery_claim
               BEFORE UPDATE ON workspace_outbox_deliveries
               BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
        )
        return con

    store._write_connection = fault_connection
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        _claim(store)
    store._write_connection = original
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox_deliveries"
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert _claim(store)["attempt_count"] == 1


def test_outbox_and_delivery_raw_sql_invariants_are_enforced(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    claimed = _claim(store)
    con = sqlite3.connect(store.db_path)
    try:
        outbox_id = claimed["outbox_id"]
        with pytest.raises(sqlite3.IntegrityError, match="outbox is immutable"):
            con.execute("UPDATE workspace_outbox SET delivered_at=1 WHERE id=?", (outbox_id,))
        with pytest.raises(sqlite3.IntegrityError, match="outbox is immutable"):
            con.execute("DELETE FROM workspace_outbox WHERE id=?", (outbox_id,))
        with pytest.raises(sqlite3.IntegrityError, match="invalid outbox delivery"):
            con.execute(
                "UPDATE workspace_outbox_deliveries SET attempt_count=99 WHERE outbox_id=?",
                (outbox_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="must begin pending"):
            con.execute(
                """INSERT INTO workspace_outbox_deliveries(
                     outbox_id,consumer,status,attempt_count,available_at,row_version,
                     created_at,updated_at,delivered_at)
                   VALUES(?, 'raw.consumer','delivered',1,1,1,1,1,1)""",
                (outbox_id,),
            )
    finally:
        con.rollback()
        con.close()
    assert store.verify_deep_health()["healthy"] is True


def test_legacy_marker_is_fail_closed_and_fresh_runtime_is_ready(tmp_path) -> None:
    store = _store_with_bound_event(tmp_path)
    assert store.capabilities() == {"workspace_v2": True, "schema_version": 2026081317}
    con = sqlite3.connect(store.db_path)
    try:
        con.execute(
            """UPDATE workspace_schema_migrations
               SET version=2026081304,
                   name='notion_workspace_v2_np2a_locator_projection'"""
        )
        con.commit()
    finally:
        con.close()
    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(WorkspaceSubstrateUnavailable, match="workspace substrate is unavailable"):
        store.claim_pending_outbox("page.projector", "worker.one", now=1000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"consumer": "UPPER", "worker": "worker.one"},
        {"consumer": "page.projector", "worker": "bad worker"},
        {"consumer": "page.projector", "worker": "worker.one", "limit": 33},
        {"consumer": "page.projector", "worker": "worker.one", "lease_seconds": 0},
        {"consumer": "page.projector", "worker": "worker.one", "now": 2**63 - 1},
    ],
)
def test_claim_grammar_and_bounds_fail_before_writes(tmp_path, kwargs) -> None:
    store = _store_with_bound_event(tmp_path)
    call = dict(kwargs)
    with pytest.raises(ValueError):
        store.claim_pending_outbox(now=call.pop("now", 1000), **call)
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM workspace_outbox_deliveries").fetchone()[0] == 0
    finally:
        con.close()
