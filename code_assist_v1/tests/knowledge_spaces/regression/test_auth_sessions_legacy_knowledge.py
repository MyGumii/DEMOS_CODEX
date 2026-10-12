from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import auth, concurrency, config, knowledge_store
from code_assist_v1.routes_knowledge import register_knowledge_routes
from code_assist_v1.routes_sessions import register_session_routes


pytestmark = pytest.mark.regression

_ORIGIN = "https://agent.example.test"
_SAME_ORIGIN_HEADERS = {
    "Origin": _ORIGIN,
}


@pytest.fixture(autouse=True)
def _restore_legacy_process_state():
    """Keep process-local legacy caches/locks from leaking across regressions."""

    failed_logins = {key: list(value) for key, value in auth._FAILED_LOGINS.items()}
    index_cache = dict(knowledge_store._INDEX_CACHE)
    with concurrency._LOCKS_GUARD:
        locks = dict(concurrency._LOCKS)
    yield
    auth._FAILED_LOGINS.clear()
    auth._FAILED_LOGINS.update(failed_logins)
    knowledge_store._INDEX_CACHE.clear()
    knowledge_store._INDEX_CACHE.update(index_cache)
    with concurrency._LOCKS_GUARD:
        concurrency._LOCKS.clear()
        concurrency._LOCKS.update(locks)


def _make_app(*route_registrars) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="regression-only-secret",
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
    )
    auth.register_auth_routes(app)
    for registrar in route_registrars:
        registrar(app)
    return app


def _create_personal_user(user_id: str, password: str) -> None:
    public = auth.create_user(
        user_id,
        password,
        display_name=f"Personal {user_id}",
        role="user",
    )
    assert public["id"] == user_id
    assert "password_hash" not in public


def _login(client, user_id: str, password: str):
    return client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": user_id, "password": password},
        headers=_SAME_ORIGIN_HEADERS,
    )


def _csrf_headers(client) -> dict[str, str]:
    me = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert me.status_code == 200
    token = me.get_json().get("csrf_token")
    assert isinstance(token, str) and token
    return {**_SAME_ORIGIN_HEADERS, "X-CSRF-Token": token}


def _assert_password_hash_not_exposed(response, stored_hash: str) -> None:
    response_text = response.get_data(as_text=True)
    assert "password_hash" not in response_text
    assert stored_hash not in response_text
    assert all(stored_hash not in value for value in response.headers.getlist("Set-Cookie"))


@pytest.mark.scenario("REG-002")
def test_reg_002_personal_cookie_login_me_logout_and_relogin_hide_password_hashes(
    isolated_data_root: Path,
) -> None:
    user_id = f"reg-auth-{uuid4().hex[:12]}"
    password = "Correct-Horse-Auth-42"
    _create_personal_user(user_id, password)

    registry_path = Path(auth.AUTH_USERS_FILE)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    stored_hash = registry["users"][user_id]["password_hash"]
    assert stored_hash
    assert registry_path.is_relative_to(isolated_data_root)

    app = _make_app()
    client = app.test_client()

    before_login = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert before_login.status_code == 200
    assert before_login.get_json()["authenticated"] is False

    login = _login(client, user_id, password)
    assert login.status_code == 200
    assert login.get_json()["status"] == "logged_in"
    assert login.get_json()["user"]["id"] == user_id
    _assert_password_hash_not_exposed(login, stored_hash)

    cookie_name = app.config["SESSION_COOKIE_NAME"]
    assert client.get_cookie(cookie_name, domain="agent.example.test") is not None
    with client.session_transaction(base_url=_ORIGIN) as cookie_session:
        session_user = cookie_session["code_assist_user"]
        assert session_user["id"] == user_id
        assert "password_hash" not in session_user

    me = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert me.status_code == 200
    assert me.get_json()["authenticated"] is True
    assert me.get_json()["user"]["id"] == user_id
    _assert_password_hash_not_exposed(me, stored_hash)

    logout = client.post(
        "/api/code/auth/logout",
        base_url=_ORIGIN,
        headers=_csrf_headers(client),
    )
    assert logout.status_code == 200
    assert logout.get_json() == {"status": "logged_out"}
    assert client.get_cookie(cookie_name, domain="agent.example.test") is None
    _assert_password_hash_not_exposed(logout, stored_hash)

    after_logout = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert after_logout.status_code == 200
    assert after_logout.get_json()["authenticated"] is False

    relogin = _login(client, user_id, password)
    assert relogin.status_code == 200
    assert relogin.get_json()["user"]["id"] == user_id
    assert client.get_cookie(cookie_name, domain="agent.example.test") is not None
    _assert_password_hash_not_exposed(relogin, stored_hash)


