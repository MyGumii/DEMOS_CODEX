"""UI-NATIVE-NAV: migrated personal state and registered native page discovery."""
import json
import multiprocessing

import pytest
from flask import Flask

from code_assist_v1 import notion_member_navigation as navigation, notion_workspace_store as nws
from code_assist_v1 import notion_workspace_member_navigation_migration as migration
from code_assist_v1 import notion_store as legacy, notion_workspace_pages as pages, routes_notion_workspace as routes
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore, ensure_collaborative_document
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import (
    environment as native_environment, archive_environment, guest_environment, image_environment,
    create, database, dump, _apply, _backup,
)
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import preferences, favorite
from code_assist_v1.tests.notion_collaboration.test_page_archive import change as archive
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant


def upgrade(old):
    _apply(migration, old.db_path, old.user_data_root, "before-member-navigation.db")
    return CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=old.identity_directory)


@pytest.fixture()
def environment(native_environment):
    old, ws = native_environment
    return upgrade(old), ws


def legacy_page(store, ws, operation):
    # Exercise the actual pre-cutover creation path with a real isolated source
    # and proof. Never manufacture a binding for a native page.
    with legacy.bind_user_data_root(store.user_data_root):
        result = pages.create_workspace_page(store, "owner", ws, title=operation, parent_id="",
            base_version=store.get_workspace("owner", ws)["version"], operation_id=operation)
        detail = legacy.get_page("owner", result["page"]["id"])
        ensure_collaborative_document(store, "owner", ws, detail["page"], detail["blocks"], source_operation_id=operation)
    return result["page"]["id"]


