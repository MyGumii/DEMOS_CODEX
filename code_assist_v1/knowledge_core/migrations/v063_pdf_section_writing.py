"""Forward section-writing admission; frozen page-policy guards stay intact.

Text-only executions have absent image evidence, not fabricated image receipts.
Multi-page imagery is bound by actual retained transport images and replay.
"""
from . import v043_pdf_visual_authoring_storage as storage
from . import v046_pdf_visual_writing_policy as writing
from . import v047_pdf_evidence_units as evidence
from . import v061_pdf_owned_evidence_prompt as owned
from . import v062_pdf_fenced_response_snapshot as fenced
from . import v051_protected_pdf_visual_binding as protected
from .pdf_fenced_response_snapshot_catalog import PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG as prior_catalog
from .v032_native_display_storage import _once

POLICY_V5 = "wiki-pdf-section-writing-v5"
PROMPT_V5_SHA256 = "90fe8f554461852cd41eb576d6284217a37e12bdc84e58ddb091d0aa21288f73"
POLICY_IDENTITIES = (*owned.POLICY_IDENTITIES,(POLICY_V5,PROMPT_V5_SHA256))


def _statement(statements,prefix):
    return owned._statement(statements,prefix)


def _v5(sql):
    return sql.replace(evidence.POLICY_V3,POLICY_V5).replace(evidence.PROMPT_V3_SHA256,PROMPT_V5_SHA256)


def _pair(policy,assembly,prompt):
    return "("+" OR ".join(f"({policy}='{version}' AND {assembly}='{version}' AND {prompt}='{digest}')"
        for version,digest in POLICY_IDENTITIES)+")"


def _branch(statement,identity,new_guard):
    before,rest = statement.split(" WHEN ",1)
    old_guard,after = rest.rsplit(" BEGIN ",1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V5}' THEN ({new_guard}) ELSE ({old_guard}) END BEGIN {after}"


_table = _once(owned._table,owned._pair("batch_policy_version","assembly_version","prompt_sha256"),
    _pair("batch_policy_version","assembly_version","prompt_sha256"))
_run_guard = _v5(protected._run_insert.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0])
# The protected v51 guard already contains the v3 identity. Only that identity
# is replaced; the owner/derivative/current-rights constraints remain unchanged.
_run_insert = _branch(owned._run_insert,"json_extract(NEW.metadata_json,'$.policy_version')",_run_guard)

_old_policy_valid = evidence._policy_valid('NEW.policy_json')
_policy_guard = _statement(evidence.V047_STATEMENTS,'CREATE TRIGGER pdf_visual_workflow_policy_insert ')
_policy_insert = _once(_policy_guard,f"({_old_policy_valid})",
    f"({_old_policy_valid} OR {owned._v4(_old_policy_valid)} OR {_v5(_old_policy_valid)})")

_old_visual_check = storage._snapshot.split("      AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.visuals') v",1)[1].split(
    "      AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b,",1)[0]
_new_visual_check = """
          WHERE NOT coalesce((json_type(v.value,'$.page')='integer'
            AND json_extract(v.value,'$.page')=v.key+1
            AND json(json_extract(v.value,'$.image'))=json(json_extract(ab.bundle_json,'$.source.pages['||v.key||']'))
            AND ((json_extract(v.value,'$.analysis')='text_sufficient'
              AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome o
                JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id,
                json_each(ob.transport_json,'$.image_input_transport.images') image
                WHERE o.run_id=r.id AND o.status='validated'
                  AND json_extract(image.value,'$.source_id')=json_extract(v.value,'$.image.source_id')))
              OR (json_extract(v.value,'$.analysis')='model_reading'
                AND EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
                  JOIN pdf_visual_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
                  JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id,
                  json_each(ob.transport_json,'$.image_input_transport.images') image
                  WHERE b.run_id=r.id AND b.visual=1 AND b.batch_id=json_extract(v.value,'$.batch_id')
                    AND json_extract(image.value,'$.source_id')=json_extract(v.value,'$.source_id')
                    AND json_extract(image.value,'$.sha256')=json_extract(v.value,'$.image.sha256'))))),0))
"""
_snapshot_guard = _v5(evidence._v3_snapshot)
_snapshot_guard = _once(_snapshot_guard,_old_visual_check,_new_visual_check)
for old,new in (
    ('json_valid(ob.response)',f'json_valid({fenced._RESPONSE_JSON})'),
    ('json_type(ob.response)',f'json_type({fenced._RESPONSE_JSON})'),
    ('json_type(ob.response,',f'json_type({fenced._RESPONSE_JSON},'),
    ('json_extract(ob.response,',f'json_extract({fenced._RESPONSE_JSON},'),
):
    _snapshot_guard = _snapshot_guard.replace(old,new)
