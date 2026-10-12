from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading

import pytest

from code_assist_v1.tools.performance_load import (
    CONFIRMATION,
    CREDENTIAL_SCHEMA,
    FIXTURE_SCHEMA,
    GATE_SCHEMA,
    HARDWARE_SCHEMA,
    PROFILE_CONFIG,
    PROFILE_ORDER,
    PerformanceLoadError,
    RunConfig,
    Sample,
    STEADY_WEIGHTS,
    build_performance_gate,
    canonical_json_bytes,
    main,
    nearest_rank,
    parse_credentials,
    parse_fixture,
    parse_hardware,
    performance_gate_errors,
    performance_report_errors,
    run_profile,
    sha256_bytes,
    write_report_pair,
    verify_main,
    verify_gate_main,
)
from code_assist_v1.tools.performance_load import (
    Capture,
    Credential,
    HttpResult,
    InvariantAssertion,
    LoadClient,
    OperationTemplate,
    Poll,
    Step,
    _Session,
    _client_binding_sha256,
    _execute_operation,
    _finalize_summary,
    _steady_schedule,
    _write_new,
)


pytestmark = pytest.mark.unit


def _step(
    name: str,
    *,
    method: str = "GET",
    path: str = "/api/knowledge/v1/spaces/${space_id}/items",
    body: dict[str, object] | None = None,
    endpoint_class: str = "crud",
    expected_status: list[int] | None = None,
    forbidden: list[str] | None = None,
    headers: dict[str, str] | None = None,
    captures: dict[str, object] | None = None,
    poll: dict[str, object] | None = None,
    assertions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "assertions": assertions or [],
        "body": body,
        "captures": captures or {},
        "database_backed": True,
        "endpoint_class": endpoint_class,
        "expected_status": expected_status or [200],
        "forbid_body_values": forbidden or [],
        "headers": headers or {},
        "method": method,
        "name": name,
        "path": path,
        "poll": poll,
    }


def _fixture_document() -> dict[str, object]:
    def assertion(code: str) -> dict[str, object]:
        return {
            "code": code,
            "expected": True,
            "operator": "equals",
            "pointers": [f"/invariants/{code.casefold()}"],
        }

    templates: dict[str, object] = {}
    for name, weight in STEADY_WEIGHTS.items():
        if name == "fts_search":
            step = _step(
                "search",
                method="POST",
                path="/api/knowledge/v1/spaces/${space_id}/search",
                body={"limit": 20, "query": "${search_query}"},
                endpoint_class="search",
            )
        elif name == "projection_job_status":
            step = _step(
                "projection",
                path="/api/knowledge/v1/spaces/${space_id}/projection-status",
                endpoint_class="projection",
                poll={
                    "equals": 0,
                    "interval_ms": 50,
                    "pointer": "/lag_generations",
                    "timeout_seconds": 30,
                },
                assertions=[
                    assertion("DOMAIN_EFFECT_AT_MOST_ONCE"),
                    assertion("JOB_TERMINAL_OR_RECOVERABLE"),
                ],
            )
        elif name == "manual_publication":
            step = _step(
                "manual-preview",
                method="POST",
                path="/api/knowledge/v1/publication-previews",
                body={
                    "markdown": "# ${markdown_value}",
                    "operation_id": "${operation_id}",
                    "source_type": "manual",
                },
            )
        else:
            step = _step(
                name,
                assertions=(
                    [assertion("STALE_SAVE_NO_REVISION")]
                    if name == "edit_save"
                    else [assertion("DUAL_PUBLICATION_ATOMIC")]
                    if name == "dual_publication"
                    else []
                ),
            )
        templates[name] = {
            "generate": (
                {"operation_id": "uuid"}
                if name == "manual_publication"
                else {}
            ),
            "steps": [step],
            "weight": weight,
        }
    templates.update(
        {
            "hot_item": {
                "generate": {},
                "steps": [
                    _step(
                        "hot-item",
                        assertions=[assertion("STALE_SAVE_NO_REVISION")],
                    )
                ],
                "weight": 0,
            },
            "write_saturation": {
                "generate": {},
                "steps": [
                    _step(
                        "write-saturation",
                        assertions=[assertion("DOMAIN_EFFECT_AT_MOST_ONCE")],
                    )
                ],
                "weight": 0,
            },
            "projection_mutation": {
                "generate": {},
                "steps": [
                    _step(
                        "projection-mutation",
                        assertions=[
                            assertion("DOMAIN_EFFECT_AT_MOST_ONCE"),
                            assertion("JOB_TERMINAL_OR_RECOVERABLE"),
                        ],
                    )
                ],
                "weight": 0,
            },
        }
    )
    for name in ("leak_api", "leak_fts", "leak_agent", "leak_graph", "leak_projection"):
        templates[name] = {
            "generate": {},
            "steps": [
                _step(
                    name,
                    path=(
                        "/api/knowledge/v1/spaces/${foreign_space_id}/items/"
                        "${foreign_item_id}"
                    ),
                    endpoint_class="leak",
                    expected_status=[403, 404],
                    forbidden=["${foreign_canary}"],
                )
            ],
            "weight": 0,
        }
    clients = []
    for index in range(40):
        part_index = index // 10
        personal_space_id = f"10000000-0000-4000-8000-{index + 1:012d}"
        clients.append(
            {
                "client_id": f"client-{index:02d}",
                "leak_cases": [
                    {
                        "case_id": f"foreign-{foreign_index:02d}",
                        "variables": {
                            "foreign_canary": f"personal-canary-{foreign_index:02d}",
                            "foreign_item_id": f"20000000-0000-4000-8000-{foreign_index + 1:012d}",
                            "foreign_space_id": f"10000000-0000-4000-8000-{foreign_index + 1:012d}",
                            "foreign_user_id": f"load-user-{foreign_index:02d}",
                        },
                    }
                    for foreign_index in range(40)
                    if foreign_index != index
                ],
                "part_key": f"part-{part_index}",
                "user_id": f"load-user-{index:02d}",
                "variables": {
                    "hot_item_id": f"30000000-0000-4000-8000-{part_index + 1:012d}",
                    "item_id": f"60000000-0000-4000-8000-{index + 1:012d}",
                    "markdown_value": f"load-{index:02d}",
                    "part_space_id": f"40000000-0000-4000-8000-{part_index + 1:012d}",
                    "personal_canary": f"personal-canary-{index:02d}",
                    "personal_canary_item_id": f"20000000-0000-4000-8000-{index + 1:012d}",
                    "personal_space_id": personal_space_id,
                    "saturation_item_id": f"50000000-0000-4000-8000-{index + 1:012d}",
                    "search_query": "performance",
                    "space_id": personal_space_id,
                },
            }
        )
    return {
        "clients": clients,
        "fixture_id": "perf001-fixed-v1",
        "fixture_schema": FIXTURE_SCHEMA,
        "schema_version": 1,
        "templates": templates,
    }


