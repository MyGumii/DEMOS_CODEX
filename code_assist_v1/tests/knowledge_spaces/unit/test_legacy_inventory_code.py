from __future__ import annotations

import builtins
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat as stat_module
import sys
from typing import Any

import pytest

from code_assist_v1.tools import legacy_inventory_code
from code_assist_v1.tools.legacy_inventory_code import scan_code_coverage


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MAPPING_PATH = (
    PACKAGE_ROOT / "docs" / "knowledge_spaces" / "CURRENT_SYSTEM_INVENTORY_MAP.json"
)
FORBIDDEN_APPLICATION_MODULES = {
    "code_assist_v1.config",
    "code_assist_v1.job_manager",
    "code_assist_v1.knowledge_store",
    "code_assist_v1.memory_store",
    "code_assist_v1.notion_store",
    "code_assist_v1.project_context_store",
    "code_assist_v1.second_brain_store",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _file_fact(path: Path) -> tuple[int, int, str]:
    raw = path.read_bytes()
    return (
        len(raw),
        path.stat().st_mtime_ns,
        hashlib.sha256(raw).hexdigest(),
    )


def _write_mapping(path: Path) -> None:
    mapping = {
        "schema_version": 1,
        "expected_submitter_count": 8,
        "expected_callsite_ids_sha256": "0" * 64,
        "mapping_rules": [
            {
                "mapping_id": "SYNTHETIC-COMPOSITION",
                "priority": 0,
                "selector": {
                    "files": ["app_code.py"],
                    "kinds": ["route_registration"],
                },
                "scope_owner": "composition",
                "replacement_phase": "P1",
                "disposition": "replace",
                "reason": "Synthetic composition registration fixture.",
            },
            {
                "mapping_id": "SYNTHETIC-PYTHON",
                "priority": 0,
                "selector": {
                    "files": ["routes_sample.py"],
                    "kinds": [
                        "context_reference",
                        "job_submit",
                        "legacy_store_call",
                        "legacy_store_import",
                        "route_decorator",
                    ],
                },
                "scope_owner": "canonical_service",
                "replacement_phase": "P3/P7",
                "disposition": "replace",
                "reason": "Synthetic Python discovery fixture.",
            },
            {
                "mapping_id": "SYNTHETIC-JAVASCRIPT",
                "priority": 0,
                "selector": {
                    "files": ["static/sample.js"],
                    "kinds": ["ui_api_call", "ui_context_reference"],
                },
                "scope_owner": "scoped_ui",
                "replacement_phase": "P3",
                "disposition": "replace",
                "reason": "Synthetic JavaScript discovery fixture.",
            },
        ],
    }
    path.write_text(_canonical(mapping), encoding="utf-8")


def _synthetic_root(tmp_path: Path) -> Path:
    root = tmp_path / "legacy source"
    (root / "static").mkdir(parents=True)
    (root / "app_code.py").write_text(
        """from routes_sample import register_sample_routes as install_routes

def create_app(app):
    install_routes(app)
    return app
""",
        encoding="utf-8",
    )
    (root / "routes_sample.py").write_text(
        """from code_assist_v1 import knowledge_store as ks
from code_assist_v1.job_manager import manager as jobs

def register_sample_routes(app):
    @app.route('/api/sample', methods=['POST'])
    def create_sample():
        compact_summary = project_context = pinned_thoughts = session_pages = None
        ks.list_docs()
        jobs.submit('sample-1', lambda: None)
        jobs.submit('sample-2', lambda: None)
        jobs.submit('sample-3', lambda: None)
        jobs.submit('sample-4', lambda: None)
        jobs.submit('sample-5', lambda: None)
        jobs.submit('sample-6', lambda: None)
        jobs.submit('sample-7', lambda: None)
        return jobs.submit('sample-8', lambda: None)
""",
        encoding="utf-8",
    )
    (root / "static" / "sample.js").write_text(
        """// api("api/code/ignored")
/* State.pinnedThoughts */
api("api/code/knowledge/list");
fetch(`/api/notion/pages/${pageId}?access_token=synthetic-secret#details`);
api(flag ? "api/code/knowledge/a" : "api/code/knowledge/b");
State.pinnedThoughts.push(item);
const context = project_context || compact_summary || session_pages;
""",
        encoding="utf-8",
    )
    _write_mapping(root / "mapping.json")
    initial = scan_code_coverage(root, root / "mapping.json")
    mapping = json.loads((root / "mapping.json").read_text(encoding="utf-8"))
    mapping["expected_callsite_ids_sha256"] = initial["mapping"][
        "callsite_ids_sha256"
    ]
    (root / "mapping.json").write_text(_canonical(mapping), encoding="utf-8")
    return root


def _create_reparse_link(link: Path, target: Path, *, is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
        return
    except OSError as first_error:
        if os.name != "nt":
            pytest.skip(f"symlink creation unavailable: {first_error.__class__.__name__}")
    command = ["cmd.exe", "/d", "/c", "mklink"]
    if is_directory:
        command.append("/J")
    command.extend((os.fspath(link), os.fspath(target)))
    created = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if created.returncode != 0:
        pytest.skip("Windows reparse-link creation is unavailable")


@pytest.mark.unit
@pytest.mark.parametrize("selected_source", ("python", "static", "mapping"))
def test_selected_code_reparse_fails_closed_without_reading_outside_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected_source: str,
) -> None:
    root = _synthetic_root(tmp_path)
    outside = tmp_path / "outside-code-root"
    outside.mkdir()
    marker = f"outside-{selected_source}-canary-never-read"
    synthetic_reparse_paths: set[Path] = set()

    if selected_source == "python":
        selected = root / "routes_sample.py"
        selected.rename(root / "routes_sample.py.approved")
        canary = outside / "routes_sample.py"
        canary.write_text(
            f"import code_assist_v1.{marker.replace('-', '_')}\n",
            encoding="utf-8",
        )
        selected.write_text("synthetic reparse placeholder\n", encoding="utf-8")
        synthetic_reparse_paths.add(selected)
        mapping = root / "mapping.json"
    elif selected_source == "static":
        selected = root / "static"
        selected.rename(root / "static.approved")
        outside_static = outside / "static"
        outside_static.mkdir()
        canary = outside_static / "sample.js"
        canary.write_text(f'api("/api/{marker}");\n', encoding="utf-8")
        _create_reparse_link(selected, outside_static, is_directory=True)
        mapping = root / "mapping.json"
    else:
        selected = root / "mapping.json"
        selected.rename(root / "mapping.json.approved")
        canary = outside / "mapping.json"
        canary.write_text(
            json.dumps({"private_marker": marker}),
            encoding="utf-8",
        )
        selected.write_text("synthetic reparse placeholder\n", encoding="utf-8")
        synthetic_reparse_paths.add(selected)
        mapping = selected

    canary_identity = canary.resolve(strict=True)
    canary_reads: list[str] = []
    original_read_bytes = Path.read_bytes
    original_open = Path.open
    original_os_open = os.open
    original_stat = Path.stat

    class _StatProxy:
        def __init__(self, value: os.stat_result) -> None:
            self._value = value
            self.st_file_attributes = int(
                getattr(value, "st_file_attributes", 0)
            ) | int(
                getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )

        def __getattr__(self, name: str) -> Any:
            return getattr(self._value, name)

    def guarded_stat(
        path: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result | _StatProxy:
        value = original_stat(path, follow_symlinks=follow_symlinks)
        return _StatProxy(value) if path in synthetic_reparse_paths else value

    def is_canary(path: Path) -> bool:
        if path in synthetic_reparse_paths:
            return True
        try:
            return path.resolve(strict=True) == canary_identity
        except OSError:
            return False

    def guarded_read_bytes(path: Path) -> bytes:
        if is_canary(path):
            canary_reads.append(os.fspath(path))
            raise AssertionError("outside canary content was read")
        return original_read_bytes(path)

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if is_canary(path):
            canary_reads.append(os.fspath(path))
            raise AssertionError("outside canary content was opened")
        return original_open(path, *args, **kwargs)

    def guarded_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        candidate = Path(path)
        if is_canary(candidate):
            canary_reads.append(os.fspath(candidate))
            raise AssertionError("outside canary content was opened")
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(legacy_inventory_code.os, "open", guarded_os_open)

    report = scan_code_coverage(root, mapping)

    assert report["summary"]["pass"] is False
    assert "unsafe_reparse_source" in {row["code"] for row in report["errors"]}
    assert canary_reads == []
    assert marker not in _canonical(report)


@pytest.mark.unit
def test_real_source_coverage_gate_is_closed_and_deterministic() -> None:
    first = scan_code_coverage(PACKAGE_ROOT)
    second = scan_code_coverage(PACKAGE_ROOT)

    assert first == second
    assert first["summary"] == {
        "discovered": 749,
        "mapped": 393,
        "pass": True,
        "retained_excluded": 356,
        "submitter_count": 8,
        "unclassified": 0,
        "unmapped_submitters": 0,
    }
    assert first["errors"] == []
    assert first["mapping"]["unused_mapping_ids"] == []
    assert first["unclassified_callsite_ids"] == []
    assert first["unmapped_submitter_ids"] == []
    assert first["summary"]["discovered"] == (
        first["summary"]["mapped"] + first["summary"]["retained_excluded"]
    )
    assert _canonical(json.loads(_canonical(first))) == _canonical(first)
    assert _canonical(first).isascii()


@pytest.mark.unit
def test_real_source_has_exact_submitter_producers_and_required_evidence() -> None:
    report = scan_code_coverage(PACKAGE_ROOT)
    submitters = [item for item in report["callsites"] if item["kind"] == "job_submit"]

    assert Counter(item["file"] for item in submitters) == {
        "routes_outlook.py": 4,
        "routes_project_context.py": 2,
        "routes_second_brain.py": 2,
    }
    assert all(item["access"] == "submit" for item in submitters)
    assert all(item["mapping_id"] and item["reason"] for item in report["callsites"])
    assert all(
        item["scope_owner"] and item["replacement_phase"]
        for item in report["callsites"]
    )
    assert all(len(item["file_sha256"]) == 64 for item in report["callsites"])
    assert all(item["line"] > 0 for item in report["callsites"])
    assert not any(item["access"] == "unknown" for item in report["callsites"])
    access_by_callee = {
        item["callee"]: item["access"]
        for item in report["callsites"]
        if item["file"] == "notion_workspace_pages.py"
    }
    assert access_by_callee[
        "code_assist_v1.notion_store.capture_legacy_page_source_proof"
    ] == "read"
    assert access_by_callee[
        "code_assist_v1.notion_store.duplicate_page_idempotent"
    ] == "write"
    assert access_by_callee[
        "code_assist_v1.notion_store.page_id_for_operation"
    ] == "reference"
    assert access_by_callee[
        "code_assist_v1.notion_store.self_owned_page_lifecycle_scope"
    ] == "read"
    assert len(report["source_fingerprint_sha256"]) == 64


@pytest.mark.unit
def test_mapping_ledger_uses_only_explicit_file_selectors_and_reasons() -> None:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    mapping_ids: set[str] = set()

    assert mapping["expected_submitter_count"] == 8
    for rule in mapping["mapping_rules"]:
        assert rule["mapping_id"] not in mapping_ids
        mapping_ids.add(rule["mapping_id"])
        assert rule["reason"].strip()
        assert rule["selector"]["files"]
        assert not any(
            any(token in file_name for token in "*?[]")
            for file_name in rule["selector"]["files"]
        )
        assert set(rule["selector"]) != {"files"}


@pytest.mark.unit
def test_agent_integration_ui_calls_are_mapped_to_canonical_bridge_not_excluded() -> None:
    report = scan_code_coverage(PACKAGE_ROOT)
    calls = [row for row in report["callsites"] if row["file"] == "static/agent_integrations.js"]
    assert {row["callsite_id"] for row in calls} == {
        "callsite-5d6de27afa9abd1905319bcaecfab4c5419224ab5f502b5f4566501168731483",
        "callsite-a15f3348c828fc18d35bdfc504e84e7a0b0828f8ed7f443c985ab1b7db3b964b",
    }
    assert all(row["mapping_id"] == "G02-UI-AGENT-KNOWLEDGE-LOOP" for row in calls)
    assert all(row["scope_owner"] == "canonical_agent_integration_bridge" for row in calls)
    assert all(row["disposition"] == "replace" for row in calls)
    assert all(row["kind"] == "ui_api_call" for row in calls)
    assert report["summary"]["unclassified"] == 0


@pytest.mark.unit
def test_agent_integration_rule_keeps_new_calls_behind_the_callsite_seal(tmp_path: Path) -> None:
    root = _synthetic_root(tmp_path)
    relative = Path("static/agent_integrations.js")
    source = root / relative
    source.write_text((PACKAGE_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    mapping_path = root / "mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    actual_mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    mapping["mapping_rules"].append(next(
        rule for rule in actual_mapping["mapping_rules"]
        if rule["mapping_id"] == "G02-UI-AGENT-KNOWLEDGE-LOOP"
    ))
    mapping_path.write_text(_canonical(mapping), encoding="utf-8")
    baseline = scan_code_coverage(root, mapping_path)
    mapping["expected_callsite_ids_sha256"] = baseline["mapping"]["callsite_ids_sha256"]
    mapping_path.write_text(_canonical(mapping), encoding="utf-8")
    assert scan_code_coverage(root, mapping_path)["summary"]["pass"] is True

    source.write_text(source.read_text(encoding="utf-8") + '\nfetch(base + "/status");\n', encoding="utf-8")
    changed = scan_code_coverage(root, mapping_path)
    assert changed["summary"]["unclassified"] == 0
    assert changed["summary"]["pass"] is False
    assert {row["code"] for row in changed["errors"]} == {"mapping_callsite_set_mismatch"}


@pytest.mark.unit
def test_synthetic_ast_and_js_discovery_is_conservative_and_alias_aware(
    tmp_path: Path,
) -> None:
    root = _synthetic_root(tmp_path)
    report = scan_code_coverage(root, root / "mapping.json")

    assert report["summary"]["pass"] is True
    assert report["summary"]["submitter_count"] == 8
    assert any(
        item["callee"] == "register:register_sample_routes"
        and item["file"] == "app_code.py"
        for item in report["callsites"]
    )
    assert any(
        item["callee"] == "code_assist_v1.knowledge_store.list_docs"
        and item["access"] == "read"
        for item in report["callsites"]
    )
    assert any(
        item["callee"] == "code_assist_v1.job_manager.manager.submit"
        and item["kind"] == "job_submit"
        for item in report["callsites"]
    )
    assert any(item["callee"] == "ui_api:<dynamic>" for item in report["callsites"])
    assert any(
        item["callee"] == "ui_api:api/code/knowledge/list"
        for item in report["callsites"]
    )
    sanitized_url = next(
        item
        for item in report["callsites"]
        if item["callee"] == "ui_api:/api/notion/pages/${}"
    )
    assert sanitized_url["query_present"] is True
    assert sanitized_url["fragment_present"] is True
    assert "synthetic-secret" not in _canonical(report)
    assert not any("ignored" in item["callee"] for item in report["callsites"])
    js_contexts = [
        item for item in report["callsites"] if item["kind"] == "ui_context_reference"
    ]
    assert {item["callee"] for item in js_contexts} == {
        "ui_context:compact_summary",
        "ui_context:pinned_thoughts",
        "ui_context:project_context",
        "ui_context:session_pages",
    }


@pytest.mark.unit
def test_callsite_identity_ignores_lines_and_file_hash_but_reports_both(
    tmp_path: Path,
) -> None:
    root = _synthetic_root(tmp_path)
    first = scan_code_coverage(root, root / "mapping.json")
    source = root / "routes_sample.py"
    source.write_text("\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    second = scan_code_coverage(root, root / "mapping.json")

    identity = lambda item: (
        item["file"],
        item["symbol"],
        item["callee"],
        item["ordinal"],
    )
    first_by_identity = {identity(item): item for item in first["callsites"]}
    second_by_identity = {identity(item): item for item in second["callsites"]}
    assert first_by_identity.keys() == second_by_identity.keys()
    assert {
        key: item["callsite_id"] for key, item in first_by_identity.items()
    } == {
        key: item["callsite_id"] for key, item in second_by_identity.items()
    }
    shifted = [
        key for key in first_by_identity if key[0] == "routes_sample.py"
    ]
    assert shifted
    assert all(
        first_by_identity[key]["line"] + 1 == second_by_identity[key]["line"]
        for key in shifted
    )
    assert all(
        first_by_identity[key]["file_sha256"]
        != second_by_identity[key]["file_sha256"]
        for key in shifted
    )


@pytest.mark.unit
def test_scan_does_not_import_application_or_mutate_scanned_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_paths = sorted(PACKAGE_ROOT.glob("*.py"))
    source_paths.extend(sorted((PACKAGE_ROOT / "static").glob("*.js")))
    source_paths.extend(sorted((PACKAGE_ROOT / "notion_static").glob("*.js")))
    source_paths.append(MAPPING_PATH)
    before_files = {path: _file_fact(path) for path in source_paths}
    before_modules = {
        name for name in sys.modules if name in FORBIDDEN_APPLICATION_MODULES
    }
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name in FORBIDDEN_APPLICATION_MODULES:
            raise AssertionError(f"scanner imported forbidden application module: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    report = scan_code_coverage(PACKAGE_ROOT)

    assert report["summary"]["pass"] is True
    assert {path: _file_fact(path) for path in source_paths} == before_files
    assert {
        name for name in sys.modules if name in FORBIDDEN_APPLICATION_MODULES
    } == before_modules


@pytest.mark.unit
def test_new_unmapped_callsite_fails_closed(tmp_path: Path) -> None:
    root = _synthetic_root(tmp_path)
    mapping = json.loads((root / "mapping.json").read_text(encoding="utf-8"))
    mapping["mapping_rules"] = [
        rule
        for rule in mapping["mapping_rules"]
        if rule["mapping_id"] != "SYNTHETIC-JAVASCRIPT"
    ]
    (root / "mapping.json").write_text(_canonical(mapping), encoding="utf-8")

    report = scan_code_coverage(root, root / "mapping.json")

    assert report["summary"]["pass"] is False
    assert report["summary"]["unclassified"] > 0
    assert report["unclassified_callsite_ids"]


@pytest.mark.unit
def test_mapping_cannot_redefine_the_eight_submitter_contract(tmp_path: Path) -> None:
    root = _synthetic_root(tmp_path)
    mapping = json.loads((root / "mapping.json").read_text(encoding="utf-8"))
    mapping["expected_submitter_count"] = 7
    (root / "mapping.json").write_text(_canonical(mapping), encoding="utf-8")

    report = scan_code_coverage(root, root / "mapping.json")

    assert report["summary"]["submitter_count"] == 8
    assert report["summary"]["pass"] is False
    assert report["errors"] == [
        {"code": "mapping_expected_submitter_count_invalid", "file": "<mapping>"}
    ]


@pytest.mark.unit
def test_new_call_in_broadly_mapped_file_breaks_stable_callsite_seal(
    tmp_path: Path,
) -> None:
    root = _synthetic_root(tmp_path)
    source = root / "routes_sample.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\ndef newly_added():\n    return ks.list_docs()\n",
        encoding="utf-8",
    )

    report = scan_code_coverage(root, root / "mapping.json")

    assert report["summary"]["unclassified"] == 0
    assert report["summary"]["pass"] is False
    assert {item["code"] for item in report["errors"]} == {
        "mapping_callsite_set_mismatch"
    }
    assert (
        report["mapping"]["callsite_ids_sha256"]
        != report["mapping"]["expected_callsite_ids_sha256"]
    )


@pytest.mark.unit
def test_cli_malformed_and_internal_errors_are_one_sanitized_json_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert legacy_inventory_code.main([]) == 2
    malformed = capsys.readouterr()
    assert malformed.err == ""
    assert len(malformed.out.splitlines()) == 1
    assert json.loads(malformed.out)["errors"] == [
        {"code": "invalid_cli_arguments", "file": "<command>"}
    ]

    secret = "DO NOT LEAK THIS EXCEPTION"

    def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(secret)

    monkeypatch.setattr(legacy_inventory_code, "scan_code_coverage", explode)
    assert legacy_inventory_code.main(["--legacy-root", str(PACKAGE_ROOT)]) == 2
    failed = capsys.readouterr()
    assert failed.err == ""
    assert len(failed.out.splitlines()) == 1
    assert secret not in failed.out
    assert json.loads(failed.out)["errors"] == [
        {"code": "inventory_internal_error", "file": "<command>"}
    ]


@pytest.mark.unit
def test_cli_unicode_root_and_filename_emit_ascii_safe_canonical_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unicode_parent = tmp_path / "지식-루트"
    unicode_parent.mkdir()
    root = _synthetic_root(unicode_parent)
    (root / "static" / "핀-화면.js").write_text(
        "// intentionally no inventory callsite\n", encoding="utf-8"
    )

    exit_code = legacy_inventory_code.main(
        ["--legacy-root", str(root), "--mapping", str(root / "mapping.json")]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert captured.out.isascii()
    report = json.loads(captured.out)
    assert report["summary"]["pass"] is True
    assert "static/핀-화면.js" in {item["path"] for item in report["files"]}
