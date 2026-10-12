"""Real opt-in upload/extraction/worker pipeline; fake model, no live corpus."""
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import transaction, db_now_ms
from code_assist_v1.knowledge_core.migrations.wiki_upload_catalog import WIKI_UPLOAD_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository, HandlerKey
from code_assist_v1.knowledge_core.repositories.wiki_authoring import AuthoringStorageConflict
from code_assist_v1.knowledge_core.services.wiki_authoring import WikiAuthoringChunkWorkflow
from code_assist_v1.knowledge_core.services.wiki_synthesis import WikiSynthesisWorkflow
from code_assist_v1.knowledge_core.services.wiki_upload import WikiUploadWorkflow, WikiUploadPolicy
from code_assist_v1.knowledge_core.services.publication import (PublicationPreviewService,
    PublicationPreviewExtractionHandler, Utf8PublicationExtractor, CreateUploadPreviewCommand)
from code_assist_v1.knowledge_core.services.curation_worker import CanonicalCurationRescanHandler
from code_assist_v1.knowledge_core.wiki_authoring import WikiChunkAuthor, AuthoringModelPolicy
from code_assist_v1.knowledge_core.wiki_synthesis import WikiReductionAuthor, SynthesisPolicy
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
from code_assist_v1.knowledge_core.worker import PreparedJobEffect
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import _migrate
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import _Stager, _SourceLoader
from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import _epoch
from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _organization
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_workflow import _rows
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis_workflow import _claim, _run, _drain


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]
_SOURCE = "publication_wiki.plan_upload"
_EXTRACT = "publication_preview.extract"
_MAP = "publication_wiki.author_chunk"


def _wire(case, *, policy=None, failpoint=None):
    chunks = WikiAuthoringChunkWorkflow(case.factory, author=WikiChunkAuthor(runner=case.runner))
    synthesis = WikiSynthesisWorkflow(case.factory, chunks=chunks, author=WikiReductionAuthor(runner=case.runner))
    upload = WikiUploadWorkflow(case.factory, chunks=chunks, synthesis=synthesis, policy=policy or case.policy)
    kernel = DurableWorkKernel(case.factory, HandlerRegistry((upload.registered_handler(), chunks.registered_handler(),
        *synthesis.registered_handlers(), PublicationPreviewExtractionHandler(case.factory,
            _SourceLoader(case.raw, original_filename="source.md"), Utf8PublicationExtractor()).registered_handler(),
        CanonicalCurationRescanHandler(case.factory, scope_resolver=case.scope).registered_handler())),
        scope_resolver=case.scope, failpoint=failpoint)
    chunks.bind_kernel(kernel)
    synthesis.bind_kernel(kernel)
    upload.bind_kernel(kernel)
    case.chunks, case.synthesis, case.upload, case.kernel = chunks, synthesis, upload, kernel
    case.service = PublicationPreviewService(case.factory, case.scope, _Stager(), wiki_upload=upload)


