"""UI-LINKED-HTTP: current instance/source access, scoped reads and errors."""
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import linked_databases as linked
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_pair, dump, _grant, revoke,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


def test_ui_linked_http_current_source_instance_scope_and_revocation(environment, monkeypatch):
    store, ws = environment
    child, _title = setup_pair(store, ws)
    client, actor = client_for(monkeypatch, store)
    url = f"/api/notion/workspaces/{ws}/pages/{child}/linked-databases/linked-block"
    before = dump(store)
    response = client.get(url)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    assert response.json["source"] == {"document_id": "page-e1", "database_id": "database-e1"}
    assert response.json["instance"] == {"document_id": child, "block_id": "linked-block"}
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("wrong workspace must fail before replay")
        patch.setattr(linked.synced_blocks.search_projection, "materialize", forbidden)
        assert client.get(url.replace(ws, "wrong-workspace")).status_code == 404
    assert dump(store) == before
    actor["id"] = "outsider"
    assert client.get(url).status_code == 404
    _grant(store.iam, child, "outsider", "linked-http-instance-grant")
    assert client.get(url).status_code == 200
    revoke(store, ws)
    before = dump(store)
    denied = client.get(url)
    assert denied.status_code == 404 and "no-store" in denied.headers["Cache-Control"]
    assert "database" not in denied.json and "source" not in denied.json
    assert dump(store) == before


@pytest.mark.parametrize("error,status,code", [
    (linked.LinkedDatabaseSourceChanged, 409, "linked_database_source_changed"),
    (linked.LinkedDatabaseShapeUnavailable, 422, "linked_database_shape_unavailable"),
])
@pytest.mark.parametrize("scoped", [False, True])
def test_ui_linked_http_failure_contract(monkeypatch, error, status, code, scoped):
    client, _actor = client_for(monkeypatch, object())
    monkeypatch.setattr(routes, "_database_authority", lambda store: store)
    monkeypatch.setattr(routes, "_database_sharing_installed", lambda authority: scoped)
    def fail(*args, **kwargs):
        raise error("private source details")
    monkeypatch.setattr(routes, "read_linked_database_scoped" if scoped else "read_linked_database", fail)
    response = client.get("/api/notion/workspaces/ws/pages/page/linked-databases/block")
    assert response.status_code == status and response.json["code"] == code
    assert "private source details" not in response.get_data(as_text=True)
    assert "no-store" in response.headers["Cache-Control"]


def test_ui_linked_http_ordinary_runtime_has_no_collaborative_fallback(monkeypatch):
    client, _actor = client_for(monkeypatch, object(), collaborative=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary runtime cannot enter canonical source storage")
    monkeypatch.setattr(routes, "read_linked_database", forbidden)
    response = client.get("/api/notion/workspaces/ws/pages/page/linked-databases/block")
    assert response.status_code == 404 and "no-store" in response.headers["Cache-Control"]
    assert client.post("/api/notion/workspaces/ws/pages/page/linked-databases", json={}).status_code == 404
