from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    Migration,
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SCHEMA_V5_MIGRATION,
    SCHEMA_V6_MIGRATION,
    SingleMigrator,
    V006_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.migrations.v006_legacy_p25_effect_map import (
    LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE,
    V006_STATEMENTS,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_V5_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
    )
)
_V6_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
    )
)
_RUN = "00000000-0000-4000-8000-000000000100"
_SESSION_MAP = "00000000-0000-4000-8000-000000000201"
_LINK_MAP = "00000000-0000-4000-8000-000000000202"
_REL_MAP = "00000000-0000-4000-8000-000000000301"
_SPACE = "00000000-0000-4000-8000-000000000401"
_CONVERSATION = "00000000-0000-4000-8000-000000000501"
_MESSAGE = "00000000-0000-4000-8000-000000000502"
_SUMMARY = "00000000-0000-4000-8000-000000000503"
_BINDING = "00000000-0000-4000-8000-000000000504"


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _factory(value, catalog=_V6_CATALOG) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)


def _migrate(value, catalog=_V6_CATALOG):
    factory = _factory(value, catalog)
    result = SingleMigrator(
        factory,
        app_version="p2.5-v6-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _insert_map(
    connection,
    mapping_id: str,
    path: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    summary_id: str | None = None,
    context_binding_id: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO legacy_migration_map(
             id,migration_run_id,legacy_kind,normalized_path,source_sha256,
             resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
             status,details_json,created_at,updated_at,conversation_id,message_id,
             summary_id,context_binding_id
           ) VALUES (?,?,?,?,?,'owner',NULL,NULL,NULL,NULL,'imported','{}',100,100,
                     ?,?,?,?)""",
        (
            mapping_id,
            _RUN,
            "conversation.user_sessions",
            path,
            path[-1] * 64,
            conversation_id,
            message_id,
            summary_id,
            context_binding_id,
        ),
    )


def _seed_conversation_targets(connection) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id="owner",
            display_name="Owner",
            password_hash="p2.5-hash",
            system_role="admin",
            account_status="active",
            created_at_ms=10,
            updated_at_ms=10,
        ),
    )
    SpaceRepository().ensure_personal(
        connection,
        space_id=_SPACE,
        user_id="owner",
        created_at_ms=20,
    )
    connection.execute(
        """INSERT INTO conversation(
             id,owner_user_id,space_id,captured_scope_epoch,title,status,
             metadata_json,created_at,updated_at,row_version
           ) VALUES (?,?,?,1,'Legacy','archived','{}',30,30,1)""",
        (_CONVERSATION, "owner", _SPACE),
    )
    connection.execute(
        """INSERT INTO conversation_message(
             id,conversation_id,sequence_no,role,content_json,created_at
           ) VALUES (?,?,1,'user','{}',31)""",
        (_MESSAGE, _CONVERSATION),
    )
    connection.execute(
        """INSERT INTO conversation_summary(
             id,conversation_id,covered_through_sequence_no,summary_md,
             summary_sha256,model_id,prompt_version,summarizer_version,status,
             created_by_user_id,created_at,row_version
           ) VALUES (?,?,1,'summary',?,'legacy','legacy','legacy','active',NULL,32,1)""",
        (_SUMMARY, _CONVERSATION, "c" * 64),
    )
    connection.execute(
        """INSERT INTO conversation_context_binding(
             id,conversation_id,binding_type,binding_mode,external_key,
             source_artifact_id,snapshot_sha256,display_metadata_json,status,
             status_reason,created_by,created_at,status_changed_at,detached_at,
             row_version
           ) VALUES (?,?,'notion_page','live','page-1',NULL,NULL,'{}','active',
                     NULL,'owner',33,33,NULL,1)""",
        (_BINDING, _CONVERSATION),
    )


def test_v6_fresh_bootstrap_has_exact_forward_objects(isolated_knowledge_layout) -> None:
    factory, result = _migrate(isolated_knowledge_layout)
    assert result.applied_versions == (1, 2, 3, 4, 5, 6)
    assert result.schema_version == 6
    connection = factory.open()
    try:
        assert schema_sha256(connection) == V006_EXPECTED_SCHEMA_SHA256
        mapping_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='legacy_migration_map'"
        ).fetchone()["sql"]
        for name in ("conversation_id", "message_id", "summary_id", "context_binding_id"):
            assert name in mapping_sql
        assert connection.execute(
            """SELECT count(*) FROM sqlite_schema WHERE name IN (
                 'legacy_migration_p25_relationship_map',
                 'ix_migration_conversation_target','ux_migration_message_target',
                 'ux_migration_summary_target','ux_migration_context_binding_target',
                 'ux_migration_p25_relationship_identity',
                 'ux_migration_p25_context_binding',
                 'ix_migration_p25_relationship_run',
                 'ix_migration_p25_relationship_endpoints',
                 'trg_legacy_migration_p25_relationship_history_update',
                 'trg_legacy_migration_p25_relationship_history_delete'
               )"""
        ).fetchone()[0] == 11
    finally:
        connection.close()


def test_nonempty_v5_map_upgrades_without_rewriting_rows(isolated_knowledge_layout) -> None:
    v5_factory, _ = _migrate(isolated_knowledge_layout, _V5_CATALOG)
    connection = v5_factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO legacy_migration_map(
                     id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                     status,details_json,created_at,updated_at
                   ) VALUES (?,?, 'operations.jobs','p2v1/job','a'||printf('%063d',0),
                             'imported','{}',10,10)""",
                (_SESSION_MAP, _RUN),
            )
        before = tuple(
            connection.execute(
                """SELECT id,migration_run_id,legacy_kind,normalized_path,
                          source_sha256,status,details_json,created_at,updated_at
                   FROM legacy_migration_map"""
            ).fetchone()
        )
    finally:
        connection.close()

    factory, result = _migrate(isolated_knowledge_layout)
    assert result.applied_versions == (6,)
    connection = factory.open()
    try:
        after = tuple(
            connection.execute(
                """SELECT id,migration_run_id,legacy_kind,normalized_path,
                          source_sha256,status,details_json,created_at,updated_at
                   FROM legacy_migration_map"""
            ).fetchone()
        )
        assert after == before
        row = connection.execute(
            """SELECT conversation_id,message_id,summary_id,context_binding_id
               FROM legacy_migration_map"""
        ).fetchone()
        assert tuple(row) == (None, None, None, None)
    finally:
        connection.close()


