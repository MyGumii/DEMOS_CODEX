from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from code_assist_v1.knowledge_core import cli
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
)
from code_assist_v1.knowledge_core.legacy.importer import LegacyIdentityImporter
from code_assist_v1.knowledge_core.legacy.migration_apply import (
    LegacyMigrationApplyService,
)
from code_assist_v1.knowledge_core.legacy.p25_importer import (
    LegacyP25Importer,
    LegacyP25PreparationError,
)
from code_assist_v1.knowledge_core.legacy.p25_notion_importer import (
    LegacyP25NotionImporter,
    LegacyP25NotionPreparationError,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import canonical_json_bytes
from code_assist_v1.knowledge_core.legacy.planner import (
    build_plan_draft,
    confirm_migration_plan,
)
from code_assist_v1.knowledge_core.repositories.migration import VerifiedBlobEvidence
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_content_importer import (
    _factory,
    _storage_layout,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    _clock,
    _coverage,
    _csv_bytes,
    _mapping_row,
    _mkdirs,
)
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    canonical_json_bytes as canonical_inventory_json_bytes,
)


pytestmark = pytest.mark.unit

_ATTESTATION = "7" * 64
_MESSAGE_SENTINEL = "SESSION_PRIVATE_SENTINEL"
_JOB_SENTINEL = "JOB_PRIVATE_SENTINEL"


