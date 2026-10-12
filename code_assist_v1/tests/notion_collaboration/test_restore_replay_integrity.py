from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1.notion_collaboration.coordinated_backup_prototype import (
    GenerationIntegrityError,
    IncompleteGenerationError,
    create_coordinated_generation,
    initialize_synthetic_collaborative_realm,
    restore_coordinated_generation,
    verify_coordinated_generation,
    verify_restored_root,
)
from code_assist_v1.notion_collaboration.coordinated_workspace_backup import (
    BACKUP_DIRECTORY,
    FAULT_POINTS as O1A_FAULT_POINTS,
    GENERATION_MANIFEST as O1A_GENERATION_MANIFEST,
    INCOMPLETE_GENERATION as O1A_INCOMPLETE_GENERATION,
    InjectedLocalBackupFailure,
    IncompleteLocalGeneration,
    LocalGenerationIntegrityError,
    RESTORE_RECEIPT as O1A_RESTORE_RECEIPT,
    create_local_workspace_generation,
    restore_local_workspace_generation,
    verify_local_workspace_generation,
    verify_local_workspace_restored_root,
)
from code_assist_v1.tests.notion_collaboration.o1a_p1_backup_fixture import (
    build_o1a_backup_environment,
)


@pytest.mark.integration
@pytest.mark.restore
@pytest.mark.scenario("OPS-003")
def test_o0a_p2_restore_replays_exact_event_boundary_and_projection_state(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    first = realm.save_page(
        "revision two",
        expected_revision=1,
        operation_id="op-o0a-p2-save-0001",
    )
    second = realm.save_page(
        "revision three",
        expected_revision=2,
        operation_id="op-o0a-p2-save-0002",
    )
    assert first["revision"] == 2
    assert second["revision"] == 3
    assert realm.update_sbe("second brain revision two") == 2

    generation = create_coordinated_generation(realm.root, "generation-replay")
    assert generation.notion_document_sequence == 3
    assert generation.sbe_publication_revision == 2
    restored = restore_coordinated_generation(
        realm.root, "generation-replay", tmp_path / "restored"
    )

    notion = sqlite3.connect(restored.target_root / "notion" / "a0a-p2-authority.sqlite3")
    try:
        notion.row_factory = sqlite3.Row
        page = notion.execute(
            "SELECT revision,page_sequence,content_json FROM pages WHERE id='page-a'"
        ).fetchone()
        assert page is not None
        assert int(page["revision"]) == int(page["page_sequence"]) == 3
        assert json.loads(str(page["content_json"]))["blocks"][0]["text"] == "revision three"
        sequences = [
            int(row[0])
            for row in notion.execute(
                "SELECT page_sequence FROM durable_events WHERE page_id='page-a' ORDER BY page_sequence"
            ).fetchall()
        ]
        assert sequences == [1, 2, 3]
    finally:
        notion.close()
    projection = json.loads(
        (restored.target_root / "projections" / "notion-pages.json").read_text(encoding="utf-8")
    )
    assert projection["pages"][0]["revision"] == 3
    snapshot = json.loads(
        (
            generation.generation_root
            / "components"
            / "collaboration-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    assert snapshot["pages"] == projection["pages"]
    assert snapshot["event_high_water"] == generation.notion_event_sequence
    assert verify_restored_root(restored.target_root)["integrity_mismatch_count"] == 0


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-006")
def test_o0a_p2_component_tamper_blocks_generation_and_creates_no_restore_root(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    generation = create_coordinated_generation(realm.root, "generation-tamper")
    event_export = generation.generation_root / "components" / "collaboration-events.json"
    value = json.loads(event_export.read_text(encoding="utf-8"))
    value["events"].pop()
    event_export.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    with pytest.raises(GenerationIntegrityError):
        verify_coordinated_generation(realm.root, "generation-tamper")
    target = tmp_path / "must-not-exist"
    with pytest.raises(GenerationIntegrityError):
        restore_coordinated_generation(realm.root, "generation-tamper", target)
    assert not target.exists()


@pytest.mark.integration
@pytest.mark.restore
def test_o0a_p2_projection_tamper_is_detected_after_successful_restore(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm")
    create_coordinated_generation(realm.root, "generation-projection")
    restored = restore_coordinated_generation(
        realm.root, "generation-projection", tmp_path / "restored"
    )
    projection = restored.target_root / "projections" / "notion-pages.json"
    projection.write_text('{"pages":[]}', encoding="utf-8")
    with pytest.raises(GenerationIntegrityError, match="projection"):
        verify_restored_root(restored.target_root)


@pytest.mark.unit
def test_o0a_p2_manifest_duplicate_key_and_undeclared_file_fail_closed(
    tmp_path: Path,
) -> None:
    realm = initialize_synthetic_collaborative_realm(tmp_path / "realm-a")
    generation = create_coordinated_generation(realm.root, "generation-duplicate")
    manifest = generation.generation_root / "generation-manifest.json"
    manifest.write_text('{"format_id":"x","format_id":"y"}', encoding="utf-8")
    with pytest.raises(IncompleteGenerationError):
        verify_coordinated_generation(realm.root, "generation-duplicate")

    other = initialize_synthetic_collaborative_realm(tmp_path / "realm-b")
    second = create_coordinated_generation(other.root, "generation-extra")
    (second.generation_root / "undeclared.txt").write_text("not declared", encoding="utf-8")
    with pytest.raises(GenerationIntegrityError, match="undeclared"):
        verify_coordinated_generation(other.root, "generation-extra")


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-006")
@pytest.mark.parametrize(
    "fault", sorted(point for point in O1A_FAULT_POINTS if point is not None)
)
def test_o1a_p1_every_generation_failpoint_leaves_only_nonrestorable_staging(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
    fault: str,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    ordered = sorted(point for point in O1A_FAULT_POINTS if point is not None)
    generation_id = f"o1a-failed-{ordered.index(fault)}"

    with pytest.raises(InjectedLocalBackupFailure):
        create_local_workspace_generation(
            environment.source_root,
            generation_id,
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
            sbe_configuration=environment.sbe_configuration,
            smoke_probe=environment.probe,
            fault=fault,
        )

    backup_root = environment.source_root / BACKUP_DIRECTORY
    final = backup_root / "generations" / generation_id
    staging = backup_root / "staging" / generation_id
    receipt = json.loads(
        (staging / O1A_INCOMPLETE_GENERATION).read_text(encoding="utf-8")
    )
    assert not final.exists()
    assert receipt["state"] == "incomplete"
    assert receipt["restorable"] is False
    assert receipt["error_code"] == "INJECTED_LOCAL_BACKUP_FAILURE"
    with pytest.raises(IncompleteLocalGeneration):
        verify_local_workspace_generation(
            environment.source_root,
            generation_id,
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
        )


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-006")
def test_o1a_p1_payload_tamper_blocks_restore_before_target_creation(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    generation = create_local_workspace_generation(
        environment.source_root,
        "o1a-tamper",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe,
    )
    notion_snapshot = generation.generation_root / "authorities/notion/catalog.sqlite3"
    with notion_snapshot.open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(LocalGenerationIntegrityError):
        verify_local_workspace_generation(
            environment.source_root,
            generation.generation_id,
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
        )
    target_parent = isolated_knowledge_layout.root.parent / "o1a-tamper-targets"
    with pytest.raises(LocalGenerationIntegrityError):
        restore_local_workspace_generation(
            environment.source_root,
            generation.generation_id,
            "must-not-exist",
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
            target_parent=target_parent,
        )
    assert not (target_parent / "must-not-exist").exists()


@pytest.mark.integration
@pytest.mark.fault
@pytest.mark.scenario("OPS-006")
def test_o1a_p1_manifest_and_restore_receipt_tamper_fail_closed(
    tmp_path: Path,
    isolated_knowledge_layout,
    request,
) -> None:
    environment = build_o1a_backup_environment(
        tmp_path, isolated_knowledge_layout, request=request
    )
    generation = create_local_workspace_generation(
        environment.source_root,
        "o1a-receipt-tamper",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        sbe_configuration=environment.sbe_configuration,
        smoke_probe=environment.probe,
    )
    manifest_path = generation.generation_root / O1A_GENERATION_MANIFEST
    original_manifest = manifest_path.read_bytes()
    manifest_path.write_text(
        '{"contract":"first","contract":"second"}', encoding="utf-8"
    )
    with pytest.raises(IncompleteLocalGeneration):
        verify_local_workspace_generation(
            environment.source_root,
            generation.generation_id,
            sbe_factory=environment.sbe_factory,
            sbe_layout=environment.sbe_layout,
        )
    manifest_path.write_bytes(original_manifest)

    restored = restore_local_workspace_generation(
        environment.source_root,
        generation.generation_id,
        "receipt-tamper",
        sbe_factory=environment.sbe_factory,
        sbe_layout=environment.sbe_layout,
        target_parent=isolated_knowledge_layout.root.parent / "o1a-receipt-targets",
    )
    receipt_path = restored.target_root / O1A_RESTORE_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["smoke"]["passed"] = False
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    with pytest.raises(LocalGenerationIntegrityError):
        verify_local_workspace_restored_root(restored.target_root)
