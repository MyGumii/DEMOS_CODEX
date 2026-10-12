from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_change_consumer_orchestration as orchestration
from code_assist_v1 import notion_legacy_change_migration as v1
from code_assist_v1 import notion_legacy_incarnation_migration as v2
from code_assist_v1 import notion_legacy_change_consumer_migration as v3
from code_assist_v1.notion_legacy_change_consumer import (
    LegacyChangeConsumerConflict,
    LegacyChangeConsumerStore,
)
from code_assist_v1.notion_legacy_change_consumer_orchestration import (
    converge_one_legacy_change_consumer_batch,
    record_legacy_change_consumer_full_sweep_completion,
    record_legacy_change_consumer_full_sweep_set_completion,
)
from code_assist_v1.notion_legacy_changes import (
    LegacySourceFence,
    sqlite_source_identity,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)
from code_assist_v1.notion_workspace_healing import WorkspaceHealingEffectReceipt


HEALING_TOKEN = "hlease_" + "e" * 32
SOURCE_TOKEN = "llease_" + "e" * 32
PASS_ID = "lpass_" + "a" * 32


def _apply_source_chain(root: Path, source: Path, backups: Path) -> None:
    for name, module in (("v1", v1), ("v2", v2), ("v3", v3)):
        plan = module.build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
        )
        module.apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backups / f"before-{name}.db",
        )


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_unbound: bool = True,
):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    catalog = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = catalog.create_workspace(
        "owner", name="Consumer convergence", operation_id="create-consumer-workspace"
    )["workspace"]
    bound_page = notion_store.create_page("owner", title="before")['page']
    unbound_page = (
        notion_store.create_page("owner", title="unbound")["page"]
        if include_unbound
        else None
    )
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", bound_page["id"]
    )
    catalog.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=workspace["version"],
        operation_id="bind-consumer-page",
    )
    source = tmp_path / "owner" / "notion" / "notion.db"
    backups = tmp_path / "consumer-backups"
    backups.mkdir()
    _apply_source_chain(tmp_path, source, backups)
    notion_store.update_page("owner", bound_page["id"], {"title": "after"})
    if unbound_page is not None:
        notion_store.update_page(
            "owner", unbound_page["id"], {"title": "still unbound"}
        )

    source_con = sqlite3.connect(source)
    source_con.row_factory = sqlite3.Row
    source_con.execute("PRAGMA foreign_keys=ON")
    state = source_con.execute(
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
    source_store = LegacyChangeConsumerStore(
        source_con,
        source_owner_id="owner",
        source_fence=fence,
        trusted_clock=lambda: clock["now"],
    )
    source_store.start_pass(
        consumer="locator.consumer",
        pass_id=PASS_ID,
        worker="worker.materialize",
        lease_token=SOURCE_TOKEN,
        lease_seconds=60,
        operation_id="start-consumer-pass",
    )
    source_store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=PASS_ID,
        worker="worker.materialize",
        lease_token=SOURCE_TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-consumer-pass",
    )
    run = catalog.start_healing_run(
        "owner",
        workspace["id"],
        worker="worker.consumer",
        lease_token=HEALING_TOKEN,
        lease_seconds=120,
        operation_id="start-consumer-healing",
        healing_mode="locator_apply",
    )["run"]
    return (
        catalog,
        workspace,
        bound_page,
        unbound_page,
        source,
        source_store,
        source_con,
        run,
    )


def _kwargs(catalog, workspace, source, source_store, run):
    return {
        "source_store": source_store,
        "catalog_store": catalog,
        "consumer": "locator.consumer",
        "pass_id": PASS_ID,
        "actor_subject_id": "owner",
        "workspace_id": workspace["id"],
        "run_id": run["id"],
        "worker": "worker.consumer",
        "lease_token": HEALING_TOKEN,
        "fencing_generation": run["lease"]["fencing_generation"],
        "expected_version": run["version"],
        "user_data_root": str(source.parents[2]),
        "expected_catalog_db_path": str(catalog.db_path),
        "source_db_for_owner": lambda owner: source if owner == "owner" else None,
        "source_quiesced_confirmed": True,
        "limit": 32,
    }


