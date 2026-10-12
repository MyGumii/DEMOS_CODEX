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
from code_assist_v1 import notion_legacy_change_migration as v1
from code_assist_v1 import notion_legacy_incarnation_migration as v2
from code_assist_v1.notion_legacy_change_consumer_contract import (
    EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM,
    MIGRATION_NAME,
    MIGRATION_VERSION,
    inspect_legacy_change_consumer_contract,
)
from code_assist_v1.notion_legacy_change_consumer_migration import (
    LegacyChangeConsumerMigrationRefused,
    apply_plan,
    build_plan,
)
from code_assist_v1.notion_legacy_changes import (
    create_legacy_owner_integrity_attestation,
    read_legacy_page_incarnation_receipt_v2,
    scan_legacy_page_changes,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    notion_store.create_page("owner", title="migration fixture")
    source = root / "owner" / "notion" / "notion.db"
    v1_plan = v1.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v1.apply_plan(
        v1_plan,
        confirm_checksum=v1_plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )
    v2_plan = v2.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v2.apply_plan(
        v2_plan,
        confirm_checksum=v2_plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )
    return root, source, backups


def test_v3_plan_is_deterministic_read_only_and_frozen(tmp_path, monkeypatch):
    root, source, _backups = _source_v2(tmp_path, monkeypatch)
    before = _sha256(source)
    first = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    second = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert first == second
    assert _sha256(source) == before
    assert first["action"] == "migrate_v2_to_v3"
    assert first["migration_version"] == MIGRATION_VERSION
    assert first["migration_name"] == MIGRATION_NAME
    assert first["migration_checksum"] == MIGRATION_CHECKSUM
    assert first["expected_object_hash"] == EXPECTED_OBJECT_HASH
    assert first["destructive_ack"] is False


def test_v3_apply_preserves_watermarks_and_is_idempotent(tmp_path, monkeypatch):
    root, source, backups = _source_v2(tmp_path, monkeypatch)
    before_con = sqlite3.connect(source)
    before = before_con.execute(
        "SELECT page_id,change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks ORDER BY page_id"
    ).fetchall()
    before_con.close()
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    result = apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v3.db",
    )
    assert result["status"] == "applied"
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    assert inspect_legacy_change_consumer_contract(con)["installed"] is True
    assert [
        tuple(row)
        for row in con.execute(
            "SELECT page_id,change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks ORDER BY page_id"
        ).fetchall()
    ] == before
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_work"
    ).fetchone()[0] == 0
    page_id = str(con.execute(
        "SELECT page_id FROM notion_page_change_watermarks ORDER BY page_id LIMIT 1"
    ).fetchone()[0])
    con.close()
    batch = scan_legacy_page_changes(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
        limit=32,
    )
    assert batch.scanned_count == 1
    receipt = read_legacy_page_incarnation_receipt_v2(
        user_data_root=root,
        source_owner_id="owner",
        page_id=page_id,
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert receipt.page_id == page_id
    attestation = create_legacy_owner_integrity_attestation(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    assert attestation.object_hash == EXPECTED_OBJECT_HASH
    current = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    assert current["action"] == "already_current"
    replay = apply_plan(
        current,
        confirm_checksum=current["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "unused-current.db",
    )
    assert replay["status"] == "already_current"
    assert not (backups / "unused-current.db").exists()


@pytest.mark.parametrize(
    ("quiesced", "backup_confirmed", "checksum"),
    [
        (False, True, "valid"),
        (True, False, "valid"),
        (True, True, "invalid"),
    ],
)
def test_v3_apply_requires_exact_operator_approval(
    tmp_path, monkeypatch, quiesced, backup_confirmed, checksum
):
    root, source, backups = _source_v2(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    before = _sha256(source)
    with pytest.raises(LegacyChangeConsumerMigrationRefused):
        apply_plan(
            plan,
            confirm_checksum=(
                plan["plan_checksum"] if checksum == "valid" else "sha256:" + "0" * 64
            ),
            source_quiesced_confirmed=quiesced,
            backup_confirmed=backup_confirmed,
            expected_backup_path=backups / "refused.db",
        )
    assert _sha256(source) == before


def test_v3_image_fault_keeps_v2_source_and_verified_backup(tmp_path, monkeypatch):
    root, source, backups = _source_v2(tmp_path, monkeypatch)
    plan = build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    before = _sha256(source)

    def fault(phase: str) -> None:
        if phase == "after_schema":
            raise RuntimeError("injected")

    backup = backups / "v3-fault.db"
    with pytest.raises(LegacyChangeConsumerMigrationRefused):
        apply_plan(
            plan,
            confirm_checksum=plan["plan_checksum"],
            source_quiesced_confirmed=True,
            backup_confirmed=True,
            expected_backup_path=backup,
            fault_injector=fault,
        )
    assert _sha256(source) == before
    assert backup.exists()
    con = sqlite3.connect(source)
    con.row_factory = sqlite3.Row
    assert inspect_legacy_change_consumer_contract(con)["installed"] is False
    con.close()


def test_v3_refuses_a_v1_only_source(tmp_path, monkeypatch):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    notion_store.create_page("owner", title="v1 only")
    source = root / "owner" / "notion" / "notion.db"
    plan = v1.build_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v1.apply_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )
    with pytest.raises(LegacyChangeConsumerMigrationRefused):
        build_plan(
            user_data_root=root,
            source_owner_id="owner",
            expected_source_db_path=source,
        )


def test_v3_imports_are_app_isolated_and_filesystem_side_effect_free(tmp_path):
    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = tuple(probe.iterdir())
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    modules = (
        "code_assist_v1.notion_legacy_change_consumer_contract",
        "code_assist_v1.notion_legacy_change_consumer",
        "code_assist_v1.notion_legacy_change_consumer_migration",
        "code_assist_v1.tools.migrate_notion_legacy_change_consumer",
    )
    script = (
        "import importlib,json,sys; "
        f"mods={modules!r}; [importlib.import_module(m) for m in mods]; "
        "names=('code_assist_v1.app_code','code_assist_v1.app_notion',"
        "'code_assist_v1.auth','code_assist_v1.config','code_assist_v1.data_paths',"
        "'code_assist_v1.notion_store'); "
        "print(json.dumps(sorted(n for n in names if n in sys.modules)))"
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
