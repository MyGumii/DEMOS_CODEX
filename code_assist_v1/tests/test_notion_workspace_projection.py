from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_projection as projection_module
from code_assist_v1.notion_workspace_projection import (
    LegacyPageHealingCandidate,
    LegacyPageQuarantineCandidate,
    LegacyProjectionAmbiguousError,
    LegacyProjectionOversizeError,
    LegacyProjectionPermissionError,
    LegacyProjectionStaleError,
    LegacyProjectionSupersededError,
    UnsafeLegacyFileError,
    WorkspaceProjectionError,
    capture_exact_owner_projection,
    capture_exact_owner_healing_candidate,
    capture_exact_owner_healing_outcome,
    empty_shadow_counts,
    project_exact_owner_page,
    revalidate_exact_owner_healing_candidate,
    revalidate_exact_owner_healing_outcome,
    shadow_reconcile_exact_owner_page,
)
from code_assist_v1.notion_legacy_changes import (
    create_legacy_owner_integrity_attestation,
)
from code_assist_v1.notion_workspace_inventory import (
    LegacyInventoryBoundsExceeded,
)
from code_assist_v1.notion_legacy_change_migration import (
    apply_plan as apply_change_tracking_plan,
    build_plan as build_change_tracking_plan,
)
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Projection", operation_id="create-projection"
    )["workspace"]
    page = notion_store.create_page("owner", title="PRIVATE PROJECTOR TITLE")["page"]
    uploaded = notion_store.upload_file(
        "owner", page["id"], filename="secret-payroll.pdf", raw=b"classified"
    )
    public = notion_store.enable_public_link("owner", page["id"])
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=workspace["version"],
        operation_id="bind-projection",
    )
    source = tmp_path / "owner" / "notion" / "notion.db"
    catalog = Path(store.db_path)
    return store, workspace, page, uploaded, public, bound, source, catalog


def _capture(tmp_path, workspace, page, source, catalog):
    return capture_exact_owner_projection(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )


def _install_change_tracking(tmp_path: Path, source: Path) -> None:
    backups = tmp_path / "change-tracking-backups"
    backups.mkdir(exist_ok=True)
    plan = build_change_tracking_plan(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_change_tracking_plan(
        plan,
        confirm_checksum=plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before.db",
    )


def _advance_binding_generation(store, workspace, page, bound):
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    staged = store.stage_page_source_binding(
        "owner",
        workspace["id"],
        proof,
        base_version=bound["workspace"]["version"],
        operation_id="restage-projection-v2",
    )
    return store.activate_page_source_binding(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="activate-projection-v2",
    )


def _healing_outcome_kwargs(
    tmp_path, workspace, page, bound, source, catalog, *, attestation=None
):
    return {
        "user_data_root": tmp_path,
        "source_owner_id": "owner",
        "workspace_id": workspace["id"],
        "source_page_id": page["id"],
        "expected_source_db_path": source,
        "expected_catalog_db_path": catalog,
        "expected_binding_version": bound["binding"]["version"],
        "source_quiesced_confirmed": True,
        "integrity_attestation": attestation,
    }


def test_capture_is_exact_owner_sanitized_and_scan_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, uploaded, public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        notion_store,
        "_known_workspace_users",
        lambda: (_ for _ in ()).throw(AssertionError("directory scan forbidden")),
    )
    captured = _capture(tmp_path, workspace, page, source, catalog)
    projection = captured["projection"]
    assert projection.binding_version == bound["binding"]["version"]
    assert [item.source_file_id for item in projection.file_sources] == [uploaded["id"]]
    assert len(projection.public_sources) == 1
    assert projection.public_sources[0].token_sha256.startswith("sha256:")
    text = json.dumps(
        {
            "workspace_version": captured["workspace_version"],
            "source_database_fingerprint": captured["source_database_fingerprint"],
            "descriptor": projection.descriptor(),
        },
        sort_keys=True,
    )
    assert str(public["token"]) not in text
    assert "secret-payroll" not in text
    assert "PRIVATE PROJECTOR TITLE" not in text
    assert "classified" not in text
    assert "rel_path" not in text


