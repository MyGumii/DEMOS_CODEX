"""AIK-036: owner-only token redisplay, audit without secrets and browser gates."""
import json
from uuid import uuid4

import pytest

from code_assist_v1.agent_integrations.routes import register
from code_assist_v1.agent_integrations.store import IntegrationStore
from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import service, connect, app_for, READ

pytestmark = pytest.mark.unit
PREFIX = "/api/integrations/v1"


def audit_count(store):
    with store.connection() as c:
        return c.execute("SELECT count(*) FROM integration_event WHERE kind='connection.token_viewed'").fetchone()[0]


def test_aik_036_existing_token_reveal_replay_no_secret_storage_or_renewal(service):
    conn = connect(service, auto_renew=True)
    before = service.store.require_connection(conn["id"])
    # Reopen an already-created ledger: existing grants need no migration/reissue.
    service.store = IntegrationStore(service.store.path, service.store.key)
    client = app_for(service).test_client()
    url = PREFIX + "/connections/" + conn["id"] + "/token"
    payload = {"operation_id": str(uuid4())}
    for _ in range(2):
        response = client.post(url, json=payload)
        assert response.status_code == 200
        assert response.get_json() == {"id": conn["id"], "token": conn["token"]}
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Agent-Token-Expires-At" not in response.headers
    assert audit_count(service.store) == 1
    assert service.store.require_connection(conn["id"]) == before
    assert conn["token"] not in client.get(PREFIX + "/connections").get_data(as_text=True)
    with service.store.connection() as c:
        assert conn["token"] not in "\n".join(c.iterdump())
        saved = c.execute("SELECT result_json FROM integration_operation WHERE operation_id=?", (payload["operation_id"],)).fetchone()[0]
        assert json.loads(saved) == {"id": conn["id"]}
    other = connect(service)
    assert client.post(PREFIX + "/connections/" + other["id"] + "/token", json=payload).status_code == 409
    assert audit_count(service.store) == 1
    service.store.revoke("alice", conn["id"], str(uuid4()), lambda: None)
    replay = client.post(url, json=payload)
    assert replay.status_code == 403 and conn["token"] not in replay.get_data(as_text=True)


@pytest.mark.parametrize("boundary", ["foreign_owner", "expired", "revoked", "epoch", "archived", "inactive", "key_changed"])
def test_aik_036_token_reveal_rechecks_current_authority_and_key(service, boundary):
    conn = connect(service)
    owner = ["alice"]
    app = app_for(service)
    # Use a fresh wrapper resolver for the cross-account case.
    if boundary == "foreign_owner":
        from flask import Flask
        app = Flask(__name__)
        register(app, service, lambda: {"id": owner[0]})
        owner[0] = "bob"
    elif boundary == "expired":
        with service.store.connection() as c:
            c.execute("UPDATE agent_connection SET expires_at=0 WHERE id=?", (conn["id"],))
    elif boundary == "revoked":
        service.store.revoke("alice", conn["id"], str(uuid4()), lambda: None)
    elif boundary == "epoch":
        service.bridge.epoch += 1
    elif boundary == "archived":
        service.bridge.archived.add(READ)
    elif boundary == "inactive":
        service.bridge.active = False
    elif boundary == "key_changed":
        service.store.key = b"z" * 32
    response = app.test_client().post(PREFIX + "/connections/" + conn["id"] + "/token",
        json={"operation_id": str(uuid4())})
    expected = {"foreign_owner": 404, "expired": 403, "revoked": 403, "epoch": 403,
                "archived": 404, "inactive": 401, "key_changed": 503}
    assert response.status_code == expected[boundary]
    assert "token" not in response.get_json() and conn["token"] not in response.get_data(as_text=True)
    assert audit_count(service.store) == 0
    if boundary == "key_changed":
        assert response.get_json()["error"]["code"] == "TOKEN_UNAVAILABLE"


def test_aik_036_real_browser_session_csrf_and_no_machine_token_reveal(service):
    from code_assist_v1 import auth
    from code_assist_v1.tests.knowledge_spaces.integration.test_auth_flask_security import _make_app, _create_user, _login, _ORIGIN
    alice, password = _create_user()
    bob, bob_password = _create_user()
    app = _make_app()
    register(app, service, lambda: {"id": "alice" if auth.current_user_id() == alice else "bob"})
    conn = connect(service)
    url = PREFIX + "/connections/" + conn["id"] + "/token"
    payload = {"operation_id": str(uuid4())}
    client = app.test_client()
    assert client.post(url, base_url=_ORIGIN, json=payload, headers={"Origin": _ORIGIN}).status_code != 200
    assert client.post(url, base_url=_ORIGIN, json=payload,
        headers={"Authorization": "Bearer " + conn["token"]}).status_code != 200
    login = _login(client, alice, password)
    headers = {"Origin": _ORIGIN, "X-CSRF-Token": login.get_json()["csrf_token"]}
    assert client.post(url, base_url=_ORIGIN, json=payload, headers={"Origin": _ORIGIN}).status_code == 403
    assert client.post(url, base_url=_ORIGIN, json=payload,
        headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.post(url, base_url="https://evil.example", json=payload, headers=headers).status_code == 403
    assert client.post(url, base_url=_ORIGIN, json=payload,
        headers={**headers, "Authorization": "Bearer " + conn["token"]}).status_code == 403
    assert client.post(url, base_url=_ORIGIN, json={**payload, "user_id": "alice"}, headers=headers).status_code == 400
    assert client.get(url, base_url=_ORIGIN).status_code == 405
    assert audit_count(service.store) == 0
    response = client.post(url, base_url=_ORIGIN, json=payload, headers=headers)
    assert response.status_code == 200 and response.get_json()["token"] == conn["token"]
    assert audit_count(service.store) == 1
    foreign = app.test_client()
    login = _login(foreign, bob, bob_password)
    denied = foreign.post(url, base_url=_ORIGIN, json={"operation_id": str(uuid4())},
        headers={"Origin": _ORIGIN, "X-CSRF-Token": login.get_json()["csrf_token"]})
    assert denied.status_code == 404 and audit_count(service.store) == 1
