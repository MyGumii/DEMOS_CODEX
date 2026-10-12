from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.crdt_candidate_benchmark_executor import (
    APPROVAL_CONTRACT,
    APPROVAL_ID,
    BenchmarkExecutionError,
    NPM_PACKAGES,
    PRIOR_APPROVAL_RECEIPT_SHA256,
    PYTHON_WHEELS,
    ROOT_MARKER_NAME,
    approval_request_descriptor,
    cleanup_owned_root,
    initialize_root,
    load_marker,
    scope_descriptor,
    scope_sha256,
    validate_approval_receipt,
    validate_run_root,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ROOT = PACKAGE_ROOT / "tools" / "notion_parity" / "spike_adapters"
EXECUTOR = PACKAGE_ROOT / "tools" / "notion_parity" / "crdt_candidate_benchmark_executor.py"
CHECKPOINT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "checkpoints"
EXPECTED_SCOPE_SHA256 = "sha256:f9245d712751e874ac24e35f37ef66be9dea3b706a8aa4af5712c1019d0283d0"


def _receipt() -> dict[str, object]:
    return {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "approved",
        "approved_by": "user",
        "approval_source": "explicit_conversation",
        "approved_at": "2026-08-23T18:00:00Z",
        "scope_sha256": EXPECTED_SCOPE_SHA256,
        "matrix_sha256": "sha256:a97bde3c677acc198e1f451619367dcec20c59a76aa4934fe7f0905718831d57",
        "corpus_sha256": "sha256:9be012b8150b3db48c66f743369a2b9905e234582056e28e427d77f311fca8b2",
        "prior_approval_receipt_sha256": PRIOR_APPROVAL_RECEIPT_SHA256,
        "single_use": True,
    }


def _write_receipt(tmp_path: Path, value: dict[str, object] | None = None) -> Path:
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(value or _receipt()), encoding="utf-8")
    return path


def _new_root(tmp_path: Path, suffix: str = "0" * 32) -> Path:
    root = tmp_path / f"code-assist-e0-p3-20260824-{suffix}"
    root.mkdir()
    return root


@pytest.mark.unit
def test_e0_p3_supplemental_scope_is_exact_single_use_and_narrow() -> None:
    request = approval_request_descriptor()
    scope = scope_descriptor()
    assert scope_sha256() == EXPECTED_SCOPE_SHA256
    assert request == {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "pending",
        "scope_sha256": EXPECTED_SCOPE_SHA256,
        "matrix_sha256": scope["matrix_sha256"],
        "corpus_sha256": scope["corpus_sha256"],
        "prior_approval_receipt_sha256": PRIOR_APPROVAL_RECEIPT_SHA256,
        "single_use": True,
        "scope": scope,
        "user_approval_phrase": f"{APPROVAL_ID} 승인",
    }
    assert len(scope["candidate_ids"]) == 3
    assert len(scope["python_wheels"]) == 12
    assert len(scope["npm_cache_packages"]) == 2
    assert scope["single_use"] is True
    assert all("sha256:" in item["sha256"] for item in scope["python_wheels"])
    assert all("product" in item or "service" in item or "identity" in item or "winner" in item or "artifact" in item or "schema" in item for item in scope["forbidden_effects"])


@pytest.mark.unit
def test_e0_p3_supplemental_receipt_rejects_prior_receipt_reuse_and_scope_tamper(
    tmp_path: Path,
) -> None:
    path = _write_receipt(tmp_path)
    assert validate_approval_receipt(path)["scope_sha256"] == EXPECTED_SCOPE_SHA256

    value = _receipt()
    value["scope_sha256"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BenchmarkExecutionError, match="scope_sha256 drift"):
        validate_approval_receipt(path)

    value = _receipt()
    value["approval_id"] = "AP-RUNTIME-SPIKE"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BenchmarkExecutionError, match="approval_id drift"):
        validate_approval_receipt(path)

    value = _receipt()
    value["extra"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BenchmarkExecutionError, match="fields are not exact"):
        validate_approval_receipt(path)


