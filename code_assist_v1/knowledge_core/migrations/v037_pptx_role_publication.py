"""Fifth, honestly typed publication body; historical migrations stay frozen.

The common receipt preserves every older body kind and extends its exact stable
binding with real role snapshot FKs. Registration is permanent; candidate
eligibility alone depends on current authority, deadline and retained bodies.
This migration is not a startup or live feature-flag transition.
"""
from . import v031_publication_body_selection as prior
from .native_publication_catalog import V031_STATEMENTS, V031_POSTCONDITION_QUERIES
from .v032_native_display_storage import _once
from .v034_native_display_publication import V034_STATEMENTS
from .v036_pptx_role_workflow import _namespace, _family, PLAN, COMPLETE

KINDS = (*prior._KINDS, "pptx_role_wiki")
TABLES = (*prior._TABLES, "pptx_role_wiki_snapshot")


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


def _columns(kinds):
    return ("publication_operation_id", "preview_id", "requested_kind",
        *(f"requested_{kind}_id" for kind in kinds), "selected_kind",
        *(f"selected_{kind}_id" for kind in kinds), "selected_body_root_operation_id",
        "source_publication_operation_id", "source_space_id", "source_item_id", "source_revision_id")


def _expand_ids(sql):
    for prefix in ("requested", "selected"):
        sql = sql.replace(prior._id(prefix, "g."), body_id(prefix, "g."))
    return sql


BODY_BOUND = _expand_ids(prior._BOUND)
_table = next(s for s in V031_STATEMENTS if s.startswith("CREATE TABLE publication_body_selection("))
for _prefix in ("requested", "selected"):
    _table = _once(_table, prior._fields(_prefix), _fields(_prefix))
    _table = _once(_table, prior._kind_check(_prefix), _kind_check(_prefix))

_insert = next(s for s in V031_STATEMENTS if s.startswith("CREATE TRIGGER publication_body_selection_insert "))
_insert = _expand_ids(_insert)
_insert = _once(_insert, ",".join(f"NEW.{name} AS {name}" for name in _columns(prior._KINDS)),
    ",".join(f"NEW.{name} AS {name}" for name in _columns(KINDS)))

_candidate = next(s for s in V031_STATEMENTS if s.startswith("CREATE VIEW native_publication_candidate "))
_candidate = _once(_namespace(_candidate), "CREATE VIEW native_publication_candidate ",
    "CREATE VIEW pptx_role_publication_candidate ")
_candidate += """ AND EXISTS(SELECT 1 FROM pptx_role_publication_snapshot registered
    WHERE registered.snapshot_id=s.id AND registered.run_id=r.id)"""

_identities = prior._IDENTITIES + """
UNION ALL SELECT 'pptx_role_wiki',s.id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,s.content_size_bytes,'markdown_lf_v1',s.manifest_sha256,
 s.manifest_size_bytes,json_extract(r.metadata_json,'$.policy_version'),state.status,
 r.id,r.preview_id,s.created_at,r.actor_user_id
FROM pptx_role_wiki_snapshot s JOIN pptx_role_authoring_run r ON r.id=s.run_id
 JOIN pptx_role_authoring_state state ON state.run_id=r.id"""

if not prior._CANDIDATES.endswith("))))"):
    raise RuntimeError("Frozen requested candidate predicate differs.")
_candidates = prior._CANDIDATES[:-1] + """
 OR (b.kind='pptx_role_wiki' AND EXISTS(SELECT 1 FROM pptx_role_publication_candidate c
     WHERE c.snapshot_id=b.snapshot_id AND c.preview_id=p.id)))"""

_unregistered_role = """EXISTS(SELECT 1 FROM pptx_role_wiki_snapshot role
    WHERE role.id IN (g.requested_pptx_role_wiki_id,g.selected_pptx_role_wiki_id)
      AND NOT EXISTS(SELECT 1 FROM pptx_role_publication_snapshot registered
        WHERE registered.snapshot_id=role.id))"""

# Keep the existing native/display and equivalent-prior lineage branches; add
# only a fully eligible own role snapshot for a newly created both publication.
_lineage = next(s for s in reversed(V031_STATEMENTS)
    if s.startswith("CREATE TRIGGER trg_lineage_group_validity_insert "))
_head, _rest = _lineage.split("WHEN ", 1)
_condition, _body = _rest.split("BEGIN ", 1)
_lineage = _head + f"""WHEN ({_condition.strip()}) AND NOT EXISTS(
    SELECT 1 FROM pptx_role_publication_candidate candidate
    WHERE candidate.actor_user_id=NEW.created_by AND candidate.artifact_id=NEW.base_artifact_id
      AND candidate.extraction_snapshot_id=NEW.base_extraction_snapshot_id
      AND candidate.content_sha256=NEW.base_content_sha256) BEGIN """ + _body

_display_registered = next(s for s in V034_STATEMENTS
    if s.startswith("CREATE VIEW native_display_registered_publication "))
_display_registered = _once(_display_registered, prior._BOUND, BODY_BOUND)

