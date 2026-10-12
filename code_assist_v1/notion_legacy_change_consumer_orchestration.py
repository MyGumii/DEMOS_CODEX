"""Disabled/offline convergence between source work and catalog healing.

The source shard and workspace catalog cannot share one SQLite transaction.
This orchestration therefore relies on two immutable ledgers: the catalog page
effect commits first, then the exact source work receipt becomes terminal.  A
crash in between replays the catalog operation before source-page I/O and then
replays or completes the source terminal operation.  Watermarks are never
deleted or acknowledged here, and this module has no request/startup/runtime
registration.
"""
from __future__ import annotations

import hashlib
from typing import Any

from code_assist_v1.notion_legacy_change_consumer import (
    LegacyChangeConsumerSourceFenceConflict,
    LegacyChangeConsumerStore,
    LegacyChangeConsumerWork,
)
from code_assist_v1.notion_workspace_healing import (
    SourcePathResolver,
    WorkspaceHealingEffectReceipt,
    heal_one_locator_batch,
)
from code_assist_v1.notion_workspace_store import WorkspaceStore


def _completion_report(
    result: dict[str, Any], *, replayed: bool
) -> dict[str, Any]:
    completions = (
        list(result["catalog_completions"])
        if "catalog_completions" in result
        else [result["catalog_completion"]]
    )
    counters = {key: 0 for key in completions[0]["counters"]}
    for completion in completions:
        for key, value in completion["counters"].items():
            counters[key] += int(value)
    return {
        "completed": True,
        "replayed": replayed,
        "pending_work_count": 0,
        "workspace_run_count": len(completions),
        "catalog_counters": counters,
    }


