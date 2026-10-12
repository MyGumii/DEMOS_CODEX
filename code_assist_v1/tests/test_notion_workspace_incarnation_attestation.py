from __future__ import annotations

import json
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.notion_workspace_store import (
    LegacyFileSourceLocator,
    LegacyPageIncarnationEvidence,
    LegacyPageLocatorProjection,
    LegacyPageSourceProof,
    LegacyPublicSourceLocator,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _store(tmp_path) -> tuple[WorkspaceStore, str]:
    store = WorkspaceStore(
        catalog_path_for_user_data_root(tmp_path / "user-data"),
        trusted_clock=lambda: 1_000,
    )
    workspace = store.create_workspace(
        "owner", name="Incarnation review", operation_id="create-workspace"
    )["workspace"]
    return store, str(workspace["id"])


def _proof(*, actor: str = "owner", revision: int = 1) -> LegacyPageSourceProof:
    return LegacyPageSourceProof(
        actor_subject_id=actor,
        owner_subject_id="owner",
        page_id="page-1",
        revision=revision,
        updated_at=100,
        content_sha256="sha256:" + "1" * 64,
    )


def _evidence(**changes) -> LegacyPageIncarnationEvidence:
    values = {
        "owner_subject_id": "owner",
        "page_id": "page-1",
        "incarnation_id": "inc_" + "a" * 32,
        "incarnation_generation": 1,
        "incarnation_retired": False,
        "source_epoch": 2,
        "full_sweep_generation": 3,
        "source_device": 4,
        "source_inode": 5,
        "source_link_count": 1,
        "change_generation": 6,
        "change_mask": 31,
        "source_exists": True,
        "source_active": True,
    }
    values.update(changes)
    values["receipt_sha256"] = store_module.legacy_incarnation_receipt_sha256(
        source_owner_id=values["owner_subject_id"],
        page_id=values["page_id"],
        incarnation_id=values["incarnation_id"],
        incarnation_generation=values["incarnation_generation"],
        incarnation_retired=values["incarnation_retired"],
        change_generation=values["change_generation"],
        change_mask=values["change_mask"],
        source_exists=values["source_exists"],
        source_active=values["source_active"],
        source_epoch=values["source_epoch"],
        full_sweep_generation=values["full_sweep_generation"],
        source_device=values["source_device"],
        source_inode=values["source_inode"],
        source_link_count=values["source_link_count"],
    )
    return LegacyPageIncarnationEvidence(**values)


def _projection(*, actor: str = "owner") -> LegacyPageLocatorProjection:
    return LegacyPageLocatorProjection(
        actor_subject_id=actor,
        owner_subject_id="owner",
        page_id="page-1",
        binding_version=1,
        file_sources=(LegacyFileSourceLocator("f_" + "b" * 32),),
        public_sources=(
            LegacyPublicSourceLocator("sha256:" + "c" * 64, False),
        ),
    )


def _active_binding(store: WorkspaceStore, workspace_id: str) -> dict:
    return store.bind_page_source(
        "owner",
        workspace_id,
        _proof(),
        source_revalidator=lambda _candidate: True,
        base_version=1,
        operation_id="bind-page",
    )


def _attest_active(
    store: WorkspaceStore,
    workspace_id: str,
    *,
    revalidator,
    operation_id: str = "attest-page",
    base_version: int = 2,
    evidence: LegacyPageIncarnationEvidence | None = None,
) -> dict:
    return store.attest_page_source_incarnation(
        "owner",
        workspace_id,
        _proof(),
        evidence or _evidence(),
        source_revalidator=revalidator,
        locator_projection=_projection(),
        expected_binding_version=1,
        base_version=base_version,
        operation_id=operation_id,
        source_quiesced_confirmed=True,
    )


def _stage_next_generation(
    store: WorkspaceStore,
    workspace_id: str,
    *,
    base_version: int,
    operation_id: str,
) -> dict:
    return store.stage_page_source_binding(
        "owner",
        workspace_id,
        _proof(),
        base_version=base_version,
        operation_id=operation_id,
    )


def test_active_review_rebuilds_locators_atomically_and_replays_before_source_io(
    tmp_path,
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    calls = []

    def revalidate(proof, evidence, projection):
        calls.append((proof, evidence, projection))
        return True

    first = _attest_active(store, workspace_id, revalidator=revalidate)
    replay = _attest_active(store, workspace_id, revalidator=revalidate)

    assert replay == first
    assert len(calls) == 1
    assert first["binding"] == {
        "workspace_id": workspace_id,
        "source_owner_subject_id": "owner",
        "source_page_id": "page-1",
        "state": "active",
        "version": 1,
    }
    assert first["workspace"]["version"] == 3
    assert first["projection"] == {
        "binding_version": 1,
        "projection_sha256": store_module._page_projection_sha256(
            workspace_id=workspace_id,
            source_owner_subject_id="owner",
            source_page_id="page-1",
            binding_version=1,
            file_sources=("f_" + "b" * 32,),
            public_sources=(("sha256:" + "c" * 64, False),),
        ),
        "file_source_count": 1,
        "public_source_count": 1,
        "changed": True,
    }
    encoded = json.dumps(first, sort_keys=True)
    assert "token" not in encoded
    assert "rel_path" not in encoded

    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            "SELECT source_file_id FROM workspace_file_sources"
        ).fetchall() == [("f_" + "b" * 32,)]
        assert con.execute(
            "SELECT token_sha256 FROM workspace_public_sources"
        ).fetchall() == [("sha256:" + "c" * 64,)]
        payloads = con.execute(
            """
            SELECT payload_json FROM workspace_audit_events
            WHERE operation_id='attest-page'
            UNION ALL
            SELECT payload_json FROM workspace_outbox
            WHERE operation_id='attest-page'
            """
        ).fetchall()
        assert len(payloads) == 2
        assert all(
            json.loads(row[0])
            == {
                "attestation": first["attestation"],
                "projection": first["projection"],
            }
            for row in payloads
        )
    finally:
        con.close()

    readiness = store.incarnation_attestation_readiness("owner", workspace_id)
    assert readiness["attested_active_binding_count"] == 1
    assert readiness["unattested_active_binding_count"] == 0
    assert readiness["ready_for_resolver_cutover"] is True
    assert store.verify_deep_health()["healthy"] is True

    assert store.get_page_source_incarnation_attestation_replay(
        "owner",
        workspace_id,
        "owner",
        "page-1",
        expected_binding_version=1,
        operation_id="attest-page",
    ) == first
    assert store.get_page_source_incarnation_attestation_replay(
        "owner",
        workspace_id,
        "owner",
        "page-1",
        expected_binding_version=1,
        operation_id="not-committed",
    ) is None


def test_pre_source_replay_is_admin_scoped_and_identity_fenced(tmp_path) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    _attest_active(
        store,
        workspace_id,
        revalidator=lambda _proof, _evidence, _projection: True,
    )

    with pytest.raises(FileNotFoundError):
        store.get_page_source_incarnation_attestation_replay(
            "outsider",
            workspace_id,
            "owner",
            "page-1",
            expected_binding_version=1,
            operation_id="attest-page",
        )
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        store.get_page_source_incarnation_attestation_replay(
            "owner",
            workspace_id,
            "owner",
            "different-page",
            expected_binding_version=1,
            operation_id="attest-page",
        )
    with pytest.raises(store_module.WorkspaceOperationConflict):
        store.get_page_source_incarnation_attestation_replay(
            "owner",
            workspace_id,
            "owner",
            "page-1",
            expected_binding_version=1,
            operation_id="bind-page",
        )


def test_same_count_projection_replacement_revokes_cutover_readiness(tmp_path) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    reviewed = _attest_active(
        store,
        workspace_id,
        revalidator=lambda _proof, _evidence, _projection: True,
    )
    replacement = LegacyPageLocatorProjection(
        actor_subject_id="owner",
        owner_subject_id="owner",
        page_id="page-1",
        binding_version=1,
        file_sources=(LegacyFileSourceLocator("f_" + "d" * 32),),
        public_sources=(
            LegacyPublicSourceLocator("sha256:" + "e" * 64, False),
        ),
    )
    store.replace_page_locator_projection(
        "owner",
        workspace_id,
        replacement,
        projection_revalidator=lambda _candidate: True,
        base_version=reviewed["workspace"]["version"],
        operation_id="replace-with-same-counts",
    )
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "ready_for_resolver_cutover"
    ] is False


