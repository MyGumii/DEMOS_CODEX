from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from code_assist_v1 import notion_publication
from code_assist_v1 import routes_notion
from code_assist_v1 import routes_notion_bridge


class _SourceStore:
    def __init__(self, export: dict[str, Any], file_paths: dict[str, Path] | None = None):
        self.export = export
        self.file_paths = file_paths or {}
        self.export_calls: list[tuple[str, str, bool]] = []
        self.file_calls: list[tuple[str, str]] = []

    def export_page_markdown(
        self,
        user_id: str,
        page_id: str,
        *,
        include_assets: bool = False,
    ) -> dict[str, Any]:
        self.export_calls.append((user_id, page_id, include_assets))
        return self.export

    def get_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        self.file_calls.append((user_id, file_id))
        path = self.file_paths.get(file_id)
        if path is None:
            raise FileNotFoundError(file_id)
        return {"id": file_id, "path": str(path)}


class _TargetStore:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def import_source_markdown(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"source_id": "source-1", "thought_id": "thought-1"}


def _page_export(*, can_edit: bool = True) -> dict[str, Any]:
    return {
        "page": {
            "id": "page-1",
            "owner_user_id": "owner-1",
            "title": "Roadmap",
            "revision": 7,
            "updated_at": 12345,
            "can_edit": can_edit,
        },
        "markdown": "# Roadmap\n\nBody\n",
        "files": [],
        "images": [{"filename": "diagram.png", "raw": b"image-bytes"}],
    }


@pytest.mark.unit
def test_snapshot_is_content_addressed_deterministic_and_frozen(tmp_path: Path) -> None:
    attachment = tmp_path / "attachment.pdf"
    attachment.write_bytes(b"pdf-body")
    exported = _page_export()
    exported["files"] = [
        {
            "id": "file-1",
            "original_name": "attachment.pdf",
            "mime_type": "application/pdf",
            "size": 999,
        },
        {
            "id": "missing-file",
            "original_name": "missing.txt",
            "mime_type": "text/plain",
            "size": 10,
        },
    ]
    source = _SourceStore(exported, {"file-1": attachment})

    first = notion_publication.capture_page_source_snapshot(
        "actor-1",
        "page-1",
        source_store=source,
        captured_at=100,
    )
    second = notion_publication.capture_page_source_snapshot(
        "actor-1",
        "page-1",
        source_store=source,
        captured_at=200,
    )

    assert first.source_snapshot_sha256 == second.source_snapshot_sha256
    assert first.asset_manifest_sha256 == second.asset_manifest_sha256
    assert first.content_sha256 == hashlib.sha256(exported["markdown"].encode()).hexdigest()
    assert first.captured_at == 100
    assert first.revision == 7
    assert first.updated_at == 12345
    assert first.descriptor()["asset_count"] == 3
    file_asset = next(item for item in first.assets if item.file_id == "file-1")
    assert file_asset.available is True
    assert file_asset.size_bytes == len(b"pdf-body")
    assert file_asset.content_sha256 == hashlib.sha256(b"pdf-body").hexdigest()
    missing = next(item for item in first.assets if item.file_id == "missing-file")
    assert missing.available is False
    assert missing.content_sha256 is None
    assert source.export_calls == [
        ("actor-1", "page-1", True),
        ("actor-1", "page-1", True),
    ]
    with pytest.raises(FrozenInstanceError):
        first.title = "Changed"  # type: ignore[misc]


@pytest.mark.unit
def test_asset_change_changes_snapshot_identity(tmp_path: Path) -> None:
    attachment = tmp_path / "attachment.bin"
    attachment.write_bytes(b"first")
    exported = _page_export()
    exported["files"] = [{"id": "file-1", "original_name": "attachment.bin"}]
    source = _SourceStore(exported, {"file-1": attachment})

    first = notion_publication.capture_page_source_snapshot(
        "actor-1", "page-1", source_store=source, captured_at=1
    )
    attachment.write_bytes(b"second")
    second = notion_publication.capture_page_source_snapshot(
        "actor-1", "page-1", source_store=source, captured_at=2
    )

    assert first.content_sha256 == second.content_sha256
    assert first.asset_manifest_sha256 != second.asset_manifest_sha256
    assert first.source_snapshot_sha256 != second.source_snapshot_sha256


