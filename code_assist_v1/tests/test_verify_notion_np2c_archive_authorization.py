from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.verify_notion_np2c_archive_authorization import (
    AUTHORIZATION_CLAIMS,
    AUTHORIZATION_CONTRACT,
    inspect_authorization_record,
    main,
    validate_authorization_record,
    validate_authorization_record_for_inspection,
    verify_authorization_record,
)
from code_assist_v1.tools.verify_notion_np2c_archive_characterization import (
    DEFAULT_MATRIX,
)
from code_assist_v1.tools.verify_notion_np2c_archive_evidence_bundle import (
    REPOSITORY_ROOT,
)


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict, str, datetime]:
    root = tmp_path / "external-archive-authorization"
    root.mkdir()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    authorization = {
        "authorization_id": "authorization-01",
        "approved": True,
        "operator_id": "operator-01",
        "reviewer_id": "reviewer-01",
        "approved_at": (now - timedelta(hours=2)).isoformat(),
        "valid_from": (now - timedelta(hours=1)).isoformat(),
        "valid_until": (now + timedelta(hours=1)).isoformat(),
        "approved_tenants": [
            {
                "tenant_id": "tenant-business",
                "plan": "business",
                "synthetic": True,
                "approved": True,
            },
            {
                "tenant_id": "tenant-enterprise",
                "plan": "enterprise",
                "synthetic": True,
                "approved": True,
            },
        ],
        "normal_public_ui_only": True,
        "synthetic_fixture_only": True,
        "external_evidence_root_approved": True,
        "cleanup_approved": True,
        "public_link_scenario_approved": True,
        "plan_transition_scenario_approved": True,
        "production_or_personal_workspace_allowed": False,
        "api_private_endpoint_network_capture_allowed": False,
        "source_catalog_or_runtime_mutation_authorized": False,
    }
    matrix_sha256 = _sha256(DEFAULT_MATRIX.read_bytes())
    record = {
        "schema_version": 1,
        "contract": AUTHORIZATION_CONTRACT,
        "matrix_sha256": matrix_sha256,
        "authorization": authorization,
        "claims": deepcopy(AUTHORIZATION_CLAIMS),
    }
    path = root / "authorization.json"
    _write(path, record)
    return root, path, record, matrix_sha256, now


