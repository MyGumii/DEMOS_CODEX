"""Opt-in native worker lineage. Historical v1-v29 and startup stay unchanged.

The storage-only rows of v29 are still readable. New registered uploads require
exact planner/batch/completion jobs. Provider reservations precede execution;
validated outcomes and job success are committed in the same kernel transaction.
"""
from .v014_wiki_authoring import _sha, _uuid
from .v029_native_authoring_storage import FAILURE_CODES

VERSION = "native-upload-v1"
PLAN = "native_wiki.plan"
BATCH = "native_wiki.author_batch"
COMPLETE = "native_wiki.complete"
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"
_REGISTERED = "EXISTS(SELECT 1 FROM native_workflow_request q JOIN native_authoring_run r ON r.preview_id=q.preview_id WHERE r.id=NEW.run_id)"
_FAILURES = "(" + ",".join("'" + code + "'" for code in FAILURE_CODES) + ")"

V030_STATEMENTS = (
    """CREATE TABLE native_workflow_scan(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        last_preview_id TEXT NOT NULL
    ) STRICT""",
    "INSERT INTO native_workflow_scan VALUES(1,'')",
    f"""CREATE TABLE native_workflow_request(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preview(id),
        parent_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        policy_size_bytes ANY NOT NULL CHECK(typeof(policy_size_bytes)='integer' AND policy_size_bytes BETWEEN 1 AND 65536),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
        deadline_at ANY NOT NULL CHECK(typeof(deadline_at)='integer' AND deadline_at>created_at)
    ) STRICT""",
    """CREATE TABLE native_workflow_policy_body(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES native_workflow_request(preview_id),
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'
            AND length(CAST(policy_json AS BLOB)) BETWEEN 1 AND 65536)
    ) STRICT""",
    f"""CREATE TABLE native_workflow_plan(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES native_workflow_request(preview_id),
        job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id),
        source_fence_sha256 TEXT NOT NULL CHECK({_sha('source_fence_sha256')})
    ) STRICT""",
    """CREATE TABLE native_workflow_planned(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES native_workflow_plan(preview_id),
        run_id TEXT NOT NULL UNIQUE REFERENCES native_authoring_run(id)
    ) STRICT""",
    """CREATE TABLE native_workflow_batch(
        run_id TEXT NOT NULL, ordinal ANY NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>0),
        job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id), PRIMARY KEY(run_id,ordinal),
        FOREIGN KEY(run_id,ordinal) REFERENCES native_authoring_batch(run_id,ordinal)
    ) STRICT""",
    """CREATE TABLE native_workflow_completion(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES native_authoring_run(id),
        job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id)
    ) STRICT""",
    """CREATE TABLE native_workflow_claim(
        attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES native_authoring_attempt(id),
        job_id TEXT NOT NULL REFERENCES durable_job(id),
        claim_owner TEXT NOT NULL CHECK(length(claim_owner) BETWEEN 1 AND 200),
        claim_attempt ANY NOT NULL CHECK(typeof(claim_attempt)='integer' AND claim_attempt BETWEEN 1 AND 2),
        UNIQUE(job_id,claim_attempt)
    ) STRICT""",
    f"""CREATE TABLE native_workflow_stop(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES native_workflow_request(preview_id),
        job_id TEXT NOT NULL REFERENCES durable_job(id),
        failure_code TEXT NOT NULL CHECK(failure_code IN {_FAILURES}),
        stopped_at ANY NOT NULL CHECK(typeof(stopped_at)='integer' AND stopped_at>=0)
    ) STRICT""",
    """CREATE TABLE native_workflow_rejection(
        job_id TEXT NOT NULL REFERENCES durable_job(id),
        claim_attempt ANY NOT NULL CHECK(typeof(claim_attempt)='integer' AND claim_attempt BETWEEN 1 AND 2),
        failure_code TEXT NOT NULL CHECK(failure_code='WORK_LIMIT'),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
        PRIMARY KEY(job_id,claim_attempt)
    ) STRICT""",
    f"""CREATE TRIGGER native_workflow_rejection_insert BEFORE INSERT ON native_workflow_rejection
    WHEN NOT EXISTS(SELECT 1 FROM durable_job j WHERE j.id=NEW.job_id AND j.state='running'
        AND j.job_type IN ('{PLAN}','{BATCH}','{COMPLETE}') AND j.handler_version='v1'
        AND j.attempt_count=NEW.claim_attempt AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW})
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_REJECTION_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_request_insert BEFORE INSERT ON native_workflow_request
    WHEN NOT EXISTS(SELECT 1 FROM publication_preview p JOIN durable_job j ON j.id=p.durable_job_id
        WHERE p.id=NEW.preview_id AND j.id=NEW.parent_job_id AND p.status='queued'
          AND p.rule_version='deterministic-ingest-native-tables-v1'
          AND j.job_type='publication_preview.extract' AND j.handler_version='v2' AND j.state='queued'
          AND j.actor_user_id=p.actor_user_id AND p.expires_at>=NEW.deadline_at
          AND NEW.created_at>=p.created_at AND NEW.deadline_at>{_NOW})
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_REQUEST_INVALID'); END""",
    """CREATE TRIGGER native_workflow_policy_insert BEFORE INSERT ON native_workflow_policy_body
    WHEN NOT EXISTS(SELECT 1 FROM native_workflow_request r WHERE r.preview_id=NEW.preview_id
        AND r.policy_size_bytes=length(CAST(NEW.policy_json AS BLOB)))
      OR json_extract(NEW.policy_json,'$.version') IS NOT 'native-upload-v1'
      OR json_type(NEW.policy_json,'$.durable_attempts') IS NOT 'integer'
      OR json_extract(NEW.policy_json,'$.durable_attempts') IS NOT 2
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_POLICY_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_plan_insert BEFORE INSERT ON native_workflow_plan
    WHEN NOT EXISTS(SELECT 1 FROM native_workflow_request q JOIN publication_preview p ON p.id=q.preview_id
        JOIN durable_job parent ON parent.id=q.parent_job_id JOIN durable_job j ON j.id=NEW.job_id
        WHERE q.preview_id=NEW.preview_id AND p.status='ready' AND parent.state='succeeded'
          AND q.deadline_at>{_NOW} AND j.state='queued' AND j.job_type='{PLAN}' AND j.handler_version='v1'
          AND j.max_attempts=2 AND j.actor_user_id=parent.actor_user_id AND j.space_id=parent.space_id
          AND json_extract(j.input_ref_json,'$.preview_id')=q.preview_id
          AND json_extract(j.expected_state_json,'$.policy_sha256')=q.policy_sha256
          AND json_extract(j.expected_state_json,'$.source_fence_sha256')=NEW.source_fence_sha256)
      OR EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_PLAN_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_run_insert BEFORE INSERT ON native_authoring_run
    WHEN EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=NEW.preview_id)
      AND NOT EXISTS(SELECT 1 FROM native_workflow_plan w JOIN durable_job j ON j.id=w.job_id
        JOIN native_workflow_request q ON q.preview_id=w.preview_id
        WHERE w.preview_id=NEW.preview_id AND j.state='running' AND j.cancel_requested=0
          AND j.claim_expires_at>{_NOW} AND q.deadline_at>{_NOW}
          AND w.source_fence_sha256=NEW.source_fence_sha256
          AND NOT EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=w.preview_id))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_RUN_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_planned_insert BEFORE INSERT ON native_workflow_planned
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_workflow_plan w ON w.preview_id=r.preview_id
        JOIN durable_job j ON j.id=w.job_id WHERE r.id=NEW.run_id AND r.preview_id=NEW.preview_id
          AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND r.batch_count=(SELECT count(*) FROM native_workflow_batch WHERE run_id=r.id))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_PLANNED_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_claim_insert BEFORE INSERT ON native_workflow_claim
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_attempt a
        JOIN native_workflow_batch w ON w.run_id=a.run_id AND w.ordinal=a.ordinal
        JOIN durable_job j ON j.id=w.job_id WHERE a.id=NEW.attempt_id AND j.id=NEW.job_id
          AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND j.claim_owner=NEW.claim_owner AND j.attempt_count=NEW.claim_attempt)
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_CLAIM_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_attempt_insert BEFORE INSERT ON native_authoring_attempt
    WHEN {_REGISTERED} AND NOT EXISTS(SELECT 1 FROM native_workflow_batch w
        JOIN durable_job j ON j.id=w.job_id JOIN native_authoring_run r ON r.id=w.run_id
        JOIN native_workflow_request q ON q.preview_id=r.preview_id
        WHERE w.run_id=NEW.run_id AND w.ordinal=NEW.ordinal
          AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND q.deadline_at>{_NOW} AND NOT EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=r.preview_id)
          AND NOT EXISTS(SELECT 1 FROM native_workflow_claim WHERE job_id=j.id AND claim_attempt=j.attempt_count))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_ATTEMPT_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_outcome_insert BEFORE INSERT ON native_authoring_outcome
    WHEN {_REGISTERED} AND NOT EXISTS(SELECT 1 FROM native_workflow_claim c
        JOIN durable_job j ON j.id=c.job_id JOIN native_authoring_run r ON r.id=NEW.run_id
        JOIN native_workflow_request q ON q.preview_id=r.preview_id
        WHERE c.attempt_id=NEW.attempt_id AND (
          (NEW.status IN ('validated','failed') AND j.state='running' AND j.cancel_requested=0
            AND j.claim_owner=c.claim_owner AND j.attempt_count=c.claim_attempt
            AND j.claim_expires_at>{_NOW} AND q.deadline_at>{_NOW}
            AND NOT EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=r.preview_id))
          OR (NEW.status='abandoned' AND (
            (NEW.failure_code='LEASE_LOST' AND (j.attempt_count>c.claim_attempt
                OR (j.state IN ('running','claimed') AND j.claim_expires_at<={_NOW})))
            OR EXISTS(SELECT 1 FROM native_workflow_stop stop WHERE stop.preview_id=r.preview_id
                AND stop.failure_code=NEW.failure_code)))))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_OUTCOME_INVALID'); END""",
    """CREATE TRIGGER native_workflow_stop_insert BEFORE INSERT ON native_workflow_stop
    WHEN NOT EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=NEW.preview_id
        AND (q.parent_job_id=NEW.job_id
          OR EXISTS(SELECT 1 FROM native_workflow_plan w WHERE w.preview_id=q.preview_id AND w.job_id=NEW.job_id)
          OR EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_workflow_batch w ON w.run_id=r.id
              WHERE r.preview_id=q.preview_id AND w.job_id=NEW.job_id)
          OR EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_workflow_completion w ON w.run_id=r.id
              WHERE r.preview_id=q.preview_id AND w.job_id=NEW.job_id)))
      OR EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
          WHERE r.preview_id=NEW.preview_id AND s.status='ready')
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_STOP_INVALID'); END""",
    f"""CREATE TRIGGER native_workflow_snapshot_insert BEFORE INSERT ON native_wiki_snapshot
    WHEN {_REGISTERED} AND NOT EXISTS(SELECT 1 FROM native_workflow_completion w
        JOIN durable_job j ON j.id=w.job_id JOIN native_authoring_run r ON r.id=w.run_id
        JOIN native_workflow_request q ON q.preview_id=r.preview_id WHERE r.id=NEW.run_id
          AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND q.deadline_at>{_NOW}
          AND NOT EXISTS(SELECT 1 FROM native_workflow_stop WHERE preview_id=r.preview_id)
          AND r.batch_count=(SELECT count(*) FROM native_workflow_batch b
              JOIN durable_job child ON child.id=b.job_id WHERE b.run_id=r.id AND child.state='succeeded'))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_SNAPSHOT_INVALID'); END""",
    """CREATE TRIGGER native_workflow_consume_guard BEFORE UPDATE OF status ON publication_preview
    WHEN NEW.status='consumed' AND EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_NOT_REGISTERED'); END""",
    """CREATE TRIGGER native_workflow_policy_delete BEFORE DELETE ON native_workflow_policy_body
    WHEN EXISTS(SELECT 1 FROM publication_preview p JOIN source_artifact a ON a.id=p.artifact_id
        WHERE p.id=OLD.preview_id AND a.body_retention_status='present')
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_POLICY_RETAINED'); END""",
    """CREATE TRIGGER native_workflow_policy_purge AFTER UPDATE OF body_retention_status ON source_artifact
    WHEN NEW.body_retention_status='purged'
    BEGIN DELETE FROM native_workflow_policy_body WHERE preview_id IN
        (SELECT id FROM publication_preview WHERE artifact_id=NEW.id); END""",
)

