from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, TransactionMode, transaction
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository
from code_assist_v1.knowledge_core.scope import Capability, ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.scope import ScopeNotFoundError
from code_assist_v1.knowledge_core.services.organization import (
    MoveOrgUnitCommand,
    OrgChangePreviewCommand,
    OrganizationService,
    OrgMoveResolution,
)
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    SpaceRetrievalRequest,
)
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
    ItemManagementReadRequest,
    ItemRevisionPageRequest,
    SpaceManagementPageRequest,
    SpaceManagementReadRequest,
)
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    ForceReleaseLeaseCommand,
    ImmutableItemEditService,
    LeaseAuthorizationError,
    LeaseHmacKeyRing,
    LeaseLostError,
    LeaseScopeStaleError,
    SaveItemCommand,
)
from code_assist_v1.knowledge_core.services.lifecycle import (
    DeactivateItemCommand,
    ItemLifecycleService,
    LifecycleAuthorizationError,
    RestoreItemCommand,
)
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentTurnContextRequest,
)
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand,
    CreateConversationCommand,
)
from code_assist_v1.knowledge_core.services.findings import (
    FindingNotFoundError,
    ReadFindingRequest,
    VersionedFindingService,
)
from code_assist_v1.knowledge_core.services.higher_authority import (
    CreateDistributionCommand,
    HigherAuthorityCursorError,
    HigherAuthorityConcealedError,
    HigherAuthorityDistributionConflictError,
    HigherAuthorityFailpointTriggered,
    HigherAuthorityForbiddenError,
    HigherAuthorityIdempotencyError,
    HigherAuthorityNativeWriteDisabledError,
    HigherAuthorityRoleConflictError,
    HigherAuthorityService,
    HigherAuthorityVersionError,
    ListDistributionTargetsQuery,
    ListDistributionsQuery,
    ReadHigherRoleQuery,
    RevokeDistributionCommand,
    SetHigherRoleCommand,
)
from code_assist_v1.knowledge_core.services.curation_worker import (
    CanonicalCurationRescanHandler,
)
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("e802f05e-d25e-4667-a395-6e0e1574ed5a")
_CURSOR_SECRET = b"sbe-2.3-higher-authority-test-secret-v1"
_ORG_PREVIEW_SECRET = b"sbe-2.3-org-move-preview-secret-v1"
_EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_LEASE_KEY = bytes(range(32))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _token(label: str) -> str:
    raw = hashlib.sha256(label.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="sbe-2.3-higher-authority-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _legacy_user(user_id: str, *, admin: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:sbe-2.3:{user_id}",
        system_role="admin" if admin else "user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    ids = {
        "team_a": _uuid("org:team:a"),
        "team_b": _uuid("org:team:b"),
        "part_a": _uuid("org:part:a"),
        "part_b": _uuid("org:part:b"),
        "part_c": _uuid("org:part:c"),
        "part_space_a": _uuid("space:part:a"),
        "part_space_b": _uuid("space:part:b"),
        "part_space_c": _uuid("space:part:c"),
        "item": _uuid("item:team:a"),
        "revision": _uuid("revision:team:a:1"),
    }
    ids["team_space_a"] = ids["team_a"]
    ids["team_space_b"] = ids["team_b"]
    ids["responsibility_space"] = RESPONSIBILITY_ROOT_ID
    users = UserRepository()
    organizations = OrganizationRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _legacy_user("admin", admin=True),
                    _legacy_user("leader"),
                    _legacy_user("replacement"),
                    _legacy_user("member"),
                    _legacy_user("foreign"),
                    _legacy_user("sibling"),
                ),
            )
            for unit_id, kind, parent_id, name in (
                (ids["team_a"], "team", RESPONSIBILITY_ROOT_ID, "Team A"),
                (ids["team_b"], "team", RESPONSIBILITY_ROOT_ID, "Team B"),
                (ids["part_a"], "part", ids["team_a"], "Part A"),
                (ids["part_b"], "part", ids["team_b"], "Part B"),
                (ids["part_c"], "part", ids["team_a"], "Part C"),
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
            for user_id, part_id in (
                ("member", ids["part_a"]),
                ("foreign", ids["part_b"]),
                ("sibling", ids["part_c"]),
            ):
                state = organizations.get_user_organization_state(
                    connection, user_id
                )
                assert state is not None
                organizations.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="admin",
                    reason="SBE-2.3 fixture membership",
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
            for user_id in ("admin", "leader", "replacement", "member", "foreign", "sibling"):
                spaces.ensure_personal(
                    connection,
                    space_id=_uuid(f"space:personal:{user_id}"),
                    user_id=user_id,
                    created_at_ms=1200,
                )
            spaces.ensure_part(
                connection,
                space_id=ids["part_space_a"],
                part_id=ids["part_a"],
                created_at_ms=1200,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part_space_c"],
                part_id=ids["part_c"],
                created_at_ms=1200,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part_space_b"],
                part_id=ids["part_b"],
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
    finally:
        connection.close()
    return ids


def _flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        part_reads=True,
        higher_spaces=True,
        team_writes=True,
        responsibility_writes=True,
    )


def _service(factory: ConnectionFactory, *, failpoint=None) -> HigherAuthorityService:
    return HigherAuthorityService(
        factory,
        ScopeResolver(_flags()),
        cursor_secret=_CURSOR_SECRET,
        failpoint=failpoint,
    )


def _epoch(factory: ConnectionFactory, user_id: str) -> int:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return UserRepository().require(connection, user_id).scope_epoch
    finally:
        connection.close()


def _role_command(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    operation: str,
    assignee: str | None,
    expected_assignment=None,
    expected_generation=None,
    expected_assignment_version=None,
    expected_space_version: int = 1,
    enable_writes: bool = True,
) -> SetHigherRoleCommand:
    return SetHigherRoleCommand(
        actor_user_id="admin",
        actor_scope_epoch=_epoch(factory, "admin"),
        operation_id=_uuid(operation),
        request_id=_uuid(f"request:{operation}"),
        org_unit_id=ids["team_a"],
        role="team_leader",
        assignee_user_id=assignee,
        expected_current_assignment_id=expected_assignment,
        expected_current_assignment_generation=expected_generation,
        expected_current_assignment_row_version=expected_assignment_version,
        expected_space_row_version=expected_space_version,
        enable_writes=enable_writes,
        reason=f"role operation {operation}",
    )


def _seed_team_item(factory: ConnectionFactory, ids: dict[str, str]) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,
                     curation_status,current_revision_id,
                     last_curation_transition_id,latest_deactivation_episode_id,
                     redirect_item_id,created_by,updated_by,created_at,updated_at,
                     row_version
                   ) VALUES (?, ?, NULL,'active','provisional',?,NULL,NULL,NULL,
                             'leader','leader',2000,2000,1)""",
                (ids["item"], ids["team_space_a"], ids["revision"]),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,
                     title,raw_content_sha256,normalized_content_sha256,
                     content_size_bytes,normalizer_version,metadata_json,
                     change_kind,change_reason,created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Team note',?,?,0,
                             'markdown_lf_v1','{}','initial','fixture',
                             'leader',2000)""",
                (ids["revision"], ids["item"], _EMPTY_SHA, _EMPTY_SHA),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,'',NULL,0,?,2000)""",
                (ids["revision"], ids["item"], _EMPTY_SHA),
            )
    finally:
        connection.close()


def _hold_team_lease(factory: ConnectionFactory, ids: dict[str, str]) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE item_lease
                      SET holder_user_id='leader',editor_session_id=?,
                          token_hmac=?,hmac_key_id='test-key',
                          fencing_generation=1,base_item_row_version=1,
                          base_revision_id=?,acquired_at=2100,heartbeat_at=2100,
                          expires_at=9999999999999,
                          absolute_expires_at=9999999999999
                    WHERE item_id=?""",
                (_uuid("editor:leader"), b"x" * 32, ids["revision"], ids["item"]),
            )
    finally:
        connection.close()


def _counts(factory: ConnectionFactory) -> tuple[int, int, int, int, int]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return tuple(
                int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "higher_scope_role_assignment",
                    "knowledge_distribution_grant",
                    "audit_event",
                    "outbox_event",
                    "operation_dedup",
                )
            )  # type: ignore[return-value]
    finally:
        connection.close()