def _fixture_bytes() -> bytes:
    return canonical_json_bytes(_fixture_document())


def _credentials_document() -> dict[str, object]:
    return {
        "clients": [
            {
                "client_id": f"client-{index:02d}",
                "password": f"private-password-{index:02d}",
                "user_id": f"load-user-{index:02d}",
            }
            for index in range(40)
        ],
        "credential_schema": CREDENTIAL_SCHEMA,
        "schema_version": 1,
    }


def _hardware_document() -> dict[str, object]:
    return {
        "cpu_model": "Target CPU",
        "gpu_memory_bytes": 24 * 1024**3,
        "gpu_model": "Target GPU",
        "hardware_schema": HARDWARE_SCHEMA,
        "logical_cpu_count": 32,
        "os_name": "Windows Server",
        "os_version": "2025",
        "ram_bytes": 128 * 1024**3,
        "schema_version": 1,
        "server_id": "target-class-a",
        "storage_model": "Local NVMe",
    }


def test_perf_001_fixture_freezes_40_users_four_ten_client_parts_and_exact_mix() -> None:
    raw = _fixture_bytes()
    fixture = parse_fixture(raw)
    assert len(fixture.clients) == 40
    assert [row.client_id for row in fixture.clients] == [
        f"client-{index:02d}" for index in range(40)
    ]
    assert {row.part_key for row in fixture.clients} == {
        "part-0",
        "part-1",
        "part-2",
        "part-3",
    }
    assert all(len(row.leak_cases) == 39 for row in fixture.clients)
    assert sum(len(row.leak_cases) for row in fixture.clients) == 1_560
    assert {name: fixture.templates[name].weight for name in STEADY_WEIGHTS} == dict(
        STEADY_WEIGHTS
    )
    assert fixture.document_sha256 == sha256_bytes(raw)


def test_perf_001_fixture_rejects_incomplete_leak_matrix_and_split_hot_item() -> None:
    incomplete = _fixture_document()
    incomplete["clients"][0]["leak_cases"].pop()
    with pytest.raises(PerformanceLoadError) as leak_error:
        parse_fixture(canonical_json_bytes(incomplete))
    assert leak_error.value.code == "FIXTURE_LEAK_MATRIX"

    split = _fixture_document()
    split["clients"][1]["variables"]["hot_item_id"] = (
        "99999999-9999-4999-8999-999999999999"
    )
    with pytest.raises(PerformanceLoadError) as hot_error:
        parse_fixture(canonical_json_bytes(split))
    assert hot_error.value.code == "FIXTURE_HOT_ITEM_GROUPS"


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        ("client-count", lambda value: value["clients"].pop()),
        ("client-duplicate", lambda value: value["clients"][39].update({"client_id": "client-00"})),
        ("part-groups", lambda value: value["clients"][39].update({"part_key": "part-0"})),
        ("weight", lambda value: value["templates"]["fts_search"].update({"weight": 34})),
        ("template", lambda value: value["templates"].pop("leak_graph")),
        (
            "invariant",
            lambda value: value["templates"]["hot_item"]["steps"][0].update(
                {"assertions": []}
            ),
        ),
        (
            "secret",
            lambda value: value["templates"]["fts_search"]["steps"][0]["body"].update(
                {"password_value": "must-not-enter-fixture"}
            ),
        ),
    ],
)
def test_perf_001_fixture_shape_mix_and_confidentiality_fail_closed(kind, mutation) -> None:
    del kind
    value = _fixture_document()
    mutation(value)
    with pytest.raises(PerformanceLoadError):
        parse_fixture(canonical_json_bytes(value))