for table, kind in (("native_workflow_batch", BATCH), ("native_workflow_completion", COMPLETE)):
    batch = table.endswith("batch")
    check = ("AND json_extract(j.input_ref_json,'$.ordinal')=NEW.ordinal "
        "AND EXISTS(SELECT 1 FROM native_authoring_batch b WHERE b.run_id=r.id AND b.ordinal=NEW.ordinal "
        "AND b.batch_id=json_extract(j.input_ref_json,'$.batch_id'))") if batch else (
        "AND r.batch_count=(SELECT count(*) FROM native_workflow_batch b JOIN durable_job child "
        "ON child.id=b.job_id WHERE b.run_id=r.id AND child.state='succeeded')")
    V030_STATEMENTS += (f"""CREATE TRIGGER {table}_insert BEFORE INSERT ON {table}
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
        JOIN native_workflow_request q ON q.preview_id=r.preview_id
        JOIN durable_job parent ON parent.id=q.parent_job_id JOIN durable_job j ON j.id=NEW.job_id
        WHERE r.id=NEW.run_id AND s.status='pending' AND j.state='queued' AND j.job_type='{kind}'
          AND j.handler_version='v1' AND j.max_attempts=2 AND j.actor_user_id=parent.actor_user_id
          AND j.space_id=parent.space_id AND json_extract(j.input_ref_json,'$.run_id')=r.id
          AND json_extract(j.expected_state_json,'$.policy_sha256')=q.policy_sha256
          AND json_extract(j.expected_state_json,'$.source_fence_sha256')=r.source_fence_sha256
          AND json_extract(j.expected_state_json,'$.source_plan_sha256')=r.source_plan_sha256 {check})
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_CHILD_INVALID'); END""",)

