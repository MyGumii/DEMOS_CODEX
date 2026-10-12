"""Forward-only v3 preview identity for ordinary Office native+visual jobs.

This admits a new job shape without reinterpreting v1/v2 requests. A ready v3
preview requires both the native source body and its complete v54 archive.
The request is registered after the queued job/preview, before worker claim.
"""

from .office_hybrid_visual_archive_catalog import OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG
from .v028_native_preview import _valid_v2
from .v050_protected_extraction_provenance import _SHA


RULE = "office-hybrid-native-visual-v1"
POLICY = "office-hybrid-upload-policy-v1"


def _current_trigger(name: str) -> str:
    result = None
    for migration in OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG.migrations:
        for statement in migration.statements:
            if (statement.startswith("CREATE TRIGGER " + name + "\n")
                    or statement.startswith("CREATE TRIGGER " + name + " ")):
                result = statement
    if result is None:
        raise RuntimeError("Retained trigger is missing: " + name)
    return result


def _once(statement: str, before: str, after: str) -> str:
    if statement.count(before) != 1:
        raise RuntimeError("Retained preview trigger contract changed")
    return statement.replace(before, after)


def _valid_v3(prefix: str) -> str:
    old = _valid_v2(prefix)
    old = _once(old, "deterministic-ingest-native-tables-v1", RULE)
    old = _once(old, "json_extract(" + prefix + ".expected_state_json,'$.schema_version')=2",
        "json_extract(" + prefix + ".expected_state_json,'$.schema_version')=3")
    return _once(old,
        "json_extract(" + prefix + ".expected_state_json,'$.handler_version')='v2'",
        "json_extract(" + prefix + ".expected_state_json,'$.handler_version')='v3'")


_READY_V3 = """ AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
    JOIN extraction_native_table_structure n
      ON n.snapshot_id=x.id AND n.artifact_id=x.artifact_id
    JOIN extraction_native_table_structure_body nb
      ON nb.snapshot_id=n.snapshot_id AND nb.artifact_id=n.artifact_id
    JOIN office_hybrid_visual_archive a
      ON a.preview_id=q.preview_id AND a.extraction_snapshot_id=x.id
      AND a.artifact_id=x.artifact_id AND a.native_structure_sha256=n.structure_sha256
    JOIN office_hybrid_visual_archive_ready ar ON ar.preview_id=a.preview_id
    JOIN office_hybrid_visual_archive_body ab ON ab.preview_id=a.preview_id
    WHERE q.preview_id=NEW.id AND q.parent_job_id=NEW.durable_job_id
      AND n.extracted_markdown_sha256=x.extracted_sha256)"""


