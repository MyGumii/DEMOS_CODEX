from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    transaction,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.candidate_retrieval import (
    CANDIDATE_NORMALIZER_VERSION,
    CANDIDATE_RETRIEVAL_MODE,
    CANDIDATE_RETRIEVAL_RULE_VERSION,
    CandidateRetrievalConflictError,
    CandidateRetrievalIntegrityError,
    CandidateRetrievalRepository,
    IncrementalCandidate,
    validate_incremental_candidate_snapshot,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.candidate_retrieval import (
    IncrementalCandidateRequest,
    IncrementalCandidateRetrievalService,
)
from code_assist_v1.knowledge_core.services.curation import (
    DETERMINISTIC_NORMALIZER_VERSION,
)
from code_assist_v1.knowledge_core.services import candidate_retrieval as service_module
from code_assist_v1.knowledge_core.repositories import candidate_retrieval as repository_module
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("edac42ca-9092-47b4-8be2-812c0b3c8c59")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(tmp_path: Path) -> ConnectionFactory:
    layout = create_isolated_test_layout(tmp_path, user_id="fixture-user")
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p6.2-candidate-retrieval-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            spaces = SpaceRepository()
            users.import_legacy_many(
                connection,
                (
                    LegacyUserRecord(
                        id="alice",
                        display_name="Alice",
                        password_hash="scrypt:p6.2:alice",
                        system_role="admin",
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    ),
                    LegacyUserRecord(
                        id="bob",
                        display_name="Bob",
                        password_hash="scrypt:p6.2:bob",
                        system_role="user",
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    ),
                ),
            )
            spaces.ensure_personal(
                connection,
                space_id=_uuid("space:alice"),
                user_id="alice",
                created_at_ms=110,
            )
            spaces.ensure_personal(
                connection,
                space_id=_uuid("space:bob"),
                user_id="bob",
                created_at_ms=110,
            )
    finally:
        connection.close()
    return factory


def _publish(
    connection,
    *,
    label: str,
    item_id: str,
    revision_id: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
) -> None:
    finding_id = _uuid(f"finding:{label}")
    dedup_id = _uuid(f"dedup:{label}")
    operation_id = _uuid(f"operation:{label}")
    transition_id = _uuid(f"transition:{label}")
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,300,NULL,300)""",
        (dedup_id, actor, operation_id, "a" * 64),
    )
    connection.execute(
        """INSERT INTO curation_finding(
             id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
             required_reviewer,visibility_effect,proposed_action,source_item_id,
             source_revision_id,target_item_id,target_revision_id,detector_name,
             detector_version,rule_version,model_id,prompt_version,confidence,
             evidence_json,proposed_action_json,reviewed_by,reviewed_at,review_reason,
             applied_operation_id,applied_dedup_id,applied_job_id,applied_by,
             applied_at,last_application_error_json,created_actor_kind,created_by,
             created_at,updated_at,row_version
           ) VALUES (?,?,'curation_release','T2','info','pending','human',
             'space_editor','agent_hidden','publish',?,?,NULL,NULL,'p6.2','v1','r1',
             NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
             'user',?,310,310,1)""",
        (finding_id, space_id, item_id, revision_id, actor),
    )
    connection.execute(
        """UPDATE curation_finding SET status='approved',reviewed_by=?,reviewed_at=320,
             review_reason='approved',updated_at=320,row_version=2 WHERE id=?""",
        (actor, finding_id),
    )
    connection.execute(
        """UPDATE curation_finding SET status='applied',applied_operation_id=?,
             applied_dedup_id=?,applied_by=?,applied_at=330,updated_at=330,
             row_version=3 WHERE id=?""",
        (operation_id, dedup_id, actor, finding_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
             NULL,?,?,'user',?,2,'publish',330)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            actor,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item SET curation_status='published',
             last_curation_transition_id=?,updated_by=?,updated_at=330,row_version=2
             WHERE id=?""",
        (transition_id, actor, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,330)""",
        (item_id, space_id, revision_id, title, body),
    )


def _insert_item(
    connection,
    *,
    label: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
    published: bool,
    stale_search_row: bool = False,
) -> tuple[str, str]:
    item_id = _uuid(f"item:{label}")
    revision_id = _uuid(f"revision:{label}")
    encoded = body.encode("utf-8")
    raw_sha = hashlib.sha256(encoded).hexdigest()
    normalized_sha = hashlib.sha256(
        body.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    ).hexdigest()
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,200,200,1)""",
        (item_id, space_id, revision_id, actor, actor),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1','{}','initial',
                     'P6.2 fixture',?,200)""",
        (
            revision_id,
            item_id,
            title,
            raw_sha,
            normalized_sha,
            len(encoded),
            actor,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,200)""",
        (revision_id, item_id, body, len(encoded), raw_sha),
    )
    if published:
        _publish(
            connection,
            label=label,
            item_id=item_id,
            revision_id=revision_id,
            space_id=space_id,
            actor=actor,
            title=title,
            body=body,
        )
    elif stale_search_row:
        connection.execute(
            """INSERT INTO search_document(
                 item_id,space_id,revision_id,title,content_md,updated_at
               ) VALUES (?,?,?,?,?,300)""",
            (item_id, space_id, revision_id, title, body),
        )
    return item_id, revision_id


