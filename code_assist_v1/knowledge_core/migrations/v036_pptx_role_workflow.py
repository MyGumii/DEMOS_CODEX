"""Dedicated role worker registration, without publication/startup activation.

Instantiate the frozen v30 durable claim/effect/retention protocol under owned
role names, then bind it to the exact v35 role source and policy. Historical
native/display jobs, tables and migration checks are not rewritten.
"""
import re

from .v014_wiki_authoring import _sha, _uuid
from .v030_native_authoring_workflow import V030_STATEMENTS, V030_POSTCONDITION_QUERIES
from .v032_native_display_storage import _once, _exact_object
from .v035_pptx_role_storage import (
    POLICY, SOURCE_POLICY, ROLE_VERSION, ASSEMBLY, LITERAL,
    PROMPT_SHA256, _configuration_valid,
)

VERSION = "pptx-role-upload-v1"
HANDLER_VERSION = "v1"
PLAN = "pptx_role_wiki.plan"
BATCH = "pptx_role_wiki.author_batch"
COMPLETE = "pptx_role_wiki.complete"
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"
_FIXED = {
    "workflow_version": VERSION, "handler_version": HANDLER_VERSION,
    "source_preparation_version": SOURCE_POLICY, "role_version": ROLE_VERSION,
    "assembly_version": ASSEMBLY, "batch_policy_version": POLICY,
    "prompt_sha256": PROMPT_SHA256, "literal_render_version": LITERAL,
}


def _namespace(sql):
    sql = re.sub(r"\b(?:native_workflow_[a-z_0-9]+|native_authoring_[a-z_0-9]+|native_wiki_snapshot[a-z_0-9]*)\b",
        lambda m: "pptx_role_" + m[0][len("native_"):], sql)
    return sql.replace("native_wiki.", "pptx_role_wiki.").replace("native-upload-v1", VERSION).replace(
        "NATIVE_", "PPTX_ROLE_")


def _job_shape(job):
    value, expected = job + ".input_ref_json", job + ".expected_state_json"
    input_fields = {"preview_id": "text"}
    run_id = f"json_extract({value},'$.run_id')"
    batch_id = f"json_extract({value},'$.batch_id')"
    plan_sha = f"json_extract({expected},'$.source_plan_sha256')"
    expected_fields = dict(schema_version="integer", version="text", policy_sha256="text",
        parent_job_id="text", source_fence_sha256="text", source_plan_sha256="text",
        _authorization_fence="object")
    shape = _exact_object(expected, expected_fields)
    shape = _once(shape, f"json_type({expected},'$._authorization_fence')='object'",
        f"json_type({expected},'$._authorization_fence') IN ('object','null')")
    shape = _once(shape, f"json_type({expected},'$.source_plan_sha256')='text'",
        f"(({job}.job_type='{PLAN}' AND json_type({expected},'$.source_plan_sha256')='null')"
        f" OR ({job}.job_type IN ('{BATCH}','{COMPLETE}')"
        f" AND json_type({expected},'$.source_plan_sha256')='text'"
        f" AND {_sha(plan_sha)}))")
    return f"""({shape})
        AND json_extract({expected},'$.schema_version')=1
        AND {_uuid(f"json_extract({value},'$.preview_id')")}
        AND {_uuid(f"json_extract({expected},'$.parent_job_id')")}
        AND {_sha(f"json_extract({expected},'$.policy_sha256')")}
        AND {_sha(f"json_extract({expected},'$.source_fence_sha256')")}
        AND (({job}.job_type='{PLAN}' AND {_exact_object(value, input_fields)})
          OR ({job}.job_type='{BATCH}'
            AND {_exact_object(value, {**input_fields, 'run_id': 'text', 'ordinal': 'integer', 'batch_id': 'text'})}
            AND {_uuid(run_id)} AND json_extract({value},'$.ordinal') BETWEEN 1 AND 4096
            AND length({batch_id})=67 AND substr({batch_id},1,3)='RB_'
            AND substr({batch_id},4) NOT GLOB '*[^0-9a-f]*')
          OR ({job}.job_type='{COMPLETE}'
            AND {_exact_object(value, {**input_fields, 'run_id': 'text'})} AND {_uuid(run_id)}))"""


