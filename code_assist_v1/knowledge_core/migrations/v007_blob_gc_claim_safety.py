"""Forward-only P5.6 blob-GC claim and reference race guards.

Versions 1 through 6 remain frozen.  Version 7 makes a claimed/retry/dead GC
candidate an insertion fence for every canonical blob-body reference, proves
zero references and backup quiescence at claim/finalize, and prevents a new
backup from entering its manifest window while physical deletion is claimed.
"""

from __future__ import annotations


_REFERENCES = """
EXISTS (SELECT 1 FROM artifact_blob_body WHERE content_sha256={sha})
OR EXISTS (SELECT 1 FROM extraction_snapshot_body WHERE content_blob_hash={sha})
OR EXISTS (SELECT 1 FROM knowledge_revision_body WHERE content_blob_hash={sha})
OR EXISTS (SELECT 1 FROM change_proposal WHERE proposed_content_blob_hash={sha})
""".strip()


def _body_fence(name: str, table: str, expression: str) -> str:
    return f"""
CREATE TRIGGER {name}
BEFORE INSERT ON {table}
FOR EACH ROW
WHEN {expression} IS NOT NULL AND EXISTS (
  SELECT 1 FROM blob_gc_candidate c
  WHERE c.blob_sha256={expression} AND c.state IN ('claimed','retry','dead')
)
BEGIN
  SELECT RAISE(ABORT, 'BLOB_GC_REFERENCE_FENCED');
END
""".strip()


V007_STATEMENTS: tuple[str, ...] = (
    _body_fence(
        "trg_artifact_blob_body_gc_fence_insert",
        "artifact_blob_body",
        "NEW.content_sha256",
    ),
    _body_fence(
        "trg_extraction_snapshot_body_gc_fence_insert",
        "extraction_snapshot_body",
        "NEW.content_blob_hash",
    ),
    _body_fence(
        "trg_knowledge_revision_body_gc_fence_insert",
        "knowledge_revision_body",
        "NEW.content_blob_hash",
    ),
    _body_fence(
        "trg_change_proposal_gc_fence_insert",
        "change_proposal",
        "NEW.proposed_content_blob_hash",
    ),
    f"""
CREATE TRIGGER trg_blob_gc_candidate_reference_insert
BEFORE INSERT ON blob_gc_candidate
FOR EACH ROW
WHEN {_REFERENCES.format(sha='NEW.blob_sha256')}
BEGIN
  SELECT RAISE(ABORT, 'BLOB_GC_REFERENCE_PRESENT');
END
""".strip(),
    f"""
CREATE TRIGGER trg_blob_gc_candidate_claim_guard
BEFORE UPDATE ON blob_gc_candidate
FOR EACH ROW
WHEN NEW.state='claimed' AND OLD.state<>'claimed' AND (
  CAST(unixepoch('subsec') * 1000 AS INTEGER)<OLD.delete_after
  OR {_REFERENCES.format(sha='OLD.blob_sha256')}
  OR EXISTS (SELECT 1 FROM backup_run WHERE state IN ('preparing','copying'))
  OR NOT EXISTS (
    SELECT 1 FROM blob_object b
    WHERE b.sha256=OLD.blob_sha256 AND b.verification_status='verified'
  )
)
BEGIN
  SELECT RAISE(ABORT, 'BLOB_GC_CLAIM_INELIGIBLE');
END
""".strip(),
    f"""
CREATE TRIGGER trg_blob_gc_candidate_finalize_delete
BEFORE DELETE ON blob_gc_candidate
FOR EACH ROW
WHEN NOT (
  (OLD.state='quarantined' AND ({_REFERENCES.format(sha='OLD.blob_sha256')}))
  OR
  (OLD.state='claimed'
    AND NOT ({_REFERENCES.format(sha='OLD.blob_sha256')})
    AND NOT EXISTS (SELECT 1 FROM backup_run WHERE state IN ('preparing','copying')))
)
BEGIN
  SELECT RAISE(ABORT, 'BLOB_GC_FINALIZE_INELIGIBLE');
END
""".strip(),
    """
CREATE TRIGGER trg_backup_run_gc_claim_insert
BEFORE INSERT ON backup_run
FOR EACH ROW
WHEN NEW.state IN ('preparing','copying')
 AND EXISTS (SELECT 1 FROM blob_gc_candidate WHERE state='claimed')
BEGIN
  SELECT RAISE(ABORT, 'BLOB_GC_BACKUP_RACE');
END
""".strip(),
)


_TRIGGER_NAMES = (
    "trg_artifact_blob_body_gc_fence_insert",
    "trg_extraction_snapshot_body_gc_fence_insert",
    "trg_knowledge_revision_body_gc_fence_insert",
    "trg_change_proposal_gc_fence_insert",
    "trg_blob_gc_candidate_reference_insert",
    "trg_blob_gc_candidate_claim_guard",
    "trg_blob_gc_candidate_finalize_delete",
    "trg_backup_run_gc_claim_insert",
)


V007_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'blob_gc_claim_safety_triggers_missing'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_artifact_blob_body_gc_fence_insert',
    'trg_extraction_snapshot_body_gc_fence_insert',
    'trg_knowledge_revision_body_gc_fence_insert',
    'trg_change_proposal_gc_fence_insert',
    'trg_blob_gc_candidate_reference_insert',
    'trg_blob_gc_candidate_claim_guard',
    'trg_blob_gc_candidate_finalize_delete',
    'trg_backup_run_gc_claim_insert'
  )
)<>8
""".strip(),
    """
SELECT 'blob_gc_claim_safety_trigger_shape_invalid'
WHERE EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_artifact_blob_body_gc_fence_insert',
    'trg_extraction_snapshot_body_gc_fence_insert',
    'trg_knowledge_revision_body_gc_fence_insert',
    'trg_change_proposal_gc_fence_insert'
  ) AND instr(sql,'BLOB_GC_REFERENCE_FENCED')=0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema WHERE type='trigger'
    AND name='trg_blob_gc_candidate_claim_guard'
    AND instr(sql,'BLOB_GC_CLAIM_INELIGIBLE')>0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema WHERE type='trigger'
    AND name='trg_blob_gc_candidate_finalize_delete'
    AND instr(sql,'BLOB_GC_FINALIZE_INELIGIBLE')>0
)
""".strip(),
)


__all__ = ["V007_POSTCONDITION_QUERIES", "V007_STATEMENTS"]
