"""Approved Wiki-file and shared-feedback API extension (2026-09-06)."""
from functools import wraps
import io
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from flask import jsonify, request, send_file
from ..config import MAX_UPLOAD_MB
from ..feedback_store import FeedbackStore, FeedbackError
from .canonical_routes import _principal, _error_response
from .scope import SessionPrincipal
from .services.wiki_files import WikiFilesService, file_name


def register_workspace_feature_routes(app, source_service, management_service, principal_resolver):
    files = WikiFilesService(source_service, management_service)
    board = FeedbackStore(files.factory.database_path.parent / "feedback.db")
    app.extensions["workspace_features"] = {"files": files, "feedback": board}

    def route(path, methods):
        def decorate(fn):
            @wraps(fn)
            def wrapped(**kwargs):
                try:
                    p = _principal(principal_resolver)
                    return fn(SessionPrincipal(p.user_id, p.scope_epoch), **kwargs)
                except FeedbackError as exc:
                    return jsonify(error={"code": "FEEDBACK_ERROR", "message": str(exc)}), exc.http_status
                except Exception as exc:
                    return _error_response(exc, str(uuid4()))
            app.add_url_rule(path, "workspace_features_"+fn.__name__, wrapped, methods=methods)
            return wrapped
        return decorate

    def uploads():
        maximum = MAX_UPLOAD_MB * 1024 * 1024
        if request.content_length and request.content_length > maximum:
            raise FeedbackError(f"첨부 합계는 {MAX_UPLOAD_MB}MB 이하여야 합니다.", 413)
        result, total = [], 0
        for f in request.files.getlist("files"):
            if not f.filename:
                continue
            content = f.read(maximum-total+1)
            total += len(content)
            if total > maximum:
                raise FeedbackError(f"첨부 합계는 {MAX_UPLOAD_MB}MB 이하여야 합니다.", 413)
            name = file_name(Path(f.filename.replace("\\", "/")).name)
            result.append((name, mimetypes.guess_type(name)[0] or "application/octet-stream", content))
        return result

    def json_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise FeedbackError("JSON 객체 형식으로 요청해 주세요.")
        return body

    def attachment(stream, name, media, size, *, close=False):
        response = send_file(stream, download_name=name, mimetype=media,
                             as_attachment=True, conditional=False, etag=False, max_age=0)
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.content_length = size
        if close:
            response.call_on_close(stream.close)
        return response

    @route("/api/knowledge/v2/spaces/<space_id>/markdown-export", ["GET"])
    def export_markdown(p, space_id):
        stream, name, media, size, count = files.export(p, space_id)
        response = attachment(stream, name, media, size, close=True)
        response.headers["X-Wiki-Document-Count"] = str(count)
        return response

    @route("/api/knowledge/v2/spaces/<space_id>/source-availability", ["POST"])
    def source_availability(p, space_id):
        body = json_body()
        return jsonify(items=files.availability(p, space_id, body.get("item_ids")))

    @route("/api/knowledge/v2/spaces/<space_id>/items/<item_id>/markdown-download", ["GET"])
    def download_markdown(p, space_id, item_id):
        stream, name, media, size, _ = files.export(p, space_id, item_id)
        return attachment(stream, name, media, size, close=True)

    @route("/api/knowledge/v2/spaces/<space_id>/items/<item_id>/original-links", ["POST"])
    def attach_original(p, space_id, item_id):
        selected = uploads()
        if len(selected) != 1:
            raise FeedbackError("한 번에 원본 파일 하나를 연결해 주세요.")
        name, _, content = selected[0]
        return jsonify(files.attach(p, space_id, item_id, filename=name, content=content,
            operation_id=request.form.get("operation_id"), expected_revision_id=request.form.get("expected_revision_id")))

    @route("/api/code/feedback", ["GET"])
    def list_feedback(p):
        mine = request.args.get("mine", "0")
        if mine not in {"0", "1"}:
            raise FeedbackError("내 글 필터가 올바르지 않습니다.")
        return jsonify(board.list(files.actor(p), before=request.args.get("cursor"),
            query=request.args.get("q", ""), search_field=request.args.get("field", "all"),
            kind=request.args.get("kind", ""), status=request.args.get("status", ""), mine=mine == "1"))

    @route("/api/code/feedback/<post_id>", ["GET"])
    def get_feedback(p, post_id):
        return jsonify(board.get(files.actor(p), post_id))

    def save(p, action, post_id=None):
        selected = uploads()
        try:
            keep = json.loads(request.form.get("keep_ids", "[]"))
            expected = int(request.form["expected_version"]) if "expected_version" in request.form else None
        except (ValueError, TypeError):
            raise FeedbackError("게시글 요청 형식이 올바르지 않습니다.") from None
        if not isinstance(keep, list):
            raise FeedbackError("첨부 목록이 올바르지 않습니다.")
        return jsonify(board.mutate(files.actor(p), action=action,
            operation_id=request.form.get("operation_id"), post_id=post_id,
            expected_version=expected, title=request.form.get("title"), kind=request.form.get("kind"),
            body=request.form.get("body"), keep_ids=keep, files=selected, revalidate=lambda: files.actor(p)))

    @route("/api/code/feedback", ["POST"])
    def create_feedback(p):
        return save(p, "create")

    @route("/api/code/feedback/<post_id>", ["PUT"])
    def edit_feedback(p, post_id):
        return save(p, "edit", post_id)

    @route("/api/code/feedback/<post_id>", ["DELETE"])
    def delete_feedback(p, post_id):
        body = json_body()
        return jsonify(board.mutate(files.actor(p), action="delete", post_id=post_id,
            operation_id=body.get("operation_id"), expected_version=body.get("expected_version"),
            revalidate=lambda: files.actor(p)))

    @route("/api/code/feedback/<post_id>/status", ["PATCH"])
    def status_feedback(p, post_id):
        body = json_body()
        return jsonify(board.mutate(files.actor(p), action="status", post_id=post_id,
            operation_id=body.get("operation_id"), expected_version=body.get("expected_version"),
            status=body.get("status"), revalidate=lambda: files.actor(p)))

    @route("/api/code/feedback/<post_id>/attachments/<attachment_id>", ["GET"])
    def download_feedback_attachment(p, post_id, attachment_id):
        asset = board.download(files.actor(p), post_id, attachment_id)
        return attachment(io.BytesIO(asset["content"]), asset["filename"], asset["media_type"], len(asset["content"]))
