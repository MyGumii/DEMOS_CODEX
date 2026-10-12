"""The item-page layout belongs to a database, never to one of its views."""

import sqlite3
from copy import deepcopy

import pytest

from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration import database_copy
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_item_page_layout_forward_schema_preserves_existing_database(icon_batch_environment):
    store, workspace_id = icon_batch_environment
    _apply(placement, store.db_path, store.user_data_root, "before-layout-placement.db")
    _apply(custom_emoji, store.db_path, store.user_data_root, "before-layout-emoji.db")
    con = sqlite3.connect(store.db_path)
    try:
        before_database = con.execute(
            "SELECT id,title,row_version FROM workspace_collaboration_databases WHERE id='database-e1'"
        ).fetchone()
        before_views = con.execute(
            "SELECT id,config_json FROM workspace_collaboration_database_views "
            "WHERE database_id='database-e1' ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "migration_required"
    _apply(migration, store.db_path, store.user_data_root, "before-item-page-layout.db")
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"

    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        assert con.execute(
            "SELECT id,title,row_version FROM workspace_collaboration_databases WHERE id='database-e1'"
        ).fetchone() == before_database
        assert con.execute(
            "SELECT id,config_json FROM workspace_collaboration_database_views "
            "WHERE database_id='database-e1' ORDER BY id"
        ).fetchall() == before_views
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_collaboration_database_item_page_layouts"
        ).fetchone()[0] == 0

        layout = '{"structure":"tabs","modules":["properties","comments","content"]}'
        con.execute(
            "INSERT INTO workspace_collaboration_database_item_page_layouts("
            "database_id,layout_json,row_version,updated_by_subject_id,created_at,updated_at) "
            "VALUES(?,?,1,'owner',1,1)",
            ("database-e1", layout),
        )
        assert con.execute(
            "SELECT layout_json,row_version FROM workspace_collaboration_database_item_page_layouts "
            "WHERE database_id='database-e1'"
        ).fetchone() == (layout, 1)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_collaboration_database_item_page_layouts("
                "database_id,layout_json,updated_by_subject_id,created_at,updated_at) "
                "VALUES('missing-database','{}','owner',1,1)"
            )
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.execute(
            "DELETE FROM workspace_collaboration_database_item_page_layouts "
            "WHERE database_id='database-e1'"
        )
        con.commit()
    finally:
        con.close()

    reopened = CollaborativeWorkspaceStore(
        store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory,
    )
    assert reopened.capabilities()["schema_version"] == migration.TARGET_VERSION
    assert reopened.capabilities()["database_row_content"] is True
    assert reopened.capabilities()["database_item_page_layout"] is True
    assert reopened.capabilities()["collaborative_database_favorites"] is True
    database = reopened.iam.read_database("owner", "database-e1")
    assert database["id"] == "database-e1"
    assert "item_page_layout" not in database
    visible_properties = [
        item["id"] for item in database["properties"] if item["type"] != "title"
    ]
    layout = {
        "structure": "tabs",
        "modules": ["properties", "comments", "content"],
        "page_properties": visible_properties,
        "panel_properties": [],
        "hidden_properties": [],
        "options": {
            "show_page": True, "property_icons": True,
            "full_width": False, "show_templates": True,
            "inline_comments": "default",
        },
    }
    page = reopened.iam.read_document("owner", "page-e1")
    request = {
        "database_id": "database-e1",
        "command_kind": "database.item_layout.set",
        "target_id": "database-e1",
        "payload": {"layout": layout},
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_target_version": 0,
        "operation_id": "test-item-page-layout-first-save",
    }
    result = reopened.iam.execute_surface_command("owner", "page-e1", **request)
    assert result["target_version"] == 1
    assert result["item_page_layout"]["value"] == layout
    assert reopened.iam.execute_surface_command("owner", "page-e1", **request) == result
    updated_database = reopened.iam.read_database("owner", "database-e1")
    assert updated_database["capabilities"]["item_page_layout"] is True
    assert updated_database["item_page_layout"] == {"value": layout, "version": 1}
    with pytest.raises(TargetVersionConflict):
        reopened.iam.execute_surface_command(
            "owner", "page-e1",
            **{**request, "expected_document_sequence": result["document_sequence"],
               "operation_id": "test-item-page-layout-stale-version"},
        )
    with pytest.raises(ValueError, match="placement"):
        reopened.iam.execute_surface_command(
            "owner", "page-e1",
            **{**request, "payload": {"layout": {
                **layout, "panel_properties": ["not-in-database"]}},
               "expected_document_sequence": result["document_sequence"],
               "expected_target_version": 1,
               "operation_id": "test-item-page-layout-unknown-property"},
        )
    content_page = reopened.iam.read_document("content-editor", "page-e1")
    with pytest.raises((PermissionError, NotFoundOrDenied)):
        reopened.iam.execute_surface_command(
            "content-editor", "page-e1",
            **{**request, "expected_document_sequence": result["document_sequence"],
               "expected_workspace_epoch": content_page["workspace_epoch"],
               "expected_document_epoch": content_page["document_epoch"],
               "expected_target_version": 1,
               "operation_id": "test-item-page-layout-content-editor-denied"},
        )
    assert reopened.iam.read_database("owner", "database-e1")["item_page_layout"] == {
        "value": layout, "version": 1,
    }
    current_page = reopened.iam.read_document("owner", "page-e1")
    copied_result = database_copy.copy_owned_database(
        reopened.iam, "owner", workspace_id, "page-e1", "database-e1",
        expected_document_sequence=current_page["document_sequence"],
        expected_workspace_epoch=current_page["workspace_epoch"],
        expected_document_epoch=current_page["document_epoch"],
        operation_id="test-item-page-layout-copy",
    )
    copied = reopened.iam.read_database("owner", copied_result["database_id"])
    copied_layout = copied["item_page_layout"]
    assert copied_layout["version"] == 1
    assert copied_layout["value"]["structure"] == "tabs"
    assert copied_layout["value"]["options"] == layout["options"]
    copied_properties = {
        item["id"] for item in copied["properties"] if item["type"] != "title"
    }
    assert set(copied_layout["value"]["page_properties"]) == copied_properties
    assert copied_properties.isdisjoint(visible_properties)
    assert reopened.iam.read_database("owner", "database-e1")["item_page_layout"] == {
        "value": layout, "version": 1,
    }
    visibility_layout = {
        **layout,
        "empty_hidden_properties": [visible_properties[0]],
        "standalone_properties": [visible_properties[0]],
        "property_group_placement": "panel",
        "sections": [
            {"id": "section-main", "name": "기본 정보", "property_ids": visible_properties},
            {"id": "section-extra", "name": "추가 정보", "property_ids": []},
        ],
    }
    with pytest.raises(ValueError, match="standalone properties"):
        from code_assist_v1.notion_collaboration.database_item_page_layout import clean_layout
        clean_layout({**visibility_layout,
                      "standalone_properties": [visible_properties[0], visible_properties[0]]},
                     property_ids=set(visible_properties))
    with pytest.raises(ValueError, match="empty visibility"):
        current_page = reopened.iam.read_document("owner", "page-e1")
        reopened.iam.execute_surface_command(
            "owner", "page-e1",
            **{**request, "payload": {"layout": {
                **visibility_layout, "page_properties": visible_properties[1:],
                "hidden_properties": [visible_properties[0]]}},
               "expected_document_sequence": current_page["document_sequence"],
               "expected_workspace_epoch": current_page["workspace_epoch"],
               "expected_document_epoch": current_page["document_epoch"],
               "expected_target_version": 1,
               "operation_id": "test-item-page-layout-invalid-visibility"},
        )
    current_page = reopened.iam.read_document("owner", "page-e1")
    visibility_result = reopened.iam.execute_surface_command(
        "owner", "page-e1",
        **{**request, "payload": {"layout": visibility_layout},
           "expected_document_sequence": current_page["document_sequence"],
           "expected_workspace_epoch": current_page["workspace_epoch"],
           "expected_document_epoch": current_page["document_epoch"],
           "expected_target_version": 1,
           "operation_id": "test-item-page-layout-visibility-save"},
    )
    assert visibility_result["item_page_layout"] == {
        "value": visibility_layout, "version": 2,
    }
    current_page = reopened.iam.read_document("owner", "page-e1")
    visibility_copy = database_copy.copy_owned_database(
        reopened.iam, "owner", workspace_id, "page-e1", "database-e1",
        expected_document_sequence=current_page["document_sequence"],
        expected_workspace_epoch=current_page["workspace_epoch"],
        expected_document_epoch=current_page["document_epoch"],
        operation_id="test-item-layout-visibility-copy",
    )
    copied_visibility = reopened.iam.read_database("owner", visibility_copy["database_id"])
    assert copied_visibility["item_page_layout"]["value"]["property_group_placement"] == "panel"
    assert copied_visibility["item_page_layout"]["value"]["empty_hidden_properties"] == [
        copied_visibility["item_page_layout"]["value"]["page_properties"][0]
    ]
    assert copied_visibility["item_page_layout"]["value"]["standalone_properties"] == [
        copied_visibility["item_page_layout"]["value"]["page_properties"][0]
    ]
    assert copied_visibility["item_page_layout"]["value"]["sections"][0]["property_ids"] == (
        copied_visibility["item_page_layout"]["value"]["page_properties"]
    )
    assert copied_visibility["item_page_layout"]["value"]["sections"][1]["property_ids"] == []
    new_property_id = "layout-added-text"
    layout_with_property = deepcopy(visibility_layout)
    layout_with_property["panel_properties"].append(new_property_id)
    layout_with_property["standalone_properties"].append(new_property_id)
    layout_with_property["sections"][1]["property_ids"].append(new_property_id)
    current_page = reopened.iam.read_document("owner", "page-e1")
    create_request = {
        **request,
        "payload": {"layout": layout_with_property, "new_properties": [
            {"id": new_property_id, "name": "레이아웃 메모", "type": "text", "config": {}}
        ]},
        "expected_document_sequence": current_page["document_sequence"],
        "expected_workspace_epoch": current_page["workspace_epoch"],
        "expected_document_epoch": current_page["document_epoch"],
        "expected_target_version": 2,
        "operation_id": "test-item-layout-atomic-property-create",
    }
    created = reopened.iam.execute_surface_command("owner", "page-e1", **create_request)
    assert created["created_property_ids"] == [new_property_id]
    assert reopened.iam.execute_surface_command("owner", "page-e1", **create_request) == created
    after_create = reopened.iam.read_database("owner", "database-e1")
    assert after_create["item_page_layout"]["value"] == layout_with_property
    assert any(item["id"] == new_property_id for item in after_create["properties"])
    stale_layout = deepcopy(layout_with_property)
    stale_layout["panel_properties"].append("layout-stale-text")
    current_page = reopened.iam.read_document("owner", "page-e1")
    with pytest.raises(TargetVersionConflict):
        reopened.iam.execute_surface_command("owner", "page-e1", **{
            **create_request,
            "payload": {"layout": stale_layout, "new_properties": [
                {"id": "layout-stale-text", "name": "충돌 속성", "type": "text", "config": {}}
            ]},
            "expected_document_sequence": current_page["document_sequence"],
            "expected_target_version": 2,
            "operation_id": "test-item-layout-stale-property-create",
        })
    assert not any(item["id"] == "layout-stale-text" for item in
                   reopened.iam.read_database("owner", "database-e1")["properties"])
    rollback_layout = deepcopy(layout_with_property)
    rollback_layout["panel_properties"].extend(["layout-rollback-good", "layout-rollback-bad"])
    rollback_layout["sections"][1]["property_ids"].extend(
        ["layout-rollback-good", "layout-rollback-bad"])
    current_page = reopened.iam.read_document("owner", "page-e1")
    with pytest.raises(ValueError, match="supported database property type"):
        reopened.iam.execute_surface_command("owner", "page-e1", **{
            **create_request,
            "payload": {"layout": rollback_layout, "new_properties": [
                {"id": "layout-rollback-good", "name": "임시 속성", "type": "text", "config": {}},
                {"id": "layout-rollback-bad", "name": "잘못된 속성", "type": "unknown", "config": {}},
            ]},
            "expected_document_sequence": current_page["document_sequence"],
            "expected_workspace_epoch": current_page["workspace_epoch"],
            "expected_document_epoch": current_page["document_epoch"],
            "expected_target_version": 3,
            "operation_id": "test-item-layout-atomic-property-rollback",
        })
    after_rollback = reopened.iam.read_database("owner", "database-e1")
    assert after_rollback["item_page_layout"]["value"] == layout_with_property
    assert {item["id"] for item in after_rollback["properties"]}.isdisjoint(
        {"layout-rollback-good", "layout-rollback-bad"})
    assert reopened.iam.read_document("owner", "page-e1")["document_sequence"] == (
        current_page["document_sequence"])
    current_page = reopened.iam.read_document("owner", "page-e1")
    reopened.iam.execute_surface_command(
        "owner", "page-e1", database_id="database-e1",
        command_kind="database.property.delete", target_id=new_property_id,
        payload={}, expected_target_version=1,
        expected_document_sequence=current_page["document_sequence"],
        expected_workspace_epoch=current_page["workspace_epoch"],
        expected_document_epoch=current_page["document_epoch"],
        operation_id="test-item-layout-created-property-delete",
    )
    after_delete = reopened.iam.read_database("owner", "database-e1")
    assert not any(item["id"] == new_property_id for item in after_delete["properties"])
    assert new_property_id not in after_delete["item_page_layout"]["value"]["panel_properties"]
    assert new_property_id not in after_delete["item_page_layout"]["value"]["standalone_properties"]
    assert after_delete["item_page_layout"]["value"]["sections"][1]["property_ids"] == []
