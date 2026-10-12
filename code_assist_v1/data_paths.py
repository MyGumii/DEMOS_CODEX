"""Pure resolution of mutable application data paths.

The resolver performs no filesystem writes and imports no application stores.
Callers must resolve paths before importing modules that copy configuration
constants at module scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


DATA_ROOT_ENV = "CODE_ASSIST_DATA_ROOT"


class DataRootConfigurationError(RuntimeError):
    """Raised when an explicit mutable-data root is unsafe or ambiguous."""


@dataclass(frozen=True)
class RuntimeDataPaths:
    """Resolved mutable roots used by the legacy applications during cutover."""

    data_root: str | None
    runtime_dir: str
    knowledge_dir: str
    sessions_dir: str
    workspace_dir: str
    user_data_dir: str
    override_active: bool


def _is_filesystem_root(path: str) -> bool:
    drive, _tail = os.path.splitdrive(path)
    anchor = drive + os.sep if drive else os.sep
    return os.path.normcase(os.path.normpath(path)) == os.path.normcase(
        os.path.normpath(anchor)
    )


def _explicit_root(raw: str) -> str:
    value = raw.strip()
    if not os.path.isabs(value):
        raise DataRootConfigurationError(
            f"{DATA_ROOT_ENV} must be an absolute path"
        )
    resolved = os.path.realpath(os.path.abspath(value))
    if _is_filesystem_root(resolved):
        raise DataRootConfigurationError(
            f"{DATA_ROOT_ENV} cannot be a filesystem root"
        )
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise DataRootConfigurationError(
            f"{DATA_ROOT_ENV} must be a directory path"
        )
    return resolved


def resolve_runtime_data_paths(
    *,
    package_dir: str,
    repository_root: str,
    standalone: bool,
    environ: Mapping[str, str] | None = None,
) -> RuntimeDataPaths:
    """Resolve legacy mutable roots with one optional all-data override.

    Without ``CODE_ASSIST_DATA_ROOT`` the historical layout is preserved.
    With it, every mutable root is a child of the one explicit absolute root,
    regardless of standalone/integrated mode.
    """

    env = os.environ if environ is None else environ
    raw_override = str(env.get(DATA_ROOT_ENV, "") or "").strip()
    if raw_override:
        data_root = _explicit_root(raw_override)
        return RuntimeDataPaths(
            data_root=data_root,
            runtime_dir=os.path.join(data_root, ".runtime"),
            knowledge_dir=os.path.join(data_root, "knowledge"),
            sessions_dir=os.path.join(data_root, "sessions"),
            workspace_dir=os.path.join(data_root, "workspace"),
            user_data_dir=os.path.join(data_root, "user_data"),
            override_active=True,
        )

    package_dir = os.path.abspath(package_dir)
    repository_root = os.path.abspath(repository_root)
    return RuntimeDataPaths(
        data_root=None,
        runtime_dir=os.path.join(package_dir, ".runtime"),
        knowledge_dir=os.path.join(
            package_dir if standalone else repository_root,
            "knowledge",
        ),
        sessions_dir=os.path.join(package_dir, "sessions"),
        workspace_dir=os.path.join(package_dir, "workspace"),
        user_data_dir=os.path.join(package_dir, "user_data"),
        override_active=False,
    )
