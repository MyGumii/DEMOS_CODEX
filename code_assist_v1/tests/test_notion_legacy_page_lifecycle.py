from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_legacy_change_migration as v1_migration
from code_assist_v1 import notion_legacy_incarnation_migration as v2_migration
from code_assist_v1.notion_legacy_change_consumer_contract import (
    EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM,
    MIGRATION_NAME,
    MIGRATION_VERSION,
    install_schema_for_migration,
)
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameBoundsExceeded,
    LegacyPageRenameOperationConflict,
    LegacyPageRenameSourceAdapter,
    LegacyPageRenameSourceConflict,
)
from code_assist_v1.notion_legacy_page_lifecycle_contract import (
    LegacyPageRenameContractError,
    RENAME_ACTION,
    SOURCE_CONSUMER,
    derive_rename_source_operation_id,
)


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
    page = notion_store.create_page("owner", title="Initial title")["page"]
    source = root / "owner" / "notion" / "notion.db"
    _apply_v1(root, source, backups)
    _apply_v2(root, source, backups)
    con = notion_store._configure_connection(sqlite3.connect(source), query_only=False)
    install_schema_for_migration(con)
    con.execute(
        "INSERT INTO notion_legacy_schema_migrations("
        "version,name,checksum,actual_schema_sha256,applied_at"
        ") VALUES(?,?,?,?,?)",
        (MIGRATION_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM, EXPECTED_OBJECT_HASH, 10),
    )
    con.commit()
    current_updated_at = int(
        con.execute("SELECT updated_at FROM pages WHERE id=?", (page["id"],)).fetchone()[0]
    )
    clock = {"now": max(current_updated_at + 10, 2_000_000_000)}
    adapter = LegacyPageRenameSourceAdapter(
        con,
        source_owner_id="owner",
        expected_source_db_path=source,
        trusted_clock=lambda: clock["now"],
    )
    return adapter, con, clock, source, page


def _source_state(con: sqlite3.Connection, page_id: str) -> dict[str, object]:
    page = con.execute(
        "SELECT title,revision,updated_at FROM pages WHERE id=?", (page_id,)
    ).fetchone()
    watermark = con.execute(
        "SELECT change_generation,change_mask,source_exists "
        "FROM notion_page_change_watermarks WHERE page_id=?",
        (page_id,),
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
    }


def test_changed_rename_commits_exact_once_and_response_loss_replays_before_page_read(
    tmp_path, monkeypatch
):
    adapter, con, _clock, _source, page = _fixture(tmp_path, monkeypatch)
    page_id = str(page["id"])
    before = adapter.capture_prestate(
        page_id=page_id, source_quiesced_confirmed=True
    )
    before_versions = int(
        con.execute("SELECT COUNT(*) FROM page_versions WHERE page_id=?", (page_id,)).fetchone()[0]
    )
    phases: list[str] = []

    def lose_response(phase: str) -> None:
        phases.append(phase)
        if phase == "after_commit":
            raise RuntimeError("simulated response loss")

    with pytest.raises(RuntimeError, match="simulated response loss"):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="  Renamed   Secret  ",
            catalog_command_id="wcmd_rename_1",
            caller_operation_id="caller.rename.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
            fault_hook=lose_response,
        )
    assert phases[-1] == "after_commit"
    persisted = _source_state(con, page_id)
    assert persisted == {
        "title": "Renamed Secret",
        "revision": before.revision + 1,
        "updated_at": max(before.updated_at + 10, 2_000_000_000),
        "change_generation": before.change_generation + 1,
        "change_mask": before.change_mask | 1,
        "source_exists": 1,
        "source_epoch": before.source_epoch + 1,
        "full_sweep_generation": before.full_sweep_generation + 1,
    }
    assert int(
        con.execute("SELECT COUNT(*) FROM page_versions WHERE page_id=?", (page_id,)).fetchone()[0]
    ) == before_versions + 1
    assert con.execute(
        "SELECT title FROM page_versions WHERE page_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (page_id,),
    ).fetchone()[0] == "Renamed Secret"

    reads: list[str] = []

    def authorizer(action, arg1, _arg2, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            reads.append(str(arg1))
            if str(arg1) == "pages":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(authorizer)
    replay = adapter.rename(
        mutation_actor_subject_id="owner",
        requested_title="  Renamed   Secret  ",
        catalog_command_id="wcmd_rename_1",
        caller_operation_id="caller.rename.1",
        expected_prestate=before,
        source_quiesced_confirmed=False,
    )
    assert replay.changed is True
    assert "pages" not in reads
    with pytest.raises(LegacyPageRenameOperationConflict):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="different secret title",
            catalog_command_id="wcmd_rename_1",
            caller_operation_id="caller.rename.1",
            expected_prestate=before,
            source_quiesced_confirmed=False,
        )
    assert "pages" not in reads
    con.set_authorizer(None)

    operation_id = derive_rename_source_operation_id(
        catalog_command_id="wcmd_rename_1", caller_operation_id="caller.rename.1"
    )
    ledger = con.execute(
        "SELECT action,request_sha256,result_json FROM "
        "notion_legacy_change_consumer_operations WHERE consumer=? AND operation_id=?",
        (SOURCE_CONSUMER, operation_id),
    ).fetchone()
    assert ledger["action"] == RENAME_ACTION
    assert str(ledger["request_sha256"]).startswith("sha256:")
    assert "Renamed Secret" not in str(ledger["result_json"])
    assert "different secret title" not in str(ledger["result_json"])
    assert replay.descriptor() == json.loads(str(ledger["result_json"]))
    con.close()


