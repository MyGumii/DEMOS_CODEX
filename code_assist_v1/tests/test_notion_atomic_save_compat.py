from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import notion_store as notion
from code_assist_v1 import routes_notion
from code_assist_v1 import routes_notion_bridge


_ACTOR = "fixture-user"


def _app_client(register_routes: Any):
    app = Flask(f"notion-contract-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="notion-contract-test")
    register_routes(app)
    return app, app.test_client()


@pytest.fixture
def atomic_save_client(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout: Any,
):
    """Use only the per-test Notion database created below the safety root."""

    monkeypatch.setattr(notion, "USER_DATA_DIR", str(isolated_knowledge_layout.user_data_dir))
    monkeypatch.setattr(routes_notion, "current_user_id", lambda: _ACTOR)
    app, client = _app_client(routes_notion.register_notion_routes)
    with client.session_transaction() as session:
        session["code_assist_user"] = {
            "id": _ACTOR,
            "display_name": "Atomic Save Tester",
        }
    return client, isolated_knowledge_layout


def _create_page(client: Any) -> tuple[str, int]:
    response = client.post("/api/notion/pages", json={"title": "Before atomic save"})
    assert response.status_code == 200
    created = response.get_json()
    assert created["capabilities"] == {"atomic_save": True, "personal_linked_database": True}
    page = created["page"]
    return str(page["id"]), int(page["revision"])


def _save_request(base_revision: int, operation_id: str) -> dict[str, Any]:
    return {
        "base_revision": base_revision,
        "operation_id": operation_id,
        "page": {
            "title": "After atomic save",
            "icon": "A",
            "cover": "linear-gradient(120deg,#fff,#ddd)",
            "settings": {"font": "serif", "full_width": True},
        },
        "blocks": [
            {
                "id": "block_atomic_contract",
                "type": "paragraph",
                "content": "page metadata and blocks commit together",
                "props": {"color": "blue"},
            }
        ],
    }


@pytest.mark.regression
def test_atomic_save_success_returns_one_coherent_revision(
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, layout = atomic_save_client
    page_id, base_revision = _create_page(client)

    response = client.post(
        f"/api/notion/pages/{page_id}/save",
        json=_save_request(base_revision, str(uuid4())),
    )

    assert response.status_code == 200
    result = response.get_json()
    assert {"page", "blocks", "files", "revision"}.issubset(result)
    assert result["revision"] == base_revision + 1
    assert result["page"]["revision"] == result["revision"]
    assert result["page"]["title"] == "After atomic save"
    assert result["page"]["settings"]["font"] == "serif"
    assert [block["content"] for block in result["blocks"]] == [
        "page metadata and blocks commit together"
    ]
    assert result["files"] == []
    assert (Path(layout.notion_dir) / "notion.db").is_file()


@pytest.mark.regression
def test_atomic_save_stale_base_is_409_with_current_revision_and_no_partial_write(
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, _layout = atomic_save_client
    page_id, base_revision = _create_page(client)
    committed = client.post(
        f"/api/notion/pages/{page_id}/save",
        json=_save_request(base_revision, str(uuid4())),
    )
    assert committed.status_code == 200
    committed_body = committed.get_json()

    stale_request = _save_request(base_revision, str(uuid4()))
    stale_request["page"]["title"] = "Must not be committed"
    stale_request["blocks"][0]["content"] = "must not replace committed blocks"
    stale = client.post(f"/api/notion/pages/{page_id}/save", json=stale_request)

    assert stale.status_code == 409
    assert stale.get_json()["error"] == "revision_conflict"
    assert stale.get_json()["current_revision"] == committed_body["revision"]
    current = client.get(f"/api/notion/pages/{page_id}").get_json()
    assert current["page"]["revision"] == committed_body["revision"]
    assert current["page"]["title"] == committed_body["page"]["title"]
    assert current["blocks"] == committed_body["blocks"]


@pytest.mark.regression
def test_atomic_save_identical_retry_returns_original_result_without_new_revision(
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, _layout = atomic_save_client
    page_id, base_revision = _create_page(client)
    request_body = _save_request(base_revision, str(uuid4()))

    first = client.post(f"/api/notion/pages/{page_id}/save", json=request_body)
    assert first.status_code == 200
    versions_after_first = client.get(
        f"/api/notion/pages/{page_id}/versions"
    ).get_json()["items"]

    retry = client.post(f"/api/notion/pages/{page_id}/save", json=request_body)
    versions_after_retry = client.get(
        f"/api/notion/pages/{page_id}/versions"
    ).get_json()["items"]

    assert retry.status_code == 200
    assert retry.get_json() == first.get_json()
    assert versions_after_retry == versions_after_first
    assert client.get(f"/api/notion/pages/{page_id}").get_json()["page"][
        "revision"
    ] == first.get_json()["revision"]


@pytest.mark.regression
def test_atomic_save_operation_id_hash_mismatch_is_409_and_preserves_first_result(
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, _layout = atomic_save_client
    page_id, base_revision = _create_page(client)
    operation_id = str(uuid4())
    original_request = _save_request(base_revision, operation_id)
    first = client.post(f"/api/notion/pages/{page_id}/save", json=original_request)
    assert first.status_code == 200

    changed_request = deepcopy(original_request)
    changed_request["blocks"][0]["content"] = "different request hash"
    mismatch = client.post(f"/api/notion/pages/{page_id}/save", json=changed_request)

    assert mismatch.status_code == 409
    error = mismatch.get_json().get("error")
    assert isinstance(error, str) and error.strip()
    assert error != "revision_conflict"
    current = client.get(f"/api/notion/pages/{page_id}").get_json()
    assert current["page"] == first.get_json()["page"]
    assert current["blocks"] == first.get_json()["blocks"]
    assert client.post(
        f"/api/notion/pages/{page_id}/save", json=original_request
    ).get_json() == first.get_json()


@pytest.mark.regression
def test_atomic_save_rolls_back_metadata_blocks_revision_and_ledger_on_mid_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, layout = atomic_save_client
    page_id, base_revision = _create_page(client)
    before = client.get(f"/api/notion/pages/{page_id}").get_json()
    versions_before = client.get(
        f"/api/notion/pages/{page_id}/versions"
    ).get_json()["items"]
    operation_id = str(uuid4())
    original_replace = notion._replace_blocks_in_connection

    def fail_after_block_write(*args: Any, **kwargs: Any) -> None:
        original_replace(*args, **kwargs)
        raise RuntimeError(r"fault after blocks at G:\private\notion.db")

    monkeypatch.setattr(notion, "_replace_blocks_in_connection", fail_after_block_write)

    failed = client.post(
        f"/api/notion/pages/{page_id}/save",
        json=_save_request(base_revision, operation_id),
    )

    assert failed.status_code == 500
    assert failed.get_json()["code"] == "internal_error"
    assert "private" not in failed.get_data(as_text=True)
    after = client.get(f"/api/notion/pages/{page_id}").get_json()
    assert after["page"] == before["page"]
    assert after["blocks"] == before["blocks"]
    assert client.get(f"/api/notion/pages/{page_id}/versions").get_json()[
        "items"
    ] == versions_before
    database = Path(layout.notion_dir) / "notion.db"
    with sqlite3.connect(database) as connection:
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM page_save_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
    assert ledger_count == 0


@pytest.mark.regression
def test_atomic_save_rolls_back_everything_when_ledger_insert_fails(
    atomic_save_client: tuple[Any, Any],
) -> None:
    client, layout = atomic_save_client
    page_id, base_revision = _create_page(client)
    before = client.get(f"/api/notion/pages/{page_id}").get_json()
    versions_before = client.get(
        f"/api/notion/pages/{page_id}/versions"
    ).get_json()["items"]
    operation_id = str(uuid4())
    database = Path(layout.notion_dir) / "notion.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_atomic_save_ledger
            BEFORE INSERT ON page_save_operations
            BEGIN
              SELECT RAISE(ABORT, 'injected ledger failure at private path');
            END
            """
        )
        connection.commit()

    failed = client.post(
        f"/api/notion/pages/{page_id}/save",
        json=_save_request(base_revision, operation_id),
    )

    assert failed.status_code == 500
    assert failed.get_json()["code"] == "internal_error"
    assert "injected ledger failure" not in failed.get_data(as_text=True)
    after = client.get(f"/api/notion/pages/{page_id}").get_json()
    assert after["page"] == before["page"]
    assert after["blocks"] == before["blocks"]
    assert client.get(f"/api/notion/pages/{page_id}/versions").get_json()[
        "items"
    ] == versions_before
    with sqlite3.connect(database) as connection:
        ledger_count = connection.execute(
            "SELECT COUNT(*) FROM page_save_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
    assert ledger_count == 0


@pytest.mark.regression
def test_legacy_workspace_requires_explicit_atomic_migration_without_read_side_effects(
    atomic_save_client: tuple[Any, Any],
) -> None:
    """An old DB is readable as-is and is upgraded only by the explicit migrator."""

    client, layout = atomic_save_client
    database = Path(layout.notion_dir) / "notion.db"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              parent_id TEXT,
              title TEXT NOT NULL,
              icon TEXT,
              cover TEXT,
              settings_json TEXT,
              position INTEGER NOT NULL DEFAULT 0,
              archived INTEGER NOT NULL DEFAULT 0,
              archived_at INTEGER,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        notion._ensure_schema(connection, bootstrap_atomic=False)
        connection.execute(
            """
            INSERT INTO pages(
              id, owner_user_id, parent_id, title, icon, cover, settings_json,
              position, archived, archived_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-page",
                _ACTOR,
                None,
                "Legacy page",
                "",
                "",
                "{}",
                1,
                0,
                None,
                1,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO blocks(
              id, page_id, owner_user_id, position, block_type, content,
              props_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "legacy-block",
                "legacy-page",
                _ACTOR,
                1,
                "paragraph",
                "Legacy body",
                "{}",
                1,
                1,
            ),
        )
        connection.commit()
        schema_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        ]
        schema_version_before = connection.execute("PRAGMA schema_version").fetchone()[0]
        assert "revision" not in {
            str(row[1]) for row in connection.execute("PRAGMA table_info(pages)")
        }
        assert not any(row[1] == "page_save_operations" for row in schema_before)

    read = client.get("/api/notion/pages/legacy-page")
    assert read.status_code == 200
    assert read.get_json()["capabilities"] == {"atomic_save": False, "personal_linked_database": True}
    assert "revision" not in read.get_json()

    unavailable = client.post(
        "/api/notion/pages/legacy-page/save",
        json=_save_request(1, str(uuid4())),
    )
    assert unavailable.status_code == 503
    assert unavailable.get_json()["error"] == "atomic_save_unavailable"

    with sqlite3.connect(database) as connection:
        schema_after = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        schema_version_after = connection.execute("PRAGMA schema_version").fetchone()[0]
    assert schema_after == schema_before
    assert schema_version_after == schema_version_before

    migration = notion.migrate_atomic_save_schema(_ACTOR)
    assert migration["atomic_save"] is True
    assert client.get("/api/notion/pages/legacy-page").get_json()["capabilities"] == {
        "atomic_save": True, "personal_linked_database": True
    }
    migrated_save = client.post(
        "/api/notion/pages/legacy-page/save",
        json=_save_request(1, str(uuid4())),
    )
    assert migrated_save.status_code == 200
    assert migrated_save.get_json()["revision"] == 2


def _registered_methods(app: Flask) -> dict[str, set[str]]:
    methods_by_path: dict[str, set[str]] = {}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        methods_by_path.setdefault(rule.rule, set()).update(
            set(rule.methods or ()) - {"HEAD", "OPTIONS"}
        )
    return methods_by_path


@pytest.mark.regression
def test_second_brain_compatibility_routes_remain_registered() -> None:
    app = Flask(f"notion-route-registration-{uuid4().hex}")
    routes_notion.register_notion_routes(app)
    routes_notion_bridge.register_notion_bridge_routes(app)
    registered = _registered_methods(app)

    expected = {
        "/api/notion/templates/second_brain": {"POST"},
        "/api/notion/pages/<path:page_id>/publish_second_brain": {"POST"},
        "/api/notion/chat/sessions": {"GET"},
        "/api/notion/pages/<path:page_id>/attach": {"POST"},
        "/api/notion/session_pages": {"GET"},
        "/api/notion/session_pages/<path:page_id>": {"DELETE"},
        "/api/notion/import_chat": {"POST"},
        "/api/code/notion/templates/second_brain": {"POST"},
        "/api/code/notion/pages/<path:page_id>/publish_second_brain": {"POST"},
        "/api/code/notion/session_attach": {"POST"},
        "/api/code/notion/session_detach": {"POST"},
        "/api/code/notion/session_pages": {"GET"},
        "/api/code/notion/save_chat_summary": {"POST"},
        "/api/code/notion/import_thought": {"POST"},
    }
    assert {path: registered.get(path) for path in expected} == expected


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/pages/page-publish/publish_second_brain",
            id="notion-workspace",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/pages/page-publish/publish_second_brain",
            id="second-brain-bridge",
        ),
    ),
)
def test_existing_publish_routes_keep_their_second_brain_delegate_contract(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
) -> None:
    export_result = {
        "page": {
            "id": "page-publish",
            "title": "Publish title",
            "owner_user_id": "page-owner",
            "can_edit": True,
        },
        "markdown": "# Publish title\n\nBody\n",
        "images": [{"filename": "diagram.png", "raw": b"image"}],
    }
    export_calls: list[tuple[Any, ...]] = []
    import_calls: list[dict[str, Any]] = []
    lock_calls: list[tuple[Any, ...]] = []

    def export_page(user_id: str, page_id: str, *, include_assets: bool = False):
        export_calls.append((user_id, page_id, include_assets))
        return export_result

    def import_source(**kwargs: Any):
        import_calls.append(kwargs)
        return {"thought_id": "thought-published", "source_type": "notion"}

    @contextmanager
    def write_lock(scope: str, user_id: str, *parts: object):
        lock_calls.append((scope, user_id, parts))
        yield

    monkeypatch.setattr(route_module, "_uid", lambda: "compat-actor")
    monkeypatch.setattr(route_module.ns, "export_page_markdown", export_page)
    monkeypatch.setattr(route_module.sb, "import_source_markdown", import_source)
    monkeypatch.setattr(route_module, "user_write_lock", write_lock)
    _app, client = _app_client(register_routes)

    response = client.post(path)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "published",
        "thought_id": "thought-published",
        "source_type": "notion",
    }
    assert export_calls == [("compat-actor", "page-publish", True)]
    assert lock_calls == [("second_brain", "compat-actor", ())]
    assert import_calls == [
        {
            "filename": "notion-Publish title-page-publish.md",
            "markdown": export_result["markdown"],
            "user_id": "compat-actor",
            "images": export_result["images"],
            "source_type": "notion",
            "source_metadata": {
                "page_id": "page-publish",
                "owner_user_id": "page-owner",
                "title": "Publish title",
            },
        }
    ]


