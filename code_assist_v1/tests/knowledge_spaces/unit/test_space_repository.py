from __future__ import annotations

import sqlite3
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
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import (
    SpaceIdentityConflictError,
    SpaceNotFoundError,
    SpaceOwnerNotFoundError,
    SpaceOwnerStateError,
    SpaceProvisioningReport,
    SpaceRepository,
    SpaceStateConflictError,
    SpaceVersionConflictError,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit
_FIXTURE_NAMESPACE = UUID("d8e7ada8-92eb-40d8-bd07-99a76e1b723f")


def _uuid(label: str) -> str:
    return str(uuid5(_FIXTURE_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.6-space-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, disabled: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.6:{user_id}",
        system_role="admin" if user_id == "admin" else "user",
        account_status="disabled" if disabled else "active",
        created_at_ms=100,
        updated_at_ms=200 if disabled else 100,
        disabled_at_ms=200 if disabled else None,
    )


def _seed_users(
    factory: ConnectionFactory, *records: LegacyUserRecord
) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy_many(connection, records)
    finally:
        connection.close()


def _create_team_and_parts(
    connection,
    *,
    suffix: str = "a",
    part_count: int = 2,
) -> tuple[str, tuple[str, ...]]:
    organization = OrganizationRepository()
    team_id = _uuid(f"team:{suffix}")
    organization.create_unit(
        connection,
        unit_id=team_id,
        kind="team",
        parent_id=RESPONSIBILITY_ROOT_ID,
        name=f"Team {suffix.upper()}",
        actor_user_id="admin",
        now_ms=1000,
    )
    part_ids = tuple(_uuid(f"part:{suffix}:{index}") for index in range(part_count))
    for index, part_id in enumerate(part_ids):
        organization.create_unit(
            connection,
            unit_id=part_id,
            kind="part",
            parent_id=team_id,
            name=f"Part {suffix.upper()} {index}",
            actor_user_id="admin",
            now_ms=1001 + index,
        )
    return team_id, part_ids


def _assert_initial(space, *, kind: str, now_ms: int) -> None:
    assert space.space_kind == kind
    assert space.status == "active"
    assert space.write_enabled is False
    assert space.generation == 0
    assert space.created_at_ms == now_ms
    assert space.updated_at_ms == now_ms
    assert space.row_version == 1


def test_personal_ensure_creates_exact_initial_row_and_all_read_shapes(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("admin"), _user("member"))
    repository = SpaceRepository()
    personal_id = _uuid("space:personal:member")
    connection = factory.open()
    try:
        with transaction(connection):
            result = repository.ensure_personal(
                connection,
                space_id=personal_id,
                user_id="member",
                created_at_ms=2000,
            )
            assert result.created is True
            _assert_initial(result.space, kind="personal", now_ms=2000)
            assert result.space.owner_user_id == "member"
            assert result.space.owner_org_unit_id is None

        assert repository.get_by_id(connection, personal_id) == result.space
        assert repository.get_personal(connection, "member") == result.space
        assert repository.get_org(connection, _uuid("not-an-owner")) is None
        assert repository.get_part(connection, _uuid("not-a-part")) is None
        assert repository.require_by_id(connection, personal_id) == result.space
        with pytest.raises(SpaceNotFoundError):
            repository.require_by_id(connection, _uuid("missing-space"))
    finally:
        connection.close()


def test_personal_ensure_accepts_disabled_canonical_owner(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("disabled01", disabled=True))
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            result = repository.ensure_personal(
                connection,
                space_id=_uuid("space:personal:disabled01"),
                user_id="disabled01",
                created_at_ms=500,
            )
        assert result.created is True
        assert result.space.owner_user_id == "disabled01"
    finally:
        connection.close()


def test_personal_ensure_is_idempotent_and_never_mutates_existing_row(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("member"))
    repository = SpaceRepository()
    first_id = _uuid("space:personal:first")
    connection = factory.open()
    try:
        with transaction(connection):
            first = repository.ensure_personal(
                connection,
                space_id=first_id,
                user_id="member",
                created_at_ms=1000,
            )
            connection.execute(
                """UPDATE knowledge_space
                   SET write_enabled=1,updated_at=1100,row_version=row_version+1
                   WHERE id=?""",
                (first.space.id,),
            )
            changed = repository.require_by_id(connection, first.space.id)
            replay = repository.ensure_personal(
                connection,
                space_id=_uuid("space:personal:unused-candidate"),
                user_id="member",
                created_at_ms=9999,
            )
        assert replay.created is False
        assert replay.space == changed
        assert replay.space.id == first_id
        assert replay.space.write_enabled is True
        assert replay.space.updated_at_ms == 1100
        assert replay.space.row_version == 2
    finally:
        connection.close()


def test_personal_ensure_rejects_missing_owner_and_candidate_id_collision(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("owner1"), _user("owner2"))
    repository = SpaceRepository()
    shared_id = _uuid("space:collision")
    connection = factory.open()
    try:
        with transaction(connection):
            repository.ensure_personal(
                connection,
                space_id=shared_id,
                user_id="owner1",
                created_at_ms=1000,
            )
            with pytest.raises(SpaceIdentityConflictError) as collision:
                repository.ensure_personal(
                    connection,
                    space_id=shared_id,
                    user_id="owner2",
                    created_at_ms=1001,
                )
            assert collision.value.code == "SPACE_IDENTITY_CONFLICT"
            with pytest.raises(SpaceOwnerNotFoundError):
                repository.ensure_personal(
                    connection,
                    space_id=_uuid("space:missing-owner"),
                    user_id="missing",
                    created_at_ms=1002,
                )
        assert repository.get_personal(connection, "owner2") is None
    finally:
        connection.close()


@pytest.mark.scenario("SPACE-005")
def test_space_005_part_ensure_creates_only_for_active_actual_part(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("admin"))
    repository = SpaceRepository()
    organization = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            team_id, (active_part_id, inactive_part_id) = _create_team_and_parts(
                connection
            )
            inactive = organization.require_unit(connection, inactive_part_id)
            organization.set_unit_status(
                connection,
                unit_id=inactive_part_id,
                expected_row_version=inactive.row_version,
                status="inactive",
                actor_user_id="admin",
                updated_at_ms=1500,
            )
            result = repository.ensure_part(
                connection,
                space_id=_uuid("space:part:active"),
                part_id=active_part_id,
                created_at_ms=2000,
            )
            assert result.created is True
            _assert_initial(result.space, kind="part", now_ms=2000)
            assert result.space.owner_user_id is None
            assert result.space.owner_org_unit_id == active_part_id

            for invalid_owner in (team_id, RESPONSIBILITY_ROOT_ID, inactive_part_id):
                with pytest.raises(SpaceOwnerStateError):
                    repository.ensure_part(
                        connection,
                        space_id=_uuid(f"space:invalid:{invalid_owner}"),
                        part_id=invalid_owner,
                        created_at_ms=2001,
                    )
            with pytest.raises(SpaceOwnerNotFoundError):
                repository.ensure_part(
                    connection,
                    space_id=_uuid("space:part:missing-owner"),
                    part_id=_uuid("part:missing"),
                    created_at_ms=2002,
                )

        assert repository.get_org(connection, active_part_id) == result.space
        assert repository.get_part(connection, active_part_id) == result.space
        assert repository.get_part(connection, team_id) is None
        kinds = tuple(
            row[0]
            for row in connection.execute(
                "SELECT space_kind FROM knowledge_space ORDER BY space_kind,id"
            ).fetchall()
        )
        # v9 physically provisions the active Responsibility root even while
        # successor higher-space traffic remains disabled.
        assert kinds == ("part", "responsibility")
    finally:
        connection.close()


def test_part_ensure_is_idempotent_and_owner_collision_fails_closed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("admin"))
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _team, (part_a, part_b) = _create_team_and_parts(connection)
            stable_id = _uuid("space:part:stable")
            first = repository.ensure_part(
                connection,
                space_id=stable_id,
                part_id=part_a,
                created_at_ms=2000,
            )
            replay = repository.ensure_part(
                connection,
                space_id=_uuid("space:part:unused-candidate"),
                part_id=part_a,
                created_at_ms=3000,
            )
            assert replay == type(replay)(space=first.space, created=False)
            with pytest.raises(SpaceIdentityConflictError):
                repository.ensure_part(
                    connection,
                    space_id=stable_id,
                    part_id=part_b,
                    created_at_ms=3001,
                )
        assert repository.get_part(connection, part_b) is None
    finally:
        connection.close()


def test_missing_owner_lists_are_stable_bounded_and_include_disabled_users(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(
        factory,
        _user("admin"),
        _user("active01"),
        _user("disabled01", disabled=True),
    )
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _team, (part_a, part_b) = _create_team_and_parts(connection)
            repository.ensure_personal(
                connection,
                space_id=_uuid("space:personal:active01"),
                user_id="active01",
                created_at_ms=2000,
            )
            repository.ensure_part(
                connection,
                space_id=_uuid("space:part:a"),
                part_id=part_a,
                created_at_ms=2000,
            )

        assert repository.list_missing_personal_owner_ids(connection) == (
            "admin",
            "disabled01",
        )
        assert repository.list_missing_personal_owner_ids(connection, limit=1) == (
            "admin",
        )
        assert repository.list_missing_part_owner_ids(connection) == (part_b,)
        assert repository.list_missing_part_owner_ids(connection, limit=1) == (
            part_b,
        )
    finally:
        connection.close()


def test_provisioning_report_counts_missing_and_future_spaces_without_exposure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("admin"), _user("member"))
    repository = SpaceRepository()
    organization = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            team_id, (part_a, part_b, inactive_part) = _create_team_and_parts(
                connection, part_count=3
            )
            repository.ensure_personal(
                connection,
                space_id=_uuid("space:personal:admin"),
                user_id="admin",
                created_at_ms=2000,
            )
            repository.ensure_part(
                connection,
                space_id=_uuid("space:part:a"),
                part_id=part_a,
                created_at_ms=2000,
            )
            inactive_space = repository.ensure_part(
                connection,
                space_id=_uuid("space:part:inactive-owner"),
                part_id=inactive_part,
                created_at_ms=2000,
            ).space
            repository.update_state(
                connection,
                space_id=inactive_space.id,
                expected_row_version=inactive_space.row_version,
                status="frozen",
                write_enabled=False,
                updated_at_ms=2001,
            )
            inactive_unit = organization.require_unit(connection, inactive_part)
            organization.set_unit_status(
                connection,
                unit_id=inactive_part,
                expected_row_version=inactive_unit.row_version,
                status="inactive",
                actor_user_id="admin",
                updated_at_ms=2002,
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,2000,2000,1)""",
                (_uuid("space:future:team"), team_id),
            )

        report = repository.provisioning_report(connection)
        assert report == SpaceProvisioningReport(
            total_user_count=2,
            personal_space_count=1,
            missing_personal_count=1,
            active_part_count=2,
            active_part_space_count=1,
            total_part_space_count=2,
            missing_part_count=1,
            higher_space_count=2,
        )
        assert report.complete is False
        assert repository.get_org(connection, team_id).space_kind == "team"  # type: ignore[union-attr]
        assert repository.get_part(connection, team_id) is None
        assert repository.list_missing_part_owner_ids(connection) == (part_b,)
    finally:
        connection.close()


def test_state_update_is_versioned_noop_safe_and_owner_identity_immutable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("member"))
    repository = SpaceRepository()
    space_id = _uuid("space:state-update")
    connection = factory.open()
    try:
        with transaction(connection):
            created = repository.ensure_personal(
                connection,
                space_id=space_id,
                user_id="member",
                created_at_ms=1000,
            ).space
            no_op = repository.update_state(
                connection,
                space_id=space_id,
                expected_row_version=1,
                status="active",
                write_enabled=False,
                updated_at_ms=2000,
            )
            assert no_op == created
            updated = repository.update_state(
                connection,
                space_id=space_id,
                expected_row_version=1,
                status="active",
                write_enabled=True,
                updated_at_ms=2000,
            )
            assert updated.owner_user_id == "member"
            assert updated.owner_org_unit_id is None
            assert updated.space_kind == "personal"
            assert updated.write_enabled is True
            assert updated.updated_at_ms == 2000
            assert updated.row_version == 2
            with pytest.raises(SpaceVersionConflictError) as stale:
                repository.update_state(
                    connection,
                    space_id=space_id,
                    expected_row_version=1,
                    status="frozen",
                    write_enabled=False,
                    updated_at_ms=2001,
                )
            assert stale.value.code == "SPACE_VERSION_CONFLICT"
        assert repository.require_by_id(connection, space_id) == updated
    finally:
        connection.close()


def test_state_update_rejects_future_space_write_and_invalid_inputs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("admin"))
    repository = SpaceRepository()
    future_space_id = _uuid("space:future:update")
    connection = factory.open()
    try:
        with transaction(connection):
            team_id, _parts = _create_team_and_parts(connection, part_count=1)
            part_space = repository.ensure_part(
                connection,
                space_id=_uuid("space:leaderless-part"),
                part_id=_parts[0],
                created_at_ms=1999,
            ).space
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,2000,2000,1)""",
                (future_space_id, team_id),
            )
            with pytest.raises(SpaceStateConflictError) as disabled:
                repository.update_state(
                    connection,
                    space_id=future_space_id,
                    expected_row_version=1,
                    status="active",
                    write_enabled=True,
                    updated_at_ms=2001,
                )
            assert disabled.value.code == "SPACE_STATE_CONFLICT"
            with pytest.raises(SpaceStateConflictError):
                repository.update_state(
                    connection,
                    space_id=part_space.id,
                    expected_row_version=part_space.row_version,
                    status="active",
                    write_enabled=True,
                    updated_at_ms=2001,
                )
            with pytest.raises(ValueError, match="positive integer"):
                repository.update_state(
                    connection,
                    space_id=future_space_id,
                    expected_row_version=True,  # type: ignore[arg-type]
                    status="active",
                    write_enabled=False,
                    updated_at_ms=2001,
                )
            with pytest.raises(ValueError, match="Boolean"):
                repository.update_state(
                    connection,
                    space_id=future_space_id,
                    expected_row_version=1,
                    status="active",
                    write_enabled=1,  # type: ignore[arg-type]
                    updated_at_ms=2001,
                )
            with pytest.raises(ValueError, match="active, frozen, or inactive"):
                repository.update_state(
                    connection,
                    space_id=future_space_id,
                    expected_row_version=1,
                    status="future",  # type: ignore[arg-type]
                    write_enabled=False,
                    updated_at_ms=2001,
                )
        assert repository.require_by_id(connection, future_space_id).row_version == 1
    finally:
        connection.close()


