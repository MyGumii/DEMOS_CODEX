from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_incarnation_orchestration as orchestration
from code_assist_v1.notion_legacy_change_migration import (
    apply_plan as apply_v1_plan,
    build_plan as build_v1_plan,
)
from code_assist_v1.notion_legacy_incarnation_migration import (
    apply_plan as apply_v2_plan,
    build_plan as build_v2_plan,
)
from code_assist_v1.notion_workspace_inventory import _page_proof
from code_assist_v1.notion_workspace_rehearsal import (
    REHEARSAL_SENTINEL_CONTENT,
    REHEARSAL_SENTINEL_NAME,
)
from code_assist_v1.notion_workspace_store import (
    LegacyPageSourceProof,
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    root = tmp_path / "user_data"
    root.mkdir()
    (root / REHEARSAL_SENTINEL_NAME).write_bytes(REHEARSAL_SENTINEL_CONTENT)
    backups = root / "backups"
    backups.mkdir()
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(root))

    page = notion_store.create_page("owner", title="PRIVATE SOURCE TITLE")["page"]
    notion_store.replace_blocks(
        "owner",
        page["id"],
        [{"type": "paragraph", "content": "PRIVATE BODY", "props": {}}],
    )
    uploaded = notion_store.upload_file(
        "owner", page["id"], filename="private.txt", raw=b"private bytes"
    )
    public = notion_store.enable_public_link("owner", page["id"])
    source = root / "owner" / "notion" / "notion.db"

    v1_plan = build_v1_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_v1_plan(
        v1_plan,
        confirm_checksum=v1_plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v1.db",
    )
    v2_plan = build_v2_plan(
        user_data_root=root,
        source_owner_id="owner",
        expected_source_db_path=source,
    )
    apply_v2_plan(
        v2_plan,
        confirm_checksum=v2_plan["plan_checksum"],
        source_quiesced_confirmed=True,
        backup_confirmed=True,
        expected_backup_path=backups / "before-v2.db",
    )

    source_con = notion_store._connect("owner")
    try:
        source_page = source_con.execute(
            "SELECT * FROM pages WHERE id=?", (page["id"],)
        ).fetchone()
        proof = _page_proof(
            source_con,
            actor="owner",
            owner="owner",
            page=source_page,
            role="owner",
        )
    finally:
        source_con.close()

    store = WorkspaceStore(
        catalog_path_for_user_data_root(root), trusted_clock=lambda: 2_000
    )
    workspace = store.create_workspace(
        "reviewer", name="Offline incarnation", operation_id="create-workspace"
    )["workspace"]
    added = store.set_member(
        "reviewer",
        workspace["id"],
        "owner",
        role="member",
        base_version=workspace["version"],
        operation_id="add-source-owner",
    )
    bound = store.bind_page_source(
        "owner",
        workspace["id"],
        proof,
        source_revalidator=lambda candidate: candidate == proof,
        base_version=added["workspace"]["version"],
        operation_id="bind-source-page",
    )
    return {
        "root": root,
        "source": source,
        "catalog": Path(store.db_path),
        "store": store,
        "workspace_id": workspace["id"],
        "page_id": page["id"],
        "binding_version": bound["binding"]["version"],
        "file_id": uploaded["id"],
        "public_token": public["token"],
    }


def _capture_args(prepared: dict[str, object]) -> dict[str, object]:
    return {
        "workspace_id": prepared["workspace_id"],
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "source_owner_id": "owner",
        "source_page_id": prepared["page_id"],
        "expected_source_db_path": prepared["source"],
        "expected_catalog_db_path": prepared["catalog"],
        "expected_binding_version": prepared["binding_version"],
        "source_quiesced_confirmed": True,
    }


def _quarantine_prepared(prepared: dict[str, object]) -> dict:
    store = prepared["store"]
    workspace = store.get_workspace("owner", prepared["workspace_id"])
    return store.quarantine_page_source_if_stale(
        "owner",
        prepared["workspace_id"],
        "owner",
        prepared["page_id"],
        source_revalidator=lambda _proof: False,
        expected_binding_version=prepared["binding_version"],
        base_version=workspace["version"],
        operation_id="quarantine-for-offline-rebind",
    )


