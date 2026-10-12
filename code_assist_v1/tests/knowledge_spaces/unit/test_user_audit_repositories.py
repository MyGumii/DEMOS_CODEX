from __future__ import annotations

from dataclasses import replace

import hashlib
import json
import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.legacy.compatibility import SessionResolution
from code_assist_v1.knowledge_core.repositories.audit import (
    AuditConflictError,
    AuditEventDraft,
    AuditRepository,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    OperationIdempotencyConflictError,
    UserHardDeleteForbiddenError,
    UserImportConflictError,
    UserLeaderGuardError,
    UserMembershipGuardError,
    UserRepository,
    UserVersionConflictError,
)


pytestmark = pytest.mark.unit


def _uuid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _migrated_factory(isolated_knowledge_layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        isolated_knowledge_layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p1.4-test",
        storage_layout=_layout(isolated_knowledge_layout),
    ).run()
    return factory


def _legacy_user(
    user_id: str,
    *,
    role: str = "user",
    status: str = "active",
    part_id: str | None = None,
    created_at: int = 100,
    must_change_password: bool = False,
    password_changed_at_ms: int | None = None,
) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.upper(),
        password_hash=f"scrypt:legacy-preserved:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status=status,  # type: ignore[arg-type]
        current_part_id=part_id,
        created_at_ms=created_at,
        updated_at_ms=created_at,
        disabled_at_ms=created_at if status == "disabled" else None,
        must_change_password=must_change_password,
        password_changed_at_ms=password_changed_at_ms,
    )


def _audit_draft(
    *,
    event_number: int,
    request_number: int,
    actor_id: str,
    action: str = "user.disable",
    details: dict[str, object] | None = None,
) -> AuditEventDraft:
    return AuditEventDraft(
        event_uuid=_uuid(event_number),
        occurred_at_ms=500,
        request_id=_uuid(request_number),
        operation_id=_uuid(request_number + 1000),
        action=action,
        outcome="committed",
        actor_kind="user",
        actor_user_id=actor_id,
        actor_display_name=actor_id.upper(),
        actor_role_snapshot="admin",
        reason="fixture reason",
        details=details or {},
        request_meta={"remote_addr": "127.0.0.1"},
    )


