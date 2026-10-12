"""Additive native batch storage. No producer, worker or publication activation.

An attempt reserves one complete runner execution (possibly several bounded
provider attempts). Outcomes are immutable and one validated outcome per batch
may feed a snapshot. Private outcomes/output follow extraction-body retention.
Pending runs block purge until a service explicitly reconciles them.
"""
from .v014_wiki_authoring import _sha, _uuid


FAILURE_CODES = ("MODEL_FAILURE", "INVALID_OUTPUT", "TIMEOUT", "WORK_LIMIT",
    "SOURCE_UNAVAILABLE", "AUTHORITY_UNAVAILABLE", "CANCELLED", "LEASE_LOST")
_FAILURES = "(" + ",".join("'" + value + "'" for value in FAILURE_CODES) + ")"
METADATA_TYPES = {
    **dict.fromkeys(("policy_version", "source_preparation_version", "source_slot_version",
        "assembly_version", "table_rendering_version", "prompt_sha256", "preview_id",
        "source_plan_sha256", "source_fence_sha256", "configuration_sha256"), "text"),
    **dict.fromkeys(("schema_version", "configuration_size_bytes", "batch_count",
        "max_executions", "attempt_units", "reserved_model_attempts", "reserved_input_bytes",
        "reserved_response_bytes", "budget_model_attempts", "budget_input_bytes",
        "budget_response_bytes", "budget_markdown_bytes", "budget_manifest_bytes"), "integer"),
}
_META_TYPES = " AND ".join(
    f"json_type(NEW.metadata_json,'$.{key}')='{kind}'" for key, kind in METADATA_TYPES.items())
_META_KEYS = ",".join("'" + key + "'" for key in METADATA_TYPES)


