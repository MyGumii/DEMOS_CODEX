"""
demos_v1/routes_openai_compat.py
====================================================================
OpenAI 호환 게이트웨이 라우트.

이 파일의 역할 (한 줄):
    Hermes(또는 다른 OpenAI 호환 클라이언트)가 demos_v1을 LLM 게이트웨이로
    사용할 수 있도록, `/v1/models` 와 `/v1/chat/completions` 두 라우트를
    노출한다.

설계 원칙:
- 기존 코드를 절대 수정하지 않음. 이 파일은 demos_v1의 기존 모듈을 호출만 한다.
- 모델 ID는 demos_v1이 이미 쓰는 키를 그대로 노출 (gguf-N / MODEL_REGISTRY 키).
- API 호출은 demos_v1.engine.py 의 패턴을 그대로 따른다 (Authorization Bearer).
- GGUF 호출은 demos_v1.gguf.gguf_chat() 을 그대로 호출한다.

이 파일을 비활성화하려면 demos_v1/__init__.py 에서
    from demos_v1.routes_openai_compat import register_openai_compat_routes
    register_openai_compat_routes(app)
두 줄만 제거하면 된다.

작성일: 2026-05-20
"""
import os
import time
import uuid
import json
import requests as req

from flask import request, jsonify, Response

from demos_v1.config import API_TOKEN
from demos_v1.models import MODEL_REGISTRY, ENV_CONFIG
from demos_v1.gguf import gguf_chat, load_gguf_model


# ============================================
# 헬퍼: 모델 ID → 디스패치 정보
# ============================================
def _resolve_model(model_id):
    """주어진 model_id 를 보고 (kind, info) 튜플을 반환.

    kind: "gguf" | "gguf-current" | "api" | None
    info:
      - kind == "gguf"          일 때: ENV_CONFIG[env_id] dict (지정 모델로 스왑)
      - kind == "gguf-current"  일 때: {} (현재 로드된 모델을 그대로 사용)
      - kind == "api"           일 때: MODEL_REGISTRY[model_id] dict
      - 매칭 없으면 (None, None)
    """
    if not model_id:
        return None, None
    # 0) 특수: "gguf-current" / "gguf" / "current" — demos_v1 이 지금 로드해둔 GGUF 그대로 사용.
    #    → demos_v1 UI 에서 모델을 바꾸면 Hermes 도 자동으로 따라감 (스왑 없음).
    if model_id in ("gguf-current", "gguf", "current", "local", "active"):
        return "gguf-current", {}
    # 1) GGUF env_id (gguf-0, gguf-1, ...) — 지정 모델로 강제 스왑
    if model_id.startswith("gguf-") and model_id in ENV_CONFIG:
        return "gguf", ENV_CONFIG[model_id]
    # 2) API MODEL_REGISTRY 키
    if model_id in MODEL_REGISTRY:
        return "api", MODEL_REGISTRY[model_id]
    # 3) env_id 로 들어왔을 수도 (dev, coder-480b 등) — MODEL_REGISTRY 값 중 env_id 매칭
    for reg_key, reg_val in MODEL_REGISTRY.items():
        if reg_val.get("env_id") == model_id:
            return "api", reg_val
    return None, None