@pytest.mark.parametrize(
    "next_evidence",
    [
        _evidence(),
        _evidence(source_epoch=3, full_sweep_generation=4, change_generation=7),
        _evidence(
            incarnation_id="inc_" + "b" * 32,
            incarnation_generation=2,
            source_epoch=3,
            full_sweep_generation=4,
            change_generation=7,
        ),
    ],
    ids=("unchanged", "archive-restore", "new-incarnation"),
)
def test_lineage_accepts_unchanged_restore_and_strict_new_incarnation(
    tmp_path, next_evidence
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    first = _attest_active(
        store,
        workspace_id,
        revalidator=lambda _proof, _evidence, _projection: True,
    )
    staged = _stage_next_generation(
        store,
        workspace_id,
        base_version=first["workspace"]["version"],
        operation_id="stage-lineage-next",
    )
    calls = 0

    def revalidate(_proof, _evidence, projection):
        nonlocal calls
        calls += 1
        assert projection is None
        return True

    result = store.attest_page_source_incarnation(
        "owner",
        workspace_id,
        _proof(),
        next_evidence,
        source_revalidator=revalidate,
        expected_binding_version=2,
        base_version=staged["workspace"]["version"],
        operation_id="attest-lineage-next",
        source_quiesced_confirmed=True,
    )
    assert calls == 1
    assert result["binding"]["state"] == "pending"
    assert result["projection"] is None
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "attestation_count"
    ] == 2


