"""Explicit PDF-only terminal-result reuse successor; v97 stays frozen.

Only a fresh request carrying the immutable v98 admission marker can inherit
actual historical partitions or reference an actual historical model outcome.
Destination leaves remain ``write`` leaves with their own normal queued jobs.
No synthetic attempt, provider refusal, old-row update, or reference chain is
introduced. SQL checks immutable lineage and current ordinary source/job gates;
the normal service still restores CAS and authenticates SHA/raw/model/transport,
exact source glyph coverage and stored output decoding (SQLite has no SHA UDF).
"""
from .v014_wiki_authoring import _sha, _uuid
from .v032_native_display_storage import _exact_object
from . import v097_pdf_context_resume as v97

VERSION = v97.VERSION
HANDLER_VERSION = v97.HANDLER_VERSION
PLAN, BATCH, COMPLETE = v97.PLAN, v97.BATCH, v97.COMPLETE
_NOW = v97._NOW
MANIFEST_FIELDS = v97.MANIFEST_FIELDS
LEAF_RECEIPT_FIELDS = {
    **v97.LEAF_RECEIPT_FIELDS,
    "execution_source": "text", "origin_request_id": "text", "origin_leaf_id": "text",
}
REFERENCE_RESULT_FIELDS = {
    "schema_version": "integer", "request_id": "text", "leaf_id": "text",
    "source_attempt_id": "text", "status": "text",
}


def _marked(request="q"):
    return f"EXISTS(SELECT 1 FROM pdf_continuation_retained_request marker WHERE marker.request_id={request}.id)"


def _planning(request="q"):
    return f"""({_marked(request)}) AND ({v97._pending(request)})
      AND EXISTS(SELECT 1 FROM durable_job root WHERE root.id={request}.root_job_id
        AND root.job_type='{PLAN}' AND root.state='running' AND root.cancel_requested=0 AND root.claim_expires_at>{_NOW}
        AND ({v97._job_bound('root', request, root=True)}))"""


def _same_requests(destination="q", source="sq"):
    # The source failed request supplies evidence, not renewed execution power.
    fields = (
        "parent_run_id", "parent_preview_id", "actor_user_id", "space_id",
        "source_archive_id", "source_fence_sha256", "parent_plan_sha256",
        "captured_scope_epoch", "authorization_snapshot_json",
        "authorization_snapshot_sha256", "policy_json", "policy_sha256",
    )
    equal = " AND ".join(f"{source}.{field}={destination}.{field}" for field in fields)
    return f"""{source}.id<>{destination}.id AND {equal}
      AND EXISTS(SELECT 1 FROM pdf_continuation_state prior_state
        WHERE prior_state.request_id={source}.id AND prior_state.status='failed'
          AND prior_state.completed_at<{destination}.created_at)
      AND {source}.created_at<{destination}.created_at"""


def _same_leaf(destination="l", source="sl", destination_body="lb", source_body="slb"):
    return f"""{destination}.kind='write' AND {source}.kind='write'
      AND {destination}.parent_ordinal={source}.parent_ordinal
      AND {destination}.coverage_sha256={source}.coverage_sha256
      AND {destination}.coverage_json={source}.coverage_json
      AND {destination}.request_sha256={source}.request_sha256
      AND {destination}.input_size_bytes={source}.input_size_bytes
      AND {destination_body}.payload_json={source_body}.payload_json"""


def _actual_origin(leaf="sl", attempt="sa", outcome="so", job="sj", request="sq"):
    return f"""{leaf}.kind='write' AND {attempt}.leaf_id={leaf}.id
      AND {attempt}.job_id={leaf}.job_id AND {attempt}.request_sha256={leaf}.request_sha256
      AND {outcome}.attempt_id={attempt}.id AND {job}.id={attempt}.job_id
      AND {job}.state='succeeded' AND {job}.cancel_requested=0
      AND {job}.job_type='{BATCH}' AND ({v97._job_bound(job, request)})
      AND {attempt}.claim_attempt={job}.attempt_count
      AND coalesce(({_exact_object(job + '.result_json', v97._RESULT_FIELDS[BATCH])}),0)
      AND json_extract({job}.result_json,'$.schema_version')=1
      AND json_extract({job}.result_json,'$.request_id')={request}.id
      AND json_extract({job}.result_json,'$.leaf_id')={leaf}.id
      AND json_extract({job}.result_json,'$.attempt_id')={attempt}.id
      AND json_extract({job}.result_json,'$.status')={outcome}.status
      AND EXISTS(SELECT 1 FROM durable_job source_root WHERE source_root.id={request}.root_job_id
        AND source_root.state='succeeded' AND source_root.cancel_requested=0 AND source_root.job_type='{PLAN}'
        AND ({v97._job_bound('source_root', request, root=True)}))
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_result_reference prior_ref WHERE prior_ref.leaf_id={leaf}.id)
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition prior_partition WHERE prior_partition.parent_leaf_id={leaf}.id)"""


def _unexecuted(leaf="l"):
    return f"""NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt own_attempt WHERE own_attempt.leaf_id={leaf}.id)
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_result_reference own_ref WHERE own_ref.leaf_id={leaf}.id)
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split own_split WHERE own_split.parent_leaf_id={leaf}.id)
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition own_partition WHERE own_partition.parent_leaf_id={leaf}.id)"""


