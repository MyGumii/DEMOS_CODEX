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
from code_assist_v1.notion_legacy_page_duplicate_trash_restore import (
    LegacyPageDuplicateTrashRestoreSourceAdapter,
    LegacyPageLifecycleManualAuditRequired,
    LegacyPageLifecycleSourceConflict,
    LegacyPageLifecycleUnsafePath,
)
from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    LegacyPageLifecycleContractError,
    derive_lifecycle_source_operation_id,
)
from code_assist_v1.notion_legacy_page_lifecycle import (
    LegacyPageRenameOperationConflict,
)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    backups = user_data / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(user_data))

    root_a = notion_store.create_page("owner", title="Root A")["page"]
    child_active = notion_store.create_page(
        "owner", title="Child active", parent_id=root_a["id"]
    )["page"]
    child_pretrashed = notion_store.create_page(
        "owner", title="Child already trashed", parent_id=root_a["id"]
    )["page"]
    root_b = notion_store.create_page("owner", title="Root B")["page"]
    uploaded = notion_store.upload_file(
        "owner",
        child_active["id"],
        filename="evidence.txt",
        raw=b"np2c duplicate bytes",
        insert_block=True,
    )
    notion_store.archive_page("owner", child_pretrashed["id"])

    workspace_root = user_data / "owner" / "notion"
    source = workspace_root / "notion.db"
    plan_v1 = v1_migration.build_plan(
        user_data_root=user_data,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v1_migration.apply_plan(
        plan_v1,
        confirm_checksum=plan_v1["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )
    plan_v2 = v2_migration.build_plan(
        user_data_root=user_data,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    v2_migration.apply_plan(
        plan_v2,
        confirm_checksum=plan_v2["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )
    con = notion_store._configure_connection(sqlite3.connect(source), query_only=False)
    install_schema_for_migration(con)
    con.execute(
        "INSERT INTO notion_legacy_schema_migrations("
        "version,name,checksum,actual_schema_sha256,applied_at) VALUES(?,?,?,?,?)",
        (MIGRATION_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM, EXPECTED_OBJECT_HASH, 10),
    )
    con.commit()
    latest = int(con.execute("SELECT MAX(updated_at) FROM pages").fetchone()[0])
    clock = iter(range(max(latest + 10, 2_000_000_000), 2_000_100_000))
    adapter = LegacyPageDuplicateTrashRestoreSourceAdapter(
        con,
        source_owner_id="owner",
        expected_source_db_path=source,
        expected_source_workspace_root=workspace_root,
        trusted_clock=lambda: next(clock),
    )
    return {
        "adapter": adapter,
        "con": con,
        "workspace_root": workspace_root,
        "root_a": root_a["id"],
        "child_active": child_active["id"],
        "child_pretrashed": child_pretrashed["id"],
        "root_b": root_b["id"],
        "file_id": uploaded["id"],
    }


def _apply(
    fixture: dict,
    *,
    action: str,
    root: str,
    tag: str,
    trash_source_operation_id: str = "",
    placement_mode: str = "",
    fallback_parent: str = "",
    relative: str = "",
    relative_position: str = "",
    fault_hook=None,
):
    adapter = fixture["adapter"]
    command = "plc_" + tag * 32
    caller = "source-" + tag
    expected = adapter.capture_precondition(
        action=action,
        mutation_actor_subject_id="owner",
        root_source_page_id=root,
        catalog_command_id=command,
        caller_operation_id=caller,
        source_quiesced_confirmed=True,
        trash_source_operation_id=trash_source_operation_id,
        placement_mode=placement_mode,
        fallback_parent_source_page_id=fallback_parent,
        relative_source_page_id=relative,
        relative_position=relative_position,
    )
    receipt = adapter.apply_lifecycle(
        action=action,
        mutation_actor_subject_id="owner",
        root_source_page_id=root,
        catalog_command_id=command,
        caller_operation_id=caller,
        expected_precondition_sha256=expected,
        source_quiesced_confirmed=True,
        trash_source_operation_id=trash_source_operation_id,
        placement_mode=placement_mode,
        fallback_parent_source_page_id=fallback_parent,
        relative_source_page_id=relative,
        relative_position=relative_position,
        fault_hook=fault_hook,
    )
    source_operation_id = derive_lifecycle_source_operation_id(
        action=action,
        catalog_command_id=command,
        caller_operation_id=caller,
    )
    return receipt, expected, source_operation_id, command, caller


def test_duplicate_is_deterministic_copies_bytes_and_replays_before_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    receipt, expected, _operation, command, caller = _apply(
        fx, action="duplicate", root=fx["root_a"], tag="1"
    )
    assert receipt.action == "duplicate"
    assert receipt.result_root_source_page_id != fx["root_a"]
    copied = [effect for effect in receipt.effects if effect.transitioned]
    assert len(copied) == 2
    assert all(
        (effect.before_lifecycle, effect.after_lifecycle) == ("absent", "active")
        for effect in copied
    )
    page_map = {
        mapping.source_object_id: mapping.result_object_id
        for mapping in receipt.mappings
        if mapping.kind == "page"
    }
    file_mapping = next(
        mapping for mapping in receipt.mappings if mapping.kind == "file"
    )
    assert file_mapping.source_object_id == fx["file_id"]
    file_row = fx["con"].execute(
        "SELECT rel_path,size FROM files WHERE id=?",
        (file_mapping.result_object_id,),
    ).fetchone()
    copied_path = fx["workspace_root"].joinpath(
        *Path(str(file_row["rel_path"]).replace("/", os.sep)).parts
    )
    assert copied_path.read_bytes() == b"np2c duplicate bytes"
    assert int(file_row["size"]) == len(b"np2c duplicate bytes")
    roots = [
        str(row[0])
        for row in fx["con"].execute(
            "SELECT id FROM pages WHERE archived=0 AND parent_id IS NULL "
            "ORDER BY position,updated_at DESC,id"
        )
    ]
    assert roots.index(receipt.result_root_source_page_id) == roots.index(fx["root_a"]) + 1
    assert fx["con"].execute(
        "SELECT parent_id FROM pages WHERE id=?", (page_map[fx["child_active"]],)
    ).fetchone()[0] == receipt.result_root_source_page_id

    monkeypatch.setattr(
        fx["adapter"],
        "_verify_source_file_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file I/O")),
    )
    replay = fx["adapter"].apply_lifecycle(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        expected_precondition_sha256=expected,
        source_quiesced_confirmed=False,
    )
    assert replay == receipt
    fx["con"].close()


def test_trash_records_only_exact_active_cohort_and_restore_preserves_prior_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    trash, _expected, trash_operation, _command, _caller = _apply(
        fx, action="trash", root=fx["root_a"], tag="2"
    )
    transitioned = {
        effect.result_page_id for effect in trash.effects if effect.transitioned
    }
    assert transitioned == {fx["root_a"], fx["child_active"]}
    assert fx["child_pretrashed"] not in transitioned
    assert {
        str(row["id"]): int(row["archived"])
        for row in fx["con"].execute(
            "SELECT id,archived FROM pages WHERE id IN (?,?,?)",
            (fx["root_a"], fx["child_active"], fx["child_pretrashed"]),
        )
    } == {
        fx["root_a"]: 1,
        fx["child_active"]: 1,
        fx["child_pretrashed"]: 1,
    }

    restored, _expected, _operation, _command, _caller = _apply(
        fx,
        action="restore",
        root=fx["root_a"],
        tag="3",
        trash_source_operation_id=trash_operation,
        placement_mode="original",
    )
    assert restored.trash_source_operation_id == trash_operation
    assert {
        effect.result_page_id
        for effect in restored.effects
        if effect.transitioned
    } == transitioned
    rows = {
        str(row["id"]): int(row["archived"])
        for row in fx["con"].execute(
            "SELECT id,archived FROM pages WHERE id IN (?,?,?)",
            (fx["root_a"], fx["child_active"], fx["child_pretrashed"]),
        )
    }
    assert rows[fx["root_a"]] == 0
    assert rows[fx["child_active"]] == 0
    assert rows[fx["child_pretrashed"]] == 1
    fx["con"].close()


def test_reviewed_fallback_restores_no_ancestor_and_updates_parent_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    child_trash, _expected, child_trash_operation, _command, _caller = _apply(
        fx, action="trash", root=fx["child_active"], tag="4"
    )
    assert any(effect.transitioned for effect in child_trash.effects)
    _apply(fx, action="trash", root=fx["root_a"], tag="5")
    restored, _expected, _operation, _command, _caller = _apply(
        fx,
        action="restore",
        root=fx["child_active"],
        tag="6",
        trash_source_operation_id=child_trash_operation,
        placement_mode="reviewed_fallback",
        fallback_parent=fx["root_b"],
    )
    assert restored.placement_mode == "reviewed_fallback"
    child = fx["con"].execute(
        "SELECT archived,parent_id FROM pages WHERE id=?", (fx["child_active"],)
    ).fetchone()
    parent = fx["con"].execute(
        "SELECT archived FROM pages WHERE id=?", (fx["root_a"],)
    ).fetchone()
    assert tuple(child) == (0, fx["root_b"])
    assert int(parent["archived"]) == 1
    reference = fx["con"].execute(
        "SELECT 1 FROM blocks WHERE page_id=? AND block_type='child_page' "
        "AND json_extract(props_json,'$.page_id')=?",
        (fx["root_b"], fx["child_active"]),
    ).fetchone()
    assert reference is not None
    fx["con"].close()


def test_duplicate_file_publish_failure_rolls_back_rows_and_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    adapter = fx["adapter"]
    command = "plc_" + "7" * 32
    caller = "source-7"
    expected = adapter.capture_precondition(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        source_quiesced_confirmed=True,
    )

    def fail(phase: str) -> None:
        if phase == "after_source_mutation":
            raise RuntimeError("fault")

    with pytest.raises(RuntimeError, match="fault"):
        adapter.apply_lifecycle(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id=command,
            caller_operation_id=caller,
            expected_precondition_sha256=expected,
            source_quiesced_confirmed=True,
            fault_hook=fail,
        )
    operation = derive_lifecycle_source_operation_id(
        action="duplicate",
        catalog_command_id=command,
        caller_operation_id=caller,
    )
    assert fx["con"].execute(
        "SELECT 1 FROM notion_legacy_change_consumer_operations "
        "WHERE consumer='np2c.lifecycle' AND operation_id=?",
        (operation,),
    ).fetchone() is None
    assert fx["con"].execute(
        "SELECT COUNT(*) FROM pages WHERE title LIKE '% copy'"
    ).fetchone()[0] == 0
    assert not any(
        path.name.startswith("p_")
        for path in (fx["workspace_root"] / "files").iterdir()
        if path.is_dir() and path.name not in {fx["root_a"], fx["child_active"]}
    )
    fx["con"].close()


def test_duplicate_rejects_escaped_or_linked_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    fx["con"].execute(
        "UPDATE files SET rel_path='../outside.bin' WHERE id=?", (fx["file_id"],)
    )
    fx["con"].commit()
    with pytest.raises(LegacyPageLifecycleUnsafePath):
        fx["adapter"].capture_precondition(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id="plc_" + "8" * 32,
            caller_operation_id="source-8",
            source_quiesced_confirmed=True,
        )
    fx["con"].close()


def test_duplicate_commit_response_loss_replays_without_source_or_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    adapter = fx["adapter"]
    command = "plc_" + "9" * 32
    caller = "source-9"
    expected = adapter.capture_precondition(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        source_quiesced_confirmed=True,
    )

    def response_lost(phase: str) -> None:
        if phase == "after_commit":
            raise RuntimeError("response lost")

    with pytest.raises(RuntimeError, match="response lost"):
        adapter.apply_lifecycle(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id=command,
            caller_operation_id=caller,
            expected_precondition_sha256=expected,
            source_quiesced_confirmed=True,
            fault_hook=response_lost,
        )
    assert fx["con"].execute(
        "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
        "WHERE consumer='np2c.lifecycle' AND action='legacy.page_lifecycle.duplicate'"
    ).fetchone()[0] == 1
    monkeypatch.setattr(
        adapter,
        "_build_precondition",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("source I/O")),
    )
    replay = adapter.apply_lifecycle(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        expected_precondition_sha256=expected,
        source_quiesced_confirmed=False,
    )
    assert replay.action == "duplicate"
    assert fx["con"].execute(
        "SELECT COUNT(*) FROM pages WHERE title LIKE '% copy'"
    ).fetchone()[0] == 1
    fx["con"].close()


def test_duplicate_precommit_file_fault_removes_published_bytes_and_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    adapter = fx["adapter"]
    command = "plc_" + "a" * 32
    caller = "source-a"
    expected = adapter.capture_precondition(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        source_quiesced_confirmed=True,
    )

    def stop_after_publish(phase: str) -> None:
        if phase == "after_file_publish":
            raise RuntimeError("precommit stop")

    with pytest.raises(RuntimeError, match="precommit stop"):
        adapter.apply_lifecycle(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id=command,
            caller_operation_id=caller,
            expected_precondition_sha256=expected,
            source_quiesced_confirmed=True,
            fault_hook=stop_after_publish,
        )
    assert fx["con"].execute(
        "SELECT COUNT(*) FROM pages WHERE title LIKE '% copy'"
    ).fetchone()[0] == 0
    assert not (fx["workspace_root"] / ".np2c-lifecycle-staging").exists()
    referenced_dirs = {
        str(row[0])
        for row in fx["con"].execute("SELECT DISTINCT page_id FROM files")
    }
    assert {
        path.name
        for path in (fx["workspace_root"] / "files").iterdir()
        if path.is_dir()
    }.issubset(referenced_dirs)
    fx["con"].close()


def test_duplicate_never_overwrites_mismatched_prepared_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    adapter = fx["adapter"]
    command = "plc_" + "b" * 32
    caller = "source-b"
    expected = adapter.capture_precondition(
        action="duplicate",
        mutation_actor_subject_id="owner",
        root_source_page_id=fx["root_a"],
        catalog_command_id=command,
        caller_operation_id=caller,
        source_quiesced_confirmed=True,
    )
    operation = derive_lifecycle_source_operation_id(
        action="duplicate", catalog_command_id=command, caller_operation_id=caller
    )
    fx["con"].execute("BEGIN")
    prepared = adapter._build_precondition(
        action="duplicate",
        actor="owner",
        root_page_id=fx["root_a"],
        source_operation_id=operation,
        trash_source_operation_id="",
        placement_mode="",
        fallback_parent_source_page_id="",
        relative_source_page_id="",
        relative_position="",
    )
    fx["con"].rollback()
    plan = prepared.file_plans[0]
    destination = fx["workspace_root"].joinpath(
        *Path(plan.result_relative_path.replace("/", os.sep)).parts
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"must not overwrite")
    with pytest.raises(LegacyPageLifecycleManualAuditRequired):
        adapter.apply_lifecycle(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id=command,
            caller_operation_id=caller,
            expected_precondition_sha256=expected,
            source_quiesced_confirmed=True,
        )
    assert destination.read_bytes() == b"must not overwrite"
    assert fx["con"].execute(
        "SELECT 1 FROM files WHERE id=?", (plan.result_file_id,)
    ).fetchone() is None
    fx["con"].close()


def test_duplicate_rejects_external_reference_and_hardlinked_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    block = fx["con"].execute(
        "SELECT id FROM blocks WHERE page_id=? ORDER BY position LIMIT 1",
        (fx["root_a"],),
    ).fetchone()
    fx["con"].execute(
        "UPDATE blocks SET props_json=? WHERE id=?",
        ('{"page_id":"p_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', block["id"]),
    )
    fx["con"].commit()
    with pytest.raises(LegacyPageLifecycleSourceConflict):
        fx["adapter"].capture_precondition(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id="plc_" + "c" * 32,
            caller_operation_id="source-c",
            source_quiesced_confirmed=True,
        )
    fx["con"].execute(
        "UPDATE blocks SET props_json='{}' WHERE id=?", (block["id"],)
    )
    fx["con"].commit()
    rel_path = str(
        fx["con"].execute(
            "SELECT rel_path FROM files WHERE id=?", (fx["file_id"],)
        ).fetchone()[0]
    )
    source = fx["workspace_root"].joinpath(
        *Path(rel_path.replace("/", os.sep)).parts
    )
    alias = source.with_name("linked-alias.bin")
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hard links unavailable on this test filesystem")
    with pytest.raises(LegacyPageLifecycleUnsafePath):
        fx["adapter"].capture_precondition(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id="plc_" + "d" * 32,
            caller_operation_id="source-d",
            source_quiesced_confirmed=True,
        )
    alias.unlink()
    fx["con"].close()


def test_restore_rejects_recreated_incarnation_and_inactive_original_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    _trash, _expected, trash_operation, _command, _caller = _apply(
        fx, action="trash", root=fx["child_active"], tag="e"
    )
    _apply(fx, action="trash", root=fx["root_a"], tag="f")
    with pytest.raises(LegacyPageLifecycleSourceConflict):
        fx["adapter"].capture_precondition(
            action="restore",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["child_active"],
            catalog_command_id="plc_" + "1" * 31 + "0",
            caller_operation_id="source-original-parent-inactive",
            source_quiesced_confirmed=True,
            trash_source_operation_id=trash_operation,
            placement_mode="original",
        )

    fx["adapter"]._incarnation_write_context = 1
    try:
        fx["con"].execute("DELETE FROM pages WHERE id=?", (fx["child_active"],))
        fx["con"].execute(
            "INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,settings_json,"
            "position,archived,archived_at,revision,created_at,updated_at) "
            "VALUES(?,?,NULL,'Recreated','','','{}',1,1,1,1,1,1)",
            (fx["child_active"], "owner"),
        )
        fx["con"].commit()
    finally:
        fx["adapter"]._incarnation_write_context = 0
    with pytest.raises(LegacyPageLifecycleSourceConflict):
        fx["adapter"].capture_precondition(
            action="restore",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["child_active"],
            catalog_command_id="plc_" + "2" * 31 + "0",
            caller_operation_id="source-recreated-incarnation",
            source_quiesced_confirmed=True,
            trash_source_operation_id=trash_operation,
            placement_mode="reviewed_fallback",
            fallback_parent_source_page_id=fx["root_b"],
        )
    fx["con"].close()


def test_new_effect_refuses_without_quiescence_before_precondition_or_file_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    adapter = fx["adapter"]
    monkeypatch.setattr(
        adapter,
        "_build_precondition",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("source I/O")),
    )
    monkeypatch.setattr(
        adapter,
        "_stage_and_publish_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file I/O")),
    )
    with pytest.raises(ValueError, match="quiescence"):
        adapter.apply_lifecycle(
            action="duplicate",
            mutation_actor_subject_id="owner",
            root_source_page_id=fx["root_a"],
            catalog_command_id="plc_" + "3" * 31 + "0",
            caller_operation_id="source-no-quiescence",
            expected_precondition_sha256="sha256:" + "0" * 64,
            source_quiesced_confirmed=False,
        )
    fx["con"].close()


