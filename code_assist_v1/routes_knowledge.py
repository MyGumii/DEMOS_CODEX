"""
Domain knowledge CRUD routes.

Legacy LLM Wiki markdown files are still supported for compatibility. Canonical
Second Brain uploads use routes_second_brain.py.
"""
from __future__ import annotations

import os
import shutil

from flask import jsonify, request
from werkzeug.utils import secure_filename

from code_assist_v1 import knowledge_store as ks
from code_assist_v1.auth import current_user_id
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.config import MAX_UPLOAD_MB
from code_assist_v1.document_extractors import extract_document


def _cleanup_extraction_temp_dirs(metadata: dict) -> None:
    for path in (metadata or {}).get("cleanup_dirs") or []:
        try:
            if path and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def _read_text_from_upload(filename: str, raw: bytes) -> str | None:
    result = extract_document(filename, raw)
    try:
        return result.markdown
    finally:
        _cleanup_extraction_temp_dirs(result.metadata or {})


def _get_user_id() -> str | None:
    auth_uid = current_user_id()
    if auth_uid:
        return auth_uid
    if request.form:
        uid = (request.form.get("user_id") or "").strip()
        if uid:
            return uid
    if request.is_json:
        data = request.get_json(silent=True) or {}
        uid = (data.get("user_id") or "").strip()
        if uid:
            return uid
    uid = (request.args.get("user_id") or "").strip()
    return uid or None


def register_knowledge_routes(app):
    @app.route("/api/code/knowledge/list")
    def api_kb_list():
        uid = _get_user_id()
        return jsonify({"items": ks.list_files(user_id=uid), "user_id": uid})

    @app.route("/api/code/knowledge/view/<path:filename>")
    def api_kb_view(filename):
        uid = _get_user_id()
        info = ks.view_file(filename, user_id=uid)
        if not info:
            return jsonify({"error": "file not found"}), 404
        return jsonify(info)

    @app.route("/api/code/knowledge/create", methods=["POST"])
    def api_kb_create():
        data = request.get_json(force=True, silent=True) or {}
        filename = (data.get("filename") or "").strip()
        content = data.get("content") or ""
        uid = _get_user_id()
        if not filename:
            return jsonify({"error": "filename required"}), 400
        with user_write_lock("knowledge", uid):
            info = ks.save_file(filename, content, user_id=uid)
        return jsonify({"status": "created", **info})

    @app.route("/api/code/knowledge/upload", methods=["POST"])
    def api_kb_upload():
        if "file" not in request.files:
            return jsonify({"error": "file field required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename required"}), 400
        uid = _get_user_id()
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_MB * 1024 * 1024:
            return jsonify({"error": f"file size exceeds limit ({MAX_UPLOAD_MB}MB)"}), 413
        raw = f.read()
        try:
            text = _read_text_from_upload(f.filename, raw)
        except Exception as e:
            return jsonify({"error": f"text extraction failed: {e}"}), 415
        if text is None:
            return jsonify({"error": "text extraction failed"}), 415
        base = os.path.splitext(secure_filename(f.filename) or "uploaded")[0]
        target_name = base + ".md"
        with user_write_lock("knowledge", uid):
            info = ks.save_file(target_name, text, user_id=uid)
        return jsonify({"status": "uploaded", **info, "extracted_chars": len(text)})

    @app.route("/api/code/knowledge/delete", methods=["POST"])
    def api_kb_delete():
        data = request.get_json(force=True, silent=True) or {}
        filename = (data.get("filename") or "").strip()
        uid = _get_user_id()
        if not filename:
            return jsonify({"error": "filename required"}), 400
        with user_write_lock("knowledge", uid):
            ok = ks.delete_file(filename, user_id=uid)
        if not ok:
            return jsonify({"error": "file not found"}), 404
        return jsonify({"status": "deleted", "filename": filename})

    @app.route("/api/code/knowledge/search", methods=["POST"])
    def api_kb_search():
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        uid = _get_user_id()
        max_results = int(data.get("max_results", 5))
        max_content_chars = int(data.get("max_content_chars", 4000))
        if not query:
            return jsonify({"error": "query required"}), 400
        results = ks.search(
            query=query,
            user_id=uid,
            max_results=max_results,
            max_content_chars=max_content_chars,
        )
        return jsonify({"query": query, "results": results, "count": len(results), "user_id": uid})

    @app.route("/api/code/knowledge/reindex", methods=["POST"])
    def api_kb_reindex():
        uid = _get_user_id()
        with user_write_lock("knowledge", uid):
            ks.invalidate_cache(user_id=uid)
        return jsonify({"status": "reindexed", "user_id": uid})
