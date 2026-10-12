"""Real durable kernel orchestration with fake prose; no live/model-quality claim."""
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
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.synthesis_workflow_catalog import SYNTHESIS_WORKFLOW_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository, JobEnqueueDraft, HandlerKey, ClaimFence, ClaimResult
from code_assist_v1.knowledge_core.repositories.wiki_authoring import WikiAuthoringRepository, AuthoringStorageConflict
from code_assist_v1.knowledge_core.services.wiki_authoring import WikiAuthoringChunkWorkflow
from code_assist_v1.knowledge_core.services.wiki_synthesis import WikiSynthesisWorkflow
from code_assist_v1.knowledge_core.services.publication import PublicationPreviewExtractionHandler, Utf8PublicationExtractor
from code_assist_v1.knowledge_core.services.curation_worker import CanonicalCurationRescanHandler
from code_assist_v1.knowledge_core.wiki_authoring import WikiChunkAuthor, AuthoringModelPolicy
from code_assist_v1.knowledge_core.wiki_synthesis import WikiReductionAuthor, SynthesisPolicy
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import _migrate
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import _seed_actor, _assign_actor_to_part, _SourceLoader
from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _uploaded_preview
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_workflow import _rows


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005")]
_MAP = "publication_wiki.author_chunk"
_PLAN = "publication_wiki.plan_synthesis"
_REDUCE = "publication_wiki.reduce_batch"


def _wire(case, *, policy=None, failpoint=None):
    chunks = WikiAuthoringChunkWorkflow(case.factory, author=WikiChunkAuthor(runner=case.runner))
    synthesis = WikiSynthesisWorkflow(case.factory, chunks=chunks, author=WikiReductionAuthor(runner=case.runner),
        policy=policy or SynthesisPolicy(input_bytes=7000, boundary_context_characters=20))
    kernel = DurableWorkKernel(case.factory, HandlerRegistry((chunks.registered_handler(), *synthesis.registered_handlers(),
        PublicationPreviewExtractionHandler(case.factory, _SourceLoader(case.raw), Utf8PublicationExtractor()).registered_handler(),
        CanonicalCurationRescanHandler(case.factory, scope_resolver=case.scope).registered_handler())),
        scope_resolver=case.scope, failpoint=failpoint)
    chunks.bind_kernel(kernel)
    synthesis.bind_kernel(kernel)
    case.chunks, case.synthesis, case.kernel = chunks, synthesis, kernel


