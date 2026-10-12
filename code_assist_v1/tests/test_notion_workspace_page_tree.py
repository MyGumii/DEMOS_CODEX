from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_page_tree as page_tree
from code_assist_v1.notion_legacy_change_migration import (
    apply_plan as apply_v1_plan,
    build_plan as build_v1_plan,
)
from code_assist_v1.notion_legacy_incarnation_migration import (
    apply_plan as apply_v2_plan,
    build_plan as build_v2_plan,
)
from code_assist_v1.notion_workspace_incarnation_orchestration import (
    run_isolated_page_incarnation_attestation,
)
from code_assist_v1.notion_workspace_inventory import _page_proof
from code_assist_v1.notion_workspace_rehearsal import (
    REHEARSAL_SENTINEL_CONTENT,
    REHEARSAL_SENTINEL_NAME,
)
from code_assist_v1.notion_workspace_store import (
    WorkspacePageTreeSourceFenceConflict,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bind_child_only: bool = False,
    post_v2_migration: Callable[[Path, Path, Path], None] | None = None,
) -> dict[str, object]:
    root = tmp_path / "user_data"
    root.mkdir()
    (root / REHEARSAL_SENTINEL_NAME).write_bytes(REHEARSAL_SENTINEL_CONTENT)
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))

    root_a = notion_store.create_page("owner", title="PRIVATE ROOT A")["page"]
    child = notion_store.create_page(
        "owner", title="PRIVATE CHILD", parent_id=root_a["id"]
    )["page"]
    root_b = notion_store.create_page("owner", title="PRIVATE ROOT B")["page"]
    source = root / "owner" / "notion" / "notion.db"

    v1 = build_v1_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_v1_plan(
        v1,
        confirm_checksum=v1["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )
    v2 = build_v2_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_v2_plan(
        v2,
        confirm_checksum=v2["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )
    if post_v2_migration is not None:
        post_v2_migration(root, source, backups)

    store = WorkspaceStore(
        catalog_path_for_user_data_root(root), trusted_clock=lambda: 2_000_000_000
    )
    workspace = store.create_workspace(
        "reviewer", name="Offline page tree", operation_id="create-workspace"
    )["workspace"]
    member = store.set_member(
        "reviewer",
        workspace["id"],
        "owner",
        role="member",
        base_version=workspace["version"],
        operation_id="add-owner",
    )
    workspace_version = member["workspace"]["version"]
    selected = [child] if bind_child_only else [root_a, child, root_b]
    bindings: dict[str, int] = {}
    for index, item in enumerate(selected):
        con = notion_store._connect("owner")
        try:
            row = con.execute(
                "SELECT * FROM pages WHERE id=?", (item["id"],)
            ).fetchone()
            proof = _page_proof(
                con, actor="owner", owner="owner", page=row, role="owner"
            )
        finally:
            con.close()
        bound = store.bind_page_source(
            "owner",
            workspace["id"],
            proof,
            source_revalidator=lambda candidate, proof=proof: candidate == proof,
            base_version=workspace_version,
            operation_id=f"bind-{index}",
        )
        bindings[item["id"]] = bound["binding"]["version"]
        workspace_version = bound["workspace"]["version"]

    for index, item in enumerate(selected):
        run_isolated_page_incarnation_attestation(
            store,
            reviewer_subject_id="reviewer",
            workspace_id=workspace["id"],
            rehearsal_root=root,
            user_data_root=root,
            source_owner_id="owner",
            source_page_id=item["id"],
            expected_source_db_path=source,
            expected_catalog_db_path=store.db_path,
            expected_binding_version=bindings[item["id"]],
            operation_id=f"attest-{index}",
            source_quiesced_confirmed=True,
        )
    context = store.page_tree_materialization_context(
        "reviewer", workspace["id"]
    )
    return {
        "root": root,
        "source": source,
        "store": store,
        "workspace_id": workspace["id"],
        "context": context,
        "root_a": root_a["id"],
        "root_b": root_b["id"],
        "child": child["id"],
    }


def _run_args(prepared: dict[str, object]) -> dict[str, object]:
    context = prepared["context"]
    return {
        "actor_subject_id": "reviewer",
        "workspace_id": prepared["workspace_id"],
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "expected_catalog_db_path": prepared["store"].db_path,
        "expected_source_db_paths": {"owner": prepared["source"]},
        "expected_workspace_version": context["workspace_version"],
        "expected_tree_version": context["tree_version"],
        "expected_tree_state_version": context["tree_state_version"],
        "operation_id": "materialize-page-tree",
        "source_quiesced_confirmed": True,
    }


def test_offline_materialization_is_dense_atomic_ready_and_replayable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch)
    store = prepared["store"]
    source_opens = 0
    integrity_modes: list[bool] = []
    real_open = page_tree.open_exact_legacy_change_source
    real_inspect = page_tree.inspect_legacy_incarnation_source_contract

    def counted_open(*args, **kwargs):
        nonlocal source_opens
        source_opens += 1
        return real_open(*args, **kwargs)

    def counted_inspect(con, *, verify_integrity=True):
        integrity_modes.append(bool(verify_integrity))
        return real_inspect(con, verify_integrity=verify_integrity)

    monkeypatch.setattr(page_tree, "open_exact_legacy_change_source", counted_open)
    monkeypatch.setattr(
        page_tree, "inspect_legacy_incarnation_source_contract", counted_inspect
    )
    report = page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    assert source_opens == 2
    assert integrity_modes == [True, False]
    assert report == {
        "contract": page_tree.OFFLINE_PAGE_TREE_MATERIALIZATION_REPORT_CONTRACT,
        "replayed": False,
        "materialization_state": "complete",
        "tree_version": 1,
        "tree_state_version": 2,
        "active_node_count": 3,
        "archived_node_count": 0,
        "deleted_node_count": 0,
        "owner_count": 1,
        "ready_for_resolver_cutover": False,
        "runtime_commands_enabled": False,
        "source_watermarks_acknowledged": False,
    }
    readiness = store.page_tree_readiness("reviewer", prepared["workspace_id"])
    assert readiness["ready_for_tree_reads"] is True
    assert readiness["ready_for_resolver_cutover"] is False

    con = store._read_connection()
    try:
        rows = con.execute(
            """
            SELECT source_page_id,parent_resolution,parent_source_page_id,
                   sibling_ordinal,depth,lifecycle_state
            FROM workspace_page_tree_nodes
            WHERE workspace_id=? ORDER BY depth,sibling_ordinal,source_page_id
            """,
            (prepared["workspace_id"],),
        ).fetchall()
        assert [(row["source_page_id"], row["depth"]) for row in rows] == [
            (prepared["root_a"], 0),
            (prepared["root_b"], 0),
            (prepared["child"], 1),
        ]
        child = next(row for row in rows if row["source_page_id"] == prepared["child"])
        assert child["parent_resolution"] == "resolved"
        assert child["parent_source_page_id"] == prepared["root_a"]
        assert child["sibling_ordinal"] == 1
        event = con.execute(
            """
            SELECT payload_json FROM workspace_outbox
            WHERE operation_id='materialize-page-tree'
            """
        ).fetchone()
        encoded = str(event["payload_json"])
        assert prepared["root_a"] not in encoded
        assert prepared["root_b"] not in encoded
        assert prepared["child"] not in encoded
        assert "PRIVATE" not in encoded
    finally:
        con.close()
    assert store.deep_health()["healthy"] is True

    monkeypatch.setattr(
        page_tree,
        "capture_exact_page_tree_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source capture ran during replay")
        ),
    )
    replay = page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    assert replay == {**report, "replayed": True}
    assert source_opens == 2


def test_hidden_parent_is_refused_before_any_catalog_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch, bind_child_only=True)
    store = prepared["store"]
    with pytest.raises(
        page_tree.WorkspacePageTreeMaterializationRefused,
        match="hidden or unbound",
    ):
        page_tree.run_offline_page_tree_materialization(
            store, **_run_args(prepared)
        )
    con = store._read_connection()
    try:
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (prepared["workspace_id"],),
        ).fetchone()
        assert state["materialization_state"] == "unmaterialized"
        assert state["tree_version"] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE action=?",
            ("workspace.page_tree_materialized",),
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_quiescence_and_replay_order_precede_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch)
    store = prepared["store"]
    args = _run_args(prepared)
    args["source_quiesced_confirmed"] = False
    args["expected_source_db_paths"] = {
        "owner": tmp_path / "must-not-be-accessed.db"
    }
    monkeypatch.setattr(
        page_tree,
        "_rehearsal_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("sentinel access ran before quiescence")
        ),
    )
    with pytest.raises(
        page_tree.WorkspacePageTreeMaterializationRefused,
        match="quiescence",
    ):
        page_tree.run_offline_page_tree_materialization(store, **args)


