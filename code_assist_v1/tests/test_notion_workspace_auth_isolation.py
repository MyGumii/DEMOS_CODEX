from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, jsonify, request
import pytest

from code_assist_v1.knowledge_core.auth_security import CSRF_SESSION_KEY


pytestmark = pytest.mark.regression

_ORIGIN = "http://notion-workspace-auth.test"
_ATTACKER_ORIGIN = "http://attacker.test"
_USER_ID = "workspace-auth-user"
_CSRF_TOKEN = "W" * 43


@dataclass(frozen=True, slots=True)
class _RouteCase:
    name: str
    method: str
    path: str
    payload: dict[str, Any] | None


_UNSAFE_CASES = (
    _RouteCase(
        "workspace_create",
        "POST",
        "/api/notion/workspaces",
        {"name": "Workspace", "operation_id": "op-workspace-create"},
    ),
    _RouteCase(
        "workspace_member_put",
        "PUT",
        "/api/notion/workspaces/ws_auth/members/target-user",
        {"base_version": 1, "operation_id": "op-member-put", "role": "member"},
    ),
    _RouteCase(
        "workspace_member_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/members/target-user",
        {"base_version": 1, "operation_id": "op-member-delete"},
    ),
    _RouteCase(
        "workspace_guest_put",
        "PUT",
        "/api/notion/workspaces/ws_auth/guests/target-user",
        {"base_version": 1, "operation_id": "op-guest-put"},
    ),
    _RouteCase(
        "workspace_guest_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/guests/target-user",
        {"base_version": 1, "operation_id": "op-guest-delete"},
    ),
    _RouteCase(
        "workspace_group_create",
        "POST",
        "/api/notion/workspaces/ws_auth/groups",
        {"name": "Group", "base_version": 1, "operation_id": "op-group-create"},
    ),
    _RouteCase(
        "workspace_group_member_put",
        "PUT",
        "/api/notion/workspaces/ws_auth/groups/grp_auth/members/target-user",
        {"base_version": 1, "operation_id": "op-group-member-put"},
    ),
    _RouteCase(
        "workspace_group_member_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/groups/grp_auth/members/target-user",
        {"base_version": 1, "operation_id": "op-group-member-delete"},
    ),
    _RouteCase(
        "workspace_teamspace_create",
        "POST",
        "/api/notion/workspaces/ws_auth/teamspaces",
        {
            "name": "Teamspace",
            "visibility": "closed",
            "base_version": 1,
            "operation_id": "op-teamspace-create",
        },
    ),
    _RouteCase(
        "workspace_teamspace_member_put",
        "PUT",
        "/api/notion/workspaces/ws_auth/teamspaces/ts_auth/members/target-user",
        {
            "role": "member",
            "base_version": 1,
            "operation_id": "op-teamspace-member-put",
        },
    ),
    _RouteCase(
        "workspace_teamspace_member_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/teamspaces/ts_auth/members/target-user",
        {"base_version": 1, "operation_id": "op-teamspace-member-delete"},
    ),
    _RouteCase(
        "workspace_navigation_preferences",
        "PATCH",
        "/api/notion/workspaces/ws_auth/navigation/preferences",
        {
            "expected_preference_version": 0,
            "sidebar_width_px": 240,
            "sidebar_collapsed": False,
            "sections": [
                {
                    "section": section,
                    "display_order": index,
                    "hidden": False,
                    "collapsed": False,
                    "item_limit": 10,
                }
                for index, section in enumerate(
                    ("favorites", "recents", "teamspaces", "shared", "private")
                )
            ],
            "operation_id": "op-navigation-preferences",
        },
    ),
    _RouteCase(
        "workspace_navigation_favorite_put",
        "PUT",
        "/api/notion/workspaces/ws_auth/navigation/favorites",
        {
            "source_owner_subject_id": _USER_ID,
            "source_page_id": "p_auth",
            "expected_favorite_version": 0,
            "operation_id": "op-navigation-favorite-put",
        },
    ),
    _RouteCase(
        "workspace_navigation_favorite_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/navigation/favorites",
        {
            "source_owner_subject_id": _USER_ID,
            "source_page_id": "p_auth",
            "expected_favorite_version": 0,
            "operation_id": "op-navigation-favorite-delete",
        },
    ),
    _RouteCase(
        "workspace_page_share_post",
        "POST",
        "/api/notion/workspaces/ws_auth/pages/page_auth/shares",
        {
            "principal_type": "subject",
            "principal_id": "target-user",
            "role": "view",
            "expected_workspace_epoch": 1,
            "expected_document_epoch": 1,
            "operation_id": "op-page-share-post",
        },
    ),
    _RouteCase(
        "workspace_page_share_delete",
        "DELETE",
        "/api/notion/workspaces/ws_auth/pages/page_auth/shares/subject/target-user",
        {
            "expected_workspace_epoch": 1,
            "expected_document_epoch": 1,
            "operation_id": "op-page-share-delete",
        },
    ),
)

