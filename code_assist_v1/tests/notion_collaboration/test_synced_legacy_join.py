"""UI-SYNC-LEGACY-JOIN: later members preserve the recorded original identity."""
from dataclasses import replace
import json

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption as adoption
from code_assist_v1.notion_collaboration import synced_legacy_adoption_commit as commit
from code_assist_v1.notion_collaboration import synced_legacy_adoption_service as service
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, _canonical_json, _sha256
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, insert_legacy, setup_selection, endpoint,
    prepare, request, dump, text_update, create,
)
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption_commit import inspect


def initial(store, ws):
    child = setup_selection(store, ws)
    selected = prepare(store, ws, child)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    first = commit.commit_prepared_adoption(store.iam, delta, operation_id="legacy-designation-first")
    return child, first


def selection(store, ws, document, identity="later-member"):
    return adoption.prepare_legacy_adoption(store.iam, "owner", ws,
        origin=endpoint("page-e1", "chosen-original"), references=[endpoint(document, identity)])


def reviewed_body(selected, operation="legacy-join-existing"):
    return {"origin": selected.value()["origin"], "references": selected.value()["references"],
        "preparation_sha256": selected.payload_sha256, "operation_id": operation}


def test_ui_legacy_join_other_page_keeps_original_raw_rows_frames_revision_and_original_reference(environment):
    store, ws = environment
    child, first = initial(store, ws)
    later = create(store, ws, operation="legacy-later-member-page")["page"]["id"]
    insert_legacy(store, later, "later-member", indent=1)
    selected = selection(store, ws, later)
    before_review = dump(store)
    assert selected.provenance_json and selected.value()["origin_adoption"] == _sha256(selected.provenance_json)
    assert json.loads(selected.provenance_json)["operation_id"] == first["operation_id"]
    assert selected == selection(store, ws, later)
    assert dump(store) == before_review
    delta = commit.prepare_adoption_delta(store.iam, selected)
    body = reviewed_body(selected)
    service.register_legacy_adoption_intent(store.iam, "owner", ws, **body)
    before, counts = inspect(store, delta)
    status = service.adopt_legacy_selection(store.iam, "owner", ws, **body)
    assert status["state"] == "committed"
    after, updated_counts = inspect(store, delta)
    assert after["page-e1"] == before["page-e1"], "joining another page never edits/materializes/revisions the original page"
    assert updated_counts["workspace_collaboration_revisions"] == counts["workspace_collaboration_revisions"] + 1
    origin_receipt = next(item for item in status["result"]["documents"] if item["document_id"] == "page-e1")
    assert origin_receipt["unchanged"] is True and origin_receipt["update_frames"] == 0
    assert status["result"]["legacy_sync_id"] == "legacy-group-A"
    assert status["result"]["sync_id"] == first["sync_id"]
    for document, block in ((child, "chosen-instance"), (later, "later-member")):
        current = synced.read_synced_block(store.iam, "owner", document, block)
        assert current["origin"] == endpoint("page-e1", "chosen-original")
        assert current["block"]["content"] == "조합된 한글 A"
    stable = dump(store)
    assert service.adopt_legacy_selection(store.iam, "owner", ws, **body)["result"]["replayed"] is True
    assert service.cancel_legacy_adoption(store.iam, "owner", ws, **body)["state"] == "committed"
    assert dump(store) == stable


def test_ui_legacy_join_same_page_changes_only_selected_target_and_one_document_revision(environment):
    store, ws = environment
    _child, first = initial(store, ws)
    insert_legacy(store, "page-e1", "later-member", indent=0)
    selected = selection(store, ws, "page-e1")
    delta = commit.prepare_adoption_delta(store.iam, selected)
    before, counts = inspect(store, delta)
    original = next(item for item in before["page-e1"]["rows"] if item["id"] == "chosen-original")
    result = commit.commit_prepared_adoption(store.iam, delta, operation_id="legacy-same-page-join")
    after, updated_counts = inspect(store, delta)
    assert next(item for item in after["page-e1"]["rows"] if item["id"] == "chosen-original") == original
    assert updated_counts["workspace_collaboration_revisions"] == counts["workspace_collaboration_revisions"] + 1
    assert len(result["documents"]) == 1 and result["sync_id"] == first["sync_id"]
    assert synced.read_synced_block(store.iam, "owner", "page-e1", "later-member")["block"]["content"] == "조합된 한글 A"


