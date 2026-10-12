from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_schema_migration as migration
from code_assist_v1 import notion_workspace_store as store_module


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = "code_assist_v1.tools.migrate_notion_workspace_schema"


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.executescript(migration.SOURCE_SQL)
        con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (migration.SOURCE_VERSION, migration.SOURCE_NAME,
             migration.SOURCE_CHECKSUM,
             migration.SOURCE_ACTUAL_SCHEMA_SHA256, 100),
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


def _metadata(db: Path) -> tuple[int, int, int, str, tuple[str, ...]]:
    stat = db.stat()
    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        schema_version = int(con.execute("PRAGMA schema_version").fetchone()[0])
    finally:
        con.close()
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    entries = tuple(sorted(item.name for item in db.parent.iterdir()))
    return stat.st_size, stat.st_mtime_ns, schema_version, digest, entries


def _apply(root: Path, db: Path, backup: Path, *, fault_hook=None):
    plan = migration.build_plan(db, root)
    result = migration.apply_plan(
        db, root, exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True,
        backup_path=backup, fault_hook=fault_hook,
    )
    return plan, result


def _schema_contract(db: Path) -> tuple[list[dict[str, str]], str]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return migration._objects(con), migration.actual_schema_sha256(con)
    finally:
        con.close()


class _CommitFaultConnection:
    """Delegate connection whose commit reports a selected ambiguous outcome."""

    def __init__(self, delegate: sqlite3.Connection, mode: str) -> None:
        object.__setattr__(self, "_delegate", delegate)
        object.__setattr__(self, "_mode", mode)

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def __setattr__(self, name, value) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._delegate, name, value)

    def commit(self) -> None:
        if self._mode == "target":
            self._delegate.commit()
        elif self._mode == "source":
            self._delegate.rollback()
        elif self._mode == "unknown":
            self._delegate.commit()
            self._delegate.execute(
                "UPDATE workspace_schema_migrations SET checksum='sha256:unknown'"
            )
            self._delegate.commit()
        elif self._mode == "active":
            pass
        else:  # pragma: no cover - test helper contract
            raise AssertionError(self._mode)
        raise RuntimeError("secret commit transport detail")


def _inject_commit_fault(
    monkeypatch: pytest.MonkeyPatch, db: Path, mode: str,
) -> None:
    original_connect = migration.sqlite3.connect
    writer_wrapped = False

    def connect(database, *args, **kwargs):
        nonlocal writer_wrapped
        delegate = original_connect(database, *args, **kwargs)
        if (not writer_wrapped and not kwargs.get("uri", False) and
                Path(os.fspath(database)).resolve() == db.resolve()):
            writer_wrapped = True
            return _CommitFaultConnection(delegate, mode)
        return delegate

    monkeypatch.setattr(migration.sqlite3, "connect", connect)


def test_dry_run_is_deterministic_and_has_zero_catalog_metadata_diff(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)

    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)

    assert first == second
    assert first["state"] == "migration_required"
    assert first["operations"] == [
        "install_locator_projection_schema", "replace_migration_marker"
    ]
    assert _metadata(db) == before
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()


def test_dry_run_refuses_sqlite_sidecars_without_metadata_diff(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    wal = Path(f"{db}-wal")
    wal.write_bytes(b"uncheckpointed-wal-sentinel")
    before = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns,
                    hashlib.sha256(item.read_bytes()).hexdigest())
        for item in db.parent.iterdir()
    }
    with pytest.raises(migration.MigrationRefused, match="catalog_not_checkpointed"):
        migration.build_plan(db, root)
    after = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns,
                    hashlib.sha256(item.read_bytes()).hexdigest())
        for item in db.parent.iterdir()
    }
    assert after == before
    assert not Path(f"{db}-shm").exists()


