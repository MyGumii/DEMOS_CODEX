from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask
import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_collaboration_migration as m2401
from code_assist_v1 import notion_workspace_iam_migration as m2403
from code_assist_v1 import notion_workspace_realtime_transport_migration as m2402
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration import iam_runtime
from code_assist_v1.notion_collaboration.gateway_service import create_application
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.routes_notion_workspace import register_notion_workspace_routes


def _backup(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    return destination


def _migrate_to_2403(root: Path) -> tuple[Path, Path]:
    collaboration_root = root / "collaboration"
    catalog = Path(nws.catalog_path_for_user_data_root(collaboration_root))
    store = nws.WorkspaceStore(catalog)
    store.bootstrap()
    for migration, name in ((m2401, "2401"), (m2402, "2402"), (m2403, "2403")):
        backup = _backup(
            catalog,
            collaboration_root / "backups" / f"before-{name}.db",
        )
        plan = migration.build_plan(catalog, collaboration_root)
        result = migration.apply_plan(
            catalog,
            collaboration_root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup,
        )
        assert result["applied"] is True
        assert migration.build_plan(catalog, collaboration_root)["state"] == (
            "already_current"
        )
    return catalog, collaboration_root


@pytest.fixture()
def collaborative_clients(tmp_path, monkeypatch):
    catalog, collaboration_root = _migrate_to_2403(tmp_path)
    legacy_root = tmp_path / "notion-host" / "user_data"
    legacy_root.mkdir(parents=True)
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(legacy_root))
    monkeypatch.setenv(iam_runtime.RUNTIME_ENABLE_ENV, "1")
    monkeypatch.setenv(iam_runtime.RUNTIME_KILL_SWITCH_ENV, "0")
    monkeypatch.setenv(iam_runtime.CATALOG_ENV, str(catalog))
    monkeypatch.setenv(iam_runtime.USER_DATA_ROOT_ENV, str(collaboration_root))
    actor = {"id": "owner"}
    users = [
        {"id": "owner", "display_name": "Owner", "disabled": False},
        {"id": "member_one", "display_name": "Member", "disabled": False},
        {"id": "disabled_one", "display_name": "Disabled", "disabled": True},
    ]
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda: actor["id"])
    monkeypatch.setattr(
        routes_notion_workspace,
        "list_public_users",
        lambda include_data=False: users,
    )
    app = Flask("i0-p2-collaborative-sharing")
    app.config.update(TESTING=True, SECRET_KEY="i0-p2-sharing-test")
    register_notion_workspace_routes(app)
    return app.test_client(), actor, catalog, collaboration_root


def _owner_workspace(client) -> dict:
    response = client.get("/api/notion/workspaces")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["capabilities"] == {
        "workspace_v2": True,
        "schema_version": 2026082403,
        "collaborative_iam_sharing": True,
        "live_collaboration": False,
        "collaborative_editing": False,
        "second_brain_publication_intents": False,
        "database_features": False,
        "collaborative_files": False,
        "collaborative_history": False,
        "collaborative_search": False,
        "core_ui_pilot": True,
        "navigation_ui": True,
    }
    return next(item for item in payload["items"] if item["access"]["role"] == "owner")


def _create_page(client, workspace: dict) -> dict:
    response = client.post(
        f"/api/notion/workspaces/{workspace['id']}/pages",
        json={
            "base_version": workspace["version"],
            "operation_id": "i0-p2-create-shared-page",
            "parent_id": "",
            "title": "Shared design",
        },
    )
    assert response.status_code == 201
    return response.get_json()


def test_i0_p2_exact_runtime_store_auto_provisions_one_personal_workspace(
    collaborative_clients,
) -> None:
    client, actor, catalog, collaboration_root = collaborative_clients
    first = _owner_workspace(client)
    second = _owner_workspace(client)
    assert first == second

    runtime = iam_runtime.open_runtime_store(
        ["owner", "member_one"],
        {
            iam_runtime.RUNTIME_ENABLE_ENV: "1",
            iam_runtime.RUNTIME_KILL_SWITCH_ENV: "0",
            iam_runtime.CATALOG_ENV: str(catalog),
            iam_runtime.USER_DATA_ROOT_ENV: str(collaboration_root),
        },
    )
    assert runtime.capabilities()["schema_version"] == 2026082403
    assert len(runtime.list_workspaces(actor["id"])) == 1
    assert iam_runtime.CollaborativeDurableTransportAuthority.open(
        catalog, collaboration_root
    ).counts()["workspace_collaboration_operations"] >= 0
    application = create_application(
        catalog,
        collaboration_root,
        aggregate_enabled=True,
        realtime_enabled=True,
        compactor_handshake_matches=True,
    )
    assert application.gateway.start(
        compactor_handshake_matches=True
    )["ready_for_admission"] is True