def _seed_candidates(factory: ConnectionFactory) -> dict[str, str]:
    result = {
        "alice_space": _uuid("space:alice"),
        "bob_space": _uuid("space:bob"),
    }
    connection = factory.open()
    try:
        with transaction(connection):
            source_body = "# Distributed cache protocol\nalpha cache invalidation quorum\n"
            result["source_body"] = source_body
            result["source"], result["source_revision"] = _insert_item(
                connection,
                label="source",
                space_id=result["alice_space"],
                actor="alice",
                title="Distributed cache protocol",
                body=source_body,
                published=False,
            )
            for label, body in (
                ("candidate-a", "# Cache invalidation\nalpha quorum cache design\n"),
                ("candidate-b", "# Distributed cache\nprotocol quorum notes\n"),
                ("candidate-c", "# Cache operations\nalpha operational guide\n"),
            ):
                result[label], result[f"{label}_revision"] = _insert_item(
                    connection,
                    label=label,
                    space_id=result["alice_space"],
                    actor="alice",
                    title=label,
                    body=body,
                    published=True,
                )
            result["provisional"], _ = _insert_item(
                connection,
                label="provisional",
                space_id=result["alice_space"],
                actor="alice",
                title="Provisional cache leak",
                body="# alpha cache provisional leak\n",
                published=False,
                stale_search_row=True,
            )
            result["foreign"], _ = _insert_item(
                connection,
                label="foreign",
                space_id=result["bob_space"],
                actor="bob",
                title="Foreign cache canary",
                body="# alpha cache foreign canary\n",
                published=True,
            )
    finally:
        connection.close()
    return result


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "knowledge_item",
                    "knowledge_revision",
                    "curation_finding",
                    "audit_event",
                    "outbox_event",
                    "search_document",
                )
            }
    finally:
        connection.close()


def _retrieve(factory: ConnectionFactory, ids: dict[str, str], *, top_k: int = 2):
    return IncrementalCandidateRetrievalService(factory).retrieve(
        IncrementalCandidateRequest(
            space_id=ids["alice_space"],
            source_item_id=ids["source"],
            source_revision_id=ids["source_revision"],
            source_markdown=ids["source_body"],
            top_k=top_k,
        )
    )


