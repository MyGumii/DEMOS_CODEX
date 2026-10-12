"""Forward-only v10 aggregate graph, semantic automation, and reference schema."""

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


SEMANTIC_RELATION_TYPE_SEED_ROWS: tuple[tuple[object, ...], ...] = (
    ("contradicts", "semantic-rel-v1", "symmetric", 1, 0, 1),
    ("depends_on", "semantic-rel-v1", "directed", 1, 0, 1),
    ("example_of", "semantic-rel-v1", "directed", 1, 0, 1),
    ("extends", "semantic-rel-v1", "directed", 1, 0, 1),
    ("related", "semantic-rel-v1", "symmetric", 1, 0, 1),
    ("supports", "semantic-rel-v1", "directed", 1, 0, 1),
)


V010_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE semantic_relation_type(
    relation_type TEXT PRIMARY KEY NOT NULL CHECK(
        relation_type IN (
          'related','supports','contradicts','depends_on','extends','example_of'
        )
    ),
    vocabulary_version TEXT NOT NULL CHECK(vocabulary_version='semantic-rel-v1'),
    direction_policy TEXT NOT NULL CHECK(
        direction_policy IN ('symmetric','directed')
    ),
    active INTEGER NOT NULL CHECK(active=1),
    created_at INTEGER NOT NULL CHECK(created_at=0),
    row_version INTEGER NOT NULL CHECK(row_version=1)
) STRICT
""".strip(),
    """
INSERT INTO semantic_relation_type(
    relation_type,vocabulary_version,direction_policy,active,created_at,row_version
) VALUES
('related','semantic-rel-v1','symmetric',1,0,1),
('supports','semantic-rel-v1','directed',1,0,1),
('contradicts','semantic-rel-v1','symmetric',1,0,1),
('depends_on','semantic-rel-v1','directed',1,0,1),
('extends','semantic-rel-v1','directed',1,0,1),
('example_of','semantic-rel-v1','directed',1,0,1)
""".strip(),
    """
