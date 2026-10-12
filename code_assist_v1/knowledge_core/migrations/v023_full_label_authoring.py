"""Forward-only full-label writing policy; frozen v22 gates/receipts unchanged."""
from .v022_structured_authoring import V022_STATEMENTS

_OLD_AUTHORING = "r.policy_version IN ('wiki-information-authoring-v1','wiki-information-authoring-v2-structure')"
_NEW_AUTHORING = "r.policy_version IN ('wiki-information-authoring-v1','wiki-information-authoring-v2-structure','wiki-information-authoring-v3-full-labels')"
_OLD_STRUCTURE = "r.policy_version<>'wiki-information-authoring-v2-structure'"
_NEW_STRUCTURE = "r.policy_version NOT IN ('wiki-information-authoring-v2-structure','wiki-information-authoring-v3-full-labels')"
if (sum(s.count(_OLD_AUTHORING) for s in V022_STATEMENTS) != 4
        or sum(s.count(_OLD_STRUCTURE) for s in V022_STATEMENTS) != 1):
    raise RuntimeError("The frozen v22 policy gates changed.")

V023_STATEMENTS = tuple(s.replace(_OLD_AUTHORING, _NEW_AUTHORING).replace(_OLD_STRUCTURE, _NEW_STRUCTURE)
    for s in V022_STATEMENTS)
V023_POSTCONDITION_QUERIES = (
    """SELECT 'full-label authoring successor gates missing' WHERE 4<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND instr(sql,'wiki-information-authoring-v3-full-labels')>0)""",
)
