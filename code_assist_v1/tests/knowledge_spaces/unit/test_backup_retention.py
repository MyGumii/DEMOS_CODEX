from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import random
import uuid

import pytest

from code_assist_v1.knowledge_core.backup_storage import FilesystemBackupStorage
from code_assist_v1.knowledge_core.services.backup_retention import (
    BackupRetentionService,
    RetainedBackup,
    select_backup_retention,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_backup_service import (
    _backup_root,
    _factory,
    _layout,
    _seed_referenced_blob,
    _service,
)


pytestmark = pytest.mark.unit


def _point(index: int, observed: datetime) -> RetainedBackup:
    return RetainedBackup(
        completed_at_ms=int(observed.timestamp() * 1000),
        backup_run_id=str(uuid.UUID(int=0x8000 + index)),
    )


def test_retention_keeps_latest_96_plus_30_utc_days_and_12_utc_months() -> None:
    origin = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
    values: list[RetainedBackup] = []
    index = 1
    for offset in range(96):
        values.append(_point(index, origin - timedelta(minutes=30 * offset)))
        index += 1
    for offset in range(1, 61):
        values.append(_point(index, origin - timedelta(days=2 + offset)))
        index += 1
    for offset in range(1, 19):
        month = origin.month - offset
        year = origin.year
        while month <= 0:
            month += 12
            year -= 1
        values.append(_point(index, datetime(year, month, 1, tzinfo=timezone.utc)))
        index += 1
    shuffled = list(values)
    random.Random(7400).shuffle(shuffled)

    selected = select_backup_retention(shuffled)
    repeated = select_backup_retention(reversed(shuffled))
    assert selected == repeated
    assert len(selected.kept) + len(selected.deletable) == len(values)
    assert {value.backup_run_id for value in selected.kept}.isdisjoint(
        value.backup_run_id for value in selected.deletable
    )
    newest = sorted(
        values,
        key=lambda value: (value.completed_at_ms, value.backup_run_id),
        reverse=True,
    )
    kept_ids = {value.backup_run_id for value in selected.kept}
    assert {value.backup_run_id for value in newest[:96]} <= kept_ids

    newest_days: dict[tuple[int, int, int], str] = {}
    newest_months: dict[tuple[int, int], str] = {}
    for value in newest:
        observed = datetime.fromtimestamp(
            value.completed_at_ms / 1000, timezone.utc
        )
        newest_days.setdefault(
            (observed.year, observed.month, observed.day), value.backup_run_id
        )
        newest_months.setdefault(
            (observed.year, observed.month), value.backup_run_id
        )
    assert set(tuple(newest_days.values())[:30]) <= kept_ids
    assert set(tuple(newest_months.values())[:12]) <= kept_ids
    assert selected.deletable
    assert tuple(value.completed_at_ms for value in selected.deletable) == tuple(
        sorted(value.completed_at_ms for value in selected.deletable)
    )


def test_verified_backup_can_be_pruned_without_touching_source_or_ledger(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_referenced_blob(factory, isolated_knowledge_layout)
    backup_root = _backup_root(isolated_knowledge_layout)
    run_id = "00000000-0000-4000-8000-000000007470"
    _service(isolated_knowledge_layout, factory, backup_root).create(
        backup_run_id=run_id
    )
    source_sha = hashlib.sha256(
        isolated_knowledge_layout.core_db_path.read_bytes()
    ).hexdigest()

    FilesystemBackupStorage(
        _layout(isolated_knowledge_layout), backup_root
    ).delete_completed(run_id)

    assert not (backup_root / f"backup-{run_id}").exists()
    assert not (backup_root / f".pruning-{run_id}.partial").exists()
    assert source_sha == hashlib.sha256(
        isolated_knowledge_layout.core_db_path.read_bytes()
    ).hexdigest()
    connection = factory.open(read_only=True)
    try:
        assert tuple(
            connection.execute(
                "SELECT state FROM backup_run WHERE id=?", (run_id,)
            ).fetchone()
        ) == ("verified",)
    finally:
        connection.close()


def test_retention_service_keeps_the_only_verified_restore_point(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    backup_root = _backup_root(isolated_knowledge_layout)
    run_id = "00000000-0000-4000-8000-000000007471"
    _service(isolated_knowledge_layout, factory, backup_root).create(
        backup_run_id=run_id
    )
    result = BackupRetentionService(
        factory,
        _layout(isolated_knowledge_layout),
        backup_root=backup_root,
    ).prune()
    assert result.kept_backup_run_ids == (run_id,)
    assert result.deleted_backup_run_ids == ()
    assert (backup_root / f"backup-{run_id}").is_dir()