def _active(leaf="l"):
    """Both direct and inherited parents are inactive; no orphan is active."""
    return f"""({v97._active(leaf)})
      AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip WHERE ip.parent_leaf_id={leaf}.id)
      OR (EXISTS(SELECT 1 FROM pdf_continuation_retained_request marker WHERE marker.request_id={leaf}.request_id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split sp WHERE sp.parent_leaf_id={leaf}.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip WHERE ip.parent_leaf_id={leaf}.id)
        AND EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip WHERE ip.parent_leaf_id={leaf}.parent_leaf_id
          AND EXISTS(SELECT 1 FROM json_each(ip.children_json) child WHERE child.value={leaf}.id)))"""


def _reference_ready(leaf="l", request="q"):
    return f"""EXISTS(SELECT 1 FROM pdf_continuation_result_reference ref
      JOIN pdf_continuation_attempt sa ON sa.id=ref.source_attempt_id
      JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id
      JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
      JOIN pdf_continuation_leaf_body lb ON lb.leaf_id={leaf}.id
      JOIN pdf_continuation_request sq ON sq.id=sl.request_id
      JOIN durable_job sj ON sj.id=sa.job_id JOIN durable_job child ON child.id={leaf}.job_id
      WHERE ref.leaf_id={leaf}.id AND ({_marked(request)}) AND ({_same_requests(request, 'sq')})
        AND ({_same_leaf(leaf)}) AND ({_actual_origin()}) AND so.status='validated'
        AND ref.request_sha256={leaf}.request_sha256 AND ref.request_sha256=sa.request_sha256
        AND ref.response_sha256=so.response_sha256 AND ref.model_sha256=so.model_sha256
        AND ref.transport_sha256=so.transport_sha256
        AND child.state='succeeded' AND child.cancel_requested=0 AND child.job_type='{BATCH}'
        AND ({v97._job_bound('child', request)})
        AND coalesce(({_exact_object('child.result_json', REFERENCE_RESULT_FIELDS)}),0)
        AND json_extract(child.result_json,'$.schema_version')=2
        AND json_extract(child.result_json,'$.request_id')={request}.id
        AND json_extract(child.result_json,'$.leaf_id')={leaf}.id
        AND json_extract(child.result_json,'$.source_attempt_id')=ref.source_attempt_id
        AND json_extract(child.result_json,'$.status')='referenced_result'
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt own_attempt WHERE own_attempt.leaf_id={leaf}.id))"""


def _partition_ready(parent="parent", request="q"):
    return f"""EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition partition
      JOIN pdf_continuation_attempt sa ON sa.id=partition.refused_attempt_id
      JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id
      JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
      JOIN pdf_continuation_leaf_body pb ON pb.leaf_id={parent}.id
      JOIN pdf_continuation_request sq ON sq.id=sl.request_id
      JOIN durable_job sj ON sj.id=sa.job_id JOIN durable_job parent_job ON parent_job.id={parent}.job_id
      JOIN pdf_continuation_split source_split ON source_split.parent_leaf_id=sl.id AND source_split.refused_attempt_id=sa.id
      WHERE partition.parent_leaf_id={parent}.id AND ({_marked(request)}) AND ({_same_requests(request, 'sq')})
        AND ({_same_leaf(parent, 'sl', 'pb', 'slb')}) AND ({_actual_origin()})
        AND so.status='context_refused' AND ({v97._context_valid('sob.transport_json', 'sa.request_sha256')})
        AND parent_job.state='succeeded' AND parent_job.cancel_requested=0 AND parent_job.job_type='{BATCH}'
        AND ({v97._job_bound('parent_job', request)})
        AND coalesce(({_exact_object('parent_job.result_json', REFERENCE_RESULT_FIELDS)}),0)
        AND json_extract(parent_job.result_json,'$.schema_version')=2
        AND json_extract(parent_job.result_json,'$.request_id')={request}.id
        AND json_extract(parent_job.result_json,'$.leaf_id')={parent}.id
        AND json_extract(parent_job.result_json,'$.source_attempt_id')=partition.refused_attempt_id
        AND json_extract(parent_job.result_json,'$.status')='inherited_partition'
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt own_attempt WHERE own_attempt.leaf_id={parent}.id))"""


def _original_trigger(name):
    prefix = f"CREATE TRIGGER {name} "
    matches = [sql for sql in v97.V097_STATEMENTS if sql.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"v98 needs one immutable v97 trigger: {name}")
    sql = matches[0]
    if sql.count("\n    WHEN ") != 1 or sql.count("\n    BEGIN SELECT RAISE(ABORT,") != 1:
        raise ValueError(f"unexpected immutable v97 trigger form: {name}")
    return sql


def _replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("immutable v97 SQL successor target is missing or ambiguous")
    return value.replace(old, new, 1)


def _split_trigger(sql):
    prefix, rest = sql.split("\n    WHEN ", 1)
    bad, ending = rest.split("\n    BEGIN SELECT RAISE(ABORT,", 1)
    return prefix, bad, "\n    BEGIN SELECT RAISE(ABORT," + ending


def _successor_trigger(name, modified, marker):
    old = _original_trigger(name)
    prefix, old_bad, ending = _split_trigger(old)
    new_prefix, new_bad, new_ending = _split_trigger(modified)
    if prefix != new_prefix or ending != new_ending:
        raise ValueError("v98 successor may change only the guarded predicate")
    return (
        f"DROP TRIGGER {name}",
        prefix + f"\n    WHEN ((NOT ({marker})) AND ({old_bad}))\n"
        + f"      OR (({marker}) AND ({new_bad}))" + ending,
    )


