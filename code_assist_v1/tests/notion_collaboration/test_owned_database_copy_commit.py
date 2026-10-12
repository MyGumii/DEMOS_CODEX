"""UI-DB-COPY: isolated canonical commit and recovery for one owned database."""
import pytest

from code_assist_v1.notion_collaboration import database_copy, page_copy_source
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.page_copy_source import CopySourceChanged
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    member_environment, native_environment, archive_environment,
    guest_environment, image_environment, connection, dump, request, upload,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    environment as appearance_environment, body_environment, team_environment, file_environment,
    request as appearance_request,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_row_content import (
    execute as row_execute, request as body_request, blocks as row_blocks,
)


@pytest.fixture()
def environment(appearance_environment):
    old, workspace_id = appearance_environment
    _apply(same_file_migration, old.db_path, old.user_data_root, 'before-db-file-copy-2420.db')
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory), workspace_id


def copy(store, workspace_id, operation="owned-db-copy", **options):
    return database_copy.copy_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", operation_id=operation,
        **(options.pop("fences", None) or request(store)), **options)


def owned_database_block(store):
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="owned-db-source-block", payload={"type": "table", "content": "",
            "props": {"database_id": "database-e1"}, "after_block_id": None},
        operation_id="owned-db-source-block-insert", **request(store))


def test_ui_owned_database_copy_commits_independent_data_and_exact_recovery(environment):
    store, workspace_id = environment
    owned_database_block(store)
    source = store.iam.read_database("owner", "database-e1")
    fences = request(store)
    result = copy(store, workspace_id, fences=fences)
    assert result["source_database_id"] == "database-e1"
    assert result["database_id"] != "database-e1"
    assert result["document_id"] == "page-e1"
    copied = store.iam.read_database("owner", result["database_id"])
    assert copied["title"] == source["title"]
    assert copied["rows"][0]["id"] != source["rows"][0]["id"]
    assert copied["rows"][0]["cells"] != {}
    current = store.iam.read_document("owner", "page-e1")
    blocks = current["blocks"]
    at = next(index for index, block in enumerate(blocks) if block["id"] == result["block_id"])
    assert at > 0
    assert blocks[at - 1]["props"].get("database_id") == "database-e1"
    assert blocks[at]["props"].get("database_id") == result["database_id"]
    prepared = page_copy_source.prepare_page_copy(store.iam, "owner", workspace_id,
        "page-e1", **request(store))
    assert [block["id"] for block in prepared.documents[0].descriptor()["blocks"]].count(result["block_id"]) == 1
    with connection(store) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_databases WHERE id=? AND document_id='page-e1'",
            (result["database_id"],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE client_id='database-copy-service'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action='collaboration.database_copy'").fetchone()[0] == 1
    committed = dump(store)
    assert copy(store, workspace_id, fences=fences) == {**result, "replayed": True}
    assert dump(store) == committed
    status = database_copy.read_owned_database_copy_result(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", operation_id="owned-db-copy", **fences)
    assert status["state"] == "committed"
    assert status["result"]["database_id"] == result["database_id"]
    with pytest.raises(OperationConflict):
        copy(store, workspace_id, fences={**fences,
            "expected_document_sequence": fences["expected_document_sequence"] + 1})


def test_ui_owned_database_copy_failure_rolls_back_all_canonical_rows(environment):
    store, workspace_id = environment
    owned_database_block(store)
    before = dump(store)
    fences = request(store)
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox", "after_operation"):
        with pytest.raises(InjectedFailure):
            copy(store, workspace_id, operation="owned-db-fault-" + fault,
                fences=fences, fault=fault)
        assert dump(store) == before


def test_ui_owned_database_copy_without_content_keeps_empty_independent_database(environment):
    store, workspace_id = environment
    owned_database_block(store)
    source = store.iam.read_database("owner", "database-e1")
    fences = request(store)
    result = copy(store, workspace_id, operation="owned-db-empty-copy",
        fences=fences, include_content=False)
    copied = store.iam.read_database("owner", result["database_id"])
    assert result["include_content"] is False
    assert copied["rows"] == []
    assert [item["name"] for item in copied["properties"]] == [item["name"] for item in source["properties"]]
    assert [item["name"] for item in copied["views"]] == [item["name"] for item in source["views"]]
    assert copy(store, workspace_id, operation="owned-db-empty-copy",
        fences=fences, include_content=False)["replayed"] is True
    with pytest.raises(OperationConflict):
        copy(store, workspace_id, operation="owned-db-empty-copy",
            fences=fences, include_content=True)
    assert database_copy.read_owned_database_copy_result(store.iam, "owner", workspace_id,
        "page-e1", "database-e1", operation_id="owned-db-empty-copy",
        include_content=False, **fences)["state"] == "committed"


def test_ui_owned_database_copy_revalidates_source_after_external_preparation(environment, monkeypatch):
    store, workspace_id = environment
    owned_database_block(store)
    original = database_copy.page_copy._check_capacity
    def change_after_preparation(*args):
        original(*args)
        store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
            target_id="owned-db-concurrent-block", payload={"type": "paragraph",
                "content": "new edit", "after_block_id": "owned-db-source-block"},
            operation_id="owned-db-concurrent-edit", **request(store))
    monkeypatch.setattr(database_copy.page_copy, "_check_capacity", change_after_preparation)
    with pytest.raises(CopySourceChanged):
        copy(store, workspace_id, operation="owned-db-stale-copy")
    with connection(store) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_databases WHERE document_id='page-e1'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action='collaboration.database_copy'").fetchone()[0] == 0


def test_ui_owned_database_copy_respects_page_lock(environment):
    store, workspace_id = environment
    owned_database_block(store)
    update_page_settings(store.iam, "owner", workspace_id, "page-e1",
        updates={"locked": True}, operation_id="owned-db-lock-page", **request(store))
    before = dump(store)
    with pytest.raises(PermissionError, match="locked"):
        copy(store, workspace_id, operation="owned-db-locked-copy")
    assert dump(store) == before


def test_ui_owned_database_copy_requires_same_document_file_schema(appearance_environment):
    store, workspace_id = appearance_environment
    owned_database_block(store)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match="not installed"):
        copy(store, workspace_id, operation="owned-db-before-file-schema")
    assert dump(store) == before


