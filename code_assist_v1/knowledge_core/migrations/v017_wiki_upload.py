"""Optional upload-entry request, source planner and exclusive disposition."""
from .v014_wiki_authoring import _sha


V017_STATEMENTS = (
    f"""CREATE TABLE publication_wiki_request(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preview(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        version TEXT NOT NULL,
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'
            AND length(CAST(policy_json AS BLOB))<=16384),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE publication_wiki_source_plan(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_wiki_request(preview_id) ON DELETE RESTRICT,
        durable_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_wiki_source_result(
        preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_wiki_request(preview_id) ON DELETE RESTRICT,
        outcome TEXT NOT NULL CHECK(outcome IN ('planned','extraction_only')),
        run_id TEXT UNIQUE REFERENCES publication_authoring_run(id) ON DELETE RESTRICT,
        reason TEXT,
        parent_state TEXT NOT NULL,
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        CHECK((outcome='planned' AND run_id IS NOT NULL AND reason IS NULL AND parent_state='succeeded')
           OR (outcome='extraction_only' AND run_id IS NULL AND reason IS NOT NULL AND reason IN
              ('source_or_planner_unavailable','source_authority_unavailable','planner_terminal')))
    ) STRICT""",
    """CREATE TRIGGER wiki_upload_request_parent BEFORE INSERT ON publication_wiki_request BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_preview p JOIN durable_job j ON j.id=p.durable_job_id
            WHERE p.id=NEW.preview_id AND p.status='queued' AND p.created_at<=NEW.created_at
            AND j.id=NEW.parent_job_id AND j.state='queued' AND j.cancel_requested=0
            AND j.job_type='publication_preview.extract' AND j.handler_version='v1'
            AND j.actor_kind='user' AND j.actor_user_id=p.actor_user_id
            AND json_extract(j.input_ref_json,'$.preview_id')=p.id
            AND json_extract(j.input_ref_json,'$.artifact_id')=p.artifact_id)
            THEN RAISE(ABORT,'Wiki request requires its new queued extraction') END;
    END""",
    """CREATE TRIGGER wiki_upload_planner_binding BEFORE INSERT ON publication_wiki_source_plan BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_wiki_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN durable_job e ON e.id=r.parent_job_id
            JOIN durable_job j ON j.id=NEW.durable_job_id
            WHERE r.preview_id=NEW.preview_id AND p.status='ready' AND e.state='succeeded'
            AND j.state='queued' AND j.cancel_requested=0 AND j.actor_kind='user'
            AND j.actor_user_id=p.actor_user_id AND j.space_id=e.space_id
            AND j.job_type='publication_wiki.plan_upload' AND j.handler_version='v1' AND j.max_attempts=2
            AND (SELECT count(*) FROM json_each(j.input_ref_json))=1
            AND json_extract(j.input_ref_json,'$.preview_id')=r.preview_id
            AND json_extract(j.expected_state_json,'$.policy_sha256')=r.policy_sha256
            AND json_extract(j.expected_state_json,'$.parent_job_id')=r.parent_job_id)
            OR EXISTS(SELECT 1 FROM publication_wiki_source_result WHERE preview_id=NEW.preview_id)
            THEN RAISE(ABORT,'Invalid initial Wiki planner binding') END;
    END""",
    """CREATE TRIGGER wiki_upload_run_gate BEFORE INSERT ON publication_authoring_run
        WHEN EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=NEW.preview_id) BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_wiki_request r
            JOIN publication_wiki_source_plan q ON q.preview_id=r.preview_id JOIN durable_job j ON j.id=q.durable_job_id
            WHERE r.preview_id=NEW.preview_id AND j.state='running' AND j.cancel_requested=0
            AND j.actor_user_id=NEW.actor_user_id
            AND json_extract(r.policy_json,'$.chunk_bytes')=json_extract(NEW.plan_json,'$.chunk_bytes')
            AND json_extract(r.policy_json,'$.model_policy')=json_extract(NEW.plan_json,'$.model_policy')
            AND json_extract(r.policy_json,'$.work_policy')=json_extract(NEW.plan_json,'$.work_policy')
            AND json_extract(r.policy_json,'$.authoring_version')=NEW.policy_version)
            OR EXISTS(SELECT 1 FROM publication_wiki_source_result WHERE preview_id=NEW.preview_id)
            THEN RAISE(ABORT,'Upload run requires its frozen running planner') END;
    END""",
    """CREATE TRIGGER wiki_upload_source_outcome BEFORE INSERT ON publication_wiki_source_result BEGIN
        SELECT CASE WHEN NEW.completed_at<(SELECT created_at FROM publication_wiki_request WHERE preview_id=NEW.preview_id)
            OR NEW.parent_state<>(SELECT j.state FROM publication_wiki_request r JOIN durable_job j ON j.id=r.parent_job_id
                WHERE r.preview_id=NEW.preview_id)
            OR (NEW.outcome='extraction_only' AND EXISTS(SELECT 1 FROM publication_authoring_run WHERE preview_id=NEW.preview_id))
            OR (NEW.outcome='planned' AND NOT EXISTS(SELECT 1 FROM publication_authoring_run a
                JOIN publication_wiki_request r ON r.preview_id=a.preview_id
                JOIN publication_wiki_source_plan q ON q.preview_id=r.preview_id
                JOIN durable_job j ON j.id=q.durable_job_id
                JOIN publication_synthesis_intent i ON i.run_id=a.id
                WHERE a.id=NEW.run_id AND a.preview_id=NEW.preview_id AND a.status='pending'
                AND j.state='running' AND j.cancel_requested=0
                AND i.parent_job_id=r.parent_job_id
                AND i.version=json_extract(r.policy_json,'$.synthesis_version')
                AND i.policy_json=json_extract(r.policy_json,'$.synthesis_policy')
                AND i.map_durable_attempts=json_extract(r.policy_json,'$.map_durable_attempts')
                AND i.reduction_durable_attempts=json_extract(r.policy_json,'$.reduction_durable_attempts')
                AND i.planner_durable_attempts=json_extract(r.policy_json,'$.synthesis_planner_durable_attempts')
                AND (SELECT count(*) FROM publication_authoring_chunk c JOIN durable_job m ON m.id=c.durable_job_id
                    WHERE c.run_id=a.id AND m.state='queued' AND m.cancel_requested=0
                    AND m.max_attempts=json_extract(r.policy_json,'$.map_durable_attempts'))=a.chunk_count))
            THEN RAISE(ABORT,'Invalid initial Wiki source outcome') END;
    END""",
    """CREATE TRIGGER wiki_upload_planner_success BEFORE UPDATE OF state ON durable_job
        WHEN NEW.job_type='publication_wiki.plan_upload' AND NEW.handler_version='v1' AND NEW.state='succeeded' BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_wiki_source_plan q
            JOIN publication_wiki_request r ON r.preview_id=q.preview_id
            JOIN publication_wiki_source_result x ON x.preview_id=r.preview_id AND x.outcome='planned'
            JOIN publication_authoring_run a ON a.id=x.run_id
            WHERE q.durable_job_id=NEW.id AND (SELECT count(*) FROM json_each(NEW.result_json))=7
            AND json_type(NEW.result_json,'$.schema_version')='integer' AND json_extract(NEW.result_json,'$.schema_version')=1
            AND json_type(NEW.result_json,'$.chunk_count')='integer'
            AND json_extract(NEW.result_json,'$.preview_id')=r.preview_id
            AND json_extract(NEW.result_json,'$.parent_job_id')=r.parent_job_id
            AND json_extract(NEW.result_json,'$.policy_sha256')=r.policy_sha256
            AND json_extract(NEW.result_json,'$.run_id')=a.id
            AND json_extract(NEW.result_json,'$.plan_sha256')=a.plan_sha256
            AND json_extract(NEW.result_json,'$.chunk_count')=a.chunk_count)
            THEN RAISE(ABORT,'Initial planner success requires its exact committed effect') END;
    END""",
    *(f"""CREATE TRIGGER {table}_{op.lower()} BEFORE {op} ON {table}
        BEGIN SELECT RAISE(ABORT,'Immutable Wiki upload identity'); END"""
      for table in ("publication_wiki_request", "publication_wiki_source_plan", "publication_wiki_source_result")
      for op in ("UPDATE", "DELETE")),
)

V017_POSTCONDITION_QUERIES = (
    "SELECT 'missing Wiki upload tables' WHERE (SELECT count(*) FROM sqlite_master WHERE type='table' "
    "AND name IN ('publication_wiki_request','publication_wiki_source_plan','publication_wiki_source_result'))<>3",
)