def _leaf_marker(expression):
    return f"EXISTS(SELECT 1 FROM pdf_continuation_leaf marker_leaf JOIN pdf_continuation_retained_request marker ON marker.request_id=marker_leaf.request_id WHERE marker_leaf.id={expression})"


# Children are inserted before the immutable inherited-partition descriptor.
# This narrow planning-only proof therefore finds the *actual* historical split
# and a matching child. The final partition trigger binds every child by index,
# authenticates both payloads, and checks the complete union before root success.
_INHERITED_CHILD = f"""(NEW.parent_leaf_id IS NOT NULL AND NEW.kind='write' AND EXISTS(
  SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_leaf_body pb ON pb.leaf_id=parent.id
  JOIN pdf_continuation_leaf sl ON sl.kind='write'
  JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
  JOIN pdf_continuation_request sq ON sq.id=sl.request_id
  JOIN pdf_continuation_attempt sa ON sa.leaf_id=sl.id
  JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
  JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
  JOIN durable_job sj ON sj.id=sa.job_id
  JOIN pdf_continuation_split source_split ON source_split.parent_leaf_id=sl.id AND source_split.refused_attempt_id=sa.id
  JOIN json_each(source_split.children_json) source_member
  JOIN pdf_continuation_leaf source_child ON source_child.id=source_member.value
  JOIN pdf_continuation_leaf_body source_child_body ON source_child_body.leaf_id=source_child.id
  WHERE parent.id=NEW.parent_leaf_id AND parent.request_id=q.id AND parent.parent_ordinal=NEW.parent_ordinal
    AND parent.kind='write' AND parent.depth+1=NEW.depth AND ({_planning()})
    AND ({_unexecuted('parent')}) AND ({_same_requests()})
    AND ({_same_leaf('parent', 'sl', 'pb', 'slb')}) AND ({_actual_origin()})
    AND so.status='context_refused' AND ({v97._context_valid('sob.transport_json', 'sa.request_sha256')})
    AND source_child.request_id=sq.id AND source_child.parent_leaf_id=sl.id AND source_child.kind='write'
    AND source_child.parent_ordinal=NEW.parent_ordinal
    AND NEW.created_at>=q.created_at AND NEW.created_at>=so.completed_at
    AND NEW.request_sha256=source_child.request_sha256 AND NEW.input_size_bytes=source_child.input_size_bytes
    AND NEW.coverage_sha256=source_child.coverage_sha256 AND NEW.coverage_json=source_child.coverage_json
    AND ({v97._subset('NEW.coverage_json', 'parent.coverage_json')})
    AND json(NEW.coverage_json)<>json(parent.coverage_json)))"""


