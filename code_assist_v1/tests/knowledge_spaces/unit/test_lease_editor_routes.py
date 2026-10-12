from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from flask import Flask
import pytest

from code_assist_v1.knowledge_core.canonical_routes import register_canonical_routes
from code_assist_v1.knowledge_core.composition import (
    CanonicalRouteBindings,
    HOST_PROFILES,
    KnowledgeCoreContainer,
    ServiceRegistry,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags, HostKind
from code_assist_v1.knowledge_core.routes import register_routes
from code_assist_v1.knowledge_core.services.editing import (
    HeartbeatLeaseResult,
    ItemEditResult,
    LeaseAcquireResult,
    LeaseHeldError,
    LeaseMutationResult,
    LeaseRevisionConflictError,
)
from code_assist_v1.knowledge_core.services.lifecycle import (
    DeactivateItemResult,
    RestoreItemResult,
)
from code_assist_v1.knowledge_core.services.purge import (
    PurgeItemResult,
    PurgePreviewResult,
)


def _uuid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"p5.5-route:{label}"))


SPACE_ID = _uuid("space")
ITEM_ID = _uuid("item")
SESSION_ID = _uuid("session")
LEASE_ID = _uuid("lease")
BASE_REVISION_ID = _uuid("base")
NEW_REVISION_ID = _uuid("new")
TOKEN = "A" * 43


class _Dependency:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: SimpleNamespace()


class _Executor:
    def complete(self, _context: object) -> object:
        raise AssertionError("not used")


class _Summarizer:
    def summarize(self, **_kwargs: object) -> object:
        raise AssertionError("not used")


class _Editing:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error: Exception | None = None

    def _record(self, name: str, command: object) -> None:
        self.calls.append((name, command))
        if self.error is not None:
            raise self.error

    def acquire(self, command: object) -> LeaseAcquireResult:
        self._record("acquire", command)
        return LeaseAcquireResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            lease_id=LEASE_ID,
            fencing_generation=7,
            base_revision_id=BASE_REVISION_ID,
            expires_at="2026-08-14T12:05:00.000Z",
            absolute_expires_at="2026-08-14T14:00:00.000Z",
        )
    def heartbeat(self, command: object) -> HeartbeatLeaseResult:
        self._record("heartbeat", command)
        return HeartbeatLeaseResult(
            request_id=command.request_id,
            lease_id=command.lease_id,
            fencing_generation=command.fencing_generation,
            expires_at="2026-08-14T12:05:30.000Z",
        )

    def release(self, command: object) -> LeaseMutationResult:
        self._record("release", command)
        return LeaseMutationResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            status="released",
            resource_id=command.item_id,
            row_version=12,
        )

    def force_release(self, command: object) -> LeaseMutationResult:
        self._record("force_release", command)
        return LeaseMutationResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            status="force_released",
            resource_id=command.item_id,
            row_version=12,
        )

    def save(self, command: object) -> ItemEditResult:
        self._record("save", command)
        return ItemEditResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            item_id=command.item_id,
            revision_id=NEW_REVISION_ID,
            row_version=12,
        )


class _Purge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def preview(self, command: object) -> PurgePreviewResult:
        self.calls.append(("preview", command))
        return PurgePreviewResult(
            request_id=command.request_id,
            item_id=command.item_id,
            eligible=True,
            purge_eligible_at="2026-08-14T00:00:00.000Z",
            retained_categories=("item_tombstone", "revision_metadata"),
            deleted_categories=("revision_bodies",),
            sizes={
                "artifact_body_bytes": 0,
                "artifact_body_count": 0,
                "candidate_blob_bytes": 0,
                "candidate_blob_count": 0,
                "extraction_body_bytes": 0,
                "extraction_body_count": 0,
                "revision_body_bytes": 12,
                "revision_body_count": 1,
            },
            blocking_references=(),
            preview_token="v1." + "A" * 80,
            confirmation_text=f"PURGE {command.item_id}",
        )

    def purge(self, command: object) -> PurgeItemResult:
        self.calls.append(("purge", command))
        return PurgeItemResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            status="purged_tombstone",
            resource_id=command.item_id,
            row_version=4,
        )


