"""Opt-in PPTX role durable storage; no worker/publication registration.

Uses dedicated tables: legacy B_/N_ batches (four members) are not role RB_/RC_
batches (sixteen members). Historical run/receipt policies are unchanged.
The repository owns no transaction, model call, parser or publication. Current
source authority is revalidated for every effect; restoration reparses originals
outside the writer transaction. Private bodies follow extraction retention.
"""
from dataclasses import asdict, dataclass, field, replace
import hmac
import json
import secrets
from hashlib import sha256
from uuid import UUID, uuid5

from ..db import db_now_ms
from ..pptx_role_authoring import (
    ROLE_AUTHORING_V2_VERSION, ROLE_ASSEMBLY_VERSION, LITERAL_RENDER_VERSION,
    NATIVE_TABLE_RENDER_VERSION, RESPONSE_BYTES, PptxRoleReceipt,
    pptx_role_request, replay_pptx_role_receipt, assemble_pptx_role_wiki,
)
from ..wiki_authoring import AuthoringWorkLimitError, AuthoringModelPolicy, AuthoringWorkPolicy, _json, _hash
from ..wiki_preservation import PreservationExecutionPolicy
from ..services.pptx_role_authoring_source import (
    PreparedPptxRoleSource, ROLE_SOURCE_PREPARATION_VERSION,
)
from ..migrations.v035_pptx_role_storage import FAILURE_CODES, METADATA_TYPES, POLICY, PROMPT_SHA256

from .artifacts import _connection


_KEY = secrets.token_bytes(32)
_NS = UUID("27f27aa2-b937-43ca-a50a-4affd2c98b9d")
_RUN_FIELDS = ("id", "preview_id", "parent_job_id", "actor_user_id", "artifact_id",
    "extraction_snapshot_id", "source_fence_sha256", "source_plan_sha256",
    "metadata_json", "metadata_sha256", "batch_count", "max_executions", "attempt_units")
_BATCH_FIELDS = ("ordinal", "batch_id", "members_json", "request_sha256", "input_size_bytes", "ordinals_json")
_ATTEMPT_FIELDS = ("id", "run_id", "ordinal", "sequence", "operation_id",
    "request_sha256", "attempt_units", "started_at")
_OUTCOME_FIELDS = ("attempt_id", "run_id", "ordinal", "status", "failure_code",
    "response_sha256", "response_size_bytes", "model_sha256", "model_size_bytes",
    "transport_sha256", "transport_size_bytes")
_BODY_FIELDS = ("response", "model_json", "transport_json")


class PptxRoleStorageConflict(ValueError):
    """Sanitized identity/state/retention conflict; never embeds source text."""


def _require(value, message="Native durable storage identity or state differs."):
    if not value:
        raise PptxRoleStorageConflict(message)


def _uuid(value):
    try:
        _require(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise PptxRoleStorageConflict("A canonical operation UUID is required.") from exc
    return value


def _seal(kind, values):
    return hmac.new(_KEY, (kind + "\0" + _json(values)).encode(), sha256).hexdigest()


def _verify(kind, values, seal):
    try:
        valid = type(seal) is str and hmac.compare_digest(_seal(kind, values), seal)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        valid = False
    _require(valid, "Native storage preparation changed or belongs to another process.")


def _row(row, fields):
    return None if row is None else tuple(row[key] for key in fields)


def _insert(connection, table, fields, values):
    connection.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)


@dataclass(frozen=True, slots=True)
class PreparedPptxRoleRun:
    source: PreparedPptxRoleSource = field(repr=False)
    run_id: str
    metadata_json: str
    batches: tuple[tuple, ...]
    seal: str = field(repr=False)

    def close(self):
        self.source.close()

    def __enter__(self):
        self.source.__enter__()
        return self

    def __exit__(self, *_):
        self.close()

    def identity(self):
        s, m = self.source, json.loads(self.metadata_json)
        return (self.run_id, s.preview_id, s.parent_job_id, s.actor_user_id, s.artifact_id,
            s.plan.source.native.snapshot_id, s.fence_sha256, s.plan.plan_sha256,
            self.metadata_json, _hash(self.metadata_json), len(self.batches), m["max_executions"], m["attempt_units"])


