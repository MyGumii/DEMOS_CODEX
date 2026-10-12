"""Pure, bounded planning from repository-verified *actual* retained writes.

This module creates no jobs, receipts, model calls, database connections or
publication authority. Evidence fields are assertions supplied by the caller's
already-authorized repository/replay boundary, not a substitute for that
boundary. The caller must repeat its normal source/claim/lease guards at commit.
An inherited partition points directly to a real historical refusal and split;
a reference points directly to a real validated write, never another reference.
"""
from dataclasses import dataclass
import json
from typing import Callable, Literal
from uuid import UUID

from .pdf_visual_continuation import (
    ContinuationLeafRequest, ContinuationPolicy, checked_leaf, checked_coverage,
    exact_union, require,
)
from .wiki_authoring import AuthoringWorkLimitError


@dataclass(frozen=True, slots=True)
class RetainedSplitChild:
    """One complete original child of an actual historical split."""

    leaf: ContinuationLeafRequest
    source_request_id: str
    source_leaf_id: str


@dataclass(frozen=True, slots=True)
class RetainedWriteEvidence:
    """Authority/replay-verified actual outcome; never a reference-table row.

    For a refusal, ``children`` must be the complete actual split, including
    children without completed results. Their leaf identities are independently
    re-materialized by the planner. Successful provider transport, model and
    source/body closure are verified by the repository before this callback.
    """

    leaf: ContinuationLeafRequest
    status: Literal["validated", "context_refused"]
    source_request_id: str
    source_leaf_id: str
    source_attempt_id: str
    completed_at: int
    children: tuple[RetainedSplitChild, ...] = ()
    split_parent_leaf_id: str | None = None
    split_attempt_id: str | None = None
    source_kind: str = "actual_write"
    source_request_status: str = "failed"
    source_job_state: str = "succeeded"
    source_job_cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class RetainedPlanNode:
    """Immutable requested work, direct result reference, or inherited split."""

    leaf: ContinuationLeafRequest
    relation: Literal["write", "reference", "partition"]
    source_request_id: str | None = None
    source_leaf_id: str | None = None
    source_attempt_id: str | None = None
    children: tuple["RetainedPlanNode", ...] = ()


EvidenceLookup = Callable[
    [str], RetainedWriteEvidence | tuple[RetainedWriteEvidence, ...] | None
]
ChildMaterializer = Callable[
    [ContinuationLeafRequest, dict, bool], ContinuationLeafRequest
]


def _canonical_id(value):
    require(type(value) is str, "Retained evidence requires canonical UUIDs")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError):
        require(False, "Retained evidence requires canonical UUIDs")
    require(canonical == value, "Retained evidence requires canonical UUIDs")


def _leaf(leaf, policy):
    payload = checked_leaf(leaf)
    require(type(leaf.parent_ordinal) is int and 1 <= leaf.parent_ordinal <= 4096,
        "Invalid retained original parent ordinal")
    require(type(payload.get("exact_parent")) is bool
        and payload.get("prompt_sha256") == policy.prompt_sha256,
        "Retained leaf differs from the admitted writer prompt")
    parent_sha = payload.get("parent_request_sha256")
    require(type(parent_sha) is str and len(parent_sha) == 64
        and set(parent_sha) <= set("0123456789abcdef"),
        "Retained leaf requires its exact original parent request identity")
    require(type(leaf.input_size_bytes) is int
        and len(leaf.payload_json.encode()) <= leaf.input_size_bytes <= 4_194_304,
        "Invalid complete retained leaf input reservation")
    coverage = checked_coverage(json.loads(leaf.coverage_json))
    require(any(coverage.values()), "A retained leaf must own original source")
    return payload, coverage


