"""Forward-only v11 explicit higher publication and curation authority.

Versions 1 through 10 remain byte-identical.  This migration adds immutable
preview/operation authority proofs for one explicit Team or Responsibility
publication target, rebuilds publication_operation to admit that one new mode,
and extends the existing space-editor review trigger to exact open higher-role
episodes.
"""

from __future__ import annotations

from .v001_triggers import TRIGGER_DEFINITIONS


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


_FROZEN_TRIGGERS = dict(TRIGGER_DEFINITIONS)


def _frozen_trigger(name: str) -> str:
    try:
        return _FROZEN_TRIGGERS[name]
    except KeyError as exc:  # pragma: no cover - import-time contract guard
        raise RuntimeError(f"The frozen trigger {name!r} is unavailable.") from exc


def _replace_once(statement: str, old: str, new: str, *, label: str) -> str:
    if statement.count(old) != 1:  # pragma: no cover - import-time contract guard
        raise RuntimeError(f"The frozen {label} replacement anchor drifted.")
    return statement.replace(old, new, 1)


_PREVIEW_TARGET_OLD = """OR NOT EXISTS (
  SELECT 1 FROM json_each(NEW.available_targets_json) WHERE value='personal'
) OR EXISTS (
  SELECT value FROM json_each(NEW.available_targets_json)
  WHERE value NOT IN ('personal','part','both')
) OR EXISTS (
  SELECT value FROM json_each(NEW.available_targets_json)
  GROUP BY value HAVING count(*)<>1
) OR (NEW.captured_part_id IS NULL AND EXISTS (
  SELECT 1 FROM json_each(NEW.available_targets_json)
  WHERE value IN ('part','both')
))"""

_PREVIEW_TARGET_NEW = """OR NOT (
  (NOT EXISTS (
     SELECT 1 FROM higher_publication_preview_authority a
     WHERE a.preview_id=NEW.id
   )
   AND EXISTS (
     SELECT 1 FROM json_each(NEW.available_targets_json) WHERE value='personal'
   )
   AND NOT EXISTS (
     SELECT value FROM json_each(NEW.available_targets_json)
     WHERE value NOT IN ('personal','part','both')
   )
   AND NOT EXISTS (
     SELECT value FROM json_each(NEW.available_targets_json)
     GROUP BY value HAVING count(*)<>1
   )
   AND NOT (NEW.captured_part_id IS NULL AND EXISTS (
     SELECT 1 FROM json_each(NEW.available_targets_json)
     WHERE value IN ('part','both')
   )))
  OR
  ((SELECT count(*) FROM json_each(NEW.available_targets_json))=1
   AND json_extract(NEW.available_targets_json,'$[0]')='explicit'
   AND EXISTS (
     SELECT 1
     FROM higher_publication_preview_authority a
     WHERE a.preview_id=NEW.id AND a.actor_user_id=NEW.actor_user_id
       AND a.actor_scope_epoch=NEW.captured_scope_epoch
   ))
)"""

_PREVIEW_JOB_OLD = """AND json(json_extract(j.authorization_snapshot_json,'$.capabilities'))
      =json(j.authorization_capabilities_json)
  )
)) OR (NEW.status='ready'"""

_PREVIEW_JOB_NEW = """AND json(json_extract(j.authorization_snapshot_json,'$.capabilities'))
      =json(j.authorization_capabilities_json)
    AND (
      NOT EXISTS (
        SELECT 1 FROM higher_publication_preview_authority a
        WHERE a.preview_id=NEW.id
      )
      OR EXISTS (
        SELECT 1 FROM higher_publication_preview_authority a
        WHERE a.preview_id=NEW.id AND a.target_space_id=j.space_id
          AND EXISTS (
            SELECT 1 FROM json_each(j.authorization_capabilities_json)
            WHERE value='publish'
          )
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.native_mutation_space_id')=a.target_space_id
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.role_assignment_id')=a.role_assignment_id
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.role_assignment_generation')
              =a.role_assignment_generation
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.corpus_closure_generation')
              =a.corpus_closure_generation
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.corpus_closure_sha256')
              =a.corpus_closure_sha256
          AND json_extract(j.expected_state_json,
            '$._authorization_fence.organization_snapshot_version')
              =CAST(a.organization_snapshot_version AS TEXT)
      )
    )
  )
)) OR (NEW.status='ready'"""

