from __future__ import annotations

import json
from pathlib import Path

import pytest

import code_assist_v1.tools.verify_internal_collaborative_e0_runtime_lock_successor as target


def _write(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.unit
def test_e0_runtime_lock_successor_is_exact_pinned_and_non_authorizing() -> None:
    report = target.verify()

    assert report["status"] == "pass", report
    assert report["compatibility_passed"] is True
    assert report["checkpoint_sha256"] == target.CHECKPOINT_SHA256
    assert report["frozen_runtime_lock_sha256"] == target.FROZEN_RUNTIME_LOCK_SHA256
    assert report["current_runtime_lock_sha256"] == target.CURRENT_RUNTIME_LOCK_SHA256
    assert report["runtime_package_count"] == 22
    assert report["forbidden_global_package_count"] == 0
    assert report["selected_runtime_remains_separately_locked"] is True
    assert report["runtime_enabled"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_e0_runtime_lock_successor_accepts_only_the_frozen_e0_binding() -> None:
    assert target.accepts_frozen_runtime_lock_binding(
        target.RUNTIME_LOCK_RELATIVE,
        target.FROZEN_RUNTIME_LOCK_SHA256,
    )
    assert not target.accepts_frozen_runtime_lock_binding(
        "code_assist_v1/another.lock",
        target.FROZEN_RUNTIME_LOCK_SHA256,
    )
    assert not target.accepts_frozen_runtime_lock_binding(
        target.RUNTIME_LOCK_RELATIVE,
        "sha256:" + "0" * 64,
    )


@pytest.mark.unit
def test_e0_runtime_lock_successor_rejects_checkpoint_substitution(
    tmp_path: Path,
) -> None:
    value = json.loads(target.CHECKPOINT_PATH.read_text(encoding="utf-8"))
    value["claims"]["runtime_enabled"] = True
    candidate = _write(tmp_path / "substituted.json", value)

    report = target.verify(candidate)

    assert report["status"] == "fail"
    assert {item["code"] for item in report["errors"]}.issuperset(
        {"CHECKPOINT_HASH", "CHECKPOINT_SELF_HASH", "CLAIMS"}
    )
    assert report["new_packet_or_gate_credit_awarded"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_e0_runtime_lock_successor_rejects_candidate_dependency_in_global_lock(
    tmp_path: Path,
) -> None:
    current = target.RUNTIME_LOCK_PATH.read_text(encoding="utf-8")
    changed = tmp_path / "requirements-runtime.lock"
    changed.write_text(current + "\npycrdt==0.14.3\n", encoding="utf-8")

    report = target.verify(runtime_lock_path=changed)

    assert report["status"] == "fail"
    assert {item["code"] for item in report["errors"]}.issuperset(
        {"RUNTIME_LOCK_TRANSITION", "CANDIDATE_ISOLATION"}
    )
    assert report["forbidden_global_package_count"] == 1
    assert report["runtime_enabled"] is False


@pytest.mark.unit
def test_e0_runtime_lock_successor_rejects_unpinned_runtime_bytes(
    tmp_path: Path,
) -> None:
    current = target.RUNTIME_LOCK_PATH.read_text(encoding="utf-8")
    changed = tmp_path / "requirements-runtime.lock"
    changed.write_text(current.replace("Flask==3.1.3", "Flask==3.1.4"), encoding="utf-8")

    report = target.verify(runtime_lock_path=changed)

    assert report["status"] == "fail"
    assert "RUNTIME_LOCK_TRANSITION" in {
        item["code"] for item in report["errors"]
    }
    assert report["product_complete"] is False


@pytest.mark.unit
def test_e0_runtime_lock_successor_cli_is_read_only_and_green(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert target.main(["--compact"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["runtime_enabled"] is False
    assert report["new_packet_or_gate_credit_awarded"] is False
