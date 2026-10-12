"""UI-SYNC-HTTP: authorized current reads, scope binding and no legacy fallback."""
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_pair, text_update, dump, _grant, revoke,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


def test_ui_synced_http_current_read_and_workspace_binding(environment, monkeypatch):
    store, ws = environment
    child = setup_pair(store, ws)
    text_update(store, ws, "page-e1", "synced-original", "current HTTP original")
    client, actor = client_for(monkeypatch, store)
    url = f"/api/notion/workspaces/{ws}/pages/{child}/synced-blocks/synced-instance"
    before = dump(store)
    response = client.get(url)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    assert response.json["block"]["content"] == "current HTTP original"
    assert response.json["origin"] == {"document_id": "page-e1", "block_id": "synced-original"}
    assert response.json["instance"] == {"document_id": child, "block_id": "synced-instance"}
    assert client.get(f"/api/notion/workspaces/{ws}/pages/{child}/collaboration").json["capabilities"]["collaborative_synced_read"] is True
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("wrong workspace must be rejected before content replay")
        patch.setattr(synced.search_projection, "materialize", forbidden)
        denied = client.get(url.replace(ws, "wrong-workspace"))
        assert denied.status_code == 404 and "no-store" in denied.headers["Cache-Control"]
        actor["id"] = "inactive-user"
        assert client.get(url).status_code == 404
    assert dump(store) == before


def test_ui_synced_http_requires_both_current_grants(environment, monkeypatch):
    store, ws = environment
    child = setup_pair(store, ws)
    client, actor = client_for(monkeypatch, store)
    url = f"/api/notion/workspaces/{ws}/pages/{child}/synced-blocks/synced-instance"
    actor["id"] = "outsider"
    # Existing direct view grant on the original does not grant the instance.
    assert client.get(url).status_code == 404
    _grant(store.iam, child, "outsider", "synced-http-view-instance")
    assert client.get(url).status_code == 200
    revoke(store, ws)
    before = dump(store)
    denied = client.get(url)
    assert denied.status_code == 404 and "no-store" in denied.headers["Cache-Control"]
    assert "current" not in denied.json and "block" not in denied.json
    assert dump(store) == before


@pytest.mark.parametrize("error,status,code", [
    (synced.SyncedSourceChanged, 409, "synced_source_changed"),
    (synced.SyncedShapeUnavailable, 422, "synced_shape_unavailable"),
])
def test_ui_synced_http_failure_contract(monkeypatch, error, status, code):
    client, _ = client_for(monkeypatch, object())
    monkeypatch.setattr(routes, "_editing_authority", lambda store: store)
    def fail(*args, **kwargs):
        raise error("private source details")
    monkeypatch.setattr(routes, "read_synced_block", fail)
    response = client.get("/api/notion/workspaces/ws/pages/page/synced-blocks/block")
    assert response.status_code == status and response.json["code"] == code
    assert "private source details" not in response.get_data(as_text=True)
    assert "no-store" in response.headers["Cache-Control"]


def test_ui_synced_http_ordinary_runtime_does_not_fall_back(monkeypatch):
    client, _ = client_for(monkeypatch, object(), collaborative=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary runtime cannot enter collaborative synced storage")
    monkeypatch.setattr(routes, "read_synced_block", forbidden)
    response = client.get("/api/notion/workspaces/ws/pages/page/synced-blocks/block")
    assert response.status_code == 404 and "no-store" in response.headers["Cache-Control"]