V029_STATEMENTS = (
    f"""CREATE TABLE native_authoring_run(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
        actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
        extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
        source_fence_sha256 TEXT NOT NULL CHECK({_sha('source_fence_sha256')}),
        source_plan_sha256 TEXT NOT NULL CHECK({_sha('source_plan_sha256')}),
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'
            AND length(CAST(metadata_json AS BLOB)) BETWEEN 1 AND 1048576),
        metadata_sha256 TEXT NOT NULL CHECK({_sha('metadata_sha256')}),
        batch_count ANY NOT NULL CHECK(typeof(batch_count)='integer' AND batch_count BETWEEN 0 AND 4096),
        max_executions ANY NOT NULL CHECK(typeof(max_executions)='integer' AND max_executions BETWEEN 1 AND 2),
        attempt_units ANY NOT NULL CHECK(typeof(attempt_units)='integer' AND attempt_units BETWEEN 1 AND 1024),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    """CREATE TABLE native_authoring_run_body(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES native_authoring_run(id) ON DELETE RESTRICT,
        configuration_json TEXT NOT NULL CHECK(json_valid(configuration_json)
            AND json_type(configuration_json)='object'
            AND length(CAST(configuration_json AS BLOB)) BETWEEN 1 AND 65536)
    ) STRICT""",
    f"""CREATE TABLE native_authoring_state(
        run_id TEXT PRIMARY KEY NOT NULL REFERENCES native_authoring_run(id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK(status IN ('pending','ready','failed')),
        failure_code TEXT CHECK(failure_code IN {_FAILURES}),
        completed_at ANY CHECK(completed_at IS NULL OR (typeof(completed_at)='integer' AND completed_at>=0)),
        row_version ANY NOT NULL CHECK(typeof(row_version)='integer' AND row_version>0),
        CHECK((status='pending' AND failure_code IS NULL AND completed_at IS NULL)
            OR (status='ready' AND failure_code IS NULL AND completed_at IS NOT NULL)
            OR (status='failed' AND failure_code IS NOT NULL AND completed_at IS NOT NULL))
    ) STRICT""",
    f"""CREATE TABLE native_authoring_batch(
        run_id TEXT NOT NULL REFERENCES native_authoring_run(id) ON DELETE RESTRICT,
        ordinal ANY NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>0),
        batch_id TEXT NOT NULL CHECK(length(batch_id)=66 AND substr(batch_id,1,2)='B_'
            AND substr(batch_id,3) NOT GLOB '*[^0-9a-f]*'),
        members_json TEXT NOT NULL CHECK(json_valid(members_json) AND json_type(members_json)='array'
            AND json_array_length(members_json) BETWEEN 1 AND 4),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        input_size_bytes ANY NOT NULL CHECK(typeof(input_size_bytes)='integer' AND input_size_bytes BETWEEN 1 AND 65536),
        PRIMARY KEY(run_id,ordinal), UNIQUE(run_id,batch_id)
    ) STRICT""",
    f"""CREATE TABLE native_authoring_attempt(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        run_id TEXT NOT NULL,
        ordinal ANY NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>0),
        sequence ANY NOT NULL CHECK(typeof(sequence)='integer' AND sequence BETWEEN 1 AND 2),
        operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        attempt_units ANY NOT NULL CHECK(typeof(attempt_units)='integer' AND attempt_units BETWEEN 1 AND 1024),
        started_at ANY NOT NULL CHECK(typeof(started_at)='integer' AND started_at>=0),
        FOREIGN KEY(run_id,ordinal) REFERENCES native_authoring_batch(run_id,ordinal) ON DELETE RESTRICT,
        UNIQUE(run_id,ordinal,sequence), UNIQUE(operation_id), UNIQUE(id,run_id,ordinal)
    ) STRICT""",
    f"""CREATE TABLE native_authoring_outcome(
        attempt_id TEXT PRIMARY KEY NOT NULL,
        run_id TEXT NOT NULL,
        ordinal ANY NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>0),
        status TEXT NOT NULL CHECK(status IN ('validated','failed','abandoned')),
        failure_code TEXT CHECK(failure_code IN {_FAILURES}),
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        response_size_bytes ANY NOT NULL CHECK(typeof(response_size_bytes)='integer' AND response_size_bytes BETWEEN 0 AND 65536),
        model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
        model_size_bytes ANY NOT NULL CHECK(typeof(model_size_bytes)='integer' AND model_size_bytes BETWEEN 2 AND 4096),
        transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
        transport_size_bytes ANY NOT NULL CHECK(typeof(transport_size_bytes)='integer' AND transport_size_bytes BETWEEN 2 AND 65536),
        completed_at ANY NOT NULL CHECK(typeof(completed_at)='integer' AND completed_at>=0),
        FOREIGN KEY(attempt_id,run_id,ordinal) REFERENCES native_authoring_attempt(id,run_id,ordinal) ON DELETE RESTRICT,
        CHECK((status='validated' AND failure_code IS NULL AND response_size_bytes>0)
            OR (status IN ('failed','abandoned') AND failure_code IS NOT NULL))
    ) STRICT""",
    "CREATE UNIQUE INDEX idx_native_validated_batch ON native_authoring_outcome(run_id,ordinal) WHERE status='validated'",
    """CREATE TABLE native_authoring_outcome_body(
        attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES native_authoring_outcome(attempt_id) ON DELETE RESTRICT,
        response TEXT NOT NULL CHECK(length(CAST(response AS BLOB))<=65536),
        model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='object'
            AND length(CAST(model_json AS BLOB))<=4096),
        transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object'
            AND length(CAST(transport_json AS BLOB))<=65536)
    ) STRICT""",
    f"""CREATE TABLE native_wiki_snapshot(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        run_id TEXT NOT NULL UNIQUE REFERENCES native_authoring_run(id) ON DELETE RESTRICT,
        content_sha256 TEXT NOT NULL CHECK({_sha('content_sha256')}),
        content_size_bytes ANY NOT NULL CHECK(typeof(content_size_bytes)='integer' AND content_size_bytes BETWEEN 1 AND 4194304),
        manifest_sha256 TEXT NOT NULL CHECK({_sha('manifest_sha256')}),
        manifest_size_bytes ANY NOT NULL CHECK(typeof(manifest_size_bytes)='integer' AND manifest_size_bytes BETWEEN 1 AND 16777216),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    """CREATE TABLE native_wiki_snapshot_body(
        snapshot_id TEXT PRIMARY KEY NOT NULL REFERENCES native_wiki_snapshot(id) ON DELETE RESTRICT,
        content_md TEXT NOT NULL CHECK(length(CAST(content_md AS BLOB)) BETWEEN 1 AND 4194304),
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'
            AND length(CAST(manifest_json AS BLOB)) BETWEEN 1 AND 16777216)
    ) STRICT""",
    f"""CREATE TRIGGER native_run_insert BEFORE INSERT ON native_authoring_run
    WHEN NOT EXISTS(SELECT 1 FROM publication_preview p
        JOIN durable_job j ON j.id=p.durable_job_id
        JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=p.artifact_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=e.id
        JOIN extraction_native_table_structure d ON d.snapshot_id=e.id
        JOIN extraction_native_table_structure_body n ON n.snapshot_id=d.snapshot_id
        JOIN source_artifact a ON a.id=p.artifact_id
        JOIN app_user u ON u.id=p.actor_user_id
        WHERE p.id=NEW.preview_id AND p.status='ready' AND p.expires_at>NEW.created_at
          AND p.expires_at>CAST(unixepoch('subsec')*1000 AS INTEGER)
          AND p.actor_user_id=NEW.actor_user_id AND u.account_status='active'
          AND u.scope_epoch=p.captured_scope_epoch
          AND p.rule_version='deterministic-ingest-native-tables-v1'
          AND p.artifact_id=NEW.artifact_id AND a.body_retention_status='present'
          AND p.extraction_snapshot_id=NEW.extraction_snapshot_id AND e.body_retention_status='present'
          AND e.status='ready' AND e.extractor_version='rich-source-v4-native-tables'
          AND p.source_snapshot_sha256=e.extracted_sha256 AND b.content_sha256=e.extracted_sha256
          AND d.structure_sha256=n.structure_sha256 AND d.extracted_markdown_sha256=e.extracted_sha256
          AND j.id=NEW.parent_job_id AND j.state='succeeded' AND j.actor_user_id=NEW.actor_user_id
          AND j.job_type='publication_preview.extract' AND j.handler_version='v2'
          AND json_type(NEW.metadata_json,'$.schema_version')='integer'
          AND json_extract(NEW.metadata_json,'$.schema_version')=1
          AND json_extract(NEW.metadata_json,'$.policy_version')='wiki-native-batch-authoring-v1'
          AND json_extract(NEW.metadata_json,'$.preview_id')=NEW.preview_id
          AND json_extract(NEW.metadata_json,'$.source_plan_sha256')=NEW.source_plan_sha256
          AND json_extract(NEW.metadata_json,'$.source_fence_sha256')=NEW.source_fence_sha256
          AND json_type(NEW.metadata_json,'$.batch_count')='integer'
          AND json_extract(NEW.metadata_json,'$.batch_count')=NEW.batch_count
          AND json_type(NEW.metadata_json,'$.max_executions')='integer'
          AND json_extract(NEW.metadata_json,'$.max_executions')=NEW.max_executions
          AND json_type(NEW.metadata_json,'$.attempt_units')='integer'
          AND json_extract(NEW.metadata_json,'$.attempt_units')=NEW.attempt_units)
      OR NOT ({_META_TYPES})
      OR (SELECT count(*) FROM json_each(NEW.metadata_json))<>{len(METADATA_TYPES)}
      OR (SELECT count(DISTINCT key) FROM json_each(NEW.metadata_json))<>{len(METADATA_TYPES)}
      OR EXISTS(SELECT 1 FROM json_each(NEW.metadata_json) WHERE key NOT IN ({_META_KEYS}))
      OR json_extract(NEW.metadata_json,'$.source_preparation_version')<>'canonical-native-authoring-source-v1'
      OR json_extract(NEW.metadata_json,'$.source_slot_version')<>'wiki-native-slot-authoring-v1'
      OR json_extract(NEW.metadata_json,'$.assembly_version')<>'wiki-native-slot-assembly-v1'
      OR json_extract(NEW.metadata_json,'$.table_rendering_version')<>'native-source-table-html-v1'
      OR NOT ({_sha("json_extract(NEW.metadata_json,'$.prompt_sha256')")})
      OR NOT ({_sha("json_extract(NEW.metadata_json,'$.configuration_sha256')")})
      OR json_extract(NEW.metadata_json,'$.configuration_size_bytes') NOT BETWEEN 1 AND 65536
      OR json_extract(NEW.metadata_json,'$.reserved_model_attempts')<>NEW.batch_count*NEW.max_executions*NEW.attempt_units
      OR json_extract(NEW.metadata_json,'$.reserved_response_bytes')<>NEW.batch_count*NEW.max_executions*NEW.attempt_units*65536
      OR EXISTS(SELECT 1 FROM json_each(NEW.metadata_json)
          WHERE key LIKE 'budget_%' AND value<1)
      OR json_extract(NEW.metadata_json,'$.reserved_model_attempts')>json_extract(NEW.metadata_json,'$.budget_model_attempts')
      OR json_extract(NEW.metadata_json,'$.reserved_input_bytes') NOT BETWEEN 0 AND json_extract(NEW.metadata_json,'$.budget_input_bytes')
      OR json_extract(NEW.metadata_json,'$.reserved_response_bytes')>json_extract(NEW.metadata_json,'$.budget_response_bytes')
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_SOURCE_INVALID'); END""",
    """CREATE TRIGGER native_run_body_insert BEFORE INSERT ON native_authoring_run_body
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN extraction_snapshot_body b
        ON b.snapshot_id=r.extraction_snapshot_id WHERE r.id=NEW.run_id
          AND json_extract(r.metadata_json,'$.configuration_size_bytes')=length(CAST(NEW.configuration_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_BODY_INVALID'); END""",
    """CREATE TRIGGER native_state_insert BEFORE INSERT ON native_authoring_state
    WHEN NEW.status<>'pending' OR NEW.row_version<>1
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_INITIAL_STATE'); END""",
    """CREATE TRIGGER native_state_update BEFORE UPDATE ON native_authoring_state
    WHEN NEW.run_id<>OLD.run_id OR OLD.status<>'pending' OR NEW.status NOT IN ('ready','failed')
      OR NEW.row_version<>OLD.row_version+1
      OR NEW.completed_at<(SELECT created_at FROM native_authoring_run WHERE id=OLD.run_id)
      OR (NEW.status='ready' AND NOT EXISTS(SELECT 1 FROM native_wiki_snapshot s
          JOIN native_wiki_snapshot_body b ON b.snapshot_id=s.id WHERE s.run_id=OLD.run_id))
      OR (NEW.status='failed' AND EXISTS(SELECT 1 FROM native_wiki_snapshot WHERE run_id=OLD.run_id))
      OR (NEW.status='failed' AND EXISTS(SELECT 1 FROM native_authoring_attempt a
          LEFT JOIN native_authoring_outcome o ON o.attempt_id=a.id
          WHERE a.run_id=OLD.run_id AND o.attempt_id IS NULL))
      OR (NEW.status='failed' AND NEW.failure_code IN ('MODEL_FAILURE','INVALID_OUTPUT','TIMEOUT')
          AND NOT EXISTS(SELECT 1 FROM native_authoring_outcome o
              WHERE o.run_id=OLD.run_id AND o.failure_code=NEW.failure_code AND o.status<>'validated'
                AND NOT EXISTS(SELECT 1 FROM native_authoring_outcome success
                    WHERE success.run_id=o.run_id AND success.ordinal=o.ordinal AND success.status='validated')))
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_TERMINAL_INVALID'); END""",
    """CREATE TRIGGER native_batch_insert BEFORE INSERT ON native_authoring_batch
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
        WHERE r.id=NEW.run_id AND s.status='pending' AND NEW.ordinal<=r.batch_count)
      OR NEW.ordinal<>(SELECT count(*)+1 FROM native_authoring_batch WHERE run_id=NEW.run_id)
      OR EXISTS(SELECT 1 FROM json_each(NEW.members_json) WHERE type<>'text'
          OR length(value)<>66 OR substr(value,1,2)<>'N_' OR substr(value,3) GLOB '*[^0-9a-f]*')
      OR (SELECT count(*) FROM json_each(NEW.members_json))<>(SELECT count(DISTINCT value) FROM json_each(NEW.members_json))
      OR EXISTS(SELECT 1 FROM native_authoring_batch b,json_each(b.members_json) prior,json_each(NEW.members_json) current
          WHERE b.run_id=NEW.run_id AND prior.value=current.value)
    BEGIN SELECT RAISE(ABORT,'NATIVE_BATCH_INVALID'); END""",
    """CREATE TRIGGER native_attempt_insert BEFORE INSERT ON native_authoring_attempt
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
        JOIN native_authoring_batch b ON b.run_id=r.id AND b.ordinal=NEW.ordinal
        JOIN publication_preview p ON p.id=r.preview_id AND p.status='ready'
        WHERE r.id=NEW.run_id AND s.status='pending' AND NEW.sequence<=r.max_executions
          AND r.batch_count=(SELECT count(*) FROM native_authoring_batch WHERE run_id=r.id)
          AND EXISTS(SELECT 1 FROM native_authoring_run_body WHERE run_id=r.id)
          AND json_extract(r.metadata_json,'$.reserved_input_bytes')=r.max_executions*r.attempt_units*
              (SELECT coalesce(sum(input_size_bytes),0) FROM native_authoring_batch WHERE run_id=r.id)
          AND p.expires_at>NEW.started_at AND p.expires_at>CAST(unixepoch('subsec')*1000 AS INTEGER)
          AND NEW.attempt_units=r.attempt_units AND NEW.request_sha256=b.request_sha256
          AND NEW.started_at>=r.created_at)
      OR NEW.sequence<>(SELECT count(*)+1 FROM native_authoring_attempt WHERE run_id=NEW.run_id AND ordinal=NEW.ordinal)
      OR EXISTS(SELECT 1 FROM native_authoring_outcome WHERE run_id=NEW.run_id AND ordinal=NEW.ordinal AND status='validated')
      OR EXISTS(SELECT 1 FROM native_authoring_attempt a LEFT JOIN native_authoring_outcome o ON o.attempt_id=a.id
          WHERE a.run_id=NEW.run_id AND a.ordinal=NEW.ordinal AND o.attempt_id IS NULL)
    BEGIN SELECT RAISE(ABORT,'NATIVE_ATTEMPT_INVALID'); END""",
    """CREATE TRIGGER native_outcome_insert BEFORE INSERT ON native_authoring_outcome
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_attempt a JOIN native_authoring_state s ON s.run_id=a.run_id
        JOIN native_authoring_run r ON r.id=a.run_id JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id
        WHERE a.id=NEW.attempt_id AND a.run_id=NEW.run_id AND a.ordinal=NEW.ordinal AND s.status='pending'
          AND NEW.completed_at>=a.started_at)
    BEGIN SELECT RAISE(ABORT,'NATIVE_OUTCOME_INVALID'); END""",
    """CREATE TRIGGER native_outcome_body_insert BEFORE INSERT ON native_authoring_outcome_body
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_outcome o JOIN native_authoring_run r ON r.id=o.run_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id
        WHERE o.attempt_id=NEW.attempt_id AND o.response_size_bytes=length(CAST(NEW.response AS BLOB))
          AND o.model_size_bytes=length(CAST(NEW.model_json AS BLOB))
          AND o.transport_size_bytes=length(CAST(NEW.transport_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'NATIVE_OUTCOME_BODY_INVALID'); END""",
    """CREATE TRIGGER native_snapshot_insert BEFORE INSERT ON native_wiki_snapshot
    WHEN NOT EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id
        WHERE r.id=NEW.run_id AND s.status='pending' AND NEW.created_at>=r.created_at
          AND NEW.content_size_bytes<=json_extract(r.metadata_json,'$.budget_markdown_bytes')
          AND NEW.manifest_size_bytes<=json_extract(r.metadata_json,'$.budget_manifest_bytes')
          AND r.batch_count=(SELECT count(*) FROM native_authoring_batch WHERE run_id=r.id)
          AND json_extract(r.metadata_json,'$.reserved_input_bytes')=r.max_executions*r.attempt_units*
              (SELECT coalesce(sum(input_size_bytes),0) FROM native_authoring_batch WHERE run_id=r.id)
          AND r.batch_count=(SELECT count(*) FROM native_authoring_outcome o
              JOIN native_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
              WHERE o.run_id=r.id AND o.status='validated'))
    BEGIN SELECT RAISE(ABORT,'NATIVE_SNAPSHOT_INCOMPLETE'); END""",
    """CREATE TRIGGER native_snapshot_body_insert BEFORE INSERT ON native_wiki_snapshot_body
    WHEN NOT EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id
        WHERE s.id=NEW.snapshot_id AND s.content_size_bytes=length(CAST(NEW.content_md AS BLOB))
          AND s.manifest_size_bytes=length(CAST(NEW.manifest_json AS BLOB))
          AND json_extract(NEW.manifest_json,'$.wiki_sha256')=s.content_sha256
          AND json_type(NEW.manifest_json,'$.schema_version')='integer'
          AND json_extract(NEW.manifest_json,'$.schema_version')=1
          AND json_extract(NEW.manifest_json,'$.plan_sha256')=r.source_plan_sha256
          AND json_type(NEW.manifest_json,'$.authoring_receipt_count')='integer'
          AND json_extract(NEW.manifest_json,'$.authoring_receipt_count')=r.batch_count
          AND json_type(NEW.manifest_json,'$.wiki_bytes')='integer'
          AND json_extract(NEW.manifest_json,'$.wiki_bytes')=s.content_size_bytes
          AND json_type(NEW.manifest_json,'$.batch_lineage.receipts')='array'
          AND json_array_length(NEW.manifest_json,'$.batch_lineage.receipts')=r.batch_count
          AND json_type(NEW.manifest_json,'$.batch_lineage.batches')='array'
          AND json_array_length(NEW.manifest_json,'$.batch_lineage.batches')=r.batch_count
          AND json_extract(NEW.manifest_json,'$.batch_lineage.prompt_sha256')=json_extract(r.metadata_json,'$.prompt_sha256')
          AND json_extract(NEW.manifest_json,'$.source.snapshot_id')=r.extraction_snapshot_id
          AND json_extract(NEW.manifest_json,'$.policy_version')='wiki-native-batch-authoring-v1')
    BEGIN SELECT RAISE(ABORT,'NATIVE_SNAPSHOT_BODY_INVALID'); END""",
    """CREATE TRIGGER native_pending_purge_guard BEFORE DELETE ON extraction_snapshot_body
    WHEN EXISTS(SELECT 1 FROM native_authoring_run r JOIN native_authoring_state s ON s.run_id=r.id
        WHERE r.extraction_snapshot_id=OLD.snapshot_id AND s.status='pending')
    BEGIN SELECT RAISE(ABORT,'NATIVE_PENDING_WORK'); END""",
    """CREATE TRIGGER native_outcome_body_delete BEFORE DELETE ON native_authoring_outcome_body
    WHEN EXISTS(SELECT 1 FROM native_authoring_outcome o JOIN native_authoring_run r ON r.id=o.run_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id WHERE o.attempt_id=OLD.attempt_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_BODY_RETENTION'); END""",
    """CREATE TRIGGER native_run_body_delete BEFORE DELETE ON native_authoring_run_body
    WHEN EXISTS(SELECT 1 FROM native_authoring_run r JOIN extraction_snapshot_body b
        ON b.snapshot_id=r.extraction_snapshot_id WHERE r.id=OLD.run_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_BODY_RETENTION'); END""",
    """CREATE TRIGGER native_snapshot_body_delete BEFORE DELETE ON native_wiki_snapshot_body
    WHEN EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id WHERE s.id=OLD.snapshot_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_BODY_RETENTION'); END""",
    """CREATE TRIGGER native_authoring_body_purge AFTER DELETE ON extraction_snapshot_body
    BEGIN DELETE FROM native_wiki_snapshot_body WHERE snapshot_id IN
        (SELECT s.id FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
         WHERE r.extraction_snapshot_id=OLD.snapshot_id);
      DELETE FROM native_authoring_outcome_body WHERE attempt_id IN
        (SELECT o.attempt_id FROM native_authoring_outcome o JOIN native_authoring_run r ON r.id=o.run_id
         WHERE r.extraction_snapshot_id=OLD.snapshot_id);
      DELETE FROM native_authoring_run_body WHERE run_id IN
        (SELECT id FROM native_authoring_run WHERE extraction_snapshot_id=OLD.snapshot_id); END""",
    """CREATE TRIGGER native_authoring_preview_consume_guard BEFORE UPDATE OF status ON publication_preview
    WHEN NEW.status='consumed' AND EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_NOT_REGISTERED'); END""",
)

