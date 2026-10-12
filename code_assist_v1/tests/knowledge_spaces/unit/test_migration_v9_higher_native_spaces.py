from __future__ import annotations

import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SCHEMA_V5_MIGRATION,
    SCHEMA_V6_MIGRATION,
    SCHEMA_V7_MIGRATION,
    SCHEMA_V8_MIGRATION,
    SCHEMA_V9_MIGRATION,
    SingleMigrator,
    V009_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.higher_spaces import (
    HigherRoleStateConflictError,
    HigherSpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("63880257-5f08-4248-9c89-70aa2f179de0")
_V8_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
        SCHEMA_V8_MIGRATION,
    )
)
_V9_CATALOG = MigrationCatalog((*_V8_CATALOG.migrations, SCHEMA_V9_MIGRATION))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _migrate(value, catalog=_V9_CATALOG):
    factory = ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)
    result = SingleMigrator(
        factory,
        app_version="sbe-1.2-v9-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _legacy_user(user_id: str, *, admin: bool = False) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:sbe-1.2:{user_id}",
        system_role="admin" if admin else "user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _insert_user_with_personal(
    connection,
    *,
    user_id: str,
    part_id: str | None = None,
    admin: bool = False,
) -> str:
    UserRepository().import_legacy(connection, _legacy_user(user_id, admin=admin))
    personal_id = _uuid(f"personal:{user_id}")
    SpaceRepository().ensure_personal(
        connection,
        space_id=personal_id,
        user_id=user_id,
        created_at_ms=110,
    )
    if part_id is not None:
        connection.execute(
            """INSERT INTO org_membership(
                 id,user_id,part_id,valid_from,valid_to,assigned_by,reason
               ) VALUES (?,?,?,120,NULL,'admin','v9 fixture membership')""",
            (_uuid(f"membership:{user_id}"), user_id, part_id),
        )
        connection.execute(
            """UPDATE app_user
                  SET current_part_id=?,scope_epoch=scope_epoch+1,
                      updated_at=120,row_version=row_version+1
                WHERE id=?""",
            (part_id, user_id),
        )
    return personal_id


def _seed_v8_hierarchy(factory: ConnectionFactory) -> dict[str, str]:
    ids = {
        "root": RESPONSIBILITY_ROOT_ID,
        "team": _uuid("org:team"),
        "part": _uuid("org:part"),
        "team2": _uuid("org:team2"),
        "part2": _uuid("org:part2"),
        "team_space": _uuid("space:team:preserved"),
        "part_space": _uuid("space:part:preserved"),
        "part_leader_episode": _uuid("part-leader:episode"),
    }
    connection = factory.open()
    try:
        with transaction(connection):
            for unit_id, kind, parent, name in (
                (ids["team"], "team", ids["root"], "Team One"),
                (ids["part"], "part", ids["team"], "Part One"),
                (ids["team2"], "team", ids["root"], "Team Two"),
                (ids["part2"], "part", ids["team2"], "Part Two"),
            ):
                connection.execute(
                    """INSERT INTO org_unit(
                         id,kind,parent_id,name,name_key,status,created_at,
                         updated_at,created_by,updated_by,row_version
                       ) VALUES (?,?,?,?,?,'active',100,100,NULL,NULL,1)""",
                    (unit_id, kind, parent, name, name.casefold()),
                )
            _insert_user_with_personal(connection, user_id="admin", admin=True)
            _insert_user_with_personal(
                connection, user_id="leader", part_id=ids["part"]
            )
            _insert_user_with_personal(
                connection, user_id="replacement", part_id=ids["part"]
            )
            _insert_user_with_personal(
                connection, user_id="foreign", part_id=ids["part2"]
            )
            _insert_user_with_personal(connection, user_id="inactive")
            connection.execute(
                """UPDATE app_user
                      SET account_status='disabled',disabled_at=130,
                          scope_epoch=scope_epoch+1,updated_at=130,
                          row_version=row_version+1
                    WHERE id='inactive'"""
            )
            connection.execute(
                """INSERT INTO org_role_assignment(
                     id,user_id,org_unit_id,role,valid_from,valid_to,
                     assigned_by,reason
                   ) VALUES (?,'leader',?,'part_leader',140,NULL,'admin',
                             'preserved v1 part leader')""",
                (ids["part_leader_episode"], ids["part"]),
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,150,150,1)""",
                (ids["team_space"], ids["team"]),
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',1,1,150,150,1)""",
                (ids["part_space"], ids["part"]),
            )
    finally:
        connection.close()
    return ids


