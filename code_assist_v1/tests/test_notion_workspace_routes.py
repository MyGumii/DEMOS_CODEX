from __future__ import annotations

import os
import tempfile

from flask import Flask
import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.routes_notion_workspace import register_notion_workspace_routes


@pytest.fixture()
def workspace_client(monkeypatch):
    temporary = tempfile.TemporaryDirectory()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", temporary.name)
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    users = [
        {"id": "owner", "display_name": "Owner", "disabled": False},
        {"id": "member_one", "display_name": "Member", "disabled": False},
        {"id": "guest_one", "display_name": "Guest", "disabled": False},
        {"id": "disabled_one", "display_name": "Disabled", "disabled": True},
    ]
    monkeypatch.setattr(
        routes_notion_workspace,
        "list_public_users",
        lambda include_data=False: users,
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="notion-workspace-route-test")
    register_notion_workspace_routes(app)
    yield app.test_client(), temporary.name
    temporary.cleanup()


def _create(client, *, operation_id: str = "create-workspace", name: str = "Engineering"):
    return client.post(
        "/api/notion/workspaces",
        json={"name": name, "icon": "E", "operation_id": operation_id},
    )


@pytest.mark.parametrize(
    ("exc", "status", "payload"),
    (
        (
            routes_notion_workspace.nws.WorkspaceSubstrateUnavailable(),
            503,
            {
                "error": "workspace_substrate_unavailable",
                "code": "workspace_substrate_unavailable",
                "message": "workspace substrate is unavailable",
            },
        ),
        (
            routes_notion_workspace.nws.WorkspaceRevisionConflict(7),
            409,
            {
                "error": "workspace_revision_conflict",
                "code": "workspace_revision_conflict",
                "message": "workspace revision conflict; current version is 7",
                "current_version": 7,
            },
        ),
        (
            routes_notion_workspace.nws.WorkspaceOperationConflict("operation-7"),
            409,
            {
                "error": "workspace_operation_id_conflict",
                "code": "workspace_operation_id_conflict",
                "message": (
                    "operation_id was already used for a different workspace request"
                ),
                "operation_id": "operation-7",
            },
        ),
    ),
)
def test_workspace_overlay_error_mapper_preserves_stable_wire_contract(
    exc: BaseException,
    status: int,
    payload: dict[str, object],
) -> None:
    app = Flask("workspace-overlay-error-contract")
    with app.test_request_context("/api/notion/workspaces"):
        response, actual_status = routes_notion_workspace._workspace_error_response(exc)

    assert actual_status == status
    assert response.get_json() == payload
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_missing_catalog_get_is_stable_503_and_has_no_filesystem_side_effect(
    workspace_client,
) -> None:
    client, data_root = workspace_client
    catalog_parent = os.path.join(data_root, ".notion-workspace-v2")

    response = client.get("/api/notion/workspaces")

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "workspace_substrate_unavailable",
        "code": "workspace_substrate_unavailable",
        "message": "workspace substrate is unavailable",
    }
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert not os.path.exists(catalog_parent)


def test_create_list_get_and_idempotent_conflict_contract(workspace_client) -> None:
    client, _ = workspace_client
    created = _create(client)
    assert created.status_code == 201
    created_json = created.get_json()
    assert created_json["status"] == "created"
    workspace = created_json["workspace"]
    workspace_id = workspace["id"]
    assert workspace["access"] == {"role": "owner", "epoch": 1}

    replay = _create(client)
    assert replay.status_code == 201
    assert replay.get_json() == created_json

    mismatch = _create(client, name="Different")
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "workspace_operation_id_conflict"

    listed = client.get("/api/notion/workspaces")
    assert listed.status_code == 200
    assert listed.get_json()["items"] == [workspace]
    assert listed.get_json()["capabilities"]["workspace_v2"] is True

    detail = client.get(f"/api/notion/workspaces/{workspace_id}")
    assert detail.status_code == 200
    assert detail.get_json()["workspace"]["id"] == workspace_id
    assert detail.get_json()["workspace"]["members"][0]["subject_id"] == "owner"