def test_v6_relationship_and_target_constraints_are_executable(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_conversation_targets(connection)
            _insert_map(
                connection,
                _SESSION_MAP,
                "p2v1/session-a",
                conversation_id=_CONVERSATION,
            )
            _insert_map(
                connection,
                _LINK_MAP,
                "p2v1/link-b",
                conversation_id=_CONVERSATION,
                context_binding_id=_BINDING,
            )
            connection.execute(
                """INSERT INTO legacy_migration_p25_relationship_map(
                     id,migration_run_id,relationship_id,relationship_sha256,
                     relationship_type,source_mapping_id,from_mapping_id,
                     conversation_id,context_binding_id,resolved_owner_user_id,
                     target_space_id,page_identity_sha256,notion_row_sha256,
                     status,details_json,created_at,updated_at
                   ) VALUES (?,?,?,'d'||printf('%063d',0),'notion_session_page',
                             ?,?,?,?,'owner',?,'e'||printf('%063d',0),
                             'f'||printf('%063d',0),'imported','{}',40,40)""",
                (
                    _REL_MAP,
                    _RUN,
                    "rel_0123456789abcdef01234567",
                    _LINK_MAP,
                    _SESSION_MAP,
                    _CONVERSATION,
                    _BINDING,
                    _SPACE,
                ),
            )

        row = connection.execute(
            "SELECT relationship_type,status FROM legacy_migration_p25_relationship_map"
        ).fetchone()
        assert tuple(row) == ("notion_session_page", "imported")
        with pytest.raises(sqlite3.IntegrityError, match=LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE):
            with transaction(connection):
                connection.execute(
                    "UPDATE legacy_migration_p25_relationship_map SET updated_at=41"
                )
        with pytest.raises(sqlite3.IntegrityError, match=LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE):
            with transaction(connection):
                connection.execute("DELETE FROM legacy_migration_p25_relationship_map")
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("message_id", _MESSAGE),
        ("summary_id", _SUMMARY),
        ("context_binding_id", _BINDING),
    ),
)
def test_v6_child_targets_are_unique(
    isolated_knowledge_layout,
    column: str,
    value: str,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_conversation_targets(connection)
            _insert_map(connection, _SESSION_MAP, "p2v1/first-a", **{column: value})
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                _insert_map(connection, _LINK_MAP, "p2v1/second-b", **{column: value})
    finally:
        connection.close()


def test_v6_rerun_is_idempotent(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    migrator = SingleMigrator(
        factory,
        app_version="p2.5-v6-rerun",
        storage_layout=_layout(isolated_knowledge_layout),
    )
    first = migrator.run()
    second = migrator.run()
    assert first.applied_versions == (1, 2, 3, 4, 5, 6)
    assert second.applied_versions == ()
    assert second.schema_version == 6


def test_v6_named_object_collision_rolls_back_column_additions(
    isolated_knowledge_layout,
) -> None:
    _migrate(isolated_knowledge_layout, _V5_CATALOG)
    collision = Migration(
        version=6,
        name="legacy_p25_effect_map_collision_probe",
        statements=(
            "CREATE TABLE legacy_migration_p25_relationship_map(id TEXT)",
            *V006_STATEMENTS,
        ),
        expected_schema_sha256="0" * 64,
    )
    catalog = MigrationCatalog((*_V5_CATALOG.migrations, collision))
    factory = _factory(isolated_knowledge_layout, catalog)
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="p2.5-v6-collision",
            storage_layout=_layout(isolated_knowledge_layout),
        ).run()

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (5,)
        columns = {
            row[1]
            for row in raw.execute("PRAGMA table_info(legacy_migration_map)").fetchall()
        }
        assert "conversation_id" not in columns
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='legacy_migration_p25_relationship_map'"
        ).fetchone() == (0,)
    finally:
        raw.close()
