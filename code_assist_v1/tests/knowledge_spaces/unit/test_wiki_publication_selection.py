"""Public-intent publication through the actual opt-in queue, no supplied Wiki ID.

Fake model outputs isolate source selection, not semantic compression quality.
"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.migrations.wiki_upload_catalog import WIKI_UPLOAD_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.services.publication import (
    PublicationService, PublicationPreviewService, GetPublicationPreviewQuery, PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.wiki_publication import (
    WikiPublicationSelector, WikiPublicationPendingError, WikiPublicationUnavailableError,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_wiki_upload_workflow import (
    _case, _organization, _migrate, _drain, _run, _claim, _rows, _EXTRACT, _SOURCE, _epoch, _Stager,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("UPLOAD-005", "UPLOAD-009", "UPLOAD-010", "UPLOAD-011")]


def _status(case):
    return case.service.get_preview(GetPublicationPreviewQuery(case.actor,
        _epoch(case.factory, case.actor), case.preview.preview_id))


def _command(case, mode="personal", target=None):
    return PublishPreviewCommand(str(uuid4()), str(uuid4()), case.actor,
        _epoch(case.factory, case.actor), case.preview.preview_id,
        _status(case)["source_snapshot_sha256"], mode, target=target)


def _publisher(case):
    return PublicationService(case.factory, case.scope, wiki_publication=WikiPublicationSelector(case.factory))


def _schema_errors(value, definition):
    from code_assist_v1.tools.contract_audit import ContractAuditor
    docs = Path(__file__).resolve().parents[3] / "docs" / "knowledge_spaces"
    path = docs / "contracts" / "schemas" / "publication.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    return ContractAuditor(docs).validate_instance(value, schema["$defs"][definition], path)


@pytest.fixture
def case(isolated_knowledge_layout):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    return _case(factory, _organization(factory))


@pytest.mark.parametrize("mode", ["personal", "part", "both", "team", "responsibility"])
def test_ordinary_intent_selects_complete_wiki_and_replays_without_client_snapshot(isolated_knowledge_layout, mode):
    factory, _ = _migrate(isolated_knowledge_layout, WIKI_UPLOAD_MIGRATION_CATALOG)
    org = _organization(factory)
    actor = {"team": "leader", "responsibility": "executive"}.get(mode, "member")
    target = getattr(org, mode) if mode in {"team", "responsibility"} else None
    case = _case(factory, org, actor=actor, target=target)
    queued = _status(case)
    assert queued["wiki_generation"] == {"schema_version": 1, "status": "pending"}
    assert not queued["publication_ready"]
    _run(case, _EXTRACT)
    stored_preview = _rows(case, "SELECT ready_result_json FROM publication_preview")
    command = _command(case, "explicit" if target else mode, target)
    assert command.authored_snapshot_id is None
    publisher = _publisher(case)
    with pytest.raises(WikiPublicationPendingError) as error:
        publisher.publish(command)
    assert error.value.http_status == 409 and error.value.retryable is True
    assert _rows(case, "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'") == [(0,)]
    assert _status(case)["status"] == "ready" and not _status(case)["publication_ready"]
    _drain(case)
    ready = _status(case)
    assert ready["wiki_generation"]["status"] == "ready" and ready["publication_ready"]
    assert _rows(case, "SELECT ready_result_json FROM publication_preview") == stored_preview
    result = publisher.publish(command)  # Identical operation ID survives pending.
    assert result.authored_snapshot_id is not None
    assert result.body_source == "authored_wiki"
    assert _status(case)["status"] == "consumed"
    assert _status(case)["wiki_generation"]["status"] == "ready"
    assert _status(case)["publication_ready"] is False
    assert len(result.targets) == (2 if mode == "both" else 1)
    body = _rows(case, "SELECT content_md FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (result.authored_snapshot_id,))[0][0]
    assert body != case.raw.decode()
    assert all(_rows(case, "SELECT content_md FROM knowledge_revision_body WHERE revision_id=?",
        (row["resolved_revision_id"],)) == [(body,)] for row in result.targets)
    # Purgeable source bodies and the consumed preview cannot control a retry.
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute("DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (result.authored_snapshot_id,))
    finally:
        connection.close()
    assert publisher.publish(replace(command, request_id=str(uuid4()))) == result


def test_failed_generation_with_retained_extraction_is_labeled_and_publishable(case):
    _drain(case)
    # Fail initial planning by a valid, frozen whole-source budget, not fake DB status.
    from code_assist_v1.knowledge_core.services.wiki_upload import WikiUploadPolicy
    factory, org = case.factory, case.org
    policy = WikiUploadPolicy()
    other = _case(factory, org, policy=replace(policy, work=replace(policy.work, source_bytes=20)))
    _run(other, _EXTRACT)
    assert other.kernel.run_claim(_claim(other, _SOURCE)).job.state == "failed"
    value = _status(other)
    assert value["wiki_generation"]["status"] == "extraction_only" and value["publication_ready"]
    result = _publisher(other).publish(_command(other))
    assert result.authored_snapshot_id is None
    assert result.body_source == "extraction"
    from code_assist_v1.knowledge_core.canonical_routes import _publication_result
    assert _publication_result(result)["body_source"] == "extraction"
    assert _rows(other, "SELECT content_md FROM knowledge_revision_body WHERE revision_id=?",
        (result.targets[0]["resolved_revision_id"],)) == [(other.raw.decode(),)]


def test_cancelled_extraction_is_never_a_successful_fallback(case):
    case.kernel.request_cancel(case.preview.job_id)
    _drain(case)
    value = _status(case)
    assert value["wiki_generation"]["status"] == "unavailable" and not value["publication_ready"]
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(0,)]
    command = PublishPreviewCommand(str(uuid4()), str(uuid4()), case.actor,
        _epoch(case.factory, case.actor), case.preview.preview_id, "0" * 64, "personal")
    with pytest.raises(WikiPublicationUnavailableError) as error:
        _publisher(case).publish(command)
    assert error.value.http_status == 409 and error.value.retryable is False
    assert _rows(case, "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'") == [(0,)]


def test_missing_generated_body_never_silently_publishes_raw(case):
    _drain(case)
    command = _command(case)
    connection = case.factory.open()
    try:
        with transaction(connection):
            connection.execute("DELETE FROM authored_wiki_snapshot_body")
    finally:
        connection.close()
    assert _status(case)["wiki_generation"]["status"] == "ready"
    assert _status(case)["publication_ready"] is False
    with pytest.raises(WikiPublicationUnavailableError):
        _publisher(case).publish(command)
    assert _rows(case, "SELECT count(*) FROM publication_operation") == [(0,)]


def test_legacy_preview_is_unchanged_even_when_selector_is_composed(case):
    legacy = PublicationPreviewService(case.factory, case.scope, _Stager(),
        wiki_publication=WikiPublicationSelector(case.factory))
    case.service = legacy
    case.preview = legacy.create_upload_preview(replace(case.command, operation_id=str(uuid4()), request_id=str(uuid4())))
    _drain(case)
    assert "wiki_generation" not in _status(case) and "publication_ready" not in _status(case)
    command = _command(case)
    publisher = _publisher(case)
    result = publisher.publish(command)
    assert result.authored_snapshot_id is None
    assert publisher.publish(command) == result


@pytest.mark.parametrize("fallback", [False, True])
def test_actual_http_publication_selects_server_body_and_never_accepts_snapshot_id(case, fallback):
    from flask import Flask
    from code_assist_v1.knowledge_core.canonical_routes import register_publication_routes
    from code_assist_v1.tests.knowledge_spaces.unit.test_publication_routes import _ScopeSelection
    if fallback:
        from code_assist_v1.knowledge_core.services.wiki_upload import WikiUploadPolicy
        case.kernel.request_cancel(case.preview.job_id)
        _drain(case)
        policy = WikiUploadPolicy()
        case = _case(case.factory, case.org, policy=replace(policy, work=replace(policy.work, source_bytes=20)))
    publisher = _publisher(case)
    app = Flask("isolated-wiki-selection")
    service = SimpleNamespace(get_preview=case.service.get_preview, publish=publisher.publish,
        create_upload_preview=case.service.create_upload_preview,
        create_manual_preview=case.service.create_manual_preview,
        create_chat_preview=case.service.create_chat_preview,
        create_notion_preview=case.service.create_notion_preview,
        cancel_preview=case.service.cancel_preview)
    register_publication_routes(app, scope_selection_service=_ScopeSelection(), publication_service=service,
        principal_resolver=lambda: {"id": case.actor, "scope_epoch": _epoch(case.factory, case.actor)})
    client = app.test_client()
    url = f"/api/knowledge/v1/publication-previews/{case.preview.preview_id}"
    queued = client.get(url)
    assert queued.status_code == 200 and queued.json["wiki_generation"]["status"] == "pending"
    _run(case, _EXTRACT)
    body = dict(operation_id=str(uuid4()), preview_id=case.preview.preview_id,
        source_snapshot_sha256=client.get(url).json["source_snapshot_sha256"], target_mode="part")
    pending = client.post("/api/knowledge/v1/publications", json=body)
    assert pending.status_code == 409 and pending.json["error"]["retryable"] is True
    assert client.post("/api/knowledge/v1/publications", json={**body, "authored_snapshot_id": str(uuid4())}).status_code == 400
    if fallback:
        assert case.kernel.run_claim(_claim(case, _SOURCE)).job.state == "failed"
    else:
        _drain(case)
    ready = client.get(url)
    assert ready.json["publication_ready"] is True
    assert "authored_snapshot_id" not in ready.json
    committed = client.post("/api/knowledge/v1/publications", json=body)
    assert committed.status_code == 201, committed.json
    assert committed.json["body_source"] == ("extraction" if fallback else "authored_wiki")
    assert set(committed.json) == {"request_id", "operation_id", "results", "body_source"}
    assert client.post("/api/knowledge/v1/publications", json=body).json == committed.json
    consumed = client.get(url)
    assert consumed.json["status"] == "consumed" and not consumed.json["publication_ready"]
    assert consumed.json["wiki_generation"]["status"] == ("extraction_only" if fallback else "ready")
    for response in (queued, ready, consumed):
        assert not _schema_errors(response.json, "PublicationPreviewResponse")
    assert not _schema_errors(committed.json, "PublicationResponse")
    invalid = dict(ready.json, publication_ready="true")
    assert _schema_errors(invalid, "PublicationPreviewResponse")
    invalid = dict(consumed.json, publication_ready=True)
    assert _schema_errors(invalid, "PublicationPreviewResponse")
    invalid = dict(ready.json)
    del invalid["publication_ready"]
    assert _schema_errors(invalid, "PublicationPreviewResponse")
    invalid = dict(ready.json)
    del invalid["wiki_generation"]
    assert _schema_errors(invalid, "PublicationPreviewResponse")


@pytest.mark.parametrize("first_kind", ["authored", "extraction"])
def test_cowriter_public_intent_reuses_actual_prior_body_and_replays_exactly(case, first_kind):
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _release_and_check_search
    from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _hits
    from code_assist_v1.knowledge_core.canonical_routes import _publication_result
    if first_kind == "extraction":
        # The first source predates opt-in generation, but is a real upload.
        _drain(case)
        case.service = PublicationPreviewService(case.factory, case.scope, _Stager())
        case.preview = case.service.create_upload_preview(replace(case.command,
            operation_id=str(uuid4()), request_id=str(uuid4())))
    _drain(case)
    first = _publisher(case).publish(_command(case, "part"))
    first_body = _rows(case, "SELECT content_md FROM knowledge_revision_body WHERE revision_id=?",
        (first.targets[0]["resolved_revision_id"],))[0][0]
    _release_and_check_search(case.factory, case.scope, first, SimpleNamespace(markdown=first_body))
    other = _case(case.factory, case.org, actor="peer", raw=case.raw)
    _drain(other)
    prepared_id = _rows(other, """SELECT s.id FROM authored_wiki_snapshot s JOIN publication_authoring_run r
        ON r.id=s.run_id WHERE r.preview_id=?""", (other.preview.preview_id,))[0][0]
    command = _command(other, "part")
    publisher = _publisher(other)
    result = publisher.publish(command)
    assert result.targets[0]["item_id"] == first.targets[0]["item_id"]
    assert result.targets[0]["outcome"] == "provenance_added"
    public = _publication_result(result)
    assert public["body_source"] == ("authored_wiki" if first_kind == "authored" else "extraction")
    assert "authored_snapshot_id" not in public and "reused_extraction_snapshot_id" not in public
    if first_kind == "authored":
        assert result.authored_snapshot_id == first.authored_snapshot_id != prepared_id
    else:
        assert result.authored_snapshot_id is None and result.reused_extraction_snapshot_id is not None
    connection = case.factory.open()
    try:
        with transaction(connection):
            connection.execute("DELETE FROM authored_wiki_snapshot_body")
    finally:
        connection.close()
    # Current candidate body was purged; replay still returns actual A1/E1.
    assert publisher.publish(replace(command, request_id=str(uuid4()))) == result
    if first_kind == "authored":
        for actor in (case.actor, other.actor):
            assert _hits(case.factory, case.scope, actor=actor, space=case.org.ids["part_space_a"]) == {first.targets[0]["item_id"]}


def test_ten_public_upload_intents_publish_twelve_items_with_exact_shared_and_private_views(case):
    from code_assist_v1.tests.knowledge_spaces.unit.test_authored_publication import _release_and_check_search
    from code_assist_v1.tests.knowledge_spaces.unit.test_synthesized_publication import _hits
    # Cancel the fixture's initial unused upload; the matrix owns ten originals.
    case.kernel.request_cancel(case.preview.job_id)
    _drain(case)
    org, factory = case.org, case.factory
    matrix = [(actor, mode, None) for actor in ("member", "peer") for mode in ("personal", "part", "both")]
    matrix += [(actor, mode, target if mode == "explicit" else None)
        for actor, target in (("leader", org.team), ("executive", org.responsibility))
        for mode in ("personal", "explicit")]
    jobs = []
    for index, (actor, mode, target) in enumerate(matrix):
        raw = (f"# 다중 업로드 {index} {actor} {mode}\n" + "조건과 예외를 함께 검토한다.\n" * 22).encode()
        current = _case(factory, org, actor=actor, target=target, raw=raw)
        _run(current, _EXTRACT)
        assert not _status(current)["publication_ready"]
        jobs.append((current, mode, target))
    assert _rows(case, "SELECT count(*) FROM extraction_snapshot_body") == [(10,)]
    _drain(current)
    private = {actor: set() for actor in ("member", "peer", "leader", "executive")}
    native = {org.ids["part_space_a"]: set(), org.team.space_id: set(), org.responsibility.space_id: set()}
    for current, mode, target in jobs:
        publisher, command = _publisher(current), _command(current, mode, target)
        assert command.authored_snapshot_id is None
        result = publisher.publish(command)
        assert publisher.publish(command) == result
        body = _rows(case, "SELECT content_md FROM authored_wiki_snapshot_body WHERE snapshot_id=?", (result.authored_snapshot_id,))[0][0]
        _release_and_check_search(factory, org.scope, result, SimpleNamespace(markdown=body))
        for row in result.targets:
            (native[row["space_id"]] if row["space_id"] in native else private[current.actor]).add(row["item_id"])
    part = native[org.ids["part_space_a"]]
    team = native[org.team.space_id] | part
    responsibility = native[org.responsibility.space_id] | team
    assert (len(part), len(team), len(responsibility)) == (4, 5, 6)
    assert _rows(case, "SELECT count(*) FROM knowledge_item") == [(12,)]
    for actor in ("member", "peer"):
        for space, expected in ((org.ids["part_space_a"], part), (org.team.space_id, team), (org.responsibility.space_id, responsibility)):
            assert _hits(factory, org.scope, actor=actor, space=space) == expected
    assert _hits(factory, org.scope, actor="leader", space=org.team.space_id) == team
    assert _hits(factory, org.scope, actor="executive", space=org.responsibility.space_id) == responsibility
    for actor in private:
        assert _hits(factory, org.scope, actor=actor, space=org.personal[actor]) == private[actor]
        assert private[actor].isdisjoint(responsibility)


def test_blob_extraction_fallback_is_not_rejected_by_inline_only_authoring_admission(case, monkeypatch):
    from code_assist_v1.knowledge_core.repositories.artifacts import ArtifactRepository
    original_put = ArtifactRepository.put_source_extraction
    def put_blob(self, connection, *, extraction, **kwargs):
        # Choose the legal alternative before initial immutable insertion. Raw
        # and extracted UTF-8 bytes are identical in this isolated fixture.
        body = replace(extraction.body, content_md=None, content_blob_hash=extraction.body.content_sha256)
        return original_put(self, connection, extraction=replace(extraction, body=body), **kwargs)
    monkeypatch.setattr(ArtifactRepository, "put_source_extraction", put_blob)
    _run(case, _EXTRACT)
    assert _status(case)["wiki_generation"]["status"] == "pending"
    # Reconciliation rejects this unsupported authoring input but keeps the
    # publication-compatible retained extraction, without any model call.
    _drain(case)
    value = _status(case)
    assert value["wiki_generation"]["status"] == "extraction_only" and value["publication_ready"]
    publisher, command = _publisher(case), _command(case)
    result = publisher.publish(command)
    assert result.authored_snapshot_id is None
    assert _rows(case, "SELECT content_md,content_blob_hash FROM knowledge_revision_body WHERE revision_id=?",
        (result.targets[0]["resolved_revision_id"],)) == [(None, command.source_snapshot_sha256)]
    assert publisher.publish(command) == result


def test_plain_service_cannot_bypass_a_registered_upload_generation(case):
    _run(case, _EXTRACT)
    plain = PublicationService(case.factory, case.scope)
    command = _command(case)
    with pytest.raises(WikiPublicationPendingError):
        plain.publish(command)
    _drain(case)
    result = plain.publish(command)
    assert result.body_source == "authored_wiki" and result.authored_snapshot_id is not None
    assert plain.publish(command) == result


@pytest.mark.parametrize("opt_in", [False, True])
def test_source_kind_presence_is_bound_to_immutable_upload_request(case, opt_in):
    from code_assist_v1.knowledge_core.repositories.publication_commit import PublicationCommitIntegrityError
    from code_assist_v1.knowledge_core.services.wiki_publication import publication_replay_options
    if not opt_in:
        _drain(case)
        case.service = PublicationPreviewService(case.factory, case.scope, _Stager())
        case.preview = case.service.create_upload_preview(replace(case.command,
            operation_id=str(uuid4()), request_id=str(uuid4())))
    _drain(case)
    command = _command(case)
    result = _publisher(case).publish(command)
    changed = replace(result, body_source=None if opt_in else "extraction")
    connection = case.factory.open(read_only=True)
    try:
        with pytest.raises(PublicationCommitIntegrityError, match="source-kind presence"):
            publication_replay_options(connection, actor_id=case.actor, operation_id=command.operation_id,
                stored=changed, requested_authored_snapshot_id=None)
    finally:
        connection.close()
