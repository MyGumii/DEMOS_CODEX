from __future__ import annotations

import base64
from dataclasses import replace
import hmac
import json
from uuid import UUID, uuid5
import zlib

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    OrganizationStateConflictError as RepositoryStateConflictError,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.services.organization import (
    AccountLifecycleCommand,
    AccountScopeStaleError,
    AccountService,
    CreateOrgUnitCommand,
    InactivateOrgUnitCommand,
    MoveOrgUnitCommand,
    OrgChangePreviewCommand,
    OrgInactivationResolution,
    OrgMemberResolution,
    OrgMoveResolution,
    OrganizationFailpointTriggered,
    OrganizationIdempotencyError,
    OrganizationPreviewError,
    OrganizationRevisionConflictError,
    OrganizationService,
    OrganizationStateConflictError,
    OrganizationValidationError,
    RenameOrgUnitCommand,
    SetLeaderCommand,
    SetMembershipCommand,
    _decode_preview_token,
    _encode_preview_token,
)


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("a493407f-f8ea-480e-996e-5ce2cc7697cf")
_PREVIEW_KEY = b"p1.5 organization preview test key" * 2


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.5-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed(factory: ConnectionFactory, *users: str) -> None:
    records = [
        LegacyUserRecord(
            id=user_id,
            display_name=user_id.title(),
            password_hash=f"scrypt:p1.5:{user_id}",
            system_role="admin" if user_id == "admin" else "user",
            account_status="active",
            created_at_ms=100,
            updated_at_ms=100,
        )
        for user_id in users
    ]
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(connection, records)
    finally:
        connection.close()


def _service(factory: ConnectionFactory, *, failpoint=None) -> OrganizationService:
    return OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        failpoint=failpoint,
    )


def _create_command(
    *, operation: str, parent_id: str, kind: str, name: str
) -> CreateOrgUnitCommand:
    return CreateOrgUnitCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=_uuid(f"operation:{operation}"),
        request_id=_uuid(f"request:{operation}"),
        parent_id=parent_id,
        kind=kind,  # type: ignore[arg-type]
        name=name,
        request_meta={"remote_addr": "127.0.0.1"},
    )


def _hierarchy(service: OrganizationService) -> tuple[str, str]:
    team = service.create_unit(
        _create_command(
            operation="create-team",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Platform Team",
        )
    )
    assert team.resource_id is not None
    part = service.create_unit(
        _create_command(
            operation="create-part",
            parent_id=team.resource_id,
            kind="part",
            name="Control Part",
        )
    )
    assert part.resource_id is not None
    return team.resource_id, part.resource_id


@pytest.mark.scenario("HSPACE-014")
def test_successor_org_creation_atomically_provisions_exact_native_spaces(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        part_reads=True,
        higher_spaces=True,
    )
    service = OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        flags=flags,
    )
    team_id, part_id = _hierarchy(service)
    connection = factory.open(read_only=True)
    try:
        team = SpaceRepository().get_org(connection, team_id)
        part = SpaceRepository().get_org(connection, part_id)
    finally:
        connection.close()
    assert team is not None
    assert team.id == team_id
    assert team.space_kind == "team"
    assert team.write_enabled is False
    assert part is not None
    assert part.id == part_id
    assert part.space_kind == "part"
    assert part.write_enabled is False

    def inject(name: str) -> None:
        if name == "organization.unit.after_create":
            raise OrganizationFailpointTriggered(name)

    failed = OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        flags=flags,
        failpoint=inject,
    )
    command = _create_command(
        operation="higher-native-rollback",
        parent_id=RESPONSIBILITY_ROOT_ID,
        kind="team",
        name="Rollback Native Team",
    )
    with pytest.raises(OrganizationFailpointTriggered):
        failed.create_unit(command)
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM org_unit WHERE name='Rollback Native Team'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE id IN (SELECT id FROM org_unit WHERE name='Rollback Native Team')"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-002")
def test_org_001_002_create_tree_rename_and_idempotency(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")
    service = _service(factory)
    team_id, part_id = _hierarchy(service)

    tree = service.get_tree("admin", 1, _uuid("request:tree")).as_dict()
    assert tree["root"]["id"] == RESPONSIBILITY_ROOT_ID
    team = tree["root"]["children"][0]
    assert team["id"] == team_id
    assert team["children"][0]["id"] == part_id
    assert team["children"][0]["space_status"] == "not_created"
    assert tree["users"] == [
        {
            "account_status": "active",
            "current_affiliation_id": None,
            "current_membership_id": None,
            "current_part_id": None,
            "display_name": "Admin",
            "id": "admin",
            "home_org_unit_id": None,
            "home_org_unit_kind": None,
            "row_version": 1,
            "scope_epoch": 1,
            "system_role": "admin",
        }
    ]

    rename = RenameOrgUnitCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=_uuid("operation:rename"),
        request_id=_uuid("request:rename"),
        unit_id=part_id,
        expected_row_version=1,
        name="Automation Part",
    )
    first = service.rename_unit(rename)
    replay_request_id = _uuid("request:rename-replay")
    second = service.rename_unit(replace(rename, request_id=replay_request_id))
    assert second == replace(first, request_id=replay_request_id)
    assert second.request_id == replay_request_id
    assert first.status == "renamed" and first.row_version == 2

    with pytest.raises(OrganizationIdempotencyError):
        service.rename_unit(
            RenameOrgUnitCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=rename.operation_id,
                request_id=_uuid("request:rename-conflict"),
                unit_id=part_id,
                expected_row_version=1,
                name="Different Input",
            )
        )

    connection = factory.open()
    try:
        assert OrganizationRepository().require_unit(connection, part_id).id == part_id
        assert len(
            [
                event
                for event in AuditRepository().list_recent(
                    connection, actor_user_id="admin"
                )
                if event.operation_id == rename.operation_id
            ]
        ) == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE aggregate_id=?", (part_id,)
        ).fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.scenario("ORG-003")
