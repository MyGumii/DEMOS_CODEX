from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Blueprint, Flask
import pytest

from code_assist_v1 import auth
from code_assist_v1.knowledge_core.admin_routes import (
    MAX_ADMIN_JSON_BYTES,
    register_admin_routes,
)
from code_assist_v1.knowledge_core.services.organization import (
    OrganizationMutationResult,
    OrganizationRevisionConflictError,
)
from code_assist_v1.tools.contract_audit import ContractAuditor


pytestmark = [pytest.mark.integration]

_ORIGIN = "https://agent.example.test"
_PASSWORD = "Correct-Horse-Organization-42"
_ROOT = "fb90d31e-9576-5823-84ef-241b7552f214"
_TEAM = "11111111-1111-4111-8111-111111111111"
_PART = "22222222-2222-4222-8222-222222222222"
_PART_2 = "33333333-3333-4333-8333-333333333333"
_ASSIGNMENT = "44444444-4444-4444-8444-444444444444"
_SPACE = "55555555-5555-4555-8555-555555555555"


@dataclass(frozen=True)
class _AccountResult:
    operation_id: str
    account_status: str
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "account_status": self.account_status,
            "operation_id": self.operation_id,
            "row_version": self.row_version,
        }


class _FakeOrganizationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error: Exception | None = None
        self.results: dict[tuple[str, str], OrganizationMutationResult] = {}

    def _mutation(self, name: str, command: object, *, status: str | None = None):
        self.calls.append((name, command))
        if self.error is not None:
            raise self.error
        key = (name, command.operation_id)
        if key in self.results:
            stored = self.results[key]
            return OrganizationMutationResult(
                request_id=command.request_id,
                operation_id=stored.operation_id,
                status=stored.status,
                resource_id=stored.resource_id,
                row_version=stored.row_version,
            )
        result = OrganizationMutationResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            status=status or name,
            resource_id=getattr(command, "unit_id", None) or getattr(command, "part_id", None),
            row_version=2,
        )
        self.results[key] = result
        return result

    def get_tree(self, **kwargs):
        self.calls.append(("get_tree", SimpleNamespace(**kwargs)))
        if self.error is not None:
            raise self.error
        return {
            "request_id": kwargs["request_id"],
            "root": {
                "id": _ROOT,
                "parent_id": None,
                "kind": "responsibility",
                "name": "AMHS담당",
                "status": "active",
                "member_count": 0,
                "leader": None,
                "space_status": "future_only",
                "row_version": 1,
                "children": [],
                "warnings": [],
            },
            "users": [
                {
                    "id": "admin",
                    "display_name": "Administrator",
                    "system_role": "admin",
                    "account_status": "active",
                    "current_part_id": None,
                    "current_membership_id": None,
                    "scope_epoch": 1,
                    "row_version": 1,
                }
            ],
        }

    def preview_change(self, command):
        self.calls.append(("preview_change", command))
        if self.error is not None:
            raise self.error
        return {
            "request_id": command.request_id,
            "unit_id": command.unit_id,
            "allowed": True,
            "blockers": [],
            "member_impact": {
                "current_member_count": 0,
                "descendant_member_count": 0,
                "member_ids": [],
                "members": [],
            },
            "leader_impact": {
                "leader_assignment_id": None,
                "leader_user_id": None,
                "will_close": False,
            },
            "space_impact": {
                "space_id": None,
                "row_version": None,
                "space_status": "not_created",
                "write_enabled": False,
                "knowledge_item_count": 0,
            },
            "preview_token": "p" * 64,
            "expires_at": "2026-08-13T00:05:00Z",
        }

    def create_unit(self, command):
        return self._mutation("create_unit", command)

    def rename_unit(self, command):
        return self._mutation("rename_unit", command)

    def move_unit(self, command):
        return self._mutation("move_unit", command)

    def inactivate_unit(self, command):
        return self._mutation("inactivate_unit", command)

    def set_membership(self, command):
        return self._mutation("set_membership", command, status="membership_set")

    def set_leader(self, command):
        return self._mutation("set_leader", command, status="leader_set")


class _FakeAccountService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def disable_account(self, command):
        self.calls.append(("disable_account", command))
        return _AccountResult(command.operation_id, "disabled", 9)

    def reenable_account(self, command):
        self.calls.append(("reenable_account", command))
        return _AccountResult(command.operation_id, "active", 10)