_receipt = """SELECT 1 FROM pptx_role_authoring_run r
    JOIN pptx_role_wiki_snapshot s ON s.run_id=r.id
    JOIN pptx_role_registered_publication registered ON registered.snapshot_id=s.id
    JOIN publication_operation p ON p.id=registered.publication_operation_id
    WHERE r.preview_id=OLD.id AND p.actor_user_id=OLD.actor_user_id
      AND p.artifact_id=OLD.artifact_id AND p.extraction_snapshot_id=OLD.extraction_snapshot_id"""

V037_STATEMENTS = (
    # The migrator uses its explicit parent-table rebuild mode for this one
    # forward version. Copy exact old values, then restore every owned guard.
    "DROP TRIGGER publication_body_selection_insert",
    "DROP TRIGGER publication_body_selection_update",
    "DROP TRIGGER publication_body_selection_delete",
    "DROP TRIGGER native_display_body_selection_guard",
    "DROP VIEW native_display_registered_publication",
    "DROP VIEW publication_requested_body_candidate",
    "DROP VIEW publication_operation_body",
    "DROP VIEW publication_body_identity",
    "ALTER TABLE publication_body_selection RENAME TO publication_body_selection_v31",
    _table,
    "INSERT INTO publication_body_selection(" + ",".join(_columns(prior._KINDS)) + ") SELECT "
        + ",".join(_columns(prior._KINDS)) + " FROM publication_body_selection_v31",
    "DROP TABLE publication_body_selection_v31",
    _identities,
    _expand_ids(prior._ACTUAL),
    f"""CREATE VIEW pptx_role_publication_snapshot AS
        SELECT s.id AS snapshot_id,r.id AS run_id,r.preview_id,r.actor_user_id,
          r.artifact_id,r.extraction_snapshot_id
        FROM pptx_role_wiki_snapshot s JOIN pptx_role_authoring_run r ON r.id=s.run_id
        JOIN pptx_role_registered_run registered ON registered.run_id=r.id
        JOIN pptx_role_authoring_state state ON state.run_id=r.id AND state.status='ready'
        JOIN pptx_role_workflow_plan plan ON plan.preview_id=r.preview_id
        JOIN durable_job planner ON planner.id=plan.job_id AND planner.job_type='{PLAN}'
          AND planner.state='succeeded' AND planner.cancel_requested=0
        JOIN pptx_role_workflow_completion completion ON completion.run_id=r.id
        JOIN durable_job finisher ON finisher.id=completion.job_id AND finisher.job_type='{COMPLETE}'
          AND finisher.state='succeeded' AND finisher.cancel_requested=0
        WHERE ({_family('planner', 'r.preview_id')}) AND ({_family('finisher', 'r.preview_id')})""",
    _candidate,
    _candidates,
    _insert,
    *(s for s in V031_STATEMENTS if s.startswith((
        "CREATE TRIGGER publication_body_selection_update ",
        "CREATE TRIGGER publication_body_selection_delete "))),
    next(s for s in V034_STATEMENTS if s.startswith("CREATE TRIGGER native_display_body_selection_guard ")),
    _display_registered,
    "DROP VIEW pptx_role_registered_publication",
    f"""CREATE VIEW pptx_role_registered_publication AS
        SELECT registered.snapshot_id,g.publication_operation_id
        FROM publication_body_selection g JOIN pptx_role_publication_snapshot registered
          ON registered.snapshot_id IN (g.requested_pptx_role_wiki_id,g.selected_pptx_role_wiki_id)
        WHERE EXISTS({BODY_BOUND}) AND NOT ({_unregistered_role})""",
    f"""CREATE TRIGGER pptx_role_body_selection_guard BEFORE INSERT ON publication_body_selection
        WHEN {_unregistered_role.replace('g.', 'NEW.')}
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_PUBLICATION_NOT_REGISTERED'); END""",
    "DROP TRIGGER trg_lineage_group_validity_insert",
    _lineage,
    "DROP TRIGGER pptx_role_authoring_preview_consume_guard",
    "DROP TRIGGER pptx_role_workflow_consume_guard",
    *(f"""CREATE TRIGGER {name} BEFORE UPDATE OF status ON publication_preview
        WHEN NEW.status='consumed' AND (EXISTS(SELECT 1 FROM pptx_role_workflow_request WHERE preview_id=OLD.id)
            OR EXISTS(SELECT 1 FROM pptx_role_authoring_run WHERE preview_id=OLD.id))
          AND NOT EXISTS({_receipt})
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_PUBLICATION_NOT_REGISTERED'); END"""
      for name in ("pptx_role_authoring_preview_consume_guard", "pptx_role_workflow_consume_guard")),
)

_prior_binding = f"SELECT 'publication body binding mismatch' FROM publication_body_selection g WHERE NOT EXISTS({prior._BOUND}) LIMIT 1"
if V031_POSTCONDITION_QUERIES.count(_prior_binding) != 1:
    raise RuntimeError("Frozen common publication binding differs.")
V037_POSTCONDITION_REPLACEMENTS = ((_prior_binding,
    f"SELECT 'publication body binding mismatch' FROM publication_body_selection g WHERE NOT EXISTS({BODY_BOUND}) LIMIT 1"),)
V037_POSTCONDITION_QUERIES = (
    f"SELECT 'role publication not registered' FROM publication_body_selection g WHERE {_unregistered_role} LIMIT 1",
)