@pytest.mark.unit
def test_e0_p3_root_is_exact_direct_temp_child_with_exclusive_marker(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    receipt = _write_receipt(tmp_path)
    assert validate_run_root(root, temp_base=tmp_path) == root.resolve()
    marker = initialize_root(
        root,
        approval_receipt_path=receipt,
        run_id="E0P3-20260824-999",
        created_at="2026-08-23T18:00:01Z",
        temp_base=tmp_path,
    )
    assert marker["scope_sha256"] == EXPECTED_SCOPE_SHA256
    assert load_marker(root)["run_id"] == "E0P3-20260824-999"
    with pytest.raises(BenchmarkExecutionError, match="already exists"):
        initialize_root(
            root,
            approval_receipt_path=receipt,
            run_id="E0P3-20260824-999",
            created_at="2026-08-23T18:00:01Z",
            temp_base=tmp_path,
        )

    nested = root / "nested"
    nested.mkdir()
    with pytest.raises(BenchmarkExecutionError, match="direct OS-temp child"):
        validate_run_root(nested, temp_base=tmp_path)


@pytest.mark.unit
def test_e0_p3_cleanup_requires_exact_marker_and_run_id(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    receipt = _write_receipt(tmp_path)
    initialize_root(
        root,
        approval_receipt_path=receipt,
        run_id="E0P3-20260824-999",
        created_at="2026-08-23T18:00:01Z",
        temp_base=tmp_path,
    )
    with pytest.raises(BenchmarkExecutionError, match="run id"):
        cleanup_owned_root(
            root,
            expected_run_id="E0P3-20260824-998",
            temp_base=tmp_path,
        )
    assert root.exists()
    cleanup_owned_root(
        root,
        expected_run_id="E0P3-20260824-999",
        temp_base=tmp_path,
    )
    assert not root.exists()


@pytest.mark.unit
def test_e0_p3_exact_artifact_denominators_match_e0_p2_lock() -> None:
    assert len(PYTHON_WHEELS) == 12
    assert {item[0] for item in PYTHON_WHEELS} == {
        "anyio",
        "click",
        "colorama",
        "h11",
        "idna",
        "pycrdt",
        "pycrdt-store",
        "pycrdt-websocket",
        "sqlite-anyio",
        "typing-extensions",
        "uvicorn",
        "websockets",
    }
    assert len({item[2] for item in PYTHON_WHEELS}) == 12
    assert len({item[3] for item in PYTHON_WHEELS}) == 12
    assert NPM_PACKAGES == (
        (
            "yjs",
            "13.6.32",
            "sha512:lfiJIIC4Xayt5ItynE407ehlE03pCjeOc4hkR4yxxvvNJ4kuiN25B0g+Qp8XagYz361LLL7DCzR5bvFJ81QKtQ==",
        ),
        (
            "@hocuspocus/server",
            "4.6.0",
            "sha512:Ex4tka1vsm9NycHU1vPwxj3weQj+L2+736U8uentRyykAefUcYkHEh7Gfxiy9PHBholWcw1H+oYgZ3LP0ShTWQ==",
        ),
    )


@pytest.mark.unit
def test_e0_p3_executor_and_relay_sources_are_shell_free_no_port_and_offline_npm() -> None:
    executor = EXECUTOR.read_text(encoding="utf-8")
    relay = (ADAPTER_ROOT / "py_opaque_benchmark_relay_adapter.py").read_text(encoding="utf-8")
    assert "shell=False" in executor
    assert '"--offline"' in executor
    assert '"--ignore-scripts"' in executor
    assert '"--no-index"' in executor
    assert "socket." not in relay
    assert ".run(" not in relay
    assert "uvicorn.run" not in relay
    assert "websockets.serve" not in relay


@pytest.mark.unit
def test_e0_p3_marker_tamper_fails_closed(tmp_path: Path) -> None:
    root = _new_root(tmp_path)
    receipt = _write_receipt(tmp_path)
    initialize_root(
        root,
        approval_receipt_path=receipt,
        run_id="E0P3-20260824-999",
        created_at="2026-08-23T18:00:01Z",
        temp_base=tmp_path,
    )
    marker_path = root / ROOT_MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["root_name"] = "other"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(BenchmarkExecutionError, match="binding drift"):
        load_marker(root)


@pytest.mark.unit
def test_e0_p3_scope_hash_changes_on_any_effect_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = scope_descriptor()
    expanded = copy.deepcopy(baseline)
    expanded["allowed_effects"].append("modify a product lockfile")
    encoded = json.dumps(expanded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    assert "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest() != EXPECTED_SCOPE_SHA256


@pytest.mark.unit
def test_e0_p3_preserved_benchmark_receipt_proves_exact_runtime_denominators() -> None:
    import hashlib

    path = CHECKPOINT_ROOT / "ui-internal-collaboration-e0-p3-candidate-benchmark-receipt-20260824.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "15f7d5fa52844adc5b576d34d6ebe8cd5415446c61f465431c67c9b0f75d67cc"
    text = raw.decode("utf-8")
    assert "C:\\\\Users" not in text
    assert "G:\\\\AI_Agent" not in text
    receipt = json.loads(text)
    assert receipt["status"] == "pass"
    assert receipt["benchmark_denominators"] == {
        "scenario_count": 15,
        "case_count_per_candidate": 53,
        "candidate_count": 3,
        "total_candidate_case_count": 159,
        "raw_report_sha256": "sha256:4e4838c777e97f44e097c4855da6c673a3e40455a6f9e663c24f52459c128350",
        "raw_report_bytes": 306967,
        "duration_seconds": 2.995591,
    }
    assert receipt["mandatory_results"]["zero_loss_candidate_count"] == 3
    assert all(
        receipt["mandatory_results"][key] == 0
        for key in (
            "acknowledged_character_loss_count",
            "convergence_mismatch_count",
            "invalid_unicode_count",
            "duplicate_effect_count",
            "ime_intermediate_published_count",
            "restart_replay_failure_count",
            "compaction_equivalence_failure_count",
            "cross_candidate_convergence_mismatch_count",
        )
    )
    assert receipt["materialization"]["python_wheel_count"] == 12
    assert receipt["materialization"]["pip_check_passed"] == 2
    assert receipt["materialization"]["npm_ls_passed"] == 2
    assert receipt["materialization"]["npm_registry_integrity_missing"] == 0
    assert receipt["opaque_relay_results"]["case_count"] == 53
    assert receipt["opaque_relay_results"]["midstream_persist_reload_passed"] == 53
    assert receipt["opaque_relay_results"]["final_restart_recovery_passed"] == 53
    assert len(receipt["canonical_scenario_hashes"]) == 15
    assert {item["candidate_id"] for item in receipt["candidate_results"]} == {
        "PY_NATIVE_PYCRDT",
        "PY_OPAQUE_NODE_COMPACTOR",
        "NODE_YJS_HOCUSPOCUS",
    }
    assert all(item["peak_process_rss_bytes"] > 10_000_000 for item in receipt["candidate_results"])
    assert receipt["isolation_and_claims"]["candidate_winner_selected"] is False
    assert receipt["isolation_and_claims"]["product_runtime_changed"] is False
