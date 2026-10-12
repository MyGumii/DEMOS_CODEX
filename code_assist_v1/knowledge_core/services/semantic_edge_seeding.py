"""Restartable v10 semantic-intent seed runs and intent materialization."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID, uuid5

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..domain import (
    FeatureFlags,
    SemanticIntentProvenanceKind,
    SemanticIntentTargetKind,
)
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.automatic_semantic_edges import (
    AutomaticSemanticEdgeRepository,
    SEMANTIC_SEED_BATCH_LIMIT,
    SemanticIntentTarget,
    SemanticSeedSource,
)
from ..repositories.jobs import JobEnqueueDraft
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.semantic_graph_contracts import (
    SemanticEdgeIntentRecord,
    SemanticEdgeIntentSeedRunRecord,
    SemanticGraphContractRepository,
    derive_semantic_lifecycle_vector,
)
from ..worker import DurableWorkKernel


SEMANTIC_EDGE_LOCAL_HANDLER_VERSION = "automatic-edge-local-v1"
SEMANTIC_EDGE_AGGREGATE_HANDLER_VERSION = "automatic-edge-aggregate-v1"
SEMANTIC_EDGE_JOB_TYPE = "semantic_edge.evaluate"
SEMANTIC_DETECTOR_GENERATION = 1

_SEED_NAMESPACE = UUID("b26d1819-f942-4f6d-98a3-04f9b64cae8d")
_AUDIT_NAMESPACE = UUID("0e25bb52-94d5-447f-8b1a-5bc8ca381f2d")
_JOB_NAMESPACE = UUID("283f64d7-60a9-41fc-8c9b-0d65adb0a3d4")
_INTENT_NAMESPACE = UUID("da50fc30-5d2a-4fe7-b7a2-1e3704780dca")


class SemanticEdgeSeedError(KnowledgeCoreError):
    code = "SEMANTIC_EDGE_SEED_ERROR"


class SemanticEdgeBaselineIncompleteError(SemanticEdgeSeedError):
    code = "SEMANTIC_EDGE_BASELINE_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class CreateSemanticSeedRunCommand:
    effect_key: str
    cause_kind: SemanticIntentProvenanceKind
    scope_kind: str
    scope_id: str | None
    detector_generation: int = SEMANTIC_DETECTOR_GENERATION
    previous_detector_generation: int | None = None
    old_ancestor_space_id: str | None = None
    new_ancestor_space_id: str | None = None
    organization_event_audit_id: int | None = None
    detector_rollout_audit_id: int | None = None
    lifecycle_restore_audit_id: int | None = None


@dataclass(frozen=True, slots=True)
class SemanticSeedBatchResult:
    seed_run_id: str
    state: str
    scanned_count: int
    intent_count: int
    batch_scanned_count: int
    batch_intent_count: int
    cursor_updated_at: int
    cursor_revision_id: str | None


@dataclass(frozen=True, slots=True)
class SemanticIntentMaterializationResult:
    selected_count: int
    inserted_count: int
    replayed_count: int


@dataclass(frozen=True, slots=True)
class SemanticSeedTriggerResult:
    selected_event_count: int
    created_run_count: int
    replayed_run_count: int


@dataclass(frozen=True, slots=True)
class RolloutSemanticDetectorCommand:
    previous_detector_generation: int
    detector_generation: int
    request_id: str


@dataclass(frozen=True, slots=True)
class SemanticDetectorRolloutResult:
    audit_event_id: int
    previous_detector_generation: int
    detector_generation: int


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _id(namespace: UUID, *parts: object) -> str:
    return str(uuid5(namespace, "\x1f".join(str(part) for part in parts)))


def _intent_effect_key(
    source: SemanticSeedSource,
    target: SemanticIntentTarget,
    *,
    detector_generation: int,
    item_lifecycle_generation: int,
    wiki_lifecycle_generation: int,
) -> str:
    return (
        f"semantic-intent:{source.source_revision_id}:{target.target_kind.value}:"
        f"{target.target_space_id}:d{detector_generation}:"
        f"o{target.organization_generation}:i{item_lifecycle_generation}:"
        f"w{wiki_lifecycle_generation}"
    )


class SemanticIntentSeedService:
    """Create and drain one bounded, restartable seed-run state machine."""

    __slots__ = ("_audit", "_auto", "_factory", "_graphs", "_outbox")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        automatic: AutomaticSemanticEdgeRepository | None = None,
        graphs: SemanticGraphContractRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SemanticIntentSeedService requires a ConnectionFactory.")
        self._factory = factory
        self._auto = automatic or AutomaticSemanticEdgeRepository()
        self._graphs = graphs or SemanticGraphContractRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    def create(
        self, command: CreateSemanticSeedRunCommand
    ) -> SemanticEdgeIntentSeedRunRecord:
        if not isinstance(command, CreateSemanticSeedRunCommand):
            raise TypeError("create requires a CreateSemanticSeedRunCommand.")
        if command.cause_kind is SemanticIntentProvenanceKind.PUBLICATION:
            raise SemanticEdgeSeedError("Publication is not a seed-run cause.")
        connection = self._factory.open()
        try:
            with transaction(connection):
                replay = self._graphs.get_seed_run_by_effect_key(
                    connection, command.effect_key
                )
                if replay is not None:
                    return replay
                self._validate_cause_event(connection, command)
                domain_high_water, audit_high_water = self._auto.snapshot_high_water(
                    connection,
                    scope_kind=command.scope_kind,
                    scope_id=command.scope_id,
                )
                now_ms = db_now_ms(connection)
                seed_run_id = _id(_SEED_NAMESPACE, command.effect_key)
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_id(_AUDIT_NAMESPACE, command.effect_key, "create"),
                        occurred_at_ms=now_ms,
                        request_id=_id(_AUDIT_NAMESPACE, command.effect_key, "request"),
                        operation_id=None,
                        action="semantic_edge.seed.create",
                        outcome="committed",
                        actor_kind="system",
                        actor_user_id=None,
                        actor_display_name="Second Brain Semantic Seeder",
                        actor_role_snapshot="system",
                        space_id=(
                            command.scope_id
                            if command.scope_kind == "wiki"
                            else command.new_ancestor_space_id
                        ),
                        item_id=command.scope_id if command.scope_kind == "item" else None,
                        details={
                            "cause_kind": command.cause_kind.value,
                            "detector_generation": command.detector_generation,
                            "schema_version": 1,
                            "seed_run_id": seed_run_id,
                        },
                    ),
                )
                event = self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=f"semantic-seed:{seed_run_id}:created",
                        space_id=(
                            command.scope_id
                            if command.scope_kind == "wiki"
                            else command.new_ancestor_space_id
                        ),
                        aggregate_type="semantic_edge_seed_run",
                        aggregate_id=seed_run_id,
                        aggregate_version=1,
                        event_type="semantic.edge.seed.created",
                        payload={
                            "cause_kind": command.cause_kind.value,
                            "detector_generation": command.detector_generation,
                            "schema_version": 1,
                            "seed_run_id": seed_run_id,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                record = SemanticEdgeIntentSeedRunRecord(
                    id=seed_run_id,
                    effect_key=command.effect_key,
                    cause_kind=command.cause_kind,
                    scope_kind=command.scope_kind,
                    scope_id=command.scope_id,
                    detector_generation=command.detector_generation,
                    previous_detector_generation=command.previous_detector_generation,
                    old_ancestor_space_id=command.old_ancestor_space_id,
                    new_ancestor_space_id=command.new_ancestor_space_id,
                    organization_event_audit_id=command.organization_event_audit_id,
                    detector_rollout_audit_id=command.detector_rollout_audit_id,
                    lifecycle_restore_audit_id=command.lifecycle_restore_audit_id,
                    snapshot_domain_high_water=domain_high_water,
                    snapshot_audit_high_water=audit_high_water,
                    cursor_updated_at=0,
                    cursor_revision_id=None,
                    state="pending",
                    fence=1,
                    scanned_count=0,
                    intent_count=0,
                    result_sha256=None,
                    error_json=None,
                    created_audit_event_id=audit.id,
                    created_outbox_event_id=event.id,
                    created_at=now_ms,
                    updated_at=now_ms,
                    completed_at=None,
                    row_version=1,
                )
                return self._graphs.append_seed_run(connection, record)
        finally:
            connection.close()


    @staticmethod
    def _validate_cause_event(
        connection: ManagedConnection,
        command: CreateSemanticSeedRunCommand,
    ) -> None:
        expected_id: int | None
        expected_actions: tuple[str, ...]
        if command.cause_kind is SemanticIntentProvenanceKind.BASELINE_BACKFILL:
            expected_id = None
            expected_actions = ()
        elif command.cause_kind is SemanticIntentProvenanceKind.ORG_RETARGET:
            expected_id = command.organization_event_audit_id
            expected_actions = (
                "organization.membership.move",
                "organization.unit.move",
                "organization.part.move",
            )
        elif command.cause_kind is SemanticIntentProvenanceKind.DETECTOR_RESEED:
            expected_id = command.detector_rollout_audit_id
            expected_actions = ("semantic_edge.detector.rollout",)
        elif command.cause_kind is SemanticIntentProvenanceKind.LIFECYCLE_REACTIVATE:
            expected_id = command.lifecycle_restore_audit_id
            expected_actions = ("knowledge_item.restore", "personal_wiki.restore")
        else:
            raise SemanticEdgeSeedError("The seed cause is unsupported.")
        if expected_id is None:
            if any(
                value is not None
                for value in (
                    command.organization_event_audit_id,
                    command.detector_rollout_audit_id,
                    command.lifecycle_restore_audit_id,
                )
            ):
                raise SemanticEdgeSeedError("The seed cause carries an invalid event proof.")
            return
        row = connection.execute(
            "SELECT action,outcome,space_id,item_id FROM audit_event WHERE id=?",
            (expected_id,),
        ).fetchone()
        if row is None or row["outcome"] not in {"committed", "moved", "restored"}:
            raise SemanticEdgeSeedError("The seed cause audit proof is unavailable.")
        action = str(row["action"])
        if action not in expected_actions:
            raise SemanticEdgeSeedError("The seed cause audit proof has the wrong action.")
        if command.scope_kind == "item" and row["item_id"] != command.scope_id:
            raise SemanticEdgeSeedError("The item restore proof is out of scope.")
        if command.scope_kind == "wiki" and row["space_id"] != command.scope_id:
            raise SemanticEdgeSeedError("The Wiki restore proof is out of scope.")

    def run_batch(
        self,
        seed_run_id: str,
        *,
        limit: int = SEMANTIC_SEED_BATCH_LIMIT,
    ) -> SemanticSeedBatchResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= SEMANTIC_SEED_BATCH_LIMIT:
            raise ValueError("limit must be from 1 through 200.")
        connection = self._factory.open()
        try:
            with transaction(connection):
                run = self._graphs.get_seed_run(connection, seed_run_id)
                if run is None:
                    raise SemanticEdgeSeedError("The semantic seed run is unavailable.")
                if run.state == "completed":
                    return self._result(run, 0, 0)
                run = self._graphs.start_seed_run(
                    connection,
                    seed_run_id=run.id,
                    expected_fence=run.fence,
                    now_ms=db_now_ms(connection),
                )
                sources = self._auto.list_seed_sources(connection, run, limit=limit)
                if not sources:
                    return self._complete(connection, run)
                prepared: list[SemanticEdgeIntentRecord] = []
                last = sources[-1]
                for source in sources:
                    lifecycle = derive_semantic_lifecycle_vector(
                        connection,
                        source_space_id=source.source_space_id,
                        source_item_id=source.source_item_id,
                    )
                    for target in self._targets_for_run(
                        connection, run=run, source=source
                    ):
                        existing = self._auto.find_generation_intent(
                            connection,
                            source_revision_id=source.source_revision_id,
                            target_kind=target.target_kind,
                            target_space_id=target.target_space_id,
                            detector_generation=run.detector_generation,
                            organization_generation=target.organization_generation,
                            item_lifecycle_generation=lifecycle.item_lifecycle_generation,
                            wiki_lifecycle_generation=lifecycle.wiki_lifecycle_generation,
                        )
                        if existing is not None:
                            continue
                        effect_key = _intent_effect_key(
                            source,
                            target,
                            detector_generation=run.detector_generation,
                            item_lifecycle_generation=lifecycle.item_lifecycle_generation,
                            wiki_lifecycle_generation=lifecycle.wiki_lifecycle_generation,
                        )
                        proof = _digest(
                            {
                                "detector_generation": run.detector_generation,
                                "item_lifecycle_generation": lifecycle.item_lifecycle_generation,
                                "organization_generation": target.organization_generation,
                                "source_space_generation": source.source_space_generation,
                                "source_space_id": source.source_space_id,
                                "target_kind": target.target_kind.value,
                                "target_space_generation": target.target_space_generation,
                                "target_space_id": target.target_space_id,
                                "wiki_lifecycle_generation": lifecycle.wiki_lifecycle_generation,
                            }
                        )
                        prepared.append(
                            SemanticEdgeIntentRecord(
                                id=_id(_INTENT_NAMESPACE, effect_key),
                                effect_key=effect_key,
                                source_space_id=source.source_space_id,
                                source_item_id=source.source_item_id,
                                source_revision_id=source.source_revision_id,
                                source_body_sha256=source.source_body_sha256,
                                target_kind=target.target_kind,
                                target_space_id=target.target_space_id,
                                target_org_unit_id=target.target_org_unit_id,
                                detector_generation=run.detector_generation,
                                source_revision_row_version=1,
                                source_space_generation=source.source_space_generation,
                                target_space_generation=target.target_space_generation,
                                organization_generation=target.organization_generation,
                                closure_proof_sha256=proof,
                                item_lifecycle_generation=lifecycle.item_lifecycle_generation,
                                wiki_lifecycle_generation=lifecycle.wiki_lifecycle_generation,
                                latest_episode_id=lifecycle.latest_episode_id,
                                latest_episode_no=lifecycle.latest_episode_no,
                                latest_episode_restored_at=lifecycle.latest_episode_restored_at,
                                provenance_kind=run.cause_kind,
                                publication_transition_id=None,
                                publication_audit_event_id=None,
                                publication_outbox_event_id=None,
                                seed_run_id=run.id,
                                seed_snapshot_high_water=run.snapshot_domain_high_water,
                                seed_cursor_updated_at=last.updated_at,
                                seed_cursor_revision_id=last.source_revision_id,
                                organization_event_audit_id=run.organization_event_audit_id,
                                detector_rollout_audit_id=run.detector_rollout_audit_id,
                                lifecycle_restore_audit_id=run.lifecycle_restore_audit_id,
                                created_at=db_now_ms(connection),
                            )
                        )
                run = self._graphs.advance_seed_run(
                    connection,
                    seed_run_id=run.id,
                    fence=run.fence,
                    cursor_updated_at=last.updated_at,
                    cursor_revision_id=last.source_revision_id,
                    scanned_increment=len(sources),
                    intent_increment=len(prepared),
                    now_ms=db_now_ms(connection),
                )
                for intent in prepared:
                    self._graphs.append_intent(connection, intent)
                if len(sources) < limit:
                    completed = self._complete(connection, run)
                    return SemanticSeedBatchResult(
                        completed.seed_run_id,
                        completed.state,
                        completed.scanned_count,
                        completed.intent_count,
                        len(sources),
                        len(prepared),
                        completed.cursor_updated_at,
                        completed.cursor_revision_id,
                    )
                return SemanticSeedBatchResult(
                    run.id,
                    run.state,
                    run.scanned_count,
                    run.intent_count,
                    len(sources),
                    len(prepared),
                    run.cursor_updated_at,
                    run.cursor_revision_id,
                )
        finally:
            connection.close()

    def ensure_baseline_run(
        self, *, detector_generation: int = SEMANTIC_DETECTOR_GENERATION
    ) -> SemanticEdgeIntentSeedRunRecord:
        """Create or replay the deterministic pre-enable global baseline."""

        if (
            isinstance(detector_generation, bool)
            or not isinstance(detector_generation, int)
            or detector_generation < 1
        ):
            raise ValueError("detector_generation must be a positive integer.")
        return self.create(
            CreateSemanticSeedRunCommand(
                effect_key=f"semantic-seed:baseline:d{detector_generation}",
                cause_kind=SemanticIntentProvenanceKind.BASELINE_BACKFILL,
                scope_kind="global",
                scope_id=None,
                detector_generation=detector_generation,
            )
        )

    def run_next_batch(
        self, *, limit: int = SEMANTIC_SEED_BATCH_LIMIT
    ) -> SemanticSeedBatchResult | None:
        """Advance at most one oldest restartable seed run by one batch."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= SEMANTIC_SEED_BATCH_LIMIT
        ):
            raise ValueError("limit must be from 1 through 200.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT id FROM semantic_edge_intent_seed_run
                        WHERE state IN ('pending','running','failed')
                        ORDER BY created_at,id LIMIT 1"""
                ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self.run_batch(str(row["id"]), limit=limit)

    def _targets_for_run(
        self,
        connection: ManagedConnection,
        *,
        run: SemanticEdgeIntentSeedRunRecord,
        source: SemanticSeedSource,
    ) -> tuple[SemanticIntentTarget, ...]:
        targets = self._auto.list_targets(
            connection, source_space_id=source.source_space_id
        )
        if run.cause_kind is not SemanticIntentProvenanceKind.ORG_RETARGET:
            return targets
        if run.new_ancestor_space_id is None:
            raise SemanticEdgeSeedError("The organization retarget has no new ancestor.")
        allowed_rows = connection.execute(
            """WITH RECURSIVE ancestors(id) AS (
                   SELECT owner_org_unit_id FROM knowledge_space WHERE id=?
                   UNION ALL SELECT unit.parent_id FROM org_unit AS unit JOIN ancestors
                     ON unit.id=ancestors.id WHERE unit.parent_id IS NOT NULL
                 )
                 SELECT space.id FROM knowledge_space AS space JOIN ancestors
                   ON ancestors.id=space.owner_org_unit_id
                  WHERE space.space_kind IN ('team','responsibility')
                    AND space.status='active'""",
            (run.new_ancestor_space_id,),
        ).fetchall()
        allowed = {str(row["id"]) for row in allowed_rows}
        return tuple(
            target
            for target in targets
            if target.target_kind is SemanticIntentTargetKind.AGGREGATE
            and target.target_space_id in allowed
            and target.target_space_id != run.old_ancestor_space_id
        )

    def _complete(
        self,
        connection: ManagedConnection,
        run: SemanticEdgeIntentSeedRunRecord,
    ) -> SemanticSeedBatchResult:
        digest = _digest(
            {
                "cause_kind": run.cause_kind.value,
                "cursor_revision_id": run.cursor_revision_id,
                "cursor_updated_at": run.cursor_updated_at,
                "detector_generation": run.detector_generation,
                "intent_count": run.intent_count,
                "scanned_count": run.scanned_count,
                "schema_version": 1,
                "seed_run_id": run.id,
                "snapshot_domain_high_water": run.snapshot_domain_high_water,
            }
        )
        completed = self._graphs.complete_seed_run(
            connection,
            seed_run_id=run.id,
            fence=run.fence,
            result_sha256=digest,
            now_ms=db_now_ms(connection),
        )
        return self._result(completed, 0, 0)

    @staticmethod
    def _result(
        run: SemanticEdgeIntentSeedRunRecord,
        batch_scanned: int,
        batch_intents: int,
    ) -> SemanticSeedBatchResult:
        return SemanticSeedBatchResult(
            run.id,
            run.state,
            run.scanned_count,
            run.intent_count,
            batch_scanned,
            batch_intents,
            run.cursor_updated_at,
            run.cursor_revision_id,
        )

    def assert_baseline_complete(
        self, *, detector_generation: int = SEMANTIC_DETECTOR_GENERATION
    ) -> None:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    """SELECT id FROM semantic_edge_intent_seed_run
                        WHERE cause_kind='baseline_backfill' AND scope_kind='global'
                          AND detector_generation=? AND state='completed'
                        ORDER BY completed_at DESC,id DESC LIMIT 1""",
                    (detector_generation,),
                ).fetchone()
                if row is None:
                    raise SemanticEdgeBaselineIncompleteError(
                        "A completed automatic-edge baseline seed is required."
                    )
                missing = connection.execute(
                    """SELECT 1 FROM knowledge_item AS item
                       JOIN knowledge_space AS space ON space.id=item.space_id
                      WHERE item.lifecycle_status='active'
                        AND item.curation_status='published' AND space.status='active'
                        AND NOT EXISTS (
                          SELECT 1 FROM semantic_edge_intent AS intent
                           WHERE intent.source_revision_id=item.current_revision_id
                             AND intent.detector_generation=?
                             AND intent.target_kind='local'
                             AND intent.target_space_id=item.space_id
                        ) LIMIT 1""",
                    (detector_generation,),
                ).fetchone()
                if missing is not None:
                    raise SemanticEdgeBaselineIncompleteError(
                        "The automatic-edge baseline does not reconcile the current corpus."
                    )
        finally:
            connection.close()


class SemanticDetectorRolloutService:
    """Append the immutable deployment event consumed by the reseed cursor."""

    __slots__ = ("_audit", "_factory", "_outbox")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SemanticDetectorRolloutService requires a ConnectionFactory.")
        self._factory = factory
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()

    def rollout(
        self, command: RolloutSemanticDetectorCommand
    ) -> SemanticDetectorRolloutResult:
        if not isinstance(command, RolloutSemanticDetectorCommand):
            raise TypeError("rollout requires a RolloutSemanticDetectorCommand.")
        previous = command.previous_detector_generation
        current = command.detector_generation
        if (
            isinstance(previous, bool)
            or not isinstance(previous, int)
            or isinstance(current, bool)
            or not isinstance(current, int)
            or previous < 1
            or current <= previous
        ):
            raise ValueError("Detector rollout generations are invalid.")
        try:
            request_id = str(UUID(command.request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a UUID.") from exc
        if request_id != command.request_id:
            raise ValueError("request_id must be a canonical lowercase UUID.")
        effect = f"semantic-detector-rollout:d{previous}:d{current}"
        event_uuid = _id(_AUDIT_NAMESPACE, effect, "rollout")
        connection = self._factory.open()
        try:
            with transaction(connection):
                existing = connection.execute(
                    """SELECT id,action,outcome,details_json FROM audit_event
                        WHERE event_uuid=?""",
                    (event_uuid,),
                ).fetchone()
                if existing is not None:
                    expected_details = {
                        "detector_generation": current,
                        "previous_detector_generation": previous,
                        "schema_version": 1,
                    }
                    if (
                        existing["action"] != "semantic_edge.detector.rollout"
                        or existing["outcome"] != "committed"
                        or json.loads(str(existing["details_json"])) != expected_details
                    ):
                        raise SemanticEdgeSeedError(
                            "The detector rollout identity conflicts with history."
                        )
                    return SemanticDetectorRolloutResult(
                        int(existing["id"]), previous, current
                    )
                now_ms = db_now_ms(connection)
                audit = self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=event_uuid,
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=None,
                        action="semantic_edge.detector.rollout",
                        outcome="committed",
                        actor_kind="system",
                        actor_user_id=None,
                        actor_display_name="Second Brain Deployment",
                        actor_role_snapshot="system",
                        details={
                            "detector_generation": current,
                            "previous_detector_generation": previous,
                            "schema_version": 1,
                        },
                    ),
                )
                self._outbox.append(
                    connection,
                    OutboxEventDraft(
                        logical_key=effect,
                        space_id=None,
                        aggregate_type="semantic_edge_detector",
                        aggregate_id=_id(_SEED_NAMESPACE, effect),
                        aggregate_version=current,
                        event_type="semantic.edge.detector.rolled_out",
                        payload={
                            "audit_event_id": audit.id,
                            "detector_generation": current,
                            "previous_detector_generation": previous,
                            "schema_version": 1,
                        },
                        available_at_ms=now_ms,
                    ),
                )
                return SemanticDetectorRolloutResult(audit.id, previous, current)
        finally:
            connection.close()


class SemanticIntentMaterializer:
    """Convert flag-eligible immutable intents to exactly one known job."""

    __slots__ = ("_auto", "_factory", "_flags", "_kernel")

    def __init__(
        self,
        factory: ConnectionFactory,
        kernel: DurableWorkKernel,
        flags: FeatureFlags,
        *,
        automatic: AutomaticSemanticEdgeRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SemanticIntentMaterializer requires a ConnectionFactory.")
        if not isinstance(kernel, DurableWorkKernel):
            raise TypeError("SemanticIntentMaterializer requires a DurableWorkKernel.")
        if not isinstance(flags, FeatureFlags):
            raise TypeError("SemanticIntentMaterializer requires FeatureFlags.")
        self._factory = factory
        self._kernel = kernel
        self._flags = flags
        self._auto = automatic or AutomaticSemanticEdgeRepository()

    def run_batch(self, *, limit: int = 200) -> SemanticIntentMaterializationResult:
        connection = self._factory.open()
        try:
            with transaction(connection):
                intents = self._auto.list_unjoined_intents(
                    connection,
                    local_enabled=self._flags.auto_semantic_edges_local,
                    aggregate_enabled=self._flags.auto_semantic_edges_aggregate,
                    limit=limit,
                )
                inserted = replayed = 0
                now_ms = db_now_ms(connection)
                for intent in intents:
                    handler_version = (
                        SEMANTIC_EDGE_LOCAL_HANDLER_VERSION
                        if intent.target_kind is SemanticIntentTargetKind.LOCAL
                        else SEMANTIC_EDGE_AGGREGATE_HANDLER_VERSION
                    )
                    result = self._kernel.enqueue_in_transaction(
                        connection,
                        JobEnqueueDraft(
                            job_id=_id(_JOB_NAMESPACE, intent.effect_key),
                            dedup_id=None,
                            operation_id=None,
                            actor_kind="system",
                            actor_user_id=None,
                            space_id=intent.target_space_id,
                            job_type=SEMANTIC_EDGE_JOB_TYPE,
                            handler_version=handler_version,
                            effect_key=intent.effect_key,
                            input_ref={
                                "intent_id": intent.id,
                                "source_revision_id": intent.source_revision_id,
                                "target_kind": intent.target_kind.value,
                                "target_space_id": intent.target_space_id,
                            },
                            expected_state={
                                "closure_proof_sha256": intent.closure_proof_sha256,
                                "detector_generation": intent.detector_generation,
                                "item_lifecycle_generation": intent.item_lifecycle_generation,
                                "organization_generation": intent.organization_generation,
                                "source_space_generation": intent.source_space_generation,
                                "target_space_generation": intent.target_space_generation,
                                "wiki_lifecycle_generation": intent.wiki_lifecycle_generation,
                            },
                            authorization_snapshot={
                                "actor_kind": "system",
                                "schema_version": 1,
                            },
                            authorization_scope_epoch=None,
                            authorization_part_id=None,
                            authorization_capabilities=(),
                            max_attempts=3,
                            cancellable=False,
                            available_at_ms=now_ms,
                        ),
                    )
                    if result.inserted:
                        inserted += 1
                    else:
                        replayed += 1
                return SemanticIntentMaterializationResult(
                    len(intents), inserted, replayed
                )
        finally:
            connection.close()


class SemanticSeedTriggerConsumer:
    """Anti-join immutable cause events into provenance-correct seed runs.

    This cursor is independent of projection/outbox acknowledgement: consumed
    authority is the immutable seed-run foreign key to the exact audit event.
    Restarting selects the same lowest unjoined IDs and converges by effect key.
    """

    __slots__ = ("_factory", "_seeds")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        seeds: SemanticIntentSeedService | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SemanticSeedTriggerConsumer requires a ConnectionFactory.")
        self._factory = factory
        self._seeds = seeds or SemanticIntentSeedService(factory)

    def run_batch(self, *, limit: int = SEMANTIC_SEED_BATCH_LIMIT) -> SemanticSeedTriggerResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= SEMANTIC_SEED_BATCH_LIMIT:
            raise ValueError("limit must be from 1 through 200.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                rows = connection.execute(
                    """SELECT audit.id,audit.action,audit.space_id,audit.item_id,
                              audit.details_json
                         FROM audit_event AS audit
                        WHERE (
                          (audit.action IN ('knowledge_item.restore','personal_wiki.restore')
                           AND audit.outcome='restored'
                           AND NOT EXISTS (
                             SELECT 1 FROM semantic_edge_intent_seed_run AS seed
                              WHERE seed.lifecycle_restore_audit_id=audit.id))
                          OR
                          (audit.action='organization.unit.move' AND audit.outcome='moved'
                           AND NOT EXISTS (
                             SELECT 1 FROM semantic_edge_intent_seed_run AS seed
                              WHERE seed.organization_event_audit_id=audit.id))
                          OR
                          (audit.action='semantic_edge.detector.rollout'
                           AND audit.outcome='committed'
                           AND NOT EXISTS (
                             SELECT 1 FROM semantic_edge_intent_seed_run AS seed
                              WHERE seed.detector_rollout_audit_id=audit.id))
                        )
                        ORDER BY audit.id LIMIT ?""",
                    (limit,),
                ).fetchall()
                events = tuple(
                    (
                        int(row["id"]),
                        str(row["action"]),
                        None if row["space_id"] is None else str(row["space_id"]),
                        None if row["item_id"] is None else str(row["item_id"]),
                        json.loads(str(row["details_json"])),
                    )
                    for row in rows
                )
        finally:
            connection.close()
        created = replayed = 0
        for audit_id, action, space_id, item_id, details in events:
            command = self._command_for_event(
                audit_id=audit_id,
                action=action,
                space_id=space_id,
                item_id=item_id,
                details=details,
            )
            before = self._seed_exists(command.effect_key)
            self._seeds.create(command)
            if before:
                replayed += 1
            else:
                created += 1
        return SemanticSeedTriggerResult(len(events), created, replayed)

    def _seed_exists(self, effect_key: str) -> bool:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return SemanticGraphContractRepository().get_seed_run_by_effect_key(
                    connection, effect_key
                ) is not None
        finally:
            connection.close()

    def _command_for_event(
        self,
        *,
        audit_id: int,
        action: str,
        space_id: str | None,
        item_id: str | None,
        details: object,
    ) -> CreateSemanticSeedRunCommand:
        if not isinstance(details, dict):
            raise SemanticEdgeSeedError("A semantic seed trigger has malformed details.")
        if action == "knowledge_item.restore":
            if item_id is None:
                raise SemanticEdgeSeedError("The item restore seed lacks an item.")
            return CreateSemanticSeedRunCommand(
                effect_key=f"semantic-seed:lifecycle:item:{audit_id}",
                cause_kind=SemanticIntentProvenanceKind.LIFECYCLE_REACTIVATE,
                scope_kind="item",
                scope_id=item_id,
                lifecycle_restore_audit_id=audit_id,
            )
        if action == "personal_wiki.restore":
            if space_id is None:
                raise SemanticEdgeSeedError("The Wiki restore seed lacks a space.")
            return CreateSemanticSeedRunCommand(
                effect_key=f"semantic-seed:lifecycle:wiki:{audit_id}",
                cause_kind=SemanticIntentProvenanceKind.LIFECYCLE_REACTIVATE,
                scope_kind="wiki",
                scope_id=space_id,
                lifecycle_restore_audit_id=audit_id,
            )
        if action == "semantic_edge.detector.rollout":
            previous = details.get("previous_detector_generation")
            current = details.get("detector_generation")
            if (
                isinstance(previous, bool)
                or not isinstance(previous, int)
                or isinstance(current, bool)
                or not isinstance(current, int)
                or previous < 1
                or current <= previous
            ):
                raise SemanticEdgeSeedError("The detector rollout generations are invalid.")
            return CreateSemanticSeedRunCommand(
                effect_key=f"semantic-seed:detector:{audit_id}:{current}",
                cause_kind=SemanticIntentProvenanceKind.DETECTOR_RESEED,
                scope_kind="global",
                scope_id=None,
                detector_generation=current,
                previous_detector_generation=previous,
                detector_rollout_audit_id=audit_id,
            )
        unit_id = details.get("resource_id")
        old_parent = details.get("old_parent_id")
        new_parent = details.get("new_parent_id")
        if not all(isinstance(value, str) for value in (unit_id, old_parent, new_parent)):
            raise SemanticEdgeSeedError("The organization move seed details are invalid.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                spaces = connection.execute(
                    """SELECT owner_org_unit_id,id,space_kind,status
                         FROM knowledge_space WHERE owner_org_unit_id IN (?,?)""",
                    (old_parent, new_parent),
                ).fetchall()
        finally:
            connection.close()
        by_org = {
            str(row["owner_org_unit_id"]): str(row["id"])
            for row in spaces
            if row["status"] == "active"
            and row["space_kind"] in {"team", "responsibility"}
        }
        if set(by_org) != {old_parent, new_parent}:
            raise SemanticEdgeSeedError("The organization move ancestors are unavailable.")
        return CreateSemanticSeedRunCommand(
            effect_key=f"semantic-seed:org-retarget:{audit_id}",
            cause_kind=SemanticIntentProvenanceKind.ORG_RETARGET,
            scope_kind="org_unit",
            scope_id=unit_id,
            old_ancestor_space_id=by_org[old_parent],
            new_ancestor_space_id=by_org[new_parent],
            organization_event_audit_id=audit_id,
        )


__all__ = [
    "CreateSemanticSeedRunCommand",
    "RolloutSemanticDetectorCommand",
    "SEMANTIC_DETECTOR_GENERATION",
    "SEMANTIC_EDGE_AGGREGATE_HANDLER_VERSION",
    "SEMANTIC_EDGE_JOB_TYPE",
    "SEMANTIC_EDGE_LOCAL_HANDLER_VERSION",
    "SemanticEdgeBaselineIncompleteError",
    "SemanticEdgeSeedError",
    "SemanticDetectorRolloutResult",
    "SemanticDetectorRolloutService",
    "SemanticIntentMaterializationResult",
    "SemanticIntentMaterializer",
    "SemanticIntentSeedService",
    "SemanticSeedBatchResult",
    "SemanticSeedTriggerConsumer",
    "SemanticSeedTriggerResult",
]
