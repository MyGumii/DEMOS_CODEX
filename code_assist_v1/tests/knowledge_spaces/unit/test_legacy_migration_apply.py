from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyLayoutError,
    LegacyMigrationApplyPlanError,
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.p25_importer import (
    LegacyP25PreparationError,
    LegacyP25StateConflictError,
)
from code_assist_v1.knowledge_core.legacy.p25_notion_importer import (
    LegacyP25NotionPreparationError,
)
from code_assist_v1.knowledge_core.legacy.source_reader import (
    LegacySourceBindingError,
)
from code_assist_v1.knowledge_core import cli
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _counts,
    _factory,
    _reviewed_fixture,
    _storage_layout,
)


pytestmark = pytest.mark.unit


def _user_count(factory) -> int:
    connection = factory.open()
    try:
        return int(
            connection.execute("SELECT COUNT(*) AS n FROM app_user").fetchone()["n"]
        )
    finally:
        connection.close()


def test_apply_service_closes_all_p23_effects_and_replays_exactly(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )

    first = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert first.identity.imported_user_count == 1
    assert first.imported_record_count == 2
    assert first.imported_relationship_count == 1
    assert first.inserted_mapping_count == 5
    assert len(first.content_batches) == 3
    after_first = _counts(factory)
    assert _user_count(factory) == 1
    assert after_first["legacy_migration_map"] == 4
    assert after_first["legacy_migration_relationship_map"] == 1

    second = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert second.inserted_mapping_count == 0
    assert second.imported_record_count == 2
    assert second.imported_relationship_count == 1
    assert _counts(factory) == after_first


def test_apply_service_runs_every_frozen_legacy_personal_content_adapter(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(
        tmp_path,
        extra_personal_files={
            # The CAS identity is bytes, not a guessed MIME type.  These two
            # differently classified sources intentionally share one digest.
            "flat.md": b"shared legacy content\r\n",
            "inbox/candidate.json": b'{"id":"candidate","status":"pending"}',
            "raw/source.rst": b"Legacy raw\r\n==========\r\n",
            "raw_chunks/chunk.txt": b"shared legacy content\r\n",
            "thoughts/thought.md": b"# thought\r\n",
            "wiki/pages/hidden.md": b"# hidden\r\n",
        },
    )
    plan_rows = json.loads(fixture.plan_bytes)["draft"]["records"]
    assert len(fixture.extra_record_ids) == 6, [
        (
            row["source_record"]["relative_path"],
            row["category"],
            row["action"],
            row["execution_phase"],
            row["import_mode"],
            row["reason_codes"],
        )
        for row in plan_rows
        if row["source_record"]["relative_path"].startswith("admin/")
    ]
    factory = _factory(isolated_knowledge_layout)
    result = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    ).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=3,
    )
    assert result.imported_record_count == 8
    assert result.imported_relationship_count == 1
    assert result.inserted_mapping_count == 11
    counts = _counts(factory)
    assert counts["source_artifact"] == 8
    assert counts["knowledge_item"] == 6
    connection = factory.open()
    try:
        assert int(
            connection.execute("SELECT COUNT(*) AS n FROM blob_object").fetchone()[
                "n"
            ]
        ) == 7
        media = {
            row["media_type"]
            for row in connection.execute(
                "SELECT media_type FROM source_artifact"
            ).fetchall()
        }
        assert {
            "application/json",
            "image/png",
            "text/markdown",
            "text/plain",
            "text/x-rst",
        }.issubset(media)
    finally:
        connection.close()


def test_reviewed_plan_keeps_unsupported_or_malformed_legacy_content_quarantined(
    tmp_path: Path,
) -> None:
    fixture = _reviewed_fixture(
        tmp_path,
        extra_personal_files={
            "inbox/malformed.json": b'{"unterminated":',
            "raw/unsupported.bin": b"not an allowlisted legacy text adapter",
        },
    )
    rows = {
        row["source_record"]["relative_path"]: row
        for row in json.loads(fixture.plan_bytes)["draft"]["records"]
    }
    for suffix in ("admin/inbox/malformed.json", "admin/raw/unsupported.bin"):
        row = next(value for path, value in rows.items() if path.endswith(suffix))
        assert row["action"] == "quarantine"
        assert row["execution_phase"] == "none"
        assert row["import_mode"] is None
        assert row["final_terminal_classification"] == "quarantined"
        assert row["record_id"] not in fixture.extra_record_ids


