from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from code_assist_v1.knowledge_core.agent_execution import (
    CanonicalAgentExecutionError,
    CanonicalAgentTurnExecutor,
    CanonicalConversationSummarizer,
    TURN_PROMPT_VERSION,
)
from code_assist_v1.knowledge_core.agent_runtime import (
    compose_canonical_agent_runtime,
)
from code_assist_v1.knowledge_core.answer_input import (
    ANSWER_INPUT_FORMAT, input_receipt, restore_source_documents,
)
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.retrieval import (
    CanonicalRetrievalSnapshot,
    RetrievalHit,
    RetrievalProjectionState,
    WikiManifest,
    WikiManifestEntry,
)
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentConversationMessage,
    AgentConversationSummary,
    AgentRankedContext,
    AgentSourceArtifact,
    AgentTurnContextBundle,
)
from code_assist_v1.knowledge_core.scope import Capability, SpaceSummary
from code_assist_v1.prompts import GENERAL_ASSISTANT_PERSONA


pytestmark = pytest.mark.unit


def _context() -> AgentTurnContextBundle:
    retrieval = CanonicalRetrievalSnapshot(
        space_id="10000000-0000-4000-8000-000000000001",
        normalized_query="scopealpha launch phrase",
        hits=(
            RetrievalHit(
                item_id="10000000-0000-4000-8000-000000000002",
                space_id="10000000-0000-4000-8000-000000000001",
                revision_id="10000000-0000-4000-8000-000000000003",
                title="Part launch phrase",
                excerpt_md="# scopealpha\nORBIT-COPPER-7319\n",
                updated_at_ms=100,
                rank=1,
                bm25_score=-1.0,
            ),
        ),
        semantic_neighbors=(),
        recent_events=(),
        projection=RetrievalProjectionState(
            projection_name="markdown",
            status="missing",
            canonical_generation=0,
            desired_generation=0,
            built_generation=0,
            canonical_fallback_used=True,
        ),
        wiki_manifest=WikiManifest(
            selected_view_space_id="10000000-0000-4000-8000-000000000001",
            authorized_published_count=1,
            entries=(
                WikiManifestEntry(
                    item_id="10000000-0000-4000-8000-000000000002",
                    revision_id="10000000-0000-4000-8000-000000000003",
                    source_space_id="10000000-0000-4000-8000-000000000001",
                    title="Part launch phrase",
                    updated_at_ms=100,
                    origin="native",
                    inherited=False,
                    overlay_grant_id=None,
                ),
            ),
            truncated=False,
            entry_cap=40,
        ),
    )
    return AgentTurnContextBundle(
        actor_user_id="local-admin",
        conversation_id="10000000-0000-4000-8000-000000000004",
        turn_id="10000000-0000-4000-8000-000000000005",
        operation_id="10000000-0000-4000-8000-000000000006",
        space_id=retrieval.space_id,
        normalized_query=retrieval.normalized_query,
        retrieval=retrieval,
        pins=(),
        explicit_contexts=(),
        recent_messages=(
            AgentConversationMessage(1, "user", "Remember the launch phrase."),
            AgentConversationMessage(2, "assistant", "I will use the part source."),
            AgentConversationMessage(3, "user", "scopealpha launch phrase"),
        ),
        active_summary=AgentConversationSummary(
            covered_through_sequence_no=0,
            summary_md="No earlier messages.",
            summary_sha256="a" * 64,
        ),
    )


def test_turn_executor_passes_only_the_authorized_context_bundle_to_codex() -> None:
    calls: list[dict[str, object]] = []

    def runner(_task, payload, **kwargs):
        calls.append({"payload": payload, **kwargs})
        return "The phrase is ORBIT-COPPER-7319.", {
            "model_id": "codex-local",
            "model_kind": "codex",
        }

    context = replace(
        _context(),
        selected_view=SpaceSummary(
            id="10000000-0000-4000-8000-000000000001",
            kind="part",
            name="Renamed Logistics Part",
            org_path=("Operations", "Automation", "Renamed Logistics Part"),
            capabilities=(Capability.READ,),
        ),
    )
    result = CanonicalAgentTurnExecutor(
        runner=runner,
        provider_order=("codex",),
        model_ids=("codex-local",),
    ).complete(context)

    assert result.assistant_content == {
        "text": "The phrase is ORBIT-COPPER-7319."
    }
    assert result.final_response == {
        "finish_reason": "stop",
        "model_id": "codex-local",
        "model_kind": "codex",
        "prompt_version": TURN_PROMPT_VERSION,
        "retrieval_source": "canonical_fts",
        "citation_trace": {
            "schema_version": 1, "status": "uncited",
            "claim_support": "not_semantically_verified", "occurrences": [], "sources": [],
        },
        "model_input_trace": input_receipt(calls[0]["payload"], calls[0]["messages"], max_output_tokens=3072),
    }
    assert len(calls) == 1
    call = calls[0]
    assert call["provider_order"] == ["codex"]
    assert call["model_ids"] == ["codex-local"]
    assert "codex_policy" not in call
    payload = call["payload"]
    assert isinstance(payload, dict)
    assert payload["space_id"] == _context().space_id
    assert payload["selected_view"] == {
        "space_id": context.space_id,
        "kind": "part",
        "name": "Renamed Logistics Part",
        "org_path": ["Operations", "Automation", "Renamed Logistics Part"],
    }
    assert payload["retrieval_call_count"] == 1
    assert payload["wiki_manifest"]["authorized_published_count"] == 1
    assert payload["wiki_evidence_status"] == {
        "retrieved_body_excerpt_count": 1,
        "retrieved_body_excerpt_bytes": len("# scopealpha\nORBIT-COPPER-7319\n".encode("utf-8")),
        "some_body_excerpts_truncated": False,
        "ranked_body_excerpt_count": 1,
        "representative_body_excerpt_count": 0,
        "continuation_body_excerpt_count": 0,
        "query_intent": "unknown",
        "coverage_truncated": False,
    }
    rendered = call["messages"][1]["content"]
    assert "ORBIT-COPPER-7319" in rendered
    assert "foreign-canary" not in rendered


