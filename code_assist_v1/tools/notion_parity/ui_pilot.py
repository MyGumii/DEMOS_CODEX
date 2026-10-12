"""Fail-closed helpers for the isolated Core Free UI pilot rehearsal.

The module is deliberately not registered by the application or a request
path. Importing it performs no filesystem access. A caller must explicitly
authorize one synthetic OS-temporary root before any mutation, and recovery is
copy-on-write into a new root rather than an in-place destructive rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping

from code_assist_v1.test_safety import assert_isolated_test_path


CONTRACT = "code_assist.notion.core_free_ui.isolated_pilot.v1"
REPORT_CONTRACT = "code_assist.notion.core_free_ui.isolated_pilot_report.v1"
SENTINEL_NAME = ".code-assist-ui-pilot-synthetic.json"
PILOT_ALIAS = "UI-PILOT-CANARY"
_READINESS_KEYS = frozenset(
    {
        "backup_ready",
        "browser_suite_green",
        "redaction_ready",
        "restore_ready",
        "scenario_contract_valid",
        "second_brain_gate_green",
    }
)


class PilotDenied(RuntimeError):
    """Raised before an unauthorized or unsafe pilot filesystem action."""


@dataclass(frozen=True, slots=True)
class PilotPermit:
    root: Path
    test_root: Path
    alias: str

    def descriptor(self) -> dict[str, object]:
        return {
            "alias": self.alias,
            "contract": CONTRACT,
            "default_off": True,
            "isolated_data_root": True,
            "kill_switch_available": True,
            "live_local_data_accessed": False,
            "opt_in": True,
            "synthetic_only": True,
        }


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    root: Path
    test_root: Path
    tree_sha256: str
    file_count: int

    def descriptor(self) -> dict[str, object]:
        return {
            "copy_on_write": True,
            "file_count": self.file_count,
            "quiesced": True,
            "verified": True,
        }


def _revalidate_permit(permit: PilotPermit) -> None:
    resolved_test_root = assert_isolated_test_path(
        permit.test_root,
        test_root=permit.test_root,
    )
    resolved_root = assert_isolated_test_path(
        permit.root,
        test_root=resolved_test_root,
    )
    if resolved_test_root != permit.test_root or resolved_root != permit.root:
        raise PilotDenied("pilot permit path identity changed")


def authorize_isolated_pilot(
    *,
    data_root: str | os.PathLike[str],
    test_root: str | os.PathLike[str],
    readiness: Mapping[str, object],
    opt_in_confirmed: bool,
    kill_switch_engaged: bool,
    synthetic_only_confirmed: bool,
) -> PilotPermit:
    """Authorize one exact test root, checking non-path gates first."""

    if opt_in_confirmed is not True:
        raise PilotDenied("pilot is default-off without explicit opt-in")
    if kill_switch_engaged is not False:
        raise PilotDenied("pilot kill switch is engaged")
    if synthetic_only_confirmed is not True:
        raise PilotDenied("pilot requires an explicit synthetic-only assertion")
    if not isinstance(readiness, Mapping) or set(readiness) != _READINESS_KEYS:
        raise PilotDenied("pilot readiness contract is incomplete")
    if any(readiness[key] is not True for key in _READINESS_KEYS):
        raise PilotDenied("pilot readiness is not green")

    try:
        resolved_test_root = assert_isolated_test_path(
            test_root,
            test_root=test_root,
        )
        resolved_root = assert_isolated_test_path(
            data_root,
            test_root=resolved_test_root,
        )
    except Exception as exc:
        raise PilotDenied("pilot root is not an isolated test path") from exc
    if resolved_root == resolved_test_root:
        raise PilotDenied("pilot data root must be below the test root")
    return PilotPermit(
        root=resolved_root,
        test_root=resolved_test_root,
        alias=PILOT_ALIAS,
    )


def initialize_synthetic_pilot_root(permit: PilotPermit) -> None:
    _revalidate_permit(permit)
    permit.root.mkdir(parents=True, exist_ok=False)
    sentinel = {
        "alias": permit.alias,
        "contract": CONTRACT,
        "synthetic_only": True,
    }
    (permit.root / SENTINEL_NAME).write_text(
        json.dumps(sentinel, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _require_sentinel(root: Path) -> None:
    sentinel_path = root / SENTINEL_NAME
    try:
        value = json.loads(sentinel_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PilotDenied("pilot sentinel is missing or invalid") from exc
    if value != {
        "alias": PILOT_ALIAS,
        "contract": CONTRACT,
        "synthetic_only": True,
    }:
        raise PilotDenied("pilot sentinel does not match the frozen contract")


def _reject_links(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        is_junction = bool(getattr(path, "is_junction", lambda: False)())
        if path.is_symlink() or is_junction:
            raise PilotDenied("pilot recovery tree contains a link or junction")


def _tree_fingerprint(root: Path) -> tuple[str, int]:
    _reject_links(root)
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            digest.update(b"D\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            continue
        if not path.is_file():
            raise PilotDenied("pilot recovery tree contains an unsupported entry")
        file_count += 1
        digest.update(b"F\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest(), file_count


def create_recovery_snapshot(
    permit: PilotPermit,
    *,
    destination: str | os.PathLike[str],
    quiesced_confirmed: bool,
    kill_switch_engaged: bool,
) -> RecoverySnapshot:
    """Copy a quiesced synthetic pilot root into a new exact destination."""

    if kill_switch_engaged is not False:
        raise PilotDenied("pilot kill switch is engaged")
    if quiesced_confirmed is not True:
        raise PilotDenied("pilot recovery snapshot requires quiescence")
    _revalidate_permit(permit)
    destination_root = assert_isolated_test_path(
        destination,
        test_root=permit.test_root,
    )
    if destination_root == permit.root or destination_root.exists():
        raise PilotDenied("pilot recovery destination must be new and distinct")
    _require_sentinel(permit.root)
    source_sha256, source_count = _tree_fingerprint(permit.root)
    shutil.copytree(permit.root, destination_root, copy_function=shutil.copy2)
    copied_sha256, copied_count = _tree_fingerprint(destination_root)
    if (source_sha256, source_count) != (copied_sha256, copied_count):
        raise PilotDenied("pilot recovery snapshot verification failed")
    return RecoverySnapshot(
        root=destination_root,
        test_root=permit.test_root,
        tree_sha256=copied_sha256,
        file_count=copied_count,
    )


def restore_recovery_snapshot(
    snapshot: RecoverySnapshot,
    *,
    destination: str | os.PathLike[str],
    quiesced_confirmed: bool,
    kill_switch_engaged: bool,
) -> PilotPermit:
    """Restore into a new root, preserving the changed pilot root for audit."""

    if kill_switch_engaged is not False:
        raise PilotDenied("pilot kill switch is engaged")
    if quiesced_confirmed is not True:
        raise PilotDenied("pilot recovery restore requires quiescence")
    resolved_test_root = assert_isolated_test_path(
        snapshot.test_root,
        test_root=snapshot.test_root,
    )
    resolved_snapshot_root = assert_isolated_test_path(
        snapshot.root,
        test_root=resolved_test_root,
    )
    if (
        resolved_test_root != snapshot.test_root
        or resolved_snapshot_root != snapshot.root
    ):
        raise PilotDenied("pilot recovery snapshot path identity changed")
    destination_root = assert_isolated_test_path(
        destination,
        test_root=snapshot.test_root,
    )
    if destination_root == snapshot.root or destination_root.exists():
        raise PilotDenied("pilot restore destination must be new and distinct")
    current_sha256, current_count = _tree_fingerprint(snapshot.root)
    if (current_sha256, current_count) != (
        snapshot.tree_sha256,
        snapshot.file_count,
    ):
        raise PilotDenied("pilot recovery snapshot changed after verification")
    shutil.copytree(snapshot.root, destination_root, copy_function=shutil.copy2)
    restored_sha256, restored_count = _tree_fingerprint(destination_root)
    if (restored_sha256, restored_count) != (
        snapshot.tree_sha256,
        snapshot.file_count,
    ):
        raise PilotDenied("pilot restored copy verification failed")
    _require_sentinel(destination_root)
    return PilotPermit(
        root=destination_root,
        test_root=snapshot.test_root,
        alias=PILOT_ALIAS,
    )


def build_pilot_report(
    *,
    backup_file_count: int,
    baseline_publication_items: int,
    baseline_publication_revisions: int,
    canary_publication_items: int,
    canary_publication_revisions: int,
    restored_publication_items: int,
    restored_publication_revisions: int,
    baseline_restored: bool,
    kill_switch_denial_verified: bool,
    second_brain_gate_green: bool,
) -> dict[str, object]:
    counts = (
        backup_file_count,
        baseline_publication_items,
        baseline_publication_revisions,
        canary_publication_items,
        canary_publication_revisions,
        restored_publication_items,
        restored_publication_revisions,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("pilot report counts must be nonnegative integers")
    if (
        baseline_restored is not True
        or kill_switch_denial_verified is not True
        or second_brain_gate_green is not True
    ):
        raise ValueError("pilot report requires successful recovery and Second Brain gate")
    if backup_file_count < 1:
        raise ValueError("pilot report requires a nonempty verified backup")
    if (
        restored_publication_items != baseline_publication_items
        or restored_publication_revisions != baseline_publication_revisions
        or canary_publication_items != baseline_publication_items + 1
        or canary_publication_revisions != baseline_publication_revisions + 1
    ):
        raise ValueError("pilot report publication counts do not prove recovery")
    return {
        "backup": {
            "copy_on_write": True,
            "file_count": backup_file_count,
            "verified": True,
        },
        "contract": REPORT_CONTRACT,
        "default_off": True,
        "kill_switch_exercised": True,
        "live_local_data_accessed": False,
        "opt_in": True,
        "publication_counts": {
            "baseline_items": baseline_publication_items,
            "baseline_revisions": baseline_publication_revisions,
            "canary_items": canary_publication_items,
            "canary_revisions": canary_publication_revisions,
            "restored_items": restored_publication_items,
            "restored_revisions": restored_publication_revisions,
        },
        "recovery": {
            "baseline_restored": True,
            "changed_root_preserved": True,
            "new_root": True,
            "verified": True,
        },
        "second_brain_gate_green": True,
        "synthetic_only": True,
    }
