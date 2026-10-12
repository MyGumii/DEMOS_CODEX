"""Additive, not-yet-activated authoring storage; v13 remains the runtime catalog.

Generation never changes extraction identity or preview readiness. Body-bearing
rows are separately removable by the future retention integration. This schema
alone does not enable a worker, publication, cleanup or a live migration.
"""


def _uuid(column):
    return f"length({column})=36 AND substr({column},9,1)='-' AND substr({column},14,1)='-' AND substr({column},19,1)='-' AND substr({column},24,1)='-' AND length(replace({column},'-',''))=32 AND replace({column},'-','') NOT GLOB '*[^0-9a-f]*'"


def _sha(column):
    return f"length({column})=64 AND {column} NOT GLOB '*[^0-9a-f]*'"


_EQUIVALENT_UPLOAD = """a.origin_type='upload' AND prior.origin_type='upload'
    AND a.raw_sha256=prior.raw_sha256 AND a.raw_size_bytes=prior.raw_size_bytes
    AND a.media_type=prior.media_type AND a.original_filename=prior.original_filename
    AND x.status='ready' AND px.status='ready' AND x.extracted_sha256=px.extracted_sha256
    AND x.extracted_size_bytes=px.extracted_size_bytes
    AND x.normalized_content_sha256=px.normalized_content_sha256
    AND x.normalizer_version=px.normalizer_version
    AND x.extractor_name=px.extractor_name AND x.extractor_version=px.extractor_version
    AND x.warnings_json=px.warnings_json"""


