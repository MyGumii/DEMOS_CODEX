"""Additive isolated synthesis ledger. Does not activate the runtime catalog."""
from .v014_wiki_authoring import _sha


def _tag(column, prefix, digits):
    return (f"length({column})={digits + 1} AND substr({column},1,1)='{prefix}' "
            f"AND substr({column},2) NOT GLOB '*[^0-9a-f]*'")


V015_STATEMENTS = (
    f"""CREATE TABLE publication_synthesis_config(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_authoring_run(id) ON DELETE RESTRICT,
        version TEXT NOT NULL,
        policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'
          AND length(CAST(policy_json AS BLOB))<=4096),
        policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
        map_durable_attempts INTEGER NOT NULL CHECK(map_durable_attempts>0),
        reduction_durable_attempts INTEGER NOT NULL CHECK(reduction_durable_attempts>0),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE publication_synthesis_wave(
        run_id TEXT NOT NULL REFERENCES publication_synthesis_config(run_id) ON DELETE RESTRICT,
        round_number INTEGER NOT NULL CHECK(round_number>0),
        wave_id TEXT NOT NULL CHECK({_tag('wave_id', 'W', 64)}),
        plan_json TEXT NOT NULL CHECK(json_valid(plan_json) AND json_type(plan_json)='object'
          AND length(CAST(plan_json AS BLOB))<=262144),
        plan_sha256 TEXT NOT NULL CHECK({_sha('plan_sha256')}),
        batch_count INTEGER NOT NULL CHECK(batch_count>0),
        created_at INTEGER NOT NULL CHECK(created_at>=0),
        PRIMARY KEY(run_id,round_number), UNIQUE(run_id,wave_id)
    ) STRICT""",
    f"""CREATE TABLE publication_synthesis_batch(
        run_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal>0),
        batch_id TEXT NOT NULL CHECK({_tag('batch_id', 'B', 24)}),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        durable_job_id TEXT UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        PRIMARY KEY(run_id,round_number,ordinal), UNIQUE(run_id,batch_id),
        FOREIGN KEY(run_id,round_number) REFERENCES publication_synthesis_wave(run_id,round_number) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE publication_synthesis_receipt(
        run_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        response_bytes INTEGER NOT NULL CHECK(response_bytes>0 AND response_bytes<=65536),
        model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
        model_bytes INTEGER NOT NULL CHECK(model_bytes>0 AND model_bytes<=2048),
        transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
        transport_bytes INTEGER NOT NULL CHECK(transport_bytes>0 AND transport_bytes<=65536),
        retention_status TEXT NOT NULL CHECK(retention_status IN ('present','purged')),
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        PRIMARY KEY(run_id,round_number,ordinal),
        FOREIGN KEY(run_id,round_number,ordinal)
          REFERENCES publication_synthesis_batch(run_id,round_number,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_synthesis_batch_body(
        run_id TEXT NOT NULL,
        round_number INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        response TEXT NOT NULL CHECK(length(CAST(response AS BLOB))<=65536),
        model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='array'
          AND length(CAST(model_json AS BLOB))<=2048),
        transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object'
          AND length(CAST(transport_json AS BLOB))<=65536),
        PRIMARY KEY(run_id,round_number,ordinal),
        FOREIGN KEY(run_id,round_number,ordinal)
          REFERENCES publication_synthesis_receipt(run_id,round_number,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE publication_synthesis_completion(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_synthesis_config(run_id) ON DELETE RESTRICT,
        snapshot_id TEXT NOT NULL UNIQUE REFERENCES authored_wiki_snapshot(id) ON DELETE RESTRICT,
        mode TEXT NOT NULL CHECK(mode IN ('single_map_no_reduction','reduced_complete_wave')),
        last_round INTEGER NOT NULL CHECK(last_round>=0),
        final_wave_id TEXT NOT NULL CHECK({_tag('final_wave_id', 'W', 64)}),
        CHECK((mode='single_map_no_reduction' AND last_round=0)
          OR (mode='reduced_complete_wave' AND last_round>0))
    ) STRICT""",
    """CREATE TRIGGER synthesis_config_binding BEFORE INSERT ON publication_synthesis_config
    WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r WHERE r.id=NEW.run_id AND r.status='pending'
      AND NEW.created_at>=r.created_at
      AND r.chunk_count=(SELECT count(*) FROM publication_authoring_chunk_body b WHERE b.run_id=r.id)
      AND NOT EXISTS(SELECT 1 FROM authored_wiki_snapshot s WHERE s.run_id=r.id))
    BEGIN SELECT RAISE(ABORT,'synthesis_config_binding'); END""",
    """CREATE TRIGGER synthesis_wave_binding BEFORE INSERT ON publication_synthesis_wave
    WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_config c
      JOIN publication_authoring_run r ON r.id=c.run_id AND r.status='pending'
      WHERE c.run_id=NEW.run_id AND NEW.created_at>=c.created_at
        AND NEW.round_number<=json_extract(c.policy_json,'$.max_rounds')
        AND json_extract(NEW.plan_json,'$.round_number') IS NEW.round_number
        AND json_extract(NEW.plan_json,'$.wave_id') IS NEW.wave_id
        AND json_array_length(NEW.plan_json,'$.batches') IS NEW.batch_count
        AND (NEW.round_number=1 OR EXISTS(SELECT 1 FROM publication_synthesis_wave p
          WHERE p.run_id=NEW.run_id AND p.round_number=NEW.round_number-1
            AND p.batch_count>1 AND p.wave_id IS json_extract(NEW.plan_json,'$.predecessor_id')
            AND p.batch_count=(SELECT count(*) FROM publication_synthesis_batch_body b
              WHERE b.run_id=p.run_id AND b.round_number=p.round_number))))
    BEGIN SELECT RAISE(ABORT,'synthesis_wave_binding'); END""",
    """CREATE TRIGGER synthesis_batch_binding BEFORE INSERT ON publication_synthesis_batch
    WHEN NEW.durable_job_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM publication_synthesis_wave w
      JOIN publication_authoring_run r ON r.id=w.run_id AND r.status='pending'
      WHERE w.run_id=NEW.run_id AND w.round_number=NEW.round_number
        AND NEW.ordinal<=w.batch_count
        AND json_extract(w.plan_json,'$.batches['||(NEW.ordinal-1)||'].batch_id') IS NEW.batch_id
        AND json_extract(w.plan_json,'$.batches['||(NEW.ordinal-1)||'].request_sha256') IS NEW.request_sha256)
    BEGIN SELECT RAISE(ABORT,'synthesis_batch_binding'); END""",
    """CREATE TRIGGER synthesis_batch_job_binding BEFORE UPDATE ON publication_synthesis_batch
    WHEN NEW.run_id<>OLD.run_id OR NEW.round_number<>OLD.round_number OR NEW.ordinal<>OLD.ordinal
      OR NEW.batch_id<>OLD.batch_id OR NEW.request_sha256<>OLD.request_sha256
      OR OLD.durable_job_id IS NOT NULL OR NEW.durable_job_id IS NULL
      OR EXISTS(SELECT 1 FROM publication_synthesis_receipt b WHERE b.run_id=OLD.run_id
        AND b.round_number=OLD.round_number AND b.ordinal=OLD.ordinal)
      OR NOT EXISTS(SELECT 1 FROM publication_authoring_run r JOIN durable_job j
        ON j.id=NEW.durable_job_id AND j.actor_user_id=r.actor_user_id
          AND j.job_type='publication_wiki.reduce_batch' AND j.handler_version='v1'
        WHERE r.id=OLD.run_id AND r.status='pending'
          AND json_extract(j.input_ref_json,'$.run_id') IS OLD.run_id
          AND json_extract(j.input_ref_json,'$.round_number') IS OLD.round_number
          AND json_extract(j.input_ref_json,'$.ordinal') IS OLD.ordinal
          AND json_extract(j.input_ref_json,'$.batch_id') IS OLD.batch_id
          AND json_extract(j.input_ref_json,'$.request_sha256') IS OLD.request_sha256)
    BEGIN SELECT RAISE(ABORT,'synthesis_job_binding'); END""",
    """CREATE TRIGGER synthesis_response_binding BEFORE INSERT ON publication_synthesis_receipt
    WHEN NEW.retention_status<>'present' OR NOT EXISTS(SELECT 1 FROM publication_synthesis_wave w
      JOIN publication_authoring_run r ON r.id=w.run_id AND r.status='pending'
      JOIN publication_synthesis_batch b ON b.run_id=w.run_id AND b.round_number=w.round_number AND b.ordinal=NEW.ordinal
      JOIN durable_job j ON j.id=b.durable_job_id AND j.actor_kind='user' AND j.actor_user_id=r.actor_user_id
        AND j.job_type='publication_wiki.reduce_batch' AND j.handler_version='v1'
        AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>NEW.completed_at
      WHERE w.run_id=NEW.run_id AND w.round_number=NEW.round_number AND NEW.completed_at>=w.created_at
        AND json_extract(j.input_ref_json,'$.run_id') IS b.run_id
        AND json_extract(j.input_ref_json,'$.round_number') IS b.round_number
        AND json_extract(j.input_ref_json,'$.ordinal') IS b.ordinal
        AND json_extract(j.input_ref_json,'$.batch_id') IS b.batch_id
        AND json_extract(j.input_ref_json,'$.request_sha256') IS b.request_sha256)
    BEGIN SELECT RAISE(ABORT,'synthesis_response_binding'); END""",
    """CREATE TRIGGER synthesis_job_success_binding BEFORE UPDATE ON durable_job
    WHEN NEW.state='succeeded' AND NEW.job_type='publication_wiki.reduce_batch'
      AND (NEW.handler_version<>'v1' OR NOT EXISTS(
        SELECT 1 FROM publication_synthesis_batch b JOIN publication_synthesis_receipt r
          ON r.run_id=b.run_id AND r.round_number=b.round_number AND r.ordinal=b.ordinal
        JOIN publication_synthesis_batch_body body ON body.run_id=r.run_id
          AND body.round_number=r.round_number AND body.ordinal=r.ordinal
        WHERE b.durable_job_id=NEW.id AND r.retention_status='present'
          AND (SELECT count(*) FROM json_each(NEW.result_json))=7
          AND json_type(NEW.result_json,'$.schema_version')='integer'
          AND json_type(NEW.result_json,'$.round_number')='integer'
          AND json_type(NEW.result_json,'$.ordinal')='integer'
          AND json_extract(NEW.result_json,'$.schema_version') IS 1
          AND json_extract(NEW.result_json,'$.run_id') IS b.run_id
          AND json_extract(NEW.result_json,'$.round_number') IS b.round_number
          AND json_extract(NEW.result_json,'$.ordinal') IS b.ordinal
          AND json_extract(NEW.result_json,'$.batch_id') IS b.batch_id
          AND json_extract(NEW.result_json,'$.request_sha256') IS b.request_sha256
          AND json_extract(NEW.result_json,'$.response_sha256') IS r.response_sha256))
    BEGIN SELECT RAISE(ABORT,'synthesis_job_result_binding'); END""",
    """CREATE TRIGGER synthesis_body_binding BEFORE INSERT ON publication_synthesis_batch_body
    WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_receipt r WHERE r.run_id=NEW.run_id
      AND r.round_number=NEW.round_number AND r.ordinal=NEW.ordinal AND r.retention_status='present'
      AND r.response_bytes=length(CAST(NEW.response AS BLOB))
      AND r.model_bytes=length(CAST(NEW.model_json AS BLOB))
      AND r.transport_bytes=length(CAST(NEW.transport_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'synthesis_body_binding'); END""",
    """CREATE TRIGGER synthesis_receipt_retention BEFORE UPDATE ON publication_synthesis_receipt
    WHEN NEW.run_id<>OLD.run_id OR NEW.round_number<>OLD.round_number OR NEW.ordinal<>OLD.ordinal
      OR NEW.response_sha256<>OLD.response_sha256 OR NEW.response_bytes<>OLD.response_bytes
      OR NEW.model_sha256<>OLD.model_sha256 OR NEW.model_bytes<>OLD.model_bytes
      OR NEW.transport_sha256<>OLD.transport_sha256 OR NEW.transport_bytes<>OLD.transport_bytes
      OR NEW.completed_at<>OLD.completed_at OR OLD.retention_status<>'present' OR NEW.retention_status<>'purged'
    BEGIN SELECT RAISE(ABORT,'synthesis_history_immutable'); END""",
    """CREATE TRIGGER synthesis_body_retention BEFORE DELETE ON publication_synthesis_batch_body
    WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_receipt r WHERE r.run_id=OLD.run_id
      AND r.round_number=OLD.round_number AND r.ordinal=OLD.ordinal AND r.retention_status='purged')
    BEGIN SELECT RAISE(ABORT,'synthesis_retention_required'); END""",
    """CREATE TRIGGER synthesis_completion_binding BEFORE INSERT ON publication_synthesis_completion
    WHEN NOT EXISTS(SELECT 1 FROM publication_synthesis_config c
      JOIN publication_authoring_run r ON r.id=c.run_id AND r.status='pending'
      JOIN authored_wiki_snapshot s ON s.run_id=r.id AND s.id=NEW.snapshot_id
      JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
      WHERE r.id=NEW.run_id
        AND json_extract(b.manifest_json,'$.synthesis.version') IS c.version
        AND json_extract(b.manifest_json,'$.synthesis.policy') IS json(c.policy_json)
        AND json_extract(b.manifest_json,'$.synthesis.final_wave_id') IS NEW.final_wave_id
        AND coalesce(json_extract(b.manifest_json,'$.synthesis.execution.map_durable_attempts'),1)=c.map_durable_attempts
        AND coalesce(json_extract(b.manifest_json,'$.synthesis.execution.reduction_durable_attempts'),1)=c.reduction_durable_attempts
        AND NEW.last_round=(SELECT count(*) FROM publication_synthesis_wave w WHERE w.run_id=r.id)
        AND json_array_length(b.manifest_json,'$.synthesis.rounds') IS
          (SELECT coalesce(sum(w.batch_count),0) FROM publication_synthesis_wave w WHERE w.run_id=r.id)
        AND NOT EXISTS(SELECT 1 FROM publication_synthesis_wave w WHERE w.run_id=r.id
          AND w.batch_count<>(SELECT count(*) FROM publication_synthesis_receipt x
            JOIN publication_synthesis_batch batch ON batch.run_id=x.run_id
              AND batch.round_number=x.round_number AND batch.ordinal=x.ordinal
            JOIN durable_job j ON j.id=batch.durable_job_id AND j.state='succeeded'
            JOIN publication_synthesis_batch_body xb ON xb.run_id=x.run_id
              AND xb.round_number=x.round_number AND xb.ordinal=x.ordinal
            WHERE x.run_id=w.run_id AND x.round_number=w.round_number AND x.retention_status='present'))
        AND ((NEW.mode='single_map_no_reduction'
          AND json_extract(b.manifest_json,'$.synthesis.model_calls') IS 0)
          OR (NEW.mode='reduced_complete_wave' AND EXISTS(SELECT 1 FROM publication_synthesis_wave w
            WHERE w.run_id=r.id AND w.round_number=NEW.last_round AND w.batch_count=1))))
    BEGIN SELECT RAISE(ABORT,'synthesis_completion_binding'); END""",
    """CREATE TRIGGER synthesis_ready_requires_completion BEFORE UPDATE ON publication_authoring_run
    WHEN NEW.status='ready' AND EXISTS(SELECT 1 FROM publication_synthesis_config c WHERE c.run_id=OLD.id)
      AND NOT EXISTS(SELECT 1 FROM publication_synthesis_completion x WHERE x.run_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'synthesis_completion_required'); END""",
    *tuple(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'synthesis_history_immutable'); END""" for table in (
            "publication_synthesis_config", "publication_synthesis_wave",
            "publication_synthesis_batch_body", "publication_synthesis_completion")),
    *tuple(f"""CREATE TRIGGER {table}_history BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT,'synthesis_history_immutable'); END""" for table in (
            "publication_synthesis_config", "publication_synthesis_wave",
            "publication_synthesis_batch", "publication_synthesis_receipt", "publication_synthesis_completion")),
)

V015_POSTCONDITION_QUERIES = (
    """SELECT 'synthesis_tables_missing' WHERE 6<>(SELECT count(*) FROM sqlite_schema
    WHERE type='table' AND name IN ('publication_synthesis_config','publication_synthesis_wave',
      'publication_synthesis_batch','publication_synthesis_receipt','publication_synthesis_batch_body',
      'publication_synthesis_completion'))""",
)