def test_duplicate_nested_page_adds_deterministic_adjacent_parent_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        notion_store.replace_blocks(
            "owner",
            fx["root_a"],
            [
                {
                    "type": "child_page",
                    "content": "Child active",
                    "props": {"page_id": fx["child_active"]},
                }
            ],
        )
        receipt, _expected, _operation, _command, _caller = _apply(
            fx, action="duplicate", root=fx["child_active"], tag="0"
        )
        assert any(
            mapping.kind == "parent_reference_block" for mapping in receipt.mappings
        )
        references = [
            str(row["page_id"])
            for row in fx["con"].execute(
                "SELECT json_extract(props_json,'$.page_id') AS page_id "
                "FROM blocks WHERE page_id=? AND block_type='child_page' ORDER BY position,id",
                (fx["root_a"],),
            )
        ]
        original_index = references.index(fx["child_active"])
        assert references[original_index + 1] == receipt.result_root_source_page_id
        assert fx["con"].execute(
            "SELECT COUNT(*) FROM page_versions WHERE page_id=?", (fx["root_a"],)
        ).fetchone()[0] >= 2
    finally:
        fx["con"].close()


def test_committed_operation_id_conflict_precedes_quiescence_and_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        _receipt, _expected, _operation, command, caller = _apply(
            fx, action="duplicate", root=fx["root_a"], tag="4"
        )
        monkeypatch.setattr(
            fx["adapter"],
            "_stage_and_publish_files",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("file I/O")),
        )
        with pytest.raises(LegacyPageRenameOperationConflict):
            fx["adapter"].apply_lifecycle(
                action="duplicate",
                mutation_actor_subject_id="owner",
                root_source_page_id=fx["root_a"],
                catalog_command_id=command,
                caller_operation_id=caller,
                expected_precondition_sha256="sha256:" + "0" * 64,
                source_quiesced_confirmed=False,
            )
    finally:
        fx["con"].close()


