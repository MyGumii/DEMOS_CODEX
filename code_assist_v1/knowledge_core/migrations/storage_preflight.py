"""Fail-closed storage-layout gate for the deployment migrator.

The operator-facing P0.4 preflight remains diagnostic.  This module is the
migration-time enforcement boundary.  It observes the explicit data, database,
blob, and projection paths itself, validates their final Win32 targets, and
binds the observations across database creation/open.  No report or Boolean
attestation supplied by a caller is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from ..errors import KnowledgeCoreDatabasePathError


@dataclass(frozen=True, slots=True)
class MigrationStorageLayout:
    """The four explicit canonical storage targets required by schema v1."""

    data_root: Path
    database: Path
    blob_root: Path
    projection_root: Path

    def __post_init__(self) -> None:
        for field_name in (
            "data_root",
            "database",
            "blob_root",
            "projection_root",
        ):
            value = getattr(self, field_name)
            try:
                raw = os.fspath(value)
            except TypeError as exc:
                raise KnowledgeCoreDatabasePathError(
                    "Every migration storage path must be an explicit filesystem path."
                ) from exc
            if not raw or _is_syntactic_unc(raw):
                raise KnowledgeCoreDatabasePathError(
                    "Migration storage paths cannot be empty or syntactic UNC paths."
                )
            candidate = Path(raw)
            if not candidate.is_absolute():
                raise KnowledgeCoreDatabasePathError(
                    "Every migration storage path must be absolute."
                )
            try:
                # Preserve the lexical path so probe_path can observe junctions and
                # reparse components.  The final-target facts are bound separately.
                normalized = Path(os.path.normpath(os.path.abspath(raw)))
            except (OSError, RuntimeError, ValueError) as exc:
                raise KnowledgeCoreDatabasePathError(
                    "A migration storage path could not be normalized."
                ) from exc
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class MigrationPathBoundary:
    """Locally observed final path and fixed NTFS volume identity."""

    resolved_path: str
    volume_root: str


@dataclass(frozen=True, slots=True)
class MigrationStorageBoundary:
    """Complete layout observation bound across migration open/bootstrap."""

    data_root: MigrationPathBoundary
    database_parent: MigrationPathBoundary
    database: MigrationPathBoundary
    blob_root: MigrationPathBoundary
    projection_root: MigrationPathBoundary
    database_file_identity: tuple[int, int] | None


def _is_syntactic_unc(value: str) -> bool:
    normalized = value.replace("/", "\\")
    return normalized.startswith("\\\\")


def _normalized_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def _same_or_descendant_windows_path(candidate: str, parent: str) -> bool:
    try:
        return ntpath.commonpath((candidate, parent)) == parent
    except ValueError:
        return False


def _eligible_path_facts(report: Mapping[str, Any]) -> MigrationPathBoundary:
    volume = report.get("volume")
    if (
        report.get("eligible") is not True
        or report.get("exists") is not True
        or report.get("syntactic_unc") is not False
        or not isinstance(report.get("resolved_path"), str)
        or not isinstance(volume, Mapping)
        or volume.get("drive_type_code") != 3
        or not isinstance(volume.get("filesystem"), str)
        or str(volume["filesystem"]).casefold() != "ntfs"
        or not isinstance(volume.get("root"), str)
    ):
        raise KnowledgeCoreDatabasePathError(
            "Migration storage must resolve to an existing local fixed NTFS path."
        )
    resolved = _normalized_windows_path(str(report["resolved_path"]))
    volume_root = _normalized_windows_path(str(volume["root"]))
    if not resolved or not volume_root:
        raise KnowledgeCoreDatabasePathError(
            "The migration storage probe returned incomplete path facts."
        )
    return MigrationPathBoundary(resolved, volume_root)


def _probe_existing_directory(path: Path) -> MigrationPathBoundary:
    # Lazy import preserves the side-effect-free Knowledge Core package import.
    from ...tools.windows_volume import probe_path

    facts = _eligible_path_facts(probe_path(path))
    if not path.is_dir():
        raise KnowledgeCoreDatabasePathError(
            "Every migration storage directory must already exist."
        )
    if facts.resolved_path == facts.volume_root:
        raise KnowledgeCoreDatabasePathError(
            "A migration storage directory cannot be a filesystem root."
        )
    return facts


def _database_file_identity(path: Path) -> tuple[int, int]:
    try:
        facts = path.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise KnowledgeCoreDatabasePathError(
            "The migration database file cannot be inspected."
        ) from exc
    if not stat.S_ISREG(facts.st_mode) or facts.st_nlink != 1:
        raise KnowledgeCoreDatabasePathError(
            "The migration database must be a regular file with one hard link."
        )
    return int(facts.st_dev), int(facts.st_ino)


def _verify_layout_relationships(boundary: MigrationStorageBoundary) -> None:
    data = boundary.data_root.resolved_path
    database = boundary.database.resolved_path
    database_parent = boundary.database_parent.resolved_path
    blob = boundary.blob_root.resolved_path
    projection = boundary.projection_root.resolved_path

    # Schema v1 gives data_root a concrete role: every canonical storage target
    # lives below it.  Final targets, rather than lexical spellings, decide this.
    for child in (database_parent, database, blob, projection):
        if not _same_or_descendant_windows_path(child, data) or child == data:
            raise KnowledgeCoreDatabasePathError(
                "Canonical database, blob, and projection targets must be below data_root."
            )

    if _same_or_descendant_windows_path(database, blob):
        raise KnowledgeCoreDatabasePathError(
            "The canonical database cannot be inside the blob root."
        )
    if _same_or_descendant_windows_path(database, projection):
        raise KnowledgeCoreDatabasePathError(
            "The canonical database cannot be inside the projection root."
        )
    if _same_or_descendant_windows_path(blob, projection) or (
        _same_or_descendant_windows_path(projection, blob)
    ):
        raise KnowledgeCoreDatabasePathError(
            "Blob and projection roots cannot overlap."
        )


def verify_migration_storage_layout(
    layout: MigrationStorageLayout,
    *,
    require_database: bool,
    expected: MigrationStorageBoundary | None = None,
) -> MigrationStorageBoundary:
    """Observe all four paths and bind final targets across create/open.

    ``require_database=False`` is the sole empty-bootstrap shape.  It still
    requires the exact final database parent plus every canonical directory to
    exist.  A second call with ``require_database=True`` captures and checks the
    created file identity before schema-write authority can be granted.
    """

    if not isinstance(layout, MigrationStorageLayout):
        raise KnowledgeCoreDatabasePathError(
            "SingleMigrator requires an explicit MigrationStorageLayout."
        )

    data = _probe_existing_directory(layout.data_root)
    database_parent = _probe_existing_directory(layout.database.parent)
    blob = _probe_existing_directory(layout.blob_root)
    projection = _probe_existing_directory(layout.projection_root)

    expected_database_path = _normalized_windows_path(
        ntpath.join(database_parent.resolved_path, layout.database.name)
    )
    if expected_database_path == database_parent.volume_root:
        raise KnowledgeCoreDatabasePathError(
            "The migration database cannot resolve to a filesystem root."
        )

    identity: tuple[int, int] | None = None
    database_exists = layout.database.exists()
    if os.path.lexists(layout.database) and not database_exists:
        raise KnowledgeCoreDatabasePathError(
            "The migration database target is a dangling or unobservable filesystem entry."
        )
    if database_exists:
        from ...tools.windows_volume import probe_path

        database = _eligible_path_facts(probe_path(layout.database))
        if (
            database.resolved_path != expected_database_path
            or database.volume_root != database_parent.volume_root
        ):
            raise KnowledgeCoreDatabasePathError(
                "The migration database final target differs from its parent binding."
            )
        identity = _database_file_identity(layout.database)
    else:
        if require_database:
            raise KnowledgeCoreDatabasePathError(
                "The migration database was not created at the verified target."
            )
        database = MigrationPathBoundary(
            expected_database_path,
            database_parent.volume_root,
        )

    boundary = MigrationStorageBoundary(
        data_root=data,
        database_parent=database_parent,
        database=database,
        blob_root=blob,
        projection_root=projection,
        database_file_identity=identity,
    )
    _verify_layout_relationships(boundary)

    if expected is not None:
        if not isinstance(expected, MigrationStorageBoundary):
            raise KnowledgeCoreDatabasePathError(
                "The expected migration storage binding is invalid."
            )
        if (
            boundary.data_root != expected.data_root
            or boundary.database_parent != expected.database_parent
            or boundary.database != expected.database
            or boundary.blob_root != expected.blob_root
            or boundary.projection_root != expected.projection_root
        ):
            raise KnowledgeCoreDatabasePathError(
                "The migration storage layout changed between preflight and open."
            )
        if expected.database_file_identity is not None and (
            boundary.database_file_identity != expected.database_file_identity
        ):
            raise KnowledgeCoreDatabasePathError(
                "The migration database file identity changed during open."
            )
        if require_database and boundary.database_file_identity is None:
            raise KnowledgeCoreDatabasePathError(
                "The migration database file identity could not be captured."
            )

    return boundary


def verify_migration_storage(
    database_path: Path,
    *,
    require_database: bool,
    expected: MigrationStorageBoundary | None = None,
) -> MigrationStorageBoundary:
    """Removed single-path compatibility surface; fail closed with guidance."""

    del database_path, require_database, expected
    raise KnowledgeCoreDatabasePathError(
        "The migration gate requires explicit data, database, blob, and projection paths."
    )


__all__ = [
    "MigrationPathBoundary",
    "MigrationStorageBoundary",
    "MigrationStorageLayout",
    "verify_migration_storage",
    "verify_migration_storage_layout",
]
