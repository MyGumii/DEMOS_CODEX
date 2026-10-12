"""Forward original-bound appearance ROI; frozen V75 behavior remains exact.

No historical body, raw response, business rows, columns or tables are changed.
The added input capability proves pixel provenance, not caption semantics,
calibrated axes, curve measurements or numerical accuracy.
"""
from . import v075_pdf_layout_literal_appendix as previous
from .pdf_layout_literal_appendix_catalog import PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V17 = 'wiki-pdf-layout-retention-v6'
PROMPT_V17_SHA256 = previous.PROMPT_V16_SHA256
ROI_INPUT_VERSION = 'pdf-caption-bound-appearance-roi-v1'
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V17, PROMPT_V17_SHA256))


def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(
        f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v, h in POLICY_IDENTITIES) + ')'


def _v17(sql):
    return (sql.replace(previous.POLICY_V16, POLICY_V17)
        .replace('pdf-layout-retention-v5', 'pdf-layout-retention-v6'))


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return (f"{before} WHEN CASE WHEN {identity}='{POLICY_V17}' "
        f"THEN ({guard}) ELSE ({existing}) END BEGIN {after}")


_v71 = previous._v71
_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
_v16_run_guard = previous._v16(previous._v15_run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _v17(_v16_run_guard))
_normalized = previous._normalized
_snapshot_guard = _v17(previous._snapshot_guard)
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    _v71.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1] + ",'wiki-pdf-layout-retention-v6')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v16(previous._old_valid)
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_v17(_old_valid)}')

# Only this forward configuration permits the additional compact input identity.
# Exact renderer/caption-region/raw/pixel replay is performed by the source
# service outside SQL. SQL binds bounded metadata to this archive's source and
# checked layout; a persisted hash alone is never a usable ROI capability.
_config = _v71._config
_roi = f"json_extract({_config},'$.figure_roi_identity')"
_roi_fields = {'version':'text','source_raw_sha256':'text','layout_catalog_sha256':'text',
    'dpi':'integer','limits':'object','rois':'array'}
_limit_values = {'source_bytes':67_108_864,'pages':1000,'dpi':288,
    'image_pixels':16_000_000,'image_bytes':16_777_216,'manifest_bytes':16_384}
_roi_image_fields = {'page':'integer','unit_id':'text','unit_sha256':'text','bbox':'array',
    'page_pixel_bbox':'array','width':'integer','height':'integer','source_id':'text',
    'descriptor_sha256':'text','pixel_sha256':'text','encoded_sha256':'text','encoded_bytes':'integer'}
_limits = f"json_extract({_roi},'$.limits')"
_roi_configuration_valid = f"""(
    ({_exact_object(_roi, _roi_fields)})
    AND json_extract({_roi},'$.version')='{ROI_INPUT_VERSION}'
    AND json_extract({_roi},'$.source_raw_sha256')=json_extract(body.bundle_json,'$.source.geometry.raw_sha256')
    AND json_extract({_roi},'$.layout_catalog_sha256')=json_extract({_config},'$.layout_identity.catalog_sha256')
    AND json_extract({_roi},'$.dpi')=288
    AND ({_exact_object(_limits, {name:'integer' for name in _limit_values})})
    AND {' AND '.join(f"json_extract({_limits},'$.{name}')={value}" for name,value in _limit_values.items())}
    AND json_array_length({_roi},'$.rois') BETWEEN 0 AND 4096
    AND json_array_length({_roi},'$.rois')<=json_array_length({_config},'$.layout_identity.crops')
    AND (SELECT count(DISTINCT json_extract(value,'$.unit_id')) FROM json_each({_roi},'$.rois'))=json_array_length({_roi},'$.rois')
    AND (SELECT count(DISTINCT json_extract(value,'$.source_id')) FROM json_each({_roi},'$.rois'))=json_array_length({_roi},'$.rois')
    AND coalesce((SELECT sum(json_extract(value,'$.encoded_bytes')) FROM json_each({_roi},'$.rois')),0)
      <=json_extract({_config},'$.render_policy.maximum_total_bytes')
    AND coalesce((SELECT sum(json_extract(value,'$.width')*json_extract(value,'$.height')) FROM json_each({_roi},'$.rois')),0)
      <=json_extract({_config},'$.render_policy.maximum_total_pixels')
    AND NOT EXISTS(SELECT 1 FROM json_each({_roi},'$.rois') image
      WHERE NOT CASE WHEN image.type='object' THEN coalesce((
        ({_exact_object('image.value', _roi_image_fields)})
        AND json_extract(image.value,'$.page') BETWEEN 1 AND archive.page_count
        AND length(json_extract(image.value,'$.unit_id')) BETWEEN 1 AND 128
        AND length(json_extract(image.value,'$.source_id'))=69
        AND substr(json_extract(image.value,'$.source_id'),1,5)='PROI_'
        AND ({_sha("substr(json_extract(image.value,'$.source_id'),6)")})
        AND {' AND '.join('('+_sha("json_extract(image.value,'$."+name+"')")+')' for name in ('unit_sha256','descriptor_sha256','pixel_sha256','encoded_sha256'))}
        AND json_extract(image.value,'$.width')>=1 AND json_extract(image.value,'$.height')>=1
        AND json_extract(image.value,'$.width')*json_extract(image.value,'$.height')<=16000000
        AND json_extract(image.value,'$.encoded_bytes') BETWEEN 1 AND 16777216
        AND json_array_length(image.value,'$.bbox')=4
        AND NOT EXISTS(SELECT 1 FROM json_each(image.value,'$.bbox') coordinate
          WHERE coordinate.type NOT IN ('integer','real') OR coordinate.value<0 OR coordinate.value>1000000)
        AND json_extract(image.value,'$.bbox[0]')<json_extract(image.value,'$.bbox[2]')
        AND json_extract(image.value,'$.bbox[1]')<json_extract(image.value,'$.bbox[3]')
        AND json_array_length(image.value,'$.page_pixel_bbox')=4
        AND NOT EXISTS(SELECT 1 FROM json_each(image.value,'$.page_pixel_bbox') coordinate
          WHERE coordinate.type!='integer' OR coordinate.value<0 OR coordinate.value>2147483647)
        AND json_extract(image.value,'$.page_pixel_bbox[2]')-json_extract(image.value,'$.page_pixel_bbox[0]')=json_extract(image.value,'$.width')
        AND json_extract(image.value,'$.page_pixel_bbox[3]')-json_extract(image.value,'$.page_pixel_bbox[1]')=json_extract(image.value,'$.height')
        AND EXISTS(SELECT 1 FROM json_each({_config},'$.layout_identity.crops') crop
          WHERE json_extract(crop.value,'$.source_id')=json_extract(image.value,'$.unit_id'))
      ),0) ELSE 0 END))"""
