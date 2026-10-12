"""Additive PDF continuation ledger; historical v95/v96 contracts are frozen.

This candidate adds no authority to a historical run and relabels no old output.
The normal service must restore retained source/CAS, authenticate exact original
glyph ownership, and recheck current authorization before making these records.
SQL separately pins source and job lineage, full ownership coverage, finite work,
typed provider context refusal, immutable receipt references and final leaves.
No provider, filename, document topic, or model ID grants admission here.
"""
from .v014_wiki_authoring import _sha, _uuid
from .v032_native_display_storage import _exact_object

VERSION = "pdf-source-continuation-v1"
HANDLER_VERSION = "v1"
PLAN = "pdf_continuation.plan"
BATCH = "pdf_continuation.author_batch"
COMPLETE = "pdf_continuation.complete"
TASK = "canonical_pdf_continuation_batch_authoring_v1"
_NOW = "CAST(unixepoch('subsec')*1000 AS INTEGER)"
FAILURE_CODES = ("MODEL_FAILURE", "INVALID_OUTPUT", "TIMEOUT", "WORK_LIMIT",
    "SOURCE_UNAVAILABLE", "AUTHORITY_UNAVAILABLE", "CANCELLED", "LEASE_LOST")
_FAILURES = "(" + ",".join(repr(code) for code in FAILURE_CODES) + ")"
POLICY_FIELDS = {"version": "text", "prompt_sha256": "text", **dict.fromkeys((
    "context_tokens", "output_tokens", "safety_tokens", "max_executions", "max_leaves",
    "max_depth", "max_model_attempts", "max_input_bytes", "max_response_bytes",
    "max_markdown_bytes", "max_manifest_bytes", "max_deadline_ms", "min_codepoints"), "integer")}
RANGE_FIELDS = {"chunk_id": "text", "chunk_ordinal": "integer", "block_id": "text",
    "page": "integer", "start": "integer", "end": "integer", "text_sha256": "text"}
TABLE_FIELDS = {"cell_key": "text", "text_sha256": "text"}
VISUAL_FIELDS = {"page": "integer", "image_id": "text", "sha256": "text"}
COVERAGE_FIELDS = dict.fromkeys(("ranges", "tables", "visuals"), "array")
INVENTORY_FIELDS = {"ordinal": "integer", "batch_id": "text", "request_sha256": "text", "coverage": "object"}
CONTEXT_FIELDS = {"format": "text", "http_status": "integer", "raw_body_sha256": "text",
    "raw_body_size_bytes": "integer", "error_code": "text", "error_type": "text", "request_sha256": "text"}


def _json_bytes(value):
    return f"length(CAST({value} AS BLOB))"


def _coverage_valid(value):
    # Codepoint SHA correspondence is independently authenticated by the normal
    # retained-plan service; SQLite has no arbitrary SHA/hash or CAS read UDF.
    return f"""coalesce(({_exact_object(value, COVERAGE_FIELDS)}),0)
      AND json_array_length({value},'$.ranges')<=4096
      AND json_array_length({value},'$.tables')<=100000
      AND json_array_length({value},'$.visuals')<=1000
      AND NOT EXISTS(SELECT 1 FROM json_each({value},'$.ranges') c WHERE NOT coalesce((
        {_exact_object('c.value', RANGE_FIELDS)} AND length(json_extract(c.value,'$.chunk_id'))=68
        AND substr(json_extract(c.value,'$.chunk_id'),1,4)='PVC_'
        AND {_sha("substr(json_extract(c.value,'$.chunk_id'),5)")}
        AND json_extract(c.value,'$.chunk_ordinal') BETWEEN 1 AND 65536
        AND length(json_extract(c.value,'$.block_id')) BETWEEN 1 AND 200
        AND json_extract(c.value,'$.page') BETWEEN 1 AND 1000
        AND json_extract(c.value,'$.start')>=0 AND json_extract(c.value,'$.end')>json_extract(c.value,'$.start')
        AND {_sha("json_extract(c.value,'$.text_sha256')")}),0))
      AND NOT EXISTS(SELECT 1 FROM json_each({value},'$.tables') c WHERE NOT coalesce((
        {_exact_object('c.value', TABLE_FIELDS)} AND length(json_extract(c.value,'$.cell_key')) BETWEEN 1 AND 300
        AND {_sha("json_extract(c.value,'$.text_sha256')")}),0))
      AND NOT EXISTS(SELECT 1 FROM json_each({value},'$.visuals') c WHERE NOT coalesce((
        {_exact_object('c.value', VISUAL_FIELDS)} AND json_extract(c.value,'$.page') BETWEEN 1 AND 1000
        AND length(json_extract(c.value,'$.image_id'))=67
        AND substr(json_extract(c.value,'$.image_id'),1,3)='PV_'
        AND {_sha("substr(json_extract(c.value,'$.image_id'),4)")}
        AND {_sha("json_extract(c.value,'$.sha256')")}),0))
      AND NOT EXISTS(SELECT 1 FROM json_each({value},'$.ranges') a,json_each({value},'$.ranges') b
        WHERE a.key<b.key AND json_extract(a.value,'$.chunk_id')=json_extract(b.value,'$.chunk_id')
          AND json_extract(a.value,'$.start')<json_extract(b.value,'$.end')
          AND json_extract(b.value,'$.start')<json_extract(a.value,'$.end'))
      AND (SELECT count(*) FROM json_each({value},'$.tables'))=
        (SELECT count(DISTINCT json_extract(value,'$.cell_key')) FROM json_each({value},'$.tables'))
      AND (SELECT count(*) FROM json_each({value},'$.visuals'))=
        (SELECT count(DISTINCT json_extract(value,'$.image_id')) FROM json_each({value},'$.visuals'))"""


def _policy_valid(value):
    return f"""coalesce(({_exact_object(value, POLICY_FIELDS)}),0)
      AND json_extract({value},'$.version')='{VERSION}'
      AND {_sha("json_extract(" + value + ",'$.prompt_sha256')")}
      AND json_extract({value},'$.context_tokens')=16384 AND json_extract({value},'$.output_tokens')=4096
      AND json_extract({value},'$.safety_tokens')=1536 AND json_extract({value},'$.max_executions') BETWEEN 1 AND 2
      AND json_extract({value},'$.max_leaves') BETWEEN 1 AND 4096
      AND json_extract({value},'$.max_depth') BETWEEN 1 AND 8
      AND json_extract({value},'$.max_model_attempts') BETWEEN 1 AND 512
      AND json_extract({value},'$.max_input_bytes') BETWEEN 1 AND 134217728
      AND json_extract({value},'$.max_response_bytes') BETWEEN 1 AND 33554432
      AND json_extract({value},'$.max_markdown_bytes') BETWEEN 1 AND 4194304
      AND json_extract({value},'$.max_manifest_bytes') BETWEEN 1 AND 16777216
      AND json_extract({value},'$.max_deadline_ms') BETWEEN 1 AND 86400000
      AND json_extract({value},'$.min_codepoints') BETWEEN 1 AND 32"""


