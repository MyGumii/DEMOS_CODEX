from __future__ import annotations

from flask import Flask
import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_pages
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _fail_scan():
    raise AssertionError("workspace-scoped page reads must never enumerate user directories")


def _store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(tmp_path))


def _workspace(store: WorkspaceStore) -> tuple[str, int]:
    result = store.create_workspace(
        "owner", name="Workspace", operation_id="create-workspace"
    )
    return result["workspace"]["id"], result["workspace"]["version"]


def _enable_navigation_pilot(monkeypatch, workspace_id: str) -> None:
    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_ENABLE_ENV, "1")
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_ENABLE_ENV, "1")
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_SUBJECTS_ENV, "owner")
    monkeypatch.setenv(
        routes_notion_workspace.CORE_UI_PILOT_WORKSPACES_ENV,
        workspace_id,
    )


def _bind(
    store: WorkspaceStore,
    workspace_id: str,
    version: int,
    *,
    page_id: str,
    owner: str = "owner",
    actor: str = "owner",
    teamspace_id: str = "",
    operation_id: str,
) -> dict:
    proof = notion_store.capture_legacy_page_source_proof(actor, owner, page_id)
    return store.bind_page_source(
        actor,
        workspace_id,
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        teamspace_id=teamspace_id,
        base_version=version,
        operation_id=operation_id,
    )


def test_private_bound_pages_are_owner_only_parent_scrubbed_and_scan_free(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    parent = notion_store.create_page("owner", title="Hidden parent")
    child = notion_store.create_page(
        "owner", title="Bound child", parent_id=parent["page"]["id"]
    )

    monkeypatch.setattr(notion_store, "_known_workspace_users", _fail_scan)
    bound = _bind(
        store,
        workspace_id,
        version,
        page_id=child["page"]["id"],
        operation_id="bind-child",
    )
    result = notion_workspace_pages.list_workspace_pages(
        store, "owner", workspace_id
    )

    assert result["degraded"] is False
    assert result["count"] == 1
    assert result["items"][0]["title"] == "Bound child"
    assert result["items"][0]["parent_id"] == ""
    assert result["items"][0]["placement"] == {
        "scope": "private",
        "teamspace_id": "",
        "binding_version": 1,
    }
    assert result["membership_epoch"] == 1
    assert bound["binding"]["source_sha256"].startswith("sha256:")

    assert notion_workspace_pages.list_workspace_pages(
        store, "owner", workspace_id, query="missing"
    )["items"] == []

    version = bound["workspace"]["version"]
    store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="member-one",
    )
    notion_store.share_page(
        "owner", child["page"]["id"], "member_one", role="view"
    )
    assert store.list_page_source_bindings("member_one", workspace_id) == []
    assert notion_workspace_pages.list_workspace_pages(
        store, "member_one", workspace_id
    )["items"] == []


def test_teamspace_binding_requires_catalog_and_legacy_access_and_revokes_closed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    version = store.set_member(
        "owner",
        workspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="member-one",
    )["workspace"]["version"]
    teamspace = store.create_teamspace(
        "owner",
        workspace_id,
        name="Project",
        visibility="private",
        base_version=version,
        operation_id="teamspace-project",
    )
    teamspace_id = teamspace["teamspace"]["id"]
    version = teamspace["workspace"]["version"]
    version = store.set_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "member_one",
        role="member",
        base_version=version,
        operation_id="teamspace-member",
    )["workspace"]["version"]

    page = notion_store.create_page("owner", title="Teamspace page")
    notion_store.share_page(
        "owner", page["page"]["id"], "member_one", role="view"
    )
    monkeypatch.setattr(notion_store, "_known_workspace_users", _fail_scan)
    bound = _bind(
        store,
        workspace_id,
        version,
        page_id=page["page"]["id"],
        teamspace_id=teamspace_id,
        operation_id="bind-teamspace-page",
    )

    visible = notion_workspace_pages.list_workspace_pages(
        store, "member_one", workspace_id
    )
    assert visible["count"] == 1
    assert visible["items"][0]["title"] == "Teamspace page"

    notion_store.revoke_share("owner", page["page"]["id"], "member_one")
    denied = notion_workspace_pages.list_workspace_pages(
        store, "member_one", workspace_id
    )
    assert denied == {
        "workspace_id": workspace_id,
        "membership_epoch": 1,
        "items": [],
        "count": 0,
        "degraded": True,
        "degraded_sources": ["legacy_page_binding"],
    }

    store.remove_teamspace_member(
        "owner",
        workspace_id,
        teamspace_id,
        "member_one",
        base_version=bound["workspace"]["version"],
        operation_id="remove-teamspace-member",
    )
    assert store.list_page_source_bindings("member_one", workspace_id) == []


