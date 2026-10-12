"""Isolated localhost validation host for the canonical Second Brain Agent.

This tool is deliberately separate from the production service launcher.  It
creates only P0.2-guarded test data outside the repository, enables canonical
personal/part reads with every mutation flag still disabled, and routes model
work to the local Codex bridge only.  It never probes or calls the company API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Mapping
from uuid import UUID, uuid4, uuid5


_NAMESPACE = UUID("68021cd2-d786-4a67-a395-4c2838faf153")
_MARKER_NAME = "local-second-brain-validation.json"
_MARKER_SCHEMA = "code-assist.local-second-brain-validation"
_MARKER_VERSION = 1
_ADMIN_ID = "local-admin"
_OTHER_ID = "local-other"
_PART_MARKER = "ORBIT-COPPER-7319"
_FOREIGN_MARKER = "FOREIGN-CANARY-4821"
_PERSONAL_MARKER = "PERSONAL-CANARY-2640"


class LocalValidationError(RuntimeError):
    """The isolated validation fixture or functional gate is invalid."""


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _configure_environment(root: Path, port: int) -> None:
    """Bind every mutable/runtime path before application modules are imported."""

    if not root.is_absolute():
        raise LocalValidationError("The local validation data root must be absolute.")
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise LocalValidationError("The local validation port is invalid.")
    origin_localhost = f"http://localhost:{port}"
    origin_loopback = f"http://127.0.0.1:{port}"
    values = {
        "CODE_ASSIST_TEST_MODE": "1",
        "CODE_ASSIST_TEST_DATA_ROOT": os.fspath(root),
        "CODE_ASSIST_DATA_ROOT": os.fspath(root),
        "CODE_ASSIST_STANDALONE": "1",
        "CODE_ASSIST_PROCESS_ROLE": "development",
        "CODE_ASSIST_PORT": str(port),
        "CODE_ASSIST_AUTOLOAD_GGUF": "0",
        "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "1",
        "CODE_ASSIST_CODEX_CWD": os.fspath(root / "codex-context"),
        "CODE_ASSIST_CODEX_SANDBOX": "read-only",
        "CODE_ASSIST_CODEX_EPHEMERAL": "1",
        "CODE_ASSIST_ALLOWED_ORIGINS": f"{origin_localhost},{origin_loopback}",
        "CODE_ASSIST_API_CONFIG": os.fspath(
            root / ".runtime" / "local-codex-api-config.json"
        ),
        "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION": "0",
    }
    for name, value in values.items():
        existing = os.environ.get(name)
        if existing not in {None, value}:
            raise LocalValidationError(
                f"The current process already bound an incompatible {name}."
            )
        os.environ[name] = value


def _ensure_local_model_registry(path: Path) -> None:
    """Create or verify a token-free Codex-only registry for this fixture."""

    value = {
        "default_model_priority": [],
        "gguf": {
            "auto_register_unconfigured": False,
            "autoload_on_startup": False,
        },
        "models": {
            "codex-local": {
                "capabilities": ["text", "analysis", "agent", "second-brain"],
                "context_window": 128000,
                "cost_tier": "local",
                "env_id": "codex-local",
                "model": "codex-cli",
                "name": "Codex Local Validation",
                "runtime": "codex",
            }
        },
    }
    encoded = _canonical_json_bytes(value)
    if path.exists():
        try:
            observed = path.read_bytes()
        except OSError as exc:
            raise LocalValidationError("The local model registry is unreadable.") from exc
        if observed != encoded:
            raise LocalValidationError("The local model registry does not match the fixture contract.")
        return
    _write_bytes_exclusive(path, encoded)


def _write_bytes_exclusive(path: Path, encoded: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short local-validation write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_json_bytes(value: object) -> bytes:
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


def _write_marker(path: Path, value: Mapping[str, object]) -> None:
    encoded = _canonical_json_bytes(dict(value))
    _write_bytes_exclusive(path, encoded)


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalValidationError("The local validation marker is unreadable.") from exc
    if not isinstance(value, dict):
        raise LocalValidationError("The local validation marker is invalid.")
    if value.get("report_schema") != _MARKER_SCHEMA or value.get("schema_version") != _MARKER_VERSION:
        raise LocalValidationError("The local validation marker has an unknown schema.")
    expected_hash = value.get("fixture_sha256")
    payload = dict(value)
    payload.pop("fixture_sha256", None)
    if expected_hash != _sha256_bytes(_canonical_json_bytes(payload)):
        raise LocalValidationError("The local validation marker hash does not match.")
    return value


def _create_auth_registry(password: str) -> dict[str, Mapping[str, Any]]:
    from code_assist_v1.auth import create_user, user_count
    from code_assist_v1.config import AUTH_USERS_FILE

    if len(password) < 12:
        raise LocalValidationError("The local validation password must be at least 12 characters.")
    if user_count() == 0:
        create_user(
            _ADMIN_ID,
            password,
            "Local Validation Admin",
            role="admin",
            must_change_password=False,
        )
        create_user(
            _OTHER_ID,
            password,
            "Local Validation Other Part",
            role="user",
            must_change_password=False,
        )
    try:
        document = json.loads(Path(AUTH_USERS_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalValidationError("The isolated authentication registry is invalid.") from exc
    users = document.get("users") if isinstance(document, dict) else None
    if not isinstance(users, dict) or set(users) != {_ADMIN_ID, _OTHER_ID}:
        raise LocalValidationError("The isolated authentication registry has unexpected accounts.")
    result: dict[str, Mapping[str, Any]] = {}
    for user_id in (_ADMIN_ID, _OTHER_ID):
        value = users.get(user_id)
        if not isinstance(value, dict) or not isinstance(value.get("password_hash"), str):
            raise LocalValidationError("An isolated authentication account is malformed.")
        result[user_id] = value
    return result


def _insert_item(
    connection: object,
    *,
    label: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
    now_ms: int,
) -> tuple[str, str]:
    item_id = _uuid(f"item:{label}")
    revision_id = _uuid(f"revision:{label}")
    encoded = body.encode("utf-8")
    digest = _sha256_bytes(encoded)
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,?, ?,1)""",
        (item_id, space_id, revision_id, actor, actor, now_ms, now_ms),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1','{}','initial',
                     'isolated local validation fixture',?,?)""",
        (
            revision_id,
            item_id,
            title,
            digest,
            digest,
            len(encoded),
            actor,
            now_ms,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,?)""",
        (revision_id, item_id, body, len(encoded), digest, now_ms),
    )
    _publish_item(
        connection,
        label=label,
        item_id=item_id,
        revision_id=revision_id,
        space_id=space_id,
        actor=actor,
        title=title,
        body=body,
        now_ms=now_ms + 1,
    )
    return item_id, revision_id