@pytest.mark.parametrize(
    ("first_evidence", "next_evidence"),
    [
        (_evidence(), _evidence(source_device=40)),
        (_evidence(), _evidence(source_inode=50)),
        (_evidence(), _evidence(source_link_count=2)),
        (_evidence(), _evidence(source_epoch=1)),
        (_evidence(), _evidence(full_sweep_generation=2)),
        (_evidence(), _evidence(change_generation=5)),
        (_evidence(change_mask=31), _evidence(change_mask=15)),
        (_evidence(), _evidence(incarnation_generation=2)),
        (
            _evidence(),
            _evidence(incarnation_id="inc_" + "b" * 32),
        ),
        (
            _evidence(incarnation_generation=2),
            _evidence(incarnation_generation=1),
        ),
        (
            _evidence(),
            _evidence(
                incarnation_id="inc_" + "b" * 32,
                incarnation_generation=2,
                source_epoch=2,
                full_sweep_generation=4,
                change_generation=7,
            ),
        ),
        (
            _evidence(),
            _evidence(
                incarnation_id="inc_" + "b" * 32,
                incarnation_generation=2,
                source_epoch=3,
                full_sweep_generation=3,
                change_generation=7,
            ),
        ),
        (
            _evidence(),
            _evidence(
                incarnation_id="inc_" + "b" * 32,
                incarnation_generation=2,
                source_epoch=3,
                full_sweep_generation=4,
                change_generation=6,
            ),
        ),
    ],
    ids=(
        "device-change",
        "inode-change",
        "link-count-change",
        "source-epoch-regression",
        "sweep-regression",
        "change-regression",
        "mask-bit-loss",
        "same-id-different-generation",
        "same-generation-different-id",
        "incarnation-generation-regression",
        "new-id-source-epoch-not-strict",
        "new-id-sweep-not-strict",
        "new-id-change-not-strict",
    ),
)
def test_lineage_rejects_rollback_before_source_io(
    tmp_path, first_evidence, next_evidence
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    first = _attest_active(
        store,
        workspace_id,
        evidence=first_evidence,
        revalidator=lambda _proof, _evidence, _projection: True,
    )
    staged = _stage_next_generation(
        store,
        workspace_id,
        base_version=first["workspace"]["version"],
        operation_id="stage-stale-lineage",
    )
    calls = 0

    def should_not_run(*_args):
        nonlocal calls
        calls += 1
        return True

    with pytest.raises(ValueError, match="lineage is stale"):
        store.attest_page_source_incarnation(
            "owner",
            workspace_id,
            _proof(),
            next_evidence,
            source_revalidator=should_not_run,
            expected_binding_version=2,
            base_version=staged["workspace"]["version"],
            operation_id="reject-stale-lineage",
            source_quiesced_confirmed=True,
        )
    assert calls == 0
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "attestation_count"
    ] == 1


