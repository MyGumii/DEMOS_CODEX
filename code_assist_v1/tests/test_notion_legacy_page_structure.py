from __future__ import annotations

import json
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
from code_assist_v1.notion_legacy_page_structure import (
    LegacyPageStructureOperationConflict,
    LegacyPageStructureSourceAdapter,
    LegacyPageStructureSourceConflict,
)
from code_assist_v1.notion_legacy_page_structure_contract import (
    CREATE_ACTION,
    MOVE_ACTION,
    REORDER_ACTION,
    LegacyPageStructureContractError,
    SOURCE_CONSUMER,
    derive_structure_source_operation_id,
    normalize_create_payload,
)


def _apply_v1(root: Path, source: Path, backup: Path) -> None:
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
        expected_backup_path=backup,
    )


def _apply_v2(root: Path, source: Path, backup: Path) -> None:
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
        expected_backup_path=backup,
    )


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_tree: bool = False,
):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    pages: dict[str, dict[str, object]] = {}
    pages["parent_a"] = notion_store.create_page("owner", title="Parent A")["page"]
    if with_tree:
        pages["parent_b"] = notion_store.create_page("owner", title="Parent B")["page"]
        for key, title in (("child_a", "Child A"), ("child_b", "Child B"), ("child_c", "Child C")):
            pages[key] = notion_store.create_page(
                "owner",
                title=title,
                parent_id=str(pages["parent_a"]["id"]),
                add_to_parent_blocks=True,
            )["page"]
    source = root / "owner" / "notion" / "notion.db"
    _apply_v1(root, source, backups / "before-v1.db")
    _apply_v2(root, source, backups / "before-v2.db")
    con = notion_store._configure_connection(
        sqlite3.connect(source), query_only=False
    )
    install_schema_for_migration(con)
    con.execute(
        "INSERT INTO notion_legacy_schema_migrations("
        "version,name,checksum,actual_schema_sha256,applied_at"
        ") VALUES(?,?,?,?,?)",
        (
            MIGRATION_VERSION,
            MIGRATION_NAME,
            MIGRATION_CHECKSUM,
            EXPECTED_OBJECT_HASH,
            10,
        ),
    )
    con.commit()
    maximum_updated_at = int(
        con.execute("SELECT COALESCE(MAX(updated_at),0) FROM pages").fetchone()[0]
    )
    clock = {"now": max(maximum_updated_at + 10, 2_000_000_000)}
    adapter = LegacyPageStructureSourceAdapter(
        con,
        source_owner_id="owner",
        expected_source_db_path=source,
        trusted_clock=lambda: clock["now"],
    )
    return adapter, con, clock, source, pages


def _child_reference_ids(con: sqlite3.Connection, parent_id: str) -> list[str]:
    rows = con.execute(
        "SELECT block_type,props_json FROM blocks WHERE page_id=? ORDER BY position",
        (parent_id,),
    ).fetchall()
    output: list[str] = []
    for row in rows:
        if str(row["block_type"]) != "child_page":
            continue
        props = json.loads(str(row["props_json"] or "{}"))
        output.append(str(props.get("page_id") or ""))
    return output