@dataclass(frozen=True)
class _P25Fixture:
    plan_bytes: bytes
    plan_sha256: str
    users_bytes: bytes
    session_bytes: bytes
    session_parent_id: str
    session_child_ids: tuple[str, ...]
    terminal_job_bytes: bytes
    terminal_job_id: str
    running_job_bytes: bytes
    running_job_id: str
    notion_payloads: tuple[tuple[str, bytes], ...] = ()
    notion_link_ids: tuple[str, ...] = ()
    notion_relationship_ids: tuple[str, ...] = ()


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reviewed_p25_fixture(
    tmp_path: Path, *, include_notion: bool = False, notion_wal: bool = False
) -> _P25Fixture:
    fixture_root = tmp_path / "p25-source-fixture"
    fixture_root.mkdir()
    roots = _mkdirs(fixture_root)
    users_bytes = (
        b'{"users":{"admin":{"disabled":false,"password_hash":'
        b'"SANITIZED_TEST_HASH","role":"admin"}}}'
    )
    (roots["runtime"] / "users.json").write_bytes(users_bytes)

    session = {
        "bindings": ["legacy-binding"],
        "id": "alpha",
        "message_count": 2,
        "messages": [
            {
                "content": _MESSAGE_SENTINEL,
                "role": "user",
                "timestamp": 1_725_000_001,
            },
            {
                "content": "sanitized response",
                "role": "assistant",
                "timestamp": 1_725_000_002,
            },
        ],
        "metadata": {
            "compact_summary": "sanitized summary",
            "project_id": "legacy-project",
        },
        "skills_used": ["legacy-skill"],
        "timestamp": 1_725_000_000,
        "title": "Legacy alpha",
        "uploaded_files": [{"name": "legacy.txt", "sha256": "a" * 64}],
    }
    terminal_job = {
        "job_id": "terminal-job",
        "kind": "outlook_sync",
        "private_payload": _JOB_SENTINEL,
        "status": "succeeded",
        "user_id": "admin",
    }
    running_job = {
        "job_id": "running-job",
        "kind": "project_context.import_text",
        "private_payload": _JOB_SENTINEL,
        "status": "running",
        "user_id": "admin",
    }
    session_bytes = _json_bytes(session)
    terminal_job_bytes = _json_bytes(terminal_job)
    running_job_bytes = _json_bytes(running_job)

    user_root = roots["user_data"] / "admin"
    session_path = user_root / "sessions" / "alpha.json"
    terminal_path = user_root / "jobs" / "terminal-job.json"
    running_path = user_root / "jobs" / "running-job.json"
    session_path.parent.mkdir(parents=True)
    terminal_path.parent.mkdir(parents=True)
    session_path.write_bytes(session_bytes)
    terminal_path.write_bytes(terminal_job_bytes)
    running_path.write_bytes(running_job_bytes)
    notion_database: Path | None = None
    notion_connection: sqlite3.Connection | None = None
    if include_notion:
        notion_database = user_root / "notion" / "notion.db"
        notion_database.parent.mkdir(parents=True)
        connection = sqlite3.connect(notion_database)
        notion_connection = connection
        try:
            if notion_wal:
                assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
                connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.executescript(
                """
                CREATE TABLE pages (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  owner_user_id TEXT NOT NULL,
                  created_at INTEGER NOT NULL
                );
                CREATE TABLE session_pages (
                  session_id TEXT NOT NULL,
                  page_id TEXT NOT NULL,
                  owner_user_id TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY(session_id, page_id)
                );
                INSERT INTO pages VALUES ('page-1','sanitized','admin',1);
                INSERT INTO session_pages VALUES ('alpha','page-1','admin',2);
                """
            )
            connection.commit()
        finally:
            if not notion_wal:
                connection.close()
                notion_connection = None

    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "p2.5-importer-snapshot",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        scratch_root=roots["scratch"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
    )
    assert exit_code == 0, report["errors"]
    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
    mapping_bytes = _csv_bytes([_mapping_row(claim="admin")])
    draft = build_plan_draft(
        inventory_bytes,
        mapping_bytes,
        expected_inventory_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=_ATTESTATION,
        expected_root_binding_sha256=report["source_snapshot"][
            "root_binding_sha256"
        ],
        expected_organization_mapping_document_sha256=hashlib.sha256(
            mapping_bytes
        ).hexdigest(),
        allow_data_quality_errors=True,
    )
    draft_bytes = canonical_json_bytes(draft) + b"\n"
    reviewed = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
        review_attestation_sha256="8" * 64,
        reviewed_at_utc="2026-08-13T02:00:00.000000Z",
        reviewer_id="p2.5-importer-reviewer",
    )
    records = list(draft["records"])
    session_parent = next(
        row
        for row in records
        if row["category"] == "conversation.user_sessions"
        and row["source_record"]["record_kind"] == "physical_file"
    )
    session_children = tuple(
        str(row["record_id"])
        for row in sorted(
            (
                row
                for row in records
                if row["category"] == "conversation.user_sessions"
                and row["source_record"]["record_kind"] == "logical_record"
            ),
            key=lambda row: int(row["source_record"]["source_ordinal"]),
        )
    )
    jobs = {
        Path(str(row["source_record"]["relative_path"])).stem: row
        for row in records
        if row["category"] == "operations.jobs" and row["action"] == "import"
    }
    notion_payloads: tuple[tuple[str, bytes], ...] = ()
    notion_link_ids: tuple[str, ...] = ()
    notion_relationship_ids: tuple[str, ...] = ()
    if notion_database is not None:
        notion_parent = next(
            row
            for row in records
            if row["category"] == "external.notion_database"
            and row["source_record"]["record_kind"] == "physical_file"
        )
        parent_id = str(notion_parent["record_id"])
        parent_source = notion_parent["source_record"]
        main_relative = str(parent_source["relative_path"])
        components = tuple(
            row
            for row in records
            if row["source_record"]["record_kind"] == "physical_file"
            and row["source_record"]["root_id"] == parent_source["root_id"]
            and str(row["source_record"]["relative_path"])
            in {
                main_relative,
                main_relative + "-wal",
                main_relative + "-shm",
                main_relative + "-journal",
            }
        )
        notion_payloads = tuple(
            sorted(
                (
                    str(row["record_id"]),
                    (roots["user_data"] / str(row["source_record"]["relative_path"])).read_bytes(),
                )
                for row in components
            )
        )
        notion_links = tuple(
            row
            for row in records
            if row["category"] == "external.notion_session_links"
            and row["action"] == "import"
        )
        notion_link_ids = tuple(sorted(str(row["record_id"]) for row in notion_links))
        relationship_ids = {
            relationship_id
            for row in notion_links
            for relationship_id in row["relationship_ids"]
        }
        notion_relationship_ids = tuple(sorted(relationship_ids))
    result = _P25Fixture(
        plan_bytes=canonical_json_bytes(reviewed) + b"\n",
        plan_sha256=str(reviewed["plan_sha256"]),
        users_bytes=users_bytes,
        session_bytes=session_bytes,
        session_parent_id=str(session_parent["record_id"]),
        session_child_ids=session_children,
        terminal_job_bytes=terminal_job_bytes,
        terminal_job_id=str(jobs["terminal-job"]["record_id"]),
        running_job_bytes=running_job_bytes,
        running_job_id=str(jobs["running-job"]["record_id"]),
        notion_payloads=notion_payloads,
        notion_link_ids=notion_link_ids,
        notion_relationship_ids=notion_relationship_ids,
    )
    if notion_connection is not None:
        notion_connection.close()
    return result