def _case(factory, org, *, actor="member", target=None, policy=None, create=True, raw=None):
    raw = raw or ("# 복잡한 원본의 분할 검증\n" + "가상 조건과 수치를 함께 검토한다.\n" * 22
        + "마지막 부록: 수동 확인이 끝나기 전에는 재개하지 않는다.\n").encode()
    case = SimpleNamespace(factory=factory, scope=org.scope, org=org, actor=actor, raw=raw, calls=[],
        policy=policy or WikiUploadPolicy(chunk_bytes=512,
            model=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)),
            synthesis=SynthesisPolicy(input_bytes=7000, boundary_context_characters=20)))
    def runner(task, payload, **kwargs):
        case.calls.append((task, payload, kwargs))
        connection = factory.open()
        try:
            with transaction(connection):
                connection.execute("SELECT 1")
        finally:
            connection.close()
        text = "sharedsynthesis 실제 큐의 생성본 저장 검증. " + payload["source_sha256"][:12]
        if task == "canonical_wiki_synthesis":
            return _response(payload, text=text), _META
        return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "요지", "claims": [{"text": text,
                "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False), _META
    case.runner = runner
    _wire(case)
    case.command = CreateUploadPreviewCommand(request_id=str(uuid4()), operation_id=str(uuid4()),
        actor_user_id=actor, actor_scope_epoch=_epoch(factory, actor), content=raw,
        original_filename="source.md", media_type="text/markdown", title="자동 생성 검증", target=target)
    if create:
        case.preview = case.service.create_upload_preview(case.command)
    return case


@pytest.fixture
def upload_case(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    return _case(factory, _organization(factory))


@pytest.mark.parametrize("scope", ["personal", "part", "team", "responsibility"])
@pytest.mark.scenario("HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_actual_upload_automatically_generates_reduced_wiki_in_all_authority_contexts(isolated_knowledge_layout, scope):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    org = _organization(factory)
    actor = {"personal": "peer", "part": "member", "team": "leader", "responsibility": "executive"}[scope]
    target = getattr(org, scope) if scope in {"team", "responsibility"} else None
    case = _case(factory, org, actor=actor, target=target)
    assert _rows(case, "SELECT count(*) FROM publication_wiki_request") == [(1,)]
    assert case.kernel.claim_next(claim_owner="no-early", lease_ms=60000, handler_keys=(HandlerKey(_SOURCE, "v1"),)) is None
    assert not case.calls
    extraction = _run(case, _EXTRACT)
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    source = _run(case, _SOURCE)
    assert not case.calls  # Planning covers the source without any provider call.
    assert source.job.space_id == extraction.job.space_id
    if target:
        assert source.job.space_id == target.space_id
        assert _rows(case, "SELECT current_part_id FROM app_user WHERE id=?", (actor,)) == [(None,)]
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("planned",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk") == [(source.job.result["chunk_count"],)]
    _drain(case)
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("ready",)]
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(1,)]
    assert _rows(case, "SELECT content_md FROM extraction_snapshot_body") == [(case.raw.decode(),)]
    assert _drain(case) == []


def test_registrar_failure_rolls_back_whole_new_upload(upload_case):
    case = upload_case
    tables = ("source_artifact", "durable_job", "publication_preview", "publication_wiki_request", "audit_event", "outbox_event")
    before = {table: _rows(case, f"SELECT count(*) FROM {table}") for table in tables}
    class Failing:
        database_path = case.factory.database_path
        def register_upload(self, connection, **kwargs):
            case.upload.register_upload(connection, **kwargs)
            raise RuntimeError("isolated registrar failed")
    service = PublicationPreviewService(case.factory, case.scope, _Stager(), wiki_upload=Failing())
    with pytest.raises(RuntimeError, match="registrar failed"):
        service.create_upload_preview(replace(case.command, request_id=str(uuid4()), operation_id=str(uuid4())))
    assert {table: _rows(case, f"SELECT count(*) FROM {table}") for table in tables} == before


@pytest.mark.parametrize("legacy", [False, True])
def test_operation_replay_never_backfills_or_adopts_new_policy(isolated_knowledge_layout, legacy):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    case = _case(factory, _organization(factory), create=False)
    service = PublicationPreviewService(factory, case.scope, _Stager()) if legacy else case.service
    first = service.create_upload_preview(case.command)
    policy_before = _rows(case, "SELECT policy_json FROM publication_wiki_request")
    _wire(case, policy=WikiUploadPolicy(chunk_bytes=9000))
    assert case.service.create_upload_preview(case.command) == first
    assert _rows(case, "SELECT policy_json FROM publication_wiki_request") == policy_before
    assert len(policy_before) == (0 if legacy else 1)
    _drain(case)
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0 if legacy else 1,)]
    if not legacy:
        plan = json.loads(_rows(case, "SELECT plan_json FROM publication_authoring_run")[0][0])
        assert plan["chunk_bytes"] == 512
        assert plan["model_policy"]["provider_order"] == ["codex"]
        assert json.loads(_rows(case, "SELECT policy_json FROM publication_synthesis_intent")[0][0])["input_bytes"] == 7000


@pytest.mark.parametrize("stage", ["extraction", "source_queued", "source_claimed"])
def test_cancellation_before_run_has_terminal_disposition_without_fabricated_wiki(upload_case, stage):
    case = upload_case
    claim = None
    if stage != "extraction":
        _run(case, _EXTRACT)
        if stage == "source_claimed":
            claim = _claim(case, _SOURCE)
            job_id = claim.job.id
        else:
            assert case.kernel.claim_next(claim_owner="not-source", lease_ms=60000, handler_keys=(HandlerKey(_EXTRACT, "v1"),)) is None
            job_id = _rows(case, "SELECT durable_job_id FROM publication_wiki_source_plan")[0][0]
    else:
        job_id = case.preview.job_id
    case.kernel.request_cancel(job_id)
    if claim:
        assert case.kernel.run_claim(claim).job.state == "cancelled"
    case.kernel.recover_expired()
    assert case.kernel.claim_next(claim_owner="terminal", lease_ms=60000) is None
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(0 if stage == "extraction" else 1,)]
    assert not case.calls


