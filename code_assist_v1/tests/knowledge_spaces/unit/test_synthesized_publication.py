"""Persisted reduction -> canonical publication -> authorized shared retrieval.

These fixtures use real UTF-8 upload extraction jobs and canonical writes, but
fake map/reduce prose and staged blobs. They are not rich-file, browser, model
quality or load evidence, and never target a live account or database.
"""
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import db_now_ms, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.migrations.synthesis_catalog import SYNTHESIS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.wiki_authoring import WikiAuthoringRepository
from code_assist_v1.knowledge_core.repositories.wiki_synthesis import WikiSynthesisRepository, reduction_job_result
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import UserRepository
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
from code_assist_v1.knowledge_core.repositories.publication_authority import HigherPublicationTargetDescriptor
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.repositories.jobs import JobRepository
from code_assist_v1.knowledge_core.services.higher_authority import SetHigherRoleCommand
from code_assist_v1.knowledge_core.services.publication import PublicationService
from code_assist_v1.knowledge_core.services.retrieval import CanonicalRetrievalService, SpaceRetrievalRequest
from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy, ExtractionSource, WikiChunkAuthor, plan_authoring
from code_assist_v1.knowledge_core.wiki_synthesis import SynthesisPolicy, WikiReductionAuthor
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import _migrate
from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import (
    _uploaded_preview, _command, _release_and_check_search,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import _seed_actor, _assign_actor_to_part
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis_storage import _claim_batch
from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import (
    _seed, _epoch, _higher_service, _role_command, _descriptor, _legacy_user,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_synthesis import _response, _META


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005", "UPLOAD-009", "UPLOAD-010", "UPLOAD-011")]


def _id():
    return str(uuid4())


def _synthesize(factory, preview, actor, prose):
    """Exercise real storage without pretending this loop is a durable handler."""
    connection = factory.open()
    try:
        def args():
            return dict(run_id=run_id, actor_user_id=actor, now_ms=db_now_ms(connection))

        row = connection.execute("""SELECT b.snapshot_id,b.content_sha256,b.content_md
            FROM publication_preview p JOIN extraction_snapshot_body b
            ON b.snapshot_id=p.extraction_snapshot_id WHERE p.id=?""", (preview.preview_id,)).fetchone()
        plan = plan_authoring(ExtractionSource(*row), chunk_bytes=512,
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
        maps, synthesis = WikiAuthoringRepository(), WikiSynthesisRepository()
        prepared = maps.prepare_run(connection, preview_id=preview.preview_id, actor_user_id=actor,
            plan=plan, now_ms=db_now_ms(connection))
        with transaction(connection):
            run_id = maps.create_run(connection, preview_id=preview.preview_id, actor_user_id=actor,
                prepared=prepared, now_ms=db_now_ms(connection))

        def map_runner(task, payload, **kwargs):
            assert not connection.in_transaction
            return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
                "sections": [{"heading": "조각별 초안", "claims": [{"text": "저장 검사용 초안.",
                    "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False), _META

        for chunk in reversed(plan.chunks):
            result = WikiChunkAuthor(runner=map_runner).author(plan, chunk.ordinal)
            prepared = maps.prepare_chunk(connection, **args(), result=result)
            with transaction(connection):
                maps.save_chunk(connection, **args(), prepared=prepared)
        policy = SynthesisPolicy(input_bytes=7000, boundary_context_characters=20)
        calls = []

        def reduce_runner(task, payload, **kwargs):
            assert not connection.in_transaction
            calls.append(payload["batch_id"])
            return _response(payload, text=prose), _META

        for _ in range(policy.max_rounds + 1):
            prepared = synthesis.prepare_progress(connection, **args(), policy=policy)
            with transaction(connection):
                snapshot = synthesis.persist_progress(connection, prepared=prepared, now_ms=db_now_ms(connection))
            if snapshot:
                assert calls and prepared.wiki is not None
                manifest = json.loads(prepared.wiki.manifest_json)
                assert manifest["synthesis"]["model_calls"] == len(calls)
                assert not manifest["synthesis"]["compression_verified"]
                return snapshot, prepared.wiki
            pending = connection.execute("""SELECT b.round_number,b.ordinal FROM publication_synthesis_batch b
                LEFT JOIN publication_synthesis_receipt r ON r.run_id=b.run_id
                  AND r.round_number=b.round_number AND r.ordinal=b.ordinal
                WHERE b.run_id=? AND r.run_id IS NULL ORDER BY b.round_number,b.ordinal""", (run_id,)).fetchall()
            for number, ordinal in pending:
                claim = _claim_batch(connection, synthesis, run_id, actor, number, ordinal)
                original, wave, batch, frozen = synthesis.load_batch(connection, **args(), round_number=number, ordinal=ordinal, claim=claim)
                result = WikiReductionAuthor(runner=reduce_runner).author(original, batch, wave=wave, policy=frozen)
                receipt = synthesis.prepare_receipt(connection, **args(), round_number=number, ordinal=ordinal, result=result, claim=claim)
                with transaction(connection):
                    synthesis.save_receipt(connection, prepared=receipt, now_ms=db_now_ms(connection))
                    JobRepository().succeed(connection, claim,
                        result=reduction_job_result(run_id=run_id, ordinal=ordinal, receipt=receipt.receipt), now_ms=db_now_ms(connection))
        pytest.fail("The complete isolated synthesis did not converge.")
    finally:
        connection.close()


def _publish(factory, scope, *, actor, mode, marker, target=None):
    # Distinct originals prevent accidental source-equivalent reuse from hiding
    # a missing author or a wrongly selected generated snapshot.
    raw = (f"# {marker}\n작성자 {actor}의 독립 검증 원문.\n"
           + "수치, 단위, 적용 범위와 예외를 함께 보존한다.\n" * 25
           + f"부록: {marker}에는 별도의 제외 조건이 있다.\n").encode()
    epoch = _epoch(factory, actor)
    preview = _uploaded_preview(factory, scope, actor=actor, epoch=epoch, raw=raw,
        filename=f"{marker}.md", target=target)
    snapshot, wiki = _synthesize(factory, preview, actor, f"sharedsynthesis {marker}의 통합 저장 검증 결과.")
    command = _command(preview, actor=actor, epoch=epoch, mode=mode, target=target, snapshot=snapshot)
    publisher = PublicationService(factory, scope)
    result = publisher.publish(command)
    assert result.authored_snapshot_id == snapshot
    assert publisher.publish(replace(command, request_id=_id())) == result
    _release_and_check_search(factory, scope, result, wiki)
    connection = factory.open(read_only=True)
    try:
        extracted = connection.execute("""SELECT b.content_md FROM publication_preview p
            JOIN extraction_snapshot_body b ON b.snapshot_id=p.extraction_snapshot_id WHERE p.id=?""", (preview.preview_id,)).fetchone()[0]
        assert extracted == raw.decode() and extracted != wiki.markdown
        for item in result.targets:
            saved = connection.execute("SELECT content_md FROM knowledge_revision_body WHERE revision_id=?",
                (item["resolved_revision_id"],)).fetchone()[0]
            assert saved == wiki.markdown
            source = connection.execute("""SELECT a.publisher_user_id,a.raw_sha256 FROM item_source s
                JOIN source_artifact a ON a.id=s.artifact_id WHERE s.item_id=?""", (item["item_id"],)).fetchone()
            assert tuple(source) == (actor, sha256(raw).hexdigest())
    finally:
        connection.close()
    return result, wiki


def _hits(factory, scope, *, actor, space):
    result = CanonicalRetrievalService(factory, scope_resolver=scope,
        retrieval=HybridRetrievalRepository()).retrieve_for_space(SpaceRetrievalRequest(
            actor_user_id=actor, actor_scope_epoch=_epoch(factory, actor),
            space_id=space, query="sharedsynthesis", top_k=20))
    assert all(row.excerpt_md.strip() for row in result.ranked_documents)
    return {row.item_id for row in result.ranked_documents}


@pytest.mark.parametrize("mode", ["personal", "part", "both"])
def test_reduced_snapshot_is_published_and_indexed_in_each_target_mode(isolated_knowledge_layout, mode):
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_MIGRATION_CATALOG)
    _seed_actor(factory)
    _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True))
    result, _ = _publish(factory, scope, actor="actor", mode=mode, marker=f"target-{mode}")
    assert len(result.targets) == (2 if mode == "both" else 1)
    for item in result.targets:
        assert _hits(factory, scope, actor="actor", space=item["space_id"]) == {item["item_id"]}
    if mode == "both":
        assert len({item["item_id"] for item in result.targets}) == 2
        assert len({item["resolved_revision_id"] for item in result.targets}) == 2


def _organization(factory):
    ids = _seed(factory)
    users, spaces, orgs = UserRepository(), SpaceRepository(), OrganizationRepository()
    connection = factory.open()
    personal = {}
    try:
        with transaction(connection):
            users.import_legacy_many(connection, (_legacy_user("peer"), _legacy_user("executive")))
            for actor in ("member", "peer", "leader", "executive"):
                record = spaces.ensure_personal(connection, space_id=_id(), user_id=actor, created_at_ms=1300)
                spaces.update_state(connection, space_id=record.space.id,
                    expected_row_version=record.space.row_version, status="active", write_enabled=True, updated_at_ms=1301)
                personal[actor] = record.space.id
            for actor in ("member", "peer"):
                orgs.open_membership(connection, membership_id=_id(), user_id=actor, part_id=ids["part_a"],
                    assigned_by="admin", reason="isolated multi-author synthesis", valid_from_ms=1400)
                user = users.require(connection, actor)
                orgs.compare_and_swap_user_part(connection, user_id=actor, expected_current_part_id=None,
                    target_part_id=ids["part_a"], expected_scope_epoch=user.scope_epoch,
                    expected_row_version=user.row_version, updated_at_ms=1401)
            orgs.open_part_leader(connection, assignment_id=_id(), user_id="member", part_id=ids["part_a"],
                assigned_by="admin", reason="isolated active Part writers", valid_from_ms=1402)
            part = spaces.require_by_id(connection, ids["part_space_a"])
            spaces.update_state(connection, space_id=part.id, expected_row_version=part.row_version,
                status="active", write_enabled=True, updated_at_ms=1403)
    finally:
        connection.close()
    higher = _higher_service(factory)
    higher.set_role(_role_command(factory, ids, operation="synthesis-team-role", assignee="leader"))
    team = _descriptor(factory, ids)
    higher.set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
        operation_id=_id(), request_id=_id(), org_unit_id=RESPONSIBILITY_ROOT_ID,
        role="responsibility_executive", assignee_user_id="executive", expected_current_assignment_id=None,
        expected_current_assignment_generation=None, expected_current_assignment_row_version=None,
        expected_space_row_version=1, enable_writes=True, reason="isolated synthesis executive"))
    connection = factory.open(read_only=True)
    try:
        role = HigherSpaceRepository().get_open_role(connection, org_unit_id=RESPONSIBILITY_ROOT_ID,
            role="responsibility_executive")
        assert all(connection.execute("SELECT current_part_id FROM app_user WHERE id=?", (actor,)).fetchone()[0] is None
                   for actor in ("leader", "executive"))
    finally:
        connection.close()
    responsibility = HigherPublicationTargetDescriptor(space_id=RESPONSIBILITY_ROOT_ID, kind="responsibility",
        capability="publish", role_assignment_id=role.id, role_assignment_generation=role.assignment_generation)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True, higher_spaces=True, team_writes=True, responsibility_writes=True))
    return SimpleNamespace(ids=ids, personal=personal, team=team, responsibility=responsibility, scope=scope)


