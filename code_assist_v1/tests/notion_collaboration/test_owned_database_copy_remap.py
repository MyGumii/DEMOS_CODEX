"""Pure preparation for a same-page owned database copy."""

from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration.page_copy_remap import (
    CopyShapeUnavailable,
    remap_owned_database_copy,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import fixture


def one_page_source():
    source = fixture()
    return replace(source, documents=(source.documents[0],))


def test_owned_database_copy_remaps_content_without_reparenting_or_mutating_source():
    source = one_page_source()
    before = source.documents[0].descriptor()
    first = remap_owned_database_copy(source, "db-a", "copy-owned-db")
    assert first == remap_owned_database_copy(source, "db-a", "copy-owned-db")
    second = remap_owned_database_copy(source, "db-a", "copy-owned-db-again")
    block, database = first.block(), first.database()
    assert first.source_document_id == "page-a"
    assert block["id"] != "database" and database["id"] != "db-a"
    assert database["id"] != second.database()["id"]
    assert block["props"]["database_id"] == database["id"]
    assert block["props"]["active_view_id"] == database["views"][0]["id"]
    assert "rows" not in block["props"] and "properties" not in block["props"]
    props = {item["name"]: item for item in database["properties"]}
    assert props["rel name"]["config"]["target_database_id"] == "db-b"
    assert props["calc name"]["config"]["copy_property_ids"]["amount"] == props["amount name"]["id"]
    assert database["rows"][0]["id"] != "row-a"
    assert database["rows"][0]["cells"][props["words name"]["id"]] == "page-b"
    assert database["views"][0]["config"]["property_visibility"] == {props["amount name"]["id"]: False}
    assert source.documents[0].descriptor() == before


def test_owned_database_copy_remaps_file_provenance_and_same_page_links():
    source = one_page_source()
    document = source.documents[0]
    descriptor = document.descriptor()
    descriptor["databases"][0]["templates"][0]["body"][0]["props"]["rich_text_html"] = (
        '<a href="/page/page-a#block-database">same page</a>'
    )
    source = replace(source, documents=(replace(document, descriptor_json=json.dumps(descriptor)),))
    plan = remap_owned_database_copy(source, "db-a", "copy-owned-db-links", local_origin="https://app.test")
    database = plan.database()
    file = json.loads(plan.files_json)[0]
    assert file["id"] != "file-source" and file["source_file_id"] == "file-source"
    assert file["source_document_id"] == "page-a" and "upload_session_id" not in file
    file_property = next(item for item in database["properties"] if item["type"] == "files")
    assert database["rows"][0]["cells"][file_property["id"]][0]["file_id"] == file["id"]
    assert database["templates"][0]["body"][0]["props"]["rich_text_html"] == (
        f'<a href="/page/page-a#block-{plan.block()["id"]}">same page</a>'
    )


def test_owned_database_copy_without_content_keeps_schema_but_clears_rows_and_own_relation_targets():
    source = one_page_source()
    document = source.documents[0]
    descriptor = document.descriptor()
    database = descriptor["databases"][0]
    database["properties"].append({"id": "self-rel", "name": "Self", "type": "relation",
        "position": len(database["properties"]), "config": {"target_database_id": "db-a"}, "version": 1})
    database["rows"][0]["cells"]["self-rel"] = ["row-a"]
    database["templates"][0]["default_values"]["self-rel"] = ["row-a"]
    database["views"][0]["config"]["filters"].append(
        {"property": "self-rel", "operator": "contains", "value": ["row-a"]})
    source = replace(source, documents=(replace(document, descriptor_json=json.dumps(descriptor)),))
    copied = remap_owned_database_copy(source, "db-a", "copy-empty-db",
        include_content=False)
    result = copied.database()
    relation = next(item for item in result["properties"] if item["name"] == "Self")
    assert result["rows"] == [] and copied.files_json == "[]"
    assert relation["config"]["target_database_id"] == result["id"]
    assert result["templates"][0]["default_values"][relation["id"]] == []
    assert all(item["property"] != relation["id"] for item in result["views"][0]["config"]["filters"])
    assert source.documents[0].descriptor()["databases"][0]["rows"]


def test_owned_database_copy_retargets_internal_relations_to_copied_rows():
    source = one_page_source()
    document = source.documents[0]
    descriptor = document.descriptor()
    database = descriptor["databases"][0]
    database["properties"].append({"id": "self-rel", "name": "Self", "type": "relation",
        "position": len(database["properties"]), "config": {"target_database_id": "db-a"}, "version": 1})
    database["rows"][0]["cells"]["self-rel"] = ["row-a"]
    database["templates"][0]["default_values"]["self-rel"] = ["row-a"]
    source = replace(source, documents=(replace(document, descriptor_json=json.dumps(descriptor)),))
    copied = remap_owned_database_copy(source, "db-a", "copy-self-relation").database()
    relation = next(item for item in copied["properties"] if item["name"] == "Self")
    assert relation["config"]["target_database_id"] == copied["id"]
    assert copied["rows"][0]["cells"][relation["id"]] == [copied["rows"][0]["id"]]
    assert copied["templates"][0]["default_values"][relation["id"]] == [copied["rows"][0]["id"]]


def test_ui_r5_single_database_copy_detaches_external_pair_and_remaps_internal_pair():
    source = one_page_source()
    document = source.documents[0]
    descriptor = document.descriptor()
    database = descriptor["databases"][0]
    external = next(item for item in database["properties"] if item["id"] == "rel")
    external["config"]["reciprocal_property_id"] = "external-back"
    for identity, inverse in (("self-a", "self-b"), ("self-b", "self-a")):
        database["properties"].append({"id": identity, "name": identity,
            "type": "relation", "position": len(database["properties"]),
            "config": {"target_database_id": "db-a",
                       "reciprocal_property_id": inverse}, "version": 1})
        database["rows"][0]["cells"][identity] = ["row-a"]
    source = replace(source, documents=(replace(document,
        descriptor_json=json.dumps(descriptor)),))
    copied = remap_owned_database_copy(source, "db-a", "copy-mixed-pair").database()
    props = {item["name"]: item for item in copied["properties"]}
    assert props["rel name"]["config"] == {"target_database_id": "db-b"}
    assert props["self-a"]["config"] == {"target_database_id": copied["id"],
        "reciprocal_property_id": props["self-b"]["id"]}
    assert props["self-b"]["config"]["reciprocal_property_id"] == props["self-a"]["id"]
    row = copied["rows"][0]
    assert row["cells"][props["self-a"]["id"]] == [row["id"]]
    assert row["cells"][props["self-b"]["id"]] == [row["id"]]


def test_owned_database_copy_only_carries_referenced_files_and_rejects_missing_ones():
    source = one_page_source()
    document = source.documents[0]
    descriptor = document.descriptor()
    unused = {**json.loads(document.files_json)[0], "id": "unused-file"}
    descriptor["files"].append({"id": "unused-file", "name": "unused.png", "sha256": unused["content_sha256"], "version": 1})
    descriptor["blocks"][3]["props"]["rows"] = [{"file_id": "stale-cache-file"}]
    source = replace(source, documents=(replace(document, descriptor_json=json.dumps(descriptor),
        files_json=json.dumps([*json.loads(document.files_json), unused])),))
    copied = remap_owned_database_copy(source, "db-a", "copy-only-used-file")
    assert [item["source_file_id"] for item in json.loads(copied.files_json)] == ["file-source"]
    descriptor["databases"][0]["rows"][0]["cells"]["file-col"][0]["file_id"] = "missing-file"
    missing = replace(source, documents=(replace(source.documents[0], descriptor_json=json.dumps(descriptor)),))
    with pytest.raises(CopyShapeUnavailable, match="attachment"):
        remap_owned_database_copy(missing, "db-a", "copy-missing-file")


def test_owned_database_copy_rejects_unowned_or_child_page_shapes():
    source = one_page_source()
    with pytest.raises(CopyShapeUnavailable):
        remap_owned_database_copy(source, "db-b", "copy-missing-db")
    document = source.documents[0]
    descriptor = document.descriptor()
    descriptor["databases"][0]["rows"][0]["body"] = {
        "blocks": [{"id": "child-link", "type": "child_page", "content": "Child", "props": {"page_id": "page-b"}}]
    }
    source = replace(source, documents=(replace(document, descriptor_json=json.dumps(descriptor)),))
    with pytest.raises(CopyShapeUnavailable, match="child-page"):
        remap_owned_database_copy(source, "db-a", "copy-child-db")
