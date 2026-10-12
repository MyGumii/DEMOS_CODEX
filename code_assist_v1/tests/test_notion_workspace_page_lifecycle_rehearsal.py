from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_legacy_change_consumer_migration as v3_migration
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameOperationConflict,
)
from code_assist_v1 import notion_workspace_page_lifecycle_rehearsal as rehearsal
from code_assist_v1.tests import test_notion_workspace_page_tree as tree_tests
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    LEASE_TOKEN,
    _materialized,
)


SOURCE_NOW = 2_000_000_100
_ORIGINAL_APPLY_V2 = tree_tests.apply_v2_plan


def _materialized_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], object]:
    """Install source v3 before catalog attestation captures file identity."""
    def apply_v2_then_v3(plan, **kwargs):
        result = _ORIGINAL_APPLY_V2(plan, **kwargs)
        root = Path(str(plan["user_data_root"]))
        source = Path(str(plan["source_db_path"]))
        v3 = v3_migration.build_plan(
            user_data_root=root,
            source_owner_id=str(plan["source_owner_id"]),
            expected_source_db_path=source,
        )
        v3_migration.apply_plan(
            v3,
            confirm_checksum=v3["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=root / "backups" / "before-v3.db",
        )
        return result

    monkeypatch.setattr(tree_tests, "apply_v2_plan", apply_v2_then_v3)
    return _materialized(tmp_path, monkeypatch)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prepared, request = _materialized_v3(tmp_path, monkeypatch)
    prestate = rehearsal.capture_isolated_page_lifecycle_rename_prestate(
        prepared["store"],
        request,
        rehearsal_root=prepared["root"],
        user_data_root=prepared["root"],
        expected_source_db_path=prepared["source"],
        expected_catalog_db_path=prepared["store"].db_path,
        source_quiesced_confirmed=True,
        source_trusted_clock=lambda: SOURCE_NOW,
    )
    return prepared, request, prestate


def _run_args(prepared, request, prestate, *, operation_id: str, title: str):
    return {
        "actor_subject_id": "reviewer",
        "mutation_actor_subject_id": "owner",
        "requested_title": title,
        "expected_source_prestate": prestate,
        "operation_id": operation_id,
        "worker": "worker.1",
        "lease_token": LEASE_TOKEN,
        "lease_seconds": 120,
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "expected_source_db_path": prepared["source"],
        "expected_catalog_db_path": prepared["store"].db_path,
        "source_quiesced_confirmed": True,
        "source_trusted_clock": lambda: SOURCE_NOW,
    }


def _source_state(prepared) -> dict[str, object]:
    con = sqlite3.connect(str(prepared["source"]))
    con.row_factory = sqlite3.Row
    try:
        page = con.execute(
            "SELECT title,revision,updated_at FROM pages WHERE id=?",
            (prepared["child"],),
        ).fetchone()
        watermark = con.execute(
            "SELECT change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?",
            (prepared["child"],),
        ).fetchone()
        state = con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        return {
            "title": str(page["title"]),
            "revision": int(page["revision"]),
            "updated_at": int(page["updated_at"]),
            "change_generation": int(watermark["change_generation"]),
            "change_mask": int(watermark["change_mask"]),
            "source_exists": int(watermark["source_exists"]),
            "source_epoch": int(state["source_epoch"]),
            "full_sweep_generation": int(state["full_sweep_generation"]),
            "page_version_count": int(
                con.execute(
                    "SELECT COUNT(*) FROM page_versions WHERE page_id=?",
                    (prepared["child"],),
                ).fetchone()[0]
            ),
            "source_operation_count": int(
                con.execute(
                    "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations"
                ).fetchone()[0]
            ),
        }
    finally:
        con.close()


def _catalog_state(prepared, request, operation_id: str) -> dict[str, object]:
    store = prepared["store"]
    con = store._read_connection()
    try:
        workspace = con.execute(
            "SELECT row_version,security_epoch FROM workspaces WHERE id=?",
            (request.workspace_id,),
        ).fetchone()
        binding = con.execute(
            "SELECT state,row_version,source_revision,source_updated_at,source_sha256 "
            "FROM workspace_page_sources WHERE workspace_id=? "
            "AND source_owner_subject_id=? AND source_page_id=?",
            (
                request.workspace_id,
                request.source_owner_subject_id,
                request.source_page_id,
            ),
        ).fetchone()
        tree = con.execute(
            "SELECT materialization_state,row_version,tree_version "
            "FROM workspace_page_tree_states WHERE workspace_id=?",
            (request.workspace_id,),
        ).fetchone()
        command = con.execute(
            "SELECT status,row_version,fencing_generation FROM "
            "workspace_page_lifecycle_commands WHERE actor_subject_id=? "
            "AND operation_id=?",
            ("reviewer", operation_id),
        ).fetchone()
        counts = tuple(
            int(con.execute(sql, (operation_id,)).fetchone()[0])
            for sql in (
                "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
                "SELECT COUNT(*) FROM workspace_audit_events WHERE operation_id=?",
                "SELECT COUNT(*) FROM workspace_outbox WHERE operation_id=?",
            )
        )
        return {
            "workspace_version": int(workspace["row_version"]),
            "security_epoch": int(workspace["security_epoch"]),
            "binding": tuple(binding),
            "tree": tuple(tree),
            "command": None if command is None else tuple(command),
            "ledger_counts": counts,
        }
    finally:
        con.close()


def test_changed_rename_converges_and_catalog_replay_bypasses_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, prestate = _fixture(tmp_path, monkeypatch)
    title = "RENAMED PRIVATE CHILD"
    operation_id = "offline-rename-changed"
    before_source = _source_state(prepared)
    before_catalog = _catalog_state(prepared, request, operation_id)
    args = _run_args(
        prepared, request, prestate, operation_id=operation_id, title=title
    )

    first = rehearsal.run_isolated_page_lifecycle_rename(
        prepared["store"], request, **args
    )
    assert first["contract"] == rehearsal.OFFLINE_PAGE_LIFECYCLE_RENAME_REPORT_CONTRACT
    assert first["status"] == "finalized"
    assert first["replayed"] is False
    assert first["changed"] is True
    assert first["full_sweep_required"] is True
    assert first["binding_version"] == request.expected_binding_version + 1
    encoded = json.dumps(first, sort_keys=True)
    for secret in (
        title,
        "owner",
        str(prepared["child"]),
        str(prepared["source"]),
        LEASE_TOKEN,
        "sha256:",
    ):
        assert secret not in encoded

    after_source = _source_state(prepared)
    assert after_source == {
        **before_source,
        "title": title,
        "revision": before_source["revision"] + 1,
        "updated_at": SOURCE_NOW,
        "change_generation": before_source["change_generation"] + 1,
        "change_mask": before_source["change_mask"] | 1,
        "source_epoch": before_source["source_epoch"] + 1,
        "full_sweep_generation": before_source["full_sweep_generation"] + 1,
        "page_version_count": before_source["page_version_count"] + 1,
        "source_operation_count": before_source["source_operation_count"] + 1,
    }
    after_catalog = _catalog_state(prepared, request, operation_id)
    assert after_catalog["workspace_version"] == before_catalog["workspace_version"] + 1
    assert after_catalog["security_epoch"] == before_catalog["security_epoch"] + 1
    assert after_catalog["binding"][0:2] == (
        "active",
        request.expected_binding_version + 1,
    )
    assert after_catalog["tree"][0] == "partial"
    assert after_catalog["command"][0] == "finalized"
    assert after_catalog["ledger_counts"] == (1, 1, 1)
    assert prepared["store"].deep_health()["healthy"] is True

    replay_args = dict(args)
    replay_args.update(
        rehearsal_root=tmp_path / "missing-root",
        user_data_root=tmp_path / "missing-data",
        expected_source_db_path=tmp_path / "missing-source.db",
        expected_catalog_db_path=tmp_path / "missing-catalog.db",
        source_quiesced_confirmed=False,
    )
    replayed = rehearsal.run_isolated_page_lifecycle_rename(
        prepared["store"], request, **replay_args
    )
    assert replayed == {**first, "replayed": True}
    assert _source_state(prepared) == after_source

    conflict_args = dict(replay_args)
    conflict_args["requested_title"] = "DIFFERENT PRIVATE TITLE"
    with pytest.raises(LegacyPageRenameOperationConflict):
        rehearsal.run_isolated_page_lifecycle_rename(
            prepared["store"], request, **conflict_args
        )


def test_exact_title_noop_writes_receipts_without_domain_version_bumps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, prestate = _fixture(tmp_path, monkeypatch)
    operation_id = "offline-rename-noop"
    before_source = _source_state(prepared)
    before_catalog = _catalog_state(prepared, request, operation_id)
    result = rehearsal.run_isolated_page_lifecycle_rename(
        prepared["store"],
        request,
        **_run_args(
            prepared,
            request,
            prestate,
            operation_id=operation_id,
            title=str(before_source["title"]),
        ),
    )
    assert result["status"] == "finalized"
    assert result["changed"] is False
    assert result["full_sweep_required"] is False
    assert result["binding_version"] == request.expected_binding_version
    after_source = _source_state(prepared)
    assert after_source == {
        **before_source,
        "source_operation_count": before_source["source_operation_count"] + 1,
    }
    after_catalog = _catalog_state(prepared, request, operation_id)
    assert after_catalog["workspace_version"] == before_catalog["workspace_version"]
    assert after_catalog["security_epoch"] == before_catalog["security_epoch"]
    assert after_catalog["binding"] == before_catalog["binding"]
    assert after_catalog["tree"] == before_catalog["tree"]
    assert after_catalog["ledger_counts"] == (1, 1, 1)
    assert prepared["store"].deep_health()["healthy"] is True


def test_source_commit_response_loss_replays_without_second_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, request, prestate = _fixture(tmp_path, monkeypatch)
    operation_id = "offline-rename-source-loss"
    before = _source_state(prepared)
    args = _run_args(
        prepared,
        request,
        prestate,
        operation_id=operation_id,
        title="RENAMED AFTER SOURCE LOSS",
    )
    lost = False

    def lose_source_response(phase: str) -> None:
        nonlocal lost
        if phase == "after_commit" and not lost:
            lost = True
            raise RuntimeError("simulated source response loss")

    with pytest.raises(RuntimeError, match="source response loss"):
        rehearsal.run_isolated_page_lifecycle_rename(
            prepared["store"], request, **args, fault_hook=lose_source_response
        )
    committed = _source_state(prepared)
    assert committed["revision"] == before["revision"] + 1
    assert committed["page_version_count"] == before["page_version_count"] + 1
    assert committed["source_operation_count"] == before["source_operation_count"] + 1
    partial = _catalog_state(prepared, request, operation_id)
    assert partial["command"][0] == "prepared"
    assert partial["ledger_counts"] == (0, 0, 0)

    result = rehearsal.run_isolated_page_lifecycle_rename(
        prepared["store"], request, **args
    )
    assert result["status"] == "finalized"
    assert result["changed"] is True
    assert _source_state(prepared) == committed
    assert _catalog_state(prepared, request, operation_id)["ledger_counts"] == (
        1,
        1,
        1,
    )


def test_catalog_response_loss_and_rollback_both_converge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Response loss after the catalog commit replays before any path access.
    response_root = tmp_path / "response"
    response_root.mkdir()
    prepared, request, prestate = _fixture(response_root, monkeypatch)
    response_op = "offline-rename-catalog-loss"
    response_args = _run_args(
        prepared,
        request,
        prestate,
        operation_id=response_op,
        title="RENAMED AFTER CATALOG LOSS",
    )
    with pytest.raises(RuntimeError, match="catalog response loss"):
        rehearsal.run_isolated_page_lifecycle_rename(
            prepared["store"],
            request,
            **response_args,
            fault_hook=lambda phase: (
                (_ for _ in ()).throw(RuntimeError("simulated catalog response loss"))
                if phase == "after_catalog_finalize"
                else None
            ),
        )
    committed_source = _source_state(prepared)
    assert _catalog_state(prepared, request, response_op)["command"][0] == "finalized"
    replay_args = dict(response_args)
    replay_args.update(
        rehearsal_root=tmp_path / "unavailable",
        user_data_root=tmp_path / "unavailable",
        expected_source_db_path=tmp_path / "unavailable.db",
        expected_catalog_db_path=tmp_path / "unavailable-catalog.db",
        source_quiesced_confirmed=False,
    )
    replayed = rehearsal.run_isolated_page_lifecycle_rename(
        prepared["store"], request, **replay_args
    )
    assert replayed["replayed"] is True
    assert _source_state(prepared) == committed_source

    # A catalog outbox fault rolls every catalog domain effect back while the
    # source receipt remains durable, then the same operation converges.
    rollback_root = tmp_path / "rollback"
    rollback_root.mkdir()
    prepared2, request2, prestate2 = _fixture(rollback_root, monkeypatch)
    rollback_op = "offline-rename-catalog-rollback"
    rollback_args = _run_args(
        prepared2,
        request2,
        prestate2,
        operation_id=rollback_op,
        title="RENAMED AFTER CATALOG ROLLBACK",
    )
    before_catalog = _catalog_state(prepared2, request2, rollback_op)
    real_write = prepared2["store"]._write_connection

    def faulting_write():
        con = real_write()
        con.execute(
            """
            CREATE TEMP TRIGGER test_offline_rename_outbox_fault
            BEFORE INSERT ON workspace_outbox
            WHEN NEW.event_type='workspace.page_lifecycle_finalized'
            BEGIN SELECT RAISE(ABORT,'injected rename finalize fault'); END
            """
        )
        return con

    monkeypatch.setattr(prepared2["store"], "_write_connection", faulting_write)
    with pytest.raises(sqlite3.IntegrityError, match="injected rename finalize fault"):
        rehearsal.run_isolated_page_lifecycle_rename(
            prepared2["store"], request2, **rollback_args
        )
    after_fault = _catalog_state(prepared2, request2, rollback_op)
    assert after_fault["workspace_version"] == before_catalog["workspace_version"]
    assert after_fault["security_epoch"] == before_catalog["security_epoch"]
    assert after_fault["binding"] == before_catalog["binding"]
    assert after_fault["tree"] == before_catalog["tree"]
    assert after_fault["command"][0] == "source_applied"
    assert after_fault["ledger_counts"] == (0, 0, 0)
    source_after_fault = _source_state(prepared2)
    monkeypatch.setattr(prepared2["store"], "_write_connection", real_write)
    result = rehearsal.run_isolated_page_lifecycle_rename(
        prepared2["store"], request2, **rollback_args
    )
    assert result["status"] == "finalized"
    assert _source_state(prepared2) == source_after_fault


def test_quiescence_is_first_and_import_is_runtime_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path_calls = 0

    def path_access_must_not_run(**_kwargs):
        nonlocal path_calls
        path_calls += 1
        raise AssertionError("path access ran")

    monkeypatch.setattr(rehearsal, "resolve_inventory_paths", path_access_must_not_run)
    with pytest.raises(
        rehearsal.WorkspacePageLifecycleRehearsalRefused,
        match="quiescence",
    ):
        rehearsal._paths(
            object(),
            rehearsal_root=tmp_path,
            user_data_root=tmp_path,
            source_owner_id="owner",
            expected_source_db_path=tmp_path / "notion.db",
            expected_catalog_db_path=tmp_path / "catalog.db",
            source_quiesced_confirmed=False,
        )
    assert path_calls == 0

    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = tuple(probe.iterdir())
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.routes_notion_workspace",
        "code_assist_v1.routes_second_brain",
        "code_assist_v1.second_brain_store",
        "code_assist_v1.knowledge_core",
    )
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_page_lifecycle_rehearsal; "
        f"names={forbidden!r}; "
        "print(json.dumps(sorted(name for name in names if name in sys.modules)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
    assert tuple(probe.iterdir()) == before
