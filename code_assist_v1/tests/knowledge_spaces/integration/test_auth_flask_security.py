from __future__ import annotations

from datetime import timedelta
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import auth
from code_assist_v1 import routes_notion
from code_assist_v1.knowledge_core.auth_security import (
    BrowserSecurityConfigurationError,
    CSRF_TOKEN_LENGTH,
)


pytestmark = [pytest.mark.integration, pytest.mark.scenario("AUTH-004")]

_ORIGIN = "https://agent.example.test"
_ATTACKER_ORIGIN = "https://attacker.example.test"


def _make_app(*, production: bool = False) -> Flask:
    app = Flask(f"auth-flask-security-{uuid4().hex}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="integration-only-secret-key-that-is-over-32-bytes",
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
        CODE_ASSIST_BROWSER_SECURITY_PRODUCTION=production,
        SESSION_COOKIE_NAME="session",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    auth.register_auth_routes(app)

    @app.post("/api/probe/mutate")
    def mutate_probe() -> dict[str, bool]:
        return {"mutated": True}

    @app.get("/api/probe/stream")
    def stream_probe() -> dict[str, bool]:
        return {"stream_started": True}

    @app.post("/public/notion/synthetic-token/duplicate")
    def public_duplicate_probe() -> dict[str, bool]:
        return {"duplicated": True}

    @app.post("/public/notion/synthetic-token/forms/form-block")
    def public_form_probe() -> dict[str, bool]:
        return {"submitted": True}

    return app


def _create_user(password: str = "Correct-Horse-Integration-42") -> tuple[str, str]:
    user_id = f"auth-sec-{uuid4().hex[:12]}"
    auth.create_user(user_id, password, display_name="Auth security user", role="user")
    return user_id, password


def _origin_headers(*, token: str | None = None, origin: str = _ORIGIN) -> dict[str, str]:
    headers = {"Origin": origin}
    if token is not None:
        headers["X-CSRF-Token"] = token
    return headers


def _login(client, user_id: str, password: str):
    return client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": user_id, "password": password},
        headers=_origin_headers(),
    )


def _assert_token(value: object) -> str:
    assert isinstance(value, str)
    assert len(value) == CSRF_TOKEN_LENGTH
    return value


def test_auth_004_login_and_register_require_allowlisted_exact_origin(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    app = _make_app()
    client = app.test_client()

    missing = client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": user_id, "password": password},
    )
    assert missing.status_code == 403
    assert missing.get_json()["reason"] == "origin_missing"

    cross = client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": user_id, "password": password},
        headers=_origin_headers(origin=_ATTACKER_ORIGIN),
    )
    assert cross.status_code == 403
    assert cross.get_json()["reason"] == "cross_origin"

    poisoned_host = client.post(
        "/api/code/auth/login",
        base_url=_ATTACKER_ORIGIN,
        json={"user_id": user_id, "password": password},
        headers=_origin_headers(origin=_ATTACKER_ORIGIN),
    )
    assert poisoned_host.status_code == 403
    assert poisoned_host.get_json()["reason"] == "request_origin_not_allowed"

    valid = _login(client, user_id, password)
    assert valid.status_code == 200
    _assert_token(valid.get_json()["csrf_token"])

    monkeypatch.setenv("CODE_ASSIST_ALLOW_SIGNUP", "1")
    register_id = f"auth-register-{uuid4().hex[:10]}"
    register_missing = app.test_client().post(
        "/api/code/auth/register",
        base_url=_ORIGIN,
        json={"user_id": register_id, "password": "Register-Horse-Password-42"},
    )
    assert register_missing.status_code == 403
    registered = app.test_client().post(
        "/api/code/auth/register",
        base_url=_ORIGIN,
        json={"user_id": register_id, "password": "Register-Horse-Password-42"},
        headers=_origin_headers(),
    )
    assert registered.status_code == 200
    _assert_token(registered.get_json()["csrf_token"])