def _source_terminal_operation_id(
    work: LegacyChangeConsumerWork,
    effect: WorkspaceHealingEffectReceipt,
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (
                "np2a-legacy-source-work-terminal-v1",
                work.consumer,
                work.pass_id,
                work.receipt_sha256,
                effect.effect_operation_id,
                effect.disposition,
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"np2a-source-work-terminal:{digest}"


def _receipt_matches(
    work: LegacyChangeConsumerWork, source_receipt: dict[str, Any]
) -> bool:
    return source_receipt == {
        "source_epoch": work.source_epoch,
        "full_sweep_generation": work.full_sweep_generation,
        "change_generation": work.change_generation,
        "change_mask": work.change_mask,
        "source_exists": work.source_exists,
        "source_active": work.source_active,
    }


def converge_one_legacy_change_consumer_batch(
    source_store: LegacyChangeConsumerStore,
    catalog_store: WorkspaceStore,
    *,
    consumer: str,
    pass_id: str,
    actor_subject_id: str,
    workspace_id: str,
    run_id: str,
    worker: str,
    lease_token: str,
    fencing_generation: int,
    expected_version: int,
    user_data_root: str,
    expected_catalog_db_path: str,
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    limit: int = 32,
) -> dict[str, Any]:
    """Converge one catalog keyset batch and terminalize matching source work.

    Source work absent from the explicitly administered workspace stays
    pending.  This is deliberate: the method never performs a catalog-global
    existence probe and therefore cannot turn a wrong workspace selection into
    cross-tenant information or a false ``no_binding`` terminal receipt.
    """
    if not isinstance(source_store, LegacyChangeConsumerStore):
        raise ValueError("LegacyChangeConsumerStore required")
    if not isinstance(catalog_store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    if source_quiesced_confirmed is not True:
        raise ValueError("consumer convergence requires source quiescence")
    source_store.assert_current_source()
    before = source_store.pass_status(consumer=consumer, pass_id=pass_id)
    if before["pass"]["status"] != "materialized":
        raise ValueError("materialized legacy change-consumer pass required")
    observed = {"matched": 0, "applied": 0, "superseded": 0}

    def observe(effect: WorkspaceHealingEffectReceipt) -> None:
        work = source_store.pending_work_for_page(
            consumer=consumer,
            pass_id=pass_id,
            page_id=effect.source_page_id,
        )
        if work is None or work.source_owner_id != effect.source_owner_subject_id:
            return
        if effect.source_receipt is None:
            if effect.disposition != "superseded":
                raise LegacyChangeConsumerSourceFenceConflict(
                    "catalog effect source receipt is unavailable"
                )
            observed["matched"] += 1
            observed["superseded"] += 1
            return
        else:
            if not _receipt_matches(work, effect.source_receipt):
                raise LegacyChangeConsumerSourceFenceConflict(
                    "catalog effect source receipt does not match work"
                )
            catalog_fence = catalog_store.get_healing_source_fence(
                actor_subject_id,
                workspace_id,
                run_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                source_owner_subject_id=work.source_owner_id,
            )
            if (
                catalog_fence is None
                or catalog_fence.source_epoch != work.source_epoch
                or catalog_fence.full_sweep_generation
                != work.full_sweep_generation
                or catalog_fence.source_device != work.source_device
                or catalog_fence.source_inode != work.source_inode
                or catalog_fence.source_link_count != work.source_link_count
            ):
                raise LegacyChangeConsumerSourceFenceConflict(
                    "catalog source fence does not match work"
                )
            if effect.disposition == "superseded":
                observed["matched"] += 1
                observed["superseded"] += 1
                return
            terminal_status = "applied"
        source_store.record_work_effect(
            work=work,
            terminal_status=terminal_status,
            effect_workspace_id=effect.workspace_id,
            effect_binding_generation=effect.expected_binding_generation,
            effect_operation_id=effect.effect_operation_id,
            effect_disposition=effect.disposition,
            operation_id=_source_terminal_operation_id(work, effect),
        )
        observed["matched"] += 1
        observed[terminal_status] += 1

    catalog_result = heal_one_locator_batch(
        catalog_store,
        actor_subject_id=actor_subject_id,
        workspace_id=workspace_id,
        run_id=run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        user_data_root=user_data_root,
        expected_catalog_db_path=expected_catalog_db_path,
        source_db_for_owner=source_db_for_owner,
        source_quiesced_confirmed=True,
        limit=limit,
        effect_observer=observe,
    )
    after = source_store.pass_status(consumer=consumer, pass_id=pass_id)
    return {
        "run": catalog_result["run"],
        "healing_report": catalog_result["healing_report"],
        "source_work_report": {
            **observed,
            "remaining_pending": after["work"]["pending"],
            "full_sweep_required": bool(after["pass"]["full_sweep_required"]),
        },
    }


def record_legacy_change_consumer_full_sweep_completion(
    source_store: LegacyChangeConsumerStore,
    catalog_store: WorkspaceStore,
    *,
    consumer: str,
    pass_id: str,
    actor_subject_id: str,
    workspace_id: str,
    run_id: str,
    source_owner_subject_id: str,
    source_quiesced_confirmed: bool,
) -> dict[str, Any]:
    """Persist an exact completed traversal receipt without acknowledging work.

    The source ledger replay is checked before quiescence and before any
    catalog call.  A new effect requires explicit quiescence, zero pending
    source work, a completed locator-apply run, and an exact owner source fence.
    The public return is identifier-free and is not resolver-readiness evidence.
    """
    if not isinstance(source_store, LegacyChangeConsumerStore):
        raise ValueError("LegacyChangeConsumerStore required")
    if not isinstance(catalog_store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    replay = source_store.get_full_sweep_completion_replay(
        consumer=consumer,
        pass_id=pass_id,
    )
    if replay is not None:
        return _completion_report(replay, replayed=True)
    if source_quiesced_confirmed is not True:
        raise ValueError("full-sweep completion requires source quiescence")
    result = source_store.record_full_sweep_completion(
        consumer=consumer,
        pass_id=pass_id,
        catalog_completion_loader=lambda: (
            catalog_store.get_completed_healing_source_evidence(
                actor_subject_id,
                workspace_id,
                run_id,
                source_owner_subject_id=source_owner_subject_id,
            )
        ),
    )
    return _completion_report(result, replayed=False)


def record_legacy_change_consumer_full_sweep_set_completion(
    source_store: LegacyChangeConsumerStore,
    catalog_store: WorkspaceStore,
    *,
    consumer: str,
    pass_id: str,
    workspace_runs: tuple[dict[str, str], ...],
    source_owner_subject_id: str,
    source_quiesced_confirmed: bool,
) -> dict[str, Any]:
    """Bind one source pass to every explicit workspace run it terminalized."""
    if not isinstance(source_store, LegacyChangeConsumerStore):
        raise ValueError("LegacyChangeConsumerStore required")
    if not isinstance(catalog_store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    replay = source_store.get_full_sweep_completion_replay(
        consumer=consumer, pass_id=pass_id
    )
    if replay is not None:
        return _completion_report(replay, replayed=True)
    if source_quiesced_confirmed is not True:
        raise ValueError("full-sweep completion requires source quiescence")
    if not isinstance(workspace_runs, tuple) or not 1 <= len(workspace_runs) <= 32:
        raise ValueError("bounded workspace_runs required")
    normalized: list[dict[str, str]] = []
    for item in workspace_runs:
        if not isinstance(item, dict) or set(item) != {
            "actor_subject_id",
            "workspace_id",
            "run_id",
        }:
            raise ValueError("valid workspace run required")
        normalized.append({key: str(item[key]) for key in sorted(item)})
    normalized.sort(key=lambda item: (item["workspace_id"], item["run_id"]))
    if len({item["workspace_id"] for item in normalized}) != len(normalized):
        raise ValueError("one completed run per workspace required")

    def load_completions() -> list[dict[str, Any]]:
        return [
            catalog_store.get_completed_healing_source_evidence(
                item["actor_subject_id"],
                item["workspace_id"],
                item["run_id"],
                source_owner_subject_id=source_owner_subject_id,
            )
            for item in normalized
        ]

    result = source_store.record_full_sweep_set_completion(
        consumer=consumer,
        pass_id=pass_id,
        catalog_completions_loader=load_completions,
    )
    return _completion_report(result, replayed=False)


__all__ = [
    "converge_one_legacy_change_consumer_batch",
    "record_legacy_change_consumer_full_sweep_completion",
    "record_legacy_change_consumer_full_sweep_set_completion",
]
