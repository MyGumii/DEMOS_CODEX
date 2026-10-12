from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from flask import Blueprint, Flask
import pytest

from code_assist_v1.knowledge_core import (
    CanonicalRouteBindings,
    EXTENSION_KEY,
    FEATURE_FLAG_NAMES,
    HostKind,
    KnowledgeCoreConfigurationError,
    KnowledgeCoreHostConflictError,
    KnowledgeCoreNotReadyError,
    KnowledgeCoreServiceUnboundError,
    ServiceRegistry,
    compose_knowledge_core,
    get_knowledge_core,
    read_feature_flags,
    register_knowledge_core,
)
from code_assist_v1.knowledge_core.legacy import LEGACY_MODULES
from code_assist_v1.knowledge_core.projection import PROJECTION_MODULES
from code_assist_v1.knowledge_core.repositories import REPOSITORY_MODULES
from code_assist_v1.knowledge_core.services import SERVICE_MODULES
from code_assist_v1.tools.runtime_preflight import FEATURE_FLAGS as PREFLIGHT_FLAGS


_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_FLAGS = frozenset({"KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA"})
_DOWNSTREAM_FLAGS = tuple(
    name
    for name in FEATURE_FLAG_NAMES
    if name != "KNOWLEDGE_CORE_ENABLED" and name not in _SCHEMA_FLAGS
)
_READY_PARENT_FLAGS = frozenset(
    {
        "KNOWLEDGE_CORE_READS",
        "KNOWLEDGE_CORE_PERSONAL_WRITES",
        "KNOWLEDGE_CORE_PART_READS",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE",
        "KNOWLEDGE_CORE_HYBRID_RERANK",
        "KNOWLEDGE_CORE_TEAM_WRITES",
        "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
        "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
        "KNOWLEDGE_CORE_TEMPORAL_RANKING",
        "KNOWLEDGE_CORE_HYBRID_RERANK",
        "KNOWLEDGE_CORE_PROJECTIONS",
        "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2",
    }
)
_PREREQUISITE_BOUND_FLAGS = frozenset(
    {
        "KNOWLEDGE_CORE_MULTI_WIKI_READS",
        "KNOWLEDGE_CORE_MULTI_WIKI_WRITES",
        "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING",
        "KNOWLEDGE_CORE_HIGHER_SPACES",
        "KNOWLEDGE_CORE_TEAM_WRITES",
        "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
    }
)
_UNREADY_FLAGS = tuple(
    name
    for name in _DOWNSTREAM_FLAGS
    if name not in _READY_PARENT_FLAGS and name not in _PREREQUISITE_BOUND_FLAGS
)


@pytest.mark.unit
def test_p1_1_architecture_module_boundaries_are_complete() -> None:
    expected = {
        "db.py",
        "domain.py",
        "errors.py",
        "routes.py",
        "scope.py",
        "worker.py",
        "migrations/__init__.py",
        *(f"repositories/{name}.py" for name in REPOSITORY_MODULES),
        *(f"services/{name}.py" for name in SERVICE_MODULES),
        *(f"projection/{name}.py" for name in PROJECTION_MODULES),
        *(f"legacy/{name}.py" for name in LEGACY_MODULES),
    }
    core_root = _PACKAGE_ROOT / "knowledge_core"
    assert not sorted(path for path in expected if not (core_root / path).is_file())
    assert REPOSITORY_MODULES == tuple(sorted(REPOSITORY_MODULES))
    assert SERVICE_MODULES == tuple(sorted(SERVICE_MODULES))
    assert PROJECTION_MODULES == tuple(sorted(PROJECTION_MODULES))
    assert LEGACY_MODULES == tuple(sorted(LEGACY_MODULES))


