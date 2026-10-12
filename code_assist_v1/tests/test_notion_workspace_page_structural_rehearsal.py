from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from code_assist_v1 import notion_store
from code_assist_v1.notion_legacy_change_consumer_contract import (
    EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM,
    MIGRATION_NAME,
    MIGRATION_VERSION,
    install_schema_for_migration,
)
from code_assist_v1.notion_workspace_page_structural_rehearsal import (
    capture_isolated_page_lifecycle_structural_precondition,
    run_isolated_page_lifecycle_structural,
)
from code_assist_v1.notion_workspace_store import (
    WorkspacePageLifecycleCommandRequest,
)
from code_assist_v1.tests.test_notion_workspace_page_lifecycle_kernel import (
    LEASE_TOKEN,
    _materialized,
)


def _install_v3(source: Path) -> None:
    con = notion_store._configure_connection(
        sqlite3.connect(source), query_only=False
    )
    try:
        install_schema_for_migration(con)
        con.execute(
            "INSERT INTO notion_legacy_schema_migrations("
            "version,name,checksum,actual_schema_sha256,applied_at) "
            "VALUES(?,?,?,?,?)",
            (
                MIGRATION_VERSION,
                MIGRATION_NAME,
                MIGRATION_CHECKSUM,
                EXPECTED_OBJECT_HASH,
                10,
            ),
        )
        con.commit()
    finally:
        con.close()


def test_structural_rehearsal_import_is_isolated_and_side_effect_free(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "import-probe"
    probe.mkdir()
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.notion_store",
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.routes_notion_workspace",
        "code_assist_v1.routes_knowledge",
        "code_assist_v1.routes_second_brain",
        "code_assist_v1.knowledge_core",
        "code_assist_v1.second_brain_store",
    )
    script = textwrap.dedent(
        f"""
        import json, pathlib, sys
        root = pathlib.Path({str(probe)!r})
        before = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())
        import code_assist_v1.notion_legacy_page_structural  # noqa: F401
        import code_assist_v1.notion_workspace_page_structural_rehearsal  # noqa: F401
        after = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file())
        names = {forbidden!r}
        print(json.dumps({{'forbidden': sorted(n for n in names if n in sys.modules), 'changed': before != after}}))
        """
    )
    environment = os.environ.copy()
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout.strip()) == {
        "forbidden": [],
        "changed": False,
    }


def _existing_generation(store, workspace_id: str, page_id: str) -> tuple[int, int]:
    con = store._read_connection()
    try:
        row = con.execute(
            "SELECT ps.row_version,a.source_incarnation_generation "
            "FROM workspace_page_sources ps "
            "JOIN workspace_page_source_incarnation_attestations a "
            "ON a.workspace_id=ps.workspace_id "
            "AND a.source_owner_subject_id=ps.source_owner_subject_id "
            "AND a.source_page_id=ps.source_page_id "
            "AND a.binding_version=ps.row_version "
            "WHERE ps.workspace_id=? AND ps.source_owner_subject_id='owner' "
            "AND ps.source_page_id=?",
            (workspace_id, page_id),
        ).fetchone()
        return int(row[0]), int(row[1])
    finally:
        con.close()


def _run(prepared, request, *, operation_id: str, title: str = ""):
    store = prepared["store"]
    root = prepared["root"]
    source = prepared["source"]
    expected = capture_isolated_page_lifecycle_structural_precondition(
        store,
        request,
        rehearsal_root=root,
        user_data_root=root,
        expected_source_db_path=source,
        expected_catalog_db_path=store.db_path,
        source_quiesced_confirmed=True,
        source_trusted_clock=lambda: 2_000_000_001,
    )
    report = run_isolated_page_lifecycle_structural(
        store,
        request,
        actor_subject_id="reviewer",
        mutation_actor_subject_id="owner",
        requested_title=title,
        expected_source_precondition_sha256=expected,
        operation_id=operation_id,
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        rehearsal_root=root,
        user_data_root=root,
        expected_source_db_path=source,
        expected_catalog_db_path=store.db_path,
        source_quiesced_confirmed=True,
        source_trusted_clock=lambda: 2_000_000_001,
    )
    return report, expected


