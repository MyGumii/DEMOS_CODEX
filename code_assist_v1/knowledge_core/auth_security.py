"""Framework-neutral browser security policy for cookie-authenticated APIs.

This module intentionally registers no Flask hook or route and reads no process
configuration.  A host adapter supplies already-resolved request facts and the
expected CSRF token from its signed cookie session.  Keeping policy evaluation
pure makes the same contract usable by ``app_code``, ``app_notion``, and the
optional Blueprint without making any of them authoritative.

The CSRF token is random session state, not another authentication credential.
It is safe to retain inside Flask's signed cookie session and must be surfaced to
same-origin JavaScript through an authenticated response before protected calls.
Login and first-account registration remain exact-path CSRF bootstrap
exemptions, but still require an allowlisted exact Origin/Host pair.
Successful login/registration clears the legacy session, so the adapter must
issue a new token afterwards; logout clears it with the rest of the session.
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import math
import re
import secrets
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any
from urllib.parse import urlsplit


CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_SESSION_KEY = "_knowledge_csrf_v1"
CSRF_TOKEN_BYTES = 32
CSRF_TOKEN_LENGTH = 43

DEFAULT_AUTH_EXEMPT_PATHS = frozenset(
    {
        "/api/code/auth/login",
        "/api/code/auth/register",
    }
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CSRF_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$", re.ASCII)
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$", re.ASCII)
_ASCII_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20\x7f]", re.ASCII)
_DEFAULT_PORTS = {"http": 80, "https": 443}


class BrowserSecurityConfigurationError(ValueError):
    """A browser-security configuration is ambiguous or unsafe."""

    code = "BROWSER_SECURITY_CONFIGURATION_INVALID"


class DenialReason(str, Enum):
    """Non-secret reason suitable for metrics and deterministic tests."""

    INVALID_REQUEST_HOST = "invalid_request_host"
    REQUEST_ORIGIN_NOT_ALLOWED = "request_origin_not_allowed"
    ORIGIN_MISSING = "origin_missing"
    ORIGIN_INVALID = "origin_invalid"
    CROSS_ORIGIN = "cross_origin"
    CSRF_SESSION_MISSING = "csrf_session_missing"
    CSRF_HEADER_MISSING = "csrf_header_missing"
    CSRF_TOKEN_INVALID = "csrf_token_invalid"


class CookieConfigViolation(str, Enum):
    """Stable fail-closed cookie configuration findings."""

    COOKIE_NAME_INVALID = "cookie_name_invalid"
    COOKIE_DOMAIN_NOT_HOST_ONLY = "cookie_domain_not_host_only"
    COOKIE_PATH_INVALID = "cookie_path_invalid"
    COOKIE_HTTPONLY_REQUIRED = "cookie_httponly_required"
    COOKIE_SECURE_REQUIRED = "cookie_secure_required"
    COOKIE_SECURE_INVALID = "cookie_secure_invalid"
    COOKIE_SAMESITE_INVALID = "cookie_samesite_invalid"
    SECRET_KEY_TOO_SHORT = "secret_key_too_short"
    SESSION_LIFETIME_INVALID = "session_lifetime_invalid"


@dataclass(frozen=True, slots=True)
class BrowserSecurityPolicy:
    """Allowlisted browser origins and exact bootstrap exemptions.

    Origins are canonicalized at construction.  In production every configured
    origin must use HTTPS, which keeps ``Secure`` session cookies usable and
    prevents accidentally declaring an HTTP deployment production-ready.
    """

    allowed_origins: frozenset[str]
    exempt_paths: frozenset[str] = DEFAULT_AUTH_EXEMPT_PATHS
    production: bool = False

    def __post_init__(self) -> None:
        origins = _normalize_allowed_origins(self.allowed_origins)
        if self.production and any(not value.startswith("https://") for value in origins):
            raise BrowserSecurityConfigurationError(
                "production browser origins must use https"
            )

        paths = frozenset(self.exempt_paths)
        if paths != DEFAULT_AUTH_EXEMPT_PATHS or any(
            not isinstance(path, str)
            or not path.startswith("/")
            or "?" in path
            or "#" in path
            for path in paths
        ):
            raise BrowserSecurityConfigurationError(
                "only the exact login and register bootstrap paths may be exempt"
            )

        object.__setattr__(self, "allowed_origins", origins)
        object.__setattr__(self, "exempt_paths", paths)


@dataclass(frozen=True, slots=True)
class RequestSecurityFacts:
    """Framework-extracted request facts consumed by the pure policy."""

    method: str
    path: str
    scheme: str
    host: str
    origin: str | None
    submitted_csrf_token: str | None
    authenticated_cookie_session: bool
    is_sse_initiation: bool = False


@dataclass(frozen=True, slots=True)
class SecurityDecision:
    """Allow/deny result; denied requests are always intended to map to 403."""

    allowed: bool
    protection_required: bool
    denial_reason: DenialReason | None = None

    @classmethod
    def allow(cls, *, protection_required: bool) -> "SecurityDecision":
        return cls(allowed=True, protection_required=protection_required)

    @classmethod
    def deny(cls, reason: DenialReason) -> "SecurityDecision":
        return cls(
            allowed=False,
            protection_required=True,
            denial_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class SessionCookiePolicy:
    """Compatibility-preserving production cookie requirements.

    ``SameSite=Lax`` matches both existing Flask hosts and permits the retained
    top-level OAuth callback flow.  Host-only ``session`` cookies continue to be
    shared by the two same-host ports; this policy never renames or rewrites an
    existing session cookie.
    """

    same_site: str = "Lax"
    max_permanent_lifetime_seconds: int = 12 * 60 * 60
    min_secret_key_bytes: int = 32

    def __post_init__(self) -> None:
        if self.same_site not in {"Lax", "Strict"}:
            raise BrowserSecurityConfigurationError(
                "session cookie SameSite policy must be Lax or Strict"
            )
        if self.max_permanent_lifetime_seconds < 1:
            raise BrowserSecurityConfigurationError(
                "maximum permanent session lifetime must be positive"
            )
        if self.min_secret_key_bytes < 32:
            raise BrowserSecurityConfigurationError(
                "minimum secret-key length cannot be below 32 bytes"
            )


@dataclass(frozen=True, slots=True)
class CookieConfigurationReport:
    """Deterministically ordered cookie gate result with no secret values."""

    production: bool
    violations: tuple[CookieConfigViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


def issue_csrf_token(session_state: MutableMapping[str, Any]) -> str:
    """Return the valid session token, creating one from 256 random bits.

    Invalid legacy/tampered values are replaced.  Callers should invoke this
    only after establishing the authenticated session, and expose the returned
    token in a same-origin authenticated JSON response or HTML bootstrap value.
    """

    existing = session_state.get(CSRF_SESSION_KEY)
    if _is_valid_csrf_token(existing):
        return existing

    raw = secrets.token_bytes(CSRF_TOKEN_BYTES)
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if not _is_valid_csrf_token(token):  # defensive guard around the encoder
        raise RuntimeError("failed to generate a valid CSRF token")
    session_state[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token(session_state: MutableMapping[str, Any]) -> str:
    """Invalidate the prior session token and issue a new one."""

    session_state.pop(CSRF_SESSION_KEY, None)
    return issue_csrf_token(session_state)


def verify_csrf_token(expected: object, submitted: object) -> bool:
    """Strictly validate and constant-time compare two CSRF token strings."""

    if not _is_valid_csrf_token(expected) or not _is_valid_csrf_token(submitted):
        return False
    return hmac.compare_digest(expected, submitted)


def evaluate_request_security(
    facts: RequestSecurityFacts,
    *,
    policy: BrowserSecurityPolicy,
    expected_csrf_token: object,
) -> SecurityDecision:
    """Evaluate CSRF and same-origin requirements for one cookie request.

    Authentication remains the auth layer's responsibility.  Cookie-authenticated
    non-safe methods are protected.  Login/registration have no prior CSRF token
    but still require exact Origin/Host proof.  An explicitly classified SSE
    initiation is protected even when transported with GET because opening the
    stream accepts/continues an authenticated Agent operation.  The host adapter
    separately applies :func:`request_host_denial_reason` to every request so safe
    token/bootstrap/static responses cannot be reached through a poisoned Host.
    """

    path = facts.path if isinstance(facts.path, str) else ""
    method = facts.method.upper() if isinstance(facts.method, str) else ""
    protected_shape = method not in _SAFE_METHODS or bool(facts.is_sse_initiation)
    bootstrap_exempt = path in policy.exempt_paths
    protection_required = bool(
        facts.authenticated_cookie_session and protected_shape and not bootstrap_exempt
    )
    if not protection_required:
        # Login and registration have no prior CSRF secret, but unsafe browser
        # requests still require a normalized same-origin Origin/Host proof.
        if not (bootstrap_exempt and protected_shape):
            return SecurityDecision.allow(protection_required=False)

    host_denial = request_host_denial_reason(
        scheme=facts.scheme,
        host=facts.host,
        policy=policy,
    )
    if host_denial is not None:
        return SecurityDecision.deny(host_denial)
    request_origin = _canonical_request_origin(facts.scheme, facts.host)
    if request_origin is None:  # pragma: no cover - established above
        return SecurityDecision.deny(DenialReason.INVALID_REQUEST_HOST)

    if facts.origin is None or facts.origin == "":
        return SecurityDecision.deny(DenialReason.ORIGIN_MISSING)
    supplied_origin = _canonicalize_origin(facts.origin)
    if supplied_origin is None:
        return SecurityDecision.deny(DenialReason.ORIGIN_INVALID)
    if supplied_origin != request_origin:
        return SecurityDecision.deny(DenialReason.CROSS_ORIGIN)

    if bootstrap_exempt:
        return SecurityDecision.allow(protection_required=True)

    if not _is_valid_csrf_token(expected_csrf_token):
        return SecurityDecision.deny(DenialReason.CSRF_SESSION_MISSING)
    if facts.submitted_csrf_token is None or facts.submitted_csrf_token == "":
        return SecurityDecision.deny(DenialReason.CSRF_HEADER_MISSING)
    if not verify_csrf_token(expected_csrf_token, facts.submitted_csrf_token):
        return SecurityDecision.deny(DenialReason.CSRF_TOKEN_INVALID)

    return SecurityDecision.allow(protection_required=True)


def request_host_denial_reason(
    *,
    scheme: object,
    host: object,
    policy: BrowserSecurityPolicy,
) -> DenialReason | None:
    """Validate one WSGI scheme/Host pair against the frozen origin allowlist.

    Safe, token-bearing bootstrap responses such as ``/auth/me`` also need this
    narrower check: they do not require an ``Origin`` header, but must never
    disclose a session CSRF token through an attacker-selected Host value.
    Reverse-proxy headers are deliberately not considered here; the deployment
    must normalize trusted proxy facts before Flask constructs the request.
    """

    request_origin = _canonical_request_origin(scheme, host)
    if request_origin is None:
        return DenialReason.INVALID_REQUEST_HOST
    if request_origin not in policy.allowed_origins:
        return DenialReason.REQUEST_ORIGIN_NOT_ALLOWED
    return None


def validate_session_cookie_config(
    config: Mapping[str, Any],
    *,
    production: bool,
    policy: SessionCookiePolicy = SessionCookiePolicy(),
) -> CookieConfigurationReport:
    """Validate Flask-compatible session-cookie settings without mutating them."""

    violations: set[CookieConfigViolation] = set()

    cookie_name = config.get("SESSION_COOKIE_NAME")
    if (
        not isinstance(cookie_name, str)
        or cookie_name != "session"
        or _COOKIE_NAME_RE.fullmatch(cookie_name) is None
    ):
        violations.add(CookieConfigViolation.COOKIE_NAME_INVALID)

    if config.get("SESSION_COOKIE_DOMAIN") is not None:
        violations.add(CookieConfigViolation.COOKIE_DOMAIN_NOT_HOST_ONLY)
    if config.get("SESSION_COOKIE_PATH") != "/":
        violations.add(CookieConfigViolation.COOKIE_PATH_INVALID)
    if config.get("SESSION_COOKIE_HTTPONLY") is not True:
        violations.add(CookieConfigViolation.COOKIE_HTTPONLY_REQUIRED)

    secure = config.get("SESSION_COOKIE_SECURE")
    if not isinstance(secure, bool):
        violations.add(CookieConfigViolation.COOKIE_SECURE_INVALID)
    elif production and not secure:
        violations.add(CookieConfigViolation.COOKIE_SECURE_REQUIRED)

    same_site = config.get("SESSION_COOKIE_SAMESITE")
    if not isinstance(same_site, str) or same_site.casefold() != policy.same_site.casefold():
        violations.add(CookieConfigViolation.COOKIE_SAMESITE_INVALID)

    if production:
        secret = _secret_key_bytes(config.get("SECRET_KEY"))
        if secret is None or len(secret) < policy.min_secret_key_bytes:
            violations.add(CookieConfigViolation.SECRET_KEY_TOO_SHORT)

        lifetime_seconds = _lifetime_seconds(config.get("PERMANENT_SESSION_LIFETIME"))
        if (
            lifetime_seconds is None
            or lifetime_seconds <= 0
            or lifetime_seconds > policy.max_permanent_lifetime_seconds
        ):
            violations.add(CookieConfigViolation.SESSION_LIFETIME_INVALID)

    ordered = tuple(sorted(violations, key=lambda item: item.value))
    return CookieConfigurationReport(production=bool(production), violations=ordered)


def require_session_cookie_config(
    config: Mapping[str, Any],
    *,
    production: bool,
    policy: SessionCookiePolicy = SessionCookiePolicy(),
) -> None:
    """Raise a stable non-secret startup error when the cookie gate fails."""

    report = validate_session_cookie_config(
        config,
        production=production,
        policy=policy,
    )
    if report.ok:
        return
    codes = ",".join(item.value for item in report.violations)
    raise BrowserSecurityConfigurationError(
        f"session cookie configuration failed: {codes}"
    )


def _is_valid_csrf_token(value: object) -> bool:
    return isinstance(value, str) and _CSRF_TOKEN_RE.fullmatch(value) is not None


def _normalize_allowed_origins(origins: Iterable[str]) -> frozenset[str]:
    try:
        candidates = tuple(origins)
    except TypeError as exc:
        raise BrowserSecurityConfigurationError(
            "allowed origins must be an iterable of absolute origins"
        ) from exc

    normalized: set[str] = set()
    for candidate in candidates:
        value = _canonicalize_origin(candidate)
        if value is None:
            raise BrowserSecurityConfigurationError(
                "allowed origins must be explicit http/https origins"
            )
        normalized.add(value)
    if not normalized:
        raise BrowserSecurityConfigurationError(
            "at least one browser origin must be configured"
        )
    return frozenset(normalized)


def _canonicalize_origin(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if _ASCII_CONTROL_OR_SPACE_RE.search(value) or any(
        marker in value for marker in (",", "?", "#")
    ):
        return None

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None

    scheme = parsed.scheme.casefold()
    if scheme not in _DEFAULT_PORTS:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None

    authority = _canonical_authority(parsed.hostname, port=port, scheme=scheme)
    if authority is None:
        return None
    return f"{scheme}://{authority}"


def _canonical_request_origin(scheme: object, host: object) -> str | None:
    if not isinstance(scheme, str) or not isinstance(host, str):
        return None
    if scheme != scheme.strip() or host != host.strip():
        return None
    normalized_scheme = scheme.casefold()
    if normalized_scheme not in _DEFAULT_PORTS:
        return None
    if _ASCII_CONTROL_OR_SPACE_RE.search(host) or any(
        value in host for value in (",", "/", "\\", "@", "?", "#")
    ):
        return None

    try:
        parsed = urlsplit(f"//{host}")
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None

    authority = _canonical_authority(
        parsed.hostname,
        port=port,
        scheme=normalized_scheme,
    )
    if authority is None:
        return None
    return f"{normalized_scheme}://{authority}"


def _canonical_authority(hostname: str, *, port: int | None, scheme: str) -> str | None:
    if not hostname or hostname.endswith(".") or "%" in hostname:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            normalized_host = hostname.encode("idna").decode("ascii").casefold()
        except (UnicodeError, ValueError):
            return None
        if (
            not normalized_host
            or len(normalized_host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label, re.ASCII) is None
                for label in normalized_host.split(".")
            )
        ):
            return None
    else:
        normalized_host = address.compressed.casefold()
        if address.version == 6:
            normalized_host = f"[{normalized_host}]"

    if port is None or port == _DEFAULT_PORTS[scheme]:
        return normalized_host
    return f"{normalized_host}:{port}"


def _secret_key_bytes(value: object) -> bytes | None:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    return None


def _lifetime_seconds(value: object) -> float | None:
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        return None
    if not math.isfinite(seconds):
        return None
    return seconds


__all__ = [
    "BrowserSecurityConfigurationError",
    "BrowserSecurityPolicy",
    "CSRF_HEADER_NAME",
    "CSRF_SESSION_KEY",
    "CSRF_TOKEN_BYTES",
    "CSRF_TOKEN_LENGTH",
    "CookieConfigViolation",
    "CookieConfigurationReport",
    "DEFAULT_AUTH_EXEMPT_PATHS",
    "DenialReason",
    "RequestSecurityFacts",
    "SecurityDecision",
    "SessionCookiePolicy",
    "evaluate_request_security",
    "issue_csrf_token",
    "require_session_cookie_config",
    "request_host_denial_reason",
    "rotate_csrf_token",
    "validate_session_cookie_config",
    "verify_csrf_token",
]
