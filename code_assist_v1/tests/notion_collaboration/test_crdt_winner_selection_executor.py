from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = PACKAGE_ROOT / "tools" / "notion_parity" / "crdt_winner_selection_executor.py"
RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "checkpoints"
    / "ui-internal-collaboration-e0-p4-winner-selection-receipt-20260824.json"
)


@pytest.mark.unit
def test_e0_p4_executor_is_shell_free_offline_hash_locked_and_exact_root_guarded() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "shell=False" in source
    assert '"--offline"' in source
    assert '"--no-index"' in source
    assert '"--require-hashes"' in source
    assert '"--ignore-scripts"' in source
    assert "validate_run_root(root, require_marker=True)" in source
    assert "resolved.parent != resolved_root" in source
    assert "shutil.rmtree(resolved)" in source
    assert "socket." not in source
    assert "uvicorn.run" not in source
    assert "websockets.serve" not in source


@pytest.mark.unit
def test_e0_p4_preserved_runtime_receipt_is_redacted_exact_and_incomplete_truthful() -> None:
    raw = RECEIPT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "c358230bc289d8b1c2244ec46b8b5ebe939fd051fa0b4a595625044f7b0d490a"
    )
    text = raw.decode("utf-8")
    assert "C:\\Users" not in text
    assert "G:\\AI_Agent" not in text
    receipt = json.loads(text)
    assert receipt["status"] == "pass"
    assert receipt["selected_candidate"]["candidate_id"] == "PY_OPAQUE_NODE_COMPACTOR"
    assert receipt["mandatory_gate_results"]["selected_case_count"] == 53
    assert receipt["mandatory_gate_results"]["midstream_persist_reload_passed"] == 53
    assert receipt["mandatory_gate_results"]["final_restart_recovery_passed"] == 53
    assert receipt["pinned_artifacts"]["selected_package_count"] == 8
    assert receipt["license_audit"]["disallowed_selected_package_count"] == 0
    assert receipt["offline_reproducibility"]["python_pip_check_passed"] is True
    assert receipt["offline_reproducibility"]["node_npm_ls_passed"] is True
    assert receipt["isolation"]["candidate_owned_processes_remaining"] == 0
    assert receipt["isolation"]["global_runtime_lock_modified"] is False
    assert receipt["claims"]["runtime_service_implemented"] is False
    assert receipt["claims"]["runtime_enabled"] is False
    assert receipt["claims"]["product_complete"] is False
