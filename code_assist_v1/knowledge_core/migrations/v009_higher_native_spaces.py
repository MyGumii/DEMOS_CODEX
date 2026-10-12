"""Forward-only v9 higher-role, native-space, and distribution schema.

The migration rebuilds ``knowledge_space`` without the v7 higher-write ban,
while retaining every existing identifier and every unrelated constraint.  It
also provisions missing native organization spaces and adds fenced Team leader
and Responsibility executive episodes plus revision-bound distribution grants.
"""

from __future__ import annotations

from .v001_seed import RESPONSIBILITY_ROOT_ID


def _uuid_check(column: str) -> str:
    return f"""length({column})=36 AND substr({column},9,1)='-'
        AND substr({column},14,1)='-' AND substr({column},19,1)='-'
        AND substr({column},24,1)='-'
        AND length(replace({column},'-',''))=32
        AND replace({column},'-','') NOT GLOB '*[^0-9a-f]*'
        AND {column}=lower({column})"""


def _user_id_check(column: str) -> str:
    return f"""length({column}) BETWEEN 1 AND 64
        AND {column}=lower(trim({column}))
        AND {column} NOT GLOB '*[^a-z0-9._-]*'"""


V009_STATEMENTS: tuple[str, ...] = (
    f"""
CREATE TABLE higher_scope_role_assignment(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    org_unit_id TEXT NOT NULL CHECK({_uuid_check('org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    user_id TEXT NOT NULL CHECK({_user_id_check('user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK(
        role IN ('team_leader','responsibility_executive')
    ),
    assigned_by_user_id TEXT NOT NULL CHECK(
        {_user_id_check('assigned_by_user_id')}
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    assigned_at INTEGER NOT NULL CHECK(assigned_at >= 0),
    ended_at INTEGER CHECK(ended_at IS NULL OR ended_at > assigned_at),
    end_reason TEXT CHECK(
        end_reason IS NULL OR (
            length(end_reason) BETWEEN 1 AND 1000
            AND end_reason=trim(end_reason)
        )
    ),
    assignment_generation INTEGER NOT NULL CHECK(assignment_generation >= 1),
    created_at INTEGER NOT NULL CHECK(created_at=assigned_at),
    updated_at INTEGER NOT NULL CHECK(updated_at >= created_at),
    row_version INTEGER NOT NULL CHECK(row_version IN (1,2)),
    CHECK(
        (ended_at IS NULL AND end_reason IS NULL AND row_version=1
          AND updated_at=assigned_at)
        OR
        (ended_at IS NOT NULL AND end_reason IS NOT NULL AND row_version=2
          AND updated_at=ended_at)
    )
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_higher_role_open_unit_role
ON higher_scope_role_assignment(org_unit_id,role)
WHERE ended_at IS NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_higher_role_unit_generation
ON higher_scope_role_assignment(org_unit_id,role,assignment_generation)
""".strip(),
    """
CREATE INDEX ix_higher_role_user_open
ON higher_scope_role_assignment(user_id,ended_at,org_unit_id,role)
""".strip(),
    """
CREATE INDEX ix_higher_role_unit_history
ON higher_scope_role_assignment(
    org_unit_id,role,assignment_generation DESC,id
)
""".strip(),
    "DROP TRIGGER trg_knowledge_space_row_version_update",
    "DROP TRIGGER trg_knowledge_space_owner_insert",
    "DROP TRIGGER trg_knowledge_space_owner_update",
    "DROP TRIGGER trg_knowledge_space_kind_disabled_insert",
    "DROP TRIGGER trg_knowledge_space_kind_disabled_update",
    f"""
CREATE TABLE knowledge_space_v9_new(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    space_kind TEXT NOT NULL CHECK(
        space_kind IN ('personal','part','team','responsibility')
    ),
    owner_user_id TEXT CHECK(
        owner_user_id IS NULL OR ({_user_id_check('owner_user_id')})
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    owner_org_unit_id TEXT CHECK(
        owner_org_unit_id IS NULL OR ({_uuid_check('owner_org_unit_id')})
    ) REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('active','frozen','inactive')),
    write_enabled INTEGER NOT NULL DEFAULT 0 CHECK(write_enabled IN (0,1)),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(
        (space_kind='personal' AND owner_user_id IS NOT NULL
          AND owner_org_unit_id IS NULL)
        OR
        (space_kind<>'personal' AND owner_user_id IS NULL
          AND owner_org_unit_id IS NOT NULL)
    ),
    CHECK(write_enabled=0 OR status='active')
) STRICT
""".strip(),
    """
INSERT INTO knowledge_space_v9_new(
    id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
    generation,created_at,updated_at,row_version
)
SELECT id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
       generation,created_at,updated_at,row_version
FROM knowledge_space
ORDER BY id
""".strip(),
    "DROP TABLE knowledge_space",
    "ALTER TABLE knowledge_space_v9_new RENAME TO knowledge_space",
    """
CREATE UNIQUE INDEX ux_space_org_owner
ON knowledge_space(owner_org_unit_id)
WHERE space_kind<>'personal'
""".strip(),
    """
CREATE INDEX ix_space_kind_status
ON knowledge_space(space_kind,status,id)
""".strip(),
    """
CREATE TRIGGER trg_knowledge_space_row_version_update
BEFORE UPDATE ON knowledge_space
FOR EACH ROW
WHEN NEW.row_version <> OLD.row_version + 1
BEGIN
  SELECT RAISE(ABORT, 'ROW_VERSION_CONFLICT');
END
""".strip(),
    """
CREATE TRIGGER trg_knowledge_space_owner_insert
BEFORE INSERT ON knowledge_space
FOR EACH ROW
WHEN NOT (
  (NEW.space_kind='personal' AND NEW.owner_user_id IS NOT NULL
    AND NEW.owner_org_unit_id IS NULL)
  OR (NEW.space_kind IN ('part','team','responsibility')
    AND NEW.owner_user_id IS NULL AND EXISTS (
      SELECT 1 FROM org_unit o
      WHERE o.id=NEW.owner_org_unit_id AND o.kind=NEW.space_kind
    ))
) OR (NEW.write_enabled=1 AND (
  NEW.status<>'active'
  OR (NEW.space_kind<>'personal' AND NOT EXISTS (
    SELECT 1 FROM org_unit o
    WHERE o.id=NEW.owner_org_unit_id AND o.status='active'
  ))
  OR (NEW.space_kind='part' AND (
    SELECT count(*) FROM org_role_assignment r
    WHERE r.org_unit_id=NEW.owner_org_unit_id AND r.role='part_leader'
      AND r.valid_to IS NULL
  )<>1)
  OR (NEW.space_kind='team' AND (
    SELECT count(*) FROM higher_scope_role_assignment h
    WHERE h.org_unit_id=NEW.owner_org_unit_id AND h.role='team_leader'
      AND h.ended_at IS NULL
  )<>1)
  OR (NEW.space_kind='responsibility' AND (
    SELECT count(*) FROM higher_scope_role_assignment h
    WHERE h.org_unit_id=NEW.owner_org_unit_id
      AND h.role='responsibility_executive' AND h.ended_at IS NULL
  )<>1)
))
BEGIN
  SELECT RAISE(ABORT, 'SPACE_OWNER_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_knowledge_space_owner_update
BEFORE UPDATE ON knowledge_space
FOR EACH ROW
WHEN NEW.id IS NOT OLD.id OR NEW.space_kind IS NOT OLD.space_kind
 OR NEW.owner_user_id IS NOT OLD.owner_user_id
 OR NEW.owner_org_unit_id IS NOT OLD.owner_org_unit_id
 OR (NEW.write_enabled=1 AND (
  NEW.status<>'active'
  OR (NEW.space_kind<>'personal' AND NOT EXISTS (
    SELECT 1 FROM org_unit o
    WHERE o.id=NEW.owner_org_unit_id AND o.status='active'
  ))
  OR (NEW.space_kind='part' AND (
    SELECT count(*) FROM org_role_assignment r
    WHERE r.org_unit_id=NEW.owner_org_unit_id AND r.role='part_leader'
      AND r.valid_to IS NULL
  )<>1)
  OR (NEW.space_kind='team' AND (
    SELECT count(*) FROM higher_scope_role_assignment h
    WHERE h.org_unit_id=NEW.owner_org_unit_id AND h.role='team_leader'
      AND h.ended_at IS NULL
  )<>1)
  OR (NEW.space_kind='responsibility' AND (
    SELECT count(*) FROM higher_scope_role_assignment h
    WHERE h.org_unit_id=NEW.owner_org_unit_id
      AND h.role='responsibility_executive' AND h.ended_at IS NULL
  )<>1)
 ))
BEGIN
  SELECT RAISE(ABORT, 'SPACE_OWNER_INVALID');
END
""".strip(),
    f"""
INSERT INTO knowledge_space(
    id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
    generation,created_at,updated_at,row_version
)
SELECT o.id,o.kind,NULL,o.id,'active',0,0,
       CASE WHEN o.id='{RESPONSIBILITY_ROOT_ID}' THEN 0 ELSE o.created_at END,
       CASE
         WHEN o.id='{RESPONSIBILITY_ROOT_ID}' THEN 0
         WHEN o.updated_at<o.created_at THEN o.created_at
         ELSE o.updated_at
       END,
       1
FROM org_unit o
WHERE o.status='active'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_space s WHERE s.owner_org_unit_id=o.id
  )
ORDER BY o.kind,o.id
""".strip(),
    f"""
CREATE TABLE knowledge_distribution_grant(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    source_space_id TEXT NOT NULL CHECK({_uuid_check('source_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK({_uuid_check('source_item_id')}),
    source_revision_id TEXT NOT NULL CHECK({_uuid_check('source_revision_id')}),
    target_org_unit_id TEXT NOT NULL CHECK({_uuid_check('target_org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_scope_kind TEXT NOT NULL CHECK(
        target_scope_kind IN ('org_unit','native_space')
    ),
    target_scope_id TEXT NOT NULL CHECK({_uuid_check('target_scope_id')}),
    grant_mode TEXT NOT NULL CHECK(grant_mode='read_only'),
    source_role_assignment_id TEXT NOT NULL CHECK(
        {_uuid_check('source_role_assignment_id')}
    ) REFERENCES higher_scope_role_assignment(id)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_by_user_id TEXT NOT NULL CHECK(
        {_user_id_check('created_by_user_id')}
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid_check('operation_id')}),
    effect_key TEXT NOT NULL CHECK(
        length(effect_key)=64 AND effect_key=lower(effect_key)
        AND effect_key NOT GLOB '*[^0-9a-f]*'
    ),
    reason TEXT NOT NULL CHECK(
        length(reason) BETWEEN 1 AND 1000 AND reason=trim(reason)
    ),
    created_audit_event_id INTEGER NOT NULL REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at > created_at),
    revoked_by_user_id TEXT CHECK(
        revoked_by_user_id IS NULL OR ({_user_id_check('revoked_by_user_id')})
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    revoke_mode TEXT CHECK(
        revoke_mode IS NULL OR revoke_mode IN ('ordinary','break_glass')
    ),
    revoke_role_assignment_id TEXT CHECK(
        revoke_role_assignment_id IS NULL OR (
            {_uuid_check('revoke_role_assignment_id')}
        )
    ) REFERENCES higher_scope_role_assignment(id)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
    revoke_reason TEXT CHECK(
        revoke_reason IS NULL OR (
            length(revoke_reason) BETWEEN 1 AND 1000
            AND revoke_reason=trim(revoke_reason)
        )
    ),
    revoke_confirmation_sha256 TEXT CHECK(
        revoke_confirmation_sha256 IS NULL OR (
            length(revoke_confirmation_sha256)=64
            AND revoke_confirmation_sha256=lower(revoke_confirmation_sha256)
            AND revoke_confirmation_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    revoke_operation_id TEXT CHECK(
        revoke_operation_id IS NULL OR ({_uuid_check('revoke_operation_id')})
    ),
    revoke_effect_key TEXT CHECK(
        revoke_effect_key IS NULL OR (
            length(revoke_effect_key)=64
            AND revoke_effect_key=lower(revoke_effect_key)
            AND revoke_effect_key NOT GLOB '*[^0-9a-f]*'
        )
    ),
    revoked_audit_event_id INTEGER REFERENCES audit_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    revoked_outbox_event_id INTEGER REFERENCES outbox_event(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    updated_at INTEGER NOT NULL CHECK(updated_at >= created_at),
    row_version INTEGER NOT NULL CHECK(row_version IN (1,2)),
    FOREIGN KEY(source_item_id,source_space_id)
        REFERENCES knowledge_item(id,space_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK(
      (revoked_at IS NULL AND revoked_by_user_id IS NULL
       AND revoke_mode IS NULL AND revoke_role_assignment_id IS NULL
       AND revoke_reason IS NULL AND revoke_confirmation_sha256 IS NULL
       AND revoke_operation_id IS NULL AND revoke_effect_key IS NULL
       AND revoked_audit_event_id IS NULL AND revoked_outbox_event_id IS NULL
       AND row_version=1 AND updated_at=created_at)
      OR
      (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL
       AND revoke_mode IS NOT NULL AND revoke_reason IS NOT NULL
       AND revoke_operation_id IS NOT NULL AND revoke_effect_key IS NOT NULL
       AND revoked_audit_event_id IS NOT NULL
       AND revoked_outbox_event_id IS NOT NULL AND row_version=2
       AND updated_at=revoked_at
       AND ((revoke_mode='ordinary'
             AND revoke_role_assignment_id IS NOT NULL
             AND revoke_confirmation_sha256 IS NULL)
            OR (revoke_mode='break_glass'
             AND revoke_role_assignment_id IS NULL
             AND revoke_confirmation_sha256 IS NOT NULL)))
    )
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_distribution_effect_key
ON knowledge_distribution_grant(effect_key)
""".strip(),
    """
CREATE UNIQUE INDEX ux_distribution_revoke_effect_key
ON knowledge_distribution_grant(revoke_effect_key)
WHERE revoke_effect_key IS NOT NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_distribution_active_revision_target
ON knowledge_distribution_grant(
    source_revision_id,target_scope_kind,target_scope_id
)
WHERE revoked_at IS NULL
""".strip(),
    """
CREATE INDEX ix_distribution_source_history
ON knowledge_distribution_grant(source_space_id,created_at DESC,id)
""".strip(),
    """
CREATE INDEX ix_distribution_target_active
ON knowledge_distribution_grant(
    target_org_unit_id,revoked_at,source_space_id,source_item_id,id
)
""".strip(),
    """
CREATE TRIGGER trg_higher_role_validity_insert
BEFORE INSERT ON higher_scope_role_assignment
FOR EACH ROW
WHEN NEW.ended_at IS NOT NULL OR NEW.end_reason IS NOT NULL
 OR NEW.row_version<>1 OR NEW.updated_at<>NEW.assigned_at
 OR NOT EXISTS (
   SELECT 1 FROM app_user a
   WHERE a.id=NEW.assigned_by_user_id AND a.account_status='active'
     AND a.system_role='admin'
 )
 OR NOT EXISTS (
   SELECT 1
   FROM org_unit o
   JOIN app_user u ON u.id=NEW.user_id AND u.account_status='active'
   JOIN org_unit p ON p.id=u.current_part_id
                  AND p.kind='part' AND p.status='active'
   LEFT JOIN org_unit t ON t.id=p.parent_id AND t.kind='team'
   WHERE o.id=NEW.org_unit_id AND o.status='active'
     AND ((NEW.role='team_leader' AND o.kind='team'
           AND p.parent_id=o.id)
       OR (NEW.role='responsibility_executive'
           AND o.kind='responsibility' AND t.parent_id=o.id))
 )
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_ASSIGNMENT_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_role_generation_insert
BEFORE INSERT ON higher_scope_role_assignment
FOR EACH ROW
WHEN NEW.assignment_generation<>(
  SELECT coalesce(max(h.assignment_generation),0)+1
  FROM higher_scope_role_assignment h
  WHERE h.org_unit_id=NEW.org_unit_id AND h.role=NEW.role
)
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_GENERATION_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_role_close_update
BEFORE UPDATE ON higher_scope_role_assignment
FOR EACH ROW
WHEN OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL
 OR NEW.id IS NOT OLD.id OR NEW.org_unit_id IS NOT OLD.org_unit_id
 OR NEW.user_id IS NOT OLD.user_id OR NEW.role IS NOT OLD.role
 OR NEW.assigned_by_user_id IS NOT OLD.assigned_by_user_id
 OR NEW.assigned_at IS NOT OLD.assigned_at
 OR NEW.assignment_generation IS NOT OLD.assignment_generation
 OR NEW.created_at IS NOT OLD.created_at
 OR NEW.end_reason IS NULL OR NEW.ended_at<=NEW.assigned_at
 OR NEW.updated_at<>NEW.ended_at
 OR NEW.row_version<>OLD.row_version+1
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_EPISODE_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_role_write_guard_update
BEFORE UPDATE ON higher_scope_role_assignment
FOR EACH ROW
WHEN OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL AND EXISTS (
  SELECT 1 FROM knowledge_space s
  WHERE s.owner_org_unit_id=OLD.org_unit_id AND s.write_enabled=1
    AND ((OLD.role='team_leader' AND s.space_kind='team')
      OR (OLD.role='responsibility_executive'
          AND s.space_kind='responsibility'))
)
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_WRITE_GUARD');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_role_delete
BEFORE DELETE ON higher_scope_role_assignment
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'HISTORY_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_app_user_higher_role_guard_update
BEFORE UPDATE ON app_user
FOR EACH ROW
WHEN (NEW.current_part_id IS NOT OLD.current_part_id
   OR (OLD.account_status='active' AND NEW.account_status='disabled'))
 AND EXISTS (
   SELECT 1 FROM higher_scope_role_assignment h
   WHERE h.user_id=OLD.id AND h.ended_at IS NULL
 )
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_WRITE_GUARD');
END
""".strip(),
    """
CREATE TRIGGER trg_org_membership_higher_role_guard_update
BEFORE UPDATE ON org_membership
FOR EACH ROW
WHEN OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL AND EXISTS (
  SELECT 1 FROM higher_scope_role_assignment h
  WHERE h.user_id=OLD.user_id AND h.ended_at IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_WRITE_GUARD');
END
""".strip(),
    """
CREATE TRIGGER trg_org_unit_higher_role_guard_update
BEFORE UPDATE ON org_unit
FOR EACH ROW
WHEN (NEW.parent_id IS NOT OLD.parent_id
   OR (OLD.status='active' AND NEW.status='inactive'))
 AND (
   EXISTS (
     SELECT 1 FROM higher_scope_role_assignment h
     WHERE h.org_unit_id=OLD.id AND h.ended_at IS NULL
   )
   OR (OLD.kind='part' AND EXISTS (
     SELECT 1 FROM higher_scope_role_assignment h
     JOIN app_user u ON u.id=h.user_id
     WHERE h.ended_at IS NULL AND u.current_part_id=OLD.id
   ))
   OR (OLD.kind='team' AND EXISTS (
     SELECT 1 FROM higher_scope_role_assignment h
     JOIN app_user u ON u.id=h.user_id
     JOIN org_unit p ON p.id=u.current_part_id
     WHERE h.ended_at IS NULL AND p.parent_id=OLD.id
   ))
 )
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_WRITE_GUARD');
END
""".strip(),
    """
CREATE TRIGGER trg_distribution_grant_insert
BEFORE INSERT ON knowledge_distribution_grant
FOR EACH ROW
WHEN NEW.revoked_at IS NOT NULL OR NEW.row_version<>1
 OR NOT EXISTS (
   SELECT 1
   FROM knowledge_space s
   JOIN knowledge_item i
     ON i.id=NEW.source_item_id AND i.space_id=s.id
   JOIN knowledge_revision r
     ON r.id=NEW.source_revision_id AND r.item_id=i.id
   JOIN higher_scope_role_assignment h
     ON h.id=NEW.source_role_assignment_id
    AND h.org_unit_id=s.owner_org_unit_id
    AND h.user_id=NEW.created_by_user_id AND h.ended_at IS NULL
   WHERE s.id=NEW.source_space_id AND s.status='active'
     AND i.current_revision_id=r.id AND i.lifecycle_status='active'
     AND ((s.space_kind='team' AND h.role='team_leader')
       OR (s.space_kind='responsibility'
           AND h.role='responsibility_executive'))
 )
 OR NOT EXISTS (
   WITH RECURSIVE descendants(id) AS (
     SELECT o.id FROM org_unit o
     JOIN knowledge_space s ON s.owner_org_unit_id=o.parent_id
     WHERE s.id=NEW.source_space_id AND o.status='active'
     UNION ALL
     SELECT o.id FROM org_unit o JOIN descendants d ON o.parent_id=d.id
     WHERE o.status='active'
   )
   SELECT 1 FROM descendants d
   WHERE d.id=NEW.target_org_unit_id
 )
 OR (NEW.target_scope_kind='org_unit'
     AND NEW.target_scope_id<>NEW.target_org_unit_id)
 OR (NEW.target_scope_kind='native_space' AND NOT EXISTS (
   SELECT 1 FROM knowledge_space t
   WHERE t.id=NEW.target_scope_id
     AND t.owner_org_unit_id=NEW.target_org_unit_id
     AND t.space_kind<>'personal' AND t.status='active'
 ))
BEGIN
  SELECT RAISE(ABORT, 'DISTRIBUTION_GRANT_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_distribution_revoke_update
BEFORE UPDATE ON knowledge_distribution_grant
FOR EACH ROW
WHEN OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
 OR NEW.id IS NOT OLD.id
 OR NEW.source_space_id IS NOT OLD.source_space_id
 OR NEW.source_item_id IS NOT OLD.source_item_id
 OR NEW.source_revision_id IS NOT OLD.source_revision_id
 OR NEW.target_org_unit_id IS NOT OLD.target_org_unit_id
 OR NEW.target_scope_kind IS NOT OLD.target_scope_kind
 OR NEW.target_scope_id IS NOT OLD.target_scope_id
 OR NEW.grant_mode IS NOT OLD.grant_mode
 OR NEW.source_role_assignment_id IS NOT OLD.source_role_assignment_id
 OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
 OR NEW.operation_id IS NOT OLD.operation_id
 OR NEW.effect_key IS NOT OLD.effect_key
 OR NEW.reason IS NOT OLD.reason
 OR NEW.created_audit_event_id IS NOT OLD.created_audit_event_id
 OR NEW.created_outbox_event_id IS NOT OLD.created_outbox_event_id
 OR NEW.created_at IS NOT OLD.created_at
 OR NEW.updated_at<>NEW.revoked_at
 OR NEW.row_version<>OLD.row_version+1
 OR (NEW.revoke_mode='ordinary' AND NOT EXISTS (
   SELECT 1
   FROM higher_scope_role_assignment h
   JOIN knowledge_space s ON s.owner_org_unit_id=h.org_unit_id
   WHERE h.id=NEW.revoke_role_assignment_id
     AND h.user_id=NEW.revoked_by_user_id AND h.ended_at IS NULL
     AND s.id=NEW.source_space_id
     AND ((s.space_kind='team' AND h.role='team_leader')
       OR (s.space_kind='responsibility'
           AND h.role='responsibility_executive'))
 ))
 OR (NEW.revoke_mode='break_glass' AND NOT EXISTS (
   SELECT 1 FROM app_user u
   WHERE u.id=NEW.revoked_by_user_id AND u.account_status='active'
     AND u.system_role='admin'
 ))
BEGIN
  SELECT RAISE(ABORT, 'DISTRIBUTION_REVOKE_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_distribution_delete
BEFORE DELETE ON knowledge_distribution_grant
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'HISTORY_IMMUTABLE');
END
""".strip(),
)


