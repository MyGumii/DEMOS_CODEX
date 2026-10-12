from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_change_migration as v1_migration
from code_assist_v1 import notion_legacy_incarnation_migration as v2_migration
from code_assist_v1.notion_legacy_change_consumer import (
    _MATERIALIZE_SOURCE_ROWS_SQL,
    LegacyChangeConsumerConflict,
    LegacyChangeConsumerLeaseConflict,
    LegacyChangeConsumerSourceFenceConflict,
    LegacyChangeConsumerStore,
)
from code_assist_v1.notion_legacy_change_consumer_contract import (
    CONSUMER_NOW_UDF,
    CONSUMER_WRITE_CONTEXT_UDF,
    EXPECTED_OBJECT_HASH,
    LegacyChangeConsumerContractError,
    MIGRATION_CHECKSUM,
    MIGRATION_NAME,
    MIGRATION_VERSION,
    inspect_legacy_change_consumer_contract,
    install_schema_for_migration,
)
from code_assist_v1.notion_legacy_changes import (
    LegacySourceFence,
    sqlite_source_identity,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    LegacyIncarnationContractError,
    inspect_legacy_incarnation_contract,
)


TOKEN = "llease_" + "a" * 32


def _catalog_completion(fence: LegacySourceFence) -> dict[str, object]:
    return {
        "workspace_id": "ws_" + "f" * 32,
        "healing_run_id": "wheal_" + "e" * 32,
        "source_owner_subject_id": "owner",
        "healing_mode": "locator_apply",
        "status": "completed",
        "has_more": False,
        "version": 2,
        "completed_at": 100,
        "counters": {
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
            "oversize": 0,
            "source_missing": 0,
        },
        "source_fence": {
            "source_epoch": fence.source_epoch,
            "full_sweep_generation": fence.full_sweep_generation,
            "source_device": fence.device,
            "source_inode": fence.inode,
            "source_link_count": fence.link_count,
        },
    }


def _apply_v1(root: Path, source: Path, backups: Path) -> None:
    plan = v1_migration.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v1_migration.apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )


def _apply_v2(root: Path, source: Path, backups: Path) -> None:
    plan = v2_migration.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v2_migration.apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    live = notion_store.create_page("owner", title="SECRET LIVE")["page"]
    archived = notion_store.create_page("owner", title="SECRET ARCHIVED")["page"]
    deleted = notion_store.create_page("owner", title="SECRET DELETED")["page"]
    source = root / "owner" / "notion" / "notion.db"
    _apply_v1(root, source, backups)
    notion_store.archive_page("owner", archived["id"])
    notion_store.archive_page("owner", deleted["id"])
    notion_store.delete_page_permanent("owner", deleted["id"])
    _apply_v2(root, source, backups)
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    install_schema_for_migration(con)
    con.execute(
        """
        INSERT INTO notion_legacy_schema_migrations(
          version,name,checksum,actual_schema_sha256,applied_at
        ) VALUES(?,?,?,?,?)
        """,
        (MIGRATION_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM, EXPECTED_OBJECT_HASH, 10),
    )
    con.commit()
    state = con.execute(
        "SELECT source_epoch,full_sweep_generation "
        "FROM notion_legacy_change_state WHERE singleton=1"
    ).fetchone()
    stat = os.stat(source)
    fence = LegacySourceFence(
        source_epoch=int(state["source_epoch"]),
        full_sweep_generation=int(state["full_sweep_generation"]),
        device=sqlite_source_identity(int(stat.st_dev)),
        inode=sqlite_source_identity(int(stat.st_ino)),
        link_count=int(stat.st_nlink),
    )
    clock = {"now": 100}
    store = LegacyChangeConsumerStore(
        con,
        source_owner_id="owner",
        source_fence=fence,
        trusted_clock=lambda: clock["now"],
    )
    return store, con, clock, source, (live, archived, deleted)


