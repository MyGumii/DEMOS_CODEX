"""UI-MOVE-002: navigation follows current canonical access and hierarchy."""
from types import SimpleNamespace

from code_assist_v1 import notion_workspace_pages
from code_assist_v1.notion_collaboration.page_move import move_page
from code_assist_v1.tests.notion_collaboration.test_page_move import environment, request


def test_ui_move_002_inherited_pages_are_listed_without_legacy_grants(tmp_path, monkeypatch):
    authority, _, _, _, workspace = environment(tmp_path)
    for doc, subject in (("page-e1","content-editor"),("page-destination","schema-editor")):
        page = authority.read_document("owner",doc)
        authority.set_page_grant("owner",doc,"subject",subject,role="view",inherit_to_descendants=True,
            expected_workspace_epoch=page["workspace_epoch"],expected_document_epoch=page["document_epoch"],
            operation_id=f"op-navigation-grant-{subject}")
    bindings = [{"source_owner_subject_id":"owner","source_page_id":doc,"placement":"private","version":1}
                for doc in ("page-e1","page-child","page-grandchild","page-destination")]
    store = SimpleNamespace(iam=authority,get_workspace=lambda *args:{"access":{"epoch":1}},
                            list_page_source_bindings=lambda *args:bindings)
    def legacy_read(*args):
        raise AssertionError("canonical navigation must not depend on a legacy content/permission read")
    monkeypatch.setattr(notion_workspace_pages.notion_store,"list_pages_from_owner_by_ids",legacy_read)
    def listed(actor):
        return {item["id"]:item for item in notion_workspace_pages.list_workspace_pages(store,actor,workspace)["items"]}
    assert "page-child" in listed("content-editor")
    assert "page-child" not in listed("schema-editor")
    move_page(authority,"owner",workspace,"page-child",**request(authority))
    assert "page-child" not in listed("content-editor")
    current = listed("schema-editor")
    assert current["page-child"]["parent_id"] == "page-destination"
    assert current["page-child"]["iam_role"] == "view"
    assert current["page-child"]["can_edit"] is False
    assert current["page-child"]["can_move"] is False
    assert current["page-grandchild"]["parent_id"] == "page-child"
    assert listed("owner")["page-child"]["can_move"] is True
