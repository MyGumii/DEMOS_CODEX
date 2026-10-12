"""Unactivated v31 cross-kind publication contract; v1-v30 remain immutable.

Requested and selected kinds have independent real FKs. Prior operations are
flattened by immutable selected snapshot IDs, never by reading private bodies or
mislabeling an extraction as a generated Wiki. Current eligibility belongs only
to INSERT/consume guards; permanent checks must survive retention/lifecycle loss.
"""
from .v014_wiki_authoring import _EQUIVALENT_UPLOAD
from .v020_preserved_publication import V020_STATEMENTS

_KINDS = ('extraction','authored_wiki','preserved_wiki','native_wiki')
_TABLES = ('extraction_snapshot','authored_wiki_snapshot','preserved_wiki_snapshot','native_wiki_snapshot')
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


def _id(prefix,alias=''):
    return 'coalesce('+','.join(f'{alias}{prefix}_{kind}_id' for kind in _KINDS)+')'


def _fields(prefix):
    declarations = ',\n'.join(f'{prefix}_{kind}_id TEXT REFERENCES {table}(id) ON DELETE RESTRICT'
        for kind,table in zip(_KINDS,_TABLES,strict=True))
    return f'{prefix}_kind TEXT NOT NULL CHECK({prefix}_kind IN {_KINDS}),\n{declarations}'


def _kind_check(prefix):
    cases = ' OR '.join('('+f"{prefix}_kind='{kind}' AND "+' AND '.join(
        f'{prefix}_{other}_id IS '+('NOT NULL' if other==kind else 'NULL') for other in _KINDS)+')'
        for kind in _KINDS)
    return f'CHECK({cases})'


_IDENTITIES = """CREATE VIEW publication_body_identity AS
SELECT 'extraction' AS kind,x.id AS snapshot_id,x.artifact_id,x.id AS extraction_snapshot_id,
 x.extracted_sha256 AS raw_sha256,x.normalized_content_sha256 AS normalized_sha256,
 x.extracted_size_bytes AS size_bytes,x.normalizer_version,NULL AS manifest_sha256,
 NULL AS manifest_size_bytes,'extraction' AS policy_version,x.status,NULL AS run_id,NULL AS preview_id,
 x.created_at AS snapshot_created_at,NULL AS actor_user_id
FROM extraction_snapshot x
UNION ALL SELECT 'authored_wiki',s.id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,s.content_size_bytes,'markdown_lf_v1',s.manifest_sha256,
 s.manifest_size_bytes,r.policy_version,r.status,r.id,r.preview_id,s.created_at,r.actor_user_id
FROM authored_wiki_snapshot s JOIN publication_authoring_run r ON r.id=s.run_id
UNION ALL SELECT 'preserved_wiki',s.id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,s.content_size_bytes,'markdown_lf_v1',s.manifest_sha256,
 s.manifest_size_bytes,r.policy_version,r.status,r.id,r.preview_id,s.created_at,r.actor_user_id
FROM preserved_wiki_snapshot s JOIN publication_preservation_run r ON r.id=s.run_id
UNION ALL SELECT 'native_wiki',s.id,r.artifact_id,r.extraction_snapshot_id,
 s.content_sha256,s.content_sha256,s.content_size_bytes,'markdown_lf_v1',s.manifest_sha256,
 s.manifest_size_bytes,json_extract(r.metadata_json,'$.policy_version'),state.status,r.id,r.preview_id,s.created_at,r.actor_user_id
FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
 JOIN native_authoring_state state ON state.run_id=r.id"""