_preview_insert = _replace_once(
    _frozen_trigger("trg_publication_preview_validity_insert"),
    _PREVIEW_TARGET_OLD,
    _PREVIEW_TARGET_NEW,
    label="publication preview target",
)
_preview_insert = _replace_once(
    _preview_insert,
    _PREVIEW_JOB_OLD,
    _PREVIEW_JOB_NEW,
    label="publication preview job authority",
)

_PREVIEW_UPDATE_OLD = """OR NEW.ready_result_json IS NOT OLD.ready_result_json
  OR NEW.completed_at IS NOT OLD.completed_at
)"""
_PREVIEW_UPDATE_NEW = """OR NEW.ready_result_json IS NOT OLD.ready_result_json
  OR NEW.completed_at IS NOT OLD.completed_at
) OR (NEW.status IN ('extracting','ready','consumed') AND EXISTS (
  SELECT 1 FROM higher_publication_preview_authority a
  WHERE a.preview_id=NEW.id
) AND NOT EXISTS (
  SELECT 1
  FROM higher_publication_preview_authority a
  JOIN app_user u ON u.id=a.actor_user_id
  JOIN knowledge_space s ON s.id=a.target_space_id
  JOIN org_unit o ON o.id=a.target_org_unit_id
  JOIN higher_scope_role_assignment h ON h.id=a.role_assignment_id
  WHERE a.preview_id=NEW.id AND u.account_status='active'
    AND u.scope_epoch=a.actor_scope_epoch AND u.row_version=a.actor_row_version
    AND s.owner_org_unit_id=o.id AND s.space_kind=a.target_space_kind
    AND s.status='active' AND s.write_enabled=1 AND o.status='active'
    AND h.org_unit_id=o.id AND h.user_id=u.id AND h.ended_at IS NULL
    AND h.assignment_generation=a.role_assignment_generation
    AND h.row_version=a.role_assignment_row_version
    AND ((a.target_space_kind='team' AND o.kind='team'
          AND h.role='team_leader')
      OR (a.target_space_kind='responsibility' AND o.kind='responsibility'
          AND h.role='responsibility_executive'))
))"""

_preview_update = _replace_once(
    _frozen_trigger("trg_publication_preview_validity_update"),
    _PREVIEW_UPDATE_OLD,
    _PREVIEW_UPDATE_NEW,
    label="publication preview transition authority",
)

_OPERATION_VALIDITY_OLD = """OR ((NEW.target_mode='both') <> (NEW.lineage_group_id IS NOT NULL))
OR (NEW.lineage_group_id IS NOT NULL AND NOT EXISTS ("""
_OPERATION_VALIDITY_NEW = """OR ((NEW.target_mode='both') <> (NEW.lineage_group_id IS NOT NULL))
OR ((NEW.target_mode='explicit') <> (NEW.explicit_authority_id IS NOT NULL))
OR (NEW.target_mode='explicit' AND NOT EXISTS (
  SELECT 1 FROM higher_publication_operation_authority a
  WHERE a.id=NEW.explicit_authority_id
    AND a.publication_operation_id=NEW.id
    AND a.actor_user_id=NEW.actor_user_id
    AND a.preview_id=(
      SELECT p.preview_id FROM higher_publication_preview_authority p
      WHERE p.id=a.preview_authority_id
    )
))
OR (NEW.lineage_group_id IS NOT NULL AND NOT EXISTS ("""

_operation_insert = _replace_once(
    _frozen_trigger("trg_publication_operation_validity_insert"),
    _OPERATION_VALIDITY_OLD,
    _OPERATION_VALIDITY_NEW,
    label="publication operation authority",
)

_TARGET_VALIDITY_OLD = """WHEN NEW.outcome='update_proposed' AND NOT EXISTS (
  SELECT 1 FROM curation_finding f"""
_TARGET_VALIDITY_NEW = """WHEN (NEW.outcome='update_proposed' AND NOT EXISTS (
  SELECT 1 FROM curation_finding f"""
_TARGET_VALIDITY_TAIL_OLD = """    AND f.required_reviewer='space_editor' AND f.proposed_action='update'
)"""
_TARGET_VALIDITY_TAIL_NEW = """    AND f.required_reviewer='space_editor' AND f.proposed_action='update'
)) OR EXISTS (
  SELECT 1 FROM publication_operation p
  WHERE p.id=NEW.publication_operation_id AND p.target_mode='explicit'
    AND NOT EXISTS (
      SELECT 1 FROM higher_publication_operation_authority a
      WHERE a.id=p.explicit_authority_id
        AND a.publication_operation_id=p.id
        AND a.target_space_id=NEW.target_space_id
    )
)"""

