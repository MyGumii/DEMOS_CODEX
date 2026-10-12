from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_store
from code_assist_v1.notion_workspace_inventory import (
    WorkspaceBackfillFailure,
    WorkspaceInventoryError,
    apply_backfill_plan,
    build_backfill_plan,
    dry_run_report,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = "code_assist_v1.tools.migrate_notion_workspace_catalog"


def _fail_scan() -> list[str]:
    raise AssertionError("backfill must never enumerate USER_DATA_DIR")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(path: Path) -> dict:
    stat = path.stat()
    return {
        "sha256": _sha(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "siblings": sorted(item.name for item in path.parent.iterdir()),
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pages: int = 2):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Inventory", operation_id="create-inventory-workspace"
    )["workspace"]
    created = []
    for index in range(pages):
        created.append(notion_store.create_page_from_markdown(
            "owner",
            title=f"PRIVATE TITLE {index}",
            markdown=f"secret body number {index}",
        )["page"])
    archived = notion_store.create_page("owner", title="ARCHIVED SECRET")["page"]
    notion_store.archive_page("owner", archived["id"])
    public = notion_store.enable_public_link("owner", created[0]["id"])
    source = tmp_path / "owner" / "notion" / "notion.db"
    catalog = Path(store.db_path)
    return store, workspace, created, str(public["token"]), source, catalog


def _plan(tmp_path: Path, workspace: dict, source: Path, catalog: Path) -> dict:
    return build_backfill_plan(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        expected_source_db_path=source.resolve(),
        expected_catalog_db_path=catalog.resolve(),
    )


@pytest.mark.regression
def test_np2a_dry_run_is_read_only_deterministic_secret_free_and_scan_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, pages, public_token, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    source_before = _metadata(source)
    catalog_before = _metadata(catalog)
    expected_proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", pages[0]["id"]
    ).descriptor()
    monkeypatch.setattr(notion_store, "_known_workspace_users", _fail_scan)

    first = _plan(tmp_path, workspace, source, catalog)
    second = _plan(tmp_path, workspace, source, catalog)
    report_text = json.dumps(dry_run_report(first), ensure_ascii=False, sort_keys=True)

    assert first == second
    assert first["summary"] == {
        "apply_ready": True,
        "blocked_count": 0,
        "already_active_count": 0,
        "would_change_count": 2,
    }
    assert first["source"]["active_page_count"] == 2
    assert first["source"]["archived_page_count"] == 1
    planned_proofs = {item["page_id"]: item["proof"] for item in first["pages"]}
    assert planned_proofs[pages[0]["id"]] == expected_proof
    assert "PRIVATE TITLE" not in report_text
    assert "secret body" not in report_text
    assert "ARCHIVED SECRET" not in report_text
    assert public_token not in report_text
    assert _metadata(source) == source_before
    assert _metadata(catalog) == catalog_before
    assert store.audit_counts() == {"operations": 1, "audit_events": 1, "outbox_events": 1}


@pytest.mark.regression
def test_np2a_exact_paths_checksum_and_version_fail_closed_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, _pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    with pytest.raises(WorkspaceInventoryError, match="exact owner shard"):
        build_backfill_plan(
            user_data_root=tmp_path,
            source_owner_id="owner",
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            expected_source_db_path=catalog,
            expected_catalog_db_path=catalog,
        )
    plan = _plan(tmp_path, workspace, source, catalog)
    siblings = sorted(item.name for item in catalog.parent.iterdir())
    with pytest.raises(WorkspaceInventoryError, match="confirm_checksum"):
        apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum="sha256:" + "0" * 64,
            confirm_workspace_version=workspace["version"],
        )
    with pytest.raises(WorkspaceInventoryError, match="confirm_workspace_version"):
        apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=plan["plan_checksum"],
            confirm_workspace_version=workspace["version"] + 1,
        )
    assert sorted(item.name for item in catalog.parent.iterdir()) == siblings


@pytest.mark.regression
def test_np2a_changed_source_or_catalog_invalidates_approved_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    source_plan = _plan(tmp_path, workspace, source, catalog)
    notion_store.update_page("owner", pages[0]["id"], {"title": "changed"})
    with pytest.raises(WorkspaceInventoryError, match="changed after"):
        apply_backfill_plan(
            source_plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=source_plan["plan_checksum"],
            confirm_workspace_version=source_plan["catalog"]["workspace_version"],
        )

    catalog_plan = _plan(tmp_path, workspace, source, catalog)
    store.set_member(
        "owner", workspace["id"], "member_one", role="member",
        base_version=catalog_plan["catalog"]["workspace_version"],
        operation_id="change-catalog-after-plan",
    )
    with pytest.raises(WorkspaceInventoryError, match="changed after"):
        apply_backfill_plan(
            catalog_plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=catalog_plan["plan_checksum"],
            confirm_workspace_version=catalog_plan["catalog"]["workspace_version"],
        )


