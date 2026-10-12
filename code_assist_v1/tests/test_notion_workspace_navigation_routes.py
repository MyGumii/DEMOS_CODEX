from __future__ import annotations

import json
import tempfile

from flask import Flask
import pytest

from code_assist_v1 import notion_store, routes_notion_workspace
from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    NAVIGATION_SECTIONS,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)
from code_assist_v1.routes_notion_workspace import register_notion_workspace_routes


@pytest.fixture()
def navigation_route_app(monkeypatch):
    temporary = tempfile.TemporaryDirectory()
    monkeypatch.delenv(routes_notion_workspace.NAVIGATION_UI_ENABLE_ENV, raising=False)
    monkeypatch.delenv(
        routes_notion_workspace.NAVIGATION_UI_KILL_SWITCH_ENV,
        raising=False,
    )
    for key in (
        routes_notion_workspace.CORE_UI_PILOT_ENABLE_ENV,
        routes_notion_workspace.CORE_UI_PILOT_KILL_SWITCH_ENV,
        routes_notion_workspace.CORE_UI_PILOT_SUBJECTS_ENV,
        routes_notion_workspace.CORE_UI_PILOT_WORKSPACES_ENV,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", temporary.name)
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    monkeypatch.setattr(
        routes_notion_workspace,
        "list_public_users",
        lambda include_data=False: [
            {"id": "owner", "disabled": False},
            {"id": "member_one", "disabled": False},
        ],
    )
    app = Flask("notion-workspace-navigation-routes")
    app.config.update(TESTING=True, SECRET_KEY="navigation-route-secret")
    register_notion_workspace_routes(app)
    yield app, app.test_client(), temporary.name
    temporary.cleanup()


def test_workspace_list_exposes_default_off_navigation_ui_and_kill_switch(
    navigation_route_app,
    monkeypatch,
) -> None:
    _app, client, _data_root = navigation_route_app
    workspace = _create_workspace(client)

    disabled = client.get("/api/notion/workspaces")
    assert disabled.status_code == 200
    assert disabled.get_json()["capabilities"]["navigation_ui"] is False

    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_ENABLE_ENV, "true")
    non_exact = client.get("/api/notion/workspaces")
    assert non_exact.status_code == 200
    assert non_exact.get_json()["capabilities"]["navigation_ui"] is False

    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_ENABLE_ENV, "1")
    legacy_flag_alone = client.get("/api/notion/workspaces")
    assert legacy_flag_alone.status_code == 200
    assert legacy_flag_alone.get_json()["capabilities"]["navigation_ui"] is False
    assert legacy_flag_alone.get_json()["capabilities"]["core_ui_pilot"] is False

    _enable_core_ui_pilot(monkeypatch, workspace["id"])
    enabled = client.get("/api/notion/workspaces")
    assert enabled.status_code == 200
    assert enabled.get_json()["capabilities"]["navigation_ui"] is True
    assert enabled.get_json()["capabilities"]["core_ui_pilot"] is True
    readiness = client.get("/api/notion/ui-pilot/readiness")
    assert readiness.status_code == 200
    assert readiness.get_json()["pilot"]["ready"] is True
    assert workspace["id"] not in readiness.get_data(as_text=True)
    assert "owner" not in readiness.get_data(as_text=True)

    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_KILL_SWITCH_ENV, "1")
    killed = client.get("/api/notion/workspaces")
    assert killed.status_code == 200
    assert killed.get_json()["capabilities"]["navigation_ui"] is False

    monkeypatch.setenv(routes_notion_workspace.NAVIGATION_UI_KILL_SWITCH_ENV, "true")
    fail_closed = client.get("/api/notion/workspaces")
    assert fail_closed.status_code == 200
    assert fail_closed.get_json()["capabilities"]["navigation_ui"] is False


def test_pilot_readiness_is_identifier_free_and_disabled_preflight_has_no_store_io(
    navigation_route_app,
    monkeypatch,
) -> None:
    _app, client, _data_root = navigation_route_app
    trap_calls = 0

    def store_trap():
        nonlocal trap_calls
        trap_calls += 1
        raise AssertionError("disabled pilot must not open the catalog")

    monkeypatch.setattr(routes_notion_workspace, "_store", store_trap)
    disabled = client.get("/api/notion/ui-pilot/readiness")
    assert disabled.status_code == 200
    assert disabled.get_json() == {
        "status": "pilot_readiness",
        "pilot": {
            "contract": "code_assist.notion.core_free_ui.pilot_control.v1",
            "default_off": True,
            "enabled": False,
            "ready": False,
            "allowlist_required": True,
            "kill_switch_available": True,
            "status": "not_ready",
        },
    }
    assert trap_calls == 0
    assert "owner" not in disabled.get_data(as_text=True)


def test_pilot_readiness_requires_authenticated_actor(
    navigation_route_app,
    monkeypatch,
) -> None:
    _app, client, _data_root = navigation_route_app
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: None)

    response = client.get("/api/notion/ui-pilot/readiness")

    assert response.status_code == 403
    assert response.get_json()["code"] == "permission_denied"


