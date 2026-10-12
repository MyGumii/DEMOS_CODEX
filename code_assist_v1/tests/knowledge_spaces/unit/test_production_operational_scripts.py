from __future__ import annotations

from pathlib import Path

from code_assist_v1.knowledge_core.production_service import (
    parse_protected_environment,
)


_ROOT = Path(__file__).resolve().parents[3]
_SERVICE_IDS = (
    "CodeAssist-Migrator",
    "CodeAssist-Web",
    "CodeAssist-Notion",
    "CodeAssist-DurableWorker",
    "CodeAssist-Projector",
)


def _text(name: str) -> str:
    return (_ROOT / "ops" / name).read_text(encoding="utf-8")


def test_install_script_freezes_acl_account_secret_and_rollback_boundaries() -> None:
    script = _text("install-services.ps1")
    assert all(service_id in script for service_id in _SERVICE_IDS)
    assert "/inheritance:r" in script
    assert "(OI)(CI)RX" in script
    assert "(OI)(CI)M" in script
    assert "ZeroFreeBSTR" in script
    assert "StartPassword = $plainPassword" in script
    assert "Start-Service" not in script
    assert "uninstall" in script


def test_start_and_stop_scripts_freeze_migrate_health_and_reverse_drain_order() -> None:
    start = _text("start-services.ps1")
    positions = [start.index(f'Start-Service -Name "{name}"') for name in _SERVICE_IDS]
    assert positions == sorted(positions)
    assert start.index("PRODUCTION_MIGRATOR_FAILED") < start.index(
        'Start-Service -Name "CodeAssist-Web"'
    )
    assert start.count("Assert-ProductionHealth") == 3  # definition plus two calls
    assert '"code-assist.production-health"' in start
    assert "$response.gate.status" in start
    assert "$response.status" not in start

    stop = _text("stop-services.ps1")
    stop_order = tuple(reversed(_SERVICE_IDS[1:]))
    positions = [stop.index(f'"{name}"') for name in stop_order]
    assert positions == sorted(positions)
    assert "WaitForStatus" in stop
    assert "FromSeconds(50)" in stop


def test_protected_environment_example_supports_local_readiness_and_keeps_flags_off() -> None:
    raw = (_ROOT / "ops" / "production.env.example").read_bytes()
    values = parse_protected_environment(raw)
    origins = set(values["CODE_ASSIST_ALLOWED_ORIGINS"].split(","))
    assert {"http://127.0.0.1:10011", "http://127.0.0.1:10012"} <= origins
    assert values["CODE_ASSIST_API_CONFIG"] == (
        "C:\\CodeAssist\\config\\api_config.json"
    )
    assert "CODE_ASSIST_STANDALONE" not in values
    assert all(
        values[name] == "0"
        for name in (
            "KNOWLEDGE_CORE_ENABLED",
            "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA",
            "KNOWLEDGE_CORE_MULTI_WIKI_READS",
            "KNOWLEDGE_CORE_MULTI_WIKI_WRITES",
            "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING",
            "KNOWLEDGE_CORE_READS",
            "KNOWLEDGE_CORE_PERSONAL_WRITES",
            "KNOWLEDGE_CORE_PART_READS",
            "KNOWLEDGE_CORE_PART_WRITES",
            "KNOWLEDGE_CORE_PROJECTIONS",
            "KNOWLEDGE_CORE_HIGHER_SPACES",
            "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
        )
    )


def test_runtime_lock_pins_the_reviewed_wsgi_distribution_exactly_once() -> None:
    rows = (_ROOT / "requirements-runtime.lock").read_text(encoding="utf-8").splitlines()
    assert [row for row in rows if row.casefold().startswith("waitress==")] == [
        "waitress==3.0.2"
    ]
