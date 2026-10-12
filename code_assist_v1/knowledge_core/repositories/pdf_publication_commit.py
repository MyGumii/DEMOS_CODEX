"""PDF-specific source binding inside the existing atomic typed publisher."""
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import json

from .pptx_role_publication_commit import PptxRolePublicationCommitRepository
from .native_publication_commit import _metadata_fence
from .publication_body_request import has_pdf_work, requested_publication_body
from .publication_commit import (
    FrozenPublicationSource, PublicationCommitRepository, _SourceBinding,
    PublicationCommitConflictError, PublicationCommitIntegrityError,
)
from .pdf_authoring import PreparedPdfRun, PreparedPdfSnapshot
from ..services.pdf_authoring_source import CanonicalPdfAuthoringSourceService, PdfSourceConflict
from ..migrations.v039_pdf_authoring_storage import (
    POLICY, GEOMETRY_VERSION, ENGINE_VERSION, ASSEMBLY,
)


@dataclass(frozen=True, slots=True)
class FrozenPdfPublicationSource(FrozenPublicationSource):
    pdf_snapshot_id: str | None = None
    pdf_manifest_json: str = ""
    pdf_manifest_sha256: str = ""
    requested_fence: tuple = ()
    metadata_fence: str = ""
    verified_run: PreparedPdfRun | None = None
    body_seal: str = ""


