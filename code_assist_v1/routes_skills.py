"""
code_assist_v1/routes_skills.py - 스킬 CRUD (단일 skills/ 폴더)
"""
from __future__ import annotations
import os
import re
import shutil
from datetime import datetime
from flask import request, jsonify

from code_assist_v1.auth import current_user_id, scoped_dir
from code_assist_v1.skill_filter import (
    list_skills,
    get_skill_meta,
    load_skill_content,
    get_skill_path,
    user_skill_dir,
    auto_select_skills,
    resolve_skill_alias,
)
from code_assist_v1.skill_whitelist import (
    load_user_whitelist,
    save_user_whitelist,
)


_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{1,63}$")


def _validate_skill_id(skill_id: str) -> tuple[bool, str]:
    if not skill_id:
        return False, "skill_id 가 비어있습니다"
    if not _SKILL_ID_RE.match(skill_id):
        return False, "skill_id 는 소문자/숫자/하이픈/언더스코어만 (2~64자)"
    return True, ""


def _build_frontmatter(meta: dict) -> str:
    name = meta.get("name") or meta.get("id") or ""
    desc = meta.get("description") or ""
    license_ = meta.get("license") or "MIT"
    author = meta.get("author") or ""
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    domains = meta.get("domains") or []
    services = meta.get("services") or []
    tool_dependencies = meta.get("tool_dependencies") or []
    for key, value in (("domains", domains), ("services", services), ("tool_dependencies", tool_dependencies)):
        if isinstance(value, str):
            meta[key] = [t.strip() for t in value.split(",") if t.strip()]
    created = meta.get("created") or datetime.now().strftime("%Y-%m-%d")

    lines = ["---", f"name: {name}", f"description: {desc}", f"license: {license_}", "metadata:"]
    if author:
        lines.append(f"  author: {author}")
    lines.append(f"  created: {created}")
    if tags:
        lines.append(f"  tags: [{', '.join(tags)}]")
    if meta.get("when_to_use"):
        lines.append(f"  when_to_use: {meta.get('when_to_use')}")
    if meta.get("when_not_to_use"):
        lines.append(f"  when_not_to_use: {meta.get('when_not_to_use')}")
    if meta.get("domains"):
        lines.append(f"  domains: [{', '.join(meta.get('domains') or [])}]")
    if meta.get("services"):
        lines.append(f"  services: [{', '.join(meta.get('services') or [])}]")
    if meta.get("tool_dependencies"):
        lines.append(f"  tool_dependencies: [{', '.join(meta.get('tool_dependencies') or [])}]")
    lines.append(f"  allow_implicit_invocation: {str(meta.get('allow_implicit_invocation', True)).lower()}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _sync_harness():
    """skills/ 변경 시 ToolRegistry 갱신."""
    try:
        from code_assist_v1.harness_setup import sync_skills, HARNESS_AVAILABLE
        if HARNESS_AVAILABLE:
            sync_skills()
    except Exception:
        pass


def register_skill_routes(app):

    @app.route("/api/code/skills/list")
    def api_skills_list():
        query = request.args.get("q", "").strip()
        items = list_skills(query=query, user_id=current_user_id())
        return jsonify({"total": len(items), "items": items})

    @app.route("/api/code/skills/auto_select", methods=["POST"])
    def api_skills_auto_select():
        data = request.get_json(force=True, silent=True) or {}
        query = data.get("query") or data.get("message") or ""
        explicit = data.get("explicit") or data.get("skills") or []
        if isinstance(explicit, str):
            explicit = [explicit]
        max_results = int(data.get("max_results") or 3)
        min_score = float(data.get("min_score") or 3.0)
        items = auto_select_skills(
            query=query,
            explicit_skill_ids=[str(x) for x in explicit if str(x).strip()],
            user_id=current_user_id(),
            max_results=max_results,
            min_score=min_score,
        )
        return jsonify({
            "items": items,
            "skill_ids": [item["id"] for item in items],
            "count": len(items),
        })

    @app.route("/api/code/skills/resolve")
    def api_skills_resolve():
        value = (request.args.get("q") or request.args.get("name") or "").strip()
        sid = resolve_skill_alias(value, user_id=current_user_id())
        if not sid:
            return jsonify({"error": "skill not found"}), 404
        return jsonify({"id": sid})

    @app.route("/api/code/skills/<skill_id>")
    def api_skills_view(skill_id):
        meta = get_skill_meta(skill_id, user_id=current_user_id())
        if not meta:
            return jsonify({"error": "스킬을 찾을 수 없습니다"}), 404
        content = load_skill_content(skill_id, max_chars=50000, user_id=current_user_id()) or ""
        return jsonify({"meta": meta, "content": content})

    @app.route("/api/code/skills/create", methods=["POST"])
    def api_skills_create():
        data = request.get_json(force=True, silent=True) or {}
        skill_id = (data.get("id") or "").strip().lower()
        ok, err = _validate_skill_id(skill_id)
        if not ok:
            return jsonify({"error": err}), 400

        skill_dir = user_skill_dir(skill_id, current_user_id())
        if os.path.exists(skill_dir):
            return jsonify({"error": "이미 같은 ID 의 스킬이 존재합니다"}), 409

        os.makedirs(skill_dir, exist_ok=True)
        fm = _build_frontmatter({
            "id": skill_id,
            "name": data.get("name", skill_id),
            "description": data.get("description", ""),
            "license": data.get("license", "MIT"),
            "author": data.get("author", ""),
            "tags": data.get("tags", []),
            "when_to_use": data.get("when_to_use", ""),
            "when_not_to_use": data.get("when_not_to_use", ""),
            "domains": data.get("domains", []),
            "services": data.get("services", []),
            "tool_dependencies": data.get("tool_dependencies", []),
            "allow_implicit_invocation": bool(data.get("allow_implicit_invocation", True)),
        })
        body = data.get("body", "") or f"# {data.get('name', skill_id)}\n\n{data.get('description', '')}\n"
        skill_md = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(fm + body)
        _sync_harness()
        return jsonify({"status": "created", "id": skill_id, "path": skill_md, "scope": "user"})

    @app.route("/api/code/skills/update", methods=["POST"])
    def api_skills_update():
        data = request.get_json(force=True, silent=True) or {}
        skill_id = (data.get("id") or "").strip().lower()
        ok, err = _validate_skill_id(skill_id)
        if not ok:
            return jsonify({"error": err}), 400

        existing_path = get_skill_path(skill_id, user_id=current_user_id())
        if not existing_path:
            return jsonify({"error": "스킬 없음"}), 404

        skill_dir = user_skill_dir(skill_id, current_user_id())
        os.makedirs(skill_dir, exist_ok=True)
        skill_md = os.path.join(skill_dir, "SKILL.md")

        if "content" in data and isinstance(data["content"], str):
            new_content = data["content"]
        else:
            fm = _build_frontmatter({
                "id": skill_id,
                "name": data.get("name", skill_id),
                "description": data.get("description", ""),
                "license": data.get("license", "MIT"),
                "author": data.get("author", ""),
                "tags": data.get("tags", []),
                "when_to_use": data.get("when_to_use", ""),
                "when_not_to_use": data.get("when_not_to_use", ""),
                "domains": data.get("domains", []),
                "services": data.get("services", []),
                "tool_dependencies": data.get("tool_dependencies", []),
                "allow_implicit_invocation": bool(data.get("allow_implicit_invocation", True)),
            })
            new_content = fm + (data.get("body") or "")
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(new_content)
        _sync_harness()
        return jsonify({"status": "updated", "id": skill_id, "path": skill_md, "scope": "user"})

    @app.route("/api/code/skills/delete", methods=["POST"])
    def api_skills_delete():
        data = request.get_json(force=True, silent=True) or {}
        skill_id = (data.get("id") or "").strip().lower()
        ok, err = _validate_skill_id(skill_id)
        if not ok:
            return jsonify({"error": err}), 400
        skill_dir = user_skill_dir(skill_id, current_user_id())
        if not os.path.isdir(skill_dir):
            if get_skill_path(skill_id, user_id=current_user_id()):
                return jsonify({"error": "shared skill cannot be deleted; create a user override first"}), 403
            return jsonify({"error": "스킬 없음"}), 404
        try:
            shutil.rmtree(skill_dir)
        except Exception as e:
            return jsonify({"error": f"삭제 실패: {e}"}), 500
        _sync_harness()
        return jsonify({"status": "deleted", "id": skill_id})

    @app.route("/api/code/skills/whitelist", methods=["GET", "POST"])
    def api_skills_whitelist():
        whitelist_path = os.path.join(scoped_dir("settings", current_user_id()), "skill_whitelist.json")
        if request.method == "GET":
            data = load_user_whitelist(whitelist_path) or {"include": [], "exclude": []}
            return jsonify(data)
        data = request.get_json(force=True, silent=True) or {}
        norm = {
            "include": [s for s in (data.get("include") or []) if isinstance(s, str)],
            "exclude": [s for s in (data.get("exclude") or []) if isinstance(s, str)],
        }
        save_user_whitelist(whitelist_path, norm)
        return jsonify({"status": "saved", "data": norm})