_SAFE_GET_CASES = (
    _RouteCase("workspace_list", "GET", "/api/notion/workspaces", None),
    _RouteCase(
        "workspace_get",
        "GET",
        "/api/notion/workspaces/ws_auth",
        None,
    ),
    _RouteCase(
        "workspace_pages_list",
        "GET",
        "/api/notion/workspaces/ws_auth/pages",
        None,
    ),
    _RouteCase(
        "workspace_teamspace_list",
        "GET",
        "/api/notion/workspaces/ws_auth/teamspaces",
        None,
    ),
    _RouteCase(
        "workspace_navigation_get",
        "GET",
        "/api/notion/workspaces/ws_auth/navigation",
        None,
    ),
    _RouteCase(
        "workspace_page_collaboration_get",
        "GET",
        "/api/notion/workspaces/ws_auth/pages/page_auth/collaboration",
        None,
    ),
    _RouteCase(
        "workspace_page_shares_get",
        "GET",
        "/api/notion/workspaces/ws_auth/pages/page_auth/shares",
        None,
    ),
)

_EXPECTED_WORKSPACE_ROUTE_SHAPE = {
    (
        "/api/notion/workspaces",
        frozenset({"GET", "POST"}),
        "api_notion_workspaces",
    ),
    (
        "/api/notion/workspaces/<workspace_id>",
        frozenset({"GET"}),
        "api_notion_workspace_get",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages",
        frozenset({"GET", "POST"}),
        "api_notion_workspace_pages",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate",
        frozenset({"POST"}),
        "api_notion_workspace_page_duplicate",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/collaboration",
        frozenset({"GET"}),
        "api_notion_collaborative_page",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
        frozenset({"GET", "POST"}),
        "api_notion_collaborative_page_shares",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares/<principal_type>/<path:principal_id>",
        frozenset({"DELETE"}),
        "api_notion_collaborative_page_share_revoke",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/realtime-ticket",
        frozenset({"POST"}),
        "api_notion_collaborative_realtime_ticket",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
        frozenset({"GET", "POST"}),
        "api_notion_live_comments",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/editing/commands",
        frozenset({"POST"}),
        "api_notion_collaborative_editing_command",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
        frozenset({"GET", "POST"}),
        "api_notion_collaborative_revision_checkpoints",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
        frozenset({"DELETE", "PATCH"}),
        "api_notion_live_comment_item",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>/reactions",
        frozenset({"POST"}),
        "api_notion_live_comment_reaction",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/inbox",
        frozenset({"GET"}),
        "api_notion_live_inbox",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/inbox/<notification_id>",
        frozenset({"PATCH"}),
        "api_notion_live_inbox_item",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/trash",
        frozenset({"POST"}),
        "api_notion_workspace_page_trash",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/trash",
        frozenset({"GET"}),
        "api_notion_workspace_trash",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/trash/<path:page_id>/restore",
        frozenset({"POST"}),
        "api_notion_workspace_trash_restore",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/navigation",
        frozenset({"GET"}),
        "api_notion_workspace_navigation",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/navigation/preferences",
        frozenset({"PATCH"}),
        "api_notion_workspace_navigation_preferences",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/navigation/favorites",
        frozenset({"DELETE", "PUT"}),
        "api_notion_workspace_navigation_favorites",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
        frozenset({"DELETE", "PUT"}),
        "api_notion_workspace_member",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
        frozenset({"DELETE", "PUT"}),
        "api_notion_workspace_guest",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/groups",
        frozenset({"POST"}),
        "api_notion_workspace_group_create",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
        frozenset({"DELETE", "PUT"}),
        "api_notion_workspace_group_member",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/teamspaces",
        frozenset({"GET", "POST"}),
        "api_notion_workspace_teamspaces",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
        frozenset({"DELETE", "PUT"}),
        "api_notion_workspace_teamspace_member",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/imports/csv",
        frozenset({"POST"}),
        "api_notion_collaborative_import_csv",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/imports/markdown",
        frozenset({"POST"}),
        "api_notion_collaborative_import_markdown",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/backlinks",
        frozenset({"GET"}),
        "api_notion_collaborative_backlinks",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>",
        frozenset({"GET"}),
        "api_notion_collaborative_database",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/commands",
        frozenset({"POST"}),
        "api_notion_collaborative_database_command",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_csv",
        frozenset({"GET"}),
        "api_notion_collaborative_export_csv",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_html",
        frozenset({"GET"}),
        "api_notion_collaborative_export_html",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_markdown",
        frozenset({"GET"}),
        "api_notion_collaborative_export_markdown",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files",
        frozenset({"GET"}),
        "api_notion_collaborative_files",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/<file_id>",
        frozenset({"DELETE"}),
        "api_notion_collaborative_file_delete",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/policy",
        frozenset({"GET"}),
        "api_notion_collaborative_file_policy",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads",
        frozenset({"POST"}),
        "api_notion_collaborative_file_begin",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/<session_id>",
        frozenset({"GET"}),
        "api_notion_collaborative_file_status",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/<session_id>/<action>",
        frozenset({"POST"}),
        "api_notion_collaborative_file_transition",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/<session_id>/chunks/<int:chunk_index>",
        frozenset({"PUT"}),
        "api_notion_collaborative_file_chunk",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history",
        frozenset({"GET"}),
        "api_notion_collaborative_history",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/updates",
        frozenset({"GET"}),
        "api_notion_collaborative_page_updates",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/diff",
        frozenset({"GET"}),
        "api_notion_collaborative_history_diff",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/policy",
        frozenset({"GET"}),
        "api_notion_collaborative_history_policy",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/revisions/<int:revision>",
        frozenset({"GET"}),
        "api_notion_collaborative_history_revision",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/revisions/<int:revision>/restore",
        frozenset({"POST"}),
        "api_notion_collaborative_history_restore",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/reminders",
        frozenset({"GET"}),
        "api_notion_collaborative_reminders",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/search",
        frozenset({"GET"}),
        "api_notion_collaborative_search",
    ),
    (
        "/api/notion/workspaces/<workspace_id>/search/rebuild",
        frozenset({"POST"}),
        "api_notion_collaborative_search_rebuild",
    ),
}