def _publish_item(
    connection: object,
    *,
    label: str,
    item_id: str,
    revision_id: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
    now_ms: int,
) -> None:
    finding_id = _uuid(f"finding:{label}")
    dedup_id = _uuid(f"dedup:{label}")
    operation_id = _uuid(f"operation:{label}")
    transition_id = _uuid(f"transition:{label}")
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,?,NULL,?)""",
        (dedup_id, actor, operation_id, "a" * 64, now_ms, now_ms),
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
             'local-validation','v1','v1',NULL,NULL,NULL,'{}','{}',
             NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'user',?,?,?,1)""",
        (
            finding_id,
            space_id,
            item_id,
            revision_id,
            actor,
            now_ms,
            now_ms,
        ),
    )
    connection.execute(
        """UPDATE curation_finding SET status='approved',reviewed_by=?,
             reviewed_at=?,review_reason='isolated fixture approval',updated_at=?,
             row_version=2 WHERE id=?""",
        (actor, now_ms + 1, now_ms + 1, finding_id),
    )
    connection.execute(
        """UPDATE curation_finding SET status='applied',applied_operation_id=?,
             applied_dedup_id=?,applied_by=?,applied_at=?,updated_at=?,row_version=3
             WHERE id=?""",
        (operation_id, dedup_id, actor, now_ms + 2, now_ms + 2, finding_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,
             NULL,NULL,?,?,'user',?,2,'isolated fixture publish',?)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            actor,
            now_ms + 2,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item SET curation_status='published',
             last_curation_transition_id=?,updated_by=?,updated_at=?,row_version=2
             WHERE id=?""",
        (transition_id, actor, now_ms + 2, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,?)""",
        (item_id, space_id, revision_id, title, body, now_ms + 2),
    )