def test_lineage_trigger_rejects_a_service_context_raw_insert(tmp_path) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    first = _attest_active(
        store,
        workspace_id,
        revalidator=lambda _proof, _evidence, _projection: True,
    )
    staged = _stage_next_generation(
        store,
        workspace_id,
        base_version=first["workspace"]["version"],
        operation_id="stage-raw-lineage",
    )
    stale = _evidence(source_epoch=1)
    con = WorkspaceStore._configure(
        sqlite3.connect(store.db_path),
        query_only=False,
        healing_clock=lambda: 1_000,
    )
    try:
        con.create_function(
            "workspace_incarnation_attestation_actor", 0, lambda: "owner"
        )
        con.create_function(
            "workspace_incarnation_attestation_operation_id",
            0,
            lambda: "raw-lineage",
        )
        with pytest.raises(sqlite3.IntegrityError, match="lineage is stale"):
            con.execute(
                """
                INSERT INTO workspace_page_source_incarnation_attestations(
                  workspace_id,source_owner_subject_id,source_page_id,
                  binding_version,source_incarnation_id,
                  source_incarnation_generation,source_incarnation_retired,
                  source_epoch,full_sweep_generation,source_device,
                  source_inode,source_link_count,source_change_generation,
                  source_change_mask,source_exists,source_active,
                  source_receipt_sha256,source_receipt_domain,
                  reviewed_by_subject_id,review_operation_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    workspace_id,
                    "owner",
                    "page-1",
                    2,
                    stale.incarnation_id,
                    stale.incarnation_generation,
                    0,
                    stale.source_epoch,
                    stale.full_sweep_generation,
                    stale.source_device,
                    stale.source_inode,
                    stale.source_link_count,
                    stale.change_generation,
                    stale.change_mask,
                    1,
                    1,
                    stale.receipt_sha256,
                    store_module.LEGACY_INCARNATION_RECEIPT_DOMAIN,
                    "owner",
                    "raw-lineage",
                    1_000,
                ),
            )
        con.rollback()
    finally:
        con.close()
    assert staged["binding"]["version"] == 2
    assert store.verify_deep_health()["healthy"] is True


def test_deep_health_rejects_noncanonical_attestation_operation_result(
    tmp_path,
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    _attest_active(
        store,
        workspace_id,
        revalidator=lambda _proof, _evidence, _projection: True,
    )
    con = sqlite3.connect(store.db_path)
    try:
        trigger_sql = con.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type='trigger'
              AND name='trg_workspace_operations_immutable_update'
            """
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_operations_immutable_update")
        con.execute(
            """
            UPDATE workspace_operations SET result_json='{}'
            WHERE actor_subject_id='owner' AND operation_id='attest-page'
            """
        )
        con.execute(trigger_sql)
        con.commit()
    finally:
        con.close()

    report = store.deep_health()
    assert report["healthy"] is False
    assert "page_source_incarnation_attestation_commitment_invalid" in report[
        "issues"
    ]
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "ready_for_resolver_cutover"
    ] is False

    callback_calls = 0

    def must_not_revalidate(_proof, _evidence, _projection):
        nonlocal callback_calls
        callback_calls += 1
        return True

    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        _attest_active(
            store,
            workspace_id,
            revalidator=must_not_revalidate,
        )
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        store.get_page_source_incarnation_attestation_replay(
            "owner",
            workspace_id,
            "owner",
            "page-1",
            expected_binding_version=1,
            operation_id="attest-page",
        )
    assert callback_calls == 0