def test_complete_source_over_work_budget_fails_without_prefix_success(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    base = WikiUploadPolicy()
    case = _case(factory, _organization(factory), policy=replace(base, work=replace(base.work, source_bytes=20)))
    _run(case, _EXTRACT)
    claim = _claim(case, _SOURCE)
    assert case.kernel.run_claim(claim).job.state == "failed"
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT content_md FROM extraction_snapshot_body") == [(case.raw.decode(),)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert not case.calls


@pytest.mark.parametrize("mutation", ["hash", "run", "extra", "boolean", "count"])
def test_job_success_requires_exact_source_plan_and_rolls_back_all_children(upload_case, mutation):
    case = upload_case
    _run(case, _EXTRACT)
    claim = _claim(case, _SOURCE)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.upload.prepare(started)
    changed = dict(prepared.result)
    if mutation == "hash": changed["plan_sha256"] = "0" * 64
    elif mutation == "run": changed["run_id"] = str(uuid4())
    elif mutation == "extra": changed["extra"] = "unbound"
    elif mutation == "boolean": changed["schema_version"] = True
    else: changed["chunk_count"] += 1
    connection = case.factory.open()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="exact committed effect"):
            with transaction(connection):
                case.upload.commit_effect(connection, started, prepared)
                JobRepository().succeed(connection, fence=claim.fence, result=changed, now_ms=db_now_ms(connection))
    finally:
        connection.close()
    for table in ("publication_authoring_run", "publication_authoring_chunk", "publication_synthesis_intent", "publication_wiki_source_result"):
        assert _rows(case, f"SELECT count(*) FROM {table}") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type=?", (_MAP,)) == [(0,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.upload.planned'") == [(0,)]
    assert case.kernel.commit_prepared(claim.fence, prepared).job.state == "succeeded"
    _drain(case)


def test_source_planner_lost_ack_replays_without_duplicate_map_jobs(upload_case):
    case = upload_case
    _run(case, _EXTRACT)
    claim = _claim(case, _SOURCE)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.upload.prepare(started)
    def failpoint(name):
        if name == "job.after_domain_commit_before_ack":
            raise RuntimeError("isolated lost upload ack")
    _wire(case, failpoint=failpoint)
    with pytest.raises(RuntimeError, match="lost upload ack"):
        case.kernel.commit_prepared(claim.fence, prepared)
    count = _rows(case, "SELECT count(*) FROM publication_authoring_chunk")
    _wire(case)
    assert case.kernel.commit_prepared(claim.fence, prepared).replayed
    assert _rows(case, "SELECT count(*) FROM publication_authoring_chunk") == count
    assert _rows(case, "SELECT count(*) FROM publication_wiki_source_result") == [(1,)]
    assert not case.calls
    _drain(case)


def test_partial_map_enqueue_rolls_back_run_intent_children_and_audit(upload_case, monkeypatch):
    case = upload_case
    _run(case, _EXTRACT)
    claim = _claim(case, _SOURCE)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.upload.prepare(started)
    original = case.chunks._repository.bind_chunk_job
    def fail(connection, **kwargs):
        original(connection, **kwargs)
        if kwargs["ordinal"] == 2:
            raise RuntimeError("isolated second map bind failure")
    monkeypatch.setattr(case.chunks._repository, "bind_chunk_job", fail)
    with pytest.raises(RuntimeError, match="second map bind failure"):
        case.kernel.commit_prepared(claim.fence, prepared)
    for table in ("publication_authoring_run", "publication_authoring_chunk", "publication_synthesis_intent", "publication_wiki_source_result"):
        assert _rows(case, f"SELECT count(*) FROM {table}") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type=?", (_MAP,)) == [(0,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action IN ('wiki.upload.planned','wiki.authoring.queued')") == [(0,)]
    monkeypatch.setattr(case.chunks._repository, "bind_chunk_job", original)
    assert case.kernel.commit_prepared(claim.fence, prepared).job.state == "succeeded"
    _drain(case)


