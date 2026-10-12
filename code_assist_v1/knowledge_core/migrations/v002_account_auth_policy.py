"""Forward-only account authentication-policy metadata migration.

Schema v1 intentionally froze the shared-knowledge physical core before the
legacy authentication adapter was implemented.  P1.4 demonstrated that two
legacy fields are authorization-relevant: dropping ``must_change_password``
would let an administrator-reset account bypass the mandatory password-change
screen.  Version 2 adds those fields without altering the reviewed v1 manifest.
"""

from __future__ import annotations


V002_STATEMENTS: tuple[str, ...] = (
    """
ALTER TABLE app_user ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0
CHECK(must_change_password IN (0,1))
""".strip(),
    """
ALTER TABLE app_user ADD COLUMN password_changed_at INTEGER
CHECK(password_changed_at IS NULL OR password_changed_at >= 0)
""".strip(),
    """
CREATE TRIGGER trg_app_user_password_policy_insert
BEFORE INSERT ON app_user
FOR EACH ROW
WHEN NEW.must_change_password=1 AND NEW.password_changed_at IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'USER_PASSWORD_POLICY_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_app_user_password_policy_update
BEFORE UPDATE ON app_user
FOR EACH ROW
WHEN (NEW.must_change_password=1 AND NEW.password_changed_at IS NOT NULL)
OR (
  NEW.password_hash IS NOT OLD.password_hash
  AND NEW.must_change_password IS OLD.must_change_password
  AND NEW.password_changed_at IS OLD.password_changed_at
  AND NOT (NEW.must_change_password=1 AND NEW.password_changed_at IS NULL)
)
OR (
  (NEW.must_change_password IS NOT OLD.must_change_password
    OR NEW.password_changed_at IS NOT OLD.password_changed_at)
  AND NEW.password_hash IS OLD.password_hash
)
BEGIN
  SELECT RAISE(ABORT, 'USER_PASSWORD_POLICY_INVALID');
END
""".strip(),
)


V002_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'app_user_password_columns_missing' WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='app_user'
    AND lower(sql) LIKE '%must_change_password integer not null default 0%'
    AND lower(sql) LIKE '%password_changed_at integer%'
)
""".strip(),
    """
SELECT id FROM app_user
  WHERE must_change_password NOT IN (0,1)
    OR (must_change_password=1 AND password_changed_at IS NOT NULL)
LIMIT 1
""".strip(),
    """
SELECT 'app_user_password_triggers_missing' WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='trigger' AND name IN (
    'trg_app_user_password_policy_insert',
    'trg_app_user_password_policy_update'
  )
) <> 2
""".strip(),
)


__all__ = ["V002_POSTCONDITION_QUERIES", "V002_STATEMENTS"]
