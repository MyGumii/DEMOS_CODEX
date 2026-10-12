"""Offline opt-in v22 rollout; never stops services, changes rollback roots or reviews Wikis."""
import argparse
from contextlib import closing
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sqlite3
from uuid import uuid4

from code_assist_v1.tools.migrate_authoring_only_preview import (
    _file_sha256, _registered_processes, _retained_records_digest,
)


def _inspect(database):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        if (connection.execute("PRAGMA quick_check").fetchone() != ("ok",)
                or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise RuntimeError("Structured-authoring database integrity check failed")
        from code_assist_v1.knowledge_core.migrations.runner import schema_sha256
        return {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "schema_sha256": schema_sha256(connection),
            "retained_records_sha256": _retained_records_digest(connection)}


def _write_new(path, value):
    # Publish an already-complete receipt without replacing an existing name.
    # A process interrupted before the hard-link leaves only an unreferenced
    # temporary file, not a torn activation receipt. Same-directory NTFS link
    # creation is atomic and fails if the destination already exists.
    encoded = json.dumps(value, indent=2).encode("utf-8")
    temporary = path.with_name(path.name + ".pending-" + str(uuid4()))
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()  # only the newly created exact temporary pathname


def _verified_backup(root, checkpoint, current):
    from code_assist_v1.knowledge_core.migrations.authoring_only_catalog import V021_EXPECTED_SCHEMA_SHA256
    record = json.loads(checkpoint.read_text(encoding="utf-8"))
    backup = Path(record["backup"]).resolve(strict=True)
    if (backup.parent != (root / ".runtime").resolve(strict=True)
            or not backup.name.startswith("before-structured-authoring-v22-") or backup.suffix != ".db"
            or record.get("database") != str(root / "core/knowledge_core.db")
            or _file_sha256(backup) != record.get("backup_sha256")):
        raise RuntimeError("Structured-authoring rollback receipt/path/hash is invalid")
    observed = _inspect(backup)
    if (observed["schema_version"] != 21 or observed["schema_sha256"] != V021_EXPECTED_SCHEMA_SHA256
            or observed != record.get("before")
            or observed["retained_records_sha256"] != current["retained_records_sha256"]):
        raise RuntimeError("The verified v21 rollback does not preserve the current records")
    return record


def migrate(*, apply=False):
    from code_assist_v1.tools.preservation_preview_runtime import (
        REGISTERED_ROOT, enabled, authoring_only_enabled, structured_authoring_enabled, factory_for,
    )
    from code_assist_v1.tools.full_second_brain_preview import _configure_environment, _load_marker
    from code_assist_v1.knowledge_core.migrations.authoring_only_catalog import V021_EXPECTED_SCHEMA_SHA256
    from code_assist_v1.knowledge_core.migrations.structured_authoring_catalog import (
        STRUCTURED_AUTHORING_MIGRATION_CATALOG, V022_EXPECTED_SCHEMA_SHA256,
    )
    from code_assist_v1.knowledge_core.wiki_preservation import STRUCTURED_AUTHORING_VERSION
    # Check optional parser availability before offline changes.
    from markdown_it import MarkdownIt
    MarkdownIt("commonmark")
    root = REGISTERED_ROOT.resolve(strict=True)
    marker = _load_marker(root)
    clone = json.loads((root / ".runtime/clone-receipt.json").read_text(encoding="utf-8"))
    if (marker.get("clone_port") != 10421 or clone.get("port") != 10421
            or Path(clone["target_root"]).resolve(strict=True) != root
            or clone.get("source_mutated_by_clone") is not False
            or not enabled(root) or not authoring_only_enabled(root)):
        raise RuntimeError("Structured authoring requires the registered v21 clone")
    database = root / "core/knowledge_core.db"
    activation = root / ".runtime/structured-authoring-v22-enabled.json"
    checkpoint = root / ".runtime/structured-authoring-v22-rollback.json"
    before = _inspect(database)
    expected = {21: V021_EXPECTED_SCHEMA_SHA256, 22: V022_EXPECTED_SCHEMA_SHA256}
    if before["schema_sha256"] != expected.get(before["schema_version"]):
        raise RuntimeError("Unexpected predecessor/candidate schema")
    if activation.exists() and not structured_authoring_enabled(root):
        raise RuntimeError("Invalid existing structured activation")
    if not apply:
        return {"status": "read_only_preflight", "port": 10421, "database": str(database),
            "activation_exists": activation.exists(), "rollback_receipt_exists": checkpoint.exists(), **before}
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 10421)) == 0:
            raise RuntimeError("Stop only10421 and its workers before offline migration")
    if _registered_processes(root):
        raise RuntimeError("Registered10421 workers are still running")
    _configure_environment(root, port=10421, codex=True)
    if activation.exists():
        connection = factory_for(root).open(read_only=True)
        connection.close()
        return {"status": "already_enabled", "port": 10421}
    if not checkpoint.exists():
        if before["schema_version"] != 21:
            raise RuntimeError("An unactivated v22 requires its existing verified v21 rollback receipt")
        backup = root / ".runtime" / ("before-structured-authoring-v22-" + str(uuid4()) + ".db")
        with backup.open("xb"):
            pass
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        record = {"database": str(database), "backup": str(backup),
            "backup_sha256": _file_sha256(backup), "before": before}
        if _inspect(backup) != before:
            raise RuntimeError("The v21 backup differs from the offline predecessor")
        # Persist the actual predecessor before migration, not a post-migration backup.
        _write_new(checkpoint, record)
    rollback = _verified_backup(root, checkpoint, before)
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import SingleMigrator, MigrationStorageLayout
    factory = ConnectionFactory._for_isolated_tests(database, STRUCTURED_AUTHORING_MIGRATION_CATALOG)
    migration = SingleMigrator(factory, app_version="isolated-10421-structured-authoring-v22",
        storage_layout=MigrationStorageLayout(data_root=root, database=database,
            blob_root=root / "blobs", projection_root=root / "projections")).run()
    after = _inspect(database)
    if (after["schema_version"] != 22 or after["schema_sha256"] != V022_EXPECTED_SCHEMA_SHA256
            or before["retained_records_sha256"] != after["retained_records_sha256"]):
        raise RuntimeError("Structured migration did not preserve existing records")
    evidence = {"port": 10421, "schema_version": 22, "policy_version": STRUCTURED_AUTHORING_VERSION,
        "extractor_version": "rich-source-v3-bounded-tables", "semantic_review_performed": False,
        "migration": asdict(migration), "backup": rollback["backup"],
        "backup_sha256": rollback["backup_sha256"], "backup_schema_version": 21,
        "before": rollback["before"], "after": after, "old_roots_modified": False}
    _write_new(activation, evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(migrate(apply=parser.parse_args().apply)), flush=True)


if __name__ == "__main__":
    main()