def test_ui_native_nav_defaults_and_guest_compatibility(environment):
    store, ws = environment
    before = dump(store)
    state = store.get_navigation_state("owner", ws)
    assert state["preference_version"] == state["favorite_version"] == 0
    assert state["sidebar"]["width_px"] == nws.UNCONFIGURED_SIDEBAR_WIDTH_PX
    assert state["favorites"] == [] and state["sections"] == nws._default_navigation_sections()
    assert [item["section"] for item in state["sections"]] == [
        "favorites", "private", "teamspaces", "shared", "recents"]
    assert not next(item for item in state["sections"] if item["section"] == "recents")["hidden"]
    guest = store.get_navigation_state("outsider", ws)
    assert guest["favorites"] == [] and guest["preference_version"] == 0
    assert guest["sidebar"]["width_px"] == nws.UNCONFIGURED_SIDEBAR_WIDTH_PX
    assert dump(store) == before, "default reads do not persist settings"
    caps = store.capabilities()
    assert caps["schema_version"] == migration.TARGET_VERSION
    for name in ("guest_navigation", "collaborative_page_archive", "live_collaboration", "collaborative_editing",
                 "database_features", "collaborative_files", "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[name], name
    CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)._connect(query_only=True).close()
    store.replace_sidebar_preferences("outsider", ws, **preferences(sidebar_width_px=240))
    assert store.get_navigation_state("outsider", ws)["sidebar"]["width_px"] == 240
    assert store.get_navigation_state("owner", ws) == state
    with database(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_member_navigation_states").fetchone()[0] == 0


def test_ui_native_nav_migration_preserves_settings_order_and_old_receipts(native_environment):
    old, ws = native_environment
    first, second = legacy_page(old, ws, "old-first"), legacy_page(old, ws, "old-second")
    sections = list(reversed(nws._default_navigation_sections()))
    for i, section in enumerate(sections):
        section.update(display_order=i, hidden=i == 2, collapsed=i == 0, item_limit=6 + i)
    pref_args = preferences(sections=sections, sidebar_width_px=411)
    pref_receipt = old.replace_sidebar_preferences("owner", ws, **pref_args)
    args = favorite(source_page_id=second)
    first_receipt = old.add_page_favorite("owner", ws, **args)
    old.add_page_favorite("owner", ws, **favorite(source_page_id=first, expected_favorite_version=1, operation_id="old-second-favorite"))
    before_state, before_rows = old.get_navigation_state("owner", ws), dump(old)
    store = upgrade(old)
    assert store.get_navigation_state("owner", ws) == before_state
    assert store.replace_sidebar_preferences("owner", ws, **pref_args) == pref_receipt
    assert store.add_page_favorite("owner", ws, **args) == first_receipt
    child = create(store, ws, parent=first)["page"]["id"]
    store.add_page_favorite("owner", ws, **favorite(source_page_id=child, expected_favorite_version=2, operation_id="native-favorite"))
    state = store.get_navigation_state("owner", ws)
    assert [item["source_page_id"] for item in state["favorites"]] == [second, first, child]
    assert state["favorite_version"] == 3 and state["preference_version"] == 1
    assert state["sidebar"] == before_state["sidebar"] and state["sections"] == sections
    # New writes do not touch the old navigation tables, source bindings or receipts.
    historical = ('workspace_navigation_states', 'workspace_sidebar_section_preferences', 'workspace_page_favorites',
        'workspace_page_sources', 'workspace_operations', 'workspace_audit_events', 'workspace_outbox')
    only_old = lambda rows: [line for line in rows if any(line.startswith(f'INSERT INTO "{table}"') for table in historical)]
    assert only_old(dump(store)) == only_old(before_rows)
    with pytest.raises(nws.WorkspaceOperationConflict):
        store.add_page_favorite("owner", ws, **{**args, "source_page_id": first})
    with database(store.db_path) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"


def test_ui_native_nav_list_favorites_and_archive_restore(environment):
    store, ws = environment
    root = create(store, ws, parent="", operation="native-root")["page"]["id"]
    child = create(store, ws, parent=root, operation="native-child", title="목록에 나타나는 하위 페이지")["page"]["id"]
    other = create(store, ws, parent="", operation="native-other")["page"]["id"]
    listing = pages.list_workspace_pages(store, "owner", ws)
    assert not listing["degraded"] and {p["id"] for p in listing["items"]} == {root, child, other}
    item = next(p for p in listing["items"] if p["id"] == child)
    assert item["parent_id"] == root and item["title"] == "목록에 나타나는 하위 페이지"
    assert item["placement"]["scope"] == "private"
    assert pages.list_workspace_pages(store, "outsider", ws)["items"] == []
    first = store.add_page_favorite("owner", ws, **favorite(source_page_id=child))
    store.add_page_favorite("owner", ws, **favorite(source_page_id=other, expected_favorite_version=1, operation_id="other-favorite"))
    archive(store, ws, child)
    state = store.get_navigation_state("owner", ws)
    assert state["favorites"] == [{"source_owner_subject_id": "owner", "source_page_id": other, "position": 0}]
    assert child not in {p["id"] for p in pages.list_workspace_pages(store, "owner", ws)["items"]}
    with pytest.raises(NotFoundOrDenied):
        store.add_page_favorite("owner", ws, **favorite(source_page_id=child))
    archive(store, ws, child, archived=False, operation="restore")
    assert [p["source_page_id"] for p in store.get_navigation_state("owner", ws)["favorites"]] == [child, other]
    assert store.add_page_favorite("owner", ws, **favorite(source_page_id=child)) == first
    _grant(store.iam, child, "outsider", "native-child-view")
    guest_items = pages.list_workspace_pages(store, "outsider", ws)["items"]
    assert len(guest_items) == 1 and guest_items[0]["id"] == child and guest_items[0]["parent_id"] == ""
    store.add_page_favorite("outsider", ws, **favorite(source_page_id=child))
    assert store.get_navigation_state("outsider", ws)["favorites"][0]["source_page_id"] == child


def test_ui_native_database_favorite_is_independent_and_authorized(environment):
    store, ws = environment
    page_id = create(store, ws, parent="", operation="db-favorite-parent")["page"]["id"]
    source = store.iam.read_document("owner", page_id)
    block_id = "db-favorite-source"
    store.iam.execute_typed_command("owner", page_id, command_kind="block.insert",
        target_id=block_id, payload={"type": "table", "content": "Favorite DB", "props": {},
                                     "after_block_id": None},
        expected_document_sequence=source["document_sequence"],
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"], operation_id="insert-db-favorite-source")
    target = {"source_owner_subject_id": "owner", "source_page_id": page_id,
              "database_block_id": block_id, "expected_favorite_version": 0,
              "operation_id": "native-db-favorite"}
    added = store.add_database_favorite("owner", ws, **target)
    assert added["favorite"] == {key: target[key] for key in
                                 ("source_owner_subject_id", "source_page_id", "database_block_id")}
    assert store.add_database_favorite("owner", ws, **target) == added
    page_added = store.add_page_favorite("owner", ws, **favorite(
        source_page_id=page_id, expected_favorite_version=1, operation_id="native-page-favorite"))
    assert page_added["changed"]
    assert store.get_navigation_state("owner", ws)["favorites"] == [
        {**added["favorite"], "position": 0},
        {"source_owner_subject_id": "owner", "source_page_id": page_id, "position": 1},
    ]
    with pytest.raises(NotFoundOrDenied):
        store.add_database_favorite("owner", ws, **{**target, "database_block_id": "missing-db",
            "expected_favorite_version": 2, "operation_id": "invalid-db-favorite"})
    removed = store.remove_database_favorite("owner", ws, **{**target,
        "expected_favorite_version": 2, "operation_id": "remove-native-db-favorite"})
    assert removed["changed"] and removed["favorite_version"] == 3
    assert store.get_navigation_state("owner", ws)["favorites"] == [
        {"source_owner_subject_id": "owner", "source_page_id": page_id, "position": 0},
    ]


def test_ui_native_nav_exact_retry_cas_noop_and_isolation(environment):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    request = favorite(source_page_id=child)
    first = store.add_page_favorite("owner", ws, **request)
    before = dump(store)
    assert store.add_page_favorite("owner", ws, **request) == first
    assert dump(store) == before
    with pytest.raises(OperationConflict):
        store.add_page_favorite("owner", ws, **{**request, "expected_favorite_version": 1})
    with pytest.raises(nws.WorkspaceOperationConflict):
        store.remove_page_favorite("owner", ws, **request)
    with pytest.raises(nws.WorkspaceRevisionConflict):
        store.add_page_favorite("owner", ws, **{**request, "operation_id": "stale"})
    noop = store.add_page_favorite("owner", ws, **{**request, "expected_favorite_version": 1, "operation_id": "noop"})
    assert noop["changed"] is False and noop["favorite_version"] == 1
    store.replace_sidebar_preferences("owner", ws, **preferences())
    remove = store.remove_page_favorite("owner", ws, **{**request, "expected_favorite_version": 1, "operation_id": "remove"})
    assert remove["changed"] and remove["favorite_version"] == 2 and remove["preference_version"] == 1
    reopened = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root, identity_directory=store.identity_directory)
    assert reopened.get_navigation_state("owner", ws)["sidebar"] == {"width_px": 320, "collapsed": True}
    assert reopened.get_navigation_state("owner", ws)["favorites"] == []
    assert store.get_navigation_state("outsider", ws)["preference_version"] == 0


@pytest.mark.parametrize("fault", ["after_event", "after_audit", "after_outbox"])
def test_ui_native_nav_atomic_failure_and_private_events(environment, fault):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    before = dump(store)
    with pytest.raises(InjectedFailure):
        store.add_page_favorite("owner", ws, **favorite(source_page_id=child), fault=fault)
    assert dump(store) == before
    result = store.add_page_favorite("owner", ws, **favorite(source_page_id=child))
    with database(store.db_path) as con:
        for table, column in (("workspace_collaboration_events", "event_type"), ("workspace_collaboration_audit_events", "action"),
                              ("workspace_collaboration_outbox", "event_type")):
            payloads = con.execute(f"SELECT payload_json FROM {table} WHERE {column}=?", (nws.NAVIGATION_FAVORITE_ADDED_ACTION,)).fetchall()
            assert len(payloads) == 1
            assert json.loads(payloads[0][0]) == {"subject_id": "owner", "preference_version": 0, "favorite_version": 1}
    assert result["favorite_version"] == 1


def test_ui_native_nav_rejects_unregistered_wrong_owner_and_inactive_identity(environment):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    before = dump(store)
    for target in (favorite(), favorite(source_page_id=child, source_owner_subject_id="admin")):
        with pytest.raises(NotFoundOrDenied):
            store.add_page_favorite("owner", ws, **target)
    disabled = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=InMemoryIdentityDirectory(["outsider"]))
    with pytest.raises(NotFoundOrDenied):
        disabled.get_navigation_state("owner", ws)
    with pytest.raises(NotFoundOrDenied):
        disabled.add_page_favorite("owner", ws, **favorite(source_page_id=child))
    assert dump(store) == before