V098_STATEMENTS = (
    f"""CREATE TABLE pdf_continuation_retained_request(
      request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_request(id) ON DELETE RESTRICT,
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_result_reference(
      leaf_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      source_attempt_id TEXT NOT NULL REFERENCES pdf_continuation_outcome(attempt_id) ON DELETE RESTRICT,
      request_sha256 TEXT NOT NULL CHECK({_sha('request_sha256')}),
      response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
      model_sha256 TEXT NOT NULL CHECK({_sha('model_sha256')}),
      transport_sha256 TEXT NOT NULL CHECK({_sha('transport_sha256')}),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TABLE pdf_continuation_inherited_partition(
      parent_leaf_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_continuation_leaf(id) ON DELETE RESTRICT,
      refused_attempt_id TEXT NOT NULL REFERENCES pdf_continuation_outcome(attempt_id) ON DELETE RESTRICT,
      children_json TEXT NOT NULL CHECK(json_valid(children_json) AND json_type(children_json)='array'
        AND json_array_length(children_json) BETWEEN 2 AND 16),
      children_sha256 TEXT NOT NULL CHECK({_sha('children_sha256')}),
      created_at ANY NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0)
    ) STRICT""",
    f"""CREATE TRIGGER pdf_continuation_retained_request_insert BEFORE INSERT ON pdf_continuation_retained_request
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_request q JOIN durable_job root ON root.id=q.root_job_id
      JOIN schema_migration applied ON applied.version=98
      WHERE q.id=NEW.request_id AND NEW.created_at=q.created_at AND q.created_at>=applied.applied_at
        AND root.created_at>=applied.applied_at AND ({v97._pending()})
        AND root.state='queued' AND root.cancel_requested=0 AND root.job_type='{PLAN}'
        AND ({v97._job_bound('root', 'q', root=True)})
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_plan_body WHERE request_id=q.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf WHERE request_id=q.id))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_RETAINED_REQUEST_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_result_reference_insert BEFORE INSERT ON pdf_continuation_result_reference
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
      JOIN pdf_continuation_request q ON q.id=l.request_id JOIN durable_job j ON j.id=l.job_id
      JOIN pdf_continuation_attempt sa ON sa.id=NEW.source_attempt_id
      JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
      JOIN pdf_continuation_request sq ON sq.id=sl.request_id JOIN durable_job sj ON sj.id=sa.job_id
      WHERE l.id=NEW.leaf_id AND ({_planning()}) AND ({_unexecuted()})
        AND j.state='queued' AND j.cancel_requested=0 AND j.job_type='{BATCH}' AND ({v97._job_bound()})
        AND ({_same_requests()}) AND ({_same_leaf()}) AND ({_actual_origin()}) AND so.status='validated'
        AND NEW.created_at>=l.created_at AND NEW.created_at>=so.completed_at
        AND NEW.request_sha256=l.request_sha256 AND NEW.request_sha256=sa.request_sha256
        AND NEW.response_sha256=so.response_sha256 AND NEW.model_sha256=so.model_sha256
        AND NEW.transport_sha256=so.transport_sha256)
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_RESULT_REFERENCE_INVALID'); END""",
    f"""CREATE TRIGGER pdf_continuation_inherited_partition_insert BEFORE INSERT ON pdf_continuation_inherited_partition
    WHEN NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_leaf_body pb ON pb.leaf_id=parent.id
      JOIN pdf_continuation_request q ON q.id=parent.request_id JOIN durable_job j ON j.id=parent.job_id
      JOIN pdf_continuation_attempt sa ON sa.id=NEW.refused_attempt_id
      JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
      JOIN pdf_continuation_request sq ON sq.id=sl.request_id JOIN durable_job sj ON sj.id=sa.job_id
      JOIN pdf_continuation_split source_split ON source_split.parent_leaf_id=sl.id AND source_split.refused_attempt_id=sa.id
      WHERE parent.id=NEW.parent_leaf_id AND ({_planning()}) AND ({_unexecuted('parent')})
        AND j.state='queued' AND j.cancel_requested=0 AND j.job_type='{BATCH}' AND ({v97._job_bound()})
        AND ({_same_requests()}) AND ({_same_leaf('parent', 'sl', 'pb', 'slb')}) AND ({_actual_origin()})
        AND so.status='context_refused' AND ({v97._context_valid('sob.transport_json', 'sa.request_sha256')})
        AND NEW.created_at>=parent.created_at AND NEW.created_at>=so.completed_at
        AND json_array_length(NEW.children_json)=json_array_length(source_split.children_json)
        AND (SELECT count(*) FROM json_each(NEW.children_json))=(SELECT count(DISTINCT value) FROM json_each(NEW.children_json))
        AND json_array_length(NEW.children_json)=(SELECT count(*) FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id)
        AND NOT EXISTS(SELECT 1 FROM json_each(NEW.children_json) member WHERE member.type<>'text' OR NOT EXISTS(
          SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
          JOIN durable_job child_job ON child_job.id=l.job_id
          JOIN pdf_continuation_leaf source_child ON source_child.id=json_extract(source_split.children_json,'$['||member.key||']')
          JOIN pdf_continuation_leaf_body source_child_body ON source_child_body.leaf_id=source_child.id
          WHERE l.id=member.value AND l.request_id=q.id AND l.parent_leaf_id=parent.id
            AND l.parent_ordinal=parent.parent_ordinal AND l.depth=parent.depth+1
            AND source_child.request_id=sq.id AND source_child.parent_leaf_id=sl.id
            AND ({_same_leaf('l', 'source_child', 'lb', 'source_child_body')})
            AND child_job.state='queued' AND child_job.cancel_requested=0 AND child_job.job_type='{BATCH}'
            AND ({v97._job_bound('child_job', 'q')})
            AND ({v97._subset('l.coverage_json', 'parent.coverage_json')})
            AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id)))
        AND ({v97._union_complete('parent.coverage_json', 'SELECT coverage_json FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id')})
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l,json_each(l.coverage_json,'$.ranges') c,
          json_each(parent.coverage_json,'$.ranges') p WHERE l.parent_leaf_id=parent.id
            AND json_extract(c.value,'$.chunk_id')=json_extract(p.value,'$.chunk_id')
            AND (json_extract(c.value,'$.start')<>json_extract(p.value,'$.start') OR json_extract(c.value,'$.end')<>json_extract(p.value,'$.end'))
            AND json_extract(c.value,'$.end')-json_extract(c.value,'$.start')<json_extract(q.policy_json,'$.min_codepoints')))
    BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_INHERITED_PARTITION_INVALID'); END""",
)

# Narrow v97 child-predicate successor. All root coverage, work limits, queued
# child-job binding and sibling disjointness clauses remain exactly inherited.
_leaf_old = _original_trigger("pdf_continuation_leaf_insert")
_child_old = f"""(NEW.parent_leaf_id IS NOT NULL AND NEW.kind='write' AND EXISTS(
              SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_attempt a ON a.leaf_id=parent.id
              JOIN pdf_continuation_outcome o ON o.attempt_id=a.id JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id
              WHERE parent.id=NEW.parent_leaf_id AND parent.request_id=q.id AND parent.parent_ordinal=NEW.parent_ordinal
                AND parent.kind='write' AND parent.depth+1=NEW.depth AND NEW.created_at>=o.completed_at
                AND o.status='context_refused' AND ({v97._context_valid('ob.transport_json','a.request_sha256')})
                AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split WHERE parent_leaf_id=parent.id)
                AND ({v97._subset('NEW.coverage_json','parent.coverage_json')}) AND json(NEW.coverage_json)<>json(parent.coverage_json)))"""
_leaf_new = _replace_once(_leaf_old, _child_old, f"({_child_old} OR {_INHERITED_CHILD})")
V098_STATEMENTS += _successor_trigger("pdf_continuation_leaf_insert", _leaf_new,
    "EXISTS(SELECT 1 FROM pdf_continuation_retained_request marker WHERE marker.request_id=NEW.request_id)")

