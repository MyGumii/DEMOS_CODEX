"""Opt-in source-owned table storage, additive to the frozen v1-v26 chain.

Descriptors contain no source text and survive retention. Canonical JSON bodies
are deleted with the existing, authority-guarded extraction body. This migration
does not register an upload handler or enable a preservation policy.
"""


def _sha(column: str) -> str:
    return f"length({column})=64 AND {column} NOT GLOB '*[^0-9a-f]*'"


V027_STATEMENTS = (
    f"""CREATE TABLE extraction_native_table_structure (
        snapshot_id TEXT NOT NULL UNIQUE,
        artifact_id TEXT NOT NULL,
        structure_version TEXT NOT NULL CHECK(structure_version='native-source-tables-v1'),
        renderer_version TEXT NOT NULL CHECK(renderer_version='native-source-table-html-v1'),
        source_format TEXT NOT NULL CHECK(source_format IN ('docx','pptx')),
        raw_source_sha256 TEXT NOT NULL CHECK({_sha('raw_source_sha256')}),
        raw_size_bytes INTEGER NOT NULL CHECK(typeof(raw_size_bytes)='integer' AND raw_size_bytes>=0),
        extracted_markdown_sha256 TEXT NOT NULL CHECK({_sha('extracted_markdown_sha256')}),
        structure_sha256 TEXT NOT NULL CHECK({_sha('structure_sha256')}),
        structure_size_bytes INTEGER NOT NULL CHECK(typeof(structure_size_bytes)='integer' AND structure_size_bytes BETWEEN 1 AND 16777216),
        table_count INTEGER NOT NULL CHECK(typeof(table_count)='integer' AND table_count BETWEEN 0 AND 4096),
        coverage TEXT NOT NULL CHECK(coverage='main_story_or_declared_slide_native_tables'),
        created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
        PRIMARY KEY(snapshot_id,artifact_id),
        FOREIGN KEY(snapshot_id,artifact_id) REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE extraction_native_table_structure_body (
        snapshot_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        structure_json TEXT NOT NULL CHECK(json_valid(structure_json) AND instr(structure_json,char(0))=0),
        structure_sha256 TEXT NOT NULL CHECK({_sha('structure_sha256')}),
        structure_size_bytes INTEGER NOT NULL CHECK(typeof(structure_size_bytes)='integer' AND structure_size_bytes BETWEEN 1 AND 16777216
            AND length(CAST(structure_json AS BLOB))=structure_size_bytes),
        created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
        PRIMARY KEY(snapshot_id,artifact_id),
        FOREIGN KEY(snapshot_id,artifact_id) REFERENCES extraction_native_table_structure(snapshot_id,artifact_id) ON DELETE RESTRICT
    ) STRICT""",
    """CREATE TRIGGER native_table_descriptor_insert BEFORE INSERT ON extraction_native_table_structure
    WHEN NOT EXISTS (
        SELECT 1 FROM extraction_snapshot x
        JOIN extraction_snapshot_body b ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
        JOIN source_artifact a ON a.id=x.artifact_id
        WHERE x.id=NEW.snapshot_id AND x.artifact_id=NEW.artifact_id
          AND x.status='ready' AND x.body_retention_status='present'
          AND x.extractor_version='rich-source-v4-native-tables'
          AND a.body_retention_status='present'
          AND a.raw_sha256=NEW.raw_source_sha256 AND a.raw_size_bytes=NEW.raw_size_bytes
          AND lower(substr(a.original_filename,-5))='.'||NEW.source_format
          AND x.extracted_sha256=NEW.extracted_markdown_sha256
          AND b.content_sha256=NEW.extracted_markdown_sha256 AND x.created_at=NEW.created_at
    ) BEGIN SELECT RAISE(ABORT,'NATIVE_TABLE_SOURCE_MISMATCH'); END""",
    """CREATE TRIGGER native_table_descriptor_update BEFORE UPDATE ON extraction_native_table_structure
    BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER native_table_descriptor_delete BEFORE DELETE ON extraction_native_table_structure
    BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER native_table_body_insert BEFORE INSERT ON extraction_native_table_structure_body
    WHEN NOT EXISTS (
        SELECT 1 FROM extraction_native_table_structure d
        JOIN extraction_snapshot_body b ON b.snapshot_id=d.snapshot_id AND b.artifact_id=d.artifact_id
        JOIN extraction_snapshot x ON x.id=d.snapshot_id AND x.artifact_id=d.artifact_id
        WHERE d.snapshot_id=NEW.snapshot_id AND d.artifact_id=NEW.artifact_id
          AND x.body_retention_status='present' AND x.status='ready'
          AND d.structure_sha256=NEW.structure_sha256 AND d.structure_size_bytes=NEW.structure_size_bytes
          AND d.created_at=NEW.created_at AND b.content_sha256=d.extracted_markdown_sha256
          AND json_type(NEW.structure_json)='object'
          AND (SELECT count(*) FROM json_each(NEW.structure_json))=6
          AND json_type(NEW.structure_json,'$.blocks')='array'
          AND json_type(NEW.structure_json,'$.source_parts')='array'
          AND json_extract(NEW.structure_json,'$.version')=d.structure_version
          AND json_extract(NEW.structure_json,'$.source_sha256')=d.raw_source_sha256
          AND json_extract(NEW.structure_json,'$.format')=d.source_format
          AND json_extract(NEW.structure_json,'$.coverage')=d.coverage
          AND (SELECT count(*) FROM json_tree(NEW.structure_json)
               WHERE key='table_id' AND type='text')=d.table_count
    ) BEGIN SELECT RAISE(ABORT,'NATIVE_TABLE_BODY_MISMATCH'); END""",
    """CREATE TRIGGER native_table_body_update BEFORE UPDATE ON extraction_native_table_structure_body
    BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER native_table_body_delete BEFORE DELETE ON extraction_native_table_structure_body
    WHEN EXISTS (SELECT 1 FROM extraction_snapshot_body b
        WHERE b.snapshot_id=OLD.snapshot_id AND b.artifact_id=OLD.artifact_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_TABLE_RETENTION_INVALID'); END""",
    """CREATE TRIGGER native_table_purge_after_extraction_body AFTER DELETE ON extraction_snapshot_body
    BEGIN DELETE FROM extraction_native_table_structure_body
        WHERE snapshot_id=OLD.snapshot_id AND artifact_id=OLD.artifact_id; END""",
    """CREATE TRIGGER native_table_purge_after_extraction_status AFTER UPDATE OF body_retention_status ON extraction_snapshot
    WHEN NEW.body_retention_status<>'present'
    BEGIN DELETE FROM extraction_native_table_structure_body
        WHERE snapshot_id=NEW.id AND artifact_id=NEW.artifact_id; END""",
    """CREATE TRIGGER native_table_extraction_retention_guard BEFORE UPDATE OF body_retention_status ON extraction_snapshot
    WHEN NEW.body_retention_status<>'present' AND EXISTS (
        SELECT 1 FROM extraction_native_table_structure_body b
        WHERE b.snapshot_id=OLD.id AND b.artifact_id=OLD.artifact_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_TABLE_RETENTION_INVALID'); END""",
    """CREATE TRIGGER native_table_artifact_retention_guard BEFORE UPDATE OF body_retention_status ON source_artifact
    WHEN NEW.body_retention_status<>'present' AND EXISTS (
        SELECT 1 FROM extraction_native_table_structure_body b WHERE b.artifact_id=OLD.id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_TABLE_RETENTION_INVALID'); END""",
    "CREATE INDEX idx_native_table_body_artifact ON extraction_native_table_structure_body(artifact_id)",
)