_descriptor_fields = {'version':'text','raw_bytes':'integer','raw_sha256':'text','page_count':'integer',
    'page':'integer','unit_id':'text','unit_sha256':'text','layout_catalog_sha256':'text','coordinate_domain':'text',
    'page_dimensions':'array','bbox':'array','dpi':'integer','limits':'object','scale':'real',
    'page_pixel_dimensions':'array','page_pixel_bbox':'array','width':'integer','height':'integer',
    'crop_margins':'array','encoded_sha256':'text','encoded_bytes':'integer','pixel_sha256':'text',
    'pixel_mode':'text','appearance_relation':'text','caption_ownership_proven':'false',
    'axes_calibrated':'false','semantic_accuracy_verified':'false'}
_roi_manifest = "json_extract(NEW.manifest_json,'$.figure_roi_inputs')"
_snapshot_guard = _once(_snapshot_guard,
    _exact_object('NEW.manifest_json', previous._manifest_fields),
    _exact_object('NEW.manifest_json', {**previous._manifest_fields,'figure_roi_inputs':'object'}))
_snapshot_guard = _once(_snapshot_guard, _exact_object('f.value', _v71._figure_fields),
    '(' + _exact_object('f.value', _v71._figure_fields) + ' OR ' +
    _exact_object('f.value', {**_v71._figure_fields,'figure_roi':'object'}) + ')')