@pytest.mark.scenario("ORG-004")
@pytest.mark.scenario("ORG-005")
@pytest.mark.scenario("ORG-006")
def test_org_003_006_membership_leader_move_and_part_aggregate_cas(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "leader", "replacement")
    service = _service(factory)
    _team_id, part_id = _hierarchy(service)

    leader_membership = service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:assign-leader"),
            request_id=_uuid("request:assign-leader"),
            target_user_id="leader",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Initial assignment",
        )
    )
    service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:assign-replacement"),
            request_id=_uuid("request:assign-replacement"),
            target_user_id="replacement",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Initial assignment",
        )
    )
    assigned_leader = service.set_leader(
        SetLeaderCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:set-leader"),
            request_id=_uuid("request:set-leader"),
            part_id=part_id,
            leader_user_id="leader",
            expected_part_row_version=1,
            expected_current_leader_assignment_id=None,
            reason="Initial leader",
        )
    )
    assert assigned_leader.row_version == 2
    assert assigned_leader.resource_id is not None

    moved = service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:unassign-leader"),
            request_id=_uuid("request:unassign-leader"),
            target_user_id="leader",
            expected_user_row_version=2,
            part_id=None,
            expected_current_leader_assignment_id=assigned_leader.resource_id,
            replacement_leader_user_id="replacement",
            reason="Rotate the leader and intentionally unassign",
            expected_part_row_version=2,
        )
    )
    assert moved.status == "unassigned" and moved.row_version == 3

    connection = factory.open()
    try:
        repository = OrganizationRepository()
        leader = repository.get_user_organization_state(connection, "leader")
        assert leader is not None
        assert leader.current_part_id is None and leader.scope_epoch == 3
        replacement = repository.get_active_part_leader(connection, part_id)
        assert replacement is not None and replacement.user_id == "replacement"
        assert repository.require_unit(connection, part_id).row_version == 3
        assert repository.get_current_membership(connection, "leader") is None
        assert leader_membership.resource_id is not None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    (
        "membership.after_old_close",
        "membership.after_new_insert",
        "membership.after_scope_epoch",
        "membership.after_lease_revoke",
    ),
)
def test_org_005_membership_failpoints_roll_back_domain_audit_outbox_and_dedup(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    setup = _service(factory)
    _team_id, part_id = _hierarchy(setup)

    def inject(name: str) -> None:
        if name == failpoint:
            raise OrganizationFailpointTriggered(name)

    service = _service(factory, failpoint=inject)
    command = SetMembershipCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=_uuid(f"operation:{failpoint}"),
        request_id=_uuid(f"request:{failpoint}"),
        target_user_id="member",
        expected_user_row_version=1,
        part_id=part_id,
        expected_current_leader_assignment_id=None,
        replacement_leader_user_id=None,
        reason="Failpoint assignment",
    )
    with pytest.raises(OrganizationFailpointTriggered):
        service.set_membership(command)

    connection = factory.open()
    try:
        repository = OrganizationRepository()
        user = repository.get_user_organization_state(connection, "member")
        assert user is not None and user.current_part_id is None
        assert user.scope_epoch == 1 and user.row_version == 1
        assert repository.get_current_membership(connection, "member") is None
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_id=?",
            (command.operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (command.operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE payload_json LIKE ?",
            (f'%{command.operation_id}%',),
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    (
        "organization.unit.after_create",
        "organization.after_domain",
        "organization.after_audit",
        "organization.after_outbox",
        "organization.before_commit",
    ),
)
def test_organization_envelope_failpoints_roll_back_domain_dedup_audit_and_outbox(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")

    def inject(name: str) -> None:
        if name == failpoint:
            raise OrganizationFailpointTriggered(name)

    command = _create_command(
        operation=f"envelope:{failpoint}",
        parent_id=RESPONSIBILITY_ROOT_ID,
        kind="team",
        name="Rollback Envelope Team",
    )
    with pytest.raises(OrganizationFailpointTriggered):
        _service(factory, failpoint=inject).create_unit(command)
    connection = factory.open()
    try:
        assert OrganizationRepository().list_units(
            connection,
            parent_id=RESPONSIBILITY_ROOT_ID,
            direct_children_only=True,
        ) == ()
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_id=?",
            (command.operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (command.operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE payload_json LIKE ?",
            (f'%{command.operation_id}%',),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_account_admin_actor_scope_epoch_is_rechecked(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    command = AccountLifecycleCommand(
        actor_user_id="admin",
        actor_scope_epoch=999,
        target_user_id="member",
        expected_row_version=1,
        operation_id=_uuid("operation:stale-admin"),
        request_id=_uuid("request:stale-admin"),
        reason="Stale administrator session",
    )
    with pytest.raises(AccountScopeStaleError):
        AccountService(factory).disable_account(command)
    connection = factory.open()
    try:
        assert UserRepository().require(connection, "member").account_status == "active"
    finally:
        connection.close()


def _preview(
    service: OrganizationService,
    *,
    label: str,
    unit_id: str,
    row_version: int,
    change: str,
    new_parent_id: str | None = None,
):
    return service.preview_change(
        OrgChangePreviewCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            request_id=_uuid(f"request:preview:{label}"),
            unit_id=unit_id,
            expected_row_version=row_version,
            change=change,  # type: ignore[arg-type]
            new_parent_id=new_parent_id,
        )
    )


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-002")
def test_move_requires_impact_bound_preview_and_retains_stable_identity(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    service = _service(factory)
    _team_a, part_id = _hierarchy(service)
    team_b = service.create_unit(
        _create_command(
            operation="create-team-b",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Operations Team",
        )
    )
    assert team_b.resource_id is not None

    preview = _preview(
        service,
        label="move",
        unit_id=part_id,
        row_version=1,
        change="move",
        new_parent_id=team_b.resource_id,
    )
    assert preview.allowed and not preview.blockers
    assert preview.member_impact["member_ids"] == []
    assert preview.member_impact["members"] == []
    assert preview.leader_impact["leader_assignment_id"] is None
    assert preview.space_impact["space_status"] == "not_created"
    assert preview.space_impact["row_version"] is None
    assert len(preview.preview_token) <= 4096

    moved = service.move_unit(
        MoveOrgUnitCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:move-part"),
            request_id=_uuid("request:move-part"),
            unit_id=part_id,
            expected_row_version=1,
            preview_token=preview.preview_token,
            new_parent_id=team_b.resource_id,
            resolution=OrgMoveResolution(),
        )
    )
    assert moved.resource_id == part_id
    assert moved.status == "moved" and moved.row_version == 2
    connection = factory.open()
    try:
        unit = OrganizationRepository().require_unit(connection, part_id)
        assert unit.id == part_id and unit.parent_id == team_b.resource_id
    finally:
        connection.close()

    stale_preview = _preview(
        service,
        label="move-stale-impact",
        unit_id=part_id,
        row_version=2,
        change="move",
        new_parent_id=_team_a,
    )
    service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:late-member"),
            request_id=_uuid("request:late-member"),
            target_user_id="member",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Change impact after preview",
        )
    )
    with pytest.raises(OrganizationPreviewError):
        service.move_unit(
            MoveOrgUnitCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=_uuid("operation:move-stale-impact"),
                request_id=_uuid("request:move-stale-impact"),
                unit_id=part_id,
                expected_row_version=2,
                preview_token=stale_preview.preview_token,
                new_parent_id=_team_a,
                resolution=OrgMoveResolution(),
            )
        )


def test_move_populated_part_retains_membership_leader_space_and_scope_epoch(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "leader")
    service = _service(factory)
    _source_team, part_id = _hierarchy(service)
    destination = service.create_unit(
        _create_command(
            operation="populated-move-destination",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Populated Destination",
        )
    )
    assert destination.resource_id is not None
    membership = service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:populated-move-membership"),
            request_id=_uuid("request:populated-move-membership"),
            target_user_id="leader",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Prepare populated move",
        )
    )
    leader = service.set_leader(
        SetLeaderCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:populated-move-leader"),
            request_id=_uuid("request:populated-move-leader"),
            part_id=part_id,
            leader_user_id="leader",
            expected_part_row_version=1,
            expected_current_leader_assignment_id=None,
            reason="Prepare populated move",
        )
    )
    space_id = _uuid("populated-move-space")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',1,0,2000,2000,1)""",
                (space_id, part_id),
            )
        repository = OrganizationRepository()
        user_before = repository.get_user_organization_state(connection, "leader")
        space_before = repository.get_part_space_state(connection, part_id)
        assert user_before is not None and space_before is not None
    finally:
        connection.close()

    preview = _preview(
        service,
        label="populated-move",
        unit_id=part_id,
        row_version=2,
        change="move",
        new_parent_id=destination.resource_id,
    )
    assert preview.allowed
    assert preview.member_impact["current_member_count"] == 1
    assert preview.member_impact["members"] == [
        {"expected_user_row_version": 2, "user_id": "leader"}
    ]
    assert preview.leader_impact["leader_assignment_id"] == leader.resource_id
    assert preview.leader_impact["leader_user_id"] == "leader"
    assert preview.space_impact == {
        "knowledge_item_count": 0,
        "row_version": 1,
        "space_id": space_id,
        "space_status": "active",
        "write_enabled": True,
    }
    moved = service.move_unit(
        MoveOrgUnitCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:populated-move"),
            request_id=_uuid("request:populated-move"),
            unit_id=part_id,
            expected_row_version=2,
            preview_token=preview.preview_token,
            new_parent_id=destination.resource_id,
            resolution=OrgMoveResolution(),
            reason="Retain every populated aggregate",
        )
    )
    assert moved.resource_id == part_id and moved.row_version == 3
    connection = factory.open()
    try:
        repository = OrganizationRepository()
        unit = repository.require_unit(connection, part_id)
        user_after = repository.get_user_organization_state(connection, "leader")
        membership_after = repository.get_current_membership(connection, "leader")
        leader_after = repository.get_active_part_leader(connection, part_id)
        space_after = repository.get_part_space_state(connection, part_id)
        assert unit.parent_id == destination.resource_id
        assert user_after == user_before
        assert membership_after is not None
        assert membership_after.id == membership.resource_id
        assert leader_after is not None and leader_after.id == leader.resource_id
        assert space_after == space_before
    finally:
        connection.close()


