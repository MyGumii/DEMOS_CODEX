"""REG-002 characterization for legacy Notion publication and job polling.

These tests record compatibility behavior at the P0 boundary.  In particular,
reopening a legacy job record proves only that its JSON metadata is pollable.
The process-local callable, claim ownership, and restart recovery are not
durable, and the stale nonterminal status asserted here is not a desired future
worker contract.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1 import auth
from code_assist_v1 import job_manager as legacy_jobs
from code_assist_v1 import notion_store as notion
from code_assist_v1 import second_brain_store as legacy_second_brain
from code_assist_v1 import routes_notion
from code_assist_v1 import routes_notion_bridge


_PUBLISH_ADAPTERS = (
    pytest.param(
        routes_notion,
        routes_notion.register_notion_routes,
        "/api/notion/pages/{page_id}/publish_second_brain",
        id="full-notion-app",
    ),
    pytest.param(
        routes_notion_bridge,
        routes_notion_bridge.register_notion_bridge_routes,
        "/api/code/notion/pages/{page_id}/publish_second_brain",
        id="second-brain-bridge",
    ),
)

_ORIGIN = "https://agent.example.test"
_SAME_ORIGIN_HEADERS = {
    "Origin": _ORIGIN,
}


@pytest.fixture(autouse=True)
def _restore_second_brain_process_cache():
    previous = dict(legacy_second_brain._USER_CACHE)
    failed_logins = {key: list(value) for key, value in auth._FAILED_LOGINS.items()}
    legacy_second_brain._USER_CACHE.clear()
    yield
    legacy_second_brain._USER_CACHE.clear()
    legacy_second_brain._USER_CACHE.update(previous)
    auth._FAILED_LOGINS.clear()
    auth._FAILED_LOGINS.update(failed_logins)


@pytest.mark.regression
@pytest.mark.scenario("REG-002")
@pytest.mark.parametrize("route_module,register_routes,path_template", _PUBLISH_ADAPTERS)
def test_accessible_notion_page_publish_adapters_forward_current_metadata_and_response(
    monkeypatch: pytest.MonkeyPatch,
    isolated_data_root: Path,
    isolated_notion_dir: Path,
    route_module: Any,
    register_routes: Any,
    path_template: str,
) -> None:
    """Both current routes export an editable shared page into the same importer."""

    adapter_id = route_module.__name__.rsplit(".", 1)[-1].replace("_", "-")
    actor = f"notion-publisher-{adapter_id}-{uuid4().hex[:8]}"
    owner = "notion-owner"
    title = "Accessible roadmap"
    auth.create_user(
        actor,
        "Correct-Horse-Notion-42",
        display_name="Publisher",
        role="user",
    )
    user_data_dir = isolated_notion_dir.parents[1]
    monkeypatch.setattr(notion, "USER_DATA_DIR", str(user_data_dir))

    created = notion.create_page(owner, title=title)
    page_id = created["page"]["id"]
    notion.replace_blocks(
        owner,
        page_id,
        [{"type": "paragraph", "content": "Shared release checklist", "props": {}}],
    )
    notion.share_page(owner, page_id, actor, role="edit")

    real_export = notion.export_page_markdown
    real_import = route_module.sb.import_source_markdown
    export_calls: list[dict[str, Any]] = []
    exported_result: dict[str, Any] = {}

    def export_spy(
        user_id: str | None,
        exported_page_id: str,
        *,
        include_assets: bool = False,
    ) -> dict[str, Any]:
        export_calls.append(
            {
                "user_id": user_id,
                "page_id": exported_page_id,
                "include_assets": include_assets,
            }
        )
        result = real_export(user_id, exported_page_id, include_assets=include_assets)
        exported_result.update(result)
        return result

    import_calls: list[dict[str, Any]] = []
    import_results: list[dict[str, Any]] = []
    expected_metadata = {"page_id": page_id, "owner_user_id": owner, "title": title}

    def import_spy(**kwargs: Any) -> dict[str, Any]:
        import_calls.append(kwargs)
        result = real_import(**kwargs)
        import_results.append(result)
        return result

    def deterministic_wiki_conversion(*args: Any, **kwargs: Any):
        del kwargs
        return args[1], {
            "converted": False,
            "error": "",
            "skipped": True,
            "model_kind": "",
            "model_used": "",
            "fallback_errors": [],
        }

    lock_calls: list[tuple[str, str | None, tuple[object, ...]]] = []

    @contextmanager
    def isolated_write_lock(scope: str, user_id: str | None, *parts: object):
        # Avoid adding a persistent key to concurrency._LOCKS during characterization.
        lock_calls.append((scope, user_id, parts))
        yield

    monkeypatch.setattr(route_module.ns, "export_page_markdown", export_spy)
    monkeypatch.setattr(route_module.sb, "_to_wiki_markdown", deterministic_wiki_conversion)
    monkeypatch.setattr(
        route_module.sb,
        "_analysis_for_import",
        lambda *args, **kwargs: {"edges": []},
    )
    monkeypatch.setattr(route_module.sb, "import_source_markdown", import_spy)
    monkeypatch.setattr(route_module, "user_write_lock", isolated_write_lock)

    app = Flask(f"notion-publication-{route_module.__name__}")
    app.config.update(
        TESTING=True,
        SECRET_KEY="notion-publication-characterization",
        CODE_ASSIST_ALLOWED_ORIGINS=(_ORIGIN,),
    )
    auth.register_auth_routes(app)
    register_routes(app)
    client = app.test_client()
    login = client.post(
        "/api/code/auth/login",
        base_url=_ORIGIN,
        json={"user_id": actor, "password": "Correct-Horse-Notion-42"},
        headers=_SAME_ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    csrf_token = login.get_json().get("csrf_token")
    assert isinstance(csrf_token, str) and csrf_token

    response = client.post(
        path_template.format(page_id=page_id),
        base_url=_ORIGIN,
        headers={**_SAME_ORIGIN_HEADERS, "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert len(import_results) == 1
    assert response.get_json() == {"status": "published", **import_results[0]}
    assert export_calls == [
        {"user_id": actor, "page_id": page_id, "include_assets": True}
    ]
    assert exported_result["page"]["can_edit"] is True
    assert "Shared release checklist" in exported_result["markdown"]
    assert lock_calls == [("second_brain", actor, ())]
    assert import_calls == [
        {
            "filename": f"notion-{title}-{page_id}.md",
            "markdown": exported_result["markdown"],
            "user_id": actor,
            "images": exported_result.get("images") or [],
            "source_type": "notion",
            "source_metadata": expected_metadata,
        }
    ]
    imported_path = Path(import_results[0]["raw_path"]).resolve()
    assert imported_path.is_relative_to(isolated_data_root.resolve())
    assert imported_path.is_file()
    assert "Shared release checklist" in imported_path.read_text(encoding="utf-8")
    assert import_results[0]["source_type"] == "notion"
    assert import_results[0]["source_metadata"] == expected_metadata


@pytest.mark.regression
def test_job_manager_restart_reopens_nonterminal_metadata_without_recovery(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout: Any,
) -> None:
    """A fresh manager can poll stale JSON, but cannot recover its callable.

    This deliberately does not claim JOB-001/JOB-002 acceptance.  The reopened
    ``running`` value is legacy metadata left by a prior process, not a lease,
    reclaim decision, executable registration, or safe restart guarantee.
    """

    monkeypatch.setattr(
        legacy_jobs,
        "USER_DATA_DIR",
        str(isolated_knowledge_layout.user_data_dir),
    )
    original = legacy_jobs.JobManager()
    reopened = None
    try:
        created = original.create_job(
            user_id="job-owner",
            kind="notion_publish",
            session_id="session-characterization",
            metadata={"page_id": "page-characterization", "source": "notion"},
        )
        persisted = original.update_job(
            created["job_id"],
            status="running",
            progress=37,
            message="exporting",
        )
        reopened = legacy_jobs.JobManager()

        # The second manager starts with no in-memory job or executable Future.
        assert reopened._jobs == {}
        assert reopened._futures == {}

        polled = reopened.get_job(created["job_id"], user_id="job-owner")

        assert polled == persisted
        assert polled is not None
        assert polled["status"] == "running"
        assert polled["progress"] == 37
        assert polled["metadata"] == {
            "page_id": "page-characterization",
            "source": "notion",
        }
        # Loading the JSON never recreates, claims, or schedules the lost callable.
        assert created["job_id"] not in reopened._futures
    finally:
        # Each characterized manager owns an executor even though this test never
        # submits work.  Close both so no executor state leaks into later tests.
        if reopened is not None:
            reopened._executor.shutdown(wait=True, cancel_futures=True)
        original._executor.shutdown(wait=True, cancel_futures=True)
