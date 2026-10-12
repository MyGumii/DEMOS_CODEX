"""Common, metadata-only history for the successor upload publication composer.

This is not an authorizer or a publication receipt. The caller must first resolve
PUBLISH on each *native* target and validate its own complete candidate. It must
then revalidate this preparation in its writer transaction before using it.
Prior private source/authoring/manifest bodies are never a reuse authority.

The SQL aggregates every equivalent-source history before choosing a row. Python
receives at most two rows; total SQLite history work is NOT constant or capped.
Committed legacy and typed successor receipts are flattened to their actual
body and stable provenance root. This module does not activate the composer.
"""
from dataclasses import dataclass
from hashlib import sha256

from ..db import ManagedConnection
from ..migrations.v014_wiki_authoring import _EQUIVALENT_UPLOAD
from ..wiki_authoring import AUTHORING_POLICY_VERSION
from ..wiki_preservation import AUTHORING_ONLY_VERSIONS, PRESERVATION_VERSION
from ..migrations.v032_native_display_storage import DISPLAY_POLICY
from .publication_commit import (
    PublicationCommitConflictError, PublicationCommitIntegrityError,
    PublicationCommitTarget, _SourceBinding, _sha, _user, _uuid, _now,
)


@dataclass(frozen=True, slots=True)
class PublishedBodyIdentity:
    kind: str
    snapshot_id: str
    raw_sha256: str
    normalized_sha256: str
    size_bytes: int
    normalizer_version: str
    manifest_sha256: str | None
    manifest_size_bytes: int | None
    policy_version: str
    policy_sha256: str | None

    @property
    def bytes_identity(self):
        return (self.raw_sha256, self.normalized_sha256, self.size_bytes,
                self.normalizer_version)


@dataclass(frozen=True, slots=True)
class PublishedBodyStorage:
    blob_sha256: str | None
    blob_size_bytes: int | None
    blob_media_type: str | None
    blob_relative_path: str | None
    blob_verification_status: str | None
    blob_created_at: int | None
    blob_verified_at: int | None
    body_created_at: int


@dataclass(frozen=True, slots=True)
class PublishedTargetHistory:
    space_id: str
    item_id: str
    current_revision_id: str
    current_row_version: int
    operation_id: str
    operation_created_at: int
    original_revision_id: str
    body: PublishedBodyIdentity
    history_count: int
    item_count: int
    invalid_count: int
    storage: PublishedBodyStorage
    root_operation_id: str
    root_created_at: int

    @property
    def order_key(self):
        # Kind is intentionally absent: earlier actual publication wins.
        return (self.root_created_at, self.root_operation_id,
                self.operation_created_at, self.operation_id, self.item_id,
                self.body.snapshot_id)


@dataclass(frozen=True, slots=True)
class PreparedPublishedUploadHistory:
    binding: _SourceBinding
    source_identity: tuple
    targets: tuple[PublicationCommitTarget, ...]
    space_fence: tuple
    histories: tuple[PublishedTargetHistory, ...]

    @property
    def artifact_id(self):
        return self.binding.artifact_id

    @property
    def extraction_snapshot_id(self):
        return self.binding.extraction_snapshot_id

    @property
    def selected(self):
        return self.histories[0] if self.histories else None

    @property
    def reuse_anchors(self):
        return tuple((row.space_id, row.item_id, row.current_revision_id)
                     for row in self.histories)


