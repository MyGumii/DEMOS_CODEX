from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from code_assist_v1.tools.verify_notion_np2b_navigation_contract import (
    load_contract,
    validate_contract,
    verify_contract,
)


def test_checked_in_np2b_navigation_ui_is_contract_tested_and_default_off() -> None:
    assert verify_contract() == {
        "valid": True,
        "error_count": 0,
        "errors": [],
        "status": "ui_contract_tested_default_off",
        "backend_schema_implemented": True,
        "backend_store_api_implemented": True,
        "routes_enabled": True,
        "ui_enabled": False,
        "recents_write_enabled": False,
    }


def test_np2b_navigation_contract_rejects_runtime_or_security_weakening() -> None:
    contract = load_contract()

    disabled = deepcopy(contract)
    disabled["implementation"]["routes_enabled"] = False
    assert validate_contract(disabled)["valid"] is False

    ui_enabled = deepcopy(contract)
    ui_enabled["implementation"]["ui_enabled"] = True
    assert validate_contract(ui_enabled)["valid"] is False

    missing_backend = deepcopy(contract)
    missing_backend["implementation"]["backend_store_api_implemented"] = False
    assert validate_contract(missing_backend)["valid"] is False

    schema_drift = deepcopy(contract)
    schema_drift["schema_contract"]["target_checksum"] = "sha256:" + "0" * 64
    assert validate_contract(schema_drift)["valid"] is False

    source_read = deepcopy(contract)
    source_read["read_invariants"]["legacy_source_database_open_count"] = 1
    assert validate_contract(source_read)["valid"] is False

    leak = deepcopy(contract)
    leak["canonical_identity"]["hidden_rows_omitted_and_dense_reindexed"] = False
    assert validate_contract(leak)["valid"] is False

    no_ledger = deepcopy(contract)
    no_ledger["mutation_invariants"]["operation_audit_outbox_atomic"] = False
    assert validate_contract(no_ledger)["valid"] is False

    duplicate_keys = deepcopy(contract)
    duplicate_keys["mutation_invariants"]["duplicate_json_keys_rejected"] = False
    assert validate_contract(duplicate_keys)["valid"] is False

    oversized = deepcopy(contract)
    oversized["mutation_invariants"]["navigation_json_max_bytes"] = 100 * 1024 * 1024
    assert validate_contract(oversized)["valid"] is False

    default_on = deepcopy(contract)
    default_on["ui_contract"]["default_enabled"] = True
    assert validate_contract(default_on)["valid"] is False

    offline_queue = deepcopy(contract)
    offline_queue["ui_contract"]["offline_action_queue_enabled"] = True
    assert validate_contract(offline_queue)["valid"] is False

    resolver_cutover = deepcopy(contract)
    resolver_cutover["ui_contract"]["content_resolver_switched"] = True
    assert validate_contract(resolver_cutover)["valid"] is False


def test_np2b_navigation_contract_rejects_duplicate_keys(tmp_path: Path) -> None:
    invalid = tmp_path / "duplicate.json"
    invalid.write_text('{"contract_version":1,"contract_version":1}', encoding="utf-8")
    report = verify_contract(invalid)
    assert report["valid"] is False
    assert "duplicate JSON object key" in report["errors"][0]
