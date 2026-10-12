from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import auth as legacy_auth
from code_assist_v1 import second_brain_store as sb
from code_assist_v1.auth import create_user, register_auth_routes
from code_assist_v1 import routes_chat_stream as chat_routes


pytestmark = pytest.mark.regression

_ORIGIN = "https://agent.example.test"
_SAME_ORIGIN_HEADERS = {
    "Origin": _ORIGIN,
}


@pytest.fixture(autouse=True)
def _reset_second_brain_cache():
    previous = dict(sb._USER_CACHE)
    failed_logins = {
        key: list(value) for key, value in legacy_auth._FAILED_LOGINS.items()
    }
    sb._USER_CACHE.clear()
    yield
    sb._USER_CACHE.clear()
    sb._USER_CACHE.update(previous)
    legacy_auth._FAILED_LOGINS.clear()
    legacy_auth._FAILED_LOGINS.update(failed_logins)


def _sse_events(response) -> list[dict]:
    return [
        json.loads(chunk.removeprefix("data: "))
        for chunk in response.get_data(as_text=True).split("\n\n")
        if chunk.startswith("data: ")
    ]


@pytest.mark.scenario("REG-003")
def test_personal_second_brain_source_save_read_list_search_and_context_are_user_isolated(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = uuid4().hex
    alice_uid = f"alice-{suffix[:10]}"
    bob_uid = f"bob-{suffix[10:20]}"
    alice_canary = f"PERSONAL_ALICE_CANARY_{suffix}"
    alice_body_canary = f"PERSONAL_ALICE_BODY_ONLY_{suffix}"
    bob_canary = f"PERSONAL_BOB_CANARY_{suffix}"
    filename = "personal-canary.md"

    alice_saved = sb.save_thought(
        {"filename": filename},
        f"# Alice personal source\n\n{alice_canary}\n\n{alice_body_canary}\n",
        user_id=alice_uid,
    )
    bob_saved = sb.save_thought(
        {"filename": filename},
        f"# Bob personal source\n\n{bob_canary}\n",
        user_id=bob_uid,
    )

    for saved, uid in ((alice_saved, alice_uid), (bob_saved, bob_uid)):
        assert saved["meta"]["id"]
        saved_path = Path(saved["meta"]["path"]).resolve()
        assert saved_path.is_relative_to(isolated_data_root.resolve())
        assert uid in saved_path.parts

    # Characterize the read results without making the legacy read-time refresh
    # writes part of the compatibility contract.
    sb._USER_CACHE.clear()
    monkeypatch.setattr(sb, "refresh_index_and_cache", lambda user_id=None: None)

    alice_read = sb.get_thought(alice_saved["meta"]["id"], user_id=alice_uid)
    bob_read = sb.get_thought(bob_saved["meta"]["id"], user_id=bob_uid)
    assert alice_read is not None
    assert bob_read is not None
    assert alice_canary in alice_read["content"]
    assert bob_canary not in alice_read["content"]
    assert bob_canary in bob_read["content"]
    assert alice_canary not in bob_read["content"]

    alice_sources = sb.list_sources(user_id=alice_uid)
    bob_sources = sb.list_sources(user_id=bob_uid)
    assert [item["source_id"] for item in alice_sources] == [alice_saved["meta"]["id"]]
    assert [item["source_id"] for item in bob_sources] == [bob_saved["meta"]["id"]]
    assert alice_canary in alice_sources[0]["content_preview"]
    assert bob_canary not in alice_sources[0]["content_preview"]
    assert bob_canary in bob_sources[0]["content_preview"]
    assert alice_canary not in bob_sources[0]["content_preview"]

    assert [
        item["id"]
        for item in sb.search_sources(alice_canary, user_id=alice_uid)
    ] == [alice_saved["meta"]["id"]]
    assert sb.search_sources(alice_canary, user_id=bob_uid) == []

    alice_context, alice_ids, alice_meta = sb.build_search_context_with_metadata(
        alice_canary,
        user_id=alice_uid,
        max_results=3,
        hop_depth=0,
    )
    bob_context, _, _ = sb.build_search_context_with_metadata(
        alice_canary,
        user_id=bob_uid,
        max_results=3,
        hop_depth=0,
    )
    assert alice_canary in alice_context
    assert alice_body_canary in alice_context
    assert bob_canary not in alice_context
    assert alice_ids == [alice_saved["meta"]["id"]]
    assert alice_meta["used_sources"] == alice_ids
    assert alice_meta["used_wiki_pages"] == alice_ids
    # The query may be echoed in a compatibility header, but a body-only marker
    # from Alice must never cross into Bob's retrieval context.
    assert alice_body_canary not in bob_context


@pytest.mark.scenario("REG-003")
def test_reg_003_chat_route_propagates_authenticated_uid_to_personal_retrieval(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_data_root  # The P0.2 fixture and autouse guard own all mutable roots.
    authenticated_uid = f"chat-user-{uuid4().hex[:12]}"
    forged_uid = "forged-other-user"
    query = "Explain the personal retrieval canary from my Second Brain."
    retrieved_id = "source/authenticated-personal.md"
    retrieved_context = f"PERSONAL_RETRIEVAL_CONTEXT_FOR_{authenticated_uid}"
    retrieval_meta = {
        "mode": "llm-wiki",
        "used_sources": [retrieved_id],
        "used_raw_chunks": [],
        "used_thoughts": [],
        "used_wiki_pages": [retrieved_id],
        "coverage_note": "mocked personal match",
        "context_truncated": False,
    }
    retrieval_calls: list[dict] = []
    legacy_retrieval_calls: list[dict] = []
    streamed_messages: list[list[dict]] = []

    create_user(
        authenticated_uid,
        "Correct-Horse-Chat-42",
        display_name="Chat Characterization User",
        role="user",
    )

    def fake_second_brain_retrieval(**kwargs):
        retrieval_calls.append(dict(kwargs))
        return retrieved_context, [retrieved_id], dict(retrieval_meta)

    def fake_legacy_retrieval(**kwargs):
        legacy_retrieval_calls.append(dict(kwargs))
        return []

    def fake_stream_api(cfg, messages, temperature, max_tokens, timeout_s):
        del cfg, temperature, max_tokens, timeout_s
        streamed_messages.append([dict(message) for message in messages])
        yield chat_routes._sse({"delta": "mock assistant response"})

    monkeypatch.setattr(
        chat_routes,
        "_resolve_model",
        lambda model_id: (
            {
                "model": model_id or "mock-personal-model",
                "context_window": 8192,
                "cost_tier": "low",
            },
            "api",
        ),
    )
    monkeypatch.setattr(
        chat_routes,
        "build_coding_system_prompt",
        lambda **kwargs: "MOCK PERSONAL SYSTEM PROMPT",
    )
    monkeypatch.setattr(chat_routes.ks, "search", fake_legacy_retrieval)
    monkeypatch.setattr(
        chat_routes.sb,
        "build_search_context_with_metadata",
        fake_second_brain_retrieval,
    )
    monkeypatch.setattr(chat_routes, "_stream_api", fake_stream_api)

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="chat-characterization-test",
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
    )
    register_auth_routes(app)
    chat_routes.register_chat_stream_routes(app)
    client = app.test_client()
    login = client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={
            "user_id": authenticated_uid,
            "password": "Correct-Horse-Chat-42",
        },
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    csrf_token = login.get_json().get("csrf_token")
    assert isinstance(csrf_token, str) and csrf_token

    response = client.post(
        "/api/code/chat/stream",
        base_url=_ORIGIN,
        json={
            "model": "mock-personal-model",
            "messages": [{"role": "user", "content": query}],
            "enable_knowledge": True,
            "enable_memory": False,
            "enable_project_context": False,
            "sb_max_results": 3,
            "sb_hop_depth": 0,
            "uid": forged_uid,
        },
        headers={**_SAME_ORIGIN_HEADERS, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    events = _sse_events(response)
    assert events[-1]["done"] is True
    assert events[-1]["second_brain_wiki_pages"] == [retrieved_id]
    assert events[-1]["second_brain_thoughts"] == [retrieved_id]
    assert events[-1]["second_brain_retrieval"] == retrieval_meta

    assert retrieval_calls == [
        {
            "query": query,
            "user_id": authenticated_uid,
            "max_results": 3,
            "hop_depth": 0,
        }
    ]
    assert retrieval_calls[0]["user_id"] != forged_uid
    assert legacy_retrieval_calls == [
        {
            "query": query,
            "user_id": authenticated_uid,
            "max_results": 8,
            "max_content_chars": 4000,
        }
    ]

    assert len(streamed_messages) == 1
    final_messages = streamed_messages[0]
    retrieval_index = next(
        index
        for index, message in enumerate(final_messages)
        if message.get("content") == retrieved_context
    )
    user_index = next(
        index
        for index, message in enumerate(final_messages)
        if message.get("role") == "user" and message.get("content") == query
    )
    assert retrieval_index < user_index
    assert final_messages[user_index] == {"role": "user", "content": query}
