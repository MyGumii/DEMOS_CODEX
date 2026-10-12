"""UI-R5: personal data-source discovery must respect current page grants."""
from __future__ import annotations

import pytest
from flask import Flask

from code_assist_v1 import notion_personal_database_sources as sources
from code_assist_v1 import notion_store as store, routes_notion


def _database(store_page, owner, block_id, title, *, linked=False):
    props = {"views": [{"id": "view-main", "name": "표", "type": "table", "config": {}}], "rows": []}
    if linked:
        props["linked_database"] = {"document_id": store_page["id"], "database_id": "original"}
    store.replace_blocks(owner, store_page["id"], [
        {"id": block_id, "type": "table", "content": title, "props": props},
    ])


def test_ui_r5_personal_source_catalog_rechecks_access_and_excludes_links(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "USER_DATA_DIR", str(tmp_path))
    shared = store.create_page("owner", title="공유 프로젝트")["page"]
    private = store.create_page("owner", title="비공개 프로젝트")["page"]
    linked = store.create_page("owner", title="연결 보기")["page"]
    _database(shared, "owner", "db-shared", "실행 표")
    _database(private, "owner", "db-private", "비밀 표")
    _database(linked, "owner", "db-linked", "연결된 표", linked=True)
    store.share_page("owner", shared["id"], "reader", "view")

    result = sources.list_sources("reader")
    assert [(item["document_id"], item["id"]) for item in result["items"]] == [(shared["id"], "db-shared")]
    assert result["count"] == 1
    assert sources.list_sources("reader", query="실행")["count"] == 1
    assert sources.list_sources("reader", query="비밀")["count"] == 0
    assert sources.list_sources("owner", limit=1)["has_more"]
    assert sources.list_sources("owner", limit=1, offset=1)["count"] == 2
    source = sources.read_source("reader", shared["id"], "db-shared")
    assert source["database"]["views"][0]["id"] == "view-main"
    assert source["editable"] is False
    assert sources.read_source("owner", shared["id"], "db-shared")["editable"] is True
    with pytest.raises(FileNotFoundError):
        sources.read_source("owner", linked["id"], "db-linked")

    store.revoke_share("owner", shared["id"], "reader")
    assert sources.list_sources("reader")["items"] == []
    with pytest.raises(FileNotFoundError):
        sources.read_source("reader", shared["id"], "db-shared")
    store.archive_page("owner", private["id"])
    assert sources.list_sources("owner")["count"] == 1


def test_ui_r5_personal_source_routes_are_uncached_and_scope_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "USER_DATA_DIR", str(tmp_path))
    page = store.create_page("owner", title="원본")["page"]
    _database(page, "owner", "db-source", "업무 표")
    app = Flask(__name__)
    routes_notion.register_notion_routes(app)
    monkeypatch.setattr(routes_notion, "_uid", lambda: "owner")
    from code_assist_v1.notion_collaboration import iam_runtime
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    client = app.test_client()
    catalog = client.get("/api/notion/database-sources?q=업무")
    assert catalog.status_code == 200
    assert catalog.json["items"][0]["id"] == "db-source"
    assert "no-store" in catalog.headers["Cache-Control"]
    detail = client.get(f"/api/notion/database-sources/{page['id']}/db-source")
    assert detail.status_code == 200
    assert detail.json["database"]["title"] == "업무 표"
    assert "no-store" in detail.headers["Cache-Control"]
    created = client.post(f"/api/notion/database-sources/{page['id']}/db-source/items",
                          json={"base_revision": detail.json["source_revision"],
                                "operation_id": "route-item-once"})
    assert created.status_code == 200
    assert "no-store" in created.headers["Cache-Control"]
    assert store.get_page("owner", created.json["item_page_id"])["page"]["parent_id"] == page["id"]

    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    assert client.get("/api/notion/database-sources").status_code == 404
    assert client.get(f"/api/notion/database-sources/{page['id']}/db-source").status_code == 404
    assert client.post(f"/api/notion/database-sources/{page['id']}/db-source/items",
                       json={"base_revision": detail.json["source_revision"],
                             "operation_id": "route-item-twice"}).status_code == 404


def test_ui_r5_personal_linked_item_creation_is_atomic_and_replay_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "USER_DATA_DIR", str(tmp_path))
    source = store.create_page("owner", title="원본")["page"]
    store.replace_blocks("owner", source["id"], [{
        "id": "source-db", "type": "table", "content": "업무",
        "props": {"view": "table", "properties": [
            {"id": "name", "name": "이름", "type": "title"},
            {"id": "done", "name": "완료", "type": "checkbox"},
        ], "rows": [{"name": "기존 항목", "done": False}]},
    }])
    revision = store.get_page("owner", source["id"])["revision"]
    result = sources.create_item("owner", source["id"], "source-db",
                                 base_revision=revision, operation_id="new-item-once")
    replay = sources.create_item("owner", source["id"], "source-db",
                                 base_revision=revision, operation_id="new-item-once")
    assert replay == result
    updated = store.get_page("owner", source["id"])
    assert updated["revision"] == revision + 1
    assert len(updated["blocks"]) == 1
    rows = updated["blocks"][0]["props"]["rows"]
    assert [row["name"] for row in rows] == ["기존 항목", "새 페이지"]
    assert rows[1]["page_id"] == result["item_page_id"]
    assert rows[1]["done"] is False
    child = store.get_page("owner", result["item_page_id"])
    assert child["page"]["parent_id"] == source["id"]
    assert child["page"]["title"] == "새 페이지"
    with pytest.raises(store.OperationIdConflict):
        sources.create_item("owner", source["id"], "source-db",
                            base_revision=updated["revision"], operation_id="new-item-once")
    with pytest.raises(store.PageRevisionConflict):
        sources.create_item("owner", source["id"], "source-db",
                            base_revision=revision, operation_id="stale-item")
    assert len(store.get_page("owner", source["id"])["blocks"][0]["props"]["rows"]) == 2
    store.archive_page("owner", result["item_page_id"])
    with pytest.raises(FileNotFoundError):
        sources.create_item("owner", source["id"], "source-db",
                            base_revision=revision, operation_id="new-item-once")


def test_ui_r5_personal_linked_item_create_rechecks_grant_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "USER_DATA_DIR", str(tmp_path))
    source = store.create_page("owner", title="공유 원본")["page"]
    _database(source, "owner", "source-db", "업무")
    store.share_page("owner", source["id"], "editor", "edit")
    revision = store.get_page("editor", source["id"])["revision"]
    result = sources.create_item("editor", source["id"], "source-db",
                                 base_revision=revision, operation_id="shared-item")
    assert store.get_page("editor", result["item_page_id"])["page"]["parent_id"] == source["id"]
    store.revoke_share("owner", source["id"], "editor")
    with pytest.raises(FileNotFoundError):
        sources.create_item("editor", source["id"], "source-db",
                            base_revision=revision, operation_id="shared-item")
    store.update_page("owner", source["id"], {"settings": {"locked": True}})
    latest = store.get_page("owner", source["id"])["revision"]
    with pytest.raises(PermissionError):
        sources.create_item("owner", source["id"], "source-db",
                            base_revision=latest, operation_id="locked-item")
    assert len(store.get_page("owner", source["id"])["blocks"][0]["props"]["rows"]) == 1
