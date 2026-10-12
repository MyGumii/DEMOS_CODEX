from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import multiprocessing
import os
import zipfile
from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1.feedback_store import FeedbackStore, FeedbackError
from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.services.management_reads import CanonicalManagementReadService
from code_assist_v1.knowledge_core.services.source_artifacts import SourceArtifactService, ItemSourceArtifactDownloadRequest, SourceArtifactStateConflictError
from code_assist_v1.knowledge_core.services.wiki_files import WikiFilesService
from code_assist_v1.knowledge_core.workspace_feature_routes import register_workspace_feature_routes
from code_assist_v1.tests.knowledge_spaces.unit.test_conversation_pins_contexts import _factory, _seed
from code_assist_v1.tools.full_second_brain_preview import _seed_published_preview_item
from code_assist_v1.knowledge_core.legacy.blob_stager import FilesystemLegacyBlobStager
from code_assist_v1.knowledge_core.migrations.storage_preflight import MigrationStorageLayout


pytestmark = pytest.mark.unit
OWNER = {"id": "author", "display_name": "작성자", "role": "user"}
OTHER = {"id": "other", "display_name": "다른 사용자", "role": "user"}
ADMIN = {"id": "admin", "display_name": "관리자", "role": "admin"}


def create_post(store):
    return store.mutate(OWNER, action="create", operation_id=str(uuid4()),
        title="첨부가 있는 요청", kind="bug", body="첫 줄\n둘째 줄",
        files=[("image.png", "image/png", b"png-content"), ("report.txt", "text/plain", b"report")])


def test_feedback_cross_user_edit_delete_persistence_and_attachment_revocation(tmp_path):
    path = tmp_path / "feedback.db"
    store = FeedbackStore(path)
    result = create_post(store)
    pid = result["id"]
    post = FeedbackStore(path).get(OTHER, pid)
    assert not post["can_edit"] and post["body"] == "첫 줄\n둘째 줄"
    assert len(post["attachments"]) == 2
    first, second = [a["id"] for a in post["attachments"]]
    assert store.download(OTHER, pid, second)["content"] == b"report"
    for actor in (OTHER, ADMIN):
        with pytest.raises(FeedbackError) as failure:
            store.mutate(actor, action="delete", operation_id=str(uuid4()), post_id=pid, expected_version=1)
        assert failure.value.http_status == 403
    edited = store.mutate(OWNER, action="edit", operation_id=str(uuid4()), post_id=pid,
        expected_version=1, title="수정", kind="improvement", body="변경된 내용", keep_ids=[second])
    assert edited["row_version"] == 2
    with pytest.raises(FeedbackError):
        store.download(OTHER, pid, first)
    with pytest.raises(FeedbackError) as conflict:
        store.mutate(OWNER, action="edit", operation_id=str(uuid4()), post_id=pid,
            expected_version=1, title="덮어쓰기", kind="bug", body="오래된 내용")
    assert conflict.value.http_status == 409
    store.mutate(ADMIN, action="status", post_id=pid, expected_version=2,
        operation_id=str(uuid4()), status="completed")
    assert store.get(OWNER, pid)["status"] == "completed"
    store.mutate(OWNER, action="delete", post_id=pid, expected_version=3, operation_id=str(uuid4()))
    assert store.list(OTHER)["items"] == []
    with pytest.raises(FeedbackError):
        store.download(OTHER, pid, second)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM feedback_history").fetchone()[0] == 4
        assert c.execute("SELECT COUNT(*) FROM feedback_post").fetchone()[0] == 1