@pytest.mark.scenario("ORG-003")
@pytest.mark.scenario("ORG-004")
@pytest.mark.scenario("ORG-005")
@pytest.mark.scenario("ORG-006")
def test_inactivate_part_exactly_resolves_members_leader_and_optional_space(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "leader", "member")
    service = _service(factory)
    _team_id, source_part = _hierarchy(service)
    destination_team = service.create_unit(
        _create_command(
            operation="inactivate-destination-team",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Destination Team",
        )
    )
    assert destination_team.resource_id is not None
    destination_part = service.create_unit(
        _create_command(
            operation="inactivate-destination-part",
            parent_id=destination_team.resource_id,
            kind="part",
            name="Destination Part",
        )
    )
    assert destination_part.resource_id is not None
    for user_id in ("leader", "member"):
        service.set_membership(
            SetMembershipCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=_uuid(f"operation:inactivate-assign:{user_id}"),
                request_id=_uuid(f"request:inactivate-assign:{user_id}"),
                target_user_id=user_id,
                expected_user_row_version=1,
                part_id=source_part,
                expected_current_leader_assignment_id=None,
                replacement_leader_user_id=None,
                reason="Prepare exact inactivation",
            )
        )
    leader = service.set_leader(
        SetLeaderCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:inactivate-leader"),
            request_id=_uuid("request:inactivate-leader"),
            part_id=source_part,
            leader_user_id="leader",
            expected_part_row_version=1,
            expected_current_leader_assignment_id=None,
            reason="Prepare exact inactivation",
        )
    )
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',0,0,2000,2000,1)""",
                (_uuid("inactivation-space"), source_part),
            )
    finally:
        connection.close()

    preview = _preview(
        service,
        label="inactivate",
        unit_id=source_part,
        row_version=2,
        change="inactivate",
    )
    assert preview.allowed
    assert preview.member_impact["current_member_count"] == 2
    assert preview.member_impact["members"] == [
        {"expected_user_row_version": 2, "user_id": "leader"},
        {"expected_user_row_version": 2, "user_id": "member"},
    ]
    assert preview.leader_impact == {
        "leader_assignment_id": leader.resource_id,
        "leader_user_id": "leader",
        "will_close": True,
    }
    assert preview.space_impact["space_status"] == "write_disabled"
    assert preview.space_impact["row_version"] == 1
    resolution = OrgInactivationResolution(
        member_resolutions=(
            OrgMemberResolution("leader", 2, destination_part.resource_id),
            OrgMemberResolution("member", 2, None),
        ),
        expected_current_leader_assignment_id=leader.resource_id,
        expected_space_row_version=1,
    )
    result = service.inactivate_unit(
        InactivateOrgUnitCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:inactivate-part"),
            request_id=_uuid("request:inactivate-part"),
            unit_id=source_part,
            expected_row_version=2,
            preview_token=preview.preview_token,
            resolution=resolution,
            reason="Retire this part with an exact resolution",
        )
    )
    assert result.status == "inactivated" and result.row_version == 4
    connection = factory.open()
    try:
        repository = OrganizationRepository()
        assert repository.require_unit(connection, source_part).status == "inactive"
        assert repository.get_active_part_leader(connection, source_part) is None
        assert repository.list_current_memberships(
            connection, part_id=source_part
        ) == ()
        leader_state = repository.get_user_organization_state(connection, "leader")
        member_state = repository.get_user_organization_state(connection, "member")
        assert leader_state is not None
        assert leader_state.current_part_id == destination_part.resource_id
        assert member_state is not None and member_state.current_part_id is None
        space = repository.get_part_space_state(connection, source_part)
        assert space is not None and space.status == "frozen"
        assert not space.write_enabled and space.row_version == 2
    finally:
        connection.close()


