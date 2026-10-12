from __future__ import annotations

from uuid import UUID, uuid5

from flask import Flask
import pytest

from code_assist_v1.knowledge_core import (
    CanonicalRouteBindings,
    HostKind,
    ServiceRegistry,
    register_knowledge_core,
)
from code_assist_v1.knowledge_core.higher_space_routes import (
    HIGHER_ADMIN_ROUTE_PREFIX,
    HIGHER_KNOWLEDGE_ROUTE_PREFIX,
    register_higher_space_routes,
)
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    KnowledgeDistributionGrantRecord,
)
from code_assist_v1.knowledge_core.services.higher_authority import (
    DistributionMutationResult,
    DistributionPageResult,
    DistributionTargetListResult,
    DistributionTargetOption,
    HigherAuthorityRoleEpisodeStaleError,
    HigherRoleMutationResult,
    HigherRoleStatusResult,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("96350668-8c28-42c4-9950-d98e27662c20")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


class _HigherService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail: Exception | None = None

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    def set_role(self, command: object):
        self.calls.append(("set_role", command))
        self._check()
        return HigherRoleMutationResult(
            request_id=getattr(command, "request_id"),
            operation_id=getattr(command, "operation_id"),
            status="assigned",
            org_unit_id=getattr(command, "org_unit_id"),
            space_id=getattr(command, "org_unit_id"),
            assignment_id=_uuid("role"),
            assignment_generation=1,
            assignment_row_version=1,
            space_row_version=2,
            space_generation=0,
            write_enabled=True,
        )

    def read_role(self, query: object):
        self.calls.append(("read_role", query))
        self._check()
        return HigherRoleStatusResult(
            request_id=getattr(query, "request_id"),
            org_unit_id=getattr(query, "org_unit_id"),
            role=getattr(query, "role"),
            space_id=_uuid(f"{getattr(query, 'role')}-space"),
            space_row_version=3,
            space_generation=1,
            write_enabled=True,
            assignment_id=_uuid("role"),
            assignment_generation=2,
            assignment_row_version=4,
            assignee_user_id="member",
            assignee_display_name="Member",
        )

    def create_distribution(self, command: object):
        self.calls.append(("create_distribution", command))
        self._check()
        return DistributionMutationResult(
            request_id=getattr(command, "request_id"),
            operation_id=getattr(command, "operation_id"),
            status="created",
            grant_id=_uuid("grant"),
            source_space_id=getattr(command, "source_space_id"),
            row_version=1,
            revoked=False,
        )

    def revoke_distribution(self, command: object):
        self.calls.append(("revoke_distribution", command))
        self._check()
        return DistributionMutationResult(
            request_id=getattr(command, "request_id"),
            operation_id=getattr(command, "operation_id"),
            status="revoked",
            grant_id=getattr(command, "grant_id"),
            source_space_id=getattr(command, "source_space_id"),
            row_version=2,
            revoked=True,
        )

    def list_distributions(self, query: object):
        self.calls.append(("list_distributions", query))
        self._check()
        record = KnowledgeDistributionGrantRecord(
            id=_uuid("grant"),
            source_space_id=getattr(query, "source_space_id"),
            source_item_id=_uuid("item"),
            source_revision_id=_uuid("revision"),
            target_org_unit_id=_uuid("part"),
            target_scope_kind="org_unit",
            target_scope_id=_uuid("part"),
            grant_mode="read_only",
            source_role_assignment_id=_uuid("role"),
            created_by_user_id="leader",
            operation_id=_uuid("create-operation"),
            effect_key="a" * 64,
            reason="share current revision",
            created_audit_event_id=1,
            created_outbox_event_id=1,
            created_at_ms=1000,
            revoked_at_ms=None,
            revoked_by_user_id=None,
            revoke_mode=None,
            revoke_role_assignment_id=None,
            revoke_reason=None,
            revoke_confirmation_sha256=None,
            revoke_operation_id=None,
            revoke_effect_key=None,
            revoked_audit_event_id=None,
            revoked_outbox_event_id=None,
            updated_at_ms=1000,
            row_version=1,
        )
        return DistributionPageResult(
            request_id=getattr(query, "request_id"),
            items=(record,),
            next_cursor="signed.cursor",
        )

    def list_distribution_targets(self, query: object):
        self.calls.append(("list_distribution_targets", query))
        self._check()
        return DistributionTargetListResult(
            request_id=getattr(query, "request_id"),
            source_space_id=getattr(query, "source_space_id"),
            items=(
                DistributionTargetOption(
                    org_unit_id=_uuid("part"),
                    kind="part",
                    name="Part A",
                    org_path=("Responsibility", "Team", "Part A"),
                ),
            ),
        )


def _app():
    app = Flask("sbe-2.3-higher-routes", static_folder=None)
    service = _HigherService()
    register_higher_space_routes(
        app,
        service=service,
        principal_resolver=lambda: {"id": "leader", "scope_epoch": 7},
    )
    return app, service


@pytest.mark.scenario("HSPACE-010", "HSPACE-011", "HSPACE-014")
def test_role_routes_bind_server_principal_and_exact_episode_fence() -> None:
    app, service = _app()
    client = app.test_client()
    team_id = _uuid("team")
    operation_id = _uuid("role-operation")
    body = {
        "assignee_user_id": "member",
        "enable_writes": True,
        "expected_current_assignment_generation": None,
        "expected_current_assignment_id": None,
        "expected_current_assignment_row_version": None,
        "expected_space_row_version": 1,
        "operation_id": operation_id,
        "reason": "assign exact leader",
    }
    response = client.put(
        f"{HIGHER_ADMIN_ROUTE_PREFIX}/teams/{team_id}/leader",
        json=body,
        headers={"Idempotency-Key": operation_id},
    )
    assert response.status_code == 200
    assert response.json["assignment_generation"] == 1
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    name, command = service.calls[-1]
    assert name == "set_role"
    assert command.actor_user_id == "leader"
    assert command.actor_scope_epoch == 7
    assert command.org_unit_id == team_id
    assert command.role == "team_leader"

    status = client.get(
        f"{HIGHER_ADMIN_ROUTE_PREFIX}/teams/{team_id}/leader"
    )
    assert status.status_code == 200
    assert status.json["role"] == "team_leader"
    assert status.json["assignee_user_id"] == "member"
    assert service.calls[-1][0] == "read_role"

    responsibility_id = _uuid("responsibility")
    response = client.put(
        f"{HIGHER_ADMIN_ROUTE_PREFIX}/responsibilities/{responsibility_id}/executive",
        json={**body, "operation_id": _uuid("executive-operation")},
    )
    assert response.status_code == 200
    assert service.calls[-1][1].role == "responsibility_executive"

    rejected = client.put(
        f"{HIGHER_ADMIN_ROUTE_PREFIX}/teams/{team_id}/leader",
        json={**body, "actor_user_id": "admin"},
    )
    assert rejected.status_code == 400
    assert len(service.calls) == 3

    service.fail = HigherAuthorityRoleEpisodeStaleError("stale episode")
    stale = client.put(
        f"{HIGHER_ADMIN_ROUTE_PREFIX}/teams/{team_id}/leader",
        json={**body, "operation_id": _uuid("stale-operation")},
    )
    assert stale.status_code == 409
    assert stale.json["error"]["code"] == "ROLE_EPISODE_STALE"


@pytest.mark.scenario("HSPACE-012", "HSPACE-013")
def test_distribution_routes_preserve_origin_and_bind_mutations() -> None:
    app, service = _app()
    client = app.test_client()
    space_id = _uuid("team-space")
    list_response = client.get(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distributions?limit=25&cursor=old"
    )
    assert list_response.status_code == 200
    assert list_response.json["next_cursor"] == "signed.cursor"
    item = list_response.json["items"][0]
    assert item["source_space_id"] == space_id
    assert item["source_item_id"] == _uuid("item")
    assert item["source_revision_id"] == _uuid("revision")
    assert item["grant_mode"] == "read_only"
    query = service.calls[-1][1]
    assert query.actor_user_id == "leader"
    assert query.limit == 25
    assert query.cursor == "old"

    targets = client.get(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distribution-targets"
    )
    assert targets.status_code == 200
    assert targets.json["source_space_id"] == space_id
    assert targets.json["items"][0]["target_scope_kind"] == "org_unit"
    assert targets.json["items"][0]["org_path"] == [
        "Responsibility",
        "Team",
        "Part A",
    ]
    assert service.calls[-1][0] == "list_distribution_targets"

    operation_id = _uuid("distribution-operation")
    create = client.post(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distributions",
        json={
            "operation_id": operation_id,
            "reason": "share revision",
            "source_item_id": _uuid("item"),
            "source_revision_id": _uuid("revision"),
            "target_org_unit_id": _uuid("part"),
            "target_scope_id": _uuid("part"),
            "target_scope_kind": "org_unit",
        },
        headers={"Idempotency-Key": operation_id},
    )
    assert create.status_code == 201
    assert create.json["grant_id"] == _uuid("grant")
    command = service.calls[-1][1]
    assert command.actor_user_id == "leader"
    assert command.source_space_id == space_id

    revoke_operation = _uuid("revoke-operation")
    revoke = client.post(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distributions/{_uuid('grant')}/revoke",
        json={
            "break_glass": False,
            "expected_row_version": 1,
            "operation_id": revoke_operation,
            "reason": "withdraw overlay",
        },
    )
    assert revoke.status_code == 200
    assert revoke.json["revoked"] is True
    assert service.calls[-1][1].confirmation is None

    assert client.get(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distributions?limit=0"
    ).status_code == 400
    assert client.get(
        f"{HIGHER_KNOWLEDGE_ROUTE_PREFIX}/{space_id}/distributions?limit=1&limit=2"
    ).status_code == 400


class _AllMethods:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class _Executor:
    def complete(self, _context: object):
        return None


class _Summarizer:
    def summarize(self, **_kwargs: object):
        return None


def test_higher_routes_register_only_through_exact_successor_flag() -> None:
    app = Flask("sbe-2.3-composition", static_folder=None)
    service = _AllMethods()
    container = register_knowledge_core(
        app,
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
            "KNOWLEDGE_CORE_HIGHER_SPACES": "1",
        },
        services=ServiceRegistry(
            agent_context=service,
            conversations=service,
            higher_authority=service,
            management_reads=service,
            retrieval=service,
            scope_selection=service,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=_Executor(),
            conversation_summarizer=_Summarizer(),
            principal_resolver=lambda: {"id": "leader", "scope_epoch": 1},
        ),
    )
    rules = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/admin/v2")
        or rule.rule.startswith(HIGHER_KNOWLEDGE_ROUTE_PREFIX)
    ]
    assert len(rules) == 6
    assert container.registered_route_groups == (
        "canonical_knowledge_v1",
        "higher_authority_v2",
    )
