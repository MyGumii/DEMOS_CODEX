"""
Routes for the lightweight agent harness planner.
"""
from __future__ import annotations

from flask import jsonify, request

from code_assist_v1 import agent_harness as ah


def register_agent_harness_routes(app):
    @app.route("/api/code/harness/plan", methods=["POST"])
    def api_harness_plan():
        data = request.get_json(force=True, silent=True) or {}
        plan = ah.analyze_request(
            data.get("query") or data.get("message") or "",
            enable_knowledge=bool(data.get("enable_knowledge", False)),
            enable_memory=bool(data.get("enable_memory", True)),
            skill_ids=[str(x) for x in (data.get("skills") or [])],
            auto_skills=bool(data.get("auto_skills", False)),
            workspace_files=data.get("workspace_files") or [],
            pinned_ids=[str(x) for x in (data.get("pinned_ids") or data.get("pinned_thoughts") or [])],
            memory_ids=[str(x) for x in (data.get("memory_ids") or [])],
        )
        return jsonify({"plan": plan, "context": ah.build_harness_context(plan)})

    @app.route("/api/code/harness/status")
    def api_code_agent_harness_status():
        return jsonify({
            "status": "ok",
            "planner": "agent_harness.py",
            "layers": ["coding", "second_brain", "spec_driven", "memory_aware"],
            "tools": [
                "wiki.search",
                "compiled.search_evidence",
                "graph.expand",
                "spec.extract",
                "spec.validate",
                "memory.search",
                "memory.propose",
                "skill.route",
                "workspace.inspect",
            ],
        })