def _family(job, preview):
    # Permanent registration: no private body, clock, lease or feature flag.
    return f"""EXISTS(SELECT 1 FROM pptx_role_workflow_request fq
        JOIN durable_job fp ON fp.id=fq.parent_job_id
        WHERE fq.preview_id={preview} AND ({_job_shape(job)})
          AND json_extract({job}.input_ref_json,'$.preview_id')=fq.preview_id
          AND json_extract({job}.expected_state_json,'$.policy_sha256')=fq.policy_sha256
          AND json_extract({job}.expected_state_json,'$.parent_job_id')=fq.parent_job_id
          AND json_extract({job}.expected_state_json,'$.version')=fq.workflow_version
          AND {job}.handler_version=fq.handler_version
          AND {job}.actor_kind='user' AND fp.actor_kind='user'
          AND {job}.actor_user_id=fp.actor_user_id AND {job}.space_id=fp.space_id
          AND {job}.max_attempts=2 AND {job}.cancellable=1
          AND {job}.authorization_snapshot_json IS fp.authorization_snapshot_json
          AND {job}.authorization_snapshot_sha256 IS fp.authorization_snapshot_sha256
          AND {job}.authorization_scope_epoch IS fp.authorization_scope_epoch
          AND {job}.authorization_part_id IS fp.authorization_part_id
          AND {job}.authorization_capabilities_json IS fp.authorization_capabilities_json
          AND json_extract({job}.expected_state_json,'$._authorization_fence')
              IS json_extract(fp.expected_state_json,'$._authorization_fence'))"""


def _run_binding(run, request="q"):
    fields = {"configuration_sha256": "configuration_sha256", "policy_version": "batch_policy_version",
        "source_preparation_version": "source_preparation_version", "source_role_version": "role_version",
        "assembly_version": "assembly_version", "prompt_sha256": "prompt_sha256",
        "literal_render_version": "literal_render_version"}
    return " AND ".join(f"json_extract({run}.metadata_json,'$.{key}')={request}.{column}"
        for key, column in fields.items())


def _policy_valid(value):
    return f"""{_exact_object(value, {'version': 'text', 'durable_attempts': 'integer', 'configuration': 'object'})}
        AND json_extract({value},'$.version')='{VERSION}'
        AND json_extract({value},'$.durable_attempts')=2
        AND ({_configuration_valid("json_extract(" + value + ",'$.configuration')")})"""


