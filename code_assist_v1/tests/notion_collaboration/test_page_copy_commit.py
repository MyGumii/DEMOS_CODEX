"""UI-PAGE-COPY: actual atomic service writes, recovery and independent content."""
from contextlib import contextmanager
import base64
from io import BytesIO
import json
import multiprocessing
import time
from types import SimpleNamespace

import pytest

from code_assist_v1.notion_collaboration import page_copy as copying, page_copy_source, page_move
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict, _sha256, bind_temporary_unlock
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance, _image_descriptor
from code_assist_v1.notion_collaboration.page_settings import current_page_lock_generation, update_page_settings
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    create, upload, connection, request, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, insert_block
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_page_archive import change


def copy(store, ws, doc="page-e1", operation="copy-service-operation", **options):
    return copying.copy_page(store.iam, "owner", ws, doc, operation_id=operation,
        **(options.pop("fences", None) or request(store, doc)), **options)


def registrations(store):
    with connection(store) as con:
        return [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_native_pages WHERE creation_kind='copy' ORDER BY document_id")]


def test_ui_copy_preserves_typed_insert_after_collaborative_frame(environment):
    store, ws = environment
    current_update(store, ws)
    insert_block(store, 'page-e1', 'copy-after-frame-note')
    source = store.iam.read_document('owner', 'page-e1')
    assert next(block for block in source['blocks'] if block['id'] == 'copy-after-frame-note')
    result = copy(store, ws, operation='copy-after-frame-note-operation')
    copied = store.iam.read_document('owner', result['page']['id'])
    assert next(block for block in copied['blocks'] if block['id'] != 'copy-after-frame-note'
                and block['content'] == 'concurrent content')


def test_ui_copy_locked_source_keeps_independent_copy_lock(environment):
    store, ws = environment
    update_page_settings(store.iam, "owner", ws, "page-e1", updates={"locked": True},
                         operation_id="copy-lock-source", **request(store))
    source_generation = current_page_lock_generation(store.iam, "owner", ws, "page-e1")
    result = copy(store, ws, operation="copy-locked-source")
    copied_id = result["page"]["id"]
    copied = store.iam.read_document("owner", copied_id)
    assert copied["settings"]["locked"] is True
    assert current_page_lock_generation(store.iam, "owner", ws, copied_id) != source_generation
    old_source_grant = {"user_id": "owner", "workspace_id": ws, "page_id": "page-e1",
                        "lock_generation": source_generation, "expires_at_ms": int(time.time() * 1000) + 60_000}
    with bind_temporary_unlock(old_source_grant):
        with pytest.raises(PermissionError, match="locked"):
            store.iam.execute_typed_command("owner", copied_id, command_kind="block.insert",
                target_id="copy-locked-attempt", payload={"type": "paragraph", "content": "blocked", "after_block_id": None},
                operation_id="copy-locked-write-attempt", **request(store, copied_id))


def test_ui_copy_commit_current_subtree_database_files_and_independence(environment, monkeypatch):
    store, ws = environment
    current_update(store, ws)
    child = create(store, ws, operation="source-child-create")["page"]["id"]
    grandchild = create(store, ws, parent=child, operation="source-grandchild-create")["page"]["id"]
    original_file = upload(store)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6xj8AAAAASUVORK5CYII=")
    icon_session = store.iam.begin_upload("owner", "page-e1", original_name="icon.png", mime_type="image/png",
        total_size=len(png), expected_sha256=_sha256(png), chunk_size=65536,
        operation_id="copy-icon-upload-begin", **request(store))
    store.iam.stage_chunk("owner", icon_session["session_id"], chunk_index=0, stream=BytesIO(png), operation_id="copy-icon-upload-chunk")
    icon_file = store.iam.finalize_upload("owner", icon_session["session_id"], operation_id="copy-icon-upload-final")["file_id"]
    update_page_appearance(store.iam, "owner", ws, "page-e1", updates={"icon": "nf:" + icon_file},
        operation_id="copy-source-image-icon", **request(store))
    _schema(store.iam, "database.property.create", "copy-number", {"name": "Amount", "type": "number", "config": {}}, "copy-amount-property")
    _schema(store.iam, "database.property.create", "copy-files", {"name": "Attachments", "type": "files", "config": {}}, "copy-files-property")
    _schema(store.iam, "database.property.create", "copy-formula", {"name": "Twice", "type": "formula", "config": {"expression": 'prop("copy-number") * 2'}}, "copy-formula-property")
    source_row = store.iam.read_database("owner", "database-e1")["rows"][0]["id"]
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id=f"{source_row}:copy-number", payload={"row_id": source_row, "property_id": "copy-number", "value": 9},
        expected_target_version=0, operation_id="copy-source-amount", **request(store))
    _schema(store.iam, "database.view.create", "copy-view", {"name": "Table", "type": "table", "config": {"sorts": [{"property": "copy-number", "direction": "desc"}]}}, "copy-source-view")
    for suffix in ("z", "a"):
        _schema(store.iam, "database.template.create", "copy-template-" + suffix,
            {"name": "Template " + suffix, "body": [] if suffix == "z" else [{"id": "template-block-" + suffix, "type": "paragraph", "content": "keep text", "props": {}}],
             "default_values": {"copy-number": 11, **({"copy-files": [{"file_id": original_file["file_id"]}, {"url": "https://example.com/evidence.pdf", "name": "Evidence"}]} if suffix == "z" else {})}},
            "copy-source-template-" + suffix)
    before = set(dump(store))
    with connection(store) as con:
        search_before = dict(con.execute("SELECT * FROM workspace_collaboration_search_state WHERE workspace_id=?", (ws,)).fetchone())
    source_db = store.iam.read_database("owner", "database-e1")
    source = page_copy_source.prepare_page_copy(store.iam, "owner", ws, "page-e1", **request(store))
    opened = 0
    original_reader, original_transaction = store.iam._reader, store.iam._transaction
    @contextmanager
    def tracked(method):
        nonlocal opened
        with method() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    monkeypatch.setattr(store.iam, "_reader", lambda: tracked(original_reader))
    monkeypatch.setattr(store.iam, "_transaction", lambda: tracked(original_transaction))
    for target, name in ((page_copy_source.search_projection, "materialize"), (page_copy_source, "_verify_blobs"),
                         (page_move, "_prepare_deltas"), (copying, "_check_capacity")):
        original = getattr(target, name)
        def outside(*args, original=original, **kwargs):
            assert opened == 0, "Node/blob/capacity work must run outside database transactions"
            return original(*args, **kwargs)
        monkeypatch.setattr(target, name, outside)
    result = copy(store, ws)
    copied_id = result["page"]["id"]
    assert len(result["page_ids"]) == 3
    assert result["page"]["title"] == "최신 협업 복제 제목"
    after = set(dump(store))
    changed = sorted(line for line in before - after if not line.startswith(('INSERT INTO "workspaces"', 'INSERT INTO "workspace_collaboration_search_state"')))
    assert not changed, [line[:300] for line in changed]
    with connection(store) as con:
        search_after = dict(con.execute("SELECT * FROM workspace_collaboration_search_state WHERE workspace_id=?", (ws,)).fetchone())
    assert search_after.pop("desired_generation") > search_before.pop("desired_generation")
    assert search_after == search_before, "only the existing search invalidation counter advances"
    mapped = {row["source_document_id"]: row["document_id"] for row in registrations(store)}
    assert set(mapped) == {"page-e1", child, grandchild}
    copied_source = page_copy_source.prepare_page_copy(store.iam, "owner", ws, copied_id, **request(store, copied_id))
    root_descriptor = copied_source.documents[0].descriptor()
    assert root_descriptor["blocks"][0]["content"] == source.documents[0].descriptor()["blocks"][0]["content"]
    assert any(block["props"].get("page_id") == mapped[child] for block in root_descriptor["blocks"])
    assert copied_source.documents[1].parent_id == copied_id
    assert copied_source.documents[2].parent_id == mapped[child]
    copied_db = store.iam.read_database("owner", root_descriptor["databases"][0]["id"])
    number = next(prop["id"] for prop in copied_db["properties"] if prop["name"] == "Amount")
    formula = next(prop["id"] for prop in copied_db["properties"] if prop["name"] == "Twice")
    assert copied_db["rows"][0]["computed"][formula] == 18
    assert [item["name"] for item in copied_db["templates"]] == [item["name"] for item in source_db["templates"]]
    assert copied_db["templates"][0]["default_values"][number] == 11
    assert copied_db["views"][0]["config"]["sorts"][0]["property"] == number
    with connection(store) as con:
        copied_file = dict(con.execute("SELECT * FROM workspace_collaboration_files WHERE document_id=? AND source_file_id=?", (copied_id, original_file["file_id"])).fetchone())
        assert copied_file["origin_kind"] == "copy" and copied_file["upload_session_id"] is None
        assert copied_file["source_file_id"] == original_file["file_id"]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        for target in result["page_ids"]:
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_revisions WHERE document_id=?", (target,)).fetchone()[0] == 1
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_revision_checkpoints WHERE document_id=?", (target,)).fetchone()[0] == 1
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_events WHERE document_id=?", (target,)).fetchone()[0] == 1
    copied_files = next(prop["id"] for prop in copied_db["properties"] if prop["name"] == "Attachments")
    file_template = next(item for item in copied_db["templates"] if item["name"] == "Template z")
    assert file_template["default_values"][copied_files][0]["file_id"] == copied_file["id"]
    assert file_template["default_values"][copied_files][0]["name"] == "evidence.bin"
    assert file_template["default_values"][copied_files][1] == {"url": "https://example.com/evidence.pdf", "name": "Evidence"}
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("content-editor", copied_file["id"])
    store.iam.delete_file("owner", original_file["file_id"], operation_id="copy-delete-original-file", **request(store))
    store.iam.delete_file("owner", icon_file, operation_id="copy-delete-original-icon", **request(store))
    change(store, ws, "page-e1", operation="copy-archive-original-tree")
    assert store.iam.get_file("owner", copied_file["id"])["path"].read_bytes() == b"canonical copy evidence"
    templated = store.iam.execute_surface_command(
        "owner", copied_id, database_id=copied_db["id"],
        command_kind="database.row.create", target_id="copied-file-template-row",
        payload={"template_id": file_template["id"]}, expected_target_version=0,
        operation_id="copy-apply-file-template", **request(store, copied_id))
    assert templated["applied_defaults"][copied_files][0]["file_id"] == copied_file["id"]
    assert templated["applied_defaults"][copied_files][1] == {"url": "https://example.com/evidence.pdf", "name": "Evidence"}
    assert result["page"]["icon"] != "nf:" + icon_file
    assert _image_descriptor(store.iam, "owner", copied_id, result["page"]["icon"])["path"].read_bytes() == png
    assert store.iam.read_document("owner", mapped[grandchild])["title"] == "새 하위 페이지"
    store.iam.delete_file("owner", copied_file["id"], operation_id="copy-delete-for-history", **request(store, copied_id))
    current_page = store.iam.read_document("owner", copied_id)
    store.iam.restore_revision("owner", copied_id, 1, expected_revision=current_page["revision"],
        operation_id="copy-restore-initial-history", **request(store, copied_id))
    assert store.iam.get_file("owner", copied_file["id"])["path"].read_bytes() == b"canonical copy evidence"
    copied_row = copied_db["rows"][0]["id"]
    store.iam.execute_typed_command("owner", copied_id, command_kind="database.cell.set", target_id=f"{copied_row}:{number}",
        payload={"row_id": copied_row, "property_id": number, "value": 12}, expected_target_version=1,
        operation_id="copy-change-independent-cell", **request(store, copied_id))
    with connection(store) as con:
        assert json.loads(con.execute("SELECT value_json FROM workspace_collaboration_database_cells WHERE row_id=? AND property_id='copy-number'", (source_row,)).fetchone()[0]) == 9
    again = copy(store, ws, copied_id, operation="copy-copy-again")
    again_data = page_copy_source.prepare_page_copy(store.iam, "owner", ws, again["page"]["id"], **request(store, again["page"]["id"]))
    again_db = store.iam.read_database("owner", again_data.documents[0].descriptor()["databases"][0]["id"])
    again_formula = next(prop["id"] for prop in again_db["properties"] if prop["name"] == "Twice")
    assert again_db["rows"][0]["computed"][again_formula] == 24
    assert [item["name"] for item in again_db["templates"]] == [item["name"] for item in source_db["templates"]]