def prepare_pptx_role_run(source, *, max_executions=2):
    """Admit all possible durable executions, with exact role policy identity."""
    _require(type(source) is PreparedPptxRoleSource)
    p = source.plan
    _require(p.policy_version == ROLE_AUTHORING_V2_VERSION == POLICY
        and source.source_preparation_version == ROLE_SOURCE_PREPARATION_VERSION
        and _hash(p.prompt) == PROMPT_SHA256,
        "Role storage requires its exact versioned source and prompt.")
    _require(type(max_executions) is int and 1 <= max_executions <= 2)
    batches = []
    for batch in p.batches:
        _, messages, digest = pptx_role_request(p, batch.ordinal)
        batches.append((batch.ordinal, batch.batch_id,
            _json([p.chunks[i - 1].chunk_id for i in batch.chunk_ordinals]),
            digest, len(_json(messages).encode()), _json(list(batch.chunk_ordinals))))
    single = json.loads(p.reservation_json)
    reserved = {key: single[key] * max_executions for key in
        ("model_attempts", "logical_input_bytes", "response_bytes")}
    if any(reserved[key] > getattr(p.work_policy, key) for key in reserved):
        raise AuthoringWorkLimitError("All durable executions exceed the role document budget.")
    identity = p.source.identity()
    display, context = identity["display"], identity["role_context"]
    m = {"schema_version": 1, "policy_version": p.policy_version,
        "source_preparation_version": ROLE_SOURCE_PREPARATION_VERSION,
        "source_role_version": context["version"], "assembly_version": ROLE_ASSEMBLY_VERSION,
        "table_rendering_version": NATIVE_TABLE_RENDER_VERSION, "prompt_sha256": _hash(p.prompt),
        "literal_render_version": LITERAL_RENDER_VERSION,
        "role_context_sha256": context["sha256"], "role_context_size_bytes": context["bytes"],
        "role_context_coverage": context["coverage"],
        "display_version": display["version"], "display_coverage": display["coverage"],
        "display_slot_version": display["slot_version"], "display_sha256": display["sha256"],
        "display_size_bytes": display["bytes"],
        "preview_id": source.preview_id, "source_plan_sha256": p.plan_sha256,
        "source_fence_sha256": source.fence_sha256, "configuration_sha256": _hash(source.configuration_json),
        "configuration_size_bytes": len(source.configuration_json.encode()),
        "batch_count": len(batches), "max_executions": max_executions, "attempt_units": p.model_policy.max_attempts,
        "reserved_model_attempts": reserved["model_attempts"], "reserved_input_bytes": reserved["logical_input_bytes"],
        "reserved_response_bytes": reserved["response_bytes"], "budget_model_attempts": p.work_policy.model_attempts,
        "budget_input_bytes": p.work_policy.logical_input_bytes, "budget_response_bytes": p.work_policy.response_bytes,
        "budget_markdown_bytes": p.work_policy.markdown_bytes, "budget_manifest_bytes": p.work_policy.manifest_bytes}
    _require(set(m) == set(METADATA_TYPES))
    _require(all(type(m[k]) is int and 0 <= m[k] < 2**63 for k, kind in METADATA_TYPES.items() if kind == "integer"))
    _require(1 <= m["configuration_size_bytes"] <= 65536 and 1 <= m["attempt_units"] <= 1024)
    metadata = _json(m)
    run_id = str(uuid5(_NS, "run:" + source.preview_id + ":" + _hash(metadata)))
    prepared = PreparedPptxRoleRun(source, run_id, metadata, tuple(batches), "")
    return replace(prepared, seal=_seal("run", (prepared.identity(), prepared.batches)))


