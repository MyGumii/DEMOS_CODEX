"""Forward-only protected PDF original/derivative binding for visual authoring.

The v42/v44/v45/v47 SQL remains frozen. This successor preserves the exact
normal-PDF route and accepts an authorized protected derivative only when its
v50 immutable extraction provenance and archived visual source agree.
"""

from . import v042_pdf_visual_source_storage as source
from . import v044_pdf_visual_authoring_workflow as workflow
from . import v045_pdf_visual_publication as publication
from . import v047_pdf_evidence_units as evidence


def _statement(statements, prefix):
    matches = tuple(sql for sql in statements if sql.startswith(prefix))
    assert len(matches) == 1, prefix
    return matches[0]


def _once(sql, old, new):
    assert sql.count(old) == 1, old
    return sql.replace(old, new)


def _extractor(extraction):
    normal = (f"{extraction}.extractor_name='canonical-pypdf' AND "
              f"{extraction}.extractor_version='rich-source-v3-bounded-tables'")
    protected = f"""{extraction}.extractor_name='canonical-approved-owner-pdf'
      AND {extraction}.extractor_version='protected-pdf-source-v1'
      AND EXISTS(SELECT 1 FROM protected_extraction_provenance protected
        WHERE protected.extraction_snapshot_id={extraction}.id
          AND protected.artifact_id={extraction}.artifact_id
          AND protected.original_sha256=(SELECT raw_sha256 FROM source_artifact
            WHERE id={extraction}.artifact_id)
          AND protected.source_kind='pdf_standard_encryption'
          AND protected.opening_method='standard_pdf_owner_password_v1'
          AND protected.extraction_method='pypdf-source-text-v1'
          AND protected.location_kind='page'
          AND protected.derivative_sha256 IS NOT NULL
          AND protected.derivative_sha256<>protected.original_sha256
          AND protected.visual_coverage='not_proven'
          AND protected.expected_locations=json_array_length(protected.coverage_json)
          AND NOT EXISTS(SELECT 1 FROM json_each(protected.coverage_json) location
            WHERE json_extract(location.value,'$.status')<>'complete'
              OR json_extract(location.value,'$.visual_examined')<>0))"""
    return f"(({normal}) OR ({protected}))"


def _bundle_bound(value):
    config = f"json_extract({value},'$.configuration_json')"
    normal = f"""({_source_bound(value)}
      AND CASE WHEN json_valid({config}) THEN
        json_type({config},'$.protected_binding') IS NULL ELSE 0 END)"""
    protected = f"""EXISTS(SELECT 1 FROM protected_extraction_provenance protected
      WHERE protected.extraction_snapshot_id=s.extraction_snapshot_id
        AND protected.artifact_id=s.artifact_id
        AND protected.original_sha256=a.raw_sha256
        AND protected.source_kind='pdf_standard_encryption'
        AND protected.opening_method='standard_pdf_owner_password_v1'
        AND protected.extraction_method='pypdf-source-text-v1'
        AND protected.location_kind='page'
        AND protected.visual_coverage='not_proven'
        AND protected.expected_locations=s.page_count
        AND protected.derivative_sha256 IS NOT NULL
        AND protected.derivative_sha256<>protected.original_sha256
        AND json_extract({value},'$.source.geometry.geometry_bytes')=s.geometry_size_bytes
        AND json_extract({value},'$.source.geometry.geometry_sha256')=s.geometry_sha256
        AND json_extract({value},'$.source.geometry.snapshot_id')=s.extraction_snapshot_id
        AND json_extract({value},'$.source.geometry.raw_sha256')=protected.derivative_sha256
        AND json_type({value},'$.source.geometry.raw_bytes')='integer'
        AND json_extract({value},'$.source.geometry.raw_bytes') BETWEEN 1 AND 67108864
        AND json_extract({value},'$.source.geometry.filename')=a.original_filename
        AND CASE WHEN json_valid({config}) THEN
          json_type({config},'$.protected_binding')='object'
          AND json_extract({config},'$.protected_binding.version')
            ='authorized-pdf-original-derivative-visual-v1'
          AND json_extract({config},'$.protected_binding.original_sha256')=a.raw_sha256
          AND json_extract({config},'$.protected_binding.derivative_sha256')=protected.derivative_sha256
          AND json_extract({config},'$.protected_binding.original_filename')=a.original_filename
          AND json_extract({config},'$.protected_binding.opening_method')=protected.opening_method
          AND json_extract({config},'$.protected_binding.expected_pages')=protected.expected_locations
          AND json_extract({config},'$.protected_binding.derivative_visual_source')
            =json_extract({value},'$.source')
          AND json_type({config},'$.protected_binding.visual_pages')='array'
          AND json_array_length({config},'$.protected_binding.visual_pages')
            =(SELECT count(*) FROM json_each(protected.coverage_json) location
              WHERE json_extract(location.value,'$.visual_present')=1)
          AND NOT EXISTS(SELECT 1 FROM json_each(protected.coverage_json) location
            WHERE json_extract(location.value,'$.status')<>'complete'
              OR json_extract(location.value,'$.visual_examined')<>0
              OR (json_extract(location.value,'$.visual_present')=1 AND NOT EXISTS(
                SELECT 1 FROM json_each({config},'$.protected_binding.visual_pages') visual
                WHERE visual.type='integer'
                  AND visual.value=json_extract(location.value,'$.ordinal'))))
          AND NOT EXISTS(SELECT 1 FROM json_each({config},'$.protected_binding.visual_pages') visual
            WHERE visual.type<>'integer'
              OR (SELECT count(*) FROM json_each(protected.coverage_json) preceding
                WHERE json_extract(preceding.value,'$.visual_present')=1
                  AND json_extract(preceding.value,'$.ordinal')<visual.value)
                <>CAST(visual.key AS INTEGER)
              OR NOT EXISTS(
              SELECT 1 FROM json_each(protected.coverage_json) location
              WHERE json_extract(location.value,'$.visual_present')=1
                AND json_extract(location.value,'$.ordinal')=visual.value))
          ELSE 0 END)"""
    return f"(({normal}) OR ({protected}))"


