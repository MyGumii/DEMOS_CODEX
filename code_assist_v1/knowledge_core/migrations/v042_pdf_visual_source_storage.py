"""Additive exact PDF page retention, not visual author/workflow activation.

Immutable descriptors and purgeable private bundle/page CAS links. Source-body
cleanup owns retention. Existing v39-41 raw-only authoring contracts are frozen.
"""
from .v014_wiki_authoring import _uuid, _sha
from .v007_blob_gc_claim_safety import V007_STATEMENTS, _REFERENCES

POLICY = "canonical-pdf-visual-archive-v1"
_SOURCE = """SELECT 1 FROM pdf_visual_source s JOIN extraction_snapshot_body e
    ON e.snapshot_id=s.extraction_snapshot_id AND e.artifact_id=s.artifact_id
    WHERE s.id={source}"""
_NEW_REFS = _REFERENCES + "\nOR EXISTS(SELECT 1 FROM pdf_visual_retained_blob WHERE sha256={sha})"
_GC_NAMES = ("trg_blob_gc_candidate_reference_insert","trg_blob_gc_candidate_claim_guard",
    "trg_blob_gc_candidate_finalize_delete")

def _gc_successor(name):
    old = next(s for s in V007_STATEMENTS if s.startswith("CREATE TRIGGER "+name+"\n"))
    new = old
    for expr in ("NEW.blob_sha256","OLD.blob_sha256"):
        new = new.replace(_REFERENCES.format(sha=expr),_NEW_REFS.format(sha=expr))
    assert new != old
    return new

def _bundle_source_bound(value):
    return f"""json_type({value},'$.source.geometry.geometry_bytes')='integer'
        AND json_extract({value},'$.source.geometry.geometry_bytes')=s.geometry_size_bytes
        AND json_extract({value},'$.source.geometry.geometry_sha256')=s.geometry_sha256
        AND json_extract({value},'$.source.geometry.snapshot_id')=s.extraction_snapshot_id
        AND json_extract({value},'$.source.geometry.raw_sha256')=a.raw_sha256
        AND json_type({value},'$.source.geometry.raw_bytes')='integer'
        AND json_extract({value},'$.source.geometry.raw_bytes')=a.raw_size_bytes
        AND json_extract({value},'$.source.geometry.filename')=a.original_filename"""

