"""
Background job status routes.
"""
from __future__ import annotations

from flask import jsonify, request

from code_assist_v1.auth import current_user, current_user_id, is_admin_user
from code_assist_v1.job_manager import manager as job_manager


def register_job_routes(app):
    @app.route("/api/code/jobs")
    def api_jobs_list():
        uid = current_user_id()
        if not uid:
            return jsonify({"error": "authentication required"}), 401
        include_all = bool(request.args.get("all") == "1" and is_admin_user(uid))
        limit = int(request.args.get("limit") or 100)
        items = job_manager.list_jobs(uid, include_all=include_all, limit=limit)
        return jsonify({
            "items": items,
            "count": len(items),
            "user_id": uid,
            "scope": "all" if include_all else "own",
        })

    @app.route("/api/code/jobs/<path:job_id>")
    def api_jobs_get(job_id):
        user = current_user()
        if not user:
            return jsonify({"error": "authentication required"}), 401
        include_any = bool(user.get("is_admin") and request.args.get("all") == "1")
        job = job_manager.get_job(job_id, user_id=user.get("id"), include_any_user=include_any)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job)

    @app.route("/api/code/jobs/<path:job_id>/cancel", methods=["POST"])
    def api_jobs_cancel(job_id):
        user = current_user()
        if not user:
            return jsonify({"error": "authentication required"}), 401
        include_any = bool(user.get("is_admin") and request.args.get("all") == "1")
        job = job_manager.cancel_job(job_id, user_id=user.get("id") or "", include_any_user=include_any)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify({"status": "cancel_requested" if job.get("cancel_requested") else job.get("status"), "job": job})

