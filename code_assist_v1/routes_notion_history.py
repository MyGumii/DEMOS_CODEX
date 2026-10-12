"""F0-P3 collaborative history routes registered only by the Notion host."""

from __future__ import annotations

from flask import request

from code_assist_v1 import notion_http
from code_assist_v1 import routes_notion_workspace as workspace_routes


def _positive_query(name: str, *, required: bool = True) -> int | None:
    raw = request.args.get(name)
    if raw is None and not required:
        return None
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise notion_http.NotionRequestValidationError(
            f"positive {name} required"
        ) from exc
    if value < 1:
        raise notion_http.NotionRequestValidationError(f"positive {name} required")
    return value


def _scope(workspace_id: str, page_id: str):
    if not workspace_routes._collaborative_runtime_enabled():
        raise FileNotFoundError("not found")
    store = workspace_routes._store()
    authority = workspace_routes._history_authority(store)
    actor = str(workspace_routes._uid() or "")
    workspace_routes._require_collaborative_page_workspace(
        store, actor, workspace_id, page_id
    )
    return authority, actor


def register_notion_history_routes(app) -> None:
    notion_http.register_error_handlers(app)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history",
        methods=["GET"],
    )
    def api_notion_collaborative_history(workspace_id: str, page_id: str):
        try:
            authority, actor = _scope(workspace_id, page_id)
            before = _positive_query("before_revision", required=False)
            raw_limit = request.args.get("limit", "50")
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError) as exc:
                raise notion_http.NotionRequestValidationError(
                    "positive limit required"
                ) from exc
            if limit < 1:
                raise notion_http.NotionRequestValidationError(
                    "positive limit required"
                )
            return workspace_routes._success(
                "history_loaded",
                authority.list_history(
                    actor, page_id, before_revision=before, limit=limit
                ),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/policy",
        methods=["GET"],
    )
    def api_notion_collaborative_history_policy(workspace_id: str, page_id: str):
        try:
            authority, _actor = _scope(workspace_id, page_id)
            return workspace_routes._success(
                "history_policy_loaded", authority.retention_policy()
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/"
        "revisions/<int:revision>",
        methods=["GET"],
    )
    def api_notion_collaborative_history_revision(
        workspace_id: str, page_id: str, revision: int
    ):
        try:
            authority, actor = _scope(workspace_id, page_id)
            return workspace_routes._success(
                "history_revision_loaded",
                authority.get_revision(actor, page_id, revision),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/diff",
        methods=["GET"],
    )
    def api_notion_collaborative_history_diff(workspace_id: str, page_id: str):
        try:
            authority, actor = _scope(workspace_id, page_id)
            return workspace_routes._success(
                "history_diff_loaded",
                authority.diff_revisions(
                    actor,
                    page_id,
                    from_revision=int(_positive_query("from_revision")),
                    to_revision=int(_positive_query("to_revision")),
                ),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/"
        "revisions/<int:revision>/restore",
        methods=["POST"],
    )
    def api_notion_collaborative_history_restore(
        workspace_id: str, page_id: str, revision: int
    ):
        try:
            workspace_routes._request_origin()
            authority, actor = _scope(workspace_id, page_id)
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(
                data,
                {
                    "expected_revision",
                    "expected_document_sequence",
                    "expected_workspace_epoch",
                    "expected_document_epoch",
                    "operation_id",
                },
            )
            result = authority.restore_revision(
                actor,
                page_id,
                revision,
                expected_revision=workspace_routes._positive_version(
                    data, "expected_revision"
                ),
                expected_document_sequence=workspace_routes._positive_version(
                    data, "expected_document_sequence"
                ),
                expected_workspace_epoch=workspace_routes._positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=workspace_routes._positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=workspace_routes._required_text(
                    data, "operation_id", max_chars=200
                ),
            )
            return workspace_routes._success("history_revision_restored", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)


__all__ = ["register_notion_history_routes"]
