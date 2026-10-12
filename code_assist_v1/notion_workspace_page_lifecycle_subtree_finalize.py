"""Offline catalog finalizer for duplicate, Trash, and restore receipts.

This module is deliberately route-unregistered.  It never opens a legacy
source.  The caller supplies one bounded, quiesced source receipt plus exact
per-result-page evidence and a callback that revalidates the complete tuple.
Exact operation replay is checked before the callback.  A new effect commits
the immutable receipt, every effect/mapping, binding/projection/tree changes,
workspace/security/tree generations, operation, audit, and outbox in one
catalog transaction.  Cross-database delivery is at-least-once and
state-idempotent; it is not claimed to be exactly-once.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_store as catalog
from code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract import (
    LegacyPageLifecycleReceipt,
    validate_lifecycle_receipt,
)


@dataclass(frozen=True)
class WorkspacePageLifecycleSubtreePageCandidate:
    page_id: str
    source_revision: int
    source_updated_at: int
    page_sha256: str
    incarnation_id: str
    incarnation_generation: int
    incarnation_retired: bool
    source_epoch: int
    full_sweep_generation: int
    source_device: int
    source_inode: int
    source_link_count: int
    change_generation: int
    change_mask: int
    source_exists: bool
    source_active: bool
    receipt_sha256: str
    source_proof: catalog.LegacyPageSourceProof | None
    locator_projection: catalog.LegacyPageLocatorProjection | None

    def descriptor(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "source_revision": self.source_revision,
            "source_updated_at": self.source_updated_at,
            "page_sha256": self.page_sha256,
            "incarnation_id": self.incarnation_id,
            "incarnation_generation": self.incarnation_generation,
            "incarnation_retired": self.incarnation_retired,
            "source_epoch": self.source_epoch,
            "full_sweep_generation": self.full_sweep_generation,
            "source_device": self.source_device,
            "source_inode": self.source_inode,
            "source_link_count": self.source_link_count,
            "change_generation": self.change_generation,
            "change_mask": self.change_mask,
            "source_exists": self.source_exists,
            "source_active": self.source_active,
            "receipt_sha256": self.receipt_sha256,
            "source_proof": (
                self.source_proof.descriptor() if self.source_proof is not None else None
            ),
            "locator_projection": (
                self.locator_projection.descriptor()
                if self.locator_projection is not None
                else None
            ),
        }


@dataclass(frozen=True)
class WorkspacePageLifecycleSubtreeCandidate:
    receipt: LegacyPageLifecycleReceipt
    pages: tuple[WorkspacePageLifecycleSubtreePageCandidate, ...]

    def descriptor(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt.descriptor(),
            "pages": [item.descriptor() for item in self.pages],
        }


def _normalize_page_candidate(
    value: WorkspacePageLifecycleSubtreePageCandidate,
    *,
    workspace_id: str,
    owner: str,
    receipt: LegacyPageLifecycleReceipt,
) -> WorkspacePageLifecycleSubtreePageCandidate:
    if not isinstance(value, WorkspacePageLifecycleSubtreePageCandidate):
        raise ValueError("WorkspacePageLifecycleSubtreePageCandidate required")
    page_id = catalog._source_page_identifier(value.page_id)
    source_revision = catalog._bounded_integer(
        value.source_revision, field="source_revision", minimum=1,
        maximum=catalog.MAX_HEALING_SEQUENCE_VALUE,
    )
    source_updated_at = catalog._bounded_integer(
        value.source_updated_at, field="source_updated_at", minimum=0,
        maximum=catalog.MAX_HEALING_TIMESTAMP,
    )
    page_sha256 = catalog._sha256_digest(
        value.page_sha256, field="page_sha256"
    )
    incarnation_id = str(value.incarnation_id or "")
    if catalog.re.fullmatch(r"inc_[0-9a-f]{32}", incarnation_id) is None:
        raise ValueError("valid lifecycle incarnation_id required")
    incarnation_generation = catalog._bounded_integer(
        value.incarnation_generation,
        field="incarnation_generation",
        minimum=1,
        maximum=catalog.MAX_HEALING_SEQUENCE_VALUE,
    )
    if value.incarnation_retired is not False:
        raise ValueError("retired lifecycle incarnation is invalid")
    source_epoch = catalog._bounded_integer(
        value.source_epoch, field="source_epoch", minimum=1,
        maximum=catalog.MAX_HEALING_SEQUENCE_VALUE,
    )
    full_sweep = catalog._bounded_integer(
        value.full_sweep_generation, field="full_sweep_generation", minimum=1,
        maximum=catalog.MAX_HEALING_SEQUENCE_VALUE,
    )
    device = catalog.sqlite_source_identity(value.source_device)
    inode = catalog.sqlite_source_identity(value.source_inode)
    links = catalog._bounded_integer(
        value.source_link_count, field="source_link_count", minimum=1, maximum=65535
    )
    change_generation = catalog._bounded_integer(
        value.change_generation, field="change_generation", minimum=1,
        maximum=catalog.MAX_HEALING_SEQUENCE_VALUE,
    )
    change_mask = catalog._bounded_integer(
        value.change_mask, field="change_mask", minimum=1, maximum=31
    )
    if type(value.source_exists) is not bool or type(value.source_active) is not bool:
        raise ValueError("lifecycle source state must be boolean")
    if not value.source_exists or value.source_active not in {True, False}:
        raise ValueError("lifecycle page must remain physically present")
    if (
        source_epoch != receipt.source_epoch
        or full_sweep != receipt.full_sweep_generation
        or device != catalog.sqlite_source_identity(receipt.source_device)
        or inode != catalog.sqlite_source_identity(receipt.source_inode)
        or links != receipt.source_link_count
    ):
        raise ValueError("lifecycle page source fence mismatch")
    receipt_sha256 = catalog.legacy_incarnation_receipt_sha256(
        source_owner_id=owner,
        page_id=page_id,
        incarnation_id=incarnation_id,
        incarnation_generation=incarnation_generation,
        incarnation_retired=False,
        change_generation=change_generation,
        change_mask=change_mask,
        source_exists=True,
        source_active=value.source_active,
        source_epoch=source_epoch,
        full_sweep_generation=full_sweep,
        source_device=device,
        source_inode=inode,
        source_link_count=links,
    )
    if catalog._sha256_digest(
        value.receipt_sha256, field="receipt_sha256"
    ) != receipt_sha256:
        raise ValueError("lifecycle page receipt digest mismatch")

    proof = value.source_proof
    projection = value.locator_projection
    if value.source_active:
        normalized_proof = catalog._normalize_owner_canonical_source_proof(proof)
        if (
            normalized_proof.owner_subject_id != owner
            or normalized_proof.page_id != page_id
            or normalized_proof.revision != source_revision
            or normalized_proof.updated_at != source_updated_at
            or normalized_proof.content_sha256 != page_sha256
        ):
            raise ValueError("lifecycle source proof identity mismatch")
        (
            projection_actor,
            projection_owner,
            projection_workspace,
            projection_page,
            _binding_version,
            normalized_projection,
        ) = catalog.WorkspaceStore._normalize_page_locator_projection(
            owner, workspace_id, projection
        )
        if (
            projection_actor != owner
            or projection_owner != owner
            or projection_workspace != workspace_id
            or projection_page != page_id
        ):
            raise ValueError("lifecycle locator projection identity mismatch")
    else:
        if proof is not None or projection is not None:
            raise ValueError("trashed lifecycle page cannot publish locators")
        normalized_proof = None
        normalized_projection = None
    normalized = WorkspacePageLifecycleSubtreePageCandidate(
        page_id=page_id,
        source_revision=source_revision,
        source_updated_at=source_updated_at,
        page_sha256=page_sha256,
        incarnation_id=incarnation_id,
        incarnation_generation=incarnation_generation,
        incarnation_retired=False,
        source_epoch=source_epoch,
        full_sweep_generation=full_sweep,
        source_device=device,
        source_inode=inode,
        source_link_count=links,
        change_generation=change_generation,
        change_mask=change_mask,
        source_exists=True,
        source_active=bool(value.source_active),
        receipt_sha256=receipt_sha256,
        source_proof=normalized_proof,
        locator_projection=normalized_projection,
    )
    if normalized != value:
        differing = tuple(
            field
            for field in WorkspacePageLifecycleSubtreePageCandidate.__dataclass_fields__
            if getattr(normalized, field) != getattr(value, field)
        )
        raise ValueError(
            "lifecycle page candidate must be canonical: " + ",".join(differing)
        )
    return normalized


def normalize_subtree_candidate(
    value: WorkspacePageLifecycleSubtreeCandidate,
    *,
    workspace_id: str,
    expected_action: str,
) -> WorkspacePageLifecycleSubtreeCandidate:
    if not isinstance(value, WorkspacePageLifecycleSubtreeCandidate):
        raise ValueError("WorkspacePageLifecycleSubtreeCandidate required")
    raw_receipt = value.receipt
    if not isinstance(raw_receipt, LegacyPageLifecycleReceipt):
        raise ValueError("LegacyPageLifecycleReceipt required")
    receipt = validate_lifecycle_receipt(
        raw_receipt,
        expected_action=catalog.PAGE_LIFECYCLE_SUBTREE_ACTION_MAP[expected_action],
        expected_request_sha256=raw_receipt.source_request_sha256,
    )
    owner = catalog._subject(
        receipt.source_owner_subject_id, field="source_owner_subject_id"
    )
    pages = tuple(
        _normalize_page_candidate(
            item, workspace_id=workspace_id, owner=owner, receipt=receipt
        )
        for item in value.pages
    )
    page_ids = tuple(item.page_id for item in pages)
    if page_ids != tuple(sorted(page_ids)) or len(page_ids) != len(set(page_ids)):
        raise ValueError("lifecycle page candidates must be unique and sorted")
    expected_pages = tuple(sorted({effect.result_page_id for effect in receipt.effects}))
    if page_ids != expected_pages:
        raise ValueError("lifecycle page candidates do not cover the receipt")
    effects = {effect.result_page_id: effect for effect in receipt.effects}
    for page in pages:
        effect = effects[page.page_id]
        expected_active = effect.after_lifecycle == "active"
        if (
            page.source_active != expected_active
            or page.incarnation_id != effect.result_incarnation_id
            or page.incarnation_generation != effect.result_incarnation_generation
            or page.change_generation != effect.result_change_generation
            or page.source_revision != effect.result_revision
        ):
            raise ValueError("lifecycle page candidate contradicts the receipt")
    normalized = WorkspacePageLifecycleSubtreeCandidate(receipt=receipt, pages=pages)
    if normalized != value:
        raise ValueError("lifecycle subtree candidate must be canonical")
    return normalized


def _projection_digest(
    workspace_id: str,
    owner: str,
    page: WorkspacePageLifecycleSubtreePageCandidate,
) -> str:
    projection = page.locator_projection
    if projection is None:
        return ""
    return catalog._page_projection_sha256(
        workspace_id=workspace_id,
        source_owner_subject_id=owner,
        source_page_id=page.page_id,
        binding_version=projection.binding_version,
        file_sources=tuple(item.source_file_id for item in projection.file_sources),
        public_sources=tuple(
            (item.token_sha256, item.allow_duplicate)
            for item in projection.public_sources
        ),
    )


def _incarnation_evidence(
    owner: str, page: WorkspacePageLifecycleSubtreePageCandidate
) -> catalog.LegacyPageIncarnationEvidence:
    if not page.source_active:
        raise ValueError("active lifecycle page evidence required")
    return catalog._normalize_incarnation_evidence(
        catalog.LegacyPageIncarnationEvidence(
            owner_subject_id=owner,
            page_id=page.page_id,
            incarnation_id=page.incarnation_id,
            incarnation_generation=page.incarnation_generation,
            incarnation_retired=False,
            source_epoch=page.source_epoch,
            full_sweep_generation=page.full_sweep_generation,
            source_device=page.source_device,
            source_inode=page.source_inode,
            source_link_count=page.source_link_count,
            change_generation=page.change_generation,
            change_mask=page.change_mask,
            source_exists=True,
            source_active=True,
            receipt_sha256=page.receipt_sha256,
        )
    )


def _insert_attestation(
    con: sqlite3.Connection,
    *,
    actor: str,
    operation_id: str,
    workspace_id: str,
    owner: str,
    binding_version: int,
    page: WorkspacePageLifecycleSubtreePageCandidate,
    now: int,
) -> None:
    evidence = _incarnation_evidence(owner, page)
    catalog._assert_incarnation_lineage(con, evidence)
    con.execute(
        """
        INSERT INTO workspace_page_source_incarnation_attestations(
          workspace_id,source_owner_subject_id,source_page_id,binding_version,
          source_incarnation_id,source_incarnation_generation,
          source_incarnation_retired,source_epoch,full_sweep_generation,
          source_device,source_inode,source_link_count,source_change_generation,
          source_change_mask,source_exists,source_active,source_receipt_sha256,
          source_receipt_domain,reviewed_by_subject_id,review_operation_id,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            owner,
            page.page_id,
            binding_version,
            evidence.incarnation_id,
            evidence.incarnation_generation,
            0,
            evidence.source_epoch,
            evidence.full_sweep_generation,
            evidence.source_device,
            evidence.source_inode,
            evidence.source_link_count,
            evidence.change_generation,
            evidence.change_mask,
            1,
            1,
            evidence.receipt_sha256,
            catalog.LEGACY_INCARNATION_RECEIPT_DOMAIN,
            actor,
            operation_id,
            now,
        ),
    )