def test_ensure_methods_require_existing_managed_write_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("member"))
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            repository.ensure_personal(
                connection,
                space_id=_uuid("space:no-transaction"),
                user_id="member",
                created_at_ms=1,
            )
        with pytest.raises(KnowledgeCoreTransactionError):
            repository.update_state(
                connection,
                space_id=_uuid("space:no-transaction"),
                expected_row_version=1,
                status="active",
                write_enabled=False,
                updated_at_ms=1,
            )
        raw = sqlite3.connect(":memory:")
        try:
            with pytest.raises(TypeError, match="ManagedConnection"):
                repository.get_by_id(raw, _uuid("space:raw"))  # type: ignore[arg-type]
            with pytest.raises(TypeError, match="ManagedConnection"):
                repository.ensure_personal(  # type: ignore[arg-type]
                    raw,
                    space_id=_uuid("space:raw-write"),
                    user_id="member",
                    created_at_ms=1,
                )
        finally:
            raw.close()
    finally:
        connection.close()


@pytest.mark.parametrize("bad_uuid", [True, 1, "A" * 36, "not-a-uuid", None])
def test_uuid_inputs_are_strict(
    isolated_knowledge_layout,
    bad_uuid,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match="canonical lowercase UUID"):
            repository.get_by_id(connection, bad_uuid)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="canonical lowercase UUID"):
            repository.get_org(connection, bad_uuid)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="canonical lowercase UUID"):
            repository.get_part(connection, bad_uuid)  # type: ignore[arg-type]
    finally:
        connection.close()


