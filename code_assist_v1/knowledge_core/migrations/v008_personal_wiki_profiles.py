"""Forward-only v8 personal-Wiki profile collection.

Versions 1 through 7 remain frozen.  Version 8 preserves every canonical
``knowledge_space`` identity and dependent row while replacing the legacy
one-personal-space-per-owner index with an owner-scoped Wiki profile.  The
profile is deliberately lifecycle metadata only; item, revision, conversation,
graph, and projection foreign keys continue to point at the stable space ID.
"""

from __future__ import annotations


V008_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE personal_wiki_profile(
    space_id TEXT PRIMARY KEY NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    owner_user_id TEXT NOT NULL CHECK(
        length(owner_user_id) BETWEEN 1 AND 64
        AND owner_user_id=lower(trim(owner_user_id))
        AND owner_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    display_name TEXT NOT NULL CHECK(
        length(display_name) BETWEEN 1 AND 120
        AND display_name=trim(display_name)
    ),
    name_key TEXT NOT NULL CHECK(
        length(name_key) BETWEEN 1 AND 160
        AND name_key=trim(name_key)
    ),
    lifecycle_status TEXT NOT NULL CHECK(
        lifecycle_status IN ('active','archived')
    ),
    lifecycle_generation INTEGER NOT NULL CHECK(lifecycle_generation >= 1),
    is_default INTEGER NOT NULL CHECK(is_default IN (0,1)),
    archived_at INTEGER CHECK(archived_at IS NULL OR archived_at >= 0),
    archived_by_user_id TEXT CHECK(
        archived_by_user_id IS NULL OR (
            length(archived_by_user_id) BETWEEN 1 AND 64
            AND archived_by_user_id=lower(trim(archived_by_user_id))
            AND archived_by_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    archive_reason TEXT CHECK(
        archive_reason IS NULL OR (
            length(archive_reason) BETWEEN 1 AND 1000
            AND archive_reason=trim(archive_reason)
        )
    ),
    created_at INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL CHECK(updated_at >= created_at),
    row_version INTEGER NOT NULL CHECK(row_version >= 1),
    CHECK(
        (lifecycle_status='active' AND is_default IN (0,1)
          AND archived_at IS NULL AND archived_by_user_id IS NULL
          AND archive_reason IS NULL)
        OR
        (lifecycle_status='archived' AND is_default=0
          AND archived_at IS NOT NULL
          AND archived_by_user_id=owner_user_id)
    )
) STRICT
""".strip(),
    """
INSERT INTO personal_wiki_profile(
    space_id,owner_user_id,display_name,name_key,lifecycle_status,
    lifecycle_generation,is_default,archived_at,archived_by_user_id,
    archive_reason,created_at,updated_at,row_version
)
SELECT id,owner_user_id,'내 지식','내 지식','active',1,1,NULL,NULL,NULL,
       created_at,CASE WHEN updated_at<created_at THEN created_at ELSE updated_at END,1
FROM knowledge_space
WHERE space_kind='personal'
ORDER BY owner_user_id,id
""".strip(),
    "DROP INDEX ux_space_personal_owner",
    """
CREATE UNIQUE INDEX ux_personal_wiki_owner_name_key
ON personal_wiki_profile(owner_user_id,name_key)
""".strip(),
    """
CREATE UNIQUE INDEX ux_personal_wiki_active_default
ON personal_wiki_profile(owner_user_id)
WHERE lifecycle_status='active' AND is_default=1
""".strip(),
    """
CREATE INDEX ix_personal_wiki_active_name_cursor
ON personal_wiki_profile(owner_user_id,lifecycle_status,name_key,space_id)
WHERE lifecycle_status='active'
""".strip(),
    """
CREATE INDEX ix_personal_wiki_active_recent_cursor
ON personal_wiki_profile(owner_user_id,lifecycle_status,updated_at DESC,space_id)
WHERE lifecycle_status='active'
""".strip(),
    """
CREATE INDEX ix_personal_wiki_archived_cursor
ON personal_wiki_profile(owner_user_id,lifecycle_status,archived_at DESC,space_id)
WHERE lifecycle_status='archived'
""".strip(),
    """
CREATE TRIGGER trg_personal_wiki_profile_owner_insert
BEFORE INSERT ON personal_wiki_profile
FOR EACH ROW
WHEN NOT EXISTS (
  SELECT 1 FROM knowledge_space s
  WHERE s.id=NEW.space_id AND s.space_kind='personal'
    AND s.owner_user_id=NEW.owner_user_id AND s.owner_org_unit_id IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'PERSONAL_WIKI_OWNER_MISMATCH');
END
""".strip(),
    """
CREATE TRIGGER trg_personal_wiki_profile_owner_update
BEFORE UPDATE ON personal_wiki_profile
FOR EACH ROW
WHEN NEW.space_id IS NOT OLD.space_id
 OR NEW.owner_user_id IS NOT OLD.owner_user_id
 OR NOT EXISTS (
   SELECT 1 FROM knowledge_space s
   WHERE s.id=NEW.space_id AND s.space_kind='personal'
     AND s.owner_user_id=NEW.owner_user_id AND s.owner_org_unit_id IS NULL
 )
BEGIN
  SELECT RAISE(ABORT, 'PERSONAL_WIKI_OWNER_MISMATCH');
END
""".strip(),
    """
CREATE TRIGGER trg_personal_wiki_profile_lifecycle_update
BEFORE UPDATE ON personal_wiki_profile
FOR EACH ROW
WHEN (
  (NEW.lifecycle_status<>OLD.lifecycle_status
    AND NEW.lifecycle_generation<>OLD.lifecycle_generation+1)
  OR
  (NEW.lifecycle_status=OLD.lifecycle_status
    AND NEW.lifecycle_generation<>OLD.lifecycle_generation)
)
BEGIN
  SELECT RAISE(ABORT, 'PERSONAL_WIKI_LIFECYCLE_GENERATION_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_personal_wiki_profile_delete
BEFORE DELETE ON personal_wiki_profile
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'PERSONAL_WIKI_PROFILE_IMMUTABLE');
END
""".strip(),
)


V008_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'personal_wiki_profile_table_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='personal_wiki_profile'
)
""".strip(),
    """
SELECT 'legacy_personal_owner_index_present'
WHERE EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='index' AND name='ux_space_personal_owner'
)
""".strip(),
    """
SELECT 'personal_wiki_profile_index_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND name IN (
    'ux_personal_wiki_owner_name_key',
    'ux_personal_wiki_active_default',
    'ix_personal_wiki_active_name_cursor',
    'ix_personal_wiki_active_recent_cursor',
    'ix_personal_wiki_archived_cursor'
  )
)<>5
""".strip(),
    """
SELECT 'personal_wiki_profile_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_personal_wiki_profile_owner_insert',
    'trg_personal_wiki_profile_owner_update',
    'trg_personal_wiki_profile_lifecycle_update',
    'trg_personal_wiki_profile_delete'
  )
)<>4
""".strip(),
    """
SELECT 'personal_space_profile_bijection_invalid'
WHERE EXISTS (
  SELECT 1 FROM knowledge_space s
  LEFT JOIN personal_wiki_profile p ON p.space_id=s.id
  WHERE s.space_kind='personal' AND p.space_id IS NULL
)
OR EXISTS (
  SELECT 1 FROM personal_wiki_profile p
  LEFT JOIN knowledge_space s ON s.id=p.space_id
  WHERE s.id IS NULL OR s.space_kind<>'personal'
     OR s.owner_user_id<>p.owner_user_id OR s.owner_org_unit_id IS NOT NULL
)
""".strip(),
    """
SELECT 'personal_wiki_exact_default_invalid'
WHERE EXISTS (
  SELECT p.owner_user_id
  FROM personal_wiki_profile p
  GROUP BY p.owner_user_id
  HAVING sum(
    CASE WHEN p.lifecycle_status='active' AND p.is_default=1 THEN 1 ELSE 0 END
  )<>1
)
OR EXISTS (
  SELECT u.id FROM app_user u
  LEFT JOIN personal_wiki_profile p ON p.owner_user_id=u.id
  GROUP BY u.id
  HAVING count(p.space_id)=0
)
""".strip(),
    """
SELECT 'personal_wiki_lifecycle_tuple_invalid'
FROM personal_wiki_profile
WHERE lifecycle_generation<1 OR row_version<1 OR updated_at<created_at
   OR (lifecycle_status='active' AND (
       archived_at IS NOT NULL OR archived_by_user_id IS NOT NULL
       OR archive_reason IS NOT NULL))
   OR (lifecycle_status='archived' AND (
       is_default<>0 OR archived_at IS NULL
       OR archived_by_user_id<>owner_user_id))
LIMIT 1
""".strip(),
)


__all__ = ["V008_POSTCONDITION_QUERIES", "V008_STATEMENTS"]