def test_workspace_pages_route_uses_locator_service_and_strict_query(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    monkeypatch.setattr(notion_store, "_known_workspace_users", _fail_scan)
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    page = notion_store.create_page("owner", title="Scoped route page")
    _bind(
        store,
        workspace_id,
        version,
        page_id=page["page"]["id"],
        operation_id="bind-route-page",
    )

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="workspace-page-route")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()

    response = client.get(f"/api/notion/workspaces/{workspace_id}/pages?q=scoped")
    assert response.status_code == 200
    assert response.get_json()["count"] == 1
    assert response.get_json()["items"][0]["title"] == "Scoped route page"
    assert response.headers["Cache-Control"] == "no-store, max-age=0"

    too_long = client.get(
        f"/api/notion/workspaces/{workspace_id}/pages?q=" + "x" * 201
    )
    assert too_long.status_code == 400
    assert too_long.get_json()["code"] == "invalid_request"


def test_workspace_page_create_is_scoped_idempotent_and_default_off(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="workspace-page-create-route")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    request_body = {
        "base_version": version,
        "operation_id": "ui-tree-create-root",
        "parent_id": "",
        "title": "Synthetic created root",
    }

    disabled = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages", json=request_body
    )
    assert disabled.status_code == 404
    assert len(notion_store.list_pages("owner")) == 0

    _enable_navigation_pilot(monkeypatch, workspace_id)
    created = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages", json=request_body
    )
    assert created.status_code == 201
    first = created.get_json()
    assert first["status"] == "page_created"
    assert first["page"]["title"] == "Synthetic created root"
    assert first["page"]["parent_id"] == ""
    assert first["binding"]["version"] == 1
    assert first["workspace"]["version"] > version

    replay = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages", json=request_body
    )
    assert replay.status_code == 200
    assert replay.get_json()["page"]["id"] == first["page"]["id"]
    assert len(notion_store.list_pages("owner")) == 1
    scoped = client.get(f"/api/notion/workspaces/{workspace_id}/pages")
    assert scoped.status_code == 200
    assert scoped.get_json()["count"] == 1

    unexpected = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages",
        json={
            **request_body,
            "base_version": first["workspace"]["version"],
            "operation_id": "ui-tree-create-unexpected",
            "unexpected": True,
        },
    )
    assert unexpected.status_code == 400
    assert len(notion_store.list_pages("owner")) == 1

    stale = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages",
        json={**request_body, "operation_id": "ui-tree-create-stale"},
    )
    assert stale.status_code == 409
    assert len(notion_store.list_pages("owner")) == 1

    changed = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages",
        json={**request_body, "title": "Conflicting title"},
    )
    assert changed.status_code == 409
    assert len(notion_store.list_pages("owner")) == 1


