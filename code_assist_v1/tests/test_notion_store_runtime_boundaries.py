from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import notion_store as notion
from code_assist_v1 import routes_notion


def _route_client(monkeypatch: pytest.MonkeyPatch, user_id: str):
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: user_id)
    app = Flask(f"notion-runtime-boundary-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="notion-runtime-boundary-test")
    routes_notion.register_notion_routes(app)
    client = app.test_client()
    with client.session_transaction() as session:
        session["code_assist_user"] = {
            "id": user_id,
            "display_name": "Runtime Boundary Tester",
        }
    return client


def _schema_snapshot(db_path: Path) -> tuple[tuple[Any, ...], ...]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        return tuple(
            tuple(row)
            for row in con.execute(
                """
                SELECT type, name, tbl_name, COALESCE(sql, '')
                FROM sqlite_master
                ORDER BY type, name
                """
            ).fetchall()
        )
    finally:
        con.close()


def _file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.regression
def test_np_1a_missing_store_gets_do_not_create_workspace(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout: Any,
) -> None:
    user_id = "np1a-missing-reader"
    user_data_dir = Path(isolated_knowledge_layout.user_data_dir)
    user_root = user_data_dir / user_id
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(user_data_dir))
    client = _route_client(monkeypatch, user_id)

    assert not user_root.exists()
    assert notion.list_pages(user_id) == []
    assert notion.list_archived_pages(user_id) == []
    assert notion.list_session_pages(user_id, "session-missing") == []
    with pytest.raises(FileNotFoundError):
        notion.get_page(user_id, "page-missing")

    pages = client.get("/api/notion/pages")
    search = client.get("/api/notion/search?q=anything")
    missing_page = client.get("/api/notion/pages/page-missing")

    assert pages.status_code == 200
    assert pages.get_json()["items"] == []
    assert search.status_code == 200
    assert search.get_json()["items"] == []
    assert missing_page.status_code == 404
    assert not user_root.exists()


@pytest.mark.regression
def test_np_1a_gets_leave_database_schema_tree_and_files_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout: Any,
) -> None:
    user_id = "np1a-read-only"
    user_data_dir = Path(isolated_knowledge_layout.user_data_dir)
    notion_root = user_data_dir / user_id / "notion"
    db_path = notion_root / "notion.db"
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(user_data_dir))

    page_id = notion.create_page(user_id, title="Read boundary")["page"]["id"]
    fixture_con = sqlite3.connect(db_path)
    try:
        fixture_con.execute(
            "UPDATE pages SET parent_id='dangling-parent' WHERE id=?",
            (page_id,),
        )
        fixture_con.commit()
    finally:
        fixture_con.close()

    schema_before = _schema_snapshot(db_path)
    files_before = _file_snapshot(notion_root)
    mtime_before = db_path.stat().st_mtime_ns
    client = _route_client(monkeypatch, user_id)

    pages = client.get("/api/notion/pages")
    page = client.get(f"/api/notion/pages/{page_id}")
    search = client.get("/api/notion/search?q=Read")

    assert pages.status_code == 200
    assert page.status_code == 200
    assert search.status_code == 200
    assert page.get_json()["page"]["parent_id"] == "dangling-parent"
    assert _schema_snapshot(db_path) == schema_before
    assert _file_snapshot(notion_root) == files_before
    assert db_path.stat().st_mtime_ns == mtime_before

    with notion._connection(user_id) as read_con:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_con.execute("UPDATE pages SET title='must fail' WHERE id=?", (page_id,))


@pytest.mark.regression
def test_np_1a_explicit_bootstrap_and_write_paths_remain_compatible(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout: Any,
) -> None:
    user_id = "np1a-writer"
    user_data_dir = Path(isolated_knowledge_layout.user_data_dir)
    notion_root = user_data_dir / user_id / "notion"
    db_path = notion_root / "notion.db"
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(user_data_dir))

    bootstrap = notion.bootstrap_workspace(user_id)
    assert bootstrap == {"user_id": user_id, "created": True, "atomic_save": True}
    assert db_path.is_file()
    assert (notion_root / "files").is_dir()

    second_bootstrap = notion.bootstrap_workspace(user_id)
    assert second_bootstrap == {"user_id": user_id, "created": False, "atomic_save": True}

    created = notion.create_page(user_id, title="Created after bootstrap")
    page_id = created["page"]["id"]
    revision = created["page"]["revision"]
    updated = notion.update_page(user_id, page_id, {"title": "Write still works"})

    assert updated["page"]["title"] == "Write still works"
    assert updated["page"]["revision"] == revision + 1
    assert notion.get_page(user_id, page_id)["page"]["title"] == "Write still works"