@pytest.mark.scenario("REG-003")
def test_reg_003_authenticated_session_save_load_list_and_rename_preserve_response_shape(
    isolated_data_root: Path,
) -> None:
    user_id = f"reg-session-{uuid4().hex[:12]}"
    password = "Correct-Horse-Session-42"
    session_id = "regression-session-001"
    _create_personal_user(user_id, password)

    app = _make_app(register_session_routes)
    client = app.test_client()
    assert _login(client, user_id, password).status_code == 200

    payload = {
        "session_id": session_id,
        "user_id": "forged-session-owner",
        "title": "Initial session",
        "messages": [
            {"role": "user", "content": "Keep this personal session compatible."},
            {"role": "assistant", "content": "Stored."},
        ],
        "skills_used": ["code-review"],
        "uploaded_files": [
            {
                "name": "notes.md",
                "size": 123,
                "content_preview": "safe preview",
                "content_full": "sensitive full upload body",
            }
        ],
        "metadata": {"project": "personal"},
    }

    saved = client.post(
        "/api/code/session/save",
        base_url=_ORIGIN,
        json=payload,
        headers=_csrf_headers(client),
    )
    assert saved.status_code == 200
    assert saved.get_json() == {"session_id": session_id, "status": "saved"}

    stored_path = Path(config.USER_DATA_DIR) / user_id / "sessions" / f"{session_id}.json"
    assert stored_path.is_relative_to(isolated_data_root)
    assert stored_path.is_file()
    assert not (Path(config.SESSIONS_DIR) / f"{session_id}.json").exists()

    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    assert stored["session_id"] == session_id
    assert stored["title"] == "Initial session"
    assert stored["first_message"] == "Keep this personal session compatible."
    assert stored["message_count"] == 2
    assert stored["messages"] == payload["messages"]
    assert stored["skills_used"] == ["code-review"]
    assert stored["metadata"] == {"project": "personal"}
    assert stored["uploaded_files"] == [
        {"name": "notes.md", "size": 123, "content_preview": "safe preview"}
    ]
    assert "content_full" not in stored_path.read_text(encoding="utf-8")

    loaded = client.get(
        f"/api/code/session/load/{session_id}",
        base_url=_ORIGIN,
    )
    assert loaded.status_code == 200
    assert loaded.get_json() == stored

    listed = client.get("/api/code/session/list", base_url=_ORIGIN)
    assert listed.status_code == 200
    summaries = listed.get_json()["sessions"]
    summary = next(item for item in summaries if item["session_id"] == session_id)
    assert summary == {
        "session_id": session_id,
        "timestamp": stored["timestamp"],
        "title": "Initial session",
        "first_message": "Keep this personal session compatible.",
        "message_count": 2,
        "skills_used": ["code-review"],
    }

    renamed = client.post(
        f"/api/code/session/rename/{session_id}",
        base_url=_ORIGIN,
        json={"title": "  Renamed   personal   session  "},
        headers=_csrf_headers(client),
    )
    assert renamed.status_code == 200
    assert renamed.get_json() == {
        "status": "renamed",
        "session_id": session_id,
        "title": "Renamed personal session",
    }
    assert client.get(
        f"/api/code/session/load/{session_id}",
        base_url=_ORIGIN,
    ).get_json()["title"] == "Renamed personal session"


