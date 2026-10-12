from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import threading
from uuid import uuid4
import xml.etree.ElementTree as ET

import pytest

from code_assist_v1.knowledge_core.production_config import (
    ProductionPaths,
    WINSW_SHA256,
    canonical_json_bytes,
    parse_production_config,
    production_config_document,
)
from code_assist_v1.knowledge_core.production_preflight import (
    PRODUCTION_PREFLIGHT_SCHEMA,
    build_production_preflight_report,
)
from code_assist_v1.knowledge_core.production_service import (
    ProductionServiceAlreadyRunningError,
    ProductionServiceError,
    WindowsStopEvent,
    apply_production_environment,
    parse_protected_environment,
    run_deployment_migrator,
    run_durable_worker_service,
    run_projector_service,
    run_web_service,
    stop_event_name,
)
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core import production_service as core_production_service
from code_assist_v1.knowledge_core.production_wsgi import ProductionWSGIConfig
from code_assist_v1.tools.production_service import render_winsw_xml
from code_assist_v1.tools import production_service as production_service_tool


pytestmark = pytest.mark.unit


def _paths(prefix: str = "C:\\CodeAssist") -> ProductionPaths:
    drive = nt_root = prefix.split("\\", 1)[0] + "\\"
    del drive
    return ProductionPaths(
        backup_root=nt_root + "CodeAssistBackup",
        blob_root=nt_root + "CodeAssistData\\blobs",
        data_root=nt_root + "CodeAssistData",
        database=nt_root + "CodeAssistData\\core\\knowledge-core.db",
        environment_file=prefix + "\\config\\production.env",
        log_root=nt_root + "CodeAssistLogs",
        part_write_rollout_record=prefix + "\\config\\rollout.json",
        projection_root=nt_root + "CodeAssistData\\projections",
        python_executable=prefix + "\\venv\\Scripts\\python.exe",
        runtime_lock=prefix + "\\requirements-runtime.lock",
        service_wrapper_executable=prefix + "\\service\\WinSW-x64.exe",
        working_directory=prefix,
    )


def _config(paths: ProductionPaths | None = None):
    document = production_config_document(
        deployment_id="code-assist-prod",
        service_account="CODEHOST\\svc_codeassist",
        paths=paths or _paths(),
    )
    return parse_production_config(canonical_json_bytes(document))


def test_protected_environment_is_canonical_and_never_owns_process_authority() -> None:
    values = parse_protected_environment(
        b"# protected deployment values\n"
        b"CODE_ASSIST_ALLOWED_ORIGINS=http://127.0.0.1:10011,http://127.0.0.1:10012,https://code.example\n"
        b"OPENAI_API_KEY=sentinel-value\n"
    )
    assert values == {
        "CODE_ASSIST_ALLOWED_ORIGINS": "http://127.0.0.1:10011,http://127.0.0.1:10012,https://code.example",
        "OPENAI_API_KEY": "sentinel-value",
    }
    for raw in (
        b"CODE_ASSIST_PROCESS_ROLE=web\n",
        b"CODE_ASSIST_TEST_MODE=1\n",
        b"CODE_ASSIST_FAILPOINT_X=1\n",
        b"PYTHONPATH=C:\\untrusted\n",
        b"OPENAI_API_KEY=x\r\n",
        b"OPENAI_API_KEY=x\nOPENAI_API_KEY=y\n",
    ):
        with pytest.raises(ProductionServiceError):
            parse_protected_environment(raw)


def test_environment_application_sets_manifest_authority_and_rejects_inherited_test_mode() -> None:
    config = _config()
    target: dict[str, str] = {}
    apply_production_environment(
        config,
        service_id="CodeAssist-Web",
        values={
            "CODE_ASSIST_ALLOWED_ORIGINS": (
                "http://127.0.0.1:10011,http://127.0.0.1:10012"
            ),
            "OPENAI_API_KEY": "sentinel",
        },
        environ=target,
    )
    assert target["CODE_ASSIST_PROCESS_ROLE"] == "web"
    assert target["CODE_ASSIST_DATA_ROOT"] == config.paths.data_root
    assert target["CODE_ASSIST_BROWSER_SECURITY_PRODUCTION"] == "1"
    assert target["CODE_ASSIST_STANDALONE"] == "0"
    assert target["OPENAI_API_KEY"] == "sentinel"

    with pytest.raises(ProductionServiceError, match="Test or failpoint"):
        apply_production_environment(
            config,
            service_id="CodeAssist-Web",
            values={},
            environ={"CODE_ASSIST_TEST_MODE": "1"},
        )


