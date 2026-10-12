"""UI-TEAMSPACE-SETTINGS: target isolation, current roles, receipts and rollback."""
import multiprocessing
import sqlite3

from flask import Flask
import pytest

from code_assist_v1 import notion_workspace_store as nws, routes_notion_workspace as routes
from code_assist_v1.notion_teamspace_settings import read_teamspace, rename_teamspace
from code_assist_v1.tests.notion_collaboration.test_workspace_images import environment as collaborative_environment


@pytest.fixture()
def team(tmp_path):
    store = nws.WorkspaceStore(nws.catalog_path_for_user_data_root(tmp_path / "synthetic"))
    ws = store.create_workspace("owner", name="Synthetic teams", operation_id="create")["workspace"]["id"]
    target = store.create_teamspace("owner", ws, name="First", visibility="closed", base_version=1, operation_id="team")["teamspace"]["id"]
    return store, ws, target


def version(store, ws):
    return store.get_workspace("owner", ws)["version"]


def rename(store, ws, target, name="Renamed", operation="rename-team", actor="owner", base=None):
    return rename_teamspace(store, actor, ws, target, name=name, base_version=base or version(store, ws), operation_id=operation)


def dump(store):
    with sqlite3.connect(f"file:{store.db_path}?mode=ro", uri=True) as con:
        return "\n".join(con.iterdump())


