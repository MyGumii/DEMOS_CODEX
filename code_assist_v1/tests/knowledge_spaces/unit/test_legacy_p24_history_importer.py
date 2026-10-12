from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core import cli
from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.p24_history_importer import (
    LegacyP24HistoryImporter,
)
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _FilesystemTestStager,
    _factory,
    _reviewed_fixture,
    _storage_layout,
)


pytestmark = pytest.mark.unit

_AUDIT_BYTES = (
    b'{"actor":"sentinel-not-exported","status":"success","type":"login"}\n'
    b'{"actor":"sentinel-two","status":"denied","type":"access"}\n'
)
_WIKI_LOG_BYTES = b"# legacy log\n- historical only\n"
_TOMBSTONE_BYTES = (
    b'{"kind":"deleted","status":"deleted","path":"secret-name.md"}\n'
)


def _history_fixture(tmp_path: Path):
    return _reviewed_fixture(
        tmp_path,
        extra_personal_files={
            "tombstones/history.jsonl": _TOMBSTONE_BYTES,
            "wiki/log.md": _WIKI_LOG_BYTES,
        },
        extra_runtime_files={"audit.jsonl": _AUDIT_BYTES},
    )


def _history_rows(fixture) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    draft = json.loads(fixture.plan_bytes)["draft"]
    rows = [
        row
        for row in draft["records"]
        if row["action"] == "import"
        and row["execution_phase"] == "P2.4"
        and row["source_record"]["category"]
        in {
            "identity.auth_audit",
            "knowledge.wiki_log",
            "knowledge.tombstones",
        }
    ]
    logical = [
        row for row in rows if row["source_record"]["record_kind"] == "logical_record"
    ]
    physical = [
        row for row in rows if row["source_record"]["record_kind"] == "physical_file"
    ]
    return logical, physical


def test_p24_history_retains_raw_artifacts_and_sanitized_events_exactly(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _history_fixture(tmp_path)
    logical, physical = _history_rows(fixture)
    assert sorted(
        (row["source_record"]["category"], row["import_mode"])
        for row in physical
    ) == [
        ("identity.auth_audit", "source_container"),
        ("knowledge.tombstones", "legacy_history"),
        ("knowledge.wiki_log", "legacy_history"),
    ]
    assert len(logical) == 3

    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    first = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    replay = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )

    assert sum(len(batch.records) for batch in first.p24_history_batches) == 6
    assert sum(
        batch.inserted_mapping_count for batch in first.p24_history_batches
    ) == 6
    assert sum(
        batch.inserted_mapping_count for batch in replay.p24_history_batches
    ) == 0

    connection = factory.open()
    try:
        assert connection.execute(
            """SELECT count(*) FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=4"""
        ).fetchone()[0] == 6
        artifacts = connection.execute(
            """SELECT artifact.raw_sha256,artifact.metadata_json
               FROM source_artifact AS artifact
               WHERE json_extract(artifact.metadata_json,'$.authority')='historical_only'
               ORDER BY artifact.raw_sha256"""
        ).fetchall()
        assert {row["raw_sha256"] for row in artifacts} == {
            row["source_record"]["sha256"]
            for row in physical
            if row["source_record"]["category"]
            in {"knowledge.tombstones", "knowledge.wiki_log"}
        }
        expected_bytes = {
            next(
                row["source_record"]["sha256"]
                for row in physical
                if row["source_record"]["category"] == "knowledge.wiki_log"
            ): _WIKI_LOG_BYTES,
            next(
                row["source_record"]["sha256"]
                for row in physical
                if row["source_record"]["category"] == "knowledge.tombstones"
            ): _TOMBSTONE_BYTES,
        }
        for digest, content in expected_bytes.items():
            assert (
                isolated_knowledge_layout.blobs_dir
                / digest[:2]
                / digest[2:4]
                / digest
            ).read_bytes() == content
        events = connection.execute(
            """SELECT action,details_json FROM audit_event
               WHERE action LIKE 'legacy_history.%'
               ORDER BY id"""
        ).fetchall()
        assert len(events) == 4
        serialized = "\n".join(str(row["details_json"]) for row in events)
        assert "sentinel-not-exported" not in serialized
        assert "sentinel-two" not in serialized
        assert "secret-name.md" not in serialized
        assert all(
            json.loads(str(row["details_json"]))["authority"]
            == "historical_only"
            for row in events
        )
        assert connection.execute(
            """SELECT count(*) FROM item_source AS source
               JOIN source_artifact AS artifact
                 ON artifact.id=source.artifact_id
               WHERE json_extract(artifact.metadata_json,'$.authority')
                     ='historical_only'"""
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_p24_history_binds_audit_rows_without_optional_kind_or_status(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    audit_bytes = b'{"actor":"sentinel-not-exported"}\n'
    fixture = _reviewed_fixture(
        tmp_path,
        extra_runtime_files={"audit.jsonl": audit_bytes},
    )
    logical, _physical = _history_rows(fixture)
    audit_rows = [
        row
        for row in logical
        if row["source_record"]["category"] == "identity.auth_audit"
    ]
    assert len(audit_rows) == 1

    factory = _factory(isolated_knowledge_layout)
    result = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    ).apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=100,
    )
    assert sum(
        batch.inserted_mapping_count for batch in result.p24_history_batches
    ) >= 2

    connection = factory.open()
    try:
        event = connection.execute(
            """SELECT details_json FROM audit_event
               WHERE action='legacy_history.identity.auth_audit'"""
        ).fetchone()
        assert event is not None
        details = json.loads(str(event["details_json"]))
        assert details["kind"] == "unknown"
        assert details["status"] == "unknown"
        assert "sentinel-not-exported" not in str(event["details_json"])
    finally:
        connection.close()