def test_sync_projection_requires_quiesce_projects_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, page, uploaded, public, _bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    with pytest.raises(WorkspaceProjectionError, match="source_quiesced"):
        project_exact_owner_page(
            user_data_root=tmp_path,
            source_owner_id="owner",
            actor_subject_id="owner",
            workspace_id=workspace["id"],
            source_page_id=page["id"],
            expected_source_db_path=source,
            expected_catalog_db_path=catalog,
            operation_id="project-page",
            source_quiesced_confirmed=False,
        )
    result = project_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        operation_id="project-page",
        source_quiesced_confirmed=True,
    )
    assert result["projection"]["file_source_count"] == 1
    assert result["projection"]["public_source_count"] == 1
    assert shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    ) == {**empty_shadow_counts(), "match": 1}
    con = sqlite3.connect(catalog)
    try:
        payload = "\n".join(
            str(row[0])
            for query in (
                "SELECT result_json FROM workspace_operations",
                "SELECT payload_json FROM workspace_audit_events",
                "SELECT payload_json FROM workspace_outbox",
            )
            for row in con.execute(query).fetchall()
        )
    finally:
        con.close()
    assert str(public["token"]) not in payload
    assert "secret-payroll" not in payload
    assert "rel_path" not in payload
    assert uploaded["id"] not in payload


def test_capture_rejects_stale_page_binding_without_projection_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, page, _uploaded, _public, _bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    notion_store.update_page("owner", page["id"], {"title": "changed"})
    with pytest.raises(WorkspaceProjectionError, match="binding is stale"):
        _capture(tmp_path, workspace, page, source, catalog)
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections"
        ).fetchone()[0] == 0
    finally:
        con.close()
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert sum(counts.values()) == 1
    assert counts["stale"] == 1


def test_healing_candidate_accepts_fresh_proof_and_revalidation_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, page, uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    notion_store.update_page("owner", page["id"], {"title": "fresh private value"})

    # The normal projector remains correctly fenced by the original binding
    # proof, while healing intentionally captures a new proof for atomic apply.
    with pytest.raises(LegacyProjectionStaleError):
        _capture(tmp_path, workspace, page, source, catalog)
    candidate = capture_exact_owner_healing_candidate(
        user_data_root=tmp_path,
        source_owner_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
        source_quiesced_confirmed=True,
    )
    assert isinstance(candidate, LegacyPageHealingCandidate)
    assert candidate.workspace_version == bound["workspace"]["version"]
    assert candidate.bound_by_subject_id == "owner"
    assert candidate.proof_actor_subject_id == "owner"
    assert candidate.source_proof is not None
    assert candidate.source_proof.actor_subject_id == "owner"
    assert candidate.source_proof.page_id == page["id"]
    assert candidate.change_generation >= 2
    assert candidate.source_exists is True
    assert candidate.source_active is True
    assert [item.source_file_id for item in candidate.file_sources] == [uploaded["id"]]
    assert revalidate_exact_owner_healing_candidate(
        candidate,
        user_data_root=tmp_path,
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        source_quiesced_confirmed=True,
    ) is True

    notion_store.update_page("owner", page["id"], {"title": "drifted again"})
    assert revalidate_exact_owner_healing_candidate(
        candidate,
        user_data_root=tmp_path,
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        source_quiesced_confirmed=True,
    ) is False


def test_healing_candidate_tombstone_requires_exact_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    notion_store.archive_page("owner", page["id"])
    notion_store.delete_page_permanent("owner", page["id"])
    candidate = capture_exact_owner_healing_candidate(
        user_data_root=tmp_path,
        source_owner_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
        source_quiesced_confirmed=True,
    )
    assert candidate.source_exists is False
    assert candidate.source_active is False
    assert candidate.source_proof is None
    assert candidate.file_sources == ()
    assert candidate.public_sources == ()


def test_healing_candidate_archived_lifecycle_is_not_a_delete_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    notion_store.archive_page("owner", page["id"])
    candidate = capture_exact_owner_healing_candidate(
        user_data_root=tmp_path,
        source_owner_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
        source_quiesced_confirmed=True,
    )
    assert candidate.source_exists is True
    assert candidate.source_active is False
    assert candidate.source_proof is None
    assert candidate.file_sources == ()
    assert candidate.public_sources == ()


