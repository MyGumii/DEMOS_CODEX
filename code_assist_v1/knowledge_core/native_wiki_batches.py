"""Bounded multi-slot writing calls with one real receipt per model invocation.

Logical prose slots keep their independent source ownership and final position.
Never fabricate per-slot transport receipts from one batch response. This pure
contract is opt-in, not registered with old durable preservation workflows.
"""
from dataclasses import asdict, dataclass, field, replace
from collections.abc import Mapping

from .native_wiki_authoring import (
    NATIVE_AUTHORING_PROMPT, DISPLAY_AUTHORING_PROMPT, NativeWikiPlan, NativeAuthoringStopped,
    RESPONSE_BYTES, native_authoring_request, _call_options, _decode,
    _assemble_validated_native_wiki,
)
from .wiki_authoring import (
    AuthoringIncompleteError, AuthoringOutputError, AuthoringSourceError,
    AuthoringWorkLimitError, _array, _hash, _json, _model_receipt, _shape, _transport_receipt,
)
from .wiki_preservation import _json_output


NATIVE_BATCH_VERSION = "wiki-native-batch-authoring-v1"
DISPLAY_BATCH_VERSION = "wiki-native-display-batch-authoring-v1"
NATIVE_BATCH_TASK = "canonical_native_wiki_batch_authoring"
MAX_TASKS_PER_BATCH = 4
MIN_OUTPUT_TOKENS_PER_TASK = 1024
NATIVE_BATCH_PROMPT = NATIVE_AUTHORING_PROMPT.partition("Return JSON only:")[0] + """
The input contains several independent writing tasks. Apply the preceding
instructions to EACH task using ONLY that task's owned_source, heading path and
reference_context. Do not move a fact to another task or cite another task's
source unless it is explicitly included in this task's own reference_context.
Return JSON only: {schema_version:1,batch_id,results}. results must contain
exactly one result per supplied task: {schema_version:1,chunk_id,claims,warnings}.
claims is a nonempty array of {text,quotes}; quotes is a nonempty array of
{source_id,text}. Each quote must be exact, contiguous and unique within that
task's supplied source_id. Each claim requires an owned_source quote; add exact
context quotes when resolving an explicitly established antecedent. warnings
must be []. Express uncertainty as a grounded claim. Do not return a review,
repair, PASS judgment or completeness claim. This is one writing stage.
"""

DISPLAY_BATCH_PROMPT = (DISPLAY_AUTHORING_PROMPT.partition("Return JSON only:")[0]
    + NATIVE_BATCH_PROMPT[len(NATIVE_AUTHORING_PROMPT.partition("Return JSON only:")[0]):])


def _version(plan):
    return NATIVE_BATCH_VERSION if plan.source.display_json is None else DISPLAY_BATCH_VERSION


def _prompt(plan):
    return NATIVE_BATCH_PROMPT if plan.source.display_json is None else DISPLAY_BATCH_PROMPT


@dataclass(frozen=True, slots=True)
class NativeWritingBatch:
    ordinal: int
    batch_id: str
    chunk_ordinals: tuple[int, ...]


def _batch(plan, ordinal, chunk_ordinals):
    identity = _hash(_json([_version(plan), plan.plan_sha256, ordinal, chunk_ordinals]))
    return NativeWritingBatch(ordinal, "B_" + identity, tuple(chunk_ordinals))


def _request(plan, batch):
    tasks = []
    for ordinal in batch.chunk_ordinals:
        item = native_authoring_request(plan, ordinal)[0]
        tasks.append({key: item[key] for key in ("chunk_id", "owned_source", "source_heading_path",
            "reference_context", "source_range_unit", "context_scope", "heading_context_policy")})
    identity = plan.source.identity()
    payload = {"schema_version": 1, "policy_version": _version(plan),
        "plan_sha256": plan.plan_sha256, "batch_id": batch.batch_id, "tasks": tasks,
        "source": {key: value for key, value in identity.items() if key != "assets"},
        "canonical_assets_sha256": _hash(_json(identity["assets"])),
        "canonical_asset_count": len(identity["assets"]), "tables_preserved_by_server": True}
    messages = [{"role": "system", "content": _prompt(plan)},
        {"role": "user", "content": _json(payload)}]
    if len(_json(messages).encode()) > min(65_536, plan.model_policy.request_budget_bytes - 2048):
        raise AuthoringWorkLimitError("Complete native batch exceeds its request budget.")
    options = _call_options(plan, messages)
    digest = _hash(_json({"policy_version": _version(plan), "plan_sha256": plan.plan_sha256,
        "batch": asdict(batch), "task": NATIVE_BATCH_TASK, "call_options": options,
        "grouping_policy": {"max_tasks": MAX_TASKS_PER_BATCH,
            "min_output_tokens_per_task": MIN_OUTPUT_TOKENS_PER_TASK}}))
    return payload, messages, digest