_immutable = ("native_authoring_run", "native_authoring_batch", "native_authoring_attempt",
    "native_authoring_outcome", "native_wiki_snapshot")
V029_STATEMENTS += tuple(f"CREATE TRIGGER {name}_immutable_{verb.lower()} BEFORE {verb} ON {name} "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END" for name in _immutable for verb in ("UPDATE", "DELETE"))
V029_STATEMENTS += tuple(f"CREATE TRIGGER {name}_immutable_update BEFORE UPDATE ON {name} "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END" for name in
    ("native_authoring_run_body", "native_authoring_outcome_body", "native_wiki_snapshot_body"))
V029_STATEMENTS += ("CREATE TRIGGER native_state_delete BEFORE DELETE ON native_authoring_state "
    "BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END",)

V029_POSTCONDITION_QUERIES = (
    """SELECT 'native configuration retention invalid' FROM native_authoring_run r
        LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id
        LEFT JOIN native_authoring_run_body b ON b.run_id=r.id
        WHERE (e.snapshot_id IS NULL)<>(b.run_id IS NULL) LIMIT 1""",
    """SELECT 'native run state/batch completeness invalid' FROM native_authoring_run r
        LEFT JOIN native_authoring_state s ON s.run_id=r.id WHERE s.run_id IS NULL
          OR r.batch_count<>(SELECT count(*) FROM native_authoring_batch WHERE run_id=r.id)
          OR json_extract(r.metadata_json,'$.reserved_input_bytes')<>r.max_executions*r.attempt_units*
              (SELECT coalesce(sum(input_size_bytes),0) FROM native_authoring_batch WHERE run_id=r.id)
          OR (s.status='ready' AND NOT EXISTS(SELECT 1 FROM native_wiki_snapshot WHERE run_id=r.id))
          OR (s.status<>'ready' AND EXISTS(SELECT 1 FROM native_wiki_snapshot WHERE run_id=r.id)) LIMIT 1""",
    """SELECT 'native outcome retention invalid' FROM native_authoring_outcome o
        JOIN native_authoring_run r ON r.id=o.run_id
        LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id
        LEFT JOIN native_authoring_outcome_body b ON b.attempt_id=o.attempt_id
        WHERE (e.snapshot_id IS NULL)<>(b.attempt_id IS NULL) LIMIT 1""",
    """SELECT 'native snapshot retention invalid' FROM native_wiki_snapshot s
        JOIN native_authoring_run r ON r.id=s.run_id
        LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id
        LEFT JOIN native_wiki_snapshot_body b ON b.snapshot_id=s.id
        WHERE (e.snapshot_id IS NULL)<>(b.snapshot_id IS NULL) LIMIT 1""",
    """SELECT 'native pending source missing' FROM native_authoring_run r
        JOIN native_authoring_state s ON s.run_id=r.id
        LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id
        WHERE s.status='pending' AND e.snapshot_id IS NULL LIMIT 1""",
)