def test_exact_sentinel_create_converges_and_replays_without_path_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _rename_request = _materialized(tmp_path, monkeypatch)
    _install_v3(prepared["source"])
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    workspace = store.get_workspace("reviewer", workspace_id)
    target = "p_" + "7" * 32
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action="create",
        source_owner_subject_id="owner",
        source_page_id=target,
        expected_tree_version=1,
        expected_binding_version=0,
        expected_incarnation_generation=0,
        base_version=int(workspace["version"]),
    )
    report, expected = _run(
        prepared,
        request,
        operation_id="rehearsal-structural-create",
        title="Created by exact rehearsal",
    )
    assert report["finalized"] is True
    assert report["action"] == "create"
    assert report["new_scope_count"] == 3
    assert report["source_mutated_page_count"] == 1
    assert store.deep_health()["healthy"] is True
    encoded = json.dumps(report, sort_keys=True)
    assert target not in encoded
    assert "owner" not in encoded

    replay = run_isolated_page_lifecycle_structural(
        store,
        request,
        actor_subject_id="reviewer",
        mutation_actor_subject_id="owner",
        requested_title="Created by exact rehearsal",
        expected_source_precondition_sha256=expected,
        operation_id="rehearsal-structural-create",
        worker="worker.1",
        lease_token=LEASE_TOKEN,
        lease_seconds=120,
        rehearsal_root=tmp_path / "missing",
        user_data_root=tmp_path / "missing",
        expected_source_db_path=tmp_path / "missing.db",
        expected_catalog_db_path=tmp_path / "missing-catalog.db",
        source_quiesced_confirmed=False,
    )
    assert replay == {**report, "replayed": True}


@pytest.mark.parametrize("action", ["reorder", "move"])
def test_exact_sentinel_existing_structural_actions_converge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    prepared, _rename_request = _materialized(tmp_path, monkeypatch)
    _install_v3(prepared["source"])
    store = prepared["store"]
    workspace_id = str(prepared["workspace_id"])
    root_a = str(prepared["root_a"])
    root_b = str(prepared["root_b"])
    target = root_b if action == "reorder" else str(prepared["child"])
    version, incarnation = _existing_generation(
        store, workspace_id, target
    )
    workspace = store.get_workspace("reviewer", workspace_id)
    request = WorkspacePageLifecycleCommandRequest(
        workspace_id=workspace_id,
        action=action,
        source_owner_subject_id="owner",
        source_page_id=target,
        expected_tree_version=1,
        expected_binding_version=version,
        expected_incarnation_generation=incarnation,
        base_version=int(workspace["version"]),
        parent_source_owner_subject_id=("owner" if action == "move" else ""),
        parent_source_page_id=(root_b if action == "move" else ""),
        relative_source_owner_subject_id=(
            "owner" if action == "reorder" else ""
        ),
        relative_source_page_id=(root_a if action == "reorder" else ""),
        relative_position=("before" if action == "reorder" else ""),
    )
    report, _expected = _run(
        prepared,
        request,
        operation_id=f"rehearsal-structural-{action}",
    )
    assert report["finalized"] is True
    assert report["action"] == action
    assert report["structure_changed"] is True
    assert store.deep_health()["healthy"] is True
    con = store._read_connection()
    try:
        if action == "reorder":
            roots = con.execute(
                "SELECT source_page_id FROM workspace_page_tree_nodes "
                "WHERE workspace_id=? AND parent_resolution='root' "
                "ORDER BY sibling_ordinal",
                (workspace_id,),
            ).fetchall()
            assert [str(row[0]) for row in roots] == [root_b, root_a]
        else:
            parent = con.execute(
                "SELECT parent_source_page_id FROM workspace_page_tree_nodes "
                "WHERE workspace_id=? AND source_page_id=?",
                (workspace_id, target),
            ).fetchone()[0]
            assert parent == root_b
    finally:
        con.close()
