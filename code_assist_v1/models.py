"""
Model registry for code_assist_v1.

Standalone mode prefers code_assist_v1/api_config.json. Integrated mode keeps
using the root api_config.json so the /code blueprint remains compatible with
demos_v1.
"""
from __future__ import annotations

import json
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR)
_STANDALONE = os.environ.get("CODE_ASSIST_STANDALONE", "").lower() in {"1", "true", "yes", "on"}

_env_config_path = os.environ.get("CODE_ASSIST_API_CONFIG", "").strip()
_explicit_config_path = bool(_env_config_path)
_pkg_config_path = os.path.join(_THIS_DIR, "api_config.json")
_root_config_path = os.path.join(_ROOT_DIR, "api_config.json")
if _env_config_path:
    API_CONFIG_PATH = _env_config_path
elif _STANDALONE and os.path.isfile(_pkg_config_path):
    API_CONFIG_PATH = _pkg_config_path
else:
    API_CONFIG_PATH = _root_config_path

_EXT_CONFIG: dict = {}
if os.path.isfile(API_CONFIG_PATH):
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as _cf:
            _EXT_CONFIG = json.load(_cf)
        if _explicit_config_path:
            # An operator-bound registry is authoritative, including an empty
            # model mapping. Invalid shapes must not activate built-in models.
            if not isinstance(_EXT_CONFIG, dict) or not isinstance(_EXT_CONFIG.get("models"), dict):
                raise ValueError("invalid explicit model registry shape")
            if any(
                not isinstance(key, str) or not key.strip() or not isinstance(info, dict)
                for key, info in _EXT_CONFIG["models"].items()
            ):
                raise ValueError("invalid explicit model registry entry")
        print(
            "[code_assist_v1] api_config loaded "
            f"({len(_EXT_CONFIG.get('models', {}))} models): {API_CONFIG_PATH}"
        )
    except Exception as _e:
        if _explicit_config_path:
            raise RuntimeError(
                "The configured API model registry is invalid."
            ) from None
        print(f"[code_assist_v1] api_config load failed, using defaults: {_e}")
else:
    if _explicit_config_path:
        raise RuntimeError(
            "The configured API model registry is unavailable."
        )
    print(f"[code_assist_v1] api_config missing, using defaults: {API_CONFIG_PATH}")


def _first_env_value(env_names) -> str:
    if isinstance(env_names, str):
        env_names = [env_names]
    if not isinstance(env_names, list):
        return ""
    for name in env_names:
        value = os.getenv(str(name).strip(), "").strip()
        if value:
            return value
    return ""


def _build_model_registry(config_models: dict) -> dict:
    registry = {}
    for key, info in config_models.items():
        entry = dict(info)
        env_url = _first_env_value(entry.get("url_env"))
        if env_url:
            entry["url"] = env_url
        env_model = _first_env_value(entry.get("model_env"))
        if env_model:
            entry["model"] = env_model
        if "capabilities" in entry and isinstance(entry["capabilities"], list):
            entry["capabilities"] = set(entry["capabilities"])
        registry[key] = entry
    return registry


_DEFAULT_MODELS = {
    "gemma4-31b": {
        "env_id": "gemma4-31b",
        "model": "gemma-4-31B-it",
        "url": "http://common.llm.skhynix.com/v1/chat/completions",
        "name": "Gemma-4-31B (Common)",
        "capabilities": ["text", "analysis", "medium"],
        "context_window": 128000,
        "prriority": 2,
        "cost_tier": "medium"
        },
    }

MODEL_REGISTRY = (
    _build_model_registry(_EXT_CONFIG["models"])
    if _explicit_config_path or _EXT_CONFIG.get("models")
    else _DEFAULT_MODELS
)

_codex_bridge_enabled = os.getenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
if _codex_bridge_enabled and "codex-local" not in MODEL_REGISTRY:
    MODEL_REGISTRY["codex-local"] = {
        "env_id": "codex-local",
        "runtime": "codex",
        "model": "codex-cli",
        "name": "Codex Local Agent",
        "capabilities": ["code", "agent", "workspace", "second-brain"],
        "context_window": 128000,
        "priority": 0,
        "cost_tier": "local",
        "codex_cwd": os.getenv("CODE_ASSIST_CODEX_CWD", ""),
        "codex_sandbox": os.getenv("CODE_ASSIST_CODEX_SANDBOX", "workspace-write"),
        "codex_ephemeral": os.getenv("CODE_ASSIST_CODEX_EPHEMERAL", "1"),
        "codex_cli_model": os.getenv("CODE_ASSIST_CODEX_CLI_MODEL", ""),
    }