def _open_team_role(factory: ConnectionFactory, ids: dict[str, str]):
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return HigherSpaceRepository().get_open_role(
                connection,
                org_unit_id=ids["team_a"],
                role="team_leader",
            )
    finally:
        connection.close()


def _space(factory: ConnectionFactory, space_id: str):
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return SpaceRepository().require_by_id(connection, space_id)
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-015", "HSPACE-016")
def test_role_status_and_distribution_targets_are_server_derived_and_episode_bound(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)

    empty = service.read_role(
        ReadHigherRoleQuery(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            request_id=_uuid("request:role:status:empty"),
            org_unit_id=ids["team_a"],
            role="team_leader",
        )
    )
    assert empty.assignment_id is None
    assert empty.assignee_user_id is None
    assert empty.write_enabled is False
    assert empty.space_id == ids["team_space_a"]

    with pytest.raises(HigherAuthorityForbiddenError):
        service.read_role(
            replace(
                ReadHigherRoleQuery(
                    actor_user_id="admin",
                    actor_scope_epoch=_epoch(factory, "admin"),
                    request_id=_uuid("request:role:status:forbidden"),
                    org_unit_id=ids["team_a"],
                    role="team_leader",
                ),
                actor_user_id="leader",
                actor_scope_epoch=_epoch(factory, "leader"),
            )
        )

    assigned = service.set_role(
        _role_command(factory, ids, operation="role:status:assign", assignee="leader")
    )
    current = service.read_role(
        ReadHigherRoleQuery(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            request_id=_uuid("request:role:status:current"),
            org_unit_id=ids["team_a"],
            role="team_leader",
        )
    )
    assert current.assignment_id == assigned.assignment_id
    assert current.assignment_generation == assigned.assignment_generation
    assert current.assignee_user_id == "leader"
    assert current.assignee_display_name == "Leader"
    assert current.write_enabled is True

    targets = service.list_distribution_targets(
        ListDistributionTargetsQuery(
            actor_user_id="leader",
            actor_scope_epoch=_epoch(factory, "leader"),
            request_id=_uuid("request:distribution:targets"),
            source_space_id=ids["team_space_a"],
        )
    )
    assert targets.source_space_id == ids["team_space_a"]
    assert {
        (item.org_unit_id, item.kind, item.org_path) for item in targets.items
    } == {
        (ids["part_a"], "part", ("Team A", "Part A")),
        (ids["part_c"], "part", ("Team A", "Part C")),
    }
    with pytest.raises(HigherAuthorityConcealedError):
        service.list_distribution_targets(
            replace(
                ListDistributionTargetsQuery(
                    actor_user_id="leader",
                    actor_scope_epoch=_epoch(factory, "leader"),
                    request_id=_uuid("request:distribution:targets:foreign"),
                    source_space_id=ids["team_space_a"],
                ),
                actor_user_id="foreign",
                actor_scope_epoch=_epoch(factory, "foreign"),
            )
        )


def test_role_episode_lifecycle_is_fenced_idempotent_and_audited(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)

    assign = _role_command(
        factory, ids, operation="role:assign", assignee="leader"
    )
    assigned = service.set_role(assign)
    assert assigned.status == "assigned"
    assert assigned.assignment_generation == 1
    assert assigned.assignment_row_version == 1
    assert assigned.space_row_version == 2
    assert assigned.write_enabled is True
    assert _counts(factory) == (1, 0, 1, 1, 1)
    _seed_team_item(factory, ids)
    _hold_team_lease(factory, ids)

    replay = service.set_role(
        replace(assign, request_id=_uuid("request:role:assign:replay"))
    )
    assert replay == replace(
        assigned, request_id=_uuid("request:role:assign:replay")
    )
    assert _counts(factory) == (1, 0, 1, 1, 1)
    with pytest.raises(HigherAuthorityIdempotencyError):
        service.set_role(replace(assign, reason="semantic drift"))
    assert _counts(factory) == (1, 0, 1, 1, 1)

    replace_command = _role_command(
        factory,
        ids,
        operation="role:replace",
        assignee="replacement",
        expected_assignment=assigned.assignment_id,
        expected_generation=assigned.assignment_generation,
        expected_assignment_version=assigned.assignment_row_version,
        expected_space_version=assigned.space_row_version,
    )
    replaced = service.set_role(replace_command)
    assert replaced.status == "replaced"
    assert replaced.assignment_id != assigned.assignment_id
    assert replaced.assignment_generation == 2
    assert replaced.assignment_row_version == 1
    assert replaced.space_row_version == 4
    assert replaced.write_enabled is True
    assert _counts(factory) == (2, 0, 2, 2, 2)
    connection = factory.open(read_only=True)
    try:
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (ids["item"],),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(lease) == (None, 2)

    with pytest.raises(HigherAuthorityVersionError):
        service.set_role(
            replace(
                replace_command,
                operation_id=_uuid("role:replace:stale"),
                request_id=_uuid("request:role:replace:stale"),
            )
        )
    assert _counts(factory) == (2, 0, 2, 2, 2)

    end = _role_command(
        factory,
        ids,
        operation="role:end",
        assignee=None,
        expected_assignment=replaced.assignment_id,
        expected_generation=replaced.assignment_generation,
        expected_assignment_version=replaced.assignment_row_version,
        expected_space_version=replaced.space_row_version,
        enable_writes=False,
    )
    ended = service.set_role(end)
    assert ended.status == "ended"
    assert ended.assignment_id is None
    assert ended.space_row_version == 5
    assert ended.write_enabled is False
    assert _open_team_role(factory, ids) is None
    assert _counts(factory) == (2, 0, 3, 3, 3)


def test_role_management_rejects_non_admin_and_ineligible_assignee_atomically(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)
    baseline = _counts(factory)
    command = _role_command(
        factory, ids, operation="role:forbidden", assignee="leader"
    )
    with pytest.raises(HigherAuthorityForbiddenError):
        service.set_role(
            replace(
                command,
                actor_user_id="member",
                actor_scope_epoch=_epoch(factory, "member"),
            )
        )
    assert _counts(factory) == baseline
    assert _open_team_role(factory, ids) is None

    with pytest.raises(HigherAuthorityRoleConflictError):
        service.set_role(
            _role_command(
                factory,
                ids,
                operation="role:ineligible",
                assignee="foreign",
            )
        )
    assert _counts(factory) == baseline
    assert _open_team_role(factory, ids) is None
    assert _space(factory, ids["team_space_a"]).write_enabled is False


@pytest.mark.scenario("HSPACE-010")
def test_role_write_enable_obeys_the_exact_level_rollout_flag(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    flags = replace(_flags(), team_writes=False)
    service = HigherAuthorityService(
        factory,
        ScopeResolver(flags),
        cursor_secret=_CURSOR_SECRET,
    )
    baseline = _counts(factory)

    with pytest.raises(HigherAuthorityNativeWriteDisabledError) as rejected:
        service.set_role(
            _role_command(
                factory,
                ids,
                operation="role:team-write-flag-disabled",
                assignee="leader",
            )
        )

    assert rejected.value.code == "NATIVE_WRITE_DISABLED"
    assert rejected.value.details == {"role": "team_leader"}
    assert _counts(factory) == baseline
    assert _open_team_role(factory, ids) is None
    assert _space(factory, ids["team_space_a"]).write_enabled is False


def test_role_failpoint_rolls_back_and_same_operation_can_retry(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    command = _role_command(
        factory, ids, operation="role:failpoint", assignee="leader"
    )

    def failpoint(name: str) -> None:
        if name == "higher_role.after_domain":
            raise RuntimeError("injected")

    with pytest.raises(HigherAuthorityFailpointTriggered):
        _service(factory, failpoint=failpoint).set_role(command)
    assert _counts(factory) == (0, 0, 0, 0, 0)
    assert _open_team_role(factory, ids) is None
    assert _space(factory, ids["team_space_a"]).row_version == 1

    retried = _service(factory).set_role(command)
    assert retried.status == "assigned"
    assert _counts(factory) == (1, 0, 1, 1, 1)


def _assign_leader(
    factory: ConnectionFactory,
    ids: dict[str, str],
    service: HigherAuthorityService,
):
    return service.set_role(
        _role_command(
            factory,
            ids,
            operation="role:distribution-leader",
            assignee="leader",
        )
    )


def _distribution_command(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    operation: str,
    target_scope_kind: str = "org_unit",
    target_scope_id: str | None = None,
    target_org_unit_id: str | None = None,
) -> CreateDistributionCommand:
    return CreateDistributionCommand(
        actor_user_id="leader",
        actor_scope_epoch=_epoch(factory, "leader"),
        operation_id=_uuid(operation),
        request_id=_uuid(f"request:{operation}"),
        source_space_id=ids["team_space_a"],
        source_item_id=ids["item"],
        source_revision_id=ids["revision"],
        target_org_unit_id=target_org_unit_id or ids["part_a"],
        target_scope_kind=target_scope_kind,  # type: ignore[arg-type]
        target_scope_id=target_scope_id or ids["part_a"],
        reason=f"distribution operation {operation}",
    )


def _grant(factory: ConnectionFactory, grant_id: str):
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return HigherSpaceRepository().get_distribution(connection, grant_id)
    finally:
        connection.close()


def _part_access_plan(factory: ConnectionFactory, ids: dict[str, str]):
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            resolved = ScopeResolver(_flags()).resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id="member",
                    captured_scope_epoch=_epoch(factory, "member"),
                ),
                ids["part_space_a"],
                Capability.READ,
            )
            return resolved.access_plan
    finally:
        connection.close()


