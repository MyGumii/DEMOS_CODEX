from __future__ import annotations

from contextlib import contextmanager
import base64
from dataclasses import replace
import hashlib
from types import SimpleNamespace
import os
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.db import ConnectionFactory, TransactionMode, transaction
from code_assist_v1.knowledge_core.legacy.blob_stager import FilesystemLegacyBlobStager
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand, ImmutableItemEditService, LeaseHmacKeyRing, SaveItemCommand,
)
from code_assist_v1.knowledge_core.services import agent_context as context_module
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentContextScopeStaleError, AgentContextStateConflictError, AgentTurnContextService,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _accepted_turn, _context_request, _factory, _flags, _insert_item, _seed,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-012")]


def _blob_fixture(tmp_path, *, factory=None):
    factory = factory or _factory(tmp_path)
    ids = _seed(factory)
    body = "# scopealpha\n\n" + "운영 배경 기록.\n" * 500 + "\n후반 예외: 센서 교체 시 0.75% 미만이어야 한다."
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            item, revision = _insert_item(
                connection, label="canonical-blob", space_id=ids["part-space-a"],
                actor="admin", title="scopealpha 저장 본문", body=body,
                published=True, blob_backed=True,
            )
    finally:
        connection.close()
    return factory, ids, item, revision, body.encode()


def test_runtime_uses_cas_verifier_without_context_transaction_and_closes_lease(tmp_path, monkeypatch):
    factory, ids, item, revision, encoded = _blob_fixture(tmp_path)
    active = []
    calls = []
    closed = []

    @contextmanager
    def tracked_transaction(*args, **kwargs):
        with transaction(*args, **kwargs):
            active.append(True)
            try:
                yield
            finally:
                active.pop()

    def verify_existing(**kwargs):
        assert not active, "CAS I/O must not run in a context SQLite transaction"
        assert kwargs["expected_sha256"] == hashlib.sha256(encoded).hexdigest()
        assert kwargs["expected_size"] == len(encoded)
        calls.append(kwargs)
        return SimpleNamespace(lease=SimpleNamespace(close=lambda: closed.append(True))), encoded

    monkeypatch.setattr(context_module, "transaction", tracked_transaction)
    runtime = compose_canonical_agent_runtime(
        factory, flags=replace(_flags(), hybrid_rerank=True),
        principal_resolver=lambda: None, model_runner=lambda *a, **k: (None, {}),
        source_artifact_stager=SimpleNamespace(verify_existing=verify_existing),
    )
    assert not calls
    bundle = runtime.services.agent_context.compose(_context_request(ids, _accepted_turn(factory, ids)))
    actual = next(row for row in bundle.ranked_contexts if row.item_id == item)
    assert actual.revision_id == revision
    assert actual.excerpt_md.encode() == encoded
    assert len(calls) == len(closed) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows canonical CAS handles")
def test_real_retained_blob_reaches_agent_via_runtime_cas_loader(isolated_knowledge_layout):
    from code_assist_v1.tests.knowledge_spaces.unit.test_sbe_windows_stager import _initialize_layout

    storage = _initialize_layout(isolated_knowledge_layout)
    factory = ConnectionFactory._for_isolated_tests(storage.database, CANONICAL_MIGRATION_CATALOG)
    factory, ids, item, _, encoded = _blob_fixture(None, factory=factory)
    stager = FilesystemLegacyBlobStager(storage)
    staged = stager.stage_and_verify(
        encoded, expected_sha256=hashlib.sha256(encoded).hexdigest(),
        media_type="application/octet-stream", effective_at_ms=200,
        normalized_content_sha256=None, normalizer_version=None,
    )
    staged.lease.close()
    runtime = compose_canonical_agent_runtime(
        factory, flags=replace(_flags(), hybrid_rerank=True),
        principal_resolver=lambda: None, model_runner=lambda *a, **k: (None, {}),
        source_artifact_stager=stager,
    )
    bundle = runtime.services.agent_context.compose(_context_request(ids, _accepted_turn(factory, ids)))
    actual = next(row for row in bundle.ranked_contexts if row.item_id == item)
    assert actual.excerpt_md.encode() == encoded
    assert actual.source_sha256 == hashlib.sha256(encoded).hexdigest()
    assert not actual.truncated


