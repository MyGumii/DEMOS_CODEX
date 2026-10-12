"""Separate v20+ publication lineage for source-bound Wiki bodies.

Legacy extraction/authored replay delegates unchanged to the base repository.
Only this opt-in repository can freeze a preserved body; clients cannot choose
its snapshot. All current publication authority remains in PublicationService.
"""
from dataclasses import dataclass, replace
import hashlib
import json

from ..db import db_now_ms
from ..wiki_preservation import (AUTHORING_ONLY_VERSIONS, PRESERVATION_VERSION,
    STRUCTURED_AUTHORING_VERSIONS, BOUNDARY_AUTHORING_VERSIONS)
from .publication_commit import (
    PublicationCommitRepository, FrozenPublicationSource, PublicationCommitConflictError,
    PublicationCommitIntegrityError, _SourceBinding, publication_operation_id,
)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenPreservedSource(FrozenPublicationSource):
    preserved_snapshot_id: str | None = None
    preserved_fence: tuple = ()
    requested_preserved_snapshot_id: str | None = None
    requested_preserved_fence: tuple = ()
    preservation_prior_fence: tuple = ()


class PreservedPublicationCommitRepository(PublicationCommitRepository):
    @staticmethod
    def _requested(connection, binding):
        row = connection.execute("""SELECT s.id,r.id,r.row_version,r.plan_sha256,
            s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes,s.created_at,
            p.row_version,u.scope_epoch,r.policy_version
            FROM publication_preservation_run r JOIN preserved_wiki_snapshot s ON s.run_id=r.id
            JOIN preserved_wiki_snapshot_body b ON b.snapshot_id=s.id
            JOIN publication_preview p ON p.id=r.preview_id
            JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
            JOIN publication_preservation_source_result o ON o.preview_id=p.id AND o.run_id=r.id AND o.outcome='planned'
            JOIN durable_job j ON j.id=r.parent_job_id AND j.state='succeeded' AND j.cancel_requested=0
            WHERE p.id=? AND r.actor_user_id=? AND r.artifact_id=? AND r.extraction_snapshot_id=?
              AND r.source_sha256=? AND r.status='ready' AND p.status='ready' AND p.expires_at>?
              AND p.extraction_snapshot_id=r.extraction_snapshot_id AND p.artifact_id=r.artifact_id
              AND p.source_snapshot_sha256=r.source_sha256""",
            (binding.preview_id, binding.actor_user_id, binding.artifact_id, binding.extraction_snapshot_id,
             binding.source_snapshot_sha256, max(binding.created_at_ms, db_now_ms(connection)))).fetchone()
        if row is None:
            raise PublicationCommitConflictError("The upload has no ready, source-bound preserved Wiki.")
        return row[0], tuple(row)[1:]

    @staticmethod
    def _prior(connection, binding, targets, policy_version=PRESERVATION_VERSION):
        if not targets:
            raise PublicationCommitConflictError("Preservation requires authorized publication targets.")
        if policy_version not in (PRESERVATION_VERSION, *AUTHORING_ONLY_VERSIONS):
            raise PublicationCommitIntegrityError(
                "The requested preservation policy is unsupported."
            )
        from ..migrations.v014_wiki_authoring import _EQUIVALENT_UPLOAD
        placeholders = ",".join("?" for _ in targets)
        rows = connection.execute(f"""SELECT DISTINCT t.target_space_id,t.item_id,i.current_revision_id,
            i.lifecycle_status,i.curation_status,i.row_version,p.id,sel.preserved_snapshot_id,
            s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes,s.created_at,
            r.id,r.row_version,r.plan_sha256,v.raw_content_sha256,v.content_size_bytes,
            b.content_sha256,b.content_size_bytes,v.normalized_content_sha256,v.normalizer_version,
            r.policy_version
            FROM source_artifact a JOIN extraction_snapshot x ON x.artifact_id=a.id AND x.id=?
            JOIN source_artifact prior ON prior.id<>a.id
            JOIN extraction_snapshot px ON px.artifact_id=prior.id
            JOIN publication_operation p ON p.artifact_id=prior.id AND p.extraction_snapshot_id=px.id
            JOIN publication_target t ON t.publication_operation_id=p.id AND t.outcome<>'update_proposed'
            JOIN publication_preserved_selection sel ON sel.publication_operation_id=p.id
            JOIN preserved_wiki_snapshot s ON s.id=sel.preserved_snapshot_id
            JOIN publication_preservation_run r ON r.id=s.run_id AND r.status='ready'
            JOIN knowledge_item i ON i.id=t.item_id
            JOIN knowledge_revision v ON v.id=i.current_revision_id AND v.item_id=i.id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=i.id
            WHERE a.id=? AND {_EQUIVALENT_UPLOAD} AND r.policy_version=?
              AND t.target_space_id IN ({placeholders})
            ORDER BY p.created_at,p.id,t.target_space_id,t.item_id""",
            (
                binding.extraction_snapshot_id,
                binding.artifact_id,
                policy_version,
                *(t.space_id for t in targets),
            )).fetchall()
        result = tuple(tuple(r) for r in rows)
        identities_by_space = {}
        for row in result:
            identities_by_space.setdefault(row[0], set()).add((row[1], row[2]))
        if any(len(identities) > 1 for identities in identities_by_space.values()):
            raise PublicationCommitConflictError("More than one prior Wiki matches the source in a target space.")
        for row in result:
            if (row[3] != "active" or row[4] not in {"published", "provisional"}
                    or row[16:20] != (row[8], row[9], row[8], row[9])
                    or row[20:22] != (row[8], "markdown_lf_v1")):
                raise PublicationCommitConflictError("The previously published source was edited or is unavailable.")
        if len({(r[8], r[9]) for r in result}) > 1:
            raise PublicationCommitConflictError("Prior target Wikis disagree; an explicit update is required.")
        return result

    def freeze_source(self, connection, **kwargs):
        # Authorization and extraction/source hashing are the existing contract.
        source = super().freeze_source(connection, **kwargs)
        exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_preservation_request'").fetchone()
        if exists is None or connection.execute("SELECT 1 FROM publication_preservation_request WHERE preview_id=?",
                (kwargs["preview_id"],)).fetchone() is None:
            return source
        if kwargs.get("authored_snapshot_id") is not None or source.origin_type != "upload":
            raise PublicationCommitConflictError("Preservation only owns its own uploaded source.")
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"], kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"], kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        requested, requested_fence = self._requested(connection, binding)
        policy_version = requested_fence[-1]
        targets = kwargs.get("authorized_targets", ())
        prior = self._prior(connection, binding, targets, policy_version)
        selected, fence = requested, requested_fence
        if prior:
            row = prior[0]
            selected = row[7]
            fence = (
                row[13],
                row[14],
                row[15],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[22],
            )
            body = connection.execute("SELECT content_md FROM knowledge_revision_body WHERE revision_id=? AND item_id=?",
                (row[2], row[1])).fetchone()
            if body is None:
                raise PublicationCommitConflictError("The previously preserved Wiki body is unavailable.")
            content = body[0]
        else:
            row = connection.execute("SELECT content_md,manifest_json FROM preserved_wiki_snapshot_body WHERE snapshot_id=?",
                (selected,)).fetchone()
            if row is None:
                raise PublicationCommitConflictError("Preserved Wiki body was purged.")
            content, manifest = row
            try:
                value = json.loads(manifest)
                if (_sha(manifest) != fence[5] or len(manifest.encode()) != fence[6]
                        or value["source_snapshot_id"] != binding.extraction_snapshot_id
                        or value["source_sha256"] != binding.source_snapshot_sha256
                        or value["wiki_sha256"] != fence[3]):
                    raise ValueError
                if policy_version == PRESERVATION_VERSION:
                    if value["all_extracted_chunks_reviewed"] is not True:
                        raise ValueError
                elif policy_version in AUTHORING_ONLY_VERSIONS:
                    if (
                        value.get("all_extracted_chunks_authored") is not True
                        or value["all_extracted_chunks_reviewed"] is not False
                        or value.get("semantic_review_performed") is not False
                        or value.get("semantic_completeness_proven") is not False
                        or value.get("unseen_visual_content_reviewed") is not False
                        or value.get("validation_mode")
                        != "source_bound_authoring_only"
                    ):
                        raise ValueError
                    if policy_version in STRUCTURED_AUTHORING_VERSIONS:
                        from ..wiki_source_structure import SOURCE_STRUCTURE_VERSION
                        if (value.get("source_structure_version") != SOURCE_STRUCTURE_VERSION
                                or not isinstance(value.get("source_outline"), list)
                                or value.get("structure_semantics") != "exact_extracted_markdown_locations_not_fact_entailment"):
                            raise ValueError
                    if policy_version in BOUNDARY_AUTHORING_VERSIONS:
                        from ..wiki_authoring import AuthoringChunk
                        from ..wiki_boundary_context import validate_context_manifest
                        if not isinstance(source.content_md, str):
                            raise ValueError
                        stored_chunks = connection.execute(
                            """SELECT ordinal,chunk_id,source_start,source_end,source_sha256
                               FROM publication_preservation_chunk WHERE run_id=? ORDER BY ordinal""",
                            (fence[0],)).fetchall()
                        chunks = tuple(AuthoringChunk(*tuple(c), source.content_md[c[2]:c[3]])
                            for c in stored_chunks)
                        validate_context_manifest(value, chunks, source.content_md)
                else:
                    raise ValueError
            except (ValueError, TypeError, KeyError) as exc:
                raise PublicationCommitIntegrityError("Preserved Wiki manifest is inconsistent.") from exc
        if not isinstance(content, str) or "\r" in content or _sha(content) != fence[3] or len(content.encode()) != fence[4]:
            raise PublicationCommitIntegrityError("Preserved Wiki content is inconsistent.")
        if (self._requested(connection, binding) != (requested, requested_fence)
                or self._prior(connection, binding, targets, policy_version) != prior):
            raise PublicationCommitConflictError("Preserved source changed during preparation.")
        values = {name: getattr(source, name) for name in FrozenPublicationSource.__dataclass_fields__}
        values.update(raw_sha256=fence[3], normalized_sha256=fence[3], size_bytes=fence[4],
            normalizer_version="markdown_lf_v1", content_md=content, content_blob_hash=None,
            content_sha256=fence[3], extraction_source=source, reuse_targets=targets)
        return FrozenPreservedSource(**values, preserved_snapshot_id=selected, preserved_fence=fence,
            requested_preserved_snapshot_id=requested, requested_preserved_fence=requested_fence,
            preservation_prior_fence=prior)

    def _revalidate_frozen_source(self, connection, draft, source):
        if not isinstance(source, FrozenPreservedSource):
            return super()._revalidate_frozen_source(connection, draft, source)
        if (source.extraction_source is None or source.authored_snapshot_id is not None
                or source.reused_extraction_snapshot_id is not None or source.authored_preparation is not None
                or source.reuse_targets != draft.targets
                or self._requested(connection, draft) != (source.requested_preserved_snapshot_id, source.requested_preserved_fence)
                or self._prior(
                    connection,
                    draft,
                    draft.targets,
                    source.requested_preserved_fence[-1],
                )
                != source.preservation_prior_fence):
            raise PublicationCommitConflictError("Preserved publication preparation is stale.")
        if source.preservation_prior_fence:
            row = source.preservation_prior_fence[0]
            expected_id = row[7]
            expected = (
                row[13],
                row[14],
                row[15],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[22],
            )
        else:
            expected_id, expected = source.requested_preserved_snapshot_id, source.requested_preserved_fence
        if (source.preserved_snapshot_id != expected_id or source.preserved_fence != expected
                or (source.content_sha256, source.raw_sha256, source.normalized_sha256, source.size_bytes)
                != (expected[3], expected[3], expected[3], expected[4])
                or source.normalizer_version != "markdown_lf_v1" or source.content_blob_hash is not None):
            raise PublicationCommitConflictError("Selected preserved body binding is inconsistent.")
        super()._revalidate_frozen_source(connection, draft, source.extraction_source)

    def commit(self, connection, draft, *, failpoint=None):
        result = super().commit(connection, draft, failpoint=failpoint)
        source = draft.frozen_source
        if not isinstance(source, FrozenPreservedSource):
            return result
        return replace(result, preserved_snapshot_id=source.preserved_snapshot_id)

    def _commit_source_selection(self, connection, draft, source, operation_id, failpoint):
        if not isinstance(source, FrozenPreservedSource):
            return
        self._verify_selected_bodies(connection, operation_id, source.content_sha256, source.size_bytes)
        connection.execute("""INSERT INTO publication_preserved_selection(
            publication_operation_id,preserved_snapshot_id,requested_snapshot_id,source_publication_operation_id)
            VALUES(?,?,?,?)""", (operation_id, source.preserved_snapshot_id,
            source.requested_preserved_snapshot_id,
            source.preservation_prior_fence[0][6] if source.preservation_prior_fence else None))
        if failpoint:
            failpoint("publication.after_preserved_selection")

    def require_exact_replay(self, connection, *, preserved_snapshot_id=None, **kwargs):
        operation = publication_operation_id(kwargs["actor_user_id"], kwargs["operation_id"])
        selection = connection.execute("SELECT preserved_snapshot_id FROM publication_preserved_selection WHERE publication_operation_id=?",
            (operation,)).fetchone()
        if (selection is None) != (preserved_snapshot_id is None) or (selection is not None and selection[0] != preserved_snapshot_id):
            raise PublicationCommitIntegrityError("Preserved replay requires its exact immutable selection.")
        result = super().require_exact_replay(connection, **kwargs)
        if preserved_snapshot_id is None:
            return result
        row = connection.execute("""SELECT s.content_sha256,s.content_size_bytes FROM publication_preserved_selection p
            JOIN preserved_wiki_snapshot s ON s.id=p.preserved_snapshot_id
            JOIN preserved_wiki_snapshot requested ON requested.id=p.requested_snapshot_id
            JOIN publication_preservation_run r ON r.id=requested.run_id
            JOIN publication_operation op ON op.id=p.publication_operation_id
            WHERE p.publication_operation_id=? AND p.preserved_snapshot_id=? AND r.preview_id=?
              AND r.actor_user_id=op.actor_user_id AND r.artifact_id=op.artifact_id
              AND r.extraction_snapshot_id=op.extraction_snapshot_id AND r.status='ready'""",
            (operation, preserved_snapshot_id, kwargs["preview_id"])).fetchone()
        if row is None:
            raise PublicationCommitIntegrityError("Preserved publication replay lineage is missing.")
        self._verify_selected_bodies(connection, operation, *row, retained=False)
        return replace(result, preserved_snapshot_id=preserved_snapshot_id)
