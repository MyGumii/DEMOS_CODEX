"""V98 direct actual-result replay for a new finite, source-bound request.

Candidate only. Existing V97 reuse/split/attempt/outcome rows are never edited.
The caller holds the normal preparation read guard and source authority. These
functions create no provider calls and cannot admit or publish a new result.
"""
from collections import defaultdict
import json

from ..db import db_now_ms
from ..pdf_visual_continuation import (
    require, _hash, _json, checked_leaf, _prepare_inventory_and_leaves,
    validate_output_receipt, validate_refusal_receipt, make_leaf, exact_union,
)
from ..pdf_continuation_retained_plan import (
    RetainedSplitChild, RetainedWriteEvidence,
)
from ..wiki_authoring import AuthoringWorkLimitError
from ..migrations.v097_pdf_context_resume import _source_current
from ..migrations.v098_pdf_terminal_result_reuse import (
    _actual_origin, _same_requests, _same_leaf, _active,
    _reference_ready, _partition_ready,
)
from .artifacts import _connection
from .pdf_authoring import _insert

_SOURCE_EQUAL_FIELDS = (
    "parent_run_id", "parent_preview_id", "actor_user_id", "space_id",
    "source_archive_id", "source_fence_sha256", "parent_plan_sha256",
    "captured_scope_epoch", "authorization_snapshot_json",
    "authorization_snapshot_sha256", "policy_json", "policy_sha256",
)

def is_retained_request(connection, request_id):
    _connection(connection, write=True)
    if connection.execute("SELECT 1 FROM schema_migration WHERE version=98").fetchone() is None:
        return False
    return connection.execute(
        "SELECT 1 FROM pdf_continuation_retained_request WHERE request_id=?",
        (request_id,)).fetchone() is not None

def current_retained_request(repo, connection, request_id, run=None):
    q, policy = repo.bound(connection, request_id, run, pending=True)
    require(is_retained_request(connection, request_id),
        "Terminal-result reuse requires a newly marked request")
    require(connection.execute("SELECT 1 FROM pdf_continuation_request q WHERE q.id=? AND (" +
        _source_current("q") + ")", (request_id,)).fetchone() is not None,
        "Current retained source or authorization is unavailable")
    return q, policy

def mark_retained_request(repo, connection, request_id):
    q, _ = repo.bound(connection, request_id, pending=True)
    require(connection.execute("SELECT 1 FROM schema_migration WHERE version=98").fetchone() is not None)
    _insert(connection, "pdf_continuation_retained_request", ("request_id", "created_at"),
        (q["id"], q["created_at"]))

def _body(row):
    for body_name, hash_name, size_name in (
        ("response", "response_sha256", "response_size_bytes"),
        ("model_json", "model_sha256", "model_size_bytes"),
        ("transport_json", "transport_sha256", "transport_size_bytes"),
    ):
        value = row[body_name]
        require(type(value) is str and _hash(value) == row[hash_name]
            and len(value.encode("utf-8")) == row[size_name],
            "Retained actual output body differs from its sealed receipt")
    require(_hash(row["payload_json"]) == row["request_sha256"]
        and _hash(row["coverage_json"]) == row["coverage_sha256"],
        "Retained actual input body differs from its sealed receipt")

def _selection(row):
    value = json.loads(row["payload_json"])
    require(type(value) is dict and type(value.get("exact_parent")) is bool,
        "Retained actual input is not a canonical leaf")
    return row["parent_ordinal"], json.loads(row["coverage_json"]), value["exact_parent"]

def _checked_leaf_body(row, leaf):
    checked_leaf(leaf)
    require(leaf.payload_json == row["payload_json"]
        and leaf.coverage_json == row["coverage_json"]
        and leaf.parent_ordinal == row["parent_ordinal"]
        and leaf.request_sha256 == row["request_sha256"]
        and leaf.input_size_bytes == row["input_size_bytes"],
        "Retained actual request replay differs")

