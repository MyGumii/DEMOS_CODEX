"""Bind a completed Office native/page Wiki to the shared atomic publisher.

Receipt replay and original-file leasing happen in the owning service before
the writer transaction. This repository only compares the sealed candidate,
current source/export authority, and the common publication selection.
"""

from dataclasses import dataclass, replace
import hmac

from .native_publication_commit import _metadata_fence
from .pdf_visual_publication_commit import PdfVisualPublicationCommitRepository
from .publication_body_request import requested_publication_body
from .publication_commit import (FrozenPublicationSource, PublicationCommitRepository,
    PublicationCommitConflictError, PublicationCommitIntegrityError, _SourceBinding)
from ..services.office_hybrid_publication_source import (
    OfficeHybridPublicationSourceConflict, OfficeHybridPublicationSourceService,
    PreparedOfficeHybridPublicationSource,
)
from ..wiki_authoring import _hash


@dataclass(frozen=True, slots=True)
class FrozenOfficeHybridPublicationSource(FrozenPublicationSource):
    office_hybrid_snapshot_id: str | None = None
    manifest_sha256: str = ""
    requested_fence: tuple = ()
    metadata_fence: str = ""
    prepared: PreparedOfficeHybridPublicationSource | None = None
    body_seal: str = ""


class OfficeHybridPublicationCommitRepository(PdfVisualPublicationCommitRepository):
    def __init__(self, native_source, public_bodies, pdf_source,
            pdf_visual_source, visual_archive, office_source):
        super().__init__(native_source, public_bodies, pdf_source,
            pdf_visual_source, visual_archive)
        if type(office_source) is not OfficeHybridPublicationSourceService:
            raise TypeError("Office publication needs its exact source verifier.")
        self.office_source = office_source

    @staticmethod
    def _has_office_request(connection, preview_id):
        return connection.execute("SELECT 1 FROM office_hybrid_upload_request WHERE preview_id=?",
            (preview_id,)).fetchone() is not None

    def _seal_office(self, source):
        values = []
        for name in FrozenOfficeHybridPublicationSource.__dataclass_fields__:
            if name == "body_seal":
                continue
            value = getattr(source, name)
            values.append((name, id(value) if name in ("content_md", "extraction_source", "prepared") else value))
        return hmac.digest(self._preparation_key, repr(values).encode(), "sha256").hex()

    @staticmethod
    def _candidate(connection, binding):
        row = connection.execute("SELECT * FROM office_hybrid_publication_candidate WHERE preview_id=?",
            (binding.preview_id,)).fetchone()
        if (row is None or (row["actor_user_id"], row["artifact_id"],
                row["extraction_snapshot_id"], row["source_snapshot_sha256"]) !=
                (binding.actor_user_id, binding.artifact_id,
                    binding.extraction_snapshot_id, binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The Office upload has no complete retained Wiki for publication.")
        requested = requested_publication_body(connection, actor_id=binding.actor_user_id,
            preview_id=binding.preview_id)
        if requested.kind != "office_hybrid_wiki" or requested.snapshot_id != row["snapshot_id"]:
            raise PublicationCommitIntegrityError("The requested Office publication identity differs.")
        return dict(row), tuple(row)

    def freeze_source(self, connection, **kwargs):
        if not self._has_office_request(connection, kwargs["preview_id"]):
            return super().freeze_source(connection, **kwargs)
        if kwargs.get("authored_snapshot_id") is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace the Office candidate.")
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"],
            kwargs["artifact_id"], kwargs["extraction_snapshot_id"],
            kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        candidate, fence = self._candidate(connection, binding)
        extraction = PublicationCommitRepository.freeze_source(self, connection, **kwargs)
        row = connection.execute("""SELECT content_md,manifest_json FROM office_hybrid_wiki_snapshot
            WHERE preview_id=? AND job_id=?""", (binding.preview_id, candidate["job_id"])).fetchone()
        if (row is None or type(row["content_md"]) is not str
                or type(row["manifest_json"]) is not str
                or "\r" in row["content_md"]
                or _hash(row["content_md"]) != candidate["content_sha256"]
                or _hash(row["manifest_json"]) != candidate["manifest_sha256"]):
            raise PublicationCommitIntegrityError("The Office Wiki body and manifest are inconsistent.")
        fields = {name: getattr(extraction, name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=candidate["content_sha256"],
            normalized_sha256=candidate["content_sha256"],
            size_bytes=len(row["content_md"].encode("utf-8")),
            normalizer_version="markdown_lf_v1", content_md=row["content_md"],
            content_blob_hash=None, content_sha256=candidate["content_sha256"],
            extraction_source=extraction, reuse_targets=kwargs.get("authorized_targets", ()),
            reuse_fence=())
        prepared = FrozenOfficeHybridPublicationSource(**fields,
            office_hybrid_snapshot_id=binding.preview_id,
            manifest_sha256=candidate["manifest_sha256"],
            requested_fence=fence, metadata_fence=_metadata_fence(connection, binding))
        return replace(prepared, body_seal=self._seal_office(prepared))

    def bind_replayed_source(self, source, prepared):
        if (type(source) is not FrozenOfficeHybridPublicationSource
                or type(prepared) is not PreparedOfficeHybridPublicationSource
                or not hmac.compare_digest(source.body_seal, self._seal_office(source))
                or source.office_hybrid_snapshot_id != prepared.source.preview_id
                or source.content_md != prepared.content_md
                or source.content_sha256 != prepared.content_sha256
                or source.manifest_sha256 != prepared.manifest_sha256):
            raise PublicationCommitIntegrityError("The Office publication differs from its replayed writer receipts.")
        bound = replace(source, prepared=prepared, body_seal="")
        return replace(bound, body_seal=self._seal_office(bound))

    def _revalidate_own_candidate(self, connection, draft, source):
        if type(source) is not FrozenOfficeHybridPublicationSource:
            return super()._revalidate_own_candidate(connection, draft, source)
        candidate, fence = self._candidate(connection, draft)
        prepared = source.prepared
        if (source.requested_fence != fence
                or source.metadata_fence != _metadata_fence(connection, draft)
                or source.office_hybrid_snapshot_id != candidate["snapshot_id"]
                or source.manifest_sha256 != candidate["manifest_sha256"]
                or type(prepared) is not PreparedOfficeHybridPublicationSource
                or prepared.job_id != candidate["job_id"]
                or prepared.source.preview_id != draft.preview_id
                or prepared.source.actor_user_id != draft.actor_user_id
                or source.extraction_source is None or source.reuse_targets != draft.targets
                or source.authored_snapshot_id is not None
                or source.reused_extraction_snapshot_id is not None
                or source.authored_preparation is not None
                or source.content_blob_hash is not None or source.reuse_fence != ()
                or source.content_md != prepared.content_md
                or source.content_sha256 != prepared.content_sha256
                or (source.raw_sha256, source.normalized_sha256,
                    source.size_bytes, source.normalizer_version) !=
                    (candidate["content_sha256"], candidate["content_sha256"],
                        len(prepared.content_md.encode("utf-8")), "markdown_lf_v1")
                or not hmac.compare_digest(source.body_seal, self._seal_office(source))):
            raise PublicationCommitConflictError("The prepared Office publication is stale or inconsistent.")
        try:
            self.office_source.revalidate_in_transaction(connection, prepared)
        except (OfficeHybridPublicationSourceConflict, PermissionError) as exc:
            raise PublicationCommitConflictError("Office source or export permission changed before publication.") from exc
        PublicationCommitRepository._revalidate_frozen_source(connection, draft,
            source.extraction_source)


__all__ = ["OfficeHybridPublicationCommitRepository", "FrozenOfficeHybridPublicationSource"]