def test_clean_persistent_wal_catalog_is_refused_without_any_write(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    con = sqlite3.connect(db)
    try:
        assert str(con.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        assert not Path(f"{db}{suffix}").exists()
    before = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns,
                    hashlib.sha256(item.read_bytes()).hexdigest())
        for item in db.parent.iterdir()
    }
    with pytest.raises(migration.MigrationRefused, match="journal_mode_unsupported"):
        migration.build_plan(db, root)
    after = {
        item.name: (item.stat().st_size, item.stat().st_mtime_ns,
                    hashlib.sha256(item.read_bytes()).hexdigest())
        for item in db.parent.iterdir()
    }
    assert after == before
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()


def test_apply_requires_all_guards_and_exact_verified_backup(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    plan = migration.build_plan(db, root)
    before = _metadata(db)
    common = dict(catalog_path=db, user_data_root=root,
                  exact_plan_checksum=plan["plan_checksum"], backup_path=backup)

    with pytest.raises(migration.MigrationRefused, match="requires"):
        migration.apply_plan(**common, backup_confirmed=False,
                             catalog_quiesced_confirmed=True)
    with pytest.raises(migration.MigrationRefused, match="requires"):
        migration.apply_plan(**common, backup_confirmed=True,
                             catalog_quiesced_confirmed=False)
    with pytest.raises(migration.MigrationRefused, match="checksum"):
        migration.apply_plan(db, root, exact_plan_checksum="sha256:wrong",
                             backup_confirmed=True,
                             catalog_quiesced_confirmed=True, backup_path=backup)
    assert _metadata(db) == before

    bad_backup = root / "wrong.bak"
    sqlite3.connect(bad_backup).close()
    with pytest.raises(migration.MigrationRefused, match="backup"):
        migration.apply_plan(**common | {"backup_path": bad_backup},
                             backup_confirmed=True,
                             catalog_quiesced_confirmed=True)
    assert _metadata(db) == before


@pytest.mark.parametrize("corrupt_target", ["source", "backup"])
def test_apply_rejects_source_or_backup_foreign_key_damage(
    tmp_path: Path, corrupt_target: str,
) -> None:
    root, db = _source_catalog(tmp_path)
    if corrupt_target == "source":
        corrupt_path = db
    else:
        corrupt_path = root / "workspace.before-1304.bak"
        _backup(db, corrupt_path)
    con = sqlite3.connect(corrupt_path)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute(
            "INSERT INTO workspace_audit_events VALUES(?,?,?,?,?,?,?,?,?)",
            ("waud_corrupt", "ws_missing", "owner", "corrupt", "x", "x",
             "corrupt-fk", "{}", 1),
        )
        con.commit()
    finally:
        con.close()
    if corrupt_target == "source":
        backup = _backup(db, root / "workspace.before-1304.bak")
    else:
        backup = corrupt_path
    plan = migration.build_plan(db, root)
    before = _metadata(db)
    with pytest.raises(migration.MigrationRefused, match="foreign_key_check"):
        migration.apply_plan(
            db, root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True, catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    assert _metadata(db) == before

def test_apply_produces_frozen_1304_target_that_1305_runtime_rejects(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    old_plan, result = _apply(root, db, backup)
    assert result["applied"] is True
    assert result["applied_plan_checksum"] == old_plan["plan_checksum"]

    migrated = store_module.WorkspaceStore(db)
    assert migration.TARGET_VERSION == 2026081304
    assert store_module.SCHEMA_VERSION == 2026081317
    assert migrated.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        migrated.verify_deep_health()
    assert _schema_contract(db) == (
        migration.TARGET_OBJECTS, migration.TARGET_ACTUAL_SCHEMA_SHA256
    )
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        con.close()

    current_plan = migration.build_plan(db, root)
    assert current_plan["state"] == "already_current"
    assert current_plan["operations"] == []
    assert current_plan["plan_checksum"] != old_plan["plan_checksum"]
    no_op = migration.apply_plan(
        db, root, exact_plan_checksum=current_plan["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path="",
    )
    assert no_op["applied"] is False
    assert no_op["reason"] == "already_current"


@pytest.mark.parametrize("fault_point", ["after_schema", "before_commit"])
def test_fault_rolls_back_every_schema_and_marker_change(
    tmp_path: Path, fault_point: str,
) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    before = _metadata(db)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected migration fault")

    with pytest.raises(RuntimeError, match="injected"):
        _apply(root, db, backup, fault_hook=fault)
    assert _metadata(db) == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_backup_change_before_commit_rolls_back(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    before = _metadata(db)

    def corrupt_backup(point: str) -> None:
        if point == "after_schema":
            backup.write_bytes(b"not-a-sqlite-backup")

    with pytest.raises(migration.MigrationRefused, match="backup changed"):
        _apply(root, db, backup, fault_hook=corrupt_backup)
    assert _metadata(db) == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_success_path_does_not_reopen_catalog_after_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    plan = migration.build_plan(db, root)
    original_build_plan = migration.build_plan
    calls = 0

    def one_plan_only(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("apply must not inspect the catalog after commit")
        return original_build_plan(*args, **kwargs)

    monkeypatch.setattr(migration, "build_plan", one_plan_only)
    report = migration.apply_plan(
        db, root, exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert report["applied"] is True
    assert report["state"] == "already_current"
    assert report["commit_recovered"] is False
    assert calls == 1


@pytest.mark.parametrize("outcome", ["target", "source", "active", "unknown"])
def test_commit_exception_converges_only_exact_durable_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str,
) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1304.bak")
    plan = migration.build_plan(db, root)
    _inject_commit_fault(monkeypatch, db, outcome)

    if outcome == "target":
        report = migration.apply_plan(
            db, root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True, catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
        assert report["applied"] is True
        assert report["commit_recovered"] is True
        assert report["state"] == "already_current"
        assert migration.build_plan(db, root)["state"] == "already_current"
    elif outcome in {"source", "active"}:
        with pytest.raises(RuntimeError, match="secret commit transport detail"):
            migration.apply_plan(
                db, root, exact_plan_checksum=plan["plan_checksum"],
                backup_confirmed=True, catalog_quiesced_confirmed=True,
                backup_path=backup,
            )
        assert migration.build_plan(db, root)["state"] == "migration_required"
    else:
        with pytest.raises(migration.MigrationRefused) as caught:
            migration.apply_plan(
                db, root, exact_plan_checksum=plan["plan_checksum"],
                backup_confirmed=True, catalog_quiesced_confirmed=True,
                backup_path=backup,
            )
        assert str(caught.value) == (
            "commit_outcome_indeterminate: manual audit required"
        )
        assert caught.value.__cause__ is None
        assert "secret" not in str(caught.value)


def test_unknown_marker_checksum_or_extra_object_is_fail_closed(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE workspace_schema_migrations SET checksum='sha256:unknown'")
        con.commit()
    finally:
        con.close()
    with pytest.raises(migration.MigrationRefused, match="unsupported_source_schema"):
        migration.build_plan(db, root)

    root2, db2 = _source_catalog(tmp_path / "extra")
    con = sqlite3.connect(db2)
    con.row_factory = sqlite3.Row
    try:
        con.execute("CREATE TABLE unexpected_object(value TEXT)")
        con.execute(
            "UPDATE workspace_schema_migrations SET actual_schema_sha256=?",
            (migration.actual_schema_sha256(con),),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(migration.MigrationRefused, match="unsupported_source_schema"):
        migration.build_plan(db2, root2)


def test_exact_path_and_symlink_guards(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    with pytest.raises(migration.MigrationRefused, match="exactly equal"):
        migration.build_plan(db, tmp_path)
    outside = tmp_path / "outside.db"
    outside.write_bytes(db.read_bytes())
    with pytest.raises(migration.MigrationRefused, match="exactly equal"):
        migration.build_plan(outside, root)

    link_root = tmp_path / "linked-root"
    try:
        link_root.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")
    linked_db = link_root / ".notion-workspace-v2" / "workspace.db"
    with pytest.raises(migration.MigrationRefused, match="symlinked"):
        migration.build_plan(linked_db, link_root)


def test_hard_linked_catalog_or_backup_is_fail_closed(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    external_alias = tmp_path / "external-catalog-alias.db"
    try:
        os.link(db, external_alias)
    except OSError:
        pytest.skip("hard links are unavailable on this platform")
    with pytest.raises(migration.MigrationRefused, match="hard-linked catalog"):
        migration.build_plan(db, root)
    external_alias.unlink()

    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1304.bak")
    backup_alias = root / "workspace.before-1304-alias.bak"
    os.link(backup, backup_alias)
    with pytest.raises(migration.MigrationRefused, match="hard-linked backup"):
        migration.apply_plan(
            db, root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True, catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
    backup_alias.unlink()
    backup.unlink()

    same_file_backup = root / "catalog-hardlink.bak"
    os.link(db, same_file_backup)
    with pytest.raises(migration.MigrationRefused, match="hard-linked catalog"):
        migration.apply_plan(
            db, root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True, catalog_quiesced_confirmed=True,
            backup_path=same_file_backup,
        )


def test_1303_remains_runtime_fail_closed_until_explicit_apply(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    runtime = store_module.WorkspaceStore(db)
    assert runtime.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        runtime.get_workspace("owner", "ws_any")


def test_cli_dry_run_rejects_apply_only_arguments(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", CLI_MODULE,
         "--catalog-path", str(db), "--user-data-root", str(root),
         "--backup-confirmed"],
        cwd=REPOSITORY_ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=30,
    )
    assert completed.returncode == 2
    assert "apply-only" in completed.stderr


def test_cli_defaults_to_read_only_plan_and_explicit_apply(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    base = [sys.executable, "-m", CLI_MODULE,
            "--catalog-path", str(db), "--user-data-root", str(root)]
    dry_run = subprocess.run(
        base, cwd=REPOSITORY_ROOT, env=environment, text=True,
        capture_output=True, check=False, timeout=30,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    plan = json.loads(dry_run.stdout)
    assert plan["mode"] == "dry-run"
    assert plan["state"] == "migration_required"
    assert _metadata(db) == before

    backup = _backup(db, root / "workspace.before-1304.bak")
    applied = subprocess.run(
        [*base, "--apply", "--backup-confirmed",
         "--catalog-quiesced-confirmed", "--exact-plan-checksum",
         plan["plan_checksum"], "--backup-path", str(backup)],
        cwd=REPOSITORY_ROOT, env=environment, text=True,
        capture_output=True, check=False, timeout=30,
    )
    assert applied.returncode == 0, applied.stderr
    report = json.loads(applied.stdout)
    assert report["mode"] == "apply"
    assert report["applied"] is True
    assert report["backup_verification"]["logical_snapshot_match"] is True
    assert report["target_version"] == migration.TARGET_VERSION == 2026081304
    assert _schema_contract(db) == (
        migration.TARGET_OBJECTS, migration.TARGET_ACTUAL_SCHEMA_SHA256
    )
    assert store_module.WorkspaceStore(db).capabilities() == {
        "workspace_v2": False,
        "schema_version": None,
    }


def test_import_has_no_config_auth_or_filesystem_side_effects(tmp_path: Path) -> None:
    import_cwd = tmp_path / "import-cwd"
    import_cwd.mkdir()
    sentinel = tmp_path / "must-not-be-created"
    code = (
        "import sys,pathlib; "
        f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r}); "
        "import code_assist_v1.notion_workspace_schema_migration; "
        "import code_assist_v1.tools.migrate_notion_workspace_schema; "
        "assert 'code_assist_v1.config' not in sys.modules; "
        "assert 'code_assist_v1.auth' not in sys.modules; "
        f"assert not pathlib.Path({str(sentinel)!r}).exists(); "
        "assert list(pathlib.Path.cwd().iterdir()) == []"
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["CODE_ASSIST_DATA_ROOT"] = str(sentinel)
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=import_cwd, env=environment,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
