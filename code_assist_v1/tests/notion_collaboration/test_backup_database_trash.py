"""UI-R5: a 2422 independent DB Trash survives coordinated restore."""

import hashlib
from io import BytesIO
import sqlite3
from pathlib import Path

import pytest

from code_assist_v1 import (
    notion_workspace_icon_image_migration as m2411,
    notion_workspace_guest_navigation_migration as m2412,
    notion_workspace_page_archive_migration as m2413,
    notion_workspace_native_page_migration as m2414,
    notion_workspace_member_navigation_migration as m2415,
    notion_workspace_file_copy_migration as m2416,
    notion_workspace_native_team_migration as m2417,
    notion_workspace_row_body_migration as m2418,
    notion_workspace_row_appearance_migration as m2419,
    notion_workspace_same_document_file_copy_migration as m2420,
    notion_workspace_database_activity_migration as m2421,
    notion_workspace_database_trash_migration as m2422,
    notion_workspace_database_row_trash_migration as m2423,
)
from code_assist_v1.notion_collaboration import database_row_trash, database_trash
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    create_local_workspace_generation,
    restore_local_workspace_generation,
    verify_local_workspace_generation,
    verify_local_workspace_restored_root,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.search_preview import read_search_preview
from code_assist_v1.notion_workspace_store import catalog_path_for_user_data_root
from code_assist_v1.tests.notion_collaboration.o1a_p1_backup_fixture import (
    PAGE_ID, build_o1a_backup_environment,
)
from code_assist_v1.tests.notion_collaboration.second_brain_publication_fixture import SUBJECTS
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply


@pytest.mark.integration
@pytest.mark.restore
def test_ui_r5_database_trash_survives_2422_coordinated_restore(
        tmp_path, isolated_knowledge_layout, request):
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request)
    for migration in (m2411, m2412, m2413, m2414, m2415, m2416,
                      m2417, m2418, m2419, m2420, m2421, m2422):
        _apply(migration, environment.notion_catalog, environment.notion_root,
               f"o1a-before-{migration.TARGET_VERSION}.db")
    store = CollaborativeWorkspaceStore(environment.notion_catalog,
        user_data_root=environment.notion_root,
        identity_directory=InMemoryIdentityDirectory(SUBJECTS))
    page = store.iam.read_document("owner", PAGE_ID)
    workspace_id = page["workspace_id"]
    payload = b"archived database attachment survives coordinated restore"
    begun = store.iam.begin_upload("owner", PAGE_ID,
        original_name="archived-attachment.txt", mime_type="text/plain",
        total_size=len(payload),
        expected_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-trash-file-begin")
    store.iam.stage_chunk("owner", begun["session_id"], chunk_index=0,
        stream=BytesIO(payload), operation_id="o1a-trash-file-chunk")
    uploaded = store.iam.finalize_upload("owner", begun["session_id"],
        operation_id="o1a-trash-file-finalize")
    file_id = uploaded["file_id"]
    assert store.iam.get_file("owner", file_id)["path"].read_bytes() == payload
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_schema_command("owner", PAGE_ID,
        database_id="o1a-trash-db", command_kind="database.create",
        target_id="o1a-trash-db", payload={"title": "O1A trashed database"},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_target_version=0, operation_id="o1a-trash-db-create")
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_typed_command("owner", PAGE_ID,
        command_kind="block.insert", target_id="o1a-trash-db-block",
        payload={"type": "table", "content": "", "props": {
            "database_id": "o1a-trash-db", "icon": "nf:" + file_id},
            "after_block_id": None},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-trash-db-block")
    page = store.iam.read_document("owner", PAGE_ID)
    database = store.iam.read_database("owner", "o1a-trash-db")
    archived = database_trash.set_owned_database_trashed(store.iam, "owner",
        workspace_id, PAGE_ID, "o1a-trash-db", archived=True,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_database_version=database["version"],
        operation_id="o1a-trash-db-archive")
    assert archived["archived"]
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)

    generation = create_local_workspace_generation(
        environment.source_root, "o1a-2422-trash",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe)
    manifest = verify_local_workspace_generation(
        environment.source_root, generation.generation_id,
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout)
    assert manifest["authorities"]["notion"]["observation"]["schema_version"] == m2422.TARGET_VERSION

    restored = restore_local_workspace_generation(
        environment.source_root, generation.generation_id, "restored-2422-trash",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        target_parent=isolated_knowledge_layout.root.parent / "o1a-restore-targets")
    receipt = verify_local_workspace_restored_root(restored.target_root)
    assert receipt["notion"]["canonical_match"] is True
    catalog = Path(catalog_path_for_user_data_root(restored.target_root / "notion"))
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT database_id,block_id FROM "
            "workspace_collaboration_database_trash_state").fetchall() == [
                ("o1a-trash-db", "o1a-trash-db-block")]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened = CollaborativeWorkspaceStore(catalog,
        user_data_root=restored.target_root / "notion",
        identity_directory=InMemoryIdentityDirectory(SUBJECTS))
    assert database_trash.list_trashed_owned_databases(reopened.iam, "owner",
        workspace_id)["count"] == 1
    with pytest.raises(NotFoundOrDenied):
        reopened.iam.read_database("owner", "o1a-trash-db")
    with pytest.raises(NotFoundOrDenied):
        reopened.iam.get_file("owner", file_id)
    hidden = reopened.iam.read_document("owner", PAGE_ID)
    restored_database = database_trash.set_owned_database_trashed(reopened.iam,
        "owner", workspace_id, PAGE_ID, "o1a-trash-db", archived=False,
        expected_document_sequence=hidden["document_sequence"],
        expected_workspace_epoch=hidden["workspace_epoch"],
        expected_document_epoch=hidden["document_epoch"],
        expected_database_version=archived["database_version"],
        operation_id="o1a-trash-db-restore")
    assert not restored_database["archived"]
    assert reopened.iam.read_database("owner", "o1a-trash-db")["id"] == "o1a-trash-db"
    assert reopened.iam.get_file("owner", file_id)["path"].read_bytes() == payload