def test_auth_004_login_me_and_auth_responses_issue_token_without_cache_or_header_leak(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    app = _make_app()
    client = app.test_client()

    login = _login(client, user_id, password)
    token = _assert_token(login.get_json()["csrf_token"])
    assert login.headers["Cache-Control"].startswith("no-store")
    assert login.headers["Pragma"] == "no-cache"
    assert token not in "\n".join(
        f"{name}: {value}" for name, value in login.headers.items()
    )

    me = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert me.status_code == 200
    assert me.get_json()["csrf_token"] == token
    assert me.headers["Cache-Control"].startswith("no-store")

    injected = client.get("/api/code/auth/me", base_url=_ATTACKER_ORIGIN)
    assert injected.status_code == 403
    assert "csrf_token" not in injected.get_data(as_text=True)
    assert token not in injected.get_data(as_text=True)


def test_auth_004_cookie_mutations_and_sse_fail_closed_then_accept_exact_proof(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    app = _make_app()
    client = app.test_client()
    token = _assert_token(_login(client, user_id, password).get_json()["csrf_token"])

    missing_origin = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers={"X-CSRF-Token": token},
    )
    assert missing_origin.status_code == 403
    assert missing_origin.get_json()["reason"] == "origin_missing"

    missing_token = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(),
    )
    assert missing_token.status_code == 403
    assert missing_token.get_json()["reason"] == "csrf_header_missing"

    invalid_token = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(token="B" * CSRF_TOKEN_LENGTH),
    )
    assert invalid_token.status_code == 403
    assert invalid_token.get_json()["reason"] == "csrf_token_invalid"

    cross_origin = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token, origin=_ATTACKER_ORIGIN),
    )
    assert cross_origin.status_code == 403
    assert cross_origin.get_json()["reason"] == "cross_origin"

    valid = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
    )
    assert valid.status_code == 200
    assert valid.get_json() == {"mutated": True}

    sse_missing = client.get(
        "/api/probe/stream",
        base_url=_ORIGIN,
        headers={"Origin": _ORIGIN, "Accept": "text/event-stream"},
    )
    assert sse_missing.status_code == 403
    assert sse_missing.get_json()["reason"] == "csrf_header_missing"

    sse_valid = client.get(
        "/api/probe/stream",
        base_url=_ORIGIN,
        headers={
            **_origin_headers(token=token),
            "Accept": "text/event-stream",
        },
    )
    assert sse_valid.status_code == 200
    assert sse_valid.get_json() == {"stream_started": True}


def test_auth_004_password_change_rotates_token_and_logout_requires_current_token(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    old_password = "Correct-Horse-Old-Password-42"
    new_password = "Correct-Horse-New-Password-84"
    user_id, _ = _create_user(old_password)
    app = _make_app()
    client = app.test_client()
    old_token = _assert_token(_login(client, user_id, old_password).get_json()["csrf_token"])

    changed = client.post(
        "/api/code/auth/password",
        base_url=_ORIGIN,
        json={"current_password": old_password, "new_password": new_password},
        headers=_origin_headers(token=old_token),
    )
    assert changed.status_code == 200
    new_token = _assert_token(changed.get_json()["csrf_token"])
    assert new_token != old_token

    stale = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=old_token),
    )
    assert stale.status_code == 403
    assert stale.get_json()["reason"] == "csrf_token_invalid"

    missing_logout = client.post(
        "/api/code/auth/logout",
        base_url=_ORIGIN,
        headers=_origin_headers(),
    )
    assert missing_logout.status_code == 403
    assert client.get("/api/code/auth/me", base_url=_ORIGIN).get_json()["authenticated"]

    logout = client.post(
        "/api/code/auth/logout",
        base_url=_ORIGIN,
        headers=_origin_headers(token=new_token),
    )
    assert logout.status_code == 200
    assert logout.get_json() == {"status": "logged_out"}
    assert client.get("/api/code/auth/me", base_url=_ORIGIN).get_json()["authenticated"] is False

    relogin = _login(client, user_id, new_password)
    assert relogin.status_code == 200