def test_ui_teamspace_settings_exact_target_metadata_only_replay_and_rollback(team, monkeypatch):
    store, ws, target = team
    other = store.create_teamspace("owner", ws, name="Other", visibility="private", base_version=version(store, ws), operation_id="other")["teamspace"]["id"]
    before = read_teamspace(store, "owner", ws, target)
    security = store.get_workspace("owner", ws)["security_epoch"]
    first = rename(store, ws, target, base=before["workspace_version"])
    current = read_teamspace(store, "owner", ws, target)
    assert current["teamspace"]["name"] == "Renamed"
    assert current["teamspace"]["version"] == before["teamspace"]["version"] + 1
    assert current["members"] == before["members"]
    assert current["teamspace"]["visibility"] == "closed"
    assert read_teamspace(store, "owner", ws, other)["teamspace"]["name"] == "Other"
    assert store.get_workspace("owner", ws)["security_epoch"] == security
    rename(store, ws, target, name="Later", operation="later")
    snapshot = dump(store)
    assert rename(store, ws, target, base=before["workspace_version"]) == first
    assert dump(store) == snapshot
    with pytest.raises(nws.WorkspaceOperationConflict):
        rename(store, ws, target, name="Different", base=before["workspace_version"])
    with pytest.raises(nws.WorkspaceRevisionConflict):
        rename(store, ws, target, operation="stale", base=before["workspace_version"])
    with pytest.raises(ValueError, match="already exists"):
        rename(store, ws, target, name="OTHER", operation="duplicate")
    assert dump(store) == snapshot
    monkeypatch.setattr(store, "_bump_workspace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError, match="injected"):
        rename(store, ws, target, operation="failed-atomic")
    assert dump(store) == snapshot
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_outbox WHERE event_type='workspace.teamspace_renamed'").fetchone()[0] == 2


def test_ui_teamspace_settings_roles_revocation_cross_workspace_and_validation(team):
    store, ws, target = team
    for subject, role in [("member", "member"), ("lead", "member"), ("admin", "membership_admin"), ("unjoined", "member")]:
        store.set_member("owner", ws, subject, role=role, base_version=version(store, ws), operation_id="add-"+subject)
    for subject, role in [("member", "member"), ("lead", "owner")]:
        store.set_teamspace_member("owner", ws, target, subject, role=role, base_version=version(store, ws), operation_id="join-"+subject)
    assert not read_teamspace(store, "member", ws, target)["capabilities"]["rename"]
    assert {m["subject_id"] for m in read_teamspace(store, "member", ws, target)["members"]} == {"member", "owner", "lead"}
    with pytest.raises(PermissionError):
        rename(store, ws, target, actor="member")
    for actor in ["unjoined", "outsider"]:
        with pytest.raises(FileNotFoundError):
            read_teamspace(store, actor, ws, target)
    second = store.create_workspace("owner", name="Second", operation_id="second")["workspace"]["id"]
    with pytest.raises(FileNotFoundError):
        read_teamspace(store, "owner", second, target)
    base = version(store, ws)
    rename(store, ws, target, actor="lead", base=base)
    rename(store, ws, target, actor="admin", operation="admin-rename")
    store.remove_teamspace_member("owner", ws, target, "lead", base_version=version(store, ws), operation_id="revoke-lead")
    with pytest.raises(FileNotFoundError):
        rename(store, ws, target, actor="lead", base=base)
    store.revoke_member("owner", ws, "member", base_version=version(store, ws), operation_id="revoke-member")
    assert "member" not in [m["subject_id"] for m in read_teamspace(store, "owner", ws, target)["members"]]
    for value in [None, 1, "", "  ", "x"*(nws.MAX_NAME_CHARS+1)]:
        with pytest.raises(ValueError):
            rename(store, ws, target, name=value, operation="invalid")


def test_ui_teamspace_settings_http_exact_contract_and_no_store(team, monkeypatch):
    store, ws, target = team
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    app = Flask(__name__); app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client(); path = f"/api/notion/workspaces/{ws}/teamspaces/{target}"
    response = client.get(path)
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    request = {"name":"HTTP name", "base_version":version(store, ws), "operation_id":"http-rename"}
    first = client.patch(path, json=request)
    assert first.status_code == 200
    assert client.patch(path, json=request).json == first.json
    for field in ["visibility", "members", "unknown"]:
        assert client.patch(path, json={**request, field:[]}).status_code == 400
    snapshot = dump(store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "outsider")
    assert client.get(path).status_code == client.patch(path, json=request).status_code == 404
    assert dump(store) == snapshot


def _rename_worker(path, ws, target, base, start, result):
    store = nws.WorkspaceStore(path)
    start.wait(20)
    try:
        result.put(rename(store, ws, target, base=base))
    except Exception as error:
        result.put({"error": repr(error)})


def test_ui_teamspace_settings_collaborative_current_identity(collaborative_environment):
    store, ws = collaborative_environment
    target = store.create_teamspace("owner", ws, name="Collaborative team", visibility="closed",
                                   base_version=version(store, ws), operation_id="create-settings-team")["teamspace"]["id"]
    base = version(store, ws)
    first = rename(store, ws, target, base=base)
    assert read_teamspace(store, "owner", ws, target)["teamspace"]["name"] == first["teamspace"]["name"]
    snapshot = dump(store)
    store.iam.identity_directory.replace([])
    from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
    with pytest.raises(NotFoundOrDenied):
        read_teamspace(store, "owner", ws, target)
    with pytest.raises(NotFoundOrDenied):
        rename(store, ws, target, base=base)
    assert dump(store) == snapshot


def test_ui_teamspace_settings_two_process_retry_commits_once(team):
    store, ws, target = team
    base = version(store, ws)
    context = multiprocessing.get_context("spawn")
    start, result = context.Event(), context.Queue()
    workers = [context.Process(target=_rename_worker, args=(store.db_path, ws, target, base, start, result)) for _ in range(2)]
    for worker in workers: worker.start()
    start.set()
    try:
        outputs = [result.get(timeout=30) for _ in workers]
        assert outputs[0] == outputs[1] and "error" not in outputs[0], outputs
    finally:
        for worker in workers:
            worker.join(30)
            if worker.is_alive(): worker.terminate(); worker.join()
    assert [worker.exitcode for worker in workers] == [0, 0]
    assert version(store, ws) == base + 1
    with sqlite3.connect(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_operations WHERE action='workspace.teamspace_renamed'").fetchone()[0] == 1
