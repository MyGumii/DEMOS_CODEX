"""Real isolated v15 persistence with fake prose, not model-quality evidence."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import transaction, db_now_ms
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG, schema_sha256, verify_canonical_integrity
from code_assist_v1.knowledge_core.migrations.authoring_catalog import AUTHORING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.synthesis_catalog import SYNTHESIS_MIGRATION_CATALOG, V015_EXPECTED_SCHEMA_SHA256
from code_assist_v1.knowledge_core.migrations.model import Migration, MigrationCatalog
from code_assist_v1.knowledge_core.migrations.v015_wiki_synthesis import V015_STATEMENTS
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.repositories.wiki_authoring import AuthoringStorageConflict, WikiAuthoringRepository
from code_assist_v1.knowledge_core.repositories.wiki_synthesis import WikiSynthesisRepository, reduction_job_result
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository, JobEnqueueDraft, HandlerKey, ClaimFence
from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy, ExtractionSource, plan_authoring
from code_assist_v1.knowledge_core.wiki_synthesis import SynthesisPolicy, WikiReductionAuthor, resume_synthesis
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import (
    _migrate, _finish_chunks, _account_status,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import (
    _seed_actor, _service, _Stager, _command,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]


@pytest.fixture
def synthesis_case(isolated_knowledge_layout, request):
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_MIGRATION_CATALOG)
    _seed_actor(factory)
    raw = "# 문서 저장 검증\n" + "수치와 적용 조건을 각 문맥에서 구분한다.\n" * 60 + "부록: 예외 조건을 보존한다.\n"
    options = getattr(request, "param", {})
    raw = options.get("raw", raw)
    preview = _service(factory, _Stager()).create_manual_preview(_command(markdown=raw))
    connection = factory.open()
    try:
        row = connection.execute("""SELECT p.extraction_snapshot_id,p.source_snapshot_sha256,
            b.content_md,p.expires_at FROM publication_preview p JOIN extraction_snapshot_body b
            ON b.snapshot_id=p.extraction_snapshot_id WHERE p.id=?""", (preview.preview_id,)).fetchone()
        plan = plan_authoring(ExtractionSource(row[0], row[1], row[2]), chunk_bytes=options.get("chunk_bytes", 512),
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
        repo = WikiAuthoringRepository()
        now = db_now_ms(connection)
        prepared = repo.prepare_run(connection, preview_id=preview.preview_id, actor_user_id="actor", plan=plan, now_ms=now)
        with transaction(connection):
            run_id = repo.create_run(connection, preview_id=preview.preview_id, actor_user_id="actor", prepared=prepared, now_ms=now)
        now = db_now_ms(connection)
        case = SimpleNamespace(factory=factory, connection=connection, repo=repo, run_id=run_id,
            now=now, expires=row[3], plan=plan, preview_id=preview.preview_id,
            synthesis=WikiSynthesisRepository(), policy=SynthesisPolicy(input_bytes=7000, boundary_context_characters=20))
        case.map_prepared = _finish_chunks(case)
        yield case
    finally:
        connection.close()


def _args(case):
    return dict(run_id=case.run_id, actor_user_id="actor", now_ms=db_now_ms(case.connection))


def _progress(case, **kwargs):
    return case.synthesis.prepare_progress(case.connection, **_args(case), **kwargs)


def _persist(case, prepared):
    with transaction(case.connection):
        return case.synthesis.persist_progress(case.connection, prepared=prepared, now_ms=db_now_ms(case.connection))


def _start(case):
    prepared = _progress(case, policy=case.policy)
    assert prepared.wiki is None
    _persist(case, prepared)
    return prepared


def _pending(case):
    return case.connection.execute("""SELECT b.round_number,b.ordinal FROM publication_synthesis_batch b
        LEFT JOIN publication_synthesis_receipt r
        ON r.run_id=b.run_id AND r.round_number=b.round_number AND r.ordinal=b.ordinal
        WHERE b.run_id=? AND r.run_id IS NULL ORDER BY b.round_number,b.ordinal""", (case.run_id,)).fetchall()


def _prepare_response(case, position):
    number, ordinal = position
    claim = _claim_batch(case.connection, case.synthesis, case.run_id, "actor", number, ordinal)
    plan, wave, batch, policy = case.synthesis.load_batch(case.connection, **_args(case),
                                                        round_number=number, ordinal=ordinal, claim=claim)
    result = WikiReductionAuthor(runner=lambda task, payload, **kwargs: (_response(payload), _META)).author(
        plan, batch, wave=wave, policy=policy)
    return case.synthesis.prepare_receipt(case.connection, **_args(case),
        round_number=number, ordinal=ordinal, result=result, claim=claim)


def _claim_batch(connection, repo, run_id, actor, number, ordinal):
    """Real repository jobs/claims; not a substitute for worker scope authority."""
    jobs = JobRepository()
    with transaction(connection):
        row = connection.execute("""SELECT b.batch_id,b.request_sha256,b.durable_job_id,
            p.durable_job_id,p.captured_scope_epoch,c.reduction_durable_attempts
            FROM publication_synthesis_batch b JOIN publication_authoring_run r ON r.id=b.run_id
            JOIN publication_preview p ON p.id=r.preview_id JOIN publication_synthesis_config c ON c.run_id=r.id
            WHERE b.run_id=? AND b.round_number=? AND b.ordinal=?""", (run_id, number, ordinal)).fetchone()
        assert row is not None and row[2] is None
        parent = jobs.require(connection, row[3]) if row[3] else None
        personal = connection.execute("SELECT id FROM knowledge_space WHERE owner_user_id=? AND space_kind='personal' LIMIT 1",
                                      (actor,)).fetchone()[0]
        user = connection.execute("SELECT account_status,system_role,current_part_id,scope_epoch FROM app_user WHERE id=?", (actor,)).fetchone()
        snapshot = dict(schema_version=1, actor_kind="user", actor_user_id=actor, account_status=user[0],
            system_role=user[1], current_part_id=user[2], scope_epoch=user[3], space_id=personal, capabilities=["publish"])
        job_id = str(uuid4())
        jobs.enqueue(connection, JobEnqueueDraft(job_id=job_id, dedup_id=None, operation_id=None,
            actor_kind="user", actor_user_id=actor, space_id=parent.space_id if parent else personal,
            job_type="publication_wiki.reduce_batch", handler_version="v1",
            effect_key=f"synthesis-fixture:{run_id}:{number}:{ordinal}",
            input_ref=dict(run_id=run_id, round_number=number, ordinal=ordinal, batch_id=row[0], request_sha256=row[1]),
            expected_state={"schema_version": 1},
            authorization_snapshot=parent.authorization_snapshot if parent else snapshot,
            authorization_scope_epoch=row[4], authorization_part_id=parent.authorization_part_id if parent else user[2],
            authorization_capabilities=parent.authorization_capabilities if parent else ("publish",),
            max_attempts=row[5], cancellable=True, available_at_ms=db_now_ms(connection)))
        repo.bind_batch_job(connection, run_id=run_id, actor_user_id=actor, round_number=number,
            ordinal=ordinal, job_id=job_id, now_ms=db_now_ms(connection))
        claim = jobs.claim_next(connection, handler_keys=(HandlerKey("publication_wiki.reduce_batch", "v1"),),
            claim_owner="synthesis-storage-test", lease_ms=60_000, now_ms=db_now_ms(connection))
        assert claim is not None and claim.job.id == job_id
        jobs.start(connection, claim.fence, now_ms=db_now_ms(connection))
        return claim.fence


def _save(case, prepared):
    with transaction(case.connection):
        case.synthesis.save_receipt(case.connection, prepared=prepared, now_ms=db_now_ms(case.connection))
        JobRepository().succeed(case.connection, prepared.claim,
            result=reduction_job_result(run_id=prepared.run_id, ordinal=prepared.ordinal, receipt=prepared.receipt),
            now_ms=db_now_ms(case.connection))


def _finish(case):
    for _ in range(case.policy.max_rounds + 1):
        prepared = _progress(case, policy=case.policy)
        snapshot = _persist(case, prepared)
        if snapshot is not None:
            return snapshot, prepared
        for position in _pending(case):
            _save(case, _prepare_response(case, position))
    pytest.fail("isolated synthesis did not converge")


def _purge_receipt(case, position):
    # Explicit lifecycle simulation only in disposable fixtures; no public or
    # automatic purge operation is added by this storage packet.
    key = (case.run_id, *position)
    with transaction(case.connection):
        case.connection.execute("""UPDATE publication_synthesis_receipt SET retention_status='purged'
            WHERE run_id=? AND round_number=? AND ordinal=?""", key)
        case.connection.execute("""DELETE FROM publication_synthesis_batch_body
            WHERE run_id=? AND round_number=? AND ordinal=?""", key)


def test_v15_is_additive_and_does_not_activate_v13_or_rewrite_v14(synthesis_case):
    case = synthesis_case
    assert CANONICAL_MIGRATION_CATALOG.migrations[-1].version == 13
    assert AUTHORING_MIGRATION_CATALOG.migrations[-1].version == 14
    assert schema_sha256(case.connection) == V015_EXPECTED_SCHEMA_SHA256
    verify_canonical_integrity(case.connection, SYNTHESIS_MIGRATION_CATALOG)
    assert case.connection.execute("SELECT status FROM publication_authoring_run WHERE id=?", (case.run_id,)).fetchone()[0] == "pending"


def test_failed_v14_upgrade_rolls_back_and_exact_v15_upgrade_replays(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    connection = factory.open(read_only=True)
    try:
        original_schema = schema_sha256(connection)
    finally:
        connection.close()
    bad = Migration(version=15, name="isolated_failed_synthesis_upgrade",
        statements=(*V015_STATEMENTS, "SELECT * FROM deliberately_missing_synthesis_upgrade_fixture"),
        expected_schema_sha256=V015_EXPECTED_SCHEMA_SHA256)
    with pytest.raises((KnowledgeCoreMigrationError, sqlite3.OperationalError)):
        _migrate(isolated_knowledge_layout, MigrationCatalog((*AUTHORING_MIGRATION_CATALOG.migrations, bad)))
    connection = factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == original_schema
        assert connection.execute("SELECT count(*) FROM sqlite_schema WHERE name='publication_synthesis_config'").fetchone()[0] == 0
        assert connection.execute("SELECT display_name FROM app_user WHERE id='actor'").fetchone()[0] == "Preview Actor"
    finally:
        connection.close()
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_MIGRATION_CATALOG)
    _migrate(isolated_knowledge_layout, SYNTHESIS_MIGRATION_CATALOG)
    connection = factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == V015_EXPECTED_SCHEMA_SHA256
        verify_canonical_integrity(connection, SYNTHESIS_MIGRATION_CATALOG)
        assert connection.execute("SELECT display_name FROM app_user WHERE id='actor'").fetchone()[0] == "Preview Actor"
    finally:
        connection.close()


def test_synthesis_configuration_blocks_map_only_ready_and_rolls_back_its_snapshot(synthesis_case):
    case = synthesis_case
    _start(case)
    with pytest.raises(sqlite3.IntegrityError, match="synthesis_completion_required"), transaction(case.connection):
        case.repo.complete(case.connection, **_args(case), prepared=case.map_prepared)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot WHERE run_id=?", (case.run_id,)).fetchone()[0] == 0
    assert _pending(case)


def test_all_durable_waves_finish_into_a_distinct_final_snapshot(synthesis_case):
    case = synthesis_case
    snapshot, prepared = _finish(case)
    row = case.connection.execute("""SELECT r.status,x.mode,x.last_round,b.content_md,b.manifest_json
        FROM publication_authoring_run r JOIN publication_synthesis_completion x ON x.run_id=r.id
        JOIN authored_wiki_snapshot_body b ON b.snapshot_id=x.snapshot_id WHERE r.id=?""", (case.run_id,)).fetchone()
    assert tuple(row[:2]) == ("ready", "reduced_complete_wave") and row[2] > 1
    assert row[3] == prepared.wiki.markdown != case.map_prepared.wiki.markdown
    assert json.loads(row[4])["synthesis"]["execution"]["reduction_durable_attempts"] == 2
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_batch_body WHERE run_id=?", (case.run_id,)).fetchone()[0] > 1
    assert _persist(case, prepared) == snapshot


def test_valid_parallel_sibling_receipts_do_not_discard_each_others_provider_work(synthesis_case):
    case = synthesis_case
    _start(case)
    positions = _pending(case)
    assert len(positions) > 1
    first = _prepare_response(case, positions[0])
    second = _prepare_response(case, positions[1])
    _save(case, first)
    _save(case, second)
    assert len(_pending(case)) == len(positions) - 2


def test_stale_progress_cannot_finalize_over_a_new_receipt(synthesis_case):
    case = synthesis_case
    _start(case)
    staged = _progress(case)
    _save(case, _prepare_response(case, _pending(case)[0]))
    with pytest.raises(AuthoringStorageConflict, match="metadata changed"):
        _persist(case, staged)
    assert _progress(case).wiki is None


@pytest.mark.parametrize("mutation", ["config", "wave", "seal"])
def test_prepared_progress_tampering_is_rejected_before_any_write(synthesis_case, mutation):
    case = synthesis_case
    prepared = _progress(case)
    if mutation == "config":
        prepared = replace(prepared, config=(*prepared.config[:3], 1, 1))
    elif mutation == "wave":
        prepared = replace(prepared, wave_json="{}")
    else:
        prepared = replace(prepared, preparation_seal="0" * 64)
    with pytest.raises(AuthoringStorageConflict):
        _persist(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_config").fetchone()[0] == 0


@pytest.mark.parametrize("point", ["plan", "receipt", "final"])
def test_current_actor_epoch_is_rechecked_at_every_writer(point, synthesis_case):
    case = synthesis_case
    if point == "plan":
        prepared = _progress(case)
    else:
        _start(case)
        if point == "receipt":
            prepared = _prepare_response(case, _pending(case)[0])
        else:
            for _ in range(case.policy.max_rounds):
                for position in _pending(case):
                    _save(case, _prepare_response(case, position))
                prepared = _progress(case)
                if prepared.wiki is not None:
                    break
                _persist(case, prepared)
            assert prepared.wiki is not None
    _account_status(case, "disabled")
    with pytest.raises(AuthoringStorageConflict):
        (_save if point == "receipt" else _persist)(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_purged_pending_receipt_cannot_be_reintroduced_or_treated_as_missing_work(synthesis_case):
    case = synthesis_case
    _start(case)
    first, other = _pending(case)[:2]
    saved = _prepare_response(case, first)
    _save(case, saved)
    in_flight = _prepare_response(case, other)
    _purge_receipt(case, first)
    with pytest.raises(AuthoringStorageConflict):
        _progress(case)
    with pytest.raises(AuthoringStorageConflict):
        _save(case, in_flight)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), transaction(case.connection):
        case.connection.execute("""UPDATE publication_synthesis_receipt SET retention_status='present'
            WHERE run_id=? AND round_number=? AND ordinal=?""", (case.run_id, *first))
    with pytest.raises(sqlite3.IntegrityError, match="body_binding"), transaction(case.connection):
        case.connection.execute("""INSERT INTO publication_synthesis_batch_body
            (run_id,round_number,ordinal,response,model_json,transport_json) VALUES(?,?,?,?,?,?)""",
            (case.run_id, *first, saved.receipt.response, saved.receipt.model_json, saved.receipt.transport_json))
    assert case.connection.execute("SELECT retention_status FROM publication_synthesis_receipt").fetchone()[0] == "purged"


def test_ready_lost_ack_replay_survives_intermediate_response_purge_and_process_key_change(synthesis_case, monkeypatch):
    case = synthesis_case
    snapshot, prepared = _finish(case)
    position = case.connection.execute("SELECT round_number,ordinal FROM publication_synthesis_receipt LIMIT 1").fetchone()
    _purge_receipt(case, position)
    import code_assist_v1.knowledge_core.repositories.wiki_synthesis as storage
    monkeypatch.setattr(storage, "_KEY", b"another-runtime-key")
    assert _persist(case, prepared) == snapshot
    with pytest.raises(AuthoringStorageConflict):
        _persist(case, replace(prepared, wiki=replace(prepared.wiki, markdown="not the saved Wiki")))


def test_configuration_and_stage_rows_are_immutable_and_replanning_is_idempotent(synthesis_case):
    case = synthesis_case
    _start(case)
    existing = _progress(case)
    assert _persist(case, existing) is None
    for statement in (
        "UPDATE publication_synthesis_config SET map_durable_attempts=1",
        "UPDATE publication_synthesis_wave SET plan_sha256='" + "0" * 64 + "'",
        "DELETE FROM publication_synthesis_wave",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"), transaction(case.connection):
            case.connection.execute(statement)
    with pytest.raises(AuthoringStorageConflict, match="configuration"):
        _progress(case, policy=replace(case.policy, max_rounds=7))


def test_preparation_and_source_replay_never_run_in_a_writer(synthesis_case):
    case = synthesis_case
    with transaction(case.connection), pytest.raises(AuthoringStorageConflict, match="outside"):
        _progress(case)


def test_jobless_reduction_cannot_be_read_or_saved_through_repository_or_sql(synthesis_case):
    case = synthesis_case
    _start(case)
    number, ordinal = _pending(case)[0]
    with pytest.raises(AuthoringStorageConflict, match="claim"):
        case.synthesis.load_batch(case.connection, **_args(case), round_number=number, ordinal=ordinal, claim=None)
    with pytest.raises(sqlite3.IntegrityError, match="response_binding"), transaction(case.connection):
        case.connection.execute("""INSERT INTO publication_synthesis_receipt
            (run_id,round_number,ordinal,response_sha256,response_bytes,model_sha256,model_bytes,
             transport_sha256,transport_bytes,retention_status,completed_at)
            VALUES(?,?,?, ?,1,?,2,?,2,'present',?)""",
            (case.run_id, number, ordinal, "0" * 64, "0" * 64, "0" * 64, db_now_ms(case.connection)))
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["job", "attempt", "owner", "cancel", "expired"])
def test_response_prepared_by_one_claim_cannot_commit_after_its_claim_changes(synthesis_case, change):
    case = synthesis_case
    _start(case)
    prepared = _prepare_response(case, _pending(case)[0])
    if change in ("job", "attempt", "owner"):
        values = {"job": {"job_id": str(uuid4())}, "attempt": {"attempt_count": 2}, "owner": {"claim_owner": "other-worker"}}
        prepared = replace(prepared, claim=replace(prepared.claim, **values[change]))
    elif change == "cancel":
        with transaction(case.connection):
            JobRepository().request_cancel(case.connection, job_id=prepared.claim.job_id, now_ms=db_now_ms(case.connection))
    if change == "expired":
        expiry = case.connection.execute("SELECT claim_expires_at FROM durable_job WHERE id=?", (prepared.claim.job_id,)).fetchone()[0]
        with pytest.raises(AuthoringStorageConflict, match="claim"), transaction(case.connection):
            case.synthesis.save_receipt(case.connection, prepared=prepared, now_ms=expiry)
    else:
        with pytest.raises(AuthoringStorageConflict):
            _save(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 0


def test_receipt_is_not_replayed_until_the_bound_job_commits_success(synthesis_case):
    case = synthesis_case
    _start(case)
    prepared = _prepare_response(case, _pending(case)[0])
    with transaction(case.connection):
        case.synthesis.save_receipt(case.connection, prepared=prepared, now_ms=db_now_ms(case.connection))
    with pytest.raises(AuthoringStorageConflict, match="successful job"):
        _progress(case)
    # This negative deliberately omitted caller-owned job success. The actual
    # workflow must put effect, audit/outbox and success in one transaction.
    assert case.connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0


def test_one_batch_cannot_borrow_a_siblings_real_running_job(synthesis_case):
    case = synthesis_case
    _start(case)
    first, second = _pending(case)[:2]
    claim = _claim_batch(case.connection, case.synthesis, case.run_id, "actor", *first)
    with pytest.raises(AuthoringStorageConflict, match="exact batch"):
        case.synthesis.load_batch(case.connection, **_args(case), round_number=second[0], ordinal=second[1], claim=claim)
    with pytest.raises(sqlite3.IntegrityError, match="job_binding"), transaction(case.connection):
        case.synthesis.bind_batch_job(case.connection, **_args(case), round_number=second[0], ordinal=second[1], job_id=claim.job_id)
    assert case.connection.execute("SELECT durable_job_id FROM publication_synthesis_batch WHERE run_id=? AND round_number=? AND ordinal=?",
        (case.run_id, *second)).fetchone()[0] is None


def test_failed_effect_transaction_can_retry_without_a_partial_receipt(synthesis_case):
    case = synthesis_case
    _start(case)
    prepared = _prepare_response(case, _pending(case)[0])
    with pytest.raises(RuntimeError, match="caller rollback"), transaction(case.connection):
        case.synthesis.save_receipt(case.connection, prepared=prepared, now_ms=db_now_ms(case.connection))
        raise RuntimeError("caller rollback before success and audit")
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 0
    _save(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 1
    assert case.connection.execute("SELECT state FROM durable_job WHERE id=?", (prepared.claim.job_id,)).fetchone()[0] == "succeeded"


@pytest.mark.parametrize("change", ["missing", "extra", "version", "run", "round", "ordinal", "batch", "request", "response"])
def test_job_success_must_identify_the_exact_saved_reduction(synthesis_case, change):
    case = synthesis_case
    _start(case)
    prepared = _prepare_response(case, _pending(case)[0])
    result = reduction_job_result(run_id=prepared.run_id, ordinal=prepared.ordinal, receipt=prepared.receipt)
    mutations = {"version": ("schema_version", True), "run": ("run_id", str(uuid4())),
        "round": ("round_number", 999), "ordinal": ("ordinal", 999), "batch": ("batch_id", "B" + "0" * 24),
        "request": ("request_sha256", "0" * 64), "response": ("response_sha256", "0" * 64),
        "extra": ("not_part_of_contract", True)}
    if change == "missing":
        result = {}
    else:
        key, value = mutations[change]
        result[key] = value
    with pytest.raises(sqlite3.IntegrityError, match="job_result_binding"), transaction(case.connection):
        case.synthesis.save_receipt(case.connection, prepared=prepared, now_ms=db_now_ms(case.connection))
        JobRepository().succeed(case.connection, prepared.claim, result=result, now_ms=db_now_ms(case.connection))
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 0
    _save(case, prepared)
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_receipt").fetchone()[0] == 1


@pytest.mark.parametrize("synthesis_case", [{"raw": "# 짧은 문서\n완전한 단일 조건.", "chunk_bytes": 4096}], indirect=True)
def test_single_complete_map_is_the_only_zero_reduction_completion(synthesis_case):
    case = synthesis_case
    snapshot, prepared = _finish(case)
    assert prepared.completion[0:2] == ("single_map_no_reduction", 0)
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_wave").fetchone()[0] == 0
    assert case.connection.execute("SELECT count(*) FROM publication_synthesis_batch").fetchone()[0] == 0
    assert _persist(case, prepared) == snapshot


def test_fresh_process_reuses_stored_responses_and_saves_only_remaining_batches(synthesis_case):
    case = synthesis_case
    _start(case)
    first = _prepare_response(case, _pending(case)[0])
    _save(case, first)
    plan, maps = case.repo.load_completed_maps(case.connection, **_args(case))
    script = r'''
import json, sys
from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction, db_now_ms
from code_assist_v1.knowledge_core.migrations.synthesis_catalog import SYNTHESIS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.wiki_synthesis import WikiSynthesisRepository, reduction_job_result
from code_assist_v1.knowledge_core.wiki_synthesis import WikiReductionAuthor
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis_storage import _claim_batch
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository
factory = ConnectionFactory._for_isolated_tests(sys.argv[1], SYNTHESIS_MIGRATION_CATALOG)
connection, repo, calls = factory.open(), WikiSynthesisRepository(), []
run_id = sys.argv[2]
def args():
    return dict(run_id=run_id, actor_user_id="actor", now_ms=db_now_ms(connection))
def runner(task, payload, **kwargs):
    calls.append(payload["batch_id"])
    return _response(payload), _META
try:
    for _ in range(9):
        prepared = repo.prepare_progress(connection, **args())
        with transaction(connection):
            snapshot = repo.persist_progress(connection, prepared=prepared, now_ms=db_now_ms(connection))
        if snapshot:
            print(json.dumps({"snapshot": snapshot, "calls": calls}))
            break
        pending = connection.execute("""SELECT b.round_number,b.ordinal FROM publication_synthesis_batch b
            LEFT JOIN publication_synthesis_receipt r ON r.run_id=b.run_id
              AND r.round_number=b.round_number AND r.ordinal=b.ordinal
            WHERE b.run_id=? AND r.run_id IS NULL ORDER BY b.round_number,b.ordinal""", (run_id,)).fetchall()
        for number, ordinal in pending:
            claim = _claim_batch(connection, repo, run_id, "actor", number, ordinal)
            plan, wave, batch, policy = repo.load_batch(connection, **args(), round_number=number, ordinal=ordinal, claim=claim)
            result = WikiReductionAuthor(runner=runner).author(plan, batch, wave=wave, policy=policy)
            prepared = repo.prepare_receipt(connection, **args(), round_number=number, ordinal=ordinal, result=result, claim=claim)
            with transaction(connection):
                repo.save_receipt(connection, prepared=prepared, now_ms=db_now_ms(connection))
                JobRepository().succeed(connection, claim,
                    result=reduction_job_result(run_id=run_id, ordinal=ordinal, receipt=prepared.receipt), now_ms=db_now_ms(connection))
    else:
        raise AssertionError("did not finish")
finally:
    connection.close()
'''
    process = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(case.factory.database_path), case.run_id],
        cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, encoding="utf-8",
        timeout=60, check=False)
    assert process.returncode == 0, process.stderr
    value = json.loads(process.stdout.splitlines()[-1])
    assert first.receipt.batch_id not in value["calls"]
    rows = case.synthesis._receipts(case.connection, case.run_id)
    assert len(rows) == len(value["calls"]) + 1
    replay = resume_synthesis(plan, maps, rows, policy=case.policy, map_durable_attempts=2, reduction_durable_attempts=2)
    actual = case.connection.execute("SELECT content_md,manifest_json FROM authored_wiki_snapshot_body WHERE snapshot_id=?",
                                    (value["snapshot"],)).fetchone()
    assert tuple(actual) == (replay.wiki.markdown, replay.wiki.manifest_json)
