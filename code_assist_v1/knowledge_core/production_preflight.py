"""P7.5 deployment/service readiness over the exact reviewed manifest."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from ctypes import wintypes
import hashlib
import ntpath
import os
from pathlib import Path
import stat
import sys
from typing import Any
import unicodedata

from .production_config import ProductionDeploymentConfig, SERVICE_ROWS, WINSW_SHA256


PRODUCTION_PREFLIGHT_SCHEMA = "code-assist.production-service-preflight"


class ProductionPreflightError(RuntimeError):
    code = "PRODUCTION_PREFLIGHT_FAILED"


def _path_key(value: str | os.PathLike[str]) -> str:
    return ntpath.normcase(ntpath.normpath(os.fspath(value).replace("/", "\\")))


def _same_or_descendant(candidate: str, parent: str) -> bool:
    try:
        return ntpath.commonpath((_path_key(candidate), _path_key(parent))) == _path_key(
            parent
        )
    except ValueError:
        return False


def _sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def windows_current_account() -> str:
    if sys.platform != "win32":
        raise ProductionPreflightError("Production service identity requires Windows.")
    name_sam_compatible = 2
    secur32 = ctypes.WinDLL("secur32", use_last_error=True)
    get_name = secur32.GetUserNameExW
    get_name.argtypes = (ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG))
    get_name.restype = wintypes.BOOL
    size = wintypes.ULONG(0)
    get_name(name_sam_compatible, None, ctypes.byref(size))
    if size.value < 2 or size.value > 1024:
        raise ProductionPreflightError("The Windows service identity is unavailable.")
    buffer = ctypes.create_unicode_buffer(size.value)
    if not get_name(name_sam_compatible, buffer, ctypes.byref(size)):
        raise ProductionPreflightError("The Windows service identity is unavailable.")
    value = buffer.value
    if not value:
        raise ProductionPreflightError("The Windows service identity is empty.")
    return value


def _account_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _path_roles(
    config: ProductionDeploymentConfig,
    config_path: str | os.PathLike[str],
    service_id: str,
    *,
    allow_missing_database: bool,
) -> tuple[tuple[str, str, str], ...]:
    paths = config.paths
    active_wrapper = ntpath.join(
        ntpath.dirname(paths.service_wrapper_executable), f"{service_id}.exe"
    )
    database_role = (
        ("database_parent", ntpath.dirname(paths.database), "directory")
        if allow_missing_database and not os.path.lexists(paths.database)
        else ("database", paths.database, "file")
    )
    return (
        ("active_service_wrapper", active_wrapper, "file"),
        ("backup_root", paths.backup_root, "directory"),
        ("blob_root", paths.blob_root, "directory"),
        ("config", os.fspath(config_path), "file"),
        ("data_root", paths.data_root, "directory"),
        database_role,
        ("environment_file", paths.environment_file, "file"),
        ("log_root", paths.log_root, "directory"),
        ("part_write_rollout_record", paths.part_write_rollout_record, "file"),
        ("projection_root", paths.projection_root, "directory"),
        ("python_executable", paths.python_executable, "file"),
        ("runtime_lock", paths.runtime_lock, "file"),
        ("service_wrapper_executable", paths.service_wrapper_executable, "file"),
        ("working_directory", paths.working_directory, "directory"),
    )


def build_production_preflight_report(
    config: ProductionDeploymentConfig,
    *,
    config_path: str | os.PathLike[str],
    service_id: str,
    allow_missing_database: bool = False,
    runtime_report_builder: Callable[..., tuple[Mapping[str, Any], int]] | None = None,
    volume_probe: Callable[[str | os.PathLike[str]], Mapping[str, Any]] | None = None,
    account_provider: Callable[[], str] = windows_current_account,
    hash_provider: Callable[[str | os.PathLike[str]], str] = _sha256_file,
    cwd: str | os.PathLike[str] | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], int]:
    if not isinstance(config, ProductionDeploymentConfig):
        raise TypeError("config must be ProductionDeploymentConfig.")
    if service_id not in {str(row["service_id"]) for row in SERVICE_ROWS}:
        raise ValueError("service_id is not a reviewed production service.")
    if allow_missing_database and service_id != "CodeAssist-Migrator":
        raise ValueError("Only the deployment migrator may start without a database.")
    if runtime_report_builder is None:
        from ..tools.runtime_preflight import build_report

        runtime_report_builder = build_report
    if volume_probe is None:
        from ..tools.windows_volume import probe_path

        volume_probe = probe_path
    reasons: list[str] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    config_path_value = os.fspath(config_path)
    if not ntpath.isabs(config_path_value):
        add("config_path_not_absolute")
    paths_report: list[dict[str, object]] = []
    for role, path, expected_kind in _path_roles(
        config,
        config_path_value,
        service_id,
        allow_missing_database=allow_missing_database,
    ):
        try:
            observed = volume_probe(path)
        except Exception:
            observed = {}
        volume = observed.get("volume") if isinstance(observed, Mapping) else None
        reparse = observed.get("reparse") if isinstance(observed, Mapping) else None
        resolved = observed.get("resolved_path") if isinstance(observed, Mapping) else None
        eligible = (
            isinstance(observed, Mapping)
            and observed.get("eligible") is True
            and observed.get("exists") is True
            and isinstance(volume, Mapping)
            and volume.get("drive_type_code") == 3
            and isinstance(volume.get("filesystem"), str)
            and str(volume["filesystem"]).casefold() == "ntfs"
            and isinstance(reparse, Mapping)
            and reparse.get("present") is False
            and isinstance(resolved, str)
            and _path_key(resolved) == _path_key(path)
        )
        kind_matches = False
        single_link = False
        if eligible:
            try:
                facts = os.stat(path, follow_symlinks=False)
                kind_matches = (
                    stat.S_ISDIR(facts.st_mode)
                    if expected_kind == "directory"
                    else stat.S_ISREG(facts.st_mode)
                )
                attributes = int(getattr(facts, "st_file_attributes", 0))
                if attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    eligible = False
                single_link = expected_kind == "directory" or int(facts.st_nlink) == 1
            except (OSError, TypeError, ValueError):
                eligible = False
        paths_report.append(
            {
                "eligible": eligible and kind_matches and single_link,
                "kind": expected_kind,
                "role": role,
                "single_link": single_link,
            }
        )
        if not eligible:
            add(f"{role}_storage_ineligible")
        elif not kind_matches:
            add(f"{role}_kind_mismatch")
        elif not single_link:
            add(f"{role}_hardlink_rejected")

    config_parent = ntpath.dirname(config_path_value)
    if not _same_or_descendant(config_parent, config.paths.working_directory):
        add("config_outside_working_directory")
    if not _same_or_descendant(
        config.paths.environment_file, config.paths.working_directory
    ):
        add("environment_outside_working_directory")
    if not _same_or_descendant(
        config.paths.service_wrapper_executable, config.paths.working_directory
    ):
        add("wrapper_outside_working_directory")

    effective_cwd = os.getcwd() if cwd is None else os.fspath(cwd)
    effective_executable = sys.executable if executable is None else os.fspath(executable)
    if _path_key(effective_cwd) != _path_key(config.paths.working_directory):
        add("working_directory_mismatch")
    if _path_key(effective_executable) != _path_key(config.paths.python_executable):
        add("python_executable_mismatch")
    try:
        current_account = account_provider()
    except Exception:
        current_account = None
        add("service_account_unavailable")
    if not isinstance(current_account, str) or _account_key(current_account) != _account_key(
        config.service_account
    ):
        add("service_account_mismatch")
    try:
        wrapper_hash = hash_provider(config.paths.service_wrapper_executable)
    except Exception:
        wrapper_hash = None
        add("wrapper_hash_unavailable")
    if wrapper_hash != WINSW_SHA256:
        add("wrapper_hash_mismatch")
    active_wrapper = ntpath.join(
        ntpath.dirname(config.paths.service_wrapper_executable), f"{service_id}.exe"
    )
    try:
        active_wrapper_hash = hash_provider(active_wrapper)
    except Exception:
        active_wrapper_hash = None
        add("active_wrapper_hash_unavailable")
    if active_wrapper_hash != WINSW_SHA256:
        add("active_wrapper_hash_mismatch")

    database_missing_for_migrator = (
        allow_missing_database and not os.path.lexists(config.paths.database)
    )
    if database_missing_for_migrator:
        runtime_report: Mapping[str, Any] = {
            "gate": {
                "status": "deferred_to_single_migrator",
            }
        }
    else:
        try:
            runtime_report, runtime_exit = runtime_report_builder(
                "production",
                data_root=config.paths.data_root,
                database=config.paths.database,
                blob_root=config.paths.blob_root,
                projection_root=config.paths.projection_root,
                runtime_lock=config.paths.runtime_lock,
                part_write_rollout_record=config.paths.part_write_rollout_record,
            )
        except Exception:
            runtime_report = {"gate": {"status": "fail"}}
            runtime_exit = 2
        runtime_gate = (
            runtime_report.get("gate")
            if isinstance(runtime_report, Mapping)
            else None
        )
        if (
            runtime_exit != 0
            or not isinstance(runtime_gate, Mapping)
            or runtime_gate.get("status") != "pass"
        ):
            add("runtime_preflight_failed")

    exit_code = 0 if not reasons else 2
    report = {
        "config_sha256": config.config_sha256,
        "gate": {
            "exit_code": exit_code,
            "reasons": reasons,
            "status": "pass" if exit_code == 0 else "fail",
        },
        "identity": {
            "account_match": (
                isinstance(current_account, str)
                and _account_key(current_account) == _account_key(config.service_account)
            )
        },
        "paths": paths_report,
        "report_schema": PRODUCTION_PREFLIGHT_SCHEMA,
        "service_id": service_id,
        "runtime": runtime_report,
        "wrapper": {
            "active_sha256_match": active_wrapper_hash == WINSW_SHA256,
            "sha256_match": wrapper_hash == WINSW_SHA256,
        },
    }
    return report, exit_code


__all__ = [
    "PRODUCTION_PREFLIGHT_SCHEMA",
    "ProductionPreflightError",
    "build_production_preflight_report",
    "windows_current_account",
]
