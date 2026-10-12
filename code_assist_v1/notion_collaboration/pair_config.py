"""Pure, fail-closed configuration for an isolated Agent/Notion host pair.

This module intentionally does not read ``os.environ`` itself.  A caller must
pass an explicit mapping, making configuration tests independent of live or
production state.  It also separates safe peer navigation from mutation
Origin/CSRF authority: the peer Origin is never accepted for an unsafe request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from typing import Mapping
from urllib.parse import urlsplit


PAIR_ENABLED_KEY = "CODE_ASSIST_PAIR_ENABLED"
PAIR_ENVIRONMENT_KEY = "CODE_ASSIST_PAIR_ENVIRONMENT"
CODE_ORIGIN_KEY = "CODE_ASSIST_CODE_ORIGIN"
NOTION_ORIGIN_KEY = "CODE_ASSIST_NOTION_ORIGIN"
PAIR_COOKIE_NAME_KEY = "CODE_ASSIST_PAIR_COOKIE_NAME"
PAIR_SECRET_KEY = "CODE_ASSIST_PAIR_SECRET"

PAIR_ENVIRONMENT = "development_synthetic"
PRODUCTION_PORTS = frozenset({10011, 10012})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})
COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", re.ASCII)
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SAFE_NAVIGATION_METHODS = frozenset({"GET", "HEAD"})


class PairHost(str, Enum):
    CODE = "code"
    NOTION = "notion"


@dataclass(frozen=True, slots=True)
class PairConfiguration:
    environment: str
    code_origin: str
    notion_origin: str
    cookie_name: str
    secret_fingerprint: str
    secure_cookie: bool
    _secret_key: str = field(repr=False, compare=False)

    @property
    def account_mutation_authority(self) -> PairHost:
        return PairHost.CODE

    def origin(self, host: PairHost) -> str:
        return self.code_origin if host is PairHost.CODE else self.notion_origin

    def mutation_origins(self, host: PairHost) -> frozenset[str]:
        """Return only the host's own Origin; peer navigation grants no mutation authority."""

        return frozenset({self.origin(host)})

    def peer_navigation_url(self, source: PairHost, path: str = "/", *, method: str = "GET") -> str | None:
        method = method.upper()
        if method not in SAFE_NAVIGATION_METHODS or not _safe_navigation_path(path):
            return None
        target = self.notion_origin if source is PairHost.CODE else self.code_origin
        return target + path

    def local_logout_path(self, host: PairHost) -> str:
        del host
        return "/api/code/auth/logout"

    def peer_logout_url(self, host: PairHost) -> None:
        """Cross-Origin logout fan-out is intentionally absent.

        Both hosts clear the same host-scoped cookie locally.  An unsafe peer
        POST would violate the independent Origin/CSRF contract.
        """

        del host
        return None

    def account_mutation_allowed(self, host: PairHost) -> bool:
        return host is PairHost.CODE

    def flask_session_config(self, host: PairHost) -> dict[str, object]:
        del host
        return {
            "SECRET_KEY": self._secret_key,
            "SESSION_COOKIE_NAME": self.cookie_name,
            "SESSION_COOKIE_DOMAIN": None,
            "SESSION_COOKIE_PATH": "/",
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": self.secure_cookie,
        }

    def public_contract(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "code_origin": self.code_origin,
            "notion_origin": self.notion_origin,
            "cookie_name": self.cookie_name,
            "secret_fingerprint": self.secret_fingerprint,
            "secure_cookie": self.secure_cookie,
            "account_mutation_authority": self.account_mutation_authority.value,
            "notion_account_mutation_allowed": False,
            "peer_logout_fanout": False,
            "mutation_origins": {
                PairHost.CODE.value: sorted(self.mutation_origins(PairHost.CODE)),
                PairHost.NOTION.value: sorted(self.mutation_origins(PairHost.NOTION)),
            },
        }


@dataclass(frozen=True, slots=True)
class PairConfigurationLoad:
    state: str
    configuration: PairConfiguration | None
    reason_codes: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        return self.state == "enabled" and self.configuration is not None