def test_p24_history_is_included_in_public_apply_cli_summary(
    tmp_path: Path, isolated_knowledge_layout, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _history_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    plan_path = tmp_path / "reviewed-history-plan.json"
    plan_path.write_bytes(fixture.plan_bytes)
    arguments = [
        "migrate",
        "apply",
        "--plan",
        str(plan_path),
        "--plan-sha256",
        fixture.plan_sha256,
        "--data-root",
        str(isolated_knowledge_layout.root),
        "--database",
        str(isolated_knowledge_layout.core_db_path),
        "--blob-root",
        str(isolated_knowledge_layout.blobs_dir),
        "--projection-root",
        str(isolated_knowledge_layout.projections_dir),
        "--batch-size",
        "2",
        "--resume",
    ]
    monkeypatch.setattr(
        cli,
        "_apply",
        lambda parsed: service.apply(
            Path(parsed.plan).read_bytes(),
            expected_plan_sha256=parsed.plan_sha256,
            batch_size=parsed.batch_size,
        ),
    )
    assert cli.main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)["apply"]
    assert payload["status"] == "complete"
    assert payload["p24_batch_count"] == 4
    assert payload["imported_record_count"] >= 6
    assert payload["inserted_mapping_count"] >= 6

    assert cli.main(arguments) == 0
    replay = json.loads(capsys.readouterr().out)["apply"]
    assert replay["p24_batch_count"] == 4
    assert replay["inserted_mapping_count"] == 0


