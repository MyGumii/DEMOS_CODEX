from __future__ import annotations

import multiprocessing
from unittest.mock import patch

from flask import Flask
import pytest

from code_assist_v1 import notion_store as ns, routes_notion
from code_assist_v1.notion_page_creation import create_draft_page


def _request(parent_id, operation="draft-one"):
    return dict(operation_id=operation, title="새 하위 페이지", parent_id=parent_id,
                blocks=[{"id": "draft-body", "type": "paragraph", "content": "첫 본문", "props": {}}])


def test_ui_draft_atomic_create_exact_replay_and_changed_input(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    parent = ns.create_page("owner", title="부모")["page"]
    ns.share_page("owner", parent["id"], "editor", "edit")
    request = _request(parent["id"])
    first = create_draft_page("editor", **request)
    child_id = first["page"]["id"]
    assert first["page"]["owner_user_id"] == "owner"
    assert first["page"]["can_edit"]
    assert first["blocks"][0]["content"] == "첫 본문"
    parent_after = ns.get_page("owner", parent["id"])
    assert sum(b.get("props", {}).get("page_id") == child_id for b in parent_after["blocks"]) == 1
    versions = len(ns.list_page_versions("owner", parent["id"]))
    ns.update_page("editor", child_id, {"title": "저장 후 다른 제목"})
    assert create_draft_page("editor", **request) == first, "replay returns original result even after later edits"
    assert ns.get_page("owner", parent["id"])["revision"] == parent_after["revision"]
    assert len(ns.list_page_versions("owner", parent["id"])) == versions
    with pytest.raises(ns.OperationIdConflict):
        create_draft_page("editor", **{**request, "title": "다른 요청"})


def test_ui_draft_permissions_lock_and_in_transaction_revalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    parent = ns.create_page("owner", title="권한 검증")["page"]
    ns.share_page("owner", parent["id"], "reader", "view")
    with pytest.raises((PermissionError, FileNotFoundError)):
        create_draft_page("reader", **_request(parent["id"]))
    ns.update_page("owner", parent["id"], {"settings": {"locked": True}})
    with pytest.raises(PermissionError):
        create_draft_page("owner", **_request(parent["id"]))
    ns.update_page("owner", parent["id"], {"settings": {"locked": False}})
    ns.share_page("owner", parent["id"], "editor", "edit")
    original = ns._require_page
    def revoke_after_preflight(user, page, role):
        resolved = original(user, page, role)
        if user == "editor":
            ns.revoke_share("owner", parent["id"], "editor")
        return resolved
    with patch.object(ns, "_require_page", side_effect=revoke_after_preflight):
        with pytest.raises(PermissionError):
            create_draft_page("editor", **_request(parent["id"]))
    assert not any(page.get("parent_id") == parent["id"] for page in ns.list_pages("owner"))


def test_ui_draft_failure_rolls_back_page_parent_link_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    parent = ns.create_page("owner", title="부모")["page"]
    before = ns.get_page("owner", parent["id"])
    with patch.object(ns, "_page_payload_in_connection", side_effect=RuntimeError("fail before commit")):
        with pytest.raises(RuntimeError):
            create_draft_page("owner", **_request(parent["id"]))
    assert ns.get_page("owner", parent["id"]) == before
    with ns._connection("owner") as con:
        assert con.execute("SELECT count(*) FROM pages WHERE parent_id=?", (parent["id"],)).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM page_save_operations").fetchone()[0] == 0
    assert create_draft_page("owner", **_request(parent["id"]))["page"]["id"]


def _process_create(root, parent, queue):
    ns.USER_DATA_DIR = root
    try:
        queue.put(create_draft_page("owner", **_request(parent))["page"]["id"])
    except Exception as error:
        queue.put({"error": repr(error)})


def test_ui_draft_cross_process_retry_creates_one_child(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    parent = ns.create_page("owner", title="동시 요청")["page"]
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [context.Process(target=_process_create, args=(str(tmp_path), parent["id"], queue)) for _ in range(3)]
    for worker in workers:
        worker.start()
    try:
        results = [queue.get(timeout=30) for _ in workers]
        assert all(isinstance(result, str) for result in results), results
        assert len(set(results)) == 1
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
        children = [p for p in ns.list_pages("owner") if p.get("parent_id") == parent["id"]]
        assert len(children) == 1
        parent_blocks = ns.get_page("owner", parent["id"])["blocks"]
        assert sum(b.get("props", {}).get("page_id") == children[0]["id"] for b in parent_blocks) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=10)


def test_ui_draft_api_validation_replay_and_canonical_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes_notion, "_uid", lambda: "owner")
    from code_assist_v1.notion_collaboration import iam_runtime
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    app = Flask(__name__)
    routes_notion.register_notion_routes(app)
    client = app.test_client()
    request = _request("")
    first = client.post("/api/notion/pages", json=request)
    assert first.status_code == 200
    assert "no-store" in first.headers["Cache-Control"]
    page_url = "/api/notion/pages/" + first.json["page"]["id"]
    assert client.get(page_url + "?draft_creation=1").json["capabilities"]["local_page_draft_create"] is True
    assert "local_page_draft_create" not in client.get(page_url).json["capabilities"]
    assert client.post("/api/notion/pages", json=request).json == first.json
    assert client.post("/api/notion/pages", json={**request, "title": "different"}).status_code == 409
    assert client.post("/api/notion/pages", json={**request, "blocks": None}).status_code == 400
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    assert client.get(page_url + "?draft_creation=1").json["capabilities"]["local_page_draft_create"] is False
    assert client.post("/api/notion/pages", json=request).status_code == 404
