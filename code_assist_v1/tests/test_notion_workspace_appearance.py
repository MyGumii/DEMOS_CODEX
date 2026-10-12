from __future__ import annotations

import sqlite3

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_store as nws, routes_notion_workspace as routes
from code_assist_v1.notion_workspace_appearance import CONTRACT, update_workspace_icon


@pytest.fixture()
def workspace(tmp_path):
    store = nws.WorkspaceStore(nws.catalog_path_for_user_data_root(tmp_path / "synthetic"))
    created = store.create_workspace("owner", name="UI icon fixture", operation_id="create")
    return store, created["workspace"]["id"]


def save(store, workspace_id, icon, operation_id, actor="owner", version=None):
    return update_workspace_icon(store, actor, workspace_id, icon=icon, operation_id=operation_id,
                                 base_version=version or store.get_workspace("owner", workspace_id)["version"])


def test_ui_workspace_icon_exact_replay_removal_and_atomic_failure(workspace, monkeypatch):
    store, workspace_id = workspace
    original = store.get_workspace("owner", workspace_id)
    first = save(store, workspace_id, "ni:rocket:purple", "icon-one", version=1)
    assert first["contract"] == CONTRACT
    assert first["workspace"]["security_epoch"] == original["security_epoch"]
    assert first["workspace"]["access"]["role"] == "owner"
    assert store.list_workspaces("owner")[0]["icon"] == "ni:rocket:purple"
    save(store, workspace_id, "", "icon-remove")
    counts = store.audit_counts()
    assert save(store, workspace_id, "ni:rocket:purple", "icon-one", version=1) == first
    assert store.get_workspace("owner", workspace_id)["icon"] == "", "old receipt does not reapply its icon"
    assert store.audit_counts() == counts
    with pytest.raises(nws.WorkspaceOperationConflict):
        save(store, workspace_id, "🌱", "icon-one", version=1)
    with pytest.raises(nws.WorkspaceRevisionConflict):
        save(store, workspace_id, "🌱", "stale", version=1)
    before = store.get_workspace("owner", workspace_id)

    def fail_after_icon_write(*args, **kwargs):
        raise RuntimeError("injected before audit/outbox")

    monkeypatch.setattr(store, "_bump_workspace", fail_after_icon_write)
    with pytest.raises(RuntimeError, match="injected"):
        save(store, workspace_id, "🌱", "rolled-back")
    assert store.get_workspace("owner", workspace_id) == before
    assert store.audit_counts() == counts
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_outbox WHERE event_type='workspace.appearance.updated'").fetchone()[0] == 2


def test_ui_workspace_icon_roles_revocation_and_format_validation(workspace):
    store, workspace_id = workspace
    for role, subject in [("member", "member"), ("membership_admin", "admin")]:
        store.set_member("owner", workspace_id, subject, role=role, base_version=store.get_workspace("owner", workspace_id)["version"], operation_id="add-" + subject)
    with pytest.raises(PermissionError):
        save(store, workspace_id, "🌱", "denied", actor="member")
    with pytest.raises(FileNotFoundError):
        save(store, workspace_id, "🌱", "outsider", actor="outsider")
    version = store.get_workspace("owner", workspace_id)["version"]
    save(store, workspace_id, "🌱", "admin-icon", actor="admin", version=version)
    store.revoke_member("owner", workspace_id, "admin", base_version=store.get_workspace("owner", workspace_id)["version"], operation_id="revoke")
    with pytest.raises(FileNotFoundError):
        save(store, workspace_id, "🌱", "admin-icon", actor="admin", version=version)
    for value in [None, 12, "x" * 65, "ni:unknown:purple", "ni:rocket:unknown", "nf:file_other_page"]:
        with pytest.raises(ValueError):
            save(store, workspace_id, value, "invalid")


def test_ui_workspace_icon_http_contract(workspace, monkeypatch):
    store, workspace_id = workspace
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    app = Flask(__name__)
    app.config.update(TESTING=True)
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    assert client.get(f"/api/notion/workspaces/{workspace_id}").json["capabilities"]["workspace_appearance"] is True
    url = f"/api/notion/workspaces/{workspace_id}/appearance"
    data = {"icon": "ni:rocket:purple", "base_version": 1, "operation_id": "http-icon"}
    first = client.patch(url, json=data)
    assert first.status_code == 200
    assert client.patch(url, json=data).json == first.json
    assert first.json["workspace"]["icon"] == data["icon"]
    for body in [{**data, "unknown": True}, {"base_version": 1, "operation_id": "no-icon"}, {**data, "icon": None}]:
        assert client.patch(url, json=body).status_code == 400
    monkeypatch.setattr(routes, "current_user_id", lambda: "outsider")
    assert client.patch(url, json={**data, "operation_id": "outsider"}).status_code == 404
