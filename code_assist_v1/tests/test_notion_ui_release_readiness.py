from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from code_assist_v1.tools.verify_notion_core_free_ui_release_readiness import (
    CONTRACT,
    DEFAULT_SNAPSHOT,
    PROJECT_ROOT,
    _validate_pilot_control,
    verify_release_readiness,
)


def _snapshot() -> dict[str, object]:
    return json.loads(DEFAULT_SNAPSHOT.read_text(encoding="utf-8"))


def _write(tmp_path: Path, value: dict[str, object]) -> Path:
    path = tmp_path / "release-readiness.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_release_readiness_rejects_superseded_historical_snapshot() -> None:
    report, exit_code = verify_release_readiness()
    assert exit_code == 1
    assert report == {
        "contract": CONTRACT,
        "status": "fail",
        "release_state": "unverified",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "development_evidence_complete": False,
        "release_ready": False,
        "pending_gates": [
            "formal_accessibility_review",
            "legal_brand_review",
            "operational_backup_restore",
            "live_canary",
        ],
        "errors": [
            {
                "code": "SHA256_MISMATCH",
                "field": "inputs.completion_matrix.sha256",
                "message": "bound file hash drifted",
            },
            {
                "code": "SHA256_MISMATCH",
                "field": "inputs.evidence_review_gate.sha256",
                "message": "bound file hash drifted",
            },
        ],
    }


def test_release_readiness_rejects_premature_release_claim(tmp_path: Path) -> None:
    value = _snapshot()
    value["claims"]["release_ready"] = True  # type: ignore[index]
    report, exit_code = verify_release_readiness(_write(tmp_path, value))
    assert exit_code == 1
    assert any(item["code"] == "CLAIMS_INVALID" for item in report["errors"])


def test_release_readiness_rejects_human_review_self_promotion(tmp_path: Path) -> None:
    value = _snapshot()
    value["gates"]["formal_accessibility_review"] = "passed"  # type: ignore[index]
    report, exit_code = verify_release_readiness(_write(tmp_path, value))
    assert exit_code == 1
    assert any(item["code"] == "GATES_INVALID" for item in report["errors"])


def test_release_readiness_rejects_bound_evidence_hash_drift(tmp_path: Path) -> None:
    value = _snapshot()
    value["inputs"]["completion_matrix"]["sha256"] = "sha256:" + "0" * 64  # type: ignore[index]
    report, exit_code = verify_release_readiness(_write(tmp_path, value))
    assert exit_code == 1
    assert any(item["code"] == "SHA256_MISMATCH" for item in report["errors"])


def test_release_readiness_rejects_pilot_control_self_enable(tmp_path: Path) -> None:
    value = _snapshot()
    pilot_path = PROJECT_ROOT / value["inputs"]["pilot_control"]["path"]  # type: ignore[index]
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    pilot["claims"]["live_feature_enabled"] = True
    errors: list[dict[str, str]] = []

    _validate_pilot_control(pilot, errors)

    assert any(item["code"] == "PILOT_CONTROL_INVALID" for item in errors)


def test_release_readiness_import_is_side_effect_free(tmp_path: Path) -> None:
    script = """
import json
from pathlib import Path
import sys
root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
import code_assist_v1.tools.verify_notion_core_free_ui_release_readiness  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.data_paths',
        'code_assist_v1.knowledge_core',
    }
    or name.startswith('code_assist_v1.routes_')
)
print(json.dumps({'changed': before != after, 'forbidden': forbidden}, sort_keys=True))
"""
    environment = {"PYTHONPATH": str(PROJECT_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}
