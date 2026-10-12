"""Read-only invariant queries for the canonical schema-v1 migration.

Every query returns zero rows for a valid database.  The deployment migrator
runs the complete manifest before commit, under its read-only SQL authorizer,
so a constraint that spans multiple aggregates cannot be skipped by a service
bug or by trigger ordering inside a larger transaction.
"""

from __future__ import annotations


POSTCONDITION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "current_membership_consistency",
        """
SELECT u.id
FROM app_user AS u
LEFT JOIN org_membership AS m
  ON m.user_id=u.id AND m.valid_to IS NULL
GROUP BY u.id,u.current_part_id
HAVING (u.current_part_id IS NULL AND count(m.id)<>0)
    OR (u.current_part_id IS NOT NULL AND (
        count(m.id)<>1 OR min(m.part_id)<>u.current_part_id
    ))
LIMIT 1
""".strip(),
    ),
    (
        "part_leader_write_consistency",
        """
SELECT s.id
FROM knowledge_space AS s
JOIN org_unit AS p ON p.id=s.owner_org_unit_id
WHERE s.space_kind='part' AND s.write_enabled=1 AND (
    p.kind<>'part' OR p.status<>'active' OR
    (SELECT count(*)
       FROM org_role_assignment AS r
       JOIN app_user AS u
         ON u.id=r.user_id AND u.account_status='active'
       JOIN org_membership AS m
         ON m.user_id=r.user_id AND m.part_id=r.org_unit_id
        AND m.valid_to IS NULL
      WHERE r.org_unit_id=p.id AND r.role='part_leader'
        AND r.valid_to IS NULL)<>1
)
UNION ALL
SELECT r.id
FROM org_role_assignment AS r
JOIN org_unit AS p ON p.id=r.org_unit_id
LEFT JOIN app_user AS u ON u.id=r.user_id
LEFT JOIN org_membership AS m
  ON m.user_id=r.user_id AND m.part_id=r.org_unit_id AND m.valid_to IS NULL
WHERE r.role='part_leader' AND r.valid_to IS NULL
  AND (p.kind<>'part' OR p.status<>'active'
       OR u.account_status<>'active' OR m.id IS NULL)
LIMIT 1
""".strip(),
    ),
    (
        "space_owner_consistency",
        """
SELECT s.id
FROM knowledge_space AS s
LEFT JOIN org_unit AS o ON o.id=s.owner_org_unit_id
WHERE NOT (
    (s.space_kind='personal' AND s.owner_user_id IS NOT NULL
      AND s.owner_org_unit_id IS NULL)
    OR
    (s.space_kind IN ('part','team','responsibility')
      AND s.owner_user_id IS NULL AND s.owner_org_unit_id IS NOT NULL
      AND o.kind=s.space_kind)
)
LIMIT 1
""".strip(),
    ),
    (
        "item_revision_transition_consistency",
        """
SELECT i.id
FROM knowledge_item AS i
LEFT JOIN knowledge_revision AS r
  ON r.id=i.current_revision_id AND r.item_id=i.id
LEFT JOIN item_curation_transition AS t
  ON t.id=i.last_curation_transition_id AND t.item_id=i.id
WHERE (i.lifecycle_status='purged_tombstone' AND i.current_revision_id IS NOT NULL)
   OR (i.lifecycle_status<>'purged_tombstone' AND r.id IS NULL)
   OR (i.last_curation_transition_id IS NOT NULL AND (
       t.id IS NULL OR t.to_status<>i.curation_status
       OR (i.lifecycle_status<>'purged_tombstone'
           AND t.result_revision_id<>i.current_revision_id)
   ))
LIMIT 1
""".strip(),
    ),
    (
        "revision_body_retention_consistency",
        """
SELECT i.id
FROM knowledge_item AS i
WHERE (i.lifecycle_status='purged_tombstone' AND EXISTS (
    SELECT 1 FROM knowledge_revision AS r
    JOIN knowledge_revision_body AS b ON b.revision_id=r.id
    WHERE r.item_id=i.id
)) OR (i.lifecycle_status<>'purged_tombstone' AND EXISTS (
    SELECT 1 FROM knowledge_revision AS r
    LEFT JOIN knowledge_revision_body AS b ON b.revision_id=r.id
    WHERE r.item_id=i.id AND b.revision_id IS NULL
))
LIMIT 1
""".strip(),
    ),
    (
        "lineage_membership_consistency",
        """
SELECT i.id
FROM knowledge_item AS i
LEFT JOIN lineage_member AS m ON m.item_id=i.id
WHERE (i.lineage_group_id IS NULL AND m.item_id IS NOT NULL)
   OR (i.lineage_group_id IS NOT NULL AND (
       m.item_id IS NULL OR m.lineage_group_id<>i.lineage_group_id
   ))
UNION ALL
SELECT m.item_id
FROM lineage_member AS m
JOIN knowledge_item AS i ON i.id=m.item_id
LEFT JOIN lineage_member AS d
  ON d.lineage_group_id=m.lineage_group_id
 AND d.item_id=m.derived_from_item_id
WHERE i.lineage_group_id<>m.lineage_group_id
   OR (m.derived_from_item_id IS NOT NULL AND d.item_id IS NULL)
LIMIT 1
""".strip(),
    ),
    (
        "conversation_pin_scope_consistency",
        """
SELECT p.conversation_id
FROM conversation_pin AS p
JOIN conversation AS c ON c.id=p.conversation_id
JOIN knowledge_item AS i ON i.id=p.item_id
JOIN knowledge_revision AS r ON r.id=p.revision_id
WHERE p.space_id<>c.space_id OR p.space_id<>i.space_id OR r.item_id<>p.item_id
LIMIT 1
""".strip(),
    ),
    (
        "edge_scope_and_evidence_consistency",
        """
SELECT e.id
FROM knowledge_edge AS e
JOIN knowledge_item AS s ON s.id=e.source_item_id
JOIN knowledge_item AS t ON t.id=e.target_item_id
LEFT JOIN knowledge_revision AS sr ON sr.id=e.source_evidence_revision_id
LEFT JOIN knowledge_revision AS tr ON tr.id=e.target_evidence_revision_id
WHERE s.space_id<>e.space_id OR t.space_id<>e.space_id
   OR (e.source_evidence_revision_id IS NOT NULL
       AND (sr.id IS NULL OR sr.item_id<>e.source_item_id))
   OR (e.target_evidence_revision_id IS NOT NULL
       AND (tr.id IS NULL OR tr.item_id<>e.target_item_id))
LIMIT 1
""".strip(),
    ),
    (
        "relation_and_finding_revision_consistency",
        """
SELECT r.id
FROM item_relation AS r
JOIN knowledge_revision AS sr ON sr.id=r.source_revision_id
JOIN knowledge_revision AS tr ON tr.id=r.target_revision_id
WHERE sr.item_id<>r.source_item_id OR tr.item_id<>r.target_item_id
UNION ALL
SELECT f.id
FROM curation_finding AS f
JOIN knowledge_item AS si ON si.id=f.source_item_id
JOIN knowledge_revision AS sr ON sr.id=f.source_revision_id
LEFT JOIN knowledge_item AS ti ON ti.id=f.target_item_id
LEFT JOIN knowledge_revision AS tr ON tr.id=f.target_revision_id
WHERE si.space_id<>f.space_id OR sr.item_id<>f.source_item_id
   OR (f.target_item_id IS NOT NULL AND (
       ti.space_id<>f.space_id OR tr.item_id<>f.target_item_id
   ))
LIMIT 1
""".strip(),
    ),
    (
        "finding_policy_and_application_consistency",
        """
WITH allowed(kind,severity,tier,mode,reviewer,visibility,action) AS (
  VALUES
    ('lint','blocker','T0','automatic','none','publication_blocked','quarantine'),
    ('lint','error','T0','automatic','none','publication_blocked','quarantine'),
    ('lint','warning','T0','automatic','none','badge','none'),
    ('lint','info','T0','automatic','none','none','none'),
    ('exact_repeat','info','T0','automatic','none','none','none'),
    ('exact_repeat','info','T1','automatic','none','none','provenance'),
    ('curation_clear','info','T1','automatic','none','none','publish'),
    ('weak_complementary','warning','T2','human','space_editor','badge','none'),
    ('near_duplicate','warning','T2','human','space_editor','agent_hidden','review_duplicate'),
    ('update','info','T2','human','space_editor','agent_hidden','update'),
    ('potential_conflict','error','T2','human','space_editor','agent_hidden','review_conflict'),
    ('semantic_edge','info','T2','human','space_editor','none','relation'),
    ('replace','warning','T2','human','space_editor','agent_hidden','replace'),
    ('merge','warning','T2','human','space_editor','agent_hidden','merge'),
    ('curation_release','info','T2','human','space_editor','agent_hidden','publish'),
    ('curation_quarantine','error','T2','human','space_editor','agent_hidden','quarantine'),
    ('deactivate','warning','T3','human','space_editor','none','deactivate'),
    ('purge','warning','T3','human','admin','none','purge')
)
SELECT f.id
FROM curation_finding AS f
WHERE NOT EXISTS (
    SELECT 1 FROM allowed AS a
    WHERE a.kind=f.finding_kind AND a.severity=f.severity
      AND a.tier=f.risk_tier AND a.mode=f.decision_mode
      AND a.reviewer=f.required_reviewer
      AND a.visibility=f.visibility_effect AND a.action=f.proposed_action
) OR NOT (
    (f.status='applied' AND f.applied_at IS NOT NULL
      AND f.last_application_error_json IS NULL AND (
        (f.applied_operation_id IS NOT NULL AND f.applied_dedup_id IS NOT NULL
          AND f.applied_by IS NOT NULL AND f.applied_job_id IS NULL)
        OR
        (f.applied_operation_id IS NULL AND f.applied_dedup_id IS NULL
          AND f.applied_by IS NULL AND f.applied_job_id IS NOT NULL
          AND f.risk_tier IN ('T0','T1') AND f.decision_mode='automatic')
      ))
    OR
    (f.status='apply_failed' AND f.applied_operation_id IS NOT NULL
      AND f.applied_dedup_id IS NOT NULL AND f.applied_by IS NOT NULL
      AND f.applied_at IS NOT NULL AND f.applied_job_id IS NULL
      AND f.last_application_error_json IS NOT NULL)
    OR
    (f.status NOT IN ('applied','apply_failed')
      AND f.applied_operation_id IS NULL AND f.applied_dedup_id IS NULL
      AND f.applied_job_id IS NULL AND f.applied_by IS NULL
      AND f.applied_at IS NULL AND f.last_application_error_json IS NULL)
)
LIMIT 1
""".strip(),
    ),
    (
        "proposal_finding_consistency",
        """
SELECT p.id
FROM change_proposal AS p
JOIN curation_finding AS f ON f.id=p.finding_id
WHERE f.source_item_id<>p.target_item_id
   OR f.source_revision_id<>p.target_revision_id
   OR f.target_item_id IS NOT NULL OR f.target_revision_id IS NOT NULL
   OR f.finding_kind<>'update' OR f.proposed_action<>'update'
   OR NOT (
       (p.state='pending_review' AND f.status='pending')
       OR (p.state=f.status AND p.state IN ('approved','applied','apply_failed'))
       OR (p.state='obsolete' AND f.status IN ('rejected','ignored','obsolete'))
   )
LIMIT 1
""".strip(),
    ),
    (
        "lease_shape_consistency",
        """
SELECT l.item_id
FROM item_lease AS l
WHERE NOT (
    (l.holder_user_id IS NULL AND l.editor_session_id IS NULL
      AND l.token_hmac IS NULL AND l.hmac_key_id IS NULL
      AND l.base_item_row_version IS NULL AND l.base_revision_id IS NULL
      AND l.acquired_at IS NULL AND l.heartbeat_at IS NULL
      AND l.expires_at IS NULL AND l.absolute_expires_at IS NULL)
    OR
    (l.holder_user_id IS NOT NULL AND l.editor_session_id IS NOT NULL
      AND l.token_hmac IS NOT NULL AND l.hmac_key_id IS NOT NULL
      AND l.base_item_row_version IS NOT NULL AND l.base_revision_id IS NOT NULL
      AND l.acquired_at IS NOT NULL AND l.heartbeat_at IS NOT NULL
      AND l.expires_at IS NOT NULL AND l.absolute_expires_at IS NOT NULL
      AND l.acquired_at<=l.heartbeat_at AND l.heartbeat_at<=l.expires_at
      AND l.expires_at<=l.absolute_expires_at)
)
LIMIT 1
""".strip(),
    ),
    (
        "queue_claim_and_terminal_consistency",
        """
SELECT id FROM outbox_event
WHERE (state='processing')<>(claim_owner IS NOT NULL)
   OR (claim_owner IS NULL)<>(claim_expires_at IS NULL)
   OR (state IN ('applied','dead'))<>(completed_at IS NOT NULL)
UNION ALL
SELECT id FROM durable_job
WHERE (state IN ('claimed','running'))<>(claim_owner IS NOT NULL)
   OR (claim_owner IS NULL)<>(claim_expires_at IS NULL)
   OR (state IN ('succeeded','failed','cancelled','dead'))
      <>(completed_at IS NOT NULL)
UNION ALL
SELECT blob_sha256 FROM blob_gc_candidate
WHERE (state='claimed')<>(claim_owner IS NOT NULL)
   OR (claim_owner IS NULL)<>(claim_expires_at IS NULL)
LIMIT 1
""".strip(),
    ),
    (
        "search_visibility_consistency",
        """
SELECT i.id
FROM knowledge_item AS i
LEFT JOIN search_document AS s ON s.item_id=i.id
LEFT JOIN knowledge_revision AS r ON r.id=i.current_revision_id
LEFT JOIN knowledge_revision_body AS b ON b.revision_id=r.id
WHERE (i.lifecycle_status='active' AND i.curation_status='published'
       AND (s.item_id IS NULL OR s.space_id<>i.space_id
            OR s.revision_id<>i.current_revision_id
            OR r.id IS NULL OR b.revision_id IS NULL OR s.title<>r.title
            OR (b.content_md IS NOT NULL AND s.content_md<>b.content_md)))
   OR ((i.lifecycle_status<>'active' OR i.curation_status<>'published')
       AND s.item_id IS NOT NULL)
UNION ALL
SELECT s.item_id
FROM search_document AS s
LEFT JOIN knowledge_item AS i ON i.id=s.item_id
WHERE i.id IS NULL OR i.lifecycle_status<>'active'
   OR i.curation_status<>'published' OR i.space_id<>s.space_id
   OR i.current_revision_id<>s.revision_id
LIMIT 1
""".strip(),
    ),
    (
        "publication_preview_shape_consistency",
        """
SELECT p.id
FROM publication_preview AS p
WHERE NOT (
    (p.status IN ('queued','extracting') AND p.durable_job_id IS NOT NULL
      AND p.extraction_snapshot_id IS NULL AND p.source_snapshot_sha256 IS NULL
      AND p.deterministic_findings_json IS NULL AND p.ready_result_json IS NULL
      AND p.error_json IS NULL AND p.completed_at IS NULL)
    OR
    (p.status IN ('ready','consumed','expired')
      AND p.extraction_snapshot_id IS NOT NULL
      AND p.source_snapshot_sha256 IS NOT NULL
      AND p.deterministic_findings_json IS NOT NULL
      AND p.ready_result_json IS NOT NULL AND p.error_json IS NULL
      AND p.completed_at IS NOT NULL)
    OR
    (p.status='failed' AND p.durable_job_id IS NOT NULL
      AND p.extraction_snapshot_id IS NULL AND p.source_snapshot_sha256 IS NULL
      AND p.deterministic_findings_json IS NULL AND p.ready_result_json IS NULL
      AND p.error_json IS NOT NULL AND p.completed_at IS NOT NULL)
    OR
    (p.status='cancelled' AND p.completed_at IS NOT NULL
      AND p.error_json IS NULL AND (
        (p.durable_job_id IS NOT NULL AND p.extraction_snapshot_id IS NULL
          AND p.source_snapshot_sha256 IS NULL
          AND p.deterministic_findings_json IS NULL AND p.ready_result_json IS NULL)
        OR
        (p.extraction_snapshot_id IS NOT NULL AND p.source_snapshot_sha256 IS NOT NULL
          AND p.deterministic_findings_json IS NOT NULL
          AND p.ready_result_json IS NOT NULL)
      ))
)
LIMIT 1
""".strip(),
    ),
    (
        "active_reference_consistency",
        """
SELECT r.id
FROM item_relation AS r
LEFT JOIN knowledge_item AS s ON s.id=r.source_item_id
LEFT JOIN knowledge_item AS t ON t.id=r.target_item_id
LEFT JOIN knowledge_revision AS sr ON sr.id=r.source_revision_id
LEFT JOIN knowledge_revision AS tr ON tr.id=r.target_revision_id
WHERE r.relation_status='active' AND (
    s.id IS NULL OR t.id IS NULL OR sr.item_id<>s.id OR tr.item_id<>t.id
)
UNION ALL
SELECT e.id
FROM knowledge_edge AS e
LEFT JOIN knowledge_item AS s ON s.id=e.source_item_id
LEFT JOIN knowledge_item AS t ON t.id=e.target_item_id
WHERE e.edge_status='active' AND (
    s.id IS NULL OR t.id IS NULL OR s.space_id<>e.space_id OR t.space_id<>e.space_id
)
LIMIT 1
""".strip(),
    ),
)


POSTCONDITION_NAMES: tuple[str, ...] = tuple(
    name for name, _query in POSTCONDITION_DEFINITIONS
)
POSTCONDITION_QUERIES: tuple[str, ...] = tuple(
    query for _name, query in POSTCONDITION_DEFINITIONS
)

if len(POSTCONDITION_NAMES) != len(set(POSTCONDITION_NAMES)):  # pragma: no cover
    raise RuntimeError("The physical v1 invariant manifest contains duplicates.")


__all__ = [
    "POSTCONDITION_DEFINITIONS",
    "POSTCONDITION_NAMES",
    "POSTCONDITION_QUERIES",
]
