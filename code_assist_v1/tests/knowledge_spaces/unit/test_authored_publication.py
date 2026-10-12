"""Real canonical publication of fake authored output; not model-quality proof."""
from dataclasses import replace
from hashlib import sha256
import base64
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import transaction, db_now_ms
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.repositories.publication_commit import PublicationCommitRepository
from code_assist_v1.knowledge_core.repositories.wiki_authoring import WikiAuthoringRepository
from code_assist_v1.knowledge_core.repositories.users import UserRepository
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.services.retrieval import CanonicalRetrievalService, SpaceRetrievalRequest
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID
from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
from code_assist_v1.knowledge_core.repositories.publication_authority import HigherPublicationTargetDescriptor
from code_assist_v1.knowledge_core.services.higher_authority import SetHigherRoleCommand
from code_assist_v1.knowledge_core.services.curation_worker import CanonicalCurationRescanHandler
from code_assist_v1.knowledge_core.services.lifecycle import ItemLifecycleService, DeactivateItemCommand
from code_assist_v1.knowledge_core.services.editing import (
    ImmutableItemEditService, LeaseHmacKeyRing, AcquireLeaseCommand, SaveItemCommand,
)
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand, CreateUploadPreviewCommand, PublicationPreviewService, PublicationService,
    PublishPreviewCommand, PublicationPreviewAuthorizationError,
    PublicationPreviewIdempotencyError, PublicationPreviewExtractionHandler, Utf8PublicationExtractor,
)
from code_assist_v1.knowledge_core.wiki_authoring import (
    ExtractionSource, AuthoringModelPolicy, plan_authoring, WikiChunkAuthor,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_authoring_storage import (
    authored_source, _finish_chunks, _migrate,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_preview_service import (
    _seed_actor, _assign_actor_to_part, _Stager, _SourceLoader,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_higher_publication_service import (
    _seed as _seed_higher, _epoch, _higher_service, _role_command,
    _descriptor, _scope as _higher_scope,
    _legacy_user,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005", "UPLOAD-008", "UPLOAD-011")]


def _id():
    return str(uuid4())


def _author(factory, preview, actor, *, prose="조건과 예외를 정리한 발행용 지식."):
    connection = factory.open()
    try:
        row = connection.execute("SELECT snapshot_id,content_sha256,content_md FROM extraction_snapshot_body WHERE snapshot_id=(SELECT extraction_snapshot_id FROM publication_preview WHERE id=?)",
                                 (preview.preview_id,)).fetchone()
        plan = plan_authoring(ExtractionSource(*row), chunk_bytes=4096,
            model_policy=AuthoringModelPolicy(provider_order=("codex",), model_ids=("fixture",)))
        repo = WikiAuthoringRepository()
        now = db_now_ms(connection)
        run_preparation = repo.prepare_run(connection, preview_id=preview.preview_id,
            actor_user_id=actor, plan=plan, now_ms=now)
        with transaction(connection):
            run = repo.create_run(connection, preview_id=preview.preview_id, actor_user_id=actor,
                                  prepared=run_preparation, now_ms=now)
        now = db_now_ms(connection)
        def runner(task, payload, **kwargs):
            assert not connection.in_transaction
            return json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
                "sections": [{"heading": "발행 검증", "claims": [{"text": prose,
                    "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False), {
                        "model_kind": "codex", "model_id": "fixture"}
        for chunk in plan.chunks:
            result = WikiChunkAuthor(runner=runner).author(plan, chunk.ordinal)
            prepared = repo.prepare_chunk(connection, run_id=run, actor_user_id=actor, result=result, now_ms=now)
            with transaction(connection):
                repo.save_chunk(connection, run_id=run, actor_user_id=actor, prepared=prepared, now_ms=now)
        prepared = repo.assemble(connection, run_id=run, actor_user_id=actor, now_ms=now)
        with transaction(connection):
            snapshot = repo.complete(connection, run_id=run, actor_user_id=actor, prepared=prepared, now_ms=now)
        return snapshot, prepared.wiki
    finally:
        connection.close()


def _command(preview, *, actor="actor", epoch=1, mode="personal", target=None, snapshot=None):
    return PublishPreviewCommand(request_id=_id(), operation_id=_id(), actor_user_id=actor,
        actor_scope_epoch=epoch, preview_id=preview.preview_id,
        source_snapshot_sha256=preview.source_snapshot_sha256, target_mode=mode,
        target=target, authored_snapshot_id=snapshot)


def _uploaded_preview(factory, scope, *, actor, epoch, raw, filename="reuse.md", target=None):
    queued = PublicationPreviewService(factory, scope, _Stager()).create_upload_preview(
        CreateUploadPreviewCommand(request_id=_id(), operation_id=_id(), actor_user_id=actor,
            actor_scope_epoch=epoch, content=raw, original_filename=filename,
            media_type="text/markdown", title="Stable original upload", target=target))
    kernel = DurableWorkKernel(factory, HandlerRegistry((PublicationPreviewExtractionHandler(
        factory, _SourceLoader(raw, original_filename=filename), Utf8PublicationExtractor()).registered_handler(),
        CanonicalCurationRescanHandler(factory, scope_resolver=scope).registered_handler())),
        scope_resolver=scope)
    for _ in range(8):
        _, claim = kernel.recover_and_claim(claim_owner="authored-upload-test", lease_ms=30000)
        assert claim is not None
        assert kernel.run_claim(claim).job.state == "succeeded"
        if claim.job.job_type == "publication_preview.extract":
            break
        assert claim.job.job_type == "curation.rescan"
    else:
        pytest.fail("The isolated extraction did not complete after existing curation work.")
    return SimpleNamespace(preview_id=queued.preview_id, source_snapshot_sha256=sha256(raw).hexdigest())


def _part_fixture(layout):
    factory, _ = _migrate(layout)
    _seed_actor(factory)
    part_id, part_space, epoch = _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True))
    return factory, scope, part_id, part_space, epoch


def _add_cowriter(factory, part_id):
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            users.import_legacy_many(connection, (_legacy_user("cowriter"),))
            spaces = SpaceRepository()
            personal = spaces.ensure_personal(connection, space_id=_id(), user_id="cowriter", created_at_ms=1200)
            spaces.update_state(connection, space_id=personal.space.id,
                expected_row_version=personal.space.row_version, status="active", write_enabled=True, updated_at_ms=1201)
            organizations = OrganizationRepository()
            organizations.open_membership(connection, membership_id=_id(), user_id="cowriter", part_id=part_id,
                assigned_by="actor", reason="isolated second Part author", valid_from_ms=1201)
            user = users.require(connection, "cowriter")
            organizations.compare_and_swap_user_part(connection, user_id="cowriter", expected_current_part_id=None,
                target_part_id=part_id, expected_scope_epoch=user.scope_epoch, expected_row_version=user.row_version,
                updated_at_ms=1202)
        return users.require(connection, "cowriter").scope_epoch
    finally:
        connection.close()


def _publish_uploaded(factory, scope, *, raw, prose, actor="actor", epoch, mode="part", filename="reuse.md"):
    preview = _uploaded_preview(factory, scope, actor=actor, epoch=epoch, raw=raw, filename=filename)
    snapshot, wiki = _author(factory, preview, actor, prose=prose)
    command = _command(preview, actor=actor, epoch=epoch, mode=mode, snapshot=snapshot)
    return preview, snapshot, wiki, command


def _release_and_check_search(factory, scope, result, wiki):
    kernel = DurableWorkKernel(factory, HandlerRegistry((CanonicalCurationRescanHandler(factory, scope_resolver=scope).registered_handler(),)),
                               scope_resolver=scope)
    for target in result.targets:
        if target["outcome"] != "created":
            continue
        _, claim = kernel.recover_and_claim(claim_owner="authored-publication-test", lease_ms=30000)
        assert claim is not None and claim.job.job_type == "curation.rescan"
        completed = kernel.run_claim(claim)
        assert completed.job.state == "succeeded", completed
    connection = factory.open(read_only=True)
    try:
        for target in result.targets:
            row = connection.execute("SELECT content_md FROM search_document WHERE item_id=? AND revision_id=?",
                                     (target["item_id"], target["resolved_revision_id"])).fetchone()
            assert row is not None and row[0] == wiki.markdown
    finally:
        connection.close()


@pytest.mark.scenario("UPLOAD-009", "UPLOAD-010")
@pytest.mark.parametrize("mode", ["personal", "part", "both"])
def test_reuploaded_original_keeps_first_authored_body_despite_new_model_prose(isolated_knowledge_layout, mode):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True))
    raw = "# 동일한 원본 파일\n관측 조건과 제외 조건은 함께 적용한다.\n".encode()
    publisher = PublicationService(factory, scope)
    first_preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    first_snapshot, first_wiki = _author(factory, first_preview, "actor", prose="기존에 발행하여 공유한 조건 설명.")
    first = publisher.publish(_command(first_preview, epoch=epoch, mode=mode, snapshot=first_snapshot))
    _release_and_check_search(factory, scope, first, first_wiki)
    second_preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    second_snapshot, second_wiki = _author(factory, second_preview, "actor", prose="동일한 자료를 모델이 다른 문장으로 재작성한 설명.")
    assert second_wiki.markdown != first_wiki.markdown
    command = _command(second_preview, epoch=epoch, mode=mode, snapshot=second_snapshot)
    second = publisher.publish(command)
    assert second.authored_snapshot_id == first_snapshot
    assert second.source_artifact_id != first.source_artifact_id
    assert {row["item_id"] for row in second.targets} == {row["item_id"] for row in first.targets}
    assert all(row["outcome"] == "provenance_added" for row in second.targets)
    assert publisher.publish(replace(command, request_id=_id())) == second
    _release_and_check_search(factory, scope, second, first_wiki)
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == len(first.targets)
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 2 * len(first.targets)
        assert connection.execute("SELECT count(*) FROM authored_wiki_snapshot").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("first_mode", ["personal", "part"])
def test_one_existing_target_supplies_same_stable_body_to_both_targets(isolated_knowledge_layout, first_mode):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 공유 가능한 원본\n같은 원본의 발행 이력을 유지한다.\n".encode()
    _, snapshot, wiki, first_command = _publish_uploaded(factory, scope, raw=raw, prose="처음 공유한 설명.", epoch=epoch, mode=first_mode)
    publisher = PublicationService(factory, scope)
    first = publisher.publish(first_command)
    _release_and_check_search(factory, scope, first, wiki)
    _, _, _, second_command = _publish_uploaded(factory, scope, raw=raw, prose="다르게 재작성한 설명.", epoch=epoch, mode="both")
    second = publisher.publish(second_command)
    assert second.authored_snapshot_id == snapshot
    assert {row["outcome"] for row in second.targets} == {"created", "provenance_added"}
    assert first.targets[0]["item_id"] in {row["item_id"] for row in second.targets}
    _release_and_check_search(factory, scope, second, wiki)


@pytest.mark.parametrize("mode", ["personal", "part", "both"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_extraction_first_reupload_retains_existing_body_without_false_authored_selection(isolated_knowledge_layout, mode, newline):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 이전 추출문 자료\n원본 조건을 보존하고 재업로드 이력을 누적한다.\n".replace("\n", newline).encode()
    publisher = PublicationService(factory, scope)
    preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    first = publisher.publish(_command(preview, epoch=epoch, mode=mode))
    _release_and_check_search(factory, scope, first, SimpleNamespace(markdown=raw.decode()))
    _, candidate, _, command = _publish_uploaded(factory, scope, raw=raw,
        prose="이번 업로드에서 생성한 별도의 모델 표현.", epoch=epoch, mode=mode)
    result = publisher.publish(command)
    assert {row["item_id"] for row in result.targets} == {row["item_id"] for row in first.targets}
    assert result.authored_snapshot_id is None
    assert result.reused_extraction_snapshot_id is not None
    assert all(row["outcome"] == "provenance_added" for row in result.targets)
    assert publisher.publish(replace(command, request_id=_id())) == result
    with pytest.raises(PublicationPreviewIdempotencyError):
        publisher.publish(replace(command, authored_snapshot_id=None))
    _release_and_check_search(factory, scope, result, SimpleNamespace(markdown=raw.decode()))
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == len(first.targets)
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 2 * len(first.targets)
        assert connection.execute("SELECT r.status FROM authored_wiki_snapshot s JOIN publication_authoring_run r ON r.id=s.run_id WHERE s.id=?",
                                  (candidate,)).fetchone()[0] == "ready"
        assert connection.execute("SELECT extraction_snapshot_id FROM publication_operation WHERE id=?",
                                  (first.publication_operation_id,)).fetchone()[0] == result.reused_extraction_snapshot_id
    finally:
        connection.close()


@pytest.mark.parametrize("first_mode", ["personal", "part"])
def test_one_extraction_anchor_supplies_both_and_replays_repeated_reuploads(isolated_knowledge_layout, first_mode):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 이전 공통 자료\r\n조건과 예외를 독립 경로에 유지한다.\r\n".encode()
    publisher = PublicationService(factory, scope)
    preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    first = publisher.publish(_command(preview, epoch=epoch, mode=first_mode))
    _release_and_check_search(factory, scope, first, SimpleNamespace(markdown=raw.decode()))
    for ordinal in range(3):
        _, _, _, command = _publish_uploaded(factory, scope, raw=raw,
            prose=f"재업로드의 표현 {ordinal}.", epoch=epoch, mode="both")
        result = publisher.publish(command)
        assert result.authored_snapshot_id is None and result.reused_extraction_snapshot_id is not None
        assert {row["outcome"] for row in result.targets} == (
            {"created", "provenance_added"} if ordinal == 0 else {"provenance_added"})
        assert first.targets[0]["item_id"] in {row["item_id"] for row in result.targets}
        _release_and_check_search(factory, scope, result, SimpleNamespace(markdown=raw.decode()))
        assert publisher.publish(replace(command, request_id=_id())) == result
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 7
        assert connection.execute("SELECT count(*) FROM publication_extraction_reuse").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0
        created = next(row for row in result.targets if row["item_id"] != first.targets[0]["item_id"])
        metadata = json.loads(connection.execute("SELECT metadata_json FROM knowledge_revision WHERE id=?",
                                                (created["resolved_revision_id"],)).fetchone()[0])
        assert metadata["body_kind"] == "extraction" and "authored_snapshot_id" not in metadata
    finally:
        connection.close()


@pytest.mark.parametrize("first_kind", ["authored", "extraction"])
@pytest.mark.parametrize("equal_body", [False, True])
def test_mixed_extraction_and_authored_native_histories_use_exact_body_and_earliest_kind(isolated_knowledge_layout, first_kind, equal_body):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    publisher = PublicationService(factory, scope)
    # Build the same deterministic Markdown through the real authoring renderer.
    # It can then be both an uploaded extraction and a generated body, without
    # directly inserting a forged snapshot or changing the product renderer.
    seed = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw="# 검사 원문\n공통 조건.\n".encode())
    _, rendered = _author(factory, seed, "actor", prose="공통 조건.")
    raw = rendered.markdown.encode()
    snapshots = []
    results = []
    for mode, kind in (("personal", first_kind), ("part", "extraction" if first_kind == "authored" else "authored")):
        preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
        snapshot, wiki = (_author(factory, preview, "actor", prose="공통 조건." if equal_body else "다른 해석.")
                          if kind == "authored" else (None, SimpleNamespace(markdown=raw.decode())))
        if equal_body:
            assert wiki.markdown == raw.decode()
        result = publisher.publish(_command(preview, epoch=epoch, mode=mode, snapshot=snapshot))
        _release_and_check_search(factory, scope, result, wiki)
        snapshots.append(snapshot)
        results.append(result)
    _, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="세번째 모델 표현.", epoch=epoch, mode="both")
    if not equal_body:
        with pytest.raises(PublicationPreviewAuthorizationError, match="conflicting"):
            publisher.publish(command)
    else:
        reused = publisher.publish(command)
        assert reused.authored_snapshot_id == snapshots[0]
        assert (reused.reused_extraction_snapshot_id is not None) == (first_kind == "extraction")
        assert {row["item_id"] for row in reused.targets} == {result.targets[0]["item_id"] for result in results}
        assert all(row["outcome"] == "provenance_added" for row in reused.targets)
        assert publisher.publish(replace(command, request_id=_id())) == reused
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM publication_operation").fetchone()[0] == (3 if equal_body else 2)
    finally:
        connection.close()


def test_extraction_reuse_receipt_and_both_targets_rollback_together(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 추출문 발행의 원자성\n기존 파트 문서는 그대로 남긴다.\n".encode()
    preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    first = PublicationService(factory, scope).publish(_command(preview, epoch=epoch, mode="part"))
    _release_and_check_search(factory, scope, first, SimpleNamespace(markdown=raw.decode()))
    _, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="새 설명.", epoch=epoch, mode="both")
    def failpoint(name):
        if name == "publication.after_extraction_reuse":
            raise RuntimeError("injected after extraction reuse")
    with pytest.raises(RuntimeError, match="injected"):
        PublicationService(factory, scope, failpoint=failpoint).publish(command)
    connection = factory.open(read_only=True)
    try:
        for table in ("knowledge_item", "knowledge_revision", "item_source", "publication_operation", "publication_target"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        for table in ("publication_extraction_reuse", "publication_authored_selection", "lineage_group", "lineage_member"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM publication_preview WHERE id=?", (command.preview_id,)).fetchone()[0] == "ready"
    finally:
        connection.close()
    assert len(PublicationService(factory, scope).publish(command).targets) == 2


def test_extraction_reuse_history_is_immutable_and_replay_survives_later_deactivation(isolated_knowledge_layout):
    factory, scope, _, part_space, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 기록과 현재 상태\n현재 보관 여부와 과거 업로드의 확정 결과는 별개다.\n".encode()
    publisher = PublicationService(factory, scope)
    preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
    first = publisher.publish(_command(preview, epoch=epoch, mode="part"))
    _release_and_check_search(factory, scope, first, SimpleNamespace(markdown=raw.decode()))
    _, candidate, _, command = _publish_uploaded(factory, scope, raw=raw, prose="재작성된 설명.", epoch=epoch)
    result = publisher.publish(command)
    connection = factory.open()
    try:
        for statement in ("DELETE FROM publication_extraction_reuse WHERE publication_operation_id=?",
                          "UPDATE publication_extraction_reuse SET extraction_snapshot_id=extraction_snapshot_id WHERE publication_operation_id=?"):
            with pytest.raises(sqlite3.IntegrityError, match="extraction_reuse_immutable"), transaction(connection):
                connection.execute(statement, (result.publication_operation_id,))
        # Ready generation is a durable completed candidate, not a failed run.
        with transaction(connection):
            connection.execute("DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (candidate,))
        target = result.targets[0]
        version = connection.execute("SELECT row_version FROM knowledge_item WHERE id=?", (target["item_id"],)).fetchone()[0]
    finally:
        connection.close()
    ItemLifecycleService(factory, scope).deactivate(DeactivateItemCommand(
        principal=SessionPrincipal(user_id="actor", captured_scope_epoch=epoch), space_id=part_space,
        item_id=target["item_id"], expected_item_row_version=version,
        operation_id=_id(), request_id=_id(), reason="이후 명시적으로 문서를 보관"))
    # An acknowledgement retry is historical; it must not re-run current-target
    # reuse or resurrect the item. Removed private candidate text is not needed.
    assert publisher.publish(replace(command, request_id=_id())) == result
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT lifecycle_status FROM knowledge_item WHERE id=?", (target["item_id"],)).fetchone()[0] == "inactive"
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 2
    finally:
        connection.close()


def test_conflicting_personal_and_part_source_histories_do_not_choose_by_target_order(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 같은 자료\n각 경로에 따로 발행된 설명이다.\n".encode()
    publisher = PublicationService(factory, scope)
    snapshots = []
    for mode in ("personal", "part"):
        _, snapshot, wiki, command = _publish_uploaded(factory, scope, raw=raw, prose=f"{mode} 공간만의 기존 설명.", epoch=epoch, mode=mode)
        result = publisher.publish(command)
        _release_and_check_search(factory, scope, result, wiki)
        snapshots.append(snapshot)
    assert snapshots[0] != snapshots[1]
    preview, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="제삼의 설명.", epoch=epoch, mode="both")
    with pytest.raises(PublicationPreviewAuthorizationError, match="conflicting"):
        publisher.publish(command)
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM publication_operation").fetchone()[0] == 2
        assert connection.execute("SELECT status FROM publication_preview WHERE id=?", (preview.preview_id,)).fetchone()[0] == "ready"
    finally:
        connection.close()


@pytest.mark.parametrize("change", ["filename", "raw"])
def test_different_source_identity_is_not_replaced_by_prior_authored_body(isolated_knowledge_layout, change):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 첫 원본\n원본 바이트와 이름은 독립적인 식별 조건이다.\n".encode()
    publisher = PublicationService(factory, scope)
    _, first_snapshot, first_wiki, first_command = _publish_uploaded(factory, scope, raw=raw, prose="처음 설명.", epoch=epoch)
    first = publisher.publish(first_command)
    _release_and_check_search(factory, scope, first, first_wiki)
    _, next_snapshot, next_wiki, command = _publish_uploaded(factory, scope,
        raw=raw if change == "filename" else raw + "새 조건.\n".encode(),
        filename="renamed.md" if change == "filename" else "reuse.md", prose="다른 자료 설명.", epoch=epoch)
    second = publisher.publish(command)
    assert second.authored_snapshot_id == next_snapshot != first_snapshot
    assert second.targets[0]["item_id"] != first.targets[0]["item_id"]
    _release_and_check_search(factory, scope, second, next_wiki)


@pytest.mark.parametrize("first_kind", ["authored", "extraction"])
def test_cowriter_reuses_shared_body_without_previous_authors_private_authoring_body(isolated_knowledge_layout, first_kind):
    factory, scope, part_id, part_space, epoch = _part_fixture(isolated_knowledge_layout)
    other_epoch = _add_cowriter(factory, part_id)
    raw = "# 두 작성자의 원본\nsharedsource 조건과 예외를 구성원 사이에 공유한다.\n".encode()
    publisher = PublicationService(factory, scope)
    if first_kind == "authored":
        _, first_snapshot, first_wiki, first_command = _publish_uploaded(factory, scope, raw=raw,
            prose="sharedsource 최초 파트 공유 설명.", actor="cowriter", epoch=other_epoch)
    else:
        first_preview = _uploaded_preview(factory, scope, actor="cowriter", epoch=other_epoch, raw=raw)
        first_snapshot, first_wiki = None, SimpleNamespace(markdown=raw.decode())
        first_command = _command(first_preview, actor="cowriter", epoch=other_epoch, mode="part")
    first = publisher.publish(first_command)
    _release_and_check_search(factory, scope, first, first_wiki)
    _, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="새 모델이 다시 작성한 문장.",
        epoch=epoch, mode="both")
    connection = factory.open()
    try:
        with transaction(connection):
            if first_snapshot is not None:
                connection.execute("DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (first_snapshot,))
            users = UserRepository()
            user = users.require(connection, "cowriter")
            organizations = OrganizationRepository()
            membership = organizations.get_current_membership(connection, "cowriter")
            now = db_now_ms(connection)
            organizations.close_current_membership(connection, user_id="cowriter",
                expected_membership_id=membership.id, closed_at_ms=now)
            organizations.compare_and_swap_user_part(connection, user_id="cowriter",
                expected_current_part_id=part_id, target_part_id=None,
                expected_scope_epoch=user.scope_epoch, expected_row_version=user.row_version, updated_at_ms=now)
            user = users.require(connection, "cowriter")
            users.transition_account_status(connection, user_id="cowriter", expected_row_version=user.row_version,
                target_status="disabled", changed_at_ms=db_now_ms(connection))
    finally:
        connection.close()
    result = publisher.publish(command)
    assert result.authored_snapshot_id == first_snapshot
    shared = next(row for row in result.targets if row["space_id"] == part_space)
    assert shared["item_id"] == first.targets[0]["item_id"] and shared["outcome"] == "provenance_added"
    _release_and_check_search(factory, scope, result, first_wiki)
    hits = CanonicalRetrievalService(factory, scope_resolver=scope, retrieval=HybridRetrievalRepository()).retrieve_for_space(
        SpaceRetrievalRequest(actor_user_id="actor", actor_scope_epoch=epoch,
            space_id=part_space, query="sharedsource", top_k=20))
    assert {row.item_id for row in hits.ranked_documents} == {shared["item_id"]}
    assert publisher.publish(replace(command, request_id=_id())) == result


@pytest.mark.parametrize("change", ["deactivate", "edit", "same_body_revision"])
@pytest.mark.parametrize("first_kind", ["authored", "extraction"])
def test_reupload_does_not_restore_or_duplicate_user_changed_source_knowledge(isolated_knowledge_layout, change, first_kind):
    factory, scope, _, part_space, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 사용자가 관리하는 원본\n처음 문서와 사용자 변경을 구분한다.\n".encode()
    publisher = PublicationService(factory, scope)
    if first_kind == "authored":
        _, _, first_wiki, first_command = _publish_uploaded(factory, scope, raw=raw, prose="최초 설명.", epoch=epoch)
    else:
        first_preview = _uploaded_preview(factory, scope, actor="actor", epoch=epoch, raw=raw)
        first_wiki = SimpleNamespace(markdown=raw.decode())
        first_command = _command(first_preview, epoch=epoch, mode="part")
    first = publisher.publish(first_command)
    _release_and_check_search(factory, scope, first, first_wiki)
    target = first.targets[0]
    connection = factory.open(read_only=True)
    try:
        version = connection.execute("SELECT row_version FROM knowledge_item WHERE id=?", (target["item_id"],)).fetchone()[0]
    finally:
        connection.close()
    principal = SessionPrincipal(user_id="actor", captured_scope_epoch=epoch)
    if change == "deactivate":
        ItemLifecycleService(factory, scope).deactivate(DeactivateItemCommand(principal=principal,
            space_id=part_space, item_id=target["item_id"], expected_item_row_version=version,
            operation_id=_id(), request_id=_id(), reason="검증에서 명시적으로 보관"))
    else:
        editor = ImmutableItemEditService(factory, scope_resolver=scope,
            key_ring=LeaseHmacKeyRing(active_key_id="test", keys={"test": bytes(range(32))}))
        acquire = AcquireLeaseCommand(principal=principal, space_id=part_space, item_id=target["item_id"],
            editor_session_id=_id(), operation_id=_id(), request_id=_id(),
            raw_token=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode())
        lease = editor.acquire(acquire)
        saved = editor.save(SaveItemCommand(principal=principal, space_id=part_space, item_id=target["item_id"],
            editor_session_id=acquire.editor_session_id, lease_id=lease.lease_id,
            fencing_generation=lease.fencing_generation, expected_item_row_version=version,
            expected_revision_id=target["resolved_revision_id"], operation_id=_id(), request_id=_id(),
            markdown=first_wiki.markdown if change == "same_body_revision" else "# 사용자 수정\n모델이 덮어쓰면 안 되는 새 조건.\n",
            reason="명시적 사용자 수정", raw_token=acquire.raw_token))
        assert saved.revision_id != target["resolved_revision_id"]
        connection = factory.open(read_only=True)
        try:
            assert connection.execute("SELECT count(*) FROM item_source WHERE revision_id=?", (saved.revision_id,)).fetchone()[0] == 0
        finally:
            connection.close()
    # The revised item may enqueue curation. Extract the next file before using
    # the existing queue by claiming the exact registered extraction job type.
    preview, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="새 모델 출력.", epoch=epoch)
    if change == "same_body_revision":
        result = publisher.publish(command)
        assert result.targets[0]["item_id"] == target["item_id"]
        assert result.targets[0]["resolved_revision_id"] == saved.revision_id
        assert result.targets[0]["outcome"] == "provenance_added"
    else:
        with pytest.raises(PublicationPreviewAuthorizationError, match="revised|unavailable"):
            publisher.publish(command)
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM publication_operation").fetchone()[0] == (2 if change == "same_body_revision" else 1)
        assert connection.execute("SELECT status FROM publication_preview WHERE id=?", (preview.preview_id,)).fetchone()[0] == (
            "consumed" if change == "same_body_revision" else "ready")
    finally:
        connection.close()


def test_concurrent_first_publication_invalidates_empty_reuse_preparation(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 동시 발행 원본\n동일 파일이 동시에 들어와도 한 문서만 남긴다.\n".encode()
    _, first_snapshot, first_wiki, first_command = _publish_uploaded(factory, scope, raw=raw, prose="첫 발행 설명.", epoch=epoch)
    preview, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="경쟁 발행 설명.", epoch=epoch)
    winner = []
    class PublishWhileReading(PublicationCommitRepository):
        def freeze_source(self, *args, **kwargs):
            source = super().freeze_source(*args, **kwargs)
            assert source.reuse_fence == ()
            winner.append(PublicationService(factory, scope).publish(first_command))
            return source
    with pytest.raises(PublicationPreviewIdempotencyError, match="stale"):
        PublicationService(factory, scope, commits=PublishWhileReading()).publish(command)
    _release_and_check_search(factory, scope, winner[0], first_wiki)
    retry = PublicationService(factory, scope).publish(command)
    assert retry.authored_snapshot_id == first_snapshot
    assert retry.targets[0]["item_id"] == winner[0].targets[0]["item_id"]
    assert retry.targets[0]["outcome"] == "provenance_added"


def test_repeated_reuploads_keep_one_bounded_reuse_summary_per_target(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 반복 업로드 자료\n업로드 이력은 남기되 지식 본문은 중복 생성하지 않는다.\n".encode()
    sizes = []
    class Capture(PublicationCommitRepository):
        def freeze_source(self, *args, **kwargs):
            source = super().freeze_source(*args, **kwargs)
            sizes.append(len(source.reuse_fence))
            assert len(source.reuse_fence) <= len(source.reuse_targets) == 1
            return source
    publisher = PublicationService(factory, scope, commits=Capture())
    first = None
    for ordinal in range(8):
        _, _, wiki, command = _publish_uploaded(factory, scope, raw=raw, prose=f"모델 표현 버전 {ordinal}.", epoch=epoch)
        result = publisher.publish(command)
        if first is None:
            first = result
            _release_and_check_search(factory, scope, first, wiki)
        else:
            assert result.authored_snapshot_id == first.authored_snapshot_id
            assert result.targets[0]["item_id"] == first.targets[0]["item_id"]
    assert sizes == [0] + [1] * 7
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 8
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 1
    finally:
        connection.close()


def test_equal_standalone_anchors_keep_item_lineages_on_both_reupload(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 동일 본문 원본\n각 공간의 기존 이력은 새 업로드로 재부착하지 않는다.\n".encode()
    publisher = PublicationService(factory, scope)
    existing = []
    snapshots = []
    for mode in ("personal", "part"):
        _, snapshot, wiki, command = _publish_uploaded(factory, scope, raw=raw, prose="같은 최초 설명.", epoch=epoch, mode=mode)
        result = publisher.publish(command)
        _release_and_check_search(factory, scope, result, wiki)
        existing.append(result.targets[0]["item_id"])
        snapshots.append(snapshot)
    assert snapshots[0] != snapshots[1]
    _, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="다르게 작성한 설명.", epoch=epoch, mode="both")
    result = publisher.publish(command)
    assert result.authored_snapshot_id == snapshots[0]
    assert {row["item_id"] for row in result.targets} == set(existing)
    assert all(row["outcome"] == "provenance_added" for row in result.targets)
    connection = factory.open(read_only=True)
    try:
        assert all(row[0] is None for row in connection.execute("SELECT lineage_group_id FROM knowledge_item"))
        # Existing operation-level unchanged-publication contract: a new
        # operation group does not reparent already-existing item histories.
        assert connection.execute("SELECT count(*) FROM lineage_group").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM lineage_member").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 4
    finally:
        connection.close()


def test_changed_reuse_manifest_fence_is_rejected_before_creating_other_target(isolated_knowledge_layout):
    factory, scope, _, _, epoch = _part_fixture(isolated_knowledge_layout)
    raw = "# 재사용 메타데이터\n선택한 본문과 생성 이력을 일치시킨다.\n".encode()
    publisher = PublicationService(factory, scope)
    _, _, wiki, command = _publish_uploaded(factory, scope, raw=raw, prose="첫 설명.", epoch=epoch)
    first = publisher.publish(command)
    _release_and_check_search(factory, scope, first, wiki)
    preview, _, _, command = _publish_uploaded(factory, scope, raw=raw, prose="두번째 설명.", epoch=epoch, mode="both")
    class ChangedManifest(PublicationCommitRepository):
        def freeze_source(self, *args, **kwargs):
            source = super().freeze_source(*args, **kwargs)
            assert source.authored_preparation is not None
            fence = source.authored_fence
            return replace(source, authored_fence=(*fence[:5], "0" * 64, *fence[6:]))
    with pytest.raises(PublicationPreviewIdempotencyError, match="inconsistent"):
        PublicationService(factory, scope, commits=ChangedManifest()).publish(command)
    connection = factory.open(read_only=True)
    try:
        for table in ("publication_operation", "publication_authored_selection", "knowledge_item", "item_source"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM publication_preview WHERE id=?", (preview.preview_id,)).fetchone()[0] == "ready"
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["personal", "part", "both"])
def test_selected_authored_body_is_published_without_replacing_extraction(isolated_knowledge_layout, mode):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True,
        personal_writes=True, part_reads=True, part_writes=True))
    raw = "# 완전한 원본\r\n\r\n설비의 조건·숫자·예외가 담긴 추출문이다.\r\n"
    preview = PublicationPreviewService(factory, scope, _Stager()).create_manual_preview(
        CreateManualPreviewCommand(request_id=_id(), operation_id=_id(), actor_user_id="actor",
            actor_scope_epoch=epoch, markdown=raw))
    snapshot, wiki = _author(factory, preview, "actor")
    command = _command(preview, epoch=epoch, mode=mode, snapshot=snapshot)
    service = PublicationService(factory, scope)
    result = service.publish(command)
    assert result.authored_snapshot_id == snapshot
    assert service.publish(replace(command, request_id=_id())) == result
    history_connection = factory.open()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="authored_selection_immutable"), transaction(history_connection):
            history_connection.execute("DELETE FROM publication_authored_selection WHERE publication_operation_id=?",
                                       (result.publication_operation_id,))
    finally:
        history_connection.close()
    assert service.publish(replace(command, request_id=_id())) == result
    with pytest.raises(PublicationPreviewIdempotencyError):
        service.publish(replace(command, authored_snapshot_id=None))
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT content_md FROM extraction_snapshot_body").fetchone()[0] == raw
        assert connection.execute("SELECT content_md FROM authored_wiki_snapshot_body").fetchone()[0] == wiki.markdown
        assert wiki.markdown != raw
        assert len(result.targets) == (2 if mode == "both" else 1)
        for target in result.targets:
            row = connection.execute("SELECT b.content_md,r.raw_content_sha256,r.metadata_json FROM knowledge_revision r JOIN knowledge_revision_body b ON b.revision_id=r.id WHERE r.id=?",
                                     (target["resolved_revision_id"],)).fetchone()
            assert row[0] == wiki.markdown and row[1] == sha256(wiki.markdown.encode()).hexdigest()
            metadata = json.loads(row[2])
            assert metadata["source_snapshot_sha256"] == sha256(raw.encode()).hexdigest()
            assert metadata["authored_snapshot_id"] == snapshot
            provenance = connection.execute("SELECT artifact_id,extraction_snapshot_id FROM item_source WHERE item_id=?",
                                            (target["item_id"],)).fetchone()
            assert provenance[0] == result.source_artifact_id
        assert connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 1
        if mode == "both":
            assert connection.execute("SELECT sync_policy,base_content_sha256 FROM lineage_group").fetchone()[:] == (
                "none", sha256(wiki.markdown.encode()).hexdigest())
    finally:
        connection.close()
    _release_and_check_search(factory, scope, result, wiki)


@pytest.mark.parametrize("kind", ["team", "responsibility"])
@pytest.mark.parametrize("first_kind", ["authored", "extraction"])
def test_direct_higher_role_publishes_and_indexes_selected_authored_wiki(isolated_knowledge_layout, kind, first_kind):
    factory, _ = _migrate(isolated_knowledge_layout)
    ids = _seed_higher(factory)
    higher = _higher_service(factory)
    if kind == "team":
        higher.set_role(_role_command(factory, ids, operation="authored-team-role", assignee="leader"))
        target = _descriptor(factory, ids)
    else:
        higher.set_role(SetHigherRoleCommand(actor_user_id="admin", actor_scope_epoch=_epoch(factory, "admin"),
            operation_id=_id(), request_id=_id(), org_unit_id=RESPONSIBILITY_ROOT_ID,
            role="responsibility_executive", assignee_user_id="leader", expected_current_assignment_id=None,
            expected_current_assignment_generation=None, expected_current_assignment_row_version=None,
            expected_space_row_version=1, enable_writes=True, reason="isolated authored publication"))
        connection = factory.open(read_only=True)
        try:
            role = HigherSpaceRepository().get_open_role(connection, org_unit_id=RESPONSIBILITY_ROOT_ID,
                                                         role="responsibility_executive")
        finally:
            connection.close()
        target = HigherPublicationTargetDescriptor(space_id=RESPONSIBILITY_ROOT_ID, kind="responsibility",
            capability="publish", role_assignment_id=role.id, role_assignment_generation=role.assignment_generation)
    scope = _higher_scope()
    epoch = _epoch(factory, "leader")
    raw = "# 상위 조직 원본\n공유된 기술의 조건과 예외를 기록한다.\n"
    preview = _uploaded_preview(factory, scope, actor="leader", epoch=epoch, raw=raw.encode(), target=target)
    snapshot, wiki = (_author(factory, preview, "leader") if first_kind == "authored" else
                      (None, SimpleNamespace(markdown=raw)))
    command = _command(preview, actor="leader", epoch=epoch, mode="explicit", target=target, snapshot=snapshot)
    service = PublicationService(factory, scope)
    result = service.publish(command)
    assert result.targets[0]["space_id"] == target.space_id
    assert service.publish(replace(command, request_id=_id())) == result
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT current_part_id FROM app_user WHERE id='leader'").fetchone()[0] is None
        assert connection.execute("SELECT content_md FROM extraction_snapshot_body").fetchone()[0] == raw
        assert connection.execute("SELECT content_md FROM knowledge_revision_body").fetchone()[0] == wiki.markdown
        assert connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == (1 if first_kind == "authored" else 0)
    finally:
        connection.close()
    _release_and_check_search(factory, scope, result, wiki)
    next_preview = _uploaded_preview(factory, scope, actor="leader", epoch=epoch, raw=raw.encode(), target=target)
    next_snapshot, next_wiki = _author(factory, next_preview, "leader", prose="동일 상위 조직 자료를 다르게 재작성한 설명.")
    assert next_wiki.markdown != wiki.markdown
    next_command = _command(next_preview, actor="leader", epoch=epoch, mode="explicit", target=target, snapshot=next_snapshot)
    reused = service.publish(next_command)
    assert reused.authored_snapshot_id == snapshot
    assert (reused.reused_extraction_snapshot_id is not None) == (first_kind == "extraction")
    assert reused.targets[0]["item_id"] == result.targets[0]["item_id"]
    assert reused.targets[0]["outcome"] == "provenance_added"
    assert service.publish(replace(next_command, request_id=_id())) == reused
    _release_and_check_search(factory, scope, reused, wiki)


def test_selected_missing_snapshot_never_falls_back_to_extraction(authored_source):
    case = authored_source
    preview = SimpleNamespace(preview_id=case.preview_id, source_snapshot_sha256=case.plan.source.content_sha256)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(case.factory, scope).publish(_command(preview, snapshot=_id()))
    assert case.connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "ready"
    assert case.connection.execute("SELECT count(*) FROM publication_operation").fetchone()[0] == 0


def test_extraction_fallback_terminalizes_pending_authoring_atomically(authored_source):
    case = authored_source
    preview = SimpleNamespace(preview_id=case.preview_id, source_snapshot_sha256=case.plan.source.content_sha256)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    result = PublicationService(case.factory, scope).publish(_command(preview))
    assert result.authored_snapshot_id is None
    assert tuple(case.connection.execute("SELECT status,failure_code FROM publication_authoring_run").fetchone()) == (
        "extraction_only", "extraction_selected")
    assert case.connection.execute("SELECT content_md FROM knowledge_revision_body").fetchone()[0] == case.plan.source.markdown
    assert case.connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 0


def test_authored_selection_and_both_targets_rollback_with_publication(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_actor_to_part(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True,
        personal_writes=True, part_reads=True, part_writes=True))
    preview = PublicationPreviewService(factory, scope, _Stager()).create_manual_preview(
        CreateManualPreviewCommand(request_id=_id(), operation_id=_id(), actor_user_id="actor",
            actor_scope_epoch=epoch, markdown="# 원본\n실패 시 원본은 유지되어야 한다.\n"))
    snapshot, _ = _author(factory, preview, "actor")
    def failpoint(name):
        if name == "publication.after_authored_selection":
            raise RuntimeError("injected after both targets and authored selection")
    command = _command(preview, epoch=epoch, mode="both", snapshot=snapshot)
    with pytest.raises(RuntimeError, match="injected"):
        PublicationService(factory, scope, failpoint=failpoint).publish(command)
    connection = factory.open(read_only=True)
    try:
        for table in ("publication_operation", "publication_target", "knowledge_item", "knowledge_revision",
                      "item_source", "lineage_group", "publication_authored_selection"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "ready"
        assert connection.execute("SELECT count(*) FROM authored_wiki_snapshot_body").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("marker_owner", ["source", "model"])
def test_author_created_references_survive_rewriting_but_model_does_not_create_them(isolated_knowledge_layout, marker_owner):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    previews = PublicationPreviewService(factory, scope, _Stager())
    def manual(text):
        return previews.create_manual_preview(CreateManualPreviewCommand(request_id=_id(), operation_id=_id(),
            actor_user_id="actor", actor_scope_epoch=1, markdown=text))
    target_raw = "# 별도 참조 대상\n천문 관측 자료를 비교하는 독립적인 지식.\n"
    target = PublicationService(factory, scope).publish(_command(manual(target_raw)))
    _release_and_check_search(factory, scope, target, SimpleNamespace(markdown=target_raw))
    target_id = target.targets[0]["item_id"]
    marker = f"[[item:{target_id}]]"
    raw = "# 새 문서\n원본에서 작성자의 참조 의도를 보존한다.\n" + (marker if marker_owner == "source" else "")
    preview = manual(raw)
    snapshot, wiki = _author(factory, preview, "actor", prose=("모델이 정리한 본문. " +
        (marker if marker_owner == "model" else "원문의 참조 표기를 생략한 문장.")))
    if marker_owner == "source":
        assert marker not in wiki.markdown
    result = PublicationService(factory, scope).publish(_command(preview, snapshot=snapshot))
    _release_and_check_search(factory, scope, result, wiki)
    connection = factory.open(read_only=True)
    try:
        references = connection.execute("SELECT target_item_id FROM knowledge_explicit_reference WHERE source_item_id=?",
                                        (result.targets[0]["item_id"],)).fetchall()
        assert [row[0] for row in references] == ([target_id] if marker_owner == "source" else [])
    finally:
        connection.close()


@pytest.mark.parametrize("loss", ["body", "scope", "expiry"])
def test_prepared_authored_publication_rechecks_state_before_new_effect(authored_source, monkeypatch, loss):
    import code_assist_v1.knowledge_core.repositories.publication_commit as commits_module
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    case = authored_source
    prepared = _finish_chunks(case)
    with transaction(case.connection):
        snapshot = case.repo.complete(case.connection, run_id=case.run_id, actor_user_id="actor", prepared=prepared, now_ms=case.now)
    preview = SimpleNamespace(preview_id=case.preview_id, source_snapshot_sha256=case.plan.source.content_sha256)
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    class ChangeAfterFreeze(PublicationCommitRepository):
        def freeze_source(self, *args, **kwargs):
            result = super().freeze_source(*args, **kwargs)
            if loss == "expiry":
                monkeypatch.setattr(commits_module, "db_now_ms", lambda connection: case.expires)
            else:
                with transaction(case.connection):
                    if loss == "body":
                        case.connection.execute("DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (snapshot,))
                    else:
                        user = UserRepository().require(case.connection, "actor")
                        UserRepository().transition_account_status(case.connection, user_id="actor",
                            expected_row_version=user.row_version, target_status="disabled", changed_at_ms=case.now + 1)
            return result
    with pytest.raises((PublicationPreviewAuthorizationError, PublicationPreviewIdempotencyError)):
        PublicationService(case.factory, scope, commits=ChangeAfterFreeze()).publish(_command(preview, snapshot=snapshot))
    assert case.connection.execute("SELECT status FROM publication_preview").fetchone()[0] == "ready"
    for table in ("publication_operation", "publication_authored_selection", "knowledge_item"):
        assert case.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("same_body", [False, True])
def test_part_cowriters_retrieve_each_others_authored_documents_and_preserve_provenance(isolated_knowledge_layout, same_body):
    factory, _ = _migrate(isolated_knowledge_layout)
    _seed_actor(factory)
    part_id, part_space, actor_epoch = _assign_actor_to_part(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            users.import_legacy_many(connection, (_legacy_user("cowriter"),))
            spaces = SpaceRepository()
            personal = spaces.ensure_personal(connection, space_id=_id(), user_id="cowriter", created_at_ms=1200)
            spaces.update_state(connection, space_id=personal.space.id,
                expected_row_version=personal.space.row_version, status="active",
                write_enabled=True, updated_at_ms=1201)
            organizations = OrganizationRepository()
            organizations.open_membership(connection, membership_id=_id(), user_id="cowriter", part_id=part_id,
                assigned_by="actor", reason="isolated second Part author", valid_from_ms=1201)
            user = users.require(connection, "cowriter")
            organizations.compare_and_swap_user_part(connection, user_id="cowriter", expected_current_part_id=None,
                target_part_id=part_id, expected_scope_epoch=user.scope_epoch, expected_row_version=user.row_version,
                updated_at_ms=1202)
        other_epoch = users.require(connection, "cowriter").scope_epoch
    finally:
        connection.close()
    scope = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        part_reads=True, part_writes=True))
    previews = PublicationPreviewService(factory, scope, _Stager())
    publisher = PublicationService(factory, scope)
    results = []
    epochs = {"actor": actor_epoch, "cowriter": other_epoch}
    for ordinal, actor in enumerate(epochs):
        raw = "# 원본 기술\n운전 조건 및 점검 예외를 함께 검토한다.\n"
        if not same_body:
            raw += ("주제: 천문 관측에서 시료를 제외하는 조건.\n" if ordinal == 0 else
                    "주제: 식품의 향 선호도 실험에서 온도를 통제하는 조건.\n")
        preview = previews.create_manual_preview(CreateManualPreviewCommand(request_id=_id(), operation_id=_id(),
            actor_user_id=actor, actor_scope_epoch=epochs[actor], markdown=raw))
        prose = "sharedqualification 공동 작성 지식 " + ("동일한 조건" if same_body else f"독립 조건 {ordinal}")
        snapshot, wiki = _author(factory, preview, actor, prose=prose)
        result = publisher.publish(_command(preview, actor=actor, epoch=epochs[actor], mode="part", snapshot=snapshot))
        results.append(result)
        _release_and_check_search(factory, scope, result, wiki)
    private = previews.create_manual_preview(CreateManualPreviewCommand(request_id=_id(), operation_id=_id(),
        actor_user_id="actor", actor_scope_epoch=actor_epoch, markdown="# 비공개\nsharedqualification 개인 전용 초안.\n"))
    private_result = publisher.publish(_command(private, epoch=actor_epoch))
    _release_and_check_search(factory, scope, private_result, SimpleNamespace(markdown="# 비공개\nsharedqualification 개인 전용 초안.\n"))
    expected = {result.targets[0]["item_id"] for result in results}
    assert len(expected) == (1 if same_body else 2)
    assert results[1].targets[0]["outcome"] == ("provenance_added" if same_body else "created")
    for actor, epoch in epochs.items():
        retrieval = CanonicalRetrievalService(factory, scope_resolver=scope, retrieval=HybridRetrievalRepository())
        hits = retrieval.retrieve_for_space(SpaceRetrievalRequest(actor_user_id=actor, actor_scope_epoch=epoch,
            space_id=part_space, query="sharedqualification", top_k=20))
        assert {row.item_id for row in hits.ranked_documents} == expected
        assert private_result.targets[0]["item_id"] not in {row.item_id for row in hits.ranked_documents}
    connection = factory.open(read_only=True)
    try:
        publishers = connection.execute("""SELECT DISTINCT a.publisher_user_id FROM item_source s
            JOIN source_artifact a ON a.id=s.artifact_id JOIN knowledge_item i ON i.id=s.item_id
            WHERE i.space_id=?""", (part_space,)).fetchall()
        assert {row[0] for row in publishers} == set(epochs)
        assert connection.execute("SELECT count(*) FROM publication_authored_selection").fetchone()[0] == 2
    finally:
        connection.close()