def test_exact_title_noop_writes_only_immutable_receipt(tmp_path, monkeypatch):
    adapter, con, _clock, _source, page = _fixture(tmp_path, monkeypatch)
    page_id = str(page["id"])
    before = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    source_before = _source_state(con, page_id)
    versions_before = int(
        con.execute("SELECT COUNT(*) FROM page_versions WHERE page_id=?", (page_id,)).fetchone()[0]
    )
    result = adapter.rename(
        mutation_actor_subject_id="owner",
        requested_title="  Initial    title ",
        catalog_command_id="wcmd_noop_1",
        caller_operation_id="caller.noop.1",
        expected_prestate=before,
        source_quiesced_confirmed=True,
    )
    assert result.changed is False
    assert _source_state(con, page_id) == source_before
    assert int(
        con.execute("SELECT COUNT(*) FROM page_versions WHERE page_id=?", (page_id,)).fetchone()[0]
    ) == versions_before
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
        "WHERE consumer=? AND action=?",
        (SOURCE_CONSUMER, RENAME_ACTION),
    ).fetchone()[0] == 1
    con.close()


def test_faults_stale_prestate_permission_lock_and_quiescence_are_write_free(
    tmp_path, monkeypatch
):
    adapter, con, clock, _source, page = _fixture(tmp_path, monkeypatch)
    page_id = str(page["id"])
    before = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    source_before = _source_state(con, page_id)

    for index, fault_phase in enumerate(
        ("after_page_version", "after_operation_insert"), start=1
    ):
        def fail_at_phase(phase: str, *, expected_phase: str = fault_phase) -> None:
            if phase == expected_phase:
                raise RuntimeError("source transaction fault")

        with pytest.raises(RuntimeError, match="source transaction fault"):
            adapter.rename(
                mutation_actor_subject_id="owner",
                requested_title="rollback secret",
                catalog_command_id=f"wcmd_fault_{index}",
                caller_operation_id=f"caller.fault.{index}",
                expected_prestate=before,
                source_quiesced_confirmed=True,
                fault_hook=fail_at_phase,
            )
        assert _source_state(con, page_id) == source_before
        assert con.execute(
            "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations WHERE consumer=?",
            (SOURCE_CONSUMER,),
        ).fetchone()[0] == 0

    with pytest.raises(PermissionError, match="edit permission required"):
        adapter.rename(
            mutation_actor_subject_id="outsider",
            requested_title="unauthorized secret",
            catalog_command_id="wcmd_permission_1",
            caller_operation_id="caller.permission.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
        )
    assert _source_state(con, page_id) == source_before

    reads: list[str] = []

    def deny_pages(action, arg1, _arg2, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            reads.append(str(arg1))
            if str(arg1) == "pages":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(deny_pages)
    with pytest.raises(ValueError, match="requires source quiescence"):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="quiescence secret",
            catalog_command_id="wcmd_quiescence_1",
            caller_operation_id="caller.quiescence.1",
            expected_prestate=before,
            source_quiesced_confirmed=False,
        )
    assert "pages" not in reads
    con.set_authorizer(None)

    con.execute(
        "UPDATE pages SET title=?,updated_at=?,revision=revision+1 WHERE id=?",
        ("external title", clock["now"] + 1, page_id),
    )
    con.commit()
    with pytest.raises(LegacyPageRenameSourceConflict):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="stale secret",
            catalog_command_id="wcmd_stale_1",
            caller_operation_id="caller.stale.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
        )
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations WHERE consumer=?",
        (SOURCE_CONSUMER,),
    ).fetchone()[0] == 0

    current = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    con.execute(
        "UPDATE pages SET settings_json=?,updated_at=?,revision=revision+1 WHERE id=?",
        ('{"locked":true}', clock["now"] + 2, page_id),
    )
    con.commit()
    locked = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    with pytest.raises(PermissionError, match="page is locked"):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="locked secret",
            catalog_command_id="wcmd_locked_1",
            caller_operation_id="caller.locked.1",
            expected_prestate=locked,
            source_quiesced_confirmed=True,
        )
    assert locked != current
    con.close()


