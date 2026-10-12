from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.browser_isolation_harness import (
    BrowserIsolationError,
    run_multi_principal_browser_isolation,
)
from code_assist_v1.notion_collaboration.multi_principal_fixture import (
    ACTION_ORDER,
    PAGE_IDS,
    SEED_NAME,
    WORKSPACE_ID,
    MultiPrincipalFixtureError,
    build_multi_principal_fixture_root,
    load_multi_principal_seed,
    principal_alias_map,
    redact_principal_evidence,
    verify_multi_principal_fixture_root,
)
from code_assist_v1.notion_collaboration.synthetic_pair_root import (
    PRINCIPALS,
    build_synthetic_pair_root,
    load_fixture_credentials,
)


def _build(tmp_path: Path, suffix: str = "one"):
    pair_root = tmp_path / f"pair-{suffix}"
    fixture_root = tmp_path / f"fixture-{suffix}"
    build_synthetic_pair_root(pair_root, allowed_parent=tmp_path, created_by="pytest-h0a-p1")
    receipt = build_multi_principal_fixture_root(
        fixture_root,
        allowed_parent=tmp_path,
        synthetic_pair_root=pair_root,
        created_by="pytest-h0a-p1",
    )
    return pair_root, fixture_root, receipt


def test_h0a_p1_builds_exact_four_principal_workspace_page_and_grant_seed(tmp_path: Path) -> None:
    pair_root, fixture_root, receipt = _build(tmp_path)
    seed = load_multi_principal_seed(fixture_root, synthetic_pair_root=pair_root)
    assert receipt["status"] == "pass"
    assert receipt["principal_count"] == 4
    assert receipt["workspace_count"] == 1
    assert receipt["page_count"] == 4
    assert receipt["grant_count"] == 3
    assert seed["workspace"]["workspace_id"] == WORKSPACE_ID
    assert [item["principal_id"] for item in seed["principals"]] == [item[0] for item in PRINCIPALS]
    assert [item["fixture_role"] for item in seed["principals"]] == [item[1] for item in PRINCIPALS]
    assert [item["page_id"] for item in seed["pages"]] == list(PAGE_IDS)
    assert all(item["body_present"] is False for item in seed["pages"])
    assert seed["credential_or_content_copy_count"] == 0
    assert seed["product_authority"] is False


def test_h0a_p1_access_oracle_keeps_guest_narrow_and_outsider_empty(tmp_path: Path) -> None:
    pair_root, fixture_root, _ = _build(tmp_path)
    access = load_multi_principal_seed(fixture_root, synthetic_pair_root=pair_root)["expected_access"]
    assert access["fixture-owner"] == {page_id: list(ACTION_ORDER) for page_id in PAGE_IDS}
    assert access["fixture-member"][PAGE_IDS[3]] == []
    assert access["fixture-page-guest"] == {
        PAGE_IDS[0]: [],
        PAGE_IDS[1]: [],
        PAGE_IDS[2]: ["read", "comment"],
        PAGE_IDS[3]: [],
    }
    assert access["fixture-outsider"] == {page_id: [] for page_id in PAGE_IDS}


def test_h0a_p1_workspace_seed_is_deterministic_across_fresh_synthetic_roots(tmp_path: Path) -> None:
    _, first_fixture, first_receipt = _build(tmp_path, "first")
    _, second_fixture, second_receipt = _build(tmp_path, "second")
    assert (first_fixture / SEED_NAME).read_bytes() == (second_fixture / SEED_NAME).read_bytes()
    assert first_receipt["workspace_seed_sha256"] == second_receipt["workspace_seed_sha256"]
    assert first_receipt["root_locator_sha256"] != second_receipt["root_locator_sha256"]