def _enable_core_ui_pilot(monkeypatch, *workspace_ids: str) -> None:
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_ENABLE_ENV, "1")
    monkeypatch.setenv(routes_notion_workspace.CORE_UI_PILOT_SUBJECTS_ENV, "owner")
    monkeypatch.setenv(
        routes_notion_workspace.CORE_UI_PILOT_WORKSPACES_ENV,
        ",".join(workspace_ids),
    )


def _create_workspace(client) -> dict:
    response = client.post(
        "/api/notion/workspaces",
        json={
            "name": "Navigation routes",
            "operation_id": "create-navigation-route-workspace",
        },
    )
    assert response.status_code == 201
    return response.get_json()["workspace"]


def _store(data_root: str) -> WorkspaceStore:
    return WorkspaceStore(catalog_path_for_user_data_root(data_root))


def _sections(*, collapsed: bool = False) -> list[dict[str, object]]:
    return [
        {
            "section": section,
            "display_order": index,
            "hidden": False,
            "collapsed": collapsed and index == 0,
            "item_limit": 10,
        }
        for index, section in enumerate(NAVIGATION_SECTIONS)
    ]


def _bind_page(store: WorkspaceStore, workspace: dict) -> None:
    proof = LegacyPageSourceProof(
        "owner",
        "owner",
        "p_navigation",
        1,
        100,
        "sha256:" + "a" * 64,
    )
    staged = store.stage_page_source_binding(
        "owner",
        workspace["id"],
        proof,
        base_version=workspace["version"],
        operation_id="navigation-route-stage",
    )
    store.activate_page_source_binding(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=lambda _proof: True,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="navigation-route-activate",
    )


def _assert_no_store(response) -> None:
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_navigation_get_returns_read_time_defaults_without_creating_state(
    navigation_route_app,
) -> None:
    _app, client, data_root = navigation_route_app
    workspace = _create_workspace(client)
    store = _store(data_root)
    before = store.audit_counts()

    response = client.get(
        f"/api/notion/workspaces/{workspace['id']}/navigation"
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "navigation_loaded",
        "workspace_id": workspace["id"],
        "subject_id": "owner",
        "membership_epoch": 1,
        "preference_version": 0,
        "favorite_version": 0,
        "sidebar": {"width_px": 270, "collapsed": False},
        "sections": _sections(),
        "favorites": [],
    }
    _assert_no_store(response)
    assert store.audit_counts() == before
    con = store._read_connection()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_navigation_states"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_navigation_preferences_route_is_strict_cas_and_replay_safe(
    navigation_route_app,
) -> None:
    _app, client, _data_root = navigation_route_app
    workspace = _create_workspace(client)
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/preferences"
    payload = {
        "expected_preference_version": 0,
        "sidebar_width_px": 320,
        "sidebar_collapsed": True,
        "sections": _sections(collapsed=True),
        "operation_id": "navigation-route-preferences",
    }

    first = client.patch(path, json=payload)
    replay = client.patch(path, json=payload)
    assert first.status_code == replay.status_code == 200
    assert replay.get_json() == first.get_json()
    assert first.get_json()["status"] == "navigation_preferences_updated"
    assert first.get_json()["preference_version"] == 1
    _assert_no_store(first)

    stale = client.patch(path, json={**payload, "operation_id": "stale-operation"})
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "workspace_revision_conflict"
    assert stale.get_json()["current_version"] == 1

    unknown = client.patch(path, json={**payload, "unknown": True})
    assert unknown.status_code == 400
    assert unknown.get_json()["code"] == "invalid_request"
    wrong_type = client.patch(
        path,
        json={**payload, "expected_preference_version": False},
    )
    assert wrong_type.status_code == 400
    non_object = client.patch(path, json=["not", "an", "object"])
    assert non_object.status_code == 400
    assert non_object.get_json()["code"] == "invalid_json_body"


