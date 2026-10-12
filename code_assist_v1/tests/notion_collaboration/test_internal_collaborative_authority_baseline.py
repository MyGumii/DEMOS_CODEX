from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from code_assist_v1.tools.verify_internal_collaborative_authority_baseline import (
    DEFAULT_CONTRACT,
    verify,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _codes(report: dict[str, object]) -> set[str]:
    return {str(item["code"]) for item in report["errors"]}


def test_a0b_p1_checked_in_authority_baseline_is_exact_and_incomplete_product_truth() -> None:
    report = verify()
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["source_schema_version"] == 2026081317
    assert report["target_schema_version"] == 2026082401
    assert report["target_object_count"] == 213
    assert report["matrix_row_ids"] == ["DATA-001", "DATA-002"]
    assert report["product_complete"] is False


def test_a0b_p1_hash_lineage_cutover_and_false_green_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    value = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    value["workspace_schema_lineage"]["parallel_reserved_version"] = 2026082401
    value["cutover_and_projection_contract"]["production_cutover_authorized"] = True
    value["artifact_sha256"][
        "code_assist_v1/notion_collaboration/cutover.py"
    ] = "sha256:" + "0" * 64
    value["claims"]["actual_dev_pair_cutover_enabled"] = True
    report = verify(_write(tmp_path / "tampered.json", value))
    assert report["status"] == "fail"
    assert {"SCHEMA_LINEAGE", "SCHEMA_COLLISION", "CUTOVER_BOUNDARY", "ARTIFACT_HASH", "FALSE_GREEN"}.issubset(
        _codes(report)
    )


def test_a0b_p1_duplicate_and_nonfinite_json_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert _codes(verify(duplicate)) == {"CONTRACT_READ_FAILED"}
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema_version":NaN}', encoding="utf-8")
    assert _codes(verify(nonfinite)) == {"CONTRACT_READ_FAILED"}


def test_a0b_p1_verifier_cli_passes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_authority_baseline",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