@pytest.mark.regression
def test_bridge_template_session_chat_and_thought_delegates_remain_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = routes_notion_bridge
    calls: dict[str, list[Any]] = {
        "template": [],
        "attach": [],
        "detach": [],
        "list": [],
        "chat_markdown": [],
        "create": [],
        "thought": [],
        "lock": [],
    }

    monkeypatch.setattr(bridge, "_uid", lambda: "compat-actor")

    def create_template(user_id: str):
        calls["template"].append(user_id)
        return {"root_page_id": "template-root", "created": True}

    def attach(user_id: str, page_id: str, session_id: str):
        calls["attach"].append((user_id, page_id, session_id))
        return {"page_id": page_id, "session_id": session_id}

    def detach(user_id: str, page_id: str, session_id: str):
        calls["detach"].append((user_id, page_id, session_id))
        return {"page_id": page_id, "session_id": session_id, "detached": True}

    def list_session_pages(user_id: str, session_id: str):
        calls["list"].append((user_id, session_id))
        return [{"page_id": "page-session", "session_id": session_id}]

    def chat_markdown(messages: list[dict[str, Any]], *, title: str, session_id: str):
        calls["chat_markdown"].append((messages, title, session_id))
        return "# Chat title\n\nConversation body\n"

    def create_page(
        user_id: str,
        *,
        title: str,
        markdown: str,
        parent_id: str,
    ):
        calls["create"].append(
            {
                "user_id": user_id,
                "title": title,
                "markdown": markdown,
                "parent_id": parent_id,
            }
        )
        return {"page": {"id": f"created-{len(calls['create'])}", "title": title}, "blocks": []}

    def get_thought(thought_id: str, *, user_id: str):
        calls["thought"].append((thought_id, user_id))
        return {
            "meta": {"id": thought_id, "title": "Thought title", "topics": ["alpha", "beta"]},
            "content": "Durable thought body",
        }

    @contextmanager
    def write_lock(scope: str, user_id: str, *parts: object):
        calls["lock"].append((scope, user_id, parts))
        yield

    monkeypatch.setattr(bridge.ns, "create_second_brain_template", create_template)
    monkeypatch.setattr(bridge.ns, "attach_page_to_session", attach)
    monkeypatch.setattr(bridge.ns, "detach_page_from_session", detach)
    monkeypatch.setattr(bridge.ns, "list_session_pages", list_session_pages)
    monkeypatch.setattr(bridge.ns, "chat_messages_to_markdown", chat_markdown)
    monkeypatch.setattr(bridge.ns, "create_page_from_markdown", create_page)
    monkeypatch.setattr(bridge.sb, "get_thought", get_thought)
    monkeypatch.setattr(bridge, "user_write_lock", write_lock)
    _app, client = _app_client(bridge.register_notion_bridge_routes)

    template = client.post("/api/code/notion/templates/second_brain")
    attached = client.post(
        "/api/code/notion/session_attach",
        json={"page_id": "page-session", "session_id": "session-1"},
    )
    listed = client.get("/api/code/notion/session_pages?session_id=session-1")
    detached = client.post(
        "/api/code/notion/session_detach",
        json={"page_id": "page-session", "session_id": "session-1"},
    )
    messages = [{"role": "user", "content": "Keep this"}]
    chat = client.post(
        "/api/code/notion/save_chat_summary",
        json={
            "messages": messages,
            "title": "Chat title",
            "session_id": "session-1",
            "parent_id": "parent-chat",
            "mode": "full",
        },
    )
    thought = client.post(
        "/api/code/notion/import_thought",
        json={"thought_id": "thought-1", "parent_id": "parent-thought"},
    )

    assert template.status_code == 200
    assert template.get_json() == {"root_page_id": "template-root", "created": True}
    assert attached.get_json() == {
        "status": "attached",
        "page_id": "page-session",
        "session_id": "session-1",
    }
    assert listed.get_json() == {
        "items": [{"page_id": "page-session", "session_id": "session-1"}],
        "count": 1,
    }
    assert detached.get_json() == {
        "status": "detached",
        "page_id": "page-session",
        "session_id": "session-1",
        "detached": True,
    }
    assert chat.status_code == 200
    assert chat.get_json()["status"] == "saved"
    assert chat.get_json()["save_mode"] == "full"
    assert chat.get_json()["page"]["id"] == "created-1"
    assert thought.status_code == 200
    assert thought.get_json()["status"] == "imported"
    assert thought.get_json()["page"]["id"] == "created-2"

    assert calls["template"] == ["compat-actor"]
    assert calls["attach"] == [("compat-actor", "page-session", "session-1")]
    assert calls["list"] == [("compat-actor", "session-1")]
    assert calls["detach"] == [("compat-actor", "page-session", "session-1")]
    assert calls["chat_markdown"] == [(messages, "Chat title", "session-1")]
    assert calls["thought"] == [("thought-1", "compat-actor")]
    assert calls["lock"] == [
        ("notion_template", "compat-actor", ()),
        ("notion_import", "compat-actor", ()),
    ]
    assert calls["create"][0] == {
        "user_id": "compat-actor",
        "title": "Chat title",
        "markdown": "# Chat title\n\nConversation body\n",
        "parent_id": "parent-chat",
    }
    assert calls["create"][1]["user_id"] == "compat-actor"
    assert calls["create"][1]["title"] == "Thought title"
    assert calls["create"][1]["parent_id"] == "parent-thought"
    assert "Durable thought body" in calls["create"][1]["markdown"]
    assert "thought_id: thought-1" in calls["create"][1]["markdown"]
    assert "topics: alpha, beta" in calls["create"][1]["markdown"]