_statements = []
for original in V030_STATEMENTS:
    statement = _namespace(original)
    if original.startswith("CREATE TABLE native_workflow_request("):
        columns = ",\n".join(f"        {key} TEXT NOT NULL CHECK({key}='{value}')" for key, value in _FIXED.items())
        statement = _once(statement, "        deadline_at ANY", f"""{columns},
        configuration_sha256 TEXT NOT NULL CHECK({_sha('configuration_sha256')}),
        deadline_at ANY""")
    elif original.startswith("CREATE TRIGGER native_workflow_request_insert "):
        statement = _once(statement, "WHERE p.id=NEW.preview_id", """JOIN operation_dedup d ON d.id=j.dedup_id
        WHERE d.status='in_progress' AND d.operation_type='job:publication_preview.extract'
          AND d.actor_user_id=j.actor_user_id AND d.operation_id=j.operation_id
          AND d.request_sha256=j.input_sha256 AND p.id=NEW.preview_id""")
    elif original.startswith("CREATE TRIGGER native_workflow_policy_insert "):
        statement = f"""CREATE TRIGGER pptx_role_workflow_policy_insert BEFORE INSERT ON pptx_role_workflow_policy_body
        WHEN NOT coalesce((EXISTS(SELECT 1 FROM pptx_role_workflow_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN source_artifact a ON a.id=p.artifact_id
            WHERE r.preview_id=NEW.preview_id AND a.body_retention_status='present'
              AND r.policy_size_bytes=length(CAST(NEW.policy_json AS BLOB)))
            AND ({_policy_valid('NEW.policy_json')})),0)
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_POLICY_INVALID'); END"""
    for name, preview in (("native_workflow_plan_insert", "q.preview_id"),
            ("native_workflow_batch_insert", "q.preview_id"),
            ("native_workflow_completion_insert", "q.preview_id"),
            ("native_workflow_rejection_insert", "json_extract(j.input_ref_json,'$.preview_id')")):
        if original.startswith("CREATE TRIGGER " + name + " "):
            statement = _once(statement, "j.handler_version='v1'", "(" + _family('j', preview) + ")")
    if original.startswith("CREATE TRIGGER native_workflow_success_"):
        statement = _once(statement, "OR NOT EXISTS(",
            "OR NOT (" + _family('NEW', "json_extract(NEW.input_ref_json,'$.preview_id')") + ") OR NOT EXISTS(")
    _statements.append(statement)

_planner = f"""SELECT 1 FROM pptx_role_workflow_request q
    JOIN pptx_role_workflow_plan w ON w.preview_id=q.preview_id JOIN durable_job j ON j.id=w.job_id
    WHERE q.preview_id=NEW.preview_id AND ({_run_binding('NEW')})
      AND ({_family('j', 'q.preview_id')}) AND j.job_type='{PLAN}'
      AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
      AND q.deadline_at>{_NOW} AND w.source_fence_sha256=NEW.source_fence_sha256
      AND NOT EXISTS(SELECT 1 FROM pptx_role_workflow_stop WHERE preview_id=q.preview_id)"""

V036_STATEMENTS = (
    *_statements,
    f"""CREATE TRIGGER pptx_role_workflow_job_identity BEFORE INSERT ON durable_job
    WHEN NEW.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
      AND NOT coalesce(({_family('NEW', "json_extract(NEW.input_ref_json,'$.preview_id')")}),0)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_VERSION_INVALID'); END""",
    f"""CREATE TRIGGER pptx_role_workflow_run_family BEFORE INSERT ON pptx_role_authoring_run
    WHEN EXISTS(SELECT 1 FROM pptx_role_workflow_request q WHERE q.preview_id=NEW.preview_id)
      AND NOT EXISTS({_planner})
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_VERSION_INVALID'); END""",
    f"""CREATE TRIGGER pptx_role_workflow_exclusive_request BEFORE INSERT ON pptx_role_workflow_request
    WHEN EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM pptx_role_authoring_run WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM durable_job j WHERE j.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
          AND json_extract(j.input_ref_json,'$.preview_id')=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_PREVIEW_ALREADY_OWNED'); END""",
    """CREATE TRIGGER pptx_role_workflow_excludes_authored_request BEFORE INSERT ON publication_wiki_request
    WHEN EXISTS(SELECT 1 FROM pptx_role_workflow_request WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_PREVIEW_ALREADY_OWNED'); END""",
    """CREATE TRIGGER pptx_role_workflow_excludes_preserved_request BEFORE INSERT ON publication_preservation_request
    WHEN EXISTS(SELECT 1 FROM pptx_role_workflow_request WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_PREVIEW_ALREADY_OWNED'); END""",
    """CREATE TRIGGER pptx_role_workflow_excludes_native_request BEFORE INSERT ON native_workflow_request
    WHEN EXISTS(SELECT 1 FROM pptx_role_workflow_request WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_PREVIEW_ALREADY_OWNED'); END""",
    """CREATE TRIGGER pptx_role_workflow_excludes_native_run BEFORE INSERT ON native_authoring_run
    WHEN EXISTS(SELECT 1 FROM pptx_role_workflow_request WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_PREVIEW_ALREADY_OWNED'); END""",
    "DROP VIEW pptx_role_registered_run",
    f"""CREATE VIEW pptx_role_registered_run AS SELECT r.id AS run_id
        FROM pptx_role_authoring_run r JOIN pptx_role_workflow_request q ON q.preview_id=r.preview_id
        JOIN pptx_role_workflow_plan w ON w.preview_id=q.preview_id
        JOIN pptx_role_workflow_planned planned ON planned.preview_id=w.preview_id AND planned.run_id=r.id
        JOIN durable_job j ON j.id=w.job_id
        JOIN publication_preview p ON p.id=q.preview_id
        JOIN durable_job parent ON parent.id=q.parent_job_id AND parent.id=p.durable_job_id
        JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
        JOIN extraction_native_table_structure structure ON structure.snapshot_id=e.id AND structure.source_format='pptx'
        WHERE ({_run_binding('r')}) AND ({_family('j', 'q.preview_id')})
          AND r.parent_job_id=q.parent_job_id AND r.actor_user_id=p.actor_user_id
          AND r.artifact_id=p.artifact_id AND r.extraction_snapshot_id=p.extraction_snapshot_id
          AND parent.actor_user_id=r.actor_user_id AND parent.job_type='publication_preview.extract'
          AND parent.handler_version='v2' AND j.job_type='{PLAN}'
          AND w.source_fence_sha256=r.source_fence_sha256
          AND json_extract(j.expected_state_json,'$.source_fence_sha256')=r.source_fence_sha256
          AND json_type(j.expected_state_json,'$.source_plan_sha256')='null'
          AND j.state='succeeded'
          AND json_extract(j.result_json,'$.run_id')=r.id
          AND json_type(j.result_json,'$.batch_count')='integer'
          AND json_extract(j.result_json,'$.batch_count')=r.batch_count""",
)

