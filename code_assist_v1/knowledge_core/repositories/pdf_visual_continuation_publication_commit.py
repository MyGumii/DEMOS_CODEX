"""Typed continuation candidates in the existing atomic upload publisher.

The historical authoring run is never made ready. Private outputs are replayed
by the owning service before the writer; this repository binds immutable new
metadata, live source leases and current ordinary publication authority.
"""
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import json

from .publication_commit import (
    FrozenPublicationSource, PublicationCommitRepository, PublicationCommitConflictError,
    PublicationCommitIntegrityError, _SourceBinding,
)
from .native_publication_commit import _metadata_fence
from .publication_body_request import has_pdf_continuation_work, requested_publication_body
from .pdf_visual_publication_commit import PdfVisualPublicationCommitRepository
from .office_hybrid_publication_commit import OfficeHybridPublicationCommitRepository
from .pdf_visual_authoring import PreparedVisualRun
from .pdf_visual_continuation import PdfContinuationRepository
from ..pdf_visual_continuation import VERSION, identity
from ..services.pdf_authoring_source import PdfSourceConflict


@dataclass(frozen=True, slots=True)
class FrozenPdfContinuationPublicationSource(FrozenPublicationSource):
    pdf_continuation_snapshot_id: str | None = None
    request_id: str | None = None
    manifest_json: str = ""
    manifest_sha256: str = ""
    requested_fence: tuple = ()
    metadata_fence: str = ""
    verified_run: PreparedVisualRun | None = None
    body_seal: str = ""


