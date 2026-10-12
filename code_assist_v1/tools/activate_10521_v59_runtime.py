"""Issue/check the v59 receipt after verified offline trigger migration."""

from __future__ import annotations

import argparse
import json

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import verify_canonical_integrity
from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import (
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG as CATALOG,
)
from code_assist_v1.knowledge_core.wiki_authoring import _json
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V58_ACTIVATION_NAME, V59_ACTIVATION_NAME,
    _v59_receipt, catalog_for, is_clean_root, selection_for,
)
from code_assist_v1.tools.migrate_10521_v51 import _active_worker_pids, _listener_active


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--model-id", default="codex-local")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root):
        raise ValueError("The 10521 root is not the registered isolated target.")
    prior = selection_for(root)
    if (prior is None or prior.model_id != args.model_id
            or not (root / ".runtime" / V58_ACTIVATION_NAME).is_file()):
        raise ValueError("The exact v58 predecessor model is unavailable or changed.")
    connection = ConnectionFactory(root / "core/knowledge_core.db", CATALOG).open(read_only=True)
    try:
        verify_canonical_integrity(connection, CATALOG)
    finally:
        connection.close()
    target = root / ".runtime" / V59_ACTIVATION_NAME
    if args.apply:
        if _listener_active() or _active_worker_pids(root):
            raise ValueError("Stop the 10521 web service and child workers before v59 activation.")
        if target.exists():
            raise ValueError("The v59 activation already exists; use --check.")
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(_json(_v59_receipt(root, prior)) + "\n")
    selected = selection_for(root)
    if (selected is None or selected.model_id != args.model_id
            or catalog_for(root) != CATALOG):
        raise ValueError("The activated v59 model or catalog differs.")
    print(json.dumps({"status": "verified", "data_root": str(root),
        "schema_version": CATALOG.latest_version,
        "catalog_checksum_sha256": CATALOG.checksum_sha256,
        "model_id": selected.model_id, "provider": selected.provider,
        "activation_sha256": selected.activation_sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