def _case(factory, scope, *, actor="actor", target=None, epoch=None, raw=None):
    if epoch is None:
        connection = factory.open(read_only=True)
        try:
            epoch = connection.execute("SELECT scope_epoch FROM app_user WHERE id=?", (actor,)).fetchone()[0]
        finally:
            connection.close()
    raw = raw or ("# 긴 원본 문서\n" + "수치와 조건을 함께 보존한다.\n" * 70 + "부록: 예외 규칙을 확인한다.\n").encode()
    preview = _uploaded_preview(factory, scope, actor=actor, epoch=epoch, raw=raw, target=target)
    case = SimpleNamespace(factory=factory, scope=scope, actor=actor, raw=raw, preview=preview,
        calls=[], before_reduce=None, failures=0)
    prose = "sharedsynthesis 저장 검증용 요지 " + sha256(raw).hexdigest()[:12]
    def runner(task, payload, **kwargs):
        case.calls.append((task, payload.get("batch_id") or payload.get("chunk_id")))
        # A separate immediate writer proves no provider call owns a DB writer.
        connection = factory.open()
        try:
            with transaction(connection):
                connection.execute("SELECT 1")
        finally:
            connection.close()
        if task == "canonical_wiki_synthesis":
            if case.before_reduce:
                case.before_reduce()
            if case.failures:
                case.failures -= 1
                raise RuntimeError("isolated reducer unavailable")
            return _response(payload, text=prose), _META
        return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "구간 요지", "claims": [{"text": prose,
                "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False), _META
    case.runner = runner
    _wire(case)
    case.prepared = case.chunks.prepare_upload(preview_id=preview.preview_id, actor_user_id=actor,
        chunk_bytes=512, model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
    case.run_id, case.map_ids = case.chunks.enqueue_upload(case.prepared)
    return case


@pytest.fixture
def workflow_case(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_WORKFLOW_MIGRATION_CATALOG)
    _seed_actor(factory)
    _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True))
    return _case(factory, scope)


def _claim(case, kind=None):
    claim = case.kernel.claim_next(claim_owner="synthesis-test", lease_ms=60_000,
        handler_keys=None if kind is None else (HandlerKey(kind, "v1"),))
    assert claim is not None
    return claim


def _run(case, kind=None):
    claim = _claim(case, kind)
    result = case.kernel.run_claim(claim)
    assert result.job.state == "succeeded", result.job.error
    return result


def _maps(case):
    for _ in case.map_ids:
        _run(case, _MAP)


def _drain(case):
    results = []
    for _ in range(150):
        claim = case.kernel.claim_next(claim_owner="synthesis-drain", lease_ms=60_000)
        if claim is None:
            return results
        result = case.kernel.run_claim(claim)
        assert result.job.state == "succeeded", result.job.error
        results.append(result)
    pytest.fail("Durable synthesis did not converge within fixture work bound.")


def test_maps_automatically_schedule_complete_waves_and_one_final_snapshot(workflow_case):
    case = workflow_case
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_config") == [(0,)]
    results = _drain(case)
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("ready",)]
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(1,)]
    planners = [r.job for r in results if r.job.job_type == _PLAN]
    reducers = [r.job for r in results if r.job.job_type == _REDUCE]
    assert len(planners) >= 2 and reducers
    assert [p.input_ref["after_round"] for p in planners] == list(range(len(planners)))
    assert len([call for call in case.calls if call[0] == "canonical_wiki_synthesis"]) == len(reducers)
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_receipt") == [(len(reducers),)]
    assert _drain(case) == []
    manifest = json.loads(_rows(case, "SELECT manifest_json FROM authored_wiki_snapshot_body")[0][0])
    assert not manifest["synthesis"]["compression_verified"]
    assert not manifest["semantic_support_verified"]


def test_intent_forbids_legacy_map_only_completion_before_first_planner(workflow_case):
    case = workflow_case
    _maps(case)
    connection = case.factory.open()
    try:
        repo = WikiAuthoringRepository()
        args = dict(run_id=case.run_id, actor_user_id=case.actor, now_ms=db_now_ms(connection))
        value = repo.assemble(connection, **args)
        with pytest.raises(sqlite3.IntegrityError, match="complete synthesis"):
            with transaction(connection):
                repo.complete(connection, **args, prepared=value)
        assert connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 0
    finally:
        connection.close()
    _drain(case)


def test_frozen_upload_policy_survives_new_workflow_before_last_map(workflow_case):
    case = workflow_case
    _run(case, _MAP)
    _wire(case, policy=SynthesisPolicy(input_bytes=65_536))
    _drain(case)
    assert json.loads(_rows(case, "SELECT policy_json FROM publication_synthesis_config")[0][0])["input_bytes"] == 7000
    with pytest.raises(AuthoringStorageConflict, match="another policy"):
        case.chunks.enqueue_upload(case.prepared)


@pytest.mark.parametrize("stage", ["planner", "reducer"])
def test_queued_cancellation_converges_and_preserves_extraction(workflow_case, stage):
    case = workflow_case
    _maps(case)
    if stage == "reducer":
        _run(case, _PLAN)
        kind = _REDUCE
    else:
        # Reconciliation enqueues the planner, without claiming it in this subset.
        assert case.kernel.claim_next(claim_owner="none", lease_ms=60_000, handler_keys=(HandlerKey(_MAP, "v1"),)) is None
        kind = _PLAN
    job_id = _rows(case, "SELECT id FROM durable_job WHERE job_type=? AND state='queued' ORDER BY id LIMIT 1", (kind,))[0][0]
    case.kernel.request_cancel(job_id)
    case.kernel.recover_expired()
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(1,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type IN (?,?) AND state IN ('queued','retry','claimed','running')",
        (_PLAN, _REDUCE)) == [(0,)]