def test_replay_rejects_noncanonical_source_ledger_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        _receipt, expected, operation, command, caller = _apply(
            fx, action="duplicate", root=fx["root_a"], tag="5"
        )
        result_json = str(
            fx["con"].execute(
                "SELECT result_json FROM notion_legacy_change_consumer_operations "
                "WHERE consumer=? AND operation_id=?",
                ("np2c.lifecycle", operation),
            ).fetchone()[0]
        )
        fx["con"].execute("DROP TRIGGER trg_notion_change_consumer_operation_update")
        fx["con"].execute(
            "UPDATE notion_legacy_change_consumer_operations SET result_json=? "
            "WHERE consumer=? AND operation_id=?",
            (result_json + " ", "np2c.lifecycle", operation),
        )
        fx["con"].commit()
        with pytest.raises(LegacyPageLifecycleContractError, match="ledger"):
            fx["adapter"].apply_lifecycle(
                action="duplicate",
                mutation_actor_subject_id="owner",
                root_source_page_id=fx["root_a"],
                catalog_command_id=command,
                caller_operation_id=caller,
                expected_precondition_sha256=expected,
                source_quiesced_confirmed=False,
            )
    finally:
        fx["con"].close()


def test_exact_prepared_file_is_reused_after_indeterminate_precommit_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        before = {
            path.relative_to(fx["workspace_root"]).as_posix()
            for path in fx["workspace_root"].rglob("*")
            if path.is_file()
        }

        def stop_after_publish(phase: str) -> None:
            if phase == "after_file_publish":
                raise LegacyPageLifecycleManualAuditRequired("simulated process stop")

        with pytest.raises(LegacyPageLifecycleManualAuditRequired):
            _apply(
                fx,
                action="duplicate",
                root=fx["root_a"],
                tag="6",
                fault_hook=stop_after_publish,
            )
        prepared = {
            path.relative_to(fx["workspace_root"]).as_posix()
            for path in fx["workspace_root"].rglob("*")
            if path.is_file()
        } - before
        assert len(prepared) == 1
        assert fx["con"].execute(
            "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
            "WHERE consumer='np2c.lifecycle'"
        ).fetchone()[0] == 0

        receipt, _expected, _operation, _command, _caller = _apply(
            fx, action="duplicate", root=fx["root_a"], tag="6"
        )
        assert len([mapping for mapping in receipt.mappings if mapping.kind == "file"]) == 1
        after = {
            path.relative_to(fx["workspace_root"]).as_posix()
            for path in fx["workspace_root"].rglob("*")
            if path.is_file()
        }
        assert prepared <= after
    finally:
        fx["con"].close()