class _ContinuationCommit:
    def __init__(self, *args, continuation_repository, **kwargs):
        if type(continuation_repository) is not PdfContinuationRepository:
            raise TypeError("Continuation publication requires its retained-source repository.")
        super().__init__(*args, **kwargs)
        self.continuation_repository = continuation_repository

    def _continuation_seal(self, source):
        values = []
        for name in FrozenPdfContinuationPublicationSource.__dataclass_fields__:
            if name == "body_seal":
                continue
            value = getattr(source, name)
            values.append((name, id(value) if name in ("content_md", "manifest_json",
                "extraction_source", "verified_run") else value))
        return hmac.digest(self._preparation_key, repr(values).encode(), "sha256").hex()

    def _continuation_requested(self, connection, binding):
        rows = connection.execute("""SELECT * FROM pdf_continuation_publication_candidate
            WHERE preview_id=? AND actor_user_id=?""", (binding.preview_id,binding.actor_user_id)).fetchall()
        if len(rows) != 1:
            raise PublicationCommitConflictError("The continuation has no single completed authorized candidate.")
        row = dict(rows[0])
        if (row["artifact_id"],row["extraction_snapshot_id"],row["source_snapshot_sha256"]) != (
                binding.artifact_id,binding.extraction_snapshot_id,binding.source_snapshot_sha256):
            raise PublicationCommitConflictError("The continuation belongs to a different upload source.")
        requested = requested_publication_body(connection,actor_id=binding.actor_user_id,preview_id=binding.preview_id)
        if (requested.kind,requested.snapshot_id,requested.run_id) != (
                "pdf_continuation_wiki",row["snapshot_id"],row["request_id"]):
            raise PublicationCommitIntegrityError("The continuation requested identity differs.")
        try:
            current = self.continuation_repository.original.source_service._snapshot(
                connection,binding.preview_id,binding.actor_user_id,now_ms=0)
        except (PdfSourceConflict, PermissionError) as exc:
            raise PublicationCommitConflictError("Current source authority no longer permits continuation publication.") from exc
        if current.fence_sha256 != row["source_fence_sha256"]:
            raise PublicationCommitConflictError("The retained continuation source changed.")
        return row, tuple(rows[0]), requested, current

    def freeze_source(self, connection, **kwargs):
        if not has_pdf_continuation_work(connection,kwargs["preview_id"]):
            return super().freeze_source(connection,**kwargs)
        rows = connection.execute("SELECT request_id FROM pdf_continuation_publication_candidate "
            "WHERE preview_id=? AND actor_user_id=?",(kwargs["preview_id"],kwargs["actor_user_id"])).fetchall()
        if len(rows) != 1:
            raise PublicationCommitConflictError("The continuation has no single authorized publication authority.")
        with self.continuation_repository.publication_authority(rows[0][0]):
            return self._freeze_continuation_source(connection,**kwargs)

    def _freeze_continuation_source(self, connection, **kwargs):
        if kwargs.get("authored_snapshot_id") is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace a continuation.")
        binding = _SourceBinding(kwargs["actor_user_id"],kwargs["preview_id"],kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"],kwargs["source_snapshot_sha256"],kwargs["effective_at_ms"])
        requested,fence,_,current = self._continuation_requested(connection,binding)
        extraction = PublicationCommitRepository.freeze_source(self,connection,
            **kwargs,continuation_snapshot_id=requested["snapshot_id"])
        body = connection.execute("""SELECT content_md,manifest_json FROM pdf_continuation_snapshot_body
            WHERE snapshot_id=?""",(requested["snapshot_id"],)).fetchone()
        try:
            if body is None or any(type(value) is not str for value in body):
                raise ValueError
            markdown, encoded = body
            manifest = json.loads(encoded)
            from .pdf_continuation_retained import is_retained_request
            manifest_version = 2 if is_retained_request(connection, requested["request_id"]) else 1
            fields = {"schema_version","version","request_id","parent_run_id","parent_plan_sha256",
                "source_fence_sha256","wiki_sha256","wiki_bytes","leaves","assembly_manifest",
                "all_original_ranges_retained","all_detected_table_cells_rendered",
                "all_visual_ownership_retained","semantic_review_performed","semantic_completeness_proven"}
            if (type(manifest) is not dict or set(manifest)!=fields
                    or json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(",",":"))!=encoded
                    or type(manifest["schema_version"]) is not int
                    or manifest["schema_version"]!=manifest_version or manifest["version"]!=VERSION
                    or manifest["request_id"]!=requested["request_id"]
                    or manifest["parent_run_id"]!=requested["parent_run_id"]
                    or manifest["parent_plan_sha256"]!=requested["source_plan_sha256"]
                    or manifest["source_fence_sha256"]!=current.fence_sha256
                    or requested["snapshot_id"]!=identity("snapshot",requested["request_id"])
                    or "\r" in markdown or not markdown.strip()
                    or sha256(markdown.encode()).hexdigest()!=requested["content_sha256"]
                    or len(markdown.encode())!=requested["content_size_bytes"]
                    or sha256(encoded.encode()).hexdigest()!=requested["manifest_sha256"]
                    or len(encoded.encode())!=requested["manifest_size_bytes"]
                    or manifest["wiki_sha256"]!=requested["content_sha256"]
                    or type(manifest["wiki_bytes"]) is not int or manifest["wiki_bytes"]!=requested["content_size_bytes"]
                    or any(manifest[key] is not True for key in ("all_original_ranges_retained",
                        "all_detected_table_cells_rendered","all_visual_ownership_retained"))
                    or any(manifest[key] is not False for key in ("semantic_review_performed","semantic_completeness_proven"))):
                raise ValueError
        except (KeyError,ValueError,TypeError,UnicodeError,RecursionError) as exc:
            raise PublicationCommitIntegrityError("The continuation body and manifest are inconsistent.") from exc
        fields = {name:getattr(extraction,name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=requested["content_sha256"],normalized_sha256=requested["content_sha256"],
            size_bytes=requested["content_size_bytes"],normalizer_version="markdown_lf_v1",
            content_md=markdown,content_sha256=requested["content_sha256"],content_blob_hash=None,
            extraction_source=extraction,reuse_targets=kwargs.get("authorized_targets",()),reuse_fence=())
        frozen = FrozenPdfContinuationPublicationSource(**fields,
            pdf_continuation_snapshot_id=requested["snapshot_id"],request_id=requested["request_id"],
            manifest_json=encoded,manifest_sha256=requested["manifest_sha256"],requested_fence=fence,
            metadata_fence=_metadata_fence(connection,binding))
        return replace(frozen,body_seal=self._continuation_seal(frozen))

    def bind_replayed_continuation(self, source, run, markdown, manifest_json):
        if (type(source) is not FrozenPdfContinuationPublicationSource or type(run) is not PreparedVisualRun
                or not hmac.compare_digest(source.body_seal,self._continuation_seal(source))
                or source.content_md!=markdown or source.manifest_json!=manifest_json):
            raise PublicationCommitIntegrityError("The continuation differs from its replayed retained receipts.")
        bound = replace(source,verified_run=run,body_seal="")
        return replace(bound,body_seal=self._continuation_seal(bound))

    def _revalidate_own_candidate(self, connection, draft, source):
        if type(source) is not FrozenPdfContinuationPublicationSource:
            return super()._revalidate_own_candidate(connection,draft,source)
        with self.continuation_repository.publication_authority(source.request_id):
            return self._revalidate_continuation_candidate(connection,draft,source)

    def _revalidate_continuation_candidate(self, connection, draft, source):
        row,fence,_,current = self._continuation_requested(connection,draft)
        run = source.verified_run
        if (source.requested_fence!=fence or source.metadata_fence!=_metadata_fence(connection,draft)
                or source.pdf_continuation_snapshot_id!=row["snapshot_id"] or source.request_id!=row["request_id"]
                or source.manifest_sha256!=row["manifest_sha256"] or type(run) is not PreparedVisualRun
                or run.run_id!=row["parent_run_id"] or run.source.fence_sha256!=current.fence_sha256
                or run.source._leases.token not in self.continuation_repository.original.source_service._active
                or source.extraction_source is None or source.reuse_targets!=draft.targets
                or source.authored_snapshot_id is not None or source.authored_preparation is not None
                or source.reused_extraction_snapshot_id is not None or source.content_blob_hash is not None
                or source.reuse_fence!=() or type(source.content_md) is not str
                or (source.raw_sha256,source.normalized_sha256,source.content_sha256,source.size_bytes,source.normalizer_version)!=
                    (row["content_sha256"],row["content_sha256"],row["content_sha256"],row["content_size_bytes"],"markdown_lf_v1")
                or not hmac.compare_digest(source.body_seal,self._continuation_seal(source))):
            raise PublicationCommitConflictError("The prepared continuation publication changed.")
        try:
            request,_ = self.continuation_repository.bound(connection,source.request_id,run)
            if request["status"]!="ready":
                raise ValueError
        except Exception as exc:
            raise PublicationCommitConflictError("The retained continuation source or completion changed.") from exc
        PublicationCommitRepository._revalidate_frozen_source(connection,draft,source.extraction_source,
            continuation_snapshot_id=source.pdf_continuation_snapshot_id)


class PdfContinuationPublicationCommitRepository(_ContinuationCommit, PdfVisualPublicationCommitRepository):
    pass


class PdfContinuationOfficeHybridPublicationCommitRepository(_ContinuationCommit, OfficeHybridPublicationCommitRepository):
    pass