@pytest.mark.regression
def test_np2a_partial_pending_is_reported_and_new_plan_resumes_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=2
    )
    plan = _plan(tmp_path, workspace, source, catalog)

    def fail_after_first_stage(event: str, page: dict) -> None:
        if event == "after_stage" and page["number"] == 1:
            raise RuntimeError("injected stop")

    with pytest.raises(WorkspaceBackfillFailure) as stopped:
        apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=plan["plan_checksum"],
            confirm_workspace_version=plan["catalog"]["workspace_version"],
            failure_injector=fail_after_first_stage,
        )
    failure_report = stopped.value.report
    assert failure_report["status"] == "partial_failure"
    assert failure_report["pages"][0]["status"] == "pending_after_failure"
    assert failure_report["postcondition"]["deep_health"]["healthy"] is True

    resume = _plan(tmp_path, workspace, source, catalog)
    assert resume["pages"][0]["action"] == "activate_pending"
    completed = apply_backfill_plan(
        resume,
        backup_confirmed=True,
        source_quiesced_confirmed=True,
        confirm_checksum=resume["plan_checksum"],
        confirm_workspace_version=resume["catalog"]["workspace_version"],
    )
    assert completed["status"] == "completed"
    assert completed["postcondition"]["all_planned_pages_active"] is True
    assert completed["postcondition"]["deep_health"]["healthy"] is True
    con = sqlite3.connect(catalog)
    try:
        rows = con.execute(
            """
            SELECT source_page_id,state,COUNT(*) FROM workspace_page_sources
            WHERE source_owner_subject_id='owner'
            GROUP BY source_page_id,state ORDER BY source_page_id
            """
        ).fetchall()
    finally:
        con.close()
    assert rows == sorted(
        [(pages[0]["id"], "active", 1), (pages[1]["id"], "active", 1)]
    )
    rerun = _plan(tmp_path, workspace, source, catalog)
    assert [item["action"] for item in rerun["pages"]] == [
        "already_active", "already_active"
    ]


@pytest.mark.regression
def test_np2a_cli_dry_run_and_guarded_apply_use_only_explicit_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, _pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    common = [
        "--user-data-root", str(tmp_path.resolve()),
        "--source-owner-id", "owner",
        "--actor-subject-id", "owner",
        "--workspace-id", workspace["id"],
        "--expected-source-db-path", str(source.resolve()),
        "--expected-catalog-db-path", str(catalog.resolve()),
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    dry = subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *common],
        cwd=REPOSITORY_ROOT, env=environment, text=True, capture_output=True,
        check=False, timeout=30,
    )
    assert dry.returncode == 0, dry.stderr
    dry_report = json.loads(dry.stdout)
    assert dry_report["mode"] == "dry-run"
    apply = subprocess.run(
        [
            sys.executable, "-m", CLI_MODULE, *common,
            "--apply", "--backup-confirmed", "--source-quiesced-confirmed",
            "--confirm-checksum", dry_report["plan_checksum"],
            "--confirm-workspace-version", str(dry_report["catalog"]["workspace_version"]),
        ],
        cwd=REPOSITORY_ROOT, env=environment, text=True, capture_output=True,
        check=False, timeout=30,
    )
    assert apply.returncode == 0, apply.stderr
    applied = json.loads(apply.stdout)
    assert applied["status"] == "completed"
    backup = Path(applied["backup"]["path"])
    assert backup.is_file()
    assert backup.parent == catalog.parent
    assert applied["backup"]["deep_health"]["healthy"] is True


@pytest.mark.regression
def test_np2a_inventory_import_is_config_free_and_has_no_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = sorted(str(item.relative_to(probe)) for item in probe.rglob("*"))
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_inventory; "
        "print(json.dumps({'auth': 'code_assist_v1.auth' in sys.modules, "
        "'config': 'code_assist_v1.config' in sys.modules}))"
    )
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"auth": False, "config": False}
    after = sorted(str(item.relative_to(probe)) for item in probe.rglob("*"))
    assert after == before


@pytest.mark.regression
def test_np2a_source_quiesce_confirmation_fails_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, _pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    plan = _plan(tmp_path, workspace, source, catalog)
    before = sorted(item.name for item in catalog.parent.iterdir())
    with pytest.raises(WorkspaceInventoryError, match="source_quiesced_confirmed"):
        apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=False,
            confirm_checksum=plan["plan_checksum"],
            confirm_workspace_version=plan["catalog"]["workspace_version"],
        )
    assert sorted(item.name for item in catalog.parent.iterdir()) == before


