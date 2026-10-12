"""Forward original-V10 prose with existing literal/chart append provenance.

The V34 composition shape is reused under one new policy identity. Historical
V94 guards remain exact ELSE branches; retained readings grant no numerical,
semantic or publication authority. Importing never migrates a database.
"""
from . import v094_pdf_chart_labels as previous
from . import v093_pdf_v10_chart_tables as composition
from .pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V36 = 'wiki-pdf-v10-numeric-append-v1'
PROMPT_SHA_V36 = '4e5f3cadda01fcc8a313d4336abc97a9aabd2328c7c368fc8420bbe56c4f8b6c'
PROMPT_V36_SHA256 = PROMPT_SHA_V36
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V36, PROMPT_SHA_V36))


def _pair(policy, assembly, prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES)+')'


def _v36(sql):
    return sql.replace(composition.POLICY_V34, POLICY_V36).replace(
        composition.PROMPT_V34_SHA256, PROMPT_SHA_V36)


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V36}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


_fixed_model = previous._fixed_model
_profile = composition._profile


def _chart_bundle_guard(bundle):
    config = f"json_extract({bundle},'$.configuration_json')"
    return composition._chart_bundle_guard(bundle)+f""" AND
        json_extract({config},'$.policy_version')='{POLICY_V36}'
        AND json_extract({config},'$.prompt_sha256')='{PROMPT_SHA_V36}'
        AND ({_fixed_model(config)})"""


def _policy_guard(value):
    config = f"json_extract({value},'$.configuration')"
    return _v36(composition._policy_guard(value))+f" AND ({_fixed_model(config)})"


_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_run_guard = _v36(composition._run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _run_guard)
_manifest_fields = composition._manifest_fields
_snapshot_guard = _once(_v36(composition._snapshot_guard),
    composition._chart_bundle_guard('ab.bundle_json'), _chart_bundle_guard('ab.bundle_json'))
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    composition.previous.batch.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1]+f",'{POLICY_V36}')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_policy = _once(previous._policy, f' OR {previous._policy_guard("NEW.policy_json")}',
    f' OR {previous._policy_guard("NEW.policy_json")} OR {_policy_guard("NEW.policy_json")}')
_layout_configuration_clause = _once(_v36(composition._layout_configuration_clause),
    composition._chart_bundle_guard('body.bundle_json'), _chart_bundle_guard('body.bundle_json'))
_family_guard = _once(_v36(composition._family_guard),
    _v36(composition._layout_configuration_clause), _layout_configuration_clause)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _family_guard)
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V35}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.','r.')}) ELSE ({previous._old_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V36}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({_prior_view_clause}) END")
_source_chart_guard = f"""CASE WHEN json_extract(json_extract(NEW.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V36}'
    THEN coalesce(({_chart_bundle_guard('NEW.bundle_json')}),0) ELSE ({previous._source_chart_guard}) END"""
_source_chart = f"""CREATE TRIGGER pdf_visual_chart_source_body_insert BEFORE INSERT ON pdf_visual_source_body
    WHEN NOT ({_source_chart_guard}) BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_CHART_SOURCE_INVALID'); END"""
_ready_chart = _branch(previous._ready_chart,
    "(SELECT json_extract(json_extract(bundle_json,'$.configuration_json'),'$.policy_version') FROM pdf_visual_source_body WHERE source_id=NEW.source_id)",
    f"EXISTS(SELECT 1 FROM pdf_visual_source_body b WHERE b.source_id=NEW.source_id AND NOT coalesce(({_chart_bundle_guard('b.bundle_json')}),0))")
_retained = previous._retained+f""" UNION SELECT json_extract(b.bundle_json,'$.chart_supplement.sha256'),s.artifact_id
    FROM pdf_visual_source_body b JOIN pdf_visual_source s ON s.id=b.source_id
    WHERE json_extract(json_extract(b.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V36}'
      AND ({_chart_bundle_guard('b.bundle_json')})"""

V095_STATEMENTS = (
    *('DROP TRIGGER '+s.split()[2] for s in composition.previous.layout.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v94',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v94',
    'DROP TABLE pdf_visual_workflow_request_v94', *composition.previous.layout.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert', _run, 'DROP TRIGGER pdf_visual_snapshot_body_insert', _snapshot,
    'DROP TRIGGER pdf_visual_batch_insert', _batch, 'DROP TRIGGER pdf_visual_attempt_insert', _attempt,
    'DROP TRIGGER pdf_visual_outcome_insert', _outcome, 'DROP TRIGGER pdf_visual_workflow_policy_insert', _policy,
    'DROP TRIGGER pdf_visual_workflow_run_family', _workflow_run_family,
    'DROP VIEW pdf_visual_registered_run', _registered_run,
    'DROP TRIGGER pdf_visual_chart_source_body_insert', _source_chart,
    'DROP TRIGGER pdf_visual_chart_ready_insert', _ready_chart,
    'DROP VIEW pdf_visual_retained_blob', _retained)


def _active(version, index):
    return next(row.query for row in prior.resolved_postconditions
        if (row.origin_version, row.origin_index) == (version, index))


_old_snapshot = _active(43, 18)
_new_snapshot = (_old_snapshot.split(' WHERE (', 1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ', 1)[1].rsplit(' BEGIN ', 1)[0].replace('NEW.', 'retained.')+') LIMIT 1')
_old_policy = _active(44, 5)
_new_policy = _once(_old_policy, f' OR {previous._policy_guard("b.policy_json")}',
    f' OR {previous._policy_guard("b.policy_json")} OR {_policy_guard("b.policy_json")}')
_old_pair = _active(46, 1)
_new_pair = _once(_old_pair, previous._pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'),
    _pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'))
_old_run = _active(47, 1)
_run_fields = ("json_extract(r.metadata_json,'$.policy_version')", "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run = _once(_old_run, previous._pair(*_run_fields), _pair(*_run_fields))
_old_pages = _active(43, 7)
_new_pages = _old_pages.replace(_old_versions, _new_versions)
_old_absent = _active(63, 1)
_new_absent = _old_absent.replace(_old_versions, _new_versions)
_old_source = _active(93, 1)
_new_source = f"SELECT 'chart source identity invalid' FROM pdf_visual_source_body retained WHERE NOT ({_source_chart_guard.replace('NEW.','retained.')}) LIMIT 1"
V095_POSTCONDITION_REPLACEMENTS = ((_old_snapshot, _new_snapshot), (_old_policy, _new_policy),
    (_old_pair, _new_pair), (_old_run, _new_run), (_old_pages, _new_pages),
    (_old_absent, _new_absent), (_old_source, _new_source))