_statements = [
    f"""CREATE TABLE office_hybrid_upload_request(
      preview_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_preview(id) ON DELETE RESTRICT,
      parent_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      artifact_id TEXT NOT NULL REFERENCES source_artifact(id) ON DELETE RESTRICT,
      rule_version TEXT NOT NULL CHECK(rule_version='{RULE}'),
      policy_version TEXT NOT NULL CHECK(policy_version='{POLICY}'),
      policy_sha256 TEXT NOT NULL CHECK({_SHA.format(x='policy_sha256')}),
      policy_json TEXT NOT NULL CHECK(json_valid(policy_json)
        AND json_type(policy_json)='object'
        AND length(CAST(policy_json AS BLOB)) BETWEEN 1 AND 65536),
      created_at INTEGER NOT NULL CHECK(created_at>=0)
    ) STRICT""",
    """CREATE TRIGGER office_hybrid_upload_request_insert
      BEFORE INSERT ON office_hybrid_upload_request
      WHEN NOT EXISTS(SELECT 1 FROM publication_preview p
        JOIN durable_job j ON j.id=p.durable_job_id
        JOIN app_user u ON u.id=p.actor_user_id
        WHERE p.id=NEW.preview_id AND p.status='queued'
          AND p.rule_version=NEW.rule_version
          AND p.durable_job_id=NEW.parent_job_id
          AND p.actor_user_id=NEW.actor_user_id
          AND p.artifact_id=NEW.artifact_id
          AND j.state='queued' AND j.job_type='publication_preview.extract'
          AND j.handler_version='v3' AND j.actor_user_id=NEW.actor_user_id
          AND j.created_at=NEW.created_at
          AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
          AND json_extract(j.input_ref_json,'$.rule_version')=NEW.rule_version
          AND json_extract(j.expected_state_json,'$.schema_version')=3
          AND json_extract(j.expected_state_json,'$.handler_version')='v3'
          AND json_extract(NEW.policy_json,'$.version')=NEW.policy_version)
      BEGIN SELECT RAISE(ABORT,'OFFICE_HYBRID_REQUEST_INVALID'); END""",
    """CREATE TRIGGER office_hybrid_upload_request_update
      BEFORE UPDATE ON office_hybrid_upload_request
      BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER office_hybrid_upload_request_delete
      BEFORE DELETE ON office_hybrid_upload_request
      BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
]

for _name in ("trg_publication_preview_validity_insert",
              "trg_publication_preview_validity_update"):
    _old = _current_trigger(_name)
    _v2 = "(j.handler_version='v2' AND coalesce((" + _valid_v2("j") + "),0)=1"
    if _name.endswith("_update"):
        _v2 += " AND EXISTS ("
        # The v2 branch has a retained native-ready predicate. Insert a new
        # top-level OR after its complete parenthesized expression instead of
        # changing that historical predicate or any historical checksum.
        _start = _old.find(_v2)
        if _start < 0:
            raise RuntimeError("Retained v2 ready preview trigger changed")
        _depth, _end = 0, None
        for _index in range(_start, len(_old)):
            if _old[_index] == "(":
                _depth += 1
            elif _old[_index] == ")":
                _depth -= 1
                if _depth == 0:
                    _end = _index + 1
                    break
        if _end is None:
            raise RuntimeError("Retained v2 ready predicate is unbalanced")
        _new = _old[:_end] + " OR (j.handler_version='v3' AND coalesce((" \
            + _valid_v3("j") + "),0)=1" + _READY_V3 + ")" + _old[_end:]
    else:
        _v2 += ")"
        _v3 = " OR (j.handler_version='v3' AND coalesce((" + _valid_v3("j") + "),0)=1)"
        _new = _once(_old, _v2, _v2 + _v3)
    _statements.extend(("DROP TRIGGER " + _name, _new))

_name = "trg_durable_job_state_insert"
_old = _current_trigger(_name)
_new = _once(_old, "NEW.handler_version NOT IN ('v1','v2')",
    "NEW.handler_version NOT IN ('v1','v2','v3')")
_v2_bad = "OR (NEW.handler_version='v2' AND coalesce((" + _valid_v2("NEW") + "),0)<>1)"
_v3_bad = " OR (NEW.handler_version='v3' AND coalesce((" + _valid_v3("NEW") + "),0)<>1)"
_new = _once(_new, _v2_bad, _v2_bad + _v3_bad)
_statements.extend(("DROP TRIGGER " + _name, _new))

_name = "office_visual_derivative_insert"
_old = _current_trigger(_name)
_new = _once(_old,
    "JOIN extraction_snapshot e ON e.id=p.extraction_snapshot_id\n           AND e.artifact_id=a.id",
    "JOIN extraction_snapshot e ON e.id=NEW.extraction_snapshot_id\n           AND e.artifact_id=a.id")
_new = _once(_new, "p.id=NEW.preview_id AND p.status='ready'",
    """p.id=NEW.preview_id AND (p.status='ready' OR (
      p.status='extracting' AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
      JOIN durable_job j ON j.id=q.parent_job_id
      WHERE q.preview_id=p.id AND q.parent_job_id=p.durable_job_id
        AND q.actor_user_id=p.actor_user_id AND q.artifact_id=p.artifact_id
        AND j.state='running' AND j.handler_version='v3')))""")
_new = _once(_new, "AND p.extraction_snapshot_id=NEW.extraction_snapshot_id",
    """AND (p.extraction_snapshot_id=NEW.extraction_snapshot_id OR (
      p.status='extracting' AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
      JOIN durable_job j ON j.id=q.parent_job_id
      WHERE q.preview_id=p.id AND q.parent_job_id=p.durable_job_id
        AND q.actor_user_id=p.actor_user_id AND q.artifact_id=p.artifact_id
        AND j.state='running' AND j.handler_version='v3')))""")
_statements.extend(("DROP TRIGGER " + _name, _new))

_name = "office_hybrid_archive_insert"
_old = _current_trigger(_name)
_new = _once(_old, "AND n.source_format=d.original_format AND p.status='ready'",
    """AND n.source_format=d.original_format AND (p.status='ready' OR (
        p.status='extracting' AND EXISTS(SELECT 1 FROM office_hybrid_upload_request q
          JOIN durable_job j ON j.id=q.parent_job_id
          WHERE q.preview_id=p.id AND q.parent_job_id=p.durable_job_id
            AND q.actor_user_id=p.actor_user_id AND q.artifact_id=p.artifact_id
            AND j.state='running' AND j.handler_version='v3')))""")
_statements.extend(("DROP TRIGGER " + _name, _new))

V055_STATEMENTS = tuple(_statements)
V055_POSTCONDITION_QUERIES = (
    """SELECT 'office_hybrid_v3_request_missing' FROM durable_job j
      JOIN publication_preview p ON p.durable_job_id=j.id
      LEFT JOIN office_hybrid_upload_request q ON q.preview_id=p.id
      WHERE j.job_type='publication_preview.extract' AND j.handler_version='v3'
        AND (q.preview_id IS NULL OR q.parent_job_id<>j.id
          OR q.actor_user_id<>p.actor_user_id OR q.artifact_id<>p.artifact_id)
      LIMIT 1""",
    """SELECT 'office_hybrid_v3_ready_missing_source' FROM publication_preview p
      JOIN durable_job j ON j.id=p.durable_job_id
      LEFT JOIN office_hybrid_visual_archive_ready a ON a.preview_id=p.id
      LEFT JOIN extraction_native_table_structure n ON n.snapshot_id=p.extraction_snapshot_id
        AND n.artifact_id=p.artifact_id
      WHERE p.status='ready' AND j.job_type='publication_preview.extract'
        AND j.handler_version='v3' AND (a.preview_id IS NULL OR n.snapshot_id IS NULL)
      LIMIT 1""",
    "SELECT 'invalid office hybrid v3 input' FROM durable_job j "
      "WHERE j.job_type='publication_preview.extract' AND j.handler_version='v3' "
      "AND coalesce((" + _valid_v3("j") + "),0)<>1 LIMIT 1",
)


__all__ = ["RULE", "POLICY", "V055_STATEMENTS", "V055_POSTCONDITION_QUERIES"]