def test_healing_candidate_is_bounded_and_quiescence_precedes_source_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    monkeypatch.setattr(projection_module, "_MAX_SHADOW_PAGE_METADATA_BYTES", 1)
    with pytest.raises(LegacyProjectionOversizeError):
        capture_exact_owner_healing_candidate(
            user_data_root=tmp_path,
            source_owner_id="owner",
            workspace_id=workspace["id"],
            source_page_id=page["id"],
            expected_source_db_path=source,
            expected_catalog_db_path=catalog,
            expected_binding_version=bound["binding"]["version"],
            source_quiesced_confirmed=True,
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            projection_module,
            "resolve_inventory_paths",
            lambda **_kwargs: pytest.fail("source path must not be consulted"),
        )
        with pytest.raises(WorkspaceProjectionError, match="source_quiesced"):
            capture_exact_owner_healing_candidate(
                user_data_root=tmp_path / "missing",
                source_owner_id="owner",
                workspace_id=workspace["id"],
                source_page_id=page["id"],
                expected_source_db_path=tmp_path / "missing.db",
                expected_catalog_db_path=tmp_path / "missing-catalog.db",
                expected_binding_version=bound["binding"]["version"],
                source_quiesced_confirmed=False,
            )


@pytest.mark.parametrize(
    ("error_type", "expected_reason"),
    [
        (LegacyProjectionPermissionError, "legacy_permission_revoked"),
        (UnsafeLegacyFileError, "unsafe_path"),
        (LegacyProjectionAmbiguousError, "ambiguous"),
        (LegacyProjectionOversizeError, "oversize"),
        (LegacyInventoryBoundsExceeded, "oversize"),
    ],
)
def test_healing_outcome_maps_only_typed_permanent_active_refusals_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    expected_reason: str,
) -> None:
    _store, workspace, page, _uploaded, public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    attestation = create_legacy_owner_integrity_attestation(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    authenticated: list[bool] = []
    original_authenticate = projection_module._authenticate_owner_snapshot

    def authenticate_spy(*args, **kwargs):
        authenticated.append(True)
        return original_authenticate(*args, **kwargs)

    def permanent_refusal(*_args, **_kwargs):
        raise error_type("PRIVATE /owner/files/payroll token material")

    monkeypatch.setattr(
        projection_module, "_authenticate_owner_snapshot", authenticate_spy
    )
    monkeypatch.setattr(
        projection_module, "_capture_bounded_fresh_page", permanent_refusal
    )
    outcome = capture_exact_owner_healing_outcome(
        **_healing_outcome_kwargs(
            tmp_path,
            workspace,
            page,
            bound,
            source,
            catalog,
            attestation=attestation,
        )
    )

    assert isinstance(outcome, LegacyPageQuarantineCandidate)
    assert outcome.quarantine_reason == expected_reason
    assert outcome.source_exists is outcome.source_active is True
    assert authenticated == [True]
    payload = json.dumps(outcome.descriptor(), sort_keys=True)
    assert "PRIVATE" not in payload
    assert "payroll" not in payload
    assert str(public["token"]) not in payload
    assert "source_proof" not in payload
    assert "file_sources" not in payload
    assert "public_sources" not in payload


@pytest.mark.parametrize(
    "transient_error",
    [
        WorkspaceProjectionError("temporary source read unavailable"),
        sqlite3.OperationalError("temporary sqlite read failure"),
    ],
)
def test_healing_outcome_preserves_transient_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transient_error: Exception,
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)

    def transient(*_args, **_kwargs):
        raise transient_error

    monkeypatch.setattr(projection_module, "_capture_bounded_fresh_page", transient)
    with pytest.raises(WorkspaceProjectionError):
        capture_exact_owner_healing_outcome(
            **_healing_outcome_kwargs(
                tmp_path, workspace, page, bound, source, catalog
            )
        )