def _open_consumer_store(
    source: Path, *, now: dict[str, int]
) -> tuple[LegacyChangeConsumerStore, sqlite3.Connection, LegacySourceFence]:
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    state = con.execute(
        "SELECT source_epoch,full_sweep_generation "
        "FROM notion_legacy_change_state WHERE singleton=1"
    ).fetchone()
    stat = os.stat(source)
    fence = LegacySourceFence(
        source_epoch=int(state["source_epoch"]),
        full_sweep_generation=int(state["full_sweep_generation"]),
        device=sqlite_source_identity(int(stat.st_dev)),
        inode=sqlite_source_identity(int(stat.st_ino)),
        link_count=int(stat.st_nlink),
    )
    return (
        LegacyChangeConsumerStore(
            con,
            source_owner_id="owner",
            source_fence=fence,
            trusted_clock=lambda: now["now"],
        ),
        con,
        fence,
    )


def test_v3_contract_is_frozen_and_v2_default_rejects_extra_triggers(
    tmp_path, monkeypatch
):
    store, con, _clock, _source, _pages = _fixture(tmp_path, monkeypatch)
    del store
    assert inspect_legacy_change_consumer_contract(con)["object_hash"] == (
        EXPECTED_OBJECT_HASH
    )
    with pytest.raises(
        LegacyIncarnationContractError,
        match="legacy incarnation trigger set is invalid",
    ):
        inspect_legacy_incarnation_contract(con)
    con.execute(
        "INSERT INTO notion_legacy_schema_migrations(" 
        "version,name,checksum,actual_schema_sha256,applied_at) "
        "VALUES(2099010101,'future','sha256:'||?, 'sha256:'||?,10)",
        ("0" * 64, "0" * 64),
    )
    con.commit()
    with pytest.raises(
        LegacyChangeConsumerContractError,
        match="migration lineage is not exact",
    ):
        inspect_legacy_change_consumer_contract(con)
    con.close()


def test_bounded_materialization_is_durable_secret_free_and_non_destructive(
    tmp_path, monkeypatch
):
    store, con, _clock, _source, pages = _fixture(tmp_path, monkeypatch)
    before_watermarks = con.execute(
        "SELECT page_id,change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id"
    ).fetchall()
    started = store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "1" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-pass-1",
    )
    assert started["pass"]["full_sweep_required"] is True
    assert started["pass"]["version"] == 1
    results = []
    version = 1
    operation = 1
    while True:
        result = store.materialize_next_batch(
            consumer="locator.consumer",
            pass_id="lpass_" + "1" * 32,
            worker="worker.materialize",
            lease_token=TOKEN,
            fencing_generation=1,
            expected_version=version,
            limit=1,
            operation_id=f"materialize-{operation}",
        )
        results.append(result)
        version = result["pass"]["version"]
        operation += 1
        if not result["pass"]["has_more"]:
            break
    assert sum(item["delta"]["scanned"] for item in results) == 3
    assert sum(item["delta"]["materialized"] for item in results) == 3
    assert results[-1]["pass"]["status"] == "materialized"
    assert store.pending_work_count(
        consumer="locator.consumer", pass_id="lpass_" + "1" * 32
    ) == 3
    status = store.pass_status(
        consumer="locator.consumer", pass_id="lpass_" + "1" * 32
    )
    assert status["work"] == {
        "pending": 3,
        "applied": 0,
        "ignored": 0,
        "superseded": 0,
    }
    status_encoded = json.dumps(status, sort_keys=True)
    assert all(page["id"] not in status_encoded for page in pages)
    assert "SECRET" not in status_encoded
    assert con.execute(
        "SELECT page_id,change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id"
    ).fetchall() == before_watermarks
    work = con.execute(
        """
        SELECT page_id,source_exists,source_active,incarnation_retired,
               receipt_sha256,status
        FROM notion_legacy_change_consumer_work ORDER BY page_id
        """
    ).fetchall()
    by_page = {str(row["page_id"]): row for row in work}
    assert bool(by_page[pages[0]["id"]]["source_exists"]) is True
    assert bool(by_page[pages[0]["id"]]["source_active"]) is True
    assert bool(by_page[pages[1]["id"]]["source_exists"]) is True
    assert bool(by_page[pages[1]["id"]]["source_active"]) is False
    assert bool(by_page[pages[2]["id"]]["source_exists"]) is False
    assert bool(by_page[pages[2]["id"]]["incarnation_retired"]) is True
    assert all(str(row["receipt_sha256"]).startswith("sha256:") for row in work)
    encoded = json.dumps(results, sort_keys=True)
    assert all(page["id"] not in encoded for page in pages)
    assert "SECRET" not in encoded
    plan = "\n".join(
        str(row[3])
        for row in con.execute(
            "EXPLAIN QUERY PLAN " + _MATERIALIZE_SOURCE_ROWS_SQL,
            ("", 33),
        ).fetchall()
    )
    assert "USE TEMP B-TREE" not in plan
    assert "SCAN notion_page_incarnation_history" not in plan
    assert "SEARCH h2 USING PRIMARY KEY" in plan
    con.close()


