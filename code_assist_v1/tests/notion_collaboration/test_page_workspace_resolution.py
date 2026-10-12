"""UI-DIRECT-LINK: exact current page authority, independent of sidebar/legacy state."""
import sqlite3

import pytest
from flask import Flask

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import iam_runtime
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.tests.notion_collaboration.test_search_authority import _authority, _grant, IDENTITIES


@pytest.fixture()
def environment(tmp_path):
    authority, root, catalog = _authority(tmp_path)
    store = iam_runtime.CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(IDENTITIES))
    ws = authority.read_document("owner", "page-e1")["workspace_id"]
    _grant(store.iam, "page-e1", "outsider", "direct-link-view")
    return store, ws


def dump(store):
    with sqlite3.connect(store.db_path) as con:
        return tuple(con.iterdump())


def test_ui_direct_link_read_only_exact_page_and_current_identity(environment):
    store, ws = environment
    before = dump(store)
    assert store.capabilities()["page_workspace_resolution"] is True
    for actor in ("owner", "outsider"):
        assert iam_runtime.resolve_page_workspace(store.iam, actor, "page-e1") == {
            "page_id": "page-e1", "workspace_id": ws}
    for actor, page in (("outsider", "missing"), ("not-active", "page-e1")):
        with pytest.raises(NotFoundOrDenied):
            iam_runtime.resolve_page_workspace(store.iam, actor, page)
    disabled = iam_runtime.CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=InMemoryIdentityDirectory([x for x in IDENTITIES if x != "outsider"]))
    with pytest.raises(NotFoundOrDenied):
        iam_runtime.resolve_page_workspace(disabled.iam, "outsider", "page-e1")
    assert dump(store) == before


def test_ui_direct_link_revocation_and_archival(environment):
    store, ws = environment
    page = store.iam.read_document("owner", "page-e1")
    store.iam.revoke_page_grant("owner", "page-e1", "subject", "outsider",
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
        operation_id="direct-link-revoke")
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        iam_runtime.resolve_page_workspace(store.iam, "outsider", "page-e1")
    assert dump(store) == before
    # Seed an archived state in this disposable catalog; owners must not resolve it either.
    with sqlite3.connect(store.db_path) as con:
        con.execute("UPDATE workspace_collaboration_documents SET archived=1 WHERE id='page-e1'")
    before = dump(store)
    with pytest.raises(NotFoundOrDenied):
        iam_runtime.resolve_page_workspace(store.iam, "owner", "page-e1")
    assert dump(store) == before


def test_ui_direct_link_http_actor_gate_and_no_projection(environment, monkeypatch):
    store, ws = environment
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: "outsider")
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    app = Flask(__name__)
    app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    before = dump(store)
    response = client.get("/api/notion/pages/page-e1/workspace?subject_id=owner&workspace_id=wrong")
    assert response.status_code == 200
    assert response.json == {"page_id": "page-e1", "workspace_id": ws}
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get("/api/notion/pages/missing/workspace?subject_id=owner").status_code == 404
    monkeypatch.setattr(routes, "current_user_id", lambda: "not-active")
    assert client.get("/api/notion/pages/page-e1/workspace?subject_id=owner").status_code == 404
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: False)
    assert client.get("/api/notion/pages/page-e1/workspace").status_code == 404
    assert dump(store) == before
