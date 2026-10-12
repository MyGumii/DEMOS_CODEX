from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from code_assist_v1.knowledge_core import cli as cli_module
from code_assist_v1.knowledge_core.cli import main
from code_assist_v1.knowledge_core import db as db_module
from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.migration_verify import (
    verification_contract_errors,
)
from code_assist_v1.knowledge_core.legacy import (
    migration_verify as migration_verify_module,
)
from code_assist_v1.knowledge_core.legacy.rehearsal import (
    LegacyMigrationRehearsal,
    LegacyMigrationRehearsalTarget,
    LegacyMigrationRehearsalTargetError,
    rehearsal_contract_errors,
    rehearsal_sha256,
    verify_empty_rehearsal_target,
)
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.tools import legacy_inventory as legacy_inventory_module
from code_assist_v1.test_safety import create_isolated_test_layout
from code_assist_v1.tests.knowledge_spaces.migration.test_legacy_migration_fault_gate import (
    _reviewed_gate_fixture,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _storage_layout,
)


pytestmark = pytest.mark.migration


def _target(root: Path) -> tuple[Any, Any, LegacyMigrationRehearsalTarget]:
    isolated = create_isolated_test_layout(root, user_id="fixture-user")
    factory = _factory(isolated)
    layout = _storage_layout(isolated)
    return isolated, factory, LegacyMigrationRehearsalTarget(factory, layout)


def _mapping_count(factory: Any) -> int:
    connection = factory.open()
    try:
        return int(
            connection.execute(
                "SELECT count(*) AS n FROM legacy_migration_map"
            ).fetchone()["n"]
        )
    finally:
        connection.close()


def _rehearse_arguments(
    *,
    plan: Path,
    plan_sha256: str,
    primary: LegacyMigrationRehearsalTarget,
    fresh: LegacyMigrationRehearsalTarget,
    report: Path,
    batch_size: int = 3,
) -> list[str]:
    return [
        "migrate",
        "rehearse",
        "--plan",
        str(plan),
        "--plan-sha256",
        plan_sha256,
        "--primary-data-root",
        str(primary.layout.data_root),
        "--primary-database",
        str(primary.layout.database),
        "--primary-blob-root",
        str(primary.layout.blob_root),
        "--primary-projection-root",
        str(primary.layout.projection_root),
        "--fresh-data-root",
        str(fresh.layout.data_root),
        "--fresh-database",
        str(fresh.layout.database),
        "--fresh-blob-root",
        str(fresh.layout.blob_root),
        "--fresh-projection-root",
        str(fresh.layout.projection_root),
        "--batch-size",
        str(batch_size),
        "--report",
        str(report),
    ]


def _bootstrap_arguments(layout: Any) -> list[str]:
    return [
        "migrate",
        "bootstrap-rehearsal",
        "--data-root",
        str(layout.root),
        "--database",
        str(layout.core_db_path),
        "--blob-root",
        str(layout.blobs_dir),
        "--projection-root",
        str(layout.projections_dir),
    ]


def test_rehearsal_replays_and_reproduces_one_reviewed_plan(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="rehearsal-realistic",
        realistic=True,
    )
    _primary_layout, _primary_factory, primary = _target(
        tmp_path / "primary-target"
    )
    _fresh_layout, _fresh_factory, fresh = _target(tmp_path / "fresh-target")

    report = LegacyMigrationRehearsal(primary, fresh).run(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=7,
    )

    assert rehearsal_contract_errors(report) == ()
    assert rehearsal_contract_errors(
        report,
        reviewed_plan=fixture.plan_bytes,
    ) == ()
    assert verification_contract_errors(report["verification"]) == ()
    assert report["schema_version"] == 2
    assert report["rehearsal_sha256"] == rehearsal_sha256(report)
    assert report["execution"] == {"batch_size": 7}
    runs = report["runs"]
    assert runs["primary_first"]["apply"]["inserted_mapping_count"] > 0
    assert runs["primary_rerun"]["apply"] == {
        **runs["primary_first"]["apply"],
        "inserted_mapping_count": 0,
    }
    assert runs["fresh_first"]["apply"] == runs["primary_first"]["apply"]
    assert {
        row["verification_sha256"] for row in runs.values()
    } == {report["verification"]["verification_sha256"]}

    tampered = deepcopy(report)
    tampered["runs"]["fresh_first"]["apply"]["imported_record_count"] += 1
    tampered["rehearsal_sha256"] = rehearsal_sha256(tampered)
    assert "rehearsal_reproducibility_binding" in rehearsal_contract_errors(
        tampered
    )

    tampered_plan_binding = deepcopy(report)
    tampered_plan_binding["plan"]["snapshot_id"] = "forged-snapshot"
    tampered_plan_binding["rehearsal_sha256"] = rehearsal_sha256(
        tampered_plan_binding
    )
    assert "rehearsal_plan_document_binding" in rehearsal_contract_errors(
        tampered_plan_binding,
        reviewed_plan=fixture.plan_bytes,
    )

    tampered_target = deepcopy(report)
    tampered_target["targets"]["fresh"]["catalog_checksum_sha256"] = "0" * 64
    tampered_target["rehearsal_sha256"] = rehearsal_sha256(tampered_target)
    assert "rehearsal_targets" in rehearsal_contract_errors(tampered_target)
    tampered_catalog = deepcopy(report)
    for target in tampered_catalog["targets"].values():
        target["catalog_checksum_sha256"] = "0" * 64
    tampered_catalog["rehearsal_sha256"] = rehearsal_sha256(tampered_catalog)
    assert "rehearsal_targets" in rehearsal_contract_errors(tampered_catalog)
    assert rehearsal_contract_errors({"rehearsal_sha256": []}) == (
        "rehearsal_shape",
    )
    invalid_execution = deepcopy(report)
    invalid_execution["execution"]["batch_size"] = 101
    invalid_execution["rehearsal_sha256"] = rehearsal_sha256(invalid_execution)
    assert "rehearsal_execution" in rehearsal_contract_errors(invalid_execution)


