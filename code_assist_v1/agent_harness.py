"""
Lightweight agent harness planning for chat requests.

This module creates a request-level execution plan that the chat route injects
into the model so Coding, LLM Wiki, memory, and spec-driven workflows are
handled consistently.
"""
from __future__ import annotations

from typing import Any


def analyze_request(
    query: str,
    *,
    enable_knowledge: bool = False,
    enable_memory: bool = True,
    skill_ids: list[str] | None = None,
    auto_skills: bool = False,
    workspace_files: list[dict] | None = None,
    pinned_ids: list[str] | None = None,
    memory_ids: list[str] | None = None,
) -> dict[str, Any]:
    query = query or ""
    low = query.lower()
    skill_ids = skill_ids or []
    workspace_files = workspace_files or []
    pinned_ids = pinned_ids or []
    memory_ids = memory_ids or []

    is_code = _has_any(low, [
        "코드", "구현", "수정", "버그", "디버그", "테스트", "함수", "class", "api",
        "python", "javascript", "typescript", "flask", "react", "파일", "에러", "traceback",
    ])
    is_spec = _has_any(low, [
        "spec", "스펙", "요구사항", "acceptance", "criteria", "검증 기준", "정책",
        "규칙", "제약", "상태", "인터페이스", "시나리오",
    ])
    asks_second_brain = enable_knowledge or bool(pinned_ids) or _has_any(
        low,
        ["second brain", "세컨", "지식", "wiki", "위키", "그래프", "노드"],
    )
    asks_memory = enable_memory and (_has_any(low, ["기억", "remember", "앞으로", "항상"]) or bool(memory_ids))
    is_search = _has_any(low, ["찾아", "검색", "어디", "관련", "목록"])

    modes = []
    if is_code:
        modes.append("coding")
    if asks_second_brain:
        modes.append("llm_wiki")
    if is_spec or (is_code and asks_second_brain):
        modes.append("spec_driven")
    if asks_memory:
        modes.append("memory_aware")
    if not modes:
        modes.append("general_assistant")

    context_order = ["current_chat"]
    if workspace_files:
        context_order.append("workspace_files")
    if pinned_ids:
        context_order.append("pinned_wiki_pages")
    if asks_second_brain:
        context_order.extend(["wiki_index", "wiki_pages", "graph_relations", "recent_wiki_log"])
    if enable_memory:
        context_order.append("user_project_memory")
    if skill_ids or auto_skills:
        context_order.append("skills")

    validation = []
    if is_spec:
        validation.extend(["requirements", "constraints", "acceptance_criteria"])
    if is_code:
        validation.extend(["existing_code_pattern", "minimal_change", "tests_or_smoke_check"])
    if asks_second_brain:
        validation.extend(["wiki_source_trace", "diff_not_assumed_applied", "graph_relation_not_body_fact"])
    if enable_memory:
        validation.append("memory_is_not_official_spec")

    tools = []
    if asks_second_brain:
        tools.extend(["wiki.index", "wiki.search", "graph.expand", "wiki.log"])
    if is_spec:
        tools.extend(["spec.extract", "spec.validate"])
    if is_code:
        tools.extend(["workspace.inspect", "workspace.edit", "workspace.test"])
    if enable_memory:
        tools.extend(["memory.search", "memory.propose"])
    if skill_ids or auto_skills:
        tools.append("skill.route")

    return {
        "modes": modes,
        "intent": {
            "coding": is_code,
            "second_brain": asks_second_brain,
            "spec_driven": is_spec or (is_code and asks_second_brain),
            "memory_aware": asks_memory or bool(memory_ids),
            "search": is_search,
        },
        "context_order": _dedupe(context_order),
        "tools": _dedupe(tools),
        "validation": _dedupe(validation),
        "signals": {
            "skills": skill_ids,
            "auto_skills": auto_skills,
            "workspace_file_count": len(workspace_files),
            "pinned_count": len(pinned_ids),
            "memory_count": len(memory_ids),
            "second_brain_enabled": enable_knowledge,
            "memory_enabled": enable_memory,
        },
    }


def build_harness_context(plan: dict[str, Any]) -> str:
    lines = [
        "=== Agent Harness Runtime Plan ===",
        "- 이 계획은 현재 요청을 처리하기 위한 실행/컨텍스트 선택 지침입니다.",
        "- 사용자의 명시 요청과 실제 제공된 컨텍스트가 이 계획보다 우선합니다.",
        f"- modes: {', '.join(plan.get('modes') or [])}",
        f"- context_order: {', '.join(plan.get('context_order') or [])}",
        f"- candidate_tools: {', '.join(plan.get('tools') or []) or '-'}",
        f"- validation_gate: {', '.join(plan.get('validation') or []) or '-'}",
        "",
        "규칙:",
        "- Coding mode에서는 현재 파일과 기존 패턴을 우선합니다.",
        "- LLM Wiki mode에서는 index.md, 관련 markdown page, graph relation, log.md 순서로 근거를 해석합니다.",
        "- graph relation은 연결성 정보이고, markdown 본문 사실과 구분해야 합니다.",
        "- 승인되지 않은 Wiki diff를 이미 반영된 지식처럼 말하지 않습니다.",
        "- Spec-driven mode에서는 요구사항, 제약, acceptance criteria, 테스트 기준을 분리합니다.",
        "- Memory는 사용자/프로젝트 선호로만 사용하고 공식 spec처럼 단정하지 않습니다.",
    ]
    return "\n".join(lines)


def _has_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def _dedupe(items: list[str]) -> list[str]:
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