def test_offline_convergence_commits_catalog_then_source_and_leaves_unbound_pending(
    tmp_path, monkeypatch
):
    catalog, workspace, bound, unbound, source, source_store, con, run = _fixture(
        tmp_path, monkeypatch
    )
    result = converge_one_legacy_change_consumer_batch(
        **_kwargs(catalog, workspace, source, source_store, run)
    )
    assert result["run"]["status"] == "completed"
    assert result["healing_report"]["stale"] == 1
    assert result["source_work_report"] == {
        "matched": 1,
        "applied": 1,
        "superseded": 0,
        "remaining_pending": 1,
        "full_sweep_required": True,
    }
    work_rows = con.execute(
        "SELECT page_id,status,effect_disposition FROM "
        "notion_legacy_change_consumer_work ORDER BY page_id"
    ).fetchall()
    by_page = {str(row["page_id"]): row for row in work_rows}
    assert str(by_page[bound["id"]]["status"]) == "applied"
    assert str(by_page[bound["id"]]["effect_disposition"]) == "stale"
    assert str(by_page[unbound["id"]]["status"]) == "pending"
    assert con.execute(
        "SELECT COUNT(*) FROM notion_page_change_watermarks"
    ).fetchone()[0] == 2
    con.close()


def test_catalog_effect_crash_replays_before_page_source_io_and_finishes_source_terminal(
    tmp_path, monkeypatch
):
    catalog, workspace, _bound, _unbound, source, source_store, con, run = _fixture(
        tmp_path, monkeypatch
    )
    original_record = source_store.record_work_effect
    calls = {"n": 0}

    def lose_first_source_terminal(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated source terminal crash")
        return original_record(**kwargs)

    monkeypatch.setattr(source_store, "record_work_effect", lose_first_source_terminal)
    with pytest.raises(RuntimeError, match="source terminal crash"):
        converge_one_legacy_change_consumer_batch(
            **_kwargs(catalog, workspace, source, source_store, run)
        )
    assert source_store.pending_work_count(
        consumer="locator.consumer", pass_id=PASS_ID
    ) == 2
    monkeypatch.setattr(source_store, "record_work_effect", original_record)
    kwargs = _kwargs(catalog, workspace, source, source_store, run)
    kwargs["source_db_for_owner"] = lambda _owner: (_ for _ in ()).throw(
        AssertionError("catalog effect replay must precede page source I/O")
    )
    replay = converge_one_legacy_change_consumer_batch(**kwargs)
    assert replay["run"]["status"] == "completed"
    assert replay["source_work_report"]["applied"] == 1
    assert replay["source_work_report"]["remaining_pending"] == 1
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
        "WHERE action='legacy.change_consumer.work_effect_recorded'"
    ).fetchone()[0] == 1
    con.close()


def test_superseded_catalog_generation_keeps_source_work_pending_for_new_binding(
    tmp_path, monkeypatch
):
    catalog, workspace, bound, _unbound, source, source_store, con, run = _fixture(
        tmp_path, monkeypatch, include_unbound=False
    )
    binding = catalog.get_page_source_binding_for_maintenance(
        "owner",
        workspace["id"],
        source_owner_subject_id="owner",
        source_page_id=bound["id"],
    )
    assert binding is not None

    def superseded_heal(*_args, effect_observer=None, **_kwargs):
        assert effect_observer is not None
        effect_observer(
            WorkspaceHealingEffectReceipt(
                workspace_id=workspace["id"],
                source_owner_subject_id="owner",
                source_page_id=bound["id"],
                expected_binding_generation=binding.binding_version,
                effect_operation_id="catalog-superseded-generation",
                disposition="superseded",
                replayed=False,
                source_receipt=None,
            )
        )
        return {
            "run": catalog.get_healing_run("owner", workspace["id"], run["id"]),
            "healing_report": {"superseded": 1},
        }

    monkeypatch.setattr(orchestration, "heal_one_locator_batch", superseded_heal)
    result = converge_one_legacy_change_consumer_batch(
        **_kwargs(catalog, workspace, source, source_store, run)
    )
    assert result["source_work_report"] == {
        "matched": 1,
        "applied": 0,
        "superseded": 1,
        "remaining_pending": 1,
        "full_sweep_required": True,
    }
    assert source_store.pending_work_batch(
        consumer="locator.consumer", pass_id=PASS_ID, limit=1
    ).items[0].page_id == bound["id"]
    assert source_store.lag_report(
        consumer="locator.consumer", pass_id=PASS_ID
    )["lagging"] is True
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
        "WHERE action='legacy.change_consumer.work_effect_recorded'"
    ).fetchone()[0] == 0
    con.close()