V036_POSTCONDITION_QUERIES = (
    *(_namespace(q) for q in V030_POSTCONDITION_QUERIES),
    f"""SELECT 'role worker registration differs' FROM durable_job j
        WHERE j.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
          AND NOT coalesce(({_family('j', "json_extract(j.input_ref_json,'$.preview_id')")}),0) LIMIT 1""",
    f"""SELECT 'role retained workflow policy invalid' FROM pptx_role_workflow_request q
        JOIN publication_preview p ON p.id=q.preview_id JOIN source_artifact a ON a.id=p.artifact_id
        LEFT JOIN pptx_role_workflow_policy_body b ON b.preview_id=q.preview_id
        WHERE (a.body_retention_status='present')<>(b.preview_id IS NOT NULL)
          OR (b.preview_id IS NOT NULL AND (q.policy_size_bytes<>length(CAST(b.policy_json AS BLOB))
            OR NOT coalesce(({_policy_valid('b.policy_json')}),0))) LIMIT 1""",
    """SELECT 'role run lacks registered planner' FROM pptx_role_authoring_run r
        JOIN pptx_role_workflow_request q ON q.preview_id=r.preview_id
        WHERE NOT EXISTS(SELECT 1 FROM pptx_role_registered_run registered WHERE registered.run_id=r.id) LIMIT 1""",
    """SELECT 'role workflow shares preview with legacy' FROM pptx_role_workflow_request q
        WHERE EXISTS(SELECT 1 FROM native_workflow_request n WHERE n.preview_id=q.preview_id)
          OR EXISTS(SELECT 1 FROM native_authoring_run r WHERE r.preview_id=q.preview_id)
          OR EXISTS(SELECT 1 FROM publication_wiki_request w WHERE w.preview_id=q.preview_id)
          OR EXISTS(SELECT 1 FROM publication_preservation_request p WHERE p.preview_id=q.preview_id) LIMIT 1""",
)
