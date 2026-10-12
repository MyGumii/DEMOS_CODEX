"""
Cookie-session authentication and per-user storage helpers for code_assist_v1.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from functools import wraps
from typing import Any
from uuid import uuid4

from flask import g, jsonify, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from code_assist_v1.config import AUTH_SECRET_FILE, AUTH_USERS_FILE, KNOWLEDGE_DIR, USER_DATA_DIR
from code_assist_v1.knowledge_core.auth_security import (
    BrowserSecurityConfigurationError,
    BrowserSecurityPolicy,
    CSRF_HEADER_NAME,
    CSRF_SESSION_KEY,
    RequestSecurityFacts,
    evaluate_request_security,
    issue_csrf_token,
    request_host_denial_reason,
    require_session_cookie_config,
)


_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_FAILED_LOGINS: dict[str, list[float]] = {}
AUTH_AUDIT_FILE = os.path.join(os.path.dirname(AUTH_USERS_FILE), "audit.jsonl")
DEFAULT_INITIAL_PASSWORD = "user1234"
CREDENTIAL_VERIFY_ATTEMPTS = 3


class UserRegistryUnavailableError(RuntimeError):
    """The legacy registry exists but cannot be trusted as account state."""

    code = "USER_REGISTRY_UNAVAILABLE"

_BROWSER_SECURITY_EXTENSION_KEY = "code_assist_browser_security"
_BROWSER_SECURITY_PRODUCTION_KEY = "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION"
_ALLOWED_ORIGINS_KEY = "CODE_ASSIST_ALLOWED_ORIGINS"
_ACCOUNT_MUTATION_AUTHORITY_ORIGIN_KEY = "CODE_ASSIST_ACCOUNT_MUTATION_AUTHORITY_ORIGIN"
_ACCOUNT_MUTATION_EXACT_PATHS = frozenset(
    {
        "/api/code/auth/register",
        "/api/code/auth/login",
        "/api/code/auth/password",
        "/api/code/auth/users",
    }
)
_DEVELOPMENT_ALLOWED_ORIGINS = frozenset(
    {
        "http://localhost",
        "http://localhost:10011",
        "http://localhost:10012",
        "http://127.0.0.1",
        "http://127.0.0.1:10011",
        "http://127.0.0.1:10012",
        "http://[::1]",
        "http://[::1]:10011",
        "http://[::1]:10012",
    }
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_PUBLIC_ANONYMOUS_FORM_RE = re.compile(
    r"^/public/notion/[^/]+/forms/[^/]+$",
    re.ASCII,
)
_ADMIN_API_PREFIX = "/api/admin/v1"
_ADMIN_REQUEST_ID_ATTRIBUTE = "code_assist_admin_request_id"


def safe_user_id(value: str | None) -> str:
    user_id = (value or "").strip().lower()
    if not _USER_ID_RE.match(user_id):
        return ""
    return user_id


def user_data_dir(user_id: str | None, *parts: str) -> str:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("authenticated user required")
    base = os.path.join(USER_DATA_DIR, uid)
    path = os.path.join(base, *parts) if parts else base
    os.makedirs(path if not parts else os.path.dirname(path), exist_ok=True)
    return path


def scoped_dir(name: str, user_id: str | None = None) -> str:
    uid = safe_user_id(user_id or current_user_id())
    if not uid:
        raise ValueError("authenticated user required")
    path = os.path.join(USER_DATA_DIR, uid, name)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_secret_key() -> str:
    parent = os.path.dirname(AUTH_SECRET_FILE)
    os.makedirs(parent, exist_ok=True)

    def read_existing() -> str:
        try:
            with open(AUTH_SECRET_FILE, "r", encoding="utf-8") as existing:
                value = existing.read().strip()
        except OSError as exc:
            raise RuntimeError("authentication secret is unreadable") from exc
        if len(value.encode("utf-8")) < 32:
            raise RuntimeError("authentication secret is missing or too short")
        return value

    if os.path.exists(AUTH_SECRET_FILE):
        return read_existing()

    # Both Flask hosts may perform first bootstrap concurrently.  Publish a
    # fully flushed same-volume file through an atomic hard-link create: one
    # process wins, every loser reads that exact winner, and no process can
    # observe or overwrite a partially written/independent secret.
    candidate = secrets.token_hex(32)
    temporary = os.path.join(
        parent,
        f".{os.path.basename(AUTH_SECRET_FILE)}.{os.getpid()}."
        f"{secrets.token_hex(8)}.tmp",
    )
    try:
        with open(temporary, "x", encoding="utf-8", newline="\n") as output:
            output.write(candidate + "\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, AUTH_SECRET_FILE)
        except FileExistsError:
            return read_existing()
        return read_existing()
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def _load_users() -> dict[str, Any]:
    if not os.path.isfile(AUTH_USERS_FILE):
        return {"users": {}}
    try:
        with open(AUTH_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UserRegistryUnavailableError(
            "the account registry is unavailable; administrator recovery is required"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        raise UserRegistryUnavailableError(
            "the account registry is invalid; administrator recovery is required"
        )
    return data


def _save_users(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(AUTH_USERS_FILE), exist_ok=True)
    tmp = AUTH_USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_USERS_FILE)


def _credential_matches(password_hash: Any, password: Any) -> bool:
    """Retry a transient KDF false negative without accepting a mismatch."""

    if not isinstance(password_hash, str) or not isinstance(password, str):
        return False
    for _ in range(CREDENTIAL_VERIFY_ATTEMPTS):
        try:
            if check_password_hash(password_hash, password):
                return True
        except (TypeError, ValueError):
            return False
    return False


def _now() -> int:
    return int(time.time())


def user_count() -> int:
    return len(_load_users().get("users") or {})


def _effective_role(uid: str, user: dict[str, Any], users: dict[str, Any]) -> str:
    role = (user.get("role") or "").strip().lower()
    if role in {"admin", "user"}:
        return role
    # Backward compatibility: before roles existed, the first account was the
    # owner account. Treat it as admin until roles are written explicitly.
    has_admin = any((u.get("role") or "").strip().lower() == "admin" for u in users.values())
    first_uid = next(iter(users.keys()), "")
    if not has_admin and uid == first_uid:
        return "admin"
    return "user"


def _active_admin_ids(users: dict[str, Any]) -> list[str]:
    return [
        uid
        for uid, user in users.items()
        if not user.get("disabled") and _effective_role(uid, user, users) == "admin"
    ]


def _active_admin_ids_after(
    users: dict[str, Any],
    target_uid: str,
    updates: dict[str, Any] | None = None,
    *,
    deleting: bool = False,
) -> list[str]:
    simulated = {
        uid: dict(user)
        for uid, user in users.items()
        if not (deleting and uid == target_uid)
    }
    if target_uid in simulated and updates:
        simulated[target_uid].update(updates)
    return _active_admin_ids(simulated)


def _ensure_active_admin_remains(
    users: dict[str, Any],
    target_uid: str,
    updates: dict[str, Any] | None = None,
    *,
    deleting: bool = False,
) -> None:
    if not _active_admin_ids_after(users, target_uid, updates, deleting=deleting):
        raise ValueError("at least one active admin account is required")


def _within_root(root: str, path: str) -> bool:
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(path)
    return path_abs == root_abs or path_abs.startswith(root_abs + os.sep)


def _count_files(root: str, suffix: str | None = None, *, recursive: bool = True) -> dict[str, int]:
    total = {"files": 0, "bytes": 0}
    if not os.path.isdir(root):
        return total
    walker = os.walk(root) if recursive else [(root, [], os.listdir(root))]
    for dirpath, _, filenames in walker:
        for name in filenames:
            if suffix and not name.endswith(suffix):
                continue
            path = os.path.join(dirpath, name)
            try:
                total["files"] += 1
                total["bytes"] += os.path.getsize(path)
            except OSError:
                continue
    return total


def user_data_summary(user_id: str | None) -> dict[str, Any]:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("valid user id required")
    user_root = os.path.join(USER_DATA_DIR, uid)
    knowledge_root = os.path.join(KNOWLEDGE_DIR, uid)
    skill_root = os.path.join(user_root, "skills")
    skill_count = 0
    if os.path.isdir(skill_root):
        for name in os.listdir(skill_root):
            if os.path.isfile(os.path.join(skill_root, name, "SKILL.md")):
                skill_count += 1

    sessions = _count_files(os.path.join(user_root, "sessions"), ".json", recursive=False)
    workspace = _count_files(os.path.join(user_root, "workspace"), recursive=True)
    project_context = _count_files(os.path.join(user_root, "projects"), recursive=True)
    memory = _count_files(os.path.join(user_root, "memory"), ".jsonl", recursive=False)
    user_data = _count_files(user_root, recursive=True)
    knowledge = _count_files(knowledge_root, recursive=True)
    docs = _count_files(knowledge_root, ".md", recursive=False)
    thoughts = _count_files(os.path.join(knowledge_root, "thoughts"), ".md", recursive=False)
    inbox = _count_files(os.path.join(knowledge_root, "inbox"), ".json", recursive=False)
    raw = _count_files(os.path.join(knowledge_root, "raw"), ".md", recursive=False)

    return {
        "user_id": uid,
        "exists": {
            "user_data": os.path.isdir(user_root),
            "knowledge": os.path.isdir(knowledge_root),
        },
        "sessions": sessions["files"],
        "workspace_files": workspace["files"],
        "workspace_bytes": workspace["bytes"],
        "project_context_files": project_context["files"],
        "project_context_bytes": project_context["bytes"],
        "memory_files": memory["files"],
        "memory_bytes": memory["bytes"],
        "user_skills": skill_count,
        "knowledge_docs": docs["files"],
        "thoughts": thoughts["files"],
        "inbox_items": inbox["files"],
        "raw_chunks": raw["files"],
        "total_files": user_data["files"] + knowledge["files"],
        "total_bytes": user_data["bytes"] + knowledge["bytes"],
    }


def _copy_tree_contents(src: str, dst: str) -> dict[str, Any]:
    result: dict[str, Any] = {"files": 0, "bytes": 0, "errors": []}
    if not os.path.isdir(src):
        return result
    os.makedirs(dst, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".DS_Store")]
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            source_file = os.path.join(dirpath, name)
            target_file = os.path.join(target_dir, name)
            try:
                shutil.copy2(source_file, target_file)
                result["files"] += 1
                result["bytes"] += os.path.getsize(target_file)
            except Exception as e:
                result["errors"].append(f"{os.path.relpath(source_file, src)}: {e}")
    return result


def transfer_user_data(source_user_id: str, target_user_id: str, include: list[str] | None = None) -> dict[str, Any]:
    source_uid = safe_user_id(source_user_id)
    target_uid = safe_user_id(target_user_id)
    if not source_uid or not target_uid:
        raise ValueError("valid source and target user ids are required")
    if source_uid == target_uid:
        raise ValueError("source and target users must be different")

    requested = set(include or ["sessions", "workspace", "skills", "knowledge", "memory", "project_context"])
    allowed = {"sessions", "workspace", "skills", "knowledge", "memory", "project_context"}
    requested &= allowed
    if not requested:
        raise ValueError("at least one data section is required")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_workspace = os.path.join(USER_DATA_DIR, target_uid, "workspace")
    import_root = os.path.join(target_workspace, "admin_imports", f"from-{source_uid}-{stamp}")
    if not _within_root(target_workspace, import_root):
        raise ValueError("invalid transfer target")
    os.makedirs(import_root, exist_ok=True)

    sections = {
        "sessions": os.path.join(USER_DATA_DIR, source_uid, "sessions"),
        "workspace": os.path.join(USER_DATA_DIR, source_uid, "workspace"),
        "project_context": os.path.join(USER_DATA_DIR, source_uid, "projects"),
        "skills": os.path.join(USER_DATA_DIR, source_uid, "skills"),
        "memory": os.path.join(USER_DATA_DIR, source_uid, "memory"),
        "knowledge": os.path.join(KNOWLEDGE_DIR, source_uid),
    }
    copied: dict[str, Any] = {}
    for section, source_path in sections.items():
        if section not in requested:
            continue
        copied[section] = _copy_tree_contents(source_path, os.path.join(import_root, section))

    manifest = {
        "source_user_id": source_uid,
        "target_user_id": target_uid,
        "created_at": _now(),
        "sections": sorted(requested),
        "copied": copied,
        "note": "Admin transfer copy. Source data is preserved; imported data is visible in the target workspace.",
    }
    with open(os.path.join(import_root, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {
        "source_user_id": source_uid,
        "target_user_id": target_uid,
        "import_path": import_root,
        "sections": copied,
    }


def delete_user_data(user_id: str) -> dict[str, Any]:
    del user_id
    # AUTH-003 permanently removes the destructive runtime primitive.  Future
    # retention/purge work must use the canonical, audited, reference-aware
    # administrator workflow; reusing this legacy helper can never erase an
    # account directory or knowledge tree.
    raise PermissionError(
        "account data hard deletion is forbidden; use canonical retention workflows"
    )


def append_audit(action: str, target_id: str = "", details: dict[str, Any] | None = None, status: str = "ok") -> None:
    try:
        os.makedirs(os.path.dirname(AUTH_AUDIT_FILE), exist_ok=True)
        entry = {
            "ts": _now(),
            "actor_id": current_user_id() or "system",
            "action": action,
            "target_id": safe_user_id(target_id) or target_id,
            "status": status,
            "details": details or {},
            "remote_addr": request.remote_addr if request else "",
        }
        with open(AUTH_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit or 100)))
    if not os.path.isfile(AUTH_AUDIT_FILE):
        return []
    try:
        with open(AUTH_AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return out


def is_admin_user(user_id: str | None) -> bool:
    uid = safe_user_id(user_id)
    if not uid:
        return False
    users = _load_users().get("users") or {}
    user = users.get(uid)
    if not user:
        return False
    return _effective_role(uid, user, users) == "admin"


def list_public_users(include_data: bool = False) -> list[dict[str, Any]]:
    users = _load_users().get("users") or {}
    items = []
    for uid, user in sorted(users.items()):
        item = public_user(user, users)
        if include_data:
            try:
                item["data_summary"] = user_data_summary(uid)
            except Exception:
                item["data_summary"] = {}
        items.append(item)
    return items


def create_user(
    user_id: str,
    password: str,
    display_name: str = "",
    role: str | None = None,
    must_change_password: bool = False,
) -> dict[str, Any]:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("계정 ID는 영문/숫자로 시작하고 영문, 숫자, '.', '_', '-'만 사용할 수 있습니다.")
    if len(password or "") < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    data = _load_users()
    users = data.setdefault("users", {})
    if uid in users:
        raise FileExistsError(uid)
    now = int(time.time())
    normalized_role = (role or "").strip().lower()
    if normalized_role not in {"admin", "user"}:
        normalized_role = "admin" if not users else "user"
    must_change = bool(must_change_password or password == DEFAULT_INITIAL_PASSWORD)
    users[uid] = {
        "id": uid,
        "display_name": display_name.strip() or uid,
        "role": normalized_role,
        "disabled": False,
        "password_hash": generate_password_hash(password),
        "must_change_password": must_change,
        "password_changed_at": None if must_change else now,
        "created_at": now,
        "updated_at": now,
    }
    _save_users(data)
    for sub in ("sessions", "workspace", "skills"):
        scoped_dir(sub, uid)
    return public_user(users[uid], users)


def verify_user(user_id: str, password: str) -> dict[str, Any] | None:
    uid = safe_user_id(user_id)
    if not uid:
        return None
    store = _load_users()
    users = store.get("users") or {}
    user = users.get(uid)
    if not user:
        return None
    if user.get("disabled"):
        return None
    if not _credential_matches(user.get("password_hash") or "", password or ""):
        return None
    if (
        not user.get("password_changed_at")
        and not user.get("must_change_password")
        and _credential_matches(
            user.get("password_hash") or "", DEFAULT_INITIAL_PASSWORD
        )
    ):
        user["must_change_password"] = True
        user["updated_at"] = _now()
        _save_users(store)
    return public_user(user, users)


def change_own_password(user_id: str, current_password: str, new_password: str) -> dict[str, Any]:
    uid = safe_user_id(user_id)
    if not uid:
        raise ValueError("valid user id required")
    if len(new_password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    store = _load_users()
    users = store.setdefault("users", {})
    user = users.get(uid)
    if not user or user.get("disabled"):
        raise LookupError("account not found")
    current_hash = user.get("password_hash") or ""
    if not _credential_matches(current_hash, current_password or ""):
        raise PermissionError("current password is incorrect")
    if _credential_matches(current_hash, new_password or ""):
        raise ValueError("new password must be different from the current password")
    now = _now()
    user["password_hash"] = generate_password_hash(new_password)
    user["must_change_password"] = False
    user["password_changed_at"] = now
    user["updated_at"] = now
    _save_users(store)
    return public_user(user, users)


def public_user(user: dict[str, Any], users: dict[str, Any] | None = None) -> dict[str, Any]:
    uid = safe_user_id(user.get("id") or "")
    all_users = users if users is not None else (_load_users().get("users") or {})
    role = _effective_role(uid, user, all_users) if uid else "user"
    return {
        "id": uid,
        "display_name": user.get("display_name") or user.get("id") or "",
        "role": role,
        "is_admin": role == "admin",
        "disabled": bool(user.get("disabled")),
        "must_change_password": bool(user.get("must_change_password")),
        "password_changed_at": user.get("password_changed_at"),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "disabled_at": user.get("disabled_at"),
    }


def login_user(user: dict[str, Any]) -> str:
    session.clear()
    session["code_assist_user"] = {
        "id": safe_user_id(user.get("id")),
        "display_name": user.get("display_name") or user.get("id"),
        "role": user.get("role") or "user",
        "is_admin": bool(user.get("is_admin")),
        "must_change_password": bool(user.get("must_change_password")),
        "login_at": int(time.time()),
    }
    session.permanent = True
    return issue_csrf_token(session)


def logout_user() -> None:
    session.clear()


def current_user() -> dict[str, Any] | None:
    user = session.get("code_assist_user")
    if not isinstance(user, dict):
        return None
    uid = safe_user_id(user.get("id"))
    if not uid:
        return None
    users = _load_users().get("users") or {}
    stored = users.get(uid)
    if stored:
        if stored.get("disabled"):
            return None
        return public_user(stored, users)
    # A signed cookie is an account-session pointer, not a self-contained user
    # registry.  A removed or not-yet-imported account must fail closed; legacy
    # migration preserves the actual account before cookie-session cutover.
    return None


def current_user_id() -> str | None:
    user = current_user()
    return user["id"] if user else None


def login_limited(user_id: str) -> bool:
    key = f"{request.remote_addr or 'local'}:{safe_user_id(user_id) or '-'}"
    now = time.time()
    recent = [t for t in _FAILED_LOGINS.get(key, []) if now - t < 300]
    _FAILED_LOGINS[key] = recent
    return len(recent) >= 8


def record_failed_login(user_id: str) -> None:
    key = f"{request.remote_addr or 'local'}:{safe_user_id(user_id) or '-'}"
    _FAILED_LOGINS.setdefault(key, []).append(time.time())


def _strict_browser_security_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise BrowserSecurityConfigurationError(
        f"{_BROWSER_SECURITY_PRODUCTION_KEY} must be an explicit boolean"
    )


def _configured_allowed_origins(app, *, production: bool) -> frozenset[str]:
    configured: object = app.config.get(_ALLOWED_ORIGINS_KEY)
    if configured is None:
        configured = os.environ.get(_ALLOWED_ORIGINS_KEY)

    if configured is None or configured == "":
        if production:
            raise BrowserSecurityConfigurationError(
                f"{_ALLOWED_ORIGINS_KEY} is required in production"
            )
        return _DEVELOPMENT_ALLOWED_ORIGINS

    if isinstance(configured, str):
        origins = tuple(part.strip() for part in configured.split(","))
        if not origins or any(not part for part in origins):
            raise BrowserSecurityConfigurationError(
                f"{_ALLOWED_ORIGINS_KEY} contains an empty origin"
            )
        return frozenset(origins)
    if isinstance(configured, (list, tuple, set, frozenset)):
        return frozenset(configured)
    raise BrowserSecurityConfigurationError(
        f"{_ALLOWED_ORIGINS_KEY} must be a string or collection of origins"
    )


def _configure_browser_security(app) -> BrowserSecurityPolicy:
    configured_production: object = app.config.get(_BROWSER_SECURITY_PRODUCTION_KEY)
    if configured_production is None:
        configured_production = os.environ.get(_BROWSER_SECURITY_PRODUCTION_KEY)
    production = _strict_browser_security_bool(configured_production)
    policy = BrowserSecurityPolicy(
        allowed_origins=_configured_allowed_origins(app, production=production),
        production=production,
    )
    if production:
        require_session_cookie_config(app.config, production=True)
    app.extensions[_BROWSER_SECURITY_EXTENSION_KEY] = policy
    return policy


def _account_mutation_authority_origin(app) -> str | None:
    configured = app.config.get(_ACCOUNT_MUTATION_AUTHORITY_ORIGIN_KEY)
    if configured is None:
        configured = os.environ.get(_ACCOUNT_MUTATION_AUTHORITY_ORIGIN_KEY)
    if configured in (None, ""):
        return None
    value = str(configured).strip()
    if value not in _configured_allowed_origins(app, production=False):
        raise BrowserSecurityConfigurationError(
            f"{_ACCOUNT_MUTATION_AUTHORITY_ORIGIN_KEY} must be an allowed exact origin"
        )
    return value


def _is_account_mutation_request(path: str, method: str) -> bool:
    if method not in {"POST", "PATCH", "DELETE"}:
        return False
    if path in _ACCOUNT_MUTATION_EXACT_PATHS:
        return True
    return path.startswith("/api/code/auth/users/")


def _is_sse_initiation(path: str) -> bool:
    if path.rstrip("/").endswith("/stream"):
        return True
    accept = request.headers.get("Accept") or ""
    return any(
        media_type.split(";", 1)[0].strip().casefold() == "text/event-stream"
        for media_type in accept.split(",")
    )


def _admin_api_request_id() -> str:
    existing = getattr(g, _ADMIN_REQUEST_ID_ATTRIBUTE, None)
    if isinstance(existing, str) and 1 <= len(existing) <= 128:
        return existing
    request_id = str(uuid4())
    setattr(g, _ADMIN_REQUEST_ID_ATTRIBUTE, request_id)
    return request_id


def _admin_api_error(
    *,
    code: str,
    message: str,
    status: int,
    retryable: bool = False,
    details: dict[str, object] | None = None,
):
    request_id = _admin_api_request_id()
    response = jsonify(
        {
            "error": {
                "code": code,
                "details": dict(details or {}),
                "message": message,
                "retryable": retryable,
            },
            "request_id": request_id,
        }
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Request-ID"] = request_id
    return response, status


def _request_security_denied(reason: object):
    reason_value = getattr(reason, "value", "request_security_rejected")
    if (request.path or "").startswith(_ADMIN_API_PREFIX):
        return _admin_api_error(
            code="FORBIDDEN",
            message="Request security validation failed.",
            status=403,
            details={"reason": reason_value},
        )
    response = jsonify({
        "error": "request security validation failed",
        "code": "FORBIDDEN",
        "reason": reason_value,
    })
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, 403


def _wants_json() -> bool:
    return request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or "")


def require_auth_response():
    if _wants_json():
        if (request.path or "").startswith(_ADMIN_API_PREFIX):
            return _admin_api_error(
                code="AUTHENTICATION_REQUIRED",
                message="A valid canonical account session is required.",
                status=401,
            )
        return jsonify({"error": "authentication required"}), 401
    return redirect("/login", code=302)


def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return require_auth_response()
        return fn(*args, **kwargs)

    return wrapped


def require_admin_response():
    user = current_user()
    if not user:
        return require_auth_response()
    if not user.get("is_admin"):
        return jsonify({"error": "admin permission required"}), 403
    return None


def register_auth_routes(app):
    browser_security = _configure_browser_security(app)
    account_mutation_authority = _account_mutation_authority_origin(app)

    @app.before_request
    def _auth_gate():
        path = request.path or "/"
        if path.startswith(_ADMIN_API_PREFIX):
            _admin_api_request_id()
        host_denial = request_host_denial_reason(
            scheme=request.scheme,
            host=request.host,
            policy=browser_security,
        )
        if host_denial is not None:
            return _request_security_denied(host_denial)

        # Machine grants are accepted only by exact integration endpoints.
        # This never changes cookie identity or browser CSRF requirements.
        if request.headers.get("Authorization") and app.extensions.get("agent_integrations"):
            from code_assist_v1.agent_integrations.routes import bearer_auth_gate
            handled, response = bearer_auth_gate(app)
            if handled:
                return response

        if account_mutation_authority and _is_account_mutation_request(path, request.method):
            request_origin = request.host_url.rstrip("/")
            submitted_origin = (request.headers.get("Origin") or "").rstrip("/")
            if request_origin != account_mutation_authority or submitted_origin != account_mutation_authority:
                response = jsonify({
                    "error": "account mutation requires the Agent identity authority",
                    "code": "ACCOUNT_MUTATION_HOST_REQUIRED",
                })
                response.headers["Cache-Control"] = "no-store, max-age=0"
                return response, 403

        public_anonymous_form = bool(
            request.method == "POST" and _PUBLIC_ANONYMOUS_FORM_RE.fullmatch(path)
        )
        user = None if public_anonymous_form else current_user()
        facts = RequestSecurityFacts(
            method=request.method,
            path=path,
            scheme=request.scheme,
            host=request.host,
            origin=request.headers.get("Origin"),
            submitted_csrf_token=request.headers.get(CSRF_HEADER_NAME),
            authenticated_cookie_session=bool(user),
            is_sse_initiation=_is_sse_initiation(path),
        )
        decision = evaluate_request_security(
            facts,
            policy=browser_security,
            expected_csrf_token=session.get(CSRF_SESSION_KEY),
        )
        if not decision.allowed:
            return _request_security_denied(decision.denial_reason)

        if path in {
            "/login",
            "/favicon.ico",
            "/healthz",
            "/api/code/auth/login",
            "/api/code/auth/register",
            "/api/code/auth/setup_required",
            "/api/code/auth/me",
        }:
            return None
        if path.startswith("/static/"):
            return None
        if path.startswith("/public/notion/") and request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if public_anonymous_form:
            return None
        if user:
            if user.get("must_change_password") and path not in {
                "/",
                "/api/code/auth/me",
                "/api/code/auth/logout",
                "/api/code/auth/password",
            }:
                if _wants_json():
                    if path.startswith(_ADMIN_API_PREFIX):
                        return _admin_api_error(
                            code="FORBIDDEN",
                            message="A password change is required before administration.",
                            status=403,
                            details={"must_change_password": True},
                        )
                    return jsonify({
                        "error": "password change required",
                        "must_change_password": True,
                    }), 403
                return redirect("/", code=302)
            return None
        return require_auth_response()

    @app.errorhandler(UserRegistryUnavailableError)
    def _user_registry_unavailable(_error):
        if (request.path or "").startswith(_ADMIN_API_PREFIX):
            return _admin_api_error(
                code="INTERNAL_ERROR",
                message="The administrator account registry is unavailable.",
                status=500,
                retryable=True,
                details={"state": "account_registry_unavailable"},
            )
        response = jsonify({
            "error": "account registry unavailable",
            "code": UserRegistryUnavailableError.code,
        })
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response, 503

    @app.after_request
    def _auth_response_no_store(response):
        if (request.path or "").startswith("/api/code/auth/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.route("/api/code/auth/setup_required")
    def api_auth_setup_required():
        return jsonify({"setup_required": user_count() == 0})

    @app.route("/api/code/auth/me")
    def api_auth_me():
        user = current_user()
        payload = {
            "authenticated": bool(user),
            "user": user,
            "setup_required": user_count() == 0,
            "allow_signup": os.environ.get("CODE_ASSIST_ALLOW_SIGNUP", "0") == "1",
        }
        if user:
            payload["csrf_token"] = issue_csrf_token(session)
        return jsonify(payload)

    @app.route("/api/code/auth/register", methods=["POST"])
    def api_auth_register():
        if user_count() > 0 and os.environ.get("CODE_ASSIST_ALLOW_SIGNUP", "0") != "1":
            return jsonify({"error": "signup is disabled after the first account is created"}), 403
        data = request.get_json(force=True, silent=True) or {}
        try:
            user = create_user(
                data.get("user_id") or data.get("id") or "",
                data.get("password") or "",
                data.get("display_name") or "",
                role="admin" if user_count() == 0 else "user",
                must_change_password=False,
            )
            csrf_token = login_user(user)
            append_audit("auth.register", user.get("id"), {"role": user.get("role")})
        except FileExistsError:
            return jsonify({"error": "account already exists"}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "registered", "user": user, "csrf_token": csrf_token})

    @app.route("/api/code/auth/login", methods=["POST"])
    def api_auth_login():
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user_id") or data.get("id") or ""
        if login_limited(user_id):
            return jsonify({"error": "too many failed attempts; wait a few minutes"}), 429
        user = verify_user(user_id, data.get("password") or "")
        if not user:
            record_failed_login(user_id)
            return jsonify({"error": "invalid account id or password"}), 401
        csrf_token = login_user(user)
        return jsonify({"status": "logged_in", "user": user, "csrf_token": csrf_token})

    @app.route("/api/code/auth/logout", methods=["POST"])
    def api_auth_logout():
        logout_user()
        return jsonify({"status": "logged_out"})

    @app.route("/api/code/auth/password", methods=["POST"])
    def api_auth_password_change():
        user = current_user()
        if not user:
            return require_auth_response()
        data = request.get_json(force=True, silent=True) or {}
        current_password = data.get("current_password") or data.get("old_password") or ""
        new_password = data.get("new_password") or data.get("password") or ""
        try:
            updated_user = change_own_password(user.get("id") or "", current_password, new_password)
        except LookupError:
            return jsonify({"error": "account not found"}), 404
        except PermissionError as e:
            return jsonify({"error": str(e)}), 401
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        csrf_token = login_user(updated_user)
        append_audit("auth.password_change", updated_user.get("id"), {})
        return jsonify({
            "status": "password_changed",
            "user": updated_user,
            "csrf_token": csrf_token,
        })

    @app.route("/api/code/auth/users", methods=["GET"])
    def api_auth_users_list():
        denied = require_admin_response()
        if denied:
            return denied
        return jsonify({"items": list_public_users(include_data=True), "total": user_count()})

    @app.route("/api/code/auth/users", methods=["POST"])
    def api_auth_users_create():
        denied = require_admin_response()
        if denied:
            return denied
        data = request.get_json(force=True, silent=True) or {}
        requested_role = (data.get("role") or "user").strip().lower()
        role = "admin" if requested_role == "admin" else "user"
        try:
            user = create_user(
                data.get("user_id") or data.get("id") or "",
                data.get("password") or "",
                data.get("display_name") or "",
                role=role,
                must_change_password=True,
            )
            append_audit("user.create", user.get("id"), {"role": role})
        except FileExistsError:
            return jsonify({"error": "account already exists"}), 409
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"status": "created", "user": user})

    @app.route("/api/code/auth/users/<path:user_id>", methods=["PATCH"])
    def api_auth_users_update(user_id):
        denied = require_admin_response()
        if denied:
            return denied
        uid = safe_user_id(user_id)
        data = request.get_json(force=True, silent=True) or {}
        store = _load_users()
        users = store.setdefault("users", {})
        user = users.get(uid)
        if not uid or not user:
            return jsonify({"error": "account not found"}), 404

        updates: dict[str, Any] = {}
        if "display_name" in data:
            display_name = str(data.get("display_name") or "").strip()
            updates["display_name"] = display_name or uid
        if "role" in data:
            role = str(data.get("role") or "").strip().lower()
            if role not in {"admin", "user"}:
                return jsonify({"error": "role must be admin or user"}), 400
            updates["role"] = role
        if "disabled" in data:
            # The JSON-era mutation cannot atomically advance canonical
            # scope_epoch, revoke sessions/leases/jobs, close membership, or
            # replace a part leader.  Keep it fail-closed until the versioned
            # P1.5 admin command is wired after migration; silently applying
            # this field would leave stale authority alive.
            return jsonify({
                "error": "account status changes require the canonical administration API",
                "code": "CANONICAL_ACCOUNT_LIFECYCLE_REQUIRED",
            }), 409
        if not updates:
            return jsonify({"error": "no supported fields to update"}), 400

        try:
            _ensure_active_admin_remains(users, uid, updates)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        updates["updated_at"] = _now()
        user.update(updates)
        _save_users(store)
        append_audit("user.update", uid, {k: v for k, v in updates.items() if k != "password_hash"})
        return jsonify({"status": "updated", "user": public_user(user, users)})

    @app.route("/api/code/auth/users/<path:user_id>/password", methods=["POST"])
    def api_auth_users_password(user_id):
        denied = require_admin_response()
        if denied:
            return denied
        uid = safe_user_id(user_id)
        data = request.get_json(force=True, silent=True) or {}
        password = data.get("password") or ""
        if len(password) < 8:
            return jsonify({"error": "password must be at least 8 characters"}), 400
        store = _load_users()
        users = store.setdefault("users", {})
        user = users.get(uid)
        if not uid or not user:
            return jsonify({"error": "account not found"}), 404
        user["password_hash"] = generate_password_hash(password)
        user["must_change_password"] = True
        user["password_changed_at"] = None
        user["updated_at"] = _now()
        _save_users(store)
        append_audit("user.password_reset", uid, {})
        return jsonify({"status": "password_reset", "user": public_user(user, users)})

    @app.route("/api/code/auth/users/<path:user_id>/data", methods=["GET"])
    def api_auth_users_data(user_id):
        denied = require_admin_response()
        if denied:
            return denied
        uid = safe_user_id(user_id)
        store = _load_users()
        users = store.get("users") or {}
        if not uid or uid not in users:
            return jsonify({"error": "account not found"}), 404
        return jsonify({"user": public_user(users[uid], users), "summary": user_data_summary(uid)})

    @app.route("/api/code/auth/users/<path:user_id>/transfer", methods=["POST"])
    def api_auth_users_transfer(user_id):
        denied = require_admin_response()
        if denied:
            return denied
        uid = safe_user_id(user_id)
        data = request.get_json(force=True, silent=True) or {}
        target_uid = safe_user_id(data.get("target_user_id") or data.get("target") or "")
        store = _load_users()
        users = store.get("users") or {}
        if not uid or uid not in users:
            return jsonify({"error": "source account not found"}), 404
        if not target_uid or target_uid not in users:
            return jsonify({"error": "target account not found"}), 404
        try:
            result = transfer_user_data(uid, target_uid, include=data.get("include") or None)
        except Exception as e:
            return jsonify({"error": str(e)}), 400
        append_audit("user.data_transfer", uid, {
            "target_user_id": target_uid,
            "sections": sorted((data.get("include") or ["sessions", "workspace", "skills", "knowledge"])),
            "import_path": result.get("import_path"),
        })
        return jsonify({"status": "transferred", **result})

    @app.route("/api/code/auth/users/<path:user_id>", methods=["DELETE"])
    def api_auth_users_delete(user_id):
        denied = require_admin_response()
        if denied:
            return denied
        uid = safe_user_id(user_id)
        users = _load_users().get("users") or {}
        if not uid or uid not in users:
            return jsonify({"error": "account not found"}), 404
        # AUTH-003: identity, audit provenance, and owned knowledge must never
        # be physically erased through the legacy endpoint.  Canonical admin
        # disable/re-enable is exposed by the versioned administration API;
        # until that cutover is enabled this destructive compatibility route
        # deliberately fails closed without touching JSON or user data.
        return jsonify({
            "error": "account hard deletion is forbidden; disable the account instead",
            "code": "USER_HARD_DELETE_FORBIDDEN",
        }), 409

    @app.route("/api/code/auth/audit", methods=["GET"])
    def api_auth_audit():
        denied = require_admin_response()
        if denied:
            return denied
        try:
            limit = int(request.args.get("limit") or 100)
        except ValueError:
            limit = 100
        return jsonify({"items": read_audit(limit=limit)})
