"""UI-PAGE-COPY-HTTP: staged canonical transport; no operating capability activation."""
from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import page_copy, page_copy_source, page_copy_remap
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, request, dump, connection,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive import change
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update


def client_for(store, monkeypatch, enabled=True):
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    original = store.capabilities
    # Only this disposable fixture opts in. Runtime remains false, even at 2416.
    if enabled:
        monkeypatch.setattr(store, "capabilities", lambda: {**original(), "collaborative_page_copy": True})
    monkeypatch.setattr(routes.notion_workspace_pages, "duplicate_workspace_page",
        lambda *a, **k: pytest.fail("collaborative copy must never use the legacy store"))
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-copy-http", NOTION_CANONICAL_ORIGIN="https://notes.example.test")
    routes.register_notion_workspace_routes(app)
    return app.test_client(), actor


def test_ui_copy_http_intent_is_discoverable_without_browser_storage(environment, monkeypatch):
    store, ws = environment
    client, actor = client_for(store, monkeypatch)
    base = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    listing = f"/api/notion/workspaces/{ws}/page-copy-intents"
    body = {**request(store), "operation_id": "copy-http-intent"}
    assert client.get(listing).json == {"items": [], "next_cursor": None}
    assert client.post(base + "-intent", json={**body, "local_origin": "untrusted"}).status_code == 400
    started = client.post(base + "-intent", json=body)
    assert started.status_code == 201 and started.json["state"] == "pending"
    assert client.post(base + "-intent", json=body).json["replayed"] is True
    pending = client.get(listing)
    assert pending.status_code == 200 and len(pending.json["items"]) == 1
    assert pending.json["items"][0]["request"] == body
    assert pending.json["items"][0]["source_page_id"] == "page-e1"
    assert client.post(base + "-intent", json={**body,
        "operation_id": "another-pending-copy"}).status_code == 409
    assert client.post(base, json=body).status_code == 201
    assert client.get(listing).json["items"] == []
    for response in (started, pending):
        assert response.headers["Cache-Control"].startswith("no-store")
    actor["id"] = "content-editor"
    assert client.get(listing).status_code == 404


def test_ui_copy_http_current_body_exact_replay_and_result_after_archive(environment, monkeypatch):
    store, ws = environment
    assert store.capabilities()["collaborative_page_copy"] is False
    current_update(store, ws)
    client, actor = client_for(store, monkeypatch)
    url = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    body = {**request(store), "operation_id": "copy-http-roundtrip"}
    before = dump(store)
    unknown = client.post(url + "-result", json=body)
    assert unknown.status_code == 200 and unknown.json == {"state": "unknown"}
    assert dump(store) == before
    unregistered = client.post(url, json=body)
    assert unregistered.status_code == 409
    assert dump(store) == before
    assert client.post(url + "-intent", json=body).status_code == 201
    saved = client.post(url, json=body, headers={"Origin": "https://ignored.example.test"})
    assert saved.status_code == 201, saved.json
    copied = saved.json["page"]["id"]
    assert saved.json["page"]["title"] == "최신 협업 복제 제목"
    again = client.post(url, json=body)
    assert again.status_code == 200 and again.json["page"]["id"] == copied
    altered = {**body, "expected_document_sequence": body["expected_document_sequence"] + 1}
    conflict = client.post(url, json=altered)
    assert conflict.status_code == 409 and conflict.json["code"] == "collaboration_operation_id_conflict"
    change(store, ws, operation="copy-http-archive-source")
    result = client.post(url + "-result", json=body)
    assert result.status_code == 200 and result.json["state"] == "committed"
    assert result.json["result"]["page"]["id"] == copied and result.json["current"]["archived"] is False
    change(store, ws, doc=copied, operation="copy-http-archive-result")
    result = client.post(url + "-result", json=body)
    assert result.json["current"]["archived"] is True
    for response in (unknown, saved, again, result):
        assert response.headers["Cache-Control"].startswith("no-store")
    actor["id"] = "content-editor"
    assert client.post(url + "-result", json=body).status_code == 404
    with connection(store) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages WHERE creation_kind='copy'").fetchone()[0] == 1


def test_ui_copy_http_pre_intent_completed_receipt_remains_recoverable(environment, monkeypatch):
    store, ws = environment
    client, _actor = client_for(store, monkeypatch)
    body = {**request(store), "operation_id": "copy-http-pre-intent-receipt"}
    old = page_copy.copy_page(store.iam, "owner", ws, "page-e1",
        local_origin="https://notes.example.test", **body)
    url = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    response = client.post(url, json=body)
    assert response.status_code == 200
    assert response.json["page"]["id"] == old["page"]["id"]
    with connection(store) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages WHERE creation_kind='copy'").fetchone()[0] == 1


