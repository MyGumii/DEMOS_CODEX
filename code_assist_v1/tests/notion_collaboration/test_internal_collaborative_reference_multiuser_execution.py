from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution as execution_verifier
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    CONTRACT_PATH,
    EXACT_APPROVAL_TEXT,
    EXPECTED_ACTIONS,
    EXPECTED_STATES,
    verify,
)


def _manifest() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.mark.unit
def test_v0r_p1_preapproval_execution_contract_passes_without_authorizing_execution() -> None:
    report = verify()

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["action_count"] == len(EXPECTED_ACTIONS) == 11
    assert report["capture_state_count"] == len(EXPECTED_STATES) == 18
    assert report["expected_screenshot_count"] == 36
    assert report["ready_for_owner_approval"] is True
    assert report["execution_authorized"] is False
    assert report["external_mutation_executed"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_v0r_p1_contract_binds_the_exact_requested_approval_and_forbidden_scope() -> None:
    manifest = _manifest()

    assert manifest["approval"]["exact_requested_text"] == EXACT_APPROVAL_TEXT
    assert manifest["approval"]["owner_receipt"] is None
    assert manifest["approval"]["external_mutation_authorized"] is False
    assert manifest["scope"]["workspace_aliases"] == ["REF-FREE-A", "REF-FREE-B"]
    assert manifest["scope"]["identity_aliases"] == ["REF-OWNER", "REF-COLLABORATOR"]
    assert manifest["scope"]["personal_workspace_access_allowed"] is False
    assert manifest["scope"]["personal_page_access_allowed"] is False
    assert manifest["scope"]["public_publish_or_public_link_allowed"] is False
    assert manifest["scope"]["permanent_delete_allowed"] is False
    assert manifest["scope"]["production_or_second_brain_mutation_allowed"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (
            lambda value: value["source_bindings"][0].__setitem__(
                "sha256", "sha256:" + "0" * 64
            ),
            "SOURCE_HASH_DRIFT",
        ),
        (
            lambda value: value["approval"].__setitem__(
                "external_mutation_authorized", True
            ),
            "APPROVAL_BOUNDARY",
        ),
        (
            lambda value: value["scope"].__setitem__(
                "personal_workspace_access_allowed", True
            ),
            "FORBIDDEN_SCOPE",
        ),
        (
            lambda value: value["capture_environment"].__setitem__(
                "viewports", [{"width": 1440, "height": 900}]
            ),
            "CAPTURE_ENVIRONMENT",
        ),
        (
            lambda value: value["capture_states"].pop(),
            "CAPTURE_STATE_DENOMINATOR",
        ),
        (
            lambda value: value["cleanup_contract"].__setitem__(
                "permanent_delete_allowed", True
            ),
            "CLEANUP_CONTRACT",
        ),
        (
            lambda value: value["claims"].__setitem__(
                "external_reference_mutation_executed", True
            ),
            "CLAIMS_FALSE_GREEN",
        ),
    ],
)
def test_v0r_p1_contract_rejects_scope_denominator_hash_and_false_green_tamper(
    tmp_path: Path,
    mutate,
    error_code: str,
) -> None:
    manifest = copy.deepcopy(_manifest())
    mutate(manifest)
    report = verify(_write(tmp_path / "candidate.json", manifest))

    assert report["status"] == "fail"
    assert error_code in {item["code"] for item in report["errors"]}
    assert report["ready_for_owner_approval"] is False
    assert report["execution_authorized"] is False


@pytest.mark.unit
def test_v0r_p1_contract_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    candidate = tmp_path / "duplicate.json"
    candidate.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    report = verify(candidate)

    assert report["status"] == "fail"
    assert report["errors"][0]["code"] == "CONTRACT_READ_FAILED"


@pytest.mark.unit
def test_v0r_p1_adopted_surface_transition_accepts_only_the_sealed_current_hash() -> None:
    manifest = _manifest()
    binding = next(
        item
        for item in manifest["source_bindings"]
        if item["path"] == execution_verifier.ADOPTED_SURFACE_REPO_PATH
    )
    current_path = execution_verifier._repository_file(
        execution_verifier.ADOPTED_SURFACE_REPO_PATH
    )

    assert current_path is not None
    current_sha256 = execution_verifier._sha256_file(current_path)
    assert execution_verifier._historical_source_transition_valid(
        binding["path"], binding["sha256"], current_sha256
    )
    assert not execution_verifier._historical_source_transition_valid(
        binding["path"], binding["sha256"], "sha256:" + "f" * 64
    )
    assert not execution_verifier._historical_source_transition_valid(
        binding["path"], "sha256:" + "0" * 64, current_sha256
    )