def test_create_commits_exact_once_and_lost_response_replays_before_page_read(
    tmp_path, monkeypatch
):
    adapter, con, _clock, _source, pages = _fixture(tmp_path, monkeypatch)
    parent_id = str(pages["parent_a"]["id"])
    before = adapter.capture_create_prestate(
        workspace_id="ws_create",
        mutation_actor_subject_id="owner",
        caller_operation_id="caller.create.1",
        parent_page_id=parent_id,
        source_quiesced_confirmed=True,
    )
    payload = normalize_create_payload(
        title="Confidential create title",
        blocks=[{"type": "paragraph", "content": "secret body", "props": {}}],
        add_to_parent_blocks=True,
    )

    def lose_response(phase: str) -> None:
        if phase == "after_commit":
            raise RuntimeError("simulated response loss")

    with pytest.raises(RuntimeError, match="simulated response loss"):
        adapter.create(
            workspace_id="ws_create",
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.create.1",
            caller_operation_id="caller.create.1",
            expected_prestate=before,
            create_payload=payload,
            source_quiesced_confirmed=True,
            fault_hook=lose_response,
        )
    assert con.execute(
        "SELECT COUNT(*) FROM pages WHERE id=?", (before.target_page_id,)
    ).fetchone()[0] == 1
    assert before.target_page_id in _child_reference_ids(con, parent_id)

    reads: list[str] = []

    def authorizer(action, arg1, _arg2, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            reads.append(str(arg1))
            if str(arg1) == "pages":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(authorizer)
    replay = adapter.create(
        workspace_id="ws_create",
        mutation_actor_subject_id="owner",
        catalog_command_id="cmd.create.1",
        caller_operation_id="caller.create.1",
        expected_prestate=before,
        create_payload=payload,
        source_quiesced_confirmed=False,
    )
    assert replay.action == CREATE_ACTION
    assert "pages" not in reads
    with pytest.raises(LegacyPageStructureOperationConflict):
        adapter.create(
            workspace_id="ws_create",
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.create.1",
            caller_operation_id="caller.create.1",
            expected_prestate=before,
            create_payload=normalize_create_payload(title="different secret"),
            source_quiesced_confirmed=False,
        )
    con.set_authorizer(None)

    operation_id = derive_structure_source_operation_id(
        action=CREATE_ACTION,
        catalog_command_id="cmd.create.1",
        caller_operation_id="caller.create.1",
    )
    ledger = con.execute(
        "SELECT result_json FROM notion_legacy_change_consumer_operations "
        "WHERE consumer=? AND operation_id=?",
        (SOURCE_CONSUMER, operation_id),
    ).fetchone()
    assert "Confidential create title" not in str(ledger["result_json"])
    assert "secret body" not in str(ledger["result_json"])
    assert replay.descriptor() == json.loads(str(ledger["result_json"]))
    con.close()


def test_create_fault_quiescence_and_root_permission_are_write_free(
    tmp_path, monkeypatch
):
    adapter, con, _clock, _source, _pages = _fixture(tmp_path, monkeypatch)
    before = adapter.capture_create_prestate(
        workspace_id="ws_fault",
        mutation_actor_subject_id="owner",
        caller_operation_id="caller.create.fault",
        source_quiesced_confirmed=True,
    )
    count_before = int(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0])

    with pytest.raises(ValueError, match="quiescence"):
        adapter.create(
            workspace_id="ws_fault",
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.create.quiescence",
            caller_operation_id="caller.create.fault",
            expected_prestate=before,
            create_payload=normalize_create_payload(title="not created"),
            source_quiesced_confirmed=False,
        )
    with pytest.raises(RuntimeError, match="operation fault"):
        adapter.create(
            workspace_id="ws_fault",
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.create.fault",
            caller_operation_id="caller.create.fault",
            expected_prestate=before,
            create_payload=normalize_create_payload(title="not created"),
            source_quiesced_confirmed=True,
            fault_hook=lambda phase: (_ for _ in ()).throw(
                RuntimeError("operation fault")
            ) if phase == "before_operation" else None,
        )
    outsider = adapter.capture_create_prestate(
        workspace_id="ws_outsider",
        mutation_actor_subject_id="outsider",
        caller_operation_id="caller.create.outsider",
        source_quiesced_confirmed=True,
    )
    with pytest.raises(PermissionError, match="owner permission"):
        adapter.create(
            workspace_id="ws_outsider",
            mutation_actor_subject_id="outsider",
            catalog_command_id="cmd.create.outsider",
            caller_operation_id="caller.create.outsider",
            expected_prestate=outsider,
            create_payload=normalize_create_payload(title="not created"),
            source_quiesced_confirmed=True,
        )
    assert int(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]) == count_before
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations"
    ).fetchone()[0] == 0
    con.close()


def test_cross_parent_move_updates_dense_scope_references_and_replays(
    tmp_path, monkeypatch
):
    adapter, con, _clock, _source, pages = _fixture(
        tmp_path, monkeypatch, with_tree=True
    )
    target_id = str(pages["child_b"]["id"])
    old_parent = str(pages["parent_a"]["id"])
    new_parent = str(pages["parent_b"]["id"])
    before = adapter.capture_move_prestate(
        page_id=target_id,
        parent_page_id=new_parent,
        source_quiesced_confirmed=True,
    )
    with pytest.raises(RuntimeError, match="simulated move response loss"):
        adapter.move_or_reorder(
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.move.1",
            caller_operation_id="caller.move.1",
            expected_prestate=before,
            source_quiesced_confirmed=True,
            fault_hook=lambda phase: (_ for _ in ()).throw(
                RuntimeError("simulated move response loss")
            ) if phase == "after_commit" else None,
        )
    moved = con.execute(
        "SELECT parent_id,position FROM pages WHERE id=?", (target_id,)
    ).fetchone()
    assert (str(moved["parent_id"]), int(moved["position"])) == (new_parent, 1)
    assert target_id not in _child_reference_ids(con, old_parent)
    assert target_id in _child_reference_ids(con, new_parent)
    destination = [
        str(row[0])
        for row in con.execute(
            "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position",
            (new_parent,),
        )
    ]
    assert destination == [target_id]

    reads: list[str] = []
    con.set_authorizer(
        lambda action, arg1, _arg2, _database, _trigger: (
            reads.append(str(arg1)) or sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and str(arg1) == "pages"
            else sqlite3.SQLITE_OK
        )
    )
    replay = adapter.move_or_reorder(
        mutation_actor_subject_id="owner",
        catalog_command_id="cmd.move.1",
        caller_operation_id="caller.move.1",
        expected_prestate=before,
        source_quiesced_confirmed=False,
    )
    assert replay.action == MOVE_ACTION
    assert replay.structure_changed is True
    assert "pages" not in reads
    con.set_authorizer(None)
    con.close()