def _seed_database(factory: object, auth_users: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    from code_assist_v1.knowledge_core.db import transaction
    from code_assist_v1.knowledge_core.repositories.organization import (
        OrganizationRepository,
        RESPONSIBILITY_ROOT_ID,
    )
    from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
    from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository

    ids = {
        key: _uuid(key)
        for key in (
            "team-a",
            "team-b",
            "part-a",
            "part-b",
            "part-space-a",
            "part-space-b",
            "personal-admin",
            "personal-other",
        )
    }
    ids["team-space-a"] = ids["team-a"]
    ids["team-space-b"] = ids["team-b"]
    now_ms = 1_780_000_000_000
    users_to_import = []
    for index, (user_id, role) in enumerate(((_ADMIN_ID, "admin"), (_OTHER_ID, "user"))):
        raw = auth_users[user_id]
        created_seconds = raw.get("created_at")
        created_at_ms = (
            int(created_seconds) * 1000
            if isinstance(created_seconds, int) and not isinstance(created_seconds, bool)
            else now_ms + index
        )
        users_to_import.append(
            LegacyUserRecord(
                id=user_id,
                display_name=str(raw.get("display_name") or user_id),
                password_hash=str(raw["password_hash"]),
                system_role=role,
                account_status="active",
                created_at_ms=created_at_ms,
                updated_at_ms=created_at_ms,
                password_changed_at_ms=created_at_ms,
            )
        )
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            organization = OrganizationRepository()
            spaces = SpaceRepository()
            users.import_legacy_many(connection, tuple(users_to_import))
            for key, name in (("team-a", "Validation Team A"), ("team-b", "Validation Team B")):
                organization.create_unit(
                    connection,
                    unit_id=ids[key],
                    kind="team",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name=name,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 10,
                )
            for part, team, name in (
                ("part-a", "team-a", "Validation Part A"),
                ("part-b", "team-b", "Validation Part B"),
            ):
                organization.create_unit(
                    connection,
                    unit_id=ids[part],
                    kind="part",
                    parent_id=ids[team],
                    name=name,
                    actor_user_id=_ADMIN_ID,
                    now_ms=now_ms + 20,
                )
            for user_id, part in ((_ADMIN_ID, "part-a"), (_OTHER_ID, "part-b")):
                organization.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=ids[part],
                    assigned_by=_ADMIN_ID,
                    reason="isolated local validation",
                    valid_from_ms=now_ms + 30,
                )
                state = organization.get_user_organization_state(connection, user_id)
                if state is None:
                    raise LocalValidationError("The fixture organization state is unavailable.")
                organization.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=ids[part],
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=now_ms + 30,
                )
            for user_id, part in ((_ADMIN_ID, "part-a"), (_OTHER_ID, "part-b")):
                organization.open_part_leader(
                    connection,
                    assignment_id=_uuid(f"leader:{part}"),
                    user_id=user_id,
                    part_id=ids[part],
                    assigned_by=_ADMIN_ID,
                    reason="isolated local validation",
                    valid_from_ms=now_ms + 40,
                )
            spaces.ensure_personal(
                connection,
                space_id=ids["personal-admin"],
                user_id=_ADMIN_ID,
                created_at_ms=now_ms + 50,
            )
            spaces.ensure_personal(
                connection,
                space_id=ids["personal-other"],
                user_id=_OTHER_ID,
                created_at_ms=now_ms + 50,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part-space-a"],
                part_id=ids["part-a"],
                created_at_ms=now_ms + 50,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part-space-b"],
                part_id=ids["part-b"],
                created_at_ms=now_ms + 50,
            )
            for team_key, space_key in (
                ("team-a", "team-space-a"),
                ("team-b", "team-space-b"),
            ):
                connection.execute(
                    """INSERT INTO knowledge_space(
                         id,space_kind,owner_user_id,owner_org_unit_id,status,
                         write_enabled,generation,created_at,updated_at,row_version
                       ) VALUES (?,'team',NULL,?,'active',0,0,?,?,1)""",
                    (
                        ids[space_key],
                        ids[team_key],
                        now_ms + 50,
                        now_ms + 50,
                    ),
                )
            ids["part-item"], ids["part-revision"] = _insert_item(
                connection,
                label="part-a-launch",
                space_id=ids["part-space-a"],
                actor=_ADMIN_ID,
                title="Scopealpha launch handbook",
                body=(
                    "# Scopealpha launch handbook\n\n"
                    f"The exact launch phrase is {_PART_MARKER}.\n"
                    "Use this phrase only when the user asks for the scopealpha launch phrase.\n"
                ),
                now_ms=now_ms + 100,
            )
            ids["foreign-item"], ids["foreign-revision"] = _insert_item(
                connection,
                label="part-b-foreign",
                space_id=ids["part-space-b"],
                actor=_OTHER_ID,
                title="Scopealpha foreign handbook",
                body=(
                    "# Scopealpha foreign handbook\n\n"
                    f"This other part contains {_FOREIGN_MARKER}.\n"
                ),
                now_ms=now_ms + 200,
            )
            ids["personal-item"], ids["personal-revision"] = _insert_item(
                connection,
                label="admin-personal",
                space_id=ids["personal-admin"],
                actor=_ADMIN_ID,
                title="Scopealpha personal note",
                body=(
                    "# Scopealpha personal note\n\n"
                    f"This private note contains {_PERSONAL_MARKER}.\n"
                ),
                now_ms=now_ms + 300,
            )
            ids["admin-scope-epoch"] = str(users.require(connection, _ADMIN_ID).scope_epoch)
    finally:
        connection.close()
    return ids


