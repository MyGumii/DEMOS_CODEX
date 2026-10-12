from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    ManagedConnection,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.outbox import OutboxRepository
from code_assist_v1.knowledge_core.repositories.personal_wikis import (
    PersonalWikiProfileRepository,
)
from code_assist_v1.knowledge_core.repositories.publication import (
    PublicationPreviewRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.curation import (
    DETERMINISTIC_INGEST_RULE_VERSION,
)
from code_assist_v1.knowledge_core.services.publication import (
    AuthorizedNotionPageSnapshot,
    CreateManualPreviewCommand,
    CreateNotionPreviewCommand,
    PublicationPreviewAuthorizationError,
    PublicationPreviewIdempotencyError,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.curation_worker import (
    CanonicalCurationRescanHandler,
)
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    ImmutableItemEditService,
    LeaseHmacKeyRing,
    SaveItemCommand,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner
from code_assist_v1.knowledge_core.worker import DurableWorkKernel, HandlerRegistry


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("1e417320-c10a-49af-b8c5-fe904acb1dd9")
_ACTOR = "publisher"
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p4.3-publication-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_actor(factory: ConnectionFactory) -> str:
    personal_id = space_id_for_owner("personal", _ACTOR)
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=_ACTOR,
                    display_name="Publication Actor",
                    password_hash="scrypt:p4.3:actor",
                    system_role="user",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            spaces = SpaceRepository()
            ensured = spaces.ensure_personal(
                connection,
                space_id=personal_id,
                user_id=_ACTOR,
                created_at_ms=100,
            )
            spaces.update_state(
                connection,
                space_id=personal_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=101,
            )
    finally:
        connection.close()
    return personal_id


def _assign_part(factory: ConnectionFactory) -> tuple[str, str, int]:
    team_id = _uuid("team")
    part_id = _uuid("part")
    part_space_id = _uuid("part-space")
    organization = OrganizationRepository()
    users = UserRepository()
    spaces = SpaceRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Publication Team",
                actor_user_id=_ACTOR,
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Publication Part",
                actor_user_id=_ACTOR,
                now_ms=111,
            )
            organization.open_membership(
                connection,
                membership_id=_uuid("membership"),
                user_id=_ACTOR,
                part_id=part_id,
                assigned_by=_ACTOR,
                reason="P4.3 fixture",
                valid_from_ms=112,
            )
            actor = users.require(connection, _ACTOR)
            moved = organization.compare_and_swap_user_part(
                connection,
                user_id=_ACTOR,
                expected_current_part_id=None,
                target_part_id=part_id,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=113,
            )
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("leader"),
                user_id=_ACTOR,
                part_id=part_id,
                assigned_by=_ACTOR,
                reason="P4.3 fixture",
                valid_from_ms=114,
            )
            ensured = spaces.ensure_part(
                connection,
                space_id=part_space_id,
                part_id=part_id,
                created_at_ms=115,
            )
            spaces.update_state(
                connection,
                space_id=part_space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=116,
            )
    finally:
        connection.close()
    return part_id, part_space_id, moved.scope_epoch


class _Lease:
    def close(self) -> None:
        return None


@dataclass(frozen=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _Lease


class _Stager:
    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> _Observation:
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        return _Observation(
            StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=(
                    f"{expected_sha256[:2]}/{expected_sha256[2:4]}/"
                    f"{expected_sha256}"
                ),
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            _Lease(),
        )


class _NotionSnapshots:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.revision = 1

    def capture(self, *, actor_user_id: str, page_id: str):
        content_sha = hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()
        asset_sha = hashlib.sha256(b"[]").hexdigest()
        identity = {
            "asset_manifest_sha256": asset_sha,
            "content_sha256": content_sha,
            "owner_user_id": _ACTOR,
            "page_id": page_id,
            "revision": self.revision,
            "schema_version": 1,
            "updated_at": self.revision,
        }
        snapshot_sha = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AuthorizedNotionPageSnapshot(
            actor_user_id=actor_user_id,
            page_id=page_id,
            owner_user_id=_ACTOR,
            title="Stable Notion page",
            revision=self.revision,
            updated_at=self.revision,
            markdown=self.markdown,
            content_sha256=content_sha,
            asset_manifest_sha256=asset_sha,
            source_snapshot_sha256=snapshot_sha,
        )


def _flags(*, part: bool = False) -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=part,
        part_writes=part,
    )


def _preview(
    factory: ConnectionFactory,
    *,
    label: str,
    markdown: str,
    epoch: int,
    part: bool,
):
    service = PublicationPreviewService(
        factory, ScopeResolver(_flags(part=part)), _Stager()
    )
    return service.create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid(f"{label}:preview-request"),
            operation_id=_uuid(f"{label}:preview-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=epoch,
            markdown=markdown,
            request_meta={"correlation_id": f"{label}:preview"},
        )
    )


