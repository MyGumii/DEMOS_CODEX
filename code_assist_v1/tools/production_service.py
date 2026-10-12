"""P7.5 production-service preflight, WinSW rendering, run, and stop CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET
from typing import Callable, Mapping

from code_assist_v1.knowledge_core.production_config import (
    ProductionConfigError,
    ProductionDeploymentConfig,
    SERVICE_ROWS,
    WINSW_SHA256,
    canonical_json_bytes,
    parse_production_config,
)
from code_assist_v1.knowledge_core.production_preflight import (
    build_production_preflight_report,
)
from code_assist_v1.knowledge_core.production_service import (
    ProductionServiceError,
    WindowsStopEvent,
    apply_production_environment,
    parse_protected_environment,
    run_production_service,
    stop_event_name,
)


_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_ENV_BYTES = 1024 * 1024
_EXIT_CONFIG = 2
_EXIT_RUNTIME = 3
_EXIT_WRITE = 4
_ERROR_MESSAGES = {
    "CONFIG_INVALID": "The production service manifest is invalid or changed.",
    "PREFLIGHT_FAILED": "The production service preflight did not pass.",
    "RENDER_FAILED": "The WinSW service package could not be rendered safely.",
    "SERVICE_FAILED": "The production service stopped with a runtime failure.",
    "STOP_FAILED": "The production service stop signal could not be delivered.",
}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ProductionConfigError("The production service arguments are invalid.")


def _canonical_output(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _error(code: str) -> dict[str, object]:
    return {
        "code": code,
        "message": _ERROR_MESSAGES[code],
        "status": "error",
    }


def _read_small_file(path: str | os.PathLike[str], maximum: int) -> bytes:
    candidate = Path(path)
    facts_before = candidate.stat(follow_symlinks=False)
    if not candidate.is_file() or facts_before.st_size < 1 or facts_before.st_size > maximum:
        raise ProductionConfigError("A production service input has an invalid size.")
    attributes = int(getattr(facts_before, "st_file_attributes", 0))
    if attributes & 0x400 or int(facts_before.st_nlink) != 1:
        raise ProductionConfigError("A production service input is not a regular file.")
    raw = candidate.read_bytes()
    facts_after = candidate.stat(follow_symlinks=False)
    if (
        (int(facts_before.st_dev), int(facts_before.st_ino), int(facts_before.st_size))
        != (int(facts_after.st_dev), int(facts_after.st_ino), int(facts_after.st_size))
        or len(raw) != int(facts_before.st_size)
    ):
        raise ProductionConfigError("A production service input changed while read.")
    return raw


def _load(
    config_path: str,
    expected_sha256: str,
) -> ProductionDeploymentConfig:
    raw = _read_small_file(config_path, _MAX_CONFIG_BYTES)
    return parse_production_config(raw, expected_document_sha256=expected_sha256)


def _load_environment(config: ProductionDeploymentConfig) -> dict[str, str]:
    raw = _read_small_file(config.paths.environment_file, _MAX_ENV_BYTES)
    return parse_protected_environment(raw)


def _service(service_id: str) -> dict[str, object]:
    matches = [dict(row) for row in SERVICE_ROWS if row["service_id"] == service_id]
    if len(matches) != 1:
        raise ProductionConfigError("The requested service identity is unknown.")
    return matches[0]


def _config_document_sha256(config_path: str) -> str:
    return hashlib.sha256(_read_small_file(config_path, _MAX_CONFIG_BYTES)).hexdigest()


def render_winsw_xml(
    config: ProductionDeploymentConfig,
    *,
    config_path: str,
    config_document_sha256: str,
    service_id: str,
) -> bytes:
    service = _service(service_id)
    if config_document_sha256 != _config_document_sha256(config_path):
        raise ProductionConfigError("The manifest document changed before rendering.")
    root = ET.Element("service")
    ET.SubElement(root, "id").text = service_id
    ET.SubElement(root, "name").text = str(service["display_name"])
    ET.SubElement(root, "description").text = (
        "Code Assist v1 reviewed production service (P7.5)."
    )
    ET.SubElement(root, "executable").text = config.paths.python_executable
    quoted_config = f'"{config_path}"'
    common = (
        "-E -m code_assist_v1.tools.production_service "
        f"--config {quoted_config} --config-sha256 {config_document_sha256} "
        f"--service {service_id}"
    )
    ET.SubElement(root, "startarguments").text = common + " run"
    if service["application"] != "deployment-migrator":
        ET.SubElement(root, "stopexecutable").text = config.paths.python_executable
        ET.SubElement(root, "stoparguments").text = common + " stop"
    ET.SubElement(root, "workingdirectory").text = config.paths.working_directory
    ET.SubElement(root, "priority").text = "Normal"
    ET.SubElement(root, "stoptimeout").text = "45 sec"
    ET.SubElement(root, "stopparentprocessfirst").text = "false"
    ET.SubElement(root, "startmode").text = (
        "Manual" if service["application"] == "deployment-migrator" else "Automatic"
    )
    ET.SubElement(root, "waithint").text = "45 sec"
    ET.SubElement(root, "sleeptime").text = "1 sec"
    for dependency in service["depends_on"]:  # type: ignore[union-attr]
        ET.SubElement(root, "depend").text = str(dependency)
    if service["application"] != "deployment-migrator":
        ET.SubElement(root, "onfailure", {"action": "restart", "delay": "10 sec"})
        ET.SubElement(root, "onfailure", {"action": "restart", "delay": "30 sec"})
    ET.SubElement(root, "onfailure", {"action": "none"})
    ET.SubElement(root, "resetfailure").text = "1 hour"
    ET.SubElement(root, "logpath").text = ntpath.join(
        config.paths.log_root, service_id
    )
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = "10240"
    ET.SubElement(log, "keepFiles").text = "8"
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _existing_output_matches(path: Path, content: bytes) -> bool:
    if not os.path.lexists(path):
        return False
    try:
        facts_before = path.stat(follow_symlinks=False)
        attributes = int(getattr(facts_before, "st_file_attributes", 0))
        raw = path.read_bytes()
        facts_after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise OSError("service_package_drift") from exc
    if (
        not stat.S_ISREG(facts_before.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        or int(facts_before.st_nlink) != 1
        or (int(facts_before.st_dev), int(facts_before.st_ino), int(facts_before.st_size))
        != (int(facts_after.st_dev), int(facts_after.st_ino), int(facts_after.st_size))
        or raw != content
    ):
        raise OSError("service_package_drift")
    return True


def _exact_write(path: Path, content: bytes) -> None:
    if _existing_output_matches(path, content):
        return
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if _existing_output_matches(path, content):
            return
        raise


def render_service_package(
    config: ProductionDeploymentConfig,
    *,
    config_path: str,
    config_document_sha256: str,
    volume_probe: Callable[[str | os.PathLike[str]], Mapping[str, object]] | None = None,
) -> tuple[str, ...]:
    if volume_probe is None:
        from code_assist_v1.tools.windows_volume import probe_path

        volume_probe = probe_path
    service_directory = Path(config.paths.service_wrapper_executable).parent
    wrapper = Path(config.paths.service_wrapper_executable)
    database = Path(config.paths.database)
    checked_paths: tuple[tuple[Path, str], ...] = (
        (Path(config.paths.backup_root), "directory"),
        (Path(config.paths.blob_root), "directory"),
        (Path(config_path), "file"),
        (Path(config.paths.data_root), "directory"),
        (
            database if os.path.lexists(database) else database.parent,
            "file" if os.path.lexists(database) else "directory",
        ),
        (Path(config.paths.environment_file), "file"),
        (Path(config.paths.log_root), "directory"),
        (Path(config.paths.part_write_rollout_record), "file"),
        (Path(config.paths.projection_root), "directory"),
        (Path(config.paths.python_executable), "file"),
        (Path(config.paths.runtime_lock), "file"),
        (service_directory, "directory"),
        (wrapper, "file"),
        (Path(config.paths.working_directory), "directory"),
    )
    for path, kind in checked_paths:
        try:
            observed = volume_probe(path)
            volume = observed.get("volume")
            reparse = observed.get("reparse")
            resolved = observed.get("resolved_path")
            facts = path.stat(follow_symlinks=False)
        except Exception as exc:
            raise OSError("service_package_storage_ineligible") from exc
        if (
            observed.get("eligible") is not True
            or observed.get("exists") is not True
            or not isinstance(volume, Mapping)
            or volume.get("drive_type_code") != 3
            or str(volume.get("filesystem", "")).casefold() != "ntfs"
            or not isinstance(reparse, Mapping)
            or reparse.get("present") is not False
            or not isinstance(resolved, str)
            or ntpath.normcase(ntpath.normpath(resolved))
            != ntpath.normcase(ntpath.normpath(os.fspath(path)))
            or (kind == "directory" and not path.is_dir())
            or (kind == "file" and (not path.is_file() or int(facts.st_nlink) != 1))
        ):
            raise OSError("service_package_storage_ineligible")
    if hashlib.sha256(wrapper.read_bytes()).hexdigest() != WINSW_SHA256:
        raise OSError("service_wrapper_hash_mismatch")
    rendered = {
        str(row["service_id"]): render_winsw_xml(
            config,
            config_path=config_path,
            config_document_sha256=config_document_sha256,
            service_id=str(row["service_id"]),
        )
        for row in SERVICE_ROWS
    }
    wrapper_bytes = wrapper.read_bytes()
    if hashlib.sha256(wrapper_bytes).hexdigest() != WINSW_SHA256:
        raise OSError("service_wrapper_changed")
    artifacts: list[tuple[Path, bytes]] = []
    for row in SERVICE_ROWS:
        service_id = str(row["service_id"])
        wrapper_copy = service_directory / f"{service_id}.exe"
        xml_path = service_directory / f"{service_id}.xml"
        artifacts.extend(
            ((wrapper_copy, wrapper_bytes), (xml_path, rendered[service_id]))
        )
    for path, content in artifacts:
        _existing_output_matches(path, content)
    for path, content in artifacts:
        _exact_write(path, content)
    outputs = [os.fspath(path) for path, _content in artifacts]
    return tuple(outputs)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Run reviewed P7.5 production services.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument(
        "--service",
        required=True,
        choices=tuple(str(row["service_id"]) for row in SERVICE_ROWS),
    )
    parser.add_argument("command", choices=("preflight", "render", "run", "stop"))
    return parser


def main(argv: list[str] | None = None) -> int:
    requested_command: str | None = None
    try:
        args = _parser().parse_args(argv)
        requested_command = args.command
        config = _load(args.config, args.config_sha256)
        if args.command == "stop":
            WindowsStopEvent.signal_existing(
                stop_event_name(config.deployment_id, args.service)
            )
            return 0
        if args.command == "render":
            outputs = render_service_package(
                config,
                config_path=args.config,
                config_document_sha256=args.config_sha256,
            )
            sys.stdout.write(
                _canonical_output(
                    {
                        "config_sha256": config.config_sha256,
                        "output_count": len(outputs),
                        "status": "rendered",
                    }
                )
                + "\n"
            )
            return 0
        values = _load_environment(config)
        apply_production_environment(
            config,
            service_id=args.service,
            values=values,
        )
        report, preflight_exit = build_production_preflight_report(
            config,
            config_path=args.config,
            service_id=args.service,
            allow_missing_database=args.service == "CodeAssist-Migrator",
        )
        if args.command == "preflight":
            sys.stdout.write(_canonical_output(report) + "\n")
            return preflight_exit
        if preflight_exit != 0:
            sys.stdout.write(_canonical_output(_error("PREFLIGHT_FAILED")) + "\n")
            return _EXIT_RUNTIME
        name = stop_event_name(config.deployment_id, args.service)
        with WindowsStopEvent.create(name) as stop:
            run_production_service(config, service_id=args.service, stop=stop)
        return 0
    except (ProductionConfigError, argparse.ArgumentError):
        sys.stdout.write(_canonical_output(_error("CONFIG_INVALID")) + "\n")
        return _EXIT_CONFIG
    except OSError:
        sys.stdout.write(_canonical_output(_error("RENDER_FAILED")) + "\n")
        return _EXIT_WRITE
    except ProductionServiceError:
        code = "STOP_FAILED" if requested_command == "stop" else "SERVICE_FAILED"
        sys.stdout.write(_canonical_output(_error(code)) + "\n")
        return _EXIT_RUNTIME
    except Exception:
        sys.stdout.write(_canonical_output(_error("SERVICE_FAILED")) + "\n")
        return _EXIT_RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
