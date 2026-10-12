from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.semantic_edge_routes import (
    register_semantic_edge_routes,
)
from code_assist_v1.knowledge_core.services.semantic_edge_correction import (
    LiftSemanticEdgeForEdgeCommand,
    SemanticEdgeCorrectionResult,
    SuppressSemanticEdgeCommand,
)


@dataclass
class _Service:
    suppress_command: SuppressSemanticEdgeCommand | None = None
    lift_command: LiftSemanticEdgeForEdgeCommand | None = None

    def suppress(self, command: SuppressSemanticEdgeCommand):
        self.suppress_command = command
        return SemanticEdgeCorrectionResult(
            command.operation_id,
            command.request_id,
            command.edge_id,
            "local",
            command.selected_space_id,
            str(uuid4()),
            "active",
            "inactive",
            False,
            1,
        )

    def lift_edge(self, command: LiftSemanticEdgeForEdgeCommand):
        self.lift_command = command
        return SemanticEdgeCorrectionResult(
            command.operation_id,
            command.request_id,
            command.edge_id,
            "local",
            command.selected_space_id,
            str(uuid4()),
            "lifted",
            "inactive",
            True,
            2,
        )


@pytest.mark.unit
def test_semantic_edge_routes_bind_exact_contract_and_infer_edge_kind() -> None:
    app = Flask(__name__)
    service = _Service()
    paths = register_semantic_edge_routes(
        app,
        service=service,
        principal_resolver=lambda: {"id": "member-1", "scope_epoch": 7},
    )
    space_id = str(uuid4())
    edge_id = str(uuid4())
    operation_id = str(uuid4())
    assert paths == (
        "/api/knowledge/v2/spaces/<space_id>/edges/<edge_id>/suppress",
        "/api/knowledge/v2/spaces/<space_id>/edges/<edge_id>/suppression/lift",
    )
    client = app.test_client()
    suppressed = client.post(
        f"/api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress",
        headers={"Idempotency-Key": operation_id},
        json={
            "expected_row_version": 3,
            "operation_id": operation_id,
            "reason": "Incorrect semantic relationship",
        },
    )
    assert suppressed.status_code == 200
    assert suppressed.headers["Cache-Control"] == "no-store, max-age=0"
    assert service.suppress_command is not None
    assert service.suppress_command.edge_kind == "auto"
    assert service.suppress_command.expected_row_version == 3
    assert service.suppress_command.principal.captured_scope_epoch == 7

    lift_operation_id = str(uuid4())
    lifted = client.post(
        f"/api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppression/lift",
        headers={"Idempotency-Key": lift_operation_id},
        json={
            "expected_row_version": 1,
            "operation_id": lift_operation_id,
            "reason": "Corrected detector policy",
        },
    )
    assert lifted.status_code == 202
    assert lifted.get_json()["reevaluation_queued"] is True
    assert service.lift_command is not None
    assert service.lift_command.edge_kind == "auto"
    assert service.lift_command.edge_id == edge_id


@pytest.mark.unit
def test_semantic_edge_routes_reject_ambiguous_or_noncanonical_requests() -> None:
    app = Flask(__name__)
    service = _Service()
    register_semantic_edge_routes(
        app,
        service=service,
        principal_resolver=lambda: {"id": "member-1", "scope_epoch": 1},
    )
    space_id = str(uuid4())
    edge_id = str(uuid4())
    operation_id = str(uuid4())
    response = app.test_client().post(
        f"/api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress",
        headers={"Idempotency-Key": str(uuid4())},
        json={"operation_id": operation_id, "reason": "wrong edge"},
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert service.suppress_command is None

    duplicate = app.test_client().post(
        f"/api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress",
        data=(
            '{"operation_id":"'
            + operation_id
            + '","operation_id":"'
            + operation_id
            + '","reason":"wrong edge"}'
        ),
        content_type="application/json",
    )
    assert duplicate.status_code == 400
    assert duplicate.get_json()["error"]["code"] == "VALIDATION_ERROR"