# All identifiers below are static product SQL; only values are parameters.
# Do not filter to a requested kind or policy: that conceals older stable bodies.
_MATCHES = f"""
WITH matched AS (
 SELECT t.target_space_id AS space_id,t.item_id,t.resolved_revision_id AS original_revision_id,
   i.current_revision_id,i.row_version,i.lifecycle_status,i.curation_status,
   i.space_id AS actual_space_id,p.id AS operation_id,p.created_at AS operation_created_at,
   sx.id AS extraction_id,sx.extracted_sha256,sx.normalized_content_sha256 AS extraction_normalized,
   sx.extracted_size_bytes,sx.normalizer_version AS extraction_normalizer,sx.status AS extraction_status,
   coalesce(gs.selected_authored_wiki_id,aus.authored_snapshot_id) AS authored_snapshot_id,
   aws.content_sha256 AS authored_sha,aws.content_size_bytes AS authored_size,
   aws.manifest_sha256 AS authored_manifest,aws.manifest_size_bytes AS authored_manifest_size,
   ar.status AS authored_status,ar.policy_version AS authored_policy,
   coalesce(gs.selected_preserved_wiki_id,prs.preserved_snapshot_id) AS preserved_snapshot_id,
   pws.content_sha256 AS preserved_sha,pws.content_size_bytes AS preserved_size,
   pws.manifest_sha256 AS preserved_manifest,pws.manifest_size_bytes AS preserved_manifest_size,
   pr.status AS preserved_status,pr.policy_version AS preserved_policy,
   coalesce(gs.selected_native_wiki_id,nss.native_snapshot_id) AS native_snapshot_id,
   nws.content_sha256 AS native_sha,nws.content_size_bytes AS native_size,
   nws.manifest_sha256 AS native_manifest,nws.manifest_size_bytes AS native_manifest_size,
   ns.status AS native_status,nq.policy_sha256 AS native_policy_sha,
   json_extract(nr.metadata_json,'$.policy_version') AS native_policy_version,
   np.run_id AS native_planned_run,nr.id AS native_run,
   actual.selection_count AS selections,actual.root_operation_id,actual.root_created_at,
   initial.raw_content_sha256 AS initial_raw,initial.normalized_content_sha256 AS initial_normalized,
   initial.content_size_bytes AS initial_size,initial.normalizer_version AS initial_normalizer,
   v.raw_content_sha256 AS current_raw,v.normalized_content_sha256 AS current_normalized,
   v.content_size_bytes AS current_size,v.normalizer_version AS current_normalizer,
   b.content_sha256 AS retained_sha,b.content_size_bytes AS retained_size,
   CASE WHEN b.content_md IS NULL THEN 0 ELSE 1 END AS inline_body,
   b.content_blob_hash AS body_blob_hash,bo.size_bytes AS blob_size,bo.media_type AS blob_media,
   bo.relative_path AS blob_path,bo.verification_status AS blob_status,
   bo.created_at AS blob_created,bo.verified_at AS blob_verified,b.created_at AS body_created
 FROM source_artifact a JOIN extraction_snapshot x ON x.artifact_id=a.id AND x.id=?
 JOIN source_artifact prior ON prior.origin_type='upload'
   AND prior.raw_sha256=a.raw_sha256 AND prior.raw_size_bytes=a.raw_size_bytes
   AND prior.media_type=a.media_type AND prior.original_filename=a.original_filename
 JOIN extraction_snapshot px ON px.artifact_id=prior.id
 JOIN publication_operation p ON p.artifact_id=prior.id AND p.extraction_snapshot_id=px.id
 JOIN operation_dedup d ON d.id=p.dedup_id AND d.status='committed'
   AND d.operation_type='publication' AND d.actor_user_id=p.actor_user_id
   AND d.operation_id=p.operation_id AND d.request_sha256=p.request_sha256
 JOIN publication_target t ON t.publication_operation_id=p.id AND t.outcome<>'update_proposed'
 LEFT JOIN knowledge_item i ON i.id=t.item_id
 LEFT JOIN knowledge_revision initial ON initial.id=t.resolved_revision_id AND initial.item_id=t.item_id
 LEFT JOIN knowledge_revision v ON v.id=i.current_revision_id AND v.item_id=t.item_id
 LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=t.item_id
 LEFT JOIN blob_object bo ON bo.sha256=b.content_blob_hash
 LEFT JOIN publication_body_selection gs ON gs.publication_operation_id=p.id
 JOIN publication_operation_body actual ON actual.publication_operation_id=p.id
 LEFT JOIN publication_authored_selection aus ON aus.publication_operation_id=p.id
 LEFT JOIN authored_wiki_snapshot aws ON aws.id=coalesce(gs.selected_authored_wiki_id,aus.authored_snapshot_id)
 LEFT JOIN publication_authoring_run ar ON ar.id=aws.run_id
 LEFT JOIN publication_preserved_selection prs ON prs.publication_operation_id=p.id
 LEFT JOIN preserved_wiki_snapshot pws ON pws.id=coalesce(gs.selected_preserved_wiki_id,prs.preserved_snapshot_id)
 LEFT JOIN publication_preservation_run pr ON pr.id=pws.run_id
 LEFT JOIN publication_native_selection nss ON nss.publication_operation_id=p.id
 LEFT JOIN native_wiki_snapshot nws ON nws.id=coalesce(gs.selected_native_wiki_id,nss.native_snapshot_id)
 LEFT JOIN native_authoring_run nr ON nr.id=nws.run_id
 LEFT JOIN native_authoring_state ns ON ns.run_id=nr.id
 LEFT JOIN native_workflow_request nq ON nq.preview_id=nr.preview_id AND nq.parent_job_id=nr.parent_job_id
 LEFT JOIN native_workflow_planned np ON np.preview_id=nr.preview_id AND np.run_id=nr.id
 LEFT JOIN publication_extraction_reuse er ON er.publication_operation_id=p.id
 LEFT JOIN extraction_snapshot sx ON sx.id=coalesce(gs.selected_extraction_id,er.extraction_snapshot_id,p.extraction_snapshot_id)
 WHERE a.id=? AND x.extracted_sha256=? AND {_EQUIVALENT_UPLOAD}
   AND t.target_space_id IN ({{spaces}})
), identities AS (
 SELECT *,
   CASE WHEN native_snapshot_id IS NOT NULL THEN 'native_wiki'
        WHEN preserved_snapshot_id IS NOT NULL THEN 'preserved_wiki'
        WHEN authored_snapshot_id IS NOT NULL THEN 'authored_wiki' ELSE 'extraction' END AS kind,
   coalesce(native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id) AS selected_id,
   CASE WHEN native_snapshot_id IS NOT NULL THEN native_sha
        WHEN preserved_snapshot_id IS NOT NULL THEN preserved_sha
        WHEN authored_snapshot_id IS NOT NULL THEN authored_sha ELSE extracted_sha256 END AS selected_raw,
   CASE WHEN native_snapshot_id IS NOT NULL THEN native_sha
        WHEN preserved_snapshot_id IS NOT NULL THEN preserved_sha
        WHEN authored_snapshot_id IS NOT NULL THEN authored_sha ELSE extraction_normalized END AS selected_normalized,
   CASE WHEN native_snapshot_id IS NOT NULL THEN native_size
        WHEN preserved_snapshot_id IS NOT NULL THEN preserved_size
        WHEN authored_snapshot_id IS NOT NULL THEN authored_size ELSE extracted_size_bytes END AS selected_size,
   CASE WHEN native_snapshot_id IS NOT NULL OR preserved_snapshot_id IS NOT NULL OR authored_snapshot_id IS NOT NULL
        THEN 'markdown_lf_v1' ELSE extraction_normalizer END AS selected_normalizer,
   coalesce(native_manifest,preserved_manifest,authored_manifest) AS manifest_sha,
   coalesce(native_manifest_size,preserved_manifest_size,authored_manifest_size) AS manifest_size,
   CASE WHEN native_snapshot_id IS NOT NULL THEN native_policy_version
        WHEN preserved_snapshot_id IS NOT NULL THEN preserved_policy
        WHEN authored_snapshot_id IS NOT NULL THEN authored_policy ELSE 'extraction' END AS policy_version
 FROM matched
), checked AS (
 SELECT *,CASE WHEN selections>1 OR actual_space_id IS NOT space_id
   OR lifecycle_status IS NOT 'active' OR coalesce(curation_status,'') NOT IN ('published','provisional')
   OR selected_id IS NULL OR selected_raw IS NULL OR selected_size IS NULL
   OR initial_raw IS NOT selected_raw OR initial_normalized IS NOT selected_normalized
   OR initial_size IS NOT selected_size OR initial_normalizer IS NOT selected_normalizer
   OR current_raw IS NOT selected_raw OR current_normalized IS NOT selected_normalized
   OR current_size IS NOT selected_size OR current_normalizer IS NOT selected_normalizer
   OR retained_sha IS NOT selected_raw OR retained_size IS NOT selected_size
   OR (inline_body=1 AND body_blob_hash IS NOT NULL)
   OR (inline_body=0 AND (body_blob_hash IS NULL OR body_blob_hash IS NOT selected_raw
       OR blob_size IS NOT selected_size OR blob_status IS NOT 'verified'
       OR blob_created IS NULL OR blob_verified IS NULL
       OR blob_media IS NOT 'application/octet-stream'
       OR blob_path IS NOT substr(selected_raw,1,2)||'/'||substr(selected_raw,3,2)||'/'||selected_raw))
   OR selected_normalizer IS NOT 'markdown_lf_v1'
   OR (kind='extraction' AND extraction_status IS NOT 'ready')
   OR (kind='authored_wiki' AND (authored_status IS NOT 'ready' OR authored_policy IS NOT ?))
   OR (kind='preserved_wiki' AND (preserved_status IS NOT 'ready' OR preserved_policy NOT IN ({{policies}})))
   OR (kind='native_wiki' AND (native_status IS NOT 'ready' OR native_policy_sha IS NULL
       OR ({{native_policy_invalid}})
       OR native_planned_run IS NULL OR native_planned_run IS NOT native_run))
   OR (kind<>'extraction' AND (manifest_sha IS NULL OR manifest_size IS NULL))
   THEN 1 ELSE 0 END AS invalid
 FROM identities
), totals AS (
 SELECT space_id,count(*) AS history_count,count(DISTINCT item_id) AS item_count,sum(invalid) AS invalid_count
 FROM checked GROUP BY space_id
), ranked AS (
 SELECT *,row_number() OVER(PARTITION BY space_id
   ORDER BY root_created_at,root_operation_id,operation_created_at,operation_id,item_id,selected_id) AS choice FROM checked
)
SELECT r.space_id,r.item_id,r.current_revision_id,r.row_version,r.operation_id,r.operation_created_at,
 r.original_revision_id,r.kind,r.selected_id,r.selected_raw,r.selected_normalized,r.selected_size,
 r.selected_normalizer,r.manifest_sha,r.manifest_size,r.policy_version,r.native_policy_sha,
 totals.history_count,totals.item_count,totals.invalid_count,
 r.body_blob_hash,r.blob_size,r.blob_media,r.blob_path,r.blob_status,r.blob_created,r.blob_verified,r.body_created,
 r.root_operation_id,r.root_created_at
FROM ranked r JOIN totals ON totals.space_id=r.space_id WHERE r.choice=1
ORDER BY r.root_created_at,r.root_operation_id,r.operation_created_at,r.operation_id,r.item_id,r.selected_id
"""


