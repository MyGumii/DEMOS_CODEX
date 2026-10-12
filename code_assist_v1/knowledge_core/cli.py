"""Offline command line entry points for knowledge-core migrations.

Importing this module remains inert.  P2.2 planning/confirmation transform
artifacts only; the explicit P2.3/P2.4/P2.5 ``apply``, P2.6 ``verify``, and
P2.8 ``rehearse`` subcommands are the only paths that open frozen sources or
migrated canonical databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence


_MAX_INVENTORY_BYTES = 256 * 1024 * 1024
_MAX_ORGANIZATION_MAPPING_BYTES = 8 * 1024 * 1024
_MAX_DRAFT_BYTES = 256 * 1024 * 1024
_SHA256_LENGTH = 64

_ERROR_MESSAGES = {
    "CLI_INVALID_ARGUMENT": "The command arguments are invalid.",
    "INPUT_READ_FAILED": "A bound input artifact could not be read safely.",
    "INPUT_TOO_LARGE": "An input artifact exceeds the supported size limit.",
    "INVENTORY_INVALID": "The inventory artifact is invalid or does not match its approvals.",
    "ORGANIZATION_MAPPING_INVALID": "The organization mapping is invalid or does not match its approval.",
    "PLAN_INVALID": "The migration plan artifact is invalid.",
    "PLAN_DRIFT": "The approved migration plan differs from the existing artifact.",
    "REVIEW_INVALID": "The plan review confirmation is invalid or stale.",
    "OUTPUT_PARENT_MISSING": "The output parent directory does not exist.",
    "OUTPUT_PARENT_INELIGIBLE": "The output parent is not an eligible local directory.",
    "OUTPUT_INSIDE_SOURCE": "The output directory overlaps a declared source root.",
    "OUTPUT_WRITE_FAILED": "The output artifact could not be written safely.",
    "SOURCE_CHANGED": "A reviewed frozen source no longer matches the approved inventory.",
    "APPLY_LAYOUT_INVALID": "Canonical apply storage is absent, unsafe, or overlaps a source root.",
    "APPLY_STATE_CONFLICT": "Canonical state conflicts with the reviewed migration plan.",
    "APPLY_FAILED": "The bounded migration apply operation did not complete.",
    "SCHEMA_BOOTSTRAP_TARGET_EXISTS": "The fresh rehearsal database target already exists.",
    "SCHEMA_BOOTSTRAP_FAILED": "The empty rehearsal schema bootstrap did not complete.",
    "VERIFY_LAYOUT_INVALID": "Canonical verification storage is absent, unsafe, or overlaps a source root.",
    "VERIFY_STATE_CONFLICT": "Canonical state does not exactly match the reviewed migration plan.",
    "VERIFY_FAILED": "The strict migration verification did not complete.",
    "REHEARSAL_TARGET_INVALID": "Both rehearsal targets must be distinct empty canonical databases.",
    "REHEARSAL_MISMATCH": "The replay or fresh-target rehearsal result was not reproducible.",
    "REHEARSAL_FAILED": "The frozen-source rehearsal did not complete.",
    "INTERNAL_ERROR": "The planner could not complete the request.",
}


class _CliArgumentError(ValueError):
    pass


class _CliOutputBoundaryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised by main
        del message
        raise _CliArgumentError("invalid_cli_arguments")


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _write_error(code: str) -> None:
    from code_assist_v1.tools.legacy_inventory_contract import canonical_json_bytes

    stable_code = code if code in _ERROR_MESSAGES else "INTERNAL_ERROR"
    payload = {
        "error": {
            "code": stable_code,
            "message": _ERROR_MESSAGES[stable_code],
        }
    }
    encoded = canonical_json_bytes(payload) + b"\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(encoded)
        buffer.flush()
    else:  # pragma: no cover - StringIO test doubles
        sys.stdout.write(encoded.decode("utf-8"))
        sys.stdout.flush()


def _input_path(value: str) -> Path:
    from code_assist_v1.tools.legacy_inventory import _is_unc

    if not isinstance(value, str) or not os.path.isabs(value) or _is_unc(value):
        raise _CliArgumentError("input_path")
    path = Path(os.path.normpath(value))
    if not path.name:
        raise _CliArgumentError("input_path")
    return path


def _read_bound_file(value: str, *, maximum_bytes: int) -> bytes:
    """Read one local artifact while pinning and revalidating its parent/file."""

    from code_assist_v1.tools.legacy_inventory import (
        _contains,
        _is_reparse,
        _observe_output_parent,
        _pinned_directory,
    )

    path = _input_path(value)
    try:
        parent_resolved, parent_identity = _observe_output_parent(path.parent)
        with _pinned_directory(path.parent, writable=False):
            observed_parent, observed_identity = _observe_output_parent(path.parent)
            if (
                observed_parent != parent_resolved
                or observed_identity != parent_identity
            ):
                raise OSError("input_parent_identity_changed")
            before = path.stat(follow_symlinks=False)
            if _is_reparse(path, before) or not path.is_file():
                raise OSError("unsafe_input_file")
            resolved_before = path.resolve(strict=True)
            if not _contains(parent_resolved, resolved_before):
                raise OSError("input_escapes_parent")
            if before.st_size > maximum_bytes:
                raise OverflowError("input_too_large")
            flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
            flags |= int(getattr(os, "O_NOFOLLOW", 0))
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise OSError("input_identity_changed")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise OverflowError("input_too_large")
            finally:
                os.close(descriptor)
            after = path.stat(follow_symlinks=False)
            resolved_after = path.resolve(strict=True)
            final_parent, final_identity = _observe_output_parent(path.parent)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or resolved_after != resolved_before
                or final_parent != parent_resolved
                or final_identity != parent_identity
                or not _contains(parent_resolved, resolved_after)
            ):
                raise OSError("input_changed")
            return b"".join(chunks)
    except OverflowError:
        raise
    except (OSError, ValueError) as exc:
        raise OSError("bound_input_read_failed") from exc


def _source_roots(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    snapshot: Any
    if artifact.get("stage") == "reviewed_migration_plan":
        draft = artifact.get("draft")
        snapshot = draft.get("source_snapshot") if isinstance(draft, Mapping) else None
    else:
        snapshot = artifact.get("source_snapshot")
    roots = snapshot.get("roots") if isinstance(snapshot, Mapping) else None
    if not isinstance(roots, list):
        return ()
    values: list[str] = []
    for row in roots:
        if isinstance(row, Mapping) and isinstance(row.get("resolved_path"), str):
            values.append(row["resolved_path"])
    return tuple(values)


def _declared_path_contains(parent: str, child: str) -> bool:
    """Compare two contract-validated absolute paths without probing either."""

    try:
        parent_value = os.path.normcase(os.path.normpath(parent))
        child_value = os.path.normcase(os.path.normpath(child))
        return os.path.commonpath((parent_value, child_value)) == parent_value
    except (TypeError, ValueError):
        return False


def _declared_output_overlap(output_parent: Path, roots: Sequence[str]) -> bool:
    parent = os.path.abspath(os.path.normpath(str(output_parent)))
    return any(
        _declared_path_contains(root, parent)
        or _declared_path_contains(parent, root)
        for root in roots
    )


def _read_winner(
    output: str, expected: bytes, *, attempts: int = 9
) -> str | None:
    """Classify a concurrently committed winner after its handle is visible."""

    for attempt in range(attempts):
        try:
            current = _read_bound_file(
                output, maximum_bytes=max(len(expected), 1) + 1
            )
        except (OSError, OverflowError):
            if attempt + 1 == attempts:
                return "OUTPUT_WRITE_FAILED"
            time.sleep(min(0.001 * (2**attempt), 0.05))
            continue
        return None if current == expected else "PLAN_DRIFT"
    return "OUTPUT_WRITE_FAILED"


def _write_artifact(
    output: str,
    artifact: Mapping[str, Any],
    *,
    protected_inputs: Sequence[str],
    protected_roots: Sequence[str] = (),
) -> str | None:
    """Atomically create a plan, accepting only an identical existing winner."""

    from code_assist_v1.knowledge_core.legacy.plan_contract import canonical_json_bytes
    from code_assist_v1.tools.legacy_inventory import (
        _prepare_output_guard,
        _write_file,
    )

    try:
        path = _input_path(output)
    except _CliArgumentError:
        return "CLI_INVALID_ARGUMENT"
    try:
        normalized_output = os.path.normcase(str(path.resolve(strict=False)))
        for input_value in protected_inputs:
            input_path = _input_path(input_value)
            if normalized_output == os.path.normcase(str(input_path.resolve(strict=True))):
                return "CLI_INVALID_ARGUMENT"
    except (OSError, ValueError, _CliArgumentError):
        return "CLI_INVALID_ARGUMENT"

    expected = canonical_json_bytes(artifact) + b"\n"
    source_roots = (*_source_roots(artifact), *protected_roots)
    if _declared_output_overlap(path.parent, source_roots):
        return "OUTPUT_INSIDE_SOURCE"
    exists = path.exists()
    # The source roots are frozen declarations, not live inputs to P2.2.  The
    # lexical check above enforces their boundary without probing them; only
    # the writable output parent is observed by the shared output primitive.
    # Explicit protected roots are live apply/verify/rehearsal boundaries.  Bind
    # their observed targets into the output guard so a late output-parent
    # junction/rename cannot redirect the artifact into canonical or source
    # storage.  P2.2 plan/confirmation keeps this empty and remains offline.
    guard, error = _prepare_output_guard(output, exists, protected_roots)
    if error == "OUTPUT_EXISTS":
        # A competing create won after our initial observation.  Preserve the
        # same idempotent rule used after the atomic write attempt: an exact
        # canonical winner succeeds, every other winner is plan drift.
        result = _read_winner(output, expected)
        return "PLAN_DRIFT" if result == "OUTPUT_WRITE_FAILED" else result
    if error is not None:
        return error
    if guard is None:
        return "OUTPUT_WRITE_FAILED"
    if exists:
        result = _read_winner(output, expected)
        return "PLAN_DRIFT" if result == "OUTPUT_WRITE_FAILED" else result
    try:
        _write_file(path, artifact, False, guard)
        return None
    except Exception:
        # A competing create may have won between preflight and handle rename.
        # Only a byte-identical canonical winner is an idempotent success.
        return _read_winner(output, expected)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="knowledge-core", add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate", add_help=True)
    migration_commands = migrate.add_subparsers(dest="migration_command", required=True)

    plan = migration_commands.add_parser("plan", add_help=True)
    plan.add_argument("--inventory", required=True)
    plan.add_argument("--inventory-manifest-sha256", required=True)
    plan.add_argument("--snapshot-attestation-sha256", required=True)
    plan.add_argument("--root-binding-sha256", required=True)
    plan.add_argument("--org-map", required=True)
    plan.add_argument("--org-map-sha256", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--allow-data-quality-errors", action="store_true")

    confirm = migration_commands.add_parser("confirm-plan", add_help=True)
    confirm.add_argument("--draft", required=True)
    confirm.add_argument("--draft-sha256", required=True)
    confirm.add_argument("--review-attestation-sha256", required=True)
    confirm.add_argument("--reviewed-at-utc", required=True)
    confirm.add_argument("--reviewer-id", required=True)
    confirm.add_argument("--output", required=True)

    apply = migration_commands.add_parser("apply", add_help=True)
    apply.add_argument("--plan", required=True)
    apply.add_argument("--plan-sha256", required=True)
    apply.add_argument("--data-root", required=True)
    apply.add_argument("--database", required=True)
    apply.add_argument("--blob-root", required=True)
    apply.add_argument("--projection-root", required=True)
    apply.add_argument("--batch-size", type=int, default=100)
    apply.add_argument("--resume", action="store_true", required=True)

    bootstrap = migration_commands.add_parser("bootstrap-rehearsal", add_help=True)
    bootstrap.add_argument("--data-root", required=True)
    bootstrap.add_argument("--database", required=True)
    bootstrap.add_argument("--blob-root", required=True)
    bootstrap.add_argument("--projection-root", required=True)

    verify = migration_commands.add_parser("verify", add_help=True)
    verify.add_argument("--plan", required=True)
    verify.add_argument("--plan-sha256", required=True)
    verify.add_argument("--data-root", required=True)
    verify.add_argument("--database", required=True)
    verify.add_argument("--blob-root", required=True)
    verify.add_argument("--projection-root", required=True)
    verify.add_argument("--strict", action="store_true", required=True)
    verify.add_argument("--report", required=True)

    rehearse = migration_commands.add_parser("rehearse", add_help=True)
    rehearse.add_argument("--plan", required=True)
    rehearse.add_argument("--plan-sha256", required=True)
    rehearse.add_argument("--primary-data-root", required=True)
    rehearse.add_argument("--primary-database", required=True)
    rehearse.add_argument("--primary-blob-root", required=True)
    rehearse.add_argument("--primary-projection-root", required=True)
    rehearse.add_argument("--fresh-data-root", required=True)
    rehearse.add_argument("--fresh-database", required=True)
    rehearse.add_argument("--fresh-blob-root", required=True)
    rehearse.add_argument("--fresh-projection-root", required=True)
    rehearse.add_argument("--batch-size", type=int, default=100)
    rehearse.add_argument("--report", required=True)
    return parser


def _plan(arguments: argparse.Namespace) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    from code_assist_v1.knowledge_core.legacy.planner import build_plan_draft

    inventory = _read_bound_file(
        arguments.inventory, maximum_bytes=_MAX_INVENTORY_BYTES
    )
    organization_mapping = _read_bound_file(
        arguments.org_map, maximum_bytes=_MAX_ORGANIZATION_MAPPING_BYTES
    )
    artifact = build_plan_draft(
        inventory,
        organization_mapping,
        expected_inventory_manifest_sha256=arguments.inventory_manifest_sha256,
        expected_snapshot_attestation_sha256=arguments.snapshot_attestation_sha256,
        expected_root_binding_sha256=arguments.root_binding_sha256,
        expected_organization_mapping_document_sha256=arguments.org_map_sha256,
        allow_data_quality_errors=arguments.allow_data_quality_errors,
    )
    return artifact, (arguments.inventory, arguments.org_map)


def _confirm(arguments: argparse.Namespace) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    from code_assist_v1.knowledge_core.legacy.planner import confirm_plan

    draft = _read_bound_file(arguments.draft, maximum_bytes=_MAX_DRAFT_BYTES)
    artifact = confirm_plan(
        draft,
        expected_draft_sha256=arguments.draft_sha256,
        review_attestation_sha256=arguments.review_attestation_sha256,
        reviewed_at_utc=arguments.reviewed_at_utc,
        reviewer_id=arguments.reviewer_id,
    )
    return artifact, (arguments.draft,)


def _known_planner_error(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _ERROR_MESSAGES:
        return code
    text = str(code or "")
    if text.startswith("INVENTORY"):
        return "INVENTORY_INVALID"
    if text.startswith("ORGANIZATION") or text.startswith("MAPPING"):
        return "ORGANIZATION_MAPPING_INVALID"
    if text.startswith("REVIEW"):
        return "REVIEW_INVALID"
    if text.startswith("PLAN"):
        return "PLAN_INVALID"
    return "INTERNAL_ERROR"


def _write_apply_success(result: object) -> None:
    from code_assist_v1.tools.legacy_inventory_contract import canonical_json_bytes

    identity = getattr(result, "identity", None)
    payload = {
        "apply": {
            "content_batch_count": len(getattr(result, "content_batches", ())),
            "imported_record_count": int(getattr(result, "imported_record_count")),
            "imported_relationship_count": int(
                getattr(result, "imported_relationship_count")
            ),
            "imported_user_count": int(getattr(identity, "imported_user_count")),
            "inserted_mapping_count": int(getattr(result, "inserted_mapping_count")),
            "plan_sha256": str(getattr(result, "plan_sha256")),
            "p24_batch_count": len(getattr(result, "p24_batches", ()))
            + len(getattr(result, "p24_history_batches", ())),
            "p25_batch_count": len(getattr(result, "p25_batches", ()))
            + len(getattr(result, "p25_notion_batches", ())),
            "status": "complete",
        }
    }
    encoded = canonical_json_bytes(payload) + b"\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(encoded)
        buffer.flush()
    else:  # pragma: no cover - StringIO test doubles
        sys.stdout.write(encoded.decode("utf-8"))
        sys.stdout.flush()


def _canonical_factory(database: str) -> object:
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import (
        CANONICAL_MIGRATION_CATALOG,
    )

    return ConnectionFactory(database, CANONICAL_MIGRATION_CATALOG)


def _apply(arguments: argparse.Namespace) -> object:
    from code_assist_v1.knowledge_core.legacy.migration_apply import (
        LegacyMigrationApplyService,
    )
    from code_assist_v1.knowledge_core.migrations import (
        MigrationStorageLayout,
    )

    reviewed_plan = _read_bound_file(
        arguments.plan, maximum_bytes=_MAX_DRAFT_BYTES
    )
    layout = MigrationStorageLayout(
        data_root=arguments.data_root,
        database=arguments.database,
        blob_root=arguments.blob_root,
        projection_root=arguments.projection_root,
    )
    factory = _canonical_factory(arguments.database)
    return LegacyMigrationApplyService(factory, layout).apply(
        reviewed_plan,
        expected_plan_sha256=arguments.plan_sha256,
        batch_size=arguments.batch_size,
    )


def _known_apply_error(exc: Exception) -> str:
    code = str(getattr(exc, "code", ""))
    if code == "LEGACY_SOURCE_BINDING_FAILED":
        return "SOURCE_CHANGED"
    if code in {
        "LEGACY_MIGRATION_APPLY_LAYOUT_INVALID",
        "KNOWLEDGE_CORE_DATABASE_PATH_INVALID",
        "KNOWLEDGE_CORE_SQLITE_RUNTIME_MISMATCH",
        "KNOWLEDGE_CORE_SQLITE_PRAGMA_MISMATCH",
        "KNOWLEDGE_CORE_SCHEMA_MISMATCH",
        "LEGACY_BLOB_STAGING_FAILED",
    }:
        return "APPLY_LAYOUT_INVALID"
    if code in {
        "LEGACY_CONTENT_STATE_CONFLICT",
        "LEGACY_IDENTITY_STATE_CONFLICT",
        "LEGACY_P24_STATE_CONFLICT",
        "LEGACY_P24_HISTORY_STATE_CONFLICT",
        "LEGACY_P24_MAPPING_CONFLICT",
        "LEGACY_P24_MAPPING_INTEGRITY_ERROR",
        "LEGACY_CONVERSATION_STATE_CONFLICT",
        "LEGACY_P25_STATE_CONFLICT",
        "LEGACY_P25_MAPPING_CONFLICT",
        "LEGACY_P25_MAPPING_INTEGRITY_ERROR",
        "MIGRATION_MAPPING_CONFLICT",
        "MIGRATION_TARGET_INTEGRITY_ERROR",
    }:
        return "APPLY_STATE_CONFLICT"
    if (
        code in {
            "LEGACY_MIGRATION_APPLY_PLAN_INVALID",
            "LEGACY_P24_PLAN_INVALID",
            "LEGACY_P24_HISTORY_PLAN_INVALID",
            "LEGACY_P25_PLAN_INVALID",
            "LEGACY_P25_NOTION_PLAN_INVALID",
            "LEGACY_P25_BATCH_LIMIT",
            "LEGACY_P25_NOTION_BATCH_LIMIT",
        }
        or "PREPARATION" in code
        or code.startswith("PLAN")
    ):
        return "PLAN_INVALID"
    if code.startswith("LEGACY_") or code.startswith("KNOWLEDGE_CORE_"):
        return "APPLY_FAILED"
    return "INTERNAL_ERROR"


def _bootstrap_rehearsal(arguments: argparse.Namespace) -> Mapping[str, Any]:
    from code_assist_v1.knowledge_core.legacy.rehearsal import (
        LegacyMigrationRehearsalTarget,
        verify_empty_rehearsal_target,
    )
    from code_assist_v1.knowledge_core.migrations import (
        MigrationStorageLayout,
        SingleMigrator,
    )
    from code_assist_v1.tools.legacy_inventory import (
        _close_windows_handle,
        _pinned_directory,
        _windows_final_path,
    )

    layout = MigrationStorageLayout(
        data_root=arguments.data_root,
        database=arguments.database,
        blob_root=arguments.blob_root,
        projection_root=arguments.projection_root,
    )
    factory = _canonical_factory(str(layout.database))
    with _pinned_directory(
        layout.database.parent,
        share_write=True,
    ) as parent_anchor:
        if layout.database.exists() or os.path.lexists(layout.database):
            raise FileExistsError("rehearsal_database_exists")
        try:
            database_handle = parent_anchor.create_relative_file(
                layout.database.name,
                share_write=True,
                delete_access=False,
            )
        except OSError as exc:
            if os.path.lexists(layout.database):
                raise FileExistsError("rehearsal_database_exists") from exc
            raise
        try:
            if os.path.normcase(str(_windows_final_path(database_handle))) != os.path.normcase(
                str(layout.database.resolve(strict=True))
            ):
                raise OSError("rehearsal_database_final_path_changed")
            result = SingleMigrator(
                factory,
                app_version="p2.8-rehearsal-bootstrap",
                storage_layout=layout,
                require_fresh_bootstrap=True,
            ).run()
        finally:
            _close_windows_handle(database_handle)
    verify_empty_rehearsal_target(LegacyMigrationRehearsalTarget(factory, layout))
    return {
        "bootstrap": {
            "applied_versions": list(result.applied_versions),
            "catalog_checksum_sha256": result.catalog_checksum_sha256,
            "schema_version": result.schema_version,
            "status": "complete",
        }
    }


def _known_bootstrap_error(exc: Exception) -> str:
    if isinstance(exc, FileExistsError):
        return "SCHEMA_BOOTSTRAP_TARGET_EXISTS"
    code = str(getattr(exc, "code", ""))
    if code == "KNOWLEDGE_CORE_FRESH_BOOTSTRAP_REQUIRED":
        return "SCHEMA_BOOTSTRAP_TARGET_EXISTS"
    if code in {
        "KNOWLEDGE_CORE_DATABASE_PATH_INVALID",
        "KNOWLEDGE_CORE_SQLITE_RUNTIME_MISMATCH",
        "KNOWLEDGE_CORE_SQLITE_PRAGMA_MISMATCH",
        "KNOWLEDGE_CORE_SCHEMA_MISMATCH",
        "KNOWLEDGE_CORE_MIGRATION_FAILED",
        "LEGACY_MIGRATION_REHEARSAL_TARGET_INVALID",
    }:
        return "SCHEMA_BOOTSTRAP_FAILED"
    if isinstance(exc, OSError):
        return "SCHEMA_BOOTSTRAP_FAILED"
    return "INTERNAL_ERROR"


def _verify(arguments: argparse.Namespace) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    from code_assist_v1.knowledge_core.legacy.migration_verify import (
        LegacyMigrationVerifier,
    )
    from code_assist_v1.knowledge_core.migrations import (
        MigrationStorageLayout,
    )

    reviewed_plan = _read_bound_file(
        arguments.plan, maximum_bytes=_MAX_DRAFT_BYTES
    )
    layout = MigrationStorageLayout(
        data_root=arguments.data_root,
        database=arguments.database,
        blob_root=arguments.blob_root,
        projection_root=arguments.projection_root,
    )
    factory = _canonical_factory(arguments.database)
    report = LegacyMigrationVerifier(factory, layout).verify(
        reviewed_plan,
        expected_plan_sha256=arguments.plan_sha256,
    )
    try:
        reviewed = json.loads(reviewed_plan)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):  # pragma: no cover
        raise _CliArgumentError("reviewed_plan") from None
    protected_roots = (
        *_source_roots(reviewed),
        str(Path(arguments.data_root)),
        str(Path(arguments.database).parent),
        str(Path(arguments.blob_root)),
        str(Path(arguments.projection_root)),
    )
    return report, protected_roots


def _known_verify_error(exc: Exception) -> str:
    code = str(getattr(exc, "code", ""))
    if code == "LEGACY_MIGRATION_VERIFICATION_PLAN_INVALID" or code.startswith(
        "PLAN"
    ):
        return "PLAN_INVALID"
    if code in {
        "KNOWLEDGE_CORE_DATABASE_PATH_INVALID",
        "KNOWLEDGE_CORE_SQLITE_RUNTIME_MISMATCH",
        "KNOWLEDGE_CORE_SQLITE_PRAGMA_MISMATCH",
        "KNOWLEDGE_CORE_SCHEMA_MISMATCH",
        "MIGRATION_STORAGE_PREFLIGHT_FAILED",
    }:
        return "VERIFY_LAYOUT_INVALID"
    if code in {
        "LEGACY_MIGRATION_VERIFICATION_INTEGRITY_ERROR",
        "LEGACY_BLOB_STAGING_FAILED",
        "KNOWLEDGE_CORE_MIGRATION_FAILED",
    }:
        return "VERIFY_STATE_CONFLICT"
    if code.startswith("LEGACY_MIGRATION_VERIFICATION") or code.startswith(
        "KNOWLEDGE_CORE_"
    ):
        return "VERIFY_FAILED"
    return "INTERNAL_ERROR"


def _rehearsal_target(arguments: argparse.Namespace, prefix: str) -> object:
    from code_assist_v1.knowledge_core.legacy.rehearsal import (
        LegacyMigrationRehearsalTarget,
    )
    from code_assist_v1.knowledge_core.migrations import (
        MigrationStorageLayout,
    )

    data_root = getattr(arguments, f"{prefix}_data_root")
    database = getattr(arguments, f"{prefix}_database")
    blob_root = getattr(arguments, f"{prefix}_blob_root")
    projection_root = getattr(arguments, f"{prefix}_projection_root")
    layout = MigrationStorageLayout(
        data_root=data_root,
        database=database,
        blob_root=blob_root,
        projection_root=projection_root,
    )
    factory = _canonical_factory(database)
    return LegacyMigrationRehearsalTarget(factory, layout)


def _rehearse(
    arguments: argparse.Namespace,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    from code_assist_v1.knowledge_core.legacy.importer import _reviewed_plan
    from code_assist_v1.knowledge_core.legacy.rehearsal import (
        LegacyMigrationRehearsal,
        LegacyMigrationRehearsalMismatchError,
        rehearsal_contract_errors,
        rehearsal_plan_binding,
        rehearsal_target_binding,
    )
    from code_assist_v1.knowledge_core.legacy.plan_contract import (
        canonical_json_bytes,
    )

    reviewed_plan = _read_bound_file(
        arguments.plan, maximum_bytes=_MAX_DRAFT_BYTES
    )
    reviewed = _reviewed_plan(
        reviewed_plan,
        expected_plan_sha256=arguments.plan_sha256,
    )
    protected_roots = (
        *_source_roots(reviewed),
        str(Path(arguments.primary_data_root)),
        str(Path(arguments.primary_database).parent),
        str(Path(arguments.primary_blob_root)),
        str(Path(arguments.primary_projection_root)),
        str(Path(arguments.fresh_data_root)),
        str(Path(arguments.fresh_database).parent),
        str(Path(arguments.fresh_blob_root)),
        str(Path(arguments.fresh_projection_root)),
    )
    try:
        report_path = _input_path(arguments.report)
        plan_path = _input_path(arguments.plan)
        if os.path.normcase(str(report_path.resolve(strict=False))) == os.path.normcase(
            str(plan_path.resolve(strict=True))
        ):
            raise _CliArgumentError("report_path")
    except (OSError, ValueError) as exc:
        raise _CliArgumentError("report_path") from exc
    if _declared_output_overlap(report_path.parent, protected_roots):
        raise _CliOutputBoundaryError("OUTPUT_INSIDE_SOURCE")
    from code_assist_v1.tools.legacy_inventory import _prepare_output_guard

    _guard, output_error = _prepare_output_guard(
        arguments.report, report_path.exists(), protected_roots
    )
    if output_error not in {None, "OUTPUT_EXISTS"}:
        raise _CliOutputBoundaryError(output_error)

    primary = _rehearsal_target(arguments, "primary")
    fresh = _rehearsal_target(arguments, "fresh")
    runner = LegacyMigrationRehearsal(primary, fresh)
    if report_path.exists():
        existing_bytes = _read_bound_file(
            arguments.report,
            maximum_bytes=_MAX_DRAFT_BYTES,
        )
        try:
            existing = json.loads(existing_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise LegacyMigrationRehearsalMismatchError(
                "The existing rehearsal report is invalid."
            ) from None
        expected_targets = {
            "fresh": rehearsal_target_binding(fresh),
            "primary": rehearsal_target_binding(primary),
        }
        if (
            not isinstance(existing, Mapping)
            or existing_bytes != canonical_json_bytes(existing) + b"\n"
            or rehearsal_contract_errors(
                existing,
                reviewed_plan=reviewed_plan,
            )
            or existing.get("plan")
            != rehearsal_plan_binding(reviewed, reviewed_plan=reviewed_plan)
            or existing.get("execution")
            != {"batch_size": arguments.batch_size}
            or existing.get("targets") != expected_targets
        ):
            raise LegacyMigrationRehearsalMismatchError(
                "The existing rehearsal report differs from this exact run."
            )
        from code_assist_v1.knowledge_core.legacy.migration_verify import (
            LegacyMigrationVerifier,
        )

        expected_verification = existing.get("verification")
        primary_verification = LegacyMigrationVerifier(
            primary.factory, primary.layout
        ).verify(
            reviewed_plan,
            expected_plan_sha256=arguments.plan_sha256,
        )
        fresh_verification = LegacyMigrationVerifier(
            fresh.factory, fresh.layout
        ).verify(
            reviewed_plan,
            expected_plan_sha256=arguments.plan_sha256,
        )
        if (
            primary_verification != expected_verification
            or fresh_verification != expected_verification
        ):
            raise LegacyMigrationRehearsalMismatchError(
                "A rehearsal target changed after the archived verification."
            )
        return existing, protected_roots
    artifact = runner.run(
        reviewed_plan,
        expected_plan_sha256=arguments.plan_sha256,
        batch_size=arguments.batch_size,
    )
    return artifact, protected_roots


def _known_rehearsal_error(exc: Exception) -> str:
    code = str(getattr(exc, "code", ""))
    if code == "LEGACY_MIGRATION_REHEARSAL_PLAN_INVALID" or code.startswith(
        "PLAN"
    ):
        return "PLAN_INVALID"
    if code in {
        "LEGACY_MIGRATION_REHEARSAL_TARGET_INVALID",
        "KNOWLEDGE_CORE_DATABASE_PATH_INVALID",
        "KNOWLEDGE_CORE_SQLITE_RUNTIME_MISMATCH",
        "KNOWLEDGE_CORE_SQLITE_PRAGMA_MISMATCH",
        "KNOWLEDGE_CORE_SCHEMA_MISMATCH",
    }:
        return "REHEARSAL_TARGET_INVALID"
    if code == "LEGACY_MIGRATION_REHEARSAL_MISMATCH":
        return "REHEARSAL_MISMATCH"
    delegated = _known_apply_error(exc)
    if delegated != "INTERNAL_ERROR":
        return delegated
    delegated = _known_verify_error(exc)
    if delegated != "INTERNAL_ERROR":
        return delegated
    if code.startswith("LEGACY_MIGRATION_REHEARSAL"):
        return "REHEARSAL_FAILED"
    return "INTERNAL_ERROR"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except _CliArgumentError:
        _write_error("CLI_INVALID_ARGUMENT")
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)

    if arguments.migration_command == "plan":
        hash_values = [
            arguments.inventory_manifest_sha256,
            arguments.snapshot_attestation_sha256,
            arguments.root_binding_sha256,
            arguments.org_map_sha256,
        ]
    elif arguments.migration_command == "confirm-plan":
        hash_values = [arguments.draft_sha256, arguments.review_attestation_sha256]
    elif arguments.migration_command == "bootstrap-rehearsal":
        hash_values = []
    else:
        hash_values = [arguments.plan_sha256]
    if not all(_valid_sha256(value) for value in hash_values):
        _write_error("CLI_INVALID_ARGUMENT")
        return 2

    if arguments.migration_command == "bootstrap-rehearsal":
        try:
            result = _bootstrap_rehearsal(arguments)
        except Exception as exc:
            code = _known_bootstrap_error(exc)
            _write_error(code)
            return 3 if code.startswith("SCHEMA_BOOTSTRAP_") else 2
        from code_assist_v1.tools.legacy_inventory_contract import (
            canonical_json_bytes,
        )

        encoded = canonical_json_bytes(result) + b"\n"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(encoded)
            buffer.flush()
        else:  # pragma: no cover - StringIO test doubles
            sys.stdout.write(encoded.decode("utf-8"))
            sys.stdout.flush()
        return 0

    if arguments.migration_command == "apply":
        try:
            result = _apply(arguments)
        except OverflowError:
            _write_error("INPUT_TOO_LARGE")
            return 2
        except OSError:
            _write_error("INPUT_READ_FAILED")
            return 2
        except _CliArgumentError:
            _write_error("CLI_INVALID_ARGUMENT")
            return 2
        except Exception as exc:
            code = _known_apply_error(exc)
            _write_error(code)
            return 3 if code.startswith("APPLY_") else 2
        _write_apply_success(result)
        return 0

    if arguments.migration_command == "verify":
        try:
            artifact, protected_roots = _verify(arguments)
        except OverflowError:
            _write_error("INPUT_TOO_LARGE")
            return 2
        except OSError:
            _write_error("INPUT_READ_FAILED")
            return 2
        except _CliArgumentError:
            _write_error("CLI_INVALID_ARGUMENT")
            return 2
        except Exception as exc:
            code = _known_verify_error(exc)
            _write_error(code)
            return 3 if code.startswith("VERIFY_") else 2
        output_error = _write_artifact(
            arguments.report,
            artifact,
            protected_inputs=(arguments.plan,),
            protected_roots=protected_roots,
        )
        if output_error is not None:
            _write_error(output_error)
            return 3 if output_error.startswith("OUTPUT_") else 2
        return 0

    if arguments.migration_command == "rehearse":
        try:
            artifact, protected_roots = _rehearse(arguments)
        except OverflowError:
            _write_error("INPUT_TOO_LARGE")
            return 2
        except OSError:
            _write_error("INPUT_READ_FAILED")
            return 2
        except _CliArgumentError:
            _write_error("CLI_INVALID_ARGUMENT")
            return 2
        except _CliOutputBoundaryError as exc:
            _write_error(exc.code)
            return 3 if exc.code.startswith("OUTPUT_") else 2
        except Exception as exc:
            code = _known_rehearsal_error(exc)
            _write_error(code)
            return 3 if code.startswith(("APPLY_", "VERIFY_", "REHEARSAL_")) else 2
        output_error = _write_artifact(
            arguments.report,
            artifact,
            protected_inputs=(arguments.plan,),
            protected_roots=protected_roots,
        )
        if output_error is not None:
            _write_error(output_error)
            return 3 if output_error.startswith("OUTPUT_") else 2
        return 0

    try:
        if arguments.migration_command == "plan":
            artifact, protected_inputs = _plan(arguments)
        elif arguments.migration_command == "confirm-plan":
            artifact, protected_inputs = _confirm(arguments)
        else:  # pragma: no cover - argparse closes this branch
            raise _CliArgumentError("migration_command")
    except OverflowError:
        _write_error("INPUT_TOO_LARGE")
        return 2
    except OSError:
        _write_error("INPUT_READ_FAILED")
        return 2
    except _CliArgumentError:
        _write_error("CLI_INVALID_ARGUMENT")
        return 2
    except Exception as exc:
        _write_error(_known_planner_error(exc))
        return 2

    output_error = _write_artifact(
        arguments.output,
        artifact,
        protected_inputs=protected_inputs,
    )
    if output_error is not None:
        _write_error(output_error)
        return 3 if output_error.startswith("OUTPUT_") else 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
