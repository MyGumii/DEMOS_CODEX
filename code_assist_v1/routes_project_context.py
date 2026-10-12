"""
Project Context routes.
"""
from __future__ import annotations

import os

from flask import jsonify, request

from code_assist_v1 import project_context_store as pcs
from code_assist_v1.auth import current_user_id
from code_assist_v1.concurrency import user_write_lock
from code_assist_v1.config import MAX_UPLOAD_MB
from code_assist_v1.job_manager import manager as job_manager


def _uid() -> str | None:
    auth_uid = current_user_id()
    if auth_uid:
        return auth_uid
    if request.method == "GET":
        value = (request.args.get("user_id") or "").strip()
        return value or None
    if request.form:
        value = (request.form.get("user_id") or "").strip()
        if value:
            return value
    data = request.get_json(silent=True) or {}
    value = (data.get("user_id") or "").strip()
    return value or None


def _project_id() -> str:
    if request.method == "GET":
        return (request.args.get("project_id") or "default").strip() or "default"
    if request.form:
        value = (request.form.get("project_id") or "").strip()
        if value:
            return value
    data = request.get_json(silent=True) or {}
    return (data.get("project_id") or "default").strip() or "default"


def _async_requested() -> bool:
    value = (
        request.form.get("async")
        or request.form.get("async_mode")
        or request.args.get("async")
        or request.args.get("async_mode")
        or ""
    )
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_async_requested(data: dict) -> bool:
    return str(data.get("async") or data.get("async_mode") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def register_project_context_routes(app):
    @app.route("/api/code/project_context/import", methods=["POST"])
    def api_project_context_import():
        if "file" not in request.files:
            return jsonify({"error": "file required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename required"}), 400
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_MB * 1024 * 1024:
            return jsonify({"error": f"file size exceeds limit ({MAX_UPLOAD_MB}MB)"}), 413
        raw = f.read()
        uid = _uid()
        project_id = _project_id()
        filename = f.filename
        domain = (request.form.get("domain") or "").strip()
        service = (request.form.get("service") or "").strip()

        def _run_import(ctx):
            ctx.update(progress=5, message="extracting project context")
            ctx.raise_if_cancelled()
            with user_write_lock("project_context", uid, project_id):
                ctx.update(progress=35, message="indexing project context")
                result = pcs.import_source_bytes(
                    filename=filename,
                    raw=raw,
                    user_id=uid,
                    project_id=project_id,
                    domain=domain,
                    service=service,
                )
            return {"status": "imported", **result}

        if _async_requested():
            job = job_manager.submit(
                user_id=uid or "",
                kind="project_context.import_source",
                target=_run_import,
                session_id=project_id,
                metadata={"filename": filename, "project_id": project_id, "domain": domain, "service": service},
            )
            return jsonify({"status": "queued", "job": job, "job_id": job["job_id"]}), 202

        try:
            with user_write_lock("project_context", uid, project_id):
                result = pcs.import_source_bytes(
                    filename=filename,
                    raw=raw,
                    user_id=uid,
                    project_id=project_id,
                    domain=domain,
                    service=service,
                )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "imported", **result})

    @app.route("/api/code/project_context/import_text", methods=["POST"])
    def api_project_context_import_text():
        data = request.get_json(force=True, silent=True) or {}
        filename = (data.get("filename") or data.get("name") or "workspace.md").strip()
        markdown = data.get("markdown") or data.get("content") or ""
        if not markdown or not str(markdown).strip():
            return jsonify({"error": "markdown required"}), 400
        if len(str(markdown).encode("utf-8", errors="ignore")) > MAX_UPLOAD_MB * 1024 * 1024:
            return jsonify({"error": f"text size exceeds limit ({MAX_UPLOAD_MB}MB)"}), 413

        uid = _uid()
        project_id = (data.get("project_id") or _project_id()).strip() or "default"
        domain = (data.get("domain") or "").strip()
        service = (data.get("service") or "").strip()
        extractor = (data.get("extractor") or "workspace").strip()
        warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
        source_uri = (data.get("source_uri") or "").strip()
        bridge_meta = data.get("bridge_meta") if isinstance(data.get("bridge_meta"), dict) else {}

        def _run_import_text(ctx):
            ctx.update(progress=10, message="indexing project markdown")
            ctx.raise_if_cancelled()
            with user_write_lock("project_context", uid, project_id):
                result = pcs.import_markdown_source(
                    filename=filename,
                    markdown=str(markdown),
                    user_id=uid,
                    project_id=project_id,
                    domain=domain,
                    service=service,
                    extractor=extractor,
                    warnings=warnings,
                    source_uri=source_uri,
                    bridge_meta=bridge_meta,
                )
            return {"status": "imported", **result}

        if _json_async_requested(data):
            job = job_manager.submit(
                user_id=uid or "",
                kind="project_context.import_text",
                target=_run_import_text,
                session_id=project_id,
                metadata={"filename": filename, "project_id": project_id, "extractor": extractor},
            )
            return jsonify({"status": "queued", "job": job, "job_id": job["job_id"]}), 202

        try:
            with user_write_lock("project_context", uid, project_id):
                result = pcs.import_markdown_source(
                    filename=filename,
                    markdown=str(markdown),
                    user_id=uid,
                    project_id=project_id,
                    domain=domain,
                    service=service,
                    extractor=extractor,
                    warnings=warnings,
                    source_uri=source_uri,
                    bridge_meta=bridge_meta,
                )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "imported", **result})

    @app.route("/api/code/project_context/list")
    def api_project_context_list():
        project_id = _project_id()
        items = pcs.list_sources(user_id=_uid(), project_id=project_id)
        return jsonify({
            "items": items,
            "count": len(items),
            "project_id": project_id,
            "user_id": _uid(),
        })

    @app.route("/api/code/project_context/delete", methods=["POST"])
    def api_project_context_delete():
        data = request.get_json(force=True, silent=True) or {}
        source_id = (data.get("source_id") or data.get("id") or "").strip()
        if not source_id:
            return jsonify({"error": "source_id required"}), 400
        uid = _uid()
        project_id = _project_id()
        with user_write_lock("project_context", uid, project_id):
            ok = pcs.delete_source(source_id, user_id=uid, project_id=project_id)
        if not ok:
            return jsonify({"error": "project context source not found"}), 404
        return jsonify({"status": "deleted", "source_id": source_id, "project_id": project_id})

    @app.route("/api/code/project_context/clear", methods=["POST"])
    def api_project_context_clear():
        uid = _uid()
        project_id = _project_id()
        with user_write_lock("project_context", uid, project_id):
            removed = pcs.clear_project(user_id=uid, project_id=project_id)
        return jsonify({"status": "cleared", "removed": removed, "project_id": project_id})

    @app.route("/api/code/project_context/search", methods=["POST"])
    def api_project_context_search():
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query required"}), 400
        project_id = (data.get("project_id") or _project_id()).strip() or "default"
        results = pcs.search_context(
            query=query,
            user_id=_uid(),
            project_id=project_id,
            max_results=int(data.get("max_results") or 8),
        )
        return jsonify({"query": query, "results": results, "count": len(results), "project_id": project_id})

    @app.route("/api/code/project_context/scores")
    def api_project_context_scores():
        project_id = _project_id()
        limit = int(request.args.get("limit") or 100)
        items = pcs.list_score_log(user_id=_uid(), project_id=project_id, limit=limit)
        return jsonify({"items": items, "count": len(items), "project_id": project_id})