@pytest.mark.scenario("REG-003")
def test_reg_003_authenticated_legacy_knowledge_create_read_and_search_ignore_forged_user_id(
    isolated_data_root: Path,
) -> None:
    suffix = uuid4().hex[:12]
    owner_id = f"reg-knowledge-owner-{suffix}"
    forged_id = f"reg-knowledge-forged-{suffix}"
    password = "Correct-Horse-Knowledge-42"
    owner_token = "owner_scope_canary_4f82c1"
    forged_token = "forged_scope_canary_9b17d0"
    shared_filename = "shared-scope-note.md"

    _create_personal_user(owner_id, password)
    _create_personal_user(forged_id, "Correct-Horse-Forged-42")
    knowledge_store.save_file(
        shared_filename,
        f"Forged user's distinct content {forged_token}",
        user_id=forged_id,
    )
    knowledge_store.save_file(
        "forged-only.md",
        f"Only the forged store contains {forged_token}",
        user_id=forged_id,
    )

    app = _make_app(register_knowledge_routes)
    client = app.test_client()
    assert _login(client, owner_id, password).status_code == 200

    owner_content = f"Authenticated owner's personal content {owner_token}"
    created = client.post(
        "/api/code/knowledge/create",
        base_url=_ORIGIN,
        json={
            "filename": shared_filename,
            "content": owner_content,
            "user_id": forged_id,
        },
        headers=_csrf_headers(client),
    )
    assert created.status_code == 200
    assert created.get_json()["status"] == "created"
    assert created.get_json()["filename"] == shared_filename
    assert created.get_json()["size"] == len(owner_content)

    owner_stored = knowledge_store.view_file(shared_filename, user_id=owner_id)
    forged_stored = knowledge_store.view_file(shared_filename, user_id=forged_id)
    assert owner_stored is not None and owner_stored["content"] == owner_content
    assert forged_stored is not None and forged_token in forged_stored["content"]
    assert Path(owner_stored["path"]).is_relative_to(isolated_data_root)

    listed = client.get(
        "/api/code/knowledge/list",
        base_url=_ORIGIN,
        query_string={"user_id": forged_id},
    )
    assert listed.status_code == 200
    assert listed.get_json()["user_id"] == owner_id
    listed_names = {item["filename"] for item in listed.get_json()["items"]}
    assert shared_filename in listed_names
    assert "forged-only.md" not in listed_names

    viewed = client.get(
        f"/api/code/knowledge/view/{shared_filename}",
        base_url=_ORIGIN,
        query_string={"user_id": forged_id},
    )
    assert viewed.status_code == 200
    assert viewed.get_json()["content"] == owner_content

    owner_search = client.post(
        "/api/code/knowledge/search",
        base_url=_ORIGIN,
        json={"query": owner_token, "user_id": forged_id},
        headers=_csrf_headers(client),
    )
    assert owner_search.status_code == 200
    assert owner_search.get_json()["user_id"] == owner_id
    assert owner_search.get_json()["count"] == 1
    assert owner_search.get_json()["results"][0]["filename"] == shared_filename
    assert owner_token in owner_search.get_json()["results"][0]["content"]

    forged_search = client.post(
        "/api/code/knowledge/search",
        base_url=_ORIGIN,
        json={"query": forged_token, "user_id": forged_id},
        headers=_csrf_headers(client),
    )
    assert forged_search.status_code == 200
    assert forged_search.get_json() == {
        "query": forged_token,
        "results": [],
        "count": 0,
        "user_id": owner_id,
    }

    # Physical delete is intentionally not a REG-003 compatibility assertion.
    # The canonical replacement presents deactivation/restore semantics instead.
    assert knowledge_store.view_file(shared_filename, user_id=forged_id) == forged_stored