def test_reorder_and_structure_equal_append_are_truthfully_receipted(
    tmp_path, monkeypatch
):
    adapter, con, clock, _source, pages = _fixture(
        tmp_path, monkeypatch, with_tree=True
    )
    parent_id = str(pages["parent_a"]["id"])
    child_a = str(pages["child_a"]["id"])
    child_b = str(pages["child_b"]["id"])
    child_c = str(pages["child_c"]["id"])
    before = adapter.capture_move_prestate(
        page_id=child_c,
        before_page_id=child_a,
        source_quiesced_confirmed=True,
    )
    result = adapter.move_or_reorder(
        mutation_actor_subject_id="owner",
        catalog_command_id="cmd.reorder.1",
        caller_operation_id="caller.reorder.1",
        expected_prestate=before,
        source_quiesced_confirmed=True,
    )
    assert result.action == REORDER_ACTION
    assert result.structure_changed is True
    assert [
        str(row[0])
        for row in con.execute(
            "SELECT id FROM pages WHERE parent_id=? AND archived=0 ORDER BY position",
            (parent_id,),
        )
    ] == [child_c, child_a, child_b]
    assert _child_reference_ids(con, parent_id) == [child_c, child_a, child_b]

    clock["now"] += 10
    equal_before = adapter.capture_move_prestate(
        page_id=child_b,
        parent_page_id=parent_id,
        source_quiesced_confirmed=True,
    )
    equal_result = adapter.move_or_reorder(
        mutation_actor_subject_id="owner",
        catalog_command_id="cmd.reorder.equal",
        caller_operation_id="caller.reorder.equal",
        expected_prestate=equal_before,
        source_quiesced_confirmed=True,
    )
    assert equal_result.action == REORDER_ACTION
    assert equal_result.source_changed is True
    assert equal_result.structure_changed is False
    con.close()


def test_stale_prestate_cycle_and_corrupt_replay_fail_closed(tmp_path, monkeypatch):
    adapter, con, clock, _source, pages = _fixture(
        tmp_path, monkeypatch, with_tree=True
    )
    target_id = str(pages["child_a"]["id"])
    before = adapter.capture_move_prestate(
        page_id=target_id,
        parent_page_id=str(pages["parent_b"]["id"]),
        source_quiesced_confirmed=True,
    )
    con.execute("UPDATE pages SET title='drifted' WHERE id=?", (target_id,))
    con.commit()
    with pytest.raises(LegacyPageStructureSourceConflict, match="prestate changed"):
        adapter.move_or_reorder(
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.move.stale",
            caller_operation_id="caller.move.stale",
            expected_prestate=before,
            source_quiesced_confirmed=True,
        )
    assert con.execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations"
    ).fetchone()[0] == 0
    with pytest.raises(ValueError, match="descendant"):
        adapter.capture_move_prestate(
            page_id=str(pages["parent_a"]["id"]),
            parent_page_id=str(pages["child_b"]["id"]),
            source_quiesced_confirmed=True,
        )

    clock["now"] += 10
    create_before = adapter.capture_create_prestate(
        workspace_id="ws_corrupt",
        mutation_actor_subject_id="owner",
        caller_operation_id="caller.create.corrupt",
        source_quiesced_confirmed=True,
    )
    result = adapter.create(
        workspace_id="ws_corrupt",
        mutation_actor_subject_id="owner",
        catalog_command_id="cmd.create.corrupt",
        caller_operation_id="caller.create.corrupt",
        expected_prestate=create_before,
        create_payload=normalize_create_payload(title="valid title"),
        source_quiesced_confirmed=True,
    )
    operation_id = derive_structure_source_operation_id(
        action=CREATE_ACTION,
        catalog_command_id="cmd.create.corrupt",
        caller_operation_id="caller.create.corrupt",
    )
    con.execute("DROP TRIGGER trg_notion_change_consumer_operation_update")
    broken = result.descriptor()
    broken["unexpected"] = 1
    con.execute(
        "UPDATE notion_legacy_change_consumer_operations SET result_json=? "
        "WHERE consumer=? AND operation_id=?",
        (
            json.dumps(broken, sort_keys=True, separators=(",", ":")),
            SOURCE_CONSUMER,
            operation_id,
        ),
    )
    con.commit()
    with pytest.raises(LegacyPageStructureContractError, match="receipt is invalid"):
        adapter.create(
            workspace_id="ws_corrupt",
            mutation_actor_subject_id="owner",
            catalog_command_id="cmd.create.corrupt",
            caller_operation_id="caller.create.corrupt",
            expected_prestate=create_before,
            create_payload=normalize_create_payload(title="valid title"),
            source_quiesced_confirmed=False,
        )
    con.close()


def test_structure_source_modules_import_without_app_or_filesystem_side_effects():
    script = r'''
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
import code_assist_v1.notion_legacy_page_structure_contract  # noqa: F401
import code_assist_v1.notion_legacy_page_structure  # noqa: F401
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
    assert json.loads(completed.stdout.strip()) == {
        "forbidden": [],
        "changed": False,
    }