@pytest.mark.unit
def test_p1_1_fresh_import_is_framework_and_storage_side_effect_free(tmp_path: Path) -> None:
    modules = (
        "code_assist_v1.knowledge_core",
        "code_assist_v1.knowledge_core.db",
        "code_assist_v1.knowledge_core.scope",
        "code_assist_v1.knowledge_core.worker",
        *(f"code_assist_v1.knowledge_core.repositories.{name}" for name in REPOSITORY_MODULES),
        *(f"code_assist_v1.knowledge_core.services.{name}" for name in SERVICE_MODULES),
        *(f"code_assist_v1.knowledge_core.projection.{name}" for name in PROJECTION_MODULES),
        *(f"code_assist_v1.knowledge_core.legacy.{name}" for name in LEGACY_MODULES),
    )
    script = """
import importlib
import json
import sys

for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
blocked = [
        name for name in (
            'flask',
            'code_assist_v1.app_code',
        'code_assist_v1.app_notion',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.notion_store',
        'code_assist_v1.second_brain_store',
    )
    if name in sys.modules
]
print(json.dumps({'blocked': blocked}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in FEATURE_FLAG_NAMES:
        environment[name] = "0"
    completed = subprocess.run(
        [sys.executable, "-c", script, json.dumps(modules)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"blocked": []}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_p1_1_feature_flag_names_are_single_contract_with_preflight() -> None:
    assert FEATURE_FLAG_NAMES == PREFLIGHT_FLAGS
    assert len(FEATURE_FLAG_NAMES) == 20
    assert len(set(FEATURE_FLAG_NAMES)) == 20


@pytest.mark.unit
def test_p1_1_feature_flags_default_off_and_core_only_is_safe() -> None:
    disabled = read_feature_flags({})
    assert disabled.as_environment_map() == {name: 0 for name in sorted(FEATURE_FLAG_NAMES)}
    assert disabled.traffic_or_worker_enabled is False

    core_only = read_feature_flags({"KNOWLEDGE_CORE_ENABLED": "1"})
    assert core_only.core_enabled is True
    assert core_only.traffic_or_worker_enabled is False

    schema_only = read_feature_flags({"KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1"})
    assert schema_only.multi_wiki_schema is True
    assert schema_only.traffic_or_worker_enabled is False


@pytest.mark.unit
@pytest.mark.parametrize("name", FEATURE_FLAG_NAMES)
def test_p1_1_feature_flags_accept_only_exact_binary_strings(name: str) -> None:
    for invalid in ("", "true", "True", " 1", "1 ", "2"):
        with pytest.raises(KnowledgeCoreConfigurationError) as caught:
            read_feature_flags({name: invalid})
        assert caught.value.code == "KNOWLEDGE_CORE_CONFIGURATION_INVALID"


@pytest.mark.unit
@pytest.mark.parametrize("name", _DOWNSTREAM_FLAGS)
def test_p1_1_downstream_flag_requires_core_enabled(name: str) -> None:
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags({name: "1"})


@pytest.mark.unit
@pytest.mark.parametrize("name", _UNREADY_FLAGS)
def test_p1_1_traffic_worker_and_future_flags_fail_closed(name: str) -> None:
    with pytest.raises(KnowledgeCoreNotReadyError) as caught:
        read_feature_flags({"KNOWLEDGE_CORE_ENABLED": "1", name: "1"})
    assert caught.value.code == "KNOWLEDGE_CORE_PHASE_NOT_READY"


@pytest.mark.unit
def test_sbe_2_1_multi_wiki_flag_prerequisites_are_exact_and_default_off() -> None:
    base = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1",
        "KNOWLEDGE_CORE_READS": "1",
    }
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags({**base, "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": "1"})
    reads = read_feature_flags(
        {**base, "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1"}
    )
    assert reads.multi_wiki_reads is True
    assert reads.multi_wiki_writes is False
    writes = read_feature_flags(
        {
            **base,
            "KNOWLEDGE_CORE_PERSONAL_WRITES": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": "1",
        }
    )
    assert writes.multi_wiki_reads is True
    assert writes.multi_wiki_writes is True


@pytest.mark.unit
@pytest.mark.scenario("WIKI-013", "WIKI-016")
def test_sbe_2_2_conversation_binding_requires_multi_wiki_reads() -> None:
    base = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1",
        "KNOWLEDGE_CORE_READS": "1",
    }
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {**base, "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": "1"}
        )
    flags = read_feature_flags(
        {
            **base,
            "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1",
            "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": "1",
        }
    )
    assert flags.conversation_wiki_binding is True


@pytest.mark.unit
@pytest.mark.scenario("AUTOEDGE-004")
def test_sbe_4_2_automatic_edge_flags_have_exact_prerequisites() -> None:
    local = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": "1",
        }
    )
    assert local.auto_semantic_edges_local is True
    assert local.auto_semantic_edges_aggregate is False
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": "1",
            }
        )
    aggregate = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
            "KNOWLEDGE_CORE_HIGHER_SPACES": "1",
            "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "1",
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": "1",
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": "1",
        }
    )
    assert aggregate.auto_semantic_edges_aggregate is True


@pytest.mark.unit
@pytest.mark.scenario("RANK-001", "RANK-002")
def test_sbe_5_1_hybrid_retrieval_flag_requires_canonical_reads() -> None:
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_HYBRID_RERANK": "1",
            }
        )
    flags = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_HYBRID_RERANK": "1",
        }
    )
    assert flags.hybrid_rerank is True


@pytest.mark.unit
@pytest.mark.scenario("RANK-015", "RANK-018")
def test_sbe_5_2_reranker_and_temporal_flags_require_hybrid_parent() -> None:
    base = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
    }
    for name in (
        "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
        "KNOWLEDGE_CORE_TEMPORAL_RANKING",
    ):
        with pytest.raises(KnowledgeCoreConfigurationError):
            read_feature_flags({**base, name: "1"})
    flags = read_feature_flags(
        {
            **base,
            "KNOWLEDGE_CORE_HYBRID_RERANK": "1",
            "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER": "1",
            "KNOWLEDGE_CORE_TEMPORAL_RANKING": "1",
        }
    )
    assert flags.local_cross_encoder is True
    assert flags.temporal_ranking is True


@pytest.mark.unit
@pytest.mark.scenario("TIME-007", "TIME-009")
def test_sbe_3_2_projection_footer_v2_requires_projection_parent() -> None:
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": "1",
            }
        )
    flags = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_PROJECTIONS": "1",
            "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": "1",
        }
    )
    assert flags.projections is True
    assert flags.projection_footer_v2 is True
    container = compose_knowledge_core(
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_PROJECTIONS": "1",
            "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": "1",
        },
    )
    assert container.flags.projection_footer_v2 is True


@pytest.mark.unit
@pytest.mark.scenario("HSPACE-006")
@pytest.mark.scenario("HSPACE-010")
def test_sbe_2_3_higher_read_and_native_write_flag_prerequisites_are_exact() -> None:
    base = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PART_READS": "1",
    }
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {"KNOWLEDGE_CORE_ENABLED": "1", "KNOWLEDGE_CORE_HIGHER_SPACES": "1"}
        )
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags({**base, "KNOWLEDGE_CORE_TEAM_WRITES": "1"})
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags({**base, "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES": "1"})

    reads = read_feature_flags({**base, "KNOWLEDGE_CORE_HIGHER_SPACES": "1"})
    assert reads.higher_spaces is True
    assert reads.team_writes is False
    assert reads.responsibility_writes is False

    writes = read_feature_flags(
        {
            **base,
            "KNOWLEDGE_CORE_HIGHER_SPACES": "1",
            "KNOWLEDGE_CORE_TEAM_WRITES": "1",
            "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES": "1",
        }
    )
    assert writes.team_writes is True
    assert writes.responsibility_writes is True


@pytest.mark.unit
@pytest.mark.scenario("AGRAPH-009")
def test_sbe_4_1_aggregate_graph_reads_require_higher_space_reads() -> None:
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_READS": "1",
                "KNOWLEDGE_CORE_PART_READS": "1",
                "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "1",
            }
        )
    flags = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
            "KNOWLEDGE_CORE_HIGHER_SPACES": "1",
            "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "1",
        }
    )
    assert flags.aggregate_graph_reads is True


@pytest.mark.unit
def test_p37_read_flags_are_ordered_and_still_require_explicit_route_bindings() -> None:
    with pytest.raises(KnowledgeCoreConfigurationError):
        read_feature_flags(
            {
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_PART_READS": "1",
            }
        )
    flags = read_feature_flags(
        {
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
        }
    )
    assert flags.personal_reads is True
    assert flags.part_reads is True

    app = Flask("p37-missing-route-bindings", static_folder=None)
    with pytest.raises(KnowledgeCoreNotReadyError):
        register_knowledge_core(
            app,
            HostKind.CODE,
            environ={
                "KNOWLEDGE_CORE_ENABLED": "1",
                "KNOWLEDGE_CORE_READS": "1",
            },
        )
    assert not [
        rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/knowledge/v1")
    ]
    assert EXTENSION_KEY not in app.extensions


@pytest.mark.unit
def test_p37_core_read_composition_registers_only_canonical_routes(tmp_path: Path) -> None:
    script = r"""
