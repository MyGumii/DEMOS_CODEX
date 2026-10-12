"""Forward-only retained Office PDF/page bytes for native-plus-visual authoring.

Only ordinary, native-extracted DOCX/PPTX may enter this archive. The v53
original/derivative row and v27 native structure remain authoritative. Page
pixels and the exact derivative PDF are private CAS blobs; their descriptors
survive extraction-body purge, while the blob references do not.
"""

from .v050_protected_extraction_provenance import _SHA
from .v042_pdf_visual_source_storage import V042_STATEMENTS, _NEW_REFS, _GC_NAMES


POLICY = "office-native-page-visual-archive-v1"
_OFFICE_REFS = _NEW_REFS + "\nOR EXISTS(SELECT 1 FROM office_hybrid_retained_blob WHERE sha256={sha})"


def _gc_successor(name: str) -> str:
    prior = next(sql for sql in V042_STATEMENTS
        if sql.startswith("CREATE TRIGGER " + name + "\n"))
    successor = prior
    for expression in ("NEW.blob_sha256", "OLD.blob_sha256"):
        successor = successor.replace(_NEW_REFS.format(sha=expression),
            _OFFICE_REFS.format(sha=expression))
    assert successor != prior
    return successor


V054_STATEMENTS = (
    f"""CREATE TABLE office_hybrid_visual_archive(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES office_visual_derivative(preview_id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
      extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
      original_sha256 TEXT NOT NULL CHECK({_SHA.format(x='original_sha256')}),
      derivative_sha256 TEXT NOT NULL CHECK({_SHA.format(x='derivative_sha256')}),
      native_structure_sha256 TEXT NOT NULL CHECK({_SHA.format(x='native_structure_sha256')}),
      identity_sha256 TEXT NOT NULL CHECK({_SHA.format(x='identity_sha256')}),
      identity_size_bytes INTEGER NOT NULL CHECK(identity_size_bytes BETWEEN 1 AND 1048576),
      page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 1000),
      page_bytes INTEGER NOT NULL CHECK(page_bytes BETWEEN 1 AND 67108864),
      page_pixels INTEGER NOT NULL CHECK(page_pixels BETWEEN 1 AND 256000000),
      policy_version TEXT NOT NULL CHECK(policy_version='{POLICY}'),
      created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE office_hybrid_visual_archive_body(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES office_hybrid_visual_archive(preview_id) ON DELETE RESTRICT,
      identity_json TEXT NOT NULL CHECK(json_valid(identity_json) AND json_type(identity_json)='object'),
      derivative_blob_sha256 TEXT NOT NULL REFERENCES blob_object(sha256) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE office_hybrid_visual_page(
      preview_id TEXT NOT NULL REFERENCES office_hybrid_visual_archive(preview_id) ON DELETE RESTRICT,
      page INTEGER NOT NULL CHECK(page BETWEEN 1 AND 1000),
      source_id TEXT NOT NULL CHECK(length(source_id)=67 AND substr(source_id,1,3)='OV_'
        AND {_SHA.format(x='substr(source_id,4)')}),
      width INTEGER NOT NULL CHECK(width>0),
      height INTEGER NOT NULL CHECK(height>0),
      content_sha256 TEXT NOT NULL CHECK({_SHA.format(x='content_sha256')}),
      size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 16777216),
      PRIMARY KEY(preview_id,page), UNIQUE(preview_id,source_id),
      CHECK(width*height<=16000000)
    ) STRICT""",
    """CREATE TABLE office_hybrid_visual_page_body(
      preview_id TEXT NOT NULL,
      page INTEGER NOT NULL,
      content_sha256 TEXT NOT NULL REFERENCES blob_object(sha256) ON DELETE RESTRICT,
      PRIMARY KEY(preview_id,page),
      FOREIGN KEY(preview_id,page) REFERENCES office_hybrid_visual_page(preview_id,page) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TABLE office_hybrid_visual_archive_ready(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES office_hybrid_visual_archive(preview_id) ON DELETE RESTRICT
    ) STRICT""",
    "CREATE INDEX idx_office_hybrid_page_blob ON office_hybrid_visual_page_body(content_sha256)",
    "CREATE INDEX idx_office_hybrid_derivative_blob ON office_hybrid_visual_archive_body(derivative_blob_sha256)",
    """CREATE VIEW office_hybrid_retained_blob AS
      SELECT b.derivative_blob_sha256 AS sha256,a.artifact_id
        FROM office_hybrid_visual_archive_body b
        JOIN office_hybrid_visual_archive a ON a.preview_id=b.preview_id
      UNION SELECT b.content_sha256,a.artifact_id
        FROM office_hybrid_visual_page_body b
        JOIN office_hybrid_visual_archive a ON a.preview_id=b.preview_id""",
    """CREATE TRIGGER office_hybrid_archive_insert BEFORE INSERT ON office_hybrid_visual_archive
    WHEN NOT EXISTS(SELECT 1 FROM office_visual_derivative d
      JOIN publication_preview p ON p.id=d.preview_id
      JOIN extraction_snapshot e ON e.id=d.extraction_snapshot_id AND e.artifact_id=d.artifact_id
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=e.id AND eb.artifact_id=e.artifact_id
      JOIN extraction_native_table_structure n ON n.snapshot_id=e.id AND n.artifact_id=e.artifact_id
      JOIN extraction_native_table_structure_body nb ON nb.snapshot_id=e.id AND nb.artifact_id=e.artifact_id
      JOIN app_user u ON u.id=d.actor_user_id
      WHERE d.preview_id=NEW.preview_id AND d.actor_user_id=NEW.actor_user_id
        AND d.artifact_id=NEW.artifact_id AND d.extraction_snapshot_id=NEW.extraction_snapshot_id
        AND d.original_sha256=NEW.original_sha256 AND d.derivative_sha256=NEW.derivative_sha256
        AND d.expected_pages=NEW.page_count AND n.structure_sha256=NEW.native_structure_sha256
        AND nb.structure_sha256=n.structure_sha256 AND n.raw_source_sha256=NEW.original_sha256
        AND n.source_format=d.original_format AND p.status='ready'
        AND p.expires_at>CAST(unixepoch('subsec')*1000 AS INTEGER)
        AND e.status='ready' AND e.body_retention_status='present'
        AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
        AND NEW.created_at>=d.created_at)
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_ARCHIVE_INVALID'); END""",
    f"""CREATE TRIGGER office_hybrid_archive_body_insert BEFORE INSERT ON office_hybrid_visual_archive_body
    WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_visual_archive a
      JOIN office_visual_derivative d ON d.preview_id=a.preview_id
      JOIN source_artifact s ON s.id=a.artifact_id
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
      JOIN blob_object o ON o.sha256=NEW.derivative_blob_sha256
      WHERE a.preview_id=NEW.preview_id AND o.sha256=a.derivative_sha256
        AND o.size_bytes=d.derivative_size_bytes AND o.verification_status='verified'
        AND o.relative_path=substr(o.sha256,1,2)||'/'||substr(o.sha256,3,2)||'/'||o.sha256
        AND length(CAST(NEW.identity_json AS BLOB))=a.identity_size_bytes
        AND json_extract(NEW.identity_json,'$.version')='office-native-structure-rendered-pages-v1'
        AND json_extract(NEW.identity_json,'$.original_filename')=s.original_filename
        AND json_extract(NEW.identity_json,'$.original_sha256')=a.original_sha256
        AND json_extract(NEW.identity_json,'$.derivative_sha256')=a.derivative_sha256
        AND json_extract(NEW.identity_json,'$.derivative_pages')=a.page_count
        AND json_extract(NEW.identity_json,'$.render_method')=d.render_method
        AND json_extract(NEW.identity_json,'$.native_format')=d.original_format
        AND json_extract(NEW.identity_json,'$.native_structure_sha256')=a.native_structure_sha256
        AND json_extract(NEW.identity_json,'$.visual_interpretation_complete')=0
        AND json_type(NEW.identity_json,'$.pages')='array'
        AND json_array_length(NEW.identity_json,'$.pages')=a.page_count)
      OR EXISTS(SELECT 1 FROM blob_gc_candidate
        WHERE blob_sha256=NEW.derivative_blob_sha256 AND state IN ('claimed','retry','dead'))
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_ARCHIVE_BODY_INVALID'); END""",
    """CREATE TRIGGER office_hybrid_page_insert BEFORE INSERT ON office_hybrid_visual_page
    WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_visual_archive a
      JOIN office_hybrid_visual_archive_body b ON b.preview_id=a.preview_id
      WHERE a.preview_id=NEW.preview_id
        AND NEW.page=(SELECT count(*)+1 FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)
        AND NEW.page<=a.page_count
        AND NEW.size_bytes+(SELECT coalesce(sum(size_bytes),0) FROM office_hybrid_visual_page
          WHERE preview_id=a.preview_id)<=a.page_bytes
        AND NEW.width*NEW.height+(SELECT coalesce(sum(width*height),0) FROM office_hybrid_visual_page
          WHERE preview_id=a.preview_id)<=a.page_pixels
        AND EXISTS(SELECT 1 FROM json_each(b.identity_json,'$.pages') p
          WHERE p.key=NEW.page-1 AND json_extract(p.value,'$.page')=NEW.page
            AND json_extract(p.value,'$.source_id')=NEW.source_id
            AND json_extract(p.value,'$.width')=NEW.width
            AND json_extract(p.value,'$.height')=NEW.height
            AND json_extract(p.value,'$.sha256')=NEW.content_sha256
            AND json_extract(p.value,'$.bytes')=NEW.size_bytes
            AND json_extract(p.value,'$.media_type')='image/png'))
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_PAGE_INVALID'); END""",
    """CREATE TRIGGER office_hybrid_page_body_insert BEFORE INSERT ON office_hybrid_visual_page_body
    WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_visual_page p
      JOIN office_hybrid_visual_archive a ON a.preview_id=p.preview_id
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
      JOIN blob_object o ON o.sha256=NEW.content_sha256
      WHERE p.preview_id=NEW.preview_id AND p.page=NEW.page
        AND p.content_sha256=NEW.content_sha256 AND p.size_bytes=o.size_bytes
        AND o.verification_status='verified'
        AND o.relative_path=substr(o.sha256,1,2)||'/'||substr(o.sha256,3,2)||'/'||o.sha256)
      OR EXISTS(SELECT 1 FROM blob_gc_candidate
        WHERE blob_sha256=NEW.content_sha256 AND state IN ('claimed','retry','dead'))
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_PAGE_BLOB_INVALID'); END""",
    """CREATE TRIGGER office_hybrid_ready_insert BEFORE INSERT ON office_hybrid_visual_archive_ready
    WHEN NOT EXISTS(SELECT 1 FROM office_hybrid_visual_archive a
      JOIN office_hybrid_visual_archive_body b ON b.preview_id=a.preview_id
      WHERE a.preview_id=NEW.preview_id
        AND (SELECT count(*) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)=a.page_count
        AND (SELECT count(*) FROM office_hybrid_visual_page_body WHERE preview_id=a.preview_id)=a.page_count
        AND (SELECT sum(size_bytes) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)=a.page_bytes
        AND (SELECT sum(width*height) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)=a.page_pixels)
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_ARCHIVE_INCOMPLETE'); END""",
    *(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} "
      "BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_HISTORY_IMMUTABLE'); END"
      for table, verbs in (
        ("office_hybrid_visual_archive", ("UPDATE", "DELETE")),
        ("office_hybrid_visual_page", ("UPDATE", "DELETE")),
        ("office_hybrid_visual_archive_body", ("UPDATE",)),
        ("office_hybrid_visual_page_body", ("UPDATE",)),
        ("office_hybrid_visual_archive_ready", ("UPDATE", "DELETE")),
      ) for verb in verbs),
    """CREATE TRIGGER office_hybrid_archive_body_retain BEFORE DELETE ON office_hybrid_visual_archive_body
    WHEN EXISTS(SELECT 1 FROM office_hybrid_visual_archive a
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
      WHERE a.preview_id=OLD.preview_id)
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_ARCHIVE_RETAINED'); END""",
    """CREATE TRIGGER office_hybrid_page_body_retain BEFORE DELETE ON office_hybrid_visual_page_body
    WHEN EXISTS(SELECT 1 FROM office_hybrid_visual_archive a
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
      WHERE a.preview_id=OLD.preview_id)
    BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_ARCHIVE_RETAINED'); END""",
    """CREATE TRIGGER office_hybrid_archive_cleanup AFTER DELETE ON extraction_snapshot_body
    BEGIN DELETE FROM office_hybrid_visual_page_body WHERE preview_id IN
        (SELECT preview_id FROM office_hybrid_visual_archive
          WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id);
      DELETE FROM office_hybrid_visual_archive_body WHERE preview_id IN
        (SELECT preview_id FROM office_hybrid_visual_archive
          WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id); END""",
    *(sql for name in _GC_NAMES for sql in ("DROP TRIGGER " + name, _gc_successor(name))),
)


