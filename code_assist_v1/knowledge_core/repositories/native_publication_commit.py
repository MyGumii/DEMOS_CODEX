"""Source-bound native Wiki selection using the existing atomic publisher."""
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import json
import secrets
from typing import NamedTuple

from .preserved_publication import PreservedPublicationCommitRepository
from .publication_commit import (FrozenPublicationSource, _SourceBinding,
    PublicationCommitConflictError, PublicationCommitIntegrityError, publication_operation_id)
from ..services.native_authoring_source import CanonicalNativeAuthoringSourceService, NativeSourceConflict

_BODY_KEY = secrets.token_bytes(32)


def _sha(text):
    return sha256(text.encode("utf-8")).hexdigest()


def has_native_request(connection, preview_id):
    exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='native_workflow_request'").fetchone()
    return exists is not None and connection.execute(
        "SELECT 1 FROM native_workflow_request WHERE preview_id=?", (preview_id,)).fetchone() is not None


class _Prior(NamedTuple):
    space_id: str
    item_id: str
    revision_id: str
    lifecycle: str
    curation: str
    row_version: int
    operation_id: str
    snapshot_id: str
    content_sha256: str
    content_size: int
    manifest_sha256: str
    manifest_size: int
    raw_sha256: str
    revision_size: int
    body_sha256: str
    body_size: int
    normalized_sha256: str
    normalizer: str
    original_revision_id: str
    history_count: int
    item_count: int
    invalid_count: int


@dataclass(frozen=True, slots=True)
class FrozenNativePublicationSource(FrozenPublicationSource):
    native_snapshot_id: str | None = None
    native_manifest_sha256: str | None = None
    requested_snapshot_id: str | None = None
    requested_fence: tuple = ()
    native_prior_fence: tuple[_Prior, ...] = ()
    metadata_fence: str = ""
    body_seal: str = ""


def _body_seal(source):
    # The immutable string is retained by the prepared object. Bind its process
    # identity after hashing it in the read phase, not its MB-sized bytes under
    # the SQLite writer lock. A replaced string requires a new valid preparation.
    value = (id(source.content_md), source.native_snapshot_id, source.requested_snapshot_id,
        source.content_sha256, source.size_bytes, source.native_manifest_sha256, source.metadata_fence)
    return hmac.digest(_BODY_KEY, json.dumps(value, separators=(",", ":")).encode(), "sha256").hex()


def _reuse_anchors(prior):
    # The ordinary resolver consumes these first three fields. Fence the exact
    # current revision, including when provenance exists only on an older one.
    return tuple(dict.fromkeys((row.space_id, row.item_id, row.revision_id) for row in prior))