for _trigger_name in ("pdf_continuation_attempt_insert", "pdf_continuation_outcome_insert", "pdf_continuation_split_insert"):
    _old = _original_trigger(_trigger_name)
    _alias = "parent" if _trigger_name == "pdf_continuation_split_insert" else "l"
    _new = _replace_once(_old, f"({v97._active(_alias)})", f"({_active(_alias)})")
    if _trigger_name == "pdf_continuation_attempt_insert":
        _new = _replace_once(_new, "      OR NEW.sequence<>",
            "      OR EXISTS(SELECT 1 FROM pdf_continuation_result_reference WHERE leaf_id=NEW.leaf_id)\n"
            "      OR EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition WHERE parent_leaf_id=NEW.leaf_id)\n"
            "      OR NEW.sequence<>")
    _expression = "NEW.parent_leaf_id" if _alias == "parent" else (
        "NEW.leaf_id" if _trigger_name == "pdf_continuation_attempt_insert" else
        "(SELECT leaf_id FROM pdf_continuation_attempt WHERE id=NEW.attempt_id)")
    V098_STATEMENTS += _successor_trigger(_trigger_name, _new, _leaf_marker(_expression))

_OWN_READY = """(l.kind='write' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
            JOIN pdf_continuation_outcome_body ob ON ob.attempt_id=o.attempt_id JOIN durable_job child ON child.id=a.job_id
            WHERE a.leaf_id=l.id AND o.status='validated' AND child.state='succeeded' AND child.cancel_requested=0))"""
for _trigger_name in ("pdf_continuation_completion_insert", "pdf_continuation_snapshot_insert"):
    _old = _original_trigger(_trigger_name)
    _new = _replace_once(_old, f"({v97._active()})", f"({_active()})")
    _new = _replace_once(_new, _OWN_READY, f"({_OWN_READY} OR (l.kind='write' AND ({_reference_ready()})))")
    _new = _replace_once(_new, "        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l",
        f"        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition partition JOIN pdf_continuation_leaf parent ON parent.id=partition.parent_leaf_id\n"
        f"          WHERE parent.request_id=q.id AND NOT ({_partition_ready()}))\n"
        "        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l")
    V098_STATEMENTS += _successor_trigger(_trigger_name, _new,
        "EXISTS(SELECT 1 FROM pdf_continuation_retained_request marker WHERE marker.request_id=NEW.request_id)")

_body_old = _original_trigger("pdf_continuation_snapshot_body_insert")
_body_new = _replace_once(_body_old, "json_extract(NEW.manifest_json,'$.schema_version')=1",
    "json_extract(NEW.manifest_json,'$.schema_version')=2")
_body_new = _body_new.replace(f"({v97._active()})", f"({_active()})")
if _body_old.count(f"({v97._active()})") != 2:
    raise ValueError("unexpected immutable v97 snapshot active clauses")
_body_new = _replace_once(_body_new, _exact_object('receipt.value', v97.LEAF_RECEIPT_FIELDS),
    _exact_object('receipt.value', LEAF_RECEIPT_FIELDS))
_body_new = _replace_once(_body_new, "SELECT 1 FROM pdf_continuation_reuse reuse WHERE reuse.leaf_id=l.id",
    "SELECT 1 FROM pdf_continuation_reuse reuse WHERE reuse.leaf_id=l.id\n"
    "                AND json_extract(receipt.value,'$.execution_source')='original_reuse'\n"
    "                AND json_extract(receipt.value,'$.origin_request_id')=q.parent_run_id\n"
    "                AND json_extract(receipt.value,'$.origin_leaf_id')=(SELECT batch_id FROM pdf_visual_authoring_batch WHERE run_id=q.parent_run_id AND ordinal=l.parent_ordinal)")
_write_receipt_old = """(l.kind='write' AND EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
                  WHERE a.leaf_id=l.id AND o.status='validated' AND json_extract(receipt.value,'$.source_attempt_id')=a.id
                    AND json_extract(receipt.value,'$.response_sha256')=o.response_sha256
                    AND json_extract(receipt.value,'$.model_sha256')=o.model_sha256
                    AND json_extract(receipt.value,'$.transport_sha256')=o.transport_sha256))"""
_write_receipt_new = _replace_once(_write_receipt_old, "WHERE a.leaf_id=l.id AND o.status='validated'",
    "WHERE a.leaf_id=l.id AND o.status='validated'\n"
    "                    AND json_extract(receipt.value,'$.execution_source')='current_write'\n"
    "                    AND json_extract(receipt.value,'$.origin_request_id')=q.id\n"
    "                    AND json_extract(receipt.value,'$.origin_leaf_id')=l.id")
_retained_receipt = f"""(l.kind='write' AND ({_reference_ready()}) AND EXISTS(
                  SELECT 1 FROM pdf_continuation_result_reference ref JOIN pdf_continuation_attempt sa ON sa.id=ref.source_attempt_id
                  JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id WHERE ref.leaf_id=l.id
                    AND json_extract(receipt.value,'$.execution_source')='retained_write'
                    AND json_extract(receipt.value,'$.origin_request_id')=sl.request_id
                    AND json_extract(receipt.value,'$.origin_leaf_id')=sl.id
                    AND json_extract(receipt.value,'$.source_attempt_id')=sa.id
                    AND json_extract(receipt.value,'$.response_sha256')=ref.response_sha256
                    AND json_extract(receipt.value,'$.model_sha256')=ref.model_sha256
                    AND json_extract(receipt.value,'$.transport_sha256')=ref.transport_sha256))"""
