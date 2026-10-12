"""Offline, bounded NP-2A shadow checks and source-fenced locator healing.

Both workflows require an explicitly quiesced legacy source and a durable,
admin-scoped healing run.  The locator-apply path atomically refreshes or
quarantines one catalog binding at a time, while reports remain identifier
free.  There is no application registration, background worker, destructive
watermark acknowledgement, or request-time migration in this slice.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from typing import Any, Callable

from code_assist_v1.notion_workspace_reconciliation import (
    SourcePathResolver,
    reconcile_active_page_binding_batch,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyOwnerIntegrityAttestation,
    LegacyOwnerIntegrityAttestationMismatch,
    create_legacy_owner_integrity_attestation,
    read_legacy_page_change_receipt,
)
from code_assist_v1.notion_workspace_inventory import LegacyInventoryBoundsExceeded
from code_assist_v1.notion_workspace_projection import (
    LegacyProjectionAmbiguousError,
    LegacyProjectionOversizeError,
    LegacyProjectionPermissionError,
    LegacyProjectionStaleError,
    LegacyProjectionSupersededError,
    UnsafeLegacyFileError,
    WorkspaceProjectionError,
    capture_exact_owner_healing_outcome,
    revalidate_exact_owner_healing_outcome,
)
from code_assist_v1.notion_workspace_store import (
    ActiveLegacyPageBindingCursor,
    ActiveLegacyPageBinding,
    WorkspaceHealingCounters,
    WorkspaceHealingSourceFence,
    WorkspaceHealingSourceFenceConflict,
    WorkspaceOperationConflict,
    WorkspacePageProjectionAmbiguous,
    WorkspacePageSourceSuperseded,
    WorkspaceStore,
)


@dataclass(frozen=True)
class WorkspaceHealingEffectReceipt:
    """Internal post-commit handoff; never expose it as an HTTP report."""

    workspace_id: str
    source_owner_subject_id: str
    source_page_id: str
    expected_binding_generation: int
    effect_operation_id: str
    disposition: str
    replayed: bool
    source_receipt: dict[str, Any] | None


def healing_checkpoint_operation_id(
    *,
    run_id: str,
    fencing_generation: int,
    expected_version: int,
) -> str:
    """Derive one stable effect ID for an exact run/fence/version checkpoint."""
    if not isinstance(run_id, str) or not run_id.startswith("wheal_"):
        raise ValueError("valid run_id required")
    for field, value in (
        ("fencing_generation", fencing_generation),
        ("expected_version", expected_version),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field} must be an integer of at least 1")
    digest = hashlib.sha256(
        f"np2a-shadow-checkpoint\0{run_id}\0{fencing_generation}\0{expected_version}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"np2a-shadow-checkpoint:{digest}"


def _stable_healing_operation_id(
    kind: str,
    *,
    run_id: str,
    fencing_generation: int,
    expected_version: int,
    workspace_id: str = "",
    owner: str = "",
    page_id: str = "",
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (
                f"np2a-{kind}",
                run_id,
                str(fencing_generation),
                str(expected_version),
                workspace_id,
                owner,
                page_id,
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"np2a-{kind}:{digest}"


def healing_page_effect_operation_id(
    *,
    run_id: str,
    fencing_generation: int,
    expected_version: int,
    workspace_id: str,
    owner: str,
    page_id: str,
    source_epoch: int,
    full_sweep_generation: int,
    binding_generation: int,
) -> str:
    """Derive the durable source-fenced identity for one page effect.

    The source epoch prevents a deleted-and-recreated watermark generation
    from colliding with an earlier pass.  The binding generation prevents a
    later catalog incarnation from claiming an earlier effect.  The complete
    candidate remains protected independently by the immutable ledger request
    hash, so a changed receipt under the same identity fails closed.
    """
    for field, value in (
        ("fencing_generation", fencing_generation),
        ("expected_version", expected_version),
        ("source_epoch", source_epoch),
        ("full_sweep_generation", full_sweep_generation),
        ("binding_generation", binding_generation),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field} must be an integer of at least 1")
    identities = {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "owner": owner,
        "page_id": page_id,
    }
    for field, value in identities.items():
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\0" in value
        ):
            raise ValueError(f"valid {field} required")
    digest = hashlib.sha256(
        "\0".join(
            (
                "np2a-healing-page-effect-v2",
                run_id,
                str(fencing_generation),
                str(expected_version),
                workspace_id,
                owner,
                page_id,
                str(source_epoch),
                str(full_sweep_generation),
                str(binding_generation),
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"np2a-healing-page-effect-v2:{digest}"


def _candidate_source_fence_matches(
    expected: WorkspaceHealingSourceFence,
    *,
    user_data_root: str | os.PathLike[str],
    owner: str,
    page_id: str,
    source_path: str | os.PathLike[str],
    source_quiesced_confirmed: bool,
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> bool:
    try:
        receipt = read_legacy_page_change_receipt(
            user_data_root=user_data_root,
            source_owner_id=owner,
            page_id=page_id,
            expected_source_db_path=source_path,
            source_quiesced_confirmed=source_quiesced_confirmed,
            integrity_attestation=integrity_attestation,
        )
        current = WorkspaceHealingSourceFence(
            source_epoch=receipt.fence.source_epoch,
            full_sweep_generation=receipt.fence.full_sweep_generation,
            source_device=(
                receipt.fence.device
                if receipt.fence.device <= 9_223_372_036_854_775_807
                else receipt.fence.device - 18_446_744_073_709_551_616
            ),
            source_inode=(
                receipt.fence.inode
                if receipt.fence.inode <= 9_223_372_036_854_775_807
                else receipt.fence.inode - 18_446_744_073_709_551_616
            ),
            source_link_count=receipt.fence.link_count,
        )
        return current == expected
    except Exception:
        return False


def checkpoint_one_shadow_batch(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    run_id: str,
    worker: str,
    lease_token: str,
    fencing_generation: int,
    expected_version: int,
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    limit: int = 32,
) -> dict[str, Any]:
    """Observe and durably checkpoint exactly one bounded keyset page.

    A retry with the same run fence/version deterministically reuses the same
    operation ID.  The store's immutable operation ledger therefore prevents
    counter duplication if the caller lost the first response after commit.
    """
    operation_id = healing_checkpoint_operation_id(
        run_id=run_id,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
    )
    replay = store.get_healing_checkpoint_replay(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        operation_id=operation_id,
    )
    if replay is not None:
        return {"run": replay["run"], "shadow_report": dict(replay["delta"])}
    store.assert_live_healing_run_lease(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
    )
    current = store.get_healing_run(
        actor_subject_id, workspace_id, run_id
    )
    if (
        current.get("healing_mode") != "shadow"
        or current["status"] != "running"
        or int(current["version"]) != expected_version
        or str(current["lease"]["worker"]) != worker
        or int(current["lease"]["fencing_generation"]) != fencing_generation
    ):
        raise ValueError("healing run lease metadata is stale")
    raw_cursor = current["cursor"]
    persisted_cursor = (
        ActiveLegacyPageBindingCursor(
            workspace_id=str(raw_cursor["workspace_id"]),
            source_owner_subject_id=str(raw_cursor["source_owner_subject_id"]),
            source_page_id=str(raw_cursor["source_page_id"]),
        )
        if isinstance(raw_cursor, dict)
        else None
    )
    observed = reconcile_active_page_binding_batch(
        store,
        actor_subject_id=actor_subject_id,
        workspace_id=workspace_id,
        user_data_root=user_data_root,
        expected_catalog_db_path=expected_catalog_db_path,
        source_db_for_owner=source_db_for_owner,
        source_quiesced_confirmed=source_quiesced_confirmed,
        after=persisted_cursor,
        limit=limit,
    )
    report = observed.report
    counters = WorkspaceHealingCounters(
        scanned=int(report["scanned"]),
        visited=int(report["visited"]),
        match=int(report["match"]),
        safe_miss=int(report["safe_miss"]),
        stale=int(report["stale"]),
        superseded=int(report["superseded"]),
        ambiguous=int(report["ambiguous"]),
        permission_mismatch=int(report["permission_mismatch"]),
        unsafe_path=int(report["unsafe_path"]),
        catalog_unavailable=int(report["catalog_unavailable"]),
        oversize=int(report.get("oversize", 0)),
    )
    checkpoint = store.checkpoint_healing_run(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        counters=counters,
        next_cursor=observed.next_cursor,
        has_more=observed.has_more,
        operation_id=operation_id,
    )
    return {"run": checkpoint["run"], "shadow_report": counters.descriptor()}


def heal_one_locator_batch(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    run_id: str,
    worker: str,
    lease_token: str,
    fencing_generation: int,
    expected_version: int,
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    source_db_for_owner: SourcePathResolver,
    source_quiesced_confirmed: bool,
    limit: int = 32,
    effect_observer: Callable[[WorkspaceHealingEffectReceipt], None] | None = None,
) -> dict[str, Any]:
    """Apply one bounded, fenced page-source healing batch offline."""
    if source_quiesced_confirmed is not True:
        raise ValueError("locator healing requires source quiescence")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
        raise ValueError("limit must be between 1 and 32")
    if effect_observer is not None and not callable(effect_observer):
        raise ValueError("effect_observer must be callable")
    checkpoint_operation = _stable_healing_operation_id(
        "healing-apply-checkpoint",
        run_id=run_id,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        workspace_id=workspace_id,
    )
    checkpoint_replay = store.get_healing_checkpoint_replay(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        operation_id=checkpoint_operation,
    )
    if checkpoint_replay is not None:
        return {
            "run": checkpoint_replay["run"],
            "healing_report": dict(checkpoint_replay["delta"]),
        }
    store.assert_live_healing_run_lease(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
    )
    current = store.get_healing_run(actor_subject_id, workspace_id, run_id)
    if (
        current.get("healing_mode") != "locator_apply"
        or current["status"] != "running"
        or int(current["version"]) != expected_version
        or str(current["lease"]["worker"]) != worker
        or int(current["lease"]["fencing_generation"]) != fencing_generation
    ):
        raise ValueError("locator-apply run lease metadata is stale")
    raw_cursor = current["cursor"]
    persisted_cursor = (
        ActiveLegacyPageBindingCursor(
            workspace_id=str(raw_cursor["workspace_id"]),
            source_owner_subject_id=str(raw_cursor["source_owner_subject_id"]),
            source_page_id=str(raw_cursor["source_page_id"]),
        )
        if isinstance(raw_cursor, dict)
        else None
    )
    batch = store.scan_active_page_source_bindings(
        actor_subject_id,
        workspace_id,
        after=persisted_cursor,
        limit=limit,
        include_quarantined=True,
    )
    counts = WorkspaceHealingCounters(scanned=batch.scanned_count)
    values = counts.descriptor()
    owner_sources: dict[
        str, tuple[str | os.PathLike[str], LegacyOwnerIntegrityAttestation]
    ] = {}
    owner_fences: dict[str, WorkspaceHealingSourceFence | None] = {}

    def observe(
        binding: ActiveLegacyPageBinding,
        *,
        expected_binding_generation: int,
        operation_id: str,
        disposition: str,
        replayed: bool,
        result: dict[str, Any] | None,
    ) -> None:
        if effect_observer is None:
            return
        source_receipt = None
        if result is not None:
            healing = result.get("healing")
            if not isinstance(healing, dict) or not isinstance(
                healing.get("source_receipt"), dict
            ):
                raise ValueError("healing effect source receipt is invalid")
            source_receipt = dict(healing["source_receipt"])
        effect_observer(
            WorkspaceHealingEffectReceipt(
                workspace_id=binding.workspace_id,
                source_owner_subject_id=binding.source_owner_subject_id,
                source_page_id=binding.source_page_id,
                expected_binding_generation=expected_binding_generation,
                effect_operation_id=operation_id,
                disposition=disposition,
                replayed=replayed,
                source_receipt=source_receipt,
            )
        )

    for binding in batch.items:
        values["visited"] += 1
        owner = binding.source_owner_subject_id
        if owner not in owner_fences:
            owner_fences[owner] = store.get_healing_source_fence(
                actor_subject_id,
                workspace_id,
                run_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                source_owner_subject_id=owner,
            )
        stored_fence = owner_fences[owner]
        replayed_effect = False
        if stored_fence is not None:
            # A committed effect can advance the binding by exactly one before
            # a later checkpoint fails.  Probe the current generation first,
            # then the immediately preceding generation, without source I/O.
            generations = [binding.binding_version]
            if binding.binding_version > 1:
                generations.append(binding.binding_version - 1)
            for effect_binding_generation in generations:
                effect_operation = healing_page_effect_operation_id(
                    run_id=run_id,
                    fencing_generation=fencing_generation,
                    expected_version=expected_version,
                    workspace_id=workspace_id,
                    owner=owner,
                    page_id=binding.source_page_id,
                    source_epoch=stored_fence.source_epoch,
                    full_sweep_generation=stored_fence.full_sweep_generation,
                    binding_generation=effect_binding_generation,
                )
                replay = store.get_healing_effect_replay(
                    actor_subject_id,
                    workspace_id,
                    run_id,
                    worker=worker,
                    lease_token=lease_token,
                    fencing_generation=fencing_generation,
                    expected_version=expected_version,
                    expected_binding_version=effect_binding_generation,
                    source_owner_subject_id=owner,
                    source_page_id=binding.source_page_id,
                    operation_id=effect_operation,
                )
                if replay is None:
                    continue
                disposition = str(
                    replay.get("healing", {}).get("disposition") or ""
                )
                if disposition not in values:
                    raise ValueError("healing effect replay is invalid")
                values[disposition] += 1
                observe(
                    binding,
                    expected_binding_generation=effect_binding_generation,
                    operation_id=effect_operation,
                    disposition=disposition,
                    replayed=True,
                    result=replay,
                )
                replayed_effect = True
                break
        if replayed_effect:
            continue
        if (
            binding.binding_state == "quarantined"
            and binding.quarantine_reason != "source_archived"
        ):
            # Quarantine already removed every locator.  Only an archived
            # source is revisited so a later permanent deletion can harden its
            # reason.  All other reasons require an explicit reviewed rebind;
            # reauthorizing the revoked projection actor would otherwise wedge
            # this bounded keyset forever.
            values["superseded"] += 1
            observe(
                binding,
                expected_binding_generation=binding.binding_version,
                operation_id=_stable_healing_operation_id(
                    "healing-work-superseded",
                    run_id=run_id,
                    fencing_generation=fencing_generation,
                    expected_version=expected_version,
                    workspace_id=workspace_id,
                    owner=owner,
                    page_id=binding.source_page_id,
                ),
                disposition="superseded",
                replayed=False,
                result=None,
            )
            continue
        try:
            try:
                store.assert_active_page_source_binding_authorized(
                    actor_subject_id, binding
                )
            except (FileNotFoundError, PermissionError):
                if binding.binding_state != "quarantined":
                    raise
                values["superseded"] += 1
                observe(
                    binding,
                    expected_binding_generation=binding.binding_version,
                    operation_id=_stable_healing_operation_id(
                        "healing-work-superseded",
                        run_id=run_id,
                        fencing_generation=fencing_generation,
                        expected_version=expected_version,
                        workspace_id=workspace_id,
                        owner=owner,
                        page_id=binding.source_page_id,
                    ),
                    disposition="superseded",
                    replayed=False,
                    result=None,
                )
                continue
            owner_source = owner_sources.get(owner)
            if owner_source is None:
                source_path = source_db_for_owner(owner)
                integrity_attestation = create_legacy_owner_integrity_attestation(
                    user_data_root=user_data_root,
                    source_owner_id=owner,
                    expected_source_db_path=source_path,
                    source_quiesced_confirmed=True,
                )
                owner_source = (source_path, integrity_attestation)
                owner_sources[owner] = owner_source
            else:
                source_path, integrity_attestation = owner_source
            candidate = capture_exact_owner_healing_outcome(
                user_data_root=user_data_root,
                source_owner_id=binding.source_owner_subject_id,
                workspace_id=workspace_id,
                source_page_id=binding.source_page_id,
                expected_source_db_path=source_path,
                expected_catalog_db_path=expected_catalog_db_path,
                expected_binding_version=binding.binding_version,
                source_quiesced_confirmed=True,
                integrity_attestation=integrity_attestation,
            )
            source_fence_operation = _stable_healing_operation_id(
                "healing-source-fence",
                run_id=run_id,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                workspace_id=workspace_id,
                owner=binding.source_owner_subject_id,
            )
            try:
                store.fence_healing_source_generation(
                    actor_subject_id,
                    workspace_id,
                    run_id,
                    worker=worker,
                    lease_token=lease_token,
                    fencing_generation=fencing_generation,
                    expected_version=expected_version,
                    source_owner_subject_id=binding.source_owner_subject_id,
                    source_fence=candidate.source_fence(),
                    source_fence_revalidator=lambda expected, owner=binding.source_owner_subject_id,
                    page=binding.source_page_id, path=source_path,
                    attestation=integrity_attestation: _candidate_source_fence_matches(
                        expected,
                        user_data_root=user_data_root,
                        owner=owner,
                        page_id=page,
                        source_path=path,
                        source_quiesced_confirmed=True,
                        integrity_attestation=attestation,
                    ),
                    operation_id=source_fence_operation,
                )
            except WorkspaceOperationConflict as exc:
                # The fence operation ID is deterministic per run/owner.  A
                # different request hash therefore means that this owner shard
                # no longer matches the generation already fixed for the pass.
                raise WorkspaceHealingSourceFenceConflict(
                    "healing source generation changed"
                ) from exc
            committed_fence = candidate.source_fence()
            owner_fences[owner] = committed_fence
            effect_operation = healing_page_effect_operation_id(
                run_id=run_id,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                workspace_id=workspace_id,
                owner=owner,
                page_id=binding.source_page_id,
                source_epoch=committed_fence.source_epoch,
                full_sweep_generation=committed_fence.full_sweep_generation,
                binding_generation=candidate.binding_version,
            )
            replay = store.get_healing_effect_replay(
                actor_subject_id,
                workspace_id,
                run_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                expected_binding_version=candidate.binding_version,
                source_owner_subject_id=owner,
                source_page_id=binding.source_page_id,
                operation_id=effect_operation,
            )
            if replay is not None:
                disposition = str(
                    replay.get("healing", {}).get("disposition") or ""
                )
                if disposition not in values:
                    raise ValueError("healing effect replay is invalid")
                values[disposition] += 1
                observe(
                    binding,
                    expected_binding_generation=candidate.binding_version,
                    operation_id=effect_operation,
                    disposition=disposition,
                    replayed=True,
                    result=replay,
                )
                continue
            result = store.apply_page_source_healing(
                actor_subject_id,
                workspace_id,
                run_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=fencing_generation,
                expected_run_version=expected_version,
                candidate=candidate,
                candidate_revalidator=lambda value, path=source_path,
                attestation=integrity_attestation: (
                    revalidate_exact_owner_healing_outcome(
                        value,
                        user_data_root=user_data_root,
                        expected_source_db_path=path,
                        expected_catalog_db_path=expected_catalog_db_path,
                        source_quiesced_confirmed=True,
                        integrity_attestation=attestation,
                    )
                ),
                operation_id=effect_operation,
            )
            disposition = str(result.get("healing", {}).get("disposition") or "")
            if disposition not in values:
                raise ValueError("healing effect result is invalid")
            values[disposition] += 1
            observe(
                binding,
                expected_binding_generation=candidate.binding_version,
                operation_id=effect_operation,
                disposition=disposition,
                replayed=False,
                result=result,
            )
        except (LegacyProjectionSupersededError, WorkspacePageSourceSuperseded):
            values["superseded"] += 1
            observe(
                binding,
                expected_binding_generation=binding.binding_version,
                operation_id=_stable_healing_operation_id(
                    "healing-work-superseded",
                    run_id=run_id,
                    fencing_generation=fencing_generation,
                    expected_version=expected_version,
                    workspace_id=workspace_id,
                    owner=owner,
                    page_id=binding.source_page_id,
                ),
                disposition="superseded",
                replayed=False,
                result=None,
            )
        except (LegacyProjectionOversizeError, LegacyInventoryBoundsExceeded,
                UnsafeLegacyFileError, LegacyProjectionPermissionError,
                LegacyProjectionAmbiguousError, WorkspacePageProjectionAmbiguous) as exc:
            # Permanent observations are converted into a source-fenced
            # quarantine DTO before the effect.  If one appears only during
            # revalidation, the source changed and the durable cursor must not
            # advance while an old projection remains active.
            raise WorkspaceProjectionError(
                "legacy healing source changed during revalidation"
            ) from exc
        except LegacyProjectionStaleError:
            values["stale"] += 1
        except (
            LegacyOwnerIntegrityAttestationMismatch,
            WorkspaceHealingSourceFenceConflict,
        ):
            # The current page did not produce a terminal page effect.  Keep
            # the identifier-free batch report internally consistent while
            # failing the whole source-generation pass.
            values["visited"] -= 1
            fail_operation = _stable_healing_operation_id(
                "healing-source-generation-failed",
                run_id=run_id,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                workspace_id=workspace_id,
            )
            failed = store.fail_healing_run(
                actor_subject_id,
                workspace_id,
                run_id,
                worker=worker,
                lease_token=lease_token,
                fencing_generation=fencing_generation,
                expected_version=expected_version,
                failure_code="source_generation_changed",
                operation_id=fail_operation,
            )
            return {
                "run": failed["run"],
                # The in-flight batch was not checkpointed.  Report the
                # persisted counters so a lost response cannot overstate
                # durable run progress after prior page effects committed.
                "healing_report": dict(failed["run"]["counters"]),
            }
        except WorkspaceProjectionError:
            # Catalog/source availability is retryable: never advance the
            # durable cursor or convert it into a successful checkpoint.
            raise

    counters = WorkspaceHealingCounters(**values)
    checkpoint = store.checkpoint_healing_run(
        actor_subject_id,
        workspace_id,
        run_id,
        worker=worker,
        lease_token=lease_token,
        fencing_generation=fencing_generation,
        expected_version=expected_version,
        counters=counters,
        next_cursor=batch.next_cursor,
        has_more=batch.has_more,
        operation_id=checkpoint_operation,
    )
    return {
        "run": checkpoint["run"],
        "healing_report": counters.descriptor(),
    }


__all__ = [
    "checkpoint_one_shadow_batch",
    "heal_one_locator_batch",
    "healing_checkpoint_operation_id",
    "healing_page_effect_operation_id",
]
