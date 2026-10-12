"""Offline-only exact-source materialization for the NP-2C page tree.

This module is deliberately absent from application startup, routes, and
workers.  It can publish only the first complete 1311 tree snapshot under an
explicit rehearsal sentinel and source-quiescence assertion.  It never
mutates a legacy source and never acknowledges source watermarks.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import sqlite3
from typing import Any

from code_assist_v1.notion_legacy_change_contract import (
    assert_legacy_rollback_journal,
    legacy_source_file_identity,
    open_exact_legacy_change_source,
    resolve_legacy_change_paths,
)
from code_assist_v1.notion_legacy_changes import (
    LegacyOwnerIntegrityAttestation,
    _positive_generation,
    _receipt_from_snapshot,
    inspect_legacy_incarnation_source_contract,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    require_legacy_page_incarnation_id,
)
from code_assist_v1.notion_workspace_rehearsal import (
    _rehearsal_root,
    _resolved_existing,
)
from code_assist_v1.notion_workspace_store import (
    MAX_PAGE_TREE_DEPTH,
    MAX_PAGE_TREE_SYNCHRONOUS_SUBTREE_NODES,
    WorkspacePageTreeNodeCandidate,
    WorkspacePageTreeSnapshot,
    WorkspacePageTreeSourceFence,
    WorkspaceStore,
    _normalize_page_tree_snapshot,
    _page_tree_node_structure_sha256,
    _page_tree_snapshot_sha256,
    _page_tree_source_fence_set_sha256,
    legacy_incarnation_receipt_sha256,
    sqlite_source_identity,
)


OFFLINE_PAGE_TREE_CAPTURE_CONTRACT = "np2c_page_tree_capture_v1"
OFFLINE_PAGE_TREE_MATERIALIZATION_REPORT_CONTRACT = (
    "np2c_page_tree_materialization_report_v1"
)


class WorkspacePageTreeMaterializationRefused(Exception):
    """Sanitized fail-closed refusal for the offline materializer."""


def _source_path_mapping(
    value: Mapping[str, str | os.PathLike[str]],
    *,
    expected_owners: tuple[str, ...],
) -> dict[str, str | os.PathLike[str]]:
    if not isinstance(value, Mapping) or any(
        not isinstance(owner, str) for owner in value
    ):
        raise WorkspacePageTreeMaterializationRefused(
            "exact source path mapping required"
        )
    if tuple(sorted(value)) != expected_owners:
        raise WorkspacePageTreeMaterializationRefused(
            "exact source path mapping does not cover the binding owners"
        )
    return {owner: value[owner] for owner in expected_owners}


def _capture_owner_rows(
    *,
    user_data_root: Path,
    rehearsal_root: Path,
    owner: str,
    expected_source_db_path: str | os.PathLike[str],
    expected_bindings: tuple[dict[str, Any], ...],
    integrity_attestation: LegacyOwnerIntegrityAttestation | None = None,
) -> tuple[
    WorkspacePageTreeSourceFence,
    list[dict[str, Any]],
    LegacyOwnerIntegrityAttestation,
]:
    paths = resolve_legacy_change_paths(
        user_data_root=user_data_root,
        source_owner_id=owner,
        expected_source_db_path=expected_source_db_path,
    )
    source_path = _resolved_existing(
        paths["source"], root=rehearsal_root, kind="legacy source"
    )
    assert_legacy_rollback_journal(source_path)
    source_identity = legacy_source_file_identity(source_path)
    con = open_exact_legacy_change_source(
        source_path,
        root=paths["root"],
        expected_resolved=source_path,
        expected_identity=source_identity,
    )
    rows: list[dict[str, Any]] = []
    try:
        con.execute("BEGIN")
        inspected = inspect_legacy_incarnation_source_contract(
            con, verify_integrity=integrity_attestation is None
        )
        if inspected.get("installed") is not True:
            raise WorkspacePageTreeMaterializationRefused(
                "legacy incarnation tracking is unavailable"
            )
        if integrity_attestation is not None and (
            integrity_attestation.source_owner_id != owner
            or integrity_attestation.source_identity != source_identity
            or integrity_attestation.object_hash != str(inspected["object_hash"])
        ):
            raise WorkspacePageTreeMaterializationRefused(
                "legacy owner integrity attestation changed"
            )
        owner_fence: WorkspacePageTreeSourceFence | None = None
        for expected in expected_bindings:
            page_id = str(expected["source_page_id"])
            receipt = _receipt_from_snapshot(
                con,
                source_owner_id=owner,
                page_id=page_id,
                source_identity=source_identity,
            )
            history = con.execute(
                """
                SELECT incarnation_generation,incarnation_id,retired_at
                FROM notion_page_incarnation_history
                WHERE page_id=?
                ORDER BY incarnation_generation DESC LIMIT 1
                """,
                (page_id,),
            ).fetchone()
            page = con.execute(
                """
                SELECT id,owner_user_id,parent_id,position,archived
                FROM pages WHERE id=? LIMIT 1
                """,
                (page_id,),
            ).fetchone()
            if history is None or page is None:
                raise WorkspacePageTreeMaterializationRefused(
                    "legacy active page evidence is unavailable"
                )
            incarnation_generation = _positive_generation(
                history["incarnation_generation"],
                field="incarnation_generation",
            )
            incarnation_id = require_legacy_page_incarnation_id(
                history["incarnation_id"]
            )
            receipt_sha256 = legacy_incarnation_receipt_sha256(
                source_owner_id=owner,
                page_id=page_id,
                incarnation_id=incarnation_id,
                incarnation_generation=incarnation_generation,
                incarnation_retired=False,
                change_generation=receipt.generation,
                change_mask=receipt.mask,
                source_exists=receipt.source_exists,
                source_active=receipt.source_active,
                source_epoch=receipt.fence.source_epoch,
                full_sweep_generation=receipt.fence.full_sweep_generation,
                source_device=receipt.fence.device,
                source_inode=receipt.fence.inode,
                source_link_count=receipt.fence.link_count,
            )
            position = page["position"]
            parent_id = page["parent_id"]
            if (
                history["retired_at"] is not None
                or not receipt.source_exists
                or not receipt.source_active
                or str(page["id"]) != page_id
                or str(page["owner_user_id"]) != owner
                or type(page["archived"]) is not int
                or int(page["archived"]) != 0
                or type(position) is not int
                or position < 0
                or (
                    parent_id is not None
                    and (
                        not isinstance(parent_id, str)
                        or not parent_id
                        or parent_id != parent_id.strip()
                        or len(parent_id) > 160
                    )
                )
            ):
                raise WorkspacePageTreeMaterializationRefused(
                    "legacy active page evidence is invalid"
                )
            if (
                incarnation_id != expected["source_incarnation_id"]
                or incarnation_generation
                != expected["source_incarnation_generation"]
                or receipt.generation != expected["source_change_generation"]
                    or receipt_sha256 != expected["source_receipt_sha256"]
            ):
                raise WorkspacePageTreeMaterializationRefused(
                    "legacy source incarnation changed"
                )
            fence = WorkspacePageTreeSourceFence(
                source_owner_subject_id=owner,
                source_epoch=receipt.fence.source_epoch,
                full_sweep_generation=receipt.fence.full_sweep_generation,
                source_device=sqlite_source_identity(receipt.fence.device),
                source_inode=sqlite_source_identity(receipt.fence.inode),
                source_link_count=receipt.fence.link_count,
            )
            if owner_fence is None:
                owner_fence = fence
            elif owner_fence != fence:
                raise WorkspacePageTreeMaterializationRefused(
                    "legacy source fence changed during capture"
                )
            rows.append(
                {
                    **expected,
                    "parent_source_page_id": str(parent_id or ""),
                    "source_position": position,
                }
            )
        con.rollback()
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
    assert_legacy_rollback_journal(source_path)
    if legacy_source_file_identity(source_path) != source_identity:
        raise WorkspacePageTreeMaterializationRefused(
            "legacy source changed during page tree capture"
        )
    if not rows:
        raise WorkspacePageTreeMaterializationRefused(
            "owner source capture contained no bindings"
        )
    assert owner_fence is not None
    if integrity_attestation is None:
        integrity_attestation = LegacyOwnerIntegrityAttestation(
            source_owner_id=owner,
            source_device=source_identity[0],
            source_inode=source_identity[1],
            source_link_count=source_identity[2],
            source_size=source_identity[3],
            source_mtime_ns=source_identity[4],
            source_epoch=owner_fence.source_epoch,
            full_sweep_generation=owner_fence.full_sweep_generation,
            object_hash=str(inspected["object_hash"]),
        )
    elif (
        integrity_attestation.source_epoch != owner_fence.source_epoch
        or integrity_attestation.full_sweep_generation
        != owner_fence.full_sweep_generation
    ):
        raise WorkspacePageTreeMaterializationRefused(
            "legacy owner integrity attestation changed"
        )
    return owner_fence, rows, integrity_attestation


def capture_exact_page_tree_snapshot(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_source_db_paths: Mapping[str, str | os.PathLike[str]],
    expected_workspace_version: int,
    expected_tree_version: int,
    expected_tree_state_version: int,
    source_quiesced_confirmed: bool,
    _integrity_attestations: Mapping[
        str, LegacyOwnerIntegrityAttestation
    ] | None = None,
    _captured_attestations: dict[
        str, LegacyOwnerIntegrityAttestation
    ] | None = None,
) -> WorkspacePageTreeSnapshot:
    """Capture one bounded complete active tree from exact owner shards."""
    if source_quiesced_confirmed is not True:
        raise WorkspacePageTreeMaterializationRefused(
            "offline page tree capture requires source quiescence"
        )
    if not isinstance(store, WorkspaceStore):
        raise WorkspacePageTreeMaterializationRefused(
            "exact workspace store is required"
        )
    root = _rehearsal_root(rehearsal_root)
    data_root = _resolved_existing(
        user_data_root, root=root, kind="data root", allow_root=True
    )
    catalog_path = _resolved_existing(
        expected_catalog_db_path, root=root, kind="catalog"
    )
    try:
        if Path(store.db_path).resolve(strict=True) != catalog_path:
            raise WorkspacePageTreeMaterializationRefused(
                "workspace store does not match the rehearsal catalog"
            )
    except OSError as exc:
        raise WorkspacePageTreeMaterializationRefused(
            "workspace catalog is unavailable"
        ) from exc

    context = store.page_tree_materialization_context(
        actor_subject_id, workspace_id
    )
    if (
        context["workspace_version"] != expected_workspace_version
        or context["tree_version"] != expected_tree_version
        or context["tree_state_version"] != expected_tree_state_version
    ):
        raise WorkspacePageTreeMaterializationRefused(
            "workspace page tree capture fence changed"
        )
    bindings = tuple(context["bindings"])
    if len(bindings) > MAX_PAGE_TREE_SYNCHRONOUS_SUBTREE_NODES:
        raise WorkspacePageTreeMaterializationRefused(
            "workspace page tree exceeds the materialization bound"
        )
    owners = tuple(sorted({str(item["source_owner_subject_id"]) for item in bindings}))
    source_paths = _source_path_mapping(
        expected_source_db_paths, expected_owners=owners
    )
    by_owner = {
        owner: tuple(
            item for item in bindings
            if item["source_owner_subject_id"] == owner
        )
        for owner in owners
    }
    source_fences: list[WorkspacePageTreeSourceFence] = []
    raw_rows: list[dict[str, Any]] = []
    for owner in owners:
        attestation = (
            _integrity_attestations.get(owner)
            if _integrity_attestations is not None
            else None
        )
        fence, owner_rows, captured_attestation = _capture_owner_rows(
            user_data_root=data_root,
            rehearsal_root=root,
            owner=owner,
            expected_source_db_path=source_paths[owner],
            expected_bindings=by_owner[owner],
            integrity_attestation=attestation,
        )
        if _captured_attestations is not None:
            _captured_attestations[owner] = captured_attestation
        source_fences.append(fence)
        raw_rows.extend(owner_rows)

    row_by_key = {
        (str(row["source_owner_subject_id"]), str(row["source_page_id"])): row
        for row in raw_rows
    }
    if len(row_by_key) != len(raw_rows):
        raise WorkspacePageTreeMaterializationRefused(
            "workspace page tree identities are duplicated"
        )
    depths: dict[tuple[str, str], int] = {}
    visiting: set[tuple[str, str]] = set()

    def depth_for(key: tuple[str, str]) -> int:
        if key in depths:
            return depths[key]
        if key in visiting:
            raise WorkspacePageTreeMaterializationRefused(
                "legacy page tree contains a cycle"
            )
        visiting.add(key)
        row = row_by_key[key]
        parent_page = str(row["parent_source_page_id"])
        if not parent_page:
            depth = 0
        else:
            parent_key = (key[0], parent_page)
            if parent_key not in row_by_key:
                raise WorkspacePageTreeMaterializationRefused(
                    "legacy page tree parent is hidden or unbound"
                )
            depth = depth_for(parent_key) + 1
        visiting.remove(key)
        if depth > MAX_PAGE_TREE_DEPTH:
            raise WorkspacePageTreeMaterializationRefused(
                "legacy page tree exceeds the depth bound"
            )
        depths[key] = depth
        return depth

    for key in row_by_key:
        depth_for(key)
    sibling_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, row in row_by_key.items():
        parent_page = str(row["parent_source_page_id"])
        scope = (key[0], parent_page) if parent_page else ("", "")
        sibling_groups.setdefault(scope, []).append(row)
    ordinals: dict[tuple[str, str], int] = {}
    for group in sibling_groups.values():
        group.sort(
            key=lambda row: (
                int(row["source_position"]),
                str(row["source_owner_subject_id"]),
                str(row["source_page_id"]),
            )
        )
        for ordinal, row in enumerate(group, start=1):
            ordinals[
                (
                    str(row["source_owner_subject_id"]),
                    str(row["source_page_id"]),
                )
            ] = ordinal

    nodes: list[WorkspacePageTreeNodeCandidate] = []
    for key, row in row_by_key.items():
        parent_page = str(row["parent_source_page_id"])
        parent_owner = key[0] if parent_page else ""
        depth = depths[key]
        ordinal = ordinals[key]
        structure = _page_tree_node_structure_sha256(
            workspace_id=workspace_id,
            source_owner_subject_id=key[0],
            source_page_id=key[1],
            binding_version=int(row["binding_version"]),
            source_incarnation_id=str(row["source_incarnation_id"]),
            source_incarnation_generation=int(
                row["source_incarnation_generation"]
            ),
            source_change_generation=int(row["source_change_generation"]),
            source_receipt_sha256=str(row["source_receipt_sha256"]),
            parent_source_owner_subject_id=parent_owner,
            parent_source_page_id=parent_page,
            sibling_ordinal=ordinal,
            depth=depth,
        )
        nodes.append(
            WorkspacePageTreeNodeCandidate(
                workspace_id=workspace_id,
                source_owner_subject_id=key[0],
                source_page_id=key[1],
                binding_version=int(row["binding_version"]),
                source_incarnation_id=str(row["source_incarnation_id"]),
                source_incarnation_generation=int(
                    row["source_incarnation_generation"]
                ),
                source_change_generation=int(row["source_change_generation"]),
                source_receipt_sha256=str(row["source_receipt_sha256"]),
                parent_source_owner_subject_id=parent_owner,
                parent_source_page_id=parent_page,
                sibling_ordinal=ordinal,
                depth=depth,
                structure_sha256=structure,
            )
        )
    canonical_nodes = tuple(
        sorted(
            nodes,
            key=lambda item: (
                item.depth,
                item.parent_source_owner_subject_id,
                item.parent_source_page_id,
                item.sibling_ordinal,
                item.source_owner_subject_id,
                item.source_page_id,
            ),
        )
    )
    canonical_fences = tuple(
        sorted(source_fences, key=lambda item: item.source_owner_subject_id)
    )
    fence_digest = _page_tree_source_fence_set_sha256(canonical_fences)
    snapshot = WorkspacePageTreeSnapshot(
        workspace_id=workspace_id,
        expected_tree_version=expected_tree_version,
        expected_tree_state_version=expected_tree_state_version,
        source_fences=canonical_fences,
        nodes=canonical_nodes,
        source_fence_set_sha256=fence_digest,
        snapshot_sha256=_page_tree_snapshot_sha256(
            workspace_id=workspace_id,
            expected_tree_version=expected_tree_version,
            expected_tree_state_version=expected_tree_state_version,
            source_fence_set_sha256=fence_digest,
            nodes=canonical_nodes,
        ),
    )
    try:
        return _normalize_page_tree_snapshot(snapshot)
    except ValueError as exc:
        raise WorkspacePageTreeMaterializationRefused(
            "captured page tree snapshot is invalid"
        ) from exc


def revalidate_exact_page_tree_snapshot(
    expected: WorkspacePageTreeSnapshot,
    store: WorkspaceStore,
    **capture_kwargs: Any,
) -> bool:
    """Recapture once and compare the entire source/catalog commitment."""
    try:
        actual = capture_exact_page_tree_snapshot(store, **capture_kwargs)
    except WorkspacePageTreeMaterializationRefused:
        return False
    return actual == expected


def _report(result: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "contract": OFFLINE_PAGE_TREE_MATERIALIZATION_REPORT_CONTRACT,
        "replayed": replayed,
        "materialization_state": str(result["materialization_state"]),
        "tree_version": int(result["tree_version"]),
        "tree_state_version": int(result["tree_state_version"]),
        "active_node_count": int(result["active_node_count"]),
        "archived_node_count": int(result["archived_node_count"]),
        "deleted_node_count": int(result["deleted_node_count"]),
        "owner_count": int(result["owner_count"]),
        "ready_for_resolver_cutover": False,
        "runtime_commands_enabled": False,
        "source_watermarks_acknowledged": False,
    }


def run_offline_page_tree_materialization(
    store: WorkspaceStore,
    *,
    actor_subject_id: str,
    workspace_id: str,
    rehearsal_root: str | os.PathLike[str],
    user_data_root: str | os.PathLike[str],
    expected_catalog_db_path: str | os.PathLike[str],
    expected_source_db_paths: Mapping[str, str | os.PathLike[str]],
    expected_workspace_version: int,
    expected_tree_version: int,
    expected_tree_state_version: int,
    operation_id: str,
    source_quiesced_confirmed: bool,
) -> dict[str, Any]:
    """Replay first, otherwise capture twice and publish one initial tree."""
    replay = store.get_page_tree_materialization_replay(
        actor_subject_id, workspace_id, operation_id=operation_id
    )
    if replay is not None:
        return _report(replay, replayed=True)
    if source_quiesced_confirmed is not True:
        raise WorkspacePageTreeMaterializationRefused(
            "offline page tree materialization requires source quiescence"
        )
    capture_kwargs = {
        "actor_subject_id": actor_subject_id,
        "workspace_id": workspace_id,
        "rehearsal_root": rehearsal_root,
        "user_data_root": user_data_root,
        "expected_catalog_db_path": expected_catalog_db_path,
        "expected_source_db_paths": expected_source_db_paths,
        "expected_workspace_version": expected_workspace_version,
        "expected_tree_version": expected_tree_version,
        "expected_tree_state_version": expected_tree_state_version,
        "source_quiesced_confirmed": True,
    }
    integrity_attestations: dict[str, LegacyOwnerIntegrityAttestation] = {}
    snapshot = capture_exact_page_tree_snapshot(
        store,
        **capture_kwargs,
        _captured_attestations=integrity_attestations,
    )
    result = store.materialize_page_tree_snapshot(
        actor_subject_id,
        workspace_id,
        snapshot,
        source_revalidator=lambda expected: revalidate_exact_page_tree_snapshot(
            expected,
            store,
            **capture_kwargs,
            _integrity_attestations=integrity_attestations,
        ),
        base_version=expected_workspace_version,
        operation_id=operation_id,
        source_quiesced_confirmed=True,
    )
    return _report(result, replayed=False)


__all__ = [
    "OFFLINE_PAGE_TREE_CAPTURE_CONTRACT",
    "OFFLINE_PAGE_TREE_MATERIALIZATION_REPORT_CONTRACT",
    "WorkspacePageTreeMaterializationRefused",
    "capture_exact_page_tree_snapshot",
    "revalidate_exact_page_tree_snapshot",
    "run_offline_page_tree_materialization",
]