@pytest.mark.regression
def test_np2a_schema_owner_and_legacy_access_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, _pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    con = sqlite3.connect(source)
    try:
        con.execute("UPDATE pages SET owner_user_id='intruder'")
        con.commit()
    finally:
        con.close()
    with pytest.raises(WorkspaceInventoryError, match="another owner"):
        _plan(tmp_path, workspace, source, catalog)

    # A distinct exact owner shard with valid schema but no share for the
    # workspace actor is reported as blocked and cannot be applied.
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    page = notion_store.create_page("source_owner", title="private")["page"]
    current = store.get_workspace("owner", workspace["id"])
    store.set_member(
        "owner", workspace["id"], "source_owner", role="member",
        base_version=current["version"], operation_id="add-source-owner",
    )
    other_source = tmp_path / "source_owner" / "notion" / "notion.db"
    blocked = build_backfill_plan(
        user_data_root=tmp_path,
        source_owner_id="source_owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        expected_source_db_path=other_source,
        expected_catalog_db_path=catalog,
    )
    assert blocked["summary"]["apply_ready"] is False
    assert blocked["pages"] == [
        {"number": 1, "page_id": page["id"], "action": "blocked_legacy_access"}
    ]
    with pytest.raises(WorkspaceInventoryError, match="blocked pages"):
        apply_backfill_plan(
            blocked,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=blocked["plan_checksum"],
            confirm_workspace_version=blocked["catalog"]["workspace_version"],
        )

    bad_owner = "bad_schema_owner"
    current = store.get_workspace("owner", workspace["id"])
    store.set_member(
        "owner", workspace["id"], bad_owner, role="member",
        base_version=current["version"], operation_id="add-bad-schema-owner",
    )
    bad_source = tmp_path / bad_owner / "notion" / "notion.db"
    bad_source.parent.mkdir(parents=True)
    con = sqlite3.connect(bad_source)
    try:
        con.execute("CREATE TABLE pages(id TEXT PRIMARY KEY)")
        con.commit()
    finally:
        con.close()
    with pytest.raises(WorkspaceInventoryError, match="schema is not ready"):
        build_backfill_plan(
            user_data_root=tmp_path,
            source_owner_id=bad_owner,
            actor_subject_id=bad_owner,
            workspace_id=workspace["id"],
            expected_source_db_path=bad_source,
            expected_catalog_db_path=catalog,
        )


@pytest.mark.regression
def test_np2a_non_admin_cannot_plan_another_owners_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    changed = store.set_member(
        "owner", workspace["id"], "ordinary_member", role="member",
        base_version=workspace["version"], operation_id="add-ordinary-member",
    )
    notion_store.share_page(
        "owner", pages[0]["id"], "ordinary_member", role="view"
    )
    blocked = build_backfill_plan(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="ordinary_member",
        workspace_id=workspace["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert blocked["catalog"]["workspace_version"] == changed["workspace"]["version"]
    assert blocked["summary"]["apply_ready"] is False
    assert blocked["pages"][0]["action"] == "blocked_catalog_permission"
    assert blocked["pages"][0]["proof"] == notion_store.capture_legacy_page_source_proof(
        "ordinary_member", "owner", pages[0]["id"]
    ).descriptor()


@pytest.mark.regression
def test_np2a_after_activate_fault_resumes_as_already_active_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    plan = _plan(tmp_path, workspace, source, catalog)

    def fail_after_activate(event: str, _page: dict) -> None:
        if event == "after_activate":
            raise RuntimeError("injected after activation")

    with pytest.raises(WorkspaceBackfillFailure) as stopped:
        apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=plan["plan_checksum"],
            confirm_workspace_version=plan["catalog"]["workspace_version"],
            failure_injector=fail_after_activate,
        )
    assert stopped.value.report["pages"][0]["status"] == "active"
    resume = _plan(tmp_path, workspace, source, catalog)
    assert resume["pages"][0]["action"] == "already_active"
    con = sqlite3.connect(catalog)
    try:
        source_rows = con.execute(
            "SELECT COUNT(*) FROM workspace_page_sources WHERE source_page_id=?",
            (pages[0]["id"],),
        ).fetchone()[0]
        operation_rows = con.execute(
            """
            SELECT action,COUNT(*) FROM workspace_operations
            WHERE action IN ('workspace.page_source_staged','workspace.page_source_bound')
            GROUP BY action ORDER BY action
            """
        ).fetchall()
    finally:
        con.close()
    assert source_rows == 1
    assert operation_rows == [
        ("workspace.page_source_bound", 1),
        ("workspace.page_source_staged", 1),
    ]


@pytest.mark.regression
def test_np2a_resolved_symlink_escape_is_rejected_when_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, _pages, _token, source, catalog = _fixture(
        tmp_path, monkeypatch, pages=1
    )
    outside = tmp_path.parent / f"{tmp_path.name}-outside-notion.db"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available on this platform")
    try:
        with pytest.raises(WorkspaceInventoryError, match="database .*escapes"):
            _plan(tmp_path, workspace, source, catalog)
    finally:
        outside.unlink(missing_ok=True)
