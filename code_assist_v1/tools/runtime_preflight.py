"""Deterministic P0.4 runtime and storage preflight.

This module is deliberately independent of application configuration and stores.
It operates only on paths supplied by its caller, never bootstraps a database or
directory, and never changes the database journal mode.  The observation
boundaries are injectable so unit tests can exercise every gate without touching
application or live-data paths.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import hashlib
import importlib.metadata
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
from typing import Any
import uuid

from code_assist_v1.knowledge_core.runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
    EXPECTED_CONNECTION_PRAGMAS,
    FAILPOINT_EXACT_NAME,
    FAILPOINT_PREFIX,
)
from code_assist_v1.tools.windows_volume import is_syntactic_unc_path, probe_path


REPORT_SCHEMA = "code_assist.runtime_preflight.v1"
APPROVED_PYTHON_IMPLEMENTATION = "CPython"
APPROVED_PYTHON_VERSION = "3.14.3"
APPROVED_NODE_VERSION = "24.14.1"
FAILURE_EXIT = 2

FEATURE_FLAGS = (
    "KNOWLEDGE_CORE_ENABLED",
    "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA",
    "KNOWLEDGE_CORE_MULTI_WIKI_READS",
    "KNOWLEDGE_CORE_MULTI_WIKI_WRITES",
    "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING",
    "KNOWLEDGE_CORE_READS",
    "KNOWLEDGE_CORE_PERSONAL_WRITES",
    "KNOWLEDGE_CORE_PART_READS",
    "KNOWLEDGE_CORE_PART_WRITES",
    "KNOWLEDGE_CORE_PROJECTIONS",
    "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2",
    "KNOWLEDGE_CORE_HIGHER_SPACES",
    "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE",
    "KNOWLEDGE_CORE_HYBRID_RERANK",
    "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
    "KNOWLEDGE_CORE_TEMPORAL_RANKING",
    "KNOWLEDGE_CORE_TEAM_WRITES",
    "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
)
EXPECTED_PRAGMAS: dict[str, Any] = dict(EXPECTED_CONNECTION_PRAGMAS)

_PRAGMA_REASON_CODES = {
    "foreign_keys": "sqlite_foreign_keys_mismatch",
    "busy_timeout": "sqlite_busy_timeout_mismatch",
    "synchronous": "sqlite_synchronous_mismatch",
    "trusted_schema": "sqlite_trusted_schema_mismatch",
    "recursive_triggers": "sqlite_recursive_triggers_mismatch",
    "temp_store": "sqlite_temp_store_mismatch",
    "wal_autocheckpoint": "sqlite_wal_autocheckpoint_mismatch",
    "journal_mode": "sqlite_journal_mode_mismatch",
}

StorageObserver = Callable[[Mapping[str, str]], Mapping[str, Mapping[str, Any]]]
VolumeProbe = Callable[[str], Mapping[str, Any]]
DirectoryWriteProbe = Callable[[str], Mapping[str, Any]]
FreeSpaceProbe = Callable[[str], int]
SqliteObserver = Callable[[str], Mapping[str, Any]]
PackageProvider = Callable[[], Any]
FactProvider = Callable[[], Mapping[str, Any]]


class _Reasons:
    """Ordered, de-duplicated reason accumulator with safety classification."""

    def __init__(self) -> None:
        self._ordered: list[str] = []
        self._fatal: set[str] = set()

    def add(self, code: str, *, fatal: bool) -> None:
        if code not in self._ordered:
            self._ordered.append(code)
        if fatal:
            self._fatal.add(code)

    @property
    def ordered(self) -> list[str]:
        return list(self._ordered)

    @property
    def has_fatal(self) -> bool:
        return bool(self._fatal)


class _CliInputError(ValueError):
    pass


class _PreflightArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default writes a usage/error string to stderr and exits.
        # Preflight failures instead have one machine-readable output contract.
        raise _CliInputError(message)


def _canonical_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalized_mode(mode: str) -> str:
    aliases = {"dev": "development", "prod": "production"}
    normalized = aliases.get(mode, mode)
    if normalized not in {"development", "production"}:
        raise ValueError("mode must be development or production")
    return normalized


def _path_text(value: str | os.PathLike[str] | None) -> str | None:
    return None if value is None else os.fspath(value)


def _normalize_explicit_path(
    value: str | os.PathLike[str] | None,
    *,
    required_code: str,
    relative_code: str,
    reasons: _Reasons,
) -> str | None:
    raw = _path_text(value)
    if not raw:
        reasons.add(required_code, fatal=True)
        return None
    if is_syntactic_unc_path(raw):
        # Reject the lexical UNC form before resolve/exists/read operations can
        # contact a remote server.  Mapped drives and local reparse targets are
        # still evaluated by the Win32 final-volume probe below.
        reasons.add("syntactic_unc", fatal=True)
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        reasons.add(relative_code, fatal=True)
        return raw
    try:
        # Keep the lexical local path so the volume probe can observe every
        # reparse/junction component.  Path.resolve() here would erase precisely
        # the path evidence this preflight is required to report.
        return os.path.normpath(os.path.abspath(raw))
    except (OSError, RuntimeError, ValueError):
        reasons.add("path_resolution_failed", fatal=True)
        return raw


def _resolve_paths(
    *,
    data_root: str | os.PathLike[str] | None,
    database: str | os.PathLike[str] | None,
    blob_root: str | os.PathLike[str] | None,
    projection_root: str | os.PathLike[str] | None,
    runtime_lock: str | os.PathLike[str] | None,
    part_write_rollout_record: str | os.PathLike[str] | None,
    reasons: _Reasons,
) -> dict[str, str | None]:
    root_raw = _path_text(data_root)
    root: str | None = None
    if root_raw:
        root = _normalize_explicit_path(
            root_raw,
            required_code="data_root_required",
            relative_code="data_root_not_absolute",
            reasons=reasons,
        )
        if root is not None and Path(root).is_absolute():
            database = database or Path(root) / "core" / "knowledge-core.db"
            blob_root = blob_root or Path(root) / "blobs"
            projection_root = projection_root or Path(root) / "projections"
    elif database is None or blob_root is None or projection_root is None:
        reasons.add("explicit_storage_paths_required", fatal=True)

    database_path = _normalize_explicit_path(
        database,
        required_code="database_path_required",
        relative_code="database_path_not_absolute",
        reasons=reasons,
    )
    blob_path = _normalize_explicit_path(
        blob_root,
        required_code="blob_root_required",
        relative_code="blob_root_not_absolute",
        reasons=reasons,
    )
    projection_path = _normalize_explicit_path(
        projection_root,
        required_code="projection_root_required",
        relative_code="projection_root_not_absolute",
        reasons=reasons,
    )
    runtime_lock_path = _normalize_explicit_path(
        runtime_lock,
        required_code="runtime_lock_required",
        relative_code="runtime_lock_not_absolute",
        reasons=reasons,
    )

    rollout_raw = _path_text(part_write_rollout_record)
    rollout_path: str | None = None
    if rollout_raw:
        rollout_path = _normalize_explicit_path(
            rollout_raw,
            required_code="part_write_rollout_record_required",
            relative_code="part_write_rollout_record_not_absolute",
            reasons=reasons,
        )

    return {
        "data_root": root,
        "database": database_path,
        "blob_root": blob_path,
        "projection_root": projection_path,
        "runtime_lock": runtime_lock_path,
        "part_write_rollout_record": rollout_path,
    }


def _observe_storage(paths: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    observations: dict[str, dict[str, Any]] = {}
    for role in ("database", "blob_root", "projection_root", "runtime_lock"):
        path = Path(paths[role])
        try:
            observations[role] = {
                "exists": path.exists(),
                "is_directory": path.is_dir(),
                "is_file": path.is_file(),
            }
        except OSError:
            observations[role] = {
                "exists": False,
                "is_directory": False,
                "is_file": False,
                "observation_failed": True,
            }

    rollout = paths.get("part_write_rollout_record")
    if rollout:
        path = Path(rollout)
        try:
            observations["part_write_rollout_record"] = {
                "exists": path.exists(),
                "is_directory": path.is_dir(),
                "is_file": path.is_file(),
            }
        except OSError:
            observations["part_write_rollout_record"] = {
                "exists": False,
                "is_directory": False,
                "is_file": False,
                "observation_failed": True,
            }
    return observations


def _probe_directory_write(path: str) -> dict[str, bool]:
    """Write, flush, delete, and verify that no probe residue remains."""
    directory = Path(path)
    probe_name = f".code-assist-preflight-{uuid.uuid4().hex}.tmp"
    probe_path_value = directory / probe_name
    write_succeeded = False
    flush_succeeded = False
    cleanup_succeeded = True

    try:
        with probe_path_value.open("xb") as handle:
            handle.write(b"code-assist-runtime-preflight-v1\n")
            handle.flush()
            os.fsync(handle.fileno())
            flush_succeeded = True
        write_succeeded = True
    except OSError:
        pass
    finally:
        if probe_path_value.exists():
            try:
                probe_path_value.unlink()
            except OSError:
                cleanup_succeeded = False

    residue_free = not probe_path_value.exists()
    return {
        "cleanup_succeeded": cleanup_succeeded and residue_free,
        "flush_succeeded": flush_succeeded,
        # Only this probe's own residue is in scope.  Comparing a complete
        # directory listing would turn a legitimate concurrent writer into a
        # false fatal preflight failure.
        "residue_stable": residue_free,
        "write_succeeded": write_succeeded,
    }


def _free_space(path: str) -> int:
    return int(shutil.disk_usage(path).free)


def _installed_distributions() -> list[dict[str, str]]:
    versions: set[tuple[str, str]] = set()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.name
        version = distribution.version
        if name and version:
            versions.add((_canonical_package_name(str(name)), str(version)))
    return [
        {"name": name, "version": version}
        for name, version in sorted(versions, key=lambda pair: (pair[0], pair[1]))
    ]


def _python_fact() -> dict[str, str]:
    return {
        "implementation": platform.python_implementation(),
        "version": ".".join(str(part) for part in sys.version_info[:3]),
    }


def _node_fact() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["node", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return {"available": False, "status": "not_found", "version": None}
    except subprocess.TimeoutExpired:
        return {"available": False, "status": "timeout", "version": None}
    except OSError:
        return {"available": False, "status": "os_error", "version": None}
    if completed.returncode != 0:
        return {"available": False, "status": "nonzero_exit", "version": None}
    version = completed.stdout.strip()
    if version.startswith("v"):
        version = version[1:]
    if not version:
        return {"available": False, "status": "empty_output", "version": None}
    return {"available": True, "status": "available", "version": version}


def _database_path_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _observe_sqlite(database: str) -> dict[str, Any]:
    """Open one existing database read-only and execute the required probes."""
    connection: sqlite3.Connection | None = None
    stage = "open"
    try:
        uri = Path(database).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)

        stage = "checkpoint_config"
        no_checkpoint_option = getattr(
            sqlite3,
            "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE",
            None,
        )
        if no_checkpoint_option is None:
            raise ValueError("required SQLite checkpoint configuration is absent")
        connection.setconfig(no_checkpoint_option, True)
        no_checkpoint_on_close = bool(connection.getconfig(no_checkpoint_option))
        if not no_checkpoint_on_close:
            raise ValueError("required SQLite checkpoint configuration did not persist")

        stage = "apply_pragmas"
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA recursive_triggers = ON")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA wal_autocheckpoint = 0")

        stage = "read_pragmas"
        pragmas = {
            name: connection.execute(f"PRAGMA {name}").fetchone()[0]
            for name in EXPECTED_PRAGMAS
        }
        pragmas["journal_mode"] = str(pragmas["journal_mode"]).lower()

        stage = "runtime"
        version, source_id = connection.execute(
            "SELECT sqlite_version(), sqlite_source_id()"
        ).fetchone()

        stage = "compile_options"
        compile_options = sorted(
            str(row[0]) for row in connection.execute("PRAGMA compile_options")
        )
        fts5_compile_option_used = bool(
            connection.execute(
                "SELECT sqlite_compileoption_used('ENABLE_FTS5')"
            ).fetchone()[0]
        )

        stage = "json"
        json_valid, json_object, json_array, json_invalid = connection.execute(
            "SELECT json_valid(?), json_type(?), json_type(?), json_valid(?)",
            ('{"schema_version":1}', '{"schema_version":1}', "[]", "{"),
        ).fetchone()
        json_probe_ok = (
            int(json_valid) == 1
            and str(json_object) == "object"
            and str(json_array) == "array"
            and int(json_invalid) == 0
        )

        stage = "database_list"
        database_list = [
            {"name": str(name), "path": str(path)}
            for _, name, path in connection.execute("PRAGMA database_list")
        ]
        main_paths = [entry["path"] for entry in database_list if entry["name"] == "main"]
        database_path_match = (
            len(main_paths) == 1
            and _database_path_key(main_paths[0]) == _database_path_key(database)
        )

        stage = "fts5"
        fts5_probe_ok = False
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE temp.code_assist_preflight_fts "
                "USING fts5(content)"
            )
            connection.execute(
                "INSERT INTO temp.code_assist_preflight_fts(content) VALUES (?)",
                ("deterministic runtime preflight",),
            )
            matched = connection.execute(
                "SELECT count(*) FROM temp.code_assist_preflight_fts "
                "WHERE code_assist_preflight_fts MATCH ?",
                ("deterministic",),
            ).fetchone()[0]
            fts5_probe_ok = int(matched) == 1
        finally:
            connection.execute("DROP TABLE IF EXISTS temp.code_assist_preflight_fts")

        stage = "quick_check"
        quick_check_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        quick_check_ok = quick_check_rows == ["ok"]

        stage = "foreign_key_check"
        foreign_key_violation_count = sum(
            1 for _ in connection.execute("PRAGMA foreign_key_check")
        )

        stage = "semantic_edge_baseline"
        semantic_tables = {
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_schema
                    WHERE type='table' AND name IN (
                      'semantic_edge_intent_seed_run','semantic_edge_intent',
                      'knowledge_item','knowledge_space'
                    )"""
            )
        }
        semantic_edge_contract_available = semantic_tables == {
            "semantic_edge_intent_seed_run",
            "semantic_edge_intent",
            "knowledge_item",
            "knowledge_space",
        }
        semantic_edge_baseline_complete = False
        if semantic_edge_contract_available:
            completed = connection.execute(
                """SELECT 1 FROM semantic_edge_intent_seed_run
                    WHERE cause_kind='baseline_backfill' AND scope_kind='global'
                      AND detector_generation=1 AND state='completed'
                    ORDER BY completed_at DESC,id DESC LIMIT 1"""
            ).fetchone()
            missing = connection.execute(
                """SELECT 1 FROM knowledge_item AS item
                   JOIN knowledge_space AS space ON space.id=item.space_id
                  WHERE item.lifecycle_status='active'
                    AND item.curation_status='published' AND space.status='active'
                    AND NOT EXISTS (
                      SELECT 1 FROM semantic_edge_intent AS intent
                       WHERE intent.source_revision_id=item.current_revision_id
                         AND intent.detector_generation=1
                         AND intent.target_kind='local'
                         AND intent.target_space_id=item.space_id
                    ) LIMIT 1"""
            ).fetchone()
            semantic_edge_baseline_complete = completed is not None and missing is None

        return {
            "compile_options": compile_options,
            "database_list": database_list,
            "database_path_match": database_path_match,
            "foreign_key_violation_count": foreign_key_violation_count,
            "fts5_compile_option_used": fts5_compile_option_used,
            "fts5_probe_ok": fts5_probe_ok,
            "json_probe_ok": json_probe_ok,
            "no_checkpoint_on_close": no_checkpoint_on_close,
            "opened": True,
            "pragmas": pragmas,
            "quick_check_ok": quick_check_ok,
            "semantic_edge_baseline_complete": semantic_edge_baseline_complete,
            "semantic_edge_contract_available": semantic_edge_contract_available,
            "source_id": str(source_id),
            "version": str(version),
        }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {"failure_stage": stage, "opened": connection is not None}
    finally:
        if connection is not None:
            connection.close()


