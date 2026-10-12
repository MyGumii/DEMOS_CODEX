"""Opt-in sixth publication body: registered PDF Wiki, never raw fallback.

Permanent lineage is independent of retained private bodies and current access.
Only the insertion candidate uses current preview/account/source eligibility.
Historical numbered schemas and existing five-kind receipts remain unchanged.
"""
from . import v037_pptx_role_publication as prior
from .v032_native_display_storage import _once
from .v039_pdf_authoring_storage import _OTHER_RUNS, _OTHER_WORKFLOWS
from .v040_pdf_authoring_workflow import _family, PLAN, COMPLETE

KINDS = (*prior.KINDS, "pdf_wiki")
TABLES = (*prior.TABLES, "pdf_wiki_snapshot")
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


def body_id(prefix, alias=""):
    return "coalesce(" + ",".join(f"{alias}{prefix}_{kind}_id" for kind in KINDS) + ")"


def _fields(prefix):
    declarations = ",\n".join(f"{prefix}_{kind}_id TEXT REFERENCES {table}(id) ON DELETE RESTRICT"
        for kind, table in zip(KINDS, TABLES, strict=True))
    return f"{prefix}_kind TEXT NOT NULL CHECK({prefix}_kind IN {KINDS}),\n{declarations}"


def _kind_check(prefix):
    cases = " OR ".join("(" + f"{prefix}_kind='{kind}' AND " + " AND ".join(
        f"{prefix}_{other}_id IS " + ("NOT NULL" if other == kind else "NULL") for other in KINDS) + ")"
        for kind in KINDS)
    return f"CHECK({cases})"


def _expand_ids(sql):
    for prefix in ("requested", "selected"):
        sql = sql.replace(prior.body_id(prefix, "g."), body_id(prefix, "g."))
    return sql


BODY_BOUND = _expand_ids(prior.BODY_BOUND)
_table = prior._table
for _prefix in ("requested", "selected"):
    _table = _once(_table, prior._fields(_prefix), _fields(_prefix))
    _table = _once(_table, prior._kind_check(_prefix), _kind_check(_prefix))

_insert = _expand_ids(prior._insert)
_insert = _once(_insert, ",".join(f"NEW.{name} AS {name}" for name in prior._columns(prior.KINDS)),
    ",".join(f"NEW.{name} AS {name}" for name in prior._columns(KINDS)))

_identities = prior._identities + """
UNION ALL SELECT 'pdf_wiki',s.id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,s.content_size_bytes,'markdown_lf_v1',s.manifest_sha256,
 s.manifest_size_bytes,json_extract(r.metadata_json,'$.policy_version'),state.status,
 r.id,r.preview_id,s.created_at,r.actor_user_id
FROM pdf_wiki_snapshot s JOIN pdf_authoring_run r ON r.id=s.run_id
 JOIN pdf_authoring_state state ON state.run_id=r.id"""

# PDF uses a legacy-v1 extraction only as provenance. It has no OOXML native
# structure, and raw-PDF/geometry hashes are not the legacy extraction hash.
_candidate = f"""CREATE VIEW pdf_publication_candidate AS
    SELECT s.id AS snapshot_id,r.id AS run_id,r.preview_id,r.actor_user_id,r.artifact_id,
      r.extraction_snapshot_id,r.source_fence_sha256,r.source_plan_sha256,r.metadata_sha256,
      s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes,
      s.created_at AS snapshot_created_at,q.policy_sha256,p.row_version AS preview_row_version,
      state.row_version AS state_row_version,p.source_snapshot_sha256,p.captured_scope_epoch,
      q.deadline_at
    FROM pdf_publication_snapshot registered
      JOIN pdf_authoring_run r ON r.id=registered.run_id
      JOIN pdf_authoring_state state ON state.run_id=r.id AND state.status='ready'
      JOIN pdf_wiki_snapshot s ON s.id=registered.snapshot_id AND s.run_id=r.id
      JOIN pdf_wiki_snapshot_body body ON body.snapshot_id=s.id
      JOIN pdf_workflow_request q ON q.preview_id=r.preview_id AND q.parent_job_id=r.parent_job_id
      JOIN publication_preview p ON p.id=r.preview_id AND p.actor_user_id=r.actor_user_id
        AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
      JOIN durable_job parent ON parent.id=r.parent_job_id AND parent.id=p.durable_job_id
        AND parent.state='succeeded' AND parent.cancel_requested=0
        AND parent.actor_user_id=r.actor_user_id AND parent.job_type='publication_preview.extract'
        AND parent.handler_version='v1'
      JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
      JOIN source_artifact a ON a.id=r.artifact_id AND a.body_retention_status='present'
        AND a.origin_type='upload' AND a.publisher_user_id=r.actor_user_id
      JOIN extraction_snapshot x ON x.id=r.extraction_snapshot_id AND x.artifact_id=r.artifact_id
        AND x.body_retention_status='present' AND x.status='ready' AND x.extracted_sha256=p.source_snapshot_sha256
        AND x.extractor_name='canonical-pypdf' AND x.extractor_version='rich-source-v3-bounded-tables'
      JOIN extraction_snapshot_body xb ON xb.snapshot_id=x.id AND xb.artifact_id=a.id
        AND xb.content_sha256=x.extracted_sha256 AND xb.content_size_bytes=x.extracted_size_bytes
      JOIN artifact_blob raw ON raw.artifact_id=a.id AND raw.role='raw' AND raw.ordinal=0
        AND raw.body_retention_status='present' AND raw.content_sha256=a.raw_sha256 AND raw.size_bytes=a.raw_size_bytes
      JOIN artifact_blob_body retained ON retained.artifact_blob_id=raw.id AND retained.content_sha256=raw.content_sha256
      JOIN blob_object object ON object.sha256=retained.content_sha256
        AND object.size_bytes=raw.size_bytes AND object.verification_status='verified'
    WHERE p.status='ready' AND p.rule_version='p6.1-deterministic-ingest-v1'
      AND p.expires_at>{_NOW} AND q.deadline_at>{_NOW}
      AND lower(substr(a.original_filename,-4))='.pdf'
      AND (SELECT count(*) FROM artifact_blob WHERE artifact_id=a.id)=1
      AND NOT EXISTS(SELECT 1 FROM pdf_workflow_stop stop WHERE stop.preview_id=r.preview_id)
      AND r.batch_count=(SELECT count(*) FROM pdf_workflow_batch b JOIN durable_job j ON j.id=b.job_id
        JOIN pdf_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
        WHERE b.run_id=r.id AND j.state='succeeded' AND j.cancel_requested=0)
      {''.join(f' AND NOT EXISTS(SELECT 1 FROM {table} WHERE preview_id=r.preview_id)' for table in (*_OTHER_RUNS, *_OTHER_WORKFLOWS))}"""