def test_member_guest_group_teamspace_routes_apply_cas_and_strict_subjects(
    workspace_client,
) -> None:
    client, _ = workspace_client
    workspace_id = _create(client).get_json()["workspace"]["id"]

    member = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json={
            "role": "member",
            "base_version": 1,
            "operation_id": "member-one",
        },
    )
    assert member.status_code == 200
    assert member.get_json()["member"]["role"] == "member"
    assert member.get_json()["workspace"]["version"] == 2

    stale = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/guest_one",
        json={
            "role": "member",
            "base_version": 1,
            "operation_id": "stale-member",
        },
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "workspace_revision_conflict"
    assert stale.get_json()["current_version"] == 2

    disabled = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/disabled_one",
        json={
            "role": "member",
            "base_version": 2,
            "operation_id": "disabled-member",
        },
    )
    assert disabled.status_code == 400
    assert disabled.get_json()["code"] == "invalid_request"

    guest = client.put(
        f"/api/notion/workspaces/{workspace_id}/guests/guest_one",
        json={"base_version": 2, "operation_id": "guest-one"},
    )
    assert guest.status_code == 200
    assert guest.get_json()["guest"]["role"] == "guest"
    assert guest.get_json()["workspace"]["version"] == 3

    group = client.post(
        f"/api/notion/workspaces/{workspace_id}/groups",
        json={"name": "Platform", "base_version": 3, "operation_id": "group-one"},
    )
    assert group.status_code == 201
    group_id = group.get_json()["group"]["id"]
    assert group.get_json()["workspace"]["version"] == 4

    group_member = client.put(
        f"/api/notion/workspaces/{workspace_id}/groups/{group_id}/members/member_one",
        json={"base_version": 4, "operation_id": "group-member-one"},
    )
    assert group_member.status_code == 200
    assert group_member.get_json()["group"]["member_subject_ids"] == ["member_one"]
    assert group_member.get_json()["workspace"]["version"] == 5

    guest_group = client.put(
        f"/api/notion/workspaces/{workspace_id}/groups/{group_id}/members/guest_one",
        json={"base_version": 5, "operation_id": "guest-group"},
    )
    assert guest_group.status_code == 400
    assert "active workspace members" in guest_group.get_json()["message"]

    teamspace = client.post(
        f"/api/notion/workspaces/{workspace_id}/teamspaces",
        json={
            "name": "Core",
            "visibility": "private",
            "base_version": 5,
            "operation_id": "teamspace-core",
        },
    )
    assert teamspace.status_code == 201
    teamspace_id = teamspace.get_json()["teamspace"]["id"]
    assert teamspace.get_json()["workspace"]["version"] == 6

    joined = client.put(
        f"/api/notion/workspaces/{workspace_id}/teamspaces/{teamspace_id}/members/member_one",
        json={
            "role": "member",
            "base_version": 6,
            "operation_id": "teamspace-member-one",
        },
    )
    assert joined.status_code == 200
    assert joined.get_json()["workspace"]["version"] == 7

    teamspaces = client.get(f"/api/notion/workspaces/{workspace_id}/teamspaces")
    assert teamspaces.status_code == 200
    assert teamspaces.get_json()["count"] == 1
    assert teamspaces.get_json()["items"][0]["id"] == teamspace_id


def test_json_and_field_validation_is_fail_closed(workspace_client) -> None:
    client, _ = workspace_client
    malformed = client.post(
        "/api/notion/workspaces",
        data=b"[1,2]",
        content_type="application/json",
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["code"] == "invalid_json_body"

    missing_operation = client.post(
        "/api/notion/workspaces",
        json={"name": "No operation"},
    )
    assert missing_operation.status_code == 400
    assert missing_operation.get_json()["code"] == "invalid_request"

    workspace_id = _create(client).get_json()["workspace"]["id"]
    bool_version = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json={
            "role": "member",
            "base_version": True,
            "operation_id": "bool-version",
        },
    )
    assert bool_version.status_code == 400
    assert bool_version.get_json()["code"] == "invalid_request"


def test_revoked_member_immediately_disappears_from_workspace_scope(
    workspace_client, monkeypatch
) -> None:
    client, _ = workspace_client
    workspace_id = _create(client).get_json()["workspace"]["id"]
    added = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json={
            "role": "member",
            "base_version": 1,
            "operation_id": "member-one",
        },
    )
    assert added.status_code == 200

    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "member_one")
    assert client.get(f"/api/notion/workspaces/{workspace_id}").status_code == 200

    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "owner")
    revoked = client.delete(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json={"base_version": 2, "operation_id": "revoke-member"},
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["status"] == "member_revoked"

    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "member_one")
    hidden = client.get(f"/api/notion/workspaces/{workspace_id}")
    assert hidden.status_code == 404
    assert hidden.get_json()["code"] == "not_found"
    assert client.get("/api/notion/workspaces").get_json()["items"] == []


def test_idempotent_member_retry_replays_after_auth_subject_is_disabled(
    workspace_client, monkeypatch
) -> None:
    client, _ = workspace_client
    workspace_id = _create(client).get_json()["workspace"]["id"]
    payload = {
        "role": "member",
        "base_version": 1,
        "operation_id": "member-before-disabled",
    }
    first = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json=payload,
    )
    assert first.status_code == 200

    monkeypatch.setattr(
        routes_notion_workspace,
        "list_public_users",
        lambda include_data=False: [
            {"id": "owner", "disabled": False},
            {"id": "member_one", "disabled": True},
        ],
    )
    replay = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json=payload,
    )
    assert replay.status_code == 200
    assert replay.get_json() == first.get_json()

    fresh = client.put(
        f"/api/notion/workspaces/{workspace_id}/members/member_one",
        json={
            "role": "member",
            "base_version": 2,
            "operation_id": "member-after-disabled",
        },
    )
    assert fresh.status_code == 400
    assert fresh.get_json()["code"] == "invalid_request"
    assert "active target_subject_id" in fresh.get_json()["message"]


def test_workspace_routes_are_tenant_scoped_without_legacy_directory_scan(
    workspace_client, monkeypatch
) -> None:
    client, _ = workspace_client

    def fail_legacy_scan():
        raise AssertionError("legacy per-user directory scan must not run")

    monkeypatch.setattr(notion_store, "_known_workspace_users", fail_legacy_scan)
    first = _create(client, operation_id="tenant-one", name="Tenant one").get_json()[
        "workspace"
    ]
    second = _create(client, operation_id="tenant-two", name="Secret tenant").get_json()[
        "workspace"
    ]
    added = client.put(
        f"/api/notion/workspaces/{first['id']}/members/member_one",
        json={
            "role": "member",
            "base_version": 1,
            "operation_id": "tenant-one-member",
        },
    )
    assert added.status_code == 200

    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: "member_one")
    listed = client.get("/api/notion/workspaces")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.get_json()["items"]] == [first["id"]]
    assert "Secret tenant" not in listed.get_data(as_text=True)

    hidden = client.get(f"/api/notion/workspaces/{second['id']}")
    assert hidden.status_code == 404
    assert hidden.get_json()["code"] == "not_found"
    assert "Secret tenant" not in hidden.get_data(as_text=True)
