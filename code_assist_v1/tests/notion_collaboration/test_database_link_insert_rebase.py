"""UI-DB-LINK-INSERT: notes survive owned DB and linked-view lifecycle writes."""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1.notion_collaboration import database_move, database_trash, page_move, page_copy_source, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block, copy, request as fences,
)
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _destination, _request, _move
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body, assert_note
from code_assist_v1.tests.notion_collaboration.test_page_archive import dump
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


@pytest.fixture()
def link_environment(activity_environment):
    old, ws = activity_environment
    _apply(trash_migration, old.db_path, old.user_data_root, "before-link-rebase-trash.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory), ws


def command_fences(store, action):
    if action == "move":
        return _request(store)
    request = fences(store)
    if action in {"archive", "restore"}:
        with store.iam._reader() as con:
            request["expected_database_version"] = con.execute(
                "SELECT row_version FROM workspace_collaboration_databases WHERE id='database-e1'").fetchone()[0]
    return request


def setup(store, ws, action):
    owned_database_block(store)
    current_update(store, ws)
    if action == "restore":
        database_trash.set_owned_database_trashed(store.iam, "owner", ws, "page-e1", "database-e1",
            archived=True, operation_id="db-link-prepare-restore", **command_fences(store, "archive"))
    original = body(store)
    pending = note(store, "pending-db-lifecycle-note")
    if action == "move":
        _destination(store, ws)
        text_update(store, ws, "page-db-destination", "destination-body", "Keep destination")
        destination_before = body(store, "page-db-destination")
        destination_note = note(store, "pending-db-destination-note", "page-db-destination")
        page = store.iam.read_document("owner", "page-e1")
        host = "page-db-linked-host"
        props = {"linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
            "views": [{"id": "local-view", "name": "Local view", "type": "table", "config": {"filters": [], "sorts": []}}],
            "active_view_id": "local-view", "indent": 1}
        store.iam.create_document("owner", ws, host, title="연결된 보기 원문",
            blocks=[{"id": "linked-host-text", "type": "paragraph", "content": "원래 보기 문단", "props": {}},
                    {"id": "retained-linked-view", "type": "table", "content": "", "props": props}],
            expected_workspace_epoch=page["workspace_epoch"], operation_id="db-link-host-create")
        text_update(store, ws, host, "linked-host-text", "현재 보기 문단")
        host_before = body(store, host)
        host_note = note(store, "pending-db-host-note", host)
    request = command_fences(store, action)
    if action == "move":
        call = lambda **options: _move(store, ws, operation_id="db-retain-note-move", fences=request, **options)
    elif action == "copy":
        call = lambda **options: copy(store, ws, "db-retain-note-copy", fences=request, **options)
    else:
        call = lambda **options: database_trash.set_owned_database_trashed(store.iam, "owner", ws, "page-e1", "database-e1",
            archived=action == "archive", operation_id="db-retain-note-" + action, **request, **options)
    assert all(block["id"] != pending["id"] for block in body(store)["blocks"])
    return locals()


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_db_link_pending_notes_rich_body_local_views_and_exact_retry(link_environment, action, monkeypatch):
    store, ws = link_environment
    plan = setup(store, ws, action)
    opened = 0
    reader, replay = store.iam._reader, search_projection.materialize
    @contextmanager
    def observed():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    def outside(*args, **kwargs):
        assert opened == 0, "DB link preparation/replay must run outside SQLite"
        return replay(*args, **kwargs)
    monkeypatch.setattr(store.iam, "_reader", observed)
    monkeypatch.setattr(search_projection, "materialize", outside)
    result = plan["call"]()
    current = body(store)
    assert_note(current, plan["pending"])
    assert current["title"] == plan["original"]["title"]
    old = plan["original"]["blocks"]
    kept = [block for block in current["blocks"] if block["id"] != plan["pending"]["id"]]
    if action in {"move", "archive"}:
        assert kept == [block for block in old if block["id"] != "owned-db-source-block"]
    elif action == "copy":
        assert [block for block in kept if block["id"] != result["block_id"]] == old
        assert store.iam.read_database("owner", result["database_id"])["rows"]
    else:
        assert [block for block in kept if block["id"] != "owned-db-source-block"] == old
    if action == "move":
        destination = body(store, "page-db-destination")
        assert_note(destination, plan["destination_note"])
        assert [block for block in destination["blocks"] if block["id"] not in {
            plan["destination_note"]["id"], "owned-db-source-block"}] == plan["destination_before"]["blocks"]
        host = body(store, plan["host"])
        assert_note(host, plan["host_note"])
        expected = deepcopy(plan["host_before"])
        next(block for block in expected["blocks"] if block["id"] == "retained-linked-view")["props"]["linked_database"]["document_id"] = "page-db-destination"
        assert [block for block in host["blocks"] if block["id"] != plan["host_note"]["id"]] == expected["blocks"]
        assert host["title"] == expected["title"]
        assert result["retargeted_linked_view_count"] == 1
    stable = dump(store)
    replayed = plan["call"]()
    assert {k: v for k, v in replayed.items() if k != "replayed"} == {k: v for k, v in result.items() if k != "replayed"}
    assert dump(store) == stable
    reopened = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    assert body(reopened) == current


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_db_link_older_uncertified_note_gap_refuses_without_writes(link_environment, action, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = link_environment
    plan = setup(store, ws, action)
    old = plan["original"]["blocks"][0]
    text_update(store, ws, "page-e1", old["id"], old["content"])
    plan["request"].update(command_fences(store, action))
    before = dump(store)
    failure = page_copy_source.CopySourceChanged if action == "copy" else AuthorityUnavailable
    with pytest.raises(failure):
        plan["call"]()
    assert dump(store) == before
    assert any(block["id"] == plan["pending"]["id"] for block in store.iam.read_document("owner", "page-e1")["blocks"])


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_db_link_unrelated_delta_mutation_refuses_before_writes(link_environment, action, monkeypatch):
    store, ws = link_environment
    plan = setup(store, ws, action)
    prepare = page_move._prepare_deltas
    def bad(*args):
        deltas = prepare(*args)
        deltas["page-e1"]["payload"] = b"\0\0"
        return deltas
    monkeypatch.setattr(page_move, "_prepare_deltas", bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match="unrelated parent content"):
        plan["call"]()
    assert dump(store) == before


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_db_link_late_parent_or_host_note_is_rechecked_and_retained(link_environment, action, monkeypatch):
    store, ws = link_environment
    plan = setup(store, ws, action)
    prepare = page_move._prepare_deltas
    stable = None
    def late(*args):
        nonlocal stable
        deltas = prepare(*args)
        note(store, "late-db-link-note", plan.get("host", "page-e1"))
        stable = dump(store)
        return deltas
    monkeypatch.setattr(page_move, "_prepare_deltas", late)
    failure = (AuthorityUnavailable if action == "move" else
               page_copy_source.CopySourceChanged if action == "copy" else DocumentSequenceConflict)
    with pytest.raises(failure):
        plan["call"]()
    assert stable is not None and dump(store) == stable


@pytest.mark.parametrize("action", ["move", "copy", "archive", "restore"])
def test_ui_db_link_pending_notes_and_database_ledgers_roll_back_together(link_environment, action):
    store, ws = link_environment
    plan = setup(store, ws, action)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        plan["call"](fault="after_outbox")
    assert dump(store) == before
    plan["call"]()
    assert_note(body(store), plan["pending"])


def test_ui_db_link_linked_retarget_delta_cannot_modify_host_body(link_environment, monkeypatch):
    store, ws = link_environment
    plan = setup(store, ws, "move")
    prepare = database_move._prepare_linked_deltas
    def bad(*args):
        deltas = prepare(*args)
        deltas[plan["host"]] = b"\0\0"
        return deltas
    monkeypatch.setattr(database_move, "_prepare_linked_deltas", bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match="linked retarget changed unrelated"):
        plan["call"]()
    assert dump(store) == before