def test_ui_copy_commit_parent_current_link_and_exact_recovery(environment):
    store, ws = environment
    current_update(store, ws)
    child = create(store, ws, operation="copy-parent-source")["page"]["id"]
    fences = request(store, child)
    from code_assist_v1.tests.notion_collaboration.test_page_move import current_body
    before = current_body(store.iam, "page-e1")
    result = copy(store, ws, child, fences=fences)
    parent = current_body(store.iam, "page-e1")
    assert parent["title"] == before["title"]
    assert parent["blocks"][:-1] == before["blocks"]
    assert parent["blocks"][-1]["props"]["page_id"] == result["page"]["id"]
    committed = dump(store)
    assert copy(store, ws, child, fences=fences) == {**result, "replayed": True}
    assert dump(store) == committed
    with pytest.raises(OperationConflict):
        copy(store, ws, child, fences={**fences, "expected_document_sequence": fences["expected_document_sequence"] + 1})
    change(store, ws, child, operation="copy-recovery-source-archive")
    args = dict(operation_id="copy-service-operation", **fences)
    assert copying.read_page_copy_result(store.iam, "owner", ws, child, **args)["state"] == "committed"
    assert copy(store, ws, child, fences=fences)["page"]["id"] == result["page"]["id"]
    change(store, ws, result["page"]["id"], operation="copy-recovery-destination-archive")
    assert copying.read_page_copy_result(store.iam, "owner", ws, child, **args)["current"]["archived"] is True
    with pytest.raises(NotFoundOrDenied):
        copying.read_page_copy_result(store.iam, "outsider", ws, child, **args)


