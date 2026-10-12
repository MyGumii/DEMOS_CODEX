"""UI-PAGE-ARCHIVE HTTP: canonical-only routes and legacy compatibility."""
from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.tests.notion_collaboration.test_page_archive import (
    environment, image_environment, guest_environment, create, request, dump, database,
)


def client_for(monkeypatch, store, *, collaborative=True):
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_navigation_ui_enabled", lambda: True)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: collaborative)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    monkeypatch.setattr(routes, "_active_human_users", lambda: [{"id": actor["id"]}])
    monkeypatch.setattr(routes.iam_runtime, "ensure_personal_workspace", lambda current, subject, **kwargs: current.list_workspaces(subject))
    app = Flask(__name__); app.config.update(TESTING=True)
    routes.register_notion_workspace_routes(app)
    return app.test_client(), actor


def forbidden_legacy(*args, **kwargs):
    raise AssertionError("collaborative lifecycle must not use the legacy page store")


def test_ui_page_archive_http_canonical_roundtrip_and_roles(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "page-child")
    client, actor = client_for(monkeypatch, store)
    for name in ("archive_workspace_page", "restore_workspace_page", "list_workspace_trash"):
        monkeypatch.setattr(routes.notion_workspace_pages, name, forbidden_legacy)
    base = f"/api/notion/workspaces/{ws}"
    page = client.get(base + "/pages/page-e1/collaboration").json
    body = {key: value for key, value in request(store, ws).items() if key != "archived"}
    actor["id"] = "outsider"
    assert client.post(base + "/pages/page-e1/trash", json=body).status_code == 404
    assert client.get(base + "/trash").status_code == 404
    actor["id"] = "owner"
    assert client.post(base + "/pages/page-e1/trash", json={**body, "base_version": 1}).status_code == 400
    archived = client.post(base + "/pages/page-e1/trash", json=body)
    assert archived.status_code == 200
    assert archived.json["archived_count"] == 2
    assert archived.json["workspace"]["version"] == store.get_workspace("owner", ws)["version"]
    assert client.post(base + "/pages/page-e1/trash", json=body).json == archived.json
    assert client.get(base + "/pages/page-e1/collaboration").status_code == 404
    assert client.get(base + "/pages/page-child/collaboration").status_code == 404
    trash = client.get(base + "/trash")
    assert trash.status_code == 200 and "no-store" in trash.headers["Cache-Control"]
    target = next(item for item in trash.json["items"] if item["id"] == "page-e1")
    restore = {"operation_id": "http-restore-page", "expected_document_sequence": target["document_sequence"],
               "expected_document_epoch": target["document_epoch"], "expected_workspace_epoch": target["workspace_epoch"]}
    restored = client.post(base + "/trash/page-e1/restore", json=restore)
    assert restored.status_code == 200 and restored.json["restored_count"] == 2
    assert client.post(base + "/trash/page-e1/restore", json=restore).json == restored.json
    assert client.get(base + "/pages/page-e1/collaboration").json["blocks"] == page["blocks"]
    assert client.get(base + "/trash").json["items"] == []
    assert client.get("/api/notion/workspaces").json["capabilities"]["collaborative_page_archive"] is True


def test_ui_page_archive_http_old_schema_never_falls_back(guest_environment, monkeypatch):
    store, ws = guest_environment
    client, _ = client_for(monkeypatch, store)
    for name in ("archive_workspace_page", "restore_workspace_page", "list_workspace_trash"):
        monkeypatch.setattr(routes.notion_workspace_pages, name, forbidden_legacy)
    base = f"/api/notion/workspaces/{ws}"
    page = store.iam.read_document("owner", "page-e1")
    req = {"operation_id": "old-schema-archive", "expected_document_sequence": page["document_sequence"],
           "expected_document_epoch": page["document_epoch"], "expected_workspace_epoch": page["workspace_epoch"]}
    assert client.post(base + "/pages/page-e1/trash", json=req).status_code == 503
    assert client.get(base + "/trash").status_code == 503
    assert client.post(base + "/pages/page-e1/lifecycle-result", json={**req, "archived": True}).status_code == 503
    assert client.get("/api/notion/workspaces").json["capabilities"]["collaborative_page_archive"] is False
    assert store.iam.read_document("owner", "page-e1")["blocks"] == page["blocks"]


def test_ui_page_archive_http_ordinary_contract_is_preserved(monkeypatch):
    client, _ = client_for(monkeypatch, object(), collaborative=False)
    calls = []
    def action(*args, **kwargs):
        calls.append((args[1:], kwargs))
        return {"page_id": "p", "replayed": False}
    monkeypatch.setattr(routes.notion_workspace_pages, "archive_workspace_page", action)
    monkeypatch.setattr(routes.notion_workspace_pages, "restore_workspace_page", action)
    monkeypatch.setattr(routes.notion_workspace_pages, "list_workspace_trash", lambda *args, **kwargs: {"items": []})
    base = "/api/notion/workspaces/ws-ordinary"
    req = {"operation_id": "ordinary-lifecycle", "base_version": 3}
    assert client.post(base + "/pages/p/trash", json=req).status_code == 200
    assert client.post(base + "/trash/p/restore", json=req).status_code == 200
    assert client.get(base + "/trash").json == {"items": []}
    assert client.post(base + "/pages/p/lifecycle-result", json={}).status_code == 404
    assert len(calls) == 2 and all(args == ("owner", "ws-ordinary", "p") and kwargs == req for args, kwargs in calls)


def test_ui_page_archive_receipt_read_after_access_fence_is_read_only(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "receipt-child")
    client, actor = client_for(monkeypatch, store)
    base = f"/api/notion/workspaces/{ws}"
    body = request(store, ws)
    endpoint = base + "/pages/page-e1/lifecycle-result"
    before = dump(store)
    assert client.post(endpoint, json=body).json == {"state": "unknown"}
    assert dump(store) == before, "unknown lookup cannot create receipts, events, or archives"
    mutation = {key: value for key, value in body.items() if key != "archived"}
    committed = client.post(base + "/pages/page-e1/trash", json=mutation).json
    committed.pop("status")
    assert client.get(base + "/pages/page-e1/collaboration").status_code == 404
    before = dump(store)
    response = client.post(endpoint, json=body)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    assert response.json["state"] == "committed" and response.json["result"] == committed
    assert response.json["current"]["archived"] is True
    assert client.post(endpoint, json={**body, "expected_document_sequence": 999}).status_code == 409
    assert client.post(endpoint, json={**body, "archived": "true"}).status_code == 400
    actor["id"] = "outsider"
    assert client.post(endpoint, json=body).status_code == 404
    assert client.post(endpoint, json={**body, "operation_id": "unknown-operation"}).status_code == 404
    actor["id"] = "owner"
    assert dump(store) == before
    restore = {key: value for key, value in request(store, ws, archived=False, operation="receipt-restore").items() if key != "archived"}
    assert client.post(base + "/trash/page-e1/restore", json=restore).status_code == 200
    before = dump(store)
    lookup = client.post(endpoint, json=body).json
    assert lookup["state"] == "committed" and lookup["result"] == committed
    assert lookup["current"]["archived"] is False
    assert dump(store) == before, "reading an old archive receipt after restore cannot archive again"
    # Recheck ownership of all affected documents, not just the selected root.
    with database(store.db_path) as con:
        con.execute("UPDATE workspace_collaboration_documents SET legacy_owner_subject_id='outsider' WHERE id='receipt-child'")
    before = dump(store)
    assert client.post(endpoint, json=body).status_code == 404
    assert dump(store) == before
