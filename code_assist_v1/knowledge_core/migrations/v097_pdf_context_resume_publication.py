"""Additive typed publication of a completed continuation; no old state reset.

The shared eight-kind receipt stays unchanged. A separate immutable sidecar
records the new requested body and the ordinary cross-kind selected witness.
Permanent registration never depends on private body retention or current access.
"""
from .pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from .v058_office_hybrid_publication_body import KINDS as LEGACY_KINDS
from .v031_publication_body_selection import _EQUIVALENT_UPLOAD
from .v032_native_display_storage import _once
from .v014_wiki_authoring import _uuid

KIND = "pdf_continuation_wiki"
KINDS = (*LEGACY_KINDS, KIND)
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"


def _previous(prefix):
    matches = [statement for migration in PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations
        for statement in migration.statements if statement.startswith(prefix)]
    if not matches:
        raise RuntimeError("The frozen publication predecessor is unavailable.")
    return matches[-1]


CONTINUATION_BOUND = f"""SELECT 1 FROM publication_operation operation
  JOIN pdf_continuation_publication_snapshot requested ON requested.snapshot_id=g.requested_snapshot_id
  JOIN publication_body_identity selected ON selected.kind=g.selected_kind AND selected.snapshot_id=g.selected_snapshot_id
  JOIN publication_preview preview ON preview.id=g.preview_id
  WHERE operation.id=g.publication_operation_id AND requested.request_id=g.request_id
    AND requested.preview_id=g.preview_id AND requested.actor_user_id=operation.actor_user_id
    AND operation.artifact_id=requested.artifact_id AND operation.extraction_snapshot_id=requested.extraction_snapshot_id
    AND preview.actor_user_id=operation.actor_user_id AND preview.artifact_id=operation.artifact_id
    AND preview.extraction_snapshot_id=operation.extraction_snapshot_id
    AND preview.source_snapshot_sha256=requested.source_snapshot_sha256
    AND selected.status='ready' AND selected.normalizer_version='markdown_lf_v1'
    AND NOT EXISTS(SELECT 1 FROM publication_target target JOIN knowledge_revision revision
      ON revision.id=target.resolved_revision_id AND revision.item_id=target.item_id
      WHERE target.publication_operation_id=operation.id AND
        (revision.raw_content_sha256<>selected.raw_sha256 OR revision.normalized_content_sha256<>selected.normalized_sha256
          OR revision.content_size_bytes<>selected.size_bytes OR revision.normalizer_version<>selected.normalizer_version))
    AND EXISTS(SELECT 1 FROM publication_target WHERE publication_operation_id=operation.id)
    AND ((g.source_publication_operation_id IS NULL AND g.selected_kind='{KIND}'
      AND g.selected_snapshot_id=g.requested_snapshot_id AND g.selected_body_root_operation_id=operation.id
      AND selected.actor_user_id=operation.actor_user_id)
      OR (g.source_publication_operation_id IS NOT NULL AND EXISTS(
        SELECT 1 FROM publication_operation witness JOIN operation_dedup dedup ON dedup.id=witness.dedup_id
          AND dedup.status='committed' AND dedup.operation_type='publication' AND dedup.actor_user_id=witness.actor_user_id
          AND dedup.operation_id=witness.operation_id AND dedup.request_sha256=witness.request_sha256
        JOIN publication_operation_body actual ON actual.publication_operation_id=witness.id AND actual.selection_count<=1
        JOIN publication_target target ON target.publication_operation_id=witness.id AND target.target_space_id=g.source_space_id
          AND target.item_id=g.source_item_id AND target.outcome<>'update_proposed'
        JOIN source_artifact a ON a.id=operation.artifact_id
        JOIN extraction_snapshot x ON x.id=operation.extraction_snapshot_id AND x.artifact_id=a.id
        JOIN source_artifact prior ON prior.id=witness.artifact_id
        JOIN extraction_snapshot px ON px.id=witness.extraction_snapshot_id AND px.artifact_id=prior.id
        JOIN knowledge_revision revision ON revision.id=g.source_revision_id AND revision.item_id=target.item_id
        WHERE witness.id=g.source_publication_operation_id AND witness.actor_user_id=operation.actor_user_id
          AND witness.created_at<=operation.created_at AND actual.kind=g.selected_kind AND actual.snapshot_id=g.selected_snapshot_id
          AND actual.root_operation_id=g.selected_body_root_operation_id AND {_EQUIVALENT_UPLOAD}
          AND revision.raw_content_sha256=selected.raw_sha256 AND revision.normalized_content_sha256=selected.normalized_sha256
          AND revision.content_size_bytes=selected.size_bytes AND revision.normalizer_version=selected.normalizer_version)))"""


