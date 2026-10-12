"""Forward model-reported curve domains; frozen V76 behavior remains exact.

Source ownership, original pixels, literal tables and raw receipts are retained.
The new fields bind reported observations to their unmodified provider record;
the decoder checks internal consistency, not source accuracy or semantics.
"""
from . import v076_pdf_figure_roi as previous
from .pdf_figure_roi_catalog import PDF_FIGURE_ROI_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object

POLICY_V18 = 'wiki-pdf-layout-retention-v7'
PROMPT_V18_SHA256 = previous.PROMPT_V17_SHA256
CURVE_DOMAIN_VERSION = 'pdf-curve-domain-self-consistency-v1'
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V18, PROMPT_V18_SHA256))


def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(
        f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES) + ')'


def _v18(sql):
    return (sql.replace(previous.POLICY_V17, POLICY_V18)
        .replace('pdf-layout-retention-v6', 'pdf-layout-retention-v7'))


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return (f"{before} WHEN CASE WHEN {identity}='{POLICY_V18}' "
        f"THEN ({guard}) ELSE ({existing}) END BEGIN {after}")


def _same_json(left, right):
    # Provider object-key order is not canonical. Bind every path, JSON type
    # and literal atom without equating differently ordered serialized objects.
    return f"""((SELECT count(*) FROM json_tree({left}))=(SELECT count(*) FROM json_tree({right}))
      AND NOT EXISTS(SELECT 1 FROM json_tree({left}) original
        WHERE NOT EXISTS(SELECT 1 FROM json_tree({right}) retained
          WHERE retained.fullkey=original.fullkey AND retained.type=original.type
            AND retained.atom IS original.atom)))"""


def _readings_valid(record):
    readings = f"json_extract({record},'$.curve_domain_readings')"
    group_fields = {'subject':'text','metric':'text','unit':'text','conditions':'array',
        'series':'array','limit':'text','condition_values':'array'}
    groups = '(' + _exact_object('reading.value', {**group_fields,'axis_reading':'null'}) + ' OR ' + \
        _exact_object('reading.value', {**group_fields,'axis_reading':'object'}) + ')'
    series = '(' + _exact_object('line.value', {'name':'text','values':'array','observed_x_range':'null'}) + ' OR ' + \
        _exact_object('line.value', {'name':'text','values':'array','observed_x_range':'array'}) + ')'
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
            ({_exact_object("json_extract(reading.value,'$.axis_reading')", {'x_range':'array','y_range':'array','x_scale':'text','y_scale':'text'})})
            AND json_extract(reading.value,'$.axis_reading.x_scale') IN ('linear','log10')
            AND json_extract(reading.value,'$.axis_reading.y_scale') IN ('linear','log10')
            AND json_array_length(reading.value,'$.axis_reading.x_range')=2
            AND json_array_length(reading.value,'$.axis_reading.y_range')=2))
          AND NOT EXISTS(SELECT 1 FROM json_each(reading.value,'$.series') line
            WHERE NOT CASE WHEN line.type='object' THEN coalesce((
              ({series}) AND json_array_length(line.value,'$.values') BETWEEN 1 AND 500
              AND NOT EXISTS(SELECT 1 FROM json_each(line.value,'$.values') y
                WHERE y.type NOT IN ('null','integer','real'))
              AND (json_type(line.value,'$.observed_x_range')='null'
                OR json_array_length(line.value,'$.observed_x_range')=2)),0) ELSE 0 END)
        ),0) ELSE 0 END)
      AND EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome outcome
        JOIN pdf_visual_authoring_outcome_body body ON body.attempt_id=outcome.attempt_id,
        json_each(body.response,'$.figures') raw_figure
        WHERE outcome.run_id=r.id AND outcome.status='validated'
          AND json_type(raw_figure.value,'$.estimated_values')='array'
          AND ({_same_json(readings, "json_extract(raw_figure.value,'$.estimated_values')")})
          AND EXISTS(SELECT 1 FROM json_each(body.transport_json,'$.image_input_transport.images') input
            WHERE json_extract(input.value,'$.source_id')=json_extract({record},'$.image_source_id')
              AND json_extract(input.value,'$.sha256')=json_extract({record},'$.image_sha256'))))"""


_v71 = previous._v71
_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_v17_run_guard = previous._v17(previous._v16_run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _v18(_v17_run_guard))
_normalized = previous._normalized
_snapshot_guard = _v18(previous._snapshot_guard)
for _fields in (_v71._figure_fields, {**_v71._figure_fields,'views':'array'},
        {**_v71._figure_fields,'figure_roi':'object'}):
    _snapshot_guard = _once(_snapshot_guard, _exact_object('f.value', _fields),
        _exact_object('f.value', {**_fields,'curve_domain_readings':'array'}))
_snapshot_guard = _once(_snapshot_guard, _v71._view_check,
    '(' + _v71._view_check.replace(_exact_object('view.value', _v71._view_fields),
        _exact_object('view.value', {**_v71._view_fields,'curve_domain_readings':'array'})) + ' AND ' +
    "(json_type(f.value,'$.views') IS NULL OR (" +
    _same_json("json_extract(f.value,'$.curve_domain_readings')", "json_extract(f.value,'$.views[0].curve_domain_readings')") +
    " AND NOT EXISTS(SELECT 1 FROM json_each(f.value,'$.views') view WHERE NOT coalesce((" +
    _readings_valid('view.value') + '),0)))) AND ' + _readings_valid('f.value') + ')')
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    _v71.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1] + ",'wiki-pdf-layout-retention-v7')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v17(previous._old_valid)
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_v18(_old_valid)}')
_layout_configuration_clause = _v18(previous._layout_configuration_clause)
_v17_family_guard = _once(previous._v17(previous._v16_family_guard),
    previous._v17(previous._v16_layout_clause), previous._layout_configuration_clause)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _v18(_v17_family_guard))
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V17}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V18}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({_prior_view_clause}) END")

V077_STATEMENTS = (
    *('DROP TRIGGER ' + statement.split()[2] for statement in _v71.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v76',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v76',
    'DROP TABLE pdf_visual_workflow_request_v76', *_v71.writing._request_triggers,
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
_p = previous._v17(previous._p)
_new_policy = _once(_old_policy, f' OR {_p}', f' OR {_p} OR {_v18(_p)}')
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
V077_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot, _new_snapshot), (_old_policy, _new_policy), (_old_pair, _new_pair),
    (_old_run, _new_run), (_old_pages, _new_pages), (_old_absent, _new_absent),
)
