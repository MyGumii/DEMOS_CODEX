"""A template-default migration must leave existing databases empty by default."""

import sqlite3

import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout
from code_assist_v1 import notion_workspace_database_template_default_migration as migration
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _schema, _surface
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration import database_copy
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_default_template_forward_schema_preserves_empty_default(icon_batch_environment):
    store, _workspace_id = icon_batch_environment
    for target, backup in (
        (placement, "before-template-placement.db"),
        (custom_emoji, "before-template-emoji.db"),
        (item_layout, "before-template-layout.db"),
    ):
        _apply(target, store.db_path, store.user_data_root, backup)
    con = sqlite3.connect(store.db_path)
    try:
        before = con.execute(
            "SELECT id,title,row_version FROM workspace_collaboration_databases "
            "ORDER BY id"
        ).fetchall()
        before_templates = con.execute(
            "SELECT id,database_id,name,state FROM workspace_collaboration_database_templates "
            "ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "migration_required"
    _apply(migration, store.db_path, store.user_data_root, "before-template-default.db")
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    con = sqlite3.connect(store.db_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        assert con.execute(
            "SELECT id,title,row_version FROM workspace_collaboration_databases ORDER BY id"
        ).fetchall() == before
        assert con.execute(
            "SELECT id,database_id,name,state FROM workspace_collaboration_database_templates "
            "ORDER BY id"
        ).fetchall() == before_templates
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_template_defaults"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_collaboration_database_template_defaults("
                "database_id,template_id,updated_by_subject_id,created_at,updated_at) "
                "VALUES('database-e1','missing-template','owner',1,1)"
            )
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_default_template_is_versioned_and_applies_to_new_rows(icon_batch_environment):
    old, workspace_id = icon_batch_environment
    for target, backup in (
        (placement, "before-default-command-placement.db"),
        (custom_emoji, "before-default-command-emoji.db"),
        (item_layout, "before-default-command-layout.db"),
        (migration, "before-default-command-template.db"),
    ):
        _apply(target, old.db_path, old.user_data_root, backup)
    store = CollaborativeWorkspaceStore(
        old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory,
    )
    assert store.capabilities()["database_template_default"] is True
    authority = store.iam
    _schema(authority, "database.template.create", "template-default-one",
            {"name": "Default", "body": [{"id": "template-heading", "type": "heading1",
                                          "content": "Default body", "props": {}}],
             "default_values": {"property-e1": "Default title"}},
            "template-default-create")
    selected = _surface(authority, "database.template_default.set", "database-e1",
                       {"template_id": "template-default-one"}, "template-default-select",
                       target_version=0, actor="schema-editor")
    assert selected["target_version"] == 1
    assert authority.read_database("owner", "database-e1")["default_template"] == {
        "template_id": "template-default-one", "version": 1}
    with pytest.raises((PermissionError, NotFoundOrDenied)):
        _surface(authority, "database.template_default.set", "database-e1",
                 {"template_id": None}, "template-default-content-denied",
                 target_version=1, actor="content-editor")
    with pytest.raises(NotFoundOrDenied):
        _surface(authority, "database.template_default.set", "database-e1",
                 {"template_id": "missing-template"}, "template-default-missing",
                 target_version=1, actor="schema-editor")
    with pytest.raises(TargetVersionConflict):
        _surface(authority, "database.template_default.set", "database-e1",
                 {"template_id": None}, "template-default-stale",
                 target_version=0, actor="schema-editor")
    created = _surface(authority, "database.row.create", "row-default-template",
                       {}, "template-default-row", target_version=0)
    assert created["template_id"] == "template-default-one"
    row = next(item for item in authority.read_database("owner", "database-e1")["rows"]
               if item["id"] == "row-default-template")
    assert row["cells"]["property-e1"] == "Default title"
    assert row["body"]["blocks"][0]["content"] == "Default body"
    empty = _surface(authority, "database.row.create", "row-explicit-empty",
                     {"template_id": None}, "template-explicit-empty-row", target_version=0)
    assert "template_id" not in empty
    assert "body" not in next(item for item in authority.read_database("owner", "database-e1")["rows"]
                             if item["id"] == "row-explicit-empty")
    current_page = authority.read_document("owner", "page-e1")
    copied_result = database_copy.copy_owned_database(
        authority, "owner", workspace_id, "page-e1", "database-e1",
        expected_document_sequence=current_page["document_sequence"],
        expected_workspace_epoch=current_page["workspace_epoch"],
        expected_document_epoch=current_page["document_epoch"],
        operation_id="template-default-database-copy",
    )
    copied = authority.read_database("owner", copied_result["database_id"])
    copied_default = copied["default_template"]["template_id"]
    assert copied_default != "template-default-one"
    assert copied_default in {item["id"] for item in copied["templates"] if item["state"] == "active"}
    _schema(authority, "database.template.archive", "template-default-one", {},
            "template-default-archive", target_version=1)
    assert authority.read_database("owner", "database-e1")["default_template"] == {
        "template_id": None, "version": 2}
    after = _surface(authority, "database.row.create", "row-after-archive",
                     {}, "template-row-after-archive", target_version=0)
    assert "template_id" not in after

