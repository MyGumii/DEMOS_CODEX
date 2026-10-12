"""Bounded, read-only reconciliation for legacy Notion locator projections.

This module is an offline maintenance boundary.  It is not registered by
either application, does not start a worker, and never writes the legacy
owner shards or the workspace catalog.  Continuation cursors are internal
typed values; only the identifier-free counter report is safe to log.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable

from code_assist_v1.notion_workspace_projection import (
    empty_shadow_counts,
    shadow_reconcile_exact_owner_page,
)
from code_assist_v1.notion_workspace_store import (
    ActiveLegacyPageBindingBatch,
    ActiveLegacyPageBindingCursor,
    WorkspacePageSourceSuperseded,
    WorkspaceStore,
)


SourcePathResolver = Callable[[str], str | os.PathLike[str]]


class WorkspaceReconciliationError(Exception):
    """A sanitized failure at the offline reconciliation boundary."""


@dataclass(frozen=True)
class WorkspaceReconciliationBatchResult:
    """Internal continuation plus an identifier-free report."""

    report: dict[str, int]
    next_cursor: ActiveLegacyPageBindingCursor | None
    has_more: bool


def _catalog_path(store: WorkspaceStore, expected: str | os.PathLike[str]) -> Path:
    try:
        actual = Path(store.db_path).resolve(strict=True)
        candidate = Path(expected).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspaceReconciliationError(
            "workspace catalog is unavailable"
        ) from exc
    if actual != candidate or not candidate.is_file():
        raise WorkspaceReconciliationError("workspace catalog is unavailable")
    return candidate


def _single_state(name: str) -> dict[str, int]:
    counts = empty_shadow_counts()
    counts[name] = 1
    return counts


def _merge(target: dict[str, int], source: dict[str, int]) -> None:
    if set(source) != set(empty_shadow_counts()) or sum(source.values()) != 1:
        raise WorkspaceReconciliationError(
            "projection reconciler returned an invalid result"
        )
    for key, value in source.items():
        target[key] += int(value)


def reconcile_active_page_binding_batch(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    after: ActiveLegacyPageBindingCursor | None = None,
    limit: int = 32,
) -> WorkspaceReconciliationBatchResult:
    """Inspect one bounded keyset page without writing either database.

    Rows activated behind the cursor during a pass are intentionally picked up
    by the next full pass.  The source quiescence attestation is mandatory; it
    is checked before catalog or source access.
    """
    if source_quiesced_confirmed is not True:
        raise WorkspaceReconciliationError(
            "reconciliation requires source_quiesced_confirmed"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspaceReconciliationError("WorkspaceStore required")
    if not callable(source_db_for_owner):
        raise WorkspaceReconciliationError("source_db_for_owner required")
    catalog_path = _catalog_path(store, expected_catalog_db_path)
    batch: ActiveLegacyPageBindingBatch = store.scan_active_page_source_bindings(
        actor_subject_id,
        workspace_id,
        after=after,
        limit=limit,
    )
    report = {
        "scanned": int(batch.scanned_count),
        "visited": len(batch.items),
        **empty_shadow_counts(),
    }

    for binding in batch.items:
        try:
            store.assert_active_page_source_binding_authorized(
                actor_subject_id, binding
            )
        except WorkspacePageSourceSuperseded:
            _merge(report, _single_state("superseded"))
            continue
        except (FileNotFoundError, PermissionError):
            _merge(report, _single_state("permission_mismatch"))
            continue
        except Exception:
            _merge(report, _single_state("catalog_unavailable"))
            continue

        try:
            source_path = source_db_for_owner(binding.source_owner_subject_id)
            outcome = shadow_reconcile_exact_owner_page(
                user_data_root=user_data_root,
                source_owner_id=binding.source_owner_subject_id,
                actor_subject_id=binding.projection_actor_subject_id,
                workspace_id=binding.workspace_id,
                source_page_id=binding.source_page_id,
                expected_source_db_path=source_path,
                expected_catalog_db_path=catalog_path,
                expected_binding_version=binding.binding_version,
            )
        except Exception:
            outcome = _single_state("catalog_unavailable")

        # Do not disclose or count a result obtained across an IAM/generation
        # race.  The post-I/O fence wins over the tentative shadow outcome.
        try:
            store.assert_active_page_source_binding_authorized(
                actor_subject_id, binding
            )
        except WorkspacePageSourceSuperseded:
            outcome = _single_state("superseded")
        except (FileNotFoundError, PermissionError):
            outcome = _single_state("permission_mismatch")
        except Exception:
            outcome = _single_state("catalog_unavailable")
        _merge(report, outcome)

    return WorkspaceReconciliationBatchResult(
        report=report,
        next_cursor=batch.next_cursor,
        has_more=batch.has_more,
    )


__all__ = [
    "SourcePathResolver",
    "WorkspaceReconciliationBatchResult",
    "WorkspaceReconciliationError",
    "reconcile_active_page_binding_batch",
]