def test_version_snapshot_bounds_and_corrupt_ledger_fail_closed(
    tmp_path, monkeypatch
):
    import code_assist_v1.notion_legacy_page_lifecycle as lifecycle

    adapter, con, _clock, _source, page = _fixture(tmp_path, monkeypatch)
    page_id = str(page["id"])
    con.execute(
        "INSERT INTO blocks(id,page_id,owner_user_id,block_type,content,props_json,"
        "position,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("b_bound_1", page_id, "owner", "paragraph", "a", "{}", 10, 10, 10),
    )
    con.execute(
        "INSERT INTO blocks(id,page_id,owner_user_id,block_type,content,props_json,"
        "position,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("b_bound_2", page_id, "owner", "paragraph", "b", "{}", 11, 10, 10),
    )
    con.commit()
    before = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    monkeypatch.setattr(lifecycle, "MAX_PAGE_VERSION_BLOCKS", 1)
    with pytest.raises(LegacyPageRenameBoundsExceeded):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="bounded secret",
            catalog_command_id="wcmd_bound_1",
            caller_operation_id="caller.bound.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
        )
    assert con.execute(
        "SELECT title FROM pages WHERE id=?", (page_id,)
    ).fetchone()[0] == "Initial title"
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations WHERE consumer=?",
        (SOURCE_CONSUMER,),
    ).fetchone()[0] == 0

    monkeypatch.setattr(lifecycle, "MAX_PAGE_VERSION_BLOCKS", 10_000)
    result = adapter.rename(
        mutation_actor_subject_id="owner",
        requested_title="valid rename",
        catalog_command_id="wcmd_valid_1",
        caller_operation_id="caller.valid.1",
        expected_prestate=before,
        source_quiesced_confirmed=True,
    )
    operation_id = derive_rename_source_operation_id(
        catalog_command_id="wcmd_valid_1", caller_operation_id="caller.valid.1"
    )
    con.execute("DROP TRIGGER trg_notion_change_consumer_operation_update")
    broken = result.descriptor()
    broken["unexpected"] = 1
    con.execute(
        "UPDATE notion_legacy_change_consumer_operations SET result_json=? "
        "WHERE consumer=? AND operation_id=?",
        (json.dumps(broken, sort_keys=True, separators=(",", ":")), SOURCE_CONSUMER, operation_id),
    )
    con.commit()
    with pytest.raises(LegacyPageRenameContractError, match="receipt is invalid"):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="valid rename",
            catalog_command_id="wcmd_valid_1",
            caller_operation_id="caller.valid.1",
            expected_prestate=before,
            source_quiesced_confirmed=False,
        )
    con.close()


def test_missing_atomic_revision_capability_is_refused_before_source_mutation(
    tmp_path, monkeypatch
):
    adapter, con, _clock, _source, page = _fixture(tmp_path, monkeypatch)
    page_id = str(page["id"])
    before = adapter.capture_prestate(page_id=page_id, source_quiesced_confirmed=True)
    source_before = _source_state(con, page_id)
    con.execute("DROP TABLE page_versions")
    con.commit()
    with pytest.raises(
        LegacyPageRenameContractError,
        match="atomic source revision capability is required",
    ):
        adapter.rename(
            mutation_actor_subject_id="owner",
            requested_title="capability secret",
            catalog_command_id="wcmd_capability_1",
            caller_operation_id="caller.capability.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
        )
    assert _source_state(con, page_id) == source_before
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations WHERE consumer=?",
        (SOURCE_CONSUMER,),
    ).fetchone()[0] == 0
    con.close()


def test_lifecycle_source_modules_import_without_app_or_filesystem_side_effects(tmp_path):
    script = r'''
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
import code_assist_v1.notion_legacy_page_lifecycle_contract  # noqa: F401
import code_assist_v1.notion_legacy_page_lifecycle  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
forbidden = sorted(name for name in sys.modules if name in {
    "code_assist_v1.app_code",
    "code_assist_v1.app_notion",
    "code_assist_v1.auth",
    "code_assist_v1.config",
    "code_assist_v1.notion_store",
    "code_assist_v1.routes_notion_bridge",
    "code_assist_v1.routes_chat_stream",
    "code_assist_v1.knowledge_core",
})
print(json.dumps({"forbidden": forbidden, "changed": before != after}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(completed.stdout.strip())
    assert result == {"forbidden": [], "changed": False}