def test_inactivation_rejects_inexact_resolution_as_failed_final_and_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    service = _service(factory)
    _team_id, part_id = _hierarchy(service)
    service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:exact-member"),
            request_id=_uuid("request:exact-member"),
            target_user_id="member",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Prepare failed resolution",
        )
    )
    preview = _preview(
        service,
        label="inexact",
        unit_id=part_id,
        row_version=1,
        change="inactivate",
    )
    operation_id = _uuid("operation:inexact-resolution")
    command = InactivateOrgUnitCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=operation_id,
        request_id=_uuid("request:inexact-resolution"),
        unit_id=part_id,
        expected_row_version=1,
        preview_token=preview.preview_token,
        resolution=OrgInactivationResolution(
            member_resolutions=(),
            expected_current_leader_assignment_id=None,
            expected_space_row_version=None,
        ),
        reason="Missing member resolution",
    )
    with pytest.raises(OrganizationStateConflictError) as first:
        service.inactivate_unit(command)
    with pytest.raises(type(first.value)) as replay:
        service.inactivate_unit(
            replace(command, request_id=_uuid("request:inexact-replay"))
        )
    assert replay.value.as_error() == first.value.as_error()
    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT status FROM operation_dedup WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == "failed_final"
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_inactivate_future_team_space_freezes_exact_optional_owner_space(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")
    service = _service(factory)
    team = service.create_unit(
        _create_command(
            operation="future-team-space",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Future Team Space",
        )
    )
    assert team.resource_id is not None
    space_id = _uuid("future-team-space-row")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,2000,2000,1)""",
                (space_id, team.resource_id),
            )
    finally:
        connection.close()

    preview = _preview(
        service,
        label="future-team-space-inactivate",
        unit_id=team.resource_id,
        row_version=1,
        change="inactivate",
    )
    assert preview.allowed
    assert preview.space_impact == {
        "knowledge_item_count": 0,
        "row_version": 1,
        "space_id": space_id,
        "space_status": "write_disabled",
        "write_enabled": False,
    }
    result = service.inactivate_unit(
        InactivateOrgUnitCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:future-team-space-inactivate"),
            request_id=_uuid("request:future-team-space-inactivate"),
            unit_id=team.resource_id,
            expected_row_version=1,
            preview_token=preview.preview_token,
            resolution=OrgInactivationResolution(
                member_resolutions=(),
                expected_current_leader_assignment_id=None,
                expected_space_row_version=1,
            ),
            reason="Freeze the disabled future team space before inactivation",
        )
    )
    assert result.status == "inactivated" and result.row_version == 2
    connection = factory.open()
    try:
        repository = OrganizationRepository()
        unit = repository.require_unit(connection, team.resource_id)
        space = repository.get_org_unit_space_state(
            connection,
            org_unit_id=team.resource_id,
            expected_kind="team",
        )
        assert unit.status == "inactive"
        assert space is not None
        assert space.status == "frozen" and not space.write_enabled
        assert space.row_version == 2
    finally:
        connection.close()


def test_service_boundary_rejects_boolean_compare_and_swap_versions(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    service = _service(factory)
    _team_id, part_id = _hierarchy(service)

    with pytest.raises(OrganizationValidationError):
        service.set_membership(
            SetMembershipCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=_uuid("operation:boolean-user-version"),
                request_id=_uuid("request:boolean-user-version"),
                target_user_id="member",
                expected_user_row_version=True,
                part_id=part_id,
                expected_current_leader_assignment_id=None,
                replacement_leader_user_id=None,
                reason="Boolean versions are not integers",
            )
        )
    with pytest.raises(OrganizationValidationError):
        service.set_leader(
            SetLeaderCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=_uuid("operation:boolean-part-version"),
                request_id=_uuid("request:boolean-part-version"),
                part_id=part_id,
                leader_user_id="member",
                expected_part_row_version=True,
                expected_current_leader_assignment_id=None,
                reason="Boolean versions are not integers",
            )
        )
    with pytest.raises(OrganizationValidationError):
        service.inactivate_unit(
            InactivateOrgUnitCommand(
                actor_user_id="admin",
                actor_scope_epoch=1,
                operation_id=_uuid("operation:boolean-space-version"),
                request_id=_uuid("request:boolean-space-version"),
                unit_id=part_id,
                expected_row_version=1,
                preview_token="not-used",
                resolution=OrgInactivationResolution(
                    member_resolutions=(
                        OrgMemberResolution("member", True, None),
                    ),
                    expected_current_leader_assignment_id=None,
                    expected_space_row_version=True,
                ),
                reason="Boolean versions are not integers",
            )
        )
class _CreateThenFailRepository(OrganizationRepository):
    def create_unit(self, *args, **kwargs):
        super().create_unit(*args, **kwargs)
        raise RepositoryStateConflictError("Injected failure after unit insert.")


def test_post_write_stable_error_is_transient_and_rolls_back_every_envelope(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin")
    command = _create_command(
        operation="post-write-stable",
        parent_id=RESPONSIBILITY_ROOT_ID,
        kind="team",
        name="Rollback Team",
    )
    failing = OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        organization=_CreateThenFailRepository(),
    )
    with pytest.raises(OrganizationFailpointTriggered) as failure:
        failing.create_unit(command)
    assert failure.value.retryable is True
    assert failure.value.details["state"] == "operation_uncommitted"
    connection = factory.open()
    try:
        assert OrganizationRepository().list_units(
            connection,
            parent_id=RESPONSIBILITY_ROOT_ID,
            direct_children_only=True,
        ) == ()
        for table in ("operation_dedup", "audit_event"):
            assert connection.execute(
                f"SELECT count(*) FROM {table} WHERE operation_id=?",
                (command.operation_id,),
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE payload_json LIKE ?",
            (f'%{command.operation_id}%',),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    retried = _service(factory).create_unit(
        replace(command, request_id=_uuid("request:post-write-retry"))
    )
    assert retried.status == "created"


def _signed_raw_token(compressed: bytes) -> str:
    body = base64.urlsafe_b64encode(compressed).rstrip(b"=")
    signature = hmac.digest(_PREVIEW_KEY, b"org-preview-v1\0" + body, "sha256")
    return "v1." + body.decode("ascii") + "." + base64.urlsafe_b64encode(
        signature
    ).rstrip(b"=").decode("ascii")


@pytest.mark.parametrize(
    "token_factory",
    (
        lambda valid: "v1." + "가" * 40 + "." + valid.rsplit(".", 1)[1],
        lambda valid: valid + "!",
        lambda valid: valid.split(".")[0] + ".!" + "." + valid.split(".")[2],
        lambda valid: valid.split(".")[0]
        + "."
        + valid.split(".")[1]
        + ".="
        + valid.split(".")[2],
    ),
)
def test_preview_token_strictly_rejects_noncanonical_base64(
    token_factory,
) -> None:
    valid = _encode_preview_token(
        _PREVIEW_KEY,
        {"schema_version": 1, "issued_at_ms": 1, "expires_at_ms": 2},
    )
    with pytest.raises(OrganizationPreviewError):
        _decode_preview_token(_PREVIEW_KEY, token_factory(valid))


def test_preview_token_bounded_decompression_and_trailing_data_guard() -> None:
    normal = zlib.compress(json.dumps({"schema_version": 1}).encode("ascii"))
    with pytest.raises(OrganizationPreviewError):
        _decode_preview_token(_PREVIEW_KEY, _signed_raw_token(normal + b"trailing"))
    bomb = zlib.compress(
        json.dumps({"padding": "a" * 70_000}).encode("ascii"), level=9
    )
    with pytest.raises(OrganizationPreviewError):
        _decode_preview_token(_PREVIEW_KEY, _signed_raw_token(bomb))


def test_preview_expiry_uses_db_time_and_token_is_actor_bound(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "other-admin")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE app_user SET system_role='admin',updated_at=updated_at+1,
                     row_version=row_version+1 WHERE id='other-admin'"""
            )
    finally:
        connection.close()
    service = _service(factory)
    team_a, part_id = _hierarchy(service)
    team_b = service.create_unit(
        _create_command(
            operation="expiry-team",
            parent_id=RESPONSIBILITY_ROOT_ID,
            kind="team",
            name="Expiry Team",
        )
    )
    assert team_b.resource_id is not None
    preview = _preview(
        service,
        label="expiry",
        unit_id=part_id,
        row_version=1,
        change="move",
        new_parent_id=team_b.resource_id,
    )
    payload = _decode_preview_token(_PREVIEW_KEY, preview.preview_token)
    payload["issued_at_ms"] = 0
    payload["expires_at_ms"] = 300_000
    expired = _encode_preview_token(_PREVIEW_KEY, payload)
    base = MoveOrgUnitCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=_uuid("operation:expired-preview"),
        request_id=_uuid("request:expired-preview"),
        unit_id=part_id,
        expected_row_version=1,
        preview_token=expired,
        new_parent_id=team_b.resource_id,
        resolution=OrgMoveResolution(),
    )
    with pytest.raises(OrganizationPreviewError):
        service.move_unit(base)
    with pytest.raises(OrganizationPreviewError):
        service.move_unit(
            replace(
                base,
                actor_user_id="other-admin",
                operation_id=_uuid("operation:wrong-preview-actor"),
                request_id=_uuid("request:wrong-preview-actor"),
                preview_token=preview.preview_token,
            )
        )
    connection = factory.open()
    try:
        assert OrganizationRepository().get_unit(connection, team_a) is not None
    finally:
        connection.close()


