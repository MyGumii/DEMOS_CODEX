"""Additive selected-preserved-Wiki publication; old v14-v17 files stay frozen."""
from .v014_wiki_authoring import V014_STATEMENTS, _EQUIVALENT_UPLOAD

_old_lineage = next(s for s in V014_STATEMENTS if s.startswith("CREATE TRIGGER trg_lineage_group_validity_insert"))
_old_guard = _old_lineage.split("WHEN ", 1)[1].split("BEGIN ", 1)[0].strip()

V020_STATEMENTS = (
    """CREATE TABLE publication_preserved_selection(
        publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
        preserved_snapshot_id TEXT NOT NULL REFERENCES preserved_wiki_snapshot(id) ON DELETE RESTRICT,
        requested_snapshot_id TEXT NOT NULL REFERENCES preserved_wiki_snapshot(id) ON DELETE RESTRICT,
        source_publication_operation_id TEXT REFERENCES publication_operation(id) ON DELETE RESTRICT,
        CHECK((source_publication_operation_id IS NULL AND preserved_snapshot_id=requested_snapshot_id)
          OR (source_publication_operation_id IS NOT NULL AND source_publication_operation_id<>publication_operation_id))
    ) STRICT""",
    "DROP TRIGGER trg_lineage_group_validity_insert",
    f"""CREATE TRIGGER trg_lineage_group_validity_insert BEFORE INSERT ON lineage_group
    WHEN ({_old_guard}) AND NOT EXISTS(
      SELECT 1 FROM extraction_snapshot x JOIN publication_preservation_run r
        ON r.extraction_snapshot_id=x.id AND r.artifact_id=x.artifact_id AND r.source_sha256=x.extracted_sha256
        AND r.actor_user_id=NEW.created_by AND r.status='ready'
      JOIN preserved_wiki_snapshot requested ON requested.run_id=r.id
      JOIN preserved_wiki_snapshot_body rb ON rb.snapshot_id=requested.id
      WHERE x.id=NEW.base_extraction_snapshot_id AND x.artifact_id=NEW.base_artifact_id AND x.status='ready'
        AND (requested.content_sha256=NEW.base_content_sha256 OR EXISTS(
          SELECT 1 FROM source_artifact a JOIN source_artifact prior ON a.id<>prior.id
          JOIN extraction_snapshot px ON px.artifact_id=prior.id
          JOIN publication_operation op ON op.artifact_id=prior.id AND op.extraction_snapshot_id=px.id
          JOIN publication_preserved_selection selected ON selected.publication_operation_id=op.id
          JOIN preserved_wiki_snapshot s ON s.id=selected.preserved_snapshot_id
          WHERE a.id=x.artifact_id AND {_EQUIVALENT_UPLOAD} AND s.content_sha256=NEW.base_content_sha256)))
    BEGIN SELECT RAISE(ABORT,'LINEAGE_MEMBER_INVALID'); END""",
    f"""CREATE TRIGGER preserved_publication_selection_binding BEFORE INSERT ON publication_preserved_selection
    WHEN EXISTS(SELECT 1 FROM publication_authored_selection WHERE publication_operation_id=NEW.publication_operation_id)
      OR EXISTS(SELECT 1 FROM publication_extraction_reuse WHERE publication_operation_id=NEW.publication_operation_id)
      OR NOT EXISTS(SELECT 1 FROM publication_operation p
        JOIN preserved_wiki_snapshot requested ON requested.id=NEW.requested_snapshot_id
        JOIN preserved_wiki_snapshot_body rb ON rb.snapshot_id=requested.id
        JOIN publication_preservation_run r ON r.id=requested.run_id AND r.status='ready'
        JOIN publication_preservation_request req ON req.preview_id=r.preview_id AND req.parent_job_id=r.parent_job_id
        JOIN publication_preview preview ON preview.id=req.preview_id AND preview.actor_user_id=p.actor_user_id
          AND preview.status='ready' AND preview.expires_at>p.created_at
        JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=p.artifact_id
          AND x.extracted_sha256=r.source_sha256 AND x.body_retention_status='present'
        JOIN preserved_wiki_snapshot s ON s.id=NEW.preserved_snapshot_id
        WHERE p.id=NEW.publication_operation_id AND r.actor_user_id=p.actor_user_id AND r.artifact_id=p.artifact_id
          AND r.extraction_snapshot_id=p.extraction_snapshot_id
          AND ((s.id=requested.id AND NEW.source_publication_operation_id IS NULL)
            OR EXISTS(SELECT 1 FROM publication_operation op
              JOIN publication_preserved_selection old ON old.publication_operation_id=op.id AND old.preserved_snapshot_id=s.id
              JOIN source_artifact a ON a.id=p.artifact_id JOIN source_artifact prior ON prior.id=op.artifact_id
              JOIN extraction_snapshot px ON px.id=op.extraction_snapshot_id AND px.artifact_id=prior.id
              JOIN publication_target ot ON ot.publication_operation_id=op.id
              JOIN publication_target nt ON nt.publication_operation_id=p.id AND nt.target_space_id=ot.target_space_id
                AND nt.item_id=ot.item_id AND nt.resolved_revision_id=ot.resolved_revision_id
              JOIN knowledge_item i ON i.id=ot.item_id AND i.current_revision_id=nt.resolved_revision_id
                AND i.lifecycle_status='active' AND i.curation_status IN ('published','provisional')
              WHERE op.id=NEW.source_publication_operation_id AND op.created_at<=p.created_at AND {_EQUIVALENT_UPLOAD}))
          AND (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
              =CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
          AND NOT EXISTS(SELECT 1 FROM publication_target t
              JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
              LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=v.item_id
              WHERE t.publication_operation_id=p.id AND (t.outcome='update_proposed'
                OR v.raw_content_sha256<>s.content_sha256 OR v.content_size_bytes<>s.content_size_bytes
                OR v.normalized_content_sha256<>s.content_sha256 OR v.normalizer_version<>'markdown_lf_v1'
                OR b.content_sha256 IS NOT s.content_sha256 OR b.content_size_bytes IS NOT s.content_size_bytes))
          AND (p.lineage_group_id IS NULL OR EXISTS(SELECT 1 FROM lineage_group g
              WHERE g.id=p.lineage_group_id AND g.base_content_sha256=s.content_sha256)))
    BEGIN SELECT RAISE(ABORT,'preserved_publication_selection_binding'); END""",
    """CREATE TRIGGER preservation_preview_consume_guard BEFORE UPDATE OF status ON publication_preview
    WHEN NEW.status='consumed' AND EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=OLD.id)
      AND NOT EXISTS(SELECT 1 FROM publication_preserved_selection s
        JOIN preserved_wiki_snapshot requested ON requested.id=s.requested_snapshot_id
        JOIN publication_preservation_run r ON r.id=requested.run_id
        JOIN publication_operation op ON op.id=s.publication_operation_id
        WHERE r.preview_id=OLD.id AND op.actor_user_id=OLD.actor_user_id AND op.artifact_id=OLD.artifact_id
          AND op.extraction_snapshot_id=OLD.extraction_snapshot_id)
    BEGIN SELECT RAISE(ABORT,'preservation_cannot_publish_unreviewed_extraction'); END""",
    """CREATE TRIGGER preserved_publication_selection_immutable BEFORE UPDATE ON publication_preserved_selection
    BEGIN SELECT RAISE(ABORT,'Immutable preserved publication selection'); END""",
    """CREATE TRIGGER preserved_publication_selection_history BEFORE DELETE ON publication_preserved_selection
    BEGIN SELECT RAISE(ABORT,'Immutable preserved publication selection'); END""",
)

V020_POSTCONDITION_QUERIES = (
    "SELECT 'preserved selection missing' WHERE NOT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='publication_preserved_selection')",
)
