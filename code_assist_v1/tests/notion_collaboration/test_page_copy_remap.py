"""UI-PAGE-COPY typed reference fidelity; no catalog writes or grant creation."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration.page_copy_source import CopySourceDocument, PreparedPageCopy
from code_assist_v1.notion_collaboration.page_copy_remap import CopyShapeUnavailable, remap_page_copy
from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority, _property_config
from code_assist_v1.tests.notion_collaboration.test_page_archive import environment, guest_environment, image_environment


def block(id, type="paragraph", content="", **props):
    return {"id": id, "type": type, "content": content, "props": props}


def property(id, type="text", **config):
    return {"id": id, "name": id + " name", "type": type, "position": 0, "config": config, "version": 7}


def fixture():
    file = {"id": "file-source", "original_name": "page-a.png", "mime_type": "image/png", "size_bytes": 12,
        "content_sha256": "sha256:" + "a" * 64, "blob_relative_path": "blobs/aa/aa/" + "a" * 64,
        "upload_session_id": "original-upload", "uploader_subject_id": "owner", "state": "active"}
    database_a = {"id": "db-a", "title": "db-a", "version": 9,
        "properties": [property("amount", "number"), property("words"), property("file-col", "files"),
            property("person-col", "person"), property("rel", "relation", target_database_id="db-b"),
            property("external-rel", "relation", target_database_id="external-db"),
            property("roll", "rollup", relation_property_id="rel", target_property_id="amount", aggregation="sum"),
            property("calc", "formula", expression='prop("amount") + (0 if "amount" == "amount" else 1)')],
        "rows": [{"id": "row-a", "version": 9, "position": 0, "cells": {"amount": 4, "words": "page-b",
            "file-col": [{"file_id": "file-source", "url": "/api/notion/collaboration/files/file-source", "name": "file-source"}],
            "person-col": ["page-b"], "rel": ["row-b"], "external-rel": ["row-external"]}, "cell_versions": {"amount": 9}}],
        "views": [{"id": "view-a", "state": "active", "version": 3, "name": "view-a", "type": "table", "config": {
            "group_property": "words", "property_visibility": {"amount": False},
            "filters": [{"property": "rel", "operator": "contains", "value": ["row-b"]},
                {"property": "words", "operator": "equals", "value": "amount"}],
            "sorts": [{"property": "amount", "direction": "desc"}]}},
            {"id": "old-view", "state": "archived", "name": "archived view"}],
        "templates": [{"id": "template-a", "state": "active", "name": "Template", "version": 4,
            "body": [block("paragraph", content="amount", rich_text_html='<a href="/page/page-b">amount</a>')],
            "default_values": {"rel": ["row-b"], "words": "row-b"}}]}
    # Property IDs are scoped during remapping, including cross-DB rollups.
    database_b = {"id": "db-b", "title": "Second database", "version": 2,
        "properties": [property("amount", "number")],
        "rows": [{"id": "row-b", "version": 2, "position": 0, "cells": {"amount": 8}, "cell_versions": {"amount": 2}}],
        "views": [], "templates": []}
    markup = '<strong>page-b &amp; amount</strong> <a title=\'href="/page/page-a"\' href="/page/page-b?label=page-a#block-target">page-b</a>'
    a = {"title": "page-a", "icon": "/api/notion/collaboration/files/file-source", "cover": "linear-gradient(red, blue)",
        "settings": {"locked": True, "full_width": True},
        "blocks": [block("paragraph", content="page-b /page/page-b", rich_text_html=markup, custom={"page_id": "page-b"}),
            block("child", "child_page", page_id="page-b"), block("outside", "link_to_page", page_id="external-page"),
            block("database", "table", database_id="db-a", properties=[{"id": "stale"}], rows=[{"stale": "stale value"}],
                active_view_id="view-a", group_property="words"),
            block("tabs", "tabs", tabs=[{"id": "tab-a", "name": "tab-a"}], active_tab_id="tab-a"),
            block("layout-1", layout_group="columns", layout_column=0, tab_group="tabs", tab_id="tab-a"),
            block("layout-2", "toggle", children=[block("nested", content="paragraph")], layout_group="columns", layout_column=1)],
        "databases": [database_a], "files": [{"id": "file-source", "name": "page-a.png", "sha256": file["content_sha256"], "version": 4}]}
    b = {"title": "Child", "icon": "", "cover": "", "settings": {}, "blocks": [block("target", content="page-a"),
        block("db-b", "table", database_id="db-b")], "databases": [database_b], "files": []}
    documents = tuple(CopySourceDocument(id, parent, position, (1, 2, 3), json.dumps(descriptor), json.dumps(files))
        for id, parent, position, descriptor, files in [("page-a", "boundary-parent", 3, a, [file]), ("page-b", "page-a", 0, b, [])])
    return PreparedPageCopy("owner", "workspace", "page-a", (7, 3), documents, "boundary-parent", (4,))


def test_ui_copy_remap_subtree_identity_determinism_and_literal_preservation():
    source = fixture()
    first = remap_page_copy(source, "copy-operation")
    assert first == remap_page_copy(source, "copy-operation")
    other = remap_page_copy(source, "different-operation")
    assert set(item.document_id for item in first.documents).isdisjoint(item.document_id for item in other.documents)
    root, child = first.documents
    assert root.parent_id == "boundary-parent" and root.position == 3
    assert child.parent_id == root.document_id
    original = source.documents[0].descriptor()
    copied = root.descriptor()
    assert copied["title"] == original["title"] and copied["settings"] == original["settings"]
    assert copied["blocks"][0]["content"] == "page-b /page/page-b"
    assert copied["blocks"][0]["props"]["custom"] == {"page_id": "page-b"}
    assert copied["blocks"][1]["props"]["page_id"] == child.document_id
    assert copied["blocks"][2]["props"]["page_id"] == "external-page"
    copied["blocks"].clear()
    assert root.descriptor()["blocks"] and source == fixture()


def test_ui_copy_remap_html_links_preserve_display_text_attributes_and_external_urls():
    plan = remap_page_copy(fixture(), "copy-links")
    root, child = plan.documents
    markup = root.descriptor()["blocks"][0]["props"]["rich_text_html"]
    assert '<strong>page-b &amp; amount</strong>' in markup
    assert 'title=\'href="/page/page-a"\'' in markup
    assert f'href="/page/{child.document_id}?label=page-a#block-{child.descriptor()["blocks"][0]["id"]}"' in markup
    assert markup.endswith('>page-b</a>')


def test_ui_copy_remap_database_relations_rollups_formulas_views_templates_and_caches():
    plan = remap_page_copy(fixture(), "copy-databases")
    root, child = [item.descriptor() for item in plan.documents]
    database, target = root["databases"][0], child["databases"][0]
    props = {item["name"]: item for item in database["properties"]}
    assert props["rel name"]["config"]["target_database_id"] == target["id"]
    assert props["external-rel name"]["config"]["target_database_id"] == "external-db"
    roll = props["roll name"]["config"]
    assert roll["relation_property_id"] == props["rel name"]["id"]
    assert roll["target_property_id"] == target["properties"][0]["id"]
    assert target["properties"][0]["id"] != props["amount name"]["id"]
    cells = database["rows"][0]["cells"]
    assert cells[props["rel name"]["id"]] == [target["rows"][0]["id"]]
    assert cells[props["external-rel name"]["id"]] == ["row-external"]
    assert cells[props["person-col name"]["id"]] == ["page-b"]
    assert cells[props["words name"]["id"]] == "page-b"
    formula = props["calc name"]["config"]
    assert formula["expression"] == 'prop("amount") + (0 if "amount" == "amount" else 1)'
    assert formula["copy_property_ids"]["amount"] == props["amount name"]["id"]
    view = database["views"][0]
    assert len(database["views"]) == 1
    assert view["config"]["filters"][0]["value"] == [target["rows"][0]["id"]]
    assert view["config"]["filters"][1]["value"] == "amount"
    assert view["config"]["property_visibility"] == {props["amount name"]["id"]: False}
    template = database["templates"][0]
    assert template["default_values"][props["rel name"]["id"]] == [target["rows"][0]["id"]]
    assert template["default_values"][props["words name"]["id"]] == "row-b"
    assert template["body"][0]["id"] != root["blocks"][0]["id"]
    block_props = root["blocks"][3]["props"]
    assert block_props["database_id"] == database["id"] and block_props["active_view_id"] == view["id"]
    assert "rows" not in block_props and "properties" not in block_props
    assert set(database["rows"][0]["cell_versions"].values()) == {1}


def test_ui_copy_remap_nested_filter_group_maps_property_and_relation_ids():
    source = fixture()
    first, second = source.documents
    descriptor = first.descriptor()
    descriptor["databases"][0]["views"][0]["config"]["filter_group"] = {
        "logic": "or", "children": [
            {"property": "words", "operator": "equals", "value": "amount"},
            {"logic": "and", "children": [
                {"property": "rel", "operator": "contains", "value": ["row-b"]},
            ]},
        ],
    }
    source = replace(source, documents=(replace(first, descriptor_json=json.dumps(descriptor)), second))
    root, child = [item.descriptor() for item in remap_page_copy(source, "copy-filter-group").documents]
    copied = root["databases"][0]
    ids = {item["name"]: item["id"] for item in copied["properties"]}
    group = copied["views"][0]["config"]["filter_group"]
    assert group["children"][0] == {"property": ids["words name"], "operator": "equals", "value": "amount"}
    assert group["children"][1]["children"][0] == {
        "property": ids["rel name"], "operator": "contains", "value": [child["databases"][0]["rows"][0]["id"]]}


def test_ui_r5_copying_both_paired_databases_remaps_inverse_ids_and_rows():
    source = fixture()
    first, second = source.documents
    first_descriptor, second_descriptor = first.descriptor(), second.descriptor()
    first_relation = next(item for item in first_descriptor["databases"][0]["properties"]
        if item["id"] == "rel")
    first_relation["config"]["reciprocal_property_id"] = "back"
    second_database = second_descriptor["databases"][0]
    second_database["properties"].append(property("back", "relation",
        target_database_id="db-a", reciprocal_property_id="rel"))
    second_database["rows"][0]["cells"]["back"] = ["row-a"]
    source = replace(source, documents=(
        replace(first, descriptor_json=json.dumps(first_descriptor)),
        replace(second, descriptor_json=json.dumps(second_descriptor))))
    root, child = [item.descriptor() for item in
        remap_page_copy(source, "copy-paired-databases").documents]
    copied_a, copied_b = root["databases"][0], child["databases"][0]
    related = next(item for item in copied_a["properties"] if item["name"] == "rel name")
    back = next(item for item in copied_b["properties"] if item["name"] == "back name")
    assert related["config"] == {"target_database_id": copied_b["id"],
        "reciprocal_property_id": back["id"]}
    assert back["config"] == {"target_database_id": copied_a["id"],
        "reciprocal_property_id": related["id"]}
    assert copied_a["rows"][0]["cells"][related["id"]] == [copied_b["rows"][0]["id"]]
    assert copied_b["rows"][0]["cells"][back["id"]] == [copied_a["rows"][0]["id"]]

    broken = second_descriptor["databases"][0]["properties"][-1]
    broken["config"]["reciprocal_property_id"] = "different"
    malformed = replace(source, documents=(source.documents[0],
        replace(second, descriptor_json=json.dumps(second_descriptor))))
    with pytest.raises(CopyShapeUnavailable, match="paired relation properties disagree"):
        remap_page_copy(malformed, "copy-broken-pair")


def test_ui_copy_remap_files_are_independent_records_with_honest_provenance():
    plan = remap_page_copy(fixture(), "copy-files")
    root = plan.documents[0]
    file = json.loads(root.files_json)[0]
    descriptor = root.descriptor()
    assert file["source_file_id"] == "file-source" and file["source_document_id"] == "page-a"
    assert file["id"] != "file-source" and "upload_session_id" not in file
    assert descriptor["files"][0]["id"] == file["id"]
    assert descriptor["icon"] == "/api/notion/collaboration/files/" + file["id"]
    database = descriptor["databases"][0]
    property_id = next(p["id"] for p in database["properties"] if p["type"] == "files")
    value = database["rows"][0]["cells"][property_id][0]
    assert value["file_id"] == file["id"] and value["url"] == descriptor["icon"]
    assert value["name"] == "file-source"
    assert file["content_sha256"] == descriptor["files"][0]["sha256"]


def test_ui_copy_remap_nested_columns_and_tabs_stay_connected():
    root = remap_page_copy(fixture(), "copy-layout").documents[0].descriptor()
    tabs, left, right = root["blocks"][4:]
    assert left["props"]["layout_group"] == right["props"]["layout_group"] != "columns"
    assert left["props"]["layout_column"] == 0 and right["props"]["layout_column"] == 1
    assert left["props"]["tab_group"] == tabs["id"]
    assert left["props"]["tab_id"] == tabs["props"]["active_tab_id"] == tabs["props"]["tabs"][0]["id"] != "tab-a"
    assert right["props"]["children"][0]["id"] != "nested"
    assert right["props"]["children"][0]["content"] == "paragraph"


def replaced_source(blocks):
    source = fixture()
    original = source.documents[0]
    descriptor = original.descriptor()
    descriptor["blocks"] = blocks
    replacement = CopySourceDocument(original.document_id, original.parent_id, original.position, original.stamp,
        json.dumps(descriptor), original.files_json)
    return PreparedPageCopy(source.actor, source.workspace_id, source.source_page_id, source.workspace_stamp,
        (replacement, source.documents[1]), source.parent_id, source.parent_stamp)


def test_ui_copy_remap_trusted_origin_fragments_and_untouched_external_hosts():
    source = replaced_source([block("link", rich_text_html='\n<a href="https://app.test/page/page-b#block-target">one</a> '
        '<a href="https://external.test/page/page-b#block-target">two</a> '
        '<a href="#block-link">local</a> <a href="https://app.test#block-link">root</a> '
        '<a href="/api/notion/collaboration/files/file-source#block-link">file</a>')])
    result = remap_page_copy(source, "origin-links", local_origin="https://app.test")
    root, child = result.documents
    html = root.descriptor()["blocks"][0]["props"]["rich_text_html"]
    assert f'https://app.test/page/{child.document_id}' in html
    assert 'https://external.test/page/page-b#block-target' in html
    assert f'href="#block-{root.descriptor()["blocks"][0]["id"]}"' in html
    assert 'href="https://app.test#block-link"' in html
    assert '#block-link">file</a>' in html
    without_origin = remap_page_copy(source, "origin-links").documents[0].descriptor()["blocks"][0]["props"]["rich_text_html"]
    assert 'https://app.test/page/page-b#block-target' in without_origin
    with pytest.raises(ValueError, match="origin"):
        remap_page_copy(source, "origin-links", local_origin="https://app.test/path")


@pytest.mark.parametrize("blocks,reason", [([block("x", "synced_block", sync_id="external")], "synced"),
    ([block("x", "table", database_id="external")], "linked"),
    ([block("x"), block("x")], "duplicate")])
def test_ui_copy_remap_unresolved_shapes_cannot_silently_produce_partial_plans(blocks, reason):
    source = replaced_source(blocks)
    before = deepcopy(source)
    with pytest.raises(CopyShapeUnavailable, match=reason):
        remap_page_copy(source, "unavailable")
    assert source == before


def test_ui_copy_remap_current_prepared_yjs_and_canonical_database(environment):
    from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, prepare
    from code_assist_v1.tests.notion_collaboration.test_page_archive import dump
    store, workspace = environment
    current_update(store, workspace)
    source = prepare(store, workspace)
    before = dump(store)
    result = remap_page_copy(source, "copy-current-canonical")
    current = result.documents[0].descriptor()
    assert current["title"] == "최신 협업 복제 제목"
    assert current["blocks"][0]["content"].startswith("<strong>최신 본문</strong>")
    original = source.documents[0].descriptor()
    assert current["blocks"][0]["content"] == original["blocks"][0]["content"]
    assert current["databases"][0]["id"] != original["databases"][0]["id"]
    assert len(current["databases"][0]["rows"]) == len(original["databases"][0]["rows"])
    assert dump(store) == before
    assert result.source is source


def test_ui_copy_remap_dynamic_formula_and_copy_of_copy_resolve_current_owned_cells():
    source = fixture()
    original = source.documents[0]
    descriptor = original.descriptor()
    database = descriptor["databases"][0]
    database["properties"] = [property("amount", "number"), property("selector"),
        property("calc", "formula", expression='prop(prop("selector")) + prop("am" + "ount")')]
    database["rows"] = [{"id": "row-a", "position": 0, "version": 8,
        "cells": {"amount": 4, "selector": "amount"}, "cell_versions": {"amount": 8, "selector": 8}}]
    database["views"], database["templates"] = [], []
    changed = CopySourceDocument(original.document_id, original.parent_id, original.position, original.stamp,
        json.dumps(descriptor), original.files_json)
    source = PreparedPageCopy(source.actor, source.workspace_id, source.source_page_id, source.workspace_stamp,
        (changed, source.documents[1]), source.parent_id, source.parent_stamp)
    first = remap_page_copy(source, "dynamic-formula")
    value = first.documents[0].descriptor()["databases"][0]
    computed = CollaborativeDatabaseAuthority._materialize_rows(None, "owner", value)[0]["computed"]
    formula = next(p for p in value["properties"] if p["type"] == "formula")
    assert computed[formula["id"]] == 8
    assert formula["config"]["expression"] == database["properties"][-1]["config"]["expression"]
    second_source = PreparedPageCopy(source.actor, source.workspace_id, first.documents[0].document_id, source.workspace_stamp,
        tuple(CopySourceDocument(item.document_id, item.parent_id, item.position, (1,), item.descriptor_json, item.files_json)
            for item in first.documents), source.parent_id, source.parent_stamp)
    second = remap_page_copy(second_source, "dynamic-second-copy")
    value = second.documents[0].descriptor()["databases"][0]
    formula = next(p for p in value["properties"] if p["type"] == "formula")
    amount = next(p for p in value["properties"] if p["type"] == "number")
    assert formula["config"]["copy_property_ids"]["amount"] == amount["id"]
    assert CollaborativeDatabaseAuthority._materialize_rows(None, "owner", value)[0]["computed"][formula["id"]] == 8
    assert _property_config("formula", formula["config"]) == formula["config"]
    with pytest.raises(ValueError):
        _property_config("formula", {"expression": 'prop("amount")', "copy_property_ids": {"amount": {"bad": "shape"}}})


def test_ui_copy_remap_bounds_expanded_formula_metadata_before_returning_a_plan(monkeypatch):
    from code_assist_v1.notion_collaboration import page_copy_remap
    source = fixture()
    before = deepcopy(source)
    monkeypatch.setattr(page_copy_remap, "MAX_PLAN_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        remap_page_copy(source, "budget-limited")
    assert source == before
