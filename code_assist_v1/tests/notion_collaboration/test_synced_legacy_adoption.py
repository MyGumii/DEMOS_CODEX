"""UI-SYNC-LEGACY-PREP: explicit choice, lossless refusal and current authority."""
from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption as adoption
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, _canonical_json, _sha256
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, create, request, dump, text_update,
)


def endpoint(document, block):
    return {"document_id": document, "block_id": block}


def insert_legacy(store, document, identity, *, content="조합된 한글 A", **extra):
    props = {"sync_id": "legacy-group-A", "rich_text_html": "<strong>조합된 한글 A</strong>", **extra}
    store.iam.execute_typed_command("owner", document, command_kind="block.insert", target_id=identity,
        payload={"type": "synced_block", "content": content, "props": props, "after_block_id": None},
        operation_id="legacy-insert-" + identity, **request(store, document))


def setup_selection(store, ws):
    insert_legacy(store, "page-e1", "chosen-original")
    child = create(store, ws, operation="legacy-child-page")["page"]["id"]
    insert_legacy(store, child, "chosen-instance", indent=1)
    return child


def prepare(store, ws, child):
    return adoption.prepare_legacy_adoption(store.iam, "owner", ws,
        origin=endpoint("page-e1", "chosen-original"),
        references=[endpoint(child, "chosen-instance")])


def revalidate(store, pending):
    with store.iam._reader() as con:
        con.execute("BEGIN")
        adoption.revalidate_legacy_adoption(store.iam, con, pending)


def test_ui_legacy_prepare_explicit_origin_keeps_all_unselected_content_and_current_html(environment):
    store, ws = environment
    child = setup_selection(store, ws)
    insert_legacy(store, child, "unselected-member", content="다른 내용은 버리지 않는다", indent=0)
    before = dump(store)
    pending = prepare(store, ws, child)
    assert pending == prepare(store, ws, child)
    assert pending.value()["commit_ready"] is False
    assert pending.value()["scope"] == "explicitly_selected_flat_document_endpoints"
    assert pending.value()["origin"] == endpoint("page-e1", "chosen-original")
    assert pending.value()["references"] == [endpoint(child, "chosen-instance")]
    assert pending.value()["legacy_sync_id"] == "legacy-group-A"
    group = synced.group_id("page-e1", "chosen-original")
    assert pending.value()["sync_id"] == group
    for item in pending.documents:
        prior = json.loads(item.source.descriptor_json)["blocks"]
        after = item.blocks()
        assert [block["id"] for block in after] == [block["id"] for block in prior]
        selected = "chosen-original" if item.source.document_id == "page-e1" else "chosen-instance"
        for old, block in zip(prior, after):
            if block["id"] != selected:
                assert block == old
                continue
            assert block["type"] == old["type"]
            assert block["props"]["sync_id"] == group
            if selected == "chosen-original":
                assert block["props"]["synced_from"] is None
                assert block["content"] == old["content"]
                assert block["props"]["rich_text_html"] == old["props"]["rich_text_html"]
            else:
                assert block["props"]["synced_from"] == pending.value()["origin"]
                assert block["content"] == "" and "rich_text_html" not in block["props"]
                assert block["props"]["indent"] == old["props"]["indent"]
        # Callers cannot mutate the borrowed immutable preparation via its value.
        after.clear()
        assert item.blocks()
    revalidate(store, pending)
    assert dump(store) == before
    assert store.capabilities().get("collaborative_page_copy") is False
    with pytest.raises(synced.SyncedShapeUnavailable, match="explicit original"):
        synced.read_synced_block(store.iam, "owner", child, "chosen-instance")
    assert dump(store) == before, "preparation did not convert operating content"


@pytest.mark.parametrize("shape", ["divergent_text", "divergent_markup", "other_group", "already_explicit", "inline_children", "inferred_children"])
def test_ui_legacy_prepare_refuses_content_loss_and_unowned_nested_shape(environment, shape):
    store, ws = environment
    insert_legacy(store, "page-e1", "chosen-original")
    child = create(store, ws, operation="legacy-refusal-child")["page"]["id"]
    options = {"content": "different text"} if shape == "divergent_text" else {}
    if shape == "divergent_markup":
        options["rich_text_html"] = "<em>조합된 한글 A</em>"
    if shape == "other_group":
        options["sync_id"] = "legacy-group-B"
    if shape == "already_explicit":
        options["synced_from"] = None
    if shape == "inline_children":
        options["children"] = [{"id": "nested-private-text", "type": "paragraph", "content": "keep", "props": {}}]
    insert_legacy(store, child, "chosen-instance", **options)
    if shape == "inferred_children":
        store.iam.execute_typed_command("owner", child, command_kind="block.insert", target_id="inferred-private-text",
            payload={"type": "paragraph", "content": "keep inferred child", "props": {"indent": 1},
                     "after_block_id": "chosen-instance"}, operation_id="legacy-inferred-child", **request(store, child))
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable):
        prepare(store, ws, child)
    assert dump(store) == before


