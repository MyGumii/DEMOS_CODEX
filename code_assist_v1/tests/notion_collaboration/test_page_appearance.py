"""UI-ICON-005: exact appearance writes, metadata reads and live delivery."""
import json
import sqlite3
from types import SimpleNamespace

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_pages, routes_notion_workspace
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance, project_page_metadata
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment


def request(authority, icon="ni:flask:blue", operation="op-ui-icon-save-0001"):
    page = authority.read_document("owner", "page-e1")
    return dict(updates={"icon": icon}, operation_id=operation,
                expected_document_sequence=page["document_sequence"],
                expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def snapshot(catalog):
    with sqlite3.connect(catalog) as con:
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'workspace_collaboration_%'")]
        return {name: sorted(con.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in tables}


def test_ui_icon_005_save_remove_replay_and_preserve_body(tmp_path):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    before = snapshot(catalog)
    req = request(authority)
    saved = update_page_appearance(authority, "owner", workspace, "page-e1", **req)
    after = authority.read_document("owner", "page-e1")
    assert after["icon"] == "ni:flask:blue"
    assert after["document_sequence"] == req["expected_document_sequence"] + 1
    assert saved["page"]["can_change_appearance"] is True
    assert saved == update_page_appearance(authority, "owner", workspace, "page-e1", **req)
    mutated = {"workspace_collaboration_documents", "workspace_collaboration_events",
               "workspace_collaboration_audit_events", "workspace_collaboration_outbox", "workspace_collaboration_operations"}
    after_tables = snapshot(catalog)
    assert {k:v for k,v in before.items() if k not in mutated} == {k:v for k,v in after_tables.items() if k not in mutated}
    replay = transport.canonical_authority().replay_domain_events("content-editor", "page-e1", after_document_sequence=req["expected_document_sequence"])
    assert [event.event_type for event in replay.events] == ["page.appearance.updated"]
    assert replay.events[0].operation_id == req["operation_id"]
    with pytest.raises(OperationConflict):
        update_page_appearance(authority, "owner", workspace, "page-e1", **{**req, "updates":{"icon":"⭐"}})
    removed = update_page_appearance(authority, "schema-editor", workspace, "page-e1", **request(authority, "", "op-ui-icon-remove-0001"))
    assert removed["page"]["icon"] == ""
    assert authority.read_document("owner", "page-e1")["icon"] == ""
    # The receipt stays immutable even after a subsequent change.
    assert saved == update_page_appearance(authority, "owner", workspace, "page-e1", **req)


def test_ui_icon_005_denial_sequence_lock_and_rollback(tmp_path):
    authority, _, _, catalog, workspace = _environment(tmp_path)
    req = request(authority)
    before = snapshot(catalog)
    with pytest.raises(NotFoundOrDenied):
        update_page_appearance(authority, "content-editor", workspace, "page-e1", **req)
    with pytest.raises(NotFoundOrDenied):
        update_page_appearance(authority, "owner", "ws_wrong", "page-e1", **req)
    with pytest.raises(DocumentSequenceConflict):
        update_page_appearance(authority, "owner", workspace, "page-e1", **{**req, "expected_document_sequence":999})
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox"):
        with pytest.raises(InjectedFailure):
            update_page_appearance(authority, "owner", workspace, "page-e1", **req, fault=fault)
        assert snapshot(catalog) == before
    with sqlite3.connect(catalog) as con:
        con.execute("UPDATE workspace_collaboration_documents SET settings_json=? WHERE id='page-e1'", (json.dumps({"locked":True}),))
    locked = snapshot(catalog)
    with pytest.raises(PermissionError):
        update_page_appearance(authority, "owner", workspace, "page-e1", **req)
    assert snapshot(catalog) == locked


def test_ui_icon_005_metadata_projection_and_title_search(tmp_path, monkeypatch):
    authority, _, _, _, workspace = _environment(tmp_path)
    old = {"id":"page-e1", "title":"old sidebar", "icon":"🚀", "parent_id":"", "position":0}
    projected = project_page_metadata(authority, "owner", workspace, [old, {"id":"not-bound"}])
    assert len(projected) == 1
    assert projected[0]["title"] == "Collaborative editing"
    assert projected[0]["icon"] == ""
    content = project_page_metadata(authority, "content-editor", workspace, [old])
    assert content[0]["can_change_appearance"] is False
    store = SimpleNamespace(iam=authority,
        get_workspace=lambda *args:{"access":{"epoch":1}},
        list_page_source_bindings=lambda *args:[{"source_owner_subject_id":"owner", "source_page_id":"page-e1", "placement":"private", "version":1}])
    monkeypatch.setattr(notion_workspace_pages.notion_store, "list_pages_from_owner_by_ids", lambda *args:[old])
    assert notion_workspace_pages.list_workspace_pages(store,"owner",workspace,query="Collaborative")["count"] == 1
    assert notion_workspace_pages.list_workspace_pages(store,"owner",workspace,query="old sidebar")["count"] == 0


def test_ui_icon_005_http_contract_uses_exact_authority(tmp_path, monkeypatch):
    authority, _, _, _, workspace = _environment(tmp_path)
    actor = {"id":"owner"}
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda:SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes_notion_workspace, "_collaborative_runtime_enabled", lambda:True)
    monkeypatch.setattr(routes_notion_workspace, "current_user_id", lambda:actor["id"])
    app = Flask(__name__); app.config.update(TESTING=True, SECRET_KEY="synthetic-ui-icons")
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    base = f"/api/notion/workspaces/{workspace}/pages/page-e1"
    req = request(authority)
    saved = client.patch(base + "/appearance", json=req)
    assert saved.status_code == 200, saved.json
    assert saved.headers["Cache-Control"].startswith("no-store")
    assert client.patch(base + "/appearance", json=req).json == saved.json
    loaded = client.get(base + "/collaboration")
    assert loaded.status_code == 200, loaded.json
    assert loaded.json["page"]["icon"] == req["updates"]["icon"]
    assert loaded.json["capabilities"]["collaborative_appearance"] is True
    for bad in ({**req,"extra":1}, {**req,"updates":{"title":"do not change body"}}, {**req,"updates":{"icon":"ni:no-such:blue"}}):
        assert client.patch(base + "/appearance", json=bad).status_code == 400
    actor["id"] = "content-editor"
    assert client.patch(base + "/appearance", json=request(authority)).status_code in (403,404)


def test_ui_icon_005_image_content_and_page_ownership(tmp_path):
    from io import BytesIO
    import base64
    from code_assist_v1.notion_collaboration.canonical_authority import _sha256
    from code_assist_v1.tests.notion_collaboration.test_file_authority import _authority
    authority, _, _ = _authority(tmp_path)
    workspace = authority.read_document("owner", "page-e1")["workspace_id"]
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6xj8AAAAASUVORK5CYII=")

    def upload(payload, suffix):
        page = authority.read_document("owner", "page-e1")
        session = authority.begin_upload("owner", "page-e1", original_name="icon.png", mime_type="image/png",
            total_size=len(payload), expected_sha256=_sha256(payload), chunk_size=65536,
            expected_document_sequence=page["document_sequence"], expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"], operation_id=f"op-icon-file-begin-{suffix}")
        authority.stage_chunk("owner", session["session_id"], chunk_index=0, stream=BytesIO(payload), operation_id=f"op-icon-file-chunk-{suffix}")
        return authority.finalize_upload("owner", session["session_id"], operation_id=f"op-icon-file-final-{suffix}")["file_id"]

    file_id = upload(png, "valid")
    req = request(authority, f"nf:{file_id}")
    saved = update_page_appearance(authority,"owner",workspace,"page-e1",**req)
    assert saved["page"]["icon"] == f"nf:{file_id}"
    authority.create_document("owner",workspace,"page-other",title="Other page",blocks=[],
        expected_workspace_epoch=req["expected_workspace_epoch"],operation_id="op-icon-other-page-create")
    other = authority.read_document("owner","page-other")
    with pytest.raises(NotFoundOrDenied):
        update_page_appearance(authority,"owner",workspace,"page-other",**{
            **req,"operation_id":"op-icon-foreign-page", "expected_document_sequence":other["document_sequence"],
            "expected_document_epoch":other["document_epoch"]})
    fake_id = upload(b"<svg>This is not a PNG</svg>", "invalid")
    with pytest.raises(ValueError, match="content"):
        update_page_appearance(authority,"owner",workspace,"page-e1",**request(authority,f"nf:{fake_id}","op-icon-invalid-image"))
