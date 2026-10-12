from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.services.spaces import (
    ProvisionPhase1SpacesCommand,
    SpaceProvisioningService,
)


pytestmark = [pytest.mark.integration, pytest.mark.concurrency]
_NAMESPACE = UUID("7403a138-209a-49b7-a77d-a12215040d50")
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p1.7-scope-multiprocess-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, role: str = "user") -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p1.7-scope:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _assign_part(
    connection,
    repository: OrganizationRepository,
    *,
    user_id: str,
    part_id: str,
    label: str,
    now_ms: int,
) -> None:
    repository.open_membership(
        connection,
        membership_id=_uuid(f"membership:{label}"),
        user_id=user_id,
        part_id=part_id,
        assigned_by="admin",
        reason="P1.7 isolated scope fixture",
        valid_from_ms=now_ms,
        expected_current_membership_id=None,
    )
    updated = repository.compare_and_swap_user_part(
        connection,
        user_id=user_id,
        expected_current_part_id=None,
        target_part_id=part_id,
        expected_scope_epoch=1,
        expected_row_version=1,
        updated_at_ms=now_ms + 1,
    )
    assert (updated.current_part_id, updated.scope_epoch, updated.row_version) == (
        part_id,
        2,
        2,
    )


def _seed_scope_fixture(factory: ConnectionFactory) -> dict[str, str]:
    users = UserRepository()
    organization = OrganizationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy_many(
                connection,
                (
                    _user("admin", role="admin"),
                    _user("member"),
                    _user("other"),
                    _user("missingpart"),
                ),
            )
            team_a = organization.create_unit(
                connection,
                unit_id=_uuid("team:a"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team A",
                actor_user_id="admin",
                now_ms=1000,
            )
            part_a = organization.create_unit(
                connection,
                unit_id=_uuid("part:a"),
                kind="part",
                parent_id=team_a.id,
                name="Part A",
                actor_user_id="admin",
                now_ms=1001,
            )
            team_b = organization.create_unit(
                connection,
                unit_id=_uuid("team:b"),
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Team B",
                actor_user_id="admin",
                now_ms=1002,
            )
            part_b = organization.create_unit(
                connection,
                unit_id=_uuid("part:b"),
                kind="part",
                parent_id=team_b.id,
                name="Part B",
                actor_user_id="admin",
                now_ms=1003,
            )
            _assign_part(
                connection,
                organization,
                user_id="member",
                part_id=part_a.id,
                label="member:a",
                now_ms=1100,
            )
    finally:
        connection.close()

    SpaceProvisioningService(factory).provision_phase1_spaces(
        ProvisionPhase1SpacesCommand(
            actor_user_id="admin",
            actor_scope_epoch=1,
            operation_id=_uuid("operation:provision"),
            request_id=_uuid("request:provision"),
            reason="P1.7 isolated scope fixture provisioning",
        )
    )

    connection = factory.open()
    try:
        with transaction(connection):
            # This active part is intentionally created after reconciliation.
            # Scope reads must never provision its missing space lazily.
            part_c = organization.create_unit(
                connection,
                unit_id=_uuid("part:c"),
                kind="part",
                parent_id=team_b.id,
                name="Part C",
                actor_user_id="admin",
                now_ms=2000,
            )
            _assign_part(
                connection,
                organization,
                user_id="missingpart",
                part_id=part_c.id,
                label="missingpart:c",
                now_ms=2100,
            )

            # This active account is intentionally imported after reconciliation.
            # Its absent personal space is a lower-bound integrity error, never a
            # read-time insert or a partial selectable-space response.
            users.import_legacy(connection, _user("missingpersonal"))

            # The v9 migration already provisions the Responsibility root space.
            # Existing schema-supported higher spaces must remain undisclosed and
            # unwritable even when a directly constructed test rollout snapshot
            # contains higher_spaces=True.
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'team',NULL,?,'active',0,0,?,?,1)""",
                (_uuid("space:team:a"), team_a.id, 2201, 2201),
            )

        read_connection = factory.open(read_only=True)
        try:
            rows = read_connection.execute(
                """SELECT space_kind,ifnull(owner_user_id,owner_org_unit_id),id
                   FROM knowledge_space ORDER BY space_kind,id"""
            ).fetchall()
            spaces = {(row[0], row[1]): row[2] for row in rows}
            assert ("part", part_c.id) not in spaces
            assert ("personal", "missingpersonal") not in spaces
            return {
                "admin_personal_space_id": spaces[("personal", "admin")],
                "member_personal_space_id": spaces[("personal", "member")],
                "other_personal_space_id": spaces[("personal", "other")],
                "missingpart_personal_space_id": spaces[
                    ("personal", "missingpart")
                ],
                "part_a_id": part_a.id,
                "part_a_space_id": spaces[("part", part_a.id)],
                "part_b_id": part_b.id,
                "part_b_space_id": spaces[("part", part_b.id)],
                "part_c_id": part_c.id,
                "responsibility_space_id": spaces[
                    ("responsibility", RESPONSIBILITY_ROOT_ID)
                ],
                "team_space_id": spaces[("team", team_a.id)],
            }
        finally:
            read_connection.close()
    finally:
        connection.close()


_MOVER = r"""
import json
from pathlib import Path
import sys
import time

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.services.organization import (
    OrganizationService,
    SetMembershipCommand,
)

