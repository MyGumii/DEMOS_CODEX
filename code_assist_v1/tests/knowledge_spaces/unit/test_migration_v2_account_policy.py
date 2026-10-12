from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.users import UserRepository


pytestmark = [pytest.mark.unit, pytest.mark.scenario("AUTH-001")]


_V2_CATALOG = MigrationCatalog((SCHEMA_V1_MIGRATION, SCHEMA_V2_MIGRATION))


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def test_existing_v1_user_upgrades_to_v2_without_reimport_or_field_loss(
    isolated_knowledge_layout,
) -> None:
    """Exercise a real v1 ledger/data file, then upgrade it in place to v2."""

    database_path = isolated_knowledge_layout.core_db_path
    v1_catalog = MigrationCatalog((SCHEMA_V1_MIGRATION,))
    v1_factory = ConnectionFactory._for_isolated_tests(database_path, v1_catalog)
    first = SingleMigrator(
        v1_factory,
        app_version="p1.3-upgrade-fixture",
        storage_layout=_layout(isolated_knowledge_layout),
    ).run()
    assert first.applied_versions == (1,)
    assert first.schema_version == 1

    password_hash = "scrypt:preserve-this-exact-v1-hash"
    v1_connection = v1_factory.open()
    try:
        with transaction(v1_connection):
            v1_connection.execute(
                """INSERT INTO app_user(
                     id,display_name,password_hash,system_role,account_status,
                     current_part_id,scope_epoch,created_at,updated_at,disabled_at,
                     row_version
                   ) VALUES (
                     'legacy01','Legacy One',?,'user','active',NULL,7,
                     111,222,NULL,4
                   )""",
                (password_hash,),
            )
    finally:
        v1_connection.close()

    canonical = ConnectionFactory._for_isolated_tests(database_path, _V2_CATALOG)
    upgraded = SingleMigrator(
        canonical,
        app_version="p1.4-upgrade-test",
        storage_layout=_layout(isolated_knowledge_layout),
    ).run()
    assert upgraded.applied_versions == (2,)
    assert upgraded.schema_version == 2

    users = UserRepository()
    connection = canonical.open()
    try:
        user = users.require(connection, "legacy01")
        assert user.id == "legacy01"
        assert user.display_name == "Legacy One"
        assert user.password_hash == password_hash
        assert user.system_role == "user"
        assert user.account_status == "active"
        assert user.current_part_id is None
        assert user.scope_epoch == 7
        assert user.created_at_ms == 111
        assert user.updated_at_ms == 222
        assert user.disabled_at_ms is None
        assert user.row_version == 4
        assert user.must_change_password is False
        assert user.password_changed_at_ms is None
        assert password_hash not in repr(user)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='app_user'"
        ).fetchone()[0]
        assert "must_change_password INTEGER NOT NULL DEFAULT 0" in table_sql
        assert "password_changed_at INTEGER" in table_sql
    finally:
        connection.close()

    retry = SingleMigrator(
        canonical,
        app_version="p1.4-upgrade-retry",
        storage_layout=_layout(isolated_knowledge_layout),
    ).run()
    assert retry.applied_versions == ()
    assert retry.schema_version == 2

    reopened = canonical.open()
    try:
        assert UserRepository().require(reopened, "legacy01") == user
        assert [
            tuple(row)
            for row in reopened.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            ).fetchall()
        ] == [(1,), (2,)]
    finally:
        reopened.close()


def test_v2_password_policy_trigger_rejects_detached_policy_mutation(
    isolated_knowledge_layout,
) -> None:
    canonical = ConnectionFactory._for_isolated_tests(
        isolated_knowledge_layout.core_db_path, _V2_CATALOG
    )
    SingleMigrator(
        canonical,
        app_version="p1.4-policy-test",
        storage_layout=_layout(isolated_knowledge_layout),
    ).run()
    connection = canonical.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO app_user(
                     id,display_name,password_hash,system_role,account_status,
                     current_part_id,scope_epoch,must_change_password,
                     password_changed_at,created_at,updated_at,disabled_at,row_version
                   ) VALUES (
                     'user01','User 01','hash-1','user','active',NULL,1,0,10,
                     10,10,NULL,1
                   )"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="USER_PASSWORD_POLICY_INVALID"):
            with transaction(connection):
                connection.execute(
                    """UPDATE app_user
                       SET must_change_password=1,password_changed_at=NULL,
                           updated_at=20,row_version=2 WHERE id='user01'"""
                )
        with pytest.raises(sqlite3.IntegrityError, match="USER_PASSWORD_POLICY_INVALID"):
            with transaction(connection):
                connection.execute(
                    """UPDATE app_user
                       SET password_hash='hash-2',updated_at=20,row_version=2
                       WHERE id='user01'"""
                )
    finally:
        connection.close()
