from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_page_tree as page_tree
from code_assist_v1 import (
    notion_workspace_page_lifecycle_subtree_orchestration as orchestration,
)
from code_assist_v1.notion_workspace_store import (
    WorkspacePageLifecycleCommandRequest,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_subtree_finalize import (
    LEASE_TOKEN,
    _binding,
    _upgrade_source_v3,
)
from code_assist_v1.tests.test_notion_workspace_page_tree import (
    _prepare,
    _run_args,
)


def _archive_request(prepared: dict[str, object]):
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    root = str(prepared["root_a"])
    binding = _binding(store, workspace_id, root)
    con = store._read_connection()
    try:
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        node = con.execute(
            "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
            "AND source_owner_subject_id='owner' AND source_page_id=?",
            (workspace_id, root),
        ).fetchone()
    finally:
        con.close()
    workspace = store.get_workspace("reviewer", workspace_id)
    return WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="archive",
        source_owner_subject_id="owner",
        source_page_id=root,
        expected_tree_version=int(state["tree_version"]),
        expected_binding_version=int(binding["row_version"]),
        expected_incarnation_generation=int(node["source_incarnation_generation"]),
        base_version=int(workspace["version"]),
    )


def test_offline_subtree_orchestration_is_exact_and_replays_before_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    store = prepared["store"]
    page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    request = _archive_request(prepared)
    source_con = sqlite3.connect(prepared["source"])
    try:
        source_now = int(
            source_con.execute("SELECT MAX(updated_at) FROM pages").fetchone()[0]
        ) + 10
    finally:
        source_con.close()
    report = orchestration.run_isolated_page_lifecycle_subtree(
        store,
        "reviewer",
        request,
        operation_id="offline-trash-subtree-1",
        source_caller_operation_id="offline-source-trash-subtree-1",
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        rehearsal_root=prepared["root"],
        user_data_root=prepared["root"],
        expected_catalog_db_path=store.db_path,
        expected_source_db_path=prepared["source"],
        source_quiesced_confirmed=True,
        source_trusted_clock=lambda: source_now,
    )
    assert report == {
        "contract": "np2c_1317_offline_lifecycle_subtree_v1",
        "offline_only": True,
        "quiescence_confirmed": True,
        "replayed": False,
        "canonical_action": "trash",
        "effect_count": 3,
        "transitioned_count": 2,
        "mapping_count": 0,
        "file_mapping_count": 0,
        "active_node_count": 1,
        "archived_node_count": 2,
        "runtime_commands_enabled": False,
        "resolver_cutover_enabled": False,
        "source_watermarks_acknowledged": False,
    }
    encoded_report = json.dumps(report, sort_keys=True)
    for forbidden in (
        str(prepared["root_a"]),
        str(prepared["child"]),
        str(prepared["root"]),
        str(prepared["source"]),
        str(store.db_path),
        LEASE_TOKEN,
        "sha256:",
    ):
        assert forbidden not in encoded_report

    replay = orchestration.run_isolated_page_lifecycle_subtree(
        store,
        "reviewer",
        request,
        operation_id="offline-trash-subtree-1",
        source_caller_operation_id="offline-source-trash-subtree-1",
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        rehearsal_root=tmp_path / "missing-sentinel",
        user_data_root=tmp_path / "missing-root",
        expected_catalog_db_path=tmp_path / "missing-catalog.db",
        expected_source_db_path=tmp_path / "missing-source.db",
        source_quiesced_confirmed=False,
        source_trusted_clock=lambda: (_ for _ in ()).throw(
            AssertionError("source clock")
        ),
    )
    assert replay == {**report, "replayed": True}
    assert store.verify_deep_health()["healthy"] is True


def test_offline_subtree_quiescence_precedes_sentinel_and_source_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    page_tree.run_offline_page_tree_materialization(
        prepared["store"], **_run_args(prepared)
    )
    request = _archive_request(prepared)
    sentinel_calls = 0

    def forbidden_sentinel(_path):
        nonlocal sentinel_calls
        sentinel_calls += 1
        raise AssertionError("sentinel accessed")

    monkeypatch.setattr(orchestration, "_rehearsal_root", forbidden_sentinel)
    with pytest.raises(
        orchestration.WorkspacePageLifecycleSubtreeOrchestrationRefused,
        match="requires source quiescence",
    ):
        orchestration.run_isolated_page_lifecycle_subtree(
            prepared["store"],
            "reviewer",
            request,
            operation_id="offline-trash-quiescence-refusal",
            source_caller_operation_id="offline-source-quiescence-refusal",
            worker="worker.1",
            lease_token=LEASE_TOKEN,
            lease_seconds=120,
            rehearsal_root=prepared["root"],
            user_data_root=prepared["root"],
            expected_catalog_db_path=prepared["store"].db_path,
            expected_source_db_path=prepared["source"],
            source_quiesced_confirmed=False,
        )
    assert sentinel_calls == 0