def _new_factory(layout: object) -> object:
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations import (
        CANONICAL_MIGRATION_CATALOG,
        MigrationStorageLayout,
        SingleMigrator,
    )

    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="local-second-brain-validation-v1",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def initialize_fixture(root: Path, *, port: int, password: str | None) -> tuple[object, dict[str, Any], str | None]:
    """Create or reopen one exact isolated fixture and return its factory/marker."""

    _configure_environment(root, port)
    from code_assist_v1.test_safety import create_isolated_test_layout

    layout = create_isolated_test_layout(root, user_id=_ADMIN_ID)
    codex_context = layout.root / "codex-context"
    codex_context.mkdir(parents=False, exist_ok=True)
    _ensure_local_model_registry(
        layout.runtime_dir / "local-codex-api-config.json"
    )
    marker_path = layout.runtime_dir / _MARKER_NAME
    if marker_path.exists():
        marker = _read_marker(marker_path)
        if Path(str(marker.get("data_root"))).resolve(strict=False) != layout.root:
            raise LocalValidationError("The fixture marker is bound to another data root.")
        if not layout.core_db_path.is_file():
            raise LocalValidationError("The fixture database is missing.")
        factory = _new_factory(layout)
        return factory, marker, None
    if layout.core_db_path.exists():
        raise LocalValidationError("An unmarked database already exists in the fixture root.")
    selected_password = password or ("Local-" + secrets.token_urlsafe(18))
    auth_users = _create_auth_registry(selected_password)
    factory = _new_factory(layout)
    ids = _seed_database(factory, auth_users)
    marker_payload: dict[str, object] = {
        "account_ids": [_ADMIN_ID, _OTHER_ID],
        "codex_cwd": os.fspath(codex_context),
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_root": os.fspath(layout.root),
        "database": os.fspath(layout.core_db_path),
        "fixture_ids": ids,
        "foreign_marker_sha256": _sha256_bytes(_FOREIGN_MARKER.encode("utf-8")),
        "model_provider_order": ["codex"],
        "mutation_flags_enabled": False,
        "part_marker_sha256": _sha256_bytes(_PART_MARKER.encode("utf-8")),
        "report_schema": _MARKER_SCHEMA,
        "schema_version": _MARKER_VERSION,
    }
    marker_payload["fixture_sha256"] = _sha256_bytes(_canonical_json_bytes(marker_payload))
    _write_marker(marker_path, marker_payload)
    return factory, dict(marker_payload), selected_password


def _principal_resolver(factory: object):
    from code_assist_v1.knowledge_core.agent_runtime import (
        canonical_cookie_principal_resolver,
    )

    return canonical_cookie_principal_resolver(factory)


