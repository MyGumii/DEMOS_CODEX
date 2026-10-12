"""UI-DB-ITEM-BODY: canonical content, current authorization and recoverable history."""
from copy import deepcopy
import multiprocessing
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_row_body_migration as migration
from code_assist_v1 import notion_guest_navigation, notion_member_navigation
from code_assist_v1.notion_collaboration import database_row_content, page_creation, page_archive, page_copy
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingTransportAuthority, DocumentSequenceConflict, TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.history_authority import HistoryIntegrityError
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_native_team_creation import (
    environment as team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment, _apply, _backup, dump,
    test_ui_team_create_member_hierarchy_and_revoked_creator_do_not_bypass_team_access as verify_team_access,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema, _surface


@pytest.fixture()
def environment(team_environment):
    old, ws = team_environment
    _apply(migration, old.db_path, old.user_data_root, "before-row-body-2418.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
                                        identity_directory=old.identity_directory), ws


def blocks(text="항목 본문"):
    return [{"id": "item-heading", "type": "heading1", "content": text, "props": {}},
            {"id": "item-paragraph", "type": "paragraph", "content": "줄 바꿈\n두 번째 줄",
             "props": {"rich_text": [{"text": "강조", "bold": True}], "indent": 1}}]


def test_template_body_is_copied_with_independent_block_ids(environment):
    store, _workspace = environment
    authority = store.iam
    source_body = blocks("템플릿 본문")
    _schema(authority, "database.template.create", "template-body",
            {"name": "Body", "body": source_body, "default_values": {}},
            "template-body-create")
    first = _surface(authority, "database.row.create", "row-from-template-one",
                     {"template_id": "template-body"}, "template-body-row-one",
                     target_version=0)
    second = _surface(authority, "database.row.create", "row-from-template-two",
                      {"template_id": "template-body"}, "template-body-row-two",
                      target_version=0)
    assert first["body_block_count"] == second["body_block_count"] == 2
    rows = {row["id"]: row for row in authority.read_database("content-editor", "database-e1")["rows"]}
    first_blocks = rows["row-from-template-one"]["body"]["blocks"]
    second_blocks = rows["row-from-template-two"]["body"]["blocks"]
    assert [block["content"] for block in first_blocks] == [block["content"] for block in second_blocks]
    assert {block["id"] for block in first_blocks}.isdisjoint({block["id"] for block in second_blocks})
    assert {block["id"] for block in first_blocks}.isdisjoint({block["id"] for block in source_body})


def test_empty_existing_item_can_apply_template_without_overwriting_its_title(environment):
    store, _workspace = environment
    authority = store.iam
    _schema(authority, "database.property.create", "property-template-note",
            {"name": "Note", "type": "text", "config": {}}, "template-note-property")
    source_body = blocks("선택한 템플릿")
    _schema(authority, "database.template.create", "template-existing",
            {"name": "Existing", "body": source_body,
             "default_values": {"property-e1": "템플릿 제목",
                                "property-template-note": "기본 메모"}},
            "template-existing-create")
    before = next(row for row in authority.read_database("content-editor", "database-e1")["rows"]
                  if row["id"] == "row-e1")
    result = _surface(authority, "database.row.template.apply", "row-e1",
                      {"template_id": "template-existing"}, "template-existing-apply",
                      target_version=before["version"])
    after = next(row for row in authority.read_database("content-editor", "database-e1")["rows"]
                 if row["id"] == "row-e1")
    assert result["target_version"] == before["version"] + 1
    assert result["applied_defaults"] == {"property-template-note": "기본 메모"}
    assert after["cells"]["property-e1"] == before["cells"]["property-e1"]
    assert after["cells"]["property-template-note"] == "기본 메모"
    assert [block["content"] for block in after["body"]["blocks"]] == [
        block["content"] for block in source_body]
    assert {block["id"] for block in after["body"]["blocks"]}.isdisjoint(
        {block["id"] for block in source_body})
    with pytest.raises(ValueError, match="positive expected_target_version"):
        _surface(authority, "database.row.template.apply", "row-e1",
                 {"template_id": "template-existing"}, "template-no-version")
    with pytest.raises(ValueError, match="empty body"):
        _surface(authority, "database.row.template.apply", "row-e1",
                 {"template_id": "template-existing"}, "template-existing-again",
                 target_version=after["version"])
    _schema(authority, "database.property.create", "property-template-number",
            {"name": "Estimate", "type": "number", "config": {}},
            "template-invalid-number-property")
    _schema(authority, "database.template.create", "template-invalid-existing",
            {"name": "Invalid", "body": source_body,
             "default_values": {"property-template-note": "must roll back",
                                "property-template-number": "wrong type"}},
            "template-invalid-existing-create")
    _surface(authority, "database.row.create", "row-empty-for-template", {},
             "template-empty-row-create", target_version=0)
    empty = next(row for row in authority.read_database("content-editor", "database-e1")["rows"]
                 if row["id"] == "row-empty-for-template")
    sequence = authority.read_document("content-editor", "page-e1")["document_sequence"]
    with pytest.raises(ValueError, match="number property"):
        _surface(authority, "database.row.template.apply", empty["id"],
                 {"template_id": "template-invalid-existing"},
                 "template-invalid-existing-apply", target_version=empty["version"])
    unchanged = next(row for row in authority.read_database("content-editor", "database-e1")["rows"]
                     if row["id"] == empty["id"])
    assert unchanged == empty
    assert authority.read_document("content-editor", "page-e1")["document_sequence"] == sequence


def request(authority, operation="item-body-save", **changes):
    page = authority.read_document("content-editor", "page-e1")
    row = next(row for row in authority.read_database("content-editor", "database-e1")["rows"] if row["id"] == "row-e1")
    return {"database_id": "database-e1", "target_id": "row-e1", "command_kind": "database.row.content.set",
            "payload": {"blocks": blocks()}, "expected_target_version": row["version"],
            "expected_document_sequence": page["document_sequence"], "expected_workspace_epoch": page["workspace_epoch"],
            "expected_document_epoch": page["document_epoch"], "operation_id": operation, **changes}


def execute(authority, value, actor="content-editor"):
    return authority.execute_surface_command(actor, "page-e1", **value)


def checkpoint(authority, operation):
    page = authority.read_document("owner", "page-e1")
    return authority.create_revision_checkpoint("owner", "page-e1", operation_id=operation,
        expected_revision=page["revision"], expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def restore(authority, revision, operation):
    page = authority.read_document("owner", "page-e1")
    return authority.restore_revision("owner", "page-e1", revision, operation_id=operation,
        expected_revision=page["revision"], expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def test_ui_db_item_body_forward_migration_exact_and_preserves_old_hashes(team_environment):
    store, ws = team_environment
    before = dump(store)
    with store.iam._reader() as con:
        original = store.iam._editing_snapshot(con, store.iam._document(con, "page-e1"))
    assert not store.capabilities()["database_row_content"]
    assert not store.iam.read_database("owner", "database-e1")["capabilities"]["row_content"]
    with pytest.raises(AuthorityUnavailable): execute(store.iam, request(store.iam))
    plan = migration.build_plan(store.db_path, store.user_data_root)
    backup = _backup(store.db_path, store.user_data_root / "backups" / "row-body-plan.db")
    options = {"exact_plan_checksum": plan["plan_checksum"], "backup_confirmed": True,
               "catalog_quiesced_confirmed": True, "backup_path": backup}
    for invalid in ({"backup_confirmed": False}, {"catalog_quiesced_confirmed": False},
                    {"exact_plan_checksum": "sha256:" + "0" * 64}):
        with pytest.raises(migration.MigrationRefused):
            migration.apply_plan(store.db_path, store.user_data_root, **{**options, **invalid})
        assert dump(store) == before
    def fail(stage):
        if stage == "after_schema": raise RuntimeError("synthetic schema failure")
    with pytest.raises(RuntimeError):
        migration.apply_plan(store.db_path, store.user_data_root, **options, fault_hook=fail)
    assert dump(store) == before
    migration.apply_plan(store.db_path, store.user_data_root, **options)
    inserts = lambda lines: [line for line in lines if line.startswith('INSERT INTO "') and not line.startswith('INSERT INTO "workspace_schema_migrations"')]
    assert inserts(dump(store)) == inserts(before)
    with store.iam._reader() as con:
        assert store.iam._editing_snapshot(con, store.iam._document(con, "page-e1")) == original
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"


def test_ui_db_item_body_commit_replay_restart_preserves_cells_and_namespace(environment):
    store, ws = environment; authority = store.iam
    page = authority.read_document("owner", "page-e1")
    database = authority.read_database("owner", "database-e1")
    value = request(authority); first = execute(authority, value)
    assert first["target_version"] == value["expected_target_version"] + 1
    assert first["document_sequence"] == value["expected_document_sequence"] + 1
    assert first["body"]["blocks"] == database_row_content.clean_blocks(blocks())
    after = dump(store)
    assert execute(authority, value) == first and dump(store) == after
    with pytest.raises(OperationConflict): execute(authority, {**value, "payload": {"blocks": blocks("different")}})
    restarted = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
                                            identity_directory=store.identity_directory)
    row = restarted.iam.read_database("owner", "database-e1")["rows"][0]
    assert row["body"] == first["body"]
    assert row["cells"] == database["rows"][0]["cells"] and row["cell_versions"] == database["rows"][0]["cell_versions"]
    assert restarted.iam.read_document("owner", "page-e1")["blocks"] == page["blocks"]
    with authority._reader() as con:
        document = authority._document(con, "page-e1")
        assert document["content_sha256"] == _sha256(_canonical_json(authority._editing_snapshot(con, document)))
        for table in ("events", "audit_events", "outbox", "operations"):
            assert con.execute(f"SELECT COUNT(*) FROM workspace_collaboration_{table} WHERE operation_id=?", (value["operation_id"],)).fetchone()[0] == 1


def test_ui_db_item_body_validation_cas_and_faults_are_atomic(environment):
    store, ws = environment; authority = store.iam; value = request(authority); before = dump(store)
    for version in (None, 0, True, "1", 1.5):
        with pytest.raises(ValueError): execute(authority, {**value, "expected_target_version": version})
    for payload in ({}, {"blocks": [], "title": "not a body field"}, {"blocks": "text"},
                    {"blocks": [{"id": "a", "content": {}}]}, {"blocks": blocks() * 2},
                    {"blocks": blocks() * 501}, {"blocks": [{"id": "a", "content": "가" * 400000}]},
                    {"blocks": [{"id": "a", "content": "", "props": {"number": float("nan")}}]}):
        with pytest.raises(ValueError): execute(authority, {**value, "payload": payload})
    with pytest.raises(TargetVersionConflict): execute(authority, {**value, "expected_target_version": 55})
    with pytest.raises(DocumentSequenceConflict): execute(authority, {**value, "expected_document_sequence": 99999})
    with pytest.raises(NotFoundOrDenied): execute(authority, {**value, "database_id": "wrong-database"})
    with pytest.raises(NotFoundOrDenied): execute(authority, {**value, "target_id": "wrong-row"})
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox"):
        with pytest.raises(InjectedFailure): execute(authority, {**value, "fault": fault})
        assert dump(store) == before


def test_ui_db_item_body_replay_requires_current_grants(environment):
    store, ws = environment; authority = store.iam; value = request(authority)
    execute(authority, value)
    page = authority.read_document("owner", "page-e1")
    authority.set_page_grant("owner", "page-e1", "subject", "content-editor", role="view",
        inherit_to_descendants=False, expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="body-remove-edit")
    before = dump(store)
    with pytest.raises(NotFoundOrDenied): execute(authority, value)
    assert dump(store) == before
    assert authority.read_database("content-editor", "database-e1")["rows"][0]["body"]["blocks"]
    page = authority.read_document("owner", "page-e1")
    authority.revoke_page_grant("owner", "page-e1", "subject", "content-editor",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="body-revoke-read")
    with pytest.raises(NotFoundOrDenied): authority.read_database("content-editor", "database-e1")


def test_ui_db_item_body_history_roundtrip_and_monotonic_versions(environment):
    store, ws = environment; authority = store.iam
    empty = checkpoint(authority, "body-empty-checkpoint")
    first = execute(authority, request(authority))
    saved = checkpoint(authority, "body-checkpoint")
    assert authority.get_revision("owner", "page-e1", saved["revision"])["snapshot"]["databases"][0]["rows"][0]["body"] == first["body"]
    execute(authority, request(authority, "body-changed", payload={"blocks": blocks("새 본문")}))
    restored = restore(authority, saved["revision"], "body-restore")
    row = authority.read_database("owner", "database-e1")["rows"][0]
    assert row["body"] == first["body"] and row["version"] == first["target_version"] + 2
    assert authority.get_revision("owner", "page-e1", restored["revision"])["snapshot"]["databases"][0]["rows"][0]["body"] == first["body"]
    restore(authority, empty["revision"], "body-restore-empty")
    cleared = authority.read_database("owner", "database-e1")["rows"][0]
    assert "body" not in cleared and cleared["version"] == row["version"] + 1


def test_ui_db_item_body_row_delete_cascades_but_history_cannot_partially_restore(environment):
    store, ws = environment; authority = store.iam
    execute(authority, request(authority))
    saved = checkpoint(authority, "body-before-delete")
    execute(authority, request(authority, "delete-item", command_kind="database.row.delete", payload={}))
    with authority._reader() as con:
        assert not con.execute("SELECT * FROM workspace_collaboration_database_row_bodies").fetchall()
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    before = dump(store)
    with pytest.raises(HistoryIntegrityError, match="database history restore is unavailable"):
        restore(authority, saved["revision"], "deleted-body-restore")
    assert dump(store) == before
    assert authority.get_revision("owner", "page-e1", saved["revision"])["snapshot"]["databases"][0]["rows"][0]["body"]["blocks"]


def test_ui_db_item_body_normal_document_save_keeps_body_in_revision_and_hash(environment):
    store, ws = environment; authority = store.iam
    first = execute(authority, request(authority))
    page = authority.read_document("owner", "page-e1")
    saved = authority.save_document("owner", "page-e1", title="페이지 제목 변경", blocks=page["blocks"],
        expected_revision=page["revision"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="normal-page-save-with-row-body")
    snapshot = authority.get_revision("owner", "page-e1", saved["revision"])["snapshot"]
    assert snapshot["databases"][0]["rows"][0]["body"] == first["body"]
    with authority._reader() as con:
        current = authority._document(con, "page-e1")
        assert snapshot == authority._editing_snapshot(con, current)
        assert saved["content_sha256"] == _sha256(_canonical_json(snapshot)) == current["content_sha256"]
    execute(authority, request(authority, "after-normal-save", payload={"blocks": blocks("변경된 항목")}))
    restore(authority, saved["revision"], "restore-normal-page-save")
    assert authority.read_database("owner", "database-e1")["rows"][0]["body"] == first["body"]


def test_ui_db_item_body_http_uses_existing_command_contract(environment, monkeypatch):
    store, ws = environment; client = client_for(store, monkeypatch, actor="content-editor")
    value = request(store.iam); path = f"/api/notion/workspaces/{ws}/pages/page-e1/databases"
    result = client.post(path + "/commands", json=value)
    assert result.status_code == 201, result.json
    assert client.post(path + "/commands", json=value).json == result.json
    assert client.post(path + "/commands", json={**value, "expected_target_version": None}).status_code == 400
    assert client.post(path.replace(ws, "wrong-workspace") + "/commands", json=value).status_code == 404
    loaded = client.get(path + "/database-e1")
    assert loaded.status_code == 200 and loaded.json["database"]["rows"][0]["body"] == result.json["body"]


def test_ui_db_item_body_successor_keeps_navigation_team_and_transport_gates(environment):
    store, ws = environment; caps = store.capabilities()
    for feature in ("database_row_content", "collaborative_team_page_creation", "collaborative_page_creation",
                    "collaborative_page_archive", "guest_navigation", "live_collaboration", "collaborative_editing",
                    "database_features", "collaborative_files", "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[feature], feature
    assert not caps["collaborative_page_copy"]
    with store.iam._reader() as con:
        for module in (page_creation, page_archive, notion_guest_navigation, notion_member_navigation):
            assert module.marker_current(con)
        # The staged copy service can check current ownership at this schema;
        # its public capability remains disabled until the copy gate is approved.
        assert page_copy._owner(store.iam, con, "owner", ws)["id"] == ws
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    transport._connect(query_only=True).close()
    verify_team_access(environment)


def _writer(catalog, root, value, barrier, queue):
    try:
        store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
            identity_directory=InMemoryIdentityDirectory(["owner", "content-editor"]))
        barrier.wait(30)
        execute(store.iam, value)
        queue.put("committed")
    except DocumentSequenceConflict:
        queue.put("conflict")
    except Exception as exc:
        queue.put(type(exc).__name__ + ": " + str(exc))


def test_ui_db_item_body_concurrent_processes_do_not_overwrite(environment):
    store, ws = environment; value = request(store.iam)
    context = multiprocessing.get_context("spawn"); barrier = context.Barrier(2); queue = context.Queue()
    competing = {**deepcopy(value), "operation_id": "competing-body", "payload": {"blocks": blocks("competing")}}
    processes = [context.Process(target=_writer, args=(store.db_path, store.user_data_root, body, barrier, queue))
                 for body in (value, competing)]
    try:
        for process in processes: process.start()
        results = sorted(queue.get(timeout=60) for _ in processes)
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert results == ["committed", "conflict"]
        assert store.iam.read_database("owner", "database-e1")["rows"][0]["version"] == value["expected_target_version"] + 1
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(10)
        queue.close()
