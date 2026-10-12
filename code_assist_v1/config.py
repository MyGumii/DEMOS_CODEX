"""
Runtime configuration for code_assist_v1.

Integrated mode keeps sharing root project resources. Standalone mode prefers
resources inside code_assist_v1/ while falling back to the root TOKEN.TXT when a
package-local token file is absent. API tokens can also be supplied from
environment variables for Codex/OpenAI-style sharing without copying secrets
into TOKEN.TXT.
"""
from __future__ import annotations

import os
import sys

from code_assist_v1.data_paths import resolve_runtime_data_paths

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    for _name in ("stdout", "stderr"):
        try:
            getattr(sys, _name).reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PKG_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

STANDALONE = os.environ.get("CODE_ASSIST_STANDALONE", "").lower() in {"1", "true", "yes", "on"}

_DATA_PATHS = resolve_runtime_data_paths(
    package_dir=PKG_DIR,
    repository_root=ROOT_DIR,
    standalone=STANDALONE,
)
DATA_ROOT = _DATA_PATHS.data_root
DATA_ROOT_OVERRIDE_ACTIVE = _DATA_PATHS.override_active
RUNTIME_DIR = _DATA_PATHS.runtime_dir


def _explicit_runtime_file(name: str, default: str, *, expected_name: str) -> str:
    """Resolve an operator-bound shared runtime file without a path fallback."""

    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    if not os.path.isabs(raw):
        raise RuntimeError(f"{name} must be an absolute path")
    resolved = os.path.realpath(os.path.abspath(raw))
    if os.path.basename(resolved) != expected_name:
        raise RuntimeError(f"{name} must target {expected_name}")
    if os.path.isdir(resolved):
        raise RuntimeError(f"{name} must target a file")
    return resolved

STATIC_DIR = os.path.join(PKG_DIR, "static")
SKILLS_DIR = os.path.join(PKG_DIR, "skills")
SESSIONS_DIR = _DATA_PATHS.sessions_dir
WORKSPACE_DIR = _DATA_PATHS.workspace_dir
USER_DATA_DIR = _DATA_PATHS.user_data_dir
AUTH_USERS_FILE = _explicit_runtime_file(
    "CODE_ASSIST_AUTH_USERS_FILE",
    os.path.join(RUNTIME_DIR, "users.json"),
    expected_name="users.json",
)
AUTH_SECRET_FILE = _explicit_runtime_file(
    "CODE_ASSIST_AUTH_SECRET_FILE",
    os.path.join(RUNTIME_DIR, "secret.key"),
    expected_name="secret.key",
)
SKILL_WHITELIST_JSON = os.path.join(PKG_DIR, "skill_whitelist.json")
MODEL_GGUF_DIR = os.path.join(PKG_DIR, "MODEL_GGUF")

KNOWLEDGE_DIR = _DATA_PATHS.knowledge_dir
_explicit_token_file = os.environ.get("CODE_ASSIST_TOKEN_FILE")
if _explicit_token_file:
    TOKEN_FILE = _explicit_token_file
elif DATA_ROOT_OVERRIDE_ACTIVE:
    # An isolated data root must not silently read a live repository token.
    TOKEN_FILE = os.path.join(RUNTIME_DIR, "TOKEN.TXT")
elif STANDALONE:
    _pkg_token_file = os.path.join(PKG_DIR, "TOKEN.TXT")
    _root_token_file = os.path.join(ROOT_DIR, "TOKEN.TXT")
    TOKEN_FILE = _pkg_token_file if os.path.isfile(_pkg_token_file) else _root_token_file
else:
    TOKEN_FILE = os.path.join(ROOT_DIR, "TOKEN.TXT")

for _d in (
    SKILLS_DIR,
    RUNTIME_DIR,
    KNOWLEDGE_DIR,
    SESSIONS_DIR,
    WORKSPACE_DIR,
    MODEL_GGUF_DIR,
    USER_DATA_DIR,
):
    os.makedirs(_d, exist_ok=True)


_DEFAULT_TOKEN_ENVS = (
    "CODE_ASSIST_API_KEY",
    "HYNIX_API_KEY",
    "HCP_API_KEY",
)

