"""Explicit role candidate validation with common, cross-kind body selection."""
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import json

from .cross_kind_publication import CrossKindPublicationCommitRepository
from .native_publication_commit import _metadata_fence
from .publication_body_request import has_pptx_role_work, requested_publication_body
from .publication_commit import (
    FrozenPublicationSource, PublicationCommitRepository, _SourceBinding,
    PublicationCommitConflictError, PublicationCommitIntegrityError,
)
from ..services.native_authoring_source import NativeSourceConflict


@dataclass(frozen=True, slots=True)
class FrozenPptxRolePublicationSource(FrozenPublicationSource):
    pptx_role_snapshot_id: str | None = None
    role_manifest_sha256: str | None = None
    requested_fence: tuple = ()
    metadata_fence: str = ""
    body_seal: str = ""


class PptxRolePublicationCommitRepository(CrossKindPublicationCommitRepository):
    def _role_seal(self, source):
        values = []
        for name in FrozenPptxRolePublicationSource.__dataclass_fields__:
            if name == "body_seal":
                continue
            value = getattr(source, name)
            values.append((name, id(value) if name in ("content_md", "extraction_source") else value))
        return hmac.digest(self._preparation_key, repr(values).encode(), "sha256").hex()

    def _role_requested(self, connection, binding, *, verify_source):
        row = connection.execute("SELECT * FROM pptx_role_publication_candidate WHERE preview_id=?",
                                 (binding.preview_id,)).fetchone()
        if (row is None or (row["actor_user_id"], row["artifact_id"], row["extraction_snapshot_id"],
                row["source_snapshot_sha256"]) != (binding.actor_user_id, binding.artifact_id,
                    binding.extraction_snapshot_id, binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The role upload has no complete retained Wiki for publication.")
        identity = requested_publication_body(connection, actor_id=binding.actor_user_id, preview_id=binding.preview_id)
        if identity.kind != "pptx_role_wiki" or identity.snapshot_id != row["snapshot_id"]:
            raise PublicationCommitIntegrityError("The requested role publication identity differs.")
        try:
            if verify_source:
                # Shared extraction/asset authority only. No native authoring
                # plan, native snapshot alias or second model invocation.
                current = self.native_source._snapshot(connection, binding.preview_id,
                    binding.actor_user_id, now_ms=binding.created_at_ms)
                if current.fence_sha256 != row["source_fence_sha256"]:
                    raise PublicationCommitConflictError("The role Wiki source changed.")
            else:
                parent = connection.execute("SELECT parent_job_id FROM pptx_role_workflow_request WHERE preview_id=?",
                                            (binding.preview_id,)).fetchone()
                self.native_source._authority.revalidate_job_authority(connection, parent[0])
        except NativeSourceConflict as exc:
            raise PublicationCommitConflictError(str(exc)) from exc
        return dict(row), tuple(row), identity

    def freeze_source(self, connection, **kwargs):
        if not has_pptx_role_work(connection, kwargs["preview_id"]):
            return super().freeze_source(connection, **kwargs)
        if kwargs.get("authored_snapshot_id") is not None:
            raise PublicationCommitIntegrityError("A client snapshot cannot replace the role candidate.")
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"], kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"], kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        requested, fence, identity = self._role_requested(connection, binding, verify_source=True)
        # The base extraction is retained only as provenance/reference input;
        # the published body is the fully checked role Wiki below.
        source = PublicationCommitRepository.freeze_source(self, connection, **kwargs)
        row = connection.execute("SELECT content_md,manifest_json FROM pptx_role_wiki_snapshot_body WHERE snapshot_id=?",
                                 (requested["snapshot_id"],)).fetchone()
        try:
            if row is None or any(type(value) is not str for value in row):
                raise ValueError
            content, manifest_json = row
            manifest = json.loads(manifest_json)
            if (sha256(content.encode()).hexdigest() != requested["content_sha256"]
                    or len(content.encode()) != requested["content_size_bytes"] or "\r" in content
                    or sha256(manifest_json.encode()).hexdigest() != requested["manifest_sha256"]
                    or len(manifest_json.encode()) != requested["manifest_size_bytes"]
                    or manifest["source"]["snapshot_id"] != binding.extraction_snapshot_id
                    or manifest["source"]["extraction_sha256"] != binding.source_snapshot_sha256
                    or manifest["source"]["format"] != "pptx"
                    or manifest["plan_sha256"] != requested["source_plan_sha256"]
                    or manifest["policy_version"] != identity.policy_version
                    or manifest["wiki_sha256"] != requested["content_sha256"]
                    or type(manifest["wiki_bytes"]) is not int or manifest["wiki_bytes"] != requested["content_size_bytes"]
                    or any(manifest[key] is not True for key in ("all_nonblank_chunks_authored",
                        "all_role_candidates_assigned_once", "all_native_tables_preserved"))
                    or any(manifest[key] is not False for key in ("semantic_review_performed",
                        "semantic_completeness_proven", "caption_ownership_inferred"))):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PublicationCommitIntegrityError("The role Wiki body and manifest are inconsistent.") from exc
        fields = {name: getattr(source, name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=requested["content_sha256"], normalized_sha256=requested["content_sha256"],
            size_bytes=requested["content_size_bytes"], normalizer_version="markdown_lf_v1", content_md=content,
            content_blob_hash=None, content_sha256=requested["content_sha256"], extraction_source=source,
            reuse_targets=kwargs.get("authorized_targets", ()), reuse_fence=())
        prepared = FrozenPptxRolePublicationSource(**fields, pptx_role_snapshot_id=requested["snapshot_id"],
            role_manifest_sha256=requested["manifest_sha256"], requested_fence=fence,
            metadata_fence=_metadata_fence(connection, binding))
        return replace(prepared, body_seal=self._role_seal(prepared))

    def _revalidate_own_candidate(self, connection, draft, source):
        if type(source) is not FrozenPptxRolePublicationSource:
            return super()._revalidate_own_candidate(connection, draft, source)
        requested, fence, _ = self._role_requested(connection, draft, verify_source=False)
        if (source.requested_fence != fence or source.metadata_fence != _metadata_fence(connection, draft)
                or source.pptx_role_snapshot_id != requested["snapshot_id"]
                or source.role_manifest_sha256 != requested["manifest_sha256"]
                or source.extraction_source is None or source.reuse_targets != draft.targets
                or source.authored_snapshot_id is not None or source.reused_extraction_snapshot_id is not None
                or source.authored_preparation is not None or source.content_blob_hash is not None
                or source.reuse_fence != () or type(source.content_md) is not str
                or (source.raw_sha256, source.normalized_sha256, source.content_sha256, source.size_bytes,
                    source.normalizer_version) != (requested["content_sha256"], requested["content_sha256"],
                    requested["content_sha256"], requested["content_size_bytes"], "markdown_lf_v1")
                or type(source.body_seal) is not str or not hmac.compare_digest(source.body_seal, self._role_seal(source))):
            raise PublicationCommitConflictError("The prepared role publication is stale or inconsistent.")
        PublicationCommitRepository._revalidate_frozen_source(connection, draft, source.extraction_source)