def test_feedback_retries_do_not_duplicate_or_allow_foreign_attachments(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.db")
    args = dict(action="create", operation_id=str(uuid4()), title="제목", kind="bug", body="내용")
    one = store.mutate(OWNER, **args)
    assert store.mutate(OWNER, **args) == one
    with pytest.raises(FeedbackError):
        store.mutate(OWNER, **{**args, "body": "다른 내용"})
    another = create_post(store)
    foreign_asset = store.get(OWNER, another["id"])["attachments"][0]["id"]
    with pytest.raises(FeedbackError):
        store.mutate(OWNER, action="edit", operation_id=str(uuid4()), post_id=one["id"], expected_version=1,
            title="다른 글", kind="bug", body="내용", keep_ids=[foreign_asset])
    assert len(store.list(OTHER)["items"]) == 2


class Lease:
    def close(self):
        pass


@dataclass
class Observation:
    blob: StagedVerifiedBlob
    lease: Lease


class Stager:
    def __init__(self):
        self.content = {}

    def stage_and_verify(self, content, *, expected_sha256, media_type, effective_at_ms, **kwargs):
        assert sha256(content).hexdigest() == expected_sha256
        self.content[expected_sha256] = content
        return Observation(StagedVerifiedBlob(expected_sha256, len(content), media_type,
            f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}", effective_at_ms, effective_at_ms), Lease())

    def verify_existing(self, *, expected_sha256, expected_size, media_type, **kwargs):
        content = self.content[expected_sha256]
        assert len(content) == expected_size
        return Observation(StagedVerifiedBlob(expected_sha256, len(content), media_type,
            f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}", 1, 1), Lease()), content


@pytest.fixture
def wiki(tmp_path):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    c = factory.open()
    try:
        with transaction(c):
            c.execute("UPDATE knowledge_space SET write_enabled=1,row_version=row_version+1,updated_at=updated_at+1 WHERE id=?", (ids["member_space"],))
    finally:
        c.close()
    scopes = ScopeResolver(FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True))
    source = SourceArtifactService(factory, scope_resolver=scopes, stager=Stager())
    management = CanonicalManagementReadService(factory, scope_resolver=scopes)
    return factory, ids, source, management, WikiFilesService(source, management)


def test_original_link_preserves_body_revision_and_graph_and_is_idempotent(wiki):
    factory, ids, source, _, service = wiki
    principal = SessionPrincipal("member01", 1)
    space, iid, revision = (str(ids[k]) for k in ("member_space", "item_id", "revision_id"))
    c = factory.open(read_only=True)
    before = tuple(c.execute("SELECT current_revision_id,row_version FROM knowledge_item WHERE id=?", (iid,)).fetchone())
    edges = c.execute("SELECT COUNT(*) FROM knowledge_edge").fetchone()[0]
    c.close()
    args = dict(filename="복구 원본.txt", content="실제 원본 내용입니다.".encode(), operation_id=str(uuid4()), expected_revision_id=revision)
    result = service.attach(principal, space, iid, **args)
    assert result["status"] == "linked"
    assert service.attach(principal, space, iid, **args) == result
    duplicate = service.attach(principal, space, iid, **{**args, "operation_id": str(uuid4())})
    assert duplicate["status"] == "already_linked"
    downloaded = source.download(ItemSourceArtifactDownloadRequest(principal, space, iid, result["artifact_id"]))
    assert downloaded.content == args["content"]
    c = factory.open(read_only=True)
    assert tuple(c.execute("SELECT current_revision_id,row_version FROM knowledge_item WHERE id=?", (iid,)).fetchone()) == before
    assert c.execute("SELECT COUNT(*) FROM knowledge_edge").fetchone()[0] == edges
    assert c.execute("SELECT COUNT(*) FROM audit_event WHERE action='item.original_linked'").fetchone()[0] == 1
    c.close()
    with pytest.raises(Exception):
        service.attach(SessionPrincipal("other01", 1), space, iid, **{**args, "operation_id": str(uuid4())})


def test_complete_export_crosses_pages_avoids_name_collisions_and_foreign_items(wiki):
    factory, ids, _, _, service = wiki
    space = str(ids["member_space"])
    c = factory.open()
    try:
        with transaction(c):
            for number in range(112):
                _seed_published_preview_item(c, actor_user_id="member01", body=f"# 같은 제목\n문서 {number}\n",
                    label=f"export-{number}", space_id=space, title="같은 제목", timestamp_ms=1000+number)
    finally:
        c.close()
    principal = SessionPrincipal("member01", 1)
    output, name, media, size, count = service.export(principal, space)
    try:
        assert count == 113 and media == "application/zip" and name.endswith(".zip")
        with zipfile.ZipFile(output) as z:
            assert z.testzip() is None
            documents = [n for n in z.namelist() if n.endswith("/document.md")]
            assert len(documents) == len(set(documents)) == 113
            assert "index.md" in z.namelist() and "log.md" in z.namelist()
            assert str(ids["other_item_id"]) not in z.read("export.json").decode()
            assert "문서 111" in "".join(z.read(n).decode() for n in documents)
    finally:
        output.close()
    with pytest.raises(Exception):
        service.export(principal, str(ids["other_space"]))


def test_feature_http_routes_require_session_and_export_download_headers(wiki):
    _, ids, source, management, _ = wiki
    app = Flask(__name__)
    who = {"value": {"id": "member01", "scope_epoch": 1}}
    register_workspace_feature_routes(app, source, management, lambda: who["value"])
    registered = {(r.rule, m) for r in app.url_map.iter_rules()
                  if r.endpoint.startswith("workspace_features_")
                  for m in r.methods - {"OPTIONS", "HEAD"}}
    prefix = "/api/knowledge/v2/spaces/<space_id>"
    assert registered == {
        (prefix + "/markdown-export", "GET"),
        (prefix + "/source-availability", "POST"),
        (prefix + "/items/<item_id>/markdown-download", "GET"),
        (prefix + "/items/<item_id>/original-links", "POST"),
        ("/api/code/feedback", "GET"), ("/api/code/feedback", "POST"),
        ("/api/code/feedback/<post_id>", "GET"),
        ("/api/code/feedback/<post_id>", "PUT"),
        ("/api/code/feedback/<post_id>", "DELETE"),
        ("/api/code/feedback/<post_id>/status", "PATCH"),
        ("/api/code/feedback/<post_id>/attachments/<attachment_id>", "GET"),
    }
    client = app.test_client()
    path = f"/api/knowledge/v2/spaces/{ids['member_space']}/items/{ids['item_id']}/markdown-download"
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.data
    for method, url in (
        ("POST", f"/api/knowledge/v2/spaces/{ids['member_space']}/source-availability"),
        ("DELETE", f"/api/code/feedback/{uuid4()}"),
        ("PATCH", f"/api/code/feedback/{uuid4()}/status"),
    ):
        assert client.open(url, method=method, json=[]).status_code == 400
    who["value"] = None
    assert client.get(path).status_code == 401
    assert client.get("/api/code/feedback").status_code == 401


def test_original_link_multipart_route_retries_and_availability(wiki):
    _, ids, source, management, _ = wiki
    app = Flask(__name__)
    register_workspace_feature_routes(app, source, management,
        lambda: {"id": "member01", "scope_epoch": 1})
    client = app.test_client()
    base = f"/api/knowledge/v2/spaces/{ids['member_space']}"
    item_id = str(ids["item_id"])
    operation_id = str(uuid4())
    before = client.post(base+"/source-availability", json={"item_ids": [item_id]}).json["items"][item_id]
    results = []
    for _ in range(2):
        response = client.post(base+f"/items/{item_id}/original-links", data={
            "files": (io.BytesIO(b"Real original bytes"), "original.txt"),
            "operation_id": operation_id, "expected_revision_id": str(ids["revision_id"])})
        assert response.status_code == 200
        results.append(response.json)
    assert results[0] == results[1]
    available = client.post(base+"/source-availability", json={"item_ids": [item_id]})
    assert available.status_code == 200 and available.json["items"][item_id] == before + 1


def test_export_rejects_concurrently_changed_inventory_instead_of_partial_zip(wiki, monkeypatch):
    factory, ids, _, _, service = wiki
    body = service._body
    def changed(principal, space_id, item):
        result = body(principal, space_id, item)
        c = factory.open()
        try:
            with transaction(c):
                _seed_published_preview_item(c, actor_user_id="member01", body="# Concurrent new item\n",
                    label="concurrent-export", space_id=space_id, title="Added during export", timestamp_ms=1001)
        finally:
            c.close()
        return result
    monkeypatch.setattr(service, "_body", changed)
    with pytest.raises(SourceArtifactStateConflictError):
        service.export(SessionPrincipal("member01", 1), str(ids["member_space"]))


@pytest.mark.skipif(os.name != "nt", reason="Windows CAS binding")
def test_original_link_uses_real_windows_verified_blob_storage(wiki):
    factory, ids, source, management, _ = wiki
    root = factory.database_path.parent.parent
    stager = FilesystemLegacyBlobStager(MigrationStorageLayout(
        data_root=root, database=factory.database_path,
        blob_root=root / "blobs", projection_root=root / "projections"))
    source = SourceArtifactService(factory, scope_resolver=source._scopes, stager=stager)
    service = WikiFilesService(source, management)
    principal = SessionPrincipal("member01", 1)
    content = "기존 Wiki를 다시 만들지 않고 연결한 실제 원본입니다.".encode()
    result = service.attach(principal, str(ids["member_space"]), str(ids["item_id"]),
        filename="original.txt", content=content, operation_id=str(uuid4()),
        expected_revision_id=str(ids["revision_id"]))
    result = source.download(ItemSourceArtifactDownloadRequest(principal,
        str(ids["member_space"]), str(ids["item_id"]), result["artifact_id"]))
    assert result.content == content


def _feedback_process_edit(path, post_id, start, output, label):
    store = FeedbackStore(path)
    output.put("ready")
    if not start.wait(20):
        raise RuntimeError("process test start timeout")
    try:
        result = store.mutate(OWNER, action="edit", post_id=post_id,
            operation_id=str(uuid4()), expected_version=1,
            title=label, kind="bug", body="Concurrent author edit")
        output.put(result["row_version"])
    except FeedbackError as error:
        output.put(error.http_status)


def test_feedback_cross_process_conflicting_edits_commit_exactly_once(tmp_path):
    path = tmp_path / "feedback.db"
    store = FeedbackStore(path)
    post_id = create_post(store)["id"]
    ctx = multiprocessing.get_context("spawn")
    start, output = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_feedback_process_edit,
        args=(path, post_id, start, output, str(number))) for number in range(2)]
    try:
        for process in processes:
            process.start()
        assert [output.get(timeout=30) for _ in processes] == ["ready", "ready"]
        start.set()
        assert sorted(output.get(timeout=30) for _ in processes) == [2, 409]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert FeedbackStore(path).get(OWNER, post_id)["row_version"] == 2
        with store.connection() as c:
            assert c.execute("SELECT COUNT(*) FROM feedback_history").fetchone()[0] == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)
        output.close()
