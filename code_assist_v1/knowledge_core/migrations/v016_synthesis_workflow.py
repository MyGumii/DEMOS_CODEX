"""Optional durable orchestration intent; no deployed catalog is changed."""
from .v014_wiki_authoring import _sha


V016_STATEMENTS = (
    f"""CREATE TABLE publication_synthesis_intent(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_authoring_run(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
        version TEXT NOT NULL,
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'
            AND length(CAST(policy_json AS BLOB))<=4096),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        source_fence TEXT NOT NULL CHECK(json_valid(source_fence) AND length(CAST(source_fence AS BLOB))<=32768),
        map_durable_attempts INTEGER NOT NULL CHECK(map_durable_attempts>0),
        reduction_durable_attempts INTEGER NOT NULL CHECK(reduction_durable_attempts>0),
        planner_durable_attempts INTEGER NOT NULL CHECK(planner_durable_attempts>0),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE publication_synthesis_planner(
        run_id TEXT NOT NULL REFERENCES publication_synthesis_intent(run_id) ON DELETE RESTRICT,
        after_round INTEGER NOT NULL CHECK(after_round>=0),
        durable_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        PRIMARY KEY(run_id,after_round)
    ) STRICT""",
    """CREATE TRIGGER synthesis_intent_parent BEFORE INSERT ON publication_synthesis_intent BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r
            JOIN publication_preview p ON p.id=r.preview_id JOIN durable_job j ON j.id=p.durable_job_id
            WHERE r.id=NEW.run_id AND r.status='pending' AND r.created_at<=NEW.created_at
            AND j.id=NEW.parent_job_id AND j.state='succeeded' AND j.job_type='publication_preview.extract'
            AND j.handler_version='v1' AND j.actor_kind='user' AND j.actor_user_id=r.actor_user_id)
            OR EXISTS(SELECT 1 FROM publication_authoring_chunk_body WHERE run_id=NEW.run_id)
            THEN RAISE(ABORT,'Invalid synthesis workflow intent') END;
    END""",
    """CREATE TRIGGER synthesis_planner_binding BEFORE INSERT ON publication_synthesis_planner BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_intent i
            JOIN publication_authoring_run r ON r.id=i.run_id JOIN durable_job j ON j.id=NEW.durable_job_id
            WHERE i.run_id=NEW.run_id AND r.status='pending' AND j.actor_kind='user'
            AND j.actor_user_id=r.actor_user_id AND j.job_type='publication_wiki.plan_synthesis'
            AND j.handler_version='v1' AND j.state='queued' AND j.cancel_requested=0
            AND j.max_attempts=i.planner_durable_attempts
            AND (SELECT count(*) FROM json_each(j.input_ref_json))=2
            AND json_extract(j.input_ref_json,'$.run_id')=NEW.run_id
            AND json_type(j.input_ref_json,'$.after_round')='integer'
            AND json_extract(j.input_ref_json,'$.after_round')=NEW.after_round
            AND json_extract(j.expected_state_json,'$.policy_sha256')=i.policy_sha256
            AND json_extract(j.expected_state_json,'$.parent_job_id')=i.parent_job_id)
            THEN RAISE(ABORT,'Invalid synthesis planner binding') END;
    END""",
    """CREATE TRIGGER synthesis_intent_ready BEFORE UPDATE OF status ON publication_authoring_run
        WHEN NEW.status='ready' AND EXISTS(SELECT 1 FROM publication_synthesis_intent WHERE run_id=NEW.id)
        AND NOT EXISTS(SELECT 1 FROM publication_synthesis_completion WHERE run_id=NEW.id)
        BEGIN SELECT RAISE(ABORT,'Workflow requires complete synthesis'); END""",
    """CREATE TRIGGER synthesis_intent_config BEFORE INSERT ON publication_synthesis_config
        WHEN EXISTS(SELECT 1 FROM publication_synthesis_intent WHERE run_id=NEW.run_id)
        BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_intent i
            WHERE i.run_id=NEW.run_id AND i.version=NEW.version AND i.policy_json=NEW.policy_json
            AND i.policy_sha256=NEW.policy_sha256 AND NEW.map_durable_attempts=i.map_durable_attempts
            AND NEW.reduction_durable_attempts=i.reduction_durable_attempts)
            THEN RAISE(ABORT,'Synthesis intent configuration changed') END; END""",
    """CREATE TRIGGER synthesis_planner_success BEFORE UPDATE OF state ON durable_job
        WHEN NEW.job_type='publication_wiki.plan_synthesis' AND NEW.handler_version='v1' AND NEW.state='succeeded'
        BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_planner p
          WHERE p.durable_job_id=NEW.id AND json_type(NEW.result_json,'$.schema_version')='integer'
          AND json_extract(NEW.result_json,'$.schema_version')=1
          AND json_extract(NEW.result_json,'$.run_id')=p.run_id
          AND json_type(NEW.result_json,'$.after_round')='integer'
          AND json_extract(NEW.result_json,'$.after_round')=p.after_round
          AND ((json_extract(NEW.result_json,'$.outcome')='wave'
            AND (SELECT count(*) FROM json_each(NEW.result_json))=8
            AND json_type(NEW.result_json,'$.round_number')='integer'
            AND json_type(NEW.result_json,'$.batch_count')='integer'
            AND EXISTS(SELECT 1 FROM publication_synthesis_wave w WHERE w.run_id=p.run_id
              AND w.round_number=p.after_round+1 AND json_extract(NEW.result_json,'$.round_number')=w.round_number
              AND json_extract(NEW.result_json,'$.wave_id')=w.wave_id
              AND json_extract(NEW.result_json,'$.plan_sha256')=w.plan_sha256
              AND json_extract(NEW.result_json,'$.batch_count')=w.batch_count
              AND (SELECT count(*) FROM publication_synthesis_batch b WHERE b.run_id=w.run_id
                AND b.round_number=w.round_number AND b.durable_job_id IS NOT NULL)=w.batch_count))
          OR (json_extract(NEW.result_json,'$.outcome')='ready'
            AND (SELECT count(*) FROM json_each(NEW.result_json))=12
            AND json_type(NEW.result_json,'$.last_round')='integer'
            AND json_type(NEW.result_json,'$.content_bytes')='integer'
            AND json_type(NEW.result_json,'$.manifest_bytes')='integer'
            AND EXISTS(SELECT 1 FROM publication_synthesis_completion c
              JOIN authored_wiki_snapshot s ON s.id=c.snapshot_id
              JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
              JOIN publication_authoring_run r ON r.id=c.run_id AND r.status='ready'
              WHERE c.run_id=p.run_id AND c.last_round=p.after_round
              AND json_extract(NEW.result_json,'$.snapshot_id')=c.snapshot_id
              AND json_extract(NEW.result_json,'$.content_sha256')=s.content_sha256
              AND json_extract(NEW.result_json,'$.content_bytes')=s.content_size_bytes
              AND json_extract(NEW.result_json,'$.manifest_sha256')=s.manifest_sha256
              AND json_extract(NEW.result_json,'$.manifest_bytes')=s.manifest_size_bytes
              AND json_extract(NEW.result_json,'$.mode')=c.mode
              AND json_extract(NEW.result_json,'$.last_round')=c.last_round
              AND json_extract(NEW.result_json,'$.final_wave_id')=c.final_wave_id))))
        THEN RAISE(ABORT,'Planner success requires its exact committed effect') END; END""",
    *(f"""CREATE TRIGGER {table}_{op.lower()} BEFORE {op} ON {table}
        BEGIN SELECT RAISE(ABORT,'Immutable synthesis workflow identity'); END"""
      for table in ("publication_synthesis_intent", "publication_synthesis_planner")
      for op in ("UPDATE", "DELETE")),
)

V016_POSTCONDITION_QUERIES = (
    "SELECT 'missing synthesis workflow tables' WHERE (SELECT count(*) FROM sqlite_master WHERE type='table' "
    "AND name IN ('publication_synthesis_intent','publication_synthesis_planner'))<>2",
)
