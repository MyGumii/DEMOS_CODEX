"""
SSE chat route for code_assist_v1.
"""
from __future__ import annotations

import json
import hashlib
import time
import warnings
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests as req
from flask import Response, request, stream_with_context

from code_assist_v1 import knowledge_store as ks
from code_assist_v1 import second_brain_store as sb
from code_assist_v1 import memory_store as ms
from code_assist_v1 import agent_harness as ah
from code_assist_v1 import project_context_store as pcs
from code_assist_v1 import notion_store as ns
from code_assist_v1.config import (
    DEFAULT_N_CTX,
    DEFAULT_MODEL_PRIORITY,
    ENV_CONFIG,
    ENV_TO_REGISTRY,
    MAX_HISTORY_TURNS,
    MODEL_REGISTRY,
    STANDALONE,
    TOKEN_SETTINGS,
    get_default_model,
    resolve_api_token,
)
from code_assist_v1.engine import (
    build_coding_system_prompt,
    build_knowledge_block,
    build_workspace_block,
)
from code_assist_v1.session_context import (
    AUTO_COMPACT_RATIO,
    build_compact_summary,
    build_context_status,
    compact_system_block,
    estimate_messages_tokens,
    recent_messages,
    should_auto_compact,
)
from code_assist_v1.codex_bridge import stream_codex_exec
from code_assist_v1.skill_filter import auto_select_skills, resolve_skill_alias
from code_assist_v1.auth import current_user_id
from code_assist_v1.concurrency import gguf_inference_slot
from code_assist_v1.model_runtime import (
    LocalInferenceOwnershipError,
    assert_local_inference_allowed,
)
from code_assist_v1.job_manager import redact_sensitive
from code_assist_v1.model_routing import (
    codex_fallback_enabled,
    ordered_model_candidates,
)

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_object(value: str) -> dict:
    for line in str(value or "").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[5:].strip())
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _stable_attempt_error(event: dict | None, *, exception: bool = False) -> str:
    if exception:
        return "provider_exception"
    status = (event or {}).get("status")
    if isinstance(status, bool) or not isinstance(status, int):
        return "empty_or_malformed_response"
    if status == 401 or status == 403:
        return "provider_authentication"
    if status == 408 or status == 504:
        return "provider_timeout"
    if status == 429:
        return "provider_rate_limited"
    if status >= 500:
        return "provider_unavailable"
    if status >= 400:
        return "provider_rejected_request"
    return "provider_failed"


def _safe_error(value) -> str:
    return redact_sensitive(value)