@pytest.mark.scenario("HSPACE-001")
@pytest.mark.scenario("HSPACE-002")
def test_v8_to_v9_preserves_ids_provisions_every_native_space_and_matches_pristine(
    isolated_knowledge_layout,
    tmp_path,
) -> None:
    v8_factory, first = _migrate(isolated_knowledge_layout, _V8_CATALOG)
    assert first.applied_versions == tuple(range(1, 9))
    ids = _seed_v8_hierarchy(v8_factory)
    before = {}
    connection = v8_factory.open()
    try:
        for name, sql in {
            "users": "SELECT id,current_part_id,scope_epoch FROM app_user ORDER BY id",
            "org": "SELECT id,kind,parent_id,status,row_version FROM org_unit ORDER BY id",
            "spaces": "SELECT id,space_kind,owner_org_unit_id,write_enabled FROM knowledge_space ORDER BY id",
            "part_roles": "SELECT id,user_id,org_unit_id,valid_from,valid_to FROM org_role_assignment ORDER BY id",
        }.items():
            before[name] = [tuple(row) for row in connection.execute(sql).fetchall()]
    finally:
        connection.close()

    factory, upgraded = _migrate(isolated_knowledge_layout)
    assert upgraded.applied_versions == (9,)
    assert upgraded.schema_version == 9
    connection = factory.open()
    try:
        assert schema_sha256(connection) == V009_EXPECTED_SCHEMA_SHA256
        assert before["users"] == [
            tuple(row)
            for row in connection.execute(
                "SELECT id,current_part_id,scope_epoch FROM app_user ORDER BY id"
            ).fetchall()
        ]
        assert before["org"] == [
            tuple(row)
            for row in connection.execute(
                "SELECT id,kind,parent_id,status,row_version FROM org_unit ORDER BY id"
            ).fetchall()
        ]
        assert before["part_roles"] == [
            tuple(row)
            for row in connection.execute(
                "SELECT id,user_id,org_unit_id,valid_from,valid_to FROM org_role_assignment ORDER BY id"
            ).fetchall()
        ]
        assert connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_org_unit_id=?",
            (ids["team"],),
        ).fetchone()[0] == ids["team_space"]
        assert tuple(connection.execute(
            "SELECT id,write_enabled FROM knowledge_space WHERE owner_org_unit_id=?",
            (ids["part"],),
        ).fetchone()) == (ids["part_space"], 1)
        assert connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_org_unit_id=?",
            (ids["root"],),
        ).fetchone()[0] == ids["root"]
        assert tuple(
            connection.execute(
                "SELECT created_at,updated_at FROM knowledge_space WHERE id=?",
                (ids["root"],),
            ).fetchone()
        ) == (0, 0)
        assert connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_org_unit_id=?",
            (ids["team2"],),
        ).fetchone()[0] == ids["team2"]
        assert connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_org_unit_id=?",
            (ids["part2"],),
        ).fetchone()[0] == ids["part2"]
        assert connection.execute(
            """SELECT count(*) FROM org_unit o
                 JOIN knowledge_space s ON s.owner_org_unit_id=o.id
                WHERE o.status='active'"""
        ).fetchone()[0] == 5
        assert tuple(connection.execute(
            "SELECT count(*) FROM higher_scope_role_assignment"
        ).fetchone()) == (0,)
        verify_canonical_integrity(connection, _V9_CATALOG)
    finally:
        connection.close()

    pristine_layout = create_isolated_test_layout(
        tmp_path / "pristine-v9", user_id="pristine-v9"
    )
    pristine_factory, pristine = _migrate(pristine_layout)
    assert pristine.applied_versions == tuple(range(1, 10))
    pristine_connection = pristine_factory.open()
    try:
        assert schema_sha256(pristine_connection) == V009_EXPECTED_SCHEMA_SHA256
        assert tuple(pristine_connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE owner_org_unit_id=?",
            (RESPONSIBILITY_ROOT_ID,),
        ).fetchone()) == (1,)
        assert tuple(
            pristine_connection.execute(
                "SELECT created_at,updated_at FROM knowledge_space WHERE id=?",
                (RESPONSIBILITY_ROOT_ID,),
            ).fetchone()
        ) == (0, 0)
    finally:
        pristine_connection.close()

    _same_factory, repeated = _migrate(isolated_knowledge_layout)
    assert repeated.applied_versions == ()
    assert repeated.schema_version == 9


