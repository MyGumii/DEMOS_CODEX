"""Shared internal LLM runner for Second Brain and Notion background tasks."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Callable

import requests as req
from urllib3.exceptions import ReadTimeoutError

from code_assist_v1.config import resolve_api_token
from code_assist_v1.llm_wire import (ChatRequestBudgetExceeded, chat_payload, encode_chat_payload,
    request_body_receipt, validate_chat_template_kwargs)
from code_assist_v1.llm_response import ChatResponsePolicy, ModelResponseError, consume_chat_response
from code_assist_v1.codex_response import CodexResponsePolicy, validate_codex_response_receipt
from code_assist_v1.model_invocation import InvocationGuard
from code_assist_v1.model_routing import (
    codex_fallback_enabled,
    ordered_model_candidates,
)
from code_assist_v1.models import DEFAULT_MODEL_PRIORITY, ENV_CONFIG, MODEL_REGISTRY


_HTTP_FAILURE_STATUS_LIMIT = 64


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
    response_policy: ChatResponsePolicy | None = None,
    response_receipt: dict[str, Any] | None = None,
    image_attempt: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    chat_template_kwargs: dict[str, Any] | None = None,
    failure_diagnostics: dict[str, Any] | None = None,
    context_request_sha256: str | None = None,
    context_refusal: dict[str, Any] | None = None,
) -> str:
    chat_template_kwargs = validate_chat_template_kwargs(chat_template_kwargs)
    if failure_diagnostics is not None and (type(failure_diagnostics) is not dict or failure_diagnostics):
        raise ValueError("Failure diagnostics require an empty invocation-local dictionary")
    if response_policy is not None and type(response_policy) is not ChatResponsePolicy:
        raise ValueError("Invalid API response policy")
    if response_receipt is not None and response_policy is None:
        raise ValueError("A response receipt requires a bounded response policy")
    if chat_template_kwargs is not None and (response_policy is None or request_budget_bytes is None):
        raise ValueError("Explicit non-thinking mode requires bounded API request and response policies")
    if failure_diagnostics is not None and response_policy is None:
        raise ValueError("Failure diagnostics require a bounded API response policy")
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
    payload = chat_payload(cfg.get("model"), messages, max_tokens=max_tokens, temperature=temperature,
        response_format=response_format, chat_template_kwargs=chat_template_kwargs)
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
        if image_attempt is not None:
            image_attempt.update(phase="prepared", request_sha256=request_body_receipt(payload, body)["body_sha256"],
                                 request_bytes=len(body))
        if request_budget_bytes is not None and len(body) > request_budget_bytes:
            raise ChatRequestBudgetExceeded("API request exceeds its exact transport-byte budget")
        transport = {"data": body}
        if request_receipt is not None:
            request_receipt.update(request_body_receipt(payload, body))
    request_options = dict(
        headers=headers, **transport,
        timeout=max(3, min(300, int(timeout_s or 180))), verify=tls_verify,
    )
    if response_policy is not None:
        # HTTP stream=True only changes acquisition. The Chat Completions
        # request itself remains stream=False, with an exact request receipt.
        headers["Accept-Encoding"] = "identity"
        request_options.update(stream=True, allow_redirects=False)
        bounded_guard = None if attempt_guard is None else InvocationGuard(attempt_guard)

        class BoundedSession(req.Session):
            def resolve_redirects(self, *args, **kwargs):
                # Requests consumes resp.content even with allow_redirects=False
                # while preparing Response.next. Suppress that hidden read too.
                return iter(())

            def send(self, request, **kwargs):
                response = None
                try:
                    if bounded_guard is not None:
                        bounded_guard()
                    if image_attempt is not None:
                        # Boundary attempted, not a promise of provider receipt.
                        image_attempt.update(phase="submission_attempted")
                    response = super().send(request, **kwargs)
                    if image_attempt is not None:
                        image_attempt.update(phase="response_received")
                    if bounded_guard is not None:
                        bounded_guard()
                    return response
                except BaseException:
                    if response is not None:
                        response.close()
                    raise

        with BoundedSession() as session:
            upstream = None
            try:
                upstream = session.post(url, **request_options)
                if context_request_sha256 is not None:
                    from code_assist_v1.llm_context_refusal import consume_context_aware_chat_response
                    text, received = consume_context_aware_chat_response(upstream, response_policy, bounded_guard,
                        request_sha256=context_request_sha256, context_refusal=context_refusal,
                        failure_diagnostics=failure_diagnostics)
                else:
                    text, received = consume_chat_response(upstream, response_policy, bounded_guard,
                        **({"failure_diagnostics": failure_diagnostics} if failure_diagnostics is not None else {}))
                if response_receipt is not None:
                    response_receipt.update(received)
                return text
            finally:
                if upstream is not None:
                    upstream.close()
        # Default legacy calls below intentionally retain their existing
        # behavior. The new opt-in lane needs its own compatibility evidence.
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
    response_policy: CodexResponsePolicy | None = None,
    response_receipt: dict[str, Any] | None = None,
    image_inputs=None,
    image_policy=None,
    image_attempt=None,
) -> str:
    from code_assist_v1.codex_bridge import CodexBridgeError, build_codex_prompt, stream_codex_exec

    if response_policy is not None:
        if type(response_policy) is not CodexResponsePolicy or preserve_input is not True or attempt_guard is None:
            raise ValueError("Bounded Codex calls require complete input, policy and guard.")
        completed = None
        image_options = {}
        expected_images = None
        prompt_messages = messages
        if image_inputs is not None or image_policy is not None:
            from code_assist_v1.llm_images import image_input_receipt, prepare_image_messages
            expected_images = image_input_receipt(image_inputs, image_policy, transport="codex-cli-image-files-v1")
            prompt_messages = prepare_image_messages(messages, image_inputs, inline=False)
            image_options = {"image_inputs": image_inputs, "image_policy": image_policy,
                             **({"image_attempt": image_attempt} if image_attempt is not None else {})}
        stream = stream_codex_exec(messages, cfg, timeout_s=timeout_s, preserve_input=True,
                                   attempt_guard=attempt_guard, response_policy=response_policy, **image_options)
        try:
            for event in stream:
                if not isinstance(event, dict) or event.get("error"):
                    raise CodexBridgeError(str(event.get("code") or "CODEX_EXEC_FAILED") if isinstance(event, dict)
                                           else "CODEX_EXEC_FAILED", "Codex did not complete successfully.", retryable=False)
                expected_keys = {"delta", "codex_response_transport"}
                if expected_images is not None:
                    expected_keys.add("image_input_transport")
                    if event.get("image_input_transport") != expected_images:
                        raise CodexBridgeError("MODEL_IMAGE_RECEIPT_INVALID", "Image input receipt does not match.", retryable=False)
                if completed is not None or set(event) != expected_keys:
                    raise CodexBridgeError("CODEX_OUTPUT_INVALID", "Codex returned an incomplete or repeated result.", retryable=False)
                text = event["delta"]
                if not isinstance(text, str) or not text.strip():
                    raise CodexBridgeError("CODEX_EMPTY_OUTPUT", "Codex returned no final text.", retryable=False)
                receipt = validate_codex_response_receipt(
                    event["codex_response_transport"], text=text, policy=response_policy,
                    expected_prompt=build_codex_prompt(prompt_messages, preserve_input=True).encode("utf-8"))
                completed = (text, receipt)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if completed is None:
            raise CodexBridgeError("CODEX_OUTPUT_INCOMPLETE", "Codex did not report a completed result.", retryable=False)
        if response_receipt is not None:
            response_receipt.update(completed[1])
        return completed[0]
    if image_inputs is not None or image_policy is not None:
        raise ValueError("Image input requires the bounded complete-input Codex lane.")
    if response_receipt is not None:
        raise ValueError("A Codex receipt requires a bounded response policy")

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
    response_policy: ChatResponsePolicy | None = None,
    codex_response_policy: CodexResponsePolicy | None = None,
    api_response_policy: ChatResponsePolicy | None = None,
    image_inputs=None,
    image_policy=None,
    image_attempt_receipt: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    chat_template_kwargs: dict[str, Any] | None = None,
    capture_failure_diagnostics: bool = False,
    capture_http_failure_statuses: bool = False,
    context_refusal_version: str | None = None,
    context_request_sha256: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    # Invocation-local authority, never serialized or treated as a model error.
    if attempt_guard is not None and not callable(attempt_guard):
        raise TypeError("attempt_guard must be callable or None")
    guard = None if attempt_guard is None else InvocationGuard(attempt_guard)
    if type(capture_request_receipt) is not bool or type(preserve_codex_input) is not bool:
        raise ValueError("Invalid complete-input capture policy")
    if type(capture_failure_diagnostics) is not bool:
        raise ValueError("Invalid API failure-diagnostic capture policy")
    if type(capture_http_failure_statuses) is not bool:
        raise ValueError("Invalid HTTP failure-status capture policy")
    if request_budget_bytes is not None and (type(request_budget_bytes) is not int or request_budget_bytes <= 0):
        raise ValueError("Invalid API request-byte budget")
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts <= 0):
        raise ValueError("Invalid internal model-attempt budget")
    if response_policy is not None and type(response_policy) is not ChatResponsePolicy:
        raise ValueError("Invalid bounded API response policy")
    if codex_response_policy is not None and type(codex_response_policy) is not CodexResponsePolicy:
        raise ValueError("Invalid bounded Codex response policy")
    if codex_response_policy is not None and response_policy is not None:
        raise ValueError("Different provider response policies cannot be combined")
    if api_response_policy is not None:
        if type(api_response_policy) is not ChatResponsePolicy:
            raise ValueError("Invalid API-lane response policy")
        if response_policy is not None or codex_response_policy is not None:
            raise ValueError("API-lane and provider-exclusive response policies cannot be combined")
    if capture_http_failure_statuses and (
            type(provider_order) not in (list, tuple) or tuple(provider_order) != ("hynix_api",)
            or response_policy is None or request_budget_bytes is None or api_response_policy is not None):
        raise ValueError("HTTP status capture requires an exclusive bounded API response policy")
    chat_template_kwargs = validate_chat_template_kwargs(chat_template_kwargs)
    if chat_template_kwargs is not None or capture_failure_diagnostics:
        if (type(provider_order) not in (list, tuple) or tuple(provider_order) != ("hynix_api",)
                or type(model_ids) not in (list, tuple) or len(model_ids) != 1
                or not isinstance(model_ids[0], str) or not model_ids[0].strip()
                or len(model_ids[0]) > 160 or any(ord(char) < 32 for char in model_ids[0])
                or max_attempts != 1 or response_policy is None or request_budget_bytes is None):
            raise ValueError("Explicit API modes require one frozen bounded API attempt")
    context_capture = context_refusal_version is not None or context_request_sha256 is not None
    if context_capture:
        from code_assist_v1.llm_context_refusal import CONTEXT_REFUSAL_OPT_IN, _hex
        if (task != "canonical_pdf_continuation_batch_authoring_v1"
                or context_refusal_version != CONTEXT_REFUSAL_OPT_IN or not _hex(context_request_sha256)
                or capture_failure_diagnostics is not True or capture_request_receipt is not True
                or tuple(provider_order or ()) != ("hynix_api",) or max_attempts != 1
                or response_policy is None or request_budget_bytes is None or guard is None):
            raise ValueError("Context refusal capture requires one bound continuation API request.")
    prompt_messages = _messages_for_task(
        task,
        payload,
        output_format=output_format,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        messages=messages,
    )
    has_images = image_inputs is not None or image_policy is not None
    if image_attempt_receipt is not None and (not has_images or type(image_attempt_receipt) is not dict or image_attempt_receipt):
        raise ValueError("Image attempt capture requires an empty invocation-local dictionary and image inputs.")
    image_attempt = image_attempt_receipt if image_attempt_receipt is not None else {}
    if has_images:
        from code_assist_v1.llm_images import (
            IMAGE_INPUT_CAPABILITY, bind_image_receipt, image_input_receipt,
            prepare_image_messages, validate_image_inputs,
        )
        if guard is None or max_attempts != 1:
            raise ValueError("Image input requires a fresh guard and exactly one model attempt.")
        guard()
        validated_images = validate_image_inputs(image_inputs, image_policy)
        # Validate message shape without constructing base64 for an unsupported
        # provider. The normal legacy text lane is deliberately unchanged.
        prepare_image_messages(prompt_messages, image_inputs, inline=False)
        image_attempt.update(schema_version=1, format="image-input-attempt-v1", phase="validated",
                             status="pending", images=list(validated_images), provider_receipt_proven=False)
    errors: list[str] = []
    failure_codes: list[str] = []
    # Invocation-local, index-aligned diagnostics only; never a retry input.
    failure_http_statuses: list[int | None] = []
    provider_failure_diagnostics: dict[str, Any] = {}
    captured_context_refusal: dict[str, Any] = {}
    captured_context_request_transport: dict[str, Any] = {}
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
            response_receipt: dict[str, Any] = {}
            codex_receipt: dict[str, Any] = {}
            failure_diagnostics: dict[str, Any] = {}
            context_refusal: dict[str, Any] = {}
            # Unlike the exclusive authoring/graph response_policy, this opt-in
            # validates only API attempts and preserves GGUF/Codex fallbacks.
            selected_api_policy = response_policy or (
                api_response_policy if provider == "hynix_api" else None
            )
            try:
                if has_images:
                    image_attempt.update(provider=provider, model_id=model_id)
                if guard is not None:
                    guard()
                if has_images:
                    capabilities = cfg.get("capabilities") or ()
                    if (provider not in {"hynix_api", "codex"}
                            or not isinstance(capabilities, (tuple, list, set, frozenset))
                            or IMAGE_INPUT_CAPABILITY not in capabilities):
                        raise ModelResponseError("MODEL_IMAGE_UNSUPPORTED", "Selected model has no enabled image input adapter.")
                    if provider == "hynix_api" and (selected_api_policy is None or request_budget_bytes is None):
                        raise ValueError("Image API input requires bounded request and response policies.")
                    if provider == "codex" and (codex_response_policy is None or preserve_codex_input is not True):
                        raise ValueError("Image Codex input requires bounded complete-input policy.")
                if response_policy is not None and provider != "hynix_api":
                    raise ModelResponseError("MODEL_RESPONSE_POLICY_UNSUPPORTED",
                                             "This response policy requires a Chat Completions API adapter.")
                if codex_response_policy is not None and provider != "codex":
                    raise ModelResponseError("MODEL_RESPONSE_POLICY_UNSUPPORTED", "This response policy requires Codex.")
                if provider == "gguf":
                    text = _call_gguf_text(
                        cfg, prompt_messages, max_tokens=max_tokens, temperature=temperature,
                        **({"attempt_guard": guard} if guard is not None else {}),
                    )
                elif provider == "hynix_api":
                    text = _call_api_text(
                        cfg,
                        prepare_image_messages(prompt_messages, image_inputs, inline=True) if has_images else prompt_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout_s=api_timeout_s,
                        **({"request_budget_bytes": request_budget_bytes}
                           if request_budget_bytes is not None else {}),
                        **({"request_receipt": receipt}
                           if request_budget_bytes is not None or capture_request_receipt else {}),
                        **({"attempt_guard": guard} if guard is not None else {}),
                        **({"response_policy": selected_api_policy, "response_receipt": response_receipt}
                           if selected_api_policy is not None else {}),
                        **({"image_attempt": image_attempt} if has_images else {}),
                        **({"response_format": response_format} if response_format is not None else {}),
                        **({"chat_template_kwargs": chat_template_kwargs} if chat_template_kwargs is not None else {}),
                        **({"failure_diagnostics": failure_diagnostics} if capture_failure_diagnostics else {}),
                        **({"context_request_sha256": context_request_sha256,
                            "context_refusal": context_refusal} if context_capture else {}),
                    )
                else:
                    codex_cfg = dict(cfg)
                    text = _call_codex_text(
                        codex_cfg,
                        prompt_messages,
                        timeout_s=timeout_s,
                        **({"preserve_input": True} if preserve_codex_input else {}),
                        **({"attempt_guard": guard} if guard is not None else {}),
                        **({"response_policy": codex_response_policy, "response_receipt": codex_receipt}
                           if codex_response_policy is not None else {}),
                        **({"image_inputs": image_inputs, "image_policy": image_policy,
                            "image_attempt": image_attempt} if has_images else {}),
                    )
            except Exception as e:
                if context_capture and receipt:
                    captured_context_request_transport = deepcopy(receipt)
                if context_capture and context_refusal:
                    from code_assist_v1.llm_context_refusal import validate_context_refusal
                    captured_context_refusal = validate_context_refusal(context_refusal)
                if capture_failure_diagnostics and failure_diagnostics:
                    provider_failure_diagnostics = deepcopy(failure_diagnostics)
                if has_images:
                    image_attempt.update(status="failed", failure_code=_provider_failure_code(e, provider))
                errors.append(f"{provider}:{model_id}: {e}")
                failure_codes.append(_provider_failure_code(e, provider,
                    api_status_categories=api_response_policy is not None))
                if capture_http_failure_statuses:
                    status = e.http_status if (
                        provider == "hynix_api" and isinstance(e, ModelResponseError)
                        and e.code == "MODEL_RESPONSE_HTTP_STATUS") else None
                    failure_http_statuses.append(status if (
                        type(status) is int and 100 <= status <= 599 and status != 200) else None)
                continue
            except BaseException as e:
                if has_images:
                    image_attempt.update(status="failed", failure_code="MODEL_INVOCATION_INTERRUPTED")
                    e.image_input_attempt = deepcopy(image_attempt)
                raise
            finally:
                # Runs on success, timeout, failure and cancellation. A failure
                # here escapes the provider fallback handler above, including
                # a pending `continue`: stale evidence must not be sent again.
                if guard is not None:
                    try:
                        guard()
                    except BaseException as e:
                        if has_images:
                            image_attempt.update(status="failed", failure_code="MODEL_INVOCATION_GUARD_FAILED")
                            e.image_input_attempt = deepcopy(image_attempt)
                        raise
            try:
                if text:
                    if callable(validate_output):
                        problem = str(validate_output(text) or "").strip()
                        if problem:
                            if has_images:
                                image_attempt.update(status="failed", failure_code="MODEL_OUTPUT_INVALID")
                            errors.append(f"{provider}:{model_id}: {problem}")
                            failure_codes.append(
                                "CODEX_OUTPUT_INVALID"
                                if provider == "codex"
                                else "MODEL_OUTPUT_INVALID"
                            )
                            if capture_http_failure_statuses:
                                failure_http_statuses.append(None)
                            continue
                    image_transport = None
                    if has_images:
                        image_transport = bind_image_receipt(
                            image_input_receipt(image_inputs, image_policy, transport=(
                                "chat-completions-data-uri-v1" if provider == "hynix_api" else "codex-cli-image-files-v1")),
                            request_sha256=receipt["body_sha256"] if provider == "hynix_api" else codex_receipt["prompt_sha256"],
                            provider=provider, model_id=model_id, text=text)
                        image_attempt.update(status="completed", phase="completed")
                    return text, {
                        "model_kind": provider,
                        "model_id": model_id,
                        "model_used": cfg.get("name") or cfg.get("model") or model_id,
                        "fallback_errors": errors,
                        **({"request_transport": receipt} if receipt else {}),
                        **({"response_transport": response_receipt,
                            "finish_reason": response_receipt.get("finish_reason")}
                           if selected_api_policy is not None else {}),
                        **({"codex_response_transport": codex_receipt}
                           if codex_response_policy is not None else {}),
                        **({"attempt_count": attempts} if max_attempts is not None else {}),
                        **({"image_input_attempt": deepcopy(image_attempt)} if has_images else {}),
                        **({"image_input_transport": image_transport} if has_images else {}),
                    }
                errors.append(f"{provider}:{model_id}: empty output")
                failure_codes.append(
                    "CODEX_EMPTY_OUTPUT" if provider == "codex" else "MODEL_EMPTY_OUTPUT"
                )
                if capture_http_failure_statuses:
                    failure_http_statuses.append(None)
            except Exception as e:
                if has_images:
                    image_attempt.update(status="failed", failure_code=_provider_failure_code(e, provider))
                errors.append(f"{provider}:{model_id}: {e}")
                failure_codes.append(_provider_failure_code(e, provider))
                if capture_http_failure_statuses:
                    failure_http_statuses.append(None)
            except BaseException as e:
                if has_images:
                    image_attempt.update(status="failed", failure_code="MODEL_INVOCATION_INTERRUPTED")
                    e.image_input_attempt = deepcopy(image_attempt)
                raise
    if has_images and image_attempt.get("status") == "pending":
        image_attempt.update(status="failed", failure_code="MODEL_NO_COMPLETED_RESULT")
    return None, {
        "model_kind": "",
        "model_id": "",
        "model_used": "",
        "fallback_errors": errors,
        "failure_codes": failure_codes,
        **({"provider_http_statuses": failure_http_statuses} if (
            capture_http_failure_statuses and failure_codes
            and len(failure_http_statuses) <= _HTTP_FAILURE_STATUS_LIMIT
            and len(failure_http_statuses) == len(failure_codes)
            and all(code == "MODEL_RESPONSE_HTTP_STATUS" for code in failure_codes)
            and all(type(status) is int and 100 <= status <= 599 and status != 200
                    for status in failure_http_statuses)) else {}),
        **({"provider_failure_diagnostics": provider_failure_diagnostics} if provider_failure_diagnostics else {}),
        **({"context_refusal": captured_context_refusal} if captured_context_refusal else {}),
        **({"request_transport": captured_context_request_transport} if captured_context_request_transport else {}),
        **({"image_input_attempt": deepcopy(image_attempt)} if has_images else {}),
        "error": "internal LLM failed" + (": " + " | ".join(errors[-4:]) if errors else ""),
    }


def _provider_failure_code(error: Exception, provider: str, *, api_status_categories: bool = False) -> str:
    """Preserve typed transport failures without parsing provider error prose."""
    # Canonical answer guidance only: exclusive authoring/Graph and legacy
    # callers retain their established generic HTTP category and retry policy.
    if (api_status_categories and provider == "hynix_api"
            and isinstance(error, ChatRequestBudgetExceeded)):
        return "MODEL_API_INPUT_TOO_LARGE"
    if (api_status_categories and provider == "hynix_api"
            and isinstance(error, ModelResponseError) and error.code == "MODEL_RESPONSE_HTTP_STATUS"):
        status = error.http_status
        if type(status) is int:
            if status in (401, 403):
                return "MODEL_API_AUTH_FAILED"
            if status == 413:
                return "MODEL_API_INPUT_TOO_LARGE"
            if status == 429:
                return "MODEL_API_RATE_LIMITED"
            if 500 <= status <= 599:
                return "MODEL_API_SERVER_ERROR"
    explicit = str(getattr(error, "code", "")).strip()
    if explicit:
        return explicit
    if isinstance(error, (TimeoutError, req.exceptions.Timeout)):
        return "CODEX_EXEC_TIMEOUT" if provider == "codex" else "MODEL_EXEC_TIMEOUT"
    # Requests wraps urllib3 read timeouts during streamed entity consumption
    # in ConnectionError, unlike timeouts before response headers. Inspect the
    # typed cause only; never classify errors by provider-controlled prose.
    if (isinstance(error, req.exceptions.ConnectionError) and error.args
            and isinstance(error.args[0], ReadTimeoutError)):
        return "MODEL_EXEC_TIMEOUT"
    return "CODEX_EXEC_FAILED" if provider == "codex" else "MODEL_EXEC_FAILED"


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