@pytest.mark.parametrize("bad_user", [True, 1, "UPPER", " bad", "", None])
def test_user_ids_are_strict(
    isolated_knowledge_layout,
    bad_user,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match="canonical lowercase login"):
            repository.get_personal(connection, bad_user)  # type: ignore[arg-type]
    finally:
        connection.close()


@pytest.mark.parametrize("bad_timestamp", [True, False, -1, 1.5, "1", None])
def test_ensure_timestamp_is_strict(
    isolated_knowledge_layout,
    bad_timestamp,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("member"))
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(ValueError, match="UTC millisecond integer"):
                repository.ensure_personal(
                    connection,
                    space_id=_uuid(f"space:bad-time:{bad_timestamp!r}"),
                    user_id="member",
                    created_at_ms=bad_timestamp,  # type: ignore[arg-type]
                )
    finally:
        connection.close()


@pytest.mark.parametrize("bad_limit", [True, False, 0, -1, 1.5, "1"])
def test_missing_list_limit_is_strict(
    isolated_knowledge_layout,
    bad_limit,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = SpaceRepository()
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match="positive integer"):
            repository.list_missing_personal_owner_ids(  # type: ignore[arg-type]
                connection, limit=bad_limit
            )
        with pytest.raises(ValueError, match="positive integer"):
            repository.list_missing_part_owner_ids(  # type: ignore[arg-type]
                connection, limit=bad_limit
            )
    finally:
        connection.close()


def test_transaction_rollback_leaves_space_missing(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_users(factory, _user("member"))
    repository = SpaceRepository()
    space_id = _uuid("space:rollback")
    connection = factory.open()
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction(connection):
                repository.ensure_personal(
                    connection,
                    space_id=space_id,
                    user_id="member",
                    created_at_ms=1000,
                )
                raise RuntimeError("rollback")
        assert repository.get_by_id(connection, space_id) is None
        assert repository.get_personal(connection, "member") is None
    finally:
        connection.close()