# ============================================
# OpenAI 응답 포맷 빌더
# ============================================
def _build_openai_response(model_id, content, finish_reason="stop",
                           prompt_tokens=0, completion_tokens=0):
    """OpenAI chat.completion 스펙에 맞는 dict 반환."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content or ""},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _build_openai_error(message, code="server_error", status=500):
    """OpenAI 호환 오류 응답."""
    return jsonify({
        "error": {
            "message": str(message),
            "type": code,
            "code": code,
        }
    }), status


# ============================================
# 라우트 등록
# ============================================
def register_openai_compat_routes(app):
    """OpenAI 호환 게이트웨이 라우트 두 개를 Flask 앱에 등록한다."""

    # --------------------------------------------------------
    # GET /v1/models — 사용 가능한 모델 목록
    # --------------------------------------------------------
    @app.route("/v1/models", methods=["GET"])
    def openai_list_models():
        """Hermes가 모델 발견에 사용. OpenAI 스펙 그대로."""
        data = []
        # 0) 특수 가상 모델: 현재 demos_v1 이 로드한 GGUF 를 자동 추종
        #    → Hermes 가 이걸 default 로 등록하면, demos_v1 에서 모델 바꿔도 따로 재설정 불필요
        try:
            import demos_v1.utils as _utils_mod
            _loaded_path = getattr(_utils_mod, "gguf_loaded_path", "") or ""
            _loaded_name = os.path.basename(_loaded_path).replace(".gguf", "") if _loaded_path else ""
        except Exception:
            _loaded_name = ""
        data.append({
            "id": "gguf-current",
            "object": "model",
            "created": 0,
            "owned_by": "demos_v1-local-gguf",
            "_demos_name": f"LOCAL (현재 로드된 GGUF: {_loaded_name or '없음'})",
            "_demos_kind": "gguf-current",
            "_demos_note": "demos_v1 이 로드한 GGUF 를 자동으로 따라감 — 모델 변경 시 Hermes 재설정 불필요",
        })
        # GGUF 모델 (env_id가 gguf-N 형태인 것)
        for env_id, cfg in ENV_CONFIG.items():
            if env_id.startswith("gguf-"):
                data.append({
                    "id": env_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "demos_v1-local-gguf",
                    "_demos_name": cfg.get("name", ""),
                    "_demos_kind": "gguf",
                })
        # API 모델
        for reg_key, reg_val in MODEL_REGISTRY.items():
            data.append({
                "id": reg_key,
                "object": "model",
                "created": 0,
                "owned_by": "demos_v1-api-proxy",
                "_demos_name": reg_val.get("name", ""),
                "_demos_kind": "api",
                "_demos_capabilities": list(reg_val.get("capabilities", set())) if isinstance(reg_val.get("capabilities"), set) else reg_val.get("capabilities", []),
                "_demos_context_window": reg_val.get("context_window", 0),
            })
        return jsonify({"object": "list", "data": data})

    # --------------------------------------------------------
    # POST /v1/chat/completions — 메인 추론 엔드포인트
    # --------------------------------------------------------
    @app.route("/v1/chat/completions", methods=["POST"])
    def openai_chat_completions():
        """OpenAI Chat Completions 호환. 모델 ID로 GGUF/API 자동 분기."""
        try:
            body = request.get_json(force=True) or {}
        except Exception as e:
            return _build_openai_error(f"잘못된 JSON: {e}", "invalid_request_error", 400)

        model_id = body.get("model") or ""
        messages = body.get("messages") or []
        temperature = float(body.get("temperature", 0.5))
        max_tokens = int(body.get("max_tokens", 4096))
        stream = bool(body.get("stream", False))

        if not messages:
            return _build_openai_error("messages 가 비어있음", "invalid_request_error", 400)

        kind, info = _resolve_model(model_id)
        if kind is None:
            return _build_openai_error(
                f"알 수 없는 model: {model_id}. /v1/models 로 사용 가능 목록 확인.",
                "model_not_found", 404
            )

        # ===== GGUF 분기 — 지정 모델 (gguf-0, gguf-1, ...) =====
        if kind == "gguf":
            # 현재 로드된 GGUF 가 요청한 것과 다르면 스왑
            gguf_path = info.get("_gguf_path")
            if gguf_path and os.path.exists(gguf_path):
                # load_gguf_model 은 이미 같은 모델이면 스킵, 다르면 재로드
                ok = load_gguf_model(gguf_path)
                if not ok:
                    return _build_openai_error(
                        f"GGUF 모델 로드 실패: {model_id}", "gguf_load_failed", 500
                    )
            answer, err = gguf_chat(messages, temperature=temperature, max_tokens=max_tokens)
            if err:
                return _build_openai_error(err, "gguf_inference_error", 500)
            resp = _build_openai_response(model_id, answer or "")
            if stream:
                # 비스트리밍 결과를 단일 SSE 청크로 흘려보냄 (Hermes 호환)
                return _wrap_as_sse(resp)
            return jsonify(resp)

        # ===== GGUF 분기 — 현재 로드된 모델 자동 추종 (gguf-current) =====
        # demos_v1 UI 에서 모델을 바꾸면 Hermes 도 자동으로 그 모델을 쓰게 됨.
        # 스왑 없이 즉시 호출 — 그래서 가장 빠름.
        if kind == "gguf-current":
            import demos_v1.utils as _utils_mod
            if _utils_mod.gguf_model is None:
                return _build_openai_error(
                    "현재 로드된 GGUF 모델이 없음. demos_v1 UI 에서 모델을 먼저 선택하거나 gguf-N (예: gguf-4) 으로 명시 호출하세요.",
                    "no_gguf_loaded", 503
                )
            answer, err = gguf_chat(messages, temperature=temperature, max_tokens=max_tokens)
            if err:
                return _build_openai_error(err, "gguf_inference_error", 500)
            # 실제 사용된 모델 이름을 응답에 포함 — 클라이언트가 어떤 게 돌았는지 알 수 있게
            loaded_path = getattr(_utils_mod, "gguf_loaded_path", "") or ""
            loaded_name = os.path.basename(loaded_path).replace(".gguf", "") if loaded_path else "unknown"
            resp = _build_openai_response(f"gguf-current ({loaded_name})", answer or "")
            if stream:
                return _wrap_as_sse(resp)
            return jsonify(resp)

        # ===== API 분기 (HCP / Common 등) =====
        if kind == "api":
            api_url = info.get("url")
            api_model = info.get("model")
            if not api_url or not api_model:
                return _build_openai_error(
                    f"API 모델 정보 불완전: {model_id}", "model_config_error", 500
                )
            headers = {"Content-Type": "application/json"}
            if API_TOKEN:
                headers["Authorization"] = f"Bearer {API_TOKEN}"
            try:
                upstream = req.post(
                    api_url,
                    headers=headers,
                    json={
                        "model": api_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": stream,
                    },
                    timeout=180,
                    verify=False,
                    stream=stream,
                )
            except Exception as e:
                return _build_openai_error(
                    f"upstream 요청 실패 ({api_url}): {e}",
                    "upstream_unreachable", 502
                )

            if upstream.status_code >= 400:
                return _build_openai_error(
                    f"upstream {upstream.status_code}: {upstream.text[:500]}",
                    "upstream_error", upstream.status_code
                )

            if stream:
                # 그대로 SSE 흘려보냄
                def _proxy():
                    for chunk in upstream.iter_content(chunk_size=1024):
                        if chunk:
                            yield chunk
                return Response(_proxy(), mimetype="text/event-stream")
            else:
                try:
                    result = upstream.json()
                except Exception as e:
                    return _build_openai_error(
                        f"upstream JSON 파싱 실패: {e}", "upstream_decode_error", 502
                    )
                # 모델 필드를 demos_v1의 ID 로 덮어써서 클라이언트 혼동 방지
                result["model"] = model_id
                return jsonify(result)

        return _build_openai_error(f"내부 디스패치 실패: {model_id}", "dispatch_error", 500)

    # 콘솔에 등록 알림
    print("  🤖 OpenAI 호환 게이트웨이 활성: GET /v1/models, POST /v1/chat/completions")


# ============================================
# (보조) 비스트리밍 응답을 단일 SSE 청크로 감싸기 — Hermes 호환용
# ============================================
def _wrap_as_sse(openai_response):
    """비스트리밍 dict 응답을 OpenAI SSE 청크 두 개로 흘려보낸다.
    1) 본문 청크 (delta.content 에 전체 텍스트)
    2) 종료 청크 (finish_reason="stop")
    3) [DONE]
    """
    content = openai_response["choices"][0]["message"]["content"]
    model_id = openai_response["model"]
    chunk_id = openai_response["id"]
    created = openai_response["created"]

    def _gen():
        first = {
            "id": chunk_id, "object": "chat.completion.chunk",
            "created": created, "model": model_id,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
        end = {
            "id": chunk_id, "object": "chat.completion.chunk",
            "created": created, "model": model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(_gen(), mimetype="text/event-stream")
