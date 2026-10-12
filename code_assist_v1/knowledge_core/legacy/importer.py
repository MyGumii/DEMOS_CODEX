"""Prepared, bounded P2.3 import of the reviewed legacy identity registry.

Preparation consumes bytes and performs every source observation before a
database transaction starts.  Application owns one bounded transaction and
composes the canonical repositories; it does not open files, consult a clock,
or catch a failed mutation inside that transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import calendar
import hashlib
import json
import sqlite3
import unicodedata
import re
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.migration import (
    LegacyMigrationDetails,
    LegacyMigrationInsertResult,
    LegacyMigrationKey,
    LegacyMigrationRecord,
    LegacyMigrationRepository,
    LegacyMigrationTarget,
    VerifiedBlobEvidence,
    legacy_user_account_projection_sha256,
    migration_child_bindings_sha256,
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from ..repositories.organization import OrganizationRepository, normalize_org_name
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.spaces import SpaceRepository
from ..repositories.users import LegacyUserRecord, UserRepository
from ..services.spaces import space_id_for_owner
from .compatibility import LegacyCompatibilityError, legacy_user_records
from .plan_contract import (
    canonical_json_bytes,
    plan_contract_errors,
    sha256_canonical,
)


MAX_IDENTITY_USERS_PER_TRANSACTION = 100
MAX_REVIEWED_PLAN_BYTES = 64 * 1024 * 1024
MAX_USERS_REGISTRY_BYTES = 16 * 1024 * 1024
USERS_REGISTRY_MEDIA_TYPE = "application/json"

_REQUEST_NAMESPACE = UUID("45fe8edf-497d-482a-a72f-6af7cdbf588a")
_OPERATION_NAMESPACE = UUID("077a50d5-9d25-4d37-be17-cea8560572ad")
_MEMBERSHIP_NAMESPACE = UUID("8466c0de-94f9-4bbd-a67e-78014fd74793")
_LEADER_NAMESPACE = UUID("6508dfd5-57e3-46eb-9de2-a5271785fd47")
_AUDIT_NAMESPACE = UUID("f43278e2-a123-45b9-a8d9-b2b577230a9a")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class LegacyIdentityImportError(KnowledgeCoreError):
    """Base error for the reviewed P2.3 identity-import boundary."""

    code = "LEGACY_IDENTITY_IMPORT_ERROR"


class LegacyIdentityPreparationError(LegacyIdentityImportError):
    code = "LEGACY_IDENTITY_PREPARATION_INVALID"


class LegacyIdentityBatchLimitError(LegacyIdentityPreparationError):
    code = "LEGACY_IDENTITY_BATCH_LIMIT"


class LegacyIdentityStateConflictError(LegacyIdentityImportError):
    code = "LEGACY_IDENTITY_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class _PreparedOrganizationUnit:
    id: str
    kind: str
    parent_id: str | None
    name: str


@dataclass(frozen=True, slots=True)
class _PreparedLegacyIdentityUser:
    legacy_user: LegacyUserRecord
    inventory_record_id: str
    inventory_record_sha256: str
    source_record_sha256: str
    root_id: str
    path_key: str
    source_ordinal: int
    target_space_id: str
    assigned_part_id: str | None
    membership_id: str | None
    leader_assignment_id: str | None
    mapping_id: str

    @property
    def user_id(self) -> str:
        return self.legacy_user.id


@dataclass(frozen=True, slots=True)
class _PreparedLegacyIdentityBatch:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    effective_at_ms: int
    inventory_manifest_sha256: str
    root_binding_sha256: str
    reviewer_id: str
    assigner_user_id: str
    organization_units: tuple[_PreparedOrganizationUnit, ...]
    users: tuple[_PreparedLegacyIdentityUser, ...]
    source_container_mapping_id: str
    source_container_key: LegacyMigrationKey
    source_container_target: LegacyMigrationTarget
    verified_blob_evidence: VerifiedBlobEvidence


@dataclass(frozen=True, slots=True)
class LegacyIdentityUserOutcome:
    user_id: str
    mapping_id: str
    result_disposition: str
    mapping_inserted: bool
    account_inserted: bool
    personal_space_created: bool
    membership_created: bool
    leader_assignment_created: bool


@dataclass(frozen=True, slots=True)
class LegacyIdentityImportResult:
    plan_sha256: str
    migration_run_id: str
    request_id: str
    operation_id: str
    source_container_mapping_id: str
    source_container_inserted: bool
    created_organization_unit_count: int
    outcomes: tuple[LegacyIdentityUserOutcome, ...]

    @property
    def imported_user_count(self) -> int:
        return len(self.outcomes)

    @property
    def inserted_mapping_count(self) -> int:
        return sum(value.mapping_inserted for value in self.outcomes) + int(
            self.source_container_inserted
        )


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_member")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non_finite_json_number:{value}")


def _parse_json_object(raw: bytes, *, field: str, maximum: int) -> dict[str, object]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= maximum:
        raise LegacyIdentityPreparationError(f"{field} bytes are invalid.")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise LegacyIdentityPreparationError(f"{field} is not strict UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise LegacyIdentityPreparationError(f"{field} must contain a JSON object.")
    return value


def _reviewed_plan(
    value: bytes | Mapping[str, object], *, expected_plan_sha256: str
) -> dict[str, object]:
    if (
        not isinstance(expected_plan_sha256, str)
        or _SHA256_RE.fullmatch(expected_plan_sha256) is None
    ):
        raise LegacyIdentityPreparationError(
            "expected_plan_sha256 must be a lowercase SHA-256 digest."
        )
    if isinstance(value, bytes):
        plan = _parse_json_object(
            value, field="reviewed_plan", maximum=MAX_REVIEWED_PLAN_BYTES
        )
        # The reviewed file contract is one canonical JSON document plus LF.
        # This rejects harmless-looking whitespace/member-order rewrites as
        # well as different byte artifacts that decode to the same object.
        if value != canonical_json_bytes(plan) + b"\n":
            raise LegacyIdentityPreparationError(
                "reviewed_plan bytes are not the canonical reviewed artifact."
            )
    elif isinstance(value, Mapping):
        try:
            encoded = canonical_json_bytes(value)
            if len(encoded) > MAX_REVIEWED_PLAN_BYTES:
                raise LegacyIdentityPreparationError("reviewed_plan is too large.")
            plan = json.loads(encoded.decode("utf-8"))
        except LegacyIdentityPreparationError:
            raise
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise LegacyIdentityPreparationError("reviewed_plan is invalid.") from None
    else:
        raise TypeError("reviewed_plan must be bytes or a mapping.")
    if plan_contract_errors(plan) or plan.get("stage") != "reviewed_migration_plan":
        raise LegacyIdentityPreparationError(
            "reviewed_plan does not satisfy the frozen P2.2 contract."
        )
    if plan.get("plan_sha256") != expected_plan_sha256:
        raise LegacyIdentityPreparationError(
            "reviewed_plan does not match expected_plan_sha256."
        )
    return plan


def _utc_ms(value: object) -> int:
    if not isinstance(value, str):
        raise LegacyIdentityPreparationError("Inventory start time is invalid.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise LegacyIdentityPreparationError("Inventory start time is invalid.") from None
    return calendar.timegm(parsed.utctimetuple()) * 1000 + parsed.microsecond // 1000


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def legacy_identity_request_id_for_plan(plan_sha256: str) -> str:
    return _derived_uuid(_REQUEST_NAMESPACE, plan_sha256)


def legacy_identity_operation_id_for_plan(plan_sha256: str) -> str:
    return _derived_uuid(_OPERATION_NAMESPACE, plan_sha256)


def _record_source(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("source_record")
    if not isinstance(value, Mapping):
        raise LegacyIdentityPreparationError("Reviewed registry record is invalid.")
    return value


def _registry_account(source: Mapping[str, object]) -> Mapping[str, object]:
    parse = source.get("parse")
    value = parse.get("registry_account") if isinstance(parse, Mapping) else None
    if not isinstance(value, Mapping):
        value = source.get("registry_account")
    if not isinstance(value, Mapping):
        raise LegacyIdentityPreparationError("Reviewed registry account is invalid.")
    return value


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LegacyIdentityPreparationError(f"Reviewed {field} is invalid.")
    return value


def _prepare_evidence(
    evidence: VerifiedBlobEvidence, *, raw: bytes, reviewed_sha256: str, size: int
) -> VerifiedBlobEvidence:
    if not isinstance(evidence, VerifiedBlobEvidence):
        raise TypeError("verified_blob_evidence must be VerifiedBlobEvidence.")
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        evidence.raw_sha256 != observed_sha256
        or evidence.raw_sha256 != reviewed_sha256
        or evidence.size_bytes != len(raw)
        or evidence.size_bytes != size
        or evidence.media_type != USERS_REGISTRY_MEDIA_TYPE
        or evidence.normalized_content_sha256 is not None
        or evidence.normalizer_version is not None
    ):
        raise LegacyIdentityPreparationError(
            "Verified users-registry evidence does not match the reviewed bytes."
        )
    return evidence


def _organization_units(
    draft: Mapping[str, object],
) -> tuple[_PreparedOrganizationUnit, ...]:
    organization = _mapping(draft.get("organization"), field="organization")
    result: list[_PreparedOrganizationUnit] = []
    responsibilities = organization.get("responsibility_units")
    teams = organization.get("team_units")
    parts = organization.get("part_units")
    if not all(isinstance(value, list) for value in (responsibilities, teams, parts)):
        raise LegacyIdentityPreparationError("Reviewed organization is invalid.")
    for raw in responsibilities:  # type: ignore[union-attr]
        row = _mapping(raw, field="responsibility unit")
        result.append(
            _PreparedOrganizationUnit(
                id=str(row["id"]), kind="responsibility", parent_id=None, name=str(row["code"])
            )
        )
    for raw in teams:  # type: ignore[union-attr]
        row = _mapping(raw, field="team unit")
        result.append(
            _PreparedOrganizationUnit(
                id=str(row["id"]),
                kind="team",
                parent_id=str(row["responsibility_id"]),
                name=str(row["code"]),
            )
        )
    for raw in parts:  # type: ignore[union-attr]
        row = _mapping(raw, field="part unit")
        result.append(
            _PreparedOrganizationUnit(
                id=str(row["id"]),
                kind="part",
                parent_id=str(row["team_id"]),
                name=str(row["code"]),
            )
        )
    kind_order = {"responsibility": 0, "team": 1, "part": 2}
    return tuple(sorted(result, key=lambda row: (kind_order[row.kind], row.id)))


def prepare_legacy_identity_batch(
    reviewed_plan: bytes | Mapping[str, object],
    users_registry_bytes: bytes,
    *,
    expected_plan_sha256: str,
    verified_blob_evidence: VerifiedBlobEvidence,
) -> _PreparedLegacyIdentityBatch:
    """Prepare one complete users-registry closure without database or file I/O.

    ``expected_plan_sha256`` is the external operator/deployment anchor.  A
    mapping input therefore means an already-parsed in-memory copy of that
    exact anchored artifact, never authority to select a different plan.
    """

    plan = _reviewed_plan(
        reviewed_plan, expected_plan_sha256=expected_plan_sha256
    )
    raw_registry = _parse_json_object(
        users_registry_bytes,
        field="users_registry",
        maximum=MAX_USERS_REGISTRY_BYTES,
    )
    raw_users = raw_registry.get("users")
    if not isinstance(raw_users, Mapping):
        raise LegacyIdentityPreparationError(
            "users_registry must contain a users object."
        )

    plan_sha256 = str(plan["plan_sha256"])
    draft = _mapping(plan.get("draft"), field="plan draft")
    inputs = _mapping(draft.get("inputs"), field="plan inputs")
    inventory_binding = _mapping(inputs.get("inventory"), field="inventory binding")
    inventory_manifest_sha256 = str(inventory_binding["manifest_sha256"])
    root_binding_sha256 = str(inventory_binding["root_binding_sha256"])
    source_inventory = _mapping(
        draft.get("source_inventory"), field="source inventory"
    )
    metadata = _mapping(source_inventory.get("metadata"), field="source metadata")
    run = _mapping(metadata.get("run"), field="inventory run")
    effective_at_ms = _utc_ms(run.get("started_at_utc"))

    rows = draft.get("records")
    mappings = draft.get("owner_mappings")
    if not isinstance(rows, list) or not isinstance(mappings, list):
        raise LegacyIdentityPreparationError("Reviewed identity ledgers are invalid.")
    mapping_by_id = {
        str(row["mapping_id"]): row
        for raw in mappings
        if isinstance(raw, Mapping)
        for row in (raw,)
    }
    containers = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("category") == "identity.users_registry"
        and row.get("action") == "import"
        and row.get("execution_phase") == "P2.3"
        and row.get("import_mode") == "source_container"
    ]
    if len(containers) != 1:
        raise LegacyIdentityPreparationError(
            "Reviewed plan must contain exactly one imported users registry."
        )
    container_row = containers[0]
    container_source = _record_source(container_row)
    container_sha256 = str(container_source.get("sha256"))
    try:
        container_size = int(container_source["size_bytes"])
    except (KeyError, TypeError, ValueError):
        raise LegacyIdentityPreparationError("Reviewed users registry size is invalid.") from None
    evidence = _prepare_evidence(
        verified_blob_evidence,
        raw=users_registry_bytes,
        reviewed_sha256=container_sha256,
        size=container_size,
    )

    ordered_raw_users = sorted(
        raw_users.items(),
        key=lambda item: (
            unicodedata.normalize("NFKC", str(item[0])).casefold(),
            str(item[0]),
        ),
    )
    raw_by_ordinal = {
        ordinal: (str(source_id), raw)
        for ordinal, (source_id, raw) in enumerate(ordered_raw_users, 1)
    }
    user_rows = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("category") == "identity.users_registry"
        and row.get("action") == "import"
        and row.get("execution_phase") == "P2.3"
        and row.get("import_mode") == "user_account"
    ]
    if len(user_rows) > MAX_IDENTITY_USERS_PER_TRANSACTION:
        raise LegacyIdentityBatchLimitError(
            "A users-registry transaction is limited to "
            f"{MAX_IDENTITY_USERS_PER_TRANSACTION} imported users."
        )

    prepared_users: list[_PreparedLegacyIdentityUser] = []
    seen_users: set[str] = set()
    seen_ordinals: set[int] = set()
    for row in sorted(user_rows, key=lambda value: str(value.get("record_id"))):
        source = _record_source(row)
        try:
            ordinal = int(source["source_ordinal"])
        except (KeyError, TypeError, ValueError):
            raise LegacyIdentityPreparationError("Registry account ordinal is invalid.") from None
        if ordinal in seen_ordinals or ordinal not in raw_by_ordinal:
            raise LegacyIdentityPreparationError(
                "Registry account ordinal is not unique and bound."
            )
        seen_ordinals.add(ordinal)
        source_id, raw_user = raw_by_ordinal[ordinal]
        if not isinstance(raw_user, Mapping):
            raise LegacyIdentityPreparationError("Imported registry account must be an object.")
        account = _registry_account(source)
        if (
            account.get("payload_sha256") != sha256_canonical(raw_user)
            or account.get("user_id_sha256")
            != hashlib.sha256(source_id.encode("utf-8")).hexdigest()
            or source.get("parent_record_id") != container_row.get("record_id")
            or not isinstance(source.get("sha256"), str)
        ):
            raise LegacyIdentityPreparationError(
                "Registry account bytes do not match their reviewed logical record."
            )
        mapping_id = row.get("owner_mapping_id")
        owner_mapping = mapping_by_id.get(str(mapping_id))
        if (
            not isinstance(owner_mapping, Mapping)
            or owner_mapping.get("decision") != "personal"
            or row.get("target_space_kind") != "personal"
            or row.get("resolved_owner_user_id") != owner_mapping.get("user_id")
        ):
            raise LegacyIdentityPreparationError(
                "Registry account does not have an exact reviewed personal mapping."
            )
        reviewed_status = str(owner_mapping["account_status"])
        # The inventory accepts all three historical status spellings
        # (``disabled``, ``account_status``, and ``active=false``), while the
        # compatibility primitive consumes the oldest Boolean spelling.  Bind
        # the original object by hash above, then adapt only that reviewed
        # status spelling for deterministic canonical normalization.
        normalization_user = dict(raw_user)
        normalization_user["disabled"] = reviewed_status == "disabled"
        try:
            normalized = legacy_user_records(
                {"users": {source_id: normalization_user}}, now_ms=effective_at_ms
            )
        except LegacyCompatibilityError:
            raise LegacyIdentityPreparationError(
                "Registry account cannot be normalized safely."
            ) from None
        if len(normalized) != 1:
            raise LegacyIdentityPreparationError("Registry account normalization is invalid.")
        legacy_user = normalized[0]
        reviewed_user_id = str(owner_mapping["user_id"])
        reviewed_role = str(owner_mapping["system_role"])
        raw_password_hash = raw_user.get("password_hash")
        if (
            legacy_user.id != reviewed_user_id
            or legacy_user.password_hash != raw_password_hash
            or legacy_user.account_status != reviewed_status
            or account.get("account_status") != reviewed_status
            or (
                account.get("role") not in {reviewed_role, "unassigned_legacy"}
            )
        ):
            raise LegacyIdentityPreparationError(
                "Normalized registry account differs from the reviewed mapping."
            )
        if reviewed_user_id in seen_users:
            raise LegacyIdentityPreparationError("Reviewed registry user is duplicated.")
        seen_users.add(reviewed_user_id)
        assigned_part_id = owner_mapping.get("part_id")
        if assigned_part_id is not None:
            assigned_part_id = str(assigned_part_id)
        legacy_user = replace(
            legacy_user,
            system_role=reviewed_role,  # type: ignore[arg-type]
            account_status=reviewed_status,  # type: ignore[arg-type]
            current_part_id=assigned_part_id,
        )
        inventory_record_id = str(row["record_id"])
        prepared_users.append(
            _PreparedLegacyIdentityUser(
                legacy_user=legacy_user,
                inventory_record_id=inventory_record_id,
                inventory_record_sha256=str(row["inventory_record_sha256"]),
                source_record_sha256=str(source["sha256"]),
                root_id=str(source["root_id"]),
                path_key=str(source["path_key"]),
                source_ordinal=ordinal,
                target_space_id=space_id_for_owner("personal", reviewed_user_id),
                assigned_part_id=assigned_part_id,
                membership_id=(
                    None
                    if assigned_part_id is None
                    else _derived_uuid(
                        _MEMBERSHIP_NAMESPACE, plan_sha256, reviewed_user_id
                    )
                ),
                leader_assignment_id=(
                    _derived_uuid(_LEADER_NAMESPACE, plan_sha256, reviewed_user_id)
                    if owner_mapping.get("is_part_leader") is True
                    else None
                ),
                mapping_id=migration_mapping_id_for_record(
                    plan_sha256, inventory_record_id
                ),
            )
        )

    prepared_users.sort(key=lambda value: (value.source_ordinal, value.user_id))
    active_admins = [
        value.user_id
        for value in prepared_users
        if value.legacy_user.system_role == "admin"
        and value.legacy_user.account_status == "active"
    ]
    if not active_admins:
        raise LegacyIdentityPreparationError(
            "Reviewed imported users require an active administrator assigner."
        )
    assigner_user_id = min(active_admins)

    child_bindings = tuple(
        (
            value.mapping_id,
            value.inventory_record_id,
            value.source_record_sha256,
        )
        for value in prepared_users
    )
    container_record_id = str(container_row["record_id"])
    container_path_key = str(container_source["path_key"])
    container_details = LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=inventory_manifest_sha256,
        root_binding_sha256=root_binding_sha256,
        inventory_record_id=container_record_id,
        inventory_record_sha256=str(container_row["inventory_record_sha256"]),
        root_id=str(container_source["root_id"]),
        path_key_sha256=hashlib.sha256(container_path_key.encode("utf-8")).hexdigest(),
        category="identity.users_registry",
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="source_container",
        result_disposition="dependency_closed",
        source_size_bytes=container_size,
        source_media_type=evidence.media_type,
        source_record_sha256=container_sha256,
        planned_import_child_count=len(prepared_users),
        imported_child_bindings_sha256=migration_child_bindings_sha256(
            child_bindings
        ),
    )
    container_key = LegacyMigrationKey(
        legacy_kind="identity.users_registry",
        normalized_path=migration_source_locator(
            root_id=container_details.root_id,
            path_key=container_path_key,
            record_kind="physical_file",
            source_ordinal=0,
        ),
        source_sha256=container_sha256,
    )
    review = _mapping(plan.get("review"), field="review")
    return _PreparedLegacyIdentityBatch(
        plan_sha256=plan_sha256,
        migration_run_id=migration_run_id_for_plan(plan_sha256),
        request_id=legacy_identity_request_id_for_plan(plan_sha256),
        operation_id=legacy_identity_operation_id_for_plan(plan_sha256),
        effective_at_ms=effective_at_ms,
        inventory_manifest_sha256=inventory_manifest_sha256,
        root_binding_sha256=root_binding_sha256,
        reviewer_id=str(review["reviewer_id"]),
        assigner_user_id=assigner_user_id,
        organization_units=_organization_units(draft),
        users=tuple(prepared_users),
        source_container_mapping_id=migration_mapping_id_for_record(
            plan_sha256, container_record_id
        ),
        source_container_key=container_key,
        source_container_target=LegacyMigrationTarget(
            resolved_owner_user_id=None,
            artifact_id=None,
            item_id=None,
            revision_id=None,
            edge_id=None,
            details=container_details,
            source_path_key=container_path_key,
        ),
        verified_blob_evidence=evidence,
    )


class LegacyIdentityImporter:
    """Validate and atomically apply one reviewed registry closure."""

    __slots__ = (
        "_audit",
        "_factory",
        "_failpoint",
        "_migration",
        "_organization",
        "_outbox",
        "_spaces",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        users: UserRepository | None = None,
        organization: OrganizationRepository | None = None,
        spaces: SpaceRepository | None = None,
        migration: LegacyMigrationRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyIdentityImporter requires a ConnectionFactory.")
        self._factory = factory
        self._users = users or UserRepository()
        self._organization = organization or OrganizationRepository()
        self._spaces = spaces or SpaceRepository()
        self._migration = migration or LegacyMigrationRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _user_target(
        batch: _PreparedLegacyIdentityBatch,
        prepared: _PreparedLegacyIdentityUser,
        *,
        result_disposition: str,
    ) -> tuple[LegacyMigrationKey, LegacyMigrationTarget]:
        details = LegacyMigrationDetails(
            plan_sha256=batch.plan_sha256,
            inventory_manifest_sha256=batch.inventory_manifest_sha256,
            root_binding_sha256=batch.root_binding_sha256,
            inventory_record_id=prepared.inventory_record_id,
            inventory_record_sha256=prepared.inventory_record_sha256,
            root_id=prepared.root_id,
            path_key_sha256=hashlib.sha256(
                prepared.path_key.encode("utf-8")
            ).hexdigest(),
            category="identity.users_registry",
            record_kind="logical_record",
            source_ordinal=prepared.source_ordinal,
            import_mode="user_account",
            result_disposition=result_disposition,  # type: ignore[arg-type]
            target_space_id=prepared.target_space_id,
            source_record_sha256=prepared.source_record_sha256,
            account_projection_schema="legacy_user_import_v1",
            account_projection_sha256=legacy_user_account_projection_sha256(
                prepared.legacy_user
            ),
            assigned_part_id=prepared.assigned_part_id,
            membership_id=prepared.membership_id,
            leader_assignment_id=prepared.leader_assignment_id,
            parent_inventory_record_id=(
                batch.source_container_target.details.inventory_record_id
            ),
        )
        return (
            LegacyMigrationKey(
                legacy_kind="identity.users_registry",
                normalized_path=migration_source_locator(
                    root_id=prepared.root_id,
                    path_key=prepared.path_key,
                    record_kind="logical_record",
                    source_ordinal=prepared.source_ordinal,
                ),
                source_sha256=prepared.source_record_sha256,
            ),
            LegacyMigrationTarget(
                resolved_owner_user_id=prepared.user_id,
                artifact_id=None,
                item_id=None,
                revision_id=None,
                edge_id=None,
                details=details,
                source_path_key=prepared.path_key,
            ),
        )

    def _ensure_org_unit(
        self,
        connection: ManagedConnection,
        unit: _PreparedOrganizationUnit,
        *,
        now_ms: int,
    ) -> bool:
        current = self._organization.get_unit(connection, unit.id)
        if current is not None:
            if (
                current.kind != unit.kind
                or current.parent_id != unit.parent_id
                or current.status != "active"
            ):
                raise LegacyIdentityStateConflictError(
                    "A reviewed organization unit conflicts with canonical state."
                )
            return False
        # P2.2's UUID, kind, and parent chain are authoritative.  Its code is
        # only a label because canonical ``org_unit`` has no external-code
        # column.  For an empty migration database we use that label as the
        # deterministic initial display name; an existing UUID keeps its
        # independently managed canonical display name.
        name, name_key = normalize_org_name(unit.name)
        try:
            connection.execute(
                """INSERT INTO org_unit(
                     id,kind,parent_id,name,name_key,status,created_at,updated_at,
                     created_by,updated_by,row_version
                   ) VALUES (?,?,?,?,?,'active',?,?,NULL,NULL,1)""",
                (
                    unit.id,
                    unit.kind,
                    unit.parent_id,
                    name,
                    name_key,
                    now_ms,
                    now_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LegacyIdentityStateConflictError(
                "A reviewed organization unit cannot be created exactly."
            ) from exc
        current = self._organization.require_unit(connection, unit.id)
        if (
            current.kind != unit.kind
            or current.parent_id != unit.parent_id
            or current.name != name
            or current.name_key != name_key
            or current.status != "active"
            or current.row_version != 1
        ):
            raise LegacyIdentityStateConflictError(
                "A created organization unit has an invalid initial projection."
            )
        return True

    def _ensure_membership(
        self,
        connection: ManagedConnection,
        prepared: _PreparedLegacyIdentityUser,
        *,
        assigner_user_id: str,
        now_ms: int,
    ) -> bool:
        if prepared.assigned_part_id is None:
            if self._organization.get_current_membership(connection, prepared.user_id):
                raise LegacyIdentityStateConflictError(
                    "An unassigned reviewed user has an active membership."
                )
            return False
        assert prepared.membership_id is not None
        current = self._organization.get_current_membership(
            connection, prepared.user_id
        )
        if current is not None:
            if (
                current.id != prepared.membership_id
                or current.part_id != prepared.assigned_part_id
                or current.assigned_by != assigner_user_id
                or current.reason != "reviewed P2.3 legacy identity import"
                or current.valid_from_ms != now_ms
            ):
                raise LegacyIdentityStateConflictError(
                    "A reviewed user membership conflicts with canonical state."
                )
            return False
        self._organization.open_membership(
            connection,
            membership_id=prepared.membership_id,
            user_id=prepared.user_id,
            part_id=prepared.assigned_part_id,
            assigned_by=assigner_user_id,
            reason="reviewed P2.3 legacy identity import",
            valid_from_ms=now_ms,
            expected_current_membership_id=None,
        )
        return True

    def _ensure_leader(
        self,
        connection: ManagedConnection,
        prepared: _PreparedLegacyIdentityUser,
        *,
        assigner_user_id: str,
        now_ms: int,
    ) -> bool:
        active_for_user = connection.execute(
            """SELECT id,org_unit_id FROM org_role_assignment
               WHERE user_id=? AND role='part_leader' AND valid_to IS NULL
               ORDER BY id""",
            (prepared.user_id,),
        ).fetchall()
        if prepared.leader_assignment_id is None:
            if active_for_user:
                raise LegacyIdentityStateConflictError(
                    "A non-leader reviewed user has an active leader assignment."
                )
            return False
        assert prepared.assigned_part_id is not None
        current = self._organization.get_active_part_leader(
            connection, prepared.assigned_part_id
        )
        if current is not None:
            if (
                len(active_for_user) != 1
                or active_for_user[0]["id"] != prepared.leader_assignment_id
                or active_for_user[0]["org_unit_id"] != prepared.assigned_part_id
                or current.id != prepared.leader_assignment_id
                or current.user_id != prepared.user_id
                or current.assigned_by != assigner_user_id
                or current.reason != "reviewed P2.3 legacy identity import"
                or current.valid_from_ms != now_ms
            ):
                raise LegacyIdentityStateConflictError(
                    "A reviewed leader assignment conflicts with canonical state."
                )
            return False
        if active_for_user:
            raise LegacyIdentityStateConflictError(
                "A reviewed leader has an active assignment outside the reviewed part."
            )
        self._organization.open_part_leader(
            connection,
            assignment_id=prepared.leader_assignment_id,
            user_id=prepared.user_id,
            part_id=prepared.assigned_part_id,
            assigned_by=assigner_user_id,
            reason="reviewed P2.3 legacy identity import",
            valid_from_ms=now_ms,
            expected_current_assignment_id=None,
        )
        return True

    def _append_mapping_events(
        self,
        connection: ManagedConnection,
        *,
        batch: _PreparedLegacyIdentityBatch,
        record: LegacyMigrationRecord,
    ) -> None:
        mode = record.details.import_mode
        details = {
            "import_mode": mode,
            "inventory_record_id": record.details.inventory_record_id,
            "mapping_id": record.id,
            "migration_run_id": record.migration_run_id,
            "plan_sha256": batch.plan_sha256,
            "result_disposition": record.details.result_disposition,
            "schema_version": 1,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, record.id),
                occurred_at_ms=batch.effective_at_ms,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                action=f"legacy_migration.{mode}",
                outcome=record.details.result_disposition,
                actor_kind="system",
                actor_user_id=None,
                actor_display_name="P2.3 legacy migration",
                actor_role_snapshot="system",
                space_id=record.details.target_space_id,
                reason="reviewed P2.3 legacy identity import",
                details=details,
                request_meta={"reviewer_id": batch.reviewer_id},
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"legacy-migration:{record.id}:imported:v1",
                space_id=record.details.target_space_id,
                aggregate_type="legacy_migration_map",
                aggregate_id=record.id,
                aggregate_version=1,
                event_type=f"legacy_migration.{mode}.imported",
                payload={
                    **details,
                    "operation_id": batch.operation_id,
                    "resolved_owner_user_id": record.resolved_owner_user_id,
                },
                available_at_ms=batch.effective_at_ms,
            ),
        )

    def apply(
        self,
        reviewed_plan_bytes: bytes,
        users_registry_bytes: bytes,
        *,
        expected_plan_sha256: str,
        verified_blob_evidence: VerifiedBlobEvidence,
    ) -> LegacyIdentityImportResult:
        """Validate anchored source artifacts, then apply one atomic closure.

        The public authority boundary accepts only the exact canonical reviewed
        artifact and externally verified registry bytes.  Preparation completes
        before a database connection is opened, so callers cannot forge a
        prepared DTO to bypass review.  The service then owns the only
        transaction that can persist this identity closure.
        """

        if not isinstance(reviewed_plan_bytes, bytes):
            raise TypeError("reviewed_plan_bytes must be bytes.")
        batch = prepare_legacy_identity_batch(
            reviewed_plan_bytes,
            users_registry_bytes,
            expected_plan_sha256=expected_plan_sha256,
            verified_blob_evidence=verified_blob_evidence,
        )
        return self._apply_prepared(batch)

    def _apply_prepared(
        self,
        batch: _PreparedLegacyIdentityBatch,
    ) -> LegacyIdentityImportResult:
        """Apply a validated internal batch in one service-owned transaction."""

        if not isinstance(batch, _PreparedLegacyIdentityBatch):
            raise TypeError("batch must be an internal prepared identity batch.")
        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._apply_in_transaction(connection, batch)
        finally:
            connection.close()

    def _apply_in_transaction(
        self,
        connection: ManagedConnection,
        batch: _PreparedLegacyIdentityBatch,
    ) -> LegacyIdentityImportResult:
        """Compose repositories only under :meth:`apply` transaction ownership."""

        existing_container = self._migration.get_by_id(
            connection, batch.source_container_mapping_id
        )
        if existing_container is not None:
            closed = self._migration.put_imported(
                connection,
                mapping_id=batch.source_container_mapping_id,
                migration_run_id=batch.migration_run_id,
                source_path_key=batch.source_container_target.source_path_key,
                key=batch.source_container_key,
                target=batch.source_container_target,
                verified_blob_evidence=batch.verified_blob_evidence,
                now_ms=batch.effective_at_ms,
            )
            outcomes: list[LegacyIdentityUserOutcome] = []
            for prepared in batch.users:
                stored = self._migration.get_by_id(connection, prepared.mapping_id)
                if stored is None:
                    raise LegacyIdentityStateConflictError(
                        "A closed users registry is missing a reviewed child mapping."
                    )
                key, target = self._user_target(
                    batch,
                    prepared,
                    result_disposition=stored.details.result_disposition,
                )
                verified = self._migration.put_imported(
                    connection,
                    mapping_id=prepared.mapping_id,
                    migration_run_id=batch.migration_run_id,
                    source_path_key=prepared.path_key,
                    key=key,
                    target=target,
                    now_ms=batch.effective_at_ms,
                )
                self._append_mapping_events(
                    connection, batch=batch, record=verified.record
                )
                outcomes.append(
                    LegacyIdentityUserOutcome(
                        user_id=prepared.user_id,
                        mapping_id=verified.record.id,
                        result_disposition=verified.record.details.result_disposition,
                        mapping_inserted=False,
                        account_inserted=False,
                        personal_space_created=False,
                        membership_created=False,
                        leader_assignment_created=False,
                    )
                )
            self._append_mapping_events(
                connection, batch=batch, record=closed.record
            )
            return LegacyIdentityImportResult(
                plan_sha256=batch.plan_sha256,
                migration_run_id=batch.migration_run_id,
                request_id=batch.request_id,
                operation_id=batch.operation_id,
                source_container_mapping_id=closed.record.id,
                source_container_inserted=False,
                created_organization_unit_count=0,
                outcomes=tuple(outcomes),
            )

        existing_results: dict[str, LegacyMigrationInsertResult] = {}
        missing: list[_PreparedLegacyIdentityUser] = []
        for prepared in batch.users:
            stored = self._migration.get_by_id(connection, prepared.mapping_id)
            if stored is None:
                missing.append(prepared)
                continue
            key, target = self._user_target(
                batch,
                prepared,
                result_disposition=stored.details.result_disposition,
            )
            existing_results[prepared.user_id] = self._migration.put_imported(
                connection,
                mapping_id=prepared.mapping_id,
                migration_run_id=batch.migration_run_id,
                source_path_key=prepared.path_key,
                key=key,
                target=target,
                now_ms=batch.effective_at_ms,
            )

        created_org_units = 0
        if missing:
            for unit in batch.organization_units:
                created_org_units += int(
                    self._ensure_org_unit(
                        connection, unit, now_ms=batch.effective_at_ms
                    )
                )
            self._hit("legacy_identity.after_organization")

        account_inserted: dict[str, bool] = {}
        for prepared in missing:
            imported = self._users.import_legacy(connection, prepared.legacy_user)
            account_inserted[prepared.user_id] = imported.inserted
        if missing:
            self._hit("legacy_identity.after_accounts")

        assigner = self._users.require(connection, batch.assigner_user_id)
        if assigner.system_role != "admin" or assigner.account_status != "active":
            raise LegacyIdentityStateConflictError(
                "The reviewed active-administrator assigner is not currently valid."
            )

        membership_created: dict[str, bool] = {}
        for prepared in missing:
            membership_created[prepared.user_id] = self._ensure_membership(
                connection,
                prepared,
                assigner_user_id=batch.assigner_user_id,
                now_ms=batch.effective_at_ms,
            )
        leader_created: dict[str, bool] = {}
        for prepared in missing:
            leader_created[prepared.user_id] = self._ensure_leader(
                connection,
                prepared,
                assigner_user_id=batch.assigner_user_id,
                now_ms=batch.effective_at_ms,
            )
        if missing:
            self._hit("legacy_identity.after_assignments")

        space_created: dict[str, bool] = {}
        for prepared in missing:
            ensured = self._spaces.ensure_personal(
                connection,
                space_id=prepared.target_space_id,
                user_id=prepared.user_id,
                created_at_ms=batch.effective_at_ms,
            )
            space_created[prepared.user_id] = ensured.created
        if missing:
            self._hit("legacy_identity.after_spaces")

        mapping_results = dict(existing_results)
        for prepared in missing:
            disposition = (
                "created" if account_inserted[prepared.user_id] else "existing_exact"
            )
            key, target = self._user_target(
                batch, prepared, result_disposition=disposition
            )
            self._hit("migration.before_checkpoint")
            mapping_results[prepared.user_id] = self._migration.put_imported(
                connection,
                mapping_id=prepared.mapping_id,
                migration_run_id=batch.migration_run_id,
                source_path_key=prepared.path_key,
                key=key,
                target=target,
                now_ms=batch.effective_at_ms,
            )
            self._hit("legacy_identity.after_user_mapping")

        for prepared in batch.users:
            result = mapping_results[prepared.user_id]
            self._append_mapping_events(
                connection, batch=batch, record=result.record
            )
        self._hit("legacy_identity.after_user_events")

        self._hit("migration.before_checkpoint")
        closed = self._migration.put_imported(
            connection,
            mapping_id=batch.source_container_mapping_id,
            migration_run_id=batch.migration_run_id,
            source_path_key=batch.source_container_target.source_path_key,
            key=batch.source_container_key,
            target=batch.source_container_target,
            verified_blob_evidence=batch.verified_blob_evidence,
            now_ms=batch.effective_at_ms,
        )
        self._hit("legacy_identity.after_container_mapping")
        self._append_mapping_events(
            connection, batch=batch, record=closed.record
        )
        self._hit("legacy_identity.before_return")

        outcomes = tuple(
            LegacyIdentityUserOutcome(
                user_id=prepared.user_id,
                mapping_id=mapping_results[prepared.user_id].record.id,
                result_disposition=(
                    mapping_results[prepared.user_id].record.details.result_disposition
                ),
                mapping_inserted=mapping_results[prepared.user_id].inserted,
                account_inserted=account_inserted.get(prepared.user_id, False),
                personal_space_created=space_created.get(prepared.user_id, False),
                membership_created=membership_created.get(prepared.user_id, False),
                leader_assignment_created=leader_created.get(prepared.user_id, False),
            )
            for prepared in batch.users
        )
        return LegacyIdentityImportResult(
            plan_sha256=batch.plan_sha256,
            migration_run_id=batch.migration_run_id,
            request_id=batch.request_id,
            operation_id=batch.operation_id,
            source_container_mapping_id=closed.record.id,
            source_container_inserted=closed.inserted,
            created_organization_unit_count=created_org_units,
            outcomes=outcomes,
        )


__all__ = [
    "LegacyIdentityBatchLimitError",
    "LegacyIdentityImportError",
    "LegacyIdentityImportResult",
    "LegacyIdentityImporter",
    "LegacyIdentityPreparationError",
    "LegacyIdentityStateConflictError",
    "LegacyIdentityUserOutcome",
    "MAX_IDENTITY_USERS_PER_TRANSACTION",
    "USERS_REGISTRY_MEDIA_TYPE",
    "legacy_identity_operation_id_for_plan",
    "legacy_identity_request_id_for_plan",
    "prepare_legacy_identity_batch",
]
