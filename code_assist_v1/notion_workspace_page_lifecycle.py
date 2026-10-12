"""Disabled NP-2C lifecycle command orchestration.

This module proves the cross-database command protocol with an injected,
replay-safe source effect.  It is deliberately absent from routes, startup,
workers, and application registration.  The source adapter must commit or
recognize an exact post-state before returning its secret-free receipt; the
catalog then records and revalidates that receipt before atomic finalization.
"""
from __future__ import annotations

from typing import Any, Callable

from code_assist_v1.notion_workspace_store import (
    SCHEMA_VERSION,
    WorkspacePageLifecycleCommandRequest,
    WorkspacePageLifecycleSourceReceipt,
    WorkspaceStore,
)


DISABLED_PAGE_LIFECYCLE_REPORT_CONTRACT = (
    "np2c_disabled_page_lifecycle_command_v1"
)


class WorkspacePageLifecycleSourceEffectFailed(Exception):
    """The adapter proved that the source effect did not commit."""


class WorkspacePageLifecycleSourceOutcomeAmbiguous(Exception):
    """The adapter cannot prove whether the source effect committed."""


SourceEffect = Callable[
    [WorkspacePageLifecycleCommandRequest, str, str],
    WorkspacePageLifecycleSourceReceipt,
]
SourceReceiptRevalidator = Callable[[WorkspacePageLifecycleSourceReceipt], bool]


def _report(result: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    """Return a secret- and identifier-free disabled-kernel report."""
    status = str(result["status"])
    return {
        "contract": DISABLED_PAGE_LIFECYCLE_REPORT_CONTRACT,
        "status": status,
        "replayed": bool(replayed),
        "finalized": status == "finalized",
        "manual_audit": status == "manual_audit",
        "failed": status == "failed",
        "command_version": int(result["command_version"]),
        "fencing_generation": int(result["fencing_generation"]),
        "affected_node_count": int(result["affected_node_count"]),
        "file_work_count": int(result["file_work_count"]),
    }


def run_disabled_page_lifecycle_command(
    store: WorkspaceStore,
    request: WorkspacePageLifecycleCommandRequest,
    *,
    actor_subject_id: str,
    operation_id: str,
    worker: str,
    lease_token: str,
    lease_seconds: int,
    source_quiesced_confirmed: bool,
    source_effect: SourceEffect,
    source_revalidator: SourceReceiptRevalidator,
) -> dict[str, Any]:
    """Converge one disabled rename command through its catalog saga.

    The same ``operation_id`` must be used by the injected source adapter as
    its own idempotency key.  An exact finalized catalog replay returns before
    either source callback.  A source-applied retry renews/reclaims its lease
    and revalidates only the stored receipt; it never reapplies the effect.
    """
    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    if not isinstance(request, WorkspacePageLifecycleCommandRequest):
        raise ValueError("WorkspacePageLifecycleCommandRequest required")
    if not callable(source_effect):
        raise ValueError("source_effect callback required")
    if not callable(source_revalidator):
        raise ValueError("source_revalidator callback required")
    if source_quiesced_confirmed is not True:
        raise ValueError("page lifecycle command requires source quiescence")
    if SCHEMA_VERSION >= 2026081313:
        raise RuntimeError(
            "legacy page lifecycle v1 kernel is disabled; "
            "use an exact rename-v2 or structural-v3 finalizer"
        )

    command = store.get_page_lifecycle_command_replay(
        actor_subject_id,
        request,
        operation_id=operation_id,
    )
    if command is None:
        command = store.prepare_page_lifecycle_command(
            actor_subject_id,
            request,
            operation_id=operation_id,
        )
    status = str(command["status"])
    if status == "finalized":
        replay = store.finalize_page_lifecycle_command(
            actor_subject_id,
            request.workspace_id,
            str(command["command_id"]),
            operation_id=operation_id,
            worker=worker,
            lease_token=lease_token,
            fencing_generation=int(command["fencing_generation"]),
            expected_command_version=int(command["command_version"]) - 1,
            source_revalidator=lambda _receipt: (_ for _ in ()).throw(
                AssertionError("source revalidation ran during finalized replay")
            ),
        )
        return _report(replay, replayed=True)
    if status in {"failed", "manual_audit"}:
        return _report(command, replayed=True)
    if status not in {"prepared", "source_applied"}:
        raise ValueError("page lifecycle command status is invalid")
    # Claiming the current version is deliberate.  It becomes the initial
    # claim, same-token heartbeat, or expired-lease reclaim as appropriate.
    command = store.claim_page_lifecycle_command(
        actor_subject_id,
        request.workspace_id,
        str(command["command_id"]),
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        expected_command_version=int(command["command_version"]),
    )
    status = str(command["status"])
    if status == "prepared":
        try:
            receipt = source_effect(
                request, str(command["command_id"]), operation_id
            )
        except WorkspacePageLifecycleSourceOutcomeAmbiguous:
            terminal = store.terminate_page_lifecycle_command(
                actor_subject_id,
                request.workspace_id,
                str(command["command_id"]),
                operation_id=operation_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=int(command["fencing_generation"]),
                expected_command_version=int(command["command_version"]),
                disposition="manual_audit",
                failure_code="source_outcome_ambiguous",
            )
            return _report(terminal, replayed=False)
        except WorkspacePageLifecycleSourceEffectFailed:
            terminal = store.terminate_page_lifecycle_command(
                actor_subject_id,
                request.workspace_id,
                str(command["command_id"]),
                operation_id=operation_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=int(command["fencing_generation"]),
                expected_command_version=int(command["command_version"]),
                disposition="failed",
                failure_code="source_effect_refused",
            )
            return _report(terminal, replayed=False)
        command = store.record_page_lifecycle_source_applied(
            actor_subject_id,
            request.workspace_id,
            str(command["command_id"]),
            receipt,
            operation_id=operation_id,
            worker=worker,
            lease_token=lease_token,
            fencing_generation=int(command["fencing_generation"]),
            expected_command_version=int(command["command_version"]),
            source_revalidator=source_revalidator,
        )

    finalized = store.finalize_page_lifecycle_command(
        actor_subject_id,
        request.workspace_id,
        str(command["command_id"]),
        operation_id=operation_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=int(command["fencing_generation"]),
        expected_command_version=int(command["command_version"]),
        source_revalidator=source_revalidator,
    )
    return _report(finalized, replayed=False)