V009_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'higher_schema_table_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='table' AND name IN (
    'higher_scope_role_assignment','knowledge_distribution_grant'
  )
)<>2
OR EXISTS (
  SELECT 1 FROM sqlite_schema WHERE name='knowledge_space_v9_new'
)
""".strip(),
    """
SELECT 'knowledge_space_v9_shape_invalid'
WHERE EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='knowledge_space'
    AND sql LIKE '%space_kind IN (''personal'',''part'') OR write_enabled=0%'
)
OR EXISTS (
  SELECT 1 FROM knowledge_space s
  LEFT JOIN org_unit o ON o.id=s.owner_org_unit_id
  WHERE NOT (
    (s.space_kind='personal' AND s.owner_user_id IS NOT NULL
      AND s.owner_org_unit_id IS NULL)
    OR (s.space_kind IN ('part','team','responsibility')
      AND s.owner_user_id IS NULL AND o.id IS NOT NULL
      AND o.kind=s.space_kind)
  )
)
OR EXISTS (
  SELECT o.id FROM org_unit o
  LEFT JOIN knowledge_space s ON s.owner_org_unit_id=o.id
  WHERE o.status='active'
  GROUP BY o.id
  HAVING count(s.id)<>1
)
""".strip(),
    """
SELECT 'higher_role_index_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND name IN (
    'ux_higher_role_open_unit_role','ux_higher_role_unit_generation',
    'ix_higher_role_user_open','ix_higher_role_unit_history'
  )
)<>4
OR (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_higher_role_validity_insert','trg_higher_role_generation_insert',
    'trg_higher_role_close_update','trg_higher_role_write_guard_update',
    'trg_higher_role_delete','trg_app_user_higher_role_guard_update',
    'trg_org_membership_higher_role_guard_update',
    'trg_org_unit_higher_role_guard_update'
  )
)<>8
""".strip(),
    """
