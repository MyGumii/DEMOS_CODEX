from __future__ import annotations

import io
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import notion_store as notion
from code_assist_v1 import routes_notion
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_workspace_store import WorkspaceStore, catalog_path_for_user_data_root


@pytest.fixture
def locked_page_client(monkeypatch, isolated_knowledge_layout):
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(isolated_knowledge_layout.user_data_dir))
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: "unlock-owner")
    app = Flask(f"notion-temp-lock-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="isolated-temporary-unlock-test")
    routes_notion.register_notion_routes(app)
    client = app.test_client()
    created = client.post("/api/notion/pages", json={"title": "Locked fixture"})
    assert created.status_code == 200
    page_id = created.get_json()["page"]["id"]
    locked = client.patch(f"/api/notion/pages/{page_id}", json={"settings": {"locked": True}})
    assert locked.status_code == 200
    return client, page_id


def _save(client, page_id, title, *, token=None):
    current = client.get(f"/api/notion/pages/{page_id}").get_json()
    headers = {"X-Notion-Temporary-Unlock": token} if token else {}
    return client.post(
        f"/api/notion/pages/{page_id}/save",
        json={
            "base_revision": current["page"]["revision"],
            "operation_id": str(uuid4()),
            "page": {"title": title},
            "blocks": current["blocks"],
        },
        headers=headers,
    )


@pytest.mark.regression
def test_temporary_unlock_allows_only_the_granted_locked_page(locked_page_client):
    client, page_id = locked_page_client
    grant = client.post(f"/api/notion/pages/{page_id}/temporary-unlock")
    assert grant.status_code == 200
    token = grant.get_json()["unlock_token"]

    assert _save(client, page_id, "Denied without grant").status_code == 403
    saved = _save(client, page_id, "Edited while globally locked", token=token)
    assert saved.status_code == 200
    assert saved.get_json()["page"]["settings"]["locked"] is True
    assert _save(client, page_id, "Denied after request cleanup").status_code == 403

    current = client.get(f"/api/notion/pages/{page_id}").get_json()
    assert current["page"]["title"] == "Edited while globally locked"
    assert "_np2b_lock_nonce" not in current["page"]["settings"]

    other = client.post("/api/notion/pages", json={"title": "Other fixture"})
    assert other.status_code == 200
    other_id = other.get_json()["page"]["id"]
    assert client.patch(f"/api/notion/pages/{other_id}", json={"settings": {"locked": True}}).status_code == 200
    assert _save(client, other_id, "Cannot cross pages", token=token).status_code == 403


@pytest.mark.regression
def test_global_relock_revokes_old_temporary_unlock(locked_page_client):
    client, page_id = locked_page_client
    token = client.post(f"/api/notion/pages/{page_id}/temporary-unlock").get_json()["unlock_token"]
    assert client.patch(f"/api/notion/pages/{page_id}", json={"settings": {"locked": False}}).status_code == 200
    assert client.patch(f"/api/notion/pages/{page_id}", json={"settings": {"locked": True}}).status_code == 200

    assert _save(client, page_id, "Old grant must fail", token=token).status_code == 403
    fresh_token = client.post(f"/api/notion/pages/{page_id}/temporary-unlock").get_json()["unlock_token"]
    assert fresh_token != token
    assert _save(client, page_id, "Fresh grant works", token=fresh_token).status_code == 200


@pytest.mark.regression
def test_temporary_unlock_rejects_invalid_and_other_user_tokens(locked_page_client, monkeypatch):
    client, page_id = locked_page_client
    token = client.post(f"/api/notion/pages/{page_id}/temporary-unlock").get_json()["unlock_token"]
    assert _save(client, page_id, "Invalid token", token="invalid-token").status_code == 403
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: "someone-else")
    response = client.post(
        f"/api/notion/pages/{page_id}/save",
        json={"base_revision": 1, "operation_id": str(uuid4()), "page": {"title": "wrong actor"}, "blocks": []},
        headers={"X-Notion-Temporary-Unlock": token},
    )
    assert response.status_code == 403


@pytest.mark.regression
def test_temporary_unlock_covers_child_creation_and_file_upload(locked_page_client):
    client, page_id = locked_page_client
    token = client.post(f"/api/notion/pages/{page_id}/temporary-unlock").get_json()["unlock_token"]
    child_request = {"operation_id": str(uuid4()), "parent_id": page_id,
                     "title": "Temporary unlock child", "blocks": [{"type": "paragraph", "content": "", "props": {}}]}
    assert client.post("/api/notion/pages", json=child_request).status_code == 403
    created = client.post("/api/notion/pages", json=child_request,
                          headers={"X-Notion-Temporary-Unlock": token})
    assert created.status_code == 200
    assert created.get_json()["page"]["parent_id"] == page_id

    def upload(headers=None):
        return client.post(
            f"/api/notion/pages/{page_id}/files",
            data={"file": (io.BytesIO(b"isolated fixture"), "fixture.txt")},
            headers=headers or {},
        )

    assert upload().status_code == 403
    uploaded = upload({"X-Notion-Temporary-Unlock": token})
    assert uploaded.status_code == 200
    assert uploaded.get_json()["file"]["original_name"] == "fixture.txt"


