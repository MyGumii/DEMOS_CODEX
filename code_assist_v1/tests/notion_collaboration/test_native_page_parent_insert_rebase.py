"""UI-CREATE-INSERT: canonical post-frame notes survive a later child link."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import page_creation, page_move, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import (
    environment, archive_environment, guest_environment, image_environment, create, current_update, dump, registry,
)
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def insert_note(store, identity="pending-rich-note"):
    page = store.iam.read_document("owner", "page-e1")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id=identity,
        payload={"type": "paragraph", "content": "새 문단의 한글 유지",
                 "props": {"rich_text_html": "<em>새 문단의 한글 유지</em>", "color": "blue"}, "after_block_id": None},
        operation_id="insert-" + identity, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def setup_parent(store, ws):
    current_update(store, ws)
    create(store, ws, operation="parent-body-bootstrap")
    insert_note(store)


def projection(store):
    with store.iam._reader() as con:
        frames = _frames(con, "page-e1", _generation(con, "page-e1"))
    return search_projection.materialize([frames])[0]


def test_ui_create_preserves_pending_note_current_text_rich_props_and_exact_retry(environment, monkeypatch):
    store, ws = environment
    setup_parent(store, ws)
    original = projection(store)
    canonical_note = next(block for block in store.iam.read_document("owner", "page-e1")["blocks"] if block["id"] == "pending-rich-note")
    readers = 0
    reader, materialize = store.iam._reader, search_projection.materialize
    @contextmanager
    def observed_reader():
        nonlocal readers
        with reader() as con:
            readers += 1
            try:
                yield con
            finally:
                readers -= 1
    def outside(*args, **kwargs):
        assert readers == 0, "independent replay cannot hold a SQLite reader"
        return materialize(*args, **kwargs)
    monkeypatch.setattr(store.iam, "_reader", observed_reader)
    monkeypatch.setattr(search_projection, "materialize", outside)
    version = store.get_workspace("owner", ws)["version"]
    result = create(store, ws, operation="child-after-pending-note", base_version=version)
    after = projection(store)
    note = next(block for block in after["blocks"] if block["id"] == "pending-rich-note")
    assert note == {key: value for key, value in canonical_note.items() if key != "position"}
    link = next(block for block in after["blocks"] if block["props"].get("page_id") == result["page"]["id"])
    assert [block for block in after["blocks"] if block["id"] not in {note["id"], link["id"]}] == original["blocks"]
    assert after["title"] == original["title"]
    stable = dump(store)
    assert create(store, ws, operation="child-after-pending-note", base_version=version)["replayed"] is True
    assert dump(store) == stable


@pytest.mark.parametrize("fault", ["after_event", "after_audit", "after_outbox"])
def test_ui_create_rebased_note_and_child_roll_back_together(environment, fault):
    store, ws = environment
    setup_parent(store, ws)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        create(store, ws, operation="rebase-fault-child", fault=fault)
    assert dump(store) == before
    result = create(store, ws, operation="rebase-fault-child")
    assert any(block["id"] == "pending-rich-note" for block in projection(store)["blocks"])
    assert result["replayed"] is False


def test_ui_create_rechecks_source_after_rebase_and_keeps_late_insert(environment, monkeypatch):
    store, ws = environment
    setup_parent(store, ws)
    prepare = page_move._prepare_deltas
    def late(*args):
        result = prepare(*args)
        insert_note(store, "late-note-after-preparation")
        return result
    monkeypatch.setattr(page_move, "_prepare_deltas", late)
    before_count = len(registry(store))
    with pytest.raises(DocumentSequenceConflict):
        create(store, ws, operation="stale-rebased-child")
    assert len(registry(store)) == before_count
    assert any(block["id"] == "late-note-after-preparation" for block in store.iam.read_document("owner", "page-e1")["blocks"])


def test_ui_create_refuses_uncertified_older_insert_missing_from_a_newer_frame_without_any_write(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = environment
    setup_parent(store, ws)
    block = projection(store)["blocks"][0]
    text_update(store, ws, "page-e1", block["id"], block["content"])
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation="unproven-gap-child")
    assert dump(store) == before
    assert any(block["id"] == "pending-rich-note" for block in store.iam.read_document("owner", "page-e1")["blocks"])


def test_ui_create_independent_projection_refuses_modified_parent_delta_before_writing(environment, monkeypatch):
    store, ws = environment
    setup_parent(store, ws)
    prepare = page_move._prepare_deltas
    def bad(*args):
        result = prepare(*args)
        result["page-e1"]["payload"] = b"\0\0"
        return result
    monkeypatch.setattr(page_move, "_prepare_deltas", bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match="unrelated parent content"):
        create(store, ws, operation="modified-rebase-child")
    assert dump(store) == before


def _create_rebased_process(catalog, root, workspace, version, ready, start, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    prepare = page_move._prepare_deltas
    def coordinated(*args):
        value = prepare(*args)
        ready.put(True)
        if not start.wait(60):
            raise RuntimeError("prepare barrier timed out")
        return value
    page_move._prepare_deltas = coordinated
    try:
        results.put(create(store, workspace, operation="two-process-rebased-child", base_version=version))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_create_two_processes_prepare_pending_note_and_commit_same_child_once(environment):
    store, ws = environment
    setup_parent(store, ws)
    version = store.get_workspace("owner", ws)["version"]
    count = len(registry(store))
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    workers = [context.Process(target=_create_rebased_process,
        args=(str(store.db_path), str(store.user_data_root), ws, version, ready, start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert [ready.get(timeout=120) for _ in workers] == [True, True]
        start.set()
        values = [results.get(timeout=120) for _ in workers]
        assert all("error" not in value for value in values), values
        assert sorted(value["replayed"] for value in values) == [False, True]
        assert values[0]["page"] == values[1]["page"]
        assert len(registry(store)) == count + 1
        current = projection(store)
        assert sum(block["id"] == "pending-rich-note" for block in current["blocks"]) == 1
        assert sum(block["props"].get("page_id") == values[0]["page"]["id"] for block in current["blocks"]) == 1
    finally:
        start.set()
        for worker in workers:
            if worker.pid:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate(); worker.join(timeout=10)
