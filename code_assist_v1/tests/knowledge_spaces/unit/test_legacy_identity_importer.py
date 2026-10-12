from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.legacy.importer import (
    LegacyIdentityBatchLimitError,
    LegacyIdentityImporter,
    LegacyIdentityPreparationError,
    LegacyIdentityStateConflictError,
    prepare_legacy_identity_batch,
)
from code_assist_v1.knowledge_core.legacy.plan_contract import canonical_json_bytes
from code_assist_v1.knowledge_core.legacy.plan_contract import reviewed_plan_sha256
from code_assist_v1.knowledge_core.legacy.planner import (
    build_plan_draft,
    confirm_migration_plan,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SCHEMA_V5_MIGRATION,
    SCHEMA_V6_MIGRATION,
    SCHEMA_V7_MIGRATION,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.audit import (
    AuditConflictError,
    AuditEventDraft,
    AuditRepository,
)
from code_assist_v1.knowledge_core.repositories.migration import (
    LegacyMigrationIntegrityError,
    VerifiedBlobEvidence,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
)
from code_assist_v1.knowledge_core.repositories.outbox import (
    OutboxConflictError,
    OutboxEventDraft,
    OutboxRepository,
)
from code_assist_v1.knowledge_core.repositories.spaces import (
    SpaceIdentityConflictError,
    SpaceRepository,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserImportConflictError,
    UserRepository,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_plan_contract import (
    _valid_draft,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_legacy_migration_planner import (
    _clock,
    _coverage,
    _csv_bytes,
    _mapping_row,
    _mkdirs,
    _probe,
)
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    canonical_json_bytes as canonical_inventory_json_bytes,
)


pytestmark = pytest.mark.unit

_V7_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
    )
)

_USERS_BYTES = (
    b'{"users":{"admin":{"disabled":false,"password_hash":'
    b'"SANITIZED_TEST_HASH","role":"admin"}}}'
)


