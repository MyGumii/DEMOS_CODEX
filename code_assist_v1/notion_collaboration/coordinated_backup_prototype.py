"""Disposable O0A-P2 coordinated-backup and new-root restore prototype.

This module is intentionally standard-library-only and marker-bound below the
OS temporary directory.  It coordinates independent Notion, Second Brain, and
identity SQLite snapshots with a cross-process admission barrier; it does not
claim a cross-database transaction.  A generation becomes restorable only
after every declared component verifies.  Restore always targets a new root,
discards sessions/secrets/transport state, provisions a new signing secret,
forces reauthentication, and rebuilds projections from canonical snapshots.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid

from .canonical_authority_prototype import (
    DATABASE_NAME as NOTION_DATABASE_NAME,
    MARKER_NAME as NOTION_MARKER_NAME,
    PROTOTYPE_CONTRACT as NOTION_CONTRACT,
    DisposableAuthority,
    initialize_disposable_authority,
)


REALM_CONTRACT = "code_assist.notion.synthetic_collaborative_realm.o0a_p2.v1"
GENERATION_FORMAT = "internal-collaborative-coordinated-generation-v1"
REALM_MARKER = ".o0a-p2-synthetic-realm.json"
COORDINATOR_DATABASE = "coordinator.sqlite3"
SBE_DATABASE = "second-brain.sqlite3"
IDENTITY_DATABASE = "identity.sqlite3"
MANIFEST_NAME = "generation-manifest.json"
INCOMPLETE_NAME = "incomplete-generation.json"
RESTORE_MARKER = ".o0a-p2-synthetic-restore.json"
RESTORE_RECEIPT = "restore-receipt.json"

MANDATORY_COMPONENT_IDS = (
    "notion_canonical_database",
    "sbe_canonical_database",
    "shared_identity_registry",
    "notion_immutable_blobs",
    "sbe_immutable_blobs",
    "collaboration_event_log",
    "collaboration_snapshots_and_checkpoints",
    "legacy_cutover_sources",
    "authority_schema_and_config_markers",
    "encrypted_key_metadata",
    "audit_outbox_and_publication_high_water",
)
FAULT_POINTS = frozenset(
    {
        None,
        "after_intent",
        "after_notion_snapshot",
        "after_sbe_snapshot",
        "after_identity_snapshot",
        "after_collaboration_snapshot",
        "after_blob_copy",
        "before_finalize",
    }
)
_FORBIDDEN_COMPONENTS = frozenset({"knowledge", "user_data", ".runtime"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z", re.ASCII)


class SyntheticBackupBoundaryError(ValueError):
    """A path or input escaped the disposable O0A-P2 boundary."""


class GenerationIntegrityError(RuntimeError):
    """A generation or restore failed closed integrity verification."""


class IncompleteGenerationError(GenerationIntegrityError):
    """A generation is absent, incomplete, or not restore-eligible."""


class InjectedBackupFailure(RuntimeError):
    """A declared synthetic fault interrupted generation creation."""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    generation_id: str
    generation_root: Path
    manifest_sha256: str
    notion_document_sequence: int
    notion_event_sequence: int
    sbe_publication_revision: int
    mandatory_component_count: int


@dataclass(frozen=True, slots=True)
class RestoreResult:
    generation_id: str
    target_root: Path
    source_manifest_sha256: str
    projection_manifest_sha256: str
    account_count: int
    session_count: int
    forced_reauthentication_count: int
    signing_secret_fingerprint: str


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _manifest_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        _canonical_bytes({key: item for key, item in value.items() if key != "manifest_sha256"})
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _identifier(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_temp_child(path: Path, *, must_not_exist: bool = False) -> Path:
    candidate = path.resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if candidate == temp_root or not _is_within(candidate, temp_root):
        raise SyntheticBackupBoundaryError("prototype path must be below the OS temporary root")
    if any(part.lower() in _FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise SyntheticBackupBoundaryError("prototype path resembles a protected data root")
    current = candidate
    while _is_within(current, temp_root) and current != temp_root:
        if current.exists() and current.is_symlink():
            raise SyntheticBackupBoundaryError("symlinked prototype paths are forbidden")
        current = current.parent
    if must_not_exist and candidate.exists():
        raise SyntheticBackupBoundaryError("restore target must not exist")
    return candidate


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(_canonical_bytes(value))
        stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _sqlite_connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0, isolation_level=None
        )
    else:
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    if read_only:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _initialize_database(path: Path, schema: str, metadata: Mapping[str, str]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(schema)
        connection.executemany(
            "INSERT INTO prototype_meta(key,value) VALUES(?,?)",
            sorted(metadata.items()),
        )
        connection.commit()
    finally:
        connection.close()


_COORDINATOR_SCHEMA = """
CREATE TABLE prototype_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE barrier_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  state TEXT NOT NULL CHECK(state IN ('idle','active')),
  generation_id TEXT,
  barrier_epoch INTEGER NOT NULL CHECK(barrier_epoch >= 0)
);
INSERT INTO barrier_state(singleton,state,generation_id,barrier_epoch) VALUES(1,'idle',NULL,0);
"""

_SBE_SCHEMA = """
CREATE TABLE prototype_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE items (
  id TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  notion_revision INTEGER NOT NULL CHECK(notion_revision >= 1)
);
CREATE TABLE audit_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  action TEXT NOT NULL,
  FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE RESTRICT
);
"""

_IDENTITY_SCHEMA = """
CREATE TABLE prototype_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE accounts (
  subject_id TEXT PRIMARY KEY,
  password_sha256 TEXT NOT NULL,
  active INTEGER NOT NULL CHECK(active IN (0,1))
) WITHOUT ROWID;
CREATE TABLE auth_state (
  subject_id TEXT PRIMARY KEY,
  reauthentication_epoch INTEGER NOT NULL CHECK(reauthentication_epoch >= 1),
  force_reauthentication INTEGER NOT NULL CHECK(force_reauthentication IN (0,1)),
  FOREIGN KEY(subject_id) REFERENCES accounts(subject_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  session_digest TEXT NOT NULL,
  FOREIGN KEY(subject_id) REFERENCES accounts(subject_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE secret_material (
  secret_id TEXT PRIMARY KEY,
  plaintext_value TEXT NOT NULL
) WITHOUT ROWID;
"""


def _notion_marker() -> dict[str, Any]:
    return {
        "contract": NOTION_CONTRACT,
        "disposable": True,
        "production_schema_version": None,
        "created_at": int(time.time()),
        "nonce": uuid.uuid4().hex,
    }


def initialize_synthetic_collaborative_realm(
    root: str | os.PathLike[str],
) -> "SyntheticCollaborativeRealm":
    """Create and seed one marker-bound disposable collaborative realm."""

    candidate = _validate_temp_child(Path(root), must_not_exist=True)
    candidate.mkdir(parents=True, exist_ok=False)
    _atomic_json(
        candidate / REALM_MARKER,
        {
            "contract": REALM_CONTRACT,
            "disposable": True,
            "production_schema_version": None,
            "created_at": _utc_now(),
            "nonce": uuid.uuid4().hex,
        },
    )
    _initialize_database(
        candidate / COORDINATOR_DATABASE,
        _COORDINATOR_SCHEMA,
        {"contract": REALM_CONTRACT, "schema_mode": "unversioned", "role": "barrier_only"},
    )
    notion = initialize_disposable_authority(candidate / "notion")
    notion.create_workspace(
        "owner", "workspace-a", operation_id="op-workspace-backup-seed-0001"
    )
    notion.add_member(
        "owner",
        "workspace-a",
        "member",
        "edit",
        expected_workspace_epoch=1,
        operation_id="op-member-backup-seed-0001",
    )
    notion.create_page(
        "owner",
        "workspace-a",
        "page-a",
        "Backup",
        {"blocks": [{"id": "block-a", "text": "base"}]},
        expected_workspace_epoch=2,
        operation_id="op-page-backup-seed-0001",
    )

    _initialize_database(
        candidate / SBE_DATABASE,
        _SBE_SCHEMA,
        {"contract": REALM_CONTRACT, "schema_mode": "synthetic_sbe_v1", "role": "second_brain"},
    )
    sbe = _sqlite_connect(candidate / SBE_DATABASE)
    try:
        sbe.execute("BEGIN IMMEDIATE")
        sbe.execute(
            "INSERT INTO items(id,body,revision,notion_revision) VALUES(?,?,?,?)",
            ("item-a", "second brain base", 1, 1),
        )
        sbe.execute(
            "INSERT INTO audit_events(item_id,action) VALUES(?,?)",
            ("item-a", "seeded"),
        )
        sbe.commit()
    finally:
        sbe.close()

    _initialize_database(
        candidate / IDENTITY_DATABASE,
        _IDENTITY_SCHEMA,
        {"contract": REALM_CONTRACT, "schema_mode": "synthetic_identity_v1", "role": "identity"},
    )
    identity = _sqlite_connect(candidate / IDENTITY_DATABASE)
    try:
        identity.execute("BEGIN IMMEDIATE")
        for subject in ("member", "owner"):
            password_hash = _sha256_bytes(("password:" + subject).encode("utf-8"))
            identity.execute(
                "INSERT INTO accounts(subject_id,password_sha256,active) VALUES(?,?,1)",
                (subject, password_hash),
            )
            identity.execute(
                "INSERT INTO auth_state(subject_id,reauthentication_epoch,force_reauthentication) VALUES(?,1,0)",
                (subject,),
            )
            identity.execute(
                "INSERT INTO sessions(session_id,subject_id,session_digest) VALUES(?,?,?)",
                ("session-" + subject, subject, _sha256_bytes(("session:" + subject).encode("utf-8"))),
            )
        identity.execute(
            "INSERT INTO secret_material(secret_id,plaintext_value) VALUES('session_signing','synthetic-source-secret-never-backed-up')"
        )
        identity.commit()
    finally:
        identity.close()

    realm = SyntheticCollaborativeRealm.open(candidate)
    realm.put_blob("notion", b"notion attachment\n")
    realm.put_blob("second_brain", b"second brain artifact\n")
    return realm


class SyntheticCollaborativeRealm:
    """Cooperating synthetic authorities guarded by one admission barrier."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.coordinator_database = root / COORDINATOR_DATABASE
        self.sbe_database = root / SBE_DATABASE
        self.identity_database = root / IDENTITY_DATABASE
        self.notion_root = root / "notion"
        self.backup_root = root / "backups"

    @classmethod
    def open(cls, root: str | os.PathLike[str]) -> "SyntheticCollaborativeRealm":
        candidate = _validate_temp_child(Path(root))
        try:
            marker = _load_json(candidate / REALM_MARKER)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise SyntheticBackupBoundaryError("valid synthetic realm marker required") from exc
        if not isinstance(marker, dict) or marker.get("contract") != REALM_CONTRACT or marker.get("disposable") is not True or marker.get("production_schema_version") is not None:
            raise SyntheticBackupBoundaryError("synthetic realm marker contract mismatch")
        required = (
            candidate / COORDINATOR_DATABASE,
            candidate / SBE_DATABASE,
            candidate / IDENTITY_DATABASE,
            candidate / "notion" / NOTION_DATABASE_NAME,
        )
        if any(not path.is_file() or path.is_symlink() for path in required):
            raise SyntheticBackupBoundaryError("synthetic authority is incomplete")
        DisposableAuthority.open(candidate / "notion")
        return cls(candidate)

    @property
    def notion(self) -> DisposableAuthority:
        return DisposableAuthority.open(self.notion_root)

    @contextmanager
    def mutation_barrier(self) -> Iterator[sqlite3.Connection]:
        connection = _sqlite_connect(self.coordinator_database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM barrier_state WHERE singleton=1"
            ).fetchone()
            if row is None or row["state"] != "idle":
                raise GenerationIntegrityError("coordinator barrier is not idle")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_page(
        self,
        text: str,
        *,
        expected_revision: int,
        operation_id: str,
    ) -> dict[str, Any]:
        with self.mutation_barrier():
            binding = self.notion.binding("owner", "page-a", minimum_role="edit")
            return self.notion.save_page(
                "owner",
                "page-a",
                {"blocks": [{"id": "block-a", "text": text}]},
                expected_revision=expected_revision,
                expected_workspace_epoch=binding.workspace_epoch,
                expected_page_epoch=binding.page_epoch,
                operation_id=operation_id,
            )

    def update_sbe(self, body: str) -> int:
        with self.mutation_barrier():
            connection = _sqlite_connect(self.sbe_database)
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT revision FROM items WHERE id='item-a'"
                ).fetchone()
                if row is None:
                    raise GenerationIntegrityError("synthetic SBE item missing")
                revision = int(row["revision"]) + 1
                connection.execute(
                    "UPDATE items SET body=?,revision=? WHERE id='item-a'",
                    (body, revision),
                )
                connection.execute(
                    "INSERT INTO audit_events(item_id,action) VALUES('item-a','updated')"
                )
                connection.commit()
                return revision
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def put_blob(self, authority_domain: str, content: bytes) -> str:
        if authority_domain not in {"notion", "second_brain"}:
            raise ValueError("unknown blob authority")
        if not isinstance(content, bytes) or not content:
            raise ValueError("non-empty blob bytes required")
        with self.mutation_barrier():
            digest = hashlib.sha256(content).hexdigest()
            root = self.root / "source-blobs" / authority_domain
            root.mkdir(parents=True, exist_ok=True)
            path = root / digest
            if path.exists():
                if _sha256_file(path) != "sha256:" + digest:
                    raise GenerationIntegrityError("content-addressed blob collision")
            else:
                with path.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            return "sha256:" + digest

    def source_page_revision(self) -> int:
        connection = _sqlite_connect(self.notion_root / NOTION_DATABASE_NAME, read_only=True)
        try:
            row = connection.execute("SELECT revision FROM pages WHERE id='page-a'").fetchone()
            if row is None:
                raise GenerationIntegrityError("source page missing")
            return int(row["revision"])
        finally:
            connection.close()

    def source_session_count(self) -> int:
        connection = _sqlite_connect(self.identity_database, read_only=True)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        finally:
            connection.close()


def _trip(selected: str | None, point: str) -> None:
    if selected == point:
        raise InjectedBackupFailure(point)


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = _sqlite_connect(source, read_only=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    _seal_sqlite_snapshot(destination)


def _seal_sqlite_snapshot(path: Path) -> None:
    """Make one standalone snapshot independent of WAL/SHM sidecars."""

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
    finally:
        connection.close()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(os.fspath(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _sqlite_schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
    ).fetchall()
    return _sha256_bytes(_canonical_bytes([dict(row) for row in rows]))


def _table_inventory(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    return {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in tables
    }


def _database_observation(path: Path) -> dict[str, Any]:
    connection = _sqlite_connect(path, read_only=True)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        inventory = _table_inventory(connection)
        return {
            "quick_check": quick_check,
            "foreign_key_violation_count": foreign_keys,
            "schema_lineage_sha256": _sqlite_schema_sha256(connection),
            "logical_inventory": inventory,
            "logical_inventory_sha256": _sha256_bytes(_canonical_bytes(inventory)),
        }
    finally:
        connection.close()


def _high_water_value(value: int | None, reason: str | None = None) -> dict[str, Any]:
    return {"value": value, "not_applicable_reason": reason}


def _notion_high_water(path: Path) -> dict[str, Any]:
    connection = _sqlite_connect(path, read_only=True)
    try:
        maximum_page = int(connection.execute("SELECT COALESCE(MAX(page_sequence),0) FROM pages").fetchone()[0])
        events = int(connection.execute("SELECT COUNT(*) FROM durable_events").fetchone()[0])
        return {
            "audit_id": _high_water_value(int(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])),
            "outbox_id": _high_water_value(int(connection.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0])),
            "document_sequence": _high_water_value(maximum_page),
            "event_sequence": _high_water_value(events),
            "snapshot_event_sequence": _high_water_value(events),
            "publication_revision": _high_water_value(None, "notion_snapshot_has_no_sbe_publication_revision"),
        }
    finally:
        connection.close()


def _sbe_high_water(path: Path) -> dict[str, Any]:
    connection = _sqlite_connect(path, read_only=True)
    try:
        publication = int(connection.execute("SELECT COALESCE(MAX(revision),0) FROM items").fetchone()[0])
        audit = int(connection.execute("SELECT COALESCE(MAX(sequence),0) FROM audit_events").fetchone()[0])
        return {
            "audit_id": _high_water_value(audit),
            "outbox_id": _high_water_value(None, "synthetic_sbe_has_no_outbox"),
            "document_sequence": _high_water_value(None, "synthetic_sbe_has_no_document_sequence"),
            "event_sequence": _high_water_value(None, "synthetic_sbe_has_no_collaboration_event_sequence"),
            "snapshot_event_sequence": _high_water_value(None, "synthetic_sbe_has_no_collaboration_snapshot_sequence"),
            "publication_revision": _high_water_value(publication),
        }
    finally:
        connection.close()


def _identity_high_water() -> dict[str, Any]:
    return {
        key: _high_water_value(None, "identity_authority_field_not_applicable")
        for key in (
            "audit_id",
            "outbox_id",
            "document_sequence",
            "event_sequence",
            "snapshot_event_sequence",
            "publication_revision",
        )
    }


def _authority_record(
    *,
    generation_id: str,
    authority_domain: str,
    snapshot_path: Path,
    relative_path: str,
    schema_version: str,
    blob_manifest_sha256: str,
    high_water: Mapping[str, Any],
) -> dict[str, Any]:
    observation = _database_observation(snapshot_path)
    if observation["quick_check"] != "ok" or observation["foreign_key_violation_count"] != 0:
        raise GenerationIntegrityError("authority snapshot integrity failed")
    return {
        "authority_domain": authority_domain,
        "snapshot_id": f"{generation_id}:{authority_domain}",
        "snapshot_relative_path": relative_path,
        "snapshot_sha256": _sha256_file(snapshot_path),
        "snapshot_size_bytes": snapshot_path.stat().st_size,
        "schema_version": schema_version,
        "schema_lineage_sha256": observation["schema_lineage_sha256"],
        "blob_manifest_sha256": blob_manifest_sha256,
        "high_water": dict(high_water),
        "quick_check": observation["quick_check"],
        "foreign_key_violation_count": observation["foreign_key_violation_count"],
        "logical_inventory_sha256": observation["logical_inventory_sha256"],
        "verification_state": "verified",
    }


def _write_component(root: Path, relative: str, value: object) -> dict[str, Any]:
    path = root / Path(relative)
    _atomic_json(path, value)
    return {
        "relative_path": relative.replace("\\", "/"),
        "content_sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _copy_blob_set(realm: SyntheticCollaborativeRealm, staging: Path, domain: str) -> dict[str, Any]:
    source = realm.root / "source-blobs" / domain
    destination = staging / "blobs" / domain
    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(source.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.is_symlink() or re.fullmatch(r"[0-9a-f]{64}", path.name) is None:
            raise GenerationIntegrityError("source blob set is not exact content-addressed storage")
        digest = _sha256_file(path)
        if digest != "sha256:" + path.name:
            raise GenerationIntegrityError("source blob digest mismatch")
        target = destination / path.name
        shutil.copyfile(path, target)
        if _sha256_file(target) != digest:
            raise GenerationIntegrityError("copied blob digest mismatch")
        rows.append(
            {
                "relative_path": target.relative_to(staging).as_posix(),
                "sha256": digest,
                "size_bytes": target.stat().st_size,
            }
        )
    manifest_relative = f"components/{domain}-blob-manifest.json"
    component = _write_component(staging, manifest_relative, {"authority_domain": domain, "blobs": rows})
    return {"rows": rows, "component": component}


def _export_notion_events(path: Path) -> list[dict[str, Any]]:
    connection = _sqlite_connect(path, read_only=True)
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM durable_events ORDER BY COALESCE(page_id,''),COALESCE(page_sequence,0),created_at,id"
            ).fetchall()
        ]
    finally:
        connection.close()


def _collaboration_snapshot(
    path: Path,
    *,
    selected_runtime_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    connection = _sqlite_connect(path, read_only=True)
    try:
        pages = []
        for row in connection.execute(
            "SELECT id,workspace_id,title,content_json,content_sha256,revision,page_sequence,access_epoch,row_version FROM pages ORDER BY id"
        ).fetchall():
            value = dict(row)
            value["content_json"] = json.loads(str(value["content_json"]))
            pages.append(value)
        result: dict[str, Any] = {
            "snapshot_format": "synthetic-collaboration-snapshot-v1",
            "pages": pages,
            "event_high_water": int(connection.execute("SELECT COUNT(*) FROM durable_events").fetchone()[0]),
            "page_sequence_high_water": int(connection.execute("SELECT COALESCE(MAX(page_sequence),0) FROM pages").fetchone()[0]),
        }
        if selected_runtime_snapshot is not None:
            result["selected_runtime_snapshot"] = dict(selected_runtime_snapshot)
        return result
    finally:
        connection.close()


def _normalize_selected_runtime_exports(
    value: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]] | None:
    """Validate the generic shape; O0A-P3 owns byte-level semantic checks."""

    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"event_ledger", "snapshot"}:
        raise GenerationIntegrityError("selected runtime exports are not exact")
    event_ledger = value["event_ledger"]
    snapshot = value["snapshot"]
    if not isinstance(event_ledger, Mapping) or not isinstance(snapshot, Mapping):
        raise GenerationIntegrityError("selected runtime exports must be objects")
    event_fields = {
        "contract",
        "runtime_binding_sha256",
        "candidate_id",
        "wire_format",
        "document_id",
        "request_id",
        "workspace_epoch",
        "page_epoch",
        "base_snapshot_id",
        "base_high_water_sequence",
        "target_high_water_sequence",
        "ordered_update_count",
        "ordered_update_bytes",
        "ordered_update_sha256",
        "ordered_update_aggregate_sha256",
        "request_sha256",
        "updates_base64",
    }
    snapshot_fields = {
        "contract",
        "runtime_binding_sha256",
        "candidate_id",
        "wire_format",
        "document_id",
        "request_id",
        "workspace_epoch",
        "page_epoch",
        "base_snapshot_id",
        "base_high_water_sequence",
        "target_high_water_sequence",
        "request_sha256",
        "snapshot_bytes",
        "snapshot_sha256",
        "snapshot_base64",
        "state_vector_bytes",
        "state_vector_sha256",
        "state_vector_base64",
        "expected_text_sha256",
    }
    if set(event_ledger) != event_fields or set(snapshot) != snapshot_fields:
        raise GenerationIntegrityError("selected runtime export fields are not exact")
    shared = (
        "contract",
        "runtime_binding_sha256",
        "candidate_id",
        "wire_format",
        "document_id",
        "request_id",
        "workspace_epoch",
        "page_epoch",
        "base_snapshot_id",
        "base_high_water_sequence",
        "target_high_water_sequence",
        "request_sha256",
    )
    if any(event_ledger[key] != snapshot[key] for key in shared):
        raise GenerationIntegrityError("selected runtime export binding drift")
    # Canonical JSON round-trip detaches caller-owned mutable containers and
    # rejects non-JSON/non-finite values before any generation path is created.
    return json.loads(_canonical_bytes({"event_ledger": event_ledger, "snapshot": snapshot}))


def _sanitize_identity_snapshot(path: Path) -> None:
    connection = _sqlite_connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM sessions")
        connection.execute("DELETE FROM secret_material")
        connection.execute(
            "UPDATE auth_state SET force_reauthentication=1, reauthentication_epoch=reauthentication_epoch+1"
        )
        connection.commit()
        connection.execute("VACUUM")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    _seal_sqlite_snapshot(path)


def _component_record(
    component_id: str,
    authority_domain: str,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "authority_domain": authority_domain,
        "relative_path": observation["relative_path"],
        "content_sha256": observation["content_sha256"],
        "size_bytes": observation["size_bytes"],
        "verification_state": "verified",
    }


def _database_component(path: Path, relative: str) -> dict[str, Any]:
    return {
        "relative_path": relative,
        "content_sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _incomplete(staging: Path, generation_id: str, code: str) -> None:
    _atomic_json(
        staging / INCOMPLETE_NAME,
        {
            "format_id": GENERATION_FORMAT,
            "generation_id": generation_id,
            "state": "incomplete",
            "error_code": code,
            "restorable": False,
            "updated_at": _utc_now(),
        },
    )


def create_coordinated_generation(
    realm_root: str | os.PathLike[str],
    generation_id: str,
    *,
    fault: str | None = None,
    barrier_signal_path: str | os.PathLike[str] | None = None,
    hold_after_barrier_seconds: float = 0.0,
    selected_runtime_exports: Mapping[str, Any] | None = None,
) -> GenerationResult:
    """Create one independently verified generation under a cross-process barrier."""

    generation = _identifier(generation_id, field="generation_id")
    if fault not in FAULT_POINTS:
        raise ValueError("unknown backup fault point")
    if isinstance(hold_after_barrier_seconds, bool) or not isinstance(hold_after_barrier_seconds, (int, float)) or not 0 <= hold_after_barrier_seconds <= 2:
        raise ValueError("hold_after_barrier_seconds must be between zero and two")
    normalized_runtime_exports = _normalize_selected_runtime_exports(selected_runtime_exports)
    realm = SyntheticCollaborativeRealm.open(realm_root)
    staging = realm.backup_root / "staging" / generation
    final = realm.backup_root / "generations" / generation
    if staging.exists() or final.exists():
        raise SyntheticBackupBoundaryError("generation path already exists")
    staging.mkdir(parents=True, exist_ok=False)
    created_at = _utc_now()
    _incomplete(staging, generation, "GENERATION_PREPARING")
    try:
        coordinator = _sqlite_connect(realm.coordinator_database)
        try:
            coordinator.execute("BEGIN IMMEDIATE")
            row = coordinator.execute(
                "SELECT state,barrier_epoch FROM barrier_state WHERE singleton=1"
            ).fetchone()
            if row is None or row["state"] != "idle":
                raise GenerationIntegrityError("coordinator barrier is not idle")
            barrier_epoch = int(row["barrier_epoch"]) + 1
            coordinator.execute(
                "UPDATE barrier_state SET state='active',generation_id=?,barrier_epoch=? WHERE singleton=1",
                (generation, barrier_epoch),
            )
            if barrier_signal_path is not None:
                signal = _validate_temp_child(Path(barrier_signal_path))
                if not _is_within(signal, realm.root):
                    raise SyntheticBackupBoundaryError("barrier signal must remain inside the synthetic realm")
                signal.write_text("barrier-acquired\n", encoding="utf-8")
            if hold_after_barrier_seconds:
                time.sleep(float(hold_after_barrier_seconds))
            _trip(fault, "after_intent")

            authorities = staging / "authorities"
            notion_snapshot = authorities / "notion.sqlite3"
            sbe_snapshot = authorities / "second-brain.sqlite3"
            identity_snapshot = authorities / "identity.sqlite3"
            _snapshot_sqlite(realm.notion_root / NOTION_DATABASE_NAME, notion_snapshot)
            _trip(fault, "after_notion_snapshot")
            _snapshot_sqlite(realm.sbe_database, sbe_snapshot)
            _trip(fault, "after_sbe_snapshot")
            _snapshot_sqlite(realm.identity_database, identity_snapshot)
            _sanitize_identity_snapshot(identity_snapshot)
            _trip(fault, "after_identity_snapshot")

            events_payload: dict[str, Any] = {"events": _export_notion_events(notion_snapshot)}
            if normalized_runtime_exports is not None:
                events_payload["selected_runtime_update_ledger"] = normalized_runtime_exports[
                    "event_ledger"
                ]
            events_observation = _write_component(
                staging,
                "components/collaboration-events.json",
                events_payload,
            )
            snapshot_observation = _write_component(
                staging,
                "components/collaboration-snapshot.json",
                _collaboration_snapshot(
                    notion_snapshot,
                    selected_runtime_snapshot=(
                        normalized_runtime_exports["snapshot"]
                        if normalized_runtime_exports is not None
                        else None
                    ),
                ),
            )
            _trip(fault, "after_collaboration_snapshot")

            notion_blobs = _copy_blob_set(realm, staging, "notion")
            sbe_blobs = _copy_blob_set(realm, staging, "second_brain")
            _trip(fault, "after_blob_copy")

            legacy_observation = _write_component(
                staging,
                "components/legacy-cutover-manifest.json",
                {"sources": [], "retirement_receipt": None, "retained": True},
            )
            schema_observation = _write_component(
                staging,
                "components/schema-config-markers.json",
                {
                    "realm_contract": REALM_CONTRACT,
                    "generation_format": GENERATION_FORMAT,
                    "notion_contract": NOTION_CONTRACT,
                    "production_schema_version": None,
                },
            )
            key_observation = _write_component(
                staging,
                "components/encrypted-key-metadata.json",
                {
                    "algorithm": "synthetic-external-reference-only",
                    "key_version": "unselected-G0-U",
                    "external_key_reference": "pending-G0-U",
                    "plaintext_key_material": None,
                },
            )
            notion_high_water = _notion_high_water(notion_snapshot)
            sbe_high_water = _sbe_high_water(sbe_snapshot)
            identity_high_water = _identity_high_water()
            high_water_observation = _write_component(
                staging,
                "components/high-water.json",
                {
                    "notion": notion_high_water,
                    "second_brain": sbe_high_water,
                    "identity": identity_high_water,
                    "cross_database_transaction_claimed": False,
                },
            )

            authority_records = [
                _authority_record(
                    generation_id=generation,
                    authority_domain="notion",
                    snapshot_path=notion_snapshot,
                    relative_path="authorities/notion.sqlite3",
                    schema_version="unversioned-a0a-p2",
                    blob_manifest_sha256=notion_blobs["component"]["content_sha256"],
                    high_water=notion_high_water,
                ),
                _authority_record(
                    generation_id=generation,
                    authority_domain="second_brain",
                    snapshot_path=sbe_snapshot,
                    relative_path="authorities/second-brain.sqlite3",
                    schema_version="synthetic-sbe-v1",
                    blob_manifest_sha256=sbe_blobs["component"]["content_sha256"],
                    high_water=sbe_high_water,
                ),
                _authority_record(
                    generation_id=generation,
                    authority_domain="identity",
                    snapshot_path=identity_snapshot,
                    relative_path="authorities/identity.sqlite3",
                    schema_version="synthetic-identity-v1",
                    blob_manifest_sha256=_sha256_bytes(_canonical_bytes([])),
                    high_water=identity_high_water,
                ),
            ]

            components = [
                _component_record("notion_canonical_database", "notion", _database_component(notion_snapshot, "authorities/notion.sqlite3")),
                _component_record("sbe_canonical_database", "second_brain", _database_component(sbe_snapshot, "authorities/second-brain.sqlite3")),
                _component_record("shared_identity_registry", "identity", _database_component(identity_snapshot, "authorities/identity.sqlite3")),
                _component_record("notion_immutable_blobs", "notion", notion_blobs["component"]),
                _component_record("sbe_immutable_blobs", "second_brain", sbe_blobs["component"]),
                _component_record("collaboration_event_log", "notion", events_observation),
                _component_record("collaboration_snapshots_and_checkpoints", "notion", snapshot_observation),
                _component_record("legacy_cutover_sources", "migration", legacy_observation),
                _component_record("authority_schema_and_config_markers", "coordinator", schema_observation),
                _component_record("encrypted_key_metadata", "security", key_observation),
                _component_record("audit_outbox_and_publication_high_water", "coordinator", high_water_observation),
            ]
            if tuple(item["component_id"] for item in components) != MANDATORY_COMPONENT_IDS:
                raise GenerationIntegrityError("mandatory component denominator drift")
            completed_at = _utc_now()
            manifest: dict[str, Any] = {
                "format_id": GENERATION_FORMAT,
                "generation_id": generation,
                "state": "verified",
                "created_at": created_at,
                "completed_at": completed_at,
                "write_barrier_id": f"barrier-{barrier_epoch}",
                "coordinator_config_sha256": _sha256_file(realm.coordinator_database),
                "component_inventory_sha256": _sha256_bytes(_canonical_bytes(list(MANDATORY_COMPONENT_IDS))),
                "publication_boundary": {
                    "notion": notion_high_water,
                    "second_brain": sbe_high_water,
                    "identity": identity_high_water,
                    "cross_database_transaction_claimed": False,
                },
                "authority_records": authority_records,
                "component_records": components,
                "manifest_sha256": "sha256:" + ("0" * 64),
            }
            manifest["manifest_sha256"] = _manifest_sha256(manifest)
            _trip(fault, "before_finalize")
            incomplete_path = staging / INCOMPLETE_NAME
            if incomplete_path.exists():
                incomplete_path.unlink()
            _atomic_json(staging / MANIFEST_NAME, manifest)
            _verify_generation_directory(staging)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            coordinator.execute(
                "UPDATE barrier_state SET state='idle',generation_id=NULL WHERE singleton=1"
            )
            coordinator.commit()
        except Exception:
            coordinator.rollback()
            raise
        finally:
            coordinator.close()
    except Exception as exc:
        if staging.exists():
            code = "INJECTED_BACKUP_FAILURE" if isinstance(exc, InjectedBackupFailure) else "GENERATION_FAILED"
            _incomplete(staging, generation, code)
        raise

    verified = verify_coordinated_generation(realm.root, generation)
    notion_high = verified["publication_boundary"]["notion"]
    sbe_high = verified["publication_boundary"]["second_brain"]
    return GenerationResult(
        generation_id=generation,
        generation_root=final,
        manifest_sha256=str(verified["manifest_sha256"]),
        notion_document_sequence=int(notion_high["document_sequence"]["value"]),
        notion_event_sequence=int(notion_high["event_sequence"]["value"]),
        sbe_publication_revision=int(sbe_high["publication_revision"]["value"]),
        mandatory_component_count=len(verified["component_records"]),
    )


def _verify_blob_manifest(root: Path, path: Path) -> list[str]:
    value = _load_json(path)
    if not isinstance(value, dict) or set(value) != {"authority_domain", "blobs"} or not isinstance(value["blobs"], list):
        raise GenerationIntegrityError("blob manifest shape invalid")
    paths: list[str] = []
    previous = ""
    for row in value["blobs"]:
        if not isinstance(row, dict) or set(row) != {"relative_path", "sha256", "size_bytes"}:
            raise GenerationIntegrityError("blob record shape invalid")
        relative = str(row["relative_path"])
        if relative <= previous:
            raise GenerationIntegrityError("blob records are not unique and sorted")
        previous = relative
        candidate = root / Path(relative)
        if not candidate.is_file() or candidate.is_symlink():
            raise GenerationIntegrityError("blob missing")
        if _sha256_file(candidate) != row["sha256"] or candidate.stat().st_size != row["size_bytes"]:
            raise GenerationIntegrityError("blob verification failed")
        paths.append(relative)
    return paths


def _verify_event_continuity(path: Path) -> None:
    connection = _sqlite_connect(path, read_only=True)
    try:
        rows = connection.execute(
            "SELECT page_id,page_sequence FROM durable_events WHERE page_id IS NOT NULL ORDER BY page_id,page_sequence"
        ).fetchall()
        grouped: dict[str, list[int]] = {}
        for row in rows:
            grouped.setdefault(str(row["page_id"]), []).append(int(row["page_sequence"]))
        for page_id, sequences in grouped.items():
            if sequences != list(range(1, len(sequences) + 1)):
                raise GenerationIntegrityError(f"event sequence gap for {page_id}")
    finally:
        connection.close()


def _verify_generation_directory(root: Path) -> dict[str, Any]:
    try:
        manifest = _load_json(root / MANIFEST_NAME)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise IncompleteGenerationError("verified generation manifest unavailable") from exc
    if not isinstance(manifest, dict) or manifest.get("format_id") != GENERATION_FORMAT or manifest.get("state") != "verified":
        raise IncompleteGenerationError("generation is not verified")
    if manifest.get("manifest_sha256") != _manifest_sha256(manifest):
        raise GenerationIntegrityError("generation manifest self-hash mismatch")
    components = manifest.get("component_records")
    if not isinstance(components, list) or tuple(item.get("component_id") for item in components if isinstance(item, dict)) != MANDATORY_COMPONENT_IDS:
        raise GenerationIntegrityError("mandatory component denominator mismatch")
    allowed = {MANIFEST_NAME}
    for item in components:
        if not isinstance(item, dict) or item.get("verification_state") != "verified":
            raise GenerationIntegrityError("component is not verified")
        relative = item.get("relative_path")
        if not isinstance(relative, str) or "\\" in relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise GenerationIntegrityError("unsafe component path")
        path = root / Path(relative)
        if not path.is_file() or path.is_symlink() or _sha256_file(path) != item.get("content_sha256") or path.stat().st_size != item.get("size_bytes"):
            raise GenerationIntegrityError("component verification failed")
        allowed.add(relative)
    authority_records = manifest.get("authority_records")
    if not isinstance(authority_records, list) or [item.get("authority_domain") for item in authority_records if isinstance(item, dict)] != ["notion", "second_brain", "identity"]:
        raise GenerationIntegrityError("authority denominator mismatch")
    for record in authority_records:
        if not isinstance(record, dict) or record.get("verification_state") != "verified":
            raise GenerationIntegrityError("authority is not verified")
        relative = record.get("snapshot_relative_path")
        path = root / Path(str(relative))
        observed = _database_observation(path)
        if (
            _sha256_file(path) != record.get("snapshot_sha256")
            or path.stat().st_size != record.get("snapshot_size_bytes")
            or observed["quick_check"] != record.get("quick_check")
            or observed["foreign_key_violation_count"] != record.get("foreign_key_violation_count")
            or observed["schema_lineage_sha256"] != record.get("schema_lineage_sha256")
            or observed["logical_inventory_sha256"] != record.get("logical_inventory_sha256")
        ):
            raise GenerationIntegrityError("authority record verification failed")
        allowed.add(str(relative))
        high_water = record.get("high_water")
        required_high_water = {"audit_id", "outbox_id", "document_sequence", "event_sequence", "snapshot_event_sequence", "publication_revision"}
        if not isinstance(high_water, dict) or set(high_water) != required_high_water:
            raise GenerationIntegrityError("authority high-water denominator mismatch")
        for value in high_water.values():
            if not isinstance(value, dict) or set(value) != {"value", "not_applicable_reason"} or (value["value"] is None) == (not isinstance(value["not_applicable_reason"], str)):
                raise GenerationIntegrityError("authority high-water null reason mismatch")

    identity = root / "authorities" / "identity.sqlite3"
    connection = _sqlite_connect(identity, read_only=True)
    try:
        if int(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]) != 0 or int(connection.execute("SELECT COUNT(*) FROM secret_material").fetchone()[0]) != 0:
            raise GenerationIntegrityError("generation retained sessions or secret material")
        if int(connection.execute("SELECT COUNT(*) FROM auth_state WHERE force_reauthentication<>1").fetchone()[0]) != 0:
            raise GenerationIntegrityError("generation did not force reauthentication")
    finally:
        connection.close()
    _verify_event_continuity(root / "authorities" / "notion.sqlite3")

    for domain in ("notion", "second_brain"):
        blob_manifest = root / "components" / f"{domain}-blob-manifest.json"
        allowed.update(_verify_blob_manifest(root, blob_manifest))
    snapshot = _load_json(root / "components" / "collaboration-snapshot.json")
    events = _load_json(root / "components" / "collaboration-events.json")
    if not isinstance(snapshot, dict) or not isinstance(events, dict) or not isinstance(events.get("events"), list):
        raise GenerationIntegrityError("collaboration snapshot/event export invalid")
    notion_high = manifest.get("publication_boundary", {}).get("notion", {})
    if snapshot.get("event_high_water") != len(events["events"]) or snapshot.get("event_high_water") != notion_high.get("event_sequence", {}).get("value"):
        raise GenerationIntegrityError("snapshot/event high-water mismatch")
    runtime_snapshot = snapshot.get("selected_runtime_snapshot")
    runtime_events = events.get("selected_runtime_update_ledger")
    if (runtime_snapshot is None) != (runtime_events is None):
        raise GenerationIntegrityError("selected runtime snapshot/event pairing mismatch")
    if runtime_snapshot is not None:
        _normalize_selected_runtime_exports(
            {"event_ledger": runtime_events, "snapshot": runtime_snapshot}
        )

    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in allowed:
                raise GenerationIntegrityError("undeclared file in verified generation")
    return manifest


def verify_coordinated_generation(
    realm_root: str | os.PathLike[str], generation_id: str
) -> dict[str, Any]:
    realm = SyntheticCollaborativeRealm.open(realm_root)
    generation = _identifier(generation_id, field="generation_id")
    final = realm.backup_root / "generations" / generation
    if not final.is_dir() or final.is_symlink():
        raise IncompleteGenerationError("verified generation is unavailable")
    return _verify_generation_directory(final)


def _projection_payloads(target: Path) -> dict[str, object]:
    notion = _sqlite_connect(target / "notion" / NOTION_DATABASE_NAME, read_only=True)
    try:
        notion_pages = []
        for row in notion.execute(
            "SELECT id,workspace_id,title,content_json,content_sha256,revision,page_sequence,access_epoch,row_version FROM pages ORDER BY id"
        ).fetchall():
            value = dict(row)
            value["content_json"] = json.loads(str(value["content_json"]))
            notion_pages.append(value)
        events = [dict(row) for row in notion.execute("SELECT * FROM durable_events ORDER BY COALESCE(page_id,''),COALESCE(page_sequence,0),created_at,id").fetchall()]
    finally:
        notion.close()
    sbe = _sqlite_connect(target / SBE_DATABASE, read_only=True)
    try:
        sbe_items = [dict(row) for row in sbe.execute("SELECT * FROM items ORDER BY id").fetchall()]
    finally:
        sbe.close()
    return {
        "notion-pages.json": {"pages": notion_pages},
        "second-brain-items.json": {"items": sbe_items},
        "collaboration-events.json": {"events": events},
    }


def _write_projections(target: Path) -> str:
    projection_root = target / "projections"
    records = []
    for name, payload in sorted(_projection_payloads(target).items()):
        path = projection_root / name
        _atomic_json(path, payload)
        records.append(
            {
                "relative_path": path.relative_to(target).as_posix(),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    manifest = {"format": "synthetic-projection-manifest-v1", "records": records}
    _atomic_json(projection_root / "projection-manifest.json", manifest)
    return _sha256_file(projection_root / "projection-manifest.json")


def restore_coordinated_generation(
    realm_root: str | os.PathLike[str],
    generation_id: str,
    target_root: str | os.PathLike[str],
) -> RestoreResult:
    """Restore a verified generation to one new disposable root."""

    realm = SyntheticCollaborativeRealm.open(realm_root)
    generation = _identifier(generation_id, field="generation_id")
    manifest = verify_coordinated_generation(realm.root, generation)
    target = _validate_temp_child(Path(target_root), must_not_exist=True)
    staging = target.with_name(target.name + ".staging-" + uuid.uuid4().hex)
    _validate_temp_child(staging, must_not_exist=True)
    staging.mkdir(parents=True, exist_ok=False)
    source = realm.backup_root / "generations" / generation
    try:
        _atomic_json(
            staging / RESTORE_MARKER,
            {
                "contract": REALM_CONTRACT,
                "source_generation_id": generation,
                "new_root": True,
                "outbound_integrations_enabled": False,
                "mutation_traffic_enabled": False,
            },
        )
        notion_root = staging / "notion"
        notion_root.mkdir()
        _atomic_json(notion_root / NOTION_MARKER_NAME, _notion_marker())
        shutil.copyfile(source / "authorities" / "notion.sqlite3", notion_root / NOTION_DATABASE_NAME)
        shutil.copyfile(source / "authorities" / "second-brain.sqlite3", staging / SBE_DATABASE)
        shutil.copyfile(source / "authorities" / "identity.sqlite3", staging / IDENTITY_DATABASE)
        for domain in ("notion", "second_brain"):
            blob_manifest = _load_json(source / "components" / f"{domain}-blob-manifest.json")
            for row in blob_manifest["blobs"]:
                relative = str(row["relative_path"])
                destination = staging / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / Path(relative), destination)

        identity = _sqlite_connect(staging / IDENTITY_DATABASE)
        try:
            identity.execute("BEGIN IMMEDIATE")
            identity.execute("DELETE FROM sessions")
            identity.execute("DELETE FROM secret_material")
            identity.execute(
                "UPDATE auth_state SET force_reauthentication=1,reauthentication_epoch=reauthentication_epoch+1"
            )
            identity.commit()
            account_count = int(identity.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
            session_count = int(identity.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            forced = int(identity.execute("SELECT COUNT(*) FROM auth_state WHERE force_reauthentication=1").fetchone()[0])
        except Exception:
            identity.rollback()
            raise
        finally:
            identity.close()

        secret = secrets.token_urlsafe(48)
        security_root = staging / "restore-security"
        security_root.mkdir()
        secret_path = security_root / "session-signing.key"
        with secret_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(secret)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
        fingerprint = _sha256_bytes(secret.encode("utf-8"))
        projection_hash = _write_projections(staging)
        receipt = {
            "format": "synthetic-coordinated-restore-receipt-v1",
            "generation_id": generation,
            "source_manifest_sha256": manifest["manifest_sha256"],
            "target_locator_sha256": _sha256_bytes(os.fspath(target.resolve(strict=False)).encode("utf-8")),
            "new_root": True,
            "outbound_integrations_enabled": False,
            "mutation_traffic_enabled": False,
            "account_count": account_count,
            "session_count": session_count,
            "forced_reauthentication_count": forced,
            "new_signing_secret_fingerprint": fingerprint,
            "projection_manifest_sha256": projection_hash,
            "integrity_mismatch_count": 0,
            "restored_at": _utc_now(),
        }
        _atomic_json(staging / RESTORE_RECEIPT, receipt)
        _verify_restored_directory(staging, source, manifest)
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            _atomic_json(
                staging / "restore-failed.json",
                {"generation_id": generation, "state": "failed", "traffic_enabled": False},
            )
        raise
    verify_restored_root(target)
    return RestoreResult(
        generation_id=generation,
        target_root=target,
        source_manifest_sha256=str(manifest["manifest_sha256"]),
        projection_manifest_sha256=projection_hash,
        account_count=account_count,
        session_count=session_count,
        forced_reauthentication_count=forced,
        signing_secret_fingerprint=fingerprint,
    )


def _verify_restored_directory(target: Path, source: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    marker = _load_json(target / RESTORE_MARKER)
    if not isinstance(marker, dict) or marker.get("contract") != REALM_CONTRACT or marker.get("new_root") is not True or marker.get("outbound_integrations_enabled") is not False or marker.get("mutation_traffic_enabled") is not False:
        raise GenerationIntegrityError("restore marker safety boundary failed")
    DisposableAuthority.open(target / "notion")
    for path in (target / "notion" / NOTION_DATABASE_NAME, target / SBE_DATABASE, target / IDENTITY_DATABASE):
        observation = _database_observation(path)
        if observation["quick_check"] != "ok" or observation["foreign_key_violation_count"] != 0:
            raise GenerationIntegrityError("restored authority integrity failed")
    if _sha256_file(target / "notion" / NOTION_DATABASE_NAME) != _sha256_file(source / "authorities" / "notion.sqlite3"):
        raise GenerationIntegrityError("restored Notion snapshot drift")
    if _sha256_file(target / SBE_DATABASE) != _sha256_file(source / "authorities" / "second-brain.sqlite3"):
        raise GenerationIntegrityError("restored SBE snapshot drift")
    identity = _sqlite_connect(target / IDENTITY_DATABASE, read_only=True)
    try:
        sessions = int(identity.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        secrets_count = int(identity.execute("SELECT COUNT(*) FROM secret_material").fetchone()[0])
        accounts = int(identity.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
        forced = int(identity.execute("SELECT COUNT(*) FROM auth_state WHERE force_reauthentication=1").fetchone()[0])
        if sessions != 0 or secrets_count != 0 or forced != accounts:
            raise GenerationIntegrityError("restore revived credentials or failed forced reauthentication")
    finally:
        identity.close()
    _verify_event_continuity(target / "notion" / NOTION_DATABASE_NAME)
    source_snapshot = _load_json(source / "components" / "collaboration-snapshot.json")
    restored_snapshot = _collaboration_snapshot(
        target / "notion" / NOTION_DATABASE_NAME,
        selected_runtime_snapshot=(
            source_snapshot.get("selected_runtime_snapshot")
            if isinstance(source_snapshot, dict)
            else None
        ),
    )
    if source_snapshot != restored_snapshot:
        raise GenerationIntegrityError("restored collaboration snapshot mismatch")
    for domain in ("notion", "second_brain"):
        blob_manifest = _load_json(source / "components" / f"{domain}-blob-manifest.json")
        for row in blob_manifest["blobs"]:
            restored = target / Path(str(row["relative_path"]))
            if not restored.is_file() or _sha256_file(restored) != row["sha256"] or restored.stat().st_size != row["size_bytes"]:
                raise GenerationIntegrityError("restored blob mismatch")
    expected_payloads = _projection_payloads(target)
    projection_manifest = _load_json(target / "projections" / "projection-manifest.json")
    if not isinstance(projection_manifest, dict) or projection_manifest.get("format") != "synthetic-projection-manifest-v1":
        raise GenerationIntegrityError("projection manifest invalid")
    for name, payload in expected_payloads.items():
        if _load_json(target / "projections" / name) != payload:
            raise GenerationIntegrityError("projection canonical mismatch")
    receipt = _load_json(target / RESTORE_RECEIPT)
    if not isinstance(receipt, dict) or receipt.get("source_manifest_sha256") != manifest.get("manifest_sha256") or receipt.get("session_count") != 0 or receipt.get("integrity_mismatch_count") != 0:
        raise GenerationIntegrityError("restore receipt invalid")
    secret_path = target / "restore-security" / "session-signing.key"
    if not secret_path.is_file() or len(secret_path.read_text(encoding="utf-8").strip().encode("utf-8")) < 32:
        raise GenerationIntegrityError("new session-signing secret unavailable")
    if _sha256_bytes(secret_path.read_text(encoding="utf-8").strip().encode("utf-8")) != receipt.get("new_signing_secret_fingerprint"):
        raise GenerationIntegrityError("new session-signing secret fingerprint mismatch")
    return receipt


def verify_restored_root(target_root: str | os.PathLike[str]) -> dict[str, Any]:
    target = _validate_temp_child(Path(target_root))
    try:
        marker = _load_json(target / RESTORE_MARKER)
        receipt = _load_json(target / RESTORE_RECEIPT)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GenerationIntegrityError("restore evidence unavailable") from exc
    if not isinstance(marker, dict) or not isinstance(receipt, dict):
        raise GenerationIntegrityError("restore evidence invalid")
    # Reconstruct only the source-independent safety and projection checks here.
    if marker.get("contract") != REALM_CONTRACT or marker.get("new_root") is not True or marker.get("outbound_integrations_enabled") is not False or marker.get("mutation_traffic_enabled") is not False:
        raise GenerationIntegrityError("restore safety marker drift")
    DisposableAuthority.open(target / "notion")
    identity = _sqlite_connect(target / IDENTITY_DATABASE, read_only=True)
    try:
        accounts = int(identity.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
        sessions = int(identity.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        forced = int(identity.execute("SELECT COUNT(*) FROM auth_state WHERE force_reauthentication=1").fetchone()[0])
        secrets_count = int(identity.execute("SELECT COUNT(*) FROM secret_material").fetchone()[0])
    finally:
        identity.close()
    if sessions != 0 or secrets_count != 0 or forced != accounts:
        raise GenerationIntegrityError("restored authentication safety drift")
    for name, payload in _projection_payloads(target).items():
        if _load_json(target / "projections" / name) != payload:
            raise GenerationIntegrityError("restored projection drift")
    secret = (target / "restore-security" / "session-signing.key").read_text(encoding="utf-8").strip()
    if _sha256_bytes(secret.encode("utf-8")) != receipt.get("new_signing_secret_fingerprint"):
        raise GenerationIntegrityError("restored signing secret drift")
    return receipt


__all__ = [
    "FAULT_POINTS",
    "GENERATION_FORMAT",
    "GenerationIntegrityError",
    "GenerationResult",
    "IncompleteGenerationError",
    "InjectedBackupFailure",
    "MANDATORY_COMPONENT_IDS",
    "REALM_CONTRACT",
    "RestoreResult",
    "SyntheticBackupBoundaryError",
    "SyntheticCollaborativeRealm",
    "create_coordinated_generation",
    "initialize_synthetic_collaborative_realm",
    "restore_coordinated_generation",
    "verify_coordinated_generation",
    "verify_restored_root",
]
