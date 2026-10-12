"""Locator-backed NP-2A page reads with no per-user directory scan fallback."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any

from code_assist_v1 import notion_store
from code_assist_v1.notion_workspace_store import (
    WorkspaceRevisionConflict,
    WorkspaceStore,
)


def _page_create_operation_id(kind: str, operation_id: str) -> str:
    digest = hashlib.sha256(
        f"code_assist.notion.workspace_page_create.v1\0{kind}\0{operation_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"np2b-page-create-{kind}-{digest[:32]}"


def _page_lifecycle_operation_id(kind: str, operation_id: str) -> str:
    digest = hashlib.sha256(
        f"code_assist.notion.workspace_page_lifecycle.v1\0{kind}\0{operation_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"np2b-page-lifecycle-{kind}-{digest[:32]}"


def _owner_workspace(
    store: WorkspaceStore, requester_subject_id: str, workspace_id: str
) -> dict[str, Any]:
    actor = str(requester_subject_id or "").strip()
    workspace = store.get_workspace(actor, workspace_id)
    if str(workspace.get("access", {}).get("role") or "") != "owner":
        raise PermissionError("workspace owner permission required")
    return workspace


def _active_self_binding_map(
    store: WorkspaceStore, requester_subject_id: str, workspace_id: str
) -> dict[str, dict[str, Any]]:
    actor = str(requester_subject_id or "").strip()
    return {
        str(item["source_page_id"]): item
        for item in store.list_page_source_bindings(actor, workspace_id)
        if str(item["source_owner_subject_id"]) == actor
    }


def create_workspace_page(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    *,
    title: str,
    parent_id: str,
    base_version: int,
    operation_id: str,
) -> dict[str, Any]:
    """Create and bind one self-owned page behind the default-off UI gate.

    The source page ID is deterministic for the actor/workspace/operation tuple.
    This lets a response-loss retry discover a completed active binding before
    applying any new source effect.  Pending binding recovery uses the same
    immutable source proof and a bounded current-version activation retry.
    """

    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    actor = str(requester_subject_id or "").strip()
    parent_id = str(parent_id or "").strip()
    operation_id = str(operation_id or "").strip()
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise ValueError("base_version must be an integer")
    workspace = store.get_workspace(actor, workspace_id)
    if str(workspace.get("access", {}).get("role") or "") != "owner":
        raise PermissionError("workspace owner permission required")

    page_id = notion_store.page_id_for_operation(
        actor,
        namespace=workspace_id,
        operation_id=operation_id,
    )
    maintenance = store.get_page_source_binding_for_maintenance(
        actor,
        workspace_id,
        source_owner_subject_id=actor,
        source_page_id=page_id,
    )
    page_result: dict[str, Any] | None = None
    if maintenance is not None:
        page_result = notion_store.create_page_idempotent(
            actor,
            namespace=workspace_id,
            operation_id=operation_id,
            title=title,
            parent_id=parent_id,
        )
        if maintenance.binding_state == "active":
            binding = next(
                (
                    item
                    for item in store.list_page_source_bindings(actor, workspace_id)
                    if item["source_owner_subject_id"] == actor
                    and item["source_page_id"] == page_id
                ),
                None,
            )
            if binding is None:
                raise RuntimeError("active page binding is unavailable")
            return {
                "binding": binding,
                "page": page_result["page"],
                "replayed": True,
                "workspace": store.get_workspace(actor, workspace_id),
            }
        if maintenance.binding_state != "pending":
            raise ValueError("page create binding is not recoverable")
    else:
        if int(workspace["version"]) != base_version:
            raise WorkspaceRevisionConflict(int(workspace["version"]))
        if parent_id:
            parent = next(
                (
                    item
                    for item in store.list_page_source_bindings(actor, workspace_id)
                    if item["source_owner_subject_id"] == actor
                    and item["source_page_id"] == parent_id
                ),
                None,
            )
            if parent is None:
                raise FileNotFoundError("parent page not found or permission denied")
        page_result = notion_store.create_page_idempotent(
            actor,
            namespace=workspace_id,
            operation_id=operation_id,
            title=title,
            parent_id=parent_id,
        )

    assert page_result is not None
    proof = notion_store.capture_legacy_page_source_proof(actor, actor, page_id)
    if maintenance is None:
        stage = store.stage_page_source_binding(
            actor,
            workspace_id,
            proof,
            base_version=base_version,
            operation_id=_page_create_operation_id("stage", operation_id),
        )
        binding_version = int(stage["binding"]["version"])
        activation_base_version = int(stage["workspace"]["version"])
    else:
        binding_version = int(maintenance.binding_version)
        activation_base_version = int(
            store.get_workspace(actor, workspace_id)["version"]
        )

    activation_operation_id = _page_create_operation_id("activate", operation_id)
    try:
        activated = store.activate_page_source_binding(
            actor,
            workspace_id,
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            expected_binding_version=binding_version,
            base_version=activation_base_version,
            operation_id=activation_operation_id,
        )
    except WorkspaceRevisionConflict:
        activated = store.activate_page_source_binding(
            actor,
            workspace_id,
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            expected_binding_version=binding_version,
            base_version=int(store.get_workspace(actor, workspace_id)["version"]),
            operation_id=activation_operation_id,
        )
    return {
        "binding": activated["binding"],
        "page": page_result["page"],
        "replayed": False,
        "workspace": activated["workspace"],
    }


def duplicate_workspace_page(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    *,
    base_version: int,
    operation_id: str,
) -> dict[str, Any]:
    """Duplicate and atomically expose one complete self-owned subtree."""

    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    actor = str(requester_subject_id or "").strip()
    source_page_id = str(source_page_id or "").strip()
    operation_id = str(operation_id or "").strip()
    if not source_page_id or len(source_page_id) > 160:
        raise ValueError("valid source_page_id required")
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise ValueError("base_version must be an integer")
    workspace = _owner_workspace(store, actor, workspace_id)
    source_scope = notion_store.self_owned_page_lifecycle_scope(
        actor, source_page_id
    )
    bindings = _active_self_binding_map(store, actor, workspace_id)
    if any(page_id not in bindings for page_id in source_scope["page_ids"]):
        raise FileNotFoundError("page subtree not found or permission denied")

    namespace = f"{workspace_id}:duplicate"
    replay = notion_store.get_duplicate_page_idempotent_replay(
        actor,
        source_page_id,
        namespace=namespace,
        operation_id=operation_id,
    )
    if replay is None and int(workspace["version"]) != base_version:
        raise WorkspaceRevisionConflict(int(workspace["version"]))
    duplicate = replay or notion_store.duplicate_page_idempotent(
        actor,
        source_page_id,
        namespace=namespace,
        operation_id=operation_id,
    )
    page_ids = [str(page_id) for page_id in duplicate["page_ids"]]
    if not page_ids or len(page_ids) > 128:
        raise ValueError("duplicate subtree result is invalid")
    batch_operation_id = _page_lifecycle_operation_id("duplicate-bind", operation_id)
    batch_replay = store.get_page_sources_batch_replay(
        actor,
        workspace_id,
        page_ids,
        operation_id=batch_operation_id,
    )
    if batch_replay is not None:
        return {
            "bindings": batch_replay["bindings"],
            "page": duplicate["page"],
            "replayed": True,
            "subtree_count": len(page_ids),
            "workspace": store.get_workspace(actor, workspace_id),
        }
    proofs = [
        notion_store.capture_legacy_page_source_proof(actor, actor, page_id)
        for page_id in page_ids
    ]
    effective_version = int(store.get_workspace(actor, workspace_id)["version"])
    try:
        bound = store.bind_page_sources_batch(
            actor,
            workspace_id,
            proofs,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=effective_version,
            operation_id=batch_operation_id,
        )
    except WorkspaceRevisionConflict:
        _owner_workspace(store, actor, workspace_id)
        bound = store.bind_page_sources_batch(
            actor,
            workspace_id,
            proofs,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=int(store.get_workspace(actor, workspace_id)["version"]),
            operation_id=batch_operation_id,
        )
    return {
        "bindings": bound["bindings"],
        "page": duplicate["page"],
        "replayed": False,
        "subtree_count": len(page_ids),
        "workspace": bound["workspace"],
    }


def archive_workspace_page(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    *,
    base_version: int,
    operation_id: str,
) -> dict[str, Any]:
    """Archive only a complete self-owned subtree already bound to a workspace."""

    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    actor = str(requester_subject_id or "").strip()
    source_page_id = str(source_page_id or "").strip()
    if not source_page_id or len(source_page_id) > 160:
        raise ValueError("valid source_page_id required")
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise ValueError("base_version must be an integer")
    workspace = _owner_workspace(store, actor, workspace_id)
    bindings = _active_self_binding_map(store, actor, workspace_id)
    if source_page_id not in bindings:
        raise FileNotFoundError("page not found or permission denied")
    namespace = f"{workspace_id}:archive"
    replay = notion_store.get_page_lifecycle_idempotent_replay(
        actor,
        source_page_id,
        action="archive",
        namespace=namespace,
        operation_id=operation_id,
    )
    if replay is not None:
        if any(page_id not in bindings for page_id in replay["page_ids"]):
            raise FileNotFoundError("page subtree not found or permission denied")
        return {
            "archived_count": len(replay["page_ids"]),
            "page_id": source_page_id,
            "replayed": True,
            "workspace": store.get_workspace(actor, workspace_id),
        }
    if int(workspace["version"]) != base_version:
        raise WorkspaceRevisionConflict(int(workspace["version"]))
    scope = notion_store.self_owned_page_lifecycle_scope(actor, source_page_id)
    if any(page_id not in bindings for page_id in scope["page_ids"]):
        raise FileNotFoundError("page subtree not found or permission denied")
    archived = notion_store.archive_page_idempotent(
        actor,
        source_page_id,
        namespace=namespace,
        operation_id=operation_id,
    )
    return {
        "archived_count": int(archived["archived_count"]),
        "page_id": source_page_id,
        "replayed": bool(archived["replayed"]),
        "workspace": store.get_workspace(actor, workspace_id),
    }


def list_workspace_trash(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    *,
    query: str = "",
) -> dict[str, Any]:
    """List archived pages only through exact active workspace locators."""

    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    actor = str(requester_subject_id or "").strip()
    workspace = _owner_workspace(store, actor, workspace_id)
    binding_ids = sorted(_active_self_binding_map(store, actor, workspace_id))
    items = notion_store.list_archived_pages_from_owner_by_ids(
        actor,
        actor,
        binding_ids,
        query=query,
    )
    return {
        "workspace_id": workspace_id,
        "membership_epoch": int(workspace["access"]["epoch"]),
        "items": items,
        "count": len(items),
    }


def restore_workspace_page(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    source_page_id: str,
    *,
    base_version: int,
    operation_id: str,
) -> dict[str, Any]:
    """Restore only an exact archived subtree retained by workspace locators."""

    if not isinstance(store, WorkspaceStore):
        raise ValueError("WorkspaceStore required")
    actor = str(requester_subject_id or "").strip()
    source_page_id = str(source_page_id or "").strip()
    if not source_page_id or len(source_page_id) > 160:
        raise ValueError("valid source_page_id required")
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise ValueError("base_version must be an integer")
    workspace = _owner_workspace(store, actor, workspace_id)
    bindings = _active_self_binding_map(store, actor, workspace_id)
    if source_page_id not in bindings:
        raise FileNotFoundError("archived page not found or permission denied")
    namespace = f"{workspace_id}:restore"
    replay = notion_store.get_page_lifecycle_idempotent_replay(
        actor,
        source_page_id,
        action="restore",
        namespace=namespace,
        operation_id=operation_id,
    )
    if replay is not None:
        if any(page_id not in bindings for page_id in replay["page_ids"]):
            raise FileNotFoundError("page subtree not found or permission denied")
        return {
            "page": notion_store.get_page(actor, source_page_id)["page"],
            "restored_count": len(replay["page_ids"]),
            "replayed": True,
            "workspace": store.get_workspace(actor, workspace_id),
        }
    if int(workspace["version"]) != base_version:
        raise WorkspaceRevisionConflict(int(workspace["version"]))
    scope = notion_store.self_owned_page_lifecycle_scope(
        actor,
        source_page_id,
        include_archived=True,
        include_ancestors=True,
    )
    if not scope["archived"]:
        raise FileNotFoundError("archived page not found")
    if any(page_id not in bindings for page_id in scope["page_ids"]):
        raise FileNotFoundError("page subtree not found or permission denied")
    restored = notion_store.restore_page_idempotent(
        actor,
        source_page_id,
        namespace=namespace,
        operation_id=operation_id,
    )
    return {
        "page": restored["page"],
        "restored_count": len(restored["page_ids"]),
        "replayed": bool(restored["replayed"]),
        "workspace": store.get_workspace(actor, workspace_id),
    }


def list_workspace_pages(
    store: WorkspaceStore,
    requester_subject_id: str,
    workspace_id: str,
    *,
    query: str = "",
) -> dict[str, Any]:
    """Read bound page summaries from the active content authority.

    Collaborative pages use current canonical permissions and metadata. Normal
    pages retain the existing locator plus legacy-owner authorization. Neither
    mode enumerates user directories or writes compatibility projections.
    """
    workspace = store.get_workspace(requester_subject_id, workspace_id)
    bindings = store.list_page_source_bindings(requester_subject_id, workspace_id)
    from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingAuthority
    from code_assist_v1.notion_collaboration.page_appearance import project_page_metadata

    authority = getattr(store, "iam", None)
    collaborative = isinstance(authority, CollaborativeEditingAuthority)
    bindings_by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        bindings_by_owner[str(binding["source_owner_subject_id"])].append(binding)

    summaries_by_locator: dict[tuple[str, str], dict[str, Any]] = {}
    degraded_sources: set[str] = set()
    for owner, owner_bindings in sorted(bindings_by_owner.items()):
        requested_ids = {str(item["source_page_id"]) for item in owner_bindings}
        summaries = ([{"id": page_id} for page_id in requested_ids] if collaborative else
                     notion_store.list_pages_from_owner_by_ids(requester_subject_id, owner, requested_ids))
        for summary in summaries:
            summaries_by_locator[(owner, str(summary.get("id") or ""))] = summary
        if len(summaries) != len(requested_ids):
            degraded_sources.add("legacy_page_binding")

    items: list[dict[str, Any]] = []
    for binding in bindings:
        owner = str(binding["source_owner_subject_id"])
        page_id = str(binding["source_page_id"])
        summary = summaries_by_locator.get((owner, page_id))
        if not summary:
            continue
        item = dict(summary)
        item["workspace_id"] = workspace_id
        item["placement"] = {
            "scope": str(binding["placement"]),
            "teamspace_id": str(binding.get("teamspace_id") or ""),
            "binding_version": int(binding["version"]),
        }
        items.append(item)

    if collaborative:
        from code_assist_v1.notion_collaboration.page_creation import native_page_items
        by_id = {item["id"]: item for item in items}
        for item in native_page_items(authority, requester_subject_id, workspace_id):
            by_id.setdefault(item["id"], item)
        items = list(by_id.values())
        projected = project_page_metadata(authority, requester_subject_id, workspace_id, items)
        if len(projected) != len(items):
            degraded_sources.add("canonical_page_metadata")
        items = projected
        # A bound legacy document can be moved beneath a native team root.
        # Its current canonical ancestors own placement there; its original
        # source locator remains provenance and must not be fabricated anew.
        from code_assist_v1.notion_collaboration import native_team_location
        with authority._reader() as con:
            con.execute("BEGIN")
            for item in items:
                root = native_team_location.inherited_root(con, workspace_id, item["id"])
                if root is not None:
                    item["placement"] = {"scope":"teamspace", "teamspace_id":root["teamspace_id"],
                                         "binding_version":item.get("placement", {}).get("binding_version", 1)}

    visible_ids = {str(item.get("id") or "") for item in items}
    for item in items:
        if str(item.get("parent_id") or "") not in visible_ids:
            item["parent_id"] = ""

    normalized_query = str(query or "").strip().casefold()
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query in str(item.get("title") or "").casefold()
        ]
    items.sort(
        key=lambda item: (
            str(item.get("placement", {}).get("scope") or ""),
            int(item.get("position") or 0),
            int(item.get("created_at") or 0) if collaborative else -int(item.get("updated_at") or 0),
            str(item.get("id") or ""),
        )
    )
    degraded = bool(degraded_sources)
    return {
        "workspace_id": workspace_id,
        "membership_epoch": int(workspace["access"]["epoch"]),
        "items": items,
        "count": len(items),
        "degraded": degraded,
        "degraded_sources": sorted(degraded_sources),
    }