def load_direct_evidence(repo, connection, request_id, run, *, cancel_event=None):
    """Restore only direct actual outcomes, in one pure leaf-materialization pass.

    The SQL selects the newest validated and newest actual refusal for each exact
    logical input, avoiding unbounded duplicate history in Python. Partition
    children are all retained, including children with no completed outcome.
    No historical deadline is used as current authority.
    """
    q, policy = current_retained_request(repo, connection, request_id, run)
    equal = " AND ".join("source." + field + "=target." + field for field in _SOURCE_EQUAL_FIELDS)
    query = """WITH eligible AS (
      SELECT a.id AS source_attempt_id,l.id AS source_leaf_id,l.request_id AS source_request_id,
        l.parent_ordinal,l.coverage_json,l.coverage_sha256,l.request_sha256,l.input_size_bytes,
        o.status,o.completed_at,o.response_sha256,o.response_size_bytes,
        o.model_sha256,o.model_size_bytes,o.transport_sha256,o.transport_size_bytes,
        sp.children_json,sp.children_sha256,sp.refused_attempt_id
      FROM pdf_continuation_request target
      JOIN pdf_continuation_request source ON """ + equal + """
      JOIN pdf_continuation_state state ON state.request_id=source.id
      JOIN pdf_continuation_leaf l ON l.request_id=source.id
      JOIN pdf_continuation_attempt a ON a.leaf_id=l.id AND a.job_id=l.job_id
      JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
      JOIN durable_job job ON job.id=a.job_id
      LEFT JOIN pdf_continuation_split sp ON sp.parent_leaf_id=l.id AND sp.refused_attempt_id=a.id
      WHERE target.id=? AND source.id<>target.id
        AND source.created_at<target.created_at AND state.status='failed'
        AND state.completed_at<target.created_at AND l.kind='write'
        AND (""" + _actual_origin("l", "a", "o", "job", "source") + """)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_result_reference ref WHERE ref.leaf_id=l.id)
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_inherited_partition ip WHERE ip.parent_leaf_id=l.id)
        AND EXISTS(SELECT 1 FROM pdf_continuation_leaf_body lb WHERE lb.leaf_id=l.id)
        AND EXISTS(SELECT 1 FROM pdf_continuation_outcome_body body WHERE body.attempt_id=a.id)
        AND ((o.status='validated' AND sp.parent_leaf_id IS NULL)
          OR (o.status='context_refused' AND sp.parent_leaf_id IS NOT NULL))
    ), ranked AS (
      SELECT *,row_number() OVER(PARTITION BY request_sha256,status
        ORDER BY completed_at DESC,source_request_id DESC,source_leaf_id DESC,source_attempt_id DESC) AS rank
      FROM eligible
    ), selected AS (
      SELECT * FROM ranked WHERE rank=1 ORDER BY parent_ordinal,request_sha256,status LIMIT ?
    ) SELECT selected.*,lb.payload_json,body.response,body.model_json,body.transport_json
      FROM selected JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=selected.source_leaf_id
      JOIN pdf_continuation_outcome_body body ON body.attempt_id=selected.source_attempt_id
      ORDER BY parent_ordinal,request_sha256,status"""
    def poll():
        require(cancel_event is None or not cancel_event.is_set(), "Retained preparation was cancelled")
    records, output_bytes = [], 0
    poll()
    for raw in connection.execute(query, (request_id, policy.max_leaves * 2 + 1)):
        poll()
        row = dict(raw)
        output_bytes += row["response_size_bytes"] + row["model_size_bytes"] + row["transport_size_bytes"]
        if output_bytes > policy.max_response_bytes:
            raise AuthoringWorkLimitError("Direct retained output replay exceeds its finite byte reservation")
        records.append(row)
    if len(records) > policy.max_leaves * 2:
        raise AuthoringWorkLimitError("Direct retained evidence exceeds the finite leaf reservation")
    by_sha, child_rows = {}, {}
    for row in records:
        poll()
        _body(row)
        key = row["request_sha256"]
        prior = by_sha.setdefault(key, row)
        require(_selection(prior) == _selection(row)
            and prior["payload_json"] == row["payload_json"]
            and prior["input_size_bytes"] == row["input_size_bytes"],
            "One logical input has conflicting retained identity")
        if row["status"] == "context_refused":
            require(type(row["children_json"]) is str
                and _hash(row["children_json"]) == row["children_sha256"]
                and row["refused_attempt_id"] == row["source_attempt_id"],
                "Actual historical partition receipt differs")
            ids = json.loads(row["children_json"])
            require(type(ids) is list and 2 <= len(ids) <= 16
                and len(set(ids)) == len(ids), "Actual historical partition is incomplete")
            children = []
            for child_id in ids:
                poll()
                child = connection.execute("""SELECT l.*,lb.payload_json
                    FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
                    WHERE l.id=? AND l.parent_leaf_id=? AND l.request_id=? AND l.kind='write'""",
                    (child_id, row["source_leaf_id"], row["source_request_id"])).fetchone()
                require(child is not None, "Actual historical partition child is unavailable")
                child = dict(child)
                require(_hash(child["payload_json"]) == child["request_sha256"]
                    and _hash(child["coverage_json"]) == child["coverage_sha256"],
                    "Actual historical child input changed")
                prior = by_sha.setdefault(child["request_sha256"], child)
                if len(by_sha) > policy.max_leaves:
                    raise AuthoringWorkLimitError("Restored retained inputs exceed the finite leaf reservation")
                require(_selection(prior) == _selection(child)
                    and prior["payload_json"] == child["payload_json"]
                    and prior["input_size_bytes"] == child["input_size_bytes"],
                    "Actual historical child has conflicting input identity")
                children.append(child)
            child_rows[row["source_attempt_id"]] = tuple(children)
    if len(by_sha) > policy.max_leaves:
        raise AuthoringWorkLimitError("Restored retained inputs exceed the finite leaf reservation")
    if sum(row["input_size_bytes"] for row in by_sha.values()) > policy.max_input_bytes:
        raise AuthoringWorkLimitError("Restored retained inputs exceed the finite byte reservation")
    if not records:
        return {}
    ordered = tuple(by_sha)
    _, leaves = _prepare_inventory_and_leaves(run.source.plan,
        tuple(_selection(by_sha[key]) for key in ordered), cancel_event=cancel_event)
    materialized = dict(zip(ordered, leaves, strict=True))
    for key, leaf in materialized.items():
        poll()
        _checked_leaf_body(by_sha[key], leaf)
    evidence = defaultdict(list)
    for row in records:
        poll()
        leaf = materialized[row["request_sha256"]]
        children = ()
        split_parent = split_attempt = None
        if row["status"] == "validated":
            validate_output_receipt(run.source.plan, leaf, row["response"],
                row["model_json"], row["transport_json"])
        else:
            validate_refusal_receipt(run.source.plan, leaf, json.loads(row["transport_json"]))
            children = tuple(RetainedSplitChild(materialized[child["request_sha256"]],
                row["source_request_id"], child["id"])
                for child in child_rows[row["source_attempt_id"]])
            split_parent, split_attempt = row["source_leaf_id"], row["source_attempt_id"]
        evidence[row["request_sha256"]].append(RetainedWriteEvidence(
            leaf=leaf, status=row["status"], source_request_id=row["source_request_id"],
            source_leaf_id=row["source_leaf_id"], source_attempt_id=row["source_attempt_id"],
            completed_at=row["completed_at"], children=children,
            split_parent_leaf_id=split_parent, split_attempt_id=split_attempt))
    current_retained_request(repo, connection, request_id, run)
    return {key: tuple(values) for key, values in evidence.items()}