def test_ui_native_nav_http_list_and_save(environment, monkeypatch):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    app = Flask(__name__); app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    base = f"/api/notion/workspaces/{ws}"
    listing = client.get(base + "/pages")
    assert listing.status_code == 200 and child in {item["id"] for item in listing.json["items"]}
    args = favorite(source_page_id=child)
    first = client.put(base + "/navigation/favorites", json=args)
    assert first.status_code == 200 and first.json["favorite_version"] == 1
    assert client.put(base + "/navigation/favorites", json=args).json == first.json
    assert client.get(base + "/navigation").json["favorites"][0]["source_page_id"] == child


def test_ui_native_nav_migration_plan_backup_rollback_and_old_rows(native_environment):
    store, ws = native_environment
    store.replace_sidebar_preferences("owner", ws, **preferences())
    before = dump(store)
    plan = migration.build_plan(store.db_path, store.user_data_root)
    assert dump(store) == before
    backup = _backup(store.db_path, store.user_data_root / "backups" / "member-exact.db")
    options = dict(exact_plan_checksum=plan["plan_checksum"], backup_confirmed=True,
        catalog_quiesced_confirmed=True, backup_path=backup)
    for changed in ({"backup_confirmed": False}, {"catalog_quiesced_confirmed": False},
                    {"exact_plan_checksum": "sha256:" + "0" * 64}):
        with pytest.raises(migration.MigrationRefused):
            migration.apply_plan(store.db_path, store.user_data_root, **{**options, **changed})
        assert dump(store) == before
    def fail(stage):
        if stage == "before_commit":
            raise RuntimeError("member migration fault")
    with pytest.raises(RuntimeError, match="member migration fault"):
        migration.apply_plan(store.db_path, store.user_data_root, **options, fault_hook=fail)
    assert dump(store) == before
    migration.apply_plan(store.db_path, store.user_data_root, **options)
    unchanged = lambda rows: [line for line in rows if line.startswith('INSERT INTO "')
        and not line.startswith(('INSERT INTO "workspace_schema_migrations"', 'INSERT INTO "workspace_member_navigation_states"'))]
    assert unchanged(dump(store)) == unchanged(before)