class _ConcurrentTreeMutationRepository(OrganizationRepository):
    __slots__ = ("_factory", "_part_id", "_mutated")

    def __init__(self, factory: ConnectionFactory, part_id: str) -> None:
        self._factory = factory
        self._part_id = part_id
        self._mutated = False

    def list_subtree(self, connection, **kwargs):
        result = super().list_subtree(connection, **kwargs)
        if not self._mutated:
            self._mutated = True
            writer = self._factory.open()
            try:
                with transaction(writer):
                    state = super().get_user_organization_state(writer, "member")
                    assert state is not None
                    super().open_membership(
                        writer,
                        membership_id=_uuid("concurrent-tree-membership"),
                        user_id="member",
                        part_id=self._part_id,
                        assigned_by="admin",
                        reason="Concurrent snapshot probe",
                        valid_from_ms=9_000,
                    )
                    super().compare_and_swap_user_part(
                        writer,
                        user_id="member",
                        expected_current_part_id=None,
                        target_part_id=self._part_id,
                        expected_scope_epoch=state.scope_epoch,
                        expected_row_version=state.row_version,
                        updated_at_ms=9_000,
                    )
            finally:
                writer.close()
        return result


def test_tree_reads_one_sqlite_snapshot_across_concurrent_writer(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    setup = _service(factory)
    _team_id, part_id = _hierarchy(setup)
    service = OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        organization=_ConcurrentTreeMutationRepository(factory, part_id),
    )
    tree = service.get_tree("admin", 1, _uuid("request:tree-snapshot")).as_dict()
    assert tree["root"]["member_count"] == 0
    member = next(user for user in tree["users"] if user["id"] == "member")
    assert member["current_part_id"] is None
    assert member["current_membership_id"] is None
    assert member["scope_epoch"] == 1
    assert member["row_version"] == 1
    connection = factory.open()
    try:
        assert len(
            OrganizationRepository().list_current_memberships(
                connection, part_id=part_id
            )
        ) == 1
    finally:
        connection.close()