def test_ui_owned_database_copy_preserves_independent_item_body_and_appearance(environment):
    store, workspace_id = environment
    owned_database_block(store)
    row_execute(store.iam, body_request(store.iam, "source-body"))
    row_execute(store.iam, appearance_request(store.iam, "source-appearance"))
    original = store.iam.read_database("owner", "database-e1")["rows"][0]
    result = copy(store, workspace_id, operation="owned-db-copy-rich-row")
    copied = store.iam.read_database("owner", result["database_id"])["rows"][0]
    assert copied["id"] != original["id"]
    assert copied["appearance"] == original["appearance"]
    assert [block["content"] for block in copied["body"]["blocks"]] == [
        block["content"] for block in original["body"]["blocks"]]
    assert {block["id"] for block in copied["body"]["blocks"]}.isdisjoint(
        block["id"] for block in original["body"]["blocks"])
    row_execute(store.iam, body_request(store.iam, "change-source-body",
        payload={"blocks": row_blocks("changed after copy")}))
    appearance_change = appearance_request(store.iam, "change-source-appearance")
    appearance_change["payload"] = {"icon": "⭐"}
    row_execute(store.iam, appearance_change)
    current = store.iam.read_database("owner", result["database_id"])["rows"][0]
    assert current["body"] == copied["body"] and current["appearance"] == copied["appearance"]


def test_ui_owned_database_copy_file_survives_source_deletion(environment):
    store, workspace_id = environment
    owned_database_block(store)
    original = upload(store)["file_id"]
    source_row = store.iam.read_database("owner", "database-e1")["rows"][0]["id"]
    source_property = "owned-db-file-property"
    _schema(store.iam, "database.property.create", source_property,
        {"name": "Files", "type": "files", "config": {}}, "owned-db-add-file-property")
    _schema(store.iam, "database.template.create", "owned-db-file-template",
        {"name": "File starter", "body": [],
         "default_values": {source_property: [{"file_id": original}]}},
        "owned-db-file-template-create")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id=f"{source_row}:{source_property}", payload={"row_id": source_row,
            "property_id": source_property, "value": [{"file_id": original}]},
        expected_target_version=0, operation_id="owned-db-attach-file", **request(store))
    row_execute(store.iam, body_request(store.iam, "owned-db-file-body",
        payload={"blocks": [{"id": "owned-db-body-file", "type": "file",
            "content": "Attachment in item body", "props": {"file_id": original}}]}))
    result = copy(store, workspace_id, operation="owned-db-copy-attachment")
    with connection(store) as con:
        files = [dict(row) for row in con.execute(
            "SELECT * FROM workspace_collaboration_files WHERE source_file_id=? AND copy_operation_id=?",
            (original, "owned-db-copy-attachment"))]
    assert len(files) == 1
    copied_file = files[0]["id"]
    assert copied_file != original
    assert files[0]["source_document_id"] == files[0]["document_id"] == "page-e1"
    copied = store.iam.read_database("owner", result["database_id"])
    target_property = next(item["id"] for item in copied["properties"] if item["type"] == "files")
    assert copied["rows"][0]["cells"][target_property] == [{"file_id": copied_file}]
    copied_template = next(item for item in copied["templates"] if item["name"] == "File starter")
    assert copied_template["default_values"][target_property][0]["file_id"] == copied_file
    assert copied_template["default_values"][target_property][0]["name"] == "evidence.bin"
    assert copied["rows"][0]["body"]["blocks"][0]["props"]["file_id"] == copied_file
    assert copied["rows"][0]["body"]["blocks"][0]["id"] != "owned-db-body-file"
    assert store.iam.get_file("owner", original)["document_id"] == "page-e1"
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id=f"{source_row}:{source_property}", payload={"row_id": source_row,
            "property_id": source_property, "value": []},
        expected_target_version=1, operation_id="owned-db-remove-original-cell-file", **request(store))
    store.iam.delete_file("owner", original, operation_id="owned-db-delete-original-file", **request(store))
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", original)
    assert store.iam.read_database("owner", result["database_id"])["rows"][0]["cells"][target_property] == [{"file_id": copied_file}]
    assert store.iam.read_database("owner", result["database_id"])["rows"][0]["body"]["blocks"][0]["props"]["file_id"] == copied_file
    assert store.iam.get_file("owner", copied_file)["path"].read_bytes() == b"canonical copy evidence"
