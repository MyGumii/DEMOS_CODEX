from __future__ import annotations

from datetime import timedelta

import pytest

from code_assist_v1.knowledge_core.auth_security import (
    BrowserSecurityConfigurationError,
    BrowserSecurityPolicy,
    CSRF_SESSION_KEY,
    CSRF_TOKEN_LENGTH,
    CookieConfigViolation,
    DenialReason,
    RequestSecurityFacts,
    evaluate_request_security,
    issue_csrf_token,
    require_session_cookie_config,
    rotate_csrf_token,
    validate_session_cookie_config,
    verify_csrf_token,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("AUTH-004")]

_TOKEN = "A" * CSRF_TOKEN_LENGTH
_OTHER_TOKEN = "B" * CSRF_TOKEN_LENGTH
_ORIGIN = "https://agent.example.test"


def _policy(*, production: bool = False) -> BrowserSecurityPolicy:
    return BrowserSecurityPolicy(
        allowed_origins=frozenset(
            {
                "https://agent.example.test",
                "https://agent.example.test:10011",
            }
        ),
        production=production,
    )


def _facts(**overrides: object) -> RequestSecurityFacts:
    values: dict[str, object] = {
        "method": "POST",
        "path": "/api/knowledge/v1/publications",
        "scheme": "https",
        "host": "agent.example.test",
        "origin": "https://agent.example.test",
        "submitted_csrf_token": _TOKEN,
        "authenticated_cookie_session": True,
        "is_sse_initiation": False,
    }
    values.update(overrides)
    return RequestSecurityFacts(**values)  # type: ignore[arg-type]


def _production_cookie_config() -> dict[str, object]:
    return {
        "SECRET_KEY": "s" * 64,
        "SESSION_COOKIE_NAME": "session",
        "SESSION_COOKIE_DOMAIN": None,
        "SESSION_COOKIE_PATH": "/",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SECURE": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
    }


def test_auth_004_issue_reuse_and_rotate_strict_session_csrf_token() -> None:
    legacy_user = {"id": "member01", "role": "user"}
    session_state: dict[str, object] = {"code_assist_user": legacy_user}

    first = issue_csrf_token(session_state)
    assert len(first) == CSRF_TOKEN_LENGTH
    assert first == session_state[CSRF_SESSION_KEY]
    assert session_state["code_assist_user"] is legacy_user
    assert issue_csrf_token(session_state) == first
    assert verify_csrf_token(first, first)

    second = rotate_csrf_token(session_state)
    assert len(second) == CSRF_TOKEN_LENGTH
    assert second != first
    assert session_state[CSRF_SESSION_KEY] == second
    assert not verify_csrf_token(second, first)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        b"A" * CSRF_TOKEN_LENGTH,
        "A" * (CSRF_TOKEN_LENGTH - 1),
        "A" * (CSRF_TOKEN_LENGTH + 1),
        "A" * (CSRF_TOKEN_LENGTH - 1) + "=",
        "A" * (CSRF_TOKEN_LENGTH - 1) + " ",
        "A" * (CSRF_TOKEN_LENGTH - 1) + ",",
    ],
)
def test_auth_004_csrf_parser_rejects_noncanonical_values(invalid: object) -> None:
    assert not verify_csrf_token(_TOKEN, invalid)
    assert not verify_csrf_token(invalid, _TOKEN)


def test_auth_004_invalid_existing_session_token_is_replaced() -> None:
    session_state: dict[str, object] = {CSRF_SESSION_KEY: "not-a-token"}
    token = issue_csrf_token(session_state)
    assert len(token) == CSRF_TOKEN_LENGTH
    assert token != "not-a-token"
    assert session_state[CSRF_SESSION_KEY] == token