def _source_bound(value):
    return source._bundle_source_bound(value)


_source_insert = _statement(source.V042_STATEMENTS, "CREATE TRIGGER pdf_visual_source_insert ")
_source_insert = _once(_source_insert,
    "e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables'",
    _extractor("e"))

_source_body_insert = _statement(source.V042_STATEMENTS, "CREATE TRIGGER pdf_visual_source_body_insert ")
_source_body_insert = _once(_source_body_insert,
    _source_bound("NEW.bundle_json"), _bundle_bound("NEW.bundle_json"))

_run_insert = _statement(evidence.V047_STATEMENTS, "CREATE TRIGGER pdf_visual_run_insert ")
_run_extractor = "e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables'"
assert _run_insert.count(_run_extractor) == 3  # v1/v2/v3 retained policy branches.
_run_insert = _run_insert.replace(_run_extractor, _extractor("e"))

_workflow_run_family = _statement(workflow.V044_STATEMENTS,
    "CREATE TRIGGER pdf_visual_workflow_run_family ")
_configuration_clause = (
    "json_extract(NEW.metadata_json,'$.configuration_sha256')=q.configuration_sha256"
)
_protected_configuration_clause = f"""EXISTS(SELECT 1 FROM pdf_visual_source archive
      JOIN pdf_visual_source_body body ON body.source_id=archive.id
      JOIN protected_extraction_provenance protected
        ON protected.extraction_snapshot_id=archive.extraction_snapshot_id
      WHERE archive.id=NEW.source_archive_id AND archive.preview_id=q.preview_id
        AND protected.artifact_id=archive.artifact_id
        AND json_extract(body.bundle_json,'$.source.geometry.raw_sha256')
          =protected.derivative_sha256
        AND json_type(json_extract(body.bundle_json,'$.configuration_json'),
          '$.protected_binding')='object'
        AND json_extract(json_extract(body.bundle_json,'$.configuration_json'),
          '$.protected_binding.original_sha256')=protected.original_sha256
        AND json_extract(NEW.metadata_json,'$.configuration_size_bytes')
          =length(CAST(json_extract(body.bundle_json,'$.configuration_json') AS BLOB)))"""
_workflow_run_family = _once(_workflow_run_family, _configuration_clause,
    f"({_configuration_clause} OR {_protected_configuration_clause})")

_registered_run = _statement(workflow.V044_STATEMENTS, "CREATE VIEW pdf_visual_registered_run ")
_registered_run = _once(_registered_run,
    "e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables'",
    _extractor("e"))
_registered_run = _once(_registered_run,
    "json_extract(r.metadata_json,'$.configuration_sha256')=q.configuration_sha256",
    "(json_extract(r.metadata_json,'$.configuration_sha256')=q.configuration_sha256 OR "
    + _protected_configuration_clause.replace("NEW.", "r.") + ")")

_publication_candidate = _statement(publication.V045_STATEMENTS,
    "CREATE VIEW pdf_visual_publication_candidate ")
_publication_candidate = _once(_publication_candidate,
    "x.extractor_name='canonical-pypdf' AND x.extractor_version='rich-source-v3-bounded-tables'",
    _extractor("x"))

V051_STATEMENTS = (
    "DROP VIEW pdf_visual_publication_candidate",
    "DROP VIEW pdf_visual_registered_run",
    "DROP TRIGGER pdf_visual_run_insert",
    "DROP TRIGGER pdf_visual_workflow_run_family",
    "DROP TRIGGER pdf_visual_source_body_insert",
    "DROP TRIGGER pdf_visual_source_insert",
    _source_insert,
    _source_body_insert,
    _run_insert,
    _workflow_run_family,
    _registered_run,
    _publication_candidate,
)

_old_geometry_check = source.V042_POSTCONDITION_QUERIES[3]
_new_geometry_check = _once(_old_geometry_check,
    _source_bound("b.bundle_json"), _bundle_bound("b.bundle_json"))
V051_POSTCONDITION_REPLACEMENTS = ((_old_geometry_check, _new_geometry_check),)

__all__ = ["V051_STATEMENTS", "V051_POSTCONDITION_REPLACEMENTS"]