def _factory(layout, catalog=CANONICAL_MIGRATION_CATALOG) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, catalog
    )
    SingleMigrator(
        factory,
        app_version="p2.3-legacy-identity-importer-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _reviewed_plan() -> dict[str, object]:
    draft = _valid_draft(assigned=True)
    raw = canonical_json_bytes(draft) + b"\n"
    return confirm_migration_plan(
        raw,
        expected_draft_sha256=hashlib.sha256(raw).hexdigest(),
        review_attestation_sha256="9" * 64,
        reviewed_at_utc="2026-08-13T02:00:00.000000Z",
        reviewer_id="p2.3-reviewer",
    )


def _prepared():
    plan_bytes, users_bytes, expected_plan_sha256, evidence = _artifacts()
    return prepare_legacy_identity_batch(
        plan_bytes,
        users_bytes,
        expected_plan_sha256=expected_plan_sha256,
        verified_blob_evidence=evidence,
    )


def _artifacts():
    plan = _reviewed_plan()
    plan_bytes = canonical_json_bytes(plan) + b"\n"
    return (
        plan_bytes,
        _USERS_BYTES,
        str(plan["plan_sha256"]),
        VerifiedBlobEvidence(
            raw_sha256=hashlib.sha256(_USERS_BYTES).hexdigest(),
            size_bytes=len(_USERS_BYTES),
            media_type="application/json",
        ),
    )


def _apply(importer: LegacyIdentityImporter):
    plan_bytes, users_bytes, expected_plan_sha256, evidence = _artifacts()
    return importer.apply(
        plan_bytes,
        users_bytes,
        expected_plan_sha256=expected_plan_sha256,
        verified_blob_evidence=evidence,
    )


def _reviewed_artifacts_for_users(tmp_path, users, mapping_rows):
    roots = _mkdirs(tmp_path)
    users_bytes = json.dumps(
        {"users": users}, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    (roots["runtime"] / "users.json").write_bytes(users_bytes)
    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "p2.3-identity-importer-fixture",
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256="a" * 64,
        scratch_root=roots["scratch"],
        allow_partial=True,
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=_probe,
    )
    assert exit_code == 0, report["errors"]
    inventory_bytes = canonical_inventory_json_bytes(report) + b"\n"
    mapping_bytes = _csv_bytes(mapping_rows)
    draft = build_plan_draft(
        inventory_bytes,
        mapping_bytes,
        expected_inventory_manifest_sha256=report["manifest_sha256"],
        expected_snapshot_attestation_sha256=report["source_snapshot"][
            "attestation_sha256"
        ],
        expected_root_binding_sha256=report["source_snapshot"][
            "root_binding_sha256"
        ],
        expected_organization_mapping_document_sha256=hashlib.sha256(
            mapping_bytes
        ).hexdigest(),
        allow_data_quality_errors=True,
    )
    draft_bytes = canonical_json_bytes(draft) + b"\n"
    plan = confirm_migration_plan(
        draft_bytes,
        expected_draft_sha256=hashlib.sha256(draft_bytes).hexdigest(),
        review_attestation_sha256="9" * 64,
        reviewed_at_utc="2026-08-13T02:00:00.000000Z",
        reviewer_id="p2.3-reviewer",
    )
    plan_bytes = canonical_json_bytes(plan) + b"\n"
    evidence = VerifiedBlobEvidence(
        raw_sha256=hashlib.sha256(users_bytes).hexdigest(),
        size_bytes=len(users_bytes),
        media_type="application/json",
    )
    return plan_bytes, users_bytes, str(plan["plan_sha256"]), evidence, report


def _counts(connection) -> dict[str, int]:
    names = (
        "app_user",
        "org_membership",
        "org_role_assignment",
        "knowledge_space",
        "legacy_migration_map",
        "audit_event",
        "outbox_event",
    )
    return {
        name: int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
        for name in names
    }


def _seed_org_units(connection, batch, *, display_prefix="Canonical") -> None:
    for index, unit in enumerate(batch.organization_units, 1):
        name = f"{display_prefix} {index}"
        connection.execute(
            """INSERT INTO org_unit(
                 id,kind,parent_id,name,name_key,status,created_at,updated_at,
                 created_by,updated_by,row_version
               ) VALUES (?,?,?,?,?,'active',?,?,NULL,NULL,1)""",
            (
                unit.id,
                unit.kind,
                unit.parent_id,
                name,
                name.casefold(),
                batch.effective_at_ms,
                batch.effective_at_ms,
            ),
        )


class _NoopEventRepository:
    def append(self, connection, draft):
        return None


def test_identity_registry_apply_is_atomic_exact_and_replay_safe_after_account_change(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    importer = LegacyIdentityImporter(factory)
    connection = factory.open()
    try:
        monkeypatch.setattr(
            builtins,
            "open",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("database apply performed file I/O")
            ),
        )
        first = _apply(importer)
        assert first.source_container_inserted is True
        assert first.inserted_mapping_count == 2
        assert first.created_organization_unit_count == 3
        assert first.outcomes[0].account_inserted is True
        assert first.outcomes[0].membership_created is True
        assert first.outcomes[0].leader_assignment_created is True
        assert first.outcomes[0].personal_space_created is True

        user = UserRepository().require(connection, "admin")
        assert user.password_hash == "SANITIZED_TEST_HASH"
        assert user.system_role == "admin"
        assert user.current_part_id == batch.users[0].assigned_part_id
        assert connection.execute(
            "SELECT count(*) FROM knowledge_space WHERE space_kind='part'"
        ).fetchone()[0] == 0
        assert _counts(connection) == {
            "app_user": 1,
            "org_membership": 1,
            "org_role_assignment": 1,
            "knowledge_space": 2,
            "legacy_migration_map": 2,
            "audit_event": 2,
            "outbox_event": 2,
        }

        with transaction(connection):
            current = UserRepository().require(connection, "admin")
            UserRepository().update_password_hash(
                connection,
                user_id="admin",
                expected_row_version=current.row_version,
                password_hash="scrypt:changed-after-import",
                password_change_kind="self_change",
                updated_at_ms=batch.effective_at_ms + 1,
            )
        before_replay = _counts(connection)
        replay = _apply(importer)
        assert replay.source_container_inserted is False
        assert replay.inserted_mapping_count == 0
        assert _counts(connection) == before_replay
        assert (
            UserRepository().require(connection, "admin").password_hash
            == "scrypt:changed-after-import"
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "failed_stage",
    (
        "legacy_identity.after_organization",
        "legacy_identity.after_accounts",
        "legacy_identity.after_assignments",
        "legacy_identity.after_spaces",
        "legacy_identity.after_user_mapping",
        "legacy_identity.after_user_events",
        "legacy_identity.after_container_mapping",
        "legacy_identity.before_return",
    ),
)
def test_identity_registry_caught_failure_cannot_commit_partial_effects(
    isolated_knowledge_layout,
    failed_stage: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()

    def failpoint(name: str) -> None:
        if name == failed_stage:
            raise RuntimeError("injected identity failure")

    connection = factory.open()
    try:
        with pytest.raises(RuntimeError, match="injected identity failure"):
            _apply(LegacyIdentityImporter(factory, failpoint=failpoint))

        # The caller catches the service error and commits an unrelated write.
        # That independent policy choice cannot commit any partial identity work.
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="unrelated",
                    display_name="Unrelated",
                    password_hash="scrypt:unrelated",
                    system_role="user",
                    account_status="active",
                    created_at_ms=1,
                    updated_at_ms=1,
                ),
            )
        assert UserRepository().get(connection, "admin") is None
        assert UserRepository().get(connection, "unrelated") is not None
        assert _counts(connection) == {
            "app_user": 1,
            "org_membership": 0,
            "org_role_assignment": 0,
            "knowledge_space": 1,
            "legacy_migration_map": 0,
            "audit_event": 0,
            "outbox_event": 0,
        }
        # Only the schema seed remains; reviewed organization writes rolled back.
        assert connection.execute("SELECT count(*) FROM org_unit").fetchone()[0] == 1
    finally:
        connection.close()


