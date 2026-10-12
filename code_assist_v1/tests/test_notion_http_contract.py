from __future__ import annotations

from typing import Any
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import notion_http
from code_assist_v1 import notion_store
from code_assist_v1 import routes_notion
from code_assist_v1 import routes_notion_bridge
from code_assist_v1 import outlook_integration


def _client(register_routes: Any):
    app = Flask(f"notion-http-contract-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="notion-http-contract")
    register_routes(app)
    return app.test_client()


@pytest.mark.unit
def test_known_error_classification_adds_stable_code_without_breaking_legacy_error() -> None:
    denied = notion_http.classify_error(PermissionError("edit permission required"))
    missing = notion_http.classify_error(FileNotFoundError("page not found"))
    invalid = notion_http.classify_error(ValueError("page_id required"))
    revision = notion_http.classify_error(notion_store.PageRevisionConflict(9))
    operation = notion_http.classify_error(
        notion_store.OperationIdConflict("operation-1", 10)
    )

    assert (denied.status, denied.payload) == (
        403,
        {
            "error": "edit permission required",
            "code": "permission_denied",
            "message": "edit permission required",
        },
    )
    assert missing.status == 404
    assert missing.payload["error"] == "page not found"
    assert missing.payload["code"] == "not_found"
    assert invalid.status == 400
    assert invalid.payload["code"] == "invalid_request"
    assert revision.payload == {
        "error": "revision_conflict",
        "code": "revision_conflict",
        "message": "page revision conflict; current revision is 9",
        "current_revision": 9,
    }
    assert operation.payload["error"] == "operation_id_conflict"
    assert operation.payload["code"] == "operation_id_conflict"
    assert operation.payload["operation_id"] == "operation-1"
    assert operation.payload["current_revision"] == 10


@pytest.mark.unit
def test_unhandled_exception_payload_never_contains_internal_exception_text() -> None:
    internal = notion_http.classify_error(
        RuntimeError(r"database failed at G:\private\user\notion.db")
    )

    assert internal.status == 500
    assert internal.log_unhandled is True
    assert internal.payload == {
        "error": "internal_error",
        "code": "internal_error",
        "message": "The Notion request could not be completed.",
    }
    assert "private" not in str(internal.payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    (
        FileNotFoundError(2, "No such file", r"G:\private\files\secret.bin"),
        PermissionError(13, "Access denied", r"G:\private\files\secret.bin"),
    ),
)
def test_os_errors_with_filenames_never_reflect_paths(exc: OSError) -> None:
    result = notion_http.classify_error(exc)

    assert result.status == 500
    assert result.log_unhandled is True
    assert result.payload == {
        "error": "internal_error",
        "code": "internal_error",
        "message": "The Notion request could not be completed.",
    }
    assert "secret.bin" not in str(result.payload)


@pytest.mark.unit
def test_ai_fallback_diagnostics_never_reflect_provider_details() -> None:
    secret = r"hynix_api:gemma4-31b: failed at G:\private\models\config.json"

    result = notion_http.public_fallback_errors({"fallback_errors": [secret]})

    assert result == ["AI provider unavailable"]
    assert "private" not in str(result)


@pytest.mark.unit
def test_route_validation_response_has_stable_code_and_no_store_headers() -> None:
    app = Flask("notion-http-validation")
    with app.test_request_context("/api/notion/test"):
        response, status = notion_http.validation_response("messages required")

    assert status == 400
    assert response.get_json() == {
        "error": "messages required",
        "code": "invalid_request",
        "message": "messages required",
    }
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path,delegate_name",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/pages",
            "create_page",
            id="notion-main",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/session_attach",
            "attach_page_to_session",
            id="notion-bridge",
        ),
    ),
)
@pytest.mark.parametrize(
    "body",
    (
        pytest.param('{"broken"', id="malformed"),
        pytest.param("[]", id="array"),
        pytest.param("null", id="null"),
        pytest.param('"text"', id="scalar"),
    ),
)
def test_non_object_json_is_400_before_any_notion_delegate(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
    delegate_name: str,
    body: str,
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(route_module, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        route_module.ns,
        delegate_name,
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    client = _client(register_routes)

    response = client.post(path, data=body, content_type="application/json")

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "request body must be a valid JSON object",
        "code": "invalid_json_body",
        "message": "request body must be a valid JSON object",
    }
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert calls == []


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path,delegate_name",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/pages/page-1",
            "get_page",
            id="notion-main",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/pages/page-1/export",
            "export_page_markdown",
            id="notion-bridge",
        ),
    ),
)
def test_unhandled_route_exception_is_logged_but_not_returned(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
    delegate_name: str,
) -> None:
    secret = r"G:\private\account\notion.db"
    monkeypatch.setattr(route_module, "_uid", lambda: "http-actor")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"sqlite failure at {secret}")

    monkeypatch.setattr(route_module.ns, delegate_name, fail)
    client = _client(register_routes)

    response = client.get(path)

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "internal_error",
        "code": "internal_error",
        "message": "The Notion request could not be completed.",
    }
    assert secret not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path,delegate_name",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/session_pages?session_id=session-1",
            "list_session_pages",
            id="notion-main-session-list",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/session_pages?session_id=session-1",
            "list_session_pages",
            id="notion-bridge-session-list",
        ),
    ),
)
def test_session_list_unhandled_failure_uses_json_envelope(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
    delegate_name: str,
) -> None:
    monkeypatch.setattr(route_module, "_uid", lambda: "http-actor")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private session path")

    monkeypatch.setattr(route_module.ns, delegate_name, fail)
    response = _client(register_routes).get(path)

    assert response.status_code == 500
    assert response.is_json
    assert response.get_json()["code"] == "internal_error"
    assert "private session path" not in response.get_data(as_text=True)


