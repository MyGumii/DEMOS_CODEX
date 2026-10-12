"""Issue/check one clean 10521 v49 receipt after schema and model preflight.

This never migrates, seeds accounts, starts workers or touches another port.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import verify_canonical_integrity
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V3, prompt_for_policy
from code_assist_v1.knowledge_core.wiki_authoring import _hash, _json
from code_assist_v1.llm_images import IMAGE_INPUT_CAPABILITY
from code_assist_v1.tools.clean_10521_runtime import (
    ACTIVATION_NAME, CATALOG, DATA_ROOT, KIND, _pairs, is_clean_root, selection_for,
)
from code_assist_v1.tools.pdf_visual_publication_runtime import (
    PdfVisualRuntimeSelection, _model_identity,
)
from code_assist_v1.tools.recovered_preview_binding import _read


def _schema_ready(root: Path) -> None:
    factory = ConnectionFactory(root / "core" / "knowledge_core.db", CATALOG)
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity(connection, CATALOG)
    finally:
        connection.close()


def _receipt(root: Path, model_id: str) -> dict[str, object]:
    raw = _read(root / ".runtime" / "api_config.json", 1_048_576)
    registry = json.loads(raw, object_pairs_hook=_pairs)
    if type(registry) is not dict or type(registry.get("models")) is not dict:
        raise ValueError("The explicit 10521 model registry is invalid.")
    identity = _model_identity(model_id, registry["models"].get(model_id))
    python = Path(sys.executable).resolve(strict=True)
    selection = PdfVisualRuntimeSelection(
        root=root,
        python_executable=str(python),
        **identity,
        registry_sha256=sha256(raw).hexdigest(),
        activation_sha256="0" * 64,
        policy_version=VERSION_V3,
    )
    from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import V049_EXPECTED_SCHEMA_SHA256
    return dict(
        kind=KIND,
        port=10521,
        data_root=str(root),
        schema_version=49,
        schema_sha256=V049_EXPECTED_SCHEMA_SHA256,
        catalog_checksum_sha256=CATALOG.checksum_sha256,
        pdf_writing_policy_version=VERSION_V3,
        pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V3)),
        pdf_upload_policy_sha256=_hash(selection.upload_policy().encoded()),
        python_executable=str(python),
        registry_sha256=selection.registry_sha256,
        image_input_capability=IMAGE_INPUT_CAPABILITY,
        semantic_review_performed=False,
        **identity,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--model-id", default="codex-local")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root):
        raise ValueError("The 10521 data root is not the registered isolated target.")
    _schema_ready(root)
    target = root / ".runtime" / ACTIVATION_NAME
    if args.apply:
        if target.exists():
            raise ValueError("The 10521 activation receipt already exists; use --check.")
        value = _receipt(root, args.model_id)
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(_json(value) + "\n")
    selected = selection_for(root)
    if selected is None or selected.model_id != args.model_id:
        raise ValueError("The activated 10521 model differs from the requested model.")
    print(json.dumps({
        "status": "verified",
        "data_root": str(root),
        "schema_version": CATALOG.latest_version,
        "catalog_checksum_sha256": CATALOG.checksum_sha256,
        "model_id": selected.model_id,
        "provider": selected.provider,
        "activation_sha256": selected.activation_sha256,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
