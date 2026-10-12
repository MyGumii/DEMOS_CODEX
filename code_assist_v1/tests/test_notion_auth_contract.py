from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, jsonify, request
import pytest

from code_assist_v1 import (
    auth,
    routes_notion,
    routes_notion_bridge,
    routes_notion_workspace,
)
from code_assist_v1.knowledge_core.auth_security import CSRF_SESSION_KEY


pytestmark = pytest.mark.regression

_ORIGIN = "http://notion-auth.test"
_ATTACKER_ORIGIN = "http://attacker.test"
_USER_ID = "auth-contract-user"
_CSRF_TOKEN = "A" * 43


@dataclass(frozen=True, slots=True)
class RouteCase:
    name: str
    family: str
    method: str
    path: str
    payload: dict[str, Any] | None = None


_UNSAFE_CASES = (
    RouteCase(
        "main_atomic_save",
        "main",
        "POST",
        "/api/notion/pages/page-1/save",
        {
            "base_revision": 7,
            "operation_id": "op-auth-contract-1",
            "page": {"title": "Saved title", "icon": "", "cover": "", "settings": {}},
            "blocks": [],
        },
    ),
    RouteCase(
        "main_publish",
        "main",
        "POST",
        "/api/notion/pages/page-1/publish_second_brain",
        {"expected_source_snapshot_sha256": "b" * 64},
    ),
    RouteCase(
        "main_template",
        "main",
        "POST",
        "/api/notion/templates/second_brain",
        {},
    ),
    RouteCase(
        "main_session",
        "main",
        "POST",
        "/api/notion/pages/page-1/attach",
        {"session_id": "session-1"},
    ),
    RouteCase(
        "main_chat",
        "main",
        "POST",
        "/api/notion/import_chat",
        {"title": "Chat", "session_id": "session-1", "messages": [{"role": "user", "content": "hello"}]},
    ),
    RouteCase(
        "bridge_publish",
        "bridge",
        "POST",
        "/api/code/notion/pages/page-1/publish_second_brain",
        {"expected_source_snapshot_sha256": "c" * 64},
    ),
    RouteCase(
        "bridge_template",
        "bridge",
        "POST",
        "/api/code/notion/templates/second_brain",
        {},
    ),
    RouteCase(
        "bridge_session",
        "bridge",
        "POST",
        "/api/code/notion/session_attach",
        {"page_id": "page-1", "session_id": "session-1"},
    ),
    RouteCase(
        "bridge_chat",
        "bridge",
        "POST",
        "/api/code/notion/save_chat_summary",
        {"title": "Chat", "mode": "full", "messages": [{"role": "user", "content": "hello"}]},
    ),
    RouteCase(
        "bridge_thought",
        "bridge",
        "POST",
        "/api/code/notion/import_thought",
        {"title": "Thought", "content": "durable idea"},
    ),
    RouteCase(
        "workspace_navigation_preferences",
        "workspace",
        "PATCH",
        "/api/notion/workspaces/ws_auth/navigation/preferences",
        {
            "expected_preference_version": 0,
            "sidebar_width_px": 240,
            "sidebar_collapsed": False,
            "sections": [],
            "operation_id": "op-navigation-preferences",
        },
    ),
    RouteCase(
        "workspace_navigation_favorite_add",
        "workspace",
        "PUT",
        "/api/notion/workspaces/ws_auth/navigation/favorites",
        {
            "source_owner_subject_id": "owner",
            "source_page_id": "p_auth",
            "expected_favorite_version": 0,
            "operation_id": "op-navigation-favorite-add",
        },
    ),
    RouteCase(
        "workspace_navigation_favorite_remove",
        "workspace",
        "DELETE",
        "/api/notion/workspaces/ws_auth/navigation/favorites",
        {
            "source_owner_subject_id": "owner",
            "source_page_id": "p_auth",
            "expected_favorite_version": 0,
            "operation_id": "op-navigation-favorite-remove",
        },
    ),
)

_SAFE_CASES = (
    RouteCase(
        "main_publish_preview",
        "main",
        "GET",
        "/api/notion/pages/page-1/publish_preview",
    ),
    RouteCase(
        "main_session_pages",
        "main",
        "GET",
        "/api/notion/session_pages?session_id=session-1",
    ),
    RouteCase(
        "main_chat_sessions",
        "main",
        "GET",
        "/api/notion/chat/sessions",
    ),
    RouteCase(
        "bridge_pages",
        "bridge",
        "GET",
        "/api/code/notion/pages",
    ),
    RouteCase(
        "bridge_export",
        "bridge",
        "GET",
        "/api/code/notion/pages/page-1/export",
    ),
    RouteCase(
        "bridge_session_pages",
        "bridge",
        "GET",
        "/api/code/notion/session_pages?session_id=session-1",
    ),
    RouteCase(
        "workspace_navigation",
        "workspace",
        "GET",
        "/api/notion/workspaces/ws_auth/navigation",
    ),
)