def test_exact_operation_replay_precedes_expired_lease_and_changed_request_conflicts(
    tmp_path, monkeypatch
):
    store, con, clock, _source, pages = _fixture(tmp_path, monkeypatch)
    store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "2" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=2,
        operation_id="start-pass-2",
    )
    first = store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id="lpass_" + "2" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=1,
        operation_id="materialize-response-loss",
    )
    clock["now"] = 500
    assert store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id="lpass_" + "2" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=1,
        operation_id="materialize-response-loss",
    ) == first
    with pytest.raises(LegacyChangeConsumerConflict):
        store.materialize_next_batch(
            consumer="locator.consumer",
            pass_id="lpass_" + "2" * 32,
            worker="worker.materialize",
            lease_token=TOKEN,
            fencing_generation=1,
            expected_version=1,
            limit=2,
            operation_id="materialize-response-loss",
        )
    con.close()


def test_expired_pass_reclaim_preserves_cursor_and_fences_the_old_lease(
    tmp_path, monkeypatch
):
    store, con, clock, _source, _pages = _fixture(tmp_path, monkeypatch)
    pass_id = "lpass_" + "4" * 32
    store.start_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=2,
        operation_id="start-pass-4",
    )
    first = store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=1,
        operation_id="materialize-before-reclaim",
    )
    assert first["pass"]["version"] == 2
    previous_cursor = con.execute(
        "SELECT cursor_page_id FROM notion_legacy_change_consumer_passes "
        "WHERE consumer=? AND pass_id=?",
        ("locator.consumer", pass_id),
    ).fetchone()[0]
    clock["now"] = 103
    new_token = "llease_" + "b" * 32
    reclaimed = store.reclaim_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.reclaimed",
        lease_token=new_token,
        lease_seconds=60,
        expected_fencing_generation=1,
        expected_version=2,
        operation_id="reclaim-pass-4",
    )
    assert reclaimed["pass"]["fencing_generation"] == 2
    assert reclaimed["pass"]["version"] == 3
    assert reclaimed["delta"] == {"scanned": 0, "materialized": 0, "duplicate": 0}
    assert con.execute(
        "SELECT cursor_page_id FROM notion_legacy_change_consumer_passes "
        "WHERE consumer=? AND pass_id=?",
        ("locator.consumer", pass_id),
    ).fetchone()[0] == previous_cursor
    with pytest.raises(LegacyChangeConsumerLeaseConflict):
        store.materialize_next_batch(
            consumer="locator.consumer",
            pass_id=pass_id,
            worker="worker.materialize",
            lease_token=TOKEN,
            fencing_generation=1,
            expected_version=2,
            limit=1,
            operation_id="stale-worker-after-reclaim",
        )
    continued = store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.reclaimed",
        lease_token=new_token,
        fencing_generation=2,
        expected_version=3,
        limit=1,
        operation_id="continued-after-reclaim",
    )
    assert continued["pass"]["version"] == 4
    clock["now"] = 500
    assert store.reclaim_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.reclaimed",
        lease_token=new_token,
        lease_seconds=60,
        expected_fencing_generation=1,
        expected_version=2,
        operation_id="reclaim-pass-4",
    ) == reclaimed
    con.close()