database, ready_raw, start_raw, committed_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
ready = Path(ready_raw)
start = Path(start_raw)
committed = Path(committed_raw)
ready.touch()
deadline = time.monotonic() + 20.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("start barrier timed out")
    time.sleep(0.01)

result = OrganizationService(
    factory,
    preview_hmac_key=b"p1.7-multiprocess-preview-key-32-bytes!!",
).set_membership(
    SetMembershipCommand(
        actor_user_id="admin",
        actor_scope_epoch=1,
        operation_id=payload["operation_id"],
        request_id=payload["request_id"],
        target_user_id="member",
        expected_user_row_version=2,
        part_id=payload["part_b_id"],
        expected_current_leader_assignment_id=None,
        replacement_leader_user_id=None,
        reason="P1.7 two-process membership move",
    )
)
committed.touch()
print(json.dumps({"kind": "moved", "result": result.as_dict()}, sort_keys=True))
"""


_RESOLVER = r"""
import json
from pathlib import Path
import sys
import time

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    TransactionMode,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.scope import (
    Capability,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    SessionPrincipal,
)

database, ready_raw, start_raw, committed_raw, payload_raw = sys.argv[1:]
payload = json.loads(payload_raw)
factory = ConnectionFactory._for_isolated_tests(
    Path(database), CANONICAL_MIGRATION_CATALOG
)
ready = Path(ready_raw)
start = Path(start_raw)
committed = Path(committed_raw)
ready.touch()
deadline = time.monotonic() + 20.0
while not start.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("start barrier timed out")
    time.sleep(0.01)
while not committed.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("membership-commit barrier timed out")
    time.sleep(0.01)

# Direct construction is a unit/integration policy snapshot, not a host parser
# bypass.  The approved higher_spaces read flag exposes only the actor's actual
# ancestor views; both write flags still require each concrete space's canonical
# write_enabled bit and exact current role authority.
resolver = ScopeResolver(
    FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
        higher_spaces=True,
    )
)
connection = factory.open(read_only=True)
try:
    with transaction(connection, TransactionMode.DEFERRED):
        before = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM knowledge_space),"
                "(SELECT count(*) FROM audit_event),"
                "(SELECT count(*) FROM outbox_event)"
            ).fetchone()
        )

        try:
            resolver.resolve_in_transaction(
                connection,
                SessionPrincipal("member", 2),
                payload["part_a_space_id"],
                Capability.READ,
            )
        except ScopeStaleError as exc:
            assert exc.code == "SCOPE_STALE"
        else:
            raise AssertionError("old membership epoch unexpectedly resolved")

        current = SessionPrincipal("member", 3)
        listed = resolver.list_selectable_in_transaction(connection, current)
        assert listed.actor_user_id == "member"
        assert listed.scope_epoch == 3
        assert [item.id for item in listed.items] == [
            payload["member_personal_space_id"],
            payload["part_b_space_id"],
            payload["responsibility_space_id"],
        ]
        assert [item.kind for item in listed.items] == [
            "personal",
            "part",
            "responsibility",
        ]
        assert listed.items[1].org_path[-1] == "Part B"
        assert [tuple(map(str, item.capabilities)) for item in listed.items] == [
            ("read",),
            ("read",),
            ("read",),
        ]

        part_scope = resolver.resolve_in_transaction(
            connection,
            current,
            payload["part_b_space_id"],
            Capability.READ,
        )
        assert part_scope.current_part_id == payload["part_b_id"]
        assert part_scope.scope_epoch == 3
        assert not part_scope.is_part_leader
        assert tuple(map(str, part_scope.effective_capabilities)) == ("read",)
        entitled_values = tuple(map(str, part_scope.entitled_capabilities))
        assert entitled_values == ("read",), entitled_values

        for space_id in (
            payload["part_a_space_id"],
            payload["other_personal_space_id"],
            payload["team_space_id"],
            payload["missing_space_id"],
        ):
            try:
                resolver.resolve_in_transaction(
                    connection, current, space_id, Capability.READ
                )
            except ScopeNotFoundError as exc:
                assert exc.code == "NOT_FOUND"
            else:
                raise AssertionError(f"forged or unavailable scope resolved: {space_id}")

        # A global administrator has no arbitrary normal-Agent scope override.
        try:
            resolver.resolve_in_transaction(
                connection,
                SessionPrincipal("admin", 1),
                payload["part_b_space_id"],
                Capability.READ,
            )
        except ScopeNotFoundError:
            pass
        else:
            raise AssertionError("administrator obtained arbitrary Agent scope")

        # Both rollout write flags are true, so only the canonical per-space bit
        # is blocking mutation.  P1.6 initialized every tested phase-1 row with
        # write_enabled=0 and P1.7 must not infer or enable it.
        for space_id in (
            payload["member_personal_space_id"],
            payload["part_b_space_id"],
        ):
            try:
                resolver.resolve_in_transaction(
                    connection, current, space_id, Capability.EDIT
                )
            except ScopeForbiddenError as exc:
                assert exc.code == "FORBIDDEN"
            else:
                raise AssertionError("write-disabled space granted edit")

        missing_part = resolver.list_selectable_in_transaction(
            connection, SessionPrincipal("missingpart", 2)
        )
        assert [item.id for item in missing_part.items] == [
            payload["missingpart_personal_space_id"],
            payload["responsibility_space_id"],
        ]
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE owner_org_unit_id=?",
            (payload["part_c_id"],),
        ).fetchone()[0] == 0

        try:
            resolver.list_selectable_in_transaction(
                connection, SessionPrincipal("missingpersonal", 1)
            )
        except ScopeIntegrityError as exc:
            assert exc.code == "INTERNAL_ERROR"
        else:
            raise AssertionError("missing personal lower bound returned partial list")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE owner_user_id='missingpersonal'"
        ).fetchone()[0] == 0

        after = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM knowledge_space),"
                "(SELECT count(*) FROM audit_event),"
                "(SELECT count(*) FROM outbox_event)"
            ).fetchone()
        )
        assert after == before
