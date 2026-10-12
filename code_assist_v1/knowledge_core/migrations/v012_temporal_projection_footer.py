"""Forward-only v12 temporal provenance and projection-v2 trust anchors.

Versions 1 through 11 remain byte-identical.  This migration adds one
independent per-space temporal generation, append-only revision assertion and
verification streams with CAS-protected heads, and an append-only manifest-v2
ledger used as the only local trust root for footer stripping/re-import.
"""

from __future__ import annotations


def _uuid(column: str) -> str:
    return f"""length({column})=36 AND substr({column},9,1)='-'
        AND substr({column},14,1)='-' AND substr({column},19,1)='-'
        AND substr({column},24,1)='-'
        AND length(replace({column},'-',''))=32
        AND replace({column},'-','') NOT GLOB '*[^0-9a-f]*'
        AND {column}=lower({column})"""


def _user(column: str) -> str:
    return f"""length({column}) BETWEEN 1 AND 64
        AND {column}=lower(trim({column}))
        AND {column} NOT GLOB '*[^a-z0-9._-]*'"""


def _sha(column: str) -> str:
    return f"""length({column})=64 AND {column}=lower({column})
        AND {column} NOT GLOB '*[^0-9a-f]*'"""


V012_STATEMENTS: tuple[str, ...] = (
    """
ALTER TABLE knowledge_space
ADD COLUMN temporal_generation INTEGER NOT NULL DEFAULT 0
CHECK(temporal_generation>=0)
""".strip(),
    f"""
CREATE TABLE revision_temporal_assertion(
    event_sequence INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE CHECK({_uuid('id')}),
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT NOT NULL CHECK({_uuid('item_id')})
        REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    revision_id TEXT NOT NULL CHECK({_uuid('revision_id')})
        REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    assertion_kind TEXT NOT NULL CHECK(
      assertion_kind IN ('source_modified','information_effective')
    ),
    event_kind TEXT NOT NULL CHECK(event_kind IN ('assert','revoke')),
    value_precision TEXT CHECK(value_precision IN ('instant','date')),
    instant_value_ms INTEGER CHECK(instant_value_ms IS NULL OR instant_value_ms>=0),
    date_value TEXT CHECK(
      date_value IS NULL OR (
        length(date_value)=10 AND substr(date_value,5,1)='-'
        AND substr(date_value,8,1)='-'
        AND date_value NOT GLOB '*[^0-9-]*'
        AND CAST(substr(date_value,6,2) AS INTEGER) BETWEEN 1 AND 12
        AND CAST(substr(date_value,9,2) AS INTEGER) BETWEEN 1 AND CASE
          CAST(substr(date_value,6,2) AS INTEGER)
          WHEN 2 THEN CASE
            WHEN CAST(substr(date_value,1,4) AS INTEGER)%400=0
              OR (CAST(substr(date_value,1,4) AS INTEGER)%4=0
                  AND CAST(substr(date_value,1,4) AS INTEGER)%100<>0)
            THEN 29 ELSE 28 END
          WHEN 4 THEN 30 WHEN 6 THEN 30 WHEN 9 THEN 30 WHEN 11 THEN 30
          ELSE 31 END
      )
    ),
    source_timezone TEXT CHECK(
      source_timezone IS NULL OR length(trim(source_timezone)) BETWEEN 1 AND 120
    ),
    source_utc_offset_minutes INTEGER CHECK(
      source_utc_offset_minutes IS NULL
      OR source_utc_offset_minutes BETWEEN -840 AND 840
    ),
    origin TEXT NOT NULL CHECK(origin IN (
      'user','trusted_upstream','reviewed_legacy_manifest','system_import'
    )),
    evidence_sha256 TEXT NOT NULL CHECK({_sha('evidence_sha256')}),
    evidence_reference TEXT NOT NULL CHECK(
      length(trim(evidence_reference)) BETWEEN 1 AND 1000
    ),
    predecessor_event_id TEXT CHECK(
      predecessor_event_id IS NULL OR ({_uuid('predecessor_event_id')})
    ) REFERENCES revision_temporal_assertion(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL CHECK(head_generation>=1),
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    temporal_summary_sha256 TEXT NOT NULL CHECK({_sha('temporal_summary_sha256')}),
    audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    committed_at INTEGER NOT NULL CHECK(committed_at>=0),
    UNIQUE(revision_id,assertion_kind,head_generation),
    CHECK((head_generation=1)=(predecessor_event_id IS NULL)),
    CHECK(
      (event_kind='assert' AND value_precision='instant'
       AND instant_value_ms IS NOT NULL AND date_value IS NULL
       AND source_timezone IS NOT NULL
       AND source_utc_offset_minutes IS NOT NULL)
      OR
      (event_kind='assert' AND value_precision='date'
       AND instant_value_ms IS NULL AND date_value IS NOT NULL
       AND source_timezone='Asia/Seoul'
       AND source_utc_offset_minutes IS NULL)
      OR
      (event_kind='revoke' AND value_precision IS NULL
       AND instant_value_ms IS NULL AND date_value IS NULL
       AND source_timezone IS NULL AND source_utc_offset_minutes IS NULL)
    )
) STRICT
""".strip(),
    """
CREATE INDEX ix_temporal_assertion_revision_history
ON revision_temporal_assertion(revision_id,event_sequence,id)
""".strip(),
    """
CREATE INDEX ix_temporal_assertion_space_commit
ON revision_temporal_assertion(space_id,committed_at,audit_event_id,id)
""".strip(),
    f"""
CREATE TABLE revision_temporal_head(
    revision_id TEXT NOT NULL CHECK({_uuid('revision_id')})
        REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    assertion_kind TEXT NOT NULL CHECK(
      assertion_kind IN ('source_modified','information_effective')
    ),
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT NOT NULL CHECK({_uuid('item_id')})
        REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_event_id TEXT NOT NULL UNIQUE CHECK({_uuid('head_event_id')})
        REFERENCES revision_temporal_assertion(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL CHECK(head_generation>=1),
    state TEXT NOT NULL CHECK(state IN ('asserted','revoked')),
    temporal_summary_sha256 TEXT NOT NULL CHECK({_sha('temporal_summary_sha256')}),
    updated_at INTEGER NOT NULL CHECK(updated_at>=0),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    PRIMARY KEY(revision_id,assertion_kind)
) STRICT
""".strip(),
    """
CREATE INDEX ix_temporal_head_item_revision
ON revision_temporal_head(space_id,item_id,revision_id,assertion_kind)
""".strip(),
    f"""
CREATE TABLE revision_verification_attestation(
    event_sequence INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE CHECK({_uuid('id')}),
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT NOT NULL CHECK({_uuid('item_id')})
        REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    revision_id TEXT NOT NULL CHECK({_uuid('revision_id')})
        REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('attest','revoke')),
    temporal_summary_sha256 TEXT NOT NULL CHECK({_sha('temporal_summary_sha256')}),
    verifier_actor_kind TEXT NOT NULL CHECK(verifier_actor_kind='user'),
    verifier_user_id TEXT NOT NULL CHECK({_user('verifier_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    authority_proof_sha256 TEXT NOT NULL CHECK({_sha('authority_proof_sha256')}),
    method TEXT NOT NULL CHECK(length(trim(method)) BETWEEN 1 AND 120),
    evidence_sha256 TEXT NOT NULL CHECK({_sha('evidence_sha256')}),
    evidence_reference TEXT NOT NULL CHECK(
      length(trim(evidence_reference)) BETWEEN 1 AND 1000
    ),
    note TEXT CHECK(note IS NULL OR length(trim(note)) BETWEEN 1 AND 2000),
    subject_verified_at_ms INTEGER CHECK(
      subject_verified_at_ms IS NULL OR subject_verified_at_ms>=0
    ),
    predecessor_event_id TEXT CHECK(
      predecessor_event_id IS NULL OR ({_uuid('predecessor_event_id')})
    ) REFERENCES revision_verification_attestation(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL CHECK(head_generation>=1),
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    attested_at INTEGER NOT NULL CHECK(attested_at>=0),
    UNIQUE(revision_id,head_generation),
    CHECK((head_generation=1)=(predecessor_event_id IS NULL))
) STRICT
""".strip(),
    """
CREATE INDEX ix_verification_revision_history
ON revision_verification_attestation(revision_id,event_sequence,id)
""".strip(),
    """
CREATE INDEX ix_verification_space_commit
ON revision_verification_attestation(space_id,attested_at,audit_event_id,id)
""".strip(),
    f"""
CREATE TABLE revision_verification_head(
    revision_id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('revision_id')})
        REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT NOT NULL CHECK({_uuid('item_id')})
        REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_event_id TEXT NOT NULL UNIQUE CHECK({_uuid('head_event_id')})
        REFERENCES revision_verification_attestation(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    head_generation INTEGER NOT NULL CHECK(head_generation>=1),
    state TEXT NOT NULL CHECK(state IN ('attested','revoked')),
    temporal_summary_sha256 TEXT NOT NULL CHECK({_sha('temporal_summary_sha256')}),
    updated_at INTEGER NOT NULL CHECK(updated_at>=0),
    row_version INTEGER NOT NULL CHECK(row_version>=1)
) STRICT
""".strip(),
    f"""
CREATE TABLE projection_manifest_v2_ledger(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    projection_name TEXT NOT NULL CHECK(projection_name='markdown_v2'),
    content_generation INTEGER NOT NULL CHECK(content_generation>=0),
    graph_generation INTEGER NOT NULL CHECK(graph_generation>=0),
    temporal_generation INTEGER NOT NULL CHECK(temporal_generation>=0),
    snapshot_as_of_at INTEGER NOT NULL CHECK(snapshot_as_of_at>=0),
    snapshot_as_of_event_id INTEGER NOT NULL CHECK(snapshot_as_of_event_id>=0),
    revision_count INTEGER NOT NULL CHECK(revision_count>=0),
    manifest_sha256 TEXT NOT NULL UNIQUE CHECK({_sha('manifest_sha256')}),
    manifest_json TEXT NOT NULL CHECK(
      json_valid(manifest_json) AND json_type(manifest_json)='object'
    ),
    projection_root_identity_sha256 TEXT NOT NULL
        CHECK({_sha('projection_root_identity_sha256')}),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    UNIQUE(space_id,content_generation,graph_generation,temporal_generation)
) STRICT
""".strip(),
    """
CREATE INDEX ix_projection_manifest_v2_space_generation
ON projection_manifest_v2_ledger(
  space_id,content_generation,graph_generation,temporal_generation,id
)
""".strip(),
    """
CREATE TRIGGER trg_temporal_assertion_validity_insert
BEFORE INSERT ON revision_temporal_assertion
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_item i
  JOIN knowledge_revision r ON r.id=NEW.revision_id AND r.item_id=i.id
  JOIN knowledge_space s ON s.id=i.space_id
  JOIN app_user u ON u.id=NEW.actor_user_id
  WHERE i.id=NEW.item_id AND i.space_id=NEW.space_id
    AND i.current_revision_id=NEW.revision_id
    AND i.lifecycle_status='active' AND i.curation_status='published'
    AND s.status='active' AND u.account_status='active'
) OR NOT (
  (NEW.head_generation=1 AND NOT EXISTS (
    SELECT 1 FROM revision_temporal_head h
    WHERE h.revision_id=NEW.revision_id
      AND h.assertion_kind=NEW.assertion_kind
  ))
  OR
  (NEW.head_generation>1 AND EXISTS (
    SELECT 1 FROM revision_temporal_head h
    WHERE h.revision_id=NEW.revision_id
      AND h.assertion_kind=NEW.assertion_kind
      AND h.head_event_id=NEW.predecessor_event_id
      AND h.head_generation=NEW.head_generation-1
  ))
) OR NOT EXISTS (
  SELECT 1 FROM audit_event a JOIN outbox_event o
  WHERE a.id=NEW.audit_event_id AND o.id=NEW.outbox_event_id
    AND a.actor_kind='user' AND a.actor_user_id=NEW.actor_user_id
    AND a.space_id=NEW.space_id AND a.item_id=NEW.item_id
    AND a.new_revision_id=NEW.revision_id
    AND a.operation_id=NEW.operation_id
    AND a.action=CASE NEW.event_kind
      WHEN 'assert' THEN 'temporal.assert' ELSE 'temporal.revoke' END
    AND o.space_id=NEW.space_id AND o.aggregate_id=NEW.item_id
    AND o.event_type=CASE NEW.event_kind
      WHEN 'assert' THEN 'knowledge.temporal.asserted'
      ELSE 'knowledge.temporal.revoked' END
)
BEGIN SELECT RAISE(ABORT,'TEMPORAL_ASSERTION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_temporal_assertion_history_update
BEFORE UPDATE ON revision_temporal_assertion
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_temporal_assertion_history_delete
BEFORE DELETE ON revision_temporal_assertion
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_temporal_head_validity_insert
BEFORE INSERT ON revision_temporal_head
WHEN NEW.row_version<>1 OR NOT EXISTS (
  SELECT 1 FROM revision_temporal_assertion e
  WHERE e.id=NEW.head_event_id AND e.revision_id=NEW.revision_id
    AND e.assertion_kind=NEW.assertion_kind AND e.space_id=NEW.space_id
    AND e.item_id=NEW.item_id AND e.head_generation=NEW.head_generation
    AND e.temporal_summary_sha256=NEW.temporal_summary_sha256
    AND NEW.state=CASE e.event_kind WHEN 'assert' THEN 'asserted' ELSE 'revoked' END
)
BEGIN SELECT RAISE(ABORT,'TEMPORAL_HEAD_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_temporal_head_validity_update
BEFORE UPDATE ON revision_temporal_head
WHEN NEW.revision_id IS NOT OLD.revision_id
 OR NEW.assertion_kind IS NOT OLD.assertion_kind
 OR NEW.space_id IS NOT OLD.space_id OR NEW.item_id IS NOT OLD.item_id
 OR NEW.head_generation<>OLD.head_generation+1
 OR NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
 OR NOT EXISTS (
  SELECT 1 FROM revision_temporal_assertion e
  WHERE e.id=NEW.head_event_id AND e.revision_id=NEW.revision_id
    AND e.assertion_kind=NEW.assertion_kind AND e.space_id=NEW.space_id
    AND e.item_id=NEW.item_id AND e.predecessor_event_id=OLD.head_event_id
    AND e.head_generation=NEW.head_generation
    AND e.temporal_summary_sha256=NEW.temporal_summary_sha256
    AND NEW.state=CASE e.event_kind WHEN 'assert' THEN 'asserted' ELSE 'revoked' END
)
BEGIN SELECT RAISE(ABORT,'TEMPORAL_HEAD_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_temporal_head_history_delete
BEFORE DELETE ON revision_temporal_head
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_attestation_validity_insert
BEFORE INSERT ON revision_verification_attestation
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_item i
  JOIN knowledge_revision r ON r.id=NEW.revision_id AND r.item_id=i.id
  JOIN knowledge_space s ON s.id=i.space_id
  JOIN app_user u ON u.id=NEW.verifier_user_id
  WHERE i.id=NEW.item_id AND i.space_id=NEW.space_id
    AND i.current_revision_id=NEW.revision_id
    AND i.lifecycle_status='active' AND i.curation_status='published'
    AND s.status='active' AND u.account_status='active'
) OR NOT (
  (NEW.head_generation=1 AND NOT EXISTS (
    SELECT 1 FROM revision_verification_head h
    WHERE h.revision_id=NEW.revision_id
  ))
  OR
  (NEW.head_generation>1 AND EXISTS (
    SELECT 1 FROM revision_verification_head h
    WHERE h.revision_id=NEW.revision_id
      AND h.head_event_id=NEW.predecessor_event_id
      AND h.head_generation=NEW.head_generation-1
  ))
) OR NOT EXISTS (
  SELECT 1 FROM audit_event a JOIN outbox_event o
  WHERE a.id=NEW.audit_event_id AND o.id=NEW.outbox_event_id
    AND a.actor_kind='user' AND a.actor_user_id=NEW.verifier_user_id
    AND a.space_id=NEW.space_id AND a.item_id=NEW.item_id
    AND a.new_revision_id=NEW.revision_id
    AND a.operation_id=NEW.operation_id
    AND a.action=CASE NEW.event_kind
      WHEN 'attest' THEN 'verification.attest' ELSE 'verification.revoke' END
    AND o.space_id=NEW.space_id AND o.aggregate_id=NEW.item_id
    AND o.event_type=CASE NEW.event_kind
      WHEN 'attest' THEN 'knowledge.verification.attested'
      ELSE 'knowledge.verification.revoked' END
)
BEGIN SELECT RAISE(ABORT,'VERIFICATION_ATTESTATION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_attestation_history_update
BEFORE UPDATE ON revision_verification_attestation
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_attestation_history_delete
BEFORE DELETE ON revision_verification_attestation
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_head_validity_insert
BEFORE INSERT ON revision_verification_head
WHEN NEW.row_version<>1 OR NOT EXISTS (
  SELECT 1 FROM revision_verification_attestation e
  WHERE e.id=NEW.head_event_id AND e.revision_id=NEW.revision_id
    AND e.space_id=NEW.space_id AND e.item_id=NEW.item_id
    AND e.head_generation=NEW.head_generation
    AND e.temporal_summary_sha256=NEW.temporal_summary_sha256
    AND NEW.state=CASE e.event_kind WHEN 'attest' THEN 'attested' ELSE 'revoked' END
)
BEGIN SELECT RAISE(ABORT,'VERIFICATION_HEAD_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_head_validity_update
BEFORE UPDATE ON revision_verification_head
WHEN NEW.revision_id IS NOT OLD.revision_id
 OR NEW.space_id IS NOT OLD.space_id OR NEW.item_id IS NOT OLD.item_id
 OR NEW.head_generation<>OLD.head_generation+1
 OR NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
 OR NOT EXISTS (
  SELECT 1 FROM revision_verification_attestation e
  WHERE e.id=NEW.head_event_id AND e.revision_id=NEW.revision_id
    AND e.space_id=NEW.space_id AND e.item_id=NEW.item_id
    AND e.predecessor_event_id=OLD.head_event_id
    AND e.head_generation=NEW.head_generation
    AND e.temporal_summary_sha256=NEW.temporal_summary_sha256
    AND NEW.state=CASE e.event_kind WHEN 'attest' THEN 'attested' ELSE 'revoked' END
)
BEGIN SELECT RAISE(ABORT,'VERIFICATION_HEAD_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_verification_head_history_delete
BEFORE DELETE ON revision_verification_head
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_projection_manifest_v2_validity_insert
BEFORE INSERT ON projection_manifest_v2_ledger
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_space s
  WHERE s.id=NEW.space_id AND s.status='active'
    AND s.generation=NEW.content_generation
    AND s.temporal_generation=NEW.temporal_generation
) OR NEW.graph_generation<>0
BEGIN SELECT RAISE(ABORT,'PROJECTION_MANIFEST_V2_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_projection_manifest_v2_history_update
BEFORE UPDATE ON projection_manifest_v2_ledger
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_projection_manifest_v2_history_delete
BEFORE DELETE ON projection_manifest_v2_ledger
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
)


V012_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'v12_table_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN (
    'revision_temporal_assertion','revision_temporal_head',
    'revision_verification_attestation','revision_verification_head',
    'projection_manifest_v2_ledger'
  )
)<>5
""".strip(),
    """
SELECT 'v12_index_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='index' AND name IN (
    'ix_temporal_assertion_revision_history','ix_temporal_assertion_space_commit',
    'ix_temporal_head_item_revision','ix_verification_revision_history',
    'ix_verification_space_commit','ix_projection_manifest_v2_space_generation'
  )
)<>6
""".strip(),
    """
SELECT 'v12_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name IN (
    'trg_temporal_assertion_validity_insert',
    'trg_temporal_assertion_history_update',
    'trg_temporal_assertion_history_delete',
    'trg_temporal_head_validity_insert','trg_temporal_head_validity_update',
    'trg_temporal_head_history_delete',
    'trg_verification_attestation_validity_insert',
    'trg_verification_attestation_history_update',
    'trg_verification_attestation_history_delete',
    'trg_verification_head_validity_insert',
    'trg_verification_head_validity_update',
    'trg_verification_head_history_delete',
    'trg_projection_manifest_v2_validity_insert',
    'trg_projection_manifest_v2_history_update',
    'trg_projection_manifest_v2_history_delete'
  )
)<>15
""".strip(),
    """
SELECT 'v12_temporal_generation_invalid'
WHERE EXISTS (SELECT 1 FROM knowledge_space WHERE temporal_generation<0)
""".strip(),
    """
SELECT 'v12_temporal_head_invalid'
WHERE EXISTS (
  SELECT 1 FROM revision_temporal_head h
  LEFT JOIN revision_temporal_assertion e ON e.id=h.head_event_id
  WHERE e.id IS NULL OR e.revision_id<>h.revision_id
    OR e.assertion_kind<>h.assertion_kind
    OR e.head_generation<>h.head_generation
    OR e.temporal_summary_sha256<>h.temporal_summary_sha256
)
OR EXISTS (
  SELECT 1 FROM revision_verification_head h
  LEFT JOIN revision_verification_attestation e ON e.id=h.head_event_id
  WHERE e.id IS NULL OR e.revision_id<>h.revision_id
    OR e.head_generation<>h.head_generation
    OR e.temporal_summary_sha256<>h.temporal_summary_sha256
)
""".strip(),
)


__all__ = ["V012_POSTCONDITION_QUERIES", "V012_STATEMENTS"]