@pytest.mark.parametrize("failure", ["missing_loader", "exception", "utf8", "hash", "size", "type"])
def test_blob_body_integrity_failure_cannot_enter_context(tmp_path, failure):
    factory, ids, _, _, encoded = _blob_fixture(tmp_path)

    def loader(digest, size):
        if failure == "exception":
            raise OSError("Private storage path must not appear in the public error")
        return {"utf8": b"\xff", "hash": b"x" * len(encoded),
                "size": encoded[:-1], "type": {"text": encoded}}[failure]

    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=None if failure == "missing_loader" else loader,
    )
    with pytest.raises(AgentContextStateConflictError, match="A selected canonical body is unavailable"):
        service.compose(_context_request(ids, _accepted_turn(factory, ids)))


@pytest.mark.parametrize("change", ["epoch", "generation"])
def test_blob_read_revalidates_authority_after_external_io(tmp_path, change):
    factory, ids, _, _, encoded = _blob_fixture(tmp_path)
    calls = []

    def loader(digest, size):
        calls.append(digest)
        connection = factory.open()
        try:
            with transaction(connection, TransactionMode.IMMEDIATE):
                if change == "epoch":
                    connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1, row_version=row_version+1 WHERE id='admin'")
                else:
                    connection.execute("UPDATE knowledge_space SET generation=generation+1, row_version=row_version+1 WHERE id=?",
                                       (ids["part-space-a"],))
        finally:
            connection.close()
        return encoded

    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=loader,
    )
    with pytest.raises((AgentContextScopeStaleError, AgentContextStateConflictError)):
        service.compose(_context_request(ids, _accepted_turn(factory, ids)))
    assert len(calls) == 1


def test_real_revision_edit_during_blob_read_rejects_old_body(tmp_path):
    factory, ids, item, revision, encoded = _blob_fixture(tmp_path)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute("UPDATE knowledge_space SET write_enabled=1, row_version=row_version+1 WHERE id=?",
                               (ids["part-space-a"],))
    finally:
        connection.close()
    flags = replace(_flags(), part_writes=True)
    scopes = ScopeResolver(flags)
    editor = ImmutableItemEditService(
        factory, key_ring=LeaseHmacKeyRing(active_key_id="test", keys={"test": b"r" * 32}),
        scope_resolver=scopes,
    )
    principal = SessionPrincipal("admin", int(ids["admin-epoch"]))
    session = str(uuid4())
    token = base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("=")
    lease = editor.acquire(AcquireLeaseCommand(
        principal=principal, space_id=ids["part-space-a"], item_id=item,
        editor_session_id=session, operation_id=str(uuid4()), request_id=str(uuid4()),
        raw_token=token,
    ))
    saved = []

    def loader(digest, size):
        saved.append(editor.save(SaveItemCommand(
            principal=principal, space_id=ids["part-space-a"], item_id=item,
            editor_session_id=session, lease_id=lease.lease_id,
            fencing_generation=lease.fencing_generation, expected_item_row_version=2,
            expected_revision_id=revision, operation_id=str(uuid4()), request_id=str(uuid4()),
            markdown="# scopealpha revised\n\nNew review requires 0.50% instead of 0.75%.\n",
            reason="Revision changed during external body read", raw_token=token,
        )))
        return encoded

    service = AgentTurnContextService(factory, scope_resolver=scopes,
        retrieval=HybridRetrievalRepository(), content_loader=loader)
    with pytest.raises(AgentContextStateConflictError):
        service.compose(_context_request(ids, _accepted_turn(factory, ids)))
    assert len(saved) == 1
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            current = connection.execute("SELECT current_revision_id FROM knowledge_item WHERE id=?", (item,)).fetchone()
            assert current["current_revision_id"] != revision
    finally:
        connection.close()


def test_budget_omitted_blob_requires_metadata_but_no_file_read(tmp_path):
    factory, ids, _, _, encoded = _blob_fixture(tmp_path)
    calls = []
    bodies = {hashlib.sha256(encoded).hexdigest(): encoded}
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(34):
                body = f"# scopealpha {index}\n" + "다중 작성자 배경.\n" * 120
                bodies[hashlib.sha256(body.encode()).hexdigest()] = body.encode()
                _insert_item(connection, label=f"blob-budget-{index}", space_id=ids["part-space-a"],
                             actor="admin", title=f"scopealpha {index}", body=body,
                             published=True, blob_backed=True)
    finally:
        connection.close()
    def loader(digest, size):
        calls.append(digest)
        return bodies[digest]

    bundle = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=loader,
    ).compose(_context_request(ids, _accepted_turn(factory, ids)))
    assert bundle.context_budget.body_omitted_count > 0
    assert len(calls) < len(bodies)
    delivered = {row.source_sha256 for row in bundle.ranked_contexts}
    assert set(calls) == delivered.intersection(bodies)
    assert bundle.context_budget.excerpt_bytes <= 98_304