@pytest.mark.parametrize("difference", ["text", "markup", "group", "unproven_marker", "reference_as_original"])
def test_ui_legacy_join_refuses_lost_content_or_unproven_original_without_writes(environment, difference):
    store, ws = environment
    child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-join-refusal-page")["page"]["id"]
    options = {"content": "different text"} if difference == "text" else {"rich_text_html": "<em>조합된 한글 A</em>"} if difference == "markup" else {"sync_id": "other-group"} if difference == "group" else {}
    insert_legacy(store, later, "later-member", **options)
    origin = endpoint("page-e1", "chosen-original")
    if difference == "unproven_marker":
        origin = endpoint(later, "fake-original")
        insert_legacy(store, later, "fake-original", synced_from=None, sync_id=synced.group_id(later, "fake-original"))
    elif difference == "reference_as_original":
        origin = endpoint(child, "chosen-instance")
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable):
        adoption.prepare_legacy_adoption(store.iam, "owner", ws,
            origin=origin, references=[endpoint(later, "later-member")])
    assert dump(store) == before


def test_ui_legacy_join_rollback_exact_retry_and_current_source_after_review(environment):
    store, ws = environment
    _child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-join-rollback-page")["page"]["id"]
    insert_legacy(store, later, "later-member")
    selected = selection(store, ws, later)
    body = reviewed_body(selected)
    service.register_legacy_adoption_intent(store.iam, "owner", ws, **body)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        service.adopt_legacy_selection(store.iam, "owner", ws, fault="after_outbox", **body)
    assert dump(store) == before
    assert service.read_legacy_adoption_result(store.iam, "owner", ws, **body)["state"] == "unknown"
    assert service.adopt_legacy_selection(store.iam, "owner", ws, **body)["state"] == "committed"
    text_update(store, ws, "page-e1", "chosen-original", "原本 최신 문장")
    stable = dump(store)
    assert service.adopt_legacy_selection(store.iam, "owner", ws, **body)["state"] == "committed"
    assert synced.read_synced_block(store.iam, "owner", later, "later-member")["block"]["content"] == "原本 최신 문장"
    assert dump(store) == stable


@pytest.mark.parametrize("change", ["edit", "lock", "identity"])
def test_ui_legacy_join_revalidates_source_and_authority_before_any_commit(environment, monkeypatch, change):
    store, ws = environment
    _child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-join-change-page")["page"]["id"]
    insert_legacy(store, later, "later-member")
    selected = selection(store, ws, later)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    if change == "edit":
        text_update(store, ws, "page-e1", "chosen-original", "changed source text")
    elif change == "lock":
        page = store.iam.read_document("owner", "page-e1")
        update_page_settings(store.iam, "owner", ws, "page-e1", updates={"locked": True},
            operation_id="legacy-original-lock", expected_document_sequence=page["document_sequence"],
            expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])
    else:
        previous = store.iam.identity_directory.is_active_human
        monkeypatch.setattr(store.iam.identity_directory, "is_active_human", lambda actor: actor != "owner" and previous(actor))
    before = dump(store)
    with pytest.raises((synced.SyncedSourceChanged, NotFoundOrDenied, PermissionError)):
        commit.commit_prepared_adoption(store.iam, delta, operation_id="legacy-stale-join")
    assert dump(store) == before


def test_ui_legacy_join_rejects_changed_proof_even_if_caller_rehashes_payload(environment):
    store, ws = environment
    _child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-modified-proof-page")["page"]["id"]
    insert_legacy(store, later, "later-member")
    selected = selection(store, ws, later)
    proof = json.loads(selected.provenance_json)
    proof["operation_id"] = "different-provenance-operation"
    proof_json = _canonical_json(proof)
    payload = selected.value(); payload["origin_adoption"] = _sha256(proof_json)
    payload_json = _canonical_json(payload)
    modified = replace(selected, provenance_json=proof_json, payload_json=payload_json, payload_sha256=_sha256(payload_json))
    before = dump(store)
    with pytest.raises(synced.SyncedSourceChanged):
        commit.prepare_adoption_delta(store.iam, modified)
    assert dump(store) == before