def test_navigation_mutations_reject_duplicate_and_nonfinite_json(
    navigation_route_app,
) -> None:
    _app, client, _data_root = navigation_route_app
    workspace = _create_workspace(client)
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/preferences"
    payload = {
        "expected_preference_version": 0,
        "sidebar_width_px": 240,
        "sidebar_collapsed": False,
        "sections": _sections(),
        "operation_id": "navigation-route-strict-json",
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    duplicate = encoded[:-1] + ',"operation_id":"navigation-route-strict-json"}'
    nonfinite = encoded.replace('"sidebar_width_px":240', '"sidebar_width_px":NaN')

    duplicate_response = client.patch(
        path, data=duplicate, content_type="application/json"
    )
    nonfinite_response = client.patch(
        path, data=nonfinite, content_type="application/json"
    )

    assert duplicate_response.status_code == nonfinite_response.status_code == 400
    assert duplicate_response.get_json()["code"] == "invalid_json_body"
    assert nonfinite_response.get_json()["code"] == "invalid_json_body"
    _assert_no_store(duplicate_response)
    _assert_no_store(nonfinite_response)


def test_navigation_mutation_body_is_bounded_before_json_materialization(
    navigation_route_app,
) -> None:
    _app, client, data_root = navigation_route_app
    workspace = _create_workspace(client)
    store = _store(data_root)
    before = store.audit_counts()
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/preferences"

    response = client.patch(
        path,
        data='{"padding":"' + ("x" * (64 * 1024)) + '"}',
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "error": "Request payload is too large",
        "code": "payload_too_large",
        "message": "Request payload is too large",
    }
    _assert_no_store(response)
    assert store.audit_counts() == before


def test_navigation_mutation_requires_json_media_type(
    navigation_route_app,
) -> None:
    _app, client, data_root = navigation_route_app
    workspace = _create_workspace(client)
    store = _store(data_root)
    before = store.audit_counts()
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/favorites"
    raw = json.dumps(
        {
            "source_owner_subject_id": "owner",
            "source_page_id": "p_navigation",
            "expected_favorite_version": 0,
            "operation_id": "navigation-route-wrong-media",
        }
    )

    response = client.put(path, data=raw, content_type="text/plain")

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_json_body"
    _assert_no_store(response)
    assert store.audit_counts() == before


def test_navigation_favorite_routes_are_idempotent_and_secret_free(
    navigation_route_app,
) -> None:
    _app, client, data_root = navigation_route_app
    workspace = _create_workspace(client)
    store = _store(data_root)
    _bind_page(store, workspace)
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/favorites"
    base = {
        "source_owner_subject_id": "owner",
        "source_page_id": "p_navigation",
        "expected_favorite_version": 0,
        "operation_id": "navigation-route-favorite-add",
    }

    added = client.put(path, json=base)
    replay = client.put(path, json=base)
    assert added.status_code == replay.status_code == 200
    assert replay.get_json() == added.get_json()
    assert added.get_json()["status"] == "page_favorite_added"
    assert added.get_json()["changed"] is True
    assert added.get_json()["favorite_version"] == 1
    _assert_no_store(added)

    removed = client.delete(
        path,
        json={
            **base,
            "expected_favorite_version": 1,
            "operation_id": "navigation-route-favorite-remove",
        },
    )
    assert removed.status_code == 200
    assert removed.get_json()["status"] == "page_favorite_removed"
    assert removed.get_json()["changed"] is True
    assert removed.get_json()["favorite_version"] == 2
    _assert_no_store(removed)

    wire = (added.get_data(as_text=True) + removed.get_data(as_text=True)).lower()
    for forbidden in (
        "page_title",
        "page_content",
        "source_path",
        "file_name",
        "raw_public_token",
        "lease_token",
    ):
        assert forbidden not in wire


def test_navigation_routes_do_not_leak_membership_or_page_visibility(
    navigation_route_app,
    monkeypatch,
) -> None:
    _app, client, _data_root = navigation_route_app
    workspace = _create_workspace(client)
    favorite_path = (
        f"/api/notion/workspaces/{workspace['id']}/navigation/favorites"
    )
    missing = client.put(
        favorite_path,
        json={
            "source_owner_subject_id": "owner",
            "source_page_id": "p_secret_target",
            "expected_favorite_version": 0,
            "operation_id": "navigation-route-hidden-favorite",
        },
    )
    assert missing.status_code == 404
    assert missing.get_json() == {
        "error": "not found",
        "code": "not_found",
        "message": "not found",
    }
    _assert_no_store(missing)

    monkeypatch.setattr(
        routes_notion_workspace, "current_user_id", lambda: "outsider"
    )
    denied = client.get(
        f"/api/notion/workspaces/{workspace['id']}/navigation"
    )
    assert denied.status_code == 404
    assert denied.get_json() == missing.get_json()
    _assert_no_store(denied)


@pytest.mark.parametrize("method", ["put", "delete"])
def test_navigation_favorite_route_rejects_unknown_or_missing_fields(
    navigation_route_app,
    method: str,
) -> None:
    _app, client, _data_root = navigation_route_app
    workspace = _create_workspace(client)
    path = f"/api/notion/workspaces/{workspace['id']}/navigation/favorites"
    payload = {
        "source_owner_subject_id": "owner",
        "source_page_id": "p_navigation",
        "expected_favorite_version": 0,
        "operation_id": "navigation-route-malformed",
    }
    sender = getattr(client, method)

    unknown = sender(path, json={**payload, "title": "must not be accepted"})
    missing = sender(
        path,
        json={key: value for key, value in payload.items() if key != "source_page_id"},
    )

    assert unknown.status_code == missing.status_code == 400
    assert unknown.get_json()["code"] == missing.get_json()["code"] == "invalid_request"
    _assert_no_store(unknown)
    _assert_no_store(missing)