def test_full_sweep_completion_is_durable_replays_before_catalog_and_unlocks_next_pass(
    tmp_path, monkeypatch
):
    catalog, workspace, _bound, _unbound, source, source_store, con, run = _fixture(
        tmp_path, monkeypatch, include_unbound=False
    )
    converged = converge_one_legacy_change_consumer_batch(
        **_kwargs(catalog, workspace, source, source_store, run)
    )
    assert converged["run"]["status"] == "completed"
    assert converged["source_work_report"]["remaining_pending"] == 0
    with pytest.raises(ValueError, match="requires source quiescence"):
        record_legacy_change_consumer_full_sweep_completion(
            source_store,
            catalog,
            consumer="locator.consumer",
            pass_id=PASS_ID,
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            run_id=run["id"],
            source_owner_subject_id="owner",
            source_quiesced_confirmed=False,
        )
    with pytest.raises((FileNotFoundError, PermissionError)):
        record_legacy_change_consumer_full_sweep_completion(
            source_store,
            catalog,
            consumer="locator.consumer",
            pass_id=PASS_ID,
            actor_subject_id="outsider",
            workspace_id=workspace["id"],
            run_id=run["id"],
            source_owner_subject_id="owner",
            source_quiesced_confirmed=True,
        )
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
        "WHERE action='legacy.change_consumer.full_sweep_completed'"
    ).fetchone()[0] == 0
    completed = record_legacy_change_consumer_full_sweep_completion(
        source_store,
        catalog,
        consumer="locator.consumer",
        pass_id=PASS_ID,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        source_owner_subject_id="owner",
        source_quiesced_confirmed=True,
    )
    assert completed["completed"] is True
    assert completed["replayed"] is False
    assert completed["pending_work_count"] == 0
    assert completed["catalog_counters"]["stale"] == 1
    assert source_store.pass_status(
        consumer="locator.consumer", pass_id=PASS_ID
    )["full_sweep_completed"] is True

    monkeypatch.setattr(
        catalog,
        "get_completed_healing_source_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completion replay must precede catalog I/O")
        ),
    )
    replay = record_legacy_change_consumer_full_sweep_completion(
        source_store,
        catalog,
        consumer="locator.consumer",
        pass_id=PASS_ID,
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        run_id=run["id"],
        source_owner_subject_id="owner",
        source_quiesced_confirmed=False,
    )
    assert replay == {**completed, "replayed": True}

    next_pass = source_store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "b" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "b" * 32,
        lease_seconds=60,
        operation_id="start-after-completed-sweep",
    )
    assert next_pass["pass"]["full_sweep_required"] is False
    assert con.execute(
        "SELECT COUNT(*) FROM notion_page_change_watermarks"
    ).fetchone()[0] == 1
    con.close()