def test_ui_copy_commit_rollback_covers_all_documents_ledgers_and_parent(environment):
    store, ws = environment
    child = create(store, ws, operation="copy-rollback-source")["page"]["id"]
    create(store, ws, parent=child, operation="copy-rollback-child")
    before = dump(store)
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox", "after_operation"):
        with pytest.raises(InjectedFailure):
            copy(store, ws, child, operation="copy-rollback-" + fault, fault=fault)
        assert dump(store) == before, fault
        assert registrations(store) == []


@pytest.mark.parametrize("concurrent", ["source", "parent", "identity"])
def test_ui_copy_commit_revalidates_after_all_preparation(environment, monkeypatch, concurrent):
    store, ws = environment
    child = create(store, ws, operation="copy-race-source")["page"]["id"]
    original = copying._check_capacity
    after_change = []
    def mutate_after_preparation(*args):
        original(*args)
        if concurrent == "identity":
            monkeypatch.setattr(store.iam.identity_directory, "is_active_human", lambda subject: False)
        else:
            insert_block(store, child if concurrent == "source" else "page-e1", "copy-race-" + concurrent)
        after_change.extend(dump(store))
    monkeypatch.setattr(copying, "_check_capacity", mutate_after_preparation)
    with pytest.raises((page_copy_source.CopySourceChanged, NotFoundOrDenied)):
        copy(store, ws, child)
    assert dump(store) == tuple(after_change) and registrations(store) == []


