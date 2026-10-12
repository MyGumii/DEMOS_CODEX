from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    FAULT_POINTS,
    IncompleteGenerationError,
    InjectedBackupFailure,
    MANDATORY_COMPONENT_IDS,
    SyntheticBackupBoundaryError,
    SyntheticCollaborativeRealm,
    create_coordinated_generation,
    initialize_synthetic_collaborative_realm,
    verify_coordinated_generation,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    LocalGenerationIntegrityError,
    RETENTION_POLICY,
    RPO_MINUTES,
    RTO_MINUTES,
    create_local_workspace_generation,
    verify_local_workspace_generation,
)
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.search_authority import CollaborativeSearchAuthority
from code_assist_v1.tests.notion_collaboration.o1a_p1_backup_fixture import (
    build_o1a_backup_environment,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

_BACKUP_WORKER = r"""
import json
import os
import sys
from code_assist_v1.notion_collaboration.coordinated_backup_prototype import create_coordinated_generation

root, generation_id, signal = sys.argv[1:]
result = create_coordinated_generation(
    root,
    generation_id,
    barrier_signal_path=signal,
    hold_after_barrier_seconds=0.75,
)
print(json.dumps({
    "pid": os.getpid(),
    "manifest_sha256": result.manifest_sha256,
    "document_sequence": result.notion_document_sequence,
    "event_sequence": result.notion_event_sequence,
}, sort_keys=True), flush=True)
"""

_WRITER_WORKER = r"""
import json
import os
import sys
from code_assist_v1.notion_collaboration.coordinated_backup_prototype import SyntheticCollaborativeRealm

root = sys.argv[1]
realm = SyntheticCollaborativeRealm.open(root)
result = realm.save_page(
    "writer-after-barrier",
    expected_revision=1,
    operation_id="op-o0a-p2-cross-process-writer-0001",
)
print(json.dumps({"pid": os.getpid(), "revision": result["revision"]}, sort_keys=True), flush=True)
"""


def _payload(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "worker emitted no JSON receipt"
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


def _wait_for(path: Path, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.scenario("OPS-001")
def test_o0a_p2_cross_process_writer_cannot_cross_generation_high_water(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    signal = realm.root / "barrier-acquired.signal"
    backup = subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-c",
            _BACKUP_WORKER,
            os.fspath(realm.root),
            "generation-boundary",
            os.fspath(signal),
        ],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for(signal)
    writer = subprocess.Popen(
        [sys.executable, "-E", "-c", _WRITER_WORKER, os.fspath(realm.root)],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    backup_stdout, backup_stderr = backup.communicate(timeout=20)
    writer_stdout, writer_stderr = writer.communicate(timeout=20)
    assert backup.returncode == 0, backup_stderr
    assert writer.returncode == 0, writer_stderr
    backup_receipt = _payload(backup_stdout)
    writer_receipt = _payload(writer_stdout)
    assert backup_receipt["pid"] != writer_receipt["pid"]
    assert backup_receipt["pid"] != os.getpid()
    assert writer_receipt["pid"] != os.getpid()
    assert writer_receipt["revision"] == 2

    manifest = verify_coordinated_generation(realm.root, "generation-boundary")
    notion_record = next(
        item for item in manifest["authority_records"] if item["authority_domain"] == "notion"
    )
    snapshot = realm.backup_root / "generations" / "generation-boundary" / notion_record["snapshot_relative_path"]
    connection = sqlite3.connect(snapshot)
    try:
        snapshot_revision = int(
            connection.execute("SELECT revision FROM pages WHERE id='page-a'").fetchone()[0]
        )
    finally:
        connection.close()
    assert snapshot_revision == 1
    assert SyntheticCollaborativeRealm.open(realm.root).source_page_revision() == 2
    assert backup_receipt["document_sequence"] == 1
    assert manifest["publication_boundary"]["cross_database_transaction_claimed"] is False
    assert tuple(item["component_id"] for item in manifest["component_records"]) == MANDATORY_COMPONENT_IDS


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-006")
@pytest.mark.parametrize("fault", sorted(point for point in FAULT_POINTS if point is not None))
def test_o0a_p2_every_generation_failpoint_leaves_no_false_completion(
    tmp_path: Path, fault: str
) -> None:
    ordered_faults = sorted(point for point in FAULT_POINTS if point is not None)
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    before_revision = realm.source_page_revision()
    generation_id = "failed-" + str(ordered_faults.index(fault))
    with pytest.raises(InjectedBackupFailure):
        create_coordinated_generation(realm.root, generation_id, fault=fault)
    final = realm.backup_root / "generations" / generation_id
    staging = realm.backup_root / "staging" / generation_id
    assert not final.exists()
    receipt = json.loads((staging / "incomplete-generation.json").read_text(encoding="utf-8"))
    assert receipt == {
        "error_code": "INJECTED_BACKUP_FAILURE",
        "format_id": "internal-collaborative-coordinated-generation-v1",
        "generation_id": generation_id,
        "restorable": False,
        "state": "incomplete",
        "updated_at": receipt["updated_at"],
    }
    with pytest.raises(IncompleteGenerationError):
        verify_coordinated_generation(realm.root, generation_id)
    assert SyntheticCollaborativeRealm.open(realm.root).source_page_revision() == before_revision


@pytest.mark.unit
def test_o0a_p2_realm_and_generation_paths_are_marker_and_temp_bound(tmp_path: Path) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    with pytest.raises(SyntheticBackupBoundaryError):
        initialize_synthetic_collaborative_realm(Path.cwd() / "not-temp-realm")
    with pytest.raises(SyntheticBackupBoundaryError):
        initialize_synthetic_collaborative_realm(realm.root)
    create_coordinated_generation(realm.root, "generation-once")
    with pytest.raises(SyntheticBackupBoundaryError):
        create_coordinated_generation(realm.root, "generation-once")


@pytest.mark.integration
@pytest.mark.restore
@pytest.mark.scenario("OPS-001")
@pytest.mark.scenario("OPS-004")
def test_o1a_p1_separate_authorities_share_one_verified_local_generation_high_water(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    created = create_local_workspace_generation(
        environment.source_root,
        "o1a-high-water",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe,
    )
    manifest = verify_local_workspace_generation(
        environment.source_root,
        created.generation_id,
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
    )
    assert manifest["authority_order"] == ["notion", "second_brain"]
    assert manifest["publication_boundary"]["cross_database_transaction_claimed"] is False
    assert manifest["publication_boundary"]["separate_canonical_authorities_preserved"] is True
    assert manifest["publication_boundary"]["link_count"] == len(
        environment.probe.second_brain_item_ids
    )
    assert manifest["publication_boundary"]["missing_item_count"] == 0
    assert manifest["publication_boundary"]["missing_revision_count"] == 0
    assert manifest["write_boundary"]["mode"] == "snapshot_high_water_capture"
    assert manifest["write_boundary"]["notion_transient_upload_session_count"] == 0
    assert manifest["objectives"] == {
        "max_rpo_minutes": RPO_MINUTES,
        "max_rto_minutes": RTO_MINUTES,
        "retention": RETENTION_POLICY,
    }
    assert manifest["authorities"]["second_brain"]["delegated_service"].endswith(
        ".BackupService"
    )
    assert manifest["claims"]["target_restore_lane_closed"] is False

    authority = CollaborativeSearchAuthority.open(
        environment.notion_catalog,
        environment.notion_root,
        InMemoryIdentityDirectory(("owner", "editor", "outsider")),
    )
    page = authority.read_document("editor", environment.probe.document_id)
    authority.add_comment(
        "editor",
        environment.probe.document_id,
        "comment-after-o1a-high-water",
        content="post snapshot mutation",
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-after-high-water-0001",
    )
    replay = verify_local_workspace_generation(
        environment.source_root,
        created.generation_id,
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
    )
    assert replay["manifest_sha256"] == created.manifest_sha256
    assert (
        replay["authorities"]["notion"]["observation"]["comment_count"]
        + 1
        == authority.list_comments("editor", environment.probe.document_id)["count"]
    )


@pytest.mark.integration
@pytest.mark.scenario("OPS-001")
def test_o1a_p1_transient_upload_prevents_generation_from_becoming_restorable(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    authority = CollaborativeSearchAuthority.open(
        environment.notion_catalog,
        environment.notion_root,
        InMemoryIdentityDirectory(("owner", "editor", "outsider")),
    )
    page = authority.read_document("owner", environment.probe.document_id)
    authority.begin_upload(
        "owner",
        environment.probe.document_id,
        original_name="pending.bin",
        mime_type="application/octet-stream",
        total_size=1,
        expected_sha256="sha256:" + "0" * 64,
        expected_document_sequence=int(page["document_sequence"]),
        expected_workspace_epoch=int(page["workspace_epoch"]),
        expected_document_epoch=int(page["document_epoch"]),
        operation_id="op-o1a-pending-upload-0001",
    )

    with pytest.raises(LocalGenerationIntegrityError, match="upload drain"):
        create_local_workspace_generation(
            environment.source_root,
            "o1a-pending-upload",
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
            sbe_configuration=environment.sbe_configuration,
            smoke_probe=environment.probe,
        )
    assert not (
        environment.source_root
        / "coordinated-backups/generations/o1a-pending-upload"
    ).exists()