def test_perf_001_fixture_rejects_duplicate_members_and_noncanonical_bytes() -> None:
    raw = _fixture_bytes()
    duplicate = raw.replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
    )
    with pytest.raises(PerformanceLoadError) as caught:
        parse_fixture(duplicate)
    assert caught.value.code == "DUPLICATE_JSON_MEMBER"
    with pytest.raises(PerformanceLoadError) as caught:
        parse_fixture(b" " + raw)
    assert caught.value.code == "FIXTURE_INVALID"


def test_perf_001_credentials_bind_exact_clients_without_hash_or_secret_output() -> None:
    fixture = parse_fixture(_fixture_bytes())
    credentials = parse_credentials(
        canonical_json_bytes(_credentials_document()), fixture
    )
    assert len(credentials) == 40
    assert "private-password" not in repr(credentials)
    changed = _credentials_document()
    changed["clients"][0]["user_id"] = "different-user"
    with pytest.raises(PerformanceLoadError) as caught:
        parse_credentials(canonical_json_bytes(changed), fixture)
    assert caught.value.code == "CREDENTIAL_BINDING"
    assert "private-password" not in str(caught.value)


def test_perf_001_hardware_document_is_exact_canonical_and_self_bound() -> None:
    raw = canonical_json_bytes(_hardware_document())
    facts = parse_hardware(raw)
    assert facts.logical_cpu_count == 32
    assert facts.document_sha256 == sha256_bytes(raw)
    extra = _hardware_document()
    extra["serial_number"] = "must-not-be-recorded"
    with pytest.raises(PerformanceLoadError):
        parse_hardware(canonical_json_bytes(extra))


def test_perf_001_every_target_profile_has_one_exact_immutable_configuration() -> None:
    for profile, expected in PROFILE_CONFIG.items():
        config = RunConfig.exact(profile, seed=0x5077)
        assert config.clients == expected["clients"] == 40
        assert config.ramp_seconds == expected["ramp_seconds"]
        assert config.duration_seconds == expected["duration_seconds"]
        assert config.cooldown_seconds == expected["cooldown_seconds"]
    with pytest.raises(PerformanceLoadError):
        RunConfig.exact("invented", seed=0x5077)


def test_perf_001_percentiles_use_documented_nearest_rank() -> None:
    values = [float(value) for value in range(1, 101)]
    assert nearest_rank(values, 50) == 50
    assert nearest_rank(values, 95) == 95
    assert nearest_rank(values, 99) == 99
    assert nearest_rank([], 95) is None


def test_perf_001_validate_only_never_requires_or_starts_target_traffic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_path = tmp_path / "fixture.json"
    credential_path = tmp_path / "credentials.json"
    hardware_path = tmp_path / "hardware.json"
    fixture_path.write_bytes(_fixture_bytes())
    credential_path.write_bytes(canonical_json_bytes(_credentials_document()))
    hardware_path.write_bytes(canonical_json_bytes(_hardware_document()))
    config = PROFILE_CONFIG["steady"]
    result = main(
        [
            "--base-url",
            "https://127.0.0.1:10011",
            "--notion-base-url",
            "https://127.0.0.1:10012",
            "--fixture",
            str(fixture_path),
            "--credentials",
            str(credential_path),
            "--hardware",
            str(hardware_path),
            "--profile",
            "steady",
            "--clients",
            str(config["clients"]),
            "--ramp-seconds",
            str(config["ramp_seconds"]),
            "--duration-seconds",
            str(config["duration_seconds"]),
            "--cooldown-seconds",
            str(config["cooldown_seconds"]),
            "--seed",
            "0x5077",
            "--output",
            str(tmp_path / "unused.json"),
            "--validate-only",
        ]
    )
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "valid"
    assert output["client_count"] == 40
    assert "password" not in json.dumps(output)
    assert not (tmp_path / "unused.json").exists()