def _no_owner_overlap(coverages):
    """Reject duplicate/overlapping ownership, including across root ordinals."""
    ranges, tables, visuals, range_identity = {}, set(), set(), {}
    for coverage in coverages:
        for row in coverage["ranges"]:
            key = row["chunk_id"]
            identity = tuple(row[k] for k in
                ("chunk_ordinal", "block_id", "page", "text_sha256"))
            require(key not in range_identity or range_identity[key] == identity,
                "An original range owner has conflicting identity")
            range_identity[key] = identity
            ranges.setdefault(key, []).append((row["start"], row["end"]))
        for row in coverage["tables"]:
            require(row["cell_key"] not in tables, "Repeated original table owner")
            tables.add(row["cell_key"])
        for row in coverage["visuals"]:
            require(row["image_id"] not in visuals, "Repeated original visual owner")
            visuals.add(row["image_id"])
    for intervals in ranges.values():
        intervals.sort()
        require(all(left[1] <= right[0] for left, right in
            zip(intervals, intervals[1:])), "Overlapping retained source ranges")


def _checked_evidence(value, leaf, policy):
    require(type(value) is RetainedWriteEvidence,
        "Only direct actual write evidence is accepted")
    require(type(value.source_kind) is str and value.source_kind == "actual_write"
        and type(value.source_request_status) is str and value.source_request_status == "failed"
        and type(value.source_job_state) is str and value.source_job_state == "succeeded"
        and value.source_job_cancel_requested is False,
        "Retained evidence is not a completed actual write in a failed request")
    for identity in (value.source_request_id, value.source_leaf_id,
                     value.source_attempt_id):
        _canonical_id(identity)
    require(type(value.completed_at) is int and value.completed_at >= 0,
        "Invalid actual write completion time")
    require(type(value.status) is str and value.status in ("validated", "context_refused")
        and type(value.children) is tuple, "Invalid actual retained outcome")
    _leaf(value.leaf, policy)
    require(value.leaf.identity() == leaf.identity(),
        "Retained evidence differs from the exact requested input")
    if value.status == "validated":
        require(not value.children and value.split_parent_leaf_id is None
            and value.split_attempt_id is None,
            "A validated result cannot fabricate a split pointer")
        return value
    require(value.split_parent_leaf_id == value.source_leaf_id
        and value.split_attempt_id == value.source_attempt_id,
        "A retained partition must point to its actual refused write")
    require(2 <= len(value.children) <= 16,
        "A retained partition must include all actual children")
    parent_payload, parent_coverage = _leaf(leaf, policy)
    child_ids, child_hashes, coverages = set(), set(), []
    for child in value.children:
        require(type(child) is RetainedSplitChild,
            "Invalid actual split child descriptor")
        _canonical_id(child.source_request_id)
        _canonical_id(child.source_leaf_id)
        require(child.source_request_id == value.source_request_id
            and child.source_leaf_id != value.source_leaf_id
            and child.source_leaf_id not in child_ids,
            "Foreign, cyclic or duplicated actual split child pointer")
        child_ids.add(child.source_leaf_id)
        child_payload, coverage = _leaf(child.leaf, policy)
        require(child.leaf.parent_ordinal == leaf.parent_ordinal
            and child_payload.get("parent_request_sha256")
                == parent_payload.get("parent_request_sha256")
            and child.leaf.request_sha256 != leaf.request_sha256
            and child.leaf.request_sha256 not in child_hashes
            and coverage != parent_coverage,
            "A retained split child does not preserve its proper parent identity")
        child_hashes.add(child.leaf.request_sha256)
        coverages.append(coverage)
    _no_owner_overlap(coverages)
    exact_union(parent_coverage, tuple(coverages))
    return value


