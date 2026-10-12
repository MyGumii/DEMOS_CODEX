from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from code_assist_v1 import internal_llm
from code_assist_v1 import routes_chat_stream as chat_routes
from code_assist_v1.model_routing import (
    apply_codex_launch_policy,
    ordered_model_candidates,
)


def _events(serialized: list[str]) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for value in serialized
        for line in value.splitlines()
        if line.startswith("data:")
    ]


def _registry() -> dict[str, dict]:
    return {
        "gemma": {"env_id": "gemma", "runtime": "api", "model": "Gemma-4"},
        "qwen": {"env_id": "qwen", "runtime": "api", "model": "Qwen3.6"},
        "big-disabled": {
            "env_id": "big-disabled",
            "runtime": "api",
            "model": "Big",
            "enabled": False,
        },
        "local": {"env_id": "gguf-local", "runtime": "gguf", "model": "local"},
        "codex-local": {
            "env_id": "codex-local",
            "runtime": "codex",
            "model": "codex-cli",
        },
    }


def _import_models_with_config(path: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["CODE_ASSIST_API_CONFIG"] = os.fspath(path)
    return subprocess.run(
        [sys.executable, "-E", "-c", "import code_assist_v1.models"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_explicit_model_registry_missing_or_malformed_fails_closed(tmp_path) -> None:
    missing = tmp_path / "missing-provider-registry.json"
    missing_result = _import_models_with_config(missing)
    assert missing_result.returncode != 0
    assert "configured API model registry is unavailable" in missing_result.stderr
    assert os.fspath(missing) not in missing_result.stderr

    malformed = tmp_path / "malformed-provider-registry.json"
    malformed.write_text("{not-json}\n", encoding="utf-8")
    malformed_result = _import_models_with_config(malformed)
    assert malformed_result.returncode != 0
    assert "configured API model registry is invalid" in malformed_result.stderr
    assert os.fspath(malformed) not in malformed_result.stderr


def test_policy_orders_api_then_gguf_and_only_enabled_codex() -> None:
    registry = _registry()
    priority = ["gemma", "qwen", "big-disabled", "local"]

    assert ordered_model_candidates(
        requested_model_id=None,
        priority_ids=priority,
        registry=registry,
        environment={},
        codex_enabled=False,
        include_fallback=True,
    ) == ["gemma", "qwen", "local"]
    assert ordered_model_candidates(
        requested_model_id=None,
        priority_ids=priority,
        registry=registry,
        environment={},
        codex_enabled=True,
        include_fallback=True,
    ) == ["gemma", "qwen", "local", "codex-local"]


def test_company_policy_freezes_all_api_lanes_before_local_and_optional_codex() -> None:
    registry = {
        "gemma4-company": {
            "env_id": "gemma4-company",
            "runtime": "api",
            "model": "Gemma-4",
        },
        "qwen36-company": {
            "env_id": "qwen36-company",
            "runtime": "api",
            "model": "Qwen3.6",
        },
        "kimi-k3-company": {
            "env_id": "kimi-k3-company",
            "runtime": "api",
            "model": "Kimi-K3",
        },
        "glm52-company": {
            "env_id": "glm52-company",
            "runtime": "api",
            "model": "GLM-5.2",
        },
        "local-gemma": {
            "env_id": "gguf-gemma4-company",
            "runtime": "gguf",
            "model": "Gemma-4-local",
        },
        "codex-local": {
            "env_id": "codex-local",
            "runtime": "codex",
            "model": "codex-cli",
        },
    }
    priority = [
        "gemma4-company",
        "qwen36-company",
        "kimi-k3-company",
        "glm52-company",
        "local-gemma",
    ]

    assert ordered_model_candidates(
        requested_model_id=None,
        priority_ids=priority,
        registry=registry,
        environment={},
        codex_enabled=False,
        include_fallback=True,
    ) == priority
    assert ordered_model_candidates(
        requested_model_id=None,
        priority_ids=priority,
        registry=registry,
        environment={},
        codex_enabled=True,
        include_fallback=True,
    ) == [*priority, "codex-local"]


def test_manual_override_is_first_but_remaining_route_stays_frozen() -> None:
    assert ordered_model_candidates(
        requested_model_id="local",
        priority_ids=["gemma", "qwen", "local"],
        registry=_registry(),
        environment={},
        codex_enabled=True,
        include_fallback=True,
    ) == ["local", "gemma", "qwen", "codex-local"]


def test_codex_launch_flag_never_promotes_codex_to_default() -> None:
    environment = {"CODE_ASSIST_DEFAULT_MODEL": "codex-local"}
    apply_codex_launch_policy(True, environ=environment)
    assert environment == {"CODE_ASSIST_ENABLE_CODEX_BRIDGE": "1"}

    apply_codex_launch_policy(False, environ=environment)
    assert environment == {"CODE_ASSIST_ENABLE_CODEX_BRIDGE": "0"}


def test_internal_default_provider_lanes_are_api_local_then_optional_codex(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "0")
    assert internal_llm.default_provider_order() == ["hynix_api", "gguf"]
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1")
    assert internal_llm.default_provider_order() == [
        "hynix_api",
        "gguf",
        "codex",
    ]


def test_company_api_lane_is_bound_by_runtime_metadata_not_hostname(monkeypatch) -> None:
    registry = {
        "gemma-company": {
            "env_id": "gemma-company",
            "runtime": "api",
            "model": "Gemma-4",
            "url": "https://reviewed-company-gateway.example/v1/chat/completions",
        }
    }
    monkeypatch.setattr(internal_llm, "MODEL_REGISTRY", registry)
    monkeypatch.setattr(internal_llm, "ENV_CONFIG", {})
    assert internal_llm._candidate_configs(
        "hynix_api", model_ids=["gemma-company"]
    ) == [
        (
            "gemma-company",
            {**registry["gemma-company"], "_registry_id": "gemma-company"},
        )
    ]


def test_internal_runner_uses_codex_only_after_api_and_gguf_fail(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1")
    calls: list[str] = []

    monkeypatch.setattr(
        internal_llm,
        "_candidate_configs",
        lambda kind, model_ids=None: [(kind, {"name": kind})],
    )

    def api(*_args, **_kwargs):
        calls.append("hynix_api")
        raise RuntimeError("offline")

    def gguf(*_args, **_kwargs):
        calls.append("gguf")
        raise RuntimeError("missing")

    def codex(*_args, **_kwargs):
        calls.append("codex")
        return "final"

    monkeypatch.setattr(internal_llm, "_call_api_text", api)
    monkeypatch.setattr(internal_llm, "_call_gguf_text", gguf)
    monkeypatch.setattr(internal_llm, "_call_codex_text", codex)

    text, meta = internal_llm.run_internal_text("test", {})
    assert text == "final"
    assert meta["model_kind"] == "codex"
    assert calls == ["hynix_api", "gguf", "codex"]


def test_semantic_edge_codex_task_preserves_configured_effort(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        internal_llm,
        "_candidate_configs",
        lambda kind, model_ids=None: [
            ("codex-local", {"name": "codex", "codex_reasoning_effort": "low"})
        ]
        if kind == "codex"
        else [],
    )

    def codex(config, *_args, **_kwargs):
        captured.append(dict(config))
        return "{}"

    monkeypatch.setattr(internal_llm, "_call_codex_text", codex)
    text, _ = internal_llm.run_internal_text(
        "semantic_edge_judge",
        {},
        provider_order=("codex",),
    )
    assert text == "{}"
    assert captured[-1]["codex_reasoning_effort"] == "low"

    text, _ = internal_llm.run_internal_text(
        "canonical_agent_turn",
        {},
        provider_order=("codex",),
    )
    assert text == "{}"
    assert captured[-1]["codex_reasoning_effort"] == "low"


def test_stream_falls_back_only_before_any_answer_content(monkeypatch) -> None:
    calls: list[str] = []

    def api(*_args, **_kwargs):
        calls.append("api")
        yield chat_routes._sse({"error": "offline", "status": 503})

    def gguf(*_args, **_kwargs):
        calls.append("gguf")
        yield chat_routes._sse({"delta": "local answer"})

    monkeypatch.setattr(chat_routes, "_stream_api", api)
    monkeypatch.setattr(chat_routes, "_stream_gguf", gguf)
    serialized = list(
        chat_routes._generate_provider_route(
            attempt_models=[
                ("gemma", {"model": "Gemma-4"}, "api"),
                ("local", {"model": "GGUF"}, "gguf"),
            ],
            data={"max_tokens": 32},
            final_messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            query="hello",
            timeout_s=5,
            base_meta={"session_id": "session"},
        )
    )
    events = _events(serialized)
    assert calls == ["api", "gguf"]
    assert [event.get("stage") for event in events if event.get("stage")] == [
        "preparing",
        "provider_attempt",
        "provider_fallback",
        "provider_attempt",
    ]
    assert [event.get("delta") for event in events if event.get("delta")] == [
        "local answer"
    ]
    done = next(event for event in events if event.get("done"))
    assert done["model_id"] == "local"
    assert [row["outcome"] for row in done["provider_attempts"]] == [
        "failed",
        "success",
    ]


def test_stream_never_combines_two_providers_after_partial_output(monkeypatch) -> None:
    codex_calls: list[str] = []

    def api(*_args, **_kwargs):
        yield chat_routes._sse({"delta": "partial"})
        yield chat_routes._sse({"error": "broken stream", "status": 502})

    def codex(*_args, **_kwargs):
        codex_calls.append("called")
        yield {"delta": "must not appear"}

    monkeypatch.setattr(chat_routes, "_stream_api", api)
    monkeypatch.setattr(chat_routes, "stream_codex_exec", codex)
    events = _events(
        list(
            chat_routes._generate_provider_route(
                attempt_models=[
                    ("gemma", {"model": "Gemma-4"}, "api"),
                    ("codex-local", {"model": "codex-cli"}, "codex"),
                ],
                data={"max_tokens": 32},
                final_messages=[{"role": "user", "content": "hello"}],
                temperature=0.0,
                query="hello",
                timeout_s=5,
                base_meta={},
            )
        )
    )
    assert codex_calls == []
    assert [event.get("delta") for event in events if event.get("delta")] == [
        "partial"
    ]
    assert not any(event.get("done") for event in events)


def test_final_codex_attempt_receives_the_same_authorized_context(monkeypatch) -> None:
    captured: list[list[dict]] = []

    monkeypatch.setattr(
        chat_routes,
        "_stream_api",
        lambda *_a, **_k: iter(
            [chat_routes._sse({"error": "offline", "status": 503})]
        ),
    )
    monkeypatch.setattr(
        chat_routes,
        "_stream_gguf",
        lambda *_a, **_k: iter(
            [chat_routes._sse({"error": "missing", "status": 503})]
        ),
    )

    def codex(messages, _cfg, timeout_s):
        assert timeout_s == 5
        captured.append([dict(message) for message in messages])
        yield {"delta": "codex answer"}

    monkeypatch.setattr(chat_routes, "stream_codex_exec", codex)
    context = {
        "role": "system",
        "content": "=== Second Brain Wiki Context ===\nwiki_id=source/approved.md",
    }
    events = _events(
        list(
            chat_routes._generate_provider_route(
                attempt_models=[
                    ("gemma", {"model": "Gemma-4"}, "api"),
                    ("local", {"model": "GGUF"}, "gguf"),
                    ("codex-local", {"model": "codex-cli"}, "codex"),
                ],
                data={"max_tokens": 32},
                final_messages=[context, {"role": "user", "content": "answer"}],
                temperature=0.0,
                query="answer",
                timeout_s=5,
                base_meta={},
            )
        )
    )

    assert captured == [[context, {"role": "user", "content": "answer"}]]
    done = next(event for event in events if event.get("done"))
    assert done["model_id"] == "codex-local"
    assert [row["model_kind"] for row in done["provider_attempts"]] == [
        "api",
        "gguf",
        "codex",
    ]