def test_workspace_page_duplicate_binds_complete_subtree_and_replays(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    store = _store(tmp_path)
    workspace_id, version = _workspace(store)
    root = notion_store.create_page("owner", title="Synthetic Root")
    child = notion_store.create_page(
        "owner", title="Synthetic Child", parent_id=root["page"]["id"]
    )
    root_bound = _bind(
        store,
        workspace_id,
        version,
        page_id=root["page"]["id"],
        operation_id="bind-duplicate-root",
    )
    child_bound = _bind(
        store,
        workspace_id,
        root_bound["workspace"]["version"],
        page_id=child["page"]["id"],
        operation_id="bind-duplicate-child",
    )

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="workspace-page-duplicate-route")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    body = {
        "base_version": child_bound["workspace"]["version"],
        "operation_id": "ui-tree-duplicate-subtree",
    }

    disabled = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{root['page']['id']}/duplicate",
        json=body,
    )
    assert disabled.status_code == 404
    assert len(notion_store.list_pages("owner")) == 2

    _enable_navigation_pilot(monkeypatch, workspace_id)
    created = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{root['page']['id']}/duplicate",
        json=body,
    )
    assert created.status_code == 201
    payload = created.get_json()
    assert payload["status"] == "page_duplicated"
    assert payload["subtree_count"] == 2
    assert payload["page"]["title"] == "Synthetic Root copy"
    duplicate_root_id = payload["page"]["id"]
    assert duplicate_root_id not in {root["page"]["id"], child["page"]["id"]}
    assert "_np2b_duplicate_request_sha256" not in payload["page"]["settings"]

    scoped = client.get(f"/api/notion/workspaces/{workspace_id}/pages").get_json()
    assert scoped["count"] == 4
    duplicate_children = [
        item
        for item in scoped["items"]
        if item["parent_id"] == duplicate_root_id
    ]
    assert [item["title"] for item in duplicate_children] == ["Synthetic Child"]

    replay = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{root['page']['id']}/duplicate",
        json=body,
    )
    assert replay.status_code == 200
    assert replay.get_json()["page"]["id"] == duplicate_root_id
    assert len(notion_store.list_pages("owner")) == 4
    assert client.get(f"/api/notion/workspaces/{workspace_id}/pages").get_json()[
        "count"
    ] == 4

    conflict = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{child['page']['id']}/duplicate",
        json=body,
    )
    assert conflict.status_code == 409
    assert len(notion_store.list_pages("owner")) == 4

    unrelated = notion_store.create_page("owner", title="Unbound archived page")
    notion_store.archive_page("owner", unrelated["page"]["id"])
    lifecycle_version = replay.get_json()["workspace"]["version"]
    archive_body = {
        "base_version": lifecycle_version,
        "operation_id": "ui-tree-archive-duplicate",
    }
    archived = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{duplicate_root_id}/trash",
        json=archive_body,
    )
    assert archived.status_code == 200
    assert archived.get_json()["archived_count"] == 2
    assert client.get(f"/api/notion/workspaces/{workspace_id}/pages").get_json()[
        "count"
    ] == 2
    trash = client.get(f"/api/notion/workspaces/{workspace_id}/trash")
    assert trash.status_code == 200
    assert trash.get_json()["count"] == 2
    assert unrelated["page"]["id"] not in {
        item["id"] for item in trash.get_json()["items"]
    }

    archive_replay = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{duplicate_root_id}/trash",
        json=archive_body,
    )
    assert archive_replay.status_code == 200
    assert archive_replay.get_json()["replayed"] is True

    restore_body = {
        "base_version": lifecycle_version,
        "operation_id": "ui-tree-restore-duplicate",
    }
    restored = client.post(
        f"/api/notion/workspaces/{workspace_id}/trash/{duplicate_root_id}/restore",
        json=restore_body,
    )
    assert restored.status_code == 200
    assert restored.get_json()["restored_count"] == 2
    assert restored.get_json()["page"]["id"] == duplicate_root_id
    assert client.get(f"/api/notion/workspaces/{workspace_id}/pages").get_json()[
        "count"
    ] == 4
    assert client.get(f"/api/notion/workspaces/{workspace_id}/trash").get_json()[
        "count"
    ] == 0

    restore_replay = client.post(
        f"/api/notion/workspaces/{workspace_id}/trash/{duplicate_root_id}/restore",
        json=restore_body,
    )
    assert restore_replay.status_code == 200
    assert restore_replay.get_json()["replayed"] is True

    old_archive_after_restore = client.post(
        f"/api/notion/workspaces/{workspace_id}/pages/{duplicate_root_id}/trash",
        json=archive_body,
    )
    assert old_archive_after_restore.status_code == 409
    permanent_delete = client.delete(
        f"/api/notion/workspaces/{workspace_id}/trash/{duplicate_root_id}"
    )
    assert permanent_delete.status_code == 404
    assert store.verify_deep_health()["healthy"] is True


def test_source_proof_revalidation_fails_after_page_revision_changes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    page = notion_store.create_page("owner", title="Before")
    page_id = page["page"]["id"]
    proof = notion_store.capture_legacy_page_source_proof("owner", "owner", page_id)
    assert notion_store.revalidate_legacy_page_source_proof(proof) is True

    notion_store.update_page("owner", page_id, {"title": "After"})
    assert notion_store.revalidate_legacy_page_source_proof(proof) is False
