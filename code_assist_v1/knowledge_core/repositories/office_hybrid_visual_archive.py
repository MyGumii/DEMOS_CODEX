"""Private exact-byte archive for ordinary Office native/page visual input.

This does not grant Office export rights or publish a Wiki. A composition-owned
authorizer is checked before CAS staging, before DB retention, and around a
later read. The caller must own a verified lease for the original Office bytes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
import secrets

from ..db import ManagedConnection, db_now_ms, transaction, TransactionMode
from ..migrations.v054_office_hybrid_visual_archive import POLICY
from ..office_hybrid_source import PreparedOfficeHybridSource, restore_office_hybrid_source
from ..office_visual_source import AuthorizedOfficePdfRender, OfficeVisualSourceError
from ..pdf_visual_source import PdfPageRenderPolicy, PdfRenderedPage, renderer_identity
from ..services.native_authoring_source import _Leases
from ...llm_images import ModelImagePolicy, validate_image_inputs, ModelImageInput
from .artifacts import ArtifactRepository, CAS_BLOB_MEDIA_TYPE, StagedVerifiedBlob, _connection


_KEY = secrets.token_bytes(32)
_FIELDS = ("preview_id", "actor_user_id", "artifact_id", "extraction_snapshot_id",
    "original_sha256", "derivative_sha256", "native_structure_sha256",
    "identity_sha256", "identity_size_bytes", "page_count", "page_bytes",
    "page_pixels", "policy_version", "created_at")
_PAGE_FIELDS = ("page", "source_id", "width", "height", "content_sha256", "size_bytes")


class OfficeHybridArchiveConflict(ValueError):
    pass


def _require(condition, message="Retained Office visual source changed or is unavailable."):
    if not condition:
        raise OfficeHybridArchiveConflict(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"))


def _close_all(leases):
    for lease in reversed(leases):
        try:
            lease.close()
        except BaseException:
            pass


@dataclass(frozen=True, slots=True)
class PreparedOfficeHybridArchive:
    source: PreparedOfficeHybridSource = field(repr=False)
    original: bytes = field(repr=False)
    metadata: tuple
    identity_json: str = field(repr=False)
    pages: tuple[tuple, ...]
    blobs: tuple[StagedVerifiedBlob, ...] = field(repr=False)
    seal: str = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)

    def close(self):
        self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active,
            "Office visual CAS preparation is closed.")
        return self

    def __exit__(self, *_):
        self.close()


def _seal(prepared):
    value = [prepared.source.seal, sha256(prepared.original).hexdigest(),
        prepared.metadata, prepared.identity_json, prepared.pages,
        [asdict(blob) for blob in prepared.blobs], prepared._leases.token]
    return hmac.digest(_KEY, _json(value).encode(), "sha256").hex()


@dataclass(frozen=True, slots=True)
class ArchivedOfficeHybridBytes:
    metadata: tuple
    identity_json: str = field(repr=False)
    derivative_pdf: bytes = field(repr=False)
    pages: tuple[tuple[int, ModelImageInput], ...] = field(repr=False)
    _leases: _Leases = field(repr=False, compare=False)

    def close(self):
        self._leases.close()

    def __enter__(self):
        _require(self._leases.token in self._leases.owner._active,
            "Office visual CAS read is closed.")
        return self

    def __exit__(self, *_):
        self.close()


class OfficeHybridVisualArchiveRepository:
    def __init__(self, factory, stager, *, export_authorizer):
        if not callable(export_authorizer):
            raise TypeError("A server-owned Office export authorizer is required.")
        self.factory, self.stager = factory, stager
        self.export_authorizer = export_authorizer
        self._active = set()

    def _authorize(self, *, actor_user_id, filename, original):
        digest = sha256(original).hexdigest()
        if self.export_authorizer(actor_user_id, filename, digest) is not True:
            raise PermissionError("Office export permission is unavailable.")

    @staticmethod
    def _observation(observation, content, media_type, leases):
        lease, blob = getattr(observation, "lease", None), getattr(observation, "blob", None)
        if callable(getattr(lease, "close", None)):
            leases.append(lease)
        digest = sha256(content).hexdigest()
        _require(type(content) is bytes and content and type(blob) is StagedVerifiedBlob
            and blob.sha256 == digest and blob.size_bytes == len(content)
            and blob.media_type == media_type
            and blob.relative_path == f"{digest[:2]}/{digest[2:4]}/{digest}"
            and callable(getattr(lease, "close", None)),
            "Office visual CAS observation differs from its bytes.")
        return blob

    def prepare(self, source, *, preview_id, artifact_id, extraction_snapshot_id,
            actor_user_id, original_filename, original, created_at_ms=None):
        if type(source) is not PreparedOfficeHybridSource:
            raise OfficeVisualSourceError("A process-local Office hybrid source is required.")
        if source.render.original_filename != original_filename:
            raise OfficeVisualSourceError("The Office source filename changed.")
        source.checked(actor_user_id=actor_user_id, original=original)
        self._authorize(actor_user_id=actor_user_id, filename=original_filename,
            original=original)
        connection = self.factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                now = db_now_ms(connection)
        finally:
            connection.close()
        if created_at_ms is not None:
            _require(type(created_at_ms) is int and 0 <= created_at_ms <= now,
                "Office archive creation time is invalid.")
            now = created_at_ms
        identity_json = _json(source.identity())
        _require(len(identity_json.encode()) <= 1_048_576,
            "Office visual identity exceeds its archive budget.")
        pages = tuple((page.page, page.image.source_id, page.width, page.height,
            sha256(page.image.data).hexdigest(), len(page.image.data)) for page in source.pages)
        metadata = (preview_id, actor_user_id, artifact_id, extraction_snapshot_id,
            source.render.original_sha256, source.render.derivative_sha256,
            source.identity()["native_structure_sha256"], sha256(identity_json.encode()).hexdigest(),
            len(identity_json.encode()), len(pages), sum(row[5] for row in pages),
            sum(row[2] * row[3] for row in pages), POLICY, now)
        leases, group = [], None
        try:
            contents = (source.render.derivative_pdf,) + tuple(
                page.image.data for page in source.pages)
            blobs = []
            for content, media_type in zip(contents,
                    ("application/pdf",) + ("image/png",) * len(source.pages), strict=True):
                observation = self.stager.stage_and_verify(content,
                    expected_sha256=sha256(content).hexdigest(), media_type=media_type,
                    effective_at_ms=now, normalized_content_sha256=None,
                    normalizer_version=None)
                blobs.append(self._observation(observation, content, media_type, leases))
            source.checked(actor_user_id=actor_user_id, original=original)
            self._authorize(actor_user_id=actor_user_id, filename=original_filename,
                original=original)
            group = _Leases(self, leases)
            prepared = PreparedOfficeHybridArchive(source, original, metadata,
                identity_json, pages, tuple(blobs), "", group)
            return replace(prepared, seal=_seal(prepared))
        except BaseException:
            if group is None:
                _close_all(leases)
            else:
                group.close()
            raise

    def _load(self, connection, *, preview_id, actor_user_id,
            allow_extracting=False):
        interim = (""" OR (p.status='extracting' AND EXISTS(
                SELECT 1 FROM office_hybrid_upload_request q
                JOIN durable_job j ON j.id=q.parent_job_id
                WHERE q.preview_id=p.id AND q.parent_job_id=p.durable_job_id
                  AND q.actor_user_id=p.actor_user_id AND q.artifact_id=p.artifact_id
                  AND j.state='running' AND j.handler_version='v3'))"""
            if allow_extracting and self.factory.migration_catalog.latest_version >= 55
            else "")
        row = connection.execute(f"""SELECT a.*,b.identity_json,b.derivative_blob_sha256
          FROM office_hybrid_visual_archive a
          JOIN office_hybrid_visual_archive_ready r ON r.preview_id=a.preview_id
          JOIN office_hybrid_visual_archive_body b ON b.preview_id=a.preview_id
          JOIN office_visual_derivative d ON d.preview_id=a.preview_id
            AND d.artifact_id=a.artifact_id
            AND d.extraction_snapshot_id=a.extraction_snapshot_id
          JOIN publication_preview p ON p.id=a.preview_id
            AND p.artifact_id=a.artifact_id
          JOIN extraction_snapshot_body eb
            ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
          JOIN app_user u ON u.id=a.actor_user_id
          WHERE a.preview_id=? AND a.actor_user_id=?
            AND p.actor_user_id=a.actor_user_id
            AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
            AND ((p.status='ready' AND p.extraction_snapshot_id=a.extraction_snapshot_id)
              {interim})""",
          (preview_id, actor_user_id)).fetchone()
        _require(row is not None)
        metadata = tuple(row[field] for field in _FIELDS)
        identity = row["identity_json"]
        _require(type(identity) is str and len(identity.encode()) == row["identity_size_bytes"]
            and sha256(identity.encode()).hexdigest() == row["identity_sha256"]
            and row["derivative_blob_sha256"] == row["derivative_sha256"])
        pages = tuple(tuple(item[field] for field in _PAGE_FIELDS)
            for item in connection.execute("""SELECT p.* FROM office_hybrid_visual_page p
              JOIN office_hybrid_visual_page_body b
                ON b.preview_id=p.preview_id AND b.page=p.page AND b.content_sha256=p.content_sha256
              WHERE p.preview_id=? ORDER BY p.page""", (preview_id,)))
        value = json.loads(identity)
        _require(type(value) is dict and value.get("version") ==
            "office-native-structure-rendered-pages-v1"
            and value.get("original_sha256") == row["original_sha256"]
            and value.get("derivative_sha256") == row["derivative_sha256"]
            and value.get("native_structure_sha256") == row["native_structure_sha256"]
            and len(pages) == row["page_count"]
            and tuple(item[0] for item in pages) == tuple(range(1, len(pages) + 1))
            and tuple((item["page"], item["source_id"], item["width"], item["height"],
                item["sha256"], item["bytes"]) for item in value["pages"]) == pages
            and sum(item[5] for item in pages) == row["page_bytes"]
            and sum(item[2] * item[3] for item in pages) == row["page_pixels"])
        return metadata, identity, pages

    def put(self, connection: ManagedConnection, prepared: PreparedOfficeHybridArchive):
        _connection(connection, write=True)
        _require(type(prepared) is PreparedOfficeHybridArchive
            and prepared._leases.owner is self
            and prepared._leases.token in self._active
            and hmac.compare_digest(prepared.seal, _seal(prepared)))
        metadata = prepared.metadata
        source = prepared.source.checked(actor_user_id=metadata[1], original=prepared.original)
        _require(source.render.original_filename == json.loads(prepared.identity_json)["original_filename"])
        self._authorize(actor_user_id=metadata[1], filename=source.render.original_filename,
            original=prepared.original)
        prior = connection.execute("SELECT 1 FROM office_hybrid_visual_archive WHERE preview_id=?",
            (metadata[0],)).fetchone()
        if prior is not None:
            _require(self._load(connection, preview_id=metadata[0], actor_user_id=metadata[1],
                allow_extracting=True) ==
                (metadata, prepared.identity_json, prepared.pages))
            return metadata[0]
        unique = {blob.sha256: blob for blob in prepared.blobs}
        for blob in unique.values():
            if ArtifactRepository._preflight_blob(connection, blob):
                connection.execute("""INSERT INTO blob_object(sha256,size_bytes,media_type,relative_path,
                   verification_status,created_at,verified_at)
                   VALUES(?,?,?,?,'verified',?,?)""",
                   (blob.sha256, blob.size_bytes, CAS_BLOB_MEDIA_TYPE,
                    blob.relative_path, blob.created_at_ms, blob.verified_at_ms))
        connection.execute("INSERT INTO office_hybrid_visual_archive(" + ",".join(_FIELDS)
            + ") VALUES(" + ",".join("?" for _ in _FIELDS) + ")", metadata)
        connection.execute("""INSERT INTO office_hybrid_visual_archive_body
            (preview_id,identity_json,derivative_blob_sha256) VALUES(?,?,?)""",
            (metadata[0], prepared.identity_json, prepared.blobs[0].sha256))
        for page in prepared.pages:
            connection.execute("INSERT INTO office_hybrid_visual_page(preview_id,"
                + ",".join(_PAGE_FIELDS) + ") VALUES(?,?,?,?,?,?,?)",
                (metadata[0], *page))
            connection.execute("""INSERT INTO office_hybrid_visual_page_body
                (preview_id,page,content_sha256) VALUES(?,?,?)""",
                (metadata[0], page[0], page[4]))
        connection.execute("INSERT INTO office_hybrid_visual_archive_ready(preview_id) VALUES(?)",
            (metadata[0],))
        _require(self._load(connection, preview_id=metadata[0], actor_user_id=metadata[1],
            allow_extracting=True) ==
            (metadata, prepared.identity_json, prepared.pages))
        return metadata[0]

    def read(self, *, preview_id, actor_user_id, original_filename, original):
        self._authorize(actor_user_id=actor_user_id, filename=original_filename,
            original=original)
        connection = self.factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                saved = self._load(connection, preview_id=preview_id,
                    actor_user_id=actor_user_id)
                metadata, identity, pages = saved
                _require(metadata[4] == sha256(original).hexdigest()
                    and json.loads(identity)["original_filename"] == original_filename)
                derivative_size = connection.execute("""SELECT d.derivative_size_bytes
                    FROM office_visual_derivative d WHERE d.preview_id=?""",
                    (preview_id,)).fetchone()[0]
        finally:
            connection.close()
        leases, group = [], None
        try:
            contents = []
            for digest, size, media_type in ((metadata[5], derivative_size, "application/pdf"),
                    *((page[4], page[5], "image/png") for page in pages)):
                observation, content = self.stager.verify_existing(
                    expected_sha256=digest, expected_size=size, media_type=media_type,
                    normalized_content_sha256=None, normalizer_version=None)
                self._observation(observation, content, media_type, leases)
                contents.append(content)
            images = tuple(ModelImageInput(page[1], "image/png", data)
                for page, data in zip(pages, contents[1:], strict=True))
            measured = validate_image_inputs(images,
                ModelImagePolicy(len(images), 16_777_216, 67_108_864,
                    16_000_000, 256_000_000))
            _require(all((page[2], page[3]) == (item["width"], item["height"])
                for page, item in zip(pages, measured, strict=True)))
            self._authorize(actor_user_id=actor_user_id, filename=original_filename,
                original=original)
            connection = self.factory.open(read_only=True)
            try:
                with transaction(connection, TransactionMode.DEFERRED):
                    _require(self._load(connection, preview_id=preview_id,
                        actor_user_id=actor_user_id) == saved)
            finally:
                connection.close()
            group = _Leases(self, leases)
            return ArchivedOfficeHybridBytes(metadata, identity, contents[0],
                tuple((page[0], image) for page, image in zip(pages, images, strict=True)),
                group)
        except BaseException:
            if group is None:
                _close_all(leases)
            else:
                group.close()
            raise

    def restore_source(self, *, preview_id, actor_user_id, original_filename,
            original):
        """Rebuild a writer input from exact archived page bytes, without COM.

        The archive read already verifies CAS bytes, page inventory and export
        rights. The writer sees those exact bytes rather than a new rendering
        that could silently differ after a runtime or host change.
        """
        with self.read(preview_id=preview_id, actor_user_id=actor_user_id,
                original_filename=original_filename, original=original) as archived:
            identity = json.loads(archived.identity_json)
            try:
                policy = PdfPageRenderPolicy(**identity["render_policy"])
                render = AuthorizedOfficePdfRender(
                    actor_user_id=actor_user_id, original_filename=original_filename,
                    original_sha256=archived.metadata[4],
                    original_format=identity["native_format"],
                    derivative_pdf=archived.derivative_pdf,
                    derivative_sha256=archived.metadata[5],
                    expected_pages=archived.metadata[9],
                    render_method=identity["render_method"],
                    export_permitted=True,
                )
                _require(identity["render_identity"] == renderer_identity(),
                    "Pinned Office page renderer identity changed.")
                pages = tuple(PdfRenderedPage(page, identity["pages"][index]["width"],
                    identity["pages"][index]["height"], image)
                    for index, (page, image) in enumerate(archived.pages))
                restored = restore_office_hybrid_source(render,
                    actor_user_id=actor_user_id, original=original,
                    pages=pages, render_policy=policy)
            except (KeyError, TypeError, ValueError) as exc:
                raise OfficeHybridArchiveConflict(
                    "Retained Office visual evidence cannot be reconstructed.") from exc
            _require(restored.identity() == identity
                and tuple((page.page, page.image.data) for page in restored.pages)
                    == tuple((page, image.data) for page, image in archived.pages),
                "Retained Office page pixels differ from their archive identity.")
            self._authorize(actor_user_id=actor_user_id, filename=original_filename,
                original=original)
            return restored


__all__ = ["OfficeHybridVisualArchiveRepository", "PreparedOfficeHybridArchive",
    "ArchivedOfficeHybridBytes", "OfficeHybridArchiveConflict"]