@pytest.mark.regression
def test_public_page_internal_failure_uses_fixed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = r"G:\private\public\notion.db"

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"failed at {secret}")

    monkeypatch.setattr(routes_notion.ns, "resolve_public_page", fail)
    response = _client(routes_notion.register_notion_routes).get(
        "/public/notion/public-token"
    )

    assert response.status_code == 500
    assert response.get_data(as_text=True) == "Public page could not be loaded"
    assert secret not in response.get_data(as_text=True)


@pytest.mark.regression
@pytest.mark.parametrize(
    "path,delegate_name",
    (
        ("/api/notion/files/file-1", "get_file"),
        ("/public/notion/public-token/files/file-1", "get_public_file"),
    ),
)
def test_file_delivery_race_never_reflects_physical_path(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    delegate_name: str,
) -> None:
    secret = r"G:\private\files\missing-secret.bin"
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        routes_notion.ns,
        delegate_name,
        lambda *args, **kwargs: {
            "path": secret,
            "original_name": "safe-name.bin",
            "mime_type": "application/octet-stream",
        },
    )

    def fail_send(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file", secret)

    monkeypatch.setattr(routes_notion, "send_file", fail_send)
    response = _client(routes_notion.register_notion_routes).get(path)

    assert response.status_code == 500
    assert response.get_json()["code"] == "internal_error"
    assert secret not in response.get_data(as_text=True)


@pytest.mark.regression
def test_html_export_render_failure_uses_sanitized_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = r"G:\private\templates\notion.html"
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        routes_notion.ns,
        "get_page",
        lambda *args, **kwargs: {"page": {"id": "page-1", "title": "Page"}},
    )

    def fail_render(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"template failed at {secret}")

    monkeypatch.setattr(routes_notion, "_public_page_html", fail_render)
    response = _client(routes_notion.register_notion_routes).get(
        "/api/notion/pages/page-1/export_html"
    )

    assert response.status_code == 500
    assert response.get_json()["code"] == "internal_error"
    assert secret not in response.get_data(as_text=True)


@pytest.mark.regression
def test_explicit_ai_page_failure_does_not_fall_back_to_other_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fail_export(user_id: str, page_id: str, **kwargs: Any) -> Any:
        calls.append(page_id)
        raise FileNotFoundError("requested page not found")

    monkeypatch.setattr(routes_notion.ns, "export_page_markdown", fail_export)
    monkeypatch.setattr(
        routes_notion.ns,
        "list_pages",
        lambda *args, **kwargs: pytest.fail("must not search another page"),
    )

    with pytest.raises(FileNotFoundError, match="requested page not found"):
        routes_notion._notion_ai_context(
            "http-actor",
            "question",
            "explicit-page",
        )

    assert calls == ["explicit-page"]