def test_failed_source_revalidation_keeps_tree_and_ledgers_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch)
    store = prepared["store"]
    args = _run_args(prepared)
    capture_kwargs = dict(args)
    capture_kwargs.pop("operation_id")
    snapshot = page_tree.capture_exact_page_tree_snapshot(
        store, **capture_kwargs
    )
    with pytest.raises(WorkspacePageTreeSourceFenceConflict):
        store.materialize_page_tree_snapshot(
            "reviewer",
            prepared["workspace_id"],
            snapshot,
            source_revalidator=lambda _snapshot: False,
            base_version=prepared["context"]["workspace_version"],
            operation_id="materialize-failed-revalidation",
            source_quiesced_confirmed=True,
        )
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_operations WHERE operation_id=?",
            ("materialize-failed-revalidation",),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_audit_events WHERE operation_id=?",
            ("materialize-failed-revalidation",),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_outbox WHERE operation_id=?",
            ("materialize-failed-revalidation",),
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_outbox_fault_rolls_back_state_nodes_and_all_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(tmp_path, monkeypatch)
    store = prepared["store"]
    real_write_connection = store._write_connection

    def faulting_write_connection():
        con = real_write_connection()
        con.execute(
            """
            CREATE TEMP TRIGGER test_page_tree_outbox_fault
            BEFORE INSERT ON workspace_outbox
            WHEN NEW.event_type='workspace.page_tree_materialized'
            BEGIN
              SELECT RAISE(ABORT, 'injected page tree outbox fault');
            END
            """
        )
        return con

    monkeypatch.setattr(store, "_write_connection", faulting_write_connection)
    with pytest.raises(Exception, match="injected page tree outbox fault"):
        page_tree.run_offline_page_tree_materialization(
            store, **_run_args(prepared)
        )
    con = store._read_connection()
    try:
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (prepared["workspace_id"],),
        ).fetchone()
        assert (state["materialization_state"], state["tree_version"], state["row_version"]) == (
            "unmaterialized",
            0,
            1,
        )
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes"
        ).fetchone()[0] == 0
        for table in (
            "workspace_operations",
            "workspace_audit_events",
            "workspace_outbox",
        ):
            assert con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE operation_id=?",
                ("materialize-page-tree",),
            ).fetchone()[0] == 0
    finally:
        con.close()


