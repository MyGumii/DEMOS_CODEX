"""Deterministic GFS retention and verified backup deletion for P7.4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..backup_storage import BackupFilesystemError, FilesystemBackupStorage
from ..db import ConnectionFactory
from ..errors import KnowledgeCoreError
from ..migrations.storage_preflight import MigrationStorageLayout
from ..repositories.backup import BackupConflictError, BackupRepository


class BackupRetentionError(KnowledgeCoreError):
    code = "KNOWLEDGE_CORE_BACKUP_RETENTION_FAILED"


@dataclass(frozen=True, slots=True, order=True)
class RetainedBackup:
    completed_at_ms: int
    backup_run_id: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.completed_at_ms, bool)
            or not isinstance(self.completed_at_ms, int)
            or self.completed_at_ms < 0
        ):
            raise ValueError("completed_at_ms must be non-negative.")
        import uuid

        try:
            canonical = str(uuid.UUID(self.backup_run_id))
        except (AttributeError, ValueError) as exc:
            raise ValueError("backup_run_id must be a canonical UUID.") from exc
        if canonical != self.backup_run_id:
            raise ValueError("backup_run_id must be a canonical UUID.")


@dataclass(frozen=True, slots=True)
class BackupRetentionSelection:
    kept: tuple[RetainedBackup, ...]
    deletable: tuple[RetainedBackup, ...]


@dataclass(frozen=True, slots=True)
class BackupRetentionResult:
    kept_backup_run_ids: tuple[str, ...]
    deleted_backup_run_ids: tuple[str, ...]


def select_backup_retention(
    backups: Iterable[RetainedBackup],
    *,
    half_hour_count: int = 96,
    daily_count: int = 30,
    monthly_count: int = 12,
) -> BackupRetentionSelection:
    """Select UTC restore points using the frozen 96/30/12 policy."""

    for name, value in (
        ("half_hour_count", half_hour_count),
        ("daily_count", daily_count),
        ("monthly_count", monthly_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer.")
    values = tuple(backups)
    if any(not isinstance(value, RetainedBackup) for value in values):
        raise TypeError("backups must contain RetainedBackup values.")
    if len({value.backup_run_id for value in values}) != len(values):
        raise ValueError("A backup run may appear only once.")
    ordered = tuple(
        sorted(
            values,
            key=lambda value: (value.completed_at_ms, value.backup_run_id),
            reverse=True,
        )
    )
    keep_ids = {value.backup_run_id for value in ordered[:half_hour_count]}

    days: set[tuple[int, int, int]] = set()
    months: set[tuple[int, int]] = set()
    for value in ordered:
        observed = datetime.fromtimestamp(
            value.completed_at_ms / 1000,
            tz=timezone.utc,
        )
        day = (observed.year, observed.month, observed.day)
        if len(days) < daily_count and day not in days:
            days.add(day)
            keep_ids.add(value.backup_run_id)
        month = (observed.year, observed.month)
        if len(months) < monthly_count and month not in months:
            months.add(month)
            keep_ids.add(value.backup_run_id)
    kept = tuple(value for value in ordered if value.backup_run_id in keep_ids)
    deletable = tuple(
        value for value in reversed(ordered) if value.backup_run_id not in keep_ids
    )
    return BackupRetentionSelection(kept=kept, deletable=deletable)


class BackupRetentionService:
    """Prune only verified completed directories outside every active hold."""

    __slots__ = ("_factory", "_repository", "_storage")

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
        *,
        backup_root: Path,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("BackupRetentionService requires a ConnectionFactory.")
        self._factory = factory
        self._repository = BackupRepository()
        self._storage = FilesystemBackupStorage(
            layout, backup_root, migration_catalog=factory.migration_catalog
        )

    def _retained(self) -> tuple[RetainedBackup, ...]:
        connection = self._factory.open(read_only=True)
        try:
            active = connection.execute(
                """SELECT count(*) FROM backup_run
                     WHERE state IN ('preparing','copying')"""
            ).fetchone()
            if active is None or int(active[0]) != 0:
                raise BackupConflictError(
                    "Backup retention cannot run while a GC hold is active."
                )
            rows = self._repository.list_verified(connection)
        finally:
            connection.close()
        retained: list[RetainedBackup] = []
        for row in rows:
            if not self._storage.completed_exists(row.id):
                continue
            if row.completed_at_ms is None:
                raise BackupFilesystemError(
                    "A verified backup lacks a completion timestamp."
                )
            manifest, _snapshot = self._storage.verify_completed(row.id)
            from .backup import _ms_from_utc

            if (
                manifest.get("manifest_sha256") != row.manifest_sha256
                or _ms_from_utc(manifest.get("completed_at"))
                != row.completed_at_ms
            ):
                raise BackupFilesystemError(
                    "A retained backup differs from its durable ledger."
                )
            retained.append(RetainedBackup(row.completed_at_ms, row.id))
        return tuple(retained)

    def prune(self) -> BackupRetentionResult:
        try:
            selection = select_backup_retention(self._retained())
            deleted: list[str] = []
            for candidate in selection.deletable:
                connection = self._factory.open(read_only=True)
                try:
                    active = connection.execute(
                        """SELECT count(*) FROM backup_run
                             WHERE state IN ('preparing','copying')"""
                    ).fetchone()
                    row = self._repository.get(connection, candidate.backup_run_id)
                    if (
                        active is None
                        or int(active[0]) != 0
                        or row is None
                        or row.state != "verified"
                        or row.completed_at_ms != candidate.completed_at_ms
                    ):
                        raise BackupConflictError(
                            "Backup retention authority changed before deletion."
                        )
                finally:
                    connection.close()
                self._storage.delete_completed(candidate.backup_run_id)
                deleted.append(candidate.backup_run_id)
            return BackupRetentionResult(
                kept_backup_run_ids=tuple(
                    value.backup_run_id for value in selection.kept
                ),
                deleted_backup_run_ids=tuple(deleted),
            )
        except (BackupConflictError, BackupFilesystemError):
            raise
        except Exception as exc:
            raise BackupRetentionError(
                "The backup retention run did not complete."
            ) from exc


__all__ = [
    "BackupRetentionError",
    "BackupRetentionResult",
    "BackupRetentionSelection",
    "BackupRetentionService",
    "RetainedBackup",
    "select_backup_retention",
]
