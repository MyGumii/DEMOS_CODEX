from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1 import notion_store
from code_assist_v1 import notion_workspace_reconciliation as reconciliation
from code_assist_v1.notion_workspace_projection import empty_shadow_counts
from code_assist_v1.notion_workspace_projection import project_exact_owner_page
from code_assist_v1.notion_workspace_store import (
    WorkspaceStore,
    catalog_path_for_user_data_root,
)


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    page_count: int,
) -> dict[str, Any]:
    monkeypatch.setattr(notion_store, "USER_DATA_DIR", str(tmp_path))
    store = WorkspaceStore(catalog_path_for_user_data_root(tmp_path))
    workspace = store.create_workspace(
        "owner",
        name="RECONCILIATION TENANT CANARY",
        operation_id="reconciliation-create-workspace",
    )["workspace"]
    pages: list[dict[str, Any]] = []
    tokens: list[str] = []
    version = int(workspace["version"])
    last_bound: dict[str, Any] | None = None
    for index in range(page_count):
        page = notion_store.create_page(
            "owner",
            title=f"RECONCILIATION PRIVATE TITLE {index}",
        )["page"]
        notion_store.upload_file(
            "owner",
            page["id"],
            filename=f"reconciliation-secret-path-{index}.txt",
            raw=f"private-body-{index}".encode("utf-8"),
        )
        public = notion_store.enable_public_link("owner", page["id"])
        tokens.append(str(public["token"]))
        proof = notion_store.capture_legacy_page_source_proof(
            "owner", "owner", page["id"]
        )
        last_bound = store.bind_page_source(
            "owner",
            workspace["id"],
            proof,
            source_revalidator=notion_store.revalidate_legacy_page_source_proof,
            base_version=version,
            operation_id=f"reconciliation-bind-{index}",
        )
        version = int(last_bound["workspace"]["version"])
        pages.append(page)
    return {
        "store": store,
        "workspace": workspace,
        "workspace_version": version,
        "pages": pages,
        "tokens": tokens,
        "last_bound": last_bound,
        "catalog": Path(store.db_path),
        "source": tmp_path / "owner" / "notion" / "notion.db",
    }


def _run(fixture: dict[str, Any], **overrides: Any):
    arguments: dict[str, Any] = {
        "actor_subject_id": "owner",
        "workspace_id": fixture["workspace"]["id"],
        "user_data_root": fixture["catalog"].parents[1],
        "expected_catalog_db_path": fixture["catalog"],
        "source_db_for_owner": lambda owner: fixture["source"],
        "source_quiesced_confirmed": True,
    }
    arguments.update(overrides)
    return reconciliation.reconcile_active_page_binding_batch(
        fixture["store"], **arguments
    )


def test_quiescence_denial_precedes_catalog_scan_and_source_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / ".notion-workspace-v2" / "workspace.db"
    store = WorkspaceStore(catalog)
    calls = {"scan": 0, "source": 0}

    def fail_scan(*_args: Any, **_kwargs: Any):
        calls["scan"] += 1
        raise AssertionError("catalog scan must not run")

    def fail_source(_owner: str):
        calls["source"] += 1
        raise AssertionError("source resolver must not run")

    monkeypatch.setattr(
        WorkspaceStore,
        "scan_active_page_source_bindings",
        fail_scan,
        raising=False,
    )
    with pytest.raises(
        reconciliation.WorkspaceReconciliationError,
        match="source_quiesced_confirmed",
    ):
        reconciliation.reconcile_active_page_binding_batch(
            store,
            actor_subject_id="owner",
            workspace_id="ws_quiescence_probe",
            user_data_root=tmp_path,
            expected_catalog_db_path=catalog,
            source_db_for_owner=fail_source,
            source_quiesced_confirmed=False,
            limit=1,
        )

    assert calls == {"scan": 0, "source": 0}
    assert not catalog.exists()


def test_bounded_exact_owner_resolution_report_is_identifier_free_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch, page_count=3)
    before_counts = fixture["store"].audit_counts()
    resolver_calls: list[str] = []
    reconcile_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        notion_store,
        "_known_workspace_users",
        lambda: (_ for _ in ()).throw(
            AssertionError("reconciliation must not enumerate owner directories")
        ),
    )

    def exact_source(owner: str) -> Path:
        resolver_calls.append(owner)
        assert owner == "owner"
        return fixture["source"]

    def safe_miss(**kwargs: Any) -> dict[str, int]:
        reconcile_calls.append(dict(kwargs))
        assert kwargs["source_owner_id"] == "owner"
        assert Path(kwargs["expected_source_db_path"]) == fixture["source"]
        assert Path(kwargs["expected_catalog_db_path"]) == fixture["catalog"]
        assert isinstance(kwargs["expected_binding_version"], int)
        return {**empty_shadow_counts(), "safe_miss": 1}

    monkeypatch.setattr(
        reconciliation,
        "shadow_reconcile_exact_owner_page",
        safe_miss,
    )

    first = _run(
        fixture,
        source_db_for_owner=exact_source,
        limit=2,
    )
    assert first.report["scanned"] == 2
    assert first.report["visited"] == 2
    assert first.report["safe_miss"] == 2
    assert first.has_more is True
    assert first.next_cursor is not None
    assert resolver_calls == ["owner", "owner"]
    assert len(reconcile_calls) == 2

    second = _run(
        fixture,
        source_db_for_owner=exact_source,
        after=first.next_cursor,
        limit=2,
    )
    assert second.report["scanned"] == 1
    assert second.report["visited"] == 1
    assert second.report["safe_miss"] == 1
    assert second.has_more is False
    assert second.next_cursor is None
    assert resolver_calls == ["owner", "owner", "owner"]
    assert len(reconcile_calls) == 3
    assert fixture["store"].audit_counts() == before_counts

    reports = json.dumps(
        {"first": first.report, "second": second.report},
        sort_keys=True,
    )
    forbidden = {
        "owner",
        str(fixture["workspace"]["id"]),
        str(fixture["source"]),
        "RECONCILIATION TENANT CANARY",
        "RECONCILIATION PRIVATE TITLE",
        "reconciliation-secret-path",
        "private-body",
        *(str(page["id"]) for page in fixture["pages"]),
        *fixture["tokens"],
    }
    assert all(value not in reports for value in forbidden)
    assert set(first.report) == {"scanned", "visited", *empty_shadow_counts()}
    assert all(type(value) is int for value in first.report.values())


