"""Register clarified native tick conditions without changing stored history.

The fresh writer keeps the existing categorical response/domain/source shapes.
Only its policy and decoder identities are registered. Historical trigger
branches, literal cells, raw provider responses and publication rows stay exact.
"""
from . import v090_pdf_native_categorical_axes as previous
from .pdf_native_categorical_axes_catalog import PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V32 = 'wiki-pdf-layout-retention-v21'
PROMPT_V32_SHA256 = previous.PROMPT_V31_SHA256
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V32, PROMPT_V32_SHA256))


def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(
        f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES) + ')'


def _v32(sql):
    return (sql.replace(previous.POLICY_V31, POLICY_V32)
        .replace('pdf-layout-retention-v20', 'pdf-layout-retention-v21'))


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return (f"{before} WHEN CASE WHEN {identity}='{POLICY_V32}' "
        f"THEN ({guard}) ELSE ({existing}) END BEGIN {after}")


_v71 = previous._v71
_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_run_guard = _v32(previous._run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _run_guard)
_normalized = previous._normalized
_snapshot_guard = _v32(previous._snapshot_guard)
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    _v71.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1] + ",'wiki-pdf-layout-retention-v21')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v31(previous._old_valid)
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_v32(_old_valid)}')
_layout_configuration_clause = _v32(previous._layout_configuration_clause)
_family_guard = _v32(previous._family_guard)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _family_guard)
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V31}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V32}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({_prior_view_clause}) END")

V091_STATEMENTS = (
    *('DROP TRIGGER ' + statement.split()[2] for statement in _v71.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v90',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v90',
    'DROP TABLE pdf_visual_workflow_request_v90', *_v71.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert', _run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert', _snapshot,
    'DROP TRIGGER pdf_visual_batch_insert', _batch,
    'DROP TRIGGER pdf_visual_attempt_insert', _attempt,
    'DROP TRIGGER pdf_visual_outcome_insert', _outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert', _policy,
    'DROP TRIGGER pdf_visual_workflow_run_family', _workflow_run_family,
    'DROP VIEW pdf_visual_registered_run', _registered_run,
)


def _active(version, index):
    return next(row.query for row in prior.resolved_postconditions
        if (row.origin_version, row.origin_index) == (version, index))


_old_snapshot = _active(43, 18)
_new_snapshot = (_old_snapshot.split(' WHERE (', 1)[0] + ' WHERE (' +
    _snapshot.split(' WHEN ', 1)[1].rsplit(' BEGIN ', 1)[0].replace('NEW.', 'retained.') + ') LIMIT 1')
_old_policy = _active(44, 5)
_p = previous._v31(previous._p)
_new_policy = _once(_old_policy, f' OR {_p}', f' OR {_p} OR {_v32(_p)}')
_old_pair = _active(46, 1)
_new_pair = _once(_old_pair,
    previous._pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'),
    _pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'))
_old_run = _active(47, 1)
_fields = ("json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')", "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run = _once(_old_run, previous._pair(*_fields), _pair(*_fields))
_old_pages = _active(43, 7)
_new_pages = _old_pages.replace(_old_versions, _new_versions)
_old_absent = _active(63, 1)
_new_absent = _old_absent.replace(_old_versions, _new_versions)
V091_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot, _new_snapshot), (_old_policy, _new_policy), (_old_pair, _new_pair),
    (_old_run, _new_run), (_old_pages, _new_pages), (_old_absent, _new_absent),
)