def _source_current(request="q"):
    # This first execution contract admits only the already registered ordinary
    # V36/V14 forward partition. Historical v95 records remain readable under
    # their frozen catalog, but non-V14/protected/Office plans gain no new lane.
    config = "json_extract(body.bundle_json,'$.configuration_json')"
    return f"""EXISTS(SELECT 1 FROM pdf_visual_authoring_run r
      JOIN pdf_visual_authoring_state state ON state.run_id=r.id
      JOIN pdf_visual_authoring_run_body run_body ON run_body.run_id=r.id
      JOIN pdf_visual_source src ON src.id=r.source_archive_id
      JOIN pdf_visual_source_ready ready ON ready.source_id=src.id
      JOIN pdf_visual_source_body body ON body.source_id=src.id
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=e.id AND eb.artifact_id=r.artifact_id
      JOIN source_artifact a ON a.id=r.artifact_id
      JOIN artifact_blob raw ON raw.artifact_id=a.id AND raw.role='raw' AND raw.ordinal=0
        AND raw.body_retention_status='present' AND raw.content_sha256=a.raw_sha256 AND raw.size_bytes=a.raw_size_bytes
      JOIN artifact_blob_body retained ON retained.artifact_blob_id=raw.id AND retained.content_sha256=raw.content_sha256
      JOIN blob_object object ON object.sha256=retained.content_sha256
        AND object.size_bytes=raw.size_bytes AND object.verification_status='verified'
      JOIN publication_preview p ON p.id=r.preview_id
      JOIN app_user u ON u.id=r.actor_user_id
      JOIN durable_job parent ON parent.id=r.parent_job_id
      JOIN knowledge_space space ON space.id={request}.space_id
      WHERE r.id={request}.parent_run_id AND state.status='failed'
        AND state.failure_code IN ('MODEL_FAILURE','INVALID_OUTPUT','TIMEOUT','WORK_LIMIT')
        AND r.preview_id={request}.parent_preview_id AND p.status='ready'
        AND p.artifact_id=r.artifact_id AND p.extraction_snapshot_id=r.extraction_snapshot_id
        AND p.durable_job_id=r.parent_job_id AND p.actor_user_id=r.actor_user_id
        AND json_extract(r.metadata_json,'$.policy_version')='wiki-pdf-v10-numeric-append-v1'
        AND json_extract({config},'$.policy_version')='wiki-pdf-v10-numeric-append-v1'
        AND json_extract({config},'$.request_partition_policy')='pdf-original-range-request-partition-v14'
        AND json_type({config},'$.protected_binding') IS NULL
        AND json_extract(run_body.configuration_json,'$.request_partition_policy')='pdf-original-range-request-partition-v14'
        AND json_type(run_body.configuration_json,'$.protected_binding') IS NULL
        AND r.actor_user_id={request}.actor_user_id AND src.actor_user_id={request}.actor_user_id
        AND src.id={request}.source_archive_id AND r.source_fence_sha256={request}.source_fence_sha256
        AND src.source_fence_sha256={request}.source_fence_sha256
        AND r.source_plan_sha256={request}.parent_plan_sha256 AND src.plan_sha256={request}.parent_plan_sha256
        AND u.account_status='active' AND u.scope_epoch={request}.captured_scope_epoch
        AND p.captured_scope_epoch=u.scope_epoch
        AND a.body_retention_status='present' AND e.body_retention_status='present' AND e.status='ready'
        AND a.origin_type='upload' AND a.publisher_user_id={request}.actor_user_id
        AND lower(substr(a.original_filename,-4))='.pdf'
        AND e.extracted_sha256=eb.content_sha256 AND p.source_snapshot_sha256=e.extracted_sha256
        AND e.extracted_size_bytes=eb.content_size_bytes
        AND parent.state='succeeded' AND parent.cancel_requested=0 AND parent.actor_user_id={request}.actor_user_id
        AND parent.job_type='publication_preview.extract' AND parent.handler_version='v1'
        AND parent.space_id={request}.space_id AND space.status='active' AND space.write_enabled=1
        AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_attempt pa
          LEFT JOIN pdf_visual_authoring_outcome po ON po.attempt_id=pa.id
          WHERE pa.run_id=r.id AND po.attempt_id IS NULL)
        AND NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot WHERE run_id=r.id))"""


def _retained(request="q"):
    # Historical descriptors can still be finalized as abandoned after current
    # authority is lost. This is private cleanup, never model/publication power.
    return f"""EXISTS(SELECT 1 FROM pdf_visual_authoring_run r
      JOIN pdf_visual_source src ON src.id=r.source_archive_id
      JOIN extraction_snapshot_body eb ON eb.snapshot_id=r.extraction_snapshot_id AND eb.artifact_id=r.artifact_id
      JOIN pdf_visual_source_body body ON body.source_id=src.id
      WHERE r.id={request}.parent_run_id AND src.id={request}.source_archive_id
        AND r.source_fence_sha256={request}.source_fence_sha256 AND r.source_plan_sha256={request}.parent_plan_sha256)"""


def _pending(request="q"):
    return f"""{_source_current(request)} AND {request}.deadline_at>{_NOW}
      AND EXISTS(SELECT 1 FROM pdf_continuation_state state WHERE state.request_id={request}.id AND state.status='pending')
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_stop stop WHERE stop.request_id={request}.id)"""


def _active(leaf="l"):
    return f"""NOT EXISTS(SELECT 1 FROM pdf_continuation_split sp WHERE sp.parent_leaf_id={leaf}.id)
      AND ({leaf}.parent_leaf_id IS NULL OR EXISTS(SELECT 1 FROM pdf_continuation_split sp
        WHERE sp.parent_leaf_id={leaf}.parent_leaf_id AND EXISTS(SELECT 1 FROM json_each(sp.children_json) c WHERE c.value={leaf}.id)))"""


def _context_valid(transport, request_sha):
    value = f"json_extract({transport},'$.context_refusal')"
    # The producer normalizes explicit error.code/type, never error.message.
    # request_sha256 is the locally guarded immutable logical leaf invocation
    # SHA, NOT the HTTP body SHA or a provider cryptographic attestation. The
    # separate captured request_transport pins the actual OpenAI-compatible wire.
    # This shape is evidence of a captured typed refusal, not semantic success.
    shape = _exact_object(value, CONTEXT_FIELDS).replace(
        f"json_type({value},'$.error_code')='text'",
        f"json_type({value},'$.error_code') IN ('text','integer','null')").replace(
        f"json_type({value},'$.error_type')='text'",
        f"json_type({value},'$.error_type') IN ('text','null')")
    return f"""coalesce(({shape}),0)
      AND json_extract({value},'$.format')='provider-context-refusal-v1'
      AND json_extract({value},'$.http_status') IN (400,413,422)
      AND json_extract({value},'$.request_sha256')={request_sha}
      AND {_sha("json_extract(" + value + ",'$.raw_body_sha256')")}
      AND json_extract({value},'$.raw_body_size_bytes') BETWEEN 1 AND 16384
      AND (json_type({value},'$.error_code')='null' OR json_extract({value},'$.error_code')='context_length_exceeded'
        OR (json_type({value},'$.error_code')='integer' AND json_extract({value},'$.error_code')=json_extract({value},'$.http_status')))
      AND (json_type({value},'$.error_type')='null' OR json_extract({value},'$.error_type') IN
        ('exceed_context_size_error','invalid_request_error','context_length_exceeded'))
      AND (json_extract({value},'$.error_code')='context_length_exceeded'
        OR json_extract({value},'$.error_type')='exceed_context_size_error')"""


def _job_bound(job="j", request="q", *, root=False):
    expected = f"{job}.expected_state_json"
    fields = dict(schema_version="integer", version="text", parent_run_id="text", source_archive_id="text",
        source_fence_sha256="text", parent_plan_sha256="text", policy_sha256="text", _authorization_fence="object")
    shape = _exact_object(expected, fields).replace(
        f"json_type({expected},'$._authorization_fence')='object'",
        f"json_type({expected},'$._authorization_fence') IN ('object','null')")
    auth = f"""{job}.authorization_snapshot_json={request}.authorization_snapshot_json
      AND {job}.authorization_snapshot_sha256={request}.authorization_snapshot_sha256
      AND {job}.authorization_scope_epoch={request}.captured_scope_epoch"""
    if not root:
        auth += f""" AND EXISTS(SELECT 1 FROM durable_job root WHERE root.id={request}.root_job_id
          AND {job}.authorization_part_id IS root.authorization_part_id
          AND {job}.authorization_capabilities_json IS root.authorization_capabilities_json
          AND json_extract({expected},'$._authorization_fence') IS json_extract(root.expected_state_json,'$._authorization_fence'))"""
    return f"""coalesce(({shape}),0) AND {job}.handler_version='{HANDLER_VERSION}'
      AND {job}.actor_kind='user' AND {job}.actor_user_id={request}.actor_user_id AND {job}.space_id={request}.space_id
      AND {job}.max_attempts=2 AND {job}.cancellable=1 AND {auth}
      AND json_extract({expected},'$.schema_version')=1 AND json_extract({expected},'$.version')='{VERSION}'
      AND json_extract({expected},'$.parent_run_id')={request}.parent_run_id
      AND json_extract({expected},'$.source_archive_id')={request}.source_archive_id
      AND json_extract({expected},'$.source_fence_sha256')={request}.source_fence_sha256
      AND json_extract({expected},'$.parent_plan_sha256')={request}.parent_plan_sha256
      AND json_extract({expected},'$.policy_sha256')={request}.policy_sha256
      AND json_extract({job}.input_ref_json,'$.request_id')={request}.id
      AND (({job}.job_type IN ('{PLAN}','{COMPLETE}') AND coalesce(({_exact_object(job + '.input_ref_json', {'request_id': 'text'})}),0))
        OR ({job}.job_type='{BATCH}' AND coalesce(({_exact_object(job + '.input_ref_json', {'request_id': 'text', 'leaf_id': 'text'})}),0)
          AND {_uuid("json_extract(" + job + ".input_ref_json,'$.leaf_id')")}))"""


