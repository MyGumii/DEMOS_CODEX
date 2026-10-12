"""Actual isolated durable jobs with a fake model, never semantic-quality proof."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import transaction, db_now_ms
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository
from code_assist_v1.knowledge_core.repositories.users import UserRepository
from code_assist_v1.knowledge_core.services.publication import PublicationPreviewExtractionHandler, Utf8PublicationExtractor
from code_assist_v1.knowledge_core.services.curation_worker import CanonicalCurationRescanHandler
from code_assist_v1.knowledge_core.services.wiki_authoring import WikiAuthoringChunkWorkflow
from code_assist_v1.knowledge_core.wiki_authoring import WikiChunkAuthor, AuthoringModelPolicy, AuthoringWorkPolicy, AuthoringWorkLimitError
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _part_fixture, _uploaded_preview
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import _SourceLoader


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]


def _kernel(case, *, failpoint=None):
    workflow = WikiAuthoringChunkWorkflow(case.factory, author=WikiChunkAuthor(runner=case.runner))
    kernel = DurableWorkKernel(case.factory, HandlerRegistry((workflow.registered_handler(),
        PublicationPreviewExtractionHandler(case.factory, _SourceLoader(case.raw), Utf8PublicationExtractor()).registered_handler(),
        CanonicalCurationRescanHandler(case.factory, scope_resolver=case.scope).registered_handler())),
        scope_resolver=case.scope, failpoint=failpoint)
    workflow.bind_kernel(kernel)
    return workflow, kernel


@pytest.fixture
def workflow_case(isolated_knowledge_layout):
    factory, scope, part, part_space, epoch = _part_fixture(isolated_knowledge_layout)
    raw = ("# 길고 분리된 원본\n" + "장비 복구의 조건과 수치를 보존한다.\n" * 35
           + "부록: 교체 직후에는 자동 실행을 허용하지 않는다.\n").encode()
    preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    calls = []
    case = SimpleNamespace(factory=factory, scope=scope, raw=raw, preview=preview,
        calls=calls, before_model=None, failures=0, part=part, part_space=part_space)
    def runner(task, payload, **kwargs):
        calls.append(payload["chunk_id"])
        if case.before_model:
            case.before_model()
        if case.failures:
            case.failures -= 1
            raise RuntimeError("isolated provider outage")
        return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "조각 기록", "claims": [{"text": "저장 검증용 생성문",
                "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False), {
                    "model_kind": "codex", "model_id": "fixture"}
    case.runner = runner
    case.workflow, case.kernel = _kernel(case)
    case.prepared = case.workflow.prepare_upload(preview_id=preview.preview_id, actor_user_id="actor",
        chunk_bytes=1024, model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
    return case


def _claim(case):
    _, claim = case.kernel.recover_and_claim(claim_owner="authoring-test", lease_ms=30_000)
    assert claim is not None
    assert claim.job.job_type == "publication_wiki.author_chunk"
    return claim


def _rows(case, sql, params=()):
    connection = case.factory.open(read_only=True)
    try:
        return [tuple(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def test_upload_chunks_have_one_job_one_effect_and_exact_enqueue_replay(workflow_case):
    case = workflow_case
    run_id, jobs = case.workflow.enqueue_upload(case.prepared)
    assert len(jobs) > 1 and not case.calls
    assert case.workflow.enqueue_upload(case.prepared) == (run_id, jobs)
    for expected_calls in range(1, len(jobs) + 1):
        result = case.kernel.run_claim(_claim(case))
        assert result.job.state == "succeeded", result.job.error
        assert len(case.calls) == expected_calls
        assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(expected_calls,)]
    assert len(set(case.calls)) == len(jobs)
    assert _rows(case, "SELECT status FROM publication_preview") == [("ready",)]
    # Map chunks are not silently advertised as a final reduced Wiki.
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(0,)]
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("pending",)]


def test_queued_cancel_converges_run_and_siblings_without_model(workflow_case):
    case = workflow_case
    _, jobs = case.workflow.enqueue_upload(case.prepared)
    case.kernel.request_cancel(jobs[0])
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert _rows(case, "SELECT DISTINCT state FROM durable_job WHERE job_type='publication_wiki.author_chunk'") == [("cancelled",)]
    assert _rows(case, "SELECT status FROM publication_preview") == [("ready",)]
    assert not case.calls


def test_external_queued_cancellation_is_reconciled_without_claim_or_model(workflow_case):
    case = workflow_case
    _, jobs = case.workflow.enqueue_upload(case.prepared)
    connection = case.factory.open()
    try:
        with transaction(connection):
            for job_id in jobs:
                JobRepository().request_cancel(connection, job_id=job_id, now_ms=db_now_ms(connection))
    finally:
        connection.close()
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("pending",)]
    recovery, claim = case.kernel.recover_and_claim(claim_owner="cleanup", lease_ms=30_000)
    assert claim is None and not case.calls
    assert _rows(case, "SELECT status,failure_code FROM publication_authoring_run") == [("extraction_only", "authoring_incomplete")]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.authoring.extraction_only'") == [(1,)]
    case.kernel.recover_expired()
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.authoring.extraction_only'") == [(1,)]


def test_explicit_extraction_publication_cancels_unneeded_chunk_work(workflow_case):
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _command
    from code_assist_v1.knowledge_core.services.publication import PublicationService
    case = workflow_case
    case.workflow.enqueue_upload(case.prepared)
    epoch = _rows(case, "SELECT scope_epoch FROM app_user WHERE id='actor'")[0][0]
    PublicationService(case.factory, case.scope).publish(_command(case.preview, epoch=epoch, mode="personal"))
    case.kernel.recover_expired()
    assert _rows(case, "SELECT status,failure_code FROM publication_authoring_run") == [("extraction_only", "extraction_selected")]
    assert _rows(case, "SELECT DISTINCT state FROM durable_job WHERE job_type='publication_wiki.author_chunk'") == [("cancelled",)]
    assert not case.calls
    case.kernel.recover_expired()
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.authoring.extraction_only'") == [(0,)]


def test_provider_retry_uses_next_attempt_without_reprocessing_successful_chunks(workflow_case):
    case = workflow_case
    _, jobs = case.workflow.enqueue_upload(case.prepared)
    completed = case.kernel.run_claim(_claim(case)).job
    assert completed.state == "succeeded"
    case.failures = 1
    failure = case.kernel.run_claim(_claim(case)).job
    assert failure.state == "retry", failure.error
    for _ in range(len(jobs) - 1):
        result = case.kernel.run_claim(_claim(case))
        assert result.job.state == "succeeded", result.job.error
    assert case.calls.count(completed.input_ref["chunk_id"]) == 1
    assert case.calls.count(failure.input_ref["chunk_id"]) == 2
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(len(jobs),)]


def test_revoked_actor_before_model_makes_zero_calls_and_converges(workflow_case):
    case = workflow_case
    case.workflow.enqueue_upload(case.prepared)
    claim = _claim(case)
    connection = case.factory.open()
    try:
        with transaction(connection):
            connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id='actor'")
    finally:
        connection.close()
    result = case.kernel.run_claim(claim)
    assert result.job.state == "failed", result.job.error
    assert not case.calls
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]


def test_cancel_during_provider_call_keeps_extraction_and_rejects_result(workflow_case):
    case = workflow_case
    case.workflow.enqueue_upload(case.prepared)
    claim = _claim(case)
    case.before_model = lambda: case.kernel.request_cancel(claim.job.id)
    result = case.kernel.run_claim(claim)
    assert result.job.state == "cancelled", result.job.error
    assert len(case.calls) == 1
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(0,)]
    assert _rows(case, "SELECT status FROM publication_preview") == [("ready",)]


def test_retry_product_is_admitted_before_any_run_or_job(workflow_case):
    case = workflow_case
    with pytest.raises(AuthoringWorkLimitError, match="Durable retries"):
        case.workflow.prepare_upload(preview_id=case.preview.preview_id, actor_user_id="actor", chunk_bytes=1024,
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)),
            work_policy=AuthoringWorkPolicy(model_attempts=case.prepared.reserved_work[0]))
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert not case.calls


def test_lost_ack_replays_committed_chunk_without_a_second_model_call(workflow_case):
    case = workflow_case
    case.workflow.enqueue_upload(case.prepared)
    claim = _claim(case)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.workflow.prepare(started)
    class LostAck(RuntimeError):
        pass
    def failpoint(name):
        if name == "job.after_domain_commit_before_ack":
            raise LostAck()
    _, failing_kernel = _kernel(case, failpoint=failpoint)
    with pytest.raises(LostAck):
        failing_kernel.commit_prepared(claim.fence, prepared)
    replay = case.kernel.commit_prepared(claim.fence, prepared)
    assert replay.replayed and replay.job.state == "succeeded"
    assert len(case.calls) == 1
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(1,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.authoring.chunk_completed'") == [(1,)]


def test_provider_exhaustion_cancels_siblings_and_keeps_exact_extraction(workflow_case):
    case = workflow_case
    case.workflow.enqueue_upload(case.prepared)
    case.failures = 100
    for _ in range(8):
        result = case.kernel.run_claim(_claim(case))
        if result.job.state == "dead":
            break
    else:
        pytest.fail("The frozen durable attempt allowance did not terminate.")
    assert set(
        _rows(case, "SELECT DISTINCT state FROM durable_job WHERE job_type='publication_wiki.author_chunk'")) == {("dead",), ("cancelled",)}
    assert _rows(case, "SELECT content_md FROM extraction_snapshot_body") == [(case.raw.decode(),)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.authoring.extraction_only'") == [(1,)]


@pytest.mark.parametrize("crash_stage", ["claimed", "after_response"])
def test_fresh_interpreter_recovers_claim_and_preserves_completed_chunk(workflow_case, crash_stage):
    case = workflow_case
    _, jobs = case.workflow.enqueue_upload(case.prepared)
    first = case.kernel.run_claim(_claim(case)).job
    assert first.state == "succeeded"
    _, interrupted = case.kernel.recover_and_claim(claim_owner="dead-process",
        lease_ms=1500 if crash_stage == "after_response" else 10)
    assert interrupted is not None
    if crash_stage == "after_response":
        started = case.kernel._start_claim(interrupted.fence)
        lost_response = case.workflow.prepare(started)
        assert lost_response.result["ordinal"] == interrupted.job.input_ref["ordinal"]
        assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(1,)]
        remaining_ms = interrupted.job.claim_expires_at_ms - _rows(case, "SELECT CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)")[0][0]
        time.sleep(max(0, remaining_ms) / 1000 + 0.02)
    # A genuinely fresh interpreter reconstructs the registry, frozen plan and
    # new preparation seal. It never receives the parent's process-local DTO.
    script = r'''
import json, sys
from types import SimpleNamespace
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.authoring_catalog import AUTHORING_MIGRATION_CATALOG
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_workflow import _kernel
factory=ConnectionFactory._for_isolated_tests(sys.argv[1], AUTHORING_MIGRATION_CATALOG)
scope=ScopeResolver(FeatureFlags(core_enabled=True,personal_reads=True,personal_writes=True,part_reads=True,part_writes=True))
calls=[]
def runner(task,payload,**kwargs):
    calls.append(payload['chunk_id'])
    return json.dumps({'schema_version':1,'chunk_id':payload['chunk_id'],'sections':[{'heading':'restart','claims':[{'text':'restored record','quotes':[payload['source']]}]}],'warnings':[]}), {'model_kind':'codex','model_id':'fixture'}
case=SimpleNamespace(factory=factory,scope=scope,raw=b'',runner=runner)
workflow,kernel=_kernel(case)
states=[]
while True:
    recovery,claim=kernel.recover_and_claim(claim_owner='fresh-process',lease_ms=30000)
    if claim is None: break
    result=kernel.run_claim(claim)
    assert result.job.state=='succeeded',result.job.error
    states.append((result.job.id,result.job.attempt_count))
print(json.dumps({'calls':calls,'states':states}))
'''
    completed = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(case.factory.database_path)],
        cwd=str(Path(__file__).resolve().parents[4]), capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.splitlines()[-1])
    assert len(report["calls"]) == len(jobs) - 1
    assert first.input_ref["chunk_id"] not in report["calls"]
    assert [interrupted.job.id, 2] in report["states"]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(len(jobs),)]
    assert _rows(case, "SELECT input_sha256 FROM durable_job WHERE id=?", (interrupted.job.id,)) == [(interrupted.job.input_sha256,)]
    if crash_stage == "after_response":
        assert case.calls.count(interrupted.job.input_ref["chunk_id"]) == 1
        assert report["calls"].count(interrupted.job.input_ref["chunk_id"]) == 1


def test_enqueue_failure_rolls_back_run_bindings_jobs_and_events(workflow_case, monkeypatch):
    case = workflow_case
    original = case.workflow._repository.bind_chunk_job
    def fail_second(connection, **kwargs):
        original(connection, **kwargs)
        if kwargs["ordinal"] == 2:
            raise RuntimeError("isolated second binding failure")
    monkeypatch.setattr(case.workflow._repository, "bind_chunk_job", fail_second)
    with pytest.raises(RuntimeError, match="second binding"):
        case.workflow.enqueue_upload(case.prepared)
    for table in ("publication_authoring_run", "publication_authoring_chunk"):
        assert _rows(case, f"SELECT count(*) FROM {table}") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type='publication_wiki.author_chunk'") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action LIKE 'wiki.authoring.%'") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM outbox_event WHERE event_type LIKE 'wiki.authoring.%'") == [(0,)]


@pytest.mark.parametrize("kind", ["team", "responsibility"])
@pytest.mark.parametrize("revoke", [False, True])
def test_direct_higher_role_has_no_part_and_is_rechecked_before_model(isolated_knowledge_layout, kind, revoke, monkeypatch):
    from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import _migrate
    from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import (
        _seed, _higher_service, _role_command, _descriptor, _epoch, _scope)
    from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
    from code_assist_v1.knowledge_core.repositories.publication_authority import HigherPublicationTargetDescriptor
    from code_assist_v1.knowledge_core.services.higher_authority import SetHigherRoleCommand
    from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
    factory, _ = _migrate(isolated_knowledge_layout)
    ids = _seed(factory)
    higher = _higher_service(factory)
    if kind == "team":
        assigned = higher.set_role(_role_command(factory, ids, operation="chunk-team-role", assignee="leader"))
        target = _descriptor(factory, ids)
        org_id, role_name = ids["team_a"], "team_leader"
    else:
        org_id, role_name = RESPONSIBILITY_ROOT_ID, "responsibility_executive"
        assigned = higher.set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=str(uuid4()), request_id=str(uuid4()), org_unit_id=org_id, role=role_name,
            assignee_user_id="leader", expected_current_assignment_id=None,
            expected_current_assignment_generation=None, expected_current_assignment_row_version=None,
            expected_space_row_version=1, enable_writes=True, reason="isolated chunk workflow"))
        target = HigherPublicationTargetDescriptor(space_id=org_id, kind=kind, capability="publish",
            role_assignment_id=assigned.assignment_id, role_assignment_generation=assigned.assignment_generation)
    scope = _scope()
    raw = ("# 상위 조직의 업무\n" + "조건, 수치, 예외는 서로 다른 구간에 있다.\n" * 24).encode()
    preview = _uploaded_preview(factory, scope, actor="leader", epoch=_epoch(factory, "leader"), raw=raw, target=target)
    calls = []
    def runner(task, payload, **kwargs):
        calls.append(payload["chunk_id"])
        return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"], "sections": [{"heading": "higher",
            "claims": [{"text": "isolated higher record", "quotes": [payload["source"]]}]}], "warnings": []}), {
                "model_kind": "codex", "model_id": "fixture"}
    case = SimpleNamespace(factory=factory, scope=scope, raw=raw, runner=runner)
    workflow, case.kernel = _kernel(case)
    prepared = workflow.prepare_upload(preview_id=preview.preview_id, actor_user_id="leader", chunk_bytes=1024,
        model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
    _, jobs = workflow.enqueue_upload(prepared)
    assert _rows(case, "SELECT current_part_id FROM app_user WHERE id='leader'") == [(None,)]
    assert _rows(case, "SELECT DISTINCT space_id FROM durable_job WHERE job_type='publication_wiki.author_chunk'") == [(target.space_id,)]
    if revoke:
        original = workflow._repository.load_plan
        def revoke_after_source_read(connection, **kwargs):
            plan = original(connection, **kwargs)
            higher.set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
                operation_id=str(uuid4()), request_id=str(uuid4()), org_unit_id=org_id, role=role_name,
                assignee_user_id=None, expected_current_assignment_id=assigned.assignment_id,
                expected_current_assignment_generation=assigned.assignment_generation,
                expected_current_assignment_row_version=assigned.assignment_row_version,
                expected_space_row_version=assigned.space_row_version, enable_writes=False, reason="isolated role end"))
            return plan
        monkeypatch.setattr(workflow._repository, "load_plan", revoke_after_source_read)
        result = case.kernel.run_claim(_claim(case))
        assert result.job.state == "cancelled", result.job.error
        assert not calls
        assert _rows(case, "SELECT status,failure_code FROM publication_authoring_run") == [("extraction_only", "authoring_incomplete")]
    else:
        for _ in jobs:
            result = case.kernel.run_claim(_claim(case))
            assert result.job.state == "succeeded", result.job.error
        assert len(calls) == len(jobs)


def test_two_part_authors_continue_when_another_document_changes_the_corpus(isolated_knowledge_layout):
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _add_cowriter, _command
    from code_assist_v1.knowledge_core.services.publication import PublicationService
    factory, scope, part, _, epoch = _part_fixture(isolated_knowledge_layout)
    second_epoch = _add_cowriter(factory, part)
    first_raw = ("# 설비\n" + "설비 상태를 점검한다.\n" * 35).encode()
    second_raw = ("# 조리\n" + "조리 재료의 조건을 기록한다.\n" * 35).encode()
    first = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=first_raw)
    second = _uploaded_preview(factory, scope, actor="cowriter", epoch=second_epoch, raw=second_raw)
    changing = _uploaded_preview(factory, scope, actor="cowriter", epoch=second_epoch, raw=b"# Change\nNew independent shared knowledge.")
    calls = []
    def runner(task, payload, **kwargs):
        calls.append(payload["source"])
        return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"], "sections": [{"heading": "shared",
            "claims": [{"text": "shared record", "quotes": [payload["source"]]}]}], "warnings": []}), {
                "model_kind": "codex", "model_id": "fixture"}
    case = SimpleNamespace(factory=factory, scope=scope, raw=b"", runner=runner)
    workflow, case.kernel = _kernel(case)
    jobs = []
    for preview, actor in ((first, "actor"), (second, "cowriter")):
        prepared = workflow.prepare_upload(preview_id=preview.preview_id, actor_user_id=actor, chunk_bytes=512,
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
        jobs.extend(workflow.enqueue_upload(prepared)[1])
    PublicationService(factory, scope).publish(_command(changing, actor="cowriter", epoch=second_epoch, mode="part"))
    for _ in range(len(jobs) + 2):
        _, claim = case.kernel.recover_and_claim(claim_owner="shared-authors", lease_ms=30_000)
        if claim is None:
            break
        result = case.kernel.run_claim(claim)
        assert result.job.state == "succeeded", result.job.error
    assert len(calls) == len(jobs)
    assert any("설비" in source for source in calls) and any("조리" in source for source in calls)
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk_body") == [(len(jobs),)]
    assert _rows(case, "SELECT count(DISTINCT actor_user_id) FROM publication_authoring_run") == [(2,)]