@pytest.mark.regression
def test_ai_route_explicit_page_failure_is_returned_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        routes_notion.ns,
        "export_page_markdown",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FileNotFoundError("requested page not found")
        ),
    )
    monkeypatch.setattr(
        routes_notion.ns,
        "list_pages",
        lambda *args, **kwargs: pytest.fail("must not search another page"),
    )
    monkeypatch.setattr(
        routes_notion,
        "_call_notion_ai",
        lambda *args, **kwargs: pytest.fail("provider must not be called"),
    )

    response = _client(routes_notion.register_notion_routes).post(
        "/api/notion/ai/ask",
        json={"question": "question", "page_id": "explicit-page"},
    )

    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"
    assert response.get_json()["message"] == "requested page not found"


@pytest.mark.regression
def test_inbox_marks_outlook_failure_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(routes_notion.ns, "list_inbox_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        outlook_integration,
        "list_notifications",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("outlook offline")),
    )

    response = _client(routes_notion.register_notion_routes).get("/api/notion/inbox")

    assert response.status_code == 200
    assert response.get_json() == {
        "items": [],
        "count": 0,
        "degraded": True,
        "degraded_sources": ["outlook_notifications"],
    }


@pytest.mark.regression
def test_search_marks_unreadable_page_content_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        routes_notion.ns,
        "list_pages",
        lambda *args, **kwargs: [
            {
                "id": "page-1",
                "title": "needle",
                "owner_user_id": "http-actor",
                "scope": "own",
                "updated_at": 1,
            }
        ],
    )
    monkeypatch.setattr(
        routes_notion.ns,
        "export_page_markdown",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("corrupt page")),
    )

    response = _client(routes_notion.register_notion_routes).get(
        "/api/notion/search?q=needle"
    )

    assert response.status_code == 200
    assert response.get_json()["degraded"] is True
    assert response.get_json()["degraded_sources"] == ["page_content"]
    assert response.get_json()["count"] == 1


@pytest.mark.regression
def test_overview_marks_optional_sources_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")
    monkeypatch.setattr(routes_notion.ns, "list_pages", lambda *args, **kwargs: [])
    monkeypatch.setattr(routes_notion, "_session_list", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        routes_notion.sb,
        "list_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("source offline")),
    )
    monkeypatch.setattr(
        routes_notion.sb,
        "list_thoughts",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("thought offline")),
    )
    monkeypatch.setattr(
        routes_notion.ns,
        "list_calendar_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("calendar offline")),
    )

    response = _client(routes_notion.register_notion_routes).get(
        "/api/notion/overview"
    )

    assert response.status_code == 200
    assert response.get_json()["degraded"] is True
    assert response.get_json()["degraded_sources"] == [
        "calendar_events",
        "second_brain_sources",
        "second_brain_thoughts",
    ]


@pytest.mark.unit
def test_ai_related_page_failure_is_reported_without_exposing_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def export(user_id: str, page_id: str, **kwargs: Any) -> dict[str, Any]:
        if page_id == "current-page":
            return {
                "page": {"id": page_id, "title": "Current"},
                "markdown": "current context",
            }
        raise RuntimeError(r"failed at G:\private\related.db")

    monkeypatch.setattr(routes_notion.ns, "export_page_markdown", export)
    monkeypatch.setattr(
        routes_notion.ns,
        "list_pages",
        lambda *args, **kwargs: [
            {"id": "current-page", "title": "Current"},
            {"id": "related-page", "title": "Related"},
        ],
    )

    context, used, degraded_sources = routes_notion._notion_ai_context(
        "http-actor",
        "related",
        "current-page",
    )

    assert "current context" in context
    assert used == ["current-page"]
    assert degraded_sources == ["related_page_content"]
    assert "private" not in str(degraded_sources)


