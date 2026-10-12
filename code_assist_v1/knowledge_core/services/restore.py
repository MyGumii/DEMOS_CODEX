"""Offline isolated restore drill and projection-rebuild verifier for P7.4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import time

from ..backup_storage import FilesystemBackupStorage
from ..db import ConnectionFactory, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..migrations.runner import (
    verify_canonical_integrity,
    verify_canonical_integrity_before_projection_rebuild,
)
from ..migrations.storage_preflight import MigrationStorageLayout
from ..migrations.model import MigrationCatalog
from ..projection.materializer import FullProjectionMaterializer
from ..projection.graph import AggregateGraphProjectionMaterializer
from ..projection.temporal import TemporalProjectionMaterializer
from ..repositories.backup import BackupIntegrityError, BackupRepository


class RestoreDrillError(KnowledgeCoreError):
    code = "KNOWLEDGE_CORE_RESTORE_DRILL_FAILED"


@dataclass(frozen=True, slots=True)
class RestoreDrillResult:
    backup_run_id: str
    target_data_root: str
    restored_snapshot_sha256: str
    snapshot_audit_high_water: int
    restored_blob_count: int
    restored_blob_bytes: int
    projection_space_count: int
    projection_entry_count: int
    projection_spaces_sha256: str
    integrity_mismatch_count: int
    rpo_minutes: float
    rto_seconds: float
    max_rpo_minutes: int
    max_rto_minutes: int
    passed: bool


def _utc_ms(value: object) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BackupIntegrityError("The backup creation timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BackupIntegrityError(
            "The backup creation timestamp is invalid."
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise BackupIntegrityError("The backup creation timestamp is not UTC.")
    return int(parsed.timestamp() * 1000)


def _restore_post_v50_projections(
    factory: ConnectionFactory, layout: MigrationStorageLayout
) -> None:
    """Recover root-bound graph and temporal files before restore passes."""
    if factory.migration_catalog.latest_version < 51:
        return
    connection = factory.open(read_only=True)
    try:
        # v51 has no root-aware temporal successor. A temporal source cannot be
        # called fully restored merely because base Markdown was regenerated.
        temporal = connection.execute(
            """SELECT count(*) FROM knowledge_space
                 WHERE temporal_generation<>0"""
        ).fetchone()[0]
        temporal_heads = connection.execute(
            "SELECT count(*) FROM revision_temporal_head"
        ).fetchone()[0]
        if (temporal or temporal_heads) and factory.migration_catalog.latest_version < 52:
            raise RestoreDrillError(
                "Temporal projection recovery requires a root-aware successor."
            )
        temporal_rows = connection.execute(
            """SELECT s.id FROM knowledge_space AS s
                WHERE s.status='active'
                  AND (s.temporal_generation<>0
                    OR EXISTS (SELECT 1 FROM revision_temporal_head AS h
                                WHERE h.space_id=s.id)
                    OR EXISTS (SELECT 1 FROM revision_verification_head AS h
                                WHERE h.space_id=s.id))
                ORDER BY s.id"""
        ).fetchall()
        temporal_space_ids = tuple(str(row[0]) for row in temporal_rows)
        rows = connection.execute(
            """SELECT vector.selected_space_id
                 FROM aggregate_generation_vector AS vector
                 JOIN knowledge_space AS space
                   ON space.id=vector.selected_space_id
                 JOIN org_unit AS unit
                   ON unit.id=space.owner_org_unit_id
                WHERE space.space_kind IN ('team','responsibility')
                  AND space.status='active' AND unit.status='active'
                ORDER BY vector.selected_space_id"""
        ).fetchall()
        space_ids = tuple(str(row[0]) for row in rows)
    finally:
        connection.close()
    if space_ids:
        materializer = AggregateGraphProjectionMaterializer(factory, layout)
        for space_id in space_ids:
            result = materializer.rebuild_space(space_id)
            manifest = layout.projection_root / space_id / "aggregate-graph-manifest.json"
            try:
                actual_sha256 = sha256(manifest.read_bytes()).hexdigest()
            except OSError as exc:
                raise RestoreDrillError(
                    "The recovered aggregate graph manifest is unavailable."
                ) from exc
            if actual_sha256 != result.manifest_sha256:
                raise RestoreDrillError(
                    "The recovered aggregate graph manifest differs from its canonical generation."
                )
    if temporal_space_ids:
        materializer = TemporalProjectionMaterializer(factory, layout)
        for space_id in temporal_space_ids:
            result = materializer.rebuild_space(space_id)
            manifest = layout.projection_root / space_id / "manifest-v2.json"
            try:
                actual_sha256 = sha256(manifest.read_bytes()).hexdigest()
            except OSError as exc:
                raise RestoreDrillError(
                    "The recovered temporal manifest is unavailable."
                ) from exc
            if actual_sha256 != result.manifest_sha256:
                raise RestoreDrillError(
                    "The recovered temporal manifest differs from its canonical generation."
                )


class RestoreDrillService:
    """Restore a verified backup into a distinct, empty isolated data root."""

    __slots__ = ("_factory", "_catalog", "_source_layout", "_storage", "_test_override")

    def __init__(
        self,
        source_factory: ConnectionFactory,
        source_layout: MigrationStorageLayout,
        *,
        backup_root: Path,
    ) -> None:
        if not isinstance(source_factory, ConnectionFactory):
            raise TypeError("RestoreDrillService requires a ConnectionFactory.")
        self._factory = source_factory
        self._catalog = source_factory.migration_catalog
        self._source_layout = source_layout
        source = source_factory.open(read_only=True)
        try:
            self._test_override = source.test_runtime_override
        finally:
            source.close()
        self._storage = FilesystemBackupStorage(
            source_layout, backup_root,
            migration_catalog=source_factory.migration_catalog,
        )

    @classmethod
    def from_backup(
        cls, *, backup_root: Path, migration_catalog: MigrationCatalog,
        expected_manifest_sha256: str,
    ) -> "RestoreDrillService":
        """Recover without opening any original DB or trusting archive-selected code."""
        service = cls.__new__(cls)
        service._factory = None
        service._source_layout = None
        service._catalog = migration_catalog
        service._test_override = False
        service._storage = FilesystemBackupStorage.for_recovery(
            backup_root, migration_catalog=migration_catalog,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        return service

    def _target_factory(self, target: MigrationStorageLayout) -> ConnectionFactory:
        if self._test_override:
            if os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
                raise RestoreDrillError(
                    "The isolated-test SQLite runtime gate is unavailable."
                )
            return ConnectionFactory._for_isolated_tests(
                target.database, self._catalog
            )
        return ConnectionFactory(target.database, self._catalog)

    def restore(
        self,
        *,
        backup_run_id: str,
        target_layout: MigrationStorageLayout,
        assumed_incident_at_ms: int,
        max_rpo_minutes: int = 60,
        max_rto_minutes: int = 240,
    ) -> RestoreDrillResult:
        if (
            isinstance(assumed_incident_at_ms, bool)
            or not isinstance(assumed_incident_at_ms, int)
            or assumed_incident_at_ms < 0
        ):
            raise ValueError("assumed_incident_at_ms must be non-negative.")
        for name, value in (
            ("max_rpo_minutes", max_rpo_minutes),
            ("max_rto_minutes", max_rto_minutes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        started = time.perf_counter()
        try:
            if self._storage.recovery_only:
                # Memory-only exact-runtime check before archive SQLite or target writes.
                self._target_factory(target_layout)._runtime_preflight()
                manifest, _snapshot = self._storage.verify_completed(backup_run_id)
                created_at_ms = _utc_ms(manifest.get("created_at"))
                age_ms = assumed_incident_at_ms - created_at_ms
                if age_ms < 0 or age_ms > max_rpo_minutes * 60_000:
                    raise RestoreDrillError("The selected backup is outside the incident RPO.")
            with self._storage.restore_target(target_layout) as target:
                manifest, snapshot, _boundary = self._storage.restore_completed(
                    backup_run_id, target
                )
                target_factory = self._target_factory(target_layout)
                connection = target_factory.open()
                try:
                    with transaction(connection):
                        captured = BackupRepository().get(connection, backup_run_id)
                        if captured is None or captured.state != "preparing":
                            raise BackupIntegrityError(
                                "The restored snapshot lacks its captured backup hold."
                            )
                        BackupRepository().mark_failed(
                            connection,
                            backup_run_id=backup_run_id,
                            error_code="RESTORED_BACKUP_HOLD_CLOSED",
                            completed_at_ms=max(
                                db_now_ms(connection), captured.created_at_ms
                            ),
                        )
                finally:
                    connection.close()

                connection = target_factory.open(read_only=True)
                try:
                    verify_canonical_integrity_before_projection_rebuild(
                        connection, target_factory.migration_catalog
                    )
                finally:
                    connection.close()
                projection = FullProjectionMaterializer(
                    target_factory, target_layout
                ).rebuild_all()
                _restore_post_v50_projections(target_factory, target_layout)
                target.verify(require_database=True)
                connection = target_factory.open(read_only=True)
                try:
                    verify_canonical_integrity(
                        connection, target_factory.migration_catalog
                    )
                    active_holds = connection.execute(
                        """SELECT count(*) FROM backup_run
                             WHERE state IN ('preparing','copying')"""
                    ).fetchone()[0]
                    if active_holds != 0:
                        raise BackupIntegrityError(
                            "The restored database retained an active backup hold."
                        )
                finally:
                    connection.close()
            created_at_ms = _utc_ms(manifest.get("created_at"))
            if assumed_incident_at_ms < created_at_ms:
                raise RestoreDrillError(
                    "The assumed incident precedes the selected backup."
                )
            rpo_minutes = (assumed_incident_at_ms - created_at_ms) / 60_000
            rto_seconds = time.perf_counter() - started
            passed = (
                rpo_minutes <= max_rpo_minutes
                and rto_seconds <= max_rto_minutes * 60
            )
            result = RestoreDrillResult(
                backup_run_id=backup_run_id,
                target_data_root=str(target_layout.data_root.resolve(strict=True)),
                restored_snapshot_sha256=snapshot.sha256,
                snapshot_audit_high_water=snapshot.audit_high_water,
                restored_blob_count=int(manifest["blob_count"]),
                restored_blob_bytes=int(manifest["blob_bytes"]),
                projection_space_count=len(projection.spaces),
                projection_entry_count=projection.entry_count,
                projection_spaces_sha256=projection.spaces_sha256,
                integrity_mismatch_count=0,
                rpo_minutes=rpo_minutes,
                rto_seconds=rto_seconds,
                max_rpo_minutes=max_rpo_minutes,
                max_rto_minutes=max_rto_minutes,
                passed=passed,
            )
            if not passed:
                raise RestoreDrillError(
                    "The isolated restore exceeded its RPO or RTO objective."
                )
            return result
        except (BackupIntegrityError, RestoreDrillError):
            raise
        except Exception as exc:
            raise RestoreDrillError(
                "The isolated restore drill did not complete."
            ) from exc


__all__ = ["RestoreDrillError", "RestoreDrillResult", "RestoreDrillService"]
