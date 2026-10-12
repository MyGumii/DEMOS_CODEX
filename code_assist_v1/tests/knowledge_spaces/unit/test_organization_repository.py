from __future__ import annotations

from hashlib import sha256
import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrgUnitVersionConflictError,
    OrganizationRepository,
    OrganizationStateConflictError,
    PartSpaceNotFoundError,
    PartSpaceVersionConflictError,
    RESPONSIBILITY_ROOT_ID,
    RESPONSIBILITY_ROOT_NAME,
    RESPONSIBILITY_ROOT_NAME_KEY,
    UserOrganizationVersionConflictError,
    normalize_org_name,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit
_FIXTURE_NAMESPACE = UUID("ff1fd8b3-3fcc-4317-b010-f59ca96fe3f1")


def _uuid(label: str) -> str:
    return str(uuid5(_FIXTURE_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.5-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _legacy_user(user_id: str, *, role: str = "user") -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.5:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed_users(factory: ConnectionFactory, *user_ids: str) -> None:
    users = UserRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            records = tuple(
                _legacy_user(user_id, role="admin" if user_id == "admin" else "user")
                for user_id in user_ids
            )
            users.import_legacy_many(connection, records)
    finally:
        connection.close()


def _create_hierarchy(
    connection,
    repository: OrganizationRepository,
    *,
    suffix: str = "a",
    now: int = 1000,
) -> tuple[str, str]:
    team_id = _uuid(f"team:{suffix}")
    part_id = _uuid(f"part:{suffix}")
    repository.create_unit(
        connection,
        unit_id=team_id,
        kind="team",
        parent_id=RESPONSIBILITY_ROOT_ID,
        name=f"Team {suffix.upper()}",
        actor_user_id="admin",
        now_ms=now,
    )
    repository.create_unit(
        connection,
        unit_id=part_id,
        kind="part",
        parent_id=team_id,
        name=f"Part {suffix.upper()}",
        actor_user_id="admin",
        now_ms=now + 1,
    )
    return team_id, part_id


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-002")
def test_org_001_002_root_hierarchy_tree_normalization_and_physical_guards(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, "admin")
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        root = repository.require_unit(connection, RESPONSIBILITY_ROOT_ID)
        assert root.name == RESPONSIBILITY_ROOT_NAME
        assert root.name_key == RESPONSIBILITY_ROOT_NAME_KEY
        assert root.kind == "responsibility"
        assert root.parent_id is None

        with pytest.raises(KnowledgeCoreTransactionError):
            repository.create_unit(
                connection,
                unit_id=_uuid("outside-transaction"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Outside",
                actor_user_id="admin",
                now_ms=500,
            )

        with transaction(connection):
            team_id, part_id = _create_hierarchy(connection, repository)

        assert repository.get_part_space_state(connection, part_id) is None
        impact = repository.get_unit_impact(connection, part_id)
        assert impact.owned_space_state == "not_created"
        assert impact.owned_space_count == 0
        assert impact.knowledge_item_count == 0

        tree = repository.list_subtree(connection)
        rows = {row.unit.id: row for row in tree}
        assert rows[RESPONSIBILITY_ROOT_ID].depth == 0
        assert rows[team_id].path_ids == (RESPONSIBILITY_ROOT_ID, team_id)
        assert rows[part_id].path_ids == (
            RESPONSIBILITY_ROOT_ID,
            team_id,
            part_id,
        )
        assert rows[part_id].path_names[-1] == "Part A"
        assert repository.list_units(
            connection,
            parent_id=team_id,
            direct_children_only=True,
        ) == (repository.require_unit(connection, part_id),)
        with pytest.raises(ValueError, match="direct_children_only"):
            repository.list_units(connection, parent_id=team_id)

        assert normalize_org_name("  ＴＥＡＭ　Ａ  ") == (
            "ＴＥＡＭ　Ａ",
            "team a",
        )
        with pytest.raises(ValueError, match="120"):
            normalize_org_name("x" * 121)
        with pytest.raises(ValueError, match="actor_user_id"):
            with transaction(connection):
                repository.create_unit(
                    connection,
                    unit_id=_uuid("missing-actor"),
                    kind="team",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name="No actor",
                    actor_user_id="",
                    now_ms=600,
                )

        with pytest.raises(OrganizationStateConflictError):
            with transaction(connection):
                repository.create_unit(
                    connection,
                    unit_id=_uuid("duplicate-team"),
                    kind="team",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name="ＴＥＡＭ　Ａ",
                    actor_user_id="admin",
                    now_ms=2000,
                )
        with pytest.raises(OrganizationStateConflictError):
            with transaction(connection):
                repository.create_unit(
                    connection,
                    unit_id=_uuid("invalid-part-parent"),
                    kind="part",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name="Invalid Part",
                    actor_user_id="admin",
                    now_ms=2001,
                )
    finally:
        connection.close()


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-002")
def test_org_001_002_unit_mutations_use_row_version_actor_and_stable_id(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, "admin")
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _team_a, part_id = _create_hierarchy(connection, repository, suffix="a")
            team_b, _part_b = _create_hierarchy(
                connection, repository, suffix="b", now=1100
            )
        original = repository.require_unit(connection, part_id)

        with transaction(connection):
            renamed = repository.rename_unit(
                connection,
                unit_id=part_id,
                expected_row_version=original.row_version,
                name="Renamed Part",
                actor_user_id="admin",
                updated_at_ms=2000,
            )
        assert renamed.id == original.id
        assert renamed.name_key == "renamed part"
        assert renamed.updated_by == "admin"
        assert renamed.row_version == original.row_version + 1

        with pytest.raises(OrgUnitVersionConflictError):
            with transaction(connection):
                repository.touch_unit(
                    connection,
                    unit_id=part_id,
                    expected_row_version=original.row_version,
                    actor_user_id="admin",
                    updated_at_ms=2001,
                )

        with transaction(connection):
            moved = repository.set_unit_parent(
                connection,
                unit_id=part_id,
                expected_row_version=renamed.row_version,
                parent_id=team_b,
                actor_user_id="admin",
                updated_at_ms=2002,
            )
            touched = repository.touch_unit(
                connection,
                unit_id=part_id,
                expected_row_version=moved.row_version,
                actor_user_id="admin",
                updated_at_ms=2003,
            )
        assert touched.id == part_id
        assert touched.parent_id == team_b
        assert touched.row_version == original.row_version + 3

        with transaction(connection):
            inactive = repository.set_unit_status(
                connection,
                unit_id=part_id,
                expected_row_version=touched.row_version,
                status="inactive",
                actor_user_id="admin",
                updated_at_ms=2004,
            )
        assert inactive.status == "inactive"
        assert part_id not in {
            row.unit.id
            for row in repository.list_subtree(
                connection, include_inactive=False
            )
        }

        with pytest.raises(OrganizationStateConflictError):
            with transaction(connection):
                repository.set_unit_parent(
                    connection,
                    unit_id=team_b,
                    expected_row_version=repository.require_unit(
                        connection, team_b
                    ).row_version,
                    parent_id=part_id,
                    actor_user_id="admin",
                    updated_at_ms=2005,
                )
    finally:
        connection.close()


@pytest.mark.scenario("ORG-003")
@pytest.mark.scenario("ORG-004")
@pytest.mark.scenario("ORG-005")
@pytest.mark.scenario("ORG-006")
def test_org_003_006_membership_scope_and_leader_exact_cas(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, "admin", "member", "replacement")
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            part_a_team, part_a = _create_hierarchy(
                connection, repository, suffix="a"
            )
            _part_b_team, part_b = _create_hierarchy(
                connection, repository, suffix="b", now=1100
            )
        assert part_a_team != part_a
        unassigned = repository.get_user_organization_state(connection, "member")
        assert unassigned is not None
        assert unassigned.current_part_id is None
        assert unassigned.current_membership_id is None

        membership_a = _uuid("membership:member:a")
        with transaction(connection):
            assigned = repository.compare_and_swap_user_part(
                connection,
                user_id="member",
                expected_current_part_id=None,
                target_part_id=part_a,
                expected_scope_epoch=unassigned.scope_epoch,
                expected_row_version=unassigned.row_version,
                updated_at_ms=2000,
            )
            opened = repository.open_membership(
                connection,
                membership_id=membership_a,
                user_id="member",
                part_id=part_a,
                assigned_by="admin",
                reason="initial assignment",
                valid_from_ms=2000,
            )
        assert assigned.scope_epoch == unassigned.scope_epoch + 1
        assert opened.id == membership_a
        state = repository.get_user_organization_state(connection, "member")
        assert state is not None
        assert state.current_part_id == state.membership_part_id == part_a
        assert state.current_membership_id == membership_a

        with pytest.raises(OrganizationStateConflictError):
            with transaction(connection):
                repository.open_membership(
                    connection,
                    membership_id=_uuid("membership:duplicate"),
                    user_id="member",
                    part_id=part_b,
                    assigned_by="admin",
                    reason="duplicate",
                    valid_from_ms=2001,
                )

        leader_id = _uuid("leader:member:a")
        with transaction(connection):
            leader = repository.open_part_leader(
                connection,
                assignment_id=leader_id,
                user_id="member",
                part_id=part_a,
                assigned_by="admin",
                reason="part leader",
                valid_from_ms=2001,
            )
            part_after_leader = repository.touch_unit(
                connection,
                unit_id=part_a,
                expected_row_version=repository.require_unit(
                    connection, part_a
                ).row_version,
                actor_user_id="admin",
                updated_at_ms=2001,
            )
        assert leader.id == leader_id
        assert part_after_leader.row_version == 2
        with pytest.raises(OrganizationStateConflictError):
            with transaction(connection):
                repository.open_part_leader(
                    connection,
                    assignment_id=_uuid("leader:replacement:unexpected"),
                    user_id="replacement",
                    part_id=part_a,
                    assigned_by="admin",
                    reason="stale leader compare",
                    valid_from_ms=2002,
                    expected_current_assignment_id=None,
                )

        impact = repository.get_unit_impact(connection, part_a)
        assert impact.current_member_count == 1
        assert impact.open_leader_count == 1
        assert impact.owned_space_state == "not_created"

        with transaction(connection):
            closed_leader = repository.close_part_leader(
                connection,
                part_id=part_a,
                expected_assignment_id=leader_id,
                closed_at_ms=2100,
            )
            repository.touch_unit(
                connection,
                unit_id=part_a,
                expected_row_version=part_after_leader.row_version,
                actor_user_id="admin",
                updated_at_ms=2100,
            )
            closed_membership = repository.close_current_membership(
                connection,
                user_id="member",
                expected_membership_id=membership_a,
                closed_at_ms=2100,
            )
            moved = repository.compare_and_swap_user_part(
                connection,
                user_id="member",
                expected_current_part_id=part_a,
                target_part_id=part_b,
                expected_scope_epoch=state.scope_epoch,
                expected_row_version=state.row_version,
                updated_at_ms=2100,
            )
            membership_b = repository.open_membership(
                connection,
                membership_id=_uuid("membership:member:b"),
                user_id="member",
                part_id=part_b,
                assigned_by="admin",
                reason="part move",
                valid_from_ms=2100,
            )
        assert closed_leader.valid_to_ms == 2100
        assert closed_membership.valid_to_ms == 2100
        assert moved.scope_epoch == state.scope_epoch + 1
        assert membership_b.part_id == part_b
        after = repository.get_user_organization_state(connection, "member")
        assert after is not None
        assert after.current_part_id == after.membership_part_id == part_b

        with pytest.raises(UserOrganizationVersionConflictError):
            with transaction(connection):
                repository.compare_and_swap_user_part(
                    connection,
                    user_id="member",
                    expected_current_part_id=part_a,
                    target_part_id=None,
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=2200,
                )
    finally:
        connection.close()


@pytest.mark.scenario("ORG-006")
def test_org_006_part_space_state_freeze_and_missing_space_are_distinct(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, "admin", "leader")
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _team, part_id = _create_hierarchy(connection, repository)
            _team_b, part_without_space = _create_hierarchy(
                connection, repository, suffix="b", now=1100
            )
            leader_state = repository.get_user_organization_state(
                connection, "leader"
            )
            assert leader_state is not None
            repository.compare_and_swap_user_part(
                connection,
                user_id="leader",
                expected_current_part_id=None,
                target_part_id=part_id,
                expected_scope_epoch=leader_state.scope_epoch,
                expected_row_version=leader_state.row_version,
                updated_at_ms=1200,
            )
            repository.open_membership(
                connection,
                membership_id=_uuid("space-test-membership"),
                user_id="leader",
                part_id=part_id,
                assigned_by="admin",
                reason="space test",
                valid_from_ms=1200,
            )
            repository.open_part_leader(
                connection,
                assignment_id=_uuid("space-test-leader"),
                user_id="leader",
                part_id=part_id,
                assigned_by="admin",
                reason="space test",
                valid_from_ms=1201,
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',0,0,1300,1300,1)""",
                (_uuid("space:a"), part_id),
            )
            item_id = _uuid("space-test-item")
            revision_id = _uuid("space-test-revision")
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,curation_status,
                     current_revision_id,last_curation_transition_id,
                     latest_deactivation_episode_id,redirect_item_id,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                     'leader','leader',1301,1301,1)""",
                (item_id, _uuid("space:a"), revision_id),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,
                     title,raw_content_sha256,normalized_content_sha256,
                     content_size_bytes,normalizer_version,metadata_json,
                     change_kind,change_reason,created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Space fixture',?,?,4,'v1','{}',
                     'initial','fixture','leader',1301)""",
                (revision_id, item_id, "d" * 64, "d" * 64),
            )

        state = repository.get_part_space_state(connection, part_id)
        assert state is not None and state.write_enabled is False
        with pytest.raises(PartSpaceNotFoundError):
            with transaction(connection):
                repository.set_part_space_state(
                    connection,
                    part_id=part_without_space,
                    expected_row_version=1,
                    status="active",
                    write_enabled=False,
                    updated_at_ms=1400,
                )
        with transaction(connection):
            assert repository.freeze_part_space(
                connection,
                part_id=part_without_space,
                expected_row_version=None,
                updated_at_ms=1400,
            ) is None
            enabled = repository.set_part_space_state(
                connection,
                part_id=part_id,
                expected_row_version=state.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=1400,
            )
        assert enabled.write_enabled is True
        with pytest.raises(PartSpaceVersionConflictError):
            with transaction(connection):
                repository.freeze_part_space(
                    connection,
                    part_id=part_id,
                    expected_row_version=state.row_version,
                    updated_at_ms=1401,
                )
        with transaction(connection):
            frozen = repository.freeze_part_space(
                connection,
                part_id=part_id,
                expected_row_version=enabled.row_version,
                updated_at_ms=1402,
            )
        assert frozen is not None
        assert frozen.status == "frozen"
        assert frozen.write_enabled is False
        impact = repository.get_unit_impact(connection, part_id)
        assert impact.owned_space_state == "frozen"
        assert impact.frozen_space_count == 1
        assert impact.knowledge_item_count == 1
        assert impact.active_knowledge_item_count == 1
        assert impact.descendant_owned_space_count == 0
    finally:
        connection.close()


def _seed_authority_work(
    connection,
    repository: OrganizationRepository,
    *,
    user_id: str,
    part_id: str,
    user_scope_epoch: int,
) -> tuple[str, str, tuple[str, str]]:
    space_id = _uuid("authority-space")
    item_id = _uuid("authority-item")
    revision_id = _uuid("authority-revision")
    connection.execute(
        """INSERT INTO knowledge_space(
             id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
             generation,created_at,updated_at,row_version
           ) VALUES (?,'part',NULL,?,'active',0,0,3000,3000,1)""",
        (space_id, part_id),
    )
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,updated_by,
             created_at,updated_at,row_version
           ) VALUES (?, ?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,
             3001,3001,1)""",
        (item_id, space_id, revision_id, user_id, user_id),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,'Authority fixture',?,?,4,'v1','{}',
             'initial','fixture',?,3001)""",
        (revision_id, item_id, "a" * 64, "a" * 64, user_id),
    )
    connection.execute(
        """UPDATE item_lease SET holder_user_id=?,editor_session_id=?,token_hmac=?,
             hmac_key_id='v1',fencing_generation=1,base_item_row_version=1,
             base_revision_id=?,acquired_at=3002,heartbeat_at=3002,
             expires_at=4000,absolute_expires_at=5000 WHERE item_id=?""",
        (user_id, _uuid("editor-session"), b"x" * 32, revision_id, item_id),
    )
    snapshot = {
        "schema_version": 1,
        "actor_kind": "user",
        "actor_user_id": user_id,
        "account_status": "active",
        "system_role": "user",
        "current_part_id": part_id,
        "scope_epoch": user_scope_epoch,
        "space_id": space_id,
        "capabilities": ["read"],
    }
    job_ids = (
        _uuid("old-cancellable-job"),
        _uuid("old-retained-job"),
    )
    for job_id, epoch, cancellable in (
        (job_ids[0], user_scope_epoch, 1),
        (job_ids[1], user_scope_epoch, 0),
    ):
        job_snapshot = dict(snapshot, scope_epoch=epoch)
        job_snapshot_json = json.dumps(
            job_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        job_snapshot_sha = sha256(job_snapshot_json.encode("utf-8")).hexdigest()
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
               ) VALUES (?,NULL,NULL,'user',?,?,'fixture.job','v1',?,'{}',?,'{}',
                 ?,?,?,?,'["read"]','queued',NULL,NULL,0,3,?,0,0,NULL,NULL,
                 3003,3003,3003,NULL)""",
            (
                job_id,
                user_id,
                space_id,
                f"fixture:{job_id}",
                "b" * 64,
                job_snapshot_json,
                job_snapshot_sha,
                epoch,
                part_id,
                cancellable,
            ),
        )
    return space_id, item_id, job_ids


def _insert_new_epoch_job(
    connection,
    *,
    job_id: str,
    user_id: str,
    space_id: str,
    scope_epoch: int,
) -> None:
    snapshot = {
        "schema_version": 1,
        "actor_kind": "user",
        "actor_user_id": user_id,
        "account_status": "active",
        "system_role": "user",
        "current_part_id": None,
        "scope_epoch": scope_epoch,
        "space_id": space_id,
        "capabilities": ["read"],
    }
    snapshot_json = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    snapshot_sha = sha256(snapshot_json.encode("utf-8")).hexdigest()
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
           ) VALUES (?,NULL,NULL,'user',?,?,'fixture.job','v1',?,'{}',?,'{}',
             ?,?,?,NULL,'["read"]','queued',NULL,NULL,0,3,1,0,0,NULL,NULL,
             3100,3100,3100,NULL)""",
        (
            job_id,
            user_id,
            space_id,
            f"fixture:{job_id}",
            "c" * 64,
            snapshot_json,
            snapshot_sha,
            scope_epoch,
        ),
    )


