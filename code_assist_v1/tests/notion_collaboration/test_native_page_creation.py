"""UI-PAGE-CREATE: native registration, current parent links and atomic retries."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1 import notion_workspace_native_page_migration as migration
from code_assist_v1.notion_collaboration import page_creation as creation, page_archive, page_move
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority, DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_workspace_store import WorkspaceRevisionConflict
from code_assist_v1.tests.notion_collaboration.test_page_archive import (
    environment as archive_environment, guest_environment, image_environment, database, dump, request as archive_request,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply, _backup
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, insert_block
from code_assist_v1.tests.notion_collaboration.test_page_move import current_body


@pytest.fixture()
def environment(archive_environment):
    old, ws = archive_environment
    _apply(migration, old.db_path, old.user_data_root, "before-native-2414.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory), ws


def create(store, ws, *, parent="page-e1", operation="native-create", title="새 하위 페이지", **options):
    return creation.create_page(store.iam, options.pop("actor", "owner"), ws, title=title,
        parent_id=parent, base_version=options.pop("base_version", store.get_workspace("owner", ws)["version"]),
        operation_id=operation, **options)


def registry(store):
    with store.iam._reader() as con:
        return [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_native_pages ORDER BY document_id")]


def _create_process(catalog, root, workspace_id, version, ready, start, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    ready.put(True)
    if not start.wait(30):
        results.put({"error": "start barrier timed out"})
        return
    try:
        results.put(creation.create_page(store.iam, "owner", workspace_id, title="동시 생성", parent_id="",
            base_version=version, operation_id="same-process-create"))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_native_page_migration_requires_exact_backup_and_keeps_old_sources(archive_environment):
    store, ws = archive_environment
    before = dump(store)
    with database(store.db_path) as con:
        before_bindings = con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id").fetchall()
    plan = migration.build_plan(store.db_path, store.user_data_root)
    assert dump(store) == before
    backup = _backup(store.db_path, store.user_data_root / "backups" / "native-plan.db")
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(store.db_path, store.user_data_root, exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=False, catalog_quiesced_confirmed=True, backup_path=backup)
    assert dump(store) == before
    for overrides in ({"exact_plan_checksum": "sha256:" + "0" * 64}, {"catalog_quiesced_confirmed": False}):
        options = {"exact_plan_checksum": plan["plan_checksum"], "backup_confirmed": True,
            "catalog_quiesced_confirmed": True, "backup_path": backup, **overrides}
        with pytest.raises(migration.MigrationRefused):
            migration.apply_plan(store.db_path, store.user_data_root, **options)
        assert dump(store) == before
    migration.apply_plan(store.db_path, store.user_data_root, exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True, catalog_quiesced_confirmed=True, backup_path=backup)
    with database(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages").fetchone()[0] == 0
        assert con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id").fetchall() == before_bindings
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    original_rows = lambda lines: [line for line in lines if line.startswith('INSERT INTO "') and not line.startswith('INSERT INTO "workspace_schema_migrations"')]
    assert original_rows(dump(store)) == original_rows(before), "the forward registry migration preserves every existing data row"
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"


def test_ui_native_page_create_current_parent_body_and_retry(environment, monkeypatch):
    store, ws = environment
    current_update(store, ws)
    before = current_body(store.iam, "page-e1")
    version = store.get_workspace("owner", ws)["version"]
    with store.iam._reader() as con:
        old_bindings = [tuple(row) for row in con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id")]
    reader_count = 0
    original_reader, original_prepare = store.iam._reader, page_move._prepare_deltas
    @contextmanager
    def reader():
        nonlocal reader_count
        with original_reader() as con:
            reader_count += 1
            try:
                yield con
            finally:
                reader_count -= 1
    def prepare(*args):
        assert reader_count == 0, "Yjs link preparation cannot retain a database reader"
        return original_prepare(*args)
    monkeypatch.setattr(store.iam, "_reader", reader)
    monkeypatch.setattr(page_move, "_prepare_deltas", prepare)
    result = create(store, ws, base_version=version)
    child = result["page"]["id"]
    assert result["parent_link_saved"] is True and result["replayed"] is False
    assert result["workspace"]["version"] == version + 1
    after = current_body(store.iam, "page-e1")
    links = [block for block in after["blocks"] if block["props"].get("page_id") == child]
    assert len(links) == 1 and links[0]["content"] == "새 하위 페이지"
    assert [block for block in after["blocks"] if block["id"] != links[0]["id"]] == before["blocks"]
    assert after["title"] == before["title"]
    snapshot = store.iam.read_document("owner", child)
    assert snapshot["document_sequence"] == 1 and len(snapshot["blocks"]) == 1
    assert snapshot["blocks"][0]["content"] == ""
    with store.iam._reader() as con:
        assert [tuple(row) for row in con.execute("SELECT * FROM workspace_page_sources ORDER BY source_page_id")] == old_bindings
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_revision_checkpoints WHERE document_id=?", (child,)).fetchone()[0] == 1
    witness = dump(store)
    replayed = create(store, ws, base_version=version)
    assert {**replayed, "replayed": False} == result and dump(store) == witness
    with pytest.raises(OperationConflict):
        create(store, ws, base_version=version, title="다른 제목")
    assert registry(store)[0]["document_id"] == child
    assert [item["id"] for item in creation.native_page_items(store.iam, "owner", ws)] == [child]
    nested = create(store, ws, parent=child, operation="nested-create")
    assert nested["page"]["parent_id"] == child


@pytest.mark.parametrize("fault", ["after_event", "after_audit", "after_outbox"])
def test_ui_native_page_create_atomic_faults_leave_no_parent_or_child_effect(environment, fault):
    store, ws = environment
    before = dump(store)
    with pytest.raises(InjectedFailure):
        create(store, ws, fault=fault)
    assert dump(store) == before and registry(store) == []


def test_ui_native_page_create_root_permissions_and_archive_roundtrip(environment):
    store, ws = environment
    caps = store.capabilities()
    for key in ("collaborative_iam_sharing", "page_workspace_resolution", "collaborative_page_archive", "guest_navigation",
                "live_collaboration", "collaborative_editing", "database_features", "collaborative_files", "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[key] is True, key
    assert store.get_navigation_state("outsider", ws)["favorites"] == []
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    transport._connect(query_only=True).close()
    version = store.get_workspace("owner", ws)["version"]
    with pytest.raises(NotFoundOrDenied):
        create(store, ws, actor="outsider")
    with pytest.raises(WorkspaceRevisionConflict):
        create(store, ws, base_version=version + 1)
    result = create(store, ws, parent="", operation="root-create")
    assert result["parent_link_saved"] is False and result["page"]["parent_id"] == ""
    child = result["page"]["id"]
    assert creation.native_page_items(store.iam, "outsider", ws) == []
    page_archive.set_page_archived(store.iam, "owner", ws, child, **archive_request(store, ws, child))
    assert creation.native_page_items(store.iam, "owner", ws) == []
    with pytest.raises(NotFoundOrDenied):
        create(store, ws, parent="", operation="root-create", base_version=version)
    page_archive.set_page_archived(store.iam, "owner", ws, child,
        **archive_request(store, ws, child, archived=False, operation="restore-native"))
    assert [item["id"] for item in creation.native_page_items(store.iam, "owner", ws)] == [child]


def test_ui_native_page_create_revalidates_parent_after_yjs_preparation(environment, monkeypatch):
    store, ws = environment
    original = page_move._prepare_deltas
    def prepare(*args):
        prepared = original(*args)
        insert_block(store, "page-e1", "concurrent-parent-block")
        return prepared
    monkeypatch.setattr(page_move, "_prepare_deltas", prepare)
    with pytest.raises(DocumentSequenceConflict):
        create(store, ws)
    assert registry(store) == []
    assert any(block["id"] == "concurrent-parent-block" for block in store.iam.read_document("owner", "page-e1")["blocks"])


def test_ui_native_page_create_old_schema_fails_without_legacy_fallback(archive_environment):
    store, ws = archive_environment
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws)
    assert dump(store) == before


def test_ui_native_page_create_lock_revocation_and_exact_marker(environment, monkeypatch):
    from code_assist_v1.notion_collaboration.page_settings import update_page_settings
    store, ws = environment
    page = store.iam.read_document("owner", "page-e1")
    update_page_settings(store.iam, "owner", ws, "page-e1", updates={"locked": True},
        expected_document_sequence=page["document_sequence"], expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id="native-lock-parent")
    before = dump(store)
    with pytest.raises(PermissionError):
        create(store, ws)
    assert dump(store) == before
    # An exact additive marker is required; merely having the new table is insufficient.
    with database(store.db_path) as con:
        con.execute("UPDATE workspace_schema_migrations SET checksum=? WHERE version=?", ("sha256:" + "0"*64, migration.TARGET_VERSION))
    try:
        with pytest.raises(AuthorityUnavailable):
            creation.create_page(store.iam, "owner", ws, title="denied", parent_id="", base_version=1, operation_id="wrong-marker-create")
    finally:
        with database(store.db_path) as con:
            con.execute("UPDATE workspace_schema_migrations SET checksum=? WHERE version=?", (migration.TARGET_CHECKSUM, migration.TARGET_VERSION))
    assert dump(store) == before
    original = page_move._prepare_deltas
    def revoke_after_prepare(*args):
        prepared = original(*args)
        monkeypatch.setattr(store.iam.identity_directory, "is_active_human", lambda subject: False)
        return prepared
    monkeypatch.setattr(page_move, "_prepare_deltas", revoke_after_prepare)
    with pytest.raises(NotFoundOrDenied):
        create(store, ws, parent="", operation="inactive-after-prepare")
    assert registry(store) == [] and dump(store) == before


def test_ui_native_page_create_cross_process_same_operation(environment):
    store, ws = environment
    version = store.get_workspace("owner", ws)["version"]
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    workers = [ctx.Process(target=_create_process,
        args=(store.db_path, store.user_data_root, ws, version, ready, start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert ready.get(timeout=30) and ready.get(timeout=30)
        start.set()
        receipts = [results.get(timeout=30), results.get(timeout=30)]
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
        assert all("error" not in receipt for receipt in receipts), receipts
        assert sorted(receipt["replayed"] for receipt in receipts) == [False, True]
        assert {**receipts[0], "replayed": False} == {**receipts[1], "replayed": False}
        assert len(registry(store)) == 1
        assert store.get_workspace("owner", ws)["version"] == version + 1
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        ready.close(); results.close()