@pytest.mark.scenario("AUTH-001")
def test_auth_001_legacy_user_import_is_exact_idempotent_and_hash_preserving(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    legacy = _legacy_user(
        "legacy01",
        role="admin",
        must_change_password=True,
        password_changed_at_ms=None,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            first = users.import_legacy(connection, legacy)
        assert first.inserted is True
        assert first.user.password_hash == legacy.password_hash
        assert first.user.system_role == "admin"
        assert first.user.must_change_password is True
        assert first.user.password_changed_at_ms is None
        assert first.user.as_legacy_public_user()["must_change_password"] is True
        assert first.user.as_legacy_public_user()["password_changed_at"] is None
        assert legacy.password_hash not in repr(legacy)
        assert legacy.password_hash not in repr(first.user)
        with pytest.raises(ValueError, match="forced password-change"):
            with transaction(connection):
                users.import_legacy(
                    connection,
                    replace(legacy, password_changed_at_ms=legacy.created_at_ms),
                )

        with transaction(connection):
            retry = users.import_legacy(connection, legacy)
        assert retry.inserted is False
        assert retry.user == first.user
        assert users.count(connection) == 1

        conflicting = replace(legacy, password_hash="scrypt:different")
        with pytest.raises(UserImportConflictError):
            with transaction(connection):
                users.import_legacy(connection, conflicting)
        assert users.require(connection, legacy.id).password_hash == legacy.password_hash
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_auth_001_password_policy_survives_import_reset_change_and_repr(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    connection = factory.open()
    try:
        original_hash = "scrypt:original-secret-material"
        with transaction(connection):
            imported = users.import_legacy(
                connection,
                LegacyUserRecord(
                    id="user01",
                    display_name="User 01",
                    password_hash=original_hash,
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                    must_change_password=False,
                    password_changed_at_ms=100,
                ),
            ).user
        assert imported.password_changed_at_ms == 100
        assert imported.must_change_password is False
        assert original_hash not in repr(imported)
        assert original_hash not in repr(
            SessionResolution(
                user=imported,
                public_user=imported.as_legacy_public_user(),
                stale=False,
            )
        )

        reset_hash = "scrypt:admin-reset-secret-material"
        with transaction(connection):
            reset = users.update_password_hash(
                connection,
                user_id="user01",
                expected_row_version=1,
                password_hash=reset_hash,
                password_change_kind="admin_reset",
                updated_at_ms=200,
            )
        assert reset.must_change_password is True
        assert reset.password_changed_at_ms is None
        assert reset.as_legacy_public_user()["must_change_password"] is True
        assert reset_hash not in repr(reset)

        repeated_reset_hash = "scrypt:repeated-admin-reset-secret-material"
        with transaction(connection):
            repeated_reset = users.update_password_hash(
                connection,
                user_id="user01",
                expected_row_version=2,
                password_hash=repeated_reset_hash,
                password_change_kind="admin_reset",
                updated_at_ms=250,
            )
        assert repeated_reset.must_change_password is True
        assert repeated_reset.password_changed_at_ms is None
        assert repeated_reset.row_version == 3
        assert repeated_reset_hash not in repr(repeated_reset)

        changed_hash = "scrypt:self-change-secret-material"
        with transaction(connection):
            changed = users.update_password_hash(
                connection,
                user_id="user01",
                expected_row_version=3,
                password_hash=changed_hash,
                password_change_kind="self_change",
                updated_at_ms=300,
            )
        assert changed.must_change_password is False
        assert changed.password_changed_at_ms == 300
        assert changed.as_legacy_public_user()["password_changed_at"] == 300
        assert changed_hash not in repr(changed)
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_auth_001_operation_dedup_detects_hash_conflict_and_freezes_result(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(connection, _legacy_user("admin", role="admin"))
        with transaction(connection):
            begun = users.begin_operation(
                connection,
                actor_user_id="admin",
                operation_type="account.disable",
                operation_id=_uuid(10),
                request_sha256="a" * 64,
                dedup_id=_uuid(11),
                now_ms=100,
            )
            committed = users.commit_operation(
                connection,
                dedup_id=begun.operation.id,
                result={"status": "disabled", "user_id": "legacy01"},
                http_status=200,
                completed_at_ms=101,
            )
        assert committed.status == "committed"
        assert committed.result == {"status": "disabled", "user_id": "legacy01"}

        with transaction(connection):
            retry = users.begin_operation(
                connection,
                actor_user_id="admin",
                operation_type="account.disable",
                operation_id=_uuid(10),
                request_sha256="a" * 64,
                dedup_id=_uuid(11),
                now_ms=102,
            )
        assert retry.inserted is False
        assert retry.operation.result_json == committed.result_json
        assert retry.operation.last_seen_at_ms == 102

        with pytest.raises(OperationIdempotencyConflictError):
            with transaction(connection):
                users.begin_operation(
                    connection,
                    actor_user_id="admin",
                    operation_type="account.disable",
                    operation_id=_uuid(10),
                    request_sha256="b" * 64,
                    dedup_id=_uuid(11),
                    now_ms=103,
                )
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_auth_001_audit_append_is_idempotent_and_rolls_back_with_domain_state(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    audit = AuditRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(connection, _legacy_user("admin", role="admin"))

        with pytest.raises(RuntimeError, match="failpoint"):
            with transaction(connection):
                users.update_profile(
                    connection,
                    user_id="admin",
                    expected_row_version=1,
                    display_name="Changed then rolled back",
                    system_role="admin",
                    updated_at_ms=200,
                )
                audit.append(
                    connection,
                    _audit_draft(
                        event_number=20,
                        request_number=21,
                        actor_id="admin",
                    ),
                )
                raise RuntimeError("failpoint")
        assert users.require(connection, "admin").display_name == "ADMIN"
        assert audit.get_by_event_uuid(connection, _uuid(20)) is None

        draft = _audit_draft(
            event_number=22,
            request_number=23,
            actor_id="admin",
            details={"z": 1, "a": "stable"},
        )
        with transaction(connection):
            updated = users.update_profile(
                connection,
                user_id="admin",
                expected_row_version=1,
                display_name="Canonical Admin",
                system_role="admin",
                updated_at_ms=300,
            )
            first = audit.append(connection, draft)
        assert updated.row_version == 2
        with transaction(connection):
            retry = audit.append(connection, draft)
        assert retry.id == first.id
        assert retry.details_json == '{"a":"stable","z":1}'

        conflict = replace(draft, outcome="different")
        with pytest.raises(AuditConflictError):
            with transaction(connection):
                audit.append(connection, conflict)
        assert len(audit.list_recent(connection)) == 1

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                audit.append(
                    connection,
                    _audit_draft(
                        event_number=24,
                        request_number=25,
                        actor_id="missing-user",
                    ),
                )
    finally:
        connection.close()


def _seed_part_account_fixture(connection, users: UserRepository) -> dict[str, str]:
    team_id = _uuid(100)
    part_id = _uuid(101)
    part_space_id = _uuid(102)
    leader_membership_id = _uuid(103)
    replacement_membership_id = _uuid(104)
    leader_assignment_id = _uuid(105)
    item_id = _uuid(106)
    revision_id = _uuid(107)
    editor_session_id = _uuid(108)
    job_id = _uuid(109)
    with transaction(connection):
        users.import_legacy(connection, _legacy_user("admin", role="admin"))
        connection.execute(
            """INSERT INTO org_unit(
                 id,kind,parent_id,name,name_key,status,created_at,updated_at,
                 created_by,updated_by,row_version
               ) VALUES (?,'team',?,'Team','team','active',10,10,'admin','admin',1)""",
            (team_id, RESPONSIBILITY_ROOT_ID),
        )
        connection.execute(
            """INSERT INTO org_unit(
                 id,kind,parent_id,name,name_key,status,created_at,updated_at,
                 created_by,updated_by,row_version
               ) VALUES (?,'part',?,'Part','part','active',10,10,'admin','admin',1)""",
            (part_id, team_id),
        )
        users.import_legacy(
            connection, _legacy_user("leader", part_id=part_id, created_at=20)
        )
        users.import_legacy(
            connection, _legacy_user("replacement", part_id=part_id, created_at=20)
        )
        connection.execute(
            """INSERT INTO org_membership(
                 id,user_id,part_id,valid_from,valid_to,assigned_by,reason
               ) VALUES (?,'leader',?,20,NULL,'admin','initial')""",
            (leader_membership_id, part_id),
        )
        connection.execute(
            """INSERT INTO org_membership(
                 id,user_id,part_id,valid_from,valid_to,assigned_by,reason
               ) VALUES (?,'replacement',?,20,NULL,'admin','initial')""",
            (replacement_membership_id, part_id),
        )
        users.open_part_leader_assignment(
            connection,
            assignment_id=leader_assignment_id,
            user_id="leader",
            part_id=part_id,
            assigned_by="admin",
            reason="initial leader",
            valid_from_ms=20,
        )
        connection.execute(
            """INSERT INTO knowledge_space(
                 id,space_kind,owner_user_id,owner_org_unit_id,status,
                 write_enabled,generation,created_at,updated_at,row_version
               ) VALUES (?,'part',NULL,?,'active',1,0,20,20,1)""",
            (part_space_id, part_id),
        )
        connection.execute(
            """INSERT INTO knowledge_item(
                 id,space_id,lineage_group_id,lifecycle_status,curation_status,
                 current_revision_id,last_curation_transition_id,
                 latest_deactivation_episode_id,redirect_item_id,created_by,
                 updated_by,created_at,updated_at,row_version
               ) VALUES (?, ?,NULL,'active','provisional',?,NULL,NULL,NULL,
                 'leader','leader',30,30,1)""",
            (item_id, part_space_id, revision_id),
        )
        connection.execute(
            """INSERT INTO knowledge_revision(
                 id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                 raw_content_sha256,normalized_content_sha256,content_size_bytes,
                 normalizer_version,metadata_json,change_kind,change_reason,
                 created_by,created_at
               ) VALUES (?,?,1,NULL,NULL,'Fixture',?,?,4,'v1','{}','initial',
                 'fixture','leader',30)""",
            (revision_id, item_id, "c" * 64, "c" * 64),
        )
        connection.execute(
            """UPDATE item_lease SET
                 holder_user_id='leader',editor_session_id=?,token_hmac=?,
                 hmac_key_id='v1',fencing_generation=1,base_item_row_version=1,
                 base_revision_id=?,acquired_at=30,heartbeat_at=30,expires_at=300,
                 absolute_expires_at=600 WHERE item_id=?""",
            (editor_session_id, b"x" * 32, revision_id, item_id),
        )
        snapshot = {
            "schema_version": 1,
            "actor_kind": "user",
            "actor_user_id": "leader",
            "account_status": "active",
            "system_role": "user",
            "current_part_id": part_id,
            "scope_epoch": 1,
            "space_id": part_space_id,
            "capabilities": ["read"],
        }
        snapshot_json = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
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
               ) VALUES (?,NULL,NULL,'user','leader',?,'fixture.job','v1',?,
                 '{}',?,'{}',?,?,1,?,'[\"read\"]','queued',NULL,NULL,0,3,1,0,0,
                 NULL,NULL,30,30,30,NULL)""",
            (
                job_id,
                part_space_id,
                f"fixture:{job_id}",
                "d" * 64,
                snapshot_json,
                hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                part_id,
            ),
        )
    return {
        "part_id": part_id,
        "part_space_id": part_space_id,
        "leader_assignment_id": leader_assignment_id,
        "item_id": item_id,
        "job_id": job_id,
    }


@pytest.mark.scenario("AUTH-001")
def test_auth_001_disable_requires_safe_leader_order_and_atomically_revokes_authority(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    audit = AuditRepository()
    connection = factory.open()
    try:
        ids = _seed_part_account_fixture(connection, users)
        with pytest.raises(UserLeaderGuardError):
            with transaction(connection):
                users.transition_account_status(
                    connection,
                    user_id="leader",
                    target_status="disabled",
                    expected_row_version=1,
                    changed_at_ms=200,
                )
        assert users.require(connection, "leader").account_status == "active"

        with pytest.raises(UserMembershipGuardError):
            with transaction(connection):
                state = users.get_part_space_write_state(connection, ids["part_id"])
                assert state is not None
                users.set_part_space_write_enabled(
                    connection,
                    part_id=ids["part_id"],
                    enabled=False,
                    expected_row_version=state.row_version,
                    updated_at_ms=150,
                )
                users.close_leader_assignment(
                    connection,
                    assignment_id=ids["leader_assignment_id"],
                    closed_at_ms=150,
                )
                users.transition_account_status(
                    connection,
                    user_id="leader",
                    target_status="disabled",
                    expected_row_version=1,
                    changed_at_ms=150,
                )
        assert users.get_active_part_leader(connection, ids["part_id"]) is not None

        with transaction(connection):
            begun = users.begin_operation(
                connection,
                actor_user_id="admin",
                operation_type="account.disable",
                operation_id=_uuid(200),
                request_sha256="e" * 64,
                dedup_id=_uuid(201),
                now_ms=200,
            )
            state = users.get_part_space_write_state(connection, ids["part_id"])
            assert state is not None
            users.set_part_space_write_enabled(
                connection,
                part_id=ids["part_id"],
                enabled=False,
                expected_row_version=state.row_version,
                updated_at_ms=200,
            )
            users.close_leader_assignment(
                connection,
                assignment_id=ids["leader_assignment_id"],
                closed_at_ms=200,
            )
            users.open_part_leader_assignment(
                connection,
                assignment_id=_uuid(202),
                user_id="replacement",
                part_id=ids["part_id"],
                assigned_by="admin",
                reason="replacement before disable",
                valid_from_ms=200,
            )
            disabled_state = users.get_part_space_write_state(
                connection, ids["part_id"]
            )
            assert disabled_state is not None
            users.set_part_space_write_enabled(
                connection,
                part_id=ids["part_id"],
                enabled=True,
                expected_row_version=disabled_state.row_version,
                updated_at_ms=201,
            )
            users.close_open_membership(
                connection, user_id="leader", closed_at_ms=200
            )
            transition = users.transition_account_status(
                connection,
                user_id="leader",
                target_status="disabled",
                expected_row_version=1,
                changed_at_ms=200,
            )
            audit.append(
                connection,
                _audit_draft(
                    event_number=203,
                    request_number=204,
                    actor_id="admin",
                    details={
                        "target_user_id": "leader",
                        "scope_epoch": transition.user.scope_epoch,
                    },
                ),
            )
            users.commit_operation(
                connection,
                dedup_id=begun.operation.id,
                result={
                    "user_id": "leader",
                    "scope_epoch": transition.user.scope_epoch,
                },
                http_status=200,
                completed_at_ms=201,
            )

        assert transition.changed is True
        assert transition.previous_part_id == ids["part_id"]
        assert transition.user.account_status == "disabled"
        assert transition.user.current_part_id is None
        assert transition.user.scope_epoch == 2
        assert transition.revoked_lease_count == 1
        assert transition.invalidated_job_count == 1
        assert transition.cancel_requested_job_count == 1
        assert users.session_epoch_is_current(
            connection, user_id="leader", captured_scope_epoch=1
        ) is False
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (ids["item_id"],),
        ).fetchone()
        assert tuple(lease) == (None, 2)
        job = connection.execute(
            "SELECT cancel_requested,authorization_scope_epoch FROM durable_job WHERE id=?",
            (ids["job_id"],),
        ).fetchone()
        assert tuple(job) == (1, 1)
        leader = users.get_active_part_leader(connection, ids["part_id"])
        assert leader is not None and leader.user_id == "replacement"
        space = users.get_part_space_write_state(connection, ids["part_id"])
        assert space is not None and space.write_enabled is True
    finally:
        connection.close()


def test_part_space_write_noop_still_enforces_expected_row_version(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    connection = factory.open()
    try:
        ids = _seed_part_account_fixture(connection, users)
        with transaction(connection):
            first = users.set_part_space_write_enabled(
                connection,
                part_id=ids["part_id"],
                enabled=False,
                expected_row_version=1,
                updated_at_ms=200,
            )
        assert first.write_enabled is False and first.row_version == 2

        with pytest.raises(UserVersionConflictError):
            with transaction(connection):
                users.set_part_space_write_enabled(
                    connection,
                    part_id=ids["part_id"],
                    enabled=False,
                    expected_row_version=1,
                    updated_at_ms=201,
                )
        current = users.get_part_space_write_state(connection, ids["part_id"])
        assert current == first
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-002")
def test_auth_002_reenable_keeps_user_unassigned_and_does_not_revive_old_epoch(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(
                connection,
                _legacy_user("disabled01", status="disabled", created_at=100),
            )
        assert users.session_epoch_is_current(
            connection, user_id="disabled01", captured_scope_epoch=1
        ) is False
        with transaction(connection):
            enabled = users.transition_account_status(
                connection,
                user_id="disabled01",
                target_status="active",
                expected_row_version=1,
                changed_at_ms=200,
            )
        assert enabled.user.account_status == "active"
        assert enabled.user.current_part_id is None
        assert enabled.user.scope_epoch == 2
        assert users.session_epoch_is_current(
            connection, user_id="disabled01", captured_scope_epoch=1
        ) is False
        assert users.session_epoch_is_current(
            connection, user_id="disabled01", captured_scope_epoch=2
        ) is True
        with transaction(connection):
            retry = users.transition_account_status(
                connection,
                user_id="disabled01",
                target_status="active",
                expected_row_version=1,
                changed_at_ms=300,
            )
        assert retry.changed is False
        assert retry.user.scope_epoch == 2
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-003")
def test_auth_003_repository_never_hard_deletes_even_with_audit_reference(
    isolated_knowledge_layout,
) -> None:
    factory = _migrated_factory(isolated_knowledge_layout)
    users = UserRepository()
    audit = AuditRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(connection, _legacy_user("admin", role="admin"))
            audit.append(
                connection,
                _audit_draft(
                    event_number=300,
                    request_number=301,
                    actor_id="admin",
                ),
            )
        with pytest.raises(UserHardDeleteForbiddenError):
            users.hard_delete(connection, "admin")
        assert users.require(connection, "admin").id == "admin"
        assert len(audit.list_recent(connection, actor_user_id="admin")) == 1
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute("DELETE FROM app_user WHERE id='admin'")
    finally:
        connection.close()
