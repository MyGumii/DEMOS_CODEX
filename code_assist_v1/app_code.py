"""
Standalone entry point for code_assist_v1.

Run:
    python -m code_assist_v1.app_code
    python -m code_assist_v1.app_code --codex

URL:
    http://localhost:10011
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import timedelta
from typing import Callable, Mapping

from code_assist_v1.model_runtime import (
    assert_web_process_role,
    gguf_catalog_enabled,
)
from code_assist_v1.model_routing import apply_codex_launch_policy

os.environ.setdefault("CODE_ASSIST_STANDALONE", "1")


def _apply_boot_args() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--port", type=int)
    args, _unknown = parser.parse_known_args()
    apply_codex_launch_policy(args.codex)
    if args.port:
        os.environ["CODE_ASSIST_PORT"] = str(args.port)


_apply_boot_args()

from flask import Flask, Response, redirect, request, send_from_directory

from code_assist_v1.config import (
    API_TOKEN,
    PORT,
    STATIC_DIR,
    VRAM_BUDGET_GB,
)


def _secure_session_cookie() -> bool:
    configured = os.environ.get("CODE_ASSIST_SESSION_COOKIE_SECURE", "").strip().lower()
    return configured in {"1", "true", "yes", "on"}


def _configure_windows_console() -> None:
    if sys.platform != "win32":
        return
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("NO_COLOR", "1")
    os.environ.setdefault("TERM", "dumb")
    try:
        os.system("chcp 65001 >nul 2>&1")
    except Exception:
        pass
    for stream_name in ("stdout", "stderr"):
        try:
            getattr(sys, stream_name).reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            try:
                original = getattr(sys, f"__{stream_name}__")
                fd = original.fileno()
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(
                        open(fd, "wb", closefd=False),
                        encoding="utf-8",
                        errors="replace",
                    ),
                )
            except (AttributeError, OSError, ValueError):
                pass


def _gguf_text_only_config(cfg: dict) -> dict:
    copied = dict(cfg)
    copied.pop("_mmproj_path", None)
    gguf = dict(copied.get("gguf") or {})
    gguf["mmproj_mode"] = "text-only"
    gguf.pop("mmproj_patterns", None)
    gguf.pop("mmproj_pattern", None)
    copied["gguf"] = gguf
    return copied


def _register_gguf_models(autoload: bool | None = None) -> list[dict]:
    if not gguf_catalog_enabled():
        return []
    from code_assist_v1.gguf_loader import find_gguf_files, load_gguf_model, resolve_gguf_paths
    from code_assist_v1.models import (
        ENV_CONFIG,
        GGUF_AUTO_REGISTER_UNCONFIGURED,
        GGUF_AUTOLOAD_ON_STARTUP,
        GGUF_AUTOLOAD_MODEL_ID,
        MODEL_REGISTRY,
    )

    gguf_files = find_gguf_files()
    gguf_files.sort(key=lambda x: x["size_gb"], reverse=True)

    matched_paths: set[str] = set()
    preferred_autoload: dict | None = None
    for mid, info in MODEL_REGISTRY.items():
        env_id = info.get("env_id") or f"gguf-{mid}"
        if info.get("enabled") is False or info.get("disabled"):
            ENV_CONFIG.pop(env_id, None)
            continue
        if info.get("runtime") != "gguf":
            continue
        resolved = resolve_gguf_paths(info, gguf_files=gguf_files)
        cfg = {
            "env_id": env_id,
            "url": "python://llama-cpp-python",
            "model": info.get("model") or mid,
            "name": info.get("name") or mid,
            "runtime": "gguf",
            "_registry_id": mid,
            "gguf": info.get("gguf") or {},
            "capabilities": info.get("capabilities") or [],
            "context_window": info.get("context_window", 0),
            "cost_tier": info.get("cost_tier", "local"),
        }
        if resolved.get("model_path"):
            cfg["_gguf_path"] = resolved["model_path"]
            cfg["_size_gb"] = resolved.get("size_gb")
            matched_paths.add(os.path.abspath(resolved["model_path"]))
            MODEL_REGISTRY[mid]["_gguf_path"] = resolved["model_path"]
            MODEL_REGISTRY[mid]["_size_gb"] = resolved.get("size_gb")
            if not preferred_autoload:
                preferred_autoload = cfg
        else:
            cfg["_missing"] = True
            cfg["_missing_patterns"] = resolved.get("patterns") or []
            MODEL_REGISTRY[mid].pop("_gguf_path", None)
            MODEL_REGISTRY[mid].pop("_size_gb", None)
        if resolved.get("mmproj_path"):
            cfg["_mmproj_path"] = resolved["mmproj_path"]
            MODEL_REGISTRY[mid]["_mmproj_path"] = resolved["mmproj_path"]
        else:
            MODEL_REGISTRY[mid].pop("_mmproj_path", None)
        ENV_CONFIG[env_id] = cfg
        MODEL_REGISTRY[mid]["env_id"] = env_id

    if GGUF_AUTO_REGISTER_UNCONFIGURED:
        auto_idx = 0
        for gf in gguf_files:
            if os.path.abspath(gf["path"]) in matched_paths:
                continue
            env_id = f"gguf-auto-{auto_idx}"
            auto_idx += 1
            model_name = gf["name"].replace(".gguf", "")
            ENV_CONFIG[env_id] = {
                "url": "python://llama-cpp-python",
                "model": model_name,
                "name": f"LOCAL ({model_name})",
                "runtime": "gguf",
                "_gguf_path": gf["path"],
                "_size_gb": gf["size_gb"],
            }

    should_autoload = GGUF_AUTOLOAD_ON_STARTUP if autoload is None else bool(autoload)
    if gguf_files and should_autoload:
        autoload_cfg = None
        if GGUF_AUTOLOAD_MODEL_ID:
            for mid, info in MODEL_REGISTRY.items():
                env_id = info.get("env_id") or mid
                if GGUF_AUTOLOAD_MODEL_ID in {mid, env_id}:
                    candidate = ENV_CONFIG.get(env_id)
                    if candidate and candidate.get("_gguf_path"):
                        autoload_cfg = candidate
                        break
        if not autoload_cfg:
            autoload_cfg = preferred_autoload

        if autoload_cfg and autoload_cfg.get("_gguf_path"):
            load_gguf_model(autoload_cfg["_gguf_path"], model_config=_gguf_text_only_config(autoload_cfg))
        elif GGUF_AUTO_REGISTER_UNCONFIGURED:
            fit = [g for g in gguf_files if g["size_gb"] <= VRAM_BUDGET_GB]
            chosen = fit[0] if fit else gguf_files[-1]
            load_gguf_model(chosen["path"])

    return gguf_files


def create_app(
    *,
    knowledge_core_services: object | None = None,
    knowledge_core_route_bindings: object | None = None,
    knowledge_core_environ: Mapping[str, str] | None = None,
    production_health_provider: Callable[
        [], tuple[Mapping[str, object], int]
    ] | None = None,
) -> Flask:
    _configure_windows_console()
    runtime_role = assert_web_process_role()

    app = Flask(
        __name__,
        static_folder=STATIC_DIR,
        static_url_path="/static",
    )
    from code_assist_v1.knowledge_core import HostKind, register_knowledge_core

    core = register_knowledge_core(
        app,
        HostKind.CODE,
        environ=knowledge_core_environ,
        services=knowledge_core_services,
        canonical_route_bindings=knowledge_core_route_bindings,
    )
    canonical_read_mode = "canonical_knowledge_v1" in core.registered_route_groups
    from code_assist_v1.auth import ensure_secret_key, register_auth_routes

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
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    register_auth_routes(app)

    if canonical_read_mode:
        from code_assist_v1.agent_integrations.routes import register_from_core
        register_from_core(app, core)

    @app.after_request
    def _disable_frontend_cache(response):
        path = request.path or ""
        if path in {"/", "/login", "/healthz"} or (
            path.startswith("/static/")
            and path.rsplit(".", 1)[-1].lower() in {"js", "css", "html"}
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/login")
    def login():
        return send_from_directory(STATIC_DIR, "login.html")

    @app.route("/code/")
    def code_alias():
        return redirect("/", code=302)

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
                    "service_id": "CodeAssist-Web",
                }, 503
        return {
            "status": "ok",
            "service": "code_assist_v1",
            "mode": (
                "production"
                if runtime_role.value == "web"
                else "standalone"
            ),
            "has_token": bool(API_TOKEN),
        }

    @app.route("/favicon.ico")
    def favicon():
        return Response(status=204)

    try:
        from code_assist_v1.seed import seed_skills_if_empty

        seed_skills_if_empty()
    except Exception as e:
        print(f"[code_assist_v1] skill seed skipped: {e}")

    gguf_files = _register_gguf_models() if gguf_catalog_enabled() else []

    from code_assist_v1.routes_models import register_model_routes
    from code_assist_v1.routes_skills import register_skill_routes
    from code_assist_v1.routes_workspace import register_workspace_routes
    from code_assist_v1.routes_jobs import register_job_routes
    from code_assist_v1.routes_notion_bridge import register_notion_bridge_routes

    if not canonical_read_mode:
        from code_assist_v1.routes_knowledge import register_knowledge_routes
        from code_assist_v1.routes_chat_stream import register_chat_stream_routes
        from code_assist_v1.routes_sessions import register_session_routes
        from code_assist_v1.routes_second_brain import register_second_brain_routes
        from code_assist_v1.routes_memory import register_memory_routes
        from code_assist_v1.routes_agent_harness import register_agent_harness_routes
        from code_assist_v1.routes_project_context import register_project_context_routes

    register_model_routes(app)
    register_skill_routes(app)
    register_job_routes(app)
    register_notion_bridge_routes(app)
    register_workspace_routes(app)
    if not canonical_read_mode:
        register_knowledge_routes(app)
        register_second_brain_routes(app)
        register_project_context_routes(app)
        register_memory_routes(app)
        register_agent_harness_routes(app)
        register_chat_stream_routes(app)
        register_session_routes(app)

    if not canonical_read_mode:
        try:
            from code_assist_v1.harness_setup import setup_harness

            setup_harness(app)
        except Exception as e:
            print(f"[code_assist_v1] harness setup skipped: {e}")

    print("=" * 60)
    print("  code_assist_v1 standalone")
    print("=" * 60)
    print(f"  URL: http://localhost:{PORT}")
    print(f"  token: {'loaded' if API_TOKEN else 'missing'}")
    print(f"  GGUF models: {len(gguf_files)}")
    print("=" * 60)

    return app


def main() -> None:
    app = create_app()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
