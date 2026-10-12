from __future__ import annotations

from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationCatalog,
    MigrationStorageLayout,
    SingleMigrator,
    V012_EXPECTED_SCHEMA_SHA256,
    V013_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.affiliations import (
    OrgAffiliationRepository,
    OrgAffiliationStateConflictError,
)
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    HigherRoleStateConflictError,
    HigherSpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("50a52dbb-97ec-4a2d-9c2c-364582a0819a")
_V12_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:12])


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _migrate(value, catalog: MigrationCatalog):
    factory = ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)
    result = SingleMigrator(
        factory,
        app_version="v13-direct-org-affiliation-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _user(user_id: str, *, admin: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.replace("_", " ").title(),
        password_hash=f"scrypt:v13:{user_id}",
        system_role="admin" if admin else "user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed_v12(factory: ConnectionFactory, *, legacy_role: bool = False) -> dict[str, str]:
    ids = {
        "team": _uuid("org:team"),
        "part": _uuid("org:part"),
        "part_space": _uuid("space:part"),
        "membership": _uuid("membership:member"),
        "legacy_membership": _uuid("membership:legacy-leader"),
        "direct_role": _uuid("role:direct-team-leader"),
        "legacy_role": _uuid("role:legacy-team-leader"),
    }
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _user("admin", admin=True),
                    _user("member"),
                    _user("team_leader"),
                    _user("legacy_leader"),
                ),
            )
            for user_id in ("admin", "member", "team_leader", "legacy_leader"):
                spaces.ensure_personal(
                    connection,
                    space_id=_uuid(f"space:personal:{user_id}"),
                    user_id=user_id,
                    created_at_ms=200,
                )
            organization.create_unit(
                connection,
                unit_id=ids["team"],
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Direct Team",
                actor_user_id="admin",
                now_ms=1000,
            )
            organization.create_unit(
                connection,
                unit_id=ids["part"],
                kind="part",
                parent_id=ids["team"],
                name="Ordinary Part",
                actor_user_id="admin",
                now_ms=1001,
            )
            spaces.ensure_higher(
                connection,
                space_id=ids["team"],
                org_unit_id=ids["team"],
                kind="team",
                created_at_ms=1050,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part_space"],
                part_id=ids["part"],
                created_at_ms=1051,
            )
            membership_rows = (("member", ids["membership"]),) + (
                (("legacy_leader", ids["legacy_membership"]),)
                if legacy_role
                else ()
            )
            for user_id, membership_id in membership_rows:
                state = organization.get_user_organization_state(connection, user_id)
                assert state is not None
                organization.open_membership(
                    connection,
                    membership_id=membership_id,
                    user_id=user_id,
                    part_id=ids["part"],
                    assigned_by="admin",
                    reason="v13 upgrade fixture",
                    valid_from_ms=1100,
                )
                organization.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=ids["part"],
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=1100,
                )
            if legacy_role:
                HigherSpaceRepository().insert_open_role(
                    connection,
                    assignment_id=ids["legacy_role"],
                    org_unit_id=ids["team"],
                    user_id="legacy_leader",
                    role="team_leader",
                    assigned_by_user_id="admin",
                    assignment_generation=1,
                    assigned_at_ms=1200,
                )
    finally:
        connection.close()
    return ids