class _Lifecycle:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def deactivate(self, command: object) -> DeactivateItemResult:
        self.calls.append(("deactivate", command))
        return DeactivateItemResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            item_id=command.item_id,
            row_version=command.expected_item_row_version + 1,
            deactivated_at="2026-08-14T00:00:00.000Z",
            deactivated_by="Editor",
            purge_eligible_at="2026-11-12T00:00:00.000Z",
            edge_counts={
                "active_incoming": 0,
                "active_outgoing": 0,
                "inactive_incoming": 0,
                "inactive_outgoing": 0,
                "total": 0,
            },
            reference_counts={
                "active_incoming": 0,
                "active_outgoing": 0,
                "conversation_pins": 0,
                "inactive_incoming": 0,
                "inactive_outgoing": 0,
                "total": 0,
            },
        )

    def restore(self, command: object) -> RestoreItemResult:
        self.calls.append(("restore", command))
        return RestoreItemResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            item_id=command.item_id,
            revision_id=BASE_REVISION_ID,
            row_version=command.expected_item_row_version + 1,
            inactive_duration_seconds=15,
            review_job_queued=False,
        )

def _app(
    editing: _Editing | None = None,
    purge: _Purge | None = None,
    lifecycle: _Lifecycle | None = None,
) -> Flask:
    app = Flask(f"p55-routes-{id(editing)}", static_folder=None)
    dependency = _Dependency()
    register_canonical_routes(
        app,
        scope_selection_service=dependency,
        conversation_service=dependency,
        retrieval_service=dependency,
        management_read_service=dependency,
        agent_context_service=dependency,
        turn_executor=_Executor(),
        conversation_summarizer=_Summarizer(),
        principal_resolver=lambda: {"id": "editor01", "scope_epoch": 4},
        item_edit_service=editing,
        item_lifecycle_service=lifecycle,
        item_purge_service=purge,
    )
    return app


def _post_headers(operation_id: str | None = None, *, fence: int | None = None) -> dict[str, str]:
    headers = {"X-Knowledge-Lease-Token": TOKEN}
    if operation_id is not None:
        headers["Idempotency-Key"] = operation_id
    if fence is not None:
        headers["X-Knowledge-Fence"] = str(fence)
    return headers


