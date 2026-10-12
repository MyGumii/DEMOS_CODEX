from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_healing_migration as migration
from code_assist_v1 import notion_workspace_outbox_migration as migration_1305
from code_assist_v1 import notion_workspace_store as store_module


CLI_MODULE = "code_assist_v1.tools.migrate_notion_workspace_healing"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.executescript(migration.SOURCE_SQL)
        con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration.SOURCE_VERSION,
                migration.SOURCE_NAME,
                migration.SOURCE_CHECKSUM,
                migration.SOURCE_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        con.commit()
    finally:
        con.close()
    return root, db


def _backup(source: Path, destination: Path) -> Path:
    source_con = sqlite3.connect(source)
    destination_con = sqlite3.connect(destination)
    try:
        source_con.backup(destination_con)
        destination_con.commit()
    finally:
        destination_con.close()
        source_con.close()
    return destination


def _metadata(path: Path):
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        tuple(sorted(item.name for item in path.parent.iterdir())),
    )


def _schema_contract(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return migration._objects(con), migration.actual_schema_sha256(con)
    finally:
        con.close()


def test_frozen_1305_is_exactly_1306_minus_healing_objects() -> None:
    assert migration.SOURCE_SQL == migration_1305.TARGET_SQL
    assert migration_1305.schema_1304._strip_schema_objects(
        migration.TARGET_SQL, migration.HEALING_RUN_OBJECTS
    ) == migration.SOURCE_SQL
    assert migration.TARGET_VERSION == 2026081306
    assert store_module.SCHEMA_VERSION == 2026081317
    assert migration.TARGET_CHECKSUM == (
        "sha256:6f5b23e4a276fd25aa96b1e427e8f4dce55e307fb059095baa46d6178ed13f52"
    )
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256 == (
        "sha256:abaadac598bea97af44260bdbbf31a9ca883b65e20dbefaabacd1c5be18ec025"
    )


def test_dry_run_is_read_only_and_1305_runtime_is_fail_closed(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    assert first == second
    assert first["state"] == "migration_required"
    assert _metadata(db) == before
    runtime = store_module.WorkspaceStore(db)
    assert runtime.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(RuntimeError, match="explicit migration"):
        runtime.bootstrap()
    assert _metadata(db) == before


def test_apply_matches_frozen_1306_and_requires_next_explicit_migration(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert result["applied"] is True
    assert result["commit_recovered"] is False
    assert _schema_contract(db) == (
        migration.TARGET_OBJECTS,
        migration.TARGET_ACTUAL_SCHEMA_SHA256,
    )
    runtime = store_module.WorkspaceStore(db)
    assert runtime.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(RuntimeError, match="explicit migration"):
        runtime.bootstrap()
    assert migration.build_plan(db, root)["state"] == "already_current"


@pytest.mark.parametrize("fault_point", ["after_schema", "before_commit"])
def test_apply_fault_rolls_back_extension_and_marker(
    tmp_path, fault_point
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    before = _metadata(db)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected healing migration fault")

    with pytest.raises(RuntimeError, match="injected"):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=fault,
        )
    assert migration.build_plan(db, root)["state"] == "migration_required"
    assert _metadata(db) == before


def test_apply_rejects_path_swap_to_logically_identical_clone(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = root / "workspace.before-1306.bak"
    backup.write_bytes(db.read_bytes())
    detached = db.with_name("workspace.detached.db")
    db.replace(detached)
    db.write_bytes(detached.read_bytes())

    with pytest.raises(migration.MigrationRefused, match="checksum|identity"):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )

    assert migration.build_plan(db, root)["state"] == "migration_required"
    assert detached.is_file()


@pytest.mark.parametrize(
    "attack_point", ["after_backup_verify", "after_final_backup_verify"]
)
@pytest.mark.parametrize("operation", ["delete", "replace"])
def test_backup_attack_after_final_verify_is_blocked_or_rolls_back(
    tmp_path, operation, attack_point
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    attack_blocked: list[OSError] = []

    def attack(point: str) -> None:
        if point != attack_point:
            return
        try:
            if operation == "delete":
                backup.unlink()
            else:
                replacement = backup.with_name("replacement.bak")
                replacement.write_bytes(backup.read_bytes())
                os.replace(replacement, backup)
        except OSError as exc:
            # Windows may make the pin/SQLite read lock itself the protection.
            attack_blocked.append(exc)

    try:
        result = migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=attack,
        )
    except migration.MigrationRefused as exc:
        assert not attack_blocked
        assert "backup changed" in str(exc)
        assert migration.build_plan(db, root)["state"] == "migration_required"
    else:
        assert attack_blocked
        assert result["applied"] is True
        assert backup.is_file()


def test_after_commit_hook_exception_converges_to_durable_target(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")

    def fault(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("post-commit observer failed")

    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
        fault_hook=fault,
    )
    assert result["applied"] is True
    assert result["commit_recovered"] is False
    assert migration.build_plan(db, root)["state"] == "already_current"
    assert backup.is_file()


@pytest.mark.parametrize("operation", ["delete", "replace"])
def test_backup_attack_after_commit_is_blocked_or_reports_manual_audit(
    tmp_path, operation
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    blocked: list[OSError] = []

    def attack(point: str) -> None:
        if point != "after_commit":
            return
        try:
            if operation == "delete":
                backup.unlink()
            else:
                replacement = backup.with_name("post-commit-replacement.bak")
                replacement.write_bytes(backup.read_bytes())
                os.replace(replacement, backup)
        except OSError as exc:
            blocked.append(exc)

    try:
        result = migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=attack,
        )
    except migration.MigrationRefused as exc:
        assert not blocked
        assert str(exc) == (
            "post_replacement_verification_failed: manual audit required"
        )
    else:
        assert blocked
        assert result["applied"] is True
        assert backup.is_file()
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_post_replace_sidecar_is_never_consumed_and_forces_manual_audit(
    tmp_path,
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    journal = Path(f"{db}-journal")

    def attack(point: str) -> None:
        if point == "after_commit":
            journal.write_bytes(b"untrusted-sidecar")

    with pytest.raises(migration.MigrationRefused) as caught:
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=attack,
        )
    assert str(caught.value) == (
        "post_replacement_verification_failed: manual audit required"
    )
    assert journal.read_bytes() == b"untrusted-sidecar"
    assert backup.is_file()
    con = migration._connect_immutable_ro(db)
    try:
        assert migration._inspect(con)["state"] == "already_current"
    finally:
        con.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic-image migration contract")
def test_source_path_swap_before_read_is_blocked_or_refused_without_write(
    tmp_path,
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    detached = db.with_name("workspace.detached.db")
    blocked: list[OSError] = []

    def attack(point: str) -> None:
        if point != "before_writer_open":
            return
        try:
            db.replace(detached)
        except OSError as exc:
            blocked.append(exc)

    try:
        result = migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
            fault_hook=attack,
        )
    except migration.MigrationRefused as exc:
        assert not blocked
        assert str(exc) in {
            "catalog identity changed during migration",
            "migration file identity changed",
            "migration file is unavailable",
        }
        assert detached.is_file()
        detached.replace(db)
        assert migration.build_plan(db, root)["state"] == "migration_required"
    else:
        assert blocked
        assert result["applied"] is True
        assert not detached.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-release contract")
def test_build_plan_refusal_releases_catalog_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root, db = _source_catalog(tmp_path)
    moved = db.with_name("workspace.moved.db")

    def refuse(*_args, **_kwargs):
        raise migration.MigrationRefused("guard refusal")

    monkeypatch.setattr(migration, "_require_checkpointed", refuse)
    with pytest.raises(migration.MigrationRefused, match="guard refusal"):
        migration.build_plan(db, root)
    db.replace(moved)
    moved.replace(db)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-release contract")
def test_apply_backup_pin_failure_releases_catalog_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    moved = db.with_name("workspace.moved.db")
    original_open_pin = migration._open_pin

    def fail_backup(path: Path) -> int:
        if Path(path) == backup:
            raise migration.MigrationRefused("backup pin refusal")
        return original_open_pin(path)

    monkeypatch.setattr(migration, "_open_pin", fail_backup)
    with pytest.raises(migration.MigrationRefused, match="backup pin refusal"):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    db.replace(moved)
    moved.replace(db)


def test_native_replace_exception_converges_exact_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    original_replace = migration.secure_fs._replace_windows_handle_relative

    def committed_then_error(*args, **kwargs):
        original_replace(*args, **kwargs)
        raise migration.secure_fs.LegacyChangeMigrationRefused(
            "simulated transport ambiguity"
        )

    monkeypatch.setattr(
        migration.secure_fs,
        "_replace_windows_handle_relative",
        committed_then_error,
    )
    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert result["applied"] is True
    assert result["commit_recovered"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_unknown_native_replace_outcome_never_deletes_live_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    original_replace = migration.secure_fs._replace_windows_handle_relative
    original_final_path = migration.secure_fs._windows_final_path
    replacement_attempted = False

    def committed_then_error(*args, **kwargs):
        nonlocal replacement_attempted
        original_replace(*args, **kwargs)
        replacement_attempted = True
        raise migration.secure_fs.LegacyChangeMigrationRefused(
            "simulated unprovable rename outcome"
        )

    def unavailable_path(handle):
        if replacement_attempted:
            raise migration.secure_fs.LegacyChangeMigrationRefused(
                "simulated final-path inspection failure"
            )
        return original_final_path(handle)

    monkeypatch.setattr(
        migration.secure_fs,
        "_replace_windows_handle_relative",
        committed_then_error,
    )
    monkeypatch.setattr(migration.secure_fs, "_windows_final_path", unavailable_path)
    with pytest.raises(migration.MigrationRefused) as caught:
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    assert str(caught.value) == (
        "replacement_outcome_indeterminate: manual audit required"
    )
    assert db.is_file()
    assert backup.is_file()
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_apply_never_opens_catalog_with_pathname_sqlite_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1306.bak")
    original_connect = migration.sqlite3.connect

    def connect(database, *args, **kwargs):
        if (
            not kwargs.get("uri", False)
            and os.fspath(database) != ":memory:"
            and Path(os.fspath(database)).resolve() == db.resolve()
        ):
            raise AssertionError("catalog pathname writer must never open")
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(migration.sqlite3, "connect", connect)
    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert result["applied"] is True


def test_cli_dry_run_is_read_only_and_apply_is_explicit(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    base = [
        sys.executable,
        "-m",
        CLI_MODULE,
        "--catalog-path",
        str(db),
        "--user-data-root",
        str(root),
    ]
    before = _metadata(db)
    dry = subprocess.run(
        base,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert dry.returncode == 0, dry.stderr
    plan = json.loads(dry.stdout)
    assert plan["mode"] == "dry-run"
    assert _metadata(db) == before
    backup = _backup(db, root / "workspace.before-1306.bak")
    applied = subprocess.run(
        [
            *base,
            "--apply",
            "--backup-confirmed",
            "--catalog-quiesced-confirmed",
            "--exact-plan-checksum",
            plan["plan_checksum"],
            "--backup-path",
            str(backup),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert applied.returncode == 0, applied.stderr
    report = json.loads(applied.stdout)
    assert report["mode"] == "apply"
    assert report["target_version"] == 2026081306


def test_import_has_no_app_auth_config_or_filesystem_side_effects(tmp_path) -> None:
    probe = tmp_path / "probe"
    probe.mkdir()
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.data_paths",
        "code_assist_v1.knowledge_core",
        "code_assist_v1.second_brain_store",
    )
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_healing_migration; "
        f"names={forbidden!r}; "
        "print(json.dumps(sorted(x for x in names if x in sys.modules)))"
    )
    before = tuple(probe.iterdir())
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
    assert json.loads(completed.stdout) == []
    assert tuple(probe.iterdir()) == before