@pytest.mark.parametrize(
    "values",
    [
        {"CODE_ASSIST_PROCESS_ROLE": "worker"},
        {"CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "0"},
        {"CODE_ASSIST_FAILPOINT_AFTER_WRITE": "1"},
        {"OPENAI_API_KEY": "sentinel\nleak"},
        {"CODE_ASSIST_ALLOWED_ORIGINS": "https://code.example"},
    ],
)
def test_environment_application_revalidates_direct_mapping_authority(
    values: dict[str, str],
) -> None:
    target = {"OPENAI_API_KEY": "ambient", "OUTLOOK_CLIENT_ID": "ambient"}
    with pytest.raises(ProductionServiceError):
        apply_production_environment(
            _config(),
            service_id="CodeAssist-Web",
            values=values,
            environ=target,
        )
    assert target == {"OPENAI_API_KEY": "ambient", "OUTLOOK_CLIENT_ID": "ambient"}


def test_environment_application_removes_unreviewed_ambient_application_values() -> None:
    target = {
        "OPENAI_API_KEY": "ambient",
        "OUTLOOK_CLIENT_ID": "ambient",
        "SYSTEMROOT": "C:\\Windows",
    }
    apply_production_environment(
        _config(),
        service_id="CodeAssist-Web",
        values={
            "CODE_ASSIST_ALLOWED_ORIGINS": (
                "http://127.0.0.1:10011,http://127.0.0.1:10012"
            ),
            "OPENAI_API_KEY": "reviewed",
        },
        environ=target,
    )
    assert target["OPENAI_API_KEY"] == "reviewed"
    assert "OUTLOOK_CLIENT_ID" not in target
    assert target["SYSTEMROOT"] == "C:\\Windows"


@pytest.mark.skipif(os.name != "nt", reason="Windows service event contract")
def test_named_stop_event_is_single_owner_and_cross_handle_signalable() -> None:
    name = stop_event_name(
        f"p75-{uuid4().hex[:12]}",
        "CodeAssist-Web",
    )
    with WindowsStopEvent.create(name) as event:
        assert event.is_set() is False
        with pytest.raises(ProductionServiceAlreadyRunningError):
            WindowsStopEvent.create(name)
        WindowsStopEvent.signal_existing(name)
        assert event.wait(2) is True
        assert event.is_set() is True


def test_web_service_binds_reviewed_port_and_host_to_the_selected_application() -> None:
    config = _config()
    app = lambda environ, start_response: []
    observed: list[tuple[object, ProductionWSGIConfig, object]] = []

    class Host:
        def __init__(self, application, wsgi_config) -> None:
            self.application = application
            self.config = wsgi_config

        def serve(self, stop) -> None:
            observed.append((self.application, self.config, stop))

    stop = threading.Event()
    run_web_service(
        config,
        service_id="CodeAssist-Notion",
        stop=stop,
        host_factory=Host,  # type: ignore[arg-type]
        application_provider=lambda kind: app if kind == "notion" else None,
    )
    assert observed == [(app, ProductionWSGIConfig("notion-web", "0.0.0.0", 10012), stop)]


def test_actual_web_composition_injects_the_bound_deep_health_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1 import app_code
    from code_assist_v1.knowledge_core import production_health

    config = _config()
    captured: dict[str, object] = {}
    app = lambda environ, start_response: []

    class Health:
        def __init__(self, factory, **kwargs) -> None:
            captured["database"] = os.fspath(factory.database_path)
            captured.update(kwargs)

    class Host:
        def __init__(self, application, wsgi_config) -> None:
            captured["application"] = application
            captured["wsgi"] = wsgi_config

        def serve(self, stop) -> None:
            captured["stop"] = stop

    def create_app(**kwargs):
        captured["health"] = kwargs.get("production_health_provider")
        return app

    monkeypatch.setattr(production_health, "ProductionHealthService", Health)
    monkeypatch.setattr(app_code, "create_app", create_app)
    stop = threading.Event()
    run_web_service(
        config,
        service_id="CodeAssist-Web",
        stop=stop,
        host_factory=Host,  # type: ignore[arg-type]
    )
    assert captured["application"] is app
    assert isinstance(captured["health"], Health)
    assert captured["database"] == config.paths.database
    assert captured["blob_root"] == config.paths.blob_root
    assert captured["config_sha256"] == config.config_sha256
    assert captured["service_id"] == "CodeAssist-Web"
    assert captured["wsgi"] == ProductionWSGIConfig(
        "code-web", "0.0.0.0", 10011
    )
    assert captured["stop"] is stop


@pytest.mark.parametrize("storage_present", (True, False))
def test_code_web_composes_canonical_agent_dependencies_when_read_flags_enable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_present: bool,
) -> None:
    from code_assist_v1 import app_code, auth
    from code_assist_v1.knowledge_core import production_health
    from code_assist_v1.knowledge_core.errors import KnowledgeCoreDatabasePathError
    from code_assist_v1.knowledge_core.legacy.blob_stager import FilesystemLegacyBlobStager
    from code_assist_v1.knowledge_core.agent_execution import (
        CanonicalAgentTurnExecutor,
        CanonicalConversationSummarizer,
    )

    for name, value in {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PERSONAL_WRITES": "0",
        "KNOWLEDGE_CORE_PART_READS": "1",
        "KNOWLEDGE_CORE_PART_WRITES": "0",
        "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1",
        "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1",
        "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": "0",
        "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": "0",
        "KNOWLEDGE_CORE_PROJECTIONS": "0",
        "KNOWLEDGE_CORE_HIGHER_SPACES": "0",
    }.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, object] = {}
    application = lambda environ, start_response: []

    class Health:
        def __init__(self, factory, **kwargs) -> None:
            captured["factory"] = factory
            captured["health_kwargs"] = kwargs

    class Host:
        def __init__(self, app, wsgi_config) -> None:
            captured["application"] = app

        def serve(self, stop) -> None:
            captured["stop"] = stop

    def create_app(**kwargs):
        captured.update(kwargs)
        return application

    monkeypatch.setattr(production_health, "ProductionHealthService", Health)
    monkeypatch.setattr(app_code, "create_app", create_app)
    monkeypatch.setattr(auth, "ensure_secret_key", lambda: "isolated-composition-test-secret")
    # Exercise the real filesystem stager against this test's storage layout;
    # an example C:\\CodeAssistData path is not a deployable storage fixture.
    paths = _real_paths(tmp_path)
    if not storage_present:
        paths = replace(paths, blob_root=os.fspath(Path(paths.data_root) / "missing-blobs"))
    config = _config(paths)
    stop = threading.Event()
    if not storage_present:
        with pytest.raises(KnowledgeCoreDatabasePathError):
            run_web_service(
                config,
                service_id="CodeAssist-Web",
                stop=stop,
                host_factory=Host,  # type: ignore[arg-type]
            )
        assert "knowledge_core_services" not in captured
        assert "application" not in captured
        assert "stop" not in captured
        assert not Path(paths.blob_root).exists()
        assert Path(paths.database).read_bytes() == b"x"
        return
    run_web_service(
        config,
        service_id="CodeAssist-Web",
        stop=stop,
        host_factory=Host,  # type: ignore[arg-type]
    )
    services = captured["knowledge_core_services"]
    bindings = captured["knowledge_core_route_bindings"]
    assert services.scope_selection is not None
    assert services.conversations is not None
    assert services.retrieval is not None
    assert services.management_reads is not None
    assert services.agent_context is not None
    assert services.personal_wikis is not None
    assert services.source_artifacts is not None
    stager = services.source_artifacts._stager
    assert isinstance(stager, FilesystemLegacyBlobStager)
    assert stager.resolved_root == os.fspath(Path(paths.blob_root).resolve())
    assert os.fspath(services.source_artifacts._factory.database_path) == paths.database
    assert isinstance(bindings.turn_executor, CanonicalAgentTurnExecutor)
    assert isinstance(bindings.conversation_summarizer, CanonicalConversationSummarizer)
    assert callable(bindings.principal_resolver)
    assert captured["application"] is application
    assert captured["stop"] is stop
    assert Path(paths.database).read_bytes() == b"x"