def test_source_file_drift_after_publish_rolls_back_rows_and_exact_new_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        source_relative = str(
            fx["con"].execute(
                "SELECT rel_path FROM files WHERE id=?", (fx["file_id"],)
            ).fetchone()[0]
        )
        source_file = fx["workspace_root"].joinpath(*source_relative.split("/"))
        before = {
            path.relative_to(fx["workspace_root"]).as_posix()
            for path in fx["workspace_root"].rglob("*")
            if path.is_file()
        }
        changed = False

        def drift_after_publish(phase: str) -> None:
            nonlocal changed
            if phase == "after_file_publish" and not changed:
                source_file.write_bytes(b"changed after deterministic publish")
                changed = True

        with pytest.raises(LegacyPageLifecycleSourceConflict):
            _apply(
                fx,
                action="duplicate",
                root=fx["root_a"],
                tag="7",
                fault_hook=drift_after_publish,
            )
        after = {
            path.relative_to(fx["workspace_root"]).as_posix()
            for path in fx["workspace_root"].rglob("*")
            if path.is_file()
        }
        assert after == before
        assert fx["con"].execute(
            "SELECT COUNT(*) FROM notion_legacy_change_consumer_operations "
            "WHERE consumer='np2c.lifecycle'"
        ).fetchone()[0] == 0
    finally:
        fx["con"].close()