def test_perf_001_cli_refuses_profile_drift_before_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = {}
    for name, payload in {
        "fixture": _fixture_bytes(),
        "credentials": canonical_json_bytes(_credentials_document()),
        "hardware": canonical_json_bytes(_hardware_document()),
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(payload)
        paths[name] = path
    result = main(
        [
            "--base-url",
            "https://127.0.0.1:10011",
            "--notion-base-url",
            "https://127.0.0.1:10012",
            "--fixture",
            str(paths["fixture"]),
            "--credentials",
            str(paths["credentials"]),
            "--hardware",
            str(paths["hardware"]),
            "--profile",
            "steady",
            "--clients",
            "39",
            "--ramp-seconds",
            "120",
            "--duration-seconds",
            "1200",
            "--seed",
            "0x5077",
            "--output",
            str(tmp_path / "result.json"),
            "--confirm-target-load",
            CONFIRMATION,
        ]
    )
    assert result == 3
    assert json.loads(capsys.readouterr().out) == {
        "code": "PROFILE_CONFIGURATION_DRIFT",
        "status": "error",
    }


def test_perf_001_cli_requires_confirmation_before_reading_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_path = tmp_path / "fixture.json"
    hardware_path = tmp_path / "hardware.json"
    missing_credentials = tmp_path / "must-not-be-read.json"
    fixture_path.write_bytes(_fixture_bytes())
    hardware_path.write_bytes(canonical_json_bytes(_hardware_document()))
    config = PROFILE_CONFIG["steady"]

    result = main(
        [
            "--base-url",
            "https://127.0.0.1:10011",
            "--notion-base-url",
            "https://127.0.0.1:10012",
            "--fixture",
            str(fixture_path),
            "--credentials",
            str(missing_credentials),
            "--hardware",
            str(hardware_path),
            "--profile",
            "steady",
            "--clients",
            str(config["clients"]),
            "--ramp-seconds",
            str(config["ramp_seconds"]),
            "--duration-seconds",
            str(config["duration_seconds"]),
            "--cooldown-seconds",
            str(config["cooldown_seconds"]),
            "--seed",
            "0x5077",
            "--output",
            str(tmp_path / "unused.json"),
            "--confirm-target-load",
            "not-confirmed",
        ]
    )

    assert result == 3
    assert json.loads(capsys.readouterr().out) == {
        "code": "TARGET_LOAD_CONFIRMATION_REQUIRED",
        "status": "error",
    }
    assert not missing_credentials.exists()
    assert not (tmp_path / "unused.json").exists()


def test_perf_001_library_runner_also_refuses_traffic_without_confirmation() -> None:
    fixture = parse_fixture(_fixture_bytes())
    credentials = parse_credentials(
        canonical_json_bytes(_credentials_document()), fixture
    )
    hardware = parse_hardware(canonical_json_bytes(_hardware_document()))
    with pytest.raises(PerformanceLoadError) as caught:
        run_profile(
            base_url="https://127.0.0.1:10011",
            notion_base_url="https://127.0.0.1:10012",
            fixture=fixture,
            credentials=credentials,
            hardware=hardware,
            config=RunConfig.exact("steady", seed=0x5077),
            confirmation="not-confirmed",
        )
    assert caught.value.code == "TARGET_LOAD_CONFIRMATION_REQUIRED"


def test_perf_001_direct_runner_validate_only_is_package_bound_and_network_free(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    script = (
        repository_root
        / "code_assist_v1"
        / "tests"
        / "knowledge_spaces"
        / "load"
        / "run_load.py"
    )
    fixture_path = (tmp_path / "fixture.json").resolve()
    credential_path = (tmp_path / "credentials.json").resolve()
    hardware_path = (tmp_path / "hardware.json").resolve()
    fixture_path.write_bytes(_fixture_bytes())
    credential_path.write_bytes(canonical_json_bytes(_credentials_document()))
    hardware_path.write_bytes(canonical_json_bytes(_hardware_document()))
    config = PROFILE_CONFIG["steady"]

    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            str(script),
            "--base-url",
            "https://127.0.0.1:10011",
            "--notion-base-url",
            "https://127.0.0.1:10012",
            "--fixture",
            str(fixture_path),
            "--credentials",
            str(credential_path),
            "--hardware",
            str(hardware_path),
            "--profile",
            "steady",
            "--clients",
            str(config["clients"]),
            "--ramp-seconds",
            str(config["ramp_seconds"]),
            "--duration-seconds",
            str(config["duration_seconds"]),
            "--cooldown-seconds",
            str(config["cooldown_seconds"]),
            "--seed",
            "0x5077",
            "--output",
            str((tmp_path / "unused.json").resolve()),
            "--validate-only",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert json.loads(completed.stdout) == {
        "client_count": 40,
        "fixture_document_sha256": sha256_bytes(_fixture_bytes()),
        "fixture_id": "perf001-fixed-v1",
        "hardware_document_sha256": sha256_bytes(
            canonical_json_bytes(_hardware_document())
        ),
        "profile": "steady",
        "status": "valid",
    }
    assert not (tmp_path / "unused.json").exists()


def test_perf_001_report_pair_is_create_only_and_csv_hash_bound(tmp_path: Path) -> None:
    report = {
        "report_schema": "code-assist.performance-load-report",
        "report_sha256": "a" * 64,
        "samples": [
            {
                "busy_failure": False,
                "canary_exposure": False,
                "client_index": 0,
                "database_backed": True,
                "endpoint_class": "crud",
                "error_code": None,
                "invariant_code": None,
                "latency_ms": 1.25,
                "metric_kind": "request",
                "operation_class": "item_read",
                "phase": "measure",
                "profile": "steady",
                "sequence": 0,
                "started_offset_ms": 0,
                "status": 200,
                "step_name": "read",
                "success": True,
            }
        ],
    }
    output = (tmp_path / "result.json").resolve()
    _, csv_path = write_report_pair(output, report)
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["csv"]["filename"] == "result.csv"
    assert document["csv"]["sha256"] == sha256_bytes(csv_path.read_bytes())
    assert "private-password" not in output.read_text(encoding="utf-8")
    with pytest.raises(PerformanceLoadError) as caught:
        write_report_pair(output, report)
    assert caught.value.code == "OUTPUT_EXISTS"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-relative evidence writer")
def test_perf_001_output_parent_identity_change_prevents_file_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    original = legacy_inventory._observe_output_parent
    calls = 0

    def changing(parent: Path):
        nonlocal calls
        resolved, identity = original(parent)
        calls += 1
        return resolved, (identity[0], identity[1] + (1 if calls > 1 else 0))

    monkeypatch.setattr(legacy_inventory, "_observe_output_parent", changing)
    output = (tmp_path / "must-not-exist.json").resolve()
    with pytest.raises(PerformanceLoadError) as caught:
        _write_new(output, b"evidence")
    assert caught.value.code == "OUTPUT_WRITE_FAILED"
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-pending semantics")
def test_perf_001_output_is_not_hardlinkable_while_bytes_are_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    original = legacy_inventory._write_windows_handle
    output = (tmp_path / "evidence.json").resolve()
    escaped = (tmp_path / "must-not-link.json").resolve()
    attempts = 0

    def probing(handle: int, payload: bytes, *, flush: bool = True) -> None:
        nonlocal attempts
        attempts += 1
        with pytest.raises(OSError):
            os.link(output, escaped)
        original(handle, payload, flush=flush)

    monkeypatch.setattr(legacy_inventory, "_write_windows_handle", probing)
    _write_new(output, b"sealed-evidence")
    assert attempts == 1
    assert output.read_bytes() == b"sealed-evidence"
    assert not escaped.exists()


def _health_binding(
    *, execute_count: int = 100, service_id: str = "CodeAssist-Web"
) -> dict[str, object]:
    return {
        "application": "code-assist-v1",
        "application_version": "p7.6-health-v1",
        "config_sha256": "1" * 64,
        "database_metrics": {
            "application_retry_count": 0,
            "execute_count": execute_count,
            "sqlite_busy_count": 0,
            "sqlite_locked_count": 0,
        },
        "gate": {"reason_codes": [], "status": "ready"},
        "jobs": {
            "attempt_count": 0,
            "oldest_nonterminal_age_ms": None,
            "retry_count": 0,
            "state_counts": {
                "cancelled": 0,
                "claimed": 0,
                "failed": 0,
                "queued": 0,
                "running": 0,
                "succeeded": 0,
            },
            "unrecoverable_count": 0,
        },
        "migration_catalog_sha256": "2" * 64,
        "outbox": {
            "attempt_count": 0,
            "oldest_nonterminal_age_ms": None,
            "retry_count": 0,
            "state_counts": {
                "claimed": 0,
                "dead": 0,
                "pending": 0,
                "sent": 0,
            },
        },
        "projection": {
            "built_generation": 5,
            "desired_generation": 5,
            "error_count": 0,
            "lag_generation": 0,
            "lagging_count": 0,
        },
        "schema_migration": {
            "checksum_sha256": "3" * 64,
            "name": "legacy_p25_effect_map",
            "version": 7,
        },
        "service_id": service_id,
        "sqlite": {"source_id": "source-id", "version": "3.50.4"},
    }


def _written_valid_report(tmp_path: Path) -> tuple[Path, Path]:
    fixture = parse_fixture(_fixture_bytes())
    sample = Sample(
        sequence=0,
        client_index=0,
        phase="measure",
        profile="hot-item",
        operation_class="hot_item",
        step_name="hot-item",
        endpoint_class="crud",
        metric_kind="request",
        started_offset_ms=0,
        latency_ms=2.5,
        status=200,
        success=True,
        error_code=None,
        database_backed=True,
        busy_failure=False,
        canary_exposure=False,
    )
    invariant = Sample(
        sequence=0,
        client_index=0,
        phase="measure",
        profile="hot-item",
        operation_class="hot_item",
        step_name="hot-item.assert",
        endpoint_class="crud",
        metric_kind="invariant",
        started_offset_ms=0,
        latency_ms=0.0,
        status=200,
        success=True,
        error_code=None,
        database_backed=False,
        busy_failure=False,
        canary_exposure=False,
        invariant_code="STALE_SAVE_NO_REVISION",
    )
    samples = [sample, invariant]
    before = _health_binding()
    after = _health_binding(execute_count=101)
    notion_before = _health_binding(service_id="CodeAssist-Notion")
    notion_after = _health_binding(
        execute_count=101, service_id="CodeAssist-Notion"
    )
    config = PROFILE_CONFIG["hot-item"]
    health_observations = [
        {"health": before, "offset_ms": index * 5_000}
        for index in range(
            max(
                1,
                (
                    config["ramp_seconds"]
                    + config["duration_seconds"]
                    + config["cooldown_seconds"]
                )
                // 5,
            )
        )
    ]
    report = {
        "configuration": {
            "clients": config["clients"],
            "cooldown_seconds": config["cooldown_seconds"],
            "duration_seconds": config["duration_seconds"],
            "fixture_document_sha256": fixture.document_sha256,
            "fixture_id": fixture.fixture_id,
            "ramp_seconds": config["ramp_seconds"],
            "seed": 0x5077,
            "steady_weights": None,
        },
        "hardware": {
            "cpu_model": "Target CPU",
            "document_sha256": sha256_bytes(
                canonical_json_bytes(_hardware_document())
            ),
            "gpu_memory_bytes": 24 * 1024**3,
            "gpu_model": "Target GPU",
            "logical_cpu_count": 32,
            "os_name": "Windows Server",
            "os_version": "2025",
            "ram_bytes": 128 * 1024**3,
            "server_id": "target-class-a",
            "storage_model": "Local NVMe",
        },
        "health_after": after,
        "health_before": before,
        "health_observations": health_observations,
        "health_sample_error_codes": [],
        "notion_health_after": notion_after,
        "notion_health_before": notion_before,
        "notion_target_origin_sha256": "8" * 64,
        "profile": "hot-item",
        "report_schema": "code-assist.performance-load-report",
        "report_sha256": "a" * 64,
        "run": {
            "completed_at_utc": "2026-08-16T01:06:00.000Z",
            "elapsed_ms": 360000,
            "run_id": "88888888-8888-4888-8888-888888888888",
            "started_at_utc": "2026-08-16T01:00:00.000Z",
        },
        "samples": [row.as_dict() for row in samples],
        "sanitized_client_binding_sha256": _client_binding_sha256(fixture),
        "schema_version": 1,
        "stage": "completed",
        "summary": _finalize_summary(
            samples,
            profile="hot-item",
            health_before=before,
            health_after=after,
            notion_health_before=notion_before,
            notion_health_after=notion_after,
            health_observations=[row["health"] for row in health_observations],
        ),
        "target_origin_sha256": "7" * 64,
    }
    return write_report_pair((tmp_path / "hot-item.json").resolve(), report)


def _written_profile_report(
    tmp_path: Path, profile: str, index: int
) -> tuple[Path, Path]:
    fixture = parse_fixture(_fixture_bytes())
    operation = {
        "steady": _steady_schedule(0x5077)[0],
        "hot-item": "hot_item",
        "write-saturation": "write_saturation",
        "projection-burst": "projection_mutation",
        "leak-scan": "leak_api",
    }[profile]
    step = fixture.templates[operation].steps[0]
    sample = Sample(
        sequence=0,
        client_index=0,
        phase="measure",
        profile=profile,
        operation_class=operation,
        step_name=step.name,
        endpoint_class=step.endpoint_class,
        metric_kind="request",
        started_offset_ms=PROFILE_CONFIG[profile]["ramp_seconds"] * 1000,
        latency_ms=2.5,
        status=step.expected_status[0],
        success=True,
        error_code=None,
        database_backed=step.database_backed,
        busy_failure=False,
        canary_exposure=False,
    )
    before = _health_binding()
    after = _health_binding(execute_count=101 + index)
    notion_before = _health_binding(service_id="CodeAssist-Notion")
    notion_after = _health_binding(
        execute_count=101 + index, service_id="CodeAssist-Notion"
    )
    config = PROFILE_CONFIG[profile]
    minimum = max(
        1,
        (
            config["ramp_seconds"]
            + config["duration_seconds"]
            + config["cooldown_seconds"]
        )
        // 5,
    )
    observations = [
        {"health": before, "offset_ms": offset * 5_000}
        for offset in range(minimum)
    ]
    elapsed_ms = (
        config["ramp_seconds"]
        + config["duration_seconds"]
        + config["cooldown_seconds"]
    ) * 1000
    report = {
        "configuration": {
            "clients": config["clients"],
            "cooldown_seconds": config["cooldown_seconds"],
            "duration_seconds": config["duration_seconds"],
            "fixture_document_sha256": fixture.document_sha256,
            "fixture_id": fixture.fixture_id,
            "ramp_seconds": config["ramp_seconds"],
            "seed": 0x5077,
            "steady_weights": dict(STEADY_WEIGHTS) if profile == "steady" else None,
        },
        "hardware": {
            "cpu_model": "Target CPU",
            "document_sha256": sha256_bytes(
                canonical_json_bytes(_hardware_document())
            ),
            "gpu_memory_bytes": 24 * 1024**3,
            "gpu_model": "Target GPU",
            "logical_cpu_count": 32,
            "os_name": "Windows Server",
            "os_version": "2025",
            "ram_bytes": 128 * 1024**3,
            "server_id": "target-class-a",
            "storage_model": "Local NVMe",
        },
        "health_after": after,
        "health_before": before,
        "health_observations": observations,
        "health_sample_error_codes": [],
        "notion_health_after": notion_after,
        "notion_health_before": notion_before,
        "notion_target_origin_sha256": "8" * 64,
        "profile": profile,
        "report_schema": "code-assist.performance-load-report",
        "report_sha256": "a" * 64,
        "run": {
            "completed_at_utc": "2026-08-16T02:00:00.000Z",
            "elapsed_ms": elapsed_ms,
            "run_id": f"88888888-8888-4888-8888-{index + 1:012x}",
            "started_at_utc": "2026-08-16T01:00:00.000Z",
        },
        "samples": [sample.as_dict()],
        "sanitized_client_binding_sha256": _client_binding_sha256(fixture),
        "schema_version": 1,
        "stage": "completed",
        "summary": _finalize_summary(
            [sample],
            profile=profile,
            health_before=before,
            health_after=after,
            notion_health_before=notion_before,
            notion_health_after=notion_after,
            health_observations=[row["health"] for row in observations],
        ),
        "target_origin_sha256": "7" * 64,
    }
    return write_report_pair((tmp_path / f"{profile}.json").resolve(), report)


def test_perf_001_written_report_recomputes_samples_summary_csv_and_hash(
    tmp_path: Path,
) -> None:
    output, csv_path = _written_valid_report(tmp_path)
    value = json.loads(output.read_text(encoding="utf-8"))
    csv_bytes = csv_path.read_bytes()
    fixture = parse_fixture(_fixture_bytes())
    assert performance_report_errors(
        value, csv_bytes=csv_bytes, fixture=fixture
    ) == ()

    value["summary"]["pass"] = False
    payload = dict(value)
    payload.pop("report_sha256")
    value["report_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    assert performance_report_errors(value, csv_bytes=csv_bytes, fixture=fixture) == (
        "summary_binding",
    )


def test_perf_001_report_validator_rejects_csv_drift_and_is_total(
    tmp_path: Path,
) -> None:
    output, csv_path = _written_valid_report(tmp_path)
    value = json.loads(output.read_text(encoding="utf-8"))
    fixture = parse_fixture(_fixture_bytes())
    assert performance_report_errors(
        value, csv_bytes=csv_path.read_bytes() + b"x", fixture=fixture
    ) == (
        "csv_binding",
    )
    assert performance_report_errors(
        {"configuration": {"clients": []}}, csv_bytes=None, fixture=fixture
    )


def test_perf_001_report_validator_binds_fixture_steps_and_both_services(
    tmp_path: Path,
) -> None:
    output, csv_path = _written_valid_report(tmp_path)
    fixture = parse_fixture(_fixture_bytes())
    value = json.loads(output.read_text(encoding="utf-8"))
    value["samples"][0]["endpoint_class"] = "search"
    errors = performance_report_errors(
        value, csv_bytes=csv_path.read_bytes(), fixture=fixture
    )
    assert "fixture_sample_binding" in errors

    value = json.loads(output.read_text(encoding="utf-8"))
    value["notion_health_before"]["service_id"] = "CodeAssist-Web"
    errors = performance_report_errors(
        value, csv_bytes=csv_path.read_bytes(), fixture=fixture
    )
    assert "service_binding" in errors


def test_perf_001_independent_verifier_returns_stable_valid_or_invalid_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output, csv_path = _written_valid_report(tmp_path)
    fixture_path = (tmp_path / "fixture.json").resolve()
    fixture_path.write_bytes(_fixture_bytes())
    verify_arguments = [
        "--report", str(output), "--csv", str(csv_path),
        "--fixture", str(fixture_path),
    ]
    assert verify_main(verify_arguments) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["status"] == "valid"
    assert success["pass"] is True

    csv_path.write_bytes(csv_path.read_bytes() + b"tamper")
    assert verify_main(verify_arguments) == 3
    invalid = json.loads(capsys.readouterr().out)
    assert invalid == {"errors": ["csv_binding"], "status": "invalid"}


def test_perf_001_gate_binds_exactly_five_profiles_to_one_target(
    tmp_path: Path,
) -> None:
    pairs = []
    for index, profile in enumerate(PROFILE_ORDER):
        report_path, csv_path = _written_profile_report(tmp_path, profile, index)
        pairs.append(
            (
                json.loads(report_path.read_text(encoding="utf-8")),
                csv_path.read_bytes(),
            )
        )

    fixture = parse_fixture(_fixture_bytes())
    gate = build_performance_gate(pairs, fixture=fixture)
    assert gate["gate_schema"] == GATE_SCHEMA
    assert [row["profile"] for row in gate["profiles"]] == list(PROFILE_ORDER)
    assert gate["summary"] == {
        "all_profiles_pass": False,
        "profile_count": 5,
        "profiles_passed": 1,
    }
    assert performance_gate_errors(gate) == ()

    gate["profiles"][0]["passed"] = True
    assert performance_gate_errors(gate) == ("gate_hash", "gate_summary")


def test_perf_001_gate_rejects_missing_profile_or_target_binding_drift(
    tmp_path: Path,
) -> None:
    pairs = []
    for index, profile in enumerate(PROFILE_ORDER):
        report_path, csv_path = _written_profile_report(tmp_path, profile, index)
        pairs.append(
            (
                json.loads(report_path.read_text(encoding="utf-8")),
                csv_path.read_bytes(),
            )
        )
    with pytest.raises(PerformanceLoadError) as missing:
        build_performance_gate(
            pairs[:-1], fixture=parse_fixture(_fixture_bytes())
        )
    assert missing.value.code == "GATE_PROFILE_SET_INVALID"

    drifted = json.loads(json.dumps(pairs[-1][0]))
    changed_hardware = _hardware_document()
    changed_hardware["server_id"] = "target-class-b"
    drifted["hardware"]["server_id"] = "target-class-b"
    drifted["hardware"]["document_sha256"] = sha256_bytes(
        canonical_json_bytes(changed_hardware)
    )
    payload = dict(drifted)
    payload.pop("report_sha256")
    drifted["report_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    changed_pairs = [*pairs[:-1], (drifted, pairs[-1][1])]
    with pytest.raises(PerformanceLoadError) as drift:
        build_performance_gate(
            changed_pairs, fixture=parse_fixture(_fixture_bytes())
        )
    assert drift.value.code == "GATE_TARGET_BINDING_DRIFT"


def test_perf_001_gate_cli_writes_one_create_only_bound_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments: list[str] = []
    for index, profile in enumerate(PROFILE_ORDER):
        report_path, csv_path = _written_profile_report(tmp_path, profile, index)
        arguments.extend(["--report-pair", str(report_path), str(csv_path)])
    output = (tmp_path / "perf-001-gate.json").resolve()
    fixture_path = (tmp_path / "fixture.json").resolve()
    fixture_path.write_bytes(_fixture_bytes())
    arguments.extend(
        ["--fixture", str(fixture_path), "--output", str(output)]
    )

    assert verify_gate_main(arguments) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "valid"
    assert result["pass"] is False
    gate = json.loads(output.read_text(encoding="utf-8"))
    assert performance_gate_errors(gate) == ()

    assert verify_gate_main(arguments) == 3
    assert json.loads(capsys.readouterr().out) == {
        "code": "OUTPUT_EXISTS",
        "status": "error",
    }


def test_perf_001_step_executor_binds_capture_and_measures_projection_lag() -> None:
    class Session:
        calls = 0

        def request(self, method, path, *, body=None, headers=None):
            del method, path, body, headers
            self.calls += 1
            return HttpResult(
                status=200,
                headers={"x-result": "header-value"},
                body=canonical_json_bytes(
                    {"lag_generations": 0, "resource_id": "captured-resource"}
                ),
                latency_ms=1.5,
            )

    step = Step(
        name="projection",
        method="POST",
        path="/api/knowledge/v1/spaces/${space_id}/search",
        body={"operation_id": "${operation_id}", "query": "safe"},
        headers={},
        expected_status=(200,),
        database_backed=True,
        endpoint_class="projection",
        captures={"resource_id": Capture("json", "/resource_id")},
        forbid_body_values=("foreign-canary",),
        poll=Poll("/lag_generations", 0, 1, 1),
    )
    template = OperationTemplate(
        name="projection_mutation",
        weight=0,
        generate={"operation_id": "uuid"},
        steps=(step,),
    )
    client = LoadClient(
        client_id="client-00",
        user_id="load-user-00",
        part_key="part-0",
        variables={"space_id": "44444444-4444-4444-8444-444444444444"},
        leak_cases=(),
    )
    rows, success = _execute_operation(
        session=Session(),
        client=client,
        client_index=0,
        template=template,
        profile="projection-burst",
        sequence=1,
        run_started=0.0,
        steady_start=0.0,
    )
    assert success is True
    assert [row.metric_kind for row in rows] == ["request", "projection_lag"]
    assert rows[1].endpoint_class == "projection"
    assert rows[1].success is True


def test_perf_001_step_executor_records_invariant_failure_as_raw_sample() -> None:
    class Session:
        def request(self, method, path, *, body=None, headers=None):
            del method, path, body, headers
            return HttpResult(
                status=200,
                headers={},
                body=canonical_json_bytes(
                    {"invariants": {"stale_save_no_revision": False}}
                ),
                latency_ms=1.0,
            )

    step = Step(
        name="stale-check",
        method="GET",
        path="/api/knowledge/v1/spaces/${space_id}/items/${item_id}",
        body=None,
        headers={},
        expected_status=(200,),
        database_backed=True,
        endpoint_class="crud",
        captures={},
        forbid_body_values=(),
        poll=None,
        assertions=(
            InvariantAssertion(
                code="STALE_SAVE_NO_REVISION",
                operator="equals",
                pointers=("/invariants/stale_save_no_revision",),
                expected=True,
            ),
        ),
    )
    template = OperationTemplate(
        name="hot_item", weight=0, generate={}, steps=(step,)
    )
    client = LoadClient(
        client_id="client-00",
        user_id="load-user-00",
        part_key="part-0",
        variables={
            "item_id": "33333333-3333-4333-8333-333333333333",
            "space_id": "44444444-4444-4444-8444-444444444444",
        },
        leak_cases=(),
    )
    rows, success = _execute_operation(
        session=Session(),
        client=client,
        client_index=0,
        template=template,
        profile="hot-item",
        sequence=1,
        run_started=0.0,
        steady_start=0.0,
    )
    assert success is False
    assert [row.metric_kind for row in rows] == ["request", "invariant"]
    assert rows[1].invariant_code == "STALE_SAVE_NO_REVISION"
    assert rows[1].error_code == "STALE_SAVE_NO_REVISION"
    assert rows[1].success is False


def test_perf_001_http_session_logs_in_with_cookie_and_csrf_without_leaking_secret() -> None:
    observed: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return None

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if self.path == "/api/code/auth/login":
                assert b"private-password" in body
                payload = canonical_json_bytes({"csrf_token": "A" * 43})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "session=opaque; Path=/; HttpOnly")
            else:
                observed.append(
                    {
                        "cookie": self.headers.get("Cookie", ""),
                        "csrf": self.headers.get("X-CSRF-Token", ""),
                    }
                )
                payload = canonical_json_bytes({"ok": True})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        session = _Session(
            base_url=f"http://127.0.0.1:{port}",
            origin=f"http://127.0.0.1:{port}",
            timeout_seconds=5,
            tls_context=ssl.create_default_context(),
        )
        credential = Credential(
            "client-00", "load-user-00", "private-password-value"
        )
        session.login(credential)
        result = session.request(
            "POST",
            "/api/knowledge/v1/spaces/44444444-4444-4444-8444-444444444444/search",
            body={"query": "safe"},
        )
        assert result.status == 200
        assert observed == [{"cookie": "session=opaque", "csrf": "A" * 43}]
        assert "private-password" not in repr(session)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