def test_server_policy_cannot_be_replaced_by_another_valid_prepared_plan(upload_case):
    case = upload_case
    _run(case, _EXTRACT)
    claim = _claim(case, _SOURCE)
    started = case.kernel._start_claim(claim.fence)
    foreign_policy = case.chunks.prepare_upload(preview_id=case.preview.preview_id, actor_user_id=case.actor,
        chunk_bytes=9000, model_policy=case.policy.model, work_policy=case.policy.work)
    connection = case.factory.open(read_only=True)
    try:
        request, _ = case.upload._request(connection, case.preview.preview_id)
    finally:
        connection.close()
    wrong = PreparedJobEffect(case.upload._result(foreign_policy, request), foreign_policy)
    result = case.kernel.commit_prepared(claim.fence, wrong)
    assert result.job.state == "failed", result.job.error
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert not case.calls


@pytest.mark.parametrize("kind", ["team", "responsibility"])
@pytest.mark.parametrize("phase", ["before_binding", "before_body", "after_plan", "commit"])
@pytest.mark.scenario("HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_real_native_role_end_prevents_initial_plan_at_each_boundary(isolated_knowledge_layout, monkeypatch, kind, phase):
    from code_assist_v1.knowledge_core.services.higher_authority import SetHigherRoleCommand
    from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
    from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import _higher_service
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    org = _organization(factory)
    actor, target = ("leader", org.team) if kind == "team" else ("executive", org.responsibility)
    case = _case(factory, org, actor=actor, target=target)
    _run(case, _EXTRACT)
    def revoke():
        connection = factory.open(read_only=True)
        try:
            role_name = "team_leader" if kind == "team" else "responsibility_executive"
            role = HigherSpaceRepository().get_open_role(connection, org_unit_id=target.space_id, role=role_name)
            version = connection.execute("SELECT row_version FROM knowledge_space WHERE id=?", (target.space_id,)).fetchone()[0]
        finally:
            connection.close()
        _higher_service(factory).set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=str(uuid4()), request_id=str(uuid4()), org_unit_id=target.space_id, role=role_name,
            assignee_user_id=None, expected_current_assignment_id=role.id,
            expected_current_assignment_generation=role.assignment_generation,
            expected_current_assignment_row_version=role.row_version, expected_space_row_version=version,
            enable_writes=False, reason="isolated automatic upload authority check"))
    if phase == "before_binding":
        revoke()
        assert case.kernel.claim_next(claim_owner="revoked", lease_ms=60000, handler_keys=(HandlerKey(_SOURCE, "v1"),)) is None
    else:
        claim = _claim(case, _SOURCE)
        if phase == "before_body":
            revoke()
            monkeypatch.setattr(case.chunks, "prepare_upload", lambda **kw: pytest.fail("revoked source read"))
        elif phase == "after_plan":
            original = case.chunks.prepare_upload
            def plan(**kwargs):
                value = original(**kwargs)
                revoke()
                return value
            monkeypatch.setattr(case.chunks, "prepare_upload", plan)
        if phase == "commit":
            started = case.kernel._start_claim(claim.fence)
            value = case.upload.prepare(started)
            revoke()
            result = case.kernel.commit_prepared(claim.fence, value)
        else:
            result = case.kernel.run_claim(claim)
        assert result.job.state in {"failed", "cancelled"}, result.job.error
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert _rows(case, "SELECT content_md FROM extraction_snapshot_body") == [(case.raw.decode(),)]
    assert not case.calls