def _build_env_config(registry: dict) -> dict:
    env: dict = {}
    for mid, info in registry.items():
        env_id = info.get("env_id") or mid
        entry = {
            "env_id": env_id,
            "url": info.get("url", ""),
            "model": info.get("model", mid),
            "name": info.get("name", mid),
            "runtime": info.get("runtime", "api"),
            "_registry_id": mid,
        }
        for key in (
            "context_window",
            "cost_tier",
            "capabilities",
            "gguf",
            "priority",
            "api_key_env",
            "token_env",
            "auth_env",
            "api_key_file",
            "token_file",
            "auth_file",
            "api_key_source",
            "token_source",
            "auth_source",
            "codex_cwd",
            "codex_sandbox",
            "codex_ephemeral",
            "codex_cli_model",
        ):
            if key in info:
                entry[key] = info[key]
        env[env_id] = entry
    return env


ENV_CONFIG: dict = _build_env_config(MODEL_REGISTRY)
ENV_TO_REGISTRY: dict = {v["env_id"]: k for k, v in MODEL_REGISTRY.items()}

_token_cfg = _EXT_CONFIG.get("token_settings", {})
TOKEN_SETTINGS = {
    "agent_max_tokens": int(os.getenv("AGENT_MAX_TOKENS", str(_token_cfg.get("agent_max_tokens", 8192)))),
    "synth_max_tokens": int(os.getenv("SYNTH_MAX_TOKENS", str(_token_cfg.get("synth_max_tokens", 16384)))),
    "default_n_ctx": int(os.getenv("DEFAULT_N_CTX", str(_token_cfg.get("default_n_ctx", 32768)))),
    "gguf_reply_cap": int(os.getenv("GGUF_MAX_TOKENS_CAP", str(_token_cfg.get("gguf_reply_cap", 4096)))),
    "gguf_ctx_reserve": int(os.getenv("GGUF_CONTEXT_RESERVE", str(_token_cfg.get("gguf_ctx_reserve", 1536)))),
    "parallel_agent_max_tokens": int(
        os.getenv("PARALLEL_AGENT_MAX_TOKENS", str(_token_cfg.get("parallel_agent_max_tokens", 4096)))
    ),
}

_gguf_cfg = _EXT_CONFIG.get("gguf", {})
GGUF_MODEL_DIR_NAME = _gguf_cfg.get("model_dir", "MODEL_GGUF")
MAX_POOL_SIZE = int(os.getenv("GGUF_MAX_POOL_SIZE", str(_gguf_cfg.get("max_pool_size", 4))))
VRAM_BUDGET_GB = float(os.getenv("GGUF_VRAM_BUDGET_GB", str(_gguf_cfg.get("vram_budget_gb", 14))))
GGUF_DEFAULT_N_CTX = int(_gguf_cfg.get("n_ctx", 32768))
GGUF_DEFAULT_N_GPU_LAYERS = int(_gguf_cfg.get("n_gpu_layers", 99))
GGUF_DEFAULT_N_BATCH = int(_gguf_cfg.get("n_batch", 2048))
GGUF_DEFAULT_N_UBATCH = int(_gguf_cfg.get("n_ubatch", min(GGUF_DEFAULT_N_BATCH, 512)))
GGUF_DEFAULT_FLASH_ATTN = bool(_gguf_cfg.get("flash_attn", False))
GGUF_DEFAULT_OFFLOAD_KQV = bool(_gguf_cfg.get("offload_kqv", True))
GGUF_DEFAULT_MUL_MAT_Q = bool(_gguf_cfg.get("mul_mat_q", True))
GGUF_DEFAULT_LOGITS_ALL = bool(_gguf_cfg.get("logits_all", False))
GGUF_DEFAULT_EMBEDDING = bool(_gguf_cfg.get("embedding", False))
GGUF_DEFAULT_USE_MMAP = bool(_gguf_cfg.get("use_mmap", True))
GGUF_DEFAULT_USE_MLOCK = bool(_gguf_cfg.get("use_mlock", False))
GGUF_AUTO_REGISTER_UNCONFIGURED = bool(_gguf_cfg.get("auto_register_unconfigured", True))
_autoload_default = str(_gguf_cfg.get("autoload_on_startup", False))
GGUF_AUTOLOAD_ON_STARTUP = os.getenv("CODE_ASSIST_AUTOLOAD_GGUF", _autoload_default).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
GGUF_AUTOLOAD_MODEL_ID = os.getenv("CODE_ASSIST_AUTOLOAD_GGUF_MODEL", str(_gguf_cfg.get("autoload_model", ""))).strip()

_configured_default_priority = _EXT_CONFIG.get(
    "default_model_priority",
    ["gguf-gemma4-31b", "gguf-qwen36-35b", "gemma4-common", "qwen36-common", "glm-5-1"],
)
_default_model_override = os.getenv("CODE_ASSIST_DEFAULT_MODEL", "").strip()
if _default_model_override:
    DEFAULT_MODEL_PRIORITY = [_default_model_override] + [
        mid for mid in _configured_default_priority if mid != _default_model_override
    ]
else:
    DEFAULT_MODEL_PRIORITY = _configured_default_priority