def _role_history_sql():
    """Extend all matched history, never filter it to the incoming body kind.

    Kept as a strict extension of the existing four-kind query so old catalogs
    execute their exact original SQL and every existing conflict check remains.
    """
    sql = _MATCHES
    def extend(old, new, count=1):
        nonlocal sql
        if sql.count(old) != count:
            raise RuntimeError("The common publication history extension differs.")
        sql = sql.replace(old, new)
    extend("   actual.selection_count AS selections", """   gs.selected_pptx_role_wiki_id AS role_snapshot_id,
   role_s.content_sha256 AS role_sha,role_s.content_size_bytes AS role_size,
   role_s.manifest_sha256 AS role_manifest,role_s.manifest_size_bytes AS role_manifest_size,
   role_state.status AS role_status,role_q.policy_sha256 AS role_policy_sha,
   json_extract(role_r.metadata_json,'$.policy_version') AS role_policy_version,
   actual.selection_count AS selections""")
    extend(" LEFT JOIN publication_extraction_reuse er", """ LEFT JOIN pptx_role_wiki_snapshot role_s ON role_s.id=gs.selected_pptx_role_wiki_id
 LEFT JOIN pptx_role_authoring_run role_r ON role_r.id=role_s.run_id
 LEFT JOIN pptx_role_authoring_state role_state ON role_state.run_id=role_r.id
 LEFT JOIN pptx_role_workflow_request role_q ON role_q.preview_id=role_r.preview_id AND role_q.parent_job_id=role_r.parent_job_id
 LEFT JOIN publication_extraction_reuse er""")
    for expression, replacement in (("'native_wiki'", "'pptx_role_wiki'"), ("native_sha", "role_sha"),
            ("native_size", "role_size"), ("native_policy_version", "role_policy_version")):
        old = "CASE WHEN native_snapshot_id IS NOT NULL THEN " + expression
        extend(old, "CASE WHEN role_snapshot_id IS NOT NULL THEN " + replacement
            + "\n        WHEN native_snapshot_id IS NOT NULL THEN " + expression, 2 if expression == "native_sha" else 1)
    extend("coalesce(native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id)",
           "coalesce(role_snapshot_id,native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id)")
    extend("CASE WHEN native_snapshot_id IS NOT NULL OR preserved_snapshot_id", "CASE WHEN role_snapshot_id IS NOT NULL OR native_snapshot_id IS NOT NULL OR preserved_snapshot_id")
    extend("coalesce(native_manifest,preserved_manifest,authored_manifest)", "coalesce(role_manifest,native_manifest,preserved_manifest,authored_manifest)")
    extend("coalesce(native_manifest_size,preserved_manifest_size,authored_manifest_size)", "coalesce(role_manifest_size,native_manifest_size,preserved_manifest_size,authored_manifest_size)")
    extend("   OR (kind<>'extraction'", """   OR (kind='pptx_role_wiki' AND (role_status IS NOT 'ready' OR role_policy_sha IS NULL
       OR NOT EXISTS(SELECT 1 FROM pptx_role_registered_publication registered
          WHERE registered.snapshot_id=identities.role_snapshot_id
            AND registered.publication_operation_id=identities.operation_id)))
   OR (kind<>'extraction'""")
    extend("r.policy_version,r.native_policy_sha,", "r.policy_version,coalesce(r.role_policy_sha,r.native_policy_sha),")
    return sql