_EXPECTED_BRIDGE_ROUTE_SHAPE = {
    (
        "/api/code/notion/pages",
        frozenset({"GET"}),
        "api_code_notion_pages",
    ),
    (
        "/api/code/notion/templates/second_brain",
        frozenset({"POST"}),
        "api_code_notion_second_brain_template",
    ),
    (
        "/api/code/notion/pages/<path:page_id>/export",
        frozenset({"GET"}),
        "api_code_notion_export",
    ),
    (
        "/api/code/notion/pages/<path:page_id>/publish_second_brain",
        frozenset({"POST"}),
        "api_code_notion_publish",
    ),
    (
        "/api/code/notion/session_attach",
        frozenset({"POST"}),
        "api_code_notion_session_attach",
    ),
    (
        "/api/code/notion/session_detach",
        frozenset({"POST"}),
        "api_code_notion_session_detach",
    ),
    (
        "/api/code/notion/session_pages",
        frozenset({"GET"}),
        "api_code_notion_session_pages",
    ),
    (
        "/api/code/notion/save_chat_summary",
        frozenset({"POST"}),
        "api_code_notion_save_chat_summary",
    ),
    (
        "/api/code/notion/import_thought",
        frozenset({"POST"}),
        "api_code_notion_import_thought",
    ),
}


def _stored_user() -> dict[str, Any]:
    return {
        "id": _USER_ID,
        "display_name": "Workspace Auth User",
        "role": "user",
        "is_admin": False,
        "disabled": False,
        "must_change_password": False,
    }


def _overlay_modules():
    auth = importlib.import_module("code_assist_v1.auth")
    workspace_routes = importlib.import_module(
        "code_assist_v1.routes_notion_workspace"
    )
    return auth, workspace_routes


def _make_overlay_app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    auth, workspace_routes = _overlay_modules()
    monkeypatch.setattr(
        auth,
        "_load_users",
        lambda: {"users": {_USER_ID: _stored_user()}},
    )

    def store_must_not_open():
        raise AssertionError("route probe must not open the workspace catalog")

    monkeypatch.setattr(workspace_routes, "_store", store_must_not_open)
    app = Flask(f"notion-workspace-auth-{uuid4().hex}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="notion-workspace-auth-secret-key-over-32-bytes",
        SERVER_NAME=urlsplit(_ORIGIN).netloc,
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
        CODE_ASSIST_BROWSER_SECURITY_PRODUCTION=False,
    )
    auth.register_auth_routes(app)
    workspace_routes.register_notion_workspace_routes(app)
    return app


