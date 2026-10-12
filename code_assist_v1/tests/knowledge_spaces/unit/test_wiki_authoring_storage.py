"""Opt-in storage integration, not live upload or semantic-quality evidence."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, db_now_ms, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG, MigrationStorageLayout, SingleMigrator, schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.authoring_catalog import (
    AUTHORING_MIGRATION_CATALOG, V014_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.repositories.wiki_authoring import (
    AuthoringStorageConflict, WikiAuthoringRepository,
)
from code_assist_v1.knowledge_core.repositories.users import UserRepository
from code_assist_v1.knowledge_core.wiki_authoring import (
    AuthoredWiki, AuthoringIncompleteError, AuthoringModelPolicy, ExtractionSource,
    WikiChunkAuthor, plan_authoring,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.publication import (
    CancelPublicationPreviewCommand, PublicationService, PublishPreviewCommand,
    PublicationPreviewIdempotencyError,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import (
    _command, _seed_actor, _service, _Stager,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]


def _migrate(layout, catalog=AUTHORING_MIGRATION_CATALOG):
    factory = ConnectionFactory._for_isolated_tests(layout.core_db_path, catalog)
    migrator = SingleMigrator(factory, app_version="sbe-8.13-authoring-storage-test",
        storage_layout=MigrationStorageLayout(data_root=layout.root, database=layout.core_db_path,
            blob_root=layout.blobs_dir, projection_root=layout.projections_dir))
    return factory, migrator.run()


@pytest.fixture
def authored_source(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    # Preserve CRLF in this manual-source fixture; it is not rich-file extraction.
    raw = "# 원본 업무 규정\r\n\r\n" + "가동 수치와 단위, 적용 조건을 함께 보존한다.\r\n" * 9
    raw += "\r\n부록: 센서 교체 직후에는 자동 실행을 허용하지 않는다.\r\n"
    preview = _service(factory, _Stager()).create_manual_preview(_command(markdown=raw))
    connection = factory.open()
    try:
        row = connection.execute("""SELECT p.id,b.snapshot_id,b.content_sha256,b.content_md,p.expires_at
            FROM publication_preview p JOIN extraction_snapshot_body b
            ON b.snapshot_id=p.extraction_snapshot_id WHERE p.id=?""", (preview.preview_id,)).fetchone()
        plan = plan_authoring(ExtractionSource(row[1], row[2], row[3]), chunk_bytes=256,
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
        now = db_now_ms(connection)
        repo = WikiAuthoringRepository()
        run_preparation = repo.prepare_run(connection, preview_id=row[0], actor_user_id="actor", plan=plan, now_ms=now)
        with transaction(connection):
            run_id = repo.create_run(connection, preview_id=row[0], actor_user_id="actor", prepared=run_preparation, now_ms=now)
        now = db_now_ms(connection)
        yield SimpleNamespace(factory=factory, connection=connection, repo=repo, run_id=run_id,
            preview_id=row[0], plan=plan, run_preparation=run_preparation, raw=raw, now=now, expires=row[4])
    finally:
        connection.close()


def _result(case, ordinal):
    def runner(task, payload, **kwargs):
        assert not case.connection.in_transaction
        # A fake verifies storage bindings only; it is not a semantic grader.
        response = json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "저장 경계 검증", "claims": [{
                "text": "이 문장은 저장 검증용 생성문이다.", "quotes": [payload["source"]]}]}],
            "warnings": []}, ensure_ascii=False)
        return response, {"model_kind": "codex", "model_id": "fixture"}
    return WikiChunkAuthor(runner=runner).author(case.plan, ordinal)


def _save(case, result, *, actor="actor", now=None):
    prepared = case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id=actor,
        result=result, now_ms=case.now if now is None else now)
    with transaction(case.connection):
        case.repo.save_chunk(case.connection, run_id=case.run_id, actor_user_id=actor,
            prepared=prepared, now_ms=case.now if now is None else now)


def _finish_chunks(case):
    for chunk in reversed(case.plan.chunks):
        _save(case, _result(case, chunk.ordinal))
    return case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)


def test_v14_is_opt_in_and_migrates_v13_without_changing_legacy_catalog(isolated_knowledge_layout):
    assert CANONICAL_MIGRATION_CATALOG.migrations[-1].version == 13
    factory, _ = _migrate(isolated_knowledge_layout, CANONICAL_MIGRATION_CATALOG)
    _seed_actor(factory)
    factory, _ = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        assert schema_sha256(connection) == V014_EXPECTED_SCHEMA_SHA256
        assert connection.execute("SELECT display_name FROM app_user WHERE id='actor'").fetchone()[0] == "Preview Actor"
        verify_canonical_integrity(connection, AUTHORING_MIGRATION_CATALOG)
    finally:
        connection.close()
    # Official migrator replay remains exact and creates no new schema version.
    _migrate(isolated_knowledge_layout)


def test_partial_results_never_become_an_authored_snapshot(authored_source):
    case = authored_source
    assert len(case.plan.chunks) > 1
    _save(case, _result(case, 1))
    with pytest.raises(AuthoringIncompleteError):
        case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0
    assert case.connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "ready"


@pytest.mark.parametrize("status", ["ready", "extraction_only"])
def test_optional_schema_rejects_terminal_status_without_completion_time(authored_source, status):
    case = authored_source
    failure = "model_unavailable" if status == "extraction_only" else None
    with pytest.raises(sqlite3.IntegrityError), transaction(case.connection):
        case.connection.execute("""UPDATE publication_authoring_run SET status=?,failure_code=?,
            completed_at=NULL,row_version=row_version+1 WHERE id=?""", (status, failure, case.run_id))
    assert case.connection.execute("SELECT status FROM publication_authoring_run").fetchone()[0] == "pending"


def test_optional_schema_rejects_ready_without_all_chunks_and_authored_body(authored_source):
    case = authored_source
    _save(case, _result(case, 1))
    with pytest.raises(sqlite3.IntegrityError, match="terminal_incomplete"), transaction(case.connection):
        case.connection.execute("""UPDATE publication_authoring_run SET status='ready',completed_at=?,
            row_version=row_version+1 WHERE id=?""", (case.now, case.run_id))
    with pytest.raises(sqlite3.IntegrityError, match="snapshot_incomplete"), transaction(case.connection):
        case.connection.execute("""INSERT INTO authored_wiki_snapshot
            (id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?)""", (str(uuid4()), case.run_id, "0" * 64, 1, "0" * 64, 2, case.now))
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("source_end", 99999), ("source_sha256", "0" * 64),
    ("request_sha256", "0" * 64), ("ordinal", 99999)])
def test_optional_schema_chunk_rows_must_match_the_frozen_plan(authored_source, field, value):
    case = authored_source
    values = dict(case.connection.execute("SELECT * FROM publication_authoring_chunk WHERE run_id=? AND ordinal=1",
                                         (case.run_id,)).fetchone())
    values[field] = value
    with pytest.raises(sqlite3.IntegrityError, match="chunk_binding"), transaction(case.connection):
        case.connection.execute("DELETE FROM publication_authoring_chunk WHERE run_id=? AND ordinal=1", (case.run_id,))
        case.connection.execute("""INSERT INTO publication_authoring_chunk
            (run_id,ordinal,chunk_id,source_start,source_end,source_sha256,request_sha256)
            VALUES(:run_id,:ordinal,:chunk_id,:source_start,:source_end,:source_sha256,:request_sha256)""", values)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk").fetchone()[0] == len(case.plan.chunks)


def test_optional_schema_rejects_late_chunk_result_after_extraction_only(authored_source):
    case = authored_source
    result = _result(case, 1)
    with transaction(case.connection):
        case.repo.extraction_only(case.connection, run_id=case.run_id, actor_user_id="actor",
                                  now_ms=case.now, failure_code="model_unavailable")
    with pytest.raises(sqlite3.IntegrityError, match="response_binding"), transaction(case.connection):
        case.connection.execute("""INSERT INTO publication_authoring_chunk_body
            (run_id,ordinal,response,response_sha256,model_json,transport_json,completed_at) VALUES(?,?,?,?,?,?,?)""",
            (case.run_id, 1, result.response, sha256(result.response.encode()).hexdigest(),
             json.dumps(dict(result.model)), "{}", case.now))
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 0


def test_optional_schema_checks_authored_body_byte_lengths_before_ready(authored_source):
    case = authored_source
    _finish_chunks(case)
    snapshot_id = str(uuid4())
    with pytest.raises(sqlite3.IntegrityError, match="body_binding"), transaction(case.connection):
        case.connection.execute("""INSERT INTO authored_wiki_snapshot
            (id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?)""", (snapshot_id, case.run_id, "0" * 64, 1, "0" * 64, 2, case.now))
        case.connection.execute("INSERT INTO authored_wiki_snapshot_body(snapshot_id,content_md,manifest_json) VALUES(?,?,?)",
                               (snapshot_id, "한글", "{}"))
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_raw_extraction_authored_are_separate_and_completion_is_exactly_idempotent(authored_source):
    case = authored_source
    before = tuple(case.connection.execute("SELECT * FROM extraction_snapshot_body").fetchone())
    prepared = _finish_chunks(case)
    assert prepared.wiki.markdown != case.plan.source.markdown
    manifest = json.loads(prepared.wiki.manifest_json)
    assert manifest["source_sha256"] == case.plan.source.content_sha256
    assert manifest["all_chunks_processed"] and not manifest["semantic_support_verified"]
    with transaction(case.connection):
        first = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
    with transaction(case.connection):
        second = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.expires + 1)
    assert first == second
    assert tuple(case.connection.execute("SELECT * FROM extraction_snapshot_body").fetchone()) == before
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 1
    assert case.connection.execute("SELECT content_md FROM authored_wiki_snapshot_body").fetchone()[0] == prepared.wiki.markdown
    assert case.connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "ready"
    assert case.connection.execute("SELECT status FROM publication_authoring_run").fetchone()[0] == "ready"
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
            prepared=replace(prepared, wiki=replace(prepared.wiki, markdown=prepared.wiki.markdown + "forged")), now_ms=case.now)


def test_chunk_retry_never_overwrites_a_completed_response(authored_source):
    case = authored_source
    result = _result(case, 1)
    _save(case, result)
    _save(case, result)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 1
    value = json.loads(result.response)
    value["sections"][0]["claims"][0]["text"] = "A different proposed paraphrase."
    from code_assist_v1.knowledge_core.wiki_authoring import validate_chunk_output
    response = json.dumps(value, ensure_ascii=False)
    sections, warnings = validate_chunk_output(response, result.chunk)
    with pytest.raises(AuthoringStorageConflict):
        _save(case, replace(result, response=response, sections=sections, warnings=warnings))


def test_actor_and_expiry_fence_every_new_authoring_effect(authored_source):
    case = authored_source
    result = _result(case, 1)
    with pytest.raises(AuthoringStorageConflict):
        _save(case, result, actor="different-user")
    with pytest.raises(AuthoringStorageConflict):
        _save(case, result, now=case.expires)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["model_unavailable", "invalid_output", "work_limit", "expired"])
def test_authoring_failure_preserves_ready_extraction_and_is_idempotent(authored_source, failure):
    case = authored_source
    before = tuple(case.connection.execute("SELECT * FROM publication_preview").fetchone())
    for _ in range(2):
        with transaction(case.connection):
            case.repo.extraction_only(case.connection, run_id=case.run_id, actor_user_id="actor",
                now_ms=case.now, failure_code=failure)
    assert tuple(case.connection.execute("SELECT * FROM publication_preview").fetchone()) == before
    assert case.connection.execute("SELECT content_md FROM extraction_snapshot_body").fetchone()[0] == case.plan.source.markdown
    with pytest.raises(AuthoringStorageConflict):
        _save(case, _result(case, 1))


def test_operation_plan_drift_and_source_mismatch_are_rejected(authored_source):
    case = authored_source
    with transaction(case.connection):
        assert case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=case.run_preparation, now_ms=case.now) == case.run_id
    changed_policy = plan_authoring(case.plan.source, chunk_bytes=256,
        model_policy=replace(case.plan.model_policy, max_tokens=100))
    changed_preparation = case.repo.prepare_run(case.connection, preview_id=case.preview_id,
        actor_user_id="actor", plan=changed_policy, now_ms=case.now)
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=changed_preparation, now_ms=case.now)
    wrong_text = "different extraction"
    wrong_source = ExtractionSource(case.plan.source.snapshot_id, sha256(wrong_text.encode()).hexdigest(), wrong_text)
    with pytest.raises(AuthoringStorageConflict):
        case.repo.prepare_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            plan=plan_authoring(wrong_source), now_ms=case.now)


def test_initial_run_commit_does_not_reload_extraction_or_build_requests(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    original = storage._preview
    def outside_only(connection, *args, **kwargs):
        assert not connection.in_transaction, "full extraction read inside initial writer"
        return original(connection, *args, **kwargs)
    monkeypatch.setattr(storage, "_preview", outside_only)
    prepared = case.repo.prepare_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
                                    plan=case.plan, now_ms=case.now)
    monkeypatch.setattr(storage, "_plan_json", lambda *args: pytest.fail("request construction inside writer"))
    with transaction(case.connection):
        assert case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=prepared, now_ms=case.now) == case.run_id


@pytest.mark.parametrize("field,value", [("plan_sha256", "0" * 64), ("plan_json", "{}"),
    ("reserved_work", (0, 0, 0)), ("run_id", "00000000-0000-0000-0000-000000000000"),
    ("chunks", ()), ("fence_json", "{}"), ("actor_user_id", "someone-else")])
def test_initial_preparation_metadata_cannot_be_tampered(authored_source, field, value):
    case = authored_source
    with pytest.raises(AuthoringStorageConflict), transaction(case.connection):
        case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=replace(case.run_preparation, **{field: value}), now_ms=case.now)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_run").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["epoch", "disabled", "expired"])
def test_initial_commit_rechecks_current_source_and_actor(authored_source, change):
    case = authored_source
    if change == "disabled":
        _account_status(case, "disabled")
    with transaction(case.connection):
        if change == "epoch":
            case.connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id='actor'")
    with pytest.raises(AuthoringStorageConflict), transaction(case.connection):
        case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=case.run_preparation, now_ms=case.expires + 1 if change == "expired" else case.now)


def test_initial_preparation_refences_after_final_expensive_work(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    original = storage._plan_work
    def change_epoch_after_work(plan):
        result = original(plan)
        assert not case.connection.in_transaction
        with transaction(case.connection):
            case.connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id='actor'")
        return result
    monkeypatch.setattr(storage, "_plan_work", change_epoch_after_work)
    with pytest.raises(AuthoringStorageConflict):
        case.repo.prepare_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            plan=case.plan, now_ms=case.now)


def test_initial_run_after_worker_key_change_requires_repreparation(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    monkeypatch.setattr(storage, "_PREPARATION_KEY", b"isolated new worker key")
    with pytest.raises(AuthoringStorageConflict), transaction(case.connection):
        case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=case.run_preparation, now_ms=case.now)
    recreated = case.repo.prepare_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
        plan=case.plan, now_ms=case.now)
    with transaction(case.connection):
        assert case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=recreated, now_ms=case.now) == case.run_id


def test_failed_completion_transaction_rolls_back_all_generated_rows(authored_source):
    case = authored_source
    prepared = _finish_chunks(case)
    with pytest.raises(RuntimeError, match="after-completion"):
        with transaction(case.connection):
            case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
            raise RuntimeError("after-completion")
    assert case.connection.execute("SELECT status FROM publication_authoring_run").fetchone()[0] == "pending"
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot_body").fetchone()[0] == 0
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
            prepared=replace(prepared, wiki=AuthoredWiki("forged", prepared.wiki.manifest_json)), now_ms=case.now)


def test_completion_commit_does_not_reload_source_or_reassemble(authored_source, monkeypatch):
    case = authored_source
    prepared = _finish_chunks(case)
    monkeypatch.setattr(case.repo, "load_plan", lambda *a, **kw: pytest.fail("whole source read inside write"))
    monkeypatch.setattr(case.repo, "assemble", lambda *a, **kw: pytest.fail("assembly inside write"))
    with transaction(case.connection):
        result = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
            prepared=prepared, now_ms=case.now)
    assert result


def test_prepared_wiki_cannot_commit_after_expiry_or_terminal_failure(authored_source):
    case = authored_source
    prepared = _finish_chunks(case)
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.expires)
    with transaction(case.connection):
        case.repo.extraction_only(case.connection, run_id=case.run_id, actor_user_id="actor",
            now_ms=case.now, failure_code="model_unavailable")
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_source_repository_requires_service_transaction(authored_source):
    case = authored_source
    with pytest.raises(AuthoringStorageConflict, match="transaction"):
        case.repo.create_run(case.connection, preview_id=case.preview_id, actor_user_id="actor",
            prepared=case.run_preparation, now_ms=case.now)


def test_worker_restart_requires_repreparation_but_keeps_durable_results(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    prepared = _finish_chunks(case)
    monkeypatch.setattr(storage, "_PREPARATION_KEY", b"a-new-process-local-preparation-key")
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict, match="changed"):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
    again = case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)
    assert again.wiki == prepared.wiki and again.preparation_seal != prepared.preparation_seal
    with transaction(case.connection):
        first = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=again, now_ms=case.now)
    # Exact committed replay is metadata-only, not a new prepared effect.
    with transaction(case.connection):
        assert case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
            prepared=prepared, now_ms=case.expires + 1) == first


def test_missing_durable_response_after_preparation_rejects_commit(authored_source):
    case = authored_source
    prepared = _finish_chunks(case)
    with transaction(case.connection):
        case.connection.execute("DELETE FROM publication_authoring_chunk_body WHERE run_id=? AND ordinal=1", (case.run_id,))
    with transaction(case.connection), pytest.raises(AuthoringIncompleteError, match="receipt"):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_preparation_identity_and_actor_cannot_be_rebound(authored_source):
    case = authored_source
    prepared = _finish_chunks(case)
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="different-user", prepared=prepared, now_ms=case.now)
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
            prepared=replace(prepared, fence_json=prepared.fence_json + " "), now_ms=case.now)


def _publish_extraction(case, preview_id, source_sha256):
    publisher = PublicationService(case.factory, ScopeResolver(
        FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True)))
    command = PublishPreviewCommand(request_id=str(uuid4()), operation_id=str(uuid4()),
        actor_user_id="actor", actor_scope_epoch=1, preview_id=preview_id,
        source_snapshot_sha256=source_sha256, target_mode="personal")
    return publisher.publish(command)


def _insert_pending_snapshot(case, prepared):
    """Exercise optional DB guards, not a product authorship-selection service."""
    snapshot_id = str(uuid4())
    content = prepared.wiki.markdown
    manifest = prepared.wiki.manifest_json
    with transaction(case.connection):
        case.connection.execute("""INSERT INTO authored_wiki_snapshot
            (id,run_id,content_sha256,content_size_bytes,manifest_sha256,manifest_size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?)""", (snapshot_id, case.run_id, sha256(content.encode()).hexdigest(),
                len(content.encode()), sha256(manifest.encode()).hexdigest(), len(manifest.encode()), case.now))
        case.connection.execute("INSERT INTO authored_wiki_snapshot_body VALUES(?,?,?)",
                               (snapshot_id, content, manifest))
    return snapshot_id


def test_optional_authored_selection_rejects_metadata_only_after_extraction_publication(authored_source):
    case = authored_source
    prepared = _finish_chunks(case)
    with transaction(case.connection):
        snapshot_id = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
                                         prepared=prepared, now_ms=case.now)
    published = _publish_extraction(case, case.preview_id, case.plan.source.content_sha256)
    assert case.connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="selection_binding"), transaction(case.connection):
        case.connection.execute("INSERT INTO publication_authored_selection VALUES(?,?)",
                               (published.publication_operation_id, snapshot_id))
    assert case.connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0
    # A metadata-only claim must not relabel an already-published extraction.
    row = case.connection.execute("""SELECT b.content_md FROM knowledge_revision_body b
        JOIN knowledge_item i ON i.current_revision_id=b.revision_id WHERE i.id=?""",
        (published.targets[0]["item_id"],)).fetchone()
    assert row[0] == case.plan.source.markdown
    assert row[0] != prepared.wiki.markdown


@pytest.mark.parametrize("mismatch", ["different-extraction", "missing-authored-body", "pending-authoring"])
def test_optional_authored_selection_rejects_unready_missing_or_unrelated_source(authored_source, mismatch):
    case = authored_source
    prepared = _finish_chunks(case)
    if mismatch == "pending-authoring":
        snapshot_id = _insert_pending_snapshot(case, prepared)
    else:
        with transaction(case.connection):
            snapshot_id = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
                                             prepared=prepared, now_ms=case.now)
    preview_id, digest = case.preview_id, case.plan.source.content_sha256
    if mismatch == "different-extraction":
        raw = "# 별도 자료\n다른 내용과 조건을 가진 문서다.\n"
        other = _service(case.factory, _Stager()).create_manual_preview(replace(_command(markdown=raw),
            request_id=str(uuid4()), operation_id=str(uuid4())))
        preview_id, digest = other.preview_id, sha256(raw.encode()).hexdigest()
    if mismatch == "missing-authored-body":
        with transaction(case.connection):
            case.connection.execute("DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (snapshot_id,))
    if mismatch == "pending-authoring":
        # The deliberately half-built snapshot also prevents a fallback from
        # terminalizing its run; the whole publication must remain unconsumed.
        with pytest.raises(PublicationPreviewIdempotencyError):
            _publish_extraction(case, preview_id, digest)
        assert case.connection.execute("SELECT count(*) FROM publication_operation").fetchone()[0] == 0
        return
    published = _publish_extraction(case, preview_id, digest)
    with pytest.raises(sqlite3.IntegrityError, match="selection_binding"), transaction(case.connection):
        case.connection.execute("INSERT INTO publication_authored_selection VALUES(?,?)",
                               (published.publication_operation_id, snapshot_id))
    assert case.connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0


def _prepared_chunk(case):
    return case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                  result=_result(case, 1), now_ms=case.now)


def _commit_chunk(case, prepared, *, now=None):
    with transaction(case.connection):
        case.repo.save_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                            prepared=prepared, now_ms=case.now if now is None else now)


def test_chunk_commit_does_not_read_original_plan_or_revalidate_quotes_or_transport(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    prepared = _prepared_chunk(case)
    monkeypatch.setattr(case.repo, "load_plan", lambda *a, **kw: pytest.fail("whole source read in writer"))
    monkeypatch.setattr(storage, "validate_chunk_output", lambda *a, **kw: pytest.fail("quote scan in writer"))
    monkeypatch.setattr(storage, "authoring_request_descriptor", lambda *a, **kw: pytest.fail("request serialization in writer"))
    monkeypatch.setattr(storage, "_transport_receipt", lambda *a, **kw: pytest.fail("transport rebuild in writer"))
    execute = type(case.connection).execute
    observed = []
    def traced(connection, sql, parameters=()):
        if connection is case.connection and connection.in_transaction:
            observed.append(sql)
            assert "content_md" not in sql and "plan_json" not in sql
        return execute(connection, sql, parameters)
    monkeypatch.setattr(type(case.connection), "execute", traced)
    _commit_chunk(case, prepared)
    _commit_chunk(case, prepared)
    assert observed
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 1


@pytest.mark.parametrize("stage", ["chunk", "wiki"])
def test_expensive_authoring_preparation_is_rejected_inside_transaction(authored_source, stage):
    case = authored_source
    result = _result(case, 1)
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict, match="outside"):
        if stage == "chunk":
            case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                    result=result, now_ms=case.now)
        else:
            case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)


@pytest.mark.parametrize("mutation", ["response", "response-hash", "model", "transport", "ordinal", "run", "actor", "fence", "seal", "unicode"])
def test_prepared_chunk_cannot_be_changed_or_rebound(authored_source, mutation):
    case = authored_source
    prepared = _prepared_chunk(case)
    if mutation in {"response", "response-hash", "model", "transport"}:
        values = list(prepared.values)
        field = {"response": 0, "response-hash": 1, "model": 2, "transport": 3}[mutation]
        values[field] += "changed"
        prepared = replace(prepared, values=tuple(values))
    elif mutation == "ordinal":
        prepared = replace(prepared, ordinal=2)
    elif mutation == "run":
        prepared = replace(prepared, run_id=str(uuid4()))
    elif mutation == "actor":
        prepared = replace(prepared, actor_user_id="someone-else")
    elif mutation == "fence":
        prepared = replace(prepared, fence_json=prepared.fence_json + " ")
    elif mutation == "unicode":
        prepared = replace(prepared, values=("\ud800", *prepared.values[1:]))
    else:
        prepared = replace(prepared, preparation_seal="0" * 64)
    with pytest.raises(AuthoringStorageConflict):
        _commit_chunk(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 0


def _account_status(case, status):
    with transaction(case.connection):
        users = UserRepository()
        current = users.require(case.connection, "actor")
        users.transition_account_status(case.connection, user_id="actor", target_status=status,
            expected_row_version=current.row_version, changed_at_ms=case.now + current.scope_epoch)


@pytest.mark.parametrize("change", ["expired", "cancelled", "consumed", "disabled", "epoch-aba", "terminal-failure"])
def test_prepared_chunk_revalidates_current_preview_and_actor_before_insert(authored_source, change):
    case = authored_source
    prepared = _prepared_chunk(case)
    now = case.now
    if change == "expired":
        now = case.expires
    elif change == "cancelled":
        _service(case.factory, _Stager()).cancel_preview(CancelPublicationPreviewCommand(
            request_id=str(uuid4()), operation_id=str(uuid4()), actor_user_id="actor", actor_scope_epoch=1,
            preview_id=case.preview_id))
    elif change == "consumed":
        _publish_extraction(case, case.preview_id, case.plan.source.content_sha256)
    elif change in {"disabled", "epoch-aba"}:
        _account_status(case, "disabled")
        if change == "epoch-aba":
            _account_status(case, "active")
    else:
        with transaction(case.connection):
            case.repo.extraction_only(case.connection, run_id=case.run_id, actor_user_id="actor",
                                     now_ms=case.now, failure_code="model_unavailable")
    with pytest.raises(AuthoringStorageConflict):
        _commit_chunk(case, prepared, now=now)
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 0


def test_new_process_key_requires_chunk_repreparation_without_model_calls(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    result = _result(case, 1)
    prepared = case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                       result=result, now_ms=case.now)
    monkeypatch.setattr(storage, "_PREPARATION_KEY", b"restarted-process")
    with pytest.raises(AuthoringStorageConflict, match="changed"):
        _commit_chunk(case, prepared)
    rebuilt = case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                      result=result, now_ms=case.now)
    assert rebuilt.values == prepared.values and rebuilt.preparation_seal != prepared.preparation_seal
    _commit_chunk(case, rebuilt)


def test_another_process_commits_during_preparation_and_stale_chunk_is_rejected(authored_source, monkeypatch):
    case = authored_source
    original_load = case.repo.load_plan
    result = _result(case, 1)
    script = r'''
import json, sys
from pathlib import Path
from code_assist_v1.knowledge_core.db import ConnectionFactory, db_now_ms, transaction
from code_assist_v1.knowledge_core.migrations.authoring_catalog import AUTHORING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.users import UserRepository
factory = ConnectionFactory._for_isolated_tests(Path(sys.argv[1]), AUTHORING_MIGRATION_CATALOG)
connection = factory.open()
try:
    with transaction(connection):
        users = UserRepository()
        actor = users.require(connection, "actor")
        users.transition_account_status(connection, user_id="actor", target_status="disabled",
            expected_row_version=actor.row_version, changed_at_ms=db_now_ms(connection))
    print(json.dumps({"status": "committed", "scope_epoch": users.require(connection, "actor").scope_epoch}))
finally:
    connection.close()
'''
    observed = []
    def load_while_writer_runs(*args, **kwargs):
        plan = original_load(*args, **kwargs)
        assert not case.connection.in_transaction
        child = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(case.connection.database_path)],
            cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, encoding="utf-8",
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        assert child.returncode == 0, child.stderr
        observed.append(json.loads(child.stdout.splitlines()[-1]))
        return plan
    monkeypatch.setattr(case.repo, "load_plan", load_while_writer_runs)
    with pytest.raises(AuthoringStorageConflict):
        case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                result=result, now_ms=case.now)
    assert observed == [{"status": "committed", "scope_epoch": 2}]
    assert case.connection.execute("SELECT count(*) FROM publication_authoring_chunk_body").fetchone()[0] == 0


def test_changed_actor_cannot_reprepare_old_preview_under_a_new_epoch(authored_source):
    case = authored_source
    result = _result(case, 1)
    _account_status(case, "disabled")
    _account_status(case, "active")
    assert UserRepository().require(case.connection, "actor").scope_epoch == 3
    with pytest.raises(AuthoringStorageConflict):
        case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                result=result, now_ms=case.now)


@pytest.mark.parametrize("reenable", [False, True])
def test_prepared_final_wiki_cannot_newly_complete_after_actor_revocation(authored_source, reenable):
    case = authored_source
    prepared = _finish_chunks(case)
    _account_status(case, "disabled")
    if reenable:
        _account_status(case, "active")
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict):
        case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor",
                           prepared=prepared, now_ms=case.now)
    with pytest.raises(AuthoringStorageConflict):
        case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_final_preparation_rechecks_preview_after_source_and_response_work(authored_source, monkeypatch):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    _finish_chunks(case)
    assemble = storage.assemble_wiki
    def cancel_after_render(*args, **kwargs):
        wiki = assemble(*args, **kwargs)
        _service(case.factory, _Stager()).cancel_preview(CancelPublicationPreviewCommand(
            request_id=str(uuid4()), operation_id=str(uuid4()), actor_user_id="actor", actor_scope_epoch=1,
            preview_id=case.preview_id))
        return wiki
    monkeypatch.setattr(storage, "assemble_wiki", cancel_after_render)
    with pytest.raises(AuthoringStorageConflict):
        case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)
    assert case.connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "cancelled"
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["chunk-prepare", "chunk-save", "wiki-assemble", "wiki-complete"])
def test_expiry_crossing_is_observed_even_when_caller_reuses_old_time(authored_source, monkeypatch, stage):
    import code_assist_v1.knowledge_core.repositories.wiki_authoring as storage
    case = authored_source
    result = _result(case, 1)
    prepared = _prepared_chunk(case) if stage == "chunk-save" else None
    wiki = _finish_chunks(case) if stage.startswith("wiki") else None
    observed = []
    def current_db_time(connection):
        observed.append(connection.in_transaction)
        if stage.endswith("prepare") or stage.endswith("assemble"):
            return case.now if len(observed) == 1 else case.expires
        return case.expires
    monkeypatch.setattr(storage, "db_now_ms", current_db_time)
    with pytest.raises(AuthoringStorageConflict):
        if stage == "chunk-prepare":
            case.repo.prepare_chunk(case.connection, run_id=case.run_id, actor_user_id="actor",
                                    result=result, now_ms=case.now)
        elif stage == "chunk-save":
            _commit_chunk(case, prepared)
        elif stage == "wiki-assemble":
            case.repo.assemble(case.connection, run_id=case.run_id, actor_user_id="actor", now_ms=case.now)
        else:
            with transaction(case.connection):
                case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=wiki, now_ms=case.now)
    assert observed
    if stage in {"chunk-prepare", "wiki-assemble"}:
        assert observed == [False, False]
    else:
        assert observed == [True]
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0