_body_new = _replace_once(_body_new, _write_receipt_old, f"({_write_receipt_new} OR {_retained_receipt})")
V098_STATEMENTS += _successor_trigger("pdf_continuation_snapshot_body_insert", _body_new,
    "EXISTS(SELECT 1 FROM pdf_continuation_snapshot marker_snapshot JOIN pdf_continuation_retained_request marker ON marker.request_id=marker_snapshot.request_id WHERE marker_snapshot.id=NEW.snapshot_id)")

# Job-success trigger statements have a distinct v97 indentation. Assert their
# exact templates instead of accepting an arbitrary string rewrite.
_ORPHAN_FREE = """NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l WHERE l.request_id=q.id AND l.parent_leaf_id IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split sp WHERE sp.parent_leaf_id=l.parent_leaf_id
            AND EXISTS(SELECT 1 FROM json_each(sp.children_json) member WHERE member.value=l.id))
          AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip WHERE ip.parent_leaf_id=l.parent_leaf_id
            AND EXISTS(SELECT 1 FROM json_each(ip.children_json) member WHERE member.value=l.id)))"""
_plan_query = _replace_once(v97._SUCCESS[PLAN], "AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l",
    f"AND ({_ORPHAN_FREE})\n        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_leaf l")
_NO_PROVIDER_SUCCESS = f"""EXISTS(SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_request q ON q.id=l.request_id
      JOIN durable_job root ON root.id=q.root_job_id
      WHERE l.job_id=NEW.id AND l.kind='write' AND ({_marked()}) AND ({v97._pending()})
        AND root.state='succeeded' AND root.cancel_requested=0 AND ({v97._job_bound('root', 'q', root=True)})
        AND OLD.state='running' AND OLD.cancel_requested=0 AND OLD.claim_owner IS NOT NULL
        AND OLD.claim_expires_at>{_NOW} AND NEW.cancel_requested=0 AND NEW.attempt_count=OLD.attempt_count
        AND ({v97._job_bound('NEW', 'q')})
        AND json_extract(NEW.result_json,'$.request_id')=q.id AND json_extract(NEW.result_json,'$.leaf_id')=l.id
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id)
        AND ((json_extract(NEW.result_json,'$.status')='referenced_result' AND ({_active()})
          AND EXISTS(SELECT 1 FROM pdf_continuation_result_reference ref
            JOIN pdf_continuation_attempt sa ON sa.id=ref.source_attempt_id
            JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
            JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
            JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
            JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
            JOIN pdf_continuation_request sq ON sq.id=sl.request_id JOIN durable_job sj ON sj.id=sa.job_id
            WHERE ref.leaf_id=l.id AND so.status='validated' AND ({_same_requests()}) AND ({_same_leaf()}) AND ({_actual_origin()})
              AND ref.request_sha256=l.request_sha256 AND ref.request_sha256=sa.request_sha256
              AND ref.response_sha256=so.response_sha256 AND ref.model_sha256=so.model_sha256 AND ref.transport_sha256=so.transport_sha256
              AND json_extract(NEW.result_json,'$.source_attempt_id')=ref.source_attempt_id))
          OR (json_extract(NEW.result_json,'$.status')='inherited_partition'
            AND EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip
              JOIN pdf_continuation_attempt sa ON sa.id=ip.refused_attempt_id
              JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
              JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=so.attempt_id
              JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
              JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
              JOIN pdf_continuation_request sq ON sq.id=sl.request_id JOIN durable_job sj ON sj.id=sa.job_id
              JOIN pdf_continuation_split source_split ON source_split.parent_leaf_id=sl.id AND source_split.refused_attempt_id=sa.id
              WHERE ip.parent_leaf_id=l.id AND so.status='context_refused' AND ({_same_requests()}) AND ({_same_leaf()}) AND ({_actual_origin()})
                AND ({v97._context_valid('sob.transport_json', 'sa.request_sha256')})
                AND json_extract(NEW.result_json,'$.source_attempt_id')=ip.refused_attempt_id))))"""