class PdfPublicationCommitRepository(PptxRolePublicationCommitRepository):
    def __init__(self, native_source, public_bodies, pdf_source):
        super().__init__(native_source, public_bodies)
        if type(pdf_source) is not CanonicalPdfAuthoringSourceService:
            raise TypeError("PDF publication needs its canonical source verifier.")
        self.pdf_source = pdf_source

    def _pdf_seal(self, source):
        values = []
        for name in FrozenPdfPublicationSource.__dataclass_fields__:
            if name == "body_seal":
                continue
            value = getattr(source, name)
            values.append((name, id(value) if name in ("content_md", "pdf_manifest_json",
                "extraction_source", "verified_run") else value))
        return hmac.digest(self._preparation_key, repr(values).encode(), "sha256").hex()

    def _pdf_requested(self, connection, binding):
        row = connection.execute("SELECT * FROM pdf_publication_candidate WHERE preview_id=?",
            (binding.preview_id,)).fetchone()
        if (row is None or (row["actor_user_id"], row["artifact_id"], row["extraction_snapshot_id"],
                row["source_snapshot_sha256"]) != (binding.actor_user_id, binding.artifact_id,
                    binding.extraction_snapshot_id, binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The PDF upload has no complete retained Wiki for publication.")
        identity = requested_publication_body(connection, actor_id=binding.actor_user_id, preview_id=binding.preview_id)
        if identity.kind != "pdf_wiki" or identity.snapshot_id != row["snapshot_id"]:
            raise PublicationCommitIntegrityError("The requested PDF publication identity differs.")
        try:
            # SQL/CPU only. Parsing, retained-file leasing and receipt replay
            # are performed by the service before its writer transaction.
            current = self.pdf_source._snapshot(connection, binding.preview_id,
                binding.actor_user_id, now_ms=binding.created_at_ms)
        except PdfSourceConflict as exc:
            raise PublicationCommitConflictError(str(exc)) from exc
        if current.fence_sha256 != row["source_fence_sha256"]:
            raise PublicationCommitConflictError("The PDF Wiki source changed.")
        return dict(row), tuple(row), identity, current

    def freeze_source(self, connection, **kwargs):
        if not has_pdf_work(connection, kwargs["preview_id"]):
            return super().freeze_source(connection, **kwargs)
        if kwargs.get("authored_snapshot_id") is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace the PDF candidate.")
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"], kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"], kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        requested, fence, identity, current = self._pdf_requested(connection, binding)
        source = PublicationCommitRepository.freeze_source(self, connection, **kwargs)
        row = connection.execute("SELECT content_md,manifest_json FROM pdf_wiki_snapshot_body WHERE snapshot_id=?",
            (requested["snapshot_id"],)).fetchone()
        metadata = json.loads(connection.execute("SELECT metadata_json FROM pdf_authoring_run WHERE id=?",
            (requested["run_id"],)).fetchone()[0])
        try:
            if row is None or any(type(value) is not str for value in row):
                raise ValueError
            content, manifest_json = row
            manifest = json.loads(manifest_json)
            original = manifest["source"]
            if (sha256(content.encode()).hexdigest() != requested["content_sha256"]
                    or len(content.encode()) != requested["content_size_bytes"] or "\r" in content
                    or sha256(manifest_json.encode()).hexdigest() != requested["manifest_sha256"]
                    or len(manifest_json.encode()) != requested["manifest_size_bytes"]
                    or original["raw_sha256"] != current.source["raw_sha256"]
                    or type(original["raw_bytes"]) is not int or original["raw_bytes"] != current.source["raw_size_bytes"]
                    or original["filename"] != current.source["original_filename"]
                    or original["geometry_version"] != GEOMETRY_VERSION or original["engine_version"] != ENGINE_VERSION
                    or original["geometry_sha256"] != metadata["geometry_sha256"]
                    or type(original["geometry_bytes"]) is not int or original["geometry_bytes"] != metadata["geometry_size_bytes"]
                    or manifest["plan_sha256"] != requested["source_plan_sha256"]
                    or manifest["policy_version"] != identity.policy_version or identity.policy_version != POLICY
                    or manifest["assembly_version"] != ASSEMBLY or manifest["structure_origin"] != "inferred_line_grid"
                    or manifest["wiki_sha256"] != requested["content_sha256"]
                    or type(manifest["wiki_bytes"]) is not int or manifest["wiki_bytes"] != requested["content_size_bytes"]
                    or any(manifest[key] is not True for key in ("all_nonblank_ranges_authored", "all_detected_grid_cells_rendered"))
                    or any(manifest[key] is not False for key in ("semantic_review_performed",
                        "semantic_completeness_proven", "caption_ownership_inferred", "images_interpreted"))):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PublicationCommitIntegrityError("The PDF Wiki body and manifest are inconsistent.") from exc
        fields = {name: getattr(source, name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=requested["content_sha256"], normalized_sha256=requested["content_sha256"],
            size_bytes=requested["content_size_bytes"], normalizer_version="markdown_lf_v1", content_md=content,
            content_blob_hash=None, content_sha256=requested["content_sha256"], extraction_source=source,
            reuse_targets=kwargs.get("authorized_targets", ()), reuse_fence=())
        prepared = FrozenPdfPublicationSource(**fields, pdf_snapshot_id=requested["snapshot_id"],
            pdf_manifest_json=manifest_json, pdf_manifest_sha256=requested["manifest_sha256"],
            requested_fence=fence, metadata_fence=_metadata_fence(connection, binding))
        return replace(prepared, body_seal=self._pdf_seal(prepared))

    def bind_replayed_snapshot(self, source, run, snapshot):
        """The owning service reconstructs/assembles outside every SQLite txn."""
        if (type(source) is not FrozenPdfPublicationSource or type(run) is not PreparedPdfRun
                or type(snapshot) is not PreparedPdfSnapshot
                or not hmac.compare_digest(source.body_seal, self._pdf_seal(source))
                or source.pdf_snapshot_id != snapshot.snapshot_id or snapshot.run_id != run.run_id
                or source.content_md != snapshot.markdown or source.pdf_manifest_json != snapshot.manifest_json):
            raise PublicationCommitIntegrityError("The PDF publication differs from its replayed writer receipts.")
        source = replace(source, verified_run=run, body_seal="")
        return replace(source, body_seal=self._pdf_seal(source))

    def _revalidate_own_candidate(self, connection, draft, source):
        if type(source) is not FrozenPdfPublicationSource:
            return super()._revalidate_own_candidate(connection, draft, source)
        requested, fence, _, current = self._pdf_requested(connection, draft)
        run = source.verified_run
        if (source.requested_fence != fence or source.metadata_fence != _metadata_fence(connection, draft)
                or source.pdf_snapshot_id != requested["snapshot_id"]
                or source.pdf_manifest_sha256 != requested["manifest_sha256"]
                or type(run) is not PreparedPdfRun or run.run_id != requested["run_id"]
                or run.source.fence_sha256 != current.fence_sha256
                or run.source._leases.token not in self.pdf_source._active
                or source.extraction_source is None or source.reuse_targets != draft.targets
                or source.authored_snapshot_id is not None or source.reused_extraction_snapshot_id is not None
                or source.authored_preparation is not None or source.content_blob_hash is not None
                or source.reuse_fence != () or type(source.content_md) is not str
                or (source.raw_sha256, source.normalized_sha256, source.content_sha256, source.size_bytes,
                    source.normalizer_version) != (requested["content_sha256"], requested["content_sha256"],
                    requested["content_sha256"], requested["content_size_bytes"], "markdown_lf_v1")
                or type(source.body_seal) is not str or not hmac.compare_digest(source.body_seal, self._pdf_seal(source))):
            raise PublicationCommitConflictError("The prepared PDF publication is stale or inconsistent.")
        try:
            # The restored plan includes mutable internal caches. Object ID,
            # persisted plan hash and an open lease do not validate those bytes.
            # This deep source seal is SQL/CPU-only, with no parser or file I/O.
            self.pdf_source.revalidate_in_transaction(connection, run.source, now_ms=draft.created_at_ms)
        except PdfSourceConflict as exc:
            raise PublicationCommitConflictError(str(exc)) from exc
        PublicationCommitRepository._revalidate_frozen_source(connection, draft, source.extraction_source)
