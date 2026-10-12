"""Explicit display worker registration, without publication activation.

Permanent metadata binds each new request to its exact handler/source family.
Private policy/response bodies may be purged without erasing that lineage.
Historical migrations, v1 identities and v32 publication blockers stay intact.
"""
from .v014_wiki_authoring import _sha
from .v030_native_authoring_workflow import V030_STATEMENTS, VERSION, PLAN, BATCH, COMPLETE
from .v031_native_publication import V031_STATEMENTS
from .v032_native_display_storage import (
    DISPLAY_POLICY, DISPLAY_SOURCE_POLICY, DISPLAY_SLOT_POLICY, DISPLAY_ASSEMBLY_POLICY,
    DISPLAY_PROMPT_SHA256, _old_run, _display_run, _once, _exact_object,
    _display_configuration_valid,
)

DISPLAY_WORKFLOW_VERSION = "native-upload-display-v1"
DISPLAY_HANDLER_VERSION = "v2"
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


def _old(name):
    return next(s for s in V030_STATEMENTS if s.startswith("CREATE TRIGGER " + name + " "))


def _family(job, preview):
    # This predicate is also used for terminal/rejection paths: no private body,
    # clock, mutable feature flag, or current job state determines its identity.
    return f"""EXISTS(SELECT 1 FROM native_workflow_request fq
        LEFT JOIN native_display_workflow_request fd ON fd.preview_id=fq.preview_id
        WHERE fq.preview_id={preview}
          AND json_extract({job}.input_ref_json,'$.preview_id')=fq.preview_id
          AND json_extract({job}.expected_state_json,'$.policy_sha256')=fq.policy_sha256
          AND json_extract({job}.expected_state_json,'$.parent_job_id')=fq.parent_job_id
          AND ((fd.preview_id IS NULL AND {job}.handler_version='v1'
              AND json_extract({job}.expected_state_json,'$.version')='{VERSION}')
            OR (fd.preview_id IS NOT NULL AND {job}.handler_version=fd.handler_version
              AND json_extract({job}.expected_state_json,'$.version')=fd.workflow_version)))"""


def _run_binding(run, registration="d"):
    return f"""json_extract({run}.metadata_json,'$.configuration_sha256')={registration}.configuration_sha256
        AND json_extract({run}.metadata_json,'$.policy_version')={registration}.batch_policy_version
        AND json_extract({run}.metadata_json,'$.source_preparation_version')={registration}.source_preparation_version
        AND json_extract({run}.metadata_json,'$.source_slot_version')={registration}.slot_policy_version
        AND json_extract({run}.metadata_json,'$.assembly_version')={registration}.assembly_version
        AND json_extract({run}.metadata_json,'$.prompt_sha256')={registration}.prompt_sha256"""


def _display_policy_valid(value):
    return f"""{_exact_object(value, {'version': 'text', 'durable_attempts': 'integer', 'configuration': 'object'})}
        AND json_extract({value},'$.version')='{DISPLAY_WORKFLOW_VERSION}'
        AND json_extract({value},'$.durable_attempts')=2
        AND ({_display_configuration_valid("json_extract(" + value + ",'$.configuration')")})"""


_registered_planner = f"""SELECT 1 FROM native_display_workflow_request d
    JOIN native_workflow_request q ON q.preview_id=d.preview_id
    JOIN native_workflow_plan w ON w.preview_id=q.preview_id JOIN durable_job j ON j.id=w.job_id
    WHERE d.preview_id=NEW.preview_id AND {_run_binding('NEW')}
      AND ({_family('j', 'q.preview_id')}) AND j.job_type='{PLAN}'
      AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
      AND q.deadline_at>{_NOW} AND w.source_fence_sha256=NEW.source_fence_sha256
      AND NOT EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=q.preview_id)"""
_display_run_v33 = _once(_display_run,
    "OR EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=NEW.preview_id)",
    f"OR (EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=NEW.preview_id) "
    f"AND NOT EXISTS({_registered_planner}))")

