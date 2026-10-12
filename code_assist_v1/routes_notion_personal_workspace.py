"""Authenticated personal appearance endpoints; never fall back from catalogs."""
from flask import Response, jsonify, request

from code_assist_v1 import notion_http
from code_assist_v1 import notion_personal_workspace as service
from code_assist_v1.auth import current_user_id


def _legacy_only():
    from code_assist_v1.routes_notion_workspace import _collaborative_runtime_enabled, _navigation_ui_requested
    if _collaborative_runtime_enabled() or _navigation_ui_requested():
        raise FileNotFoundError("personal workspace appearance is unavailable in this workspace mode")
    actor = current_user_id()
    if not actor:
        raise PermissionError("authentication required")
    return actor


def _error(exc):
    if isinstance(exc, service.AppearanceConflict):
        return jsonify({"code": "workspace_appearance_conflict", "error": str(exc), "message": str(exc)}), 409
    return notion_http.error_response(exc)


def _json(payload):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


def register_personal_workspace_routes(app):
    @app.get("/api/notion/personal-workspaces")
    def personal_workspaces():
        try:
            return _json(service.list_visible(_legacy_only()))
        except Exception as exc:
            return _error(exc)

    @app.route("/api/notion/personal-workspaces/<owner>/appearance", methods=["GET", "PATCH"])
    def personal_workspace_appearance(owner):
        try:
            actor = _legacy_only()
            if request.method == "GET":
                return _json(service.read(actor, owner))
            data = notion_http.json_object_request()
            return _json(service.update(actor, owner, icon=data.get("icon"), base_version=data.get("base_version"), operation_id=data.get("operation_id")))
        except Exception as exc:
            return _error(exc)

    @app.post("/api/notion/personal-workspaces/<owner>/icon-image")
    def personal_workspace_image_upload(owner):
        try:
            actor = _legacy_only()
            version = int(request.headers.get("X-Notion-Base-Version", ""))
            raw = request.stream.read(service.MAX_BYTES + 1)
            return _json(service.update(actor, owner, raw=raw, mime_type=request.mimetype, base_version=version,
                                        operation_id=request.headers.get("X-Notion-Operation-Id")))
        except Exception as exc:
            return _error(exc)

    @app.get("/api/notion/personal-workspaces/<owner>/images/<image_id>")
    def personal_workspace_image_read(owner, image_id):
        try:
            image = service.read_image(_legacy_only(), owner, image_id)
            response = Response(image["raw"], mimetype=image["mime_type"])
            response.headers.update({"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})
            return response
        except Exception as exc:
            return _error(exc)
