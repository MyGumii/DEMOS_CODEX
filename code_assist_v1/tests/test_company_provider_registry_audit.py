from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.company_provider_registry_audit import (
    COMPANY_GGUF_ID,
    EXPECTED_MODEL_PRIORITY,
    ProviderRegistryAuditError,
    audit_runtime_registry,
    main,
    report_contract_errors,
)


def _runtime_registry() -> dict:
    models: dict[str, dict] = {}
    for registry_id, model in (
        ("gemma4-company", "exact-gemma-4-model"),
        ("qwen36-company", "exact-qwen-3.6-model"),
        ("kimi-k3-company", "exact-kimi-k3-model"),
        ("glm52-company", "exact-glm-5.2-model"),
    ):
        models[registry_id] = {
            "env_id": registry_id,
            "runtime": "api",
            "model": model,
            "url": "https://llm.example.internal/v1/chat/completions",
            "api_key_env": "COMPANY_LLM_RUNTIME_TOKEN",
        }
    models[COMPANY_GGUF_ID] = {
        "env_id": COMPANY_GGUF_ID,
        "runtime": "gguf",
        "model": "gemma-4-31B-it-qat-UD-Q4_K_XL",
        "gguf": {
            "model_path_env": "CODE_ASSIST_GEMMA4_Q4_GGUF",
            "mmproj_path_env": "CODE_ASSIST_GEMMA4_MMPROJ_F16",
        },
    }
    models["disabled-diagnostic"] = {
        "runtime": "api",
        "enabled": False,
        "model": "disabled-model",
        "url": "https://disabled.invalid/v1/chat/completions",
    }
    return {
        "gguf": {
            "auto_register_unconfigured": False,
            "autoload_on_startup": True,
            "autoload_model": COMPANY_GGUF_ID,
        },
        "models": models,
        "default_model_priority": list(EXPECTED_MODEL_PRIORITY),
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def test_runtime_registry_audit_freezes_company_api_gguf_and_optional_codex_policy(
    tmp_path: Path,
) -> None:
    config = tmp_path / "company-runtime-registry.json"
    _write(config, _runtime_registry())

    report = audit_runtime_registry(config)

    assert report_contract_errors(report) == ()
    assert report["summary"] == {
        "expected_model_count": 5,
        "observed_model_count": 5,
        "api_model_count": 4,
        "gguf_model_count": 1,
        "error_count": 0,
        "passed": True,
    }
    assert report["observed_model_priority"] == list(EXPECTED_MODEL_PRIORITY)
    assert [row["registry_id"] for row in report["models"]] == list(
        EXPECTED_MODEL_PRIORITY
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert "COMPANY_LLM_RUNTIME_TOKEN" in serialized
    assert "sentinel-secret" not in serialized
    assert "llm.example.internal" not in serialized


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda value: value["default_model_priority"].reverse(),
            "priority_order",
        ),
        (
            lambda value: value["models"].pop(COMPANY_GGUF_ID),
            f"missing_model:{COMPANY_GGUF_ID}",
        ),
        (
            lambda value: value["models"].update(
                {
                    "codex-local": {
                        "runtime": "codex",
                        "model": "codex-cli",
                    }
                }
            ),
            "configured_codex_lane",
        ),
        (
            lambda value: value["models"].update(
                {
                    "unreviewed-api": {
                        "runtime": "api",
                        "model": "unreviewed",
                        "url": "https://llm.example.internal/v1/chat/completions",
                    }
                }
            ),
            "extra_enabled_model:unreviewed-api",
        ),
        (
            lambda value: value["gguf"].update(
                {"auto_register_unconfigured": True}
            ),
            "gguf_auto_registration_enabled",
        ),
        (
            lambda value: value["models"]["kimi-k3-company"].update(
                {"enabled": False}
            ),
            "model_disabled:kimi-k3-company",
        ),
        (
            lambda value: value["models"]["glm52-company"].update(
                {"url": "not-an-endpoint"}
            ),
            "api_endpoint:glm52-company",
        ),
        (
            lambda value: value["models"]["qwen36-company"].pop("api_key_env"),
            "api_token_env:qwen36-company",
        ),
    ],
)
def test_runtime_registry_policy_drift_is_reported(
    tmp_path: Path, mutate, expected_error: str
) -> None:
    value = deepcopy(_runtime_registry())
    mutate(value)
    config = tmp_path / "drifted-registry.json"
    _write(config, value)

    report = audit_runtime_registry(config)

    assert report_contract_errors(report) == ()
    assert report["summary"]["passed"] is False
    assert expected_error in report["errors"]


def test_registry_rejects_duplicate_json_and_reports_literal_secret(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"models":{},"models":{}}\n', encoding="utf-8")
    with pytest.raises(ProviderRegistryAuditError, match="duplicate JSON member"):
        audit_runtime_registry(duplicate)

    value = _runtime_registry()
    value["models"]["gemma4-company"]["api_key"] = "sentinel-secret"
    value["models"]["gemma4-company"]["metadata"] = {
        "client_secret": "nested-sentinel-secret"
    }
    config = tmp_path / "literal-secret.json"
    _write(config, value)
    report = audit_runtime_registry(config)
    assert "literal_secret:gemma4-company" in report["errors"]
    assert "embedded_literal_secret" in report["errors"]
    assert "sentinel-secret" not in json.dumps(report)


def test_registry_audit_cli_is_create_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "company-runtime-registry.json"
    output = tmp_path / "provider-registry-audit.json"
    _write(config, _runtime_registry())

    assert main(["--config", str(config), "--output", str(output)]) == 0
    original = output.read_bytes()
    assert main(["--config", str(config), "--output", str(output)]) == 3
    assert output.read_bytes() == original
    assert "OUTPUT_EXISTS" in capsys.readouterr().err


def test_report_validator_is_total_for_arbitrary_json_shape() -> None:
    malformed = {
        "report_schema": "code-assist.company-provider-registry-audit",
        "schema_version": 1,
        "errors": [{"not": "hashable"}],
        "models": [["not", "a", "row"]],
        "summary": {},
    }

    assert report_contract_errors(malformed)
