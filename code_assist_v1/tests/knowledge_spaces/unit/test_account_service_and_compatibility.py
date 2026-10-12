from __future__ import annotations

from hashlib import sha256
import json
import sqlite3
from uuid import UUID, uuid5

import pytest
from werkzeug.security import generate_password_hash

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.auth_security import CSRF_SESSION_KEY
from code_assist_v1.knowledge_core.legacy.compatibility import (
    LegacyAccountAdapter,
    legacy_user_records,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import (
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserHardDeleteForbiddenError,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.organization import (
    AccountFailpointTriggered,
    AccountIdempotencyError,
    AccountLifecycleCommand,
    AccountRevisionConflictError,
    AccountScopeStaleError,
    AccountService,
)


pytestmark = pytest.mark.unit
_FIXTURE_NAMESPACE = UUID("60d456a0-7889-45a3-bbae-b1b64efefae4")


def _uuid(label: str) -> str:
    return str(uuid5(_FIXTURE_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.4-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(
    user_id: str,
    *,
    role: str = "user",
    created: int = 1000,
) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=generate_password_hash("correct horse battery staple"),
        system_role=role,  # type: ignore[arg-type]
        account_status="active",
        created_at_ms=created,
        updated_at_ms=created,
    )


def _command(
    target: str,
    *,
    version: int,
    operation: str,
    replacement: str | None = None,
) -> AccountLifecycleCommand:
    return AccountLifecycleCommand(
        actor_user_id="admin",
        target_user_id=target,
        expected_row_version=version,
        operation_id=_uuid(f"operation:{operation}"),
        request_id=_uuid(f"request:{operation}"),
        reason="P1.4 account lifecycle test",
        replacement_leader_user_id=replacement,
        request_meta={"remote_addr": "127.0.0.1"},
    )


def _seed_part(
    factory: ConnectionFactory,
    *,
    leader: str,
    replacement: str,
    write_enabled: bool = True,
) -> tuple[str, str]:
    root_id = RESPONSIBILITY_ROOT_ID
    team_id = _uuid("team")
    part_id = _uuid("part")
    space_id = _uuid("part-space")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO org_unit(
                     id,kind,parent_id,name,name_key,status,created_at,updated_at,
                     created_by,updated_by,row_version
                   ) VALUES (?,'team',?,'Team','team','active',2000,2000,'admin','admin',1)""",
                (team_id, root_id),
            )
            connection.execute(
                """INSERT INTO org_unit(
                     id,kind,parent_id,name,name_key,status,created_at,updated_at,
                     created_by,updated_by,row_version
                   ) VALUES (?,'part',?,'Part','part','active',2001,2001,'admin','admin',1)""",
                (part_id, team_id),
            )
            for index, user_id in enumerate((leader, replacement), start=1):
                connection.execute(
                    "UPDATE app_user SET current_part_id=?,scope_epoch=scope_epoch+1,"
                    "updated_at=?,row_version=row_version+1 WHERE id=?",
                    (part_id, 2100 + index, user_id),
                )
                connection.execute(
                    """INSERT INTO org_membership(
                         id,user_id,part_id,valid_from,valid_to,assigned_by,reason
                       ) VALUES (?,?,?,?,NULL,'admin','seed')""",
                    (_uuid(f"membership:{user_id}"), user_id, part_id, 2000 + index),
                )
            connection.execute(
                """INSERT INTO org_role_assignment(
                     id,user_id,org_unit_id,role,valid_from,valid_to,assigned_by,reason
                   ) VALUES (?,? ,?,'part_leader',2050,NULL,'admin','seed')""",
                (_uuid("leader-role"), leader, part_id),
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',?,0,2200,2200,1)""",
                (space_id, part_id, 1 if write_enabled else 0),
            )
    finally:
        connection.close()
    return part_id, space_id


def _seed_lease_and_jobs(
    factory: ConnectionFactory,
    *,
    actor: str,
    part_id: str,
    space_id: str,
) -> dict[str, str]:
    item_id = _uuid("authority-item")
    revision_id = _uuid("authority-revision")
    editor_session_id = _uuid("authority-editor-session")
    cancellable_job_id = _uuid("authority-job-cancellable")
    retained_job_id = _uuid("authority-job-retained")
    connection = factory.open()
    try:
        with transaction(connection):
            user = UserRepository().require(connection, actor)
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,curation_status,
                     current_revision_id,last_curation_transition_id,
                     latest_deactivation_episode_id,redirect_item_id,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?, ?,NULL,'active','provisional',?,NULL,NULL,NULL,
                     ?,?,3000,3000,1)""",
                (item_id, space_id, revision_id, actor, actor),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Fixture',?,?,4,'v1','{}','initial',
                     'fixture',?,3000)""",
                (revision_id, item_id, "c" * 64, "c" * 64, actor),
            )
            connection.execute(
                """UPDATE item_lease SET
                     holder_user_id=?,editor_session_id=?,token_hmac=?,
                     hmac_key_id='v1',fencing_generation=1,base_item_row_version=1,
                     base_revision_id=?,acquired_at=3000,heartbeat_at=3000,
                     expires_at=4000,absolute_expires_at=5000 WHERE item_id=?""",
                (actor, editor_session_id, b"x" * 32, revision_id, item_id),
            )
            snapshot = {
                "schema_version": 1,
                "actor_kind": "user",
                "actor_user_id": actor,
                "account_status": "active",
                "system_role": user.system_role,
                "current_part_id": part_id,
                "scope_epoch": user.scope_epoch,
                "space_id": space_id,
                "capabilities": ["read"],
            }
            snapshot_json = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            snapshot_sha = sha256(snapshot_json.encode("utf-8")).hexdigest()
            for job_id, cancellable in (
                (cancellable_job_id, 1),
                (retained_job_id, 0),
            ):
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
                       ) VALUES (?,NULL,NULL,'user',?,?,'fixture.job','v1',?,
                         '{}',?,'{}',?,?,?,?,'["read"]','queued',NULL,NULL,0,3,?,0,0,
                         NULL,NULL,3000,3000,3000,NULL)""",
                    (
                        job_id,
                        actor,
                        space_id,
                        f"fixture:{job_id}",
                        "d" * 64,
                        snapshot_json,
                        snapshot_sha,
                        user.scope_epoch,
                        part_id,
                        cancellable,
                    ),
                )
    finally:
        connection.close()
    return {
        "item_id": item_id,
        "cancellable_job_id": cancellable_job_id,
        "retained_job_id": retained_job_id,
    }


@pytest.mark.scenario("AUTH-001")
def test_auth_001_disable_replaces_leader_and_replay_is_one_atomic_result(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users(
        (_user("admin", role="admin"), _user("leader"), _user("replacement"))
    )
    part_id, space_id = _seed_part(
        factory, leader="leader", replacement="replacement"
    )
    before = factory.open()
    try:
        leader = UserRepository().require(before, "leader")
        stale_cookie: dict[str, object] = {}
        LegacyAccountAdapter().login(stale_cookie, leader)
    finally:
        before.close()

    command = _command(
        "leader",
        version=leader.row_version,
        operation="replace-leader",
        replacement="replacement",
    )
    first = service.disable_account(command)
    # A transport retry has a new server request ID/connection metadata but the
    # same semantic command and operation ID.  It must return the first commit.
    replay = service.disable_account(
        AccountLifecycleCommand(
            actor_user_id=command.actor_user_id,
            target_user_id=command.target_user_id,
            expected_row_version=command.expected_row_version,
            operation_id=command.operation_id,
            request_id=_uuid("request:replace-leader-retry"),
            reason=command.reason,
            replacement_leader_user_id=command.replacement_leader_user_id,
            request_meta={"remote_addr": "127.0.0.2"},
        )
    )
    assert first == replay
    assert first.changed is True
    assert first.leader_action == "replaced"
    assert first.part_write_enabled_after is True
    assert first.closed_membership is True

    connection = factory.open()
    try:
        users = UserRepository()
        disabled = users.require(connection, "leader")
        assert disabled.account_status == "disabled"
        assert disabled.current_part_id is None
        assert disabled.scope_epoch == leader.scope_epoch + 1
        assert users.get_open_membership(connection, "leader") is None
        new_leader = users.get_active_part_leader(connection, part_id)
        assert new_leader is not None and new_leader.user_id == "replacement"
        part_space = users.get_part_space_write_state(connection, part_id)
        assert part_space is not None and part_space.space_id == space_id
        assert part_space.write_enabled is True
        assert LegacyAccountAdapter().resolve_session(
            connection, stale_cookie
        ).stale is True
        assert len(AuditRepository().list_recent(connection)) == 1
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE state='pending'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_auth_001_disable_without_replacement_turns_part_writes_off(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users(
        (_user("admin", role="admin"), _user("leader"), _user("member"))
    )
    part_id, _space_id = _seed_part(
        factory, leader="leader", replacement="member"
    )
    connection = factory.open()
    try:
        version = UserRepository().require(connection, "leader").row_version
    finally:
        connection.close()

    result = service.disable_account(
        _command("leader", version=version, operation="write-off")
    )
    assert result.leader_action == "write_disabled"
    assert result.part_write_enabled_after is False
    connection = factory.open()
    try:
        users = UserRepository()
        assert users.get_active_part_leader(connection, part_id) is None
        space = users.get_part_space_write_state(connection, part_id)
        assert space is not None and space.write_enabled is False
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
@pytest.mark.parametrize(
    "failpoint_name",
    (
        "account.after_status",
        "account.after_scope_epoch",
        "account.after_session_revoke",
    ),
)
def test_auth_001_audit_failure_or_failpoint_rolls_back_every_disable_effect(
    isolated_knowledge_layout,
    failpoint_name: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    bootstrap = AccountService(factory)
    bootstrap.import_legacy_users((_user("admin", role="admin"), _user("target")))
    connection = factory.open()
    try:
        before = UserRepository().require(connection, "target")
    finally:
        connection.close()

    def failpoint(name: str) -> None:
        if name == failpoint_name:
            raise AccountFailpointTriggered("intentional rollback")

    with pytest.raises(AccountFailpointTriggered):
        AccountService(factory, failpoint=failpoint).disable_account(
            _command("target", version=before.row_version, operation="rollback")
        )

    class ExplodingAuditRepository(AuditRepository):
        def append(self, connection, draft):  # type: ignore[no-untyped-def]
            del connection, draft
            raise RuntimeError("intentional audit append failure")

    with pytest.raises(RuntimeError, match="audit append failure"):
        AccountService(factory, audit=ExplodingAuditRepository()).disable_account(
            _command("target", version=before.row_version, operation="audit-rollback")
        )

    connection = factory.open()
    try:
        after = UserRepository().require(connection, "target")
        assert after == before
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operation_dedup").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-002")
def test_auth_002_reenable_restores_only_login_and_old_cookie_never_revives(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users((_user("admin", role="admin"), _user("target")))
    connection = factory.open()
    try:
        original = UserRepository().require(connection, "target")
        old_cookie: dict[str, object] = {}
        LegacyAccountAdapter().login(old_cookie, original)
    finally:
        connection.close()

    disabled = service.disable_account(
        _command("target", version=original.row_version, operation="disable")
    )
    enabled = service.reenable_account(
        _command("target", version=disabled.row_version, operation="reenable")
    )
    assert enabled.account_status == "active"
    assert enabled.scope_epoch == original.scope_epoch + 2
    assert enabled.previous_part_id is None

    connection = factory.open()
    try:
        adapter = LegacyAccountAdapter()
        assert adapter.resolve_session(connection, old_cookie).stale is True
        current = UserRepository().require(connection, "target")
        assert current.current_part_id is None
        new_cookie: dict[str, object] = {}
        adapter.login(new_cookie, current)
        assert isinstance(new_cookie.get(CSRF_SESSION_KEY), str)
        resolved = adapter.resolve_session(connection, new_cookie)
        assert resolved.stale is False
        assert resolved.public_user is not None
        assert "password_hash" not in resolved.public_user
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-002")
def test_disable_and_reenable_never_revives_membership_lease_or_old_epoch_jobs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users(
        (_user("admin", role="admin"), _user("leader"), _user("replacement"))
    )
    part_id, space_id = _seed_part(
        factory, leader="leader", replacement="replacement"
    )
    authority = _seed_lease_and_jobs(
        factory,
        actor="leader",
        part_id=part_id,
        space_id=space_id,
    )
    connection = factory.open()
    try:
        original = UserRepository().require(connection, "leader")
    finally:
        connection.close()

    disabled = service.disable_account(
        _command(
            "leader",
            version=original.row_version,
            operation="authority-disable",
            replacement="replacement",
        )
    )
    enabled = service.reenable_account(
        _command(
            "leader",
            version=disabled.row_version,
            operation="authority-reenable",
        )
    )
    assert enabled.scope_epoch == original.scope_epoch + 2
    assert disabled.revoked_lease_count == 1
    assert disabled.invalidated_job_count == 2
    assert disabled.cancel_requested_job_count == 1

    connection = factory.open()
    try:
        current = UserRepository().require(connection, "leader")
        assert current.current_part_id is None
        assert UserRepository().get_open_membership(connection, "leader") is None
        membership = connection.execute(
            "SELECT valid_to FROM org_membership WHERE user_id='leader'"
        ).fetchone()
        assert membership is not None and membership[0] is not None
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (authority["item_id"],),
        ).fetchone()
        assert tuple(lease) == (None, 2)
        jobs = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                """SELECT id,cancellable,cancel_requested,authorization_scope_epoch
                   FROM durable_job WHERE actor_user_id='leader'"""
            ).fetchall()
        }
        assert jobs[authority["cancellable_job_id"]] == (
            1,
            1,
            original.scope_epoch,
        )
        assert jobs[authority["retained_job_id"]] == (
            0,
            0,
            original.scope_epoch,
        )
        assert all(values[2] != current.scope_epoch for values in jobs.values())
    finally:
        connection.close()

@pytest.mark.scenario("AUTH-003")
def test_auth_003_legacy_adapter_blocks_destructive_delete(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    AccountService(factory).import_legacy_users(
        (_user("admin", role="admin"), _user("target"))
    )
    connection = factory.open()
    try:
        with pytest.raises(UserHardDeleteForbiddenError):
            LegacyAccountAdapter().reject_hard_delete(connection, "target")
        assert UserRepository().require(connection, "target").id == "target"
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-002")
def test_legacy_user_parser_preserves_hash_and_first_account_admin_fallback() -> None:
    password_hash = generate_password_hash("legacy-password")
    payload = {
        "users": {
            "owner": {
                "id": "owner",
                "display_name": "Owner",
                "password_hash": password_hash,
                "created_at": 10,
            },
            "member": {
                "id": "member",
                "display_name": "Member",
                "password_hash": generate_password_hash("member-password"),
                "created_at": 11,
            },
        }
    }
    records = legacy_user_records(payload, now_ms=12_000)
    assert records[0].system_role == "admin"
    assert records[1].system_role == "user"
    assert records[0].password_hash == password_hash
    rendered = json.dumps([record.id for record in records])
    assert password_hash not in rendered


@pytest.mark.scenario("AUTH-002")
def test_legacy_default_password_is_imported_as_mandatory_change() -> None:
    password_hash = generate_password_hash("user1234")
    records = legacy_user_records(
        {
            "users": {
                "legacy": {
                    "id": "legacy",
                    "password_hash": password_hash,
                    "created_at": 10,
                }
            }
        },
        now_ms=12_000,
    )
    assert len(records) == 1
    assert records[0].password_hash == password_hash
    assert records[0].must_change_password is True
    assert records[0].password_changed_at_ms is None


@pytest.mark.scenario("AUTH-001")
def test_final_revision_error_is_atomically_bound_and_exactly_replayed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users((_user("admin", role="admin"), _user("target")))
    command = _command("target", version=999, operation="failed-final")

    with pytest.raises(AccountRevisionConflictError) as first:
        service.disable_account(command)
    assert first.value.code == "REVISION_CONFLICT"
    assert first.value.http_status == 409
    assert first.value.details["current_row_version"] == 1

    retry = AccountLifecycleCommand(
        actor_user_id=command.actor_user_id,
        target_user_id=command.target_user_id,
        expected_row_version=command.expected_row_version,
        operation_id=command.operation_id,
        request_id=_uuid("request:failed-final-retry"),
        reason=command.reason,
        request_meta={"remote_addr": "127.0.0.2"},
    )
    with pytest.raises(AccountRevisionConflictError) as replay:
        service.disable_account(retry)
    assert str(replay.value) == str(first.value)
    assert replay.value.details == first.value.details

    changed_payload = AccountLifecycleCommand(
        actor_user_id=command.actor_user_id,
        target_user_id=command.target_user_id,
        expected_row_version=1,
        operation_id=command.operation_id,
        request_id=_uuid("request:failed-final-conflict"),
        reason=command.reason,
    )
    with pytest.raises(AccountIdempotencyError):
        service.disable_account(changed_payload)

    connection = factory.open()
    try:
        assert UserRepository().require(connection, "target").account_status == "active"
        operation = connection.execute(
            "SELECT status,http_status,error_json FROM operation_dedup"
        ).fetchone()
        assert tuple(operation[:2]) == ("failed_final", 409)
        assert json.loads(operation[2])["code"] == "REVISION_CONFLICT"
        assert connection.execute(
            "SELECT outcome FROM audit_event"
        ).fetchone()[0] == "rejected"
        assert connection.execute(
            "SELECT event_type FROM outbox_event"
        ).fetchone()[0] == "account.command_rejected"
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_long_audit_reason_does_not_overflow_role_history(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users(
        (_user("admin", role="admin"), _user("leader"), _user("replacement"))
    )
    _seed_part(factory, leader="leader", replacement="replacement")
    connection = factory.open()
    try:
        leader = UserRepository().require(connection, "leader")
    finally:
        connection.close()
    command = _command(
        "leader",
        version=leader.row_version,
        operation="long-reason",
        replacement="replacement",
    )
    long_reason = "r" * 2000
    result = service.disable_account(
        AccountLifecycleCommand(
            actor_user_id=command.actor_user_id,
            target_user_id=command.target_user_id,
            expected_row_version=command.expected_row_version,
            operation_id=command.operation_id,
            request_id=command.request_id,
            reason=long_reason,
            replacement_leader_user_id=command.replacement_leader_user_id,
        )
    )
    assert result.leader_action == "replaced"
    connection = factory.open()
    try:
        role_reason = connection.execute(
            "SELECT reason FROM org_role_assignment WHERE user_id='replacement'"
        ).fetchone()[0]
        assert len(role_reason) <= 1000
        assert connection.execute(
            "SELECT reason FROM audit_event"
        ).fetchone()[0] == long_reason
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_account_audit_rejects_sensitive_request_metadata(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users((_user("admin", role="admin"), _user("target")))
    connection = factory.open()
    try:
        target = UserRepository().require(connection, "target")
    finally:
        connection.close()
    command = AccountLifecycleCommand(
        actor_user_id="admin",
        target_user_id="target",
        expected_row_version=target.row_version,
        operation_id=_uuid("operation:sensitive-meta"),
        request_id=_uuid("request:sensitive-meta"),
        reason="sensitive metadata must fail before commit",
        request_meta={"Authorization": "Bearer must-not-persist"},
    )
    with pytest.raises(ValueError, match="Sensitive request metadata"):
        service.disable_account(command)
    connection = factory.open()
    try:
        assert UserRepository().require(connection, "target") == target
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("AUTH-001")
def test_account_admin_epoch_and_leader_episode_are_commit_cas_fences(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    service = AccountService(factory)
    service.import_legacy_users(
        (_user("admin", role="admin"), _user("leader"), _user("replacement"))
    )
    part_id, _space_id = _seed_part(
        factory, leader="leader", replacement="replacement"
    )
    connection = factory.open()
    try:
        users = UserRepository()
        actor = users.require(connection, "admin")
        leader = users.require(connection, "leader")
        role = users.get_active_part_leader(connection, part_id)
        part_version = int(
            connection.execute(
                "SELECT row_version FROM org_unit WHERE id=?", (part_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert role is not None

    base = _command(
        "leader",
        version=leader.row_version,
        operation="strict-leader-cas",
        replacement="replacement",
    )
    with pytest.raises(AccountScopeStaleError):
        service.disable_account(
            AccountLifecycleCommand(
                actor_user_id=base.actor_user_id,
                actor_scope_epoch=actor.scope_epoch + 1,
                target_user_id=base.target_user_id,
                expected_row_version=base.expected_row_version,
                operation_id=_uuid("operation:stale-admin-epoch"),
                request_id=_uuid("request:stale-admin-epoch"),
                reason=base.reason,
                replacement_leader_user_id=base.replacement_leader_user_id,
                expected_current_leader_assignment_id=role.id,
                expected_part_row_version=part_version,
            )
        )

    result = service.disable_account(
        AccountLifecycleCommand(
            actor_user_id=base.actor_user_id,
            actor_scope_epoch=actor.scope_epoch,
            target_user_id=base.target_user_id,
            expected_row_version=base.expected_row_version,
            operation_id=base.operation_id,
            request_id=base.request_id,
            reason=base.reason,
            replacement_leader_user_id=base.replacement_leader_user_id,
            expected_current_leader_assignment_id=role.id,
            expected_part_row_version=part_version,
        )
    )
    assert result.leader_action == "replaced"
    connection = factory.open()
    try:
        assert int(
            connection.execute(
                "SELECT row_version FROM org_unit WHERE id=?", (part_id,)
            ).fetchone()[0]
        ) == part_version + 1
    finally:
        connection.close()