class _FakePurgeQueueService:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def list_eligible(self, command):
        self.calls.append(command)
        return {
            "has_more": False,
            "items": [
                {
                    "blocking_reference_count": 2,
                    "deactivated_at": "2026-05-01T00:00:00.000Z",
                    "item_id": "66666666-6666-4666-8666-666666666666",
                    "purge_eligible_at": "2026-07-30T00:00:00.000Z",
                    "space_id": _SPACE,
                    "title": "Retired guidance",
                }
            ],
            "next_cursor": None,
            "request_id": command.request_id,
        }


def _principal(*, role: str = "admin", epoch: int = 17):
    user = auth.current_user()
    if user is None:
        return None
    return {"id": user["id"], "scope_epoch": epoch, "role": role}


def _make_app(
    organization: _FakeOrganizationService | None = None,
    accounts: _FakeAccountService | None = None,
    *,
    role: str = "admin",
    purge: _FakePurgeQueueService | None = None,
) -> tuple[Flask, _FakeOrganizationService, _FakeAccountService]:
    organization = organization or _FakeOrganizationService()
    accounts = accounts or _FakeAccountService()
    app = Flask(f"admin-org-api-{uuid4().hex}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="integration-only-secret-key-that-is-over-32-bytes",
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
        CODE_ASSIST_BROWSER_SECURITY_PRODUCTION=False,
        SESSION_COOKIE_NAME="session",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    auth.register_auth_routes(app)
    register_admin_routes(
        app,
        organization,
        accounts,
        lambda: _principal(role=role),
        purge_service=purge,
    )
    return app, organization, accounts


def _login(app: Flask, *, role: str = "admin"):
    uid = f"org-api-{uuid4().hex[:12]}"
    auth.create_user(uid, _PASSWORD, display_name="Org API", role=role)
    client = app.test_client()
    response = client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": uid, "password": _PASSWORD},
        headers={"Origin": _ORIGIN},
    )
    assert response.status_code == 200, response.get_json()
    token = response.get_json()["csrf_token"]
    return client, uid, {"Origin": _ORIGIN, "X-CSRF-Token": token}


def _op() -> str:
    return str(uuid4())


def _status_body(operation_id: str | None = None) -> dict[str, object]:
    return {
        "operation_id": operation_id or _op(),
        "expected_user_row_version": 8,
        "expected_current_leader_assignment_id": _ASSIGNMENT,
        "expected_part_row_version": 12,
        "replacement_leader_user_id": "member02",
        "reason": "관리자 승인",
    }


