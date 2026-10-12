"""Issue/check the v52 receipt after an offline isolated 10521 migration.

Predecessor receipts remain unchanged. This tool does not migrate or restart
the service, and the new catalog is never selected without this exact marker.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import verify_canonical_integrity
from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import (
    TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG as CATALOG,
    V052_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.wiki_authoring import _json
from code_assist_v1.tools.activate_10521_v51_runtime import _receipt as v51_receipt
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V51_ACTIVATION_NAME, V52_ACTIVATION_NAME, V52_KIND,
    _v51_selection_for, catalog_for, is_clean_root, selection_for,
)
from code_assist_v1.tools.recovered_preview_binding import _read
from code_assist_v1.tools.migrate_10521_v51 import _active_worker_pids, _listener_active


def _receipt(root, model_id):
    previous = _v51_selection_for(root)
    if previous is None or previous.model_id != model_id:
        raise ValueError("The exact v51 predecessor model is unavailable or changed.")
    predecessor = _read(root / ".runtime" / V51_ACTIVATION_NAME)
    return {
        **v51_receipt(root, model_id),
        "kind": V52_KIND,
        "schema_version": 52,
        "schema_sha256": V052_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--model-id", default="codex-local")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root):
        raise ValueError("The 10521 data root is not the registered isolated target.")
    factory = ConnectionFactory(root / "core" / "knowledge_core.db", CATALOG)
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity(connection, CATALOG)
    finally:
        connection.close()
    target = root / ".runtime" / V52_ACTIVATION_NAME
    if args.apply:
        if _listener_active() or _active_worker_pids(root):
            raise ValueError("Stop the 10521 web service and all child workers before v52 activation.")
        if target.exists():
            raise ValueError("The v52 activation receipt already exists; use --check.")
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(_json(_receipt(root, args.model_id)) + "\n")
    selected = selection_for(root)
    if (selected is None or selected.model_id != args.model_id
            or catalog_for(root) != CATALOG):
        raise ValueError("The activated v52 model or catalog differs.")
    print(json.dumps({
        "status": "verified", "data_root": str(root),
        "schema_version": CATALOG.latest_version,
        "catalog_checksum_sha256": CATALOG.checksum_sha256,
        "model_id": selected.model_id, "provider": selected.provider,
        "activation_sha256": selected.activation_sha256,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