@pytest.mark.parametrize(
    "path",
    [
        "/api/code/auth/login",
        "/api/code/auth/register",
    ],
)
def test_auth_004_preserves_only_exact_login_and_register_csrf_exemptions(
    path: str,
) -> None:
    decision = evaluate_request_security(
        _facts(
            path=path,
            origin=_ORIGIN,
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=None,
    )
    assert decision.allowed
    assert decision.protection_required
    assert decision.denial_reason is None


@pytest.mark.parametrize(
    "path",
    ["/api/code/auth/login", "/api/code/auth/register"],
)
def test_auth_004_bootstrap_paths_still_require_same_origin(path: str) -> None:
    missing = evaluate_request_security(
        _facts(path=path, origin=None, submitted_csrf_token=None),
        policy=_policy(),
        expected_csrf_token=None,
    )
    cross = evaluate_request_security(
        _facts(
            path=path,
            origin="https://attacker.example.test",
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=None,
    )
    assert missing.denial_reason is DenialReason.ORIGIN_MISSING
    assert cross.denial_reason is DenialReason.CROSS_ORIGIN


def test_auth_004_near_match_auth_path_is_not_an_exemption() -> None:
    decision = evaluate_request_security(
        _facts(
            path="/api/code/auth/login/",
            origin=None,
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=None,
    )
    assert not decision.allowed
    assert decision.denial_reason is DenialReason.ORIGIN_MISSING


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "TRACE"])
def test_auth_004_all_non_safe_cookie_methods_require_and_accept_full_proof(
    method: str,
) -> None:
    decision = evaluate_request_security(
        _facts(method=method.lower()),
        policy=_policy(),
        expected_csrf_token=_TOKEN,
    )
    assert decision.allowed
    assert decision.protection_required


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_auth_004_non_stream_safe_methods_do_not_require_csrf(method: str) -> None:
    decision = evaluate_request_security(
        _facts(
            method=method,
            origin=None,
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=None,
    )
    assert decision.allowed
    assert not decision.protection_required


def test_auth_004_get_sse_initiation_requires_same_origin_and_csrf() -> None:
    denied = evaluate_request_security(
        _facts(
            method="GET",
            is_sse_initiation=True,
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=_TOKEN,
    )
    assert not denied.allowed
    assert denied.denial_reason is DenialReason.CSRF_HEADER_MISSING

    allowed = evaluate_request_security(
        _facts(method="GET", is_sse_initiation=True),
        policy=_policy(),
        expected_csrf_token=_TOKEN,
    )
    assert allowed.allowed
    assert allowed.protection_required


def test_auth_004_anonymous_request_remains_the_auth_layers_responsibility() -> None:
    decision = evaluate_request_security(
        _facts(
            authenticated_cookie_session=False,
            origin=None,
            submitted_csrf_token=None,
        ),
        policy=_policy(),
        expected_csrf_token=None,
    )
    assert decision.allowed
    assert not decision.protection_required


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"origin": None}, DenialReason.ORIGIN_MISSING),
        ({"origin": ""}, DenialReason.ORIGIN_MISSING),
        ({"origin": "null"}, DenialReason.ORIGIN_INVALID),
        ({"origin": "https://user@agent.example.test"}, DenialReason.ORIGIN_INVALID),
        ({"origin": "https://agent.example.test/path"}, DenialReason.ORIGIN_INVALID),
        ({"origin": "https://agent.example.test?"}, DenialReason.ORIGIN_INVALID),
        ({"origin": "https://agent.example.test#"}, DenialReason.ORIGIN_INVALID),
        (
            {"origin": "https://agent.example.test, https://evil.example"},
            DenialReason.ORIGIN_INVALID,
        ),
        ({"origin": "https://evil.example"}, DenialReason.CROSS_ORIGIN),
        ({"origin": "http://agent.example.test"}, DenialReason.CROSS_ORIGIN),
        ({"origin": "https://agent.example.test:10011"}, DenialReason.CROSS_ORIGIN),
        ({"host": "evil.example"}, DenialReason.REQUEST_ORIGIN_NOT_ALLOWED),
        ({"host": "agent.example.test,evil.example"}, DenialReason.INVALID_REQUEST_HOST),
        ({"host": "user@agent.example.test"}, DenialReason.INVALID_REQUEST_HOST),
    ],
)
def test_auth_004_rejects_missing_invalid_cross_origin_or_poisoned_host(
    changes: dict[str, object],
    reason: DenialReason,
) -> None:
    decision = evaluate_request_security(
        _facts(**changes),
        policy=_policy(),
        expected_csrf_token=_TOKEN,
    )
    assert not decision.allowed
    assert decision.protection_required
    assert decision.denial_reason is reason


def test_auth_004_default_ports_and_case_are_canonicalized() -> None:
    decision = evaluate_request_security(
        _facts(
            scheme="HTTPS",
            host="AGENT.EXAMPLE.TEST:443",
            origin="https://AGENT.EXAMPLE.TEST:443/",
        ),
        policy=_policy(),
        expected_csrf_token=_TOKEN,
    )
    assert decision.allowed


@pytest.mark.parametrize(
    ("expected", "submitted", "reason"),
    [
        (None, _TOKEN, DenialReason.CSRF_SESSION_MISSING),
        ("invalid", _TOKEN, DenialReason.CSRF_SESSION_MISSING),
        (_TOKEN, None, DenialReason.CSRF_HEADER_MISSING),
        (_TOKEN, "", DenialReason.CSRF_HEADER_MISSING),
        (_TOKEN, _OTHER_TOKEN, DenialReason.CSRF_TOKEN_INVALID),
        (_TOKEN, _TOKEN + "," + _TOKEN, DenialReason.CSRF_TOKEN_INVALID),
    ],
)
def test_auth_004_rejects_missing_or_invalid_csrf_proof(
    expected: object,
    submitted: str | None,
    reason: DenialReason,
) -> None:
    decision = evaluate_request_security(
        _facts(submitted_csrf_token=submitted),
        policy=_policy(),
        expected_csrf_token=expected,
    )
    assert not decision.allowed
    assert decision.denial_reason is reason


