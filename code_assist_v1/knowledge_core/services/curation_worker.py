"""Durable automatic document-curation gate for current revisions.

User-authored rescan rows dispatch a separate system decision row.  Only that
system row may apply the schema's automatic publish/quarantine transition.
Preparation reads and hashes Markdown outside the effect transaction; commit
revalidates immutable hashes and current-row fences before one atomic effect.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, db_now_ms, transaction
from ..domain import (
    ExplicitReferenceAvailability,
    SemanticIntentProvenanceKind,
)
from ..repositories.automatic_semantic_edges import AutomaticSemanticEdgeRepository
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import DurableJobRecord, JobEnqueueDraft, JobRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.semantic_graph_contracts import (
    ExplicitReferenceRecord,
    SemanticEdgeIntentRecord,
    SemanticGraphContractRepository,
    derive_semantic_lifecycle_vector,
)
from ..scope import Capability, ScopeResolutionError, ScopeResolver, SessionPrincipal
from ..worker import (
    JobExpectedStateConflictError,
    PermanentJobExecutionError,
    PreparedJobEffect,
    RegisteredJobHandler,
)
from .curation import DETERMINISTIC_INGEST_RULE_VERSION, lint_markdown_deterministic


CURATION_RESCAN_RULE_VERSION = "curation-rescan-v1"
_ID_NAMESPACE = UUID("7e178bd0-9a75-4ca5-84e6-7544aa54766d")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_CONTEXT_KEYS = frozenset(
    {
        "actor_row_version",
        "actor_scope_epoch",
        "actor_user_id",
        "closure_generation",
        "closure_proof_sha256",
        "operation_id",
        "ordinal",
        "organization_snapshot_version",
        "source_space_id",
        "target_item_id",
        "target_item_row_version",
        "target_revision_id",
        "target_space_id",
    }
)


def _id(*parts: object) -> str:
    return str(uuid5(_ID_NAMESPACE, ":".join(str(part) for part in parts)))


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _DispatchDecision:
    expected_state: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PreparedDecision:
    item_id: str
    revision_id: str
    space_id: str
    item_row_version: int
    title: str
    markdown: str
    raw_sha256: str
    normalized_sha256: str
    content_size_bytes: int
    updated_by: str
    outcome: str
    finding_code: str
    finding_severity: str
    candidate_item_id: str | None
    candidate_revision_id: str | None


class CanonicalCurationRescanHandler:
    """Exact v1 curation handler with explicit user-to-system dispatch."""

    __slots__ = ("_audit", "_factory", "_graphs", "_jobs", "_outbox", "_scope")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver | None = None,
        jobs: JobRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        graphs: SemanticGraphContractRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("CanonicalCurationRescanHandler requires a ConnectionFactory.")
        self._factory = factory
        self._jobs = jobs or JobRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._graphs = graphs or SemanticGraphContractRepository()
        if scope_resolver is not None and not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("scope_resolver must be a ScopeResolver.")
        self._scope = scope_resolver

    def registered_handler(self) -> RegisteredJobHandler:
        return RegisteredJobHandler.build(
            job_type="curation.rescan",
            handler_version="v1",
            actor_kind="user",
            accept_system_actor=True,
            required_capability=Capability.EDIT,
            validate_input=self.validate_input,
            prepare=self.prepare,
            revalidate_expected_state=self.revalidate_expected_state,
            commit_effect=self.commit_effect,
            converge_terminal=self.converge_terminal,
        )

    @staticmethod
    def validate_input(
        input_ref: Mapping[str, object], expected_state: Mapping[str, object]
    ) -> None:
        del expected_state
        if set(input_ref) != {"item_id", "revision_id", "rule_version"}:
            raise JobExpectedStateConflictError("The curation input shape is invalid.")
        if (
            not isinstance(input_ref.get("item_id"), str)
            or _UUID_RE.fullmatch(str(input_ref["item_id"])) is None
            or not isinstance(input_ref.get("revision_id"), str)
            or _UUID_RE.fullmatch(str(input_ref["revision_id"])) is None
            or input_ref.get("rule_version") != CURATION_RESCAN_RULE_VERSION
        ):
            raise JobExpectedStateConflictError("The curation input binding is invalid.")

    def prepare(self, job: DurableJobRecord) -> PreparedJobEffect:
        self.validate_input(job.input_ref, job.expected_state)
        if job.actor_kind == "user":
            system_job_id = _id("decision-job", job.input_ref["item_id"], job.input_ref["revision_id"])
            return PreparedJobEffect(
                result={
                    "schema_version": 1,
                    "status": "system_decision_dispatched",
                    "system_job_id": system_job_id,
                },
                payload=_DispatchDecision(dict(job.expected_state)),
            )
        if job.actor_kind != "system" or job.space_id is None:
            raise PermanentJobExecutionError("The curation decision actor is invalid.")
        prepared = self._prepare_system_decision(job)
        return PreparedJobEffect(
            result={
                "finding_code": prepared.finding_code,
                "schema_version": 1,
                "status": prepared.outcome,
            },
            payload=prepared,
        )

    def _prepare_system_decision(self, job: DurableJobRecord) -> _PreparedDecision:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT i.space_id,i.current_revision_id,i.curation_status,
                              i.lifecycle_status,i.row_version,i.updated_by,
                              r.title,r.raw_content_sha256,
                              r.normalized_content_sha256,r.content_size_bytes,
                              r.normalizer_version,b.content_md,b.content_blob_hash,
                              b.content_size_bytes AS body_size,b.content_sha256
                         FROM knowledge_item i
                         JOIN knowledge_revision r
                           ON r.id=i.current_revision_id AND r.item_id=i.id
                         JOIN knowledge_revision_body b
                           ON b.revision_id=r.id AND b.item_id=i.id
                        WHERE i.id=? AND i.space_id=?""",
                    (str(job.input_ref["item_id"]), job.space_id),
                ).fetchone()
                if (
                    row is None
                    or row["current_revision_id"] != job.input_ref["revision_id"]
                    or row["curation_status"] != "provisional"
                    or row["lifecycle_status"] != "active"
                    or row["normalizer_version"] != "markdown_lf_v1"
                    or row["content_blob_hash"] is not None
                    or not isinstance(row["content_md"], str)
                ):
                    raise JobExpectedStateConflictError(
                        "The curation revision is no longer provisional and current."
                    )
                markdown = str(row["content_md"])
                encoded = markdown.encode("utf-8")
                raw_sha = hashlib.sha256(encoded).hexdigest()
                normalized_sha = hashlib.sha256(
                    markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
                ).hexdigest()
                if (
                    len(encoded) != row["content_size_bytes"]
                    or row["body_size"] != row["content_size_bytes"]
                    or row["content_sha256"] != raw_sha
                    or row["raw_content_sha256"] != raw_sha
                    or row["normalized_content_sha256"] != normalized_sha
                ):
                    raise PermanentJobExecutionError(
                        "The curation revision body evidence is inconsistent."
                    )
                lint = lint_markdown_deterministic(markdown)
                blocker = next(
                    (finding for finding in lint if finding.severity in {"blocker", "error"}),
                    None,
                )
                candidates = connection.execute(
                    """SELECT i.id,i.current_revision_id
                         FROM knowledge_item i JOIN knowledge_revision r
                           ON r.id=i.current_revision_id AND r.item_id=i.id
                        WHERE i.space_id=? AND i.id<>?
                          AND i.lifecycle_status='active'
                          AND i.curation_status='published'
                          AND r.normalized_content_sha256=?
                        ORDER BY i.id LIMIT 2""",
                    (job.space_id, job.input_ref["item_id"], normalized_sha),
                ).fetchall()
                if blocker is not None:
                    outcome = "quarantined"
                    finding_code = blocker.code
                    severity = blocker.severity
                    candidate_item = candidate_revision = None
                elif candidates:
                    outcome = "pending_review"
                    finding_code = "normalized_near_duplicate"
                    severity = "warning"
                    candidate_item = str(candidates[0]["id"])
                    candidate_revision = str(candidates[0]["current_revision_id"])
                else:
                    outcome = "published"
                    finding_code = "curation_clear"
                    severity = "info"
                    candidate_item = candidate_revision = None
                return _PreparedDecision(
                    item_id=str(job.input_ref["item_id"]),
                    revision_id=str(job.input_ref["revision_id"]),
                    space_id=job.space_id,
                    item_row_version=int(row["row_version"]),
                    title=str(row["title"]),
                    markdown=markdown,
                    raw_sha256=raw_sha,
                    normalized_sha256=normalized_sha,
                    content_size_bytes=len(encoded),
                    updated_by=str(row["updated_by"]),
                    outcome=outcome,
                    finding_code=finding_code,
                    finding_severity=severity,
                    candidate_item_id=candidate_item,
                    candidate_revision_id=candidate_revision,
                )
        finally:
            connection.close()

    def revalidate_expected_state(
        self, connection: ManagedConnection, job: DurableJobRecord
    ) -> None:
        self.validate_input(job.input_ref, job.expected_state)
        if job.actor_kind == "system":
            row = connection.execute(
                """SELECT current_revision_id,curation_status,lifecycle_status
                     FROM knowledge_item WHERE id=? AND space_id=?""",
                (str(job.input_ref["item_id"]), job.space_id),
            ).fetchone()
            if (
                row is None
                or row["current_revision_id"] != job.input_ref["revision_id"]
                or row["curation_status"] != "provisional"
                or row["lifecycle_status"] != "active"
            ):
                raise JobExpectedStateConflictError(
                    "The curation decision fence is stale."
                )

    def commit_effect(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        prepared: PreparedJobEffect,
    ) -> None:
        if job.actor_kind == "user":
            if not isinstance(prepared.payload, _DispatchDecision) or job.space_id is None:
                raise PermanentJobExecutionError("The curation dispatch payload is invalid.")
            now_ms = db_now_ms(connection)
            system_job_id = _id("decision-job", job.input_ref["item_id"], job.input_ref["revision_id"])
            self._jobs.enqueue(
                connection,
                JobEnqueueDraft(
                    job_id=system_job_id,
                    dedup_id=None,
                    operation_id=None,
                    actor_kind="system",
                    actor_user_id=None,
                    space_id=job.space_id,
                    job_type="curation.rescan",
                    handler_version="v1",
                    effect_key=f"curation-decision:{job.input_ref['item_id']}:{job.input_ref['revision_id']}",
                    input_ref=dict(job.input_ref),
                    expected_state=dict(prepared.payload.expected_state),
                    authorization_snapshot={"actor_kind": "system", "schema_version": 1},
                    authorization_scope_epoch=None,
                    authorization_part_id=None,
                    authorization_capabilities=(),
                    max_attempts=8,
                    cancellable=False,
                    available_at_ms=now_ms,
                    created_at_ms=None,
                ),
            )
            return
        if not isinstance(prepared.payload, _PreparedDecision):
            raise PermanentJobExecutionError("The curation decision payload is invalid.")
        self._commit_system_decision(connection, job, prepared.payload)

    def _insert_finding(
        self,
        connection: ManagedConnection,
        *,
        job: DurableJobRecord,
        prepared: _PreparedDecision,
        now_ms: int,
    ) -> str:
        finding_id = _id("finding", job.id, prepared.outcome)
        pending = prepared.outcome == "pending_review"
        if pending:
            kind, tier, severity, mode, reviewer, visibility, action = (
                "near_duplicate", "T2", "warning", "human", "space_editor",
                "agent_hidden", "review_duplicate",
            )
        elif prepared.outcome == "quarantined":
            kind, tier, severity, mode, reviewer, visibility, action = (
                "lint", "T0", prepared.finding_severity, "automatic", "none",
                "publication_blocked", "quarantine",
            )
        else:
            kind, tier, severity, mode, reviewer, visibility, action = (
                "curation_clear", "T1", "info", "automatic", "none", "none", "publish",
            )
        columns = (
            "id,space_id,finding_kind,risk_tier,severity,status,decision_mode,"
            "required_reviewer,visibility_effect,proposed_action,source_item_id,"
            "source_revision_id,target_item_id,target_revision_id,detector_name,"
            "detector_version,rule_version,model_id,prompt_version,confidence,"
            "evidence_json,proposed_action_json,reviewed_by,reviewed_at,review_reason,"
            "applied_operation_id,applied_dedup_id,applied_job_id,applied_by,"
            "applied_at,last_application_error_json,created_actor_kind,created_by,"
            "created_at,updated_at,row_version"
        )
        values = (
            finding_id, prepared.space_id, kind, tier, severity,
            "pending" if pending else "applied", mode, reviewer, visibility, action,
            prepared.item_id, prepared.revision_id, prepared.candidate_item_id,
            prepared.candidate_revision_id, "canonical-document-curation",
            "1", CURATION_RESCAN_RULE_VERSION, None, None, 1.0,
            _canonical({
                "candidate_item_id": prepared.candidate_item_id,
                "candidate_revision_id": prepared.candidate_revision_id,
                "content_sha256": prepared.raw_sha256,
                "finding_code": prepared.finding_code,
                "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
                "schema_version": 1,
            }),
            _canonical({"action": action, "schema_version": 1}),
            None, None, None, None, None, None if pending else job.id, None,
            None if pending else now_ms, None, "system", None, now_ms, now_ms, 1,
        )
        connection.execute(
            f"INSERT INTO curation_finding({columns}) VALUES ({','.join('?' for _ in values)})",
            values,
        )
        return finding_id

    def _revalidate_reference_contexts(
        self,
        connection: ManagedConnection,
        *,
        job: DurableJobRecord,
        prepared: _PreparedDecision,
    ) -> tuple[dict[str, object], ...]:
        raw_contexts = job.expected_state.get("explicit_references", [])
        if not isinstance(raw_contexts, list) or len(raw_contexts) > 1000:
            raise JobExpectedStateConflictError(
                "The explicit reference authorization fence is stale."
            )
        if not raw_contexts:
            return ()
        if self._scope is None:
            raise PermanentJobExecutionError(
                "Explicit-reference curation requires the canonical scope resolver."
            )

        contexts: list[dict[str, object]] = []
        for expected_ordinal, raw_context in enumerate(raw_contexts, start=1):
            if not isinstance(raw_context, dict) or set(raw_context) != _REFERENCE_CONTEXT_KEYS:
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
            context = dict(raw_context)
            integer_fields = (
                "actor_row_version",
                "actor_scope_epoch",
                "closure_generation",
                "ordinal",
                "target_item_row_version",
            )
            if any(
                isinstance(context[field], bool)
                or not isinstance(context[field], int)
                or int(context[field]) < 1
                for field in integer_fields
            ):
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
            if context["ordinal"] != expected_ordinal:
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
            uuid_fields = (
                "operation_id",
                "source_space_id",
                "target_item_id",
                "target_revision_id",
                "target_space_id",
            )
            if any(
                not isinstance(context[field], str)
                or _UUID_RE.fullmatch(str(context[field])) is None
                for field in uuid_fields
            ):
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
            actor_user_id = context["actor_user_id"]
            if (
                not isinstance(actor_user_id, str)
                or not 1 <= len(actor_user_id) <= 64
                or actor_user_id != actor_user_id.strip().lower()
                or re.fullmatch(r"[a-z0-9._-]+", actor_user_id) is None
                or not isinstance(context["organization_snapshot_version"], str)
                or not str(context["organization_snapshot_version"]).isdigit()
                or int(str(context["organization_snapshot_version"])) < 1
                or not isinstance(context["closure_proof_sha256"], str)
                or _SHA256_RE.fullmatch(str(context["closure_proof_sha256"])) is None
                or context["source_space_id"] != prepared.space_id
                or context["target_item_id"] == prepared.item_id
            ):
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
            contexts.append(context)

        first = contexts[0]
        common_fields = (
            "actor_row_version",
            "actor_scope_epoch",
            "actor_user_id",
            "closure_generation",
            "closure_proof_sha256",
            "operation_id",
            "organization_snapshot_version",
            "source_space_id",
        )
        if any(
            any(context[field] != first[field] for field in common_fields)
            for context in contexts[1:]
        ) or any(
            job.expected_state.get(field) != first[field]
            for field in ("actor_scope_epoch", "actor_user_id", "operation_id")
        ):
            raise JobExpectedStateConflictError(
                "The explicit reference authorization fence is stale."
            )

        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=str(first["actor_user_id"]),
                    captured_scope_epoch=int(first["actor_scope_epoch"]),
                ),
                prepared.space_id,
                Capability.READ,
            )
        except ScopeResolutionError as exc:
            raise JobExpectedStateConflictError(
                "The explicit reference authorization fence is stale."
            ) from exc
        plan = resolved.access_plan
        if plan is None or (
            plan.actor_user_id != first["actor_user_id"]
            or plan.scope_epoch != first["actor_scope_epoch"]
            or plan.actor_row_version != first["actor_row_version"]
            or plan.selected_view.id != prepared.space_id
            or plan.corpus_closure.generation != first["closure_generation"]
            or plan.corpus_closure.sha256 != first["closure_proof_sha256"]
            or str(plan.corpus_closure.organization_snapshot_version)
            != first["organization_snapshot_version"]
        ):
            raise JobExpectedStateConflictError(
                "The explicit reference authorization fence is stale."
            )

        source_ids = {
            descriptor.source_space_id
            for descriptor in plan.corpus_closure.sources
            if Capability.READ in descriptor.capabilities
        }
        overlay_ids = {
            (overlay.source_space_id, overlay.source_item_id, overlay.source_revision_id)
            for overlay in plan.corpus_closure.distribution_overlays
            if Capability.READ in overlay.capabilities
        }
        for context in contexts:
            target = connection.execute(
                """SELECT i.space_id,i.current_revision_id,i.row_version,
                          i.lifecycle_status,i.curation_status
                     FROM knowledge_item i JOIN knowledge_space s ON s.id=i.space_id
                    WHERE i.id=? AND s.status='active'""",
                (str(context["target_item_id"]),),
            ).fetchone()
            identity = (
                str(context["target_space_id"]),
                str(context["target_item_id"]),
                str(context["target_revision_id"]),
            )
            if (
                target is None
                or target["space_id"] != context["target_space_id"]
                or target["current_revision_id"] != context["target_revision_id"]
                or target["row_version"] != context["target_item_row_version"]
                or target["lifecycle_status"] != "active"
                or target["curation_status"] != "published"
                or (identity[0] not in source_ids and identity not in overlay_ids)
            ):
                raise JobExpectedStateConflictError(
                    "The explicit reference authorization fence is stale."
                )
        return tuple(contexts)

    def _append_explicit_references(
        self,
        connection: ManagedConnection,
        *,
        prepared: _PreparedDecision,
        contexts: tuple[dict[str, object], ...],
        audit_event_id: int,
        outbox_event_id: int,
        now_ms: int,
    ) -> None:
        for context in contexts:
            ordinal = int(context["ordinal"])
            effect_key = f"explicit-reference:{prepared.revision_id}:{ordinal}"
            self._graphs.append_reference(
                connection,
                ExplicitReferenceRecord(
                    id=_id("explicit-reference", prepared.revision_id, ordinal),
                    source_space_id=prepared.space_id,
                    source_item_id=prepared.item_id,
                    source_revision_id=prepared.revision_id,
                    ordinal=ordinal,
                    syntax_version="item-ref-v1",
                    target_space_id=str(context["target_space_id"]),
                    target_item_id=str(context["target_item_id"]),
                    target_revision_id=str(context["target_revision_id"]),
                    closure_proof_sha256=str(context["closure_proof_sha256"]),
                    actor_user_id=str(context["actor_user_id"]),
                    operation_id=str(context["operation_id"]),
                    effect_key=effect_key,
                    audit_event_id=audit_event_id,
                    outbox_event_id=outbox_event_id,
                    availability_status=ExplicitReferenceAvailability.AVAILABLE,
                    unavailable_reason=None,
                    created_at=now_ms,
                    updated_at=now_ms,
                    row_version=1,
                ),
            )

    def _commit_system_decision(
        self,
        connection: ManagedConnection,
        job: DurableJobRecord,
        prepared: _PreparedDecision,
    ) -> None:
        row = connection.execute(
            """SELECT i.current_revision_id,i.curation_status,i.lifecycle_status,
                      i.row_version,i.updated_by,r.raw_content_sha256,
                      r.normalized_content_sha256,r.content_size_bytes,
                      b.content_sha256,b.content_size_bytes AS body_size,
                      CASE WHEN b.content_md IS NULL THEN 0 ELSE 1 END AS inline_body,
                      length(CAST(b.content_md AS BLOB)) AS inline_size
                 FROM knowledge_item i JOIN knowledge_revision r
                   ON r.id=i.current_revision_id AND r.item_id=i.id
                 JOIN knowledge_revision_body b
                   ON b.revision_id=r.id AND b.item_id=i.id
                WHERE i.id=? AND i.space_id=?""",
            (prepared.item_id, prepared.space_id),
        ).fetchone()
        if row is None or (
            row["current_revision_id"], row["curation_status"], row["lifecycle_status"],
            row["row_version"], row["updated_by"], row["raw_content_sha256"],
            row["normalized_content_sha256"], row["content_size_bytes"],
            row["content_sha256"], row["body_size"], row["inline_body"],
            row["inline_size"],
        ) != (
            prepared.revision_id, "provisional", "active", prepared.item_row_version,
            prepared.updated_by, prepared.raw_sha256, prepared.normalized_sha256,
            prepared.content_size_bytes, prepared.raw_sha256,
            prepared.content_size_bytes, 1, prepared.content_size_bytes,
        ):
            raise JobExpectedStateConflictError("The prepared curation evidence is stale.")
        reference_contexts = self._revalidate_reference_contexts(
            connection,
            job=job,
            prepared=prepared,
        )
        now_ms = db_now_ms(connection)
        finding_id = self._insert_finding(
            connection, job=job, prepared=prepared, now_ms=now_ms
        )
        if prepared.outcome == "pending_review":
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=f"curation:{job.id}:pending-review",
                    space_id=prepared.space_id,
                    aggregate_type="curation_finding",
                    aggregate_id=finding_id,
                    aggregate_version=1,
                    event_type="curation.finding.pending",
                    payload={
                        "finding_id": finding_id,
                        "item_id": prepared.item_id,
                        "revision_id": prepared.revision_id,
                        "schema_version": 1,
                    },
                    available_at_ms=now_ms,
                ),
            )
            return

        transition_id = _id("transition", job.id, prepared.outcome)
        next_version = prepared.item_row_version + 1
        connection.execute(
            """INSERT INTO item_curation_transition(
                 id,item_id,space_id,transition_kind,from_status,to_status,
                 base_revision_id,result_revision_id,finding_id,rescan_job_id,
                 decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
                 result_item_row_version,reason,created_at
               ) VALUES (?, ?,?,'finding_decision','provisional',?, ?,?,?,NULL,
                         ?,NULL,NULL,'system',NULL,?,?,?)""",
            (
                transition_id, prepared.item_id, prepared.space_id, prepared.outcome,
                prepared.revision_id, prepared.revision_id, finding_id, job.id,
                next_version, "Automatic deterministic document curation.", now_ms,
            ),
        )
        cursor = connection.execute(
            """UPDATE knowledge_item SET curation_status=?,
                      last_curation_transition_id=?,updated_by=?,updated_at=?,
                      row_version=row_version+1
                 WHERE id=? AND space_id=? AND current_revision_id=?
                   AND curation_status='provisional' AND lifecycle_status='active'
                   AND row_version=?""",
            (
                prepared.outcome, transition_id, prepared.updated_by, now_ms,
                prepared.item_id, prepared.space_id, prepared.revision_id,
                prepared.item_row_version,
            ),
        )
        if cursor.rowcount != 1:
            raise JobExpectedStateConflictError("The curation item changed before transition.")
        if prepared.outcome == "quarantined":
            request_id = _id("audit-request", job.id)
            self._audit.append(
                connection,
                AuditEventDraft(
                    event_uuid=_id("audit", job.id),
                    occurred_at_ms=now_ms,
                    request_id=request_id,
                    operation_id=None,
                    action="curation.automatic_quarantine",
                    outcome="quarantined",
                    actor_kind="system",
                    actor_user_id=None,
                    actor_display_name="Second Brain Curation",
                    actor_role_snapshot="system",
                    space_id=prepared.space_id,
                    item_id=prepared.item_id,
                    base_revision_id=prepared.revision_id,
                    new_revision_id=prepared.revision_id,
                    details={
                        "decision_job_id": job.id,
                        "finding_id": finding_id,
                        "schema_version": 1,
                        "transition_id": transition_id,
                    },
                ),
            )
            self._outbox.append(
                connection,
                OutboxEventDraft(
                    logical_key=f"curation:{job.id}:quarantined",
                    space_id=prepared.space_id,
                    aggregate_type="knowledge_item",
                    aggregate_id=prepared.item_id,
                    aggregate_version=next_version,
                    event_type="knowledge.item.quarantined",
                    payload={
                        "finding_id": finding_id,
                        "item_id": prepared.item_id,
                        "revision_id": prepared.revision_id,
                        "schema_version": 1,
                        "transition_id": transition_id,
                    },
                    available_at_ms=now_ms,
                ),
            )
            return
        connection.execute(
            """INSERT INTO search_document(
                 item_id,space_id,revision_id,title,content_md,updated_at
               ) VALUES (?,?,?,?,?,?)""",
            (
                prepared.item_id, prepared.space_id, prepared.revision_id,
                prepared.title, prepared.markdown, now_ms,
            ),
        )
        space_cursor = connection.execute(
            """UPDATE knowledge_space SET generation=generation+1,
                      updated_at=?,row_version=row_version+1
                 WHERE id=? AND status='active'""",
            (now_ms, prepared.space_id),
        )
        if space_cursor.rowcount != 1:
            raise JobExpectedStateConflictError("The curation space changed before release.")
        request_id = _id("audit-request", job.id)
        audit = self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_id("audit", job.id),
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=None,
                action="curation.automatic_release",
                outcome="committed",
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="Second Brain Curation",
                actor_role_snapshot="system",
                space_id=prepared.space_id,
                item_id=prepared.item_id,
                base_revision_id=prepared.revision_id,
                new_revision_id=prepared.revision_id,
                details={
                    "decision_job_id": job.id,
                    "finding_id": finding_id,
                    "schema_version": 1,
                    "transition_id": transition_id,
                },
            ),
        )
        outbox = self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"curation:{job.id}:published",
                space_id=prepared.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=prepared.item_id,
                aggregate_version=next_version,
                event_type="knowledge.item.published",
                payload={
                    "item_id": prepared.item_id,
                    "revision_id": prepared.revision_id,
                    "schema_version": 1,
                    "transition_id": transition_id,
                },
                available_at_ms=now_ms,
            ),
        )
        self._append_explicit_references(
            connection,
            prepared=prepared,
            contexts=reference_contexts,
            audit_event_id=audit.id,
            outbox_event_id=outbox.id,
            now_ms=now_ms,
        )
        self._append_semantic_intents(
            connection,
            prepared=prepared,
            transition_id=transition_id,
            audit_event_id=audit.id,
            outbox_event_id=outbox.id,
            now_ms=now_ms,
        )

    def _append_semantic_intents(
        self,
        connection: ManagedConnection,
        *,
        prepared: _PreparedDecision,
        transition_id: str,
        audit_event_id: int,
        outbox_event_id: int,
        now_ms: int,
    ) -> None:
        lifecycle = derive_semantic_lifecycle_vector(
            connection,
            source_space_id=prepared.space_id,
            source_item_id=prepared.item_id,
        )
        source = connection.execute(
            """SELECT s.generation,s.owner_org_unit_id,o.row_version
                 FROM knowledge_space s LEFT JOIN org_unit o ON o.id=s.owner_org_unit_id
                WHERE s.id=? AND s.status='active'""",
            (prepared.space_id,),
        ).fetchone()
        if source is None:
            raise JobExpectedStateConflictError("The semantic source space is unavailable.")
        # Publication and later baseline/reseed must enumerate the same homes.
        # In particular a higher-native source has an own-home aggregate job
        # for descendants as well as its local job for same-space native peers.
        targets = AutomaticSemanticEdgeRepository().list_targets(
            connection, source_space_id=prepared.space_id,
        )
        source_generation = int(source["generation"])
        for target in targets:
            target_kind = target.target_kind
            target_space = target.target_space_id
            target_org = target.target_org_unit_id
            target_generation = target.target_space_generation
            org_generation = target.organization_generation
            proof = _sha_json(
                {
                    "detector_generation": 1,
                    "organization_generation": org_generation,
                    "source_space_id": prepared.space_id,
                    "source_space_generation": source_generation,
                    "target_kind": target_kind.value,
                    "target_space_generation": target_generation,
                    "target_space_id": target_space,
                }
            )
            effect_key = (
                f"semantic-intent:publication:{prepared.revision_id}:"
                f"{target_kind.value}:{target_space}:1"
            )
            self._graphs.append_intent(
                connection,
                SemanticEdgeIntentRecord(
                    id=_id("semantic-intent", effect_key),
                    effect_key=effect_key,
                    source_space_id=prepared.space_id,
                    source_item_id=prepared.item_id,
                    source_revision_id=prepared.revision_id,
                    source_body_sha256=prepared.normalized_sha256,
                    target_kind=target_kind,
                    target_space_id=target_space,
                    target_org_unit_id=target_org,
                    detector_generation=1,
                    source_revision_row_version=1,
                    source_space_generation=source_generation,
                    target_space_generation=target_generation,
                    organization_generation=org_generation,
                    closure_proof_sha256=proof,
                    item_lifecycle_generation=lifecycle.item_lifecycle_generation,
                    wiki_lifecycle_generation=lifecycle.wiki_lifecycle_generation,
                    latest_episode_id=lifecycle.latest_episode_id,
                    latest_episode_no=lifecycle.latest_episode_no,
                    latest_episode_restored_at=lifecycle.latest_episode_restored_at,
                    provenance_kind=SemanticIntentProvenanceKind.PUBLICATION,
                    publication_transition_id=transition_id,
                    publication_audit_event_id=audit_event_id,
                    publication_outbox_event_id=outbox_event_id,
                    seed_run_id=None,
                    seed_snapshot_high_water=None,
                    seed_cursor_updated_at=None,
                    seed_cursor_revision_id=None,
                    organization_event_audit_id=None,
                    detector_rollout_audit_id=None,
                    lifecycle_restore_audit_id=None,
                    created_at=now_ms,
                ),
            )

    @staticmethod
    def converge_terminal(connection: ManagedConnection, job: DurableJobRecord) -> None:
        del connection, job


__all__ = ["CURATION_RESCAN_RULE_VERSION", "CanonicalCurationRescanHandler"]
