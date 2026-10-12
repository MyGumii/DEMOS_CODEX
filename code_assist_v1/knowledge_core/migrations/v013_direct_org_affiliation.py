"""Forward-only v13 direct Part/Team/Responsibility affiliation model.

Part membership remains mirrored for compatibility, while
``org_home_affiliation`` is the common current-home ledger.  Team leaders and
Responsibility executives have a direct higher-unit home and no synthetic
descendant Part membership.
"""

from __future__ import annotations


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


V013_STATEMENTS: tuple[str, ...] = (
    # An open v9 role represents the superseded descendant-Part authority
    # model.  It cannot be silently rewritten into a direct employment home.
    """
CREATE TABLE v013_open_role_preflight(
    open_role_count INTEGER NOT NULL CHECK(open_role_count=0)
) STRICT
""".strip(),
    """
INSERT INTO v013_open_role_preflight(open_role_count)
SELECT count(*) FROM higher_scope_role_assignment WHERE ended_at IS NULL
""".strip(),
    "DROP TABLE v013_open_role_preflight",
    f"""
CREATE TABLE org_home_affiliation(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    user_id TEXT NOT NULL CHECK({_user_id_check('user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    org_unit_id TEXT NOT NULL CHECK({_uuid_check('org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    home_kind TEXT NOT NULL CHECK(
        home_kind IN ('part','team','responsibility')
    ),
    valid_from INTEGER NOT NULL CHECK(valid_from >= 0),
    valid_to INTEGER CHECK(valid_to IS NULL OR valid_to > valid_from),
    assigned_by TEXT NOT NULL CHECK({_user_id_check('assigned_by')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(
        length(reason) BETWEEN 1 AND 1000 AND reason=trim(reason)
    ),
    row_version INTEGER NOT NULL CHECK(row_version IN (1,2)),
    CHECK(
        (valid_to IS NULL AND row_version=1)
        OR (valid_to IS NOT NULL AND row_version=2)
    )
) STRICT
""".strip(),
    """
INSERT INTO org_home_affiliation(
    id,user_id,org_unit_id,home_kind,valid_from,valid_to,
    assigned_by,reason,row_version
)
SELECT id,user_id,part_id,'part',valid_from,valid_to,
       assigned_by,reason,CASE WHEN valid_to IS NULL THEN 1 ELSE 2 END
FROM org_membership
ORDER BY valid_from,id
""".strip(),
    """
CREATE UNIQUE INDEX ux_org_home_affiliation_open_user
ON org_home_affiliation(user_id) WHERE valid_to IS NULL
""".strip(),
    """
CREATE INDEX ix_org_home_affiliation_unit_open
ON org_home_affiliation(org_unit_id,home_kind,valid_to,user_id,id)
""".strip(),
    """
CREATE INDEX ix_org_home_affiliation_user_history
ON org_home_affiliation(user_id,valid_from DESC,id)
""".strip(),
    """
CREATE TRIGGER trg_org_home_affiliation_validity_insert
BEFORE INSERT ON org_home_affiliation
FOR EACH ROW
WHEN NEW.valid_to IS NOT NULL OR NEW.row_version<>1
 OR NOT EXISTS (
   SELECT 1 FROM app_user u JOIN org_unit o ON o.id=NEW.org_unit_id
   WHERE u.id=NEW.user_id AND u.account_status='active'
     AND o.kind=NEW.home_kind AND o.status='active'
 )
 OR (NEW.home_kind='part' AND NOT EXISTS (
   SELECT 1 FROM org_membership m
   WHERE m.id=NEW.id AND m.user_id=NEW.user_id
     AND m.part_id=NEW.org_unit_id AND m.valid_from=NEW.valid_from
     AND m.valid_to IS NULL
 ))
 OR (NEW.home_kind IN ('team','responsibility') AND (
   EXISTS (
     SELECT 1 FROM app_user u
     WHERE u.id=NEW.user_id AND u.current_part_id IS NOT NULL
   )
   OR EXISTS (
     SELECT 1 FROM org_membership m
     WHERE m.user_id=NEW.user_id AND m.valid_to IS NULL
   )
 ))
BEGIN
  SELECT RAISE(ABORT, 'ORG_HOME_AFFILIATION_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_org_home_affiliation_close_update
BEFORE UPDATE ON org_home_affiliation
FOR EACH ROW
WHEN OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
 OR NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id
 OR NEW.org_unit_id IS NOT OLD.org_unit_id
 OR NEW.home_kind IS NOT OLD.home_kind
 OR NEW.valid_from IS NOT OLD.valid_from
 OR NEW.assigned_by IS NOT OLD.assigned_by
 OR NEW.reason IS NOT OLD.reason
 OR NEW.valid_to<=NEW.valid_from OR NEW.row_version<>2
 OR (OLD.home_kind IN ('team','responsibility') AND EXISTS (
   SELECT 1 FROM higher_scope_role_assignment h
   WHERE h.user_id=OLD.user_id AND h.org_unit_id=OLD.org_unit_id
     AND h.ended_at IS NULL
 ))
BEGIN
  SELECT RAISE(ABORT, 'ORG_HOME_AFFILIATION_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_org_home_affiliation_delete
BEFORE DELETE ON org_home_affiliation
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'HISTORY_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_org_membership_home_insert
AFTER INSERT ON org_membership
FOR EACH ROW
BEGIN
  INSERT INTO org_home_affiliation(
    id,user_id,org_unit_id,home_kind,valid_from,valid_to,
    assigned_by,reason,row_version
  ) VALUES(
    NEW.id,NEW.user_id,NEW.part_id,'part',NEW.valid_from,NULL,
    NEW.assigned_by,NEW.reason,1
  );
END
""".strip(),
    """
CREATE TRIGGER trg_org_membership_home_close
AFTER UPDATE OF valid_to ON org_membership
FOR EACH ROW
WHEN OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
BEGIN
  UPDATE org_home_affiliation
     SET valid_to=NEW.valid_to,row_version=2
   WHERE id=NEW.id AND user_id=NEW.user_id
     AND org_unit_id=NEW.part_id AND home_kind='part'
     AND valid_to IS NULL AND row_version=1;
  SELECT CASE WHEN changes()<>1
    THEN RAISE(ABORT, 'ORG_HOME_AFFILIATION_MIRROR_INVALID') END;
END
""".strip(),
    "DROP TRIGGER trg_app_user_part_update",
    """
CREATE TRIGGER trg_app_user_part_update
BEFORE UPDATE ON app_user
FOR EACH ROW
BEGIN
  SELECT CASE WHEN NEW.id IS NOT OLD.id OR (
    NEW.current_part_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM org_unit p
      WHERE p.id=NEW.current_part_id AND p.kind='part' AND p.status='active'
    )
  ) THEN RAISE(ABORT, 'USER_PART_INVALID') END;
  SELECT CASE WHEN (
    ((NEW.current_part_id IS NOT OLD.current_part_id)
      OR (NEW.account_status IS NOT OLD.account_status))
    AND NEW.scope_epoch <> OLD.scope_epoch + 1
  ) OR (
    NEW.current_part_id IS OLD.current_part_id
    AND NEW.account_status IS OLD.account_status
    AND NEW.scope_epoch NOT IN (OLD.scope_epoch,OLD.scope_epoch+1)
  ) THEN RAISE(ABORT, 'SCOPE_EPOCH_INVALID') END;
END
""".strip(),
    "DROP TRIGGER trg_higher_role_validity_insert",
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
   FROM org_home_affiliation f
   JOIN app_user u ON u.id=f.user_id
   JOIN org_unit o ON o.id=f.org_unit_id
   WHERE f.user_id=NEW.user_id AND f.org_unit_id=NEW.org_unit_id
     AND f.valid_to IS NULL AND u.account_status='active'
     AND u.current_part_id IS NULL AND o.status='active'
     AND ((NEW.role='team_leader' AND f.home_kind='team' AND o.kind='team')
       OR (NEW.role='responsibility_executive'
           AND f.home_kind='responsibility' AND o.kind='responsibility'))
 )
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_ASSIGNMENT_INVALID');
END
""".strip(),
    "DROP TRIGGER trg_org_unit_higher_role_guard_update",
    """