def publication_statements(*, version, complete_job_type, source_current, job_bound):
    """Called after the core v97 declarations; keep module imports acyclic."""
    snapshot = f"""CREATE VIEW pdf_continuation_publication_snapshot AS
      SELECT snapshot.id AS snapshot_id,q.id AS request_id,q.id AS run_id,q.parent_run_id,
        q.parent_preview_id AS preview_id,q.actor_user_id,old.artifact_id,old.extraction_snapshot_id,
        preview.source_snapshot_sha256,q.source_archive_id,q.source_fence_sha256,
        q.parent_plan_sha256 AS source_plan_sha256,q.policy_sha256,
        snapshot.content_sha256,snapshot.content_size_bytes,snapshot.manifest_sha256,snapshot.manifest_size_bytes,
        snapshot.created_at AS snapshot_created_at,q.captured_scope_epoch,q.deadline_at,'{version}' AS policy_version
      FROM pdf_continuation_snapshot snapshot JOIN pdf_continuation_request q ON q.id=snapshot.request_id
      JOIN pdf_continuation_state state ON state.request_id=q.id AND state.status='ready'
      JOIN pdf_continuation_completion completion ON completion.request_id=q.id
      JOIN durable_job complete ON complete.id=completion.job_id AND complete.state='succeeded' AND complete.cancel_requested=0
        AND complete.job_type='{complete_job_type}'
      JOIN durable_job root ON root.id=q.root_job_id AND root.state='succeeded' AND root.cancel_requested=0
      JOIN pdf_visual_authoring_run old ON old.id=q.parent_run_id AND old.preview_id=q.parent_preview_id
        AND old.actor_user_id=q.actor_user_id AND old.source_archive_id=q.source_archive_id
        AND old.source_fence_sha256=q.source_fence_sha256 AND old.source_plan_sha256=q.parent_plan_sha256
      JOIN pdf_visual_authoring_state old_state ON old_state.run_id=old.id AND old_state.status='failed'
      JOIN publication_preview preview ON preview.id=old.preview_id AND preview.actor_user_id=old.actor_user_id
        AND preview.artifact_id=old.artifact_id AND preview.extraction_snapshot_id=old.extraction_snapshot_id
      WHERE NOT EXISTS(SELECT 1 FROM pdf_continuation_stop stop WHERE stop.request_id=q.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot WHERE run_id=old.id)
        AND ({job_bound('root','q',root=True)}) AND ({job_bound('complete','q')})
        AND json_extract(complete.result_json,'$.request_id')=q.id
        AND json_extract(complete.result_json,'$.snapshot_id')=snapshot.id
        AND json_extract(complete.result_json,'$.content_sha256')=snapshot.content_sha256
        AND json_extract(complete.result_json,'$.manifest_sha256')=snapshot.manifest_sha256"""
    candidate = f"""CREATE VIEW pdf_continuation_publication_candidate AS
      SELECT registered.*,preview.row_version AS preview_row_version
      FROM pdf_continuation_publication_snapshot registered
      JOIN pdf_continuation_request q ON q.id=registered.request_id
      JOIN pdf_continuation_snapshot_body body ON body.snapshot_id=registered.snapshot_id
      JOIN pdf_continuation_completion completion ON completion.request_id=q.id
      JOIN durable_job j ON j.id=completion.job_id
      JOIN publication_preview preview ON preview.id=q.parent_preview_id
      WHERE preview.status='ready' AND q.deadline_at>{_NOW} AND ({source_current('q')})
        AND ({job_bound('j','q')})
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_request other JOIN pdf_continuation_state state
          ON state.request_id=other.id WHERE other.parent_preview_id=q.parent_preview_id AND other.id<>q.id
            AND state.status IN ('pending','ready'))"""
    table = f"""CREATE TABLE pdf_continuation_body_selection(
      publication_operation_id TEXT PRIMARY KEY NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
      preview_id TEXT NOT NULL UNIQUE REFERENCES publication_preview(id) ON DELETE RESTRICT,
      request_id TEXT NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      requested_snapshot_id TEXT NOT NULL REFERENCES pdf_continuation_snapshot(id) ON DELETE RESTRICT,
      selected_kind TEXT NOT NULL CHECK(selected_kind IN {KINDS}),
      selected_snapshot_id TEXT NOT NULL CHECK({_uuid('selected_snapshot_id')}),
      selected_body_root_operation_id TEXT NOT NULL REFERENCES publication_operation(id) ON DELETE RESTRICT,
      source_publication_operation_id TEXT REFERENCES publication_operation(id) ON DELETE RESTRICT,
      source_space_id TEXT REFERENCES knowledge_space(id) ON DELETE RESTRICT,
      source_item_id TEXT REFERENCES knowledge_item(id) ON DELETE RESTRICT,
      source_revision_id TEXT REFERENCES knowledge_revision(id) ON DELETE RESTRICT,
      CHECK((source_publication_operation_id IS NULL AND source_space_id IS NULL AND source_item_id IS NULL AND source_revision_id IS NULL)
        OR (source_publication_operation_id IS NOT NULL AND source_space_id IS NOT NULL AND source_item_id IS NOT NULL AND source_revision_id IS NOT NULL))
    ) STRICT"""
    identity = _previous("CREATE VIEW publication_body_identity AS") + f"""
      UNION ALL SELECT '{KIND}',snapshot_id,artifact_id,extraction_snapshot_id,
        content_sha256,content_sha256,content_size_bytes,'markdown_lf_v1',manifest_sha256,manifest_size_bytes,
        policy_version,'ready',run_id,preview_id,snapshot_created_at,actor_user_id
      FROM pdf_continuation_publication_snapshot"""
    old_actual = _previous("CREATE VIEW publication_operation_body AS")
    old_actual = _once(old_actual,
        "WHERE l.selection_count<=1 AND NOT EXISTS(SELECT 1 FROM publication_body_selection g",
        "WHERE l.selection_count<=1 AND NOT EXISTS(SELECT 1 FROM pdf_continuation_body_selection c WHERE c.publication_operation_id=l.publication_operation_id) AND NOT EXISTS(SELECT 1 FROM publication_body_selection g")
    actual = """CREATE VIEW publication_operation_body AS SELECT previous.* FROM (""" + old_actual.removeprefix(
        "CREATE VIEW publication_operation_body AS") + """) previous
      WHERE NOT EXISTS(SELECT 1 FROM pdf_continuation_body_selection c WHERE c.publication_operation_id=previous.publication_operation_id)
      UNION ALL SELECT c.publication_operation_id,c.selected_kind,c.selected_snapshot_id,1,
        c.selected_body_root_operation_id,root.created_at
      FROM pdf_continuation_body_selection c JOIN publication_operation root ON root.id=c.selected_body_root_operation_id"""
    candidates = """CREATE VIEW publication_requested_body_candidate AS SELECT previous.* FROM (""" + _previous(
        "CREATE VIEW publication_requested_body_candidate AS").removeprefix("CREATE VIEW publication_requested_body_candidate AS") + f""") previous
      WHERE NOT EXISTS(SELECT 1 FROM pdf_continuation_request q WHERE q.parent_preview_id=previous.preview_id)
      UNION ALL SELECT c.preview_id,c.actor_user_id,c.artifact_id,c.extraction_snapshot_id,c.source_snapshot_sha256,
        b.kind,b.snapshot_id,b.raw_sha256,b.normalized_sha256,b.size_bytes,b.normalizer_version,b.snapshot_created_at
      FROM pdf_continuation_publication_candidate c JOIN publication_body_identity b
        ON b.kind='{KIND}' AND b.snapshot_id=c.snapshot_id"""
    registered = f"""CREATE VIEW pdf_continuation_registered_publication AS
      SELECT g.requested_snapshot_id AS snapshot_id,g.request_id,g.preview_id,g.publication_operation_id,
        g.selected_kind,g.selected_snapshot_id
      FROM pdf_continuation_body_selection g WHERE EXISTS({CONTINUATION_BOUND})"""
    statements = [table,snapshot,candidate,"DROP VIEW publication_body_identity",identity,
        "DROP VIEW publication_operation_body",actual,"DROP VIEW publication_requested_body_candidate",candidates,registered]
    legacy_tables = ("publication_body_selection","publication_authored_selection","publication_preserved_selection",
        "publication_native_selection","publication_extraction_reuse")
    excluded = " OR ".join(f"EXISTS(SELECT 1 FROM {table} WHERE publication_operation_id=NEW.publication_operation_id)" for table in legacy_tables)
    current_bound = CONTINUATION_BOUND.replace("g.","NEW.")
    statements.append(f"""CREATE TRIGGER pdf_continuation_body_selection_insert BEFORE INSERT ON pdf_continuation_body_selection
      WHEN {excluded} OR NOT EXISTS({current_bound}) OR NOT EXISTS(
        SELECT 1 FROM pdf_continuation_publication_candidate c JOIN publication_operation p ON p.id=NEW.publication_operation_id
        JOIN operation_dedup d ON d.id=p.dedup_id AND d.status='in_progress'
        WHERE c.snapshot_id=NEW.requested_snapshot_id AND c.request_id=NEW.request_id AND c.preview_id=NEW.preview_id
          AND c.actor_user_id=p.actor_user_id AND c.artifact_id=p.artifact_id AND c.extraction_snapshot_id=p.extraction_snapshot_id)
      BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_PUBLICATION_INVALID'); END""")
    for table_name in legacy_tables:
        statements.append(f"""CREATE TRIGGER pdf_continuation_exclusive_{table_name} BEFORE INSERT ON {table_name}
          WHEN EXISTS(SELECT 1 FROM pdf_continuation_body_selection WHERE publication_operation_id=NEW.publication_operation_id)
          BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_PUBLICATION_EXCLUSIVE'); END""")
    for verb in ("UPDATE","DELETE"):
        statements.append(f"""CREATE TRIGGER pdf_continuation_body_selection_{verb.lower()} BEFORE {verb} ON pdf_continuation_body_selection
          BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""")
    for view_name,kind in (("native_display_registered_publication","native_wiki"),
            ("pptx_role_registered_publication","pptx_role_wiki"),("pdf_registered_publication","pdf_wiki"),
            ("pdf_visual_registered_publication","pdf_visual_wiki"),("office_hybrid_registered_publication","office_hybrid_wiki")):
        old = _previous("CREATE VIEW "+view_name+" AS")
        statements.extend(("DROP VIEW "+view_name,old+f""" UNION ALL
          SELECT g.selected_snapshot_id,g.publication_operation_id FROM pdf_continuation_body_selection g
          WHERE g.selected_kind='{kind}' AND EXISTS({CONTINUATION_BOUND})"""))
    receipt = """SELECT 1 FROM pdf_continuation_registered_publication registered JOIN publication_operation operation
      ON operation.id=registered.publication_operation_id WHERE registered.preview_id=OLD.id
        AND operation.actor_user_id=OLD.actor_user_id AND operation.artifact_id=OLD.artifact_id
        AND operation.extraction_snapshot_id=OLD.extraction_snapshot_id"""
    for trigger in ("pdf_visual_authoring_preview_consume_guard","pdf_visual_workflow_consume_guard"):
        old = _previous("CREATE TRIGGER "+trigger+" ")
        head,body = old.split("BEGIN ",1)
        statements.extend(("DROP TRIGGER "+trigger,head+f" AND NOT EXISTS({receipt}) BEGIN "+body))
    old_lineage = _previous("CREATE TRIGGER trg_lineage_group_validity_insert ")
    head,body = old_lineage.split("BEGIN ",1)
    statements.extend(("DROP TRIGGER trg_lineage_group_validity_insert",head+""" AND NOT EXISTS(
      SELECT 1 FROM pdf_continuation_publication_candidate c WHERE c.actor_user_id=NEW.created_by
        AND c.artifact_id=NEW.base_artifact_id AND c.extraction_snapshot_id=NEW.base_extraction_snapshot_id
        AND c.content_sha256=NEW.base_content_sha256) BEGIN """+body))
    return tuple(statements)


PUBLICATION_POSTCONDITION_QUERIES = (
    f"SELECT 'continuation publication binding mismatch' FROM pdf_continuation_body_selection g WHERE NOT EXISTS({CONTINUATION_BOUND}) LIMIT 1",
    "SELECT 'continuation publication selection overlap' FROM pdf_continuation_body_selection c WHERE "
        + " OR ".join("EXISTS(SELECT 1 FROM "+table+" g WHERE g.publication_operation_id=c.publication_operation_id)"
            for table in ("publication_body_selection","publication_authored_selection","publication_preserved_selection",
                "publication_native_selection","publication_extraction_reuse")) + " LIMIT 1",
)

REPLACED_LEGACY_OBJECTS = frozenset((
    "publication_body_identity", "publication_operation_body", "publication_requested_body_candidate",
    "native_display_registered_publication", "pptx_role_registered_publication", "pdf_registered_publication",
    "pdf_visual_registered_publication", "office_hybrid_registered_publication",
    "pdf_visual_authoring_preview_consume_guard", "pdf_visual_workflow_consume_guard", "trg_lineage_group_validity_insert",
))