@pytest.mark.parametrize("phase", ["wave", "ready"])
@pytest.mark.parametrize("mutation", ["wrong_hash", "wrong_round", "extra", "boolean"])
def test_planner_success_rejects_a_result_not_identifying_its_effect(workflow_case, phase, mutation):
    case = workflow_case
    _maps(case)
    if phase == "ready":
        for _ in range(30):
            claim = _claim(case)
            if claim.job.job_type == _PLAN and claim.job.input_ref["after_round"] > 0:
                after_round = claim.job.input_ref["after_round"]
                if _rows(case, "SELECT batch_count FROM publication_synthesis_wave WHERE round_number=?", (after_round,)) == [(1,)]:
                    break
            result = case.kernel.run_claim(claim)
            assert result.job.state == "succeeded", result.job.error
        else:
            pytest.fail("No terminal planner.")
    else:
        claim = _claim(case, _PLAN)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.synthesis.prepare_plan(started)
    assert prepared.result["outcome"] == phase
    changed = dict(prepared.result)
    if mutation == "wrong_hash":
        changed["plan_sha256" if phase == "wave" else "manifest_sha256"] = "0" * 64
    elif mutation == "wrong_round":
        changed["after_round"] += 1
    elif mutation == "extra":
        changed["extra"] = "not retained"
    else:
        changed["schema_version"] = True
    connection = case.factory.open()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="exact committed effect"):
            with transaction(connection):
                case.synthesis.commit_plan(connection, started, prepared)
                JobRepository().succeed(connection, fence=claim.fence, result=changed, now_ms=db_now_ms(connection))
    finally:
        connection.close()
    # Failed success validation rolls back both the effect and its audit/outbox.
    assert _rows(case, "SELECT state FROM durable_job WHERE id=?", (claim.job.id,)) == [("running",)]
    assert case.kernel.commit_prepared(claim.fence, prepared).job.state == "succeeded"
    _drain(case)


def test_all_but_one_batch_does_not_schedule_next_round(workflow_case):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    count = _rows(case, "SELECT batch_count FROM publication_synthesis_wave WHERE round_number=1")[0][0]
    assert count > 1
    for _ in range(count - 1):
        _run(case, _REDUCE)
    assert case.kernel.claim_next(claim_owner="no-early-plan", lease_ms=60_000,
        handler_keys=(HandlerKey(_PLAN, "v1"),)) is None
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_planner") == [(1,)]
    _drain(case)


def test_planner_binding_failure_rolls_back_wave_reducers_and_event(workflow_case, monkeypatch):
    case = workflow_case
    _maps(case)
    claim = _claim(case, _PLAN)
    started = case.kernel._start_claim(claim.fence)
    prepared = case.synthesis.prepare_plan(started)
    original = case.synthesis._repository.bind_batch_job
    def fail(connection, **kwargs):
        original(connection, **kwargs)
        if kwargs["ordinal"] == 2:
            raise RuntimeError("isolated second reducer binding failure")
    monkeypatch.setattr(case.synthesis._repository, "bind_batch_job", fail)
    with pytest.raises(RuntimeError, match="second reducer binding"):
        case.kernel.commit_prepared(claim.fence, prepared)
    for table in ("publication_synthesis_config", "publication_synthesis_wave", "publication_synthesis_batch"):
        assert _rows(case, f"SELECT count(*) FROM {table}") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type=?", (_REDUCE,)) == [(0,)]
    assert _rows(case, "SELECT count(*) FROM audit_event WHERE action='wiki.synthesis.wave'") == [(0,)]
    monkeypatch.setattr(case.synthesis._repository, "bind_batch_job", original)
    assert case.kernel.commit_prepared(claim.fence, prepared).job.state == "succeeded"
    _drain(case)