V097_STATEMENTS = (
    f"""CREATE TABLE pdf_continuation_request(
      id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}), operation_id TEXT NOT NULL UNIQUE CHECK({_uuid('operation_id')}),
      parent_run_id TEXT NOT NULL REFERENCES pdf_visual_authoring_run(id) ON DELETE RESTRICT,
      parent_preview_id TEXT NOT NULL REFERENCES publication_preview(id) ON DELETE RESTRICT,
      root_job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
      actor_user_id TEXT NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      space_id TEXT NOT NULL REFERENCES knowledge_space(id) ON DELETE RESTRICT,
      source_archive_id TEXT NOT NULL REFERENCES pdf_visual_source(id) ON DELETE RESTRICT,
      source_fence_sha256 TEXT NOT NULL CHECK({_sha('source_fence_sha256')}),
      parent_plan_sha256 TEXT NOT NULL CHECK({_sha('parent_plan_sha256')}),
      captured_scope_epoch ANY NOT NULL CHECK(typeof(captured_scope_epoch)='integer' AND captured_scope_epoch>=0),
      authorization_snapshot_json TEXT NOT NULL CHECK(json_valid(authorization_snapshot_json) AND json_type(authorization_snapshot_json)='object' AND {_json_bytes('authorization_snapshot_json')} BETWEEN 2 AND 65536),
      authorization_snapshot_sha256 TEXT NOT NULL CHECK({_sha('authorization_snapshot_sha256')}),
      policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object' AND {_json_bytes('policy_json')} BETWEEN 1 AND 65536),
      policy_sha256 TEXT NOT NULL CHECK({_sha('policy_sha256')}),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
      deadline_at ANY NOT NULL CHECK(typeof(deadline_at)='integer' AND deadline_at>created_at)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_state(
      request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      status TEXT NOT NULL CHECK(status IN ('pending','ready','failed')), failure_code TEXT CHECK(failure_code IN {_FAILURES}),
      completed_at ANY CHECK(completed_at IS NULL OR (typeof(completed_at)='integer' AND completed_at>=0)),
      row_version ANY NOT NULL CHECK(typeof(row_version)='integer' AND row_version>0),
      CHECK((status='pending' AND failure_code IS NULL AND completed_at IS NULL) OR
        (status='ready' AND failure_code IS NULL AND completed_at IS NOT NULL) OR
        (status='failed' AND failure_code IS NOT NULL AND completed_at IS NOT NULL))
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_plan_body(
      request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      parent_inventory_json TEXT NOT NULL CHECK(json_valid(parent_inventory_json) AND json_type(parent_inventory_json)='array' AND {_json_bytes('parent_inventory_json')} BETWEEN 2 AND 16777216),
      parent_inventory_sha256 TEXT NOT NULL CHECK({_sha('parent_inventory_sha256')})
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_leaf(
      id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}), request_id TEXT NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      parent_ordinal ANY NOT NULL CHECK(typeof(parent_ordinal)='integer' AND parent_ordinal BETWEEN 1 AND 4096),
      parent_leaf_id TEXT REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      depth ANY NOT NULL CHECK(typeof(depth)='integer' AND depth BETWEEN 0 AND 8),
      kind TEXT NOT NULL CHECK(kind IN ('reuse','write')),
      job_id TEXT UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT,
      coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json) AND json_type(coverage_json)='object' AND {_json_bytes('coverage_json')} BETWEEN 2 AND 16777216),
      coverage_sha256 TEXT NOT NULL CHECK({_sha('coverage_sha256')}),
      request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
      input_size_bytes ANY NOT NULL CHECK(typeof(input_size_bytes)='integer' AND input_size_bytes BETWEEN 1 AND 4194304),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
      CHECK((parent_leaf_id IS NULL AND depth=0) OR (parent_leaf_id IS NOT NULL AND depth>0)),
      CHECK((kind='reuse' AND parent_leaf_id IS NULL AND job_id IS NULL) OR (kind='write' AND job_id IS NOT NULL))
    ) STRICT""",
    "CREATE UNIQUE INDEX idx_pdf_continuation_root_leaf ON pdf_continuation_leaf(request_id,parent_ordinal) WHERE parent_leaf_id IS NULL",
    f"""CREATE TABLE pdf_continuation_leaf_body(
      leaf_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object' AND {_json_bytes('payload_json')} BETWEEN 2 AND 4194304)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_reuse(
      leaf_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      parent_attempt_id TEXT NOT NULL REFERENCES pdf_visual_authoring_outcome(attempt_id) ON DELETE RESTRICT,
      request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}), response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
      model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}), transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')})
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_attempt(
      id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}), operation_id TEXT NOT NULL UNIQUE CHECK({_uuid('operation_id')}),
      leaf_id TEXT NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
      claim_owner TEXT NOT NULL CHECK(length(claim_owner) BETWEEN 1 AND 200),
      claim_attempt ANY NOT NULL CHECK(typeof(claim_attempt)='integer' AND claim_attempt BETWEEN 1 AND 2),
      sequence ANY NOT NULL CHECK(typeof(sequence)='integer' AND sequence BETWEEN 1 AND 2),
      request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
      started_at ANY NOT NULL CHECK(typeof(started_at)='integer' AND started_at>=0),
      UNIQUE(leaf_id,sequence), UNIQUE(job_id,claim_attempt), UNIQUE(id,leaf_id)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_outcome(
      attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_attempt(id) ON DELETE RESTRICT,
      status TEXT NOT NULL CHECK(status IN ('validated','context_refused','failed','abandoned')),
      failure_code TEXT CHECK(failure_code IN {_FAILURES} OR failure_code='CONTEXT_LIMIT'),
      response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
      response_size_bytes ANY NOT NULL CHECK(typeof(response_size_bytes)='integer' AND response_size_bytes BETWEEN 0 AND 65536),
      model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
      model_size_bytes ANY NOT NULL CHECK(typeof(model_size_bytes)='integer' AND model_size_bytes BETWEEN 2 AND 4096),
      transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
      transport_size_bytes ANY NOT NULL CHECK(typeof(transport_size_bytes)='integer' AND transport_size_bytes BETWEEN 2 AND 65536),
      completed_at ANY NOT NULL CHECK(typeof(completed_at)='integer' AND completed_at>=0),
      CHECK((status='validated' AND failure_code IS NULL AND response_size_bytes>0) OR
        (status='context_refused' AND failure_code='CONTEXT_LIMIT' AND response_size_bytes=0) OR
        (status IN ('failed','abandoned') AND failure_code IN {_FAILURES}))
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_outcome_body(
      attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_outcome(attempt_id) ON DELETE RESTRICT,
      response TEXT NOT NULL CHECK({_json_bytes('response')}<=65536),
      model_json TEXT NOT NULL CHECK(json_valid(model_json) AND json_type(model_json)='object' AND {_json_bytes('model_json')}<=4096),
      transport_json TEXT NOT NULL CHECK(json_valid(transport_json) AND json_type(transport_json)='object' AND {_json_bytes('transport_json')}<=65536)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_split(
      parent_leaf_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      refused_attempt_id TEXT NOT NULL UNIQUE REFERENCES pdf_continuation_outcome(attempt_id) ON DELETE RESTRICT,
      children_json TEXT NOT NULL CHECK(json_valid(children_json) AND json_type(children_json)='array' AND json_array_length(children_json) BETWEEN 2 AND 16),
      children_sha256 TEXT NOT NULL CHECK({_sha('children_sha256')}),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_stop(
      request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      job_id TEXT NOT NULL REFERENCES durable_job(id) ON DELETE RESTRICT,
      failure_code TEXT NOT NULL CHECK(failure_code IN {_FAILURES}),
      stopped_at ANY NOT NULL CHECK(typeof(stopped_at)='integer' AND stopped_at>=0)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_completion(
      request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      job_id TEXT NOT NULL UNIQUE REFERENCES durable_job(id) ON DELETE RESTRICT
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_snapshot(
      id TEXT PRIMARY KEY NOT NULL CHECK({_uuid('id')}), request_id TEXT NOT NULL UNIQUE REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      content_sha256 TEXT NOT NULL CHECK({_sha('content_sha256')}),
      content_size_bytes ANY NOT NULL CHECK(typeof(content_size_bytes)='integer' AND content_size_bytes BETWEEN 1 AND 4194304),
      manifest_sha256 TEXT NOT NULL CHECK({_sha('manifest_sha256')}),
      manifest_size_bytes ANY NOT NULL CHECK(typeof(manifest_size_bytes)='integer' AND manifest_size_bytes BETWEEN 1 AND 16777216),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_snapshot_body(
      snapshot_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_snapshot(id) ON DELETE RESTRICT,
      content_md TEXT NOT NULL CHECK({_json_bytes('content_md')} BETWEEN 1 AND 4194304),
      manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object' AND {_json_bytes('manifest_json')} BETWEEN 1 AND 16777216)
    ) STRICT""",
)


