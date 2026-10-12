"""Offline v22-to-v23 opt-in; never restarts servers or rewrites retained Wikis."""
import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sqlite3
from uuid import uuid4

from code_assist_v1.tools.migrate_structured_authoring_preview import _inspect, _write_new
from code_assist_v1.tools.migrate_authoring_only_preview import _file_sha256, _registered_processes


def _verified_backup(root, checkpoint, current):
    from code_assist_v1.knowledge_core.migrations.structured_authoring_catalog import V022_EXPECTED_SCHEMA_SHA256
    record = json.loads(checkpoint.read_text(encoding="utf-8"))
    backup = Path(record["backup"]).resolve(strict=True)
    if (backup.parent != (root / ".runtime").resolve(strict=True)
            or not backup.name.startswith("before-full-label-authoring-v23-") or backup.suffix != ".db"
            or record.get("database") != str(root / "core/knowledge_core.db")
            or _file_sha256(backup) != record.get("backup_sha256")):
        raise RuntimeError("Full-label rollback receipt/path/hash is invalid")
    observed = _inspect(backup)
    if (observed["schema_version"] != 22 or observed["schema_sha256"] != V022_EXPECTED_SCHEMA_SHA256
            or observed != record.get("before")
            or observed["retained_records_sha256"] != current["retained_records_sha256"]):
        raise RuntimeError("The verified v22 rollback does not preserve current records")
    return record


def migrate(*, apply=False):
    from code_assist_v1.tools.preservation_preview_runtime import (
        REGISTERED_ROOT, structured_authoring_enabled, full_label_authoring_enabled, factory_for,
    )
    from code_assist_v1.tools.full_second_brain_preview import _configure_environment, _load_marker
    from code_assist_v1.knowledge_core.migrations.structured_authoring_catalog import V022_EXPECTED_SCHEMA_SHA256
    from code_assist_v1.knowledge_core.migrations.full_label_authoring_catalog import (
        FULL_LABEL_AUTHORING_MIGRATION_CATALOG, V023_EXPECTED_SCHEMA_SHA256,
    )
    from code_assist_v1.knowledge_core.wiki_preservation import CAPTION_AUTHORING_VERSION
    from markdown_it import MarkdownIt
    MarkdownIt("commonmark")
    root = REGISTERED_ROOT.resolve(strict=True)
    marker = _load_marker(root)
    clone = json.loads((root / ".runtime/clone-receipt.json").read_text(encoding="utf-8"))
    if (marker.get("clone_port") != 10421 or clone.get("port") != 10421
            or Path(clone["target_root"]).resolve(strict=True) != root
            or clone.get("source_mutated_by_clone") is not False
            or not structured_authoring_enabled(root)):
        raise RuntimeError("Full-label authoring requires the registered enabled v22 clone")
    database = root / "core/knowledge_core.db"
    activation = root / ".runtime/full-label-authoring-v23-enabled.json"
    checkpoint = root / ".runtime/full-label-authoring-v23-rollback.json"
    before = _inspect(database)
    if before["schema_sha256"] != {22: V022_EXPECTED_SCHEMA_SHA256,
            23: V023_EXPECTED_SCHEMA_SHA256}.get(before["schema_version"]):
        raise RuntimeError("Unexpected predecessor/candidate schema")
    if activation.exists() and not full_label_authoring_enabled(root):
        raise RuntimeError("Invalid existing full-label activation")
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
        if before["schema_version"] != 22:
            raise RuntimeError("An unactivated v23 requires its existing verified v22 rollback receipt")
        backup = root / ".runtime" / ("before-full-label-authoring-v23-" + str(uuid4()) + ".db")
        with backup.open("xb"):
            pass
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        if _inspect(backup) != before:
            raise RuntimeError("The v22 backup differs from its offline predecessor")
        _write_new(checkpoint, {"database": str(database), "backup": str(backup),
            "backup_sha256": _file_sha256(backup), "before": before})
    rollback = _verified_backup(root, checkpoint, before)
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import SingleMigrator, MigrationStorageLayout
    factory = ConnectionFactory._for_isolated_tests(database, FULL_LABEL_AUTHORING_MIGRATION_CATALOG)
    migration = SingleMigrator(factory, app_version="isolated-10421-full-label-authoring-v23",
        storage_layout=MigrationStorageLayout(data_root=root, database=database,
            blob_root=root / "blobs", projection_root=root / "projections")).run()
    after = _inspect(database)
    if (after["schema_version"] != 23 or after["schema_sha256"] != V023_EXPECTED_SCHEMA_SHA256
            or before["retained_records_sha256"] != after["retained_records_sha256"]):
        raise RuntimeError("Full-label migration did not preserve existing records")
    evidence = {"port": 10421, "schema_version": 23, "policy_version": CAPTION_AUTHORING_VERSION,
        "extractor_version": "rich-source-v3-bounded-tables", "semantic_review_performed": False,
        "migration": asdict(migration), "backup": rollback["backup"],
        "backup_sha256": rollback["backup_sha256"], "backup_schema_version": 22,
        "before": rollback["before"], "after": after}
    _write_new(activation, evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(migrate(apply=parser.parse_args().apply)), flush=True)