_snapshot_guard += """ OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id WHERE s.id=NEW.snapshot_id
      AND json_extract(NEW.manifest_json,'$.version')=json_extract(r.metadata_json,'$.policy_version')
      AND json_extract(NEW.manifest_json,'$.prompt_sha256')=json_extract(r.metadata_json,'$.prompt_sha256'))"""
_snapshot_insert = _branch(fenced._snapshot_insert,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)

_batch_table = _once(_statement(storage.V043_STATEMENTS,'CREATE TABLE pdf_visual_authoring_batch('),
    'image_bytes BETWEEN 1 AND 16777216','image_bytes BETWEEN 0 AND 33554432')
_old_batch_insert = _statement(storage.V043_STATEMENTS,'CREATE TRIGGER pdf_visual_batch_insert ')
_new_batch_guard = _old_batch_insert.split(' WHEN ',1)[1].rsplit('    BEGIN ',1)[0]
_new_batch_guard = _new_batch_guard.replace(
    "      OR EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b WHERE b.run_id=NEW.run_id\n"
    "          AND (b.page>NEW.page OR (b.page=NEW.page AND NEW.visual=1)))\n",'')
_new_batch_guard = _new_batch_guard.replace(
    "      OR (NEW.visual=0 AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b\n"
    "          WHERE b.run_id=NEW.run_id AND b.page=NEW.page AND b.visual=1))\n",'')
_new_batch_guard = _once(_new_batch_guard,'p.size_bytes=NEW.image_bytes',
    "((NEW.visual=0 AND NEW.image_bytes=0) OR (NEW.visual=1 AND NEW.image_bytes>0))")