@dataclass(frozen=True, slots=True)
class PptxRoleRunConfiguration:
    run_id: str
    preview_id: str
    actor_user_id: str
    metadata_json: str
    configuration_json: str = field(repr=False)
    seal: str = field(repr=False)


def restore_pptx_role_run(source_service, configuration):
    """Reparse the retained original outside SQLite; never deserialize a saved plan."""
    _require(type(configuration) is PptxRoleRunConfiguration)
    values = asdict(configuration)
    values.pop("seal")
    _verify("configuration", values, configuration.seal)
    config = json.loads(configuration.configuration_json)
    _require(set(config) == {"version", "policy_version", "prompt_sha256", "chunk_bytes", "context_bytes",
        "model_policy", "work_policy", "execution_policy"}
        and config["version"] == ROLE_SOURCE_PREPARATION_VERSION
        and config["policy_version"] == POLICY and config["prompt_sha256"] == PROMPT_SHA256)
    metadata = json.loads(configuration.metadata_json)
    _require(config["version"] == metadata["source_preparation_version"]
        and config["policy_version"] == metadata["policy_version"]
        and config["prompt_sha256"] == metadata["prompt_sha256"])
    model = dict(config["model_policy"])
    for key in ("provider_order", "model_ids"):
        if model[key] is not None: model[key] = tuple(model[key])
    if model["wire_models"] is not None: model["wire_models"] = tuple(tuple(pair) for pair in model["wire_models"])
    source = source_service.prepare(preview_id=configuration.preview_id,
        actor_user_id=configuration.actor_user_id, chunk_bytes=config["chunk_bytes"],
        context_bytes=config["context_bytes"], model_policy=AuthoringModelPolicy(**model),
        work_policy=AuthoringWorkPolicy(**config["work_policy"]),
        execution_policy=PreservationExecutionPolicy(**config["execution_policy"]),
        policy_version=config["policy_version"])
    try:
        run = prepare_pptx_role_run(source, max_executions=metadata["max_executions"])
        _require(run.run_id == configuration.run_id and run.metadata_json == configuration.metadata_json)
        return run
    except BaseException:
        source.close()
        raise


@dataclass(frozen=True, slots=True)
class PptxRoleAttempt:
    id: str
    run_id: str
    ordinal: int
    sequence: int
    operation_id: str
    request_sha256: str
    attempt_units: int
    started_at: int


@dataclass(frozen=True, slots=True)
class PreparedPptxRoleOutcome:
    attempt: PptxRoleAttempt
    metadata: tuple
    body: tuple[str, str, str] = field(repr=False)
    run_metadata_sha256: str
    seal: str = field(repr=False)