@pytest.mark.parametrize("kind", [_PLAN, _REDUCE])
def test_lost_ack_replays_exact_effect_without_provider_repeat(workflow_case, kind):
    case = workflow_case
    _maps(case)
    if kind == _REDUCE:
        _run(case, _PLAN)
    claim = _claim(case, kind)
    started = case.kernel._start_claim(claim.fence)
    prepared = (case.synthesis.prepare_plan if kind == _PLAN else case.synthesis.prepare_batch)(started)
    calls = len(case.calls)
    class LostAck(RuntimeError):
        pass
    def failpoint(name):
        if name == "job.after_domain_commit_before_ack":
            raise LostAck()
    _wire(case, failpoint=failpoint)
    with pytest.raises(LostAck):
        case.kernel.commit_prepared(claim.fence, prepared)
    _wire(case)
    assert case.kernel.commit_prepared(claim.fence, prepared).replayed
    assert len(case.calls) == calls
    _drain(case)


def test_fresh_process_resumes_after_one_reduction_receipt(workflow_case):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    first = _run(case, _REDUCE).job
    script = r'''
import json, sys
from types import SimpleNamespace
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.synthesis_workflow_catalog import SYNTHESIS_WORKFLOW_MIGRATION_CATALOG
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis_workflow import _wire, _drain
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META
factory=ConnectionFactory._for_isolated_tests(sys.argv[1],SYNTHESIS_WORKFLOW_MIGRATION_CATALOG)
calls=[]
def runner(task,payload,**kwargs):
    assert task=='canonical_wiki_synthesis'
    calls.append(payload['batch_id'])
    return _response(payload),_META
case=SimpleNamespace(factory=factory,scope=ScopeResolver(FeatureFlags(core_enabled=True,personal_reads=True,
    personal_writes=True,part_reads=True,part_writes=True)),raw=b'',runner=runner)
_wire(case)
results=_drain(case)
print(json.dumps({'calls':calls,'jobs':[r.job.id for r in results]}))
'''
    completed = subprocess.run([sys.executable, "-E", "-B", "-c", script, str(case.factory.database_path)],
        cwd=str(Path(__file__).resolve().parents[4]), capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.splitlines()[-1])
    assert report["calls"] and first.input_ref["batch_id"] not in report["calls"]
    assert first.id not in report["jobs"]
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("ready",)]


@pytest.mark.scenario("UPLOAD-009", "UPLOAD-010", "UPLOAD-011", "HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_worker_outputs_publish_to_multi_author_personal_part_and_native_higher_views(isolated_knowledge_layout):
    from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _organization, _hits
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _command, _release_and_check_search
    from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import _epoch
    from code_assist_v1.knowledge_core.services.publication import PublicationService
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_WORKFLOW_MIGRATION_CATALOG)
    org = _organization(factory)
    shared, team_items, all_items = set(), set(), set()
    private = {actor: set() for actor in org.personal}
    rows = (("member", "both", None), ("peer", "both", None), ("leader", "personal", None),
        ("executive", "personal", None), ("leader", "explicit", org.team), ("executive", "explicit", org.responsibility))
    for index, (actor, mode, target) in enumerate(rows):
        case = _case(factory, org.scope, actor=actor, target=target,
            raw=(f"# distinct upload {index}\n" + "조건과 예외를 보존한다.\n" * 30).encode())
        _drain(case)
        snapshot, body = _rows(case, """SELECT s.id,b.content_md FROM authored_wiki_snapshot s
            JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id WHERE s.run_id=?""", (case.run_id,))[0]
        result = PublicationService(factory, org.scope).publish(_command(case.preview, actor=actor,
            epoch=_epoch(factory, actor), mode=mode, target=target, snapshot=snapshot))
        _release_and_check_search(factory, org.scope, result, SimpleNamespace(markdown=body))
        for item in result.targets:
            if item["space_id"] == org.personal[actor]:
                private[actor].add(item["item_id"])
            elif item["space_id"] == org.ids["part_space_a"]:
                shared.add(item["item_id"])
            elif item["space_id"] == org.team.space_id:
                team_items.add(item["item_id"])
            else:
                all_items.add(item["item_id"])
    team_items |= shared
    all_items |= team_items
    for actor in ("member", "peer"):
        assert _hits(factory, org.scope, actor=actor, space=org.ids["part_space_a"]) == shared
        assert _hits(factory, org.scope, actor=actor, space=org.team.space_id) == team_items
        assert _hits(factory, org.scope, actor=actor, space=org.responsibility.space_id) == all_items
    for actor, items in private.items():
        assert _hits(factory, org.scope, actor=actor, space=org.personal[actor]) == items
        assert items.isdisjoint(all_items)