@pytest.mark.scenario("HSPACE-002")
def test_failed_v9_parent_rebuild_rolls_back_exactly_to_v8(
    isolated_knowledge_layout,
) -> None:
    v8_factory, _ = _migrate(isolated_knowledge_layout, _V8_CATALOG)
    connection = v8_factory.open()
    try:
        with transaction(connection):
            personal_id = _insert_user_with_personal(
                connection, user_id="collision-owner"
            )
            connection.execute(
                """INSERT INTO org_unit(
                     id,kind,parent_id,name,name_key,status,created_at,updated_at,
                     created_by,updated_by,row_version
                   ) VALUES (?,'team',?,'Collision Team','collision team',
                             'active',200,200,NULL,NULL,1)""",
                (personal_id, RESPONSIBILITY_ROOT_ID),
            )
    finally:
        connection.close()

    with pytest.raises(KnowledgeCoreMigrationError):
        _migrate(isolated_knowledge_layout)

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (8,)
        assert raw.execute(
            "SELECT count(*) FROM schema_migration WHERE version=9"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='higher_scope_role_assignment'"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='knowledge_distribution_grant'"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='trg_knowledge_space_kind_disabled_insert'"
        ).fetchone() == (1,)
        assert "space_kind IN ('personal','part') OR write_enabled=0" in raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='knowledge_space'"
        ).fetchone()[0]
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        raw.close()