def prepare_pptx_role_outcome(run, attempt, *, receipt=None, status="validated", failure_code=None):
    _require(type(run) is PreparedPptxRoleRun and type(attempt) is PptxRoleAttempt)
    _verify("run", (run.identity(), run.batches), run.seal)
    policy = json.loads(run.metadata_json)
    _uuid(attempt.id)
    _uuid(attempt.operation_id)
    _require(all(type(getattr(attempt, key)) is int for key in
        ("ordinal", "sequence", "attempt_units", "started_at"))
        and 1 <= attempt.sequence <= policy["max_executions"]
        and attempt.attempt_units == policy["attempt_units"]
        and 0 <= attempt.started_at < 2**63,
        "Native attempt requires exact integer reservation and time fields.")
    _require(attempt.run_id == run.run_id and type(attempt.ordinal) is int
        and 1 <= attempt.ordinal <= len(run.batches)
        and attempt.request_sha256 == run.batches[attempt.ordinal - 1][3])
    _require(status in ("validated", "failed", "abandoned"))
    if status == "validated":
        _require(failure_code is None)
        replay_pptx_role_receipt(run.source.plan, attempt.ordinal, receipt)
    else:
        _require(failure_code in FAILURE_CODES)
        if receipt is not None:
            _require(type(receipt) is PptxRoleReceipt and receipt.validation_state == "attempt_only"
                and receipt.batch_id == run.batches[attempt.ordinal - 1][1]
                and receipt.policy_version == policy["policy_version"]
                and receipt.request_sha256 == attempt.request_sha256
                and receipt.response_sha256 == _hash(receipt.response))
    body = ("", "{}", "{}") if receipt is None else (
        receipt.response, _json(dict(receipt.model)), receipt.transport_json)
    _require(all(type(value) is str for value in body))
    _require(len(body[0].encode()) <= RESPONSE_BYTES and len(body[1].encode()) <= 4096
        and len(body[2].encode()) <= 65536)
    _require(type(json.loads(body[1])) is dict and type(json.loads(body[2])) is dict)
    metadata = (attempt.id, run.run_id, attempt.ordinal, status, failure_code,
        *tuple(value for part in body for value in (_hash(part), len(part.encode()))))
    values = (asdict(attempt), metadata, body, _hash(run.metadata_json))
    return PreparedPptxRoleOutcome(attempt, metadata, body, _hash(run.metadata_json), _seal("outcome", values))


@dataclass(frozen=True, slots=True)
class PreparedPptxRoleSnapshot:
    run_id: str
    run_metadata_sha256: str
    snapshot_id: str
    markdown: str = field(repr=False)
    manifest_json: str = field(repr=False)
    receipt_sha256s: tuple[str, ...]
    seal: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PptxRoleAuthoringCleanup:
    native_extraction: object
    deleted_run_bodies: int
    deleted_outcome_bodies: int
    deleted_wiki_bodies: int
    deleted_private_bytes: int


def _snapshot_values(prepared):
    row = asdict(prepared)
    row.pop("seal")
    return row


def prepare_pptx_role_snapshot(run, receipts):
    _require(type(run) is PreparedPptxRoleRun)
    _verify("run", (run.identity(), run.batches), run.seal)
    wiki = assemble_pptx_role_wiki(run.source.plan, receipts)
    by_id = {receipt.batch_id: receipt for receipt in receipts}
    digests = tuple(_hash(_json(asdict(by_id[b[1]]))) for b in run.batches)
    snapshot_id = str(uuid5(_NS, "snapshot:" + run.run_id))
    result = PreparedPptxRoleSnapshot(run.run_id, _hash(run.metadata_json), snapshot_id,
        wiki.markdown, wiki.manifest_json, digests, "")
    return replace(result, seal=_seal("snapshot", _snapshot_values(result)))