def test_pending_review_remains_invisible_and_does_not_publish_locators(
    tmp_path,
) -> None:
    store, workspace_id = _store(tmp_path)
    staged = store.stage_page_source_binding(
        "owner",
        workspace_id,
        _proof(),
        base_version=1,
        operation_id="stage-page",
    )
    assert staged["binding"]["state"] == "pending"
    calls = 0

    def revalidate(_proof, _evidence, projection):
        nonlocal calls
        calls += 1
        assert projection is None
        return True

    reviewed = store.attest_page_source_incarnation(
        "owner",
        workspace_id,
        _proof(),
        _evidence(),
        source_revalidator=revalidate,
        expected_binding_version=1,
        base_version=1,
        operation_id="attest-pending",
        source_quiesced_confirmed=True,
    )

    assert calls == 1
    assert reviewed["binding"]["state"] == "pending"
    assert reviewed["workspace"]["version"] == 1
    assert reviewed["projection"] is None
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute(
            "SELECT state FROM workspace_page_sources"
        ).fetchone() == ("pending",)
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections"
        ).fetchone() == (0,)
    finally:
        con.close()
    readiness = store.incarnation_attestation_readiness("owner", workspace_id)
    assert readiness["attested_pending_binding_count"] == 1
    assert readiness["active_binding_count"] == 0
    assert readiness["ready_for_resolver_cutover"] is False

    activated = store.activate_page_source_binding(
        "owner",
        workspace_id,
        _proof(),
        source_revalidator=lambda _candidate: True,
        expected_binding_version=1,
        base_version=1,
        operation_id="activate-reviewed-pending",
    )
    readiness = store.incarnation_attestation_readiness("owner", workspace_id)
    assert readiness["attested_active_binding_count"] == 1
    assert readiness["ready_for_resolver_cutover"] is False

    store.replace_page_locator_projection(
        "owner",
        workspace_id,
        _projection(),
        projection_revalidator=lambda _candidate: True,
        base_version=activated["workspace"]["version"],
        operation_id="project-reviewed-active",
    )
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "ready_for_resolver_cutover"
    ] is False


def test_review_is_current_workspace_admin_only_and_fails_before_source_io(
    tmp_path,
) -> None:
    store, workspace_id = _store(tmp_path)
    added = store.set_member(
        "owner",
        workspace_id,
        "member",
        role="member",
        base_version=1,
        operation_id="add-member",
    )
    bound = store.bind_page_source(
        "owner",
        workspace_id,
        _proof(),
        source_revalidator=lambda _candidate: True,
        base_version=added["workspace"]["version"],
        operation_id="bind-page",
    )
    calls = 0

    def should_not_run(*_args):
        nonlocal calls
        calls += 1
        return True

    with pytest.raises(PermissionError):
        store.attest_page_source_incarnation(
            "member",
            workspace_id,
            _proof(actor="member"),
            _evidence(),
            source_revalidator=should_not_run,
            locator_projection=_projection(actor="member"),
            expected_binding_version=1,
            base_version=bound["workspace"]["version"],
            operation_id="member-review",
            source_quiesced_confirmed=True,
        )
    assert calls == 0
    assert store.audit_counts()["operations"] == 4

    promoted = store.set_member(
        "owner",
        workspace_id,
        "member",
        role="membership_admin",
        base_version=bound["workspace"]["version"],
        operation_id="promote-member-admin",
    )
    reviewed = store.attest_page_source_incarnation(
        "member",
        workspace_id,
        # Source evidence remains bound to the original owner principal; the
        # current workspace admin is only the explicit catalog reviewer.
        _proof(actor="owner"),
        _evidence(),
        source_revalidator=lambda proof, _evidence, projection: (
            proof.actor_subject_id == "owner"
            and projection is not None
            and projection.actor_subject_id == "owner"
        ),
        locator_projection=_projection(actor="owner"),
        expected_binding_version=1,
        base_version=promoted["workspace"]["version"],
        operation_id="admin-reviews-owner-source",
        source_quiesced_confirmed=True,
    )
    assert reviewed["attestation"]["reviewed_by_subject_id"] == "member"
    assert reviewed["binding"]["state"] == "active"