def test_registration_is_explicit_exact_and_validates_every_dependency_first(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app = Flask(f"admin-registration-{uuid4().hex}")
    organization = _FakeOrganizationService()
    accounts = _FakeAccountService()
    before = tuple(app.url_map.iter_rules())

    incomplete = SimpleNamespace(get_tree=lambda **_kwargs: None)
    with pytest.raises(TypeError, match="preview_change"):
        register_admin_routes(app, incomplete, accounts, lambda: None)
    assert tuple(app.url_map.iter_rules()) == before
    assert not getattr(app, "_knowledge_core_admin_v1_registered", False)

    registered = register_admin_routes(
        app,
        organization,
        accounts,
        lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
    )
    assert len(registered) == 9
    admin_rules = [rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/admin/v1")]
    assert len(admin_rules) == 9
    assert all("purge" not in rule.rule for rule in admin_rules)
    with pytest.raises(RuntimeError, match="already registered"):
        register_admin_routes(app, organization, accounts, lambda: None)
    assert len([rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/admin/v1")]) == 9

    purge_app = Flask(f"admin-purge-registration-{uuid4().hex}")
    purge = _FakePurgeQueueService()
    registered_with_purge = register_admin_routes(
        purge_app,
        _FakeOrganizationService(),
        _FakeAccountService(),
        lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
        purge_service=purge,
    )
    assert len(registered_with_purge) == 10
    assert "/api/admin/v1/purge-eligible" in registered_with_purge
    colliding = Flask(f"admin-collision-{uuid4().hex}")
    colliding.add_url_rule(
        "/api/admin/v1/org/units/<string:shadow_id>/move",
        endpoint="shadow_move",
        view_func=lambda shadow_id: shadow_id,
        methods=["POST"],
    )
    with pytest.raises(RuntimeError, match="route already exists"):
        register_admin_routes(
            colliding, organization, accounts, lambda: None
        )
    assert not getattr(colliding, "_knowledge_core_admin_v1_registered", False)

    head_shadow = Flask(f"admin-head-shadow-{uuid4().hex}")
    head_shadow.add_url_rule(
        "/api/admin/v1/org/tree",
        endpoint="shadow_head",
        view_func=lambda: "shadow",
        methods=["HEAD"],
    )
    head_rules_before = tuple(head_shadow.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="route already exists"):
        register_admin_routes(
            head_shadow, organization, accounts, lambda: None
        )
    assert tuple(head_shadow.url_map.iter_rules()) == head_rules_before

    nonempty_blueprint = Blueprint(f"nonempty_{uuid4().hex}", __name__)
    nonempty_blueprint.add_url_rule(
        "/api/admin/v1/org/units",
        endpoint="shadow_create",
        view_func=lambda: "shadow",
        methods=["POST"],
    )
    with pytest.raises(RuntimeError, match="dedicated empty Blueprint"):
        register_admin_routes(
            nonempty_blueprint, organization, accounts, lambda: None
        )

    callback_blueprint = Blueprint(f"callback_{uuid4().hex}", __name__)

    @callback_blueprint.before_request
    def shadow_admin_handler():
        return "shadow"

    with pytest.raises(RuntimeError, match="dedicated empty Blueprint"):
        register_admin_routes(
            callback_blueprint, organization, accounts, lambda: None
        )
    assert callback_blueprint.deferred_functions == []

    conflicting = Flask(f"admin-registration-conflict-{uuid4().hex}")

    @conflicting.post("/api/admin/v1/org/units")
    def existing_admin_route():
        return {}

    existing = tuple(conflicting.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="route already exists"):
        register_admin_routes(
            conflicting,
            _FakeOrganizationService(),
            _FakeAccountService(),
            lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
        )
    assert tuple(conflicting.url_map.iter_rules()) == existing


def test_explicit_purge_queue_route_binds_admin_principal_and_strict_query(
    isolated_data_root,
) -> None:
    del isolated_data_root
    purge = _FakePurgeQueueService()
    app, _organization, _accounts = _make_app(purge=purge)
    client, uid, _headers = _login(app)

    response = client.get(
        "/api/admin/v1/purge-eligible?limit=1&cursor=opaque-cursor",
        base_url=_ORIGIN,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert payload["items"][0]["blocking_reference_count"] == 2
    assert len(purge.calls) == 1
    command = purge.calls[0]
    assert command.principal.user_id == uid
    assert command.principal.captured_scope_epoch == 17
    assert command.cursor == "opaque-cursor"
    assert command.limit == 1

    for query in (
        "unknown=1",
        "limit=01",
        "limit=101",
        "cursor=a&cursor=b",
    ):
        invalid = client.get(
            "/api/admin/v1/purge-eligible?" + query,
            base_url=_ORIGIN,
        )
        assert invalid.status_code == 400
        assert invalid.get_json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(purge.calls) == 1

    denied_purge = _FakePurgeQueueService()
    denied_app, _organization, _accounts = _make_app(
        role="user",
        purge=denied_purge,
    )
    denied_client, _uid, _headers = _login(denied_app, role="user")
    denied = denied_client.get(
        "/api/admin/v1/purge-eligible",
        base_url=_ORIGIN,
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "FORBIDDEN"
    assert denied_purge.calls == []


def test_blueprint_is_supported_but_import_and_default_host_registration_are_inert(
    isolated_data_root,
) -> None:
    del isolated_data_root
    organization = _FakeOrganizationService()
    accounts = _FakeAccountService()
    blueprint = Blueprint(f"admin_{uuid4().hex}", __name__)
    registered = register_admin_routes(
        blueprint,
        organization,
        accounts,
        lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
    )
    app = Flask(f"admin-blueprint-{uuid4().hex}")
    assert not [rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/admin/v1")]
    app.register_blueprint(blueprint)
    assert len(registered) == 9
    assert len([rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/admin/v1")]) == 9
    first_attachment_rules = tuple(app.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="route already exists on Blueprint host"):
        app.register_blueprint(blueprint, name=f"second_{uuid4().hex}")
    assert tuple(app.url_map.iter_rules()) == first_attachment_rules

    shadow_host = Flask(f"admin-blueprint-shadow-{uuid4().hex}")
    shadow_host.add_url_rule(
        "/api/admin/v1/org/units",
        endpoint="shadow_create",
        view_func=lambda: "shadow",
        methods=["POST"],
    )
    shadow_rules_before = tuple(shadow_host.url_map.iter_rules())
    shadow_blueprint = Blueprint(f"shadow_admin_{uuid4().hex}", __name__)
    register_admin_routes(
        shadow_blueprint,
        _FakeOrganizationService(),
        _FakeAccountService(),
        lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
    )
    with pytest.raises(RuntimeError, match="route already exists on Blueprint host"):
        shadow_host.register_blueprint(shadow_blueprint)
    assert tuple(shadow_host.url_map.iter_rules()) == shadow_rules_before

    endpoint_host = Flask(f"admin-blueprint-endpoint-{uuid4().hex}")
    endpoint_blueprint = Blueprint(f"endpoint_admin_{uuid4().hex}", __name__)
    endpoint_host.add_url_rule(
        "/unrelated-existing-view",
        endpoint=f"{endpoint_blueprint.name}.knowledge_admin_v1_tree",
        view_func=lambda: "shadow",
        methods=["GET"],
    )
    register_admin_routes(
        endpoint_blueprint,
        _FakeOrganizationService(),
        _FakeAccountService(),
        lambda: {"id": "admin", "scope_epoch": 1, "role": "admin"},
    )
    with pytest.raises(RuntimeError, match="route endpoint already exists"):
        endpoint_host.register_blueprint(endpoint_blueprint)
    assert not [
        rule
        for rule in endpoint_host.url_map.iter_rules()
        if rule.rule.startswith("/api/admin/v1")
    ]


@pytest.mark.scenario("AUTH-004")
def test_actual_auth_hook_blocks_host_origin_and_csrf_before_admin_service(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, organization, _accounts = _make_app()
    client, _uid, headers = _login(app)

    no_csrf = client.post(
        "/api/admin/v1/org/units",
        base_url=_ORIGIN,
        json={"operation_id": _op(), "parent_id": _ROOT, "kind": "team", "name": "T"},
        headers={"Origin": _ORIGIN},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.get_json()["request_id"] == no_csrf.headers["X-Request-ID"]
    assert no_csrf.get_json()["error"]["code"] == "FORBIDDEN"
    assert no_csrf.headers["Cache-Control"] == "no-store, max-age=0"
    assert organization.calls == []

    cross = client.post(
        "/api/admin/v1/org/units",
        base_url=_ORIGIN,
        json={"operation_id": _op(), "parent_id": _ROOT, "kind": "team", "name": "T"},
        headers={**headers, "Origin": "https://attacker.example.test"},
    )
    assert cross.status_code == 403
    assert organization.calls == []

    bad_host = client.get(
        "/api/admin/v1/org/tree",
        base_url="https://attacker.example.test",
    )
    assert bad_host.status_code == 403
    assert bad_host.get_json()["request_id"] == bad_host.headers["X-Request-ID"]
    assert organization.calls == []

    anonymous = app.test_client().get(
        "/api/admin/v1/org/tree", base_url=_ORIGIN
    )
    assert anonymous.status_code == 401
    assert anonymous.get_json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert anonymous.get_json()["request_id"] == anonymous.headers["X-Request-ID"]
    assert anonymous.headers["Cache-Control"] == "no-store, max-age=0"


def test_all_nine_endpoints_forward_only_session_actor_and_epoch(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, organization, accounts = _make_app()
    client, uid, headers = _login(app)

    tree = client.get("/api/admin/v1/org/tree", base_url=_ORIGIN)
    assert tree.status_code == 200
    tree_payload = tree.get_json()
    assert tree_payload["users"] == [
        {
            "account_status": "active",
            "current_membership_id": None,
            "current_part_id": None,
            "display_name": "Administrator",
            "id": "admin",
            "row_version": 1,
            "scope_epoch": 1,
            "system_role": "admin",
        }
    ]
    assert "password_hash" not in tree.get_data(as_text=True)

    create = client.post(
        "/api/admin/v1/org/units",
        base_url=_ORIGIN,
        json={"operation_id": _op(), "parent_id": _ROOT, "kind": "team", "name": "팀 A"},
        headers=headers,
    )
    assert create.status_code == 201

    preview = client.post(
        f"/api/admin/v1/org/units/{_TEAM}/change-preview",
        base_url=_ORIGIN,
        json={"expected_row_version": 2, "change": "move", "new_parent_id": _ROOT},
        headers=headers,
    )
    assert preview.status_code == 200
    preview_payload = preview.get_json()
    assert preview_payload["member_impact"]["members"] == []
    assert preview_payload["leader_impact"]["leader_assignment_id"] is None
    assert preview_payload["space_impact"]["row_version"] is None

    rename = client.patch(
        f"/api/admin/v1/org/units/{_TEAM}",
        base_url=_ORIGIN,
        json={"operation_id": _op(), "expected_row_version": 2, "name": "팀 B"},
        headers=headers,
    )
    assert rename.status_code == 200

    move = client.post(
        f"/api/admin/v1/org/units/{_TEAM}/move",
        base_url=_ORIGIN,
        json={
            "operation_id": _op(),
            "expected_row_version": 3,
            "preview_token": "p" * 64,
            "new_parent_id": _ROOT,
            "resolution": {"retain_memberships": True, "retain_leader": True, "retain_space": True},
        },
        headers=headers,
    )
    assert move.status_code == 200

    membership = client.put(
        "/api/admin/v1/users/member01/part-membership",
        base_url=_ORIGIN,
        json={
            "operation_id": _op(),
            "expected_user_row_version": 4,
            "part_id": _PART,
            "expected_current_leader_assignment_id": None,
            "expected_part_row_version": None,
            "reason": "배정",
        },
        headers=headers,
    )
    assert membership.status_code == 200

    leader = client.put(
        f"/api/admin/v1/org/parts/{_PART}/leader",
        base_url=_ORIGIN,
        json={
            "operation_id": _op(),
            "leader_user_id": "member01",
            "expected_part_row_version": 6,
            "expected_current_leader_assignment_id": None,
            "reason": "파트장 지정",
        },
        headers=headers,
    )
    assert leader.status_code == 200

    disable_body = _status_body()
    disable = client.post(
        "/api/admin/v1/users/member01/disable",
        base_url=_ORIGIN,
        json=disable_body,
        headers=headers,
    )
    assert disable.status_code == 200
    assert disable.get_json()["status"] == "disabled"

    enable_body = _status_body()
    enable_body["replacement_leader_user_id"] = None
    enable = client.post(
        "/api/admin/v1/users/member01/enable",
        base_url=_ORIGIN,
        json=enable_body,
        headers=headers,
    )
    assert enable.status_code == 200
    assert enable.get_json()["status"] == "active"

    assert [name for name, _command in organization.calls] == [
        "get_tree",
        "create_unit",
        "preview_change",
        "rename_unit",
        "move_unit",
        "set_membership",
        "set_leader",
    ]
    assert [name for name, _command in accounts.calls] == ["disable_account", "reenable_account"]
    for _name, command in organization.calls + accounts.calls:
        assert command.actor_user_id == uid
        assert command.actor_scope_epoch == 17
        assert "actor_user_id" not in getattr(command, "request_meta", {})
    assert accounts.calls[0][1].expected_current_leader_assignment_id == _ASSIGNMENT
    assert accounts.calls[0][1].expected_part_row_version == 12
    assert organization.calls[-2][1].expected_part_row_version is None
    assert organization.calls[-1][1].expected_part_row_version == 6


def test_tree_and_preview_responses_match_canonical_ui_read_model_schema(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, _organization, _accounts = _make_app()
    client, _uid, headers = _login(app)
    tree = client.get("/api/admin/v1/org/tree", base_url=_ORIGIN)
    preview = client.post(
        f"/api/admin/v1/org/units/{_PART}/change-preview",
        base_url=_ORIGIN,
        json={"expected_row_version": 1, "change": "inactivate"},
        headers=headers,
    )
    assert tree.status_code == 200
    assert preview.status_code == 200

    docs_root = Path(__file__).resolve().parents[3] / "docs" / "knowledge_spaces"
    schema_path = docs_root / "contracts" / "schemas" / "organization.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    auditor = ContractAuditor(docs_root.resolve())
    tree_payload = tree.get_json()
    preview_payload = preview.get_json()
    assert not auditor.validate_instance(
        tree_payload, schema["$defs"]["OrgTreeResponse"], schema_path
    )
    assert not auditor.validate_instance(
        preview_payload,
        schema["$defs"]["OrgChangePreviewResponse"],
        schema_path,
    )

    missing_users = dict(tree_payload)
    del missing_users["users"]
    assert auditor.validate_instance(
        missing_users, schema["$defs"]["OrgTreeResponse"], schema_path
    )
    incomplete_user = json.loads(json.dumps(tree_payload))
    del incomplete_user["users"][0]["current_membership_id"]
    assert auditor.validate_instance(
        incomplete_user, schema["$defs"]["OrgTreeResponse"], schema_path
    )
    incomplete_preview = json.loads(json.dumps(preview_payload))
    del incomplete_preview["space_impact"]["row_version"]
    assert auditor.validate_instance(
        incomplete_preview,
        schema["$defs"]["OrgChangePreviewResponse"],
        schema_path,
    )


def test_inactivation_resolution_is_exact_and_uses_one_reason_authority(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, organization, _accounts = _make_app()
    client, _uid, headers = _login(app)
    operation_id = _op()
    payload = {
        "operation_id": operation_id,
        "expected_row_version": 9,
        "status": "inactive",
        "reason": "정리 완료",
        "preview_token": "p" * 64,
        "resolution": {
            "member_resolutions": [
                {"user_id": "member01", "expected_user_row_version": 3, "target_part_id": _PART_2}
            ],
            "expected_current_leader_assignment_id": _ASSIGNMENT,
            "leader_action": "close",
            "expected_space_row_version": 7,
            "space_action": "freeze_if_present",
        },
    }
    response = client.patch(
        f"/api/admin/v1/org/units/{_PART}",
        base_url=_ORIGIN,
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    command = organization.calls[-1][1]
    assert command.reason == "정리 완료"
    assert command.resolution.member_resolutions[0].target_part_id == _PART_2
    assert not hasattr(command.resolution, "member_reason")

    payload["operation_id"] = _op()
    payload["resolution"]["member_reason"] = "계약 밖 이유"
    unknown_reason = client.patch(
        f"/api/admin/v1/org/units/{_PART}",
        base_url=_ORIGIN,
        json=payload,
        headers=headers,
    )
    assert unknown_reason.status_code == 400
    assert unknown_reason.get_json()["error"]["code"] == "VALIDATION_ERROR"
    assert unknown_reason.get_json()["error"]["details"]["field"] == "member_reason"

    payload["operation_id"] = _op()
    del payload["resolution"]["member_reason"]
    payload["resolution"]["member_resolutions"].append(
        {"user_id": "member01", "expected_user_row_version": 4, "target_part_id": None}
    )
    duplicate = client.patch(
        f"/api/admin/v1/org/units/{_PART}",
        base_url=_ORIGIN,
        json=payload,
        headers=headers,
    )
    assert duplicate.status_code == 400


def test_strict_json_idempotency_and_stable_errors(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, organization, _accounts = _make_app()
    client, _uid, headers = _login(app)
    url = "/api/admin/v1/org/units"
    base = {"parent_id": _ROOT, "kind": "team", "name": "팀"}

    missing = client.post(url, base_url=_ORIGIN, json=base, headers=headers)
    assert missing.status_code == 428
    assert missing.get_json()["error"]["code"] == "OPERATION_ID_REQUIRED"

    alias = _op()
    header_only = client.post(
        url,
        base_url=_ORIGIN,
        json=base,
        headers={**headers, "Idempotency-Key": alias},
    )
    assert header_only.status_code == 428
    assert header_only.get_json()["error"]["code"] == "OPERATION_ID_REQUIRED"

    accepted_alias = client.post(
        url,
        base_url=_ORIGIN,
        json={**base, "operation_id": alias},
        headers={**headers, "Idempotency-Key": alias},
    )
    assert accepted_alias.status_code == 201
    assert organization.calls[-1][1].operation_id == alias

    replay = client.post(
        url,
        base_url=_ORIGIN,
        json={**base, "operation_id": alias},
        headers={**headers, "Idempotency-Key": alias},
    )
    assert replay.status_code == 201
    assert replay.headers["X-Request-ID"] != accepted_alias.headers["X-Request-ID"]
    assert replay.get_json()["request_id"] == replay.headers["X-Request-ID"]
    assert {
        key: value for key, value in replay.get_json().items() if key != "request_id"
    } == {
        key: value
        for key, value in accepted_alias.get_json().items()
        if key != "request_id"
    }

    conflict = client.post(
        url,
        base_url=_ORIGIN,
        json={**base, "operation_id": _op()},
        headers={**headers, "Idempotency-Key": _op()},
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    empty_alias = client.post(
        url,
        base_url=_ORIGIN,
        json={**base, "operation_id": _op()},
        headers={**headers, "Idempotency-Key": ""},
    )
    assert empty_alias.status_code == 400
    assert empty_alias.get_json()["error"]["code"] == "VALIDATION_ERROR"

    unknown = client.post(
        url,
        base_url=_ORIGIN,
        json={**base, "operation_id": _op(), "actor_user_id": "forged"},
        headers=headers,
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["error"]["details"]["field"] == "actor_user_id"

    duplicate_json = json.dumps({**base, "operation_id": _op()})[:-1] + ',"name":"duplicate"}'
    duplicate = client.post(
        url,
        base_url=_ORIGIN,
        data=duplicate_json,
        content_type="application/json",
        headers=headers,
    )
    assert duplicate.status_code == 400

    non_finite = client.post(
        url,
        base_url=_ORIGIN,
        data=(
            '{"operation_id":"'
            + _op()
            + '","parent_id":"'
            + _ROOT
            + '","kind":"team","name":1e1000000}'
        ),
        content_type="application/json",
        headers=headers,
    )
    assert non_finite.status_code == 400

    wrong_type = client.post(url, base_url=_ORIGIN, data="x", content_type="text/plain", headers=headers)
    assert wrong_type.status_code == 400

    oversized = client.post(
        url,
        base_url=_ORIGIN,
        data=json.dumps({**base, "operation_id": _op(), "name": "x" * MAX_ADMIN_JSON_BYTES}),
        content_type="application/json",
        headers=headers,
    )
    assert oversized.status_code == 400

    # Werkzeug's input stream is finite even when the WSGI environment omits
    # Content-Length.  The adapter must still stop at MAX+1 instead of calling
    # unbounded get_data().
    chunked_body = json.dumps(
        {**base, "operation_id": _op(), "name": "x" * MAX_ADMIN_JSON_BYTES}
    ).encode("utf-8")
    chunked = client.open(
        url,
        method="POST",
        base_url=_ORIGIN,
        input_stream=io.BytesIO(chunked_body),
        content_length=None,
        content_type="application/json",
        headers={**headers, "Transfer-Encoding": "chunked"},
    )
    assert chunked.status_code == 400

    invalid_path = client.post(
        "/api/admin/v1/org/units/NOT-A-UUID/change-preview",
        base_url=_ORIGIN,
        json={"expected_row_version": 1, "change": "inactivate"},
        headers=headers,
    )
    assert invalid_path.status_code == 400

    organization.error = OrganizationRevisionConflictError(
        "The unit changed.", details={"current_row_version": 8}
    )
    service_error = client.get("/api/admin/v1/org/tree", base_url=_ORIGIN)
    assert service_error.status_code == 409
    assert service_error.get_json() == {
        "request_id": service_error.headers["X-Request-ID"],
        "error": {
            "code": "REVISION_CONFLICT",
            "message": "The unit changed.",
            "retryable": False,
            "details": {"current_row_version": 8},
        },
    }
    assert service_error.headers["Cache-Control"] == "no-store, max-age=0"

    organization.error = RuntimeError("programmer-invariant-synthetic")
    internal = client.get("/api/admin/v1/org/tree", base_url=_ORIGIN)
    assert internal.status_code == 500
    assert internal.get_json() == {
        "request_id": internal.headers["X-Request-ID"],
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "The administrator command could not be completed.",
            "retryable": True,
            "details": {"state": "internal_error"},
        },
    }
    assert "programmer-invariant-synthetic" not in internal.get_data(as_text=True)
    assert internal.headers["Cache-Control"] == "no-store, max-age=0"


def test_non_admin_canonical_principal_is_forbidden_even_with_valid_cookie(
    isolated_data_root,
) -> None:
    del isolated_data_root
    app, organization, _accounts = _make_app(role="user")
    client, _uid, _headers = _login(app, role="user")
    response = client.get("/api/admin/v1/org/tree", base_url=_ORIGIN)
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"
    assert organization.calls == []
