from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from code_assist_v1.tools.verify_notion_h5e_ack_contract import (
    DEFAULT_CONTRACT,
    load_contract,
    validate_contract,
    verify_contract,
)


def test_checked_in_h5e_contract_is_valid_and_non_enabled() -> None:
    report = verify_contract()
    assert report == {
        "valid": True,
        "error_count": 0,
        "errors": [],
        "status": "proposed_not_authorized",
        "implemented": False,
        "destructive_ack_enabled": False,
    }


def test_h5e_contract_rejects_authorization_or_fence_weakening() -> None:
    contract = load_contract()
    enabled = deepcopy(contract)
    enabled["implementation"]["destructive_ack_enabled"] = True
    assert validate_contract(enabled)["valid"] is False

    no_superseded_fence = deepcopy(contract)
    no_superseded_fence["ack_eligibility_all"].remove("superseded_work_zero")
    assert validate_contract(no_superseded_fence)["valid"] is False

    weak_identity = deepcopy(contract)
    weak_identity["operation_identity_inputs"].remove("source_epoch")
    assert validate_contract(weak_identity)["valid"] is False


def test_h5e_contract_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    invalid = tmp_path / "duplicate.json"
    invalid.write_text('{"contract_version":1,"contract_version":1}', encoding="utf-8")
    report = verify_contract(invalid)
    assert report["valid"] is False
    assert "duplicate JSON object key" in report["errors"][0]


def test_h5e_contract_cli_is_read_only_and_deterministic() -> None:
    before = DEFAULT_CONTRACT.read_bytes()
    command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.verify_notion_h5e_ack_contract",
        "--contract",
        str(DEFAULT_CONTRACT),
    ]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["valid"] is True
    assert DEFAULT_CONTRACT.read_bytes() == before