def test_winsw_xml_freezes_graceful_stop_dependencies_logs_and_no_secret(
    tmp_path: Path,
) -> None:
    config = _config()
    path = tmp_path / "production-services.json"
    raw = canonical_json_bytes(config.as_dict())
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    xml = render_winsw_xml(
        config,
        config_path=os.fspath(path),
        config_document_sha256=digest,
        service_id="CodeAssist-DurableWorker",
    )
    root = ET.fromstring(xml)
    assert root.findtext("id") == "CodeAssist-DurableWorker"
    assert root.findtext("executable") == config.paths.python_executable
    assert root.findtext("stopexecutable") == config.paths.python_executable
    assert root.findtext("stoptimeout") == "45 sec"
    assert root.findtext("stopparentprocessfirst") == "false"
    assert [item.text for item in root.findall("depend")] == [
        "CodeAssist-Web",
        "CodeAssist-Notion",
    ]
    assert root.find("serviceaccount") is None
    assert root.find("env") is None
    start = root.findtext("startarguments") or ""
    stop = root.findtext("stoparguments") or ""
    assert " run" in start and " stop" in stop
    assert digest in start and digest in stop
    assert b"sentinel" not in xml
    log = root.find("log")
    assert log is not None and log.attrib == {"mode": "roll-by-size"}
    assert log.findtext("sizeThreshold") == "10240"
    assert log.findtext("keepFiles") == "8"