def test_quarantine_outcome_attested_revalidation_is_source_only_and_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)
    attestation = create_legacy_owner_integrity_attestation(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    refusal = {"type": UnsafeLegacyFileError}

    def permanent_refusal(*_args, **_kwargs):
        raise refusal["type"]("PRIVATE source detail")

    monkeypatch.setattr(
        projection_module, "_capture_bounded_fresh_page", permanent_refusal
    )
    outcome = capture_exact_owner_healing_outcome(
        **_healing_outcome_kwargs(
            tmp_path,
            workspace,
            page,
            bound,
            source,
            catalog,
            attestation=attestation,
        )
    )
    assert isinstance(outcome, LegacyPageQuarantineCandidate)
    monkeypatch.setattr(
        projection_module,
        "_catalog_binding",
        lambda *_args, **_kwargs: pytest.fail(
            "attested revalidation must not read the catalog"
        ),
    )
    revalidate_kwargs = {
        "user_data_root": tmp_path,
        "expected_source_db_path": source,
        "expected_catalog_db_path": catalog,
        "source_quiesced_confirmed": True,
        "integrity_attestation": attestation,
    }
    assert revalidate_exact_owner_healing_outcome(
        outcome, **revalidate_kwargs
    ) is True

    refusal["type"] = LegacyProjectionAmbiguousError
    assert revalidate_exact_owner_healing_outcome(
        outcome, **revalidate_kwargs
    ) is False
    refusal["type"] = UnsafeLegacyFileError
    notion_store.update_page("owner", page["id"], {"title": "source drift"})
    assert revalidate_exact_owner_healing_outcome(
        outcome, **revalidate_kwargs
    ) is False


def test_expected_binding_generation_fences_before_source_capture_or_store_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, page, _uploaded, _public, bound_v1, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    active_v2 = _advance_binding_generation(store, workspace, page, bound_v1)
    assert active_v2["binding"]["version"] == bound_v1["binding"]["version"] + 1

    opened: list[Path] = []
    original_open = projection_module._open_read_only

    def tracked_open(path):
        opened.append(Path(path).resolve())
        return original_open(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(projection_module, "_open_read_only", tracked_open)
        scoped.setattr(
            WorkspaceStore,
            "replace_page_locator_projection",
            lambda *args, **kwargs: pytest.fail("store replace must not run"),
        )
        with pytest.raises(
            LegacyProjectionSupersededError, match="generation is superseded"
        ):
            project_exact_owner_page(
                user_data_root=tmp_path,
                source_owner_id="owner",
                actor_subject_id="owner",
                workspace_id=workspace["id"],
                source_page_id=page["id"],
                expected_source_db_path=source,
                expected_catalog_db_path=catalog,
                expected_binding_version=bound_v1["binding"]["version"],
                operation_id="stale-generation-must-not-write",
                source_quiesced_confirmed=True,
            )

    # Only the catalog binding lookup occurred; the legacy source database was
    # never opened for page/file/public capture.
    assert opened == [catalog.resolve()]
    con = sqlite3.connect(catalog)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_source_projections"
        ).fetchone()[0] == 0
        assert con.execute(
            """SELECT COUNT(*) FROM workspace_operations
               WHERE operation_id='stale-generation-must-not-write'"""
        ).fetchone()[0] == 0
    finally:
        con.close()

    exact = project_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=active_v2["binding"]["version"],
        operation_id="exact-generation-projects",
        source_quiesced_confirmed=True,
    )
    assert exact["projection"]["binding_version"] == active_v2["binding"]["version"]
    assert exact["projection"]["changed"] is True


@pytest.mark.parametrize("invalid", [False, 0, -1, "1"])
def test_expected_binding_generation_rejects_invalid_values_before_capture(
    tmp_path: Path,
    invalid,
) -> None:
    with pytest.raises(WorkspaceProjectionError, match="expected_binding_version"):
        capture_exact_owner_projection(
            user_data_root=tmp_path,
            source_owner_id="owner",
            actor_subject_id="owner",
            workspace_id="ws_not_consulted",
            source_page_id="page-not-consulted",
            expected_source_db_path=tmp_path / "not-consulted.db",
            expected_catalog_db_path=tmp_path / "not-consulted-catalog.db",
            expected_binding_version=invalid,
        )


def test_unsafe_attachment_path_is_redacted_and_shadow_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, uploaded, _public, _bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    con = sqlite3.connect(source)
    try:
        con.execute("UPDATE files SET rel_path='../outside.bin' WHERE id=?", (uploaded["id"],))
        con.commit()
    finally:
        con.close()
    with pytest.raises(UnsafeLegacyFileError) as error:
        _capture(tmp_path, workspace, page, source, catalog)
    assert "outside" not in str(error.value)
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert counts == {**empty_shadow_counts(), "unsafe_path": 1}


