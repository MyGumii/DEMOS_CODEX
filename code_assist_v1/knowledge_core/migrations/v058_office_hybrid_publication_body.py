"""Forward-only eighth publication body for exact Office native/page Wiki.

This extends the shared atomic body-selection receipt without reinterpreting
historical seven-kind rows. Candidate eligibility is current; a committed
selection is immutable and bound to its permanently registered v56 snapshot.
"""

from . import v045_pdf_visual_publication as prior
from .v032_native_display_storage import _once
from .v037_pptx_role_publication import _columns
from .v055_office_hybrid_preview import RULE


KIND = "office_hybrid_wiki"
KINDS = (*prior.KINDS, KIND)
TABLE = "office_hybrid_wiki_snapshot"


def body_id(prefix, alias=""):
    return "coalesce(" + ",".join(f"{alias}{prefix}_{kind}_id" for kind in KINDS) + ")"


def _fields(prefix):
    return (prior._fields(prefix).replace(
        f"{prefix}_kind TEXT NOT NULL CHECK({prefix}_kind IN {prior.KINDS})",
        f"{prefix}_kind TEXT NOT NULL CHECK({prefix}_kind IN {KINDS})")
        + f",\n{prefix}_{KIND}_id TEXT REFERENCES {TABLE}(preview_id) ON DELETE RESTRICT")


def _kind_check(prefix):
    cases = " OR ".join("(" + f"{prefix}_kind='{kind}' AND " + " AND ".join(
        f"{prefix}_{other}_id IS " + ("NOT NULL" if other == kind else "NULL")
        for other in KINDS) + ")" for kind in KINDS)
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
_insert = _once(_insert,
    ",".join(f"NEW.{name} AS {name}" for name in _columns(prior.KINDS)),
    ",".join(f"NEW.{name} AS {name}" for name in _columns(KINDS)))

_identities = prior._identities + f"""
UNION ALL SELECT '{KIND}',s.preview_id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,length(CAST(s.content_md AS BLOB)),
 'markdown_lf_v1',s.manifest_sha256,length(CAST(s.manifest_json AS BLOB)),
 r.workflow_version,'ready',r.job_id,r.preview_id,s.created_at,r.actor_user_id
FROM office_hybrid_wiki_snapshot s
 JOIN office_hybrid_publication_snapshot registered
   ON registered.snapshot_id=s.preview_id AND registered.job_id=s.job_id
 JOIN office_hybrid_wiki_run r ON r.preview_id=s.preview_id AND r.job_id=s.job_id"""

_candidates = """CREATE VIEW publication_requested_body_candidate AS
SELECT previous.* FROM (""" + prior._candidates.removeprefix(
    "CREATE VIEW publication_requested_body_candidate AS") + """) previous
WHERE NOT EXISTS(SELECT 1 FROM office_hybrid_upload_request q
  WHERE q.preview_id=previous.preview_id)
UNION ALL SELECT c.preview_id,c.actor_user_id,c.artifact_id,c.extraction_snapshot_id,
 c.source_snapshot_sha256,b.kind,b.snapshot_id,b.raw_sha256,b.normalized_sha256,
 b.size_bytes,b.normalizer_version,b.snapshot_created_at
FROM office_hybrid_publication_candidate c JOIN publication_body_identity b
 ON b.kind='office_hybrid_wiki' AND b.snapshot_id=c.snapshot_id"""

_unregistered = """EXISTS(SELECT 1 FROM office_hybrid_wiki_snapshot office
    WHERE office.preview_id IN (g.requested_office_hybrid_wiki_id,
      g.selected_office_hybrid_wiki_id)
      AND NOT EXISTS(SELECT 1 FROM office_hybrid_publication_snapshot registered
        WHERE registered.snapshot_id=office.preview_id))"""

_head, _rest = prior._lineage.split("WHEN ", 1)
_condition, _body = _rest.split("BEGIN ", 1)
_lineage = _head + f"""WHEN ({_condition.strip()}) AND NOT EXISTS(
    SELECT 1 FROM office_hybrid_publication_candidate candidate
    WHERE candidate.actor_user_id=NEW.created_by
      AND candidate.artifact_id=NEW.base_artifact_id
      AND candidate.extraction_snapshot_id=NEW.base_extraction_snapshot_id
      AND candidate.content_sha256=NEW.base_content_sha256) BEGIN """ + _body