def test_offline_subtree_resumes_after_catalog_fault_without_reapplying_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    store = prepared["store"]
    page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    request = _archive_request(prepared)
    source_con = sqlite3.connect(prepared["source"])
    try:
        source_now = int(
            source_con.execute("SELECT MAX(updated_at) FROM pages").fetchone()[0]
        ) + 10
    finally:
        source_con.close()

    def stop_after_evidence(phase: str) -> None:
        if phase == "after_evidence":
            raise RuntimeError("catalog fault after source commit")

    arguments = {
        "operation_id": "offline-trash-source-applied-retry-1",
        "source_caller_operation_id": "offline-source-trash-retry-1",
        "worker": "worker.1",
        "lease_token": LEASE_TOKEN,
        "lease_seconds": 120,
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "expected_catalog_db_path": store.db_path,
        "expected_source_db_path": prepared["source"],
        "source_quiesced_confirmed": True,
        "source_trusted_clock": lambda: source_now,
    }
    with pytest.raises(RuntimeError, match="catalog fault after source commit"):
        orchestration.run_isolated_page_lifecycle_subtree(
            store,
            "reviewer",
            request,
            fault_hook=stop_after_evidence,
            **arguments,
        )

    con = store._read_connection()
    try:
        command = con.execute(
            "SELECT status,source_receipt_sha256 FROM "
            "workspace_page_lifecycle_commands WHERE actor_subject_id=? "
            "AND operation_id=?",
            ("reviewer", arguments["operation_id"]),
        ).fetchone()
        assert tuple(command) == ("source_applied", command["source_receipt_sha256"])
        assert str(command["source_receipt_sha256"]).startswith("sha256:")
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_subtree_manifests"
        ).fetchone()[0] == 0
    finally:
        con.close()

    monkeypatch.setattr(
        orchestration.LegacyPageDuplicateTrashRestoreSourceAdapter,
        "capture_precondition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("committed source replay must precede precondition capture")
        ),
    )
    report = orchestration.run_isolated_page_lifecycle_subtree(
        store,
        "reviewer",
        request,
        **arguments,
    )
    assert report["replayed"] is False
    assert report["canonical_action"] == "trash"
    assert report["effect_count"] == 3
    assert report["transitioned_count"] == 2
    assert store.verify_deep_health()["healthy"] is True


def test_offline_subtree_resumes_when_source_receipt_acknowledgement_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare(
        tmp_path, monkeypatch, post_v2_migration=_upgrade_source_v3
    )
    store = prepared["store"]
    page_tree.run_offline_page_tree_materialization(
        store, **_run_args(prepared)
    )
    request = _archive_request(prepared)
    source_con = sqlite3.connect(prepared["source"])
    try:
        source_now = int(
            source_con.execute("SELECT MAX(updated_at) FROM pages").fetchone()[0]
        ) + 10
    finally:
        source_con.close()
    arguments = {
        "operation_id": "offline-trash-source-ack-loss-1",
        "source_caller_operation_id": "offline-source-trash-ack-loss-1",
        "worker": "worker.1",
        "lease_token": LEASE_TOKEN,
        "lease_seconds": 120,
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "expected_catalog_db_path": store.db_path,
        "expected_source_db_path": prepared["source"],
        "source_quiesced_confirmed": True,
        "source_trusted_clock": lambda: source_now,
    }
    original_record = store.record_page_lifecycle_source_applied
    record_calls = 0

    def lose_first_ack(*args, **kwargs):
        nonlocal record_calls
        record_calls += 1
        if record_calls == 1:
            raise RuntimeError("source acknowledgement lost")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(
        store, "record_page_lifecycle_source_applied", lose_first_ack
    )
    with pytest.raises(RuntimeError, match="source acknowledgement lost"):
        orchestration.run_isolated_page_lifecycle_subtree(
            store,
            "reviewer",
            request,
            **arguments,
        )

    con = store._read_connection()
    try:
        command = con.execute(
            "SELECT status,source_receipt_sha256 FROM "
            "workspace_page_lifecycle_commands WHERE actor_subject_id=? "
            "AND operation_id=?",
            ("reviewer", arguments["operation_id"]),
        ).fetchone()
        assert tuple(command) == ("prepared", None)
    finally:
        con.close()
    source_con = sqlite3.connect(prepared["source"])
    try:
        assert source_con.execute(
            "SELECT archived FROM pages WHERE id=?", (prepared["root_a"],)
        ).fetchone()[0] == 1
    finally:
        source_con.close()

    monkeypatch.setattr(
        orchestration.LegacyPageDuplicateTrashRestoreSourceAdapter,
        "capture_precondition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source ledger replay must precede precondition capture")
        ),
    )
    report = orchestration.run_isolated_page_lifecycle_subtree(
        store,
        "reviewer",
        request,
        **arguments,
    )
    assert report["replayed"] is False
    assert report["canonical_action"] == "trash"
    assert report["transitioned_count"] == 2
    assert record_calls == 2
    assert store.verify_deep_health()["healthy"] is True


def test_offline_subtree_module_import_is_side_effect_free(tmp_path: Path) -> None:
    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    script = r'''import json,sys
before=set(sys.modules)
import code_assist_v1.notion_workspace_page_lifecycle_subtree_orchestration
blocked=("app_code","app_notion","auth","config","data_paths","knowledge_core",
         "routes_notion_bridge","routes_chat_stream","second_brain")
print(json.dumps(sorted(name for name in set(sys.modules)-before
                        if any(part in name.lower() for part in blocked))))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout.strip() == "[]"
    assert tuple(
        sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    ) == before
