"""Approval-bound 10111/10112/10113 development realm launcher.

This module is deliberately separate from the D0A synthetic launcher.  It may
bind the operator development ports only when ``AP-DEV-PAIR`` is supplied,
imports only an exact active-human identity allowlist, creates a new session
secret/cookie namespace, and owns every child through exact ``Popen`` handles.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import site
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


APPROVAL_ID = "AP-DEV-PAIR"
AGENT_PORT = 10111
NOTION_PORT = 10112
GATEWAY_PORT = 10113
OPERATOR_PORTS = (AGENT_PORT, NOTION_PORT, GATEWAY_PORT)
LOOPBACK_HOST = "127.0.0.1"
ROOT_MARKER_NAME = ".code-assist-approved-dev-pair.json"
ACTIVE_RUN_NAME = "active-run.json"
STOP_REQUEST_NAME = "stop.request.json"
IDENTITY_FIELDS = frozenset(
    {
        "id",
        "display_name",
        "role",
        "disabled",
        "password_hash",
        "must_change_password",
        "password_changed_at",
        "created_at",
        "updated_at",
        "disabled_at",
    }
)
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$", re.ASCII)
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_HOST_ROLES = ("agent", "notion")


class ApprovedDevPairError(RuntimeError):
    """Fail-closed approved development realm error."""


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite number: {value}")
        ),
    )


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _path_locator(path: Path) -> str:
    return _sha256(os.path.normcase(str(path.resolve())).encode("utf-8"))


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ApprovedDevPairError("PATH_UNREADABLE") from exc
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_FLAG
    )


def _write_exclusive(path: Path, value: bytes) -> None:
    with path.open("xb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_exclusive(path, _canonical_bytes(value))


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_json_exclusive(temporary, value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK_HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_port(port: int, *, accepting: bool, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if _port_accepting(port) is accepting:
            return True
        time.sleep(0.03)
    return _port_accepting(port) is accepting


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _sanitized_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "LOCALAPPDATA",
        "APPDATA",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result["PYTHONUTF8"] = "1"
    result["PYTHONUNBUFFERED"] = "1"
    return result


def _bootstrap_prefix(module: str) -> list[str]:
    repository_root = Path(__file__).resolve().parents[2]
    package_site = next(
        (Path(value) for value in site.getsitepackages() if Path(value).name == "site-packages"),
        None,
    )
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    if package_site is None or not package_site.is_dir() or not executable.is_file():
        raise ApprovedDevPairError("CHILD_INTERPRETER_INVALID")
    bootstrap = (
        "import runpy,sys;"
        "sys.path[:0]=sys.argv[1:3];"
        f"sys.argv=['{module}']+sys.argv[3:];"
        f"runpy.run_module('{module}',run_name='__main__')"
    )
    return [
        str(executable),
        "-E",
        "-c",
        bootstrap,
        str(repository_root),
        str(package_site),
    ]


def _validate_approval(approval_id: str) -> None:
    if approval_id != APPROVAL_ID:
        raise ApprovedDevPairError("AP_DEV_PAIR_REQUIRED")


def _validate_root_inputs(root: Path, allowed_parent: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not allowed_parent.is_absolute():
        raise ApprovedDevPairError("ABSOLUTE_PATH_REQUIRED")
    parent = allowed_parent.resolve(strict=True)
    if not parent.is_dir() or _is_reparse(parent):
        raise ApprovedDevPairError("ALLOWED_PARENT_INVALID")
    candidate = root.resolve(strict=False)
    if candidate.parent != parent or candidate == parent or candidate.name in {"", ".", ".."}:
        raise ApprovedDevPairError("DEVELOPMENT_ROOT_INVALID")
    return candidate, parent


def _selected_identities(
    source_registry: Path,
    *,
    expected_source_sha256: str,
    allowlist: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], str]:
    source = source_registry.resolve(strict=True)
    if not source.is_file() or _is_reparse(source):
        raise ApprovedDevPairError("IDENTITY_SOURCE_INVALID")
    source_bytes = source.read_bytes()
    observed_sha256 = _sha256(source_bytes)
    if observed_sha256 != expected_source_sha256:
        raise ApprovedDevPairError("IDENTITY_SOURCE_HASH_MISMATCH")
    try:
        document = json.loads(
            source_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ApprovedDevPairError("IDENTITY_SOURCE_INVALID") from exc
    source_users = document.get("users") if isinstance(document, dict) else None
    if not isinstance(source_users, dict):
        raise ApprovedDevPairError("IDENTITY_SOURCE_INVALID")
    normalized_allowlist = tuple(str(item).strip().lower() for item in allowlist)
    if (
        not normalized_allowlist
        or len(set(normalized_allowlist)) != len(normalized_allowlist)
        or any(not _USER_ID_RE.fullmatch(item) for item in normalized_allowlist)
    ):
        raise ApprovedDevPairError("IDENTITY_ALLOWLIST_INVALID")
    selected: dict[str, dict[str, Any]] = {}
    for user_id in normalized_allowlist:
        raw = source_users.get(user_id)
        if not isinstance(raw, dict) or str(raw.get("id", "")).strip().lower() != user_id:
            raise ApprovedDevPairError("APPROVED_IDENTITY_MISSING")
        account_type = str(raw.get("account_type", "") or "").strip().lower()
        if (
            bool(raw.get("disabled"))
            or bool(raw.get("service_account"))
            or bool(raw.get("system_account"))
            or account_type in {"service", "system"}
            or not isinstance(raw.get("password_hash"), str)
            or not raw["password_hash"].strip()
        ):
            raise ApprovedDevPairError("APPROVED_IDENTITY_NOT_ACTIVE_HUMAN")
        copied = {key: raw[key] for key in IDENTITY_FIELDS if key in raw}
        copied["id"] = user_id
        copied["disabled"] = False
        selected[user_id] = copied
    return selected, observed_sha256


def _marker_matches(root: Path, marker: Mapping[str, Any]) -> bool:
    expected = {
        "schema_version",
        "contract",
        "root_id",
        "root_locator_sha256",
        "identity_source_sha256",
        "identity_allowlist",
        "cookie_name",
        "ports",
        "approval_id",
    }
    return (
        set(marker) == expected
        and marker.get("schema_version") == 1
        and marker.get("contract") == "code_assist.notion.approved_dev_pair_root.v1"
        and marker.get("root_locator_sha256") == _path_locator(root)
        and marker.get("approval_id") == APPROVAL_ID
        and marker.get("ports")
        == {"agent": AGENT_PORT, "gateway": GATEWAY_PORT, "notion": NOTION_PORT}
    )


def prepare_approved_dev_root(
    source_registry: Path,
    root: Path,
    *,
    allowed_parent: Path,
    expected_source_sha256: str,
    allowlist: Sequence[str],
    approval_id: str,
) -> dict[str, Any]:
    """Create or verify one marker-bound realm without copying user content."""

    _validate_approval(approval_id)
    candidate, _parent = _validate_root_inputs(Path(root), Path(allowed_parent))
    selected, source_sha256 = _selected_identities(
        Path(source_registry),
        expected_source_sha256=expected_source_sha256,
        allowlist=allowlist,
    )
    normalized_allowlist = sorted(selected)
    marker_path = candidate / ROOT_MARKER_NAME
    if candidate.exists():
        if not candidate.is_dir() or _is_reparse(candidate):
            raise ApprovedDevPairError("DEVELOPMENT_ROOT_INVALID")
        try:
            marker = _load_json(marker_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ApprovedDevPairError("EXISTING_ROOT_MARKER_INVALID") from exc
        if (
            not isinstance(marker, dict)
            or not _marker_matches(candidate, marker)
            or marker.get("identity_source_sha256") != source_sha256
            or marker.get("identity_allowlist") != normalized_allowlist
            or marker_path.read_bytes() != _canonical_bytes(marker)
        ):
            raise ApprovedDevPairError("EXISTING_ROOT_MARKER_INVALID")
        return dict(marker)

    created = False
    try:
        os.mkdir(candidate)
        created = True
        if _is_reparse(candidate):
            raise ApprovedDevPairError("DEVELOPMENT_ROOT_REPARSE_FORBIDDEN")
        for role in ("agent", "notion"):
            host_root = candidate / role
            host_root.mkdir()
            for name in (".runtime", "knowledge", "sessions", "user_data", "workspace"):
                (host_root / name).mkdir()
        for name in ("gateway-data", "gateway-runtime", "runs"):
            (candidate / name).mkdir()
        identity_document = {"users": {key: selected[key] for key in normalized_allowlist}}
        _write_json_exclusive(candidate / "agent/.runtime/users.json", identity_document)
        _write_exclusive(
            candidate / "agent/.runtime/secret.key",
            (secrets.token_hex(32) + "\n").encode("utf-8"),
        )
        _write_json_exclusive(
            candidate / "agent/.runtime/api_config.json",
            {
                "default_model_priority": [],
                "gguf": {"autoload_on_startup": False},
                "models": {},
            },
        )
        root_id = str(uuid4())
        marker = {
            "schema_version": 1,
            "contract": "code_assist.notion.approved_dev_pair_root.v1",
            "root_id": root_id,
            "root_locator_sha256": _path_locator(candidate),
            "identity_source_sha256": source_sha256,
            "identity_allowlist": normalized_allowlist,
            "cookie_name": "ca_dev_pair_" + hashlib.sha256(root_id.encode()).hexdigest()[:20],
            "ports": {
                "agent": AGENT_PORT,
                "gateway": GATEWAY_PORT,
                "notion": NOTION_PORT,
            },
            "approval_id": APPROVAL_ID,
        }
        _write_json_exclusive(marker_path, marker)
        return marker
    except BaseException:
        if created and candidate.exists() and not _is_reparse(candidate):
            shutil.rmtree(candidate)
        raise


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def _prepare_gateway_catalog(root: Path) -> Path:
    from code_assist_v1 import notion_workspace_collaboration_migration as authority_migration
    from code_assist_v1 import notion_workspace_iam_migration as iam_migration
    from code_assist_v1 import notion_workspace_live_collaboration_migration as live_migration
    from code_assist_v1 import notion_workspace_editing_authority_migration as editing_migration
    from code_assist_v1 import notion_workspace_realtime_transport_migration as transport_migration
    from code_assist_v1 import notion_workspace_second_brain_publication_migration as publication_migration
    from code_assist_v1 import notion_workspace_database_features_migration as database_migration
    from code_assist_v1 import notion_workspace_file_authority_migration as file_migration
    from code_assist_v1 import notion_workspace_history_authority_migration as history_migration
    from code_assist_v1 import notion_workspace_search_authority_migration as search_migration
    from code_assist_v1 import notion_workspace_store as workspace_store
    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    from code_assist_v1.notion_collaboration.durable_transport import DurableTransportAuthority
    from code_assist_v1.notion_collaboration.editing_authority import (
        CollaborativeEditingTransportAuthority,
    )
    from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
    from code_assist_v1.notion_collaboration.history_authority import CollaborativeHistoryAuthority
    from code_assist_v1.notion_collaboration.search_authority import CollaborativeSearchAuthority
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.iam_runtime import (
        CollaborativeDurableTransportAuthority,
    )

    data_root = root / "gateway-data"
    catalog = Path(workspace_store.catalog_path_for_user_data_root(data_root))

    def migrate_2407() -> Path:
        backup_2407 = data_root / "backups/before-2407.db"
        if not backup_2407.exists():
            _backup_sqlite(catalog, backup_2407)
        plan_2407 = database_migration.build_plan(catalog, data_root)
        database_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2407["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2407,
        )
        return migrate_2408()

    def migrate_2408() -> Path:
        backup_2408 = data_root / "backups/before-2408.db"
        if not backup_2408.exists():
            _backup_sqlite(catalog, backup_2408)
        plan_2408 = file_migration.build_plan(catalog, data_root)
        file_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2408["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2408,
        )
        CollaborativeFileAuthority.open(
            catalog,
            data_root,
            InMemoryIdentityDirectory(()),
            initialize_storage=True,
        )
        CollaborativeEditingTransportAuthority.open(catalog, data_root)
        return migrate_2409()

    def migrate_2409() -> Path:
        backup_2409 = data_root / "backups/before-2409.db"
        if not backup_2409.exists():
            _backup_sqlite(catalog, backup_2409)
        plan_2409 = history_migration.build_plan(catalog, data_root)
        history_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2409["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2409,
        )
        CollaborativeHistoryAuthority.open(
            catalog,
            data_root,
            InMemoryIdentityDirectory(()),
        )
        CollaborativeEditingTransportAuthority.open(catalog, data_root)
        return migrate_2410()

    def migrate_2410() -> Path:
        backup_2410 = data_root / "backups/before-2410.db"
        if not backup_2410.exists():
            _backup_sqlite(catalog, backup_2410)
        plan_2410 = search_migration.build_plan(catalog, data_root)
        search_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2410["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2410,
        )
        CollaborativeSearchAuthority.open(
            catalog,
            data_root,
            InMemoryIdentityDirectory(()),
        )
        CollaborativeEditingTransportAuthority.open(catalog, data_root)
        return catalog

    current_version: int | None = None
    if catalog.exists():
        # This phase runs only after the approved ports are quiesced.  A prior
        # abrupt manager exit may leave a valid WAL requiring recovery before a
        # read-only connection can even inspect the migration marker.
        con = sqlite3.connect(catalog)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            row = con.execute(
                "SELECT MAX(version) FROM workspace_schema_migrations"
            ).fetchone()
            current_version = None if row is None or row[0] is None else int(row[0])
        finally:
            con.close()
        if current_version == search_migration.TARGET_VERSION:
            CollaborativeSearchAuthority.open(
                catalog,
                data_root,
                InMemoryIdentityDirectory(()),
            )
            CollaborativeEditingTransportAuthority.open(catalog, data_root)
            return catalog
        if current_version == history_migration.TARGET_VERSION:
            return migrate_2410()
        if current_version == file_migration.TARGET_VERSION:
            return migrate_2409()
        if current_version == database_migration.TARGET_VERSION:
            return migrate_2408()
        if current_version == publication_migration.TARGET_VERSION:
            return migrate_2407()
        if current_version == editing_migration.TARGET_VERSION:
            backup_2406 = data_root / "backups/before-2406.db"
            if not backup_2406.exists():
                _backup_sqlite(catalog, backup_2406)
            plan_2406 = publication_migration.build_plan(catalog, data_root)
            publication_migration.apply_plan(
                catalog,
                data_root,
                exact_plan_checksum=plan_2406["plan_checksum"],
                backup_confirmed=True,
                catalog_quiesced_confirmed=True,
                backup_path=backup_2406,
            )
            return migrate_2407()
        if current_version == live_migration.TARGET_VERSION:
            backup_2405 = data_root / "backups/before-2405.db"
            if not backup_2405.exists():
                _backup_sqlite(catalog, backup_2405)
            plan_2405 = editing_migration.build_plan(catalog, data_root)
            editing_migration.apply_plan(
                catalog,
                data_root,
                exact_plan_checksum=plan_2405["plan_checksum"],
                backup_confirmed=True,
                catalog_quiesced_confirmed=True,
                backup_path=backup_2405,
            )
            backup_2406 = data_root / "backups/before-2406.db"
            if not backup_2406.exists():
                _backup_sqlite(catalog, backup_2406)
            plan_2406 = publication_migration.build_plan(catalog, data_root)
            publication_migration.apply_plan(
                catalog,
                data_root,
                exact_plan_checksum=plan_2406["plan_checksum"],
                backup_confirmed=True,
                catalog_quiesced_confirmed=True,
                backup_path=backup_2406,
            )
            return migrate_2407()
        if current_version == iam_migration.TARGET_VERSION:
            CollaborativeDurableTransportAuthority.open(catalog, data_root)
        elif current_version == transport_migration.TARGET_VERSION:
            DurableTransportAuthority.open(catalog, data_root)
        else:
            raise AuthorityUnavailable("approved development catalog schema is unsupported")
    else:
        store = workspace_store.WorkspaceStore(catalog)
        store.bootstrap()
        backup_2401 = data_root / "backups/before-2401.db"
        _backup_sqlite(catalog, backup_2401)
        plan_2401 = authority_migration.build_plan(catalog, data_root)
        authority_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2401["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2401,
        )
        backup_2402 = data_root / "backups/before-2402.db"
        _backup_sqlite(catalog, backup_2402)
        plan_2402 = transport_migration.build_plan(catalog, data_root)
        transport_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2402["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2402,
        )
        current_version = transport_migration.TARGET_VERSION
    if current_version == transport_migration.TARGET_VERSION:
        backup_2403 = data_root / "backups/before-2403.db"
        _backup_sqlite(catalog, backup_2403)
        plan_2403 = iam_migration.build_plan(catalog, data_root)
        iam_migration.apply_plan(
            catalog,
            data_root,
            exact_plan_checksum=plan_2403["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=True,
            backup_path=backup_2403,
        )
    backup_2404 = data_root / "backups/before-2404.db"
    _backup_sqlite(catalog, backup_2404)
    plan_2404 = live_migration.build_plan(catalog, data_root)
    live_migration.apply_plan(
        catalog,
        data_root,
        exact_plan_checksum=plan_2404["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2404,
    )
    backup_2405 = data_root / "backups/before-2405.db"
    _backup_sqlite(catalog, backup_2405)
    plan_2405 = editing_migration.build_plan(catalog, data_root)
    editing_migration.apply_plan(
        catalog,
        data_root,
        exact_plan_checksum=plan_2405["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2405,
    )
    backup_2406 = data_root / "backups/before-2406.db"
    _backup_sqlite(catalog, backup_2406)
    plan_2406 = publication_migration.build_plan(catalog, data_root)
    publication_migration.apply_plan(
        catalog,
        data_root,
        exact_plan_checksum=plan_2406["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup_2406,
    )
    return migrate_2407()


@dataclass(frozen=True, slots=True)
class ApprovedHostDescriptor:
    role: str
    pid: int
    port: int
    origin: str


class ApprovedDevPairRuntime:
    """Own one approved fixed-port development realm for the current process."""

    def __init__(
        self,
        source_registry: Path,
        root: Path,
        *,
        allowed_parent: Path,
        expected_source_sha256: str,
        allowlist: Sequence[str],
        approval_id: str,
        timeout_seconds: float = 180.0,
    ) -> None:
        _validate_approval(approval_id)
        if timeout_seconds <= 0:
            raise ApprovedDevPairError("TIMEOUT_INVALID")
        self.source_registry = Path(source_registry)
        self.root_input = Path(root)
        self.allowed_parent = Path(allowed_parent)
        self.expected_source_sha256 = expected_source_sha256
        self.allowlist = tuple(allowlist)
        self.approval_id = approval_id
        self.timeout = float(timeout_seconds)
        self.launch_id = uuid4().hex
        self.root: Path | None = None
        self.marker: dict[str, Any] | None = None
        self.run_root: Path | None = None
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.descriptors: dict[str, ApprovedHostDescriptor] = {}
        self.gateway: Any = None
        self.started = False
        self.closed = False

    def _child_environment(self, role: str) -> dict[str, str]:
        assert self.root is not None and self.marker is not None
        port = AGENT_PORT if role == "agent" else NOTION_PORT
        host_root = self.root / role
        authority_runtime = self.root / "agent/.runtime"
        origins = ",".join(
            [
                f"http://127.0.0.1:{AGENT_PORT}",
                f"http://localhost:{AGENT_PORT}",
                f"http://127.0.0.1:{NOTION_PORT}",
                f"http://localhost:{NOTION_PORT}",
            ]
        )
        environment = _sanitized_environment()
        from code_assist_v1 import notion_workspace_store as workspace_store

        collaboration_root = self.root / "gateway-data"
        collaboration_catalog = workspace_store.catalog_path_for_user_data_root(
            collaboration_root
        )
        environment.update(
            {
                "CODE_ASSIST_DATA_ROOT": str(host_root),
                "CODE_ASSIST_TEST_DATA_ROOT": str(host_root),
                "CODE_ASSIST_STANDALONE": "1",
                "CODE_ASSIST_PROCESS_ROLE": "development",
                "CODE_ASSIST_PORT": str(AGENT_PORT),
                "CODE_ASSIST_NOTION_PORT": str(NOTION_PORT),
                "CODE_ASSIST_API_CONFIG": str(self.root / "agent/.runtime/api_config.json"),
                "CODE_ASSIST_AUTOLOAD_GGUF": "0",
                "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "0",
                "CODE_ASSIST_ALLOWED_ORIGINS": origins,
                "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "0",
                "CODE_ASSIST_SESSION_COOKIE_SECURE": "0",
                "CODE_ASSIST_AUTH_USERS_FILE": str(authority_runtime / "users.json"),
                "CODE_ASSIST_AUTH_SECRET_FILE": str(authority_runtime / "secret.key"),
                "CODE_ASSIST_ACCOUNT_MUTATION_AUTHORITY_ORIGIN": f"http://127.0.0.1:{AGENT_PORT}",
                "CODE_ASSIST_SESSION_COOKIE_NAME": str(self.marker["cookie_name"]),
                "CODE_ASSIST_HOST_ROLE": role,
                "CODE_ASSIST_HOST_PORT": str(port),
                "CODE_ASSIST_I0_COLLABORATIVE_SHARING": "1",
                "CODE_ASSIST_I0_COLLABORATIVE_SHARING_KILL_SWITCH": "0",
                "CODE_ASSIST_COLLABORATION_CATALOG": str(collaboration_catalog),
                "CODE_ASSIST_COLLABORATION_USER_DATA_ROOT": str(
                    collaboration_root
                ),
                "CODE_ASSIST_COLLABORATION_GATEWAY_URL": (
                    f"ws://127.0.0.1:{GATEWAY_PORT}/realtime"
                ),
            }
        )
        return environment

    def _spawn_host(self, role: str) -> subprocess.Popen[bytes]:
        assert self.run_root is not None and self.root is not None
        descriptor = self.run_root / f"{role}.json"
        command = _bootstrap_prefix("code_assist_v1.notion_collaboration.approved_dev_pair")
        command.extend(
            [
                "--child",
                "--role",
                role,
                "--root",
                str(self.root),
                "--run-root",
                str(self.run_root),
                "--descriptor",
                str(descriptor),
                "--launch-id",
                self.launch_id,
                "--approval-id",
                self.approval_id,
            ]
        )
        return subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=self._child_environment(role),
            stdin=subprocess.DEVNULL,
            stdout=(self.run_root / f"{role}.stdout.log").open("xb"),
            stderr=(self.run_root / f"{role}.stderr.log").open("xb"),
            shell=False,
            close_fds=True,
        )

    def _wait_host(self, role: str, process: subprocess.Popen[bytes], deadline: float) -> ApprovedHostDescriptor:
        assert self.run_root is not None
        descriptor_path = self.run_root / f"{role}.json"
        expected_port = AGENT_PORT if role == "agent" else NOTION_PORT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ApprovedDevPairError(f"{role.upper()}_EXITED_BEFORE_READY")
            if descriptor_path.is_file():
                document = _load_json(descriptor_path)
                expected_keys = {
                    "schema_version",
                    "contract",
                    "role",
                    "pid",
                    "port",
                    "origin",
                    "launch_id",
                    "root_id",
                }
                if (
                    not isinstance(document, dict)
                    or set(document) != expected_keys
                    or descriptor_path.read_bytes() != _canonical_bytes(document)
                    or document.get("contract") != "code_assist.notion.approved_dev_pair_host.v1"
                    or document.get("role") != role
                    or document.get("pid") != process.pid
                    or document.get("port") != expected_port
                    or document.get("origin") != f"http://{LOOPBACK_HOST}:{expected_port}"
                    or document.get("launch_id") != self.launch_id
                    or document.get("root_id") != self.marker.get("root_id")
                ):
                    raise ApprovedDevPairError("HOST_DESCRIPTOR_INVALID")
                return ApprovedHostDescriptor(
                    role=role,
                    pid=process.pid,
                    port=expected_port,
                    origin=str(document["origin"]),
                )
            time.sleep(0.03)
        raise ApprovedDevPairError(f"{role.upper()}_READINESS_TIMEOUT")

    def _verify_health(self, descriptor: ApprovedHostDescriptor, deadline: float) -> None:
        while time.monotonic() < deadline:
            try:
                with urlopen(Request(descriptor.origin + "/healthz"), timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                pass
            time.sleep(0.03)
        raise ApprovedDevPairError(f"{descriptor.role.upper()}_HEALTH_TIMEOUT")

    def start(self) -> dict[str, Any]:
        if self.started or self.closed:
            raise ApprovedDevPairError("PAIR_NOT_STARTABLE")
        if any(_port_accepting(port) for port in OPERATOR_PORTS):
            raise ApprovedDevPairError("OPERATOR_PORT_ALREADY_IN_USE")
        try:
            self.marker = prepare_approved_dev_root(
                self.source_registry,
                self.root_input,
                allowed_parent=self.allowed_parent,
                expected_source_sha256=self.expected_source_sha256,
                allowlist=self.allowlist,
                approval_id=self.approval_id,
            )
            self.root = self.root_input.resolve(strict=True)
            active = self.root / ACTIVE_RUN_NAME
            if active.exists():
                try:
                    recorded = _load_json(active)
                    recorded_run = (
                        self.root / "runs" / str(recorded.get("run_root_name", ""))
                    ).resolve(strict=True)
                    recorded_run.relative_to((self.root / "runs").resolve(strict=True))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise ApprovedDevPairError("ACTIVE_RUN_INVALID") from exc
                exact_stale_record = (
                    recorded.get("contract")
                    == "code_assist.notion.approved_dev_pair_active_run.v1"
                    and recorded.get("root_locator_sha256") == _path_locator(self.root)
                    and recorded.get("run_root_locator_sha256")
                    == _path_locator(recorded_run)
                    and recorded.get("ports") == list(OPERATOR_PORTS)
                )
                if (
                    not exact_stale_record
                    or _pid_running(int(recorded.get("manager_pid", 0)))
                    or any(_port_accepting(port) for port in OPERATOR_PORTS)
                ):
                    raise ApprovedDevPairError("ACTIVE_RUN_ALREADY_RECORDED")
                # An abrupt, externally verified shutdown can leave only this
                # exact ownership record behind.  No process or port is live,
                # so removing the stale marker is safe and permits recovery.
                active.unlink()
            self.run_root = self.root / "runs" / self.launch_id
            self.run_root.mkdir()
            _write_json_exclusive(
                self.run_root / "run-marker.json",
                {
                    "contract": "code_assist.notion.approved_dev_pair_run.v1",
                    "launch_id": self.launch_id,
                    "root_id": self.marker["root_id"],
                    "run_root_locator_sha256": _path_locator(self.run_root),
                },
            )
            catalog = _prepare_gateway_catalog(self.root)
            from code_assist_v1.notion_collaboration.cross_process_gateway import CrossProcessGatewayRuntime

            gateway_root = self.root / "gateway-runtime" / self.launch_id
            self.gateway = CrossProcessGatewayRuntime(
                gateway_root,
                allowed_parent=self.root / "gateway-runtime",
                catalog_path=catalog,
                user_data_root=self.root / "gateway-data",
                timeout_seconds=self.timeout,
                operator_fixed_port=GATEWAY_PORT,
                operator_approval_id=self.approval_id,
            )
            self.gateway.start()
            deadline = time.monotonic() + self.timeout
            for role in _HOST_ROLES:
                process = self._spawn_host(role)
                self.processes[role] = process
                self.descriptors[role] = self._wait_host(role, process, deadline)
                self._verify_health(self.descriptors[role], deadline)
            if not all(_wait_port(port, accepting=True, deadline=deadline) for port in OPERATOR_PORTS):
                raise ApprovedDevPairError("PAIR_PORT_READINESS_FAILED")
            self.started = True
            return self.receipt()
        except BaseException:
            try:
                self.close()
            except ApprovedDevPairError:
                pass
            raise

    def receipt(self) -> dict[str, Any]:
        if not self.started or self.root is None or self.marker is None or self.gateway is None:
            raise ApprovedDevPairError("PAIR_NOT_STARTED")
        return {
            "schema_version": 1,
            "contract": "code_assist.notion.approved_dev_pair_runtime_receipt.v1",
            "status": "pass",
            "approval_id": self.approval_id,
            "launch_id": self.launch_id,
            "root_id": self.marker["root_id"],
            "root_locator_sha256": self.marker["root_locator_sha256"],
            "identity_source_sha256": self.marker["identity_source_sha256"],
            "approved_identity_ids": list(self.marker["identity_allowlist"]),
            "approved_identity_count": len(self.marker["identity_allowlist"]),
            "cookie_name": self.marker["cookie_name"],
            "new_signing_secret": True,
            "existing_signing_secret_copied": False,
            "hosts": [
                {
                    "role": item.role,
                    "pid": item.pid,
                    "port": item.port,
                    "origin": item.origin,
                }
                for item in self.descriptors.values()
            ],
            "gateway": {
                "pid": int(self.gateway.descriptor["gateway_pid"]),
                "compactor_pid": int(self.gateway.descriptor["compactor_pid"]),
                "port": self.gateway.port,
                "origin": self.gateway.origin,
                "selected_cross_process_runtime": True,
            },
            "account_mutation_authority_origin": f"http://{LOOPBACK_HOST}:{AGENT_PORT}",
            "notion_password_authority_copy": False,
            "personal_content_imported": False,
            "production_port_or_root_mutated": False,
            "plaintext_password_hash_secret_cookie_or_session_recorded": False,
        }

    def close(self) -> dict[str, bool]:
        if self.closed:
            return {
                "owned_host_processes_stopped": True,
                "owned_gateway_processes_stopped": True,
                "owned_ports_closed": True,
            }
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + min(self.timeout, 15.0)
        for process in self.processes.values():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        gateway_cleanup = {
            "gateway_stopped": True,
            "compactor_stopped": True,
            "port_closed": True,
            "runtime_root_removed": True,
        }
        if self.gateway is not None:
            gateway_cleanup = self.gateway.close()
        hosts_stopped = all(process.poll() is not None for process in self.processes.values())
        ports_closed = all(
            _wait_port(port, accepting=False, deadline=deadline) for port in OPERATOR_PORTS
        )
        self.closed = True
        result = {
            "owned_host_processes_stopped": hosts_stopped,
            "owned_gateway_processes_stopped": all(gateway_cleanup.values()),
            "owned_ports_closed": ports_closed,
        }
        if not all(result.values()):
            raise ApprovedDevPairError("OWNED_RUNTIME_CLEANUP_FAILED")
        return result

    def __enter__(self) -> "ApprovedDevPairRuntime":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _child_main(args: argparse.Namespace) -> int:
    _validate_approval(args.approval_id)
    root = Path(args.root).resolve(strict=True)
    run_root = Path(args.run_root).resolve(strict=True)
    descriptor = Path(args.descriptor).resolve(strict=False)
    marker = _load_json(root / ROOT_MARKER_NAME)
    run_marker = _load_json(run_root / "run-marker.json")
    role = str(args.role)
    port = AGENT_PORT if role == "agent" else NOTION_PORT
    if (
        role not in _HOST_ROLES
        or descriptor.parent != run_root
        or descriptor.exists()
        or not isinstance(marker, dict)
        or not _marker_matches(root, marker)
        or run_marker
        != {
            "contract": "code_assist.notion.approved_dev_pair_run.v1",
            "launch_id": args.launch_id,
            "root_id": marker["root_id"],
            "run_root_locator_sha256": _path_locator(run_root),
        }
        or os.environ.get("CODE_ASSIST_HOST_ROLE") != role
        or os.environ.get("CODE_ASSIST_HOST_PORT") != str(port)
    ):
        raise ApprovedDevPairError("CHILD_BINDING_INVALID")
    if role == "agent":
        from code_assist_v1.app_code import create_app
    else:
        from code_assist_v1.app_notion import create_app
    from werkzeug.serving import make_server

    application = create_app()
    application.config.update(
        SESSION_COOKIE_NAME=str(marker["cookie_name"]),
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
    )
    server = make_server(LOOPBACK_HOST, port, application, threaded=True)
    try:
        _write_json_exclusive(
            descriptor,
            {
                "schema_version": 1,
                "contract": "code_assist.notion.approved_dev_pair_host.v1",
                "role": role,
                "pid": os.getpid(),
                "port": port,
                "origin": f"http://{LOOPBACK_HOST}:{port}",
                "launch_id": args.launch_id,
                "root_id": marker["root_id"],
            },
        )
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _serve_main(args: argparse.Namespace) -> int:
    runtime = ApprovedDevPairRuntime(
        Path(args.source_registry),
        Path(args.root),
        allowed_parent=Path(args.allowed_parent),
        expected_source_sha256=args.expected_source_sha256,
        allowlist=args.allow_identity,
        approval_id=args.approval_id,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        receipt = runtime.start()
        assert runtime.root is not None and runtime.run_root is not None
        active_path = runtime.root / ACTIVE_RUN_NAME
        _write_json_exclusive(
            active_path,
            {
                "contract": "code_assist.notion.approved_dev_pair_active_run.v1",
                "launch_id": runtime.launch_id,
                "manager_pid": os.getpid(),
                "root_locator_sha256": _path_locator(runtime.root),
                "run_root_locator_sha256": _path_locator(runtime.run_root),
                "run_root_name": runtime.run_root.name,
                "ports": list(OPERATOR_PORTS),
            },
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), flush=True)
        stop_request = runtime.run_root / STOP_REQUEST_NAME
        while not stop_request.exists():
            if any(process.poll() is not None for process in runtime.processes.values()):
                raise ApprovedDevPairError("OWNED_HOST_EXITED")
            if runtime.gateway.process is None or runtime.gateway.process.poll() is not None:
                raise ApprovedDevPairError("OWNED_GATEWAY_EXITED")
            time.sleep(0.25)
        request_document = _load_json(stop_request)
        if request_document != {
            "contract": "code_assist.notion.approved_dev_pair_stop_request.v1",
            "launch_id": runtime.launch_id,
            "root_locator_sha256": _path_locator(runtime.root),
        }:
            raise ApprovedDevPairError("STOP_REQUEST_INVALID")
        return 0
    finally:
        cleanup = runtime.close()
        if runtime.root is not None:
            active_path = runtime.root / ACTIVE_RUN_NAME
            try:
                active = _load_json(active_path)
                if active.get("launch_id") == runtime.launch_id:
                    active_path.unlink()
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        print(json.dumps({"status": "stopped", "cleanup": cleanup}, sort_keys=True), flush=True)


def _stop_completed(active_path: Path) -> bool:
    return not active_path.exists() and not any(
        _port_accepting(port) for port in OPERATOR_PORTS
    )


def _stop_main(args: argparse.Namespace) -> int:
    _validate_approval(args.approval_id)
    root, _parent = _validate_root_inputs(Path(args.root), Path(args.allowed_parent))
    root = root.resolve(strict=True)
    marker = _load_json(root / ROOT_MARKER_NAME)
    active_path = root / ACTIVE_RUN_NAME
    active = _load_json(active_path)
    run_root = (root / "runs" / str(active.get("run_root_name", ""))).resolve(strict=True)
    if (
        not isinstance(marker, dict)
        or not _marker_matches(root, marker)
        or active.get("contract") != "code_assist.notion.approved_dev_pair_active_run.v1"
        or active.get("root_locator_sha256") != _path_locator(root)
        or active.get("run_root_locator_sha256") != _path_locator(run_root)
        or active.get("ports") != list(OPERATOR_PORTS)
        or not _pid_running(int(active.get("manager_pid", 0)))
    ):
        raise ApprovedDevPairError("ACTIVE_RUN_INVALID")
    _write_json_exclusive(
        run_root / STOP_REQUEST_NAME,
        {
            "contract": "code_assist.notion.approved_dev_pair_stop_request.v1",
            "launch_id": active["launch_id"],
            "root_locator_sha256": _path_locator(root),
        },
    )
    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        if _stop_completed(active_path):
            print(json.dumps({"status": "stopped", "ports_closed": True}, sort_keys=True))
            return 0
        time.sleep(0.1)
    if _stop_completed(active_path):
        print(json.dumps({"status": "stopped", "ports_closed": True}, sort_keys=True))
        return 0
    raise ApprovedDevPairError("STOP_TIMEOUT")


def _status_main(args: argparse.Namespace) -> int:
    _validate_approval(args.approval_id)
    root, _parent = _validate_root_inputs(Path(args.root), Path(args.allowed_parent))
    active_path = root / ACTIVE_RUN_NAME
    active = _load_json(active_path) if active_path.is_file() else None
    result = {
        "status": "running" if active and all(_port_accepting(port) for port in OPERATOR_PORTS) else "stopped",
        "ports": {str(port): _port_accepting(port) for port in OPERATOR_PORTS},
        "active_run_recorded": bool(active),
        "manager_running": bool(active and _pid_running(int(active.get("manager_pid", 0)))),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-registry")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--allow-identity", action="append", default=[])
    parser.add_argument("--root", required=True)
    parser.add_argument("--allowed-parent")
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--role", choices=_HOST_ROLES, help=argparse.SUPPRESS)
    parser.add_argument("--run-root", help=argparse.SUPPRESS)
    parser.add_argument("--descriptor", help=argparse.SUPPRESS)
    parser.add_argument("--launch-id", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.child:
        if not all((args.role, args.run_root, args.descriptor, args.launch_id)):
            raise ApprovedDevPairError("CHILD_ARGUMENTS_REQUIRED")
        return _child_main(args)
    if not args.allowed_parent:
        raise ApprovedDevPairError("ALLOWED_PARENT_REQUIRED")
    if args.serve:
        if not all((args.source_registry, args.expected_source_sha256, args.allow_identity)):
            raise ApprovedDevPairError("SERVE_ARGUMENTS_REQUIRED")
        return _serve_main(args)
    if args.stop:
        return _stop_main(args)
    return _status_main(args)


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess integration
    raise SystemExit(main())


__all__ = [
    "AGENT_PORT",
    "APPROVAL_ID",
    "ApprovedDevPairError",
    "ApprovedDevPairRuntime",
    "GATEWAY_PORT",
    "NOTION_PORT",
    "OPERATOR_PORTS",
    "prepare_approved_dev_root",
]