def test_empty_workspace_materializes_without_source_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "user_data"
    root.mkdir()
    (root / REHEARSAL_SENTINEL_NAME).write_bytes(REHEARSAL_SENTINEL_CONTENT)
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    store = WorkspaceStore(catalog_path_for_user_data_root(root))
    workspace = store.create_workspace(
        "reviewer", name="Empty page tree", operation_id="create-empty-workspace"
    )["workspace"]
    context = store.page_tree_materialization_context(
        "reviewer", workspace["id"]
    )
    monkeypatch.setattr(
        page_tree,
        "open_exact_legacy_change_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty tree opened a source")
        ),
    )
    report = page_tree.run_offline_page_tree_materialization(
        store,
        actor_subject_id="reviewer",
        workspace_id=workspace["id"],
        rehearsal_root=root,
        user_data_root=root,
        expected_catalog_db_path=store.db_path,
        expected_source_db_paths={},
        expected_workspace_version=context["workspace_version"],
        expected_tree_version=0,
        expected_tree_state_version=1,
        operation_id="materialize-empty-page-tree",
        source_quiesced_confirmed=True,
    )
    assert report["active_node_count"] == 0
    assert report["owner_count"] == 0
    assert store.page_tree_readiness(
        "reviewer", workspace["id"]
    )["ready_for_tree_reads"] is True


def test_page_tree_import_is_side_effect_free_and_not_runtime_registered(
    tmp_path: Path,
) -> None:
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
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.routes_notion_workspace",
        "code_assist_v1.routes_knowledge",
        "code_assist_v1.routes_second_brain",
        "code_assist_v1.second_brain_store",
        "code_assist_v1.knowledge_core",
    )
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_page_tree; "
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