def plan_retained_roots(
    roots: tuple[ContinuationLeafRequest, ...] | list[ContinuationLeafRequest],
    policy: ContinuationPolicy,
    lookup_evidence: EvidenceLookup,
    materialize_child: ChildMaterializer,
) -> tuple[RetainedPlanNode, ...]:
    """Return immutable nodes without invoking a model or admitting output.

    ``lookup_evidence(request_sha256)`` returns None, one evidence descriptor or
    a tuple of direct descriptors, already checked by the repository authority
    boundary. Exact validated evidence wins over an actual refused split; ties
    choose the newest completed direct attempt deterministically.

    ``materialize_child(parent_leaf, coverage_dict, exact_parent)`` must return
    the exact sealed child input described by the historical actual split.
    Every root and ancestor counts toward ``max_leaves``. Bounds fail closed;
    an oversized inherited partition is never silently replaced by fresh work.
    """
    require(type(policy) is ContinuationPolicy, "Use an admitted continuation policy")
    policy.encoded()
    require(type(roots) in (tuple, list) and bool(roots),
        "Retained planning requires a complete root sequence")
    require(callable(lookup_evidence) and callable(materialize_child),
        "Retained planning requires evidence and materialization callbacks")
    if len(roots) > policy.max_leaves:
        raise AuthoringWorkLimitError("Retained planning exceeds its finite leaf budget")
    roots = tuple(roots)
    root_coverages, ordinals = [], set()
    for root in roots:
        payload, coverage = _leaf(root, policy)
        require(payload["exact_parent"] is True and root.parent_ordinal not in ordinals,
            "Retained roots must be distinct exact original parents")
        ordinals.add(root.parent_ordinal)
        root_coverages.append(coverage)
    _no_owner_overlap(root_coverages)
    seen_requests, seen_attempts, frontier = set(), set(), []
    node_count = 0

    def visit(leaf, depth, ancestors):
        nonlocal node_count
        if depth > policy.max_depth or node_count >= policy.max_leaves:
            raise AuthoringWorkLimitError("Retained planning exceeds its finite depth/leaf budget")
        _, coverage = _leaf(leaf, policy)
        require(leaf.request_sha256 not in ancestors,
            "Cyclic retained request input")
        require(leaf.request_sha256 not in seen_requests,
            "Duplicated retained request input")
        seen_requests.add(leaf.request_sha256)
        node_count += 1
        found = lookup_evidence(leaf.request_sha256)
        records = () if found is None else (found,) if type(found) is RetainedWriteEvidence else found
        require(type(records) is tuple, "Evidence lookup must return direct typed records")
        checked, record_attempts = [], set()
        for record in records:
            record = _checked_evidence(record, leaf, policy)
            require(record.source_attempt_id not in record_attempts,
                "Duplicated actual write evidence")
            record_attempts.add(record.source_attempt_id)
            checked.append(record)
        if not checked:
            frontier.append(coverage)
            return RetainedPlanNode(leaf, "write")
        validated = [record for record in checked if record.status == "validated"]
        chosen = max(validated or checked, key=lambda record:
            (record.completed_at, record.source_request_id,
             record.source_leaf_id, record.source_attempt_id))
        require(chosen.source_attempt_id not in seen_attempts,
            "A direct actual write pointer is reused by different requests")
        seen_attempts.add(chosen.source_attempt_id)
        provenance = (chosen.source_request_id, chosen.source_leaf_id, chosen.source_attempt_id)
        if chosen.status == "validated":
            frontier.append(coverage)
            return RetainedPlanNode(leaf, "reference", *provenance)
        if depth >= policy.max_depth or node_count + len(chosen.children) > policy.max_leaves:
            raise AuthoringWorkLimitError("Actual retained split exceeds its finite depth/leaf budget")
        children = []
        for descriptor in chosen.children:
            payload, child_coverage = _leaf(descriptor.leaf, policy)
            child = materialize_child(leaf, child_coverage, payload["exact_parent"])
            _leaf(child, policy)
            require(child.identity() == descriptor.leaf.identity(),
                "Materialized child differs from its actual historical input")
            children.append(visit(child, depth + 1, ancestors | {leaf.request_sha256}))
        return RetainedPlanNode(leaf, "partition", *provenance, tuple(children))

    planned = tuple(visit(root, 0, frozenset()) for root in roots)
    _no_owner_overlap(frontier)
    return planned