def test_full_sweep_obligation_is_stable_until_source_generation_changes(
    tmp_path, monkeypatch
):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    notion_store.bootstrap_workspace("owner")
    source = root / "owner" / "notion" / "notion.db"
    _apply_v1(root, source, backups)
    _apply_v2(root, source, backups)
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    install_schema_for_migration(con)
    con.execute(
        """
        INSERT INTO notion_legacy_schema_migrations(
          version,name,checksum,actual_schema_sha256,applied_at
        ) VALUES(?,?,?,?,?)
        """,
        (MIGRATION_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM, EXPECTED_OBJECT_HASH, 10),
    )
    con.commit()
    con.close()

    clock = {"now": 100}
    store, con, first_fence = _open_consumer_store(source, now=clock)
    first = store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "5" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-empty-pass-1",
    )
    assert first["pass"]["full_sweep_required"] is True
    first_done = store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id="lpass_" + "5" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=1,
        operation_id="finish-empty-pass-1",
    )
    assert first_done["pass"]["status"] == "materialized"
    with pytest.raises(
        LegacyChangeConsumerConflict, match="required full sweep is not completed"
    ):
        store.start_pass(
            consumer="locator.consumer",
            pass_id="lpass_" + "6" * 32,
            worker="worker.materialize",
            lease_token="llease_" + "c" * 32,
            lease_seconds=60,
            operation_id="start-empty-pass-2",
        )
    loaded = {"count": 0}

    def load_completion():
        loaded["count"] += 1
        return _catalog_completion(first_fence)

    completion = store.record_full_sweep_completion(
        consumer="locator.consumer",
        pass_id="lpass_" + "5" * 32,
        catalog_completion_loader=load_completion,
    )
    assert loaded["count"] == 1
    assert completion["pending_work_count"] == 0
    assert store.pass_status(
        consumer="locator.consumer", pass_id="lpass_" + "5" * 32
    )["full_sweep_completed"] is True
    assert store.record_full_sweep_completion(
        consumer="locator.consumer",
        pass_id="lpass_" + "5" * 32,
        catalog_completion_loader=lambda: (_ for _ in ()).throw(
            AssertionError("completion replay must precede catalog I/O")
        ),
    ) == completion
    second = store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "6" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "c" * 32,
        lease_seconds=60,
        operation_id="start-empty-pass-2",
    )
    assert second["pass"]["full_sweep_required"] is False
    store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id="lpass_" + "6" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "c" * 32,
        fencing_generation=1,
        expected_version=1,
        limit=1,
        operation_id="finish-empty-pass-2",
    )
    con.close()

    notion_store.create_page("owner", title="new generation")
    clock["now"] = 200
    changed_store, changed_con, changed_fence = _open_consumer_store(source, now=clock)
    assert changed_fence.full_sweep_generation > first_fence.full_sweep_generation
    changed = changed_store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "7" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "d" * 32,
        lease_seconds=60,
        operation_id="start-changed-generation",
    )
    assert changed["pass"]["full_sweep_required"] is True
    changed_con.close()


