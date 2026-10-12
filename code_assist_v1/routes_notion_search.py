"""F0-P4 permission-aware search, backlink, reminder, and transfer routes."""

from __future__ import annotations

import re

from flask import Response, request

from code_assist_v1 import notion_http
from code_assist_v1 import notion_database_export_bundle
from code_assist_v1 import routes_notion_workspace as workspace_routes
from code_assist_v1.notion_collaboration.search_preview import read_search_preview
from code_assist_v1.notion_collaboration.database_search import search_database_titles_scoped


MAX_TRANSFER_CHARS = 2_000_000


def _authority_scope():
    if not workspace_routes._collaborative_runtime_enabled():
        raise FileNotFoundError("not found")
    store = workspace_routes._store()
    authority = workspace_routes._search_authority(store)
    return store, authority, str(workspace_routes._uid() or "")


def _page_scope(workspace_id: str, page_id: str):
    store, authority, actor = _authority_scope()
    workspace_routes._require_collaborative_page_workspace(
        store, actor, workspace_id, page_id
    )
    return authority, actor


def _integer_query(name: str, default: int, *, minimum: int = 0) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise notion_http.NotionRequestValidationError(
            f"integer {name} required"
        ) from exc
    if value < minimum:
        raise notion_http.NotionRequestValidationError(
            f"{name} must be at least {minimum}"
        )
    return value


def _boolean_query(name: str) -> bool:
    raw = str(request.args.get(name, "0") or "0").strip().lower()
    if raw in {"0", "false"}:
        return False
    if raw in {"1", "true"}:
        return True
    raise notion_http.NotionRequestValidationError(f"boolean {name} required")