def test_identity_registry_preparation_rejects_duplicates_drift_and_normalized_evidence() -> None:
    plan = _reviewed_plan()
    digest = hashlib.sha256(_USERS_BYTES).hexdigest()
    evidence = VerifiedBlobEvidence(
        raw_sha256=digest,
        size_bytes=len(_USERS_BYTES),
        media_type="application/json",
    )
    duplicate = b'{"users":{"admin":{},"admin":{}}}'
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            plan,
            duplicate,
            expected_plan_sha256=str(plan["plan_sha256"]),
            verified_blob_evidence=evidence,
        )
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            plan,
            _USERS_BYTES,
            expected_plan_sha256=str(plan["plan_sha256"]),
            verified_blob_evidence=replace(
                evidence, normalized_content_sha256="a" * 64
            ),
        )
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            plan,
            _USERS_BYTES + b"\n",
            expected_plan_sha256=str(plan["plan_sha256"]),
            verified_blob_evidence=evidence,
        )


@pytest.mark.parametrize(
    "drifted",
    (
        b'{"users":{"admin":{"disabled":false,"password_hash":"CHANGED","role":"admin"}}}',
        b'{"users":{"aaa":{"disabled":false,"password_hash":"X","role":"user"},'
        b'"admin":{"disabled":false,"password_hash":"SANITIZED_TEST_HASH","role":"admin"}}}',
        b'{"users":{"admin":{"disabled":false,"password_hash":"SANITIZED_TEST_HASH",'
        b'"role":"user"}}}',
    ),
    ids=("password_payload", "ordinal", "role_payload"),
)
def test_registry_payload_and_ordinal_drift_fail_even_with_fresh_blob_evidence(
    drifted: bytes,
) -> None:
    plan = _reviewed_plan()
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            canonical_json_bytes(plan) + b"\n",
            drifted,
            expected_plan_sha256=str(plan["plan_sha256"]),
            verified_blob_evidence=VerifiedBlobEvidence(
                raw_sha256=hashlib.sha256(drifted).hexdigest(),
                size_bytes=len(drifted),
                media_type="application/json",
            ),
        )


def test_identity_batch_uses_only_plan_bound_time_and_deterministic_command_ids() -> None:
    first = _prepared()
    second = _prepared()
    assert first == second
    assert first.effective_at_ms == 1_786_582_923_000
    assert first.request_id == second.request_id
    assert first.operation_id == second.operation_id
    assert first.request_id != first.operation_id