CREATE TRIGGER trg_semantic_relation_type_history_update
BEFORE UPDATE ON semantic_relation_type
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_relation_type_history_delete
BEFORE DELETE ON semantic_relation_type
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    "DROP TRIGGER trg_knowledge_edge_approval_insert",
    "DROP TRIGGER trg_knowledge_edge_validity_update",
    "DROP TRIGGER trg_knowledge_edge_history_delete",
    "DROP INDEX ux_edge_active_identity",
    "DROP INDEX ux_edge_approval_finding",
    "DROP INDEX ix_edge_reverse",
    "DROP INDEX ix_edge_revalidation",
    "ALTER TABLE knowledge_edge RENAME TO knowledge_edge_v10_old",
    f"""
CREATE TABLE knowledge_edge(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    space_id TEXT NOT NULL CHECK({_uuid('space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK({_uuid('source_item_id')}),
    target_item_id TEXT NOT NULL CHECK({_uuid('target_item_id')}),
    edge_type TEXT NOT NULL REFERENCES semantic_relation_type(relation_type)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    edge_status TEXT NOT NULL CHECK(edge_status IN ('active','inactive')),
    origin TEXT NOT NULL CHECK(
        origin IN ('manual','semantic_approved','semantic_auto')
    ),
    actor_kind TEXT NOT NULL DEFAULT 'user' CHECK(actor_kind IN ('user','system')),
    source_evidence_revision_id TEXT CHECK(
        source_evidence_revision_id IS NULL OR ({_uuid('source_evidence_revision_id')})
    ),
    target_evidence_revision_id TEXT CHECK(
        target_evidence_revision_id IS NULL OR ({_uuid('target_evidence_revision_id')})
    ),
    approval_finding_id TEXT CHECK(
        approval_finding_id IS NULL OR ({_uuid('approval_finding_id')})
    ) REFERENCES curation_finding(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    system_job_id TEXT CHECK(system_job_id IS NULL OR ({_uuid('system_job_id')}))
        REFERENCES durable_job(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    semantic_key TEXT CHECK(semantic_key IS NULL OR ({_sha('semantic_key')})),
    detector_generation INTEGER CHECK(
        detector_generation IS NULL OR detector_generation>=1
    ),
    model_provider TEXT CHECK(
        model_provider IS NULL OR length(trim(model_provider)) BETWEEN 1 AND 120
    ),
    model_name TEXT CHECK(
        model_name IS NULL OR length(trim(model_name)) BETWEEN 1 AND 160
    ),
    prompt_schema_version TEXT CHECK(
        prompt_schema_version IS NULL
        OR length(trim(prompt_schema_version)) BETWEEN 1 AND 80
    ),
    scoring_version TEXT CHECK(
        scoring_version IS NULL OR length(trim(scoring_version)) BETWEEN 1 AND 80
    ),
    confidence_ppm INTEGER CHECK(
        confidence_ppm IS NULL OR confidence_ppm BETWEEN 0 AND 1000000
    ),
    effect_key TEXT CHECK(
        effect_key IS NULL OR length(trim(effect_key)) BETWEEN 1 AND 300
    ),
    semantic_evidence_sha256 TEXT CHECK(
        semantic_evidence_sha256 IS NULL OR ({_sha('semantic_evidence_sha256')})
    ),
    source_evidence_span_sha256 TEXT CHECK(
        source_evidence_span_sha256 IS NULL
        OR ({_sha('source_evidence_span_sha256')})
    ),
    target_evidence_span_sha256 TEXT CHECK(
        target_evidence_span_sha256 IS NULL
        OR ({_sha('target_evidence_span_sha256')})
    ),
    source_evidence_location_json TEXT CHECK(
        source_evidence_location_json IS NULL OR (
          json_valid(source_evidence_location_json)
          AND json_type(source_evidence_location_json)='object'
        )
    ),
    target_evidence_location_json TEXT CHECK(
        target_evidence_location_json IS NULL OR (
          json_valid(target_evidence_location_json)
          AND json_type(target_evidence_location_json)='object'
        )
    ),
    audit_event_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    evidence_json TEXT NOT NULL DEFAULT '{{}}' CHECK(
        json_valid(evidence_json) AND json_type(evidence_json)='object'
    ),
    needs_revalidation INTEGER NOT NULL DEFAULT 0 CHECK(needs_revalidation IN (0,1)),
    created_by TEXT CHECK(created_by IS NULL OR ({_user('created_by')}))
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    updated_by TEXT CHECK(updated_by IS NULL OR ({_user('updated_by')}))
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=0),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    FOREIGN KEY(source_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(source_evidence_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_evidence_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK(source_item_id<>target_item_id),
    CHECK((source_evidence_revision_id IS NULL)=(target_evidence_revision_id IS NULL)),
    CHECK(
      (origin='manual' AND actor_kind='user' AND created_by IS NOT NULL
       AND updated_by IS NOT NULL AND approval_finding_id IS NULL
       AND system_job_id IS NULL AND semantic_key IS NULL
       AND detector_generation IS NULL AND model_provider IS NULL
       AND model_name IS NULL AND prompt_schema_version IS NULL
       AND scoring_version IS NULL AND confidence_ppm IS NULL
       AND effect_key IS NULL AND semantic_evidence_sha256 IS NULL
       AND source_evidence_span_sha256 IS NULL
       AND target_evidence_span_sha256 IS NULL
       AND source_evidence_location_json IS NULL
       AND target_evidence_location_json IS NULL
       AND audit_event_id IS NULL AND outbox_event_id IS NULL)
      OR
      (origin='semantic_approved' AND actor_kind='user' AND created_by IS NOT NULL
       AND updated_by IS NOT NULL AND approval_finding_id IS NOT NULL
       AND source_evidence_revision_id IS NOT NULL AND system_job_id IS NULL
       AND semantic_key IS NULL AND detector_generation IS NULL
       AND model_provider IS NULL AND model_name IS NULL
       AND prompt_schema_version IS NULL AND scoring_version IS NULL
       AND confidence_ppm IS NULL AND effect_key IS NULL
       AND semantic_evidence_sha256 IS NULL
       AND source_evidence_span_sha256 IS NULL
       AND target_evidence_span_sha256 IS NULL
       AND source_evidence_location_json IS NULL
       AND target_evidence_location_json IS NULL
       AND audit_event_id IS NULL AND outbox_event_id IS NULL)
      OR
      (origin='semantic_auto' AND actor_kind='system' AND created_by IS NULL
       AND updated_by IS NULL AND approval_finding_id IS NULL
       AND system_job_id IS NOT NULL AND source_evidence_revision_id IS NOT NULL
       AND semantic_key IS NOT NULL AND detector_generation IS NOT NULL
       AND model_provider IS NOT NULL AND model_name IS NOT NULL
       AND prompt_schema_version IS NOT NULL AND scoring_version IS NOT NULL
       AND confidence_ppm IS NOT NULL AND effect_key IS NOT NULL
       AND semantic_evidence_sha256 IS NOT NULL
       AND source_evidence_span_sha256 IS NOT NULL
       AND target_evidence_span_sha256 IS NOT NULL
       AND source_evidence_location_json IS NOT NULL
       AND target_evidence_location_json IS NOT NULL
       AND audit_event_id IS NOT NULL AND outbox_event_id IS NOT NULL)
    )
) STRICT
""".strip(),
    """
INSERT INTO knowledge_edge(
    id,space_id,source_item_id,target_item_id,edge_type,edge_status,origin,
    actor_kind,source_evidence_revision_id,target_evidence_revision_id,
    approval_finding_id,system_job_id,semantic_key,detector_generation,
    model_provider,model_name,prompt_schema_version,scoring_version,
    confidence_ppm,effect_key,semantic_evidence_sha256,
    source_evidence_span_sha256,target_evidence_span_sha256,
    source_evidence_location_json,target_evidence_location_json,evidence_json,
    audit_event_id,outbox_event_id,needs_revalidation,created_by,updated_by,
    created_at,updated_at,row_version
)
SELECT id,space_id,source_item_id,target_item_id,edge_type,edge_status,origin,
       'user',source_evidence_revision_id,target_evidence_revision_id,
       approval_finding_id,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
       NULL,NULL,NULL,NULL,evidence_json,NULL,NULL,needs_revalidation,
       created_by,updated_by,created_at,updated_at,row_version
FROM knowledge_edge_v10_old
""".strip(),
    "DROP TABLE knowledge_edge_v10_old",
    """
CREATE UNIQUE INDEX ux_edge_active_identity
ON knowledge_edge(space_id,source_item_id,target_item_id,edge_type)
WHERE edge_status='active'
""".strip(),
    """
CREATE UNIQUE INDEX ux_edge_approval_finding
ON knowledge_edge(approval_finding_id)
WHERE approval_finding_id IS NOT NULL
""".strip(),
    """
CREATE INDEX ix_edge_reverse
ON knowledge_edge(space_id,target_item_id,edge_status)
""".strip(),
    """
CREATE INDEX ix_edge_revalidation
ON knowledge_edge(space_id,needs_revalidation,edge_status,id)
""".strip(),
    """
CREATE UNIQUE INDEX ux_edge_auto_effect_key
ON knowledge_edge(effect_key) WHERE origin='semantic_auto'
""".strip(),
    """
CREATE UNIQUE INDEX ux_edge_auto_semantic_key_active
ON knowledge_edge(space_id,semantic_key)
WHERE origin='semantic_auto' AND edge_status='active'
""".strip(),
    """
CREATE INDEX ix_edge_auto_job
ON knowledge_edge(system_job_id,edge_status,id)
WHERE system_job_id IS NOT NULL
""".strip(),
    """
CREATE TRIGGER trg_knowledge_edge_approval_insert
BEFORE INSERT ON knowledge_edge
WHEN NEW.needs_revalidation<>0
 OR (NEW.origin='semantic_approved' AND NOT EXISTS (
   SELECT 1 FROM curation_finding f
   WHERE f.id=NEW.approval_finding_id AND f.finding_kind='semantic_edge'
     AND f.risk_tier='T2' AND f.decision_mode='human'
     AND f.required_reviewer='space_editor' AND f.proposed_action='relation'
     AND f.status='applied' AND f.space_id=NEW.space_id
     AND f.source_item_id=NEW.source_item_id
     AND f.source_revision_id=NEW.source_evidence_revision_id
     AND f.target_item_id=NEW.target_item_id
     AND f.target_revision_id=NEW.target_evidence_revision_id
 ))
 OR (NEW.origin='semantic_auto' AND (
   NOT EXISTS (
     SELECT 1 FROM durable_job j
     WHERE j.id=NEW.system_job_id AND j.actor_kind='system'
       AND j.actor_user_id IS NULL AND j.job_type='semantic_edge.evaluate'
       AND j.effect_key=NEW.effect_key
   )
   OR NOT EXISTS (
     SELECT 1 FROM knowledge_item s JOIN knowledge_item t
     WHERE s.id=NEW.source_item_id AND t.id=NEW.target_item_id
       AND s.space_id=NEW.space_id AND t.space_id=NEW.space_id
       AND s.lifecycle_status='active' AND t.lifecycle_status='active'
       AND s.curation_status='published' AND t.curation_status='published'
       AND s.current_revision_id=NEW.source_evidence_revision_id
       AND t.current_revision_id=NEW.target_evidence_revision_id
   )
   OR NOT EXISTS (
     SELECT 1 FROM audit_event a
     WHERE a.id=NEW.audit_event_id AND a.outcome='committed'
       AND a.actor_kind='system' AND a.actor_user_id IS NULL
       AND a.space_id=NEW.space_id AND a.item_id=NEW.source_item_id
       AND a.base_revision_id=NEW.source_evidence_revision_id
       AND a.new_revision_id=NEW.target_evidence_revision_id
   )
   OR NOT EXISTS (
     SELECT 1 FROM outbox_event o
     WHERE o.id=NEW.outbox_event_id AND o.space_id=NEW.space_id
       AND o.aggregate_type='knowledge_edge' AND o.aggregate_id=NEW.id
       AND o.aggregate_version=NEW.row_version
       AND o.event_type='semantic.edge.activated'
   )
  ))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_EDGE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_knowledge_edge_validity_update
BEFORE UPDATE ON knowledge_edge
WHEN NEW.id IS NOT OLD.id OR NEW.space_id IS NOT OLD.space_id
 OR NEW.source_item_id IS NOT OLD.source_item_id
 OR NEW.target_item_id IS NOT OLD.target_item_id
 OR NEW.edge_type IS NOT OLD.edge_type OR NEW.origin IS NOT OLD.origin
 OR NEW.actor_kind IS NOT OLD.actor_kind
 OR NEW.source_evidence_revision_id IS NOT OLD.source_evidence_revision_id
 OR NEW.target_evidence_revision_id IS NOT OLD.target_evidence_revision_id
 OR NEW.approval_finding_id IS NOT OLD.approval_finding_id
 OR NEW.system_job_id IS NOT OLD.system_job_id
 OR NEW.semantic_key IS NOT OLD.semantic_key
 OR NEW.detector_generation IS NOT OLD.detector_generation
 OR NEW.model_provider IS NOT OLD.model_provider OR NEW.model_name IS NOT OLD.model_name
 OR NEW.prompt_schema_version IS NOT OLD.prompt_schema_version
 OR NEW.scoring_version IS NOT OLD.scoring_version
 OR NEW.confidence_ppm IS NOT OLD.confidence_ppm
 OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.semantic_evidence_sha256 IS NOT OLD.semantic_evidence_sha256
 OR NEW.source_evidence_span_sha256 IS NOT OLD.source_evidence_span_sha256
 OR NEW.target_evidence_span_sha256 IS NOT OLD.target_evidence_span_sha256
 OR NEW.source_evidence_location_json IS NOT OLD.source_evidence_location_json
 OR NEW.target_evidence_location_json IS NOT OLD.target_evidence_location_json
 OR NEW.audit_event_id IS NOT OLD.audit_event_id
 OR NEW.outbox_event_id IS NOT OLD.outbox_event_id
 OR NEW.evidence_json IS NOT OLD.evidence_json OR NEW.created_by IS NOT OLD.created_by
 OR NEW.created_at IS NOT OLD.created_at
 OR NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
 OR (NEW.edge_status='active' AND NEW.needs_revalidation<>0)
 OR NOT (
   (NEW.edge_status=OLD.edge_status
    OR (OLD.edge_status='active' AND NEW.edge_status='inactive')
    OR (OLD.edge_status='inactive' AND NEW.edge_status='active'))
   AND (NEW.needs_revalidation=OLD.needs_revalidation
    OR (OLD.needs_revalidation=0 AND NEW.needs_revalidation=1)
    OR (OLD.needs_revalidation=1 AND NEW.needs_revalidation=0))
 )
BEGIN SELECT RAISE(ABORT,'SEMANTIC_EDGE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_knowledge_edge_history_delete
BEFORE DELETE ON knowledge_edge
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE semantic_edge_intent_seed_run(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    cause_kind TEXT NOT NULL CHECK(cause_kind IN (
      'baseline_backfill','org_retarget','detector_reseed','lifecycle_reactivate'
    )),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('global','org_unit','item','wiki')),
    scope_id TEXT CHECK(scope_id IS NULL OR ({_uuid('scope_id')})),
    detector_generation INTEGER NOT NULL CHECK(detector_generation>=1),
    previous_detector_generation INTEGER CHECK(
      previous_detector_generation IS NULL OR previous_detector_generation>=1
    ),
    old_ancestor_space_id TEXT CHECK(
      old_ancestor_space_id IS NULL OR ({_uuid('old_ancestor_space_id')})
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    new_ancestor_space_id TEXT CHECK(
      new_ancestor_space_id IS NULL OR ({_uuid('new_ancestor_space_id')})
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    organization_event_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    detector_rollout_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    lifecycle_restore_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    snapshot_domain_high_water INTEGER NOT NULL CHECK(snapshot_domain_high_water>=0),
    snapshot_audit_high_water INTEGER NOT NULL CHECK(snapshot_audit_high_water>=0),
    cursor_updated_at INTEGER NOT NULL DEFAULT 0 CHECK(cursor_updated_at>=0),
    cursor_revision_id TEXT CHECK(
      cursor_revision_id IS NULL OR ({_uuid('cursor_revision_id')})
    ) REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed')),
    fence INTEGER NOT NULL CHECK(fence>=1),
    scanned_count INTEGER NOT NULL DEFAULT 0 CHECK(scanned_count>=0),
    intent_count INTEGER NOT NULL DEFAULT 0 CHECK(intent_count>=0),
    result_sha256 TEXT CHECK(result_sha256 IS NULL OR ({_sha('result_sha256')})),
    error_json TEXT CHECK(error_json IS NULL OR (
      json_valid(error_json) AND json_type(error_json)='object'
    )),
    created_audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at>=created_at),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    CHECK((scope_kind='global')=(scope_id IS NULL)),
    CHECK(
      (cause_kind='baseline_backfill' AND scope_kind='global'
       AND previous_detector_generation IS NULL
       AND old_ancestor_space_id IS NULL AND new_ancestor_space_id IS NULL
       AND organization_event_audit_id IS NULL
       AND detector_rollout_audit_id IS NULL
       AND lifecycle_restore_audit_id IS NULL)
      OR
      (cause_kind='org_retarget' AND scope_kind='org_unit'
       AND organization_event_audit_id IS NOT NULL
       AND old_ancestor_space_id IS NOT NULL AND new_ancestor_space_id IS NOT NULL
       AND old_ancestor_space_id<>new_ancestor_space_id
       AND previous_detector_generation IS NULL
       AND detector_rollout_audit_id IS NULL
       AND lifecycle_restore_audit_id IS NULL)
      OR
      (cause_kind='detector_reseed' AND scope_kind='global'
       AND previous_detector_generation IS NOT NULL
       AND previous_detector_generation<detector_generation
       AND detector_rollout_audit_id IS NOT NULL
       AND old_ancestor_space_id IS NULL AND new_ancestor_space_id IS NULL
       AND organization_event_audit_id IS NULL
       AND lifecycle_restore_audit_id IS NULL)
      OR
      (cause_kind='lifecycle_reactivate' AND scope_kind IN ('item','wiki')
       AND lifecycle_restore_audit_id IS NOT NULL
       AND previous_detector_generation IS NULL
       AND old_ancestor_space_id IS NULL AND new_ancestor_space_id IS NULL
       AND organization_event_audit_id IS NULL
       AND detector_rollout_audit_id IS NULL)
    ),
    CHECK(
      (state IN ('pending','running') AND result_sha256 IS NULL
       AND error_json IS NULL AND completed_at IS NULL)
      OR (state='completed' AND result_sha256 IS NOT NULL
       AND error_json IS NULL AND completed_at IS NOT NULL)
      OR (state='failed' AND result_sha256 IS NULL
       AND error_json IS NOT NULL AND completed_at IS NOT NULL)
    )
) STRICT
""".strip(),
    """
CREATE INDEX ix_semantic_seed_state_cursor
ON semantic_edge_intent_seed_run(state,cursor_updated_at,cursor_revision_id,id)
""".strip(),
    """
CREATE INDEX ix_semantic_seed_scope_history
ON semantic_edge_intent_seed_run(scope_kind,scope_id,cause_kind,created_at,id)
""".strip(),
    """
CREATE TRIGGER trg_semantic_seed_state_insert
BEFORE INSERT ON semantic_edge_intent_seed_run
WHEN NEW.state<>'pending' OR NEW.fence<>1 OR NEW.cursor_updated_at<>0
 OR NEW.cursor_revision_id IS NOT NULL OR NEW.scanned_count<>0
 OR NEW.intent_count<>0 OR NEW.updated_at<>NEW.created_at
 OR NEW.row_version<>1
 OR NOT (
   (NEW.scope_kind='global' AND NEW.scope_id IS NULL)
   OR (NEW.scope_kind='org_unit' AND EXISTS (
     SELECT 1 FROM org_unit o WHERE o.id=NEW.scope_id AND o.status='active'
   ))
   OR (NEW.scope_kind='item' AND EXISTS (
     SELECT 1 FROM knowledge_item i WHERE i.id=NEW.scope_id
       AND i.lifecycle_status='active'
   ))
   OR (NEW.scope_kind='wiki' AND EXISTS (
     SELECT 1 FROM personal_wiki_profile p WHERE p.space_id=NEW.scope_id
       AND p.lifecycle_status='active'
   ))
 )
 OR (NEW.cause_kind='org_retarget' AND NOT EXISTS (
   SELECT 1 FROM knowledge_space old JOIN knowledge_space new
   WHERE old.id=NEW.old_ancestor_space_id
     AND new.id=NEW.new_ancestor_space_id
     AND old.space_kind IN ('team','responsibility')
     AND new.space_kind IN ('team','responsibility')
 ))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_SEED_TRANSITION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_seed_transition_update
BEFORE UPDATE ON semantic_edge_intent_seed_run
WHEN NEW.id IS NOT OLD.id OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.cause_kind IS NOT OLD.cause_kind OR NEW.scope_kind IS NOT OLD.scope_kind
 OR NEW.scope_id IS NOT OLD.scope_id
 OR NEW.detector_generation IS NOT OLD.detector_generation
 OR NEW.previous_detector_generation IS NOT OLD.previous_detector_generation
 OR NEW.old_ancestor_space_id IS NOT OLD.old_ancestor_space_id
 OR NEW.new_ancestor_space_id IS NOT OLD.new_ancestor_space_id
 OR NEW.organization_event_audit_id IS NOT OLD.organization_event_audit_id
 OR NEW.detector_rollout_audit_id IS NOT OLD.detector_rollout_audit_id
 OR NEW.lifecycle_restore_audit_id IS NOT OLD.lifecycle_restore_audit_id
 OR NEW.snapshot_domain_high_water IS NOT OLD.snapshot_domain_high_water
 OR NEW.snapshot_audit_high_water IS NOT OLD.snapshot_audit_high_water
 OR NEW.created_audit_event_id IS NOT OLD.created_audit_event_id
 OR NEW.created_outbox_event_id IS NOT OLD.created_outbox_event_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.updated_at<OLD.updated_at
 OR NEW.row_version<>OLD.row_version+1
 OR NEW.scanned_count<OLD.scanned_count OR NEW.intent_count<OLD.intent_count
 OR NEW.cursor_updated_at<OLD.cursor_updated_at
 OR (NEW.cursor_updated_at=OLD.cursor_updated_at
     AND OLD.cursor_revision_id IS NOT NULL
     AND (NEW.cursor_revision_id IS NULL OR NEW.cursor_revision_id<OLD.cursor_revision_id))
 OR NOT (
   (OLD.state='pending' AND NEW.state='running' AND NEW.fence=OLD.fence)
   OR (OLD.state='running' AND NEW.state IN ('running','completed','failed')
       AND NEW.fence=OLD.fence)
   OR (OLD.state='failed' AND NEW.state='running' AND NEW.fence>OLD.fence)
 )
BEGIN SELECT RAISE(ABORT,'SEMANTIC_SEED_TRANSITION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_seed_history_delete
BEFORE DELETE ON semantic_edge_intent_seed_run
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE semantic_edge_intent(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    source_space_id TEXT NOT NULL CHECK({_uuid('source_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK({_uuid('source_item_id')}),
    source_revision_id TEXT NOT NULL CHECK({_uuid('source_revision_id')}),
    source_body_sha256 TEXT NOT NULL CHECK({_sha('source_body_sha256')}),
    target_kind TEXT NOT NULL CHECK(target_kind IN ('local','aggregate')),
    target_space_id TEXT NOT NULL CHECK({_uuid('target_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_org_unit_id TEXT CHECK(
      target_org_unit_id IS NULL OR ({_uuid('target_org_unit_id')})
    ) REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    detector_generation INTEGER NOT NULL CHECK(detector_generation>=1),
    source_revision_row_version INTEGER NOT NULL CHECK(source_revision_row_version>=1),
    source_space_generation INTEGER NOT NULL CHECK(source_space_generation>=0),
    target_space_generation INTEGER NOT NULL CHECK(target_space_generation>=0),
    organization_generation INTEGER NOT NULL CHECK(organization_generation>=0),
    closure_proof_sha256 TEXT NOT NULL CHECK({_sha('closure_proof_sha256')}),
    item_lifecycle_generation INTEGER NOT NULL CHECK(item_lifecycle_generation>=1),
    wiki_lifecycle_generation INTEGER NOT NULL CHECK(wiki_lifecycle_generation>=0),
    latest_episode_id TEXT CHECK(
      latest_episode_id IS NULL OR ({_uuid('latest_episode_id')})
    ),
    latest_episode_no INTEGER CHECK(latest_episode_no IS NULL OR latest_episode_no>=1),
    latest_episode_restored_at INTEGER CHECK(
      latest_episode_restored_at IS NULL OR latest_episode_restored_at>=0
    ),
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN (
      'publication','baseline_backfill','org_retarget','detector_reseed',
      'lifecycle_reactivate'
    )),
    publication_transition_id TEXT CHECK(
      publication_transition_id IS NULL OR ({_uuid('publication_transition_id')})
    ) REFERENCES item_curation_transition(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    publication_audit_event_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    publication_outbox_event_id INTEGER REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    seed_run_id TEXT CHECK(seed_run_id IS NULL OR ({_uuid('seed_run_id')}))
        REFERENCES semantic_edge_intent_seed_run(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    seed_snapshot_high_water INTEGER CHECK(
      seed_snapshot_high_water IS NULL OR seed_snapshot_high_water>=0
    ),
    seed_cursor_updated_at INTEGER CHECK(
      seed_cursor_updated_at IS NULL OR seed_cursor_updated_at>=0
    ),
    seed_cursor_revision_id TEXT CHECK(
      seed_cursor_revision_id IS NULL OR ({_uuid('seed_cursor_revision_id')})
    ) REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    organization_event_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    detector_rollout_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    lifecycle_restore_audit_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    FOREIGN KEY(source_item_id,source_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(latest_episode_id,source_item_id)
      REFERENCES item_deactivation_episode(id,item_id)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK((latest_episode_id IS NULL)=(latest_episode_no IS NULL)),
    CHECK(
      (provenance_kind='publication' AND publication_transition_id IS NOT NULL
       AND publication_audit_event_id IS NOT NULL
       AND publication_outbox_event_id IS NOT NULL AND seed_run_id IS NULL
       AND seed_snapshot_high_water IS NULL AND seed_cursor_updated_at IS NULL
       AND seed_cursor_revision_id IS NULL
       AND organization_event_audit_id IS NULL
       AND detector_rollout_audit_id IS NULL
       AND lifecycle_restore_audit_id IS NULL)
      OR
      (provenance_kind<>'publication' AND publication_transition_id IS NULL
       AND publication_audit_event_id IS NULL
       AND publication_outbox_event_id IS NULL AND seed_run_id IS NOT NULL
       AND seed_snapshot_high_water IS NOT NULL
       AND seed_cursor_updated_at IS NOT NULL
       AND seed_cursor_revision_id IS NOT NULL
       AND ((provenance_kind='baseline_backfill'
             AND organization_event_audit_id IS NULL
             AND detector_rollout_audit_id IS NULL
             AND lifecycle_restore_audit_id IS NULL)
         OR (provenance_kind='org_retarget'
             AND organization_event_audit_id IS NOT NULL
             AND detector_rollout_audit_id IS NULL
             AND lifecycle_restore_audit_id IS NULL)
         OR (provenance_kind='detector_reseed'
             AND organization_event_audit_id IS NULL
             AND detector_rollout_audit_id IS NOT NULL
             AND lifecycle_restore_audit_id IS NULL)
         OR (provenance_kind='lifecycle_reactivate'
             AND organization_event_audit_id IS NULL
             AND detector_rollout_audit_id IS NULL
             AND lifecycle_restore_audit_id IS NOT NULL)))
    )
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_semantic_intent_generation_identity
ON semantic_edge_intent(
  source_revision_id,target_kind,target_space_id,detector_generation,
  organization_generation,item_lifecycle_generation,wiki_lifecycle_generation
)
""".strip(),
    """
CREATE INDEX ix_semantic_intent_materialize
ON semantic_edge_intent(created_at,id,effect_key)
""".strip(),
    """
CREATE INDEX ix_semantic_intent_source
ON semantic_edge_intent(source_space_id,source_revision_id,target_kind,target_space_id)
""".strip(),
    """
CREATE TRIGGER trg_semantic_intent_validity_insert
BEFORE INSERT ON semantic_edge_intent
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_item i
  JOIN knowledge_revision r ON r.id=NEW.source_revision_id AND r.item_id=i.id
  JOIN knowledge_space s ON s.id=NEW.source_space_id
  WHERE i.id=NEW.source_item_id AND i.space_id=NEW.source_space_id
    AND i.lifecycle_status='active' AND i.curation_status='published'
    AND i.current_revision_id=NEW.source_revision_id
    AND i.latest_deactivation_episode_id IS NEW.latest_episode_id
    AND r.normalized_content_sha256=NEW.source_body_sha256
    AND s.status='active' AND s.generation=NEW.source_space_generation
    AND NEW.source_revision_row_version=1
    AND (
      (s.space_kind='personal' AND EXISTS (
        SELECT 1 FROM personal_wiki_profile p
        WHERE p.space_id=s.id AND p.lifecycle_status='active'
          AND p.lifecycle_generation=NEW.wiki_lifecycle_generation
      ))
      OR (s.space_kind<>'personal' AND NEW.wiki_lifecycle_generation=0)
    )
)
OR NOT (
  (NEW.latest_episode_id IS NULL AND NEW.item_lifecycle_generation=1
   AND NEW.latest_episode_restored_at IS NULL
   AND NOT EXISTS (
     SELECT 1 FROM item_deactivation_episode e
     WHERE e.item_id=NEW.source_item_id
   ))
  OR EXISTS (
    SELECT 1 FROM item_deactivation_episode e
    WHERE e.id=NEW.latest_episode_id AND e.item_id=NEW.source_item_id
      AND e.episode_no=NEW.latest_episode_no
      AND e.episode_no=(
        SELECT max(x.episode_no) FROM item_deactivation_episode x
        WHERE x.item_id=NEW.source_item_id
      )
      AND e.purged_at IS NULL
      AND ((e.restored_at IS NULL
            AND NEW.latest_episode_restored_at IS NULL
            AND NEW.item_lifecycle_generation=2*e.episode_no)
        OR (e.restored_at IS NOT NULL
            AND NEW.latest_episode_restored_at=e.restored_at
            AND NEW.item_lifecycle_generation=2*e.episode_no+1))
  )
)
OR NOT EXISTS (
  SELECT 1 FROM knowledge_space t
  LEFT JOIN org_unit o ON o.id=t.owner_org_unit_id
  WHERE t.id=NEW.target_space_id AND t.status='active'
    AND t.generation=NEW.target_space_generation
    AND (
      (NEW.target_kind='local' AND t.id=NEW.source_space_id
       AND NEW.target_org_unit_id IS t.owner_org_unit_id
       AND NEW.organization_generation=ifnull(o.row_version,0))
      OR
      (NEW.target_kind='aggregate' AND t.space_kind IN ('team','responsibility')
       AND NEW.target_org_unit_id=t.owner_org_unit_id
       AND NEW.organization_generation=o.row_version
       AND EXISTS (
         WITH RECURSIVE descendants(id) AS (
           SELECT o.id UNION ALL
           SELECT c.id FROM org_unit c JOIN descendants d ON c.parent_id=d.id
         )
         SELECT 1 FROM descendants d
         JOIN knowledge_space source ON source.owner_org_unit_id=d.id
         WHERE source.id=NEW.source_space_id AND d.id<>o.id
       ))
    )
)
OR (NEW.provenance_kind='publication' AND NOT EXISTS (
  SELECT 1 FROM item_curation_transition ct
  JOIN audit_event a ON a.id=NEW.publication_audit_event_id
  JOIN outbox_event x ON x.id=NEW.publication_outbox_event_id
  WHERE ct.id=NEW.publication_transition_id
    AND ct.item_id=NEW.source_item_id AND ct.space_id=NEW.source_space_id
    AND ct.result_revision_id=NEW.source_revision_id
    AND ct.to_status='published'
    AND a.outcome='committed' AND a.space_id=NEW.source_space_id
    AND a.item_id=NEW.source_item_id
    AND a.new_revision_id=NEW.source_revision_id
    AND a.occurred_at=ct.created_at
    AND x.space_id=NEW.source_space_id AND x.created_at=ct.created_at
))
OR (NEW.provenance_kind<>'publication' AND NOT EXISTS (
  SELECT 1 FROM semantic_edge_intent_seed_run sr
  WHERE sr.id=NEW.seed_run_id AND sr.cause_kind=NEW.provenance_kind
    AND sr.state='running'
    AND sr.detector_generation=NEW.detector_generation
    AND sr.snapshot_domain_high_water=NEW.seed_snapshot_high_water
    AND (NEW.seed_cursor_updated_at,NEW.seed_cursor_revision_id)
        <=(sr.cursor_updated_at,sr.cursor_revision_id)
    AND NEW.organization_event_audit_id IS sr.organization_event_audit_id
    AND NEW.detector_rollout_audit_id IS sr.detector_rollout_audit_id
    AND NEW.lifecycle_restore_audit_id IS sr.lifecycle_restore_audit_id
    AND (
      (sr.scope_kind='global' AND sr.scope_id IS NULL)
      OR (sr.scope_kind='org_unit' AND NEW.provenance_kind='org_retarget')
      OR (sr.scope_kind='item' AND NEW.provenance_kind='lifecycle_reactivate'
          AND sr.scope_id=NEW.source_item_id)
      OR (sr.scope_kind='wiki' AND NEW.provenance_kind='lifecycle_reactivate'
          AND sr.scope_id=NEW.source_space_id)
    )
))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_INTENT_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_intent_history_update
BEFORE UPDATE ON semantic_edge_intent
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_intent_history_delete
BEFORE DELETE ON semantic_edge_intent
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE aggregate_knowledge_edge(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    semantic_key TEXT NOT NULL CHECK({_sha('semantic_key')}),
    home_space_id TEXT NOT NULL CHECK({_uuid('home_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    home_org_unit_id TEXT NOT NULL CHECK({_uuid('home_org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_space_id TEXT NOT NULL CHECK({_uuid('source_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK({_uuid('source_item_id')}),
    source_revision_id TEXT NOT NULL CHECK({_uuid('source_revision_id')}),
    target_space_id TEXT NOT NULL CHECK({_uuid('target_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_item_id TEXT NOT NULL CHECK({_uuid('target_item_id')}),
    target_revision_id TEXT NOT NULL CHECK({_uuid('target_revision_id')}),
    edge_type TEXT NOT NULL REFERENCES semantic_relation_type(relation_type)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK(
      direction IN ('source_to_target','target_to_source','symmetric')
    ),
    edge_status TEXT NOT NULL CHECK(
      edge_status IN ('active','inactive','needs_revalidation','suppressed')
    ),
    origin TEXT NOT NULL CHECK(origin IN ('semantic_auto','manual')),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','system')),
    actor_user_id TEXT CHECK(actor_user_id IS NULL OR ({_user('actor_user_id')}))
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    system_job_id TEXT CHECK(system_job_id IS NULL OR ({_uuid('system_job_id')}))
        REFERENCES durable_job(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    effect_key TEXT NOT NULL CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    detector_generation INTEGER CHECK(
      detector_generation IS NULL OR detector_generation>=1
    ),
    model_provider TEXT CHECK(
      model_provider IS NULL OR length(trim(model_provider)) BETWEEN 1 AND 120
    ),
    model_name TEXT CHECK(
      model_name IS NULL OR length(trim(model_name)) BETWEEN 1 AND 160
    ),
    prompt_schema_version TEXT CHECK(
      prompt_schema_version IS NULL
      OR length(trim(prompt_schema_version)) BETWEEN 1 AND 80
    ),
    scoring_version TEXT CHECK(
      scoring_version IS NULL OR length(trim(scoring_version)) BETWEEN 1 AND 80
    ),
    confidence_ppm INTEGER CHECK(
      confidence_ppm IS NULL OR confidence_ppm BETWEEN 0 AND 1000000
    ),
    source_evidence_span_sha256 TEXT NOT NULL CHECK({_sha('source_evidence_span_sha256')}),
    target_evidence_span_sha256 TEXT NOT NULL CHECK({_sha('target_evidence_span_sha256')}),
    source_evidence_location_json TEXT NOT NULL CHECK(
      json_valid(source_evidence_location_json)
      AND json_type(source_evidence_location_json)='object'
    ),
    target_evidence_location_json TEXT NOT NULL CHECK(
      json_valid(target_evidence_location_json)
      AND json_type(target_evidence_location_json)='object'
    ),
    closure_proof_sha256 TEXT NOT NULL CHECK({_sha('closure_proof_sha256')}),
    source_item_row_version INTEGER NOT NULL CHECK(source_item_row_version>=1),
    target_item_row_version INTEGER NOT NULL CHECK(target_item_row_version>=1),
    audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
    deactivated_at INTEGER CHECK(deactivated_at IS NULL OR deactivated_at>=created_at),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    FOREIGN KEY(source_item_id,source_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,target_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK(source_space_id<>target_space_id AND source_item_id<>target_item_id),
    CHECK((edge_status='active')=(deactivated_at IS NULL)),
    CHECK(
      (origin='manual' AND actor_kind='user' AND actor_user_id IS NOT NULL
       AND system_job_id IS NULL AND detector_generation IS NULL
       AND model_provider IS NULL AND model_name IS NULL
       AND prompt_schema_version IS NULL AND scoring_version IS NULL
       AND confidence_ppm IS NULL)
      OR
      (origin='semantic_auto' AND actor_kind='system' AND actor_user_id IS NULL
       AND system_job_id IS NOT NULL AND detector_generation IS NOT NULL
       AND model_provider IS NOT NULL AND model_name IS NOT NULL
       AND prompt_schema_version IS NOT NULL AND scoring_version IS NOT NULL
       AND confidence_ppm IS NOT NULL)
    )
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_aggregate_edge_effect_key
ON aggregate_knowledge_edge(effect_key)
""".strip(),
    """
CREATE UNIQUE INDEX ux_aggregate_edge_open_semantic_key
ON aggregate_knowledge_edge(semantic_key)
WHERE edge_status IN ('active','needs_revalidation')
""".strip(),
    """
CREATE UNIQUE INDEX ux_aggregate_edge_open_endpoints
ON aggregate_knowledge_edge(
  edge_type,direction,
  CASE WHEN direction='symmetric' AND source_revision_id>target_revision_id
       THEN target_revision_id ELSE source_revision_id END,
  CASE WHEN direction='symmetric' AND source_revision_id>target_revision_id
       THEN source_revision_id ELSE target_revision_id END
)
WHERE edge_status IN ('active','needs_revalidation')
""".strip(),
    """
CREATE INDEX ix_aggregate_edge_home_state_type
ON aggregate_knowledge_edge(home_space_id,edge_status,edge_type,id)
""".strip(),
    """
CREATE INDEX ix_aggregate_edge_source_revision
ON aggregate_knowledge_edge(source_revision_id,edge_status,home_space_id,id)
""".strip(),
    """
CREATE INDEX ix_aggregate_edge_target_revision
ON aggregate_knowledge_edge(target_revision_id,edge_status,home_space_id,id)
""".strip(),
    """
CREATE TRIGGER trg_aggregate_edge_validity_insert
BEFORE INSERT ON aggregate_knowledge_edge
WHEN NEW.edge_status<>'active'
 OR NOT EXISTS (
   SELECT 1 FROM semantic_relation_type r
   WHERE r.relation_type=NEW.edge_type AND r.active=1
     AND ((r.direction_policy='symmetric' AND NEW.direction='symmetric')
       OR (r.direction_policy='directed' AND NEW.direction<>'symmetric'))
 )
 OR NOT EXISTS (
   SELECT 1 FROM knowledge_space h JOIN org_unit ho ON ho.id=h.owner_org_unit_id
   JOIN knowledge_space ss ON ss.id=NEW.source_space_id
   JOIN knowledge_space ts ON ts.id=NEW.target_space_id
   JOIN knowledge_item si ON si.id=NEW.source_item_id AND si.space_id=ss.id
   JOIN knowledge_item ti ON ti.id=NEW.target_item_id AND ti.space_id=ts.id
   WHERE h.id=NEW.home_space_id AND ho.id=NEW.home_org_unit_id
     AND h.space_kind IN ('team','responsibility') AND h.status='active'
     AND ss.space_kind<>'personal' AND ts.space_kind<>'personal'
     AND ss.status='active' AND ts.status='active'
     AND si.lifecycle_status='active' AND ti.lifecycle_status='active'
     AND si.curation_status='published' AND ti.curation_status='published'
     AND si.current_revision_id=NEW.source_revision_id
     AND ti.current_revision_id=NEW.target_revision_id
     AND si.row_version=NEW.source_item_row_version
     AND ti.row_version=NEW.target_item_row_version
 )
 OR NEW.home_org_unit_id IS NOT (
   WITH RECURSIVE
   sa(id,depth) AS (
     SELECT s.owner_org_unit_id,0 FROM knowledge_space s WHERE s.id=NEW.source_space_id
     UNION ALL SELECT o.parent_id,sa.depth+1 FROM org_unit o JOIN sa ON o.id=sa.id
     WHERE o.parent_id IS NOT NULL
   ),
   ta(id,depth) AS (
     SELECT s.owner_org_unit_id,0 FROM knowledge_space s WHERE s.id=NEW.target_space_id
     UNION ALL SELECT o.parent_id,ta.depth+1 FROM org_unit o JOIN ta ON o.id=ta.id
     WHERE o.parent_id IS NOT NULL
   )
   SELECT sa.id FROM sa JOIN ta ON ta.id=sa.id JOIN org_unit o ON o.id=sa.id
   WHERE o.status='active' AND o.kind IN ('team','responsibility')
   ORDER BY sa.depth+ta.depth,sa.id LIMIT 1
 )
 OR (NEW.origin='semantic_auto' AND NOT EXISTS (
   SELECT 1 FROM durable_job j WHERE j.id=NEW.system_job_id
     AND j.actor_kind='system' AND j.actor_user_id IS NULL
     AND j.job_type='semantic_edge.evaluate' AND j.effect_key=NEW.effect_key
 ))
BEGIN SELECT RAISE(ABORT,'AGGREGATE_EDGE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_aggregate_edge_state_update
BEFORE UPDATE ON aggregate_knowledge_edge
WHEN NEW.id IS NOT OLD.id OR NEW.semantic_key IS NOT OLD.semantic_key
 OR NEW.home_space_id IS NOT OLD.home_space_id
 OR NEW.home_org_unit_id IS NOT OLD.home_org_unit_id
 OR NEW.source_space_id IS NOT OLD.source_space_id
 OR NEW.source_item_id IS NOT OLD.source_item_id
 OR NEW.source_revision_id IS NOT OLD.source_revision_id
 OR NEW.target_space_id IS NOT OLD.target_space_id
 OR NEW.target_item_id IS NOT OLD.target_item_id
 OR NEW.target_revision_id IS NOT OLD.target_revision_id
 OR NEW.edge_type IS NOT OLD.edge_type OR NEW.direction IS NOT OLD.direction
 OR NEW.origin IS NOT OLD.origin OR NEW.actor_kind IS NOT OLD.actor_kind
 OR NEW.actor_user_id IS NOT OLD.actor_user_id
 OR NEW.system_job_id IS NOT OLD.system_job_id
 OR NEW.operation_id IS NOT OLD.operation_id OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.detector_generation IS NOT OLD.detector_generation
 OR NEW.model_provider IS NOT OLD.model_provider OR NEW.model_name IS NOT OLD.model_name
 OR NEW.prompt_schema_version IS NOT OLD.prompt_schema_version
 OR NEW.scoring_version IS NOT OLD.scoring_version
 OR NEW.confidence_ppm IS NOT OLD.confidence_ppm
 OR NEW.source_evidence_span_sha256 IS NOT OLD.source_evidence_span_sha256
 OR NEW.target_evidence_span_sha256 IS NOT OLD.target_evidence_span_sha256
 OR NEW.source_evidence_location_json IS NOT OLD.source_evidence_location_json
 OR NEW.target_evidence_location_json IS NOT OLD.target_evidence_location_json
 OR NEW.closure_proof_sha256 IS NOT OLD.closure_proof_sha256
 OR NEW.source_item_row_version IS NOT OLD.source_item_row_version
 OR NEW.target_item_row_version IS NOT OLD.target_item_row_version
 OR NEW.audit_event_id IS NOT OLD.audit_event_id
 OR NEW.outbox_event_id IS NOT OLD.outbox_event_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.updated_at<OLD.updated_at
 OR NEW.row_version<>OLD.row_version+1
 OR NOT (
   (OLD.edge_status='active' AND NEW.edge_status IN (
      'inactive','needs_revalidation','suppressed'
    ) AND NEW.deactivated_at IS NOT NULL)
   OR (OLD.edge_status='needs_revalidation'
       AND NEW.edge_status='active' AND NEW.deactivated_at IS NULL)
   OR (OLD.edge_status='needs_revalidation'
       AND NEW.edge_status IN ('inactive','suppressed')
       AND NEW.deactivated_at IS NOT NULL)
   OR (OLD.edge_status=NEW.edge_status
       AND NEW.edge_status IN ('inactive','needs_revalidation','suppressed')
       AND NEW.deactivated_at IS OLD.deactivated_at)
 )
BEGIN SELECT RAISE(ABORT,'AGGREGATE_EDGE_TRANSITION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_aggregate_edge_history_delete
BEFORE DELETE ON aggregate_knowledge_edge
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE semantic_edge_decision(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    job_id TEXT NOT NULL CHECK({_uuid('job_id')})
        REFERENCES durable_job(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    intent_id TEXT NOT NULL CHECK({_uuid('intent_id')})
        REFERENCES semantic_edge_intent(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    record_kind TEXT NOT NULL CHECK(record_kind IN ('candidate','job_terminal')),
    ordinal INTEGER CHECK(ordinal IS NULL OR ordinal BETWEEN 1 AND 12),
    outcome TEXT NOT NULL CHECK(outcome IN (
      'activated','rejected','no_candidate','stale','provider_failure','dead'
    )),
    candidate_space_id TEXT CHECK(
      candidate_space_id IS NULL OR ({_uuid('candidate_space_id')})
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    candidate_item_id TEXT CHECK(
      candidate_item_id IS NULL OR ({_uuid('candidate_item_id')})
    ),
    candidate_revision_id TEXT CHECK(
      candidate_revision_id IS NULL OR ({_uuid('candidate_revision_id')})
    ),
    retrieval_version TEXT CHECK(
      retrieval_version IS NULL OR length(trim(retrieval_version)) BETWEEN 1 AND 80
    ),
    retrieval_score_json TEXT CHECK(retrieval_score_json IS NULL OR (
      json_valid(retrieval_score_json) AND json_type(retrieval_score_json)='object'
    )),
    model_provider TEXT CHECK(
      model_provider IS NULL OR length(trim(model_provider)) BETWEEN 1 AND 120
    ),
    model_name TEXT CHECK(
      model_name IS NULL OR length(trim(model_name)) BETWEEN 1 AND 160
    ),
    prompt_schema_version TEXT CHECK(
      prompt_schema_version IS NULL
      OR length(trim(prompt_schema_version)) BETWEEN 1 AND 80
    ),
    response_sha256 TEXT CHECK(
      response_sha256 IS NULL OR ({_sha('response_sha256')})
    ),
    edge_type TEXT REFERENCES semantic_relation_type(relation_type)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    direction TEXT CHECK(
      direction IS NULL OR direction IN (
        'source_to_target','target_to_source','symmetric'
      )
    ),
    model_grade INTEGER CHECK(model_grade IS NULL OR model_grade BETWEEN 0 AND 3),
    source_evidence_span_sha256 TEXT CHECK(
      source_evidence_span_sha256 IS NULL
      OR ({_sha('source_evidence_span_sha256')})
    ),
    target_evidence_span_sha256 TEXT CHECK(
      target_evidence_span_sha256 IS NULL
      OR ({_sha('target_evidence_span_sha256')})
    ),
    validation_result TEXT CHECK(
      validation_result IS NULL OR validation_result IN ('pass','reject')
    ),
    terminal_reason TEXT CHECK(
      terminal_reason IS NULL OR length(trim(terminal_reason)) BETWEEN 1 AND 200
    ),
    candidate_count INTEGER CHECK(
      candidate_count IS NULL OR candidate_count BETWEEN 0 AND 12
    ),
    candidate_ids_sha256 TEXT CHECK(
      candidate_ids_sha256 IS NULL OR ({_sha('candidate_ids_sha256')})
    ),
    local_edge_id TEXT CHECK(local_edge_id IS NULL OR ({_uuid('local_edge_id')}))
        REFERENCES knowledge_edge(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    aggregate_edge_id TEXT CHECK(
      aggregate_edge_id IS NULL OR ({_uuid('aggregate_edge_id')})
    ) REFERENCES aggregate_knowledge_edge(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    evidence_json TEXT NOT NULL CHECK(
      json_valid(evidence_json) AND json_type(evidence_json)='object'
    ),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    FOREIGN KEY(candidate_item_id,candidate_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(candidate_revision_id,candidate_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK(NOT (local_edge_id IS NOT NULL AND aggregate_edge_id IS NOT NULL)),
    CHECK(
      (record_kind='candidate' AND ordinal IS NOT NULL
       AND outcome IN ('activated','rejected')
       AND candidate_space_id IS NOT NULL AND candidate_item_id IS NOT NULL
       AND candidate_revision_id IS NOT NULL AND retrieval_version IS NOT NULL
       AND retrieval_score_json IS NOT NULL AND model_provider IS NOT NULL
       AND model_name IS NOT NULL AND prompt_schema_version IS NOT NULL
       AND response_sha256 IS NOT NULL AND edge_type IS NOT NULL
       AND direction IS NOT NULL AND model_grade IS NOT NULL
       AND source_evidence_span_sha256 IS NOT NULL
       AND target_evidence_span_sha256 IS NOT NULL
       AND validation_result IS NOT NULL AND terminal_reason IS NULL
       AND candidate_count IS NULL AND candidate_ids_sha256 IS NULL
       AND ((outcome='activated' AND validation_result='pass'
             AND (local_edge_id IS NOT NULL OR aggregate_edge_id IS NOT NULL))
         OR (outcome='rejected' AND validation_result='reject'
             AND local_edge_id IS NULL AND aggregate_edge_id IS NULL)))
      OR
      (record_kind='job_terminal' AND ordinal IS NULL
       AND candidate_space_id IS NULL AND candidate_item_id IS NULL
       AND candidate_revision_id IS NULL AND retrieval_version IS NULL
       AND retrieval_score_json IS NULL AND model_provider IS NULL
       AND model_name IS NULL AND prompt_schema_version IS NULL
       AND response_sha256 IS NULL AND edge_type IS NULL AND direction IS NULL
       AND model_grade IS NULL AND source_evidence_span_sha256 IS NULL
       AND target_evidence_span_sha256 IS NULL AND validation_result IS NULL
       AND terminal_reason IS NOT NULL AND candidate_count IS NOT NULL
       AND candidate_ids_sha256 IS NOT NULL
       AND local_edge_id IS NULL AND aggregate_edge_id IS NULL
       AND ((outcome IN ('activated','rejected') AND candidate_count>=1)
         OR (outcome IN ('no_candidate','stale','provider_failure','dead')
             AND candidate_count=0)))
    )
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_semantic_decision_terminal_job
ON semantic_edge_decision(job_id) WHERE record_kind='job_terminal'
""".strip(),
    """
CREATE UNIQUE INDEX ux_semantic_decision_candidate_ordinal
ON semantic_edge_decision(job_id,ordinal) WHERE record_kind='candidate'
""".strip(),
    """
CREATE INDEX ix_semantic_decision_intent
ON semantic_edge_decision(intent_id,record_kind,ordinal,id)
""".strip(),
    """
CREATE TRIGGER trg_semantic_decision_validity_insert
BEFORE INSERT ON semantic_edge_decision
WHEN NOT EXISTS (
  SELECT 1 FROM durable_job j JOIN semantic_edge_intent i ON i.id=NEW.intent_id
  WHERE j.id=NEW.job_id AND j.actor_kind='system' AND j.actor_user_id IS NULL
    AND j.job_type='semantic_edge.evaluate' AND j.effect_key=i.effect_key
    AND j.state IN ('claimed','running','retry','succeeded','failed','cancelled','dead')
)
OR (NEW.record_kind='candidate' AND (
  NOT EXISTS (
    SELECT 1 FROM knowledge_item c
    WHERE c.id=NEW.candidate_item_id AND c.space_id=NEW.candidate_space_id
      AND c.current_revision_id=NEW.candidate_revision_id
      AND c.lifecycle_status='active' AND c.curation_status='published'
  )
  OR NOT EXISTS (
    SELECT 1 FROM semantic_relation_type r
    WHERE r.relation_type=NEW.edge_type AND r.active=1
      AND ((r.direction_policy='symmetric' AND NEW.direction='symmetric')
        OR (r.direction_policy='directed' AND NEW.direction<>'symmetric'))
  )
  OR (NEW.outcome='activated' AND NOT EXISTS (
    SELECT 1 FROM semantic_edge_intent i
    WHERE i.id=NEW.intent_id AND (
      (i.target_kind='local' AND NEW.aggregate_edge_id IS NULL
       AND EXISTS (
         SELECT 1 FROM knowledge_edge e
         WHERE e.id=NEW.local_edge_id AND e.system_job_id=NEW.job_id
           AND e.origin='semantic_auto' AND e.edge_type=NEW.edge_type
           AND e.space_id=i.target_space_id
           AND ((e.source_evidence_revision_id=i.source_revision_id
                 AND e.target_evidence_revision_id=NEW.candidate_revision_id)
             OR (e.target_evidence_revision_id=i.source_revision_id
                 AND e.source_evidence_revision_id=NEW.candidate_revision_id))
       ))
      OR (i.target_kind='aggregate' AND NEW.local_edge_id IS NULL
       AND EXISTS (
         SELECT 1 FROM aggregate_knowledge_edge e
         WHERE e.id=NEW.aggregate_edge_id AND e.system_job_id=NEW.job_id
           AND e.origin='semantic_auto' AND e.edge_type=NEW.edge_type
           AND e.home_space_id=i.target_space_id
           AND ((e.source_revision_id=i.source_revision_id
                 AND e.target_revision_id=NEW.candidate_revision_id)
             OR (e.target_revision_id=i.source_revision_id
                 AND e.source_revision_id=NEW.candidate_revision_id))
       ))
    )
  ))
))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_DECISION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_job_terminal_update
BEFORE UPDATE ON durable_job
WHEN OLD.job_type='semantic_edge.evaluate'
 AND NEW.state IN ('succeeded','failed','cancelled','dead')
 AND (
   (SELECT count(*) FROM semantic_edge_decision d
    WHERE d.job_id=OLD.id AND d.record_kind='job_terminal')<>1
   OR EXISTS (
     SELECT 1 FROM semantic_edge_decision t
     WHERE t.job_id=OLD.id AND t.record_kind='job_terminal'
       AND (
         t.candidate_count<>(
           SELECT count(*) FROM semantic_edge_decision c
           WHERE c.job_id=OLD.id AND c.record_kind='candidate'
         )
         OR (t.candidate_count>0 AND (
           (SELECT min(c.ordinal) FROM semantic_edge_decision c
             WHERE c.job_id=OLD.id AND c.record_kind='candidate')<>1
           OR (SELECT max(c.ordinal) FROM semantic_edge_decision c
             WHERE c.job_id=OLD.id AND c.record_kind='candidate')
                <>t.candidate_count
         ))
         OR (t.outcome='activated' AND NOT EXISTS (
           SELECT 1 FROM semantic_edge_decision c
           WHERE c.job_id=OLD.id AND c.record_kind='candidate'
             AND c.outcome='activated'
         ))
         OR (t.outcome='rejected' AND EXISTS (
           SELECT 1 FROM semantic_edge_decision c
           WHERE c.job_id=OLD.id AND c.record_kind='candidate'
             AND c.outcome<>'rejected'
         ))
       )
   )
 )
BEGIN SELECT RAISE(ABORT,'SEMANTIC_DECISION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_decision_history_update
BEFORE UPDATE ON semantic_edge_decision
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_decision_history_delete
BEFORE DELETE ON semantic_edge_decision
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE semantic_edge_suppression(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    semantic_key TEXT NOT NULL CHECK({_sha('semantic_key')}),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('local','aggregate')),
    scope_space_id TEXT NOT NULL CHECK({_uuid('scope_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    local_edge_id TEXT CHECK(local_edge_id IS NULL OR ({_uuid('local_edge_id')}))
        REFERENCES knowledge_edge(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    aggregate_edge_id TEXT CHECK(
      aggregate_edge_id IS NULL OR ({_uuid('aggregate_edge_id')})
    ) REFERENCES aggregate_knowledge_edge(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    detector_generation_floor INTEGER NOT NULL CHECK(detector_generation_floor>=1),
    detector_generation_ceiling INTEGER CHECK(
      detector_generation_ceiling IS NULL
      OR detector_generation_ceiling>=detector_generation_floor
    ),
    status TEXT NOT NULL CHECK(status IN ('active','lifted')),
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    superseding_local_edge_id TEXT CHECK(
      superseding_local_edge_id IS NULL OR ({_uuid('superseding_local_edge_id')})
    ) REFERENCES knowledge_edge(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    superseding_aggregate_edge_id TEXT CHECK(
      superseding_aggregate_edge_id IS NULL
      OR ({_uuid('superseding_aggregate_edge_id')})
    ) REFERENCES aggregate_knowledge_edge(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    lifted_by_user_id TEXT CHECK(
      lifted_by_user_id IS NULL OR ({_user('lifted_by_user_id')})
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    lifted_at INTEGER CHECK(lifted_at IS NULL OR lifted_at>=0),
    lift_audit_event_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    lift_outbox_event_id INTEGER REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    CHECK((scope_kind='local' AND local_edge_id IS NOT NULL
           AND aggregate_edge_id IS NULL)
       OR (scope_kind='aggregate' AND local_edge_id IS NULL
           AND aggregate_edge_id IS NOT NULL)),
    CHECK(NOT (superseding_local_edge_id IS NOT NULL
               AND superseding_aggregate_edge_id IS NOT NULL)),
    CHECK((status='active' AND lifted_by_user_id IS NULL AND lifted_at IS NULL
           AND lift_audit_event_id IS NULL AND lift_outbox_event_id IS NULL)
       OR (status='lifted' AND lifted_by_user_id IS NOT NULL
           AND lifted_at IS NOT NULL AND lift_audit_event_id IS NOT NULL
           AND lift_outbox_event_id IS NOT NULL))
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_semantic_suppression_active_key_scope
ON semantic_edge_suppression(semantic_key,scope_kind,scope_space_id)
WHERE status='active'
""".strip(),
    """
CREATE INDEX ix_semantic_suppression_generation
ON semantic_edge_suppression(
  scope_space_id,status,detector_generation_floor,detector_generation_ceiling,id
)
""".strip(),
    """
CREATE TRIGGER trg_semantic_suppression_validity_insert
BEFORE INSERT ON semantic_edge_suppression
WHEN NEW.status<>'active' OR NEW.row_version<>1
 OR NEW.updated_at<>NEW.created_at
 OR (NEW.scope_kind='local' AND NOT EXISTS (
  SELECT 1 FROM knowledge_edge e WHERE e.id=NEW.local_edge_id
    AND e.space_id=NEW.scope_space_id AND e.semantic_key=NEW.semantic_key
)) OR (NEW.scope_kind='aggregate' AND NOT EXISTS (
  SELECT 1 FROM aggregate_knowledge_edge e WHERE e.id=NEW.aggregate_edge_id
    AND e.home_space_id=NEW.scope_space_id AND e.semantic_key=NEW.semantic_key
))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_SUPPRESSION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_suppression_lift_update
BEFORE UPDATE ON semantic_edge_suppression
WHEN NEW.id IS NOT OLD.id OR NEW.semantic_key IS NOT OLD.semantic_key
 OR NEW.scope_kind IS NOT OLD.scope_kind OR NEW.scope_space_id IS NOT OLD.scope_space_id
 OR NEW.local_edge_id IS NOT OLD.local_edge_id
 OR NEW.aggregate_edge_id IS NOT OLD.aggregate_edge_id
 OR NEW.reason IS NOT OLD.reason
 OR NEW.detector_generation_floor IS NOT OLD.detector_generation_floor
 OR NEW.detector_generation_ceiling IS NOT OLD.detector_generation_ceiling
 OR NEW.actor_user_id IS NOT OLD.actor_user_id
 OR NEW.operation_id IS NOT OLD.operation_id OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.audit_event_id IS NOT OLD.audit_event_id
 OR NEW.outbox_event_id IS NOT OLD.outbox_event_id
 OR NEW.superseding_local_edge_id IS NOT OLD.superseding_local_edge_id
 OR NEW.superseding_aggregate_edge_id IS NOT OLD.superseding_aggregate_edge_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.updated_at<OLD.updated_at
 OR NEW.row_version<>OLD.row_version+1
 OR NOT (OLD.status='active' AND NEW.status='lifted'
         AND NEW.updated_at=NEW.lifted_at)
BEGIN SELECT RAISE(ABORT,'SEMANTIC_SUPPRESSION_TRANSITION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_semantic_suppression_history_delete
BEFORE DELETE ON semantic_edge_suppression
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE aggregate_generation_vector(
    selected_space_id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('selected_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_vector_json TEXT NOT NULL CHECK(
      json_valid(source_vector_json) AND json_type(source_vector_json)='array'
    ),
    source_vector_sha256 TEXT NOT NULL CHECK({_sha('source_vector_sha256')}),
    home_edge_generation INTEGER NOT NULL CHECK(home_edge_generation>=0),
    desired_generation INTEGER NOT NULL CHECK(desired_generation>=0),
    built_generation INTEGER NOT NULL CHECK(
      built_generation>=0 AND built_generation<=desired_generation
    ),
    manifest_sha256 TEXT CHECK(manifest_sha256 IS NULL OR ({_sha('manifest_sha256')})),
    closure_proof_sha256 TEXT NOT NULL CHECK({_sha('closure_proof_sha256')}),
    organization_row_version INTEGER NOT NULL CHECK(organization_row_version>=1),
    status TEXT NOT NULL CHECK(status IN ('idle','building','ready','error')),
    last_error_json TEXT CHECK(last_error_json IS NULL OR (
      json_valid(last_error_json) AND json_type(last_error_json)='object'
    )),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    CHECK((status='ready')=(manifest_sha256 IS NOT NULL)),
    CHECK((status='error')=(last_error_json IS NOT NULL))
) STRICT
""".strip(),
    """
CREATE INDEX ix_aggregate_generation_status
ON aggregate_generation_vector(status,desired_generation,built_generation,selected_space_id)
""".strip(),
    """
CREATE TRIGGER trg_aggregate_generation_validity_insert
BEFORE INSERT ON aggregate_generation_vector
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_space s JOIN org_unit o ON o.id=s.owner_org_unit_id
  WHERE s.id=NEW.selected_space_id
    AND s.space_kind IN ('team','responsibility') AND s.status='active'
    AND o.status='active' AND o.row_version=NEW.organization_row_version
)
BEGIN SELECT RAISE(ABORT,'AGGREGATE_GENERATION_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_aggregate_generation_update
BEFORE UPDATE ON aggregate_generation_vector
WHEN NEW.selected_space_id IS NOT OLD.selected_space_id
 OR NEW.desired_generation<OLD.desired_generation
 OR NEW.built_generation<OLD.built_generation
 OR NEW.home_edge_generation<OLD.home_edge_generation
 OR NEW.created_at IS NOT OLD.created_at OR NEW.updated_at<OLD.updated_at
 OR NEW.row_version<>OLD.row_version+1
BEGIN SELECT RAISE(ABORT,'AGGREGATE_GENERATION_INVALID'); END
""".strip(),
    f"""
CREATE TABLE knowledge_explicit_reference(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    source_space_id TEXT NOT NULL CHECK({_uuid('source_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK({_uuid('source_item_id')}),
    source_revision_id TEXT NOT NULL CHECK({_uuid('source_revision_id')}),
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 1000),
    syntax_version TEXT NOT NULL CHECK(syntax_version='item-ref-v1'),
    target_space_id TEXT NOT NULL CHECK({_uuid('target_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_item_id TEXT NOT NULL CHECK({_uuid('target_item_id')}),
    target_revision_id TEXT NOT NULL CHECK({_uuid('target_revision_id')}),
    closure_proof_sha256 TEXT NOT NULL CHECK({_sha('closure_proof_sha256')}),
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    effect_key TEXT NOT NULL UNIQUE CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    availability_status TEXT NOT NULL CHECK(
      availability_status IN ('available','unavailable')
    ),
    unavailable_reason TEXT CHECK(
      unavailable_reason IS NULL
      OR unavailable_reason IN (
        'target_inactive','target_archived','permission_lost','target_missing'
      )
    ),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
    row_version INTEGER NOT NULL CHECK(row_version>=1),
    FOREIGN KEY(source_item_id,source_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,target_space_id)
      REFERENCES knowledge_item(id,space_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
      REFERENCES knowledge_revision(id,item_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK(source_item_id<>target_item_id),
    CHECK((availability_status='available')=(unavailable_reason IS NULL))
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_explicit_reference_revision_ordinal
ON knowledge_explicit_reference(source_revision_id,ordinal)
""".strip(),
    """
CREATE INDEX ix_explicit_reference_source_order
ON knowledge_explicit_reference(source_space_id,source_item_id,source_revision_id,ordinal)
""".strip(),
    """
CREATE INDEX ix_explicit_reference_target_availability
ON knowledge_explicit_reference(target_space_id,target_item_id,availability_status,id)
""".strip(),
    """
CREATE TRIGGER trg_explicit_reference_validity_insert
BEFORE INSERT ON knowledge_explicit_reference
WHEN NEW.availability_status<>'available' OR NEW.row_version<>1
 OR NEW.updated_at<>NEW.created_at
 OR NOT EXISTS (
   SELECT 1 FROM knowledge_item s JOIN knowledge_item t
   JOIN knowledge_space ss ON ss.id=NEW.source_space_id
   JOIN knowledge_space ts ON ts.id=NEW.target_space_id
   WHERE s.id=NEW.source_item_id AND s.space_id=ss.id
     AND t.id=NEW.target_item_id AND t.space_id=ts.id
     AND s.lifecycle_status='active' AND t.lifecycle_status='active'
     AND s.curation_status='published' AND t.curation_status='published'
     AND s.current_revision_id=NEW.source_revision_id
     AND t.current_revision_id=NEW.target_revision_id
     AND (
       ss.id=ts.id
       OR (ss.space_kind='personal' AND ts.id=ss.id)
       OR (ss.space_kind IN ('team','responsibility') AND EXISTS (
         WITH RECURSIVE descendants(id) AS (
           SELECT ss.owner_org_unit_id UNION ALL
           SELECT o.id FROM org_unit o JOIN descendants d ON o.parent_id=d.id
         )
         SELECT 1 FROM descendants d WHERE d.id=ts.owner_org_unit_id
       ))
       OR (ss.space_kind='part' AND EXISTS (
         WITH RECURSIVE ancestors(id) AS (
           SELECT ss.owner_org_unit_id UNION ALL
           SELECT o.parent_id FROM org_unit o JOIN ancestors a ON o.id=a.id
           WHERE o.parent_id IS NOT NULL
         )
         SELECT 1 FROM ancestors a WHERE a.id=ts.owner_org_unit_id
       ))
     )
 )
BEGIN SELECT RAISE(ABORT,'EXPLICIT_REFERENCE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_explicit_reference_availability_update
BEFORE UPDATE ON knowledge_explicit_reference
WHEN NEW.availability_status='available' AND OLD.availability_status<>'available'
 AND NOT EXISTS (
   SELECT 1 FROM knowledge_item t
   WHERE t.id=NEW.target_item_id AND t.space_id=NEW.target_space_id
     AND t.lifecycle_status='active' AND t.curation_status='published'
     AND t.current_revision_id=NEW.target_revision_id
 )
BEGIN SELECT RAISE(ABORT,'EXPLICIT_REFERENCE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_explicit_reference_state_update
BEFORE UPDATE ON knowledge_explicit_reference
WHEN NEW.id IS NOT OLD.id OR NEW.source_space_id IS NOT OLD.source_space_id
 OR NEW.source_item_id IS NOT OLD.source_item_id
 OR NEW.source_revision_id IS NOT OLD.source_revision_id
 OR NEW.ordinal IS NOT OLD.ordinal OR NEW.syntax_version IS NOT OLD.syntax_version
 OR NEW.target_space_id IS NOT OLD.target_space_id
 OR NEW.target_item_id IS NOT OLD.target_item_id
 OR NEW.target_revision_id IS NOT OLD.target_revision_id
 OR NEW.closure_proof_sha256 IS NOT OLD.closure_proof_sha256
 OR NEW.actor_user_id IS NOT OLD.actor_user_id
 OR NEW.operation_id IS NOT OLD.operation_id OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.audit_event_id IS NOT OLD.audit_event_id
 OR NEW.outbox_event_id IS NOT OLD.outbox_event_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.updated_at<OLD.updated_at
 OR NEW.row_version<>OLD.row_version+1
BEGIN SELECT RAISE(ABORT,'EXPLICIT_REFERENCE_INVALID'); END
""".strip(),
    """
CREATE TRIGGER trg_explicit_reference_history_delete
BEFORE DELETE ON knowledge_explicit_reference
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
)


V010_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'v10_table_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN (
    'semantic_relation_type','semantic_edge_intent_seed_run',
    'semantic_edge_intent','aggregate_knowledge_edge','semantic_edge_decision',
    'semantic_edge_suppression','aggregate_generation_vector',
    'knowledge_explicit_reference'
  )
)<>8
OR EXISTS (SELECT 1 FROM sqlite_schema WHERE name='knowledge_edge_v10_old')
OR (SELECT count(*) FROM semantic_relation_type)<>6
OR EXISTS (
  SELECT 1 FROM semantic_relation_type
  WHERE vocabulary_version<>'semantic-rel-v1' OR active<>1
)
""".strip(),
    """
SELECT 'v10_index_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='index' AND name IN (
    'ux_edge_auto_effect_key','ux_edge_auto_semantic_key_active','ix_edge_auto_job',
    'ix_semantic_seed_state_cursor','ix_semantic_seed_scope_history',
    'ux_semantic_intent_generation_identity','ix_semantic_intent_materialize',
    'ix_semantic_intent_source','ux_aggregate_edge_effect_key',
    'ux_aggregate_edge_open_semantic_key','ux_aggregate_edge_open_endpoints',
    'ix_aggregate_edge_home_state_type','ix_aggregate_edge_source_revision',
    'ix_aggregate_edge_target_revision','ux_semantic_decision_terminal_job',
    'ux_semantic_decision_candidate_ordinal','ix_semantic_decision_intent',
    'ux_semantic_suppression_active_key_scope',
    'ix_semantic_suppression_generation','ix_aggregate_generation_status',
    'ux_explicit_reference_revision_ordinal','ix_explicit_reference_source_order',
    'ix_explicit_reference_target_availability'
  )
)<>23
""".strip(),
    """
SELECT 'v10_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name IN (
    'trg_semantic_relation_type_history_update',
    'trg_semantic_relation_type_history_delete',
    'trg_knowledge_edge_approval_insert','trg_knowledge_edge_validity_update',
    'trg_knowledge_edge_history_delete','trg_semantic_seed_state_insert',
    'trg_semantic_seed_transition_update','trg_semantic_seed_history_delete',
    'trg_semantic_intent_validity_insert',
    'trg_semantic_intent_history_update','trg_semantic_intent_history_delete',
    'trg_aggregate_edge_validity_insert','trg_aggregate_edge_state_update',
    'trg_aggregate_edge_history_delete','trg_semantic_decision_validity_insert',
    'trg_semantic_job_terminal_update',
    'trg_semantic_decision_history_update','trg_semantic_decision_history_delete',
    'trg_semantic_suppression_validity_insert',
    'trg_semantic_suppression_lift_update',
    'trg_semantic_suppression_history_delete',
    'trg_aggregate_generation_validity_insert','trg_aggregate_generation_update',
    'trg_explicit_reference_validity_insert',
    'trg_explicit_reference_availability_update',
    'trg_explicit_reference_state_update',
    'trg_explicit_reference_history_delete'
  )
)<>27
""".strip(),
    """
SELECT 'v10_local_edge_state_invalid'
WHERE EXISTS (
  SELECT 1 FROM knowledge_edge e
  WHERE (e.origin IN ('manual','semantic_approved') AND (
      e.actor_kind<>'user' OR e.created_by IS NULL OR e.updated_by IS NULL
      OR e.system_job_id IS NOT NULL))
     OR (e.origin='semantic_auto' AND (
      e.actor_kind<>'system' OR e.system_job_id IS NULL
      OR e.created_by IS NOT NULL OR e.updated_by IS NOT NULL
      OR e.source_evidence_revision_id IS NULL
      OR e.target_evidence_revision_id IS NULL
      OR e.effect_key IS NULL OR e.semantic_key IS NULL))
)
""".strip(),
    """
SELECT 'v10_aggregate_state_invalid'
WHERE EXISTS (
  SELECT 1 FROM aggregate_knowledge_edge e
  JOIN knowledge_space h ON h.id=e.home_space_id
  JOIN knowledge_space s ON s.id=e.source_space_id
  JOIN knowledge_space t ON t.id=e.target_space_id
  WHERE h.owner_org_unit_id<>e.home_org_unit_id
     OR h.space_kind NOT IN ('team','responsibility')
     OR s.space_kind='personal' OR t.space_kind='personal'
     OR s.id=t.id
)
OR EXISTS (
  SELECT semantic_key FROM aggregate_knowledge_edge
  WHERE edge_status IN ('active','needs_revalidation')
  GROUP BY semantic_key HAVING count(*)<>1
)
""".strip(),
    """
SELECT 'v10_intent_seed_state_invalid'
WHERE EXISTS (
  SELECT 1 FROM semantic_edge_intent i
  JOIN knowledge_item k ON k.id=i.source_item_id
  JOIN knowledge_revision r ON r.id=i.source_revision_id
  WHERE k.space_id<>i.source_space_id OR r.item_id<>i.source_item_id
)
OR EXISTS (
  SELECT effect_key FROM semantic_edge_intent GROUP BY effect_key HAVING count(*)<>1
)
OR EXISTS (
  SELECT job_id FROM semantic_edge_decision
  WHERE record_kind='job_terminal' GROUP BY job_id HAVING count(*)<>1
)
""".strip(),
    """
SELECT 'v10_reference_graph_separation_invalid'
WHERE EXISTS (
  SELECT 1 FROM knowledge_explicit_reference r
  WHERE r.syntax_version<>'item-ref-v1'
     OR r.source_item_id=r.target_item_id
)
OR EXISTS (
  SELECT source_revision_id,ordinal FROM knowledge_explicit_reference
  GROUP BY source_revision_id,ordinal HAVING count(*)<>1
)
""".strip(),
)


__all__ = [
    "SEMANTIC_RELATION_TYPE_SEED_ROWS",
    "V010_POSTCONDITION_QUERIES",
    "V010_STATEMENTS",
]