V014_STATEMENTS = (
    f"""CREATE TABLE publication_authoring_run(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
        actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
        extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
        source_sha256 TEXT NOT NULL CHECK({_sha('source_sha256')}),
        policy_version TEXT NOT NULL,
        plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
        plan_sha256 TEXT NOT NULL CHECK({_sha('plan_sha256')}),
        chunk_count INTEGER NOT NULL CHECK(chunk_count>0),
        status TEXT NOT NULL CHECK(status IN ('pending','ready','extraction_only')),
        failure_code TEXT,
        created_at INTEGER NOT NULL CHECK(created_at>=0),
        completed_at INTEGER,
        row_version INTEGER NOT NULL CHECK(row_version>0),
        CHECK((status='pending' AND completed_at IS NULL AND failure_code IS NULL)
           OR (status='ready' AND completed_at IS NOT NULL AND completed_at>=created_at AND failure_code IS NULL)
           OR (status='extraction_only' AND completed_at IS NOT NULL AND completed_at>=created_at AND failure_code IS NOT NULL))
    ) STRICT""",
    f"""CREATE TABLE publication_authoring_chunk(
        run_id TEXT NOT NULL REFERENCES publication_authoring_run(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL CHECK(ordinal>0),
        chunk_id TEXT NOT NULL,
        source_start INTEGER NOT NULL CHECK(source_start>=0),
        source_end INTEGER NOT NULL CHECK(source_end>source_start),
        source_sha256 TEXT NOT NULL CHECK({_sha('source_sha256')}),
        request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
        durable_job_id TEXT UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
        PRIMARY KEY(run_id,ordinal), UNIQUE(run_id,chunk_id)
    ) STRICT""",
    f"""CREATE TABLE publication_authoring_chunk_body(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        response TEXT NOT NULL,
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        model_json TEXT NOT NULL CHECK(json_valid(model_json)),
        transport_json TEXT NOT NULL CHECK(json_valid(transport_json)),
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        PRIMARY KEY(run_id,ordinal),
        FOREIGN KEY(run_id,ordinal) REFERENCES publication_authoring_chunk(run_id,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE authored_wiki_snapshot(
        id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
        run_id TEXT NOT NULL UNIQUE REFERENCES publication_authoring_run(id) ON DELETE RESTRICT,
        content_sha256 TEXT NOT NULL CHECK({_sha('content_sha256')}),
        content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes>0),
        manifest_sha256 TEXT NOT NULL CHECK({_sha('manifest_sha256')}),
        manifest_size_bytes INTEGER NOT NULL CHECK(manifest_size_bytes>0),
        created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TABLE authored_wiki_snapshot_body(
        snapshot_id TEXT PRIMARY KEY NOT NULL REFERENCES authored_wiki_snapshot(id) ON DELETE RESTRICT,
        content_md TEXT NOT NULL,
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json))
    ) STRICT""",
    """CREATE TABLE publication_authored_selection(
        publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
        authored_snapshot_id TEXT NOT NULL REFERENCES authored_wiki_snapshot(id) ON DELETE RESTRICT
    ) STRICT""",
    # A ready candidate can legitimately lose to an already-published extraction
    # body. Keep that disposition separate: it is not an authored selection.
    """CREATE TABLE publication_extraction_reuse(
        publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
        requested_authored_snapshot_id TEXT NOT NULL REFERENCES authored_wiki_snapshot(id) ON DELETE RESTRICT,
        extraction_snapshot_id TEXT NOT NULL REFERENCES extraction_snapshot(id) ON DELETE RESTRICT,
        source_publication_operation_id TEXT NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
        CHECK(publication_operation_id<>source_publication_operation_id)
    ) STRICT""",
    """CREATE INDEX publication_authoring_pending ON publication_authoring_run(status,created_at,id)""",
    """CREATE INDEX authored_upload_source_identity ON source_artifact(
        raw_sha256,raw_size_bytes,media_type,original_filename) WHERE origin_type='upload'""",
    # The historical extraction-only branch is preserved. An authored lineage
    # may instead bind a ready, exact-source, LF-rendered snapshot. No v1 SQL
    # or active catalog is edited for this opt-in successor behavior.
    "DROP TRIGGER trg_lineage_group_validity_insert",
    f"""CREATE TRIGGER trg_lineage_group_validity_insert BEFORE INSERT ON lineage_group
    WHEN NOT EXISTS(SELECT 1 FROM extraction_snapshot x
      WHERE x.id=NEW.base_extraction_snapshot_id AND x.artifact_id=NEW.base_artifact_id
        AND x.status='ready' AND (x.normalized_content_sha256=NEW.base_content_sha256
          OR EXISTS(SELECT 1 FROM publication_authoring_run r
            JOIN authored_wiki_snapshot s ON s.run_id=r.id
            JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
            WHERE r.extraction_snapshot_id=x.id AND r.artifact_id=x.artifact_id
              AND r.source_sha256=x.extracted_sha256 AND r.actor_user_id=NEW.created_by
              AND r.status='ready' AND s.content_sha256=NEW.base_content_sha256)
          OR EXISTS(SELECT 1 FROM source_artifact a
            JOIN source_artifact prior ON {_EQUIVALENT_UPLOAD.split('AND x.status')[0]}
            JOIN extraction_snapshot px ON px.artifact_id=prior.id
            JOIN publication_operation op ON op.artifact_id=prior.id AND op.extraction_snapshot_id=px.id
            JOIN publication_authored_selection sel ON sel.publication_operation_id=op.id
            JOIN authored_wiki_snapshot s ON s.id=sel.authored_snapshot_id
            WHERE a.id=x.artifact_id AND {_EQUIVALENT_UPLOAD}
              AND s.content_sha256=NEW.base_content_sha256
              AND EXISTS(SELECT 1 FROM publication_authoring_run current_run
                WHERE current_run.actor_user_id=NEW.created_by AND current_run.artifact_id=a.id
                  AND current_run.extraction_snapshot_id=x.id AND current_run.status='ready'))))
    BEGIN SELECT RAISE(ABORT,'LINEAGE_MEMBER_INVALID'); END""",
    """CREATE TRIGGER publication_authoring_run_binding BEFORE INSERT ON publication_authoring_run
    WHEN NEW.status<>'pending' OR NEW.row_version<>1 OR NOT EXISTS(SELECT 1 FROM publication_preview p
      JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id AND e.artifact_id=p.artifact_id
      WHERE p.id=NEW.preview_id AND p.actor_user_id=NEW.actor_user_id
        AND p.artifact_id=NEW.artifact_id AND p.extraction_snapshot_id=NEW.extraction_snapshot_id
        AND p.source_snapshot_sha256=NEW.source_sha256 AND e.extracted_sha256=NEW.source_sha256
        AND e.status='ready' AND p.status='ready'
        AND json_array_length(NEW.plan_json,'$.chunks')=NEW.chunk_count
        AND json_extract(NEW.plan_json,'$.policy_version')=NEW.policy_version
        AND json_extract(NEW.plan_json,'$.source_snapshot_id')=NEW.extraction_snapshot_id
        AND json_extract(NEW.plan_json,'$.source_sha256')=NEW.source_sha256)
    BEGIN SELECT RAISE(ABORT,'authoring_source_binding'); END""",
    """CREATE TRIGGER publication_authoring_run_immutable BEFORE UPDATE ON publication_authoring_run
    WHEN NEW.id<>OLD.id OR NEW.preview_id<>OLD.preview_id OR NEW.actor_user_id<>OLD.actor_user_id
      OR NEW.artifact_id<>OLD.artifact_id OR NEW.extraction_snapshot_id<>OLD.extraction_snapshot_id
      OR NEW.source_sha256<>OLD.source_sha256 OR NEW.policy_version<>OLD.policy_version
      OR NEW.plan_json<>OLD.plan_json OR NEW.plan_sha256<>OLD.plan_sha256
      OR NEW.chunk_count<>OLD.chunk_count OR NEW.created_at<>OLD.created_at
      OR OLD.status<>'pending' OR NEW.row_version<>OLD.row_version+1
    BEGIN SELECT RAISE(ABORT,'authoring_run_immutable'); END""",
    """CREATE TRIGGER publication_authoring_run_terminal BEFORE UPDATE ON publication_authoring_run
    WHEN (NEW.status='ready' AND (NOT EXISTS(
        SELECT 1 FROM authored_wiki_snapshot s JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
        WHERE s.run_id=OLD.id AND s.created_at<=NEW.completed_at)
      OR OLD.chunk_count<>(SELECT count(*) FROM publication_authoring_chunk_body b
         WHERE b.run_id=OLD.id AND b.completed_at<=NEW.completed_at)))
      OR (NEW.status='extraction_only' AND EXISTS(SELECT 1 FROM authored_wiki_snapshot s WHERE s.run_id=OLD.id))
    BEGIN SELECT RAISE(ABORT,'authoring_terminal_incomplete'); END""",
    """CREATE TRIGGER publication_authoring_chunk_binding BEFORE INSERT ON publication_authoring_chunk
    WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r WHERE r.id=NEW.run_id AND r.status='pending'
      AND NEW.ordinal<=r.chunk_count
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].ordinal')=NEW.ordinal
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].chunk_id')=NEW.chunk_id
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].start')=NEW.source_start
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].end')=NEW.source_end
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].source_sha256')=NEW.source_sha256
      AND json_extract(r.plan_json,'$.chunks['||(NEW.ordinal-1)||'].request_sha256')=NEW.request_sha256)
    BEGIN SELECT RAISE(ABORT,'authoring_chunk_binding'); END""",
    """CREATE TRIGGER publication_authoring_chunk_body_binding BEFORE INSERT ON publication_authoring_chunk_body
    WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r WHERE r.id=NEW.run_id
      AND r.status='pending' AND NEW.completed_at>=r.created_at)
    BEGIN SELECT RAISE(ABORT,'authoring_response_binding'); END""",
    """CREATE TRIGGER authored_wiki_snapshot_binding BEFORE INSERT ON authored_wiki_snapshot
    WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r WHERE r.id=NEW.run_id AND r.status='pending'
      AND NEW.created_at>=r.created_at AND r.chunk_count=(SELECT count(*)
        FROM publication_authoring_chunk_body b WHERE b.run_id=r.id AND b.completed_at<=NEW.created_at))
    BEGIN SELECT RAISE(ABORT,'authored_snapshot_incomplete'); END""",
    """CREATE TRIGGER authored_wiki_snapshot_body_binding BEFORE INSERT ON authored_wiki_snapshot_body
    WHEN NOT EXISTS(SELECT 1 FROM authored_wiki_snapshot s JOIN publication_authoring_run r ON r.id=s.run_id
      WHERE s.id=NEW.snapshot_id AND r.status='pending' AND instr(NEW.content_md,char(13))=0
        AND s.content_size_bytes=length(CAST(NEW.content_md AS BLOB))
        AND s.manifest_size_bytes=length(CAST(NEW.manifest_json AS BLOB)))
    BEGIN SELECT RAISE(ABORT,'authored_body_binding'); END""",
    f"""CREATE TRIGGER publication_authored_selection_binding BEFORE INSERT ON publication_authored_selection
    WHEN EXISTS(SELECT 1 FROM publication_extraction_reuse e WHERE e.publication_operation_id=NEW.publication_operation_id)
      OR NOT EXISTS(SELECT 1 FROM publication_operation p
      JOIN authored_wiki_snapshot s ON s.id=NEW.authored_snapshot_id
      JOIN publication_authoring_run r ON r.id=s.run_id AND r.status='ready'
      WHERE p.id=NEW.publication_operation_id AND s.id=NEW.authored_snapshot_id
        AND ((r.actor_user_id=p.actor_user_id AND r.artifact_id=p.artifact_id
          AND r.extraction_snapshot_id=p.extraction_snapshot_id
          AND EXISTS(SELECT 1 FROM authored_wiki_snapshot_body b WHERE b.snapshot_id=s.id))
        OR EXISTS(SELECT 1 FROM source_artifact a
          JOIN extraction_snapshot x ON x.artifact_id=a.id AND x.id=p.extraction_snapshot_id
          JOIN source_artifact prior ON prior.origin_type='upload'
          JOIN extraction_snapshot px ON px.artifact_id=prior.id
          JOIN publication_operation op ON op.artifact_id=prior.id AND op.extraction_snapshot_id=px.id
          JOIN publication_authored_selection old ON old.publication_operation_id=op.id
          JOIN publication_target ot ON ot.publication_operation_id=op.id
          JOIN publication_target nt ON nt.publication_operation_id=p.id AND nt.target_space_id=ot.target_space_id
          JOIN knowledge_item i ON i.id=ot.item_id
          JOIN knowledge_revision_body vb ON vb.revision_id=i.current_revision_id AND vb.item_id=i.id
          WHERE a.id=p.artifact_id AND {_EQUIVALENT_UPLOAD}
            AND old.authored_snapshot_id=s.id AND i.lifecycle_status='active'
            AND i.curation_status IN ('published','provisional')
            AND vb.content_sha256=s.content_sha256 AND vb.content_size_bytes=s.content_size_bytes
            AND EXISTS(SELECT 1 FROM publication_authoring_run current_run
              WHERE current_run.actor_user_id=p.actor_user_id AND current_run.artifact_id=p.artifact_id
                AND current_run.extraction_snapshot_id=p.extraction_snapshot_id AND current_run.status='ready')))
        AND (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
          =CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
        AND NOT EXISTS(SELECT 1 FROM publication_target t
          JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
          LEFT JOIN knowledge_revision_body vb ON vb.revision_id=v.id AND vb.item_id=v.item_id
          WHERE t.publication_operation_id=p.id AND (t.outcome='update_proposed'
            OR v.raw_content_sha256<>s.content_sha256 OR v.content_size_bytes<>s.content_size_bytes
            OR vb.content_sha256 IS NOT s.content_sha256 OR vb.content_size_bytes IS NOT s.content_size_bytes))
        AND (p.lineage_group_id IS NULL OR EXISTS(SELECT 1 FROM lineage_group g
          WHERE g.id=p.lineage_group_id AND g.base_content_sha256=s.content_sha256)))
    BEGIN SELECT RAISE(ABORT,'authored_selection_binding'); END""",
    f"""CREATE TRIGGER publication_extraction_reuse_binding BEFORE INSERT ON publication_extraction_reuse
    WHEN EXISTS(SELECT 1 FROM publication_authored_selection s WHERE s.publication_operation_id=NEW.publication_operation_id)
      OR NOT EXISTS(SELECT 1 FROM publication_operation p
        JOIN source_artifact a ON a.id=p.artifact_id
        JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
        JOIN authored_wiki_snapshot s ON s.id=NEW.requested_authored_snapshot_id
        JOIN authored_wiki_snapshot_body sb ON sb.snapshot_id=s.id
        JOIN publication_authoring_run r ON r.id=s.run_id AND r.status='ready'
          AND r.actor_user_id=p.actor_user_id AND r.artifact_id=a.id
          AND r.extraction_snapshot_id=x.id AND r.source_sha256=x.extracted_sha256
        JOIN publication_operation op ON op.id=NEW.source_publication_operation_id
          AND op.created_at<=p.created_at
        JOIN source_artifact prior ON prior.id=op.artifact_id
        JOIN extraction_snapshot px ON px.id=op.extraction_snapshot_id AND px.artifact_id=prior.id
          AND px.id=NEW.extraction_snapshot_id
        WHERE p.id=NEW.publication_operation_id AND {_EQUIVALENT_UPLOAD}
          AND NOT EXISTS(SELECT 1 FROM publication_authored_selection old WHERE old.publication_operation_id=op.id)
          AND EXISTS(SELECT 1 FROM publication_target ot
            JOIN publication_target nt ON nt.publication_operation_id=p.id
              AND nt.target_space_id=ot.target_space_id AND nt.item_id=ot.item_id
            JOIN knowledge_item i ON i.id=ot.item_id AND i.current_revision_id=nt.resolved_revision_id
              AND i.lifecycle_status='active' AND i.curation_status IN ('published','provisional')
            JOIN knowledge_revision v ON v.id=ot.resolved_revision_id AND v.item_id=i.id
            WHERE ot.publication_operation_id=op.id AND ot.outcome<>'update_proposed'
              AND v.raw_content_sha256=px.extracted_sha256 AND v.content_size_bytes=px.extracted_size_bytes
              AND v.normalized_content_sha256=px.normalized_content_sha256 AND v.normalizer_version=px.normalizer_version)
          AND (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
            =CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
          AND NOT EXISTS(SELECT 1 FROM publication_target t
            JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=v.item_id
            WHERE t.publication_operation_id=p.id AND (t.outcome='update_proposed'
              OR v.raw_content_sha256<>px.extracted_sha256 OR v.content_size_bytes<>px.extracted_size_bytes
              OR v.normalized_content_sha256<>px.normalized_content_sha256 OR v.normalizer_version<>px.normalizer_version
              OR b.content_sha256 IS NOT px.extracted_sha256 OR b.content_size_bytes IS NOT px.extracted_size_bytes))
          AND (p.lineage_group_id IS NULL OR EXISTS(SELECT 1 FROM lineage_group g
            WHERE g.id=p.lineage_group_id AND g.base_content_sha256=px.normalized_content_sha256)))
    BEGIN SELECT RAISE(ABORT,'extraction_reuse_binding'); END""",
    """CREATE TRIGGER publication_extraction_reuse_immutable BEFORE UPDATE ON publication_extraction_reuse
    BEGIN SELECT RAISE(ABORT,'extraction_reuse_immutable'); END""",
    """CREATE TRIGGER publication_extraction_reuse_history BEFORE DELETE ON publication_extraction_reuse
    BEGIN SELECT RAISE(ABORT,'extraction_reuse_immutable'); END""",
    """CREATE TRIGGER publication_authoring_chunk_immutable BEFORE UPDATE ON publication_authoring_chunk
    WHEN NEW.run_id<>OLD.run_id OR NEW.ordinal<>OLD.ordinal OR NEW.chunk_id<>OLD.chunk_id
      OR NEW.source_start<>OLD.source_start OR NEW.source_end<>OLD.source_end
      OR NEW.source_sha256<>OLD.source_sha256 OR NEW.request_sha256<>OLD.request_sha256
      OR OLD.durable_job_id IS NOT NULL OR NEW.durable_job_id IS NULL
    BEGIN SELECT RAISE(ABORT,'authoring_chunk_immutable'); END""",
    """CREATE TRIGGER publication_authoring_chunk_body_immutable BEFORE UPDATE ON publication_authoring_chunk_body
    BEGIN SELECT RAISE(ABORT,'authoring_response_immutable'); END""",
    """CREATE TRIGGER authored_wiki_snapshot_immutable BEFORE UPDATE ON authored_wiki_snapshot
    BEGIN SELECT RAISE(ABORT,'authored_snapshot_immutable'); END""",
    """CREATE TRIGGER authored_wiki_body_immutable BEFORE UPDATE ON authored_wiki_snapshot_body
    BEGIN SELECT RAISE(ABORT,'authored_body_immutable'); END""",
    """CREATE TRIGGER publication_authored_selection_immutable BEFORE UPDATE ON publication_authored_selection
    BEGIN SELECT RAISE(ABORT,'authored_selection_immutable'); END""",
    """CREATE TRIGGER publication_authored_selection_history BEFORE DELETE ON publication_authored_selection
    BEGIN SELECT RAISE(ABORT,'authored_selection_immutable'); END""",
)

V014_POSTCONDITION_QUERIES = (
    """SELECT 'authoring_tables_missing' WHERE 7<>(SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN
    ('publication_authoring_run','publication_authoring_chunk','publication_authoring_chunk_body',
     'authored_wiki_snapshot','authored_wiki_snapshot_body','publication_authored_selection','publication_extraction_reuse'))""",
)