@pytest.mark.scenario("ORG-005")
def test_org_005_move_revokes_old_part_lease_and_invalidates_exact_old_epoch_jobs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, "admin", "member")
    repository = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _team, part_id = _create_hierarchy(connection, repository)
            initial = repository.get_user_organization_state(connection, "member")
            assert initial is not None
            assigned = repository.compare_and_swap_user_part(
                connection,
                user_id="member",
                expected_current_part_id=None,
                target_part_id=part_id,
                expected_scope_epoch=initial.scope_epoch,
                expected_row_version=initial.row_version,
                updated_at_ms=2000,
            )
            membership = repository.open_membership(
                connection,
                membership_id=_uuid("authority-membership"),
                user_id="member",
                part_id=part_id,
                assigned_by="admin",
                reason="authority fixture",
                valid_from_ms=2000,
            )
            space_id, item_id, old_job_ids = _seed_authority_work(
                connection,
                repository,
                user_id="member",
                part_id=part_id,
                user_scope_epoch=assigned.scope_epoch,
            )

        before_lease = connection.execute(
            "SELECT fencing_generation,holder_user_id FROM item_lease WHERE item_id=?",
            (item_id,),
        ).fetchone()
        assert before_lease is not None
        assert before_lease[0] == 1 and before_lease[1] == "member"

        with transaction(connection):
            repository.close_current_membership(
                connection,
                user_id="member",
                expected_membership_id=membership.id,
                closed_at_ms=3100,
            )
            unassigned = repository.compare_and_swap_user_part(
                connection,
                user_id="member",
                expected_current_part_id=part_id,
                target_part_id=None,
                expected_scope_epoch=assigned.scope_epoch,
                expected_row_version=assigned.row_version,
                updated_at_ms=3100,
            )
            new_epoch_job_id = _uuid("new-epoch-job")
            _insert_new_epoch_job(
                connection,
                job_id=new_epoch_job_id,
                user_id="member",
                space_id=space_id,
                scope_epoch=unassigned.scope_epoch,
            )
            lease_counts = repository.revoke_user_part_leases(
                connection, user_id="member", part_id=part_id
            )
            job_counts = repository.invalidate_user_jobs_for_scope_epoch(
                connection,
                user_id="member",
                invalidated_scope_epoch=assigned.scope_epoch,
                updated_at_ms=3100,
            )
        counts = lease_counts.combine(job_counts)
        assert unassigned.scope_epoch == assigned.scope_epoch + 1
        assert unassigned.current_part_id is None
        assert counts.revoked_lease_count == 1
        assert counts.invalidated_job_count == 2
        assert counts.cancel_requested_job_count == 1

        after_lease = connection.execute(
            "SELECT fencing_generation,holder_user_id FROM item_lease WHERE item_id=?",
            (item_id,),
        ).fetchone()
        assert after_lease is not None
        assert after_lease[0] == 2 and after_lease[1] is None
        job_rows = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT id,cancel_requested FROM durable_job ORDER BY id"
            ).fetchall()
        }
        assert job_rows[old_job_ids[0]] == 1
        assert job_rows[old_job_ids[1]] == 0
        assert job_rows[new_epoch_job_id] == 0

        with transaction(connection):
            retry_counts = repository.invalidate_user_jobs_for_scope_epoch(
                connection,
                user_id="member",
                invalidated_scope_epoch=assigned.scope_epoch,
                updated_at_ms=3200,
            )
        assert retry_counts.invalidated_job_count == 2
        assert retry_counts.cancel_requested_job_count == 0
    finally:
        connection.close()