@pytest.mark.scenario("LEASE-001")
def test_editing_routes_are_all_or_none_and_dependency_checked() -> None:
    without = _app()
    editing = _Editing()
    with_editing = _app(editing)
    prefix = "/api/knowledge/v1"
    assert len([r for r in without.url_map.iter_rules() if r.rule.startswith(prefix)]) == 21
    routes = {
        (rule.rule, method)
        for rule in with_editing.url_map.iter_rules()
        if "/items/" in rule.rule
        for method in rule.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    assert {
        (f"{prefix}/spaces/<space_id>/items/<item_id>", "PUT"),
        (f"{prefix}/spaces/<space_id>/items/<item_id>/lease/acquire", "POST"),
        (f"{prefix}/spaces/<space_id>/items/<item_id>/lease/heartbeat", "POST"),
        (f"{prefix}/spaces/<space_id>/items/<item_id>/lease/release", "POST"),
        (f"{prefix}/spaces/<space_id>/items/<item_id>/lease/force-release", "POST"),
    } <= routes
    with pytest.raises(TypeError, match="missing save"):
        _app(SimpleNamespace(acquire=lambda: None, force_release=lambda: None, heartbeat=lambda: None, release=lambda: None))  # type: ignore[arg-type]


@pytest.mark.scenario("LEASE-001")
@pytest.mark.scenario("LEASE-011")
def test_acquire_heartbeat_and_release_bind_exact_session_token_and_fence() -> None:
    editing = _Editing()
    client = _app(editing).test_client()
    acquire_operation = _uuid("acquire-op")
    acquired = client.post(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/acquire",
        json={"editor_session_id": SESSION_ID, "operation_id": acquire_operation},
        headers=_post_headers(acquire_operation),
    )
    assert acquired.status_code == 201
    assert acquired.json == {
        "absolute_expires_at": "2026-08-14T14:00:00.000Z",
        "base_revision_id": BASE_REVISION_ID,
        "expires_at": "2026-08-14T12:05:00.000Z",
        "fencing_generation": 7,
        "lease_id": LEASE_ID,
        "operation_id": acquire_operation,
        "request_id": acquired.headers["X-Request-ID"],
    }
    command = editing.calls[-1][1]
    assert command.editor_session_id == SESSION_ID
    assert command.raw_token == TOKEN
    assert TOKEN not in repr(command)
    assert TOKEN not in acquired.get_data(as_text=True)
    assert all("lease" not in key.lower() and "token" not in key.lower() for key in command.request_meta)

    heartbeat = client.post(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/heartbeat",
        json={"editor_session_id": SESSION_ID, "lease_id": LEASE_ID},
        headers=_post_headers(fence=7),
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json["lease_id"] == LEASE_ID
    assert heartbeat.json["fencing_generation"] == 7
    heartbeat_command = editing.calls[-1][1]
    assert heartbeat_command.editor_session_id == SESSION_ID
    assert heartbeat_command.raw_token == TOKEN

    release_operation = _uuid("release-op")
    released = client.post(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/release",
        json={
            "editor_session_id": SESSION_ID,
            "lease_id": LEASE_ID,
            "operation_id": release_operation,
        },
        headers=_post_headers(release_operation, fence=7),
    )
    assert released.status_code == 200
    assert released.json["status"] == "released"
    assert released.json["resource_id"] == ITEM_ID


@pytest.mark.scenario("REV-001")
@pytest.mark.scenario("LEASE-001")
@pytest.mark.scenario("LEASE-006")
def test_update_binds_body_headers_and_if_match_without_last_write_wins() -> None:
    editing = _Editing()
    client = _app(editing).test_client()
    operation_id = _uuid("save-op")
    body = {
        "editor_session_id": SESSION_ID,
        "expected_revision_id": BASE_REVISION_ID,
        "expected_row_version": 11,
        "lease_id": LEASE_ID,
        "markdown": "# retained local draft\n",
        "operation_id": operation_id,
        "reason": "Documented edit",
    }
    headers = _post_headers(operation_id, fence=7)
    headers["If-Match"] = f'"{BASE_REVISION_ID}"'
    response = client.put(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}",
        json=body,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json["revision_id"] == NEW_REVISION_ID
    command = editing.calls[-1][1]
    assert command.expected_item_row_version == 11
    assert command.expected_revision_id == BASE_REVISION_ID
    assert command.markdown == body["markdown"]
    assert command.lease_id == LEASE_ID
    assert command.raw_token == TOKEN

    bad_headers = dict(headers)
    bad_headers["If-Match"] = f'"{NEW_REVISION_ID}"'
    rejected = client.put(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}",
        json={**body, "operation_id": _uuid("save-op-conflict")},
        headers={**bad_headers, "Idempotency-Key": _uuid("save-op-conflict")},
    )
    assert rejected.status_code == 409
    assert rejected.json["error"]["code"] == "REVISION_CONFLICT"
    assert rejected.json["error"]["details"]["draft_recovery"]["preserve_local_draft"] is True
    assert len(editing.calls) == 1


@pytest.mark.scenario("LIFE-008")
@pytest.mark.scenario("LIFE-009")
def test_manual_purge_routes_are_optional_and_bind_exact_preview_command() -> None:
    purge = _Purge()
    without = _app()
    with_purge = _app(purge=purge)
    prefix = "/api/knowledge/v1"
    without_routes = {rule.rule for rule in without.url_map.iter_rules()}
    assert f"{prefix}/spaces/<space_id>/items/<item_id>/purge" not in without_routes
    assert f"{prefix}/spaces/<space_id>/items/<item_id>/purge-preview" not in without_routes

    client = with_purge.test_client()
    preview = client.get(
        f"{prefix}/spaces/{SPACE_ID}/items/{ITEM_ID}/purge-preview"
    )
    assert preview.status_code == 200
    assert preview.json["eligible"] is True
    assert preview.json["confirmation_text"] == f"PURGE {ITEM_ID}"
    assert purge.calls[-1][0] == "preview"
    preview_command = purge.calls[-1][1]
    assert preview_command.principal.user_id == "editor01"
    assert preview_command.space_id == SPACE_ID
    assert preview_command.item_id == ITEM_ID

    operation_id = _uuid("purge-op")
    response = client.post(
        f"{prefix}/spaces/{SPACE_ID}/items/{ITEM_ID}/purge",
        json={
            "confirmation": f"PURGE {ITEM_ID}",
            "expected_row_version": 3,
            "operation_id": operation_id,
            "preview_token": preview.json["preview_token"],
        },
        headers={"Idempotency-Key": operation_id},
    )
    assert response.status_code == 200
    assert response.json == {
        "operation_id": operation_id,
        "request_id": response.headers["X-Request-ID"],
        "resource_id": ITEM_ID,
        "row_version": 4,
        "status": "purged_tombstone",
    }
    command = purge.calls[-1][1]
    assert command.expected_item_row_version == 3
    assert command.preview_token == preview.json["preview_token"]
    assert command.confirmation == f"PURGE {ITEM_ID}"
    assert command.operation_id == operation_id
    assert all("token" not in key.casefold() for key in command.request_meta)


@pytest.mark.scenario("LIFE-001")
@pytest.mark.scenario("LIFE-002")
@pytest.mark.scenario("LIFE-003")
def test_lifecycle_routes_are_optional_strict_and_bind_exact_commands() -> None:
    lifecycle = _Lifecycle()
    without = _app()
    prefix = "/api/knowledge/v1"
    deactivate_path = (
        f"{prefix}/spaces/{SPACE_ID}/items/{ITEM_ID}/deactivate"
    )
    restore_path = f"{prefix}/spaces/{SPACE_ID}/items/{ITEM_ID}/restore"
    assert without.test_client().post(deactivate_path).status_code == 404

    with pytest.raises(TypeError, match="missing restore"):
        _app(lifecycle=SimpleNamespace(deactivate=lambda _command: None))  # type: ignore[arg-type]

    client = _app(lifecycle=lifecycle).test_client()
    deactivate_operation = _uuid("deactivate-route-op")
    deactivated = client.post(
        deactivate_path,
        json={
            "expected_row_version": 3,
            "operation_id": deactivate_operation,
            "reason": "Retire superseded guidance",
        },
        headers={"Idempotency-Key": deactivate_operation},
    )
    assert deactivated.status_code == 200
    assert deactivated.json["item_id"] == ITEM_ID
    assert deactivated.json["row_version"] == 4
    assert deactivated.json["purge_eligible_at"] == "2026-11-12T00:00:00.000Z"
    command = lifecycle.calls[-1][1]
    assert command.principal.user_id == "editor01"
    assert command.principal.captured_scope_epoch == 4
    assert command.space_id == SPACE_ID
    assert command.item_id == ITEM_ID
    assert command.expected_item_row_version == 3
    assert command.reason == "Retire superseded guidance"

    restore_operation = _uuid("restore-route-op")
    restored = client.post(
        restore_path,
        json={
            "expected_row_version": 4,
            "operation_id": restore_operation,
        },
        headers={"Idempotency-Key": restore_operation},
    )
    assert restored.status_code == 200
    assert restored.json == {
        "inactive_duration_seconds": 15,
        "item_id": ITEM_ID,
        "operation_id": restore_operation,
        "request_id": restored.headers["X-Request-ID"],
        "review_job_queued": False,
        "revision_id": BASE_REVISION_ID,
        "row_version": 5,
    }

    before = len(lifecycle.calls)
    invalid = client.post(
        deactivate_path + "?unexpected=1",
        json={
            "expected_row_version": 5,
            "operation_id": _uuid("deactivate-route-invalid"),
            "reason": "Invalid query",
        },
    )
    assert invalid.status_code == 400
    assert len(lifecycle.calls) == before


@pytest.mark.scenario("LIFE-009")
def test_manual_purge_route_rejects_extra_fields_and_mismatched_operation_header() -> None:
    purge = _Purge()
    client = _app(purge=purge).test_client()
    path = f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/purge"
    operation_id = _uuid("purge-strict-op")
    body = {
        "confirmation": f"PURGE {ITEM_ID}",
        "expected_row_version": 3,
        "operation_id": operation_id,
        "preview_token": "v1." + "A" * 80,
    }
    extra = client.post(
        path,
        json={**body, "automatic": True},
        headers={"Idempotency-Key": operation_id},
    )
    assert extra.status_code == 400
    mismatch = client.post(
        path,
        json=body,
        headers={"Idempotency-Key": _uuid("different-op")},
    )
    assert mismatch.status_code == 409
    assert purge.calls == []


@pytest.mark.scenario("LEASE-007")
@pytest.mark.scenario("LEASE-008")
def test_force_release_requires_exact_confirmation_and_audit_reason() -> None:
    editing = _Editing()
    client = _app(editing).test_client()
    operation_id = _uuid("force-op")
    path = f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/force-release"
    rejected = client.post(
        path,
        json={"confirmation": "force", "operation_id": operation_id, "reason": "Owner unavailable"},
        headers={"Idempotency-Key": operation_id},
    )
    assert rejected.status_code == 400
    assert editing.calls == []
    accepted = client.post(
        path,
        json={"confirmation": "FORCE_RELEASE", "operation_id": operation_id, "reason": "Owner unavailable"},
        headers={"Idempotency-Key": operation_id},
    )
    assert accepted.status_code == 200
    assert accepted.json["status"] == "force_released"
    assert editing.calls[-1][1].reason == "Owner unavailable"


@pytest.mark.scenario("LEASE-002")
@pytest.mark.scenario("LEASE-012")
def test_lease_service_error_details_reach_client_but_token_and_draft_do_not() -> None:
    editing = _Editing()
    editing.error = LeaseHeldError(
        "Another editor holds the lease.",
        details={
            "expires_at": "2026-08-14T12:05:00.000Z",
            "heartbeat_at": "2026-08-14T12:00:30.000Z",
            "holder_user_id": "other-user",
        },
    )
    client = _app(editing).test_client()
    operation_id = _uuid("held-op")
    held = client.post(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/acquire",
        json={"editor_session_id": SESSION_ID, "operation_id": operation_id},
        headers=_post_headers(operation_id),
    )
    assert held.status_code == 423
    assert held.json["error"]["code"] == "LEASE_HELD"
    assert held.json["error"]["details"]["holder_user_id"] == "other-user"
    assert TOKEN not in held.get_data(as_text=True)

    editing.error = LeaseRevisionConflictError(
        "The item changed.",
        details={
            "current_revision_id": NEW_REVISION_ID,
            "current_row_version": 12,
            "draft_recovery": {
                "actions": ["view_current", "copy_draft", "reacquire_and_compare"],
                "force_overwrite_allowed": False,
                "preserve_local_draft": True,
            },
        },
    )
    save_operation = _uuid("conflict-op")
    conflict = client.put(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}",
        json={
            "editor_session_id": SESSION_ID,
            "expected_revision_id": BASE_REVISION_ID,
            "expected_row_version": 11,
            "lease_id": LEASE_ID,
            "markdown": "PRIVATE LOCAL DRAFT",
            "operation_id": save_operation,
            "reason": "Edit",
        },
        headers={
            **_post_headers(save_operation, fence=7),
            "If-Match": f'"{BASE_REVISION_ID}"',
        },
    )
    assert conflict.status_code == 409
    assert conflict.json["error"]["details"]["draft_recovery"]["preserve_local_draft"] is True
    assert "PRIVATE LOCAL DRAFT" not in conflict.get_data(as_text=True)
    assert TOKEN not in conflict.get_data(as_text=True)