def test_auth_004_public_form_is_exact_anonymous_exception_but_duplicate_is_cookie_protected(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    app = _make_app()

    anonymous = app.test_client()
    submitted = anonymous.post(
        "/public/notion/synthetic-token/forms/form-block",
        base_url=_ORIGIN,
    )
    assert submitted.status_code == 200
    assert submitted.get_json() == {"submitted": True}
    unauthenticated_duplicate = anonymous.post(
        "/public/notion/synthetic-token/duplicate",
        base_url=_ORIGIN,
    )
    assert unauthenticated_duplicate.status_code == 302
    assert unauthenticated_duplicate.headers["Location"] == "/login"

    client = app.test_client()
    token = _assert_token(_login(client, user_id, password).get_json()["csrf_token"])
    rejected = client.post(
        "/public/notion/synthetic-token/duplicate",
        base_url=_ORIGIN,
        headers=_origin_headers(),
    )
    assert rejected.status_code == 403
    assert rejected.get_json()["reason"] == "csrf_header_missing"
    accepted = client.post(
        "/public/notion/synthetic-token/duplicate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
    )
    assert accepted.status_code == 200
    assert accepted.get_json() == {"duplicated": True}


def test_auth_004_actual_public_duplicate_html_bootstraps_csrf_and_route_requires_it(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    duplicate_calls: list[tuple[str, str]] = []
    public_data = {
        "page": {
            "id": "public-source-page",
            "title": "Public duplicate fixture",
            "owner_user_id": "owner-user",
            "updated_at": 1,
            "icon": "",
        },
        "blocks": [],
        "files": [],
        "public_link": {"allow_duplicate": True},
    }
    monkeypatch.setattr(
        routes_notion.ns,
        "resolve_public_page",
        lambda token: public_data if token == "public-token" else None,
    )

    def duplicate_public_page(actor_id: str, token: str) -> dict[str, object]:
        duplicate_calls.append((actor_id, token))
        return {"page": {"id": "duplicated-page-id"}}

    monkeypatch.setattr(
        routes_notion.ns,
        "duplicate_public_page",
        duplicate_public_page,
    )
    app = _make_app()
    routes_notion.register_notion_routes(app)
    client = app.test_client()
    token = _assert_token(_login(client, user_id, password).get_json()["csrf_token"])

    page = client.get("/public/notion/public-token", base_url=_ORIGIN)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "id='publicDuplicateForm'" in html
    assert 'fetch("/api/code/auth/me", { credentials: "same-origin" })' in html
    assert 'headers: { "X-CSRF-Token": csrfToken }' in html
    assert 'credentials: "same-origin"' in html
    assert "form.addEventListener(\"submit\"" in html

    rejected = client.post(
        "/public/notion/public-token/duplicate",
        base_url=_ORIGIN,
        headers=_origin_headers(),
    )
    assert rejected.status_code == 403
    assert duplicate_calls == []

    accepted = client.post(
        "/public/notion/public-token/duplicate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
    )
    assert accepted.status_code == 303
    assert accepted.headers["Location"] == "/page/duplicated-page-id"
    assert duplicate_calls == [(user_id, "public-token")]


def test_auth_004_signed_cookie_cannot_authenticate_after_registry_account_disappears(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    user_id, password = _create_user()
    app = _make_app()
    client = app.test_client()
    token = _assert_token(_login(client, user_id, password).get_json()["csrf_token"])

    store = auth._load_users()
    del store["users"][user_id]
    auth._save_users(store)

    me = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert me.status_code == 200
    assert me.get_json()["authenticated"] is False
    assert "csrf_token" not in me.get_json()
    mutation = client.post(
        "/api/probe/mutate",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
    )
    assert mutation.status_code == 401


def test_auth_004_host_allowlist_applies_to_safe_static_health_and_public_routes(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    app = _make_app()

    for path in (
        "/static/app.js",
        "/healthz",
        "/public/notion/public-token",
        "/api/code/auth/setup_required",
    ):
        response = app.test_client().get(path, base_url=_ATTACKER_ORIGIN)
        assert response.status_code == 403, path
        assert response.get_json()["reason"] == "request_origin_not_allowed"


def test_auth_004_existing_corrupt_registry_fails_closed_without_first_admin_bootstrap(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = isolated_data_root / "corrupt-auth-fixture" / "users.json"
    monkeypatch.setattr(auth, "AUTH_USERS_FILE", str(registry_path))
    assert registry_path.is_relative_to(isolated_data_root)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text('{"users":', encoding="utf-8")
    before = registry_path.read_bytes()
    monkeypatch.setenv("CODE_ASSIST_ALLOW_SIGNUP", "1")
    app = _make_app()
    client = app.test_client()

    setup = client.get("/api/code/auth/setup_required", base_url=_ORIGIN)
    assert setup.status_code == 503
    assert setup.get_json() == {
        "error": "account registry unavailable",
        "code": "USER_REGISTRY_UNAVAILABLE",
    }
    register = client.post(
        "/api/code/auth/register",
        base_url=_ORIGIN,
        headers=_origin_headers(),
        json={
            "user_id": "attacker-admin",
            "password": "Attacker-Horse-Password-42",
        },
    )
    assert register.status_code == 503
    assert registry_path.read_bytes() == before


def test_auth_004_legacy_delete_endpoint_and_helper_preserve_registry_and_data(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    admin_id = f"auth-admin-{uuid4().hex[:10]}"
    member_id = f"auth-member-{uuid4().hex[:10]}"
    admin_password = "Admin-Horse-Password-42"
    member_password = "Member-Horse-Password-42"
    auth.create_user(admin_id, admin_password, display_name="Admin", role="admin")
    auth.create_user(member_id, member_password, display_name="Member", role="user")
    member_data = Path(auth.USER_DATA_DIR) / member_id / "memory" / "keep.txt"
    member_knowledge = Path(auth.KNOWLEDGE_DIR) / member_id / "keep.md"
    member_data.parent.mkdir(parents=True, exist_ok=True)
    member_knowledge.parent.mkdir(parents=True, exist_ok=True)
    member_data.write_text("retain data", encoding="utf-8")
    member_knowledge.write_text("retain knowledge", encoding="utf-8")
    registry_path = Path(auth.AUTH_USERS_FILE)
    before_registry = registry_path.read_bytes()

    app = _make_app()
    client = app.test_client()
    token = _assert_token(_login(client, admin_id, admin_password).get_json()["csrf_token"])
    response = client.delete(
        f"/api/code/auth/users/{member_id}",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
        json={"delete_data": True},
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "USER_HARD_DELETE_FORBIDDEN"
    assert registry_path.read_bytes() == before_registry
    assert auth.verify_user(member_id, member_password) is not None
    assert member_data.read_text(encoding="utf-8") == "retain data"
    assert member_knowledge.read_text(encoding="utf-8") == "retain knowledge"

    with pytest.raises(PermissionError, match="hard deletion is forbidden"):
        auth.delete_user_data(member_id)
    assert registry_path.read_bytes() == before_registry
    assert member_data.is_file()
    assert member_knowledge.is_file()


def test_auth_004_legacy_disabled_patch_requires_canonical_lifecycle_and_changes_nothing(
    isolated_data_root: Path,
) -> None:
    del isolated_data_root
    admin_id = f"auth-admin-{uuid4().hex[:10]}"
    member_id = f"auth-member-{uuid4().hex[:10]}"
    admin_password = "Admin-Patch-Horse-Password-42"
    auth.create_user(admin_id, admin_password, display_name="Admin", role="admin")
    auth.create_user(
        member_id,
        "Member-Patch-Horse-Password-42",
        display_name="Member before patch",
        role="user",
    )
    registry_path = Path(auth.AUTH_USERS_FILE)
    before_registry = registry_path.read_bytes()
    before_user = dict(auth._load_users()["users"][member_id])

    app = _make_app()
    client = app.test_client()
    token = _assert_token(_login(client, admin_id, admin_password).get_json()["csrf_token"])
    response = client.patch(
        f"/api/code/auth/users/{member_id}",
        base_url=_ORIGIN,
        headers=_origin_headers(token=token),
        json={"disabled": True, "display_name": "Must not be applied"},
    )

    assert response.status_code == 409
    assert response.get_json()["code"] == "CANONICAL_ACCOUNT_LIFECYCLE_REQUIRED"
    assert registry_path.read_bytes() == before_registry
    assert auth._load_users()["users"][member_id] == before_user
    me = client.get("/api/code/auth/me", base_url=_ORIGIN)
    assert me.get_json()["user"]["id"] == admin_id


@pytest.mark.parametrize("port", [10011, 10012])
def test_auth_004_fixed_development_allowlist_supports_both_existing_hosts(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    port: int,
) -> None:
    del isolated_data_root
    monkeypatch.delenv("CODE_ASSIST_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CODE_ASSIST_BROWSER_SECURITY_PRODUCTION", raising=False)
    user_id, password = _create_user()
    app = Flask(f"auth-existing-host-{port}-{uuid4().hex}")
    app.config.update(TESTING=True, SECRET_KEY="development-secret")
    auth.register_auth_routes(app)
    origin = f"http://localhost:{port}"

    response = app.test_client().post(
        "/api/code/auth/login",
        base_url=origin,
        json={"user_id": user_id, "password": password},
        headers={"Origin": origin},
    )
    assert response.status_code == 200


def test_auth_004_production_registration_requires_explicit_https_origins_and_cookie_gate(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_data_root
    monkeypatch.delenv("CODE_ASSIST_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CODE_ASSIST_BROWSER_SECURITY_PRODUCTION", raising=False)
    missing = Flask(f"auth-production-missing-{uuid4().hex}")
    missing.config.update(
        TESTING=True,
        SECRET_KEY="s" * 64,
        CODE_ASSIST_BROWSER_SECURITY_PRODUCTION=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    with pytest.raises(BrowserSecurityConfigurationError, match="required in production"):
        auth.register_auth_routes(missing)

    valid = _make_app(production=True)
    assert valid.extensions["code_assist_browser_security"].production is True


def test_auth_004_concurrent_first_bootstrap_publishes_one_shared_secret(
    isolated_data_root: Path,
) -> None:
    race_root = isolated_data_root / "secret-bootstrap-race"
    race_root.mkdir()
    start_marker = race_root / "start"
    process_count = 12
    script = r"""
import hashlib
import os
from pathlib import Path
import time

from code_assist_v1.auth import ensure_secret_key

root = Path(os.environ["CODE_ASSIST_TEST_DATA_ROOT"])
index = os.environ["SECRET_RACE_INDEX"]
(root / ("ready-" + index)).write_text("ready", encoding="ascii")
deadline = time.monotonic() + 20
while not (root / "start").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("secret race start barrier timed out")
    time.sleep(0.005)
value = ensure_secret_key()
print(hashlib.sha256(value.encode("utf-8")).hexdigest())
"""
    processes: list[subprocess.Popen[str]] = []
    for index in range(process_count):
        child_env = os.environ.copy()
        child_env.update(
            {
                "CODE_ASSIST_TEST_DATA_ROOT": os.fspath(race_root),
                "CODE_ASSIST_DATA_ROOT": os.fspath(race_root),
                "SECRET_RACE_INDEX": str(index),
            }
        )
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=os.fspath(Path(__file__).parents[4]),
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    ready_deadline = time.monotonic() + 20
    while len(tuple(race_root.glob("ready-*"))) != process_count:
        if time.monotonic() >= ready_deadline:
            for process in processes:
                process.kill()
            raise AssertionError("secret bootstrap subprocess barrier timed out")
        time.sleep(0.01)
    start_marker.write_text("start", encoding="ascii")

    digests: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        digest = stdout.strip().splitlines()[-1]
        assert len(digest) == 64
        digests.append(digest)

    assert len(set(digests)) == 1
    secret_path = race_root / ".runtime" / "secret.key"
    secret = secret_path.read_text(encoding="utf-8").strip()
    assert len(secret.encode("utf-8")) >= 32
    assert hashlib.sha256(secret.encode("utf-8")).hexdigest() == digests[0]
    assert not tuple(secret_path.parent.glob(".secret.key.*.tmp"))