finally:
    connection.close()

print(json.dumps({
    "before": before,
    "kind": "verified",
    "member_epoch": 3,
    "selectable_space_ids": [item.id for item in listed.items],
}, sort_keys=True))
"""


def _run_two_processes(
    layout,
    payload: dict[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    start = layout.root / "scope-start"
    committed = layout.root / "membership-committed"
    ready = (layout.root / "mover-ready", layout.root / "resolver-ready")
    environment = os.environ.copy()
    encoded = json.dumps(payload, sort_keys=True)
    processes = (
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _MOVER,
                str(layout.core_db_path),
                str(ready[0]),
                str(start),
                str(committed),
                encoded,
            ],
            cwd=_PACKAGE_ROOT.parent,
            env=environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ),
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _RESOLVER,
                str(layout.core_db_path),
                str(ready[1]),
                str(start),
                str(committed),
                encoded,
            ],
            cwd=_PACKAGE_ROOT.parent,
            env=environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ),
    )
    try:
        deadline = time.monotonic() + 20.0
        while not all(path.exists() for path in ready):
            for process in processes:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    raise AssertionError(
                        f"scope worker exited before barrier: {process.returncode}; "
                        f"stdout={stdout!r}; stderr={stderr!r}"
                    )
            if time.monotonic() >= deadline:
                raise AssertionError("scope workers did not reach the start barrier")
            time.sleep(0.01)
        start.touch()

        results: list[dict[str, object]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=40)
            assert process.returncode == 0, (stdout, stderr)
            assert stderr == ""
            lines = [line for line in stdout.splitlines() if line.strip()]
            assert lines
            results.append(json.loads(lines[-1]))
        return results[0], results[1]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


@pytest.mark.scenario("ORG-001")
@pytest.mark.scenario("ORG-005")
@pytest.mark.scenario("ORG-006")
@pytest.mark.scenario("SPACE-001")
@pytest.mark.scenario("SPACE-002")
@pytest.mark.scenario("SPACE-003")
@pytest.mark.scenario("SPACE-004")
@pytest.mark.scenario("SPACE-005")
@pytest.mark.scenario("HSPACE-007")
@pytest.mark.scenario("HSPACE-009")
def test_two_process_scope_resolution_observes_move_and_never_widens_or_lazy_creates(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    payload = _seed_scope_fixture(factory)
    payload.update(
        {
            "missing_space_id": _uuid("space:missing-or-forged"),
            "operation_id": _uuid("operation:membership-move"),
            "request_id": _uuid("request:membership-move"),
        }
    )

    mover, verifier = _run_two_processes(isolated_knowledge_layout, payload)
    assert mover["kind"] == "moved"
    assert mover["result"]["status"] == "moved"
    assert mover["result"]["row_version"] == 3
    assert verifier["kind"] == "verified"
    assert verifier["member_epoch"] == 3
    assert verifier["selectable_space_ids"] == [
        payload["member_personal_space_id"],
        payload["part_b_space_id"],
        payload["responsibility_space_id"],
    ]

    connection = factory.open(read_only=True)
    try:
        member = OrganizationRepository().get_user_organization_state(
            connection, "member"
        )
        assert member is not None
        assert (member.current_part_id, member.membership_part_id) == (
            payload["part_b_id"],
            payload["part_b_id"],
        )
        assert (member.scope_epoch, member.row_version) == (3, 3)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE owner_org_unit_id=?",
            (payload["part_c_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE owner_user_id='missingpersonal'"
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM knowledge_space
               WHERE space_kind IN ('personal','part') AND write_enabled<>0"""
        ).fetchone()[0] == 0
    finally:
        connection.close()