def test_preview_token_stays_within_4096_bytes_for_200_members(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    member_ids = tuple(
        "u" + _uuid(f"member:{index}").replace("-", "")
        for index in range(200)
    )
    _seed(factory, "admin", *member_ids)
    service = _service(factory)
    _team_id, part_id = _hierarchy(service)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = OrganizationRepository()
            for index, user_id in enumerate(member_ids):
                state = repository.get_user_organization_state(connection, user_id)
                assert state is not None
                repository.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{index}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="admin",
                    reason="Worst-case preview population",
                    valid_from_ms=10_000 + index,
                )
                repository.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=part_id,
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=10_000 + index,
                )
    finally:
        connection.close()
    preview = _preview(
        service,
        label="200-members",
        unit_id=part_id,
        row_version=1,
        change="inactivate",
    )
    assert preview.allowed
    assert preview.member_impact["current_member_count"] == 200
    assert len(preview.member_impact["member_ids"]) == 200
    assert len(preview.member_impact["members"]) == 200
    assert all(
        member["expected_user_row_version"] == 2
        for member in preview.member_impact["members"]
    )
    assert len(preview.preview_token) <= 4096


def test_tree_users_are_id_sorted_redacted_and_include_legal_disabled_unassigned(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "member", "admin")
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(
                connection,
                (
                    LegacyUserRecord(
                        id="disabled",
                        display_name="Disabled User",
                        password_hash="scrypt:p1.8:must-not-leak",
                        system_role="user",
                        account_status="disabled",
                        created_at_ms=100,
                        updated_at_ms=100,
                        disabled_at_ms=100,
                    ),
                ),
            )
    finally:
        connection.close()
    service = _service(factory)
    _team_id, part_id = _hierarchy(service)
    membership = service.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:tree-user-membership"),
            request_id=_uuid("request:tree-user-membership"),
            target_user_id="member",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Populate canonical admin read model",
        )
    )
    assert membership.resource_id is not None

    payload = service.get_tree(
        "admin", 1, _uuid("request:tree-canonical-users")
    ).as_dict()
    assert [user["id"] for user in payload["users"]] == [
        "admin",
        "disabled",
        "member",
    ]
    disabled = payload["users"][1]
    assert disabled == {
        "account_status": "disabled",
        "current_affiliation_id": None,
        "current_membership_id": None,
        "current_part_id": None,
        "display_name": "Disabled User",
        "id": "disabled",
        "home_org_unit_id": None,
        "home_org_unit_kind": None,
        "row_version": 1,
        "scope_epoch": 1,
        "system_role": "user",
    }
    member = payload["users"][2]
    assert member["current_part_id"] == part_id
    assert member["current_membership_id"] == membership.resource_id
    assert member["current_affiliation_id"] == membership.resource_id
    assert member["home_org_unit_id"] == part_id
    assert member["home_org_unit_kind"] == "part"
    assert member["scope_epoch"] == 2
    assert member["row_version"] == 2
    forbidden_keys = {
        "password_hash",
        "must_change_password",
        "password_changed_at",
        "created_at",
        "updated_at",
        "disabled_at",
    }
    assert all(forbidden_keys.isdisjoint(user) for user in payload["users"])