def put_result_reference(repo, connection, request_id, leaf_id, source_attempt_id):
    current_retained_request(repo, connection, request_id)
    require(connection.execute("SELECT 1 FROM pdf_continuation_leaf WHERE id=? AND request_id=?",
        (leaf_id, request_id)).fetchone() is not None, "Reference target belongs to another request")
    row = connection.execute("""SELECT a.request_sha256,o.response_sha256,o.model_sha256,o.transport_sha256
        FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
        WHERE a.id=? AND o.status='validated'""", (source_attempt_id,)).fetchone()
    require(row is not None, "A retained reference requires an actual validated write")
    _insert(connection, "pdf_continuation_result_reference",
        ("leaf_id","source_attempt_id","request_sha256","response_sha256","model_sha256","transport_sha256","created_at"),
        (leaf_id,source_attempt_id,row["request_sha256"],row["response_sha256"],
            row["model_sha256"],row["transport_sha256"],db_now_ms(connection)))

def put_inherited_partition(repo, connection, request_id, parent_leaf_id, source_attempt_id, children):
    current_retained_request(repo, connection, request_id)
    require(connection.execute("SELECT 1 FROM pdf_continuation_leaf WHERE id=? AND request_id=?",
        (parent_leaf_id, request_id)).fetchone() is not None, "Partition target belongs to another request")
    encoded = _json(children)
    _insert(connection, "pdf_continuation_inherited_partition",
        ("parent_leaf_id","refused_attempt_id","children_json","children_sha256","created_at"),
        (parent_leaf_id,source_attempt_id,encoded,_hash(encoded),db_now_ms(connection)))