def _canonical_origin(value: str) -> tuple[str, str, int, str] | None:
    if not value or value != value.strip() or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    hostname = hostname.lower()
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical = f"{scheme}://{rendered_host}:{port}"
    if value.rstrip("/") != canonical:
        return None
    return canonical, hostname, port, scheme


def _safe_navigation_path(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\x00" in value:
        return False
    parsed = urlsplit(value)
    return not parsed.scheme and not parsed.netloc and not parsed.fragment


def _secret_fingerprint(secret: str) -> str:
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


def load_pair_configuration(environ: Mapping[str, str]) -> PairConfigurationLoad:
    """Validate one explicit development-pair environment without side effects."""

    raw_enabled = str(environ.get(PAIR_ENABLED_KEY, "")).strip().lower()
    if raw_enabled in FALSE_VALUES:
        return PairConfigurationLoad("disabled", None, ("PAIR_DISABLED",))
    if raw_enabled not in TRUE_VALUES:
        return PairConfigurationLoad("invalid", None, ("PAIR_ENABLED_INVALID",))

    reasons: set[str] = set()
    environment = str(environ.get(PAIR_ENVIRONMENT_KEY, ""))
    if environment != PAIR_ENVIRONMENT:
        reasons.add("PAIR_ENVIRONMENT_INVALID")

    code = _canonical_origin(str(environ.get(CODE_ORIGIN_KEY, "")))
    notion = _canonical_origin(str(environ.get(NOTION_ORIGIN_KEY, "")))
    if code is None:
        reasons.add("CODE_ORIGIN_INVALID")
    if notion is None:
        reasons.add("NOTION_ORIGIN_INVALID")
    if code is not None and notion is not None:
        if code[0] == notion[0]:
            reasons.add("PAIR_ORIGINS_NOT_DISTINCT")
        if code[1] != notion[1]:
            reasons.add("PAIR_HOSTNAME_MISMATCH")
        if code[3] != notion[3]:
            reasons.add("PAIR_SCHEME_MISMATCH")
        if code[1] not in LOOPBACK_HOSTS or notion[1] not in LOOPBACK_HOSTS:
            reasons.add("DEVELOPMENT_HOST_NOT_LOOPBACK")
        if code[2] in PRODUCTION_PORTS or notion[2] in PRODUCTION_PORTS:
            reasons.add("PRODUCTION_PORT_FORBIDDEN")

    cookie_name = str(environ.get(PAIR_COOKIE_NAME_KEY, ""))
    if not COOKIE_NAME_RE.fullmatch(cookie_name):
        reasons.add("PAIR_COOKIE_NAME_INVALID")
    elif cookie_name == "session":
        reasons.add("PRODUCTION_COOKIE_NAME_FORBIDDEN")

    secret = str(environ.get(PAIR_SECRET_KEY, ""))
    if len(secret.encode("utf-8")) < 32:
        reasons.add("PAIR_SECRET_TOO_SHORT")
    if secret.strip().lower() in {"change-me", "development-secret", "secret"}:
        reasons.add("PAIR_SECRET_PLACEHOLDER")

    if reasons:
        return PairConfigurationLoad("invalid", None, tuple(sorted(reasons)))
    assert code is not None and notion is not None
    configuration = PairConfiguration(
        environment=environment,
        code_origin=code[0],
        notion_origin=notion[0],
        cookie_name=cookie_name,
        secret_fingerprint=_secret_fingerprint(secret),
        secure_cookie=code[3] == "https",
        _secret_key=secret,
    )
    return PairConfigurationLoad("enabled", configuration, ())


__all__ = [
    "CODE_ORIGIN_KEY",
    "NOTION_ORIGIN_KEY",
    "PAIR_COOKIE_NAME_KEY",
    "PAIR_ENABLED_KEY",
    "PAIR_ENVIRONMENT_KEY",
    "PAIR_SECRET_KEY",
    "PairConfiguration",
    "PairConfigurationLoad",
    "PairHost",
    "load_pair_configuration",
]