def test_ui_legacy_prepare_current_yjs_replay_runs_outside_readers_and_does_not_copy_stale_text(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    for document, block in (("page-e1", "chosen-original"), (child, "chosen-instance")):
        text_update(store, ws, document, block, "새로운 현재 Yjs 문장")
    before = dump(store)
    opened = 0
    reader = store.iam._reader
    @contextmanager
    def tracked_reader():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    monkeypatch.setattr(store.iam, "_reader", tracked_reader)
    materialize = synced.search_projection.materialize
    def outside(*args, **kwargs):
        assert opened == 0
        return materialize(*args, **kwargs)
    monkeypatch.setattr(synced.search_projection, "materialize", outside)
    pending = prepare(store, ws, child)
    original = next(item for item in pending.documents if item.source.document_id == "page-e1")
    assert next(block for block in original.blocks() if block["id"] == "chosen-original")["content"] == "새로운 현재 Yjs 문장"
    assert all(item.source.frames for item in pending.documents)
    assert dump(store) == before


@pytest.mark.parametrize("failure", ["content_change", "identity_revoked"])
def test_ui_legacy_prepare_rechecks_after_replay_without_partial_conversion(environment, monkeypatch, failure):
    store, ws = environment
    child = setup_selection(store, ws)
    materialize = synced.search_projection.materialize
    calls, after = 0, []
    def during(*args, **kwargs):
        nonlocal calls
        result = materialize(*args, **kwargs)
        calls += 1
        if calls == 2:
            if failure == "content_change":
                store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="concurrent-legacy-note",
                    payload={"type": "paragraph", "content": "new outside note", "after_block_id": None},
                    operation_id="legacy-concurrent-note", **request(store))
            else:
                previous = store.iam.identity_directory.is_active_human
                monkeypatch.setattr(store.iam.identity_directory, "is_active_human",
                    lambda subject: subject != "owner" and previous(subject))
            after.extend(dump(store))
        return result
    monkeypatch.setattr(synced.search_projection, "materialize", during)
    with pytest.raises((synced.SyncedSourceChanged, NotFoundOrDenied)):
        prepare(store, ws, child)
    assert calls == 2 and dump(store) == tuple(after)


def test_ui_legacy_revalidation_rejects_changed_locked_or_tampered_preparation(environment):
    store, ws = environment
    child = setup_selection(store, ws)
    pending = prepare(store, ws, child)
    tampered = replace(pending.documents[0], blocks_json="[]")
    with pytest.raises(ValueError, match="modified"):
        revalidate(store, replace(pending, documents=(tampered, *pending.documents[1:])))
    metadata = pending.value()
    metadata["origin"]["block_id"] = "another-block"
    payload = _canonical_json(metadata)
    with pytest.raises(ValueError, match="modified"):
        revalidate(store, replace(pending, payload_json=payload, payload_sha256=_sha256(payload)))
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        adoption.prepare_legacy_adoption(store.iam, "outsider", ws,
            origin=endpoint("page-e1", "chosen-original"), references=[endpoint(child, "chosen-instance")])
    assert dump(store) == before
    update_page_settings(store.iam, "owner", ws, child, updates={"locked": True},
        operation_id="legacy-lock-before-validation", **request(store, child))
    locked = dump(store)
    with pytest.raises(PermissionError, match="locked"):
        revalidate(store, pending)
    assert dump(store) == locked


def test_ui_legacy_prepare_rejects_implicit_duplicate_or_unbounded_selection_before_replay(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid selection must fail before replay")
    monkeypatch.setattr(synced.search_projection, "materialize", forbidden)
    original = endpoint("page-e1", "chosen-original")
    instance = endpoint(child, "chosen-instance")
    before = dump(store)
    for origin, references in (({}, [instance]), (original, [original]), (original, [instance, instance]),
                               (original, [endpoint(child, "ref-" + str(index)) for index in range(128)])):
        with pytest.raises(ValueError):
            adoption.prepare_legacy_adoption(store.iam, "owner", ws, origin=origin, references=references)
    monkeypatch.setattr(adoption, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(synced.SyncedShapeUnavailable, match="budget"):
        prepare(store, ws, child)
    assert dump(store) == before