def test_reducer_retry_is_per_batch_and_does_not_repeat_successful_maps(workflow_case):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    case.failures = 1
    claim = _claim(case, _REDUCE)
    first = case.kernel.run_claim(claim)
    assert first.job.state == "retry"
    map_calls = sum(call[0] != "canonical_wiki_synthesis" for call in case.calls)
    _drain(case)
    assert sum(call[0] != "canonical_wiki_synthesis" for call in case.calls) == map_calls
    assert _rows(case, "SELECT attempt_count FROM durable_job WHERE id=?", (claim.job.id,)) == [(2,)]


@pytest.mark.parametrize("phase", ["before_body", "before_provider", "after_provider"])
def test_reducer_rechecks_current_epoch_before_body_provider_and_commit(workflow_case, monkeypatch, phase):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    claim = _claim(case, _REDUCE)
    def revoke_epoch():
        connection = case.factory.open()
        try:
            with transaction(connection):
                connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id=?", (case.actor,))
        finally:
            connection.close()
    if phase == "before_body":
        revoke_epoch()
        monkeypatch.setattr(case.synthesis._repository, "load_batch", lambda *a, **kw: pytest.fail("unauthorized source read"))
    elif phase == "before_provider":
        original = case.synthesis._repository.load_batch
        def load(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke_epoch()
            return value
        monkeypatch.setattr(case.synthesis._repository, "load_batch", load)
    else:
        case.before_reduce = revoke_epoch
    result = case.kernel.run_claim(claim)
    assert result.job.state in {"failed", "cancelled"}
    assert sum(call[0] == "canonical_wiki_synthesis" for call in case.calls) == (phase == "after_provider")
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_receipt") == [(0,)]
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]