def _insert_projection(
    con: sqlite3.Connection,
    *,
    workspace_id: str,
    owner: str,
    binding_version: int,
    page: WorkspacePageLifecycleSubtreePageCandidate,
    now: int,
) -> None:
    projection = page.locator_projection
    if projection is None or projection.binding_version != binding_version:
        raise catalog.WorkspacePageLifecycleSourceConflict(
            "lifecycle locator binding generation changed"
        )
    files = tuple(item.source_file_id for item in projection.file_sources)
    public = tuple(
        (item.token_sha256, item.allow_duplicate)
        for item in projection.public_sources
    )
    con.execute(
        """
        INSERT INTO workspace_page_source_projections(
          workspace_id,source_owner_subject_id,source_page_id,binding_version,
          projection_sha256,file_source_count,public_source_count,projected_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            owner,
            page.page_id,
            binding_version,
            _projection_digest(workspace_id, owner, page),
            len(files),
            len(public),
            now,
        ),
    )
    con.executemany(
        "INSERT INTO workspace_file_sources VALUES(?,?,?,?,?,?)",
        [
            (workspace_id, owner, page.page_id, binding_version, item, now)
            for item in files
        ],
    )
    try:
        con.executemany(
            "INSERT INTO workspace_public_sources VALUES(?,?,?,?,?,?,?)",
            [
                (
                    workspace_id,
                    owner,
                    page.page_id,
                    binding_version,
                    digest,
                    int(allow_duplicate),
                    now,
                )
                for digest, allow_duplicate in public
            ],
        )
    except sqlite3.IntegrityError as exc:
        raise catalog.WorkspacePageProjectionAmbiguous(
            "public token digest is already projected"
        ) from exc


def _current_tree_source_fence_set_sha256(
    con: sqlite3.Connection,
    *,
    workspace_id: str,
    current_owner: str,
    current_receipt: LegacyPageLifecycleReceipt,
) -> str:
    """Rebuild the exact per-owner tree fence after a full-shard receipt.

    A subtree receipt covers every current page in its owner shard.  Other
    owners are unchanged because a command can start only from a complete
    materialization; their latest full-shard manifest (or initial active-node
    attestations) therefore remains the exact fence authority.
    """
    owners = {
        str(row[0])
        for row in con.execute(
            "SELECT DISTINCT source_owner_subject_id "
            "FROM workspace_page_tree_nodes WHERE workspace_id=?",
            (workspace_id,),
        )
    }
    owners.add(current_owner)
    fences: list[catalog.WorkspacePageTreeSourceFence] = []
    for owner in sorted(owners):
        if owner == current_owner:
            fields = (
                current_receipt.source_epoch,
                current_receipt.full_sweep_generation,
                current_receipt.source_device,
                current_receipt.source_inode,
                current_receipt.source_link_count,
            )
        else:
            latest = con.execute(
                "SELECT source_epoch,full_sweep_generation,source_device,"
                "source_inode,source_link_count "
                "FROM workspace_page_lifecycle_subtree_manifests "
                "WHERE workspace_id=? AND source_owner_subject_id=? "
                "ORDER BY order_generation DESC,created_at DESC,command_id DESC "
                "LIMIT 1",
                (workspace_id, owner),
            ).fetchone()
            if latest is not None:
                fields = tuple(latest)
            else:
                initial = con.execute(
                    "SELECT DISTINCT a.source_epoch,a.full_sweep_generation,"
                    "a.source_device,a.source_inode,a.source_link_count "
                    "FROM workspace_page_tree_nodes n "
                    "JOIN workspace_page_source_incarnation_attestations a "
                    "ON a.workspace_id=n.workspace_id "
                    "AND a.source_owner_subject_id=n.source_owner_subject_id "
                    "AND a.source_page_id=n.source_page_id "
                    "AND a.binding_version=n.binding_version "
                    "WHERE n.workspace_id=? AND n.source_owner_subject_id=? "
                    "AND n.lifecycle_state='active'",
                    (workspace_id, owner),
                ).fetchall()
                if len(initial) != 1:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "workspace page tree owner fence is unavailable"
                    )
                fields = tuple(initial[0])
        fences.append(
            catalog.WorkspacePageTreeSourceFence(
                source_owner_subject_id=owner,
                source_epoch=int(fields[0]),
                full_sweep_generation=int(fields[1]),
                source_device=catalog.sqlite_source_identity(fields[2]),
                source_inode=catalog.sqlite_source_identity(fields[3]),
                source_link_count=int(fields[4]),
            )
        )
    return catalog._page_tree_source_fence_set_sha256(tuple(fences))


def finalize_page_lifecycle_subtree_catalog(
    store: catalog.WorkspaceStore,
    actor_subject_id: str | None,
    workspace_id: str,
    command_id: str,
    candidate: WorkspacePageLifecycleSubtreeCandidate,
    *,
    operation_id: str,
    worker: str,
    lease_token: str,
    fencing_generation: int,
    expected_command_version: int,
    source_revalidator: Callable[[WorkspacePageLifecycleSubtreeCandidate], bool],
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Commit one exact source receipt and its full catalog effect atomically."""
    if not isinstance(store, catalog.WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    if not callable(source_revalidator):
        raise ValueError("source_revalidator callback required")
    actor = catalog._subject(actor_subject_id, field="actor_subject_id")
    ws_id = catalog._identifier(workspace_id, prefix="ws", field="workspace_id")
    command = catalog._identifier(command_id, prefix="plc", field="command_id")
    op_id = catalog._operation_id(operation_id)
    worker_id = catalog._delivery_principal(worker, field="worker")
    token_sha256 = catalog._page_lifecycle_lease_token_digest(lease_token)
    fence = catalog._expected_version(fencing_generation)
    command_version = catalog._expected_version(expected_command_version)

    read_con = store._read_connection()
    try:
        store._require_workspace_admin(read_con, ws_id, actor)
        command_row = store._page_lifecycle_row(
            read_con,
            workspace_id=ws_id,
            actor_subject_id=actor,
            operation_id=op_id,
            command_id=command,
        )
        command_action = str(command_row["action"])
    finally:
        read_con.close()
    if command_action not in catalog.PAGE_LIFECYCLE_SUBTREE_ACTION_MAP:
        raise ValueError("subtree page lifecycle command required")
    normalized = normalize_subtree_candidate(
        candidate, workspace_id=ws_id, expected_action=command_action
    )
    receipt = normalized.receipt
    owner = receipt.source_owner_subject_id
    pages = {item.page_id: item for item in normalized.pages}
    effects = {item.result_page_id: item for item in receipt.effects}
    transitioned_count = sum(int(item.transitioned) for item in receipt.effects)
    file_mapping_count = sum(int(item.kind == "file") for item in receipt.mappings)
    aggregate_file_bytes = sum(
        item.byte_size for item in receipt.mappings if item.kind == "file"
    )

    request = {
        "workspace_id": ws_id,
        "command_id": command,
        "worker": worker_id,
        "lease_token_sha256": token_sha256,
        "fencing_generation": fence,
        "expected_command_version": command_version,
        "candidate": normalized.descriptor(),
    }
    request_hash = catalog._request_hash(
        actor, catalog.PAGE_LIFECYCLE_FINALIZED_ACTION, request
    )
    replayed, replay = store._replay_from_ledger(
        actor_subject_id=actor,
        operation_id=op_id,
        action=catalog.PAGE_LIFECYCLE_FINALIZED_ACTION,
        request_hash=request_hash,
        result_validator=lambda con, row, result: (
            catalog._validate_page_lifecycle_finalized_commitment_on_connection(
                con,
                row,
                result,
                actor_subject_id=actor,
                operation_id=op_id,
                workspace_id=ws_id,
            )
        ),
    )
    if replayed:
        return replay

    def require_current(con: sqlite3.Connection) -> tuple[
        sqlite3.Row, sqlite3.Row, sqlite3.Row, dict[str, dict[str, Any]]
    ]:
        store._require_workspace_admin(con, ws_id, actor)
        command_current = store._page_lifecycle_row(
            con,
            workspace_id=ws_id,
            actor_subject_id=actor,
            operation_id=op_id,
            command_id=command,
        )
        now_check = store._healing_now()
        if (
            str(command_current["action"]) != command_action
            or str(command_current["canonical_lifecycle_action"])
            != catalog.PAGE_LIFECYCLE_SUBTREE_ACTION_MAP[command_action]
            or str(command_current["status"]) != "source_applied"
            or str(command_current["source_owner_subject_id"]) != owner
            or str(command_current["source_page_id"]) != receipt.root_source_page_id
            or str(command_current["source_receipt_sha256"])
            != receipt.source_operation_receipt_sha256
            or int(command_current["affected_node_count"]) != transitioned_count
            or int(command_current["file_work_count"]) != file_mapping_count
            or int(command_current["row_version"]) != command_version
            or int(command_current["fencing_generation"]) != fence
            or str(command_current["lease_worker"] or "") != worker_id
            or str(command_current["lease_token_sha256"] or "") != token_sha256
            or int(command_current["lease_expires_at"] or 0) <= now_check
        ):
            raise catalog.WorkspacePageLifecycleLeaseConflict(
                "page lifecycle command lease unavailable"
            )
        workspace = store._assert_version(
            con, ws_id, int(command_current["expected_workspace_version"])
        )
        if int(workspace["security_epoch"]) != int(
            command_current["expected_workspace_security_epoch"]
        ):
            raise catalog.WorkspacePageTreeSourceFenceConflict(
                "workspace security generation changed"
            )
        state = con.execute(
            "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
            (ws_id,),
        ).fetchone()
        if (
            state is None
            or str(state["materialization_state"]) != "complete"
            or int(state["tree_version"])
            != int(command_current["expected_tree_version"])
            or int(state["row_version"])
            != int(command_current["expected_tree_state_version"])
        ):
            raise catalog.WorkspacePageTreeSourceFenceConflict(
                "workspace page tree generation changed"
            )

        current: dict[str, dict[str, Any]] = {}
        for effect in receipt.effects:
            page = pages[effect.result_page_id]
            if command_action == "duplicate" and effect.transitioned:
                if con.execute(
                    "SELECT 1 FROM workspace_page_sources WHERE workspace_id=? "
                    "AND source_owner_subject_id=? AND source_page_id=?",
                    (ws_id, owner, effect.result_page_id),
                ).fetchone() or con.execute(
                    "SELECT 1 FROM workspace_page_tree_nodes WHERE workspace_id=? "
                    "AND source_owner_subject_id=? AND source_page_id=?",
                    (ws_id, owner, effect.result_page_id),
                ).fetchone():
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "duplicate lifecycle destination already exists"
                    )
                source_binding = con.execute(
                    "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
                    "AND source_owner_subject_id=? AND source_page_id=?",
                    (ws_id, owner, effect.source_page_id),
                ).fetchone()
                source_attestation = con.execute(
                    "SELECT * FROM workspace_page_source_incarnation_attestations "
                    "WHERE workspace_id=? AND source_owner_subject_id=? "
                    "AND source_page_id=? AND binding_version=?",
                    (
                        ws_id,
                        owner,
                        effect.source_page_id,
                        int(source_binding["row_version"]) if source_binding else 0,
                    ),
                ).fetchone()
                if (
                    source_binding is None
                    or source_attestation is None
                    or str(source_binding["state"]) != "active"
                    or int(source_binding["source_revision"]) != effect.source_revision
                    or str(source_attestation["source_incarnation_id"])
                    != effect.source_incarnation_id
                    or int(source_attestation["source_incarnation_generation"])
                    != effect.source_incarnation_generation
                    or int(source_attestation["source_change_generation"])
                    != effect.source_change_generation
                ):
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "duplicate lifecycle source changed"
                    )
                before = {
                    "binding": None,
                    "node": None,
                    "attestation": source_attestation,
                    "before_binding_version": 0,
                    "before_source_revision": int(source_binding["source_revision"]),
                    "before_source_updated_at": int(source_binding["source_updated_at"]),
                    "before_page_sha256": str(source_binding["source_sha256"]),
                    "before_source_receipt_sha256": str(
                        source_attestation["source_receipt_sha256"]
                    ),
                    "after_binding_version": 1,
                    "source_binding": source_binding,
                }
            else:
                binding = con.execute(
                    "SELECT * FROM workspace_page_sources WHERE workspace_id=? "
                    "AND source_owner_subject_id=? AND source_page_id=?",
                    (ws_id, owner, effect.result_page_id),
                ).fetchone()
                node = con.execute(
                    "SELECT * FROM workspace_page_tree_nodes WHERE workspace_id=? "
                    "AND source_owner_subject_id=? AND source_page_id=?",
                    (ws_id, owner, effect.result_page_id),
                ).fetchone()
                if binding is None or node is None:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle catalog page is unavailable"
                    )
                before_binding = int(binding["row_version"])
                if (
                    int(node["binding_version"]) != before_binding
                    or int(node["source_incarnation_generation"])
                    != effect.source_incarnation_generation
                ):
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle catalog generation changed"
                    )
                if command_action == "restore" and effect.transitioned:
                    prior = con.execute(
                        """
                        SELECT e.* FROM workspace_page_lifecycle_subtree_effects e
                        JOIN workspace_page_lifecycle_subtree_manifests m
                          ON m.command_id=e.command_id
                        WHERE e.workspace_id=? AND e.source_owner_subject_id=?
                          AND e.result_page_id=? AND m.canonical_action='trash'
                          AND e.transitioned=1 AND e.after_binding_version=?
                        """,
                        (ws_id, owner, effect.result_page_id, before_binding),
                    ).fetchall()
                    if (
                        str(binding["state"]) != "quarantined"
                        or str(binding["quarantine_reason"] or "")
                        != "source_archived"
                        or str(node["lifecycle_state"]) != "archived"
                        or str(node["canonical_lifecycle_state"]) != "trashed"
                        or len(prior) != 1
                        or int(prior[0]["result_revision"]) != effect.source_revision
                        or str(prior[0]["result_incarnation_id"])
                        != effect.source_incarnation_id
                        or int(prior[0]["result_change_generation"])
                        != effect.source_change_generation
                    ):
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "restore lifecycle source changed"
                        )
                    before_revision = int(prior[0]["after_source_revision"])
                    before_updated = int(prior[0]["after_source_updated_at"])
                    before_sha = str(prior[0]["after_page_sha256"])
                    before_receipt = str(prior[0]["after_source_receipt_sha256"])
                else:
                    attestation = con.execute(
                        "SELECT * FROM workspace_page_source_incarnation_attestations "
                        "WHERE workspace_id=? AND source_owner_subject_id=? "
                        "AND source_page_id=? AND binding_version=?",
                        (ws_id, owner, effect.result_page_id, before_binding),
                    ).fetchone()
                    if (
                        str(binding["state"]) != "active"
                        or str(node["lifecycle_state"]) != "active"
                        or attestation is None
                        or int(binding["source_revision"]) != effect.source_revision
                        or str(attestation["source_incarnation_id"])
                        != effect.source_incarnation_id
                        or int(attestation["source_change_generation"])
                        != effect.source_change_generation
                    ):
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "lifecycle active source changed"
                        )
                    before_revision = int(binding["source_revision"])
                    before_updated = int(binding["source_updated_at"])
                    before_sha = str(binding["source_sha256"])
                    before_receipt = str(attestation["source_receipt_sha256"])
                before = {
                    "binding": binding,
                    "node": node,
                    "before_binding_version": before_binding,
                    "before_source_revision": before_revision,
                    "before_source_updated_at": before_updated,
                    "before_page_sha256": before_sha,
                    "before_source_receipt_sha256": before_receipt,
                    "after_binding_version": before_binding + 1,
                    "source_binding": binding,
                }
            if page.locator_projection is not None and (
                page.locator_projection.binding_version
                != before["after_binding_version"]
            ):
                raise catalog.WorkspacePageLifecycleSourceConflict(
                    "lifecycle locator binding generation changed"
                )
            current[effect.result_page_id] = before
        return command_current, workspace, state, current

    preflight_con = store._read_connection()
    try:
        require_current(preflight_con)
    finally:
        preflight_con.close()
    try:
        revalidated = source_revalidator(normalized)
    except Exception as exc:
        raise catalog.WorkspacePageLifecycleSourceConflict(
            "page lifecycle source evidence changed"
        ) from exc
    if revalidated is not True:
        raise catalog.WorkspacePageLifecycleSourceConflict(
            "page lifecycle source evidence changed"
        )

    authorization = store._authorization("workspace.admin", ws_id)

    def handler(con: sqlite3.Connection) -> catalog._MutationEffect:
        command_current, workspace_before, state_before, current = require_current(con)
        now = store._healing_now()
        order_generation = int(state_before["tree_version"]) + 1
        canonical_action = catalog.PAGE_LIFECYCLE_SUBTREE_ACTION_MAP[command_action]
        con.create_function(
            "workspace_page_lifecycle_subtree_command_id", 0, lambda: command
        )
        con.create_function(
            "workspace_page_lifecycle_subtree_source_receipt_sha256",
            0,
            lambda: receipt.source_operation_receipt_sha256,
        )
        store._set_page_tree_context(
            con,
            actor_subject_id=actor,
            operation_id=op_id,
            action=command_action,
            now=now,
        )
        con.create_function(
            "workspace_incarnation_attestation_actor", 0, lambda: actor
        )
        con.create_function(
            "workspace_incarnation_attestation_operation_id", 0, lambda: op_id
        )
        try:
            con.execute(
                """
                INSERT INTO workspace_page_lifecycle_subtree_manifests(
                  command_id,workspace_id,canonical_action,source_owner_subject_id,
                  root_source_page_id,result_root_source_page_id,
                  source_request_sha256,source_operation_receipt_sha256,
                  post_manifest_sha256,file_manifest_sha256,source_device,
                  source_inode,source_link_count,source_epoch,full_sweep_generation,
                  trash_source_operation_id,placement_mode,effect_count,mapping_count,
                  file_mapping_count,aggregate_file_bytes,order_generation,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    command, ws_id, canonical_action, owner,
                    receipt.root_source_page_id, receipt.result_root_source_page_id,
                    receipt.source_request_sha256,
                    receipt.source_operation_receipt_sha256,
                    receipt.post_manifest_sha256, receipt.file_manifest_sha256,
                    catalog.sqlite_source_identity(receipt.source_device),
                    catalog.sqlite_source_identity(receipt.source_inode),
                    receipt.source_link_count, receipt.source_epoch,
                    receipt.full_sweep_generation, receipt.trash_source_operation_id,
                    receipt.placement_mode, len(receipt.effects), len(receipt.mappings),
                    file_mapping_count, aggregate_file_bytes, order_generation, now,
                ),
            )
            for ordinal, effect in enumerate(receipt.effects, start=1):
                before = current[effect.result_page_id]
                page = pages[effect.result_page_id]
                con.execute(
                    """
                    INSERT INTO workspace_page_lifecycle_subtree_effects(
                      command_id,effect_ordinal,workspace_id,source_owner_subject_id,
                      source_page_id,result_page_id,source_incarnation_id,
                      source_incarnation_generation,source_revision,
                      source_change_generation,result_incarnation_id,
                      result_incarnation_generation,result_revision,
                      result_change_generation,before_lifecycle,after_lifecycle,
                      before_parent_source_page_id,after_parent_source_page_id,
                      before_sibling_ordinal,after_sibling_ordinal,transitioned,
                      before_binding_version,after_binding_version,
                      before_source_revision,before_source_updated_at,before_page_sha256,
                      after_source_revision,after_source_updated_at,after_page_sha256,
                      before_source_receipt_sha256,after_source_receipt_sha256,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        command, ordinal, ws_id, owner, effect.source_page_id,
                        effect.result_page_id, effect.source_incarnation_id,
                        effect.source_incarnation_generation, effect.source_revision,
                        effect.source_change_generation, effect.result_incarnation_id,
                        effect.result_incarnation_generation, effect.result_revision,
                        effect.result_change_generation, effect.before_lifecycle,
                        effect.after_lifecycle, effect.before_parent_source_page_id,
                        effect.after_parent_source_page_id,
                        effect.before_sibling_ordinal, effect.after_sibling_ordinal,
                        int(effect.transitioned), before["before_binding_version"],
                        before["after_binding_version"],
                        before["before_source_revision"],
                        before["before_source_updated_at"],
                        before["before_page_sha256"], page.source_revision,
                        page.source_updated_at, page.page_sha256,
                        before["before_source_receipt_sha256"], page.receipt_sha256, now,
                    ),
                )
            for ordinal, mapping in enumerate(receipt.mappings, start=1):
                con.execute(
                    "INSERT INTO workspace_page_lifecycle_object_mappings "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        command, ordinal, mapping.kind, mapping.source_object_id,
                        mapping.result_object_id, mapping.source_page_id,
                        mapping.result_page_id, mapping.byte_size,
                        mapping.content_sha256, now,
                    ),
                )
            if fault_hook is not None:
                fault_hook("after_evidence")

            # Free all old locator uniqueness before publishing any new page.
            for effect in receipt.effects:
                before = current[effect.result_page_id]
                binding = before["binding"]
                if binding is not None and str(binding["state"]) == "active":
                    con.execute(
                        "DELETE FROM workspace_page_source_projections "
                        "WHERE workspace_id=? AND source_owner_subject_id=? "
                        "AND source_page_id=? AND binding_version=?",
                        (
                            ws_id, owner, effect.result_page_id,
                            before["before_binding_version"],
                        ),
                    )

            duplicate_pending: list[tuple[str, int]] = []
            for effect in receipt.effects:
                page = pages[effect.result_page_id]
                before = current[effect.result_page_id]
                next_version = int(before["after_binding_version"])
                con.create_function(
                    "workspace_page_lifecycle_subtree_source_generation",
                    0,
                    lambda generation=page.change_generation: generation,
                )
                if command_action == "duplicate" and effect.transitioned:
                    source_binding = before["source_binding"]
                    con.execute(
                        """
                        INSERT INTO workspace_page_sources(
                          workspace_id,source_owner_subject_id,source_page_id,teamspace_id,
                          placement,state,quarantined_at,source_revision,source_updated_at,
                          source_sha256,bound_by_subject_id,row_version,created_at,updated_at,
                          quarantine_reason,quarantine_source_generation
                        ) VALUES(?,?,?,?,?,'pending',NULL,?,?,?,?,?,?,?,NULL,NULL)
                        """,
                        (
                            ws_id, owner, page.page_id, source_binding["teamspace_id"],
                            str(source_binding["placement"]), page.source_revision,
                            page.source_updated_at, page.page_sha256, actor, next_version,
                            now, now,
                        ),
                    )
                    duplicate_pending.append((page.page_id, next_version))
                elif page.source_active:
                    updated = con.execute(
                        """
                        UPDATE workspace_page_sources
                        SET state='active',quarantined_at=NULL,quarantine_reason=NULL,
                            quarantine_source_generation=NULL,source_revision=?,
                            source_updated_at=?,source_sha256=?,row_version=?,updated_at=?
                        WHERE workspace_id=? AND source_owner_subject_id=?
                          AND source_page_id=? AND row_version=?
                        """,
                        (
                            page.source_revision, page.source_updated_at,
                            page.page_sha256, next_version, now, ws_id, owner,
                            page.page_id, before["before_binding_version"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "lifecycle binding generation changed"
                        )
                else:
                    updated = con.execute(
                        """
                        UPDATE workspace_page_sources
                        SET state='quarantined',quarantined_at=?,
                            quarantine_reason='source_archived',
                            quarantine_source_generation=?,row_version=?,updated_at=?
                        WHERE workspace_id=? AND source_owner_subject_id=?
                          AND source_page_id=? AND state='active' AND row_version=?
                        """,
                        (
                            now, page.change_generation, next_version, now, ws_id,
                            owner, page.page_id, before["before_binding_version"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "lifecycle binding generation changed"
                        )

            for page_id, version in duplicate_pending:
                _insert_attestation(
                    con, actor=actor, operation_id=op_id, workspace_id=ws_id,
                    owner=owner, binding_version=version, page=pages[page_id], now=now,
                )
                activated = con.execute(
                    "UPDATE workspace_page_sources SET state='active',updated_at=? "
                    "WHERE workspace_id=? AND source_owner_subject_id=? "
                    "AND source_page_id=? AND state='pending' AND row_version=?",
                    (now, ws_id, owner, page_id, version),
                )
                if activated.rowcount != 1:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "duplicate lifecycle activation changed"
                    )
            for effect in receipt.effects:
                page = pages[effect.result_page_id]
                if page.source_active and not (
                    command_action == "duplicate" and effect.transitioned
                ):
                    _insert_attestation(
                        con, actor=actor, operation_id=op_id, workspace_id=ws_id,
                        owner=owner,
                        binding_version=current[page.page_id]["after_binding_version"],
                        page=page, now=now,
                    )
            for effect in receipt.effects:
                page = pages[effect.result_page_id]
                if page.source_active:
                    _insert_projection(
                        con, workspace_id=ws_id, owner=owner,
                        binding_version=current[page.page_id]["after_binding_version"],
                        page=page, now=now,
                    )
            if fault_hook is not None:
                fault_hook("after_bindings")

            active_delta = sum(
                1 if item.transitioned and item.after_lifecycle == "active" else -1
                if item.transitioned and item.after_lifecycle == "trashed" else 0
                for item in receipt.effects
            )
            archived_delta = sum(
                1 if item.transitioned and item.after_lifecycle == "trashed" else -1
                if (
                    item.transitioned
                    and item.before_lifecycle == "trashed"
                    and item.after_lifecycle == "active"
                )
                else 0
                for item in receipt.effects
            )
            source_fence_set_sha256 = _current_tree_source_fence_set_sha256(
                con,
                workspace_id=ws_id,
                current_owner=owner,
                current_receipt=receipt,
            )
            state_updated = con.execute(
                """
                UPDATE workspace_page_tree_states
                SET tree_version=?,materialization_state='complete',
                    source_fence_set_sha256=?,
                    active_node_count=active_node_count+?,
                    archived_node_count=archived_node_count+?,
                    row_version=row_version+1,last_actor_subject_id=?,
                    last_operation_id=?,updated_at=?
                WHERE workspace_id=? AND tree_version=? AND row_version=?
                  AND materialization_state='complete'
                """,
                (
                    order_generation, source_fence_set_sha256, active_delta,
                    archived_delta, actor, op_id, now, ws_id,
                    int(state_before["tree_version"]),
                    int(state_before["row_version"]),
                ),
            )
            if state_updated.rowcount != 1:
                raise catalog.WorkspacePageTreeSourceFenceConflict(
                    "lifecycle tree state changed"
                )

            def desired_depth(effect) -> int:
                parent = effect.after_parent_source_page_id
                if not parent:
                    return 0
                parent_row = con.execute(
                    "SELECT depth,lifecycle_state FROM workspace_page_tree_nodes "
                    "WHERE workspace_id=? AND source_owner_subject_id=? "
                    "AND source_page_id=?",
                    (ws_id, owner, parent),
                ).fetchone()
                if parent_row is None or str(parent_row["lifecycle_state"]) != "active":
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle destination parent changed"
                    )
                return int(parent_row["depth"]) + 1

            def update_existing(effect) -> None:
                before = current[effect.result_page_id]
                old_node = before["node"]
                if old_node is None:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle tree page is unavailable"
                    )
                page = pages[effect.result_page_id]
                if page.source_active:
                    parent = effect.after_parent_source_page_id
                    parent_resolution = "resolved" if parent else "root"
                    parent_owner = owner if parent else None
                    parent_page = parent or None
                    sibling = effect.after_sibling_ordinal
                    depth = desired_depth(effect)
                    lifecycle = "active"
                    canonical_state = (
                        "active" if effect.transitioned and command_action == "restore"
                        else str(old_node["canonical_lifecycle_state"])
                    )
                else:
                    parent_resolution = str(old_node["parent_resolution"])
                    parent_owner = old_node["parent_source_owner_subject_id"]
                    parent_page = old_node["parent_source_page_id"]
                    sibling = int(old_node["sibling_ordinal"])
                    depth = int(old_node["depth"])
                    lifecycle = "archived"
                    canonical_state = "trashed"
                if sibling is None:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle sibling order changed"
                    )
                structure = catalog._page_tree_node_structure_sha256(
                    workspace_id=ws_id,
                    source_owner_subject_id=owner,
                    source_page_id=page.page_id,
                    binding_version=before["after_binding_version"],
                    source_incarnation_id=page.incarnation_id,
                    source_incarnation_generation=page.incarnation_generation,
                    source_change_generation=page.change_generation,
                    source_receipt_sha256=page.receipt_sha256,
                    parent_source_owner_subject_id=parent_owner or "",
                    parent_source_page_id=parent_page or "",
                    sibling_ordinal=int(sibling),
                    depth=depth,
                )
                updated = con.execute(
                    """
                    UPDATE workspace_page_tree_nodes
                    SET binding_version=?,source_incarnation_id=?,
                        source_incarnation_generation=?,source_change_generation=?,
                        source_receipt_sha256=?,parent_resolution=?,
                        parent_source_owner_subject_id=?,parent_source_page_id=?,
                        sibling_ordinal=?,depth=?,lifecycle_state=?,structure_sha256=?,
                        row_version=row_version+1,last_actor_subject_id=?,
                        last_operation_id=?,updated_at=?,order_generation=?,
                        canonical_lifecycle_state=?
                    WHERE workspace_id=? AND source_owner_subject_id=?
                      AND source_page_id=? AND row_version=?
                    """,
                    (
                        before["after_binding_version"], page.incarnation_id,
                        page.incarnation_generation, page.change_generation,
                        page.receipt_sha256, parent_resolution, parent_owner, parent_page,
                        sibling, depth, lifecycle, structure, actor, op_id, now,
                        order_generation, canonical_state, ws_id, owner, page.page_id,
                        int(old_node["row_version"]),
                    ),
                )
                if updated.rowcount != 1:
                    raise catalog.WorkspacePageTreeSourceFenceConflict(
                        "lifecycle tree generation changed"
                    )

            transitioned = [item for item in receipt.effects if item.transitioned]
            unchanged = [item for item in receipt.effects if not item.transitioned]
            if command_action == "archive":
                for effect in sorted(
                    transitioned,
                    key=lambda item: int(current[item.result_page_id]["node"]["depth"]),
                ):
                    update_existing(effect)
            ordered_unchanged = sorted(
                unchanged,
                key=lambda item: (
                    0 if (item.after_sibling_ordinal or 0) < (item.before_sibling_ordinal or 0)
                    else 1,
                    (item.before_sibling_ordinal or 0)
                    if (item.after_sibling_ordinal or 0) < (item.before_sibling_ordinal or 0)
                    else -(item.before_sibling_ordinal or 0),
                ),
            )
            for effect in ordered_unchanged:
                update_existing(effect)

            if command_action == "restore":
                pending = list(transitioned)
                while pending:
                    progressed = False
                    for effect in list(pending):
                        parent = effect.after_parent_source_page_id
                        if parent and any(
                            item.result_page_id == parent for item in pending
                        ):
                            continue
                        update_existing(effect)
                        pending.remove(effect)
                        progressed = True
                    if not progressed:
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "restore lifecycle subtree is cyclic"
                        )

            if command_action == "duplicate":
                pending = list(transitioned)
                while pending:
                    progressed = False
                    for effect in list(pending):
                        parent = effect.after_parent_source_page_id
                        if parent and any(
                            item.result_page_id == parent for item in pending
                        ):
                            continue
                        page = pages[effect.result_page_id]
                        before = current[effect.result_page_id]
                        depth = desired_depth(effect)
                        parent_resolution = "resolved" if parent else "root"
                        structure = catalog._page_tree_node_structure_sha256(
                            workspace_id=ws_id,
                            source_owner_subject_id=owner,
                            source_page_id=page.page_id,
                            binding_version=before["after_binding_version"],
                            source_incarnation_id=page.incarnation_id,
                            source_incarnation_generation=page.incarnation_generation,
                            source_change_generation=page.change_generation,
                            source_receipt_sha256=page.receipt_sha256,
                            parent_source_owner_subject_id=owner if parent else "",
                            parent_source_page_id=parent,
                            sibling_ordinal=int(effect.after_sibling_ordinal),
                            depth=depth,
                        )
                        con.execute(
                            """
                            INSERT INTO workspace_page_tree_nodes(
                              workspace_id,source_owner_subject_id,source_page_id,
                              binding_version,source_incarnation_id,
                              source_incarnation_generation,source_change_generation,
                              source_receipt_sha256,parent_resolution,
                              parent_source_owner_subject_id,parent_source_page_id,
                              sibling_ordinal,depth,lifecycle_state,structure_sha256,
                              row_version,last_actor_subject_id,last_operation_id,
                              created_at,updated_at,order_generation,
                              canonical_lifecycle_state
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                ws_id, owner, page.page_id,
                                before["after_binding_version"], page.incarnation_id,
                                page.incarnation_generation, page.change_generation,
                                page.receipt_sha256, parent_resolution,
                                owner if parent else None, parent or None,
                                effect.after_sibling_ordinal, depth, "active", structure,
                                1, actor, op_id, now, now, order_generation, "active",
                            ),
                        )
                        pending.remove(effect)
                        progressed = True
                    if not progressed:
                        raise catalog.WorkspacePageTreeSourceFenceConflict(
                            "duplicate lifecycle subtree is cyclic"
                        )
            if fault_hook is not None:
                fault_hook("after_tree")

            workspace_after = store._bump_workspace(
                con, ws_id, security_change=True
            )
            finalized = con.execute(
                """
                UPDATE workspace_page_lifecycle_commands
                SET status='finalized',lease_worker=NULL,lease_token_sha256=NULL,
                    lease_expires_at=NULL,row_version=row_version+1,
                    failure_code=NULL,updated_at=?,terminal_at=?
                WHERE id=? AND status='source_applied' AND row_version=?
                  AND fencing_generation=?
                """,
                (now, now, command, command_version, fence),
            )
            if finalized.rowcount != 1:
                raise catalog.WorkspacePageLifecycleLeaseConflict(
                    "page lifecycle command finalization changed"
                )
            final_row = store._page_lifecycle_row(
                con,
                workspace_id=ws_id,
                actor_subject_id=actor,
                operation_id=op_id,
                command_id=command,
            )
            state_after = con.execute(
                "SELECT * FROM workspace_page_tree_states WHERE workspace_id=?",
                (ws_id,),
            ).fetchone()
            result = catalog._normalize_page_lifecycle_subtree_finalized_result({
                "contract": catalog.PAGE_LIFECYCLE_SUBTREE_RESULT_CONTRACT,
                "command_id": command,
                "action": command_action,
                "canonical_action": canonical_action,
                "status": "finalized",
                "workspace_id": ws_id,
                "source_owner_subject_id": owner,
                "root_source_page_id": receipt.root_source_page_id,
                "result_root_source_page_id": receipt.result_root_source_page_id,
                "catalog_request_sha256": str(final_row["request_sha256"]),
                "source_request_sha256": receipt.source_request_sha256,
                "source_operation_receipt_sha256": (
                    receipt.source_operation_receipt_sha256
                ),
                "post_manifest_sha256": receipt.post_manifest_sha256,
                "file_manifest_sha256": receipt.file_manifest_sha256,
                "effect_count": len(receipt.effects),
                "transitioned_count": transitioned_count,
                "mapping_count": len(receipt.mappings),
                "file_mapping_count": file_mapping_count,
                "aggregate_file_bytes": aggregate_file_bytes,
                "order_generation": order_generation,
                "fencing_generation": fence,
                "command_version": int(final_row["row_version"]),
                "workspace_version": int(workspace_after["row_version"]),
                "security_epoch": int(workspace_after["security_epoch"]),
                "tree_version": int(state_after["tree_version"]),
                "previous_tree_state_version": int(state_before["row_version"]),
                "tree_state_version": int(state_after["row_version"]),
                "active_node_count": int(state_after["active_node_count"]),
                "archived_node_count": int(state_after["archived_node_count"]),
                "changed": True,
                "full_sweep_required": True,
            })
            return catalog._MutationEffect(
                result=result,
                workspace_id=ws_id,
                target_type="workspace_page_lifecycle_command",
                target_id=command,
                event_payload=result,
                authorization=authorization,
            )
        finally:
            con.create_function(
                "workspace_page_lifecycle_subtree_command_id", 0, lambda: ""
            )
            con.create_function(
                "workspace_page_lifecycle_subtree_source_receipt_sha256",
                0,
                lambda: "",
            )
            con.create_function(
                "workspace_page_lifecycle_subtree_source_generation", 0, lambda: 0
            )
            con.create_function(
                "workspace_incarnation_attestation_actor", 0, lambda: ""
            )
            con.create_function(
                "workspace_incarnation_attestation_operation_id", 0, lambda: ""
            )

    return store._mutate(
        actor_subject_id=actor,
        operation_id=op_id,
        action=catalog.PAGE_LIFECYCLE_FINALIZED_ACTION,
        request=request,
        handler=handler,
        replay_authorizer=lambda con, _row: store._require_workspace_admin(
            con, ws_id, actor
        ),
        replay_result_validator=lambda con, row, result: (
            catalog._validate_page_lifecycle_finalized_commitment_on_connection(
                con,
                row,
                result,
                actor_subject_id=actor,
                operation_id=op_id,
                workspace_id=ws_id,
            )
        ),
    )


__all__ = [
    "WorkspacePageLifecycleSubtreeCandidate",
    "WorkspacePageLifecycleSubtreePageCandidate",
    "finalize_page_lifecycle_subtree_catalog",
    "normalize_subtree_candidate",
]
