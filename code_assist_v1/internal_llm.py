"""Shared internal LLM runner for Second Brain and Notion background tasks."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests as req

from code_assist_v1.config import resolve_api_token
from code_assist_v1.llm_wire import chat_payload, encode_chat_payload, request_body_receipt
from code_assist_v1.model_invocation import InvocationGuard
from code_assist_v1.model_routing import (
    codex_fallback_enabled,
    ordered_model_candidates,
)
from code_assist_v1.models import DEFAULT_MODEL_PRIORITY, ENV_CONFIG, MODEL_REGISTRY


def codex_internal_enabled() -> bool:
    return codex_fallback_enabled()


def default_provider_order() -> list[str]:
    """Return the launch-policy provider lanes in exact fallback order."""

    providers = ["hynix_api", "gguf"]
    if codex_internal_enabled():
        providers.append("codex")
    return providers


def extract_json(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        return {}
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", value, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _model_enabled(cfg: dict[str, Any]) -> bool:
    return not (cfg.get("enabled") is False or cfg.get("disabled") is True)


def _model_kind(cfg: dict[str, Any]) -> str:
    runtime = str(cfg.get("runtime") or "").lower()
    env_id = str(cfg.get("env_id") or "")
    if runtime == "codex" or env_id == "codex-local":
        return "codex"
    if runtime == "gguf" or env_id.startswith("gguf-"):
        return "gguf"
    return "api"


def _merged_config(model_id: str) -> dict[str, Any]:
    if model_id in MODEL_REGISTRY:
        cfg = dict(MODEL_REGISTRY.get(model_id) or {})
        env_id = cfg.get("env_id") or model_id
        env_cfg = dict(ENV_CONFIG.get(env_id) or {})
        merged = {**cfg, **env_cfg}
        merged.setdefault("env_id", env_id)
        merged.setdefault("_registry_id", model_id)
        return merged
    if model_id in ENV_CONFIG:
        cfg = dict(ENV_CONFIG.get(model_id) or {})
        cfg.setdefault("env_id", model_id)
        return cfg
    for key, cfg in MODEL_REGISTRY.items():
        if str((cfg or {}).get("env_id") or "") == model_id:
            return _merged_config(key)
    return {}


def _candidate_model_ids() -> list[str]:
    return ordered_model_candidates(
        requested_model_id=None,
        priority_ids=list(DEFAULT_MODEL_PRIORITY or []),
        registry=MODEL_REGISTRY,
        environment=ENV_CONFIG,
        codex_enabled=codex_internal_enabled(),
        include_fallback=True,
    )


def _candidate_configs(kind: str, model_ids: list[str] | tuple[str, ...] | None = None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    candidates = [str(item) for item in (model_ids or []) if str(item)] or _candidate_model_ids()
    for model_id in candidates:
        cfg = _merged_config(model_id)
        if not cfg or not _model_enabled(cfg):
            continue
        resolved_kind = _model_kind(cfg)
        if kind == "gguf" and resolved_kind == "gguf":
            out.append((model_id, cfg))
        elif kind == "hynix_api" and resolved_kind == "api":
            # ``hynix_api`` is the frozen legacy lane name, not a hostname
            # allowlist.  The reviewed external company registry may use a
            # different approved DNS name; runtime/API metadata already owns
            # model classification and the registry audit owns endpoint policy.
            out.append((model_id, cfg))
        elif kind == "codex" and resolved_kind == "codex" and codex_internal_enabled():
            out.append((model_id, cfg))
    return out


def _resolve_gguf_path(cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg.get("_gguf_path"):
        return cfg
    try:
        from code_assist_v1.gguf_loader import find_gguf_files, resolve_gguf_paths

        resolved = resolve_gguf_paths(cfg, gguf_files=find_gguf_files())
        if resolved.get("model_path"):
            copied = dict(cfg)
            copied["_gguf_path"] = resolved["model_path"]
            if resolved.get("mmproj_path"):
                copied["_mmproj_path"] = resolved["mmproj_path"]
            return copied
    except Exception:
        pass
    return cfg


def _text_only_gguf_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    gguf_cfg = dict(out.get("gguf") or {})
    gguf_cfg["mmproj_mode"] = "text-only"
    gguf_cfg.pop("mmproj_pattern", None)
    gguf_cfg.pop("mmproj_patterns", None)
    out["gguf"] = gguf_cfg
    out.pop("_mmproj_path", None)
    return out


def _call_gguf_text(
    cfg: dict[str, Any], messages: list[dict[str, Any]], max_tokens: int, temperature: float,
    *, attempt_guard: Callable[[], None] | None = None,
) -> str:
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    from code_assist_v1.concurrency import gguf_inference_slot
    from code_assist_v1.gguf_loader import is_gguf_model_ready, load_gguf_model
    from code_assist_v1.routes_chat_stream import _gguf_load_config_for_request, _prepare_gguf_messages
    import code_assist_v1.utils as state

    cfg = _text_only_gguf_config(_resolve_gguf_path(cfg))
    gguf_path = cfg.get("_gguf_path")
    if not gguf_path:
        raise RuntimeError("local GGUF file is not configured")
    load_cfg = _gguf_load_config_for_request(cfg, messages)
    model_key = str(cfg.get("env_id") or cfg.get("model") or "gguf-internal")
    with gguf_inference_slot(model_key):
        if not is_gguf_model_ready(gguf_path, model_config=load_cfg) and not load_gguf_model(gguf_path, model_config=load_cfg):
            raise RuntimeError(f"GGUF model load failed: {gguf_path}")
        if state.gguf_model is None:
            raise RuntimeError("GGUF model is not loaded")
        prepared = _prepare_gguf_messages(messages, cfg)
        if attempt_guard is not None:
            attempt_guard()
        try:
            response = state.gguf_model.create_chat_completion(
                messages=prepared,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
        finally:
            if attempt_guard is not None:
                attempt_guard()
    try:
        return str(response["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return str(response or "").strip()


def _call_api_text(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout_s: int = 180,
    request_budget_bytes: int | None = None,
    request_receipt: dict[str, Any] | None = None,
    attempt_guard: Callable[[], None] | None = None,
) -> str:
    url = cfg.get("url")
    if not url:
        raise RuntimeError("model API URL is empty")
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
        raise RuntimeError(f"configured API token source is unavailable: {token_source}")
    payload = chat_payload(cfg.get("model"), messages, max_tokens=max_tokens, temperature=temperature)
    tls_verify: bool | str = cfg.get("tls_verify", True) is not False
    ca_bundle = str(cfg.get("tls_ca_bundle") or "").strip()
    if ca_bundle:
        tls_verify = ca_bundle
    transport: dict[str, Any] = {"json": payload}
    if request_budget_bytes is not None or request_receipt is not None:
        if request_budget_bytes is not None and (type(request_budget_bytes) is not int or request_budget_bytes <= 0):
            raise ValueError("Invalid API request-byte budget")
        # Measure the exact bytes handed to requests, not a similar messages
        # object which requests might subsequently escape differently.
        body = encode_chat_payload(payload)
        if request_budget_bytes is not None and len(body) > request_budget_bytes:
            raise ValueError("API request exceeds its exact transport-byte budget")
        transport = {"data": body}
        if request_receipt is not None:
            request_receipt.update(request_body_receipt(payload, body))
    request_options = dict(
        headers=headers, **transport,
        timeout=max(3, min(300, int(timeout_s or 180))), verify=tls_verify,
    )
    if attempt_guard is None:
        upstream = req.post(url, **request_options)
    else:
        guard = InvocationGuard(attempt_guard)

        class GuardedSession(req.Session):
            def send(self, request, **kwargs):
                # Request/auth/environment preparation has finished. Redirects
                # also recurse through this boundary before their own send.
                guard()
                try:
                    return super().send(request, **kwargs)
                finally:
                    guard()

        with GuardedSession() as session:
            upstream = session.post(url, **request_options)
    if upstream.status_code >= 400:
        raise RuntimeError(f"model API HTTP {upstream.status_code}: {upstream.text[:500]}")
    data = upstream.json()
    try:
        return str(data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return str(data or "").strip()


def _call_codex_text(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout_s: int = 900,
    preserve_input: bool = False,
    attempt_guard: Callable[[], None] | None = None,
) -> str:
    from code_assist_v1.codex_bridge import CodexBridgeError, stream_codex_exec

    chunks: list[str] = []
    errors: list[str] = []
    failure_code = "CODEX_EMPTY_OUTPUT"
    retryable = True
    for event in stream_codex_exec(
        messages,
        cfg,
        timeout_s=timeout_s,
        **({"preserve_input": True} if preserve_input else {}),
        **({"attempt_guard": attempt_guard} if attempt_guard is not None else {}),
    ):
        if event.get("error"):
            errors.append(str(event.get("error") or "Codex bridge failed"))
            failure_code = str(event.get("code") or "CODEX_EXEC_FAILED")
            retryable = bool(event.get("retryable", True))
            continue
        delta = event.get("delta")
        if delta:
            chunks.append(str(delta))
    text = "".join(chunks).strip()
    if text:
        return text
    raise CodexBridgeError(
        failure_code,
        " | ".join(errors) or "Codex bridge returned no output",
        retryable=retryable,
    )


def _default_system(output_format: str) -> str:
    if output_format == "json":
        return (
            "You are an internal Second Brain and Notion processing model. "
            "Return compact valid JSON only. Do not include markdown fences, prose, or chain-of-thought."
        )
    return (
        "You are an internal Second Brain and Notion processing model. "
        "Return the requested final content only. Do not include chain-of-thought or process commentary."
    )


def _messages_for_task(
    task: str,
    payload: dict[str, Any],
    *,
    output_format: str,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if messages is not None:
        return [dict(m) for m in messages if isinstance(m, dict)]
    return [
        {"role": "system", "content": system_prompt or _default_system(output_format)},
        {
            "role": "user",
            "content": user_prompt
            or json.dumps({"task": task, "payload": payload}, ensure_ascii=False, default=str),
        },
    ]


def run_internal_text(
    task: str,
    payload: dict[str, Any],
    *,
    output_format: str = "text",
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.15,
    timeout_s: int = 900,
    api_timeout_s: int = 180,
    validate_output: Any | None = None,
    provider_order: list[str] | tuple[str, ...] | None = None,
    model_ids: list[str] | tuple[str, ...] | None = None,
    request_budget_bytes: int | None = None,
    max_attempts: int | None = None,
    capture_request_receipt: bool = False,
    preserve_codex_input: bool = False,
    attempt_guard: Callable[[], None] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    # Invocation-local authority, never serialized or treated as a model error.
    if attempt_guard is not None and not callable(attempt_guard):
        raise TypeError("attempt_guard must be callable or None")
    guard = None if attempt_guard is None else InvocationGuard(attempt_guard)
    if type(capture_request_receipt) is not bool or type(preserve_codex_input) is not bool:
        raise ValueError("Invalid complete-input capture policy")
    if request_budget_bytes is not None and (type(request_budget_bytes) is not int or request_budget_bytes <= 0):
        raise ValueError("Invalid API request-byte budget")
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts <= 0):
        raise ValueError("Invalid internal model-attempt budget")
    prompt_messages = _messages_for_task(
        task,
        payload,
        output_format=output_format,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        messages=messages,
    )
    errors: list[str] = []
    failure_codes: list[str] = []
    providers = [str(item) for item in (provider_order or []) if str(item)]
    if not providers:
        providers = default_provider_order()
    attempts = 0
    for provider in providers:
        for model_id, cfg in _candidate_configs(provider, model_ids=model_ids):
            if max_attempts is not None and attempts >= max_attempts:
                break
            attempts += 1
            receipt: dict[str, Any] = {}
            if guard is not None:
                guard()
            try:
                if provider == "gguf":
                    text = _call_gguf_text(
                        cfg, prompt_messages, max_tokens=max_tokens, temperature=temperature,
                        **({"attempt_guard": guard} if guard is not None else {}),
                    )
                elif provider == "hynix_api":
                    text = _call_api_text(
                        cfg,
                        prompt_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout_s=api_timeout_s,
                        **({"request_budget_bytes": request_budget_bytes}
                           if request_budget_bytes is not None else {}),
                        **({"request_receipt": receipt}
                           if request_budget_bytes is not None or capture_request_receipt else {}),
                        **({"attempt_guard": guard} if guard is not None else {}),
                    )
                else:
                    codex_cfg = dict(cfg)
                    text = _call_codex_text(
                        codex_cfg,
                        prompt_messages,
                        timeout_s=timeout_s,
                        **({"preserve_input": True} if preserve_codex_input else {}),
                        **({"attempt_guard": guard} if guard is not None else {}),
                    )
            except Exception as e:
                errors.append(f"{provider}:{model_id}: {e}")
                failure_codes.append(
                    str(getattr(e, "code", "")).strip()
                    or ("CODEX_EXEC_FAILED" if provider == "codex" else "MODEL_EXEC_FAILED")
                )
                continue
            finally:
                # Runs on success, timeout, failure and cancellation. A failure
                # here escapes the provider fallback handler above, including
                # a pending `continue`: stale evidence must not be sent again.
                if guard is not None:
                    guard()
            try:
                if text:
                    if callable(validate_output):
                        problem = str(validate_output(text) or "").strip()
                        if problem:
                            errors.append(f"{provider}:{model_id}: {problem}")
                            failure_codes.append(
                                "CODEX_OUTPUT_INVALID"
                                if provider == "codex"
                                else "MODEL_OUTPUT_INVALID"
                            )
                            continue
                    return text, {
                        "model_kind": provider,
                        "model_id": model_id,
                        "model_used": cfg.get("name") or cfg.get("model") or model_id,
                        "fallback_errors": errors,
                        **({"request_transport": receipt} if receipt else {}),
                        **({"attempt_count": attempts} if max_attempts is not None else {}),
                    }
                errors.append(f"{provider}:{model_id}: empty output")
                failure_codes.append(
                    "CODEX_EMPTY_OUTPUT" if provider == "codex" else "MODEL_EMPTY_OUTPUT"
                )
            except Exception as e:
                errors.append(f"{provider}:{model_id}: {e}")
                failure_codes.append(
                    str(getattr(e, "code", "")).strip()
                    or ("CODEX_EXEC_FAILED" if provider == "codex" else "MODEL_EXEC_FAILED")
                )
    return None, {
        "model_kind": "",
        "model_id": "",
        "model_used": "",
        "fallback_errors": errors,
        "failure_codes": failure_codes,
        "error": "internal LLM failed" + (": " + " | ".join(errors[-4:]) if errors else ""),
    }


def run_internal_json(
    task: str,
    payload: dict[str, Any],
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    timeout_s: int = 900,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    def _json_problem(value: str) -> str:
        return "" if extract_json(value) else "non-JSON output"

    text, meta = run_internal_text(
        task,
        payload,
        output_format="json",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
        validate_output=_json_problem,
    )
    if not text:
        return None, meta
    parsed = extract_json(text)
    if not parsed:
        meta = dict(meta)
        meta["error"] = "internal LLM returned non-JSON output"
        meta["raw_output"] = text[:1000]
        return None, meta
    parsed["_internal_model_kind"] = meta.get("model_kind", "")
    parsed["_internal_model_id"] = meta.get("model_id", "")
    parsed["_internal_model_used"] = meta.get("model_used", "")
    parsed["_internal_fallback_errors"] = meta.get("fallback_errors") or []
    return parsed, meta