CREATE TRIGGER trg_org_unit_higher_role_guard_update
BEFORE UPDATE ON org_unit
FOR EACH ROW
WHEN (NEW.parent_id IS NOT OLD.parent_id
   OR (OLD.status='active' AND NEW.status='inactive'))
 AND EXISTS (
   SELECT 1 FROM higher_scope_role_assignment h
   WHERE h.org_unit_id=OLD.id AND h.ended_at IS NULL
 )
BEGIN
  SELECT RAISE(ABORT, 'HIGHER_ROLE_WRITE_GUARD');
END
""".strip(),
)


V013_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'org_home_affiliation_schema_invalid'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='org_home_affiliation'
)
OR (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND name IN (
    'ux_org_home_affiliation_open_user',
    'ix_org_home_affiliation_unit_open',
    'ix_org_home_affiliation_user_history'
  )
)<>3
OR (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_org_home_affiliation_validity_insert',
    'trg_org_home_affiliation_close_update',
    'trg_org_home_affiliation_delete',
    'trg_org_membership_home_insert',
    'trg_org_membership_home_close'
  )
)<>5
""".strip(),
    """
SELECT 'org_home_affiliation_part_mirror_invalid'
WHERE EXISTS (
  SELECT 1 FROM app_user u
  LEFT JOIN org_membership m
    ON m.user_id=u.id AND m.valid_to IS NULL
  LEFT JOIN org_home_affiliation f
    ON f.user_id=u.id AND f.valid_to IS NULL
  WHERE (u.current_part_id IS NULL AND m.id IS NOT NULL)
     OR (u.current_part_id IS NOT NULL AND (
       m.id IS NULL OR m.part_id<>u.current_part_id
       OR f.id IS NULL OR f.id<>m.id OR f.org_unit_id<>u.current_part_id
       OR f.home_kind<>'part'
     ))
     OR (u.current_part_id IS NULL AND f.home_kind='part')
)
""".strip(),
    """
SELECT 'org_home_affiliation_higher_state_invalid'
WHERE EXISTS (
  SELECT 1 FROM higher_scope_role_assignment h
  LEFT JOIN app_user u ON u.id=h.user_id
  LEFT JOIN org_home_affiliation f
    ON f.user_id=h.user_id AND f.valid_to IS NULL
  WHERE h.ended_at IS NULL AND (
    u.id IS NULL OR u.account_status<>'active' OR u.current_part_id IS NOT NULL
    OR f.id IS NULL OR f.id<>h.id OR f.org_unit_id<>h.org_unit_id
    OR (h.role='team_leader' AND f.home_kind<>'team')
    OR (h.role='responsibility_executive'
        AND f.home_kind<>'responsibility')
    OR EXISTS (
      SELECT 1 FROM org_membership m
      WHERE m.user_id=h.user_id AND m.valid_to IS NULL
    )
  )
)
OR EXISTS (
  SELECT 1 FROM org_home_affiliation f
  WHERE f.valid_to IS NULL AND f.home_kind IN ('team','responsibility')
    AND NOT EXISTS (
      SELECT 1 FROM higher_scope_role_assignment h
      WHERE h.user_id=f.user_id AND h.org_unit_id=f.org_unit_id
        AND h.ended_at IS NULL
        AND ((f.home_kind='team' AND h.role='team_leader')
          OR (f.home_kind='responsibility'
              AND h.role='responsibility_executive'))
    )
)
""".strip(),
)


__all__ = ["V013_POSTCONDITION_QUERIES", "V013_STATEMENTS"]