def test_v12_upgrade_backfills_part_and_enforces_direct_team_home(
    tmp_path, isolated_knowledge_layout
) -> None:
    v12_factory, initial = _migrate(isolated_knowledge_layout, _V12_CATALOG)
    assert initial.applied_versions == tuple(range(1, 13))
    ids = _seed_v12(v12_factory)

    upgraded, result = _migrate(isolated_knowledge_layout, CANONICAL_MIGRATION_CATALOG)
    assert result.applied_versions == (13,)
    affiliations = OrgAffiliationRepository()
    higher = HigherSpaceRepository()
    connection = upgraded.open()
    try:
        with transaction(connection):
            member_home = affiliations.get_current(connection, "member")
            assert member_home is not None
            assert member_home.id == ids["membership"]
            assert member_home.home_kind == "part"
            assert member_home.org_unit_id == ids["part"]

            opened = affiliations.open_direct(
                connection,
                affiliation_id=ids["direct_role"],
                user_id="team_leader",
                org_unit_id=ids["team"],
                home_kind="team",
                assigned_by_user_id="admin",
                reason="Direct Team leader home",
                valid_from_ms=2000,
            )
            affiliations.bump_direct_scope(
                connection,
                user_id="team_leader",
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=2001,
            )
            role = higher.insert_open_role(
                connection,
                assignment_id=ids["direct_role"],
                org_unit_id=ids["team"],
                user_id="team_leader",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=2002,
            )
            assert role.id == opened.id

            with pytest.raises(OrgAffiliationStateConflictError):
                affiliations.open_direct(
                    connection,
                    affiliation_id=_uuid("role:duplicate-home"),
                    user_id="team_leader",
                    org_unit_id=RESPONSIBILITY_ROOT_ID,
                    home_kind="responsibility",
                    assigned_by_user_id="admin",
                    reason="Invalid second home",
                    valid_from_ms=2003,
                )
            with pytest.raises(HigherRoleStateConflictError):
                higher.insert_open_role(
                    connection,
                    assignment_id=_uuid("role:part-user-team-lead"),
                    org_unit_id=ids["team"],
                    user_id="member",
                    role="team_leader",
                    assigned_by_user_id="admin",
                    assignment_generation=2,
                    assigned_at_ms=2004,
                )
        with transaction(connection):
            higher.close_role(
                connection,
                assignment_id=ids["direct_role"],
                expected_generation=1,
                expected_row_version=1,
                ended_at_ms=2100,
                end_reason="Direct role test completed",
            )
            affiliations.close_direct(
                connection,
                affiliation_id=ids["direct_role"],
                user_id="team_leader",
                closed_at_ms=2100,
            )
            user = UserRepository().require(connection, "team_leader")
            affiliations.bump_direct_scope(
                connection,
                user_id="team_leader",
                expected_scope_epoch=user.scope_epoch,
                expected_row_version=user.row_version,
                updated_at_ms=2101,
            )
        assert schema_sha256(connection) == V013_EXPECTED_SCHEMA_SHA256
        verify_canonical_integrity(connection, CANONICAL_MIGRATION_CATALOG)
    finally:
        connection.close()

    pristine = create_isolated_test_layout(tmp_path / "v13-pristine", user_id="v13")
    pristine_factory, pristine_result = _migrate(
        pristine, CANONICAL_MIGRATION_CATALOG
    )
    assert pristine_result.applied_versions == tuple(range(1, 14))
    pristine_connection = pristine_factory.open(read_only=True)
    try:
        assert schema_sha256(pristine_connection) == V013_EXPECTED_SCHEMA_SHA256
    finally:
        pristine_connection.close()


def test_v13_refuses_silent_conversion_of_an_open_legacy_part_anchored_role(
    tmp_path,
) -> None:
    layout = create_isolated_test_layout(tmp_path / "v13-refusal", user_id="v13")
    v12_factory, _ = _migrate(layout, _V12_CATALOG)
    ids = _seed_v12(v12_factory, legacy_role=True)

    with pytest.raises(KnowledgeCoreMigrationError):
        _migrate(layout, CANONICAL_MIGRATION_CATALOG)

    connection = v12_factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == V012_EXPECTED_SCHEMA_SHA256
        assert connection.execute(
            "SELECT max(version) FROM schema_migration"
        ).fetchone()[0] == 12
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='org_home_affiliation'"
        ).fetchone()[0] == 0
        row = connection.execute(
            "SELECT user_id,ended_at FROM higher_scope_role_assignment WHERE id=?",
            (ids["legacy_role"],),
        ).fetchone()
        assert tuple(row) == ("legacy_leader", None)
    finally:
        connection.close()
