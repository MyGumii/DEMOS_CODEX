"""Forward-only durable receipts and completed draft for v3 Office Wiki work.

The v55 preview remains a source, not a Wiki. A separate bounded writer job
holds immutable native-batch and page-visual model receipts. Its success and
complete Markdown snapshot must commit together. Historical v1/v2 workflows
and their policies are not reinterpreted.
"""

from .v050_protected_extraction_provenance import _SHA
from .v055_office_hybrid_preview import RULE


WRITE = "office_hybrid_wiki.write"
VERSION = "office-hybrid-wiki-write-v1"
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


V056_STATEMENTS = (
    f"""CREATE TABLE office_hybrid_wiki_run(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES office_hybrid_upload_request(preview_id) ON DELETE RESTRICT,
      job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
      parent_job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
      extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
      workflow_version TEXT NOT NULL CHECK(workflow_version='{VERSION}'),
      policy_sha256 TEXT NOT NULL CHECK({_SHA.format(x='policy_sha256')}),
      source_fence_sha256 TEXT NOT NULL CHECK({_SHA.format(x='source_fence_sha256')}),
      native_plan_sha256 TEXT NOT NULL CHECK({_SHA.format(x='native_plan_sha256')}),
      visual_plan_sha256 TEXT NOT NULL CHECK({_SHA.format(x='visual_plan_sha256')}),
      native_batch_count INTEGER NOT NULL CHECK(native_batch_count BETWEEN 0 AND 4096),
      visual_page_count INTEGER NOT NULL CHECK(visual_page_count BETWEEN 1 AND 4096),
      created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE office_hybrid_wiki_receipt(
      preview_id TEXT NOT NULL REFERENCES office_hybrid_wiki_run(preview_id) ON DELETE RESTRICT,
      task_kind TEXT NOT NULL CHECK(task_kind IN ('native_batch','visual_page')),
      ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 4096),
      task_id TEXT NOT NULL CHECK(length(task_id) BETWEEN 3 AND 80),
      request_sha256 TEXT NOT NULL CHECK({_SHA.format(x='request_sha256')}),
      response_sha256 TEXT NOT NULL CHECK({_SHA.format(x='response_sha256')}),
      response_text TEXT NOT NULL CHECK(length(CAST(response_text AS BLOB)) BETWEEN 1 AND 65536),
      model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='array'
        AND length(CAST(model_json AS BLOB)) BETWEEN 2 AND 8192),
      transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object'
        AND length(CAST(transport_json AS BLOB)) BETWEEN 2 AND 65536),
      created_at INTEGER NOT NULL CHECK(created_at>=0),
      PRIMARY KEY(preview_id,task_kind,ordinal)
    ) STRICT""",
    f"""CREATE TABLE office_hybrid_wiki_snapshot(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES office_hybrid_wiki_run(preview_id) ON DELETE RESTRICT,
      job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      content_md TEXT NOT NULL CHECK(length(CAST(content_md AS BLOB)) BETWEEN 1 AND 67108864),
      content_sha256 TEXT NOT NULL CHECK({_SHA.format(x='content_sha256')}),
      manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'
        AND length(CAST(manifest_json AS BLOB)) BETWEEN 2 AND 67108864),
      manifest_sha256 TEXT NOT NULL CHECK({_SHA.format(x='manifest_sha256')}),
      created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    f"""CREATE TRIGGER office_hybrid_wiki_run_insert
      BEFORE INSERT ON office_hybrid_wiki_run
      WHEN NOT EXISTS(SELECT 1 FROM publication_preview p
        JOIN office_hybrid_upload_request q ON q.preview_id=p.id
        JOIN durable_job parent ON parent.id=p.durable_job_id
        JOIN durable_job j ON j.id=NEW.job_id
        JOIN app_user u ON u.id=p.actor_user_id
        JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
        JOIN extraction_native_table_structure n ON n.snapshot_id=e.id AND n.artifact_id=e.artifact_id
        JOIN office_hybrid_visual_archive_ready ar ON ar.preview_id=p.id
        JOIN office_hybrid_visual_archive a ON a.preview_id=ar.preview_id
        WHERE p.id=NEW.preview_id AND p.status='ready' AND p.rule_version='{RULE}'
          AND p.expires_at>{_NOW}
          AND p.durable_job_id=NEW.parent_job_id
          AND p.actor_user_id=NEW.actor_user_id AND p.artifact_id=NEW.artifact_id
          AND p.extraction_snapshot_id=NEW.extraction_snapshot_id
          AND q.parent_job_id=parent.id AND q.policy_sha256=NEW.policy_sha256
          AND parent.state='succeeded' AND parent.handler_version='v3'
          AND parent.job_type='publication_preview.extract'
          AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
          AND e.status='ready' AND e.artifact_id=p.artifact_id
          AND n.structure_sha256=a.native_structure_sha256
          AND a.page_count=NEW.visual_page_count
          AND j.state='queued' AND j.job_type='{WRITE}' AND j.handler_version='v1'
          AND j.actor_kind='user' AND j.actor_user_id=p.actor_user_id
          AND j.space_id=parent.space_id
          AND j.authorization_scope_epoch=p.captured_scope_epoch
          AND j.authorization_part_id=parent.authorization_part_id
          AND json_extract(j.input_ref_json,'$.preview_id')=p.id
          AND json_extract(j.expected_state_json,'$.schema_version')=1
          AND json_extract(j.expected_state_json,'$.workflow_version')=NEW.workflow_version
          AND json_extract(j.expected_state_json,'$.parent_job_id')=parent.id
          AND json_extract(j.expected_state_json,'$.policy_sha256')=NEW.policy_sha256
          AND json_extract(j.expected_state_json,'$.source_fence_sha256')=NEW.source_fence_sha256
          AND json_extract(j.expected_state_json,'$.native_plan_sha256')=NEW.native_plan_sha256
          AND json_extract(j.expected_state_json,'$.visual_plan_sha256')=NEW.visual_plan_sha256
          AND json_extract(j.expected_state_json,'$.native_batch_count')=NEW.native_batch_count
          AND json_extract(j.expected_state_json,'$.visual_page_count')=NEW.visual_page_count)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_WIKI_RUN_INVALID'); END""",
    f"""CREATE TRIGGER office_hybrid_wiki_receipt_insert
      BEFORE INSERT ON office_hybrid_wiki_receipt
      WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_wiki_run r
        JOIN durable_job j ON j.id=r.job_id
        JOIN publication_preview p ON p.id=r.preview_id
        JOIN app_user u ON u.id=r.actor_user_id
        WHERE r.preview_id=NEW.preview_id
          AND j.state='running' AND j.job_type='{WRITE}' AND j.handler_version='v1'
          AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND p.status='ready' AND p.expires_at>{_NOW}
          AND p.actor_user_id=r.actor_user_id
          AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
          AND NEW.ordinal<=CASE NEW.task_kind
            WHEN 'native_batch' THEN r.native_batch_count ELSE r.visual_page_count END)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_WIKI_RECEIPT_INVALID'); END""",
    f"""CREATE TRIGGER office_hybrid_wiki_snapshot_insert
      BEFORE INSERT ON office_hybrid_wiki_snapshot
      WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_wiki_run r
        JOIN durable_job j ON j.id=r.job_id
        JOIN publication_preview p ON p.id=r.preview_id
        JOIN app_user u ON u.id=r.actor_user_id
        WHERE r.preview_id=NEW.preview_id AND r.job_id=NEW.job_id
          AND r.actor_user_id=NEW.actor_user_id
          AND j.state='running' AND j.job_type='{WRITE}' AND j.handler_version='v1'
          AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND p.status='ready' AND p.expires_at>{_NOW}
          AND p.actor_user_id=r.actor_user_id
          AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
          AND (SELECT count(*) FROM office_hybrid_wiki_receipt z
            WHERE z.preview_id=r.preview_id AND z.task_kind='native_batch')=r.native_batch_count
          AND (SELECT count(*) FROM office_hybrid_wiki_receipt z
            WHERE z.preview_id=r.preview_id AND z.task_kind='visual_page')=r.visual_page_count)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_WIKI_SNAPSHOT_INVALID'); END""",
    f"""CREATE TRIGGER office_hybrid_wiki_job_success
      BEFORE UPDATE OF state ON durable_job
      WHEN NEW.job_type='{WRITE}' AND NEW.handler_version='v1' AND NEW.state='succeeded'
        AND NOT EXISTS(SELECT 1 FROM office_hybrid_wiki_snapshot s
          JOIN office_hybrid_wiki_run r ON r.preview_id=s.preview_id
          WHERE r.job_id=NEW.id AND s.job_id=NEW.id)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_WIKI_SUCCESS_WITHOUT_SNAPSHOT'); END""",
    *tuple(f"""CREATE TRIGGER {table}_{operation}
      BEFORE {operation.upper()} ON {table}
      BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END"""
      for table in ("office_hybrid_wiki_run", "office_hybrid_wiki_receipt",
          "office_hybrid_wiki_snapshot") for operation in ("update", "delete")),
)


V056_POSTCONDITION_QUERIES = (
    f"""SELECT 'office_hybrid_wiki_success_missing_snapshot' FROM durable_job j
      LEFT JOIN office_hybrid_wiki_run r ON r.job_id=j.id
      LEFT JOIN office_hybrid_wiki_snapshot s ON s.job_id=j.id
      WHERE j.job_type='{WRITE}' AND j.handler_version='v1' AND j.state='succeeded'
        AND (r.job_id IS NULL OR s.job_id IS NULL) LIMIT 1""",
    """SELECT 'office_hybrid_wiki_snapshot_incomplete' FROM office_hybrid_wiki_snapshot s
      JOIN office_hybrid_wiki_run r ON r.preview_id=s.preview_id
      WHERE (SELECT count(*) FROM office_hybrid_wiki_receipt z
        WHERE z.preview_id=r.preview_id AND z.task_kind='native_batch')<>r.native_batch_count
        OR (SELECT count(*) FROM office_hybrid_wiki_receipt z
        WHERE z.preview_id=r.preview_id AND z.task_kind='visual_page')<>r.visual_page_count
      LIMIT 1""",
)


__all__ = ["WRITE", "VERSION", "V056_STATEMENTS", "V056_POSTCONDITION_QUERIES"]