def test_i0_p2_page_share_is_visible_to_second_subject_and_revoke_fences_it(
    collaborative_clients,
) -> None:
    client, actor, _catalog, _collaboration_root = collaborative_clients
    workspace = _owner_workspace(client)
    created = _create_page(client, workspace)
    page_id = created["page"]["id"]

    shares = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    )
    assert shares.status_code == 200
    share_state = shares.get_json()
    assert share_state["items"] == []
    granted = client.post(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares",
        json={
            "principal_type": "subject",
            "principal_id": "member_one",
            "role": "edit_content",
            "inherit_to_descendants": True,
            "expected_workspace_epoch": share_state["workspace_epoch"],
            "expected_document_epoch": share_state["document_epoch"],
            "operation_id": "i0-p2-share-member-one",
        },
    )
    assert granted.status_code == 200
    assert granted.get_json()["role"] == "edit_content"

    actor["id"] = "member_one"
    workspaces = client.get("/api/notion/workspaces").get_json()["items"]
    shared_workspace = next(item for item in workspaces if item["id"] == workspace["id"])
    assert shared_workspace["access"]["role"] == "guest"
    navigation = client.get(
        f"/api/notion/workspaces/{workspace['id']}/navigation"
    )
    assert navigation.status_code == 200
    assert navigation.get_json()["membership_epoch"] >= 1
    pages = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages"
    ).get_json()["items"]
    assert [item["id"] for item in pages] == [page_id]
    access = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/collaboration"
    )
    assert access.status_code == 200
    assert access.get_json()["page"]["iam_role"] == "edit_content"
    assert notion_store.get_page("member_one", page_id)["page"]["role"] == "edit"

    actor["id"] = "owner"
    refreshed = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()
    revoked = client.delete(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares/subject/member_one",
        json={
            "expected_workspace_epoch": refreshed["workspace_epoch"],
            "expected_document_epoch": refreshed["document_epoch"],
            "operation_id": "i0-p2-revoke-member-one",
        },
    )
    assert revoked.status_code == 200

    actor["id"] = "member_one"
    assert client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/collaboration"
    ).status_code == 404
    assert client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages"
    ).status_code == 404
    with pytest.raises((FileNotFoundError, PermissionError)):
        notion_store.get_page("member_one", page_id)


def test_i0_p2_workspace_invite_role_change_revoke_and_disabled_guard(
    collaborative_clients,
) -> None:
    client, actor, _catalog, _collaboration_root = collaborative_clients
    workspace = _owner_workspace(client)

    invited = client.put(
        f"/api/notion/workspaces/{workspace['id']}/members/member_one",
        json={
            "role": "member",
            "expected_workspace_epoch": workspace["security_epoch"],
            "operation_id": "i0-p2-workspace-invite",
        },
    )
    assert invited.status_code == 200
    current = invited.get_json()["workspace"]
    changed = client.put(
        f"/api/notion/workspaces/{workspace['id']}/members/member_one",
        json={
            "role": "restricted_member",
            "expected_workspace_epoch": current["security_epoch"],
            "operation_id": "i0-p2-workspace-role-change",
        },
    )
    assert changed.status_code == 200
    current = changed.get_json()["workspace"]

    disabled = client.put(
        f"/api/notion/workspaces/{workspace['id']}/members/disabled_one",
        json={
            "role": "member",
            "expected_workspace_epoch": current["security_epoch"],
            "operation_id": "i0-p2-disabled-invite",
        },
    )
    assert disabled.status_code == 404

    revoked = client.delete(
        f"/api/notion/workspaces/{workspace['id']}/members/member_one",
        json={
            "expected_workspace_epoch": current["security_epoch"],
            "operation_id": "i0-p2-workspace-revoke",
        },
    )
    assert revoked.status_code == 200
    actor["id"] = "member_one"
    visible_ids = {
        item["id"] for item in client.get("/api/notion/workspaces").get_json()["items"]
    }
    assert workspace["id"] not in visible_ids

    store = routes_notion_workspace._store()
    with pytest.raises(NotFoundOrDenied):
        store.iam.resolve_access("member_one", "does-not-exist")


