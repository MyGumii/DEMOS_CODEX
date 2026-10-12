from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.temporal import TemporalService
from code_assist_v1.knowledge_core.temporal_routes import register_temporal_routes
from code_assist_v1.tests.knowledge_spaces.unit.test_item_lifecycle_service import (
    _flags,
    _seed,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("8cb2e297-6015-42c8-8f19-50a826b5b779")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


@pytest.mark.scenario("TIME-002", "TIME-004", "TIME-005", "TIME-006", "TIME-015")
def test_temporal_v2_routes_are_exact_and_drive_the_real_service(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    routes = register_temporal_routes(
        app,
        service=TemporalService(fixture.factory, ScopeResolver(_flags())),
        principal_resolver=lambda: {
            "id": "editor",
            "scope_epoch": fixture.epochs["editor"],
        },
    )
    assert routes == (
        "/api/knowledge/v2/spaces/<space_id>/items/<item_id>/temporal",
        "/api/knowledge/v2/spaces/<space_id>/items/<item_id>/temporal-assertions",
        "/api/knowledge/v2/spaces/<space_id>/items/<item_id>/temporal-assertions/<assertion_id>/revoke",
        "/api/knowledge/v2/spaces/<space_id>/items/<item_id>/verification-attestations",
        "/api/knowledge/v2/spaces/<space_id>/items/<item_id>/verification-attestations/<attestation_id>/revoke",
    )
    base = (
        f"/api/knowledge/v2/spaces/{fixture.space_id}/items/{fixture.item_id}"
    )
    operation_id = _uuid("route-assert-operation")
    client = app.test_client()
    response = client.post(
        f"{base}/temporal-assertions",
        headers={"Idempotency-Key": operation_id},
        json={
            "assertion_kind": "information_effective",
            "evidence_reference": "approved policy record",
            "evidence_sha256": sha256(b"route evidence").hexdigest(),
            "expected_head_event_id": None,
            "expected_head_generation": 0,
            "expected_revision_id": fixture.revision_id,
            "operation_id": operation_id,
            "origin": "user",
            "value": {
                "precision": "date",
                "timezone": "Asia/Seoul",
                "value": "2026-08-25",
            },
        },
    )
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    asserted = response.get_json()
    assert asserted["assertion_kind"] == "information_effective"
    assert asserted["value"] == {
        "precision": "date",
        "timezone": "Asia/Seoul",
        "value": "2026-08-25",
    }

    read = client.get(f"{base}/temporal?after_audit_id=0&limit=10")
    assert read.status_code == 200
    payload = read.get_json()
    assert payload["information_effective_at"] == asserted["value"]
    assert payload["history"][0]["event_id"] == asserted["assertion_id"]
    assert payload["next_cursor"] is None


@pytest.mark.scenario("TIME-006", "TIME-015")
def test_temporal_routes_fail_closed_for_auth_shape_and_idempotency(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_temporal_routes(
        app,
        service=TemporalService(fixture.factory, ScopeResolver(_flags())),
        principal_resolver=lambda: None,
    )
    base = (
        f"/api/knowledge/v2/spaces/{fixture.space_id}/items/{fixture.item_id}"
    )
    client = app.test_client()
    unauthenticated = client.get(f"{base}/temporal")
    assert unauthenticated.status_code == 401
    assert unauthenticated.get_json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    malformed = client.post(
        f"{base}/temporal-assertions",
        data=b'{"operation_id":"x","operation_id":"y"}',
        content_type="application/json",
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_temporal_routes_reject_duplicate_registration() -> None:
    app = Flask(__name__)

    class Service:
        assert_temporal = attest = read = revoke_assertion = revoke_verification = (
            lambda *args, **kwargs: None
        )

    register_temporal_routes(
        app,
        service=Service(),
        principal_resolver=lambda: {"id": "editor", "scope_epoch": 1},
    )
    with pytest.raises(RuntimeError):
        register_temporal_routes(
            app,
            service=Service(),
            principal_resolver=lambda: {"id": "editor", "scope_epoch": 1},
        )