@pytest.mark.unit
def test_legacy_publication_keeps_filename_metadata_images_and_lock_contract() -> None:
    exported = _page_export()
    source = _SourceStore(exported)
    target = _TargetStore()
    lock_calls: list[tuple[str, str]] = []

    @contextmanager
    def write_lock(scope: str, user_id: str):
        lock_calls.append((scope, user_id))
        yield

    snapshot, result = notion_publication.publish_page_to_legacy_second_brain(
        "actor-1",
        "page-1",
        source_store=source,
        target_store=target,
        write_lock=write_lock,
    )

    assert result == {"source_id": "source-1", "thought_id": "thought-1"}
    assert lock_calls == [("second_brain", "actor-1")]
    assert target.calls == [
        {
            "filename": "notion-Roadmap-page-1.md",
            "markdown": exported["markdown"],
            "user_id": "actor-1",
            "images": exported["images"],
            "source_type": "notion",
            "source_metadata": {
                "page_id": "page-1",
                "owner_user_id": "owner-1",
                "title": "Roadmap",
            },
        }
    ]
    assert snapshot.page_payload() == exported["page"]


@pytest.mark.unit
def test_publication_rechecks_edit_capability_before_target_side_effect() -> None:
    source = _SourceStore(_page_export(can_edit=False))
    target = _TargetStore()
    lock_entered = False

    @contextmanager
    def write_lock(scope: str, user_id: str):
        nonlocal lock_entered
        lock_entered = True
        yield

    with pytest.raises(PermissionError, match="edit permission required"):
        notion_publication.publish_page_to_legacy_second_brain(
            "actor-1",
            "page-1",
            source_store=source,
            target_store=target,
            write_lock=write_lock,
        )

    assert lock_entered is False
    assert target.calls == []


@pytest.mark.unit
def test_approved_snapshot_mismatch_stops_before_lock_and_target_side_effect() -> None:
    source = _SourceStore(_page_export())
    target = _TargetStore()
    lock_entered = False

    @contextmanager
    def write_lock(scope: str, user_id: str):
        nonlocal lock_entered
        lock_entered = True
        yield

    with pytest.raises(notion_publication.PageSourceSnapshotConflict) as raised:
        notion_publication.publish_page_to_legacy_second_brain(
            "actor-1",
            "page-1",
            source_store=source,
            target_store=target,
            write_lock=write_lock,
            expected_source_snapshot_sha256="0" * 64,
        )

    assert raised.value.expected_sha256 == "0" * 64
    assert raised.value.snapshot.page_id == "page-1"
    assert raised.value.snapshot.source_snapshot_sha256 != "0" * 64
    assert lock_entered is False
    assert target.calls == []


@pytest.mark.unit
def test_malformed_snapshot_precondition_is_rejected_before_source_read() -> None:
    source = _SourceStore(_page_export())
    target = _TargetStore()

    @contextmanager
    def write_lock(scope: str, user_id: str):
        yield

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        notion_publication.publish_page_to_legacy_second_brain(
            "actor-1",
            "page-1",
            source_store=source,
            target_store=target,
            write_lock=write_lock,
            expected_source_snapshot_sha256="NOT-A-DIGEST",
        )

    assert source.export_calls == []
    assert target.calls == []


