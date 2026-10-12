"""Sentinel-guarded, offline-only NP-2A rehearsal evidence on schema 1317.

This module is deliberately not registered with application startup, request
handling, or a command-line entry point.  It can mutate only an explicitly
prepared rehearsal catalog whose catalog and legacy shards resolve beneath a
sentinel-marked root.  It never acknowledges or deletes legacy watermarks.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any

from code_assist_v1.notion_legacy_change_contract import (
    inspect_legacy_change_contract,
)
from code_assist_v1.notion_workspace_healing import heal_one_locator_batch
from code_assist_v1.notion_workspace_reconciliation import SourcePathResolver
from code_assist_v1.notion_workspace_store import (
    SCHEMA_VERSION,
    SOURCE_HEALING_QUARANTINE_REASONS,
    WorkspaceHealingCounters,
    WorkspaceStore,
)


REHEARSAL_SENTINEL_NAME = ".np2a-1309-offline-rehearsal"
REHEARSAL_SENTINEL_CONTENT = b"NP2A_1309_OFFLINE_REHEARSAL_ONLY\n"
REHEARSAL_REPORT_CONTRACT = "np2a_1317_offline_rehearsal_v10"


class WorkspaceRehearsalRefused(Exception):
    """Raised before rehearsal writes when isolation evidence is incomplete."""


def _resolved_existing(
    path: str | os.PathLike[str],
    *,
    root: Path,
    kind: str,
    allow_root: bool = False,
) -> Path:
    try:
        candidate = Path(os.fspath(path)).resolve(strict=True)
        relative = candidate.relative_to(root)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceRehearsalRefused(
            f"rehearsal {kind} must resolve beneath its sentinel root"
        ) from exc
    if not allow_root and not relative.parts:
        raise WorkspaceRehearsalRefused(
            f"rehearsal {kind} must resolve beneath its sentinel root"
        )
    return candidate


def _rehearsal_root(path: str | os.PathLike[str]) -> Path:
    try:
        raw = Path(os.fspath(path))
        root = raw.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceRehearsalRefused(
            "rehearsal sentinel root is unavailable"
        ) from exc
    sentinel = root / REHEARSAL_SENTINEL_NAME
    try:
        if (
            not root.is_dir()
            or raw.is_symlink()
            or sentinel.is_symlink()
            or not sentinel.is_file()
            or sentinel.read_bytes() != REHEARSAL_SENTINEL_CONTENT
        ):
            raise WorkspaceRehearsalRefused(
                "exact offline rehearsal sentinel is required"
            )
    except OSError as exc:
        raise WorkspaceRehearsalRefused(
            "exact offline rehearsal sentinel is required"
        ) from exc
    return root


def _open_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA query_only=ON")
    return con


def _catalog_snapshot(
    catalog_path: Path,
    *,
    workspace_id: str,
) -> tuple[tuple[str, ...], dict[str, int]]:
    con = _open_readonly(catalog_path)
    try:
        owners = tuple(
            str(row[0])
            for row in con.execute(
                "SELECT DISTINCT source_owner_subject_id "
                "FROM workspace_page_sources WHERE workspace_id=? "
                "ORDER BY source_owner_subject_id",
                (workspace_id,),
            ).fetchall()
        )
        binding_row = con.execute(
            "SELECT COUNT(*) AS total,"
            "SUM(CASE WHEN state='active' THEN 1 ELSE 0 END) AS active,"
            "SUM(CASE WHEN state='quarantined' THEN 1 ELSE 0 END) AS quarantined "
            "FROM workspace_page_sources WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        permanent_placeholders = ",".join(
            "?" for _ in SOURCE_HEALING_QUARANTINE_REASONS
        )
        permanent = int(
            con.execute(
                "SELECT COUNT(*) FROM workspace_page_sources "
                "WHERE workspace_id=? AND state='quarantined' "
                f"AND quarantine_reason IN ({permanent_placeholders})",
                (workspace_id, *sorted(SOURCE_HEALING_QUARANTINE_REASONS)),
            ).fetchone()[0]
        )

        def projected_count(table: str, state: str) -> int:
            return int(
                con.execute(
                    f"SELECT COUNT(*) FROM {table} item "
                    "JOIN workspace_page_sources binding "
                    "ON binding.workspace_id=item.workspace_id "
                    "AND binding.source_owner_subject_id="
                    "item.source_owner_subject_id "
                    "AND binding.source_page_id=item.source_page_id "
                    "WHERE binding.workspace_id=? AND binding.state=?",
                    (workspace_id, state),
                ).fetchone()[0]
            )

        return owners, {
            "binding_count": int(binding_row["total"] or 0),
            "active_binding_count": int(binding_row["active"] or 0),
            "quarantined_binding_count": int(binding_row["quarantined"] or 0),
            "permanent_quarantine_count": permanent,
            "active_projection_count": projected_count(
                "workspace_page_source_projections", "active"
            ),
            "quarantined_projection_count": projected_count(
                "workspace_page_source_projections", "quarantined"
            ),
            "active_file_locator_count": projected_count(
                "workspace_file_sources", "active"
            ),
            "quarantined_file_locator_count": projected_count(
                "workspace_file_sources", "quarantined"
            ),
            "active_public_locator_count": projected_count(
                "workspace_public_sources", "active"
            ),
            "quarantined_public_locator_count": projected_count(
                "workspace_public_sources", "quarantined"
            ),
        }
    except sqlite3.Error as exc:
        raise WorkspaceRehearsalRefused(
            "rehearsal catalog evidence is unavailable"
        ) from exc
    finally:
        con.close()


def _assert_legacy_watermark_ready(source_path: Path) -> None:
    con = _open_readonly(source_path)
    try:
        contract = inspect_legacy_change_contract(con, verify_integrity=True)
        if contract.get("installed") is not True:
            raise WorkspaceRehearsalRefused(
                "legacy change watermark contract is not installed"
            )
    except WorkspaceRehearsalRefused:
        raise
    except Exception as exc:
        raise WorkspaceRehearsalRefused(
            "legacy change watermark contract is invalid"
        ) from exc
    finally:
        con.close()


def run_isolated_np2a_1309_rehearsal(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    lease_token: str,
    limit: int = 32,
) -> dict[str, Any]:
    """Run one isolated locator pass plus response-loss replay.

    The prepared fixture must contain at least two bindings so the pass can
    prove both a normal stale locator refresh and a source-derived permanent
    quarantine.  Only aggregate, identifier-free evidence is returned.
    """
    if source_quiesced_confirmed is not True:
        raise WorkspaceRehearsalRefused(
            "offline rehearsal requires source quiescence confirmation"
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 32:
        raise WorkspaceRehearsalRefused(
            "offline rehearsal limit must be between 2 and 32"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspaceRehearsalRefused("exact workspace store is required")

    root = _rehearsal_root(rehearsal_root)
    _resolved_existing(
        user_data_root, root=root, kind="data root", allow_root=True
    )
    catalog_path = _resolved_existing(
        expected_catalog_db_path, root=root, kind="catalog"
    )
    try:
        store_catalog = Path(store.db_path).resolve(strict=True)
    except OSError as exc:
        raise WorkspaceRehearsalRefused(
            "rehearsal catalog is unavailable"
        ) from exc
    if store_catalog != catalog_path:
        raise WorkspaceRehearsalRefused(
            "workspace store does not match the rehearsal catalog"
        )
    capabilities = store.capabilities()
    if capabilities != {"workspace_v2": True, "schema_version": SCHEMA_VERSION}:
        raise WorkspaceRehearsalRefused(
            "rehearsal catalog is not the exact ready runtime schema"
        )

    owners, before = _catalog_snapshot(
        catalog_path, workspace_id=workspace_id
    )
    if not owners or not 2 <= before["binding_count"] <= limit:
        raise WorkspaceRehearsalRefused(
            "rehearsal fixture must fit one bounded multi-binding pass"
        )
    source_paths: dict[str, Path] = {}
    for owner in owners:
        try:
            raw_source = source_db_for_owner(owner)
        except Exception as exc:
            raise WorkspaceRehearsalRefused(
                "rehearsal source resolver failed"
            ) from exc
        source_path = _resolved_existing(
            raw_source, root=root, kind="legacy source"
        )
        _assert_legacy_watermark_ready(source_path)
        source_paths[owner] = source_path

    def guarded_source(owner: str) -> Path:
        try:
            return source_paths[owner]
        except KeyError as exc:
            raise WorkspaceRehearsalRefused(
                "rehearsal source owner is outside the prepared fixture"
            ) from exc

    run = store.start_healing_run(
        actor_subject_id,
        workspace_id,
        worker="np2a.rehearsal.offline",
        lease_token=lease_token,
        lease_seconds=300,
        operation_id="np2a-1309-offline-rehearsal-start-v2",
        healing_mode="locator_apply",
    )["run"]
    kwargs = {
        "actor_subject_id": actor_subject_id,
        "workspace_id": workspace_id,
        "run_id": run["id"],
        "worker": "np2a.rehearsal.offline",
        "lease_token": lease_token,
        "fencing_generation": run["lease"]["fencing_generation"],
        "expected_version": run["version"],
        "user_data_root": user_data_root,
        "expected_catalog_db_path": catalog_path,
        "source_db_for_owner": guarded_source,
        "source_quiesced_confirmed": True,
        "limit": limit,
    }
    first = heal_one_locator_batch(store, **kwargs)
    counters = WorkspaceHealingCounters().descriptor()
    sanitized_counters = {
        key: int(first.get("healing_report", {}).get(key, -1))
        for key in counters
    }
    permanent_observations = sum(
        sanitized_counters[key]
        for key in ("ambiguous", "permission_mismatch", "unsafe_path", "oversize")
    )
    if (
        first.get("run", {}).get("status") != "completed"
        or sanitized_counters["stale"] < 1
        or permanent_observations < 1
    ):
        raise WorkspaceRehearsalRefused(
            "rehearsal did not prove normal healing and permanent quarantine"
        )

    def source_io_must_not_repeat(_owner: str) -> Path:
        raise AssertionError("committed checkpoint replay repeated source I/O")

    replay = heal_one_locator_batch(
        store,
        **{**kwargs, "source_db_for_owner": source_io_must_not_repeat},
    )
    if replay != first:
        raise WorkspaceRehearsalRefused(
            "rehearsal checkpoint replay did not converge"
        )

    _owners_after, after = _catalog_snapshot(
        catalog_path, workspace_id=workspace_id
    )
    if (
        after["active_binding_count"] < 1
        or after["permanent_quarantine_count"] < 1
        or after["active_projection_count"] < 1
        or after["quarantined_projection_count"] != 0
        or after["quarantined_file_locator_count"] != 0
        or after["quarantined_public_locator_count"] != 0
    ):
        raise WorkspaceRehearsalRefused(
            "rehearsal locator or quarantine postcondition failed"
        )
    health = store.verify_deep_health()
    return {
        "contract": REHEARSAL_REPORT_CONTRACT,
        "offline_only": True,
        "quiescence_confirmed": True,
        "catalog_schema_version": SCHEMA_VERSION,
        "legacy_watermark_source_count": len(source_paths),
        "legacy_watermarks_ready": True,
        "healing_report": sanitized_counters,
        "checkpoint_replay_identical": True,
        "checkpoint_replay_bypassed_source_io": True,
        "catalog": after,
        "health": {
            "healthy": bool(health["healthy"]),
            "integrity_ok": bool(health["integrity_ok"]),
            "foreign_key_violation_count": int(
                health["foreign_key_violation_count"]
            ),
            "issue_count": len(health["issues"]),
        },
    }


# Internal compatibility alias.  The evidence emitted by this callable is
# intentionally the current 1309/v2 contract; callers cannot silently obtain
# a report labelled as the older 1308 rehearsal from a 1309 catalog.
run_isolated_np2a_1308_rehearsal = run_isolated_np2a_1309_rehearsal


__all__ = [
    "REHEARSAL_REPORT_CONTRACT",
    "REHEARSAL_SENTINEL_CONTENT",
    "REHEARSAL_SENTINEL_NAME",
    "WorkspaceRehearsalRefused",
    "run_isolated_np2a_1308_rehearsal",
    "run_isolated_np2a_1309_rehearsal",
]
