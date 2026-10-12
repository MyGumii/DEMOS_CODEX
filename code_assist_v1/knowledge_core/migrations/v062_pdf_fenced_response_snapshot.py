"""Forward-only snapshot guard for exact fenced v4 provider JSON.

The response ledger keeps the provider's original bytes and transport hashes.
Python already accepts one complete JSON code fence; v61's snapshot trigger
incorrectly required the unmodified response column to be a JSON object.
Only the v4 JSON-field checks use the unwrapped expression. Receipt/raw-byte
equality, all other snapshot checks, and v1-v3 branches remain unchanged.
"""

from . import v047_pdf_evidence_units as evidence
from . import v061_pdf_owned_evidence_prompt as prior
from .v032_native_display_storage import _once


_TRIMMED = "trim(ob.response, ' '||char(9)||char(10)||char(13))"
_PREFIX = (
    f"(CASE WHEN substr({_TRIMMED},1,9)='```json'||char(13)||char(10) THEN 9 "
    f"WHEN substr({_TRIMMED},1,8)='```json'||char(10) THEN 8 ELSE 0 END)"
)
_SUFFIX = (
    f"(CASE WHEN substr({_TRIMMED},-5)=char(13)||char(10)||'```' THEN 5 "
    f"WHEN substr({_TRIMMED},-4)=char(10)||'```' THEN 4 ELSE 0 END)"
)
_INNER = f"substr({_TRIMMED},{_PREFIX}+1,length({_TRIMMED})-{_PREFIX}-{_SUFFIX})"
_RESPONSE_JSON = (
    f"(CASE WHEN json_valid(ob.response) THEN ob.response "
    f"WHEN {_PREFIX}>0 AND {_SUFFIX}>0 AND json_valid({_INNER}) THEN {_INNER} "
    "ELSE NULL END)"
)


def _v4_snapshot_guard():
    predecessor = prior._statement(
        evidence.V047_STATEMENTS,
        "CREATE TRIGGER pdf_visual_snapshot_body_insert ",
    )
    before, rest = predecessor.split(" WHEN ", 1)
    old_guard, after = rest.rsplit(" BEGIN ", 1)
    v4_guard = prior._v4(old_guard)
    for old, new in (
        ("json_valid(ob.response)", f"json_valid({_RESPONSE_JSON})"),
        ("json_type(ob.response)", f"json_type({_RESPONSE_JSON})"),
        ("json_type(ob.response,", f"json_type({_RESPONSE_JSON},"),
        ("json_extract(ob.response,", f"json_extract({_RESPONSE_JSON},"),
    ):
        if old not in v4_guard:
            raise ValueError("Expected exact v4 response guard expression")
        v4_guard = v4_guard.replace(old, new)
    condition = f"json_extract(NEW.manifest_json,'$.version')='{prior.POLICY_V4}'"
    return (f"{before} WHEN CASE WHEN {condition} THEN ({v4_guard}) "
            f"ELSE ({old_guard}) END BEGIN {after}")


_snapshot_insert = _v4_snapshot_guard()
V062_STATEMENTS = (
    "DROP TRIGGER pdf_visual_snapshot_body_insert",
    _snapshot_insert,
)

_old_snapshot_guard = prior._new_snapshot_guard
_new_snapshot_guard = _snapshot_insert.split(" WHEN ", 1)[1].rsplit(
    " BEGIN ", 1
)[0].replace("NEW.", "retained.")
_old_snapshot_query = prior._new_snapshot_query
_new_snapshot_query = _once(
    _old_snapshot_query, _old_snapshot_guard, _new_snapshot_guard
)
V062_POSTCONDITION_REPLACEMENTS = ((_old_snapshot_query, _new_snapshot_query),)
