"""Compile and verify a fresh-gate-bound immutable V0B-P1 receipt.

The browser capture runner intentionally emits a receipt with stale preflight
placeholders.  This standalone tool leaves that receipt untouched and derives a
new receipt only after independently validating a fresh Core-25 JUnit file and
two ordered quiet-host UI-PERF-001 evidence triples.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    CONTRACT as V0B_CONTRACT,
    SOURCE_REPO_PATHS,
    verify as verify_v0b_receipt,
)
from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget import (
    DEFAULT_BUDGET,
    load_active_performance_budget,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SCENARIOS_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/scenarios.v1.json"
)
UI_SOURCE_REPO_PATHS = (
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
)
VALIDATION_SOURCE_REPO_PATHS = (
    "code_assist_v1/tests/browser/test_notion_ui1_golden_journey.py",
    "code_assist_v1/tests/browser/test_notion_ui_performance.py",
    "code_assist_v1/tools/notion_ui_performance_report_plugin.py",
)
FINAL_EVIDENCE_CONTRACT = (
    "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_evidence.v1"
)
FINAL_AUDIT_CONTRACT = (
    "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_audit.v1"
)
QUIET_HOST_PREFLIGHT_CONTRACT = (
    "code_assist.notion.core_free_ui.quiet_host_preflight.v1"
)
PERFORMANCE_OBSERVATION_CONTRACT = (
    "code_assist.notion.core_free_ui.performance_observation.v1"
)
PERFORMANCE_MEASUREMENT_CONTRACT = (
    "code_assist.notion.core_free_ui.performance_measurement.v1"
)
PERFORMANCE_TEST_ID = (
    "tests.browser.test_notion_ui_performance::"
    "test_ui_perf_001_measurements_are_isolated_bounded_and_precalibration_only"
)
CORE_TEST_IDS = (
    "tests.browser.test_notion_ui1_golden_journey::test_ui1_local_chrome_shell_search_and_editor_are_repeatable_and_isolated",
    "tests.browser.test_notion_ui1_golden_journey::test_ui1_second_brain_cancel_approve_and_replay_contract",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_shell_001_workspace_switch_is_scoped_repeatable_and_focus_safe",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_shell_003_home_page_topbar_and_breadcrumb_are_coherent",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_tree_001_nested_page_expand_collapse_is_keyboard_focus_safe",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_tree_002_create_rename_nest_reorder_move_is_coherent",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_tree_003_duplicate_trash_restore_preserves_second_brain",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_editor_004_korean_ime_commits_once_after_composition",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_editor_002_slash_menu_transform_is_accessible_and_persistent",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_editor_003_selection_clipboard_and_history_are_deterministic",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_editor_005_main_full_and_side_peek_share_one_persistence_model",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_editor_006_network_and_conflict_drafts_recover_without_false_save",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_db_001_typed_row_properties_round_trip_through_a_page",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_db_002_filter_sort_and_group_state_survive_reload",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_db_003_database_views_preserve_canonical_rows_and_focus",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_search_002_in_page_find_link_and_backlink_stay_workspace_scoped",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_collab_001_comment_thread_create_resolve_reopen_round_trip",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_share_001_dialog_open_escape_and_focus_are_read_only",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_sb_003_session_attach_detach_revalidates_current_access",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_resp_001_shell_editor_and_overlays_fit_adopted_viewports",
    "tests.browser.test_notion_ui1_golden_journey::test_ui_a11y_001_full_surface_keyboard_focus_and_media_contract",
)
MEASUREMENT_METRICS = frozenset(
    {
        "create_page_ready_ms",
        "editor_input_ack_ms",
        "page_ready_ms",
        "reload_ready_ms",
        "save_ready_ms",
        "search_open_ms",
        "search_result_ms",
        "second_brain_preview_ready_ms",
        "shell_ready_ms",
    }
)
MEASUREMENT_ONLY_METRIC = "create_page_ready_ms"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
QUIET_THRESHOLD_PERCENT = 30.0
QUIET_SUFFIX_COUNT = 5
MAX_PREFLIGHT_TO_TEST_GAP = timedelta(minutes=5)


class FreshEvidenceError(ValueError):
    """A fail-closed fresh evidence validation error."""

    def __init__(self, code: str, subject: str, message: str) -> None:
        super().__init__(f"{code}:{subject}:{message}")
        self.code = code
        self.subject = subject
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise FreshEvidenceError("DUPLICATE_JSON_KEY", key, "duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                FreshEvidenceError("NONFINITE_JSON", str(path), value)
            ),
        )
    except FreshEvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FreshEvidenceError("JSON_READ_FAILED", str(path), str(exc)) from exc


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise FreshEvidenceError("FILE_READ_FAILED", str(path), str(exc)) from exc


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _require_fields(value: Any, expected: set[str], subject: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise FreshEvidenceError("OBJECT_REQUIRED", subject, "object required")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        raise FreshEvidenceError(
            "FIELD_SET",
            subject,
            f"missing={missing!r}; extra={extra!r}",
        )
    return value


def _parse_time(value: Any, subject: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FreshEvidenceError("TIME_INVALID", subject, "aware ISO timestamp required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreshEvidenceError("TIME_INVALID", subject, str(exc)) from exc
    if result.utcoffset() is None:
        raise FreshEvidenceError("TIME_INVALID", subject, "timezone offset required")
    return result


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, subject: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FreshEvidenceError("NUMBER_REQUIRED", subject, "finite number required")
    result = float(value)
    if not math.isfinite(result):
        raise FreshEvidenceError("NUMBER_REQUIRED", subject, "finite number required")
    return result


def _resolve_repo_path(repository_root: Path, relative: Any, subject: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise FreshEvidenceError("PATH_INVALID", subject, "repository-relative POSIX path required")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise FreshEvidenceError("PATH_INVALID", subject, "safe repository path required")
    root = repository_root.resolve()
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FreshEvidenceError("PATH_INVALID", subject, "path escapes repository") from exc
    return candidate


def _repo_path(repository_root: Path, path: Path, subject: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(repository_root.resolve()).as_posix()
    except ValueError as exc:
        raise FreshEvidenceError("PATH_INVALID", subject, "path must be inside repository") from exc


def _file_binding(repository_root: Path, path: Path, subject: str) -> dict[str, str]:
    if not path.is_file():
        raise FreshEvidenceError("FILE_MISSING", subject, "bound file is missing")
    return {
        "path": _repo_path(repository_root, path, subject),
        "sha256": _sha256_file(path),
    }


def _bound_path(repository_root: Path, value: Any, subject: str) -> Path:
    if not isinstance(value, dict) or "path" not in value or "sha256" not in value:
        raise FreshEvidenceError("BINDING_REQUIRED", subject, "path and SHA-256 binding required")
    binding = value
    digest = binding.get("sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise FreshEvidenceError("SHA256_INVALID", f"{subject}.sha256", "canonical SHA-256 required")
    path = _resolve_repo_path(repository_root, binding.get("path"), f"{subject}.path")
    if not path.is_file():
        raise FreshEvidenceError("FILE_MISSING", subject, "bound file is missing")
    if _sha256_file(path) != digest:
        raise FreshEvidenceError("HASH_MISMATCH", subject, "bound file hash changed")
    return path


def _source_binding(repository_root: Path, relative_paths: Sequence[str]) -> dict[str, Any]:
    digest = hashlib.sha256()
    files: list[dict[str, str]] = []
    for relative in relative_paths:
        path = _resolve_repo_path(repository_root, relative, relative)
        if not path.is_file():
            raise FreshEvidenceError("SOURCE_MISSING", relative, "source file missing")
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
        files.append({"path": relative, "sha256": _sha256_bytes(payload)})
    return {"build_sha256": "sha256:" + digest.hexdigest(), "files": files}


def _scenario_ids(repository_root: Path) -> list[str]:
    path = _resolve_repo_path(repository_root, SCENARIOS_REPO_PATH, "scenario catalog")
    value = _load_json(path)
    rows = value.get("required_scenarios") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise FreshEvidenceError("SCENARIO_CATALOG", SCENARIOS_REPO_PATH, "required_scenarios missing")
    result = [row.get("scenario_id") for row in rows if isinstance(row, dict)]
    if (
        len(result) != 25
        or len(set(result)) != 25
        or not all(isinstance(item, str) and item for item in result)
        or "UI-PERF-001" not in result
    ):
        raise FreshEvidenceError("SCENARIO_CATALOG", SCENARIOS_REPO_PATH, "exact 25-scenario catalog required")
    return list(result)


def _parse_junit(path: Path, expected_ids: Sequence[str], subject: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FreshEvidenceError("JUNIT_READ_FAILED", subject, str(exc)) from exc
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise FreshEvidenceError("JUNIT_XML_UNSAFE", subject, "DTD/entity declarations are forbidden")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FreshEvidenceError("JUNIT_XML_INVALID", subject, str(exc)) from exc
    suites = [suite for suite in root.iter("testsuite") if list(suite.iter("testcase"))]
    if len(suites) != 1:
        raise FreshEvidenceError("JUNIT_SUITE_COUNT", subject, "exactly one testcase-bearing suite required")
    suite = suites[0]

    def count(name: str) -> int:
        try:
            value = int(suite.attrib.get(name, ""))
        except ValueError as exc:
            raise FreshEvidenceError("JUNIT_COUNT", f"{subject}.{name}", str(exc)) from exc
        if value < 0:
            raise FreshEvidenceError("JUNIT_COUNT", f"{subject}.{name}", "nonnegative count required")
        return value

    declared = {name: count(name) for name in ("tests", "failures", "errors", "skipped")}
    testcases = list(suite.iter("testcase"))
    ids: list[str] = []
    failed = 0
    errored = 0
    skipped = 0
    for index, case in enumerate(testcases):
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        if not classname or not name:
            raise FreshEvidenceError("JUNIT_TESTCASE_ID", f"{subject}[{index}]", "classname and name required")
        ids.append(f"{classname}::{name}")
        failed += len(case.findall("failure"))
        errored += len(case.findall("error"))
        skipped += len(case.findall("skipped"))
    if (
        declared != {
            "tests": len(testcases),
            "failures": failed,
            "errors": errored,
            "skipped": skipped,
        }
        or failed
        or errored
        or skipped
    ):
        raise FreshEvidenceError("JUNIT_NOT_GREEN", subject, f"declared={declared!r}")
    if tuple(ids) != tuple(expected_ids) or len(set(ids)) != len(ids):
        raise FreshEvidenceError("JUNIT_TEST_SET", subject, "exact ordered test set required")
    started = _parse_time(suite.attrib.get("timestamp"), f"{subject}.timestamp")
    try:
        duration_value: Any = float(suite.attrib["time"])
    except (KeyError, ValueError) as exc:
        raise FreshEvidenceError("JUNIT_DURATION", subject, "finite duration required") from exc
    duration = _finite_number(duration_value, f"{subject}.time")
    if duration < 0:
        raise FreshEvidenceError("JUNIT_DURATION", subject, "nonnegative duration required")
    ended = started + timedelta(seconds=duration)
    return {
        "started_at": _format_time(started),
        "ended_at": _format_time(ended),
        "duration_seconds": round(duration, 6),
        "test_count": len(ids),
        "test_ids": ids,
    }


def _assert_sources_precede_run(
    repository_root: Path,
    source_paths: Sequence[str],
    run_started_at: str,
    subject: str,
) -> None:
    started = _parse_time(run_started_at, subject).timestamp()
    for relative in source_paths:
        path = _resolve_repo_path(repository_root, relative, relative)
        if path.stat().st_mtime > started + 2.0:
            raise FreshEvidenceError(
                "RUN_PREDATES_CURRENT_SOURCE",
                subject,
                f"JUnit predates current {relative}",
            )


def _sample_windows_cpu_percent(seconds: float) -> float:
    """Return total busy CPU percent using the Windows GetSystemTimes API."""

    import ctypes

    class _FileTime(ctypes.Structure):
        _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

    def snapshot() -> tuple[int, int, int]:
        idle = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise FreshEvidenceError(
                "CPU_SAMPLE_FAILED",
                "preflight",
                "GetSystemTimes failed",
            )

        def ticks(value: _FileTime) -> int:
            return (int(value.high) << 32) | int(value.low)

        return ticks(idle), ticks(kernel), ticks(user)

    before_idle, before_kernel, before_user = snapshot()
    time.sleep(seconds)
    after_idle, after_kernel, after_user = snapshot()
    idle_delta = after_idle - before_idle
    total_delta = (after_kernel - before_kernel) + (after_user - before_user)
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        raise FreshEvidenceError(
            "CPU_SAMPLE_FAILED",
            "preflight",
            "invalid GetSystemTimes delta",
        )
    return 100.0 * (total_delta - idle_delta) / total_delta


def record_quiet_host_preflight(
    *,
    output_path: Path,
    run_id: str,
    repository_root: Path = REPOSITORY_ROOT,
    sample_interval_seconds: float = 1.0,
    max_samples: int = 60,
    sampler: Callable[[float], float] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Sample host CPU and exclusively write only an eligible preflight receipt."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise FreshEvidenceError("RUN_ID_INVALID", "run_id", "safe nonempty run id required")
    interval = _finite_number(sample_interval_seconds, "sample_interval_seconds")
    if interval <= 0 or interval > 10:
        raise FreshEvidenceError("SAMPLE_INTERVAL", "sample_interval_seconds", "must be in (0, 10]")
    if isinstance(max_samples, bool) or not isinstance(max_samples, int) or not (5 <= max_samples <= 600):
        raise FreshEvidenceError("MAX_SAMPLES", "max_samples", "integer in [5, 600] required")
    _repo_path(repository_root, output_path, "output")
    if output_path.exists():
        raise FreshEvidenceError("OUTPUT_EXISTS", "output", "immutable output already exists")
    if sampler is None:
        try:
            import psutil
        except ImportError:  # pragma: no cover - depends on the runtime image
            if os.name != "nt":
                raise FreshEvidenceError(
                    "CPU_SAMPLER_MISSING",
                    "preflight",
                    "psutil is unavailable and no native sampler exists",
                )
            sampler = _sample_windows_cpu_percent
        else:
            sampler = lambda seconds: float(psutil.cpu_percent(interval=seconds))
    clock = now or (lambda: datetime.now(timezone.utc))
    started = clock()
    samples: list[float] = []
    for _ in range(max_samples):
        sample = round(_finite_number(sampler(interval), "cpu_sample"), 3)
        if sample < 0 or sample > 100:
            raise FreshEvidenceError("CPU_SAMPLE_RANGE", "cpu_sample", "CPU percent must be in [0, 100]")
        samples.append(sample)
        if len(samples) >= QUIET_SUFFIX_COUNT and all(
            value < QUIET_THRESHOLD_PERCENT for value in samples[-QUIET_SUFFIX_COUNT:]
        ):
            break
    else:
        raise FreshEvidenceError(
            "QUIET_HOST_NOT_REACHED",
            "preflight",
            f"no {QUIET_SUFFIX_COUNT}-sample suffix below {QUIET_THRESHOLD_PERCENT:g}%",
        )
    ended = clock()
    receipt = {
        "schema_version": 1,
        "contract": QUIET_HOST_PREFLIGHT_CONTRACT,
        "run_id": run_id,
        "started_at": _format_time(started),
        "ended_at": _format_time(ended),
        "cpu_threshold_percent": QUIET_THRESHOLD_PERCENT,
        "required_consecutive_samples": QUIET_SUFFIX_COUNT,
        "sample_interval_seconds": interval,
        "samples_percent": samples,
        "eligible_suffix_percent": samples[-QUIET_SUFFIX_COUNT:],
        "status": "pass",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        with output_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FreshEvidenceError("OUTPUT_EXISTS", "output", "immutable output already exists") from exc
    return receipt


def _validate_preflight(path: Path) -> dict[str, Any]:
    value = _require_fields(
        _load_json(path),
        {
            "schema_version",
            "contract",
            "run_id",
            "started_at",
            "ended_at",
            "cpu_threshold_percent",
            "required_consecutive_samples",
            "sample_interval_seconds",
            "samples_percent",
            "eligible_suffix_percent",
            "status",
        },
        "preflight",
    )
    if (
        value.get("schema_version") != 1
        or value.get("contract") != QUIET_HOST_PREFLIGHT_CONTRACT
        or value.get("status") != "pass"
        or RUN_ID_RE.fullmatch(str(value.get("run_id", ""))) is None
        or value.get("cpu_threshold_percent") != QUIET_THRESHOLD_PERCENT
        or value.get("required_consecutive_samples") != QUIET_SUFFIX_COUNT
    ):
        raise FreshEvidenceError("PREFLIGHT_IDENTITY", str(path), "quiet-host preflight contract drift")
    started = _parse_time(value.get("started_at"), "preflight.started_at")
    ended = _parse_time(value.get("ended_at"), "preflight.ended_at")
    if ended < started:
        raise FreshEvidenceError("PREFLIGHT_TIME", str(path), "end precedes start")
    interval = _finite_number(value.get("sample_interval_seconds"), "preflight.sample_interval_seconds")
    if interval <= 0 or interval > 10:
        raise FreshEvidenceError("PREFLIGHT_INTERVAL", str(path), "sample interval outside (0, 10]")
    samples_raw = value.get("samples_percent")
    suffix_raw = value.get("eligible_suffix_percent")
    if not isinstance(samples_raw, list) or not (QUIET_SUFFIX_COUNT <= len(samples_raw) <= 600):
        raise FreshEvidenceError("PREFLIGHT_SAMPLES", str(path), "bounded sample array required")
    samples = [_finite_number(item, f"preflight.samples[{index}]") for index, item in enumerate(samples_raw)]
    if any(item < 0 or item > 100 for item in samples):
        raise FreshEvidenceError("PREFLIGHT_SAMPLE_RANGE", str(path), "CPU samples must be in [0, 100]")
    suffix = samples[-QUIET_SUFFIX_COUNT:]
    if suffix_raw != suffix or any(item >= QUIET_THRESHOLD_PERCENT for item in suffix):
        raise FreshEvidenceError("PREFLIGHT_QUIET_SUFFIX", str(path), "last five samples must each be below 30%")
    return dict(value)


def _validate_performance_report(
    path: Path,
    *,
    budgets: Mapping[str, int],
    ui_source_build_sha256: str,
) -> dict[str, Any]:
    value = _require_fields(
        _load_json(path),
        {
            "schema_version",
            "contract",
            "recorded_at",
            "measurement_report",
            "measurement_report_sha256",
            "identifier_free",
        },
        "performance_report",
    )
    if (
        value.get("schema_version") != 1
        or value.get("contract") != PERFORMANCE_OBSERVATION_CONTRACT
        or value.get("identifier_free") is not True
    ):
        raise FreshEvidenceError("PERFORMANCE_REPORT_IDENTITY", str(path), "identifier-free observation required")
    recorded_at = _parse_time(value.get("recorded_at"), "performance_report.recorded_at")
    report = _require_fields(
        value.get("measurement_report"),
        {
            "action_log_sha256",
            "browser",
            "contract",
            "fixture_alias",
            "measurement_policy",
            "metrics",
            "profile",
            "root_count",
            "sample_count",
            "samples_per_root",
            "scenario_id",
            "source_build_sha256",
            "state",
            "unit",
        },
        "performance_report.measurement_report",
    )
    if (
        report.get("contract") != PERFORMANCE_MEASUREMENT_CONTRACT
        or report.get("scenario_id") != "UI-PERF-001"
        or report.get("measurement_policy") != "precalibration_measurement_only"
        or report.get("fixture_alias") != "UI-PARITY-RUN-EDITOR"
        or report.get("profile") != "free-owner-chrome-desktop-ko-KR"
        or report.get("root_count") != 2
        or report.get("sample_count") != 6
        or report.get("samples_per_root") != 3
        or report.get("unit") != "ms"
        or report.get("source_build_sha256") != ui_source_build_sha256
        or not isinstance(report.get("action_log_sha256"), str)
        or SHA256_RE.fullmatch(str(report.get("action_log_sha256"))) is None
    ):
        raise FreshEvidenceError("PERFORMANCE_MEASUREMENT_IDENTITY", str(path), "measurement contract drift")
    browser = _require_fields(report.get("browser"), {"family", "version"}, "performance_report.browser")
    if browser.get("family") != "chrome" or not isinstance(browser.get("version"), str) or not browser.get("version"):
        raise FreshEvidenceError("PERFORMANCE_BROWSER", str(path), "Chrome version required")
    expected_state = {
        "created_page_count_delta": 1,
        "external_request_count": 0,
        "independent_page_created": True,
        "page_restored": True,
        "publication_item_delta": 0,
        "publication_revision_delta": 0,
        "synthetic_only": True,
    }
    if report.get("state") != expected_state:
        raise FreshEvidenceError("PERFORMANCE_STATE", str(path), "isolated synthetic state required")
    if _canonical_sha256(report) != value.get("measurement_report_sha256"):
        raise FreshEvidenceError("PERFORMANCE_INTERNAL_HASH", str(path), "measurement report hash mismatch")
    if set(budgets) != MEASUREMENT_METRICS - {MEASUREMENT_ONLY_METRIC}:
        raise FreshEvidenceError("ACTIVE_BUDGET_SET", str(path), "exact eight active metric budgets required")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != MEASUREMENT_METRICS:
        raise FreshEvidenceError("PERFORMANCE_METRIC_SET", str(path), "exact nine measured metrics required")
    result_metrics: dict[str, dict[str, Any]] = {}
    for name in sorted(MEASUREMENT_METRICS):
        distribution = _require_fields(
            metrics.get(name),
            {"max", "min", "p50", "p95", "p99", "sample_count"},
            f"performance_report.metrics.{name}",
        )
        if distribution.get("sample_count") != 6:
            raise FreshEvidenceError("PERFORMANCE_SAMPLE_COUNT", name, "six samples required")
        ordered = [
            _finite_number(distribution.get(key), f"{name}.{key}")
            for key in ("min", "p50", "p95", "p99", "max")
        ]
        if ordered[0] < 0 or ordered != sorted(ordered):
            raise FreshEvidenceError("PERFORMANCE_DISTRIBUTION", name, "ordered nonnegative distribution required")
        row: dict[str, Any] = {
            "p95_ms": ordered[2],
            "sample_count": 6,
        }
        if name == MEASUREMENT_ONLY_METRIC:
            row["budget_state"] = "measurement_only_unbudgeted"
        else:
            budget = _finite_number(budgets[name], f"budget.{name}")
            if ordered[2] > budget:
                raise FreshEvidenceError(
                    "PERFORMANCE_BUDGET_EXCEEDED",
                    name,
                    f"p95 {ordered[2]} > active budget {budget}",
                )
            row.update({"budget_ms": budget, "within_budget": True})
        result_metrics[name] = row
    return {
        "recorded_at": _format_time(recorded_at),
        "measurement_report_sha256": value["measurement_report_sha256"],
        "ui_source_build_sha256": report["source_build_sha256"],
        "browser_version": browser["version"],
        "metrics": result_metrics,
    }


def _validate_performance_run(
    *,
    ordinal: int,
    preflight_path: Path,
    junit_path: Path,
    report_path: Path,
    repository_root: Path,
    budgets: Mapping[str, int],
    ui_source_build_sha256: str,
) -> dict[str, Any]:
    preflight = _validate_preflight(preflight_path)
    junit = _parse_junit(junit_path, (PERFORMANCE_TEST_ID,), f"performance[{ordinal}].junit")
    report = _validate_performance_report(
        report_path,
        budgets=budgets,
        ui_source_build_sha256=ui_source_build_sha256,
    )
    preflight_end = _parse_time(preflight["ended_at"], "preflight.ended_at")
    junit_start = _parse_time(junit["started_at"], "junit.started_at")
    junit_end = _parse_time(junit["ended_at"], "junit.ended_at")
    report_time = _parse_time(report["recorded_at"], "performance_report.recorded_at")
    gap = junit_start - preflight_end
    if gap < timedelta(0) or gap > MAX_PREFLIGHT_TO_TEST_GAP:
        raise FreshEvidenceError(
            "PREFLIGHT_RUN_ADJACENCY",
            f"performance[{ordinal}]",
            "test must start within five minutes after preflight",
        )
    if report_time < junit_start - timedelta(seconds=5) or report_time > junit_end + timedelta(seconds=60):
        raise FreshEvidenceError(
            "REPORT_RUN_ADJACENCY",
            f"performance[{ordinal}]",
            "report timestamp must belong to its JUnit run",
        )
    _assert_sources_precede_run(
        repository_root,
        (*UI_SOURCE_REPO_PATHS, VALIDATION_SOURCE_REPO_PATHS[1], VALIDATION_SOURCE_REPO_PATHS[2]),
        junit["started_at"],
        f"performance[{ordinal}]",
    )
    return {
        "ordinal": ordinal,
        "run_id": preflight["run_id"],
        "preflight": {
            **_file_binding(repository_root, preflight_path, f"performance[{ordinal}].preflight"),
            "started_at": preflight["started_at"],
            "ended_at": preflight["ended_at"],
            "cpu_threshold_percent": QUIET_THRESHOLD_PERCENT,
            "required_consecutive_samples": QUIET_SUFFIX_COUNT,
            "samples_percent": preflight["samples_percent"],
            "eligible_suffix_percent": preflight["eligible_suffix_percent"],
        },
        "junit": {
            **_file_binding(repository_root, junit_path, f"performance[{ordinal}].junit"),
            **junit,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        },
        "identifier_free_report": {
            **_file_binding(repository_root, report_path, f"performance[{ordinal}].report"),
            "recorded_at": report["recorded_at"],
            "measurement_report_sha256": report["measurement_report_sha256"],
            "identifier_free": True,
            "ui_source_build_sha256": report["ui_source_build_sha256"],
            "browser_version": report["browser_version"],
        },
        "metrics": report["metrics"],
        "all_active_metrics_within_budget": True,
        "status": "pass",
    }


def _artifact_paths_from_fresh_evidence(
    receipt: Mapping[str, Any], repository_root: Path
) -> tuple[Path, Path, list[tuple[Path, Path, Path]], Path]:
    evidence = _require_fields(
        receipt.get("fresh_gate_evidence"),
        {
            "schema_version",
            "contract",
            "base_capture_receipt",
            "product_build",
            "validation_sources",
            "core_25_junit",
            "performance_budget",
            "performance_runs",
            "consecutive_quiet_host_runs",
        },
        "fresh_gate_evidence",
    )
    capture_path = _bound_path(repository_root, evidence.get("base_capture_receipt"), "base_capture_receipt")
    core_path = _bound_path(repository_root, evidence.get("core_25_junit"), "core_25_junit")
    budget_path = _bound_path(repository_root, evidence.get("performance_budget"), "performance_budget")
    rows = evidence.get("performance_runs")
    if not isinstance(rows, list) or len(rows) != 2:
        raise FreshEvidenceError("PERFORMANCE_RUN_COUNT", "performance_runs", "exactly two runs required")
    triples: list[tuple[Path, Path, Path]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise FreshEvidenceError("PERFORMANCE_RUN_SHAPE", f"performance_runs[{index}]", "object required")
        triples.append(
            (
                _bound_path(repository_root, row.get("preflight"), f"performance_runs[{index}].preflight"),
                _bound_path(repository_root, row.get("junit"), f"performance_runs[{index}].junit"),
                _bound_path(repository_root, row.get("identifier_free_report"), f"performance_runs[{index}].identifier_free_report"),
            )
        )
    return capture_path, core_path, triples, budget_path


def _assemble_receipt(
    *,
    capture_path: Path,
    core_junit_path: Path,
    performance_runs: Sequence[tuple[Path, Path, Path]],
    budget_path: Path,
    repository_root: Path,
    recorded_at: str,
    receipt_id: str | None,
    budget_loader: Callable[[Path], Mapping[str, int]],
) -> dict[str, Any]:
    if len(performance_runs) != 2:
        raise FreshEvidenceError("PERFORMANCE_RUN_COUNT", "performance_runs", "exactly two runs required")
    capture = _load_json(capture_path)
    if not isinstance(capture, dict) or capture.get("contract") != V0B_CONTRACT:
        raise FreshEvidenceError("CAPTURE_RECEIPT_IDENTITY", str(capture_path), "V0B-P1 capture receipt required")
    current_capture_source = _source_binding(repository_root, SOURCE_REPO_PATHS)
    release = capture.get("release_candidate")
    if not isinstance(release, dict) or release.get("source_build_sha256") != current_capture_source["build_sha256"] or release.get("source_files") != current_capture_source["files"]:
        raise FreshEvidenceError("CAPTURE_PRODUCT_BUILD", str(capture_path), "capture must bind the current product build")
    current_ui_source = _source_binding(repository_root, UI_SOURCE_REPO_PATHS)
    validation_sources = _source_binding(repository_root, VALIDATION_SOURCE_REPO_PATHS)
    core = _parse_junit(core_junit_path, CORE_TEST_IDS, "core_25.junit")
    _assert_sources_precede_run(
        repository_root,
        (*SOURCE_REPO_PATHS, VALIDATION_SOURCE_REPO_PATHS[0]),
        core["started_at"],
        "core_25",
    )
    try:
        budgets = dict(budget_loader(budget_path))
    except FreshEvidenceError:
        raise
    except Exception as exc:
        raise FreshEvidenceError("ACTIVE_BUDGET_INVALID", str(budget_path), str(exc)) from exc
    if set(budgets) != MEASUREMENT_METRICS - {MEASUREMENT_ONLY_METRIC}:
        raise FreshEvidenceError("ACTIVE_BUDGET_SET", str(budget_path), "exact eight active budgets required")
    run_rows = [
        _validate_performance_run(
            ordinal=index,
            preflight_path=triple[0],
            junit_path=triple[1],
            report_path=triple[2],
            repository_root=repository_root,
            budgets=budgets,
            ui_source_build_sha256=current_ui_source["build_sha256"],
        )
        for index, triple in enumerate(performance_runs, start=1)
    ]
    first_end = _parse_time(run_rows[0]["junit"]["ended_at"], "performance_runs[0].junit.ended_at")
    second_preflight_start = _parse_time(run_rows[1]["preflight"]["started_at"], "performance_runs[1].preflight.started_at")
    if second_preflight_start < first_end:
        raise FreshEvidenceError(
            "PERFORMANCE_RUN_ORDER",
            "performance_runs",
            "second preflight must start after the first JUnit run ends",
        )
    if run_rows[0]["run_id"] == run_rows[1]["run_id"]:
        raise FreshEvidenceError("PERFORMANCE_RUN_ID", "performance_runs", "run ids must be unique")
    for field in ("preflight", "junit", "identifier_free_report"):
        hashes = [row[field]["sha256"] for row in run_rows]
        if len(set(hashes)) != 2:
            raise FreshEvidenceError("PERFORMANCE_ARTIFACT_REUSE", field, "two distinct artifacts required")
    scenario_ids = _scenario_ids(repository_root)
    limiting: tuple[float, int, str, float, float] | None = None
    for row in run_rows:
        for name, metric in row["metrics"].items():
            if name == MEASUREMENT_ONLY_METRIC:
                continue
            p95 = float(metric["p95_ms"])
            budget = float(metric["budget_ms"])
            candidate = (p95 / budget, int(row["ordinal"]), name, p95, budget)
            if limiting is None or candidate > limiting:
                limiting = candidate
    assert limiting is not None
    evidence = {
        "schema_version": 1,
        "contract": FINAL_EVIDENCE_CONTRACT,
        "base_capture_receipt": _file_binding(repository_root, capture_path, "base_capture_receipt"),
        "product_build": {
            "capture_source_build_sha256": current_capture_source["build_sha256"],
            "ui_source_build_sha256": current_ui_source["build_sha256"],
            "source_files": current_capture_source["files"],
        },
        "validation_sources": validation_sources,
        "core_25_junit": {
            **_file_binding(repository_root, core_junit_path, "core_25_junit"),
            **core,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "source_build_sha256": current_capture_source["build_sha256"],
        },
        "performance_budget": {
            **_file_binding(repository_root, budget_path, "performance_budget"),
            "percentile": "p95",
            "sample_count_per_metric": 6,
            "metric_budgets_ms": budgets,
        },
        "performance_runs": run_rows,
        "consecutive_quiet_host_runs": True,
    }
    artifact_digest = _canonical_sha256(
        {
            "capture": evidence["base_capture_receipt"]["sha256"],
            "core": evidence["core_25_junit"]["sha256"],
            "performance": [
                {
                    "preflight": row["preflight"]["sha256"],
                    "junit": row["junit"]["sha256"],
                    "report": row["identifier_free_report"]["sha256"],
                }
                for row in run_rows
            ],
            "product": current_capture_source["build_sha256"],
        }
    )
    expected_receipt_id = "V0B-P1-L6-FINAL-CANDIDATE-FRESH-" + artifact_digest[7:23].upper()
    if receipt_id is not None and receipt_id != expected_receipt_id:
        raise FreshEvidenceError("RECEIPT_ID", "receipt_id", "artifact-derived receipt id mismatch")
    derived = copy.deepcopy(capture)
    derived["receipt_id"] = expected_receipt_id
    derived["recorded_at"] = recorded_at
    derived["core_25"] = {
        "status": "pass",
        "source_build_sha256": current_capture_source["build_sha256"],
        "collected_test_count": 21,
        "passed_test_count": 21,
        "failed_test_count": 0,
        "started_at": core["started_at"],
        "ended_at": core["ended_at"],
        "scenario_results": [
            {"scenario_id": scenario_id, "status": "pass"}
            for scenario_id in scenario_ids
        ],
        "source": "fresh_exact_junit_bound_by_fresh_gate_evidence",
    }
    derived["performance"] = {
        "scenario_id": "UI-PERF-001",
        "status": "pass",
        "source_build_sha256": current_capture_source["build_sha256"],
        "ui_source_build_sha256": current_ui_source["build_sha256"],
        "observed_p95_ms": limiting[3],
        "approved_budget_ms": limiting[4],
        "limiting_metric": limiting[2],
        "limiting_run_ordinal": limiting[1],
        "quiet_host_verified": True,
        "consecutive_pass_count": 2,
        "all_active_metrics_within_budget": True,
        "started_at": run_rows[0]["junit"]["started_at"],
        "ended_at": run_rows[1]["junit"]["ended_at"],
        "source": "two_fresh_quiet_host_junit_and_identifier_free_reports",
    }
    derived["fresh_gate_evidence"] = evidence
    derived["claims"] = {
        "V0R_P1_gate_passed": True,
        "final_release_candidate_core_25_rerun": True,
        "all_adopted_multiuser_states_captured": True,
        "zero_unmasked_RGBA_difference": bool(
            capture.get("claims", {}).get("zero_unmasked_RGBA_difference")
        ),
        "strict_visual_similarity_passed": True,
        "accessibility_evidence_complete": True,
        "PIX_001_closed": True,
        "A11Y_001_closed": True,
        "UI_CORE_25_closed": True,
        "V0B_P1_gate_passed": True,
        "production_changed": False,
        "second_brain_changed": False,
        "product_complete": False,
        "active_shared_goal_complete": False,
    }
    recorded = _parse_time(recorded_at, "recorded_at")
    latest_artifact = max(
        _parse_time(core["ended_at"], "core.ended_at"),
        *(_parse_time(row["junit"]["ended_at"], "performance.junit.ended_at") for row in run_rows),
    )
    if recorded < latest_artifact:
        raise FreshEvidenceError("RECORDED_AT", "recorded_at", "receipt predates bound evidence")
    return derived


def _base_verify_kwargs(receipt: Mapping[str, Any], repository_root: Path) -> dict[str, Path]:
    reference_path = _resolve_repo_path(
        repository_root,
        receipt.get("reference_receipt", {}).get("path") if isinstance(receipt.get("reference_receipt"), dict) else None,
        "reference_receipt.path",
    )
    reference = _load_json(reference_path)
    reference_run = reference.get("run") if isinstance(reference, dict) else None
    run = receipt.get("run")
    if not isinstance(reference_run, dict) or not isinstance(run, dict):
        raise FreshEvidenceError("CAPTURE_RUN_PATHS", "run", "capture/reference run paths required")
    return {
        "reference_receipt_path": reference_path,
        "reference_screenshot_root": _resolve_repo_path(repository_root, reference_run.get("screenshot_root"), "reference.run.screenshot_root"),
        "reference_mask_root": _resolve_repo_path(repository_root, reference_run.get("mask_root"), "reference.run.mask_root"),
        "candidate_screenshot_root": _resolve_repo_path(repository_root, run.get("candidate_screenshot_root"), "run.candidate_screenshot_root"),
        "diff_root": _resolve_repo_path(repository_root, run.get("diff_root"), "run.diff_root"),
        "scenarios_path": _resolve_repo_path(repository_root, SCENARIOS_REPO_PATH, "scenarios"),
    }


def _run_base_verifier(
    *,
    path: Path,
    receipt: Mapping[str, Any],
    repository_root: Path,
    base_verifier: Callable[..., Mapping[str, Any]],
    allow_receipt_bound_runtime_for_visual_audit: bool = False,
) -> Mapping[str, Any]:
    kwargs: dict[str, Any] = _base_verify_kwargs(receipt, repository_root)
    if allow_receipt_bound_runtime_for_visual_audit:
        kwargs["allow_receipt_bound_runtime_for_visual_audit"] = True
    report = base_verifier(path, **kwargs)
    visual_audit_only = (
        allow_receipt_bound_runtime_for_visual_audit
        and report.get("status") == "pass"
        and report.get("error_count") == 0
        and report.get("errors") == []
        and report.get("runtime_compatibility_required") is True
        and report.get("gate_passed") is False
    )
    if not visual_audit_only and (
        report.get("status") != "pass" or report.get("gate_passed") is not True
    ):
        raise FreshEvidenceError(
            "BASE_V0B_GATE",
            str(path),
            json.dumps(report.get("errors", report), ensure_ascii=False, sort_keys=True),
        )
    return report


def compile_final_receipt(
    *,
    capture_path: Path,
    core_junit_path: Path,
    performance_runs: Sequence[tuple[Path, Path, Path]],
    output_path: Path,
    budget_path: Path = DEFAULT_BUDGET,
    repository_root: Path = REPOSITORY_ROOT,
    budget_loader: Callable[[Path], Mapping[str, int]] = load_active_performance_budget,
    base_verifier: Callable[..., Mapping[str, Any]] = verify_v0b_receipt,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Validate all inputs, then exclusively create a derived immutable receipt."""

    _repo_path(repository_root, output_path, "output")
    if output_path.exists():
        raise FreshEvidenceError("OUTPUT_EXISTS", "output", "immutable output already exists")
    if output_path.resolve(strict=False) == capture_path.resolve(strict=False):
        raise FreshEvidenceError("OUTPUT_IS_INPUT", "output", "base capture receipt cannot be overwritten")
    recorded_at = _format_time((now or (lambda: datetime.now(timezone.utc)))())
    derived = _assemble_receipt(
        capture_path=capture_path,
        core_junit_path=core_junit_path,
        performance_runs=performance_runs,
        budget_path=budget_path,
        repository_root=repository_root,
        recorded_at=recorded_at,
        receipt_id=None,
        budget_loader=budget_loader,
    )
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="v0b-fresh-gate-validate-",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            json.dump(derived, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_path = Path(handle.name)
        _run_base_verifier(
            path=temporary_path,
            receipt=derived,
            repository_root=repository_root,
            base_verifier=base_verifier,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    payload = json.dumps(derived, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        with output_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise FreshEvidenceError("OUTPUT_EXISTS", "output", "immutable output already exists") from exc
    return {
        "contract": FINAL_AUDIT_CONTRACT,
        "status": "pass",
        "receipt_path": _repo_path(repository_root, output_path, "output"),
        "receipt_sha256": _sha256_file(output_path),
        "receipt_id": derived["receipt_id"],
        "core_test_count": 21,
        "performance_run_count": 2,
        "gate_passed": True,
        "product_complete": False,
    }


def verify_final_receipt(
    path: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    budget_loader: Callable[[Path], Mapping[str, int]] = load_active_performance_budget,
    base_verifier: Callable[..., Mapping[str, Any]] = verify_v0b_receipt,
    allow_receipt_bound_runtime_for_visual_audit: bool = False,
) -> dict[str, Any]:
    """Rebuild all derived fields from bound artifacts and run the base V0B audit."""

    try:
        receipt = _load_json(path)
        if not isinstance(receipt, dict):
            raise FreshEvidenceError("RECEIPT_SHAPE", "$", "object required")
        capture, core, runs, budget = _artifact_paths_from_fresh_evidence(receipt, repository_root)
        expected = _assemble_receipt(
            capture_path=capture,
            core_junit_path=core,
            performance_runs=runs,
            budget_path=budget,
            repository_root=repository_root,
            recorded_at=str(receipt.get("recorded_at", "")),
            receipt_id=str(receipt.get("receipt_id", "")),
            budget_loader=budget_loader,
        )
        if receipt != expected:
            raise FreshEvidenceError("DERIVED_RECEIPT_DRIFT", "$", "receipt differs from rebuilt artifact-bound value")
        base = _run_base_verifier(
            path=path,
            receipt=receipt,
            repository_root=repository_root,
            base_verifier=base_verifier,
            allow_receipt_bound_runtime_for_visual_audit=(
                allow_receipt_bound_runtime_for_visual_audit
            ),
        )
        runtime_compatibility_required = (
            base.get("runtime_compatibility_required") is True
        )
        return {
            "contract": FINAL_AUDIT_CONTRACT,
            "status": "pass",
            "error_count": 0,
            "errors": [],
            "receipt_sha256": _sha256_file(path),
            "receipt_id": receipt["receipt_id"],
            "base_v0b_receipt_sha256": receipt["fresh_gate_evidence"]["base_capture_receipt"]["sha256"],
            "core_test_count": 21,
            "performance_run_count": 2,
            "exact_pixel_pass_count": base.get("exact_pixel_pass_count"),
            "visual_similarity_pass_count": base.get(
                "visual_similarity_pass_count"
            ),
            "accessibility_pass_count": base.get("accessibility_pass_count"),
            "runtime_compatibility_required": runtime_compatibility_required,
            "gate_passed": not runtime_compatibility_required,
            "product_complete": False,
        }
    except FreshEvidenceError as exc:
        return {
            "contract": FINAL_AUDIT_CONTRACT,
            "status": "fail",
            "error_count": 1,
            "errors": [exc.as_dict()],
            "gate_passed": False,
            "product_complete": False,
        }


def _print(value: object, compact: bool) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="record an immutable quiet-host CPU preflight")
    preflight.add_argument("--run-id", required=True)
    preflight.add_argument("--output", type=Path, required=True)
    preflight.add_argument("--sample-interval-seconds", type=float, default=1.0)
    preflight.add_argument("--max-samples", type=int, default=60)
    preflight.add_argument("--compact", action="store_true")
    compile_parser = subparsers.add_parser("compile", help="compile a new immutable final receipt")
    compile_parser.add_argument("--capture-receipt", type=Path, required=True)
    compile_parser.add_argument("--core-junit", type=Path, required=True)
    compile_parser.add_argument(
        "--performance-run",
        nargs=3,
        action="append",
        metavar=("PREFLIGHT", "JUNIT", "REPORT"),
        required=True,
    )
    compile_parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    compile_parser.add_argument("--output", type=Path, required=True)
    compile_parser.add_argument("--compact", action="store_true")
    verify_parser = subparsers.add_parser("verify", help="verify a compiled final receipt")
    verify_parser.add_argument("--receipt", type=Path, required=True)
    verify_parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            value = record_quiet_host_preflight(
                output_path=args.output,
                run_id=args.run_id,
                sample_interval_seconds=args.sample_interval_seconds,
                max_samples=args.max_samples,
            )
            report = {
                "contract": QUIET_HOST_PREFLIGHT_CONTRACT,
                "status": "pass",
                "run_id": value["run_id"],
                "sample_count": len(value["samples_percent"]),
                "eligible_suffix_percent": value["eligible_suffix_percent"],
                "preflight_sha256": _sha256_file(args.output),
            }
        elif args.command == "compile":
            report = compile_final_receipt(
                capture_path=args.capture_receipt,
                core_junit_path=args.core_junit,
                performance_runs=[tuple(Path(item) for item in row) for row in args.performance_run],
                output_path=args.output,
                budget_path=args.budget,
            )
        else:
            report = verify_final_receipt(args.receipt)
        _print(report, args.compact)
        return 0 if report.get("status") == "pass" else 1
    except FreshEvidenceError as exc:
        report = {
            "contract": FINAL_AUDIT_CONTRACT,
            "status": "fail",
            "error_count": 1,
            "errors": [exc.as_dict()],
            "gate_passed": False,
        }
        _print(report, getattr(args, "compact", False))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CORE_TEST_IDS",
    "FreshEvidenceError",
    "compile_final_receipt",
    "record_quiet_host_preflight",
    "verify_final_receipt",
]