_receipt = """SELECT 1 FROM office_hybrid_registered_publication registered
    JOIN publication_operation operation
      ON operation.id=registered.publication_operation_id
    WHERE registered.snapshot_id=OLD.id
      AND operation.actor_user_id=OLD.actor_user_id
      AND operation.artifact_id=OLD.artifact_id
      AND operation.extraction_snapshot_id=OLD.extraction_snapshot_id"""

V058_STATEMENTS = (
    "DROP TRIGGER publication_body_selection_insert",
    "DROP TRIGGER publication_body_selection_update",
    "DROP TRIGGER publication_body_selection_delete",
    "DROP TRIGGER native_display_body_selection_guard",
    "DROP TRIGGER pptx_role_body_selection_guard",
    "DROP TRIGGER pdf_body_selection_guard",
    "DROP TRIGGER pdf_visual_body_selection_guard",
    "DROP VIEW native_display_registered_publication",
    "DROP VIEW pptx_role_registered_publication",
    "DROP VIEW pdf_registered_publication",
    "DROP VIEW pdf_visual_registered_publication",
    "DROP VIEW publication_requested_body_candidate",
    "DROP VIEW publication_operation_body",
    "DROP VIEW publication_body_identity",
    "ALTER TABLE publication_body_selection RENAME TO publication_body_selection_v45",
    _table,
    "INSERT INTO publication_body_selection(" + ",".join(_columns(prior.KINDS)) + ") SELECT "
        + ",".join(_columns(prior.KINDS)) + " FROM publication_body_selection_v45",
    "DROP TABLE publication_body_selection_v45",
    _identities,
    _expand_ids(next(s for s in prior.V045_STATEMENTS
        if s.startswith("CREATE VIEW publication_operation_body "))),
    _candidates,
    _insert,
    *(s for s in prior.V045_STATEMENTS if s.startswith((
        "CREATE TRIGGER publication_body_selection_update ",
        "CREATE TRIGGER publication_body_selection_delete ",
        "CREATE TRIGGER native_display_body_selection_guard ",
        "CREATE TRIGGER pptx_role_body_selection_guard ",
        "CREATE TRIGGER pdf_body_selection_guard ",
        "CREATE TRIGGER pdf_visual_body_selection_guard "))),
    *(_expand_ids(next(s for s in prior.V045_STATEMENTS
        if s.startswith("CREATE VIEW " + name + " ")))
      for name in ("native_display_registered_publication",
          "pptx_role_registered_publication", "pdf_registered_publication",
          "pdf_visual_registered_publication")),
    f"""CREATE VIEW office_hybrid_registered_publication AS
      SELECT registered.snapshot_id,g.publication_operation_id
      FROM publication_body_selection g
      JOIN office_hybrid_publication_snapshot registered
        ON registered.snapshot_id IN (g.requested_office_hybrid_wiki_id,
          g.selected_office_hybrid_wiki_id)
      WHERE EXISTS({BODY_BOUND}) AND NOT ({_unregistered})""",
    f"""CREATE TRIGGER office_hybrid_body_selection_guard
      BEFORE INSERT ON publication_body_selection
      WHEN {_unregistered.replace('g.', 'NEW.')}
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_PUBLICATION_NOT_REGISTERED'); END""",
    "DROP TRIGGER trg_lineage_group_validity_insert",
    _lineage,
    "DROP TRIGGER office_hybrid_preview_consume_guard",
    f"""CREATE TRIGGER office_hybrid_preview_consume_guard
      BEFORE UPDATE OF status ON publication_preview
      WHEN NEW.status='consumed' AND OLD.rule_version='{RULE}'
        AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
          WHERE q.preview_id=OLD.id)
        AND NOT EXISTS({_receipt})
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_PUBLICATION_NOT_REGISTERED'); END""",
)

_prior_binding = prior.V045_POSTCONDITION_REPLACEMENTS[0][1]
V058_POSTCONDITION_REPLACEMENTS = ((_prior_binding,
    f"SELECT 'publication body binding mismatch' FROM publication_body_selection g WHERE NOT EXISTS({BODY_BOUND}) LIMIT 1"),)
V058_POSTCONDITION_QUERIES = (
    f"SELECT 'Office publication not registered' FROM publication_body_selection g WHERE {_unregistered} LIMIT 1",
)


__all__ = ["KIND", "V058_STATEMENTS", "V058_POSTCONDITION_QUERIES",
    "V058_POSTCONDITION_REPLACEMENTS"]
