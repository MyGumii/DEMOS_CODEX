"""Issue/check the v50 provenance receipt after an offline 10521 migration.

This never migrates the database, replaces the v49 receipt, or restarts a
server.  The exact v49 marker is retained as its predecessor evidence.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import verify_canonical_integrity
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import (
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG as CATALOG,
    V050_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.wiki_authoring import _json
from code_assist_v1.tools.activate_10521_runtime import _receipt as v49_receipt
from code_assist_v1.tools.clean_10521_runtime import (
    ACTIVATION_NAME as V49_ACTIVATION_NAME, DATA_ROOT, V50_ACTIVATION_NAME,
    V50_KIND, _v49_selection_for, catalog_for, is_clean_root, selection_for,
)
from code_assist_v1.tools.recovered_preview_binding import _read


def _receipt(root, model_id):
    previous = _v49_selection_for(root)
    if previous is None or previous.model_id != model_id:
        raise ValueError("The v49 predecessor model is unavailable or changed.")
    predecessor = _read(root / ".runtime" / V49_ACTIVATION_NAME)
    value = v49_receipt(root, model_id)
    return {
        **value,
        "kind": V50_KIND,
        "schema_version": 50,
        "schema_sha256": V050_EXPECTED_SCHEMA_SHA256,
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
    target = root / ".runtime" / V50_ACTIVATION_NAME
    if args.apply:
        if target.exists():
            raise ValueError("The v50 activation receipt already exists; use --check.")
        value = _receipt(root, args.model_id)
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(_json(value) + "\n")
    selected = selection_for(root)
    if (selected is None or selected.model_id != args.model_id
            or catalog_for(root) != CATALOG):
        raise ValueError("The activated v50 model or catalog differs.")
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