_ROLE_MATCHES = _role_history_sql()


def _pdf_history_sql():
    """Six-kind history keeps all older witnesses, never private PDF bodies."""
    sql = _ROLE_MATCHES
    def extend(old, new, count=1):
        nonlocal sql
        if sql.count(old) != count:
            raise RuntimeError("The PDF publication history extension differs.")
        sql = sql.replace(old, new)
    extend("   actual.selection_count AS selections", """   gs.selected_pdf_wiki_id AS pdf_snapshot_id,
   pdf_s.content_sha256 AS pdf_sha,pdf_s.content_size_bytes AS pdf_size,
   pdf_s.manifest_sha256 AS pdf_manifest,pdf_s.manifest_size_bytes AS pdf_manifest_size,
   pdf_state.status AS pdf_status,pdf_q.policy_sha256 AS pdf_policy_sha,
   json_extract(pdf_r.metadata_json,'$.policy_version') AS pdf_policy_version,
   actual.selection_count AS selections""")
    extend(" LEFT JOIN publication_extraction_reuse er", """ LEFT JOIN pdf_wiki_snapshot pdf_s ON pdf_s.id=gs.selected_pdf_wiki_id
 LEFT JOIN pdf_authoring_run pdf_r ON pdf_r.id=pdf_s.run_id
 LEFT JOIN pdf_authoring_state pdf_state ON pdf_state.run_id=pdf_r.id
 LEFT JOIN pdf_workflow_request pdf_q ON pdf_q.preview_id=pdf_r.preview_id AND pdf_q.parent_job_id=pdf_r.parent_job_id
 LEFT JOIN publication_extraction_reuse er""")
    for expression, replacement in (("'pptx_role_wiki'", "'pdf_wiki'"), ("role_sha", "pdf_sha"),
            ("role_size", "pdf_size"), ("role_policy_version", "pdf_policy_version")):
        old = "CASE WHEN role_snapshot_id IS NOT NULL THEN " + expression
        extend(old, "CASE WHEN pdf_snapshot_id IS NOT NULL THEN " + replacement
            + "\n        WHEN role_snapshot_id IS NOT NULL THEN " + expression, 2 if expression == "role_sha" else 1)
    extend("coalesce(role_snapshot_id,native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id)",
           "coalesce(pdf_snapshot_id,role_snapshot_id,native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id)")
    extend("CASE WHEN role_snapshot_id IS NOT NULL OR native_snapshot_id", "CASE WHEN pdf_snapshot_id IS NOT NULL OR role_snapshot_id IS NOT NULL OR native_snapshot_id")
    extend("coalesce(role_manifest,native_manifest,preserved_manifest,authored_manifest)", "coalesce(pdf_manifest,role_manifest,native_manifest,preserved_manifest,authored_manifest)")
    extend("coalesce(role_manifest_size,native_manifest_size,preserved_manifest_size,authored_manifest_size)", "coalesce(pdf_manifest_size,role_manifest_size,native_manifest_size,preserved_manifest_size,authored_manifest_size)")
    extend("   OR (kind<>'extraction'", """   OR (kind='pdf_wiki' AND (pdf_status IS NOT 'ready' OR pdf_policy_sha IS NULL
       OR NOT EXISTS(SELECT 1 FROM pdf_registered_publication registered
          WHERE registered.snapshot_id=identities.pdf_snapshot_id
            AND registered.publication_operation_id=identities.operation_id)))
   OR (kind<>'extraction'""")
    extend("coalesce(r.role_policy_sha,r.native_policy_sha)", "coalesce(r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)")
    return sql