def test_ui_copy_http_gate_schema_validation_and_legacy_isolation(environment, monkeypatch):
    store, ws = environment
    client, actor = client_for(store, monkeypatch, enabled=False)
    url = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    before = dump(store)
    for suffix in ("", "-result"):
        response = client.post(url + suffix, json={"base_version": 1, "operation_id": "old-writer"})
        assert response.status_code == 503
    assert dump(store) == before
    client, actor = client_for(store, monkeypatch)
    body = {**request(store), "operation_id": "copy-http-invalid"}
    for invalid in ({**body, "local_origin": "https://attacker.invalid"},
                    {**body, "base_version": 1}, {**body, "expected_document_sequence": True},
                    {k: v for k, v in body.items() if k != "operation_id"}):
        for suffix in ("", "-result"):
            assert client.post(url + suffix, json=invalid).status_code == 400
    actor["id"] = "content-editor"
    assert client.post(url, json=body).status_code == 404
    actor["id"] = "owner"
    assert client.post(url.replace(ws, "wrong-workspace"), json=body).status_code == 404
    assert dump(store) == before


def test_ui_copy_http_preparation_failure_has_no_partial_copy(environment, monkeypatch):
    store, ws = environment
    client, _ = client_for(store, monkeypatch)
    url = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    body = {**request(store), "operation_id": "copy-http-refused"}
    assert client.post(url + "-intent", json=body).status_code == 201
    before = dump(store)
    for failure, status, code in (
        (page_copy_source.CopySourceChanged("private detail"), 409, "page_copy_source_changed"),
        (page_copy_remap.CopyShapeUnavailable("private detail"), 422, "page_copy_shape_unavailable"),
    ):
        def refuse(*a, **kw):
            raise failure
        monkeypatch.setattr(page_copy_source, "prepare_page_copy", refuse)
        response = client.post(url, json=body)
        assert response.status_code == status and response.json["code"] == code
        assert "private detail" not in response.get_data(as_text=True)
        assert dump(store) == before
    listing = client.get(f"/api/notion/workspaces/{ws}/page-copy-intents")
    assert [item["operation_id"] for item in listing.json["items"]] == [body["operation_id"]]
    cancelled = client.post(url + "-cancel", json=body)
    assert cancelled.status_code == 200 and cancelled.json["state"] == "cancelled"
    assert client.get(f"/api/notion/workspaces/{ws}/page-copy-intents").json["items"] == []
    corrected = {**body, "operation_id": "copy-http-refused-corrected"}
    assert client.post(url + "-intent", json=corrected).status_code == 201


def test_ui_copy_http_older_schema_refuses_even_a_mistaken_capability(member_environment, monkeypatch):
    store, ws = member_environment
    client, _ = client_for(store, monkeypatch)
    before = dump(store)
    url = f"/api/notion/workspaces/{ws}/pages/page-e1/duplicate"
    body = {**request(store), "operation_id": "copy-http-old-schema"}
    for suffix in ("", "-result"):
        assert client.post(url + suffix, json=body).status_code == 503
    assert dump(store) == before


def test_ui_copy_http_ordinary_mode_keeps_its_original_contract(monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "_navigation_ui_enabled", lambda: True)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: False)
    monkeypatch.setattr(routes, "_store", lambda: "legacy-fixture-store")
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    def legacy(*args, **kwargs):
        calls.append((args, kwargs))
        return {"replayed": False, "page": {"id": "ordinary-copy"}}
    monkeypatch.setattr(routes.notion_workspace_pages, "duplicate_workspace_page", legacy)
    app = Flask(__name__); app.config.update(TESTING=True, SECRET_KEY="synthetic-legacy-copy")
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    url = "/api/notion/workspaces/ordinary/pages/page-one/duplicate"
    response = client.post(url, json={"base_version": 1, "operation_id": "ordinary-copy"})
    assert response.status_code == 201 and response.json["page"]["id"] == "ordinary-copy"
    assert calls == [(("legacy-fixture-store", "owner", "ordinary", "page-one"), {"base_version": 1, "operation_id": "ordinary-copy"})]
    assert client.post(url + "-result", json={}).status_code == 404