def test_identity_plan_requires_external_hash_and_exact_canonical_bytes() -> None:
    plan = _reviewed_plan()
    expected = str(plan["plan_sha256"])
    evidence = VerifiedBlobEvidence(
        raw_sha256=hashlib.sha256(_USERS_BYTES).hexdigest(),
        size_bytes=len(_USERS_BYTES),
        media_type="application/json",
    )
    with pytest.raises(TypeError):
        prepare_legacy_identity_batch(  # type: ignore[call-arg]
            plan, _USERS_BYTES, verified_blob_evidence=evidence
        )
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            plan,
            _USERS_BYTES,
            expected_plan_sha256="0" * 64,
            verified_blob_evidence=evidence,
        )
    noncanonical = json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8")
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            noncanonical,
            _USERS_BYTES,
            expected_plan_sha256=expected,
            verified_blob_evidence=evidence,
        )
    canonical = canonical_json_bytes(plan) + b"\n"
    prepared = prepare_legacy_identity_batch(
        canonical,
        _USERS_BYTES,
        expected_plan_sha256=expected,
        verified_blob_evidence=evidence,
    )
    assert prepared.plan_sha256 == expected

    alternate = deepcopy(plan)
    alternate["review"]["reviewer_id"] = "alternate-reviewer"
    alternate["plan_sha256"] = reviewed_plan_sha256(alternate)
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            alternate,
            _USERS_BYTES,
            expected_plan_sha256=expected,
            verified_blob_evidence=evidence,
        )


@pytest.mark.parametrize(
    "drift",
    ("owner_mapping", "source_container", "logical_record_hash"),
)
def test_rehashed_plan_internal_mapping_and_source_binding_drift_is_rejected(
    drift: str,
) -> None:
    plan = deepcopy(_reviewed_plan())
    draft = plan["draft"]
    assert isinstance(draft, dict)
    if drift == "owner_mapping":
        draft["owner_mappings"][0]["user_id"] = "attacker"
    else:
        row = next(
            value
            for value in draft["records"]
            if value["category"] == "identity.users_registry"
            and value["source_record"]["record_kind"]
            == ("physical_file" if drift == "source_container" else "logical_record")
        )
        if drift == "source_container":
            row["source_record"]["sha256"] = "0" * 64
        else:
            row["inventory_record_sha256"] = "0" * 64
    plan["plan_sha256"] = reviewed_plan_sha256(plan)
    with pytest.raises(LegacyIdentityPreparationError):
        prepare_legacy_identity_batch(
            canonical_json_bytes(plan) + b"\n",
            _USERS_BYTES,
            expected_plan_sha256=str(plan["plan_sha256"]),
            verified_blob_evidence=VerifiedBlobEvidence(
                raw_sha256=hashlib.sha256(_USERS_BYTES).hexdigest(),
                size_bytes=len(_USERS_BYTES),
                media_type="application/json",
            ),
        )


def test_public_apply_cannot_accept_or_persist_a_forged_prepared_batch(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    forged = replace(
        _prepared(),
        plan_sha256="0" * 64,
        assigner_user_id="attacker",
    )
    importer = LegacyIdentityImporter(factory)
    with pytest.raises(TypeError, match="reviewed_plan_bytes must be bytes"):
        importer.apply(  # type: ignore[arg-type]
            forged,
            _USERS_BYTES,
            expected_plan_sha256="0" * 64,
            verified_blob_evidence=forged.verified_blob_evidence,
        )
    connection = factory.open()
    try:
        assert _counts(connection) == {
            "app_user": 0,
            "org_membership": 0,
            "org_role_assignment": 0,
            "knowledge_space": 1,
            "legacy_migration_map": 0,
            "audit_event": 0,
            "outbox_event": 0,
        }
    finally:
        connection.close()


def test_reviewed_role_less_admin_and_status_spellings_import_exactly(
    isolated_knowledge_layout,
    tmp_path,
) -> None:
    artifacts = _reviewed_artifacts_for_users(
        tmp_path,
        {
            "admin": {"password_hash": "HASH-ADMIN"},
            "disabled-account-status": {
                "account_status": "disabled",
                "password_hash": "HASH-STATUS",
                "role": "user",
            },
            "disabled-active-flag": {
                "active": False,
                "password_hash": "HASH-ACTIVE",
                "role": "user",
            },
            "active-unassigned": {
                "password_hash": "HASH-UNASSIGNED",
                "role": "user",
            },
        },
        [
            _mapping_row(
                claim="admin",
                user_id="admin",
                role="admin",
                assigned=True,
                leader=True,
            ),
            _mapping_row(
                claim="disabled-account-status",
                user_id="disabled-account-status",
                role="user",
                status="disabled",
            ),
            _mapping_row(
                claim="disabled-active-flag",
                user_id="disabled-active-flag",
                role="user",
                status="disabled",
            ),
            _mapping_row(
                claim="active-unassigned",
                user_id="active-unassigned",
                role="user",
            ),
        ],
    )
    plan_bytes, users_bytes, expected, evidence, report = artifacts
    admin_record = next(
        row
        for row in report["records"]
        if row.get("record_kind") == "logical_record"
        and row.get("owner", {}).get("user_id") == "admin"
    )
    assert admin_record["parse"]["registry_account"]["role"] == "unassigned_legacy"
    assert admin_record["reason_codes"] == ["legacy_role_requires_plan"]

    factory = _factory(isolated_knowledge_layout)
    result = LegacyIdentityImporter(factory).apply(
        plan_bytes,
        users_bytes,
        expected_plan_sha256=expected,
        verified_blob_evidence=evidence,
    )
    assert result.imported_user_count == 4
    connection = factory.open()
    try:
        assert UserRepository().require(connection, "admin").system_role == "admin"
        assert (
            UserRepository().require(connection, "disabled-account-status").account_status
            == "disabled"
        )
        assert (
            UserRepository().require(connection, "disabled-active-flag").account_status
            == "disabled"
        )
        assert (
            UserRepository().require(connection, "disabled-active-flag").current_part_id
            is None
        )
        assert UserRepository().require(connection, "active-unassigned").account_status == "active"
        assert UserRepository().require(connection, "active-unassigned").current_part_id is None
        assert connection.execute("SELECT count(*) FROM org_membership").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM org_role_assignment").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM knowledge_space").fetchone()[0] == 5
        audits = connection.execute(
            """SELECT actor_kind,actor_user_id,actor_display_name,actor_role_snapshot
               FROM audit_event ORDER BY id"""
        ).fetchall()
        assert audits
        assert {
            (
                row["actor_kind"],
                row["actor_user_id"],
                row["actor_display_name"],
                row["actor_role_snapshot"],
            )
            for row in audits
        } == {("system", None, "P2.3 legacy migration", "system")}
    finally:
        connection.close()