def test_ui_legacy_join_preserves_original_post_frame_canonical_insert_without_materializing_it(environment):
    store, ws = environment
    _child, _first = initial(store, ws)
    # Creating a child adds a durable parent-link frame. Insert the note after
    # that frame so this fixture actually represents a post-frame typed insert.
    later = create(store, ws, operation="legacy-post-frame-later")["page"]["id"]
    insert_legacy(store, later, "later-member")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="outside-late-note",
        payload={"type": "paragraph", "content": "동기화 밖 추가 문단 유지", "props": {}, "after_block_id": None},
        operation_id="legacy-original-outside-note", **request(store))
    with store.iam._reader() as con:
        frame_sequence = con.execute("SELECT MAX(document_sequence) FROM workspace_collaboration_crdt_updates WHERE document_id='page-e1'").fetchone()[0]
        insert_sequence = con.execute("SELECT document_sequence FROM workspace_collaboration_typed_commands WHERE document_id='page-e1' AND target_id='outside-late-note'").fetchone()[0]
        assert insert_sequence > frame_sequence
    selected = selection(store, ws, later)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    assert dict(delta.updates)["page-e1"] == ()
    before, _counts = inspect(store, delta)
    commit.commit_prepared_adoption(store.iam, delta, operation_id="legacy-original-post-frame-join")
    after, _after_counts = inspect(store, delta)
    assert after["page-e1"] == before["page-e1"]
    assert next(block for block in after["page-e1"]["snapshot"]["blocks"] if block["id"] == "outside-late-note")["content"] == "동기화 밖 추가 문단 유지"
    assert synced.read_synced_block(store.iam, "owner", later, "later-member")["block"]["content"] == "조합된 한글 A"


def test_ui_legacy_join_refuses_original_insert_missing_from_a_later_frame_without_overwriting_it(environment):
    store, ws = environment
    _child, _first = initial(store, ws)
    later = create(store, ws, operation="legacy-unrebased-later")["page"]["id"]
    insert_legacy(store, later, "later-member")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="outside-late-note",
        payload={"type": "paragraph", "content": "나중의 프레임이 누락해도 보존", "props": {}, "after_block_id": None},
        operation_id="legacy-original-unrebased-note", **request(store))
    # Represent an older/client-authored stream that omits the canonical note.
    # Native creation now rebases proven inserts, so it cannot create this gap.
    text_update(store, ws, "page-e1", "chosen-original", "조합된 한글 A")
    with store.iam._reader() as con:
        frame_sequence = con.execute("SELECT MAX(document_sequence) FROM workspace_collaboration_crdt_updates WHERE document_id='page-e1'").fetchone()[0]
        insert_sequence = con.execute("SELECT document_sequence FROM workspace_collaboration_typed_commands WHERE document_id='page-e1' AND target_id='outside-late-note'").fetchone()[0]
        assert insert_sequence < frame_sequence
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable, match="structures differ"):
        selection(store, ws, later)
    assert dump(store) == before
    with store.iam._reader() as con:
        block = con.execute("SELECT content FROM workspace_collaboration_blocks WHERE document_id='page-e1' AND id='outside-late-note'").fetchone()
        assert block[0] == "나중의 프레임이 누락해도 보존"


def test_ui_legacy_join_after_note_then_child_creation_retains_rebased_note_and_original_history(environment):
    store, ws = environment
    _child, _first = initial(store, ws)
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="outside-late-note",
        payload={"type": "paragraph", "content": "문단 추가 후 하위 페이지 생성도 유지", "props": {"rich_text_html": "<em>문단 추가 후 하위 페이지 생성도 유지</em>"}, "after_block_id": None},
        operation_id="legacy-original-before-child-note", **request(store))
    later = create(store, ws, operation="legacy-child-after-note")["page"]["id"]
    insert_legacy(store, later, "later-member")
    selected = selection(store, ws, later)
    delta = commit.prepare_adoption_delta(store.iam, selected)
    assert dict(delta.updates)["page-e1"] == ()
    before, _counts = inspect(store, delta)
    commit.commit_prepared_adoption(store.iam, delta, operation_id="legacy-rebased-parent-join")
    after, _after_counts = inspect(store, delta)
    assert after["page-e1"] == before["page-e1"]
    current = synced._current(store.iam, "owner", "page-e1", 64 * 1024 * 1024, ws, strict_structure=True)
    note = next(block for block in json.loads(current.descriptor_json)["blocks"] if block["id"] == "outside-late-note")
    assert note["content"] == "문단 추가 후 하위 페이지 생성도 유지"
    assert note["props"]["rich_text_html"] == "<em>문단 추가 후 하위 페이지 생성도 유지</em>"
    assert synced.read_synced_block(store.iam, "owner", later, "later-member")["block"]["content"] == "조합된 한글 A"