@pytest.mark.regression
def test_temporary_unlock_covers_personal_page_lifecycle(locked_page_client):
    client, page_id = locked_page_client
    token = client.post(f"/api/notion/pages/{page_id}/temporary-unlock").get_json()["unlock_token"]
    headers = {"X-Notion-Temporary-Unlock": token}
    destination = client.post("/api/notion/pages", json={"title": "Move destination"}).get_json()["page"]["id"]

    move_path = f"/api/notion/pages/{page_id}/move"
    assert client.post(move_path, json={"parent_id": destination}).status_code == 403
    moved = client.post(move_path, json={"parent_id": destination}, headers=headers)
    assert moved.status_code == 200
    assert moved.get_json()["page"]["parent_id"] == destination

    duplicate_path = f"/api/notion/pages/{page_id}/duplicate"
    assert client.post(duplicate_path, json={}).status_code == 403
    duplicate = client.post(duplicate_path, json={}, headers=headers)
    assert duplicate.status_code == 200
    copied_id = duplicate.get_json()["page"]["id"]
    assert duplicate.get_json()["page"]["settings"]["locked"] is True
    assert _save(client, copied_id, "Source grant cannot edit copy", token=token).status_code == 403

    assert client.delete(f"/api/notion/pages/{page_id}").status_code == 403
    archived = client.delete(f"/api/notion/pages/{page_id}", headers=headers)
    assert archived.status_code == 200
    assert archived.get_json()["archived"] is True


@pytest.mark.regression
def test_temporary_unlock_covers_bound_workspace_lifecycle(
    monkeypatch, isolated_knowledge_layout,
):
    actor = "unlock-owner"
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(isolated_knowledge_layout.user_data_dir))
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: actor)
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: actor)
    source = notion.create_page(actor, title="Bound locked fixture")["page"]["id"]
    notion.update_page(actor, source, {"settings": {"locked": True}})
    store = WorkspaceStore(catalog_path_for_user_data_root(isolated_knowledge_layout.user_data_dir))
    workspace = store.create_workspace(actor, name="Synthetic workspace", operation_id=str(uuid4()))["workspace"]
    workspace_id = workspace["id"]
    proof = notion.capture_legacy_page_source_proof(actor, actor, source)
    bound = store.bind_page_source(
        actor, workspace_id, proof,
        source_revalidator=notion.revalidate_legacy_page_source_proof,
        base_version=workspace["version"], operation_id=str(uuid4()),
    )
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: store)
    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_ENABLE_ENV, "1")
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_ENABLE_ENV, "1")
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_SUBJECTS_ENV, actor)
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_WORKSPACES_ENV, workspace_id)

    app = Flask(f"notion-bound-unlock-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="isolated-bound-unlock-test")
    routes_notion.register_notion_routes(app)
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    token = client.post(f"/api/notion/pages/{source}/temporary-unlock").get_json()["unlock_token"]
    headers = {"X-Notion-Temporary-Unlock": token}
    duplicate_path = f"/api/notion/workspaces/{workspace_id}/pages/{source}/duplicate"
    duplicate_body = {"base_version": bound["workspace"]["version"], "operation_id": str(uuid4())}
    assert client.post(duplicate_path, json=duplicate_body).status_code == 403
    duplicate = client.post(duplicate_path, json=duplicate_body, headers=headers)
    assert duplicate.status_code == 201
    assert duplicate.get_json()["page"]["settings"]["locked"] is True

    create_path = f"/api/notion/workspaces/{workspace_id}/pages"
    create_body = {"base_version": duplicate.get_json()["workspace"]["version"],
                   "operation_id": str(uuid4()), "parent_id": source, "title": "Unlocked child"}
    assert client.post(create_path, json=create_body).status_code == 403
    child = client.post(create_path, json=create_body, headers=headers)
    assert child.status_code == 201
    assert child.get_json()["page"]["parent_id"] == source

    trash_path = f"/api/notion/workspaces/{workspace_id}/pages/{source}/trash"
    trash_body = {"base_version": child.get_json()["workspace"]["version"], "operation_id": str(uuid4())}
    assert client.post(trash_path, json=trash_body).status_code == 403
    archived = client.post(trash_path, json=trash_body, headers=headers)
    assert archived.status_code == 200
    assert archived.get_json()["archived_count"] == 2
