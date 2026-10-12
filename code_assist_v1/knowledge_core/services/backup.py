"""Transaction-owning P7.4 online backup orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Callable

from ..backup_manifest import (
    VerifiedBackupBlob,
    build_backup_manifest,
)
from ..backup_storage import (
    BackupFilesystemError,
    FilesystemBackupStorage,
    SnapshotObservation,
)
from ..db import ConnectionFactory, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..migrations.storage_preflight import MigrationStorageLayout
from ..repositories.backup import (
    BackupBlobSpec,
    BackupConflictError,
    BackupIntegrityError,
    BackupRepository,
    BackupRunRecord,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z", re.ASCII)


class BackupServiceError(KnowledgeCoreError):
    code = "KNOWLEDGE_CORE_BACKUP_FAILED"


@dataclass(frozen=True, slots=True)
class BackupServiceConfiguration:
    app_version: str
    build_revision: str
    configuration_version: str
    lease_hmac_key_set_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.app_version, str)
            or not self.app_version.strip()
            or self.app_version != self.app_version.strip()
            or len(self.app_version) > 80
        ):
            raise ValueError("app_version must be stable non-empty text.")
        if (
            not isinstance(self.build_revision, str)
            or _SHA256_RE.fullmatch(self.build_revision) is None
        ):
            raise ValueError("build_revision must be a lowercase SHA-256 digest.")
        for name in ("configuration_version", "lease_hmac_key_set_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a stable version identifier.")


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_run_id: str
    state: str
    completed_directory: str
    snapshot_db_sha256: str
    manifest_sha256: str
    snapshot_audit_high_water: int
    blob_count: int
    blob_bytes: int
    replayed: bool


def _utc_from_ms(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BackupIntegrityError("A backup timestamp is invalid.")
    return (
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _ms_from_utc(value: object) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BackupIntegrityError("The completed manifest timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BackupIntegrityError(
            "The completed manifest timestamp is invalid."
        ) from exc
    milliseconds = int(parsed.timestamp() * 1000)
    if _utc_from_ms(milliseconds) != value:
        raise BackupIntegrityError(
            "The completed manifest timestamp is not canonical milliseconds."
        )
    return milliseconds


class BackupService:
    """Create or recover one durable, verified online backup run."""

    __slots__ = (
        "_configuration",
        "_factory",
        "_failpoint",
        "_repository",
        "_storage",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
        *,
        backup_root: Path,
        configuration: BackupServiceConfiguration,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("BackupService requires a ConnectionFactory.")
        if not isinstance(configuration, BackupServiceConfiguration):
            raise TypeError(
                "BackupService requires immutable BackupServiceConfiguration."
            )
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable when provided.")
        self._factory = factory
        self._configuration = configuration
        self._failpoint = failpoint
        self._repository = BackupRepository()
        self._storage = FilesystemBackupStorage(
            layout, backup_root, migration_catalog=factory.migration_catalog
        )

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _db_now(self) -> int:
        connection = self._factory.open(read_only=True)
        try:
            return db_now_ms(connection)
        finally:
            connection.close()

    def _begin(self, backup_run_id: str) -> tuple[BackupRunRecord, bool]:
        connection = self._factory.open()
        try:
            with transaction(connection):
                existing = self._repository.get(connection, backup_run_id)
                now_ms = db_now_ms(connection)
                run = self._repository.begin(
                    connection,
                    backup_run_id=backup_run_id,
                    created_at_ms=(
                        now_ms if existing is None else existing.created_at_ms
                    ),
                )
                return run, existing is not None
        finally:
            connection.close()

    def _fail(self, backup_run_id: str, error_code: str) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                run = self._repository.get(connection, backup_run_id)
                if run is None or run.state in {"verified", "failed"}:
                    return
                self._repository.mark_failed(
                    connection,
                    backup_run_id=backup_run_id,
                    error_code=error_code,
                    completed_at_ms=max(db_now_ms(connection), run.created_at_ms),
                )
        finally:
            connection.close()

    def _run_blobs(self, backup_run_id: str):
        connection = self._factory.open(read_only=True)
        try:
            return self._repository.list_blobs(connection, backup_run_id)
        finally:
            connection.close()

    def _verify_run_snapshot(
        self,
        run: BackupRunRecord,
        snapshot: SnapshotObservation,
    ) -> None:
        if (
            run.snapshot_db_sha256 != snapshot.sha256
            or run.snapshot_audit_high_water != snapshot.audit_high_water
        ):
            raise BackupIntegrityError(
                "The durable backup run differs from its staged SQLite snapshot."
            )
        stored = self._run_blobs(run.id)
        expected = tuple(
            BackupBlobSpec(value.sha256, value.size_bytes)
            for value in snapshot.blobs
        )
        observed = tuple(
            BackupBlobSpec(value.blob_sha256, value.size_bytes) for value in stored
        )
        if observed != expected:
            raise BackupIntegrityError(
                "The durable backup hold set differs from the staged snapshot."
            )

    def _recover_completed(
        self,
        run: BackupRunRecord,
        *,
        replayed: bool,
    ) -> BackupResult:
        manifest, snapshot = self._storage.verify_completed(run.id)
        if run.state == "copying":
            self._verify_run_snapshot(run, snapshot)
            completed_at_ms = _ms_from_utc(manifest.get("completed_at"))
            connection = self._factory.open()
            try:
                with transaction(connection):
                    run = self._repository.mark_verified(
                        connection,
                        backup_run_id=run.id,
                        manifest_sha256=str(manifest["manifest_sha256"]),
                        completed_at_ms=completed_at_ms,
                    )
            finally:
                connection.close()
        if (
            run.state != "verified"
            or run.manifest_sha256 != manifest.get("manifest_sha256")
            or run.snapshot_db_sha256 != snapshot.sha256
            or run.snapshot_audit_high_water != snapshot.audit_high_water
        ):
            raise BackupIntegrityError(
                "The verified backup ledger differs from completed storage."
            )
        return BackupResult(
            backup_run_id=run.id,
            state="verified",
            completed_directory=f"backup-{run.id}",
            snapshot_db_sha256=snapshot.sha256,
            manifest_sha256=str(manifest["manifest_sha256"]),
            snapshot_audit_high_water=snapshot.audit_high_water,
            blob_count=int(manifest["blob_count"]),
            blob_bytes=int(manifest["blob_bytes"]),
            replayed=replayed,
        )

    def create(self, *, backup_run_id: str) -> BackupResult:
        stage = "BACKUP_SNAPSHOT_FAILED"
        run: BackupRunRecord | None = None
        try:
            run, existed = self._begin(backup_run_id)
            if run.state == "failed":
                raise BackupConflictError("A failed backup run cannot be retried.")
            if run.state == "verified":
                return self._recover_completed(run, replayed=True)
            self._hit("backup.after_hold_acquire")
            if self._storage.completed_exists(run.id):
                if run.state != "copying":
                    raise BackupIntegrityError(
                        "Completed backup storage has no copying ledger state."
                    )
                return self._recover_completed(run, replayed=True)

            with self._storage.workspace(run.id) as workspace:
                if run.state == "preparing":
                    source = self._factory.open(read_only=True)
                    try:
                        snapshot = workspace.create_snapshot(source)
                    finally:
                        source.close()
                    self._hit("backup.after_database_snapshot")
                    connection = self._factory.open()
                    try:
                        with transaction(connection):
                            run = self._repository.begin_copying(
                                connection,
                                backup_run_id=run.id,
                                snapshot_db_sha256=snapshot.sha256,
                                snapshot_audit_high_water=snapshot.audit_high_water,
                                blobs=(
                                    BackupBlobSpec(value.sha256, value.size_bytes)
                                    for value in snapshot.blobs
                                ),
                            )
                    finally:
                        connection.close()
                elif run.state == "copying":
                    snapshot = workspace.inspect_snapshot()
                    self._verify_run_snapshot(run, snapshot)
                else:  # pragma: no cover - closed enum defended above
                    raise BackupIntegrityError("The backup run state is unsupported.")

                stage = "BACKUP_BLOB_COPY_FAILED"
                snapshot_by_sha = {value.sha256: value for value in snapshot.blobs}
                for stored in self._run_blobs(run.id):
                    source_blob = snapshot_by_sha.get(stored.blob_sha256)
                    if source_blob is None:
                        raise BackupIntegrityError(
                            "A durable backup blob is absent from the snapshot."
                        )
                    workspace.copy_blob(source_blob)
                    if stored.status == "pending":
                        connection = self._factory.open()
                        try:
                            with transaction(connection):
                                now_ms = db_now_ms(connection)
                                self._repository.mark_blob_verified(
                                    connection,
                                    backup_run_id=run.id,
                                    blob_sha256=stored.blob_sha256,
                                    copied_at_ms=now_ms,
                                    verified_at_ms=now_ms,
                                )
                        finally:
                            connection.close()
                    elif stored.status != "verified":
                        raise BackupIntegrityError(
                            "A failed blob row cannot belong to a copying backup."
                        )

                stored_blobs = self._run_blobs(run.id)
                if any(value.status != "verified" for value in stored_blobs):
                    raise BackupIntegrityError(
                        "The backup blob hold set did not converge to verified."
                    )
                existing_manifest = workspace.read_manifest()
                completed_at_ms = (
                    max(self._db_now(), run.created_at_ms)
                    if existing_manifest is None
                    else _ms_from_utc(existing_manifest.get("completed_at"))
                )
                manifest = build_backup_manifest(
                    migration_catalog=self._factory.migration_catalog,
                    backup_run_id=run.id,
                    app_version=self._configuration.app_version,
                    build_revision=self._configuration.build_revision,
                    snapshot_db_sha256=snapshot.sha256,
                    snapshot_db_size_bytes=snapshot.size_bytes,
                    snapshot_audit_high_water=snapshot.audit_high_water,
                    blobs=tuple(
                        VerifiedBackupBlob(
                            value.blob_sha256,
                            value.size_bytes,
                            _utc_from_ms(value.copied_at_ms),  # type: ignore[arg-type]
                            _utc_from_ms(value.verified_at_ms),  # type: ignore[arg-type]
                        )
                        for value in stored_blobs
                    ),
                    configuration_version=self._configuration.configuration_version,
                    lease_hmac_key_set_version=(
                        self._configuration.lease_hmac_key_set_version
                    ),
                    created_at=_utc_from_ms(run.created_at_ms),
                    completed_at=_utc_from_ms(completed_at_ms),
                )
                if existing_manifest is not None and existing_manifest != manifest:
                    raise BackupIntegrityError(
                        "The staged manifest differs from the resumed backup facts."
                    )
                stage = "BACKUP_FINALIZATION_FAILED"
                workspace.write_manifest(manifest)
                self._hit("backup.after_blob_manifest")
                if workspace.inspect_snapshot() != snapshot:
                    raise BackupIntegrityError(
                        "The staged snapshot changed before finalization."
                    )
                for value in snapshot.blobs:
                    workspace.copy_blob(value)
                self._hit("backup.before_finalize_rename")
                workspace.finalize()

            connection = self._factory.open()
            try:
                with transaction(connection):
                    run = self._repository.mark_verified(
                        connection,
                        backup_run_id=run.id,
                        manifest_sha256=str(manifest["manifest_sha256"]),
                        completed_at_ms=completed_at_ms,
                    )
            finally:
                connection.close()
            return self._recover_completed(run, replayed=existed)
        except (BackupConflictError, BackupIntegrityError, BackupFilesystemError):
            if run is not None and run.state not in {"verified", "failed"}:
                try:
                    self._fail(run.id, stage)
                except Exception:
                    pass
            raise
        except Exception as exc:
            if run is not None and run.state not in {"verified", "failed"}:
                try:
                    self._fail(run.id, stage)
                except Exception as failure_error:
                    exc.add_note(
                        "The backup failure ledger also could not be finalized: "
                        f"{type(failure_error).__name__}."
                    )
            raise BackupServiceError("Knowledge Core backup did not complete.") from exc


__all__ = [
    "BackupResult",
    "BackupService",
    "BackupServiceConfiguration",
    "BackupServiceError",
]
