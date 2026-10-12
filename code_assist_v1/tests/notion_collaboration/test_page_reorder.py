"""UI-MOVE-004: before/after order is durable and preserves page/link identities."""
import sqlite3

import pytest

from code_assist_v1.notion_collaboration import page_move
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.page_appearance import project_page_metadata
from code_assist_v1.tests.notion_collaboration.test_page_move import environment, request, current_body
from code_assist_v1.tests.notion_collaboration.test_page_appearance import snapshot


def relative(authority, anchor, direction="before", operation="op-ui-order-001"):
    result = request(authority, operation=operation)
    result.pop("parent_id")
    result[f"{direction}_id"] = anchor
    return result


def sibling(authority, workspace, parent="page-e1"):
    page = authority.read_document("owner", parent)
    authority.create_document("owner", workspace, "page-sibling", title="Sibling", parent_document_id=parent,
        blocks=[{"id":"sibling-body","type":"paragraph","content":"keep sibling body"}],
        expected_workspace_epoch=page["workspace_epoch"], operation_id="op-order-create-sibling")
    page = authority.read_document("owner", parent)
    authority.execute_typed_command("owner", parent, command_kind="block.insert", target_id="sibling-link",
        payload={"type":"child_page","content":"Sibling","props":{"page_id":"page-sibling"},"after_block_id":page["blocks"][-1]["id"]},
        expected_document_sequence=page["document_sequence"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="op-order-insert-link")


def test_ui_move_004_same_parent_before_after_retains_link_identity(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    sibling(authority, workspace)
    before = authority.read_document("owner", "page-child")
    req = relative(authority, "page-sibling", "after")
    moved = page_move.move_page(authority, "owner", workspace, "page-child", **req)
    body = current_body(authority, "page-e1")
    assert [block["id"] for block in body["blocks"]] == ["block-a","block-b","sibling-link","child-link-original"]
    assert body["blocks"][-1]["props"] == {"page_id":"page-child"}
    after = authority.read_document("owner", "page-child")
    assert after["blocks"] == before["blocks"]
    assert moved["changed"] is True
    assert after["workspace_epoch"] == before["workspace_epoch"]
    assert after["document_epoch"] == before["document_epoch"]
    stable = snapshot(catalog)
    assert page_move.move_page(authority,"owner",workspace,"page-child",**req) == moved
    assert snapshot(catalog) == stable
    with pytest.raises(OperationConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**{**req,"after_id":"page-e1"})
    noop = page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-sibling","after","op-ui-order-noop"))
    assert noop["changed"] is False
    page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-sibling","before","op-ui-order-back"))
    assert [block["id"] for block in current_body(authority,"page-e1")["blocks"]][-2:] == ["child-link-original","sibling-link"]
    projected = project_page_metadata(authority,"owner",workspace,[{"id":doc} for doc in ("page-e1","page-child","page-sibling")])
    positions = {item["id"]:item["position"] for item in projected}
    assert positions["page-child"] < positions["page-sibling"]


def test_ui_move_004_cross_parent_relative_move_and_root_order(tmp_path):
    authority, _, _, _, workspace = environment(tmp_path)
    sibling(authority, workspace, "page-destination")
    moved = page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-sibling"))
    assert moved["parent_id"] == "page-destination"
    destination = current_body(authority,"page-destination")["blocks"]
    assert [block["props"].get("page_id") for block in destination] == [None,"page-child","page-sibling"]
    assert [block["content"] for block in current_body(authority,"page-e1")["blocks"]] == ["A","B"]
    page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-destination","before","op-ui-order-root"))
    def roots():
        with authority._reader() as con:
            return [row[0] for row in con.execute("SELECT id FROM workspace_collaboration_documents WHERE workspace_id=? AND parent_document_id IS NULL ORDER BY position,created_at,id",(workspace,))]
    first = roots()
    assert first.index("page-child")+1 == first.index("page-destination")
    before = authority.read_document("owner","page-child")
    page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-e1","after","op-ui-order-root-after"))
    after = roots()
    assert after.index("page-e1")+1 == after.index("page-child")
    assert authority.read_document("owner","page-child")["document_epoch"] == before["document_epoch"]
    authority.create_document("owner", workspace, "page-new-root", title="New root", blocks=[],
        expected_workspace_epoch=authority.read_document("owner","page-child")["workspace_epoch"],
        operation_id="op-order-create-last-root")
    assert roots() == [*after,"page-new-root"], "creating a root must not reset the saved sibling order"


def test_ui_move_004_root_order_rolls_back_with_receipt(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req = relative(authority,"page-destination")
    before = snapshot(catalog)
    with pytest.raises(InjectedFailure):
        page_move.move_page(authority,"owner",workspace,"page-child",**req,fault="after_outbox")
    assert snapshot(catalog) == before


def test_ui_move_004_relative_anchor_is_revalidated_after_preparation(tmp_path, monkeypatch):
    authority, _, _, catalog, workspace = environment(tmp_path)
    sibling(authority,workspace)
    original=page_move._prepare_deltas
    def moved_anchor(*args):
        deltas=original(*args)
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET parent_document_id='page-destination',row_version=row_version+1 WHERE id='page-sibling'")
        return deltas
    monkeypatch.setattr(page_move,"_prepare_deltas",moved_anchor)
    with pytest.raises(DocumentSequenceConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**relative(authority,"page-sibling","after"))
    assert authority.read_document("owner","page-child")["parent_document_id"] == "page-e1"


def test_ui_move_004_destination_contract_rejects_ambiguity(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req=request(authority)
    before=snapshot(catalog)
    for updates in ({"before_id":"page-e1"},{"parent_id":False},{"parent_id":None},{"parent_id":None,"after_id":""}):
        with pytest.raises(ValueError):
            page_move.move_page(authority,"owner",workspace,"page-child",**{**req,**updates})
    assert snapshot(catalog) == before


def test_ui_move_004_http_accepts_exact_relative_destination(tmp_path, monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from code_assist_v1 import routes_notion_workspace as routes
    authority, _, _, _, workspace = environment(tmp_path)
    monkeypatch.setattr(routes,"_store",lambda:SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes,"_collaborative_runtime_enabled",lambda:True)
    monkeypatch.setattr(routes,"current_user_id",lambda:"owner")
    app=Flask(__name__);app.config.update(TESTING=True,SECRET_KEY="synthetic-reorder")
    routes.register_notion_workspace_routes(app)
    client=app.test_client();url=f"/api/notion/workspaces/{workspace}/pages/page-child/move"
    req=relative(authority,"page-destination")
    response=client.post(url,json=req)
    assert response.status_code == 200, response.json
    assert response.json["parent_id"] == ""
    assert client.post(url,json=req).json == response.json
    assert client.post(url,json={**req,"after_id":"page-e1"}).status_code == 400
