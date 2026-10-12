"""Forward-only v4 PDF ownership prompt identity; retained v1-v3 stay valid.

The new prompt keeps the v47 source-evidence shape and fail-closed resolver.
Only policy/prompt identity checks expand. No source, citation, or output guard
is relaxed. The v51 protected-PDF extractor branch remains in the run trigger.
"""

from . import v046_pdf_visual_writing_policy as writing
from . import v047_pdf_evidence_units as evidence
from . import v051_protected_pdf_visual_binding as protected
from .company_policy_provenance_catalog import COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG as prior_catalog
from .v032_native_display_storage import _once


POLICY_V4 = "wiki-pdf-page-visual-writing-v4"
PROMPT_V4_SHA256 = "4f93e9d647370c8185ad3278679cb0bef5d8bd1ebe9a13d02d76f1f354237c9d"
POLICY_IDENTITIES = (*evidence.POLICY_IDENTITIES, (POLICY_V4, PROMPT_V4_SHA256))


def _v4(sql):
    if evidence.POLICY_V3 not in sql:
        raise ValueError("Expected an exact v3 policy SQL branch")
    return sql.replace(evidence.POLICY_V3, POLICY_V4).replace(
        evidence.PROMPT_V3_SHA256, PROMPT_V4_SHA256,
    )


def _pair(policy, assembly, prompt):
    return "(" + " OR ".join(
        f"({policy}='{version}' AND {assembly}='{version}' AND {prompt}='{digest}')"
        for version, digest in POLICY_IDENTITIES
    ) + ")"


def _statement(statements, prefix):
    values = [value for value in statements if value.startswith(prefix)]
    if len(values) != 1:
        raise ValueError("Expected one frozen predecessor statement")
    return values[0]


def _with_v4_branch(statement, identity):
    before, rest = statement.split(" WHEN ", 1)
    guard, after = rest.rsplit(" BEGIN ", 1)
    # CASE selects the old exact guard for every retained policy. The new
    # branch changes only the frozen policy/prompt identity inside that guard.
    condition = f"json_extract(NEW.{identity},'$." + (
        "policy_version" if identity == "metadata_json" else "version"
    ) + f"')='{POLICY_V4}'"
    return f"{before} WHEN CASE WHEN {condition} THEN ({_v4(guard)}) ELSE ({guard}) END BEGIN {after}"


_old_pair = evidence._pair(
    "batch_policy_version", "assembly_version", "prompt_sha256",
)
_table = _once(evidence._table, _old_pair, _pair(
    "batch_policy_version", "assembly_version", "prompt_sha256",
))

_run_insert = _with_v4_branch(protected._run_insert, "metadata_json")
_snapshot_insert = _with_v4_branch(_statement(
    evidence.V047_STATEMENTS, "CREATE TRIGGER pdf_visual_snapshot_body_insert "
), "manifest_json")
_old_policy_valid = evidence._policy_valid("NEW.policy_json")
_policy_insert = _once(_statement(
    evidence.V047_STATEMENTS, "CREATE TRIGGER pdf_visual_workflow_policy_insert "
), f"({_old_policy_valid})", f"({_old_policy_valid} OR {_v4(_old_policy_valid)})")

V061_STATEMENTS = (
    *("DROP TRIGGER " + value.split()[2] for value in writing._request_triggers),
    "ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v60",
    _table,
    "INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v60",
    "DROP TABLE pdf_visual_workflow_request_v60",
    *writing._request_triggers,
    "DROP TRIGGER pdf_visual_run_insert",
    _run_insert,
    "DROP TRIGGER pdf_visual_snapshot_body_insert",
    _snapshot_insert,
    "DROP TRIGGER pdf_visual_workflow_policy_insert",
    _policy_insert,
)


def _active(origin_version, origin_index):
    values = [row.query for row in prior_catalog.resolved_postconditions
              if (row.origin_version, row.origin_index) == (origin_version, origin_index)]
    if len(values) != 1:
        raise ValueError("Expected one active predecessor postcondition")
    return values[0]


_old_snapshot_guard = evidence._snapshot.replace("NEW.", "retained.")
_new_snapshot_guard = _with_v4_branch(
    _statement(evidence.V047_STATEMENTS, "CREATE TRIGGER pdf_visual_snapshot_body_insert "),
    "manifest_json",
).split(" WHEN ", 1)[1].rsplit(" BEGIN ", 1)[0].replace("NEW.", "retained.")
_old_snapshot_query = _active(43, 18)
_new_snapshot_query = _once(_old_snapshot_query, _old_snapshot_guard, _new_snapshot_guard)

_old_policy_query = _active(44, 5)
_policy_value = evidence._policy_valid("b.policy_json")
_new_policy_query = _once(_old_policy_query, _policy_value,
    f"({_policy_value} OR {_v4(_policy_value)})")

_old_pair_query = _active(46, 1)
_new_pair_query = _once(_old_pair_query, evidence._pair(
    "q.batch_policy_version", "q.assembly_version", "q.prompt_sha256",
), _pair("q.batch_policy_version", "q.assembly_version", "q.prompt_sha256"))

_old_run_query = _active(47, 1)
_new_run_query = _once(_old_run_query, evidence._pair(
    "json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')",
), _pair(
    "json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')",
))

V061_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot_query, _new_snapshot_query),
    (_old_policy_query, _new_policy_query),
    (_old_pair_query, _new_pair_query),
    (_old_run_query, _new_run_query),
)

V061_POSTCONDITION_QUERIES = ()