V033_STATEMENTS = (
    f"""CREATE TABLE native_display_workflow_request(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES native_workflow_request(preview_id),
        parent_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id),
        workflow_version TEXT NOT NULL CHECK(workflow_version='{DISPLAY_WORKFLOW_VERSION}'),
        handler_version TEXT NOT NULL CHECK(handler_version='{DISPLAY_HANDLER_VERSION}'),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        configuration_sha256 TEXT NOT NULL CHECK({_sha('configuration_sha256')}),
        source_preparation_version TEXT NOT NULL CHECK(source_preparation_version='{DISPLAY_SOURCE_POLICY}'),
        slot_policy_version TEXT NOT NULL CHECK(slot_policy_version='{DISPLAY_SLOT_POLICY}'),
        assembly_version TEXT NOT NULL CHECK(assembly_version='{DISPLAY_ASSEMBLY_POLICY}'),
        batch_policy_version TEXT NOT NULL CHECK(batch_policy_version='{DISPLAY_POLICY}'),
        prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256='{DISPLAY_PROMPT_SHA256}'),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TRIGGER native_display_workflow_request_insert BEFORE INSERT ON native_display_workflow_request
    WHEN NOT EXISTS(SELECT 1 FROM native_workflow_request q JOIN durable_job j ON j.id=q.parent_job_id
        WHERE q.preview_id=NEW.preview_id AND q.parent_job_id=NEW.parent_job_id
          AND q.policy_sha256=NEW.policy_sha256 AND q.created_at=NEW.created_at
          AND j.state='queued' AND j.job_type='publication_preview.extract' AND j.handler_version='v2')
      OR EXISTS(SELECT 1 FROM native_workflow_policy_body WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM native_workflow_plan WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=NEW.preview_id)
      OR EXISTS(SELECT 1 FROM durable_job j WHERE j.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
          AND json_extract(j.input_ref_json,'$.preview_id')=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_REQUEST_INVALID'); END""",
    "CREATE TRIGGER native_display_workflow_request_update BEFORE UPDATE ON native_display_workflow_request "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END",
    "CREATE TRIGGER native_display_workflow_request_delete BEFORE DELETE ON native_display_workflow_request "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END",
    "DROP TRIGGER native_workflow_policy_insert",
    f"""CREATE TRIGGER native_workflow_policy_insert BEFORE INSERT ON native_workflow_policy_body
    WHEN CASE WHEN EXISTS(SELECT 1 FROM native_display_workflow_request WHERE preview_id=NEW.preview_id)
      THEN NOT coalesce((
        EXISTS(SELECT 1 FROM native_workflow_request q
            JOIN publication_preview p ON p.id=q.preview_id JOIN source_artifact a ON a.id=p.artifact_id
            WHERE q.preview_id=NEW.preview_id AND a.body_retention_status='present'
              AND q.policy_size_bytes=length(CAST(NEW.policy_json AS BLOB)))
        AND ({_display_policy_valid('NEW.policy_json')})),0)
      ELSE ({_old('native_workflow_policy_insert').split('WHEN ', 1)[1].split('BEGIN ', 1)[0].strip()}) END
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_POLICY_INVALID'); END""",
    f"""CREATE TRIGGER native_display_worker_identity BEFORE INSERT ON durable_job
    WHEN NEW.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
      AND NOT ({_family('NEW', "json_extract(NEW.input_ref_json,'$.preview_id')")})
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_VERSION_INVALID'); END""",
    "DROP TRIGGER native_run_insert",
    f"""CREATE TRIGGER native_run_insert BEFORE INSERT ON native_authoring_run
    WHEN CASE WHEN json_extract(NEW.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
      THEN ({_display_run_v33}) ELSE ({_old_run}) END
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_SOURCE_INVALID'); END""",
    f"""CREATE TRIGGER native_display_run_family BEFORE INSERT ON native_authoring_run
    WHEN EXISTS(SELECT 1 FROM native_display_workflow_request d WHERE d.preview_id=NEW.preview_id)
      AND NOT EXISTS({_registered_planner})
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_VERSION_INVALID'); END""",
    "DROP VIEW native_display_registered_run",
    f"""CREATE VIEW native_display_registered_run AS SELECT r.id AS run_id
        FROM native_authoring_run r JOIN native_display_workflow_request d ON d.preview_id=r.preview_id
        JOIN native_workflow_request q ON q.preview_id=d.preview_id
        JOIN native_workflow_plan w ON w.preview_id=q.preview_id
        JOIN native_workflow_planned p ON p.preview_id=w.preview_id AND p.run_id=r.id
        JOIN durable_job j ON j.id=w.job_id
        JOIN publication_preview preview ON preview.id=q.preview_id
        JOIN durable_job parent ON parent.id=q.parent_job_id AND parent.id=preview.durable_job_id
        JOIN extraction_snapshot extraction ON extraction.id=r.extraction_snapshot_id AND extraction.artifact_id=r.artifact_id
        WHERE d.parent_job_id=q.parent_job_id AND d.policy_sha256=q.policy_sha256
          AND d.created_at=q.created_at AND ({_run_binding('r')})
          AND r.parent_job_id=q.parent_job_id AND r.actor_user_id=preview.actor_user_id
          AND r.artifact_id=preview.artifact_id AND r.extraction_snapshot_id=preview.extraction_snapshot_id
          AND parent.actor_user_id=r.actor_user_id AND parent.job_type='publication_preview.extract'
          AND parent.handler_version='v2'
          AND w.source_fence_sha256=r.source_fence_sha256
          AND j.job_type='{PLAN}' AND ({_family('j', 'q.preview_id')})""",
)

