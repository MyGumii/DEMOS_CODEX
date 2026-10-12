"""UI-R5: an exact 2423 catalog reopens through the ordinary app facade."""

from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1.notion_collaboration import (
    database_copy, native_team_location, page_archive, page_creation,
)
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.database_updates import read_database_updates
from code_assist_v1.notion_collaboration.database_row_trash import (
    list_trashed_database_rows, set_database_row_trashed,
    trash_database_rows_batch, restore_database_rows_batch,
    read_database_rows_batch_trash_result,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    DocumentSequenceConflict, TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import (
    read as read_linked_database, setup_pair,
)
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


def _row_trash_worker(args):
    catalog, root, workspace_id, archived, operation_id, fences = args
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    try:
        result = set_database_row_trashed(store.iam, "owner", workspace_id,
            "page-e1", "database-e1", "row-e1", archived=archived,
            operation_id=operation_id, **fences)
        return {"state": "committed", "result": result}
    except (DocumentSequenceConflict, TargetVersionConflict, NotFoundOrDenied) as exc:
        return {"state": type(exc).__name__}


def test_ui_r5_database_row_trash_runtime_reopens_and_keeps_gate_disabled(
        activity_environment):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-runtime-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-runtime.db")

    def reopen():
        return CollaborativeWorkspaceStore(source.db_path,
            user_data_root=source.user_data_root,
            identity_directory=source.identity_directory)

    store = reopen()
    capabilities = store.capabilities()
    assert capabilities["workspace_v2"] is True
    assert capabilities["schema_version"] == row_trash_migration.TARGET_VERSION
    assert capabilities["database_row_content"] is True
    assert capabilities["database_row_appearance"] is True
    assert capabilities["collaborative_database_row_trash"] is False
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    row = next(item for item in database["rows"] if item["id"] == "row-e1")
    fences = {"expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "expected_row_version": row["version"]}
    archived = set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="row-runtime-archive", **fences)
    assert archived["archived"] is True
    archived_updates = read_database_updates(store.iam, "owner", workspace_id, "database-e1")
    assert archived_updates["items"][0]["event_type"] == "database.row.trashed"
    assert "row_id" not in archived_updates["items"][0]

    reopened = reopen()
    assert reopened.capabilities()["collaborative_database_row_trash"] is False
    assert all(item["id"] != "row-e1" for item in
        reopened.iam.read_database("owner", "database-e1")["rows"])
    assert list_trashed_database_rows(reopened.iam, "owner", workspace_id,
        "page-e1", "database-e1")["count"] == 1
    restored = set_database_row_trashed(reopened.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=False,
        operation_id="row-runtime-restore",
        **{**fences, "expected_document_sequence": archived["document_sequence"],
            "expected_row_version": archived["row_version"]})
    assert restored["archived"] is False
    restored_updates = read_database_updates(reopened.iam, "owner", workspace_id, "database-e1")
    assert restored_updates["items"][0]["row_id"] == "row-e1"
    assert any(item["id"] == "row-e1" for item in
        reopen().iam.read_database("owner", "database-e1")["rows"])


def test_ui_r5_selected_rows_archive_atomically_and_replay_one_receipt(
        activity_environment):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _surface(source.iam, "database.row.create", "row-e2", {},
        "row-batch-create-second")
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-batch-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-batch.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    rows = [{"row_id": item["id"], "expected_row_version": item["version"]}
        for item in database["rows"]]
    fences = {"rows": rows, "operation_id": "row-batch-archive",
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"]}
    with sqlite3.connect(store.db_path) as con:
        con.execute("CREATE TRIGGER fail_second_batch_row BEFORE UPDATE ON "
            "workspace_collaboration_database_rows WHEN NEW.id='row-e2' "
            "BEGIN SELECT RAISE(ABORT,'synthetic row failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic row failure"):
            trash_database_rows_batch(store.iam, "owner", workspace_id,
                "page-e1", "database-e1", **fences)
    finally:
        with sqlite3.connect(store.db_path) as con:
            con.execute("DROP TRIGGER fail_second_batch_row")
    assert [item["id"] for item in store.iam.read_database(
        "owner", "database-e1")["rows"]] == ["row-e1", "row-e2"]
    assert store.iam.read_document("owner", "page-e1")["document_sequence"] == (
        page["document_sequence"])
    assert read_database_rows_batch_trash_result(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", **fences) is None
    result = trash_database_rows_batch(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", **fences)
    assert result["replayed"] is False
    assert result["contract"] == "code_assist.notion.database_row_trash_batch.v1"
    assert result["document_sequence"] == page["document_sequence"] + 1
    assert [item["row_id"] for item in result["rows"]] == ["row-e1", "row-e2"]
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    assert list_trashed_database_rows(store.iam, "owner", workspace_id,
        "page-e1", "database-e1")["count"] == 2
    assert trash_database_rows_batch(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", **fences)["replayed"] is True
    receipt = read_database_rows_batch_trash_result(store.iam, "owner",
        workspace_id, "page-e1", "database-e1", **fences)
    assert receipt is not None and receipt["rows"] == result["rows"]
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE operation_id='row-batch-archive'").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    restore_fences = {**fences,
        "rows": [{"row_id": item["row_id"],
                  "expected_row_version": item["row_version"]}
                 for item in result["rows"]],
        "operation_id": "row-batch-restore",
        "expected_document_sequence": result["document_sequence"]}
    with sqlite3.connect(store.db_path) as con:
        con.execute("CREATE TRIGGER fail_second_batch_restore BEFORE UPDATE ON "
            "workspace_collaboration_database_rows WHEN NEW.id='row-e2' "
            "BEGIN SELECT RAISE(ABORT,'synthetic restore failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic restore failure"):
            restore_database_rows_batch(store.iam, "owner", workspace_id,
                "page-e1", "database-e1", **restore_fences)
    finally:
        with sqlite3.connect(store.db_path) as con:
            con.execute("DROP TRIGGER fail_second_batch_restore")
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    assert list_trashed_database_rows(store.iam, "owner", workspace_id,
        "page-e1", "database-e1")["count"] == 2
    assert read_database_rows_batch_trash_result(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", archived=False, **restore_fences) is None
    restored = restore_database_rows_batch(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", **restore_fences)
    assert restored["archived"] is False
    assert restored["document_sequence"] == result["document_sequence"] + 1
    assert [item["id"] for item in store.iam.read_database(
        "owner", "database-e1")["rows"]] == ["row-e1", "row-e2"]
    assert list_trashed_database_rows(store.iam, "owner", workspace_id,
        "page-e1", "database-e1")["count"] == 0
    assert restore_database_rows_batch(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", **restore_fences)["replayed"] is True
    receipt = read_database_rows_batch_trash_result(store.iam, "owner",
        workspace_id, "page-e1", "database-e1", archived=False,
        **restore_fences)
    assert receipt is not None and receipt["rows"] == restored["rows"]


def test_ui_r5_database_row_trash_two_process_replay_and_restore_conflict(
        activity_environment):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-race-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-race.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    fences = {"expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "expected_row_version": database["rows"][0]["version"]}
    shared = (str(store.db_path), str(store.user_data_root), workspace_id,
        True, "row-race-archive", fences)
    with ProcessPoolExecutor(max_workers=2,
            mp_context=multiprocessing.get_context("spawn")) as pool:
        archived_results = list(pool.map(_row_trash_worker, (shared, shared)))
    assert [item["state"] for item in archived_results] == ["committed", "committed"]
    assert sorted(item["result"]["replayed"] for item in archived_results) == [False, True]
    archived = archived_results[0]["result"]
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE operation_id='row-race-archive'").fetchone()[0] == 1

    restore_fences = {**fences,
        "expected_document_sequence": archived["document_sequence"],
        "expected_row_version": archived["row_version"]}
    first = (str(store.db_path), str(store.user_data_root), workspace_id,
        False, "row-race-restore-a", restore_fences)
    second = (*first[:4], "row-race-restore-b", restore_fences)
    with ProcessPoolExecutor(max_workers=2,
            mp_context=multiprocessing.get_context("spawn")) as pool:
        restored_results = list(pool.map(_row_trash_worker, (first, second)))
    assert sorted(item["state"] for item in restored_results) == [
        "DocumentSequenceConflict", "committed"]
    assert store.iam.read_database("owner", "database-e1")["rows"][0]["id"] == "row-e1"
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id='row-e1'").fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_archived_row_is_absent_from_saved_block_cache_and_exports(
        activity_environment):
    def owned_block(snapshot):
        return next(block for block in snapshot["blocks"]
            if block["id"] == "owned-db-source-block")

    source, workspace_id = activity_environment
    owned_database_block(source)
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-cache-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-cache.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    with sqlite3.connect(store.db_path) as con:
        con.execute("INSERT INTO workspace_collaboration_database_rows("
            "id,database_id,position,row_version,created_at,updated_at) "
            "SELECT 'row-e2',database_id,position+1,1,created_at,updated_at "
            "FROM workspace_collaboration_database_rows WHERE id='row-e1'")
        con.execute("UPDATE workspace_collaboration_blocks SET props_json=? WHERE id=?",
            (json.dumps({"database_id": "database-e1", "_canonical_version": 1,
                "rows": [{"_canonical_row_id": "row-e1", "title": "cached-secret"},
                    {"_canonical_row_id": "row-e2", "title": "active-row"}]},
                ensure_ascii=False), "owned-db-source-block"))
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    assert owned_block(page)["props"]["rows"][0]["title"] == "cached-secret"
    fences = {"expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "expected_row_version": database["rows"][0]["version"]}
    archived = set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="row-cache-archive", **fences)
    assert archived["archived"] is True
    expected_cache = [{"_canonical_row_id": "row-e2", "title": "active-row"}]
    assert owned_block(store.iam.read_document("owner", "page-e1"))["props"]["rows"] == expected_cache
    assert owned_block(store.iam.export_bundle("owner", "page-e1")["snapshot"])["props"]["rows"] == expected_cache
    markdown = store.iam.export_markdown("owner", "page-e1")["markdown"]
    embedded = store.iam._bundle_from_markdown(markdown)
    assert embedded is not None
    assert owned_block(embedded["snapshot"])["props"]["rows"] == expected_cache
    restored = set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=False,
        operation_id="row-cache-restore",
        **{**fences, "expected_document_sequence": archived["document_sequence"],
            "expected_row_version": archived["row_version"]})
    assert restored["archived"] is False
    assert [item["title"] for item in owned_block(
        store.iam.read_document("owner", "page-e1"))["props"]["rows"]] == [
            "cached-secret", "active-row"]


def test_ui_r5_linked_database_follows_row_trash_and_restore(activity_environment):
    source, workspace_id = activity_environment
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-linked-row-trash-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-linked-row-trash.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    child, _title = setup_pair(store, workspace_id)
    assert [row["id"] for row in read_linked_database(store, child)["database"]["rows"]] == ["row-e1"]
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    fences = {"expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "expected_row_version": database["rows"][0]["version"]}
    archived = set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="linked-row-archive", **fences)
    assert read_linked_database(store, child)["database"]["rows"] == []
    restored = set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=False,
        operation_id="linked-row-restore",
        **{**fences, "expected_document_sequence": archived["document_sequence"],
            "expected_row_version": archived["row_version"]})
    assert restored["archived"] is False
    assert [row["id"] for row in read_linked_database(store, child)["database"]["rows"]] == ["row-e1"]


def test_ui_r5_page_archive_preserves_nested_row_trash(activity_environment):
    source, workspace_id = activity_environment
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-page-row-trash-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-page-row-trash.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    assert store.capabilities()["collaborative_page_archive"] is True
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    row_fences = {"expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "expected_row_version": database["rows"][0]["version"]}
    set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="nested-row-archive", **row_fences)
    page = store.iam.read_document("owner", "page-e1")
    archived = page_archive.set_page_archived(store.iam, "owner", workspace_id,
        "page-e1", archived=True, operation_id="nested-page-archive",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"])
    assert archived["archived"] is True
    assert "page-e1" in {item["id"] for item in
        page_archive.list_archived_pages(store.iam, "owner", workspace_id)["items"]}
    restored = page_archive.set_page_archived(store.iam, "owner", workspace_id,
        "page-e1", archived=False, operation_id="nested-page-restore",
        expected_document_sequence=archived["document_sequence"],
        expected_workspace_epoch=archived["workspace_epoch"],
        expected_document_epoch=archived["document_epoch"])
    assert restored["archived"] is False
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    trash = list_trashed_database_rows(store.iam, "owner", workspace_id,
        "page-e1", "database-e1")
    assert [item["row_id"] for item in trash["items"]] == ["row-e1"]
    page = store.iam.read_document("owner", "page-e1")
    set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=False,
        operation_id="nested-row-restore",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_database_version=database["version"],
        expected_row_version=trash["items"][0]["row_version"])
    assert store.iam.read_database("owner", "database-e1")["rows"][0]["id"] == "row-e1"


def test_ui_r5_team_root_creation_remains_available_on_2423(activity_environment):
    source, workspace_id = activity_environment
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-team-row-trash-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-team-row-trash.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    assert store.capabilities()["collaborative_team_page_creation"] is True
    with store.iam._reader() as con:
        assert native_team_location.installed(con)
    team = store.create_teamspace("owner", workspace_id, name="Synthetic team",
        visibility="closed", base_version=store.get_workspace("owner", workspace_id)["version"],
        operation_id="team-row-trash-create")["teamspace"]["id"]
    created = page_creation.create_page(store.iam, "owner", workspace_id,
        title="Synthetic team page", teamspace_id=team,
        base_version=store.get_workspace("owner", workspace_id)["version"],
        operation_id="team-row-trash-page")
    page_id = created["page"]["id"]
    assert store.iam.read_document("owner", page_id)["document_id"] == page_id
    native = {item["id"]: item for item in
        page_creation.native_page_items(store.iam, "owner", workspace_id)}
    assert native[page_id]["placement"]["teamspace_id"] == team


def test_ui_r5_database_copy_omits_archived_row_on_2423(activity_environment):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-copy-row-trash-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-copy-row-trash.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    assert store.capabilities()["collaborative_owned_database_copy"] is False
    _surface(store.iam, "database.row.create", "row-e2", {},
        "copy-active-row-create", actor="owner", target_version=0)
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    original_row = next(row for row in database["rows"] if row["id"] == "row-e1")
    set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=True,
        operation_id="copy-source-row-archive",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_database_version=database["version"],
        expected_row_version=original_row["version"])
    page = store.iam.read_document("owner", "page-e1")
    copied = database_copy.copy_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", operation_id="row-trash-safe-copy",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"])
    copied_database = store.iam.read_database("owner", copied["database_id"])
    assert len(copied_database["rows"]) == 1
    assert copied_database["rows"][0]["id"] != "row-e1"
    assert copied_database["rows"][0]["cells"] == {}
    assert [row["id"] for row in store.iam.read_database("owner", "database-e1")["rows"]] == ["row-e2"]
    trash = list_trashed_database_rows(store.iam, "owner", workspace_id,
        "page-e1", "database-e1")
    assert [item["row_id"] for item in trash["items"]] == ["row-e1"]
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_database_row_trash_state "
            "WHERE database_id=?", (copied["database_id"],)).fetchone()[0] == 0
    page = store.iam.read_document("owner", "page-e1")
    set_database_row_trashed(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", "row-e1", archived=False,
        operation_id="copy-source-row-restore",
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_database_version=database["version"],
        expected_row_version=trash["items"][0]["row_version"])
    assert {row["id"] for row in store.iam.read_database("owner", "database-e1")["rows"]} == {
        "row-e1", "row-e2"}
    assert store.iam.read_database("owner", copied["database_id"])["rows"] == copied_database["rows"]
