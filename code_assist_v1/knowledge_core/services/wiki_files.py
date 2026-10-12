"""Original-only attachment and complete, authorized Markdown exports."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import mimetypes
import re
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5
import zipfile

from ..db import TransactionMode, db_now_ms, transaction
from ..repositories.artifacts import ArtifactIds, ArtifactRepository
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.item_history import ItemHistoryRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import UserRepository
from ..scope import Capability
from .management_reads import _closure_inputs, _materialize_body
from .publication import prepare_publication_artifact, build_source_artifact_bundle
from .source_artifacts import (
    _uuid, SourceArtifactNotFoundError, SourceArtifactStateConflictError,
    SourceArtifactServiceError,
)


def file_name(value, fallback="document", maximum=150):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(value)).strip(" .")
    value = value[:maximum].rstrip(" .") or fallback
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"{x}{i}" for x in ("COM", "LPT") for i in range(1, 10)]}:
        value = "_" + value
    return value


class WikiFilesService:
    def __init__(self, source_service, management_service):
        self.sources = source_service
        self.factory = source_service._factory
        self.scopes = source_service._scopes
        self.stager = source_service._stager
        self.reads = source_service._management
        self.management = management_service
        self.history = ItemHistoryRepository()

    def actor(self, principal):
        c = self.factory.open(read_only=True)
        try:
            row = c.execute("SELECT id,display_name,system_role AS role,scope_epoch,account_status FROM app_user WHERE id=?", (principal.user_id,)).fetchone()
            if row is None or row["account_status"] != "active" or row["scope_epoch"] != principal.captured_scope_epoch:
                raise SourceArtifactNotFoundError("유효한 로그인 계정이 필요합니다.")
            return dict(row)
        finally:
            c.close()

    def availability(self, principal, space_id, item_ids):
        if not isinstance(item_ids, list) or len(item_ids) > 100:
            raise ValueError("A bounded item list is required.")
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                plan, _ = self._authorization(c, principal, space_id)
                sources, overlays = _closure_inputs(plan)
                result = {}
                for item_id in item_ids:
                    auth = self.reads.resolve_item_authorization(c, selected_space_id=space_id,
                        item_id=_uuid(item_id, "item_id"), source_space_ids=sources, overlays=overlays)
                    if auth is None:
                        raise SourceArtifactNotFoundError("문서를 찾을 수 없습니다.")
                    result[item_id] = len(self.sources._artifacts.list_for_authorized_item(c, item_id=item_id, authorization=auth))
                return result
        finally:
            c.close()

    def _authorization(self, c, principal, space_id, item_id=None, *, write=False):
        scope = self.scopes.resolve_in_transaction(c, principal, _uuid(space_id, "space_id"), Capability.EDIT if write else Capability.READ)
        plan = scope.access_plan
        if plan is None:
            raise SourceArtifactServiceError("지식 공간을 확인할 수 없습니다.")
        if item_id is None:
            return plan, None
        sources, overlays = _closure_inputs(plan)
        auth = self.reads.resolve_item_authorization(c, selected_space_id=space_id,
            item_id=_uuid(item_id, "item_id"), source_space_ids=sources, overlays=overlays)
        if auth is None or (write and auth.origin != "native"):
            raise SourceArtifactNotFoundError("문서를 찾을 수 없거나 원본 연결 권한이 없습니다.")
        row = c.execute("SELECT lifecycle_status FROM knowledge_item WHERE id=?", (item_id,)).fetchone()
        if row is None or row[0] != "active":
            raise SourceArtifactNotFoundError("활성 문서에서만 사용할 수 있습니다.")
        return plan, auth

    def attach(self, principal, space_id, item_id, *, filename, content, operation_id, expected_revision_id):
        _uuid(operation_id, "operation_id")
        _uuid(expected_revision_id, "expected_revision_id")
        filename = file_name(filename)
        digest = sha256(content).hexdigest()
        request_hash = sha256(json.dumps([space_id, item_id, filename, digest, expected_revision_id], ensure_ascii=False).encode()).hexdigest()
        key = str(uuid5(NAMESPACE_URL, f"wiki-original:{principal.user_id}:{operation_id}"))
        actor = self.actor(principal)
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                self._authorization(c, principal, space_id, item_id, write=True)
        finally:
            c.close()
        now = time.time_ns() // 1_000_000
        # Native extraction records real text when available. No LLM call or
        # Wiki re-publication is performed; extraction failure still retains raw bytes.
        extracted = None
        failure = None
        info = None
        try:
            from ...document_extractors import extract_document
            info = extract_document(filename, content, allow_configured_converters=False)
            extracted = info.markdown or None
            if extracted is None:
                failure = "original_text_unavailable"
        except Exception:
            failure = "original_text_unavailable"
        finally:
            if info is not None:
                from ...second_brain_store import _cleanup_extraction_temp_dirs
                _cleanup_extraction_temp_dirs(info.metadata)
        prepared = prepare_publication_artifact(content, extracted_markdown=extracted,
            extraction_failure_code=failure, extractor_name="original_link_native",
            extractor_version="1", created_at_ms=now)
        media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        observed = self.stager.stage_and_verify(content, expected_sha256=digest, media_type=media,
            effective_at_ms=now, normalized_content_sha256=None, normalizer_version=None)
        ids = ArtifactIds(*(str(uuid5(NAMESPACE_URL, f"{key}:{kind}")) for kind in ("artifact", "raw", "extraction")))
        try:
            c = self.factory.open()
            try:
                with transaction(c, TransactionMode.IMMEDIATE):
                    _, auth = self._authorization(c, principal, space_id, item_id, write=True)
                    now = db_now_ms(c)
                    users = UserRepository()
                    begun = users.begin_operation(c, actor_user_id=principal.user_id,
                        operation_type="item.original_link", operation_id=operation_id,
                        request_sha256=request_hash, dedup_id=key, now_ms=now)
                    if not begun.inserted:
                        if begun.operation.status == "committed":
                            return dict(begun.operation.result)
                        raise SourceArtifactStateConflictError("원본 연결 요청이 처리 중입니다.")
                    if auth.source_revision_id != expected_revision_id:
                        raise SourceArtifactStateConflictError("문서가 변경되었습니다. 최신 문서를 열고 다시 연결해 주세요.")
                    existing = c.execute("""SELECT s.artifact_id FROM item_source s JOIN source_artifact a ON a.id=s.artifact_id
                        WHERE s.item_id=? AND a.raw_sha256=? AND a.body_retention_status='present' ORDER BY s.created_at LIMIT 1""", (item_id, digest)).fetchone()
                    artifact_id = existing[0] if existing else ids.artifact_id
                    if existing is None:
                        bundle = build_source_artifact_bundle(prepared, ids=ids, origin_type="upload",
                            origin_key=f"original-link:{item_id}", origin_revision_key=expected_revision_id,
                            raw_blob=observed.blob, logical_name=filename, original_filename=filename,
                            publisher_user_id=principal.user_id, created_at_ms=prepared.extraction.created_at_ms)
                        ArtifactRepository().put_source_bundle(c, bundle)
                        c.execute("INSERT INTO item_source(id,item_id,revision_id,artifact_id,extraction_snapshot_id,role,created_by,created_at) VALUES(?,?,?,?,?,'evidence',?,?)",
                            (str(uuid5(NAMESPACE_URL, key+":link")), item_id, expected_revision_id,
                             ids.artifact_id, ids.extraction_snapshot_id, principal.user_id, now))
                        audit = AuditRepository().append(c, AuditEventDraft(
                            event_uuid=str(uuid5(NAMESPACE_URL, key+":audit")), occurred_at_ms=now,
                            request_id=operation_id, operation_id=operation_id, action="item.original_linked",
                            outcome="success", actor_kind="user", actor_user_id=principal.user_id,
                            actor_display_name=actor["display_name"], actor_role_snapshot=actor["role"],
                            space_id=space_id, item_id=item_id, base_revision_id=expected_revision_id,
                            details={"artifact_id": artifact_id, "filename": filename, "sha256": digest}))
                        OutboxRepository().append(c, OutboxEventDraft(logical_key=f"original-link:{key}",
                            space_id=space_id, aggregate_type="knowledge_item", aggregate_id=item_id,
                            aggregate_version=1, event_type="item.original_linked",
                            payload={"schema_version": 1, "item_id": item_id, "artifact_id": artifact_id,
                                     "audit_event_id": audit.id}, available_at_ms=now))
                        c.execute("UPDATE knowledge_space SET generation=generation+1,updated_at=?,row_version=row_version+1 WHERE id=?", (now, space_id))
                    result = {"artifact_id": artifact_id, "item_id": item_id, "status": "already_linked" if existing else "linked"}
                    users.commit_operation(c, dedup_id=key, result=result, http_status=200, completed_at_ms=now)
                    return result
            finally:
                c.close()
        finally:
            if getattr(observed, "lease", None):
                observed.lease.close()

    def _inventory(self, principal, space_id, item_id=None):
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                plan, auth = self._authorization(c, principal, space_id, item_id)
                sources, overlays = _closure_inputs(plan)
                items = []
                before_time = before_id = None
                if item_id:
                    r = self.history.get_revision(c, space_id=auth.source_space_id, item_id=item_id, revision_id=auth.source_revision_id)
                    items = [(item_id, auth.source_space_id, r.id, r.title)]
                else:
                    while True:
                        batch = self.reads.list_items_closure(c, selected_space_id=space_id, source_space_ids=sources, overlays=overlays,
                            before_updated_at_ms=before_time, before_item_id=before_id, limit=100)
                        items.extend((r.id, r.space_id, r.current_revision_id, r.title) for r in batch)
                        if len(batch) < 100:
                            break
                        before_time, before_id = batch[-1].updated_at_ms, batch[-1].id
                events = []
                before_time = before_id = None
                if item_id is None:
                    while True:
                        batch = self.reads.list_activity_closure(c, selected_space_id=space_id, source_space_ids=sources, overlays=overlays,
                            before_occurred_at_ms=before_time, before_event_uuid=before_id, limit=100)
                        events.extend(asdict(event) for event in batch)
                        if len(batch) < 100:
                            break
                        before_time, before_id = batch[-1].occurred_at_ms, batch[-1].event_uuid
                return {"closure": plan.corpus_closure.sha256, "name": plan.selected_view.name,
                        "items": items, "events": events}
        finally:
            c.close()

    def _body(self, principal, space_id, item):
        iid, source_space, revision_id, title = item
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                _, auth = self._authorization(c, principal, space_id, iid)
                if auth.source_revision_id != revision_id:
                    raise SourceArtifactStateConflictError("내려받는 중 문서가 변경되었습니다. 다시 시도해 주세요.")
                revision = self.history.get_revision(c, space_id=source_space, item_id=iid, revision_id=revision_id)
                assets = [dict(r) for r in c.execute("""SELECT DISTINCT b.logical_name,b.content_sha256,b.size_bytes,b.media_type
                    FROM item_source s JOIN knowledge_revision sr ON sr.id=s.revision_id
                    JOIN artifact_blob b ON b.artifact_id=s.artifact_id
                    JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
                    WHERE s.item_id=? AND sr.revision_no<=? AND b.role<>'raw'
                    AND b.body_retention_status='present' AND b.media_type LIKE 'image/%'""", (iid, revision.revision_no))]
        finally:
            c.close()
        body = _materialize_body(revision.body, self.management._content_loader)
        if body is None:
            raise SourceArtifactServiceError("문서 본문이 없어 다운로드를 완료할 수 없습니다.")
        return body, assets

    def export(self, principal, space_id, item_id=None):
        initial = self._inventory(principal, space_id, item_id)
        output = tempfile.TemporaryFile(mode="w+b")
        try:
            if item_id:
                body, _ = self._body(principal, space_id, initial["items"][0])
                output.write(body.encode("utf-8"))
                name = file_name(initial["items"][0][3]) + ".md"
                media = "text/markdown; charset=utf-8"
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                name = file_name(initial["name"]) + "_" + stamp + ".zip"
                media = "application/zip"
                manifest = []
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    index = [f"# {initial['name']}\n"]
                    for item in initial["items"]:
                        body, assets = self._body(principal, space_id, item)
                        folder = f"documents/{file_name(item[3], maximum=90)}--{item[0]}"
                        asset_names = set()
                        for asset in assets:
                            extension = mimetypes.guess_extension(asset["media_type"]) or ".bin"
                            dest = f"assets/{asset['content_sha256']}{extension}"
                            observed, content = self.stager.verify_existing(expected_sha256=asset["content_sha256"],
                                expected_size=asset["size_bytes"], media_type=asset["media_type"],
                                normalized_content_sha256=None, normalizer_version=None)
                            try:
                                if dest not in asset_names:
                                    archive.writestr(f"{folder}/{dest}", content)
                                    asset_names.add(dest)
                            finally:
                                if getattr(observed, "lease", None):
                                    observed.lease.close()
                            logical = asset["logical_name"]
                            body = body.replace(f"]({logical})", f"]({dest})")
                        path = folder + "/document.md"
                        archive.writestr(path, body.encode("utf-8"))
                        index.append(f"- [{item[3]}](<{path}>)")
                        manifest.append({"item_id": item[0], "source_space_id": item[1], "revision_id": item[2], "path": path})
                    archive.writestr("index.md", "\n".join(index)+"\n")
                    logs = ["# Log", ""]
                    for event in reversed(initial["events"]):
                        occurred = datetime.fromtimestamp(event['occurred_at_ms'] / 1000, timezone.utc).isoformat(timespec="milliseconds")
                        logs.append(f"- {occurred} · {event.get('action')} · {event.get('outcome')} · {event.get('reason') or ''}")
                    archive.writestr("log.md", "\n".join(logs)+"\n")
                    archive.writestr("export.json", json.dumps({"schema_version": 1, "space_id": space_id,
                        "created_at": stamp, "documents": manifest}, ensure_ascii=False, indent=2))
            # A changed closure, revision, visibility or log high-water rejects the
            # completed temporary output; it never returns a silently partial ZIP.
            if self._inventory(principal, space_id, item_id) != initial:
                raise SourceArtifactStateConflictError("다운로드 중 지식 공간이 변경되었습니다. 다시 시도해 주세요.")
            size = output.tell()
            output.seek(0)
            return output, name, media, size, len(initial["items"])
        except BaseException:
            output.close()
            raise