def test_stale_proof_bad_digest_and_missing_active_projection_fail_closed(
    tmp_path,
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)
    calls = 0

    def should_not_run(*_args):
        nonlocal calls
        calls += 1
        return True

    before = store.audit_counts()
    with pytest.raises(ValueError, match="source quiescence"):
        store.attest_page_source_incarnation(
            "owner",
            workspace_id,
            _proof(),
            _evidence(),
            source_revalidator=should_not_run,
            locator_projection=_projection(),
            expected_binding_version=1,
            base_version=2,
            operation_id="not-quiesced",
            source_quiesced_confirmed=False,
        )
    assert store.audit_counts() == before

    with pytest.raises(ValueError, match="proof is stale"):
        store.attest_page_source_incarnation(
            "owner",
            workspace_id,
            _proof(revision=2),
            _evidence(),
            source_revalidator=should_not_run,
            locator_projection=_projection(),
            expected_binding_version=1,
            base_version=2,
            operation_id="stale-proof",
            source_quiesced_confirmed=True,
        )
    bad = _evidence()
    bad = LegacyPageIncarnationEvidence(
        **{**bad.__dict__, "receipt_sha256": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValueError, match="digest mismatch"):
        store.attest_page_source_incarnation(
            "owner",
            workspace_id,
            _proof(),
            bad,
            source_revalidator=should_not_run,
            locator_projection=_projection(),
            expected_binding_version=1,
            base_version=2,
            operation_id="bad-digest",
            source_quiesced_confirmed=True,
        )
    with pytest.raises(ValueError, match="requires locator projection"):
        store.attest_page_source_incarnation(
            "owner",
            workspace_id,
            _proof(),
            _evidence(),
            source_revalidator=should_not_run,
            expected_binding_version=1,
            base_version=2,
            operation_id="missing-projection",
            source_quiesced_confirmed=True,
        )
    assert calls == 0
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "attestation_count"
    ] == 0


def test_source_revalidation_refusal_and_late_audit_failure_roll_back_everything(
    tmp_path, monkeypatch
) -> None:
    store, workspace_id = _store(tmp_path)
    _active_binding(store, workspace_id)

    with pytest.raises(ValueError, match="invalid or stale"):
        _attest_active(
            store,
            workspace_id,
            revalidator=lambda _proof, _evidence, _projection: False,
            operation_id="source-refused",
        )
    assert store.incarnation_attestation_readiness("owner", workspace_id)[
        "attestation_count"
    ] == 0

    con = sqlite3.connect(store.db_path)
    try:
        duplicate_audit_id = con.execute(
            "SELECT id FROM workspace_audit_events ORDER BY created_at,id LIMIT 1"
        ).fetchone()[0]
        before = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "workspace_page_source_incarnation_attestations",
                "workspace_page_source_projections",
                "workspace_operations",
                "workspace_audit_events",
                "workspace_outbox",
            )
        }
    finally:
        con.close()

    original_new_id = store_module._new_id

    def colliding_id(prefix: str) -> str:
        if prefix == "waud":
            return duplicate_audit_id
        return original_new_id(prefix)

    monkeypatch.setattr(store_module, "_new_id", colliding_id)
    with pytest.raises(sqlite3.IntegrityError):
        _attest_active(
            store,
            workspace_id,
            revalidator=lambda _proof, _evidence, _projection: True,
            operation_id="late-audit-failure",
        )

    con = sqlite3.connect(store.db_path)
    try:
        after = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    finally:
        con.close()
    assert after == before