@pytest.mark.regression
@pytest.mark.parametrize(
    "path,method,expected_status,expected_code",
    (
        ("/api/notion/route-that-does-not-exist", "get", 404, "route_not_found"),
        ("/api/notion/pages", "put", 405, "method_not_allowed"),
        ("/api/code/notion/route-that-does-not-exist", "get", 404, "route_not_found"),
    ),
)
def test_notion_framework_errors_use_scoped_json_envelope(
    path: str,
    method: str,
    expected_status: int,
    expected_code: str,
) -> None:
    register = (
        routes_notion_bridge.register_notion_bridge_routes
        if path.startswith("/api/code/notion")
        else routes_notion.register_notion_routes
    )
    client = _client(register)

    response = getattr(client, method)(path)

    assert response.status_code == expected_status
    assert response.is_json
    assert response.get_json()["code"] == expected_code
    assert response.get_json()["message"]
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


@pytest.mark.regression
def test_notion_oversized_request_uses_json_envelope() -> None:
    app = Flask(f"notion-http-oversized-{uuid4().hex}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="notion-http-contract",
        MAX_CONTENT_LENGTH=16,
    )
    routes_notion.register_notion_routes(app)

    response = app.test_client().post(
        "/api/notion/pages",
        data='{"title":"this payload is too large"}',
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "error": "Request payload is too large",
        "code": "payload_too_large",
        "message": "Request payload is too large",
    }
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


@pytest.mark.regression
def test_framework_handler_does_not_change_non_notion_404() -> None:
    client = _client(routes_notion.register_notion_routes)

    response = client.get("/api/unrelated/route-that-does-not-exist")

    assert response.status_code == 404
    assert response.content_type.startswith("text/html")
    assert b"<!doctype html>" in response.data.lower()


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,method,path,body,delegate_name",
    (
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "put",
            "/api/notion/pages/page-1/blocks",
            {},
            "replace_blocks",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "put",
            "/api/notion/pages/page-1/blocks",
            {"blocks": "erase everything"},
            "replace_blocks",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "post",
            "/api/notion/pages/page-1/forms/form-1/submit",
            {"values": []},
            "submit_form",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "post",
            "/api/notion/pages",
            {"add_to_parent_blocks": "false"},
            "create_page",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "post",
            "/api/notion/pages",
            {"settings": []},
            "create_page",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "patch",
            "/api/notion/pages/page-1/public_link",
            {"allow_duplicate": "false"},
            "update_public_link_options",
        ),
        (
            routes_notion,
            routes_notion.register_notion_routes,
            "post",
            "/api/notion/pages/page-1/shares",
            {"shared_user_id": "user-2", "role": "administrator"},
            "share_page",
        ),
        (
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "post",
            "/api/code/notion/save_chat_summary",
            {"messages": [{"role": "user", "content": "hello"}], "mode": "magic"},
            "create_page_from_markdown",
        ),
    ),
)
def test_invalid_mutation_field_types_are_rejected_before_delegate(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    method: str,
    path: str,
    body: dict[str, Any],
    delegate_name: str,
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(route_module, "_uid", lambda: "http-actor")
    monkeypatch.setattr(
        route_module.ns,
        delegate_name,
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = getattr(_client(register_routes), method)(path, json=body)

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_request"
    assert response.get_json()["message"]
    assert calls == []


@pytest.mark.regression
def test_explicit_false_boolean_reaches_delegate_as_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[bool] = []
    monkeypatch.setattr(routes_notion, "_uid", lambda: "http-actor")

    def update(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs["allow_duplicate"])
        return {
            "page_id": "page-1",
            "enabled": True,
            "allow_duplicate": kwargs["allow_duplicate"],
            "token": "token",
        }

    monkeypatch.setattr(routes_notion.ns, "update_public_link_options", update)
    response = _client(routes_notion.register_notion_routes).patch(
        "/api/notion/pages/page-1/public_link",
        json={"allow_duplicate": False},
    )

    assert response.status_code == 200
    assert captured == [False]
    assert response.get_json()["allow_duplicate"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "blocks,match",
    (
        (["text"], "block 1 must be an object"),
        ([{"type": "unknown-widget"}], "unsupported block type"),
        ([{"type": "paragraph", "props": []}], "props must be an object"),
    ),
)
def test_block_preparation_rejects_lossy_coercion(
    blocks: list[Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        notion_store._prepare_blocks(blocks)