def test_i0_p2_overlapping_group_and_direct_grants_reconcile_projection_and_bind_path(
    collaborative_clients,
) -> None:
    client, actor, _catalog, _collaboration_root = collaborative_clients
    workspace = _owner_workspace(client)
    created = _create_page(client, workspace)
    page_id = created["page"]["id"]
    workspace = client.get(
        f"/api/notion/workspaces/{workspace['id']}"
    ).get_json()["workspace"]

    invited = client.put(
        f"/api/notion/workspaces/{workspace['id']}/members/member_one",
        json={
            "role": "member",
            "expected_workspace_epoch": workspace["security_epoch"],
            "operation_id": "i0-p2-overlap-member",
        },
    )
    assert invited.status_code == 200
    workspace = invited.get_json()["workspace"]
    group_created = client.post(
        f"/api/notion/workspaces/{workspace['id']}/groups",
        json={
            "name": "Design reviewers",
            "base_version": workspace["version"],
            "operation_id": "i0-p2-overlap-group",
        },
    )
    assert group_created.status_code == 201
    group_payload = group_created.get_json()
    group_id = group_payload["group"]["id"]
    workspace = group_payload["workspace"]
    group_member = client.put(
        f"/api/notion/workspaces/{workspace['id']}/groups/{group_id}/members/member_one",
        json={
            "base_version": workspace["version"],
            "operation_id": "i0-p2-overlap-group-member",
        },
    )
    assert group_member.status_code == 200

    share_state = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()
    group_grant = client.post(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares",
        json={
            "principal_type": "group",
            "principal_id": group_id,
            "role": "comment",
            "inherit_to_descendants": False,
            "expected_workspace_epoch": share_state["workspace_epoch"],
            "expected_document_epoch": share_state["document_epoch"],
            "operation_id": "i0-p2-overlap-group-grant",
        },
    )
    assert group_grant.status_code == 200
    assert notion_store.get_page("member_one", page_id)["page"]["role"] == "comment"

    share_state = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()
    direct_grant = client.post(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares",
        json={
            "principal_type": "subject",
            "principal_id": "member_one",
            "role": "edit_content",
            "inherit_to_descendants": False,
            "expected_workspace_epoch": share_state["workspace_epoch"],
            "expected_document_epoch": share_state["document_epoch"],
            "operation_id": "i0-p2-overlap-direct-grant",
        },
    )
    assert direct_grant.status_code == 200
    assert notion_store.get_page("member_one", page_id)["page"]["role"] == "edit"

    share_state = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()
    direct_revoke = client.delete(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares/subject/member_one",
        json={
            "expected_workspace_epoch": share_state["workspace_epoch"],
            "expected_document_epoch": share_state["document_epoch"],
            "operation_id": "i0-p2-overlap-direct-revoke",
        },
    )
    assert direct_revoke.status_code == 200
    assert notion_store.get_page("member_one", page_id)["page"]["role"] == "comment"

    workspace = client.get(
        f"/api/notion/workspaces/{workspace['id']}"
    ).get_json()["workspace"]
    group_removed = client.delete(
        f"/api/notion/workspaces/{workspace['id']}/groups/{group_id}/members/member_one",
        json={
            "base_version": workspace["version"],
            "operation_id": "i0-p2-overlap-group-remove",
        },
    )
    assert group_removed.status_code == 200
    with pytest.raises((FileNotFoundError, PermissionError)):
        notion_store.get_page("member_one", page_id)

    second_workspace = client.post(
        "/api/notion/workspaces",
        json={
            "name": "Second workspace",
            "operation_id": "i0-p2-second-workspace",
        },
    )
    assert second_workspace.status_code == 201
    second_id = second_workspace.get_json()["workspace"]["id"]
    share_state = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()
    mismatched = client.post(
        f"/api/notion/workspaces/{second_id}/pages/{page_id}/shares",
        json={
            "principal_type": "subject",
            "principal_id": "member_one",
            "role": "view",
            "inherit_to_descendants": False,
            "expected_workspace_epoch": share_state["workspace_epoch"],
            "expected_document_epoch": share_state["document_epoch"],
            "operation_id": "i0-p2-mismatched-workspace",
        },
    )
    assert mismatched.status_code == 404
    remaining = client.get(
        f"/api/notion/workspaces/{workspace['id']}/pages/{page_id}/shares"
    ).get_json()["items"]
    assert all(item["principal_id"] != "member_one" for item in remaining)
    assert actor["id"] == "owner"