def test_shadow_safe_miss_contains_only_stable_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, public, _bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert counts == {**empty_shadow_counts(), "safe_miss": 1}
    text = json.dumps(counts, sort_keys=True)
    assert page["id"] not in text
    assert str(public["token"]) not in text


def test_shadow_detects_public_drift_and_corrupt_summary_without_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, _bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    project_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        operation_id="project-shadow",
        source_quiesced_confirmed=True,
    )
    notion_store.update_public_link_options(
        "owner", page["id"], allow_duplicate=False
    )
    stale = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert stale == {**empty_shadow_counts(), "stale": 1}

    # Restore the source and prove a corrupt secret-free summary is also stale.
    notion_store.update_public_link_options("owner", page["id"], allow_duplicate=True)
    con = sqlite3.connect(catalog)
    try:
        con.execute("DROP TRIGGER trg_page_projection_identity_immutable")
        con.execute(
            "UPDATE workspace_page_source_projections SET projection_sha256=?",
            ("sha256:" + "0" * 64,),
        )
        con.commit()
    finally:
        con.close()
    corrupt = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
    )
    assert corrupt == {**empty_shadow_counts(), "stale": 1}


def test_shadow_capture_skips_unbounded_database_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        projection_module,
        "logical_database_fingerprint",
        lambda _con: (_ for _ in ()).throw(
            AssertionError("full database fingerprint forbidden in shadow mode")
        ),
    )
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
    )
    assert counts == {**empty_shadow_counts(), "safe_miss": 1}


def test_shadow_capture_rejects_oversize_page_with_stable_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(projection_module, "_MAX_SHADOW_BLOCKS", 0)
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
    )
    assert counts == {**empty_shadow_counts(), "oversize": 1}


@pytest.mark.parametrize(
    "cap_name",
    ["_MAX_SHADOW_PAGE_METADATA_BYTES", "_MAX_SHADOW_FILE_METADATA_BYTES"],
)
def test_shadow_capture_bounds_page_and_file_metadata_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_name: str,
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(projection_module, cap_name, 1)
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
    )
    assert counts == {**empty_shadow_counts(), "oversize": 1}


def test_shadow_permission_ancestry_has_a_hard_hop_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(
            """
            CREATE TABLE pages(id TEXT PRIMARY KEY,parent_id TEXT,archived INTEGER);
            CREATE TABLE page_shares(
              page_id TEXT,shared_user_id TEXT,role TEXT,revoked_at INTEGER
            );
            INSERT INTO pages VALUES('child','parent',0);
            INSERT INTO pages VALUES('parent','root',0);
            INSERT INTO pages VALUES('root','',0);
            """
        )
        monkeypatch.setattr(projection_module, "_MAX_SHADOW_PERMISSION_HOPS", 2)
        with pytest.raises(
            projection_module.LegacyProjectionOversizeError,
            match="permission ancestry",
        ):
            projection_module._bounded_inherited_share_role(
                con, "child", "reader"
            )
    finally:
        con.close()


def test_shadow_linked_reference_count_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    con = sqlite3.connect(source)
    try:
        block_id = con.execute(
            "SELECT id FROM blocks WHERE page_id=? LIMIT 1", (page["id"],)
        ).fetchone()[0]
        con.execute(
            "UPDATE blocks SET props_json=? WHERE id=?",
            (json.dumps({"page_id": "private-linked-canary"}), block_id),
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(projection_module, "_MAX_SHADOW_LINKED_REFERENCES", 0)
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=bound["binding"]["version"],
    )
    assert counts == {**empty_shadow_counts(), "oversize": 1}


def test_shadow_linked_permission_checks_share_one_global_query_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Bounded linked permissions", operation_id="create-linked"
    )["workspace"]
    member = store.set_member(
        "owner",
        workspace["id"],
        "admin",
        role="membership_admin",
        base_version=workspace["version"],
        operation_id="add-linked-admin",
    )
    linked = notion_store.create_page("owner", title="PRIVATE LINKED TITLE")["page"]
    target = notion_store.create_page("owner", title="Shared target")["page"]
    notion_store.replace_blocks(
        "owner",
        target["id"],
        [{
            "id": "b_" + "1" * 32,
            "type": "link_to_page",
            "content": "",
            "props": {"page_id": linked["id"]},
        }],
    )
    notion_store.share_page("owner", target["id"], "admin", "view")
    proof = notion_store.capture_legacy_page_source_proof(
        "admin", "owner", target["id"]
    )
    bound = store.bind_page_source(
        "admin",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=member["workspace"]["version"],
        operation_id="bind-linked-admin",
    )
    source = tmp_path / "owner" / "notion" / "notion.db"
    monkeypatch.setattr(projection_module, "_MAX_SHADOW_PERMISSION_QUERIES", 1)
    counts = shadow_reconcile_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="admin",
        workspace_id=workspace["id"],
        source_page_id=target["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=Path(store.db_path),
        expected_binding_version=bound["binding"]["version"],
    )
    assert counts == {**empty_shadow_counts(), "oversize": 1}