_target_insert = _replace_once(
    _frozen_trigger("trg_publication_target_validity_insert"),
    _TARGET_VALIDITY_OLD,
    _TARGET_VALIDITY_NEW,
    label="publication target opening",
)
_target_insert = _replace_once(
    _target_insert,
    _TARGET_VALIDITY_TAIL_OLD,
    _TARGET_VALIDITY_TAIL_NEW,
    label="publication target explicit authority",
)

_CURATION_EDITOR_OLD = """AND ((s.space_kind='personal' AND s.owner_user_id=u.id)
        OR (s.space_kind='part' AND s.owner_org_unit_id=u.current_part_id))"""
_CURATION_EDITOR_NEW = """AND ((s.space_kind='personal' AND s.owner_user_id=u.id)
        OR (s.space_kind='part' AND s.owner_org_unit_id=u.current_part_id)
        OR (s.space_kind IN ('team','responsibility') AND EXISTS (
          SELECT 1 FROM higher_scope_role_assignment h
          JOIN org_unit o ON o.id=s.owner_org_unit_id
          WHERE h.org_unit_id=o.id AND h.user_id=u.id AND h.ended_at IS NULL
            AND h.row_version=1 AND o.status='active'
            AND ((s.space_kind='team' AND o.kind='team'
                  AND h.role='team_leader')
              OR (s.space_kind='responsibility' AND o.kind='responsibility'
                  AND h.role='responsibility_executive'))
        )))"""

_curation_update = _replace_once(
    _frozen_trigger("trg_curation_finding_state_update"),
    _CURATION_EDITOR_OLD,
    _CURATION_EDITOR_NEW,
    label="curation higher reviewer",
)