_SUCCESS = {
    PLAN: """SELECT 1 FROM native_workflow_plan w JOIN native_workflow_planned p ON p.preview_id=w.preview_id
        JOIN native_authoring_run r ON r.id=p.run_id WHERE w.job_id=NEW.id
          AND json_extract(NEW.result_json,'$.run_id')=r.id
          AND json_extract(NEW.result_json,'$.batch_count')=r.batch_count""",
    BATCH: """SELECT 1 FROM native_workflow_batch w JOIN native_workflow_claim c ON c.job_id=w.job_id
        JOIN native_authoring_outcome o ON o.attempt_id=c.attempt_id
        JOIN native_authoring_outcome_body b ON b.attempt_id=o.attempt_id
        WHERE w.job_id=NEW.id AND c.claim_attempt=NEW.attempt_count AND o.status='validated'
          AND json_extract(NEW.result_json,'$.attempt_id')=o.attempt_id
          AND json_extract(NEW.result_json,'$.run_id')=w.run_id
          AND json_extract(NEW.result_json,'$.ordinal')=w.ordinal""",
    COMPLETE: """SELECT 1 FROM native_workflow_completion w JOIN native_wiki_snapshot s ON s.run_id=w.run_id
        JOIN native_wiki_snapshot_body b ON b.snapshot_id=s.id
        JOIN native_authoring_state state ON state.run_id=w.run_id
        WHERE w.job_id=NEW.id AND state.status='ready'
          AND json_extract(NEW.result_json,'$.snapshot_id')=s.id
          AND json_extract(NEW.result_json,'$.run_id')=w.run_id
          AND json_extract(NEW.result_json,'$.content_sha256')=s.content_sha256
          AND json_extract(NEW.result_json,'$.manifest_sha256')=s.manifest_sha256""",
}
_RESULT_TYPES = {
    PLAN: {"schema_version": "integer", "run_id": "text", "batch_count": "integer"},
    BATCH: {"schema_version": "integer", "run_id": "text", "ordinal": "integer", "attempt_id": "text"},
    COMPLETE: {"schema_version": "integer", "run_id": "text", "snapshot_id": "text",
        "content_sha256": "text", "manifest_sha256": "text"},
}
for ordinal, (kind, query) in enumerate(_SUCCESS.items()):
    types = _RESULT_TYPES[kind]
    shape = " OR ".join(f"json_type(NEW.result_json,'$.{key}') IS NOT '{value}'" for key, value in types.items())
    V030_STATEMENTS += (f"""CREATE TRIGGER native_workflow_success_{ordinal} BEFORE UPDATE OF state ON durable_job
    WHEN NEW.job_type='{kind}' AND NEW.handler_version='v1' AND NEW.state='succeeded'
      AND ({shape} OR (SELECT count(*) FROM json_each(NEW.result_json))<>{len(types)}
        OR (SELECT count(DISTINCT key) FROM json_each(NEW.result_json))<>{len(types)}
        OR json_extract(NEW.result_json,'$.schema_version') IS NOT 1 OR NOT EXISTS({query}))
    BEGIN SELECT RAISE(ABORT,'NATIVE_WORKFLOW_SUCCESS_INVALID'); END""",)

