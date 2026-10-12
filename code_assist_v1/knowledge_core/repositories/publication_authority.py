"""Immutable explicit Team/Responsibility publication authority proofs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Literal
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


HigherPublicationTargetKind = Literal["team", "responsibility"]
_PREVIEW_NAMESPACE = UUID("a49f16f4-845f-49fd-b889-53ff094a53e9")
_OPERATION_NAMESPACE = UUID("80dcd5b7-668c-4b77-8b62-7713d36a3f58")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SQLITE_MAX = 9_223_372_036_854_775_807


class HigherPublicationAuthorityRepositoryError(KnowledgeCoreDatabaseError):
    code = "HIGHER_PUBLICATION_AUTHORITY_ERROR"


class HigherPublicationAuthorityConflictError(
    HigherPublicationAuthorityRepositoryError
):
    code = "ROLE_EPISODE_STALE"


class HigherPublicationAuthorityIntegrityError(
    HigherPublicationAuthorityRepositoryError
):
    code = "HIGHER_PUBLICATION_AUTHORITY_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class HigherPublicationTargetDescriptor:
    space_id: str
    kind: HigherPublicationTargetKind
    capability: Literal["publish"]
    role_assignment_id: str
    role_assignment_generation: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "kind": self.kind,
            "role_assignment_generation": self.role_assignment_generation,
            "role_assignment_id": self.role_assignment_id,
            "space_id": self.space_id,
        }


@dataclass(frozen=True, slots=True)
class HigherPublicationPreviewAuthorityDraft:
    preview_id: str
    actor_user_id: str
    actor_scope_epoch: int
    actor_row_version: int
    descriptor: HigherPublicationTargetDescriptor
    corpus_closure_generation: int
    corpus_closure_sha256: str
    organization_snapshot_version: int
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class HigherPublicationPreviewAuthorityRecord:
    authority_id: str
    preview_id: str
    actor_user_id: str
    target_space_id: str
    target_space_kind: HigherPublicationTargetKind
    target_org_unit_id: str
    role_assignment_id: str
    role_assignment_generation: int
    role_assignment_row_version: int
    actor_scope_epoch: int
    actor_row_version: int
    corpus_closure_generation: int
    corpus_closure_sha256: str
    organization_snapshot_version: int
    descriptor: HigherPublicationTargetDescriptor
    target_descriptor_sha256: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class HigherPublicationOperationAuthorityDraft:
    publication_operation_id: str
    preview: HigherPublicationPreviewAuthorityRecord
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class HigherPublicationOperationAuthorityRecord:
    authority_id: str
    publication_operation_id: str
    preview_authority_id: str
    preview_id: str
    actor_user_id: str
    target_space_id: str
    target_space_kind: HigherPublicationTargetKind
    target_org_unit_id: str
    role_assignment_id: str
    role_assignment_generation: int
    role_assignment_row_version: int
    actor_scope_epoch: int
    actor_row_version: int
    corpus_closure_generation: int
    corpus_closure_sha256: str
    organization_snapshot_version: int
    descriptor: HigherPublicationTargetDescriptor
    target_descriptor_sha256: str
    created_at_ms: int


def _connection(connection: object, *, write: bool) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError(
            "HigherPublicationAuthorityRepository requires a ManagedConnection."
        )
    if write and not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Higher publication authority writes require a managed transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical UUID.")
    return canonical


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _integer(value: object, *, field: str, minimum: int = 1) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _SQLITE_MAX
    ):
        raise ValueError(f"{field} is outside the canonical integer domain.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256.")
    return value


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _descriptor(
    value: HigherPublicationTargetDescriptor,
) -> HigherPublicationTargetDescriptor:
    if not isinstance(value, HigherPublicationTargetDescriptor):
        raise TypeError("A HigherPublicationTargetDescriptor is required.")
    if value.kind not in {"team", "responsibility"}:
        raise ValueError("The explicit publication target kind is invalid.")
    if value.capability != "publish":
        raise ValueError("The explicit publication capability must be publish.")
    return HigherPublicationTargetDescriptor(
        space_id=_uuid(value.space_id, field="target.space_id"),
        kind=value.kind,
        capability="publish",
        role_assignment_id=_uuid(
            value.role_assignment_id, field="target.role_assignment_id"
        ),
        role_assignment_generation=_integer(
            value.role_assignment_generation,
            field="target.role_assignment_generation",
        ),
    )


def target_descriptor_sha256(value: HigherPublicationTargetDescriptor) -> str:
    descriptor = _descriptor(value)
    return sha256(_canonical(descriptor.to_mapping()).encode("utf-8")).hexdigest()


def preview_authority_id(preview_id: str) -> str:
    return str(uuid5(_PREVIEW_NAMESPACE, _uuid(preview_id, field="preview_id")))


def operation_authority_id(publication_operation_id: str) -> str:
    return str(
        uuid5(
            _OPERATION_NAMESPACE,
            _uuid(publication_operation_id, field="publication_operation_id"),
        )
    )


def _value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise HigherPublicationAuthorityIntegrityError(
            "A higher publication authority row cannot be mapped."
        ) from exc


def _stored_descriptor(value: object, expected_sha256: object) -> HigherPublicationTargetDescriptor:
    if not isinstance(value, str) or not isinstance(expected_sha256, str):
        raise HigherPublicationAuthorityIntegrityError(
            "The stored target descriptor is invalid."
        )
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise HigherPublicationAuthorityIntegrityError(
            "The stored target descriptor is invalid."
        ) from exc
    if not isinstance(parsed, dict) or set(parsed) != {
        "capability",
        "kind",
        "role_assignment_generation",
        "role_assignment_id",
        "space_id",
    } or _canonical(parsed) != value:
        raise HigherPublicationAuthorityIntegrityError(
            "The stored target descriptor is not canonical."
        )
    try:
        descriptor = _descriptor(
            HigherPublicationTargetDescriptor(
                space_id=parsed["space_id"],  # type: ignore[arg-type]
                kind=parsed["kind"],  # type: ignore[arg-type]
                capability=parsed["capability"],  # type: ignore[arg-type]
                role_assignment_id=parsed["role_assignment_id"],  # type: ignore[arg-type]
                role_assignment_generation=parsed["role_assignment_generation"],  # type: ignore[arg-type]
            )
        )
        digest = _sha(expected_sha256, field="target_descriptor_sha256")
    except (TypeError, ValueError) as exc:
        raise HigherPublicationAuthorityIntegrityError(
            "The stored target descriptor is invalid."
        ) from exc
    if target_descriptor_sha256(descriptor) != digest:
        raise HigherPublicationAuthorityIntegrityError(
            "The stored target descriptor hash is inconsistent."
        )
    return descriptor


_PREVIEW_COLUMNS = """id,preview_id,actor_user_id,target_space_id,
target_space_kind,target_org_unit_id,role_assignment_id,
role_assignment_generation,role_assignment_row_version,actor_scope_epoch,
actor_row_version,corpus_closure_generation,corpus_closure_sha256,
organization_snapshot_version,target_descriptor_json,
target_descriptor_sha256,created_at"""

_OPERATION_COLUMNS = """id,publication_operation_id,preview_authority_id,
preview_id,actor_user_id,target_space_id,target_space_kind,target_org_unit_id,
role_assignment_id,role_assignment_generation,role_assignment_row_version,
actor_scope_epoch,actor_row_version,corpus_closure_generation,
corpus_closure_sha256,organization_snapshot_version,target_descriptor_json,
target_descriptor_sha256,created_at"""


def _preview_record(row: object) -> HigherPublicationPreviewAuthorityRecord:
    descriptor = _stored_descriptor(
        _value(row, "target_descriptor_json"),
        _value(row, "target_descriptor_sha256"),
    )
    try:
        record = HigherPublicationPreviewAuthorityRecord(
            authority_id=_uuid(_value(row, "id"), field="authority_id"),
            preview_id=_uuid(_value(row, "preview_id"), field="preview_id"),
            actor_user_id=_user(_value(row, "actor_user_id")),
            target_space_id=_uuid(
                _value(row, "target_space_id"), field="target_space_id"
            ),
            target_space_kind=_value(row, "target_space_kind"),  # type: ignore[arg-type]
            target_org_unit_id=_uuid(
                _value(row, "target_org_unit_id"), field="target_org_unit_id"
            ),
            role_assignment_id=_uuid(
                _value(row, "role_assignment_id"), field="role_assignment_id"
            ),
            role_assignment_generation=_integer(
                _value(row, "role_assignment_generation"),
                field="role_assignment_generation",
            ),
            role_assignment_row_version=_integer(
                _value(row, "role_assignment_row_version"),
                field="role_assignment_row_version",
            ),
            actor_scope_epoch=_integer(
                _value(row, "actor_scope_epoch"), field="actor_scope_epoch"
            ),
            actor_row_version=_integer(
                _value(row, "actor_row_version"), field="actor_row_version"
            ),
            corpus_closure_generation=_integer(
                _value(row, "corpus_closure_generation"),
                field="corpus_closure_generation",
            ),
            corpus_closure_sha256=_sha(
                _value(row, "corpus_closure_sha256"),
                field="corpus_closure_sha256",
            ),
            organization_snapshot_version=_integer(
                _value(row, "organization_snapshot_version"),
                field="organization_snapshot_version",
            ),
            descriptor=descriptor,
            target_descriptor_sha256=_sha(
                _value(row, "target_descriptor_sha256"),
                field="target_descriptor_sha256",
            ),
            created_at_ms=_integer(
                _value(row, "created_at"), field="created_at", minimum=0
            ),
        )
    except (TypeError, ValueError) as exc:
        raise HigherPublicationAuthorityIntegrityError(
            "The preview authority row is invalid."
        ) from exc
    if (
        record.target_space_kind not in {"team", "responsibility"}
        or record.descriptor.space_id != record.target_space_id
        or record.descriptor.kind != record.target_space_kind
        or record.descriptor.role_assignment_id != record.role_assignment_id
        or record.descriptor.role_assignment_generation
        != record.role_assignment_generation
    ):
        raise HigherPublicationAuthorityIntegrityError(
            "The preview authority target binding is inconsistent."
        )
    return record


def _operation_record(row: object) -> HigherPublicationOperationAuthorityRecord:
    preview = _preview_record(row)
    return HigherPublicationOperationAuthorityRecord(
        authority_id=preview.authority_id,
        publication_operation_id=_uuid(
            _value(row, "publication_operation_id"),
            field="publication_operation_id",
        ),
        preview_authority_id=_uuid(
            _value(row, "preview_authority_id"), field="preview_authority_id"
        ),
        preview_id=preview.preview_id,
        actor_user_id=preview.actor_user_id,
        target_space_id=preview.target_space_id,
        target_space_kind=preview.target_space_kind,
        target_org_unit_id=preview.target_org_unit_id,
        role_assignment_id=preview.role_assignment_id,
        role_assignment_generation=preview.role_assignment_generation,
        role_assignment_row_version=preview.role_assignment_row_version,
        actor_scope_epoch=preview.actor_scope_epoch,
        actor_row_version=preview.actor_row_version,
        corpus_closure_generation=preview.corpus_closure_generation,
        corpus_closure_sha256=preview.corpus_closure_sha256,
        organization_snapshot_version=preview.organization_snapshot_version,
        descriptor=preview.descriptor,
        target_descriptor_sha256=preview.target_descriptor_sha256,
        created_at_ms=preview.created_at_ms,
    )


class HigherPublicationAuthorityRepository:
    __slots__ = ()

    def get_preview(
        self, connection: ManagedConnection, preview_id: str
    ) -> HigherPublicationPreviewAuthorityRecord | None:
        connection = _connection(connection, write=False)
        preview_id = _uuid(preview_id, field="preview_id")
        row = connection.execute(
            f"SELECT {_PREVIEW_COLUMNS} "
            "FROM higher_publication_preview_authority WHERE preview_id=?",
            (preview_id,),
        ).fetchone()
        return None if row is None else _preview_record(row)

    def put_preview(
        self,
        connection: ManagedConnection,
        draft: HigherPublicationPreviewAuthorityDraft,
    ) -> HigherPublicationPreviewAuthorityRecord:
        connection = _connection(connection, write=True)
        if not isinstance(draft, HigherPublicationPreviewAuthorityDraft):
            raise TypeError("A HigherPublicationPreviewAuthorityDraft is required.")
        descriptor = _descriptor(draft.descriptor)
        preview_id = _uuid(draft.preview_id, field="preview_id")
        actor = _user(draft.actor_user_id)
        epoch = _integer(draft.actor_scope_epoch, field="actor_scope_epoch")
        actor_row = _integer(draft.actor_row_version, field="actor_row_version")
        closure_generation = _integer(
            draft.corpus_closure_generation, field="corpus_closure_generation"
        )
        closure_sha = _sha(
            draft.corpus_closure_sha256, field="corpus_closure_sha256"
        )
        organization = _integer(
            draft.organization_snapshot_version,
            field="organization_snapshot_version",
        )
        created_at = _integer(draft.created_at_ms, field="created_at_ms", minimum=0)
        authority_id = preview_authority_id(preview_id)
        descriptor_json = _canonical(descriptor.to_mapping())
        descriptor_sha = target_descriptor_sha256(descriptor)
        role = connection.execute(
            """SELECT h.org_unit_id,h.row_version
                 FROM higher_scope_role_assignment h
                 JOIN knowledge_space s ON s.owner_org_unit_id=h.org_unit_id
                WHERE h.id=? AND h.user_id=? AND h.ended_at IS NULL
                  AND h.assignment_generation=? AND s.id=?
                  AND s.space_kind=?""",
            (
                descriptor.role_assignment_id,
                actor,
                descriptor.role_assignment_generation,
                descriptor.space_id,
                descriptor.kind,
            ),
        ).fetchone()
        if role is None:
            raise HigherPublicationAuthorityConflictError(
                "The explicit publication role episode is stale."
            )
        org_unit_id = _uuid(_value(role, "org_unit_id"), field="target_org_unit_id")
        role_row_version = _integer(
            _value(role, "row_version"), field="role_assignment_row_version"
        )
        existing = self.get_preview(connection, preview_id)
        expected = (
            authority_id,
            actor,
            descriptor.space_id,
            descriptor.kind,
            org_unit_id,
            descriptor.role_assignment_id,
            descriptor.role_assignment_generation,
            role_row_version,
            epoch,
            actor_row,
            closure_generation,
            closure_sha,
            organization,
            descriptor,
            descriptor_sha,
            created_at,
        )
        if existing is not None:
            actual = (
                existing.authority_id,
                existing.actor_user_id,
                existing.target_space_id,
                existing.target_space_kind,
                existing.target_org_unit_id,
                existing.role_assignment_id,
                existing.role_assignment_generation,
                existing.role_assignment_row_version,
                existing.actor_scope_epoch,
                existing.actor_row_version,
                existing.corpus_closure_generation,
                existing.corpus_closure_sha256,
                existing.organization_snapshot_version,
                existing.descriptor,
                existing.target_descriptor_sha256,
                existing.created_at_ms,
            )
            if actual != expected:
                raise HigherPublicationAuthorityConflictError(
                    "The preview authority ID is already bound incompatibly."
                )
            return existing
        try:
            connection.execute(
                """INSERT INTO higher_publication_preview_authority(
                     id,preview_id,actor_user_id,target_space_id,target_space_kind,
                     target_org_unit_id,role_assignment_id,
                     role_assignment_generation,role_assignment_row_version,
                     actor_scope_epoch,actor_row_version,
                     corpus_closure_generation,corpus_closure_sha256,
                     organization_snapshot_version,target_descriptor_json,
                     target_descriptor_sha256,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    authority_id,
                    preview_id,
                    actor,
                    descriptor.space_id,
                    descriptor.kind,
                    org_unit_id,
                    descriptor.role_assignment_id,
                    descriptor.role_assignment_generation,
                    role_row_version,
                    epoch,
                    actor_row,
                    closure_generation,
                    closure_sha,
                    organization,
                    descriptor_json,
                    descriptor_sha,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HigherPublicationAuthorityConflictError(
                "The preview authority proof is no longer current."
            ) from exc
        result = self.get_preview(connection, preview_id)
        if result is None:
            raise HigherPublicationAuthorityIntegrityError(
                "The preview authority proof was not stored."
            )
        return result

    def get_operation(
        self, connection: ManagedConnection, publication_operation_id: str
    ) -> HigherPublicationOperationAuthorityRecord | None:
        connection = _connection(connection, write=False)
        operation_id = _uuid(
            publication_operation_id, field="publication_operation_id"
        )
        row = connection.execute(
            f"SELECT {_OPERATION_COLUMNS} "
            "FROM higher_publication_operation_authority "
            "WHERE publication_operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else _operation_record(row)

    def put_operation(
        self,
        connection: ManagedConnection,
        draft: HigherPublicationOperationAuthorityDraft,
    ) -> HigherPublicationOperationAuthorityRecord:
        connection = _connection(connection, write=True)
        if not isinstance(draft, HigherPublicationOperationAuthorityDraft):
            raise TypeError("A HigherPublicationOperationAuthorityDraft is required.")
        publication_operation_id = _uuid(
            draft.publication_operation_id, field="publication_operation_id"
        )
        preview = draft.preview
        if not isinstance(preview, HigherPublicationPreviewAuthorityRecord):
            raise TypeError("An exact preview authority record is required.")
        created_at = _integer(draft.created_at_ms, field="created_at_ms", minimum=0)
        authority_id = operation_authority_id(publication_operation_id)
        existing = self.get_operation(connection, publication_operation_id)
        if existing is not None:
            if (
                existing.authority_id != authority_id
                or existing.preview_authority_id != preview.authority_id
                or existing.preview_id != preview.preview_id
                or existing.actor_user_id != preview.actor_user_id
                or existing.target_space_id != preview.target_space_id
                or existing.target_space_kind != preview.target_space_kind
                or existing.target_org_unit_id != preview.target_org_unit_id
                or existing.role_assignment_id != preview.role_assignment_id
                or existing.role_assignment_generation
                != preview.role_assignment_generation
                or existing.role_assignment_row_version
                != preview.role_assignment_row_version
                or existing.actor_scope_epoch != preview.actor_scope_epoch
                or existing.actor_row_version != preview.actor_row_version
                or existing.corpus_closure_generation
                != preview.corpus_closure_generation
                or existing.corpus_closure_sha256
                != preview.corpus_closure_sha256
                or existing.organization_snapshot_version
                != preview.organization_snapshot_version
                or existing.descriptor != preview.descriptor
                or existing.target_descriptor_sha256
                != preview.target_descriptor_sha256
                or existing.created_at_ms != created_at
            ):
                raise HigherPublicationAuthorityConflictError(
                    "The operation authority ID is already bound incompatibly."
                )
            return existing
        descriptor_json = _canonical(preview.descriptor.to_mapping())
        try:
            connection.execute(
                """INSERT INTO higher_publication_operation_authority(
                     id,publication_operation_id,preview_authority_id,preview_id,
                     actor_user_id,target_space_id,target_space_kind,
                     target_org_unit_id,role_assignment_id,
                     role_assignment_generation,role_assignment_row_version,
                     actor_scope_epoch,actor_row_version,
                     corpus_closure_generation,corpus_closure_sha256,
                     organization_snapshot_version,target_descriptor_json,
                     target_descriptor_sha256,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    authority_id,
                    publication_operation_id,
                    preview.authority_id,
                    preview.preview_id,
                    preview.actor_user_id,
                    preview.target_space_id,
                    preview.target_space_kind,
                    preview.target_org_unit_id,
                    preview.role_assignment_id,
                    preview.role_assignment_generation,
                    preview.role_assignment_row_version,
                    preview.actor_scope_epoch,
                    preview.actor_row_version,
                    preview.corpus_closure_generation,
                    preview.corpus_closure_sha256,
                    preview.organization_snapshot_version,
                    descriptor_json,
                    preview.target_descriptor_sha256,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HigherPublicationAuthorityConflictError(
                "The operation authority proof is stale or inconsistent."
            ) from exc
        result = self.get_operation(connection, publication_operation_id)
        if result is None:
            raise HigherPublicationAuthorityIntegrityError(
                "The operation authority proof was not stored."
            )
        return result


__all__ = [
    "HigherPublicationAuthorityConflictError",
    "HigherPublicationAuthorityIntegrityError",
    "HigherPublicationAuthorityRepository",
    "HigherPublicationAuthorityRepositoryError",
    "HigherPublicationOperationAuthorityDraft",
    "HigherPublicationOperationAuthorityRecord",
    "HigherPublicationPreviewAuthorityDraft",
    "HigherPublicationPreviewAuthorityRecord",
    "HigherPublicationTargetDescriptor",
    "HigherPublicationTargetKind",
    "operation_authority_id",
    "preview_authority_id",
    "target_descriptor_sha256",
]
