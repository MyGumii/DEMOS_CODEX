"""UI-LINK-INSERT: user notes survive later page link lifecycle operations."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import page_move, page_archive, page_copy_source, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    create, copy, request as fences, dump, current_update,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive import request as archive_request
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def note(store, identity, document="page-e1", after=None):
    store.iam.execute_typed_command("owner", document, command_kind="block.insert", target_id=identity,
        payload={"type": "paragraph", "content": "새 문단 한글과 서식 보존 " + identity,
                 "props": {"rich_text_html": "<em>새 문단 한글과 서식 보존 " + identity + "</em>", "color": "blue"},
                 "after_block_id": after}, operation_id="insert-" + identity, **fences(store, document))
    return next(block for block in store.iam.read_document("owner", document)["blocks"] if block["id"] == identity)


def body(store, document="page-e1"):
    with store.iam._reader() as con:
        frames = _frames(con, document, _generation(con, document))
    return search_projection.materialize([frames])[0]


def setup(store, ws, action):
    current_update(store, ws)
    child = create(store, ws, operation="link-retention-child")["page"]["id"]
    anchor = create(store, ws, operation="link-retention-anchor")["page"]["id"]
    if action == "restore":
        page_archive.set_page_archived(store.iam, "owner", ws, child,
            **archive_request(store, ws, child, operation="prepare-restore"))
    original = body(store)
    pending = note(store, "pending-link-note")
    assert all(block["id"] != pending["id"] for block in body(store)["blocks"])
    if action == "move":
        destination = create(store, ws, parent="", operation="link-retention-destination")["page"]["id"]
        create(store, ws, parent=destination, operation="destination-frame-bootstrap")
        destination_before = body(store, destination)
        destination_note = note(store, "pending-destination-note", destination)
        request = dict(parent_id=destination, operation_id="retain-move-note", **fences(store, child))
        call = lambda **options: page_move.move_page(store.iam, "owner", ws, child, **request, **options)
    elif action == "copy":
        request = fences(store, child)
        call = lambda **options: copy(store, ws, child, "retain-copy-note", fences=request, **options)
    else:
        request = archive_request(store, ws, child, archived=action == "archive", operation="retain-" + action + "-note")
        call = lambda **options: page_archive.set_page_archived(store.iam, "owner", ws, child, **request, **options)
    return locals()


def without(blocks, *identities):
    return [block for block in blocks if block["id"] not in identities]


def assert_note(current, pending):
    assert next(block for block in current["blocks"] if block["id"] == pending["id"]) == {
        key: value for key, value in pending.items() if key != "position"}


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_link_pending_note_current_rich_body_title_and_exact_retry(environment, action, monkeypatch):
    store, ws = environment
    plan = setup(store, ws, action)
    opened = 0
    reader, materialize = store.iam._reader, search_projection.materialize
    @contextmanager
    def observed_reader():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    def outside(*args, **kwargs):
        assert opened == 0, "link verification cannot retain a SQLite reader"
        return materialize(*args, **kwargs)
    monkeypatch.setattr(store.iam, "_reader", observed_reader)
    monkeypatch.setattr(search_projection, "materialize", outside)
    result = plan["call"]()
    current = body(store)
    assert_note(current, plan["pending"])
    assert current["title"] == plan["original"]["title"]
    if action in {"move", "archive"}:
        old_link = next(block for block in plan["original"]["blocks"] if block["props"].get("page_id") == plan["child"])
        assert without(current["blocks"], plan["pending"]["id"]) == without(plan["original"]["blocks"], old_link["id"])
    else:
        linked = result["page"]["id"] if action == "copy" else plan["child"]
        link = next(block for block in current["blocks"] if block["props"].get("page_id") == linked)
        assert without(current["blocks"], plan["pending"]["id"], link["id"]) == plan["original"]["blocks"]
    if action == "move":
        destination = body(store, plan["destination"])
        assert_note(destination, plan["destination_note"])
        link = next(block for block in destination["blocks"] if block["props"].get("page_id") == plan["child"])
        assert without(destination["blocks"], link["id"], plan["destination_note"]["id"]) == plan["destination_before"]["blocks"]
        assert destination["title"] == plan["destination_before"]["title"]
    stable = dump(store)
    replay = plan["call"]()
    assert {key: value for key, value in replay.items() if key != "replayed"} == {key: value for key, value in result.items() if key != "replayed"}
    assert dump(store) == stable
    reopened = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    assert body(reopened) == current


@pytest.mark.parametrize("direction", ["before", "after"])
def test_ui_link_same_parent_position_keeps_note_and_original_link_identity(environment, direction):
    store, ws = environment
    plan = setup(store, ws, "archive")
    old = plan["original"]["blocks"]
    child_link = next(block for block in old if block["props"].get("page_id") == plan["child"])
    anchor_link = next(block for block in old if block["props"].get("page_id") == plan["anchor"])
    page_move.move_page(store.iam, "owner", ws, plan["child"], **{direction + "_id": plan["anchor"]},
        operation_id="same-parent-" + direction, **fences(store, plan["child"]))
    current = body(store)
    assert_note(current, plan["pending"])
    assert next(block for block in current["blocks"] if block["id"] == child_link["id"]) == child_link
    ids = [block["id"] for block in current["blocks"]]
    assert ids.index(child_link["id"]) == ids.index(anchor_link["id"]) + (-1 if direction == "before" else 1)
    assert without(current["blocks"], child_link["id"], plan["pending"]["id"]) == without(old, child_link["id"])
    assert current["title"] == plan["original"]["title"]


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_link_uncertified_older_gap_refuses_without_writes(environment, action, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = environment
    plan = setup(store, ws, action)
    block = plan["original"]["blocks"][0]
    text_update(store, ws, "page-e1", block["id"], block["content"])
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        plan["call"]()
    assert dump(store) == before
    assert any(block["id"] == plan["pending"]["id"] for block in store.iam.read_document("owner", "page-e1")["blocks"])


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_link_independent_replay_refuses_tampered_delta_without_writes(environment, action, monkeypatch):
    store, ws = environment
    plan = setup(store, ws, action)
    prepare = page_move._prepare_deltas
    def tampered(*args):
        deltas = prepare(*args)
        deltas["page-e1"]["payload"] = b"\0\0"
        return deltas
    monkeypatch.setattr(page_move, "_prepare_deltas", tampered)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match="unrelated parent content"):
        plan["call"]()
    assert dump(store) == before


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_link_parent_edit_after_preparation_is_rechecked_and_retained(environment, action, monkeypatch):
    store, ws = environment
    plan = setup(store, ws, action)
    prepare = page_move._prepare_deltas
    after_edit = None
    def late(*args):
        nonlocal after_edit
        deltas = prepare(*args)
        note(store, "late-after-link-preparation")
        after_edit = dump(store)
        return deltas
    monkeypatch.setattr(page_move, "_prepare_deltas", late)
    expected = page_copy_source.CopySourceChanged if action == "copy" else DocumentSequenceConflict
    with pytest.raises(expected):
        plan["call"]()
    assert after_edit is not None and dump(store) == after_edit
    assert any(block["id"] == "late-after-link-preparation" for block in store.iam.read_document("owner", "page-e1")["blocks"])


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_link_rebased_note_lifecycle_and_all_ledgers_roll_back_together(environment, action):
    store, ws = environment
    plan = setup(store, ws, action)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        plan["call"](fault="after_outbox")
    assert dump(store) == before
    plan["call"]()
    assert_note(body(store), plan["pending"])


def _move_process(catalog, root, ws, child, request, ready, start, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    prepare = page_move._prepare_deltas
    def coordinated(*args):
        value = prepare(*args)
        ready.put(True)
        if not start.wait(60):
            raise RuntimeError("outside-transaction barrier timed out")
        return value
    page_move._prepare_deltas = coordinated
    try:
        results.put(page_move.move_page(store.iam, "owner", ws, child, **request))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_link_two_processes_prepare_both_pending_notes_and_move_once(environment):
    store, ws = environment
    plan = setup(store, ws, "move")
    context = multiprocessing.get_context("spawn")
    ready, start, results = context.Queue(), context.Event(), context.Queue()
    workers = [context.Process(target=_move_process, args=(str(store.db_path), str(store.user_data_root), ws,
        plan["child"], plan["request"], ready, start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert [ready.get(timeout=120) for _ in workers] == [True, True]
        start.set()
        values = [results.get(timeout=120) for _ in workers]
        assert all("error" not in value for value in values), values
        assert values[0] == values[1]
        assert_note(body(store), plan["pending"])
        current = body(store, plan["destination"])
        assert_note(current, plan["destination_note"])
        assert sum(block["props"].get("page_id") == plan["child"] for block in current["blocks"]) == 1
    finally:
        start.set()
        for worker in workers:
            if worker.pid:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=10)
