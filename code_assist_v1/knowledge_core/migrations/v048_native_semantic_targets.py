"""Opt-in own-home aggregate intents; no historical row or trigger edits in v10."""
from .v010_semantic_graph_contracts import V010_STATEMENTS

_prior = next(sql for sql in V010_STATEMENTS
              if sql.startswith('CREATE TRIGGER trg_semantic_intent_validity_insert\n'))
_strict = 'WHERE source.id=NEW.source_space_id AND d.id<>o.id'
assert _prior.count(_strict) == 1
_successor = _prior.replace(_strict, '''WHERE source.id=NEW.source_space_id
           AND (d.id<>o.id OR (
             source.id=t.id AND source.space_kind=t.space_kind
             AND o.status='active'
           ))''')

V048_STATEMENTS = (
    'DROP TRIGGER trg_semantic_intent_validity_insert',
    _successor,
)
V048_POSTCONDITION_QUERIES = ("""
SELECT 'v48_native_aggregate_identity_invalid'
FROM semantic_edge_intent i JOIN knowledge_space s ON s.id=i.source_space_id
WHERE i.target_kind='aggregate' AND i.target_space_id=i.source_space_id
  AND (s.space_kind NOT IN ('team','responsibility')
       OR i.target_org_unit_id IS NOT s.owner_org_unit_id)
LIMIT 1
""".strip(),)