_ACTUAL = f"""CREATE VIEW publication_operation_body AS
WITH legacy AS (SELECT p.id AS publication_operation_id,p.created_at,
 CASE WHEN n.native_snapshot_id IS NOT NULL THEN 'native_wiki'
      WHEN s.preserved_snapshot_id IS NOT NULL THEN 'preserved_wiki'
      WHEN a.authored_snapshot_id IS NOT NULL THEN 'authored_wiki' ELSE 'extraction' END AS kind,
 coalesce(n.native_snapshot_id,s.preserved_snapshot_id,a.authored_snapshot_id,
   e.extraction_snapshot_id,p.extraction_snapshot_id) AS snapshot_id,
 (n.publication_operation_id IS NOT NULL)
  +(s.publication_operation_id IS NOT NULL)+(a.publication_operation_id IS NOT NULL)
  +(e.publication_operation_id IS NOT NULL) AS selection_count
FROM publication_operation p
 LEFT JOIN publication_native_selection n ON n.publication_operation_id=p.id
 LEFT JOIN publication_preserved_selection s ON s.publication_operation_id=p.id
 LEFT JOIN publication_authored_selection a ON a.publication_operation_id=p.id
 LEFT JOIN publication_extraction_reuse e ON e.publication_operation_id=p.id),
roots AS (SELECT l.*,row_number() OVER(PARTITION BY kind,snapshot_id
  ORDER BY l.created_at,l.publication_operation_id) AS choice FROM legacy l
 JOIN publication_operation p ON p.id=l.publication_operation_id
 JOIN operation_dedup d ON d.id=p.dedup_id AND d.status='committed' AND d.operation_type='publication'
   AND d.actor_user_id=p.actor_user_id AND d.operation_id=p.operation_id AND d.request_sha256=p.request_sha256
 WHERE l.selection_count<=1 AND NOT EXISTS(SELECT 1 FROM publication_body_selection g
   WHERE g.publication_operation_id=l.publication_operation_id))
SELECT l.publication_operation_id,coalesce(g.selected_kind,l.kind) AS kind,
 coalesce({_id('selected','g.')},l.snapshot_id) AS snapshot_id,
 l.selection_count+(g.publication_operation_id IS NOT NULL) AS selection_count,
 coalesce(g.selected_body_root_operation_id,r.publication_operation_id,l.publication_operation_id) AS root_operation_id,
 coalesce(root.created_at,r.created_at,l.created_at) AS root_created_at
FROM legacy l LEFT JOIN publication_body_selection g ON g.publication_operation_id=l.publication_operation_id
 LEFT JOIN publication_operation root ON root.id=g.selected_body_root_operation_id
 LEFT JOIN roots r ON r.kind=l.kind AND r.snapshot_id=l.snapshot_id AND r.choice=1"""

_CANDIDATES = f"""CREATE VIEW publication_requested_body_candidate AS
SELECT p.id AS preview_id,p.actor_user_id,p.artifact_id,p.extraction_snapshot_id,
 p.source_snapshot_sha256,b.kind,b.snapshot_id,b.raw_sha256,b.normalized_sha256,b.size_bytes,
 b.normalizer_version,b.snapshot_created_at
FROM publication_preview p JOIN app_user u ON u.id=p.actor_user_id
 AND u.account_status='active' AND u.scope_epoch=p.captured_scope_epoch
 JOIN source_artifact a ON a.id=p.artifact_id AND a.origin_type='upload' AND a.body_retention_status='present'
 JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id AND x.artifact_id=a.id
   AND x.status='ready' AND x.body_retention_status='present' AND x.extracted_sha256=p.source_snapshot_sha256
 JOIN extraction_snapshot_body xb ON xb.snapshot_id=x.id AND xb.artifact_id=a.id
   AND xb.content_sha256=x.extracted_sha256 AND xb.content_size_bytes=x.extracted_size_bytes
 JOIN durable_job j ON j.id=p.durable_job_id AND j.actor_user_id=p.actor_user_id
   AND j.job_type='publication_preview.extract' AND j.state='succeeded' AND j.cancel_requested=0
 JOIN publication_body_identity b ON b.artifact_id=a.id AND b.extraction_snapshot_id=x.id AND b.status='ready'
WHERE p.status='ready' AND p.expires_at>{_NOW} AND (
 (b.kind='native_wiki' AND EXISTS(SELECT 1 FROM native_publication_candidate c
     WHERE c.snapshot_id=b.snapshot_id AND c.preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_authoring_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=p.id))
 OR (b.kind='preserved_wiki' AND j.handler_version='v1'
   AND NOT EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_authoring_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=p.id)
   AND EXISTS(SELECT 1 FROM publication_preservation_request q
     JOIN publication_preservation_run r ON r.preview_id=q.preview_id AND r.parent_job_id=q.parent_job_id
     JOIN publication_preservation_source_result o ON o.preview_id=p.id AND o.run_id=r.id AND o.outcome='planned'
     JOIN preserved_wiki_snapshot_body body ON body.snapshot_id=b.snapshot_id
     WHERE q.preview_id=p.id AND q.parent_job_id=p.durable_job_id AND r.id=b.run_id
       AND r.actor_user_id=p.actor_user_id AND r.source_sha256=p.source_snapshot_sha256))
 OR (b.kind='authored_wiki' AND j.handler_version='v1'
   AND NOT EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_preservation_run WHERE preview_id=p.id)
   AND EXISTS(SELECT 1 FROM publication_authoring_run r JOIN authored_wiki_snapshot_body body ON body.snapshot_id=b.snapshot_id
     WHERE r.id=b.run_id AND r.preview_id=p.id AND r.actor_user_id=p.actor_user_id
       AND r.source_sha256=p.source_snapshot_sha256)
   AND (NOT EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=p.id)
     OR EXISTS(SELECT 1 FROM publication_wiki_source_result o
       JOIN publication_synthesis_completion c ON c.run_id=o.run_id AND c.snapshot_id=b.snapshot_id
       WHERE o.preview_id=p.id AND o.outcome='planned' AND o.run_id=b.run_id)))
 OR (b.kind='extraction' AND j.handler_version='v1' AND b.snapshot_id=x.id
   AND NOT EXISTS(SELECT 1 FROM native_workflow_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM native_authoring_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_preservation_request WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_preservation_run WHERE preview_id=p.id)
   AND NOT EXISTS(SELECT 1 FROM publication_authoring_run WHERE preview_id=p.id AND status<>'extraction_only')
   AND (NOT EXISTS(SELECT 1 FROM publication_wiki_request WHERE preview_id=p.id)
     OR EXISTS(SELECT 1 FROM publication_wiki_source_result o
       LEFT JOIN publication_authoring_run r ON r.id=o.run_id AND r.preview_id=o.preview_id
       WHERE o.preview_id=p.id AND (o.outcome='extraction_only' OR (o.outcome='planned' AND r.status='extraction_only'))))))"""