def test_multi_workspace_no_ack_rehearsal_covers_every_terminal_workspace_and_restarts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    catalog = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace_a = catalog.create_workspace(
        "owner", name="Consumer A", operation_id="create-consumer-a"
    )["workspace"]
    workspace_b = catalog.create_workspace(
        "owner", name="Consumer B", operation_id="create-consumer-b"
    )["workspace"]
    page_a = notion_store.create_page("owner", title="before-a")["page"]
    page_b = notion_store.create_page("owner", title="before-b")["page"]
    for workspace, page, suffix in (
        (workspace_a, page_a, "a"),
        (workspace_b, page_b, "b"),
    ):
        proof = notion_store.capture_legacy_page_source_proof(
            "owner", "owner", page["id"]
        )
        catalog.bind_page_source(
            "owner",
            workspace["id"],
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=workspace["version"],
            operation_id=f"bind-consumer-{suffix}",
        )
    source = tmp_path / "owner" / "notion" / "notion.db"
    backups = tmp_path / "multi-consumer-backups"
    backups.mkdir()
    _apply_source_chain(tmp_path, source, backups)
    notion_store.update_page("owner", page_a["id"], {"title": "after-a"})
    notion_store.update_page("owner", page_b["id"], {"title": "after-b"})

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
    source_store = LegacyChangeConsumerStore(
        con,
        source_owner_id="owner",
        source_fence=fence,
        trusted_clock=lambda: 100,
    )
    source_store.start_pass(
        consumer="locator.consumer",
        pass_id=PASS_ID,
        worker="worker.materialize",
        lease_token=SOURCE_TOKEN,
        lease_seconds=60,
        operation_id="start-multi-pass",
    )
    source_store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id=PASS_ID,
        worker="worker.materialize",
        lease_token=SOURCE_TOKEN,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-multi-pass",
    )
    runs = []
    for workspace, suffix in ((workspace_a, "a"), (workspace_b, "b")):
        run = catalog.start_healing_run(
            "owner",
            workspace["id"],
            worker=f"worker.consumer.{suffix}",
            lease_token=HEALING_TOKEN,
            lease_seconds=120,
            operation_id=f"start-multi-healing-{suffix}",
            healing_mode="locator_apply",
        )["run"]
        kwargs = _kwargs(catalog, workspace, source, source_store, run)
        kwargs["worker"] = f"worker.consumer.{suffix}"
        convergence = converge_one_legacy_change_consumer_batch(**kwargs)
        runs.append((workspace, run))
        assert convergence["run"]["status"] == "completed"
        if suffix == "a":
            first_lag = source_store.lag_report(
                consumer="locator.consumer", pass_id=PASS_ID
            )
            assert first_lag["work"]["pending"] == 1
            assert first_lag["lagging"] is True
    assert source_store.pending_work_count(
        consumer="locator.consumer", pass_id=PASS_ID
    ) == 0
    before_completion = source_store.lag_report(
        consumer="locator.consumer", pass_id=PASS_ID
    )
    assert before_completion["work"]["pending"] == 0
    assert before_completion["full_sweep_completed"] is False
    assert before_completion["lagging"] is True
    with pytest.raises(
        LegacyChangeConsumerConflict,
        match="not ready for completion",
    ):
        source_store.record_full_sweep_completion(
            consumer="locator.consumer",
            pass_id=PASS_ID,
            catalog_completion_loader=lambda: catalog.get_completed_healing_source_evidence(
                "owner",
                workspace_b["id"],
                runs[1][1]["id"],
                source_owner_subject_id="owner",
            ),
        )

    catalog = WorkspaceStore(catalog.db_path)
    completed = record_legacy_change_consumer_full_sweep_set_completion(
        source_store,
        catalog,
        consumer="locator.consumer",
        pass_id=PASS_ID,
        workspace_runs=tuple(
            {
                "actor_subject_id": "owner",
                "workspace_id": workspace["id"],
                "run_id": run["id"],
            }
            for workspace, run in reversed(runs)
        ),
        source_owner_subject_id="owner",
        source_quiesced_confirmed=True,
    )
    assert completed["workspace_run_count"] == 2
    assert completed["catalog_counters"]["stale"] == 2
    after_completion = source_store.lag_report(
        consumer="locator.consumer", pass_id=PASS_ID
    )
    assert after_completion["lagging"] is False
    encoded_lag = json.dumps(after_completion, sort_keys=True)
    assert page_a["id"] not in encoded_lag
    assert page_b["id"] not in encoded_lag
    assert con.execute(
        "SELECT COUNT(*) FROM notion_page_change_watermarks"
    ).fetchone()[0] == 2
    con.close()

    reopened = sqlite3.connect(source)
    reopened.row_factory = sqlite3.Row
    reopened.execute("PRAGMA foreign_keys=ON")
    restarted_store = LegacyChangeConsumerStore(
        reopened,
        source_owner_id="owner",
        source_fence=fence,
        trusted_clock=lambda: 200,
    )
    monkeypatch.setattr(
        catalog,
        "get_completed_healing_source_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("set replay must precede catalog I/O")
        ),
    )
    replay = record_legacy_change_consumer_full_sweep_set_completion(
        restarted_store,
        catalog,
        consumer="locator.consumer",
        pass_id=PASS_ID,
        workspace_runs=tuple(
            {
                "actor_subject_id": "owner",
                "workspace_id": workspace["id"],
                "run_id": run["id"],
            }
            for workspace, run in runs
        ),
        source_owner_subject_id="owner",
        source_quiesced_confirmed=False,
    )
    assert replay == {**completed, "replayed": True}
    assert restarted_store.lag_report(
        consumer="locator.consumer", pass_id=PASS_ID
    ) == after_completion
    next_pass = restarted_store.start_pass(
        consumer="locator.consumer",
        pass_id="lpass_" + "c" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "c" * 32,
        lease_seconds=60,
        operation_id="start-after-multi-sweep",
    )
    assert next_pass["pass"]["full_sweep_required"] is False
    repeated = restarted_store.materialize_next_batch(
        consumer="locator.consumer",
        pass_id="lpass_" + "c" * 32,
        worker="worker.materialize",
        lease_token="llease_" + "c" * 32,
        fencing_generation=1,
        expected_version=1,
        limit=32,
        operation_id="materialize-no-ack-repeat",
    )
    assert repeated["pass"]["status"] == "materialized"
    assert repeated["delta"] == {
        "scanned": 2,
        "materialized": 0,
        "duplicate": 2,
    }
    assert restarted_store.lag_report(
        consumer="locator.consumer", pass_id="lpass_" + "c" * 32
    ) == {
        "pass_status": "materialized",
        "full_sweep_required": False,
        "full_sweep_completed": False,
        "work": {
            "pending": 0,
            "applied": 0,
            "ignored": 0,
            "superseded": 0,
        },
        "lagging": False,
    }
    assert reopened.execute(
        "SELECT COUNT(*) FROM notion_page_change_watermarks"
    ).fetchone()[0] == 2
    reopened.close()