def test_p24_history_parent_cannot_close_before_imported_children(
    tmp_path: Path, isolated_knowledge_layout
) -> None:
    fixture = _history_fixture(tmp_path)
    logical, physical = _history_rows(fixture)
    auth_parent = next(
        row for row in physical if row["source_record"]["category"] == "identity.auth_audit"
    )
    factory = _factory(isolated_knowledge_layout)
    # The normal P2.3 prefix establishes users/personal space.  A failing P2.4
    # history attempt does not poison the immutable ledger.
    service = LegacyMigrationApplyService(
        factory, _storage_layout(isolated_knowledge_layout)
    )
    plan = json.loads(fixture.plan_bytes)["draft"]
    root = next(
        row
        for row in plan["source_snapshot"]["roots"]
        if row["root_id"] == auth_parent["source_record"]["root_id"]
    )
    payload = (
        Path(root["resolved_path"]) / auth_parent["source_record"]["relative_path"]
    ).read_bytes()
    importer = LegacyP24HistoryImporter(
        factory,
        FilesystemLegacyBlobStager(_storage_layout(isolated_knowledge_layout)),
    )
    # Identity has not been applied, but the global audit parent reaches the
    # child-closure check first and must fail without a durable row.
    with pytest.raises(Exception) as caught:
        importer.apply_batch(
            fixture.plan_bytes,
            {str(auth_parent["record_id"]): payload},
            selected_record_ids=(str(auth_parent["record_id"]),),
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert getattr(caught.value, "code", None) == "LEGACY_P24_MAPPING_INTEGRITY_ERROR"
    connection = factory.open()
    try:
        assert connection.execute(
            """SELECT count(*) FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=4"""
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='legacy_history.identity.auth_audit'"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    completed = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    assert sum(
        batch.inserted_mapping_count for batch in completed.p24_history_batches
    ) == len(logical) + len(physical)


@pytest.mark.parametrize("case", ("unsorted", "extra_payload", "overlap"))
def test_p24_history_public_boundary_rejects_unbound_inputs_before_db_write(
    tmp_path: Path,
    isolated_knowledge_layout,
    case: str,
) -> None:
    fixture = _history_fixture(tmp_path)
    _, physical = _history_rows(fixture)
    selected_rows = sorted(
        (
            row
            for row in physical
            if row["source_record"]["category"]
            in {"knowledge.tombstones", "knowledge.wiki_log"}
        ),
        key=lambda row: str(row["record_id"]),
    )
    selected_ids = tuple(str(row["record_id"]) for row in selected_rows)
    draft = json.loads(fixture.plan_bytes)["draft"]
    roots = {
        row["root_id"]: Path(row["resolved_path"])
        for row in draft["source_snapshot"]["roots"]
    }
    payloads = {
        str(row["record_id"]): (
            roots[row["source_record"]["root_id"]]
            / row["source_record"]["relative_path"]
        ).read_bytes()
        for row in selected_rows
    }
    if case == "unsorted":
        selected_ids = tuple(reversed(selected_ids))
    elif case == "extra_payload":
        payloads = {**payloads, "rec_ffffffffffffffffffffffff": b"extra"}
    factory = _factory(isolated_knowledge_layout)
    stager = (
        _FilesystemTestStager(
            roots[selected_rows[0]["source_record"]["root_id"]]
        )
        if case == "overlap"
        else _FilesystemTestStager(isolated_knowledge_layout.blobs_dir)
    )
    before = connection_count = None
    connection = factory.open()
    try:
        before = connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0]
    finally:
        connection.close()
    with pytest.raises(Exception) as caught:
        LegacyP24HistoryImporter(factory, stager).apply_batch(
            fixture.plan_bytes,
            payloads,
            selected_record_ids=selected_ids,
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert getattr(caught.value, "code", None) == "LEGACY_P24_HISTORY_PLAN_INVALID"
    connection = factory.open()
    try:
        connection_count = connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0]
    finally:
        connection.close()
    assert connection_count == before


@pytest.mark.parametrize(
    "failpoint", ("p24_history.after_effect", "p24_history.after_mapping")
)
def test_p24_history_failpoints_roll_back_effect_and_checkpoint(
    tmp_path: Path,
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _history_fixture(tmp_path)

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError("p24-history-failpoint")

    factory = _factory(isolated_knowledge_layout)
    with pytest.raises(RuntimeError, match="p24-history-failpoint"):
        LegacyMigrationApplyService(
            factory,
            _storage_layout(isolated_knowledge_layout),
            failpoint=stop,
        ).apply(
            fixture.plan_bytes,
            expected_plan_sha256=fixture.plan_sha256,
            batch_size=100,
        )
    connection = factory.open()
    try:
        assert connection.execute(
            """SELECT count(*) FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=4"""
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action LIKE 'legacy_history.%'"
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM source_artifact
               WHERE json_extract(metadata_json,'$.authority')='historical_only'"""
        ).fetchone()[0] == 0
    finally:
        connection.close()
