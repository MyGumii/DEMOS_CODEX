"""Isolated full-feature localhost preview for the existing Second Brain UI.

This is deliberately not a production deployment entry point.  It copies the
legacy data tree into a marker-bound preview root, preserves the normal model
registry, optionally selects the Codex bridge as an explicit validation lane,
and adds the canonical organization/Part administration API to the existing UI.

The live repository data roots are read-only inputs.  Every mutable effect is
written below ``--data-root``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Mapping
from uuid import UUID, uuid5


_SCHEMA = "code-assist.full-second-brain-preview"
_SCHEMA_VERSION = 3
_MARKER = "full-second-brain-preview.json"
_NAMESPACE = UUID("f739b9d8-02a0-4b57-8c0e-e65d076295ad")
_ADMIN_ID = "admin"
_VALIDATION_CODEX_CLI_MODEL = "gpt-5.6-luna"
_PREVIEW_TEAM_NAME = "Second Brain Preview Team"
_PREVIEW_PART_NAME = "Second Brain Preview Part"
_PREVIEW_PART_B_NAME = "Second Brain Preview Research Part"
_PREVIEW_TEAM_LEADER_ID = "preview-team-leader"
_PREVIEW_RESPONSIBILITY_EXECUTIVE_ID = "preview-responsibility-executive"


class FullPreviewError(RuntimeError):
    """Stable local-preview failure."""


@dataclass(frozen=True, slots=True)
class GoldOrgUnit:
    id: str
    kind: str
    name: str
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class GoldUser:
    id: str
    display_name: str
    membership_id: str
    part_id: str


@dataclass(frozen=True, slots=True)
class GoldPartLeader:
    assignment_id: str
    part_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class GoldOrganizationSnapshot:
    source_database: Path
    responsibility: GoldOrgUnit
    teams: tuple[GoldOrgUnit, ...]
    parts: tuple[GoldOrgUnit, ...]
    users: tuple[GoldUser, ...]
    part_leaders: tuple[GoldPartLeader, ...]


def _gold_team_leader_ids(
    snapshot: GoldOrganizationSnapshot,
) -> dict[str, str]:
    return {
        team.id: f"sbv-team-{index:02d}-lead"
        for index, team in enumerate(
            sorted(snapshot.teams, key=lambda value: value.name.casefold()), start=1
        )
    }


def _direct_validation_accounts(
    snapshot: GoldOrganizationSnapshot | None,
) -> tuple[tuple[str, str], ...]:
    if snapshot is None:
        return (
            (_PREVIEW_TEAM_LEADER_ID, "Second Brain Preview Team Leader"),
            (
                _PREVIEW_RESPONSIBILITY_EXECUTIVE_ID,
                "Second Brain Preview Responsibility Executive",
            ),
        )
    leaders = _gold_team_leader_ids(snapshot)
    rows = [
        (leaders[team.id], f"{team.name} Team Leader")
        for team in sorted(snapshot.teams, key=lambda value: value.name.casefold())
    ]
    rows.append(("sbv-responsibility-exec", "AMHS Responsibility Executive"))
    return tuple(rows)


def _load_gold_organization(source_root: Path) -> GoldOrganizationSnapshot:
    """Read only the approved 10011 organization surface; never copy secrets."""

    source = source_root.resolve(strict=True)
    database = source / "core" / "knowledge_core.db"
    if not database.is_file():
        raise FullPreviewError("The 10011 organization source database is missing.")
    connection = sqlite3.connect(
        "file:" + database.as_posix() + "?mode=ro",
        isolation_level=None,
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        required = {"app_user", "org_membership", "org_role_assignment", "org_unit"}
        if not required.issubset(tables):
            raise FullPreviewError("The 10011 organization source schema is incomplete.")
        unit_rows = connection.execute(
            """SELECT id,kind,name,parent_id
                 FROM org_unit
                WHERE status='active'
                  AND kind IN ('responsibility','team','part')
                ORDER BY kind,id"""
        ).fetchall()
        units = tuple(
            GoldOrgUnit(
                id=str(row["id"]),
                kind=str(row["kind"]),
                name=str(row["name"]),
                parent_id=None if row["parent_id"] is None else str(row["parent_id"]),
            )
            for row in unit_rows
        )
        responsibilities = tuple(unit for unit in units if unit.kind == "responsibility")
        teams = tuple(unit for unit in units if unit.kind == "team")
        parts = tuple(unit for unit in units if unit.kind == "part")
        if len(responsibilities) != 1 or len(teams) != 3 or len(parts) != 7:
            raise FullPreviewError("The 10011 organization must contain 1/3/7 hierarchy units.")
        responsibility = responsibilities[0]
        team_ids = {unit.id for unit in teams}
        part_ids = {unit.id for unit in parts}
        if (
            any(unit.parent_id != responsibility.id for unit in teams)
            or any(unit.parent_id not in team_ids for unit in parts)
        ):
            raise FullPreviewError("The 10011 organization hierarchy is inconsistent.")
        user_rows = connection.execute(
            """SELECT u.id,u.display_name,u.current_part_id,m.id AS membership_id
                 FROM app_user AS u
                 JOIN org_membership AS m
                   ON m.user_id=u.id AND m.part_id=u.current_part_id
                  AND m.valid_to IS NULL
                WHERE u.account_status='active' AND u.id LIKE 'sbv-p__-u__'
                ORDER BY u.id"""
        ).fetchall()
        users = tuple(
            GoldUser(
                id=str(row["id"]),
                display_name=str(row["display_name"]),
                membership_id=str(row["membership_id"]),
                part_id=str(row["current_part_id"]),
            )
            for row in user_rows
        )
        counts = {part_id: 0 for part_id in part_ids}
        for user in users:
            if user.part_id not in counts:
                raise FullPreviewError("A 10011 validation user belongs to an unknown Part.")
            counts[user.part_id] += 1
        if len(users) != 70 or set(counts.values()) != {10}:
            raise FullPreviewError("The 10011 organization must contain ten validation users per Part.")
        leader_rows = connection.execute(
            """SELECT id,org_unit_id,user_id
                 FROM org_role_assignment
                WHERE role='part_leader' AND valid_to IS NULL
                ORDER BY org_unit_id"""
        ).fetchall()
        part_leaders = tuple(
            GoldPartLeader(
                assignment_id=str(row["id"]),
                part_id=str(row["org_unit_id"]),
                user_id=str(row["user_id"]),
            )
            for row in leader_rows
            if str(row["org_unit_id"]) in part_ids
        )
        if len(part_leaders) != 7 or {row.part_id for row in part_leaders} != part_ids:
            raise FullPreviewError("The 10011 Part-leader assignments are incomplete.")
        return GoldOrganizationSnapshot(
            source_database=database,
            responsibility=responsibility,
            teams=tuple(sorted(teams, key=lambda unit: unit.name.casefold())),
            parts=tuple(sorted(parts, key=lambda unit: unit.name.casefold())),
            users=users,
            part_leaders=part_leaders,
        )
    except sqlite3.Error as exc:
        raise FullPreviewError("The 10011 organization source could not be read.") from exc
    finally:
        connection.close()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _preview_feature_flags():
    """One marker-bound successor flag set; production environment stays closed."""

    from code_assist_v1.knowledge_core.domain import FeatureFlags

    return FeatureFlags(
        core_enabled=True,
        multi_wiki_schema=True,
        multi_wiki_reads=True,
        multi_wiki_writes=True,
        conversation_wiki_binding=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
        projections=True,
        projection_footer_v2=True,
        higher_spaces=True,
        aggregate_graph_reads=True,
        auto_semantic_edges_local=True,
        auto_semantic_edges_aggregate=True,
        hybrid_rerank=True,
        temporal_ranking=True,
        team_writes=True,
        responsibility_writes=True,
    )


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _repository_root() -> Path:
    return _package_root().parent


def _default_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return base / "CodeAssist" / "previews" / "second-brain-10111-sbe7-v2"


def _configure_environment(root: Path, *, port: int, codex: bool) -> None:
    if not root.is_absolute() or not 1 <= port <= 65535:
        raise FullPreviewError("The preview root or port is invalid.")
    origins = f"http://localhost:{port},http://127.0.0.1:{port}"
    values = {
        "CODE_ASSIST_TEST_MODE": "1",
        "CODE_ASSIST_TEST_DATA_ROOT": os.fspath(root),
        "CODE_ASSIST_DATA_ROOT": os.fspath(root),
        "CODE_ASSIST_STANDALONE": "1",
        "CODE_ASSIST_PROCESS_ROLE": "development",
        "CODE_ASSIST_PORT": str(port),
        "CODE_ASSIST_API_CONFIG": os.fspath(root / ".runtime" / "api_config.json"),
        "CODE_ASSIST_AUTOLOAD_GGUF": "0",
        # This preview never executes an operator-supplied document converter.
        # Native in-process parsers are the only allowed normalization lane.
        "CODE_ASSIST_DOCUMENT_CONVERTERS": "native",
        "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "1" if codex else "0",
        "CODE_ASSIST_CODEX_CWD": os.fspath(root / "codex-context"),
        "CODE_ASSIST_CODEX_SANDBOX": "read-only",
        "CODE_ASSIST_CODEX_EPHEMERAL": "1",
        "CODE_ASSIST_ALLOWED_ORIGINS": origins,
        "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "0",
        "CODE_ASSIST_SESSION_COOKIE_SECURE": "0",
    }
    if codex:
        # This isolated bridge is a validation provider, not the production
        # OpenAI-compatible lane.  The currently selected Codex model accepts
        # ``none`` and completes the bounded structured matrix substantially
        # faster without changing company-provider routing or prompts.
        values["CODE_ASSIST_CODEX_REASONING_EFFORT"] = "none"
        values["CODE_ASSIST_CODEX_CLI_MODEL"] = _VALIDATION_CODEX_CLI_MODEL
    for name, value in values.items():
        current = os.environ.get(name)
        if current not in (None, value):
            raise FullPreviewError(f"The process already bound an incompatible {name}.")
        os.environ[name] = value
    if codex and "--codex" not in sys.argv:
        sys.argv.append("--codex")


def _tree_fingerprint(root: Path) -> tuple[int, int, str]:
    members: list[dict[str, object]] = []
    total = 0
    if root.is_dir():
        for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
            raw = path.read_bytes()
            total += len(raw)
            members.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(raw),
                    "size": len(raw),
                }
            )
    return len(members), total, _sha256(_canonical_bytes(members))


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        raise FullPreviewError(f"Preview target already exists: {target.name}")
    if source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2)
    else:
        target.mkdir(parents=True, exist_ok=False)


def _write_exclusive(path: Path, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("short preview write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _preview_model_config(source: Path, target: Path, *, codex: bool) -> None:
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("models"), dict):
        raise FullPreviewError("The normal model registry is invalid.")
    value = dict(value)
    value["gguf"] = {**dict(value.get("gguf") or {}), "autoload_on_startup": False}
    models = dict(value["models"])
    priority = [str(item) for item in value.get("default_model_priority") or []]
    if codex:
        models["codex-local"] = {
            "capabilities": ["code", "agent", "workspace", "second-brain"],
            "context_window": 128000,
            "cost_tier": "local",
            "env_id": "codex-local",
            "model": "codex-cli",
            "name": f"Codex Local Bridge ({_VALIDATION_CODEX_CLI_MODEL})",
            "runtime": "codex",
            "codex_cli_model": _VALIDATION_CODEX_CLI_MODEL,
        }
        if "codex-local" not in priority:
            priority.append("codex-local")
    value["models"] = models
    value["default_model_priority"] = priority
    _write_exclusive(target, _canonical_bytes(value))


def _load_marker(root: Path) -> dict[str, Any]:
    path = root / ".runtime" / _MARKER
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullPreviewError("The preview marker is unreadable.") from exc
    if not isinstance(value, dict):
        raise FullPreviewError("The preview marker is invalid.")
    digest = value.get("marker_sha256")
    payload = dict(value)
    payload.pop("marker_sha256", None)
    if (
        value.get("report_schema") != _SCHEMA
        or value.get("schema_version") != _SCHEMA_VERSION
        or digest != _sha256(_canonical_bytes(payload))
        or Path(str(value.get("data_root"))).resolve(strict=False) != root
    ):
        raise FullPreviewError("The preview marker binding is invalid.")
    return value


def prepare_preview_root(
    root: Path,
    *,
    codex: bool,
    gold_organization: GoldOrganizationSnapshot | None = None,
    clean_validation_root: bool = False,
) -> dict[str, Any]:
    root = root.resolve(strict=False)
    marker_path = root / ".runtime" / _MARKER
    if marker_path.exists():
        marker = _load_marker(root)
        marker_mode = str(marker.get("validation_seed_mode") or "fixture")
        requested_mode = "clean" if clean_validation_root else "fixture"
        if marker_mode != requested_mode:
            raise FullPreviewError("The preview validation-seed mode changed.")
        bound = marker.get("organization_source")
        requested = None if gold_organization is None else gold_organization.source_database
        if (bound is None) != (requested is None):
            raise FullPreviewError("The preview organization-source binding changed.")
        if isinstance(bound, Mapping) and Path(str(bound.get("path"))).resolve(strict=True) != requested:
            raise FullPreviewError("The preview is bound to another organization source.")
        return marker
    if root.exists() and any(root.iterdir()):
        raise FullPreviewError("An unmarked non-empty preview root is refused.")
    root.mkdir(parents=True, exist_ok=True)
    package = _package_root()
    for name in ("knowledge", "user_data", "sessions", "workspace"):
        _copy_tree(package / name, root / name)
    runtime = root / ".runtime"
    runtime.mkdir(parents=False, exist_ok=False)
    source_users = package / ".runtime" / "users.json"
    auth_registry = json.loads(source_users.read_text(encoding="utf-8"))
    auth_users = auth_registry.get("users") if isinstance(auth_registry, dict) else None
    if not isinstance(auth_users, dict) or not isinstance(auth_users.get(_ADMIN_ID), dict):
        raise FullPreviewError("The source authentication registry is invalid.")
    admin_auth = auth_users[_ADMIN_ID]
    password_hash = admin_auth.get("password_hash")
    if not isinstance(password_hash, str) or not password_hash:
        raise FullPreviewError("The preview admin password hash is unavailable.")
    if gold_organization is not None:
        for index, user in enumerate(gold_organization.users):
            auth_users[user.id] = {
                "created_at": 1_787_391_632 + index,
                "display_name": user.display_name,
                "id": user.id,
                "must_change_password": False,
                "password_changed_at": 1_787_391_632 + index,
                "password_hash": password_hash,
                "role": "user",
                "updated_at": 1_787_391_632 + index,
            }
    direct_offset = len(auth_users) + 1
    for index, (user_id, display_name) in enumerate(
        _direct_validation_accounts(gold_organization), start=direct_offset
    ):
        auth_users[user_id] = {
            "active": True,
            "created_at": 1_787_392_000 + index,
            "display_name": display_name,
            "id": user_id,
            "must_change_password": False,
            "password_changed_at": 1_787_392_000 + index,
            "password_hash": password_hash,
            "role": "user",
            "updated_at": 1_787_392_000 + index,
        }
    _write_exclusive(runtime / "users.json", _canonical_bytes(auth_registry))
    _preview_model_config(package / "api_config.json", runtime / "api_config.json", codex=codex)
    for name in ("core", "blobs", "projections", "codex-context"):
        (root / name).mkdir(parents=False, exist_ok=False)
    knowledge_count, knowledge_bytes, knowledge_sha = _tree_fingerprint(package / "knowledge")
    user_count, user_bytes, user_sha = _tree_fingerprint(package / "user_data")
    payload: dict[str, object] = {
        "company_api_called": False,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_root": os.fspath(root),
        "knowledge_source": {
            "bytes": knowledge_bytes,
            "files": knowledge_count,
            "sha256": knowledge_sha,
        },
        "model_policy": "api_then_gguf_then_optional_codex",
        "original_data_mutated": False,
        "report_schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "validation_seed_mode": "clean" if clean_validation_root else "fixture",
        "user_data_source": {
            "bytes": user_bytes,
            "files": user_count,
            "sha256": user_sha,
        },
    }
    if gold_organization is not None:
        source_raw = gold_organization.source_database.read_bytes()
        payload["organization_source"] = {
            "path": os.fspath(gold_organization.source_database),
            "sha256": _sha256(source_raw),
            "size": len(source_raw),
        }
    payload["marker_sha256"] = _sha256(_canonical_bytes(payload))
    _write_exclusive(marker_path, _canonical_bytes(payload))
    return dict(payload)


def assert_original_inputs_unchanged(marker: Mapping[str, object]) -> None:
    package = _package_root()
    for key, name in (("knowledge_source", "knowledge"), ("user_data_source", "user_data")):
        expected = marker.get(key)
        if not isinstance(expected, Mapping):
            raise FullPreviewError(f"The marker has no {key} binding.")
        files, size, digest = _tree_fingerprint(package / name)
        observed = {"bytes": size, "files": files, "sha256": digest}
        if dict(expected) != observed:
            raise FullPreviewError(
                f"The original {name} input changed after this preview was copied."
            )
    organization_source = marker.get("organization_source")
    if organization_source is not None:
        if not isinstance(organization_source, Mapping):
            raise FullPreviewError("The organization source marker is invalid.")
        source = Path(str(organization_source.get("path"))).resolve(strict=True)
        raw = source.read_bytes()
        observed = {"path": os.fspath(source), "sha256": _sha256(raw), "size": len(raw)}
        if dict(organization_source) != observed:
            raise FullPreviewError(
                "The 10011 organization source changed after the preview imported it."
            )


def _bootstrap_preview_schema(database: Path) -> None:
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import (
        CANONICAL_MIGRATION_CATALOG,
        MigrationStorageLayout,
        SingleMigrator,
    )

    preview_root = database.parent.parent
    layout = MigrationStorageLayout(
        data_root=preview_root,
        database=database,
        blob_root=preview_root / "blobs",
        projection_root=preview_root / "projections",
    )
    factory = ConnectionFactory._for_isolated_tests(
        database, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="full-second-brain-preview-v2",
        storage_layout=layout,
    ).run()
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        if row is None or str(row[0]).lower() != "wal":
            raise FullPreviewError("The preview database could not enter WAL mode.")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    finally:
        connection.close()


def _canonical_factory(root: Path) -> object:
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG

    database = root / "core" / "knowledge_core.db"
    _bootstrap_preview_schema(database)
    factory = ConnectionFactory._for_isolated_tests(database, CANONICAL_MIGRATION_CATALOG)
    probe = factory.open(read_only=True)
    probe.close()
    return factory


def _read_auth_users(root: Path) -> dict[str, Mapping[str, Any]]:
    value = json.loads((root / ".runtime" / "users.json").read_text(encoding="utf-8"))
    users = value.get("users") if isinstance(value, dict) else None
    if not isinstance(users, dict) or _ADMIN_ID not in users:
        raise FullPreviewError("The copied authentication registry has no admin.")
    return {
        str(user_id): raw
        for user_id, raw in users.items()
        if isinstance(raw, dict) and isinstance(raw.get("password_hash"), str)
    }


def seed_gold_administration(
    factory: object,
    root: Path,
    snapshot: GoldOrganizationSnapshot,
) -> dict[str, Any]:
    """Recreate the approved 10011 1/3/7 organization in the isolated DB."""

    from code_assist_v1.knowledge_core.db import transaction
    from code_assist_v1.knowledge_core.repositories.organization import (
        OrganizationRepository,
        RESPONSIBILITY_ROOT_ID,
    )
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository
    from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner

    if snapshot.responsibility.id != RESPONSIBILITY_ROOT_ID:
        raise FullPreviewError("The 10011 responsibility root identity is incompatible.")
    auth_users = _read_auth_users(root)
    now_ms = 1_787_391_632_000
    team_by_id = {unit.id: unit for unit in snapshot.teams}
    part_by_id = {unit.id: unit for unit in snapshot.parts}
    users_by_part: dict[str, list[GoldUser]] = {part_id: [] for part_id in part_by_id}
    for user in snapshot.users:
        users_by_part[user.part_id].append(user)
    leader_by_part = {leader.part_id: leader for leader in snapshot.part_leaders}
    admin_parts = [
        part_id for part_id, leader in leader_by_part.items() if leader.user_id == _ADMIN_ID
    ]
    if len(admin_parts) != 1:
        raise FullPreviewError("The 10011 source must identify the admin's exact Part.")
    main_part_id = admin_parts[0]
    main_team_id = part_by_id[main_part_id].parent_id
    if main_team_id is None:
        raise FullPreviewError("The 10011 admin Part has no Team.")
    sibling_parts = sorted(
        (unit for unit in snapshot.parts if unit.parent_id == main_team_id and unit.id != main_part_id),
        key=lambda unit: unit.name.casefold(),
    )
    if not sibling_parts:
        raise FullPreviewError("The 10011 admin Team has no second Part fixture.")
    second_part_id = sibling_parts[0].id
    ids: dict[str, Any] = {
        "responsibility": snapshot.responsibility.id,
        "team": main_team_id,
        "part": main_part_id,
        "part_b": second_part_id,
        "membership": _uuid("gold-membership-admin"),
        "leader": leader_by_part[main_part_id].assignment_id,
        "team_ids": tuple(unit.id for unit in snapshot.teams),
        "part_ids": tuple(unit.id for unit in snapshot.parts),
        "gold_user_ids": tuple(user.id for user in snapshot.users),
        "gold_users_by_part": {
            part_id: tuple(user.id for user in sorted(rows, key=lambda value: value.id))
            for part_id, rows in users_by_part.items()
        },
        "part_actor": users_by_part[main_part_id][0].id,
        "part_b_actor": users_by_part[second_part_id][0].id,
    }
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            organization = OrganizationRepository()
            spaces = SpaceRepository()
            existing_count = int(connection.execute("SELECT count(*) FROM app_user").fetchone()[0])
            if existing_count == 0:
                rows: list[LegacyUserRecord] = []
                for index, (user_id, raw) in enumerate(sorted(auth_users.items())):
                    created = raw.get("created_at")
                    created_ms = (
                        int(created) * 1000
                        if isinstance(created, int) and not isinstance(created, bool)
                        else now_ms + index
                    )
                    rows.append(
                        LegacyUserRecord(
                            id=user_id,
                            display_name=str(raw.get("display_name") or user_id),
                            password_hash=str(raw["password_hash"]),
                            system_role="admin" if user_id == _ADMIN_ID else "user",
                            account_status="active",
                            created_at_ms=created_ms,
                            updated_at_ms=created_ms,
                            password_changed_at_ms=created_ms,
                            must_change_password=False,
                        )
                    )
                users.import_legacy_many(connection, tuple(rows))
                for offset, unit in enumerate(snapshot.teams + snapshot.parts, start=1):
                    organization.create_unit(
                        connection,
                        unit_id=unit.id,
                        kind=unit.kind,
                        parent_id=unit.parent_id,
                        name=unit.name,
                        actor_user_id=_ADMIN_ID,
                        now_ms=now_ms + offset,
                    )
                organization.open_membership(
                    connection,
                    membership_id=ids["membership"],
                    user_id=_ADMIN_ID,
                    part_id=main_part_id,
                    assigned_by=_ADMIN_ID,
                    reason="read-only 10011 organization copy",
                    valid_from_ms=now_ms + 100,
                )
                admin_state = organization.get_user_organization_state(connection, _ADMIN_ID)
                if admin_state is None:
                    raise FullPreviewError("The gold preview admin state is missing.")
                organization.compare_and_swap_user_part(
                    connection,
                    user_id=_ADMIN_ID,
                    expected_current_part_id=None,
                    target_part_id=main_part_id,
                    expected_scope_epoch=admin_state.scope_epoch,
                    expected_row_version=admin_state.row_version,
                    updated_at_ms=now_ms + 100,
                )
                for offset, gold_user in enumerate(snapshot.users, start=1):
                    organization.open_membership(
                        connection,
                        membership_id=gold_user.membership_id,
                        user_id=gold_user.id,
                        part_id=gold_user.part_id,
                        assigned_by=_ADMIN_ID,
                        reason="read-only 10011 organization copy",
                        valid_from_ms=now_ms + 200 + offset,
                    )
                    state = organization.get_user_organization_state(connection, gold_user.id)
                    if state is None:
                        raise FullPreviewError("A gold preview user state is missing.")
                    organization.compare_and_swap_user_part(
                        connection,
                        user_id=gold_user.id,
                        expected_current_part_id=None,
                        target_part_id=gold_user.part_id,
                        expected_scope_epoch=state.scope_epoch,
                        expected_row_version=state.row_version,
                        updated_at_ms=now_ms + 200 + offset,
                    )
                for offset, leader in enumerate(snapshot.part_leaders, start=1):
                    organization.open_part_leader(
                        connection,
                        assignment_id=leader.assignment_id,
                        user_id=leader.user_id,
                        part_id=leader.part_id,
                        assigned_by=_ADMIN_ID,
                        reason="read-only 10011 Part-leader copy",
                        valid_from_ms=now_ms + 400 + offset,
                    )
                for index, (user_id, raw) in enumerate(sorted(auth_users.items())):
                    if not raw.get("active", True):
                        continue
                    ensured = spaces.ensure_personal(
                        connection,
                        space_id=space_id_for_owner("personal", user_id),
                        user_id=user_id,
                        created_at_ms=now_ms + 600 + index,
                    )
                    if not ensured.space.write_enabled:
                        spaces.update_state(
                            connection,
                            space_id=ensured.space.id,
                            expected_row_version=ensured.space.row_version,
                            status="active",
                            write_enabled=True,
                            updated_at_ms=now_ms + 700 + index,
                        )
            elif existing_count != len(auth_users):
                raise FullPreviewError("The gold preview canonical user count drifted.")

            team_spaces: dict[str, str] = {}
            part_spaces: dict[str, str] = {}
            for index, unit in enumerate((snapshot.responsibility,) + snapshot.teams + snapshot.parts):
                if unit.kind == "part":
                    space_id = space_id_for_owner("part", unit.id)
                else:
                    space_id = _uuid(f"gold-{unit.kind}-space:{unit.id}")
                owned = spaces.get_org(connection, unit.id)
                stored = owned or spaces.get_by_id(connection, space_id)
                if stored is None:
                    if unit.kind == "part":
                        stored = spaces.ensure_part(
                            connection,
                            space_id=space_id,
                            part_id=unit.id,
                            created_at_ms=now_ms + 800 + index,
                        ).space
                    else:
                        connection.execute(
                            """INSERT INTO knowledge_space(
                                 id,space_kind,owner_user_id,owner_org_unit_id,status,
                                 write_enabled,generation,created_at,updated_at,row_version
                               ) VALUES (?,?,NULL,?,'active',0,0,?,?,1)""",
                            (space_id, unit.kind, unit.id, now_ms + 800 + index, now_ms + 800 + index),
                        )
                        stored = spaces.require_by_id(connection, space_id)
                if (
                    stored.space_kind != unit.kind
                    or stored.owner_org_unit_id != unit.id
                    or stored.owner_user_id is not None
                    or stored.status != "active"
                ):
                    raise FullPreviewError("A gold preview organization space drifted.")
                if unit.kind == "part" and not stored.write_enabled:
                    stored = spaces.update_state(
                        connection,
                        space_id=stored.id,
                        expected_row_version=stored.row_version,
                        status="active",
                        write_enabled=True,
                        updated_at_ms=now_ms + 900 + index,
                    )
                if unit.kind == "team":
                    team_spaces[unit.id] = stored.id
                elif unit.kind == "part":
                    part_spaces[unit.id] = stored.id
                else:
                    ids["responsibility_space"] = stored.id
            ids["team_spaces"] = team_spaces
            ids["part_spaces"] = part_spaces
            ids["team_space"] = team_spaces[main_team_id]
            ids["part_space"] = part_spaces[main_part_id]
            ids["part_b_space"] = part_spaces[second_part_id]
            for gold_user in snapshot.users:
                stored = users.require(connection, gold_user.id)
                if stored.current_part_id != gold_user.part_id or stored.account_status != "active":
                    raise FullPreviewError("A copied 10011 user membership drifted.")
    finally:
        connection.close()
    _ensure_gold_higher_roles(factory, ids, snapshot)
    return ids


def _ensure_gold_higher_roles(
    factory: object,
    ids: dict[str, Any],
    snapshot: GoldOrganizationSnapshot,
) -> None:
    from code_assist_v1.knowledge_core.db import TransactionMode, transaction
    from code_assist_v1.knowledge_core.repositories.higher_spaces import HigherSpaceRepository
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.higher_authority import (
        HigherAuthorityService,
        SetHigherRoleCommand,
    )

    direct_leaders = _gold_team_leader_ids(snapshot)
    assignments: list[tuple[str, str, str, str]] = []
    for team in snapshot.teams:
        assignments.append(
            (
                team.id,
                ids["team_spaces"][team.id],
                "team_leader",
                direct_leaders[team.id],
            )
        )
    executive = "sbv-responsibility-exec"
    assignments.append(
        (
            snapshot.responsibility.id,
            ids["responsibility_space"],
            "responsibility_executive",
            executive,
        )
    )
    service = HigherAuthorityService(
        factory,
        ScopeResolver(_preview_feature_flags()),
        cursor_secret=b"full-second-brain-preview-gold-higher-v1",
    )
    role_ids: dict[str, str] = {}
    for org_unit_id, space_id, role, user_id in assignments:
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                epoch = UserRepository().require(connection, _ADMIN_ID).scope_epoch
                current = HigherSpaceRepository().get_open_role(
                    connection, org_unit_id=org_unit_id, role=role
                )
                space = SpaceRepository().require_by_id(connection, space_id)
        finally:
            connection.close()
        if current is None:
            result = service.set_role(
                SetHigherRoleCommand(
                    actor_user_id=_ADMIN_ID,
                    actor_scope_epoch=epoch,
                    operation_id=_uuid(f"gold-higher-role-operation:{org_unit_id}:{role}"),
                    request_id=_uuid(f"gold-higher-role-request:{org_unit_id}:{role}"),
                    org_unit_id=org_unit_id,
                    role=role,
                    assignee_user_id=user_id,
                    expected_current_assignment_id=None,
                    expected_current_assignment_generation=None,
                    expected_current_assignment_row_version=None,
                    expected_space_row_version=space.row_version,
                    enable_writes=True,
                    reason="isolated gold-corpus native authority",
                )
            )
            role_id = result.assignment_id
        else:
            if current.user_id != user_id or not space.write_enabled:
                raise FullPreviewError("A gold preview higher role drifted.")
            role_id = current.id
        if role_id is None:
            raise FullPreviewError("A gold preview higher role is missing.")
        role_ids[f"{org_unit_id}:{role}"] = role_id
    ids["gold_higher_role_ids"] = role_ids
    ids["responsibility_executive_user"] = executive
    ids["team_leader_users"] = {
        org_unit_id: user_id
        for org_unit_id, _, role, user_id in assignments
        if role == "team_leader"
    }


def seed_canonical_administration(factory: object, root: Path) -> dict[str, str]:
    from code_assist_v1.knowledge_core.db import transaction
    from code_assist_v1.knowledge_core.repositories.organization import (
        OrganizationRepository,
        RESPONSIBILITY_ROOT_ID,
    )
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository
    from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner

    ids = {
        "responsibility": RESPONSIBILITY_ROOT_ID,
        "team": _uuid("preview-team"),
        "part": _uuid("preview-part"),
        "part_b": _uuid("preview-part-b"),
        "membership": _uuid("preview-membership-admin"),
        "leader": _uuid("preview-leader-admin"),
        "membership_b": _uuid("preview-membership-user01"),
        "leader_b": _uuid("preview-leader-user01"),
        "team_leader_user": _PREVIEW_TEAM_LEADER_ID,
        "responsibility_executive_user": _PREVIEW_RESPONSIBILITY_EXECUTIVE_ID,
    }
    ids["team_leader_users"] = {
        ids["team"]: ids["team_leader_user"],
    }
    now_ms = 1_786_950_000_000
    users = _read_auth_users(root)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = UserRepository()
            organization = OrganizationRepository()
            spaces = SpaceRepository()
            existing = connection.execute("SELECT count(*) FROM app_user").fetchone()
            if existing is not None and int(existing[0]) == 0:
                rows = []
                for index, (user_id, raw) in enumerate(sorted(users.items())):
                    created = raw.get("created_at")
                    created_ms = (
                        int(created) * 1000
                        if isinstance(created, int) and not isinstance(created, bool)
                        else now_ms + index
                    )
                    role = raw.get("role") if raw.get("role") in {"admin", "user"} else "user"
                    rows.append(
                        LegacyUserRecord(
                            id=user_id,
                            display_name=str(raw.get("display_name") or user_id),
                            password_hash=str(raw["password_hash"]),
                            system_role=role,
                            account_status="active" if raw.get("active", True) else "disabled",
                            created_at_ms=created_ms,
                            updated_at_ms=created_ms,
                            password_changed_at_ms=created_ms,
                            must_change_password=bool(raw.get("must_change_password", False)),
                        )
                    )
                repository.import_legacy_many(connection, tuple(rows))
                organization.create_unit(
                    connection,
                    unit_id=ids["team"],
                    kind="team",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name=_PREVIEW_TEAM_NAME,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 10,
                )
                organization.create_unit(
                    connection,
                    unit_id=ids["part"],
                    kind="part",
                    parent_id=ids["team"],
                    name=_PREVIEW_PART_NAME,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 20,
                )
                organization.create_unit(
                    connection,
                    unit_id=ids["part_b"],
                    kind="part",
                    parent_id=ids["team"],
                    name=_PREVIEW_PART_B_NAME,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 21,
                )
                organization.open_membership(
                    connection,
                    membership_id=ids["membership"],
                    user_id=_ADMIN_ID,
                    part_id=ids["part"],
                    assigned_by=_ADMIN_ID,
                    reason="isolated full preview bootstrap",
                    valid_from_ms=now_ms + 30,
                )
                state = organization.get_user_organization_state(connection, _ADMIN_ID)
                if state is None:
                    raise FullPreviewError("The preview admin organization state is missing.")
                organization.compare_and_swap_user_part(
                    connection,
                    user_id=_ADMIN_ID,
                    expected_current_part_id=None,
                    target_part_id=ids["part"],
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=now_ms + 30,
                )
                organization.open_part_leader(
                    connection,
                    assignment_id=ids["leader"],
                    user_id=_ADMIN_ID,
                    part_id=ids["part"],
                    assigned_by=_ADMIN_ID,
                    reason="isolated full preview bootstrap",
                    valid_from_ms=now_ms + 40,
                )
                for user_id, raw in sorted(users.items()):
                    if raw.get("active", True):
                        ensured = spaces.ensure_personal(
                            connection,
                            space_id=space_id_for_owner("personal", user_id),
                            user_id=user_id,
                            created_at_ms=now_ms + 50,
                        )
                        if not ensured.space.write_enabled:
                            spaces.update_state(
                                connection,
                                space_id=ensured.space.id,
                                expected_row_version=ensured.space.row_version,
                                status="active",
                                write_enabled=True,
                                updated_at_ms=now_ms + 51,
                            )
                ensured_part = spaces.ensure_part(
                    connection,
                    space_id=space_id_for_owner("part", ids["part"]),
                    part_id=ids["part"],
                    created_at_ms=now_ms + 50,
                )
                if not ensured_part.space.write_enabled:
                    spaces.update_state(
                        connection,
                        space_id=ensured_part.space.id,
                        expected_row_version=ensured_part.space.row_version,
                        status="active",
                        write_enabled=True,
                        updated_at_ms=now_ms + 51,
                    )
            if organization.get_unit(connection, ids["part_b"]) is None:
                organization.create_unit(
                    connection,
                    unit_id=ids["part_b"],
                    kind="part",
                    parent_id=ids["team"],
                    name=_PREVIEW_PART_B_NAME,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 21,
                )
            research_user = repository.get(connection, "user01")
            if research_user is None or research_user.account_status != "active":
                raise FullPreviewError("The preview requires active user01 for Part B.")
            research_state = organization.get_user_organization_state(
                connection, "user01"
            )
            if research_state is None:
                raise FullPreviewError("The preview user01 organization state is missing.")
            if research_state.current_part_id is None:
                organization.open_membership(
                    connection,
                    membership_id=ids["membership_b"],
                    user_id="user01",
                    part_id=ids["part_b"],
                    assigned_by=_ADMIN_ID,
                    reason="isolated full preview research membership",
                    valid_from_ms=now_ms + 31,
                )
                organization.compare_and_swap_user_part(
                    connection,
                    user_id="user01",
                    expected_current_part_id=None,
                    target_part_id=ids["part_b"],
                    expected_scope_epoch=research_state.scope_epoch,
                    expected_row_version=research_state.row_version,
                    updated_at_ms=now_ms + 31,
                )
            elif research_state.current_part_id != ids["part_b"]:
                raise FullPreviewError("The preview user01 Part B membership drifted.")
            if organization.get_active_part_leader(connection, ids["part_b"]) is None:
                organization.open_part_leader(
                    connection,
                    assignment_id=ids["leader_b"],
                    user_id="user01",
                    part_id=ids["part_b"],
                    assigned_by=_ADMIN_ID,
                    reason="isolated full preview research leader",
                    valid_from_ms=now_ms + 41,
                )
            for key, kind, owner_id in (
                ("responsibility_space", "responsibility", ids["responsibility"]),
                ("team_space", "team", ids["team"]),
                ("part_space", "part", ids["part"]),
                ("part_b_space", "part", ids["part_b"]),
            ):
                owned = spaces.get_org(connection, owner_id)
                space_id = (
                    owned.id
                    if owned is not None
                    else (
                        space_id_for_owner("part", owner_id)
                        if kind == "part"
                        else _uuid(f"preview-{kind}-space:{owner_id}")
                    )
                )
                ids[key] = space_id
                stored = owned or spaces.get_by_id(connection, space_id)
                if stored is None:
                    connection.execute(
                        """INSERT INTO knowledge_space(
                             id,space_kind,owner_user_id,owner_org_unit_id,status,
                             write_enabled,generation,created_at,updated_at,row_version
                           ) VALUES (?,?,NULL,?,'active',0,0,?,?,1)""",
                        (space_id, kind, owner_id, now_ms + 50, now_ms + 50),
                    )
                elif (
                    stored.space_kind != kind
                    or stored.owner_user_id is not None
                    or stored.owner_org_unit_id != owner_id
                    or stored.status != "active"
                ):
                    raise FullPreviewError(
                        f"The canonical preview {kind} space drifted."
                    )
            for user_id, raw in sorted(users.items()):
                if not raw.get("active", True):
                    continue
                personal_id = space_id_for_owner("personal", user_id)
                personal = spaces.get_by_id(connection, personal_id)
                if personal is None:
                    ensured = spaces.ensure_personal(
                        connection,
                        space_id=personal_id,
                        user_id=user_id,
                        created_at_ms=now_ms + 50,
                    )
                    personal = ensured.space
                if personal.status != "active":
                    raise FullPreviewError("A canonical preview personal space is inactive.")
                if not personal.write_enabled:
                    spaces.update_state(
                        connection,
                        space_id=personal.id,
                        expected_row_version=personal.row_version,
                        status="active",
                        write_enabled=True,
                        updated_at_ms=now_ms + 60,
                    )
            for part_space_id in (ids["part_space"], ids["part_b_space"]):
                part_space = spaces.get_by_id(connection, part_space_id)
                if part_space is None:
                    raise FullPreviewError("A canonical preview Part space is missing.")
                if not part_space.write_enabled:
                    spaces.update_state(
                        connection,
                        space_id=part_space.id,
                        expected_row_version=part_space.row_version,
                        status="active",
                        write_enabled=True,
                        updated_at_ms=now_ms + 60,
                    )
            admin = repository.require(connection, _ADMIN_ID)
            if not admin.is_admin or admin.account_status != "active":
                raise FullPreviewError("The canonical preview admin is not active.")
    finally:
        connection.close()
    _ensure_preview_higher_roles(factory, ids)
    return ids


def _ensure_preview_higher_roles(factory: object, ids: dict[str, str]) -> None:
    """Assign exact Team/Responsibility native roles through the v11 service."""

    from code_assist_v1.knowledge_core.db import TransactionMode, transaction
    from code_assist_v1.knowledge_core.repositories.higher_spaces import (
        HigherSpaceRepository,
    )
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.higher_authority import (
        HigherAuthorityService,
        SetHigherRoleCommand,
    )

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            epoch = UserRepository().require(connection, _ADMIN_ID).scope_epoch
    finally:
        connection.close()
    service = HigherAuthorityService(
        factory,
        ScopeResolver(_preview_feature_flags()),
        cursor_secret=b"full-second-brain-preview-higher-cursor-v1",
    )
    for label, org_key, space_key, role, assignee in (
        (
            "team",
            "team",
            "team_space",
            "team_leader",
            ids["team_leader_user"],
        ),
        (
            "responsibility",
            "responsibility",
            "responsibility_space",
            "responsibility_executive",
            ids["responsibility_executive_user"],
        ),
    ):
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                current = HigherSpaceRepository().get_open_role(
                    connection,
                    org_unit_id=ids[org_key],
                    role=role,
                )
                space = SpaceRepository().require_by_id(connection, ids[space_key])
        finally:
            connection.close()
        if current is not None:
            if current.user_id != assignee or not space.write_enabled:
                raise FullPreviewError(f"The preview {label} native role drifted.")
            ids[f"{label}_role"] = current.id
            continue
        result = service.set_role(
            SetHigherRoleCommand(
                actor_user_id=_ADMIN_ID,
                actor_scope_epoch=epoch,
                operation_id=_uuid(f"preview-{label}-role-operation"),
                request_id=_uuid(f"preview-{label}-role-request"),
                org_unit_id=ids[org_key],
                role=role,
                assignee_user_id=assignee,
                expected_current_assignment_id=None,
                expected_current_assignment_generation=None,
                expected_current_assignment_row_version=None,
                expected_space_row_version=space.row_version,
                enable_writes=True,
                reason="isolated full preview native-authority bootstrap",
            )
        )
        if result.assignment_id is None or not result.write_enabled:
            raise FullPreviewError(f"The preview {label} native role was not enabled.")
        ids[f"{label}_role"] = result.assignment_id


def _preview_markdown_title(path: Path, body: str) -> str:
    for line in body.splitlines():
        value = line.strip()
        if value.startswith("# ") and value[2:].strip():
            return value[2:].strip()[:500]
    return path.stem[:500]


def _seed_published_preview_item(
    connection: object,
    *,
    actor_user_id: str,
    body: str,
    label: str,
    space_id: str,
    title: str,
    timestamp_ms: int,
) -> None:
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    item_id = _uuid(f"preview-item:{label}")
    revision_id = _uuid(f"preview-revision:{label}")
    existing = connection.execute(
        """SELECT item.space_id,revision.title,revision.raw_content_sha256,
                  body.content_md
             FROM knowledge_item AS item
             JOIN knowledge_revision AS revision
               ON revision.id=item.current_revision_id
              AND revision.item_id=item.id
             JOIN knowledge_revision_body AS body
               ON body.revision_id=revision.id AND body.item_id=item.id
            WHERE item.id=?""",
        (item_id,),
    ).fetchone()
    if existing is not None:
        if (
            existing["space_id"] != space_id
            or existing["title"] != title
            or existing["raw_content_sha256"] != digest
            or existing["content_md"] != body
        ):
            raise FullPreviewError("A canonical preview knowledge item drifted.")
        return
    operation_id = _uuid(f"preview-operation:{label}")
    dedup_id = _uuid(f"preview-dedup:{label}")
    finding_id = _uuid(f"preview-finding:{label}")
    transition_id = _uuid(f"preview-transition:{label}")
    metadata_json = json.dumps(
        {"preview_source": "isolated_legacy_copy", "schema_version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,?, ?,1)""",
        (
            item_id,
            space_id,
            revision_id,
            actor_user_id,
            actor_user_id,
            timestamp_ms,
            timestamp_ms,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1',?,'initial',
                     'isolated full preview projection',?,?)""",
        (
            revision_id,
            item_id,
            title,
            digest,
            digest,
            len(encoded),
            metadata_json,
            actor_user_id,
            timestamp_ms,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,?)""",
        (revision_id, item_id, body, len(encoded), digest, timestamp_ms),
    )
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,?,NULL,?)""",
        (
            dedup_id,
            actor_user_id,
            operation_id,
            digest,
            timestamp_ms + 1,
            timestamp_ms + 1,
        ),
    )
    connection.execute(
        """INSERT INTO curation_finding(
             id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
             required_reviewer,visibility_effect,proposed_action,source_item_id,
             source_revision_id,target_item_id,target_revision_id,detector_name,
             detector_version,rule_version,model_id,prompt_version,confidence,
             evidence_json,proposed_action_json,reviewed_by,reviewed_at,review_reason,
             applied_operation_id,applied_dedup_id,applied_job_id,applied_by,
             applied_at,last_application_error_json,created_actor_kind,created_by,
             created_at,updated_at,row_version
           ) VALUES (?,?,'curation_release','T2','info','pending','human',
             'space_editor','agent_hidden','publish',?,?,NULL,NULL,
             'full-preview','v1','preview-v1',NULL,NULL,NULL,'{}','{}',
              NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'user',?,?,?,1)""",
        (
            finding_id,
            space_id,
            item_id,
            revision_id,
            actor_user_id,
            timestamp_ms + 2,
            timestamp_ms + 2,
        ),
    )
    connection.execute(
        """UPDATE curation_finding
              SET status='approved',reviewed_by=?,reviewed_at=?,
                  review_reason='isolated preview approval',updated_at=?,row_version=2
            WHERE id=?""",
        (actor_user_id, timestamp_ms + 3, timestamp_ms + 3, finding_id),
    )
    connection.execute(
        """UPDATE curation_finding
              SET status='applied',applied_operation_id=?,applied_dedup_id=?,
                  applied_by=?,applied_at=?,updated_at=?,row_version=3
            WHERE id=?""",
        (
            operation_id,
            dedup_id,
            actor_user_id,
            timestamp_ms + 4,
            timestamp_ms + 4,
            finding_id,
        ),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
                     NULL,?,?,'user',?,2,'isolated preview publish',?)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            actor_user_id,
            timestamp_ms + 4,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item
              SET curation_status='published',last_curation_transition_id=?,
                  updated_by=?,updated_at=?,row_version=2
            WHERE id=?""",
        (transition_id, actor_user_id, timestamp_ms + 4, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,?)""",
        (item_id, space_id, revision_id, title, body, timestamp_ms + 4),
    )


def seed_canonical_preview_knowledge(
    factory: object,
    root: Path,
    organization_ids: Mapping[str, Any],
    *,
    include_fixture_documents: bool = True,
) -> dict[str, int]:
    """Import preserved admin sources and optionally add preview-only fixtures."""

    from code_assist_v1.knowledge_core.db import TransactionMode, transaction
    from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner

    sources = root / "knowledge" / _ADMIN_ID / "wiki" / "sources"
    documents: list[tuple[str, str, str]] = []
    if sources.is_dir():
        for path in sorted(sources.glob("*.md"), key=lambda item: item.name.casefold()):
            body = path.read_text(encoding="utf-8", errors="strict")
            documents.append((path.name, _preview_markdown_title(path, body), body))
    fixtures = (
        (
            _ADMIN_ID,
            "part:shared-space-guide",
            organization_ids["part_space"],
            "Part shared incident knowledge",
            "# Part shared incident knowledge\n\n"
            f"Path: AMHS Responsibility / {_PREVIEW_TEAM_NAME} / {_PREVIEW_PART_NAME}\n\n"
            "The incident response owner checks service health, records mitigation, "
            "and shares verified recovery evidence with the Team.\n",
        ),
        (
            _ADMIN_ID,
            "part:incident-runbook",
            organization_ids["part_space"],
            "Part incident recovery runbook",
            "# Part incident recovery runbook\n\n"
            "For a service health incident, capture the alert time, mitigation owner, "
            "recovery time, and verified evidence. Unrelated design notes stay separate.\n",
        ),
        (
            str(organization_ids.get("part_b_actor", "user01")),
            "part-b:recovery-evidence",
            organization_ids["part_b_space"],
            "Research Part recovery evidence",
            "# Research Part recovery evidence\n\n"
            "The research Part validates service health recovery evidence and publishes "
            "the confirmed incident timeline for cross-Part Team review.\n",
        ),
        (
            str(
                organization_ids.get("team_leader_users", {}).get(
                    organization_ids["team"], _ADMIN_ID
                )
            ),
            "team:native-incident-coordination",
            organization_ids["team_space"],
            "Team native incident coordination",
            "# Team native incident coordination\n\n"
            "The Team leader combines verified incident timelines from descendant Parts, "
            "records cross-Part dependencies, and distributes read-only guidance.\n",
        ),
        (
            str(organization_ids.get("responsibility_executive_user", _ADMIN_ID)),
            "responsibility:native-operating-policy",
            organization_ids["responsibility_space"],
            "Responsibility native operating policy",
            "# Responsibility native operating policy\n\n"
            "The responsibility executive records organization-wide incident policy and "
            "reviews inherited Team evidence without editing descendant originals.\n",
        ),
    )
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            base = int(time.time() * 1000) - 10_000
            personal_space = space_id_for_owner("personal", _ADMIN_ID)
            for index, (filename, title, body) in enumerate(documents):
                _seed_published_preview_item(
                    connection,
                    actor_user_id=_ADMIN_ID,
                    body=body,
                    label=f"personal:{filename}",
                    space_id=personal_space,
                    title=title,
                    timestamp_ms=base + index * 10,
                )
            if include_fixture_documents:
                for offset, (actor, label, space_id, title, body) in enumerate(fixtures):
                    _seed_published_preview_item(
                        connection,
                        actor_user_id=actor,
                        body=body,
                        label=label,
                        space_id=space_id,
                        title=title,
                        timestamp_ms=base + (len(documents) + offset) * 10,
                    )
    finally:
        connection.close()
    return {
        "part": 3 if include_fixture_documents else 0,
        "personal": len(documents),
        "responsibility": 1 if include_fixture_documents else 0,
        "team": 1 if include_fixture_documents else 0,
    }


def seed_gold_corpus(
    factory: object,
    snapshot: GoldOrganizationSnapshot,
    organization_ids: dict[str, Any],
) -> dict[str, int]:
    """Create two Wikis per member plus related and deliberately isolated pages."""

    from code_assist_v1.knowledge_core.db import TransactionMode, transaction
    from code_assist_v1.knowledge_core.repositories.personal_wikis import PersonalWikiProfileRepository
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    from code_assist_v1.knowledge_core.services.personal_wikis import (
        CreatePersonalWikiCommand,
        PersonalWikiLifecycleService,
    )
    from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner

    lifecycle = PersonalWikiLifecycleService(
        factory,
        cursor_secret=b"full-second-brain-preview-gold-wiki-v1",
        new_wiki_write_enabled=True,
    )
    wiki_spaces: dict[str, tuple[str, str]] = {}
    for user in snapshot.users:
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                record = UserRepository().require(connection, user.id)
                default = PersonalWikiProfileRepository().require_exact_default(
                    connection, owner_user_id=user.id
                )
        finally:
            connection.close()
        result = lifecycle.create(
            CreatePersonalWikiCommand(
                actor_user_id=user.id,
                actor_scope_epoch=record.scope_epoch,
                operation_id=_uuid(f"gold-project-wiki-operation:{user.id}"),
                request_id=_uuid(f"gold-project-wiki-request:{user.id}"),
                display_name="프로젝트 Wiki",
                expected_row_version=default.row_version,
            )
        )
        wiki_spaces[user.id] = (default.space_id, result.resource_id)

    parts_by_team: dict[str, list[GoldOrgUnit]] = {team.id: [] for team in snapshot.teams}
    for part in snapshot.parts:
        parts_by_team[str(part.parent_id)].append(part)
    for values in parts_by_team.values():
        values.sort(key=lambda unit: unit.name.casefold())
    users_by_part = organization_ids["gold_users_by_part"]
    team_by_part = {part.id: str(part.parent_id) for part in snapshot.parts}
    responsibility_cluster = "responsibility-amhs-continuity"
    documents: list[tuple[str, str, str, str, str]] = []
    isolated_item_ids: list[str] = []
    related_pairs: list[tuple[str, str]] = []

    for user in snapshot.users:
        for wiki_index, space_id in enumerate(wiki_spaces[user.id], start=1):
            cluster = f"personal-{user.id}-wiki-{wiki_index}"
            answer = f"PERSONAL-{user.id.upper()}-W{wiki_index}-ANSWER"
            first_label = f"gold:personal:{user.id}:wiki-{wiki_index}:related-a"
            second_label = f"gold:personal:{user.id}:wiki-{wiki_index}:related-b"
            isolated_label = f"gold:personal:{user.id}:wiki-{wiki_index}:isolated"
            documents.extend(
                (
                    (
                        user.id,
                        first_label,
                        space_id,
                        f"{user.display_name} Wiki {wiki_index} 설비 기준",
                        f"# {user.display_name} Wiki {wiki_index} 설비 기준\n\n"
                        f"semantic-cluster: {cluster}\n\nanswer-key: {answer}\n\n"
                        "컨베이어 상태등은 청색이고 점검 주기는 14일입니다. 이 기준은 동일 Wiki의 복구 절차와 연결됩니다.\n",
                    ),
                    (
                        user.id,
                        second_label,
                        space_id,
                        f"{user.display_name} Wiki {wiki_index} 복구 절차",
                        f"# {user.display_name} Wiki {wiki_index} 복구 절차\n\n"
                        f"semantic-cluster: {cluster}\n\n"
                        "컨베이어 상태등이 적색이면 전원을 확인하고 기준 문서의 14일 점검 기록을 대조합니다.\n",
                    ),
                    (
                        user.id,
                        isolated_label,
                        space_id,
                        f"{user.display_name} Wiki {wiki_index} 독립 메모",
                        f"# {user.display_name} Wiki {wiki_index} 독립 메모\n\n"
                        f"semantic-cluster: isolated-{user.id}-wiki-{wiki_index}\n\n"
                        "이 문서는 사내 식물 화분의 급수 요일만 기록하며 설비 복구 지식과 관련이 없습니다.\n",
                    ),
                )
            )
            related_pairs.append((_uuid(f"preview-item:{first_label}"), _uuid(f"preview-item:{second_label}")))
            isolated_item_ids.append(_uuid(f"preview-item:{isolated_label}"))

    user_display = {user.id: user.display_name for user in snapshot.users}
    for part in snapshot.parts:
        part_cluster = f"part-{part.id}-operations"
        team_id = team_by_part[part.id]
        team_cluster = f"team-{team_id}-coordination"
        for index, user_id in enumerate(users_by_part[part.id]):
            tags = [part_cluster]
            if index == 0:
                tags.append(team_cluster)
                if parts_by_team[team_id][0].id == part.id:
                    tags.append(responsibility_cluster)
            answer = f"PART-{user_id.upper()}-ANSWER"
            label = f"gold:part:{part.id}:member:{user_id}"
            tag_lines = "\n".join(f"semantic-cluster: {tag}" for tag in tags)
            documents.append(
                (
                    user_id,
                    label,
                    organization_ids["part_spaces"][part.id],
                    f"{part.name} {user_display[user_id]} 운영 기록",
                    f"# {part.name} {user_display[user_id]} 운영 기록\n\n{tag_lines}\n\n"
                    f"answer-key: {answer}\n\n"
                    f"{part.name}의 반송 기준은 3회 연속 센서 불일치이며 담당자는 교대 전에 기록을 공유합니다.\n",
                )
            )
        isolated_label = f"gold:part:{part.id}:isolated"
        isolated_actor = users_by_part[part.id][-1]
        documents.append(
            (
                isolated_actor,
                isolated_label,
                organization_ids["part_spaces"][part.id],
                f"{part.name} 독립 복지 메모",
                f"# {part.name} 독립 복지 메모\n\nsemantic-cluster: isolated-part-{part.id}\n\n"
                "이 문서는 휴게실 도서 교환 일정만 기록하며 AMHS 운영 절차와 관련이 없습니다.\n",
            )
        )
        isolated_item_ids.append(_uuid(f"preview-item:{isolated_label}"))

    for team in snapshot.teams:
        leader = organization_ids["team_leader_users"][team.id]
        cluster = f"team-{team.id}-coordination"
        label = f"gold:team:{team.id}:native"
        documents.append(
            (
                leader,
                label,
                organization_ids["team_spaces"][team.id],
                f"{team.name} 팀장 통합 지침",
                f"# {team.name} 팀장 통합 지침\n\nsemantic-cluster: {cluster}\n\n"
                f"answer-key: TEAM-{team.id.upper()}-ANSWER\n\n"
                "팀장은 하위 Part의 3회 센서 불일치 기록을 결합해 공통 조치 순서를 배포합니다.\n",
            )
        )
        isolated_label = f"gold:team:{team.id}:isolated"
        documents.append(
            (
                leader,
                isolated_label,
                organization_ids["team_spaces"][team.id],
                f"{team.name} 독립 행사 메모",
                f"# {team.name} 독립 행사 메모\n\nsemantic-cluster: isolated-team-{team.id}\n\n"
                "이 문서는 팀 사진 촬영 시간만 다루며 설비 지식과 관련이 없습니다.\n",
            )
        )
        isolated_item_ids.append(_uuid(f"preview-item:{isolated_label}"))

    executive = organization_ids["responsibility_executive_user"]
    responsibility_label = "gold:responsibility:native"
    documents.append(
        (
            executive,
            responsibility_label,
            organization_ids["responsibility_space"],
            "AMHS담당 임원 통합 운영 원칙",
            f"# AMHS담당 임원 통합 운영 원칙\n\nsemantic-cluster: {responsibility_cluster}\n\n"
            "answer-key: RESPONSIBILITY-AMHS-ANSWER\n\n"
            "담당 임원은 각 Team의 연속 센서 불일치 기록을 비교해 조직 공통 재발방지 원칙을 확정합니다.\n",
        )
    )
    responsibility_isolated = "gold:responsibility:isolated"
    documents.append(
        (
            executive,
            responsibility_isolated,
            organization_ids["responsibility_space"],
            "AMHS담당 독립 문화 메모",
            "# AMHS담당 독립 문화 메모\n\nsemantic-cluster: isolated-responsibility-culture\n\n"
            "이 문서는 사내 전시 작품 교체 일정만 기록하며 운영 지식과 관련이 없습니다.\n",
        )
    )
    isolated_item_ids.append(_uuid(f"preview-item:{responsibility_isolated}"))

    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            base = 1_787_400_000_000
            for index, (actor, label, space_id, title, body) in enumerate(documents):
                _seed_published_preview_item(
                    connection,
                    actor_user_id=actor,
                    body=body,
                    label=label,
                    space_id=space_id,
                    title=title,
                    timestamp_ms=base + index * 10,
                )
    finally:
        connection.close()
    organization_ids["gold_wiki_spaces"] = wiki_spaces
    organization_ids["gold_isolated_item_ids"] = tuple(isolated_item_ids)
    organization_ids["gold_related_pairs"] = tuple(related_pairs)
    return {
        "personal_items": len(snapshot.users) * 2 * 3,
        "personal_wikis": len(snapshot.users) * 2,
        "part_items": len(snapshot.parts) * 11,
        "team_items": len(snapshot.teams) * 2,
        "responsibility_items": 2,
        "total_items": len(documents),
    }


def seed_canonical_successor_preview(
    factory: object,
    organization_ids: Mapping[str, Any],
    *,
    semantic_judge: object,
    semantic_model_provider: str,
    semantic_model_name: str,
) -> dict[str, int]:
    """Seed authority/reference behavior and model-judged semantic edges.

    The caller must inject the same class of semantic judge used by the running
    worker.  Test doubles belong in tests; the live preview must never infer
    edges from fixture-only marker text.
    """

    from code_assist_v1.knowledge_core.db import TransactionMode, transaction
    from code_assist_v1.knowledge_core.domain import SemanticIntentProvenanceKind
    from code_assist_v1.knowledge_core.repositories.higher_spaces import (
        HigherSpaceRepository,
    )
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.automatic_semantic_edges import (
        AutomaticSemanticEdgeHandler,
    )
    from code_assist_v1.knowledge_core.services.higher_authority import (
        CreateDistributionCommand,
        HigherAuthorityService,
    )
    from code_assist_v1.knowledge_core.services.semantic_edge_seeding import (
        CreateSemanticSeedRunCommand,
        SemanticIntentMaterializer,
        SemanticIntentSeedService,
    )
    from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry

    ids = organization_ids
    gold_mode = "gold_wiki_spaces" in ids
    team_actor = str(ids.get("team_leader_users", {}).get(ids["team"], _ADMIN_ID))
    team_item = _uuid("preview-item:team:native-incident-coordination")
    team_revision = _uuid("preview-revision:team:native-incident-coordination")
    part_item = _uuid("preview-item:part:shared-space-guide")
    part_revision = _uuid("preview-revision:part:shared-space-guide")
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            epoch = UserRepository().require(connection, team_actor).scope_epoch
            existing_grant = connection.execute(
                """SELECT id,created_audit_event_id,created_outbox_event_id
                     FROM knowledge_distribution_grant
                    WHERE source_space_id=? AND source_item_id=?
                      AND source_revision_id=? AND target_org_unit_id=?
                      AND revoked_at IS NULL
                    ORDER BY id LIMIT 1""",
                (ids["team_space"], team_item, team_revision, ids["part"]),
            ).fetchone()
    finally:
        connection.close()
    higher = HigherAuthorityService(
        factory,
        ScopeResolver(_preview_feature_flags()),
        cursor_secret=b"full-second-brain-preview-higher-cursor-v1",
    )
    if existing_grant is None:
        created = higher.create_distribution(
            CreateDistributionCommand(
                actor_user_id=team_actor,
                actor_scope_epoch=epoch,
                operation_id=_uuid("preview-team-distribution-operation"),
                request_id=_uuid("preview-team-distribution-request"),
                source_space_id=ids["team_space"],
                source_item_id=team_item,
                source_revision_id=team_revision,
                target_org_unit_id=ids["part"],
                target_scope_kind="org_unit",
                target_scope_id=ids["part"],
                reason="isolated preview read-only Team guidance distribution",
            )
        )
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                grant = HigherSpaceRepository().get_distribution(
                    connection, created.grant_id
                )
                if grant is None:
                    raise FullPreviewError("The preview distribution is missing.")
                audit_id = grant.created_audit_event_id
                outbox_id = grant.created_outbox_event_id
        finally:
            connection.close()
    else:
        audit_id = int(existing_grant["created_audit_event_id"])
        outbox_id = int(existing_grant["created_outbox_event_id"])

    reference_id = _uuid("preview-team-explicit-reference")
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            existing_reference = connection.execute(
                "SELECT id FROM knowledge_explicit_reference WHERE id=?",
                (reference_id,),
            ).fetchone()
            if existing_reference is None:
                connection.execute(
                    """INSERT INTO knowledge_explicit_reference(
                         id,source_space_id,source_item_id,source_revision_id,
                         ordinal,syntax_version,target_space_id,target_item_id,
                         target_revision_id,closure_proof_sha256,actor_user_id,
                         operation_id,effect_key,audit_event_id,outbox_event_id,
                         availability_status,unavailable_reason,created_at,
                         updated_at,row_version
                       ) VALUES (?,?,?,?,1,'item-ref-v1',?,?,?,?,?,?,?,?,?,
                                 'available',NULL,?,?,1)""",
                    (
                        reference_id,
                        ids["team_space"],
                        team_item,
                        team_revision,
                        ids["part_space"],
                        part_item,
                        part_revision,
                        _sha256(b"full-preview-reference-closure"),
                        team_actor,
                        _uuid("preview-team-reference-operation"),
                        "full-preview-team-reference-v1",
                        audit_id,
                        outbox_id,
                        1_786_950_100_000,
                        1_786_950_100_000,
                    ),
                )
    finally:
        connection.close()

    seeds = SemanticIntentSeedService(factory)
    run = seeds.create(
        CreateSemanticSeedRunCommand(
            effect_key=(
                "full-preview-semantic-gold-v1"
                if gold_mode
                else "full-preview-semantic-baseline-v1"
            ),
            cause_kind=SemanticIntentProvenanceKind.BASELINE_BACKFILL,
            scope_kind="global",
            scope_id=None,
        )
    )
    initialize_semantic_baseline = run.state != "completed"
    if initialize_semantic_baseline:
        for _ in range(1000):
            progress = seeds.run_batch(run.id, limit=100)
            if progress.state == "completed":
                break
        else:
            raise FullPreviewError("The preview semantic seed did not converge.")

    flags = _preview_feature_flags()
    handler = AutomaticSemanticEdgeHandler(
        factory,
        semantic_judge,
        model_provider=semantic_model_provider,
        model_name=semantic_model_name,
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(handler.registered_handlers()),
        scope_resolver=ScopeResolver(flags),
    )
    if initialize_semantic_baseline:
        materializer = SemanticIntentMaterializer(factory, kernel, flags)
        for _ in range(1000):
            materialized = materializer.run_batch(limit=100)
            if materialized.selected_count == 0:
                break
        else:
            raise FullPreviewError("The preview semantic jobs did not materialize.")
        for _ in range(2000):
            claim = kernel.claim_next(
                claim_owner="full-preview-semantic",
                lease_ms=30_000,
            )
            if claim is None:
                break
            kernel.run_claim(claim)
        else:
            raise FullPreviewError("The preview semantic jobs did not drain.")

    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            local_count = int(
                connection.execute(
                    """SELECT count(*) FROM knowledge_edge
                        WHERE origin='semantic_auto' AND edge_status='active'"""
                ).fetchone()[0]
            )
            aggregate_count = int(
                connection.execute(
                    """SELECT count(*) FROM aggregate_knowledge_edge
                        WHERE origin='semantic_auto' AND edge_status='active'"""
                ).fetchone()[0]
            )
            reference_count = int(
                connection.execute(
                    "SELECT count(*) FROM knowledge_explicit_reference WHERE id=?",
                    (reference_id,),
                ).fetchone()[0]
            )
    finally:
        connection.close()
    if local_count < 1 or aggregate_count < 1 or reference_count != 1:
        raise FullPreviewError(
            "The successor preview lacks local/aggregate auto edges or its reference."
        )
    return {
        "aggregate_auto_edges": aggregate_count,
        "explicit_references": reference_count,
        "local_auto_edges": local_count,
        "read_only_distributions": 1,
    }


def initialize_clean_semantic_baseline(factory: object) -> dict[str, object]:
    """Complete the required baseline using only preserved canonical content.

    Clean validation roots intentionally omit preview fixtures, explicit
    references, and prebuilt edges.  Durable workers still require a completed
    semantic baseline before they may consume publication jobs.  This path
    completes only the durable seed ledger.  The supervised worker then
    materializes and evaluates the resulting intents through its normal lane.
    """

    from code_assist_v1.knowledge_core.domain import SemanticIntentProvenanceKind
    from code_assist_v1.knowledge_core.services.semantic_edge_seeding import (
        CreateSemanticSeedRunCommand,
        SemanticIntentSeedService,
    )

    seeds = SemanticIntentSeedService(factory)
    run = seeds.create(
        CreateSemanticSeedRunCommand(
            effect_key="full-preview-semantic-clean-v1",
            cause_kind=SemanticIntentProvenanceKind.BASELINE_BACKFILL,
            scope_kind="global",
            scope_id=None,
        )
    )
    initialize = run.state != "completed"
    if initialize:
        for _ in range(1000):
            progress = seeds.run_batch(run.id, limit=100)
            if progress.state == "completed":
                break
        else:
            raise FullPreviewError("The clean preview semantic seed did not converge.")

    connection = factory.open(read_only=True)
    try:
        local_count = int(
            connection.execute(
                """SELECT count(*) FROM knowledge_edge
                    WHERE origin='semantic_auto' AND edge_status='active'"""
            ).fetchone()[0]
        )
        aggregate_count = int(
            connection.execute(
                """SELECT count(*) FROM aggregate_knowledge_edge
                    WHERE origin='semantic_auto' AND edge_status='active'"""
            ).fetchone()[0]
        )
        preserved_source_items = int(
            connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0]
        )
    finally:
        connection.close()
    seeds.assert_baseline_complete()
    return {
        "aggregate_auto_edges": aggregate_count,
        "evaluation_lane": "supervised_durable_worker",
        "local_auto_edges": local_count,
        "preserved_source_items": preserved_source_items,
    }


def build_preview_app(factory: object, marker: Mapping[str, object]) -> object:
    from code_assist_v1.app_code import create_app
    from code_assist_v1.knowledge_core.admin_routes import register_admin_routes
    from code_assist_v1.knowledge_core.agent_runtime import (
        canonical_cookie_principal_resolver,
        compose_canonical_agent_runtime,
    )
    from code_assist_v1.knowledge_core.legacy.blob_stager import (
        FilesystemLegacyBlobStager,
    )
    from code_assist_v1.knowledge_core.migrations import MigrationStorageLayout
    from code_assist_v1.knowledge_core.repositories.conversations import (
        ConversationRepository,
    )
    from code_assist_v1.knowledge_core.scope import ScopeResolver
    from code_assist_v1.knowledge_core.services.editing import (
        ImmutableItemEditService,
        LeaseHmacKeyRing,
    )
    from code_assist_v1.knowledge_core.services.lifecycle import ItemLifecycleService
    from code_assist_v1.knowledge_core.services.organization import AccountService, OrganizationService
    from code_assist_v1.knowledge_core.services.publication import (
        CanonicalPublicationSourceLoader,
        PublicationApiService,
        PublicationPreviewExtractionHandler,
        PublicationPreviewService,
        PublicationService,
    )
    from code_assist_v1.knowledge_core.services.rich_publication import (
        CanonicalRichPublicationExtractor,
    )
    from code_assist_v1.knowledge_core.services.curation_worker import (
        CanonicalCurationRescanHandler,
    )
    from code_assist_v1.knowledge_core.services.automatic_semantic_edges import (
        AutomaticSemanticEdgeHandler,
        InternalLLMSemanticJudge,
    )
    from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry
    from code_assist_v1.tools.full_second_brain_preview_workers import (
        preview_semantic_provider_policy,
    )

    worker_supervisors: list[object] = []

    def health() -> tuple[Mapping[str, object], int]:
        payload: dict[str, object] = {
            "company_api_called": False,
            "data_mode": "isolated_legacy_copy_with_canonical_admin",
            "marker_sha256": marker.get("marker_sha256"),
            "report_schema": _SCHEMA,
            "schema_version": _SCHEMA_VERSION,
            "status": "ready",
        }
        if not worker_supervisors:
            return payload, 200
        status_method = getattr(worker_supervisors[0], "status", None)
        if not callable(status_method):
            payload["status"] = "degraded"
            return payload, 503
        worker_status = status_method()
        payload["workers"] = worker_status
        children = (
            worker_status.get("children")
            if isinstance(worker_status, Mapping)
            else None
        )
        healthy = (
            isinstance(children, list)
            and {child.get("kind") for child in children if isinstance(child, Mapping)}
            == {"durable", "projector"}
            and all(
                child.get("alive") is True
                for child in children
                if isinstance(child, Mapping)
            )
        )
        if not healthy:
            payload["status"] = "degraded"
            return payload, 503
        return payload, 200

    class _PreviewPublicationApi:
        """Run upload extraction and preview curation inline only on port 10111."""

        def __init__(self, base, kernel, *, extraction_key, curation_key):
            self._base = base
            self._kernel = kernel
            self._extraction_key = extraction_key
            self._curation_key = curation_key

        def create_manual_preview(self, command):
            return self._base.create_manual_preview(command)

        def create_upload_preview(self, command):
            queued = self._base.create_upload_preview(command)
            for _ in range(4):
                self._kernel.recover_expired()
                claim = self._kernel.claim_next(
                    claim_owner="full-preview-inline-publication",
                    lease_ms=30_000,
                    handler_keys=(self._extraction_key,),
                )
                if claim is None:
                    break
                self._kernel.run_claim(claim)
                if claim.job.id == queued.job_id:
                    break
            return queued

        def create_chat_preview(self, command):
            return self._base.create_chat_preview(command)

        def create_notion_preview(self, command):
            return self._base.create_notion_preview(command)

        def get_preview(self, query):
            return self._base.get_preview(query)

        def cancel_preview(self, command):
            return self._base.cancel_preview(command)

        def publish(self, command):
            result = self._base.publish(command)
            for _ in range(8):
                self._kernel.recover_expired()
                claim = self._kernel.claim_next(
                    claim_owner="full-preview-inline-curation",
                    lease_ms=30_000,
                    handler_keys=(self._curation_key,),
                )
                if claim is None:
                    break
                self._kernel.run_claim(claim)
            return result

    preview_root = Path(str(marker["data_root"]))
    layout = MigrationStorageLayout(
        data_root=preview_root,
        database=preview_root / "core" / "knowledge_core.db",
        blob_root=preview_root / "blobs",
        projection_root=preview_root / "projections",
    )
    stager = FilesystemLegacyBlobStager(layout)
    principal_resolver = canonical_cookie_principal_resolver(factory)
    flags = _preview_feature_flags()
    scope_resolver = ScopeResolver(flags)
    conversation_repository = ConversationRepository(
        allow_higher_spaces=True,
        conversation_wiki_binding=True,
    )
    codex_only = os.environ.get("CODE_ASSIST_ENABLE_CODEX_BRIDGE") == "1"
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=flags,
        principal_resolver=principal_resolver,
        scope_resolver=scope_resolver,
        conversation_repository=conversation_repository,
        provider_order=("codex",) if codex_only else None,
        model_ids=("codex-local",) if codex_only else None,
        personal_wiki_cursor_secret=(
            b"full-second-brain-preview-personal-wiki-cursor-v1"
        ),
        higher_authority_cursor_secret=(
            b"full-second-brain-preview-higher-cursor-v1"
        ),
        source_artifact_stager=stager,
    )
    from code_assist_v1.knowledge_core.services.source_artifacts import (
        SourceArtifactService,
    )
    preview_service = PublicationPreviewService(factory, scope_resolver, stager)
    extraction_handler = PublicationPreviewExtractionHandler(
        factory,
        CanonicalPublicationSourceLoader(factory, stager),
        CanonicalRichPublicationExtractor(),
        asset_stager=stager,
    )
    curation_handler = CanonicalCurationRescanHandler(
        factory,
        scope_resolver=scope_resolver,
    )
    provider_policy = preview_semantic_provider_policy(
        codex_validation=codex_only
    )
    semantic_handler = AutomaticSemanticEdgeHandler(
        factory,
        InternalLLMSemanticJudge(
            provider_order=provider_policy.provider_order,
            model_ids=provider_policy.model_ids,
        ),
        model_provider=provider_policy.model_provider,
        model_name=provider_policy.model_name,
    )
    extraction_registered = extraction_handler.registered_handler()
    curation_registered = curation_handler.registered_handler()
    inline_kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                extraction_registered,
                curation_registered,
                *semantic_handler.registered_handlers(),
            )
        ),
        scope_resolver=scope_resolver,
    )
    publication = _PreviewPublicationApi(
        PublicationApiService(
            preview_service,
            PublicationService(factory, scope_resolver),
        ),
        inline_kernel,
        extraction_key=extraction_registered.key,
        curation_key=curation_registered.key,
    )
    lease_key = hashlib.sha256(
        b"full-second-brain-preview-lease-v1\0"
        + str(marker["marker_sha256"]).encode("ascii")
    ).digest()
    services = replace(
        runtime.services,
        publication=publication,
        editing=ImmutableItemEditService(
            factory,
            key_ring=LeaseHmacKeyRing(
                active_key_id="full-preview-v1",
                keys={"full-preview-v1": lease_key},
            ),
            scope_resolver=scope_resolver,
        ),
        lifecycle=ItemLifecycleService(factory, scope_resolver),
        management_reads=runtime.services.require("management_reads"),
        retrieval=runtime.services.require("retrieval"),
        source_artifacts=SourceArtifactService(
            factory,
            scope_resolver=scope_resolver,
            stager=stager,
        ),
    )
    runtime = replace(runtime, services=services)
    environment = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        # The regular production rollout flags remain closed.  This isolated
        # host attaches its fully injected route set explicitly below.
        "KNOWLEDGE_CORE_READS": "0",
        # Global mutation flags stay closed; publication routes are attached
        # explicitly below to this isolated marker-bound host only.
        "KNOWLEDGE_CORE_PERSONAL_WRITES": "0",
        "KNOWLEDGE_CORE_PART_READS": "0",
        # Production rollout flags stay closed globally.  The marker-bound
        # canonical runtime above receives its explicit preview-only flags.
        "KNOWLEDGE_CORE_PART_WRITES": "0",
        "KNOWLEDGE_CORE_PROJECTIONS": "0",
        "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": "0",
        # Higher-space traffic is likewise opened only in the injected
        # canonical services, never through the process environment.
        "KNOWLEDGE_CORE_HIGHER_SPACES": "0",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "0",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": "0",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": "0",
    }
    app = create_app(
        knowledge_core_services=runtime.services,
        knowledge_core_route_bindings=runtime.route_bindings,
        knowledge_core_environ=environment,
        production_health_provider=health,
    )
    from code_assist_v1.knowledge_core.canonical_routes import (
        register_canonical_routes,
    )

    register_canonical_routes(
        app,
        scope_selection_service=runtime.services.require("scope_selection"),
        conversation_service=runtime.services.require("conversations"),
        retrieval_service=runtime.services.require("retrieval"),
        management_read_service=runtime.services.require("management_reads"),
        agent_context_service=runtime.services.require("agent_context"),
        turn_executor=runtime.route_bindings.turn_executor,
        conversation_summarizer=runtime.route_bindings.conversation_summarizer,
        principal_resolver=principal_resolver,
        publication_service=runtime.services.require("publication"),
        item_edit_service=runtime.services.require("editing"),
        item_lifecycle_service=runtime.services.require("lifecycle"),
        item_purge_service=None,
        curation_service=None,
        job_service=None,
        job_mutations_enabled=False,
        source_artifact_service=runtime.services.require("source_artifacts"),
    )
    from code_assist_v1.knowledge_core.personal_wiki_routes import (
        register_personal_wiki_routes,
    )

    register_personal_wiki_routes(
        app,
        service=runtime.services.require("personal_wikis"),
        search_service=runtime.services.require("personal_wiki_search"),
        principal_resolver=principal_resolver,
        writes_enabled=True,
    )
    # This isolated host mounts canonical routes explicitly after create_app;
    # use the same integration composition hook as the production app host.
    from code_assist_v1.agent_integrations.routes import register_from_core
    from code_assist_v1.knowledge_core import get_knowledge_core
    register_from_core(app, get_knowledge_core(app))
    from code_assist_v1.knowledge_core.higher_space_routes import (
        register_higher_space_routes,
    )
    from code_assist_v1.knowledge_core.semantic_edge_routes import (
        register_semantic_edge_routes,
    )
    from code_assist_v1.knowledge_core.temporal_routes import (
        register_temporal_routes,
    )

    register_higher_space_routes(
        app,
        service=runtime.services.require("higher_authority"),
        principal_resolver=principal_resolver,
    )
    register_temporal_routes(
        app,
        service=runtime.services.require("temporal"),
        principal_resolver=principal_resolver,
    )
    register_semantic_edge_routes(
        app,
        service=runtime.services.require("semantic_edge_correction"),
        principal_resolver=principal_resolver,
    )
    # create_app registered the legacy compatibility surface because the
    # global rollout flags above remain closed.  The explicit canonical routes
    # coexist under /api/knowledge/v1 and all writes stay below the copied root.
    app.extensions["full_second_brain_preview_runtime"] = runtime
    app.extensions["full_second_brain_preview_worker_supervisors"] = (
        worker_supervisors
    )
    from flask import request

    @app.after_request
    def _mark_full_preview(response):
        if request.path == "/" and response.mimetype == "text/html":
            response.direct_passthrough = False
            body = response.get_data(as_text=True)
            body = body.replace(
                '<html lang="ko">',
                '<html lang="ko" data-keep-legacy-graph="true">',
                1,
            )
            response.set_data(body)
        return response

    secret = app.secret_key
    secret_bytes = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    preview_key = hashlib.sha256(b"full-preview-admin-v1\0" + secret_bytes).digest()
    register_admin_routes(
        app,
        OrganizationService(factory, preview_hmac_key=preview_key, flags=flags),
        AccountService(factory),
        principal_resolver,
    )
    return app


def run_functional_gate(app: object, *, password: str, port: int) -> dict[str, object]:
    client = app.test_client()
    origin = f"http://127.0.0.1:{port}"
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": _ADMIN_ID, "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if login.status_code != 200:
        raise FullPreviewError(f"Preview login failed with HTTP {login.status_code}.")
    models = client.get("/api/code/models", base_url=origin)
    knowledge = client.get("/api/code/knowledge/list", base_url=origin)
    graph = client.get("/api/code/knowledge/graph", base_url=origin)
    index = client.get("/api/code/knowledge/system_doc/index", base_url=origin)
    organization = client.get("/api/admin/v1/org/tree", base_url=origin)
    for name, response in (
        ("models", models),
        ("knowledge", knowledge),
        ("graph", graph),
        ("index", index),
        ("organization", organization),
    ):
        if response.status_code != 200:
            raise FullPreviewError(f"The {name} gate failed with HTTP {response.status_code}.")
    model_payload = models.get_json(silent=True) or {}
    api_rows = model_payload.get("api") or []
    codex_rows = model_payload.get("codex") or []
    priority = model_payload.get("priority") or []
    if len(api_rows) < 3 or not codex_rows or priority[-1:] != ["codex-local"]:
        raise FullPreviewError("The normal API/GGUF/Codex model ordering is not visible.")
    serialized_knowledge = json.dumps(knowledge.get_json(silent=True), ensure_ascii=False)
    serialized_graph = json.dumps(graph.get_json(silent=True), ensure_ascii=False)
    if len(serialized_knowledge) < 20 or len(serialized_graph) < 20:
        raise FullPreviewError("The copied knowledge or graph surface is empty.")
    org_payload = organization.get_json(silent=True) or {}
    if not isinstance(org_payload.get("users"), list) or not org_payload["users"]:
        raise FullPreviewError("The canonical organization admin snapshot is empty.")
    return {
        "api_model_count": len(api_rows),
        "codex_model_count": len(codex_rows),
        "graph_http": graph.status_code,
        "index_http": index.status_code,
        "knowledge_http": knowledge.status_code,
        "organization_user_count": len(org_payload["users"]),
        "status": "pass",
    }


def run_gold_functional_gate(
    app: object,
    *,
    password: str,
    port: int,
    snapshot: GoldOrganizationSnapshot,
    organization_ids: Mapping[str, Any],
) -> dict[str, object]:
    """Exercise every personal Wiki and every unique organization view over HTTP."""

    origin = f"http://127.0.0.1:{port}"
    client = app.test_client()
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": _ADMIN_ID, "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if login.status_code != 200:
        raise FullPreviewError("The gold gate could not establish its CSRF session.")
    csrf = (login.get_json(silent=True) or {}).get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise FullPreviewError("The gold gate login did not issue a CSRF token.")
    first_user_login = app.test_client().post(
        "/api/code/auth/login",
        json={"user_id": snapshot.users[0].id, "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if first_user_login.status_code != 200:
        raise FullPreviewError("A copied 10011 validation account cannot log in.")

    def assume(user_id: str, display_name: str, *, admin: bool = False) -> None:
        with client.session_transaction(base_url=origin) as session:
            session["code_assist_user"] = {
                "display_name": display_name,
                "id": user_id,
                "is_admin": admin,
                "login_at": 1_787_400_000,
                "must_change_password": False,
                "role": "admin" if admin else "user",
            }

    def get(path: str, label: str):
        response = client.get(
            path,
            headers={"X-Request-ID": _uuid(f"gold-http-get:{label}")},
            base_url=origin,
        )
        if response.status_code != 200:
            raise FullPreviewError(f"The gold {label} read failed with HTTP {response.status_code}.")
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict):
            raise FullPreviewError(f"The gold {label} read returned no JSON object.")
        return payload

    def post(path: str, payload: Mapping[str, object], label: str, expected: tuple[int, ...] = (200, 201)):
        response = client.post(
            path,
            json=dict(payload),
            headers={
                "Origin": origin,
                "X-CSRF-Token": csrf,
                "X-Request-ID": _uuid(f"gold-http-post:{label}"),
            },
            base_url=origin,
        )
        if response.status_code not in expected:
            raise FullPreviewError(
                f"The gold {label} mutation failed with HTTP {response.status_code}: "
                + json.dumps(response.get_json(silent=True), ensure_ascii=False)
            )
        result = response.get_json(silent=True)
        if not isinstance(result, dict):
            raise FullPreviewError(f"The gold {label} mutation returned no JSON object.")
        return result

    personal_graphs = 0
    personal_sessions = 0
    personal_searches = 0
    organization_sessions = 0
    peer_sharing_checks = 0
    higher_native_publications = 0
    default_wikis: dict[str, str] = {}
    user_display = {user.id: user.display_name for user in snapshot.users}
    for user in snapshot.users:
        assume(user.id, user.display_name)
        base_spaces = get("/api/knowledge/v1/spaces", f"spaces:{user.id}")["items"]
        if [row.get("kind") for row in base_spaces] != [
            "personal",
            "part",
            "team",
            "responsibility",
        ]:
            raise FullPreviewError("A gold user does not resolve to all four scope levels.")
        wiki_page = get(
            "/api/knowledge/v2/personal-wikis?lifecycle_status=active&limit=50",
            f"wikis:{user.id}",
        )
        wikis = wiki_page.get("items")
        if not isinstance(wikis, list) or len(wikis) != 2:
            raise FullPreviewError("A gold user does not have exactly two active Wiki fixtures.")
        default = next((row for row in wikis if row.get("is_default") is True), None)
        if not isinstance(default, dict):
            raise FullPreviewError("A gold user has no exact default Wiki.")
        default_wikis[user.id] = str(default["space_id"])
        for wiki_index, profile in enumerate(
            sorted(wikis, key=lambda row: str(row.get("space_id"))), start=1
        ):
            space_id = str(profile["space_id"])
            exact = get(
                f"/api/knowledge/v1/spaces?selected_space_id={space_id}",
                f"exact:{user.id}:{wiki_index}",
            )
            expected_option_count = 4 if profile.get("is_default") is True else 5
            if len(exact.get("items") or []) != expected_option_count:
                raise FullPreviewError("An exact gold Wiki is not selectable.")
            items = get(
                f"/api/knowledge/v1/spaces/{space_id}/items?limit=100",
                f"items:{user.id}:{wiki_index}",
            ).get("items")
            if not isinstance(items, list) or len(items) < 3:
                raise FullPreviewError("A gold personal Wiki does not contain its three fixtures.")
            graph = get(
                f"/api/knowledge/v1/spaces/{space_id}/graph",
                f"graph:{user.id}:{wiki_index}",
            )
            nodes = graph.get("nodes")
            edges = graph.get("edges")
            if (
                not isinstance(nodes, list)
                or not isinstance(edges, list)
                or len(nodes) < 3
                or len(edges) < 1
            ):
                raise FullPreviewError(
                    "A gold personal Wiki graph lacks its 3 nodes / 1 related edge: "
                    + json.dumps(
                        {
                            "space_id": space_id,
                            "is_default": profile.get("is_default"),
                            "nodes": None if not isinstance(nodes, list) else len(nodes),
                            "edges": None if not isinstance(edges, list) else len(edges),
                            "keys": sorted(graph),
                        },
                        ensure_ascii=False,
                    )
                )
            personal_graphs += 1
            activity = get(
                f"/api/knowledge/v1/spaces/{space_id}/activity?limit=100",
                f"activity:{user.id}:{wiki_index}",
            )
            if not isinstance(activity.get("items"), list) or not activity["items"]:
                raise FullPreviewError("A gold personal Wiki Log is empty.")
            get(
                f"/api/knowledge/v1/spaces/{space_id}/projection-status",
                f"index-status:{user.id}:{wiki_index}",
            )
            source_wiki_index = 1 if profile.get("is_default") is True else 2
            answer = f"PERSONAL-{user.id.upper()}-W{source_wiki_index}-ANSWER"
            search = post(
                f"/api/knowledge/v1/spaces/{space_id}/search",
                {"query": answer, "limit": 10},
                f"search:{user.id}:{wiki_index}",
            )
            results = search.get("items")
            if (
                not isinstance(results, list)
                or not results
                or results[0].get("source_space_id") != space_id
            ):
                raise FullPreviewError("A gold personal Wiki search escaped its exact Wiki.")
            personal_searches += 1
            conversation = post(
                "/api/knowledge/v1/conversations",
                {
                    "operation_id": _uuid(f"gold-conversation-operation:{user.id}:{space_id}"),
                    "space_id": space_id,
                    "title": f"Gold Wiki {wiki_index}",
                },
                f"conversation:{user.id}:{wiki_index}",
                (201,),
            )
            conversation_id = str(conversation.get("resource_id"))
            detail = get(
                f"/api/knowledge/v1/conversations/{conversation_id}",
                f"conversation-detail:{user.id}:{wiki_index}",
            )
            if (detail.get("conversation") or {}).get("space", {}).get("id") != space_id:
                raise FullPreviewError("A gold conversation lost its exact Wiki binding.")
            personal_sessions += 1

    part_views = 0
    team_views = 0
    for part in snapshot.parts:
        members = organization_ids["gold_users_by_part"][part.id]
        representative = members[0]
        assume(representative, user_display[representative])
        space_id = organization_ids["part_spaces"][part.id]
        page = get(f"/api/knowledge/v1/spaces/{space_id}/items?limit=100", f"part-items:{part.id}")
        if len(page.get("items") or []) < 11:
            raise FullPreviewError("A gold Part does not expose all member knowledge.")
        graph = get(f"/api/knowledge/v1/spaces/{space_id}/graph", f"part-graph:{part.id}")
        if not isinstance(graph.get("edges"), list) or len(graph["edges"]) < 9:
            raise FullPreviewError("A gold Part graph lacks its local related edges.")
        get(f"/api/knowledge/v1/spaces/{space_id}/activity?limit=100", f"part-log:{part.id}")
        get(f"/api/knowledge/v1/spaces/{space_id}/projection-status", f"part-index:{part.id}")
        part_views += 1

        preview = post(
            "/api/knowledge/v1/publication-previews",
            {
                "markdown": f"# {part.name} 동시 발행 검증\n\nGOLD-BOTH-{part.id}\n",
                "operation_id": _uuid(f"gold-both-preview-operation:{part.id}"),
                "source_type": "manual",
                "title": f"{part.name} 동시 발행 검증",
            },
            f"both-preview:{part.id}",
        )
        if preview.get("status") not in {"ready", "consumed"}:
            raise FullPreviewError(
                "A gold both-publication preview is neither ready nor consumed."
            )
        committed = post(
            "/api/knowledge/v1/publications",
            {
                "operation_id": _uuid(f"gold-both-commit-operation:{part.id}"),
                "personal_space_id": default_wikis[representative],
                "preview_id": preview["preview_id"],
                "source_snapshot_sha256": preview["source_snapshot_sha256"],
                "target_mode": "both",
            },
            f"both-commit:{part.id}",
            (201,),
        )
        results = committed.get("results")
        if (
            not isinstance(results, list)
            or len(results) != 2
            or len({row.get("item_id") for row in results}) != 2
            or {row.get("space_id") for row in results}
            != {default_wikis[representative], space_id}
        ):
            raise FullPreviewError("Personal+Part did not create two independent items.")

        peer = members[1]
        assume(peer, user_display[peer])
        peer_search = post(
            f"/api/knowledge/v1/spaces/{space_id}/search",
            {"query": f"GOLD-BOTH-{part.id}", "limit": 10},
            f"part-peer-search:{part.id}",
        )
        peer_results = peer_search.get("items")
        if (
            not isinstance(peer_results, list)
            or not peer_results
            or peer_results[0].get("source_space_id") != space_id
        ):
            raise FullPreviewError("A Part peer cannot retrieve a shared Part publication.")
        peer_sharing_checks += 1
        part_conversation = post(
            "/api/knowledge/v1/conversations",
            {
                "operation_id": _uuid(f"gold-part-conversation-operation:{part.id}"),
                "space_id": space_id,
                "title": f"{part.name} shared session",
            },
            f"part-conversation:{part.id}",
            (201,),
        )
        part_conversation_id = str(part_conversation.get("resource_id"))
        part_detail = get(
            f"/api/knowledge/v1/conversations/{part_conversation_id}",
            f"part-conversation-detail:{part.id}",
        )
        if (part_detail.get("conversation") or {}).get("space", {}).get("id") != space_id:
            raise FullPreviewError("A Part conversation lost its exact scope binding.")
        organization_sessions += 1

    for team in snapshot.teams:
        leader = organization_ids["team_leader_users"][team.id]
        assume(leader, user_display[leader])
        space_id = organization_ids["team_spaces"][team.id]
        scope_items = get("/api/knowledge/v1/spaces", f"team-scopes:{team.id}")["items"]
        scope_option = next((row for row in scope_items if row.get("id") == space_id), None)
        target = (
            None
            if not isinstance(scope_option, dict)
            else (scope_option.get("native_authority") or {}).get("publication_target")
        )
        if not isinstance(target, dict):
            raise FullPreviewError("A Team leader has no native publication target.")
        native_key = f"GOLD-TEAM-NATIVE-{team.id}"
        native_preview = post(
            "/api/knowledge/v1/publication-previews",
            {
                "markdown": (
                    f"# {team.name} leader publication\n\n"
                    f"semantic-cluster: team-{team.id}-coordination\n\n"
                    f"answer-key: {native_key}\n"
                ),
                "operation_id": _uuid(f"gold-team-native-preview-operation:{team.id}"),
                "source_type": "manual",
                "target": target,
                "title": f"{team.name} leader publication",
            },
            f"team-native-preview:{team.id}",
        )
        native_commit = post(
            "/api/knowledge/v1/publications",
            {
                "operation_id": _uuid(f"gold-team-native-commit-operation:{team.id}"),
                "preview_id": native_preview["preview_id"],
                "source_snapshot_sha256": native_preview["source_snapshot_sha256"],
                "target_mode": "explicit",
                "target": target,
            },
            f"team-native-commit:{team.id}",
            (201,),
        )
        if {row.get("space_id") for row in native_commit.get("results") or []} != {space_id}:
            raise FullPreviewError("A Team leader publication did not remain Team-native.")
        higher_native_publications += 1
        page = get(f"/api/knowledge/v1/spaces/{space_id}/items?limit=100", f"team-items:{team.id}")
        if len(page.get("items") or []) < 24:
            raise FullPreviewError("A gold Team does not combine its descendant Part knowledge.")
        graph = get(f"/api/knowledge/v1/spaces/{space_id}/graph", f"team-graph:{team.id}")
        if not isinstance(graph.get("edges"), list) or len(graph["edges"]) < 1:
            raise FullPreviewError("A gold Team graph has no aggregate edge.")
        get(f"/api/knowledge/v1/spaces/{space_id}/activity?limit=100", f"team-log:{team.id}")
        get(f"/api/knowledge/v1/spaces/{space_id}/projection-status", f"team-index:{team.id}")
        team_views += 1

        descendant_parts = [part for part in snapshot.parts if part.parent_id == team.id]
        peer = organization_ids["gold_users_by_part"][descendant_parts[-1].id][-1]
        assume(peer, user_display[peer])
        team_search = post(
            f"/api/knowledge/v1/spaces/{space_id}/search",
            {"query": native_key, "limit": 10},
            f"team-peer-search:{team.id}",
        )
        team_results = team_search.get("items")
        if (
            not isinstance(team_results, list)
            or not team_results
            or team_results[0].get("source_space_id") != space_id
        ):
            raise FullPreviewError("A Team member cannot retrieve the Team leader publication.")
        peer_sharing_checks += 1
        team_conversation = post(
            "/api/knowledge/v1/conversations",
            {
                "operation_id": _uuid(f"gold-team-conversation-operation:{team.id}"),
                "space_id": space_id,
                "title": f"{team.name} shared session",
            },
            f"team-conversation:{team.id}",
            (201,),
        )
        team_conversation_id = str(team_conversation.get("resource_id"))
        team_detail = get(
            f"/api/knowledge/v1/conversations/{team_conversation_id}",
            f"team-conversation-detail:{team.id}",
        )
        if (team_detail.get("conversation") or {}).get("space", {}).get("id") != space_id:
            raise FullPreviewError("A Team conversation lost its exact scope binding.")
        organization_sessions += 1

    executive = str(organization_ids["responsibility_executive_user"])
    assume(executive, user_display[executive])
    responsibility_space = str(organization_ids["responsibility_space"])
    responsibility_options = get(
        "/api/knowledge/v1/spaces", "responsibility-scopes"
    )["items"]
    responsibility_option = next(
        (row for row in responsibility_options if row.get("id") == responsibility_space),
        None,
    )
    responsibility_target = (
        None
        if not isinstance(responsibility_option, dict)
        else (responsibility_option.get("native_authority") or {}).get(
            "publication_target"
        )
    )
    if not isinstance(responsibility_target, dict):
        raise FullPreviewError("The Responsibility executive has no native publication target.")
    responsibility_key = "GOLD-RESPONSIBILITY-NATIVE"
    responsibility_preview = post(
        "/api/knowledge/v1/publication-previews",
        {
            "markdown": (
                "# AMHS responsibility executive publication\n\n"
                "semantic-cluster: responsibility-amhs-continuity\n\n"
                f"answer-key: {responsibility_key}\n"
            ),
            "operation_id": _uuid("gold-responsibility-native-preview-operation"),
            "source_type": "manual",
            "target": responsibility_target,
            "title": "AMHS responsibility executive publication",
        },
        "responsibility-native-preview",
    )
    responsibility_commit = post(
        "/api/knowledge/v1/publications",
        {
            "operation_id": _uuid("gold-responsibility-native-commit-operation"),
            "preview_id": responsibility_preview["preview_id"],
            "source_snapshot_sha256": responsibility_preview[
                "source_snapshot_sha256"
            ],
            "target_mode": "explicit",
            "target": responsibility_target,
        },
        "responsibility-native-commit",
        (201,),
    )
    if {
        row.get("space_id") for row in responsibility_commit.get("results") or []
    } != {responsibility_space}:
        raise FullPreviewError(
            "The executive publication did not remain Responsibility-native."
        )
    higher_native_publications += 1
    responsibility_items = get(
        f"/api/knowledge/v1/spaces/{responsibility_space}/items?limit=100",
        "responsibility-items",
    )
    if len(responsibility_items.get("items") or []) < 80:
        raise FullPreviewError("The gold Responsibility does not combine all Team knowledge.")
    responsibility_graph = get(
        f"/api/knowledge/v1/spaces/{responsibility_space}/graph",
        "responsibility-graph",
    )
    if (
        not isinstance(responsibility_graph.get("edges"), list)
        or len(responsibility_graph["edges"]) < 2
    ):
        raise FullPreviewError("The gold Responsibility graph lacks cross-Team edges.")
    get(
        f"/api/knowledge/v1/spaces/{responsibility_space}/activity?limit=100",
        "responsibility-log",
    )
    get(
        f"/api/knowledge/v1/spaces/{responsibility_space}/projection-status",
        "responsibility-index",
    )
    responsibility_peer = snapshot.users[-1].id
    assume(responsibility_peer, user_display[responsibility_peer])
    responsibility_search = post(
        f"/api/knowledge/v1/spaces/{responsibility_space}/search",
        {"query": responsibility_key, "limit": 10},
        "responsibility-peer-search",
    )
    responsibility_results = responsibility_search.get("items")
    if (
        not isinstance(responsibility_results, list)
        or not responsibility_results
        or responsibility_results[0].get("source_space_id") != responsibility_space
    ):
        raise FullPreviewError(
            "A descendant member cannot retrieve the executive publication."
        )
    peer_sharing_checks += 1
    responsibility_conversation = post(
        "/api/knowledge/v1/conversations",
        {
            "operation_id": _uuid("gold-responsibility-conversation-operation"),
            "space_id": responsibility_space,
            "title": "AMHS responsibility shared session",
        },
        "responsibility-conversation",
        (201,),
    )
    responsibility_conversation_id = str(responsibility_conversation.get("resource_id"))
    responsibility_detail = get(
        f"/api/knowledge/v1/conversations/{responsibility_conversation_id}",
        "responsibility-conversation-detail",
    )
    if (
        (responsibility_detail.get("conversation") or {}).get("space", {}).get("id")
        != responsibility_space
    ):
        raise FullPreviewError("A Responsibility conversation lost its exact scope binding.")
    organization_sessions += 1
    return {
        "company_api_called": False,
        "higher_native_publications": higher_native_publications,
        "organization_shape": "1/3/7",
        "organization_sessions": organization_sessions,
        "part_views": part_views,
        "peer_sharing_checks": peer_sharing_checks,
        "personal_graphs": personal_graphs,
        "personal_searches": personal_searches,
        "personal_sessions": personal_sessions,
        "team_views": team_views,
        "users": len(snapshot.users),
        "status": "pass",
    }


def audit_gold_graph_contract(
    factory: object,
    organization_ids: Mapping[str, Any],
) -> dict[str, object]:
    """Prove local isolation, aggregate placement, and detached fixtures."""

    from code_assist_v1.knowledge_core.db import TransactionMode, transaction

    wiki_spaces = tuple(
        space_id
        for user_spaces in organization_ids["gold_wiki_spaces"].values()
        for space_id in user_spaces
    )
    part_spaces = tuple(organization_ids["part_spaces"].values())
    isolated_items = tuple(organization_ids["gold_isolated_item_ids"])
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            if len(wiki_spaces) != 140 or len(part_spaces) != 7:
                raise FullPreviewError("The gold graph audit received an invalid scope set.")
            wiki_placeholders = ",".join("?" for _ in wiki_spaces)
            personal_profiles = int(
                connection.execute(
                    f"""SELECT count(*) FROM personal_wiki_profile
                          WHERE lifecycle_status='active'
                            AND space_id IN ({wiki_placeholders})""",
                    wiki_spaces,
                ).fetchone()[0]
            )
            personal_edge_counts = tuple(
                int(
                    connection.execute(
                        """SELECT count(*) FROM knowledge_edge
                             WHERE space_id=? AND edge_status='active'""",
                        (space_id,),
                    ).fetchone()[0]
                )
                for space_id in wiki_spaces
            )
            part_edge_counts = tuple(
                int(
                    connection.execute(
                        """SELECT count(*) FROM knowledge_edge
                             WHERE space_id=? AND edge_status='active'""",
                        (space_id,),
                    ).fetchone()[0]
                )
                for space_id in part_spaces
            )
            placeholders = ",".join("?" for _ in isolated_items)
            isolated_local_touches = int(
                connection.execute(
                    f"""SELECT count(*) FROM knowledge_edge
                          WHERE edge_status='active' AND origin='semantic_auto'
                            AND (source_item_id IN ({placeholders})
                              OR target_item_id IN ({placeholders}))""",
                    isolated_items + isolated_items,
                ).fetchone()[0]
            )
            isolated_aggregate_touches = int(
                connection.execute(
                    f"""SELECT count(*) FROM aggregate_knowledge_edge
                          WHERE edge_status='active' AND origin='semantic_auto'
                            AND (source_item_id IN ({placeholders})
                              OR target_item_id IN ({placeholders}))""",
                    isolated_items + isolated_items,
                ).fetchone()[0]
            )
            aggregate_rows = connection.execute(
                """SELECT home.space_kind,count(*) AS count
                     FROM aggregate_knowledge_edge AS edge
                     JOIN knowledge_space AS home ON home.id=edge.home_space_id
                    WHERE edge.edge_status='active' AND edge.origin='semantic_auto'
                    GROUP BY home.space_kind ORDER BY home.space_kind"""
            ).fetchall()
            invalid_aggregate_home = int(
                connection.execute(
                    """SELECT count(*) FROM aggregate_knowledge_edge AS edge
                         JOIN knowledge_space AS home ON home.id=edge.home_space_id
                        WHERE edge.edge_status='active'
                          AND home.space_kind NOT IN ('team','responsibility')"""
                ).fetchone()[0]
            )
            local_cross_space = int(
                connection.execute(
                    """SELECT count(*) FROM knowledge_edge AS edge
                         JOIN knowledge_item AS source ON source.id=edge.source_item_id
                         JOIN knowledge_item AS target ON target.id=edge.target_item_id
                        WHERE edge.edge_status='active'
                          AND (source.space_id<>edge.space_id
                            OR target.space_id<>edge.space_id)"""
                ).fetchone()[0]
            )
    finally:
        connection.close()
    aggregate_by_kind = {str(row["space_kind"]): int(row["count"]) for row in aggregate_rows}
    if (
        personal_profiles != 140
        or sum(part_edge_counts) < 1
        or len(isolated_items) != 151
        or isolated_local_touches != 0
        or isolated_aggregate_touches != 0
        or invalid_aggregate_home != 0
        or local_cross_space != 0
        or set(aggregate_by_kind) - {"team", "responsibility"}
    ):
        raise FullPreviewError(
            "The gold graph isolation audit failed: "
            + json.dumps(
                {
                    "aggregate_by_kind": aggregate_by_kind,
                    "detached": len(isolated_items),
                    "invalid_aggregate_home": invalid_aggregate_home,
                    "isolated_aggregate_touches": isolated_aggregate_touches,
                    "isolated_local_touches": isolated_local_touches,
                    "local_cross_space": local_cross_space,
                    "part_min": min(part_edge_counts),
                    "part_total": sum(part_edge_counts),
                    "personal_min": min(personal_edge_counts),
                    "personal_total": sum(personal_edge_counts),
                    "personal_profiles": personal_profiles,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return {
        "aggregate_auto_edges_by_home_kind": aggregate_by_kind,
        "detached_fixture_count": len(isolated_items),
        "detached_fixture_edge_touches": 0,
        "local_cross_space_edges": local_cross_space,
        "part_graphs": len(part_edge_counts),
        "part_min_local_edges": min(part_edge_counts),
        "part_total_local_edges": sum(part_edge_counts),
        "personal_graphs": len(personal_edge_counts),
        "personal_min_local_edges": min(personal_edge_counts),
        "personal_total_local_edges": sum(personal_edge_counts),
        "status": "pass",
    }


def run_codex_bridge_gate() -> dict[str, object]:
    """Exercise only the optional local Codex lane; never try company APIs."""

    from code_assist_v1.internal_llm import run_internal_text

    marker = "SECOND-BRAIN-CODEX-BRIDGE-READY"
    text, metadata = run_internal_text(
        "full_second_brain_preview_gate",
        {"expected": marker},
        user_prompt=(
            "This is a local bridge health check. Reply with exactly "
            f"{marker} and no other text."
        ),
        max_tokens=64,
        temperature=0.0,
        timeout_s=900,
        provider_order=("codex",),
        model_ids=("codex-local",),
        validate_output=lambda value: "" if value.strip() == marker else "unexpected marker",
    )
    if text is None or text.strip() != marker:
        errors = metadata.get("fallback_errors") if isinstance(metadata, Mapping) else None
        raise FullPreviewError(
            "The local Codex bridge gate failed: "
            + json.dumps(errors if isinstance(errors, list) else [], ensure_ascii=False)
        )
    return {
        "company_api_called": False,
        "model_id": metadata.get("model_id"),
        "model_kind": metadata.get("model_kind"),
        "status": "pass",
    }


def run_gold_codex_grounding_gate(
    app: object,
    *,
    password: str,
    port: int,
    snapshot: GoldOrganizationSnapshot,
    organization_ids: Mapping[str, Any],
) -> dict[str, object]:
    """Ground Codex in exact personal and inherited organization search hits."""

    from code_assist_v1.internal_llm import run_internal_text

    origin = f"http://127.0.0.1:{port}"
    client = app.test_client()
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": _ADMIN_ID, "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if login.status_code != 200:
        raise FullPreviewError("The Codex grounding gate could not log in.")
    csrf = (login.get_json(silent=True) or {}).get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise FullPreviewError("The Codex grounding gate has no CSRF token.")
    display_by_user = {user.id: user.display_name for user in snapshot.users}

    def search(
        *, actor: str, selected_space_id: str, query: str, expected_source: str
    ) -> str:
        with client.session_transaction(base_url=origin) as session:
            session["code_assist_user"] = {
                "display_name": display_by_user[actor],
                "id": actor,
                "is_admin": False,
                "login_at": 1_787_400_000,
                "must_change_password": False,
                "role": "user",
            }
        response = client.post(
            f"/api/knowledge/v1/spaces/{selected_space_id}/search",
            json={"query": query, "limit": 10},
            headers={
                "Origin": origin,
                "X-CSRF-Token": csrf,
                "X-Request-ID": _uuid(f"gold-codex-search:{selected_space_id}:{query}"),
            },
            base_url=origin,
        )
        if response.status_code != 200:
            raise FullPreviewError(
                f"Codex grounding search failed with HTTP {response.status_code}."
            )
        hits = (response.get_json(silent=True) or {}).get("items")
        if (
            not isinstance(hits, list)
            or not hits
            or hits[0].get("source_space_id") != expected_source
            or not isinstance(hits[0].get("snippet"), str)
        ):
            raise FullPreviewError("Codex grounding did not receive the expected source hit.")
        return str(hits[0]["snippet"])

    first_user = snapshot.users[0].id
    personal_space = organization_ids["gold_wiki_spaces"][first_user][0]
    personal_key = f"PERSONAL-{first_user.upper()}-W1-ANSWER"
    first_part = snapshot.parts[0]
    part_actor = organization_ids["gold_users_by_part"][first_part.id][0]
    part_space = organization_ids["part_spaces"][first_part.id]
    part_key = f"PART-{part_actor.upper()}-ANSWER"
    first_team = next(team for team in snapshot.teams if team.id == first_part.parent_id)
    team_space = organization_ids["team_spaces"][first_team.id]
    team_key = f"TEAM-{first_team.id.upper()}-ANSWER"
    responsibility_space = organization_ids["responsibility_space"]
    excerpts = (
        search(
            actor=first_user,
            selected_space_id=personal_space,
            query=personal_key,
            expected_source=personal_space,
        ),
        search(
            actor=part_actor,
            selected_space_id=part_space,
            query=part_key,
            expected_source=part_space,
        ),
        search(
            actor=part_actor,
            selected_space_id=team_space,
            query=part_key,
            expected_source=part_space,
        ),
        search(
            actor=part_actor,
            selected_space_id=responsibility_space,
            query=team_key,
            expected_source=team_space,
        ),
    )
    expected = "|".join((personal_key, part_key, part_key, team_key))
    prompt = (
        "Use only the four canonical Second Brain excerpts below. For each excerpt, "
        "copy its answer-key value in order and join the four values with |. "
        f"Reply with exactly {expected} and no explanation.\n\n"
        + "\n\n".join(
            f"EXCERPT {index}\n{excerpt}"
            for index, excerpt in enumerate(excerpts, start=1)
        )
    )
    output, metadata = run_internal_text(
        "full_second_brain_gold_grounding_gate",
        {"expected": expected, "excerpt_count": len(excerpts)},
        user_prompt=prompt,
        max_tokens=160,
        temperature=0.0,
        timeout_s=900,
        provider_order=("codex",),
        model_ids=("codex-local",),
        validate_output=lambda value: (
            "" if value.strip() == expected else "unexpected grounded answer"
        ),
    )
    if output is None or output.strip() != expected:
        errors = metadata.get("fallback_errors") if isinstance(metadata, Mapping) else None
        raise FullPreviewError(
            "The Codex grounding gate failed: "
            + json.dumps(errors if isinstance(errors, list) else [], ensure_ascii=False)
        )
    return {
        "company_api_called": False,
        "grounded_queries": 4,
        "inherited_queries": 2,
        "model_id": metadata.get("model_id"),
        "model_kind": metadata.get("model_kind"),
        "status": "pass",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=_default_data_root())
    parser.add_argument("--port", type=int, default=10111)
    parser.add_argument(
        "--organization-source-root",
        type=Path,
        help="Read-only 10011 preview root containing the approved 1/3/7 organization.",
    )
    parser.add_argument("--password", default="")
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--probe-codex", action="store_true")
    parser.add_argument(
        "--clean-validation-root",
        action="store_true",
        help=(
            "Import only preserved admin source Markdown plus organization and authority; "
            "do not seed preview/gold knowledge or semantic edges."
        ),
    )
    parser.add_argument("--serve", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.data_root.resolve(strict=False)
    gold_organization = (
        None
        if args.organization_source_root is None
        else _load_gold_organization(args.organization_source_root)
    )
    if gold_organization is not None:
        source_root = gold_organization.source_database.parent.parent.resolve(strict=True)
        if root == source_root or source_root in root.parents or root in source_root.parents:
            raise FullPreviewError("The gold preview target must be isolated from the 10011 source.")
    _configure_environment(root, port=args.port, codex=args.codex)
    marker = prepare_preview_root(
        root,
        codex=args.codex,
        gold_organization=gold_organization,
        clean_validation_root=args.clean_validation_root,
    )
    assert_original_inputs_unchanged(marker)
    factory = _canonical_factory(root)
    organization_ids = (
        seed_canonical_administration(factory, root)
        if gold_organization is None
        else seed_gold_administration(factory, root, gold_organization)
    )
    knowledge_projection = seed_canonical_preview_knowledge(
        factory,
        root,
        organization_ids,
        include_fixture_documents=not args.clean_validation_root,
    )
    gold_projection = None
    if gold_organization is not None and not args.clean_validation_root:
        gold_projection = seed_gold_corpus(factory, gold_organization, organization_ids)
    from code_assist_v1.knowledge_core.services.automatic_semantic_edges import (
        InternalLLMSemanticJudge,
    )
    from code_assist_v1.tools.full_second_brain_preview_workers import (
        preview_semantic_provider_policy,
    )

    semantic_policy = preview_semantic_provider_policy(
        codex_validation=args.codex
    )
    successor_projection = None
    clean_semantic_baseline = None
    semantic_judge = InternalLLMSemanticJudge(
        provider_order=semantic_policy.provider_order,
        model_ids=semantic_policy.model_ids,
    )
    if args.clean_validation_root:
        clean_semantic_baseline = initialize_clean_semantic_baseline(factory)
    else:
        successor_projection = seed_canonical_successor_preview(
            factory,
            organization_ids,
            semantic_judge=semantic_judge,
            semantic_model_provider=semantic_policy.model_provider,
            semantic_model_name=semantic_policy.model_name,
        )
    app = build_preview_app(factory, marker)
    result: dict[str, object] = {
        "data_root": os.fspath(root),
        "knowledge_projection": knowledge_projection,
        "marker_sha256": marker.get("marker_sha256"),
        "status": "ready",
        "url": f"http://127.0.0.1:{args.port}",
        "validation_seed_mode": "clean" if args.clean_validation_root else "fixture",
    }
    if successor_projection is not None:
        result["successor_projection"] = successor_projection
    if clean_semantic_baseline is not None:
        result["clean_semantic_baseline"] = clean_semantic_baseline
    if gold_projection is not None:
        result["gold_projection"] = gold_projection
        result["graph_contract_audit"] = audit_gold_graph_contract(
            factory, organization_ids
        )
        result["source_input_audit"] = {
            "company_api_called": False,
            "organization_source": marker.get("organization_source"),
            "source_mode": "read-only",
            "status": "pass",
        }
    if args.probe:
        if not args.password:
            raise FullPreviewError("--probe requires --password.")
        result["functional_gate"] = run_functional_gate(
            app, password=args.password, port=args.port
        )
        if gold_organization is not None and not args.clean_validation_root:
            result["gold_functional_gate"] = run_gold_functional_gate(
                app,
                password=args.password,
                port=args.port,
                snapshot=gold_organization,
                organization_ids=organization_ids,
            )
    if args.probe_codex:
        if not args.codex:
            raise FullPreviewError("--probe-codex requires --codex.")
        result["codex_bridge_gate"] = run_codex_bridge_gate()
        if gold_organization is not None and not args.clean_validation_root:
            if not args.password:
                raise FullPreviewError(
                    "Gold --probe-codex requires --password for canonical retrieval."
                )
            result["gold_codex_grounding_gate"] = run_gold_codex_grounding_gate(
                app,
                password=args.password,
                port=args.port,
                snapshot=gold_organization,
                organization_ids=organization_ids,
            )
    if args.serve:
        from waitress import serve
        from code_assist_v1.tools.full_second_brain_preview_workers import (
            PreviewWorkerSupervisor,
        )

        supervisor = PreviewWorkerSupervisor(
            root,
            marker_sha256=str(marker["marker_sha256"]),
            codex_validation=args.codex,
        )
        supervisor.start()
        app.extensions["full_second_brain_preview_worker_supervisors"].append(
            supervisor
        )
        result["workers"] = supervisor.status()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        try:
            serve(app, host="127.0.0.1", port=args.port, threads=8)
        finally:
            supervisor.stop()
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
