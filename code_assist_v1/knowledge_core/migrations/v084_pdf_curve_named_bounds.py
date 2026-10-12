"""Forward named curve bounds policy; all historical guards remain exact.

Only the new policy/decoder identities are registered. Named bounds retain
strict numeric validation; historical guards remain strict. No response is
repaired and no persisted source-configuration field is added.
"""
from . import v083_pdf_bounded_text_partition as previous
from .pdf_bounded_text_partition_catalog import PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from . import v077_pdf_curve_domain as original_curve

POLICY_V25 = 'wiki-pdf-layout-retention-v14'
PROMPT_V25_SHA256 = previous.PROMPT_V24_SHA256
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V25, PROMPT_V25_SHA256))


def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(
        f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES) + ')'


def _v25(sql):
    return (sql.replace(previous.POLICY_V24, POLICY_V25)
        .replace('pdf-layout-retention-v13', 'pdf-layout-retention-v14'))


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return (f"{before} WHEN CASE WHEN {identity}='{POLICY_V25}' "
        f"THEN ({guard}) ELSE ({existing}) END BEGIN {after}")


def _bounds_valid(value, *, strict):
    shapes = ' OR '.join(_exact_object(value, {'min':low,'max':high})
        for low in ('integer','real') for high in ('integer','real'))
    relation = '<' if strict else '<='
    return (f"(({shapes}) AND abs(json_extract({value},'$.min'))<=1.7976931348623157e308 "
        f"AND abs(json_extract({value},'$.max'))<=1.7976931348623157e308 "
        f"AND json_extract({value},'$.min'){relation}json_extract({value},'$.max'))")


def _readings_valid(record):
    readings = f"json_extract({record},'$.curve_domain_readings')"
    fields = {'subject':'text','metric':'text','unit':'text','conditions':'array',
        'series':'array','limit':'text','condition_values':'array'}
    groups = '(' + _exact_object('reading.value', {**fields,'axis_reading':'null'}) + ' OR ' + \
        _exact_object('reading.value', {**fields,'axis_reading':'object'}) + ')'
    series = '(' + _exact_object('line.value', {'name':'text','values':'array','observed_x_bounds':'null'}) + ' OR ' + \
        _exact_object('line.value', {'name':'text','values':'array','observed_x_bounds':'object'}) + ')'
    return f"""(json_type({record},'$.curve_domain_readings')='array'
      AND json_array_length({readings}) BETWEEN 0 AND 500
      AND NOT EXISTS(SELECT 1 FROM json_each({readings}) reading
        WHERE NOT CASE WHEN reading.type='object' THEN coalesce((
          ({groups})
          AND json_array_length(reading.value,'$.conditions') BETWEEN 1 AND 500
          AND json_array_length(reading.value,'$.condition_values')=json_array_length(reading.value,'$.conditions')
          AND json_array_length(reading.value,'$.series') BETWEEN 1 AND 500
          AND NOT EXISTS(SELECT 1 FROM json_each(reading.value,'$.condition_values') x
            WHERE x.type NOT IN ('null','integer','real'))
          AND (json_type(reading.value,'$.axis_reading')='null' OR (
            ({_exact_object("json_extract(reading.value,'$.axis_reading')", {'x_bounds':'object','y_bounds':'object','x_scale':'text','y_scale':'text'})})
            AND json_extract(reading.value,'$.axis_reading.x_scale') IN ('linear','log10')
            AND json_extract(reading.value,'$.axis_reading.y_scale') IN ('linear','log10')
            AND {_bounds_valid("json_extract(reading.value,'$.axis_reading.x_bounds')", strict=True)}
            AND {_bounds_valid("json_extract(reading.value,'$.axis_reading.y_bounds')", strict=True)}))
          AND NOT EXISTS(SELECT 1 FROM json_each(reading.value,'$.series') line
            WHERE NOT CASE WHEN line.type='object' THEN coalesce((
              ({series}) AND json_array_length(line.value,'$.values') BETWEEN 1 AND 500
              AND NOT EXISTS(SELECT 1 FROM json_each(line.value,'$.values') y
                WHERE y.type NOT IN ('null','integer','real'))
              AND (json_type(line.value,'$.observed_x_bounds')='null'
                OR {_bounds_valid("json_extract(line.value,'$.observed_x_bounds')", strict=False)})),0) ELSE 0 END)
        ),0) ELSE 0 END)
      AND EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome outcome
        JOIN pdf_visual_authoring_outcome_body body ON body.attempt_id=outcome.attempt_id,
        json_each(body.response,'$.figures') raw_figure
        WHERE outcome.run_id=r.id AND outcome.status='validated'
          AND json_type(raw_figure.value,'$.estimated_values')='array'
          AND ({original_curve._same_json(readings, "json_extract(raw_figure.value,'$.estimated_values')")})
          AND EXISTS(SELECT 1 FROM json_each(body.transport_json,'$.image_input_transport.images') input
            WHERE json_extract(input.value,'$.source_id')=json_extract({record},'$.image_source_id')
              AND json_extract(input.value,'$.sha256')=json_extract({record},'$.image_sha256'))))"""


_v71 = previous._v71
_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_run_guard = _v25(previous._run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _run_guard)
_normalized = previous._normalized
_snapshot_guard = _v25(previous._snapshot_guard)
for _record in ('view.value','f.value'):
    _snapshot_guard = _once(_snapshot_guard,
        original_curve._readings_valid(_record), _readings_valid(_record))
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    _v71.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1] + ",'wiki-pdf-layout-retention-v14')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v24(previous._old_valid)
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_v25(_old_valid)}')
_layout_configuration_clause = _v25(previous._layout_configuration_clause)
_family_guard = _v25(previous._family_guard)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _family_guard)
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V24}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V25}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({_prior_view_clause}) END")

V084_STATEMENTS = (
    *('DROP TRIGGER ' + statement.split()[2] for statement in _v71.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v83',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v83',
    'DROP TABLE pdf_visual_workflow_request_v83', *_v71.writing._request_triggers,
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
_p = previous._v24(previous._p)
_new_policy = _once(_old_policy, f' OR {_p}', f' OR {_p} OR {_v25(_p)}')
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
V084_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot, _new_snapshot), (_old_policy, _new_policy), (_old_pair, _new_pair),
    (_old_run, _new_run), (_old_pages, _new_pages), (_old_absent, _new_absent),
)