V042_STATEMENTS = (
    f"""CREATE TABLE pdf_visual_source(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        operation_id TEXT NOT NULL UNIQUE CHECK({_uuid('operation_id')}),
        preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
        actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
        extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
        parent_job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
        policy_version TEXT NOT NULL CHECK(policy_version='{POLICY}'),
        source_fence_sha256 TEXT NOT NULL CHECK({_sha('source_fence_sha256')}),
        plan_sha256 TEXT NOT NULL CHECK({_sha('plan_sha256')}),
        bundle_sha256 TEXT NOT NULL CHECK({_sha('bundle_sha256')}),
        bundle_size_bytes ANY NOT NULL CHECK(typeof(bundle_size_bytes)='integer' AND bundle_size_bytes BETWEEN 1 AND 1048576),
        geometry_sha256 TEXT NOT NULL CHECK({_sha('geometry_sha256')}),
        geometry_size_bytes ANY NOT NULL CHECK(typeof(geometry_size_bytes)='integer' AND geometry_size_bytes BETWEEN 1 AND 33554432),
        page_count ANY NOT NULL CHECK(typeof(page_count)='integer' AND page_count BETWEEN 1 AND 1000),
        page_bytes ANY NOT NULL CHECK(typeof(page_bytes)='integer' AND page_bytes BETWEEN 1 AND 67108864),
        page_pixels ANY NOT NULL CHECK(typeof(page_pixels)='integer' AND page_pixels BETWEEN 1 AND 256000000),
        created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    """CREATE TABLE pdf_visual_source_body(
        source_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_visual_source(id) ON DELETE RESTRICT,
        bundle_json TEXT NOT NULL CHECK(json_valid(bundle_json) AND json_type(bundle_json)='object'),
        geometry_content_sha256 TEXT NOT NULL REFERENCES blob_object(sha256) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE pdf_visual_page(
        source_id TEXT NOT NULL REFERENCES pdf_visual_source(id) ON DELETE RESTRICT,
        page ANY NOT NULL CHECK(typeof(page)='integer' AND page BETWEEN 1 AND 1000),
        image_id TEXT NOT NULL CHECK(length(image_id)=67 AND substr(image_id,1,3)='PV_'
            AND {_sha('substr(image_id,4)')}),
        width ANY NOT NULL CHECK(typeof(width)='integer' AND width>0), height ANY NOT NULL CHECK(typeof(height)='integer' AND height>0),
        content_sha256 TEXT NOT NULL CHECK({_sha('content_sha256')}),
        size_bytes ANY NOT NULL CHECK(typeof(size_bytes)='integer' AND size_bytes BETWEEN 1 AND 16777216),
        PRIMARY KEY(source_id,page), UNIQUE(source_id,image_id), CHECK(width*height<=16000000)
    ) STRICT""",
    """CREATE TABLE pdf_visual_page_body(
        source_id TEXT NOT NULL, page ANY NOT NULL CHECK(typeof(page)='integer'),
        content_sha256 TEXT NOT NULL REFERENCES blob_object(sha256) ON DELETE RESTRICT,
        PRIMARY KEY(source_id,page),
        FOREIGN KEY(source_id,page) REFERENCES pdf_visual_page(source_id,page) ON DELETE RESTRICT
    ) STRICT""",
    "CREATE INDEX idx_pdf_visual_page_blob ON pdf_visual_page_body(content_sha256)",
    "CREATE INDEX idx_pdf_visual_geometry_blob ON pdf_visual_source_body(geometry_content_sha256)",
    """CREATE TABLE pdf_visual_source_ready(
        source_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_visual_source(id) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE VIEW pdf_visual_retained_blob AS
        SELECT b.content_sha256 AS sha256,s.artifact_id FROM pdf_visual_page_body b
        JOIN pdf_visual_source s ON s.id=b.source_id
        UNION SELECT b.geometry_content_sha256,s.artifact_id FROM pdf_visual_source_body b
        JOIN pdf_visual_source s ON s.id=b.source_id""",
    """CREATE TRIGGER pdf_visual_source_insert BEFORE INSERT ON pdf_visual_source
    WHEN NOT EXISTS(SELECT 1 FROM publication_preview p
        JOIN source_artifact a ON a.id=p.artifact_id
        JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=a.id
        JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=a.id
        JOIN durable_job j ON j.id=p.durable_job_id
        JOIN app_user u ON u.id=p.actor_user_id
        WHERE p.id=NEW.preview_id AND p.status='ready' AND p.actor_user_id=NEW.actor_user_id
          AND p.expires_at>CAST(unixepoch('subsec')*1000 AS INTEGER)
          AND p.artifact_id=NEW.artifact_id AND p.extraction_snapshot_id=NEW.extraction_snapshot_id
          AND p.durable_job_id=NEW.parent_job_id AND j.state='succeeded'
          AND j.actor_user_id=NEW.actor_user_id AND j.job_type='publication_preview.extract' AND j.handler_version='v1'
          AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
          AND a.origin_type='upload' AND a.publisher_user_id=NEW.actor_user_id
          AND a.body_retention_status='present' AND e.body_retention_status='present'
          AND lower(substr(a.original_filename,-4))='.pdf' AND e.status='ready'
          AND e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables')
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SOURCE_INVALID'); END""",
    f"""CREATE TRIGGER pdf_visual_source_body_insert BEFORE INSERT ON pdf_visual_source_body
    WHEN NOT EXISTS(SELECT 1 FROM pdf_visual_source s
        JOIN source_artifact a ON a.id=s.artifact_id
        JOIN extraction_snapshot_body e ON e.snapshot_id=s.extraction_snapshot_id AND e.artifact_id=s.artifact_id
        JOIN blob_object o ON o.sha256=NEW.geometry_content_sha256
        WHERE s.id=NEW.source_id AND length(CAST(NEW.bundle_json AS BLOB))=s.bundle_size_bytes
          AND s.geometry_sha256=o.sha256 AND s.geometry_size_bytes=o.size_bytes AND o.verification_status='verified'
          AND json_extract(NEW.bundle_json,'$.version')='canonical-pdf-visual-archive-v1'
          AND json_extract(NEW.bundle_json,'$.plan_sha256')=s.plan_sha256
          AND json_type(NEW.bundle_json,'$.source.pages')='array'
          AND {_bundle_source_bound('NEW.bundle_json')}
          AND json_array_length(NEW.bundle_json,'$.source.pages')=s.page_count)
      OR EXISTS(SELECT 1 FROM blob_gc_candidate WHERE blob_sha256=NEW.geometry_content_sha256 AND state IN ('claimed','retry','dead'))
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_BUNDLE_INVALID'); END""",
    """CREATE TRIGGER pdf_visual_page_insert BEFORE INSERT ON pdf_visual_page
    WHEN NOT EXISTS(SELECT 1 FROM pdf_visual_source s JOIN pdf_visual_source_body b ON b.source_id=s.id
      WHERE s.id=NEW.source_id AND NEW.page=(SELECT count(*)+1 FROM pdf_visual_page WHERE source_id=s.id)
        AND NEW.page<=s.page_count
        AND NEW.size_bytes+(SELECT coalesce(sum(size_bytes),0) FROM pdf_visual_page WHERE source_id=s.id)<=s.page_bytes
        AND NEW.width*NEW.height+(SELECT coalesce(sum(width*height),0) FROM pdf_visual_page WHERE source_id=s.id)<=s.page_pixels
        AND EXISTS(SELECT 1 FROM json_each(b.bundle_json,'$.source.pages') p
          WHERE p.key=NEW.page-1 AND json_extract(p.value,'$.page')=NEW.page
            AND json_type(p.value,'$.page')='integer' AND json_type(p.value,'$.width')='integer'
            AND json_type(p.value,'$.height')='integer' AND json_type(p.value,'$.bytes')='integer'
            AND json_extract(p.value,'$.width')=NEW.width AND json_extract(p.value,'$.height')=NEW.height
            AND json_extract(p.value,'$.source_id')=NEW.image_id AND json_extract(p.value,'$.sha256')=NEW.content_sha256
            AND json_extract(p.value,'$.bytes')=NEW.size_bytes AND json_extract(p.value,'$.media_type')='image/png'))
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_PAGE_INVALID'); END""",
    """CREATE TRIGGER pdf_visual_page_body_insert BEFORE INSERT ON pdf_visual_page_body
    WHEN NOT EXISTS(SELECT 1 FROM pdf_visual_page p JOIN pdf_visual_source s ON s.id=p.source_id
      JOIN extraction_snapshot_body e ON e.snapshot_id=s.extraction_snapshot_id AND e.artifact_id=s.artifact_id
      JOIN blob_object o ON o.sha256=NEW.content_sha256
      WHERE p.source_id=NEW.source_id AND p.page=NEW.page AND p.content_sha256=NEW.content_sha256
        AND p.size_bytes=o.size_bytes AND o.verification_status='verified'
        AND o.relative_path=substr(o.sha256,1,2)||'/'||substr(o.sha256,3,2)||'/'||o.sha256)
      OR EXISTS(SELECT 1 FROM blob_gc_candidate WHERE blob_sha256=NEW.content_sha256 AND state IN ('claimed','retry','dead'))
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_PAGE_BLOB_INVALID'); END""",
    """CREATE TRIGGER pdf_visual_ready_insert BEFORE INSERT ON pdf_visual_source_ready
    WHEN NOT EXISTS(SELECT 1 FROM pdf_visual_source s JOIN pdf_visual_source_body b ON b.source_id=s.id
      WHERE s.id=NEW.source_id
        AND (SELECT count(*) FROM pdf_visual_page WHERE source_id=s.id)=s.page_count
        AND (SELECT count(*) FROM pdf_visual_page_body WHERE source_id=s.id)=s.page_count
        AND (SELECT sum(size_bytes) FROM pdf_visual_page WHERE source_id=s.id)=s.page_bytes
        AND (SELECT sum(width*height) FROM pdf_visual_page WHERE source_id=s.id)=s.page_pixels)
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SOURCE_INCOMPLETE'); END""",
    *(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_IMMUTABLE'); END"
        for table,verbs in (("pdf_visual_source",("UPDATE","DELETE")),("pdf_visual_page",("UPDATE","DELETE")),
            ("pdf_visual_source_body",("UPDATE",)),("pdf_visual_page_body",("UPDATE",)),
            ("pdf_visual_source_ready",("UPDATE","DELETE"))) for verb in verbs),
    *(f"""CREATE TRIGGER {table}_retain BEFORE DELETE ON {table}
        WHEN EXISTS({_SOURCE.format(source='OLD.source_id')})
        BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SOURCE_RETAINED'); END"""
        for table in ("pdf_visual_source_body","pdf_visual_page_body")),
    """CREATE TRIGGER pdf_visual_source_cleanup AFTER DELETE ON extraction_snapshot_body
    BEGIN DELETE FROM pdf_visual_page_body WHERE source_id IN (SELECT id FROM pdf_visual_source
        WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id);
      DELETE FROM pdf_visual_source_body WHERE source_id IN (SELECT id FROM pdf_visual_source
        WHERE extraction_snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id); END""",
    *(statement for name in _GC_NAMES for statement in ("DROP TRIGGER "+name,_gc_successor(name))),
)

V042_POSTCONDITION_QUERIES = (
    """SELECT 'PDF visual source page descriptor mismatch' FROM pdf_visual_source s
      WHERE NOT EXISTS(SELECT 1 FROM pdf_visual_source_ready WHERE source_id=s.id)
        OR (SELECT count(*) FROM pdf_visual_page WHERE source_id=s.id)<>s.page_count
        OR (SELECT coalesce(sum(size_bytes),0) FROM pdf_visual_page WHERE source_id=s.id)<>s.page_bytes
        OR (SELECT coalesce(sum(width*height),0) FROM pdf_visual_page WHERE source_id=s.id)<>s.page_pixels LIMIT 1""",
    """SELECT 'PDF visual source retention mismatch' FROM pdf_visual_source s
      LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=s.extraction_snapshot_id AND e.artifact_id=s.artifact_id
      LEFT JOIN pdf_visual_source_body b ON b.source_id=s.id
      WHERE (e.snapshot_id IS NULL)<>(b.source_id IS NULL)
        OR (SELECT count(*) FROM pdf_visual_page_body WHERE source_id=s.id)<>CASE WHEN e.snapshot_id IS NULL THEN 0 ELSE s.page_count END LIMIT 1""",
    """SELECT 'PDF visual blob identity mismatch' FROM pdf_visual_page_body b
      JOIN pdf_visual_page p ON p.source_id=b.source_id AND p.page=b.page
      LEFT JOIN blob_object o ON o.sha256=b.content_sha256
      WHERE o.sha256 IS NULL OR o.sha256<>p.content_sha256 OR o.size_bytes<>p.size_bytes
        OR o.verification_status<>'verified'
        OR EXISTS(SELECT 1 FROM blob_gc_candidate WHERE blob_sha256=o.sha256 AND state IN ('claimed','retry','dead')) LIMIT 1""",
    f"""SELECT 'PDF visual geometry identity mismatch' FROM pdf_visual_source_body b
      JOIN pdf_visual_source s ON s.id=b.source_id LEFT JOIN blob_object o ON o.sha256=b.geometry_content_sha256
      JOIN source_artifact a ON a.id=s.artifact_id
      WHERE o.sha256 IS NULL OR o.sha256<>s.geometry_sha256 OR o.size_bytes<>s.geometry_size_bytes
        OR o.verification_status<>'verified' OR length(CAST(b.bundle_json AS BLOB))<>s.bundle_size_bytes
        OR NOT coalesce(({_bundle_source_bound('b.bundle_json')}),0)
        OR EXISTS(SELECT 1 FROM blob_gc_candidate WHERE blob_sha256=o.sha256 AND state IN ('claimed','retry','dead')) LIMIT 1""",
)