def _parse_runtime_lock(path: str) -> tuple[list[dict[str, str]], str]:
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    requirements: dict[str, str] = {}
    for raw_line in raw.decode("utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line)
        if match is None:
            raise ValueError("runtime lock contains a non-exact requirement")
        name = _canonical_package_name(match.group(1))
        version = match.group(2)
        if name in requirements:
            raise ValueError("runtime lock contains a duplicate distribution")
        requirements[name] = version
    if not requirements:
        raise ValueError("runtime lock contains no requirements")
    return (
        [
            {"name": name, "version": requirements[name]}
            for name in sorted(requirements)
        ],
        digest,
    )


def _coerce_installed_packages(value: Any) -> list[dict[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        pairs = [(str(name), str(version)) for name, version in value.items()]
    else:
        for item in value:
            if isinstance(item, Mapping):
                pairs.append((str(item["name"]), str(item["version"])))
            else:
                name, version = item
                pairs.append((str(name), str(version)))
    normalized = {
        (_canonical_package_name(name), version) for name, version in pairs
    }
    return [
        {"name": name, "version": version}
        for name, version in sorted(normalized, key=lambda pair: (pair[0], pair[1]))
    ]


def _sanitize_volume_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    reason_codes = observation.get("reason_codes", [])
    if not isinstance(reason_codes, (list, tuple)):
        reason_codes = ["volume_probe_invalid"]
    sanitized: dict[str, Any] = {
        "eligible": observation.get("eligible") is True,
        "exists": observation.get("exists") is True,
        "reason_codes": sorted({str(code) for code in reason_codes}),
    }
    # Preserve only stable, non-secret filesystem facts.  In particular, never
    # forward raw exception text or an implementation executable path.
    for key in (
        "drive_type",
        "filesystem",
        "final_is_reparse_point",
        "is_local",
        "is_reparse_point",
        "volume_kind",
    ):
        value = observation.get(key)
        if isinstance(value, (bool, int, str)) or value is None:
            sanitized[key] = value
    reparse = observation.get("reparse")
    if isinstance(reparse, Mapping):
        sanitized["reparse_present"] = reparse.get("present") is True
    volume = observation.get("volume")
    if isinstance(volume, Mapping):
        sanitized["drive_type"] = volume.get("drive_type")
        sanitized["filesystem"] = volume.get("filesystem")
    syntactic_unc = observation.get("syntactic_unc")
    if isinstance(syntactic_unc, bool):
        sanitized["syntactic_unc"] = syntactic_unc
    resolved_path = observation.get("resolved_path")
    if isinstance(resolved_path, str):
        sanitized["resolved_path"] = resolved_path
    if isinstance(volume, Mapping) and isinstance(volume.get("root"), str):
        sanitized["volume_root"] = str(volume["root"])
    return sanitized


def _sanitize_sqlite_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in (
        "database_path_match",
        "foreign_key_violation_count",
        "fts5_compile_option_used",
        "fts5_probe_ok",
        "json_probe_ok",
        "no_checkpoint_on_close",
        "opened",
        "quick_check_ok",
        "semantic_edge_baseline_complete",
        "semantic_edge_contract_available",
        "source_id",
        "version",
    ):
        value = observation.get(key)
        if isinstance(value, (bool, int, str)) or value is None:
            sanitized[key] = value
    compile_options = observation.get("compile_options")
    if isinstance(compile_options, (list, tuple)):
        sanitized["compile_options"] = sorted(str(item) for item in compile_options)
    pragmas = observation.get("pragmas")
    if isinstance(pragmas, Mapping):
        sanitized["pragmas"] = {
            name: pragmas.get(name) for name in EXPECTED_PRAGMAS
        }
    database_list = observation.get("database_list")
    if isinstance(database_list, (list, tuple)):
        rows: list[dict[str, str]] = []
        for row in database_list:
            if isinstance(row, Mapping) and "name" in row and "path" in row:
                rows.append({"name": str(row["name"]), "path": str(row["path"])})
        sanitized["database_list"] = rows
    failure_stage = observation.get("failure_stage")
    if isinstance(failure_stage, str):
        sanitized["failure_stage"] = failure_stage
    return sanitized


def _safe_bool(observation: Mapping[str, Any], key: str) -> bool:
    return observation.get(key) is True


def _windows_path_key(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path.replace("/", "\\")))


def _same_or_descendant_windows_path(candidate: str, directory: str) -> bool:
    candidate_key = _windows_path_key(candidate)
    directory_key = _windows_path_key(directory)
    try:
        common = ntpath.commonpath((candidate_key, directory_key))
    except ValueError:
        return False
    return _windows_path_key(common) == directory_key


def build_report(
    mode: str,
    *,
    data_root: str | os.PathLike[str] | None = None,
    database: str | os.PathLike[str] | None = None,
    blob_root: str | os.PathLike[str] | None = None,
    projection_root: str | os.PathLike[str] | None = None,
    runtime_lock: str | os.PathLike[str] | None = None,
    part_write_rollout_record: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    volume_probe: VolumeProbe = probe_path,
    free_space_probe: FreeSpaceProbe = _free_space,
    package_provider: PackageProvider = _installed_distributions,
    sqlite_observer: SqliteObserver = _observe_sqlite,
    storage_observer: StorageObserver = _observe_storage,
    directory_write_probe: DirectoryWriteProbe = _probe_directory_write,
    python_provider: FactProvider = _python_fact,
    node_provider: FactProvider = _node_fact,
) -> tuple[dict[str, Any], int]:
    """Build the deterministic report and intended process exit code."""
    normalized_mode = _normalized_mode(mode)
    environment = os.environ if environ is None else environ
    reasons = _Reasons()

    paths = _resolve_paths(
        data_root=data_root,
        database=database,
        blob_root=blob_root,
        projection_root=projection_root,
        runtime_lock=runtime_lock,
        part_write_rollout_record=part_write_rollout_record,
        reasons=reasons,
    )

    flags: dict[str, int | None] = {}
    for name in FEATURE_FLAGS:
        raw_value = environment.get(name, "0")
        if raw_value not in {"0", "1"}:
            flags[name] = None
            reasons.add("feature_flag_invalid", fatal=True)
        else:
            flags[name] = int(raw_value)
    if flags["KNOWLEDGE_CORE_HIGHER_SPACES"] == 1 and not all(
        flags[name] == 1
        for name in (
            "KNOWLEDGE_CORE_ENABLED",
            "KNOWLEDGE_CORE_READS",
            "KNOWLEDGE_CORE_PART_READS",
        )
    ):
        reasons.add("higher_spaces_prerequisite_missing", fatal=True)
    for write_flag in (
        "KNOWLEDGE_CORE_TEAM_WRITES",
        "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
    ):
        if flags[write_flag] == 1 and flags["KNOWLEDGE_CORE_HIGHER_SPACES"] != 1:
            reasons.add("higher_write_prerequisite_missing", fatal=True)
    if (
        flags["KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS"] == 1
        and flags["KNOWLEDGE_CORE_HIGHER_SPACES"] != 1
    ):
        reasons.add("aggregate_graph_prerequisite_missing", fatal=True)
    if flags["KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE"] == 1 and not all(
        flags[name] == 1
        for name in (
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
            "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
        )
    ):
        reasons.add("aggregate_semantic_edge_prerequisite_missing", fatal=True)
    if flags["KNOWLEDGE_CORE_HYBRID_RERANK"] == 1 and not all(
        flags[name] == 1
        for name in (
            "KNOWLEDGE_CORE_ENABLED",
            "KNOWLEDGE_CORE_READS",
        )
    ):
        reasons.add("hybrid_rerank_prerequisite_missing", fatal=True)
    if flags["KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER"] == 1 and not all(
        flags[name] == 1
        for name in (
            "KNOWLEDGE_CORE_ENABLED",
            "KNOWLEDGE_CORE_HYBRID_RERANK",
        )
    ):
        reasons.add("local_cross_encoder_prerequisite_missing", fatal=True)
    if flags["KNOWLEDGE_CORE_TEMPORAL_RANKING"] == 1 and not all(
        flags[name] == 1
        for name in (
            "KNOWLEDGE_CORE_ENABLED",
            "KNOWLEDGE_CORE_HYBRID_RERANK",
        )
    ):
        reasons.add("temporal_ranking_prerequisite_missing", fatal=True)
    if flags["KNOWLEDGE_CORE_PROJECTION_FOOTER_V2"] == 1 and not all(
        flags[name] == 1
        for name in ("KNOWLEDGE_CORE_ENABLED", "KNOWLEDGE_CORE_PROJECTIONS")
    ):
        reasons.add("projection_footer_v2_prerequisite_missing", fatal=True)

    failpoint_names = sorted(
        name
        for name in environment
        if name == FAILPOINT_EXACT_NAME or name.startswith(FAILPOINT_PREFIX)
    )
    if failpoint_names:
        reasons.add("failpoint_configuration_present", fatal=True)

    try:
        python_observation = python_provider()
    except Exception:
        python_observation = {}
        reasons.add("dependency_observation_failed", fatal=False)
    if not isinstance(python_observation, Mapping):
        python_observation = {}
        reasons.add("dependency_observation_failed", fatal=False)
    python_fact = {
        "implementation": (
            str(python_observation.get("implementation"))
            if python_observation.get("implementation") is not None
            else None
        ),
        "version": (
            str(python_observation.get("version"))
            if python_observation.get("version") is not None
            else None
        ),
    }
    python_fact["approved"] = {
        "implementation": APPROVED_PYTHON_IMPLEMENTATION,
        "version": APPROVED_PYTHON_VERSION,
    }
    python_fact["exact_match"] = (
        python_fact.get("implementation") == APPROVED_PYTHON_IMPLEMENTATION
        and python_fact.get("version") == APPROVED_PYTHON_VERSION
    )
    if python_fact.get("implementation") != APPROVED_PYTHON_IMPLEMENTATION:
        reasons.add("python_implementation_mismatch", fatal=False)
    if python_fact.get("version") != APPROVED_PYTHON_VERSION:
        reasons.add("python_version_mismatch", fatal=False)

    try:
        node_observation = node_provider()
    except Exception:
        node_observation = {}
        reasons.add("dependency_observation_failed", fatal=False)
    if not isinstance(node_observation, Mapping):
        node_observation = {}
        reasons.add("dependency_observation_failed", fatal=False)
    node_fact = {
        "available": node_observation.get("available") is True,
        "status": (
            str(node_observation.get("status"))
            if node_observation.get("status") is not None
            else None
        ),
        "version": (
            str(node_observation.get("version"))
            if node_observation.get("version") is not None
            else None
        ),
    }
    node_fact["verified_baseline_version"] = APPROVED_NODE_VERSION
    node_fact["baseline_match"] = (
        node_fact.get("available") is True
        and node_fact.get("version") == APPROVED_NODE_VERSION
    )

    lock_report: dict[str, Any] = {
        "ambiguous_distributions": [],
        "exact_match": False,
        "missing": [],
        "requirements": [],
        "sha256": None,
        "version_mismatches": [],
    }
    installed_report: list[dict[str, str]] = []
    lock_path = paths.get("runtime_lock")
    if lock_path is not None and Path(lock_path).is_absolute():
        try:
            requirements, lock_hash = _parse_runtime_lock(lock_path)
        except (OSError, UnicodeError, ValueError):
            reasons.add("runtime_lock_invalid", fatal=True)
        else:
            try:
                installed_report = _coerce_installed_packages(package_provider())
            except Exception:
                installed_report = []
                reasons.add("dependency_observation_failed", fatal=False)
            versions_by_name: dict[str, set[str]] = {}
            for item in installed_report:
                versions_by_name.setdefault(item["name"], set()).add(item["version"])
            ambiguous = [
                {"name": name, "versions": sorted(versions)}
                for name, versions in sorted(versions_by_name.items())
                if len(versions) > 1
            ]
            installed_by_name = {
                item["name"]: item["version"] for item in installed_report
            }
            missing = [
                item["name"] for item in requirements if item["name"] not in installed_by_name
            ]
            mismatches = [
                {
                    "installed": installed_by_name[item["name"]],
                    "name": item["name"],
                    "required": item["version"],
                }
                for item in requirements
                if item["name"] in installed_by_name
                and installed_by_name[item["name"]] != item["version"]
            ]
            lock_report = {
                "ambiguous_distributions": ambiguous,
                "exact_match": not missing and not mismatches and not ambiguous,
                "missing": missing,
                "requirements": requirements,
                "sha256": lock_hash,
                "version_mismatches": mismatches,
            }
            if missing:
                reasons.add("dependency_missing", fatal=False)
            if mismatches:
                reasons.add("dependency_version_mismatch", fatal=False)
            if ambiguous:
                reasons.add("dependency_ambiguous_distribution", fatal=False)

    valid_storage_paths = all(
        isinstance(paths.get(role), str) and Path(str(paths[role])).is_absolute()
        for role in ("database", "blob_root", "projection_root", "runtime_lock")
    )
    storage_report: dict[str, Any] = {"status": "skipped"}
    storage_ok = False
    storage_facts: Mapping[str, Mapping[str, Any]] = {}
    if valid_storage_paths:
        try:
            storage_facts = storage_observer(
                {
                    role: str(paths[role])
                    for role in (
                        "database",
                        "blob_root",
                        "projection_root",
                        "runtime_lock",
                        "part_write_rollout_record",
                    )
                    if paths.get(role) is not None
                    and Path(str(paths[role])).is_absolute()
                }
            )
        except Exception:
            storage_facts = {}
            reasons.add("storage_observation_failed", fatal=True)

        if not isinstance(storage_facts, Mapping):
            storage_facts = {}
            reasons.add("storage_observation_failed", fatal=True)

        expected_kinds = {
            "database": "is_file",
            "blob_root": "is_directory",
            "projection_root": "is_directory",
            "runtime_lock": "is_file",
        }
        storage_report = {}
        storage_ok = True
        for role, kind_key in expected_kinds.items():
            fact = dict(storage_facts.get(role, {}))
            storage_report[role] = {
                "exists": fact.get("exists") is True,
                "kind_matches": fact.get(kind_key) is True,
            }
            if fact.get("exists") is not True:
                reasons.add(f"{role}_missing", fatal=True)
                storage_ok = False
            elif fact.get(kind_key) is not True:
                reasons.add(f"{role}_wrong_kind", fatal=True)
                storage_ok = False

    rollout_fact = dict(storage_facts.get("part_write_rollout_record", {}))
    if flags["KNOWLEDGE_CORE_PART_WRITES"] == 1:
        if not paths.get("part_write_rollout_record") or rollout_fact.get("is_file") is not True:
            reasons.add("part_write_rollout_missing", fatal=True)
        else:
            # P0.4 has no authority or complete schema for validating the P1-P7
            # evidence packet.  Presence alone must never enable shared writes.
            reasons.add("part_write_rollout_unverifiable_p0", fatal=True)

    filesystem_report: dict[str, Any] = {
        "directories": [],
        "free_space": [],
        "storage": storage_report,
        "volumes": [],
    }
    volume_ok = storage_ok
    volumes_by_role: dict[str, dict[str, Any]] = {}
    if storage_ok:
        database_path = str(paths["database"])
        locations = (
            ("database", database_path),
            ("database_parent", str(Path(database_path).parent)),
            ("blob_root", str(paths["blob_root"])),
            ("projection_root", str(paths["projection_root"])),
        )
        for role, path in locations:
            try:
                volume = _sanitize_volume_observation(volume_probe(path))
            except Exception:
                volume = {"eligible": False, "reason_codes": ["volume_probe_failed"]}
            filesystem_report["volumes"].append({"role": role, **volume})
            volumes_by_role[role] = volume
            if volume["eligible"] is not True:
                volume_ok = False
                codes = volume["reason_codes"] or ["volume_ineligible"]
                for code in codes:
                    reasons.add(str(code), fatal=True)
            elif not isinstance(volume.get("resolved_path"), str) or not isinstance(
                volume.get("volume_root"), str
            ):
                volume_ok = False
                reasons.add("volume_probe_incomplete", fatal=True)

    if volume_ok:
        for role in ("database_parent", "blob_root", "projection_root"):
            fact = volumes_by_role[role]
            if _windows_path_key(str(fact["resolved_path"])) == _windows_path_key(
                str(fact["volume_root"])
            ):
                reasons.add(f"{role}_is_volume_root", fatal=True)
                volume_ok = False

        database_final = str(volumes_by_role["database"]["resolved_path"])
        blob_final = str(volumes_by_role["blob_root"]["resolved_path"])
        projection_final = str(
            volumes_by_role["projection_root"]["resolved_path"]
        )
        if _same_or_descendant_windows_path(database_final, blob_final):
            reasons.add("database_inside_blob_root", fatal=True)
            volume_ok = False
        if _same_or_descendant_windows_path(database_final, projection_final):
            reasons.add("database_inside_projection_root", fatal=True)
            volume_ok = False
        if _same_or_descendant_windows_path(
            blob_final, projection_final
        ) or _same_or_descendant_windows_path(projection_final, blob_final):
            reasons.add("blob_projection_overlap", fatal=True)
            volume_ok = False

    filesystem_runtime_ok = volume_ok
    if volume_ok:
        directories = (
            ("database_parent", str(Path(str(paths["database"])).parent)),
            ("blob_root", str(paths["blob_root"])),
            ("projection_root", str(paths["projection_root"])),
        )
        for role, path in directories:
            try:
                write_fact = dict(directory_write_probe(path))
            except Exception:
                write_fact = {}
            fact_report = {
                "cleanup_succeeded": _safe_bool(write_fact, "cleanup_succeeded"),
                "flush_succeeded": _safe_bool(write_fact, "flush_succeeded"),
                "residue_stable": _safe_bool(write_fact, "residue_stable"),
                "role": role,
                "write_succeeded": _safe_bool(write_fact, "write_succeeded"),
            }
            filesystem_report["directories"].append(fact_report)
            if not fact_report["write_succeeded"] or not fact_report["flush_succeeded"]:
                reasons.add("directory_write_probe_failed", fatal=True)
                filesystem_runtime_ok = False
            if not fact_report["cleanup_succeeded"]:
                reasons.add("directory_probe_cleanup_failed", fatal=True)
                filesystem_runtime_ok = False
            if not fact_report["residue_stable"]:
                reasons.add("directory_probe_residue_unstable", fatal=True)
                filesystem_runtime_ok = False

            try:
                free_bytes = int(free_space_probe(path))
                if free_bytes < 0:
                    raise ValueError("free bytes cannot be negative")
                filesystem_report["free_space"].append(
                    {"free_bytes": free_bytes, "role": role}
                )
            except Exception:
                filesystem_report["free_space"].append(
                    {"free_bytes": None, "role": role}
                )
                reasons.add("free_space_probe_failed", fatal=True)
                filesystem_runtime_ok = False

    sqlite_report: dict[str, Any] = {"status": "skipped"}
    if filesystem_runtime_ok:
        try:
            sqlite_report = _sanitize_sqlite_observation(
                sqlite_observer(str(paths["database"]))
            )
        except Exception:
            sqlite_report = {"failure_stage": "observer", "opened": False}

        if sqlite_report.get("opened") is not True:
            reasons.add("database_open_failed", fatal=True)
        else:
            failure_stage = sqlite_report.get("failure_stage")
            if failure_stage == "database_list":
                reasons.add("sqlite_database_path_probe_failed", fatal=True)
            elif failure_stage == "quick_check":
                reasons.add("sqlite_quick_check_failed", fatal=True)
            elif failure_stage == "foreign_key_check":
                reasons.add("sqlite_foreign_key_check_failed", fatal=True)
            elif failure_stage is not None:
                reasons.add("sqlite_probe_failed", fatal=False)
            else:
                required_observations = {
                    "compile_options",
                    "database_path_match",
                    "foreign_key_violation_count",
                    "fts5_compile_option_used",
                    "fts5_probe_ok",
                    "json_probe_ok",
                    "no_checkpoint_on_close",
                    "pragmas",
                    "quick_check_ok",
                    "source_id",
                    "version",
                }
                if not required_observations.issubset(sqlite_report):
                    reasons.add("sqlite_probe_incomplete", fatal=False)
                else:
                    if sqlite_report.get("version") != APPROVED_SQLITE_VERSION:
                        reasons.add("sqlite_version_mismatch", fatal=False)
                    if sqlite_report.get("source_id") != APPROVED_SQLITE_SOURCE_ID:
                        reasons.add("sqlite_source_id_mismatch", fatal=False)

                    compile_options = sqlite_report.get("compile_options")
                    if not isinstance(compile_options, list):
                        compile_options = []
                    sqlite_report["compile_options"] = sorted(
                        str(item) for item in compile_options
                    )
                    if (
                        sqlite_report.get("fts5_compile_option_used") is not True
                        or "ENABLE_FTS5" not in sqlite_report["compile_options"]
                    ):
                        reasons.add("sqlite_enable_fts5_missing", fatal=False)
                    if sqlite_report.get("fts5_probe_ok") is not True:
                        reasons.add("sqlite_fts5_probe_failed", fatal=False)
                    if sqlite_report.get("json_probe_ok") is not True:
                        reasons.add("sqlite_json_probe_failed", fatal=False)
                    if sqlite_report.get("no_checkpoint_on_close") is not True:
                        reasons.add(
                            "sqlite_no_checkpoint_on_close_failed",
                            fatal=False,
                        )

                    pragma_facts = sqlite_report.get("pragmas")
                    if not isinstance(pragma_facts, Mapping):
                        pragma_facts = {}
                    for pragma_name, expected in EXPECTED_PRAGMAS.items():
                        actual = pragma_facts.get(pragma_name)
                        if pragma_name == "journal_mode" and isinstance(actual, str):
                            actual = actual.lower()
                        if actual != expected:
                            reasons.add(
                                _PRAGMA_REASON_CODES[pragma_name], fatal=False
                            )

                    if sqlite_report.get("database_path_match") is not True:
                        reasons.add("sqlite_database_path_mismatch", fatal=True)
                    if sqlite_report.get("quick_check_ok") is not True:
                        reasons.add("sqlite_quick_check_failed", fatal=True)
                    if sqlite_report.get("foreign_key_violation_count") != 0:
                        reasons.add("sqlite_foreign_key_check_failed", fatal=True)
                    if (
                        flags["KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL"] == 1
                        and sqlite_report.get("semantic_edge_baseline_complete") is not True
                    ):
                        reasons.add(
                            "semantic_edge_baseline_incomplete",
                            fatal=True,
                        )

            sqlite_report["approved"] = {
                "source_id": APPROVED_SQLITE_SOURCE_ID,
                "version": APPROVED_SQLITE_VERSION,
            }
            sqlite_report["exact_build_match"] = (
                sqlite_report.get("version") == APPROVED_SQLITE_VERSION
                and sqlite_report.get("source_id") == APPROVED_SQLITE_SOURCE_ID
            )
            sqlite_report["expected_pragmas"] = dict(EXPECTED_PRAGMAS)

    if reasons.has_fatal:
        gate_status = "fail"
        exit_code = FAILURE_EXIT
        message = "Preflight found an unsafe input or runtime condition."
    elif reasons.ordered and normalized_mode == "production":
        gate_status = "fail"
        exit_code = FAILURE_EXIT
        message = "Production is blocked by a runtime contract mismatch."
    elif reasons.ordered:
        gate_status = "warning"
        exit_code = 0
        message = "Development may continue with runtime contract warnings."
    else:
        gate_status = "pass"
        exit_code = 0
        message = "All P0.4 runtime preflight checks passed."

    report: dict[str, Any] = {
        "dependencies": {
            "installed_distributions": installed_report,
            "node": node_fact,
            "python": python_fact,
            "runtime_lock": lock_report,
        },
        "failpoints": {"names": failpoint_names, "present": bool(failpoint_names)},
        "feature_flags": flags,
        "filesystem": filesystem_report,
        "gate": {
            "exit_code": exit_code,
            "message": message,
            "reasons": reasons.ordered,
            "status": gate_status,
        },
        "mode": normalized_mode,
        "paths": paths,
        "report_schema": REPORT_SCHEMA,
        "scope": "p0.4-runtime-preflight-only",
        "sqlite": sqlite_report,
    }
    return report, exit_code


def canonical_json(report: Mapping[str, Any]) -> str:
    """Return the one-line canonical representation used by the CLI."""
    return json.dumps(
        report,
        # ASCII escaping makes the byte contract independent of a Windows
        # console/code-page setting while json.loads still restores exact paths.
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _failure_report(reason: str, mode: str = "development") -> dict[str, Any]:
    try:
        normalized_mode = _normalized_mode(mode)
    except ValueError:
        normalized_mode = "development"
    flags = {
        name: int(os.environ.get(name, "0"))
        if os.environ.get(name, "0") in {"0", "1"}
        else None
        for name in FEATURE_FLAGS
    }
    failpoint_names = sorted(
        name
        for name in os.environ
        if name == FAILPOINT_EXACT_NAME or name.startswith(FAILPOINT_PREFIX)
    )
    return {
        "dependencies": {"status": "skipped"},
        "failpoints": {
            "names": failpoint_names,
            "present": bool(failpoint_names),
        },
        "feature_flags": flags,
        "filesystem": {"status": "skipped"},
        "gate": {
            "exit_code": FAILURE_EXIT,
            "message": "Preflight could not safely evaluate the supplied inputs.",
            "reasons": [reason],
            "status": "fail",
        },
        "mode": normalized_mode,
        "paths": {},
        "report_schema": REPORT_SCHEMA,
        "scope": "p0.4-runtime-preflight-only",
        "sqlite": {"status": "skipped"},
    }


def _requested_mode(argv: Sequence[str] | None) -> str:
    arguments = list(sys.argv[1:] if argv is None else argv)
    requested: str | None = None
    for index, argument in enumerate(arguments):
        if argument == "--mode" and index + 1 < len(arguments):
            requested = arguments[index + 1]
        elif argument.startswith("--mode="):
            requested = argument.partition("=")[2]
    try:
        return _normalized_mode(requested or "development")
    except ValueError:
        return "development"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _PreflightArgumentParser(
        description="Run deterministic P0.4 knowledge-core runtime preflight."
    )
    parser.add_argument(
        "--mode",
        choices=("development", "production", "dev", "prod"),
        default="development",
    )
    parser.add_argument("--data-root")
    parser.add_argument("--database")
    parser.add_argument("--blob-root")
    parser.add_argument("--projection-root")
    parser.add_argument("--runtime-lock", required=True)
    parser.add_argument("--part-write-rollout-record")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    requested_mode = _requested_mode(argv)
    try:
        args = _parse_args(argv)
        report, exit_code = build_report(
            args.mode,
            data_root=args.data_root,
            database=args.database,
            blob_root=args.blob_root,
            projection_root=args.projection_root,
            runtime_lock=args.runtime_lock,
            part_write_rollout_record=args.part_write_rollout_record,
        )
    except _CliInputError:
        report = _failure_report("invalid_cli_arguments", requested_mode)
        exit_code = FAILURE_EXIT
    except Exception:
        # No exception text is emitted: it may contain paths, environment data,
        # or version-specific details and would break deterministic output.
        report = _failure_report("preflight_internal_error", requested_mode)
        exit_code = FAILURE_EXIT
    sys.stdout.write(canonical_json(report))
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
