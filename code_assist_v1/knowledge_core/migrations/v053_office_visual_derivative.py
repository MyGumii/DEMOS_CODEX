"""Forward-only Office original / authorized PDF derivative provenance.

This row is an audit and publication binding, not an authorization grant.
The producer must recheck current export rights before rendering and before
publication. No derivative PDF bytes or decryption secrets are stored here.
The v52 PDF route and its historical migrations remain unchanged.
"""

from .v050_protected_extraction_provenance import _SHA, _UUID


V053_STATEMENTS = (
    f"""CREATE TABLE office_visual_derivative(
      preview_id TEXT PRIMARY KEY NOT NULL CHECK({_UUID.format(x='preview_id')})
        REFERENCES publication_preview(id) ON DELETE RESTRICT,
      artifact_id TEXT NOT NULL CHECK({_UUID.format(x='artifact_id')})
        REFERENCES source_artifact(id) ON DELETE RESTRICT,
      extraction_snapshot_id TEXT NOT NULL UNIQUE
        CHECK({_UUID.format(x='extraction_snapshot_id')})
        REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      original_sha256 TEXT NOT NULL CHECK({_SHA.format(x='original_sha256')}),
      original_format TEXT NOT NULL CHECK(original_format IN ('docx','pptx')),
      derivative_sha256 TEXT NOT NULL CHECK({_SHA.format(x='derivative_sha256')}
        AND derivative_sha256<>original_sha256),
      derivative_size_bytes INTEGER NOT NULL
        CHECK(derivative_size_bytes BETWEEN 1 AND 67108864),
      expected_pages INTEGER NOT NULL CHECK(expected_pages BETWEEN 1 AND 1000),
      render_method TEXT NOT NULL CHECK(length(trim(render_method)) BETWEEN 1 AND 120),
      authorization_method TEXT NOT NULL
        CHECK(length(trim(authorization_method)) BETWEEN 1 AND 120),
      created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE INDEX ix_office_visual_derivative_artifact
       ON office_visual_derivative(artifact_id,preview_id)""",
    """CREATE TRIGGER office_visual_derivative_insert
       BEFORE INSERT ON office_visual_derivative
       WHEN NOT EXISTS(
         SELECT 1 FROM publication_preview p
         JOIN source_artifact a ON a.id=p.artifact_id
         JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id
           AND e.artifact_id=a.id
         JOIN extraction_snapshot_body b ON b.snapshot_id=e.id
           AND b.artifact_id=a.id
         JOIN app_user u ON u.id=p.actor_user_id
         WHERE p.id=NEW.preview_id AND p.status='ready'
           AND p.actor_user_id=NEW.actor_user_id
           AND p.artifact_id=NEW.artifact_id
           AND p.extraction_snapshot_id=NEW.extraction_snapshot_id
           AND a.publisher_user_id=NEW.actor_user_id
           AND a.origin_type='upload' AND a.body_retention_status='present'
           AND e.status='ready' AND e.body_retention_status='present'
           AND u.account_status='active'
           AND a.raw_sha256=NEW.original_sha256
           AND a.raw_size_bytes>0
           AND ((NEW.original_format='docx'
                 AND lower(substr(a.original_filename,-5))='.docx')
             OR (NEW.original_format='pptx'
                 AND lower(substr(a.original_filename,-5))='.pptx'))
           AND NEW.created_at>=e.created_at
       )
       BEGIN SELECT RAISE(ABORT,'OFFICE_VISUAL_DERIVATIVE_INVALID'); END""",
    """CREATE TRIGGER office_visual_derivative_update
       BEFORE UPDATE ON office_visual_derivative
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER office_visual_derivative_delete
       BEFORE DELETE ON office_visual_derivative
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
)

V053_POSTCONDITION_QUERIES = (
    """SELECT 'office_visual_derivative_source_mismatch'
       FROM office_visual_derivative d
       LEFT JOIN publication_preview p ON p.id=d.preview_id
       LEFT JOIN source_artifact a ON a.id=d.artifact_id
       LEFT JOIN extraction_snapshot e ON e.id=d.extraction_snapshot_id
       WHERE p.id IS NULL OR a.id IS NULL OR e.id IS NULL
         OR p.artifact_id<>d.artifact_id
         OR p.extraction_snapshot_id<>d.extraction_snapshot_id
         OR p.actor_user_id<>d.actor_user_id
         OR a.publisher_user_id<>d.actor_user_id
         OR a.raw_sha256<>d.original_sha256
         OR e.artifact_id<>d.artifact_id
       LIMIT 1""",
)

__all__ = ["V053_STATEMENTS", "V053_POSTCONDITION_QUERIES"]