def test_service_package_render_is_create_once_exact_replay_and_drift_rejecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _real_paths(tmp_path)
    config = _config(paths)
    config_path = Path(paths.working_directory) / "config" / "production-services.json"
    raw = canonical_json_bytes(config.as_dict())
    config_path.write_bytes(raw)
    document_sha256 = hashlib.sha256(raw).hexdigest()
    wrapper_sha256 = hashlib.sha256(b"x").hexdigest()
    monkeypatch.setattr(production_service_tool, "WINSW_SHA256", wrapper_sha256)

    outputs = production_service_tool.render_service_package(
        config,
        config_path=os.fspath(config_path),
        config_document_sha256=document_sha256,
    )
    assert len(outputs) == 10
    assert all(Path(path).is_file() for path in outputs)
    assert production_service_tool.render_service_package(
        config,
        config_path=os.fspath(config_path),
        config_document_sha256=document_sha256,
    ) == outputs

    Path(outputs[-1]).write_bytes(b"drift")
    with pytest.raises(OSError, match="service_package_drift"):
        production_service_tool.render_service_package(
            config,
            config_path=os.fspath(config_path),
            config_document_sha256=document_sha256,
        )


def test_service_package_rejects_ineligible_storage_before_any_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _real_paths(tmp_path)
    config = _config(paths)
    config_path = Path(paths.working_directory) / "config" / "production-services.json"
    raw = canonical_json_bytes(config.as_dict())
    config_path.write_bytes(raw)
    monkeypatch.setattr(
        production_service_tool,
        "WINSW_SHA256",
        hashlib.sha256(b"x").hexdigest(),
    )
    with pytest.raises(OSError, match="service_package_storage_ineligible"):
        production_service_tool.render_service_package(
            config,
            config_path=os.fspath(config_path),
            config_document_sha256=hashlib.sha256(raw).hexdigest(),
            volume_probe=lambda _path: {
                "eligible": False,
                "exists": True,
                "reparse": {"present": True},
                "resolved_path": os.fspath(_path),
                "volume": {"drive_type_code": 3, "filesystem": "NTFS"},
            },
        )
    service_directory = Path(paths.service_wrapper_executable).parent
    assert not any(service_directory.glob("CodeAssist-*.xml"))
    assert not any(
        path.name != "CodeAssist-Web.exe"
        for path in service_directory.glob("CodeAssist-*.exe")
    )