def test_h0a_p1_public_receipt_uses_aliases_and_contains_no_credentials_or_paths(tmp_path: Path) -> None:
    pair_root, fixture_root, receipt = _build(tmp_path)
    credentials = load_fixture_credentials(pair_root)
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert str(pair_root) not in serialized
    assert str(fixture_root) not in serialized
    assert "password" not in serialized
    assert all(item["id"] not in serialized for item in credentials)
    assert all(item["password"] not in serialized for item in credentials)
    assert all(item["alias"].startswith("principal:") for item in receipt["principals"])


def test_h0a_p1_redaction_aliases_principals_and_removes_secret_fields_and_values(tmp_path: Path) -> None:
    pair_root, _, _ = _build(tmp_path)
    credentials = {item["id"]: item for item in load_fixture_credentials(pair_root)}
    secret = (pair_root / "data/.runtime/secret.key").read_text(encoding="utf-8").strip()
    aliases = principal_alias_map(pair_root)
    evidence = {
        "actor": "fixture-owner",
        "message": "fixture-owner invited fixture-page-guest",
        "password": credentials["fixture-owner"]["password"],
        "session_cookie": "cookie-value",
        "runtime_path": str(pair_root),
        "nested": [secret, {"subject": "fixture-outsider"}],
    }
    redacted = redact_principal_evidence(
        evidence,
        aliases=aliases,
        forbidden_values=[secret, credentials["fixture-owner"]["password"], str(pair_root)],
    )
    serialized = json.dumps(redacted, sort_keys=True)
    assert all(item[0] not in serialized for item in PRINCIPALS)
    assert secret not in serialized
    assert str(pair_root) not in serialized
    assert redacted["actor"] == aliases["fixture-owner"]
    assert redacted["password"] == "[REDACTED]"
    assert redacted["session_cookie"] == "[REDACTED]"
    assert redacted["runtime_path"] == "[REDACTED]"


def test_h0a_p1_redaction_rejects_bad_alias_denominator_nonfinite_and_key_collision(tmp_path: Path) -> None:
    pair_root, _, _ = _build(tmp_path)
    aliases = principal_alias_map(pair_root)
    with pytest.raises(MultiPrincipalFixtureError, match="PRINCIPAL_ALIAS_DENOMINATOR_INVALID"):
        redact_principal_evidence({}, aliases={})
    with pytest.raises(MultiPrincipalFixtureError, match="NONFINITE_EVIDENCE_FORBIDDEN"):
        redact_principal_evidence({"value": math.nan}, aliases=aliases)
    collision_aliases = dict(aliases)
    collision_aliases["fixture-owner"] = "principal:owner:same"
    collision_aliases["fixture-member"] = "principal:member:same"
    with pytest.raises(MultiPrincipalFixtureError, match="REDACTED_EVIDENCE_KEY_COLLISION"):
        redact_principal_evidence(
            {"fixture-owner": 1, "principal:owner:same": 2},
            aliases=collision_aliases,
        )


