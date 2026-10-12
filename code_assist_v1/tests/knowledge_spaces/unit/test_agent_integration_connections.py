"""AIK-031..033: renewal, separate Notion roots/tree paths and browser OAuth."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.agent_integrations import store as ledger, service as service_module
from code_assist_v1.agent_integrations.contracts import submission_input
from code_assist_v1.agent_integrations.notion import LegacyNotionConnector
from code_assist_v1.tests.knowledge_spaces.unit.test_agent_integrations import (
    service, connect, app_for, submission,
)

pytestmark = pytest.mark.unit
PREFIX = "/api/integrations/v1"
DAY = 86400000


def test_aik_031_only_successful_machine_use_renews_active_grant(service, monkeypatch):
    clock = [1800000000000]
    monkeypatch.setattr(ledger, "now_ms", lambda: clock[0])
    monkeypatch.setattr(service_module, "now_ms", lambda: clock[0])
    connection = connect(service, auto_renew=True)
    client = app_for(service).test_client()
    headers = {"Authorization": "Bearer " + connection["token"]}
    expiry = lambda: service.store.require_connection(connection["id"])["expires_at"]
    original = expiry()
    clock[0] += DAY
    assert client.get(PREFIX + "/status").status_code == 200
    assert expiry() == original  # Browser inspection and worker auth do not renew.
    service.authorize_connection("alice", connection["id"])
    assert expiry() == original
    denied = client.post(PREFIX + "/knowledge/ask", headers=headers,
        json={"operation_id": str(uuid4()), "message": "No ask scope"})
    assert denied.status_code == 403 and expiry() == original
    assert client.post(PREFIX + "/knowledge/search", headers=headers, json={}).status_code == 400
    assert expiry() == original
    response = client.get(PREFIX + "/status", headers=headers)
    assert response.status_code == 200
    assert expiry() == original + DAY
    assert int(response.headers["X-Agent-Token-Expires-At"]) == expiry()
    clock[0] = expiry()
    assert client.get(PREFIX + "/status", headers=headers).status_code == 401
    assert expiry() == original + DAY  # No resurrection, even with the same secret.
    with service.store.connection() as c:
        assert c.execute("SELECT count(*) FROM integration_event WHERE kind='connection.renewed'").fetchone()[0] == 1


def test_aik_031_existing_token_mode_change_is_owned_idempotent_and_keeps_secret(service, monkeypatch):
    clock = [1800000000000]
    monkeypatch.setattr(ledger, "now_ms", lambda: clock[0])
    monkeypatch.setattr(service_module, "now_ms", lambda: clock[0])
    connection = connect(service)
    client = app_for(service).test_client()
    headers = {"Authorization": "Bearer " + connection["token"]}
    before = service.store.require_connection(connection["id"])
    url = PREFIX + "/connections/" + connection["id"] + "/renewal"
    payload = dict(operation_id=str(uuid4()), expected_version=before["row_version"], auto_renew=True)
    assert client.post(url, json=payload, headers=headers).status_code == 403
    first = client.post(url, json=payload)
    assert first.status_code == 200
    assert client.post(url, json=payload).get_json() == first.get_json()
    after = service.store.require_connection(connection["id"])
    assert after["token_hash"] == before["token_hash"] and after["expires_at"] == before["expires_at"]
    clock[0] += DAY
    assert client.get(PREFIX + "/status", headers=headers).status_code == 200
    current = service.store.require_connection(connection["id"])
    assert current["expires_at"] == before["expires_at"] + DAY
    assert client.post(url, json={**payload, "operation_id":str(uuid4())}).status_code == 409
    assert client.post(url, json=dict(operation_id=str(uuid4()), expected_version=current["row_version"], auto_renew=False)).status_code == 200
    clock[0] += DAY
    assert client.get(PREFIX + "/status", headers=headers).status_code == 200
    assert service.store.require_connection(connection["id"])["expires_at"] == current["expires_at"]
    service.store.revoke("alice", connection["id"], str(uuid4()), lambda: None)
    assert client.get(PREFIX + "/status", headers=headers).status_code == 401
    assert client.post(url, json=payload).status_code == 403


def test_aik_032_bound_notion_tree_nested_target_and_parallel_root_isolation(tmp_path, monkeypatch):
    from code_assist_v1 import notion_store as ns
    roots = [tmp_path / "second-brain", tmp_path / "notion"]
    for root in roots:
        root.mkdir()
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(roots[0]))
    monkeypatch.delenv("CODE_ASSIST_I0_COLLABORATIVE_SHARING", raising=False)
    local_page = ns.create_page("alice", title="Second Brain local page")["page"]["id"]
    with ns.bind_user_data_root(roots[1]):
        parent = ns.create_page("alice", title="Projects")["page"]["id"]
        child = ns.create_page("alice", title="Alpha", parent_id=parent)["page"]["id"]
        nested = ns.create_page("alice", title="Records", parent_id=child)["page"]["id"]
        foreign = ns.create_page("bob", title="Private")["page"]["id"]
    connector = LegacyNotionConnector(roots[1])
    targets = connector.targets({"id":"alice"})["items"]
    indexed = {p["id"]:p for p in targets}
    assert indexed[nested]["path"] == "Projects / Alpha / Records"
    assert indexed[nested]["depth"] == 2 and indexed[nested]["parent_id"] == child
    assert local_page not in indexed and foreign not in indexed
    run = dict(id=str(uuid4()), row_version=1, state="queued",
        submission=submission_input(submission()), evidence=[], draft=None)
    result = connector.record({"id":"alice"}, nested, run, lambda:None)
    assert connector.record({"id":"alice"}, nested, run, lambda:None)["document_id"] == result["document_id"]
    with ns.bind_user_data_root(roots[1]):
        stored = ns.get_page("alice", result["document_id"])
        assert stored["page"]["parent_id"] == nested
        assert stored["blocks"][0]["type"] == "heading1"
    assert [p["id"] for p in ns.list_pages("alice")] == [local_page]
    def read_bound(which):
        with ns.bind_user_data_root(roots[which]):
            return which, {p["id"] for p in ns.list_pages("alice")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for which, ids in pool.map(read_bound, [0, 1] * 12):
            assert (local_page in ids) == (which == 0)
            assert (nested in ids) == (which == 1)
    with pytest.raises(FileNotFoundError, match="permission denied"):
        connector.record({"id":"bob"}, nested, run, lambda:None)
    assert ns._db_path("alice").startswith(str(roots[0]))


def test_aik_032_bad_root_never_falls_back_or_creates_directory(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        LegacyNotionConnector(missing)
    with pytest.raises(ValueError):
        LegacyNotionConnector("relative/root")
    assert not missing.exists()


def test_aik_033_browser_outlook_flow_reuses_graph_and_never_syncs(service):
    calls = []
    expected = "http://localhost" + PREFIX + "/outlook/callback"
    cfg = {"configured":True, "redirect_uri":expected, "missing":[]}
    graph = SimpleNamespace(
        connection_status=lambda owner, **kw: dict(authenticated=False, configuration=cfg, account={}),
        public_configuration=lambda:cfg,
        begin_authorization=lambda owner: calls.append(("begin", owner)) or "https://login.microsoftonline.com/fixture?state=one",
        complete_authorization=lambda owner, query: calls.append(("complete",owner,query)),
        disconnect=lambda owner:calls.append(("disconnect",owner)),
    )
    service.outlook = SimpleNamespace(graph=graph)
    client = app_for(service).test_client()
    connection = connect(service)
    headers = {"Authorization":"Bearer " + connection["token"]}
    for suffix, method in [("status","get"),("connect","post"),("callback","get"),("disconnect","post")]:
        assert getattr(client,method)(PREFIX + "/outlook/" + suffix,headers=headers).status_code == 403
    status = client.get(PREFIX + "/outlook/status").get_json()
    assert status["enabled"] and status["redirect_matches"]
    response = client.post(PREFIX + "/outlook/connect",json={})
    assert response.status_code == 200
    assert response.get_json()["authorization_url"].startswith("https://login.microsoftonline.com/")
    response = client.get(PREFIX + "/outlook/callback?code=fixture&state=one")
    assert response.status_code == 303 and response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Location"] == "/?agent_outlook=connected"
    assert client.post(PREFIX + "/outlook/disconnect",json={}).get_json() == {"disconnected":True}
    assert calls == [("begin","alice"),("complete","alice",{"code":"fixture","state":"one"}),("disconnect","alice")]
    cfg["redirect_uri"] = "http://localhost:10322/foreign-callback"
    assert client.post(PREFIX + "/outlook/connect",json={}).status_code == 503
    assert len(calls) == 3


def test_aik_033_missing_outlook_configuration_explained_without_starting_oauth(service):
    client = app_for(service).test_client()
    assert client.get(PREFIX + "/outlook/status").get_json() == dict(enabled=False, authenticated=False)
    assert client.post(PREFIX + "/outlook/connect",json={}).status_code == 503
    service.outlook = SimpleNamespace(graph=SimpleNamespace(public_configuration=lambda:{"configured":False}))
    response = client.post(PREFIX + "/outlook/connect",json={})
    assert response.status_code == 503 and response.get_json()["error"]["code"] == "OUTLOOK_SETUP_REQUIRED"


def test_aik_033_oauth_failure_does_not_echo_code_or_state(service):
    from code_assist_v1.outlook_graph import GraphOAuthError
    def reject(owner, query):
        raise GraphOAuthError("private provider diagnostic with code secret")
    service.outlook = SimpleNamespace(graph=SimpleNamespace(complete_authorization=reject))
    response = app_for(service).test_client().get(PREFIX + "/outlook/callback?code=secret&state=invalid")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "OUTLOOK_OAUTH_FAILED"
    assert "secret" not in response.get_data(as_text=True)
    assert response.headers["Referrer-Policy"] == "no-referrer"