@pytest.mark.scenario("HSPACE-001")
def test_v9_schema_has_exact_higher_distribution_objects_and_foreign_keys(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        assert tuple(connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()) == (57,)
        assert tuple(connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()) == (152,)
        assert tuple(connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()) == (165,)
        assert {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_schema WHERE type='index'
                     AND (name LIKE '%higher_role%' OR name LIKE '%distribution%')
                     AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        } == {
            "ux_higher_role_open_unit_role",
            "ux_higher_role_unit_generation",
            "ix_higher_role_user_open",
            "ix_higher_role_unit_history",
            "ux_distribution_effect_key",
            "ux_distribution_revoke_effect_key",
            "ux_distribution_active_revision_target",
            "ix_distribution_source_history",
            "ix_distribution_target_active",
        }
        distribution_sql = connection.execute(
            """SELECT sql FROM sqlite_schema
                 WHERE type='table' AND name='knowledge_distribution_grant'"""
        ).fetchone()[0]
        assert "REFERENCES knowledge_space(id)" in distribution_sql
        assert "REFERENCES knowledge_item(id,space_id)" in distribution_sql
        assert "REFERENCES knowledge_revision(id,item_id)" in distribution_sql
        assert "REFERENCES higher_scope_role_assignment(id)" in distribution_sql
        verify_canonical_integrity(connection, _V9_CATALOG)
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-003")
@pytest.mark.scenario("HSPACE-004")
@pytest.mark.scenario("HSPACE-005")
def test_higher_role_kind_ancestry_uniqueness_generation_and_write_fences(
    isolated_knowledge_layout,
) -> None:
    v8_factory, _ = _migrate(isolated_knowledge_layout, _V8_CATALOG)
    ids = _seed_v8_hierarchy(v8_factory)
    factory, _ = _migrate(isolated_knowledge_layout)
    repository = HigherSpaceRepository()
    team_episode_1 = _uuid("higher:team:1")
    responsibility_episode = _uuid("higher:responsibility:1")
    connection = factory.open()
    try:
        with transaction(connection):
            opened = repository.insert_open_role(
                connection,
                assignment_id=team_episode_1,
                org_unit_id=ids["team"],
                user_id="leader",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=300,
            )
            assert opened.assignment_generation == 1
            second = repository.insert_open_role(
                connection,
                assignment_id=responsibility_episode,
                org_unit_id=ids["root"],
                user_id="leader",
                role="responsibility_executive",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=301,
            )
            assert second.role == "responsibility_executive"

        for label, unit, user, role in (
            ("wrong-kind", ids["team"], "leader", "responsibility_executive"),
            ("foreign", ids["team"], "foreign", "team_leader"),
            ("inactive", ids["team2"], "inactive", "team_leader"),
        ):
            with pytest.raises(HigherRoleStateConflictError):
                with transaction(connection):
                    repository.insert_open_role(
                        connection,
                        assignment_id=_uuid(f"higher:invalid:{label}"),
                        org_unit_id=unit,
                        user_id=user,
                        role=role,  # type: ignore[arg-type]
                        assigned_by_user_id="admin",
                        assignment_generation=1,
                        assigned_at_ms=310,
                    )

        with pytest.raises(HigherRoleStateConflictError):
            with transaction(connection):
                repository.insert_open_role(
                    connection,
                    assignment_id=_uuid("higher:team:duplicate"),
                    org_unit_id=ids["team"],
                    user_id="replacement",
                    role="team_leader",
                    assigned_by_user_id="admin",
                    assignment_generation=2,
                    assigned_at_ms=320,
                )

        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=1,generation=generation+1,
                          updated_at=330,row_version=row_version+1
                    WHERE id=?""",
                (ids["team_space"],),
            )
        with pytest.raises(HigherRoleStateConflictError):
            with transaction(connection):
                repository.close_role(
                    connection,
                    assignment_id=team_episode_1,
                    expected_generation=1,
                    expected_row_version=1,
                    ended_at_ms=340,
                    end_reason="replace leader",
                )

        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=0,generation=generation+1,
                          updated_at=341,row_version=row_version+1
                    WHERE id=?""",
                (ids["team_space"],),
            )
            closed = repository.close_role(
                connection,
                assignment_id=team_episode_1,
                expected_generation=1,
                expected_row_version=1,
                ended_at_ms=342,
                end_reason="replace leader",
            )
            assert closed.ended_at_ms == 342

        with pytest.raises(HigherRoleStateConflictError):
            with transaction(connection):
                repository.insert_open_role(
                    connection,
                    assignment_id=_uuid("higher:team:bad-generation"),
                    org_unit_id=ids["team"],
                    user_id="replacement",
                    role="team_leader",
                    assigned_by_user_id="admin",
                    assignment_generation=1,
                    assigned_at_ms=350,
                )

        team_episode_2 = _uuid("higher:team:2")
        with transaction(connection):
            replacement = repository.insert_open_role(
                connection,
                assignment_id=team_episode_2,
                org_unit_id=ids["team"],
                user_id="replacement",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=2,
                assigned_at_ms=351,
            )
            assert replacement.assignment_generation == 2
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=1,generation=generation+1,
                          updated_at=352,row_version=row_version+1
                    WHERE id=?""",
                (ids["team_space"],),
            )

        with pytest.raises(sqlite3.IntegrityError, match="HIGHER_ROLE_WRITE_GUARD"):
            with transaction(connection):
                connection.execute(
                    """UPDATE app_user
                          SET account_status='disabled',disabled_at=360,
                              scope_epoch=scope_epoch+1,updated_at=360,
                              row_version=row_version+1
                        WHERE id='replacement'"""
                )

        with pytest.raises(sqlite3.IntegrityError, match="SPACE_OWNER_INVALID"):
            with transaction(connection):
                connection.execute(
                    """UPDATE knowledge_space
                          SET write_enabled=1,generation=generation+1,
                              updated_at=370,row_version=row_version+1
                        WHERE owner_org_unit_id=?""",
                    (ids["team2"],),
                )

        # Existing personal and Part write semantics remain valid in v9.
        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=1,generation=generation+1,
                          updated_at=380,row_version=row_version+1
                    WHERE owner_user_id='leader'"""
            )
        assert tuple(connection.execute(
            "SELECT write_enabled FROM knowledge_space WHERE owner_user_id='leader'"
        ).fetchone()) == (1,)
        assert tuple(connection.execute(
            "SELECT write_enabled FROM knowledge_space WHERE id=?",
            (ids["part_space"],),
        ).fetchone()) == (1,)
        assert repository.next_assignment_generation(
            connection, org_unit_id=ids["team"], role="team_leader"
        ) == 3
        assert [
            row.assignment_generation
            for row in repository.list_role_history(
                connection,
                org_unit_id=ids["team"],
                role="team_leader",
            )
        ] == [2, 1]
        verify_canonical_integrity(connection, _V9_CATALOG)
    finally:
        connection.close()