def _subset(child, parent):
    """Every child unit derives from the same pinned original ownership."""
    return f"""NOT EXISTS(SELECT 1 FROM json_each({child},'$.ranges') c WHERE NOT EXISTS(
      SELECT 1 FROM json_each({parent},'$.ranges') p
      WHERE json_extract(c.value,'$.chunk_id')=json_extract(p.value,'$.chunk_id')
        AND json_extract(c.value,'$.chunk_ordinal')=json_extract(p.value,'$.chunk_ordinal')
        AND json_extract(c.value,'$.block_id')=json_extract(p.value,'$.block_id')
        AND json_extract(c.value,'$.page')=json_extract(p.value,'$.page')
        AND json_extract(c.value,'$.text_sha256')=json_extract(p.value,'$.text_sha256')
        AND json_extract(c.value,'$.start')>=json_extract(p.value,'$.start')
        AND json_extract(c.value,'$.end')<=json_extract(p.value,'$.end')))
      AND NOT EXISTS(SELECT 1 FROM json_each({child},'$.tables') c WHERE NOT EXISTS(
        SELECT 1 FROM json_each({parent},'$.tables') p WHERE json(p.value)=json(c.value)))
      AND NOT EXISTS(SELECT 1 FROM json_each({child},'$.visuals') c WHERE NOT EXISTS(
        SELECT 1 FROM json_each({parent},'$.visuals') p WHERE json(p.value)=json(c.value)))"""


def _inventory_valid(value, request="q"):
    coverage = "json_extract(i.value,'$.coverage')"
    return f"""json_array_length({value})=(SELECT batch_count FROM pdf_visual_authoring_run WHERE id={request}.parent_run_id)
      AND NOT EXISTS(SELECT 1 FROM json_each({value}) i WHERE NOT coalesce((
        {_exact_object('i.value', INVENTORY_FIELDS)} AND json_extract(i.value,'$.ordinal')=i.key+1
        AND {_coverage_valid(coverage)}
        AND EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b WHERE b.run_id={request}.parent_run_id
          AND b.ordinal=i.key+1 AND b.batch_id=json_extract(i.value,'$.batch_id')
          AND b.request_sha256=json_extract(i.value,'$.request_sha256')
          AND json_array_length(b.members_json)=json_array_length({coverage},'$.ranges')
          AND NOT EXISTS(SELECT 1 FROM json_each(b.members_json) m WHERE NOT EXISTS(
            SELECT 1 FROM json_each({coverage},'$.ranges') c WHERE c.key=m.key
              AND json_extract(c.value,'$.chunk_id')=m.value
              AND json_extract(c.value,'$.chunk_ordinal')=json_extract(b.ordinals_json,'$['||m.key||']'))))
        AND NOT EXISTS(SELECT 1 FROM json_each({coverage},'$.visuals') v WHERE NOT EXISTS(
          SELECT 1 FROM pdf_visual_page p WHERE p.source_id={request}.source_archive_id
            AND p.page=json_extract(v.value,'$.page') AND p.image_id=json_extract(v.value,'$.image_id')
            AND p.content_sha256=json_extract(v.value,'$.sha256')))),0))
      AND NOT EXISTS(SELECT 1 FROM json_each({value}) a,json_each({value}) b,
        json_each(a.value,'$.coverage.tables') x,json_each(b.value,'$.coverage.tables') y
        WHERE a.key<b.key AND json_extract(x.value,'$.cell_key')=json_extract(y.value,'$.cell_key'))
      AND NOT EXISTS(SELECT 1 FROM json_each({value}) a,json_each({value}) b,
        json_each(a.value,'$.coverage.visuals') x,json_each(b.value,'$.coverage.visuals') y
        WHERE a.key<b.key AND json_extract(x.value,'$.image_id')=json_extract(y.value,'$.image_id'))"""