class _MissingMembershipMirrorRepository(OrganizationRepository):
    __slots__ = ("_missing_user_id",)

    def __init__(self, missing_user_id: str) -> None:
        self._missing_user_id = missing_user_id

    def list_current_memberships(self, connection, **kwargs):
        return tuple(
            membership
            for membership in super().list_current_memberships(connection, **kwargs)
            if membership.user_id != self._missing_user_id
        )


def test_tree_fails_closed_on_current_part_membership_mirror_mismatch(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed(factory, "admin", "member")
    setup = _service(factory)
    _team_id, part_id = _hierarchy(setup)
    setup.set_membership(
        SetMembershipCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:tree-mirror-membership"),
            request_id=_uuid("request:tree-mirror-membership"),
            target_user_id="member",
            expected_user_row_version=1,
            part_id=part_id,
            expected_current_leader_assignment_id=None,
            replacement_leader_user_id=None,
            reason="Create mirror mismatch fixture",
        )
    )
    service = OrganizationService(
        factory,
        preview_hmac_key=_PREVIEW_KEY,
        organization=_MissingMembershipMirrorRepository("member"),
    )

    with pytest.raises(OrganizationStateConflictError) as raised:
        service.get_tree("admin", 1, _uuid("request:tree-mirror-mismatch"))
    assert raised.value.code == "STATE_CONFLICT"
    assert raised.value.details == {"state": "current_membership_mismatch"}
