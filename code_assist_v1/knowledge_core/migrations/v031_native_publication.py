"""Exact native snapshot publication; no raw fallback or legacy reinterpretation."""
from .v014_wiki_authoring import _EQUIVALENT_UPLOAD
from .v020_preserved_publication import V020_STATEMENTS

_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"
_old = next(s for s in V020_STATEMENTS if s.startswith("CREATE TRIGGER trg_lineage_group_validity_insert"))
_old_guard = _old.split("WHEN ", 1)[1].split("BEGIN ", 1)[0].strip()

V031_STATEMENTS = (
    "CREATE INDEX native_publication_source_history ON publication_operation(artifact_id,extraction_snapshot_id,created_at,id)",
    """CREATE TABLE publication_native_selection(
        publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
        native_snapshot_id TEXT NOT NULL REFERENCES native_wiki_snapshot(id) ON DELETE RESTRICT,
        requested_snapshot_id TEXT NOT NULL UNIQUE REFERENCES native_wiki_snapshot(id) ON DELETE RESTRICT,
        source_publication_operation_id TEXT REFERENCES publication_operation(id) ON DELETE RESTRICT,
        CHECK((source_publication_operation_id IS NULL AND native_snapshot_id=requested_snapshot_id)
          OR (source_publication_operation_id IS NOT NULL AND source_publication_operation_id<>publication_operation_id
            AND native_snapshot_id<>requested_snapshot_id))
    ) STRICT""",
    f"""CREATE VIEW native_publication_candidate AS
    SELECT s.id AS snapshot_id,r.id AS run_id,r.preview_id,r.actor_user_id,r.artifact_id,
      r.extraction_snapshot_id,r.source_fence_sha256,r.source_plan_sha256,r.metadata_sha256,
      s.content_sha256,s.content_size_bytes,s.manifest_sha256,s.manifest_size_bytes,
      s.created_at AS snapshot_created_at,q.policy_sha256,p.row_version AS preview_row_version,
      state.row_version AS state_row_version,p.source_snapshot_sha256,p.captured_scope_epoch,
      q.deadline_at
    FROM native_authoring_run r JOIN native_authoring_state state ON state.run_id=r.id AND state.status='ready'
      JOIN native_wiki_snapshot s ON s.run_id=r.id
      JOIN native_wiki_snapshot_body body ON body.snapshot_id=s.id
      JOIN native_workflow_request q ON q.preview_id=r.preview_id AND q.parent_job_id=r.parent_job_id
      JOIN native_workflow_planned planned ON planned.run_id=r.id AND planned.preview_id=r.preview_id
      JOIN native_workflow_plan plan ON plan.preview_id=r.preview_id AND plan.source_fence_sha256=r.source_fence_sha256
      JOIN durable_job pj ON pj.id=plan.job_id AND pj.state='succeeded' AND pj.cancel_requested=0
      JOIN native_workflow_completion c ON c.run_id=r.id
      JOIN durable_job cj ON cj.id=c.job_id AND cj.state='succeeded' AND cj.cancel_requested=0
      JOIN publication_preview p ON p.id=r.preview_id AND p.actor_user_id=r.actor_user_id
        AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
      JOIN durable_job parent ON parent.id=r.parent_job_id AND parent.id=p.durable_job_id
        AND parent.state='succeeded' AND parent.cancel_requested=0
        AND parent.actor_user_id=r.actor_user_id AND parent.job_type='publication_preview.extract'
        AND parent.handler_version='v2'
      JOIN app_user u ON u.id=r.actor_user_id AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
      JOIN source_artifact a ON a.id=r.artifact_id AND a.body_retention_status='present'
      JOIN extraction_snapshot x ON x.id=r.extraction_snapshot_id AND x.artifact_id=r.artifact_id
        AND x.body_retention_status='present' AND x.status='ready' AND x.extracted_sha256=p.source_snapshot_sha256
      JOIN extraction_snapshot_body xb ON xb.snapshot_id=x.id AND xb.artifact_id=a.id
        AND xb.content_sha256=x.extracted_sha256 AND xb.content_size_bytes=x.extracted_size_bytes
      JOIN extraction_native_table_structure n ON n.snapshot_id=x.id AND n.artifact_id=a.id
        AND n.raw_source_sha256=a.raw_sha256 AND n.extracted_markdown_sha256=x.extracted_sha256
      JOIN extraction_native_table_structure_body nb ON nb.snapshot_id=n.snapshot_id
        AND nb.structure_sha256=n.structure_sha256 AND nb.structure_size_bytes=n.structure_size_bytes
    WHERE p.status='ready' AND p.expires_at>{_NOW} AND q.deadline_at>{_NOW}
      AND NOT EXISTS(SELECT 1 FROM native_workflow_stop stop WHERE stop.preview_id=r.preview_id)
      AND r.batch_count=(SELECT count(*) FROM native_workflow_batch b JOIN durable_job j ON j.id=b.job_id
        JOIN native_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
        WHERE b.run_id=r.id AND j.state='succeeded' AND j.cancel_requested=0)""",
    "DROP TRIGGER trg_lineage_group_validity_insert",
    f"""CREATE TRIGGER trg_lineage_group_validity_insert BEFORE INSERT ON lineage_group
    WHEN ({_old_guard}) AND NOT EXISTS(
      SELECT 1 FROM native_publication_candidate candidate
      JOIN extraction_snapshot x ON x.id=candidate.extraction_snapshot_id
      WHERE candidate.actor_user_id=NEW.created_by AND candidate.artifact_id=NEW.base_artifact_id
        AND candidate.extraction_snapshot_id=NEW.base_extraction_snapshot_id
        AND (candidate.content_sha256=NEW.base_content_sha256 OR EXISTS(
          SELECT 1 FROM source_artifact a JOIN source_artifact prior ON a.id<>prior.id
          JOIN extraction_snapshot px ON px.artifact_id=prior.id
          JOIN publication_operation op ON op.artifact_id=prior.id AND op.extraction_snapshot_id=px.id
          JOIN publication_native_selection selected ON selected.publication_operation_id=op.id
          JOIN native_wiki_snapshot s ON s.id=selected.native_snapshot_id
          JOIN native_authoring_run r ON r.id=s.run_id
          JOIN native_workflow_request q ON q.preview_id=r.preview_id AND q.policy_sha256=candidate.policy_sha256
          WHERE a.id=x.artifact_id AND {_EQUIVALENT_UPLOAD} AND s.content_sha256=NEW.base_content_sha256)))
    BEGIN SELECT RAISE(ABORT,'LINEAGE_MEMBER_INVALID'); END""",
    f"""CREATE TRIGGER native_publication_selection_insert BEFORE INSERT ON publication_native_selection
    WHEN EXISTS(SELECT 1 FROM publication_authored_selection WHERE publication_operation_id=NEW.publication_operation_id)
      OR EXISTS(SELECT 1 FROM publication_extraction_reuse WHERE publication_operation_id=NEW.publication_operation_id)
      OR EXISTS(SELECT 1 FROM publication_preserved_selection WHERE publication_operation_id=NEW.publication_operation_id)
      OR NOT EXISTS(SELECT 1 FROM publication_operation p
        JOIN native_publication_candidate requested ON requested.snapshot_id=NEW.requested_snapshot_id
          AND requested.actor_user_id=p.actor_user_id AND requested.artifact_id=p.artifact_id
          AND requested.extraction_snapshot_id=p.extraction_snapshot_id
        JOIN native_wiki_snapshot s ON s.id=NEW.native_snapshot_id
        WHERE p.id=NEW.publication_operation_id AND requested.snapshot_created_at<=p.created_at
          AND ((s.id=requested.snapshot_id AND NEW.source_publication_operation_id IS NULL)
            OR EXISTS(SELECT 1 FROM publication_operation op
              JOIN publication_native_selection old ON old.publication_operation_id=op.id AND old.native_snapshot_id=s.id
              JOIN native_authoring_run sr ON sr.id=s.run_id
              JOIN native_workflow_request sq ON sq.preview_id=sr.preview_id AND sq.policy_sha256=requested.policy_sha256
              JOIN source_artifact a ON a.id=p.artifact_id JOIN source_artifact prior ON prior.id=op.artifact_id
              JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
              JOIN extraction_snapshot px ON px.id=op.extraction_snapshot_id AND px.artifact_id=prior.id
              JOIN publication_target ot ON ot.publication_operation_id=op.id AND ot.outcome<>'update_proposed'
              JOIN publication_target nt ON nt.publication_operation_id=p.id AND nt.target_space_id=ot.target_space_id
                AND nt.item_id=ot.item_id
              JOIN knowledge_item i ON i.id=ot.item_id AND i.current_revision_id=nt.resolved_revision_id
                AND i.lifecycle_status='active' AND i.curation_status IN ('published','provisional')
              WHERE op.id=NEW.source_publication_operation_id AND op.created_at<=p.created_at AND {_EQUIVALENT_UPLOAD}))
          AND (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
              =CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
          AND NOT EXISTS(SELECT 1 FROM publication_target t
              LEFT JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
              LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=v.item_id
              WHERE t.publication_operation_id=p.id AND (v.id IS NULL OR t.outcome='update_proposed'
                OR v.raw_content_sha256<>s.content_sha256 OR v.content_size_bytes<>s.content_size_bytes
                OR v.normalized_content_sha256<>s.content_sha256 OR v.normalizer_version<>'markdown_lf_v1'
                OR b.content_sha256 IS NOT s.content_sha256 OR b.content_size_bytes IS NOT s.content_size_bytes))
          AND (p.lineage_group_id IS NULL OR EXISTS(SELECT 1 FROM lineage_group g
              WHERE g.id=p.lineage_group_id AND g.base_content_sha256=s.content_sha256)))
    BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_SELECTION_INVALID'); END""",
    "DROP TRIGGER native_workflow_consume_guard",
    "DROP TRIGGER native_authoring_preview_consume_guard",
    """CREATE TRIGGER native_publication_consume_guard BEFORE UPDATE OF status ON publication_preview
    WHEN NEW.status='consumed' AND (EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=OLD.id)
        OR EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=OLD.id))
      AND NOT EXISTS(SELECT 1 FROM publication_native_selection s
        JOIN native_wiki_snapshot requested ON requested.id=s.requested_snapshot_id
        JOIN native_authoring_run r ON r.id=requested.run_id
        JOIN publication_operation op ON op.id=s.publication_operation_id
        WHERE r.preview_id=OLD.id AND op.actor_user_id=OLD.actor_user_id AND op.artifact_id=OLD.artifact_id
          AND op.extraction_snapshot_id=OLD.extraction_snapshot_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_SELECTION_REQUIRED'); END""",
)
V031_STATEMENTS += tuple(f"CREATE TRIGGER native_publication_selection_{verb.lower()} BEFORE {verb} ON publication_native_selection "
    "BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_HISTORY_IMMUTABLE'); END" for verb in ("UPDATE", "DELETE"))
