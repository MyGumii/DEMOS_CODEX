from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.agent_runtime import (
    compose_canonical_agent_runtime,
)
from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.domain import (
    FeatureFlags,
    SemanticEdgeDirection,
    SemanticRelationType,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.projection.graph import (
    AggregateGraphProjectionMaterializer,
)
from code_assist_v1.knowledge_core.projection.worker import (
    OutboxProjectionWorker,
    ProjectionWorkerConfig,
)
from code_assist_v1.knowledge_core.repositories.aggregate_graph import (
    AggregateGraphRepository,
)
from code_assist_v1.knowledge_core.repositories.affiliations import (
    OrgAffiliationRepository,
)
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    HigherSpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
)
from code_assist_v1.knowledge_core.repositories.outbox import (
    OutboxEventDraft,
    OutboxRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.aggregate_graph import (
    AggregateGraphIdempotencyError,
    AggregateGraphLocalEdgeRequiredError,
    AggregateGraphService,
    AggregateGraphServiceError,
    AggregateGraphValidationError,
    CreateAggregateEdgeCommand,
)
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
    SpaceManagementReadRequest,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v10_semantic_graph_contracts import (
    _published_item,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("af799fc9-548a-45d5-ae06-e8af55a399be")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="sbe-4.1-aggregate-graph-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, admin: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:sbe-4.1:{user_id}",
        system_role="admin" if admin else "user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        part_reads=True,
        higher_spaces=True,
        aggregate_graph_reads=True,
        team_writes=True,
        responsibility_writes=True,
    )


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    ids = {
        "team_a": _uuid("org:team:a"),
        "team_b": _uuid("org:team:b"),
        "part_a": _uuid("org:part:a"),
        "part_a2": _uuid("org:part:a2"),
        "part_b": _uuid("org:part:b"),
        "part_space_a": _uuid("space:part:a"),
        "part_space_a2": _uuid("space:part:a2"),
        "part_space_b": _uuid("space:part:b"),
    }
    ids["team_space_a"] = ids["team_a"]
    ids["team_space_b"] = ids["team_b"]
    ids["responsibility_space"] = RESPONSIBILITY_ROOT_ID
    users = UserRepository()
    organizations = OrganizationRepository()
    spaces = SpaceRepository()
    higher = HigherSpaceRepository()
    affiliations = OrgAffiliationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _user("admin", admin=True),
                    _user("teamlead"),
                    _user("executive"),
                    _user("partmember"),
                    _user("a2member"),
                    _user("bmember"),
                ),
            )
            for unit_id, kind, parent_id, name in (
                (ids["team_a"], "team", RESPONSIBILITY_ROOT_ID, "Team A"),
                (ids["team_b"], "team", RESPONSIBILITY_ROOT_ID, "Team B"),
                (ids["part_a"], "part", ids["team_a"], "Part A"),
                (ids["part_a2"], "part", ids["team_a"], "Part A2"),
                (ids["part_b"], "part", ids["team_b"], "Part B"),
            ):
                organizations.create_unit(
                    connection,
                    unit_id=unit_id,
                    kind=kind,  # type: ignore[arg-type]
                    parent_id=parent_id,
                    name=name,
                    actor_user_id="admin",
                    now_ms=1000,
                )
            memberships = (
                ("partmember", ids["part_a"]),
                ("a2member", ids["part_a2"]),
                ("bmember", ids["part_b"]),
            )
            for user_id, part_id in memberships:
                state = organizations.get_user_organization_state(connection, user_id)
                assert state is not None
                organizations.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="admin",
                    reason="SBE-4.1 fixture membership",
                    valid_from_ms=1100,
                )
                organizations.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=part_id,
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=1100,
                )
            for user_id in (
                "admin",
                "teamlead",
                "executive",
                "partmember",
                "a2member",
                "bmember",
            ):
                spaces.ensure_personal(
                    connection,
                    space_id=_uuid(f"space:personal:{user_id}"),
                    user_id=user_id,
                    created_at_ms=1200,
                )
            for user_id, unit_id, home_kind, role_label in (
                ("teamlead", ids["team_a"], "team", "role:teamlead"),
                (
                    "executive",
                    RESPONSIBILITY_ROOT_ID,
                    "responsibility",
                    "role:executive",
                ),
            ):
                state = organizations.get_user_organization_state(connection, user_id)
                assert state is not None
                affiliations.open_direct(
                    connection,
                    affiliation_id=_uuid(role_label),
                    user_id=user_id,
                    org_unit_id=unit_id,
                    home_kind=home_kind,  # type: ignore[arg-type]
                    assigned_by_user_id="admin",
                    reason="SBE direct higher-role fixture",
                    valid_from_ms=1250,
                )
                affiliations.bump_direct_scope(
                    connection,
                    user_id=user_id,
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=1250,
                )
            for space_id, part_id in (
                (ids["part_space_a"], ids["part_a"]),
                (ids["part_space_a2"], ids["part_a2"]),
                (ids["part_space_b"], ids["part_b"]),
            ):
                spaces.ensure_part(
                    connection,
                    space_id=space_id,
                    part_id=part_id,
                    created_at_ms=1200,
                )
            for team_space, team_id in (
                (ids["team_space_a"], ids["team_a"]),
                (ids["team_space_b"], ids["team_b"]),
            ):
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'team',NULL,?,'active',0,0,1200,1200,1)""",
                    (team_space, team_id),
                )
            higher.insert_open_role(
                connection,
                assignment_id=_uuid("role:teamlead"),
                org_unit_id=ids["team_a"],
                user_id="teamlead",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=1300,
            )
            higher.insert_open_role(
                connection,
                assignment_id=_uuid("role:executive"),
                org_unit_id=RESPONSIBILITY_ROOT_ID,
                user_id="executive",
                role="responsibility_executive",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=1300,
            )
            for space_id in (
                ids["team_space_a"],
                ids["responsibility_space"],
            ):
                connection.execute(
                    """UPDATE knowledge_space
                          SET write_enabled=1,updated_at=1300,row_version=row_version+1
                        WHERE id=?""",
                    (space_id,),
                )
    finally:
        connection.close()

    item_specs = (
        ("a1", ids["part_space_a"], "partmember"),
        ("a2", ids["part_space_a"], "partmember"),
        ("a2-child", ids["part_space_a2"], "a2member"),
        ("b1", ids["part_space_b"], "bmember"),
    )
    connection = factory.open()
    try:
        with transaction(connection):
            for index, (label, space_id, actor) in enumerate(item_specs, start=1):
                item, revision = _published_item(
                    connection,
                    label=f"sbe41:{label}",
                    space_id=space_id,
                    actor=actor,
                    timestamp=2000 + index,
                )
                ids[f"item_{label}"] = item
                ids[f"revision_{label}"] = revision
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,
                     edge_status,origin,source_evidence_revision_id,
                     target_evidence_revision_id,approval_finding_id,
                     evidence_json,needs_revalidation,created_by,updated_by,
                     created_at,updated_at,row_version
                   ) VALUES (?, ?, ?, ?,'related','active','manual',?,?,NULL,
                             '{}',0,'partmember','partmember',3000,3000,1)""",
                (
                    _uuid("edge:local:a"),
                    ids["part_space_a"],
                    ids["item_a1"],
                    ids["item_a2"],
                    ids["revision_a1"],
                    ids["revision_a2"],
                ),
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=3000,
                          row_version=row_version+1
                    WHERE id=?""",
                (ids["part_space_a"],),
            )
    finally:
        connection.close()
    return ids


def _epoch(factory: ConnectionFactory, user_id: str) -> int:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return UserRepository().require(connection, user_id).scope_epoch
    finally:
        connection.close()


def _item_version(factory: ConnectionFactory, item_id: str) -> int:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            row = connection.execute(
                "SELECT row_version FROM knowledge_item WHERE id=?", (item_id,)
            ).fetchone()
            assert row is not None
            return int(row[0])
    finally:
        connection.close()


def _command(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    actor: str,
    source: str,
    target: str,
    label: str,
    edge_type: SemanticRelationType = SemanticRelationType.RELATED,
    direction: SemanticEdgeDirection = SemanticEdgeDirection.SYMMETRIC,
) -> CreateAggregateEdgeCommand:
    return CreateAggregateEdgeCommand(
        principal=SessionPrincipal(actor, _epoch(factory, actor)),
        source_space_id=ids[f"space_for_{source}"],
        source_item_id=ids[f"item_{source}"],
        source_revision_id=ids[f"revision_{source}"],
        source_item_row_version=_item_version(factory, ids[f"item_{source}"]),
        target_space_id=ids[f"space_for_{target}"],
        target_item_id=ids[f"item_{target}"],
        target_revision_id=ids[f"revision_{target}"],
        target_item_row_version=_item_version(factory, ids[f"item_{target}"]),
        edge_type=edge_type,
        direction=direction,
        source_evidence_span_sha256=hashlib.sha256(source.encode()).hexdigest(),
        target_evidence_span_sha256=hashlib.sha256(target.encode()).hexdigest(),
        source_evidence_location={"line_end": 1, "line_start": 1},
        target_evidence_location={"line_end": 1, "line_start": 1},
        reason=f"SBE-4.1 edge {label}",
        operation_id=_uuid(f"operation:{label}"),
        request_id=_uuid(f"request:{label}"),
    )


def _decorate_spaces(ids: dict[str, str]) -> None:
    ids.update(
        {
            "space_for_a1": ids["part_space_a"],
            "space_for_a2": ids["part_space_a"],
            "space_for_a2-child": ids["part_space_a2"],
            "space_for_b1": ids["part_space_b"],
        }
    )


def _management(factory: ConnectionFactory) -> CanonicalManagementReadService:
    return CanonicalManagementReadService(
        factory, scope_resolver=ScopeResolver(_flags())
    )


def _read_graph(
    factory: ConnectionFactory,
    *,
    actor: str,
    space_id: str,
):
    return _management(factory).graph(
        SpaceManagementReadRequest(
            actor_user_id=actor,
            actor_scope_epoch=_epoch(factory, actor),
            space_id=space_id,
        )
    )


@pytest.mark.scenario("AGRAPH-009")
def test_enabled_runtime_binds_aggregate_graph_service(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=_flags(),
        principal_resolver=lambda: None,
        higher_authority_cursor_secret=b"h" * 32,
    )
    assert isinstance(runtime.services.aggregate_graph, AggregateGraphService)


@pytest.mark.scenario("AGRAPH-005", "AGRAPH-006", "AGRAPH-008", "AGRAPH-012")
def test_exact_lca_storage_replay_and_part_isolation(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    repository = AggregateGraphRepository()
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            local = repository.resolve_placement(
                connection,
                source_space_id=ids["part_space_a"],
                target_space_id=ids["part_space_a"],
            )
            team = repository.resolve_placement(
                connection,
                source_space_id=ids["part_space_a"],
                target_space_id=ids["part_space_a2"],
            )
            responsibility = repository.resolve_placement(
                connection,
                source_space_id=ids["part_space_a"],
                target_space_id=ids["part_space_b"],
            )
        assert local.kind == "local" and local.home_space_id == ids["part_space_a"]
        assert team.home_space_id == ids["team_space_a"]
        assert responsibility.home_space_id == ids["responsibility_space"]
    finally:
        connection.close()

    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    with pytest.raises(AggregateGraphLocalEdgeRequiredError):
        service.create(
            _command(
                factory,
                ids,
                actor="teamlead",
                source="a1",
                target="a2",
                label="local-required",
            )
        )
    before_part = _read_graph(
        factory, actor="partmember", space_id=ids["part_space_a"]
    )
    command = _command(
        factory,
        ids,
        actor="teamlead",
        source="a1",
        target="a2-child",
        label="same-team",
    )
    created = service.create(command)
    assert created.home_space_id == ids["team_space_a"]
    replay = service.create(replace(command, request_id=_uuid("request:replay")))
    assert replay == replace(created, request_id=_uuid("request:replay"))
    with pytest.raises(AggregateGraphIdempotencyError):
        service.create(replace(command, reason="changed request"))

    after_part = _read_graph(
        factory, actor="partmember", space_id=ids["part_space_a"]
    )
    assert before_part.generation == after_part.generation
    assert before_part.edges == after_part.edges
    assert {edge.edge_scope for edge in after_part.edges} == {"local"}
    team_graph = _read_graph(
        factory, actor="teamlead", space_id=ids["team_space_a"]
    )
    aggregate = [edge for edge in team_graph.edges if edge.edge_scope == "aggregate"]
    assert [edge.id for edge in aggregate] == [created.edge_id]
    assert aggregate[0].home_space_id == ids["team_space_a"]
    assert {
        aggregate[0].source_space_id,
        aggregate[0].target_space_id,
    } == {ids["part_space_a"], ids["part_space_a2"]}
    disabled = CanonicalManagementReadService(
        factory,
        scope_resolver=ScopeResolver(
            replace(_flags(), aggregate_graph_reads=False)
        ),
    ).graph(
        SpaceManagementReadRequest(
            actor_user_id="teamlead",
            actor_scope_epoch=_epoch(factory, "teamlead"),
            space_id=ids["team_space_a"],
        )
    )
    assert all(edge.edge_scope == "local" for edge in disabled.edges)


@pytest.mark.scenario("AGRAPH-012")
def test_self_duplicate_and_directional_cycle_cases_are_fail_closed_or_bounded(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    symmetric = _command(
        factory,
        ids,
        actor="teamlead",
        source="a1",
        target="a2-child",
        label="symmetric-primary",
    )
    first = service.create(symmetric)
    with pytest.raises(AggregateGraphServiceError):
        service.create(
            _command(
                factory,
                ids,
                actor="teamlead",
                source="a2-child",
                target="a1",
                label="symmetric-duplicate-reversed",
            )
        )
    with pytest.raises(AggregateGraphValidationError):
        service.create(
            replace(
                symmetric,
                target_space_id=symmetric.source_space_id,
                target_item_id=symmetric.source_item_id,
                target_revision_id=symmetric.source_revision_id,
                target_item_row_version=symmetric.source_item_row_version,
                operation_id=_uuid("operation:self-edge"),
                request_id=_uuid("request:self-edge"),
            )
        )
    forward = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="directed-forward",
            edge_type=SemanticRelationType.DEPENDS_ON,
            direction=SemanticEdgeDirection.SOURCE_TO_TARGET,
        )
    )
    reverse = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a2-child",
            target="a1",
            label="directed-reverse",
            edge_type=SemanticRelationType.DEPENDS_ON,
            direction=SemanticEdgeDirection.SOURCE_TO_TARGET,
        )
    )
    graph = _read_graph(factory, actor="teamlead", space_id=ids["team_space_a"])
    aggregate_ids = {
        edge.id for edge in graph.edges if edge.edge_scope == "aggregate"
    }
    assert {first.edge_id, forward.edge_id, reverse.edge_id}.issubset(aggregate_ids)


@pytest.mark.scenario("AGRAPH-007", "AGRAPH-008", "AGRAPH-009", "AGRAPH-010")
def test_responsibility_composes_team_and_responsibility_homes(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    team_edge = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="same-team",
        )
    )
    responsibility_edge = service.create(
        _command(
            factory,
            ids,
            actor="executive",
            source="a2",
            target="b1",
            label="cross-team",
        )
    )
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            team_native = AggregateGraphRepository().resolve_placement(
                connection,
                source_space_id=ids["team_space_a"],
                target_space_id=ids["part_space_a"],
            )
            responsibility_native = AggregateGraphRepository().resolve_placement(
                connection,
                source_space_id=ids["responsibility_space"],
                target_space_id=ids["part_space_b"],
            )
        assert team_native.home_space_id == ids["team_space_a"]
        assert responsibility_native.home_space_id == ids["responsibility_space"]
    finally:
        connection.close()
    team_graph = _read_graph(
        factory, actor="teamlead", space_id=ids["team_space_a"]
    )
    assert {edge.id for edge in team_graph.edges if edge.edge_scope == "aggregate"} == {
        team_edge.edge_id
    }
    responsibility_graph = _read_graph(
        factory, actor="executive", space_id=ids["responsibility_space"]
    )
    aggregate = {
        edge.id: edge
        for edge in responsibility_graph.edges
        if edge.edge_scope == "aggregate"
    }
    assert set(aggregate) == {
        team_edge.edge_id,
        responsibility_edge.edge_id,
    }
    assert aggregate[team_edge.edge_id].inherited is True
    assert aggregate[responsibility_edge.edge_id].inherited is False
    assert len({node.id for node in responsibility_graph.nodes}) == len(
        responsibility_graph.nodes
    )
    assert all(node.space_id is not None for node in responsibility_graph.nodes)


@pytest.mark.scenario("AGRAPH-011", "AGRAPH-013")
def test_endpoint_drift_hides_edge_and_invalidates_only_current_ancestors(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    result = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="stale-endpoint",
        )
    )
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            before = {
                str(row["selected_space_id"]): int(row["desired_generation"])
                for row in connection.execute(
                    """SELECT selected_space_id,desired_generation
                         FROM aggregate_generation_vector"""
                ).fetchall()
            }
    finally:
        connection.close()
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_item SET updated_at=4000,row_version=row_version+1
                    WHERE id=?""",
                (ids["item_a1"],),
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=4000,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part_space_a"],),
            )
            version_row = connection.execute(
                "SELECT row_version FROM knowledge_item WHERE id=?",
                (ids["item_a1"],),
            ).fetchone()
            assert version_row is not None
            OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key="sbe41:endpoint:drift",
                    space_id=ids["part_space_a"],
                    aggregate_type="knowledge_item",
                    aggregate_id=ids["item_a1"],
                    aggregate_version=int(version_row[0]),
                    event_type="knowledge_item.edited",
                    payload={"schema_version": 1},
                    available_at_ms=4000,
                ),
            )
    finally:
        connection.close()
    graph = _read_graph(factory, actor="teamlead", space_id=ids["team_space_a"])
    assert result.edge_id not in {edge.id for edge in graph.edges}
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            after = {
                str(row["selected_space_id"]): int(row["desired_generation"])
                for row in connection.execute(
                    """SELECT selected_space_id,desired_generation
                         FROM aggregate_generation_vector"""
                ).fetchall()
            }
    finally:
        connection.close()
    assert after[ids["team_space_a"]] == before[ids["team_space_a"]] + 1
    assert after[ids["responsibility_space"]] == before[ids["responsibility_space"]] + 1
    assert ids["team_space_b"] not in after


