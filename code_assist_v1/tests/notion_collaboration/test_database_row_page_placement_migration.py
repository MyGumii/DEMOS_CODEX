"""A database row keeps one ID and its cells while it has page placement."""

import sqlite3
from pathlib import Path

import pytest

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as migration
from code_assist_v1.notion_collaboration import database_row_trash, page_move
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import _notion_observation
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, environment, appearance_environment,
    body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_row_page_placement_forward_schema_preserves_row_and_rejects_orphans(
        icon_batch_environment):
    store, workspace_id = icon_batch_environment
    page = store.iam.read_document("owner", "page-e1")
    store.iam.create_document(
        "owner", workspace_id, "row-e1", title="Same row ID",
        blocks=[{"id": "placement-body", "type": "paragraph",
                 "content": "Page body", "props": {}}],
        parent_document_id="page-e1",
        expected_workspace_epoch=page["workspace_epoch"],
        operation_id="placement-test-create-shell",
    )
    with store.iam._transaction() as con:
        con.execute(
            "INSERT INTO workspace_collaboration_database_properties("
            "id,database_id,name,property_type,position,config_json,row_version,"
            "created_at,updated_at) "
            "VALUES('property-e-date','database-e1','날짜','date',1,'{}',1,1,1)"
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_cells("
            "row_id,property_id,value_json,row_version,updated_at) "
            "VALUES('row-e1','property-e-date','\"2026-09-15\"',1,1)"
        )
    with store.iam._reader() as con:
        row_before = tuple(con.execute(
            "SELECT * FROM workspace_collaboration_database_rows WHERE id='row-e1'"
        ).fetchone())
        cells_before = [tuple(item) for item in con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id"
        )]

    _apply(migration, store.db_path, store.user_data_root,
           "before-row-page-placement.db")
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    assert _notion_observation(Path(store.db_path))["schema_version"] == migration.TARGET_VERSION

    con = sqlite3.connect(store.db_path)
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    try:
        assert tuple(con.execute(
            "SELECT * FROM workspace_collaboration_database_rows WHERE id='row-e1'"
        ).fetchone()) == row_before
        assert [tuple(item) for item in con.execute(
            "SELECT * FROM workspace_collaboration_database_cells "
            "WHERE row_id='row-e1' ORDER BY property_id"
        )] == cells_before
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_page_placements("
            "row_id,page_document_id,placement,parent_document_id,"
            "created_by_subject_id,created_operation_id,row_version,created_at,updated_at) "
            "VALUES('row-e1','row-e1','page','page-e1','owner','placement-test',1,1,1)"
        )
        con.execute(
            "UPDATE workspace_collaboration_blocks SET props_json=? "
            "WHERE id='owned-db-source-block'",
            ('{"database_id":"database-e1","rows":'
             '[{"id":"row-e1"},{"id":"row-e2"}]}',),
        )
        assert tuple(con.execute(
            "SELECT placement,parent_document_id FROM "
            "workspace_collaboration_database_row_page_placements WHERE row_id='row-e1'"
        ).fetchone()) == ("page", "page-e1")
        hidden = store.iam._editing_snapshot(con, store.iam._document(con, "page-e1"))
        source = next(item for item in hidden["databases"] if item["id"] == "database-e1")
        assert "row-e1" not in [item["id"] for item in source["rows"]]
        assert "row-e2" in [item["id"] for item in source["rows"]]
        table = next(item for item in hidden["blocks"]
                     if item["id"] == "owned-db-source-block")
        assert [item["id"] for item in table["props"]["rows"]] == ["row-e2"]
        with pytest.raises(NotFoundOrDenied):
            database_row_trash.require_active(con, "database-e1", "row-e1")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM workspace_collaboration_database_rows WHERE id='row-e1'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO workspace_collaboration_database_row_page_placements("
                "row_id,page_document_id,placement,parent_document_id,"
                "created_by_subject_id,created_operation_id,created_at,updated_at) "
                "VALUES('missing-row','row-e1','page','page-e1','owner','bad',1,1)"
            )
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row_two_version = con.execute(
            "SELECT row_version FROM workspace_collaboration_database_rows "
            "WHERE id='row-e2'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_trash_state("
            "row_id,database_id,archive_workspace_id,archive_document_id,"
            "archived_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
            "VALUES('row-e2','database-e1',?,'page-e1',?,'owner',2,'placement-test-trash')",
            (workspace_id, row_two_version),
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
    assert reopened.capabilities()["collaborative_database_row_move_batch"] is False
    assert reopened.iam.read_database("owner", "database-e1")["rows"] == []
    with reopened.iam._transaction() as con:
        con.execute(
            "UPDATE workspace_collaboration_database_row_page_placements "
            "SET placement='database',parent_document_id=NULL,row_version=row_version+1 "
            "WHERE row_id='row-e1'"
        )
    reattached = reopened.iam.read_database("owner", "database-e1")["rows"]
    assert [item["id"] for item in reattached] == ["row-e1"]
    assert reattached[0]["cells"]["property-e-date"] == "2026-09-15"


@pytest.mark.integration
def test_ui_r5_row_page_move_prepares_multiple_child_links_without_body_replacement(
        icon_batch_environment):
    store, _workspace_id = icon_batch_environment
    with store.iam._reader() as con:
        parent = store.iam._document(con, "page-e1")
        generation = _generation(con, "page-e1")
        source = {
            "id": "page-e1", "generation": generation,
            "frames": _frames(con, "page-e1", generation),
            "snapshot": store.iam._editing_snapshot(con, parent),
            "insert": None,
            "insert_many": [
                {"id": "batch-child-link-a", "type": "child_page",
                 "content": "A", "props": {"page_id": "row-e1"}},
                {"id": "batch-child-link-b", "type": "child_page",
                 "content": "B", "props": {"page_id": "row-e2"}},
            ],
        }
    delta = page_move._prepare_deltas({"page-e1": source}, "row-e1",
                                      "row-page-batch-links")["page-e1"]
    assert [item["page_id"] for item in delta["placed_many"]] == ["row-e1", "row-e2"]
    assert delta["placed_many"][1]["after_block_id"] == "batch-child-link-a"
    assert delta["changed"] is True
    assert delta["removed"] == []
    assert delta["payload"]
