"""Explicit operator CLI for verified Knowledge Core backup and restore drills."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
)
from code_assist_v1.knowledge_core.migrations.model import MigrationCatalog
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
)
from code_assist_v1.knowledge_core.services.backup_retention import (
    BackupRetentionService,
)
from code_assist_v1.knowledge_core.services.restore import RestoreDrillService
from code_assist_v1.tools.legacy_inventory_contract import canonical_json_bytes


class _ArgumentError(ValueError):
    pass


SCHEMA_CATALOG_NAMES = (
    "canonical", "preserved-publication-v20", "authoring-only-v21",
    "structured-authoring-v22", "full-label-authoring-v23",
    "native-publication-v31", "native-display-publication-v34",
    "pptx-role-publication-v37", "legacy-authoring-retention-v38",
    "pdf-publication-v41", "pdf-visual-publication-v45", "pdf-visual-writing-policy-v46", "pdf-evidence-units-v47",
    "native-semantic-targets-v48",
    "semantic-rename-reseed-v49",
    "protected-extraction-provenance-v50",
    "protected-pdf-visual-binding-v51",
    "temporal-root-rebinding-v52",
    "office-visual-derivative-v53",
    "office-hybrid-visual-archive-v54",
    "office-hybrid-preview-v55",
    "office-hybrid-wiki-v56",
    "office-hybrid-publication-candidate-v57",
    "office-hybrid-publication-body-v58",
    "office-personal-writer-part-v59",
    "company-policy-provenance-v60",
    "pdf-owned-evidence-prompt-v61",
)


def _schema_catalog(name: str) -> MigrationCatalog:
    """Select compiled code explicitly; never infer a catalog from DB/manifest."""
    if name not in SCHEMA_CATALOG_NAMES:
        raise _ArgumentError("unsupported_schema_catalog")
    if name == "canonical":
        return CANONICAL_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.preserved_publication_catalog import PRESERVED_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.authoring_only_catalog import AUTHORING_ONLY_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.structured_authoring_catalog import STRUCTURED_AUTHORING_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.full_label_authoring_catalog import FULL_LABEL_AUTHORING_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pptx_role_publication_catalog import PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_visual_derivative_catalog import OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_hybrid_visual_archive_catalog import OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_hybrid_preview_catalog import OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_hybrid_wiki_catalog import OFFICE_HYBRID_WIKI_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_candidate_catalog import OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.company_policy_provenance_catalog import COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
    return {
        "preserved-publication-v20": PRESERVED_PUBLICATION_MIGRATION_CATALOG,
        "authoring-only-v21": AUTHORING_ONLY_MIGRATION_CATALOG,
        "structured-authoring-v22": STRUCTURED_AUTHORING_MIGRATION_CATALOG,
        "full-label-authoring-v23": FULL_LABEL_AUTHORING_MIGRATION_CATALOG,
        "native-publication-v31": NATIVE_PUBLICATION_MIGRATION_CATALOG,
        "native-display-publication-v34": NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG,
        "pptx-role-publication-v37": PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG,
        "legacy-authoring-retention-v38": LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG,
        "pdf-publication-v41": PDF_PUBLICATION_MIGRATION_CATALOG,
        "pdf-visual-publication-v45": PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG,
        "pdf-visual-writing-policy-v46": PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG,
        "pdf-evidence-units-v47": PDF_EVIDENCE_UNITS_MIGRATION_CATALOG,
        "native-semantic-targets-v48": NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG,
        "semantic-rename-reseed-v49": SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG,
        "protected-extraction-provenance-v50": PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG,
        "protected-pdf-visual-binding-v51": PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
        "temporal-root-rebinding-v52": TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
        "office-visual-derivative-v53": OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG,
        "office-hybrid-visual-archive-v54": OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG,
        "office-hybrid-preview-v55": OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG,
        "office-hybrid-wiki-v56": OFFICE_HYBRID_WIKI_MIGRATION_CATALOG,
        "office-hybrid-publication-candidate-v57": OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG,
        "office-hybrid-publication-body-v58": OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
        "office-personal-writer-part-v59": OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
        "company-policy-provenance-v60": COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
        "pdf-owned-evidence-prompt-v61": PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
    }[name]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentError("invalid_arguments")


def _absolute(value: str) -> Path:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise _ArgumentError("absolute_path_required")
    return Path(os.path.normpath(value))


def _layout(namespace: argparse.Namespace, *, prefix: str = "") -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=_absolute(getattr(namespace, prefix + "data_root")),
        database=_absolute(getattr(namespace, prefix + "database")),
        blob_root=_absolute(getattr(namespace, prefix + "blob_root")),
        projection_root=_absolute(getattr(namespace, prefix + "projection_root")),
    )


def _factory(
    layout: MigrationStorageLayout, *, schema_catalog: str = "canonical"
) -> ConnectionFactory:
    catalog = _schema_catalog(schema_catalog)
    if os.environ.get("CODE_ASSIST_TEST_MODE") == "1":
        return ConnectionFactory._for_isolated_tests(
            layout.database, catalog
        )
    return ConnectionFactory(layout.database, catalog)


def _add_source_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--schema-catalog", choices=SCHEMA_CATALOG_NAMES, default="canonical",
        help="Exact already-deployed schema contract; does not migrate or activate features.",
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--blob-root", required=True)
    parser.add_argument("--projection-root", required=True)
    parser.add_argument("--backup-root", required=True)


def _parser() -> _Parser:
    parser = _Parser(prog="knowledge-backup")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    _add_source_paths(create)
    create.add_argument("--backup-run-id", required=True)
    create.add_argument("--app-version", required=True)
    create.add_argument("--build-revision", required=True)
    create.add_argument("--configuration-version", required=True)
    create.add_argument("--lease-hmac-key-set-version", required=True)

    restore = commands.add_parser("restore-drill")
    _add_source_paths(restore)
    restore.add_argument("--backup-run-id", required=True)
    restore.add_argument("--target-data-root", required=True)
    restore.add_argument("--target-database", required=True)
    restore.add_argument("--target-blob-root", required=True)
    restore.add_argument("--target-projection-root", required=True)
    restore.add_argument("--max-rpo-minutes", type=int, default=60)
    restore.add_argument("--max-rto-minutes", type=int, default=240)

    prune = commands.add_parser("prune")
    _add_source_paths(prune)
    recover = commands.add_parser("recover-backup")
    recover.add_argument("--schema-catalog", choices=SCHEMA_CATALOG_NAMES, required=True)
    recover.add_argument("--backup-root", required=True)
    recover.add_argument("--backup-run-id", required=True)
    recover.add_argument("--expected-manifest-sha256", required=True,
        help="Trusted hash saved from the original backup result, outside this archive.")
    recover.add_argument("--incident-at-ms", type=int, required=True)
    recover.add_argument("--target-data-root", required=True)
    recover.add_argument("--target-database", required=True)
    recover.add_argument("--target-blob-root", required=True)
    recover.add_argument("--target-projection-root", required=True)
    recover.add_argument("--max-rpo-minutes", type=int, default=60)
    recover.add_argument("--max-rto-minutes", type=int, default=240)
    return parser


def _write(value: object) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pragma: no cover - StringIO doubles
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
        return
    buffer.write(payload)
    buffer.flush()


def _execute(namespace: argparse.Namespace) -> dict[str, object]:
    if namespace.command == "recover-backup":
        result = RestoreDrillService.from_backup(
            backup_root=_absolute(namespace.backup_root),
            migration_catalog=_schema_catalog(namespace.schema_catalog),
            expected_manifest_sha256=namespace.expected_manifest_sha256,
        ).restore(
            backup_run_id=namespace.backup_run_id,
            target_layout=_layout(namespace, prefix="target_"),
            assumed_incident_at_ms=namespace.incident_at_ms,
            max_rpo_minutes=namespace.max_rpo_minutes,
            max_rto_minutes=namespace.max_rto_minutes,
        )
        return {"operation": namespace.command, "result": asdict(result), "status": "pass"}
    source = _layout(namespace)
    factory = _factory(source, schema_catalog=namespace.schema_catalog)
    # Reject mismatches before creating a backup hold, staging tree or target.
    probe = factory.open(read_only=True)
    try:
        pass
    finally:
        probe.close()
    backup_root = _absolute(namespace.backup_root)
    if namespace.command == "create":
        result = BackupService(
            factory,
            source,
            backup_root=backup_root,
            configuration=BackupServiceConfiguration(
                app_version=namespace.app_version,
                build_revision=namespace.build_revision,
                configuration_version=namespace.configuration_version,
                lease_hmac_key_set_version=namespace.lease_hmac_key_set_version,
            ),
        ).create(backup_run_id=namespace.backup_run_id)
    elif namespace.command == "restore-drill":
        result = RestoreDrillService(
            factory,
            source,
            backup_root=backup_root,
        ).restore(
            backup_run_id=namespace.backup_run_id,
            target_layout=_layout(namespace, prefix="target_"),
            assumed_incident_at_ms=int(time.time() * 1000),
            max_rpo_minutes=namespace.max_rpo_minutes,
            max_rto_minutes=namespace.max_rto_minutes,
        )
    elif namespace.command == "prune":
        result = BackupRetentionService(
            factory,
            source,
            backup_root=backup_root,
        ).prune()
    else:  # pragma: no cover - argparse closes the enum
        raise _ArgumentError("unknown_command")
    return {
        "operation": namespace.command,
        "result": asdict(result),
        "status": "pass",
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        namespace = _parser().parse_args(argv)
        _write(_execute(namespace))
        return 0
    except _ArgumentError:
        _write(
            {
                "error": {
                    "code": "CLI_INVALID_ARGUMENT",
                    "message": "The backup operation arguments are invalid.",
                },
                "status": "error",
            }
        )
        return 2
    except Exception:
        _write(
            {
                "error": {
                    "code": "BACKUP_OPERATION_FAILED",
                    "message": "The backup operation did not complete.",
                },
                "status": "error",
            }
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
