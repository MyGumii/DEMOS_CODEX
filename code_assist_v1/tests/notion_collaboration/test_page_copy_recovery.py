"""UI-PAGE-COPY-RECOVERY: durable terminal decisions and authorized discovery."""
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import page_copy as copying
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    connection, request, dump, create, original_rows,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_http import client_for
from code_assist_v1.tests.notion_collaboration.test_page_archive import change


def arguments(store, operation="recovery-operation"):
    return {"operation_id": operation, **request(store)}


def cancel(store, ws, body):
    return copying.cancel_page_copy(store.iam, "owner", ws, "page-e1", **body)


def copies(store):
    with connection(store) as con:
        return con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages WHERE creation_kind='copy'").fetchone()[0]


def test_ui_copy_recovery_intent_survives_lost_browser_record_until_terminal_result(environment):
    store, ws = environment
    body = arguments(store, "durable-intent")
    with pytest.raises(InjectedFailure):
        copying.register_page_copy_intent(
            store.iam, "owner", ws, "page-e1", fault="after_event", **body)
    assert copying.list_page_copy_intents(store.iam, "owner", ws)["items"] == []
    started = copying.register_page_copy_intent(
        store.iam, "owner", ws, "page-e1", **body)
    assert started["state"] == "pending" and started["replayed"] is False
    assert copying.register_page_copy_intent(
        store.iam, "owner", ws, "page-e1", **body)["replayed"] is True
    pending = copying.list_page_copy_intents(store.iam, "owner", ws)
    assert pending["next_cursor"] is None
    assert pending["items"] == [{
        "state": "pending", "source_page_id": "page-e1",
        "operation_id": body["operation_id"], "request": body,
        "created_at": pending["items"][0]["created_at"],
    }]
    assert copying.read_page_copy_result(
        store.iam, "owner", ws, "page-e1", **body)["state"] == "unknown"
    with pytest.raises(OperationConflict):
        copying.register_page_copy_intent(
            store.iam, "owner", ws, "page-e1",
            **{**body, "expected_document_sequence": body["expected_document_sequence"] + 1})
    with pytest.raises(OperationConflict):
        copying.read_page_copy_result(
            store.iam, "owner", ws, "page-e1",
            **{**body, "expected_document_sequence": body["expected_document_sequence"] + 1})
    with pytest.raises(OperationConflict):
        copying.register_page_copy_intent(
            store.iam, "owner", ws, "page-e1", **arguments(store, "second-intent"))
    with pytest.raises(NotFoundOrDenied):
        copying.list_page_copy_intents(store.iam, "content-editor", ws)
    copied = copying.copy_page(store.iam, "owner", ws, "page-e1", **body)
    assert copied["page"]["id"]
    assert copying.list_page_copy_intents(store.iam, "owner", ws)["items"] == []
    assert copies(store) == 1


def test_ui_copy_recovery_cancel_is_atomic_exact_and_does_not_edit_documents(environment, monkeypatch):
    store, ws = environment
    body = arguments(store)
    before = original_rows(store)
    result = cancel(store, ws, body)
    assert result["state"] == "cancelled" and result["result"]["copy_request"] == body
    after = dump(store)
    after_rows = original_rows(store)
    changed = {table for table in before if before[table] != after_rows[table]}
    assert changed == {"workspace_collaboration_events", "workspace_collaboration_audit_events",
                       "workspace_collaboration_outbox", "workspace_collaboration_operations"}
    assert cancel(store, ws, body)["result"]["replayed"] is True
    assert dump(store) == after
    with pytest.raises(OperationConflict):
        cancel(store, ws, {**body, "expected_document_sequence": body["expected_document_sequence"] + 1})
    monkeypatch.setattr(copying.page_copy_source, "prepare_page_copy", lambda *a, **k: pytest.fail("cancelled copy cannot prepare again"))
    with pytest.raises(copying.CopyCancelled):
        copying.copy_page(store.iam, "owner", ws, "page-e1", **body)
    recovered = copying.read_page_copy_result(store.iam, "owner", ws, "page-e1", **body)
    assert recovered["state"] == "cancelled" and dump(store) == after and copies(store) == 0


def test_ui_copy_recovery_completed_copy_wins_and_discovery_survives_source_archive(environment):
    store, ws = environment
    body = arguments(store)
    result = copying.copy_page(store.iam, "owner", ws, "page-e1", **body)
    before = dump(store)
    observed = cancel(store, ws, body)
    assert observed["state"] == "committed" and observed["result"]["page"]["id"] == result["page"]["id"]
    assert dump(store) == before and copies(store) == 1
    change(store, ws, operation="recovery-source-archive")
    change(store, ws, doc=result["page"]["id"], operation="recovery-copy-archive")
    before = dump(store)
    listing = copying.list_page_copy_results(store.iam, "owner", ws)
    assert listing["next_cursor"] is None and len(listing["items"]) == 1
    assert listing["items"][0]["request"] == body and listing["items"][0]["current"]["archived"] is True
    assert dump(store) == before
    with pytest.raises(NotFoundOrDenied):
        copying.list_page_copy_results(store.iam, "content-editor", ws)


