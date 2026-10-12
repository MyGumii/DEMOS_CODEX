"""NP-2A workspace/IAM routes registered only by the Notion host.

The legacy Notion and Code bridge registrars stay untouched.  These routes use
the injected Notion test/runtime data root to locate the independent canonical
workspace catalog and never scan per-user directories.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

from flask import Response, current_app, jsonify, request
from itsdangerous import URLSafeTimedSerializer
from werkzeug.exceptions import RequestEntityTooLarge

from code_assist_v1 import notion_http
from code_assist_v1 import notion_database_export_bundle
from code_assist_v1 import notion_core_ui_pilot
from code_assist_v1 import notion_store as legacy_notion_store
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1 import notion_workspace_pages
from code_assist_v1.notion_workspace_appearance import update_workspace_icon
from code_assist_v1 import notion_workspace_custom_emoji
from code_assist_v1.notion_teamspace_settings import read_teamspace, rename_teamspace
from code_assist_v1 import notion_workspace_images
from code_assist_v1.auth import current_user_id, list_public_users, safe_user_id
from code_assist_v1.notion_collaboration import iam_runtime
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance
from code_assist_v1.notion_collaboration.page_settings import (
    TEMPORARY_UNLOCK_SALT, current_page_lock_generation, update_page_settings,
)
from code_assist_v1.notion_collaboration.page_activity import read_page_activity
from code_assist_v1.notion_collaboration.database_updates import (
    read_database_updates, read_database_updates_scoped,
)
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration import direct_database_favorites
from code_assist_v1.notion_collaboration.database_comments import (
    add_row_comment, eligible_mention_subject_ids, list_row_comments,
    mutate_row_comment,
)
from code_assist_v1.notion_collaboration.database_notifications import (
    list_direct_inbox, mark_direct_inbox_read,
)
from code_assist_v1.notion_collaboration.synced_blocks import (
    read_synced_block, SyncedShapeUnavailable, SyncedSourceChanged,
)
from code_assist_v1.notion_collaboration.synced_edit import edit_synced_text
from code_assist_v1.notion_collaboration.synced_detach_service import detach_synced_block, read_synced_detach_result
from code_assist_v1.notion_collaboration.synced_row_clipboard import insert_row_reference, read_row_reference_result, read_row_paste_target
from code_assist_v1.notion_collaboration import synced_legacy_adoption_service
from code_assist_v1.notion_collaboration.linked_databases import (
    read_linked_database, read_linked_database_scoped,
    list_linkable_databases, list_linkable_databases_scoped,
    read_linkable_database_source, read_linkable_database_source_scoped,
    execute_linked_cells, execute_linked_cells_scoped,
    execute_linked_row_create_scoped,
    execute_linked_template_create_scoped,
    execute_linked_template_update_scoped,
    insert_linked_database_scoped,
    LinkedDatabaseShapeUnavailable, LinkedDatabaseSourceChanged,
)
from code_assist_v1.notion_collaboration.page_creation import create_page as create_collaborative_page
from code_assist_v1.notion_collaboration.page_archive import set_page_archived, list_archived_pages, read_page_lifecycle_result
from code_assist_v1.notion_collaboration.page_move import move_page as move_collaborative_page
from code_assist_v1.notion_collaboration.page_copy import (
    CopyCancelled, copy_page, read_page_copy_result, cancel_page_copy,
    list_page_copy_results, register_page_copy_intent, list_page_copy_intents,
)
from code_assist_v1.notion_collaboration.database_copy import (
    copy_owned_database, read_owned_database_copy_result,
)
from code_assist_v1.notion_collaboration.database_move import (
    DatabaseMoveShapeUnavailable, move_owned_database, read_owned_database_move_result,
)
from code_assist_v1.notion_collaboration.database_row_move import (
    DatabaseRowMoveShapeUnavailable, move_owned_rows, read_owned_row_location,
)
from code_assist_v1.notion_collaboration.database_row_page_move import (
    list_page_database_destinations, move_owned_rows_to_page,
    read_page_return_target,
    return_page_to_owned_database,
)
from code_assist_v1.notion_collaboration.database_trash import (
    DatabaseTrashShapeUnavailable,
    set_owned_database_trashed, read_owned_database_trash_result,
    list_trashed_owned_databases, read_trashed_owned_database,
    export_trashed_owned_database,
)
from code_assist_v1.notion_collaboration.database_row_trash import (
    set_database_row_trashed, read_database_row_trash_result,
    list_trashed_database_rows, trash_database_rows_batch,
    restore_database_rows_batch,
    read_database_rows_batch_trash_result,
)
from code_assist_v1.notion_collaboration.page_copy_source import CopySourceChanged
from code_assist_v1.notion_collaboration.page_copy_remap import CopyShapeUnavailable
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    EpochConflict,
    NotFoundOrDenied,
    OperationConflict,
    RevisionConflict,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    CheckpointConflict,
    CollaborativeEditingAuthority,
    DocumentSequenceConflict,
    TargetVersionConflict,
)
from code_assist_v1.notion_collaboration.database_features import (
    CollaborativeDatabaseAuthority,
    SCHEMA_COMMAND_KINDS,
    SURFACE_COMMAND_KINDS,
)
from code_assist_v1.notion_collaboration.database_relation_pair import (
    disable_relation_pair, enable_relation_pair,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    IdentityUnavailable,
    PAGE_ROLE_LEVEL,
    WORKSPACE_ROLES as COLLABORATIVE_WORKSPACE_ROLES,
)
from code_assist_v1.notion_collaboration.file_authority import (
    CapacityUnavailable,
    CollaborativeFileAuthority,
    UploadStateConflict,
)
from code_assist_v1.notion_collaboration.history_authority import (
    CollaborativeHistoryAuthority,
    HistoryIntegrityError,
)
from code_assist_v1.notion_collaboration.search_authority import (
    CollaborativeSearchAuthority,
)
from code_assist_v1.notion_collaboration.live_authority import (
    CollaborativeLiveAuthority,
    CommentRevisionConflict,
)


MAX_NAVIGATION_JSON_BYTES = 64 * 1024
NAVIGATION_UI_ENABLE_ENV = "CODE_ASSIST_NP2B_NAVIGATION_UI"
NAVIGATION_UI_KILL_SWITCH_ENV = "CODE_ASSIST_NP2B_NAVIGATION_UI_KILL_SWITCH"
CORE_UI_PILOT_ENABLE_ENV = notion_core_ui_pilot.PILOT_ENABLE_ENV
CORE_UI_PILOT_KILL_SWITCH_ENV = notion_core_ui_pilot.PILOT_KILL_SWITCH_ENV
CORE_UI_PILOT_SUBJECTS_ENV = notion_core_ui_pilot.PILOT_SUBJECTS_ENV
CORE_UI_PILOT_WORKSPACES_ENV = notion_core_ui_pilot.PILOT_WORKSPACES_ENV


def _uid() -> str | None:
    return current_user_id()


def _active_human_users() -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in list_public_users(include_data=False)
        if safe_user_id(str(item.get("id") or "")) and not item.get("disabled")
    ]


def _active_human_subject_ids() -> list[str]:
    return [safe_user_id(str(item["id"])) for item in _active_human_users()]


def _known_human_subject_ids() -> list[str]:
    return sorted(
        {
            subject
            for item in list_public_users(include_data=False)
            if (subject := safe_user_id(str(item.get("id") or "")))
        }
    )


def _collaborative_runtime_enabled() -> bool:
    return iam_runtime.runtime_requested()


def _store() -> nws.WorkspaceStore:
    if _collaborative_runtime_enabled():
        return iam_runtime.open_runtime_store(_active_human_subject_ids())
    path = nws.catalog_path_for_user_data_root(legacy_notion_store.USER_DATA_DIR)
    return nws.WorkspaceStore(path)


def _require_collaborative_page_workspace(
    store: iam_runtime.CollaborativeWorkspaceStore,
    actor_subject_id: str,
    workspace_id: str,
    page_id: str,
):
    access = store.iam.resolve_access(actor_subject_id, page_id)
    if access.workspace_id != workspace_id:
        raise NotFoundOrDenied("not found or permission denied")
    return access


def _reconcile_legacy_page_access_projection(
    store: iam_runtime.CollaborativeWorkspaceStore,
    page_id: str,
) -> None:
    """Make the legacy editor shard match the canonical IAM effective result.

    IAM commits first and remains authoritative.  Replaying the same IAM
    operation re-runs this projection, so a transient shard failure is
    repairable without issuing a second canonical mutation.
    """

    owner = iam_runtime.document_owner(store.iam, page_id)
    subjects = set(_known_human_subject_ids())
    subjects.update(
        str(item.get("shared_user_id") or "")
        for item in legacy_notion_store.list_shares(owner, page_id)
    )
    subjects.discard("")
    subjects.discard(owner)
    for subject in sorted(subjects):
        try:
            access = store.iam.resolve_access(subject, page_id)
        except (IdentityUnavailable, NotFoundOrDenied):
            legacy_notion_store.revoke_share(owner, page_id, subject)
            continue
        legacy_notion_store.share_page(
            owner,
            page_id,
            subject,
            iam_runtime.legacy_projection_role(access.role),
        )


def _reconcile_legacy_workspace_access_projection(
    store: iam_runtime.CollaborativeWorkspaceStore,
    workspace_id: str,
) -> None:
    for page_id in iam_runtime.workspace_document_ids(store.iam, workspace_id):
        _reconcile_legacy_page_access_projection(store, page_id)


def _navigation_ui_requested() -> bool:
    """Return the exact legacy NP2B feature request and kill-switch state."""

    enable = os.environ.get(NAVIGATION_UI_ENABLE_ENV, "")
    kill_switch = os.environ.get(NAVIGATION_UI_KILL_SWITCH_ENV, "")
    return enable == "1" and kill_switch in {"", "0"}


def _core_ui_pilot_decision(
    *,
    store: nws.WorkspaceStore | None = None,
    workspaces: list[dict[str, Any]] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> notion_core_ui_pilot.CoreUiPilotDecision:
    """Evaluate the product-wide pilot after a no-I/O configuration preflight."""

    configuration = notion_core_ui_pilot.load_core_ui_pilot_configuration()
    actor = safe_user_id(str(_uid() or ""))
    if not configuration.preflight_ready or actor not in configuration.subjects:
        return notion_core_ui_pilot.CoreUiPilotDecision(enabled=False)

    active_store = store or _store()
    visible = workspaces
    if visible is None:
        visible = active_store.list_workspaces(actor)
    active_capabilities = capabilities or active_store.capabilities()
    return notion_core_ui_pilot.evaluate_core_ui_pilot(
        configuration,
        actor_subject_id=actor,
        visible_workspace_ids=[str(item.get("id") or "") for item in visible],
        substrate_ready=active_capabilities.get("workspace_v2") is True,
    )


def _navigation_ui_enabled(
    *,
    store: nws.WorkspaceStore | None = None,
    workspaces: list[dict[str, Any]] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> bool:
    """Return the legacy feature gate constrained by the Core UI pilot.

    Both switches are intentionally independent of app config imports so the
    protected host/config boundary stays unchanged.  Configuration or
    allowlist failures remain false and avoid catalog I/O.
    """

    if _collaborative_runtime_enabled():
        return True
    if not _navigation_ui_requested():
        return False
    return _core_ui_pilot_decision(
        store=store,
        workspaces=workspaces,
        capabilities=capabilities,
    ).enabled


def _json_data() -> dict[str, Any]:
    return notion_http.json_object_request()


class _DuplicateNavigationJsonKey(ValueError):
    pass


def _navigation_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateNavigationJsonKey("duplicate navigation JSON key")
        value[key] = item
    return value


def _reject_nonfinite_navigation_json(_value: str) -> None:
    raise ValueError("non-finite navigation JSON number")


def _navigation_json_data() -> dict[str, Any]:
    """Parse one strict JSON object without duplicate or non-finite values."""
    if request.mimetype != "application/json":
        raise notion_http.NotionRequestBodyError(
            "request body must be a valid JSON object"
        )
    if (
        request.content_length is not None
        and request.content_length > MAX_NAVIGATION_JSON_BYTES
    ):
        raise RequestEntityTooLarge()
    raw = request.stream.read(MAX_NAVIGATION_JSON_BYTES + 1)
    if len(raw) > MAX_NAVIGATION_JSON_BYTES:
        raise RequestEntityTooLarge()
    if not raw.strip():
        return {}
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_navigation_json_object,
            parse_constant=_reject_nonfinite_navigation_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise notion_http.NotionRequestBodyError(
            "request body must be a valid JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise notion_http.NotionRequestBodyError(
            "request body must be a valid JSON object"
        )
    return value


def _required_text(
    data: dict[str, Any],
    field: str,
    *,
    max_chars: int = 200,
) -> str:
    if field not in data or not isinstance(data[field], str):
        raise notion_http.NotionRequestValidationError(f"{field} required")
    value = data[field].strip()
    if not value:
        raise notion_http.NotionRequestValidationError(f"{field} required")
    if len(value) > max_chars:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be at most {max_chars} characters"
        )
    return value


def _optional_text(
    data: dict[str, Any],
    field: str,
    *,
    max_chars: int,
) -> str:
    if field not in data or data[field] is None:
        return ""
    if not isinstance(data[field], str):
        raise notion_http.NotionRequestValidationError(f"{field} must be a string")
    value = data[field].strip()
    if len(value) > max_chars:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be at most {max_chars} characters"
        )
    return value


def _base_version(data: dict[str, Any]) -> int:
    value = data.get("base_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise notion_http.NotionRequestValidationError(
            "base_version must be an integer of at least 1"
        )
    return value


def _positive_epoch(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be an integer of at least 1"
        )
    return value


def _positive_version(data: dict[str, Any], field: str = "expected_row_version") -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be an integer of at least 1"
        )
    return value


def _nonnegative_integer(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be a non-negative integer"
        )
    return value


def _mention_ids(data: dict[str, Any]) -> list[str]:
    value = data.get("mention_subject_ids", [])
    if not isinstance(value, list) or len(value) > 100 or not all(
        isinstance(item, str) for item in value
    ):
        raise notion_http.NotionRequestValidationError(
            "mention_subject_ids must be an array of user IDs"
        )
    return value


def _live_authority(store: iam_runtime.CollaborativeWorkspaceStore) -> CollaborativeLiveAuthority:
    if not isinstance(store.iam, CollaborativeLiveAuthority):
        raise AuthorityUnavailable("live collaboration authority is unavailable")
    return store.iam


def _editing_authority(
    store: iam_runtime.CollaborativeWorkspaceStore,
) -> CollaborativeEditingAuthority:
    if not isinstance(store.iam, CollaborativeEditingAuthority):
        raise AuthorityUnavailable("collaborative editing authority is unavailable")
    return store.iam


def _database_authority(
    store: iam_runtime.CollaborativeWorkspaceStore,
) -> CollaborativeDatabaseAuthority:
    if not isinstance(store.iam, CollaborativeDatabaseAuthority):
        raise AuthorityUnavailable("collaborative database authority is unavailable")
    return store.iam


def _database_sharing_installed(authority: CollaborativeDatabaseAuthority) -> bool:
    with authority._reader() as con:
        con.execute("BEGIN")
        return database_access.installed(con)


def _file_authority(
    store: iam_runtime.CollaborativeWorkspaceStore,
) -> CollaborativeFileAuthority:
    if not isinstance(store.iam, CollaborativeFileAuthority):
        raise AuthorityUnavailable("collaborative file authority is unavailable")
    return store.iam


def _history_authority(
    store: iam_runtime.CollaborativeWorkspaceStore,
) -> CollaborativeHistoryAuthority:
    if not isinstance(store.iam, CollaborativeHistoryAuthority):
        raise AuthorityUnavailable("collaborative history authority is unavailable")
    return store.iam


def _search_authority(
    store: iam_runtime.CollaborativeWorkspaceStore,
) -> CollaborativeSearchAuthority:
    if not isinstance(store.iam, CollaborativeSearchAuthority):
        raise AuthorityUnavailable("collaborative search authority is unavailable")
    return store.iam


def _page_copy_authority(store):
    # Transport is staged independently of unfinished copy shapes and UI recovery.
    if store.capabilities().get("collaborative_page_copy") is not True:
        raise AuthorityUnavailable("canonical page copy is not available")
    return _editing_authority(store)


def _database_copy_authority(store):
    if store.capabilities().get("collaborative_owned_database_copy") is not True:
        raise AuthorityUnavailable("canonical database copy is not available")
    return _editing_authority(store)


def _database_move_authority(store):
    if store.capabilities().get("collaborative_owned_database_move") is not True:
        raise AuthorityUnavailable("canonical database move is not available")
    return _editing_authority(store)


def _database_trash_authority(store):
    if store.capabilities().get("collaborative_owned_database_trash") is not True:
        raise AuthorityUnavailable("canonical database Trash is not available")
    return _editing_authority(store)


def _database_row_trash_authority(store):
    if store.capabilities().get("collaborative_database_row_trash") is not True:
        raise AuthorityUnavailable("canonical database-row Trash is not available")
    return _editing_authority(store)


def _synced_detach_authority(store):
    if store.capabilities().get("collaborative_synced_detach") is not True:
        raise AuthorityUnavailable("synced detach is not available")
    return _editing_authority(store)


def _synced_legacy_adoption_authority(store):
    if store.capabilities().get("collaborative_synced_legacy_adoption") is not True:
        raise AuthorityUnavailable("legacy synced origin adoption is not available")
    return _editing_authority(store)


def _request_origin() -> str:
    candidate = str(request.headers.get("Origin") or request.host_url).rstrip("/")
    actual = urlsplit(candidate)
    host = urlsplit(request.host_url)
    actual_port = actual.port or (443 if actual.scheme == "https" else 80)
    host_port = host.port or (443 if host.scheme == "https" else 80)
    if (
        actual.scheme not in {"http", "https"}
        or actual.scheme != host.scheme
        or actual.hostname != host.hostname
        or actual_port != host_port
        or actual.path not in {"", "/"}
        or actual.query
        or actual.fragment
    ):
        raise notion_http.NotionRequestValidationError("same-origin request required")
    return candidate


def _exact_json_fields(data: dict[str, Any], required: set[str]) -> None:
    """Reject both missing and unknown fields for new NP-2B commands."""
    if set(data) != required:
        raise notion_http.NotionRequestValidationError(
            "request fields do not match the navigation contract"
        )


def _navigation_version(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise notion_http.NotionRequestValidationError(
            f"{field} must be a non-negative integer"
        )
    return value


def _subject_status(subject_id: str) -> tuple[str, bool]:
    target = safe_user_id(subject_id)
    if not target:
        raise notion_http.NotionRequestValidationError("valid subject_id required")
    active = {
        safe_user_id(str(item.get("id") or ""))
        for item in list_public_users(include_data=False)
        if item.get("id") and not item.get("disabled")
    }
    return target, target in active


def _success(status: str, result: dict[str, Any], *, http_status: int = 200):
    response = jsonify({"status": status, **result})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, http_status


def _workspace_error_response(exc: BaseException):
    """Map overlay-only errors without coupling shared Notion HTTP to NP-2A."""
    if isinstance(exc, RequestEntityTooLarge):
        return notion_http.validation_response(
            "Request payload is too large",
            code="payload_too_large",
            status=413,
        )
    if isinstance(exc, EpochConflict):
        status = 409
        code = "collaboration_epoch_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "collaboration access changed; refresh and retry",
            "current_workspace_epoch": exc.workspace_epoch,
            "current_document_epoch": exc.document_epoch,
        }
    elif isinstance(exc, DatabaseTrashShapeUnavailable):
        status = 422
        code = "database_trash_shape_unavailable"
        payload = {"error": code, "code": code,
            "message": "데이터베이스 블록의 실시간 첨부가 저장본과 다릅니다. 동기화 후 다시 시도해 주세요."}
    elif isinstance(exc, (AuthorityUnavailable, IdentityUnavailable)):
        status = 503
        code = "collaboration_authority_unavailable"
        payload = {
            "error": code,
            "code": code,
            "message": "collaboration authority is unavailable",
        }
    elif isinstance(exc, CommentRevisionConflict):
        status = 409
        code = "comment_revision_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "comment changed; refresh and retry",
            "current_row_version": exc.current_row_version,
        }
    elif isinstance(exc, DocumentSequenceConflict):
        status = 409
        code = "document_sequence_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "document changed; replay and retry",
            "current_document_sequence": exc.current_document_sequence,
        }
    elif isinstance(exc, TargetVersionConflict):
        status = 409
        code = "typed_command_target_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "command target changed; replay and retry",
            "current_target_version": exc.current_target_version,
        }
    elif isinstance(exc, RevisionConflict):
        status = 409
        code = "revision_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "revision changed; refresh and retry",
            "current_revision": exc.current_revision,
        }
    elif isinstance(exc, CheckpointConflict):
        status = 409
        code = "checkpoint_not_ready"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc) or "checkpoint is not ready",
        }
    elif isinstance(exc, CopyCancelled):
        status = 409
        code = "page_copy_cancelled"
        payload = {"error": code, "code": code, "message": "이 복제 요청은 취소되었습니다. 새로 복제해 주세요."}
    elif isinstance(exc, CopySourceChanged):
        status = 409
        code = "page_copy_source_changed"
        payload = {"error": code, "code": code, "message": "복제 중 원본 또는 대상 위치가 변경되었습니다. 다시 시도해 주세요."}
    elif isinstance(exc, CopyShapeUnavailable):
        status = 422
        code = "page_copy_shape_unavailable"
        payload = {"error": code, "code": code, "message": "아직 복제를 지원하지 않는 블록이나 데이터베이스가 포함되어 있습니다. 복사본은 생성되지 않았습니다."}
    elif isinstance(exc, DatabaseMoveShapeUnavailable):
        status = 422
        code = "database_move_shape_unavailable"
        payload = {"error": code, "code": code, "message": "아직 이동을 지원하지 않는 데이터베이스 참조가 있습니다. 원본은 변경되지 않았습니다."}
    elif isinstance(exc, DatabaseRowMoveShapeUnavailable):
        status = 422
        code = "database_row_move_shape_unavailable"
        payload = {"error": code, "code": code,
            "message": "선택한 행을 이 위치로 손실 없이 옮길 수 없습니다. 원본은 변경되지 않았습니다."}
    elif isinstance(exc, LinkedDatabaseSourceChanged):
        status = 409
        code = "linked_database_source_changed"
        payload = {"error": code, "code": code, "message": "연결된 보기가 변경되었습니다. 다시 불러와 주세요."}
    elif isinstance(exc, LinkedDatabaseShapeUnavailable):
        status = 422
        code = "linked_database_shape_unavailable"
        payload = {"error": code, "code": code, "message": "연결된 데이터베이스의 원본 또는 보기 형식을 확인할 수 없습니다."}
    elif isinstance(exc, SyncedSourceChanged):
        status = 409
        code = "synced_source_changed"
        payload = {"error": code, "code": code, "message": "synced source changed; refresh and retry"}
    elif isinstance(exc, SyncedShapeUnavailable):
        status = 422
        code = "synced_shape_unavailable"
        payload = {"error": code, "code": code, "message": "synced source cannot be resolved"}
    elif isinstance(exc, HistoryIntegrityError):
        status = 409
        code = "history_integrity_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc) or "history snapshot is unavailable",
        }
    elif isinstance(exc, OperationConflict):
        status = 409
        code = "collaboration_operation_id_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": "operation ID was reused with different input",
        }
    elif isinstance(exc, UploadStateConflict):
        status = 409
        code = "file_upload_state_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc) or "file upload state changed; refresh and retry",
        }
    elif isinstance(exc, CapacityUnavailable):
        status = 507
        code = "operational_storage_capacity_unavailable"
        payload = {
            "error": code,
            "code": code,
            "message": "operational storage capacity is temporarily unavailable",
        }
    elif isinstance(exc, NotFoundOrDenied):
        status = 404
        code = "not_found_or_permission_denied"
        payload = {
            "error": code,
            "code": code,
            "message": "not found or permission denied",
        }
    elif isinstance(exc, nws.WorkspaceSubstrateUnavailable):
        status = 503
        code = "workspace_substrate_unavailable"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc).strip() or "workspace substrate is unavailable",
        }
    elif isinstance(exc, nws.WorkspaceRevisionConflict):
        status = 409
        code = "workspace_revision_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc).strip() or "workspace revision conflict",
            "current_version": exc.current_version,
        }
    elif isinstance(exc, nws.WorkspaceOperationConflict):
        status = 409
        code = "workspace_operation_id_conflict"
        payload = {
            "error": code,
            "code": code,
            "message": str(exc).strip() or "workspace operation id conflict",
            "operation_id": exc.operation_id,
        }
    elif isinstance(exc, FileNotFoundError) and not getattr(exc, "filename", None):
        status = 404
        code = "not_found"
        payload = {
            "error": "not found",
            "code": code,
            "message": "not found",
        }
    else:
        return notion_http.error_response(exc)

    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, status


def register_notion_workspace_routes(app) -> None:
    """Register the NP-2A overlay without changing the legacy route registrar."""
    notion_http.register_error_handlers(app)

    @app.route("/api/notion/ui-pilot/readiness", methods=["GET"])
    def api_notion_core_ui_pilot_readiness():
        try:
            actor = safe_user_id(str(_uid() or ""))
            if not actor:
                raise PermissionError("authentication required")
            decision = _core_ui_pilot_decision()
            return _success(
                "pilot_readiness",
                {"pilot": decision.public_descriptor()},
            )
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces", methods=["GET", "POST"])
    def api_notion_workspaces():
        try:
            store = _store()
            if request.method == "GET":
                items = store.list_workspaces(_uid())
                if _collaborative_runtime_enabled():
                    actor = safe_user_id(str(_uid() or ""))
                    user = next(
                        (
                            item
                            for item in _active_human_users()
                            if safe_user_id(str(item.get("id") or "")) == actor
                        ),
                        {"id": actor, "display_name": actor},
                    )
                    items = iam_runtime.ensure_personal_workspace(
                        store,
                        actor,
                        display_name=str(user.get("display_name") or actor),
                    )
                capabilities = store.capabilities()
                if _collaborative_runtime_enabled():
                    capabilities["core_ui_pilot"] = True
                    capabilities["navigation_ui"] = True
                else:
                    pilot = _core_ui_pilot_decision(
                        store=store,
                        workspaces=items,
                        capabilities=capabilities,
                    )
                    capabilities["core_ui_pilot"] = pilot.enabled
                    capabilities["navigation_ui"] = (
                        _navigation_ui_requested() and pilot.enabled
                    )
                response = jsonify(
                    {
                        "items": items,
                        "count": len(items),
                        "capabilities": capabilities,
                    }
                )
                response.headers["Cache-Control"] = "no-store, max-age=0"
                return response

            data = _json_data()
            result = store.create_workspace(
                _uid(),
                name=_required_text(data, "name"),
                icon=_optional_text(data, "icon", max_chars=64),
                operation_id=_required_text(data, "operation_id"),
            )
            return _success("created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>", methods=["GET"])
    def api_notion_workspace_get(workspace_id: str):
        try:
            store = _store()
            workspace = store.get_workspace(_uid(), workspace_id)
            response = jsonify({"workspace": workspace, "capabilities": {
                "workspace_appearance": True, "workspace_image": notion_workspace_images.supported(store),
                "custom_emoji": notion_workspace_custom_emoji.supported(store),
            }})
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/appearance", methods=["PATCH"])
    def api_notion_workspace_appearance(workspace_id: str):
        try:
            data = _navigation_json_data()
            _exact_json_fields(data, {"icon", "base_version", "operation_id"})
            result = update_workspace_icon(
                _store(), _uid(), workspace_id, icon=data["icon"],
                base_version=_base_version(data), operation_id=_required_text(data, "operation_id"),
            )
            return _success("workspace_appearance_updated", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/icon-image", methods=["POST"])
    def api_notion_workspace_icon_image(workspace_id: str):
        try:
            raw = request.stream.read(notion_workspace_images.MAX_BYTES + 1)
            version = int(request.headers.get("X-Notion-Base-Version", ""))
            result = notion_workspace_images.update_workspace_image(
                _store(), _uid(), workspace_id, raw=raw, mime_type=request.mimetype,
                base_version=version, operation_id=request.headers.get("X-Notion-Operation-Id", ""),
            )
            return _success("workspace_image_updated", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspace-images/<image_id>", methods=["GET"])
    def api_notion_workspace_image_get(image_id: str):
        try:
            image = notion_workspace_images.read_workspace_image(_store(), _uid(), image_id)
            response = Response(image["raw"], mimetype=image["mime_type"])
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Disposition"] = "inline"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/custom-emoji", methods=["GET", "POST"])
    def api_notion_workspace_custom_emoji(workspace_id: str):
        try:
            store, actor = _store(), _uid()
            if request.method == "GET":
                response = jsonify({"items": notion_workspace_custom_emoji.list_custom_emojis(
                    store, actor, workspace_id,
                )})
                response.headers["Cache-Control"] = "private, no-store, max-age=0"
                return response
            upload = request.files.get("image")
            if upload is None:
                raise ValueError("emoji image is required")
            result = notion_workspace_custom_emoji.create_custom_emoji(
                store, actor, workspace_id,
                name=request.form.get("name"),
                raw=upload.stream.read(notion_workspace_images.MAX_BYTES + 1),
                mime_type=upload.mimetype,
                base_version=int(request.form.get("base_version", "")),
                operation_id=request.form.get("operation_id", ""),
            )
            return _success("custom_emoji_created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/custom-emoji/<emoji_id>", methods=["GET"])
    def api_notion_custom_emoji_image(emoji_id: str):
        try:
            image = notion_workspace_custom_emoji.read_custom_emoji(_store(), _uid(), emoji_id)
            response = Response(image["raw"], mimetype=image["mime_type"])
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Disposition"] = "inline"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages",
        methods=["GET", "POST"],
    )
    def api_notion_workspace_pages(workspace_id: str):
        try:
            if request.method == "POST":
                if not _navigation_ui_enabled():
                    raise FileNotFoundError("not found")
                data = _navigation_json_data()
                _exact_json_fields(
                    data,
                    {"base_version", "operation_id", "parent_id", "title"}
                    | (set(data) & {"teamspace_id", "template", "source_document_id", "source_database_id"}),
                )
                store, actor = _store(), str(_uid() or "")
                arguments = dict(
                    title=_required_text(data, "title", max_chars=160),
                    parent_id=_optional_text(data, "parent_id", max_chars=160),
                    base_version=_base_version(data),
                    operation_id=_required_text(data, "operation_id", max_chars=128),
                )
                if isinstance(store, iam_runtime.CollaborativeWorkspaceStore):
                    scoped_creation = _database_sharing_installed(_database_authority(store))
                    if (not store.capabilities().get("collaborative_page_creation")
                            and not (scoped_creation and data.get("template") == "linked_database")):
                        raise AuthorityUnavailable("native page creation is not installed")
                    if "template" in data:
                        selected_template = _required_text(data,"template",max_chars=40)
                        if (selected_template not in store.capabilities().get("page_creation_templates",[])
                                and not (scoped_creation and selected_template == "linked_database")):
                            raise ValueError("supported page template required")
                        arguments["template"] = selected_template
                    if "source_document_id" in data or "source_database_id" in data:
                        if arguments.get("template") != "linked_database":
                            raise ValueError("linked database source requires linked_database template")
                        if "source_document_id" in data:
                            arguments["source_document_id"] = _required_text(data, "source_document_id", max_chars=160)
                        arguments["source_database_id"] = _required_text(data, "source_database_id", max_chars=160)
                    if "teamspace_id" in data:
                        if not isinstance(data["teamspace_id"], str):
                            raise ValueError("teamspace_id must be a string")
                        if (not store.capabilities().get("collaborative_team_page_creation")
                                and not scoped_creation):
                            raise AuthorityUnavailable("native team page creation is not installed")
                        arguments["teamspace_id"] = _required_text(data, "teamspace_id", max_chars=160)
                    result = create_collaborative_page(store.iam, actor, workspace_id, **arguments)
                else:
                    if "teamspace_id" in data or "template" in data or "source_document_id" in data or "source_database_id" in data:
                        raise ValueError("teamspace/template requires native page creation")
                    result = notion_workspace_pages.create_workspace_page(store, actor, workspace_id, **arguments)
                return _success(
                    "page_created",
                    result,
                    http_status=200 if result["replayed"] else 201,
                )
            query = str(request.args.get("q") or "").strip()
            if len(query) > 200:
                raise notion_http.NotionRequestValidationError(
                    "q must be at most 200 characters"
                )
            result = notion_workspace_pages.list_workspace_pages(
                _store(),
                str(_uid() or ""),
                workspace_id,
                query=query,
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/meetings", methods=["GET"])
    def api_notion_workspace_meetings(workspace_id: str):
        try:
            store, actor = _store(), str(_uid() or "")
            query = str(request.args.get("q") or "").strip()
            if len(query) > 200:
                raise notion_http.NotionRequestValidationError("q must be at most 200 characters")
            limit, offset = int(request.args.get("limit") or 100), int(request.args.get("offset") or 0)
            if _collaborative_runtime_enabled():
                result = _search_authority(store).list_meetings(actor, workspace_id, query=query, limit=limit, offset=offset)
            else:
                catalog = notion_workspace_pages.list_workspace_pages(store, actor, workspace_id)
                result = legacy_notion_store.list_meeting_pages(
                    actor, query=query, limit=limit, offset=offset, allowed_pages=catalog["items"],
                )
                result["degraded"] = bool(result["degraded"] or catalog.get("degraded"))
                result["degraded_sources"] = sorted(set(result["degraded_sources"]) | set(catalog.get("degraded_sources") or []))
            return _success("meetings_loaded", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<page_id>/databases/<database_id>/copy",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_duplicate(workspace_id: str, page_id: str, database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_copy_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "operation_id", "include_content"})
            result = copy_owned_database(authority, str(_uid() or ""), workspace_id,
                page_id, database_id,
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            return _success("database_duplicated", result,
                http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<page_id>/databases/<database_id>/copy-result",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_duplicate_result(workspace_id: str, page_id: str,
                                                               database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_copy_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "operation_id", "include_content"})
            result = read_owned_database_copy_result(authority, str(_uid() or ""), workspace_id,
                page_id, database_id,
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/move",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_move(workspace_id: str, database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_move_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"source_document_id", "destination_document_id", "expected_source_sequence",
                "expected_destination_sequence", "expected_workspace_epoch",
                "expected_source_epoch", "expected_destination_epoch", "operation_id"})
            result = move_owned_database(authority, str(_uid() or ""), workspace_id,
                data.pop("source_document_id"), data.pop("destination_document_id"),
                database_id, **data)
            return _success("database_moved", result,
                http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/move-result",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_move_result(workspace_id: str,
                                                         database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_move_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"source_document_id", "destination_document_id", "expected_source_sequence",
                "expected_destination_sequence", "expected_workspace_epoch",
                "expected_source_epoch", "expected_destination_epoch", "operation_id"})
            result = read_owned_database_move_result(authority, str(_uid() or ""), workspace_id,
                data.pop("source_document_id"), data.pop("destination_document_id"),
                database_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/trash",
        methods=["POST"],
    )
    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/restore",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_trash(workspace_id: str,
                                                   database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_version", "operation_id"})
            archived = request.path.endswith("/trash")
            result = set_owned_database_trashed(authority, str(_uid() or ""), workspace_id,
                data.pop("document_id"), database_id, archived=archived, **data)
            return _success("database_trashed" if archived else "database_restored", result,
                http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/lifecycle-result",
        methods=["POST"],
    )
    def api_notion_workspace_owned_database_lifecycle_result(workspace_id: str,
                                                               database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "archived", "expected_document_sequence",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_version", "operation_id"})
            result = read_owned_database_trash_result(authority, str(_uid() or ""),
                workspace_id, data.pop("document_id"), database_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/database-trash",
        methods=["GET"],
    )
    def api_notion_workspace_owned_database_trash_list(workspace_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_trash_authority(_store())
            if set(request.args) - {"q"}:
                raise ValueError("invalid database Trash query")
            result = list_trashed_owned_databases(authority, str(_uid() or ""),
                workspace_id, query=request.args.get("q", ""))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<document_id>/archived-databases/<block_id>",
        methods=["GET"],
    )
    def api_notion_workspace_owned_database_trash_preview(workspace_id: str,
                                                            document_id: str,
                                                            block_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_trash_authority(_store())
            result = read_trashed_owned_database(authority, str(_uid() or ""),
                workspace_id, document_id, block_id)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<document_id>/archived-databases/<block_id>/export",
        methods=["GET"],
    )
    def api_notion_workspace_owned_database_trash_export(workspace_id: str,
                                                           document_id: str,
                                                           block_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            bundle = request.args.get("bundle", "markdown_csv")
            if bundle not in {"markdown_csv", "html"}:
                raise notion_http.NotionRequestValidationError("invalid export bundle")
            page_content = request.args.get("page_content", "all")
            if page_content not in {"all", "exclude_files_images"}:
                raise notion_http.NotionRequestValidationError("invalid page content option")
            authority = _database_trash_authority(_store())
            result = export_trashed_owned_database(authority, str(_uid() or ""),
                workspace_id, document_id, block_id,
                view_id=request.args.get("view_id"),
                include_media=page_content == "all",
                with_asset_bundle=bundle == "html")
            if bundle == "html":
                stream = result["archive"]
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(0)

                def chunks():
                    try:
                        while chunk := stream.read(1024 * 1024):
                            yield chunk
                    finally:
                        stream.close()

                response = Response(chunks(), mimetype="application/zip")
                response.content_length = size
                response.call_on_close(stream.close)
            elif result["row_count"] == 0:
                response = Response(notion_database_export_bundle.empty_archive(),
                    mimetype="application/zip")
            else:
                body = notion_database_export_bundle.markdown_csv_archive(
                    result["title"], result["csv"], result["all_csv"],
                    identity=result["database_id"])
                response = Response(body, mimetype="application/zip")
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", block_id).strip("-") or "database"
            response.headers["Content-Disposition"] = (
                f'attachment; filename="{safe_name}.zip"')
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/trash",
        methods=["POST"],
    )
    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/restore",
        methods=["POST"],
    )
    def api_notion_workspace_database_row_trash(workspace_id: str,
                                                 database_id: str, row_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_row_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "expected_document_sequence",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_version", "expected_row_version", "operation_id"})
            archived = request.path.endswith("/trash")
            result = set_database_row_trashed(authority, str(_uid() or ""), workspace_id,
                data.pop("document_id"), database_id, row_id, archived=archived, **data)
            return _success("database_row_trashed" if archived else "database_row_restored",
                result, http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/lifecycle-result",
        methods=["POST"],
    )
    def api_notion_workspace_database_row_lifecycle_result(workspace_id: str,
                                                             database_id: str, row_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_row_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "archived",
                "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_version",
                "expected_row_version", "operation_id"})
            result = read_database_row_trash_result(authority, str(_uid() or ""),
                workspace_id, data.pop("document_id"), database_id, row_id, **data)
            response = jsonify({"state": "unknown"} if result is None else
                {"state": "committed", "result": result})
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/trash-batch",
        methods=["POST"],
    )
    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/restore-batch",
        methods=["POST"],
    )
    def api_notion_workspace_database_rows_trash_batch(workspace_id: str,
                                                        database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_row_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "rows", "operation_id",
                "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_version"})
            archived = request.path.endswith("/trash-batch")
            action = trash_database_rows_batch if archived else restore_database_rows_batch
            result = action(authority, str(_uid() or ""),
                workspace_id, data.pop("document_id"), database_id, **data)
            return _success("database_rows_trashed" if archived else "database_rows_restored", result,
                http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/trash-batch/lifecycle-result",
        methods=["POST"],
    )
    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/restore-batch/lifecycle-result",
        methods=["POST"],
    )
    def api_notion_workspace_database_rows_trash_batch_result(workspace_id: str,
                                                               database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_row_trash_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"document_id", "archived", "rows", "operation_id",
                "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_version"})
            if data["archived"] is not request.path.endswith("/trash-batch/lifecycle-result"):
                raise ValueError("database-row batch lifecycle does not match the route")
            result = read_database_rows_batch_trash_result(authority,
                str(_uid() or ""), workspace_id, data.pop("document_id"),
                database_id, **data)
            response = jsonify({"state": "unknown"} if result is None else
                {"state": "committed", "result": result})
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/row-trash",
        methods=["GET"],
    )
    def api_notion_workspace_database_row_trash_list(workspace_id: str,
                                                       database_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_row_trash_authority(_store())
            if set(request.args) - {"document_id", "limit", "row_id"}:
                raise ValueError("invalid database-row Trash query")
            document_id = request.args.get("document_id")
            if not document_id:
                raise ValueError("document_id is required")
            limit = int(request.args.get("limit", "100"))
            result = list_trashed_database_rows(authority, str(_uid() or ""),
                workspace_id, document_id, database_id, limit=limit,
                row_id=request.args.get("row_id"))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate-intent",
        methods=["POST"],
    )
    def api_notion_workspace_page_duplicate_intent(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _page_copy_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = register_page_copy_intent(
                authority, str(_uid() or ""), workspace_id, page_id,
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201 if result["state"] == "pending" and not result["replayed"] else 200
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate",
        methods=["POST"],
    )
    def api_notion_workspace_page_duplicate(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
            if _collaborative_runtime_enabled():
                authority = _page_copy_authority(_store())
                _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
                result = copy_page(authority, str(_uid() or ""), workspace_id, page_id,
                    local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""),
                    require_registered_intent=True, **data)
                return _success("page_duplicated", result, http_status=200 if result["replayed"] else 201)
            _exact_json_fields(data, {"base_version", "operation_id"})
            result = notion_workspace_pages.duplicate_workspace_page(
                _store(),
                str(_uid() or ""),
                workspace_id,
                page_id,
                base_version=_base_version(data),
                operation_id=_required_text(data, "operation_id", max_chars=128),
            )
            return _success(
                "page_duplicated",
                result,
                http_status=200 if result["replayed"] else 201,
            )
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate-result",
        methods=["POST"],
    )
    def api_notion_workspace_page_duplicate_result(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _page_copy_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = read_page_copy_result(authority, str(_uid() or ""), workspace_id, page_id,
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate-cancel", methods=["POST"])
    def api_notion_workspace_page_duplicate_cancel(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _page_copy_authority(_store())
            data = _navigation_json_data()
            _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = cancel_page_copy(authority, str(_uid() or ""), workspace_id, page_id,
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/page-copy-results", methods=["GET"])
    def api_notion_workspace_page_copy_results(workspace_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _page_copy_authority(_store())
            if set(request.args) - {"limit", "cursor"} or any(len(request.args.getlist(key)) != 1 for key in request.args):
                raise ValueError("invalid copy result query")
            result = list_page_copy_results(authority, str(_uid() or ""), workspace_id,
                limit=int(request.args.get("limit", "20")), cursor=request.args.get("cursor", ""),
                local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/page-copy-intents", methods=["GET"])
    def api_notion_workspace_page_copy_intents(workspace_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _page_copy_authority(_store())
            if set(request.args) - {"limit", "cursor"} or any(len(request.args.getlist(key)) != 1 for key in request.args):
                raise ValueError("invalid copy intent query")
            result = list_page_copy_intents(authority, str(_uid() or ""), workspace_id,
                limit=int(request.args.get("limit", "20")), cursor=request.args.get("cursor", ""))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/trash",
        methods=["POST"],
    )
    def api_notion_workspace_page_trash(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
            if _collaborative_runtime_enabled():
                _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
                result = set_page_archived(_editing_authority(_store()), str(_uid() or ""), workspace_id, page_id,
                                          archived=True, **data)
                return _success("page_trashed", result)
            _exact_json_fields(data, {"base_version", "operation_id"})
            result = notion_workspace_pages.archive_workspace_page(
                _store(),
                str(_uid() or ""),
                workspace_id,
                page_id,
                base_version=_base_version(data),
                operation_id=_required_text(data, "operation_id", max_chars=128),
            )
            return _success("page_trashed", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/lifecycle-result",
        methods=["POST"],
    )
    def api_notion_workspace_page_lifecycle_result(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled() or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
            _exact_json_fields(data, {"archived", "expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = read_page_lifecycle_result(_editing_authority(_store()), str(_uid() or ""), workspace_id, page_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/trash",
        methods=["GET"],
    )
    def api_notion_workspace_trash(workspace_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            query = str(request.args.get("q") or "").strip()
            if len(query) > 200:
                raise notion_http.NotionRequestValidationError(
                    "q must be at most 200 characters"
                )
            if _collaborative_runtime_enabled():
                result = list_archived_pages(_editing_authority(_store()), str(_uid() or ""), workspace_id, query=query)
            else:
                result = notion_workspace_pages.list_workspace_trash(
                    _store(), str(_uid() or ""), workspace_id, query=query,
                )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/trash/<path:page_id>/restore",
        methods=["POST"],
    )
    def api_notion_workspace_trash_restore(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
            if _collaborative_runtime_enabled():
                _exact_json_fields(data, {"expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
                result = set_page_archived(_editing_authority(_store()), str(_uid() or ""), workspace_id, page_id,
                                          archived=False, **data)
                return _success("page_restored", result)
            _exact_json_fields(data, {"base_version", "operation_id"})
            result = notion_workspace_pages.restore_workspace_page(
                _store(),
                str(_uid() or ""),
                workspace_id,
                page_id,
                base_version=_base_version(data),
                operation_id=_required_text(data, "operation_id", max_chars=128),
            )
            return _success("page_restored", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/navigation",
        methods=["GET"],
    )
    def api_notion_workspace_navigation(workspace_id: str):
        try:
            result = _store().get_navigation_state(_uid(), workspace_id)
            return _success("navigation_loaded", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/navigation/preferences",
        methods=["PATCH"],
    )
    def api_notion_workspace_navigation_preferences(workspace_id: str):
        try:
            data = _navigation_json_data()
            _exact_json_fields(
                data,
                {
                    "expected_preference_version",
                    "sidebar_width_px",
                    "sidebar_collapsed",
                    "sections",
                    "operation_id",
                },
            )
            result = _store().replace_sidebar_preferences(
                _uid(),
                workspace_id,
                expected_preference_version=_navigation_version(
                    data, "expected_preference_version"
                ),
                sidebar_width_px=data["sidebar_width_px"],
                sidebar_collapsed=data["sidebar_collapsed"],
                sections=data["sections"],
                operation_id=_required_text(data, "operation_id"),
            )
            return _success("navigation_preferences_updated", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/navigation/favorites",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_navigation_favorites(workspace_id: str):
        try:
            data = _navigation_json_data()
            _exact_json_fields(
                data,
                {
                    "source_owner_subject_id",
                    "source_page_id",
                    "expected_favorite_version",
                    "operation_id",
                },
            )
            common = {
                "source_owner_subject_id": _required_text(
                    data, "source_owner_subject_id", max_chars=64
                ),
                "source_page_id": _required_text(
                    data, "source_page_id", max_chars=160
                ),
                "expected_favorite_version": _navigation_version(
                    data, "expected_favorite_version"
                ),
                "operation_id": _required_text(data, "operation_id"),
            }
            store = _store()
            if request.method == "PUT":
                result = store.add_page_favorite(_uid(), workspace_id, **common)
                return _success("page_favorite_added", result)
            result = store.remove_page_favorite(_uid(), workspace_id, **common)
            return _success("page_favorite_removed", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/navigation/database-favorites",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_navigation_database_favorites(workspace_id: str):
        try:
            data = _navigation_json_data()
            _exact_json_fields(
                data,
                {
                    "source_owner_subject_id",
                    "source_page_id",
                    "database_block_id",
                    "expected_favorite_version",
                    "operation_id",
                },
            )
            common = {
                "source_owner_subject_id": _required_text(data, "source_owner_subject_id", max_chars=64),
                "source_page_id": _required_text(data, "source_page_id", max_chars=160),
                "database_block_id": _required_text(data, "database_block_id", max_chars=160),
                "expected_favorite_version": _navigation_version(data, "expected_favorite_version"),
                "operation_id": _required_text(data, "operation_id"),
            }
            store = _store()
            if request.method == "PUT":
                result = store.add_database_favorite(_uid(), workspace_id, **common)
                return _success("database_favorite_added", result)
            result = store.remove_database_favorite(_uid(), workspace_id, **common)
            return _success("database_favorite_removed", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_member(workspace_id: str, subject_id: str):
        try:
            data = _json_data()
            store = _store()
            if _collaborative_runtime_enabled():
                target = safe_user_id(subject_id)
                if not target:
                    raise notion_http.NotionRequestValidationError(
                        "valid subject_id required"
                    )
                common = {
                    "expected_workspace_epoch": _positive_epoch(
                        data, "expected_workspace_epoch"
                    ),
                    "operation_id": _required_text(
                        data, "operation_id", max_chars=128
                    ),
                }
                if request.method == "PUT":
                    role = notion_http.enum_field(
                        data,
                        "role",
                        COLLABORATIVE_WORKSPACE_ROLES,
                        default="member",
                    )
                    result = store.iam.set_workspace_member(
                        str(_uid() or ""),
                        workspace_id,
                        target,
                        role=role,
                        **common,
                    )
                    for page_id in result.get("affected_document_ids") or []:
                        _reconcile_legacy_page_access_projection(store, page_id)
                    result["workspace"] = store.get_workspace(
                        str(_uid() or ""), workspace_id
                    )
                    return _success("member_set", result)
                result = store.iam.revoke_workspace_member(
                    str(_uid() or ""), workspace_id, target, **common
                )
                for page_id in result.get("affected_document_ids") or []:
                    _reconcile_legacy_page_access_projection(store, page_id)
                result["workspace"] = store.get_workspace(
                    str(_uid() or ""), workspace_id
                )
                return _success("member_revoked", result)
            common = {
                "base_version": _base_version(data),
                "operation_id": _required_text(data, "operation_id"),
            }
            if request.method == "PUT":
                target, target_active = _subject_status(subject_id)
                role = notion_http.enum_field(
                    data,
                    "role",
                    nws.WORKSPACE_ROLES,
                    default="member",
                )
                result = store.set_member(
                    _uid(),
                    workspace_id,
                    target,
                    role=role,
                    target_active=target_active,
                    **common,
                )
                return _success("member_set", result)
            result = store.revoke_member(
                _uid(), workspace_id, safe_user_id(subject_id), **common
            )
            return _success("member_revoked", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/pages/<path:page_id>/workspace", methods=["GET"])
    def api_notion_page_workspace(page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            payload = iam_runtime.resolve_page_workspace(_store().iam, str(_uid() or ""), page_id)
            response = jsonify(payload)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/collaboration",
        methods=["GET"],
    )
    def api_notion_collaborative_page(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            payload = iam_runtime.collaborative_page_payload(
                store.iam, str(_uid() or ""), page_id
            )
            if payload["page"]["workspace_id"] != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
            response = jsonify(payload)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/activity", methods=["GET"])
    def api_notion_collaborative_page_activity(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            response = jsonify(read_page_activity(_store().iam, str(_uid() or ""), workspace_id, page_id))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>",
        methods=["GET"],
    )
    def api_notion_independent_database(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            response = jsonify(database_access.read_projection(
                authority, str(_uid() or ""), workspace_id, database_id,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/database-favorites",
        methods=["GET"],
    )
    def api_notion_direct_database_favorites(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            if not direct_database_favorites.installed(authority):
                raise FileNotFoundError("not found")
            proof_id = request.args.get("access_database_id", "")
            response = jsonify(direct_database_favorites.read(
                authority, str(_uid() or ""), workspace_id,
                access_database_id=proof_id,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/favorite",
        methods=["PUT", "DELETE"],
    )
    def api_notion_direct_database_favorite(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not direct_database_favorites.installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"expected_favorite_version",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_epoch", "operation_id"})
            response = jsonify(direct_database_favorites.change(
                authority, str(_uid() or ""), workspace_id, database_id,
                remove=request.method == "DELETE", **data,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/cells",
        methods=["POST"],
    )
    def api_notion_independent_database_cells(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"cells", "expected_document_sequence",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_epoch", "operation_id"})
            response = jsonify(database_access.change_cells(
                authority, str(_uid() or ""), workspace_id, database_id, **data,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows",
        methods=["POST"],
    )
    def api_notion_independent_database_row(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            required = {"title", "expected_document_sequence",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_epoch", "operation_id"}
            if not required <= set(data) or set(data) - required - {
                    "use_default_template", "template_id"}:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the navigation contract")
            if "template_id" in data and not isinstance(data["template_id"], str):
                raise notion_http.NotionRequestValidationError(
                    "template_id must be a named template")
            response = jsonify(database_access.create_blank_row(
                authority, str(_uid() or ""), workspace_id, database_id, **data,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/body",
        methods=["POST"],
    )
    def api_notion_independent_database_row_body(
            workspace_id: str, database_id: str, row_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"blocks", "expected_target_version",
                "expected_document_sequence", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_epoch",
                "operation_id"})
            response = jsonify(database_access.change_row_body(
                authority, str(_uid() or ""), workspace_id, database_id,
                row_id, **data,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/mention-candidates",
        methods=["POST"],
    )
    def api_notion_independent_database_mention_candidates(
            workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"subject_ids"})
            result = eligible_mention_subject_ids(
                authority, str(_uid() or ""), workspace_id, database_id,
                data["subject_ids"],
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/comments",
        methods=["GET", "POST"],
    )
    def api_notion_independent_database_row_comments(
            workspace_id: str, database_id: str, row_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            if request.method == "GET":
                result = list_row_comments(
                    authority, str(_uid() or ""), workspace_id, database_id, row_id,
                )
                status = 200
            else:
                _request_origin()
                data = _json_data()
                required = {"comment_id", "content", "expected_workspace_epoch",
                    "expected_document_epoch", "expected_database_epoch", "operation_id"}
                if not required <= set(data) or not set(data) <= required | {
                        "parent_comment_id", "mention_subject_ids"}:
                    raise notion_http.NotionRequestValidationError(
                        "request fields do not match the database comment contract")
                result = add_row_comment(
                    authority, str(_uid() or ""), workspace_id, database_id, row_id,
                    comment_id=_required_text(data, "comment_id", max_chars=160),
                    content=_required_text(data, "content", max_chars=20_000),
                    parent_comment_id=_optional_text(
                        data, "parent_comment_id", max_chars=160) or None,
                    mention_subject_ids=_mention_ids(data),
                    expected_workspace_epoch=_positive_epoch(
                        data, "expected_workspace_epoch"),
                    expected_document_epoch=_positive_epoch(
                        data, "expected_document_epoch"),
                    expected_database_epoch=_positive_epoch(
                        data, "expected_database_epoch"),
                    operation_id=_required_text(data, "operation_id", max_chars=200),
                )
                status = 201
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/comments/<comment_id>",
        methods=["PATCH", "DELETE"],
    )
    def api_notion_independent_database_row_comment_item(
            workspace_id: str, database_id: str, row_id: str, comment_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            common_fields = {"expected_row_version", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_epoch", "operation_id"}
            if request.method == "DELETE":
                mutation, value = "delete", {}
                _exact_json_fields(data, common_fields)
            elif "content" in data:
                mutation = "edit"
                edit_required = common_fields | {"content"}
                if not edit_required <= set(data) or not set(data) <= edit_required | {
                        "mention_subject_ids"}:
                    raise notion_http.NotionRequestValidationError(
                        "request fields do not match the database comment contract")
                value = {"content": _required_text(data, "content", max_chars=20_000),
                         "mention_subject_ids": _mention_ids(data)}
            else:
                mutation = "resolve"
                _exact_json_fields(data, common_fields | {"resolved"})
                if not isinstance(data["resolved"], bool):
                    raise notion_http.NotionRequestValidationError(
                        "resolved must be a boolean")
                value = {"resolved": data["resolved"]}
            result = mutate_row_comment(
                authority, str(_uid() or ""), workspace_id, database_id,
                row_id, comment_id, mutation=mutation,
                expected_row_version=_positive_version(data),
                expected_workspace_epoch=_positive_epoch(data, "expected_workspace_epoch"),
                expected_document_epoch=_positive_epoch(data, "expected_document_epoch"),
                expected_database_epoch=_positive_epoch(data, "expected_database_epoch"),
                operation_id=_required_text(data, "operation_id", max_chars=200),
                **value,
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/rows/<row_id>/comments/<comment_id>/reactions",
        methods=["POST"],
    )
    def api_notion_independent_database_row_comment_reaction(
            workspace_id: str, database_id: str, row_id: str, comment_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"emoji", "expected_row_version",
                "expected_workspace_epoch", "expected_document_epoch",
                "expected_database_epoch", "operation_id"})
            result = mutate_row_comment(
                authority, str(_uid() or ""), workspace_id, database_id,
                row_id, comment_id, mutation="react",
                emoji=_required_text(data, "emoji", max_chars=32),
                expected_row_version=_positive_version(data),
                expected_workspace_epoch=_positive_epoch(data, "expected_workspace_epoch"),
                expected_document_epoch=_positive_epoch(data, "expected_document_epoch"),
                expected_database_epoch=_positive_epoch(data, "expected_database_epoch"),
                operation_id=_required_text(data, "operation_id", max_chars=200),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/databases/<database_id>/updates",
        methods=["GET"],
    )
    def api_notion_collaborative_database_updates(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            raw_limit = request.args.get("limit", "50")
            raw_before = request.args.get("before_sequence")
            limit = int(raw_limit)
            before = int(raw_before) if raw_before is not None else None
            store = _store()
            scoped = _database_sharing_installed(_database_authority(store))
            reader = read_database_updates_scoped if scoped else read_database_updates
            response = jsonify(reader(
                store.iam, str(_uid() or ""), workspace_id, database_id,
                limit=limit, before_sequence=before,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/synced-blocks/<block_id>",
        methods=["GET"],
    )
    def api_notion_collaborative_synced_block(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            response = jsonify(read_synced_block(
                _editing_authority(_store()), str(_uid() or ""), page_id, block_id,
                workspace_id=workspace_id,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route('/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/synced-blocks/<block_id>/locations', methods=['GET'])
    def api_notion_collaborative_synced_locations(workspace_id: str, page_id: str, block_id: str):
        from code_assist_v1.notion_collaboration.synced_locations import read_synced_locations
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError('not found')
            response = jsonify(read_synced_locations(
                _editing_authority(_store()), str(_uid() or ''), page_id, block_id,
                workspace_id=workspace_id))
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/synced-blocks/<block_id>/text", methods=["POST"])
    def api_notion_collaborative_synced_text(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
            _exact_json_fields(data, {"origin", "witnesses", "updates", "operation_id"})
            result = edit_synced_text(_editing_authority(_store()), str(_uid() or ""), workspace_id, page_id, block_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/synced-blocks/<block_id>/detach", methods=["POST"])
    def api_notion_collaborative_synced_detach(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _synced_detach_authority(_store())
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, {"origin", "witnesses", "operation_id"})
            result = detach_synced_block(authority, str(_uid() or ""), workspace_id, page_id,
                block_id, local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            return _success("synced_detached", result, http_status=200 if result["replayed"] else 201)
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/synced-blocks/<block_id>/detach-result", methods=["POST"])
    def api_notion_collaborative_synced_detach_result(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _synced_detach_authority(_store())
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, {"origin", "witnesses", "operation_id"})
            result = read_synced_detach_result(authority, str(_uid() or ""), workspace_id,
                page_id, block_id, local_origin=current_app.config.get("NOTION_CANONICAL_ORIGIN", ""), **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route('/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>/rows/<row_id>/synced-paste-target', methods=['GET'])
    def api_notion_synced_row_paste_target(workspace_id: str, page_id: str, database_id: str, row_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError('not found')
            response = jsonify(read_row_paste_target(_editing_authority(_store()), str(_uid() or ''),
                workspace_id, page_id, database_id, row_id))
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response, status

    @app.route('/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>/rows/<row_id>/synced-paste', methods=['POST'])
    @app.route('/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>/rows/<row_id>/synced-paste-result', methods=['POST'])
    def api_notion_synced_row_paste(workspace_id: str, page_id: str, database_id: str, row_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError('not found')
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, {'source', 'anchor_id', 'replace_empty', 'expected_target_version',
                'expected_workspace_epoch', 'expected_document_epoch', 'expected_database_epoch', 'operation_id'})
            reader = request.path.endswith('/synced-paste-result')
            operation = read_row_reference_result if reader else insert_row_reference
            result = operation(_editing_authority(_store()), str(_uid() or ''), workspace_id,
                               page_id, database_id, row_id, **data)
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response, 200 if reader or result.get('replayed') else 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/synced-legacy/candidates", methods=["POST"])
    def api_notion_synced_legacy_candidates(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _synced_legacy_adoption_authority(_store())
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, {"anchor", "document_id", *({"cursor"} if "cursor" in data else set())})
            response = jsonify(synced_legacy_adoption_service.list_legacy_candidates(
                authority, str(_uid() or ""), workspace_id, **data))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/synced-legacy/prepare", methods=["POST"])
    def api_notion_synced_legacy_review(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _synced_legacy_adoption_authority(_store())
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, {"origin", "references"})
            result = synced_legacy_adoption_service.review_legacy_adoption(authority,
                str(_uid() or ""), workspace_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/synced-legacy/<action>", methods=["POST"])
    def api_notion_synced_legacy_request(workspace_id: str, action: str):
        try:
            handlers = {"intent": synced_legacy_adoption_service.register_legacy_adoption_intent,
                "adopt": synced_legacy_adoption_service.adopt_legacy_selection,
                "result": synced_legacy_adoption_service.read_legacy_adoption_result,
                "cancel": synced_legacy_adoption_service.cancel_legacy_adoption}
            if action not in handlers or not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _synced_legacy_adoption_authority(_store())
            _request_origin()
            data = _navigation_json_data()
            _exact_json_fields(data, synced_legacy_adoption_service.REQUEST_FIELDS)
            result = handlers[action](authority, str(_uid() or ""), workspace_id, **data)
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            replayed = result.get("result", result).get("replayed", True)
            return response, 201 if action != "result" and replayed is False else 200
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/synced-legacy/requests", methods=["GET"])
    def api_notion_synced_legacy_history(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            if set(request.args) - {"limit", "cursor"}:
                raise notion_http.NotionRequestValidationError("unexpected legacy recovery query")
            result = synced_legacy_adoption_service.list_legacy_adoption_requests(
                _synced_legacy_adoption_authority(_store()), str(_uid() or ""), workspace_id,
                limit=int(request.args.get("limit", "20")), cursor=request.args.get("cursor", ""))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/database-sources", methods=["GET"])
    def api_notion_linkable_databases(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            reader = (list_linkable_databases_scoped if _database_sharing_installed(authority)
                      else list_linkable_databases)
            response = jsonify(reader(
                authority, str(_uid() or ""), workspace_id,
                query=request.args.get("q", ""), after=request.args.get("after", ""),
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route("/api/notion/workspaces/<workspace_id>/database-sources/<database_id>", methods=["GET"])
    def api_notion_linkable_database_source(workspace_id: str, database_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            reader = (read_linkable_database_source_scoped
                      if _database_sharing_installed(authority)
                      else read_linkable_database_source)
            response = jsonify(reader(
                authority, str(_uid() or ""),
                workspace_id, database_id,
            ))
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases",
        methods=["POST"],
    )
    def api_notion_insert_linked_database(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            required = {"database_id", "block_id", "expected_document_sequence",
                        "expected_workspace_epoch", "expected_document_epoch", "operation_id"}
            optional = {"after_block_id", "view_type", "view_name", "view_config", "layout"}
            _exact_json_fields(data, required | (set(data) & optional))
            result = insert_linked_database_scoped(
                authority, str(_uid() or ""), workspace_id, page_id,
                data["database_id"], data["block_id"],
                expected_document_sequence=data["expected_document_sequence"],
                expected_workspace_epoch=data["expected_workspace_epoch"],
                expected_document_epoch=data["expected_document_epoch"],
                operation_id=data["operation_id"],
                after_block_id=data.get("after_block_id"),
                view_type=data.get("view_type", "table"),
                view_name=data.get("view_name", "표"),
                view_config=data.get("view_config"),
                layout=data.get("layout"),
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases/<block_id>",
        methods=["GET"],
    )
    def api_notion_collaborative_linked_database(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            authority = _database_authority(_store())
            actor = str(_uid() or "")
            if _database_sharing_installed(authority):
                result = read_linked_database_scoped(
                    authority, actor, workspace_id, page_id, block_id,
                )
            else:
                result = read_linked_database(
                    authority, actor, page_id, block_id, workspace_id=workspace_id,
                )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases/<block_id>/cells",
        methods=["POST"],
    )
    def api_notion_linked_database_cells(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _json_data()
            authority = _database_authority(_store())
            actor = str(_uid() or "")
            if _database_sharing_installed(authority):
                _exact_json_fields(data, {"source", "instance_witness", "source_access", "cells", "operation_id"})
                result = execute_linked_cells_scoped(
                    authority, actor, workspace_id, page_id, block_id, **data,
                )
            else:
                _exact_json_fields(data, {"source", "instance_witness", "source_witness", "cells", "operation_id"})
                result = execute_linked_cells(
                    authority, actor, workspace_id, page_id, block_id, **data,
                )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases/<block_id>/rows",
        methods=["POST"],
    )
    def api_notion_linked_database_rows(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            required = {"source", "instance_witness", "source_access",
                        "title", "operation_id"}
            if not required <= set(data) or set(data) - required - {
                    "use_default_template", "template_id"}:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the navigation contract")
            result = execute_linked_row_create_scoped(
                authority, str(_uid() or ""), workspace_id, page_id,
                block_id, **data,
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases/<block_id>/templates",
        methods=["POST"],
    )
    def api_notion_linked_database_templates(workspace_id: str, page_id: str, block_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"source", "instance_witness", "source_access",
                                      "name", "target_id", "operation_id"})
            result = execute_linked_template_create_scoped(
                authority, str(_uid() or ""), workspace_id, page_id,
                block_id, **data,
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 201
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/linked-databases/<block_id>/templates/<template_id>",
        methods=["PATCH"],
    )
    def api_notion_linked_database_template_update(workspace_id: str, page_id: str,
                                                   block_id: str, template_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            _request_origin()
            authority = _database_authority(_store())
            if not _database_sharing_installed(authority):
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"source", "instance_witness", "source_access",
                                      "name", "body", "default_values",
                                      "expected_target_version", "operation_id"})
            result = execute_linked_template_update_scoped(
                authority, str(_uid() or ""), workspace_id, page_id,
                block_id, target_id=template_id, **data,
            )
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, 200
        except Exception as exc:
            response, status = _workspace_error_response(exc)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response, status

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/move",
        methods=["POST"],
    )
    def api_notion_collaborative_page_move(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _json_data()
            destination_fields = set(data) & {"parent_id", "before_id", "after_id"}
            if len(destination_fields) != 1:
                raise notion_http.NotionRequestValidationError("exactly one move destination is required")
            _exact_json_fields(data, {*destination_fields, "expected_document_sequence",
                                     "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = move_collaborative_page(
                _editing_authority(_store()), str(_uid() or ""), workspace_id, page_id, **data,
            )
            return _success("page_moved", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/return-to-database",
        methods=["POST"],
    )
    def api_notion_page_return_to_database(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            if store.capabilities().get("collaborative_page_to_database_row") is not True:
                raise AuthorityUnavailable("page-to-database return is not available")
            data = _json_data()
            _exact_json_fields(data, {
                "destination_document_id", "destination_database_id",
                "expected_page_sequence", "expected_parent_sequence",
                "expected_destination_sequence", "expected_row_version",
                "expected_workspace_epoch", "expected_page_epoch",
                "expected_parent_epoch", "expected_destination_epoch",
                "operation_id",
            })
            result = return_page_to_owned_database(
                _editing_authority(store), str(_uid() or ""), workspace_id,
                page_id, **data)
            return _success("page_returned_to_database", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/return-to-database-target",
        methods=["GET"],
    )
    def api_notion_page_return_to_database_target(workspace_id: str,
                                                    page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            if store.capabilities().get("collaborative_page_to_database_row") is not True:
                raise AuthorityUnavailable("page-to-database return is not available")
            result = read_page_return_target(
                _editing_authority(store), str(_uid() or ""), workspace_id,
                page_id,
                destination_document_id=request.args.get(
                    "destination_document_id"),
                destination_database_id=request.args.get(
                    "destination_database_id"))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/database-destinations",
        methods=["GET"],
    )
    def api_notion_page_database_destinations(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            if store.capabilities().get("collaborative_page_to_database_row") is not True:
                raise AuthorityUnavailable("page-to-database move is not available")
            result = list_page_database_destinations(
                _editing_authority(store), str(_uid() or ""), workspace_id,
                page_id, request.args.get("q", ""))
            response = jsonify(result)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/settings", methods=["PATCH"])
    def api_notion_page_settings(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"updates", "expected_document_sequence",
                                     "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = update_page_settings(_editing_authority(_store()), str(_uid() or ""), workspace_id, page_id, **data)
            return _success("settings_saved", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/temporary-unlock", methods=["POST"])
    def api_notion_collaborative_temporary_unlock(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            if not current_app.secret_key:
                raise RuntimeError("application signing key is required")
            actor = str(_uid() or "")
            generation = current_page_lock_generation(
                _editing_authority(_store()), actor, workspace_id, page_id,
            )
            token = URLSafeTimedSerializer(
                current_app.secret_key, salt=TEMPORARY_UNLOCK_SALT,
            ).dumps({
                "user_id": actor, "workspace_id": workspace_id, "page_id": page_id,
                "lock_generation": generation,
                "expires_at_ms": int(time.time() * 1000) + 12 * 60 * 60 * 1000,
            })
            response = jsonify({"workspace_id": workspace_id, "page_id": page_id,
                                "lock_generation": generation, "unlock_token": token})
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/appearance",
        methods=["PATCH"],
    )
    def api_notion_page_appearance(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            data = _json_data()
            _exact_json_fields(data, {"updates", "expected_document_sequence",
                                     "expected_workspace_epoch", "expected_document_epoch", "operation_id"})
            result = update_page_appearance(
                _editing_authority(_store()), str(_uid() or ""), workspace_id, page_id, **data,
            )
            return _success("appearance_saved", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/realtime-ticket",
        methods=["POST"],
    )
    def api_notion_collaborative_realtime_ticket(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            _live_authority(store)
            data = _json_data()
            _exact_json_fields(data, {"client_id", "session_binding_sha256"})
            result = iam_runtime.issue_realtime_ticket(
                store,
                str(_uid() or ""),
                workspace_id,
                page_id,
                client_id=_required_text(data, "client_id", max_chars=160),
                session_binding_sha256=_required_text(
                    data, "session_binding_sha256", max_chars=71
                ),
                origin=_request_origin(),
            )
            return _success("realtime_ticket_issued", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
        methods=["GET", "POST"],
    )
    def api_notion_live_comments(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _live_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            if request.method == "GET":
                target_fields = {key: request.args[key] for key in ("database_id", "row_id") if key in request.args}
                if target_fields and set(target_fields) != {"database_id", "row_id"}:
                    raise notion_http.NotionRequestValidationError("database_id and row_id are required together")
                item_target = {"kind": "database_row", **target_fields} if target_fields else None
                result = authority.list_comments(actor, page_id, item_target=item_target)
                return _success("comments_loaded", result)
            data = _json_data()
            allowed = {
                "anchor",
                "block_id",
                "comment_id",
                "content",
                "expected_document_epoch",
                "expected_workspace_epoch",
                "mention_subject_ids",
                "operation_id",
                "parent_comment_id",
            }
            required = {
                "comment_id",
                "content",
                "expected_document_epoch",
                "expected_workspace_epoch",
                "operation_id",
            }
            if not required <= set(data) or not set(data) <= allowed:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the comment contract"
                )
            anchor = data.get("anchor", {})
            if not isinstance(anchor, dict):
                raise notion_http.NotionRequestValidationError(
                    "anchor must be an object"
                )
            result = authority.add_comment(
                actor,
                page_id,
                _required_text(data, "comment_id", max_chars=160),
                content=_required_text(data, "content", max_chars=20_000),
                block_id=_optional_text(data, "block_id", max_chars=160) or None,
                parent_comment_id=(
                    _optional_text(data, "parent_comment_id", max_chars=160) or None
                ),
                anchor=anchor,
                mention_subject_ids=_mention_ids(data),
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=_required_text(data, "operation_id", max_chars=200),
            )
            return _success("comment_created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/editing/commands",
        methods=["GET", "POST"],
    )
    def api_notion_collaborative_editing_command(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _editing_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            if request.method == "GET":
                operation = _required_text(dict(request.args), "operation_id", max_chars=200)
                receipt = authority.typed_command_receipt(actor, page_id, operation)
                response = jsonify({"receipt": receipt})
                response.headers["Cache-Control"] = "no-store"
                return response
            data = _json_data()
            required = {
                "command_kind",
                "target_id",
                "payload",
                "expected_document_sequence",
                "expected_workspace_epoch",
                "expected_document_epoch",
                "operation_id",
            }
            allowed = required | {"expected_target_version"}
            if not required <= set(data) or not set(data) <= allowed:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the editing command contract"
                )
            if not isinstance(data.get("payload"), dict):
                raise notion_http.NotionRequestValidationError(
                    "payload must be an object"
                )
            expected_target = data.get("expected_target_version")
            if expected_target is not None and (
                isinstance(expected_target, bool)
                or not isinstance(expected_target, int)
                or expected_target < 0
            ):
                raise notion_http.NotionRequestValidationError(
                    "expected_target_version must be a non-negative integer or null"
                )
            result = authority.execute_typed_command(
                actor,
                page_id,
                command_kind=_required_text(data, "command_kind", max_chars=100),
                target_id=_required_text(data, "target_id", max_chars=160),
                payload=data["payload"],
                expected_document_sequence=_positive_epoch(
                    data, "expected_document_sequence"
                ),
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                expected_target_version=expected_target,
                operation_id=_required_text(data, "operation_id", max_chars=200),
            )
            return _success("editing_command_committed", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/database-rows/<row_id>/location",
        methods=["GET"],
    )
    def api_notion_collaborative_database_row_location(workspace_id: str, row_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            location = read_owned_row_location(
                _database_authority(_store()), str(_uid() or ""), workspace_id, row_id)
            response = jsonify(location)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>",
        methods=["GET"],
    )
    def api_notion_collaborative_database(
        workspace_id: str, page_id: str, database_id: str
    ):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _database_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            items = authority.read_databases(actor, page_id)
            database = next(
                (item for item in items if str(item.get("id") or "") == database_id),
                None,
            )
            if database is None:
                raise NotFoundOrDenied("not found or permission denied")
            return _success(
                "database_loaded",
                {
                    "contract": "code_assist.notion.collaborative_database_http.v1",
                    "workspace_id": workspace_id,
                    "document_id": page_id,
                    "database": database,
                },
            )
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/commands",
        methods=["POST"],
    )
    def api_notion_collaborative_database_command(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _database_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            data = _json_data()
            required = {
                "database_id",
                "command_kind",
                "target_id",
                "payload",
                "expected_document_sequence",
                "expected_workspace_epoch",
                "expected_document_epoch",
                "operation_id",
            }
            allowed = required | {"expected_target_version"}
            if not required <= set(data) or not set(data) <= allowed:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the database command contract"
                )
            if not isinstance(data.get("payload"), dict):
                raise notion_http.NotionRequestValidationError(
                    "payload must be an object"
                )
            expected_target = data.get("expected_target_version")
            if expected_target is not None and (
                isinstance(expected_target, bool)
                or not isinstance(expected_target, int)
                or expected_target < 0
            ):
                raise notion_http.NotionRequestValidationError(
                    "expected_target_version must be a non-negative integer or null"
                )
            database_id = _required_text(data, "database_id", max_chars=160)
            command_kind = _required_text(data, "command_kind", max_chars=100).lower()
            common = {
                "expected_document_sequence": _positive_epoch(
                    data, "expected_document_sequence"
                ),
                "expected_workspace_epoch": _positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                "expected_document_epoch": _positive_epoch(
                    data, "expected_document_epoch"
                ),
                "expected_target_version": expected_target,
                "operation_id": _required_text(data, "operation_id", max_chars=200),
            }
            if command_kind in SCHEMA_COMMAND_KINDS:
                result = authority.execute_schema_command(
                    actor,
                    page_id,
                    database_id=database_id,
                    command_kind=command_kind,
                    target_id=_required_text(data, "target_id", max_chars=160),
                    payload=data["payload"],
                    **common,
                )
            elif command_kind == "database.relation_pair.enable":
                if store.capabilities().get("collaborative_database_relation_pair") is not True:
                    raise AuthorityUnavailable("database relation pair is not available")
                pair = data["payload"]
                if set(pair) != {"target_document_id", "target_database_id",
                        "inverse_property_id", "inverse_name",
                        "expected_target_sequence", "expected_target_epoch"} \
                        or not common["expected_target_version"]:
                    raise notion_http.NotionRequestValidationError(
                        "relation pair destination and versions are required")
                result = enable_relation_pair(
                    authority, actor, workspace_id, page_id, database_id,
                    _required_text(data, "target_id", max_chars=160),
                    _required_text(pair, "target_document_id", max_chars=160),
                    _required_text(pair, "target_database_id", max_chars=160),
                    _required_text(pair, "inverse_property_id", max_chars=160),
                    _required_text(pair, "inverse_name", max_chars=1000),
                    expected_source_sequence=common["expected_document_sequence"],
                    expected_target_sequence=_positive_epoch(pair, "expected_target_sequence"),
                    expected_workspace_epoch=common["expected_workspace_epoch"],
                    expected_source_epoch=common["expected_document_epoch"],
                    expected_target_epoch=_positive_epoch(pair, "expected_target_epoch"),
                    expected_source_property_version=common["expected_target_version"],
                    operation_id=common["operation_id"],
                )
            elif command_kind == "database.relation_pair.disable":
                if store.capabilities().get("collaborative_database_relation_pair") is not True:
                    raise AuthorityUnavailable("database relation pair is not available")
                pair = data["payload"]
                if set(pair) != {"expected_inverse_sequence",
                        "expected_inverse_epoch", "expected_inverse_property_version"} \
                        or not common["expected_target_version"]:
                    raise notion_http.NotionRequestValidationError(
                        "relation pair versions are required")
                result = disable_relation_pair(
                    authority, actor, workspace_id, page_id, database_id,
                    _required_text(data, "target_id", max_chars=160),
                    expected_retained_sequence=common["expected_document_sequence"],
                    expected_inverse_sequence=_positive_epoch(pair, "expected_inverse_sequence"),
                    expected_workspace_epoch=common["expected_workspace_epoch"],
                    expected_retained_epoch=common["expected_document_epoch"],
                    expected_inverse_epoch=_positive_epoch(pair, "expected_inverse_epoch"),
                    expected_retained_property_version=common["expected_target_version"],
                    expected_inverse_property_version=_positive_epoch(
                        pair, "expected_inverse_property_version"),
                    operation_id=common["operation_id"],
                )
            elif command_kind in SURFACE_COMMAND_KINDS:
                result = authority.execute_surface_command(
                    actor,
                    page_id,
                    database_id=database_id,
                    command_kind=command_kind,
                    target_id=_required_text(data, "target_id", max_chars=160),
                    payload=data["payload"],
                    **common,
                )
            elif command_kind == "database.cells.set":
                result = authority.execute_cell_batch(
                    actor, page_id, database_id=database_id,
                    target_id=_required_text(data, "target_id", max_chars=160),
                    payload=data["payload"], **common,
                )
            elif command_kind == "database.rows.icon.set":
                if store.capabilities().get("collaborative_database_row_icon_batch") is not True:
                    raise AuthorityUnavailable("database-row icon batch is not available")
                result = authority.execute_row_icon_batch(
                    actor, page_id, database_id=database_id,
                    target_id=_required_text(data, "target_id", max_chars=160),
                    payload=data["payload"], **common,
                )
            elif command_kind == "database.rows.duplicate":
                if store.capabilities().get("collaborative_database_row_duplicate_batch") is not True:
                    raise AuthorityUnavailable("database-row duplication is not available")
                result = authority.execute_row_duplicate_batch(
                    actor, page_id, database_id=database_id,
                    target_id=_required_text(data, "target_id", max_chars=160),
                    payload=data["payload"], **common,
                )
            elif command_kind == "database.rows.move":
                if store.capabilities().get("collaborative_database_row_move_batch") is not True:
                    raise AuthorityUnavailable("database-row move is not available")
                if _required_text(data, "target_id", max_chars=160) != database_id \
                        or common["expected_target_version"] is not None:
                    raise notion_http.NotionRequestValidationError(
                        "row move must target its source database")
                destination = data["payload"]
                if set(destination) != {"destination_document_id",
                        "destination_database_id", "expected_destination_sequence",
                        "expected_destination_epoch", "rows"}:
                    raise notion_http.NotionRequestValidationError(
                        "row move destination and rows are required")
                result = move_owned_rows(
                    authority, actor, workspace_id, page_id, database_id,
                    _required_text(destination, "destination_document_id", max_chars=160),
                    _required_text(destination, "destination_database_id", max_chars=160),
                    rows=destination["rows"],
                    expected_source_sequence=common["expected_document_sequence"],
                    expected_destination_sequence=_positive_epoch(
                        destination, "expected_destination_sequence"),
                    expected_workspace_epoch=common["expected_workspace_epoch"],
                    expected_source_epoch=common["expected_document_epoch"],
                    expected_destination_epoch=_positive_epoch(
                        destination, "expected_destination_epoch"),
                    operation_id=common["operation_id"],
                )
            elif command_kind == "database.rows.move_to_page":
                if store.capabilities().get("collaborative_database_row_to_page_batch") is not True:
                    raise AuthorityUnavailable("database-row page move is not available")
                if _required_text(data, "target_id", max_chars=160) != database_id \
                        or common["expected_target_version"] is not None:
                    raise notion_http.NotionRequestValidationError(
                        "row-to-page move must target its source database")
                destination = data["payload"]
                if set(destination) != {"parent_document_id", "expected_parent_sequence",
                        "expected_parent_epoch", "rows"}:
                    raise notion_http.NotionRequestValidationError(
                        "row-to-page parent and rows are required")
                result = move_owned_rows_to_page(
                    authority, actor, workspace_id, page_id, database_id,
                    _required_text(destination, "parent_document_id", max_chars=160),
                    rows=destination["rows"],
                    expected_source_sequence=common["expected_document_sequence"],
                    expected_parent_sequence=_positive_epoch(
                        destination, "expected_parent_sequence"),
                    expected_workspace_epoch=common["expected_workspace_epoch"],
                    expected_source_epoch=common["expected_document_epoch"],
                    expected_parent_epoch=_positive_epoch(
                        destination, "expected_parent_epoch"),
                    operation_id=common["operation_id"],
                )
            elif command_kind == "database.cell.set":
                database = authority.read_database(actor, database_id)
                row_id = str(data["payload"].get("row_id") or "")
                property_id = str(data["payload"].get("property_id") or "")
                if not any(str(item.get("id") or "") == row_id for item in database["rows"]):
                    raise NotFoundOrDenied("not found or permission denied")
                if not any(
                    str(item.get("id") or "") == property_id
                    for item in database["properties"]
                ):
                    raise NotFoundOrDenied("not found or permission denied")
                result = authority.execute_typed_command(
                    actor,
                    page_id,
                    command_kind=command_kind,
                    target_id=_required_text(data, "target_id", max_chars=321),
                    payload=data["payload"],
                    **common,
                )
            else:
                raise notion_http.NotionRequestValidationError(
                    "supported database command required"
                )
            return _success("database_command_committed", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
        methods=["GET", "POST"],
    )
    def api_notion_collaborative_revision_checkpoints(
        workspace_id: str, page_id: str
    ):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _editing_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            if request.method == "GET":
                items = authority.list_revision_checkpoints(actor, page_id)
                return _success(
                    "revision_checkpoints_loaded",
                    {
                        "contract": "code_assist.notion.collaborative_editing_authority.v1",
                        "workspace_id": workspace_id,
                        "document_id": page_id,
                        "items": items,
                        "count": len(items),
                    },
                )
            data = _json_data()
            required = {
                "expected_revision",
                "expected_document_sequence",
                "expected_workspace_epoch",
                "expected_document_epoch",
                "operation_id",
            }
            allowed = required | {"checkpoint_reason", "publication_revision_id"}
            if not required <= set(data) or not set(data) <= allowed:
                raise notion_http.NotionRequestValidationError(
                    "request fields do not match the revision checkpoint contract"
                )
            result = authority.create_revision_checkpoint(
                actor,
                page_id,
                expected_revision=_positive_epoch(data, "expected_revision"),
                expected_document_sequence=_positive_epoch(
                    data, "expected_document_sequence"
                ),
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=_required_text(data, "operation_id", max_chars=200),
                checkpoint_reason=(
                    _optional_text(data, "checkpoint_reason", max_chars=32)
                    or "manual"
                ),
                publication_revision_id=(
                    _optional_text(data, "publication_revision_id", max_chars=160)
                    or None
                ),
            )
            return _success("revision_checkpoint_created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
        methods=["PATCH", "DELETE"],
    )
    def api_notion_live_comment_item(
        workspace_id: str, page_id: str, comment_id: str
    ):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _live_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            data = _json_data()
            common = {
                "expected_row_version": _positive_version(data),
                "expected_workspace_epoch": _positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                "expected_document_epoch": _positive_epoch(
                    data, "expected_document_epoch"
                ),
                "operation_id": _required_text(
                    data, "operation_id", max_chars=200
                ),
            }
            if request.method == "DELETE":
                _exact_json_fields(
                    data,
                    {
                        "expected_document_epoch",
                        "expected_row_version",
                        "expected_workspace_epoch",
                        "operation_id",
                    },
                )
                result = authority.delete_comment(
                    actor, page_id, comment_id, **common
                )
                return _success("comment_deleted", result)
            if "content" in data:
                _exact_json_fields(
                    data,
                    {
                        "content",
                        "expected_document_epoch",
                        "expected_row_version",
                        "expected_workspace_epoch",
                        "mention_subject_ids",
                        "operation_id",
                    },
                )
                result = authority.update_comment(
                    actor,
                    page_id,
                    comment_id,
                    content=_required_text(data, "content", max_chars=20_000),
                    mention_subject_ids=_mention_ids(data),
                    **common,
                )
                return _success("comment_updated", result)
            _exact_json_fields(
                data,
                {
                    "expected_document_epoch",
                    "expected_row_version",
                    "expected_workspace_epoch",
                    "operation_id",
                    "resolved",
                },
            )
            if not isinstance(data.get("resolved"), bool):
                raise notion_http.NotionRequestValidationError(
                    "resolved must be a boolean"
                )
            result = authority.set_comment_resolved(
                actor,
                page_id,
                comment_id,
                resolved=data["resolved"],
                **common,
            )
            return _success("comment_resolution_set", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/"
        "<comment_id>/reactions",
        methods=["POST"],
    )
    def api_notion_live_comment_reaction(
        workspace_id: str, page_id: str, comment_id: str
    ):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _live_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
            data = _json_data()
            _exact_json_fields(
                data,
                {
                    "emoji",
                    "expected_document_epoch",
                    "expected_row_version",
                    "expected_workspace_epoch",
                    "operation_id",
                },
            )
            result = authority.toggle_reaction(
                actor,
                page_id,
                comment_id,
                emoji=_required_text(data, "emoji", max_chars=32),
                expected_row_version=_positive_version(data),
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=_required_text(data, "operation_id", max_chars=200),
            )
            return _success("comment_reaction_toggled", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/inbox",
        methods=["GET"],
    )
    def api_notion_live_inbox(workspace_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            actor = str(_uid() or "")
            sharing_installed = _database_sharing_installed(_database_authority(store))
            if not sharing_installed:
                store.get_workspace(actor, workspace_id)
            raw_limit = request.args.get("limit", "100")
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError) as exc:
                raise notion_http.NotionRequestValidationError(
                    "limit must be an integer"
                ) from exc
            authority = _live_authority(store)
            result = authority.list_inbox(
                actor, workspace_id=workspace_id, limit=limit
            )
            if sharing_installed:
                direct = list_direct_inbox(
                    authority, actor, workspace_id, limit=limit
                )
                items = sorted(
                    [*result["items"], *direct["items"]],
                    key=lambda item: (item["created_at"], item["id"]),
                    reverse=True,
                )[:limit]
                result = {
                    **result, "items": items, "count": len(items),
                    "unread_count": sum(not item["read"] for item in items),
                }
            return _success("inbox_loaded", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/inbox/<notification_id>",
        methods=["PATCH"],
    )
    def api_notion_live_inbox_item(workspace_id: str, notification_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            actor = str(_uid() or "")
            sharing_installed = _database_sharing_installed(_database_authority(store))
            if not sharing_installed:
                store.get_workspace(actor, workspace_id)
            data = _json_data()
            direct_fields = {
                "read", "operation_id", "expected_workspace_epoch",
                "expected_document_epoch", "expected_database_epoch",
                "expected_notification_epoch",
            }
            direct = "expected_database_epoch" in data
            _exact_json_fields(data, direct_fields if direct else {"operation_id", "read"})
            if data.get("read") is not True:
                raise notion_http.NotionRequestValidationError(
                    "read must be true"
                )
            if direct:
                if not sharing_installed:
                    raise NotFoundOrDenied("not found or permission denied")
                result = mark_direct_inbox_read(
                    _live_authority(store), actor, workspace_id, notification_id,
                    expected_workspace_epoch=_positive_epoch(
                        data, "expected_workspace_epoch"),
                    expected_document_epoch=_positive_epoch(
                        data, "expected_document_epoch"),
                    expected_database_epoch=_positive_epoch(
                        data, "expected_database_epoch"),
                    expected_notification_epoch=_positive_epoch(
                        data, "expected_notification_epoch"),
                    operation_id=_required_text(data, "operation_id", max_chars=200),
                )
            else:
                result = _live_authority(store).mark_notification_read(
                    actor,
                    notification_id,
                    expected_workspace_id=workspace_id,
                    operation_id=_required_text(data, "operation_id", max_chars=200),
                )
            return _success("notification_read", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
        methods=["GET", "POST"],
    )
    def api_notion_collaborative_page_shares(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            actor = str(_uid() or "")
            if request.method == "GET":
                result = iam_runtime.list_page_grants(store.iam, actor, page_id)
                if result["workspace_id"] != workspace_id:
                    raise NotFoundOrDenied("not found or permission denied")
                return _success("shares_loaded", result)
            data = _json_data()
            _require_collaborative_page_workspace(
                store, actor, workspace_id, page_id
            )
            principal_type = notion_http.enum_field(
                data,
                "principal_type",
                {"subject", "group", "teamspace"},
                default="subject",
            )
            principal_id = _required_text(
                data, "principal_id", max_chars=160
            )
            role = notion_http.enum_field(
                data, "role", set(PAGE_ROLE_LEVEL), default="view"
            )
            result = store.iam.set_page_grant(
                actor,
                page_id,
                principal_type,
                principal_id,
                role=role,
                inherit_to_descendants=bool(
                    notion_http.boolean_field(
                        data, "inherit_to_descendants", default=False
                    )
                ),
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=_required_text(data, "operation_id", max_chars=128),
            )
            _reconcile_legacy_page_access_projection(store, page_id)
            return _success("share_set", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares/"
        "<principal_type>/<path:principal_id>",
        methods=["DELETE"],
    )
    def api_notion_collaborative_page_share_revoke(
        workspace_id: str,
        page_id: str,
        principal_type: str,
        principal_id: str,
    ):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            data = _json_data()
            _require_collaborative_page_workspace(
                store, str(_uid() or ""), workspace_id, page_id
            )
            result = store.iam.revoke_page_grant(
                str(_uid() or ""),
                page_id,
                principal_type,
                principal_id,
                expected_workspace_epoch=_positive_epoch(
                    data, "expected_workspace_epoch"
                ),
                expected_document_epoch=_positive_epoch(
                    data, "expected_document_epoch"
                ),
                operation_id=_required_text(data, "operation_id", max_chars=128),
            )
            _reconcile_legacy_page_access_projection(store, page_id)
            return _success("share_revoked", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_guest(workspace_id: str, subject_id: str):
        try:
            data = _json_data()
            store = _store()
            common = {
                "base_version": _base_version(data),
                "operation_id": _required_text(data, "operation_id"),
            }
            if request.method == "PUT":
                target, target_active = _subject_status(subject_id)
                result = store.set_guest(
                    _uid(),
                    workspace_id,
                    target,
                    target_active=target_active,
                    **common,
                )
                return _success("guest_set", result)
            result = store.revoke_guest(
                _uid(), workspace_id, safe_user_id(subject_id), **common
            )
            return _success("guest_revoked", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/groups", methods=["POST"])
    def api_notion_workspace_group_create(workspace_id: str):
        try:
            data = _json_data()
            result = _store().create_group(
                _uid(),
                workspace_id,
                name=_required_text(data, "name"),
                base_version=_base_version(data),
                operation_id=_required_text(data, "operation_id"),
            )
            return _success("group_created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_group_member(
        workspace_id: str, group_id: str, subject_id: str
    ):
        try:
            data = _json_data()
            store = _store()
            common = {
                "base_version": _base_version(data),
                "operation_id": _required_text(data, "operation_id"),
            }
            if request.method == "PUT":
                target, target_active = _subject_status(subject_id)
                result = store.set_group_member(
                    _uid(),
                    workspace_id,
                    group_id,
                    target,
                    target_active=target_active,
                    **common,
                )
                if _collaborative_runtime_enabled():
                    _reconcile_legacy_workspace_access_projection(
                        store, workspace_id
                    )
                return _success("group_member_set", result)
            result = store.remove_group_member(
                _uid(),
                workspace_id,
                group_id,
                safe_user_id(subject_id),
                **common,
            )
            if _collaborative_runtime_enabled():
                _reconcile_legacy_workspace_access_projection(
                    store, workspace_id
                )
            return _success("group_member_removed", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>", methods=["GET", "PATCH"])
    def api_notion_teamspace_settings(workspace_id: str, teamspace_id: str):
        try:
            store, actor = _store(), _uid()
            if request.method == "GET":
                response = jsonify(read_teamspace(store, actor, workspace_id, teamspace_id))
                response.headers["Cache-Control"] = "no-store, max-age=0"
                return response
            data = _navigation_json_data()
            _exact_json_fields(data, {"name", "base_version", "operation_id"})
            result = rename_teamspace(store, actor, workspace_id, teamspace_id,
                                     name=_required_text(data, "name"), base_version=_base_version(data),
                                     operation_id=_required_text(data, "operation_id"))
            return _success("teamspace_renamed", result)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route("/api/notion/workspaces/<workspace_id>/teamspaces", methods=["GET", "POST"])
    def api_notion_workspace_teamspaces(workspace_id: str):
        try:
            store = _store()
            if request.method == "GET":
                workspace = store.get_workspace(_uid(), workspace_id)
                response = jsonify(
                    {
                        "workspace_id": workspace["id"],
                        "items": workspace.get("teamspaces") or [],
                        "count": len(workspace.get("teamspaces") or []),
                        "workspace_version": workspace["version"],
                        "membership_epoch": workspace["access"]["epoch"],
                    }
                )
                response.headers["Cache-Control"] = "no-store, max-age=0"
                return response
            data = _json_data()
            result = store.create_teamspace(
                _uid(),
                workspace_id,
                name=_required_text(data, "name"),
                visibility=notion_http.enum_field(
                    data,
                    "visibility",
                    nws.TEAMSPACE_VISIBILITIES,
                    default="open",
                ),
                base_version=_base_version(data),
                operation_id=_required_text(data, "operation_id"),
            )
            return _success("teamspace_created", result, http_status=201)
        except Exception as exc:
            return _workspace_error_response(exc)

    @app.route(
        "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
        methods=["PUT", "DELETE"],
    )
    def api_notion_workspace_teamspace_member(
        workspace_id: str, teamspace_id: str, subject_id: str
    ):
        try:
            data = _json_data()
            store = _store()
            common = {
                "base_version": _base_version(data),
                "operation_id": _required_text(data, "operation_id"),
            }
            if request.method == "PUT":
                target, target_active = _subject_status(subject_id)
                role = notion_http.enum_field(
                    data,
                    "role",
                    nws.TEAMSPACE_ROLES,
                    default="member",
                )
                result = store.set_teamspace_member(
                    _uid(),
                    workspace_id,
                    teamspace_id,
                    target,
                    role=role,
                    target_active=target_active,
                    **common,
                )
                if _collaborative_runtime_enabled():
                    _reconcile_legacy_workspace_access_projection(
                        store, workspace_id
                    )
                return _success("teamspace_member_set", result)
            result = store.remove_teamspace_member(
                _uid(),
                workspace_id,
                teamspace_id,
                safe_user_id(subject_id),
                **common,
            )
            if _collaborative_runtime_enabled():
                _reconcile_legacy_workspace_access_projection(
                    store, workspace_id
                )
            return _success("teamspace_member_removed", result)
        except Exception as exc:
            return _workspace_error_response(exc)