def build_validation_app(
    factory: object,
    marker: Mapping[str, object],
    *,
    model_runner: object | None = None,
) -> object:
    """Compose the real canonical route group with Codex-only model policy."""

    # app_code owns the public ``--codex`` launch policy.  The validation tool
    # enters through that same parser path instead of silently mutating the
    # production model default or bypassing the launch gate.
    injected_codex_argument = "--codex" not in sys.argv
    if injected_codex_argument:
        sys.argv.append("--codex")
    try:
        from code_assist_v1.app_code import create_app
    finally:
        if injected_codex_argument:
            sys.argv.remove("--codex")
    from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
    from code_assist_v1.knowledge_core.composition import CanonicalRouteBindings
    from code_assist_v1.knowledge_core.domain import FeatureFlags

    flags = FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=False,
        part_reads=True,
        part_writes=False,
        projections=False,
        higher_spaces=False,
    )

    def local_model_runner(*args: object, **kwargs: object) -> tuple[object, object]:
        from code_assist_v1.internal_llm import run_internal_text

        text, metadata = run_internal_text(*args, **kwargs)
        if text is None:
            errors = metadata.get("fallback_errors") if isinstance(metadata, dict) else None
            print(
                json.dumps(
                    {
                        "code": "LOCAL_CODEX_PROVIDER_FAILED",
                        "fallback_errors": errors if isinstance(errors, list) else [],
                        "status": "failed",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
        return text, metadata

    environment = {
        "KNOWLEDGE_CORE_ENABLED": "1",
        "KNOWLEDGE_CORE_READS": "1",
        "KNOWLEDGE_CORE_PERSONAL_WRITES": "0",
        "KNOWLEDGE_CORE_PART_READS": "1",
        "KNOWLEDGE_CORE_PART_WRITES": "0",
        "KNOWLEDGE_CORE_PROJECTIONS": "0",
        "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": "0",
        "KNOWLEDGE_CORE_HIGHER_SPACES": "0",
        "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": "0",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": "0",
        "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": "0",
    }
    selected_runner = local_model_runner if model_runner is None else model_runner
    if not callable(selected_runner):
        raise TypeError("model_runner must be callable.")
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=flags,
        principal_resolver=_principal_resolver(factory),
        provider_order=("codex",),
        model_ids=("codex-local",),
        model_runner=selected_runner,
    )

    class _LocalTurnExecutor:
        def complete(self, context: object) -> object:
            try:
                return runtime.route_bindings.turn_executor.complete(context)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "code": getattr(exc, "code", "AGENT_EXECUTION_FAILED"),
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:500],
                            "stage": "turn_executor",
                            "status": "failed",
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                raise

    local_bindings = CanonicalRouteBindings(
        turn_executor=_LocalTurnExecutor(),
        conversation_summarizer=runtime.route_bindings.conversation_summarizer,
        principal_resolver=runtime.route_bindings.principal_resolver,
    )

    def local_health() -> tuple[Mapping[str, object], int]:
        return (
            {
                "report_schema": _MARKER_SCHEMA,
                "schema_version": _MARKER_VERSION,
                "status": "ready",
                "traffic_scope": "localhost_single_user_validation",
                "company_api_called": False,
                "fixture_sha256": marker.get("fixture_sha256"),
            },
            200,
        )

    return create_app(
        knowledge_core_services=runtime.services,
        knowledge_core_route_bindings=local_bindings,
        knowledge_core_environ=environment,
        production_health_provider=local_health,
    )


def _mutation_headers(csrf_token: str, operation_id: str, port: int) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Idempotency-Key": operation_id,
        "Origin": f"http://127.0.0.1:{port}",
        "X-CSRF-Token": csrf_token,
    }


def _assistant_text(conversation: Mapping[str, object]) -> str:
    messages = conversation.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content, dict):
            nested = content.get("content")
            if isinstance(nested, dict) and isinstance(nested.get("text"), str):
                return nested["text"]
    return ""