def _metadata_fence(connection, binding):
    """Metadata only; neither model text, extraction nor structure JSON is read."""
    rows = []
    for sql, args in (
        ("SELECT * FROM publication_preview WHERE id=?", (binding.preview_id,)),
        ("SELECT * FROM durable_job WHERE id=(SELECT durable_job_id FROM publication_preview WHERE id=?)", (binding.preview_id,)),
        ("SELECT * FROM source_artifact WHERE id=?", (binding.artifact_id,)),
        ("SELECT * FROM extraction_snapshot WHERE id=?", (binding.extraction_snapshot_id,)),
        ("SELECT * FROM extraction_native_table_structure WHERE snapshot_id=?", (binding.extraction_snapshot_id,)),
        ("SELECT snapshot_id,artifact_id,content_blob_hash,content_size_bytes,content_sha256,created_at FROM extraction_snapshot_body WHERE snapshot_id=?", (binding.extraction_snapshot_id,)),
        ("SELECT snapshot_id,artifact_id,structure_sha256,structure_size_bytes,created_at FROM extraction_native_table_structure_body WHERE snapshot_id=?", (binding.extraction_snapshot_id,)),
        ("""SELECT b.*,bb.content_sha256 AS retained_sha256,bb.created_at AS retained_created_at,
            o.sha256 AS object_sha256,o.size_bytes AS object_size_bytes,o.relative_path,o.verification_status
            FROM artifact_blob b LEFT JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
            LEFT JOIN blob_object o ON o.sha256=bb.content_sha256 WHERE b.artifact_id=? ORDER BY b.ordinal,b.id""", (binding.artifact_id,)),
    ):
        rows.append(tuple(tuple(row) for row in connection.execute(sql, args)))
    return _sha(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


class NativePublicationCommitRepository(PreservedPublicationCommitRepository):
    def __init__(self, native_source, **kwargs):
        if type(native_source) is not CanonicalNativeAuthoringSourceService:
            raise TypeError("Native publication requires its canonical source verifier.")
        super().__init__(**kwargs)
        self.native_source = native_source
        self.database_path = native_source._factory.database_path
        from ..migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
        expected = NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG.migrations
        self._display_publication_enabled = native_source._factory.migration_catalog.migrations[:len(expected)] == expected

    def _batch_policy(self, connection, requested):
        """The exact source family, not a manifest-selected policy or default."""
        from ..migrations.v032_native_display_storage import DISPLAY_POLICY
        row = connection.execute("SELECT json_extract(metadata_json,'$.policy_version') "
            "FROM native_authoring_run WHERE id=?", (requested["run_id"],)).fetchone()
        if row is not None and row[0] == "wiki-native-batch-authoring-v1":
            return row[0]
        if (row is not None and row[0] == DISPLAY_POLICY and self._display_publication_enabled
                and connection.execute("SELECT 1 FROM native_display_publication_snapshot "
                    "WHERE snapshot_id=? AND run_id=? AND preview_id=?",
                    (requested["snapshot_id"], requested["run_id"], requested["preview_id"])).fetchone()):
            return DISPLAY_POLICY
        raise PublicationCommitIntegrityError("The native Wiki publication family is not registered.")

    def _native_requested(self, connection, binding, *, verify_source=True):
        row = connection.execute("SELECT * FROM native_publication_candidate WHERE preview_id=?",
            (binding.preview_id,)).fetchone()
        if (row is None or (row["actor_user_id"], row["artifact_id"], row["extraction_snapshot_id"],
                row["source_snapshot_sha256"]) != (binding.actor_user_id, binding.artifact_id,
                    binding.extraction_snapshot_id, binding.source_snapshot_sha256)):
            raise PublicationCommitConflictError("The native upload has no complete, retained Wiki for publication.")
        if verify_source:
            try:
                current = self.native_source._snapshot(connection, binding.preview_id, binding.actor_user_id,
                    now_ms=binding.created_at_ms)
            except NativeSourceConflict as exc:
                raise PublicationCommitConflictError(str(exc)) from exc
            if current.fence_sha256 != row["source_fence_sha256"]:
                raise PublicationCommitConflictError("The completed native Wiki source changed.")
        else:
            self.native_source._authority.revalidate_job_authority(connection,
                connection.execute("SELECT parent_job_id FROM native_workflow_request WHERE preview_id=?",
                    (binding.preview_id,)).fetchone()[0])
        return dict(row), tuple(row)

    @staticmethod
    def _native_prior(connection, binding, targets, policy_sha256):
        """Return <=2 summaries, never all history rows to Python.

        history_count fences and discloses the entire matched metadata history.
        SQL still inspects that history; this is not a constant-total-work or
        multi-client latency guarantee. No previous private candidate body is read.
        """
        if not targets or len(targets) > 2 or len({t.space_id for t in targets}) != len(targets):
            raise PublicationCommitConflictError("Native publication requires current authorized targets.")
        from ..migrations.v014_wiki_authoring import _EQUIVALENT_UPLOAD
        marks = ",".join("?" for _ in targets)
        rows = connection.execute(f"""WITH matches AS (SELECT DISTINCT t.target_space_id AS space_id,
            t.item_id,i.current_revision_id AS revision_id,i.lifecycle_status AS lifecycle,
            i.curation_status AS curation,i.row_version,p.id AS operation_id,sel.native_snapshot_id AS snapshot_id,
            s.content_sha256,s.content_size_bytes AS content_size,s.manifest_sha256,s.manifest_size_bytes AS manifest_size,
            v.raw_content_sha256 AS raw_sha256,v.content_size_bytes AS revision_size,
            b.content_sha256 AS body_sha256,b.content_size_bytes AS body_size,
            v.normalized_content_sha256 AS normalized_sha256,v.normalizer_version AS normalizer,
            t.resolved_revision_id AS original_revision_id,p.created_at AS operation_created
            FROM source_artifact a JOIN extraction_snapshot x ON x.artifact_id=a.id AND x.id=?
            JOIN source_artifact prior ON prior.id<>a.id
            JOIN extraction_snapshot px ON px.artifact_id=prior.id
            JOIN publication_operation p ON p.artifact_id=prior.id AND p.extraction_snapshot_id=px.id
            JOIN publication_target t ON t.publication_operation_id=p.id AND t.outcome<>'update_proposed'
            JOIN publication_native_selection sel ON sel.publication_operation_id=p.id
            JOIN native_wiki_snapshot s ON s.id=sel.native_snapshot_id
            JOIN native_authoring_run r ON r.id=s.run_id
            JOIN native_authoring_state state ON state.run_id=r.id AND state.status='ready'
            JOIN native_workflow_request q ON q.preview_id=r.preview_id AND q.policy_sha256=?
            JOIN knowledge_item i ON i.id=t.item_id
            JOIN knowledge_revision v ON v.id=i.current_revision_id AND v.item_id=i.id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=i.id
            WHERE a.id=? AND {_EQUIVALENT_UPLOAD} AND t.target_space_id IN ({marks})),
            summaries AS (SELECT space_id,count(*) AS history_count,count(DISTINCT item_id) AS item_count,
              sum(CASE WHEN lifecycle<>'active' OR curation NOT IN ('published','provisional')
                OR raw_sha256 IS NOT content_sha256 OR revision_size IS NOT content_size
                OR body_sha256 IS NOT content_sha256 OR body_size IS NOT content_size
                OR normalized_sha256 IS NOT content_sha256 OR normalizer IS NOT 'markdown_lf_v1'
                THEN 1 ELSE 0 END) AS invalid_count FROM matches GROUP BY space_id),
            ranked AS (SELECT *,row_number() OVER(PARTITION BY space_id
                ORDER BY operation_created,operation_id,item_id,snapshot_id) AS choice FROM matches)
            SELECT r.space_id,r.item_id,r.revision_id,r.lifecycle,r.curation,r.row_version,r.operation_id,
              r.snapshot_id,r.content_sha256,r.content_size,r.manifest_sha256,r.manifest_size,r.raw_sha256,
              r.revision_size,r.body_sha256,r.body_size,r.normalized_sha256,r.normalizer,r.original_revision_id,
              totals.history_count,totals.item_count,totals.invalid_count
            FROM ranked r JOIN summaries totals ON totals.space_id=r.space_id WHERE r.choice=1
            ORDER BY r.operation_created,r.operation_id,r.space_id,r.item_id""",
            (binding.extraction_snapshot_id, policy_sha256, binding.artifact_id,
                *(t.space_id for t in targets))).fetchall()
        result = tuple(_Prior(*row) for row in rows)
        by_space = {}
        for row in result:
            by_space.setdefault(row.space_id, set()).add((row.item_id, row.revision_id))
            if (row.invalid_count or row.lifecycle != "active" or row.curation not in {"published", "provisional"}
                    or (row.raw_sha256, row.revision_size, row.body_sha256, row.body_size,
                        row.normalized_sha256, row.normalizer) != (row.content_sha256, row.content_size,
                            row.content_sha256, row.content_size, row.content_sha256, "markdown_lf_v1")):
                raise PublicationCommitConflictError("A prior native Wiki was edited, deactivated or is unavailable; use an explicit update.")
        if any(row.item_count != 1 for row in result) or any(len(ids) > 1 for ids in by_space.values()):
            raise PublicationCommitConflictError("More than one prior native Wiki matches a target.")
        if len({(r.content_sha256, r.content_size) for r in result}) > 1:
            raise PublicationCommitConflictError("Prior native target Wikis disagree; use an explicit update.")
        return result

    def freeze_source(self, connection, **kwargs):
        if not has_native_request(connection, kwargs["preview_id"]):
            return super().freeze_source(connection, **kwargs)
        from ..services.preserved_publication import has_preservation_request
        from ..services.wiki_publication import WikiPublicationSelector
        if (kwargs.get("authored_snapshot_id") is not None
                or has_preservation_request(connection, kwargs["preview_id"])
                or WikiPublicationSelector.has_request(connection, kwargs["preview_id"])):
            raise PublicationCommitConflictError("One upload cannot belong to multiple authoring policies.")
        source = super().freeze_source(connection, **kwargs)
        binding = _SourceBinding(kwargs["actor_user_id"], kwargs["preview_id"], kwargs["artifact_id"],
            kwargs["extraction_snapshot_id"], kwargs["source_snapshot_sha256"], kwargs["effective_at_ms"])
        requested, fence = self._native_requested(connection, binding)
        batch_policy = self._batch_policy(connection, requested)
        targets = kwargs.get("authorized_targets", ())
        prior = self._native_prior(connection, binding, targets, requested["policy_sha256"])
        # Always validate the newly completed output, even if dedupe later chooses
        # the prior published revision. Neither an incomplete run nor raw text wins.
        body = connection.execute("SELECT content_md,manifest_json FROM native_wiki_snapshot_body WHERE snapshot_id=?",
            (requested["snapshot_id"],)).fetchone()
        try:
            if body is None:
                raise ValueError
            content, encoded_manifest = body
            manifest = json.loads(encoded_manifest)
            if (_sha(encoded_manifest) != requested["manifest_sha256"]
                    or len(encoded_manifest.encode()) != requested["manifest_size_bytes"]
                    or manifest["source"]["snapshot_id"] != binding.extraction_snapshot_id
                    or manifest["source"]["extraction_sha256"] != binding.source_snapshot_sha256
                    or manifest["plan_sha256"] != requested["source_plan_sha256"]
                    or manifest["wiki_sha256"] != requested["content_sha256"]
                    or type(manifest["wiki_bytes"]) is not int or manifest["wiki_bytes"] != requested["content_size_bytes"]
                    or manifest["policy_version"] != batch_policy
                    or manifest["all_prose_chunks_authored"] is not True
                    or manifest["all_native_table_slots_preserved"] is not True
                    or manifest["semantic_review_performed"] is not False
                    or manifest["semantic_completeness_proven"] is not False
                    or manifest["unseen_visual_content_reviewed"] is not False
                    or _sha(content) != requested["content_sha256"]
                    or len(content.encode()) != requested["content_size_bytes"] or "\r" in content):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PublicationCommitIntegrityError("The native Wiki body/manifest is inconsistent.") from exc
        selected, digest, size, manifest_sha = (requested["snapshot_id"], requested["content_sha256"],
            requested["content_size_bytes"], requested["manifest_sha256"])
        if prior:
            chosen = prior[0]
            selected, digest, size, manifest_sha = (chosen.snapshot_id, chosen.content_sha256,
                chosen.content_size, chosen.manifest_sha256)
            body = connection.execute("SELECT content_md FROM knowledge_revision_body WHERE revision_id=? AND item_id=?",
                (chosen.revision_id, chosen.item_id)).fetchone()
            if body is None or type(body[0]) is not str:
                raise PublicationCommitConflictError("The prior native Wiki body is unavailable.")
            content = body[0]
        if type(content) is not str or "\r" in content or _sha(content) != digest or len(content.encode()) != size:
            raise PublicationCommitIntegrityError("Selected native Wiki bytes disagree with their immutable identity.")
        fields = {name: getattr(source, name) for name in FrozenPublicationSource.__dataclass_fields__}
        fields.update(raw_sha256=digest, normalized_sha256=digest, size_bytes=size,
            normalizer_version="markdown_lf_v1", content_md=content, content_blob_hash=None,
            content_sha256=digest, extraction_source=source, reuse_targets=targets,
            reuse_fence=_reuse_anchors(prior))
        result = FrozenNativePublicationSource(**fields, native_snapshot_id=selected,
            native_manifest_sha256=manifest_sha, requested_snapshot_id=requested["snapshot_id"],
            requested_fence=fence, native_prior_fence=prior, metadata_fence=_metadata_fence(connection, binding))
        return replace(result, body_seal=_body_seal(result))

    def _revalidate_frozen_source(self, connection, draft, source):
        if not isinstance(source, FrozenNativePublicationSource):
            return super()._revalidate_frozen_source(connection, draft, source)
        requested, fence = self._native_requested(connection, draft, verify_source=False)
        prior = self._native_prior(connection, draft, draft.targets, requested["policy_sha256"])
        expected = ((prior[0].snapshot_id, prior[0].content_sha256, prior[0].content_size, prior[0].manifest_sha256)
            if prior else (requested["snapshot_id"], requested["content_sha256"],
                requested["content_size_bytes"], requested["manifest_sha256"]))
        if (fence != source.requested_fence or prior != source.native_prior_fence
                or source.requested_snapshot_id != requested["snapshot_id"] or source.reuse_targets != draft.targets
                or source.reuse_fence != _reuse_anchors(prior)
                or source.extraction_source is None or source.authored_snapshot_id is not None
                or source.reused_extraction_snapshot_id is not None or source.authored_preparation is not None
                or (source.native_snapshot_id, source.content_sha256, source.size_bytes,
                    source.native_manifest_sha256) != expected
                or source.raw_sha256 != expected[1] or source.normalized_sha256 != expected[1]
                or source.normalizer_version != "markdown_lf_v1" or source.content_blob_hash is not None
                or type(source.content_md) is not str or source.metadata_fence != _metadata_fence(connection, draft)
                or not hmac.compare_digest(source.body_seal, _body_seal(source))):
            raise PublicationCommitConflictError("Native publication preparation is stale or inconsistent.")
        super()._revalidate_frozen_source(connection, draft, source.extraction_source)

    def _commit_source_selection(self, connection, draft, source, operation_id, failpoint):
        if not isinstance(source, FrozenNativePublicationSource):
            return super()._commit_source_selection(connection, draft, source, operation_id, failpoint)
        self._verify_selected_bodies(connection, operation_id, source.content_sha256, source.size_bytes)
        connection.execute("""INSERT INTO publication_native_selection(
            publication_operation_id,native_snapshot_id,requested_snapshot_id,source_publication_operation_id)
            VALUES(?,?,?,?)""", (operation_id, source.native_snapshot_id, source.requested_snapshot_id,
                source.native_prior_fence[0].operation_id if source.native_prior_fence else None))
        if failpoint:
            failpoint("publication.after_native_selection")

    def commit(self, connection, draft, *, failpoint=None):
        result = super().commit(connection, draft, failpoint=failpoint)
        if isinstance(draft.frozen_source, FrozenNativePublicationSource):
            return replace(result, native_snapshot_id=draft.frozen_source.native_snapshot_id)
        return result

    def require_exact_replay(self, connection, *, native_snapshot_id=None, **kwargs):
        if native_snapshot_id is None:
            if has_native_request(connection, kwargs["preview_id"]):
                raise PublicationCommitIntegrityError("Native publication replay requires its exact selection.")
            return super().require_exact_replay(connection, **kwargs)
        if any(kwargs.get(key) is not None for key in
                ("authored_snapshot_id", "preserved_snapshot_id", "reused_extraction_snapshot_id", "requested_authored_snapshot_id")):
            raise PublicationCommitIntegrityError("Native replay cannot mix selection lineages.")
        operation = publication_operation_id(kwargs["actor_user_id"], kwargs["operation_id"])
        row = connection.execute("""SELECT s.content_sha256,s.content_size_bytes FROM publication_native_selection sel
            JOIN native_wiki_snapshot s ON s.id=sel.native_snapshot_id
            JOIN native_wiki_snapshot requested ON requested.id=sel.requested_snapshot_id
            JOIN native_authoring_run r ON r.id=requested.run_id
            JOIN native_authoring_state state ON state.run_id=r.id AND state.status='ready'
            JOIN native_workflow_request q ON q.preview_id=r.preview_id
            JOIN publication_operation p ON p.id=sel.publication_operation_id
            WHERE p.id=? AND s.id=? AND r.preview_id=? AND r.actor_user_id=p.actor_user_id
              AND r.artifact_id=p.artifact_id AND r.extraction_snapshot_id=p.extraction_snapshot_id""",
            (operation, native_snapshot_id, kwargs["preview_id"])).fetchone()
        if row is None:
            raise PublicationCommitIntegrityError("Native publication replay lineage is missing.")
        result = super().require_exact_replay(connection, **kwargs)
        self._verify_selected_bodies(connection, operation, *row, retained=False)
        return replace(result, native_snapshot_id=native_snapshot_id)
