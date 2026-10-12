"""UI-GUEST-NAV: current guest authority, exact retries and durable personal state."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

from code_assist_v1 import notion_guest_navigation as guest, notion_workspace_store as nws
from code_assist_v1 import notion_workspace_guest_navigation_migration as migration
from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.canonical_authority import OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_workspace_images import environment as image_environment, dump
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant, IDENTITIES


@pytest.fixture()
def environment(image_environment):
    old, ws = image_environment
    _apply(migration, old.db_path, old.user_data_root, "before-2412.db")
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
        identity_directory=InMemoryIdentityDirectory((*IDENTITIES, "admin")))
    _grant(store.iam, "page-e1", "outsider", "guest-nav-view")
    return store, ws


def preferences(**changes):
    return {"expected_preference_version": 0, "sidebar_width_px": 320, "sidebar_collapsed": True,
        "sections": nws._default_navigation_sections(), "operation_id": "guest-prefs", **changes}


def favorite(**changes):
    return {"source_owner_subject_id": "owner", "source_page_id": "page-e1",
        "expected_favorite_version": 0, "operation_id": "guest-favorite", **changes}


def revoke(store, ws):
    page = store.iam.read_document("owner", "page-e1")
    store.iam.revoke_page_grant("owner", "page-e1", "subject", "outsider",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
        operation_id="guest-nav-revoke")


def test_ui_guest_nav_schema_reads_and_member_compatibility(environment):
    store, ws = environment
    before = dump(store)
    state = store.get_navigation_state("outsider", ws)
    assert state["preference_version"] == state["favorite_version"] == 0
    assert state["sidebar"]["width_px"] == nws.UNCONFIGURED_SIDEBAR_WIDTH_PX
    assert state["favorites"] == [] and not state["sidebar"]["collapsed"]
    assert dump(store) == before
    caps = store.capabilities()
    for name in ("guest_navigation", "live_collaboration", "collaborative_editing", "database_features",
                 "collaborative_files", "collaborative_history", "collaborative_search", "workspace_image"):
        assert caps[name] is True, name
    assert caps["schema_version"] == migration.TARGET_VERSION
    assert migration.build_plan(store.db_path, store.user_data_root)["state"] == "already_current"
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    transport._connect(query_only=True).close()
    owner = store.get_navigation_state("owner", ws)
    store.replace_sidebar_preferences("owner", ws, **preferences(expected_preference_version=owner["preference_version"]))
    assert store.get_navigation_state("outsider", ws) == state
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_guest_navigation_states").fetchone()[0] == 0


def test_ui_guest_nav_exact_replay_cas_and_isolation(environment):
    store, ws = environment
    page = store.iam.read_document("owner", "page-e1")
    first = store.replace_sidebar_preferences("outsider", ws, **preferences())
    after = dump(store)
    assert store.replace_sidebar_preferences("outsider", ws, **preferences()) == first
    assert dump(store) == after
    with pytest.raises(OperationConflict):
        store.replace_sidebar_preferences("outsider", ws, **preferences(sidebar_width_px=400))
    with pytest.raises(nws.WorkspaceRevisionConflict):
        store.replace_sidebar_preferences("outsider", ws, **preferences(operation_id="stale"))
    reopened = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=InMemoryIdentityDirectory((*IDENTITIES, "admin")))
    assert reopened.get_navigation_state("outsider", ws)["sidebar"] == {"width_px": 320, "collapsed": True}
    assert reopened.get_navigation_state("owner", ws)["preference_version"] == 0
    assert store.iam.read_document("owner", "page-e1") == page
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_memberships WHERE subject_id='outsider' AND revoked_at IS NULL").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_outbox WHERE event_type='guest.navigation.preferences'").fetchone()[0] == 1
    revoke(store, ws)
    with pytest.raises((PermissionError, FileNotFoundError)):
        store.replace_sidebar_preferences("outsider", ws, **preferences())


def test_ui_guest_nav_favorites_visibility_order_and_replay(environment):
    store, ws = environment
    result = store.add_page_favorite("outsider", ws, **favorite())
    assert result["favorite_version"] == 1
    after = dump(store)
    assert store.add_page_favorite("outsider", ws, **favorite()) == result
    assert dump(store) == after
    assert store.get_navigation_state("outsider", ws)["favorites"] == [
        {"source_owner_subject_id": "owner", "source_page_id": "page-e1", "position": 0}]
    with pytest.raises((PermissionError, FileNotFoundError)):
        store.add_page_favorite("outsider", ws, **favorite(source_owner_subject_id="admin", operation_id="wrong-owner", expected_favorite_version=1))
    removed = store.remove_page_favorite("outsider", ws, **favorite(expected_favorite_version=1, operation_id="remove"))
    assert removed["favorite_version"] == 2 and store.get_navigation_state("outsider", ws)["favorites"] == []
    store.add_page_favorite("outsider", ws, **favorite(expected_favorite_version=2, operation_id="again"))
    current = store.iam.read_document("owner", "page-e1")
    store.iam.create_document("owner", ws, "page-guest-second", title="Other shared page", blocks=[],
        expected_workspace_epoch=current["workspace_epoch"], operation_id="guest-second-page")
    _grant(store.iam, "page-guest-second", "outsider", "guest-second-grant")
    store.add_page_favorite("outsider", ws, **favorite(source_page_id="page-guest-second",
        expected_favorite_version=3, operation_id="second-favorite"))
    revoke(store, ws)
    assert store.get_navigation_state("outsider", ws)["favorites"] == [
        {"source_owner_subject_id": "owner", "source_page_id": "page-guest-second", "position": 0}]
    with pytest.raises((PermissionError, FileNotFoundError)):
        store.add_page_favorite("outsider", ws, **favorite())


def test_ui_guest_database_favorite_disappears_when_page_access_is_revoked(environment):
    store, ws = environment
    source = store.iam.read_document("owner", "page-e1")
    block_id = "guest-db-favorite-source"
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id=block_id, payload={"type": "table", "content": "Guest favorite DB", "props": {},
                                     "after_block_id": None},
        expected_document_sequence=source["document_sequence"],
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"], operation_id="insert-guest-db-favorite-source")
    target = favorite(database_block_id=block_id, operation_id="guest-db-favorite")
    added = store.add_database_favorite("outsider", ws, **target)
    assert added["favorite"]["database_block_id"] == block_id
    assert store.add_database_favorite("outsider", ws, **target) == added
    assert store.get_navigation_state("outsider", ws)["favorites"] == [
        {**added["favorite"], "position": 0}]
    latest = store.iam.read_document("owner", "page-e1")
    store.iam.create_document("owner", ws, "page-guest-db-visible", title="Other shared page", blocks=[],
        expected_workspace_epoch=latest["workspace_epoch"], operation_id="guest-db-second-page")
    _grant(store.iam, "page-guest-db-visible", "outsider", "guest-db-second-grant")
    revoke(store, ws)
    assert store.get_navigation_state("outsider", ws)["favorites"] == []
    with pytest.raises((PermissionError, FileNotFoundError)):
        store.add_database_favorite("outsider", ws, **{**target,
            "expected_favorite_version": 1, "operation_id": "guest-db-favorite-after-revoke"})


def test_ui_guest_nav_outbox_failure_rolls_back_everything(environment):
    store, ws = environment
    with sqlite3.connect(store.db_path) as con:
        con.execute("CREATE TRIGGER guest_nav_fault BEFORE INSERT ON workspace_collaboration_outbox "
                    "WHEN NEW.event_type='guest.navigation.preferences' BEGIN SELECT RAISE(ABORT,'guest fault'); END")
    before = dump(store)
    with pytest.raises(sqlite3.IntegrityError, match="guest fault"):
        store.replace_sidebar_preferences("outsider", ws, **preferences())
    assert dump(store) == before
    with sqlite3.connect(store.db_path) as con:
        con.execute("DROP TRIGGER guest_nav_fault")


def test_ui_guest_nav_validation_and_inactive_identity(environment):
    store, ws = environment
    before = dump(store)
    for changes in ({"sidebar_width_px": True}, {"sidebar_width_px": 601}, {"sidebar_collapsed": 1},
                    {"expected_preference_version": True}, {"sections": []}):
        with pytest.raises(ValueError):
            store.replace_sidebar_preferences("outsider", ws, **preferences(**changes))
    assert dump(store) == before
    disabled = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=InMemoryIdentityDirectory([x for x in IDENTITIES if x != "outsider"]))
    with pytest.raises((PermissionError, FileNotFoundError)):
        disabled.get_navigation_state("outsider", ws)
    with pytest.raises((PermissionError, FileNotFoundError)):
        disabled.replace_sidebar_preferences("outsider", ws, **preferences())


def test_ui_guest_nav_http_actor_boundary_and_conflict(environment, monkeypatch):
    store, ws = environment
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "outsider")
    app = Flask(__name__); app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    base = f"/api/notion/workspaces/{ws}/navigation"
    assert client.get(base).json["subject_id"] == "outsider"
    saved = client.patch(base + "/preferences", json=preferences())
    assert saved.status_code == 200, saved.json
    assert client.patch(base + "/preferences", json=preferences()).json == saved.json
    assert client.patch(base + "/preferences", json=preferences(operation_id="http-stale")).status_code == 409
    assert client.patch(base + "/preferences", json=preferences(subject_id="owner")).status_code == 400
    assert client.patch(base + "/preferences", json=preferences(sidebar_collapsed=1)).status_code == 400
    assert client.put(base + "/favorites", json=favorite()).status_code == 200
    assert client.get(base).json["favorites"][0]["source_page_id"] == "page-e1"
    assert client.delete(base + "/favorites", json=favorite(expected_favorite_version=1, operation_id="http-remove")).status_code == 200
    assert client.get(base.replace(ws, "ws_" + "b" * 32)).status_code == 404
    revoke(store, ws)
    assert client.get(base).status_code == 404
    assert client.patch(base + "/preferences", json=preferences()).status_code == 404


def test_ui_guest_nav_two_processes_commit_one_operation(environment):
    store, ws = environment
    script = '''import contextlib,json,sys
with contextlib.redirect_stdout(sys.stderr):
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    s=CollaborativeWorkspaceStore(sys.argv[1],user_data_root=sys.argv[2],identity_directory=InMemoryIdentityDirectory(["owner","outsider"]))
print("READY",flush=True)
sys.stdin.readline()
print(json.dumps(s.replace_sidebar_preferences("outsider",sys.argv[3],**json.loads(sys.argv[4]))),flush=True)
'''
    processes = [subprocess.Popen([sys.executable, "-c", script, str(store.db_path), str(store.user_data_root), ws, json.dumps(preferences())],
        cwd=Path(__file__).resolve().parents[3], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True) for _ in range(2)]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "READY"
        for process in processes:
            process.stdin.write("go\n"); process.stdin.flush()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert results[0] == results[1]
        assert store.get_navigation_state("outsider", ws)["preference_version"] == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.communicate()