# Stable binding used by both INSERT and permanent verification. No live state,
# expiry, retained body existence, current roles or current item revision here.
_BOUND = f"""SELECT 1 FROM publication_operation p
 JOIN publication_preview preview ON preview.id=g.preview_id AND preview.actor_user_id=p.actor_user_id
   AND preview.artifact_id=p.artifact_id AND preview.extraction_snapshot_id=p.extraction_snapshot_id
 JOIN publication_body_identity requested ON requested.kind=g.requested_kind AND requested.snapshot_id={_id('requested','g.')}
   AND requested.artifact_id=p.artifact_id AND requested.extraction_snapshot_id=p.extraction_snapshot_id
   AND requested.snapshot_created_at<=p.created_at AND requested.status='ready'
 JOIN publication_body_identity selected ON selected.kind=g.selected_kind AND selected.snapshot_id={_id('selected','g.')}
   AND selected.status='ready' AND selected.snapshot_created_at<=p.created_at
 WHERE p.id=g.publication_operation_id
   AND (requested.kind='extraction' OR (requested.preview_id=preview.id AND requested.actor_user_id=p.actor_user_id))
   AND (SELECT count(*) FROM publication_target t WHERE t.publication_operation_id=p.id)
       =CASE p.target_mode WHEN 'both' THEN 2 ELSE 1 END
   AND NOT EXISTS(SELECT 1 FROM publication_target t
     LEFT JOIN knowledge_item item ON item.id=t.item_id
     LEFT JOIN knowledge_revision v ON v.id=t.resolved_revision_id AND v.item_id=t.item_id
     LEFT JOIN knowledge_revision_body body ON body.revision_id=v.id AND body.item_id=v.item_id
     WHERE t.publication_operation_id=p.id AND (t.outcome='update_proposed' OR v.id IS NULL
       OR item.space_id IS NOT t.target_space_id
       OR v.raw_content_sha256 IS NOT selected.raw_sha256 OR v.normalized_content_sha256 IS NOT selected.normalized_sha256
       OR v.content_size_bytes IS NOT selected.size_bytes OR v.normalizer_version IS NOT selected.normalizer_version
       OR (body.revision_id IS NOT NULL AND (body.content_sha256 IS NOT selected.raw_sha256 OR body.content_size_bytes IS NOT selected.size_bytes))))
   AND (p.lineage_group_id IS NULL OR EXISTS(SELECT 1 FROM lineage_group l
     WHERE l.id=p.lineage_group_id AND l.base_content_sha256=selected.normalized_sha256))
   AND ((g.source_publication_operation_id IS NULL AND g.selected_body_root_operation_id=p.id
       AND requested.kind=selected.kind AND requested.snapshot_id=selected.snapshot_id)
     OR EXISTS(SELECT 1 FROM publication_operation old
       JOIN operation_dedup d ON d.id=old.dedup_id AND d.status='committed' AND d.operation_type='publication'
         AND d.actor_user_id=old.actor_user_id AND d.operation_id=old.operation_id AND d.request_sha256=old.request_sha256
       JOIN publication_operation_body old_body ON old_body.publication_operation_id=old.id AND old_body.selection_count<=1
         AND old_body.kind=selected.kind AND old_body.snapshot_id=selected.snapshot_id
       LEFT JOIN publication_body_selection old_selection ON old_selection.publication_operation_id=old.id
       JOIN publication_operation root ON root.id=g.selected_body_root_operation_id AND root.id<>p.id
       JOIN operation_dedup rd ON rd.id=root.dedup_id AND rd.status='committed' AND rd.operation_type='publication'
         AND rd.actor_user_id=root.actor_user_id AND rd.operation_id=root.operation_id AND rd.request_sha256=root.request_sha256
       JOIN publication_operation_body root_body ON root_body.publication_operation_id=root.id AND root_body.selection_count<=1
         AND root_body.kind=selected.kind AND root_body.snapshot_id=selected.snapshot_id
       JOIN source_artifact a ON a.id=p.artifact_id JOIN extraction_snapshot x ON x.id=p.extraction_snapshot_id
       JOIN source_artifact prior ON prior.id=old.artifact_id JOIN extraction_snapshot px ON px.id=old.extraction_snapshot_id
       JOIN publication_target ot ON ot.publication_operation_id=old.id AND ot.outcome<>'update_proposed'
         AND ot.target_space_id=g.source_space_id AND ot.item_id=g.source_item_id
       JOIN knowledge_revision ov ON ov.id=ot.resolved_revision_id AND ov.item_id=ot.item_id
         AND ov.raw_content_sha256=selected.raw_sha256 AND ov.normalized_content_sha256=selected.normalized_sha256
         AND ov.content_size_bytes=selected.size_bytes AND ov.normalizer_version=selected.normalizer_version
       JOIN publication_target nt ON nt.publication_operation_id=p.id AND nt.target_space_id=ot.target_space_id
         AND nt.item_id=ot.item_id AND nt.resolved_revision_id=g.source_revision_id
       WHERE old.id=g.source_publication_operation_id AND old.id<>p.id AND old.created_at<=p.created_at
         AND (old_selection.publication_operation_id IS NULL
           OR old_selection.selected_body_root_operation_id=g.selected_body_root_operation_id)
         AND {_EQUIVALENT_UPLOAD}))"""


