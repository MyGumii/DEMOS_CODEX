"""
Model list and system prompt routes for code_assist_v1.
"""
from __future__ import annotations

import os

from flask import jsonify

from code_assist_v1.model_routing import codex_fallback_enabled, model_kind, ordered_model_candidates
from code_assist_v1.model_runtime import gguf_catalog_enabled

from code_assist_v1.prompts import (
    ANTI_HALLUCINATION,
    CODING_SYSTEM_PROMPT,
    KNOWLEDGE_INJECT_HEADER,
    SKILL_INJECT_HEADER,
    WORKSPACE_INJECT_HEADER,
)


def _standalone() -> bool:
    return os.environ.get("CODE_ASSIST_STANDALONE", "").lower() in {"1", "true", "yes", "on"}


def _load_model_state():
    if _standalone():
        if gguf_catalog_enabled():
            try:
                from code_assist_v1.app_code import _register_gguf_models

                _register_gguf_models(autoload=False)
            except Exception as e:
                print(f"[code_assist_v1] GGUF model refresh skipped: {e}")
        from code_assist_v1.models import ENV_CONFIG, MODEL_REGISTRY

        return MODEL_REGISTRY, ENV_CONFIG
    try:
        from demos_v1.models import ENV_CONFIG, MODEL_REGISTRY

        return MODEL_REGISTRY, ENV_CONFIG
    except ImportError:
        from code_assist_v1.models import ENV_CONFIG, MODEL_REGISTRY

        return MODEL_REGISTRY, ENV_CONFIG


def _model_enabled(info: dict | None) -> bool:
    if not isinstance(info, dict):
        return True
    return info.get("enabled") is not False and not info.get("disabled")


def register_model_routes(app):
    @app.route("/api/code/models")
    def api_code_models():
        from code_assist_v1.config import DEFAULT_MODEL_PRIORITY

        model_registry, env_config = _load_model_state()
        codex_enabled = codex_fallback_enabled()

        api_models = []
        for mid, info in model_registry.items():
            if not _model_enabled(info):
                continue
            runtime = info.get("runtime", "api")
            env_id = info.get("env_id")
            if runtime == "gguf" or str(env_id or "").startswith("gguf-"):
                continue
            if model_kind(info) == "codex":
                continue
            caps = info.get("capabilities", [])
            if isinstance(caps, set):
                caps = sorted(caps)
            api_models.append({
                "id": mid,
                "env_id": env_id,
                "name": info.get("name"),
                "model": info.get("model"),
                "url": info.get("url"),
                "context_window": info.get("context_window", 0),
                "cost_tier": info.get("cost_tier", "medium"),
                "capabilities": list(caps),
                "kind": "api",
            })

        codex_models = []
        for mid, info in model_registry.items():
            if not codex_enabled or not _model_enabled(info) or model_kind(info) != "codex":
                continue
            caps = info.get("capabilities", [])
            if isinstance(caps, set):
                caps = sorted(caps)
            codex_models.append({
                "id": mid,
                "env_id": info.get("env_id") or mid,
                "name": info.get("name") or mid,
                "model": info.get("model") or "codex-cli",
                "context_window": info.get("context_window", 0),
                "cost_tier": info.get("cost_tier", "local"),
                "capabilities": list(caps),
                "kind": "codex",
            })

        gguf_models = []
        if gguf_catalog_enabled():
            for env_id, ecfg in env_config.items():
                registry_id = ecfg.get("_registry_id")
                if registry_id and not _model_enabled(model_registry.get(registry_id)):
                    continue
                if env_id.startswith("gguf-") or ecfg.get("runtime") == "gguf":
                    gguf_models.append({
                        "id": env_id,
                        "env_id": env_id,
                        "name": ecfg.get("name"),
                        "model": ecfg.get("model"),
                        "url": ecfg.get("url"),
                        "size_gb": ecfg.get("_size_gb"),
                        "missing": bool(ecfg.get("_missing")),
                        "mmproj": bool(ecfg.get("_mmproj_path")),
                        "registry_id": ecfg.get("_registry_id"),
                        "kind": "gguf",
                    })

        def _is_gguf_env(env_id: str, ecfg: dict | None = None) -> bool:
            return str(env_id or "").startswith("gguf-") or bool((ecfg or {}).get("runtime") == "gguf")

        def _usable_default(env_id: str) -> bool:
            ecfg = env_config.get(env_id)
            if ecfg and _is_gguf_env(env_id, ecfg):
                if not gguf_catalog_enabled() or ecfg.get("_missing"):
                    return False
            return bool(env_id)

        routing_priority = ordered_model_candidates(
            requested_model_id=None,
            priority_ids=list(DEFAULT_MODEL_PRIORITY or []),
            registry=model_registry,
            environment=env_config,
            codex_enabled=codex_enabled,
            include_fallback=True,
        )
        routing_priority = [
            mid
            for mid in routing_priority
            if gguf_catalog_enabled()
            or not (
                (mid in model_registry and model_registry[mid].get("runtime") == "gguf")
                or (mid in env_config and _is_gguf_env(mid, env_config[mid]))
            )
        ]

        default_id = ""
        for mid in routing_priority:
            candidate_id = ""
            if mid in model_registry:
                info = model_registry[mid]
                if info.get("runtime") == "gguf":
                    candidate_id = info.get("env_id") or mid
                elif info.get("runtime") == "codex":
                    candidate_id = mid
                else:
                    candidate_id = mid
            if mid in env_config:
                candidate_id = mid
            if _usable_default(candidate_id):
                default_id = candidate_id
                break
        if not default_id:
            if gguf_catalog_enabled():
                default_id = next(
                    (
                        env_id
                        for env_id in env_config
                        if _is_gguf_env(env_id, env_config[env_id])
                        and not env_config[env_id].get("_missing")
                    ),
                    "",
                )
            if not default_id:
                default_id = next(
                    (entry["id"] for entry in (*api_models, *codex_models)),
                    "",
                )

        effective_priority = routing_priority

        return jsonify({
            "api": api_models,
            "gguf": gguf_models,
            "codex": codex_models,
            "default": default_id,
            "priority": effective_priority,
            "mode": "standalone" if _standalone() else "integrated",
        })

    @app.route("/api/code/system_prompt")
    def api_system_prompt():
        return jsonify({
            "base": CODING_SYSTEM_PROMPT.strip(),
            "anti_hallucination": ANTI_HALLUCINATION.strip(),
            "headers": {
                "knowledge": KNOWLEDGE_INJECT_HEADER.strip(),
                "workspace": WORKSPACE_INJECT_HEADER.strip(),
                "skill": SKILL_INJECT_HEADER.strip(),
            },
            "note": (
                "The final system message is built from the base prompt, "
                "Agent Harness runtime plan, enabled skills, optional Wiki/Second Brain context, "
                "user/project memory, and attached workspace files."
            ),
        })
