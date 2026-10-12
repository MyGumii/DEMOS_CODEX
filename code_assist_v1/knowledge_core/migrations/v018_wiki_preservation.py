"""Opt-in durable storage for information-preserving Wiki generation.

This additive schema does not register workers or select a publication body.
It deliberately uses a new lineage instead of changing the frozen v14-v17
authoring, synthesis, upload, or replay contracts. Generated and failed model
bodies are purgeable and are removed when their canonical extraction body is
purged; immutable hashes and bounded status metadata may remain.
"""
from .v014_wiki_authoring import _sha, _uuid


_STAGE = "('inventory','review','repair')"


V018_STATEMENTS = (
    f"""CREATE TABLE publication_preservation_run(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
        actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
        extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
        source_sha256 TEXT NOT NULL CHECK({_sha('source_sha256')}),
        source_size_bytes INTEGER NOT NULL CHECK(source_size_bytes>0),
        policy_version TEXT NOT NULL,
        plan_json TEXT NOT NULL CHECK(json_valid(plan_json) AND json_type(plan_json)='object'
            AND length(CAST(plan_json AS BLOB))<=1048576),
        plan_sha256 TEXT NOT NULL CHECK({_sha('plan_sha256')}),
        chunk_count INTEGER NOT NULL CHECK(chunk_count>0),
        reserved_model_attempts INTEGER NOT NULL CHECK(reserved_model_attempts>0),
        status TEXT NOT NULL CHECK(status IN ('pending','ready','failed')),
        failure_code TEXT,
        created_at INTEGER NOT NULL CHECK(created_at>=0),
        completed_at INTEGER,
        row_version INTEGER NOT NULL CHECK(row_version>0),
        CHECK((status='pending' AND failure_code IS NULL AND completed_at IS NULL)
           OR (status='ready' AND failure_code IS NULL AND completed_at IS NOT NULL
             AND completed_at>=created_at)
           OR (status='failed' AND failure_code IS NOT NULL AND completed_at IS NOT NULL
             AND completed_at>=created_at))
    ) STRICT""",
    f"""CREATE TABLE publication_preservation_chunk(
        run_id TEXT NOT NULL REFERENCES publication_preservation_run(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal>0),
        chunk_id TEXT NOT NULL CHECK(length(chunk_id) BETWEEN 3 AND 96),
        source_start INTEGER NOT NULL CHECK(source_start>=0),
        source_end INTEGER NOT NULL CHECK(source_end>source_start),
        source_sha256 TEXT NOT NULL CHECK({_sha('source_sha256')}),
        PRIMARY KEY(run_id,ordinal), UNIQUE(run_id,chunk_id)
    ) STRICT""",
    f"""CREATE TABLE publication_preservation_receipt(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 4),
        stage TEXT NOT NULL CHECK(stage IN {_STAGE}),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        response_size_bytes INTEGER NOT NULL CHECK(response_size_bytes>=0 AND response_size_bytes<=32768),
        model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
        model_size_bytes INTEGER NOT NULL CHECK(model_size_bytes>1 AND model_size_bytes<=4096),
        transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
        transport_size_bytes INTEGER NOT NULL CHECK(transport_size_bytes>1 AND transport_size_bytes<=65536),
        validation_state TEXT NOT NULL CHECK(validation_state='validated'),
        attempt_units INTEGER NOT NULL CHECK(attempt_units>0),
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        PRIMARY KEY(run_id,ordinal,sequence),
        FOREIGN KEY(run_id,ordinal) REFERENCES publication_preservation_chunk(run_id,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_preservation_receipt_body(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        response TEXT NOT NULL,
        model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='object'),
        transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object'),
        PRIMARY KEY(run_id,ordinal,sequence),
        FOREIGN KEY(run_id,ordinal,sequence)
            REFERENCES publication_preservation_receipt(run_id,ordinal,sequence) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE publication_preservation_failure(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        failure_ordinal INTEGER NOT NULL CHECK(failure_ordinal>0),
        stage_sequence INTEGER NOT NULL CHECK(stage_sequence BETWEEN 1 AND 4),
        stage TEXT NOT NULL CHECK(stage IN {_STAGE}),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        failure_code TEXT NOT NULL CHECK(failure_code IN
            ('PRESERVATION_INCOMPLETE','PRESERVATION_PROVIDER_NO_CONTENT',
             'CODEX_EXEC_TIMEOUT','CODEX_OUTPUT_TOO_LARGE','CODEX_EXEC_FAILED',
             'CODEX_OUTPUT_INCOMPLETE','MODEL_EXEC_FAILED','MODEL_RESPONSE_TOO_LARGE',
             'INVALID_OUTPUT','WORK_LIMIT','SOURCE_UNAVAILABLE','AUTHORITY_UNAVAILABLE')),
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        response_size_bytes INTEGER NOT NULL CHECK(response_size_bytes>=0 AND response_size_bytes<=32768),
        model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
        model_size_bytes INTEGER NOT NULL CHECK(model_size_bytes>1 AND model_size_bytes<=4096),
        transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
        transport_size_bytes INTEGER NOT NULL CHECK(transport_size_bytes>1 AND transport_size_bytes<=65536),
        attempt_units INTEGER NOT NULL CHECK(attempt_units>=0),
        failed_at INTEGER NOT NULL CHECK(failed_at>=0),
        PRIMARY KEY(run_id,ordinal,failure_ordinal),
        FOREIGN KEY(run_id,ordinal) REFERENCES publication_preservation_chunk(run_id,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE publication_preservation_failure_body(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        failure_ordinal INTEGER NOT NULL,
        response TEXT NOT NULL,
        model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='object'),
        transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object'),
        PRIMARY KEY(run_id,ordinal,failure_ordinal),
        FOREIGN KEY(run_id,ordinal,failure_ordinal)
            REFERENCES publication_preservation_failure(run_id,ordinal,failure_ordinal) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE preserved_wiki_snapshot(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        run_id TEXT NOT NULL UNIQUE REFERENCES publication_preservation_run(id) ON DELETE RESTRICT,
        content_sha256 TEXT NOT NULL CHECK({_sha('content_sha256')}),
        content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes>0),
        manifest_sha256 TEXT NOT NULL CHECK({_sha('manifest_sha256')}),
        manifest_size_bytes INTEGER NOT NULL CHECK(manifest_size_bytes>0),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE preserved_wiki_snapshot_body(
        snapshot_id TEXT PRIMARY KEY NOT NULL REFERENCES preserved_wiki_snapshot(id) ON DELETE RESTRICT,
        content_md TEXT NOT NULL,
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object')
    ) STRICT""",
    """CREATE INDEX publication_preservation_pending
        ON publication_preservation_run(status,created_at,id)""",
    """CREATE TRIGGER publication_preservation_run_binding BEFORE INSERT ON publication_preservation_run
    WHEN NEW.status<>'pending' OR NEW.row_version<>1 OR NOT EXISTS(
        SELECT 1 FROM publication_preview p
        JOIN app_user u ON u.id=p.actor_user_id AND u.account_status='active'
          AND u.scope_epoch=p.captured_scope_epoch
        JOIN source_artifact a ON a.id=p.artifact_id AND a.publisher_user_id=p.actor_user_id
          AND a.body_retention_status='present'
        JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=a.id
          AND e.status='ready' AND e.body_retention_status='present'
        JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=a.id
          AND b.content_sha256=e.extracted_sha256 AND b.content_size_bytes=e.extracted_size_bytes
          AND b.content_md IS NOT NULL AND b.content_blob_hash IS NULL
        JOIN durable_job j ON j.id=p.durable_job_id AND j.state='succeeded'
        WHERE p.id=NEW.preview_id AND p.id=json_extract(NEW.plan_json,'$.preview_id')
          AND p.actor_user_id=NEW.actor_user_id AND p.artifact_id=NEW.artifact_id
          AND p.extraction_snapshot_id=NEW.extraction_snapshot_id
          AND p.source_snapshot_sha256=NEW.source_sha256
          AND j.id=NEW.parent_job_id AND j.actor_user_id=NEW.actor_user_id
          AND e.extracted_sha256=NEW.source_sha256 AND e.extracted_size_bytes=NEW.source_size_bytes
          AND p.status='ready' AND p.expires_at>NEW.created_at
          AND json_extract(NEW.plan_json,'$.schema_version')=1
          AND json_extract(NEW.plan_json,'$.policy_version')=NEW.policy_version
          AND json_extract(NEW.plan_json,'$.source_snapshot_id')=NEW.extraction_snapshot_id
          AND json_extract(NEW.plan_json,'$.source_sha256')=NEW.source_sha256
          AND json_extract(NEW.plan_json,'$.source_size_bytes')=NEW.source_size_bytes
          AND json_array_length(NEW.plan_json,'$.chunks')=NEW.chunk_count
          AND json_type(NEW.plan_json,'$.reservation.durable_attempts')='integer'
          AND json_extract(NEW.plan_json,'$.reservation.durable_attempts') BETWEEN 1 AND 2
          AND json_extract(NEW.plan_json,'$.reservation.reserved_model_attempts')=NEW.reserved_model_attempts
          AND json_type(NEW.plan_json,'$.execution_policy.timeout_s')='integer')
    BEGIN SELECT RAISE(ABORT,'preservation_run_binding'); END""",
    """CREATE TRIGGER publication_preservation_run_immutable BEFORE UPDATE ON publication_preservation_run
    WHEN NEW.id<>OLD.id OR NEW.preview_id<>OLD.preview_id OR NEW.parent_job_id<>OLD.parent_job_id
      OR NEW.actor_user_id<>OLD.actor_user_id OR NEW.artifact_id<>OLD.artifact_id
      OR NEW.extraction_snapshot_id<>OLD.extraction_snapshot_id OR NEW.source_sha256<>OLD.source_sha256
      OR NEW.source_size_bytes<>OLD.source_size_bytes OR NEW.policy_version<>OLD.policy_version
      OR NEW.plan_json<>OLD.plan_json OR NEW.plan_sha256<>OLD.plan_sha256
      OR NEW.chunk_count<>OLD.chunk_count OR NEW.reserved_model_attempts<>OLD.reserved_model_attempts
      OR NEW.created_at<>OLD.created_at OR OLD.status<>'pending' OR NEW.row_version<>OLD.row_version+1
      OR NEW.status NOT IN ('ready','failed')
    BEGIN SELECT RAISE(ABORT,'preservation_run_immutable'); END""",
    """CREATE TRIGGER publication_preservation_run_terminal BEFORE UPDATE OF status ON publication_preservation_run
    WHEN (NEW.status='ready' AND NOT EXISTS(
        SELECT 1 FROM preserved_wiki_snapshot s JOIN preserved_wiki_snapshot_body b ON b.snapshot_id=s.id
        WHERE s.run_id=OLD.id AND s.created_at<=NEW.completed_at))
      OR (NEW.status='failed' AND (EXISTS(SELECT 1 FROM preserved_wiki_snapshot WHERE run_id=OLD.id)
        OR (NEW.failure_code NOT IN
              ('PRESERVATION_INCOMPLETE','WORK_LIMIT','SOURCE_UNAVAILABLE','AUTHORITY_UNAVAILABLE')
          AND NOT EXISTS(SELECT 1 FROM publication_preservation_failure f
            WHERE f.run_id=OLD.id AND f.failure_code=NEW.failure_code))))
    BEGIN SELECT RAISE(ABORT,'preservation_terminal_incomplete'); END""",
    """CREATE TRIGGER publication_preservation_chunk_binding BEFORE INSERT ON publication_preservation_chunk
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r WHERE r.id=NEW.run_id AND r.status='pending'
      AND NEW.ordinal<=r.chunk_count
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].ordinal')=NEW.ordinal
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].chunk_id')=NEW.chunk_id
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].start')=NEW.source_start
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].end')=NEW.source_end
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].source_sha256')=NEW.source_sha256)
    BEGIN SELECT RAISE(ABORT,'preservation_chunk_binding'); END""",
    """CREATE TRIGGER publication_preservation_receipt_binding BEFORE INSERT ON publication_preservation_receipt
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=e.artifact_id
      WHERE r.id=NEW.run_id AND r.status='pending' AND e.body_retention_status='present'
        AND x.content_sha256=r.source_sha256 AND NEW.completed_at>=r.created_at
        AND NEW.sequence=1+(SELECT count(*) FROM publication_preservation_receipt p
          WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal)
        AND NEW.stage=CASE NEW.sequence WHEN 1 THEN 'inventory' WHEN 2 THEN 'review'
          WHEN 3 THEN 'repair' WHEN 4 THEN 'review' END
        AND NEW.attempt_units+(SELECT coalesce(sum(attempt_units),0) FROM (
          SELECT attempt_units FROM publication_preservation_receipt WHERE run_id=NEW.run_id
          UNION ALL SELECT attempt_units FROM publication_preservation_failure WHERE run_id=NEW.run_id
        ))<=r.reserved_model_attempts)
    BEGIN SELECT RAISE(ABORT,'preservation_receipt_binding'); END""",
    """CREATE TRIGGER publication_preservation_receipt_body_binding BEFORE INSERT ON publication_preservation_receipt_body
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_receipt r
      JOIN publication_preservation_run p ON p.id=r.run_id AND p.status='pending'
      JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.body_retention_status='present'
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=p.artifact_id
      WHERE r.run_id=NEW.run_id AND r.ordinal=NEW.ordinal AND r.sequence=NEW.sequence
        AND r.response_size_bytes=length(CAST(NEW.response AS BLOB))
        AND r.model_size_bytes=length(CAST(NEW.model_json AS BLOB))
        AND r.transport_size_bytes=length(CAST(NEW.transport_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'preservation_receipt_body_binding'); END""",
    """CREATE TRIGGER publication_preservation_failure_binding BEFORE INSERT ON publication_preservation_failure
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=e.artifact_id
      WHERE r.id=NEW.run_id AND r.status='pending' AND e.body_retention_status='present'
        AND x.content_sha256=r.source_sha256 AND NEW.failed_at>=r.created_at
        AND NEW.failure_ordinal=1+(SELECT count(*) FROM publication_preservation_failure f
          WHERE f.run_id=NEW.run_id AND f.ordinal=NEW.ordinal)
        AND NEW.stage_sequence=CASE
          WHEN 4=(SELECT count(*) FROM publication_preservation_receipt p
            WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal) THEN 4
          ELSE 1+(SELECT count(*) FROM publication_preservation_receipt p
            WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal) END
        AND NEW.stage=CASE NEW.stage_sequence WHEN 1 THEN 'inventory' WHEN 2 THEN 'review'
          WHEN 3 THEN 'repair' WHEN 4 THEN 'review' END
        AND (4<>(SELECT count(*) FROM publication_preservation_receipt p
              WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal)
          OR (NEW.failure_code='PRESERVATION_INCOMPLETE' AND NEW.attempt_units=0
            AND NEW.request_sha256=(SELECT request_sha256 FROM publication_preservation_receipt p
              WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal AND p.sequence=4)))
        AND NEW.attempt_units+(SELECT coalesce(sum(attempt_units),0) FROM (
          SELECT attempt_units FROM publication_preservation_receipt WHERE run_id=NEW.run_id
          UNION ALL SELECT attempt_units FROM publication_preservation_failure WHERE run_id=NEW.run_id
        ))<=r.reserved_model_attempts)
    BEGIN SELECT RAISE(ABORT,'preservation_failure_binding'); END""",
    """CREATE TRIGGER publication_preservation_failure_body_binding BEFORE INSERT ON publication_preservation_failure_body
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_failure f
      JOIN publication_preservation_run p ON p.id=f.run_id AND p.status='pending'
      JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.body_retention_status='present'
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=p.artifact_id
      WHERE f.run_id=NEW.run_id AND f.ordinal=NEW.ordinal AND f.failure_ordinal=NEW.failure_ordinal
        AND f.response_size_bytes=length(CAST(NEW.response AS BLOB))
        AND f.model_size_bytes=length(CAST(NEW.model_json AS BLOB))
        AND f.transport_size_bytes=length(CAST(NEW.transport_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'preservation_failure_body_binding'); END""",
    """CREATE TRIGGER preserved_wiki_snapshot_binding BEFORE INSERT ON preserved_wiki_snapshot
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r WHERE r.id=NEW.run_id
      AND r.status='pending' AND NEW.created_at>=r.created_at
      AND r.chunk_count=(SELECT count(*) FROM publication_preservation_chunk c
        WHERE c.run_id=r.id AND (SELECT count(*) FROM publication_preservation_receipt q
          JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
            AND b.ordinal=q.ordinal AND b.sequence=q.sequence
          WHERE q.run_id=c.run_id AND q.ordinal=c.ordinal) IN (2,4)))
    BEGIN SELECT RAISE(ABORT,'preserved_snapshot_incomplete'); END""",
    """CREATE TRIGGER preserved_wiki_snapshot_body_binding BEFORE INSERT ON preserved_wiki_snapshot_body
    WHEN NOT EXISTS(SELECT 1 FROM preserved_wiki_snapshot s
      JOIN publication_preservation_run r ON r.id=s.run_id AND r.status='pending'
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.body_retention_status='present'
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=r.artifact_id
      WHERE s.id=NEW.snapshot_id AND instr(NEW.content_md,char(13))=0
        AND s.content_size_bytes=length(CAST(NEW.content_md AS BLOB))
        AND s.manifest_size_bytes=length(CAST(NEW.manifest_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'preserved_snapshot_body_binding'); END""",
    *(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'Immutable preservation record'); END"""
      for table in (
          "publication_preservation_chunk", "publication_preservation_receipt",
          "publication_preservation_failure", "preserved_wiki_snapshot",
      )),
    *(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'Immutable retained preservation body'); END"""
      for table in (
          "publication_preservation_receipt_body", "publication_preservation_failure_body",
          "preserved_wiki_snapshot_body",
      )),
    *(f"""CREATE TRIGGER {table}_delete_guard BEFORE DELETE ON {table}
        WHEN EXISTS(SELECT 1 FROM publication_preservation_run r
          JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.body_retention_status='present'
          JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=r.artifact_id
          WHERE r.id={run_expression})
        BEGIN SELECT RAISE(ABORT,'Preservation body is still retained with its source'); END"""
      for table, run_expression in (
          ("publication_preservation_receipt_body", "OLD.run_id"),
          ("publication_preservation_failure_body", "OLD.run_id"),
          ("preserved_wiki_snapshot_body", "(SELECT run_id FROM preserved_wiki_snapshot WHERE id=OLD.snapshot_id)"),
      )),
    """CREATE TRIGGER preservation_purge_after_source_body AFTER DELETE ON extraction_snapshot_body
    BEGIN
      DELETE FROM publication_preservation_receipt_body WHERE run_id IN
        (SELECT id FROM publication_preservation_run WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id);
      DELETE FROM publication_preservation_failure_body WHERE run_id IN
        (SELECT id FROM publication_preservation_run WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id);
      DELETE FROM preserved_wiki_snapshot_body WHERE snapshot_id IN
        (SELECT s.id FROM preserved_wiki_snapshot s JOIN publication_preservation_run r ON r.id=s.run_id
          WHERE r.extraction_snapshot_id=OLD.snapshot_id AND r.artifact_id=OLD.artifact_id);
    END""",
    """CREATE TRIGGER preservation_purge_after_source_status AFTER UPDATE OF body_retention_status ON extraction_snapshot
    WHEN NEW.body_retention_status<>'present'
    BEGIN
      DELETE FROM publication_preservation_receipt_body WHERE run_id IN
        (SELECT id FROM publication_preservation_run WHERE extraction_snapshot_id=NEW.id AND artifact_id=NEW.artifact_id);
      DELETE FROM publication_preservation_failure_body WHERE run_id IN
        (SELECT id FROM publication_preservation_run WHERE extraction_snapshot_id=NEW.id AND artifact_id=NEW.artifact_id);
      DELETE FROM preserved_wiki_snapshot_body WHERE snapshot_id IN
        (SELECT s.id FROM preserved_wiki_snapshot s JOIN publication_preservation_run r ON r.id=s.run_id
          WHERE r.extraction_snapshot_id=NEW.id AND r.artifact_id=NEW.artifact_id);
    END""",
)


V018_POSTCONDITION_QUERIES = (
    """SELECT 'preservation_tables_missing' WHERE 8<>(SELECT count(*) FROM sqlite_schema
      WHERE type='table' AND name IN (
        'publication_preservation_run','publication_preservation_chunk',
        'publication_preservation_receipt','publication_preservation_receipt_body',
        'publication_preservation_failure','publication_preservation_failure_body',
        'preserved_wiki_snapshot','preserved_wiki_snapshot_body'))""",
)


__all__ = ["V018_POSTCONDITION_QUERIES", "V018_STATEMENTS"]
