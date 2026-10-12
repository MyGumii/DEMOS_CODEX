"""
Routes for user-scoped long-term memory.
"""
from __future__ import annotations

from flask import jsonify, request

from code_assist_v1.auth import current_user_id
from code_assist_v1 import memory_store as ms


def _uid() -> str | None:
    return current_user_id()


def register_memory_routes(app):
    @app.route("/api/code/memory/list")
    def api_memory_list():
        return jsonify({
            "items": ms.list_memories(
                user_id=_uid(),
                status=(request.args.get("status") or "active").strip() or None,
                memory_type=(request.args.get("memory_type") or "").strip() or None,
                q=request.args.get("q", ""),
            )
        })

    @app.route("/api/code/memory/search", methods=["POST"])
    def api_memory_search():
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query required"}), 400
        statuses = data.get("statuses") or ["active"]
        if isinstance(statuses, str):
            statuses = [statuses]
        items = ms.search_memories(
            query=query,
            user_id=_uid(),
            max_results=int(data.get("max_results") or 8),
            statuses={str(x) for x in statuses},
        )
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/code/memory/create", methods=["POST"])
    def api_memory_create():
        data = request.get_json(force=True, silent=True) or {}
        try:
            item = ms.save_memory(
                user_id=_uid(),
                text=data.get("text") or data.get("content") or "",
                memory_type=data.get("memory_type") or "preference",
                status=data.get("status") or "active",
                scope=data.get("scope") or "user",
                tags=data.get("tags") or None,
                source=data.get("source") or {"type": "manual"},
                confidence=float(data.get("confidence") or 0.8),
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "created", "memory": item})

    @app.route("/api/code/memory/update", methods=["POST"])
    def api_memory_update():
        data = request.get_json(force=True, silent=True) or {}
        memory_id = (data.get("id") or "").strip()
        if not memory_id:
            return jsonify({"error": "id required"}), 400
        try:
            item = ms.update_memory(_uid(), memory_id, data.get("updates") or data)
        except FileNotFoundError:
            return jsonify({"error": "memory not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "updated", "memory": item})

    @app.route("/api/code/memory/delete", methods=["POST"])
    def api_memory_delete():
        data = request.get_json(force=True, silent=True) or {}
        memory_id = (data.get("id") or "").strip()
        if not memory_id:
            return jsonify({"error": "id required"}), 400
        try:
            result = ms.delete_memory(_uid(), memory_id, purge=bool(data.get("purge", False)))
        except FileNotFoundError:
            return jsonify({"error": "memory not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(result)