@dataclass(frozen=True, slots=True)
class NativeBatchPlan:
    source_plan: NativeWikiPlan
    batches: tuple[NativeWritingBatch, ...] = field(init=False)
    reservation_json: str = field(init=False)

    def __post_init__(self):
        plan = self.source_plan
        if type(plan) is not NativeWikiPlan:
            raise AuthoringSourceError("A verified native source plan is required.")
        if any(c.text.strip() for c in plan.chunks) and plan.model_policy.max_tokens < MIN_OUTPUT_TOKENS_PER_TASK:
            raise AuthoringWorkLimitError("Native batch output allowance is too small for a writing task.")
        batches, pending, owned_bytes = [], [], 0
        for chunk in plan.chunks:
            if not chunk.text.strip():
                continue
            size = len(chunk.text.encode())
            tentative = _batch(plan, len(batches) + 1, [*pending, chunk.ordinal])
            fits = (owned_bytes + size <= plan.chunk_bytes
                and len(pending) + 1 <= MAX_TASKS_PER_BATCH
                and (len(pending) + 1) * MIN_OUTPUT_TOKENS_PER_TASK <= plan.model_policy.max_tokens)
            if fits:
                try:
                    _request(plan, tentative)
                except AuthoringWorkLimitError:
                    fits = False
            if not fits and pending:
                batches.append(_batch(plan, len(batches) + 1, pending))
                pending, owned_bytes = [], 0
            pending.append(chunk.ordinal)
            owned_bytes += size
            # A single task must fit too. Never turn a failed candidate into a
            # silently truncated request or lose its final prose slot.
            _request(plan, _batch(plan, len(batches) + 1, pending))
        if pending:
            batches.append(_batch(plan, len(batches) + 1, pending))
        attempts = len(batches) * plan.model_policy.max_attempts
        inputs = sum(len(_json(_request(plan, batch)[1]).encode()) for batch in batches) * plan.model_policy.max_attempts
        response_bytes = attempts * RESPONSE_BYTES
        if (attempts > plan.work_policy.model_attempts or inputs > plan.work_policy.logical_input_bytes
                or response_bytes > plan.work_policy.response_bytes):
            raise AuthoringWorkLimitError("All native batch work exceeds its document budget.")
        object.__setattr__(self, "batches", tuple(batches))
        object.__setattr__(self, "reservation_json", _json({"model_attempts": attempts,
            "logical_input_bytes": inputs, "response_bytes": response_bytes,
            "batch_count": len(batches), "prose_chunk_count": sum(bool(c.text.strip()) for c in plan.chunks),
            "max_tasks_per_batch": MAX_TASKS_PER_BATCH,
            "min_output_tokens_per_task": MIN_OUTPUT_TOKENS_PER_TASK,
            "output_fit_is_a_semantic_guarantee": False}))


def native_batch_request(plan, ordinal):
    if type(plan) is not NativeBatchPlan or type(ordinal) is not int or not 1 <= ordinal <= len(plan.batches):
        raise AuthoringSourceError("Unknown native writing batch.")
    return _request(plan.source_plan, plan.batches[ordinal - 1])


@dataclass(frozen=True, slots=True)
class NativeBatchReceipt:
    batch_id: str
    request_sha256: str
    response: str = field(repr=False)
    response_sha256: str
    model: tuple[tuple[str, str], ...]
    transport_json: str
    policy_version: str = NATIVE_BATCH_VERSION
    validation_state: str = "validated"


def replay_native_batch(plan, ordinal, receipt):
    _, messages, digest = native_batch_request(plan, ordinal)
    batch = plan.batches[ordinal - 1]
    if (type(receipt) is not NativeBatchReceipt or receipt.batch_id != batch.batch_id
            or receipt.policy_version != _version(plan.source_plan) or receipt.validation_state != "validated"
            or receipt.request_sha256 != digest or receipt.response_sha256 != _hash(receipt.response)):
        raise AuthoringIncompleteError("Invalid native batch receipt binding.")
    policy = plan.source_plan.model_policy
    model = _model_receipt(receipt.model, policy)
    _transport_receipt(receipt.transport_json, policy, messages=messages, model=model)
    row = _shape(_json_output(receipt.response), {"schema_version", "batch_id", "results"})
    if type(row["schema_version"]) is not int or row["schema_version"] != 1 or row["batch_id"] != batch.batch_id:
        raise AuthoringOutputError("Stale native batch response.")
    chunks = {plan.source_plan.chunks[i - 1].chunk_id: plan.source_plan.chunks[i - 1] for i in batch.chunk_ordinals}
    claims = {}
    for item in _array(row["results"]):
        if type(item) is not dict or type(item.get("chunk_id")) is not str:
            raise AuthoringOutputError("Invalid native batch result.")
        chunk_id = item["chunk_id"]
        if chunk_id not in chunks or chunk_id in claims:
            raise AuthoringOutputError("Duplicate or foreign native batch chunk.")
        claims[chunk_id] = _decode(plan.source_plan, chunks[chunk_id], _json(item))
    if set(claims) != set(chunks):
        raise AuthoringIncompleteError("Every batch task must finish; no partial result.")
    return claims