def test_registered_preview_cannot_skip_source_planner(upload_case):
    case = upload_case
    _run(case, _EXTRACT)
    prepared = case.chunks.prepare_upload(preview_id=case.preview.preview_id, actor_user_id=case.actor,
        chunk_bytes=case.policy.chunk_bytes, model_policy=case.policy.model, work_policy=case.policy.work)
    with pytest.raises(sqlite3.IntegrityError, match="frozen running planner"):
        case.chunks.enqueue_upload(prepared)
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    _drain(case)


@pytest.mark.parametrize("component", ["map", "synthesis"])
def test_old_request_rejects_unversioned_attempt_ceiling_drift(upload_case, monkeypatch, component):
    from code_assist_v1.knowledge_core.services import wiki_authoring, wiki_synthesis
    case = upload_case
    before = _rows(case, "SELECT policy_json,policy_sha256 FROM publication_wiki_request")
    _run(case, _EXTRACT)
    if component == "map":
        monkeypatch.setattr(wiki_authoring, "_DURABLE_ATTEMPTS", 3)
    else:
        monkeypatch.setattr(wiki_synthesis, "_ATTEMPTS", 3)
    assert case.kernel.claim_next(claim_owner="policy-drift", lease_ms=60000, handler_keys=(HandlerKey(_SOURCE, "v1"),)) is None
    assert _rows(case, "SELECT policy_json,policy_sha256 FROM publication_wiki_request") == before
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]
    assert not case.calls


@pytest.mark.parametrize("extracted", [False, True])
def test_preview_cancel_service_converges_initial_request(upload_case, extracted):
    from code_assist_v1.knowledge_core.services.publication import CancelPublicationPreviewCommand
    case = upload_case
    if extracted:
        _run(case, _EXTRACT)
    case.service.cancel_preview(CancelPublicationPreviewCommand(request_id=str(uuid4()), operation_id=str(uuid4()),
        actor_user_id=case.actor, actor_scope_epoch=_epoch(case.factory, case.actor), preview_id=case.preview.preview_id))
    assert case.kernel.claim_next(claim_owner="cancelled-preview", lease_ms=60000) is None
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert not case.calls


def test_source_planner_retry_exhaustion_retains_dead_state_and_error(upload_case, monkeypatch):
    from code_assist_v1.knowledge_core.worker import RetryableJobExecutionError
    case = upload_case
    _run(case, _EXTRACT)
    def fail(**kwargs):
        raise RetryableJobExecutionError("isolated planner transient failure")
    monkeypatch.setattr(case.chunks, "prepare_upload", fail)
    first = case.kernel.run_claim(_claim(case, _SOURCE))
    assert first.job.state == "retry"
    second = case.kernel.run_claim(_claim(case, _SOURCE))
    assert second.job.state == "dead", second.job.error
    assert second.job.error and not second.job.cancel_requested
    assert _rows(case, "SELECT outcome FROM publication_wiki_source_result") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(0,)]