def test_route_composition_exposes_editing_only_under_explicit_write_binding() -> None:
    dependency = _Dependency()
    bindings = CanonicalRouteBindings(
        turn_executor=_Executor(),
        conversation_summarizer=_Summarizer(),
        principal_resolver=lambda: {"id": "editor01", "scope_epoch": 4},
    )
    services = ServiceRegistry(
        agent_context=dependency,
        conversations=dependency,
        editing=_Editing(),
        lifecycle=_Lifecycle(),
        management_reads=dependency,
        publication=dependency,
        retrieval=dependency,
        scope_selection=dependency,
    )
    read_only = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.CODE],
        flags=FeatureFlags(core_enabled=True, personal_reads=True),
        services=services,
        canonical_route_bindings=bindings,
    )
    app = Flask("p55-read-only", static_folder=None)
    assert register_routes(app, read_only) == ("canonical_knowledge_v1",)
    assert app.test_client().options(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/acquire"
    ).status_code == 404

    writable = KnowledgeCoreContainer(
        contract_version=1,
        host=HOST_PROFILES[HostKind.CODE],
        flags=FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            personal_writes=True,
        ),
        services=services,
        canonical_route_bindings=bindings,
    )
    writable_app = Flask("p55-write-bound", static_folder=None)
    assert register_routes(writable_app, writable) == (
        "canonical_knowledge_v1",
        "canonical_publication_v1",
        "canonical_editing_v1",
        "canonical_lifecycle_v1",
    )
    assert writable_app.test_client().options(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/lease/acquire"
    ).status_code == 200
    assert writable_app.test_client().options(
        f"/api/knowledge/v1/spaces/{SPACE_ID}/items/{ITEM_ID}/deactivate"
    ).status_code == 200


def test_p55_contract_requires_session_and_confirmation_fields() -> None:
    root = Path(__file__).resolve().parents[3]
    schema = json.loads(
        (root / "docs/knowledge_spaces/contracts/schemas/knowledge.schema.json").read_text(
            encoding="utf-8"
        )
    )["$defs"]
    assert set(schema["HeartbeatLeaseRequest"]["required"]) == {
        "editor_session_id",
        "lease_id",
    }
    assert set(schema["ReleaseLeaseRequest"]["required"]) == {
        "editor_session_id",
        "lease_id",
        "operation_id",
    }
    assert {"editor_session_id", "lease_id"} <= set(schema["UpdateItemRequest"]["required"])
    assert schema["ForceReleaseRequest"]["properties"]["confirmation"] == {
        "const": "FORCE_RELEASE"
    }
