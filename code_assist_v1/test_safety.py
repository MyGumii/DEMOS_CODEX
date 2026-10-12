"""Test-only filesystem isolation guards.

This module deliberately depends only on the Python standard library.  In
particular, it must remain safe to import before :mod:`code_assist_v1.config` or
either Flask application, because those modules bind or initialize live paths at
import time.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Final


class UnsafeTestDataPathError(RuntimeError):
    """Raised when a test path could overlap mutable application data."""


_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})
_USER_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Resolve the code locations once without importing application configuration.
_PACKAGE_ROOT: Final = Path(__file__).resolve().parent
_WORKSPACE_ROOT: Final = _PACKAGE_ROOT.parent

# Keep the individual live roots explicit even though the broader package and
# workspace guards also cover them.  This prevents a future relocation or a
# narrowed workspace policy from accidentally removing the data-specific guard.
_PROTECTED_ROOTS: Final = (
    _WORKSPACE_ROOT,
    _PACKAGE_ROOT,
    _PACKAGE_ROOT / ".runtime",
    _PACKAGE_ROOT / "user_data",
    _PACKAGE_ROOT / "sessions",
    _PACKAGE_ROOT / "workspace",
    _PACKAGE_ROOT / "knowledge",
    _WORKSPACE_ROOT / "knowledge",
)


@dataclass(frozen=True, slots=True)
class IsolatedTestLayout:
    """Filesystem paths created for one isolated test data root."""

    root: Path
    runtime_dir: Path
    knowledge_dir: Path
    user_data_dir: Path
    sessions_dir: Path
    workspace_dir: Path
    core_db_parent: Path
    core_db_path: Path
    blobs_dir: Path
    projections_dir: Path
    notion_dir: Path


def _resolve(path: Path, *, label: str) -> Path:
    """Resolve existing symlinks/reparse points and tolerate a missing leaf."""

    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeTestDataPathError(f"Cannot resolve {label}: {path!s}") from exc


def _same_or_below(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    """Return true when either resolved path contains the other."""

    return _same_or_below(left, right) or _same_or_below(right, left)


def _is_filesystem_root(path: Path) -> bool:
    """Reject a drive/share root itself without rejecting every descendant."""

    anchor = path.anchor
    return bool(anchor) and path == Path(anchor)


def _require_absolute(path: str | os.PathLike[str], *, label: str) -> Path:
    try:
        parsed = Path(path).expanduser()
    except (OSError, TypeError, ValueError) as exc:
        raise UnsafeTestDataPathError(f"Invalid {label}") from exc
    if not parsed.is_absolute():
        raise UnsafeTestDataPathError(f"{label} must be an absolute path: {parsed!s}")
    return parsed


def _assert_not_protected(path: Path, *, label: str) -> None:
    if _is_filesystem_root(path):
        raise UnsafeTestDataPathError(f"{label} cannot be a filesystem root: {path!s}")

    for raw_protected in _PROTECTED_ROOTS:
        protected = _resolve(Path(raw_protected), label="protected root")
        if _overlaps(path, protected):
            raise UnsafeTestDataPathError(
                f"{label} overlaps a protected workspace, package, or live-data root: {path!s}"
            )


def assert_isolated_test_path(
    path: str | os.PathLike[str],
    test_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Validate and return a resolved path inside the isolated test root.

    ``CODE_ASSIST_TEST_MODE`` must be explicitly truthy.  ``test_root`` takes
    precedence over ``CODE_ASSIST_TEST_DATA_ROOT``; either root must be absolute.
    Both the candidate and root are resolved before containment/protected-root
    checks so an existing symlink or Windows reparse point cannot escape the
    isolated tree.
    """

    if os.environ.get("CODE_ASSIST_TEST_MODE", "").strip().lower() not in _TRUTHY:
        raise UnsafeTestDataPathError("CODE_ASSIST_TEST_MODE must be explicitly enabled")

    selected_root = test_root
    if selected_root is None:
        selected_root = os.environ.get("CODE_ASSIST_TEST_DATA_ROOT")
    if selected_root is None or not str(selected_root).strip():
        raise UnsafeTestDataPathError(
            "An absolute CODE_ASSIST_TEST_DATA_ROOT or explicit test_root is required"
        )

    raw_root = _require_absolute(selected_root, label="test root")
    raw_candidate = _require_absolute(path, label="candidate test path")
    resolved_root = _resolve(raw_root, label="test root")
    resolved_candidate = _resolve(raw_candidate, label="candidate test path")

    _assert_not_protected(resolved_root, label="test root")
    _assert_not_protected(resolved_candidate, label="candidate test path")

    if not _same_or_below(resolved_candidate, resolved_root):
        raise UnsafeTestDataPathError(
            f"Candidate must be the isolated root or one of its descendants: {resolved_candidate!s}"
        )

    return resolved_candidate


def create_isolated_test_layout(
    root: str | os.PathLike[str],
    user_id: str = "fixture-user",
) -> IsolatedTestLayout:
    """Create the standard isolated test directory tree under ``root``.

    The core database file itself is not created; only its guarded parent is.
    Every directory target is validated before the first filesystem mutation so
    a pre-existing symlink/reparse point cannot redirect creation into live data.
    """

    if not _USER_ID_RE.fullmatch(str(user_id or "")):
        raise UnsafeTestDataPathError("user_id is not a safe fixture identifier")

    resolved_root = assert_isolated_test_path(root, test_root=root)
    if resolved_root.exists() and not resolved_root.is_dir():
        raise UnsafeTestDataPathError(f"Test root is not a directory: {resolved_root!s}")

    layout = IsolatedTestLayout(
        root=resolved_root,
        runtime_dir=resolved_root / ".runtime",
        knowledge_dir=resolved_root / "knowledge",
        user_data_dir=resolved_root / "user_data",
        sessions_dir=resolved_root / "sessions",
        workspace_dir=resolved_root / "workspace",
        core_db_parent=resolved_root / "core",
        core_db_path=resolved_root / "core" / "knowledge-core.db",
        blobs_dir=resolved_root / "blobs",
        projections_dir=resolved_root / "projections",
        notion_dir=resolved_root / "user_data" / user_id / "notion",
    )

    directories = (
        layout.root,
        layout.runtime_dir,
        layout.knowledge_dir,
        layout.user_data_dir,
        layout.sessions_dir,
        layout.workspace_dir,
        layout.core_db_parent,
        layout.blobs_dir,
        layout.projections_dir,
        layout.notion_dir,
    )
    for directory in directories:
        assert_isolated_test_path(directory, test_root=resolved_root)
    assert_isolated_test_path(layout.core_db_path, test_root=resolved_root)

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    return layout


__all__ = [
    "IsolatedTestLayout",
    "UnsafeTestDataPathError",
    "assert_isolated_test_path",
    "create_isolated_test_layout",
]