@pytest.mark.parametrize("kind", ["team", "responsibility"])
@pytest.mark.parametrize("phase", ["before_body", "before_provider", "after_provider", "commit"])
@pytest.mark.scenario("HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_native_higher_role_revocation_stops_reduction_at_each_boundary(isolated_knowledge_layout, monkeypatch, kind, phase):
    from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _organization
    from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import _epoch, _higher_service
    from code_assist_v1.knowledge_core.services.higher_authority import SetHigherRoleCommand
    from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_WORKFLOW_MIGRATION_CATALOG)
    org = _organization(factory)
    actor, target = ("leader", org.team) if kind == "team" else ("executive", org.responsibility)
    case = _case(factory, org.scope, actor=actor, target=target)
    _maps(case)
    _run(case, _PLAN)
    claim = _claim(case, _REDUCE)
    assert _rows(case, "SELECT current_part_id FROM app_user WHERE id=?", (actor,)) == [(None,)]
    assert claim.job.space_id == target.space_id
    role_name = "team_leader" if kind == "team" else "responsibility_executive"
    def revoke():
        connection = factory.open(read_only=True)
        try:
            role = HigherSpaceRepository().get_open_role(connection, org_unit_id=target.space_id, role=role_name)
            version = connection.execute("SELECT row_version FROM knowledge_space WHERE id=?", (target.space_id,)).fetchone()[0]
        finally:
            connection.close()
        _higher_service(factory).set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=str(uuid4()), request_id=str(uuid4()), org_unit_id=target.space_id, role=role_name,
            assignee_user_id=None, expected_current_assignment_id=role.id,
            expected_current_assignment_generation=role.assignment_generation,
            expected_current_assignment_row_version=role.row_version, expected_space_row_version=version,
            enable_writes=False, reason="isolated synthesis current-role verification"))
    if phase == "before_body":
        revoke()
        monkeypatch.setattr(case.synthesis._repository, "load_batch", lambda *a, **kw: pytest.fail("revoked body read"))
    elif phase == "before_provider":
        original = case.synthesis._repository.load_batch
        def load(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value
        monkeypatch.setattr(case.synthesis._repository, "load_batch", load)
    elif phase == "after_provider":
        case.before_reduce = revoke
    if phase == "commit":
        started = case.kernel._start_claim(claim.fence)
        prepared = case.synthesis.prepare_batch(started)
        revoke()
        result = case.kernel.commit_prepared(claim.fence, prepared)
    else:
        result = case.kernel.run_claim(claim)
    assert result.job.state in {"cancelled", "failed"}, result.job.error
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_receipt") == [(0,)]
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert sum(call[0] == "canonical_wiki_synthesis" for call in case.calls) == (phase in {"after_provider", "commit"})


def test_two_processes_compete_for_exactly_one_initial_planner(workflow_case):
    case = workflow_case
    _maps(case)
    script = r'''
import json,sys
from types import SimpleNamespace
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.repositories.jobs import HandlerKey
from code_assist_v1.knowledge_core.migrations.synthesis_workflow_catalog import SYNTHESIS_WORKFLOW_MIGRATION_CATALOG
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis_workflow import _wire
case=SimpleNamespace(factory=ConnectionFactory._for_isolated_tests(sys.argv[1],SYNTHESIS_WORKFLOW_MIGRATION_CATALOG),
    scope=ScopeResolver(FeatureFlags(core_enabled=True,personal_reads=True,personal_writes=True,part_reads=True,part_writes=True)),
    raw=b'',runner=lambda *a,**k:None)
_wire(case)
claim=case.kernel.claim_next(claim_owner=sys.argv[2],lease_ms=60000,handler_keys=(HandlerKey('publication_wiki.plan_synthesis','v1'),))
print(json.dumps(None if claim is None else claim.job.id))
'''
    processes = [subprocess.Popen([sys.executable, "-E", "-B", "-c", script, str(case.factory.database_path), f"competitor-{index}"],
        cwd=str(Path(__file__).resolve().parents[4]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for index in range(2)]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=45)
        assert process.returncode == 0, stdout + stderr
        results.append(json.loads(stdout.splitlines()[-1]))
    assert sum(value is not None for value in results) == 1
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_planner") == [(1,)]
    winner = next(value for value in results if value is not None)
    case.kernel.request_cancel(winner)
    connection = case.factory.open()
    try:
        with transaction(connection):
            job = JobRepository().require(connection, winner)
    finally:
        connection.close()
    cancelled = case.kernel.run_claim(ClaimResult(job, ClaimFence(job.id, job.claim_owner, job.attempt_count)))
    assert cancelled.job.state == "cancelled"
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]


@pytest.mark.parametrize("phase", ["before_body", "after_replay", "commit"])
def test_model_free_planner_has_current_authority_fences_too(workflow_case, monkeypatch, phase):
    case = workflow_case
    _maps(case)
    claim = _claim(case, _PLAN)
    def revoke():
        connection = case.factory.open()
        try:
            with transaction(connection):
                connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id=?", (case.actor,))
        finally:
            connection.close()
    if phase == "before_body":
        revoke()
        monkeypatch.setattr(case.synthesis._repository, "prepare_progress", lambda *a, **kw: pytest.fail("unauthorized planner body read"))
    elif phase == "after_replay":
        original = case.synthesis._repository.prepare_progress
        def replay(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value
        monkeypatch.setattr(case.synthesis._repository, "prepare_progress", replay)
    if phase == "commit":
        started = case.kernel._start_claim(claim.fence)
        prepared = case.synthesis.prepare_plan(started)
        revoke()
        result = case.kernel.commit_prepared(claim.fence, prepared)
    else:
        result = case.kernel.run_claim(claim)
    assert result.job.state == "failed", result.job.error
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_wave") == [(0,)]
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert not any(task == "canonical_wiki_synthesis" for task, _ in case.calls)


def test_purged_intermediate_receipt_is_terminal_not_reduced_again(workflow_case):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    first = _run(case, _REDUCE).job
    connection = case.factory.open()
    try:
        with transaction(connection):
            key = (case.run_id, first.input_ref["round_number"], first.input_ref["ordinal"])
            connection.execute("UPDATE publication_synthesis_receipt SET retention_status='purged' WHERE run_id=? AND round_number=? AND ordinal=?", key)
            connection.execute("DELETE FROM publication_synthesis_batch_body WHERE run_id=? AND round_number=? AND ordinal=?", key)
    finally:
        connection.close()
    assert _drain(case) == []
    assert sum(task == "canonical_wiki_synthesis" for task, _ in case.calls) == 1
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(1,)]


def test_reducer_exhaustion_cancels_all_siblings_without_false_ready(workflow_case):
    case = workflow_case
    _maps(case)
    _run(case, _PLAN)
    case.failures = 20
    claim = _claim(case, _REDUCE)
    assert case.kernel.run_claim(claim).job.state == "retry"
    # Normal ordering can retry this batch or select another. Drain until the
    # first exhausted exact job terminalizes the complete run.
    for _ in range(30):
        claim = case.kernel.claim_next(claim_owner="exhaustion", lease_ms=60_000,
            handler_keys=(HandlerKey(_REDUCE, "v1"),))
        if claim is None:
            break
        assert case.kernel.run_claim(claim).job.state in {"retry", "dead"}
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("extraction_only",)]
    assert _rows(case, "SELECT count(*) FROM authored_wiki_snapshot") == [(0,)]
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_receipt") == [(0,)]