_batch_insert = _branch(_old_batch_insert,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",_new_batch_guard)
_old_attempt_insert = _statement(storage.V043_STATEMENTS,'CREATE TRIGGER pdf_visual_attempt_insert ')
_attempt_insert = _once(_old_attempt_insert,
    "(SELECT count(*) FROM pdf_visual_authoring_batch WHERE run_id=r.id AND visual=1)=(SELECT page_count FROM pdf_visual_source WHERE id=r.source_archive_id)",
    f"(json_extract(r.metadata_json,'$.policy_version')='{POLICY_V5}' OR "
    "(SELECT count(*) FROM pdf_visual_authoring_batch WHERE run_id=r.id AND visual=1)=(SELECT page_count FROM pdf_visual_source WHERE id=r.source_archive_id))")

_outcome_table = _once(_statement(storage.V043_STATEMENTS,'CREATE TABLE pdf_visual_authoring_outcome('),
    "status<>'validated' OR evidence_binding='matched'","status<>'validated' OR evidence_binding IN ('matched','absent')")
_old_outcome_insert = _statement(storage.V043_STATEMENTS,'CREATE TRIGGER pdf_visual_outcome_insert ')
_outcome_insert = _once(_old_outcome_insert,"    BEGIN SELECT RAISE",
    f""" OR (NEW.status='validated' AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_run r
      JOIN pdf_visual_authoring_batch b ON b.run_id=r.id AND b.ordinal=NEW.ordinal
      WHERE r.id=NEW.run_id AND (NEW.evidence_binding='matched'
        OR (json_extract(r.metadata_json,'$.policy_version')='{POLICY_V5}'
          AND b.visual=0 AND b.image_bytes=0 AND NEW.evidence_binding='absent'))))
    BEGIN SELECT RAISE""")


def _table_rebuild(name,table,trigger):
    values = [value for value in storage.V043_STATEMENTS
        if value.startswith('CREATE TRIGGER ') and (' ON '+name+' ') in value]
    restored = [trigger if value.startswith('CREATE TRIGGER '+('pdf_visual_batch_insert' if name.endswith('batch') else 'pdf_visual_outcome_insert')+' ') else value
        for value in values]
    return (*('DROP TRIGGER '+value.split()[2] for value in values),
        f'ALTER TABLE {name} RENAME TO {name}_v62',table,
        f'INSERT INTO {name} SELECT * FROM {name}_v62',f'DROP TABLE {name}_v62',*restored)


V063_STATEMENTS = (
    *('DROP TRIGGER '+value.split()[2] for value in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v62',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v62',
    'DROP TABLE pdf_visual_workflow_request_v62',*writing._request_triggers,
    *_table_rebuild('pdf_visual_authoring_batch',_batch_table,_batch_insert),
    *_table_rebuild('pdf_visual_authoring_outcome',_outcome_table,_outcome_insert),
    _statement(storage.V043_STATEMENTS,'CREATE UNIQUE INDEX idx_pdf_visual_validated_batch '),
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt_insert,
    'DROP TRIGGER pdf_visual_run_insert',_run_insert,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy_insert,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot_insert,
)


def _active(version,index):
    values = [row.query for row in prior_catalog.resolved_postconditions
        if (row.origin_version,row.origin_index)==(version,index)]
    if len(values)!=1: raise ValueError('Missing exact predecessor postcondition')
    return values[0]


_old_snapshot_query = _active(43,18)
_new_snapshot_query = _once(_old_snapshot_query,fenced._new_snapshot_guard,
    _snapshot_insert.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.'))
_old_policy_query = _active(44,5)
_new_policy_query = _once(_old_policy_query,
    f"({evidence._policy_valid('b.policy_json')} OR {owned._v4(evidence._policy_valid('b.policy_json'))})",
    f"({evidence._policy_valid('b.policy_json')} OR {owned._v4(evidence._policy_valid('b.policy_json'))} OR {_v5(evidence._policy_valid('b.policy_json'))})")
_old_pair_query = _active(46,1)
_new_pair_query = _once(_old_pair_query,owned._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run_query = _active(47,1)
_new_run_query = _once(_old_run_query,owned._pair("json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')"),
    _pair("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')",
        "json_extract(r.metadata_json,'$.prompt_sha256')"))
_old_pages_query = _active(43,7)
_new_pages_query = _once(_old_pages_query,'WHERE (SELECT count(*)',
    f"WHERE json_extract(r.metadata_json,'$.policy_version')<>'{POLICY_V5}' AND (SELECT count(*)")
V063_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot_query,_new_snapshot_query),(_old_policy_query,_new_policy_query),
    (_old_pair_query,_new_pair_query),(_old_run_query,_new_run_query),(_old_pages_query,_new_pages_query),
)
V063_POSTCONDITION_QUERIES = (
    f"""SELECT 'unexpected text-only validated PDF outcome' FROM pdf_visual_authoring_outcome o
      JOIN pdf_visual_authoring_run r ON r.id=o.run_id
      JOIN pdf_visual_authoring_batch b ON b.run_id=r.id AND b.ordinal=o.ordinal
      WHERE o.status='validated' AND o.evidence_binding='absent'
        AND NOT (json_extract(r.metadata_json,'$.policy_version')='{POLICY_V5}' AND b.visual=0 AND b.image_bytes=0) LIMIT 1""",
)
