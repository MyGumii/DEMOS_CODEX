"""Forward-only Office draft identity and currently eligible publication view.

This migration does not register a new public body kind or make a draft
publishable by itself. The permanent snapshot view preserves completed writer
lineage; the candidate view adds current account, preview, and retained-source
checks. External Office export authorization remains an application check.
"""

from .v055_office_hybrid_preview import RULE
from .v056_office_hybrid_wiki import VERSION, WRITE


_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


V057_STATEMENTS = (
    f"""CREATE VIEW office_hybrid_publication_snapshot AS
      SELECT s.preview_id AS snapshot_id,r.preview_id,r.job_id,r.parent_job_id,
        r.actor_user_id,r.artifact_id,r.extraction_snapshot_id,
        r.workflow_version,r.policy_sha256,r.source_fence_sha256,
        r.native_plan_sha256,r.visual_plan_sha256,
        r.native_batch_count,r.visual_page_count,
        s.content_sha256,s.manifest_sha256,s.created_at AS snapshot_created_at
      FROM office_hybrid_wiki_snapshot s
      JOIN office_hybrid_wiki_run r ON r.preview_id=s.preview_id
        AND r.job_id=s.job_id AND r.actor_user_id=s.actor_user_id
      JOIN durable_job writer ON writer.id=r.job_id
        AND writer.job_type='{WRITE}' AND writer.handler_version='v1'
        AND writer.state='succeeded' AND writer.cancel_requested=0
        AND writer.actor_user_id=r.actor_user_id
        AND json_extract(writer.result_json,'$.schema_version')=1
        AND json_extract(writer.result_json,'$.preview_id')=r.preview_id
        AND json_extract(writer.result_json,'$.content_sha256')=s.content_sha256
        AND json_extract(writer.result_json,'$.manifest_sha256')=s.manifest_sha256
      JOIN publication_preview p ON p.id=r.preview_id
        AND p.durable_job_id=r.parent_job_id
        AND p.actor_user_id=r.actor_user_id
        AND p.artifact_id=r.artifact_id
        AND p.extraction_snapshot_id=r.extraction_snapshot_id
        AND p.rule_version='{RULE}'
      JOIN durable_job parent ON parent.id=r.parent_job_id
        AND parent.job_type='publication_preview.extract'
        AND parent.handler_version='v3' AND parent.state='succeeded'
        AND parent.actor_user_id=r.actor_user_id
      JOIN office_hybrid_upload_request q ON q.preview_id=r.preview_id
        AND q.parent_job_id=r.parent_job_id
        AND q.actor_user_id=r.actor_user_id
        AND q.artifact_id=r.artifact_id
        AND q.policy_sha256=r.policy_sha256
      WHERE r.workflow_version='{VERSION}'""",
    f"""CREATE VIEW office_hybrid_publication_candidate AS
      SELECT registered.*,p.source_snapshot_sha256,p.captured_scope_epoch,
        p.row_version AS preview_row_version,a.raw_sha256,a.raw_size_bytes
      FROM office_hybrid_publication_snapshot registered
      JOIN publication_preview p ON p.id=registered.preview_id
      JOIN app_user u ON u.id=registered.actor_user_id
        AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
      JOIN source_artifact a ON a.id=registered.artifact_id
        AND a.origin_type='upload' AND a.publisher_user_id=registered.actor_user_id
        AND a.body_retention_status='present'
      JOIN extraction_snapshot e ON e.id=registered.extraction_snapshot_id
        AND e.artifact_id=a.id AND e.status='ready'
        AND e.body_retention_status='present'
        AND e.extracted_sha256=p.source_snapshot_sha256
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=e.id
        AND eb.artifact_id=a.id AND eb.content_sha256=e.extracted_sha256
        AND eb.content_size_bytes=e.extracted_size_bytes
      JOIN extraction_native_table_structure n ON n.snapshot_id=e.id
        AND n.artifact_id=a.id AND n.extracted_markdown_sha256=e.extracted_sha256
      JOIN extraction_native_table_structure_body nb ON nb.snapshot_id=n.snapshot_id
        AND nb.artifact_id=n.artifact_id AND nb.structure_sha256=n.structure_sha256
      JOIN office_hybrid_visual_archive archive ON archive.preview_id=p.id
        AND archive.artifact_id=a.id AND archive.extraction_snapshot_id=e.id
        AND archive.original_sha256=a.raw_sha256
        AND archive.native_structure_sha256=n.structure_sha256
        AND archive.page_count=registered.visual_page_count
      JOIN office_hybrid_visual_archive_ready ready ON ready.preview_id=archive.preview_id
      JOIN office_hybrid_visual_archive_body body ON body.preview_id=archive.preview_id
      JOIN artifact_blob raw ON raw.artifact_id=a.id
        AND raw.role='raw' AND raw.ordinal=0
        AND raw.body_retention_status='present'
        AND raw.content_sha256=a.raw_sha256 AND raw.size_bytes=a.raw_size_bytes
      JOIN artifact_blob_body retained ON retained.artifact_blob_id=raw.id
        AND retained.content_sha256=raw.content_sha256
      JOIN blob_object object ON object.sha256=retained.content_sha256
        AND object.size_bytes=raw.size_bytes AND object.verification_status='verified'
      WHERE p.status='ready' AND p.expires_at>{_NOW}
        AND (SELECT count(*) FROM office_hybrid_visual_page page
          WHERE page.preview_id=archive.preview_id)=archive.page_count
        AND (SELECT count(*) FROM office_hybrid_visual_page_body page
          WHERE page.preview_id=archive.preview_id)=archive.page_count""",
    f"""CREATE TRIGGER office_hybrid_preview_consume_guard
      BEFORE UPDATE OF status ON publication_preview
      WHEN NEW.status='consumed' AND OLD.rule_version='{RULE}'
        AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
          WHERE q.preview_id=OLD.id)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_PUBLICATION_NOT_REGISTERED'); END""",
)


V057_POSTCONDITION_QUERIES = (
    """SELECT 'office_hybrid_snapshot_unregistered' FROM office_hybrid_wiki_snapshot s
      WHERE NOT EXISTS(SELECT 1 FROM office_hybrid_publication_snapshot registered
        WHERE registered.snapshot_id=s.preview_id AND registered.job_id=s.job_id)
      LIMIT 1""",
)


__all__ = ["V057_STATEMENTS", "V057_POSTCONDITION_QUERIES"]