V054_POSTCONDITION_QUERIES = (
    """SELECT 'office_hybrid_visual_archive_incomplete'
      FROM office_hybrid_visual_archive a
      LEFT JOIN office_hybrid_visual_archive_ready r ON r.preview_id=a.preview_id
      WHERE r.preview_id IS NULL
        OR (SELECT count(*) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)<>a.page_count
        OR (SELECT coalesce(sum(size_bytes),0) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)<>a.page_bytes
        OR (SELECT coalesce(sum(width*height),0) FROM office_hybrid_visual_page WHERE preview_id=a.preview_id)<>a.page_pixels
      LIMIT 1""",
    """SELECT 'office_hybrid_visual_archive_retention_mismatch'
      FROM office_hybrid_visual_archive a
      LEFT JOIN extraction_snapshot_body eb ON eb.snapshot_id=a.extraction_snapshot_id AND eb.artifact_id=a.artifact_id
      LEFT JOIN office_hybrid_visual_archive_body b ON b.preview_id=a.preview_id
      WHERE (eb.snapshot_id IS NULL)<>(b.preview_id IS NULL)
        OR (SELECT count(*) FROM office_hybrid_visual_page_body WHERE preview_id=a.preview_id)
          <>CASE WHEN eb.snapshot_id IS NULL THEN 0 ELSE a.page_count END
      LIMIT 1""",
    """SELECT 'office_hybrid_visual_archive_blob_mismatch'
      FROM office_hybrid_visual_archive a
      JOIN office_hybrid_visual_archive_body b ON b.preview_id=a.preview_id
      JOIN office_visual_derivative d ON d.preview_id=a.preview_id
      LEFT JOIN blob_object o ON o.sha256=b.derivative_blob_sha256
      WHERE o.sha256 IS NULL OR o.sha256<>a.derivative_sha256 OR o.size_bytes<>d.derivative_size_bytes
        OR o.verification_status<>'verified'
        OR length(CAST(b.identity_json AS BLOB))<>a.identity_size_bytes
      UNION ALL SELECT 'office_hybrid_visual_page_blob_mismatch'
      FROM office_hybrid_visual_page_body b
      JOIN office_hybrid_visual_page p ON p.preview_id=b.preview_id AND p.page=b.page
      LEFT JOIN blob_object o ON o.sha256=b.content_sha256
      WHERE o.sha256 IS NULL OR o.sha256<>p.content_sha256 OR o.size_bytes<>p.size_bytes
        OR o.verification_status<>'verified'
      LIMIT 1""",
)


__all__ = ["POLICY", "V054_STATEMENTS", "V054_POSTCONDITION_QUERIES"]