def test_active_delete_and_same_id_recreate_is_quarantined_as_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, page, _uploaded, _public, bound, source, catalog = _fixture(
        tmp_path, monkeypatch
    )
    _install_change_tracking(tmp_path, source)

    con = sqlite3.connect(source)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        old = con.execute("SELECT * FROM pages WHERE id=?", (page["id"],)).fetchone()
        assert old is not None
        values = dict(old)
        columns = tuple(values)
        con.execute("DELETE FROM pages WHERE id=?", (page["id"],))
        values.update(
            title="RECREATED INCARNATION",
            revision=1,
            created_at=int(values["created_at"]) + 10,
            updated_at=int(values["updated_at"]) + 10,
            archived=0,
            archived_at=None,
        )
        placeholders = ",".join("?" for _ in columns)
        con.execute(
            f"INSERT INTO pages({','.join(columns)}) VALUES({placeholders})",
            tuple(values[column] for column in columns),
        )
        con.execute(
            """
            INSERT INTO blocks(
              id,page_id,owner_user_id,position,block_type,content,props_json,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "b_" + "a" * 32,
                page["id"],
                "owner",
                0,
                "paragraph",
                "new incarnation",
                "{}",
                values["created_at"],
                values["updated_at"],
            ),
        )
        con.commit()
    finally:
        con.close()

    attestation = create_legacy_owner_integrity_attestation(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    outcome = capture_exact_owner_healing_outcome(
        **_healing_outcome_kwargs(
            tmp_path,
            workspace,
            page,
            bound,
            source,
            catalog,
            attestation=attestation,
        )
    )
    assert isinstance(outcome, LegacyPageQuarantineCandidate)
    assert outcome.quarantine_reason == "ambiguous"
    assert revalidate_exact_owner_healing_outcome(
        outcome,
        user_data_root=tmp_path,
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        source_quiesced_confirmed=True,
        integrity_attestation=attestation,
    ) is True


def test_equal_revision_linked_page_drift_remains_a_healing_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner", name="Linked drift", operation_id="create-linked-drift"
    )["workspace"]
    linked = notion_store.create_page("owner", title="linked before")["page"]
    page = notion_store.create_page("owner", title="referencing page")["page"]
    notion_store.replace_blocks(
        "owner",
        page["id"],
        [{
            "id": "b_" + "b" * 32,
            "type": "child_page",
            "content": "",
            "props": {"page_id": linked["id"]},
        }],
    )
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        base_version=workspace["version"],
        operation_id="bind-linked-drift",
    )
    source = tmp_path / "owner" / "notion" / "notion.db"
    _install_change_tracking(tmp_path, source)
    before = notion_store.get_page("owner", page["id"])["page"]
    notion_store.update_page("owner", linked["id"], {"title": "linked after"})
    after = notion_store.get_page("owner", page["id"])["page"]
    assert after["revision"] == before["revision"]

    attestation = create_legacy_owner_integrity_attestation(
        user_data_root=tmp_path,
        source_owner_id="owner",
        expected_source_db_path=source,
        source_quiesced_confirmed=True,
    )
    outcome = capture_exact_owner_healing_outcome(
        user_data_root=tmp_path,
        source_owner_id="owner",
        workspace_id=workspace["id"],
        source_page_id=page["id"],
        expected_source_db_path=source,
        expected_catalog_db_path=store.db_path,
        expected_binding_version=bound["binding"]["version"],
        source_quiesced_confirmed=True,
        integrity_attestation=attestation,
    )
    assert isinstance(outcome, LegacyPageHealingCandidate)
    assert outcome.source_proof is not None
    assert outcome.source_proof.content_sha256 != proof.content_sha256
