"""Forward literal-appendix registration; all historical raw/body guards stay exact.

No business rows, existing responses, columns or tables are rewritten. Literal
source retention is distinct from model semantics and visual numeric accuracy.
"""
from . import v074_pdf_layout_nonblank_groups as previous
from .pdf_layout_nonblank_groups_catalog import PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V16 = 'wiki-pdf-layout-retention-v5'
PROMPT_V16_SHA256 = previous.PROMPT_V15_SHA256
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V16, PROMPT_V16_SHA256))


def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(
        f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES) + ')'


def _v16(sql):
    return (sql.replace(previous.POLICY_V15, POLICY_V16)
        .replace('pdf-layout-retention-v4', 'pdf-layout-retention-v5'))


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return (f"{before} WHEN CASE WHEN {identity}='{POLICY_V16}' "
        f"THEN ({guard}) ELSE ({existing}) END BEGIN {after}")


_v71 = previous._v71
_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_v15_run_guard = previous._v15(previous._v14_run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _v16(_v15_run_guard))
_normalized = previous._normalized
_snapshot_guard = _v16(previous._snapshot_guard)
_manifest_fields = {**_v71._manifest_fields, 'literal_appendix': 'object'}
_snapshot_guard = _once(_snapshot_guard, _exact_object('NEW.manifest_json', _v71._manifest_fields),
    _exact_object('NEW.manifest_json', _manifest_fields))
_appendix = "json_extract(NEW.manifest_json,'$.literal_appendix')"
_appendix_fields = {'version':'text','records':'array','records_sha256':'text',
    'appendix_sha256':'text','output_byte_range':'array','semantic_accuracy_verified':'false'}
_record_fields = {'kind':'text','page':'integer','chunk_id':'text','block_id':'text',
    'start':'integer','end':'integer','text':'text','text_sha256':'text',
    'source_text_sha256':'text','range_domain':'text'}