def test_source_drift_and_unconfigured_raw_writes_fail_closed(
    tmp_path, monkeypatch
):
    store, con, _clock, source, _pages = _fixture(tmp_path, monkeypatch)
    store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "3" * 32,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-pass-3",
    )
    con.execute("UPDATE pages SET title=title||' changed' WHERE archived=0")
    con.commit()
    before = con.execute(
        "SELECT row_version,scanned_count FROM notion_legacy_change_consumer_passes "
        "WHERE pass_id=?",
        ("lpass_" + "3" * 32,),
    ).fetchone()
    with pytest.raises(LegacyChangeConsumerSourceFenceConflict):
        store.materialize_next_batch(
            consumer="locator.consumer",
            pass_id="lpass_" + "3" * 32,
            worker="worker.materialize",
            lease_token=TOKEN,
            fencing_generation=1,
            expected_version=1,
            limit=1,
            operation_id="materialize-after-drift",
        )
    after = con.execute(
        "SELECT row_version,scanned_count FROM notion_legacy_change_consumer_passes "
        "WHERE pass_id=?",
        ("lpass_" + "3" * 32,),
    ).fetchone()
    assert tuple(after) == tuple(before)
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_work"
    ).fetchone()[0] == 0
    con.close()

    raw = sqlite3.connect(source)
    try:
        with pytest.raises(sqlite3.OperationalError, match=CONSUMER_WRITE_CONTEXT_UDF):
            raw.execute(
                "UPDATE notion_legacy_change_consumer_passes SET row_version=2"
            )
    finally:
        raw.close()


def test_pending_work_terminal_receipts_are_bounded_exact_and_non_destructive(
    tmp_path, monkeypatch
):
    store, con, clock, _source, pages = _fixture(tmp_path, monkeypatch)
    pass_id = "lpass_" + "8" * 32
    store.start_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-pass-8",
    )
    materialized = store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-pass-8",
    )
    assert materialized["pass"]["status"] == "materialized"
    catalog_loads = {"count": 0}

    def must_not_load_catalog():
        catalog_loads["count"] += 1
        return _catalog_completion(store._fence)

    with pytest.raises(
        LegacyChangeConsumerConflict,
        match="required full sweep is not ready for completion",
    ):
        store.record_full_sweep_completion(
            consumer="locator.consumer",
            pass_id=pass_id,
            catalog_completion_loader=must_not_load_catalog,
        )
    assert catalog_loads["count"] == 0
    before_watermarks = con.execute(
        "SELECT page_id,change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id"
    ).fetchall()
    first_batch = store.pending_work_batch(
        consumer="locator.consumer", pass_id=pass_id, limit=2
    )
    assert first_batch.scanned_count == 2
    assert first_batch.has_more is True
    first, second = first_batch.items
    workspace_id = "ws_" + "a" * 32
    first_result = store.record_work_effect(
        work=first,
        terminal_status="applied",
        effect_workspace_id=workspace_id,
        effect_binding_generation=7,
        effect_operation_id="catalog-effect-1",
        effect_disposition="stale",
        operation_id="source-terminal-1",
    )
    assert first_result["delta"] == {
        "applied": 1,
        "ignored": 0,
        "superseded": 0,
    }
    clock["now"] = 500
    assert store.record_work_effect(
        work=first,
        terminal_status="applied",
        effect_workspace_id=workspace_id,
        effect_binding_generation=7,
        effect_operation_id="catalog-effect-1",
        effect_disposition="stale",
        operation_id="source-terminal-1",
    ) == first_result
    with pytest.raises(LegacyChangeConsumerConflict):
        store.record_work_effect(
            work=first,
            terminal_status="applied",
            effect_workspace_id=workspace_id,
            effect_binding_generation=8,
            effect_operation_id="catalog-effect-1",
            effect_disposition="stale",
            operation_id="source-terminal-1",
        )
    ignored = store.record_work_effect(
        work=second,
        terminal_status="ignored",
        effect_workspace_id=None,
        effect_binding_generation=None,
        effect_operation_id="catalog-no-binding-2",
        effect_disposition="no_binding",
        operation_id="source-terminal-2",
    )
    assert ignored["delta"]["ignored"] == 1
    remaining = store.pending_work_batch(
        consumer="locator.consumer", pass_id=pass_id, limit=32
    )
    assert remaining.scanned_count == 1
    assert remaining.has_more is False
    third = remaining.items[0]
    superseded = store.record_work_effect(
        work=third,
        terminal_status="superseded",
        effect_workspace_id=workspace_id,
        effect_binding_generation=9,
        effect_operation_id="catalog-superseded-3",
        effect_disposition="superseded",
        operation_id="source-terminal-3",
    )
    assert superseded["delta"]["superseded"] == 1
    assert store.pending_work_batch(
        consumer="locator.consumer", pass_id=pass_id, limit=32
    ).scanned_count == 0
    assert con.execute(
        "SELECT page_id,change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id"
    ).fetchall() == before_watermarks
    status = store.pass_status(consumer="locator.consumer", pass_id=pass_id)
    assert status["work"] == {
        "pending": 0,
        "applied": 1,
        "ignored": 1,
        "superseded": 1,
    }
    encoded_status = json.dumps(status, sort_keys=True)
    assert all(page["id"] not in encoded_status for page in pages)
    assert "SECRET" not in encoded_status
    assert inspect_legacy_change_consumer_contract(con)["installed"] is True
    con.close()