@pytest.mark.scenario("HSPACE-009", "HSPACE-010", "HSPACE-011")
def test_multi_author_reduced_documents_accumulate_in_appropriate_shared_views(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, SYNTHESIS_MIGRATION_CATALOG)
    org = _organization(factory)
    shared = set()
    private = {actor: set() for actor in ("member", "peer", "leader", "executive")}
    # Twelve independently uploaded/reduced documents, two genuine Part authors.
    for actor in ("member", "peer"):
        for index in range(6):
            mode = "both" if index == 0 else "part"
            result, _ = _publish(factory, org.scope, actor=actor, mode=mode, marker=f"{actor}-{index}")
            for item in result.targets:
                (shared if item["space_id"] == org.ids["part_space_a"] else private[actor]).add(item["item_id"])
    assert len(shared) == 12
    for actor in private:
        result, _ = _publish(factory, org.scope, actor=actor, mode="personal", marker=f"private-{actor}")
        private[actor].add(result.targets[0]["item_id"])
    team, _ = _publish(factory, org.scope, actor="leader", mode="explicit", target=org.team, marker="team-native")
    responsibility, _ = _publish(factory, org.scope, actor="executive", mode="explicit",
        target=org.responsibility, marker="responsibility-native")
    team_items = shared | {team.targets[0]["item_id"]}
    all_shared = team_items | {responsibility.targets[0]["item_id"]}
    for actor in ("member", "peer"):
        assert _hits(factory, org.scope, actor=actor, space=org.ids["part_space_a"]) == shared
        assert _hits(factory, org.scope, actor=actor, space=org.team.space_id) == team_items
        assert _hits(factory, org.scope, actor=actor, space=org.responsibility.space_id) == all_shared
    assert _hits(factory, org.scope, actor="leader", space=org.team.space_id) == team_items
    assert _hits(factory, org.scope, actor="executive", space=org.responsibility.space_id) == all_shared
    for actor in private:
        assert _hits(factory, org.scope, actor=actor, space=org.personal[actor]) == private[actor]
        assert private[actor].isdisjoint(all_shared)