def test_locked_source_and_restore_destination_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    try:
        notion_store.update_page("owner", fx["root_a"], {"settings": {"locked": True}})
        with pytest.raises(PermissionError, match="locked"):
            fx["adapter"].capture_precondition(
                action="duplicate",
                mutation_actor_subject_id="owner",
                root_source_page_id=fx["root_a"],
                catalog_command_id="plc_" + "8" * 32,
                caller_operation_id="source-locked-duplicate",
                source_quiesced_confirmed=True,
            )
        notion_store.update_page("owner", fx["root_a"], {"settings": {"locked": False}})
        trash, _expected, trash_operation, _command, _caller = _apply(
            fx, action="trash", root=fx["child_active"], tag="8"
        )
        assert trash.action == "trash"
        notion_store.update_page("owner", fx["root_b"], {"settings": {"locked": True}})
        with pytest.raises(PermissionError, match="locked"):
            fx["adapter"].capture_precondition(
                action="restore",
                mutation_actor_subject_id="owner",
                root_source_page_id=fx["child_active"],
                catalog_command_id="plc_" + "9" * 32,
                caller_operation_id="source-locked-restore",
                source_quiesced_confirmed=True,
                trash_source_operation_id=trash_operation,
                placement_mode="reviewed_fallback",
                fallback_parent_source_page_id=fx["root_b"],
            )
    finally:
        fx["con"].close()


def test_lifecycle_modules_import_without_app_or_filesystem_side_effects() -> None:
    root = Path(__file__).resolve().parents[2]

    def file_inventory() -> list[str]:
        return sorted(
            str((Path(directory) / name).relative_to(root))
            for directory, _directories, names in os.walk(root)
            for name in names
        )

    before = file_inventory()
    script = r'''
import json
import sys

import code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract  # noqa: F401
import code_assist_v1.notion_legacy_page_duplicate_trash_restore  # noqa: F401
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
print(json.dumps({"forbidden": forbidden}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    after = file_inventory()
    assert json.loads(completed.stdout.strip()) == {"forbidden": []}
    assert after == before