def extend_native_publication(statements,postconditions):
    table = f"""CREATE TABLE publication_body_selection(
      publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
      preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
      {_fields('requested')},
      {_fields('selected')},
      selected_body_root_operation_id TEXT NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
      source_publication_operation_id TEXT REFERENCES publication_operation(id) ON DELETE RESTRICT,
      source_space_id TEXT REFERENCES knowledge_space(id) ON DELETE RESTRICT,
      source_item_id TEXT REFERENCES knowledge_item(id) ON DELETE RESTRICT,
      source_revision_id TEXT REFERENCES knowledge_revision(id) ON DELETE RESTRICT,
      {_kind_check('requested')},
      {_kind_check('selected')},
      CHECK((source_publication_operation_id IS NULL AND source_space_id IS NULL AND source_item_id IS NULL AND source_revision_id IS NULL)
        OR (source_publication_operation_id IS NOT NULL AND source_publication_operation_id<>publication_operation_id
          AND source_space_id IS NOT NULL AND source_item_id IS NOT NULL AND source_revision_id IS NOT NULL))
    ) STRICT"""
    additions = [table,_IDENTITIES,_ACTUAL,_CANDIDATES]
    old_tables = ('publication_authored_selection','publication_extraction_reuse',
        'publication_preserved_selection','publication_native_selection')
    exclusive = ' OR '.join(f'EXISTS(SELECT 1 FROM {t} WHERE publication_operation_id=NEW.publication_operation_id)' for t in old_tables)
    columns = ('publication_operation_id','preview_id','requested_kind',
        *(f'requested_{kind}_id' for kind in _KINDS),'selected_kind',
        *(f'selected_{kind}_id' for kind in _KINDS),'selected_body_root_operation_id','source_publication_operation_id',
        'source_space_id','source_item_id','source_revision_id')
    new_row = ','.join(f'NEW.{name} AS {name}' for name in columns)
    additions.append(f"""CREATE TRIGGER publication_body_selection_insert BEFORE INSERT ON publication_body_selection
      WHEN ({exclusive}) OR NOT EXISTS(
        SELECT 1 FROM (SELECT {new_row}) g WHERE EXISTS({_BOUND})
          AND EXISTS(SELECT 1 FROM publication_requested_body_candidate c
            WHERE c.preview_id=g.preview_id AND c.kind=g.requested_kind AND c.snapshot_id={_id('requested','g.')})
          AND (SELECT count(*) FROM publication_requested_body_candidate c WHERE c.preview_id=g.preview_id)=1
          AND (g.source_publication_operation_id IS NULL OR EXISTS(SELECT 1 FROM publication_operation_body b
            WHERE b.publication_operation_id=g.source_publication_operation_id
              AND b.root_operation_id=g.selected_body_root_operation_id))
          AND (g.source_publication_operation_id IS NULL OR EXISTS(SELECT 1 FROM knowledge_item i
            WHERE i.id=g.source_item_id AND i.space_id=g.source_space_id AND i.current_revision_id=g.source_revision_id
              AND i.lifecycle_status='active' AND i.curation_status IN ('published','provisional')))
          AND NOT EXISTS(SELECT 1 FROM publication_target t LEFT JOIN knowledge_revision_body b
            ON b.revision_id=t.resolved_revision_id AND b.item_id=t.item_id
            WHERE t.publication_operation_id=g.publication_operation_id AND b.revision_id IS NULL))
      BEGIN SELECT RAISE(ABORT,'PUBLICATION_BODY_SELECTION_INVALID'); END""")
    for table_name in old_tables:
        additions.append(f"""CREATE TRIGGER publication_body_exclusive_{table_name} BEFORE INSERT ON {table_name}
          WHEN EXISTS(SELECT 1 FROM publication_body_selection WHERE publication_operation_id=NEW.publication_operation_id)
          BEGIN SELECT RAISE(ABORT,'PUBLICATION_BODY_SELECTION_AMBIGUOUS'); END""")
    for verb in ('UPDATE','DELETE'):
        additions.append(f"CREATE TRIGGER publication_body_selection_{verb.lower()} BEFORE {verb} ON publication_body_selection "
            "BEGIN SELECT RAISE(ABORT,'PUBLICATION_BODY_SELECTION_IMMUTABLE'); END")
    # Lineage is inserted before operation/targets/selection. Only the complete
    # current upload and already committed source provenance can be consulted.
    # The receipt guard later binds all target bodies in this same transaction.
    lineage = next(s for s in statements if s.startswith('CREATE TRIGGER trg_lineage_group_validity_insert'))
    head,rest = lineage.split('WHEN ',1)
    condition,body = rest.split('BEGIN ',1)
    prior_body = f'''SELECT 1 FROM publication_requested_body_candidate candidate
      JOIN source_artifact a ON a.id=candidate.artifact_id
      JOIN extraction_snapshot x ON x.id=candidate.extraction_snapshot_id
      JOIN source_artifact prior ON prior.origin_type='upload'
      JOIN extraction_snapshot px ON px.artifact_id=prior.id
      JOIN publication_operation old ON old.artifact_id=prior.id AND old.extraction_snapshot_id=px.id
      JOIN operation_dedup d ON d.id=old.dedup_id AND d.status='committed'
        AND d.actor_user_id=old.actor_user_id AND d.operation_id=old.operation_id
        AND d.operation_type='publication' AND d.request_sha256=old.request_sha256
      JOIN publication_operation_body actual ON actual.publication_operation_id=old.id AND actual.selection_count<=1
      JOIN publication_body_identity b ON b.kind=actual.kind AND b.snapshot_id=actual.snapshot_id AND b.status='ready'
      JOIN publication_target t ON t.publication_operation_id=old.id AND t.outcome<>'update_proposed'
      JOIN knowledge_item i ON i.id=t.item_id AND i.space_id=t.target_space_id
        AND i.lifecycle_status='active' AND i.curation_status IN ('published','provisional')
      JOIN knowledge_revision original ON original.id=t.resolved_revision_id AND original.item_id=i.id
        AND original.raw_content_sha256=b.raw_sha256 AND original.normalized_content_sha256=b.normalized_sha256
        AND original.content_size_bytes=b.size_bytes AND original.normalizer_version=b.normalizer_version
      JOIN knowledge_revision current ON current.id=i.current_revision_id AND current.item_id=i.id
        AND current.raw_content_sha256=b.raw_sha256 AND current.normalized_content_sha256=b.normalized_sha256
        AND current.content_size_bytes=b.size_bytes AND current.normalizer_version=b.normalizer_version
      JOIN knowledge_revision_body retained ON retained.revision_id=current.id AND retained.item_id=i.id
        AND retained.content_sha256=b.raw_sha256 AND retained.content_size_bytes=b.size_bytes
      WHERE candidate.actor_user_id=NEW.created_by AND candidate.artifact_id=NEW.base_artifact_id
        AND candidate.extraction_snapshot_id=NEW.base_extraction_snapshot_id
        AND old.created_at<=NEW.created_at AND b.normalized_sha256=NEW.base_content_sha256
        AND {_EQUIVALENT_UPLOAD}'''
    additions.extend(('DROP TRIGGER trg_lineage_group_validity_insert',
        head+f'WHEN ({condition.strip()}) AND NOT EXISTS({prior_body}) BEGIN '+body))
    # Preserve old guards and add only an exact new-receipt exception.
    receipt = """SELECT 1 FROM publication_body_selection g JOIN publication_operation p ON p.id=g.publication_operation_id
      WHERE g.preview_id=OLD.id AND p.actor_user_id=OLD.actor_user_id AND p.artifact_id=OLD.artifact_id
        AND p.extraction_snapshot_id=OLD.extraction_snapshot_id"""
    native_guard = next(s for s in statements if s.startswith('CREATE TRIGGER native_publication_consume_guard'))
    preserved_guard = next(s for s in V020_STATEMENTS if s.startswith('CREATE TRIGGER preservation_preview_consume_guard'))
    for name,definition in (('native_publication_consume_guard',native_guard),('preservation_preview_consume_guard',preserved_guard)):
        head,rest = definition.split('WHEN ',1)
        condition,body = rest.split('BEGIN ',1)
        additions.extend((f'DROP TRIGGER {name}',head+f'WHEN ({condition.strip()}) AND NOT EXISTS({receipt}) BEGIN '+body))
    native_consumed = postconditions[0].rsplit(' LIMIT 1',1)
    new_postconditions = (native_consumed[0]+" AND NOT EXISTS(SELECT 1 FROM publication_body_selection g "
        "JOIN publication_operation op ON op.id=g.publication_operation_id WHERE g.preview_id=p.id "
        "AND op.actor_user_id=p.actor_user_id AND op.artifact_id=p.artifact_id "
        "AND op.extraction_snapshot_id=p.extraction_snapshot_id) LIMIT 1",*postconditions[1:],
        f"SELECT 'publication body binding mismatch' FROM publication_body_selection g WHERE NOT EXISTS({_BOUND}) LIMIT 1",
        """SELECT 'publication body incomplete commit' FROM publication_body_selection g
          WHERE NOT EXISTS(SELECT 1 FROM publication_operation p
            JOIN publication_preview v ON v.id=g.preview_id AND v.status='consumed'
            JOIN operation_dedup d ON d.id=p.dedup_id AND d.status='committed'
              AND d.operation_type='publication' AND d.actor_user_id=p.actor_user_id
              AND d.operation_id=p.operation_id AND d.request_sha256=p.request_sha256
            WHERE p.id=g.publication_operation_id
              AND json_extract(d.result_json,'$.publication_operation_id')=p.id
              AND json_extract(d.result_json,'$.preview_id')=v.id) LIMIT 1""",
        "SELECT 'publication body selection ambiguous' FROM publication_operation_body WHERE selection_count>1 LIMIT 1")
    return (*statements,*additions),new_postconditions
