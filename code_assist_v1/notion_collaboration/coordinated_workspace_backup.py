"""O1A-P1 isolated coordinated backup and new-root restore.

The Notion collaboration catalog and the Second Brain database remain separate
canonical authorities.  This module binds one atomic SQLite snapshot from each
authority into an outer generation without claiming a cross-database
transaction.  The Second Brain side deliberately delegates snapshot, blob-hold,
manifest, and restore work to the existing P7.4 services.

This packet is intentionally local-only: every source, generation, and restore
path must be below the operating-system temporary root and must carry the O1A
marker.  It is not the target-host rehearsal owned by O1B-P1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID, uuid5

from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
from code_assist_v1 import notion_workspace_store as workspace_store
from code_assist_v1.knowledge_core.backup_manifest import (
    BACKUP_SNAPSHOT_RELATIVE_PATH,
)
from code_assist_v1.knowledge_core.backup_storage import FilesystemBackupStorage
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
)
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
)
from code_assist_v1.knowledge_core.services.restore import RestoreDrillService
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    _atomic_json,
    _canonical_bytes,
    _database_observation,
    _load_json,
    _manifest_sha256,
    _sha256_bytes,
    _sha256_file,
    _snapshot_sqlite,
    _sqlite_connect,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CollaborativeEditingTransportAuthority,
)
from code_assist_v1.notion_collaboration.file_authority import (
    FILE_AUTHORITY_CONTRACT,
    STORE_DIRECTORY,
    STORE_MARKER,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    InMemoryIdentityDirectory,
)
from code_assist_v1.notion_collaboration.search_authority import (
    CollaborativeSearchAuthority,
)


SOURCE_CONTRACT = "code_assist.notion.o1a_p1_isolated_source.v1"
GENERATION_CONTRACT = "code_assist.notion.o1a_p1_local_generation.v1"
RESTORE_CONTRACT = "code_assist.notion.o1a_p1_new_root_restore.v1"
SOURCE_MARKER = ".o1a-p1-isolated-source.json"
GENERATION_MANIFEST = "generation-manifest.json"
INCOMPLETE_GENERATION = "incomplete-generation.json"
RESTORE_RECEIPT = "restore-receipt.json"
RESTORE_MARKER = ".o1a-p1-new-root-restore.json"
BACKUP_DIRECTORY = "coordinated-backups"
RESTORE_DIRECTORY = "restore-targets"
NOTION_SNAPSHOT = "authorities/notion/catalog.sqlite3"
NOTION_FILE_ROOT = "authorities/notion/files"
SBE_BACKUP_ROOT = "authorities/second-brain"
RPO_MINUTES = 15
RTO_MINUTES = 60
RETENTION_POLICY = {"half_hour_count": 96, "daily_count": 30, "monthly_count": 12}
FAULT_POINTS = frozenset(
    {
        None,
        "after_notion_snapshot",
        "after_notion_blob_copy",
        "after_second_brain_backup",
        "before_finalize",
    }
)
_GENERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z", re.ASCII)
_FORBIDDEN_COMPONENTS = frozenset({"knowledge", "user_data", ".runtime"})
_SBE_RUN_NAMESPACE = UUID("ea81ec66-61bb-496f-a516-e0b415d3ef59")
_SEARCH_PROJECTION_TABLES = frozenset(
    {
        "workspace_collaboration_search_state",
        "workspace_collaboration_search_documents",
        "workspace_collaboration_search_backlinks",
        "workspace_collaboration_search_reminders",
    }
)
_TRANSIENT_UPLOAD_STATES = ("prepared", "ready", "committing")


class LocalBackupBoundaryError(ValueError):
    """An O1A path or argument escaped its isolated realm."""


class LocalGenerationIntegrityError(RuntimeError):
    """A generation or restored root failed closed verification."""


class IncompleteLocalGeneration(LocalGenerationIntegrityError):
    """A requested generation is absent or not restore eligible."""


class InjectedLocalBackupFailure(RuntimeError):
    """A declared O1A fault point stopped generation creation."""


@dataclass(frozen=True, slots=True)
class WorkspaceSmokeProbe:
    workspace_id: str
    document_id: str
    owner_subject_id: str
    member_subject_id: str
    denied_subject_id: str
    search_query: str
    file_id: str
    second_brain_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "document_id",
            "owner_subject_id",
            "member_subject_id",
            "denied_subject_id",
            "search_query",
            "file_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"{name} must be bounded non-empty text")
        values = self.second_brain_item_ids
        if (
            not isinstance(values, tuple)
            or not values
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("second_brain_item_ids must be a unique non-empty tuple")


@dataclass(frozen=True, slots=True)
class LocalGenerationResult:
    generation_id: str
    generation_root: Path
    manifest_sha256: str
    notion_snapshot_sha256: str
    notion_event_high_water: int
    second_brain_backup_run_id: str
    second_brain_manifest_sha256: str
    publication_link_count: int


@dataclass(frozen=True, slots=True)
class LocalRestoreResult:
    generation_id: str
    target_root: Path
    restore_receipt_sha256: str
    rpo_minutes: float
    rto_seconds: float
    integrity_mismatch_count: int
    smoke_passed: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _utc_ms(value: object) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LocalGenerationIntegrityError("non-canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise LocalGenerationIntegrityError("non-canonical UTC timestamp") from exc
    rendered = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if rendered != value:
        raise LocalGenerationIntegrityError("non-canonical UTC timestamp")
    return int(parsed.timestamp() * 1000)


def _path_locator(path: Path) -> str:
    return _sha256_bytes(os.path.normcase(os.fspath(path.resolve())).encode("utf-8"))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _isolated_path(path: Path, *, must_exist: bool = True) -> Path:
    candidate = Path(path).resolve(strict=must_exist)
    roots: list[Path] = [Path(tempfile.gettempdir()).resolve(strict=True)]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        host_temporary = (Path(local_app_data) / "Temp").resolve(strict=True)
        if host_temporary not in roots:
            roots.append(host_temporary)
    eligible = [
        root for root in roots if candidate != root and _is_within(candidate, root)
    ]
    if not eligible:
        raise LocalBackupBoundaryError("O1A paths must be below the OS temporary root")
    temporary = max(eligible, key=lambda value: len(value.parts))
    if any(part.casefold() in _FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise LocalBackupBoundaryError("O1A path resembles a protected product root")
    current = candidate
    while current != temporary and _is_within(current, temporary):
        if current.exists() and current.is_symlink():
            raise LocalBackupBoundaryError("symlinked O1A paths are forbidden")
        current = current.parent
    return candidate


def _generation_id(value: str) -> str:
    if not isinstance(value, str) or _GENERATION_RE.fullmatch(value) is None:
        raise ValueError("valid generation_id required")
    return value


def _receipt_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {key: item for key, item in value.items() if key != "receipt_sha256"}
        )
    )


def _sbe_run_id(source_id: str, generation_id: str) -> str:
    return str(uuid5(_SBE_RUN_NAMESPACE, f"{source_id}:{generation_id}"))


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": _sha256_bytes(value)}
    raise LocalGenerationIntegrityError("SQLite row contains an unsupported value")


def _table_digest(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not columns:
        raise LocalGenerationIntegrityError("table inventory changed during observation")
    names = [str(row[1]) for row in columns]
    primary = [str(row[1]) for row in sorted(columns, key=lambda row: int(row[5])) if int(row[5])]
    order = primary or names
    quoted = ",".join(f'"{name}"' for name in names)
    ordering = ",".join(f'"{name}"' for name in order)
    digest = hashlib.sha256()
    digest.update(_canonical_bytes(names))
    count = 0
    for row in connection.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {ordering}'):
        digest.update(b"\n")
        digest.update(_canonical_bytes([_json_scalar(value) for value in row]))
        count += 1
    return {"row_count": count, "row_sha256": "sha256:" + digest.hexdigest()}


def _table_digests(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        if str(row[0]) not in _SEARCH_PROJECTION_TABLES
    ]
    return {table: _table_digest(connection, table) for table in tables}


def _max_integer(connection: sqlite3.Connection, table: str, column: str) -> int:
    value = connection.execute(
        f'SELECT COALESCE(MAX("{column}"),0) FROM "{table}"'
    ).fetchone()[0]
    return int(value)


def _count(connection: sqlite3.Connection, table: str, where: str = "") -> int:
    suffix = "" if not where else " WHERE " + where
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"{suffix}').fetchone()[0])


def _notion_high_water(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "workspace_audit_rowid": _max_integer(connection, "workspace_audit_events", "rowid"),
        "workspace_outbox_rowid": _max_integer(connection, "workspace_outbox", "rowid"),
        "collaboration_audit_rowid": _max_integer(
            connection, "workspace_collaboration_audit_events", "rowid"
        ),
        "collaboration_outbox_rowid": _max_integer(
            connection, "workspace_collaboration_outbox", "rowid"
        ),
        "collaboration_event_rowid": _max_integer(
            connection, "workspace_collaboration_events", "rowid"
        ),
        "document_sequence": _max_integer(
            connection, "workspace_collaboration_documents", "document_sequence"
        ),
        "crdt_update_sequence": _max_integer(
            connection, "workspace_collaboration_crdt_updates", "update_sequence"
        ),
        "crdt_snapshot_sequence": _max_integer(
            connection,
            "workspace_collaboration_crdt_snapshots",
            "through_update_sequence",
        ),
        "structure_command_sequence": _max_integer(
            connection, "workspace_collaboration_typed_commands", "command_sequence"
        ),
        "history_revision": _max_integer(
            connection, "workspace_collaboration_revision_checkpoints", "revision"
        ),
        "publication_event_sequence": _max_integer(
            connection,
            "workspace_collaboration_publication_intent_events",
            "event_sequence",
        ),
        "publication_outbox_rowid": _max_integer(
            connection, "workspace_collaboration_publication_outbox", "rowid"
        ),
        "workspace_security_epoch": _max_integer(connection, "workspaces", "security_epoch"),
        "document_access_epoch": _max_integer(
            connection, "workspace_collaboration_documents", "access_epoch"
        ),
        "membership_epoch": _max_integer(
            connection, "workspace_memberships", "membership_epoch"
        ),
        "grant_epoch": max(
            _max_integer(connection, "workspace_collaboration_page_grants", "grant_epoch"),
            _max_integer(connection, "workspace_collaboration_access_grants", "grant_epoch"),
        ),
    }


def _event_continuity(connection: sqlite3.Connection) -> dict[str, Any]:
    mismatch = 0
    event_count = 0
    for row in connection.execute(
        "SELECT document_id,document_sequence FROM workspace_collaboration_events "
        "ORDER BY document_id,document_sequence,id"
    ):
        event_count += 1
    documents = connection.execute(
        "SELECT id,document_sequence FROM workspace_collaboration_documents ORDER BY id"
    ).fetchall()
    for document_id, high_water in documents:
        values = [
            int(row[0])
            for row in connection.execute(
                "SELECT document_sequence FROM workspace_collaboration_events "
                "WHERE document_id=? AND document_sequence IS NOT NULL "
                "ORDER BY document_sequence,id",
                (document_id,),
            )
        ]
        if values and (values != sorted(set(values)) or values[-1] > int(high_water)):
            mismatch += 1
    return {
        "event_count": event_count,
        "document_count": len(documents),
        "event_order_mismatch_count": mismatch,
    }


def _publication_links(connection: sqlite3.Connection) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    rows = connection.execute(
        "SELECT id,source_revision,publication_result_json "
        "FROM workspace_collaboration_publication_intents "
        "WHERE state='committed' ORDER BY id"
    ).fetchall()
    for intent_id, source_revision, raw in rows:
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalGenerationIntegrityError("committed publication result is invalid") from exc
        targets = value.get("targets") if isinstance(value, dict) else None
        if not isinstance(targets, list):
            raise LocalGenerationIntegrityError("committed publication targets are invalid")
        for target in targets:
            if not isinstance(target, dict):
                raise LocalGenerationIntegrityError("committed publication target is invalid")
            item_id = target.get("item_id")
            revision_id = target.get("resolved_revision_id")
            space_id = target.get("space_id")
            if not all(isinstance(item, str) and item for item in (item_id, revision_id, space_id)):
                raise LocalGenerationIntegrityError("committed publication identity is invalid")
            links.append(
                {
                    "intent_id": str(intent_id),
                    "source_revision": str(source_revision),
                    "space_id": space_id,
                    "item_id": item_id,
                    "revision_id": revision_id,
                }
            )
    return sorted(links, key=lambda item: tuple(item.values()))


def _notion_observation(path: Path) -> dict[str, Any]:
    database = _database_observation(path)
    connection = _sqlite_connect(path, read_only=True)
    try:
        marker = connection.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if marker is None or tuple(marker) != (
            search_migration.TARGET_VERSION,
            search_migration.TARGET_NAME,
            search_migration.TARGET_CHECKSUM,
            search_migration.TARGET_ACTUAL_SCHEMA_SHA256,
        ):
            raise LocalGenerationIntegrityError("Notion snapshot schema is not 2026082410")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        if tables.intersection({"sessions", "secret_material", "accounts"}):
            raise LocalGenerationIntegrityError("Notion snapshot contains identity secrets")
        transient = int(
            connection.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_upload_sessions "
                "WHERE state IN ('prepared','ready','committing')"
            ).fetchone()[0]
        )
        if transient:
            raise LocalGenerationIntegrityError(
                "Notion file upload drain is not closed for backup"
            )
        continuity = _event_continuity(connection)
        if continuity["event_order_mismatch_count"]:
            raise LocalGenerationIntegrityError("Notion event ordering is invalid")
        digests = _table_digests(connection)
        high_water = _notion_high_water(connection)
        links = _publication_links(connection)
        return {
            "database": database,
            "schema_version": int(marker[0]),
            "schema_marker_sha256": _sha256_bytes(_canonical_bytes(list(marker))),
            "canonical_table_digests": digests,
            "canonical_table_set_sha256": _sha256_bytes(_canonical_bytes(digests)),
            "high_water": high_water,
            "event_continuity": continuity,
            "comment_count": _count(connection, "workspace_collaboration_comments"),
            "active_share_count": _count(
                connection, "workspace_collaboration_access_grants", "active=1"
            )
            + _count(connection, "workspace_collaboration_page_grants", "active=1")
            + _count(connection, "workspace_memberships", "revoked_at IS NULL"),
            "history_checkpoint_count": _count(
                connection, "workspace_collaboration_revision_checkpoints"
            ),
            "file_record_count": _count(connection, "workspace_collaboration_files"),
            "search_workspace_count": _count(connection, "workspace_collaboration_search_state"),
            "search_converged_workspace_count": _count(
                connection,
                "workspace_collaboration_search_state",
                "desired_generation=built_generation",
            ),
            "publication_links": links,
            "publication_links_sha256": _sha256_bytes(_canonical_bytes(links)),
            "transient_upload_session_count": transient,
        }
    finally:
        connection.close()


def _notion_blob_rows(snapshot: Path) -> list[dict[str, Any]]:
    connection = _sqlite_connect(snapshot, read_only=True)
    try:
        rows = connection.execute(
            "SELECT blob_relative_path,content_sha256,size_bytes,"
            "SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) AS active_count,"
            "COUNT(*) AS reference_count "
            "FROM workspace_collaboration_files "
            "GROUP BY blob_relative_path,content_sha256,size_bytes "
            "ORDER BY blob_relative_path"
        ).fetchall()
        return [
            {
                "blob_relative_path": str(row[0]),
                "content_sha256": str(row[1]),
                "size_bytes": int(row[2]),
                "active_reference_count": int(row[3]),
                "reference_count": int(row[4]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def _copy_notion_blobs(notion_root: Path, snapshot: Path, staging: Path) -> dict[str, Any]:
    source_store = (notion_root / STORE_DIRECTORY).resolve(strict=True)
    source_blob_root = (source_store / "blobs").resolve(strict=True)
    destination_store = staging / NOTION_FILE_ROOT
    records: list[dict[str, Any]] = []
    copied_bytes = 0
    for row in _notion_blob_rows(snapshot):
        relative = Path(str(row["blob_relative_path"]))
        source = (source_store / relative).resolve(strict=False)
        try:
            source.relative_to(source_blob_root)
        except ValueError as exc:
            raise LocalGenerationIntegrityError("Notion blob path escaped storage") from exc
        included = source.is_file() and not source.is_symlink()
        if not included and int(row["active_reference_count"]):
            raise LocalGenerationIntegrityError("active Notion blob is absent")
        record = dict(row)
        record["included"] = included
        if included:
            observed_sha = _sha256_file(source)
            observed_size = source.stat().st_size
            if observed_sha != row["content_sha256"] or observed_size != row["size_bytes"]:
                raise LocalGenerationIntegrityError("Notion blob differs from canonical metadata")
            target = destination_store / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if _sha256_file(target) != observed_sha or target.stat().st_size != observed_size:
                raise LocalGenerationIntegrityError("Notion blob copy verification failed")
            record["generation_relative_path"] = target.relative_to(staging).as_posix()
            copied_bytes += observed_size
        else:
            record["generation_relative_path"] = None
        records.append(record)
    manifest = {
        "contract": "code_assist.notion.o1a_p1_blob_manifest.v1",
        "record_count": len(records),
        "included_count": sum(1 for row in records if row["included"]),
        "included_bytes": copied_bytes,
        "records": records,
    }
    manifest["records_sha256"] = _sha256_bytes(_canonical_bytes(records))
    _atomic_json(staging / "components/notion-file-manifest.json", manifest)
    return manifest


def _walk_files(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink():
            raise LocalGenerationIntegrityError("generation contains a symlink")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative in {GENERATION_MANIFEST, INCOMPLETE_GENERATION}:
                continue
            records.append(
                {
                    "relative_path": relative,
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return records


def _trip(selected: str | None, point: str) -> None:
    if selected == point:
        raise InjectedLocalBackupFailure(point)


def _source_paths(
    source_root: Path,
    sbe_layout: MigrationStorageLayout,
) -> tuple[Path, Path, Path, Path]:
    root = _isolated_path(Path(source_root))
    notion_root = _isolated_path(root / "notion")
    notion_catalog = _isolated_path(
        Path(workspace_store.catalog_path_for_user_data_root(notion_root))
    )
    sbe_root = _isolated_path(Path(sbe_layout.data_root))
    sbe_database = _isolated_path(Path(sbe_layout.database))
    if not _is_within(sbe_database, sbe_root):
        raise LocalBackupBoundaryError("Second Brain database escaped its data root")
    return root, notion_root, notion_catalog, sbe_root


def initialize_isolated_workspace_backup_source(
    source_root: Path,
    *,
    sbe_layout: MigrationStorageLayout,
) -> dict[str, Any]:
    """Bind existing disposable Notion/SBE authorities to one O1A source marker."""

    root, notion_root, notion_catalog, sbe_root = _source_paths(source_root, sbe_layout)
    if root in {notion_root, sbe_root} or notion_root == sbe_root:
        raise LocalBackupBoundaryError("canonical authority roots must remain separate")
    marker_path = root / SOURCE_MARKER
    marker = {
        "schema_version": 1,
        "contract": SOURCE_CONTRACT,
        "source_id": _sha256_bytes(
            _canonical_bytes(
                {
                    "root": _path_locator(root),
                    "notion": _path_locator(notion_catalog),
                    "second_brain": _path_locator(Path(sbe_layout.database)),
                }
            )
        ),
        "root_locator_sha256": _path_locator(root),
        "notion_root_locator_sha256": _path_locator(notion_root),
        "notion_catalog_locator_sha256": _path_locator(notion_catalog),
        "second_brain_root_locator_sha256": _path_locator(sbe_root),
        "second_brain_database_locator_sha256": _path_locator(Path(sbe_layout.database)),
        "synthetic_isolated_only": True,
        "identity_authority_included": False,
        "identity_boundary": "external_agent_identity_forced_reauthentication",
        "production_ports_changed": False,
    }
    if marker_path.exists():
        observed = _load_json(marker_path)
        if observed != marker:
            raise LocalBackupBoundaryError("O1A source marker binding mismatch")
    else:
        _atomic_json(marker_path, marker)
    for directory in (root / BACKUP_DIRECTORY / "staging", root / BACKUP_DIRECTORY / "generations", root / RESTORE_DIRECTORY):
        directory.mkdir(parents=True, exist_ok=True)
        _isolated_path(directory)
    return marker


def _verify_source_marker(
    source_root: Path,
    sbe_layout: MigrationStorageLayout,
) -> tuple[dict[str, Any], Path, Path, Path]:
    root, notion_root, notion_catalog, sbe_root = _source_paths(source_root, sbe_layout)
    try:
        marker = _load_json(root / SOURCE_MARKER)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise LocalBackupBoundaryError("O1A source marker is unavailable") from exc
    expected = initialize_isolated_workspace_backup_source(root, sbe_layout=sbe_layout)
    if marker != expected or marker.get("synthetic_isolated_only") is not True:
        raise LocalBackupBoundaryError("O1A source marker is invalid")
    if marker.get("second_brain_root_locator_sha256") != _path_locator(sbe_root):
        raise LocalBackupBoundaryError("Second Brain source binding changed")
    return marker, root, notion_root, notion_catalog


def _validate_sbe_publication_links(snapshot: Path, links: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    connection = _sqlite_connect(snapshot, read_only=True)
    missing_items: list[str] = []
    missing_revisions: list[str] = []
    try:
        for link in links:
            item_id = str(link["item_id"])
            revision_id = str(link["revision_id"])
            if connection.execute("SELECT 1 FROM knowledge_item WHERE id=?", (item_id,)).fetchone() is None:
                missing_items.append(item_id)
            if connection.execute(
                "SELECT 1 FROM knowledge_revision WHERE id=? AND item_id=?",
                (revision_id, item_id),
            ).fetchone() is None:
                missing_revisions.append(revision_id)
    finally:
        connection.close()
    return {
        "link_count": len(links),
        "missing_item_count": len(missing_items),
        "missing_revision_count": len(missing_revisions),
        "links_sha256": _sha256_bytes(_canonical_bytes(list(links))),
    }


def create_local_workspace_generation(
    source_root: Path,
    generation_id: str,
    *,
    sbe_factory: ConnectionFactory,
    sbe_layout: MigrationStorageLayout,
    sbe_configuration: BackupServiceConfiguration,
    smoke_probe: WorkspaceSmokeProbe,
    fault: str | None = None,
) -> LocalGenerationResult:
    """Create one fail-closed local generation from separate authorities."""

    if fault not in FAULT_POINTS:
        raise ValueError("unknown O1A fault point")
    generation = _generation_id(generation_id)
    marker, root, notion_root, notion_catalog = _verify_source_marker(
        source_root, sbe_layout
    )
    staging = root / BACKUP_DIRECTORY / "staging" / generation
    final = root / BACKUP_DIRECTORY / "generations" / generation
    if staging.exists() or final.exists():
        raise LocalBackupBoundaryError("generation id has already been used")
    staging.mkdir(parents=True)
    started_at = _utc_now()
    sbe_run_id = _sbe_run_id(str(marker["source_id"]), generation)
    try:
        notion_snapshot = staging / NOTION_SNAPSHOT
        _snapshot_sqlite(notion_catalog, notion_snapshot)
        notion = _notion_observation(notion_snapshot)
        if notion["transient_upload_session_count"] != 0:
            raise LocalGenerationIntegrityError(
                "Notion upload drain is incomplete at the captured high-water"
            )
        _trip(fault, "after_notion_snapshot")

        notion_blobs = _copy_notion_blobs(notion_root, notion_snapshot, staging)
        _trip(fault, "after_notion_blob_copy")

        sbe_backup_root = staging / SBE_BACKUP_ROOT
        sbe_backup_root.mkdir(parents=True)
        # P7.4 adds a UUID partial-directory suffix.  A short same-volume work
        # root keeps that verified implementation below legacy Windows path
        # limits; only its atomically completed directory is moved into the
        # outer generation.
        sbe_work_root = Path(
            tempfile.mkdtemp(prefix="o1a-sbe-", dir=tempfile.gettempdir())
        ).resolve(strict=True)
        try:
            sbe_result = BackupService(
                sbe_factory,
                sbe_layout,
                backup_root=sbe_work_root,
                configuration=sbe_configuration,
            ).create(backup_run_id=sbe_run_id)
            completed_source = sbe_work_root / sbe_result.completed_directory
            completed_target = sbe_backup_root / sbe_result.completed_directory
            os.replace(completed_source, completed_target)
        finally:
            if sbe_work_root.exists():
                shutil.rmtree(sbe_work_root, ignore_errors=True)
        sbe_manifest, sbe_snapshot = FilesystemBackupStorage(
            sbe_layout, sbe_backup_root
        ).verify_completed(sbe_run_id)
        if (
            sbe_result.manifest_sha256 != sbe_manifest.get("manifest_sha256")
            or sbe_result.snapshot_db_sha256 != sbe_snapshot.sha256
        ):
            raise LocalGenerationIntegrityError("P7.4 backup result binding mismatch")
        _trip(fault, "after_second_brain_backup")

        links = notion["publication_links"]
        sbe_database_snapshot = (
            sbe_backup_root
            / sbe_result.completed_directory
            / BACKUP_SNAPSHOT_RELATIVE_PATH
        )
        publication = _validate_sbe_publication_links(sbe_database_snapshot, links)
        if publication["missing_item_count"] or publication["missing_revision_count"]:
            raise LocalGenerationIntegrityError(
                "Notion publication high-water is absent from Second Brain snapshot"
            )
        probe_items = set(smoke_probe.second_brain_item_ids)
        linked_items = {str(item["item_id"]) for item in links}
        if not probe_items.issubset(linked_items):
            raise LocalGenerationIntegrityError(
                "Second Brain smoke items are not bound to committed Notion intents"
            )

        completed_at = _utc_now()
        payload_files = _walk_files(staging)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "contract": GENERATION_CONTRACT,
            "generation_id": generation,
            "state": "verified",
            "realm_id": "synthetic_isolated",
            "source_marker_sha256": _sha256_file(root / SOURCE_MARKER),
            "source_id": marker["source_id"],
            "started_at": started_at,
            "completed_at": completed_at,
            "capture_window_ms": _utc_ms(completed_at) - _utc_ms(started_at),
            "objectives": {
                "max_rpo_minutes": RPO_MINUTES,
                "max_rto_minutes": RTO_MINUTES,
                "retention": dict(RETENTION_POLICY),
            },
            "authority_order": ["notion", "second_brain"],
            "authorities": {
                "notion": {
                    "canonical_authority": True,
                    "snapshot_relative_path": NOTION_SNAPSHOT,
                    "snapshot_sha256": _sha256_file(notion_snapshot),
                    "snapshot_size_bytes": notion_snapshot.stat().st_size,
                    "observation": notion,
                    "blob_manifest_relative_path": "components/notion-file-manifest.json",
                    "blob_manifest_sha256": _sha256_file(
                        staging / "components/notion-file-manifest.json"
                    ),
                    "blob_count": notion_blobs["included_count"],
                    "blob_bytes": notion_blobs["included_bytes"],
                    "capture_boundary": "atomic_sqlite_snapshot_plus_verified_referenced_blobs",
                },
                "second_brain": {
                    "canonical_authority": True,
                    "delegated_service": "knowledge_core.services.backup.BackupService",
                    "backup_run_id": sbe_run_id,
                    "completed_directory_relative_path": (
                        f"{SBE_BACKUP_ROOT}/{sbe_result.completed_directory}"
                    ),
                    "manifest_sha256": sbe_result.manifest_sha256,
                    "snapshot_sha256": sbe_result.snapshot_db_sha256,
                    "snapshot_audit_high_water": sbe_result.snapshot_audit_high_water,
                    "blob_count": sbe_result.blob_count,
                    "blob_bytes": sbe_result.blob_bytes,
                    "capture_boundary": "p7_4_online_backup_blob_hold_and_atomic_finalize",
                },
            },
            "publication_boundary": {
                **publication,
                "notion_publication_event_sequence": notion["high_water"][
                    "publication_event_sequence"
                ],
                "second_brain_audit_high_water": sbe_result.snapshot_audit_high_water,
                "capture_order": ["notion", "second_brain"],
                "cross_database_transaction_claimed": False,
                "separate_canonical_authorities_preserved": True,
            },
            "identity_boundary": {
                "included": False,
                "authority": "external_agent_identity",
                "sessions_credentials_secrets_copied": False,
                "restore_requires_fresh_identity_session": True,
            },
            "write_boundary": {
                "mode": "snapshot_high_water_capture",
                "notion_transient_upload_session_count": notion[
                    "transient_upload_session_count"
                ],
                "notion_event_high_water": notion["high_water"][
                    "collaboration_event_rowid"
                ],
                "second_brain_audit_high_water": sbe_result.snapshot_audit_high_water,
            },
            "smoke_probe": {
                **asdict(smoke_probe),
                "second_brain_item_ids": list(smoke_probe.second_brain_item_ids),
            },
            "payload_files": payload_files,
            "payload_file_set_sha256": _sha256_bytes(_canonical_bytes(payload_files)),
            "claims": {
                "O1A_P1_local_generation_complete": True,
                "target_restore_lane_closed": False,
                "production_changed": False,
                "product_complete": False,
            },
        }
        manifest["manifest_sha256"] = _manifest_sha256(manifest)
        _atomic_json(staging / GENERATION_MANIFEST, manifest)
        _trip(fault, "before_finalize")
        os.replace(staging, final)
        verified = verify_local_workspace_generation(
            source_root,
            generation,
            sbe_factory=sbe_factory,
            sbe_layout=sbe_layout,
        )
        return LocalGenerationResult(
            generation_id=generation,
            generation_root=final,
            manifest_sha256=str(verified["manifest_sha256"]),
            notion_snapshot_sha256=str(
                verified["authorities"]["notion"]["snapshot_sha256"]
            ),
            notion_event_high_water=int(
                verified["write_boundary"]["notion_event_high_water"]
            ),
            second_brain_backup_run_id=sbe_run_id,
            second_brain_manifest_sha256=sbe_result.manifest_sha256,
            publication_link_count=int(publication["link_count"]),
        )
    except BaseException as exc:
        if staging.exists():
            receipt = {
                "schema_version": 1,
                "contract": GENERATION_CONTRACT,
                "generation_id": generation,
                "state": "incomplete",
                "restorable": False,
                "error_code": (
                    "INJECTED_LOCAL_BACKUP_FAILURE"
                    if isinstance(exc, InjectedLocalBackupFailure)
                    else "LOCAL_GENERATION_FAILED"
                ),
                "updated_at": _utc_now(),
            }
            _atomic_json(staging / INCOMPLETE_GENERATION, receipt)
        raise


def _exact_generation_files(root: Path, manifest: Mapping[str, Any]) -> None:
    records = manifest.get("payload_files")
    if not isinstance(records, list):
        raise LocalGenerationIntegrityError("generation payload denominator is invalid")
    allowed = {GENERATION_MANIFEST}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "relative_path",
            "sha256",
            "size_bytes",
        }:
            raise LocalGenerationIntegrityError("generation payload record is invalid")
        relative = record["relative_path"]
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or relative.startswith("/")
            or ".." in Path(relative).parts
        ):
            raise LocalGenerationIntegrityError("generation payload path is unsafe")
        path = root / Path(relative)
        if (
            not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != record["sha256"]
            or path.stat().st_size != record["size_bytes"]
        ):
            raise LocalGenerationIntegrityError("generation payload verification failed")
        allowed.add(relative)
    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if observed != allowed:
        raise LocalGenerationIntegrityError("generation contains undeclared files")
    if manifest.get("payload_file_set_sha256") != _sha256_bytes(
        _canonical_bytes(records)
    ):
        raise LocalGenerationIntegrityError("generation payload set hash mismatch")


def verify_local_workspace_generation(
    source_root: Path,
    generation_id: str,
    *,
    sbe_factory: ConnectionFactory,
    sbe_layout: MigrationStorageLayout,
) -> dict[str, Any]:
    marker, root, _notion_root, _notion_catalog = _verify_source_marker(
        source_root, sbe_layout
    )
    generation = _generation_id(generation_id)
    final = root / BACKUP_DIRECTORY / "generations" / generation
    if not final.is_dir() or final.is_symlink():
        raise IncompleteLocalGeneration("verified local generation is unavailable")
    try:
        manifest = _load_json(final / GENERATION_MANIFEST)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise IncompleteLocalGeneration("verified local generation manifest is unavailable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("contract") != GENERATION_CONTRACT
        or manifest.get("state") != "verified"
        or manifest.get("generation_id") != generation
        or manifest.get("source_id") != marker.get("source_id")
    ):
        raise IncompleteLocalGeneration("local generation state is invalid")
    if manifest.get("manifest_sha256") != _manifest_sha256(manifest):
        raise LocalGenerationIntegrityError("local generation self hash mismatch")
    _exact_generation_files(final, manifest)

    notion_record = manifest.get("authorities", {}).get("notion", {})
    notion_snapshot = final / str(notion_record.get("snapshot_relative_path"))
    if (
        _sha256_file(notion_snapshot) != notion_record.get("snapshot_sha256")
        or notion_snapshot.stat().st_size != notion_record.get("snapshot_size_bytes")
    ):
        raise LocalGenerationIntegrityError("Notion snapshot binding mismatch")
    notion = _notion_observation(notion_snapshot)
    if notion != notion_record.get("observation"):
        raise LocalGenerationIntegrityError("Notion logical snapshot binding mismatch")

    sbe_record = manifest.get("authorities", {}).get("second_brain", {})
    sbe_root = final / SBE_BACKUP_ROOT
    sbe_manifest, sbe_snapshot = FilesystemBackupStorage(
        sbe_layout, sbe_root
    ).verify_completed(str(sbe_record.get("backup_run_id")))
    if (
        sbe_manifest.get("manifest_sha256") != sbe_record.get("manifest_sha256")
        or sbe_snapshot.sha256 != sbe_record.get("snapshot_sha256")
        or sbe_snapshot.audit_high_water != sbe_record.get("snapshot_audit_high_water")
    ):
        raise LocalGenerationIntegrityError("Second Brain P7.4 binding mismatch")
    publication = _validate_sbe_publication_links(
        sbe_root
        / Path(str(sbe_record.get("completed_directory_relative_path"))).name
        / BACKUP_SNAPSHOT_RELATIVE_PATH,
        notion["publication_links"],
    )
    boundary = manifest.get("publication_boundary", {})
    for key in ("link_count", "missing_item_count", "missing_revision_count", "links_sha256"):
        if publication[key] != boundary.get(key):
            raise LocalGenerationIntegrityError("publication boundary binding mismatch")
    if boundary.get("cross_database_transaction_claimed") is not False:
        raise LocalGenerationIntegrityError("cross-database transaction claim is forbidden")
    return manifest


def _projection_reset_and_rebuild(
    catalog: Path,
    notion_root: Path,
    identities: InMemoryIdentityDirectory,
) -> list[dict[str, Any]]:
    connection = _sqlite_connect(catalog)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM workspace_collaboration_search_reminders")
        connection.execute("DELETE FROM workspace_collaboration_search_backlinks")
        connection.execute("DELETE FROM workspace_collaboration_search_documents")
        connection.execute(
            "UPDATE workspace_collaboration_search_state SET built_generation=0,"
            "source_document_count=0,indexed_document_count=0,backlink_count=0,"
            "reminder_count=0,last_rebuild_sha256=NULL,built_at=NULL"
        )
        connection.commit()
        owners = [
            (str(row[0]), str(row[1]))
            for row in connection.execute("SELECT id,owner_subject_id FROM workspaces ORDER BY id")
        ]
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    authority = CollaborativeSearchAuthority.open(catalog, notion_root, identities)
    return [authority.rebuild_search_index(owner, workspace) for workspace, owner in owners]


def _copy_restored_notion_blobs(
    generation_root: Path,
    target_root: Path,
    catalog: Path,
    identities: InMemoryIdentityDirectory,
) -> None:
    del identities  # The marker binds storage to the restored catalog, not an identity.
    storage_root = target_root / STORE_DIRECTORY
    storage_root.mkdir(mode=0o700)
    (storage_root / "staging").mkdir(mode=0o700)
    (storage_root / "blobs").mkdir(mode=0o700)
    _atomic_json(
        storage_root / STORE_MARKER,
        {
            "contract": FILE_AUTHORITY_CONTRACT,
            "catalog_locator_sha256": _sha256_bytes(
                os.path.normcase(str(catalog)).encode("utf-8")
            ),
        },
    )
    manifest = _load_json(generation_root / "components/notion-file-manifest.json")
    for record in manifest.get("records", []):
        if not isinstance(record, dict) or record.get("included") is not True:
            continue
        source = generation_root / str(record["generation_relative_path"])
        target = storage_root / str(record["blob_relative_path"])
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to((storage_root / "blobs").resolve(strict=True))
        except ValueError as exc:
            raise LocalGenerationIntegrityError("restored Notion blob escaped storage") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if (
            _sha256_file(target) != record["content_sha256"]
            or target.stat().st_size != record["size_bytes"]
        ):
            raise LocalGenerationIntegrityError("restored Notion blob verification failed")


def _restore_smoke(
    target_root: Path,
    notion_root: Path,
    notion_catalog: Path,
    sbe_layout: MigrationStorageLayout,
    source_factory: ConnectionFactory,
    probe: WorkspaceSmokeProbe,
) -> dict[str, Any]:
    subjects = tuple(
        dict.fromkeys(
            (
                probe.owner_subject_id,
                probe.member_subject_id,
                probe.denied_subject_id,
            )
        )
    )
    identities = InMemoryIdentityDirectory(subjects)
    authority = CollaborativeSearchAuthority.open(notion_catalog, notion_root, identities)
    owner_page = authority.read_document(probe.owner_subject_id, probe.document_id)
    member_page = authority.read_document(probe.member_subject_id, probe.document_id)
    denied = False
    try:
        authority.read_document(probe.denied_subject_id, probe.document_id)
    except NotFoundOrDenied:
        denied = True

    transport = CollaborativeEditingTransportAuthority.open(notion_catalog, notion_root)
    counts = transport.counts()
    comments = authority.list_comments(probe.member_subject_id, probe.document_id)
    revisions = authority.list_history(
        probe.member_subject_id, probe.document_id, limit=100
    )
    restored_file = authority.get_file(probe.member_subject_id, probe.file_id)
    found = authority.search(
        probe.member_subject_id,
        probe.workspace_id,
        query=probe.search_query,
    )

    sbe_factory = ConnectionFactory._for_isolated_tests(
        sbe_layout.database, source_factory.migration_catalog
    )
    connection = sbe_factory.open(read_only=True)
    try:
        sbe_items = sum(
            1
            for item_id in probe.second_brain_item_ids
            if connection.execute(
                "SELECT 1 FROM knowledge_item WHERE id=?", (item_id,)
            ).fetchone()
            is not None
        )
    finally:
        connection.close()
    result = {
        "permission": {
            "owner_read": str(owner_page["document_id"]) == probe.document_id,
            "member_read": str(member_page["document_id"]) == probe.document_id,
            "denied_subject_fenced": denied,
        },
        "realtime": {
            "document_sequence": int(owner_page["document_sequence"]),
            "event_count": int(counts.get("workspace_collaboration_events", 0)),
            "crdt_update_count": int(
                counts.get("workspace_collaboration_crdt_updates", 0)
            ),
        },
        "comments": {"count": int(comments["count"])},
        "files": {
            "file_id": probe.file_id,
            "size_bytes": int(restored_file["size_bytes"]),
            "sha256": str(restored_file["content_sha256"]),
        },
        "history": {"revision_count": len(revisions["items"])},
        "search": {
            "matched_document": any(
                str(item.get("document_id")) == probe.document_id
                for item in found["items"]
            ),
            "converged": bool(found["index"]["converged"]),
        },
        "second_brain": {
            "expected_item_count": len(probe.second_brain_item_ids),
            "restored_item_count": sbe_items,
        },
    }
    checks = [
        result["permission"]["owner_read"],
        result["permission"]["member_read"],
        result["permission"]["denied_subject_fenced"],
        result["realtime"]["document_sequence"] >= 1,
        result["realtime"]["event_count"] >= 1,
        result["comments"]["count"] >= 1,
        result["files"]["size_bytes"] >= 1,
        result["history"]["revision_count"] >= 1,
        result["search"]["matched_document"],
        result["search"]["converged"],
        result["second_brain"]["expected_item_count"]
        == result["second_brain"]["restored_item_count"],
    ]
    result["passed"] = all(checks)
    result["check_count"] = len(checks)
    result["failed_check_count"] = sum(not item for item in checks)
    return result


def restore_local_workspace_generation(
    source_root: Path,
    generation_id: str,
    target_name: str,
    *,
    sbe_factory: ConnectionFactory,
    sbe_layout: MigrationStorageLayout,
    assumed_incident_at_ms: int | None = None,
    target_parent: Path | None = None,
) -> LocalRestoreResult:
    """Restore a verified O1A generation into a fresh isolated root."""

    started = time.perf_counter()
    manifest = verify_local_workspace_generation(
        source_root,
        generation_id,
        sbe_factory=sbe_factory,
        sbe_layout=sbe_layout,
    )
    _marker, root, _source_notion_root, _source_notion_catalog = _verify_source_marker(
        source_root, sbe_layout
    )
    restore_parent = (
        root / RESTORE_DIRECTORY if target_parent is None else Path(target_parent)
    )
    restore_parent = _isolated_path(restore_parent, must_exist=False)
    restore_parent.mkdir(parents=True, exist_ok=True)
    _isolated_path(restore_parent)
    target = _isolated_path(
        restore_parent / _generation_id(target_name), must_exist=False
    )
    if target.exists():
        raise LocalBackupBoundaryError("restore target already exists")
    generation_root = root / BACKUP_DIRECTORY / "generations" / _generation_id(
        generation_id
    )
    probe_value = manifest.get("smoke_probe")
    if not isinstance(probe_value, dict):
        raise LocalGenerationIntegrityError("restore smoke probe is unavailable")
    probe = WorkspaceSmokeProbe(
        workspace_id=str(probe_value["workspace_id"]),
        document_id=str(probe_value["document_id"]),
        owner_subject_id=str(probe_value["owner_subject_id"]),
        member_subject_id=str(probe_value["member_subject_id"]),
        denied_subject_id=str(probe_value["denied_subject_id"]),
        search_query=str(probe_value["search_query"]),
        file_id=str(probe_value["file_id"]),
        second_brain_item_ids=tuple(probe_value["second_brain_item_ids"]),
    )
    target.mkdir(parents=True)
    try:
        notion_root = target / "notion"
        notion_root.mkdir()
        notion_catalog = Path(workspace_store.catalog_path_for_user_data_root(notion_root))
        notion_catalog.parent.mkdir(parents=True, exist_ok=True)
        source_snapshot = generation_root / NOTION_SNAPSHOT
        shutil.copyfile(source_snapshot, notion_catalog)
        if _sha256_file(notion_catalog) != manifest["authorities"]["notion"]["snapshot_sha256"]:
            raise LocalGenerationIntegrityError("restored Notion database hash mismatch")

        subjects = tuple(
            dict.fromkeys(
                (
                    probe.owner_subject_id,
                    probe.member_subject_id,
                    probe.denied_subject_id,
                )
            )
        )
        identities = InMemoryIdentityDirectory(subjects)
        _copy_restored_notion_blobs(
            generation_root, notion_root, notion_catalog, identities
        )
        projection_reports = _projection_reset_and_rebuild(
            notion_catalog, notion_root, identities
        )

        sbe_target_root = target / "second-brain"
        sbe_database_root = sbe_target_root / "database"
        sbe_blob_root = sbe_target_root / "blobs"
        sbe_projection_root = sbe_target_root / "projections"
        for directory in (
            sbe_target_root,
            sbe_database_root,
            sbe_blob_root,
            sbe_projection_root,
        ):
            directory.mkdir()
        sbe_target_layout = MigrationStorageLayout(
            data_root=sbe_target_root,
            database=sbe_database_root / "knowledge-core.sqlite",
            blob_root=sbe_blob_root,
            projection_root=sbe_projection_root,
        )
        incident = (
            int(time.time() * 1000)
            if assumed_incident_at_ms is None
            else int(assumed_incident_at_ms)
        )
        sbe_record = manifest["authorities"]["second_brain"]
        try:
            sbe_restore = RestoreDrillService(
                sbe_factory,
                sbe_layout,
                backup_root=generation_root / SBE_BACKUP_ROOT,
            ).restore(
                backup_run_id=str(sbe_record["backup_run_id"]),
                target_layout=sbe_target_layout,
                assumed_incident_at_ms=incident,
                max_rpo_minutes=RPO_MINUTES,
                max_rto_minutes=RTO_MINUTES,
            )
        except Exception as exc:
            violations: list[str] = []
            if sbe_target_layout.database.is_file():
                diagnostic = sqlite3.connect(sbe_target_layout.database)
                try:
                    v1_names = {
                        query: name for name, query in POSTCONDITION_DEFINITIONS
                    }
                    search_query = next(
                        query
                        for name, query in POSTCONDITION_DEFINITIONS
                        if name == "search_visibility_consistency"
                    )
                    for migration in CANONICAL_MIGRATION_CATALOG.migrations:
                        for index, query in enumerate(
                            migration.postcondition_queries, start=1
                        ):
                            if query == search_query:
                                continue
                            row = diagnostic.execute(query).fetchone()
                            if row is not None:
                                label = v1_names.get(
                                    query,
                                    f"v{migration.version:03d}-postcondition-{index}",
                                )
                                violations.append(f"{label}:{tuple(row)!r}")
                finally:
                    diagnostic.close()
            raise LocalGenerationIntegrityError(
                "P7.4 Second Brain restore failed; postcondition violations="
                + ",".join(violations)
            ) from exc

        restored_notion = _notion_observation(notion_catalog)
        source_notion = manifest["authorities"]["notion"]["observation"]
        canonical_match = (
            restored_notion["canonical_table_digests"]
            == source_notion["canonical_table_digests"]
            and restored_notion["high_water"] == source_notion["high_water"]
            and restored_notion["publication_links_sha256"]
            == source_notion["publication_links_sha256"]
        )
        smoke = _restore_smoke(
            target,
            notion_root,
            notion_catalog,
            sbe_target_layout,
            sbe_factory,
            probe,
        )
        rto_seconds = time.perf_counter() - started
        notion_rpo_minutes = max(
            0.0,
            (incident - _utc_ms(str(manifest["started_at"]))) / 60_000,
        )
        rpo_minutes = max(notion_rpo_minutes, float(sbe_restore.rpo_minutes))
        passed = (
            canonical_match
            and smoke["passed"]
            and sbe_restore.integrity_mismatch_count == 0
            and rpo_minutes <= RPO_MINUTES
            and rto_seconds <= RTO_MINUTES * 60
        )
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "contract": RESTORE_CONTRACT,
            "generation_id": generation_id,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "realm_id": "synthetic_isolated",
            "new_root": True,
            "outbound_integrations_enabled": False,
            "mutation_traffic_enabled_during_restore": False,
            "identity_sessions_restored": False,
            "fresh_identity_session_required": True,
            "notion": {
                "canonical_match": canonical_match,
                "quick_check": restored_notion["database"]["quick_check"],
                "foreign_key_violation_count": restored_notion["database"][
                    "foreign_key_violation_count"
                ],
                "projection_workspace_count": len(projection_reports),
                "projection_reports_sha256": _sha256_bytes(
                    _canonical_bytes(projection_reports)
                ),
            },
            "second_brain": {
                "delegated_service": "knowledge_core.services.restore.RestoreDrillService",
                "snapshot_audit_high_water": sbe_restore.snapshot_audit_high_water,
                "projection_space_count": sbe_restore.projection_space_count,
                "projection_entry_count": sbe_restore.projection_entry_count,
                "integrity_mismatch_count": sbe_restore.integrity_mismatch_count,
            },
            "smoke": smoke,
            "objectives": {
                "rpo_minutes": rpo_minutes,
                "rto_seconds": rto_seconds,
                "max_rpo_minutes": RPO_MINUTES,
                "max_rto_minutes": RTO_MINUTES,
                "passed": passed,
            },
            "integrity_mismatch_count": 0 if canonical_match and smoke["passed"] else 1,
            "status": "pass" if passed else "failed",
            "claims": {
                "O1A_P1_new_root_restore_complete": passed,
                "target_restore_lane_closed": False,
                "production_changed": False,
                "product_complete": False,
            },
        }
        if not passed:
            raise LocalGenerationIntegrityError("O1A restored root did not pass")
        receipt["receipt_sha256"] = _receipt_sha256(receipt)
        _atomic_json(target / RESTORE_RECEIPT, receipt)
        marker = {
            "schema_version": 1,
            "contract": RESTORE_CONTRACT,
            "generation_id": generation_id,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "receipt_sha256": receipt["receipt_sha256"],
            "new_root": True,
            "outbound_integrations_enabled": False,
            "mutation_traffic_enabled": False,
        }
        _atomic_json(target / RESTORE_MARKER, marker)
        return LocalRestoreResult(
            generation_id=generation_id,
            target_root=target,
            restore_receipt_sha256=str(receipt["receipt_sha256"]),
            rpo_minutes=rpo_minutes,
            rto_seconds=rto_seconds,
            integrity_mismatch_count=int(receipt["integrity_mismatch_count"]),
            smoke_passed=bool(smoke["passed"]),
        )
    except BaseException:
        # A failed target is never marked restore eligible.  Keeping the isolated
        # directory makes forensic assertions possible without touching sources.
        raise


def verify_local_workspace_restored_root(target_root: Path) -> dict[str, Any]:
    target = _isolated_path(Path(target_root))
    try:
        marker = _load_json(target / RESTORE_MARKER)
        receipt = _load_json(target / RESTORE_RECEIPT)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise LocalGenerationIntegrityError("O1A restore receipt is unavailable") from exc
    if (
        not isinstance(marker, dict)
        or not isinstance(receipt, dict)
        or marker.get("contract") != RESTORE_CONTRACT
        or receipt.get("contract") != RESTORE_CONTRACT
        or receipt.get("status") != "pass"
        or receipt.get("receipt_sha256") != _receipt_sha256(receipt)
        or marker.get("receipt_sha256") != receipt.get("receipt_sha256")
        or marker.get("source_manifest_sha256")
        != receipt.get("source_manifest_sha256")
        or marker.get("new_root") is not True
        or marker.get("outbound_integrations_enabled") is not False
        or marker.get("mutation_traffic_enabled") is not False
        or receipt.get("integrity_mismatch_count") != 0
        or receipt.get("smoke", {}).get("passed") is not True
    ):
        raise LocalGenerationIntegrityError("O1A restore receipt binding mismatch")
    notion_catalog = Path(
        workspace_store.catalog_path_for_user_data_root(target / "notion")
    )
    observation = _notion_observation(notion_catalog)
    if (
        observation["database"]["quick_check"] != "ok"
        or observation["database"]["foreign_key_violation_count"] != 0
    ):
        raise LocalGenerationIntegrityError("restored Notion integrity failed")
    return receipt


__all__ = [
    "FAULT_POINTS",
    "GENERATION_CONTRACT",
    "INCOMPLETE_GENERATION",
    "IncompleteLocalGeneration",
    "InjectedLocalBackupFailure",
    "LocalBackupBoundaryError",
    "LocalGenerationIntegrityError",
    "LocalGenerationResult",
    "LocalRestoreResult",
    "RETENTION_POLICY",
    "RPO_MINUTES",
    "RTO_MINUTES",
    "WorkspaceSmokeProbe",
    "create_local_workspace_generation",
    "initialize_isolated_workspace_backup_source",
    "restore_local_workspace_generation",
    "verify_local_workspace_generation",
    "verify_local_workspace_restored_root",
]