SELECT 'higher_role_state_invalid'
WHERE EXISTS (
  SELECT 1
  FROM higher_scope_role_assignment h
  JOIN org_unit o ON o.id=h.org_unit_id
  LEFT JOIN app_user u ON u.id=h.user_id
  LEFT JOIN org_unit p ON p.id=u.current_part_id
  LEFT JOIN org_unit t ON t.id=p.parent_id
  WHERE h.ended_at IS NULL AND (
    u.account_status<>'active' OR p.kind<>'part' OR p.status<>'active'
    OR o.status<>'active'
    OR (h.role='team_leader'
        AND (o.kind<>'team' OR p.parent_id<>o.id))
    OR (h.role='responsibility_executive'
        AND (o.kind<>'responsibility' OR t.parent_id<>o.id))
  )
)
OR EXISTS (
  SELECT org_unit_id,role
  FROM higher_scope_role_assignment
  GROUP BY org_unit_id,role
  HAVING min(assignment_generation)<>1
      OR max(assignment_generation)<>count(*)
      OR count(DISTINCT assignment_generation)<>count(*)
)
OR EXISTS (
  SELECT org_unit_id,role
  FROM higher_scope_role_assignment
  WHERE ended_at IS NULL
  GROUP BY org_unit_id,role HAVING count(*)<>1
)
""".strip(),
    """
