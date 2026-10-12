from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
import pytest

from code_assist_v1 import notion_store as ns, routes_notion


def test_ui_shell_meetings_legacy_metadata_permissions_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    parent = ns.create_page("owner", title="공유 부모")["page"]
    meeting = ns.create_page("owner", title="검토 회의", parent_id=parent["id"])["page"]
    private = ns.create_page("owner", title="비공개 회의")["page"]
    for page in [meeting, private]:
        ns.replace_blocks("owner", page["id"], [
            {"id": "note-" + page["id"], "type": "ai_notes", "content": "", "props": {"transcript": "PRIVATE TRANSCRIPT"}},
            {"id": "second-" + page["id"], "type": "ai_notes", "content": "", "props": {}},
        ])
    ns.share_page("owner", parent["id"], "reader", "view")
    result = ns.list_meeting_pages("reader")
    assert [item["id"] for item in result["items"]] == [meeting["id"]]
    assert result["items"][0]["note_count"] == 2
    assert result["items"][0]["block_id"] == "note-" + meeting["id"]
    assert "PRIVATE TRANSCRIPT" not in json.dumps(result)
    assert ns.list_meeting_pages("reader", allowed_pages=[])["items"] == []
    assert ns.list_meeting_pages("reader", owner_filter="another-owner")["items"] == []
    assert ns.list_meeting_pages("owner", query="검토")["count"] == 1
    first = ns.list_meeting_pages("owner", limit=1)
    second = ns.list_meeting_pages("owner", limit=1, offset=1)
    assert first["count"] == 2 and first["has_more"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    ns.archive_page("owner", private["id"])
    assert ns.list_meeting_pages("owner")["count"] == 1
    ns.revoke_share("owner", parent["id"], "reader")
    assert ns.list_meeting_pages("reader")["items"] == []


def test_ui_shell_meetings_route_no_store_and_no_canonical_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    app = Flask(__name__)
    routes_notion.register_notion_routes(app)
    monkeypatch.setattr(routes_notion, "_uid", lambda: "reader")
    from code_assist_v1.notion_collaboration import iam_runtime
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    response = app.test_client().get("/api/notion/meetings")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    with patch.object(ns, "list_meeting_pages", side_effect=AssertionError("legacy fallback")):
        assert app.test_client().get("/api/notion/meetings").status_code == 404


def test_ui_shell_meetings_canonical_current_snapshot_and_revoke(tmp_path, monkeypatch):
    from code_assist_v1.tests.notion_collaboration.test_search_authority import _authority, _grant
    from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
    authority, _root, _catalog = _authority(tmp_path)
    source = authority.read_document("owner", "page-e1")
    workspace = source["workspace_id"]
    with pytest.raises(NotFoundOrDenied):
        authority.list_meetings("outsider", workspace)
    authority.create_document(
        "owner", workspace, "meeting-ui4", title="회의 검증",
        blocks=[{"id": "meeting-note", "type": "ai_notes", "content": "", "props": {"transcript": "PRIVATE TRANSCRIPT"}}],
        expected_workspace_epoch=source["workspace_epoch"], operation_id="ui4-meeting-create-0001",
    )
    _grant(authority, "meeting-ui4", "outsider", "ui4-meeting-grant-0001")
    _grant(authority, "page-e1", "outsider", "ui4-nonmeeting-grant-0001")
    result = authority.list_meetings("outsider", workspace)
    assert [item["id"] for item in result["items"]] == ["meeting-ui4"]
    assert result["items"][0]["block_id"] == "meeting-note"
    assert "PRIVATE TRANSCRIPT" not in json.dumps(result)
    assert authority.list_meetings("outsider", workspace, query="다른 제목")["items"] == []
    from code_assist_v1 import routes_notion_workspace
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes_notion_workspace, "_uid", lambda: "outsider")
    monkeypatch.setattr(routes_notion_workspace, "_collaborative_runtime_enabled", lambda: True)
    app = Flask("meetings-canonical")
    routes_notion_workspace.register_notion_workspace_routes(app)
    response = app.test_client().get(f"/api/notion/workspaces/{workspace}/meetings")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert [item["id"] for item in response.json["items"]] == ["meeting-ui4"]
    page = authority.read_document("owner", "meeting-ui4")
    authority.revoke_page_grant(
        "owner", "meeting-ui4", "subject", "outsider",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
        operation_id="ui4-meeting-revoke-0001",
    )
    assert authority.list_meetings("outsider", workspace)["items"] == []
    page = authority.read_document("owner", "meeting-ui4")
    authority.save_document(
        "owner", "meeting-ui4", title=page["title"],
        blocks=[{"id": "ordinary", "type": "paragraph", "content": "회의라는 단어만 있음", "props": {}}],
        expected_revision=page["revision"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="ui4-meeting-remove-note-0001",
    )
    assert authority.list_meetings("owner", workspace)["items"] == []