# A pending/failed/unregistered PDF run must not become an extraction candidate.
# These exclusions apply to all older branches, including the raw v1 branch.
_candidates = prior._candidates + """
 AND NOT EXISTS(SELECT 1 FROM pdf_workflow_request WHERE preview_id=p.id)
 AND NOT EXISTS(SELECT 1 FROM pdf_authoring_run WHERE preview_id=p.id)
UNION ALL SELECT c.preview_id,c.actor_user_id,c.artifact_id,c.extraction_snapshot_id,
 c.source_snapshot_sha256,b.kind,b.snapshot_id,b.raw_sha256,b.normalized_sha256,b.size_bytes,
 b.normalizer_version,b.snapshot_created_at
FROM pdf_publication_candidate c JOIN publication_body_identity b
 ON b.kind='pdf_wiki' AND b.snapshot_id=c.snapshot_id"""

_unregistered_pdf = """EXISTS(SELECT 1 FROM pdf_wiki_snapshot pdf
    WHERE pdf.id IN (g.requested_pdf_wiki_id,g.selected_pdf_wiki_id)
      AND NOT EXISTS(SELECT 1 FROM pdf_publication_snapshot registered
        WHERE registered.snapshot_id=pdf.id))"""

_head, _rest = prior._lineage.split("WHEN ", 1)
_condition, _body = _rest.split("BEGIN ", 1)
_lineage = _head + f"""WHEN ({_condition.strip()}) AND NOT EXISTS(
    SELECT 1 FROM pdf_publication_candidate candidate
    WHERE candidate.actor_user_id=NEW.created_by AND candidate.artifact_id=NEW.base_artifact_id
      AND candidate.extraction_snapshot_id=NEW.base_extraction_snapshot_id
      AND candidate.content_sha256=NEW.base_content_sha256) BEGIN """ + _body

_receipt = """SELECT 1 FROM pdf_authoring_run r
    JOIN pdf_wiki_snapshot s ON s.run_id=r.id
    JOIN pdf_registered_publication registered ON registered.snapshot_id=s.id
    JOIN publication_operation p ON p.id=registered.publication_operation_id
    WHERE r.preview_id=OLD.id AND p.actor_user_id=OLD.actor_user_id
      AND p.artifact_id=OLD.artifact_id AND p.extraction_snapshot_id=OLD.extraction_snapshot_id"""

