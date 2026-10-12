"""Hash-bind a create-only frozen copy to a stable source observation.

This tool does not quiesce writers or create the copy.  It verifies that the
already-created copy exactly matches two consecutive source observations and
emits a body-free operator receipt suitable for P2.1's external attestation
input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "code-assist.frozen-copy-attestation"
SCHEMA_VERSION = 1
DEFAULT_EXCLUDED_DIRECTORIES = (".pytest_cache", "MODEL_GGUF", "__pycache__")
DEFAULT_EXCLUDED_SUFFIXES = (".pyc",)
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class AttestationError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_root(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise AttestationError("root must be absolute")
    resolved = path.resolve(strict=True)
    info = os.lstat(resolved)
    if not stat.S_ISDIR(info.st_mode):
        raise AttestationError("root must be a directory")
    if int(getattr(info, "st_file_attributes", 0)) & _REPARSE_FLAG:
        raise AttestationError("root must not be a reparse point")
    return resolved


def _path_key(relative: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative.replace("\\", "/"))
    if normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in normalized.split("/")
    ):
        raise AttestationError("unsafe relative path")
    return normalized.casefold()


def _scan(root: Path) -> dict[str, tuple[int, int, str]]:
    rows: dict[str, tuple[int, int, str]] = {}
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            if name in DEFAULT_EXCLUDED_DIRECTORIES:
                continue
            entry = current_path / name
            info = os.lstat(entry)
            if int(getattr(info, "st_file_attributes", 0)) & _REPARSE_FLAG or stat.S_ISLNK(
                info.st_mode
            ):
                raise AttestationError("reparse directory encountered")
            if not stat.S_ISDIR(info.st_mode):
                raise AttestationError("non-directory entry encountered in directory list")
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            if Path(name).suffix.casefold() in DEFAULT_EXCLUDED_SUFFIXES:
                continue
            entry = current_path / name
            before = os.lstat(entry)
            if int(getattr(before, "st_file_attributes", 0)) & _REPARSE_FLAG or stat.S_ISLNK(
                before.st_mode
            ):
                raise AttestationError("reparse file encountered")
            if not stat.S_ISREG(before.st_mode):
                raise AttestationError("special file encountered")
            relative = entry.relative_to(root).as_posix()
            key = _path_key(relative)
            if key in rows:
                raise AttestationError("normalized path collision")
            digest = _sha256_file(entry)
            after = os.lstat(entry)
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ino != after.st_ino
            ):
                raise AttestationError("file changed during observation")
            rows[key] = (int(after.st_size), int(after.st_mtime_ns), digest)
    return rows


def _tree_digest(rows: dict[str, tuple[int, int, str]]) -> str:
    # mtime is intentionally excluded from cross-root equality; it remains in
    # the two source observations used for the stability check.
    return hashlib.sha256(
        _canonical_bytes(
            [
                {"path_key": key, "sha256": value[2], "size": value[0]}
                for key, value in sorted(rows.items())
            ]
        )
    ).hexdigest()


def attest_frozen_copy(
    source_root: str | Path,
    snapshot_root: str | Path,
    *,
    snapshot_id: str,
    snapshot_created_at_utc: str,
) -> dict[str, Any]:
    source = _safe_root(source_root)
    snapshot = _safe_root(snapshot_root)
    if source == snapshot or source in snapshot.parents or snapshot in source.parents:
        raise AttestationError("source and snapshot roots must be disjoint")
    if not snapshot_id or len(snapshot_id) > 128:
        raise AttestationError("snapshot_id is invalid")
    try:
        parsed_time = datetime.fromisoformat(snapshot_created_at_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationError("snapshot_created_at_utc is invalid") from exc
    if parsed_time.tzinfo is None or parsed_time.utcoffset() != timezone.utc.utcoffset(parsed_time):
        raise AttestationError("snapshot_created_at_utc must be UTC")

    source_before = _scan(source)
    snapshot_rows = _scan(snapshot)
    source_after = _scan(source)
    source_stable = source_before == source_after
    source_digest = _tree_digest(source_after)
    snapshot_digest = _tree_digest(snapshot_rows)
    snapshot_matches = source_digest == snapshot_digest
    payload = {
        "report_schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "snapshot_created_at_utc": snapshot_created_at_utc,
        "source_root_path_sha256": hashlib.sha256(
            os.fspath(source).encode("utf-8")
        ).hexdigest(),
        "snapshot_root_path_sha256": hashlib.sha256(
            os.fspath(snapshot).encode("utf-8")
        ).hexdigest(),
        "source_tree_sha256": source_digest,
        "snapshot_tree_sha256": snapshot_digest,
        "file_count": len(snapshot_rows),
        "total_bytes": sum(value[0] for value in snapshot_rows.values()),
        "excluded_directories": list(DEFAULT_EXCLUDED_DIRECTORIES),
        "excluded_suffixes": list(DEFAULT_EXCLUDED_SUFFIXES),
        "source_stable": source_stable,
        "snapshot_matches": snapshot_matches,
        "passed": bool(source_stable and snapshot_matches),
    }
    return {
        **payload,
        "attestation_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    }


def _write_create_only(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--snapshot-root", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--snapshot-created-at-utc", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    try:
        report = attest_frozen_copy(
            arguments.source_root,
            arguments.snapshot_root,
            snapshot_id=arguments.snapshot_id,
            snapshot_created_at_utc=arguments.snapshot_created_at_utc,
        )
        _write_create_only(Path(arguments.output), report)
    except FileExistsError:
        print("OUTPUT_EXISTS", file=sys.stderr)
        return 3
    except (AttestationError, OSError, ValueError):
        print("ATTESTATION_FAILED", file=sys.stderr)
        return 3
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