def test_distribution_is_revision_bound_read_only_and_idempotent(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)
    _assign_leader(factory, ids, service)
    _seed_team_item(factory, ids)
    command = _distribution_command(
        factory, ids, operation="distribution:create"
    )

    created = service.create_distribution(command)
    assert created.status == "created"
    assert created.row_version == 1
    assert created.revoked is False
    assert _counts(factory) == (1, 1, 2, 2, 2)

    replay = service.create_distribution(
        replace(command, request_id=_uuid("request:distribution:create:replay"))
    )
    assert replay == replace(
        created, request_id=_uuid("request:distribution:create:replay")
    )
    with pytest.raises(HigherAuthorityIdempotencyError):
        service.create_distribution(replace(command, reason="semantic drift"))
    assert _counts(factory) == (1, 1, 2, 2, 2)

    plan = _part_access_plan(factory, ids)
    assert plan is not None
    assert len(plan.corpus_closure.distribution_overlays) == 1
    overlay = plan.corpus_closure.distribution_overlays[0]
    assert overlay.grant_id == created.grant_id
    assert overlay.source_space_id == ids["team_space_a"]
    assert overlay.source_item_id == ids["item"]
    assert overlay.source_revision_id == ids["revision"]
    assert overlay.capabilities == (Capability.READ,)

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            copied = connection.execute(
                "SELECT count(*) FROM knowledge_item WHERE space_id=?",
                (ids["part_space_a"],),
            ).fetchone()[0]
    finally:
        connection.close()
    assert copied == 0

    before = _counts(factory)
    with pytest.raises(HigherAuthorityDistributionConflictError):
        service.create_distribution(
            _distribution_command(
                factory,
                ids,
                operation="distribution:foreign-target",
                target_org_unit_id=ids["part_b"],
                target_scope_id=ids["part_b"],
            )
        )
    assert _counts(factory) == before


def test_distribution_ordinary_and_breakglass_revoke_are_exactly_authorized(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)
    _assign_leader(factory, ids, service)
    _seed_team_item(factory, ids)
    first = service.create_distribution(
        _distribution_command(factory, ids, operation="distribution:ordinary")
    )

    forbidden = RevokeDistributionCommand(
        actor_user_id="member",
        actor_scope_epoch=_epoch(factory, "member"),
        operation_id=_uuid("distribution:revoke:forbidden"),
        request_id=_uuid("request:distribution:revoke:forbidden"),
        source_space_id=ids["team_space_a"],
        grant_id=first.grant_id,
        expected_row_version=first.row_version,
        reason="member cannot revoke",
    )
    before = _counts(factory)
    with pytest.raises(HigherAuthorityForbiddenError):
        service.revoke_distribution(forbidden)
    assert _counts(factory) == before

    ordinary = replace(
        forbidden,
        actor_user_id="leader",
        actor_scope_epoch=_epoch(factory, "leader"),
        operation_id=_uuid("distribution:revoke:ordinary"),
        request_id=_uuid("request:distribution:revoke:ordinary"),
        reason="leader ordinary revoke",
    )
    revoked = service.revoke_distribution(ordinary)
    assert revoked.status == "revoked"
    assert revoked.row_version == 2
    assert revoked.revoked is True
    assert _part_access_plan(factory, ids).corpus_closure.distribution_overlays == ()

    second = service.create_distribution(
        _distribution_command(
            factory,
            ids,
            operation="distribution:breakglass",
            target_scope_kind="native_space",
            target_scope_id=ids["part_space_a"],
        )
    )
    wrong_confirmation = RevokeDistributionCommand(
        actor_user_id="admin",
        actor_scope_epoch=_epoch(factory, "admin"),
        operation_id=_uuid("distribution:breakglass:wrong"),
        request_id=_uuid("request:distribution:breakglass:wrong"),
        source_space_id=ids["team_space_a"],
        grant_id=second.grant_id,
        expected_row_version=second.row_version,
        reason="emergency revoke",
        break_glass=True,
        confirmation="wrong",
    )
    before = _counts(factory)
    with pytest.raises(HigherAuthorityForbiddenError):
        service.revoke_distribution(wrong_confirmation)
    assert _counts(factory) == before

    breakglass = replace(
        wrong_confirmation,
        operation_id=_uuid("distribution:breakglass:admin"),
        request_id=_uuid("request:distribution:breakglass:admin"),
        confirmation=f"REVOKE DISTRIBUTION {second.grant_id}",
    )
    emergency = service.revoke_distribution(breakglass)
    assert emergency.revoked is True
    stored = _grant(factory, second.grant_id)
    assert stored is not None
    assert stored.revoke_mode == "break_glass"
    assert stored.revoke_role_assignment_id is None
    assert stored.revoke_confirmation_sha256 is not None
    assert _counts(factory) == (1, 2, 5, 5, 5)


