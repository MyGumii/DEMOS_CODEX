from __future__ import annotations

import json

import pytest

from code_assist_v1.notion_collaboration.pair_config import (
    CODE_ORIGIN_KEY,
    NOTION_ORIGIN_KEY,
    PAIR_COOKIE_NAME_KEY,
    PAIR_ENABLED_KEY,
    PAIR_ENVIRONMENT_KEY,
    PAIR_SECRET_KEY,
    PairHost,
    load_pair_configuration,
)


_SECRET = "d0a-pair-test-only-9f4d31c8b7526a0e7c1942ab"


def _valid(**overrides: str) -> dict[str, str]:
    value = {
        PAIR_ENABLED_KEY: "1",
        PAIR_ENVIRONMENT_KEY: "development_synthetic",
        CODE_ORIGIN_KEY: "http://localhost:41011",
        NOTION_ORIGIN_KEY: "http://localhost:41012",
        PAIR_COOKIE_NAME_KEY: "code_assist_dev_pair_session",
        PAIR_SECRET_KEY: _SECRET,
    }
    value.update(overrides)
    return value


@pytest.mark.unit
def test_missing_or_explicitly_disabled_configuration_has_no_fallback() -> None:
    missing = load_pair_configuration({})
    disabled = load_pair_configuration({PAIR_ENABLED_KEY: "off"})
    assert missing == disabled
    assert missing.state == "disabled"
    assert missing.configuration is None
    assert missing.reason_codes == ("PAIR_DISABLED",)


@pytest.mark.unit
def test_valid_pair_shares_only_cookie_secret_and_subject_boundary() -> None:
    loaded = load_pair_configuration(_valid())
    assert loaded.enabled
    pair = loaded.configuration
    assert pair is not None
    assert pair.account_mutation_authority is PairHost.CODE
    assert pair.account_mutation_allowed(PairHost.CODE)
    assert not pair.account_mutation_allowed(PairHost.NOTION)
    assert pair.flask_session_config(PairHost.CODE) == pair.flask_session_config(PairHost.NOTION)
    assert pair.flask_session_config(PairHost.CODE)["SESSION_COOKIE_NAME"] != "session"
    assert pair.flask_session_config(PairHost.CODE)["SESSION_COOKIE_DOMAIN"] is None


@pytest.mark.unit
def test_peer_navigation_never_grants_unsafe_mutation_origin() -> None:
    pair = load_pair_configuration(_valid()).configuration
    assert pair is not None
    assert pair.peer_navigation_url(PairHost.CODE, "/workspace") == "http://localhost:41012/workspace"
    assert pair.peer_navigation_url(PairHost.NOTION, "/") == "http://localhost:41011/"
    assert pair.peer_navigation_url(PairHost.CODE, "/api/notion/pages", method="POST") is None
    assert pair.peer_navigation_url(PairHost.CODE, "//attacker.example/path") is None
    assert pair.mutation_origins(PairHost.CODE) == {"http://localhost:41011"}
    assert pair.mutation_origins(PairHost.NOTION) == {"http://localhost:41012"}


@pytest.mark.unit
def test_logout_is_local_csrf_protected_with_no_cross_origin_fanout() -> None:
    pair = load_pair_configuration(_valid()).configuration
    assert pair is not None
    assert pair.local_logout_path(PairHost.CODE) == "/api/code/auth/logout"
    assert pair.local_logout_path(PairHost.NOTION) == "/api/code/auth/logout"
    assert pair.peer_logout_url(PairHost.CODE) is None
    assert pair.peer_logout_url(PairHost.NOTION) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({PAIR_ENABLED_KEY: "sometimes"}, "PAIR_ENABLED_INVALID"),
        ({PAIR_ENVIRONMENT_KEY: "production"}, "PAIR_ENVIRONMENT_INVALID"),
        ({CODE_ORIGIN_KEY: "http://localhost:10011"}, "PRODUCTION_PORT_FORBIDDEN"),
        ({NOTION_ORIGIN_KEY: "http://localhost:10012"}, "PRODUCTION_PORT_FORBIDDEN"),
        ({NOTION_ORIGIN_KEY: "http://127.0.0.1:41012"}, "PAIR_HOSTNAME_MISMATCH"),
        ({NOTION_ORIGIN_KEY: "https://localhost:41012"}, "PAIR_SCHEME_MISMATCH"),
        ({CODE_ORIGIN_KEY: "http://localhost:41011/path"}, "CODE_ORIGIN_INVALID"),
        ({NOTION_ORIGIN_KEY: "http://user@localhost:41012"}, "NOTION_ORIGIN_INVALID"),
        ({PAIR_COOKIE_NAME_KEY: "session"}, "PRODUCTION_COOKIE_NAME_FORBIDDEN"),
        ({PAIR_COOKIE_NAME_KEY: "bad cookie"}, "PAIR_COOKIE_NAME_INVALID"),
        ({PAIR_SECRET_KEY: "short"}, "PAIR_SECRET_TOO_SHORT"),
    ],
)
def test_invalid_pair_configuration_fails_closed(overrides: dict[str, str], code: str) -> None:
    loaded = load_pair_configuration(_valid(**overrides))
    assert loaded.state == "invalid"
    assert loaded.configuration is None
    assert code in loaded.reason_codes


@pytest.mark.unit
def test_secret_is_redacted_from_repr_and_public_contract() -> None:
    pair = load_pair_configuration(_valid()).configuration
    assert pair is not None
    rendered = repr(pair) + json.dumps(pair.public_contract(), sort_keys=True)
    assert _SECRET not in rendered
    assert pair.secret_fingerprint in rendered
    assert "10011" not in rendered
    assert "10012" not in rendered


@pytest.mark.unit
def test_https_pair_sets_secure_cookie_and_preserves_exact_origins() -> None:
    pair = load_pair_configuration(
        _valid(
            **{
                CODE_ORIGIN_KEY: "https://localhost:41443",
                NOTION_ORIGIN_KEY: "https://localhost:42443",
            }
        )
    ).configuration
    assert pair is not None
    assert pair.secure_cookie
    assert pair.flask_session_config(PairHost.CODE)["SESSION_COOKIE_SECURE"] is True