_TOKEN_SOURCE_ENVS = {
    "codex": (
        "CODE_ASSIST_CODEX_TOKEN",
        "CODEX_API_KEY",
        "CODEX_TOKEN",
        "CODEX_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ),
    "openai": (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODE_ASSIST_CODEX_TOKEN",
    ),
    "hynix": (
        "HYNIX_API_KEY",
        "HCP_API_KEY",
        "CODE_ASSIST_API_KEY",
    ),
}


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


def _clean_token(value: str | None) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        print("  API token contains non-ASCII characters")
        return ""
    return token


def _token_from_env(env_names) -> tuple[str, str]:
    for name in _as_list(env_names):
        env_name = name.strip()
        if not env_name:
            continue
        token = _clean_token(os.environ.get(env_name))
        if token:
            return token, f"env:{env_name}"
    return "", ""


def _token_from_file(path: str | None, label: str | None = None) -> tuple[str, str]:
    if not path or not os.path.isfile(path):
        return "", ""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            token = _clean_token(f.read())
    except Exception as e:
        print(f"  token file read failed: {e}")
        return "", ""
    return (token, label or f"file:{path}") if token else ("", "")


def load_token_with_source() -> tuple[str, str]:
    token, source = _token_from_env(_DEFAULT_TOKEN_ENVS)
    if token:
        return token, source
    token, source = _token_from_file(TOKEN_FILE, f"file:{TOKEN_FILE}")
    if token:
        return token, source
    return "", ""


def load_token() -> str:
    token, _source = load_token_with_source()
    return token


API_TOKEN, API_TOKEN_SOURCE = load_token_with_source()


def resolve_api_token(model_config: dict | None = None) -> tuple[str, str]:
    """Resolve the bearer token for a model request without exposing its value."""
    cfg = model_config if isinstance(model_config, dict) else {}

    env_names = cfg.get("api_key_env") or cfg.get("token_env") or cfg.get("auth_env")
    if env_names:
        token, source = _token_from_env(env_names)
        return token, source or "missing:env"

    file_path = cfg.get("api_key_file") or cfg.get("token_file") or cfg.get("auth_file")
    if file_path:
        token, source = _token_from_file(str(file_path), f"file:{file_path}")
        return token, source or "missing:file"

    source_name = str(
        cfg.get("api_key_source") or cfg.get("token_source") or cfg.get("auth_source") or ""
    ).strip().lower()
    if source_name:
        if source_name in {"default", "token", "token_txt", "token.txt", "file"}:
            return API_TOKEN, API_TOKEN_SOURCE
        env_names = _TOKEN_SOURCE_ENVS.get(source_name)
        if env_names:
            token, source = _token_from_env(env_names)
            return token, source or f"missing:{source_name}"
        return "", f"unknown:{source_name}"

    return API_TOKEN, API_TOKEN_SOURCE


def get_api_token(model_config: dict | None = None) -> str:
    token, _source = resolve_api_token(model_config)
    return token

from code_assist_v1.models import (  # noqa: E402
    DEFAULT_MODEL_PRIORITY,
    ENV_CONFIG,
    ENV_TO_REGISTRY,
    GGUF_DEFAULT_N_BATCH,
    GGUF_DEFAULT_N_CTX,
    GGUF_DEFAULT_N_GPU_LAYERS,
    MAX_POOL_SIZE,
    MODEL_REGISTRY,
    TOKEN_SETTINGS,
    VRAM_BUDGET_GB,
)

PORT = int(os.environ.get("CODE_ASSIST_PORT", "10011"))

DEFAULT_N_CTX = TOKEN_SETTINGS.get("default_n_ctx", 32768)
MAX_AGENT_TOKENS = TOKEN_SETTINGS.get("agent_max_tokens", 8192)
MAX_HISTORY_TURNS = 12
MAX_WORKSPACE_FILE_CHARS = 4000
MAX_WORKSPACE_TOTAL_CHARS = 16000
MAX_KNOWLEDGE_INJECT_CHARS = 12000

MAX_UPLOAD_MB = 50
ALLOWED_UPLOAD_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".html", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".md", ".txt", ".csv", ".tsv",
    ".java", ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".sql", ".dockerfile",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ipynb",
}


def get_default_model() -> str:
    for mid in DEFAULT_MODEL_PRIORITY:
        if mid in MODEL_REGISTRY:
            return mid
    if MODEL_REGISTRY:
        return next(iter(MODEL_REGISTRY.keys()))
    return ""