def _authenticate(client: Any) -> None:
    with client.session_transaction() as cookie_session:
        cookie_session["code_assist_user"] = {
            "id": _USER_ID,
            "display_name": "Workspace Auth User",
            "role": "user",
            "is_admin": False,
            "must_change_password": False,
        }
        cookie_session[CSRF_SESSION_KEY] = _CSRF_TOKEN


def _install_probe(
    app: Flask,
    case: _RouteCase,
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
    case: _RouteCase,
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


def _route_shape(app: Flask, prefix: str) -> set[tuple[str, frozenset[str], str]]:
    return {
        (
            str(rule),
            frozenset(set(rule.methods or ()) - {"HEAD", "OPTIONS"}),
            str(rule.endpoint),
        )
        for rule in app.url_map.iter_rules()
        if str(rule).startswith(prefix)
    }


def _isolated_file_manifest(root: Path) -> dict[str, tuple[int, str]]:
    manifest: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        manifest[path.relative_to(root).as_posix()] = (
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
    return manifest


def _install_noop_module(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    callable_name: str,
    return_value: Any = None,
) -> None:
    module = ModuleType(module_name)
    setattr(module, callable_name, lambda *args, **kwargs: return_value)
    monkeypatch.setitem(sys.modules, module_name, module)


@pytest.mark.parametrize("case", _UNSAFE_CASES, ids=lambda case: case.name)
def test_workspace_overlay_unsafe_auth_and_request_security_matrix(
    case: _RouteCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_overlay_app(monkeypatch)
    reached: list[dict[str, Any]] = []
    expected_endpoint, expected_view_args = _install_probe(app, case, reached)

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

    missing_csrf = _send(client, case, headers={"Origin": _ORIGIN})
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
    assert reached == [
        {
            "endpoint": expected_endpoint,
            "view_args": expected_view_args,
            "json": case.payload,
        }
    ]


@pytest.mark.parametrize("case", _SAFE_GET_CASES, ids=lambda case: case.name)
def test_workspace_overlay_get_requires_cookie_auth_but_not_origin_or_csrf(
    case: _RouteCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_overlay_app(monkeypatch)
    reached: list[dict[str, Any]] = []
    expected_endpoint, expected_view_args = _install_probe(app, case, reached)

    anonymous = _send(app.test_client(), case)
    assert anonymous.status_code == 401
    assert anonymous.get_json() == {"error": "authentication required"}
    assert reached == []

    client = app.test_client()
    _authenticate(client)
    valid = _send(client, case)
    assert valid.status_code == 200
    assert valid.get_json() == {"status": "handler_reached", "case": case.name}
    assert reached == [
        {
            "endpoint": expected_endpoint,
            "view_args": expected_view_args,
            "json": None,
        }
    ]


def test_workspace_routes_are_notion_host_only_and_bridge_shape_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    isolated_data_root: Path,
) -> None:
    from code_assist_v1 import config

    isolated_root = isolated_data_root.resolve()
    assert Path(config.DATA_ROOT or "").resolve() == isolated_root
    before = _isolated_file_manifest(isolated_root)

    app_code = importlib.import_module("code_assist_v1.app_code")
    app_notion = importlib.import_module("code_assist_v1.app_notion")
    auth = importlib.import_module("code_assist_v1.auth")
    notion_store = importlib.import_module("code_assist_v1.notion_store")
    notion_workspace_store = importlib.import_module(
        "code_assist_v1.notion_workspace_store"
    )
    workspace_routes = importlib.import_module(
        "code_assist_v1.routes_notion_workspace"
    )

    catalog_path = Path(
        notion_workspace_store.catalog_path_for_user_data_root(
            notion_store.USER_DATA_DIR
        )
    ).resolve()
    catalog_path.relative_to(isolated_root)
    catalog_before = catalog_path.exists()

    monkeypatch.setattr(
        auth,
        "ensure_secret_key",
        lambda: "host-isolation-secret-key-over-32-bytes",
    )
    monkeypatch.setattr(app_code, "_register_gguf_models", lambda autoload=None: [])

    # Build both real host composition roots while replacing unrelated route
    # families with inert registrars. This keeps the assertion focused on the
    # actual workspace/bridge registration and avoids constructing workers,
    # schedulers, model registries, or file-backed compatibility services.
    for module_name, callable_name in (
        ("code_assist_v1.routes_models", "register_model_routes"),
        ("code_assist_v1.routes_skills", "register_skill_routes"),
        ("code_assist_v1.routes_knowledge", "register_knowledge_routes"),
        ("code_assist_v1.routes_workspace", "register_workspace_routes"),
        ("code_assist_v1.routes_chat_stream", "register_chat_stream_routes"),
        ("code_assist_v1.routes_sessions", "register_session_routes"),
        ("code_assist_v1.routes_second_brain", "register_second_brain_routes"),
        ("code_assist_v1.routes_memory", "register_memory_routes"),
        ("code_assist_v1.routes_agent_harness", "register_agent_harness_routes"),
        ("code_assist_v1.routes_project_context", "register_project_context_routes"),
        ("code_assist_v1.routes_jobs", "register_job_routes"),
        ("code_assist_v1.routes_outlook", "register_outlook_routes"),
    ):
        _install_noop_module(monkeypatch, module_name, callable_name)
    _install_noop_module(
        monkeypatch,
        "code_assist_v1.seed",
        "seed_skills_if_empty",
        0,
    )
    _install_noop_module(
        monkeypatch,
        "code_assist_v1.harness_setup",
        "setup_harness",
        False,
    )

    def store_must_not_open():
        raise AssertionError("route registration must not open the workspace catalog")

    monkeypatch.setattr(workspace_routes, "_store", store_must_not_open)

    notion_app = app_notion.create_app()
    code_app = app_code.create_app()

    assert _route_shape(notion_app, "/api/notion/workspaces") == (
        _EXPECTED_WORKSPACE_ROUTE_SHAPE
    )
    assert _route_shape(code_app, "/api/notion/workspaces") == set()
    assert _route_shape(code_app, "/api/code/notion") == (
        _EXPECTED_BRIDGE_ROUTE_SHAPE
    )

    assert catalog_path.exists() is catalog_before
    assert _isolated_file_manifest(isolated_root) == before


def test_app_code_factory_import_graph_excludes_workspace_overlay_modules() -> None:
    """Build the Code host in a fresh process and prove NP-2A stays unloaded."""
    project_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import importlib
        import sys
        from types import ModuleType, SimpleNamespace

        def install_noop(module_name, callable_name, return_value=None):
            module = ModuleType(module_name)
            setattr(
                module,
                callable_name,
                lambda *args, **kwargs: return_value,
            )
            sys.modules[module_name] = module

        app_code = importlib.import_module("code_assist_v1.app_code")
        auth = importlib.import_module("code_assist_v1.auth")
        knowledge_core = importlib.import_module("code_assist_v1.knowledge_core")

        auth.ensure_secret_key = lambda: "app-code-import-boundary-secret-over-32-bytes"
        auth.register_auth_routes = lambda app: None
        knowledge_core.register_knowledge_core = lambda *args, **kwargs: SimpleNamespace(
            registered_route_groups=()
        )
        app_code._register_gguf_models = lambda autoload=None: []

        for module_name, callable_name in (
            ("code_assist_v1.routes_models", "register_model_routes"),
            ("code_assist_v1.routes_skills", "register_skill_routes"),
            ("code_assist_v1.routes_knowledge", "register_knowledge_routes"),
            ("code_assist_v1.routes_workspace", "register_workspace_routes"),
            ("code_assist_v1.routes_chat_stream", "register_chat_stream_routes"),
            ("code_assist_v1.routes_sessions", "register_session_routes"),
            ("code_assist_v1.routes_second_brain", "register_second_brain_routes"),
            ("code_assist_v1.routes_memory", "register_memory_routes"),
            ("code_assist_v1.routes_agent_harness", "register_agent_harness_routes"),
            ("code_assist_v1.routes_project_context", "register_project_context_routes"),
            ("code_assist_v1.routes_jobs", "register_job_routes"),
        ):
            install_noop(module_name, callable_name)
        install_noop("code_assist_v1.seed", "seed_skills_if_empty", 0)
        install_noop("code_assist_v1.harness_setup", "setup_harness", False)

        app = app_code.create_app()
        forbidden = {
            name
            for name in sys.modules
            if name == "code_assist_v1.routes_notion_workspace"
            or name.startswith("code_assist_v1.notion_workspace")
        }
        assert forbidden == set(), sorted(forbidden)

        workspace_routes = {
            str(rule)
            for rule in app.url_map.iter_rules()
            if str(rule).startswith("/api/notion/workspaces")
        }
        bridge_routes = {
            str(rule)
            for rule in app.url_map.iter_rules()
            if str(rule).startswith("/api/code/notion")
        }
        assert workspace_routes == set(), sorted(workspace_routes)
        assert len(bridge_routes) == 9, sorted(bridge_routes)
        """
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