for _index, _kind in enumerate((PLAN, BATCH)):
    _name = f"pdf_continuation_job_success_{_index}"
    _old = f"""CREATE TRIGGER {_name} BEFORE UPDATE OF state ON durable_job
  WHEN NEW.job_type='{_kind}' AND NEW.handler_version='{HANDLER_VERSION}' AND NEW.state='succeeded'
    AND (NOT coalesce(({_exact_object('NEW.result_json', v97._RESULT_FIELDS[_kind])}),0)
      OR json_extract(NEW.result_json,'$.schema_version')<>1 OR NOT ({v97._SUCCESS[_kind]}))
  BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_JOB_SUCCESS_INVALID'); END"""
    if sum(sql == _old for sql in v97.V097_STATEMENTS) != 1:
        raise ValueError("unexpected immutable v97 job-success template")
    if _kind == PLAN:
        _valid = f"coalesce(({_exact_object('NEW.result_json', v97._RESULT_FIELDS[_kind])}),0) AND json_extract(NEW.result_json,'$.schema_version')=1 AND ({_plan_query})"
    else:
        _valid = f"""(coalesce(({_exact_object('NEW.result_json', v97._RESULT_FIELDS[_kind])}),0)
          AND json_extract(NEW.result_json,'$.schema_version')=1 AND ({v97._SUCCESS[_kind]}))
        OR (coalesce(({_exact_object('NEW.result_json', REFERENCE_RESULT_FIELDS)}),0)
          AND json_extract(NEW.result_json,'$.schema_version')=2 AND ({_NO_PROVIDER_SUCCESS}))"""
    _marker = "EXISTS(SELECT 1 FROM pdf_continuation_retained_request marker JOIN pdf_continuation_request marker_q ON marker_q.id=marker.request_id WHERE marker_q.root_job_id=NEW.id)" if _kind == PLAN else "EXISTS(SELECT 1 FROM pdf_continuation_leaf marker_leaf JOIN pdf_continuation_retained_request marker ON marker.request_id=marker_leaf.request_id WHERE marker_leaf.job_id=NEW.id)"
    _old_bad = _old.split("\n    AND (", 1)[1].split("\n  BEGIN", 1)[0]
    if not _old_bad.endswith(")"):
        raise ValueError("unexpected immutable v97 job predicate")
    _old_bad = _old_bad[:-1]
    V098_STATEMENTS += (
        f"DROP TRIGGER {_name}",
        f"""CREATE TRIGGER {_name} BEFORE UPDATE OF state ON durable_job
  WHEN NEW.job_type='{_kind}' AND NEW.handler_version='{HANDLER_VERSION}' AND NEW.state='succeeded'
    AND (((NOT ({_marker})) AND ({_old_bad})) OR (({_marker}) AND NOT coalesce(({_valid}),0)))
  BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_JOB_SUCCESS_INVALID'); END""",
    )

_IMMUTABLE = ("pdf_continuation_retained_request", "pdf_continuation_result_reference", "pdf_continuation_inherited_partition")
V098_STATEMENTS += tuple(
    f"CREATE TRIGGER {table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'PDF_CONTINUATION_RETAINED_IMMUTABLE'); END"
    for table in _IMMUTABLE for verb in ("UPDATE", "DELETE")
)

