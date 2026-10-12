"""UI-META-005: authoritative activity reads do not replay or write content."""
from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import durable_transport, page_archive
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_page_archive import (
    environment, guest_environment, image_environment, create, request, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_title_projection import yjs, append
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import revoke


def forbidden(*args, **kwargs):
    raise AssertionError("activity reads cannot load bodies, replay titles or write")


def test_ui_page_activity_current_body_ack_and_read_only_http(environment, monkeypatch, yjs):
    store, ws = environment
    client, actor = client_for(monkeypatch, store)
    endpoint = f"/api/notion/workspaces/{ws}/pages/page-e1/activity"
    before = client.get(endpoint).json["page"]
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    with monkeypatch.context() as patch:
        patch.setattr(durable_transport.time, "time", lambda: before["updated_at"] + 60)
        append(store.iam, transport, ws, [yjs["body"]])
    witness = dump(store)
    for name in ("_transaction", "read_document", "_editing_snapshot"):
        monkeypatch.setattr(store.iam, name, forbidden)
    monkeypatch.setattr(routes.iam_runtime, "collaborative_page_payload", forbidden)
    response = client.get(endpoint)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    current = response.json["page"]
    assert set(current) == {"id", "workspace_id", "document_sequence", "workspace_epoch", "document_epoch", "updated_at", "last_edit"}
    assert current["last_edit"] == {"actor_subject_id": "owner", "document_sequence": current["document_sequence"], "edited_at": current["updated_at"]}
    assert current["updated_at"] == before["updated_at"] + 60
    assert current["document_sequence"] == before["document_sequence"] + 1
    actor["id"] = "outsider"  # The fixture has a current direct view grant.
    assert client.get(endpoint).json == response.json
    for url in (endpoint.replace(ws, "wrong-workspace"), endpoint.replace("page-e1", "missing-page")):
        denied = client.get(url)
        assert denied.status_code == 404 and "no-store" in denied.headers["Cache-Control"]
    actor["id"] = "inactive-user"
    assert client.get(endpoint).status_code == 404
    assert dump(store) == witness


def test_ui_page_activity_current_revocation_and_archived_ancestor(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "activity-child")
    client, actor = client_for(monkeypatch, store)
    endpoint = f"/api/notion/workspaces/{ws}/pages/page-e1/activity"
    actor["id"] = "outsider"
    assert client.get(endpoint).status_code == 200
    revoke(store, ws)
    assert client.get(endpoint).status_code == 404
    actor["id"] = "owner"
    assert client.get(endpoint).status_code == 200
    page_archive.set_page_archived(store.iam, "owner", ws, "page-e1", **request(store, ws))
    witness = dump(store)
    assert client.get(endpoint).status_code == 404
    assert client.get(endpoint.replace("page-e1", "activity-child")).status_code == 404
    assert dump(store) == witness


def test_ui_page_activity_ordinary_runtime_does_not_fall_back(monkeypatch):
    client, _ = client_for(monkeypatch, object(), collaborative=False)
    response = client.get("/api/notion/workspaces/ws/pages/page/activity")
    assert response.status_code == 404 and "no-store" in response.headers["Cache-Control"]


def test_ui_page_menu_last_editor_excludes_later_comments_and_permissions(environment, monkeypatch):
    store, ws = environment
    a = store.iam
    page = a.read_document("owner", "page-e1")
    a.set_page_grant("owner", "page-e1", "subject", "content-editor", role="edit", inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="menu-editor-grant")
    page = a.read_document("content-editor", "page-e1")
    result = a.execute_typed_command("content-editor", "page-e1", command_kind="block.insert", target_id="menu-editor-block",
        payload={"type": "text", "content": "Another author's edit", "props": {}, "after_block_id": None},
        operation_id="menu-editor-body", **{"expected_" + key: page[key] for key in ("workspace_epoch", "document_epoch", "document_sequence")})
    client, actor = client_for(monkeypatch, store)
    endpoint = f"/api/notion/workspaces/{ws}/pages/page-e1/activity"
    edit = client.get(endpoint).json["page"]["last_edit"]
    assert edit["actor_subject_id"] == "content-editor" and edit["document_sequence"] == result["document_sequence"]
    page = a.read_document("owner", "page-e1")
    a.add_comment("owner", "page-e1", "menu-owner-comment", content="Commenting does not make me the last editor",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="menu-owner-comment")
    page = a.read_document("owner", "page-e1")
    a.set_page_grant("owner", "page-e1", "subject", "outsider", role="comment", inherit_to_descendants=False,
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"], operation_id="menu-view-grant")
    before = dump(store)
    actor["id"] = "outsider"
    response = client.get(endpoint)
    assert response.status_code == 200 and response.json["page"]["last_edit"] == edit
    assert response.json["page"]["document_sequence"] > edit["document_sequence"]
    assert dump(store) == before
