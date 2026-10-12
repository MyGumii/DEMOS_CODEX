from __future__ import annotations

import ast
import inspect
import json

import pytest

from code_assist_v1.notion_collaboration import capability_switches as switches


def _enabled_environment(*, realm: str = "development", rollout: str = "canary") -> dict[str, str]:
    value = {
        switches.AGGREGATE_KEY: "true",
        switches.REALM_KEY: realm,
        switches.ROLLOUT_KEY: rollout,
        switches.GENERATION_KEY: "build-20260824-0001",
        switches.CAPABILITY_KEYS["realtime_transport"]: "true",
        switches.CAPABILITY_KEYS["collaborative_editing"]: "true",
    }
    if rollout == "canary":
        value[switches.CANARY_SUBJECTS_KEY] = json.dumps(["fixture-owner", "fixture-member"])
        value[switches.CANARY_WORKSPACES_KEY] = json.dumps(["workspace-alpha"])
    return value


@pytest.mark.unit
def test_k0_defaults_off_and_sbe_or_child_flags_cannot_bypass_aggregate() -> None:
    assert switches.load_capability_switches({}).state == "disabled"
    sbe_only = switches.load_capability_switches(
        {
            "CODE_ASSIST_KNOWLEDGE_CORE_ENABLED": "true",
            "CODE_ASSIST_SBE_SUCCESSOR_ENABLED": "true",
            switches.CAPABILITY_KEYS["realtime_transport"]: "true",
        }
    )
    assert sbe_only.enabled is False
    assert sbe_only.reason_codes == (
        "AGGREGATE_DISABLED",
        "CAPABILITY_REQUESTED_WHILE_AGGREGATE_DISABLED",
    )

    explicit_off = _enabled_environment()
    explicit_off[switches.AGGREGATE_KEY] = "false"
    assert switches.load_capability_switches(explicit_off).enabled is False


@pytest.mark.unit
def test_k0_invalid_or_unknown_notion_configuration_fails_closed() -> None:
    invalid = _enabled_environment()
    invalid[switches.CAPABILITY_KEYS["files"]] = "sometimes"
    invalid["CODE_ASSIST_NOTION_COLLAB_CAP_TYPO"] = "true"
    loaded = switches.load_capability_switches(invalid)
    assert loaded.state == "invalid"
    assert set(loaded.reason_codes) >= {"CAPABILITY_VALUE_INVALID", "UNKNOWN_NOTION_COLLAB_KEY"}

    missing_scope = _enabled_environment()
    del missing_scope[switches.CANARY_WORKSPACES_KEY]
    assert "CANARY_SCOPE_REQUIRED" in switches.load_capability_switches(missing_scope).reason_codes


@pytest.mark.unit
def test_k0_development_canary_requires_exact_generation_schema_subject_workspace_and_capability() -> None:
    loaded = switches.load_capability_switches(_enabled_environment())
    assert loaded.enabled is True
    assert loaded.configuration is not None
    config = loaded.configuration
    runtime = switches.RuntimeScope(
        realm="development",
        deployment_generation_id="build-20260824-0001",
        schema_compatible=True,
    )
    owner = switches.RequestScope(subject_id="fixture-owner", workspace_id="workspace-alpha")
    assert config.allows("realtime_transport", runtime=runtime, request=owner) is True
    assert config.allows("files", runtime=runtime, request=owner) is False
    assert config.allows("realtime_transport", runtime=runtime, request=switches.RequestScope("outsider", "workspace-alpha")) is False
    assert config.allows("realtime_transport", runtime=runtime, request=switches.RequestScope("fixture-owner", "workspace-other")) is False
    assert config.allows(
        "realtime_transport",
        runtime=switches.RuntimeScope("development", "different-build", True),
        request=owner,
    ) is False
    assert config.allows(
        "realtime_transport",
        runtime=switches.RuntimeScope("development", "build-20260824-0001", False),
        request=owner,
    ) is False


@pytest.mark.unit
def test_k0_production_full_requires_separate_runtime_promotion_authority() -> None:
    loaded = switches.load_capability_switches(_enabled_environment(realm="production", rollout="full"))
    assert loaded.enabled is True
    assert loaded.configuration is not None
    config = loaded.configuration
    request = switches.RequestScope("internal-user", "workspace-alpha")
    denied = switches.RuntimeScope("production", "build-20260824-0001", True, full_rollout_authorized=False)
    allowed = switches.RuntimeScope("production", "build-20260824-0001", True, full_rollout_authorized=True)
    assert config.allows("realtime_transport", runtime=denied, request=request) is False
    assert config.allows("realtime_transport", runtime=allowed, request=request) is True


@pytest.mark.unit
def test_k0_configuration_fingerprint_is_order_independent_and_public_contract_redacts_allowlists() -> None:
    first = _enabled_environment()
    second = dict(reversed(list(first.items())))
    one = switches.load_capability_switches(first).configuration
    two = switches.load_capability_switches(second).configuration
    assert one is not None and two is not None
    assert one.configuration_fingerprint == two.configuration_fingerprint
    public = one.public_contract()
    assert public["canary_subject_count"] == 2
    assert public["canary_workspace_count"] == 1
    assert "fixture-owner" not in json.dumps(public)
    assert public["commercial_plan_or_storage_quota_enforced"] is False
    assert public["sbe_flag_alias_or_bypass_allowed"] is False


@pytest.mark.unit
def test_k0_forward_schema_and_code_rollback_never_request_downgrade() -> None:
    current = switches.evaluate_schema_gate(
        observed_schema_version=switches.BASE_SCHEMA_VERSION,
        aggregate_enabled=False,
        additive_forward_compatibility_attested=False,
        collaboration_migration_implemented=False,
    )
    assert current["legacy_application_may_start"] is True
    assert current["collaboration_may_start"] is False
    assert current["schema_downgrade_required"] is False

    rolled_back = switches.evaluate_schema_gate(
        observed_schema_version=switches.COLLABORATION_SCHEMA_VERSION,
        aggregate_enabled=False,
        additive_forward_compatibility_attested=True,
        collaboration_migration_implemented=True,
    )
    assert rolled_back["legacy_application_may_start"] is True
    assert rolled_back["collaboration_may_start"] is False
    assert rolled_back["rollback_action"] == "disable_Notion_collaboration_aggregate_and_run_compatible_code"
    assert rolled_back["schema_downgrade_required"] is False

    enabled = switches.evaluate_schema_gate(
        observed_schema_version=switches.COLLABORATION_SCHEMA_VERSION,
        aggregate_enabled=True,
        additive_forward_compatibility_attested=True,
        collaboration_migration_implemented=True,
    )
    assert enabled["collaboration_may_start"] is True


@pytest.mark.unit
def test_k0_namespace_excludes_non_adopted_capabilities_and_module_has_no_ambient_reads() -> None:
    assert not ({"public_links", "external_guests", "pricing", "ai"} & set(switches.CAPABILITIES))
    source = inspect.getsource(switches)
    tree = ast.parse(source)
    assert not any(
        (isinstance(node, ast.Import) and any(alias.name == "os" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "os")
        or (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os")
        for node in ast.walk(tree)
    )
    assert "knowledge_core" not in source
    assert "app_code" not in source