# SQL validates retained structural provenance, not mathematical truth. Exact
# SHA, extraction replay and rendered appendix bytes remain checked outside SQL.
_appendix_valid = f"""NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id WHERE s.id=NEW.snapshot_id
      AND ({_exact_object(_appendix, _appendix_fields)})
      AND json_extract({_appendix},'$.version')='pdf-source-literal-appendix-v1'
      AND ({_sha(f"json_extract({_appendix},'$.records_sha256')")})
      AND ({_sha(f"json_extract({_appendix},'$.appendix_sha256')")})
      AND json_array_length({_appendix},'$.records') BETWEEN 0 AND 2048
      AND json_array_length({_appendix},'$.output_byte_range')=2
      AND json_type({_appendix},'$.output_byte_range[0]')='integer'
      AND json_type({_appendix},'$.output_byte_range[1]')='integer'
      AND json_extract({_appendix},'$.output_byte_range[0]')>=0
      AND json_extract({_appendix},'$.output_byte_range[1]')=s.content_size_bytes
      AND json_extract({_appendix},'$.output_byte_range[1]')>=json_extract({_appendix},'$.output_byte_range[0]')
      AND json_extract({_appendix},'$.output_byte_range[1]')-json_extract({_appendix},'$.output_byte_range[0]')<=262144
      AND ((json_array_length({_appendix},'$.records')=0
        AND json_extract({_appendix},'$.output_byte_range[0]')=s.content_size_bytes)
        OR (json_array_length({_appendix},'$.records')>0
        AND json_extract({_appendix},'$.output_byte_range[1]')>json_extract({_appendix},'$.output_byte_range[0]')))
      AND NOT EXISTS(SELECT 1 FROM json_each({_appendix},'$.records') lr
        WHERE NOT CASE WHEN lr.type='object' THEN coalesce((
          ({_exact_object('lr.value', _record_fields)})
          AND json_extract(lr.value,'$.kind') IN ('numbered_equation_context','explicit_definition_context','mixed_literal_context')
          AND json_extract(lr.value,'$.range_domain')='source_chunk_unicode_code_points'
          AND json_extract(lr.value,'$.start')>=0
          AND json_extract(lr.value,'$.end')>json_extract(lr.value,'$.start')
          AND length(json_extract(lr.value,'$.text'))=json_extract(lr.value,'$.end')-json_extract(lr.value,'$.start')
          AND length(CAST(json_extract(lr.value,'$.text') AS BLOB)) BETWEEN 1 AND 8192
          AND ({_sha("json_extract(lr.value,'$.text_sha256')")})
          AND ({_sha("json_extract(lr.value,'$.source_text_sha256')")})
          AND EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.chunks') chunk
            WHERE json_extract(chunk.value,'$.chunk_id')=json_extract(lr.value,'$.chunk_id')
              AND json_extract(chunk.value,'$.block_id')=json_extract(lr.value,'$.block_id')
              AND json_extract(chunk.value,'$.page')=json_extract(lr.value,'$.page')
              AND json_extract(chunk.value,'$.text_sha256')=json_extract(lr.value,'$.source_text_sha256')
              AND json_extract(lr.value,'$.end')<=json_extract(chunk.value,'$.end')-json_extract(chunk.value,'$.start'))
          AND EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.evidence_catalogs') ec
            WHERE EXISTS(SELECT 1 FROM json_each(ec.value,'$.catalog.source_descriptors') d
              WHERE json_extract(d.value,'$.source_id')=json_extract(lr.value,'$.chunk_id')
                AND json_extract(d.value,'$.block_id')=json_extract(lr.value,'$.block_id')
                AND json_extract(d.value,'$.page')=json_extract(lr.value,'$.page')
                AND json_extract(d.value,'$.text_sha256')=json_extract(lr.value,'$.source_text_sha256'))
              AND substr((SELECT group_concat(fragment,'') FROM (
                SELECT json_extract(u.value,'$.text') AS fragment
                FROM json_each(ec.value,'$.catalog.units') u
                WHERE json_extract(u.value,'$.source_id')=json_extract(lr.value,'$.chunk_id')
                ORDER BY json_extract(u.value,'$.range[0]'))),
                json_extract(lr.value,'$.start')+1,json_extract(lr.value,'$.end')-json_extract(lr.value,'$.start'))
                =json_extract(lr.value,'$.text'))
          AND NOT EXISTS(SELECT 1 FROM json_each({_appendix},'$.records') earlier
            WHERE earlier.key<lr.key
              AND json_extract(earlier.value,'$.chunk_id')=json_extract(lr.value,'$.chunk_id')
              AND json_extract(earlier.value,'$.end')>json_extract(lr.value,'$.start'))
          ),0) ELSE 0 END))"""
_snapshot_guard += ' OR ' + _appendix_valid
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    _v71.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1] + ",'wiki-pdf-layout-retention-v5')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v15(previous._old_valid)
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_v16(_old_valid)}')
_v15_family_guard = previous._v15(previous._v14_family_guard)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _v16(_v15_family_guard))
_current_layout_clause = previous._v15(previous.previous._v14(
    previous.previous.previous._v13(_v71._layout_configuration_clause)))
_prior_view_clause = (
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V15}' "
    f"THEN ({_current_layout_clause.replace('NEW.', 'r.')}) "
    f"ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V16}' "
    f"THEN ({_v16(_current_layout_clause).replace('NEW.', 'r.')}) "
    f"ELSE ({_prior_view_clause}) END")

V075_STATEMENTS = (
    *('DROP TRIGGER ' + s.split()[2] for s in _v71.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v74',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v74',
    'DROP TABLE pdf_visual_workflow_request_v74', *_v71.writing._request_triggers,
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
    return next(r.query for r in prior.resolved_postconditions
        if (r.origin_version, r.origin_index) == (version, index))


_old_snapshot = _active(43, 18)
_new_snapshot = (_old_snapshot.split(' WHERE (', 1)[0] + ' WHERE (' +
    _snapshot.split(' WHEN ', 1)[1].rsplit(' BEGIN ', 1)[0].replace('NEW.', 'retained.') + ') LIMIT 1')
_old_policy = _active(44, 5)
_p = previous._v15(previous._p)
_new_policy = _once(_old_policy, f' OR {_p}', f' OR {_p} OR {_v16(_p)}')
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
V075_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot, _new_snapshot), (_old_policy, _new_policy),
    (_old_pair, _new_pair), (_old_run, _new_run), (_old_pages, _new_pages), (_old_absent, _new_absent),
)