def _stored_user() -> dict[str, Any]:
    return {
        "id": _USER_ID,
        "display_name": "Auth Contract User",
        "role": "user",
        "is_admin": False,
        "disabled": False,
        "must_change_password": False,
    }


def test_auth_credential_verification_retries_transient_false_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def transient_false_negative(_password_hash: str, _password: str) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr(
        auth,
        "_load_users",
        lambda: {
            "users": {
                _USER_ID: {
                    **_stored_user(),
                    "password_hash": "scrypt:synthetic",
                    "password_changed_at": 1,
                }
            }
        },
    )
    monkeypatch.setattr(auth, "check_password_hash", transient_false_negative)

    assert auth.verify_user(_USER_ID, "correct-password") is not None
    assert calls == 2


def test_auth_credential_verification_keeps_persistent_mismatch_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def persistent_mismatch(_password_hash: str, _password: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(
        auth,
        "_load_users",
        lambda: {
            "users": {
                _USER_ID: {
                    **_stored_user(),
                    "password_hash": "scrypt:synthetic",
                    "password_changed_at": 1,
                }
            }
        },
    )
    monkeypatch.setattr(auth, "check_password_hash", persistent_mismatch)

    assert auth.verify_user(_USER_ID, "wrong-password") is None
    assert calls == auth.CREDENTIAL_VERIFY_ATTEMPTS


def _make_app(
    family: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Flask:
    # Keep account validation real while replacing its persistent registry read.
    monkeypatch.setattr(
        auth,
        "_load_users",
        lambda: {"users": {_USER_ID: _stored_user()}},
    )
    app = Flask(f"notion-auth-contract-{family}-{uuid4().hex}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="notion-auth-contract-secret-key-over-32-bytes",
        SERVER_NAME=urlsplit(_ORIGIN).netloc,
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
        CODE_ASSIST_BROWSER_SECURITY_PRODUCTION=False,
    )
    auth.register_auth_routes(app)
    if family == "main":
        routes_notion.register_notion_routes(app)
    elif family == "bridge":
        routes_notion_bridge.register_notion_bridge_routes(app)
    elif family == "workspace":
        routes_notion_workspace.register_notion_workspace_routes(app)
    else:  # pragma: no cover - test table construction guard
        raise AssertionError(f"unknown route family: {family}")
    return app


def _authenticate(client: Any) -> None:
    with client.session_transaction() as cookie_session:
        cookie_session["code_assist_user"] = {
            "id": _USER_ID,
            "display_name": "Auth Contract User",
            "role": "user",
            "is_admin": False,
            "must_change_password": False,
        }
        cookie_session[CSRF_SESSION_KEY] = _CSRF_TOKEN


def _install_route_probe(
    app: Flask,
    case: RouteCase,
    reached: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    route_path = urlsplit(case.path).path
    adapter = app.url_map.bind(urlsplit(_ORIGIN).netloc, url_scheme="http")
    endpoint, expected_view_args = adapter.match(route_path, method=case.method)

    def probe(**view_args: Any):
        reached.append(
            {
                "endpoint": endpoint,
                "view_args": dict(view_args),
                "json": request.get_json(silent=True),
            }
        )
        return jsonify({"status": "handler_reached", "case": case.name})

    app.view_functions[endpoint] = probe
    return endpoint, expected_view_args


def _send(
    client: Any,
    case: RouteCase,
    *,
    headers: dict[str, str] | None = None,
):
    return client.open(
        case.path,
        method=case.method,
        base_url=_ORIGIN,
        headers=headers or {},
        json=case.payload,
    )


def _assert_security_denial(response: Any, reason: str) -> None:
    assert response.status_code == 403
    assert response.get_json() == {
        "error": "request security validation failed",
        "code": "FORBIDDEN",
        "reason": reason,
    }
    assert response.headers["Cache-Control"].startswith("no-store")


@pytest.mark.parametrize("case", _UNSAFE_CASES, ids=lambda case: case.name)
def test_notion_unsafe_auth_negative_matrix_and_valid_gate_pass(
    case: RouteCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(case.family, monkeypatch)
    reached: list[dict[str, Any]] = []
    expected_endpoint, expected_view_args = _install_route_probe(app, case, reached)

    anonymous = _send(app.test_client(), case)
    assert anonymous.status_code == 401
    assert anonymous.get_json() == {"error": "authentication required"}
    assert reached == []

    client = app.test_client()
    _authenticate(client)

    missing_origin = _send(
        client,
        case,
        headers={"X-CSRF-Token": _CSRF_TOKEN},
    )
    _assert_security_denial(missing_origin, "origin_missing")
    assert reached == []

    missing_csrf = _send(
        client,
        case,
        headers={"Origin": _ORIGIN},
    )
    _assert_security_denial(missing_csrf, "csrf_header_missing")
    assert reached == []

    cross_origin = _send(
        client,
        case,
        headers={"Origin": _ATTACKER_ORIGIN, "X-CSRF-Token": _CSRF_TOKEN},
    )
    _assert_security_denial(cross_origin, "cross_origin")
    assert reached == []

    valid = _send(
        client,
        case,
        headers={"Origin": _ORIGIN, "X-CSRF-Token": _CSRF_TOKEN},
    )
    assert valid.status_code == 200
    assert valid.get_json() == {"status": "handler_reached", "case": case.name}
    assert reached == [{
        "endpoint": expected_endpoint,
        "view_args": expected_view_args,
        "json": case.payload,
    }]


@pytest.mark.parametrize("case", _SAFE_CASES, ids=lambda case: case.name)
def test_notion_safe_routes_require_auth_but_not_origin_or_csrf(
    case: RouteCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(case.family, monkeypatch)
    reached: list[dict[str, Any]] = []
    expected_endpoint, expected_view_args = _install_route_probe(app, case, reached)

    anonymous = _send(app.test_client(), case)
    assert anonymous.status_code == 401
    assert anonymous.get_json() == {"error": "authentication required"}
    assert reached == []

    client = app.test_client()
    _authenticate(client)
    valid = _send(client, case)
    assert valid.status_code == 200
    assert valid.get_json() == {"status": "handler_reached", "case": case.name}
    assert reached == [{
        "endpoint": expected_endpoint,
        "view_args": expected_view_args,
        "json": None,
    }]


def test_public_page_get_and_exact_anonymous_form_exemptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app("main", monkeypatch)
    resolve_calls: list[str] = []
    submit_calls: list[tuple[str, str, dict[str, Any]]] = []
    duplicate_calls: list[tuple[str, str]] = []

    def resolve_public_page(token: str) -> dict[str, Any]:
        resolve_calls.append(token)
        return {
            "page": {
                "id": "page-1",
                "title": "Public auth contract",
                "icon": "",
                "owner_user_id": "owner-user",
                "updated_at": 1,
            },
            "blocks": [],
            "files": [],
            "public_link": {"allow_duplicate": True},
        }

    def submit_public_form(token: str, block_id: str, values: dict[str, Any]) -> None:
        submit_calls.append((token, block_id, dict(values)))

    def duplicate_public_page(user_id: str, token: str) -> dict[str, Any]:
        duplicate_calls.append((user_id, token))
        return {"page": {"id": "should-not-be-created"}}

    monkeypatch.setattr(routes_notion.ns, "resolve_public_page", resolve_public_page)
    monkeypatch.setattr(routes_notion.ns, "submit_public_form", submit_public_form)
    monkeypatch.setattr(routes_notion.ns, "duplicate_public_page", duplicate_public_page)

    client = app.test_client()
    page = client.get("/public/notion/public-token", base_url=_ORIGIN)
    assert page.status_code == 200
    assert "Public auth contract" in page.get_data(as_text=True)
    assert resolve_calls == ["public-token"]

    submitted = client.post(
        "/public/notion/public-token/forms/form-block",
        base_url=_ORIGIN,
        data={"f_title": "Anonymous answer", "ignored": "not a field"},
    )
    assert submitted.status_code == 303
    assert submitted.headers["Location"] == "/public/notion/public-token?submitted=1"
    assert submit_calls == [
        ("public-token", "form-block", {"title": "Anonymous answer"})
    ]

    duplicate = client.post(
        "/public/notion/public-token/duplicate",
        base_url=_ORIGIN,
    )
    assert duplicate.status_code == 302
    assert duplicate.headers["Location"] == "/login"
    assert duplicate_calls == []

    # Flask's route accepts a path block_id, but auth's anonymous exception is
    # deliberately one segment only. This locks the exemption to the exact URL.
    nested = client.post(
        "/public/notion/public-token/forms/nested/form-block",
        base_url=_ORIGIN,
        data={"f_title": "must not submit"},
    )
    assert nested.status_code == 302
    assert nested.headers["Location"] == "/login"
    assert submit_calls == [
        ("public-token", "form-block", {"title": "Anonymous answer"})
    ]
