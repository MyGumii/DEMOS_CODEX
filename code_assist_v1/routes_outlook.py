"""Authenticated Outlook integration routes for the Notion workspace."""
from __future__ import annotations

from flask import jsonify, redirect, request

from code_assist_v1.auth import current_user_id
from code_assist_v1.job_manager import manager as job_manager
from code_assist_v1.outlook_integration import (
    create_daily_digest,
    get_settings,
    probe_connector,
    remember_connector_status,
    reset_connection_state,
    status_payload,
    sync_user,
    update_settings,
    wake_outlook_scheduler,
)
from code_assist_v1.outlook_graph import (
    GraphAuthenticationRequired,
    GraphConfigurationError,
    GraphOAuthError,
    begin_authorization,
    complete_authorization,
    disconnect as disconnect_graph,
)


def _uid() -> str:
    uid = current_user_id()
    if not uid:
        raise ValueError("authenticated user required")
    return uid


def _json_data() -> dict:
    return request.get_json(force=True, silent=True) or {}


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _error_response(exc: Exception):
    if isinstance(exc, GraphConfigurationError):
        return jsonify({"error": str(exc), "code": "graph_not_configured"}), 503
    if isinstance(exc, GraphAuthenticationRequired):
        return jsonify({"error": str(exc), "code": "graph_authentication_required"}), 401
    if isinstance(exc, GraphOAuthError):
        return jsonify({"error": str(exc), "code": "graph_oauth_error"}), 400
    if isinstance(exc, ValueError):
        return jsonify({"error": str(exc)}), 400
    if isinstance(exc, PermissionError):
        return jsonify({"error": str(exc)}), 403
    return jsonify({"error": str(exc)}), 500


def _oauth_redirect(result: str):
    response = redirect(f"/?outlook={result}", code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def register_outlook_routes(app) -> None:
    @app.route("/api/notion/outlook/status", methods=["GET"])
    def api_notion_outlook_status():
        try:
            return jsonify(status_payload(_uid(), probe=_truthy(request.args.get("probe"))))
        except Exception as exc:
            return _error_response(exc)

    @app.route("/api/notion/outlook/settings", methods=["PUT"])
    def api_notion_outlook_settings():
        uid = _uid()
        data = _json_data()
        current = get_settings(uid)
        selected_connector = str(data.get("connector") or current.get("connector") or "microsoft_graph")
        requested_enabled = _truthy(data.get("enabled")) if "enabled" in data else bool(current.get("enabled"))
        if requested_enabled:
            connector = probe_connector(uid, connector=selected_connector)
            if not connector.get("available") or (
                selected_connector == "microsoft_graph" and not connector.get("authenticated")
            ):
                update_settings(uid, {"enabled": False})
                return jsonify({
                    "error": connector.get("error") or (
                        "Microsoft 계정 로그인이 필요합니다."
                        if selected_connector == "microsoft_graph"
                        else "Classic Outlook에 연결할 수 없습니다."
                    ),
                    "connector_status": connector,
                }), 409
        try:
            settings = update_settings(uid, data)
        except Exception as exc:
            return _error_response(exc)
        job = None
        if settings.get("enabled") and not current.get("enabled"):
            job = job_manager.submit(
                user_id=uid,
                kind="outlook_initial_sync",
                metadata={"connector": settings.get("connector") or "microsoft_graph"},
                target=lambda _ctx: sync_user(uid),
            )
        wake_outlook_scheduler()
        payload = status_payload(uid)
        if job:
            payload["job"] = job
        return jsonify(payload)

    @app.route("/api/notion/outlook/oauth/start", methods=["GET"])
    def api_notion_outlook_oauth_start():
        try:
            uid = _uid()
            update_settings(uid, {"connector": "microsoft_graph", "enabled": False})
            authorization_url = begin_authorization(uid)
            response = redirect(authorization_url, code=302)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        except Exception as exc:
            return _error_response(exc)

    @app.route("/api/notion/outlook/oauth/callback", methods=["GET"])
    def api_notion_outlook_oauth_callback():
        try:
            uid = _uid()
            connector = complete_authorization(uid, request.args.to_dict(flat=True))
            update_settings(uid, {"connector": "microsoft_graph", "enabled": True})
            remember_connector_status(uid, connector, error="")
            job_manager.submit(
                user_id=uid,
                kind="outlook_initial_sync",
                metadata={"connector": "microsoft_graph"},
                target=lambda _ctx: sync_user(uid),
            )
            wake_outlook_scheduler()
            return _oauth_redirect("connected")
        except Exception as exc:
            uid = current_user_id()
            if uid:
                try:
                    update_settings(uid, {"connector": "microsoft_graph", "enabled": False})
                    remember_connector_status(
                        uid,
                        {
                            "available": False,
                            "authenticated": False,
                            "requires_auth": True,
                            "connector": "microsoft_graph",
                            "error": str(exc),
                        },
                        error=str(exc),
                    )
                except Exception:
                    pass
            return _oauth_redirect("error")

    @app.route("/api/notion/outlook/connection", methods=["DELETE"])
    def api_notion_outlook_disconnect():
        try:
            uid = _uid()
            current = get_settings(uid)
            if current.get("connector") == "microsoft_graph":
                disconnect_graph(uid)
            update_settings(uid, {"enabled": False})
            reset_connection_state(uid)
            wake_outlook_scheduler()
            return jsonify(status_payload(uid))
        except Exception as exc:
            return _error_response(exc)

    @app.route("/api/notion/outlook/sync", methods=["POST"])
    def api_notion_outlook_sync():
        uid = _uid()
        if not get_settings(uid).get("enabled"):
            return jsonify({"error": "Outlook 연결이 꺼져 있습니다."}), 409
        job = job_manager.submit(
            user_id=uid,
            kind="outlook_sync",
            metadata={"connector": get_settings(uid).get("connector") or "microsoft_graph"},
            target=lambda _ctx: sync_user(uid),
        )
        wake_outlook_scheduler()
        return jsonify({"status": "queued", "job": job}), 202

    @app.route("/api/notion/outlook/digest", methods=["POST"])
    def api_notion_outlook_digest():
        uid = _uid()
        if not get_settings(uid).get("enabled"):
            return jsonify({"error": "Outlook 연결이 꺼져 있습니다."}), 409
        job = job_manager.submit(
            user_id=uid,
            kind="outlook_daily_digest",
            metadata={"manual": True},
            target=lambda _ctx: create_daily_digest(uid, force=True),
        )
        return jsonify({"status": "queued", "job": job}), 202
