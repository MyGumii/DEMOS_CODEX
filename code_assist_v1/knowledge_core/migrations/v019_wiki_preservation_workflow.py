"""Opt-in durable upload workflow for v18 preservation records.

The v14-v17 Wiki pipeline remains immutable. These tables bind a new upload
request to its extraction parent, planner, per-chunk jobs and finalizer. A
failed preservation request never selects the raw extraction as a Wiki.
"""
from .v014_wiki_authoring import _sha


V019_STATEMENTS = (
    f"""CREATE TABLE publication_preservation_request(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preview(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        version TEXT NOT NULL,
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'
            AND length(CAST(policy_json AS BLOB))<=16384),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE publication_preservation_source_plan(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preservation_request(preview_id) ON DELETE RESTRICT,
        durable_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_preservation_source_result(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preservation_request(preview_id) ON DELETE RESTRICT,
        outcome TEXT NOT NULL CHECK(outcome IN ('planned','failed')),
        run_id TEXT UNIQUE REFERENCES publication_preservation_run(id) ON DELETE RESTRICT,
        failure_code TEXT,
        parent_state TEXT NOT NULL CHECK(parent_state IN
            ('queued','retry','claimed','running','succeeded','failed','cancelled','dead')),
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        CHECK((outcome='planned' AND run_id IS NOT NULL AND failure_code IS NULL
                AND parent_state='succeeded')
           OR (outcome='failed' AND run_id IS NULL AND failure_code IN
                ('source_or_planner_unavailable','source_authority_unavailable','planner_terminal')))
    ) STRICT""",
    """CREATE TABLE publication_preservation_chunk_work(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        durable_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        PRIMARY KEY(run_id,ordinal),
        FOREIGN KEY(run_id,ordinal)
            REFERENCES publication_preservation_chunk(run_id,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_preservation_completion_work(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preservation_run(id) ON DELETE RESTRICT,
        durable_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TRIGGER preservation_request_parent BEFORE INSERT ON publication_preservation_request
    WHEN NOT EXISTS(SELECT 1 FROM publication_preview p
      JOIN durable_job j ON j.id=p.durable_job_id
      WHERE p.id=NEW.preview_id AND p.status='queued' AND p.created_at<=NEW.created_at
        AND j.id=NEW.parent_job_id AND j.state='queued' AND j.cancel_requested=0
        AND j.job_type='publication_preview.extract' AND j.handler_version='v1'
        AND j.actor_kind='user' AND j.actor_user_id=p.actor_user_id
        AND json_extract(j.input_ref_json,'$.preview_id')=p.id
        AND json_extract(j.input_ref_json,'$.artifact_id')=p.artifact_id
        AND NEW.version='preservation-upload-v1'
        AND json_extract(NEW.policy_json,'$.version')=NEW.version
        AND json_type(NEW.policy_json,'$.durable_attempts')='integer'
        AND json_extract(NEW.policy_json,'$.durable_attempts')=2)
    BEGIN SELECT RAISE(ABORT,'Invalid preservation upload request'); END""",
    """CREATE TRIGGER preservation_source_plan_binding BEFORE INSERT ON publication_preservation_source_plan
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_request r
      JOIN publication_preview p ON p.id=r.preview_id
      JOIN durable_job e ON e.id=r.parent_job_id
      JOIN durable_job j ON j.id=NEW.durable_job_id
      WHERE r.preview_id=NEW.preview_id AND p.status='ready' AND e.state='succeeded'
        AND j.state='queued' AND j.cancel_requested=0 AND j.actor_kind='user'
        AND j.actor_user_id=p.actor_user_id AND j.space_id=e.space_id
        AND j.job_type='publication_preservation.plan_upload' AND j.handler_version='v1'
        AND j.max_attempts=2 AND json_extract(j.input_ref_json,'$.preview_id')=r.preview_id
        AND json_extract(j.expected_state_json,'$.policy_sha256')=r.policy_sha256
        AND json_extract(j.expected_state_json,'$.parent_job_id')=r.parent_job_id)
      OR EXISTS(SELECT 1 FROM publication_preservation_source_result WHERE preview_id=NEW.preview_id)
    BEGIN SELECT RAISE(ABORT,'Invalid preservation planner binding'); END""",
    """CREATE TRIGGER preservation_upload_run_gate BEFORE INSERT ON publication_preservation_run
    WHEN EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=NEW.preview_id)
      AND NOT EXISTS(SELECT 1 FROM publication_preservation_request r
        JOIN publication_preservation_source_plan q ON q.preview_id=r.preview_id
        JOIN durable_job j ON j.id=q.durable_job_id
        WHERE r.preview_id=NEW.preview_id AND j.state='running' AND j.cancel_requested=0
          AND j.actor_user_id=NEW.actor_user_id
          AND json_extract(r.policy_json,'$.preservation_version')=NEW.policy_version
          AND json_extract(r.policy_json,'$.chunk_bytes')=json_extract(NEW.plan_json,'$.chunk_bytes')
          AND json_extract(r.policy_json,'$.model_policy')=json_extract(NEW.plan_json,'$.model_policy')
          AND json_extract(r.policy_json,'$.work_policy')=json_extract(NEW.plan_json,'$.work_policy')
          AND json_extract(r.policy_json,'$.execution_policy')=json_extract(NEW.plan_json,'$.execution_policy')
          AND json_extract(r.policy_json,'$.durable_attempts')=
              json_extract(NEW.plan_json,'$.reservation.durable_attempts'))
    BEGIN SELECT RAISE(ABORT,'Preservation run requires its frozen running planner'); END""",
    """CREATE TRIGGER preservation_chunk_work_binding BEFORE INSERT ON publication_preservation_chunk_work
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r
      JOIN publication_preservation_chunk c ON c.run_id=r.id AND c.ordinal=NEW.ordinal
      JOIN publication_preservation_request q ON q.preview_id=r.preview_id
      JOIN durable_job p ON p.id=q.parent_job_id
      JOIN durable_job j ON j.id=NEW.durable_job_id
      WHERE r.id=NEW.run_id AND r.status='pending' AND j.state='queued'
        AND j.cancel_requested=0 AND j.actor_kind='user' AND j.actor_user_id=r.actor_user_id
        AND j.space_id=p.space_id AND j.job_type='publication_preservation.author_chunk'
        AND j.handler_version='v1' AND j.max_attempts=2
        AND json_extract(j.input_ref_json,'$.run_id')=r.id
        AND json_extract(j.input_ref_json,'$.ordinal')=c.ordinal
        AND json_extract(j.input_ref_json,'$.chunk_id')=c.chunk_id
        AND json_extract(j.expected_state_json,'$.plan_sha256')=r.plan_sha256
        AND json_extract(j.expected_state_json,'$.parent_job_id')=r.parent_job_id
        AND json_extract(j.expected_state_json,'$.policy_sha256')=q.policy_sha256)
    BEGIN SELECT RAISE(ABORT,'Invalid preservation chunk work'); END""",
    """CREATE TRIGGER preservation_completion_work_binding BEFORE INSERT ON publication_preservation_completion_work
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r
      JOIN publication_preservation_request q ON q.preview_id=r.preview_id
      JOIN durable_job p ON p.id=q.parent_job_id
      JOIN durable_job j ON j.id=NEW.durable_job_id
      WHERE r.id=NEW.run_id AND r.status='pending' AND j.state='queued'
        AND j.cancel_requested=0 AND j.actor_kind='user' AND j.actor_user_id=r.actor_user_id
        AND j.space_id=p.space_id AND j.job_type='publication_preservation.complete'
        AND j.handler_version='v1' AND j.max_attempts=2
        AND json_extract(j.input_ref_json,'$.run_id')=r.id
        AND json_extract(j.expected_state_json,'$.plan_sha256')=r.plan_sha256
        AND json_extract(j.expected_state_json,'$.parent_job_id')=r.parent_job_id
        AND json_extract(j.expected_state_json,'$.policy_sha256')=q.policy_sha256
        AND r.chunk_count=(SELECT count(*) FROM publication_preservation_chunk_work w
          JOIN durable_job c ON c.id=w.durable_job_id
          WHERE w.run_id=r.id AND c.state='succeeded'))
    BEGIN SELECT RAISE(ABORT,'Invalid preservation completion work'); END""",
    """CREATE TRIGGER preservation_source_outcome BEFORE INSERT ON publication_preservation_source_result
    WHEN NEW.completed_at<(SELECT created_at FROM publication_preservation_request WHERE preview_id=NEW.preview_id)
      OR NEW.parent_state<>(SELECT j.state FROM publication_preservation_request r
          JOIN durable_job j ON j.id=r.parent_job_id WHERE r.preview_id=NEW.preview_id)
      OR (NEW.outcome='failed' AND EXISTS(
          SELECT 1 FROM publication_preservation_run WHERE preview_id=NEW.preview_id))
      OR (NEW.outcome='planned' AND NOT EXISTS(SELECT 1 FROM publication_preservation_run r
          JOIN publication_preservation_request q ON q.preview_id=r.preview_id
          JOIN publication_preservation_source_plan s ON s.preview_id=q.preview_id
          JOIN durable_job j ON j.id=s.durable_job_id
          WHERE r.id=NEW.run_id AND r.preview_id=NEW.preview_id AND r.status='pending'
            AND j.state='running' AND j.cancel_requested=0
            AND r.chunk_count=(SELECT count(*) FROM publication_preservation_chunk_work w
              JOIN durable_job c ON c.id=w.durable_job_id
              WHERE w.run_id=r.id AND c.state='queued' AND c.cancel_requested=0)))
    BEGIN SELECT RAISE(ABORT,'Invalid preservation source outcome'); END""",
    """CREATE TRIGGER preservation_planner_success BEFORE UPDATE OF state ON durable_job
    WHEN NEW.job_type='publication_preservation.plan_upload' AND NEW.handler_version='v1'
      AND NEW.state='succeeded' AND NOT EXISTS(SELECT 1 FROM publication_preservation_source_plan q
        JOIN publication_preservation_source_result x ON x.preview_id=q.preview_id AND x.outcome='planned'
        JOIN publication_preservation_run r ON r.id=x.run_id
        WHERE q.durable_job_id=NEW.id
          AND json_extract(NEW.result_json,'$.schema_version')=1
          AND json_extract(NEW.result_json,'$.preview_id')=q.preview_id
          AND json_extract(NEW.result_json,'$.run_id')=r.id
          AND json_extract(NEW.result_json,'$.plan_sha256')=r.plan_sha256
          AND json_extract(NEW.result_json,'$.chunk_count')=r.chunk_count)
    BEGIN SELECT RAISE(ABORT,'Preservation planner success lacks its committed effect'); END""",
    """CREATE TRIGGER preservation_chunk_success BEFORE UPDATE OF state ON durable_job
    WHEN NEW.job_type='publication_preservation.author_chunk' AND NEW.handler_version='v1'
      AND NEW.state='succeeded' AND NOT EXISTS(SELECT 1 FROM publication_preservation_chunk_work w
        JOIN publication_preservation_chunk c ON c.run_id=w.run_id AND c.ordinal=w.ordinal
        WHERE w.durable_job_id=NEW.id
          AND json_extract(NEW.result_json,'$.schema_version')=1
          AND json_extract(NEW.result_json,'$.run_id')=w.run_id
          AND json_extract(NEW.result_json,'$.ordinal')=w.ordinal
          AND json_extract(NEW.result_json,'$.chunk_id')=c.chunk_id
          AND json_extract(NEW.result_json,'$.receipt_count')=(SELECT count(*)
            FROM publication_preservation_receipt q
            JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
              AND b.ordinal=q.ordinal AND b.sequence=q.sequence
            WHERE q.run_id=w.run_id AND q.ordinal=w.ordinal)
          AND json_extract(NEW.result_json,'$.receipt_count') IN (2,4))
    BEGIN SELECT RAISE(ABORT,'Preservation chunk success lacks validated receipts'); END""",
    """CREATE TRIGGER preservation_completion_success BEFORE UPDATE OF state ON durable_job
    WHEN NEW.job_type='publication_preservation.complete' AND NEW.handler_version='v1'
      AND NEW.state='succeeded' AND NOT EXISTS(SELECT 1 FROM publication_preservation_completion_work w
        JOIN publication_preservation_run r ON r.id=w.run_id AND r.status='ready'
        JOIN preserved_wiki_snapshot s ON s.run_id=r.id
        JOIN preserved_wiki_snapshot_body b ON b.snapshot_id=s.id
        WHERE w.durable_job_id=NEW.id
          AND json_extract(NEW.result_json,'$.schema_version')=1
          AND json_extract(NEW.result_json,'$.run_id')=r.id
          AND json_extract(NEW.result_json,'$.preserved_snapshot_id')=s.id
          AND json_extract(NEW.result_json,'$.content_sha256')=s.content_sha256
          AND json_extract(NEW.result_json,'$.manifest_sha256')=s.manifest_sha256)
    BEGIN SELECT RAISE(ABORT,'Preservation completion lacks its exact snapshot'); END""",
    *(f"""CREATE TRIGGER {table}_{op.lower()} BEFORE {op} ON {table}
        BEGIN SELECT RAISE(ABORT,'Immutable preservation workflow identity'); END"""
      for table in (
          "publication_preservation_request", "publication_preservation_source_plan",
          "publication_preservation_source_result", "publication_preservation_chunk_work",
          "publication_preservation_completion_work",
      ) for op in ("UPDATE", "DELETE")),
)


V019_POSTCONDITION_QUERIES = (
    """SELECT 'preservation workflow tables missing' WHERE 5<>(SELECT count(*)
      FROM sqlite_schema WHERE type='table' AND name IN (
        'publication_preservation_request','publication_preservation_source_plan',
        'publication_preservation_source_result','publication_preservation_chunk_work',
        'publication_preservation_completion_work'))""",
)


__all__ = ["V019_POSTCONDITION_QUERIES", "V019_STATEMENTS"]
