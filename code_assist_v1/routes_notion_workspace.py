"""NP-2A workspace/IAM routes registered only by the Notion host.

The legacy Notion and Code bridge registrars stay untouched.  These routes use
the injected Notion test/runtime data root to locate the independent canonical
workspace catalog and never scan per-user directories.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

from flask import jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from code_assist_v1 import notion_http
from code_assist_v1 import notion_core_ui_pilot
from code_assist_v1 import notion_store as legacy_notion_store
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1 import notion_workspace_pages
from code_assist_v1.auth import current_user_id, list_public_users, safe_user_id
from code_assist_v1.notion_collaboration import iam_runtime
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
            workspace = _store().get_workspace(_uid(), workspace_id)
            response = jsonify({"workspace": workspace})
            response.headers["Cache-Control"] = "no-store, max-age=0"
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
                    {"base_version", "operation_id", "parent_id", "title"},
                )
                result = notion_workspace_pages.create_workspace_page(
                    _store(),
                    str(_uid() or ""),
                    workspace_id,
                    title=_required_text(data, "title", max_chars=160),
                    parent_id=_optional_text(data, "parent_id", max_chars=160),
                    base_version=_base_version(data),
                    operation_id=_required_text(data, "operation_id", max_chars=128),
                )
                if _collaborative_runtime_enabled():
                    page_snapshot = legacy_notion_store.get_page(
                        str(_uid() or ""), str(result["page"]["id"])
                    )
                    iam_runtime.ensure_collaborative_document(
                        _store(),
                        str(_uid() or ""),
                        workspace_id,
                        page_snapshot["page"],
                        page_snapshot.get("blocks") or [],
                        source_operation_id=_required_text(
                            data, "operation_id", max_chars=128
                        ),
                    )
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

    @app.route(
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate",
        methods=["POST"],
    )
    def api_notion_workspace_page_duplicate(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
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
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/trash",
        methods=["POST"],
    )
    def api_notion_workspace_page_trash(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
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
            result = notion_workspace_pages.list_workspace_trash(
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

    @app.route(
        "/api/notion/workspaces/<workspace_id>/trash/<path:page_id>/restore",
        methods=["POST"],
    )
    def api_notion_workspace_trash_restore(workspace_id: str, page_id: str):
        try:
            if not _navigation_ui_enabled():
                raise FileNotFoundError("not found")
            data = _navigation_json_data()
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
                result = authority.list_comments(actor, page_id)
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
        methods=["POST"],
    )
    def api_notion_collaborative_editing_command(workspace_id: str, page_id: str):
        try:
            if not _collaborative_runtime_enabled():
                raise FileNotFoundError("not found")
            store = _store()
            authority = _editing_authority(store)
            actor = str(_uid() or "")
            _require_collaborative_page_workspace(store, actor, workspace_id, page_id)
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
            store.get_workspace(actor, workspace_id)
            raw_limit = request.args.get("limit", "100")
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError) as exc:
                raise notion_http.NotionRequestValidationError(
                    "limit must be an integer"
                ) from exc
            result = _live_authority(store).list_inbox(
                actor, workspace_id=workspace_id, limit=limit
            )
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
            store.get_workspace(actor, workspace_id)
            data = _json_data()
            _exact_json_fields(data, {"operation_id", "read"})
            if data.get("read") is not True:
                raise notion_http.NotionRequestValidationError(
                    "read must be true"
                )
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
