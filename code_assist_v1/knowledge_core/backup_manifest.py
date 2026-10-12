"""Strict, self-hashed P7.4 backup manifest contract.

The manifest contains only operational identifiers and content hashes.  It
never embeds configuration values, HMAC keys, canonical bodies, or exception
text.  ``manifest_sha256`` is the SHA-256 of the canonical JSON object with
only that field omitted; ``blobs_sha256`` seals the sorted immutable-blob
identity rows independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
import uuid
from typing import Mapping, Sequence

from .migrations.canonical_catalog import CANONICAL_MIGRATION_CATALOG
from .runtime import APPROVED_SQLITE_SOURCE_ID, APPROVED_SQLITE_VERSION


BACKUP_MANIFEST_SCHEMA_VERSION = 1
BACKUP_SCHEMA_VERSION = 1
BACKUP_SNAPSHOT_RELATIVE_PATH = "database/knowledge-core.sqlite"
BACKUP_APP_NAME = "code-assist"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z", re.ASCII)
_UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z",
    re.ASCII,
)

_MANIFEST_KEYS = frozenset(
    {
        "app_name",
        "app_version",
        "backup_run_id",
        "blob_bytes",
        "blob_count",
        "blobs",
        "blobs_sha256",
        "build_revision",
        "completed_at",
        "configuration_version",
        "created_at",
        "foreign_key_violation_count",
        "lease_hmac_key_set_version",
        "manifest_schema_version",
        "manifest_sha256",
        "physical_schema_sha256",
        "physical_schema_version",
        "quick_check",
        "schema_catalog_sha256",
        "schema_version",
        "snapshot_audit_high_water",
        "snapshot_db_relative_path",
        "snapshot_db_sha256",
        "snapshot_db_size_bytes",
        "sqlite_source_id",
        "sqlite_version",
        "state",
    }
)
_BLOB_KEYS = frozenset(
    {
        "copied_at",
        "error",
        "relative_path",
        "sha256",
        "size_bytes",
        "status",
        "verified_at",
    }
)


@dataclass(frozen=True, slots=True)
class VerifiedBackupBlob:
    sha256: str
    size_bytes: int
    copied_at: str
    verified_at: str

    @property
    def relative_path(self) -> str:
        return f"blobs/{self.sha256[:2]}/{self.sha256[2:4]}/{self.sha256}"

    def row(self) -> dict[str, object]:
        return {
            "copied_at": self.copied_at,
            "error": None,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "status": "verified",
            "verified_at": self.verified_at,
        }


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_canonical(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def manifest_sha256(value: Mapping[str, object]) -> str:
    return _sha256_canonical(
        {key: item for key, item in value.items() if key != "manifest_sha256"}
    )


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _version(value: object) -> bool:
    return isinstance(value, str) and _VERSION_RE.fullmatch(value) is not None


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == timezone.utc else None


def _safe_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and unicodedata.normalize("NFKC", value) == value
        and not any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}
            for character in value
        )
    )


def _blob_identity(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "relative_path": row.get("relative_path"),
            "sha256": row.get("sha256"),
            "size_bytes": row.get("size_bytes"),
        }
        for row in rows
    ]


def backup_manifest_errors(
    value: object, *, verify_hash: bool = True
) -> tuple[str, ...]:
    """Return stable validation codes and never raise for JSON-shaped input."""

    errors: set[str] = set()
    if not isinstance(value, Mapping):
        return ("manifest_shape",)
    try:
        if set(value) != _MANIFEST_KEYS:
            errors.add("manifest_shape")
        if value.get("manifest_schema_version") != BACKUP_MANIFEST_SCHEMA_VERSION:
            errors.add("manifest_version")
        if value.get("state") != "verified":
            errors.add("manifest_state")
        if not _uuid(value.get("backup_run_id")):
            errors.add("backup_run_id")
        if value.get("app_name") != BACKUP_APP_NAME:
            errors.add("app_name")
        if not _safe_text(value.get("app_version"), maximum=80):
            errors.add("app_version")
        if not _sha(value.get("build_revision")):
            errors.add("build_revision")
        catalog = CANONICAL_MIGRATION_CATALOG
        latest = catalog.migration(catalog.latest_version)
        if value.get("schema_version") != BACKUP_SCHEMA_VERSION:
            errors.add("schema_version")
        if value.get("physical_schema_version") != catalog.latest_version:
            errors.add("physical_schema_version")
        if value.get("schema_catalog_sha256") != catalog.checksum_sha256:
            errors.add("schema_catalog_sha256")
        if value.get("physical_schema_sha256") != latest.expected_schema_sha256:
            errors.add("physical_schema_sha256")
        if value.get("sqlite_version") != APPROVED_SQLITE_VERSION:
            errors.add("sqlite_version")
        if value.get("sqlite_source_id") != APPROVED_SQLITE_SOURCE_ID:
            errors.add("sqlite_source_id")
        if value.get("snapshot_db_relative_path") != BACKUP_SNAPSHOT_RELATIVE_PATH:
            errors.add("snapshot_path")
        for key in (
            "snapshot_db_size_bytes",
            "snapshot_audit_high_water",
            "blob_count",
            "blob_bytes",
            "foreign_key_violation_count",
        ):
            if not _integer(value.get(key)):
                errors.add(key)
        if not _sha(value.get("snapshot_db_sha256")):
            errors.add("snapshot_db_sha256")
        if value.get("quick_check") != "ok" or value.get(
            "foreign_key_violation_count"
        ) != 0:
            errors.add("snapshot_integrity")
        for key in ("configuration_version", "lease_hmac_key_set_version"):
            if not _version(value.get(key)):
                errors.add(key)
        created = _utc(value.get("created_at"))
        completed = _utc(value.get("completed_at"))
        if created is None or completed is None:
            errors.add("manifest_time")
        elif completed < created:
            errors.add("manifest_chronology")

        raw_blobs = value.get("blobs")
        blobs: list[Mapping[str, object]] = []
        if not isinstance(raw_blobs, list):
            errors.add("blobs_shape")
        else:
            for row in raw_blobs:
                if not isinstance(row, Mapping) or set(row) != _BLOB_KEYS:
                    errors.add("blob_shape")
                    continue
                blobs.append(row)
                digest = row.get("sha256")
                size = row.get("size_bytes")
                copied = _utc(row.get("copied_at"))
                verified = _utc(row.get("verified_at"))
                if not _sha(digest) or not _integer(size):
                    errors.add("blob_identity")
                expected_path = (
                    None
                    if not isinstance(digest, str)
                    else f"blobs/{digest[:2]}/{digest[2:4]}/{digest}"
                )
                if row.get("relative_path") != expected_path:
                    errors.add("blob_path")
                if row.get("status") != "verified" or row.get("error") is not None:
                    errors.add("blob_status")
                if copied is None or verified is None or verified < copied:
                    errors.add("blob_time")
            digests = [row.get("sha256") for row in blobs]
            if digests != sorted(digests) or len(digests) != len(set(digests)):
                errors.add("blob_order")
            if value.get("blob_count") != len(blobs):
                errors.add("blob_count")
            if all(_integer(row.get("size_bytes")) for row in blobs):
                if value.get("blob_bytes") != sum(
                    int(row["size_bytes"]) for row in blobs
                ):
                    errors.add("blob_bytes")
            if value.get("blobs_sha256") != _sha256_canonical(
                _blob_identity(blobs)
            ):
                errors.add("blobs_sha256")
        if not _sha(value.get("blobs_sha256")):
            errors.add("blobs_sha256")
        if not _sha(value.get("manifest_sha256")):
            errors.add("manifest_sha256")
        elif verify_hash and value.get("manifest_sha256") != manifest_sha256(value):
            errors.add("manifest_sha256")
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.add("manifest_shape")
    return tuple(sorted(errors))


def build_backup_manifest(
    *,
    backup_run_id: str,
    app_version: str,
    build_revision: str,
    snapshot_db_sha256: str,
    snapshot_db_size_bytes: int,
    snapshot_audit_high_water: int,
    blobs: Sequence[VerifiedBackupBlob],
    configuration_version: str,
    lease_hmac_key_set_version: str,
    created_at: str,
    completed_at: str,
) -> dict[str, object]:
    if any(not isinstance(blob, VerifiedBackupBlob) for blob in blobs):
        raise TypeError("blobs must contain only VerifiedBackupBlob values.")
    rows = [blob.row() for blob in sorted(blobs, key=lambda item: item.sha256)]
    catalog = CANONICAL_MIGRATION_CATALOG
    latest = catalog.migration(catalog.latest_version)
    manifest: dict[str, object] = {
        "app_name": BACKUP_APP_NAME,
        "app_version": app_version,
        "backup_run_id": backup_run_id,
        "blob_bytes": sum(blob.size_bytes for blob in blobs),
        "blob_count": len(blobs),
        "blobs": rows,
        "blobs_sha256": _sha256_canonical(_blob_identity(rows)),
        "build_revision": build_revision,
        "completed_at": completed_at,
        "configuration_version": configuration_version,
        "created_at": created_at,
        "foreign_key_violation_count": 0,
        "lease_hmac_key_set_version": lease_hmac_key_set_version,
        "manifest_schema_version": BACKUP_MANIFEST_SCHEMA_VERSION,
        "manifest_sha256": "0" * 64,
        "physical_schema_sha256": latest.expected_schema_sha256,
        "physical_schema_version": catalog.latest_version,
        "quick_check": "ok",
        "schema_catalog_sha256": catalog.checksum_sha256,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "snapshot_audit_high_water": snapshot_audit_high_water,
        "snapshot_db_relative_path": BACKUP_SNAPSHOT_RELATIVE_PATH,
        "snapshot_db_sha256": snapshot_db_sha256,
        "snapshot_db_size_bytes": snapshot_db_size_bytes,
        "sqlite_source_id": APPROVED_SQLITE_SOURCE_ID,
        "sqlite_version": APPROVED_SQLITE_VERSION,
        "state": "verified",
    }
    manifest["manifest_sha256"] = manifest_sha256(manifest)
    errors = backup_manifest_errors(manifest)
    if errors:
        raise ValueError("Backup manifest is invalid: " + ",".join(errors))
    return manifest


__all__ = [
    "BACKUP_APP_NAME",
    "BACKUP_MANIFEST_SCHEMA_VERSION",
    "BACKUP_SCHEMA_VERSION",
    "BACKUP_SNAPSHOT_RELATIVE_PATH",
    "VerifiedBackupBlob",
    "backup_manifest_errors",
    "build_backup_manifest",
    "canonical_json_bytes",
    "manifest_sha256",
]