V097_STATEMENTS += (
    f"""CREATE TRIGGER pdf_continuation_request_insert BEFORE INSERT ON pdf_continuation_request
    WHEN NOT ({_policy_valid('NEW.policy_json')}) OR NOT ({_source_current('NEW')})
      OR NEW.deadline_at-NEW.created_at>json_extract(NEW.policy_json,'$.max_deadline_ms')
      OR NEW.deadline_at<={_NOW}
      OR NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_run r JOIN pdf_visual_authoring_state s ON s.run_id=r.id
        JOIN durable_job j ON j.id=NEW.root_job_id WHERE r.id=NEW.parent_run_id
          AND NEW.created_at>=s.completed_at AND NEW.created_at>=j.created_at
          AND j.state='queued' AND j.cancel_requested=0 AND j.job_type='{PLAN}' AND ({_job_bound('j','NEW',root=True)}))
      OR EXISTS(SELECT 1 FROM pdf_continuation_request prior JOIN pdf_continuation_state s ON s.request_id=prior.id
        WHERE prior.parent_run_id=NEW.parent_run_id AND s.status IN ('pending','ready'))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_REQUEST_INVALID'); END""",
    """CREATE TRIGGER pdf_continuation_state_insert BEFORE INSERT ON pdf_continuation_state
    WHEN NEW.status<>'pending' OR NEW.row_version<>1
      OR EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_continuation_request other ON other.parent_run_id=q.parent_run_id
        JOIN pdf_continuation_state s ON s.request_id=other.id WHERE q.id=NEW.request_id AND other.id<>q.id AND s.status IN ('pending','ready'))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_INITIAL_STATE_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_plan_insert BEFORE INSERT ON pdf_continuation_plan_body
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN durable_job j ON j.id=q.root_job_id
      WHERE q.id=NEW.request_id AND ({_pending()}) AND j.state='running' AND j.cancel_requested=0
        AND j.claim_expires_at>{_NOW} AND ({_job_bound('j','q',root=True)})
        AND ({_inventory_valid('NEW.parent_inventory_json')}))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_INVENTORY_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_leaf_insert BEFORE INSERT ON pdf_continuation_leaf
    WHEN NOT ({_coverage_valid('NEW.coverage_json')})
      OR NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_continuation_plan_body b ON b.request_id=q.id
        WHERE q.id=NEW.request_id AND ({_pending()}) AND NEW.created_at>=q.created_at
          AND NEW.depth<=json_extract(q.policy_json,'$.max_depth')
          AND (SELECT count(*) FROM pdf_continuation_leaf WHERE request_id=q.id)<json_extract(q.policy_json,'$.max_leaves')
          AND EXISTS(SELECT 1 FROM json_each(b.parent_inventory_json) inv WHERE inv.key=NEW.parent_ordinal-1
            AND ({_subset('NEW.coverage_json', "json_extract(inv.value,'$.coverage')")}))
          AND ((NEW.parent_leaf_id IS NULL AND EXISTS(SELECT 1 FROM json_each(b.parent_inventory_json) inv
            WHERE inv.key=NEW.parent_ordinal-1 AND json(NEW.coverage_json)=json(json_extract(inv.value,'$.coverage')))
            AND EXISTS(SELECT 1 FROM durable_job root WHERE root.id=q.root_job_id AND root.state='running'
              AND root.cancel_requested=0 AND root.claim_expires_at>{_NOW})
            AND ((NEW.kind='reuse' AND EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome o
              WHERE o.run_id=q.parent_run_id AND o.ordinal=NEW.parent_ordinal AND o.status='validated'))
              OR (NEW.kind='write' AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome o
                WHERE o.run_id=q.parent_run_id AND o.ordinal=NEW.parent_ordinal AND o.status='validated'))))
            OR (NEW.parent_leaf_id IS NOT NULL AND NEW.kind='write' AND EXISTS(
              SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_attempt a ON a.leaf_id=parent.id
              JOIN pdf_continuation_outcome o ON o.attempt_id=a.id JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id
              WHERE parent.id=NEW.parent_leaf_id AND parent.request_id=q.id AND parent.parent_ordinal=NEW.parent_ordinal
                AND parent.kind='write' AND parent.depth+1=NEW.depth AND NEW.created_at>=o.completed_at
                AND o.status='context_refused' AND ({_context_valid('ob.transport_json','a.request_sha256')})
                AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split WHERE parent_leaf_id=parent.id)
                AND ({_subset('NEW.coverage_json','parent.coverage_json')}) AND json(NEW.coverage_json)<>json(parent.coverage_json))))
          AND (NEW.kind='reuse' OR EXISTS(SELECT 1 FROM durable_job j WHERE j.id=NEW.job_id AND j.state='queued'
            AND j.cancel_requested=0 AND j.job_type='{BATCH}' AND ({_job_bound()})
            AND json_extract(j.input_ref_json,'$.leaf_id')=NEW.id)))
      OR EXISTS(SELECT 1 FROM pdf_continuation_leaf prior,json_each(prior.coverage_json,'$.ranges') p,json_each(NEW.coverage_json,'$.ranges') n
        WHERE prior.request_id=NEW.request_id AND prior.parent_ordinal=NEW.parent_ordinal
          AND prior.parent_leaf_id IS NEW.parent_leaf_id
          AND json_extract(p.value,'$.chunk_id')=json_extract(n.value,'$.chunk_id')
          AND json_extract(p.value,'$.start')<json_extract(n.value,'$.end') AND json_extract(n.value,'$.start')<json_extract(p.value,'$.end'))
      OR EXISTS(SELECT 1 FROM pdf_continuation_leaf prior,json_each(prior.coverage_json,'$.tables') p,json_each(NEW.coverage_json,'$.tables') n
        WHERE prior.request_id=NEW.request_id AND prior.parent_leaf_id IS NEW.parent_leaf_id
          AND json_extract(p.value,'$.cell_key')=json_extract(n.value,'$.cell_key'))
      OR EXISTS(SELECT 1 FROM pdf_continuation_leaf prior,json_each(prior.coverage_json,'$.visuals') p,json_each(NEW.coverage_json,'$.visuals') n
        WHERE prior.request_id=NEW.request_id AND prior.parent_leaf_id IS NEW.parent_leaf_id
          AND json_extract(p.value,'$.image_id')=json_extract(n.value,'$.image_id'))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_LEAF_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_leaf_body_insert BEFORE INSERT ON pdf_continuation_leaf_body
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_request q ON q.id=l.request_id
      WHERE l.id=NEW.leaf_id AND l.kind='write' AND ({_pending()})
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_PAYLOAD_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_reuse_insert BEFORE INSERT ON pdf_continuation_reuse
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_request q ON q.id=l.request_id
      JOIN pdf_visual_authoring_batch b ON b.run_id=q.parent_run_id AND b.ordinal=l.parent_ordinal
      JOIN pdf_visual_authoring_attempt a ON a.id=NEW.parent_attempt_id AND a.run_id=b.run_id AND a.ordinal=b.ordinal
      JOIN pdf_visual_authoring_outcome o ON o.attempt_id=a.id JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
      WHERE l.id=NEW.leaf_id AND l.kind='reuse' AND ({_pending()}) AND o.status='validated'
        AND NEW.request_sha256=b.request_sha256 AND NEW.request_sha256=a.request_sha256 AND NEW.request_sha256=l.request_sha256
        AND NEW.response_sha256=o.response_sha256 AND NEW.model_sha256=o.model_sha256 AND NEW.transport_sha256=o.transport_sha256
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf_body WHERE leaf_id=l.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_REUSE_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_attempt_insert BEFORE INSERT ON pdf_continuation_attempt
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body body ON body.leaf_id=l.id
      JOIN pdf_continuation_request q ON q.id=l.request_id JOIN durable_job root ON root.id=q.root_job_id
      JOIN durable_job j ON j.id=l.job_id WHERE l.id=NEW.leaf_id AND l.kind='write' AND ({_active()}) AND ({_pending()})
        AND root.state='succeeded' AND j.id=NEW.job_id AND j.state='running' AND j.cancel_requested=0
        AND j.claim_expires_at>{_NOW} AND j.claim_owner=NEW.claim_owner AND j.attempt_count=NEW.claim_attempt
        AND j.job_type='{BATCH}' AND ({_job_bound()})
        AND NEW.started_at>=l.created_at AND NEW.request_sha256=l.request_sha256
        AND NEW.sequence<=json_extract(q.policy_json,'$.max_executions')
        AND (SELECT count(*) FROM pdf_continuation_leaf WHERE request_id=q.id AND parent_leaf_id IS NULL)=
          (SELECT batch_count FROM pdf_visual_authoring_run WHERE id=q.parent_run_id)
        AND (SELECT count(*) FROM pdf_continuation_attempt pa JOIN pdf_continuation_leaf pl ON pl.id=pa.leaf_id WHERE pl.request_id=q.id)<json_extract(q.policy_json,'$.max_model_attempts')
        AND l.input_size_bytes+(SELECT coalesce(sum(pl.input_size_bytes),0) FROM pdf_continuation_attempt pa JOIN pdf_continuation_leaf pl ON pl.id=pa.leaf_id WHERE pl.request_id=q.id)<=json_extract(q.policy_json,'$.max_input_bytes')
        AND 65536*(1+(SELECT count(*) FROM pdf_continuation_attempt pa JOIN pdf_continuation_leaf pl ON pl.id=pa.leaf_id WHERE pl.request_id=q.id))<=json_extract(q.policy_json,'$.max_response_bytes'))
      OR NEW.sequence<>(SELECT count(*)+1 FROM pdf_continuation_attempt WHERE leaf_id=NEW.leaf_id)
      OR EXISTS(SELECT 1 FROM pdf_continuation_attempt a LEFT JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
        WHERE a.leaf_id=NEW.leaf_id AND (o.attempt_id IS NULL OR o.status IN ('validated','context_refused')))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_ATTEMPT_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_outcome_insert BEFORE INSERT ON pdf_continuation_outcome
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_leaf l ON l.id=a.leaf_id
      JOIN pdf_continuation_request q ON q.id=l.request_id JOIN durable_job j ON j.id=a.job_id
      WHERE a.id=NEW.attempt_id AND NEW.completed_at>=a.started_at
        AND ((NEW.status<>'abandoned' AND ({_active()}) AND ({_pending()})
          AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW}
          AND j.claim_owner=a.claim_owner AND j.attempt_count=a.claim_attempt)
          OR (NEW.status='abandoned' AND ((NEW.failure_code='LEASE_LOST' AND
            (j.attempt_count>a.claim_attempt OR (j.state IN ('running','claimed') AND j.claim_expires_at<={_NOW})))
            OR EXISTS(SELECT 1 FROM pdf_continuation_stop stop WHERE stop.request_id=q.id AND stop.failure_code=NEW.failure_code)))))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_OUTCOME_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_outcome_body_insert BEFORE INSERT ON pdf_continuation_outcome_body
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_outcome o JOIN pdf_continuation_attempt a ON a.id=o.attempt_id
      JOIN pdf_continuation_leaf l ON l.id=a.leaf_id JOIN pdf_continuation_request q ON q.id=l.request_id
      WHERE o.attempt_id=NEW.attempt_id AND ({_retained()})
        AND o.response_size_bytes={_json_bytes('NEW.response')} AND o.model_size_bytes={_json_bytes('NEW.model_json')}
        AND o.transport_size_bytes={_json_bytes('NEW.transport_json')}
        AND ((o.status='context_refused' AND ({_context_valid('NEW.transport_json','a.request_sha256')}))
          OR (o.status<>'context_refused' AND json_type(NEW.transport_json,'$.context_refusal') IS NULL)))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_OUTCOME_BODY_INVALID'); END""",
)