def test_turn_executor_reuses_legacy_general_assistant_policy() -> None:
    calls: list[dict[str, object]] = []

    def runner(_task, payload, **kwargs):
        calls.append({"payload": payload, **kwargs})
        return "일반 지식으로 설명할 수 있습니다.", {
            "model_id": "codex-local",
            "model_kind": "codex",
        }

    original = _context()
    empty_retrieval = replace(
        original.retrieval,
        normalized_query="파이썬의 장점을 설명해줘",
        hits=(),
        semantic_neighbors=(),
    )
    context = replace(
        original,
        normalized_query=empty_retrieval.normalized_query,
        retrieval=empty_retrieval,
        recent_messages=(
            AgentConversationMessage(1, "user", empty_retrieval.normalized_query),
        ),
        active_summary=None,
    )

    result = CanonicalAgentTurnExecutor(runner=runner).complete(context)

    assert result.assistant_content["text"] == "일반 지식으로 설명할 수 있습니다."
    system_prompt = calls[0]["messages"][0]["content"]
    assert GENERAL_ASSISTANT_PERSONA.strip() in system_prompt
    assert "Use your general knowledge and reasoning" in system_prompt
    assert "high-priority supplemental evidence" in system_prompt
    assert "query-independent wiki_manifest" in system_prompt
    assert "never say that no Wiki body or body evidence was returned" in system_prompt
    assert "Codex tools remain available" in system_prompt
    assert "answer from it without calling an external tool" in system_prompt
    assert "considered answerable from the Wiki by default" in system_prompt
    assert "selected_view is authoritative server metadata" in system_prompt
    assert "not expected to be a document title" in system_prompt
    assert "Do not bypass sufficient Wiki evidence" in system_prompt
    assert "Web, shell, skills, connectors, and workspace tools may be used" in system_prompt
    assert "HR-NAF" not in system_prompt
    assert "using only the supplied" not in system_prompt
    assert calls[0]["payload"]["retrieval_candidates"] == []
    assert calls[0]["payload"]["wiki_manifest"]["listed_count"] == 1
    assert calls[0]["payload"]["citation_sources"] == []
    assert result.final_response["citation_trace"]["status"] == "no_wiki_body"


def test_turn_executor_supplies_only_authorized_original_download_links() -> None:
    calls: list[dict[str, object]] = []

    def runner(_task, payload, **kwargs):
        calls.append({"payload": payload, **kwargs})
        handle = payload["citation_sources"][0]["handle"]
        answer = f"## Finding\n\nBody\n\n근거 문서: [[wiki:{handle}]]\n\n---\n\n## 전체 요약\n\nSummary"
        assert kwargs["validate_output"](answer) == ""
        return answer, {
            "model_id": "codex-local",
            "model_kind": "codex",
        }

    context = replace(
        _context(),
        selected_view_space_id="10000000-0000-4000-8000-000000000001",
        ranked_contexts=(
            AgentRankedContext(
                item_id="10000000-0000-4000-8000-000000000002",
                revision_id="10000000-0000-4000-8000-000000000003",
                source_space_id="10000000-0000-4000-8000-000000000001",
                rank=1,
                title="Part launch phrase",
                excerpt_md="# scopealpha\nORBIT-COPPER-7319\n",
                relation_semantics="direct_match",
                truncated=False,
                source_artifacts=(
                    AgentSourceArtifact(
                        artifact_id="10000000-0000-4000-8000-000000000007",
                        source_revision_id="10000000-0000-4000-8000-000000000003",
                        original_filename="source.pdf",
                        media_type="application/pdf",
                        size_bytes=123,
                    ),
                ),
            ),
        ),
    )
    result = CanonicalAgentTurnExecutor(runner=runner).complete(context)

    model_payload = calls[0]["payload"]
    assert model_payload["input_format"] == ANSWER_INPUT_FORMAT
    assert "source_documents" not in model_payload
    source = restore_source_documents(model_payload)["source_documents"][0]
    artifact = source["artifacts"][0]
    assert artifact["original_filename"] == "source.pdf"
    assert "download_url" not in artifact
    expected_url = (
        "/api/knowledge/v1/spaces/10000000-0000-4000-8000-000000000001/"
        "items/10000000-0000-4000-8000-000000000002/source-artifacts/"
        "10000000-0000-4000-8000-000000000007/download"
    )
    assert result.final_response["source_documents"][0]["artifacts"][0]["download_url"] == expected_url
    assert expected_url in result.assistant_content["text"]
    assert "[[wiki:" not in result.assistant_content["text"]
    trace = result.final_response["citation_trace"]
    assert trace["status"] == "resolved"
    assert trace["sources"][0]["item_id"] == source["item_id"]
    assert trace["sources"][0]["revision_id"] == source["revision_id"]
    assert trace["sources"][0]["artifact_ids"] == [artifact["artifact_id"]]
    assert result.final_response["source_documents_role"] == "supplied_not_claim_verified"
    system_prompt = calls[0]["messages"][0]["content"]
    assert "Directly below each chapter" in system_prompt
    assert "Markdown horizontal rule" in system_prompt
    assert "## 전체 요약" in system_prompt
    assert "must never depend on a particular" in system_prompt
    assert calls[0]["max_tokens"] == 3072