import json
import sys
from flask import Flask
from code_assist_v1.knowledge_core import (
    CanonicalRouteBindings,
    HostKind,
    ServiceRegistry,
    register_knowledge_core,
)

class Services:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

class Executor:
    def complete(self, context):
        return None

class Summarizer:
    def summarize(self, **kwargs):
        return None

service = Services()
app = Flask('p37-core-read-composition', static_folder=None)
container = register_knowledge_core(
    app,
    HostKind.CODE,
    environ={
        'KNOWLEDGE_CORE_ENABLED': '1',
        'KNOWLEDGE_CORE_READS': '1',
        'KNOWLEDGE_CORE_PART_READS': '1',
    },
    services=ServiceRegistry(
        agent_context=service,
        conversations=service,
        management_reads=service,
        retrieval=service,
        scope_selection=service,
    ),
    canonical_route_bindings=CanonicalRouteBindings(
        turn_executor=Executor(),
        conversation_summarizer=Summarizer(),
        principal_resolver=lambda: {'id': 'admin', 'scope_epoch': 1},
    ),
)
canonical = sorted(
    rule.rule for rule in app.url_map.iter_rules()
    if rule.rule.startswith('/api/knowledge/v1')
)
blocked = sorted(name for name in (
    'code_assist_v1.routes_knowledge',
    'code_assist_v1.routes_second_brain',
    'code_assist_v1.routes_memory',
    'code_assist_v1.routes_project_context',
    'code_assist_v1.routes_chat_stream',
    'code_assist_v1.routes_sessions',
    'code_assist_v1.knowledge_core.legacy.read_compatibility',
) if name in sys.modules)
print(json.dumps({
    'blocked': blocked,
    'canonical_count': len(canonical),
    'groups': list(container.registered_route_groups),
    'legacy_paths': sorted(
        rule.rule for rule in app.url_map.iter_rules()
        if not rule.rule.startswith('/api/knowledge/v1')
    ),
}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "blocked": [],
        "canonical_count": 21,
        "groups": ["canonical_knowledge_v1"],
        "legacy_paths": [],
    }


@pytest.mark.unit
def test_p37_code_host_core_mode_does_not_import_or_register_legacy_retrieval(
    tmp_path: Path,
) -> None:
    script = r"""
import json
import sys
import types

calls = []
def module(name, **attributes):
    value = types.ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    sys.modules[name] = value

def registrar(label):
    return lambda target: calls.append(label)

module(
    'code_assist_v1.auth',
    ensure_secret_key=lambda: 'test-secret',
    register_auth_routes=registrar('auth'),
)
module('code_assist_v1.seed', seed_skills_if_empty=lambda: calls.append('seed'))
for short_name, registrar_name in {
    'routes_models': 'register_model_routes',
    'routes_skills': 'register_skill_routes',
    'routes_workspace': 'register_workspace_routes',
    'routes_jobs': 'register_job_routes',
    'routes_notion_bridge': 'register_notion_bridge_routes',
}.items():
    module('code_assist_v1.' + short_name, **{registrar_name: registrar(short_name)})

from code_assist_v1.knowledge_core import CanonicalRouteBindings, ServiceRegistry
class Services:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None
class Executor:
    def complete(self, context):
        return None
class Summarizer:
    def summarize(self, **kwargs):
        return None

from code_assist_v1 import app_code
app_code._configure_windows_console = lambda: None
app_code._register_gguf_models = lambda: []
service = Services()
app = app_code.create_app(
    knowledge_core_services=ServiceRegistry(
        agent_context=service,
        conversations=service,
        management_reads=service,
        retrieval=service,
        scope_selection=service,
    ),
    knowledge_core_route_bindings=CanonicalRouteBindings(
        turn_executor=Executor(),
        conversation_summarizer=Summarizer(),
        principal_resolver=lambda: {'id': 'admin', 'scope_epoch': 1},
    ),
    knowledge_core_environ={
        'KNOWLEDGE_CORE_ENABLED': '1',
        'KNOWLEDGE_CORE_READS': '1',
        'KNOWLEDGE_CORE_PART_READS': '1',
    },
)
legacy_modules = sorted(name for name in (
    'code_assist_v1.routes_knowledge',
    'code_assist_v1.routes_chat_stream',
    'code_assist_v1.routes_sessions',
    'code_assist_v1.routes_second_brain',
    'code_assist_v1.routes_memory',
    'code_assist_v1.routes_agent_harness',
    'code_assist_v1.routes_project_context',
    'code_assist_v1.harness_setup',
) if name in sys.modules)
print(json.dumps({
    'calls': calls,
    'canonical_count': len([
        rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith('/api/knowledge/v1')
    ]),
    'legacy_modules': legacy_modules,
}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in FEATURE_FLAG_NAMES:
        environment[name] = "0"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "calls": [
            "auth",
            "seed",
            "routes_models",
            "routes_skills",
            "routes_jobs",
            "routes_notion_bridge",
            "routes_workspace",
        ],
        "canonical_count": 21,
        "legacy_modules": [],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("host_kind", "surfaces"),
    (
        (HostKind.CODE, {"admin_api", "knowledge_api", "publication_adapter"}),
        (HostKind.NOTION, {"publication_adapter"}),
        (HostKind.BLUEPRINT, {"knowledge_api", "publication_adapter"}),
    ),
)
def test_p1_1_all_hosts_use_one_disabled_composition_contract(
    host_kind: HostKind, surfaces: set[str]
) -> None:
    container = compose_knowledge_core(host_kind, environ={})
    assert container.contract_version == 1
    assert container.host.kind is host_kind
    assert container.host.intended_surfaces == frozenset(surfaces)
    assert container.registered_route_groups == ()
    assert container.serves_traffic is False


@pytest.mark.unit
def test_p1_1_registration_adds_no_route_and_is_idempotent() -> None:
    app = Flask("p1_1_test", static_folder=None)
    before = tuple(sorted(rule.rule for rule in app.url_map.iter_rules()))

    first = register_knowledge_core(app, HostKind.CODE, environ={})
    second = register_knowledge_core(app, HostKind.CODE, environ={})

    assert first is second
    assert get_knowledge_core(app) is first
    assert app.extensions[EXTENSION_KEY] is first
    assert tuple(sorted(rule.rule for rule in app.url_map.iter_rules())) == before
    assert first.serves_traffic is False


@pytest.mark.unit
def test_p1_1_route_adapter_and_host_expose_the_same_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.knowledge_core import routes

    captured: list[object] = []

    def register_stub(target: object, container: object) -> tuple[str, ...]:
        captured.append(container)
        return ("test_only",)

    monkeypatch.setattr(routes, "register_routes", register_stub)
    app = Flask("p1_1_identity", static_folder=None)
    registered = register_knowledge_core(app, HostKind.CODE, environ={})
    assert captured == [registered]
    assert get_knowledge_core(app) is registered
    assert registered.registered_route_groups == ("test_only",)


@pytest.mark.unit
def test_p1_1_blueprint_adapter_uses_same_registration_contract() -> None:
    blueprint = Blueprint("p1_1_blueprint", __name__)
    container = register_knowledge_core(blueprint, HostKind.BLUEPRINT, environ={})
    assert get_knowledge_core(blueprint) is container
    assert container.host.kind is HostKind.BLUEPRINT
    assert tuple(blueprint.deferred_functions) == ()


@pytest.mark.unit
def test_p1_1_registration_conflicts_and_unbound_services_fail_closed() -> None:
    app = Flask("p1_1_conflict", static_folder=None)
    register_knowledge_core(app, HostKind.CODE, environ={})
    with pytest.raises(KnowledgeCoreHostConflictError):
        register_knowledge_core(app, HostKind.NOTION, environ={})

    invalid = Flask("p1_1_invalid_extension", static_folder=None)
    invalid.extensions[EXTENSION_KEY] = object()
    with pytest.raises(KnowledgeCoreHostConflictError):
        register_knowledge_core(invalid, HostKind.CODE, environ={})

    with pytest.raises(KnowledgeCoreHostConflictError):
        register_knowledge_core(object(), HostKind.CODE, environ={})
    with pytest.raises(KnowledgeCoreHostConflictError):
        compose_knowledge_core("not-a-host", environ={})
    with pytest.raises(KnowledgeCoreServiceUnboundError):
        ServiceRegistry().require("publication")
    with pytest.raises(KnowledgeCoreServiceUnboundError):
        ServiceRegistry().require("scope_selection")
    with pytest.raises(KnowledgeCoreServiceUnboundError):
        ServiceRegistry().require("management_reads")
    with pytest.raises(KnowledgeCoreServiceUnboundError):
        ServiceRegistry().require("unknown")


def _registration_call(path: Path, function_name: str) -> ast.Call:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_knowledge_core"
    ]
    assert len(calls) == 1
    return calls[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "function_name", "member", "first_legacy_call"),
    (
        ("app_code.py", "create_app", "CODE", "register_auth_routes"),
        ("app_notion.py", "create_app", "NOTION", "register_auth_routes"),
        ("blueprint.py", "register_code_blueprint", "BLUEPRINT", "register_model_routes"),
    ),
)
def test_p1_1_every_host_calls_shared_composition_before_legacy_registration(
    filename: str,
    function_name: str,
    member: str,
    first_legacy_call: str,
) -> None:
    path = _PACKAGE_ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    registration = _registration_call(path, function_name)
    assert len(registration.args) == 2
    host_argument = registration.args[1]
    assert isinstance(host_argument, ast.Attribute)
    assert isinstance(host_argument.value, ast.Name)
    assert (host_argument.value.id, host_argument.attr) == ("HostKind", member)

    legacy_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == first_legacy_call
    ]
    assert legacy_lines
    assert registration.lineno < min(legacy_lines)


@pytest.mark.unit
def test_p1_1_registration_subprocess_is_inert_and_registers_no_route(
    tmp_path: Path,
) -> None:
    script = """
import json
from pathlib import Path
import sys
import threading

before_files = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*'))
before_threads = sorted(thread.name for thread in threading.enumerate())
from flask import Flask
from code_assist_v1.knowledge_core import HostKind, get_knowledge_core, register_knowledge_core
app = Flask('p1_1_subprocess', static_folder=None)
before_rules = sorted(rule.rule for rule in app.url_map.iter_rules())
container = register_knowledge_core(app, HostKind.CODE, environ={})
after_rules = sorted(rule.rule for rule in app.url_map.iter_rules())
after_files = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*'))
after_threads = sorted(thread.name for thread in threading.enumerate())
blocked = [name for name in (
    'sqlite3',
    'code_assist_v1.app_code',
    'code_assist_v1.app_notion',
    'code_assist_v1.auth',
    'code_assist_v1.config',
    'code_assist_v1.knowledge_store',
    'code_assist_v1.notion_store',
    'code_assist_v1.second_brain_store',
) if name in sys.modules]
print(json.dumps({
    'blocked': blocked,
    'files_unchanged': before_files == after_files,
    'host': container.host.kind.value,
    'identity': get_knowledge_core(app) is container,
    'routes_unchanged': before_rules == after_rules,
    'threads_unchanged': before_threads == after_threads,
}, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "blocked": [],
        "files_unchanged": True,
        "host": "app_code",
        "identity": True,
        "routes_unchanged": True,
        "threads_unchanged": True,
    }


@pytest.mark.unit
def test_p1_1_real_host_factories_execute_shared_registration_with_stubbed_legacy(
    tmp_path: Path,
) -> None:
    script = r"""
import json
import sys
import types

def module(name, **attributes):
    value = types.ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    sys.modules[name] = value

def noop(*args, **kwargs):
    return None

module(
    'code_assist_v1.auth',
    ensure_secret_key=lambda: 'test-secret',
    list_public_users=lambda include_data=False: [],
    register_auth_routes=noop,
    safe_user_id=lambda value: value,
)
module('code_assist_v1.seed', seed_skills_if_empty=noop)
module('code_assist_v1.harness_setup', setup_harness=noop)
module('code_assist_v1.notion_store', repair_workspace_tree=noop)
module('code_assist_v1.internal_llm', codex_internal_enabled=lambda: False)

registrars = {
    'routes_models': 'register_model_routes',
    'routes_skills': 'register_skill_routes',
    'routes_knowledge': 'register_knowledge_routes',
    'routes_workspace': 'register_workspace_routes',
    'routes_chat_stream': 'register_chat_stream_routes',
    'routes_sessions': 'register_session_routes',
    'routes_second_brain': 'register_second_brain_routes',
    'routes_memory': 'register_memory_routes',
    'routes_agent_harness': 'register_agent_harness_routes',
    'routes_project_context': 'register_project_context_routes',
    'routes_jobs': 'register_job_routes',
    'routes_notion_bridge': 'register_notion_bridge_routes',
    'routes_notion': 'register_notion_routes',
    'routes_notion_workspace': 'register_notion_workspace_routes',
    'routes_outlook': 'register_outlook_routes',
}
for short_name, registrar in registrars.items():
    module('code_assist_v1.' + short_name, **{registrar: noop})

from flask import Flask
from code_assist_v1.knowledge_core import get_knowledge_core
from code_assist_v1 import app_code, app_notion

app_code._configure_windows_console = noop
app_code._register_gguf_models = lambda: []
code_app = app_code.create_app()
notion_app = app_notion.create_app()

from code_assist_v1 import blueprint
mount_app = Flask('p1_1_mount', static_folder=None)
assert blueprint.register_code_blueprint(mount_app) is True

results = {}
for name, target in (
    ('app_code', code_app),
    ('app_notion', notion_app),
    ('blueprint', blueprint.code_bp),
):
    container = get_knowledge_core(target)
    rules = [rule.rule for rule in (target.url_map.iter_rules() if hasattr(target, 'url_map') else ())]
    results[name] = {
        'host': container.host.kind.value,
        'knowledge_v1_routes': sorted(
            rule for rule in rules
            if rule.startswith('/api/knowledge/v1') or rule.startswith('/api/admin/v1')
        ),
        'route_groups': list(container.registered_route_groups),
    }
print(json.dumps(results, sort_keys=True))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in FEATURE_FLAG_NAMES:
        environment[name] = "0"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "app_code": {
            "host": "app_code",
            "knowledge_v1_routes": [],
            "route_groups": [],
        },
        "app_notion": {
            "host": "app_notion",
            "knowledge_v1_routes": [],
            "route_groups": [],
        },
        "blueprint": {
            "host": "blueprint",
            "knowledge_v1_routes": [],
            "route_groups": [],
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("module_name", "factory_name"),
    (
        ("code_assist_v1.app_code", "create_app"),
        ("code_assist_v1.app_notion", "create_app"),
    ),
)
def test_p1_1_real_hosts_reject_traffic_flag_before_legacy_registration(
    tmp_path: Path,
    module_name: str,
    factory_name: str,
) -> None:
    script = r"""
import importlib
import json
import sys
import types

calls = []
def touched(*args, **kwargs):
    calls.append('legacy')
def module(name, **attributes):
    value = types.ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    sys.modules[name] = value

module(
    'code_assist_v1.auth',
    ensure_secret_key=touched,
    list_public_users=lambda include_data=False: calls.append('legacy') or [],
    register_auth_routes=touched,
)
target = importlib.import_module(sys.argv[1])
if hasattr(target, '_configure_windows_console'):
    target._configure_windows_console = lambda: None
try:
    getattr(target, sys.argv[2])()
except Exception as exc:
    print(json.dumps({
        'calls': calls,
        'code': getattr(exc, 'code', None),
        'type': type(exc).__name__,
    }, sort_keys=True))
else:
    raise SystemExit('unsafe feature flags unexpectedly started the host')
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in FEATURE_FLAG_NAMES:
        environment[name] = "0"
    environment["KNOWLEDGE_CORE_ENABLED"] = "1"
    environment["KNOWLEDGE_CORE_READS"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script, module_name, factory_name],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "calls": [],
        "code": "KNOWLEDGE_CORE_PHASE_NOT_READY",
        "type": "KnowledgeCoreNotReadyError",
    }