@pytest.mark.regression
def test_publish_preview_preserves_payload_and_adds_snapshot_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exported = _page_export()
    export_calls: list[tuple[str, str, bool]] = []

    def export_page(
        user_id: str,
        page_id: str,
        *,
        include_assets: bool = False,
    ) -> dict[str, Any]:
        export_calls.append((user_id, page_id, include_assets))
        return exported

    monkeypatch.setattr(routes_notion, "_uid", lambda: "actor-1")
    monkeypatch.setattr(routes_notion.ns, "export_page_markdown", export_page)
    app = Flask("notion-publication-preview")
    app.config.update(TESTING=True, SECRET_KEY="preview-test")
    routes_notion.register_notion_routes(app)

    response = app.test_client().get(
        "/api/notion/pages/page-1/publish_preview"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["page"] == exported["page"]
    assert payload["markdown"] == exported["markdown"]
    assert payload["image_count"] == 1
    assert payload["file_count"] == 0
    assert payload["source_snapshot"] == {
        "schema_version": 1,
        "page_id": "page-1",
        "owner_user_id": "owner-1",
        "revision": 7,
        "updated_at": 12345,
        "content_sha256": hashlib.sha256(exported["markdown"].encode()).hexdigest(),
        "asset_manifest_sha256": payload["source_snapshot"]["asset_manifest_sha256"],
        "source_snapshot_sha256": payload["source_snapshot"]["source_snapshot_sha256"],
        "asset_count": 1,
    }
    assert len(payload["source_snapshot"]["asset_manifest_sha256"]) == 64
    assert len(payload["source_snapshot"]["source_snapshot_sha256"]) == 64
    assert export_calls == [("actor-1", "page-1", True)]


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/pages/page-1/publish_second_brain",
            id="notion-workspace",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/pages/page-1/publish_second_brain",
            id="second-brain-bridge",
        ),
    ),
)
def test_publish_route_returns_stable_409_when_preview_snapshot_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
) -> None:
    exported = _page_export()
    import_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(route_module, "_uid", lambda: "actor-1")
    monkeypatch.setattr(
        route_module.ns,
        "export_page_markdown",
        lambda user_id, page_id, *, include_assets=False: exported,
    )
    monkeypatch.setattr(
        route_module.sb,
        "import_source_markdown",
        lambda **kwargs: import_calls.append(kwargs),
    )
    app = Flask(f"notion-publication-conflict-{route_module.__name__}")
    app.config.update(TESTING=True, SECRET_KEY="conflict-test")
    register_routes(app)

    response = app.test_client().post(
        path,
        json={"expected_source_snapshot_sha256": "0" * 64},
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"] == "source_snapshot_conflict"
    assert payload["expected_source_snapshot_sha256"] == "0" * 64
    assert payload["current_source_snapshot"]["page_id"] == "page-1"
    assert len(payload["current_source_snapshot"]["source_snapshot_sha256"]) == 64
    assert import_calls == []


@pytest.mark.regression
@pytest.mark.parametrize(
    "route_module,register_routes,path",
    (
        pytest.param(
            routes_notion,
            routes_notion.register_notion_routes,
            "/api/notion/pages/page-1/publish_second_brain",
            id="notion-workspace",
        ),
        pytest.param(
            routes_notion_bridge,
            routes_notion_bridge.register_notion_bridge_routes,
            "/api/code/notion/pages/page-1/publish_second_brain",
            id="second-brain-bridge",
        ),
    ),
)
def test_both_existing_publish_routes_delegate_to_one_shared_boundary(
    monkeypatch: pytest.MonkeyPatch,
    route_module: Any,
    register_routes: Any,
    path: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def shared_publication(actor_user_id: str, page_id: str, **kwargs: Any):
        calls.append(
            {
                "actor_user_id": actor_user_id,
                "expected_source_snapshot_sha256": kwargs[
                    "expected_source_snapshot_sha256"
                ],
                "page_id": page_id,
                "source_store": kwargs["source_store"],
                "target_store": kwargs["target_store"],
                "write_lock": kwargs["write_lock"],
            }
        )
        return object(), {"source_id": "shared-source", "thought_id": "shared-thought"}

    monkeypatch.setattr(route_module, "_uid", lambda: "actor-1")
    monkeypatch.setattr(
        notion_publication,
        "publish_page_to_legacy_second_brain",
        shared_publication,
    )
    app = Flask(f"shared-notion-publication-{route_module.__name__}")
    app.config.update(TESTING=True, SECRET_KEY="shared-publication-test")
    register_routes(app)
    expected = "a" * 64
    response = app.test_client().post(
        path,
        json={"expected_source_snapshot_sha256": expected},
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "source_id": "shared-source",
        "status": "published",
        "thought_id": "shared-thought",
    }
    assert calls == [
        {
            "actor_user_id": "actor-1",
            "expected_source_snapshot_sha256": expected,
            "page_id": "page-1",
            "source_store": route_module.ns,
            "target_store": route_module.sb,
            "write_lock": route_module.user_write_lock,
        }
    ]