def retained_active(connection, request_id):
    return tuple(dict(row) for row in connection.execute(
        "SELECT l.* FROM pdf_continuation_leaf l WHERE l.request_id=? AND (" + _active() +
        ") ORDER BY l.parent_ordinal,l.depth,l.coverage_json,l.id", (request_id,)))


def _selected_origin(connection, request_id, leaf_id, status, source_attempt_id=None):
    """Direct origin proof shared by prepare and atomic no-provider commit.

    This intentionally does not require the destination job to be succeeded:
    prepare runs under its normal running claim. The durable success trigger
    independently binds its canonical schema2 result in the commit transaction.
    """
    require(status in ("referenced_result", "inherited_partition"))
    if status == "referenced_result":
        selected = "JOIN pdf_continuation_result_reference descriptor ON descriptor.leaf_id=l.id"
        attempt = "descriptor.source_attempt_id"
        extra = """so.status='validated' AND descriptor.request_sha256=l.request_sha256
            AND descriptor.response_sha256=so.response_sha256 AND descriptor.model_sha256=so.model_sha256
            AND descriptor.transport_sha256=so.transport_sha256"""
        partition_fields = "NULL AS children_json,NULL AS children_sha256,NULL AS source_children_json"
    else:
        selected = """JOIN pdf_continuation_inherited_partition descriptor ON descriptor.parent_leaf_id=l.id
            JOIN pdf_continuation_split source_split ON source_split.refused_attempt_id=descriptor.refused_attempt_id"""
        attempt = "descriptor.refused_attempt_id"
        extra = "so.status='context_refused' AND source_split.parent_leaf_id=sl.id"
        partition_fields = "descriptor.children_json,descriptor.children_sha256,source_split.children_json AS source_children_json"
    query = """SELECT sa.id AS source_attempt_id,sl.id AS source_leaf_id,sq.id AS source_request_id,
        sl.parent_ordinal,sl.coverage_json,sl.coverage_sha256,sl.request_sha256,sl.input_size_bytes,
        slb.payload_json,so.*,sob.response,sob.model_json,sob.transport_json,""" + partition_fields + """
        FROM pdf_continuation_leaf l JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
        JOIN pdf_continuation_request q ON q.id=l.request_id
        """ + selected + """
        JOIN pdf_continuation_attempt sa ON sa.id=""" + attempt + """
        JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
        JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=sa.id
        JOIN pdf_continuation_leaf sl ON sl.id=sa.leaf_id
        JOIN pdf_continuation_leaf_body slb ON slb.leaf_id=sl.id
        JOIN pdf_continuation_request sq ON sq.id=sl.request_id JOIN durable_job sj ON sj.id=sa.job_id
        WHERE l.id=? AND l.request_id=? AND (""" + _same_requests() + ") AND (" + _same_leaf() + ") AND (" + _actual_origin() + ") AND (" + extra + """ )
        AND NOT EXISTS(SELECT 1 FROM pdf_continuation_attempt WHERE leaf_id=l.id)"""
    rows = connection.execute(query, (leaf_id, request_id)).fetchall()
    require(len(rows) == 1, "Selected retained execution no longer has one actual origin")
    row = dict(rows[0])
    require(source_attempt_id is None or row["source_attempt_id"] == source_attempt_id,
        "Selected retained attempt differs")
    _body(row)
    return row


