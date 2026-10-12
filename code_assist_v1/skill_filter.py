"""
Skill discovery and routing helpers for code_assist_v1.

Only scans code_assist_v1/skills. Full SKILL.md content is loaded only after a
skill is explicitly or implicitly selected.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from code_assist_v1.config import SKILLS_DIR, USER_DATA_DIR
from code_assist_v1.auth import safe_user_id


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict = {}
    current_section: str | None = None
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i].rstrip()
        i += 1
        raw_line = raw_line.rstrip()
        if not raw_line:
            continue
        if raw_line.startswith(" ") and current_section:
            nested = re.match(r"^\s+([A-Za-z0-9_\-]+):\s*(.*)$", raw_line)
            if nested:
                section = out.setdefault(current_section, {})
                if isinstance(section, dict):
                    key = nested.group(1)
                    val = nested.group(2).strip()
                    if val in {">", "|"}:
                        block: list[str] = []
                        while i < len(lines) and lines[i].startswith("    "):
                            block.append(lines[i].strip())
                            i += 1
                        section[key] = _fold_block_value(block, val)
                    else:
                        section[key] = _parse_frontmatter_value(val)
            continue
        kv = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", raw_line)
        if kv:
            val = kv.group(2).strip()
            key = kv.group(1)
            if val in {">", "|"}:
                block: list[str] = []
                while i < len(lines) and lines[i].startswith(" "):
                    block.append(lines[i].strip())
                    i += 1
                out[key] = _fold_block_value(block, val)
                current_section = None
            else:
                out[key] = _parse_frontmatter_value(val)
                current_section = key if not val else None
    return out


def _fold_block_value(lines: list[str], style: str) -> str:
    if style == "|":
        return "\n".join(lines).strip()
    return " ".join(line for line in lines if line).strip()


def _parse_frontmatter_value(value: str):
    value = (value or "").strip()
    if not value:
        return {}
    if value and (value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return bool(value)


def _routing_fields(fm: dict) -> dict:
    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return {
        "name": str(fm.get("name") or ""),
        "description": str(fm.get("description") or ""),
        "tags": _as_list(fm.get("tags") or metadata.get("tags")),
        "when_to_use": str(fm.get("when_to_use") or metadata.get("when_to_use") or ""),
        "when_not_to_use": str(fm.get("when_not_to_use") or metadata.get("when_not_to_use") or ""),
        "domains": _as_list(fm.get("domains") or metadata.get("domains")),
        "services": _as_list(fm.get("services") or metadata.get("services")),
        "tool_dependencies": _as_list(
            fm.get("tool_dependencies") or metadata.get("tool_dependencies") or fm.get("tools") or metadata.get("tools")
        ),
        "allow_implicit_invocation": _as_bool(
            fm.get("allow_implicit_invocation", metadata.get("allow_implicit_invocation", True))
        ),
    }


def _user_skills_dir(user_id: str | None = None) -> str | None:
    uid = safe_user_id(user_id)
    if not uid:
        return None
    return os.path.join(USER_DATA_DIR, uid, "skills")


def _scan_skill_dir(root: str, scope: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not os.path.isdir(root):
        return result
    for name in sorted(os.listdir(root)):
        sd = os.path.join(root, name)
        skill_md = os.path.join(sd, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                head = f.read(4000)
        except Exception:
            head = ""
        fm = _parse_frontmatter(head)
        routing = _routing_fields(fm)
        result[name] = {
            "id": name,
            "name": routing["name"] or name,
            "description": routing["description"][:400],
            "tags": routing["tags"],
            "when_to_use": routing["when_to_use"],
            "when_not_to_use": routing["when_not_to_use"],
            "domains": routing["domains"],
            "services": routing["services"],
            "tool_dependencies": routing["tool_dependencies"],
            "allow_implicit_invocation": routing["allow_implicit_invocation"],
            "path": skill_md,
            "scope": scope,
            "editable": scope == "user",
            "frontmatter": fm,
        }
    return result


def scan_all_skills(user_id: str | None = None) -> dict[str, dict]:
    result = _scan_skill_dir(SKILLS_DIR, "shared")
    user_dir = _user_skills_dir(user_id)
    if user_dir:
        result.update(_scan_skill_dir(user_dir, "user"))
    return result


def list_skills(query: str = "", user_id: str | None = None) -> list[dict]:
    all_skills = scan_all_skills(user_id=user_id)
    if query:
        ql = query.lower()
        keep = [
            sid for sid, info in all_skills.items()
            if ql in _routing_text(sid, info).lower()
        ]
    else:
        keep = list(all_skills.keys())
    out = []
    for sid in sorted(keep):
        info = all_skills[sid]
        out.append(_public_skill_info(sid, info))
    return out


def get_skill_path(skill_id: str, user_id: str | None = None) -> Optional[str]:
    user_dir = _user_skills_dir(user_id)
    if user_dir:
        p = os.path.join(user_dir, skill_id, "SKILL.md")
        if os.path.isfile(p):
            return p
    p = os.path.join(SKILLS_DIR, skill_id, "SKILL.md")
    return p if os.path.isfile(p) else None


def get_user_skill_path(skill_id: str, user_id: str | None = None) -> Optional[str]:
    user_dir = _user_skills_dir(user_id)
    if not user_dir:
        return None
    p = os.path.join(user_dir, skill_id, "SKILL.md")
    return p if os.path.isfile(p) else None


def user_skill_dir(skill_id: str, user_id: str | None = None) -> str:
    user_dir = _user_skills_dir(user_id)
    if not user_dir:
        raise ValueError("authenticated user required")
    return os.path.join(user_dir, skill_id)


def load_skill_content(skill_id: str, max_chars: int = 8000, user_id: str | None = None) -> Optional[str]:
    path = get_skill_path(skill_id, user_id=user_id)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (truncated)"
    return content


def get_skill_meta(skill_id: str, user_id: str | None = None) -> Optional[dict]:
    path = get_skill_path(skill_id, user_id=user_id)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4000)
    except Exception:
        head = ""
    fm = _parse_frontmatter(head)
    routing = _routing_fields(fm)
    user_path = get_user_skill_path(skill_id, user_id=user_id)
    scope = "user" if user_path and os.path.abspath(user_path) == os.path.abspath(path) else "shared"
    return {
        "id": skill_id,
        "name": routing["name"] or skill_id,
        "description": routing["description"],
        "path": path,
        "tags": routing["tags"],
        "when_to_use": routing["when_to_use"],
        "when_not_to_use": routing["when_not_to_use"],
        "domains": routing["domains"],
        "services": routing["services"],
        "tool_dependencies": routing["tool_dependencies"],
        "allow_implicit_invocation": routing["allow_implicit_invocation"],
        "frontmatter": fm,
        "scope": scope,
        "editable": scope == "user",
    }


def resolve_skill_alias(value: str, user_id: str | None = None) -> Optional[str]:
    key = (value or "").strip().lower()
    if not key:
        return None
    all_skills = scan_all_skills(user_id=user_id)
    if key in all_skills:
        return key
    normalized = re.sub(r"[\s_]+", "-", key)
    if normalized in all_skills:
        return normalized
    for sid, info in all_skills.items():
        names = {
            sid.lower(),
            str(info.get("name") or "").lower(),
            re.sub(r"[\s_]+", "-", str(info.get("name") or "").lower()),
        }
        if key in names or normalized in names:
            return sid
    return None


def auto_select_skills(
    query: str,
    explicit_skill_ids: list[str] | None = None,
    user_id: str | None = None,
    max_results: int = 3,
    min_score: float = 3.0,
) -> list[dict]:
    query = (query or "").strip()
    explicit: list[str] = []
    for sid in explicit_skill_ids or []:
        resolved = resolve_skill_alias(str(sid), user_id=user_id) or str(sid)
        if get_skill_path(resolved, user_id=user_id) and resolved not in explicit:
            explicit.append(resolved)

    all_skills = scan_all_skills(user_id=user_id)
    if not query:
        return [
            {"id": sid, "reason": "explicit", "score": 999.0, "explicit": True}
            for sid in explicit
        ]

    ql = query.lower()
    terms = [t for t in re.split(r"[^0-9A-Za-z가-힣_+-]+", ql) if len(t) >= 2]
    scored: list[tuple[float, dict]] = []
    for sid, info in all_skills.items():
        if sid in explicit:
            scored.append((999.0, {"id": sid, "reason": "explicit", "score": 999.0, "explicit": True}))
            continue
        if not bool(info.get("allow_implicit_invocation", True)):
            continue
        negative = str(info.get("when_not_to_use") or "").lower()
        fields = {
            "id": sid,
            "name": info.get("name") or "",
            "description": info.get("description") or "",
            "tags": " ".join(info.get("tags") or []),
            "when_to_use": info.get("when_to_use") or "",
            "domains": " ".join(info.get("domains") or []),
            "services": " ".join(info.get("services") or []),
        }
        score = 0.0
        reasons: list[str] = []
        for term in terms:
            if term in str(fields["id"]).lower():
                score += 8
                reasons.append(f"id:{term}")
            if term in str(fields["name"]).lower():
                score += 7
                reasons.append(f"name:{term}")
            if term in str(fields["tags"]).lower():
                score += 5
                reasons.append(f"tag:{term}")
            if term in str(fields["domains"]).lower() or term in str(fields["services"]).lower():
                score += 4
                reasons.append(f"domain:{term}")
            if term in str(fields["when_to_use"]).lower():
                score += 3
                reasons.append(f"when:{term}")
            if term in str(fields["description"]).lower():
                score += 2
                reasons.append(f"desc:{term}")
            if term in negative:
                score -= 5
        if sid.lower() in ql or str(info.get("name") or "").lower() in ql:
            score += 10
            reasons.append("direct mention")
        if score >= min_score:
            scored.append(
                (
                    score,
                    {
                        "id": sid,
                        "name": info.get("name") or sid,
                        "description": info.get("description") or "",
                        "score": round(score, 3),
                        "reason": ", ".join(dict.fromkeys(reasons[:6])) or "metadata match",
                        "explicit": False,
                    },
                )
            )

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen: list[dict] = []
    seen: set[str] = set()
    limit = max_results + len(explicit)
    for _, item in scored:
        if item["id"] in seen:
            continue
        chosen.append(item)
        seen.add(item["id"])
        if len(chosen) >= limit:
            break
    return chosen


def _public_skill_info(sid: str, info: dict) -> dict:
    return {
        "id": sid,
        "name": info.get("name") or sid,
        "description": info.get("description", ""),
        "tags": info.get("tags") or [],
        "when_to_use": info.get("when_to_use") or "",
        "when_not_to_use": info.get("when_not_to_use") or "",
        "domains": info.get("domains") or [],
        "services": info.get("services") or [],
        "tool_dependencies": info.get("tool_dependencies") or [],
        "allow_implicit_invocation": bool(info.get("allow_implicit_invocation", True)),
        "scope": info.get("scope") or "shared",
        "editable": bool(info.get("editable", False)),
    }


def _routing_text(sid: str, info: dict) -> str:
    return " ".join(
        [
            sid,
            str(info.get("name") or ""),
            str(info.get("description") or ""),
            " ".join(info.get("tags") or []),
            str(info.get("when_to_use") or ""),
            str(info.get("when_not_to_use") or ""),
            " ".join(info.get("domains") or []),
            " ".join(info.get("services") or []),
            " ".join(info.get("tool_dependencies") or []),
        ]
    )