def test_distribution_history_cursor_is_signed_bound_and_stale_aware(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    service = _service(factory)
    _assign_leader(factory, ids, service)
    _seed_team_item(factory, ids)
    service.create_distribution(
        _distribution_command(factory, ids, operation="distribution:cursor:one")
    )
    service.create_distribution(
        _distribution_command(
            factory,
            ids,
            operation="distribution:cursor:two",
            target_scope_kind="native_space",
            target_scope_id=ids["part_space_a"],
        )
    )
    first_page = service.list_distributions(
        ListDistributionsQuery(
            actor_user_id="leader",
            actor_scope_epoch=_epoch(factory, "leader"),
            request_id=_uuid("request:distribution:list:first"),
            source_space_id=ids["team_space_a"],
            limit=1,
        )
    )
    assert len(first_page.items) == 1
    assert first_page.next_cursor is not None
    cursor = first_page.next_cursor

    second_page = service.list_distributions(
        ListDistributionsQuery(
            actor_user_id="leader",
            actor_scope_epoch=_epoch(factory, "leader"),
            request_id=_uuid("request:distribution:list:second"),
            source_space_id=ids["team_space_a"],
            cursor=cursor,
            limit=1,
        )
    )
    assert len(second_page.items) == 1
    assert second_page.items[0].id != first_page.items[0].id
    assert second_page.next_cursor is None

    with pytest.raises(HigherAuthorityCursorError):
        service.list_distributions(
            ListDistributionsQuery(
                actor_user_id="leader",
                actor_scope_epoch=_epoch(factory, "leader"),
                request_id=_uuid("request:distribution:list:tampered"),
                source_space_id=ids["team_space_a"],
                cursor=cursor[:-1] + ("0" if cursor[-1] != "0" else "1"),
                limit=1,
            )
        )
    with pytest.raises(HigherAuthorityCursorError):
        service.list_distributions(
            ListDistributionsQuery(
                actor_user_id="admin",
                actor_scope_epoch=_epoch(factory, "admin"),
                request_id=_uuid("request:distribution:list:actor-bound"),
                source_space_id=ids["team_space_a"],
                cursor=cursor,
                limit=1,
            )
        )

    anchor = first_page.items[0]
    service.revoke_distribution(
        RevokeDistributionCommand(
            actor_user_id="leader",
            actor_scope_epoch=_epoch(factory, "leader"),
            operation_id=_uuid("distribution:cursor:revoke-anchor"),
            request_id=_uuid("request:distribution:cursor:revoke-anchor"),
            source_space_id=ids["team_space_a"],
            grant_id=anchor.id,
            expected_row_version=anchor.row_version,
            reason="invalidate cursor anchor",
        )
    )
    with pytest.raises(HigherAuthorityCursorError):
        service.list_distributions(
            ListDistributionsQuery(
                actor_user_id="leader",
                actor_scope_epoch=_epoch(factory, "leader"),
                request_id=_uuid("request:distribution:list:stale"),
                source_space_id=ids["team_space_a"],
                cursor=cursor,
                limit=1,
            )
        )


def test_distribution_failpoints_roll_back_evidence_domain_and_dedup(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    stable = _service(factory)
    _assign_leader(factory, ids, stable)
    _seed_team_item(factory, ids)
    create = _distribution_command(
        factory, ids, operation="distribution:failpoint:create"
    )

    def create_failpoint(name: str) -> None:
        if name == "distribution_create.after_evidence":
            raise RuntimeError("injected create failure")

    baseline = _counts(factory)
    with pytest.raises(HigherAuthorityFailpointTriggered):
        _service(factory, failpoint=create_failpoint).create_distribution(create)
    assert _counts(factory) == baseline
    created = stable.create_distribution(create)
    assert _counts(factory) == (1, 1, 2, 2, 2)

    revoke = RevokeDistributionCommand(
        actor_user_id="leader",
        actor_scope_epoch=_epoch(factory, "leader"),
        operation_id=_uuid("distribution:failpoint:revoke"),
        request_id=_uuid("request:distribution:failpoint:revoke"),
        source_space_id=ids["team_space_a"],
        grant_id=created.grant_id,
        expected_row_version=created.row_version,
        reason="failpoint revoke",
    )

    def revoke_failpoint(name: str) -> None:
        if name == "distribution_revoke.after_domain":
            raise RuntimeError("injected revoke failure")

    with pytest.raises(HigherAuthorityFailpointTriggered):
        _service(factory, failpoint=revoke_failpoint).revoke_distribution(revoke)
    assert _counts(factory) == (1, 1, 2, 2, 2)
    unchanged = _grant(factory, created.grant_id)
    assert unchanged is not None
    assert unchanged.revoked_at_ms is None
    assert unchanged.row_version == 1

    retried = stable.revoke_distribution(revoke)
    assert retried.revoked is True
    assert _counts(factory) == (1, 1, 3, 3, 3)


def test_agent_runtime_requires_and_binds_higher_cursor_secret(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    with pytest.raises(ValueError, match="higher-space reads"):
        compose_canonical_agent_runtime(
            factory,
            flags=_flags(),
            principal_resolver=lambda: None,
        )
    ids = _seed(factory)
    higher = _service(factory)
    _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_search_item(
                connection,
                label="team-agent-context",
                item_id=ids["item"],
                revision_id=ids["revision"],
                space_id=ids["team_space_a"],
                actor="leader",
                title="Team note",
            )
    finally:
        connection.close()
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=_flags(),
        principal_resolver=lambda: None,
        higher_authority_cursor_secret=_CURSOR_SECRET,
    )
    assert isinstance(runtime.services.higher_authority, HigherAuthorityService)
    epoch = _epoch(factory, "leader")
    created = runtime.services.conversations.create(
        CreateConversationCommand(
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            operation_id=_uuid("higher-runtime:conversation:create"),
            request_id=_uuid("higher-runtime:conversation:create-request"),
            space_id=ids["team_space_a"],
            title="Team-native Agent context",
        )
    )
    accepted = runtime.services.conversations.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            operation_id=_uuid("higher-runtime:turn:accept"),
            request_id=_uuid("higher-runtime:turn:accept-request"),
            conversation_id=created.conversation.id,
            user_content={"text": "Team"},
        )
    )
    bundle = runtime.services.agent_context.compose(
        AgentTurnContextRequest(
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            conversation_id=created.conversation.id,
            turn_id=accepted.turn_id,
            neighbor_limit=0,
            event_limit=0,
        )
    )
    assert bundle.space_id == ids["team_space_a"]
    assert [hit.item_id for hit in bundle.retrieval.hits] == [ids["item"]]
    assert bundle.retrieval.hits[0].origin == "native"
    assert bundle.retrieval.corpus_closure_sha256 is not None


def _organization_service(factory: ConnectionFactory) -> OrganizationService:
    return OrganizationService(
        factory,
        preview_hmac_key=_ORG_PREVIEW_SECRET,
        flags=_flags(),
    )


def _move_preview(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    label: str,
):
    return _organization_service(factory).preview_change(
        OrgChangePreviewCommand(
            actor_user_id="admin",
            actor_scope_epoch=_epoch(factory, "admin"),
            request_id=_uuid(f"request:{label}:preview"),
            unit_id=ids["part_a"],
            expected_row_version=1,
            change="move",
            new_parent_id=ids["team_b"],
        )
    )