def _replay_origin(connection, request_id, leaf_id, status, row, run):
    saved = json.loads(row["payload_json"])
    leaf = make_leaf(run.source.plan, row["parent_ordinal"], json.loads(row["coverage_json"]),
        exact_parent=saved["exact_parent"])
    _checked_leaf_body(row, leaf)
    if status == "referenced_result":
        validate_output_receipt(run.source.plan, leaf, row["response"], row["model_json"], row["transport_json"])
    else:
        validate_refusal_receipt(run.source.plan, leaf, json.loads(row["transport_json"]))
        require(_hash(row["children_json"]) == row["children_sha256"])
        children, original = json.loads(row["children_json"]), json.loads(row["source_children_json"])
        require(type(children) is list and type(original) is list and len(children) == len(original))
        coverages = []
        for child_id, original_id in zip(children, original, strict=True):
            pair = connection.execute("""SELECT child.coverage_json FROM pdf_continuation_leaf child
                JOIN pdf_continuation_leaf_body cb ON cb.leaf_id=child.id
                JOIN pdf_continuation_leaf prior ON prior.id=? JOIN pdf_continuation_leaf_body pb ON pb.leaf_id=prior.id
                WHERE child.id=? AND child.request_id=? AND child.parent_leaf_id=?
                  AND prior.request_id=? AND prior.parent_leaf_id=? AND child.kind='write' AND prior.kind='write'
                  AND child.parent_ordinal=prior.parent_ordinal AND child.coverage_json=prior.coverage_json
                  AND child.coverage_sha256=prior.coverage_sha256 AND child.request_sha256=prior.request_sha256
                  AND child.input_size_bytes=prior.input_size_bytes AND cb.payload_json=pb.payload_json""",
                (original_id, child_id, request_id, leaf_id, row["source_request_id"], row["source_leaf_id"])).fetchone()
            require(pair is not None, "Inherited exact child differs from actual historical partition")
            coverages.append(json.loads(pair[0]))
        exact_union(json.loads(row["coverage_json"]), coverages)
    return leaf


def retained_execution(repo, connection, request_id, leaf_id, run):
    current_retained_request(repo, connection, request_id, run)
    reference = connection.execute("SELECT source_attempt_id FROM pdf_continuation_result_reference WHERE leaf_id=?", (leaf_id,)).fetchone()
    partition = connection.execute("SELECT refused_attempt_id FROM pdf_continuation_inherited_partition WHERE parent_leaf_id=?", (leaf_id,)).fetchone()
    require(not (reference is not None and partition is not None), "Conflicting retained execution")
    if reference is None and partition is None:
        return None
    status = "referenced_result" if reference is not None else "inherited_partition"
    attempt = (reference if reference is not None else partition)[0]
    row = _selected_origin(connection, request_id, leaf_id, status, attempt)
    _replay_origin(connection, request_id, leaf_id, status, row, run)
    current_retained_request(repo, connection, request_id, run)
    return dict(schema_version=2, request_id=request_id, leaf_id=leaf_id, source_attempt_id=attempt, status=status)


def assert_retained_execution(repo, connection, request_id, leaf_id, source_attempt_id, status):
    current_retained_request(repo, connection, request_id)
    _selected_origin(connection, request_id, leaf_id, status, source_attempt_id)


def retained_ready(repo, connection, request_id):
    current_retained_request(repo, connection, request_id)
    if not retained_active(connection, request_id):
        return False
    incomplete = connection.execute("""SELECT 1 FROM pdf_continuation_request q
        JOIN pdf_continuation_leaf l ON l.request_id=q.id WHERE q.id=? AND (""" + _active() + """ )
        AND l.kind<>'reuse' AND NOT (
            EXISTS(SELECT 1 FROM pdf_continuation_attempt a JOIN pdf_continuation_outcome o ON o.attempt_id=a.id
                JOIN durable_job child ON child.id=a.job_id WHERE a.leaf_id=l.id AND o.status='validated'
                    AND child.state='succeeded' AND child.cancel_requested=0)
            OR (""" + _reference_ready() + ")) LIMIT 1", (request_id,)).fetchone()
    parents = connection.execute("""SELECT 1 FROM pdf_continuation_request q
        JOIN pdf_continuation_leaf parent ON parent.request_id=q.id
        JOIN pdf_continuation_inherited_partition ip ON ip.parent_leaf_id=parent.id
        WHERE q.id=? AND NOT (""" + _partition_ready() + ") LIMIT 1", (request_id,)).fetchone()
    return incomplete is None and parents is None