V027_POSTCONDITION_QUERIES = (
    """SELECT 'native extraction structure bundle invalid' FROM extraction_snapshot x
        LEFT JOIN extraction_native_table_structure d ON d.snapshot_id=x.id AND d.artifact_id=x.artifact_id
        LEFT JOIN extraction_native_table_structure_body b ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
        LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=x.id AND e.artifact_id=x.artifact_id
        WHERE x.extractor_version='rich-source-v4-native-tables' AND (
            (x.status='ready' AND (d.snapshot_id IS NULL
                OR (x.body_retention_status='present' AND (b.snapshot_id IS NULL OR e.snapshot_id IS NULL))
                OR (x.body_retention_status='purged' AND (b.snapshot_id IS NOT NULL OR e.snapshot_id IS NOT NULL))
                OR x.body_retention_status NOT IN ('present','purged')))
            OR (x.status='failed' AND (d.snapshot_id IS NOT NULL OR b.snapshot_id IS NOT NULL))
        ) LIMIT 1""",
    """SELECT 'native structure source binding invalid' FROM extraction_native_table_structure d
        LEFT JOIN extraction_snapshot x ON x.id=d.snapshot_id AND x.artifact_id=d.artifact_id
        LEFT JOIN source_artifact a ON a.id=d.artifact_id
        LEFT JOIN extraction_native_table_structure_body b ON b.snapshot_id=d.snapshot_id AND b.artifact_id=d.artifact_id
        WHERE x.extractor_version IS NOT 'rich-source-v4-native-tables' OR x.status IS NOT 'ready'
           OR x.extracted_sha256 IS NOT d.extracted_markdown_sha256 OR x.created_at IS NOT d.created_at
           OR a.raw_sha256 IS NOT d.raw_source_sha256 OR a.raw_size_bytes IS NOT d.raw_size_bytes
           OR lower(substr(a.original_filename,-5)) IS NOT '.'||d.source_format
           OR (b.snapshot_id IS NOT NULL AND (b.structure_sha256 IS NOT d.structure_sha256
               OR b.structure_size_bytes IS NOT d.structure_size_bytes OR b.created_at IS NOT d.created_at))
        LIMIT 1""",
)
