from __future__ import annotations

from collections import Counter
import json
import hashlib
import sqlite3
from pathlib import Path
from uuid import UUID
from typing import Any

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core import cli
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
)
from code_assist_v1.knowledge_core.legacy.content_importer import (
    LegacyPersonalContentImporter,
)
from code_assist_v1.knowledge_core.legacy.migration_verify import (
    LegacyMigrationVerificationIntegrityError,
    LegacyMigrationVerificationPlanError,
    LegacyMigrationVerifier,
    verification_contract_errors,
    verification_sha256,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import sha256_canonical
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _apply_identity,
    _factory,
    _reviewed_fixture,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_p25_importer import (
    _reviewed_p25_fixture,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_p24_importer import (
    _reviewed as _reviewed_p24,
    _use_planner_fixture_probe,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_p24_history_importer import (
    _history_fixture,
    _history_rows,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    planner_fixture,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_schema_v1_acceptance import (
    _apply_release,
    _insert_human_release_finding,
)


pytestmark = pytest.mark.unit


def _uuid(value: int) -> str:
    return str(UUID(int=value))


def _publish_first_migrated_item_without_search(
    factory: Any,
    *,
    body: str = "body",
    id_base: int = 9600,
    owner_user_id: str | None = None,
    space_id: str | None = None,
) -> str:
    connection = sqlite3.connect(factory.database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA recursive_triggers=ON")
        if owner_user_id is None and space_id is None:
            row = connection.execute(
                """SELECT s.id AS space_id,s.owner_user_id
                     FROM knowledge_space AS s
                    WHERE s.space_kind='personal' ORDER BY s.id LIMIT 1"""
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT s.id AS space_id,s.owner_user_id
                     FROM knowledge_space AS s
                    WHERE s.id=? AND s.owner_user_id=? AND s.space_kind='personal'""",
                (space_id, owner_user_id),
            ).fetchone()
        assert row is not None
        item_id = _uuid(id_base)
        revision_id = _uuid(id_base + 5)
        finding_id = _uuid(id_base + 1)
        transition_id = _uuid(id_base + 2)
        dedup_id = _uuid(id_base + 3)
        operation_id = _uuid(id_base + 4)
        body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        body_size = len(body.encode("utf-8"))
        connection.execute(
            """INSERT INTO knowledge_item(
              id,space_id,lineage_group_id,lifecycle_status,curation_status,
              current_revision_id,last_curation_transition_id,
              latest_deactivation_episode_id,redirect_item_id,created_by,updated_by,
              created_at,updated_at,row_version
            ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,1,1,1)""",
            (item_id, row["space_id"], revision_id, row["owner_user_id"], row["owner_user_id"]),
        )
        connection.execute(
            """INSERT INTO knowledge_revision(
              id,item_id,revision_no,parent_revision_id,base_revision_id,title,
              raw_content_sha256,normalized_content_sha256,content_size_bytes,
              normalizer_version,metadata_json,change_kind,change_reason,created_by,created_at
            ) VALUES (?,?,1,NULL,NULL,'Title',?,?,?,'markdown_lf_v1','{}',
              'initial','initial',?,1)""",
            (
                revision_id,
                item_id,
                body_sha256,
                body_sha256,
                body_size,
                row["owner_user_id"],
            ),
        )
        connection.execute(
            """INSERT INTO knowledge_revision_body(
              revision_id,item_id,content_md,content_blob_hash,content_size_bytes,
              content_sha256,created_at
            ) VALUES (?,?,?,NULL,?,?,1)""",
            (revision_id, item_id, body, body_size, body_sha256),
        )
        connection.commit()
        connection.execute(
            """INSERT INTO operation_dedup(
              id,actor_user_id,operation_type,operation_id,request_sha256,status,
              http_status,result_json,error_json,created_at,completed_at,last_seen_at
            ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,1,NULL,1)""",
            (dedup_id, row["owner_user_id"], operation_id, "b" * 64),
        )
        _insert_human_release_finding(
            connection,
            finding_id=finding_id,
            user_id=str(row["owner_user_id"]),
            space_id=str(row["space_id"]),
            item_id=item_id,
            revision_id=revision_id,
        )
        _apply_release(
            connection,
            user_id=str(row["owner_user_id"]),
            space_id=str(row["space_id"]),
            item_id=item_id,
            revision_id=revision_id,
            finding_id=finding_id,
            dedup_id=dedup_id,
            operation_id=operation_id,
            transition_id=transition_id,
        )
        connection.execute("DELETE FROM search_document WHERE item_id=?", (item_id,))
        connection.commit()
        return item_id
    finally:
        connection.close()


def test_strict_verifier_closes_every_plan_effect_rebuilds_and_replays(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path, include_notion=True)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    reviewed = json.loads(fixture.plan_bytes)
    draft = reviewed["draft"]
    verifier = LegacyMigrationVerifier(factory, layout)

    first = verifier.verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(first) == ()
    assert first["reconciliation"]["observed_record_count"] == len(
        draft["records"]
    )
    assert first["reconciliation"]["observed_relationship_count"] == len(
        draft["relationships"]
    )
    assert first["reconciliation"]["unresolved_records"] == 0
    assert first["reconciliation"]["unresolved_relationships"] == 0
    assert first["rebuild"]["after_sha256"] == first["rebuild"]["expected_sha256"]
    assert first["rebuild"]["fts_integrity"] == "pass"
    assert first["rebuild"]["projection_integrity"] == "pass"
    assert first["rebuild"]["projection_name"] == "markdown"
    assert first["rebuild"]["projection_space_count"] == len(
        first["rebuild"]["projection_spaces"]
    )
    assert first["rebuild"]["projection_spaces_sha256"] == sha256_canonical(
        first["rebuild"]["projection_spaces"]
    )
    for row in first["rebuild"]["projection_spaces"]:
        manifest = layout.projection_root / row["space_id"] / "manifest.json"
        assert hashlib.sha256(manifest.read_bytes()).hexdigest() == row["manifest_sha256"]
    assert any(
        row["observed_terminal_classification"] == "retained_external"
        for row in first["records"]
    )
    assert all(
        (row["observed_terminal_classification"] == "imported")
        == (row["mapping_id"] is not None)
        for row in first["records"]
    )

    second = verifier.verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert second == first


def test_verifier_rejects_incomplete_apply_and_contract_tamper(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    verifier = LegacyMigrationVerifier(factory, layout)
    with pytest.raises(LegacyMigrationVerificationIntegrityError):
        verifier.verify(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
        )

    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    report = verifier.verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    report["records"][0]["observed_terminal_classification"] = "quarantined"
    report["verification_sha256"] = verification_sha256(report)
    assert "verification_records" in verification_contract_errors(report)


def test_verifier_reopens_every_imported_cas_object_and_rejects_corruption(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    digest = hashlib.sha256(fixture.asset_bytes).hexdigest()
    cas_path = (
        isolated_knowledge_layout.blobs_dir
        / digest[:2]
        / digest[2:4]
        / digest
    )
    cas_path.write_bytes(b"x" * len(fixture.asset_bytes))

    with pytest.raises(LegacyMigrationVerificationIntegrityError):
        LegacyMigrationVerifier(factory, layout).verify(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
        )


def test_verifier_rejects_missing_imported_relationship_checkpoint(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    importer = LegacyPersonalContentImporter(
        factory, FilesystemLegacyBlobStager(layout)
    )
    importer.apply_batch(
        fixture.plan_bytes,
        {
            fixture.source_record_id: fixture.source_bytes,
            fixture.asset_record_id: fixture.asset_bytes,
        },
        selected_record_ids=tuple(
            sorted((fixture.source_record_id, fixture.asset_record_id))
        ),
        expected_plan_sha256=fixture.plan_sha256,
    )

    with pytest.raises(LegacyMigrationVerificationIntegrityError):
        LegacyMigrationVerifier(factory, layout).verify(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
        )


def test_verifier_repairs_only_the_rebuildable_search_projection(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    item_id = _publish_first_migrated_item_without_search(factory)

    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert report["rebuild"]["row_count"] == 1
    assert report["rebuild"]["after_sha256"] == report["rebuild"]["expected_sha256"]
    connection = factory.open()
    try:
        row = connection.execute(
            "SELECT item_id,content_md FROM search_document"
        ).fetchone()
        assert row is not None
        assert row["item_id"] == item_id
        assert row["content_md"] == "body"
        assert connection.execute(
            "SELECT count(*) AS n FROM knowledge_fts WHERE knowledge_fts MATCH 'body'"
        ).fetchone()["n"] == 1
    finally:
        connection.close()


def test_verifier_rebuilds_fts_canaries_without_cross_space_results(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    other_space_id = _uuid(9750)
    connection = sqlite3.connect(factory.database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute(
            """INSERT INTO app_user(
                 id,display_name,password_hash,system_role,account_status,
                 current_part_id,scope_epoch,created_at,updated_at,disabled_at,row_version
               ) VALUES ('other','Other','hash','user','active',NULL,1,1,1,NULL,1)"""
        )
        connection.execute(
            """INSERT INTO knowledge_space(
                 id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
                 generation,created_at,updated_at,row_version
               ) VALUES (?,'personal','other',NULL,'active',0,0,1,1,1)""",
            (other_space_id,),
        )
        connection.execute(
            """INSERT INTO personal_wiki_profile(
                 space_id,owner_user_id,display_name,name_key,lifecycle_status,
                 lifecycle_generation,is_default,archived_at,
                 archived_by_user_id,archive_reason,created_at,updated_at,row_version
               ) VALUES (?,'other','Other Wiki','other wiki','active',1,1,
                         NULL,NULL,NULL,1,1,1)""",
            (other_space_id,),
        )
        admin_space_id = connection.execute(
            "SELECT id FROM knowledge_space WHERE owner_user_id='admin'"
        ).fetchone()[0]
        connection.commit()
    finally:
        connection.close()

    admin_item = _publish_first_migrated_item_without_search(
        factory,
        body="alpha_scope_canary",
        id_base=9760,
        owner_user_id="admin",
        space_id=admin_space_id,
    )
    other_item = _publish_first_migrated_item_without_search(
        factory,
        body="beta_scope_canary",
        id_base=9780,
        owner_user_id="other",
        space_id=other_space_id,
    )
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert report["rebuild"]["row_count"] == 2

    connection = factory.open()
    try:
        alpha = connection.execute(
            """SELECT d.item_id,d.space_id
                 FROM knowledge_fts AS f
                 JOIN search_document AS d ON d.rowid=f.rowid
                WHERE knowledge_fts MATCH 'alpha_scope_canary'"""
        ).fetchall()
        beta = connection.execute(
            """SELECT d.item_id,d.space_id
                 FROM knowledge_fts AS f
                 JOIN search_document AS d ON d.rowid=f.rowid
                WHERE knowledge_fts MATCH 'beta_scope_canary'"""
        ).fetchall()
        assert [tuple(row) for row in alpha] == [(admin_item, admin_space_id)]
        assert [tuple(row) for row in beta] == [(other_item, other_space_id)]
        assert connection.execute(
            """SELECT count(*) AS n
                 FROM knowledge_fts AS f
                 JOIN search_document AS d ON d.rowid=f.rowid
                WHERE knowledge_fts MATCH 'alpha_scope_canary' AND d.space_id=?""",
            (other_space_id,),
        ).fetchone()["n"] == 0
        assert connection.execute(
            """SELECT count(*) AS n
                 FROM knowledge_fts AS f
                 JOIN search_document AS d ON d.rowid=f.rowid
                WHERE knowledge_fts MATCH 'beta_scope_canary' AND d.space_id=?""",
            (admin_space_id,),
        ).fetchone()["n"] == 0
    finally:
        connection.close()


def test_verification_contract_rejects_resealed_action_mapping_and_count_tamper(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )

    report["records"][0]["action"] = "quarantine"
    report["verification_sha256"] = verification_sha256(report)
    assert "verification_records" in verification_contract_errors(report)
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    report["rebuild"]["legacy_rebuild_record_count"] += 1
    report["verification_sha256"] = verification_sha256(report)
    assert "verification_rebuild" in verification_contract_errors(report)
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    report["rebuild"]["projection_entry_count"] += 1
    report["verification_sha256"] = verification_sha256(report)
    assert "verification_rebuild" in verification_contract_errors(report)

    malformed = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    malformed["records"][0]["action"] = []
    assert "verification_records" in verification_contract_errors(
        malformed, verify_hash=False
    )
    malformed = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    malformed["relationships"] = [
        {
            "action": "import",
            "mapping_id": _uuid(9898),
            "observed_terminal_classification": {},
            "planned_terminal_classification": "imported",
            "relationship_id": "rel_" + "a" * 24,
            "target_identity_sha256": "b" * 64,
        }
    ]
    assert "verification_relationships" in verification_contract_errors(
        malformed, verify_hash=False
    )
    malformed = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    malformed["rebuild"]["projection_spaces"][0]["space_id"] = []
    assert "verification_rebuild" in verification_contract_errors(
        malformed, verify_hash=False
    )

    omitted = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    omitted["records"].pop()
    omitted["reconciliation"]["observed_record_count"] = len(omitted["records"])
    omitted["reconciliation"]["observed_records_sha256"] = sha256_canonical(
        omitted["records"]
    )
    omitted["reconciliation"]["terminal_classification_counts"] = dict(
        sorted(
            Counter(
                row["observed_terminal_classification"]
                for row in omitted["records"]
            ).items()
        )
    )
    omitted["reconciliation"]["canonical_targets_sha256"] = sha256_canonical(
        [
            [row["record_id"], row["mapping_id"], row["target_identity_sha256"]]
            for row in omitted["records"]
        ]
        + [
            [
                row["relationship_id"],
                row["mapping_id"],
                row["target_identity_sha256"],
            ]
            for row in omitted["relationships"]
        ]
    )
    omitted["verification_sha256"] = verification_sha256(omitted)
    assert "verification_plan_binding" in verification_contract_errors(omitted)


def test_verifier_closes_p24_graph_finding_and_relationship_effects(
    planner_fixture: Any,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_planner_fixture_probe(monkeypatch)
    reviewed, plan_sha256, draft = _reviewed_p24(planner_fixture)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        reviewed,
        expected_plan_sha256=plan_sha256,
        batch_size=4,
    )

    report = LegacyMigrationVerifier(factory, layout).verify(
        reviewed,
        expected_plan_sha256=plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    assert report["reconciliation"]["observed_record_count"] == len(
        draft["records"]
    )
    assert report["reconciliation"]["observed_relationship_count"] == len(
        draft["relationships"]
    )
    p24_imported = {
        str(row["relationship_id"])
        for row in draft["relationships"]
        if row["action"] == "import" and row["execution_phase"] == "P2.4"
    }
    observed = {
        str(row["relationship_id"])
        for row in report["relationships"]
        if row["mapping_id"] is not None
    }
    assert p24_imported.issubset(observed)


def test_verifier_replays_every_p24_labelled_history_effect(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _history_fixture(tmp_path)
    logical, physical = _history_rows(fixture)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )

    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert verification_contract_errors(report) == ()
    expected = {
        str(row["record_id"])
        for row in (*logical, *physical)
    }
    observed = {
        str(row["record_id"])
        for row in report["records"]
        if row["mapping_id"] is not None
    }
    assert expected.issubset(observed)


def test_cli_verify_writes_one_canonical_replay_safe_report(
    tmp_path, isolated_knowledge_layout: Any, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    plan_path = tmp_path / "approved" / "reviewed-plan.json"
    plan_path.parent.mkdir()
    plan_path.write_bytes(fixture.plan_bytes)
    report_path = tmp_path / "reports" / "verification.json"
    report_path.parent.mkdir()
    report = LegacyMigrationVerifier(factory, layout).verify(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
    )
    source_roots = cli._source_roots(json.loads(fixture.plan_bytes))

    def fake_verify(_arguments: Any):
        return report, (
            *source_roots,
            str(layout.data_root),
            str(Path(layout.database).parent),
            str(layout.blob_root),
            str(layout.projection_root),
        )

    monkeypatch.setattr(cli, "_verify", fake_verify)
    arguments = [
        "migrate",
        "verify",
        "--plan",
        str(plan_path),
        "--plan-sha256",
        fixture.plan_sha256,
        "--data-root",
        str(layout.data_root),
        "--database",
        str(layout.database),
        "--blob-root",
        str(layout.blob_root),
        "--projection-root",
        str(layout.projection_root),
        "--strict",
        "--report",
        str(report_path),
    ]

    assert cli.main(arguments) == 0
    assert capsys.readouterr().out == ""
    report_bytes = report_path.read_bytes()
    persisted_report = json.loads(report_bytes)
    assert report_bytes.endswith(b"\n")
    assert verification_contract_errors(persisted_report) == ()
    assert cli.main(arguments) == 0
    assert capsys.readouterr().out == ""
    assert report_path.read_bytes() == report_bytes

    inside = list(arguments)
    inside[-1] = str(Path(layout.data_root) / "unsafe-verification.json")
    assert cli.main(inside) == 3
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "OUTPUT_INSIDE_SOURCE"
    assert not Path(inside[-1]).exists()


@pytest.mark.parametrize(
    ("failure", "expected_exit", "expected_code"),
    (
        (
            LegacyMigrationVerificationPlanError("invalid reviewed plan"),
            2,
            "PLAN_INVALID",
        ),
        (
            LegacyMigrationVerificationIntegrityError("canonical drift"),
            3,
            "VERIFY_STATE_CONFLICT",
        ),
    ),
)
def test_cli_verify_maps_plan_and_state_failures_without_internal_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    failure: Exception,
    expected_exit: int,
    expected_code: str,
) -> None:
    plan = tmp_path / "reviewed-plan.json"
    plan.write_bytes(b"{}\n")
    report = tmp_path / "verification.json"
    root = tmp_path / "canonical"
    root.mkdir()
    database = root / "knowledge-core.db"
    database.touch()
    blobs = root / "blobs"
    projections = root / "projections"
    blobs.mkdir()
    projections.mkdir()

    def fail(_arguments: Any):
        raise failure

    monkeypatch.setattr(cli, "_verify", fail)
    result = cli.main(
        [
            "migrate",
            "verify",
            "--plan",
            str(plan),
            "--plan-sha256",
            "a" * 64,
            "--data-root",
            str(root),
            "--database",
            str(database),
            "--blob-root",
            str(blobs),
            "--projection-root",
            str(projections),
            "--strict",
            "--report",
            str(report),
        ]
    )
    assert result == expected_exit
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == expected_code
    assert str(failure) not in json.dumps(payload)
    assert not report.exists()


def test_verifier_rejects_conversation_evidence_drift_beyond_fk_checks(
    tmp_path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE conversation
                      SET metadata_json='{}',updated_at=updated_at+1,
                          row_version=row_version+1"""
            )
    finally:
        connection.close()

    with pytest.raises(LegacyMigrationVerificationIntegrityError):
        LegacyMigrationVerifier(factory, layout).verify(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
        )


def test_verifier_rejects_p24_edge_effect_drift_beyond_fk_checks(
    planner_fixture: Any,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_planner_fixture_probe(monkeypatch)
    reviewed, plan_sha256, _draft = _reviewed_p24(planner_fixture)
    factory = _factory(isolated_knowledge_layout)
    layout = _storage_layout(isolated_knowledge_layout)
    LegacyMigrationApplyService(factory, layout).apply(
        reviewed,
        expected_plan_sha256=plan_sha256,
        batch_size=4,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_edge
                      SET edge_status='inactive',updated_at=updated_at+1,
                          row_version=row_version+1"""
            )
    finally:
        connection.close()

    with pytest.raises(LegacyMigrationVerificationIntegrityError):
        LegacyMigrationVerifier(factory, layout).verify(
            reviewed,
            expected_plan_sha256=plan_sha256,
        )