def test_single_complete_map_finishes_without_reduction_provider_call(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_WORKFLOW_MIGRATION_CATALOG)
    _seed_actor(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    case = _case(factory, scope, raw="# 짧은 원문\n완전한 적용 조건이다.".encode())
    assert len(case.map_ids) == 1
    results = _drain(case)
    assert len(results) == 2
    assert not any(task == "canonical_wiki_synthesis" for task, _ in case.calls)
    assert _rows(case, "SELECT mode,last_round FROM publication_synthesis_completion") == [("single_map_no_reduction", 0)]


def test_exact_enqueue_retry_after_partial_map_keeps_one_intent_and_job_set(workflow_case):
    case = workflow_case
    _run(case, _MAP)
    assert case.chunks.enqueue_upload(case.prepared) == (case.run_id, case.map_ids)
    assert _rows(case, "SELECT count(*) FROM publication_synthesis_intent") == [(1,)]
    assert _rows(case, "SELECT count(*) FROM durable_job WHERE job_type=?", (_MAP,)) == [(len(case.map_ids),)]
    _drain(case)


def test_unbound_failed_job_cannot_poison_a_bound_synthesis_run(workflow_case):
    case = workflow_case
    _maps(case)
    claim = _claim(case, _PLAN)
    record = claim.job
    connection = case.factory.open()
    try:
        with transaction(connection):
            poison = JobRepository().enqueue(connection, JobEnqueueDraft(job_id=str(uuid4()), dedup_id=None,
                operation_id=None, actor_kind="user", actor_user_id=record.actor_user_id, space_id=record.space_id,
                job_type=record.job_type, handler_version=record.handler_version, effect_key="unbound-fixture:" + str(uuid4()),
                input_ref=record.input_ref, expected_state=record.expected_state,
                authorization_snapshot=record.authorization_snapshot, authorization_scope_epoch=record.authorization_scope_epoch,
                authorization_part_id=record.authorization_part_id, authorization_capabilities=record.authorization_capabilities,
                max_attempts=record.max_attempts, cancellable=True, available_at_ms=db_now_ms(connection)))
            JobRepository().request_cancel(connection, job_id=poison.job.id, now_ms=db_now_ms(connection))
        with transaction(connection):
            case.synthesis.reconcile_pending(connection)
    finally:
        connection.close()
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("pending",)]
    assert case.kernel.run_claim(claim).job.state == "succeeded"
    _drain(case)
    assert _rows(case, "SELECT status FROM publication_authoring_run") == [("ready",)]