_PDF_MATCHES = _pdf_history_sql()


def _pdf_visual_history_sql():
    """Seven-kind immutable public witnesses; no private visual body access."""
    sql = _PDF_MATCHES
    def extend(old, new, count=1):
        nonlocal sql
        if sql.count(old) != count:
            raise RuntimeError("The visual PDF publication history extension differs.")
        sql = sql.replace(old, new)
    extend("   actual.selection_count AS selections", """   gs.selected_pdf_visual_wiki_id AS visual_snapshot_id,
   visual_s.content_sha256 AS visual_sha,visual_s.content_size_bytes AS visual_size,
   visual_s.manifest_sha256 AS visual_manifest,visual_s.manifest_size_bytes AS visual_manifest_size,
   visual_state.status AS visual_status,visual_q.policy_sha256 AS visual_policy_sha,
   json_extract(visual_r.metadata_json,'$.policy_version') AS visual_policy_version,
   actual.selection_count AS selections""")
    extend(" LEFT JOIN publication_extraction_reuse er", """ LEFT JOIN pdf_visual_wiki_snapshot visual_s ON visual_s.id=gs.selected_pdf_visual_wiki_id
 LEFT JOIN pdf_visual_authoring_run visual_r ON visual_r.id=visual_s.run_id
 LEFT JOIN pdf_visual_authoring_state visual_state ON visual_state.run_id=visual_r.id
 LEFT JOIN pdf_visual_workflow_request visual_q ON visual_q.preview_id=visual_r.preview_id AND visual_q.parent_job_id=visual_r.parent_job_id
 LEFT JOIN publication_extraction_reuse er""")
    for expression, replacement in (("'pdf_wiki'", "'pdf_visual_wiki'"), ("pdf_sha", "visual_sha"),
            ("pdf_size", "visual_size"), ("pdf_policy_version", "visual_policy_version")):
        old = "CASE WHEN pdf_snapshot_id IS NOT NULL THEN " + expression
        extend(old, "CASE WHEN visual_snapshot_id IS NOT NULL THEN " + replacement
            + "\n        WHEN pdf_snapshot_id IS NOT NULL THEN " + expression, 2 if expression == "pdf_sha" else 1)
    for field in ("snapshot_id", "manifest", "manifest_size"):
        rest = ("role_snapshot_id,native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id"
                if field == "snapshot_id" else
                ",".join(prefix + "_" + field for prefix in ("role", "native", "preserved", "authored")))
        extend("coalesce(pdf_" + field + "," + rest + ")",
               "coalesce(visual_" + field + ",pdf_" + field + "," + rest + ")")
    extend("CASE WHEN pdf_snapshot_id IS NOT NULL OR role_snapshot_id",
           "CASE WHEN visual_snapshot_id IS NOT NULL OR pdf_snapshot_id IS NOT NULL OR role_snapshot_id")
    extend("   OR (kind<>'extraction'", """   OR (kind='pdf_visual_wiki' AND (visual_status IS NOT 'ready' OR visual_policy_sha IS NULL
       OR NOT EXISTS(SELECT 1 FROM pdf_visual_registered_publication registered
          WHERE registered.snapshot_id=identities.visual_snapshot_id
            AND registered.publication_operation_id=identities.operation_id)))
   OR (kind<>'extraction'""")
    extend("coalesce(r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)",
           "coalesce(r.visual_policy_sha,r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)")
    return sql


_PDF_VISUAL_MATCHES = _pdf_visual_history_sql()