def _union_complete(parent, relation):
    """Subset + disjoint insertion + equal measures imply exact finite cover."""
    return f"""NOT EXISTS(SELECT 1 FROM json_each({parent},'$.ranges') p WHERE
      json_extract(p.value,'$.end')-json_extract(p.value,'$.start')<>(
        SELECT coalesce(sum(json_extract(c.value,'$.end')-json_extract(c.value,'$.start')),0)
        FROM ({relation}) leaf,json_each(leaf.coverage_json,'$.ranges') c
        WHERE json_extract(c.value,'$.chunk_id')=json_extract(p.value,'$.chunk_id')))
      AND json_array_length({parent},'$.tables')=(SELECT count(*) FROM ({relation}) leaf,json_each(leaf.coverage_json,'$.tables'))
      AND json_array_length({parent},'$.visuals')=(SELECT count(*) FROM ({relation}) leaf,json_each(leaf.coverage_json,'$.visuals'))"""


MANIFEST_FIELDS = {"schema_version": "integer", "version": "text", "request_id": "text", "parent_run_id": "text",
    "parent_plan_sha256": "text", "source_fence_sha256": "text", "wiki_sha256": "text", "wiki_bytes": "integer",
    "leaves": "array", "assembly_manifest": "object", "all_original_ranges_retained": "true",
    "all_detected_table_cells_rendered": "true", "all_visual_ownership_retained": "true",
    "semantic_review_performed": "false", "semantic_completeness_proven": "false"}
LEAF_RECEIPT_FIELDS = {"leaf_id": "text", "kind": "text", "parent_ordinal": "integer", "source_attempt_id": "text",
    "request_sha256": "text", "response_sha256": "text", "model_sha256": "text", "transport_sha256": "text"}


V097_STATEMENTS += (
    f"""CREATE TRIGGER pdf_continuation_split_insert BEFORE INSERT ON pdf_continuation_split
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_request q ON q.id=parent.request_id
      JOIN pdf_continuation_attempt a ON a.id=NEW.refused_attempt_id AND a.leaf_id=parent.id
      JOIN pdf_continuation_outcome o ON o.attempt_id=a.id JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id
      WHERE parent.id=NEW.parent_leaf_id AND parent.kind='write' AND ({_pending()}) AND ({_active('parent')})
        AND o.status='context_refused' AND NEW.created_at>=o.completed_at
        AND ({_context_valid('ob.transport_json','a.request_sha256')})
        AND (SELECT count(*) FROM json_each(NEW.children_json))=(SELECT count(DISTINCT value) FROM json_each(NEW.children_json))
        AND NOT EXISTS(SELECT 1 FROM json_each(NEW.children_json) c WHERE c.type<>'text' OR NOT EXISTS(
          SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
          WHERE l.id=c.value AND l.parent_leaf_id=parent.id AND l.request_id=q.id AND l.kind='write'
            AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id)))
        AND json_array_length(NEW.children_json)=(SELECT count(*) FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id)
        AND ({_union_complete('parent.coverage_json', 'SELECT coverage_json FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id')})
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l,json_each(l.coverage_json,'$.ranges') c,
          json_each(parent.coverage_json,'$.ranges') p WHERE l.parent_leaf_id=parent.id
            AND json_extract(c.value,'$.chunk_id')=json_extract(p.value,'$.chunk_id')
            AND (json_extract(c.value,'$.start')<>json_extract(p.value,'$.start') OR json_extract(c.value,'$.end')<>json_extract(p.value,'$.end'))
            AND json_extract(c.value,'$.end')-json_extract(c.value,'$.start')<json_extract(q.policy_json,'$.min_codepoints')))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_SPLIT_INVALID'); END""",
    """CREATE TRIGGER pdf_continuation_stop_insert BEFORE INSERT ON pdf_continuation_stop
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_continuation_state s ON s.request_id=q.id
      WHERE q.id=NEW.request_id AND s.status='pending' AND NEW.stopped_at>=q.created_at AND
        (q.root_job_id=NEW.job_id OR EXISTS(SELECT 1 FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND l.job_id=NEW.job_id)
          OR EXISTS(SELECT 1 FROM pdf_continuation_completion c WHERE c.request_id=q.id AND c.job_id=NEW.job_id)))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_STOP_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_completion_insert BEFORE INSERT ON pdf_continuation_completion
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN durable_job j ON j.id=NEW.job_id
      JOIN durable_job root ON root.id=q.root_job_id WHERE q.id=NEW.request_id AND ({_pending()})
        AND root.state='succeeded' AND j.job_type='{COMPLETE}' AND j.state='queued' AND j.cancel_requested=0 AND ({_job_bound()})
        AND (SELECT count(*) FROM pdf_continuation_leaf WHERE request_id=q.id AND parent_leaf_id IS NULL)=
          (SELECT batch_count FROM pdf_visual_authoring_run WHERE id=q.parent_run_id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND ({_active()}) AND NOT (
          (l.kind='reuse' AND EXISTS(SELECT 1 FROM pdf_continuation_reuse WHERE leaf_id=l.id)) OR
          (l.kind='write' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
            JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id JOIN durable_job child ON child.id=a.job_id
            WHERE a.leaf_id=l.id AND o.status='validated' AND child.state='succeeded' AND child.cancel_requested=0)))))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_COMPLETION_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_snapshot_insert BEFORE INSERT ON pdf_continuation_snapshot
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_continuation_completion c ON c.request_id=q.id
      JOIN durable_job j ON j.id=c.job_id WHERE q.id=NEW.request_id AND ({_pending()})
        AND j.state='running' AND j.cancel_requested=0 AND j.claim_expires_at>{_NOW} AND ({_job_bound()})
        AND NEW.created_at>=q.created_at AND NEW.content_size_bytes<=json_extract(q.policy_json,'$.max_markdown_bytes')
        AND NEW.manifest_size_bytes<=json_extract(q.policy_json,'$.max_manifest_bytes')
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND ({_active()}) AND NOT (
          (l.kind='reuse' AND EXISTS(SELECT 1 FROM pdf_continuation_reuse reuse JOIN pdf_visual_authoring_outcome_body ob
            ON ob.attempt_id=reuse.parent_attempt_id WHERE reuse.leaf_id=l.id)) OR
          (l.kind='write' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
            JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id JOIN durable_job child ON child.id=a.job_id
            WHERE a.leaf_id=l.id AND o.status='validated' AND child.state='succeeded' AND child.cancel_requested=0)))))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_SNAPSHOT_INCOMPLETE'); END""",
    f"""CREATE TRIGGER pdf_continuation_snapshot_body_insert BEFORE INSERT ON pdf_continuation_snapshot_body
    WHEN NOT coalesce(({_exact_object('NEW.manifest_json', MANIFEST_FIELDS)}),0)
      OR NOT EXISTS(SELECT 1 FROM pdf_continuation_snapshot s JOIN pdf_continuation_request q ON q.id=s.request_id
        WHERE s.id=NEW.snapshot_id AND ({_pending()})
          AND s.content_size_bytes={_json_bytes('NEW.content_md')} AND s.manifest_size_bytes={_json_bytes('NEW.manifest_json')}
          AND json_extract(NEW.manifest_json,'$.schema_version')=1 AND json_extract(NEW.manifest_json,'$.version')='{VERSION}'
          AND json_extract(NEW.manifest_json,'$.request_id')=q.id AND json_extract(NEW.manifest_json,'$.parent_run_id')=q.parent_run_id
          AND json_extract(NEW.manifest_json,'$.parent_plan_sha256')=q.parent_plan_sha256
          AND json_extract(NEW.manifest_json,'$.source_fence_sha256')=q.source_fence_sha256
          AND json_extract(NEW.manifest_json,'$.wiki_sha256')=s.content_sha256 AND json_extract(NEW.manifest_json,'$.wiki_bytes')=s.content_size_bytes
          AND json_array_length(NEW.manifest_json,'$.leaves')=(SELECT count(*) FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND ({_active()}))
          AND (SELECT count(*) FROM json_each(NEW.manifest_json,'$.leaves'))=(SELECT count(DISTINCT json_extract(value,'$.leaf_id')) FROM json_each(NEW.manifest_json,'$.leaves'))
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.leaves') receipt WHERE NOT EXISTS(
            SELECT 1 FROM pdf_continuation_leaf l WHERE l.id=json_extract(receipt.value,'$.leaf_id') AND l.request_id=q.id AND ({_active()})
              AND coalesce(({_exact_object('receipt.value', LEAF_RECEIPT_FIELDS)}),0)
              AND json_extract(receipt.value,'$.kind')=l.kind AND json_extract(receipt.value,'$.parent_ordinal')=l.parent_ordinal
              AND json_extract(receipt.value,'$.request_sha256')=l.request_sha256
              AND ((l.kind='reuse' AND EXISTS(SELECT 1 FROM pdf_continuation_reuse reuse WHERE reuse.leaf_id=l.id
                AND json_extract(receipt.value,'$.source_attempt_id')=reuse.parent_attempt_id
                AND json_extract(receipt.value,'$.response_sha256')=reuse.response_sha256
                AND json_extract(receipt.value,'$.model_sha256')=reuse.model_sha256
                AND json_extract(receipt.value,'$.transport_sha256')=reuse.transport_sha256)) OR
                (l.kind='write' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
                  WHERE a.leaf_id=l.id AND o.status='validated' AND json_extract(receipt.value,'$.source_attempt_id')=a.id
                    AND json_extract(receipt.value,'$.response_sha256')=o.response_sha256
                    AND json_extract(receipt.value,'$.model_sha256')=o.model_sha256
                    AND json_extract(receipt.value,'$.transport_sha256')=o.transport_sha256))))))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_SNAPSHOT_BODY_INVALID'); END""",
    """CREATE TRIGGER pdf_continuation_state_update BEFORE UPDATE ON pdf_continuation_state
    WHEN NEW.request_id<>OLD.request_id OR OLD.status<>'pending' OR NEW.status NOT IN ('ready','failed')
      OR NEW.row_version<>OLD.row_version+1
      OR NEW.completed_at<(SELECT created_at FROM pdf_continuation_request WHERE id=OLD.request_id)
      OR (NEW.status='ready' AND NOT EXISTS(SELECT 1 FROM pdf_continuation_snapshot s
        JOIN pdf_continuation_snapshot_body b ON b.snapshot_id=s.id WHERE s.request_id=OLD.request_id))
      OR (NEW.status='failed' AND EXISTS(SELECT 1 FROM pdf_continuation_snapshot WHERE request_id=OLD.request_id))
      OR (NEW.status='failed' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_leaf l ON l.id=a.leaf_id
        LEFT JOIN pdf_continuation_outcome o ON o.attempt_id=a.id WHERE l.request_id=OLD.request_id AND o.attempt_id IS NULL))
      OR (NEW.status='failed' AND NOT EXISTS(SELECT 1 FROM pdf_continuation_stop stop
        WHERE stop.request_id=OLD.request_id AND stop.failure_code=NEW.failure_code)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_leaf l ON l.id=a.leaf_id
          JOIN pdf_continuation_outcome o ON o.attempt_id=a.id WHERE l.request_id=OLD.request_id
            AND o.status IN ('failed','abandoned') AND o.failure_code=NEW.failure_code))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_TERMINAL_INVALID'); END""",
)


