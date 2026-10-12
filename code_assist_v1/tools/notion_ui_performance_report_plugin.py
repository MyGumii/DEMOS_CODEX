"""Persist the identifier-free UI-PERF-001 report from an explicit pytest run."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


REPORT_PATH_ENV = "CODE_ASSIST_UI_PERF_REPORT_PATH"
TEST_NAME = (
    "test_ui_perf_001_measurements_are_isolated_bounded_and_precalibration_only"
)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_report(report: dict[str, Any]) -> None:
    configured = os.environ.get(REPORT_PATH_ENV, "").strip()
    if not configured:
        return
    output = Path(configured).resolve(strict=False)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "contract": "code_assist.notion.core_free_ui.performance_observation.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "measurement_report": report,
        "measurement_report_sha256": _canonical_sha256(report),
        "identifier_free": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def pytest_collection_modifyitems(items: list[object]) -> None:
    """Wrap only the collected UI-PERF-001 measurement function."""

    for item in items:
        if getattr(item, "name", "") != TEST_NAME:
            continue
        module = getattr(item, "module", None)
        original = getattr(module, "_run_ui_perf_001_measurements", None)
        if not callable(original) or getattr(original, "__report_sink_wrapped__", False):
            continue

        def wrapped(
            playwright: object,
            roots: tuple[Path, Path],
            *,
            _original: Callable[[object, tuple[Path, Path]], dict[str, Any]] = original,
        ) -> dict[str, Any]:
            report = _original(playwright, roots)
            _write_report(report)
            return report

        wrapped.__report_sink_wrapped__ = True  # type: ignore[attr-defined]
        setattr(module, "_run_ui_perf_001_measurements", wrapped)


__all__ = ["REPORT_PATH_ENV", "TEST_NAME"]
