"""Strict NP-2A workspace route and semantic overlay verifier.

The NP-1 route and semantic manifests remain frozen.  This verifier registers
the workspace registrar on an empty Flask application, compares its exact
surface with the NP-2A overlay, proves that the legacy 59/61/15 baseline still
passes, checks the main/overlay composition for route collisions, and rejects
workspace-overlay registration by ``app_code``.  It never sends a request or
opens a user/workspace database.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from flask import Flask

from code_assist_v1.tools.verify_notion_route_contract import (
    verify_contract as verify_legacy_route_contract,
)
from code_assist_v1.tools.verify_notion_route_semantics import (
    verify_manifest as verify_legacy_route_semantics,
)


SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
CONTRACT_ID = "notion-workspace-route-contract-np2a"
SEMANTIC_MANIFEST_ID = "notion-workspace-route-semantics-np2a"
PHASE = "NP-2A"
REGISTRAR_ID = "notion_workspace"
REGISTRAR_MODULE = "code_assist_v1.routes_notion_workspace"
REGISTRAR_CALLABLE = "register_notion_workspace_routes"
ALLOWED_PREFIXES = ("/api/notion/ui-pilot/readiness", "/api/notion/workspaces")
ALLOWED_METHODS = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
IGNORED_AUTOMATIC_METHODS = frozenset({"HEAD", "OPTIONS"})
EXPECTED_RULE_COUNT = 30
EXPECTED_OPERATION_COUNT = 42
EXPECTED_LEGACY = {
    "route_contract_id": "notion-route-contract-np1b",
    "semantic_manifest_id": "notion-route-semantics-np1c-v2",
    "rule_count": 59,
    "operation_count": 61,
    "hard_operation_count": 15,
}
EXPECTED_INTEGRATION_POLICY = {
    "registered_hosts": ["app_notion"],
    "forbidden_hosts": ["app_code"],
    "combined_main_collision_count": 0,
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "notion_workspace_route_contract.json"
DEFAULT_SEMANTICS = PROJECT_ROOT / "docs" / "notion_workspace_route_semantics.json"
APP_CODE_PATH = PROJECT_ROOT / "app_code.py"

TOP_KEYS = frozenset(
    {
        "schema_version",
        "contract_id",
        "phase",
        "method_policy",
        "registrar",
        "required_hard_routes",
        "legacy_baseline",
        "integration_policy",
    }
)
METHOD_POLICY_KEYS = frozenset({"allowed", "ignored_automatic"})
REGISTRAR_KEYS = frozenset(
    {"id", "module", "callable", "allowed_path_prefixes", "routes"}
)
ROUTE_KEYS = frozenset({"methods", "path", "endpoint"})
HARD_ROUTE_KEYS = frozenset({"id", "method", "path", "endpoint"})
LEGACY_KEYS = frozenset(EXPECTED_LEGACY)
INTEGRATION_KEYS = frozenset(EXPECTED_INTEGRATION_POLICY)
SEMANTIC_TOP_KEYS = frozenset(
    {
        "schema_version",
        "manifest_id",
        "status",
        "route_contract",
        "characterization",
        "vocabulary",
        "error_profiles",
        "required_hard_operations",
        "operations",
    }
)
SEMANTIC_OPERATION_KEYS = frozenset(
    {
        "id",
        "registrar",
        "method",
        "path",
        "endpoint",
        "auth_class",
        "request",
        "success",
        "error_profiles",
    }
)
ENDPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,159}$")


@dataclass(frozen=True, order=True)
class RouteSurface:
    methods: tuple[str, ...]
    path: str
    endpoint: str

    @property
    def key(self) -> tuple[tuple[str, ...], str]:
        return self.methods, self.path

    @property
    def operation_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((method, self.path) for method in self.methods)

    def descriptor(self) -> str:
        return f"{','.join(self.methods)} {self.path} -> {self.endpoint}"


def _hard(method: str, path: str, endpoint: str) -> RouteSurface:
    return RouteSurface((method,), path, endpoint)


HARD_EXPECTATIONS: dict[str, RouteSurface] = {
    "core_ui_pilot_readiness": _hard(
        "GET", "/api/notion/ui-pilot/readiness", "api_notion_core_ui_pilot_readiness"
    ),
    "workspace_create": _hard(
        "POST", "/api/notion/workspaces", "api_notion_workspaces"
    ),
    "workspace_get": _hard(
        "GET", "/api/notion/workspaces/<workspace_id>", "api_notion_workspace_get"
    ),
    "workspace_inbox_get": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/inbox",
        "api_notion_live_inbox",
    ),
    "workspace_inbox_read": _hard(
        "PATCH",
        "/api/notion/workspaces/<workspace_id>/inbox/<notification_id>",
        "api_notion_live_inbox_item",
    ),
    "workspace_group_create": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/groups",
        "api_notion_workspace_group_create",
    ),
    "workspace_group_member_remove": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
        "api_notion_workspace_group_member",
    ),
    "workspace_group_member_set": _hard(
        "PUT",
        "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
        "api_notion_workspace_group_member",
    ),
    "workspace_guest_revoke": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
        "api_notion_workspace_guest",
    ),
    "workspace_guest_set": _hard(
        "PUT",
        "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
        "api_notion_workspace_guest",
    ),
    "workspace_list": _hard(
        "GET", "/api/notion/workspaces", "api_notion_workspaces"
    ),
    "workspace_member_revoke": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
        "api_notion_workspace_member",
    ),
    "workspace_member_set": _hard(
        "PUT",
        "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
        "api_notion_workspace_member",
    ),
    "workspace_navigation_get": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/navigation",
        "api_notion_workspace_navigation",
    ),
    "workspace_navigation_preferences_update": _hard(
        "PATCH",
        "/api/notion/workspaces/<workspace_id>/navigation/preferences",
        "api_notion_workspace_navigation_preferences",
    ),
    "workspace_page_favorite_add": _hard(
        "PUT",
        "/api/notion/workspaces/<workspace_id>/navigation/favorites",
        "api_notion_workspace_navigation_favorites",
    ),
    "workspace_page_favorite_remove": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/navigation/favorites",
        "api_notion_workspace_navigation_favorites",
    ),
    "workspace_page_create": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages",
        "api_notion_workspace_pages",
    ),
    "workspace_page_collaboration_get": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/collaboration",
        "api_notion_collaborative_page",
    ),
    "workspace_page_comment_create": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
        "api_notion_live_comments",
    ),
    "workspace_page_comment_delete": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
        "api_notion_live_comment_item",
    ),
    "workspace_page_comment_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
        "api_notion_live_comments",
    ),
    "workspace_page_comment_reaction": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/"
        "<comment_id>/reactions",
        "api_notion_live_comment_reaction",
    ),
    "workspace_page_comment_update": _hard(
        "PATCH",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
        "api_notion_live_comment_item",
    ),
    "workspace_page_database_command": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/commands",
        "api_notion_collaborative_database_command",
    ),
    "workspace_page_database_get": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>",
        "api_notion_collaborative_database",
    ),
    "workspace_page_editing_command": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/editing/commands",
        "api_notion_collaborative_editing_command",
    ),
    "workspace_page_history_checkpoint_create": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
        "api_notion_collaborative_revision_checkpoints",
    ),
    "workspace_page_history_checkpoint_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
        "api_notion_collaborative_revision_checkpoints",
    ),
    "workspace_page_realtime_ticket": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/realtime-ticket",
        "api_notion_collaborative_realtime_ticket",
    ),
    "workspace_page_duplicate": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate",
        "api_notion_workspace_page_duplicate",
    ),
    "workspace_page_trash": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/trash",
        "api_notion_workspace_page_trash",
    ),
    "workspace_page_share_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
        "api_notion_collaborative_page_shares",
    ),
    "workspace_page_share_revoke": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares/"
        "<principal_type>/<path:principal_id>",
        "api_notion_collaborative_page_share_revoke",
    ),
    "workspace_page_share_set": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
        "api_notion_collaborative_page_shares",
    ),
    "workspace_pages_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/pages",
        "api_notion_workspace_pages",
    ),
    "workspace_teamspace_create": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/teamspaces",
        "api_notion_workspace_teamspaces",
    ),
    "workspace_teamspace_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/teamspaces",
        "api_notion_workspace_teamspaces",
    ),
    "workspace_teamspace_member_remove": _hard(
        "DELETE",
        "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
        "api_notion_workspace_teamspace_member",
    ),
    "workspace_teamspace_member_set": _hard(
        "PUT",
        "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
        "api_notion_workspace_teamspace_member",
    ),
    "workspace_trash_list": _hard(
        "GET",
        "/api/notion/workspaces/<workspace_id>/trash",
        "api_notion_workspace_trash",
    ),
    "workspace_trash_restore": _hard(
        "POST",
        "/api/notion/workspaces/<workspace_id>/trash/<path:page_id>/restore",
        "api_notion_workspace_trash_restore",
    ),
}


EXPECTED_CHARACTERIZATION = {
    "claim": "current_handler_characterization_not_notion_product_parity",
    "prefixes": ["/api/notion/ui-pilot/readiness", "/api/notion/workspaces"],
    "prefix_error_profile": "notion_prefix_http",
    "known_boundaries": {
        "host_scope": (
            "The overlay is registered by app_notion only; app_code and the frozen "
            "Notion bridge registrar do not register it."
        ),
        "legacy_baseline": (
            "The frozen NP-1 surface remains 59 rules, 61 operations, and 15 hard "
            "operations."
        ),
        "security_scope": (
            "Every registered operation is a required hard operation because it "
            "observes readiness or the canonical tenant and IAM boundary."
        ),
        "surface_scope": (
            "This manifest characterizes the implemented UI-pilot readiness, workspace, "
            "page lifecycle and trash, member, guest, group, teamspace, per-member "
            "navigation, collaborative page sharing, comments, presence admission, "
            "server-sequenced editing commands, revision checkpoints, and "
            "permission-fenced inbox handlers; it does not claim "
            "Notion product parity."
        ),
    },
}

EXPECTED_VOCABULARY = {
    "auth_classes": ["authenticated"],
    "transports": ["json_object", "none", "path", "query"],
    "content_types": ["application/json"],
    "methods": ["DELETE", "GET", "PATCH", "POST", "PUT"],
    "error_codes": [
        "FORBIDDEN",
        "authentication_required",
        "collaboration_authority_unavailable",
        "collaboration_epoch_conflict",
        "collaboration_operation_id_conflict",
        "checkpoint_not_ready",
        "comment_revision_conflict",
        "document_sequence_conflict",
        "internal_error",
        "invalid_json_body",
        "invalid_request",
        "method_not_allowed",
        "not_found",
        "not_found_or_permission_denied",
        "password_change_required",
        "payload_too_large",
        "permission_denied",
        "route_not_found",
        "revision_conflict",
        "typed_command_target_conflict",
        "workspace_operation_id_conflict",
        "workspace_revision_conflict",
        "workspace_substrate_unavailable",
    ],
}

EXPECTED_ERROR_PROFILES = [
    {
        "id": "auth_required",
        "errors": [
            {"status": 401, "code": "authentication_required", "wire_code_field": False}
        ],
    },
    {
        "id": "collaboration_conflict",
        "errors": [
            {
                "status": 409,
                "code": "collaboration_epoch_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "comment_revision_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "document_sequence_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "typed_command_target_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "revision_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "checkpoint_not_ready",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "collaboration_operation_id_conflict",
                "wire_code_field": True,
            },
        ],
    },
    {
        "id": "collaboration_standard",
        "errors": [
            {
                "status": 404,
                "code": "not_found_or_permission_denied",
                "wire_code_field": True,
            },
            {
                "status": 503,
                "code": "collaboration_authority_unavailable",
                "wire_code_field": True,
            },
        ],
    },
    {
        "id": "json_body",
        "errors": [
            {"status": 400, "code": "invalid_json_body", "wire_code_field": True}
        ],
    },
    {
        "id": "notion_prefix_http",
        "errors": [
            {"status": 404, "code": "route_not_found", "wire_code_field": True},
            {"status": 405, "code": "method_not_allowed", "wire_code_field": True},
            {"status": 413, "code": "payload_too_large", "wire_code_field": True},
        ],
    },
    {
        "id": "password_change",
        "errors": [
            {
                "status": 403,
                "code": "password_change_required",
                "wire_code_field": False,
            }
        ],
    },
    {
        "id": "payload_limit",
        "errors": [
            {"status": 413, "code": "payload_too_large", "wire_code_field": True}
        ],
    },
    {
        "id": "request_security",
        "errors": [
            {"status": 403, "code": "FORBIDDEN", "wire_code_field": True}
        ],
    },
    {
        "id": "workspace_conflict",
        "errors": [
            {
                "status": 409,
                "code": "workspace_operation_id_conflict",
                "wire_code_field": True,
            },
            {
                "status": 409,
                "code": "workspace_revision_conflict",
                "wire_code_field": True,
            },
        ],
    },
    {
        "id": "workspace_standard",
        "errors": [
            {"status": 400, "code": "invalid_request", "wire_code_field": True},
            {"status": 403, "code": "permission_denied", "wire_code_field": True},
            {"status": 404, "code": "not_found", "wire_code_field": True},
            {"status": 500, "code": "internal_error", "wire_code_field": True},
        ],
    },
    {
        "id": "workspace_unavailable",
        "errors": [
            {
                "status": 503,
                "code": "workspace_substrate_unavailable",
                "wire_code_field": True,
            }
        ],
    },
]

READ_PROFILES = [
    "auth_required",
    "password_change",
    "workspace_standard",
    "workspace_unavailable",
]
WRITE_PROFILES = [
    "auth_required",
    "json_body",
    "password_change",
    "payload_limit",
    "request_security",
    "workspace_conflict",
    "workspace_standard",
    "workspace_unavailable",
]
CREATE_WORKSPACE_PROFILES = [
    "auth_required",
    "json_body",
    "password_change",
    "payload_limit",
    "request_security",
    "workspace_conflict",
    "workspace_standard",
]
COLLABORATION_READ_PROFILES = [*READ_PROFILES, "collaboration_standard"]
COLLABORATION_WRITE_PROFILES = [
    *WRITE_PROFILES,
    "collaboration_conflict",
    "collaboration_standard",
]


def _operation(
    endpoint: str,
    method: str,
    path: str,
    *,
    transport: str,
    required_fields: Sequence[str],
    optional_fields: Sequence[str] | None = None,
    path_fields: Sequence[str],
    status: int | Sequence[int],
    required_keys: Sequence[str],
    profiles: Sequence[str],
) -> dict[str, Any]:
    request_contract: dict[str, Any] = {
        "transport": transport,
        "required_fields": list(required_fields),
        "path_fields": list(path_fields),
    }
    if optional_fields is not None:
        request_contract["optional_fields"] = list(optional_fields)
    return {
        "id": f"{REGISTRAR_ID}.{endpoint}.{method.lower()}",
        "registrar": REGISTRAR_ID,
        "method": method,
        "path": path,
        "endpoint": endpoint,
        "auth_class": "authenticated",
        "request": request_contract,
        "success": {
            "statuses": [status] if isinstance(status, int) else list(status),
            "content_types": ["application/json"],
            "required_keys": list(required_keys),
        },
        "error_profiles": list(profiles),
    }


EXPECTED_OPERATIONS = sorted(
    [
        _operation(
            "api_notion_core_ui_pilot_readiness",
            "GET",
            "/api/notion/ui-pilot/readiness",
            transport="none",
            required_fields=[],
            path_fields=[],
            status=200,
            required_keys=["pilot", "status"],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_page",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/collaboration",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=["blocks", "capabilities", "contract", "files", "page", "revision"],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_editing_command",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/editing/commands",
            transport="json_object",
            required_fields=[
                "command_kind",
                "expected_document_epoch",
                "expected_document_sequence",
                "expected_workspace_epoch",
                "operation_id",
                "payload",
                "target_id",
            ],
            optional_fields=["expected_target_version"],
            path_fields=["workspace_id", "page_id"],
            status=201,
            required_keys=[
                "command_id",
                "command_kind",
                "command_sequence",
                "content_sha256",
                "contract",
                "document_id",
                "document_sequence",
                "effective_role",
                "status",
                "target_id",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_database",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/<database_id>",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id", "page_id", "database_id"],
            status=200,
            required_keys=["contract", "database", "document_id", "status", "workspace_id"],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_database_command",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/databases/commands",
            transport="json_object",
            required_fields=[
                "command_kind",
                "database_id",
                "expected_document_epoch",
                "expected_document_sequence",
                "expected_workspace_epoch",
                "operation_id",
                "payload",
                "target_id",
            ],
            optional_fields=["expected_target_version"],
            path_fields=["workspace_id", "page_id"],
            status=201,
            required_keys=[
                "command_kind",
                "content_sha256",
                "contract",
                "database_id",
                "document_id",
                "document_sequence",
                "effective_role",
                "status",
                "target_id",
                "target_version",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_realtime_ticket",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/realtime-ticket",
            transport="json_object",
            required_fields=["client_id", "session_binding_sha256"],
            path_fields=["workspace_id", "page_id"],
            status=201,
            required_keys=[
                "capabilities",
                "client_id",
                "document_epoch",
                "document_id",
                "expires_at_ms",
                "gateway_url",
                "last_document_sequence",
                "session_binding_sha256",
                "status",
                "subject_id",
                "token",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_revision_checkpoints",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "contract",
                "count",
                "document_id",
                "items",
                "status",
                "workspace_id",
            ],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_revision_checkpoints",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/checkpoints",
            transport="json_object",
            required_fields=[
                "expected_document_epoch",
                "expected_document_sequence",
                "expected_revision",
                "expected_workspace_epoch",
                "operation_id",
            ],
            optional_fields=["checkpoint_reason", "publication_revision_id"],
            path_fields=["workspace_id", "page_id"],
            status=201,
            required_keys=[
                "content_sha256",
                "contract",
                "document_id",
                "document_sequence",
                "identities",
                "revision",
                "status",
                "through_structure_command_sequence",
                "through_update_sequence",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_live_comment_item",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
            transport="json_object",
            required_fields=[
                "expected_document_epoch",
                "expected_row_version",
                "expected_workspace_epoch",
                "operation_id",
            ],
            path_fields=["workspace_id", "page_id", "comment_id"],
            status=200,
            required_keys=[
                "comment_id",
                "deleted",
                "document_epoch",
                "document_id",
                "document_sequence",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_live_comment_item",
            "PATCH",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/<comment_id>",
            transport="json_object",
            required_fields=[
                "expected_document_epoch",
                "expected_row_version",
                "expected_workspace_epoch",
                "operation_id",
            ],
            optional_fields=["content", "mention_subject_ids", "resolved"],
            path_fields=["workspace_id", "page_id", "comment_id"],
            status=200,
            required_keys=[
                "comment_id",
                "document_epoch",
                "document_id",
                "document_sequence",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_live_comment_reaction",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments/"
            "<comment_id>/reactions",
            transport="json_object",
            required_fields=[
                "emoji",
                "expected_document_epoch",
                "expected_row_version",
                "expected_workspace_epoch",
                "operation_id",
            ],
            path_fields=["workspace_id", "page_id", "comment_id"],
            status=200,
            required_keys=[
                "added",
                "comment_id",
                "document_epoch",
                "document_id",
                "document_sequence",
                "emoji",
                "reactions",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_live_comments",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "count",
                "document_epoch",
                "document_id",
                "document_sequence",
                "items",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_live_comments",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/comments",
            transport="json_object",
            required_fields=[
                "comment_id",
                "content",
                "expected_document_epoch",
                "expected_workspace_epoch",
                "operation_id",
            ],
            optional_fields=[
                "anchor",
                "block_id",
                "mention_subject_ids",
                "parent_comment_id",
            ],
            path_fields=["workspace_id", "page_id"],
            status=201,
            required_keys=[
                "comment_id",
                "document_epoch",
                "document_id",
                "document_sequence",
                "notification_count",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_live_inbox",
            "GET",
            "/api/notion/workspaces/<workspace_id>/inbox",
            transport="query",
            required_fields=[],
            optional_fields=["limit"],
            path_fields=["workspace_id"],
            status=200,
            required_keys=["count", "items", "status", "unread_count"],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_live_inbox_item",
            "PATCH",
            "/api/notion/workspaces/<workspace_id>/inbox/<notification_id>",
            transport="json_object",
            required_fields=["operation_id", "read"],
            path_fields=["workspace_id", "notification_id"],
            status=200,
            required_keys=[
                "document_epoch",
                "document_id",
                "notification_id",
                "read",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_page_share_revoke",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares/"
            "<principal_type>/<path:principal_id>",
            transport="json_object",
            required_fields=[
                "expected_document_epoch",
                "expected_workspace_epoch",
                "operation_id",
            ],
            path_fields=["workspace_id", "page_id", "principal_type", "principal_id"],
            status=200,
            required_keys=[
                "document_epoch",
                "document_id",
                "principal_id",
                "principal_type",
                "state",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_page_shares",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "document_epoch",
                "document_id",
                "effective_access",
                "items",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_READ_PROFILES,
        ),
        _operation(
            "api_notion_collaborative_page_shares",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/shares",
            transport="json_object",
            required_fields=[
                "expected_document_epoch",
                "expected_workspace_epoch",
                "operation_id",
                "principal_id",
            ],
            optional_fields=["inherit_to_descendants", "principal_type", "role"],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "document_epoch",
                "document_id",
                "principal_id",
                "principal_type",
                "role",
                "status",
                "workspace_epoch",
                "workspace_id",
            ],
            profiles=COLLABORATION_WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_get",
            "GET",
            "/api/notion/workspaces/<workspace_id>",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id"],
            status=200,
            required_keys=["workspace"],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspace_group_create",
            "POST",
            "/api/notion/workspaces/<workspace_id>/groups",
            transport="json_object",
            required_fields=["base_version", "name", "operation_id"],
            path_fields=["workspace_id"],
            status=201,
            required_keys=["group", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_group_member",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "group_id", "subject_id"],
            status=200,
            required_keys=["group", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_group_member",
            "PUT",
            "/api/notion/workspaces/<workspace_id>/groups/<group_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "group_id", "subject_id"],
            status=200,
            required_keys=["group", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_guest",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "subject_id"],
            status=200,
            required_keys=["revoked_subject_id", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_guest",
            "PUT",
            "/api/notion/workspaces/<workspace_id>/guests/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "subject_id"],
            status=200,
            required_keys=["guest", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_member",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "subject_id"],
            status=200,
            required_keys=["revoked_subject_id", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_member",
            "PUT",
            "/api/notion/workspaces/<workspace_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "subject_id"],
            status=200,
            required_keys=["member", "status", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_navigation",
            "GET",
            "/api/notion/workspaces/<workspace_id>/navigation",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "favorite_version",
                "favorites",
                "membership_epoch",
                "preference_version",
                "sections",
                "sidebar",
                "status",
                "subject_id",
                "workspace_id",
            ],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspace_navigation_favorites",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/navigation/favorites",
            transport="json_object",
            required_fields=[
                "expected_favorite_version",
                "operation_id",
                "source_owner_subject_id",
                "source_page_id",
            ],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "changed",
                "favorite",
                "favorite_version",
                "preference_version",
                "status",
                "subject_id",
                "workspace_id",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_navigation_favorites",
            "PUT",
            "/api/notion/workspaces/<workspace_id>/navigation/favorites",
            transport="json_object",
            required_fields=[
                "expected_favorite_version",
                "operation_id",
                "source_owner_subject_id",
                "source_page_id",
            ],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "changed",
                "favorite",
                "favorite_version",
                "preference_version",
                "status",
                "subject_id",
                "workspace_id",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_navigation_preferences",
            "PATCH",
            "/api/notion/workspaces/<workspace_id>/navigation/preferences",
            transport="json_object",
            required_fields=[
                "expected_preference_version",
                "operation_id",
                "sections",
                "sidebar_collapsed",
                "sidebar_width_px",
            ],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "favorite_version",
                "preference_version",
                "sections",
                "sidebar",
                "status",
                "subject_id",
                "workspace_id",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_page_duplicate",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/duplicate",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "page_id"],
            status=[200, 201],
            required_keys=[
                "bindings",
                "page",
                "replayed",
                "status",
                "subtree_count",
                "workspace",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_page_trash",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/trash",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "archived_count",
                "page_id",
                "replayed",
                "status",
                "workspace",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_pages",
            "GET",
            "/api/notion/workspaces/<workspace_id>/pages",
            transport="query",
            required_fields=[],
            optional_fields=["q"],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "count",
                "degraded",
                "degraded_sources",
                "items",
                "membership_epoch",
                "workspace_id",
            ],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspace_pages",
            "POST",
            "/api/notion/workspaces/<workspace_id>/pages",
            transport="json_object",
            required_fields=["base_version", "operation_id", "parent_id", "title"],
            path_fields=["workspace_id"],
            status=[200, 201],
            required_keys=[
                "binding",
                "page",
                "replayed",
                "status",
                "workspace",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_teamspace_member",
            "DELETE",
            "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "teamspace_id", "subject_id"],
            status=200,
            required_keys=["status", "teamspace_member", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_teamspace_member",
            "PUT",
            "/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>/members/<subject_id>",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "teamspace_id", "subject_id"],
            status=200,
            required_keys=["status", "teamspace_member", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_teamspaces",
            "GET",
            "/api/notion/workspaces/<workspace_id>/teamspaces",
            transport="path",
            required_fields=[],
            path_fields=["workspace_id"],
            status=200,
            required_keys=[
                "count",
                "items",
                "membership_epoch",
                "workspace_id",
                "workspace_version",
            ],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspace_teamspaces",
            "POST",
            "/api/notion/workspaces/<workspace_id>/teamspaces",
            transport="json_object",
            required_fields=["base_version", "name", "operation_id"],
            path_fields=["workspace_id"],
            status=201,
            required_keys=["status", "teamspace", "workspace"],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspace_trash",
            "GET",
            "/api/notion/workspaces/<workspace_id>/trash",
            transport="query",
            required_fields=[],
            optional_fields=["q"],
            path_fields=["workspace_id"],
            status=200,
            required_keys=["count", "items", "membership_epoch", "workspace_id"],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspace_trash_restore",
            "POST",
            "/api/notion/workspaces/<workspace_id>/trash/<path:page_id>/restore",
            transport="json_object",
            required_fields=["base_version", "operation_id"],
            path_fields=["workspace_id", "page_id"],
            status=200,
            required_keys=[
                "page",
                "replayed",
                "restored_count",
                "status",
                "workspace",
            ],
            profiles=WRITE_PROFILES,
        ),
        _operation(
            "api_notion_workspaces",
            "GET",
            "/api/notion/workspaces",
            transport="none",
            required_fields=[],
            path_fields=[],
            status=200,
            required_keys=["capabilities", "count", "items"],
            profiles=READ_PROFILES,
        ),
        _operation(
            "api_notion_workspaces",
            "POST",
            "/api/notion/workspaces",
            transport="json_object",
            required_fields=["name", "operation_id"],
            path_fields=[],
            status=201,
            required_keys=["status", "workspace"],
            profiles=CREATE_WORKSPACE_PROFILES,
        ),
    ],
    key=lambda item: item["id"],
)
EXPECTED_OPERATIONS_BY_ID = {item["id"]: item for item in EXPECTED_OPERATIONS}


class DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def _issue(
    errors: list[dict[str, str]],
    code: str,
    message: str,
    subject: str = "",
) -> None:
    item = {"code": code, "message": message}
    if subject:
        item["subject"] = subject
    errors.append(item)


def _exact_keys(
    errors: list[dict[str, str]],
    value: Any,
    expected: frozenset[str],
    subject: str,
) -> bool:
    if not isinstance(value, dict):
        _issue(errors, "OBJECT_INVALID", "value must be an object", subject)
        return False
    actual = set(value)
    for key in sorted(expected - actual):
        _issue(errors, "MISSING_KEY", f"missing key: {key}", subject)
    for key in sorted(actual - expected):
        _issue(errors, "UNKNOWN_KEY", f"unknown key: {key}", subject)
    return actual == expected


def _safe_overlay_path(path: Any) -> bool:
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    if any(token in path for token in ("..", "\\", "//", "?", "#")):
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in ALLOWED_PREFIXES)


def _route_from_value(
    value: Any,
    index: int,
    errors: list[dict[str, str]],
) -> RouteSurface | None:
    subject = f"registrar.routes[{index}]"
    if not _exact_keys(errors, value, ROUTE_KEYS, subject):
        return None
    methods_value = value.get("methods")
    path = value.get("path")
    endpoint = value.get("endpoint")
    if (
        not isinstance(methods_value, list)
        or not methods_value
        or any(not isinstance(item, str) or item not in ALLOWED_METHODS for item in methods_value)
        or len(set(methods_value)) != len(methods_value)
        or methods_value != sorted(methods_value)
    ):
        _issue(
            errors,
            "METHODS_INVALID",
            "methods must contain unique sorted allowed methods",
            subject,
        )
        return None
    if not _safe_overlay_path(path):
        _issue(errors, "PATH_INVALID", f"invalid overlay path: {path!r}", subject)
        return None
    if not isinstance(endpoint, str) or not ENDPOINT_RE.fullmatch(endpoint):
        _issue(errors, "ENDPOINT_INVALID", f"invalid endpoint: {endpoint!r}", subject)
        return None
    return RouteSurface(tuple(methods_value), path, endpoint)


def _contains_operation(routes: Iterable[RouteSurface], required: RouteSurface) -> bool:
    method = required.methods[0]
    return any(
        route.path == required.path
        and route.endpoint == required.endpoint
        and method in route.methods
        for route in routes
    )


def _validate_contract_manifest(
    manifest: Any,
    errors: list[dict[str, str]],
) -> list[RouteSurface]:
    if not isinstance(manifest, dict):
        _issue(errors, "MANIFEST_ROOT_INVALID", "manifest root must be an object")
        return []
    _exact_keys(errors, manifest, TOP_KEYS, "manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _issue(errors, "SCHEMA_VERSION_INVALID", "schema_version must be 1")
    if manifest.get("contract_id") != CONTRACT_ID:
        _issue(errors, "CONTRACT_ID_INVALID", f"contract_id must be {CONTRACT_ID}")
    if manifest.get("phase") != PHASE:
        _issue(errors, "PHASE_INVALID", f"phase must be {PHASE}")

    method_policy = manifest.get("method_policy")
    if _exact_keys(errors, method_policy, METHOD_POLICY_KEYS, "method_policy"):
        if method_policy.get("allowed") != sorted(ALLOWED_METHODS):
            _issue(errors, "METHOD_POLICY_INVALID", "allowed methods mismatch")
        if method_policy.get("ignored_automatic") != sorted(IGNORED_AUTOMATIC_METHODS):
            _issue(errors, "METHOD_POLICY_INVALID", "automatic methods mismatch")

    routes: list[RouteSurface] = []
    registrar = manifest.get("registrar")
    if _exact_keys(errors, registrar, REGISTRAR_KEYS, "registrar"):
        expected_target = {
            "id": REGISTRAR_ID,
            "module": REGISTRAR_MODULE,
            "callable": REGISTRAR_CALLABLE,
            "allowed_path_prefixes": list(ALLOWED_PREFIXES),
        }
        for key, expected in expected_target.items():
            if registrar.get(key) != expected:
                _issue(
                    errors,
                    "REGISTRAR_TARGET_MISMATCH",
                    f"{key} must equal {expected!r}",
                    f"registrar.{key}",
                )
        route_values = registrar.get("routes")
        if not isinstance(route_values, list):
            _issue(errors, "ROUTES_INVALID", "routes must be an array", "registrar.routes")
        else:
            for index, value in enumerate(route_values):
                route = _route_from_value(value, index, errors)
                if route is not None:
                    routes.append(route)
            if routes != sorted(routes):
                _issue(errors, "ROUTE_ORDER_INVALID", "routes must be deterministically sorted")
            if len(routes) != EXPECTED_RULE_COUNT:
                _issue(
                    errors,
                    "ROUTE_COUNT_INVALID",
                    f"expected {EXPECTED_RULE_COUNT} routes, found {len(routes)}",
                )
            operation_keys = [key for route in routes for key in route.operation_keys]
            if len(operation_keys) != len(set(operation_keys)):
                _issue(errors, "DUPLICATE_OPERATION", "duplicate method/path operation")
            if len(operation_keys) != EXPECTED_OPERATION_COUNT:
                _issue(
                    errors,
                    "OPERATION_COUNT_INVALID",
                    f"expected {EXPECTED_OPERATION_COUNT} operations, found {len(operation_keys)}",
                )
            endpoint_paths: dict[str, str] = {}
            for route in routes:
                previous = endpoint_paths.setdefault(route.endpoint, route.path)
                if previous != route.path:
                    _issue(
                        errors,
                        "ENDPOINT_REUSED",
                        f"endpoint is reused by multiple paths: {route.endpoint}",
                    )

    hard_values = manifest.get("required_hard_routes")
    normalized_hard: dict[str, RouteSurface] = {}
    if not isinstance(hard_values, list):
        _issue(errors, "HARD_ROUTES_INVALID", "required_hard_routes must be an array")
    else:
        hard_ids: list[str] = []
        for index, value in enumerate(hard_values):
            subject = f"required_hard_routes[{index}]"
            if not _exact_keys(errors, value, HARD_ROUTE_KEYS, subject):
                continue
            identifier = value.get("id")
            method = value.get("method")
            path = value.get("path")
            endpoint = value.get("endpoint")
            if not isinstance(identifier, str) or not IDENTIFIER_RE.fullmatch(identifier):
                _issue(errors, "HARD_ID_INVALID", f"invalid hard route id: {identifier!r}", subject)
                continue
            hard_ids.append(identifier)
            if (
                not isinstance(method, str)
                or method not in ALLOWED_METHODS
                or not _safe_overlay_path(path)
                or not isinstance(endpoint, str)
                or not ENDPOINT_RE.fullmatch(endpoint)
            ):
                _issue(errors, "HARD_ROUTE_INVALID", "invalid hard route descriptor", subject)
                continue
            normalized_hard[identifier] = RouteSurface((method,), path, endpoint)
        if hard_ids != sorted(hard_ids):
            _issue(errors, "HARD_ROUTE_ORDER_INVALID", "hard routes must be sorted by id")
        if set(normalized_hard) != set(HARD_EXPECTATIONS):
            _issue(errors, "HARD_ROUTE_SET_INVALID", "required hard route IDs mismatch")
        for identifier, expected in HARD_EXPECTATIONS.items():
            if normalized_hard.get(identifier) != expected:
                _issue(
                    errors,
                    "HARD_ROUTE_MISMATCH",
                    f"hard route does not match {expected.descriptor()}",
                    identifier,
                )
            if not _contains_operation(routes, expected):
                _issue(errors, "HARD_ROUTE_MISSING", "hard route is absent from routes", identifier)

    legacy = manifest.get("legacy_baseline")
    if _exact_keys(errors, legacy, LEGACY_KEYS, "legacy_baseline"):
        if legacy != EXPECTED_LEGACY:
            _issue(errors, "LEGACY_BASELINE_INVALID", "legacy baseline must remain 59/61/15")
    integration = manifest.get("integration_policy")
    if _exact_keys(errors, integration, INTEGRATION_KEYS, "integration_policy"):
        if integration != EXPECTED_INTEGRATION_POLICY:
            _issue(errors, "INTEGRATION_POLICY_INVALID", "integration policy mismatch")
    return routes


def _expected_hard_operations() -> list[dict[str, str]]:
    return [
        {
            "id": identifier,
            "operation_id": f"{REGISTRAR_ID}.{route.endpoint}.{route.methods[0].lower()}",
        }
        for identifier, route in sorted(HARD_EXPECTATIONS.items())
    ]


def _validate_semantics(
    semantic: Any,
    routes: Sequence[RouteSurface],
    errors: list[dict[str, str]],
) -> int:
    if not isinstance(semantic, dict):
        _issue(errors, "SEMANTIC_ROOT_INVALID", "semantic manifest root must be an object")
        return 0
    _exact_keys(errors, semantic, SEMANTIC_TOP_KEYS, "semantics")
    if semantic.get("schema_version") != SCHEMA_VERSION:
        _issue(errors, "SEMANTIC_SCHEMA_INVALID", "semantic schema_version must be 1")
    if semantic.get("manifest_id") != SEMANTIC_MANIFEST_ID:
        _issue(errors, "SEMANTIC_ID_INVALID", "semantic manifest_id mismatch")
    if semantic.get("status") != "characterization_manifest":
        _issue(errors, "SEMANTIC_STATUS_INVALID", "status must be characterization_manifest")
    if semantic.get("route_contract") != {
        "contract_id": CONTRACT_ID,
        "path": "docs/notion_workspace_route_contract.json",
    }:
        _issue(errors, "SEMANTIC_CONTRACT_REF_INVALID", "route contract reference mismatch")
    if semantic.get("characterization") != EXPECTED_CHARACTERIZATION:
        _issue(errors, "CHARACTERIZATION_INVALID", "characterization boundary mismatch")
    if semantic.get("vocabulary") != EXPECTED_VOCABULARY:
        _issue(errors, "VOCABULARY_INVALID", "semantic vocabulary mismatch")
    if semantic.get("error_profiles") != EXPECTED_ERROR_PROFILES:
        _issue(errors, "ERROR_PROFILES_INVALID", "error profile definitions mismatch")

    operation_values = semantic.get("operations")
    normalized: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    if not isinstance(operation_values, list):
        _issue(errors, "SEMANTIC_OPERATIONS_INVALID", "operations must be an array")
    else:
        for index, value in enumerate(operation_values):
            subject = f"operations[{index}]"
            if not _exact_keys(errors, value, SEMANTIC_OPERATION_KEYS, subject):
                continue
            identifier = value.get("id")
            if not isinstance(identifier, str) or not IDENTIFIER_RE.fullmatch(identifier):
                _issue(errors, "OPERATION_ID_INVALID", f"invalid operation id: {identifier!r}", subject)
                continue
            if identifier in normalized:
                _issue(errors, "DUPLICATE_SEMANTIC_OPERATION", "duplicate operation id", identifier)
                continue
            ordered_ids.append(identifier)
            normalized[identifier] = value
        if ordered_ids != sorted(ordered_ids):
            _issue(errors, "SEMANTIC_OPERATION_ORDER_INVALID", "operations must be sorted by id")
        if set(normalized) != set(EXPECTED_OPERATIONS_BY_ID):
            _issue(errors, "SEMANTIC_OPERATION_SET_INVALID", "semantic operation IDs mismatch")
        for identifier, expected in EXPECTED_OPERATIONS_BY_ID.items():
            actual = normalized.get(identifier)
            if actual is not None and actual != expected:
                _issue(
                    errors,
                    "SEMANTIC_OPERATION_MISMATCH",
                    "operation semantics differ from the implemented handler boundary",
                    identifier,
                )

    route_operations = {
        (method, route.path, route.endpoint)
        for route in routes
        for method in route.methods
    }
    semantic_operations = {
        (str(item.get("method")), str(item.get("path")), str(item.get("endpoint")))
        for item in normalized.values()
    }
    if route_operations != semantic_operations:
        _issue(errors, "SEMANTIC_ROUTE_LINK_INVALID", "semantic operations do not exactly cover routes")

    expected_hard = _expected_hard_operations()
    if semantic.get("required_hard_operations") != expected_hard:
        _issue(errors, "HARD_OPERATIONS_INVALID", "required hard operation mapping mismatch")
    else:
        for item in expected_hard:
            if item["operation_id"] not in normalized:
                _issue(
                    errors,
                    "HARD_OPERATION_MISSING",
                    "required hard operation is absent",
                    item["id"],
                )
    return len(normalized)


def _load_registrar(module_name: str, callable_name: str) -> Callable[[Flask], Any]:
    with redirect_stdout(io.StringIO()):
        module = importlib.import_module(module_name)
    registrar = getattr(module, callable_name, None)
    if not callable(registrar):
        raise RuntimeError(f"route registrar is not callable: {module_name}.{callable_name}")
    return registrar


def collect_registrar_routes(
    registrar: Callable[[Flask], Any] | None = None,
    *,
    module_name: str = REGISTRAR_MODULE,
    callable_name: str = REGISTRAR_CALLABLE,
) -> list[RouteSurface]:
    app = Flask(f"route-surface-{callable_name}", static_folder=None)
    (registrar or _load_registrar(module_name, callable_name))(app)
    routes: list[RouteSurface] = []
    for rule in app.url_map.iter_rules():
        methods = tuple(sorted(set(rule.methods or ()) - IGNORED_AUTOMATIC_METHODS))
        routes.append(RouteSurface(methods, str(rule), str(rule.endpoint)))
    return sorted(routes)


def _compare_actual_surface(
    expected: Sequence[RouteSurface],
    actual: Sequence[RouteSurface],
    errors: list[dict[str, str]],
) -> None:
    if len(actual) != EXPECTED_RULE_COUNT:
        _issue(
            errors,
            "ACTUAL_ROUTE_COUNT_INVALID",
            f"expected {EXPECTED_RULE_COUNT} actual routes, found {len(actual)}",
        )
    expected_by_key = {item.key: item for item in expected}
    actual_by_key = {item.key: item for item in actual}
    for key in sorted(set(expected_by_key) - set(actual_by_key)):
        _issue(
            errors,
            "MISSING_ACTUAL_ROUTE",
            expected_by_key[key].descriptor(),
        )
    for key in sorted(set(actual_by_key) - set(expected_by_key)):
        _issue(
            errors,
            "UNEXPECTED_ACTUAL_ROUTE",
            actual_by_key[key].descriptor(),
        )
    for key in sorted(set(expected_by_key) & set(actual_by_key)):
        if expected_by_key[key].endpoint != actual_by_key[key].endpoint:
            _issue(
                errors,
                "ACTUAL_ENDPOINT_MISMATCH",
                f"expected {expected_by_key[key].endpoint}, found {actual_by_key[key].endpoint}",
            )
    for route in actual:
        if not _safe_overlay_path(route.path):
            _issue(errors, "ACTUAL_PATH_ESCAPE", route.descriptor())
        for method in route.methods:
            if method not in ALLOWED_METHODS:
                _issue(errors, "ACTUAL_METHOD_INVALID", route.descriptor())
    for identifier, required in HARD_EXPECTATIONS.items():
        if not _contains_operation(actual, required):
            _issue(errors, "HARD_ROUTE_ACTUAL_MISSING", required.descriptor(), identifier)


def _validate_legacy_baseline(errors: list[dict[str, str]]) -> None:
    route_report, route_exit = verify_legacy_route_contract()
    route_summary = route_report.get("summary") or {}
    if route_exit != 0 or {
        "expected_route_count": route_summary.get("expected_route_count"),
        "actual_route_count": route_summary.get("actual_route_count"),
        "required_hard_route_count": route_summary.get("required_hard_route_count"),
    } != {
        "expected_route_count": 59,
        "actual_route_count": 59,
        "required_hard_route_count": 15,
    }:
        _issue(
            errors,
            "LEGACY_ROUTE_BASELINE_FAILED",
            json.dumps(route_report.get("errors") or [], sort_keys=True),
        )
    semantic_report, semantic_exit = verify_legacy_route_semantics()
    semantic_summary = semantic_report.get("summary") or {}
    if semantic_exit != 0 or {
        "rule_count": semantic_summary.get("rule_count"),
        "operation_count": semantic_summary.get("operation_count"),
        "hard_operation_count": semantic_summary.get("hard_operation_count"),
    } != {
        "rule_count": 59,
        "operation_count": 61,
        "hard_operation_count": 15,
    }:
        _issue(
            errors,
            "LEGACY_SEMANTIC_BASELINE_FAILED",
            json.dumps(semantic_report.get("errors") or [], sort_keys=True),
        )


def surface_collision_count(
    main_routes: Sequence[RouteSurface],
    overlay_routes: Sequence[RouteSurface],
) -> int:
    """Count forbidden method/path and endpoint collisions between registrars."""
    main_operations = {
        (method, route.path) for route in main_routes for method in route.methods
    }
    overlay_operations = {
        (method, route.path) for route in overlay_routes for method in route.methods
    }
    method_path_collisions = main_operations & overlay_operations
    endpoint_collisions = {route.endpoint for route in main_routes} & {
        route.endpoint for route in overlay_routes
    }
    return len(method_path_collisions) + len(endpoint_collisions)


def _main_overlay_collision_count(errors: list[dict[str, str]]) -> int:
    try:
        main_routes = collect_registrar_routes(
            module_name="code_assist_v1.routes_notion",
            callable_name="register_notion_routes",
        )
        overlay_routes = collect_registrar_routes()
    except Exception as exc:
        _issue(errors, "COLLISION_COLLECTION_FAILED", str(exc))
        return -1
    count = surface_collision_count(main_routes, overlay_routes)
    if count:
        _issue(
            errors,
            "MAIN_OVERLAY_COLLISION",
            f"found {count} method/path or endpoint collisions",
        )

    # Register both together as Flask will see them in production.  The exact
    # count proves that neither registrar silently replaced or omitted a rule.
    app = Flask("notion-main-overlay-composition", static_folder=None)
    _load_registrar("code_assist_v1.routes_notion", "register_notion_routes")(app)
    _load_registrar(REGISTRAR_MODULE, REGISTRAR_CALLABLE)(app)
    combined_count = len(list(app.url_map.iter_rules()))
    if combined_count != len(main_routes) + len(overlay_routes):
        _issue(
            errors,
            "COMBINED_ROUTE_COUNT_INVALID",
            f"expected {len(main_routes) + len(overlay_routes)}, found {combined_count}",
        )
    return count


def _app_code_overlay_registration_count(errors: list[dict[str, str]]) -> int:
    try:
        source = APP_CODE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(APP_CODE_PATH))
    except Exception as exc:
        _issue(errors, "APP_CODE_AUDIT_FAILED", str(exc))
        return -1
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == REGISTRAR_MODULE:
                    findings.append(f"import:{getattr(node, 'lineno', 0)}")
        elif isinstance(node, ast.ImportFrom):
            imports_module = node.module == REGISTRAR_MODULE or (
                node.module == "code_assist_v1"
                and any(alias.name == "routes_notion_workspace" for alias in node.names)
            )
            imports_callable = any(
                alias.name == REGISTRAR_CALLABLE for alias in node.names
            )
            if imports_module or imports_callable:
                findings.append(f"from-import:{getattr(node, 'lineno', 0)}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name == REGISTRAR_CALLABLE:
                findings.append(f"call:{getattr(node, 'lineno', 0)}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in {REGISTRAR_MODULE, REGISTRAR_CALLABLE}:
                findings.append(f"dynamic-reference:{getattr(node, 'lineno', 0)}")
    if findings:
        _issue(
            errors,
            "APP_CODE_OVERLAY_REGISTERED",
            "app_code must not import or call the workspace overlay registrar",
            ",".join(sorted(findings)),
        )
    return len(findings)


def _report(
    errors: Sequence[dict[str, str]],
    *,
    expected_routes: Sequence[RouteSurface],
    actual_routes: Sequence[RouteSurface],
    semantic_operation_count: int,
    collision_count: int,
    app_code_registration_count: int,
) -> dict[str, Any]:
    ordered_errors = sorted(
        (dict(item) for item in errors),
        key=lambda item: (
            item.get("code", ""),
            item.get("subject", ""),
            item.get("message", ""),
        ),
    )
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "semantic_manifest_id": SEMANTIC_MANIFEST_ID,
        "phase": PHASE,
        "status": "pass" if not ordered_errors else "fail",
        "summary": {
            "expected_route_count": len(expected_routes),
            "actual_route_count": len(actual_routes),
            "operation_count": semantic_operation_count,
            "required_hard_operation_count": len(HARD_EXPECTATIONS),
            "legacy_rule_count": 59,
            "legacy_operation_count": 61,
            "legacy_hard_operation_count": 15,
            "main_overlay_collision_count": collision_count,
            "app_code_overlay_registration_count": app_code_registration_count,
            "error_count": len(ordered_errors),
        },
        "errors": ordered_errors,
    }


def validate_manifest_data(
    manifest: Any,
    semantic_manifest: Any,
    *,
    actual_routes: Sequence[RouteSurface] | None = None,
    check_runtime_invariants: bool = True,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    expected_routes = _validate_contract_manifest(manifest, errors)
    semantic_count = _validate_semantics(semantic_manifest, expected_routes, errors)

    if actual_routes is None and check_runtime_invariants:
        try:
            actual_routes = collect_registrar_routes()
        except Exception as exc:
            _issue(errors, "ACTUAL_ROUTE_COLLECTION_FAILED", str(exc))
            actual_routes = []
    elif actual_routes is None:
        actual_routes = expected_routes
    actual_routes = sorted(actual_routes)
    _compare_actual_surface(expected_routes, actual_routes, errors)

    collision_count = 0
    app_code_registration_count = 0
    if check_runtime_invariants:
        _validate_legacy_baseline(errors)
        collision_count = _main_overlay_collision_count(errors)
        app_code_registration_count = _app_code_overlay_registration_count(errors)

    report = _report(
        errors,
        expected_routes=expected_routes,
        actual_routes=actual_routes,
        semantic_operation_count=semantic_count,
        collision_count=collision_count,
        app_code_registration_count=app_code_registration_count,
    )
    return report, 0 if not errors else 2


def verify_contract(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    semantic_path: str | Path = DEFAULT_SEMANTICS,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        _issue(errors, "MANIFEST_LOAD_FAILED", str(exc))
        manifest = {}
    try:
        semantic_manifest = load_manifest(semantic_path)
    except Exception as exc:
        _issue(errors, "SEMANTIC_LOAD_FAILED", str(exc))
        semantic_manifest = {}
    if errors:
        report = _report(
            errors,
            expected_routes=[],
            actual_routes=[],
            semantic_operation_count=0,
            collision_count=0,
            app_code_registration_count=0,
        )
        return report, 2
    return validate_manifest_data(manifest, semantic_manifest)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the strict NP-2A Notion workspace route overlay"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--semantics", type=Path, default=DEFAULT_SEMANTICS)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_contract(args.manifest, args.semantics)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