V098_POSTCONDITION_QUERIES = (
    """SELECT 'retained continuation admission predates schema98' FROM pdf_continuation_retained_request marker
      JOIN pdf_continuation_request q ON q.id=marker.request_id JOIN durable_job root ON root.id=q.root_job_id
      LEFT JOIN schema_migration applied ON applied.version=98
      WHERE applied.version IS NULL OR marker.created_at<>q.created_at OR q.created_at<applied.applied_at
        OR root.created_at<applied.applied_at LIMIT 1""",
    f"""SELECT 'retained continuation direct result lineage mismatch' FROM pdf_continuation_result_reference ref
      JOIN pdf_continuation_leaf l ON l.id=ref.leaf_id JOIN pdf_continuation_request q ON q.id=l.request_id
      JOIN pdf_continuation_attempt sa ON sa.id=ref.source_attempt_id JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_request sq ON sq.id=sl.request_id
      JOIN durable_job sj ON sj.id=sa.job_id
      WHERE NOT coalesce((({_marked()}) AND ({_same_requests()}) AND ({_actual_origin()}) AND so.status='validated'
        AND l.kind='write' AND l.parent_ordinal=sl.parent_ordinal AND l.coverage_json=sl.coverage_json
        AND l.coverage_sha256=sl.coverage_sha256 AND l.request_sha256=sl.request_sha256 AND l.input_size_bytes=sl.input_size_bytes
        AND ref.request_sha256=l.request_sha256 AND ref.request_sha256=sa.request_sha256
        AND ref.response_sha256=so.response_sha256 AND ref.model_sha256=so.model_sha256 AND ref.transport_sha256=so.transport_sha256
        AND ref.created_at>=l.created_at AND ref.created_at>=so.completed_at
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split WHERE parent_leaf_id=l.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition WHERE parent_leaf_id=l.id)),0) LIMIT 1""",
    f"""SELECT 'retained continuation inherited partition lineage mismatch' FROM pdf_continuation_inherited_partition ip
      JOIN pdf_continuation_leaf parent ON parent.id=ip.parent_leaf_id JOIN pdf_continuation_request q ON q.id=parent.request_id
      JOIN pdf_continuation_attempt sa ON sa.id=ip.refused_attempt_id JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
      JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id JOIN pdf_continuation_request sq ON sq.id=sl.request_id
      JOIN durable_job sj ON sj.id=sa.job_id
      LEFT JOIN pdf_continuation_split source_split ON source_split.parent_leaf_id=sl.id AND source_split.refused_attempt_id=sa.id
      WHERE NOT coalesce((({_marked()}) AND ({_same_requests()}) AND ({_actual_origin()}) AND so.status='context_refused'
        AND parent.kind='write' AND parent.parent_ordinal=sl.parent_ordinal AND parent.coverage_json=sl.coverage_json
        AND parent.coverage_sha256=sl.coverage_sha256 AND parent.request_sha256=sl.request_sha256 AND parent.input_size_bytes=sl.input_size_bytes
        AND ip.created_at>=parent.created_at AND ip.created_at>=so.completed_at AND source_split.parent_leaf_id IS NOT NULL
        AND json_array_length(ip.children_json)=json_array_length(source_split.children_json)
        AND json_array_length(ip.children_json)=(SELECT count(*) FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=parent.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_result_reference WHERE leaf_id=parent.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_split WHERE parent_leaf_id=parent.id)
        AND NOT EXISTS(SELECT 1 FROM json_each(ip.children_json) member WHERE member.type<>'text' OR NOT EXISTS(
          SELECT 1 FROM pdf_continuation_leaf child JOIN pdf_continuation_leaf source_child
            ON source_child.id=json_extract(source_split.children_json,'$['||member.key||']')
          WHERE child.id=member.value AND child.request_id=q.id AND child.parent_leaf_id=parent.id AND child.kind='write'
            AND child.parent_ordinal=parent.parent_ordinal AND child.depth=parent.depth+1
            AND source_child.request_id=sq.id AND source_child.parent_leaf_id=sl.id AND source_child.kind='write'
            AND child.parent_ordinal=source_child.parent_ordinal AND child.coverage_json=source_child.coverage_json
            AND child.coverage_sha256=source_child.coverage_sha256 AND child.request_sha256=source_child.request_sha256
            AND child.input_size_bytes=source_child.input_size_bytes))
        AND ({v97._union_complete('parent.coverage_json', 'SELECT coverage_json FROM pdf_continuation_leaf WHERE parent_leaf_id=parent.id')})),0) LIMIT 1""",
    """SELECT 'retained continuation inherited sibling overlap' FROM pdf_continuation_inherited_partition ip
      JOIN pdf_continuation_leaf a ON a.parent_leaf_id=ip.parent_leaf_id
      JOIN pdf_continuation_leaf b ON b.parent_leaf_id=ip.parent_leaf_id AND a.id<b.id
      WHERE EXISTS(SELECT 1 FROM json_each(a.coverage_json,'$.ranges') x,json_each(b.coverage_json,'$.ranges') y
        WHERE json_extract(x.value,'$.chunk_id')=json_extract(y.value,'$.chunk_id')
          AND json_extract(x.value,'$.start')<json_extract(y.value,'$.end') AND json_extract(y.value,'$.start')<json_extract(x.value,'$.end'))
        OR EXISTS(SELECT 1 FROM json_each(a.coverage_json,'$.tables') x,json_each(b.coverage_json,'$.tables') y
          WHERE json_extract(x.value,'$.cell_key')=json_extract(y.value,'$.cell_key'))
        OR EXISTS(SELECT 1 FROM json_each(a.coverage_json,'$.visuals') x,json_each(b.coverage_json,'$.visuals') y
          WHERE json_extract(x.value,'$.image_id')=json_extract(y.value,'$.image_id')) LIMIT 1""",
    f"""SELECT 'retained continuation committed plan has orphan child' FROM pdf_continuation_request q
      JOIN pdf_continuation_retained_request marker ON marker.request_id=q.id
      JOIN durable_job root ON root.id=q.root_job_id WHERE root.state='succeeded' AND NOT ({_ORPHAN_FREE}) LIMIT 1""",
    """SELECT 'retained continuation body mismatch while source retained' FROM pdf_continuation_result_reference ref
      JOIN pdf_continuation_leaf l ON l.id=ref.leaf_id JOIN pdf_continuation_request q ON q.id=l.request_id
      JOIN pdf_visual_authoring_run original ON original.id=q.parent_run_id
      JOIN extraction_snapshot_body retained ON retained.snapshot_id=original.extraction_snapshot_id
      JOIN pdf_continuation_attempt sa ON sa.id=ref.source_attempt_id
      LEFT JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
      LEFT JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sa.leaf_id
      LEFT JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=sa.id
      WHERE lb.leaf_id IS NULL OR slb.leaf_id IS NULL OR sob.attempt_id IS NULL OR lb.payload_json<>slb.payload_json LIMIT 1""",
    """SELECT 'retained continuation partition body mismatch while source retained' FROM pdf_continuation_inherited_partition ip
      JOIN pdf_continuation_leaf parent ON parent.id=ip.parent_leaf_id JOIN pdf_continuation_request q ON q.id=parent.request_id
      JOIN pdf_visual_authoring_run original ON original.id=q.parent_run_id
      JOIN extraction_snapshot_body retained ON retained.snapshot_id=original.extraction_snapshot_id
      JOIN pdf_continuation_attempt sa ON sa.id=ip.refused_attempt_id
      LEFT JOIN pdf_continuation_leaf_body pb ON pb.leaf_id=parent.id
      LEFT JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sa.leaf_id
      LEFT JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=sa.id
      LEFT JOIN pdf_continuation_split source_split ON source_split.refused_attempt_id=sa.id
      WHERE pb.leaf_id IS NULL OR slb.leaf_id IS NULL OR sob.attempt_id IS NULL OR pb.payload_json<>slb.payload_json
        OR EXISTS(SELECT 1 FROM json_each(ip.children_json) member
          LEFT JOIN pdf_continuation_leaf_body cb ON cb.leaf_id=member.value
          LEFT JOIN pdf_continuation_leaf_body scb ON scb.leaf_id=json_extract(source_split.children_json,'$['||member.key||']')
          WHERE cb.leaf_id IS NULL OR scb.leaf_id IS NULL OR cb.payload_json<>scb.payload_json) LIMIT 1""",
)

__all__ = ["V098_STATEMENTS", "V098_POSTCONDITION_QUERIES", "MANIFEST_FIELDS", "LEAF_RECEIPT_FIELDS",
    "REFERENCE_RESULT_FIELDS", "_active", "_reference_ready", "_partition_ready"]