def test_ui_copy_recovery_pagination_current_destination_ownership_and_legacy_records(environment, monkeypatch):
    store, ws = environment
    for i in range(4):
        cancel(store, ws, arguments(store, f"cancel-{i}"))
    child = create(store, ws, operation="recovery-source-child")["page"]["id"]
    result = copying.copy_page(store.iam, "owner", ws, "page-e1", **arguments(store, "owner-copy"))
    copied_child = next(item for item in result["page_ids"] if item != result["page"]["id"])
    with connection(store) as con:
        con.execute("UPDATE workspace_collaboration_documents SET legacy_owner_subject_id='content-editor' WHERE id=?", (copied_child,))
    before = dump(store)
    cursor = ""; seen = []
    for _ in range(4):
        listing = copying.list_page_copy_results(store.iam, "owner", ws, limit=2, cursor=cursor)
        seen.extend(item["operation_id"] for item in listing["items"])
        cursor = listing["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == [f"cancel-{i}" for i in range(4)] and cursor is None
    assert dump(store) == before
    for kwargs in ({"limit": True}, {"limit": 0}, {"limit": 101}, {"cursor": "not a cursor"}):
        with pytest.raises(ValueError):
            copying.list_page_copy_results(store.iam, "owner", ws, **kwargs)
    # Emulate a pre-recovery receipt without changing an immutable stored row.
    monkeypatch.setattr(copying, "_copy_request", lambda *_args: None)
    old = copying.copy_page(store.iam, "owner", ws, child, **arguments(store, "old-shape") | request(store, child))
    listing = copying.list_page_copy_results(store.iam, "owner", ws)
    old_item = next(item for item in listing["items"] if item["operation_id"] == "old-shape")
    assert old_item["request"] is None and old_item["result"]["page"]["id"] == old["page"]["id"]


def test_ui_copy_recovery_cancel_faults_rollback(environment):
    store, ws = environment
    body = arguments(store)
    before = dump(store)
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox", "after_operation"):
        with pytest.raises(InjectedFailure):
            copying.cancel_page_copy(store.iam, "owner", ws, "page-e1", fault=fault, **body)
        assert dump(store) == before
    with pytest.raises(NotFoundOrDenied):
        copying.cancel_page_copy(store.iam, "content-editor", ws, "page-e1", **body)


def test_ui_copy_recovery_old_schema_denies(member_environment):
    old, old_ws = member_environment
    with pytest.raises(AuthorityUnavailable):
        copying.cancel_page_copy(old.iam, "owner", old_ws, "page-e1", **arguments(old))
    with pytest.raises(AuthorityUnavailable):
        copying.list_page_copy_results(old.iam, "owner", old_ws)


def _recovery_worker(kind, order, catalog, root, ws, body, prepared, finished, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    try:
        if kind == "copy":
            capacity = copying._check_capacity
            def pause(*args):
                capacity(*args)
                prepared.set()
                if order == "cancel-first" and not finished.wait(30):
                    raise RuntimeError("cancellation timeout")
            copying._check_capacity = pause
            value = copying.copy_page(store.iam, "owner", ws, "page-e1", **body)
            finished.set()
            results.put((kind, "committed", value["page"]["id"]))
        else:
            assert prepared.wait(30)
            if order == "copy-first":
                assert finished.wait(30)
            value = cancel(store, ws, body)
            finished.set()
            results.put((kind, value["state"], value["result"].get("page", {}).get("id")))
    except copying.CopyCancelled:
        results.put((kind, "cancelled", None))
    except Exception as exc:
        results.put((kind, "error", repr(exc)))


@pytest.mark.parametrize("order", ["cancel-first", "copy-first"])
def test_ui_copy_recovery_two_process_terminal_order_has_one_outcome(environment, order):
    store, ws = environment
    body = arguments(store, "two-process-terminal")
    ctx = multiprocessing.get_context("spawn")
    prepared, finished, results = ctx.Event(), ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_recovery_worker, args=(kind, order, store.db_path, store.user_data_root, ws, body, prepared, finished, results))
               for kind in ("copy", "cancel")]
    try:
        for worker in workers:
            worker.start()
        outcomes = [results.get(timeout=45), results.get(timeout=45)]
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
        expected = "cancelled" if order == "cancel-first" else "committed"
        assert {outcome[1] for outcome in outcomes} == {expected}, outcomes
        assert copies(store) == (0 if order == "cancel-first" else 1)
        with connection(store) as con:
            for table in ("operations", "events", "audit_events", "outbox"):
                assert con.execute(f"SELECT COUNT(*) FROM workspace_collaboration_{table} WHERE operation_id=?", (body["operation_id"],)).fetchone()[0] == 1
    finally:
        prepared.set(); finished.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        results.close()


def test_ui_copy_recovery_http_cancel_discovery_gates_validation_and_no_store(environment, monkeypatch):
    store, ws = environment
    client, actor = client_for(store, monkeypatch, enabled=False)
    base = f"/api/notion/workspaces/{ws}"
    cancel_url = base + "/pages/page-e1/duplicate-cancel"
    listing_url = base + "/page-copy-results"
    body = arguments(store)
    before = dump(store)
    assert client.post(cancel_url, json=body).status_code == 503
    assert client.get(listing_url).status_code == 503
    assert dump(store) == before
    client, actor = client_for(store, monkeypatch)
    response = client.post(cancel_url, json=body)
    assert response.status_code == 200 and response.json["state"] == "cancelled"
    assert response.headers["Cache-Control"].startswith("no-store")
    before = dump(store)
    response = client.get(listing_url)
    assert response.status_code == 200 and response.json["items"][0]["request"] == body
    assert response.headers["Cache-Control"].startswith("no-store") and dump(store) == before
    cancelled = client.post(cancel_url.removesuffix("-cancel"), json=body)
    assert cancelled.status_code == 409 and cancelled.json["code"] == "page_copy_cancelled"
    for query in ("?limit=0", "?limit=101", "?limit=x", "?cursor=bad", "?extra=1", "?limit=1&limit=2"):
        assert client.get(listing_url + query).status_code == 400
    assert client.post(cancel_url, json={**body, "base_version": 1}).status_code == 400
    actor["id"] = "content-editor"
    assert client.get(listing_url).status_code == 404
    assert client.post(cancel_url, json=body).status_code == 404
    assert dump(store) == before
