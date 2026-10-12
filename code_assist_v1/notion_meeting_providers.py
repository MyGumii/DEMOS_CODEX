"""Notion-only provider policy; never changes the shared SBE model contract."""
from __future__ import annotations

import re

from code_assist_v1 import internal_llm


def family(model_id: str, cfg: dict) -> str:
    name = re.sub(r"[^a-z0-9]", "", model_id.lower() + str(cfg.get("model", "")).lower())
    if "gemma4" in name:
        return "gemma4"
    if "qwen36" in name:
        return "qwen3.6"
    return ""


def codex_connected() -> bool:
    if not internal_llm.codex_internal_enabled():
        return False
    from code_assist_v1 import codex_bridge
    binary = codex_bridge._resolve_codex_bin()
    return bool(binary and codex_bridge._codex_cli_authenticated(binary))


def company_models() -> list[tuple[str, dict]]:
    candidates = internal_llm._candidate_configs("hynix_api")
    return [item for name in ("gemma4", "qwen3.6") for item in candidates if family(*item) == name]


def text_models() -> list[tuple[str, str, dict]]:
    candidates = company_models()
    # One primary per family keeps the three-attempt budget from being consumed
    # by several Gemma aliases before Qwen or the connected fallback is reached.
    result = []
    for name in ("gemma4", "qwen3.6"):
        match = next(((mid, cfg) for mid, cfg in candidates if family(mid, cfg) == name), None)
        if match:
            result.append((match[0], "hynix_api", match[1]))
    if codex_connected():
        result.extend((mid, "codex", cfg) for mid, cfg in internal_llm._candidate_configs("codex")[:1])
    return result