def _transfer_text(data: dict[str, object], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise notion_http.NotionRequestValidationError(f"{field} required")
    if len(value) > MAX_TRANSFER_CHARS:
        raise notion_http.NotionRequestValidationError(f"{field} is too large")
    return value


def _download(body: str | bytes, *, mimetype: str, page_id: str, suffix: str) -> Response:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", page_id).strip("-") or "page"
    response = Response(body.encode("utf-8") if isinstance(body, str) else body, mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{safe_name}.{suffix}"'
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _download_stream(stream, *, mimetype: str, page_id: str, suffix: str) -> Response:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", page_id).strip("-") or "page"
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)

    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    response = Response(chunks(), mimetype=mimetype)
    response.content_length = size
    response.headers["Content-Disposition"] = f'attachment; filename="{safe_name}.{suffix}"'
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.call_on_close(stream.close)
    return response


def register_notion_search_routes(app) -> None:
    notion_http.register_error_handlers(app)

    @app.route("/api/notion/workspaces/<workspace_id>/database-search", methods=["GET"])
    def api_notion_independent_database_search(workspace_id: str):
        try:
            if not workspace_routes._collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = workspace_routes._store()
            authority = workspace_routes._database_authority(store)
            if not workspace_routes._database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            result = search_database_titles_scoped(
                authority,
                str(workspace_routes._uid() or ""), workspace_id,
                query=request.args.get("q", ""),
                sort=str(request.args.get("sort", "relevance") or "relevance"),
                limit=_integer_query("limit", 50, minimum=1),
                date_after=_integer_query("date_after", 0),
                direct_only=_boolean_query("direct_only"),
            )
            return workspace_routes._success("database_search_loaded", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/search-preview", methods=["GET"])
    def api_notion_collaborative_search_preview(workspace_id: str, page_id: str):
        try:
            authority, actor = _page_scope(workspace_id, page_id)
            return workspace_routes._success("search_preview_loaded", read_search_preview(authority, actor, workspace_id, page_id))
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/search", methods=["GET"])
    def api_notion_collaborative_search(workspace_id: str):
        try:
            _store, authority, actor = _authority_scope()
            result = authority.search(
                actor,
                workspace_id,
                query=str(request.args.get("q", "") or ""),
                title_only=_boolean_query("title_only"),
                creator=str(request.args.get("creator", "") or ""),
                scope=str(request.args.get("scope", "all") or "all"),
                date_after=_integer_query("date_after", 0),
                sort=str(request.args.get("sort", "relevance") or "relevance"),
                limit=_integer_query("limit", 50, minimum=1),
            )
            return workspace_routes._success("search_loaded", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/search/rebuild", methods=["POST"]
    )
    def api_notion_collaborative_search_rebuild(workspace_id: str):
        try:
            workspace_routes._request_origin()
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(data, set())
            _store, authority, actor = _authority_scope()
            return workspace_routes._success(
                "search_rebuilt", authority.rebuild_search_index(actor, workspace_id)
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/backlinks",
        methods=["GET"],
    )
    def api_notion_collaborative_backlinks(workspace_id: str, page_id: str):
        try:
            authority, actor = _page_scope(workspace_id, page_id)
            return workspace_routes._success(
                "backlinks_loaded",
                authority.list_backlinks(actor, workspace_id, page_id),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/reminders", methods=["GET"]
    )
    def api_notion_collaborative_reminders(workspace_id: str):
        try:
            _store, authority, actor = _authority_scope()
            return workspace_routes._success(
                "reminders_loaded",
                authority.list_reminders(
                    actor,
                    workspace_id,
                    limit=_integer_query("limit", 200, minimum=1),
                ),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_markdown",
        methods=["GET"],
    )
    def api_notion_collaborative_export_markdown(workspace_id: str, page_id: str):
        try:
            authority, actor = _page_scope(workspace_id, page_id)
            result = authority.export_markdown(actor, page_id)
            return _download(
                result["markdown"],
                mimetype="text/markdown",
                page_id=page_id,
                suffix="md",
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_html",
        methods=["GET"],
    )
    def api_notion_collaborative_export_html(workspace_id: str, page_id: str):
        try:
            authority, actor = _page_scope(workspace_id, page_id)
            block_id = request.args.get("block_id")
            view_id = request.args.get("view_id")
            bundle = request.args.get("bundle")
            page_content = request.args.get("page_content", "all")
            if page_content not in {"all", "exclude_files_images"}:
                raise notion_http.NotionRequestValidationError("invalid page content option")
            if page_content != "all" and not block_id:
                raise notion_http.NotionRequestValidationError("database block required for page content option")
            if view_id is not None and not block_id:
                raise notion_http.NotionRequestValidationError("block_id required for database view")
            if bundle not in (None, "1", "markdown_csv") or bundle is not None and not block_id:
                raise notion_http.NotionRequestValidationError("database block required for HTML bundle")
            if block_id is not None:
                result = authority.export_database_html(actor, page_id, block_id=block_id,
                    view_id=view_id, include_media=page_content == "all",
                    with_asset_bundle=bundle == "1")
            else:
                result = authority.export_html(actor, page_id)
            if bundle == "1":
                return _download_stream(result["archive"],
                    mimetype="application/zip", page_id=page_id, suffix="zip")
            if bundle == "markdown_csv":
                return _download(
                    notion_database_export_bundle.markdown_csv_archive(
                        result["title"], result["csv"], result["all_csv"], identity=block_id),
                    mimetype="application/zip", page_id=page_id, suffix="zip",
                )
            return _download(
                result["html"],
                mimetype="text/html",
                page_id=page_id,
                suffix="html",
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_csv",
        methods=["GET"],
    )
    def api_notion_collaborative_export_csv(workspace_id: str, page_id: str):
        try:
            authority, actor = _page_scope(workspace_id, page_id)
            block_id = str(request.args.get("block_id", "") or "").strip()
            if not block_id:
                raise notion_http.NotionRequestValidationError("block_id required")
            view_id = request.args.get("view_id")
            result = authority.export_csv(actor, page_id, block_id=block_id, view_id=view_id)
            return _download(
                result["csv"],
                mimetype="text/csv",
                page_id=page_id,
                suffix="csv",
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    def _import_payload(workspace_id: str, *, kind: str):
        workspace_routes._request_origin()
        data = workspace_routes._json_data()
        body_field = "markdown" if kind == "markdown" else "csv"
        workspace_routes._exact_json_fields(
            data,
            {
                "title",
                body_field,
                "parent_id",
                "expected_workspace_epoch",
                "operation_id",
            },
        )
        _store, authority, actor = _authority_scope()
        kwargs = {
            "title": workspace_routes._optional_text(data, "title", max_chars=500),
            "expected_workspace_epoch": workspace_routes._positive_epoch(
                data, "expected_workspace_epoch"
            ),
            "operation_id": workspace_routes._required_text(
                data, "operation_id", max_chars=200
            ),
            "parent_document_id": workspace_routes._optional_text(
                data, "parent_id", max_chars=200
            )
            or None,
        }
        if kind == "markdown":
            return authority.import_markdown(
                actor,
                workspace_id,
                _transfer_text(data, "markdown"),
                **kwargs,
            )
        return authority.import_csv(
            actor,
            workspace_id,
            _transfer_text(data, "csv"),
            **kwargs,
        )

    @app.route(
        "/api/notion/workspaces/<workspace_id>/imports/markdown", methods=["POST"]
    )
    def api_notion_collaborative_import_markdown(workspace_id: str):
        try:
            return workspace_routes._success(
                "markdown_imported",
                _import_payload(workspace_id, kind="markdown"),
                http_status=201,
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/imports/csv", methods=["POST"]
    )
    def api_notion_collaborative_import_csv(workspace_id: str):
        try:
            return workspace_routes._success(
                "csv_imported",
                _import_payload(workspace_id, kind="csv"),
                http_status=201,
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)


__all__ = ["register_notion_search_routes"]
