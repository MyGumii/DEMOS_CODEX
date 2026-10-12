"""UI-SEARCH-PREVIEW: current document structure, permission fences and no writes."""
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration import search_preview
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.routes_notion_search import register_notion_search_routes
from code_assist_v1.tests.notion_collaboration.test_visible_search import _environment, frames
from code_assist_v1.tests.notion_collaboration.test_title_projection import append, compact, yjs
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant, _rich_pages, IDENTITIES
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema


def _dump(catalog):
    with sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True) as con:
        return tuple(con.iterdump())


def _revoke(authority, document="page-e1"):
    page = authority.read_document("owner", document)
    authority.revoke_page_grant("owner", document, "subject", "outsider",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
        operation_id=f"op-preview-revoke-{document}-0001")


def _save(authority, document, blocks):
    page = authority.read_document("owner", document)
    authority.save_document("owner", document, title=page["title"], blocks=blocks,
        expected_revision=page["revision"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id=f"op-preview-save-{document}-0001")


def test_ui_search_preview_current_body_compaction_empty_and_no_writes(tmp_path, frames):
    a, transport, _, catalog, ws = _environment(tmp_path)
    grant = append(a, transport, ws, [frames["base"], frames["current"]])
    before = _dump(catalog)
    result = search_preview.read_search_preview(a, "owner", ws, "page-e1")
    assert result["page"]["title"] == "Visible projection"
    assert result["page"]["read_only"] is True
    assert "LatestBodyToken" in str(result["blocks"]) and "LatestTableToken" in str(result["blocks"])
    assert "OldBodyToken" not in str(result["blocks"])
    assert result["databases"] == []  # Deleted database blocks do not resurrect their stored rows.
    assert _dump(catalog) == before
    compact(transport, grant, {"snapshot": frames["current"], "stateVector": frames["currentVector"]})
    assert search_preview.read_search_preview(a, "owner", ws, "page-e1")["blocks"] == result["blocks"]
    append(a, transport, ws, [frames["empty"]])
    assert search_preview.read_search_preview(a, "owner", ws, "page-e1")["blocks"] == []


def test_ui_search_preview_retries_concurrent_body_edit(tmp_path, frames, monkeypatch):
    a, transport, _, _, ws = _environment(tmp_path)
    append(a, transport, ws, [frames["base"]])
    original = search_preview.materialize
    calls = []
    def during_decode(documents):
        result = original(documents)
        if not calls:
            calls.append(True)
            append(a, transport, ws, [frames["current"]])
        return result
    monkeypatch.setattr(search_preview, "materialize", during_decode)
    result = search_preview.read_search_preview(a, "owner", ws, "page-e1")
    assert "LatestBodyToken" in str(result["blocks"]) and "OldBodyToken" not in str(result["blocks"])


def test_ui_search_preview_reauthorizes_after_decode(tmp_path, frames, monkeypatch):
    a, transport, _, _, ws = _environment(tmp_path)
    _grant(a, "page-e1", "outsider", "op-preview-grant-read-0001")
    append(a, transport, ws, [frames["current"]])
    original = search_preview.materialize
    def during_decode(documents):
        result = original(documents)
        _revoke(a)
        return result
    monkeypatch.setattr(search_preview, "materialize", during_decode)
    with pytest.raises(NotFoundOrDenied):
        search_preview.read_search_preview(a, "outsider", ws, "page-e1")


def test_ui_search_preview_current_references_and_revocation_race(tmp_path, yjs, monkeypatch):
    a, transport, _, _, ws = _environment(tmp_path)
    _rich_pages(a)
    _save(a, "page-e1", [{"id":"link-block", "type":"child_page", "content":"Old target title",
        "props":{"page_id":"page-search-target"}}])
    append(a, transport, ws, [yjs["base"], yjs["rename"]], document="page-search-target")
    assert search_preview.read_search_preview(a,"outsider",ws,"page-e1")["references"]["page-search-target"]["title"] == "새 협업 제목"
    original = search_preview.read_current_titles
    calls = []
    def during_titles(*args):
        result = original(*args)
        if not calls:
            calls.append(True); _revoke(a, "page-search-target")
        return result
    monkeypatch.setattr(search_preview, "read_current_titles", during_titles)
    result = search_preview.read_search_preview(a,"outsider",ws,"page-e1")
    assert result["references"] == {}
    assert result["blocks"][0]["props"]["page_id"] == "page-search-target"


def test_ui_search_preview_database_computed_values_and_relation_labels(tmp_path):
    a, _, _, catalog, ws = _environment(tmp_path)
    _save(a,"page-e1",a.read_document("owner","page-e1")["blocks"] + [
        {"id":"preview-db","type":"table","content":"Tasks","props":{"database_id":"database-e1"}}])
    for ident, name, typ, config in [
        ("preview-title","Name","title",{}),
        ("preview-formula","Computed","formula",{"expression":"42 * 2"}),
        ("preview-relation","Related","relation",{"target_database_id":"database-e1"}),
    ]:
        _schema(a,"database.property.create",ident,{"name":name,"type":typ,"config":config},f"op-{ident}-create-0001")
    for prop,value in [("preview-title","Current relation title"),("preview-relation",["row-e1"])]:
        page=a.read_document("owner","page-e1")
        a.execute_typed_command("owner","page-e1",command_kind="database.cell.set",target_id=f"row-e1:{prop}",
            payload={"row_id":"row-e1","property_id":prop,"value":value},
            expected_document_sequence=page["document_sequence"],expected_workspace_epoch=page["workspace_epoch"],
            expected_document_epoch=page["document_epoch"],expected_target_version=0,operation_id=f"op-{prop}-cell-0001")
    before=_dump(catalog)
    db=search_preview.read_search_preview(a,"owner",ws,"page-e1")["databases"][0]
    assert db["rows"][0]["computed"]["preview-formula"] == 84
    assert db["relation_labels"]["preview-relation"]["row-e1"] == "Current relation title"
    assert _dump(catalog) == before


def test_ui_search_preview_title_only_updates_keep_structural_body(tmp_path, yjs):
    a, transport, _, _, ws = _environment(tmp_path)
    body=search_preview.read_search_preview(a,"owner",ws,"page-e1")["blocks"]
    append(a, transport, ws, [yjs["base"],yjs["rename"]])
    result=search_preview.read_search_preview(a,"owner",ws,"page-e1")
    assert result["page"]["title"] == "새 협업 제목" and result["blocks"] == body


def test_ui_search_preview_http_contract_scope_and_no_store(tmp_path, monkeypatch):
    a, _, root, catalog, ws = _environment(tmp_path)
    store=CollaborativeWorkspaceStore(catalog,user_data_root=root,identity_directory=InMemoryIdentityDirectory(IDENTITIES))
    actor={"id":"owner"}
    monkeypatch.setattr(routes_notion_workspace,"_store",lambda:store)
    monkeypatch.setattr(routes_notion_workspace,"_uid",lambda:actor["id"])
    monkeypatch.setattr(routes_notion_workspace,"_collaborative_runtime_enabled",lambda:True)
    app=Flask("search-preview-http"); app.config.update(TESTING=True,SECRET_KEY="test-preview")
    register_notion_search_routes(app); client=app.test_client()
    path=f"/api/notion/workspaces/{ws}/pages/page-e1/search-preview"
    response=client.get(path)
    assert response.status_code == 200 and response.get_json()["contract"] == search_preview.CONTRACT
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get(path.replace(ws,"wrong-workspace")).status_code == 404
    actor["id"]="outsider"
    assert client.get(path).status_code == 404
    _grant(a,"page-e1","outsider","op-preview-http-grant-0001")
    assert client.get(path).get_json()["page"]["role"] == "view"
    _revoke(a)
    assert client.get(path).status_code == 404