V011_STATEMENTS: tuple[str, ...] = (
    "DROP TRIGGER trg_publication_preview_validity_insert",
    "DROP TRIGGER trg_publication_preview_validity_update",
    "DROP TRIGGER trg_publication_operation_validity_insert",
    "DROP TRIGGER trg_publication_operation_history_update",
    "DROP TRIGGER trg_publication_operation_history_delete",
    "DROP TRIGGER trg_publication_target_validity_insert",
    "DROP TRIGGER trg_curation_finding_state_update",
    f"""
CREATE TABLE higher_publication_preview_authority(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    preview_id TEXT NOT NULL UNIQUE CHECK({_uuid('preview_id')})
        REFERENCES publication_preview(id) ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_id TEXT NOT NULL CHECK({_uuid('target_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_kind TEXT NOT NULL CHECK(
        target_space_kind IN ('team','responsibility')
    ),
    target_org_unit_id TEXT NOT NULL CHECK({_uuid('target_org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    role_assignment_id TEXT NOT NULL CHECK({_uuid('role_assignment_id')})
        REFERENCES higher_scope_role_assignment(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    role_assignment_generation INTEGER NOT NULL CHECK(
        role_assignment_generation>=1
    ),
    role_assignment_row_version INTEGER NOT NULL CHECK(
        role_assignment_row_version>=1
    ),
    actor_scope_epoch INTEGER NOT NULL CHECK(actor_scope_epoch>=1),
    actor_row_version INTEGER NOT NULL CHECK(actor_row_version>=1),
    corpus_closure_generation INTEGER NOT NULL CHECK(
        corpus_closure_generation>=1
    ),
    corpus_closure_sha256 TEXT NOT NULL CHECK({_sha('corpus_closure_sha256')}),
    organization_snapshot_version INTEGER NOT NULL CHECK(
        organization_snapshot_version>=1
    ),
    target_descriptor_json TEXT NOT NULL CHECK(
        json_valid(target_descriptor_json)
        AND json_type(target_descriptor_json)='object'
    ),
    target_descriptor_sha256 TEXT NOT NULL CHECK({_sha('target_descriptor_sha256')}),
    created_at INTEGER NOT NULL CHECK(created_at>=0)
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_higher_publication_preview_target
ON higher_publication_preview_authority(preview_id,target_space_id)
""".strip(),
    """
CREATE INDEX ix_higher_publication_preview_role
ON higher_publication_preview_authority(
  role_assignment_id,role_assignment_generation,preview_id
)
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_preview_authority_insert
BEFORE INSERT ON higher_publication_preview_authority
FOR EACH ROW
WHEN (SELECT count(*) FROM json_each(NEW.target_descriptor_json))<>5
 OR EXISTS (
   SELECT 1 FROM json_each(NEW.target_descriptor_json)
   WHERE key NOT IN ('space_id','kind','capability','role_assignment_id',
                     'role_assignment_generation')
 )
 OR json_extract(NEW.target_descriptor_json,'$.space_id')<>NEW.target_space_id
 OR json_extract(NEW.target_descriptor_json,'$.kind')<>NEW.target_space_kind
 OR json_extract(NEW.target_descriptor_json,'$.capability')<>'publish'
 OR json_extract(NEW.target_descriptor_json,'$.role_assignment_id')
      <>NEW.role_assignment_id
 OR json_extract(NEW.target_descriptor_json,'$.role_assignment_generation')
      <>NEW.role_assignment_generation
 OR NOT EXISTS (
   SELECT 1
   FROM app_user u
   JOIN knowledge_space s ON s.id=NEW.target_space_id
   JOIN org_unit o ON o.id=NEW.target_org_unit_id
   JOIN higher_scope_role_assignment h ON h.id=NEW.role_assignment_id
   WHERE u.id=NEW.actor_user_id AND u.account_status='active'
     AND u.scope_epoch=NEW.actor_scope_epoch
     AND u.row_version=NEW.actor_row_version
     AND s.owner_org_unit_id=o.id AND s.space_kind=NEW.target_space_kind
     AND s.status='active' AND s.write_enabled=1 AND o.status='active'
     AND h.org_unit_id=o.id AND h.user_id=u.id AND h.ended_at IS NULL
     AND h.assignment_generation=NEW.role_assignment_generation
     AND h.row_version=NEW.role_assignment_row_version
     AND ((NEW.target_space_kind='team' AND o.kind='team'
           AND h.role='team_leader')
       OR (NEW.target_space_kind='responsibility'
           AND o.kind='responsibility'
           AND h.role='responsibility_executive'))
 )
BEGIN
  SELECT RAISE(ABORT,'HIGHER_PUBLICATION_AUTHORITY_INVALID');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_preview_authority_update
BEFORE UPDATE ON higher_publication_preview_authority
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_preview_authority_delete
BEFORE DELETE ON higher_publication_preview_authority
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    f"""
CREATE TABLE publication_operation_v11_new(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    dedup_id TEXT NOT NULL CHECK({_uuid('dedup_id')})
        REFERENCES operation_dedup(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK({_uuid('operation_id')}),
    request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
    artifact_id TEXT NOT NULL CHECK({_uuid('artifact_id')})
        REFERENCES source_artifact(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    extraction_snapshot_id TEXT NOT NULL CHECK({_uuid('extraction_snapshot_id')}),
    target_mode TEXT NOT NULL CHECK(
        target_mode IN ('personal','part','both','explicit')
    ),
    lineage_group_id TEXT CHECK(
        lineage_group_id IS NULL OR ({_uuid('lineage_group_id')})
    ) REFERENCES lineage_group(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    explicit_authority_id TEXT UNIQUE CHECK(
        explicit_authority_id IS NULL OR ({_uuid('explicit_authority_id')})
    ) REFERENCES higher_publication_operation_authority(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    FOREIGN KEY(extraction_snapshot_id,artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK((target_mode='both')=(lineage_group_id IS NOT NULL)),
    CHECK((target_mode='explicit')=(explicit_authority_id IS NOT NULL))
) STRICT
""".strip(),
    """
INSERT INTO publication_operation_v11_new(
  id,dedup_id,actor_user_id,operation_id,request_sha256,artifact_id,
  extraction_snapshot_id,target_mode,lineage_group_id,explicit_authority_id,
  created_at
)
SELECT id,dedup_id,actor_user_id,operation_id,request_sha256,artifact_id,
       extraction_snapshot_id,target_mode,lineage_group_id,NULL,created_at
FROM publication_operation
ORDER BY id
""".strip(),
    "DROP TABLE publication_operation",
    "ALTER TABLE publication_operation_v11_new RENAME TO publication_operation",
    """
CREATE UNIQUE INDEX ux_publication_actor_operation
ON publication_operation(actor_user_id,operation_id)
""".strip(),
    """
CREATE UNIQUE INDEX ux_publication_dedup
ON publication_operation(dedup_id)
""".strip(),
    f"""
CREATE TABLE higher_publication_operation_authority(
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}),
    publication_operation_id TEXT NOT NULL UNIQUE
        CHECK({_uuid('publication_operation_id')})
        REFERENCES publication_operation(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    preview_authority_id TEXT NOT NULL UNIQUE CHECK({_uuid('preview_authority_id')})
        REFERENCES higher_publication_preview_authority(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    preview_id TEXT NOT NULL UNIQUE CHECK({_uuid('preview_id')})
        REFERENCES publication_preview(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL CHECK({_user('actor_user_id')})
        REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_id TEXT NOT NULL CHECK({_uuid('target_space_id')})
        REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_kind TEXT NOT NULL CHECK(
        target_space_kind IN ('team','responsibility')
    ),
    target_org_unit_id TEXT NOT NULL CHECK({_uuid('target_org_unit_id')})
        REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    role_assignment_id TEXT NOT NULL CHECK({_uuid('role_assignment_id')})
        REFERENCES higher_scope_role_assignment(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    role_assignment_generation INTEGER NOT NULL CHECK(
        role_assignment_generation>=1
    ),
    role_assignment_row_version INTEGER NOT NULL CHECK(
        role_assignment_row_version>=1
    ),
    actor_scope_epoch INTEGER NOT NULL CHECK(actor_scope_epoch>=1),
    actor_row_version INTEGER NOT NULL CHECK(actor_row_version>=1),
    corpus_closure_generation INTEGER NOT NULL CHECK(
        corpus_closure_generation>=1
    ),
    corpus_closure_sha256 TEXT NOT NULL CHECK({_sha('corpus_closure_sha256')}),
    organization_snapshot_version INTEGER NOT NULL CHECK(
        organization_snapshot_version>=1
    ),
    target_descriptor_json TEXT NOT NULL CHECK(
        json_valid(target_descriptor_json)
        AND json_type(target_descriptor_json)='object'
    ),
    target_descriptor_sha256 TEXT NOT NULL CHECK({_sha('target_descriptor_sha256')}),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    FOREIGN KEY(publication_operation_id,target_space_id)
        REFERENCES publication_target(publication_operation_id,target_space_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
) STRICT
""".strip(),
    """
CREATE INDEX ix_higher_publication_operation_role
ON higher_publication_operation_authority(
  role_assignment_id,role_assignment_generation,publication_operation_id
)
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_operation_authority_insert
BEFORE INSERT ON higher_publication_operation_authority
FOR EACH ROW
WHEN (SELECT count(*) FROM json_each(NEW.target_descriptor_json))<>5
 OR EXISTS (
   SELECT 1 FROM json_each(NEW.target_descriptor_json)
   WHERE key NOT IN ('space_id','kind','capability','role_assignment_id',
                     'role_assignment_generation')
 )
 OR json_extract(NEW.target_descriptor_json,'$.space_id')<>NEW.target_space_id
 OR json_extract(NEW.target_descriptor_json,'$.kind')<>NEW.target_space_kind
 OR json_extract(NEW.target_descriptor_json,'$.capability')<>'publish'
 OR json_extract(NEW.target_descriptor_json,'$.role_assignment_id')
      <>NEW.role_assignment_id
 OR json_extract(NEW.target_descriptor_json,'$.role_assignment_generation')
      <>NEW.role_assignment_generation
 OR NOT EXISTS (
   SELECT 1
   FROM higher_publication_preview_authority p
   JOIN publication_preview v ON v.id=p.preview_id
   JOIN app_user u ON u.id=p.actor_user_id
   JOIN knowledge_space s ON s.id=p.target_space_id
   JOIN org_unit o ON o.id=p.target_org_unit_id
   JOIN higher_scope_role_assignment h ON h.id=p.role_assignment_id
   WHERE p.id=NEW.preview_authority_id AND p.preview_id=NEW.preview_id
     AND p.actor_user_id=NEW.actor_user_id
     AND p.target_space_id=NEW.target_space_id
     AND p.target_space_kind=NEW.target_space_kind
     AND p.target_org_unit_id=NEW.target_org_unit_id
     AND p.role_assignment_id=NEW.role_assignment_id
     AND p.role_assignment_generation=NEW.role_assignment_generation
     AND p.role_assignment_row_version=NEW.role_assignment_row_version
     AND p.actor_scope_epoch=NEW.actor_scope_epoch
     AND p.actor_row_version=NEW.actor_row_version
     AND p.corpus_closure_generation=NEW.corpus_closure_generation
     AND p.corpus_closure_sha256=NEW.corpus_closure_sha256
     AND p.organization_snapshot_version=NEW.organization_snapshot_version
     AND p.target_descriptor_json=NEW.target_descriptor_json
     AND p.target_descriptor_sha256=NEW.target_descriptor_sha256
     AND v.actor_user_id=u.id AND v.status='ready'
     AND u.account_status='active' AND u.scope_epoch=NEW.actor_scope_epoch
     AND u.row_version=NEW.actor_row_version
     AND s.owner_org_unit_id=o.id AND s.space_kind=NEW.target_space_kind
     AND s.status='active' AND s.write_enabled=1 AND o.status='active'
     AND h.org_unit_id=o.id AND h.user_id=u.id AND h.ended_at IS NULL
     AND h.assignment_generation=NEW.role_assignment_generation
     AND h.row_version=NEW.role_assignment_row_version
     AND ((NEW.target_space_kind='team' AND o.kind='team'
           AND h.role='team_leader')
       OR (NEW.target_space_kind='responsibility'
           AND o.kind='responsibility'
           AND h.role='responsibility_executive'))
 )
BEGIN
  SELECT RAISE(ABORT,'HIGHER_PUBLICATION_AUTHORITY_STALE');
END
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_operation_authority_update
BEFORE UPDATE ON higher_publication_operation_authority
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    """
CREATE TRIGGER trg_higher_publication_operation_authority_delete
BEFORE DELETE ON higher_publication_operation_authority
BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END
""".strip(),
    _preview_insert,
    _preview_update,
    _operation_insert,
    _frozen_trigger("trg_publication_operation_history_update"),
    _frozen_trigger("trg_publication_operation_history_delete"),
    _target_insert,
    _curation_update,
)


V011_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'v11_authority_table_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN (
    'higher_publication_preview_authority',
    'higher_publication_operation_authority'
  )
)<>2
OR EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE name='publication_operation_v11_new'
)
""".strip(),
    """
SELECT 'v11_authority_index_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='index' AND name IN (
    'ux_higher_publication_preview_target',
    'ix_higher_publication_preview_role',
    'ix_higher_publication_operation_role',
    'ux_publication_actor_operation','ux_publication_dedup'
  )
)<>5
""".strip(),
    """
SELECT 'v11_authority_trigger_set_invalid'
WHERE (
  SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name IN (
    'trg_higher_publication_preview_authority_insert',
    'trg_higher_publication_preview_authority_update',
    'trg_higher_publication_preview_authority_delete',
    'trg_higher_publication_operation_authority_insert',
    'trg_higher_publication_operation_authority_update',
    'trg_higher_publication_operation_authority_delete',
    'trg_publication_preview_validity_insert',
    'trg_publication_preview_validity_update',
    'trg_publication_operation_validity_insert',
    'trg_publication_operation_history_update',
    'trg_publication_operation_history_delete',
    'trg_publication_target_validity_insert',
    'trg_curation_finding_state_update'
  )
)<>13
""".strip(),
    """
SELECT 'v11_legacy_operation_shape_invalid'
WHERE EXISTS (
  SELECT 1 FROM publication_operation
  WHERE target_mode IN ('personal','part','both')
    AND explicit_authority_id IS NOT NULL
)
OR EXISTS (
  SELECT 1 FROM publication_operation
  WHERE (target_mode='both')<>(lineage_group_id IS NOT NULL)
)
""".strip(),
    """
SELECT 'v11_explicit_operation_shape_invalid'
WHERE EXISTS (
  SELECT 1
  FROM publication_operation p
  LEFT JOIN higher_publication_operation_authority a
    ON a.id=p.explicit_authority_id AND a.publication_operation_id=p.id
  WHERE p.target_mode='explicit'
    AND (a.id IS NULL OR p.lineage_group_id IS NOT NULL
      OR (SELECT count(*) FROM publication_target t
          WHERE t.publication_operation_id=p.id
            AND t.target_space_id=a.target_space_id)<>1
      OR (SELECT count(*) FROM publication_target t
          WHERE t.publication_operation_id=p.id)<>1)
)
""".strip(),
    """
SELECT 'v11_preview_authority_shape_invalid'
WHERE EXISTS (
  SELECT 1
  FROM higher_publication_preview_authority a
  LEFT JOIN publication_preview p ON p.id=a.preview_id
  WHERE p.id IS NULL OR p.actor_user_id<>a.actor_user_id
    OR json(p.available_targets_json)<>json('["explicit"]')
)
""".strip(),
)


__all__ = ["V011_POSTCONDITION_QUERIES", "V011_STATEMENTS"]
