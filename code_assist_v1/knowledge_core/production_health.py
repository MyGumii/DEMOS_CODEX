"""P7.6 secret-free production readiness and live metric snapshot.

The collector is deliberately read-only.  It exposes aggregate counts, ages,
versions, and byte totals only: no filesystem paths, identifiers, payloads,
claims, account names, or stored error JSON cross this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import ntpath
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any

from .db import (
    ConnectionFactory,
    database_runtime_metrics,
    db_now_ms,
    observe_sqlite_health,
    transaction,
    TransactionMode,
)
from .feature_flags import FEATURE_FLAG_NAMES, read_feature_flags
from .migrations import CANONICAL_MIGRATION_CATALOG
from .runtime import FAILPOINT_EXACT_NAME, FAILPOINT_PREFIX
from .runtime import EXPECTED_CONNECTION_PRAGMAS


PRODUCTION_HEALTH_SCHEMA = "code-assist.production-health"
PRODUCTION_HEALTH_SCHEMA_VERSION = 1
APPLICATION_BUILD_VERSION = "p7.6-health-v1"
MAX_VERIFIED_BACKUP_AGE_MS = 45 * 60 * 1000
MIGRATION_FREE_SPACE_MULTIPLIER = 3
HEALTH_CACHE_TTL_SECONDS = 5.0

_OUTBOX_STATES = ("pending", "processing", "retry", "applied", "dead")
_JOB_STATES = (
    "queued",
    "claimed",
    "running",
    "retry",
    "succeeded",
    "failed",
    "cancelled",
    "dead",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class ProductionHealthError(RuntimeError):
    code = "PRODUCTION_HEALTH_FAILED"


def _nonnegative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProductionHealthError(f"{field} is not a nonnegative integer.")
    return value


def _one_int(connection: object, sql: str, *, field: str) -> int:
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise ProductionHealthError("The health connection is invalid.")
    row = execute(sql).fetchone()
    if row is None or len(row) != 1:
        raise ProductionHealthError(f"{field} is unavailable.")
    return _nonnegative(row[0], field=field)


def _state_counts(
    connection: object,
    *,
    table: str,
    states: tuple[str, ...],
) -> dict[str, int]:
    if table not in {"outbox_event", "durable_job"}:
        raise ProductionHealthError("The health state table is not allowlisted.")
    rows = connection.execute(  # type: ignore[attr-defined]
        f"SELECT state,count(*) FROM {table} GROUP BY state ORDER BY state"
    ).fetchall()
    counts = {state: 0 for state in states}
    for row in rows:
        if len(row) != 2 or row[0] not in counts:
            raise ProductionHealthError("A health state row is invalid.")
        counts[str(row[0])] = _nonnegative(row[1], field=f"{table}.count")
    return counts


def _age_ms(now_ms: int, observed_ms: object | None) -> int | None:
    if observed_ms is None:
        return None
    value = _nonnegative(observed_ms, field="observed timestamp")
    return max(0, now_ms - value)


def _free_bytes(path: str | os.PathLike[str]) -> int:
    return int(shutil.disk_usage(path).free)


def _file_size(path: Path) -> int:
    try:
        facts = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return 0
    return int(facts.st_size)


def _path_key(path: str | os.PathLike[str]) -> str:
    return ntpath.normcase(ntpath.normpath(os.fspath(path)))


def _storage_path_eligible(
    path: str | os.PathLike[str],
    *,
    volume_probe: Callable[[str | os.PathLike[str]], Mapping[str, Any]],
) -> bool:
    try:
        observed = volume_probe(path)
    except Exception:
        return False
    volume = observed.get("volume") if isinstance(observed, Mapping) else None
    reparse = observed.get("reparse") if isinstance(observed, Mapping) else None
    resolved = observed.get("resolved_path") if isinstance(observed, Mapping) else None
    return bool(
        isinstance(observed, Mapping)
        and observed.get("eligible") is True
        and observed.get("exists") is True
        and isinstance(volume, Mapping)
        and volume.get("drive_type_code") == 3
        and isinstance(volume.get("filesystem"), str)
        and str(volume["filesystem"]).casefold() == "ntfs"
        and isinstance(reparse, Mapping)
        and reparse.get("present") is False
        and isinstance(resolved, str)
        and _path_key(resolved) == _path_key(path)
    )


def _feature_flag_snapshot(environment: Mapping[str, str]) -> dict[str, int]:
    flags = read_feature_flags(environment)
    values = flags.as_environment_map()
    if set(values) != set(FEATURE_FLAG_NAMES):
        raise ProductionHealthError("The feature-flag snapshot is incomplete.")
    return {name: int(values[name]) for name in sorted(values)}


def _migration_fact(connection: object) -> dict[str, object]:
    row = connection.execute(  # type: ignore[attr-defined]
        """SELECT version,name,checksum_sha256
             FROM schema_migration ORDER BY version DESC LIMIT 1"""
    ).fetchone()
    if row is None or len(row) != 3:
        raise ProductionHealthError("The schema migration fact is unavailable.")
    version = _nonnegative(row[0], field="schema migration version")
    return {
        "checksum_sha256": str(row[2]),
        "name": str(row[1]),
        "version": version,
    }


def _referenced_blob_fact(connection: object) -> dict[str, int]:
    from .retained_blob_references import retained_blob_union_sql
    row = connection.execute(  # type: ignore[attr-defined]
        f"""WITH referenced(sha256) AS ({retained_blob_union_sql(connection)})
           SELECT count(*),coalesce(sum(b.size_bytes),0),
                  coalesce(sum(CASE WHEN b.verification_status='verified' THEN 0 ELSE 1 END),0)
             FROM blob_object b JOIN referenced r ON r.sha256=b.sha256"""
    ).fetchone()
    if row is None or len(row) != 3:
        raise ProductionHealthError("The referenced-blob metric is unavailable.")
    return {
        "count": _nonnegative(row[0], field="referenced blob count"),
        "size_bytes": _nonnegative(row[1], field="referenced blob bytes"),
        "unverified_count": _nonnegative(
            row[2], field="unverified referenced blob count"
        ),
    }


def _collect_persisted_health(connection: object) -> dict[str, object]:
    """Collect every canonical metric from one SQLite read snapshot."""

    now_ms = db_now_ms(connection)  # type: ignore[arg-type]
    migration = _migration_fact(connection)
    outbox_counts = _state_counts(
        connection, table="outbox_event", states=_OUTBOX_STATES
    )
    outbox_oldest = connection.execute(  # type: ignore[attr-defined]
        """SELECT min(created_at) FROM outbox_event
            WHERE state IN ('pending','processing','retry')"""
    ).fetchone()
    if outbox_oldest is None or len(outbox_oldest) != 1:
        raise ProductionHealthError("The outbox age metric is unavailable.")
    outbox_attempts = _one_int(
        connection,
        "SELECT coalesce(sum(attempt_count),0) FROM outbox_event",
        field="outbox attempt count",
    )
    outbox_retries = _one_int(
        connection,
        """SELECT coalesce(sum(CASE WHEN attempt_count>0
                                    THEN attempt_count-1 ELSE 0 END),0)
             FROM outbox_event""",
        field="outbox retry count",
    )

    job_counts = _state_counts(connection, table="durable_job", states=_JOB_STATES)
    job_oldest = connection.execute(  # type: ignore[attr-defined]
        """SELECT min(created_at) FROM durable_job
            WHERE state IN ('queued','claimed','running','retry')"""
    ).fetchone()
    if job_oldest is None or len(job_oldest) != 1:
        raise ProductionHealthError("The job age metric is unavailable.")
    job_attempts = _one_int(
        connection,
        "SELECT coalesce(sum(attempt_count),0) FROM durable_job",
        field="job attempt count",
    )
    job_retries = _one_int(
        connection,
        """SELECT coalesce(sum(CASE WHEN attempt_count>0
                                    THEN attempt_count-1 ELSE 0 END),0)
             FROM durable_job""",
        field="job retry count",
    )

    lease_row = connection.execute(  # type: ignore[attr-defined]
        """SELECT
               coalesce(sum(CASE WHEN holder_user_id IS NOT NULL AND expires_at>? THEN 1 ELSE 0 END),0),
               coalesce(sum(CASE WHEN holder_user_id IS NOT NULL AND expires_at<=? THEN 1 ELSE 0 END),0),
               coalesce(sum(CASE WHEN holder_user_id IS NULL THEN 1 ELSE 0 END),0)
             FROM item_lease""",
        (now_ms, now_ms),
    ).fetchone()
    if lease_row is None or len(lease_row) != 3:
        raise ProductionHealthError("The lease metric is unavailable.")

    projection_row = connection.execute(  # type: ignore[attr-defined]
        """SELECT count(*),coalesce(sum(desired_generation),0),
                  coalesce(sum(built_generation),0),
                  coalesce(sum(desired_generation-built_generation),0),
                  coalesce(max(desired_generation-built_generation),0),
                  coalesce(sum(CASE WHEN desired_generation>built_generation THEN 1 ELSE 0 END),0),
                  coalesce(sum(CASE WHEN status='error' THEN 1 ELSE 0 END),0),
                  min(CASE WHEN desired_generation>built_generation THEN updated_at END)
             FROM projection_state"""
    ).fetchone()
    if projection_row is None or len(projection_row) != 8:
        raise ProductionHealthError("The projection metric is unavailable.")

    backup_row = connection.execute(  # type: ignore[attr-defined]
        """SELECT completed_at FROM backup_run
            WHERE state='verified' ORDER BY completed_at DESC,id DESC LIMIT 1"""
    ).fetchone()
    backup_completed = None if backup_row is None else backup_row[0]
    return {
        "backup_age": _age_ms(now_ms, backup_completed),
        "blobs": _referenced_blob_fact(connection),
        "job_attempts": job_attempts,
        "job_counts": job_counts,
        "job_oldest_age": _age_ms(now_ms, job_oldest[0]),
        "job_retries": job_retries,
        "lease_row": tuple(lease_row),
        "migration": migration,
        "now_ms": now_ms,
        "outbox_attempts": outbox_attempts,
        "outbox_counts": outbox_counts,
        "outbox_oldest_age": _age_ms(now_ms, outbox_oldest[0]),
        "outbox_retries": outbox_retries,
        "projection_row": tuple(projection_row),
    }


def build_production_health_snapshot(
    factory: ConnectionFactory,
    *,
    blob_root: str | os.PathLike[str],
    config_sha256: str,
    service_id: str,
    environ: Mapping[str, str] | None = None,
    free_space_probe: Callable[[str | os.PathLike[str]], int] = _free_bytes,
    file_size_probe: Callable[[Path], int] = _file_size,
    volume_probe: Callable[[str | os.PathLike[str]], Mapping[str, Any]] | None = None,
    allow_test_runtime_override: bool = False,
) -> tuple[dict[str, Any], int]:
    """Return one closed readiness document and its HTTP status code."""

    if not isinstance(factory, ConnectionFactory):
        raise TypeError("factory must be a ConnectionFactory.")
    if not isinstance(config_sha256, str) or _SHA256_RE.fullmatch(config_sha256) is None:
        raise ValueError("config_sha256 must be a SHA-256 string.")
    if service_id not in {"CodeAssist-Web", "CodeAssist-Notion"}:
        raise ValueError("service_id must identify a reviewed web service.")
    if not isinstance(allow_test_runtime_override, bool):
        raise TypeError("allow_test_runtime_override must be boolean.")
    if allow_test_runtime_override and os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
        raise ProductionHealthError(
            "The SQLite health runtime override is restricted to isolated tests."
        )
    environment = os.environ if environ is None else environ
    if volume_probe is None:
        from ..tools.windows_volume import probe_path

        volume_probe = probe_path
    reasons: set[str] = set()

    def add(reason: str) -> None:
        reasons.add(reason)

    flags: dict[str, int] = {}
    try:
        flags = _feature_flag_snapshot(environment)
    except Exception:
        flags = {
            name: (int(environment[name]) if environment.get(name) in {"0", "1"} else -1)
            for name in sorted(FEATURE_FLAG_NAMES)
        }
        add("feature_flags_invalid")
    failpoints = any(
        name == FAILPOINT_EXACT_NAME or name.startswith(FAILPOINT_PREFIX)
        for name in environment
    )
    if failpoints:
        add("failpoint_configuration_present")

    database_path = factory.database_path
    storage_paths = {
        "blob": blob_root,
        "database": database_path,
        "database_parent": database_path.parent,
    }
    storage_eligibility = {
        role: _storage_path_eligible(path, volume_probe=volume_probe)
        for role, path in sorted(storage_paths.items())
    }
    for role, eligible in storage_eligibility.items():
        if not eligible:
            add(f"{role}_storage_ineligible")
    if not all(storage_eligibility.values()):
        reason_rows = sorted(reasons)
        return (
            {
                "failpoints_absent": not failpoints,
                "feature_flags": flags,
                "gate": {"reason_codes": reason_rows, "status": "not_ready"},
                "observed_at_ms": int(time.time() * 1000),
                "report_schema": PRODUCTION_HEALTH_SCHEMA,
                "schema_version": PRODUCTION_HEALTH_SCHEMA_VERSION,
                "service_id": service_id,
                "storage": {"eligibility": storage_eligibility},
            },
            503,
        )

    sqlite = observe_sqlite_health(factory)
    expected_pragmas = {
        name: value
        for name, value in sqlite.pragmas.items()
        if name not in {"query_only", "user_version"}
    }
    sqlite_ready = (
        (sqlite.runtime.exact_allowlist_match or allow_test_runtime_override)
        and (not sqlite.test_runtime_override or allow_test_runtime_override)
        and sqlite.fts5_executable
        and "ENABLE_FTS5" in sqlite.compile_options
        and sqlite.quick_check_ok
        and sqlite.foreign_key_violation_count == 0
        and sqlite.schema_version == CANONICAL_MIGRATION_CATALOG.latest_version
    )
    if not sqlite_ready:
        add("sqlite_readiness_failed")

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            persisted = _collect_persisted_health(connection)
    finally:
        connection.close()

    now_ms = int(persisted["now_ms"])
    migration = persisted["migration"]
    outbox_counts = persisted["outbox_counts"]
    outbox_attempts = int(persisted["outbox_attempts"])
    outbox_retries = int(persisted["outbox_retries"])
    job_counts = persisted["job_counts"]
    job_attempts = int(persisted["job_attempts"])
    job_retries = int(persisted["job_retries"])
    lease_row = persisted["lease_row"]
    projection_row = persisted["projection_row"]
    backup_age = persisted["backup_age"]
    blobs = persisted["blobs"]
    if not isinstance(migration, dict) or migration.get("version") != sqlite.schema_version:
        add("schema_migration_binding_failed")
    if backup_age is None:
        add("verified_backup_missing")
    elif int(backup_age) > MAX_VERIFIED_BACKUP_AGE_MS:
        add("verified_backup_stale")
    if not isinstance(blobs, dict):
        raise ProductionHealthError("The referenced-blob metric is invalid.")
    if blobs["unverified_count"]:
        add("referenced_blob_unverified")

    outbox_dead = outbox_counts["dead"]
    if outbox_dead:
        add("dead_outbox_present")
    unrecoverable_jobs = job_counts["failed"] + job_counts["dead"]
    if unrecoverable_jobs:
        add("unrecoverable_job_present")
    if _nonnegative(projection_row[6], field="projection error count"):
        add("projection_error_present")

    database_bytes = _nonnegative(
        file_size_probe(database_path), field="database file bytes"
    )
    wal_bytes = _nonnegative(
        file_size_probe(Path(os.fspath(database_path) + "-wal")),
        field="WAL file bytes",
    )
    required_free = MIGRATION_FREE_SPACE_MULTIPLIER * (
        database_bytes + blobs["size_bytes"]
    )
    database_free = _nonnegative(
        free_space_probe(database_path.parent), field="database free bytes"
    )
    blob_free = _nonnegative(free_space_probe(blob_root), field="blob free bytes")
    if database_free < required_free:
        add("database_disk_capacity_low")
    if blob_free < required_free:
        add("blob_disk_capacity_low")

    db_metrics = database_runtime_metrics()
    reason_rows = sorted(reasons)
    status_code = 200 if not reason_rows else 503
    snapshot: dict[str, Any] = {
        "backup": {
            "age_ms": backup_age,
            "latest_verified_present": backup_age is not None,
            "maximum_age_ms": MAX_VERIFIED_BACKUP_AGE_MS,
        },
        "build": {
            "application": "code-assist-v1",
            "application_version": APPLICATION_BUILD_VERSION,
            "config_sha256": config_sha256,
            "migration_catalog_sha256": CANONICAL_MIGRATION_CATALOG.checksum_sha256,
            "schema_migration": migration,
        },
        "database_metrics": db_metrics,
        "failpoints_absent": not failpoints,
        "feature_flags": flags,
        "gate": {
            "reason_codes": reason_rows,
            "status": "ready" if not reason_rows else "not_ready",
        },
        "jobs": {
            "attempt_count": job_attempts,
            "oldest_nonterminal_age_ms": persisted["job_oldest_age"],
            "state_counts": job_counts,
            "retry_count": job_retries,
            "unrecoverable_count": unrecoverable_jobs,
        },
        "leases": {
            "active_count": _nonnegative(lease_row[0], field="active lease count"),
            "released_count": _nonnegative(lease_row[2], field="released lease count"),
            "stale_count": _nonnegative(lease_row[1], field="stale lease count"),
        },
        "observed_at_ms": now_ms,
        "outbox": {
            "attempt_count": outbox_attempts,
            "oldest_nonterminal_age_ms": persisted["outbox_oldest_age"],
            "retry_count": outbox_retries,
            "state_counts": outbox_counts,
        },
        "projection": {
            "built_generation": _nonnegative(
                projection_row[2], field="built projection generation"
            ),
            "desired_generation": _nonnegative(
                projection_row[1], field="desired projection generation"
            ),
            "error_count": _nonnegative(
                projection_row[6], field="projection error count"
            ),
            "lag_generation": _nonnegative(
                projection_row[3], field="projection generation lag"
            ),
            "lag_generation_max": _nonnegative(
                projection_row[4], field="maximum projection generation lag"
            ),
            "lagging_count": _nonnegative(
                projection_row[5], field="lagging projection count"
            ),
            "oldest_lag_age_ms": _age_ms(now_ms, projection_row[7]),
            "row_count": _nonnegative(projection_row[0], field="projection row count"),
        },
        "report_schema": PRODUCTION_HEALTH_SCHEMA,
        "schema_version": PRODUCTION_HEALTH_SCHEMA_VERSION,
        "service_id": service_id,
        "sqlite": {
            "compile_options": list(sqlite.compile_options),
            "database_open": True,
            "foreign_key_violation_count": sqlite.foreign_key_violation_count,
            "fts5_executable": sqlite.fts5_executable,
            "pragmas": {
                "actual": expected_pragmas,
                "expected": dict(sorted(EXPECTED_CONNECTION_PRAGMAS.items())),
            },
            "quick_check_ok": sqlite.quick_check_ok,
            "source_id": sqlite.runtime.source_id,
            "test_runtime_override": sqlite.test_runtime_override,
            "version": sqlite.runtime.version,
        },
        "storage": {
            "blob": {
                "free_bytes": blob_free,
                "referenced": blobs,
            },
            "database": {
                "file_bytes": database_bytes,
                "free_bytes": database_free,
            },
            "eligibility": storage_eligibility,
            "migration_required_free_bytes": required_free,
            "migration_space_multiplier": MIGRATION_FREE_SPACE_MULTIPLIER,
            "wal": {
                "file_bytes": wal_bytes,
                "free_bytes": database_free,
            },
        },
    }
    return snapshot, status_code


class ProductionHealthService:
    """Callable Flask-compatible provider over immutable deployment bindings."""

    __slots__ = (
        "_blob_root",
        "_cache",
        "_cache_observed_at",
        "_config_sha256",
        "_environ",
        "_factory",
        "_lock",
        "_service_id",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        blob_root: str | os.PathLike[str],
        config_sha256: str,
        service_id: str,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._factory = factory
        self._blob_root = os.fspath(blob_root)
        self._config_sha256 = config_sha256
        self._service_id = service_id
        self._environ = environ
        self._lock = threading.Lock()
        self._cache: tuple[dict[str, Any], int] | None = None
        self._cache_observed_at = 0.0

    def __call__(self) -> tuple[dict[str, Any], int]:
        with self._lock:
            observed = time.monotonic()
            if (
                self._cache is not None
                and observed - self._cache_observed_at < HEALTH_CACHE_TTL_SECONDS
            ):
                report, status = self._cache
                return deepcopy(report), status
            try:
                result = build_production_health_snapshot(
                    self._factory,
                    blob_root=self._blob_root,
                    config_sha256=self._config_sha256,
                    service_id=self._service_id,
                    environ=self._environ,
                )
            except Exception:
                result = (
                    {
                        "gate": {
                            "reason_codes": ["health_observation_failed"],
                            "status": "not_ready",
                        },
                        "report_schema": PRODUCTION_HEALTH_SCHEMA,
                        "schema_version": PRODUCTION_HEALTH_SCHEMA_VERSION,
                        "service_id": self._service_id,
                    },
                    503,
                )
            self._cache = (deepcopy(result[0]), result[1])
            self._cache_observed_at = time.monotonic()
            return deepcopy(result[0]), result[1]


__all__ = [
    "APPLICATION_BUILD_VERSION",
    "HEALTH_CACHE_TTL_SECONDS",
    "MAX_VERIFIED_BACKUP_AGE_MS",
    "MIGRATION_FREE_SPACE_MULTIPLIER",
    "PRODUCTION_HEALTH_SCHEMA",
    "PRODUCTION_HEALTH_SCHEMA_VERSION",
    "ProductionHealthError",
    "ProductionHealthService",
    "build_production_health_snapshot",
]
