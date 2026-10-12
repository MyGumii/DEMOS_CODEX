from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    GenerationIntegrityError,
    initialize_synthetic_collaborative_realm,
)
from code_assist_v1.notion_collaboration.selected_runtime_backup_prototype import (
    build_selected_runtime_exports,
    create_selected_runtime_generation,
    restore_selected_runtime_generation,
    validate_selected_runtime_exports,
    verify_selected_runtime_generation,
    verify_selected_runtime_restored_root,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT,
    build_compaction_request,
    runtime_binding_sha256,
)
from code_assist_v1.tools.notion_parity.run_selected_runtime_backup_benchmark import (
    ADAPTER_SOURCE,
    run_benchmark,
    validate_benchmark_receipt,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "checkpoints"
    / "ui-internal-collaboration-o0a-p3-selected-runtime-backup-receipt-20260824.json"
)


def _synthetic_exports() -> dict[str, dict[str, object]]:
    updates = [b"synthetic-yjs-update-a", b"synthetic-yjs-update-b"]
    update_hashes = ["sha256:" + hashlib.sha256(item).hexdigest() for item in updates]
    request = build_compaction_request(
        request_id="request-test-1",
        document_id="document-test-1",
        workspace_epoch=2,
        page_epoch=3,
        base_snapshot_id=None,
        base_high_water_sequence=0,
        target_high_water_sequence=2,
        ordered_update_bytes=sum(map(len, updates)),
        ordered_update_sha256=update_hashes,
    )
    snapshot = b"synthetic-selected-snapshot"
    state_vector = b"synthetic-state-vector"
    digest = lambda value: "sha256:" + hashlib.sha256(value).hexdigest()
    response = {
        "contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "base_high_water_sequence": 0,
        "target_high_water_sequence": 2,
        "applied_update_count": 2,
        "snapshot_bytes": len(snapshot),
        "snapshot_sha256": digest(snapshot),
        "state_vector_bytes": len(state_vector),
        "state_vector_sha256": digest(state_vector),
        "runtime_binding_sha256": runtime_binding_sha256(),
    }
    expected_text = digest(b"synthetic semantic text")
    return build_selected_runtime_exports(
        request=request,
        response=response,
        ordered_updates_base64=[base64.b64encode(item).decode("ascii") for item in updates],
        snapshot_base64=base64.b64encode(snapshot).decode("ascii"),
        state_vector_base64=base64.b64encode(state_vector).decode("ascii"),
        expected_text_sha256=expected_text,
        observed_text_sha256=expected_text,
    )


@pytest.mark.unit
@pytest.mark.scenario("OPS-001")
def test_o0a_p3_selected_update_snapshot_and_request_high_water_are_one_binding() -> None:
    exports = _synthetic_exports()
    validated = validate_selected_runtime_exports(exports)
    assert validated["event_ledger"]["target_high_water_sequence"] == 2
    assert validated["event_ledger"]["ordered_update_count"] == 2
    assert validated["snapshot"]["target_high_water_sequence"] == 2
    assert validated["snapshot"]["runtime_binding_sha256"] == runtime_binding_sha256()

    tampered = copy.deepcopy(exports)
    tampered["event_ledger"]["updates_base64"][0] = base64.b64encode(b"tampered").decode("ascii")
    with pytest.raises(GenerationIntegrityError, match="binding drift|hash"):
        validate_selected_runtime_exports(tampered)


@pytest.mark.integration
@pytest.mark.restore
@pytest.mark.scenario("OPS-002")
@pytest.mark.scenario("OPS-003")
def test_o0a_p3_selected_snapshot_is_manifest_bound_and_materialized_only_in_new_root(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    generation = create_selected_runtime_generation(
        realm.root,
        "selected-generation-test",
        selected_runtime_exports=_synthetic_exports(),
    )
    verified = verify_selected_runtime_generation(realm.root, generation.generation.generation_id)
    assert verified["target_high_water_sequence"] == 2
    assert verified["snapshot_sha256"] == generation.snapshot_sha256

    restored = restore_selected_runtime_generation(
        realm.root,
        generation.generation.generation_id,
        tmp_path / "restored",
    )
    report = verify_selected_runtime_restored_root(restored.restore.target_root)
    assert report["status"] == "pending_application_validation"
    assert (restored.restore.target_root / "collaboration-runtime" / "selected-snapshot.bin").is_file()
    assert restored.restore.session_count == 0


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.restore
@pytest.mark.scenario("OPS-006")
def test_o0a_p3_selected_runtime_crash_restart_compaction_and_restore_benchmark() -> None:
    report = run_benchmark(iterations=1)
    assert report["status"] == "pass"
    assert report["denominators"]["forced_compactor_crash_count"] == 1
    assert report["crash_restart"]["forced_crash_returncode"] == 91
    assert report["crash_restart"]["acknowledged_update_loss_count"] == 0
    assert report["crash_restart"]["convergence_mismatch_count"] == 0
    assert report["measurements"]["observed_rpo_seconds"] == 0
    assert report["provisional_engineering_targets"]["rto_passed"] is True
    assert report["provisional_engineering_targets"]["write_drain_passed"] is True
    assert report["claims"]["full_target_restore_lane_closed"] is False
    assert report["cleanup"]["owned_root_removed"] is True


@pytest.mark.unit
def test_o0a_p3_selected_adapter_has_no_network_start_surface() -> None:
    source = ADAPTER_SOURCE.read_text(encoding="utf-8")
    assert ".listen(" not in source
    assert "createServer(" not in source
    assert "network_listener: false" in source
    assert "port_bound: false" in source


@pytest.mark.unit
def test_o0a_p3_preserved_five_iteration_benchmark_receipt_is_self_bound() -> None:
    report = validate_benchmark_receipt(json.loads(BENCHMARK_RECEIPT.read_text(encoding="utf-8")))
    assert report["denominators"]["iteration_count"] == 5
    assert report["denominators"]["successful_new_root_restore_count"] == 5
    assert report["measurements"]["new_root_restore_to_application_validated_ms"]["p99"] > 0
    assert report["claims"]["full_target_restore_lane_closed"] is False
    assert report["claims"]["product_complete"] is False
