from __future__ import annotations

import json

import pytest
from flask import Flask

from code_assist_v1 import (
    app_code,
    internal_llm,
    job_manager as legacy_jobs,
    outlook_integration,
    routes_chat_stream,
    routes_models,
)
from code_assist_v1 import gguf_loader
from code_assist_v1.model_runtime import (
    LocalInferenceOwnershipError,
    ModelRuntimeConfigurationError,
    assert_local_inference_allowed,
    assert_web_process_role,
    assert_worker_process_role,
    gguf_catalog_enabled,
    hold_inference_ownership,
    process_role,
)


pytestmark = pytest.mark.unit


def test_process_roles_are_exact_and_development_preserves_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODE_ASSIST_PROCESS_ROLE", raising=False)
    assert process_role().value == "development"
    assert gguf_catalog_enabled() is True
    assert_local_inference_allowed()
    assert_web_process_role().value == "development"

    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", " production ")
    with pytest.raises(ModelRuntimeConfigurationError):
        process_role()


def test_web_role_never_discovers_loads_or_executes_local_gguf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    assert gguf_catalog_enabled() is False
    assert assert_web_process_role().value == "web"
    with pytest.raises(LocalInferenceOwnershipError):
        assert_local_inference_allowed()
    with pytest.raises(ModelRuntimeConfigurationError):
        assert_worker_process_role()

    # The app helper returns before importing/scanning the GGUF loader.
    assert app_code._register_gguf_models(autoload=True) == []
    with pytest.raises(LocalInferenceOwnershipError):
        gguf_loader.find_gguf_files()
    with pytest.raises(LocalInferenceOwnershipError):
        gguf_loader.resolve_gguf_paths({"gguf": {"model_pattern": "*.gguf"}})
    with pytest.raises(LocalInferenceOwnershipError):
        gguf_loader.load_gguf_model("D:/outside/model.gguf")
    with pytest.raises(LocalInferenceOwnershipError):
        next(gguf_loader.gguf_chat_stream([]))
    with pytest.raises(LocalInferenceOwnershipError):
        internal_llm._call_gguf_text({}, [], 1, 0.0)

    events = list(
        routes_chat_stream._stream_gguf(
            {"env_id": "gguf-forged"}, [], 0.0, 1
        )
    )
    assert len(events) == 1
    payload = json.loads(events[0].removeprefix("data: ").strip())
    assert payload == {
        "error": "Local GGUF inference is unavailable in this web process.",
        "status": 503,
    }


def test_web_model_catalog_omits_local_profiles_and_never_defaults_to_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "web")
    monkeypatch.setattr(
        routes_models,
        "_load_model_state",
        lambda: (
            {
                "local": {
                    "env_id": "gguf-local",
                    "runtime": "gguf",
                    "name": "Local",
                },
                "remote": {
                    "env_id": "remote",
                    "runtime": "api",
                    "name": "Remote",
                    "model": "remote-v1",
                },
            },
            {
                "gguf-local": {"runtime": "gguf", "name": "Local"},
                "remote": {"runtime": "api", "name": "Remote"},
            },
        ),
    )
    monkeypatch.setattr(
        "code_assist_v1.config.DEFAULT_MODEL_PRIORITY",
        ["local", "remote"],
    )
    app = Flask(__name__)
    routes_models.register_model_routes(app)
    response = app.test_client().get("/api/code/models")
    assert response.status_code == 200
    assert response.json["gguf"] == []
    assert response.json["default"] == "remote"
    assert response.json["priority"] == ["remote"]


def test_production_roles_reject_legacy_in_process_job_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_target(_context) -> None:
        nonlocal called
        called = True

    for role in ("web", "worker"):
        monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", role)
        with pytest.raises(ModelRuntimeConfigurationError, match="durable job"):
            legacy_jobs.manager.submit(
                user_id="fixture-user",
                kind="fixture.legacy",
                target=forbidden_target,
            )
        with pytest.raises(ModelRuntimeConfigurationError, match="durable job"):
            legacy_jobs.manager.create_job(
                user_id="fixture-user",
                kind="fixture.legacy",
            )
        with pytest.raises(ModelRuntimeConfigurationError, match="durable job"):
            outlook_integration.start_outlook_scheduler()
    assert called is False


def test_worker_role_requires_the_machine_wide_owner(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    with pytest.raises(ModelRuntimeConfigurationError):
        assert_web_process_role()
    with pytest.raises(LocalInferenceOwnershipError):
        assert_local_inference_allowed()

    with hold_inference_ownership(isolated_knowledge_layout.core_db_path) as name:
        assert name == "Global\\CodeAssistV1InferenceOwnerV1"
        assert_local_inference_allowed()
        assert gguf_loader.resolve_gguf_paths({})["model_path"] is None
        with pytest.raises(LocalInferenceOwnershipError):
            with hold_inference_ownership(
                isolated_knowledge_layout.root / "another-core.sqlite3"
            ):
                pass

    with pytest.raises(LocalInferenceOwnershipError):
        assert_local_inference_allowed()