def retained_results(repo, connection, request_id, run):
    """Normal output replay with explicit, direct actual origin provenance."""
    repo.inventory(connection, request_id, run)
    q, _ = repo.bound(connection, request_id, run)
    require(is_retained_request(connection, request_id))
    original = {receipt.request_sha256: receipt for receipt in repo.original.load_receipts(connection, run)}
    active = retained_active(connection, request_id)
    source_rows = {}
    for leaf in active:
        if leaf["kind"] == "reuse":
            continue
        reference = connection.execute("SELECT source_attempt_id FROM pdf_continuation_result_reference WHERE leaf_id=?", (leaf["id"],)).fetchone()
        if reference is not None:
            require(connection.execute("SELECT 1 FROM pdf_continuation_leaf l JOIN pdf_continuation_request q ON q.id=l.request_id WHERE l.id=? AND (" +
                _reference_ready() + ")", (leaf["id"],)).fetchone() is not None,
                "Retained result job is not canonically completed")
            row = _selected_origin(connection, request_id, leaf["id"], "referenced_result", reference[0])
            origin = "retained_write"
        else:
            rows = connection.execute("""SELECT sa.id AS source_attempt_id,l.id AS source_leaf_id,q.id AS source_request_id,
                l.parent_ordinal,l.coverage_json,l.coverage_sha256,l.request_sha256,l.input_size_bytes,lb.payload_json,
                so.*,sob.response,sob.model_json,sob.transport_json FROM pdf_continuation_leaf l
                JOIN pdf_continuation_request q ON q.id=l.request_id JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
                JOIN pdf_continuation_attempt sa ON sa.leaf_id=l.id
                JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
                JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=sa.id
                JOIN durable_job sj ON sj.id=sa.job_id WHERE l.id=? AND l.request_id=? AND so.status='validated' AND (""" +
                _actual_origin("l", "sa", "so", "sj", "q") + ")", (leaf["id"], request_id)).fetchall()
            require(len(rows) == 1, "Current result has no single actual completed origin")
            row, origin = dict(rows[0]), "current_write"
            _body(row)
        source_rows[leaf["id"]] = (row, origin)
    write_ids = tuple(source_rows)
    restored = {}
    if write_ids:
        _, requests = _prepare_inventory_and_leaves(run.source.plan,
            tuple(_selection(source_rows[key][0]) for key in write_ids))
        restored = dict(zip(write_ids, requests, strict=True))
    results = []
    for leaf in active:
        projection = None
        if leaf["kind"] == "reuse":
            ref = connection.execute("SELECT * FROM pdf_continuation_reuse WHERE leaf_id=?", (leaf["id"],)).fetchone()
            require(ref is not None and ref["request_sha256"] in original)
            receipt = original[ref["request_sha256"]]
            batch = run.source.plan.batches[leaf["parent_ordinal"] - 1]
            require(receipt.batch_id == batch.batch_id and receipt.response_sha256 == ref["response_sha256"]
                and _hash(_json(dict(receipt.model))) == ref["model_sha256"]
                and _hash(receipt.transport_json) == ref["transport_sha256"]
                and leaf["request_sha256"] == ref["request_sha256"], "Original reuse differs")
            response, response_sha = receipt.response, receipt.response_sha256
            source_attempt, model_sha, transport_sha = ref["parent_attempt_id"], ref["model_sha256"], ref["transport_sha256"]
            origin, origin_request, origin_leaf = "original_reuse", q["parent_run_id"], batch.batch_id
            reference = dict(kind="reuse", parent_attempt_id=source_attempt)
        else:
            row, origin = source_rows[leaf["id"]]
            request = restored[leaf["id"]]
            _checked_leaf_body(row, request)
            _, projection, _ = validate_output_receipt(run.source.plan, request,
                row["response"], row["model_json"], row["transport_json"])
            response, response_sha = row["response"], row["response_sha256"]
            source_attempt, model_sha, transport_sha = row["source_attempt_id"], row["model_sha256"], row["transport_sha256"]
            origin_request, origin_leaf = row["source_request_id"], row["source_leaf_id"]
            reference = dict(kind="write", attempt_id=source_attempt)
        result = dict(parent_ordinal=leaf["parent_ordinal"], coverage_json=leaf["coverage_json"],
            leaf_id=leaf["id"], request_sha256=leaf["request_sha256"], response=response,
            response_sha256=response_sha, reference=reference, kind=leaf["kind"],
            source_attempt_id=source_attempt, model_sha256=model_sha, transport_sha256=transport_sha,
            execution_source=origin, origin_request_id=origin_request, origin_leaf_id=origin_leaf)
        if projection is not None:
            result["output_projection"] = projection
        results.append(result)
    def order(row):
        ranges = json.loads(row["coverage_json"])["ranges"]
        return row["parent_ordinal"], min(((r["chunk_ordinal"], r["start"]) for r in ranges), default=(10**9, 0)), row["leaf_id"]
    return tuple(sorted(results, key=order))