def test_pending_work_requires_materialized_pass_and_exact_terminal_shape(
    tmp_path, monkeypatch
):
    store, con, _clock, _source, _pages = _fixture(tmp_path, monkeypatch)
    pass_id = "lpass_" + "9" * 32
    store.start_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-pass-9",
    )
    with pytest.raises(
        LegacyChangeConsumerConflict, match="pass is not materialized"
    ):
        store.pending_work_batch(
            consumer="locator.consumer", pass_id=pass_id, limit=1
        )
    store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-pass-9",
    )
    work = store.pending_work_batch(
        consumer="locator.consumer", pass_id=pass_id, limit=1
    ).items[0]
    with pytest.raises(ValueError, match="ignored work must record no_binding"):
        store.record_work_effect(
            work=work,
            terminal_status="ignored",
            effect_workspace_id="ws_" + "a" * 32,
            effect_binding_generation=1,
            effect_operation_id="bad-ignore",
            effect_disposition="no_binding",
            operation_id="bad-ignore-source",
        )
    with pytest.raises(ValueError, match="superseded work disposition required"):
        store.record_work_effect(
            work=work,
            terminal_status="superseded",
            effect_workspace_id="ws_" + "a" * 32,
            effect_binding_generation=1,
            effect_operation_id="bad-superseded",
            effect_disposition="stale",
            operation_id="bad-superseded-source",
        )
    con.close()


def test_no_ack_contract_rejects_missing_or_regressed_watermark_state(
    tmp_path, monkeypatch
):
    store, con, _clock, _source, _pages = _fixture(tmp_path, monkeypatch)
    pass_id = "lpass_" + "d" * 32
    store.start_pass(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        lease_seconds=60,
        operation_id="start-contract-tamper-pass",
    )
    store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=pass_id,
        worker="worker.materialize",
        lease_token=TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-contract-tamper-pass",
    )
    watermark = con.execute(
        "SELECT page_id,change_generation,change_mask,changed_at,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id LIMIT 1"
    ).fetchone()
    con.execute(
        "DELETE FROM notion_page_change_watermarks WHERE page_id=?",
        (str(watermark["page_id"]),),
    )
    con.commit()
    with pytest.raises(
        LegacyChangeConsumerContractError, match="schema is invalid"
    ):
        inspect_legacy_change_consumer_contract(con)
    con.execute(
        "INSERT INTO notion_page_change_watermarks(" 
        "page_id,change_generation,change_mask,changed_at,source_exists) "
        "VALUES(?,?,?,?,?)",
        tuple(watermark),
    )
    con.commit()
    assert inspect_legacy_change_consumer_contract(con)["installed"] is True
    con.execute(
        "UPDATE notion_page_change_watermarks SET change_mask=1 WHERE page_id=?",
        (str(watermark["page_id"]),),
    )
    con.commit()
    with pytest.raises(
        LegacyChangeConsumerContractError, match="state is invalid"
    ):
        inspect_legacy_change_consumer_contract(con)
    con.close()