def test_more_than_one_hundred_users_is_rejected_before_database_open(
    isolated_knowledge_layout,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = {
        "admin": {"password_hash": "HASH-ADMIN", "role": "admin"},
        **{
            f"user{index:03d}": {
                "password_hash": f"HASH-{index:03d}",
                "role": "user",
            }
            for index in range(100)
        },
    }
    mappings = [
        _mapping_row(claim="admin", user_id="admin", role="admin"),
        *[
            _mapping_row(
                claim=f"user{index:03d}",
                user_id=f"user{index:03d}",
                role="user",
            )
            for index in range(100)
        ],
    ]
    plan_bytes, users_bytes, expected, evidence, _ = _reviewed_artifacts_for_users(
        tmp_path, users, mappings
    )
    factory = _factory(isolated_knowledge_layout)
    monkeypatch.setattr(
        ConnectionFactory,
        "open",
        lambda self: (_ for _ in ()).throw(
            AssertionError("database opened before batch limit validation")
        ),
    )
    with pytest.raises(LegacyIdentityBatchLimitError):
        LegacyIdentityImporter(factory).apply(
            plan_bytes,
            users_bytes,
            expected_plan_sha256=expected,
            verified_blob_evidence=evidence,
        )


def test_mixed_registry_closes_only_reviewed_imported_children(
    isolated_knowledge_layout,
    tmp_path,
) -> None:
    plan_bytes, users_bytes, expected, evidence, report = (
        _reviewed_artifacts_for_users(
            tmp_path,
            {
                "admin": {"password_hash": "HASH-ADMIN", "role": "admin"},
                "broken": {"role": "user"},
            },
            [_mapping_row(claim="admin", user_id="admin", role="admin")],
        )
    )
    registry_rows = [
        row
        for row in report["records"]
        if row.get("category") == "identity.users_registry"
        and row.get("record_kind") == "logical_record"
    ]
    assert len(registry_rows) == 2
    assert sum(row["status"] == "invalid" for row in registry_rows) == 1

    batch = prepare_legacy_identity_batch(
        plan_bytes,
        users_bytes,
        expected_plan_sha256=expected,
        verified_blob_evidence=evidence,
    )
    assert [user.user_id for user in batch.users] == ["admin"]
    assert batch.source_container_target.details.planned_import_child_count == 1

    factory = _factory(isolated_knowledge_layout)
    result = LegacyIdentityImporter(factory).apply(
        plan_bytes,
        users_bytes,
        expected_plan_sha256=expected,
        verified_blob_evidence=evidence,
    )
    assert result.imported_user_count == 1
    assert result.inserted_mapping_count == 2


def test_existing_authoritative_org_hierarchy_keeps_its_display_names(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_org_units(connection, batch, display_prefix="Managed name")
        result = _apply(LegacyIdentityImporter(factory))
        assert result.created_organization_unit_count == 0
        assert [
            connection.execute(
                "SELECT name FROM org_unit WHERE id=?", (unit.id,)
            ).fetchone()[0]
            for unit in batch.organization_units
        ] == ["Managed name 1", "Managed name 2", "Managed name 3"]
    finally:
        connection.close()


@pytest.mark.parametrize("conflict", ("inactive", "wrong_parent"))
def test_existing_org_authority_conflict_rolls_back_all_new_identity_effects(
    isolated_knowledge_layout,
    conflict: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    connection = factory.open()
    try:
        with transaction(connection):
            if conflict == "inactive":
                unit = batch.organization_units[0]
                connection.execute(
                    """INSERT INTO org_unit(
                         id,kind,parent_id,name,name_key,status,created_at,updated_at,
                         created_by,updated_by,row_version
                       ) VALUES (?,?,?,?,?,'inactive',?,?,NULL,NULL,1)""",
                    (
                        unit.id,
                        unit.kind,
                        unit.parent_id,
                        "Inactive authority",
                        "inactive authority",
                        batch.effective_at_ms,
                        batch.effective_at_ms,
                    ),
                )
            else:
                responsibility, team, part = batch.organization_units
                wrong_parent_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                for unit_id, kind, parent_id, name in (
                    (responsibility.id, "responsibility", None, "Reviewed responsibility"),
                    (wrong_parent_id, "responsibility", None, "Other responsibility"),
                    (team.id, "team", wrong_parent_id, "Wrongly parented team"),
                    (part.id, "part", team.id, "Reviewed part"),
                ):
                    connection.execute(
                        """INSERT INTO org_unit(
                             id,kind,parent_id,name,name_key,status,created_at,updated_at,
                             created_by,updated_by,row_version
                           ) VALUES (?,?,?,?,?,'active',?,?,NULL,NULL,1)""",
                        (
                            unit_id,
                            kind,
                            parent_id,
                            name,
                            name.casefold(),
                            batch.effective_at_ms,
                            batch.effective_at_ms,
                        ),
                    )
        before = _counts(connection)
        org_count = connection.execute("SELECT count(*) FROM org_unit").fetchone()[0]
        with pytest.raises(LegacyIdentityStateConflictError):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
        assert connection.execute("SELECT count(*) FROM org_unit").fetchone()[0] == org_count
    finally:
        connection.close()


def test_existing_conflicting_user_rolls_back_new_org_effects(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="admin",
                    display_name="Conflicting admin",
                    password_hash="OTHER-HASH",
                    system_role="user",
                    account_status="active",
                    created_at_ms=1,
                    updated_at_ms=1,
                ),
            )
        before = _counts(connection)
        with pytest.raises(UserImportConflictError):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
        assert connection.execute("SELECT count(*) FROM org_unit").fetchone()[0] == 1
    finally:
        connection.close()


def test_existing_conflicting_personal_space_rolls_back_imported_identity(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    connection = factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="other",
                    display_name="Other owner",
                    password_hash="OTHER-HASH",
                    system_role="user",
                    account_status="active",
                    created_at_ms=1,
                    updated_at_ms=1,
                ),
            )
            SpaceRepository().ensure_personal(
                connection,
                space_id=batch.users[0].target_space_id,
                user_id="other",
                created_at_ms=1,
            )
        before = _counts(connection)
        with pytest.raises(SpaceIdentityConflictError):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
        assert UserRepository().get(connection, "admin") is None
        assert connection.execute("SELECT count(*) FROM org_unit").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("conflict", ("membership", "leader"))
def test_existing_assignment_conflict_rolls_back_all_new_effects(
    isolated_knowledge_layout,
    conflict: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    prepared = batch.users[0]
    assert prepared.assigned_part_id is not None
    assert prepared.membership_id is not None
    assert prepared.leader_assignment_id is not None
    connection = factory.open()
    try:
        organization = OrganizationRepository()
        with transaction(connection):
            _seed_org_units(connection, batch)
            UserRepository().import_legacy(connection, prepared.legacy_user)
            organization.open_membership(
                connection,
                membership_id=(
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
                    if conflict == "membership"
                    else prepared.membership_id
                ),
                user_id=prepared.user_id,
                part_id=prepared.assigned_part_id,
                assigned_by=batch.assigner_user_id,
                reason="reviewed P2.3 legacy identity import",
                valid_from_ms=batch.effective_at_ms,
            )
            if conflict == "leader":
                organization.open_part_leader(
                    connection,
                    assignment_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    user_id=prepared.user_id,
                    part_id=prepared.assigned_part_id,
                    assigned_by=batch.assigner_user_id,
                    reason="reviewed P2.3 legacy identity import",
                    valid_from_ms=batch.effective_at_ms,
                )
        before = _counts(connection)
        with pytest.raises(LegacyIdentityStateConflictError):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_closed_container_replay_backfills_missing_audit_and_outbox_events(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _apply(
        LegacyIdentityImporter(
            factory,
            audit=_NoopEventRepository(),  # type: ignore[arg-type]
            outbox=_NoopEventRepository(),  # type: ignore[arg-type]
        )
    )
    connection = factory.open()
    try:
        assert _counts(connection)["legacy_migration_map"] == 2
        assert _counts(connection)["audit_event"] == 0
        assert _counts(connection)["outbox_event"] == 0
    finally:
        connection.close()

    replay = _apply(LegacyIdentityImporter(factory))
    assert replay.source_container_inserted is False
    connection = factory.open()
    try:
        assert _counts(connection)["audit_event"] == 2
        assert _counts(connection)["outbox_event"] == 2
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("event_kind", "mapping_kind"),
    (
        ("audit", "child"),
        ("outbox", "child"),
        ("audit", "container"),
        ("outbox", "container"),
    ),
)
def test_closed_container_replay_rejects_conflicting_event_identity_atomically(
    isolated_knowledge_layout,
    event_kind: str,
    mapping_kind: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    batch = _prepared()
    _apply(
        LegacyIdentityImporter(
            factory,
            audit=_NoopEventRepository(),  # type: ignore[arg-type]
            outbox=_NoopEventRepository(),  # type: ignore[arg-type]
        )
    )
    mapping_id = (
        batch.users[0].mapping_id
        if mapping_kind == "child"
        else batch.source_container_mapping_id
    )
    connection = factory.open()
    try:
        with transaction(connection):
            if event_kind == "audit":
                AuditRepository().append(
                    connection,
                    AuditEventDraft(
                        event_uuid=str(
                            uuid5(
                                UUID("f43278e2-a123-45b9-a8d9-b2b577230a9a"),
                                mapping_id,
                            )
                        ),
                        occurred_at_ms=batch.effective_at_ms,
                        request_id=batch.request_id,
                        operation_id=batch.operation_id,
                        action="tampered.legacy.event",
                        outcome="tampered",
                        actor_kind="system",
                        actor_user_id=None,
                        actor_display_name="Tampered fixture",
                        actor_role_snapshot="system",
                    ),
                )
            else:
                OutboxRepository().append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"legacy-migration:{mapping_id}:imported:v1",
                        space_id=None,
                        aggregate_type="tampered",
                        aggregate_id=mapping_id,
                        aggregate_version=1,
                        event_type="tampered.legacy.event",
                        payload={"schema_version": 1, "tampered": True},
                        available_at_ms=batch.effective_at_ms,
                    ),
                )
        before = _counts(connection)
        expected_error = AuditConflictError if event_kind == "audit" else OutboxConflictError
        with pytest.raises(expected_error):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
    finally:
        connection.close()


def test_closed_container_replay_rejects_missing_child_target_space(
    isolated_knowledge_layout,
) -> None:
    # This deliberately corrupts the historical target by deleting it.  v8
    # correctly prevents that through the personal_wiki_profile FK and delete
    # guard, so retain the legacy replay canary against the frozen v7 catalog.
    factory = _factory(isolated_knowledge_layout, _V7_CATALOG)
    batch = _prepared()
    _apply(
        LegacyIdentityImporter(
            factory,
            audit=_NoopEventRepository(),  # type: ignore[arg-type]
            outbox=_NoopEventRepository(),  # type: ignore[arg-type]
        )
    )
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "DELETE FROM knowledge_space WHERE id=?",
                (batch.users[0].target_space_id,),
            )
        before = _counts(connection)
        with pytest.raises(LegacyMigrationIntegrityError):
            _apply(LegacyIdentityImporter(factory))
        assert _counts(connection) == before
    finally:
        connection.close()