_roi_manifest_valid = f"""NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot snapshot
    JOIN pdf_visual_authoring_run run ON run.id=snapshot.run_id
    JOIN pdf_visual_source archive ON archive.id=run.source_archive_id
    JOIN pdf_visual_source_body body ON body.source_id=archive.id
    WHERE snapshot.id=NEW.snapshot_id
      AND ({_exact_object(_roi_manifest, {'version':'text','sources':'array','delivered_unit_ids':'array','semantic_accuracy_verified':'false'})})
      AND json_extract({_roi_manifest},'$.version')='{ROI_INPUT_VERSION}'
      AND ({_roi_configuration_valid})
      AND json_array_length({_roi_manifest},'$.sources')=json_array_length({_roi},'$.rois')
      AND NOT EXISTS(SELECT 1 FROM json_each({_roi_manifest},'$.sources') descriptor
        WHERE NOT CASE WHEN descriptor.type='object' THEN coalesce((
          ({_exact_object('descriptor.value', _descriptor_fields)})
          AND json_extract(descriptor.value,'$.version')='{ROI_INPUT_VERSION}'
          AND json_extract(descriptor.value,'$.raw_sha256')=json_extract({_roi},'$.source_raw_sha256')
          AND json_extract(descriptor.value,'$.raw_bytes')=json_extract(body.bundle_json,'$.source.geometry.raw_bytes')
          AND json_extract(descriptor.value,'$.page_count')=archive.page_count
          AND json_extract(descriptor.value,'$.layout_catalog_sha256')=json_extract({_roi},'$.layout_catalog_sha256')
          AND json_extract(descriptor.value,'$.dpi')=288 AND json_extract(descriptor.value,'$.scale')=4.0
          AND json_extract(descriptor.value,'$.pixel_mode')='RGB'
          AND json_extract(descriptor.value,'$.coordinate_domain')='zero-origin-uncropped-unrotated-page-canvas'
          AND json_extract(descriptor.value,'$.appearance_relation')='direct-pdf-static-figure-region-appearance'
          AND json(json_extract(descriptor.value,'$.limits'))=json(json_extract({_roi},'$.limits'))
          AND json_array_length(descriptor.value,'$.page_dimensions')=2
          AND NOT EXISTS(SELECT 1 FROM json_each(descriptor.value,'$.page_dimensions') coordinate
            WHERE coordinate.type NOT IN ('integer','real') OR coordinate.value<=0 OR coordinate.value>1000000)
          AND json_array_length(descriptor.value,'$.page_pixel_dimensions')=2
          AND NOT EXISTS(SELECT 1 FROM json_each(descriptor.value,'$.page_pixel_dimensions') coordinate
            WHERE coordinate.type!='integer' OR coordinate.value<=0 OR coordinate.value>2147483647)
          AND json_array_length(descriptor.value,'$.crop_margins')=4
          AND NOT EXISTS(SELECT 1 FROM json_each(descriptor.value,'$.crop_margins') coordinate
            WHERE coordinate.type NOT IN ('integer','real') OR coordinate.value<0 OR coordinate.value>1000000)
          AND EXISTS(SELECT 1 FROM json_each({_roi},'$.rois') image
            WHERE image.key=descriptor.key
              AND {' AND '.join("json_extract(descriptor.value,'$."+name+"')=json_extract(image.value,'$."+name+"')" for name in ('page','unit_id','unit_sha256','width','height','encoded_sha256','encoded_bytes','pixel_sha256'))}
              AND json(json_extract(descriptor.value,'$.bbox'))=json(json_extract(image.value,'$.bbox'))
              AND json(json_extract(descriptor.value,'$.page_pixel_bbox'))=json(json_extract(image.value,'$.page_pixel_bbox')))
        ),0) ELSE 0 END)
      AND json_array_length({_roi_manifest},'$.delivered_unit_ids') BETWEEN 0 AND json_array_length({_roi_manifest},'$.sources')
      AND (SELECT count(DISTINCT value) FROM json_each({_roi_manifest},'$.delivered_unit_ids'))=json_array_length({_roi_manifest},'$.delivered_unit_ids')
      AND json_array_length({_roi_manifest},'$.delivered_unit_ids')=(SELECT count(*) FROM json_each(NEW.manifest_json,'$.layout_details.figures') figure WHERE json_type(figure.value,'$.figure_roi')='object')
      AND NOT EXISTS(SELECT 1 FROM json_each({_roi_manifest},'$.delivered_unit_ids') delivered
        WHERE delivered.type!='text'
          OR EXISTS(SELECT 1 FROM json_each({_roi_manifest},'$.delivered_unit_ids') earlier
            WHERE earlier.key<delivered.key AND earlier.value>=delivered.value)
          OR NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.layout_details.figures') figure
            WHERE json_extract(figure.value,'$.unit_id')=delivered.value
              AND json_type(figure.value,'$.figure_roi')='object'))
      AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.layout_details.figures') figure
        WHERE json_type(figure.value,'$.figure_roi') IS NOT NULL
          AND (json_type(figure.value,'$.figure_roi')!='object'
            OR NOT EXISTS(SELECT 1 FROM json_each({_roi_manifest},'$.sources') descriptor
              WHERE json_extract(descriptor.value,'$.unit_id')=json_extract(figure.value,'$.unit_id')
                AND json(json_extract(figure.value,'$.figure_roi'))=json(descriptor.value))))
    )"""
_snapshot_guard += ' OR ' + _roi_manifest_valid
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_v16_layout_clause = previous._v16(previous._current_layout_clause)
_layout_configuration_clause = _once(_v17(_v16_layout_clause),
    f"AND json_type({_config},'$.layout_identity')='object'",
    f"AND json_type({_config},'$.layout_identity')='object' AND ({_roi_configuration_valid})")
_layout_configuration_clause = _once(_layout_configuration_clause,
    f"json_remove({_config},'$.layout_identity','$.protected_binding')",
    f"json_remove({_config},'$.layout_identity','$.protected_binding','$.figure_roi_identity')")
_v16_family_guard = previous._v16(previous._v15_family_guard)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')",
    _once(_v17(_v16_family_guard), _v17(_v16_layout_clause), _layout_configuration_clause))
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V16}' "
    f"THEN ({_v16_layout_clause.replace('NEW.', 'r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V17}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.', 'r.')}) ELSE ({_prior_view_clause}) END")

V076_STATEMENTS = (
    *('DROP TRIGGER ' + statement.split()[2] for statement in _v71.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v75',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v75',
    'DROP TABLE pdf_visual_workflow_request_v75', *_v71.writing._request_triggers,
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
_p = previous._v16(previous._p)
_new_policy = _once(_old_policy, f' OR {_p}', f' OR {_p} OR {_v17(_p)}')
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
V076_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot, _new_snapshot), (_old_policy, _new_policy), (_old_pair, _new_pair),
    (_old_run, _new_run), (_old_pages, _new_pages), (_old_absent, _new_absent),
)