def _apply_identity(factory: Any, fixture: _P25Fixture) -> None:
    LegacyIdentityImporter(factory).apply(
        fixture.plan_bytes,
        fixture.users_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        verified_blob_evidence=VerifiedBlobEvidence(
            raw_sha256=hashlib.sha256(fixture.users_bytes).hexdigest(),
            size_bytes=len(fixture.users_bytes),
            media_type="application/json",
        ),
    )


def _counts(factory: Any) -> dict[str, int]:
    tables = (
        "conversation",
        "conversation_message",
        "conversation_summary",
        "conversation_context_binding",
        "source_artifact",
        "legacy_migration_map",
        "legacy_migration_p25_relationship_map",
        "audit_event",
        "outbox_event",
        "durable_job",
    )
    connection = factory.open()
    try:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[
                    "n"
                ]
            )
            for table in tables
        }
    finally:
        connection.close()


def _importer(factory: Any, layout: Any, **kwargs: Any) -> LegacyP25Importer:
    return LegacyP25Importer(
        factory,
        FilesystemLegacyBlobStager(_storage_layout(layout)),
        **kwargs,
    )


def test_session_and_jobs_apply_exactly_once_without_replaying_jobs(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    importer = _importer(factory, isolated_knowledge_layout)
    selected = tuple(
        sorted(
            (
                fixture.session_parent_id,
                *fixture.session_child_ids,
                fixture.terminal_job_id,
                fixture.running_job_id,
            )
        )
    )
    payloads = {
        fixture.session_parent_id: fixture.session_bytes,
        fixture.terminal_job_id: fixture.terminal_job_bytes,
        fixture.running_job_id: fixture.running_job_bytes,
    }

    first = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=selected,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert len(first.records) == len(selected)
    assert first.inserted_mapping_count == len(selected)
    assert {row.import_mode for row in first.records} >= {
        "source_container",
        "conversation_child",
        "job_history",
        "abandoned_legacy_job",
    }
    after_first = _counts(factory)
    assert after_first["conversation"] == 1
    assert after_first["conversation_message"] == 2
    assert after_first["conversation_summary"] == 1
    assert after_first["conversation_context_binding"] == 0
    assert after_first["source_artifact"] == 2
    assert after_first["durable_job"] == 0
    connection = factory.open()
    try:
        child_kind_counts = {
            str(row["child_kind"]): int(row["n"])
            for row in connection.execute(
                """SELECT json_extract(details_json,'$.child_kind') AS child_kind,
                          count(*) AS n
                     FROM legacy_migration_map
                    WHERE json_extract(details_json,'$.schema_version')=5
                      AND json_extract(details_json,'$.import_mode')='conversation_child'
                    GROUP BY child_kind"""
            )
        }
        parent_closure = connection.execute(
            """SELECT json_extract(details_json,'$.planned_import_child_record_ids')
                         AS planned,
                      json_extract(details_json,'$.imported_child_bindings_sha256')
                         AS imported_sha
                 FROM legacy_migration_map
                WHERE json_extract(details_json,'$.schema_version')=5
                  AND json_extract(details_json,'$.import_mode')='source_container'
                  AND json_extract(details_json,'$.category')
                      IN ('conversation.user_sessions','conversation.global_sessions')"""
        ).fetchone()
    finally:
        connection.close()
    assert child_kind_counts == {
        "bindings": 2,
        "messages": 2,
        "metadata": 1,
        "session_core_metadata": 1,
        "summaries": 1,
        "uploads": 1,
    }
    assert len(json.loads(str(parent_closure["planned"]))) == len(
        fixture.session_child_ids
    )
    assert len(str(parent_closure["imported_sha"])) == 64

    second = importer.apply_batch(
        fixture.plan_bytes,
        payloads,
        selected_record_ids=selected,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert second.inserted_mapping_count == 0
    assert _counts(factory) == after_first


def test_session_children_resume_before_dependency_closed_parent(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    importer = _importer(factory, isolated_knowledge_layout)

    children = importer.apply_batch(
        fixture.plan_bytes,
        {fixture.session_parent_id: fixture.session_bytes},
        selected_record_ids=tuple(sorted(fixture.session_child_ids)),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert children.inserted_mapping_count == len(fixture.session_child_ids)
    parent = importer.apply_batch(
        fixture.plan_bytes,
        {fixture.session_parent_id: fixture.session_bytes},
        selected_record_ids=(fixture.session_parent_id,),
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert parent.records[0].result_disposition == "dependency_closed"
    assert parent.inserted_mapping_count == 1


def test_p25_failpoint_rolls_back_conversation_mapping_audit_and_outbox(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    before = _counts(factory)

    def failpoint(name: str) -> None:
        if name == "p25.after_session_child":
            raise RuntimeError("synthetic P2.5 rollback")

    importer = _importer(
        factory,
        isolated_knowledge_layout,
        failpoint=failpoint,
    )
    with pytest.raises(RuntimeError, match="synthetic P2.5 rollback"):
        importer.apply_batch(
            fixture.plan_bytes,
            {fixture.session_parent_id: fixture.session_bytes},
            selected_record_ids=(fixture.session_child_ids[0],),
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before


def test_p25_rejects_source_drift_before_db_and_keeps_private_payloads_out_of_events(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    importer = _importer(factory, isolated_knowledge_layout)
    before = _counts(factory)
    with pytest.raises(LegacyP25PreparationError):
        importer.apply_batch(
            fixture.plan_bytes,
            {fixture.session_parent_id: fixture.session_bytes + b"x"},
            selected_record_ids=(fixture.session_child_ids[0],),
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before

    selected = tuple(sorted((fixture.session_child_ids[1], fixture.running_job_id)))
    importer.apply_batch(
        fixture.plan_bytes,
        {
            fixture.session_parent_id: fixture.session_bytes,
            fixture.running_job_id: fixture.running_job_bytes,
        },
        selected_record_ids=selected,
        expected_plan_sha256=fixture.plan_sha256,
    )
    connection = factory.open()
    try:
        event_text = "\n".join(
            str(row["payload_json"])
            for row in connection.execute("SELECT payload_json FROM outbox_event")
        ) + "\n" + "\n".join(
            str(row["details_json"])
            for row in connection.execute("SELECT details_json FROM audit_event")
        )
    finally:
        connection.close()
    assert _MESSAGE_SENTINEL not in event_text
    assert _JOB_SENTINEL not in event_text
    assert _counts(factory)["durable_job"] == 0


def test_end_to_end_apply_batches_session_children_then_parent_and_jobs(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path, include_notion=True)
    factory = _factory(isolated_knowledge_layout)
    service = LegacyMigrationApplyService(
        factory,
        _storage_layout(isolated_knowledge_layout),
    )

    first = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    assert first.p25_batches
    assert sum(len(batch.records) for batch in first.p25_batches) == (
        len(fixture.session_child_ids) + 3
    )
    assert sum(len(batch.records) for batch in first.p25_notion_batches) == 1
    assert sum(len(batch.relationships) for batch in first.p25_notion_batches) == 1
    assert _counts(factory)["durable_job"] == 0
    after_first = _counts(factory)

    second = service.apply(
        fixture.plan_bytes,
        expected_plan_sha256=fixture.plan_sha256,
        batch_size=2,
    )
    assert second.inserted_mapping_count == 0
    assert _counts(factory) == after_first


def test_public_apply_cli_imports_p25_and_reports_sanitized_exact_replay(
    tmp_path: Path,
    isolated_knowledge_layout: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path, include_notion=True)
    factory = _factory(isolated_knowledge_layout)
    from code_assist_v1.knowledge_core import db as db_module

    monkeypatch.setattr(db_module, "ConnectionFactory", lambda *_args: factory)
    plan_path = tmp_path / "reviewed-p25-plan.json"
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

    assert cli.main(arguments) == 0
    first_text = capsys.readouterr().out
    first = json.loads(first_text)["apply"]
    assert first["status"] == "complete"
    assert first["p25_batch_count"] > 1
    assert first["imported_record_count"] == len(fixture.session_child_ids) + 4
    assert first["imported_relationship_count"] == 1
    assert first["inserted_mapping_count"] > 0
    assert _MESSAGE_SENTINEL not in first_text
    assert _JOB_SENTINEL not in first_text

    assert cli.main(arguments) == 0
    second_text = capsys.readouterr().out
    second = json.loads(second_text)["apply"]
    assert second["inserted_mapping_count"] == 0
    assert second["imported_record_count"] == first["imported_record_count"]
    assert second["imported_relationship_count"] == 1
    assert _MESSAGE_SENTINEL not in second_text
    assert _JOB_SENTINEL not in second_text


@pytest.mark.parametrize("notion_wal", [False, True])
def test_frozen_notion_link_creates_exact_live_binding_and_relationship_checkpoint(
    tmp_path: Path, isolated_knowledge_layout: Any, notion_wal: bool
) -> None:
    fixture = _reviewed_p25_fixture(
        tmp_path, include_notion=True, notion_wal=notion_wal
    )
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    session_importer = _importer(factory, isolated_knowledge_layout)
    session_importer.apply_batch(
        fixture.plan_bytes,
        {fixture.session_parent_id: fixture.session_bytes},
        selected_record_ids=tuple(
            sorted((*fixture.session_child_ids, fixture.session_parent_id))
        ),
        expected_plan_sha256=fixture.plan_sha256,
    )
    importer = LegacyP25NotionImporter(factory, isolated_knowledge_layout.root)

    first = importer.apply_batch(
        fixture.plan_bytes,
        dict(fixture.notion_payloads),
        selected_record_ids=fixture.notion_link_ids,
        selected_relationship_ids=fixture.notion_relationship_ids,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert first.inserted_mapping_count == 2
    assert len(first.records) == 1
    after_first = _counts(factory)

    second = importer.apply_batch(
        fixture.plan_bytes,
        dict(fixture.notion_payloads),
        selected_record_ids=fixture.notion_link_ids,
        selected_relationship_ids=fixture.notion_relationship_ids,
        expected_plan_sha256=fixture.plan_sha256,
    )
    assert second.inserted_mapping_count == 0
    assert _counts(factory) == after_first

    connection = factory.open()
    try:
        binding = connection.execute(
            """SELECT binding_type,binding_mode,external_key,status
               FROM conversation_context_binding"""
        ).fetchone()
        assert tuple(binding) == ("notion_page", "live", "page-1", "active")
        relationship_count = int(
            connection.execute(
                "SELECT count(*) FROM legacy_migration_p25_relationship_map"
            ).fetchone()[0]
        )
        retained_parent_mappings = int(
            connection.execute(
                """SELECT count(*) FROM legacy_migration_map
                   WHERE legacy_kind='external.notion_database'"""
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert relationship_count == 1
    assert retained_parent_mappings == 0


def test_notion_snapshot_drift_and_failpoint_leave_no_partial_binding_or_checkpoint(
    tmp_path: Path, isolated_knowledge_layout: Any
) -> None:
    fixture = _reviewed_p25_fixture(tmp_path, include_notion=True)
    factory = _factory(isolated_knowledge_layout)
    _apply_identity(factory, fixture)
    _importer(factory, isolated_knowledge_layout).apply_batch(
        fixture.plan_bytes,
        {fixture.session_parent_id: fixture.session_bytes},
        selected_record_ids=tuple(
            sorted((*fixture.session_child_ids, fixture.session_parent_id))
        ),
        expected_plan_sha256=fixture.plan_sha256,
    )
    before = _counts(factory)
    payloads = dict(fixture.notion_payloads)
    first_component = next(iter(payloads))
    payloads[first_component] += b"drift"
    importer = LegacyP25NotionImporter(factory, isolated_knowledge_layout.root)
    with pytest.raises(LegacyP25NotionPreparationError):
        importer.apply_batch(
            fixture.plan_bytes,
            payloads,
            selected_record_ids=fixture.notion_link_ids,
            selected_relationship_ids=fixture.notion_relationship_ids,
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before

    def failpoint(name: str) -> None:
        if name == "p25_notion.after_relationship_mapping":
            raise RuntimeError("synthetic Notion rollback")

    importer = LegacyP25NotionImporter(
        factory,
        isolated_knowledge_layout.root,
        failpoint=failpoint,
    )
    with pytest.raises(RuntimeError, match="synthetic Notion rollback"):
        importer.apply_batch(
            fixture.plan_bytes,
            dict(fixture.notion_payloads),
            selected_record_ids=fixture.notion_link_ids,
            selected_relationship_ids=fixture.notion_relationship_ids,
            expected_plan_sha256=fixture.plan_sha256,
        )
    assert _counts(factory) == before
    assert not tuple(isolated_knowledge_layout.root.glob(".p25-notion-*"))