@pytest.mark.integration
@pytest.mark.restore
def test_ui_r5_database_row_trash_survives_2423_coordinated_restore(
        tmp_path, isolated_knowledge_layout, request):
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request)
    for migration in (m2411, m2412, m2413, m2414, m2415, m2416,
                      m2417, m2418, m2419, m2420, m2421, m2422):
        _apply(migration, environment.notion_catalog, environment.notion_root,
               f"o1a-row-before-{migration.TARGET_VERSION}.db")
    store = CollaborativeWorkspaceStore(environment.notion_catalog,
        user_data_root=environment.notion_root,
        identity_directory=InMemoryIdentityDirectory(SUBJECTS))
    page = store.iam.read_document("owner", PAGE_ID)
    workspace_id = page["workspace_id"]
    payload = b"archived row attachment survives coordinated restore"
    begun = store.iam.begin_upload("owner", PAGE_ID,
        original_name="archived-row-attachment.txt", mime_type="text/plain",
        total_size=len(payload),
        expected_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-row-file-begin")
    store.iam.stage_chunk("owner", begun["session_id"], chunk_index=0,
        stream=BytesIO(payload), operation_id="o1a-row-file-chunk")
    file_id = store.iam.finalize_upload("owner", begun["session_id"],
        operation_id="o1a-row-file-finalize")["file_id"]

    def schema(kind, target, value, operation):
        current = store.iam.read_document("owner", PAGE_ID)
        return store.iam.execute_schema_command("owner", PAGE_ID,
            database_id="o1a-row-trash-db", command_kind=kind,
            target_id=target, payload=value,
            expected_document_sequence=current["document_sequence"],
            expected_workspace_epoch=current["workspace_epoch"],
            expected_document_epoch=current["document_epoch"],
            expected_target_version=0, operation_id=operation)

    schema("database.create", "o1a-row-trash-db",
        {"title": "O1A row Trash database"}, "o1a-row-db-create")
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_typed_command("owner", PAGE_ID,
        command_kind="block.insert", target_id="o1a-row-trash-db-block",
        payload={"type": "table", "content": "",
            "props": {"database_id": "o1a-row-trash-db"},
            "after_block_id": None},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-row-db-block")
    schema("database.property.create", "o1a-row-files",
        {"name": "Attachment", "type": "files", "config": {}},
        "o1a-row-files-property")
    schema("database.property.create", "o1a-row-title",
        {"name": "Title", "type": "title", "config": {}},
        "o1a-row-title-property")
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_surface_command("owner", PAGE_ID,
        database_id="o1a-row-trash-db", command_kind="database.row.create",
        target_id="o1a-archived-row", payload={},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_target_version=0, operation_id="o1a-row-create")
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_typed_command("owner", PAGE_ID,
        command_kind="database.cell.set",
        target_id="o1a-archived-row:o1a-row-files",
        payload={"row_id": "o1a-archived-row", "property_id": "o1a-row-files",
            "value": [{"file_id": file_id}]}, expected_target_version=0,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-row-files-cell")
    page = store.iam.read_document("owner", PAGE_ID)
    store.iam.execute_typed_command("owner", PAGE_ID,
        command_kind="database.cell.set",
        target_id="o1a-archived-row:o1a-row-title",
        payload={"row_id": "o1a-archived-row", "property_id": "o1a-row-title",
            "value": "ZebraArchiveSentinel"}, expected_target_version=0,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id="o1a-row-title-cell")
    _apply(m2423, environment.notion_catalog, environment.notion_root,
        "o1a-before-2423-row-trash.db")
    source_reopened = CollaborativeWorkspaceStore(environment.notion_catalog,
        user_data_root=environment.notion_root,
        identity_directory=InMemoryIdentityDirectory(SUBJECTS))
    assert source_reopened.capabilities()["schema_version"] == m2423.TARGET_VERSION
    assert source_reopened.capabilities()["collaborative_database_row_trash"] is False
    authority = source_reopened.iam
    assert any(item["document_id"] == PAGE_ID for item in authority.search(
        "owner", workspace_id, query="ZebraArchiveSentinel")["items"])
    before_export = authority.export_database_html("owner", PAGE_ID,
        block_id="o1a-row-trash-db-block")
    assert "ZebraArchiveSentinel" in before_export["html"]
    assert "ZebraArchiveSentinel" in before_export["csv"]
    with authority._reader() as con:
        page = authority._document(con, PAGE_ID)
        workspace = authority._workspace(con, workspace_id)
        database = con.execute("SELECT row_version FROM "
            "workspace_collaboration_databases WHERE id='o1a-row-trash-db'").fetchone()
        row = con.execute("SELECT row_version FROM "
            "workspace_collaboration_database_rows WHERE id='o1a-archived-row'").fetchone()
        fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database["row_version"]),
            "expected_row_version": int(row["row_version"])}
    archived = database_row_trash.set_database_row_trashed(authority, "owner",
        workspace_id, PAGE_ID, "o1a-row-trash-db", "o1a-archived-row",
        archived=True, operation_id="o1a-row-archive", **fences)
    assert authority.read_database("owner", "o1a-row-trash-db")["rows"] == []
    assert authority.search("owner", workspace_id,
        query="ZebraArchiveSentinel")["items"] == []
    hidden_export = authority.export_database_html("owner", PAGE_ID,
        block_id="o1a-row-trash-db-block")
    assert "ZebraArchiveSentinel" not in hidden_export["html"]
    assert "ZebraArchiveSentinel" not in hidden_export["csv"]
    preview = read_search_preview(authority, "owner", workspace_id, PAGE_ID)
    assert next(item for item in preview["databases"]
        if item["id"] == "o1a-row-trash-db")["rows"] == []
    with pytest.raises(NotFoundOrDenied):
        authority.get_file("owner", file_id)

    generation = create_local_workspace_generation(
        environment.source_root, "o1a-2423-row-trash",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe)
    manifest = verify_local_workspace_generation(
        environment.source_root, generation.generation_id,
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout)
    assert manifest["authorities"]["notion"]["observation"]["schema_version"] == m2423.TARGET_VERSION
    restored = restore_local_workspace_generation(
        environment.source_root, generation.generation_id, "restored-2423-row-trash",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        target_parent=isolated_knowledge_layout.root.parent / "o1a-restore-targets")
    receipt = verify_local_workspace_restored_root(restored.target_root)
    assert receipt["notion"]["canonical_match"] is True
    catalog = Path(catalog_path_for_user_data_root(restored.target_root / "notion"))
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT row_id,database_id FROM "
            "workspace_collaboration_database_row_trash_state").fetchall() == [
                ("o1a-archived-row", "o1a-row-trash-db")]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    restored_store = CollaborativeWorkspaceStore(catalog,
        user_data_root=restored.target_root / "notion",
        identity_directory=InMemoryIdentityDirectory(SUBJECTS))
    assert restored_store.capabilities()["schema_version"] == m2423.TARGET_VERSION
    reopened = restored_store.iam
    assert reopened.read_database("owner", "o1a-row-trash-db")["rows"] == []
    with pytest.raises(NotFoundOrDenied):
        reopened.get_file("owner", file_id)
    restored_row = database_row_trash.set_database_row_trashed(reopened, "owner",
        workspace_id, PAGE_ID, "o1a-row-trash-db", "o1a-archived-row",
        archived=False, operation_id="o1a-row-restore",
        expected_document_sequence=archived["document_sequence"],
        expected_workspace_epoch=archived["workspace_epoch"],
        expected_document_epoch=archived["document_epoch"],
        expected_database_version=archived["database_version"],
        expected_row_version=archived["row_version"])
    assert not restored_row["archived"]
    assert reopened.read_database("owner", "o1a-row-trash-db")["rows"][0]["id"] == "o1a-archived-row"
    assert any(item["document_id"] == PAGE_ID for item in reopened.search(
        "owner", workspace_id, query="ZebraArchiveSentinel")["items"])
    restored_export = reopened.export_database_html("owner", PAGE_ID,
        block_id="o1a-row-trash-db-block")
    assert "ZebraArchiveSentinel" in restored_export["html"]
    assert "ZebraArchiveSentinel" in restored_export["csv"]
    assert reopened.get_file("owner", file_id)["path"].read_bytes() == payload