def retained_assembly(repo, connection, request_id, run, results, policy):
    from ..pdf_continuation_retained_assembly import RetainedAssemblySplit, assemble_retained
    from ..pdf_visual_continuation import parent_inventory
    repo.inventory(connection, request_id, run)
    repo.bound(connection, request_id, run)
    require(is_retained_request(connection, request_id))
    all_rows = [dict(row) for row in connection.execute("""SELECT l.*,lb.payload_json
        FROM pdf_continuation_leaf l LEFT JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
        WHERE l.request_id=? ORDER BY l.parent_ordinal,l.depth,l.coverage_json,l.id""", (request_id,))]
    require(len(all_rows) <= policy.max_leaves and all_rows, "Retained assembly exceeds finite tree budget")
    roots = parent_inventory(run.source.plan)
    writes = tuple(row for row in all_rows if row["kind"] == "write")
    selections, indexes = [], {}
    def selection_index(spec):
        ordinal, coverage, exact_parent = spec
        key = ordinal, _json(coverage), exact_parent
        if key not in indexes:
            indexes[key] = len(selections)
            selections.append(spec)
        return indexes[key]
    root_indexes = tuple(selection_index((parent["ordinal"], parent["coverage"], True)) for parent in roots)
    write_indexes = tuple(selection_index(_selection(row)) for row in writes)
    require(len(selections) <= len(all_rows), "Assembly selections exceed admitted exact tree nodes")
    _, leaves = _prepare_inventory_and_leaves(run.source.plan, tuple(selections))
    root_leaves = tuple(leaves[index] for index in root_indexes)
    by_id = dict(zip((row["id"] for row in writes), (leaves[index] for index in write_indexes), strict=True))
    for row in writes:
        _checked_leaf_body(row, by_id[row["id"]])
    splits = []
    for row in writes:
        direct = connection.execute("SELECT * FROM pdf_continuation_split WHERE parent_leaf_id=?", (row["id"],)).fetchone()
        inherited = connection.execute("SELECT * FROM pdf_continuation_inherited_partition WHERE parent_leaf_id=?", (row["id"],)).fetchone()
        require(not (direct is not None and inherited is not None), "Conflicting partition lineage")
        if direct is None and inherited is None:
            continue
        split = dict(direct if direct is not None else inherited)
        require(_hash(split["children_json"]) == split["children_sha256"])
        if inherited is not None:
            require(connection.execute("SELECT 1 FROM pdf_continuation_leaf parent JOIN pdf_continuation_request q ON q.id=parent.request_id WHERE parent.id=? AND (" +
                _partition_ready() + ")", (row["id"],)).fetchone() is not None, "Inherited parent is not canonically completed")
            origin = _selected_origin(connection, request_id, row["id"], "inherited_partition", split["refused_attempt_id"])
            _replay_origin(connection, request_id, row["id"], "inherited_partition", origin, run)
        else:
            origin = connection.execute("""SELECT so.*,sob.response,sob.model_json,sob.transport_json,
                sa.request_sha256,l.parent_ordinal,l.coverage_json,l.coverage_sha256,l.input_size_bytes,lb.payload_json
                FROM pdf_continuation_attempt sa JOIN pdf_continuation_outcome so ON so.attempt_id=sa.id
                JOIN pdf_continuation_outcome_body sob ON sob.attempt_id=sa.id
                JOIN pdf_continuation_leaf l ON l.id=sa.leaf_id JOIN pdf_continuation_leaf_body lb ON lb.leaf_id=l.id
                JOIN pdf_continuation_request q ON q.id=l.request_id JOIN durable_job sj ON sj.id=sa.job_id
                WHERE sa.id=? AND l.id=? AND q.id=? AND so.status='context_refused' AND (""" +
                _actual_origin("l", "sa", "so", "sj", "q") + ")", (split["refused_attempt_id"], row["id"], request_id)).fetchone()
            require(origin is not None, "Direct partition has no actual completed refusal")
            _body(origin)
            validate_refusal_receipt(run.source.plan, by_id[row["id"]], json.loads(origin["transport_json"]))
        children = json.loads(split["children_json"])
        require(type(children) is list and 2 <= len(children) <= 16 and len(set(children)) == len(children)
            and all(child in by_id for child in children), "Retained assembly child is unavailable")
        actual_children = {child["id"] for child in all_rows if child["parent_leaf_id"] == row["id"]}
        require(set(children) == actual_children, "Retained assembly has orphan children")
        splits.append(RetainedAssemblySplit(parent=by_id[row["id"]], children=tuple(by_id[child] for child in children)))
    return assemble_retained(run.source.plan, tuple(root_leaves), tuple(splits), results, policy)