def _publish_command(
    preview,
    *,
    label: str,
    epoch: int,
    target_mode: str,
) -> PublishPreviewCommand:
    return PublishPreviewCommand(
        request_id=_uuid(f"{label}:request"),
        operation_id=_uuid(f"{label}:operation"),
        actor_user_id=_ACTOR,
        actor_scope_epoch=epoch,
        preview_id=preview.preview_id,
        source_snapshot_sha256=preview.source_snapshot_sha256,
        target_mode=target_mode,  # type: ignore[arg-type]
        request_meta={"correlation_id": label, "ignored": "secret-ish"},
    )


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    tables = (
        "publication_operation",
        "publication_target",
        "lineage_group",
        "lineage_member",
        "knowledge_item",
        "knowledge_revision",
        "item_source",
    )
    connection = factory.open(read_only=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def _assert_postconditions(factory: ConnectionFactory) -> None:
    connection = factory.open(read_only=True)
    try:
        for name, sql in POSTCONDITION_DEFINITIONS:
            assert connection.execute(sql).fetchone() is None, name
    finally:
        connection.close()


def _publication_state(factory: ConnectionFactory, preview_id: str) -> dict[str, object]:
    """Return every mutable publication-side view used by the rollback matrix."""

    tables = (
        "publication_operation",
        "publication_target",
        "lineage_group",
        "lineage_member",
        "knowledge_item",
        "knowledge_revision",
        "knowledge_revision_body",
        "item_source",
        "curation_finding",
        "change_proposal",
        "audit_event",
        "outbox_event",
        "operation_dedup",
    )
    connection = factory.open(read_only=True)
    try:
        preview = connection.execute(
            "SELECT status,row_version,completed_at FROM publication_preview WHERE id=?",
            (preview_id,),
        ).fetchone()
        generations = connection.execute(
            "SELECT id,generation,row_version FROM knowledge_space ORDER BY id"
        ).fetchall()
        return {
            "counts": {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in tables
            },
            "generations": tuple(tuple(row) for row in generations),
            "preview": None if preview is None else tuple(preview),
        }
    finally:
        connection.close()


class _PublicationSqlFailpoint(RuntimeError):
    pass


def _is_mutating_sql(sql: str) -> bool:
    normalized = " ".join(sql.upper().split())
    if "TEMP." in normalized or "__KNOWLEDGE_CORE_FTS_PROBE" in normalized:
        return False
    return any(
        token in normalized
        for token in ("INSERT INTO ", "INSERT OR ", "UPDATE ", "DELETE FROM ", "REPLACE INTO ")
    )


_PUBLICATION_PROCESS_WORKER = r"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.publication_authority import HigherPublicationTargetDescriptor
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.publication import (
    CancelPublicationPreviewCommand,
    GetPublicationPreviewQuery,
    PublicationPreviewServiceError,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)

database, ready_raw, start_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
scope = ScopeResolver(FeatureFlags(
    core_enabled=True,
    personal_reads=True,
    personal_writes=True,
    part_reads=True,
    part_writes=True,
    higher_spaces=True,
    team_writes=True,
    responsibility_writes=True,
))
service = PublicationService(factory, scope)
preview_service = PublicationPreviewService(factory, scope, type(
    "NoStage",
    (),
    {"stage_and_verify": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unused"))},
)())
ready = Path(ready_raw)
start = Path(start_raw)
ready.touch()
deadline = time.monotonic() + 30.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("publication start barrier timed out")
    time.sleep(0.01)
try:
    action = payload.pop("_action", "publish")
    if isinstance(payload.get("target"), dict):
        payload["target"] = HigherPublicationTargetDescriptor(**payload["target"])
    if action == "poll":
        result = preview_service.get_preview(GetPublicationPreviewQuery(**payload))
    elif action == "cancel":
        cancelled = preview_service.cancel_preview(CancelPublicationPreviewCommand(**payload))
        result = {
            "preview_id": cancelled.preview_id,
            "row_version": cancelled.row_version,
            "source_snapshot_sha256": cancelled.source_snapshot_sha256,
            "status": cancelled.status,
        }
    elif action == "publish_drop_response":
        service.publish(PublishPreviewCommand(**payload))
        os._exit(23)
    else:
        result = service.publish(PublishPreviewCommand(**payload)).to_mapping()
    print(json.dumps({"kind": "ok", "result": result}, sort_keys=True))
except sqlite3.OperationalError as exc:
    message = str(exc)
    kind = "sqlite_busy" if "busy" in message.lower() or "locked" in message.lower() else "sqlite_error"
    print(json.dumps({"kind": kind, "message": message}, sort_keys=True))
except PublicationPreviewServiceError as exc:
    print(json.dumps({"kind": "service_error", "message": str(exc)}, sort_keys=True))
"""


def _run_publication_processes(
    layout,
    *,
    label: str,
    payloads: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    start = layout.root / f"{label}-start"
    ready_paths = tuple(
        layout.root / f"{label}-ready-{index}" for index in range(len(payloads))
    )
    environment = os.environ.copy()
    processes: list[subprocess.Popen[str]] = []
    for ready, payload in zip(ready_paths, payloads, strict=True):
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-E",
                    "-c",
                    _PUBLICATION_PROCESS_WORKER,
                    str(layout.core_db_path),
                    str(ready),
                    str(start),
                    json.dumps(payload, sort_keys=True),
                ],
                cwd=_PACKAGE_ROOT.parent,
                env=environment,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    deadline = time.monotonic() + 30.0
    while not all(path.exists() for path in ready_paths):
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"publication worker exited before barrier: {process.returncode}; "
                    f"stdout={stdout!r}; stderr={stderr!r}"
                )
        if time.monotonic() >= deadline:
            raise AssertionError("publication workers did not reach the start barrier")
        time.sleep(0.01)
    start.touch()

    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        assert stderr == ""
        lines = [line for line in stdout.splitlines() if line.strip()]
        assert lines
        results.append(json.loads(lines[-1]))
    assert not any(result["kind"] == "sqlite_busy" for result in results), results
    return tuple(results)


def _run_publication_response_loss(
    layout,
    *,
    label: str,
    payload: dict[str, object],
) -> None:
    start = layout.root / f"{label}-start"
    ready = layout.root / f"{label}-ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-c",
            _PUBLICATION_PROCESS_WORKER,
            str(layout.core_db_path),
            str(ready),
            str(start),
            json.dumps({**payload, "_action": "publish_drop_response"}, sort_keys=True),
        ],
        cwd=_PACKAGE_ROOT.parent,
        env=os.environ.copy(),
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 30.0
    while not ready.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"response-loss worker exited before barrier: {process.returncode}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            process.communicate()
            raise AssertionError("response-loss worker did not reach the start barrier")
        time.sleep(0.01)
    start.touch()
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 23, (stdout, stderr)
    assert stdout == "" and stderr == ""


def _exercise_publication_sql_failpoint_matrix(
    *,
    factory: ConnectionFactory,
    service: PublicationService,
    command: PublishPreviewCommand,
    preview_id: str,
    monkeypatch: pytest.MonkeyPatch,
    minimum_writes: int,
):
    baseline = _publication_state(factory, preview_id)
    original_execute = ManagedConnection.execute
    original_executemany = ManagedConnection.executemany
    state: dict[str, object] = {
        "active": False,
        "fail_at": None,
        "seen": [],
        "stop_after_terminal": False,
    }

    def observe(sql: str) -> None:
        if not state["active"] or not _is_mutating_sql(sql):
            return
        seen = state["seen"]
        assert isinstance(seen, list)
        normalized = " ".join(sql.upper().split())
        seen.append(normalized)
        fail_at = state["fail_at"]
        if isinstance(fail_at, int) and len(seen) == fail_at:
            raise _PublicationSqlFailpoint(f"publication SQL write {fail_at}")
        if (
            state["stop_after_terminal"]
            and "UPDATE OPERATION_DEDUP" in normalized
            and "STATUS='COMMITTED'" in normalized
        ):
            raise _PublicationSqlFailpoint("publication terminal write discovery")

    def execute(connection, sql, parameters=()):
        cursor = original_execute(connection, sql, parameters)
        observe(sql)
        return cursor

    def executemany(connection, sql, parameters):
        cursor = original_executemany(connection, sql, parameters)
        observe(sql)
        return cursor

    monkeypatch.setattr(ManagedConnection, "execute", execute)
    monkeypatch.setattr(ManagedConnection, "executemany", executemany)

    state.update(active=True, fail_at=None, seen=[], stop_after_terminal=True)
    with pytest.raises(_PublicationSqlFailpoint, match="terminal write discovery"):
        service.publish(command)
    matrix = tuple(state["seen"])
    state["active"] = False
    assert len(matrix) >= minimum_writes
    assert "UPDATE OPERATION_DEDUP" in matrix[-1]
    assert _publication_state(factory, preview_id) == baseline
    _assert_postconditions(factory)

    for fail_at in range(1, len(matrix) + 1):
        state.update(
            active=True,
            fail_at=fail_at,
            seen=[],
            stop_after_terminal=False,
        )
        with pytest.raises(
            _PublicationSqlFailpoint,
            match=rf"publication SQL write {fail_at}",
        ):
            service.publish(command)
        observed = tuple(state["seen"])
        state["active"] = False
        assert observed == matrix[:fail_at]
        assert _publication_state(factory, preview_id) == baseline
        _assert_postconditions(factory)

    result = service.publish(command)
    _assert_postconditions(factory)
    return result, matrix


@pytest.mark.scenario("PUB-001")
@pytest.mark.scenario("PUB-011")
@pytest.mark.scenario("CUR-003")
def test_personal_publication_commits_provisional_item_and_exact_replay(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    preview = _preview(
        factory,
        label="personal",
        markdown="# Personal\n\nBody\n",
        epoch=1,
        part=False,
    )
    service = PublicationService(factory, ScopeResolver(_flags()))
    command = _publish_command(
        preview,
        label="personal-publish",
        epoch=1,
        target_mode="personal",
    )
    first = service.publish(command)
    replay = service.publish(replace(command, request_id=_uuid("personal-retry-request")))
    assert replay == first
    assert first.target_mode == "personal"
    assert first.lineage_group_id is None
    assert first.targets[0]["outcome"] == "created"
    assert first.targets[0]["space_id"] == personal_id
    assert _counts(factory) == {
        "publication_operation": 1,
        "publication_target": 1,
        "lineage_group": 0,
        "lineage_member": 0,
        "knowledge_item": 1,
        "knowledge_revision": 1,
        "item_source": 1,
    }
    _assert_postconditions(factory)
    connection = factory.open(read_only=True)
    try:
        item = connection.execute(
            """SELECT lifecycle_status,curation_status,row_version
               FROM knowledge_item"""
        ).fetchone()
        assert tuple(item) == ("active", "provisional", 1)
        assert connection.execute(
            "SELECT status FROM publication_preview"
        ).fetchone()[0] == "consumed"
        assert connection.execute(
            "SELECT change_kind FROM knowledge_revision"
        ).fetchone()[0] == "initial"
        assert connection.execute("SELECT role FROM item_source").fetchone()[0] == "initial"
        assert connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (personal_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM item_relation"
        ).fetchone()[0] == 0
        target_json = connection.execute(
            "SELECT response_json FROM publication_target"
        ).fetchone()[0]
        assert json.loads(target_json) == dict(first.targets[0])
        operation_json = connection.execute(
            """SELECT result_json FROM operation_dedup
               WHERE operation_type='publication'"""
        ).fetchone()[0]
        assert json.loads(operation_json) == first.to_mapping()
        assert connection.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM outbox_event").fetchone()[0] == 3
    finally:
        connection.close()


@pytest.mark.scenario("WIKI-019")
@pytest.mark.scenario("UPLOAD-013")
def test_personal_publication_reauthorizes_and_commits_the_exact_selected_wiki(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    default_personal_id = _seed_actor(factory)
    selected_personal_id = _uuid("selected-personal-wiki")
    connection = factory.open()
    try:
        with transaction(connection):
            profiles = PersonalWikiProfileRepository()
            current_default = profiles.get_default(connection, _ACTOR)
            assert current_default is not None
            profiles.insert_new_wiki(
                connection,
                space_id=selected_personal_id,
                owner_user_id=_ACTOR,
                display_name="Selected Wiki",
                expected_default_row_version=current_default.row_version,
                created_at_ms=102,
                write_enabled=True,
            )
    finally:
        connection.close()

    preview = _preview(
        factory,
        label="selected-personal",
        markdown="# Exact selected Wiki\n\nBody\n",
        epoch=1,
        part=False,
    )
    command = replace(
        _publish_command(
            preview,
            label="selected-personal-publish",
            epoch=1,
            target_mode="personal",
        ),
        personal_space_id=selected_personal_id,
    )
    service = PublicationService(factory, ScopeResolver(_flags()))
    result = service.publish(command)
    assert [target["space_id"] for target in result.targets] == [selected_personal_id]

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (selected_personal_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (default_personal_id,),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    with pytest.raises(PublicationPreviewIdempotencyError):
        service.publish(
            replace(
                command,
                request_id=_uuid("selected-personal-drift-request"),
                personal_space_id=default_personal_id,
            )
        )
    assert _counts(factory)["knowledge_item"] == 1


def test_publication_curation_job_atomically_releases_search_and_semantic_intent(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview = _preview(
        factory,
        label="automatic-curation",
        markdown="# Automatic curation\n\nDistinct body for release.\n",
        epoch=1,
        part=False,
    )
    result = PublicationService(factory, ScopeResolver(_flags())).publish(
        _publish_command(
            preview,
            label="automatic-curation-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (CanonicalCurationRescanHandler(factory).registered_handler(),)
        ),
        scope_resolver=ScopeResolver(_flags()),
    )
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="curation-unit-test",
        lease_ms=30_000,
    )
    assert claim is not None
    assert claim.job.actor_kind == "system"
    completed = kernel.run_claim(claim)
    assert completed.job.state == "succeeded"
    item_id = str(result.targets[0]["item_id"])
    connection = factory.open(read_only=True)
    try:
        item = connection.execute(
            """SELECT curation_status,last_curation_transition_id
                 FROM knowledge_item WHERE id=?""",
            (item_id,),
        ).fetchone()
        assert item is not None
        assert item["curation_status"] == "published"
        assert item["last_curation_transition_id"] is not None
        assert connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?", (item_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM semantic_edge_intent WHERE source_item_id=?",
            (item_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM durable_job
                 WHERE job_type LIKE 'semantic%'"""
        ).fetchone()[0] == 0
    finally:
        connection.close()

    _assert_postconditions(factory)


@pytest.mark.scenario("UPLOAD-007")
def test_strong_near_duplicate_finding_remains_provisional_and_hidden(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(_flags())
    publisher = PublicationService(factory, scope)
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    first = publisher.publish(
        _publish_command(
            _preview(
                factory,
                label="near-duplicate-crlf",
                markdown="# Normalized candidate\r\n\r\nSame normalized body.\r\n",
                epoch=1,
                part=False,
            ),
            label="near-duplicate-crlf-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    _recovery, first_claim = kernel.recover_and_claim(
        claim_owner="near-duplicate-first-curation",
        lease_ms=30_000,
    )
    assert first_claim is not None
    assert kernel.run_claim(first_claim).job.state == "succeeded"

    second = publisher.publish(
        _publish_command(
            _preview(
                factory,
                label="near-duplicate-lf",
                markdown="# Normalized candidate\n\nSame normalized body.\n",
                epoch=1,
                part=False,
            ),
            label="near-duplicate-lf-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    first_item_id = str(first.targets[0]["item_id"])
    second_item_id = str(second.targets[0]["item_id"])
    assert second_item_id != first_item_id
    _recovery, second_claim = kernel.recover_and_claim(
        claim_owner="near-duplicate-second-curation",
        lease_ms=30_000,
    )
    assert second_claim is not None
    completed = kernel.run_claim(second_claim)
    assert completed.job.state == "succeeded"
    assert completed.job.result["status"] == "pending_review"

    connection = factory.open(read_only=True)
    try:
        item = connection.execute(
            "SELECT curation_status,row_version FROM knowledge_item WHERE id=?",
            (second_item_id,),
        ).fetchone()
        assert tuple(item) == ("provisional", 1)
        finding = connection.execute(
            """SELECT finding_kind,risk_tier,severity,status,decision_mode,
                      required_reviewer,visibility_effect,proposed_action,
                      target_item_id,target_revision_id
                 FROM curation_finding WHERE source_item_id=?""",
            (second_item_id,),
        ).fetchone()
        assert finding is not None
        assert tuple(finding)[:8] == (
            "near_duplicate",
            "T2",
            "warning",
            "pending",
            "human",
            "space_editor",
            "agent_hidden",
            "review_duplicate",
        )
        assert finding["target_item_id"] == first_item_id
        assert finding["target_revision_id"] == first.targets[0]["resolved_revision_id"]
        assert connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?",
            (second_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM semantic_edge_intent WHERE source_item_id=?",
            (second_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM item_curation_transition WHERE item_id=?",
            (second_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM outbox_event
                 WHERE event_type='curation.finding.pending'
                   AND aggregate_id=(SELECT id FROM curation_finding WHERE source_item_id=?)""",
            (second_item_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("UPLOAD-007")
def test_blocker_is_atomically_quarantined_without_search_or_semantic_intent(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    scope = ScopeResolver(_flags())
    result = PublicationService(factory, scope).publish(
        _publish_command(
            _preview(
                factory,
                label="automatic-quarantine",
                markdown="---\ntitle: unterminated\n",
                epoch=1,
                part=False,
            ),
            label="automatic-quarantine-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    item_id = str(result.targets[0]["item_id"])
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="automatic-quarantine-curation",
        lease_ms=30_000,
    )
    assert claim is not None
    completed = kernel.run_claim(claim)
    assert completed.job.state == "succeeded"
    assert completed.job.result["status"] == "quarantined"

    connection = factory.open(read_only=True)
    try:
        assert tuple(
            connection.execute(
                "SELECT curation_status,row_version FROM knowledge_item WHERE id=?",
                (item_id,),
            ).fetchone()
        ) == ("quarantined", 2)
        finding = connection.execute(
            """SELECT finding_kind,risk_tier,severity,status,decision_mode,
                      required_reviewer,visibility_effect,proposed_action
                 FROM curation_finding WHERE source_item_id=?""",
            (item_id,),
        ).fetchone()
        assert tuple(finding) == (
            "lint",
            "T0",
            "blocker",
            "applied",
            "automatic",
            "none",
            "publication_blocked",
            "quarantine",
        )
        assert connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?",
            (item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM semantic_edge_intent WHERE source_item_id=?",
            (item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM item_curation_transition WHERE item_id=?",
            (item_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM audit_event
                 WHERE item_id=? AND action='curation.automatic_quarantine'""",
            (item_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM outbox_event
                 WHERE aggregate_id=? AND event_type='knowledge.item.quarantined'""",
            (item_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("REF-001")
@pytest.mark.scenario("REF-002")
def test_explicit_item_reference_is_revalidated_and_revision_bound_on_release(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    scope = ScopeResolver(_flags())
    publisher = PublicationService(factory, scope)
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )

    target_preview = _preview(
        factory,
        label="reference-target",
        markdown="# Reference target\n\nCanonical target body.\n",
        epoch=1,
        part=False,
    )
    target_result = publisher.publish(
        _publish_command(
            target_preview,
            label="reference-target-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    target_item_id = str(target_result.targets[0]["item_id"])
    target_revision_id = str(target_result.targets[0]["resolved_revision_id"])
    _recovery, target_claim = kernel.recover_and_claim(
        claim_owner="reference-target-curation",
        lease_ms=30_000,
    )
    assert target_claim is not None
    assert kernel.run_claim(target_claim).job.state == "succeeded"

    source_preview = _preview(
        factory,
        label="reference-source",
        markdown=(
            "# Reference source\n\n"
            f"The fixed target is [[item:{target_item_id}]].\n"
        ),
        epoch=1,
        part=False,
    )
    source_result = publisher.publish(
        _publish_command(
            source_preview,
            label="reference-source-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    source_item_id = str(source_result.targets[0]["item_id"])
    source_revision_id = str(source_result.targets[0]["resolved_revision_id"])

    connection = factory.open(read_only=True)
    try:
        job_row = connection.execute(
            """SELECT expected_state_json FROM durable_job
                 WHERE json_extract(input_ref_json,'$.item_id')=?
                   AND actor_kind='system'""",
            (source_item_id,),
        ).fetchone()
        assert job_row is not None
        expected_state = json.loads(job_row["expected_state_json"])
        captured = expected_state["explicit_references"]
        assert len(captured) == 1
        assert captured[0]["ordinal"] == 1
        assert captured[0]["source_space_id"] == personal_id
        assert captured[0]["target_item_id"] == target_item_id
        assert captured[0]["target_revision_id"] == target_revision_id
    finally:
        connection.close()

    _recovery, source_claim = kernel.recover_and_claim(
        claim_owner="reference-source-curation",
        lease_ms=30_000,
    )
    assert source_claim is not None
    assert kernel.run_claim(source_claim).job.state == "succeeded"

    connection = factory.open(read_only=True)
    try:
        reference = connection.execute(
            """SELECT source_space_id,source_item_id,source_revision_id,ordinal,
                      syntax_version,target_space_id,target_item_id,
                      target_revision_id,closure_proof_sha256,actor_user_id,
                      operation_id,effect_key,audit_event_id,outbox_event_id,
                      availability_status,unavailable_reason,row_version
                 FROM knowledge_explicit_reference"""
        ).fetchone()
        assert reference is not None
        assert reference["source_space_id"] == personal_id
        assert reference["source_item_id"] == source_item_id
        assert reference["source_revision_id"] == source_revision_id
        assert reference["ordinal"] == 1
        assert reference["syntax_version"] == "item-ref-v1"
        assert reference["target_space_id"] == personal_id
        assert reference["target_item_id"] == target_item_id
        assert reference["target_revision_id"] == target_revision_id
        assert reference["closure_proof_sha256"] == captured[0]["closure_proof_sha256"]
        assert reference["actor_user_id"] == _ACTOR
        assert reference["operation_id"] == expected_state["operation_id"]
        assert reference["effect_key"] == (
            f"explicit-reference:{source_revision_id}:1"
        )
        assert reference["availability_status"] == "available"
        assert reference["unavailable_reason"] is None
        assert reference["row_version"] == 1
        assert connection.execute(
            "SELECT action FROM audit_event WHERE id=?",
            (reference["audit_event_id"],),
        ).fetchone()[0] == "curation.automatic_release"
        assert connection.execute(
            "SELECT event_type FROM outbox_event WHERE id=?",
            (reference["outbox_event_id"],),
        ).fetchone()[0] == "knowledge.item.published"
        assert connection.execute(
            "SELECT curation_status FROM knowledge_item WHERE id=?",
            (source_item_id,),
        ).fetchone()[0] == "published"
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edge"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM aggregate_knowledge_edge"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    token = base64.urlsafe_b64encode(
        hashlib.sha256(b"reference-newer-revision-token").digest()
    ).rstrip(b"=").decode("ascii")
    editor = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="reference-newer-v1",
            keys={"reference-newer-v1": bytes(reversed(range(32)))},
        ),
        scope_resolver=scope,
    )
    principal = SessionPrincipal(user_id=_ACTOR, captured_scope_epoch=1)
    acquire_command = AcquireLeaseCommand(
        principal=principal,
        space_id=personal_id,
        item_id=target_item_id,
        editor_session_id=_uuid("reference-newer-editor-session"),
        operation_id=_uuid("reference-newer-acquire-operation"),
        request_id=_uuid("reference-newer-acquire-request"),
        raw_token=token,
    )
    lease = editor.acquire(acquire_command)
    newer = editor.save(
        SaveItemCommand(
            principal=principal,
            space_id=personal_id,
            item_id=target_item_id,
            editor_session_id=acquire_command.editor_session_id,
            lease_id=lease.lease_id,
            fencing_generation=lease.fencing_generation,
            expected_item_row_version=2,
            expected_revision_id=target_revision_id,
            operation_id=_uuid("reference-newer-save-operation"),
            request_id=_uuid("reference-newer-save-request"),
            markdown="# Reference target\n\nA newer target revision.\n",
            reason="Prove stable reference revision behavior",
            raw_token=token,
        )
    )
    assert newer.revision_id != target_revision_id
    connection = factory.open(read_only=True)
    try:
        observed = connection.execute(
            """SELECT r.target_revision_id,r.availability_status,
                      i.current_revision_id
                 FROM knowledge_explicit_reference r
                 JOIN knowledge_item i ON i.id=r.target_item_id
                WHERE r.source_item_id=?""",
            (source_item_id,),
        ).fetchone()
        assert tuple(observed) == (
            target_revision_id,
            "available",
            newer.revision_id,
        )
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("REF-005")
def test_explicit_reference_target_revision_drift_rolls_back_source_release(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    scope = ScopeResolver(_flags())
    publisher = PublicationService(factory, scope)
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )

    target_result = publisher.publish(
        _publish_command(
            _preview(
                factory,
                label="reference-drift-target",
                markdown="# Drift target\n\nOriginal target revision.\n",
                epoch=1,
                part=False,
            ),
            label="reference-drift-target-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    target_item_id = str(target_result.targets[0]["item_id"])
    target_revision_id = str(target_result.targets[0]["resolved_revision_id"])
    _recovery, target_claim = kernel.recover_and_claim(
        claim_owner="reference-drift-target-curation",
        lease_ms=30_000,
    )
    assert target_claim is not None
    assert kernel.run_claim(target_claim).job.state == "succeeded"

    source_result = publisher.publish(
        _publish_command(
            _preview(
                factory,
                label="reference-drift-source",
                markdown=(
                    "# Drift source\n\n"
                    f"This binds [[item:{target_item_id}]].\n"
                ),
                epoch=1,
                part=False,
            ),
            label="reference-drift-source-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    source_item_id = str(source_result.targets[0]["item_id"])
    _recovery, source_claim = kernel.recover_and_claim(
        claim_owner="reference-drift-source-curation",
        lease_ms=30_000,
    )
    assert source_claim is not None

    token = base64.urlsafe_b64encode(
        hashlib.sha256(b"reference-drift-edit-token").digest()
    ).rstrip(b"=").decode("ascii")
    editor = ImmutableItemEditService(
        factory,
        key_ring=LeaseHmacKeyRing(
            active_key_id="reference-test-v1",
            keys={"reference-test-v1": bytes(range(32))},
        ),
        scope_resolver=scope,
    )
    principal = SessionPrincipal(user_id=_ACTOR, captured_scope_epoch=1)
    acquire_command = AcquireLeaseCommand(
        principal=principal,
        space_id=personal_id,
        item_id=target_item_id,
        editor_session_id=_uuid("reference-drift-editor-session"),
        operation_id=_uuid("reference-drift-acquire-operation"),
        request_id=_uuid("reference-drift-acquire-request"),
        raw_token=token,
    )
    lease = editor.acquire(acquire_command)
    edited = editor.save(
        SaveItemCommand(
            principal=principal,
            space_id=personal_id,
            item_id=target_item_id,
            editor_session_id=acquire_command.editor_session_id,
            lease_id=lease.lease_id,
            fencing_generation=lease.fencing_generation,
            expected_item_row_version=2,
            expected_revision_id=target_revision_id,
            operation_id=_uuid("reference-drift-save-operation"),
            request_id=_uuid("reference-drift-save-request"),
            markdown="# Drift target\n\nA replacement target revision.\n",
            reason="Exercise reference revision drift",
            raw_token=token,
        )
    )
    assert edited.revision_id != target_revision_id

    rejected = kernel.run_claim(source_claim)
    assert rejected.job.state == "failed"
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT curation_status FROM knowledge_item WHERE id=?",
            (source_item_id,),
        ).fetchone()[0] == "provisional"
        assert connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?",
            (source_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_explicit_reference WHERE source_item_id=?",
            (source_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM semantic_edge_intent WHERE source_item_id=?",
            (source_item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM item_curation_transition
                 WHERE item_id=?""",
            (source_item_id,),
        ).fetchone()[0] == 0
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("REF-003")
@pytest.mark.scenario("REF-004")
def test_inaccessible_explicit_reference_rolls_back_publication_without_disclosure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    foreign_actor = "foreign-publisher"
    foreign_space_id = space_id_for_owner("personal", foreign_actor)
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id=foreign_actor,
                    display_name="Foreign Publisher",
                    password_hash="scrypt:p4.3:foreign",
                    system_role="user",
                    account_status="active",
                    created_at_ms=150,
                    updated_at_ms=150,
                ),
            )
            spaces = SpaceRepository()
            ensured = spaces.ensure_personal(
                connection,
                space_id=foreign_space_id,
                user_id=foreign_actor,
                created_at_ms=150,
            )
            spaces.update_state(
                connection,
                space_id=foreign_space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=151,
            )
    finally:
        connection.close()

    scope = ScopeResolver(_flags())
    foreign_preview = PublicationPreviewService(factory, scope, _Stager()).create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid("foreign-reference-preview-request"),
            operation_id=_uuid("foreign-reference-preview-operation"),
            actor_user_id=foreign_actor,
            actor_scope_epoch=1,
            markdown="# Foreign target\n\nNot in the publisher closure.\n",
        )
    )
    foreign_result = PublicationService(factory, scope).publish(
        PublishPreviewCommand(
            request_id=_uuid("foreign-reference-publish-request"),
            operation_id=_uuid("foreign-reference-publish-operation"),
            actor_user_id=foreign_actor,
            actor_scope_epoch=1,
            preview_id=foreign_preview.preview_id,
            source_snapshot_sha256=foreign_preview.source_snapshot_sha256,
            target_mode="personal",
        )
    )
    foreign_item_id = str(foreign_result.targets[0]["item_id"])
    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    _recovery, foreign_claim = kernel.recover_and_claim(
        claim_owner="foreign-reference-curation",
        lease_ms=30_000,
    )
    assert foreign_claim is not None
    assert kernel.run_claim(foreign_claim).job.state == "succeeded"

    source_preview = _preview(
        factory,
        label="inaccessible-reference-source",
        markdown=(
            "# Inaccessible reference\n\n"
            f"This must fail closed: [[item:{foreign_item_id}]].\n"
        ),
        epoch=1,
        part=False,
    )
    baseline = _publication_state(factory, source_preview.preview_id)
    connection = factory.open(read_only=True)
    try:
        baseline_jobs = connection.execute("SELECT count(*) FROM durable_job").fetchone()[0]
        baseline_references = connection.execute(
            "SELECT count(*) FROM knowledge_explicit_reference"
        ).fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(
        PublicationPreviewAuthorizationError,
        match="not available in the publication closure",
    ):
        PublicationService(factory, scope).publish(
            _publish_command(
                source_preview,
                label="inaccessible-reference-publish",
                epoch=1,
                target_mode="personal",
            )
        )

    assert _publication_state(factory, source_preview.preview_id) == baseline
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM durable_job").fetchone()[0] == baseline_jobs
        assert connection.execute(
            "SELECT count(*) FROM knowledge_explicit_reference"
        ).fetchone()[0] == baseline_references
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (source_preview.preview_id,),
        ).fetchone()[0] == "ready"
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("CUR-001")
@pytest.mark.scenario("PUB-009")
def test_distinct_source_with_exact_body_adds_provenance_without_generation(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    service = PublicationService(factory, ScopeResolver(_flags()))
    first_preview = _preview(
        factory, label="first", markdown="# Same\n\nBody\n", epoch=1, part=False
    )
    first = service.publish(
        _publish_command(
            first_preview,
            label="first-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    second_preview = _preview(
        factory, label="second", markdown="# Same\n\nBody\n", epoch=1, part=False
    )
    second = service.publish(
        _publish_command(
            second_preview,
            label="second-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    assert first.targets[0]["outcome"] == "created"
    assert second.targets[0]["outcome"] == "provenance_added"
    assert second.targets[0]["item_id"] == first.targets[0]["item_id"]
    assert _counts(factory)["knowledge_item"] == 1
    assert _counts(factory)["item_source"] == 2
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (personal_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT group_concat(role,',') FROM item_source ORDER BY created_at"
        ).fetchone()[0] in {"initial,evidence", "evidence,initial"}
        audit = connection.execute(
            "SELECT details_json FROM audit_event "
            "WHERE action='publication.provenance_added'"
        ).fetchone()
        assert audit is not None
        assert json.loads(audit[0])["deterministic_rule_version"] == (
            DETERMINISTIC_INGEST_RULE_VERSION
        )
        committed = connection.execute(
            "SELECT payload_json FROM outbox_event "
            "WHERE aggregate_type='publication_operation' AND aggregate_id=? "
            "AND event_type='publication.committed'",
            (second.publication_operation_id,),
        ).fetchone()
        assert committed is not None
        assert json.loads(committed[0])["deterministic_rule_version"] == (
            DETERMINISTIC_INGEST_RULE_VERSION
        )
    finally:
        connection.close()


@pytest.mark.scenario("CUR-002")
def test_normalized_only_match_creates_distinct_item_and_never_auto_deduplicates(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    service = PublicationService(factory, ScopeResolver(_flags()))
    first_preview = _preview(
        factory,
        label="normalized-crlf",
        markdown="# Same\r\n\r\nBody\r\n",
        epoch=1,
        part=False,
    )
    second_preview = _preview(
        factory,
        label="normalized-lf",
        markdown="# Same\n\nBody\n",
        epoch=1,
        part=False,
    )
    first = service.publish(
        _publish_command(
            first_preview,
            label="normalized-crlf-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    second = service.publish(
        _publish_command(
            second_preview,
            label="normalized-lf-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    assert first.targets[0]["outcome"] == second.targets[0]["outcome"] == "created"
    assert first.targets[0]["item_id"] != second.targets[0]["item_id"]
    assert _counts(factory)["knowledge_item"] == 2
    connection = factory.open(read_only=True)
    try:
        revisions = connection.execute(
            "SELECT raw_content_sha256,normalized_content_sha256 "
            "FROM knowledge_revision ORDER BY id"
        ).fetchall()
        assert len({row[0] for row in revisions}) == 2
        assert len({row[1] for row in revisions}) == 1
        assert connection.execute(
            "SELECT count(*) FROM item_relation WHERE relation_type='exact_duplicate_of'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("CUR-001")
@pytest.mark.scenario("PUB-003")
@pytest.mark.scenario("PUB-011")
def test_both_publication_creates_distinct_items_and_sync_none_lineage(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    _, part_space_id, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="both",
        markdown="# Both\n\nOne transaction\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="both-publish",
        epoch=epoch,
        target_mode="both",
    )
    scope = ScopeResolver(_flags(part=True))
    result = PublicationService(factory, scope).publish(command)
    assert result.lineage_group_id is not None
    assert [target["space_id"] for target in result.targets] == [
        personal_id,
        part_space_id,
    ]
    assert {target["outcome"] for target in result.targets} == {"created"}
    assert len({target["item_id"] for target in result.targets}) == 2
    assert len({target["resolved_revision_id"] for target in result.targets}) == 2
    assert _counts(factory) == {
        "publication_operation": 1,
        "publication_target": 2,
        "lineage_group": 1,
        "lineage_member": 2,
        "knowledge_item": 2,
        "knowledge_revision": 2,
        "item_source": 2,
    }
    _assert_postconditions(factory)
    connection = factory.open(read_only=True)
    try:
        group = connection.execute(
            "SELECT sync_policy FROM lineage_group"
        ).fetchone()[0]
        members = connection.execute(
            """SELECT member_role,derived_from_item_id,derived_from_revision_id
               FROM lineage_member ORDER BY member_role"""
        ).fetchall()
        assert group == "none"
        assert [row["member_role"] for row in members] == ["origin", "part_copy"]
        assert members[0]["derived_from_item_id"] is None
        assert members[1]["derived_from_item_id"] is not None
        provenance = connection.execute(
            """SELECT target.target_space_id,target.item_id,
                      target.resolved_revision_id,
                      operation.artifact_id AS operation_artifact_id,
                      operation.extraction_snapshot_id AS operation_snapshot_id,
                      source.artifact_id AS source_artifact_id,
                      source.extraction_snapshot_id AS source_snapshot_id,
                      revision.raw_content_sha256 AS revision_raw_sha256,
                      revision.normalized_content_sha256 AS revision_normalized_sha256,
                      artifact.raw_sha256 AS artifact_raw_sha256,
                      snapshot.normalized_content_sha256 AS snapshot_normalized_sha256
                 FROM publication_operation AS operation
                 JOIN publication_target AS target
                   ON target.publication_operation_id=operation.id
                 JOIN item_source AS source
                   ON source.item_id=target.item_id
                  AND source.revision_id=target.resolved_revision_id
                 JOIN knowledge_revision AS revision
                   ON revision.id=target.resolved_revision_id
                 JOIN source_artifact AS artifact ON artifact.id=operation.artifact_id
                 JOIN extraction_snapshot AS snapshot
                   ON snapshot.id=operation.extraction_snapshot_id
                  AND snapshot.artifact_id=operation.artifact_id
                WHERE operation.id=? ORDER BY target.target_space_id""",
            (result.publication_operation_id,),
        ).fetchall()
        assert len(provenance) == 2
        result_by_space = {target["space_id"]: target for target in result.targets}
        for row in provenance:
            target = result_by_space[row["target_space_id"]]
            assert row["item_id"] == target["item_id"]
            assert row["resolved_revision_id"] == target["resolved_revision_id"]
            assert (
                row["operation_artifact_id"]
                == row["source_artifact_id"]
                == result.source_artifact_id
            )
            assert (
                row["operation_snapshot_id"]
                == row["source_snapshot_id"]
                == preview.extraction_snapshot_id
            )
            assert row["revision_raw_sha256"] == row["artifact_raw_sha256"]
            assert (
                row["revision_normalized_sha256"]
                == row["snapshot_normalized_sha256"]
                == preview.source_snapshot_sha256
            )
            assert connection.execute(
                """SELECT count(*) FROM audit_event
                     WHERE operation_id=? AND item_id=? AND new_revision_id=?""",
                (command.operation_id, target["item_id"], target["resolved_revision_id"]),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM outbox_event
                     WHERE aggregate_type='knowledge_item' AND aggregate_id=?
                       AND event_type='knowledge.item.provisional.created'""",
                (target["item_id"],),
            ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM outbox_event
                 WHERE aggregate_type='publication_operation' AND aggregate_id=?
                   AND event_type='publication.committed'""",
            (result.publication_operation_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()

    kernel = DurableWorkKernel(
        factory,
        HandlerRegistry(
            (
                CanonicalCurationRescanHandler(
                    factory,
                    scope_resolver=scope,
                ).registered_handler(),
            )
        ),
        scope_resolver=scope,
    )
    completed_ids: set[str] = set()
    for ordinal in (1, 2):
        _recovery, claim = kernel.recover_and_claim(
            claim_owner=f"both-curation-{ordinal}",
            lease_ms=30_000,
        )
        assert claim is not None
        run = kernel.run_claim(claim)
        assert run.job.state == "succeeded"
        completed_ids.add(str(claim.job.input_ref["item_id"]))
    assert completed_ids == {str(target["item_id"]) for target in result.targets}
    _recovery, claim = kernel.recover_and_claim(
        claim_owner="both-curation-empty",
        lease_ms=30_000,
    )
    assert claim is None
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            """SELECT count(*) FROM knowledge_item
                 WHERE curation_status='published'"""
        ).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM search_document").fetchone()[0] == 2
        for target in result.targets:
            assert connection.execute(
                "SELECT count(*) FROM semantic_edge_intent WHERE source_item_id=?",
                (target["item_id"],),
            ).fetchone()[0] >= 1
        assert connection.execute(
            "SELECT count(*) FROM durable_job WHERE job_type LIKE 'semantic%'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("PUB-002")
def test_part_only_publication_creates_only_the_current_part_item(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    personal_id = _seed_actor(factory)
    _, part_space_id, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="part-only",
        markdown="# Part only\n",
        epoch=epoch,
        part=True,
    )
    result = PublicationService(factory, ScopeResolver(_flags(part=True))).publish(
        _publish_command(
            preview,
            label="part-only-publish",
            epoch=epoch,
            target_mode="part",
        )
    )
    assert result.lineage_group_id is None
    assert result.targets == (
        {
            "created_revision_id": result.targets[0]["resolved_revision_id"],
            "finding_id": None,
            "item_id": result.targets[0]["item_id"],
            "outcome": "created",
            "resolved_revision_id": result.targets[0]["resolved_revision_id"],
            "space_id": part_space_id,
            "source_artifact_id": result.source_artifact_id,
        },
    )
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?", (personal_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?", (part_space_id,)
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("PUB-003")
@pytest.mark.scenario("PUB-009")
def test_both_may_add_personal_provenance_while_creating_part_atomically(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    publisher = PublicationService(factory, ScopeResolver(_flags()))
    personal_preview = _preview(
        factory,
        label="mixed-personal",
        markdown="# Mixed\n\nBody\n",
        epoch=1,
        part=False,
    )
    personal = publisher.publish(
        _publish_command(
            personal_preview,
            label="mixed-personal-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    _, part_space_id, epoch = _assign_part(factory)
    both_preview = _preview(
        factory,
        label="mixed-both",
        markdown="# Mixed\n\nBody\n",
        epoch=epoch,
        part=True,
    )
    both = PublicationService(factory, ScopeResolver(_flags(part=True))).publish(
        _publish_command(
            both_preview,
            label="mixed-both-publish",
            epoch=epoch,
            target_mode="both",
        )
    )
    assert [target["outcome"] for target in both.targets] == [
        "provenance_added",
        "created",
    ]
    assert both.targets[0]["item_id"] == personal.targets[0]["item_id"]
    assert both.targets[1]["space_id"] == part_space_id
    assert both.lineage_group_id is not None
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM knowledge_revision").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM item_source").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM lineage_member").fetchone()[0] == 2
    finally:
        connection.close()
    _assert_postconditions(factory)


class _FailingOutbox(OutboxRepository):
    def append(self, connection, draft):
        raise RuntimeError("P4.3 failpoint")


@pytest.mark.scenario("PUB-004")
def test_publication_failure_rolls_back_every_target_and_preview_consumption(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="rollback",
        markdown="# Rollback\n",
        epoch=epoch,
        part=True,
    )
    service = PublicationService(
        factory,
        ScopeResolver(_flags(part=True)),
        outbox=_FailingOutbox(),
    )
    with pytest.raises(RuntimeError, match="P4.3 failpoint"):
        service.publish(
            _publish_command(
                preview,
                label="rollback-publish",
                epoch=epoch,
                target_mode="both",
            )
        )
    assert _counts(factory) == {
        "publication_operation": 0,
        "publication_target": 0,
        "lineage_group": 0,
        "lineage_member": 0,
        "knowledge_item": 0,
        "knowledge_revision": 0,
        "item_source": 0,
    }
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?", (preview.preview_id,)
        ).fetchone()[0] == "ready"
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failpoint",
    (
        "publication.after_operation",
        "publication.after_first_target_item",
        "publication.after_first_target_revision",
        "publication.after_second_target_item",
        "publication.after_second_target_revision",
        "publication.after_first_target_result",
        "publication.after_audit",
        "publication.after_outbox",
        "publication.before_commit",
    ),
)
def test_publication_failpoints_before_commit_roll_back_every_effect(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label=f"publication-fault:{failpoint}",
        markdown="# Publication fault\n",
        epoch=epoch,
        part=True,
    )

    def stop(name: str) -> None:
        if name == failpoint:
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match=failpoint):
        PublicationService(
            factory,
            ScopeResolver(_flags(part=True)),
            failpoint=stop,
        ).publish(
            _publish_command(
                preview,
                label=f"publication-fault-command:{failpoint}",
                epoch=epoch,
                target_mode="both",
            )
        )
    assert _counts(factory) == {
        "publication_operation": 0,
        "publication_target": 0,
        "lineage_group": 0,
        "lineage_member": 0,
        "knowledge_item": 0,
        "knowledge_revision": 0,
        "item_source": 0,
    }
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (preview.preview_id,),
        ).fetchone()[0] == "ready"
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_publication_response_loss_after_commit_replays_exact_result(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="publication-post-commit",
        markdown="# Publication committed\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="publication-post-commit-command",
        epoch=epoch,
        target_mode="both",
    )

    def stop(name: str) -> None:
        if name == "publication.after_commit_before_response":
            raise RuntimeError(name)

    with pytest.raises(RuntimeError, match="publication.after_commit_before_response"):
        PublicationService(
            factory,
            ScopeResolver(_flags(part=True)),
            failpoint=stop,
        ).publish(command)

    replay = PublicationService(
        factory, ScopeResolver(_flags(part=True))
    ).publish(command)
    assert replay.target_mode == "both"
    assert len(replay.targets) == 2
    counts = _counts(factory)
    assert counts["publication_operation"] == 1
    assert counts["publication_target"] == 2
    assert counts["knowledge_item"] == 2
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (preview.preview_id,),
        ).fetchone()[0] == "consumed"
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("PUB-008")
def test_part_capability_drift_rejects_without_effects(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="part-drift",
        markdown="# Part\n",
        epoch=epoch,
        part=True,
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, ScopeResolver(_flags(part=False))).publish(
            _publish_command(
                preview,
                label="part-drift-publish",
                epoch=epoch,
                target_mode="part",
            )
        )
    assert _counts(factory)["publication_operation"] == 0


@pytest.mark.scenario("PUB-010")
@pytest.mark.scenario("PUB-011")
@pytest.mark.scenario("CUR-003")
def test_stable_notion_source_change_creates_fixed_update_proposal_and_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    snapshots = _NotionSnapshots("# Stable\n\nFirst\n")
    previews = PublicationPreviewService(
        factory,
        ScopeResolver(_flags()),
        _Stager(),
        notion_snapshots=snapshots,
    )
    first_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-1-request"),
            operation_id=_uuid("notion-1-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            page_id="stable-page",
        )
    )
    publisher = PublicationService(factory, ScopeResolver(_flags()))
    first = publisher.publish(
        _publish_command(
            first_preview,
            label="notion-1-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    unchanged_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-same-request"),
            operation_id=_uuid("notion-same-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            page_id="stable-page",
        )
    )
    unchanged = publisher.publish(
        _publish_command(
            unchanged_preview,
            label="notion-same-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    assert unchanged.targets[0]["outcome"] == "no_op"
    assert _counts(factory)["knowledge_item"] == 1
    assert _counts(factory)["item_source"] == 1
    connection = factory.open(read_only=True)
    try:
        generation_before_change = connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?",
            (first.targets[0]["space_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    snapshots.markdown = "# Stable\n\nChanged\n"
    snapshots.revision = 2
    second_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-2-request"),
            operation_id=_uuid("notion-2-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            page_id="stable-page",
        )
    )
    command = _publish_command(
        second_preview,
        label="notion-2-publish",
        epoch=1,
        target_mode="personal",
    )
    changed = publisher.publish(command)
    replay = publisher.publish(
        replace(command, request_id=_uuid("notion-2-replay-request"))
    )
    assert replay == changed
    target = changed.targets[0]
    assert target == {
        "created_revision_id": None,
        "finding_id": target["finding_id"],
        "item_id": first.targets[0]["item_id"],
        "outcome": "update_proposed",
        "resolved_revision_id": first.targets[0]["resolved_revision_id"],
        "space_id": first.targets[0]["space_id"],
        "source_artifact_id": second_preview.artifact_id,
    }
    assert isinstance(target["finding_id"], str)
    assert _counts(factory)["knowledge_item"] == 1
    assert _counts(factory)["knowledge_revision"] == 1
    assert _counts(factory)["item_source"] == 1

    connection = factory.open(read_only=True)
    try:
        finding = connection.execute(
            """SELECT * FROM curation_finding WHERE id=?""",
            (target["finding_id"],),
        ).fetchone()
        assert finding is not None
        assert tuple(
            finding[key]
            for key in (
                "space_id",
                "finding_kind",
                "risk_tier",
                "severity",
                "status",
                "decision_mode",
                "required_reviewer",
                "visibility_effect",
                "proposed_action",
                "source_item_id",
                "source_revision_id",
                "target_item_id",
                "target_revision_id",
            )
        ) == (
            target["space_id"],
            "update",
            "T2",
            "info",
            "pending",
            "human",
            "space_editor",
            "agent_hidden",
            "update",
            target["item_id"],
            target["resolved_revision_id"],
            None,
            None,
        )
        evidence = json.loads(finding["evidence_json"])
        assert evidence == {
            "captured_part_id": None,
            "extraction_snapshot_id": second_preview.extraction_snapshot_id,
            "extraction_snapshot_sha256": second_preview.source_snapshot_sha256,
            "notion_asset_manifest_sha256": hashlib.sha256(b"[]").hexdigest(),
            "notion_owner_user_id": _ACTOR,
            "notion_page_id": "stable-page",
            "notion_revision": 2,
            "notion_source_snapshot_sha256": json.loads(
                connection.execute(
                    "SELECT metadata_json FROM source_artifact WHERE id=?",
                    (second_preview.artifact_id,),
                ).fetchone()[0]
            )["notion_provenance"]["source_snapshot_sha256"],
            "notion_updated_at": 2,
            "preview_id": second_preview.preview_id,
            "proposed_content_sha256": second_preview.source_snapshot_sha256,
            "proposed_normalized_content_sha256": hashlib.sha256(
                snapshots.markdown.encode("utf-8")
            ).hexdigest(),
            "publication_operation_id": changed.publication_operation_id,
            "publisher_user_id": _ACTOR,
            "schema_version": 1,
            "source_artifact_id": second_preview.artifact_id,
            "target_item_id": target["item_id"],
            "target_revision_id": target["resolved_revision_id"],
        }
        proposal = connection.execute(
            "SELECT * FROM change_proposal WHERE finding_id=?",
            (target["finding_id"],),
        ).fetchone()
        assert proposal is not None
        assert tuple(
            proposal[key]
            for key in (
                "finding_input_row_version",
                "proposal_kind",
                "proposal_series_id",
                "proposal_version",
                "supersedes_proposal_id",
                "base_item_id",
                "base_revision_id",
                "source_item_id",
                "source_revision_id",
                "target_item_id",
                "target_revision_id",
                "source_artifact_id",
                "extraction_snapshot_id",
                "proposed_content_md",
                "proposed_content_blob_hash",
                "proposed_content_size_bytes",
                "proposed_content_sha256",
                "state",
                "row_version",
            )
        ) == (
            1,
            "stable_source_update",
            proposal["id"],
            1,
            None,
            target["item_id"],
            target["resolved_revision_id"],
            None,
            None,
            target["item_id"],
            target["resolved_revision_id"],
            second_preview.artifact_id,
            second_preview.extraction_snapshot_id,
            snapshots.markdown,
            None,
            len(snapshots.markdown.encode("utf-8")),
            second_preview.source_snapshot_sha256,
            "pending_review",
            1,
        )
        expected_preview = {
            "base_item_id": target["item_id"],
            "base_revision_id": target["resolved_revision_id"],
            "extraction_snapshot_id": second_preview.extraction_snapshot_id,
            "extraction_snapshot_sha256": second_preview.source_snapshot_sha256,
            "finding_input_row_version": 1,
            "proposal_kind": "stable_source_update",
            "proposal_version": 1,
            "proposed_content_sha256": second_preview.source_snapshot_sha256,
            "rule_version": "p4.4-stable-source-update-v1",
            "source_artifact_id": second_preview.artifact_id,
            "source_item_id": None,
            "source_revision_id": None,
            "target_item_id": target["item_id"],
            "target_revision_id": target["resolved_revision_id"],
        }
        assert proposal["preview_sha256"] == hashlib.sha256(
            json.dumps(
                expected_preview,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (second_preview.preview_id,),
        ).fetchone()[0] == "consumed"
        assert connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?",
            (target["space_id"],),
        ).fetchone()[0] == generation_before_change
        audit_row = connection.execute(
            """SELECT details_json FROM audit_event
               WHERE action='publication.update_proposed'
                 AND item_id=? AND base_revision_id=?""",
            (target["item_id"], target["resolved_revision_id"]),
        ).fetchone()
        assert audit_row is not None
        audit_details = json.loads(audit_row[0])
        assert audit_details["finding_id"] == target["finding_id"]
        assert audit_details["proposal_id"] == proposal["id"]
        outbox_row = connection.execute(
            """SELECT payload_json FROM outbox_event
               WHERE aggregate_type='curation_finding' AND aggregate_id=?
                 AND event_type='curation.finding.pending'""",
            (target["finding_id"],),
        ).fetchone()
        assert outbox_row is not None
        outbox_payload = json.loads(outbox_row[0])
        assert outbox_payload["finding_id"] == target["finding_id"]
        assert outbox_payload["proposal_id"] == proposal["id"]
    finally:
        connection.close()
    _assert_postconditions(factory)


def test_changed_notion_proposal_failpoint_rolls_back_finding_and_consumption(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    snapshots = _NotionSnapshots("# Stable\n\nFirst\n")
    previews = PublicationPreviewService(
        factory,
        ScopeResolver(_flags()),
        _Stager(),
        notion_snapshots=snapshots,
    )
    first_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-rollback-first-request"),
            operation_id=_uuid("notion-rollback-first-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            page_id="rollback-page",
        )
    )
    PublicationService(factory, ScopeResolver(_flags())).publish(
        _publish_command(
            first_preview,
            label="notion-rollback-first-publish",
            epoch=1,
            target_mode="personal",
        )
    )
    snapshots.markdown = "# Stable\n\nChanged\n"
    snapshots.revision = 2
    changed_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-rollback-change-request"),
            operation_id=_uuid("notion-rollback-change-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=1,
            page_id="rollback-page",
        )
    )
    publisher = PublicationService(
        factory,
        ScopeResolver(_flags()),
        outbox=_FailingOutbox(),
    )
    with pytest.raises(RuntimeError, match="P4.3 failpoint"):
        publisher.publish(
            _publish_command(
                changed_preview,
                label="notion-rollback-change-publish",
                epoch=1,
                target_mode="personal",
            )
        )
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM curation_finding"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM change_proposal"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM publication_operation"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM publication_target"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM item_source"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?",
            (changed_preview.preview_id,),
        ).fetchone()[0] == "ready"
        assert connection.execute(
            """SELECT count(*) FROM operation_dedup
               WHERE operation_type='publication'"""
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM audit_event
               WHERE action='publication.update_proposed'"""
        ).fetchone()[0] == 0
    finally:
        connection.close()
    _assert_postconditions(factory)


def test_part_notion_update_proposal_fixes_publishers_captured_part(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    part_id, part_space_id, epoch = _assign_part(factory)
    snapshots = _NotionSnapshots("# Part source\n\nFirst\n")
    previews = PublicationPreviewService(
        factory,
        ScopeResolver(_flags(part=True)),
        _Stager(),
        notion_snapshots=snapshots,
    )
    first_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("part-notion-first-request"),
            operation_id=_uuid("part-notion-first-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=epoch,
            page_id="part-stable-page",
        )
    )
    publisher = PublicationService(factory, ScopeResolver(_flags(part=True)))
    first = publisher.publish(
        _publish_command(
            first_preview,
            label="part-notion-first-publish",
            epoch=epoch,
            target_mode="part",
        )
    )
    assert first.targets[0]["space_id"] == part_space_id
    snapshots.markdown = "# Part source\n\nChanged\n"
    snapshots.revision = 2
    changed_preview = previews.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("part-notion-change-request"),
            operation_id=_uuid("part-notion-change-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=epoch,
            page_id="part-stable-page",
        )
    )
    changed = publisher.publish(
        _publish_command(
            changed_preview,
            label="part-notion-change-publish",
            epoch=epoch,
            target_mode="part",
        )
    )
    assert changed.targets[0]["outcome"] == "update_proposed"
    assert changed.targets[0]["space_id"] == part_space_id
    assert changed.targets[0]["item_id"] == first.targets[0]["item_id"]
    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            """SELECT evidence_json FROM curation_finding WHERE id=?""",
            (changed.targets[0]["finding_id"],),
        ).fetchone()
        assert row is not None
        evidence = json.loads(row[0])
        assert evidence["captured_part_id"] == part_id
        assert evidence["publisher_user_id"] == _ACTOR
        assert evidence["notion_owner_user_id"] == _ACTOR
        assert evidence["notion_page_id"] == "part-stable-page"
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item WHERE space_id=?",
            (part_space_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision"
        ).fetchone()[0] == 1
    finally:
        connection.close()
    _assert_postconditions(factory)


def test_both_notion_repeats_remain_independent_noops_then_two_proposals(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    snapshots = _NotionSnapshots("# Dual source\n\nFirst\n")
    previews = PublicationPreviewService(
        factory,
        ScopeResolver(_flags(part=True)),
        _Stager(),
        notion_snapshots=snapshots,
    )
    publisher = PublicationService(factory, ScopeResolver(_flags(part=True)))

    def create_preview(label: str):
        return previews.create_notion_preview(
            CreateNotionPreviewCommand(
                request_id=_uuid(f"{label}:request"),
                operation_id=_uuid(f"{label}:operation"),
                actor_user_id=_ACTOR,
                actor_scope_epoch=epoch,
                page_id="dual-stable-page",
            )
        )

    first = publisher.publish(
        _publish_command(
            create_preview("dual-first-preview"),
            label="dual-first-publish",
            epoch=epoch,
            target_mode="both",
        )
    )
    first_by_space = {target["space_id"]: target for target in first.targets}
    assert {target["outcome"] for target in first.targets} == {"created"}
    unchanged = publisher.publish(
        _publish_command(
            create_preview("dual-unchanged-preview"),
            label="dual-unchanged-publish",
            epoch=epoch,
            target_mode="both",
        )
    )
    assert [target["outcome"] for target in unchanged.targets] == ["no_op", "no_op"]
    assert {
        target["space_id"]: (
            target["item_id"], target["resolved_revision_id"]
        )
        for target in unchanged.targets
    } == {
        space_id: (target["item_id"], target["resolved_revision_id"])
        for space_id, target in first_by_space.items()
    }
    snapshots.markdown = "# Dual source\n\nChanged\n"
    snapshots.revision = 2
    changed = publisher.publish(
        _publish_command(
            create_preview("dual-changed-preview"),
            label="dual-changed-publish",
            epoch=epoch,
            target_mode="both",
        )
    )
    assert [target["outcome"] for target in changed.targets] == [
        "update_proposed",
        "update_proposed",
    ]
    assert len({target["finding_id"] for target in changed.targets}) == 2
    for target in changed.targets:
        original = first_by_space[target["space_id"]]
        assert target["item_id"] == original["item_id"]
        assert target["resolved_revision_id"] == original["resolved_revision_id"]
        assert target["created_revision_id"] is None

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_item"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM item_source"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM curation_finding"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM change_proposal"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM lineage_member"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM lineage_group"
        ).fetchone()[0] == 3
        assert connection.execute(
            """SELECT count(*) FROM curation_finding AS finding
               JOIN change_proposal AS proposal
                 ON proposal.finding_id=finding.id
               JOIN knowledge_item AS item ON item.id=finding.source_item_id
               WHERE finding.space_id=item.space_id
                 AND finding.source_revision_id=item.current_revision_id
                 AND proposal.target_item_id=item.id
                 AND proposal.target_revision_id=item.current_revision_id"""
        ).fetchone()[0] == 2
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("PUB-006")
def test_publication_operation_input_drift_is_rejected(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview = _preview(
        factory, label="drift", markdown="# Drift\n", epoch=1, part=False
    )
    service = PublicationService(factory, ScopeResolver(_flags()))
    command = _publish_command(
        preview,
        label="drift-publish",
        epoch=1,
        target_mode="personal",
    )
    service.publish(command)
    other = _preview(
        factory, label="drift-other", markdown="# Other\n", epoch=1, part=False
    )
    with pytest.raises(PublicationPreviewIdempotencyError):
        service.publish(replace(command, preview_id=other.preview_id))


@pytest.mark.scenario("PUB-007")
@pytest.mark.scenario("PUB-013")
def test_source_snapshot_drift_and_second_operation_on_consumed_preview_are_rejected(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview = _preview(
        factory, label="snapshot", markdown="# Snapshot\n", epoch=1, part=False
    )
    service = PublicationService(factory, ScopeResolver(_flags()))
    first = _publish_command(
        preview,
        label="snapshot-publish",
        epoch=1,
        target_mode="personal",
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        service.publish(replace(first, source_snapshot_sha256="0" * 64))
    assert _counts(factory)["publication_operation"] == 0
    service.publish(first)
    with pytest.raises(PublicationPreviewAuthorizationError):
        service.publish(
            replace(
                first,
                request_id=_uuid("snapshot-second-request"),
                operation_id=_uuid("snapshot-second-operation"),
            )
        )
    assert _counts(factory)["publication_operation"] == 1


@pytest.mark.scenario("PUB-004")
def test_every_publication_sql_write_boundary_rolls_back_without_partial_dual_outcome(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="sql-failpoint-matrix",
        markdown="# SQL failpoint matrix\n\nAll or none.\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="sql-failpoint-matrix-publish",
        epoch=epoch,
        target_mode="both",
    )
    service = PublicationService(factory, ScopeResolver(_flags(part=True)))
    result, matrix = _exercise_publication_sql_failpoint_matrix(
        factory=factory,
        service=service,
        command=command,
        preview_id=preview.preview_id,
        monkeypatch=monkeypatch,
        minimum_writes=20,
    )
    print(f"dual_create_sql_write_boundaries={len(matrix)}")
    # Each target contributes one provisional outbox plus both the canonical
    # Markdown and v12 temporal-aware Markdown-v2 projection advances, and one
    # exact automatic document-curation job inside the publication transaction.
    assert len(matrix) == 30
    assert [target["outcome"] for target in result.targets] == ["created", "created"]
    assert len({target["item_id"] for target in result.targets}) == 2
    assert len({target["resolved_revision_id"] for target in result.targets}) == 2


@pytest.mark.scenario("PUB-004")
@pytest.mark.scenario("PUB-010")
def test_every_changed_notion_sql_write_boundary_rolls_back_without_partial_proposal(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    snapshots = _NotionSnapshots("# Stable failpoint\n\nFirst\n")
    preview_service = PublicationPreviewService(
        factory,
        ScopeResolver(_flags(part=True)),
        _Stager(),
        notion_snapshots=snapshots,
    )
    first_preview = preview_service.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-matrix-first-request"),
            operation_id=_uuid("notion-matrix-first-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=epoch,
            page_id="notion-matrix-page",
        )
    )
    publisher = PublicationService(factory, ScopeResolver(_flags(part=True)))
    publisher.publish(
        _publish_command(
            first_preview,
            label="notion-matrix-first-publish",
            epoch=epoch,
            target_mode="both",
        )
    )

    snapshots.markdown = "# Stable failpoint\n\nChanged\n"
    snapshots.revision = 2
    changed_preview = preview_service.create_notion_preview(
        CreateNotionPreviewCommand(
            request_id=_uuid("notion-matrix-changed-request"),
            operation_id=_uuid("notion-matrix-changed-operation"),
            actor_user_id=_ACTOR,
            actor_scope_epoch=epoch,
            page_id="notion-matrix-page",
        )
    )
    command = _publish_command(
        changed_preview,
        label="notion-matrix-changed-publish",
        epoch=epoch,
        target_mode="both",
    )
    result, matrix = _exercise_publication_sql_failpoint_matrix(
        factory=factory,
        service=publisher,
        command=command,
        preview_id=changed_preview.preview_id,
        monkeypatch=monkeypatch,
        minimum_writes=15,
    )
    print(f"dual_update_proposal_sql_write_boundaries={len(matrix)}")
    # The two target-space outbox rows each carry canonical Markdown and v12
    # temporal-aware Markdown-v2 desired-generation projection writes.
    assert len(matrix) == 20
    assert [target["outcome"] for target in result.targets] == [
        "update_proposed",
        "update_proposed",
    ]
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM knowledge_revision").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM curation_finding").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM change_proposal").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.scenario("PUB-005")
def test_exact_publication_operation_replays_one_thousand_times_without_duplicate_effect(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="thousand-retries",
        markdown="# Thousand retries\n\nExactly one durable result.\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="thousand-retries-publish",
        epoch=epoch,
        target_mode="both",
    )
    first = PublicationService(factory, ScopeResolver(_flags(part=True))).publish(
        command
    )
    for retry in range(1_000):
        if retry % 100 == 0:
            service = PublicationService(factory, ScopeResolver(_flags(part=True)))
        replay = service.publish(
            replace(command, request_id=_uuid(f"thousand-retries-request-{retry}"))
        )
        assert replay == first

    assert _counts(factory) == {
        "publication_operation": 1,
        "publication_target": 2,
        "lineage_group": 1,
        "lineage_member": 2,
        "knowledge_item": 2,
        "knowledge_revision": 2,
        "item_source": 2,
    }
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE operation_id=?",
            (command.operation_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE logical_key LIKE ?",
            (f"publication:{first.publication_operation_id}:%",),
        ).fetchone()[0] == 3
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.scenario("PUB-008")
@pytest.mark.scenario("PUB-013")
def test_unassigned_and_membership_stale_actors_cannot_publish_to_part(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    unassigned_preview = _preview(
        factory,
        label="unassigned-part",
        markdown="# Personal only\n",
        epoch=1,
        part=False,
    )
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, ScopeResolver(_flags(part=True))).publish(
            _publish_command(
                unassigned_preview,
                label="unassigned-part-publish",
                epoch=1,
                target_mode="part",
            )
        )

    part_id, _, epoch = _assign_part(factory)
    stale_preview = _preview(
        factory,
        label="stale-part",
        markdown="# Stale part\n",
        epoch=epoch,
        part=True,
    )
    organization = OrganizationRepository()
    users = UserRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            membership = organization.get_current_membership(connection, _ACTOR)
            actor = users.require(connection, _ACTOR)
            assert membership is not None and membership.part_id == part_id
            part_space = organization.get_part_space_state(connection, part_id)
            leader = organization.get_active_part_leader(connection, part_id)
            assert part_space is not None and leader is not None
            organization.set_part_space_state(
                connection,
                part_id=part_id,
                expected_row_version=part_space.row_version,
                status=part_space.status,
                write_enabled=False,
                updated_at_ms=200,
            )
            organization.close_part_leader(
                connection,
                part_id=part_id,
                expected_assignment_id=leader.id,
                closed_at_ms=200,
            )
            organization.close_current_membership(
                connection,
                user_id=_ACTOR,
                expected_membership_id=membership.id,
                closed_at_ms=200,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id=_ACTOR,
                expected_current_part_id=part_id,
                target_part_id=None,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=200,
            )
    finally:
        connection.close()

    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, ScopeResolver(_flags(part=True))).publish(
            _publish_command(
                stale_preview,
                label="stale-part-publish",
                epoch=epoch,
                target_mode="part",
            )
        )
    assert _counts(factory)["publication_operation"] == 0

    connection = factory.open(read_only=True)
    try:
        current = users.require(connection, _ACTOR)
    finally:
        connection.close()
    actor_changed_preview = _preview(
        factory,
        label="actor-changed",
        markdown="# Actor changed\n",
        epoch=current.scope_epoch,
        part=False,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            actor = users.require(connection, _ACTOR)
            users.transition_account_status(
                connection,
                user_id=_ACTOR,
                target_status="disabled",
                expected_row_version=actor.row_version,
                changed_at_ms=300,
            )
    finally:
        connection.close()
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, ScopeResolver(_flags())).publish(
            _publish_command(
                actor_changed_preview,
                label="actor-changed-publish",
                epoch=current.scope_epoch,
                target_mode="personal",
            )
        )
    assert _counts(factory)["publication_operation"] == 0


@pytest.mark.scenario("PUB-013")
def test_expired_ready_preview_cannot_commit_publication(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview = _preview(
        factory,
        label="expired-commit",
        markdown="# Expired\n",
        epoch=1,
        part=False,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            expired = PublicationPreviewRepository().expire_ready(
                connection,
                preview_id=preview.preview_id,
                now_ms=preview.expires_at_ms + 1,
            )
    finally:
        connection.close()
    assert expired.status == "expired"
    with pytest.raises(PublicationPreviewAuthorizationError):
        PublicationService(factory, ScopeResolver(_flags())).publish(
            _publish_command(
                preview,
                label="expired-commit-publish",
                epoch=1,
                target_mode="personal",
            )
        )
    assert _counts(factory)["publication_operation"] == 0
    state = _publication_state(factory, preview.preview_id)
    assert state["preview"] is not None and state["preview"][0] == "expired"


@pytest.mark.concurrency
@pytest.mark.scenario("PUB-012")
def test_ready_preview_is_polled_cancelled_and_repolled_across_process_restarts(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    preview = _preview(
        factory,
        label="preview-process",
        markdown="# Durable preview\n",
        epoch=1,
        part=False,
    )
    polled = _run_publication_processes(
        isolated_knowledge_layout,
        label="preview-process-poll-ready",
        payloads=(
            {
                "_action": "poll",
                "actor_scope_epoch": 1,
                "actor_user_id": _ACTOR,
                "preview_id": preview.preview_id,
            },
        ),
    )
    assert polled[0]["kind"] == "ok"
    assert polled[0]["result"]["status"] == "ready"
    assert (
        polled[0]["result"]["source_snapshot_sha256"]
        == preview.source_snapshot_sha256
    )

    cancelled = _run_publication_processes(
        isolated_knowledge_layout,
        label="preview-process-cancel",
        payloads=(
            {
                "_action": "cancel",
                "actor_scope_epoch": 1,
                "actor_user_id": _ACTOR,
                "operation_id": _uuid("preview-process-cancel-operation"),
                "preview_id": preview.preview_id,
                "request_id": _uuid("preview-process-cancel-request"),
            },
        ),
    )
    assert cancelled[0]["kind"] == "ok"
    assert cancelled[0]["result"]["status"] == "cancelled"

    repolled = _run_publication_processes(
        isolated_knowledge_layout,
        label="preview-process-poll-cancelled",
        payloads=(
            {
                "_action": "poll",
                "actor_scope_epoch": 1,
                "actor_user_id": _ACTOR,
                "preview_id": preview.preview_id,
            },
        ),
    )
    assert repolled[0]["kind"] == "ok"
    assert repolled[0]["result"]["status"] == "cancelled"
    connection = factory.open(read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_revision").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.concurrency
@pytest.mark.scenario("PUB-003")
@pytest.mark.scenario("PUB-005")
@pytest.mark.scenario("PUB-012")
def test_concurrent_process_retry_and_restart_return_one_exact_dual_publication(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="process-replay",
        markdown="# Process replay\n\nOne durable dual-target result.\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="process-replay-publish",
        epoch=epoch,
        target_mode="both",
    )
    base_payload: dict[str, object] = {
        "actor_scope_epoch": command.actor_scope_epoch,
        "actor_user_id": command.actor_user_id,
        "operation_id": command.operation_id,
        "preview_id": command.preview_id,
        "request_meta": {"correlation_id": "process-replay"},
        "source_snapshot_sha256": command.source_snapshot_sha256,
        "target_mode": command.target_mode,
    }
    concurrent = _run_publication_processes(
        isolated_knowledge_layout,
        label="publication-concurrent",
        payloads=tuple(
            {
                **base_payload,
                "request_id": _uuid(f"process-concurrent-request-{index}"),
            }
            for index in range(4)
        ),
    )
    assert {result["kind"] for result in concurrent} == {"ok"}, concurrent
    stored_results = [result["result"] for result in concurrent]
    assert all(isinstance(result, dict) for result in stored_results)
    assert all(result == stored_results[0] for result in stored_results[1:])

    restarted = _run_publication_processes(
        isolated_knowledge_layout,
        label="publication-restarted",
        payloads=(
            {
                **base_payload,
                "request_id": _uuid("process-restarted-request"),
            },
        ),
    )
    assert restarted == ({"kind": "ok", "result": stored_results[0]},)
    assert _counts(factory) == {
        "publication_operation": 1,
        "publication_target": 2,
        "lineage_group": 1,
        "lineage_member": 2,
        "knowledge_item": 2,
        "knowledge_revision": 2,
        "item_source": 2,
    }
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM operation_dedup WHERE operation_type='publication'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM publication_preview WHERE id=?", (preview.preview_id,)
        ).fetchone()[0] == "consumed"
    finally:
        connection.close()
    _assert_postconditions(factory)


@pytest.mark.concurrency
@pytest.mark.scenario("PUB-005")
@pytest.mark.scenario("PUB-012")
def test_process_response_loss_after_commit_replays_the_exact_durable_result(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    preview = _preview(
        factory,
        label="response-loss",
        markdown="# Response loss\n\nCommitted before transport loss.\n",
        epoch=epoch,
        part=True,
    )
    command = _publish_command(
        preview,
        label="response-loss-publish",
        epoch=epoch,
        target_mode="both",
    )
    payload: dict[str, object] = {
        "actor_scope_epoch": command.actor_scope_epoch,
        "actor_user_id": command.actor_user_id,
        "operation_id": command.operation_id,
        "preview_id": command.preview_id,
        "request_id": command.request_id,
        "request_meta": {"correlation_id": "response-loss"},
        "source_snapshot_sha256": command.source_snapshot_sha256,
        "target_mode": command.target_mode,
    }
    _run_publication_response_loss(
        isolated_knowledge_layout,
        label="publication-response-loss",
        payload=payload,
    )

    replay = _run_publication_processes(
        isolated_knowledge_layout,
        label="publication-response-loss-retry",
        payloads=(
            {
                **payload,
                "request_id": _uuid("response-loss-retry-request"),
            },
        ),
    )
    assert replay[0]["kind"] == "ok"
    result = replay[0]["result"]
    assert result["operation_id"] == command.operation_id
    assert result["preview_id"] == preview.preview_id
    assert result["target_mode"] == "both"
    assert [target["outcome"] for target in result["targets"]] == [
        "created",
        "created",
    ]
    assert _counts(factory) == {
        "publication_operation": 1,
        "publication_target": 2,
        "lineage_group": 1,
        "lineage_member": 2,
        "knowledge_item": 2,
        "knowledge_revision": 2,
        "item_source": 2,
    }
    _assert_postconditions(factory)


@pytest.mark.concurrency
@pytest.mark.scenario("PUB-003")
@pytest.mark.scenario("PUB-004")
@pytest.mark.scenario("PUB-009")
def test_distinct_processes_publish_equal_content_as_created_then_provenance_only(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_actor(factory)
    _, _, epoch = _assign_part(factory)
    previews = tuple(
        _preview(
            factory,
            label=f"distinct-process-{index}",
            markdown="# Equal concurrent content\n\nTwo sources, two spaces.\n",
            epoch=epoch,
            part=True,
        )
        for index in range(2)
    )
    commands = tuple(
        _publish_command(
            preview,
            label=f"distinct-process-publish-{index}",
            epoch=epoch,
            target_mode="both",
        )
        for index, preview in enumerate(previews)
    )
    results = _run_publication_processes(
        isolated_knowledge_layout,
        label="publication-distinct-content-race",
        payloads=tuple(
            {
                "actor_scope_epoch": command.actor_scope_epoch,
                "actor_user_id": command.actor_user_id,
                "operation_id": command.operation_id,
                "preview_id": command.preview_id,
                "request_id": command.request_id,
                "request_meta": {"correlation_id": f"distinct-{index}"},
                "source_snapshot_sha256": command.source_snapshot_sha256,
                "target_mode": command.target_mode,
            }
            for index, command in enumerate(commands)
        ),
    )
    assert {result["kind"] for result in results} == {"ok"}, results
    outcome_vectors = {
        tuple(target["outcome"] for target in result["result"]["targets"])
        for result in results
    }
    assert outcome_vectors == {
        ("created", "created"),
        ("provenance_added", "provenance_added"),
    }
    assert _counts(factory) == {
        "publication_operation": 2,
        "publication_target": 4,
        "lineage_group": 2,
        "lineage_member": 2,
        "knowledge_item": 2,
        "knowledge_revision": 2,
        "item_source": 4,
    }
    _assert_postconditions(factory)
