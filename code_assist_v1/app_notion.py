"""
Standalone entry point for the Notion-like workspace.

Run:
    python -m code_assist_v1.app_notion
    python -m code_assist_v1.app_notion --codex
    python -m code_assist_v1.app_notion --port 10012

URL:
    http://localhost:10012
"""
from __future__ import annotations

import argparse
import atexit
import os
from datetime import timedelta
from typing import Callable, Mapping

os.environ.setdefault("CODE_ASSIST_STANDALONE", "1")

from code_assist_v1.model_routing import apply_codex_launch_policy


def _apply_boot_args() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--port", type=int)
    args, _unknown = parser.parse_known_args()
    apply_codex_launch_policy(args.codex)
    if args.port:
        os.environ["CODE_ASSIST_NOTION_PORT"] = str(args.port)


_apply_boot_args()

from flask import Flask, Response, redirect, request, send_from_directory

from code_assist_v1.config import STATIC_DIR


NOTION_PORT = int(os.environ.get("CODE_ASSIST_NOTION_PORT", "10012"))
NOTION_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notion_static")


def _secure_session_cookie() -> bool:
    configured = os.environ.get("CODE_ASSIST_SESSION_COOKIE_SECURE", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return os.environ.get("CODE_ASSIST_MS_REDIRECT_URI", "").strip().lower().startswith("https://")


def _is_nonadopted_internal_surface(path: str) -> bool:
    """Return whether a legacy route is intentionally absent from the internal product."""
    return (
        path == "/api/notion/ai/ask"
        or path.startswith("/api/notion/outlook")
        or path.startswith("/public/notion/")
        or "/public_link" in path
        or "/forms/" in path
    )


def create_app(
    *,
    knowledge_core_services: object | None = None,
    knowledge_core_publication_bindings: object | None = None,
    knowledge_core_environ: Mapping[str, str] | None = None,
    production_health_provider: Callable[
        [], tuple[Mapping[str, object], int]
    ] | None = None,
) -> Flask:
    from code_assist_v1.model_runtime import assert_web_process_role

    runtime_role = assert_web_process_role()
    app = Flask(
        __name__,
        static_folder=STATIC_DIR,
        static_url_path="/static",
    )
    from code_assist_v1.knowledge_core import HostKind, register_knowledge_core

    register_knowledge_core(
        app,
        HostKind.NOTION,
        environ=knowledge_core_environ,
        services=knowledge_core_services,
        publication_route_bindings=knowledge_core_publication_bindings,
    )
    from code_assist_v1.auth import ensure_secret_key, register_auth_routes
    from code_assist_v1.internal_llm import codex_internal_enabled
    from code_assist_v1.routes_jobs import register_job_routes
    from code_assist_v1.routes_notion import register_notion_routes
    from code_assist_v1.routes_notion_ai_notes import register_notion_ai_notes_routes
    from code_assist_v1.routes_notion_document_ai import register_notion_document_ai_routes
    from code_assist_v1.routes_notion_ai_chat import register_notion_ai_chat_routes
    from code_assist_v1.routes_notion_workspace import register_notion_workspace_routes
    from code_assist_v1.routes_notion_files import register_notion_file_routes
    from code_assist_v1.routes_notion_history import register_notion_history_routes
    from code_assist_v1.routes_notion_search import register_notion_search_routes
    from code_assist_v1.routes_outlook import register_outlook_routes

    app.secret_key = ensure_secret_key()
    app.config.update(
        SESSION_COOKIE_NAME="session",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_secure_session_cookie(),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

    # Register the product-surface fence before authentication so excluded
    # features are a non-oracle 404 for both anonymous and signed-in callers.
    @app.before_request
    def _reject_nonadopted_internal_surfaces():
        from code_assist_v1.notion_collaboration import iam_runtime

        path = request.path or ""
        if not iam_runtime.runtime_requested() or not _is_nonadopted_internal_surface(path):
            return None
        return {
            "code": "route_not_found",
            "error": "Notion route not found",
            "ok": False,
        }, 404

    register_auth_routes(app)
    register_job_routes(app)
    register_notion_routes(app)
    register_notion_ai_notes_routes(app)
    register_notion_document_ai_routes(app)
    register_notion_ai_chat_routes(app)
    register_notion_workspace_routes(app)
    register_notion_file_routes(app)
    register_notion_history_routes(app)
    register_notion_search_routes(app)
    register_outlook_routes(app)

    @app.after_request
    def _disable_frontend_cache(response):
        path = request.path or ""
        if path in {"/", "/workspace", "/shared", "/login", "/healthz", "/ai"} or path.startswith(("/notion_static/", "/ai/", "/database/")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        if path.startswith("/api/notion/outlook/oauth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.route("/")
    @app.route("/workspace")
    @app.route("/ai")
    @app.route("/ai/<chat_id>")
    @app.route("/shared")
    @app.route("/page/<path:_page_id>")
    @app.route("/database/<workspace_id>/<database_id>")
    def index(_page_id: str | None = None, chat_id: str | None = None,
              workspace_id: str | None = None, database_id: str | None = None):
        return send_from_directory(NOTION_STATIC_DIR, "index.html")

    @app.route("/notion_static/<path:filename>")
    def notion_static(filename):
        return send_from_directory(NOTION_STATIC_DIR, filename)

    @app.route("/login")
    def login():
        return send_from_directory(STATIC_DIR, "login.html")

    @app.route("/code/")
    def code_alias():
        host = (request.host or "localhost").split(":", 1)[0] or "localhost"
        code_port = 10111 if request.host.rsplit(":", 1)[-1] == "10112" else 10011
        return redirect(f"http://{host}:{code_port}", code=302)

    @app.route("/healthz")
    def healthz():
        if production_health_provider is not None:
            try:
                payload, status_code = production_health_provider()
                if (
                    not isinstance(payload, Mapping)
                    or isinstance(status_code, bool)
                    or status_code not in {200, 503}
                ):
                    raise ValueError("invalid production health response")
                return dict(payload), status_code
            except Exception:
                return {
                    "gate": {
                        "reason_codes": ["health_observation_failed"],
                        "status": "not_ready",
                    },
                    "report_schema": "code-assist.production-health",
                    "schema_version": 1,
                    "service_id": "CodeAssist-Notion",
                }, 503
        return {
            "status": "ok",
            "service": "notion_workspace",
            "mode": (
                "production"
                if runtime_role.value == "web"
                else "standalone"
            ),
            "ai_provider": "hynix_gemma4",
            "ai_provider_order": [
                "company_api",
                "local_gguf",
                *(["codex"] if codex_internal_enabled() else []),
            ],
            "ai_fallback": "local_extractive",
        }

    @app.route("/favicon.ico")
    def favicon():
        return Response(status=204)

    print("=" * 60)
    print("  Notion Workspace")
    print("=" * 60)
    print(f"  URL: http://localhost:{NOTION_PORT}")
    print("  auth: shared with code_assist_v1")
    print("=" * 60)

    return app


def main() -> None:
    app = create_app()
    from code_assist_v1.outlook_integration import start_outlook_scheduler, stop_outlook_scheduler

    start_outlook_scheduler()
    atexit.register(stop_outlook_scheduler)
    app.run(host="0.0.0.0", port=NOTION_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
