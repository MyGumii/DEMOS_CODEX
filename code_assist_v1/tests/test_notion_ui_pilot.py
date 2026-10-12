from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.ui_pilot import (
    PilotDenied,
    authorize_isolated_pilot,
    build_pilot_report,
    create_recovery_snapshot,
    initialize_synthetic_pilot_root,
    restore_recovery_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _readiness() -> dict[str, bool]:
    return {
        "backup_ready": True,
        "browser_suite_green": True,
        "redaction_ready": True,
        "restore_ready": True,
        "scenario_contract_valid": True,
        "second_brain_gate_green": True,
    }


class _PathTrap(os.PathLike[str]):
    def __fspath__(self) -> str:
        raise AssertionError("path was inspected before the pilot gate")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("opt_in", "kill_switch", "synthetic"),
    (
        (False, False, True),
        (True, True, True),
        (True, False, False),
    ),
)
def test_pilot_default_off_kill_switch_and_synthetic_gate_precede_path_access(
    opt_in: bool,
    kill_switch: bool,
    synthetic: bool,
) -> None:
    with pytest.raises(PilotDenied):
        authorize_isolated_pilot(
            data_root=_PathTrap(),
            test_root=_PathTrap(),
            readiness=_readiness(),
            opt_in_confirmed=opt_in,
            kill_switch_engaged=kill_switch,
            synthetic_only_confirmed=synthetic,
        )


@pytest.mark.unit
def test_pilot_readiness_is_exact_and_precedes_path_access() -> None:
    readiness = _readiness()
    readiness.pop("backup_ready")
    with pytest.raises(PilotDenied, match="readiness"):
        authorize_isolated_pilot(
            data_root=_PathTrap(),
            test_root=_PathTrap(),
            readiness=readiness,
            opt_in_confirmed=True,
            kill_switch_engaged=False,
            synthetic_only_confirmed=True,
        )


@pytest.mark.unit
def test_copy_on_write_snapshot_and_restore_preserve_baseline_without_overwrite(
    tmp_path: Path,
) -> None:
    active = tmp_path / "pilot-active"
    permit = authorize_isolated_pilot(
        data_root=active,
        test_root=tmp_path,
        readiness=_readiness(),
        opt_in_confirmed=True,
        kill_switch_engaged=False,
        synthetic_only_confirmed=True,
    )
    initialize_synthetic_pilot_root(permit)
    state = active / "synthetic-state.txt"
    state.write_text("baseline", encoding="utf-8")

    snapshot = create_recovery_snapshot(
        permit,
        destination=tmp_path / "pilot-backup",
        quiesced_confirmed=True,
        kill_switch_engaged=False,
    )
    state.write_text("canary", encoding="utf-8")
    restored = restore_recovery_snapshot(
        snapshot,
        destination=tmp_path / "pilot-restored",
        quiesced_confirmed=True,
        kill_switch_engaged=False,
    )

    assert state.read_text(encoding="utf-8") == "canary"
    assert (restored.root / "synthetic-state.txt").read_text(encoding="utf-8") == (
        "baseline"
    )
    assert snapshot.descriptor() == {
        "copy_on_write": True,
        "file_count": 2,
        "quiesced": True,
        "verified": True,
    }
    with pytest.raises(PilotDenied, match="new and distinct"):
        restore_recovery_snapshot(
            snapshot,
            destination=restored.root,
            quiesced_confirmed=True,
            kill_switch_engaged=False,
        )


@pytest.mark.unit
def test_pilot_report_is_identifier_free_and_requires_successful_recovery() -> None:
    report = build_pilot_report(
        backup_file_count=9,
        baseline_publication_items=0,
        baseline_publication_revisions=0,
        canary_publication_items=1,
        canary_publication_revisions=1,
        restored_publication_items=0,
        restored_publication_revisions=0,
        baseline_restored=True,
        kill_switch_denial_verified=True,
        second_brain_gate_green=True,
    )
    assert report["default_off"] is True
    assert report["kill_switch_exercised"] is True
    assert report["publication_counts"] == {
        "baseline_items": 0,
        "baseline_revisions": 0,
        "canary_items": 1,
        "canary_revisions": 1,
        "restored_items": 0,
        "restored_revisions": 0,
    }
    assert "path" not in str(report).lower()
    with pytest.raises(ValueError, match="successful recovery"):
        build_pilot_report(
            backup_file_count=9,
            baseline_publication_items=0,
            baseline_publication_revisions=0,
            canary_publication_items=1,
            canary_publication_revisions=1,
            restored_publication_items=0,
            restored_publication_revisions=0,
            baseline_restored=False,
            kill_switch_denial_verified=True,
            second_brain_gate_green=True,
        )


@pytest.mark.unit
def test_pilot_module_import_has_no_app_or_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    script = """
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
import code_assist_v1.tools.notion_parity.ui_pilot  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.data_paths',
        'code_assist_v1.knowledge_core',
    }
    or name.startswith('code_assist_v1.routes_')
    or name.startswith('code_assist_v1.notion_workspace')
)
print(json.dumps({'forbidden': forbidden, 'changed': before != after}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}
