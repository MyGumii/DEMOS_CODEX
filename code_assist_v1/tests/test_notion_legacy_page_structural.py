from __future__ import annotations

from pathlib import Path
import sqlite3

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
from code_assist_v1.notion_legacy_page_structural import (
    EFFECT_NEW_PARENT,
    EFFECT_NEW_SCOPE,
    EFFECT_OLD_PARENT,
    EFFECT_OLD_SCOPE,
    EFFECT_SUBTREE,
    EFFECT_TARGET,
    LegacyPageStructuralSourceAdapter,
)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "user_data"
    root.mkdir()
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))
    root_a = notion_store.create_page("owner", title="Root A")["page"]
    child = notion_store.create_page(
        "owner", title="Child", parent_id=root_a["id"]
    )["page"]
    root_b = notion_store.create_page("owner", title="Root B")["page"]
    source = root / "owner" / "notion" / "notion.db"
    plan_v1 = v1_migration.build_plan(
        user_data_root=root,
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
        user_data_root=root,
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
    adapter = LegacyPageStructuralSourceAdapter(
        con,
        source_owner_id="owner",
        expected_source_db_path=source,
        trusted_clock=lambda: max(latest + 10, 2_000_000_000),
    )
    return adapter, con, root_a["id"], child["id"], root_b["id"]


def _apply(adapter, *, action: str, target: str, parent: str = "", relative: str = "", position: str = "", title: str = ""):
    expected = adapter.capture_precondition(
        action=action,
        target_source_page_id=target,
        new_parent_source_page_id=parent,
        relative_source_page_id=relative,
        source_quiesced_confirmed=True,
    )
    return adapter.apply_structural(
        action=action,
        mutation_actor_subject_id="owner",
        target_source_page_id=target,
        new_parent_source_page_id=parent,
        relative_source_page_id=relative,
        relative_position=position,
        requested_title=title,
        catalog_command_id="plc_" + "1" * 32,
        caller_operation_id=f"source-{action}",
        expected_precondition_sha256=expected,
        source_quiesced_confirmed=True,
    ), expected


def test_create_commits_structural_receipt_and_replays_before_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, con, root_a, _child, root_b = _fixture(tmp_path, monkeypatch)
    target = "p_" + "9" * 32
    receipt, expected = _apply(
        adapter, action="create", target=target, title="Created offline"
    )
    assert receipt.action == "create"
    assert receipt.old_scope_count == 0
    assert receipt.new_scope_count == 3
    target_effect = next(
        effect for effect in receipt.effects if effect.source_page_id == target
    )
    assert target_effect.effect_roles == (
        EFFECT_TARGET | EFFECT_NEW_SCOPE | EFFECT_SUBTREE
    )
    assert target_effect.source_mutated is True
    assert con.execute(
        "SELECT title FROM pages WHERE id=?", (target,)
    ).fetchone()[0] == "Created offline"

    replay = adapter.apply_structural(
        action="create",
        mutation_actor_subject_id="owner",
        target_source_page_id=target,
        new_parent_source_page_id="",
        relative_source_page_id="",
        relative_position="",
        requested_title="Created offline",
        catalog_command_id="plc_" + "1" * 32,
        caller_operation_id="source-create",
        expected_precondition_sha256=expected,
        source_quiesced_confirmed=False,
    )
    assert replay == receipt
    assert {root_a, root_b, target}.issubset(
        {effect.source_page_id for effect in receipt.effects}
    )
    con.close()


def test_reorder_records_complete_old_and_new_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, con, root_a, _child, root_b = _fixture(tmp_path, monkeypatch)
    receipt, _expected = _apply(
        adapter,
        action="reorder",
        target=root_b,
        relative=root_a,
        position="before",
    )
    assert receipt.old_scope_count == 2
    assert receipt.new_scope_count == 2
    assert [
        row[0]
        for row in con.execute(
            "SELECT id FROM pages WHERE parent_id IS NULL AND archived=0 "
            "ORDER BY position,updated_at DESC,id"
        )
    ] == [root_b, root_a]
    effects = {effect.source_page_id: effect for effect in receipt.effects}
    assert effects[root_b].effect_roles == (
        EFFECT_TARGET | EFFECT_OLD_SCOPE | EFFECT_NEW_SCOPE | EFFECT_SUBTREE
    )
    assert effects[root_a].effect_roles == (
        EFFECT_OLD_SCOPE | EFFECT_NEW_SCOPE
    )
    assert all(effect.source_mutated for effect in effects.values())
    con.close()


def test_move_records_parent_roles_and_cross_scope_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, con, root_a, child, root_b = _fixture(tmp_path, monkeypatch)
    receipt, _expected = _apply(
        adapter, action="move", target=child, parent=root_b
    )
    assert receipt.old_scope_count == 1
    assert receipt.new_scope_count == 1
    effects = {effect.source_page_id: effect for effect in receipt.effects}
    assert effects[child].effect_roles == (
        EFFECT_TARGET | EFFECT_OLD_SCOPE | EFFECT_NEW_SCOPE | EFFECT_SUBTREE
    )
    assert effects[root_a].effect_roles & EFFECT_OLD_PARENT
    assert effects[root_b].effect_roles & EFFECT_NEW_PARENT
    assert effects[root_a].source_mutated is False
    assert effects[root_b].source_mutated is False
    assert con.execute(
        "SELECT parent_id FROM pages WHERE id=?", (child,)
    ).fetchone()[0] == root_b
    con.close()