@pytest.mark.scenario("HSPACE-001")
def test_distribution_schema_accepts_only_revision_bound_descendant_grants(
    isolated_knowledge_layout,
) -> None:
    v8_factory, _ = _migrate(isolated_knowledge_layout, _V8_CATALOG)
    ids = _seed_v8_hierarchy(v8_factory)
    factory, _ = _migrate(isolated_knowledge_layout)
    repository = HigherSpaceRepository()
    role_id = _uuid("distribution:team-role")
    item_id = _uuid("distribution:item")
    revision_id = _uuid("distribution:revision")
    grant_id = _uuid("distribution:grant")
    operation_id = _uuid("distribution:operation")
    connection = factory.open()
    try:
        with transaction(connection):
            repository.insert_open_role(
                connection,
                assignment_id=role_id,
                org_unit_id=ids["team"],
                user_id="leader",
                role="team_leader",
                assigned_by_user_id="admin",
                assignment_generation=1,
                assigned_at_ms=400,
            )
            empty_hash = (
                "e3b0c44298fc1c149afbf4c8996fb924"
                "27ae41e4649b934ca495991b7852b855"
            )
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,
                     curation_status,current_revision_id,
                     last_curation_transition_id,latest_deactivation_episode_id,
                     redirect_item_id,created_by,updated_by,created_at,updated_at,
                     row_version
                   ) VALUES (?, ?, NULL,'active','provisional',?,NULL,NULL,NULL,
                             'leader','leader',401,401,1)""",
                (item_id, ids["team_space"], revision_id),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,
                     title,raw_content_sha256,normalized_content_sha256,
                     content_size_bytes,normalizer_version,metadata_json,
                     change_kind,change_reason,created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Distributed note',?,?,0,
                             'markdown_lf_v1','{}','initial',
                             'distribution fixture','leader',401)""",
                (revision_id, item_id, empty_hash, empty_hash),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,'',NULL,0,?,401)""",
                (revision_id, item_id, empty_hash),
            )
            audit_id = connection.execute(
                """INSERT INTO audit_event(
                     event_uuid,occurred_at,request_id,operation_id,action,
                     outcome,actor_kind,actor_user_id,actor_display_name,
                     actor_role_snapshot,space_id,item_id,base_revision_id,
                     new_revision_id,reason,details_json,request_meta_json,
                     schema_version
                   ) VALUES (?,402,?,?,'knowledge.distribution.grant',
                             'committed','user','leader','Leader','team_leader',
                             ?,?,NULL,?,'fixture','{}','{}',1)""",
                (
                    _uuid("distribution:audit-event"),
                    _uuid("distribution:request"),
                    operation_id,
                    ids["team_space"],
                    item_id,
                    revision_id,
                ),
            ).lastrowid
            outbox_id = connection.execute(
                """INSERT INTO outbox_event(
                     logical_key,space_id,aggregate_type,aggregate_id,
                     aggregate_version,event_type,payload_json,
                     payload_schema_version,state,available_at,attempt_count,
                     created_at
                   ) VALUES ('distribution:fixture',?,'distribution',?,1,
                             'knowledge.distribution.granted','{}',1,'pending',
                             402,0,402)""",
                (ids["team_space"], grant_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO knowledge_distribution_grant(
                     id,source_space_id,source_item_id,source_revision_id,
                     target_org_unit_id,target_scope_kind,target_scope_id,
                     grant_mode,source_role_assignment_id,created_by_user_id,
                     operation_id,effect_key,reason,created_audit_event_id,
                     created_outbox_event_id,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'org_unit',?,'read_only',?,?,?,?,'fixture',
                             ?,?,402,402,1)""",
                (
                    grant_id,
                    ids["team_space"],
                    item_id,
                    revision_id,
                    ids["part"],
                    ids["part"],
                    role_id,
                    "leader",
                    operation_id,
                    "a" * 64,
                    audit_id,
                    outbox_id,
                ),
            )

        grant = repository.get_distribution(connection, grant_id)
        assert grant is not None
        assert grant.source_revision_id == revision_id
        assert grant.target_org_unit_id == ids["part"]
        assert grant.target_scope_kind == "org_unit"
        assert grant.created_audit_event_id == audit_id
        assert grant.created_outbox_event_id == outbox_id
        assert repository.list_distributions(
            connection, source_space_id=ids["team_space"]
        ) == (grant,)

        with pytest.raises(sqlite3.IntegrityError, match="DISTRIBUTION_GRANT_INVALID"):
            with transaction(connection):
                foreign_grant_id = _uuid("distribution:foreign-grant")
                foreign_operation_id = _uuid("distribution:foreign-operation")
                foreign_audit = connection.execute(
                    """INSERT INTO audit_event(
                         event_uuid,occurred_at,request_id,operation_id,action,
                         outcome,actor_kind,actor_user_id,actor_display_name,
                         actor_role_snapshot,space_id,item_id,base_revision_id,
                         new_revision_id,reason,details_json,request_meta_json,
                         schema_version
                       ) VALUES (?,403,?,?,'knowledge.distribution.grant',
                                 'committed','user','leader','Leader','team_leader',
                                 ?,?,NULL,?,'fixture','{}','{}',1)""",
                    (
                        _uuid("distribution:foreign-audit"),
                        _uuid("distribution:foreign-request"),
                        foreign_operation_id,
                        ids["team_space"],
                        item_id,
                        revision_id,
                    ),
                ).lastrowid
                foreign_outbox = connection.execute(
                    """INSERT INTO outbox_event(
                         logical_key,space_id,aggregate_type,aggregate_id,
                         aggregate_version,event_type,payload_json,
                         payload_schema_version,state,available_at,attempt_count,
                         created_at
                       ) VALUES ('distribution:foreign',?,'distribution',?,1,
                                 'knowledge.distribution.granted','{}',1,
                                 'pending',403,0,403)""",
                    (ids["team_space"], foreign_grant_id),
                ).lastrowid
                connection.execute(
                    """INSERT INTO knowledge_distribution_grant(
                         id,source_space_id,source_item_id,source_revision_id,
                         target_org_unit_id,target_scope_kind,target_scope_id,
                         grant_mode,source_role_assignment_id,created_by_user_id,
                         operation_id,effect_key,reason,created_audit_event_id,
                         created_outbox_event_id,created_at,updated_at,row_version
                       ) VALUES (?,?,?,?,?,'org_unit',?,'read_only',?,?,?,?,'fixture',
                                 ?,?,403,403,1)""",
                    (
                        foreign_grant_id,
                        ids["team_space"],
                        item_id,
                        revision_id,
                        ids["team2"],
                        ids["team2"],
                        role_id,
                        "leader",
                        foreign_operation_id,
                        "b" * 64,
                        foreign_audit,
                        foreign_outbox,
                    ),
                )
        assert repository.get_distribution(connection, grant_id) == grant
        verify_canonical_integrity(connection, _V9_CATALOG)
    finally:
        connection.close()