@pytest.mark.unit
def test_live_authorization_preflight_is_external_bounded_and_nonimplementing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, path, record, _, now = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(record["authorization"])

    report = verify_authorization_record(
        path,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
        execution_time=now,
    )

    assert report == {
        "valid": True,
        "status": "authorized_preflight",
        "authorized_to_begin_characterization": True,
        "error_count": 0,
        "errors": [],
        "approved_tenant_count": 2,
        "approved_plans": ["business", "enterprise"],
        "normal_public_ui_only": True,
        "authorization_sha256": approved_authorization_sha256,
        "approval_digest_matched": True,
        "implementation_authorized": False,
        "completion_claimed": False,
        "authorization_record_sha256": _sha256(path.read_bytes()),
    }
    assert main(
        [
            str(path),
            "--evidence-root",
            str(root),
            "--approved-authorization-sha256",
            approved_authorization_sha256,
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == report


@pytest.mark.unit
def test_inspection_exposes_candidate_digest_without_granting_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, path, record, _, _ = _fixture(tmp_path)
    candidate_sha256 = canonical_sha256(record["authorization"])

    report = inspect_authorization_record(path, root)

    assert report == {
        "inspection_valid": True,
        "valid": False,
        "status": "inspection_only_not_authorized",
        "authorized_to_begin_characterization": False,
        "error_count": 0,
        "errors": [],
        "approved_tenant_count": 2,
        "approved_plans": ["business", "enterprise"],
        "normal_public_ui_only": True,
        "authorization_sha256": candidate_sha256,
        "approval_digest_matched": False,
        "implementation_authorized": False,
        "completion_claimed": False,
        "authorization_record_sha256": _sha256(path.read_bytes()),
    }
    assert main(
        [str(path), "--evidence-root", str(root), "--inspect-only"]
    ) == 0
    assert json.loads(capsys.readouterr().out) == report


@pytest.mark.unit
def test_inspection_to_approved_preflight_is_exact_and_mutation_invalidates_digest(
    tmp_path: Path,
) -> None:
    root, path, record, _, now = _fixture(tmp_path)
    inspection = inspect_authorization_record(path, root)
    candidate_sha256 = inspection["authorization_sha256"]

    assert inspection["inspection_valid"] is True
    assert inspection["valid"] is False
    assert inspection["authorized_to_begin_characterization"] is False
    assert inspection["approval_digest_matched"] is False
    assert candidate_sha256 == canonical_sha256(record["authorization"])

    approved = verify_authorization_record(
        path,
        root,
        approved_authorization_sha256=candidate_sha256,
        execution_time=now,
    )
    assert approved["valid"] is True
    assert approved["authorized_to_begin_characterization"] is True
    assert approved["approval_digest_matched"] is True

    changed = deepcopy(record)
    changed["authorization"]["authorization_id"] = "authorization-02"
    _write(path, changed)
    drifted = verify_authorization_record(
        path,
        root,
        approved_authorization_sha256=candidate_sha256,
        execution_time=now,
    )
    assert drifted["valid"] is False
    assert drifted["authorized_to_begin_characterization"] is False
    assert drifted["approval_digest_matched"] is False


@pytest.mark.unit
def test_inspection_rejects_privilege_drift_and_cannot_be_used_as_approval(
    tmp_path: Path,
) -> None:
    root, path, record, matrix_sha256, _ = _fixture(tmp_path)
    changed = deepcopy(record)
    changed["authorization"]["source_catalog_or_runtime_mutation_authorized"] = True
    _write(path, changed)

    report = inspect_authorization_record(path, root)
    direct = validate_authorization_record_for_inspection(
        changed,
        matrix_sha256=matrix_sha256,
    )

    for result in (report, direct):
        assert result["inspection_valid"] is False
        assert result["valid"] is False
        assert result["authorized_to_begin_characterization"] is False
        assert result["approval_digest_matched"] is False
        assert result["implementation_authorized"] is False
        assert result["completion_claimed"] is False


@pytest.mark.unit
def test_authorization_contract_rejects_expiry_privilege_and_claim_drift(
    tmp_path: Path,
) -> None:
    _, _, record, matrix_sha256, now = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(record["authorization"])
    cases: list[tuple[str, dict, datetime]] = []

    changed = deepcopy(record)
    changed["authorization"]["approved"] = False
    cases.append(("not_approved", changed, now))

    changed = deepcopy(record)
    changed["authorization"]["operator_id"] = "reviewer-01"
    cases.append(("same_reviewer", changed, now))

    changed = deepcopy(record)
    changed["authorization"]["approved_tenants"].pop()
    cases.append(("missing_enterprise", changed, now))

    changed = deepcopy(record)
    changed["authorization"]["approved_tenants"][0]["synthetic"] = False
    cases.append(("real_tenant", changed, now))

    for field in (
        "normal_public_ui_only",
        "external_evidence_root_approved",
        "cleanup_approved",
        "public_link_scenario_approved",
        "plan_transition_scenario_approved",
    ):
        changed = deepcopy(record)
        changed["authorization"][field] = False
        cases.append((field, changed, now))

    for field in (
        "production_or_personal_workspace_allowed",
        "api_private_endpoint_network_capture_allowed",
        "source_catalog_or_runtime_mutation_authorized",
    ):
        changed = deepcopy(record)
        changed["authorization"][field] = True
        cases.append((field, changed, now))

    changed = deepcopy(record)
    changed["authorization"]["valid_until"] = (now + timedelta(hours=25)).isoformat()
    cases.append(("overlong_window", changed, now))

    cases.append(("expired", deepcopy(record), now + timedelta(hours=2)))

    changed = deepcopy(record)
    changed["matrix_sha256"] = "sha256:" + "0" * 64
    cases.append(("stale_matrix", changed, now))

    changed = deepcopy(record)
    changed["claims"]["live_characterized"] = True
    cases.append(("premature_live_claim", changed, now))

    changed = deepcopy(record)
    changed["authorization"]["operator_id"] = "operator@example.test"
    cases.append(("sensitive_identifier", changed, now))

    for name, candidate, execution_time in cases:
        report = validate_authorization_record(
            candidate,
            matrix_sha256=matrix_sha256,
            approved_authorization_sha256=approved_authorization_sha256,
            execution_time=execution_time,
        )
        assert report["valid"] is False, name
        assert report["authorized_to_begin_characterization"] is False, name
        assert report["implementation_authorized"] is False, name
        assert report["completion_claimed"] is False, name


@pytest.mark.unit
def test_preflight_rejects_repository_root_and_malformed_record(tmp_path: Path) -> None:
    root, path, record, _, now = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(record["authorization"])
    assert verify_authorization_record(
        path,
        REPOSITORY_ROOT,
        approved_authorization_sha256=approved_authorization_sha256,
        execution_time=now,
    )["valid"] is False

    wrong_digest = verify_authorization_record(
        path,
        root,
        approved_authorization_sha256="sha256:" + "0" * 64,
        execution_time=now,
    )
    assert wrong_digest["valid"] is False
    assert wrong_digest["approval_digest_matched"] is False

    duplicate = root / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )
    first = verify_authorization_record(
        duplicate,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
        execution_time=now,
    )
    second = verify_authorization_record(
        duplicate,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
        execution_time=now,
    )
    assert first == second
    assert first["valid"] is False
    assert first["errors"] == ["authorization record could not be verified"]


@pytest.mark.unit
def test_preflight_does_not_scan_external_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, path, record, _, now = _fixture(tmp_path)
    approved_authorization_sha256 = canonical_sha256(record["authorization"])

    def forbidden_scan(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("directory scan is forbidden")

    monkeypatch.setattr(Path, "iterdir", forbidden_scan)
    monkeypatch.setattr(Path, "glob", forbidden_scan)
    monkeypatch.setattr(Path, "rglob", forbidden_scan)

    assert verify_authorization_record(
        path,
        root,
        approved_authorization_sha256=approved_authorization_sha256,
        execution_time=now,
    )["valid"] is True


@pytest.mark.unit
def test_preflight_import_isolated_from_app_and_filesystem(tmp_path: Path) -> None:
    script = """
import json
from pathlib import Path
import sys
import code_assist_v1.tools.verify_notion_np2c_archive_authorization
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.app_notion',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.notion_store',
        'code_assist_v1.routes_notion',
        'code_assist_v1.routes_notion_bridge',
        'code_assist_v1.routes_notion_workspace',
    }
)
print(json.dumps({'forbidden': forbidden, 'entries': sorted(p.name for p in Path.cwd().iterdir())}))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"forbidden": [], "entries": []}
