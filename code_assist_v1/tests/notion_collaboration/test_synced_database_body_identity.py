"""UI-SYNC-DB-IDENTITY: exact visible body ownership and independent copies."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, page_copy_remap as remap
from code_assist_v1.notion_collaboration.database_features import _duplicate_row_blocks
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import fixture, block


def original(identity, text="공유 내용 😀"):
    return block(identity, "synced_block", text, synced_from=None,
                 children=[block(identity + "-child", content="자식", rich_text_html="<b>자식</b>")])


def reference(identity, document, target):
    return block(identity, "synced_block", synced_from={"document_id": document, "block_id": target}, sync_id="stale-group")


def source_with_synced_bodies():
    source = fixture()
    root, child = (value.descriptor() for value in source.documents)
    database = root["databases"][0]
    database["templates"][0]["body"] = [original("template-original"),
        reference("template-peer", "page-a", "template-original"),
        reference("template-external", "outside-page", "outside-original")]
    database["rows"][0]["body"] = {"blocks": [original("row-original"),
        reference("row-template-peer", "page-a", "template-original"),
        reference("row-external", "outside-page", "outside-original")]}
    root["blocks"].append(reference("page-row-peer", "page-a", "row-original"))
    child["blocks"].append(reference("child-template-peer", "page-a", "template-original"))
    return replace(source, documents=tuple(replace(value, descriptor_json=json.dumps(body))
        for value, body in zip(source.documents, (root, child))))


def test_copy_remaps_row_template_main_and_cross_page_endpoints_without_source_mutation():
    source = source_with_synced_bodies(); before = deepcopy(source)
    result = remap.remap_page_copy(source, "synced-database-body-copy")
    assert result == remap.remap_page_copy(source, "synced-database-body-copy") and source == before
    root, child = (value.descriptor() for value in result.documents)
    database = root["databases"][0]
    template = database["templates"][0]["body"]
    row = database["rows"][0]["body"]["blocks"]
    copied_document = result.documents[0].document_id
    for instance, target in ((template[1], template[0]), (row[1], template[0]),
                             (root["blocks"][-1], row[0]), (child["blocks"][-1], template[0])):
        assert instance["props"]["synced_from"] == {"document_id": copied_document, "block_id": target["id"]}
        assert instance["content"] == "" and not instance["props"].get("children")
        assert instance["props"]["sync_id"] == synced.group_id(copied_document, target["id"])
    for original_block in (template[0], row[0]):
        assert original_block["props"]["synced_from"] is None
        assert original_block["props"]["sync_id"] == synced.group_id(copied_document, original_block["id"])
        assert original_block["props"]["children"][0]["props"]["rich_text_html"] == "<b>자식</b>"
    for instance in (template[2], row[2]):
        assert instance["props"]["synced_from"] == {"document_id": "outside-page", "block_id": "outside-original"}
        assert instance["props"]["sync_id"] == synced.group_id("outside-page", "outside-original")


def test_copy_of_copied_database_bodies_keeps_independent_originals():
    source = source_with_synced_bodies(); first = remap.remap_page_copy(source, "db-sync-first")
    second_source = replace(source, source_page_id=first.documents[0].document_id,
        documents=tuple(replace(old, document_id=new.document_id, parent_id=new.parent_id,
                                descriptor_json=new.descriptor_json, files_json=new.files_json)
                        for old, new in zip(source.documents, first.documents)))
    second = remap.remap_page_copy(second_source, "db-sync-second")
    old = first.documents[0].descriptor()["databases"][0]
    new = second.documents[0].descriptor()["databases"][0]
    old_original = old["templates"][0]["body"][0]
    new_original = new["templates"][0]["body"][0]
    assert old_original["id"] != new_original["id"]
    assert old_original["content"] == new_original["content"]
    assert new["rows"][0]["body"]["blocks"][1]["props"]["synced_from"] == {
        "document_id": second.documents[0].document_id, "block_id": new_original["id"]}


@pytest.mark.parametrize("collision", ["page", "row", "template"])
def test_copy_rejects_synced_original_identity_collisions_across_bodies(collision):
    source = source_with_synced_bodies(); root = source.documents[0].descriptor()
    database = root["databases"][0]
    if collision == "page": root["blocks"].append(block("template-original"))
    elif collision == "row": database["rows"][0]["body"]["blocks"].append(block("template-original"))
    else: database["templates"].append({"id":"another-template", "state":"active", "name":"Other",
        "body":[block("template-original")], "default_values":{}})
    bad = replace(source, documents=(replace(source.documents[0], descriptor_json=json.dumps(root)), source.documents[1]))
    with pytest.raises(remap.CopyShapeUnavailable, match="ambiguous"):
        remap.remap_page_copy(bad, "db-sync-collision")


def test_body_locator_excludes_archived_templates_and_preserves_unrelated_duplicate_plain_ids():
    descriptor = source_with_synced_bodies().documents[0].descriptor()
    database = descriptor["databases"][0]
    database["templates"].append({"id":"old-template", "state":"archived", "body":[original("row-original")]})
    database["rows"][0]["body"]["blocks"].append(block("paragraph"))
    location, body, value = synced.locate_block(descriptor, "row-original")
    assert location.payload() == {"kind":"row", "database_id":"db-a", "row_id":"row-a"}
    assert value is body[0] and value["content"] == "공유 내용 😀"
    with pytest.raises(synced.SyncedShapeUnavailable, match="ambiguous"):
        synced.locate_block(descriptor, "paragraph")
    database["templates"][-1]["body"] = [original("archived-only")]
    with pytest.raises(NotFoundOrDenied): synced.locate_block(descriptor, "archived-only")


def test_row_duplication_clones_owned_originals_and_retains_external_group_identity():
    source = [original("owned"), reference("peer", "page-a", "owned"),
              reference("external", "page-a", "outside-main-original")]
    before = deepcopy(source)
    def instantiate(operation):
        return _duplicate_row_blocks(source, actor="owner", document="page-a", database="db-a",
                                     operation=operation, source_row="template-a")
    first, second = instantiate("row-one"), instantiate("row-two")
    assert source == before and first == instantiate("row-one")
    assert first[0]["id"] != second[0]["id"]
    for result in (first, second):
        assert result[1]["props"]["synced_from"] == {"document_id":"page-a", "block_id":result[0]["id"]}
        assert result[0]["props"]["sync_id"] == result[1]["props"]["sync_id"] == synced.group_id("page-a", result[0]["id"])
        assert result[2]["props"]["synced_from"] == source[2]["props"]["synced_from"]
        assert result[2]["props"]["sync_id"] == synced.group_id("page-a", "outside-main-original")


@pytest.mark.parametrize("children", ["inline", "flat", "indent"])
def test_template_instantiation_keeps_original_endpoints_without_copying_owned_content(children):
    source = [original("owned"), reference("peer", "page-a", "owned"),
              reference("external", "outside-page", "outside-main-original"),
              block("ordinary", content="独立", url="#block-owned-child",
                    rich_text_html='<a href="#block-owned-child">source child</a>')]
    if children != "inline":
        child = source[0]["props"]["children"].pop()
        if children == "flat": source[0]["props"]["children"] = [child["id"]]
        else:
            source[0]["props"].pop("children")
            child["props"]["indent"] = 1
        source.insert(1, child)
    before = deepcopy(source)
    def instantiate(operation):
        return _duplicate_row_blocks(source, actor="owner", document="page-a", database="db-a",
            operation=operation, source_row="template-a", template_references=True)
    first, second = instantiate("row-one"), instantiate("row-two")
    assert source == before and first == instantiate("row-one")
    assert len(first) == len(second) == 4
    assert set(synced.index_blocks(first)).isdisjoint(synced.index_blocks(second))
    for result in (first, second):
        for instance in result[:2]:
            assert instance["props"]["synced_from"] == {"document_id":"page-a", "block_id":"owned"}
            assert instance["props"]["sync_id"] == synced.group_id("page-a", "owned")
            assert instance["content"] == "" and not instance["props"].get("children")
            assert not instance["props"].get("rich_text_html")
        assert result[2]["props"]["synced_from"] == source[-2]["props"]["synced_from"]
        assert result[3]["content"] == "独立"
        assert result[3]["props"]["url"] == "#block-owned-child"
        assert result[3]["props"]["rich_text_html"] == source[-1]["props"]["rich_text_html"]


@pytest.mark.parametrize("template_references", [False, True])
@pytest.mark.parametrize("invalid", ["legacy", "content", "extra", "plain-target", "reference-chain"])
def test_template_instantiation_does_not_infer_or_clone_invalid_synced_originals(invalid, template_references):
    source = [original("owned"), reference("peer", "page-a", "owned")]
    if invalid == "legacy": source[0]["props"].pop("synced_from")
    elif invalid == "content": source[1]["content"] = "stale source cache"
    elif invalid == "extra": source[1]["props"]["synced_from"]["row_id"] = "guess"
    elif invalid == "plain-target": source[0] = block("owned")
    else: source[0] = reference("owned", "outside-page", "original")
    before = deepcopy(source)
    with pytest.raises(synced.SyncedShapeUnavailable):
        _duplicate_row_blocks(source, actor="owner", document="page-a", database="db-a",
                              operation="invalid-body", source_row="template-a", template_references=template_references)
    assert source == before


def test_partial_database_copy_remains_guarded_without_whole_body_identity_witness():
    source = source_with_synced_bodies()
    with pytest.raises(remap.CopyShapeUnavailable, match="complete body identity witness"):
        remap._Remapper(source, "partial-db-sync", None, partial_documents=True)


def test_copy_rejects_instance_endpoint_collision_and_direct_database_reference_guard_stays_closed():
    from code_assist_v1.notion_collaboration.database_access import _template_body_has_unscoped_reference
    source = source_with_synced_bodies(); root = source.documents[0].descriptor()
    root["blocks"].append(block("template-peer"))
    bad = replace(source, documents=(replace(source.documents[0], descriptor_json=json.dumps(root)),source.documents[1]))
    with pytest.raises(remap.CopyShapeUnavailable, match="ambiguous"):
        remap.remap_page_copy(bad, "db-sync-instance-collision")
    for body in ([original("private")], [reference("peer", "page-a", "private")]):
        assert _template_body_has_unscoped_reference(body) is True


def test_partial_plan_only_preserves_missing_external_originals_not_present_invalid_targets():
    source = fixture(); root = source.documents[0].descriptor()
    root["blocks"].append(block("only-plain"))
    root["blocks"].append(reference("peer", "page-a", "only-plain"))
    bad = replace(source, documents=(replace(source.documents[0],descriptor_json=json.dumps(root)),source.documents[1]))
    with pytest.raises(remap.CopyShapeUnavailable,match="must target an original"):
        remap._Remapper(bad,"partial-present-plain",None,partial_documents=True)
    root["blocks"][-1] = reference("peer", "page-a", "outside-selection")
    selected = replace(source,documents=(replace(source.documents[0],descriptor_json=json.dumps(root)),source.documents[1]))
    result = remap._Remapper(selected,"partial-missing-origin",None,partial_documents=True).build()
    assert result.documents[0].descriptor()["blocks"][-1]["props"]["synced_from"] == {
        "document_id":"page-a","block_id":"outside-selection"}