class PptxRoleAuthoringRepository:
    def __init__(self, source_service):
        self.source_service = source_service

    def load_configuration(self, connection, *, preview_id, actor_user_id):
        """Authorize before exposing private restart settings, under a read snapshot."""
        _connection(connection, write=True)
        current = self.source_service._base._snapshot(connection, preview_id, actor_user_id, now_ms=0)
        row = connection.execute("SELECT * FROM pptx_role_authoring_run WHERE preview_id=?", (preview_id,)).fetchone()
        _require(row is not None and row["actor_user_id"] == actor_user_id
            and row["source_fence_sha256"] == current.fence_sha256
            and _hash(row["metadata_json"]) == row["metadata_sha256"])
        body = connection.execute("SELECT configuration_json FROM pptx_role_authoring_run_body WHERE run_id=?", (row["id"],)).fetchone()
        metadata = json.loads(row["metadata_json"])
        _require(body is not None and _hash(body[0]) == metadata["configuration_sha256"]
            and len(body[0].encode()) == metadata["configuration_size_bytes"])
        result = PptxRoleRunConfiguration(row["id"], preview_id, actor_user_id, row["metadata_json"], body[0], "")
        values = asdict(result)
        values.pop("seal")
        return replace(result, seal=_seal("configuration", values))

    def cleanup_orphan_bodies(self, connection, *, artifact_id, purged_at_ms):
        """Accounting wrapper only; existing audited orphan service owns authority.

        This does not permit cleanup of published items, active previews, young
        sources or pending native work, and is not the administrator purge API.
        """
        from .native_table_artifacts import NativeTableArtifactRepository
        _connection(connection, write=True)
        _uuid(artifact_id)
        def counts():
            values = []
            for sql in (
                "SELECT count(*),coalesce(sum(length(CAST(b.configuration_json AS BLOB))),0) FROM pptx_role_authoring_run_body b JOIN pptx_role_authoring_run r ON r.id=b.run_id WHERE r.artifact_id=?",
                "SELECT count(*),coalesce(sum(length(CAST(b.response AS BLOB))+length(CAST(b.model_json AS BLOB))+length(CAST(b.transport_json AS BLOB))),0) FROM pptx_role_authoring_outcome_body b JOIN pptx_role_authoring_outcome o ON o.attempt_id=b.attempt_id JOIN pptx_role_authoring_run r ON r.id=o.run_id WHERE r.artifact_id=?",
                "SELECT count(*),coalesce(sum(length(CAST(b.content_md AS BLOB))+length(CAST(b.manifest_json AS BLOB))),0) FROM pptx_role_wiki_snapshot_body b JOIN pptx_role_wiki_snapshot s ON s.id=b.snapshot_id JOIN pptx_role_authoring_run r ON r.id=s.run_id WHERE r.artifact_id=?",
            ):
                values.append(tuple(connection.execute(sql, (artifact_id,)).fetchone()))
            return values
        before = counts()
        result = NativeTableArtifactRepository().cleanup_orphan_bodies(connection,
            artifact_id=artifact_id, purged_at_ms=purged_at_ms)
        _require(all(pair == (0, 0) for pair in counts())
            and (result.legacy.changed or not any(pair[0] for pair in before)),
            "Native private bodies survived orphan cleanup.")
        return PptxRoleAuthoringCleanup(result, *(pair[0] for pair in before), sum(pair[1] for pair in before))

    def _check(self, connection, run):
        _connection(connection, write=True)  # reads also require an explicit consistent snapshot
        _require(type(run) is PreparedPptxRoleRun)
        _verify("run", (run.identity(), run.batches), run.seal)
        self.source_service.revalidate_in_transaction(connection, run.source)

    def _bound(self, connection, run):
        self._check(connection, run)
        actual = connection.execute("SELECT * FROM pptx_role_authoring_run WHERE id=?", (run.run_id,)).fetchone()
        _require(_row(actual, _RUN_FIELDS) == run.identity())
        batches = connection.execute("SELECT * FROM pptx_role_authoring_batch WHERE run_id=? ORDER BY ordinal", (run.run_id,)).fetchall()
        _require(tuple(_row(b, _BATCH_FIELDS) for b in batches) == run.batches)
        body = connection.execute("SELECT configuration_json FROM pptx_role_authoring_run_body WHERE run_id=?", (run.run_id,)).fetchone()
        _require(body is not None and body[0] == run.source.configuration_json, "Native run configuration is missing or changed.")
        state = connection.execute("SELECT * FROM pptx_role_authoring_state WHERE run_id=?", (run.run_id,)).fetchone()
        _require(state is not None)
        return state

    def put_run(self, connection, run):
        self._check(connection, run)
        exists = connection.execute("SELECT id FROM pptx_role_authoring_run WHERE preview_id=?", (run.source.preview_id,)).fetchone()
        if exists is not None:
            _require(exists[0] == run.run_id, "Preview already belongs to another native run policy.")
            self._bound(connection, run)
            return run.run_id
        _insert(connection, "pptx_role_authoring_run", (*_RUN_FIELDS, "created_at"), (*run.identity(), db_now_ms(connection)))
        _insert(connection, "pptx_role_authoring_run_body", ("run_id", "configuration_json"), (run.run_id, run.source.configuration_json))
        _insert(connection, "pptx_role_authoring_state", ("run_id", "status", "row_version"), (run.run_id, "pending", 1))
        for batch in run.batches:
            _insert(connection, "pptx_role_authoring_batch", ("run_id", *_BATCH_FIELDS), (run.run_id, *batch))
        self._bound(connection, run)
        return run.run_id

    def start_attempt(self, connection, run, *, ordinal, operation_id):
        """Reserve once; replaying this ID is NOT permission to call the model again.

        The owning worker must fence execution. After a confirmed lost worker,
        an ambiguous call is closed as abandoned and a NEW operation reserves
        the next execution, even if no response was durably saved.
        """
        state = self._bound(connection, run)
        _uuid(operation_id)
        _require(type(ordinal) is int and 1 <= ordinal <= len(run.batches))
        prior = connection.execute("SELECT * FROM pptx_role_authoring_attempt WHERE operation_id=?", (operation_id,)).fetchone()
        if prior is not None:
            _require(prior["run_id"] == run.run_id and prior["ordinal"] == ordinal
                and prior["request_sha256"] == run.batches[ordinal - 1][3])
            return PptxRoleAttempt(*_row(prior, _ATTEMPT_FIELDS))
        _require(state["status"] == "pending", "Native run is terminal.")
        attempts = connection.execute("SELECT count(*) FROM pptx_role_authoring_attempt WHERE run_id=? AND ordinal=?", (run.run_id, ordinal)).fetchone()[0]
        units = json.loads(run.metadata_json)["attempt_units"]
        attempt = PptxRoleAttempt(str(uuid5(_NS, "attempt:" + operation_id)), run.run_id, ordinal,
            attempts + 1, operation_id, run.batches[ordinal - 1][3], units, db_now_ms(connection))
        _insert(connection, "pptx_role_authoring_attempt", _ATTEMPT_FIELDS, tuple(asdict(attempt).values()))
        return attempt

    def put_outcome(self, connection, run, prepared):
        state = self._bound(connection, run)
        _require(type(prepared) is PreparedPptxRoleOutcome)
        _verify("outcome", (asdict(prepared.attempt), prepared.metadata, prepared.body,
            prepared.run_metadata_sha256), prepared.seal)
        _require(prepared.run_metadata_sha256 == _hash(run.metadata_json))
        a = connection.execute("SELECT * FROM pptx_role_authoring_attempt WHERE id=?", (prepared.attempt.id,)).fetchone()
        _require(_row(a, _ATTEMPT_FIELDS) == tuple(asdict(prepared.attempt).values()) and a["run_id"] == run.run_id)
        row = connection.execute("SELECT * FROM pptx_role_authoring_outcome WHERE attempt_id=?", (a["id"],)).fetchone()
        if row is not None:
            body = connection.execute("SELECT * FROM pptx_role_authoring_outcome_body WHERE attempt_id=?", (a["id"],)).fetchone()
            _require(_row(row, _OUTCOME_FIELDS) == prepared.metadata and _row(body, _BODY_FIELDS) == prepared.body)
            return a["id"]
        _require(state["status"] == "pending")
        _insert(connection, "pptx_role_authoring_outcome", (*_OUTCOME_FIELDS, "completed_at"), (*prepared.metadata, db_now_ms(connection)))
        _insert(connection, "pptx_role_authoring_outcome_body", ("attempt_id", *_BODY_FIELDS), (a["id"], *prepared.body))
        return a["id"]

    def load_receipts(self, connection, run):
        """Read/hash-check in a caller snapshot; semantic/structural replay is outside it."""
        self._bound(connection, run)
        rows = connection.execute("""SELECT o.*,b.response,b.model_json,b.transport_json FROM pptx_role_authoring_outcome o
            LEFT JOIN pptx_role_authoring_outcome_body b ON b.attempt_id=o.attempt_id
            WHERE o.run_id=? AND o.status='validated' ORDER BY o.ordinal""", (run.run_id,)).fetchall()
        receipts = []
        for row in rows:
            for text_key, prefix in zip(_BODY_FIELDS, ("response", "model", "transport")):
                text = row[text_key]
                _require(type(text) is str and len(text.encode()) == row[prefix + "_size_bytes"]
                    and _hash(text) == row[prefix + "_sha256"], "Native outcome body is missing or changed.")
            model = json.loads(row["model_json"])
            _require(type(model) is dict and set(model) <= {"model_kind", "model_id", "model_used"}
                and all(type(v) is str for v in model.values()))
            batch = run.batches[row["ordinal"] - 1]
            # Match the author receipt's defined order, not JSON's sorted-key order.
            ordered = tuple((k, model[k]) for k in ("model_kind", "model_id", "model_used") if k in model)
            receipts.append(PptxRoleReceipt(batch[1], batch[3], row["response"], row["response_sha256"], ordered,
                row["transport_json"], policy_version=json.loads(run.metadata_json)["policy_version"]))
        return tuple(receipts)

    def put_snapshot(self, connection, run, prepared):
        state = self._bound(connection, run)
        _require(type(prepared) is PreparedPptxRoleSnapshot)
        _verify("snapshot", _snapshot_values(prepared), prepared.seal)
        _require(prepared.run_id == run.run_id and prepared.run_metadata_sha256 == _hash(run.metadata_json))
        receipts = self.load_receipts(connection, run)
        _require(tuple(_hash(_json(asdict(r))) for r in receipts) == prepared.receipt_sha256s
            and len(receipts) == len(run.batches), "Native snapshot lacks its exact stored validated outcomes.")
        fields = ("id", "run_id", "content_sha256", "content_size_bytes", "manifest_sha256", "manifest_size_bytes")
        values = (prepared.snapshot_id, run.run_id, _hash(prepared.markdown), len(prepared.markdown.encode()),
            _hash(prepared.manifest_json), len(prepared.manifest_json.encode()))
        prior = connection.execute("SELECT * FROM pptx_role_wiki_snapshot WHERE run_id=?", (run.run_id,)).fetchone()
        if prior is not None:
            body = connection.execute("SELECT content_md,manifest_json FROM pptx_role_wiki_snapshot_body WHERE snapshot_id=?", (prior["id"],)).fetchone()
            _require(state["status"] == "ready" and _row(prior, fields) == values
                and body is not None and tuple(body) == (prepared.markdown, prepared.manifest_json))
            return prior["id"]
        _require(state["status"] == "pending")
        now = db_now_ms(connection)
        _insert(connection, "pptx_role_wiki_snapshot", (*fields, "created_at"), (*values, now))
        _insert(connection, "pptx_role_wiki_snapshot_body", ("snapshot_id", "content_md", "manifest_json"),
            (prepared.snapshot_id, prepared.markdown, prepared.manifest_json))
        connection.execute("UPDATE pptx_role_authoring_state SET status='ready',completed_at=?,row_version=row_version+1 WHERE run_id=?", (now, run.run_id))
        return prepared.snapshot_id

    def fail_run(self, connection, run, *, failure_code):
        """Trusted service terminalization; an unfinished attempt must be reconciled first.

        Revoked/expired sources require the future audited worker reconciliation
        path, not bypassing this ordinary source-authorized storage API.
        """
        state = self._bound(connection, run)
        _require(failure_code in FAILURE_CODES)
        if state["status"] == "failed":
            _require(state["failure_code"] == failure_code)
            return
        _require(state["status"] == "pending")
        connection.execute("UPDATE pptx_role_authoring_state SET status='failed',failure_code=?,completed_at=?,row_version=row_version+1 WHERE run_id=?",
            (failure_code, db_now_ms(connection), run.run_id))