# Widen only through the exact permanent registration predicate. v1/v2 cannot
# share a request merely because both handler names are present in the registry.
for _name, _preview in (
    ("native_workflow_plan_insert", "q.preview_id"),
    ("native_workflow_batch_insert", "q.preview_id"),
    ("native_workflow_completion_insert", "q.preview_id"),
    ("native_workflow_rejection_insert", "json_extract(j.input_ref_json,'$.preview_id')"),
):
    V033_STATEMENTS += ("DROP TRIGGER " + _name,
        _once(_old(_name), "j.handler_version='v1'", "(" + _family('j', _preview) + ")"))

# Do not weaken the retained v1 success guards. v2 must also commit its exact
# prepared result and corresponding domain effect in the same transaction.
for _ordinal in range(3):
    _name = f"native_workflow_success_{_ordinal}"
    _statement = _once(_old(_name), "CREATE TRIGGER " + _name + " ",
        f"CREATE TRIGGER native_display_workflow_success_{_ordinal} ")
    _statement = _once(_statement, "NEW.handler_version='v1'", "NEW.handler_version='v2'")
    V033_STATEMENTS += (_once(_statement, "OR NOT EXISTS(",
        "OR NOT (" + _family('NEW', "json_extract(NEW.input_ref_json,'$.preview_id')") + ") OR NOT EXISTS("),)

# The publication owner will replace this filter together with the retained
# v32 publication view/three blockers. A ready snapshot is not a published item.
_candidate = next(s for s in V031_STATEMENTS if s.startswith("CREATE VIEW native_publication_candidate "))
V033_STATEMENTS += ("DROP VIEW native_publication_candidate",
    _candidate + " AND json_extract(r.metadata_json,'$.policy_version')='wiki-native-batch-authoring-v1'")

V033_POSTCONDITION_QUERIES = (
    """SELECT 'display request metadata differs' FROM native_display_workflow_request d
        JOIN native_workflow_request q ON q.preview_id=d.preview_id
        WHERE d.parent_job_id<>q.parent_job_id OR d.policy_sha256<>q.policy_sha256
          OR d.created_at<>q.created_at LIMIT 1""",
    f"""SELECT 'native worker request version differs' FROM durable_job j
        WHERE j.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}')
          AND NOT ({_family('j', "json_extract(j.input_ref_json,'$.preview_id')")}) LIMIT 1""",
    f"""SELECT 'display retained policy invalid' FROM native_display_workflow_request d
        JOIN native_workflow_request q ON q.preview_id=d.preview_id
        JOIN publication_preview p ON p.id=q.preview_id JOIN source_artifact a ON a.id=p.artifact_id
        LEFT JOIN native_workflow_policy_body b ON b.preview_id=d.preview_id
        WHERE (a.body_retention_status='present')<>(b.preview_id IS NOT NULL)
          OR (b.preview_id IS NOT NULL AND (q.policy_size_bytes<>length(CAST(b.policy_json AS BLOB))
            OR NOT coalesce(({_display_policy_valid('b.policy_json')}),0))) LIMIT 1""",
)