_SUCCESS = {
    PLAN: f"""EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_continuation_plan_body b ON b.request_id=q.id
      WHERE q.root_job_id=NEW.id AND ({_job_bound('NEW','q',root=True)})
        AND json_extract(NEW.result_json,'$.request_id')=q.id
        AND json_extract(NEW.result_json,'$.parent_inventory_sha256')=b.parent_inventory_sha256
        AND json_extract(NEW.result_json,'$.root_leaf_count')=(SELECT batch_count FROM pdf_visual_authoring_run WHERE id=q.parent_run_id)
        AND (SELECT count(*) FROM pdf_continuation_leaf WHERE request_id=q.id AND parent_leaf_id IS NULL)=json_extract(NEW.result_json,'$.root_leaf_count')
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND l.parent_leaf_id IS NULL AND
          ((l.kind='reuse' AND NOT EXISTS(SELECT 1 FROM pdf_continuation_reuse WHERE leaf_id=l.id)) OR
            (l.kind='write' AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf_body WHERE leaf_id=l.id)))))""",
    BATCH: f"""EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
      JOIN pdf_continuation_outcome_body b ON b.attempt_id=o.attempt_id JOIN pdf_continuation_leaf l ON l.id=a.leaf_id
      JOIN pdf_continuation_request q ON q.id=l.request_id WHERE a.job_id=NEW.id AND a.claim_attempt=NEW.attempt_count
        AND o.status IN ('validated','context_refused') AND ({_job_bound('NEW','q')})
        AND json_extract(NEW.result_json,'$.request_id')=q.id AND json_extract(NEW.result_json,'$.leaf_id')=l.id
        AND json_extract(NEW.result_json,'$.attempt_id')=a.id AND json_extract(NEW.result_json,'$.status')=o.status
        AND (o.status='validated' OR EXISTS(SELECT 1 FROM pdf_continuation_split sp WHERE sp.parent_leaf_id=l.id AND sp.refused_attempt_id=a.id)))""",
    COMPLETE: f"""EXISTS(SELECT 1 FROM pdf_continuation_completion c JOIN pdf_continuation_snapshot s ON s.request_id=c.request_id
      JOIN pdf_continuation_snapshot_body b ON b.snapshot_id=s.id JOIN pdf_continuation_state state ON state.request_id=c.request_id
      JOIN pdf_continuation_request q ON q.id=c.request_id WHERE c.job_id=NEW.id AND state.status='ready' AND ({_job_bound('NEW','q')})
        AND json_extract(NEW.result_json,'$.request_id')=q.id AND json_extract(NEW.result_json,'$.snapshot_id')=s.id
        AND json_extract(NEW.result_json,'$.content_sha256')=s.content_sha256 AND json_extract(NEW.result_json,'$.manifest_sha256')=s.manifest_sha256)""",
}
_RESULT_FIELDS = {
    PLAN: {"schema_version": "integer", "request_id": "text", "parent_inventory_sha256": "text", "root_leaf_count": "integer"},
    BATCH: {"schema_version": "integer", "request_id": "text", "leaf_id": "text", "attempt_id": "text", "status": "text"},
    COMPLETE: {"schema_version": "integer", "request_id": "text", "snapshot_id": "text", "content_sha256": "text", "manifest_sha256": "text"},
}
V097_STATEMENTS += tuple(f"""CREATE TRIGGER pdf_continuation_job_success_{i} BEFORE UPDATE OF state ON durable_job
  WHEN NEW.job_type='{kind}' AND NEW.handler_version='{HANDLER_VERSION}' AND NEW.state='succeeded'
    AND (NOT coalesce(({_exact_object('NEW.result_json', _RESULT_FIELDS[kind])}),0)
      OR json_extract(NEW.result_json,'$.schema_version')<>1 OR NOT ({query}))
  BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_JOB_SUCCESS_INVALID'); END"""
  for i, (kind, query) in enumerate(_SUCCESS.items()))