@pytest.mark.parametrize("invalid", ["근거 문서: [[wiki:missing]]",
                                  "근거 문서: [source.pdf](/api/knowledge/v1/example)"])
def test_turn_executor_fences_invalid_links_even_if_custom_runner_ignores_validator(invalid):
    def runner(_task, _payload, **kwargs):
        assert kwargs["validate_output"](invalid)
        return invalid, {"model_kind": "hynix_api", "model_id": "company-compatible"}

    with pytest.raises(CanonicalAgentExecutionError, match="citation structure"):
        CanonicalAgentTurnExecutor(runner=runner, provider_order=("hynix_api",)).complete(_context())


def test_turn_executor_rejects_invalid_citation_identity_before_model_call():
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return "must not call", {}

    with pytest.raises(CanonicalAgentExecutionError, match="citation context"):
        CanonicalAgentTurnExecutor(runner=runner).complete(replace(_context(), turn_id="invalid"))
    assert calls == []


def test_turn_executor_preserves_stable_codex_failure_category() -> None:
    def runner(_task, _payload, **_kwargs):
        return None, {
            "failure_codes": ["CODEX_EXEC_TIMEOUT"],
            "fallback_errors": ["redacted provider detail"],
        }

    with pytest.raises(CanonicalAgentExecutionError) as raised:
        CanonicalAgentTurnExecutor(runner=runner).complete(_context())

    assert raised.value.code == "CODEX_EXEC_TIMEOUT"
    assert raised.value.retryable is True
    assert "redacted provider detail" not in str(raised.value)


def test_turn_executor_rejects_context_binding_drift_before_model_call() -> None:
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "never", {}

    context = _context()
    object.__setattr__(context, "retrieval_call_count", 2)
    with pytest.raises(CanonicalAgentExecutionError):
        CanonicalAgentTurnExecutor(runner=runner).complete(context)
    assert calls == 0


@dataclass(frozen=True)
class _Message:
    sequence_no: int
    role: str
    text: str

    @property
    def content(self):
        return {"role": self.role, "content": {"text": self.text}}


class _ConversationReader:
    def list_messages(self, *, after_sequence_no, limit, **_kwargs):
        rows = (
            _Message(1, "user", "Question"),
            _Message(2, "assistant", "Answer"),
            _Message(3, "user", "Follow-up"),
        )
        return tuple(row for row in rows if row.sequence_no > after_sequence_no)[:limit]


def test_summarizer_materializes_messages_before_invoking_the_model() -> None:
    calls: list[dict[str, object]] = []

    def runner(_task, payload, **kwargs):
        calls.append({"payload": payload, **kwargs})
        return "- Question answered\n- Follow-up pending", {
            "model_id": "codex-local",
            "model_kind": "codex",
        }

    result = CanonicalConversationSummarizer(
        _ConversationReader(),
        runner=runner,
        provider_order=("codex",),
    ).summarize(
        actor_user_id="local-admin",
        actor_scope_epoch=1,
        conversation_id="10000000-0000-4000-8000-000000000004",
        through_message_sequence=3,
    )
    assert result.summary_md.endswith("Follow-up pending")
    assert result.model_id == "codex-local"
    assert len(calls) == 1
    assert len(calls[0]["payload"]["messages"]) == 3


def test_runtime_composition_is_side_effect_free_and_binds_every_agent_slot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing.db"
    factory = ConnectionFactory._for_isolated_tests(
        database,
        CANONICAL_MIGRATION_CATALOG,
    )
    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        part_reads=True,
    )
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=flags,
        principal_resolver=lambda: {"id": "local-admin", "scope_epoch": 1},
        provider_order=("codex",),
    )
    assert not database.exists()
    for slot in (
        "scope_selection",
        "conversations",
        "retrieval",
        "management_reads",
        "agent_context",
    ):
        assert runtime.services.require(slot) is not None
    assert runtime.route_bindings.turn_executor.provider_order == ("codex",)