@pytest.mark.scenario("UPLOAD-009", "UPLOAD-010", "UPLOAD-011", "HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_multiple_authors_queue_all_modes_then_publish_into_exact_shared_views(isolated_knowledge_layout):
    from code_assist_v1.knowledge_core.services.publication import PublicationService
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _command, _release_and_check_search
    from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _hits
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    org = _organization(factory)
    jobs = []
    matrix = [(actor, mode, None) for actor in ("member", "peer") for mode in ("personal", "part", "both")]
    matrix += [(actor, mode, target if mode == "explicit" else None)
        for actor, target in (("leader", org.team), ("executive", org.responsibility))
        for mode in ("personal", "explicit")]
    for index, (actor, mode, target) in enumerate(matrix):
        raw = (f"# 독립 업로드 {actor} {mode} {index}\n" + "조건과 예외를 함께 보존한다.\n" * 22).encode()
        case = _case(factory, org, actor=actor, target=target, raw=raw)
        _run(case, _EXTRACT)
        jobs.append((case, mode, target))
        assert not case.calls
    # Ten completed extractions coexist before any source/map/reduce job runs.
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(10,)]
    _drain(case)
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run WHERE status='ready'") == [(10,)]
    private = {actor: set() for actor in ("member", "peer", "leader", "executive")}
    part, team, responsibility = set(), set(), set()
    for item_case, mode, target in jobs:
        preview_id = item_case.preview.preview_id
        snapshot, body = _rows(case, """SELECT s.id,b.content_md FROM authored_wiki_snapshot s
            JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
            JOIN publication_authoring_run r ON r.id=s.run_id WHERE r.preview_id=?""", (preview_id,))[0]
        preview = SimpleNamespace(preview_id=preview_id, source_snapshot_sha256=sha256(item_case.raw).hexdigest())
        result = PublicationService(factory, org.scope).publish(_command(preview, actor=item_case.actor,
            epoch=_epoch(factory, item_case.actor), mode=mode, target=target, snapshot=snapshot))
        _release_and_check_search(factory, org.scope, result, SimpleNamespace(markdown=body))
        for item in result.targets:
            if item["space_id"] == org.ids["part_space_a"]: part.add(item["item_id"])
            elif item["space_id"] == org.team.space_id: team.add(item["item_id"])
            elif item["space_id"] == org.responsibility.space_id: responsibility.add(item["item_id"])
            else: private[item_case.actor].add(item["item_id"])
    assert len(part) == 4 and len(team) == len(responsibility) == 1
    assert _rows(case, "SELECT count(*) FROM knowledge_item") == [(12,)]
    team |= part
    responsibility |= team
    for actor in ("member", "peer"):
        assert _hits(factory, org.scope, actor=actor, space=org.ids["part_space_a"]) == part
        assert _hits(factory, org.scope, actor=actor, space=org.team.space_id) == team
        assert _hits(factory, org.scope, actor=actor, space=org.responsibility.space_id) == responsibility
    assert _hits(factory, org.scope, actor="leader", space=org.team.space_id) == team
    assert _hits(factory, org.scope, actor="executive", space=org.responsibility.space_id) == responsibility
    for actor in private:
        assert _hits(factory, org.scope, actor=actor, space=org.personal[actor]) == private[actor]
        assert private[actor].isdisjoint(responsibility)


def test_two_fresh_processes_schedule_and_execute_one_source_plan(upload_case):
    case = upload_case
    _run(case, _EXTRACT)
    script = r'''
import json, sys
from types import SimpleNamespace
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.wiki_upload_catalog import WIKI_UPLOAD_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.services.wiki_upload import WikiUploadPolicy
from code_assist_v1.knowledge_core.repositories.jobs import HandlerKey
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_upload_workflow import _wire
def no_model(*a, **k):
    raise AssertionError('source planner called a model')
case=SimpleNamespace(factory=ConnectionFactory._for_isolated_tests(sys.argv[1], WIKI_UPLOAD_MIGRATION_CATALOG),
    scope=ScopeResolver(FeatureFlags(core_enabled=True,personal_reads=True,personal_writes=True,
        part_reads=True,part_writes=True,higher_spaces=True,team_writes=True,responsibility_writes=True)),
    raw=b'',runner=no_model,policy=WikiUploadPolicy())
_wire(case)
claim=case.kernel.claim_next(claim_owner=sys.argv[2],lease_ms=60000,
    handler_keys=(HandlerKey('publication_wiki.plan_upload','v1'),))
if claim is None:
    print(json.dumps(None))
else:
    result=case.kernel.run_claim(claim)
    assert result.job.state=='succeeded', result.job.error
    print(json.dumps(result.job.id))
'''
    processes = [subprocess.Popen([sys.executable, "-E", "-B", "-c", script,
        str(case.factory.database_path), f"source-competitor-{index}"],
        cwd=str(Path(__file__).resolve().parents[4]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for index in range(2)]
    results = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode == 0, stdout + stderr
            results.append(json.loads(stdout.splitlines()[-1]))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    assert sum(value is not None for value in results) == 1
    assert _rows(case, "SELECT count(*) FROM publication_wiki_source_plan") == [(1,)]
    assert _rows(case, "SELECT count(*) FROM publication_wiki_source_result WHERE outcome='planned'") == [(1,)]
    assert _rows(case, "SELECT count(*) FROM publication_authoring_run") == [(1,)]
    assert json.loads(_rows(case, "SELECT plan_json FROM publication_authoring_run")[0][0])["chunk_bytes"] == 512
    _drain(case)