_IMMUTABLE = ("pdf_continuation_request", "pdf_continuation_leaf", "pdf_continuation_reuse",
    "pdf_continuation_attempt", "pdf_continuation_outcome", "pdf_continuation_split",
    "pdf_continuation_stop", "pdf_continuation_completion", "pdf_continuation_snapshot")
_BODIES = ("pdf_continuation_plan_body", "pdf_continuation_leaf_body", "pdf_continuation_outcome_body", "pdf_continuation_snapshot_body")
V097_STATEMENTS += tuple(f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_IMMUTABLE'); END"
    for table in _IMMUTABLE for verb in ("UPDATE", "DELETE"))
V097_STATEMENTS += tuple(f"CREATE TRIGGER {table}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_IMMUTABLE'); END" for table in _BODIES)
V097_STATEMENTS += (
    "CREATE TRIGGER pdf_continuation_state_delete BEFORE DELETE ON pdf_continuation_state BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_IMMUTABLE'); END",
    """CREATE TRIGGER pdf_continuation_pending_purge BEFORE DELETE ON extraction_snapshot_body
      WHEN EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id
        JOIN pdf_continuation_state s ON s.request_id=q.id WHERE r.extraction_snapshot_id=OLD.snapshot_id AND s.status='pending')
      BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_PENDING_WORK'); END""",
)
_BODY_REQUESTS = {
    "pdf_continuation_plan_body": "SELECT q.* FROM pdf_continuation_request q WHERE q.id=OLD.request_id",
    "pdf_continuation_leaf_body": "SELECT q.* FROM pdf_continuation_request q JOIN pdf_continuation_leaf l ON l.request_id=q.id WHERE l.id=OLD.leaf_id",
    "pdf_continuation_outcome_body": "SELECT q.* FROM pdf_continuation_request q JOIN pdf_continuation_leaf l ON l.request_id=q.id JOIN pdf_continuation_attempt a ON a.leaf_id=l.id WHERE a.id=OLD.attempt_id",
    "pdf_continuation_snapshot_body": "SELECT q.* FROM pdf_continuation_request q JOIN pdf_continuation_snapshot s ON s.request_id=q.id WHERE s.id=OLD.snapshot_id",
}
V097_STATEMENTS += tuple(f"""CREATE TRIGGER {table}_retain BEFORE DELETE ON {table}
  WHEN EXISTS(SELECT 1 FROM ({relation}) q WHERE ({_retained()}))
  BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_BODY_RETAINED'); END""" for table, relation in _BODY_REQUESTS.items())
V097_STATEMENTS += (
    """CREATE TRIGGER pdf_continuation_body_cleanup AFTER DELETE ON extraction_snapshot_body
      BEGIN
        DELETE FROM pdf_continuation_snapshot_body WHERE snapshot_id IN (SELECT s.id FROM pdf_continuation_snapshot s
          JOIN pdf_continuation_request q ON q.id=s.request_id JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id WHERE r.extraction_snapshot_id=OLD.snapshot_id);
        DELETE FROM pdf_continuation_outcome_body WHERE attempt_id IN (SELECT a.id FROM pdf_continuation_attempt a
          JOIN pdf_continuation_leaf l ON l.id=a.leaf_id JOIN pdf_continuation_request q ON q.id=l.request_id
          JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id WHERE r.extraction_snapshot_id=OLD.snapshot_id);
        DELETE FROM pdf_continuation_leaf_body WHERE leaf_id IN (SELECT l.id FROM pdf_continuation_leaf l
          JOIN pdf_continuation_request q ON q.id=l.request_id JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id WHERE r.extraction_snapshot_id=OLD.snapshot_id);
        DELETE FROM pdf_continuation_plan_body WHERE request_id IN (SELECT q.id FROM pdf_continuation_request q
          JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id WHERE r.extraction_snapshot_id=OLD.snapshot_id);
      END""",
)

V097_POSTCONDITION_QUERIES = (
    """SELECT 'continuation root lineage mismatch' FROM pdf_continuation_request q
      JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id JOIN pdf_visual_source s ON s.id=q.source_archive_id
      JOIN durable_job j ON j.id=q.root_job_id WHERE q.parent_preview_id<>r.preview_id OR q.actor_user_id<>r.actor_user_id
        OR q.source_archive_id<>r.source_archive_id OR q.source_fence_sha256<>r.source_fence_sha256
        OR q.parent_plan_sha256<>r.source_plan_sha256 OR q.source_archive_id<>s.id OR j.actor_user_id<>q.actor_user_id OR j.space_id<>q.space_id LIMIT 1""",
    """SELECT 'continuation reuse receipt mismatch' FROM pdf_continuation_reuse reuse
      JOIN pdf_continuation_leaf l ON l.id=reuse.leaf_id JOIN pdf_continuation_request q ON q.id=l.request_id
      JOIN pdf_visual_authoring_attempt a ON a.id=reuse.parent_attempt_id JOIN pdf_visual_authoring_outcome o ON o.attempt_id=a.id
      WHERE l.kind<>'reuse' OR a.run_id<>q.parent_run_id OR a.ordinal<>l.parent_ordinal OR o.status<>'validated'
        OR a.request_sha256<>reuse.request_sha256 OR l.request_sha256<>reuse.request_sha256
        OR o.response_sha256<>reuse.response_sha256 OR o.model_sha256<>reuse.model_sha256 OR o.transport_sha256<>reuse.transport_sha256 LIMIT 1""",
    """SELECT 'continuation child lineage mismatch' FROM pdf_continuation_leaf child
      JOIN pdf_continuation_leaf parent ON parent.id=child.parent_leaf_id WHERE child.request_id<>parent.request_id
        OR child.parent_ordinal<>parent.parent_ordinal OR child.depth<>parent.depth+1 OR child.kind<>'write' LIMIT 1""",
    """SELECT 'continuation pending outcome lacks body' FROM pdf_continuation_outcome o
      JOIN pdf_continuation_attempt a ON a.id=o.attempt_id JOIN pdf_continuation_leaf l ON l.id=a.leaf_id
      JOIN pdf_continuation_request q ON q.id=l.request_id JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id
      JOIN extraction_snapshot_body retained ON retained.snapshot_id=r.extraction_snapshot_id
      LEFT JOIN pdf_continuation_outcome_body b ON b.attempt_id=o.attempt_id WHERE b.attempt_id IS NULL LIMIT 1""",
    """SELECT 'continuation ready snapshot mismatch' FROM pdf_continuation_state state
      WHERE (state.status='ready')<>EXISTS(SELECT 1 FROM pdf_continuation_snapshot s WHERE s.request_id=state.request_id) LIMIT 1""",
    """SELECT 'continuation retained snapshot mismatch' FROM pdf_continuation_snapshot s
      JOIN pdf_continuation_request q ON q.id=s.request_id JOIN pdf_visual_authoring_run r ON r.id=q.parent_run_id
      LEFT JOIN extraction_snapshot_body retained ON retained.snapshot_id=r.extraction_snapshot_id
      LEFT JOIN pdf_continuation_snapshot_body body ON body.snapshot_id=s.id
      WHERE (retained.snapshot_id IS NULL)<>(body.snapshot_id IS NULL) LIMIT 1""",
)

# A completed continuation has its own requested-body identity and immutable
# registration sidecar. It never impersonates a successful historical run or
# bypasses the existing normal atomic index/log/projection publication path.
from .v097_pdf_context_resume_publication import (
    publication_statements, PUBLICATION_POSTCONDITION_QUERIES,
)
V097_STATEMENTS += publication_statements(version=VERSION, complete_job_type=COMPLETE,
    source_current=_source_current, job_bound=_job_bound)
V097_POSTCONDITION_QUERIES += PUBLICATION_POSTCONDITION_QUERIES
