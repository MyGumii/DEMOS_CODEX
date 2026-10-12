"""Forward-only authoring-only policy gates for the v18-v20 lineage.

The reviewed v5 branch remains byte-for-byte replayable and still requires
two or four validated receipts.  The new policy can complete only with one
validated inventory receipt per source chunk and an explicitly unreviewed
manifest.  No existing immutable row is rewritten.
"""
from .v020_preserved_publication import V020_STATEMENTS


_REVIEWED = "wiki-information-preservation-v5"
_AUTHORING_ONLY = "wiki-information-authoring-v1"
_OLD_SELECTION = next(
    statement
    for statement in V020_STATEMENTS
    if statement.startswith(
        "CREATE TRIGGER preserved_publication_selection_binding"
    )
)
_SELECTION_ANCHOR = (
    "JOIN preserved_wiki_snapshot s ON s.id=NEW.preserved_snapshot_id"
)
if _OLD_SELECTION.count(_SELECTION_ANCHOR) != 1:
    raise RuntimeError("The frozen v20 preserved selection trigger changed.")
_AUTHORING_ONLY_SELECTION = _OLD_SELECTION.replace(
    _SELECTION_ANCHOR,
    _SELECTION_ANCHOR
    + """
        JOIN publication_preservation_run selected_run ON selected_run.id=s.run_id
          AND selected_run.status='ready' AND selected_run.policy_version=r.policy_version""",
)