def test_ui_native_nav_member_transition_and_revocation(environment):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    _grant(store.iam, child, "outsider", "native-member-view")
    guest = store.replace_sidebar_preferences("outsider", ws, **preferences())
    def epoch():
        return store.get_workspace("owner", ws)["security_epoch"]
    store.iam.set_workspace_member("owner", ws, "outsider", role="member",
        expected_workspace_epoch=epoch(), operation_id="guest-becomes-member")
    state = store.get_navigation_state("outsider", ws)
    assert state["preference_version"] == 0 and state["sidebar"]["width_px"] == nws.UNCONFIGURED_SIDEBAR_WIDTH_PX
    member = store.add_page_favorite("outsider", ws, **favorite(source_page_id=child, operation_id="member-native-favorite"))
    assert member["favorite_version"] == 1
    store.iam.revoke_workspace_member("owner", ws, "outsider", expected_workspace_epoch=epoch(), operation_id="native-member-revoke")
    with pytest.raises((PermissionError, FileNotFoundError)):
        navigation.change_favorite(store, "outsider", ws, **favorite(source_page_id=child, operation_id="member-native-favorite"))
    with pytest.raises((PermissionError, FileNotFoundError)):
        store.get_navigation_state("outsider", ws)
    # Membership revocation also revokes guest access. An explicit subsequent
    # grant restores the guest's settings, never the former member's favorites.
    _grant(store.iam, child, "outsider", "native-guest-return")
    assert store.get_navigation_state("outsider", ws)["preference_version"] == guest["preference_version"]
    assert store.get_navigation_state("outsider", ws)["favorites"] == []


def _favorite_process(catalog, root, ws, page_id, ready, start, results):
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    ready.put(True)
    if not start.wait(30):
        results.put({"error": "start barrier timed out"})
        return
    try:
        results.put(store.add_page_favorite("owner", ws, **favorite(source_page_id=page_id, operation_id="two-process-favorite")))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_native_nav_two_processes_commit_one_favorite(environment):
    store, ws = environment
    child = create(store, ws, parent="")["page"]["id"]
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=_favorite_process,
        args=(str(store.db_path), str(store.user_data_root), ws, child, ready, start, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=45)
        start.set()
        values = [results.get(timeout=45) for _ in processes]
        assert all("error" not in value for value in values), values
        assert values[0] == values[1]
        for process in processes:
            process.join(30)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate(); process.join(10)
        ready.close(); results.close()
    state = store.get_navigation_state("owner", ws)
    assert state["favorite_version"] == 1 and len(state["favorites"]) == 1
    with database(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=?",
            (nws.NAVIGATION_FAVORITE_ADDED_ACTION,)).fetchone()[0] == 1