def test_apply_service_resumes_after_a_committed_asset_batch(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)

    def failpoint(name: str) -> None:
        if name == "legacy_content.after_item":
            raise RuntimeError("synthetic item interruption")

    interrupted = LegacyMigrationApplyService(
        factory,
        _storage_layout(isolated_knowledge_layout),
        failpoint=failpoint,
    )
    with pytest.raises(RuntimeError, match="synthetic item interruption"):
        interrupted.apply(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
            batch_size=1,
        )
    midway = _counts(factory)
    assert _user_count(factory) == 1
    assert midway["legacy_migration_map"] == 3
    assert midway["knowledge_item"] == 0
    assert midway["legacy_migration_relationship_map"] == 0

    resumed = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    ).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=1,
    )
    assert resumed.inserted_mapping_count == 2
    assert _counts(factory)["legacy_migration_map"] == 4
    assert _counts(factory)["legacy_migration_relationship_map"] == 1


def test_apply_service_rejects_tree_drift_before_first_database_effect(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    plan = json.loads(fixture.plan_bytes)["draft"]
    workspace = next(
        row
        for row in plan["source_snapshot"]["roots"]
        if row["root_id"] == "workspace"
    )
    (Path(workspace["resolved_path"]) / "late-source.txt").write_text(
        "drift", encoding="utf-8"
    )
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    with pytest.raises(LegacySourceBindingError):
        service.apply(
            fixture.plan_bytes, expected_plan_sha256=fixture.plan_sha256
        )
    assert _user_count(factory) == 0
    assert _counts(factory)["legacy_migration_map"] == 0


def test_apply_service_rejects_canonical_storage_source_overlap(
    isolated_knowledge_layout,
) -> None:
    fixture = _reviewed_fixture(isolated_knowledge_layout.root)
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    with pytest.raises(LegacyMigrationApplyLayoutError):
        service.apply(
            fixture.plan_bytes, expected_plan_sha256=fixture.plan_sha256
        )
    assert _user_count(factory) == 0


@pytest.mark.parametrize("batch_size", [0, 101, True, 1.5])
def test_apply_service_rejects_invalid_batch_size_before_source_reads(
    tmp_path: Path, isolated_knowledge_layout, batch_size: object
) -> None:
    fixture = _reviewed_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    with pytest.raises(LegacyMigrationApplyPlanError):
        service.apply(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
            batch_size=batch_size,  # type: ignore[arg-type]
        )
    assert _user_count(factory) == 0


def _apply_cli_arguments(tmp_path: Path) -> list[str]:
    return [
        "migrate",
        "apply",
        "--plan",
        str(tmp_path / "reviewed-plan.json"),
        "--plan-sha256",
        "a" * 64,
        "--data-root",
        str(tmp_path / "data"),
        "--database",
        str(tmp_path / "data" / "knowledge-core.db"),
        "--blob-root",
        str(tmp_path / "data" / "blobs"),
        "--projection-root",
        str(tmp_path / "data" / "projections"),
        "--batch-size",
        "100",
        "--resume",
    ]


def test_cli_apply_requires_explicit_authority_and_prints_sanitized_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    captured = {}

    def fake_apply(arguments):
        captured["arguments"] = arguments
        return SimpleNamespace(
            plan_sha256="a" * 64,
            identity=SimpleNamespace(imported_user_count=2),
            content_batches=(object(), object()),
            p25_batches=(object(), object()),
            p25_notion_batches=(object(),),
            imported_record_count=3,
            imported_relationship_count=1,
            inserted_mapping_count=7,
        )

    monkeypatch.setattr(cli, "_apply", fake_apply)
    assert cli.main(_apply_cli_arguments(tmp_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "apply": {
            "content_batch_count": 2,
            "imported_record_count": 3,
            "imported_relationship_count": 1,
            "imported_user_count": 2,
            "inserted_mapping_count": 7,
            "plan_sha256": "a" * 64,
            "p24_batch_count": 0,
            "p25_batch_count": 3,
            "status": "complete",
        }
    }
    assert captured["arguments"].resume is True


@pytest.mark.parametrize(
    ("error", "exit_code", "code"),
    [
        (LegacySourceBindingError("source changed"), 2, "SOURCE_CHANGED"),
        (LegacyMigrationApplyLayoutError("layout changed"), 3, "APPLY_LAYOUT_INVALID"),
        (LegacyP25StateConflictError("private state"), 3, "APPLY_STATE_CONFLICT"),
        (LegacyP25PreparationError("private plan"), 2, "PLAN_INVALID"),
        (LegacyP25NotionPreparationError("private notion"), 2, "PLAN_INVALID"),
    ],
)
def test_cli_apply_maps_errors_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    error: Exception,
    exit_code: int,
    code: str,
) -> None:
    def fail(_arguments):
        raise error

    monkeypatch.setattr(cli, "_apply", fail)
    assert cli.main(_apply_cli_arguments(tmp_path)) == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == code
    assert str(error) not in json.dumps(payload)


def test_cli_apply_requires_resume_flag(
    tmp_path: Path, capsys
) -> None:
    arguments = _apply_cli_arguments(tmp_path)
    arguments.remove("--resume")
    assert cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CLI_INVALID_ARGUMENT"
