"""Offline v21 activation, strictly limited to the user-authorized 10421 clone."""
import argparse
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
from uuid import uuid4


def _registered_processes(root):
    # This deliberately Windows-only deployment tool must also reject orphan
    # workers which do not own the web listening socket. Never stop them here.
    script = "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, text=True, timeout=30)
    rows = json.loads(result.stdout or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    target = str(root).replace("/", "\\").casefold()
    return [row["ProcessId"] for row in rows
        if target in (row.get("CommandLine") or "").replace("/", "\\").casefold()]


def _retained_records_digest(connection):
    # Hash canonical records in deterministic order without emitting contents
    # (including user records) or loading a full database into memory.
    names = ("app_user", "knowledge_item", "knowledge_revision", "knowledge_revision_body",
        "publication_preview", "publication_preservation_request", "publication_preservation_run",
        "publication_preservation_chunk", "publication_preservation_receipt",
        "publication_preservation_receipt_body", "publication_preservation_failure",
        "preserved_wiki_snapshot", "preserved_wiki_snapshot_body")
    digest = sha256()
    for name in names:
        digest.update(name.encode())
        for row in connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid'):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _file_sha256(path):
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate(*, apply=False):
    from code_assist_v1.tools.preservation_preview_runtime import REGISTERED_ROOT
    from code_assist_v1.tools.full_second_brain_preview import _configure_environment, _load_marker
    from code_assist_v1.knowledge_core.wiki_preservation import AUTHORING_ONLY_VERSION
    root = REGISTERED_ROOT.resolve(strict=True)
    marker = _load_marker(root)
    receipt = json.loads((root / ".runtime/clone-receipt.json").read_text(encoding="utf-8"))
    previous = json.loads((root / ".runtime/preservation-v20-enabled.json").read_text(encoding="utf-8"))
    if (marker.get("clone_port") != 10421 or receipt.get("port") != 10421
            or Path(receipt["target_root"]).resolve(strict=True) != root
            or receipt.get("source_mutated_by_clone") is not False
            or previous.get("port") != 10421 or previous.get("schema_version") != 20):
        raise RuntimeError("Not the registered v20 isolated clone")
    _configure_environment(root, port=10421, codex=True)
    database = root / "core/knowledge_core.db"
    activation = root / ".runtime/authoring-only-v21-enabled.json"
    counts_sql = "SELECT (SELECT count(*) FROM app_user),(SELECT count(*) FROM knowledge_item),(SELECT count(*) FROM knowledge_revision),(SELECT count(*) FROM publication_preservation_run),(SELECT count(*) FROM publication_preservation_receipt)"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        before = tuple(source.execute(counts_sql).fetchone())
        retained_before = _retained_records_digest(source)
        if source.execute("PRAGMA quick_check").fetchone() != ("ok",) or source.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Database integrity preflight failed")
    if not apply:
        return {"status": "read_only_preflight", "port": 10421, "activation_exists": activation.exists(), "counts": before}
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 10421)) == 0:
            raise RuntimeError("Stop only the 10421 server and its workers before offline migration")
    if _registered_processes(root):
        raise RuntimeError("Registered 10421 Python server/workers remain; stop them before migration")
    if activation.exists():
        from code_assist_v1.tools.preservation_preview_runtime import factory_for, authoring_only_enabled
        if not authoring_only_enabled(root):
            raise RuntimeError("Existing activation receipt is invalid")
        connection = factory_for(root).open(read_only=True)
        connection.close()
        return {"status": "already_enabled", "port": 10421}
    backup = root / ".runtime" / ("before-authoring-only-v21-" + str(uuid4()) + ".db")
    with backup.open("xb"):
        pass
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("Backup integrity check failed")
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import SingleMigrator, MigrationStorageLayout
    from code_assist_v1.knowledge_core.migrations.authoring_only_catalog import AUTHORING_ONLY_MIGRATION_CATALOG
    factory = ConnectionFactory._for_isolated_tests(database, AUTHORING_ONLY_MIGRATION_CATALOG)
    result = SingleMigrator(factory, app_version="isolated-10421-authoring-only-v21",
        storage_layout=MigrationStorageLayout(data_root=root, database=database,
            blob_root=root / "blobs", projection_root=root / "projections")).run()
    connection = factory.open(read_only=True)
    try:
        after = tuple(connection.execute(counts_sql).fetchone())
        retained_after = _retained_records_digest(connection)
        if before != after or retained_before != retained_after:
            raise RuntimeError("Migration changed existing records or broke foreign keys")
    finally:
        connection.close()
    # Administrative integrity PRAGMAs are intentionally not allowed through
    # the application's restricted read-only authorizer. Check offline via a
    # dedicated mode=ro SQLite connection, as in the preflight and backup.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as verified:
        if verified.execute("PRAGMA quick_check").fetchone() != ("ok",) or verified.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Post-migration database integrity failed")
    evidence = {"port": 10421, "schema_version": 21, "policy_version": AUTHORING_ONLY_VERSION,
        "semantic_review_performed": False, "migration": asdict(result),
        "backup": str(backup), "backup_sha256": _file_sha256(backup),
        "retained_records_sha256_before": retained_before, "retained_records_sha256_after": retained_after,
        "preserved_counts_users_items_revisions_runs_receipts": after, "old_roots_modified": False}
    with activation.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply)), flush=True)


if __name__ == "__main__":
    main()