class NativeWikiBatchAuthor:
    def __init__(self, *, runner):
        if not callable(runner):
            raise TypeError("runner must be callable")
        self._runner = runner

    def author_batch(self, plan, ordinal, *, receipt=None, on_receipt=None, attempt_guard=None):
        payload, messages, digest = native_batch_request(plan, ordinal)
        if any(callback is not None and not callable(callback) for callback in (on_receipt, attempt_guard)):
            raise TypeError("Batch callbacks must be callable.")
        if attempt_guard is not None:
            attempt_guard()
        if receipt is not None:
            replay_native_batch(plan, ordinal, receipt)
            return receipt
        retained = None
        try:
            response, metadata = self._runner(NATIVE_BATCH_TASK, payload,
                **_call_options(plan.source_plan, messages),
                **({"attempt_guard": attempt_guard} if attempt_guard is not None else {}))
            if type(response) is not str or len(response.encode()) > RESPONSE_BYTES:
                raise AuthoringOutputError("Missing or oversized native batch response.")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            model = tuple((key, metadata[key]) for key in ("model_kind", "model_id", "model_used")
                if isinstance(metadata.get(key), str) and len(metadata[key]) <= 160)
            raw_transport = metadata.get("request_transport", {})
            transport = '{"invalid_transport":true}'
            if (type(raw_transport) is dict and set(raw_transport) <= {"serializer", "model", "body_bytes", "body_sha256"}
                    and all((type(v) is str and len(v) <= 200) or (type(v) is int and 0 <= v < 2**63)
                        for v in raw_transport.values())):
                transport = _json(raw_transport)
            retained = NativeBatchReceipt(payload["batch_id"], digest, response, _hash(response), model,
                transport, policy_version=_version(plan.source_plan), validation_state="attempt_only")
            validated = replace(retained, validation_state="validated")
            replay_native_batch(plan, ordinal, validated)
            if attempt_guard is not None:
                attempt_guard()
            if on_receipt is not None:
                on_receipt(validated)
            return validated
        except Exception as exc:
            raise NativeAuthoringStopped(retained) from exc


def assemble_native_batches(plan, receipts):
    if type(plan) is not NativeBatchPlan or type(receipts) not in (list, tuple):
        raise AuthoringIncompleteError("Invalid native batch assembly.")
    p = plan.source_plan
    checked_source = NativeWikiPlan(p.source, p.chunk_bytes, p.context_bytes,
        p.model_policy, p.work_policy, p.execution_policy)
    if checked_source != p or NativeBatchPlan(checked_source) != plan:
        raise AuthoringIncompleteError("Native batch source plan changed.")
    if any(type(receipt) is not NativeBatchReceipt for receipt in receipts):
        raise AuthoringIncompleteError("Single/batch receipt lineages cannot be mixed.")
    by_id = {receipt.batch_id: receipt for receipt in receipts}
    if len(by_id) != len(receipts) or set(by_id) != {b.batch_id for b in plan.batches}:
        raise AuthoringIncompleteError("Every native batch must complete exactly once.")
    claims = {c.chunk_id: [] for c in p.chunks if not c.text.strip()}
    refs, retained = {}, []
    for batch in plan.batches:
        receipt = by_id[batch.batch_id]
        decoded = replay_native_batch(plan, batch.ordinal, receipt)
        claims.update(decoded)
        receipt_value = asdict(receipt)
        retained.append(receipt_value)
        for chunk_id in decoded:
            refs[chunk_id] = {"batch_id": batch.batch_id, "receipt_sha256": _hash(_json(receipt_value))}
    lineage = {"policy_version": _version(p), "prompt_sha256": _hash(_prompt(p)),
        "batches": [asdict(batch) for batch in plan.batches], "reservation_json": plan.reservation_json,
        "receipts": retained, "logical_authoring_calls": len(receipts)}
    return _assemble_validated_native_wiki(p, claims, refs, batch_lineage=lineage)