for table in ("native_workflow_request", "native_workflow_plan", "native_workflow_planned",
        "native_workflow_batch", "native_workflow_completion", "native_workflow_claim", "native_workflow_stop",
        "native_workflow_rejection"):
    for verb in ("UPDATE", "DELETE"):
        V030_STATEMENTS += (f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END",)
V030_STATEMENTS += ("CREATE TRIGGER native_workflow_policy_update BEFORE UPDATE ON native_workflow_policy_body "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END",)

V030_POSTCONDITION_QUERIES = (
    """SELECT 'native successful batch lacks validated result' FROM native_workflow_batch w
        JOIN durable_job j ON j.id=w.job_id WHERE (j.state='succeeded')<>
          EXISTS(SELECT 1 FROM native_authoring_outcome o WHERE o.run_id=w.run_id
              AND o.ordinal=w.ordinal AND o.status='validated') LIMIT 1""",
    """SELECT 'native successful completion lacks snapshot' FROM native_workflow_completion w
        JOIN durable_job j ON j.id=w.job_id WHERE (j.state='succeeded')<>
          EXISTS(SELECT 1 FROM native_wiki_snapshot s WHERE s.run_id=w.run_id) LIMIT 1""",
    """SELECT 'native attempt lacks claim' FROM native_authoring_attempt a
        JOIN native_authoring_run r ON r.id=a.run_id JOIN native_workflow_request q ON q.preview_id=r.preview_id
        LEFT JOIN native_workflow_claim c ON c.attempt_id=a.id WHERE c.attempt_id IS NULL LIMIT 1""",
)