def test_stale_scanned_generation_is_terminal_before_source_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch, page_count=1)
    stale_batch = fixture["store"].scan_active_page_source_bindings(
        "owner",
        fixture["workspace"]["id"],
        limit=1,
    )
    assert len(stale_batch.items) == 1

    page = fixture["pages"][0]
    proof = notion_store.capture_legacy_page_source_proof(
        "owner", "owner", page["id"]
    )
    staged = fixture["store"].stage_page_source_binding(
        "owner",
        fixture["workspace"]["id"],
        proof,
        base_version=fixture["workspace_version"],
        operation_id="reconciliation-restage-generation",
    )
    fixture["store"].activate_page_source_binding(
        "owner",
        fixture["workspace"]["id"],
        proof,
        source_revalidator=notion_store.revalidate_legacy_page_source_proof,
        expected_binding_version=staged["binding"]["version"],
        base_version=staged["workspace"]["version"],
        operation_id="reconciliation-activate-generation",
    )
    baseline = fixture["store"].audit_counts()
    calls = {"source": 0, "shadow": 0}

    monkeypatch.setattr(
        fixture["store"],
        "scan_active_page_source_bindings",
        lambda *_args, **_kwargs: stale_batch,
    )

    def source_must_not_resolve(_owner: str) -> Path:
        calls["source"] += 1
        raise AssertionError("superseded generation must not resolve its source")

    def shadow_must_not_run(**_kwargs: Any) -> dict[str, int]:
        calls["shadow"] += 1
        raise AssertionError("superseded generation must not enter shadow reconciliation")

    monkeypatch.setattr(
        reconciliation,
        "shadow_reconcile_exact_owner_page",
        shadow_must_not_run,
    )
    result = _run(
        fixture,
        source_db_for_owner=source_must_not_resolve,
        limit=1,
    )

    assert calls == {"source": 0, "shadow": 0}
    assert result.report["scanned"] == 1
    assert result.report["visited"] == 1
    assert result.report["superseded"] == 1
    assert sum(result.report[state] for state in empty_shadow_counts()) == 1
    assert fixture["store"].audit_counts() == baseline


def test_real_bounded_shadow_batch_distinguishes_match_and_safe_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch, page_count=2)
    page = fixture["pages"][0]
    binding = next(
        item
        for item in fixture["store"].scan_active_page_source_bindings(
            "owner", fixture["workspace"]["id"], limit=2
        ).items
        if item.source_page_id == page["id"]
    )
    project_exact_owner_page(
        user_data_root=tmp_path,
        source_owner_id="owner",
        actor_subject_id="owner",
        workspace_id=fixture["workspace"]["id"],
        source_page_id=page["id"],
        expected_source_db_path=fixture["source"],
        expected_catalog_db_path=fixture["catalog"],
        expected_binding_version=binding.binding_version,
        operation_id="reconciliation-real-projection",
        source_quiesced_confirmed=True,
    )
    before = fixture["store"].audit_counts()

    result = _run(fixture, limit=2)

    assert result.report["visited"] == 2
    assert result.report["match"] == 1
    assert result.report["safe_miss"] == 1
    assert sum(result.report[state] for state in empty_shadow_counts()) == 2
    assert fixture["store"].audit_counts() == before


def test_reconciler_import_does_not_cross_app_bridge_knowledge_or_second_brain(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "import-probe"
    probe.mkdir()
    before = tuple(probe.iterdir())
    environment = os.environ.copy()
    environment["CODE_ASSIST_DATA_ROOT"] = str(probe)
    environment["CODE_ASSIST_TEST_DATA_ROOT"] = str(probe)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    forbidden = (
        "code_assist_v1.app_code",
        "code_assist_v1.app_notion",
        "code_assist_v1.routes_notion_bridge",
        "code_assist_v1.routes_knowledge",
        "code_assist_v1.routes_second_brain",
        "code_assist_v1.second_brain_store",
        "code_assist_v1.notion_publication",
        "code_assist_v1.knowledge_core",
    )
    script = (
        "import json,sys; "
        "import code_assist_v1.notion_workspace_reconciliation; "
        f"names={forbidden!r}; "
        "print(json.dumps(sorted(name for name in names if name in sys.modules)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
    assert tuple(probe.iterdir()) == before