def _last_user_query(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
    return ""


def _api_endpoint_problem(url: str | None) -> str:
    if not url:
        return "모델 API URL이 비어 있습니다. code_assist_v1/api_config.json의 models.*.url을 설정하세요."
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not parsed.netloc:
        return f"모델 API URL 형식이 올바르지 않습니다: {url}"
    if "redacted" in host or host.endswith(".internal") and host.startswith("redacted"):
        return (
            "모델 API endpoint가 placeholder로 설정되어 있습니다. "
            "code_assist_v1/api_config.json의 models.*.url을 실제 접근 가능한 "
            "OpenAI 호환 /v1/chat/completions endpoint로 바꾸세요."
        )
    return ""


def _merged_model_config(registry_id: str) -> dict:
    cfg = dict(MODEL_REGISTRY.get(registry_id, {}) or {})
    env_id = cfg.get("env_id") or registry_id
    env_cfg = dict(ENV_CONFIG.get(env_id, {}) or {})
    merged = {**cfg, **env_cfg}
    merged.setdefault("env_id", env_id)
    merged.setdefault("registry_id", registry_id)
    return merged


def _model_kind(cfg: dict) -> str:
    env_id = str(cfg.get("env_id") or "")
    runtime = str(cfg.get("runtime") or "").lower()
    if runtime == "codex" or env_id == "codex-local":
        return "codex"
    return "gguf" if runtime == "gguf" or env_id.startswith("gguf-") else "api"


def _max_tokens_for_request(data: dict, cfg: dict, kind: str, query: str) -> int:
    max_tokens = int(data.get("max_tokens") or 0)
    if max_tokens > 0:
        return max_tokens
    tier = cfg.get("cost_tier", "medium")
    max_tokens = 16384 if tier == "high" else (8192 if tier == "medium" else 4096)
    if kind == "gguf":
        runtime = cfg.get("gguf") if isinstance(cfg.get("gguf"), dict) else {}
        try:
            cap = int(runtime.get("reply_cap") or cfg.get("reply_cap") or TOKEN_SETTINGS.get("gguf_reply_cap", 4096))
        except Exception:
            cap = 4096
        max_tokens = min(max_tokens, max(256, cap))
        if _is_lightweight_chat(query):
            max_tokens = min(max_tokens, 256)
    return max_tokens


def _gguf_context_budget(
    cfg: dict,
    final_messages: list[dict],
    context_window: int | None = None,
) -> tuple[int, int, int]:
    runtime = cfg.get("gguf") if isinstance(cfg.get("gguf"), dict) else {}
    try:
        reserve = int(runtime.get("ctx_reserve") or cfg.get("ctx_reserve") or TOKEN_SETTINGS.get("gguf_ctx_reserve", 1536))
    except Exception:
        reserve = 1536
    window = int(context_window or cfg.get("context_window") or cfg.get("model_context_window") or DEFAULT_N_CTX)
    input_tokens = estimate_messages_tokens(final_messages)
    return input_tokens, reserve, window - input_tokens - reserve


def _fit_max_tokens_to_context(
    max_tokens: int,
    cfg: dict,
    kind: str,
    final_messages: list[dict],
    query: str,
    context_window: int | None = None,
) -> int:
    if kind != "gguf":
        return max_tokens
    _input_tokens, _reserve, available = _gguf_context_budget(cfg, final_messages, context_window)
    if _is_lightweight_chat(query):
        max_tokens = min(max_tokens, 256)
    if available <= 0:
        return 0
    return max(1, min(max_tokens, available))


def _context_window_for_model(cfg: dict, kind: str) -> int:
    try:
        return int(cfg.get("context_window") or cfg.get("model_context_window") or DEFAULT_N_CTX)
    except Exception:
        return DEFAULT_N_CTX


def _auto_compact_limit_for_model(cfg: dict, context_window: int) -> int:
    raw = cfg.get("model_auto_compact_token_limit") or cfg.get("auto_compact_token_limit")
    try:
        limit = int(raw or 0)
        if limit > 0:
            return limit
    except Exception:
        pass
    return max(1, int(context_window * AUTO_COMPACT_RATIO))


def _compact_summary_from_request(data: dict) -> str:
    return str(data.get("compact_summary") or data.get("session_summary") or "").strip()


def _normalize_skill_ids(raw_ids, auth_user_id: str | None) -> list[str]:
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    out: list[str] = []
    for raw_sid in raw_ids:
        sid_text = str(raw_sid).strip()
        if not sid_text:
            continue
        sid = resolve_skill_alias(sid_text, user_id=auth_user_id) or sid_text
        if sid not in out:
            out.append(sid)
    return out


def _session_status_payload(data: dict, auth_user_id: str | None, compact_now: bool = False) -> dict:
    model_id = data.get("model") or data.get("env") or None
    if isinstance(model_id, list):
        model_id = model_id[0] if model_id else None
    cfg, kind = _resolve_model(model_id)
    if not cfg:
        raise ValueError(f"Unknown model: {model_id}")

    raw_messages = data.get("messages", []) or []
    if not isinstance(raw_messages, list):
        raw_messages = []
    query = _last_user_query(raw_messages)
    skill_ids = _normalize_skill_ids(data.get("skills", []) or [], auth_user_id)
    context_window = _context_window_for_model(cfg, kind)
    max_tokens = _max_tokens_for_request(data, cfg, kind, query)
    auto_compact_limit = _auto_compact_limit_for_model(cfg, context_window)
    compact_summary = _compact_summary_from_request(data)

    system_text = build_coding_system_prompt(
        skill_ids=skill_ids,
        user_extra=(data.get("system_prompt") or "").strip(),
        n_ctx=context_window,
        user_id=auth_user_id,
    )
    base_msgs: list[dict] = [{"role": "system", "content": system_text}]
    ws_block = build_workspace_block(data.get("workspace_files") or [])
    if ws_block:
        base_msgs.append(ws_block)
    try:
        notion_text, _notion_ids = ns.build_session_context(
            auth_user_id,
            str(data.get("session_id") or ""),
        )
        if notion_text:
            base_msgs.append({"role": "system", "content": notion_text})
    except Exception:
        pass

    def _assemble(summary: str) -> list[dict]:
        out = list(base_msgs)
        block = compact_system_block(summary)
        if block:
            out.append(block)
            out.extend(recent_messages(raw_messages, MAX_HISTORY_TURNS))
        else:
            out.extend(raw_messages)
        return out

    if compact_now and len(raw_messages) > MAX_HISTORY_TURNS:
        compact_summary, compacted_count = build_compact_summary(
            raw_messages,
            previous_summary=compact_summary,
            keep_recent=MAX_HISTORY_TURNS,
        )
    else:
        compacted_count = 0

    final_messages = _assemble(compact_summary)
    status = build_context_status(
        model_id=str(cfg.get("model") or cfg.get("name") or model_id or ""),
        model_kind=kind,
        context_window=context_window,
        reserved_output_tokens=max_tokens,
        final_messages=final_messages,
        compact_summary=compact_summary,
        auto_compact_limit=auto_compact_limit,
    )
    status["raw_message_count"] = len(raw_messages)
    status["conversation_tokens_est"] = estimate_messages_tokens(raw_messages)
    status["status_scope"] = "session messages + skills + workspace + attached Notion pages + compact summary"
    status["live_retrieval_note"] = "Second Brain, memory, project retrieval, and attached Notion pages can increase actual usage."
    return {
        "status": status,
        "compact_summary": compact_summary,
        "compacted_message_count": compacted_count,
        "recent_message_count": min(len(raw_messages), MAX_HISTORY_TURNS),
        "thresholds": {
            "warn_ratio": 0.65,
            "auto_compact_ratio": AUTO_COMPACT_RATIO,
            "force_compact_ratio": 0.85,
            "auto_compact_token_limit": auto_compact_limit,
        },
    }


def _model_enabled(cfg: dict | None) -> bool:
    if not isinstance(cfg, dict):
        return True
    return cfg.get("enabled") is not False and not cfg.get("disabled")


def _is_search_only_intent(query: str) -> bool:
    q = query.lower()
    search_terms = ["검색", "찾아", "목록", "리스트", "파일명", "문서 목록", "search", "find", "list"]
    content_terms = ["내용", "관련", "설명", "분석", "요약", "만들", "explain", "analyze", "summarize"]
    return any(term in q for term in search_terms) and not any(term in q for term in content_terms)


def _is_lightweight_chat(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    compact = "".join(q.split())
    greetings = {
        "hi",
        "hello",
        "hey",
        "ㅎㅇ",
        "하이",
        "안녕",
        "안녕하세요",
        "반가워",
        "테스트",
        "test",
    }
    if compact in greetings:
        return True
    if len(compact) <= 12 and any(token in compact for token in ("안녕", "ㅎㅇ", "하이")):
        return True
    return False


def _resolve_model(model_id: str | None) -> tuple[dict | None, str]:
    if not model_id:
        model_id = get_default_model()
    if not model_id:
        return None, ""

    if not STANDALONE:
        try:
            from demos_v1.models import (
                ENV_CONFIG as dv_env_config,
                ENV_TO_REGISTRY as dv_env_to_registry,
                MODEL_REGISTRY as dv_model_registry,
            )

            if model_id.startswith("gguf-"):
                cfg = dv_env_config.get(model_id)
                if cfg:
                    return cfg, "gguf"
            if model_id in dv_model_registry:
                return dv_model_registry[model_id], "api"
            reg_key = dv_env_to_registry.get(model_id)
            if reg_key and reg_key in dv_model_registry:
                return dv_model_registry[reg_key], "api"
        except ImportError:
            pass

    if model_id in MODEL_REGISTRY:
        cfg = _merged_model_config(model_id)
        if not _model_enabled(cfg):
            return None, ""
        return cfg, _model_kind(cfg)
    if model_id in ENV_CONFIG:
        cfg = dict(ENV_CONFIG.get(model_id) or {})
        registry_id = cfg.get("_registry_id")
        if not _model_enabled(cfg) or (registry_id and not _model_enabled(MODEL_REGISTRY.get(registry_id))):
            return None, ""
        return cfg, _model_kind(cfg)
    reg_key = ENV_TO_REGISTRY.get(model_id)
    if reg_key and reg_key in MODEL_REGISTRY:
        cfg = _merged_model_config(reg_key)
        if not _model_enabled(cfg):
            return None, ""
        return cfg, _model_kind(cfg)
    return None, ""


def _codex_fallback_enabled() -> bool:
    return codex_fallback_enabled()


def _ordered_attempt_models(
    requested_model_id: str | None,
    *,
    include_fallback: bool,
) -> list[tuple[str, dict, str]]:
    requested = str(requested_model_id or "").strip()
    candidate_ids = ordered_model_candidates(
        requested_model_id=requested or None,
        priority_ids=list(DEFAULT_MODEL_PRIORITY or []),
        registry=MODEL_REGISTRY,
        environment=ENV_CONFIG,
        codex_enabled=_codex_fallback_enabled(),
        include_fallback=include_fallback,
    )
    attempts: list[tuple[str, dict, str]] = []
    if requested:
        requested_cfg, requested_kind = _resolve_model(requested)
        if requested_cfg:
            attempts.append((requested, requested_cfg, requested_kind))
            if not include_fallback:
                return attempts
    for candidate_id in candidate_ids:
        if attempts and candidate_id == attempts[0][0]:
            continue
        cfg, kind = _resolve_model(candidate_id)
        if cfg:
            attempts.append((candidate_id, cfg, kind))
        if not include_fallback and attempts:
            break
    return attempts


def _gguf_runtime_cfg(cfg: dict | None) -> dict:
    if not isinstance(cfg, dict):
        return {}
    gguf = cfg.get("gguf")
    return gguf if isinstance(gguf, dict) else {}


def _cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _messages_have_images(messages: list[dict]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type in {"image", "image_url", "input_image"}:
                return True
            if part.get("image_url") or part.get("input_image"):
                return True
    return False


def _gguf_load_config_for_request(cfg: dict, messages: list[dict]) -> dict:
    runtime = _gguf_runtime_cfg(cfg)
    mode = str(runtime.get("mmproj_mode") or "always").strip().lower()
    if mode in {"off", "false", "0", "text", "text-only"}:
        use_mmproj = False
    elif mode == "auto":
        use_mmproj = _messages_have_images(messages)
    else:
        use_mmproj = True

    if use_mmproj:
        return cfg

    copied = dict(cfg)
    copied.pop("_mmproj_path", None)
    gguf = dict(runtime)
    gguf.pop("mmproj_patterns", None)
    gguf.pop("mmproj_pattern", None)
    copied["gguf"] = gguf
    return copied


def _append_no_think_token(messages: list[dict], token: str) -> None:
    if not token:
        return
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            if token not in content:
                msg["content"] = f"{content.rstrip()}\n\n{token}".strip()
            return
        if isinstance(content, list):
            for part in reversed(content):
                if isinstance(part, dict) and part.get("type") == "text":
                    text = str(part.get("text") or "")
                    if token not in text:
                        part["text"] = f"{text.rstrip()}\n\n{token}".strip()
                    return
            content.append({"type": "text", "text": token})
            return
        msg["content"] = f"{content}\n\n{token}".strip()
        return


def _prepare_gguf_messages(messages: list[dict], cfg: dict) -> list[dict]:
    runtime = _gguf_runtime_cfg(cfg)
    out: list[dict] = []
    for msg in messages:
        copied = dict(msg)
        content = copied.get("content")
        if isinstance(content, list):
            copied["content"] = [dict(part) if isinstance(part, dict) else part for part in content]
        out.append(copied)

    if _cfg_bool(runtime, "disable_thinking", True):
        instruction = (
            "For this local GGUF response, do not output chain-of-thought, hidden reasoning, "
            "scratchpad text, analysis sections, reasoning sections, thinking sections, or <think> blocks. "
            "Start directly with the final answer and do not use labels like Analysis:, Reasoning:, or Thinking:."
        )
        if out and out[0].get("role") == "system":
            out[0]["content"] = f"{out[0].get('content', '')}\n\n{instruction}"
        else:
            out.insert(0, {"role": "system", "content": instruction})
        _append_no_think_token(out, str(runtime.get("no_think_token") or "").strip())
    return out


class _ThinkingStripper:
    def __init__(self, enabled: bool, strip_reasoning_prefix: bool = True):
        self.enabled = enabled
        self.strip_reasoning_prefix = strip_reasoning_prefix
        self.in_think = False
        self.block_end_tag = "</think>"
        self.in_reasoning_prefix = False
        self.prefix_checked = False
        self.buffer = ""
        self.block_tags = ("think", "analysis", "reasoning", "scratchpad", "thought")
        self.reasoning_markers = (
            "analysis:",
            "reasoning:",
            "thinking:",
            "thought:",
            "scratchpad:",
            "chain-of-thought:",
            "사고:",
            "추론:",
            "분석:",
        )
        self.final_markers = (
            "\nfinal:",
            "\nfinal answer:",
            "\nanswer:",
            "\n최종:",
            "\n최종 답변:",
            "\n답변:",
        )

    def feed(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        self.buffer += text
        out: list[str] = []
        while self.buffer:
            lower = self.buffer.lower()
            if self.strip_reasoning_prefix and self.in_reasoning_prefix:
                final_at = self._find_final_marker(lower)
                if final_at < 0:
                    self.buffer = self.buffer[-64:]
                    return "".join(out)
                marker_end = lower.find(":", final_at)
                self.buffer = self.buffer[marker_end + 1:] if marker_end >= 0 else self.buffer[final_at:]
                self.in_reasoning_prefix = False
                self.prefix_checked = True
                continue

            if self.strip_reasoning_prefix and not self.prefix_checked:
                stripped = lower.lstrip()
                leading = len(self.buffer) - len(self.buffer.lstrip())
                if any(stripped.startswith(marker) for marker in self.reasoning_markers):
                    self.buffer = self.buffer[leading:]
                    self.in_reasoning_prefix = True
                    continue
                if len(self.buffer) > 96 or "\n" in self.buffer:
                    self.prefix_checked = True

            if self.in_think:
                end = lower.find(self.block_end_tag)
                if end < 0:
                    self.buffer = self.buffer[-16:]
                    return "".join(out)
                self.buffer = self.buffer[end + len(self.block_end_tag):]
                self.in_think = False
                continue

            found = [
                (lower.find(f"<{tag}"), tag)
                for tag in self.block_tags
                if lower.find(f"<{tag}") >= 0
            ]
            start, tag = min(found, key=lambda x: x[0]) if found else (-1, "")
            if start < 0:
                keep = 4
                if len(self.buffer) <= keep:
                    return "".join(out)
                out.append(self.buffer[:-keep])
                self.buffer = self.buffer[-keep:]
                return "".join(out)

            close = lower.find(">", start)
            if close < 0:
                out.append(self.buffer[:start])
                self.buffer = self.buffer[start:]
                return "".join(out)
            out.append(self.buffer[:start])
            self.buffer = self.buffer[close + 1:]
            self.in_think = True
            self.block_end_tag = f"</{tag}>" if tag else "</think>"
        return "".join(out)

    def flush(self) -> str:
        if not self.enabled:
            out = self.buffer
            self.buffer = ""
            return out
        if self.in_think:
            self.buffer = ""
            self.in_think = False
            return ""
        out = self.buffer
        self.buffer = ""
        return out

    def _find_final_marker(self, lower: str) -> int:
        positions = [lower.find(marker) for marker in self.final_markers]
        positions = [pos for pos in positions if pos >= 0]
        return min(positions) if positions else -1


def _stream_api(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout_s: int,
):
    url = cfg.get("url")
    model = cfg.get("model")
    problem = _api_endpoint_problem(url)
    if problem:
        yield _sse({"error": problem, "status": 503})
        return
    headers = {"Content-Type": "application/json"}
    api_token, token_source = resolve_api_token(cfg)
    explicit_auth = any(
        key in cfg
        for key in (
            "api_key_env",
            "token_env",
            "auth_env",
            "api_key_file",
            "token_file",
            "auth_file",
            "api_key_source",
            "token_source",
            "auth_source",
        )
    )
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    elif explicit_auth:
        yield _sse({
            "error": f"Configured API token source is unavailable: {_safe_error(token_source)}",
            "status": 401,
        })
        return
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "tool_choice": "none",
    }
    try:
        tls_verify: bool | str = cfg.get("tls_verify", True) is not False
        ca_bundle = str(cfg.get("tls_ca_bundle") or "").strip()
        if ca_bundle:
            tls_verify = ca_bundle
        upstream = req.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=timeout_s,
            verify=tls_verify,
        )
    except req.exceptions.Timeout:
        yield _sse({"error": f"모델 API 요청이 {timeout_s}초 안에 응답하지 않았습니다. endpoint와 네트워크를 확인하세요.", "status": 504})
        return
    except req.exceptions.ConnectionError as e:
        host = urlparse(str(url)).netloc or str(url)
        yield _sse({"error": f"Model API connection failed: {_safe_error(host)}. Check endpoint DNS/network/api_config.json. ({_safe_error(e)})", "status": 502})
        return
    if upstream.status_code >= 400:
        try:
            body_preview = upstream.text[:500]
        except Exception:
            body_preview = ""
        yield _sse({"error": f"HTTP {upstream.status_code}: {_safe_error(body_preview)}", "status": upstream.status_code})
        return

    saw_done = False
    emitted_content = False
    try:
        for raw_line in upstream.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            chunk = raw_line[5:].strip()
            if chunk == "[DONE]":
                saw_done = True
                break
            if not chunk:
                continue
            try:
                event = json.loads(chunk)
            except Exception:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text_piece = delta.get("content") or ""
            if text_piece:
                emitted_content = True
                yield _sse({"delta": text_piece})
    finally:
        upstream.close()
    if not saw_done:
        yield _sse({"error": "Provider stream ended without [DONE].", "status": 502})
    elif not emitted_content:
        yield _sse({"error": "Provider stream returned no content.", "status": 502})


def _stream_gguf(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    stream_meta: dict | None = None,
    query: str = "",
):
    try:
        assert_local_inference_allowed()
    except LocalInferenceOwnershipError:
        yield _sse(
            {
                "error": "Local GGUF inference is unavailable in this web process.",
                "status": 503,
            }
        )
        return
    model_key = str(cfg.get("env_id") or cfg.get("model") or cfg.get("name") or "gguf")
    with gguf_inference_slot(model_key):
        yield from _stream_gguf_locked(cfg, messages, temperature, max_tokens, stream_meta=stream_meta, query=query)


def _stream_gguf_locked(
    cfg: dict,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    stream_meta: dict | None = None,
    query: str = "",
):
    stream_meta = stream_meta if isinstance(stream_meta, dict) else {}
    yield _sse({"event": "status", "stage": "loading_gguf", "message": "GGUF 모델 확인 중"})
    if STANDALONE:
        import code_assist_v1.utils as state
        from code_assist_v1.gguf_loader import is_gguf_model_ready, load_gguf_model, loaded_gguf_n_ctx

        gguf_path = cfg.get("_gguf_path")
        if not gguf_path:
            name = cfg.get("name") or cfg.get("model") or cfg.get("env_id") or "selected model"
            yield _sse({"error": f"GGUF file not found for {name}. Put the file in code_assist_v1/MODEL_GGUF or set the configured GGUF path env var.", "status": 503})
            return
        load_cfg = _gguf_load_config_for_request(cfg, messages)
        if not is_gguf_model_ready(gguf_path, model_config=load_cfg) and not load_gguf_model(gguf_path, model_config=load_cfg):
            yield _sse({"error": f"GGUF model load failed: {gguf_path}", "status": 500})
            return
        effective_n_ctx = loaded_gguf_n_ctx()
    else:
        import demos_v1.utils as state
        effective_n_ctx = int(cfg.get("context_window") or DEFAULT_N_CTX)

        gguf_path = cfg.get("_gguf_path")
        if gguf_path:
            try:
                from demos_v1.gguf import load_gguf_model

                if not load_gguf_model(gguf_path):
                    yield _sse({"error": f"GGUF model load failed: {gguf_path}", "status": 500})
                    return
            except Exception as e:
                yield _sse({"error": f"GGUF model load failed: {_safe_error(e)}", "status": 500})
                return

    if state.gguf_model is None:
        yield _sse({"error": "GGUF model is not loaded", "status": 503})
        return

    messages = _prepare_gguf_messages(messages, cfg)
    if effective_n_ctx:
        input_tokens, reserve_tokens, available_tokens = _gguf_context_budget(cfg, messages, effective_n_ctx)
        stream_meta["gguf_input_tokens_est"] = input_tokens
        stream_meta["gguf_ctx_reserve"] = reserve_tokens
        if available_tokens <= 0:
            stream_meta["context_overflow"] = True
            yield _sse({
                "error": (
                    f"현재 입력({input_tokens:,} tokens)이 로드된 GGUF context({int(effective_n_ctx):,})를 초과합니다. "
                    "/compact 후 다시 시도하거나 질문, 첨부 파일, 고정 지식을 줄여주세요."
                ),
                "status": 413,
            })
            return
        max_tokens = _fit_max_tokens_to_context(max_tokens, cfg, "gguf", messages, query, effective_n_ctx)
        stream_meta["gguf_loaded_n_ctx"] = effective_n_ctx
        stream_meta["gguf_actual_max_tokens"] = max_tokens
        if effective_n_ctx < int(cfg.get("context_window") or effective_n_ctx):
            stream_meta["gguf_n_ctx_fallback"] = True
    yield _sse({
        "event": "status",
        "stage": "reasoning",
        "message": "내부 추론 및 답변 준비 중",
        "loaded_n_ctx": effective_n_ctx,
        "max_tokens": max_tokens,
    })
    runtime_cfg = _gguf_runtime_cfg(cfg)
    stripper = _ThinkingStripper(
        _cfg_bool(runtime_cfg, "strip_thinking", True),
        strip_reasoning_prefix=_cfg_bool(runtime_cfg, "strip_reasoning_prefix", True),
    )
    stop = runtime_cfg.get("stop") if isinstance(runtime_cfg.get("stop"), list) else None

    try:
        chat_kwargs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if stop:
            chat_kwargs["stop"] = [str(x) for x in stop if str(x)]
        finish_reason = ""
        completion_tokens = 0
        for chunk in state.gguf_model.create_chat_completion(**chat_kwargs):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            content = delta.get("content") or ""
            if content:
                content = stripper.feed(content)
                if content:
                    completion_tokens += estimate_messages_tokens([{"role": "assistant", "content": content}])
                    yield _sse({"delta": content})
        tail = stripper.flush()
        if tail:
            completion_tokens += estimate_messages_tokens([{"role": "assistant", "content": tail}])
            yield _sse({"delta": tail})
        stream_meta["finish_reason"] = finish_reason or ""
        stream_meta["completion_tokens_est"] = completion_tokens
        if finish_reason == "length":
            stream_meta["output_truncated"] = True
    except Exception as e:
        yield _sse({"error": f"GGUF stream error: {_safe_error(e)}", "status": 500})


def _generate_provider_route(
    *,
    attempt_models: list[tuple[str, dict, str]],
    data: dict,
    final_messages: list[dict],
    temperature: float,
    query: str,
    timeout_s: int,
    base_meta: dict,
):
    route_started = time.time()
    attempt_ledger: list[dict] = []
    routing_policy_sha256 = hashlib.sha256(
        json.dumps(
            [
                {
                    "model_id": model_id,
                    "model_kind": kind,
                    "server_model": str(config.get("model") or ""),
                }
                for model_id, config, kind in attempt_models
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    yield _sse(
        {
            "event": "status",
            "stage": "preparing",
            "message": "Provider route prepared",
        }
    )
    for ordinal, (candidate_id, candidate_cfg, candidate_kind) in enumerate(
        attempt_models,
        start=1,
    ):
        started_at = _utc_now()
        attempt_started = time.time()
        emitted_content = False
        failure_event: dict | None = None
        stream_meta: dict = {}
        yield _sse(
            {
                "event": "status",
                "stage": "provider_attempt",
                "attempt": ordinal,
                "model_id": candidate_id,
                "model_kind": candidate_kind,
            }
        )
        candidate_cap = _max_tokens_for_request(
            data,
            candidate_cfg,
            candidate_kind,
            query,
        )
        candidate_max_tokens = _fit_max_tokens_to_context(
            candidate_cap,
            candidate_cfg,
            candidate_kind,
            final_messages,
            query,
            _context_window_for_model(candidate_cfg, candidate_kind),
        )
        try:
            if candidate_kind == "gguf":
                serialized_events = _stream_gguf(
                    candidate_cfg,
                    final_messages,
                    temperature,
                    candidate_max_tokens,
                    stream_meta=stream_meta,
                    query=query,
                )
            elif candidate_kind == "codex":
                serialized_events = (
                    _sse(event)
                    for event in stream_codex_exec(
                        final_messages,
                        candidate_cfg,
                        timeout_s=timeout_s,
                    )
                )
            else:
                serialized_events = _stream_api(
                    candidate_cfg,
                    final_messages,
                    temperature,
                    candidate_max_tokens,
                    timeout_s,
                )

            for serialized in serialized_events:
                event = _sse_object(serialized)
                if event.get("error"):
                    failure_event = event
                    break
                if event.get("delta"):
                    emitted_content = True
                yield serialized
        except Exception:
            failure_event = {"status": 500}

        elapsed_ms = int((time.time() - attempt_started) * 1000)
        if emitted_content and failure_event is None:
            attempt_ledger.append(
                {
                    "attempt": ordinal,
                    "model_id": candidate_id,
                    "model_kind": candidate_kind,
                    "started_at_utc": started_at,
                    "finished_at_utc": _utc_now(),
                    "elapsed_ms": elapsed_ms,
                    "outcome": "success",
                    "error_code": None,
                    "emitted_content": True,
                }
            )
            selected_meta = {
                **base_meta,
                "model_used": candidate_cfg.get("model")
                or candidate_cfg.get("name")
                or candidate_id,
                "model_kind": candidate_kind,
                "model_id": candidate_id,
                "provider_attempts": attempt_ledger,
                "routing_policy_sha256": routing_policy_sha256,
            }
            yield _sse(
                {
                    "done": True,
                    "elapsed_ms": int((time.time() - route_started) * 1000),
                    **selected_meta,
                    **stream_meta,
                }
            )
            return

        error_code = _stable_attempt_error(failure_event)
        attempt_ledger.append(
            {
                "attempt": ordinal,
                "model_id": candidate_id,
                "model_kind": candidate_kind,
                "started_at_utc": started_at,
                "finished_at_utc": _utc_now(),
                "elapsed_ms": elapsed_ms,
                "outcome": "failed",
                "error_code": error_code,
                "emitted_content": emitted_content,
            }
        )
        if emitted_content:
            # Never concatenate a second provider's answer after content has
            # reached the client.  The missing completion marker is deliberate.
            yield _sse(
                {
                    "error": "Provider stream failed after partial output.",
                    "status": 502,
                    "provider_attempts": attempt_ledger,
                    "routing_policy_sha256": routing_policy_sha256,
                }
            )
            return
        if ordinal < len(attempt_models):
            yield _sse(
                {
                    "event": "status",
                    "stage": "provider_fallback",
                    "failed_attempt": ordinal,
                    "error_code": error_code,
                }
            )

    yield _sse(
        {
            "error": "All configured model attempts failed.",
            "status": 503,
            "provider_attempts": attempt_ledger,
            "routing_policy_sha256": routing_policy_sha256,
        }
    )


def register_chat_stream_routes(app):
    @app.route("/api/code/session/status", methods=["POST"])
    @app.route("/api/code/chat/status", methods=["POST"])
    def api_code_session_status():
        auth_user_id = current_user_id()
        data = request.get_json(force=True, silent=True) or {}
        try:
            return _session_status_payload(data, auth_user_id, compact_now=False)
        except Exception as e:
            return {"error": _safe_error(e)}, 400

    @app.route("/api/code/session/compact", methods=["POST"])
    @app.route("/api/code/chat/compact", methods=["POST"])
    def api_code_session_compact():
        auth_user_id = current_user_id()
        data = request.get_json(force=True, silent=True) or {}
        try:
            result = _session_status_payload(data, auth_user_id, compact_now=True)
            result["status_text"] = "compacted"
            return result
        except Exception as e:
            return {"error": _safe_error(e)}, 400

    @app.route("/api/code/chat/stream", methods=["POST"])
    def api_code_chat_stream():
        auth_user_id = current_user_id()
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception as e:
            return Response(
                _sse({"error": f"Invalid request: {e}", "status": 400}),
                mimetype="text/event-stream",
                status=400,
            )

        model_id = data.get("model") or data.get("env") or None
        if isinstance(model_id, list):
            model_id = model_id[0] if model_id else None
        fallback_enabled = not bool(data.get("disable_fallback", True))
        attempt_models = _ordered_attempt_models(
            model_id,
            include_fallback=fallback_enabled,
        )
        if not attempt_models:
            return Response(
                _sse({"error": f"Unknown model: {model_id}", "status": 400}),
                mimetype="text/event-stream",
                status=400,
            )
        model_id, cfg, kind = attempt_models[0]

        raw_messages = data.get("messages", []) or []
        if not isinstance(raw_messages, list):
            raw_messages = []
        compact_summary = _compact_summary_from_request(data)
        normalized_skill_ids = _normalize_skill_ids(data.get("skills", []) or [], auth_user_id)
        query_for_routing = _last_user_query(raw_messages)
        auto_skill_matches: list[dict] = []
        if bool(data.get("auto_skills", False)):
            auto_skill_matches = auto_select_skills(
                query=query_for_routing,
                explicit_skill_ids=normalized_skill_ids,
                user_id=auth_user_id,
                max_results=int(data.get("auto_skill_max", 3) or 3),
                min_score=float(data.get("auto_skill_min_score", 3.0) or 3.0),
            )
            skill_ids = [item["id"] for item in auto_skill_matches]
        else:
            skill_ids = normalized_skill_ids
        user_extra = (data.get("system_prompt") or "").strip()
        effort = int(data.get("effort", 2))
        temperature = [0.2, 0.4, 0.7, 0.9][min(max(effort, 0), 3)]
        max_token_cap = _max_tokens_for_request(data, cfg, kind, query_for_routing)
        max_tokens = max_token_cap
        context_window = _context_window_for_model(cfg, kind)
        auto_compact_limit = _auto_compact_limit_for_model(cfg, context_window)

        try:
            system_text = build_coding_system_prompt(
                skill_ids=skill_ids,
                user_extra=user_extra,
                n_ctx=context_window,
                user_id=auth_user_id,
            )
        except Exception as e:
            return Response(
                _sse({"error": f"System prompt build failed: {e}", "status": 500}),
                mimetype="text/event-stream",
                status=500,
            )

        if bool(data.get("think_mode", False)):
            system_text += "\n\nThink carefully before answering. Keep the final answer clear and concise."

        if bool(data.get("enable_knowledge", False)):
            system_text += (
                "\n\n[Runtime Mode]\n"
                "- Second Brain mode: ON.\n"
                "- Use retrieved LLM Wiki markdown, index.md, log.md, and graph relations as high-priority evidence when they are provided.\n"
                "- Second Brain no longer uses raw chunks or atomic thought nodes as canonical knowledge.\n"
                "- Project Context, when provided, is project-scoped evidence and must not be treated as canonical long-term knowledge.\n"
                "- If retrieved context is insufficient, say what is missing instead of inventing facts.\n"
            )
        else:
            system_text += (
                "\n\n[Runtime Mode]\n"
                "- Coding Agent mode: ON. Second Brain search is OFF for this request.\n"
                "- Work from the user's request, active skills, Project Context, workspace files, and visible code context.\n"
                "- Do not claim that Second Brain knowledge was used unless pinned nodes or explicit source/spec content are provided.\n"
            )

        lightweight_chat = _is_lightweight_chat(query_for_routing)
        enable_memory = bool(data.get("enable_memory", True)) and not lightweight_chat
        memory_block = None
        memory_ids: list[str] = []
        memory_written: dict | None = None
        if enable_memory and query_for_routing.strip():
            try:
                memory_text, memory_ids = ms.build_memory_context(
                    query_for_routing,
                    user_id=auth_user_id,
                    max_results=int(data.get("memory_max_results", 5) or 5),
                )
                if memory_text:
                    memory_block = {"role": "system", "content": memory_text}
                written = ms.propose_from_user_message(
                    user_id=auth_user_id,
                    message=query_for_routing,
                    session_id=str(data.get("session_id") or ""),
                )
                if written:
                    memory_written = {
                        "id": written.get("id"),
                        "status": written.get("status"),
                        "memory_type": written.get("memory_type"),
                    }
            except Exception as e:
                memory_block = {
                    "role": "system",
                    "content": f"=== User / Project Memory ===\n- Memory layer unavailable for this turn: {e}",
                }

        kb_block = None
        kb_files: list[str] = []
        sb_block = None
        sb_ids: list[str] = []
        sb_meta: dict = {}
        project_context_block = None
        project_context_ids: list[str] = []
        project_id = str(data.get("project_id") or "default").strip() or "default"
        if bool(data.get("enable_knowledge", False)) and not lightweight_chat:
            query = query_for_routing
            if query.strip():
                try:
                    user_id = auth_user_id
                    results = ks.search(
                        query=query,
                        user_id=user_id,
                        max_results=int(data.get("kb_max_results", 8)),
                        max_content_chars=4000,
                    )
                    kb_files = [r["filename"] for r in results]
                    kb_block = build_knowledge_block(
                        query=query,
                        knowledge_results=results,
                        intent_search_only=_is_search_only_intent(query),
                    )
                    sb_text, sb_ids, sb_meta = sb.build_search_context_with_metadata(
                        query=query,
                        user_id=user_id,
                        max_results=int(data.get("sb_max_results", 5)),
                        hop_depth=int(data.get("sb_hop_depth", 1)),
                    )
                    if sb_text:
                        sb_block = {"role": "system", "content": sb_text}
                except Exception as e:
                    return Response(
                        _sse({"error": f"Second Brain context build failed: {e}", "status": 500}),
                        mimetype="text/event-stream",
                        status=500,
                    )

        if bool(data.get("enable_project_context", True)) and not lightweight_chat:
            query = query_for_routing
            if query.strip():
                try:
                    project_context_text, project_context_ids = pcs.build_project_context(
                        query=query,
                        user_id=auth_user_id,
                        project_id=project_id,
                        max_results=int(data.get("project_context_max_results", 5) or 5),
                    )
                    if project_context_text:
                        project_context_block = {"role": "system", "content": project_context_text}
                except Exception as e:
                    project_context_block = {
                        "role": "system",
                        "content": f"=== Project Context Store ===\n- Project context unavailable for this turn: {e}",
                    }

        pinned_block = None
        pinned_ids: list[str] = []
        pinned_input = data.get("pinned_thoughts") or []
        if isinstance(pinned_input, list):
            user_id = auth_user_id
            pinned_text, pinned_ids = sb.build_pinned_context(
                [str(x) for x in pinned_input if isinstance(x, (str, int, float))],
                user_id=user_id,
            )
            if pinned_text:
                pinned_block = {"role": "system", "content": pinned_text}

        ws_block = None
        ws_files = data.get("workspace_files") or []
        if ws_files:
            ws_block = build_workspace_block(ws_files)

        notion_block = None
        notion_ids: list[str] = []
        session_id = str(data.get("session_id") or "")
        if session_id:
            try:
                notion_text, notion_ids = ns.build_session_context(auth_user_id, session_id)
                if notion_text:
                    notion_block = {"role": "system", "content": notion_text}
            except Exception as e:
                notion_block = {
                    "role": "system",
                    "content": f"=== Attached Notion Workspace Pages ===\n- Notion context unavailable for this turn: {e}",
                }

        harness_plan = ah.analyze_request(
            query_for_routing,
            enable_knowledge=bool(data.get("enable_knowledge", False)),
            enable_memory=enable_memory,
            skill_ids=skill_ids,
            auto_skills=bool(data.get("auto_skills", False)),
            workspace_files=ws_files,
            pinned_ids=[str(x) for x in (data.get("pinned_thoughts") or [])],
            memory_ids=memory_ids,
        )
        harness_block = {"role": "system", "content": ah.build_harness_context(harness_plan)}

        base_msgs: list[dict] = [{"role": "system", "content": system_text}, harness_block]
        for block in (kb_block, sb_block, project_context_block, pinned_block, notion_block, memory_block, ws_block):
            if block:
                base_msgs.append(block)

        def _assemble_final_messages(summary: str, conversation_messages: list[dict]) -> list[dict]:
            out = list(base_msgs)
            summary_block = compact_system_block(summary)
            if summary_block:
                out.append(summary_block)
            for m in conversation_messages:
                if m.get("role") == "system":
                    out.append({"role": "user", "content": "[Additional instruction]\n" + str(m.get("content", ""))})
                else:
                    out.append(m)
            return out

        conversation_messages = recent_messages(raw_messages, MAX_HISTORY_TURNS) if compact_summary else list(raw_messages)
        final_msgs = _assemble_final_messages(compact_summary, conversation_messages)
        max_tokens = _fit_max_tokens_to_context(max_token_cap, cfg, kind, final_msgs, query_for_routing, context_window)
        context_status = build_context_status(
            model_id=str(cfg.get("model") or cfg.get("name") or model_id or ""),
            model_kind=kind,
            context_window=context_window,
            reserved_output_tokens=max_tokens,
            final_messages=final_msgs,
            compact_summary=compact_summary,
            auto_compact_limit=auto_compact_limit,
        )
        auto_compacted = False
        compacted_message_count = 0
        if should_auto_compact(context_status) and len(raw_messages) > MAX_HISTORY_TURNS:
            compact_summary, compacted_message_count = build_compact_summary(
                raw_messages,
                previous_summary=compact_summary,
                keep_recent=MAX_HISTORY_TURNS,
            )
            conversation_messages = recent_messages(raw_messages, MAX_HISTORY_TURNS)
            final_msgs = _assemble_final_messages(compact_summary, conversation_messages)
            max_tokens = _fit_max_tokens_to_context(max_token_cap, cfg, kind, final_msgs, query_for_routing, context_window)
            context_status = build_context_status(
                model_id=str(cfg.get("model") or cfg.get("name") or model_id or ""),
                model_kind=kind,
                context_window=context_window,
                reserved_output_tokens=max_tokens,
                final_messages=final_msgs,
                compact_summary=compact_summary,
                auto_compact_limit=auto_compact_limit,
            )
            auto_compacted = True

        timeout_s = 120 if fallback_enabled else 600
        meta = {
            "model_used": cfg.get("model") or cfg.get("name"),
            "model_kind": kind,
            "session_id": str(data.get("session_id") or ""),
            "loaded_skills": skill_ids,
            "auto_skill_matches": auto_skill_matches,
            "knowledge_files": kb_files,
            "second_brain_wiki_pages": sb_ids,
            "second_brain_thoughts": sb_ids,  # compatibility key; now contains wiki page/source ids
            "second_brain_retrieval": sb_meta,
            "project_context_ids": project_context_ids,
            "project_id": project_id,
            "pinned_wiki_pages": pinned_ids,
            "pinned_thoughts": pinned_ids,  # compatibility key
            "notion_pages": notion_ids,
            "memory_ids": memory_ids,
            "memory_written": memory_written,
            "harness_modes": harness_plan.get("modes") or [],
            "harness_tools": harness_plan.get("tools") or [],
            "workspace_files": [f.get("filename") for f in ws_files] if ws_files else [],
            "context_status": context_status,
            "requested_max_tokens": max_token_cap,
            "max_tokens": max_tokens,
            "context_compacted": bool(compact_summary),
            "auto_compacted": auto_compacted,
            "compacted_message_count": compacted_message_count,
            "compact_summary": compact_summary if auto_compacted else "",
            "system_prompt_length": len(system_text)
            + len(harness_block["content"])
            + (len(kb_block["content"]) if kb_block else 0)
            + (len(sb_block["content"]) if sb_block else 0)
            + (len(project_context_block["content"]) if project_context_block else 0)
            + (len(pinned_block["content"]) if pinned_block else 0)
            + (len(notion_block["content"]) if notion_block else 0)
            + (len(memory_block["content"]) if memory_block else 0)
            + (len(ws_block["content"]) if ws_block else 0),
            "compact_summary_length": len(compact_summary or ""),
        }

        def generate():
            yield from _generate_provider_route(
                attempt_models=attempt_models,
                data=data,
                final_messages=final_msgs,
                temperature=temperature,
                query=query_for_routing,
                timeout_s=timeout_s,
                base_meta=meta,
            )
            return

            t0 = time.time()
            stream_meta: dict = {}
            try:
                yield _sse({"event": "status", "stage": "preparing", "message": "컨텍스트 준비 완료"})
                if kind == "gguf":
                    yield from _stream_gguf(cfg, final_msgs, temperature, max_tokens, stream_meta=stream_meta, query=query_for_routing)
                elif kind == "codex":
                    yield _sse({"event": "status", "stage": "agent_running", "message": "Codex agent 실행 중"})
                    for event in stream_codex_exec(final_msgs, cfg, timeout_s=timeout_s):
                        yield _sse(event)
                else:
                    yield _sse({"event": "status", "stage": "api_waiting", "message": "API 모델 응답 대기 중"})
                    yield from _stream_api(cfg, final_msgs, temperature, max_tokens, timeout_s)
                yield _sse({"done": True, "elapsed_ms": int((time.time() - t0) * 1000), **meta, **stream_meta})
            except req.exceptions.Timeout:
                yield _sse({"error": f"Timeout ({timeout_s}s)", "status": 504})
            except req.exceptions.ConnectionError as e:
                yield _sse({"error": f"Connection failed: {_safe_error(e)}", "status": 502})
            except Exception as e:
                yield _sse({"error": f"Stream error: {_safe_error(e)}", "status": 500})

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
