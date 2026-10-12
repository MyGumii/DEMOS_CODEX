"""F0-P2 collaborative file routes registered only by the Notion host."""

from __future__ import annotations

from flask import Response, send_file, request

from code_assist_v1 import notion_http
from code_assist_v1 import routes_notion_workspace as workspace_routes
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration import database_file_upload
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied


def _scope(workspace_id: str, page_id: str):
    if not workspace_routes._collaborative_runtime_enabled():
        raise FileNotFoundError("not found")
    store = workspace_routes._store()
    authority = workspace_routes._file_authority(store)
    actor = str(workspace_routes._uid() or "")
    workspace_routes._require_collaborative_page_workspace(
        store, actor, workspace_id, page_id
    )
    return authority, actor


def _session_scope(workspace_id: str, page_id: str, session_id: str):
    authority, actor = _scope(workspace_id, page_id)
    if authority.upload_status(actor, session_id)["document_id"] != page_id:
        raise NotFoundOrDenied("not found or permission denied")
    return authority, actor


def _database_scope(workspace_id: str, database_id: str):
    if not workspace_routes._collaborative_runtime_enabled():
        raise FileNotFoundError("not found")
    authority = workspace_routes._file_authority(workspace_routes._store())
    with authority._reader() as con:
        con.execute("BEGIN")
        if not database_access.upload_scope_installed(con):
            raise FileNotFoundError("not found")
    actor = str(workspace_routes._uid() or "")
    database_access.resolve(authority, actor, workspace_id, database_id)
    return authority, actor


def _database_session_scope(workspace_id: str, database_id: str, session_id: str):
    authority, actor = _database_scope(workspace_id, database_id)
    if authority.upload_status(actor, session_id).get("database_id") != database_id:
        raise NotFoundOrDenied("not found or permission denied")
    return authority, actor