def _real_paths(root: Path) -> ProductionPaths:
    work = root / "work"
    data = root / "data"
    logs = root / "logs"
    backup = root / "backup"
    for directory in (
        work,
        work / "config",
        work / "venv" / "Scripts",
        work / "service",
        data,
        data / "core",
        data / "blobs",
        data / "projections",
        logs,
        backup,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    for path in (
        work / "config" / "production.env",
        work / "config" / "rollout.json",
        work / "venv" / "Scripts" / "python.exe",
        work / "requirements-runtime.lock",
        work / "service" / "WinSW-x64.exe",
        work / "service" / "CodeAssist-Web.exe",
        data / "core" / "knowledge-core.db",
    ):
        path.write_bytes(b"x")
    return ProductionPaths(
        backup_root=os.fspath(backup),
        blob_root=os.fspath(data / "blobs"),
        data_root=os.fspath(data),
        database=os.fspath(data / "core" / "knowledge-core.db"),
        environment_file=os.fspath(work / "config" / "production.env"),
        log_root=os.fspath(logs),
        part_write_rollout_record=os.fspath(work / "config" / "rollout.json"),
        projection_root=os.fspath(data / "projections"),
        python_executable=os.fspath(work / "venv" / "Scripts" / "python.exe"),
        runtime_lock=os.fspath(work / "requirements-runtime.lock"),
        service_wrapper_executable=os.fspath(work / "service" / "WinSW-x64.exe"),
        working_directory=os.fspath(work),
    )


def test_production_preflight_binds_every_path_identity_account_wrapper_and_runtime(
    tmp_path: Path,
) -> None:
    paths = _real_paths(tmp_path)
    config = _config(paths)
    config_path = Path(paths.working_directory) / "config" / "production-services.json"
    config_path.write_bytes(canonical_json_bytes(config.as_dict()))

    def probe(path):
        return {
            "eligible": True,
            "exists": True,
            "reparse": {"present": False},
            "resolved_path": os.fspath(path),
            "volume": {"drive_type_code": 3, "filesystem": "NTFS"},
        }

    report, exit_code = build_production_preflight_report(
        config,
        config_path=config_path,
        service_id="CodeAssist-Web",
        runtime_report_builder=lambda *args, **kwargs: ({"gate": {"status": "pass"}}, 0),
        volume_probe=probe,
        account_provider=lambda: "codehost\\SVC_CODEASSIST",
        hash_provider=lambda _path: WINSW_SHA256,
        cwd=paths.working_directory,
        executable=paths.python_executable,
    )
    assert exit_code == 0
    assert report["report_schema"] == PRODUCTION_PREFLIGHT_SCHEMA
    assert report["gate"] == {"exit_code": 0, "reasons": [], "status": "pass"}
    assert len(report["paths"]) == 14
    assert report["wrapper"] == {
        "active_sha256_match": True,
        "sha256_match": True,
    }


def test_production_preflight_fails_closed_on_reparse_account_and_runtime_drift(
    tmp_path: Path,
) -> None:
    paths = _real_paths(tmp_path)
    config = _config(paths)
    config_path = Path(paths.working_directory) / "config" / "production-services.json"
    config_path.write_bytes(canonical_json_bytes(config.as_dict()))

    def probe(path):
        return {
            "eligible": True,
            "exists": True,
            "reparse": {"present": os.fspath(path) == paths.environment_file},
            "resolved_path": os.fspath(path),
            "volume": {"drive_type_code": 3, "filesystem": "NTFS"},
        }

    report, exit_code = build_production_preflight_report(
        config,
        config_path=config_path,
        service_id="CodeAssist-Web",
        runtime_report_builder=lambda *args, **kwargs: ({"gate": {"status": "fail"}}, 2),
        volume_probe=probe,
        account_provider=lambda: "CODEHOST\\different",
        hash_provider=lambda _path: "0" * 64,
        cwd=paths.log_root,
        executable=paths.runtime_lock,
    )
    assert exit_code == 2
    reasons = report["gate"]["reasons"]
    assert "environment_file_storage_ineligible" in reasons
    assert "service_account_mismatch" in reasons
    assert "wrapper_hash_mismatch" in reasons
    assert "runtime_preflight_failed" in reasons
    assert "working_directory_mismatch" in reasons
    assert "python_executable_mismatch" in reasons


def test_only_deployment_migrator_may_preflight_a_fresh_database_parent(
    tmp_path: Path,
) -> None:
    paths = _real_paths(tmp_path)
    Path(paths.database).unlink()
    migrator_wrapper = Path(paths.service_wrapper_executable).with_name(
        "CodeAssist-Migrator.exe"
    )
    migrator_wrapper.write_bytes(b"x")
    config = _config(paths)
    config_path = Path(paths.working_directory) / "config" / "production-services.json"
    config_path.write_bytes(canonical_json_bytes(config.as_dict()))
    runtime_calls: list[str] = []

    def probe(path):
        return {
            "eligible": True,
            "exists": True,
            "reparse": {"present": False},
            "resolved_path": os.fspath(path),
            "volume": {"drive_type_code": 3, "filesystem": "NTFS"},
        }

    report, exit_code = build_production_preflight_report(
        config,
        config_path=config_path,
        service_id="CodeAssist-Migrator",
        allow_missing_database=True,
        runtime_report_builder=lambda *_args, **_kwargs: (
            runtime_calls.append("called") or {"gate": {"status": "fail"}},
            2,
        ),
        volume_probe=probe,
        account_provider=lambda: "CODEHOST\\svc_codeassist",
        hash_provider=lambda _path: WINSW_SHA256,
        cwd=paths.working_directory,
        executable=paths.python_executable,
    )
    assert exit_code == 0
    assert runtime_calls == []
    assert report["runtime"] == {
        "gate": {"status": "deferred_to_single_migrator"}
    }
    assert any(row["role"] == "database_parent" for row in report["paths"])

    with pytest.raises(ValueError, match="Only the deployment migrator"):
        build_production_preflight_report(
            config,
            config_path=config_path,
            service_id="CodeAssist-Web",
            allow_missing_database=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows production process contracts")
def test_background_service_entrypoints_compose_real_bounded_loops_without_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _real_paths(tmp_path)
    layout = MigrationStorageLayout(
        data_root=Path(paths.data_root),
        database=Path(paths.database),
        blob_root=Path(paths.blob_root),
        projection_root=Path(paths.projection_root),
    )
    Path(paths.database).unlink()
    factory = ConnectionFactory._for_isolated_tests(
        layout.database,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p7.5-entrypoint-test",
        storage_layout=layout,
    ).run()
    config = _config(paths)
    monkeypatch.setattr(core_production_service, "_connection_factory", lambda _config: factory)
    monkeypatch.setattr(core_production_service, "_storage_layout", lambda _config: layout)
    for name in (
        "KNOWLEDGE_CORE_ENABLED",
        "KNOWLEDGE_CORE_READS",
        "KNOWLEDGE_CORE_PERSONAL_WRITES",
        "KNOWLEDGE_CORE_PART_READS",
        "KNOWLEDGE_CORE_PART_WRITES",
        "KNOWLEDGE_CORE_PROJECTIONS",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
    ):
        monkeypatch.setenv(name, "0")

    stop = threading.Event()
    stop.set()
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    worker_result = run_durable_worker_service(config, stop=stop)
    assert worker_result.iterations == 0
    assert worker_result.claimed == 0

    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "projector")
    projection_result, checkpoint_result = run_projector_service(config, stop=stop)
    assert projection_result.iterations == 0
    assert checkpoint_result.attempts == 0

    migration_result = run_deployment_migrator(config)
    assert migration_result.applied_versions == ()