def test_rehearsal_rejects_a_nonempty_target_before_touching_fresh(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="rehearsal-dirty",
        realistic=False,
    )
    _primary_layout, primary_factory, primary = _target(
        tmp_path / "primary-target"
    )
    _fresh_layout, fresh_factory, fresh = _target(tmp_path / "fresh-target")
    LegacyMigrationApplyService(primary.factory, primary.layout).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert _mapping_count(primary_factory) > 0

    with pytest.raises(LegacyMigrationRehearsalTargetError):
        LegacyMigrationRehearsal(primary, fresh).run(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
            batch_size=1,
        )

    assert _mapping_count(fresh_factory) == 0


def test_rehearsal_requires_two_disjoint_targets(tmp_path: Path) -> None:
    _layout, _factory_value, target = _target(tmp_path / "one-target")

    with pytest.raises(LegacyMigrationRehearsalTargetError):
        LegacyMigrationRehearsal(target, target)


def test_bootstrap_rehearsal_cli_creates_only_a_fresh_empty_target(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = create_isolated_test_layout(
        tmp_path / "fresh-bootstrap", user_id="fixture-user"
    )
    assert not isolated.core_db_path.exists()
    catalog = CANONICAL_MIGRATION_CATALOG
    monkeypatch.setattr(
        cli_module,
        "_canonical_factory",
        lambda database: db_module.ConnectionFactory._for_isolated_tests(
            database, catalog
        ),
    )

    assert main(_bootstrap_arguments(isolated)) == 0
    first = json.loads(capfd.readouterr().out)
    assert first["bootstrap"]["status"] == "complete"
    assert first["bootstrap"]["schema_version"] == catalog.latest_version
    assert first["bootstrap"]["applied_versions"] == [
        migration.version for migration in catalog.migrations
    ]
    factory = db_module.ConnectionFactory._for_isolated_tests(
        isolated.core_db_path, catalog
    )
    verify_empty_rehearsal_target(
        LegacyMigrationRehearsalTarget(factory, _storage_layout(isolated))
    )
    assert main(_bootstrap_arguments(isolated)) == 3
    second = json.loads(capfd.readouterr().out)
    assert second["error"]["code"] == "SCHEMA_BOOTSTRAP_TARGET_EXISTS"
    verify_empty_rehearsal_target(
        LegacyMigrationRehearsalTarget(factory, _storage_layout(isolated))
    )


def test_bootstrap_rehearsal_cli_preserves_a_late_database_winner(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = create_isolated_test_layout(
        tmp_path / "late-winner", user_id="fixture-user"
    )
    monkeypatch.setattr(
        cli_module,
        "_canonical_factory",
        lambda database: db_module.ConnectionFactory._for_isolated_tests(
            database, CANONICAL_MIGRATION_CATALOG
        ),
    )
    original = legacy_inventory_module._WindowsDirectoryAnchor.create_relative_file
    winner = b"late-winner-must-not-be-opened-or-overwritten"

    def create_late_winner(anchor: Any, name: str, **kwargs: Any) -> int:
        (anchor.path / name).write_bytes(winner)
        return original(anchor, name, **kwargs)

    monkeypatch.setattr(
        legacy_inventory_module._WindowsDirectoryAnchor,
        "create_relative_file",
        create_late_winner,
    )

    assert main(_bootstrap_arguments(isolated)) == 3
    output = json.loads(capfd.readouterr().out)
    assert output["error"]["code"] == "SCHEMA_BOOTSTRAP_TARGET_EXISTS"
    assert isolated.core_db_path.read_bytes() == winner


def test_rehearsal_cli_preflights_output_then_writes_closed_evidence(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _reviewed_gate_fixture(
        tmp_path,
        name="rehearsal-cli",
        realistic=False,
    )
    _primary_layout, primary_factory, primary = _target(
        tmp_path / "primary-target"
    )
    _fresh_layout, fresh_factory, fresh = _target(tmp_path / "fresh-target")
    input_dir = tmp_path / "approved-input"
    input_dir.mkdir()
    plan_path = input_dir / "reviewed-plan.json"
    plan_path.write_bytes(fixture.plan_bytes)
    monkeypatch.setattr(
        cli_module,
        "_canonical_factory",
        lambda database: db_module.ConnectionFactory._for_isolated_tests(
            database, primary_factory.migration_catalog
        ),
    )

    unsafe_report = primary.layout.data_root / "must-not-write.json"
    assert main(
        _rehearse_arguments(
            plan=plan_path,
            plan_sha256=fixture.plan_sha256,
            primary=primary,
            fresh=fresh,
            report=unsafe_report,
        )
    ) == 3
    output = capfd.readouterr().out.strip().splitlines()
    assert json.loads(output[-1])["error"]["code"] == "OUTPUT_INSIDE_SOURCE"
    assert not unsafe_report.exists()
    assert _mapping_count(primary_factory) == 0
    assert _mapping_count(fresh_factory) == 0

    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    report_path = evidence_dir / "rehearsal.json"
    assert main(
        _rehearse_arguments(
            plan=plan_path,
            plan_sha256=fixture.plan_sha256,
            primary=primary,
            fresh=fresh,
            report=report_path,
        )
    ) == 0
    assert capfd.readouterr().out == ""
    report = json.loads(report_path.read_bytes())
    assert rehearsal_contract_errors(report) == ()
    assert report["runs"]["primary_rerun"]["apply"][
        "inserted_mapping_count"
    ] == 0
    report_bytes = report_path.read_bytes()
    primary_mappings = _mapping_count(primary_factory)
    fresh_mappings = _mapping_count(fresh_factory)

    # A lost CLI response is safely retryable even though both targets are now
    # populated: the exact plan/report/target identities converge without a
    # second apply.
    assert main(
        _rehearse_arguments(
            plan=plan_path,
            plan_sha256=fixture.plan_sha256,
            primary=primary,
            fresh=fresh,
            report=report_path,
        )
    ) == 0
    assert capfd.readouterr().out == ""
    assert report_path.read_bytes() == report_bytes
    assert _mapping_count(primary_factory) == primary_mappings
    assert _mapping_count(fresh_factory) == fresh_mappings

    # The archived evidence binds the exact batch size used to produce it.
    # A retry with a different execution parameter is not the same rehearsal.
    assert main(
        _rehearse_arguments(
            plan=plan_path,
            plan_sha256=fixture.plan_sha256,
            primary=primary,
            fresh=fresh,
            report=report_path,
            batch_size=4,
        )
    ) == 3
    batch_mismatch = json.loads(capfd.readouterr().out)
    assert batch_mismatch["error"]["code"] == "REHEARSAL_MISMATCH"
    assert report_path.read_bytes() == report_bytes
    assert _mapping_count(primary_factory) == primary_mappings
    assert _mapping_count(fresh_factory) == fresh_mappings

    # Existing evidence is not trusted on its own: an exact retry must re-run
    # strict verification on both bound targets and match the archived result.
    with monkeypatch.context() as retry_patch:
        retry_patch.setattr(
            migration_verify_module.LegacyMigrationVerifier,
            "verify",
            lambda self, reviewed_plan, *, expected_plan_sha256: {
                "verification_sha256": "0" * 64
            },
        )
        assert main(
            _rehearse_arguments(
                plan=plan_path,
                plan_sha256=fixture.plan_sha256,
                primary=primary,
                fresh=fresh,
                report=report_path,
            )
        ) == 3
    retry_mismatch = json.loads(capfd.readouterr().out)
    assert retry_mismatch["error"]["code"] == "REHEARSAL_MISMATCH"
    assert report_path.read_bytes() == report_bytes
    assert _mapping_count(primary_factory) == primary_mappings
    assert _mapping_count(fresh_factory) == fresh_mappings

    _other_layout, other_factory, other = _target(tmp_path / "other-target")
    assert main(
        _rehearse_arguments(
            plan=plan_path,
            plan_sha256=fixture.plan_sha256,
            primary=primary,
            fresh=other,
            report=report_path,
        )
    ) == 3
    mismatch = json.loads(capfd.readouterr().out)
    assert mismatch["error"]["code"] == "REHEARSAL_MISMATCH"
    assert _mapping_count(other_factory) == 0
    assert report_path.read_bytes() == report_bytes