SELECT 'higher_space_write_state_invalid'
WHERE EXISTS (
  SELECT 1
  FROM knowledge_space s
  JOIN org_unit o ON o.id=s.owner_org_unit_id
  WHERE s.space_kind IN ('team','responsibility') AND s.write_enabled=1
    AND (s.status<>'active' OR o.status<>'active' OR (
      SELECT count(*) FROM higher_scope_role_assignment h
      WHERE h.org_unit_id=o.id AND h.ended_at IS NULL
        AND ((s.space_kind='team' AND h.role='team_leader')
          OR (s.space_kind='responsibility'
              AND h.role='responsibility_executive'))
    )<>1)
)
""".strip(),
    """
SELECT 'distribution_index_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND name IN (
    'ux_distribution_effect_key','ux_distribution_revoke_effect_key',
    'ux_distribution_active_revision_target',
    'ix_distribution_source_history','ix_distribution_target_active'
  )
)<>5
OR (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_distribution_grant_insert','trg_distribution_revoke_update',
    'trg_distribution_delete'
  )
)<>3
""".strip(),
    """
SELECT 'distribution_state_invalid'
WHERE EXISTS (
  SELECT 1
  FROM knowledge_distribution_grant g
  JOIN knowledge_space s ON s.id=g.source_space_id
  JOIN knowledge_item i
    ON i.id=g.source_item_id AND i.space_id=g.source_space_id
  JOIN knowledge_revision r
    ON r.id=g.source_revision_id AND r.item_id=g.source_item_id
  JOIN org_unit target ON target.id=g.target_org_unit_id
  WHERE s.space_kind NOT IN ('team','responsibility')
     OR target.status<>'active'
     OR (g.target_scope_kind='org_unit'
         AND g.target_scope_id<>g.target_org_unit_id)
     OR (g.target_scope_kind='native_space' AND NOT EXISTS (
       SELECT 1 FROM knowledge_space ts
       WHERE ts.id=g.target_scope_id
         AND ts.owner_org_unit_id=g.target_org_unit_id
     ))
)
""".strip(),
)


__all__ = ["V009_POSTCONDITION_QUERIES", "V009_STATEMENTS"]