@pytest.mark.scenario("CUR-002")
def test_changed_item_only_top_k_is_same_space_current_published_and_body_free(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    before = _counts(factory)
    snapshot = _retrieve(factory, ids, top_k=2)

    assert snapshot.rule_version == CANDIDATE_RETRIEVAL_RULE_VERSION
    assert snapshot.retrieval_mode == CANDIDATE_RETRIEVAL_MODE
    assert snapshot.fts_query_count == 1
    assert snapshot.all_pairs_scan_count == 0
    assert snapshot.model_call_count == 0
    assert len(snapshot.candidates) == 2
    assert [candidate.rank for candidate in snapshot.candidates] == [1, 2]
    candidate_ids = {candidate.item_id for candidate in snapshot.candidates}
    assert ids["source"] not in candidate_ids
    assert ids["foreign"] not in candidate_ids
    assert ids["provisional"] not in candidate_ids
    assert all(set(candidate.as_evidence()) == {
        "bm25_score",
        "content_size_bytes",
        "item_id",
        "match_kind",
        "normalized_content_sha256",
        "rank",
        "raw_content_sha256",
        "revision_id",
        "title_sha256",
    } for candidate in snapshot.candidates)
    serialized = json.dumps(snapshot.as_evidence(), sort_keys=True)
    assert ids["source_body"] not in serialized
    assert "foreign canary" not in serialized
    assert validate_incremental_candidate_snapshot(snapshot) == snapshot
    assert _counts(factory) == before


@pytest.mark.scenario("CUR-002")
def test_normalized_only_result_is_evidence_and_never_an_automatic_effect(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    space_id = _uuid("space:alice")
    source_body = "# Line endings\r\nnormalized alpha candidate\r\n"
    connection = factory.open()
    try:
        with transaction(connection):
            source, revision = _insert_item(
                connection,
                label="normalized-source",
                space_id=space_id,
                actor="alice",
                title="Line endings",
                body=source_body,
                published=False,
            )
            target, _ = _insert_item(
                connection,
                label="normalized-target",
                space_id=space_id,
                actor="alice",
                title="Line endings target",
                body=source_body.replace("\r\n", "\n"),
                published=True,
            )
    finally:
        connection.close()
    snapshot = IncrementalCandidateRetrievalService(factory).retrieve(
        IncrementalCandidateRequest(
            space_id=space_id,
            source_item_id=source,
            source_revision_id=revision,
            source_markdown=source_body,
            top_k=5,
        )
    )
    row = next(candidate for candidate in snapshot.candidates if candidate.item_id == target)
    assert row.match_kind == "normalized"
    assert not hasattr(row, "automatic_effect")
    assert snapshot.all_pairs_scan_count == snapshot.model_call_count == 0


def test_stale_source_body_revision_or_space_fails_closed_without_writes(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    before = _counts(factory)
    service = IncrementalCandidateRetrievalService(factory)
    base = IncrementalCandidateRequest(
        space_id=ids["alice_space"],
        source_item_id=ids["source"],
        source_revision_id=ids["source_revision"],
        source_markdown=ids["source_body"],
    )
    for request in (
        replace(base, source_markdown=ids["source_body"] + "changed"),
        replace(base, source_revision_id=_uuid("revision:missing")),
        replace(base, space_id=ids["bob_space"]),
    ):
        with pytest.raises(CandidateRetrievalConflictError):
            service.retrieve(request)
    assert _counts(factory) == before


def test_stale_fts_projection_body_fails_closed_instead_of_becoming_evidence(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE search_document
                      SET content_md='# Cache invalidation forged alpha body\n'
                    WHERE item_id=?""",
                (ids["candidate-a"],),
            )
    finally:
        connection.close()
    with pytest.raises(CandidateRetrievalIntegrityError):
        _retrieve(factory, ids, top_k=20)


def test_stale_fts_projection_title_fails_closed(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "UPDATE search_document SET title='forged cache title' WHERE item_id=?",
                (ids["candidate-a"],),
            )
    finally:
        connection.close()
    with pytest.raises(CandidateRetrievalIntegrityError):
        _retrieve(factory, ids, top_k=20)


def test_no_searchable_terms_runs_no_fts_query(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    space_id = _uuid("space:alice")
    connection = factory.open()
    try:
        with transaction(connection):
            source, revision = _insert_item(
                connection,
                label="punctuation-only",
                space_id=space_id,
                actor="alice",
                title="!!",
                body="#\n",
                published=False,
            )
    finally:
        connection.close()
    snapshot = IncrementalCandidateRetrievalService(factory).retrieve(
        IncrementalCandidateRequest(
            space_id=space_id,
            source_item_id=source,
            source_revision_id=revision,
            source_markdown="#\n",
        )
    )
    assert snapshot.query_term_count == 0
    assert snapshot.fts_query_count == 0
    assert snapshot.candidates == ()


def test_result_order_and_hash_are_stable_and_sql_bounded(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    first = _retrieve(factory, ids, top_k=3)
    second = _retrieve(factory, ids, top_k=3)
    assert first == second
    assert len(first.candidates) == 3
    assert first.evidence_sha256 == second.evidence_sha256
    source = inspect.getsource(CandidateRetrievalRepository.retrieve_changed_item)
    assert "knowledge_fts MATCH ?" in source
    assert "LIMIT ?" in source
    assert "document.space_id=?" in source
    assert "document.item_id<>?" in source
    assert "item.curation_status='published'" in source


def test_public_snapshot_validator_rejects_forged_execution_or_candidate_evidence(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    snapshot = _retrieve(factory, ids)
    with pytest.raises(CandidateRetrievalIntegrityError):
        validate_incremental_candidate_snapshot(
            replace(snapshot, all_pairs_scan_count=1)  # type: ignore[arg-type]
        )
    with pytest.raises(CandidateRetrievalIntegrityError):
        validate_incremental_candidate_snapshot(replace(snapshot, model_call_count=1))  # type: ignore[arg-type]
    with pytest.raises(CandidateRetrievalIntegrityError):
        validate_incremental_candidate_snapshot(
            replace(
                snapshot,
                candidates=(
                    replace(snapshot.candidates[0], rank=2),
                    *snapshot.candidates[1:],
                ),
            )
        )
    forged = IncrementalCandidate(
        item_id=snapshot.candidates[0].item_id,
        revision_id=snapshot.candidates[0].revision_id,
        rank=1,
        bm25_score=float("nan"),
        match_kind="lexical",
        title_sha256="a" * 64,
        raw_content_sha256="b" * 64,
        normalized_content_sha256="c" * 64,
        content_size_bytes=1,
    )
    with pytest.raises(CandidateRetrievalIntegrityError):
        validate_incremental_candidate_snapshot(
            replace(snapshot, candidates=(forged,), evidence_sha256="d" * 64)
        )
    with pytest.raises(CandidateRetrievalIntegrityError):
        validate_incremental_candidate_snapshot(
            replace(
                snapshot,
                candidates=(
                    replace(snapshot.candidates[0], match_kind="exact"),
                    *snapshot.candidates[1:],
                ),
                evidence_sha256="e" * 64,
            )
        )


def test_candidate_modules_have_no_model_network_or_write_surface() -> None:
    source = inspect.getsource(repository_module) + inspect.getsource(service_module)
    lowered = source.casefold()
    for forbidden in (
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "pathlib",
        "read_text(",
        "read_bytes(",
        "write_text(",
        "write_bytes(",
        "insert into",
        "update ",
        "delete from",
        "replace into",
    ):
        assert forbidden not in lowered
    assert "self._factory.open(read_only=true)" in lowered
    assert CANDIDATE_NORMALIZER_VERSION == DETERMINISTIC_NORMALIZER_VERSION


def test_query_limits_and_repository_transaction_ownership(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed_candidates(factory)
    service = IncrementalCandidateRetrievalService(factory)
    for top_k in (0, 21, True):
        with pytest.raises(ValueError, match="top_k"):
            service.retrieve(
                IncrementalCandidateRequest(
                    space_id=ids["alice_space"],
                    source_item_id=ids["source"],
                    source_revision_id=ids["source_revision"],
                    source_markdown=ids["source_body"],
                    top_k=top_k,  # type: ignore[arg-type]
                )
            )
    connection = factory.open(read_only=True)
    try:
        with pytest.raises(Exception, match="transaction"):
            CandidateRetrievalRepository().retrieve_changed_item(
                connection,
                space_id=ids["alice_space"],
                source_item_id=ids["source"],
                source_revision_id=ids["source_revision"],
                source_markdown=ids["source_body"],
            )
    finally:
        connection.close()