def test_ui_copy_commit_capacity_fails_without_writes(environment, monkeypatch):
    store, ws = environment
    before = dump(store)
    monkeypatch.setattr(copying.shutil, "disk_usage", lambda path: SimpleNamespace(total=100, free=0))
    with pytest.raises(copying.CapacityUnavailable):
        copy(store, ws)
    assert dump(store) == before


def test_ui_copy_commit_old_schema_fails_without_writes(member_environment):
    store, ws = member_environment
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        copy(store, ws)
    assert dump(store) == before


def test_ui_copy_plan_keeps_tied_sibling_database_and_template_order():
    from copy import deepcopy
    from dataclasses import replace
    from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import fixture
    prepared = fixture()
    root, child = prepared.documents
    content = root.descriptor()
    original_db = content["databases"][0]
    second_template = deepcopy(original_db["templates"][0])
    second_template.update(id="another-template", name="Second template")
    original_db["templates"].append(second_template)
    sibling_content = {"title": "Sibling", "icon": "", "cover": "", "settings": {}, "blocks": [], "databases": [], "files": []}
    sibling = replace(child, document_id="page-c", descriptor_json=json.dumps(sibling_content), files_json="[]")
    prepared = replace(prepared, documents=(replace(root, descriptor_json=json.dumps(content)), child, sibling))
    plan = copying.page_copy_remap.remap_page_copy(prepared, "copy-order-ties")
    assert [item.position for item in plan.documents] == [3, 0, 1]
    copied_db = copying._contents(plan)[plan.documents[0].document_id]["databases"][0]
    assert [item["name"] for item in copied_db["properties"]] == [item["name"] for item in original_db["properties"]]
    assert [item["position"] for item in copied_db["properties"]] == list(range(len(original_db["properties"])))
    assert [item["name"] for item in copied_db["templates"]] == [item["name"] for item in original_db["templates"]]


def _copy_process(catalog, data_root, workspace, fences, ready, start, prepared_barrier, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    store = CollaborativeWorkspaceStore(catalog, user_data_root=data_root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    ready.put(True)
    if not start.wait(30):
        results.put({"error": "start barrier timeout"})
        return
    capacity = copying._check_capacity
    def both_prepared(*args):
        capacity(*args)
        prepared_barrier.wait(timeout=30)
    copying._check_capacity = both_prepared
    try:
        results.put(copy(store, workspace, fences=fences))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_copy_commit_two_process_same_operation_has_one_subtree(environment):
    store, ws = environment
    create(store, ws, operation="copy-process-source-child")
    fences = request(store)
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    prepared_barrier = context.Barrier(2)
    workers = [context.Process(target=_copy_process, args=(store.db_path, store.user_data_root, ws, fences, ready, start, prepared_barrier, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert ready.get(timeout=30) and ready.get(timeout=30)
        start.set()
        receipts = [results.get(timeout=40), results.get(timeout=40)]
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
        assert all("error" not in result for result in receipts), receipts
        assert sorted(result["replayed"] for result in receipts) == [False, True]
        assert {**receipts[0], "replayed": False} == {**receipts[1], "replayed": False}
        assert len(registrations(store)) == 2
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        ready.close(); results.close()