def _rebind_args(
    prepared: dict[str, object], quarantined: dict
) -> dict[str, object]:
    return {
        "reviewer_subject_id": "reviewer",
        "workspace_id": prepared["workspace_id"],
        "rehearsal_root": prepared["root"],
        "user_data_root": prepared["root"],
        "source_owner_id": "owner",
        "source_page_id": prepared["page_id"],
        "expected_source_db_path": prepared["source"],
        "expected_catalog_db_path": prepared["catalog"],
        "expected_quarantined_binding_version": quarantined["binding"]["version"],
        "operation_id": "offline-rebind-source-page",
        "source_quiesced_confirmed": True,
    }


def test_capture_uses_one_pinned_snapshot_for_receipt_proof_and_locators(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[int] = []
    helper_connections: list[tuple[str, int, bool]] = []
    real_open = orchestration.open_exact_legacy_change_source
    real_receipt = orchestration._receipt_from_incarnation_snapshot
    real_projection = orchestration._capture_bounded_fresh_page

    def counted_open(*args, **kwargs):
        con = real_open(*args, **kwargs)
        opened.append(id(con))
        return con

    def counted_receipt(con, **kwargs):
        helper_connections.append(("receipt", id(con), con.in_transaction))
        return real_receipt(con, **kwargs)

    def counted_projection(con, **kwargs):
        helper_connections.append(("projection", id(con), con.in_transaction))
        return real_projection(con, **kwargs)

    monkeypatch.setattr(orchestration, "open_exact_legacy_change_source", counted_open)
    monkeypatch.setattr(
        orchestration, "_receipt_from_incarnation_snapshot", counted_receipt
    )
    monkeypatch.setattr(
        orchestration, "_capture_bounded_fresh_page", counted_projection
    )

    candidate = orchestration.capture_isolated_page_incarnation_candidate(
        prepared["store"], **_capture_args(prepared)
    )

    assert len(opened) == 1
    assert helper_connections == [
        ("receipt", opened[0], True),
        ("projection", opened[0], True),
    ]
    assert candidate.source_proof.actor_subject_id == "owner"
    assert candidate.source_proof.actor_subject_id != "reviewer"
    assert candidate.source_incarnation.owner_subject_id == "owner"
    assert candidate.source_incarnation.incarnation_retired is False
    assert candidate.source_incarnation.source_exists is True
    assert candidate.source_incarnation.source_active is True
    assert candidate.source_incarnation.receipt_sha256.startswith("sha256:")
    assert candidate.locator_projection is not None
    assert [
        item.source_file_id for item in candidate.locator_projection.file_sources
    ] == [prepared["file_id"]]
    encoded = json.dumps(candidate.locator_projection.descriptor(), sort_keys=True)
    assert prepared["public_token"] not in encoded
    assert "rel_path" not in encoded
    assert "PRIVATE" not in encoded


def test_quiescence_and_sentinel_precede_user_data_path_access(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def path_access_must_not_run(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("user-data path access ran")

    monkeypatch.setattr(orchestration, "resolve_inventory_paths", path_access_must_not_run)
    args = _capture_args(prepared)
    args["source_quiesced_confirmed"] = False
    with pytest.raises(
        orchestration.WorkspaceIncarnationOrchestrationRefused,
        match="quiescence",
    ):
        orchestration.capture_isolated_page_incarnation_candidate(
            prepared["store"], **args
        )
    assert calls == 0

    bad_root = tmp_path / "no-sentinel"
    bad_root.mkdir()
    args["source_quiesced_confirmed"] = True
    args["rehearsal_root"] = bad_root
    with pytest.raises(Exception, match="sentinel"):
        orchestration.capture_isolated_page_incarnation_candidate(
            prepared["store"], **args
        )
    assert calls == 0


def test_revalidation_is_source_only_and_compares_complete_tuple(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = orchestration.capture_isolated_page_incarnation_candidate(
        prepared["store"], **_capture_args(prepared)
    )

    def catalog_binding_must_not_run(*_args, **_kwargs):
        raise AssertionError("source callback reread catalog binding")

    monkeypatch.setattr(orchestration, "_catalog_binding", catalog_binding_must_not_run)
    args = _capture_args(prepared)
    args.pop("expected_binding_version")
    assert orchestration.revalidate_isolated_page_incarnation_candidate(
        candidate, prepared["store"], **args
    ) is True

    notion_store.replace_blocks(
        "owner",
        prepared["page_id"],
        [{"type": "paragraph", "content": "changed after capture", "props": {}}],
    )
    assert orchestration.revalidate_isolated_page_incarnation_candidate(
        candidate, prepared["store"], **args
    ) is False


def test_top_level_attests_then_replays_before_all_source_and_sentinel_io(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    source_snapshots = 0
    real_capture = orchestration._capture_one_source_snapshot

    def counted_capture(**kwargs):
        nonlocal source_snapshots
        source_snapshots += 1
        return real_capture(**kwargs)

    monkeypatch.setattr(orchestration, "_capture_one_source_snapshot", counted_capture)
    args = {
        "reviewer_subject_id": "reviewer",
        **_capture_args(prepared),
        "operation_id": "offline-attest-source-page",
    }
    first = orchestration.run_isolated_page_incarnation_attestation(
        prepared["store"], **args
    )
    assert source_snapshots == 2  # capture plus source-only callback revalidation
    assert first == {
        "contract": orchestration.OFFLINE_INCARNATION_ATTESTATION_REPORT_CONTRACT,
        "offline_only": True,
        "committed": True,
        "ledger_replay": False,
        "source_io_bypassed": False,
        "source_snapshot_revalidated": True,
        "binding_state": "active",
        "locator_projection": {
            "present": True,
            "changed": True,
            "file_source_count": 1,
            "public_source_count": 1,
        },
    }
    encoded = json.dumps(first, sort_keys=True)
    for secret in (
        "reviewer",
        "owner",
        str(prepared["workspace_id"]),
        str(prepared["page_id"]),
        str(prepared["file_id"]),
        str(prepared["public_token"]),
        "inc_",
        "sha256:",
        str(prepared["root"]),
    ):
        assert secret not in encoded

    def no_sentinel_on_replay(_path):
        raise AssertionError("ledger replay accessed the sentinel")

    monkeypatch.setattr(orchestration, "_rehearsal_root", no_sentinel_on_replay)
    replay = orchestration.run_isolated_page_incarnation_attestation(
        prepared["store"],
        **{
            **args,
            "rehearsal_root": Path("Z:/missing"),
            "user_data_root": Path("Z:/missing"),
            "expected_source_db_path": Path("Z:/missing/source.db"),
            "expected_catalog_db_path": Path("Z:/missing/catalog.db"),
            "source_quiesced_confirmed": False,
        },
    )
    assert source_snapshots == 2
    assert replay == {
        **first,
        "ledger_replay": True,
        "source_io_bypassed": True,
        "source_snapshot_revalidated": False,
    }


def test_outsider_is_rejected_before_sentinel_or_source_path_access(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel_calls = 0
    source_calls = 0

    def sentinel_must_not_run(_path):
        nonlocal sentinel_calls
        sentinel_calls += 1
        raise AssertionError("outsider accessed sentinel")

    def source_must_not_run(**_kwargs):
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("outsider accessed source")

    monkeypatch.setattr(orchestration, "_rehearsal_root", sentinel_must_not_run)
    monkeypatch.setattr(
        orchestration, "_capture_one_source_snapshot", source_must_not_run
    )
    args = {
        "reviewer_subject_id": "outsider",
        **_capture_args(prepared),
        "operation_id": "outsider-source-probe",
    }
    with pytest.raises((PermissionError, FileNotFoundError)) as raised:
        orchestration.run_isolated_page_incarnation_attestation(
            prepared["store"], **args
        )
    assert sentinel_calls == source_calls == 0
    message = str(raised.value)
    assert str(prepared["workspace_id"]) not in message
    assert str(prepared["page_id"]) not in message
    assert "owner" not in message


def test_rebound_capture_uses_committed_proof_actor_not_historical_binder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "user_data"
    root.mkdir()
    (root / REHEARSAL_SENTINEL_NAME).write_bytes(REHEARSAL_SENTINEL_CONTENT)
    source = root / "owner" / "notion" / "notion.db"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-placeholder")
    store = WorkspaceStore(catalog_path_for_user_data_root(root))
    workspace_id = store.create_workspace(
        "reviewer", name="Proof actor split", operation_id="create-proof-actor"
    )["workspace"]["id"]
    catalog = Path(store.db_path)
    digest = "sha256:" + "a" * 64
    captured_actors: list[str] = []

    monkeypatch.setattr(
        orchestration,
        "_catalog_binding",
        lambda *_args, **_kwargs: {
            "workspace_version": 7,
            "binding_version": 3,
            "binding_state": "active",
            "bound_by_subject_id": "reviewer",
            "proof_actor_subject_id": "owner",
            "source_revision": 2,
            "source_updated_at": 102,
            "source_sha256": digest,
        },
    )

    def capture_snapshot(**kwargs):
        captured_actors.append(kwargs["source_actor_subject_id"])
        return (
            LegacyPageSourceProof(
                actor_subject_id="owner",
                owner_subject_id="owner",
                page_id="page-1",
                revision=2,
                updated_at=102,
                content_sha256=digest,
            ),
            object(),
            None,
        )

    monkeypatch.setattr(
        orchestration, "_capture_one_source_snapshot", capture_snapshot
    )
    candidate = orchestration.capture_isolated_page_incarnation_candidate(
        store,
        workspace_id=workspace_id,
        rehearsal_root=root,
        user_data_root=root,
        source_owner_id="owner",
        source_page_id="page-1",
        expected_source_db_path=source,
        expected_catalog_db_path=catalog,
        expected_binding_version=3,
        source_quiesced_confirmed=True,
    )

    assert captured_actors == ["owner"]
    assert candidate.source_proof.actor_subject_id == "owner"


def test_top_level_rebind_is_owner_canonical_and_replays_before_all_path_io(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    quarantined = _quarantine_prepared(prepared)
    source_snapshots = 0
    real_capture = orchestration._capture_one_source_snapshot

    def counted_capture(**kwargs):
        nonlocal source_snapshots
        source_snapshots += 1
        assert kwargs["source_actor_subject_id"] == "owner"
        assert kwargs["binding_state"] == "active"
        assert kwargs["binding_version"] == quarantined["binding"]["version"] + 1
        return real_capture(**kwargs)

    monkeypatch.setattr(orchestration, "_capture_one_source_snapshot", counted_capture)
    args = _rebind_args(prepared, quarantined)
    first = orchestration.run_isolated_page_incarnation_rebind(
        prepared["store"], **args
    )
    assert source_snapshots == 2
    assert first == {
        "contract": orchestration.OFFLINE_INCARNATION_REBIND_REPORT_CONTRACT,
        "offline_only": True,
        "committed": True,
        "ledger_replay": False,
        "source_io_bypassed": False,
        "source_snapshot_revalidated": True,
        "binding_state": "active",
        "locator_projection": {
            "present": True,
            "changed": True,
            "file_source_count": 1,
            "public_source_count": 1,
        },
        "previous_binding_state": "quarantined",
    }
    encoded = json.dumps(first, sort_keys=True)
    for secret in (
        "reviewer",
        "owner",
        str(prepared["workspace_id"]),
        str(prepared["page_id"]),
        str(prepared["file_id"]),
        str(prepared["public_token"]),
        "inc_",
        "sha256:",
        str(prepared["root"]),
    ):
        assert secret not in encoded

    rebound_capture = orchestration.capture_isolated_page_incarnation_candidate(
        prepared["store"],
        **{
            **_capture_args(prepared),
            "expected_binding_version": quarantined["binding"]["version"] + 1,
        },
    )
    assert rebound_capture.source_proof.actor_subject_id == "owner"
    revalidation_args = _capture_args(prepared)
    revalidation_args.pop("expected_binding_version")
    assert orchestration.revalidate_isolated_page_incarnation_candidate(
        rebound_capture, prepared["store"], **revalidation_args
    ) is True
    assert source_snapshots == 4
    assert prepared["store"].verify_deep_health()["healthy"] is True

    monkeypatch.setattr(
        orchestration,
        "_rehearsal_root",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("rebind replay accessed the sentinel")
        ),
    )
    replay = orchestration.run_isolated_page_incarnation_rebind(
        prepared["store"],
        **{
            **args,
            "rehearsal_root": Path("Z:/missing"),
            "user_data_root": Path("Z:/missing"),
            "expected_source_db_path": Path("Z:/missing/source.db"),
            "expected_catalog_db_path": Path("Z:/missing/catalog.db"),
            "source_quiesced_confirmed": False,
        },
    )
    assert source_snapshots == 4
    assert replay == {
        **first,
        "ledger_replay": True,
        "source_io_bypassed": True,
        "source_snapshot_revalidated": False,
    }
    assert prepared["store"].incarnation_attestation_readiness(
        "reviewer", prepared["workspace_id"]
    )["ready_for_resolver_cutover"] is True


def test_rebind_refuses_quiescence_and_outsider_before_sentinel_or_source_io(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    quarantined = _quarantine_prepared(prepared)
    sentinel_calls = 0
    source_calls = 0

    def sentinel_must_not_run(_path):
        nonlocal sentinel_calls
        sentinel_calls += 1
        raise AssertionError("rebind accessed sentinel")

    def source_must_not_run(**_kwargs):
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("rebind accessed source")

    monkeypatch.setattr(orchestration, "_rehearsal_root", sentinel_must_not_run)
    monkeypatch.setattr(
        orchestration, "_capture_one_source_snapshot", source_must_not_run
    )
    args = _rebind_args(prepared, quarantined)
    with pytest.raises(
        orchestration.WorkspaceIncarnationOrchestrationRefused,
        match="quiescence",
    ):
        orchestration.run_isolated_page_incarnation_rebind(
            prepared["store"], **{**args, "source_quiesced_confirmed": False}
        )
    with pytest.raises((PermissionError, FileNotFoundError)):
        orchestration.run_isolated_page_incarnation_rebind(
            prepared["store"], **{**args, "reviewer_subject_id": "outsider"}
        )
    assert sentinel_calls == source_calls == 0


def test_rebind_source_tuple_drift_leaves_quarantine_and_ledger_unchanged(
    prepared: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    quarantined = _quarantine_prepared(prepared)
    real_capture = orchestration._capture_one_source_snapshot
    captures = 0

    def drifting_capture(**kwargs):
        nonlocal captures
        captures += 1
        proof, evidence, projection = real_capture(**kwargs)
        if captures == 2:
            proof = LegacyPageSourceProof(
                actor_subject_id=proof.actor_subject_id,
                owner_subject_id=proof.owner_subject_id,
                page_id=proof.page_id,
                revision=proof.revision + 1,
                updated_at=proof.updated_at,
                content_sha256=proof.content_sha256,
            )
        return proof, evidence, projection

    monkeypatch.setattr(
        orchestration, "_capture_one_source_snapshot", drifting_capture
    )
    with pytest.raises(ValueError, match="invalid or stale"):
        orchestration.run_isolated_page_incarnation_rebind(
            prepared["store"], **_rebind_args(prepared, quarantined)
        )
    assert captures == 2

    con = prepared["store"]._read_connection()
    try:
        binding = con.execute(
            "SELECT state,row_version FROM workspace_page_sources"
        ).fetchone()
        operation = con.execute(
            "SELECT 1 FROM workspace_operations WHERE operation_id=?",
            ("offline-rebind-source-page",),
        ).fetchone()
    finally:
        con.close()
    assert (binding["state"], binding["row_version"]) == (
        "quarantined",
        quarantined["binding"]["version"],
    )
    assert operation is None