def run_functional_gate(app: object, *, password: str, port: int) -> dict[str, object]:
    """Exercise login, exact part retrieval, Codex answer, and cross-part denial."""

    client = app.test_client()
    origin = f"http://127.0.0.1:{port}"
    login = client.post(
        "/api/code/auth/login",
        json={"user_id": _ADMIN_ID, "password": password},
        headers={"Origin": origin},
        base_url=origin,
    )
    if login.status_code != 200 or not isinstance(login.json, dict):
        raise LocalValidationError(f"Local login gate failed with HTTP {login.status_code}.")
    csrf = login.json.get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise LocalValidationError("Local login did not issue a CSRF token.")
    spaces = client.get("/api/knowledge/v1/spaces", base_url=origin)
    if spaces.status_code != 200 or not isinstance(spaces.json, dict):
        raise LocalValidationError("Canonical space discovery failed.")
    rows = spaces.json.get("items")
    if not isinstance(rows, list):
        raise LocalValidationError("Canonical space discovery returned an invalid shape.")
    part_rows = [row for row in rows if isinstance(row, dict) and row.get("kind") == "part"]
    if len(part_rows) != 1 or part_rows[0].get("id") is None:
        raise LocalValidationError("The local user did not resolve to exactly one part space.")
    part_space_id = str(part_rows[0]["id"])
    search = client.post(
        f"/api/knowledge/v1/spaces/{part_space_id}/search",
        json={"query": "scopealpha launch phrase", "limit": 10},
        headers={
            "Origin": origin,
            "X-CSRF-Token": csrf,
        },
        base_url=origin,
    )
    if search.status_code != 200 or not isinstance(search.json, dict):
        raise LocalValidationError("Canonical scoped retrieval failed.")
    serialized_search = json.dumps(search.json, ensure_ascii=False)
    if _FOREIGN_MARKER in serialized_search or _PERSONAL_MARKER in serialized_search:
        raise LocalValidationError("Scoped retrieval exposed another space.")
    conversation_operation = str(uuid4())
    created = client.post(
        "/api/knowledge/v1/conversations",
        json={
            "operation_id": conversation_operation,
            "space_id": part_space_id,
            "title": "Local Codex validation",
        },
        headers=_mutation_headers(csrf, conversation_operation, port),
        base_url=origin,
    )
    if created.status_code != 201 or not isinstance(created.json, dict):
        raise LocalValidationError(f"Conversation creation failed with HTTP {created.status_code}.")
    conversation_id = created.json.get("resource_id")
    if not isinstance(conversation_id, str):
        raise LocalValidationError("Conversation creation returned no resource ID.")
    turn_operation = str(uuid4())
    turn = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={
            "operation_id": turn_operation,
            "message": (
                "Using only the retrieved scopealpha source, return the exact launch "
                "phrase. Do not return any other marker."
            ),
        },
        headers={
            **_mutation_headers(csrf, turn_operation, port),
            "Accept": "text/event-stream",
        },
        base_url=origin,
    )
    turn_text = turn.get_data(as_text=True)
    if turn.status_code != 200 or "event: completed" not in turn_text:
        event_kinds = [
            line.removeprefix("event: ").strip()
            for line in turn_text.splitlines()
            if line.startswith("event: ")
        ]
        raise LocalValidationError(
            "The Codex-backed conversation turn did not complete "
            f"(HTTP {turn.status_code}, events={event_kinds!r})."
        )
    detail = client.get(
        f"/api/knowledge/v1/conversations/{conversation_id}",
        base_url=origin,
    )
    if detail.status_code != 200 or not isinstance(detail.json, dict):
        raise LocalValidationError("The completed conversation could not be read back.")
    answer = _assistant_text(detail.json)
    if _PART_MARKER not in answer:
        raise LocalValidationError("The Codex answer did not contain the scoped source marker.")
    if _FOREIGN_MARKER in answer or _PERSONAL_MARKER in answer:
        raise LocalValidationError("The Codex answer crossed a canonical space boundary.")
    return {
        "answer_sha256": _sha256_bytes(answer.encode("utf-8")),
        "company_api_called": False,
        "conversation_id": conversation_id,
        "foreign_marker_absent": True,
        "model_provider": "codex",
        "part_marker_present": True,
        "part_space_id": part_space_id,
        "status": "pass",
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--mode", choices=("initialize", "probe", "serve"), default="serve")
    parser.add_argument("--password", default=None)
    parser.add_argument("--port", type=int, default=10111)
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Serve an already-probed fixture without calling Codex during startup.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.data_root.expanduser().resolve(strict=False)
    try:
        factory, marker, generated_password = initialize_fixture(
            root,
            port=args.port,
            password=args.password,
        )
        app = build_validation_app(factory, marker)
        password = args.password or generated_password
        probe_result: Mapping[str, object] | None = None
        if args.mode == "probe" or (args.mode == "serve" and not args.skip_probe):
            if not password:
                raise LocalValidationError(
                    "A password is required to probe an existing fixture."
                )
            probe_result = run_functional_gate(app, password=password, port=args.port)
        payload = {
            "data_root": os.fspath(root),
            "fixture_sha256": marker.get("fixture_sha256"),
            "mode": args.mode,
            "password": generated_password,
            "probe": probe_result,
            "url": f"http://127.0.0.1:{args.port}/",
            "user_id": _ADMIN_ID,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        if args.mode == "serve":
            from waitress import serve

            serve(app, host="127.0.0.1", port=args.port, threads=4)
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "code": getattr(exc, "code", "LOCAL_VALIDATION_FAILED"),
                    "error": str(exc),
                    "status": "failed",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
