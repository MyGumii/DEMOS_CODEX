"""Registered display publication; historical catalogs and bodies are unchanged.

Live eligibility is separate from permanent receipt lineage. Publication uses
the existing atomic native/cross-kind contracts, including prior public-body
reuse. No mutable flag, retained private body or current TTL proves registration.
"""
from .v031_native_publication import V031_STATEMENTS, V031_POSTCONDITION_QUERIES
from .v031_publication_body_selection import _BOUND
from .v032_native_display_storage import DISPLAY_POLICY
from .v033_native_display_workflow import _family, PLAN, COMPLETE


def _native_invalid(index):
    query = V031_POSTCONDITION_QUERIES[index]
    if "FROM publication_native_selection s\n" not in query or not query.endswith(" LIMIT 1"):
        raise RuntimeError("Frozen native publication postcondition differs.")
    return query.split("WHERE ", 1)[1].rsplit(" LIMIT 1", 1)[0]


# Reuse all permanent native receipt contracts: body, exclusivity, requested
# lineage and equivalent-upload reuse. Do not replace them with a version list.
_NATIVE_VALID = " AND ".join("NOT (" + _native_invalid(i) + ")" for i in (1, 2, 3, 4))


def _unregistered(alias, columns):
    refs = ",".join(alias + "." + column for column in columns)
    return f"""EXISTS(SELECT 1 FROM native_wiki_snapshot ds
        JOIN native_authoring_run dr ON dr.id=ds.run_id
        WHERE ds.id IN ({refs}) AND json_extract(dr.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND NOT EXISTS(SELECT 1 FROM native_display_publication_snapshot registered
            WHERE registered.snapshot_id=ds.id))"""


_NATIVE_REFS = ("native_snapshot_id", "requested_snapshot_id")
_BODY_REFS = ("selected_native_wiki_id", "requested_native_wiki_id")
_candidate = next(s for s in V031_STATEMENTS if s.startswith("CREATE VIEW native_publication_candidate "))

V034_STATEMENTS = (
    f"""CREATE VIEW native_display_publication_snapshot AS
        SELECT s.id AS snapshot_id,r.id AS run_id,r.preview_id,r.actor_user_id,
          r.artifact_id,r.extraction_snapshot_id
        FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        JOIN native_display_registered_run registered ON registered.run_id=r.id
        JOIN native_authoring_state state ON state.run_id=r.id AND state.status='ready'
        JOIN native_workflow_plan plan ON plan.preview_id=r.preview_id
        JOIN durable_job planner ON planner.id=plan.job_id AND planner.job_type='{PLAN}'
          AND planner.state='succeeded' AND planner.cancel_requested=0
        JOIN native_workflow_completion completion ON completion.run_id=r.id
        JOIN durable_job finisher ON finisher.id=completion.job_id AND finisher.job_type='{COMPLETE}'
          AND finisher.state='succeeded' AND finisher.cancel_requested=0
        WHERE ({_family('planner', 'r.preview_id')}) AND ({_family('finisher', 'r.preview_id')})""",
    "DROP VIEW native_publication_candidate",
    _candidate + f""" AND (json_extract(r.metadata_json,'$.policy_version')='wiki-native-batch-authoring-v1'
        OR EXISTS(SELECT 1 FROM native_display_publication_snapshot registered
            WHERE registered.snapshot_id=s.id AND registered.run_id=r.id))""",
    "DROP VIEW native_display_registered_publication",
    f"""CREATE VIEW native_display_registered_publication AS
        SELECT registered.snapshot_id,s.publication_operation_id
        FROM publication_native_selection s JOIN native_display_publication_snapshot registered
          ON registered.snapshot_id IN (s.native_snapshot_id,s.requested_snapshot_id)
        LEFT JOIN native_wiki_snapshot n ON n.id=s.native_snapshot_id
        LEFT JOIN publication_operation p ON p.id=s.publication_operation_id
        WHERE ({_NATIVE_VALID}) AND NOT ({_unregistered('s', _NATIVE_REFS)})
        UNION
        SELECT registered.snapshot_id,g.publication_operation_id
        FROM publication_body_selection g JOIN native_display_publication_snapshot registered
          ON registered.snapshot_id IN (g.selected_native_wiki_id,g.requested_native_wiki_id)
        WHERE EXISTS({_BOUND}) AND NOT ({_unregistered('g', _BODY_REFS)})""",
    "DROP TRIGGER native_display_selection_guard",
    f"""CREATE TRIGGER native_display_selection_guard BEFORE INSERT ON publication_native_selection
        WHEN {_unregistered('NEW', _NATIVE_REFS)}
        BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
    "DROP TRIGGER native_display_body_selection_guard",
    f"""CREATE TRIGGER native_display_body_selection_guard BEFORE INSERT ON publication_body_selection
        WHEN {_unregistered('NEW', _BODY_REFS)}
        BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
    "DROP TRIGGER native_display_preview_consume_guard",
    f"""CREATE TRIGGER native_display_preview_consume_guard BEFORE UPDATE OF status ON publication_preview
        WHEN NEW.status='consumed' AND EXISTS(SELECT 1 FROM native_authoring_run r
            WHERE r.preview_id=OLD.id AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}')
          AND NOT EXISTS(SELECT 1 FROM native_authoring_run r
            JOIN native_wiki_snapshot s ON s.run_id=r.id
            JOIN native_display_registered_publication registered ON registered.snapshot_id=s.id
            JOIN publication_operation p ON p.id=registered.publication_operation_id
            WHERE r.preview_id=OLD.id AND p.actor_user_id=OLD.actor_user_id
              AND p.artifact_id=OLD.artifact_id AND p.extraction_snapshot_id=OLD.extraction_snapshot_id)
        BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
)

# The cumulative v32 queries validate every consumed/selected display snapshot
# against the permanent registered view, including after private-body purge.
V034_POSTCONDITION_QUERIES = ()