def test_h0a_p1_tamper_and_unexpected_file_fail_closed(tmp_path: Path) -> None:
    pair_root, fixture_root, _ = _build(tmp_path)
    seed_path = fixture_root / SEED_NAME
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed["expected_access"]["fixture-outsider"][PAGE_IDS[0]] = ["read"]
    seed_path.write_bytes((json.dumps(seed, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    with pytest.raises(MultiPrincipalFixtureError, match="WORKSPACE_SEED_DRIFT"):
        verify_multi_principal_fixture_root(fixture_root, synthetic_pair_root=pair_root)

    other_pair, other_fixture, _ = _build(tmp_path, "extra")
    (other_fixture / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(MultiPrincipalFixtureError, match="FIXTURE_FILE_DENOMINATOR_MISMATCH"):
        verify_multi_principal_fixture_root(other_fixture, synthetic_pair_root=other_pair)


def test_h0a_p1_rejects_existing_and_cross_parent_roots_without_modification(tmp_path: Path) -> None:
    pair_root = tmp_path / "pair"
    build_synthetic_pair_root(pair_root, allowed_parent=tmp_path, created_by="pytest-h0a-existing")
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(MultiPrincipalFixtureError, match="FIXTURE_ROOT_ALREADY_EXISTS"):
        build_multi_principal_fixture_root(
            existing,
            allowed_parent=tmp_path,
            synthetic_pair_root=pair_root,
            created_by="pytest-h0a-existing",
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(MultiPrincipalFixtureError, match="ROOT_PARENT_MISMATCH"):
        build_multi_principal_fixture_root(
            other / "fixture",
            allowed_parent=other,
            synthetic_pair_root=pair_root,
            created_by="pytest-h0a-cross-parent",
        )


def test_h0a_p1_builder_has_no_live_source_copy_or_product_authority_parameters() -> None:
    parameters = set(inspect.signature(build_multi_principal_fixture_root).parameters)
    assert not parameters & {
        "source_workspace",
        "source_users",
        "copy_root",
        "knowledge_root",
        "user_data_root",
        "session_root",
        "production_root",
        "preview_root",
        "service_name",
    }


@pytest.mark.integration
def test_h0a_p2_four_ephemeral_chrome_contexts_have_no_cookie_storage_cache_worker_or_network_leak(
    tmp_path: Path,
) -> None:
    pair_root, fixture_root, _ = _build(tmp_path, "browser")
    credentials = load_fixture_credentials(pair_root)
    receipt = run_multi_principal_browser_isolation(
        pair_root,
        fixture_root,
        tmp_path / "browser-runtime",
        allowed_parent=tmp_path,
    )
    assert receipt["status"] == "pass"
    assert receipt["principal_context_count"] == 4
    assert receipt["principal_roles"] == ["owner", "member", "internal_page_guest", "outsider"]
    assert receipt["browser"]["engine"] == "chromium"
    assert receipt["browser"]["channel"] == "chrome"
    assert isinstance(receipt["browser"]["version"], str) and receipt["browser"]["version"]
    assert receipt["browser"]["headless"] is True
    assert receipt["browser"]["persistent_context_used"] is False
    assert receipt["browser"]["signed_in_profile_used"] is False
    assert receipt["browser"]["disconnected_after_close"] is True
    assert receipt["independent_context_cookie_values"] is True
    assert receipt["same_subject_cross_host_checks"] == 8
    assert receipt["local_storage_checks"] == 16
    assert receipt["cache_storage_checks"] == 16
    assert receipt["service_worker_checks"] == 8
    assert receipt["observer_context_empty"] is True
    assert receipt["reset_context_empty"] is True
    assert receipt["outsider_effective_action_count"] == 0
    assert receipt["network_target_escape_count"] == 0
    assert receipt["forbidden_operator_port_request_count"] == 0
    assert receipt["cleanup"] == {
        "browser_process_stopped": True,
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert str(pair_root) not in serialized
    assert str(fixture_root) not in serialized
    assert str(tmp_path) not in serialized
    assert all(item["id"] not in serialized for item in credentials)
    assert all(item["password"] not in serialized for item in credentials)
    assert '"cookie_value":' not in serialized
    assert '"password":' not in serialized


@pytest.mark.integration
def test_h0a_p2_injected_context_failure_cleans_runtime_root(tmp_path: Path) -> None:
    pair_root, fixture_root, _ = _build(tmp_path, "browser-fault")
    runtime_root = tmp_path / "browser-fault-runtime"
    with pytest.raises(BrowserIsolationError, match="INJECTED_BROWSER_CONTEXT_FAILURE"):
        run_multi_principal_browser_isolation(
            pair_root,
            fixture_root,
            runtime_root,
            allowed_parent=tmp_path,
            fault_after_context_count=1,
        )
    assert not runtime_root.exists()


def test_h0a_p2_public_harness_has_no_persistent_profile_port_or_live_root_authority() -> None:
    parameters = set(inspect.signature(run_multi_principal_browser_isolation).parameters)
    assert not parameters & {
        "user_data_dir",
        "profile_path",
        "persistent_context",
        "chrome_profile",
        "code_port",
        "notion_port",
        "production_root",
        "preview_root",
        "service_name",
    }
