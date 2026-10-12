"""Explicit v2 writing identity alongside immutable v1 retained jobs.

No activation, row relabeling, call retry, output-schema relaxation or historical
migration edit. Whole identity pairs are accepted, never independent IN lists.
"""
from . import v043_pdf_visual_authoring_storage as storage
from . import v044_pdf_visual_authoring_workflow as workflow
from .v032_native_display_storage import _once

POLICY_V2 = "wiki-pdf-page-visual-writing-v2"
PROMPT_V2_SHA256 = "ae70093a4ef9c702ba45492e0509a63c980c1162e781f500d0fbb8cc60edfe42"
POLICY_IDENTITIES = ((storage.POLICY, storage.PROMPT_SHA256), (POLICY_V2, PROMPT_V2_SHA256))


def _v2(sql):
    assert storage.POLICY in sql and storage.PROMPT_SHA256 in sql
    return sql.replace(storage.POLICY, POLICY_V2).replace(storage.PROMPT_SHA256, PROMPT_V2_SHA256)


def _pair(policy, assembly, prompt):
    return "(" + " OR ".join(
        f"({policy}='{version}' AND {assembly}='{version}' AND {prompt}='{digest}')"
        for version, digest in POLICY_IDENTITIES) + ")"


def _policy_valid(value):
    prior = workflow._policy_valid(value)
    return f"(({prior}) OR ({_v2(prior)}))"


def _policy_binding(value, request):
    config = f"json_extract({value},'$.configuration')"
    return " AND ".join((
        f"json_extract({config},'$.policy_version')={request}.batch_policy_version",
        f"json_extract({config},'$.policy_version')={request}.assembly_version",
        f"json_extract({config},'$.prompt_sha256')={request}.prompt_sha256",
    ))


# Each full historical invalidity predicate includes every original guard.
# Reject unless one complete version is valid, then additionally bind the run
# to the exact retained archive configuration (formerly implicitly only v1).
_run = f"""(({storage._run}) AND ({_v2(storage._run)}))
    OR NOT EXISTS(SELECT 1 FROM pdf_visual_source_body ab
      WHERE ab.source_id=NEW.source_archive_id
        AND json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.policy_version')
          =json_extract(NEW.metadata_json,'$.policy_version')
        AND json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.prompt_sha256')
          =json_extract(NEW.metadata_json,'$.prompt_sha256'))"""

_snapshot = f"""(({storage._snapshot}) AND ({_v2(storage._snapshot)}))
    OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
      JOIN pdf_visual_authoring_run r ON r.id=s.run_id WHERE s.id=NEW.snapshot_id
        AND json_extract(NEW.manifest_json,'$.version')=json_extract(r.metadata_json,'$.policy_version')
        AND json_extract(NEW.manifest_json,'$.prompt_sha256')=json_extract(r.metadata_json,'$.prompt_sha256'))"""

_table = next(s for s in workflow.V044_STATEMENTS if s.startswith("CREATE TABLE pdf_visual_workflow_request("))
for _column in ("assembly_version", "batch_policy_version", "prompt_sha256"):
    _table = _once(_table, f"{_column} TEXT NOT NULL CHECK({_column}='{workflow._FIXED[_column]}')",
        f"{_column} TEXT NOT NULL")
_table = _once(_table, ") STRICT", ",\n        CHECK(" + _pair(
    "batch_policy_version", "assembly_version", "prompt_sha256") + ")\n    ) STRICT")
_request_triggers = tuple(s for s in workflow.V044_STATEMENTS
    if s.startswith("CREATE TRIGGER ") and any(token in s for token in (
        " ON pdf_visual_workflow_request\n", " ON pdf_visual_workflow_request BEGIN")))
assert len(_request_triggers) == 4

V046_STATEMENTS = (
    *("DROP TRIGGER " + s.split()[2] for s in _request_triggers),
    "ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v45",
    _table,
    "INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v45",
    "DROP TABLE pdf_visual_workflow_request_v45",
    *_request_triggers,
    "DROP TRIGGER pdf_visual_run_insert",
    f"CREATE TRIGGER pdf_visual_run_insert BEFORE INSERT ON pdf_visual_authoring_run WHEN {_run} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_RUN_SOURCE_INVALID'); END",
    "DROP TRIGGER pdf_visual_snapshot_body_insert",
    f"CREATE TRIGGER pdf_visual_snapshot_body_insert BEFORE INSERT ON pdf_visual_wiki_snapshot_body WHEN {_snapshot} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SNAPSHOT_BODY_INVALID'); END",
    "DROP TRIGGER pdf_visual_workflow_policy_insert",
    f"""CREATE TRIGGER pdf_visual_workflow_policy_insert BEFORE INSERT ON pdf_visual_workflow_policy_body
        WHEN NOT coalesce((EXISTS(SELECT 1 FROM pdf_visual_workflow_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN source_artifact a ON a.id=p.artifact_id
            WHERE r.preview_id=NEW.preview_id AND a.body_retention_status='present'
              AND r.policy_size_bytes=length(CAST(NEW.policy_json AS BLOB))
              AND ({_policy_binding('NEW.policy_json', 'r')}))
            AND ({_policy_valid('NEW.policy_json')})),0)
        BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_WORKFLOW_POLICY_INVALID'); END""",
)

_old_policy_check = next(q for q in workflow.V044_POSTCONDITION_QUERIES
    if q.startswith("SELECT 'PDF retained workflow policy invalid'"))
_new_policy_check = _once(_old_policy_check, workflow._policy_valid('b.policy_json'),
    _policy_valid('b.policy_json') + " AND (" + _policy_binding('b.policy_json', 'q') + ")")

V046_POSTCONDITION_REPLACEMENTS = (
    (storage.V043_POSTCONDITION_QUERIES[-1],
     f"SELECT 'visual snapshot identity invalid' FROM pdf_visual_wiki_snapshot_body retained WHERE ({_snapshot.replace('NEW.', 'retained.')}) LIMIT 1"),
    (_old_policy_check, _new_policy_check),
)
V046_POSTCONDITION_QUERIES = (
    f"SELECT 'PDF writing identity pair invalid' FROM pdf_visual_workflow_request q WHERE NOT coalesce(({_pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256')}),0) LIMIT 1",
    """SELECT 'PDF retained run archive policy differs' FROM pdf_visual_authoring_run r
      JOIN pdf_visual_source_body ab ON ab.source_id=r.source_archive_id
      WHERE NOT coalesce((json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.policy_version')
          =json_extract(r.metadata_json,'$.policy_version')
        AND json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.prompt_sha256')
          =json_extract(r.metadata_json,'$.prompt_sha256')),0) LIMIT 1""",
)