V041_STATEMENTS = (
    "DROP TRIGGER publication_body_selection_insert",
    "DROP TRIGGER publication_body_selection_update",
    "DROP TRIGGER publication_body_selection_delete",
    "DROP TRIGGER native_display_body_selection_guard",
    "DROP TRIGGER pptx_role_body_selection_guard",
    "DROP VIEW native_display_registered_publication",
    "DROP VIEW pptx_role_registered_publication",
    "DROP VIEW publication_requested_body_candidate",
    "DROP VIEW publication_operation_body",
    "DROP VIEW publication_body_identity",
    "ALTER TABLE publication_body_selection RENAME TO publication_body_selection_v37",
    _table,
    "INSERT INTO publication_body_selection(" + ",".join(prior._columns(prior.KINDS)) + ") SELECT "
        + ",".join(prior._columns(prior.KINDS)) + " FROM publication_body_selection_v37",
    "DROP TABLE publication_body_selection_v37",
    _identities,
    _expand_ids(next(s for s in prior.V037_STATEMENTS if s.startswith("CREATE VIEW publication_operation_body "))),
    f"""CREATE VIEW pdf_publication_snapshot AS
        SELECT s.id AS snapshot_id,r.id AS run_id,r.preview_id,r.actor_user_id,
          r.artifact_id,r.extraction_snapshot_id
        FROM pdf_wiki_snapshot s JOIN pdf_authoring_run r ON r.id=s.run_id
        JOIN pdf_registered_run registered ON registered.run_id=r.id
        JOIN pdf_authoring_state state ON state.run_id=r.id AND state.status='ready'
        JOIN pdf_workflow_plan plan ON plan.preview_id=r.preview_id
        JOIN durable_job planner ON planner.id=plan.job_id AND planner.job_type='{PLAN}'
          AND planner.state='succeeded' AND planner.cancel_requested=0
        JOIN pdf_workflow_completion completion ON completion.run_id=r.id
        JOIN durable_job finisher ON finisher.id=completion.job_id AND finisher.job_type='{COMPLETE}'
          AND finisher.state='succeeded' AND finisher.cancel_requested=0
        WHERE ({_family('planner', 'r.preview_id')}) AND ({_family('finisher', 'r.preview_id')})
          AND json_extract(finisher.input_ref_json,'$.run_id')=r.id
          AND json_extract(finisher.expected_state_json,'$.source_fence_sha256')=r.source_fence_sha256
          AND json_extract(finisher.expected_state_json,'$.source_plan_sha256')=r.source_plan_sha256
          AND json_extract(finisher.result_json,'$.run_id')=r.id
          AND json_extract(finisher.result_json,'$.snapshot_id')=s.id""",
    _candidate,
    _candidates,
    _insert,
    *(s for s in prior.V037_STATEMENTS if s.startswith((
        "CREATE TRIGGER publication_body_selection_update ",
        "CREATE TRIGGER publication_body_selection_delete ",
        "CREATE TRIGGER native_display_body_selection_guard ",
        "CREATE TRIGGER pptx_role_body_selection_guard "))),
    _expand_ids(prior._display_registered),
    _expand_ids(next(s for s in prior.V037_STATEMENTS if s.startswith("CREATE VIEW pptx_role_registered_publication "))),
    "DROP VIEW pdf_registered_publication",
    f"""CREATE VIEW pdf_registered_publication AS
        SELECT registered.snapshot_id,g.publication_operation_id
        FROM publication_body_selection g JOIN pdf_publication_snapshot registered
          ON registered.snapshot_id IN (g.requested_pdf_wiki_id,g.selected_pdf_wiki_id)
        WHERE EXISTS({BODY_BOUND}) AND NOT ({_unregistered_pdf})""",
    f"""CREATE TRIGGER pdf_body_selection_guard BEFORE INSERT ON publication_body_selection
        WHEN {_unregistered_pdf.replace('g.', 'NEW.')}
        BEGIN SELECT RAISE(ABORT,'PDF_PUBLICATION_NOT_REGISTERED'); END""",
    "DROP TRIGGER trg_lineage_group_validity_insert",
    _lineage,
    "DROP TRIGGER pdf_authoring_preview_consume_guard",
    "DROP TRIGGER pdf_workflow_consume_guard",
    *(f"""CREATE TRIGGER {name} BEFORE UPDATE OF status ON publication_preview
        WHEN NEW.status='consumed' AND (EXISTS(SELECT 1 FROM pdf_workflow_request WHERE preview_id=OLD.id)
            OR EXISTS(SELECT 1 FROM pdf_authoring_run WHERE preview_id=OLD.id))
          AND NOT EXISTS({_receipt})
        BEGIN SELECT RAISE(ABORT,'PDF_PUBLICATION_NOT_REGISTERED'); END"""
      for name in ("pdf_authoring_preview_consume_guard", "pdf_workflow_consume_guard")),
)

_prior_binding = prior.V037_POSTCONDITION_REPLACEMENTS[0][1]
V041_POSTCONDITION_REPLACEMENTS = ((_prior_binding,
    f"SELECT 'publication body binding mismatch' FROM publication_body_selection g WHERE NOT EXISTS({BODY_BOUND}) LIMIT 1"),)
V041_POSTCONDITION_QUERIES = (
    f"SELECT 'PDF publication not registered' FROM publication_body_selection g WHERE {_unregistered_pdf} LIMIT 1",
)
