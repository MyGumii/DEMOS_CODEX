"""UI-PAGE-ARCHIVE: canonical state, preserved bodies, scoped restore and fences."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import notion_workspace_page_archive_migration as migration
from code_assist_v1.notion_collaboration import page_archive, page_move
from code_assist_v1.notion_collaboration.canonical_authority import (
    EpochConflict, InjectedFailure, NotFoundOrDenied, OperationConflict,
)
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority, DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_workspace_images import environment as image_environment
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import environment as guest_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _backup
from code_assist_v1.tests.notion_collaboration.test_page_move import current_body


@contextmanager
def database(path):
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


def dump(store):
    # A sqlite context manager commits but does not close. Explicitly close the
    # inventory connection before the Windows migrator replaces a catalog.
    with database(store.db_path) as con:
        return tuple(con.iterdump())


@pytest.fixture()
def environment(guest_environment):
    old, ws = guest_environment
    _apply(migration, old.db_path, old.user_data_root, "before-2413.db")
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)
    return store, ws


def request(store, ws, doc="page-e1", *, archived=True, operation="archive-1"):
    # Restoring is an explicit owner-only trash action; it must not require an
    # ordinary read grant to the archived body.
    with store.iam._reader() as con:
        workspace, page = page_archive._owner(store.iam, con, "owner", ws, doc)
    return dict(archived=archived, operation_id="ui-" + operation, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=workspace["security_epoch"], expected_document_epoch=page["access_epoch"])


def change(store, ws, doc="page-e1", *, archived=True, operation="archive-1", **kwargs):
    return page_archive.set_page_archived(store.iam, "owner", ws, doc,
        **request(store, ws, doc, archived=archived, operation=operation), **kwargs)


def create(store, ws, doc, parent="page-e1"):
    current = store.iam.read_document("owner", parent or "page-e1")
    store.iam.create_document("owner", ws, doc, title="한글 " + doc, parent_document_id=parent,
        blocks=[{"id": "body-" + doc, "type": "paragraph", "content": "보존 " + doc}],
        expected_workspace_epoch=current["workspace_epoch"], operation_id="create-" + doc)
    if parent:
        current = store.iam.read_document("owner", parent)
        store.iam.execute_typed_command("owner", parent, command_kind="block.insert", target_id="link-" + doc,
            payload={"type": "child_page", "content": "한글 " + doc, "props": {"page_id": doc}},
            expected_document_sequence=current["document_sequence"], expected_workspace_epoch=current["workspace_epoch"],
            expected_document_epoch=current["document_epoch"], operation_id="link-" + doc)


def links(store, doc):
    return [b["props"]["page_id"] for b in current_body(store.iam, doc)["blocks"] if b["type"] == "child_page"]


def test_ui_page_archive_schema_and_prior_capabilities(environment):
    store, ws = environment
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    caps = store.capabilities()
    assert caps["schema_version"] == migration.TARGET_VERSION
    for name in ("guest_navigation", "collaborative_editing", "live_collaboration", "database_features",
                 "collaborative_files", "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[name], name
    assert store.get_navigation_state("outsider", ws)["favorites"] == []
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    transport._connect(query_only=True).close()
    before = dump(store)
    assert page_archive.list_archived_pages(store.iam, "owner", ws)["items"] == []
    assert dump(store) == before


def test_ui_page_archive_body_links_replay_and_restore(environment):
    store, ws = environment
    create(store, ws, "page-child")
    create(store, ws, "page-grandchild", "page-child")
    before = {doc: store.iam.read_document("owner", doc) for doc in ("page-child", "page-grandchild")}
    req = request(store, ws, "page-child")
    result = page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req)
    assert result["page_ids"] == ["page-child", "page-grandchild"]
    assert links(store, "page-e1") == []
    after = dump(store)
    assert page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req) == result
    assert dump(store) == after
    for doc in before:
        for actor in ("owner", "outsider"):
            with pytest.raises(NotFoundOrDenied):
                store.iam.read_document(actor, doc)
    trash = page_archive.list_archived_pages(store.iam, "owner", ws, query="한글")
    assert {p["id"] for p in trash["items"]} == set(before)
    assert all(not item["can_edit"] for item in trash["items"])
    assert all(item["archived_by_subject_id"] == "owner" for item in trash["items"])
    restored = change(store, ws, "page-child", archived=False, operation="restore-1")
    assert restored["restored_count"] == 2
    assert links(store, "page-e1") == ["page-child"]
    for doc, saved in before.items():
        current = store.iam.read_document("owner", doc)
        assert current["blocks"] == saved["blocks"] and current["revision"] == saved["revision"]
        assert current["document_epoch"] == saved["document_epoch"] + 2
        assert current["document_sequence"] == saved["document_sequence"] + 2
    # An uncertain old response returns the original receipt, without deleting
    # the now-restored page again.
    state = dump(store)
    assert page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req) == result
    assert dump(store) == state
    with pytest.raises(OperationConflict):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **{**req, "expected_document_sequence": 999})


def test_ui_page_archive_prior_trash_and_partial_ancestor_restore(environment):
    store, ws = environment
    create(store, ws, "page-parent")
    for doc in ("page-child-a", "page-child-b", "page-earlier"):
        create(store, ws, doc, "page-parent")
    create(store, ws, "page-leaf", "page-child-a")
    change(store, ws, "page-earlier", operation="earlier")
    change(store, ws, "page-parent", operation="parent")
    result = change(store, ws, "page-leaf", archived=False, operation="restore-leaf")
    assert result["page_ids"] == ["page-child-a", "page-leaf", "page-parent"]
    assert links(store, "page-e1") == ["page-parent"]
    remaining = {p["id"] for p in page_archive.list_archived_pages(store.iam, "owner", ws)["items"]}
    assert remaining == {"page-child-b", "page-earlier"}
    change(store, ws, "page-parent", operation="parent-again")
    result = change(store, ws, "page-parent", archived=False, operation="restore-parent")
    assert result["page_ids"] == ["page-child-a", "page-leaf", "page-parent"]
    for doc in remaining:
        with pytest.raises(NotFoundOrDenied):
            store.iam.read_document("owner", doc)


def test_ui_page_archive_cas_roles_locks_and_rollback(environment):
    store, ws = environment
    create(store, ws, "page-child")
    req = request(store, ws, "page-child")
    before = dump(store)
    for actor in ("outsider", "admin"):
        with pytest.raises(NotFoundOrDenied):
            page_archive.set_page_archived(store.iam, actor, ws, "page-child", **req)
        with pytest.raises(NotFoundOrDenied):
            page_archive.list_archived_pages(store.iam, actor, ws)
    with pytest.raises(DocumentSequenceConflict):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **{**req, "expected_document_sequence": 999})
    with pytest.raises(EpochConflict):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **{**req, "expected_document_epoch": 999})
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox"):
        with pytest.raises(InjectedFailure):
            page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req, fault=fault)
        assert dump(store) == before
    with database(store.db_path) as con:
        con.execute("UPDATE workspace_collaboration_documents SET settings_json=? WHERE id='page-child'", (json.dumps({"locked": True}),))
    with pytest.raises(PermissionError):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req)


def test_ui_page_archive_revalidates_descendants_after_node_preparation(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "page-child")
    req = request(store, ws, "page-child")
    prepare = page_move._prepare_deltas
    def concurrent_edit(*args):
        prepared = prepare(*args)
        create(store, ws, "page-new-descendant", "page-child")
        return prepared
    monkeypatch.setattr(page_move, "_prepare_deltas", concurrent_edit)
    with pytest.raises(DocumentSequenceConflict):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-child", **req)
    assert store.iam.read_document("owner", "page-child")["blocks"]
    assert store.iam.read_document("owner", "page-new-descendant")["blocks"]
    assert page_archive.list_archived_pages(store.iam, "owner", ws)["items"] == []


def test_ui_page_archive_ancestor_fence_and_current_replay_authority(environment):
    store, ws = environment
    create(store, ws, "page-child")
    req = request(store, ws)
    change(store, ws)
    with database(store.db_path) as con:
        # Defensive visibility must also handle an imported inconsistent child
        # flag. A direct owner/guest grant cannot bypass the archived ancestor.
        con.execute("UPDATE workspace_collaboration_documents SET archived=0 WHERE id='page-child'")
    for actor in ("owner", "outsider"):
        with pytest.raises(NotFoundOrDenied):
            store.iam.read_document(actor, "page-child")
    with store.iam._reader() as con:
        epoch = store.iam._workspace(con, ws)["security_epoch"]
    store.iam.set_workspace_member("owner", ws, "admin", role="owner", expected_workspace_epoch=epoch, operation_id="ui-second-owner")
    with store.iam._reader() as con:
        epoch = store.iam._workspace(con, ws)["security_epoch"]
    store.iam.set_workspace_member("admin", ws, "owner", role="member", expected_workspace_epoch=epoch, operation_id="ui-former-owner")
    with pytest.raises(NotFoundOrDenied):
        page_archive.set_page_archived(store.iam, "owner", ws, "page-e1", **req)


def test_ui_page_archive_two_processes_one_commit(environment):
    store, ws = environment
    req = request(store, ws)
    script = r'''
import contextlib, json, sys
from pathlib import Path
with contextlib.redirect_stdout(sys.stderr):
    from code_assist_v1.notion_collaboration.workspace_image_authority import WorkspaceImageAuthority
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.page_archive import set_page_archived
authority = WorkspaceImageAuthority.open(Path(sys.argv[1]), Path(sys.argv[2]), InMemoryIdentityDirectory(["owner"]))
print("READY", flush=True)
sys.stdin.readline()
print(json.dumps(set_page_archived(authority, "owner", sys.argv[3], "page-e1", **json.loads(sys.argv[4])), sort_keys=True), flush=True)
'''
    workers = [subprocess.Popen([sys.executable, "-c", script, str(store.db_path), str(store.user_data_root), ws, json.dumps(req)],
        cwd=str(Path(__file__).resolve().parents[3]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for worker in workers:
            assert worker.stdout.readline().strip() == "READY"
        for worker in workers:
            worker.stdin.write("go\n"); worker.stdin.flush()
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=45)
            assert worker.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert results[0] == results[1]
        with database(store.db_path) as con:
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_outbox WHERE event_type='page.archived'").fetchone()[0] == 1
            assert con.execute("SELECT document_sequence FROM workspace_collaboration_documents WHERE id='page-e1'").fetchone()[0] == req["expected_document_sequence"] + 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill(); worker.wait(timeout=10)


def test_ui_page_archive_offline_migration_rollback_backup_and_existing_trash(guest_environment):
    old, ws = guest_environment
    body = old.iam.read_document("owner", "page-e1")
    long_id = "p" + "x" * 199
    old.iam.create_document("owner", ws, long_id, title="긴 식별자", blocks=[],
        expected_workspace_epoch=body["workspace_epoch"], operation_id="ui-archive-long-id")
    with database(old.db_path) as con:
        con.execute("UPDATE workspace_collaboration_documents SET archived=1,archived_at=123 WHERE id IN (?,?)", ("page-e1", long_id))
    before = dump(old)
    backup = _backup(old.db_path, old.user_data_root / "backups" / "archive-before-2413.db")
    plan = migration.build_plan(old.db_path, old.user_data_root)
    def fault(stage):
        if stage == "before_commit":
            raise RuntimeError("migration rollback probe")
    with pytest.raises(RuntimeError, match="rollback probe"):
        migration.apply_plan(old.db_path, old.user_data_root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True, catalog_quiesced_confirmed=True,
            backup_path=backup, fault_hook=fault)
    assert dump(old) == before
    migration.apply_plan(old.db_path, old.user_data_root, exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup)
    with database(backup) as con:
        assert tuple(con.iterdump()) == before
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)
    with store.iam._reader() as con:
        state = con.execute("SELECT * FROM workspace_collaboration_page_archive_state WHERE document_id='page-e1'").fetchone()
        assert state["batch_id"] == "imported:page-e1" and state["archived_at"] == 123
        assert con.execute("SELECT batch_id FROM workspace_collaboration_page_archive_state WHERE document_id=?", (long_id,)).fetchone()[0] == "imported:" + long_id
    result = change(store, ws, archived=False, operation="restore-imported")
    assert result["restored_count"] == 1
    assert store.iam.read_document("owner", "page-e1")["blocks"] == body["blocks"]
