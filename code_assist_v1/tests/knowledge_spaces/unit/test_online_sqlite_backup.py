from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    online_backup_to_existing_file,
    transaction,
)
from code_assist_v1.knowledge_core.errors import (
    KnowledgeCoreDatabasePathError,
    KnowledgeCoreTransactionError,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)


pytestmark = pytest.mark.unit


def _factory(value) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        value.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.4-online-backup-test",
        storage_layout=MigrationStorageLayout(
            data_root=value.root,
            database=value.core_db_path,
            blob_root=value.blobs_dir,
            projection_root=value.projections_dir,
        ),
    ).run()
    return factory


def _empty_target(path: Path) -> tuple[int, int]:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        facts = os.fstat(descriptor)
        return int(facts.st_dev), int(facts.st_ino)
    finally:
        os.close(descriptor)


def _seed_user(factory: ConnectionFactory, user_id: str) -> None:
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO app_user(
                     id,display_name,password_hash,system_role,account_status,
                     current_part_id,scope_epoch,must_change_password,
                     password_changed_at,created_at,updated_at,disabled_at,row_version
                   ) VALUES (?,?,'hash','user','active',NULL,1,0,1,1,1,NULL,1)""",
                (user_id, user_id),
            )
    finally:
        connection.close()


def test_online_backup_uses_snapshot_api_and_produces_standalone_integral_db(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_user(factory, "backup-user")
    parent = isolated_knowledge_layout.root / "backup-stage"
    parent.mkdir()
    target = parent / "knowledge-core.sqlite"
    identity = _empty_target(target)

    source = factory.open(read_only=True)
    try:
        result = online_backup_to_existing_file(
            source, target, expected_destination_identity=identity
        )
    finally:
        source.close()

    assert result.destination_path == target.resolve(strict=True)
    assert result.destination_identity == identity
    assert result.file_size_bytes > 0
    assert result.page_count > 0
    assert result.page_size >= 512
    assert result.user_version == CANONICAL_MIGRATION_CATALOG.latest_version
    assert result.audit_high_water == 0
    assert result.quick_check == "ok"
    assert result.foreign_key_violation_count == 0
    assert not Path(str(target) + "-wal").exists()
    assert not Path(str(target) + "-shm").exists()

    restored = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    try:
        assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert restored.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
        assert restored.execute(
            "SELECT display_name FROM app_user WHERE id='backup-user'"
        ).fetchone() == ("backup-user",)
    finally:
        restored.close()


def test_online_backup_rejects_transaction_and_wrong_destination_identity(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    parent = isolated_knowledge_layout.root / "backup-stage"
    parent.mkdir()
    target = parent / "knowledge-core.sqlite"
    identity = _empty_target(target)
    source = factory.open()
    try:
        with transaction(source):
            with pytest.raises(KnowledgeCoreTransactionError, match="cannot run"):
                online_backup_to_existing_file(
                    source, target, expected_destination_identity=identity
                )
        with pytest.raises(KnowledgeCoreDatabasePathError, match="changed before"):
            online_backup_to_existing_file(
                source,
                target,
                expected_destination_identity=(identity[0], identity[1] + 1),
            )
        with pytest.raises(KnowledgeCoreDatabasePathError, match="cannot be the canonical"):
            online_backup_to_existing_file(
                source,
                factory.database_path,
                expected_destination_identity=source.database_identity,
            )
    finally:
        source.close()


def test_online_backup_rejects_multiply_linked_destination(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    parent = isolated_knowledge_layout.root / "backup-stage"
    parent.mkdir()
    target = parent / "knowledge-core.sqlite"
    identity = _empty_target(target)
    sibling = parent / "other.sqlite"
    os.link(target, sibling)
    source = factory.open(read_only=True)
    try:
        with pytest.raises(KnowledgeCoreDatabasePathError, match="one hard link"):
            online_backup_to_existing_file(
                source, target, expected_destination_identity=identity
            )
    finally:
        source.close()