@pytest.mark.scenario("AGRAPH-013")
def test_invalidation_is_ancestor_only_and_ignores_unrelated_outbox(
    isolated_knowledge_layout,
    monkeypatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="bounded-invalidation",
        )
    )
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            before = {
                str(row["selected_space_id"]): int(row["desired_generation"])
                for row in connection.execute(
                    """SELECT selected_space_id,desired_generation
                         FROM aggregate_generation_vector"""
                ).fetchall()
            }
    finally:
        connection.close()

    def forbidden_capture(*_args, **_kwargs):
        raise AssertionError("mutation invalidation scanned descendants")

    monkeypatch.setattr(
        AggregateGraphRepository,
        "capture_source_vector",
        forbidden_capture,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            targets = AggregateGraphRepository().invalidate_current_ancestors(
                connection,
                source_space_id=ids["part_space_a"],
                now_ms=now_ms,
            )
            assert targets == (
                ids["team_space_a"],
                ids["responsibility_space"],
            )
            OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key="sbe41:unrelated-outbox",
                    space_id=ids["part_space_a"],
                    aggregate_type="conversation",
                    aggregate_id=_uuid("conversation:unrelated"),
                    aggregate_version=1,
                    event_type="conversation.updated",
                    payload={"schema_version": 1},
                    available_at_ms=now_ms,
                ),
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=?,row_version=row_version+1
                    WHERE id=?""",
                (now_ms, ids["part_space_a"]),
            )
            relevant = OutboxEventDraft(
                logical_key="sbe41:exact-outbox-replay",
                space_id=ids["part_space_a"],
                aggregate_type="knowledge_item",
                aggregate_id=ids["item_a1"],
                aggregate_version=2,
                event_type="knowledge_item.edited",
                payload={"schema_version": 1},
                available_at_ms=now_ms,
            )
            first_event = OutboxRepository().append(connection, relevant)
            replay_event = OutboxRepository().append(connection, relevant)
            assert replay_event == first_event
    finally:
        connection.close()
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            after = {
                str(row["selected_space_id"]): int(row["desired_generation"])
                for row in connection.execute(
                    """SELECT selected_space_id,desired_generation
                         FROM aggregate_generation_vector"""
                ).fetchall()
            }
    finally:
        connection.close()
    assert after[ids["team_space_a"]] == before[ids["team_space_a"]] + 2
    assert (
        after[ids["responsibility_space"]]
        == before[ids["responsibility_space"]] + 2
    )
    assert ids["team_space_b"] not in after


@pytest.mark.scenario("AGRAPH-014")
def test_graph_projection_is_byte_identical_and_contains_no_markdown(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    AggregateGraphService(factory, ScopeResolver(_flags())).create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="projection",
        )
    )
    layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    materializer = AggregateGraphProjectionMaterializer(factory, layout)
    first = materializer.rebuild_space(ids["team_space_a"])
    second = materializer.rebuild_space(ids["team_space_a"])
    assert first.manifest_sha256 == second.manifest_sha256
    generation = (
        isolated_knowledge_layout.projections_dir
        / ids["team_space_a"]
        / "aggregate-graph-generations"
        / first.generation_name
    )
    assert {path.name for path in generation.iterdir()} == {
        "backlinks.json",
        "graph.json",
        "index.json",
        "log.json",
        "manifest.json",
    }
    assert not tuple(generation.rglob("*.md"))
    manifest = json.loads((generation / "manifest.json").read_text("utf-8"))
    assert all(not row["path"].endswith(".md") for row in manifest["files"])


@pytest.mark.scenario("AGRAPH-006", "AGRAPH-009", "AGRAPH-013", "AGRAPH-014")
def test_team_home_change_creates_a_new_responsibility_projection_generation(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    service = AggregateGraphService(factory, ScopeResolver(_flags()))
    first_edge = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="responsibility-first-team-edge",
        )
    )
    layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    materializer = AggregateGraphProjectionMaterializer(factory, layout)
    first = materializer.rebuild_space(ids["responsibility_space"])
    second_edge = service.create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a2",
            target="a2-child",
            label="responsibility-second-team-edge",
        )
    )
    second = materializer.rebuild_space(ids["responsibility_space"])
    assert second.generation_name != first.generation_name
    assert second.manifest_sha256 != first.manifest_sha256
    graph_path = (
        isolated_knowledge_layout.projections_dir
        / ids["responsibility_space"]
        / "aggregate-graph-generations"
        / second.generation_name
        / "graph.json"
    )
    graph = json.loads(graph_path.read_text("utf-8"))
    assert {row["id"] for row in graph["edges"] if row["edge_scope"] == "aggregate"} >= {
        first_edge.edge_id,
        second_edge.edge_id,
    }


@pytest.mark.scenario("AGRAPH-011", "AGRAPH-013", "AGRAPH-014")
def test_same_responsibility_org_move_changes_vector_and_hides_stale_home(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    edge = AggregateGraphService(factory, ScopeResolver(_flags())).create(
        _command(
            factory,
            ids,
            actor="teamlead",
            source="a1",
            target="a2-child",
            label="org-move-stale-home",
        )
    )
    layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    materializer = AggregateGraphProjectionMaterializer(factory, layout)
    before = materializer.rebuild_space(ids["responsibility_space"])
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            unit = OrganizationRepository().require_unit(connection, ids["part_a2"])
            OrganizationRepository().set_unit_parent(
                connection,
                unit_id=unit.id,
                expected_row_version=unit.row_version,
                parent_id=ids["team_b"],
                actor_user_id="admin",
                updated_at_ms=now_ms,
            )
            AggregateGraphRepository().invalidate_generation_targets(
                connection,
                selected_space_ids=(
                    ids["team_space_a"],
                    ids["team_space_b"],
                    ids["responsibility_space"],
                ),
                now_ms=now_ms,
            )
    finally:
        connection.close()
    after = materializer.rebuild_space(ids["responsibility_space"])
    assert after.generation_name != before.generation_name
    graph = _read_graph(
        factory,
        actor="executive",
        space_id=ids["responsibility_space"],
    )
    assert edge.edge_id not in {row.id for row in graph.edges}


@pytest.mark.scenario("AGRAPH-009", "AGRAPH-013", "AGRAPH-014")
def test_enabled_worker_bootstraps_and_builds_bounded_higher_targets(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    _decorate_spaces(ids)
    connection = factory.open()
    try:
        with transaction(connection):
            now_ms = db_now_ms(connection)
            OutboxRepository().append(
                connection,
                OutboxEventDraft(
                    logical_key="sbe41:worker:no-space-org-event",
                    space_id=None,
                    aggregate_type="org_unit",
                    aggregate_id=ids["part_a2"],
                    aggregate_version=1,
                    event_type="organization.unit.moved",
                    payload={"schema_version": 1},
                    available_at_ms=now_ms,
                ),
            )
    finally:
        connection.close()
    layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    result = OutboxProjectionWorker(
        factory,
        layout,
        ProjectionWorkerConfig(
            claim_owner="sbe41-aggregate-worker",
            aggregate_graphs=True,
        ),
    ).run_once()
    assert result.event is not None and result.event.state == "applied"
    for space_id in (
        ids["team_space_a"],
        ids["team_space_b"],
        ids["responsibility_space"],
    ):
        manifest = (
            isolated_knowledge_layout.projections_dir
            / space_id
            / "aggregate-graph-manifest.json"
        )
        assert manifest.is_file()
        assert not tuple(
            (manifest.parent / "aggregate-graph-generations").rglob("*.md")
        )
