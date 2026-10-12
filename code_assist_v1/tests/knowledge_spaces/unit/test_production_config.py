from __future__ import annotations

import copy
import hashlib
import json

import pytest

from code_assist_v1.knowledge_core.production_config import (
    ACL_POLICY,
    CHECKPOINT_INTERVAL_SECONDS,
    CHECKPOINT_MODE,
    ProductionConfigError,
    ProductionPaths,
    STARTUP_ORDER,
    WINSW_SHA256,
    WINSW_VERSION,
    canonical_json_bytes,
    parse_production_config,
    production_config_document,
    production_config_errors,
    production_config_sha256,
)
from code_assist_v1.knowledge_core.production_wsgi import WAITRESS_VERSION


def _paths(**overrides: str) -> ProductionPaths:
    values = {
        "backup_root": "D:\\CodeAssistBackup",
        "blob_root": "C:\\CodeAssistData\\blobs",
        "data_root": "C:\\CodeAssistData",
        "database": "C:\\CodeAssistData\\core\\knowledge-core.db",
        "environment_file": "C:\\CodeAssist\\config\\production.env",
        "log_root": "C:\\CodeAssistLogs",
        "part_write_rollout_record": "C:\\CodeAssist\\config\\rollout.json",
        "projection_root": "C:\\CodeAssistData\\projections",
        "python_executable": "C:\\CodeAssist\\venv\\Scripts\\python.exe",
        "runtime_lock": "C:\\CodeAssist\\requirements-runtime.lock",
        "service_wrapper_executable": "C:\\CodeAssist\\service\\WinSW-x64.exe",
        "working_directory": "C:\\CodeAssist",
    }
    values.update(overrides)
    return ProductionPaths(**values)


def _document() -> dict[str, object]:
    return production_config_document(
        deployment_id="code-assist-prod",
        service_account="CODEHOST\\svc_codeassist",
        paths=_paths(),
    )


def _rehash(value: dict[str, object]) -> None:
    value["config_sha256"] = production_config_sha256(value)


def test_production_manifest_is_canonical_self_bound_and_round_trips() -> None:
    value = _document()
    raw = canonical_json_bytes(value)
    parsed = parse_production_config(
        raw,
        expected_document_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert production_config_errors(value) == ()
    assert parsed.config_sha256 == value["config_sha256"]
    assert parsed.paths == _paths()
    assert value["wsgi"]["version"] == WAITRESS_VERSION  # type: ignore[index]
    assert value["wrapper"]["version"] == WINSW_VERSION  # type: ignore[index]
    assert value["wrapper"]["sha256"] == WINSW_SHA256  # type: ignore[index]
    assert value["checkpoint"] == {  # type: ignore[index]
        "coordinator_service": "CodeAssist-Projector",
        "interval_seconds": CHECKPOINT_INTERVAL_SECONDS,
        "mode": CHECKPOINT_MODE,
    }
    assert tuple(value["startup_order"]) == STARTUP_ORDER  # type: ignore[arg-type]
    assert value["acl"] == dict(ACL_POLICY)


@pytest.mark.parametrize(
    ("section", "key", "replacement", "expected"),
    [
        ("wsgi", "threads", 32, "wsgi_binding"),
        ("wsgi", "version", "latest", "wsgi_binding"),
        ("wrapper", "version", "3.0.0-alpha", "wrapper_binding"),
        ("wrapper", "sha256", "0" * 64, "wrapper_binding"),
        ("checkpoint", "mode", "TRUNCATE", "checkpoint_binding"),
        ("checkpoint", "interval_seconds", 0, "checkpoint_binding"),
        ("acl", "inheritance", "inherited", "acl_binding"),
    ],
)
def test_reviewed_runtime_values_cannot_be_redefined_by_a_resealed_manifest(
    section: str, key: str, replacement: object, expected: str
) -> None:
    value = _document()
    nested = value[section]
    assert isinstance(nested, dict)
    nested[key] = replacement
    _rehash(value)
    assert expected in production_config_errors(value)


def test_service_set_and_migrator_first_order_are_exact() -> None:
    value = _document()
    services = value["services"]
    assert isinstance(services, list)
    services.reverse()
    value["startup_order"] = [
        "CodeAssist-Web",
        "deployment-migrator",
        "CodeAssist-Notion",
        "CodeAssist-DurableWorker",
        "CodeAssist-Projector",
    ]
    _rehash(value)
    assert production_config_errors(value) == (
        "services_binding",
        "startup_order_binding",
    )


@pytest.mark.parametrize(
    "account",
    [
        "NT AUTHORITY\\SYSTEM",
        "NT AUTHORITY\\LOCAL SERVICE",
        "BUILTIN\\Administrators",
        ".\\Administrator",
        "administrator",
        "DOMAIN\\x",
    ],
)
def test_service_account_must_be_a_dedicated_non_builtin_identity(account: str) -> None:
    value = _document()
    value["service_account"] = account
    _rehash(value)
    assert "service_account" in production_config_errors(value)


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("working_directory", "relative\\work"),
        ("working_directory", "\\\\server\\share\\work"),
        ("working_directory", "C:\\"),
        ("environment_file", "C:\\CodeAssist\\config\\..\\secret.env"),
    ],
)
def test_paths_reject_relative_unc_root_and_noncanonical_spellings(
    field: str, path: str
) -> None:
    with pytest.raises(ProductionConfigError):
        _paths(**{field: path})


def test_data_log_and_backup_roots_must_be_disjoint() -> None:
    with pytest.raises(ProductionConfigError, match="must not overlap"):
        _paths(log_root="C:\\CodeAssistData\\logs")


def test_immutable_working_tree_is_disjoint_and_owns_reviewed_runtime_files() -> None:
    with pytest.raises(ProductionConfigError, match="must not overlap"):
        _paths(data_root="C:\\CodeAssist", blob_root="C:\\CodeAssist\\blobs", database="C:\\CodeAssist\\core\\knowledge-core.db", projection_root="C:\\CodeAssist\\projections")
    with pytest.raises(ProductionConfigError, match="below working_directory"):
        _paths(runtime_lock="C:\\Elsewhere\\requirements-runtime.lock")


def test_parser_rejects_duplicate_members_and_noncanonical_bytes() -> None:
    value = _document()
    raw = canonical_json_bytes(value)
    duplicate = raw.replace(
        b'{"acl":',
        b'{"config_schema":"duplicate","acl":',
        1,
    )
    with pytest.raises(ProductionConfigError, match="invalid JSON"):
        parse_production_config(duplicate)
    pretty = json.dumps(value, indent=2, ensure_ascii=True).encode("ascii")
    with pytest.raises(ProductionConfigError, match="canonical JSON"):
        parse_production_config(pretty)


def test_validator_is_total_for_arbitrary_json_shapes() -> None:
    values: list[object] = [None, [], "x", 1, {"config_schema": []}, _document()]
    malformed = copy.deepcopy(_document())
    malformed["config_sha256"] = []
    values.append(malformed)
    for value in values:
        result = production_config_errors(value)
        assert isinstance(result, tuple)
        assert all(isinstance(code, str) and code for code in result)


def test_expected_raw_document_hash_is_an_external_binding() -> None:
    raw = canonical_json_bytes(_document())
    with pytest.raises(ProductionConfigError, match="document hash"):
        parse_production_config(raw, expected_document_sha256="0" * 64)