def _office_history_sql():
    """Eighth-kind public witnesses, preserving every older candidate rule."""
    sql = _PDF_VISUAL_MATCHES
    def extend(old, new, count=1):
        nonlocal sql
        if sql.count(old) != count:
            raise RuntimeError("The Office publication history extension differs.")
        sql = sql.replace(old, new)
    extend("   actual.selection_count AS selections", """   gs.selected_office_hybrid_wiki_id AS office_snapshot_id,
   office_s.content_sha256 AS office_sha,
   length(CAST(office_s.content_md AS BLOB)) AS office_size,
   office_s.manifest_sha256 AS office_manifest,
   length(CAST(office_s.manifest_json AS BLOB)) AS office_manifest_size,
   office_writer.state AS office_status,office_r.policy_sha256 AS office_policy_sha,
   office_r.workflow_version AS office_policy_version,
   actual.selection_count AS selections""")
    extend(" LEFT JOIN publication_extraction_reuse er", """ LEFT JOIN office_hybrid_wiki_snapshot office_s
   ON office_s.preview_id=gs.selected_office_hybrid_wiki_id
 LEFT JOIN office_hybrid_wiki_run office_r
   ON office_r.preview_id=office_s.preview_id AND office_r.job_id=office_s.job_id
 LEFT JOIN durable_job office_writer ON office_writer.id=office_r.job_id
 LEFT JOIN publication_extraction_reuse er""")
    for expression, replacement in (("'pdf_visual_wiki'", "'office_hybrid_wiki'"),
            ("visual_sha", "office_sha"), ("visual_size", "office_size"),
            ("visual_policy_version", "office_policy_version")):
        old = "CASE WHEN visual_snapshot_id IS NOT NULL THEN " + expression
        extend(old, "CASE WHEN office_snapshot_id IS NOT NULL THEN " + replacement
            + "\n        WHEN visual_snapshot_id IS NOT NULL THEN " + expression,
            2 if expression == "visual_sha" else 1)
    for field in ("snapshot_id", "manifest", "manifest_size"):
        rest = ("role_snapshot_id,native_snapshot_id,preserved_snapshot_id,authored_snapshot_id,extraction_id"
            if field == "snapshot_id" else
            ",".join(prefix + "_" + field for prefix in ("role", "native", "preserved", "authored")))
        extend("coalesce(visual_" + field + ",pdf_" + field + "," + rest + ")",
            "coalesce(office_" + field + ",visual_" + field + ",pdf_" + field + "," + rest + ")")
    extend("CASE WHEN visual_snapshot_id IS NOT NULL OR pdf_snapshot_id",
        "CASE WHEN office_snapshot_id IS NOT NULL OR visual_snapshot_id IS NOT NULL OR pdf_snapshot_id")
    extend("   OR (kind<>'extraction'", """   OR (kind='office_hybrid_wiki' AND (office_status IS NOT 'succeeded'
       OR office_policy_sha IS NULL OR NOT EXISTS(
           SELECT 1 FROM office_hybrid_registered_publication registered
           WHERE registered.snapshot_id=identities.office_snapshot_id
             AND registered.publication_operation_id=identities.operation_id)))
   OR (kind<>'extraction'""")
    extend("coalesce(r.visual_policy_sha,r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)",
        "coalesce(r.office_policy_sha,r.visual_policy_sha,r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)")
    return sql


_OFFICE_MATCHES = _office_history_sql()


def _continuation_history_sql():
    """Ninth-kind metadata-only public witnesses; older SQL is unchanged."""
    from ..migrations.v058_office_hybrid_publication_body import KINDS
    sql = _OFFICE_MATCHES
    def extend(old, new, count=1):
        nonlocal sql
        if sql.count(old) != count:
            raise RuntimeError("The continuation history predecessor differs.")
        sql = sql.replace(old, new)
    columns = ["publication_operation_id", *("selected_"+kind+"_id" for kind in KINDS)]
    projected = ",".join(columns)
    values = ["publication_operation_id", *("CASE WHEN selected_kind='"+kind+"' THEN selected_snapshot_id END" for kind in KINDS)]
    extend(" LEFT JOIN publication_body_selection gs ON gs.publication_operation_id=p.id",
        " LEFT JOIN (SELECT "+projected+",NULL AS selected_pdf_continuation_wiki_id FROM publication_body_selection UNION ALL SELECT "+
        ",".join(values)+",CASE WHEN selected_kind='pdf_continuation_wiki' THEN selected_snapshot_id END FROM pdf_continuation_body_selection) gs ON gs.publication_operation_id=p.id")
    extend("   actual.selection_count AS selections", """   gs.selected_pdf_continuation_wiki_id AS continuation_snapshot_id,
   continuation_s.content_sha256 AS continuation_sha,continuation_s.content_size_bytes AS continuation_size,
   continuation_s.manifest_sha256 AS continuation_manifest,continuation_s.manifest_size_bytes AS continuation_manifest_size,
   continuation_s.policy_sha256 AS continuation_policy_sha,continuation_s.policy_version AS continuation_policy_version,
   actual.selection_count AS selections""")
    extend(" LEFT JOIN publication_extraction_reuse er", """ LEFT JOIN pdf_continuation_publication_snapshot continuation_s
   ON continuation_s.snapshot_id=gs.selected_pdf_continuation_wiki_id
 LEFT JOIN publication_extraction_reuse er""")
    for expression, replacement in (("'office_hybrid_wiki'", "'pdf_continuation_wiki'"),
            ("office_sha", "continuation_sha"), ("office_size", "continuation_size"),
            ("office_policy_version", "continuation_policy_version")):
        old = "CASE WHEN office_snapshot_id IS NOT NULL THEN " + expression
        extend(old, "CASE WHEN continuation_snapshot_id IS NOT NULL THEN " + replacement
            + "\n        WHEN office_snapshot_id IS NOT NULL THEN " + expression, 2 if expression=="office_sha" else 1)
    for field in ("snapshot_id", "manifest", "manifest_size"):
        extend("coalesce(office_"+field+",", "coalesce(continuation_"+field+",office_"+field+",")
    extend("CASE WHEN office_snapshot_id IS NOT NULL OR visual_snapshot_id",
        "CASE WHEN continuation_snapshot_id IS NOT NULL OR office_snapshot_id IS NOT NULL OR visual_snapshot_id")
    extend("   OR (kind<>'extraction'", """   OR (kind='pdf_continuation_wiki' AND (continuation_policy_sha IS NULL OR NOT EXISTS(
       SELECT 1 FROM pdf_continuation_registered_publication registered
       WHERE registered.selected_kind='pdf_continuation_wiki'
         AND registered.selected_snapshot_id=identities.continuation_snapshot_id
         AND registered.publication_operation_id=identities.operation_id)))
   OR (kind<>'extraction'""")
    extend("coalesce(r.office_policy_sha,r.visual_policy_sha,r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)",
        "coalesce(r.continuation_policy_sha,r.office_policy_sha,r.visual_policy_sha,r.pdf_policy_sha,r.role_policy_sha,r.native_policy_sha)")
    return sql


