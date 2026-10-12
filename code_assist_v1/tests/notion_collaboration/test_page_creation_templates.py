"""UI-CREATE-CHOOSER: complete templates commit with their page and receipt."""
import json
import pytest
from code_assist_v1.notion_collaboration import page_templates, page_creation
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, OperationConflict, _canonical_json, _sha256
from code_assist_v1.tests.notion_collaboration.test_native_team_creation import (
    environment,file_environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    teams,create,version,dump,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for,body


@pytest.mark.parametrize("template",["database","task_tracker","meeting_notes","ai_notes"])
def test_ui_creation_template_complete_snapshot_and_exact_retry(environment,template):
    store,ws=environment;a,_=teams(store,ws);base=version(store,ws)
    result=create(store,ws,a,template=template,actor="content-editor",base=base)
    doc=result["page"]["id"]; current=store.iam.read_document("schema-editor",doc)
    if template=="ai_notes":
        assert [block["type"] for block in current["blocks"]]==["ai_notes"]
    elif template=="meeting_notes":
        assert len(current["blocks"])==8
        assert current["blocks"][0]["content"]=="회의 안건"
    else:
        databases=store.iam.read_databases("schema-editor",doc)
        assert len(databases)==1
        database=databases[0]
        assert database["id"]==current["blocks"][0]["props"]["database_id"]
        assert database["rows"]==[] and database["properties"][0]["type"]=="title"
        assert len(database["properties"])==(3 if template=="task_tracker" else 1)
        assert len(database["views"])==(2 if template=="task_tracker" else 1)
        if template=="task_tracker":
            assert database["views"][1]["config"]["board_group_property"]==database["properties"][1]["id"]
        with store.iam._reader() as con:
            row=store.iam._document(con,doc);snapshot=store.iam._editing_snapshot(con,row)
            assert row["content_sha256"]==_sha256(_canonical_json(snapshot))
            revision=con.execute("SELECT * FROM workspace_collaboration_revisions WHERE document_id=?",(doc,)).fetchone()
            assert json.loads(revision["snapshot_json"])==snapshot
            assert revision["content_sha256"]==row["content_sha256"]
    before=dump(store)
    assert create(store,ws,a,template=template,actor="content-editor",base=base)["page"]["id"]==doc
    with pytest.raises(OperationConflict):create(store,ws,a,template="page",actor="content-editor",base=base)
    assert dump(store)==before


def test_ui_creation_template_database_rollback_and_parent_link(environment):
    store,ws=environment;a,_=teams(store,ws);before=dump(store)
    with pytest.raises(InjectedFailure):create(store,ws,a,template="task_tracker",fault="after_outbox")
    assert dump(store)==before
    root=create(store,ws,a)["page"]["id"]
    result=create(store,ws,parent=root,template="database",operation="nested-db")
    assert result["parent_link_saved"] is True
    assert len(store.iam.read_databases("content-editor",result["page"]["id"]))==1


def test_ui_creation_template_http_validation_and_default_compatibility(environment,monkeypatch):
    store,ws=environment;client=client_for(store,monkeypatch);path=f"/api/notion/workspaces/{ws}/pages"
    request={**body(store,ws),"template":"database"}
    created=client.post(path,json=request)
    assert created.status_code==201,created.json
    assert client.post(path,json=request).status_code==200
    before=dump(store)
    for invalid in ("unknown","",None,{},7):
        response=client.post(path,json={**body(store,ws,operation="invalid-template"),"template":invalid})
        assert response.status_code==400,response.json
    assert dump(store)==before
    default=client.post(path,json=body(store,ws,operation="compatible-empty-page"))
    assert default.status_code==201,default.json
    assert store.iam.read_document("owner",default.json["page"]["id"])["blocks"][0]["type"]=="paragraph"