def _seed_ready_aggregate_vectors(
    factory: ConnectionFactory, ids: dict[str, str]
) -> tuple[str, ...]:
    connection = factory.open()
    try:
        with transaction(connection):
            responsibility_space = SpaceRepository().get_org(
                connection, RESPONSIBILITY_ROOT_ID
            )
            assert responsibility_space is not None
            space_ids = (
                ids["team_space_a"],
                ids["team_space_b"],
                responsibility_space.id,
            )
            for space_id in space_ids:
                org_version = connection.execute(
                    """SELECT org.row_version
                         FROM knowledge_space AS space
                         JOIN org_unit AS org ON org.id=space.owner_org_unit_id
                        WHERE space.id=?""",
                    (space_id,),
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO aggregate_generation_vector(
                         selected_space_id,source_vector_json,
                         source_vector_sha256,home_edge_generation,
                         desired_generation,built_generation,manifest_sha256,
                         closure_proof_sha256,organization_row_version,status,
                         last_error_json,created_at,updated_at,row_version
                       ) VALUES (?,'[]',?,0,5,5,?, ?,?,'ready',NULL,2500,2500,1)""",
                    (
                        space_id,
                        _EMPTY_SHA,
                        _EMPTY_SHA,
                        _EMPTY_SHA,
                        int(org_version),
                    ),
                )
            return space_ids
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-005", "HSPACE-014")
def test_part_move_does_not_displace_a_direct_team_leader(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    assigned = _service(factory).set_role(
        _role_command(
            factory,
            ids,
            operation="role:org-move-blocker",
            assignee="leader",
        )
    )

    preview = _move_preview(factory, ids, label="org-move-blocked")

    assert assigned.assignment_id is not None
    assert preview.allowed is True
    assert not any(blocker["code"] == "OPEN_HIGHER_ROLE" for blocker in preview.blockers)
    connection = factory.open(read_only=True)
    try:
        assert OrganizationRepository().require_unit(
            connection, ids["part_a"]
        ).parent_id == ids["team_a"]
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-006", "HSPACE-011", "HSPACE-013", "HSPACE-014")
def test_org_move_atomically_revokes_lost_overlays_and_invalidates_union(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _service(factory)
    assigned = _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    created = higher.create_distribution(
        _distribution_command(
            factory,
            ids,
            operation="distribution:org-move-cascade",
        )
    )
    before_plan = _part_access_plan(factory, ids)
    assert before_plan is not None
    assert [
        overlay.grant_id
        for overlay in before_plan.corpus_closure.distribution_overlays
    ] == [created.grant_id]
    ended = higher.set_role(
        _role_command(
            factory,
            ids,
            operation="role:org-move-close",
            assignee=None,
            expected_assignment=assigned.assignment_id,
            expected_generation=assigned.assignment_generation,
            expected_assignment_version=assigned.assignment_row_version,
            expected_space_version=assigned.space_row_version,
            enable_writes=False,
        )
    )
    assert ended.status == "ended"
    affected_vectors = _seed_ready_aggregate_vectors(factory, ids)
    organization = _organization_service(factory)
    preview = _move_preview(factory, ids, label="org-move-cascade")
    assert preview.allowed is True
    command = MoveOrgUnitCommand(
        actor_user_id="admin",
        actor_scope_epoch=_epoch(factory, "admin"),
        operation_id=_uuid("organization:org-move-cascade"),
        request_id=_uuid("request:organization:org-move-cascade"),
        unit_id=ids["part_a"],
        expected_row_version=1,
        preview_token=preview.preview_token,
        new_parent_id=ids["team_b"],
        resolution=OrgMoveResolution(),
        reason="Move Part A and invalidate its former ancestry",
    )

    def fail_after_all_move_domain_effects(name: str) -> None:
        if name == "organization.move.after_closure_invalidation":
            raise RuntimeError("injected organization-move rollback")

    faulting = OrganizationService(
        factory,
        preview_hmac_key=_ORG_PREVIEW_SECRET,
        flags=_flags(),
        failpoint=fail_after_all_move_domain_effects,
    )
    with pytest.raises(RuntimeError, match="injected organization-move rollback"):
        faulting.move_unit(command)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            assert OrganizationRepository().require_unit(
                connection, ids["part_a"]
            ).parent_id == ids["team_a"]
            rolled_back_grant = HigherSpaceRepository().get_distribution(
                connection, created.grant_id
            )
            assert rolled_back_grant is not None
            assert rolled_back_grant.revoked_at_ms is None
            vector_states = connection.execute(
                """SELECT desired_generation,built_generation,status,row_version
                     FROM aggregate_generation_vector"""
            ).fetchall()
            assert all(tuple(row) == (5, 5, "ready", 1) for row in vector_states)
            assert connection.execute(
                "SELECT count(*) FROM operation_dedup WHERE operation_id=?",
                (command.operation_id,),
            ).fetchone()[0] == 0
    finally:
        connection.close()

    moved = organization.move_unit(command)
    assert moved.status == "moved" and moved.row_version == 2
    replay = organization.move_unit(
        replace(
            command,
            request_id=_uuid("request:organization:org-move-cascade:replay"),
        )
    )
    assert replay == replace(
        moved,
        request_id=_uuid("request:organization:org-move-cascade:replay"),
    )

    revoked = _grant(factory, created.grant_id)
    assert revoked is not None
    assert revoked.revoked_at_ms is not None
    assert revoked.revoke_mode == "break_glass"
    assert revoked.revoke_operation_id == command.operation_id
    assert revoked.row_version == 2
    after_part = _part_access_plan(factory, ids)
    assert after_part is not None
    assert after_part.corpus_closure.distribution_overlays == ()

    resolver = ScopeResolver(_flags())
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            principal = SessionPrincipal(
                user_id="member",
                captured_scope_epoch=_epoch(factory, "member"),
            )
            with pytest.raises(ScopeNotFoundError):
                resolver.resolve_in_transaction(
                    connection,
                    principal,
                    ids["team_space_a"],
                    Capability.READ,
                )
            new_team = resolver.resolve_in_transaction(
                connection,
                principal,
                ids["team_space_b"],
                Capability.READ,
            ).access_plan
            assert new_team is not None
            assert ids["part_space_a"] in {
                source.source_space_id
                for source in new_team.corpus_closure.sources
            }
            vectors = connection.execute(
                """SELECT selected_space_id,desired_generation,built_generation,
                          status,manifest_sha256,row_version
                     FROM aggregate_generation_vector
                    ORDER BY selected_space_id"""
            ).fetchall()
            assert {str(row["selected_space_id"]) for row in vectors} == set(
                affected_vectors
            )
            assert all(
                (
                    int(row["desired_generation"]),
                    int(row["built_generation"]),
                    str(row["status"]),
                    row["manifest_sha256"],
                    int(row["row_version"]),
                )
                == (6, 5, "idle", None, 2)
                for row in vectors
            )
            cascade_audits = connection.execute(
                """SELECT count(*) FROM audit_event
                    WHERE action='knowledge_distribution.org_move_invalidate'
                      AND operation_id=?""",
                (command.operation_id,),
            ).fetchone()[0]
            cascade_outbox = connection.execute(
                """SELECT count(*) FROM outbox_event
                    WHERE event_type=
                          'knowledge_distribution.invalidated_by_org_move'
                      AND payload_json LIKE ?""",
                (f'%{created.grant_id}%',),
            ).fetchone()[0]
            assert (cascade_audits, cascade_outbox) == (1, 1)
    finally:
        connection.close()


def _publish_search_item(
    connection,
    *,
    label: str,
    item_id: str,
    revision_id: str,
    space_id: str,
    actor: str,
    title: str,
) -> None:
    finding_id = _uuid(f"retrieval:finding:{label}")
    job_id = _uuid(f"retrieval:job:{label}")
    transition_id = _uuid(f"retrieval:transition:{label}")
    input_ref_json = json.dumps(
        {
            "item_id": item_id,
            "revision_id": revision_id,
            "rule_version": "sbe-2.3",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    authorization_json = '{"actor_kind":"system","schema_version":1}'
    connection.execute(
        """INSERT INTO durable_job(
             id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,
             job_type,handler_version,effect_key,input_ref_json,input_sha256,
             expected_state_json,authorization_snapshot_json,
             authorization_snapshot_sha256,authorization_scope_epoch,
             authorization_part_id,authorization_capabilities_json,state,
             claim_owner,claim_expires_at,attempt_count,max_attempts,cancellable,
             cancel_requested,progress_permille,result_json,error_json,
             available_at,created_at,updated_at,completed_at
           ) VALUES (?,NULL,NULL,'system',NULL,?,'curation.rescan','v1',?,?,?,
                     '{}',?,?,NULL,NULL,'[]','queued',NULL,NULL,0,3,0,0,0,
                     NULL,NULL,3000,3000,3000,NULL)""",
        (
            job_id,
            space_id,
            f"retrieval:curation:{label}",
            input_ref_json,
            hashlib.sha256(input_ref_json.encode("utf-8")).hexdigest(),
            authorization_json,
            hashlib.sha256(authorization_json.encode("utf-8")).hexdigest(),
        ),
    )
    connection.execute(
        """UPDATE durable_job
              SET state='claimed',claim_owner='retrieval-fixture',
                  claim_expires_at=9000,attempt_count=1,updated_at=3001
            WHERE id=?""",
        (job_id,),
    )
    connection.execute(
        """UPDATE durable_job SET state='running',updated_at=3002 WHERE id=?""",
        (job_id,),
    )
    connection.execute(
        """INSERT INTO curation_finding(
             id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
             required_reviewer,visibility_effect,proposed_action,source_item_id,
             source_revision_id,target_item_id,target_revision_id,detector_name,
             detector_version,rule_version,model_id,prompt_version,confidence,
             evidence_json,proposed_action_json,reviewed_by,reviewed_at,
             review_reason,applied_operation_id,applied_dedup_id,applied_job_id,
             applied_by,applied_at,last_application_error_json,
             created_actor_kind,created_by,created_at,updated_at,row_version
           ) VALUES (?,?,'curation_clear','T1','info','applied','automatic',
             'none','none','publish',?,?,NULL,NULL,'sbe-2.3','v1','sbe-2.3',
             NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,?,NULL,3010,NULL,
             'system',NULL,3010,3010,1)""",
        (finding_id, space_id, item_id, revision_id, job_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,
                     NULL,?,NULL,NULL,'system',NULL,2,'publish',3030)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            job_id,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item
              SET curation_status='published',last_curation_transition_id=?,
                  updated_by=?,updated_at=3030,row_version=2
            WHERE id=?""",
        (transition_id, actor, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,3030)""",
        (item_id, space_id, revision_id, title, ""),
    )
    connection.execute(
        """UPDATE durable_job
              SET state='succeeded',claim_owner=NULL,claim_expires_at=NULL,
                  progress_permille=1000,result_json='{}',error_json=NULL,
                  updated_at=3031,completed_at=3031
            WHERE id=?""",
        (job_id,),
    )


def _seed_searchable_closure_items(
    factory: ConnectionFactory, ids: dict[str, str]
) -> dict[str, str]:
    item_ids = {
        "part_a_item": _uuid("retrieval:item:part-a"),
        "part_a_revision": _uuid("retrieval:revision:part-a"),
        "part_b_item": _uuid("retrieval:item:part-b"),
        "part_b_revision": _uuid("retrieval:revision:part-b"),
    }
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_search_item(
                connection,
                label="team-a",
                item_id=ids["item"],
                revision_id=ids["revision"],
                space_id=ids["team_space_a"],
                actor="leader",
                title="Team note",
            )
            for suffix, space_id, author in (
                ("a", ids["part_space_a"], "member"),
                ("b", ids["part_space_b"], "foreign"),
            ):
                item_id = item_ids[f"part_{suffix}_item"]
                revision_id = item_ids[f"part_{suffix}_revision"]
                title = f"Part note {suffix.upper()}"
                connection.execute(
                    """INSERT INTO knowledge_item(
                         id,space_id,lineage_group_id,lifecycle_status,
                         curation_status,current_revision_id,
                         last_curation_transition_id,
                         latest_deactivation_episode_id,redirect_item_id,
                         created_by,updated_by,created_at,updated_at,row_version
                       ) VALUES (?, ?,NULL,'active','provisional',?,NULL,NULL,NULL,
                                 ?,?,3000,3000,1)""",
                    (item_id, space_id, revision_id, author, author),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision(
                         id,item_id,revision_no,parent_revision_id,
                         base_revision_id,title,raw_content_sha256,
                         normalized_content_sha256,content_size_bytes,
                         normalizer_version,metadata_json,change_kind,
                         change_reason,created_by,created_at
                       ) VALUES (?,?,1,NULL,NULL,?,?,?,0,'markdown_lf_v1','{}',
                                 'initial','fixture',?,3000)""",
                    (revision_id, item_id, title, _EMPTY_SHA, _EMPTY_SHA, author),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision_body(
                         revision_id,item_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,'',NULL,0,?,3000)""",
                    (revision_id, item_id, _EMPTY_SHA),
                )
                _publish_search_item(
                    connection,
                    label=f"part-{suffix}",
                    item_id=item_id,
                    revision_id=revision_id,
                    space_id=space_id,
                    actor=author,
                    title=title,
                )
    finally:
        connection.close()
    return item_ids


def _seed_explicit_reference_fixture(
    factory: ConnectionFactory,
    ids: dict[str, str],
    searchable: dict[str, str],
    *,
    distribution_grant_id: str,
) -> str:
    connection = factory.open()
    try:
        with transaction(connection):
            grant = HigherSpaceRepository().get_distribution(
                connection, distribution_grant_id
            )
            assert grant is not None
            connection.execute(
                """INSERT INTO knowledge_explicit_reference(
                     id,source_space_id,source_item_id,source_revision_id,
                     ordinal,syntax_version,target_space_id,target_item_id,
                     target_revision_id,closure_proof_sha256,actor_user_id,
                     operation_id,effect_key,audit_event_id,outbox_event_id,
                     availability_status,unavailable_reason,created_at,
                     updated_at,row_version
                   ) VALUES (?,?,?,?,1,'item-ref-v1',?,?,?,?,?,?,?,?,?,
                             'available',NULL,4000,4000,1)""",
                (
                    _uuid("retrieval:reference:a"),
                    ids["team_space_a"],
                    ids["item"],
                    ids["revision"],
                    ids["part_space_a"],
                    searchable["part_a_item"],
                    searchable["part_a_revision"],
                    hashlib.sha256(b"closure:a").hexdigest(),
                    "leader",
                    _uuid("retrieval:reference:operation:a"),
                    hashlib.sha256(b"reference:a").hexdigest(),
                    grant.created_audit_event_id,
                    grant.created_outbox_event_id,
                ),
            )
    finally:
        connection.close()

    edit_flags = replace(_flags(), part_writes=True)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO org_role_assignment(
                     id,user_id,org_unit_id,role,valid_from,valid_to,
                     assigned_by,reason
                   ) VALUES (?,'member',?,'part_leader',4040,NULL,'admin',
                             'explicit-reference newer revision fixture')""",
                (_uuid("retrieval:reference:part-a-leader"), ids["part_a"]),
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=1,updated_at=4050,row_version=row_version+1
                    WHERE id=?""",
                (ids["part_space_a"],),
            )
    finally:
        connection.close()
    editor = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1",
            keys={"lease-v1": _LEASE_KEY},
        ),
        scope_resolver=ScopeResolver(edit_flags),
    )
    raw_token = _token("retrieval:reference:part-a-edit")
    acquire = AcquireLeaseCommand(
        principal=SessionPrincipal("member", _epoch(factory, "member")),
        space_id=ids["part_space_a"],
        item_id=searchable["part_a_item"],
        editor_session_id=_uuid("retrieval:reference:part-a-editor"),
        operation_id=_uuid("retrieval:reference:part-a-acquire"),
        request_id=_uuid("retrieval:reference:part-a-acquire-request"),
        raw_token=raw_token,
    )
    lease = editor.acquire(acquire)
    saved = editor.save(
        SaveItemCommand(
            principal=acquire.principal,
            space_id=acquire.space_id,
            item_id=acquire.item_id,
            editor_session_id=acquire.editor_session_id,
            lease_id=lease.lease_id,
            fencing_generation=lease.fencing_generation,
            expected_item_row_version=2,
            expected_revision_id=searchable["part_a_revision"],
            operation_id=_uuid("retrieval:reference:part-a-save"),
            request_id=_uuid("retrieval:reference:part-a-save-request"),
            markdown="# Part note A revised\nreviewed body\n",
            reason="newer explicit-reference fixture",
            raw_token=raw_token,
        )
    )
    scope = ScopeResolver(edit_flags)
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory, scope_resolver=scope
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    for index in range(5):
        _recovery, claim = kernel.recover_and_claim(
            claim_owner=f"retrieval-reference-curation-{index}",
            lease_ms=30_000,
        )
        if claim is None:
            break
        kernel.run_claim(claim)
        connection = factory.open(read_only=True)
        try:
            state = connection.execute(
                """SELECT current_revision_id,curation_status
                     FROM knowledge_item WHERE id=?""",
                (searchable["part_a_item"],),
            ).fetchone()
        finally:
            connection.close()
        if state is not None and tuple(state) == (saved.revision_id, "published"):
            break
    connection = factory.open(read_only=True)
    try:
        state = connection.execute(
            """SELECT current_revision_id,curation_status
                 FROM knowledge_item WHERE id=?""",
            (searchable["part_a_item"],),
        ).fetchone()
    finally:
        connection.close()
    assert state is not None
    assert tuple(state) == (saved.revision_id, "published")
    return saved.revision_id


@pytest.mark.scenario(
    "HSPACE-006",
    "HSPACE-007",
    "HSPACE-008",
    "HSPACE-009",
    "HSPACE-012",
)
def test_retrieval_consumes_exact_closure_and_preserves_every_origin(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _service(factory)
    assigned = _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    searchable = _seed_searchable_closure_items(factory, ids)
    created = higher.create_distribution(
        _distribution_command(
            factory,
            ids,
            operation="distribution:retrieval-overlay",
        )
    )
    sibling_distribution = higher.create_distribution(
        _distribution_command(
            factory,
            ids,
            operation="distribution:retrieval-sibling",
            target_org_unit_id=ids["part_c"],
            target_scope_id=ids["part_c"],
        )
    )
    newer_part_a_revision = _seed_explicit_reference_fixture(
        factory,
        ids,
        searchable,
        distribution_grant_id=created.grant_id,
    )
    higher.set_role(
        _role_command(
            factory,
            ids,
            operation="role:retrieval-close",
            assignee=None,
            expected_assignment=assigned.assignment_id,
            expected_generation=assigned.assignment_generation,
            expected_assignment_version=assigned.assignment_row_version,
            expected_space_version=assigned.space_row_version,
            enable_writes=False,
        )
    )
    retrieval = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
    )

    part = retrieval.retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
            query="note",
            top_k=10,
            neighbor_limit=0,
            event_limit=0,
        )
    )
    part_by_id = {hit.item_id: hit for hit in part.hits}
    assert set(part_by_id) == {ids["item"], searchable["part_a_item"]}
    assert part_by_id[ids["item"]].origin == "overlay"
    assert part_by_id[ids["item"]].overlay_grant_id == created.grant_id
    assert part_by_id[ids["item"]].space_id == ids["team_space_a"]
    assert part_by_id[searchable["part_a_item"]].origin == "native"
    assert searchable["part_b_item"] not in part_by_id
    assert part.corpus_closure_sha256 is not None

    team = retrieval.retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["team_space_a"],
            query="note",
            top_k=10,
            neighbor_limit=0,
            event_limit=0,
        )
    )
    team_by_id = {hit.item_id: hit for hit in team.hits}
    assert set(team_by_id) == {ids["item"], searchable["part_a_item"]}
    assert team_by_id[ids["item"]].origin == "native"
    assert team_by_id[searchable["part_a_item"]].origin == "inherited"
    assert searchable["part_b_item"] not in team_by_id

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            responsibility = SpaceRepository().get_org(
                connection, RESPONSIBILITY_ROOT_ID
            )
            assert responsibility is not None
            responsibility_space_id = responsibility.id
    finally:
        connection.close()
    responsibility_result = retrieval.retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=responsibility_space_id,
            query="note",
            top_k=10,
            neighbor_limit=0,
            event_limit=0,
        )
    )
    responsibility_by_id = {
        hit.item_id: hit for hit in responsibility_result.hits
    }
    assert set(responsibility_by_id) == {
        ids["item"],
        searchable["part_a_item"],
        searchable["part_b_item"],
    }
    assert all(hit.origin == "inherited" for hit in responsibility_by_id.values())
    assert all(
        hit.selected_view_space_id == responsibility_space_id
        for hit in responsibility_by_id.values()
    )

    management = CanonicalManagementReadService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
    )
    part_page = management.list_items(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
        )
    )
    part_items = {item.id: item for item in part_page.items}
    assert set(part_items) == {ids["item"], searchable["part_a_item"]}
    assert part_items[ids["item"]].origin == "overlay"
    assert part_items[ids["item"]].overlay_grant_id == created.grant_id
    assert part_items[ids["item"]].capabilities == ("read",)
    assert part_items[searchable["part_a_item"]].origin == "native"
    assert part_page.corpus_closure_sha256 is not None

    overlay_detail = management.get_item(
        ItemManagementReadRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
            item_id=ids["item"],
        )
    )
    assert overlay_detail.source_space_id == ids["team_space_a"]
    assert overlay_detail.selected_view_space_id == ids["part_space_a"]
    assert overlay_detail.origin == "overlay"
    assert overlay_detail.inherited is True
    assert overlay_detail.capabilities == ("read",)
    assert [reference.ordinal for reference in overlay_detail.references] == [1]
    assert overlay_detail.references[0].availability_status == "available"
    assert overlay_detail.references[0].target is not None
    assert overlay_detail.references[0].target.item_id == searchable["part_a_item"]
    assert overlay_detail.references[0].target.current_revision_id == newer_part_a_revision
    assert overlay_detail.references[0].target.newer_revision_available is True
    assert overlay_detail.references[0].target.origin == "native"
    sibling_detail = management.get_item(
        ItemManagementReadRequest(
            actor_user_id="sibling",
            actor_scope_epoch=_epoch(factory, "sibling"),
            space_id=ids["part_space_c"],
            item_id=ids["item"],
        )
    )
    assert sibling_detail.origin == "overlay"
    assert sibling_detail.overlay_grant_id == sibling_distribution.grant_id
    assert [reference.ordinal for reference in sibling_detail.references] == [1]
    assert sibling_detail.references[0].availability_status == "unavailable"
    assert sibling_detail.references[0].unavailable_reason == "target_unavailable"
    assert sibling_detail.references[0].target is None
    overlay_revisions = management.list_revisions(
        ItemRevisionPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
            item_id=ids["item"],
        )
    )
    assert [revision.id for revision in overlay_revisions.items] == [ids["revision"]]
    assert overlay_revisions.origin == "overlay"
    assert overlay_revisions.source_space_id == ids["team_space_a"]

    team_page = management.list_items(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["team_space_a"],
        )
    )
    team_items = {item.id: item for item in team_page.items}
    assert set(team_items) == {ids["item"], searchable["part_a_item"]}
    assert team_items[ids["item"]].origin == "native"
    assert team_items[searchable["part_a_item"]].origin == "inherited"
    assert searchable["part_b_item"] not in team_items

    responsibility_page = management.list_items(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=responsibility_space_id,
        )
    )
    responsibility_items = {item.id: item for item in responsibility_page.items}
    assert set(responsibility_items) == {
        ids["item"],
        searchable["part_a_item"],
        searchable["part_b_item"],
    }
    assert all(item.origin == "inherited" for item in responsibility_items.values())
    assert all(
        item.selected_view_space_id == responsibility_space_id
        for item in responsibility_items.values()
    )
    responsibility_detail = management.get_item(
        ItemManagementReadRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=responsibility_space_id,
            item_id=ids["item"],
        )
    )
    assert [reference.availability_status for reference in responsibility_detail.references] == [
        "available",
    ]
    assert all(reference.target is not None for reference in responsibility_detail.references)
    assert all(
        reference.target.origin == "inherited"
        for reference in responsibility_detail.references
        if reference.target is not None
    )

    findings = management.list_findings(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["team_space_a"],
        )
    )
    finding_origins = {finding.source_item_id: finding.origin for finding in findings.items}
    assert finding_origins[ids["item"]] == "native"
    assert finding_origins[searchable["part_a_item"]] == "inherited"
    assert searchable["part_b_item"] not in finding_origins

    finding_service = VersionedFindingService(
        factory,
        ScopeResolver(_flags()),
    )
    inherited_finding = finding_service.read(
        ReadFindingRequest(
            principal=SessionPrincipal("member", _epoch(factory, "member")),
            space_id=ids["team_space_a"],
            finding_id=_uuid("retrieval:finding:part-a"),
        )
    )
    assert inherited_finding.source_space_id == ids["part_space_a"]
    assert inherited_finding.selected_view_space_id == ids["team_space_a"]
    assert inherited_finding.origin == "inherited"
    assert inherited_finding.capabilities == ("read",)
    overlay_finding = finding_service.read(
        ReadFindingRequest(
            principal=SessionPrincipal("member", _epoch(factory, "member")),
            space_id=ids["part_space_a"],
            finding_id=_uuid("retrieval:finding:team-a"),
        )
    )
    assert overlay_finding.source_space_id == ids["team_space_a"]
    assert overlay_finding.selected_view_space_id == ids["part_space_a"]
    assert overlay_finding.origin == "overlay"
    assert overlay_finding.overlay_grant_id == created.grant_id
    assert overlay_finding.capabilities == ("read",)
    with pytest.raises(FindingNotFoundError):
        finding_service.read(
            ReadFindingRequest(
                principal=SessionPrincipal("member", _epoch(factory, "member")),
                space_id=ids["team_space_a"],
                finding_id=_uuid("retrieval:finding:part-b"),
            )
        )

    part_graph = management.graph(
        SpaceManagementReadRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
        )
    )
    graph_nodes = {node.id: node for node in part_graph.nodes}
    assert set(graph_nodes) == {ids["item"], searchable["part_a_item"]}
    assert graph_nodes[ids["item"]].origin == "overlay"
    assert graph_nodes[ids["item"]].capabilities == ("read",)
    assert part_graph.corpus_closure_sha256 == part_page.corpus_closure_sha256

    part_activity = management.list_activity(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=_epoch(factory, "member"),
            space_id=ids["part_space_a"],
        )
    )
    distribution_events = [
        event
        for event in part_activity.items
        if event.action == "knowledge_distribution.create"
        and event.reason == "distribution operation distribution:retrieval-overlay"
    ]
    assert len(distribution_events) == 1
    assert distribution_events[0].item_id == ids["item"]
    assert distribution_events[0].origin == "overlay"
    assert distribution_events[0].overlay_grant_id == created.grant_id
    assert distribution_events[0].capabilities == ("read",)

    with pytest.raises(ScopeNotFoundError):
        management.get_item(
            ItemManagementReadRequest(
                actor_user_id="member",
                actor_scope_epoch=_epoch(factory, "member"),
                space_id=ids["team_space_a"],
                item_id=searchable["part_b_item"],
            )
        )


@pytest.mark.scenario("HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_higher_native_edit_lease_and_job_fence_survive_no_role_aba(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _service(factory)
    assigned = _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_search_item(
                connection,
                label="team-edit",
                item_id=ids["item"],
                revision_id=ids["revision"],
                space_id=ids["team_space_a"],
                actor="leader",
                title="Team note",
            )
    finally:
        connection.close()

    editor = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1",
            keys={"lease-v1": _LEASE_KEY},
        ),
        scope_resolver=ScopeResolver(_flags()),
    )
    with pytest.raises(LeaseAuthorizationError):
        editor.acquire(
            AcquireLeaseCommand(
                principal=SessionPrincipal("member", _epoch(factory, "member")),
                space_id=ids["team_space_a"],
                item_id=ids["item"],
                editor_session_id=_uuid("higher-edit:member-session"),
                operation_id=_uuid("higher-edit:member-acquire"),
                request_id=_uuid("higher-edit:member-acquire-request"),
                raw_token=_token("higher-edit:member"),
            )
        )

    old_token = _token("higher-edit:old")
    old_acquire_command = AcquireLeaseCommand(
        principal=SessionPrincipal("leader", _epoch(factory, "leader")),
        space_id=ids["team_space_a"],
        item_id=ids["item"],
        editor_session_id=_uuid("higher-edit:old-session"),
        operation_id=_uuid("higher-edit:old-acquire"),
        request_id=_uuid("higher-edit:old-acquire-request"),
        raw_token=old_token,
    )
    old_lease = editor.acquire(old_acquire_command)
    replacement = higher.set_role(
        _role_command(
            factory,
            ids,
            operation="higher-edit:replace",
            assignee="replacement",
            expected_assignment=assigned.assignment_id,
            expected_generation=assigned.assignment_generation,
            expected_assignment_version=assigned.assignment_row_version,
            expected_space_version=assigned.space_row_version,
        )
    )
    reassigned = higher.set_role(
        _role_command(
            factory,
            ids,
            operation="higher-edit:reassign",
            assignee="leader",
            expected_assignment=replacement.assignment_id,
            expected_generation=replacement.assignment_generation,
            expected_assignment_version=replacement.assignment_row_version,
            expected_space_version=replacement.space_row_version,
        )
    )
    with pytest.raises((LeaseLostError, LeaseScopeStaleError)):
        editor.save(
            SaveItemCommand(
                principal=old_acquire_command.principal,
                space_id=ids["team_space_a"],
                item_id=ids["item"],
                editor_session_id=old_acquire_command.editor_session_id,
                lease_id=old_lease.lease_id,
                fencing_generation=old_lease.fencing_generation,
                expected_item_row_version=2,
                expected_revision_id=ids["revision"],
                operation_id=_uuid("higher-edit:stale-save"),
                request_id=_uuid("higher-edit:stale-save-request"),
                markdown="# stale must not commit\n",
                reason="stale role lease",
                raw_token=old_token,
            )
        )

    new_token = _token("higher-edit:new")
    new_acquire_command = AcquireLeaseCommand(
        principal=SessionPrincipal("leader", _epoch(factory, "leader")),
        space_id=ids["team_space_a"],
        item_id=ids["item"],
        editor_session_id=_uuid("higher-edit:new-session"),
        operation_id=_uuid("higher-edit:new-acquire"),
        request_id=_uuid("higher-edit:new-acquire-request"),
        raw_token=new_token,
    )
    new_lease = editor.acquire(new_acquire_command)
    saved = editor.save(
        SaveItemCommand(
            principal=new_acquire_command.principal,
            space_id=ids["team_space_a"],
            item_id=ids["item"],
            editor_session_id=new_acquire_command.editor_session_id,
            lease_id=new_lease.lease_id,
            fencing_generation=new_lease.fencing_generation,
            expected_item_row_version=2,
            expected_revision_id=ids["revision"],
            operation_id=_uuid("higher-edit:fresh-save"),
            request_id=_uuid("higher-edit:fresh-save-request"),
            markdown="# Team native edit\nbody\n",
            reason="current leader edit",
            raw_token=new_token,
        )
    )
    assert saved.status == "updated"
    assert saved.row_version == 3
    connection = factory.open(read_only=True)
    try:
        job = connection.execute(
            """SELECT expected_state_json FROM durable_job
                 WHERE job_type='curation.rescan' AND actor_user_id='leader'
                 ORDER BY created_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        assert job is not None
        fence = json.loads(job[0])["_authorization_fence"]
        assert fence["role_assignment_id"] == reassigned.assignment_id
        assert fence["role_assignment_generation"] == reassigned.assignment_generation
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-009", "HSPACE-010")
def test_higher_native_lifecycle_is_exact_role_only(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _service(factory)
    _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_search_item(
                connection,
                label="team-lifecycle",
                item_id=ids["item"],
                revision_id=ids["revision"],
                space_id=ids["team_space_a"],
                actor="leader",
                title="Team note",
            )
    finally:
        connection.close()
    lifecycle = ItemLifecycleService(factory, ScopeResolver(_flags()))
    with pytest.raises(LifecycleAuthorizationError):
        lifecycle.deactivate(
            DeactivateItemCommand(
                principal=SessionPrincipal("member", _epoch(factory, "member")),
                space_id=ids["team_space_a"],
                item_id=ids["item"],
                expected_item_row_version=2,
                operation_id=_uuid("higher-lifecycle:member-deactivate"),
                request_id=_uuid("higher-lifecycle:member-deactivate-request"),
                reason="must be denied",
            )
        )
    deactivated = lifecycle.deactivate(
        DeactivateItemCommand(
            principal=SessionPrincipal("leader", _epoch(factory, "leader")),
            space_id=ids["team_space_a"],
            item_id=ids["item"],
            expected_item_row_version=2,
            operation_id=_uuid("higher-lifecycle:deactivate"),
            request_id=_uuid("higher-lifecycle:deactivate-request"),
            reason="reviewed higher lifecycle",
        )
    )
    assert deactivated.row_version == 3
    restored = lifecycle.restore(
        RestoreItemCommand(
            principal=SessionPrincipal("leader", _epoch(factory, "leader")),
            space_id=ids["team_space_a"],
            item_id=ids["item"],
            expected_item_row_version=3,
            operation_id=_uuid("higher-lifecycle:restore"),
            request_id=_uuid("higher-lifecycle:restore-request"),
        )
    )
    assert restored.row_version == 4


@pytest.mark.scenario("HSPACE-009", "HSPACE-010")
def test_higher_lease_force_release_distinguishes_role_and_admin_break_glass(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _service(factory)
    _assign_leader(factory, ids, higher)
    _seed_team_item(factory, ids)
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_search_item(
                connection,
                label="team-force-release",
                item_id=ids["item"],
                revision_id=ids["revision"],
                space_id=ids["team_space_a"],
                actor="leader",
                title="Team note",
            )
    finally:
        connection.close()
    editor = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="lease-v1",
            keys={"lease-v1": _LEASE_KEY},
        ),
        scope_resolver=ScopeResolver(_flags()),
    )
    acquired = editor.acquire(
        AcquireLeaseCommand(
            principal=SessionPrincipal("leader", _epoch(factory, "leader")),
            space_id=ids["team_space_a"],
            item_id=ids["item"],
            editor_session_id=_uuid("higher-force:session"),
            operation_id=_uuid("higher-force:acquire"),
            request_id=_uuid("higher-force:acquire-request"),
            raw_token=_token("higher-force:token"),
        )
    )
    assert acquired.fencing_generation >= 1
    with pytest.raises(LeaseAuthorizationError):
        editor.force_release(
            ForceReleaseLeaseCommand(
                principal=SessionPrincipal("member", _epoch(factory, "member")),
                space_id=ids["team_space_a"],
                item_id=ids["item"],
                operation_id=_uuid("higher-force:member"),
                request_id=_uuid("higher-force:member-request"),
                reason="Member cannot invoke break glass.",
                confirmation="FORCE_RELEASE",
            )
        )
    result = editor.force_release(
        ForceReleaseLeaseCommand(
            principal=SessionPrincipal("admin", _epoch(factory, "admin")),
            space_id=ids["team_space_a"],
            item_id=ids["item"],
            operation_id=_uuid("higher-force:admin"),
            request_id=_uuid("higher-force:admin-request"),
            reason="Administrator confirmed abandoned higher editor.",
            confirmation="FORCE_RELEASE",
        )
    )
    assert result.status == "force_released"
    connection = factory.open(read_only=True)
    try:
        audit = connection.execute(
            """SELECT json_extract(details_json,'$.authorization_mode')
                 FROM audit_event
                WHERE action='knowledge_lease.force_released'
                  AND actor_user_id='admin'
                ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        assert audit is not None and audit[0] == "break_glass"
    finally:
        connection.close()