_CONTINUATION_MATCHES = _continuation_history_sql()


class PublishedUploadHistoryRepository:
    @staticmethod
    def prepare(connection, binding, authorized_targets):
        if not isinstance(connection, ManagedConnection):
            raise TypeError("Upload history requires a managed canonical connection.")
        if not isinstance(binding, _SourceBinding):
            # PublicationCommitDraft has extra fields; accept only the exact
            # immutable identity constructed explicitly by its owning composer.
            raise TypeError("Upload history requires an explicit source binding.")
        _user(binding.actor_user_id)
        _uuid(binding.preview_id, field="preview_id")
        _uuid(binding.artifact_id, field="artifact_id")
        _uuid(binding.extraction_snapshot_id, field="extraction_snapshot_id")
        _sha(binding.source_snapshot_sha256, field="source_snapshot_sha256")
        _now(binding.created_at_ms)
        targets = tuple(authorized_targets)
        if (not 1 <= len(targets) <= 2 or any(type(t) is not PublicationCommitTarget for t in targets)
                or len({t.space_id for t in targets}) != len(targets)):
            raise PublicationCommitIntegrityError("Upload history requires one or two exact native targets.")
        for target in targets:
            _uuid(target.space_id, field="target_space_id")
            if target.kind not in {"personal", "part", "team", "responsibility"}:
                raise PublicationCommitIntegrityError("Upload history cannot use an aggregate projection as a target.")
        if len(targets) == 2 and {t.kind for t in targets} != {"personal", "part"}:
            raise PublicationCommitIntegrityError("Two upload targets must be independent personal and Part spaces.")
        targets = tuple(sorted(targets, key=lambda t: t.space_id))
        # Fence target identity and current write state, not its content version.
        # Every unrelated publication increments knowledge_space.row_version;
        # including it would reject independently prepared uploads to this space.
        # Equivalent-source history/anchors remain compared in full below, and
        # the owning body service separately revalidates current publish authority.
        spaces = tuple(tuple(row) for row in connection.execute(
            "SELECT id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled "
            "FROM knowledge_space WHERE id IN ("+','.join('?' for _ in targets)+") ORDER BY id",
            tuple(t.space_id for t in targets)))
        if (len(spaces)!=len(targets)
                or any((row[0],row[1])!=(target.space_id,target.kind) or row[4:6]!=('active',1)
                       for row,target in zip(spaces,targets,strict=True))):
            raise PublicationCommitConflictError("Upload history native target identity or write state changed.")
        source = connection.execute("""SELECT a.raw_sha256,a.raw_size_bytes,a.media_type,a.original_filename,
            x.extracted_sha256,x.extracted_size_bytes,x.normalized_content_sha256,x.normalizer_version,
            x.extractor_name,x.extractor_version,x.warnings_json
            FROM source_artifact a JOIN extraction_snapshot x ON x.artifact_id=a.id
            JOIN publication_preview p ON p.artifact_id=a.id AND p.extraction_snapshot_id=x.id
            WHERE a.id=? AND x.id=? AND a.origin_type='upload' AND x.status='ready'
              AND x.extracted_sha256=? AND p.id=? AND p.actor_user_id=?""",
            (binding.artifact_id,binding.extraction_snapshot_id,binding.source_snapshot_sha256,
             binding.preview_id,binding.actor_user_id)).fetchone()
        if source is None:
            raise PublicationCommitConflictError("Upload history source identity is unavailable or inconsistent.")
        policies = (PRESERVATION_VERSION, *AUTHORING_ONLY_VERSIONS)
        native_policy_invalid = "native_policy_version IS NOT 'wiki-native-batch-authoring-v1'"
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
                "AND name='native_display_registered_publication'").fetchone() is not None:
            # Old catalogs with an empty registration view still reject display
            # history. The successor requires the exact snapshot/operation pair,
            # not merely a completed run or an allowlisted policy string.
            native_policy_invalid = f"""NOT coalesce((native_policy_version='wiki-native-batch-authoring-v1'
                OR (native_policy_version='{DISPLAY_POLICY}' AND EXISTS(
                    SELECT 1 FROM native_display_registered_publication registered
                    WHERE registered.snapshot_id=identities.native_snapshot_id
                      AND registered.publication_operation_id=identities.operation_id))),0)"""
        role_enabled = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
            "AND name='pptx_role_publication_snapshot'").fetchone() is not None
        pdf_enabled = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
            "AND name='pdf_publication_snapshot'").fetchone() is not None
        visual_enabled = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
            "AND name='pdf_visual_publication_snapshot'").fetchone() is not None
        office_enabled = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
            "AND name='office_hybrid_registered_publication'").fetchone() is not None
        continuation_enabled = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' "
            "AND name='pdf_continuation_registered_publication'").fetchone() is not None
        sql = (_CONTINUATION_MATCHES if continuation_enabled else _OFFICE_MATCHES if office_enabled else _PDF_VISUAL_MATCHES if visual_enabled else _PDF_MATCHES if pdf_enabled else _ROLE_MATCHES if role_enabled else _MATCHES).format(spaces=','.join('?' for _ in targets), policies=','.join('?' for _ in policies),
            native_policy_invalid=native_policy_invalid)
        rows = connection.execute(sql, (binding.extraction_snapshot_id,binding.artifact_id,
            binding.source_snapshot_sha256,*(t.space_id for t in targets),AUTHORING_POLICY_VERSION,*policies)).fetchall()
        histories = tuple(PublishedTargetHistory(*tuple(row)[:7], PublishedBodyIdentity(*tuple(row)[7:17]),
            *tuple(row)[17:20], PublishedBodyStorage(*tuple(row)[20:28]),*tuple(row)[28:]) for row in rows)
        if any(row.invalid_count or row.item_count != 1 for row in histories):
            raise PublicationCommitConflictError(
                "Equivalent-source knowledge was edited, deactivated, ambiguous or unavailable; explicit resolution is required.")
        if len({row.body.bytes_identity for row in histories}) > 1:
            raise PublicationCommitConflictError(
                "Equivalent-source publication targets have different canonical bodies; explicit resolution is required.")
        return PreparedPublishedUploadHistory(binding,tuple(source),targets,spaces,histories)

    @classmethod
    def revalidate(cls, connection, binding, authorized_targets, prepared):
        if type(prepared) is not PreparedPublishedUploadHistory:
            raise TypeError("Upload history requires its immutable read-phase preparation.")
        if binding!=prepared.binding:
            raise PublicationCommitConflictError("Upload history belongs to a different source request binding.")
        current = cls.prepare(connection, binding, authorized_targets)
        if current != prepared:
            raise PublicationCommitConflictError("Equivalent-source upload history changed after preparation.")

    @classmethod
    def load_selected_body(cls, connection, binding, authorized_targets, prepared):
        """Read the already-authorized canonical body, never a private candidate.

        The composer calls this in its read phase, before opening its writer.
        Its own complete candidate must still have been verified separately.
        """
        cls.revalidate(connection,binding,authorized_targets,prepared)
        selected = prepared.selected
        if selected is None:
            return None
        if selected.storage.blob_sha256 is not None:
            raise PublicationCommitIntegrityError(
                "Blob-backed public bodies require the service's outside-transaction verifier.")
        row = connection.execute("""SELECT content_md FROM knowledge_revision_body
            WHERE revision_id=? AND item_id=?""",
            (selected.current_revision_id,selected.item_id)).fetchone()
        if row is None or type(row[0]) is not str:
            raise PublicationCommitConflictError("The selected canonical upload body is no longer retained.")
        body = row[0]
        cls.validate_body(body,selected.body)
        cls.revalidate(connection,binding,authorized_targets,prepared)
        return body

    @staticmethod
    def validate_body(body,identity):
        if type(body) is not str:
            raise PublicationCommitIntegrityError("The selected canonical upload body is not text.")
        try:
            raw = body.encode('utf-8')
            normalized = body.replace('\r\n','\n').replace('\r','\n').encode('utf-8')
        except UnicodeError as exc:
            raise PublicationCommitIntegrityError("The selected canonical body is not valid UTF-8.") from exc
        if ((sha256(raw).hexdigest(),sha256(normalized).hexdigest(),len(raw),'markdown_lf_v1')
                != identity.bytes_identity or (identity.kind != 'extraction' and '\r' in body)):
            raise PublicationCommitIntegrityError("The selected canonical upload body failed exact identity validation.")
