"""Exact P7.5 production-service deployment manifest.

The manifest is intentionally configuration, not authority to weaken runtime
checks.  Every server/wrapper/checkpoint/start-order value is compared with the
reviewed constants below.  Target path and ACL observations are performed by
the deployment preflight immediately before installation/start.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import ntpath
import re
from types import MappingProxyType
from typing import Any, Mapping

from .production_wsgi import (
    DEFAULT_BACKLOG,
    DEFAULT_CHANNEL_TIMEOUT_SECONDS,
    DEFAULT_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CONNECTION_LIMIT,
    DEFAULT_DRAIN_TIMEOUT_SECONDS,
    DEFAULT_THREADS,
    WAITRESS_DISTRIBUTION,
    WAITRESS_VERSION,
)


PRODUCTION_CONFIG_SCHEMA = "code-assist.production-services"
PRODUCTION_CONFIG_VERSION = 1
WINSW_VERSION = "2.12.0"
WINSW_ASSET = "WinSW-x64.exe"
WINSW_SHA256 = "05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da"
CHECKPOINT_MODE = "PASSIVE"
CHECKPOINT_INTERVAL_SECONDS = 300
CODE_PORT = 10011
NOTION_PORT = 10012
ACL_POLICY = MappingProxyType(
    {
        "inheritance": "removed",
        "immutable_working_tree": "service_read_execute",
        "mutable_data_log_backup_roots": "service_modify",
        "protected_config_environment_files": "service_read",
        "system_and_administrators": "full_control",
    }
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DEPLOYMENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,39}$", re.ASCII)
_SERVICE_ACCOUNT_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,62})\\"
    r"(?:[A-Za-z0-9][A-Za-z0-9_.@$-]{1,63})$",
    re.ASCII,
)
_BUILTIN_ACCOUNTS = frozenset(
    {
        ".\\administrator",
        ".\\guest",
        "builtin\\administrators",
        "nt authority\\local service",
        "nt authority\\network service",
        "nt authority\\system",
    }
)

_PATH_KEYS = (
    "backup_root",
    "blob_root",
    "data_root",
    "database",
    "environment_file",
    "log_root",
    "part_write_rollout_record",
    "projection_root",
    "python_executable",
    "runtime_lock",
    "service_wrapper_executable",
    "working_directory",
)

_SERVICE_ROWS = (
    {
        "application": "deployment-migrator",
        "checkpoint_coordinator": False,
        "depends_on": [],
        "display_name": "CodeAssist Deployment Migrator",
        "process_role": "migrator",
        "service_id": "CodeAssist-Migrator",
    },
    {
        "application": "code",
        "checkpoint_coordinator": False,
        "depends_on": [],
        "display_name": "CodeAssist Web",
        "process_role": "web",
        "service_id": "CodeAssist-Web",
    },
    {
        "application": "notion",
        "checkpoint_coordinator": False,
        "depends_on": [],
        "display_name": "CodeAssist Notion",
        "process_role": "web",
        "service_id": "CodeAssist-Notion",
    },
    {
        "application": "durable-worker",
        "checkpoint_coordinator": False,
        "depends_on": ["CodeAssist-Web", "CodeAssist-Notion"],
        "display_name": "CodeAssist Durable Worker",
        "process_role": "worker",
        "service_id": "CodeAssist-DurableWorker",
    },
    {
        "application": "projector",
        "checkpoint_coordinator": True,
        "depends_on": ["CodeAssist-Web", "CodeAssist-Notion"],
        "display_name": "CodeAssist Projector",
        "process_role": "projector",
        "service_id": "CodeAssist-Projector",
    },
)
SERVICE_ROWS = tuple(MappingProxyType(dict(row)) for row in _SERVICE_ROWS)
STARTUP_ORDER = (
    "CodeAssist-Migrator",
    "CodeAssist-Web",
    "CodeAssist-Notion",
    "CodeAssist-DurableWorker",
    "CodeAssist-Projector",
)


class ProductionConfigError(ValueError):
    code = "PRODUCTION_CONFIG_INVALID"


def canonical_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ProductionConfigError(
            "The production manifest is not canonical JSON data."
        ) from exc


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ProductionConfigError(
                "The production manifest contains a duplicate JSON member."
            )
        value[key] = item
    return value


def production_config_sha256(value: Mapping[str, object]) -> str:
    payload = dict(value)
    payload.pop("config_sha256", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _exact_keys(value: object, keys: tuple[str, ...] | set[str]) -> bool:
    return isinstance(value, Mapping) and set(value) == set(keys)


def _positive_integer(value: object, *, maximum: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > 0
        and (maximum is None or value <= maximum)
    )


def _windows_absolute_local_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value != ntpath.normpath(value):
        return False
    folded = value.replace("/", "\\").casefold()
    if folded.startswith(("\\\\", "\\?\\", "\\.\\", "\\??\\")):
        return False
    drive, tail = ntpath.splitdrive(value)
    return bool(
        re.fullmatch(r"[A-Za-z]:", drive)
        and tail.startswith("\\")
        and value not in {drive + "\\", drive + "/"}
    )


def _same_or_descendant(candidate: str, parent: str) -> bool:
    candidate_key = ntpath.normcase(ntpath.normpath(candidate))
    parent_key = ntpath.normcase(ntpath.normpath(parent))
    try:
        return ntpath.commonpath((candidate_key, parent_key)) == parent_key
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ProductionPaths:
    backup_root: str
    blob_root: str
    data_root: str
    database: str
    environment_file: str
    log_root: str
    part_write_rollout_record: str
    projection_root: str
    python_executable: str
    runtime_lock: str
    service_wrapper_executable: str
    working_directory: str

    def __post_init__(self) -> None:
        for name in _PATH_KEYS:
            if not _windows_absolute_local_path(getattr(self, name)):
                raise ProductionConfigError(
                    f"paths.{name} must be a normalized absolute local Windows path."
                )
        data_root = self.data_root
        for child in (self.database, self.blob_root, self.projection_root):
            if not _same_or_descendant(child, data_root) or ntpath.normcase(
                child
            ) == ntpath.normcase(data_root):
                raise ProductionConfigError(
                    "Canonical database, blob, and projection paths must be below data_root."
                )
        if _same_or_descendant(self.database, self.blob_root) or _same_or_descendant(
            self.database, self.projection_root
        ):
            raise ProductionConfigError(
                "The database cannot be inside a blob or projection root."
            )
        if _same_or_descendant(self.blob_root, self.projection_root) or (
            _same_or_descendant(self.projection_root, self.blob_root)
        ):
            raise ProductionConfigError(
                "The blob and projection roots cannot overlap."
            )
        isolated_roots = (self.data_root, self.log_root, self.backup_root)
        for index, left in enumerate(isolated_roots):
            for right in isolated_roots[index + 1 :]:
                if _same_or_descendant(left, right) or _same_or_descendant(right, left):
                    raise ProductionConfigError(
                        "Data, log, and backup roots must not overlap."
                    )
        for mutable_root in isolated_roots:
            if _same_or_descendant(self.working_directory, mutable_root) or (
                _same_or_descendant(mutable_root, self.working_directory)
            ):
                raise ProductionConfigError(
                    "The immutable working tree and mutable roots must not overlap."
                )
        for child in (
            self.environment_file,
            self.part_write_rollout_record,
            self.python_executable,
            self.runtime_lock,
            self.service_wrapper_executable,
        ):
            if not _same_or_descendant(child, self.working_directory) or ntpath.normcase(
                child
            ) == ntpath.normcase(self.working_directory):
                raise ProductionConfigError(
                    "Reviewed executable and configuration files must be below working_directory."
                )

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _PATH_KEYS}


@dataclass(frozen=True, slots=True)
class ProductionDeploymentConfig:
    deployment_id: str
    service_account: str
    paths: ProductionPaths
    bind_host: str
    code_port: int
    notion_port: int
    config_sha256: str

    def __post_init__(self) -> None:
        if _DEPLOYMENT_ID_RE.fullmatch(self.deployment_id) is None:
            raise ProductionConfigError("deployment_id has an invalid shape.")
        if (
            _SERVICE_ACCOUNT_RE.fullmatch(self.service_account) is None
            or self.service_account.casefold() in _BUILTIN_ACCOUNTS
            or self.service_account.casefold().endswith("\\administrator")
        ):
            raise ProductionConfigError(
                "service_account must be a dedicated non-built-in Windows account."
            )
        if self.bind_host not in {"127.0.0.1", "0.0.0.0"}:
            raise ProductionConfigError("network.bind_host is invalid.")
        if self.code_port == self.notion_port or not all(
            _positive_integer(port, maximum=65535)
            for port in (self.code_port, self.notion_port)
        ):
            raise ProductionConfigError("Production ports are invalid or collide.")
        if _SHA_RE.fullmatch(self.config_sha256) is None:
            raise ProductionConfigError("config_sha256 has an invalid shape.")

    def as_dict(self) -> dict[str, object]:
        return production_config_document(
            deployment_id=self.deployment_id,
            service_account=self.service_account,
            paths=self.paths,
            bind_host=self.bind_host,
            code_port=self.code_port,
            notion_port=self.notion_port,
        )


def production_config_document(
    *,
    deployment_id: str,
    service_account: str,
    paths: ProductionPaths,
    bind_host: str = "0.0.0.0",
    code_port: int = CODE_PORT,
    notion_port: int = NOTION_PORT,
) -> dict[str, object]:
    if not isinstance(paths, ProductionPaths):
        raise TypeError("paths must be ProductionPaths.")
    value: dict[str, object] = {
        "acl": dict(ACL_POLICY),
        "checkpoint": {
            "coordinator_service": "CodeAssist-Projector",
            "interval_seconds": CHECKPOINT_INTERVAL_SECONDS,
            "mode": CHECKPOINT_MODE,
        },
        "config_schema": PRODUCTION_CONFIG_SCHEMA,
        "deployment_id": deployment_id,
        "network": {
            "bind_host": bind_host,
            "code_port": code_port,
            "notion_port": notion_port,
        },
        "paths": paths.as_dict(),
        "schema_version": PRODUCTION_CONFIG_VERSION,
        "service_account": service_account,
        "services": [dict(row) for row in SERVICE_ROWS],
        "startup_order": list(STARTUP_ORDER),
        "wrapper": {
            "asset": WINSW_ASSET,
            "name": "WinSW",
            "sha256": WINSW_SHA256,
            "version": WINSW_VERSION,
        },
        "wsgi": {
            "backlog": DEFAULT_BACKLOG,
            "channel_timeout_seconds": DEFAULT_CHANNEL_TIMEOUT_SECONDS,
            "cleanup_interval_seconds": DEFAULT_CLEANUP_INTERVAL_SECONDS,
            "connection_limit": DEFAULT_CONNECTION_LIMIT,
            "distribution": WAITRESS_DISTRIBUTION,
            "drain_timeout_seconds": DEFAULT_DRAIN_TIMEOUT_SECONDS,
            "threads": DEFAULT_THREADS,
            "version": WAITRESS_VERSION,
        },
    }
    value["config_sha256"] = production_config_sha256(value)
    return value


def production_config_errors(value: object) -> tuple[str, ...]:
    errors: list[str] = []

    def add(code: str) -> None:
        if code not in errors:
            errors.append(code)

    top_keys = {
        "acl",
        "checkpoint",
        "config_schema",
        "config_sha256",
        "deployment_id",
        "network",
        "paths",
        "schema_version",
        "service_account",
        "services",
        "startup_order",
        "wrapper",
        "wsgi",
    }
    if not _exact_keys(value, top_keys):
        return ("top_level_shape",)
    assert isinstance(value, Mapping)
    if value.get("config_schema") != PRODUCTION_CONFIG_SCHEMA:
        add("config_schema")
    if value.get("schema_version") != PRODUCTION_CONFIG_VERSION:
        add("schema_version")
    if not isinstance(value.get("deployment_id"), str) or _DEPLOYMENT_ID_RE.fullmatch(
        str(value.get("deployment_id"))
    ) is None:
        add("deployment_id")
    account = value.get("service_account")
    if (
        not isinstance(account, str)
        or _SERVICE_ACCOUNT_RE.fullmatch(account) is None
        or account.casefold() in _BUILTIN_ACCOUNTS
        or account.casefold().endswith("\\administrator")
    ):
        add("service_account")

    paths_value = value.get("paths")
    if not _exact_keys(paths_value, set(_PATH_KEYS)):
        add("paths_shape")
        paths = None
    else:
        assert isinstance(paths_value, Mapping)
        try:
            paths = ProductionPaths(**{key: paths_value[key] for key in _PATH_KEYS})
        except (TypeError, ProductionConfigError):
            paths = None
            add("paths_binding")

    network = value.get("network")
    if not _exact_keys(network, {"bind_host", "code_port", "notion_port"}):
        add("network_shape")
    else:
        assert isinstance(network, Mapping)
        code_port = network.get("code_port")
        notion_port = network.get("notion_port")
        if (
            network.get("bind_host") not in {"127.0.0.1", "0.0.0.0"}
            or not _positive_integer(code_port, maximum=65535)
            or not _positive_integer(notion_port, maximum=65535)
            or code_port == notion_port
        ):
            add("network_binding")

    expected_wsgi = production_config_document(
        deployment_id="abc",
        service_account="DOMAIN\\service",
        paths=ProductionPaths(
            backup_root="C:\\backup",
            blob_root="C:\\data\\blobs",
            data_root="C:\\data",
            database="C:\\data\\core\\knowledge-core.db",
            environment_file="C:\\work\\config\\production.env",
            log_root="C:\\logs",
            part_write_rollout_record="C:\\work\\config\\rollout.json",
            projection_root="C:\\data\\projections",
            python_executable="C:\\work\\venv\\Scripts\\python.exe",
            runtime_lock="C:\\work\\requirements-runtime.lock",
            service_wrapper_executable="C:\\work\\service\\WinSW-x64.exe",
            working_directory="C:\\work",
        ),
    )
    for key in ("acl", "checkpoint", "services", "startup_order", "wrapper", "wsgi"):
        if value.get(key) != expected_wsgi[key]:
            add(f"{key}_binding")

    digest = value.get("config_sha256")
    if not isinstance(digest, str) or _SHA_RE.fullmatch(digest) is None:
        add("config_sha256_shape")
    else:
        try:
            bound_digest = production_config_sha256(value)
        except ProductionConfigError:
            add("config_json_shape")
        else:
            if bound_digest != digest:
                add("config_sha256_binding")
    del paths
    return tuple(errors)


def parse_production_config(
    raw: bytes,
    *,
    expected_document_sha256: str | None = None,
) -> ProductionDeploymentConfig:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes.")
    if expected_document_sha256 is not None and (
        _SHA_RE.fullmatch(expected_document_sha256) is None
        or hashlib.sha256(raw).hexdigest() != expected_document_sha256
    ):
        raise ProductionConfigError("The production manifest document hash differs.")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_members)
    except (UnicodeError, json.JSONDecodeError, ProductionConfigError) as exc:
        raise ProductionConfigError("The production manifest is invalid JSON.") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ProductionConfigError(
            "The production manifest must use canonical JSON plus one LF."
        )
    errors = production_config_errors(value)
    if errors:
        raise ProductionConfigError(
            "The production manifest violates its frozen contract: " + ",".join(errors)
        )
    path_values = value["paths"]
    network = value["network"]
    assert isinstance(path_values, dict) and isinstance(network, dict)
    paths = ProductionPaths(**{key: path_values[key] for key in _PATH_KEYS})
    return ProductionDeploymentConfig(
        deployment_id=str(value["deployment_id"]),
        service_account=str(value["service_account"]),
        paths=paths,
        bind_host=str(network["bind_host"]),
        code_port=int(network["code_port"]),
        notion_port=int(network["notion_port"]),
        config_sha256=str(value["config_sha256"]),
    )


__all__ = [
    "ACL_POLICY",
    "CHECKPOINT_INTERVAL_SECONDS",
    "CHECKPOINT_MODE",
    "CODE_PORT",
    "NOTION_PORT",
    "PRODUCTION_CONFIG_SCHEMA",
    "PRODUCTION_CONFIG_VERSION",
    "ProductionConfigError",
    "ProductionDeploymentConfig",
    "ProductionPaths",
    "SERVICE_ROWS",
    "STARTUP_ORDER",
    "WINSW_ASSET",
    "WINSW_SHA256",
    "WINSW_VERSION",
    "canonical_json_bytes",
    "parse_production_config",
    "production_config_document",
    "production_config_errors",
    "production_config_sha256",
]