V031_STATEMENTS += tuple(f"""CREATE TRIGGER native_publication_exclusive_{name} BEFORE INSERT ON {table}
    WHEN EXISTS(SELECT 1 FROM publication_native_selection WHERE publication_operation_id=NEW.publication_operation_id)
    BEGIN SELECT RAISE(ABORT,'NATIVE_PUBLICATION_SELECTION_AMBIGUOUS'); END""" for name, table in (
    ("authored", "publication_authored_selection"), ("extraction", "publication_extraction_reuse"),
    ("preserved", "publication_preserved_selection")))

V031_POSTCONDITION_QUERIES = (
    """SELECT 'native publication consumed without selection' FROM publication_preview p
        WHERE p.status='consumed' AND (EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=p.id)
            OR EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=p.id))
          AND NOT EXISTS(SELECT 1 FROM publication_native_selection s JOIN native_wiki_snapshot n ON n.id=s.requested_snapshot_id
            JOIN native_authoring_run r ON r.id=n.run_id
            JOIN publication_operation op ON op.id=s.publication_operation_id
            WHERE r.preview_id=p.id AND op.actor_user_id=p.actor_user_id
              AND op.artifact_id=p.artifact_id AND op.extraction_snapshot_id=p.extraction_snapshot_id) LIMIT 1""",
    """SELECT 'native publication body identity mismatch' FROM publication_native_selection s
        LEFT JOIN native_wiki_snapshot n ON n.id=s.native_snapshot_id
        LEFT JOIN publication_operation p ON p.id=s.publication_operation_id
        WHERE p.id IS NULL OR n.id IS NULL
          OR (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
            <>CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
          OR EXISTS(SELECT 1 FROM publication_target t
            LEFT JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
            LEFT JOIN knowledge_revision_body b ON b.revision_id=v.id AND b.item_id=v.item_id
            WHERE t.publication_operation_id=p.id AND (v.id IS NULL OR t.outcome='update_proposed'
              OR v.raw_content_sha256 IS NOT n.content_sha256 OR v.content_size_bytes IS NOT n.content_size_bytes
              OR v.normalized_content_sha256 IS NOT n.content_sha256 OR v.normalizer_version IS NOT 'markdown_lf_v1'
              OR (b.revision_id IS NOT NULL AND (b.content_sha256 IS NOT n.content_sha256
                OR b.content_size_bytes IS NOT n.content_size_bytes)))) LIMIT 1""",
    """SELECT 'native publication selection ambiguous' FROM publication_native_selection s
        WHERE EXISTS(SELECT 1 FROM publication_authored_selection WHERE publication_operation_id=s.publication_operation_id)
          OR EXISTS(SELECT 1 FROM publication_extraction_reuse WHERE publication_operation_id=s.publication_operation_id)
          OR EXISTS(SELECT 1 FROM publication_preserved_selection WHERE publication_operation_id=s.publication_operation_id) LIMIT 1""",
    """SELECT 'native publication requested lineage mismatch' FROM publication_native_selection s
        WHERE NOT EXISTS(SELECT 1 FROM publication_operation p
          JOIN native_wiki_snapshot requested ON requested.id=s.requested_snapshot_id
          JOIN native_authoring_run r ON r.id=requested.run_id
          JOIN native_authoring_state state ON state.run_id=r.id AND state.status='ready'
          JOIN native_workflow_request q ON q.preview_id=r.preview_id AND q.parent_job_id=r.parent_job_id
          JOIN publication_preview preview ON preview.id=r.preview_id
          WHERE p.id=s.publication_operation_id AND r.actor_user_id=p.actor_user_id
            AND r.artifact_id=p.artifact_id AND r.extraction_snapshot_id=p.extraction_snapshot_id
            AND preview.actor_user_id=p.actor_user_id AND preview.artifact_id=p.artifact_id
            AND preview.extraction_snapshot_id=p.extraction_snapshot_id AND requested.created_at<=p.created_at) LIMIT 1""",
    f"""SELECT 'native publication reuse lineage mismatch' FROM publication_native_selection s
        WHERE s.source_publication_operation_id IS NOT NULL AND (
          s.source_publication_operation_id=s.publication_operation_id OR s.native_snapshot_id=s.requested_snapshot_id
          OR NOT EXISTS(SELECT 1 FROM publication_operation p
            JOIN native_wiki_snapshot requested ON requested.id=s.requested_snapshot_id
            JOIN native_authoring_run rr ON rr.id=requested.run_id
            JOIN native_workflow_request rq ON rq.preview_id=rr.preview_id AND rq.parent_job_id=rr.parent_job_id
            JOIN publication_operation old ON old.id=s.source_publication_operation_id AND old.created_at<=p.created_at
            JOIN publication_native_selection old_selection ON old_selection.publication_operation_id=old.id
              AND old_selection.native_snapshot_id=s.native_snapshot_id
            JOIN native_wiki_snapshot chosen ON chosen.id=s.native_snapshot_id
            JOIN native_authoring_run cr ON cr.id=chosen.run_id
            JOIN native_workflow_request cq ON cq.preview_id=cr.preview_id AND cq.parent_job_id=cr.parent_job_id
              AND cq.policy_sha256=rq.policy_sha256
            JOIN source_artifact a ON a.id=p.artifact_id
            JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
            JOIN source_artifact prior ON prior.id=old.artifact_id
            JOIN extraction_snapshot px ON px.id=old.extraction_snapshot_id AND px.artifact_id=prior.id
            JOIN publication_target nt ON nt.publication_operation_id=p.id AND nt.outcome<>'update_proposed'
            JOIN publication_target ot ON ot.publication_operation_id=old.id AND ot.outcome<>'update_proposed'
              AND ot.target_space_id=nt.target_space_id AND ot.item_id=nt.item_id
            WHERE p.id=s.publication_operation_id AND {_EQUIVALENT_UPLOAD})) LIMIT 1""",
)
