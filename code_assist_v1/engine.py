"""System prompt and context block builders for code_assist_v1."""
from __future__ import annotations

import os
from typing import Optional

from code_assist_v1.config import (
    MAX_KNOWLEDGE_INJECT_CHARS,
    MAX_WORKSPACE_FILE_CHARS,
    MAX_WORKSPACE_TOTAL_CHARS,
)
from code_assist_v1.prompts import (
    ANTI_HALLUCINATION,
    CODING_SYSTEM_PROMPT,
    GENERAL_ASSISTANT_PERSONA,
    KNOWLEDGE_INJECT_HEADER,
    SKILL_INJECT_HEADER,
    WORKSPACE_INJECT_HEADER,
)
from code_assist_v1.skill_filter import load_skill_content


def build_coding_system_prompt(
    skill_ids: list[str] | None = None,
    user_extra: str = "",
    n_ctx: int = 32768,
    user_id: str | None = None,
) -> str:
    """Build the base system prompt with optional active skill content."""
    parts: list[str] = [
        CODING_SYSTEM_PROMPT.strip(),
        GENERAL_ASSISTANT_PERSONA.strip(),
        ANTI_HALLUCINATION.strip(),
    ]

    skill_ids = skill_ids or []
    if skill_ids:
        parts.append(SKILL_INJECT_HEADER.strip())
        budget = max(2000, int(n_ctx * 0.4) - 1500)
        used = 0
        for sid in skill_ids:
            content = load_skill_content(sid, max_chars=4000, user_id=user_id)
            if not content:
                continue
            chunk = f"\n--- Skill: {sid} ---\n{content}"
            if used + len(chunk) > budget:
                remain = budget - used
                if remain > 200:
                    parts.append(chunk[:remain] + "\n... (truncated)")
                break
            parts.append(chunk)
            used += len(chunk)

    if user_extra and user_extra.strip():
        parts.append("\n=== User Extra Instructions ===\n" + user_extra.strip())

    return "\n\n".join(parts)


def build_knowledge_block(
    query: str,
    knowledge_results: list[dict],
    intent_search_only: bool = False,
) -> Optional[dict]:
    """Build a system context block from BM25 knowledge results."""
    if not knowledge_results:
        return None

    body_parts = [KNOWLEDGE_INJECT_HEADER.strip(), f"검색어: {query}", ""]
    total = 0
    for result in knowledge_results:
        chunk = (result.get("content") or "")[:4000]
        if total + len(chunk) > MAX_KNOWLEDGE_INJECT_CHARS:
            chunk = chunk[: max(0, MAX_KNOWLEDGE_INJECT_CHARS - total)]
            if not chunk:
                break
        body_parts.append(
            f"--- 문서: {result.get('filename', '?')} (score {result.get('score', 0):.1f}) ---"
        )
        body_parts.append(chunk)
        body_parts.append("")
        total += len(chunk)

    if intent_search_only:
        body_parts.append(
            "사용자가 검색/목록 확인을 요청했습니다. 파일명, 관련도, 간단한 설명만 보여주고 "
            "본문 분석이나 요약은 요청받은 경우에만 수행하세요."
        )
    else:
        body_parts.append(
            "위 발췌만 근거로 사용하세요. 발췌 밖의 사실은 단정하지 말고, "
            "가능하면 어떤 문서에서 가져온 내용인지 답변에 표시하세요."
        )

    return {"role": "system", "content": "\n".join(body_parts)}


def build_workspace_block(workspace_files: list[dict]) -> Optional[dict]:
    """Build a system context block from attached workspace files."""
    if not workspace_files:
        return None

    parts = [WORKSPACE_INJECT_HEADER.strip()]
    total = 0
    for file_info in workspace_files:
        filename = file_info.get("filename", "untitled")
        lang = file_info.get("lang", "")
        content = file_info.get("content") or ""
        if len(content) > MAX_WORKSPACE_FILE_CHARS:
            content = content[:MAX_WORKSPACE_FILE_CHARS] + "\n... (file truncated)"
        if total + len(content) > MAX_WORKSPACE_TOTAL_CHARS:
            remain = MAX_WORKSPACE_TOTAL_CHARS - total
            if remain < 300:
                parts.append(f"\n--- {filename} (omitted: workspace context budget exceeded) ---")
                continue
            content = content[:remain] + "\n... (truncated by workspace context budget)"
        fence_lang = lang or _guess_lang(filename)
        parts.append(f"\n--- File: {filename} ---\n```{fence_lang}\n{content}\n```")
        total += len(content)

    return {"role": "system", "content": "\n".join(parts)}


def _guess_lang(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "scss",
        ".sass": "sass",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".ps1": "powershell",
        ".bat": "batch",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".sql": "sql",
        ".vue": "vue",
        ".svelte": "svelte",
        ".xml": "xml",
    }.get(ext, "")


def trim_message_history(messages: list[dict], max_turns: int = 12) -> list[dict]:
    """Trim chat history while preserving the first system messages."""
    if len(messages) <= max_turns:
        return messages
    sys_head = [m for m in messages[:2] if m.get("role") == "system"]
    recent = messages[-max_turns:]
    return sys_head + recent
