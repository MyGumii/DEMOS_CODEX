"""One offline deployment migration, restricted to the authorized 10421 clone."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sqlite3
from uuid import uuid4

def main():
    from code_assist_v1.tools.preservation_preview_runtime import REGISTERED_ROOT
    from code_assist_v1.tools.full_second_brain_preview import _configure_environment, _load_marker
    root = REGISTERED_ROOT.resolve(strict=True)
    marker = _load_marker(root)
    receipt = json.loads((root / ".runtime/clone-receipt.json").read_text(encoding="utf-8"))
    if marker.get("clone_port") != 10421 or Path(receipt["target_root"]).resolve(strict=True) != root:
        raise RuntimeError("Not the registered isolated clone")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 10421)) == 0:
            raise RuntimeError("Stop only the 10421 server and its workers before offline migration")
    _configure_environment(root, port=10421, codex=True)
    database = root / "core/knowledge_core.db"
    backup = root / ".runtime" / ("before-preservation-v20-" + str(uuid4()) + ".db")
    counts_sql = "SELECT (SELECT count(*) FROM app_user),(SELECT count(*) FROM knowledge_item),(SELECT count(*) FROM knowledge_revision)"
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        before = source.execute(counts_sql).fetchone()
        with backup.open("xb"):
            pass
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
            assert target.execute("PRAGMA quick_check").fetchone() == ("ok",)
        finally:
            target.close()
    finally:
        source.close()
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import SingleMigrator, MigrationStorageLayout
    from code_assist_v1.knowledge_core.migrations.preserved_publication_catalog import PRESERVED_PUBLICATION_MIGRATION_CATALOG
    factory = ConnectionFactory._for_isolated_tests(database, PRESERVED_PUBLICATION_MIGRATION_CATALOG)
    result = SingleMigrator(factory, app_version="isolated-10421-preservation-v20",
        storage_layout=MigrationStorageLayout(data_root=root, database=database,
            blob_root=root / "blobs", projection_root=root / "projections")).run()
    connection = factory.open(read_only=True)
    try:
        after = tuple(connection.execute(counts_sql).fetchone())
        assert before == after
    finally:
        connection.close()
    evidence = {"port":10421, "schema_version":20, "migration":asdict(result),
        "backup":str(backup), "backup_sha256":sha256(backup.read_bytes()).hexdigest(),
        "preserved_counts_users_items_revisions":after, "old_roots_modified":False}
    with (root / ".runtime/preservation-v20-enabled.json").open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
    print(json.dumps(evidence), flush=True)

if __name__ == "__main__":
    main()