def test_auth_004_origin_policy_rejects_empty_wildcard_and_http_production() -> None:
    with pytest.raises(BrowserSecurityConfigurationError):
        BrowserSecurityPolicy(allowed_origins=frozenset())
    with pytest.raises(BrowserSecurityConfigurationError):
        BrowserSecurityPolicy(allowed_origins=frozenset({"*"}))
    with pytest.raises(BrowserSecurityConfigurationError):
        BrowserSecurityPolicy(
            allowed_origins=frozenset({"http://agent.example.test"}),
            production=True,
        )
    with pytest.raises(BrowserSecurityConfigurationError):
        BrowserSecurityPolicy(
            allowed_origins=frozenset({"https://agent.example.test"}),
            exempt_paths=frozenset(
                {
                    "/api/code/auth/login",
                    "/api/code/auth/register",
                    "/api/knowledge/v1/publications",
                }
            ),
        )


def test_auth_004_production_cookie_attributes_pass_without_mutation() -> None:
    config = _production_cookie_config()
    before = dict(config)

    report = validate_session_cookie_config(config, production=True)

    assert report.ok
    assert report.violations == ()
    assert config == before
    require_session_cookie_config(config, production=True)


def test_auth_004_flask_emits_the_validated_production_cookie_attributes() -> None:
    from flask import Flask, session

    app = Flask("auth-004-cookie-fixture")
    app.config.update(_production_cookie_config())

    @app.get("/cookie-fixture")
    def cookie_fixture() -> dict[str, bool]:
        session["code_assist_user"] = {"id": "member01"}
        session.permanent = True
        issue_csrf_token(session)
        return {"ok": True}

    response = app.test_client().get(
        "/cookie-fixture",
        base_url="https://agent.example.test",
    )
    cookie = response.headers["Set-Cookie"]

    assert cookie.startswith("session=")
    assert "; Secure" in cookie
    assert "; HttpOnly" in cookie
    assert "; Path=/" in cookie
    assert "; SameSite=Lax" in cookie
    assert "Domain=" not in cookie


def test_auth_004_development_may_use_http_cookie_but_keeps_browser_guards() -> None:
    config = _production_cookie_config()
    config["SESSION_COOKIE_SECURE"] = False
    config["SECRET_KEY"] = "short-development-key"
    config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)

    report = validate_session_cookie_config(config, production=False)

    assert report.ok


@pytest.mark.parametrize(
    ("key", "value", "violation"),
    [
        ("SESSION_COOKIE_NAME", "bad cookie", CookieConfigViolation.COOKIE_NAME_INVALID),
        ("SESSION_COOKIE_NAME", "other-session", CookieConfigViolation.COOKIE_NAME_INVALID),
        (
            "SESSION_COOKIE_DOMAIN",
            ".example.test",
            CookieConfigViolation.COOKIE_DOMAIN_NOT_HOST_ONLY,
        ),
        ("SESSION_COOKIE_PATH", "/api", CookieConfigViolation.COOKIE_PATH_INVALID),
        (
            "SESSION_COOKIE_HTTPONLY",
            False,
            CookieConfigViolation.COOKIE_HTTPONLY_REQUIRED,
        ),
        (
            "SESSION_COOKIE_SECURE",
            False,
            CookieConfigViolation.COOKIE_SECURE_REQUIRED,
        ),
        (
            "SESSION_COOKIE_SECURE",
            "true",
            CookieConfigViolation.COOKIE_SECURE_INVALID,
        ),
        (
            "SESSION_COOKIE_SAMESITE",
            None,
            CookieConfigViolation.COOKIE_SAMESITE_INVALID,
        ),
        (
            "SESSION_COOKIE_SAMESITE",
            "None",
            CookieConfigViolation.COOKIE_SAMESITE_INVALID,
        ),
        ("SECRET_KEY", "short", CookieConfigViolation.SECRET_KEY_TOO_SHORT),
        (
            "PERMANENT_SESSION_LIFETIME",
            timedelta(hours=12, seconds=1),
            CookieConfigViolation.SESSION_LIFETIME_INVALID,
        ),
    ],
)
def test_auth_004_production_cookie_gate_fails_each_unsafe_attribute(
    key: str,
    value: object,
    violation: CookieConfigViolation,
) -> None:
    config = _production_cookie_config()
    config[key] = value

    report = validate_session_cookie_config(config, production=True)

    assert not report.ok
    assert violation in report.violations


def test_auth_004_cookie_failure_is_deterministic_and_does_not_echo_secret() -> None:
    config = _production_cookie_config()
    secret = "do-not-echo-this-secret"
    config.update(
        {
            "SECRET_KEY": secret,
            "SESSION_COOKIE_HTTPONLY": False,
            "SESSION_COOKIE_SECURE": False,
        }
    )

    first = validate_session_cookie_config(config, production=True)
    second = validate_session_cookie_config(config, production=True)
    assert first == second
    assert tuple(item.value for item in first.violations) == tuple(
        sorted(item.value for item in first.violations)
    )

    with pytest.raises(BrowserSecurityConfigurationError) as captured:
        require_session_cookie_config(config, production=True)
    assert captured.value.code == "BROWSER_SECURITY_CONFIGURATION_INVALID"
    assert secret not in str(captured.value)