V021_STATEMENTS = (
    "DROP TRIGGER publication_preservation_receipt_binding",
    "DROP TRIGGER preserved_wiki_snapshot_binding",
    "DROP TRIGGER preserved_wiki_snapshot_body_binding",
    "DROP TRIGGER preservation_chunk_success",
    "DROP TRIGGER preserved_publication_selection_binding",
    f"""CREATE TRIGGER publication_preservation_receipt_binding BEFORE INSERT ON publication_preservation_receipt
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=e.artifact_id
      WHERE r.id=NEW.run_id AND r.status='pending' AND e.body_retention_status='present'
        AND x.content_sha256=r.source_sha256 AND NEW.completed_at>=r.created_at
        AND NEW.sequence=1+(SELECT count(*) FROM publication_preservation_receipt p
          WHERE p.run_id=NEW.run_id AND p.ordinal=NEW.ordinal)
        AND ((r.policy_version='{_REVIEWED}'
              AND NEW.stage=CASE NEW.sequence WHEN 1 THEN 'inventory' WHEN 2 THEN 'review'
                WHEN 3 THEN 'repair' WHEN 4 THEN 'review' END)
          OR (r.policy_version='{_AUTHORING_ONLY}'
              AND NEW.sequence=1 AND NEW.stage='inventory'))
        AND NEW.attempt_units+(SELECT coalesce(sum(attempt_units),0) FROM (
          SELECT attempt_units FROM publication_preservation_receipt WHERE run_id=NEW.run_id
          UNION ALL SELECT attempt_units FROM publication_preservation_failure WHERE run_id=NEW.run_id
        ))<=r.reserved_model_attempts)
    BEGIN SELECT RAISE(ABORT,'preservation_receipt_binding'); END""",
    f"""CREATE TRIGGER preserved_wiki_snapshot_binding BEFORE INSERT ON preserved_wiki_snapshot
    WHEN NOT EXISTS(SELECT 1 FROM publication_preservation_run r WHERE r.id=NEW.run_id
      AND r.status='pending' AND NEW.created_at>=r.created_at
      AND r.chunk_count=(SELECT count(*) FROM publication_preservation_chunk c
        WHERE c.run_id=r.id AND ((r.policy_version='{_REVIEWED}' AND
          (SELECT count(*) FROM publication_preservation_receipt q
            JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
              AND b.ordinal=q.ordinal AND b.sequence=q.sequence
            WHERE q.run_id=c.run_id AND q.ordinal=c.ordinal) IN (2,4))
          OR (r.policy_version='{_AUTHORING_ONLY}' AND
          (SELECT count(*) FROM publication_preservation_receipt q
            JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
              AND b.ordinal=q.ordinal AND b.sequence=q.sequence
            WHERE q.run_id=c.run_id AND q.ordinal=c.ordinal)=1))))
    BEGIN SELECT RAISE(ABORT,'preserved_snapshot_incomplete'); END""",
    f"""CREATE TRIGGER preserved_wiki_snapshot_body_binding BEFORE INSERT ON preserved_wiki_snapshot_body
    WHEN NOT EXISTS(SELECT 1 FROM preserved_wiki_snapshot s
      JOIN publication_preservation_run r ON r.id=s.run_id AND r.status='pending'
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.body_retention_status='present'
      JOIN extraction_snapshot_body x ON x.snapshot_id=e.id AND x.artifact_id=r.artifact_id
      WHERE s.id=NEW.snapshot_id AND instr(NEW.content_md,char(13))=0
        AND s.content_size_bytes=length(CAST(NEW.content_md AS BLOB))
        AND s.manifest_size_bytes=length(CAST(NEW.manifest_json AS BLOB))
        AND json_extract(NEW.manifest_json,'$.policy_version')=r.policy_version
        AND ((r.policy_version='{_REVIEWED}'
              AND json_type(NEW.manifest_json,'$.all_extracted_chunks_reviewed')='true')
          OR (r.policy_version='{_AUTHORING_ONLY}'
              AND json_type(NEW.manifest_json,'$.all_extracted_chunks_authored')='true'
              AND json_type(NEW.manifest_json,'$.all_extracted_chunks_reviewed')='false'
              AND json_type(NEW.manifest_json,'$.semantic_review_performed')='false'
              AND json_type(NEW.manifest_json,'$.semantic_completeness_proven')='false'
              AND json_type(NEW.manifest_json,'$.unseen_visual_content_reviewed')='false'
              AND json_extract(NEW.manifest_json,'$.validation_mode')='source_bound_authoring_only'
              AND json_array_length(NEW.manifest_json,'$.chunks')=r.chunk_count
              AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.chunks') c
                WHERE json_type(c.value,'$.review') IS NOT 'null'
                  OR json_array_length(c.value,'$.receipts') IS NOT 1
                  OR json_extract(c.value,'$.receipts[0].stage') IS NOT 'inventory'
                  OR json_extract(c.value,'$.receipts[0].validation_state') IS NOT 'validated'))))
    BEGIN SELECT RAISE(ABORT,'preserved_snapshot_body_binding'); END""",
    f"""CREATE TRIGGER preservation_chunk_success BEFORE UPDATE OF state ON durable_job
    WHEN NEW.job_type='publication_preservation.author_chunk' AND NEW.handler_version='v1'
      AND NEW.state='succeeded' AND NOT EXISTS(SELECT 1 FROM publication_preservation_chunk_work w
        JOIN publication_preservation_chunk c ON c.run_id=w.run_id AND c.ordinal=w.ordinal
        JOIN publication_preservation_run r ON r.id=w.run_id
        WHERE w.durable_job_id=NEW.id
          AND json_extract(NEW.result_json,'$.schema_version')=1
          AND json_extract(NEW.result_json,'$.run_id')=w.run_id
          AND json_extract(NEW.result_json,'$.ordinal')=w.ordinal
          AND json_extract(NEW.result_json,'$.chunk_id')=c.chunk_id
          AND json_extract(NEW.result_json,'$.receipt_count')=(SELECT count(*)
            FROM publication_preservation_receipt q
            JOIN publication_preservation_receipt_body b ON b.run_id=q.run_id
              AND b.ordinal=q.ordinal AND b.sequence=q.sequence
            WHERE q.run_id=w.run_id AND q.ordinal=w.ordinal)
          AND ((r.policy_version='{_REVIEWED}'
                AND json_extract(NEW.result_json,'$.receipt_count') IN (2,4))
            OR (r.policy_version='{_AUTHORING_ONLY}'
                AND json_extract(NEW.result_json,'$.receipt_count')=1)))
    BEGIN SELECT RAISE(ABORT,'Preservation chunk success lacks validated receipts'); END""",
    _AUTHORING_ONLY_SELECTION,
)


V021_POSTCONDITION_QUERIES = (
    """SELECT 'authoring-only successor triggers missing' WHERE 5<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND name IN (
        'publication_preservation_receipt_binding',
        'preserved_wiki_snapshot_binding',
        'preserved_wiki_snapshot_body_binding',
        'preservation_chunk_success',
        'preserved_publication_selection_binding'))""",
)


__all__ = ["V021_POSTCONDITION_QUERIES", "V021_STATEMENTS"]