def register_notion_file_routes(app) -> None:
    notion_http.register_error_handlers(app)

    @app.post('/api/notion/icon-image-preview')
    def api_notion_icon_image_preview():
        try:
            workspace_routes._request_origin()
            actor = workspace_routes._uid()
            if not actor:
                raise PermissionError('로그인이 필요합니다.')
            data = workspace_routes._navigation_json_data()
            workspace_routes._exact_json_fields(data, {'url'})
            from code_assist_v1.notion_remote_icon import fetch_image
            raw, mime = fetch_image(data['url'])
            if workspace_routes._uid() != actor:
                raise PermissionError('로그인이 필요합니다.')
            response = Response(raw, mimetype=mime)
            response.headers.update({'Cache-Control': 'private, no-store, max-age=0',
                'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline'})
            return response
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files",
        methods=["GET"],
    )
    def api_notion_collaborative_files(workspace_id: str, page_id: str):
        try:
            authority, actor = _scope(workspace_id, page_id)
            result = authority.list_files(actor, page_id)
            result["files"] = [
                {
                    **item,
                    "size": item["size_bytes"],
                    "url": f"/api/notion/collaboration/files/{item['id']}",
                }
                for item in result["files"]
            ]
            return workspace_routes._success("files_loaded", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/policy",
        methods=["GET"],
    )
    def api_notion_collaborative_file_policy(workspace_id: str, page_id: str):
        try:
            authority, _actor = _scope(workspace_id, page_id)
            return workspace_routes._success(
                "file_policy_loaded", authority.capacity_policy()
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads",
        methods=["POST"],
    )
    def api_notion_collaborative_file_begin(workspace_id: str, page_id: str):
        try:
            workspace_routes._request_origin()
            authority, actor = _scope(workspace_id, page_id)
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(
                data,
                {
                    "original_name",
                    "mime_type",
                    "total_size",
                    "expected_sha256",
                    "expected_document_sequence",
                    "expected_workspace_epoch",
                    "expected_document_epoch",
                    "operation_id",
                    "chunk_size",
                },
            )
            result = authority.begin_upload(
                actor,
                page_id,
                original_name=workspace_routes._required_text(
                    data, "original_name", max_chars=255
                ),
                mime_type=workspace_routes._optional_text(
                    data, "mime_type", max_chars=255
                ),
                total_size=workspace_routes._nonnegative_integer(data, "total_size"),
                expected_sha256=workspace_routes._required_text(
                    data, "expected_sha256", max_chars=71
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
                chunk_size=workspace_routes._positive_version(data, "chunk_size"),
            )
            return workspace_routes._success(
                "file_upload_begun", result, http_status=201
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/"
        "<session_id>/chunks/<int:chunk_index>",
        methods=["PUT"],
    )
    def api_notion_collaborative_file_chunk(
        workspace_id: str, page_id: str, session_id: str, chunk_index: int
    ):
        try:
            workspace_routes._request_origin()
            if request.mimetype != "application/octet-stream":
                raise notion_http.NotionRequestValidationError(
                    "chunk must use application/octet-stream"
                )
            operation_id = str(
                request.headers.get("X-Notion-Operation-Id") or ""
            ).strip()
            if not operation_id or len(operation_id) > 200:
                raise notion_http.NotionRequestValidationError(
                    "X-Notion-Operation-Id required"
                )
            authority, actor = _session_scope(workspace_id, page_id, session_id)
            result = authority.stage_chunk(
                actor,
                session_id,
                chunk_index=chunk_index,
                stream=request.stream,
                operation_id=operation_id,
            )
            return workspace_routes._success("file_chunk_received", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/"
        "<session_id>",
        methods=["GET"],
    )
    def api_notion_collaborative_file_status(
        workspace_id: str, page_id: str, session_id: str
    ):
        try:
            authority, actor = _session_scope(workspace_id, page_id, session_id)
            return workspace_routes._success(
                "file_upload_status", authority.upload_status(actor, session_id)
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/uploads/"
        "<session_id>/<action>",
        methods=["POST"],
    )
    def api_notion_collaborative_file_transition(
        workspace_id: str, page_id: str, session_id: str, action: str
    ):
        try:
            workspace_routes._request_origin()
            if action not in {"finalize", "cancel"}:
                raise FileNotFoundError("not found")
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(data, {"operation_id"})
            operation_id = workspace_routes._required_text(
                data, "operation_id", max_chars=200
            )
            authority, actor = _session_scope(workspace_id, page_id, session_id)
            if action == "finalize":
                result = authority.finalize_upload(
                    actor, session_id, operation_id=operation_id
                )
                result = {
                    **result,
                    "id": result["file_id"],
                    "size": result["size_bytes"],
                    "url": f"/api/notion/collaboration/files/{result['file_id']}",
                }
                return workspace_routes._success(
                    "file_uploaded", {"file": result}
                )
            return workspace_routes._success(
                "file_upload_canceled",
                authority.cancel_upload(actor, session_id, operation_id=operation_id),
            )
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/files/<file_id>",
        methods=["DELETE"],
    )
    def api_notion_collaborative_file_delete(
        workspace_id: str, page_id: str, file_id: str
    ):
        try:
            workspace_routes._request_origin()
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(
                data,
                {
                    "expected_document_sequence",
                    "expected_workspace_epoch",
                    "expected_document_epoch",
                    "operation_id",
                },
            )
            authority, actor = _scope(workspace_id, page_id)
            if authority.get_file(actor, file_id)["document_id"] != page_id:
                raise NotFoundOrDenied("not found or permission denied")
            result = authority.delete_file(
                actor,
                file_id,
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
            return workspace_routes._success("file_deleted", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/files/uploads",
        methods=["POST"],
    )
    def api_notion_database_file_begin(workspace_id: str, database_id: str):
        try:
            workspace_routes._request_origin()
            authority, actor = _database_scope(workspace_id, database_id)
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(data, {
                "original_name", "mime_type", "total_size", "expected_sha256",
                "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_epoch",
                "operation_id", "chunk_size",
            })
            result = database_file_upload.begin_upload(
                authority, actor, workspace_id, database_id,
                original_name=workspace_routes._required_text(
                    data, "original_name", max_chars=255),
                mime_type=workspace_routes._optional_text(
                    data, "mime_type", max_chars=255),
                total_size=workspace_routes._nonnegative_integer(data, "total_size"),
                expected_sha256=workspace_routes._required_text(
                    data, "expected_sha256", max_chars=71),
                expected_document_sequence=workspace_routes._positive_version(
                    data, "expected_document_sequence"),
                expected_workspace_epoch=workspace_routes._positive_epoch(
                    data, "expected_workspace_epoch"),
                expected_document_epoch=workspace_routes._positive_epoch(
                    data, "expected_document_epoch"),
                expected_database_epoch=workspace_routes._positive_epoch(
                    data, "expected_database_epoch"),
                operation_id=workspace_routes._required_text(
                    data, "operation_id", max_chars=200),
                chunk_size=workspace_routes._positive_version(data, "chunk_size"),
            )
            return workspace_routes._success(
                "database_file_upload_begun", result, http_status=201)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/files/uploads/"
        "<session_id>/chunks/<int:chunk_index>", methods=["PUT"],
    )
    def api_notion_database_file_chunk(
        workspace_id: str, database_id: str, session_id: str, chunk_index: int,
    ):
        try:
            workspace_routes._request_origin()
            if request.mimetype != "application/octet-stream":
                raise notion_http.NotionRequestValidationError(
                    "chunk must use application/octet-stream")
            operation_id = str(request.headers.get("X-Notion-Operation-Id") or "").strip()
            if not operation_id or len(operation_id) > 200:
                raise notion_http.NotionRequestValidationError(
                    "X-Notion-Operation-Id required")
            authority, actor = _database_session_scope(
                workspace_id, database_id, session_id)
            result = authority.stage_chunk(
                actor, session_id, chunk_index=chunk_index,
                stream=request.stream, operation_id=operation_id)
            return workspace_routes._success("database_file_chunk_received", result)
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/files/uploads/"
        "<session_id>", methods=["GET"],
    )
    def api_notion_database_file_status(
        workspace_id: str, database_id: str, session_id: str,
    ):
        try:
            authority, actor = _database_session_scope(
                workspace_id, database_id, session_id)
            return workspace_routes._success(
                "database_file_upload_status",
                authority.upload_status(actor, session_id))
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/files/uploads/"
        "<session_id>/<action>", methods=["POST"],
    )
    def api_notion_database_file_transition(
        workspace_id: str, database_id: str, session_id: str, action: str,
    ):
        try:
            workspace_routes._request_origin()
            if action not in {"finalize", "cancel"}:
                raise FileNotFoundError("not found")
            data = workspace_routes._json_data()
            workspace_routes._exact_json_fields(data, {"operation_id"})
            operation_id = workspace_routes._required_text(
                data, "operation_id", max_chars=200)
            authority, actor = _database_session_scope(
                workspace_id, database_id, session_id)
            if action == "finalize":
                result = authority.finalize_upload(
                    actor, session_id, operation_id=operation_id)
                if result.get("database_id") != database_id:
                    raise NotFoundOrDenied("not found or permission denied")
                result = {
                    **result, "id": result["file_id"],
                    "size": result["size_bytes"],
                    "url": f"/api/notion/workspaces/{workspace_id}/databases/"
                           f"{database_id}/files/{result['file_id']}",
                }
                return workspace_routes._success(
                    "database_file_uploaded", {"file": result})
            return workspace_routes._success(
                "database_file_upload_canceled",
                authority.cancel_upload(actor, session_id, operation_id=operation_id))
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route("/api/notion/collaboration/files/<file_id>", methods=["GET"])
    def api_notion_collaborative_file_download(file_id: str):
        try:
            if not workspace_routes._collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = workspace_routes._file_authority(workspace_routes._store())
            item = authority.get_file(str(workspace_routes._uid() or ""), file_id)
            response = send_file(
                item["path"],
                mimetype=item["mime_type"] or None,
                as_attachment=request.args.get("download") == "1",
                download_name=item["original_name"],
                conditional=True,
            )
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            return response
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/files/<file_id>",
        methods=["GET"],
    )
    def api_notion_database_file_download(
        workspace_id: str, database_id: str, file_id: str,
    ):
        try:
            if not workspace_routes._collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = workspace_routes._file_authority(workspace_routes._store())
            if not workspace_routes._database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            item = database_access.get_file(
                authority, str(workspace_routes._uid() or ""),
                workspace_id, database_id, file_id,
            )
            response = send_file(
                item["path"],
                mimetype=item["mime_type"] or None,
                as_attachment=request.args.get("download") == "1",
                download_name=item["original_name"],
                conditional=True,
            )
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception as exc:
            return workspace_routes._workspace_error_response(exc)


__all__ = ["register_notion_file_routes"]
