"""Connection-scoped immutable item/revision persistence.

P2.3 uses this module only after source-artifact/extraction bundles have been
verified and inserted in the same service-owned transaction.  It imports one
legacy Markdown body into an owner's personal space, or adds deterministic
``migrated`` provenance to an already selected exact-body item.  It performs no
filesystem I/O, hashing, normalization, authorization, mapping, audit, outbox,
FTS, publication, finding, or transaction-finalization work.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Literal, Sequence
from uuid import UUID, uuid5

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .artifacts import CAS_BLOB_MEDIA_TYPE, PreparedMarkdownBody


LegacyMarkdownDisposition = Literal["created", "provenance_added", "no_op"]
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$", re.ASCII)
_ITEM_NAMESPACE = UUID("b2926cb9-91cd-45ce-938b-a9c5d91a0f9e")
_REVISION_NAMESPACE = UUID("1eb390a8-162f-47d9-a19e-4fe61ce6a0ae")
_ITEM_SOURCE_NAMESPACE = UUID("6ce6aa7d-6d07-482a-8084-26eece89505f")
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807


class ItemRepositoryError(KnowledgeCoreDatabaseError):
    code = "ITEM_REPOSITORY_ERROR"


class ItemBundleConflictError(ItemRepositoryError):
    code = "ITEM_BUNDLE_CONFLICT"


class ItemBundleIntegrityError(ItemRepositoryError):
    code = "ITEM_BUNDLE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyItemIds:
    item_id: str
    revision_id: str
    item_source_id: str


@dataclass(frozen=True, slots=True)
class LegacyMarkdownImportBundle:
    plan_sha256: str
    inventory_record_id: str
    ids: LegacyItemIds
    owner_user_id: str
    personal_space_id: str
    artifact_id: str
    extraction_snapshot_id: str
    title: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str
    metadata_json: str
    change_reason: str
    body: PreparedMarkdownBody
    created_at_ms: int
    target_item_id: str | None = None
    target_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyMarkdownImportResult:
    disposition: LegacyMarkdownDisposition
    item_id: str
    revision_id: str
    item_source_id: str
    inserted_item: bool
    inserted_source: bool


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical lowercase UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return canonical


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _record_id(value: object) -> str:
    if not isinstance(value, str) or _RECORD_RE.fullmatch(value) is None:
        raise ValueError("inventory_record_id is invalid.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("owner_user_id is invalid.")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.strip()) <= maximum
        or "\x00" in value
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _size(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError("content_size_bytes must be a non-negative integer.")
    return value


def _utc_ms(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _SQLITE_INTEGER_MAX
    ):
        raise ValueError("created_at_ms must be a non-negative UTC millisecond integer.")
    return value


def _canonical_object(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("metadata_json must be a canonical JSON object.")
    try:
        parsed = json.loads(value)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise ValueError("metadata_json must be a canonical JSON object.") from None
    if type(parsed) is not dict or canonical != value:
        raise ValueError("metadata_json must be a canonical JSON object.")
    return value


def _prepare_body(value: PreparedMarkdownBody) -> PreparedMarkdownBody:
    if not isinstance(value, PreparedMarkdownBody):
        raise TypeError("A PreparedMarkdownBody is required.")
    if (value.content_md is None) == (value.content_blob_hash is None):
        raise ValueError("A Markdown body requires exactly one storage form.")
    if value.content_md is not None:
        if not isinstance(value.content_md, str) or "\x00" in value.content_md:
            raise ValueError("The prepared inline Markdown body is invalid.")
        content_md = value.content_md
        content_blob_hash = None
    else:
        content_md = None
        content_blob_hash = _sha(
            value.content_blob_hash, field="content_blob_hash"  # type: ignore[arg-type]
        )
    return PreparedMarkdownBody(
        content_md=content_md,
        content_blob_hash=content_blob_hash,
        content_size_bytes=_size(value.content_size_bytes),
        content_sha256=_sha(value.content_sha256, field="content_sha256"),
    )


def _id_for(namespace: UUID, plan_sha256: str, record_id: str) -> str:
    plan_sha256 = _sha(plan_sha256, field="plan_sha256")
    return str(uuid5(namespace, f"p2.3:{plan_sha256}:{record_id}"))


def legacy_item_ids_for_record(
    plan_sha256: str, inventory_record_id: str
) -> LegacyItemIds:
    record_id = _record_id(inventory_record_id)
    return LegacyItemIds(
        item_id=_id_for(_ITEM_NAMESPACE, plan_sha256, record_id),
        revision_id=_id_for(_REVISION_NAMESPACE, plan_sha256, record_id),
        item_source_id=_id_for(_ITEM_SOURCE_NAMESPACE, plan_sha256, record_id),
    )


def _prepare_bundle(value: LegacyMarkdownImportBundle) -> LegacyMarkdownImportBundle:
    if not isinstance(value, LegacyMarkdownImportBundle):
        raise TypeError("A LegacyMarkdownImportBundle is required.")
    plan_sha = _sha(value.plan_sha256, field="plan_sha256")
    record_id = _record_id(value.inventory_record_id)
    ids = legacy_item_ids_for_record(plan_sha, record_id)
    if value.ids != ids:
        raise ValueError("Item IDs do not match the reviewed record.")
    owner = _user(value.owner_user_id)
    space_id = _uuid(value.personal_space_id, field="personal_space_id")
    artifact_id = _uuid(value.artifact_id, field="artifact_id")
    extraction_id = _uuid(
        value.extraction_snapshot_id, field="extraction_snapshot_id"
    )
    title = _text(value.title, field="title", maximum=500)
    raw_sha = _sha(value.raw_content_sha256, field="raw_content_sha256")
    normalized_sha = _sha(
        value.normalized_content_sha256,
        field="normalized_content_sha256",
    )
    size = _size(value.content_size_bytes)
    normalizer = _text(
        value.normalizer_version, field="normalizer_version", maximum=80
    )
    if normalizer != "markdown_lf_v1":
        raise ValueError("Legacy Markdown requires the frozen normalizer.")
    metadata = _canonical_object(value.metadata_json)
    change_reason = _text(
        value.change_reason, field="change_reason", maximum=2000
    )
    body = _prepare_body(value.body)
    if (
        body.content_sha256 != raw_sha
        or body.content_size_bytes != size
        or (body.content_blob_hash is not None and body.content_blob_hash != raw_sha)
    ):
        raise ValueError("Revision body evidence does not match revision metadata.")
    created_at = _utc_ms(value.created_at_ms)
    target_item = value.target_item_id
    target_revision = value.target_revision_id
    if (target_item is None) != (target_revision is None):
        raise ValueError("Existing exact target IDs must be supplied together.")
    if target_item is not None:
        target_item = _uuid(target_item, field="target_item_id")
        target_revision = _uuid(
            target_revision, field="target_revision_id"  # type: ignore[arg-type]
        )
    return LegacyMarkdownImportBundle(
        plan_sha,
        record_id,
        ids,
        owner,
        space_id,
        artifact_id,
        extraction_id,
        title,
        raw_sha,
        normalized_sha,
        size,
        normalizer,
        metadata,
        change_reason,
        body,
        created_at,
        target_item,
        target_revision,
    )


def _row_tuple(row: object | None, fields: Sequence[str]) -> tuple[object, ...] | None:
    if row is None:
        return None
    try:
        return tuple(row[field] for field in fields)  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise ItemBundleIntegrityError(
            "A canonical item row cannot be mapped."
        ) from exc


def _body_identity(body: PreparedMarkdownBody) -> tuple[object, ...]:
    return (
        body.content_md,
        body.content_blob_hash,
        body.content_size_bytes,
        body.content_sha256,
    )


class ItemRepository:
    """Insert one imported item or attach one exact migrated provenance row."""

    __slots__ = ()

    @staticmethod
    def _connection(connection: ManagedConnection) -> ManagedConnection:
        if not isinstance(connection, ManagedConnection):
            raise TypeError("ItemRepository requires a ManagedConnection.")
        if not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "Item repository writes require an existing managed transaction."
            )
        return connection

    @staticmethod
    def _verify_source(
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
    ) -> None:
        source = connection.execute(
            """SELECT artifact.raw_sha256,artifact.raw_size_bytes,
                      artifact.media_type AS artifact_media_type,
                      artifact.publisher_user_id,artifact.body_retention_status,
                      manifest.content_sha256,manifest.size_bytes,
                      manifest.media_type AS manifest_media_type,
                      manifest.body_retention_status AS manifest_retention,
                      link.content_sha256 AS linked_sha256,
                      blob.size_bytes AS blob_size_bytes,
                      blob.media_type AS blob_media_type,
                      blob.verification_status,blob.verified_at
               FROM source_artifact AS artifact
               JOIN artifact_blob AS manifest
                 ON manifest.artifact_id=artifact.id
                AND manifest.role='raw' AND manifest.ordinal=0
               JOIN artifact_blob_body AS link
                 ON link.artifact_blob_id=manifest.id
               JOIN blob_object AS blob ON blob.sha256=link.content_sha256
               WHERE artifact.id=? AND artifact.origin_type='legacy'""",
            (bundle.artifact_id,),
        ).fetchone()
        if (
            source is None
            or source["raw_sha256"] != bundle.raw_content_sha256  # type: ignore[index]
            or source["raw_size_bytes"] != bundle.content_size_bytes  # type: ignore[index]
            or not isinstance(source["artifact_media_type"], str)  # type: ignore[index]
            or not source["artifact_media_type"]  # type: ignore[index]
            or source["publisher_user_id"] != bundle.owner_user_id  # type: ignore[index]
            or source["body_retention_status"] != "present"  # type: ignore[index]
            or source["content_sha256"] != bundle.raw_content_sha256  # type: ignore[index]
            or source["size_bytes"] != bundle.content_size_bytes  # type: ignore[index]
            or source["manifest_media_type"] != source["artifact_media_type"]  # type: ignore[index]
            or source["manifest_retention"] != "present"  # type: ignore[index]
            or source["linked_sha256"] != bundle.raw_content_sha256  # type: ignore[index]
            or source["blob_size_bytes"] != bundle.content_size_bytes  # type: ignore[index]
            or source["blob_media_type"] != CAS_BLOB_MEDIA_TYPE  # type: ignore[index]
            or source["verification_status"] != "verified"  # type: ignore[index]
            or type(source["verified_at"]) is not int  # type: ignore[index]
        ):
            raise ItemBundleConflictError(
                "The legacy Markdown source artifact is not exact."
            )
        extraction = connection.execute(
            """SELECT snapshot.status,snapshot.extracted_sha256,
                      snapshot.normalized_content_sha256,
                      snapshot.extracted_size_bytes,snapshot.normalizer_version,
                      snapshot.body_retention_status,body.content_md,
                      body.content_blob_hash,body.content_size_bytes,
                      body.content_sha256
               FROM extraction_snapshot AS snapshot
               JOIN extraction_snapshot_body AS body
                 ON body.snapshot_id=snapshot.id
                AND body.artifact_id=snapshot.artifact_id
               WHERE snapshot.id=? AND snapshot.artifact_id=?""",
            (bundle.extraction_snapshot_id, bundle.artifact_id),
        ).fetchone()
        if extraction is None or _row_tuple(
            extraction,
            (
                "status",
                "extracted_sha256",
                "normalized_content_sha256",
                "extracted_size_bytes",
                "normalizer_version",
                "body_retention_status",
                "content_md",
                "content_blob_hash",
                "content_size_bytes",
                "content_sha256",
            ),
        ) != (
            "ready",
            bundle.raw_content_sha256,
            bundle.normalized_content_sha256,
            bundle.content_size_bytes,
            bundle.normalizer_version,
            "present",
            *_body_identity(bundle.body),
        ):
            raise ItemBundleConflictError(
                "The legacy Markdown extraction is not exact."
            )
        if bundle.body.content_blob_hash is not None:
            body_blob = connection.execute(
                """SELECT size_bytes,verification_status,verified_at
                   FROM blob_object WHERE sha256=?""",
                (bundle.body.content_blob_hash,),
            ).fetchone()
            if (
                body_blob is None
                or body_blob["size_bytes"] != bundle.content_size_bytes  # type: ignore[index]
                or body_blob["verification_status"] != "verified"  # type: ignore[index]
                or type(body_blob["verified_at"]) is not int  # type: ignore[index]
            ):
                raise ItemBundleConflictError(
                    "The legacy Markdown body blob is not exact."
                )

    @staticmethod
    def _verify_space(
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
    ) -> None:
        row = connection.execute(
            """SELECT 1 FROM knowledge_space
               WHERE id=? AND space_kind='personal' AND owner_user_id=?""",
            (bundle.personal_space_id, bundle.owner_user_id),
        ).fetchone()
        if row is None:
            raise ItemBundleConflictError(
                "The legacy item target is not the owner's personal space."
            )

    @staticmethod
    def _find_exact_target(
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
    ) -> tuple[str, str] | None:
        """Resolve one exact current imported body in the same personal space.

        Normalized equality alone is deliberately insufficient.  Inline bodies
        compare the already-prepared text exactly; blob-backed bodies compare
        the exact immutable content hash/link.  More than one candidate is an
        ambiguity that the importer must not resolve by row order.
        """

        rows = connection.execute(
            """SELECT item.id AS item_id,revision.id AS revision_id
               FROM knowledge_item AS item
               JOIN knowledge_revision AS revision
                 ON revision.id=item.current_revision_id
                AND revision.item_id=item.id
               JOIN knowledge_revision_body AS body
                 ON body.revision_id=revision.id AND body.item_id=item.id
               WHERE item.space_id=? AND item.lineage_group_id IS NULL
                 AND item.lifecycle_status='active'
                 AND item.curation_status='provisional'
                 AND item.last_curation_transition_id IS NULL
                 AND item.latest_deactivation_episode_id IS NULL
                 AND item.redirect_item_id IS NULL
                 AND item.created_by=? AND item.updated_by=?
                 AND item.row_version=1
                 AND revision.revision_no=1
                 AND revision.parent_revision_id IS NULL
                 AND revision.base_revision_id IS NULL
                 AND revision.raw_content_sha256=?
                 AND revision.normalized_content_sha256=?
                 AND revision.content_size_bytes=?
                 AND revision.normalizer_version=?
                 AND revision.change_kind='import'
                 AND revision.created_by=?
                 AND body.content_md IS ?
                 AND body.content_blob_hash IS ?
                 AND body.content_size_bytes=?
                 AND body.content_sha256=?
               ORDER BY item.id,revision.id""",
            (
                bundle.personal_space_id,
                bundle.owner_user_id,
                bundle.owner_user_id,
                bundle.raw_content_sha256,
                bundle.normalized_content_sha256,
                bundle.content_size_bytes,
                bundle.normalizer_version,
                bundle.owner_user_id,
                bundle.body.content_md,
                bundle.body.content_blob_hash,
                bundle.body.content_size_bytes,
                bundle.body.content_sha256,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise ItemBundleConflictError(
                "More than one exact imported body exists in the personal space."
            )
        if not rows:
            return None
        return (
            _uuid(rows[0]["item_id"], field="item_id"),
            _uuid(rows[0]["revision_id"], field="revision_id"),
        )

    @staticmethod
    def _preflight_source_link(
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
        *,
        item_id: str,
        revision_id: str,
    ) -> Literal["insert", "replay"]:
        by_id = connection.execute(
            """SELECT id,item_id,revision_id,artifact_id,
                      extraction_snapshot_id,role,created_by,created_at
               FROM item_source WHERE id=?""",
            (bundle.ids.item_source_id,),
        ).fetchone()
        by_identity = connection.execute(
            """SELECT id FROM item_source
               WHERE item_id=? AND revision_id=? AND artifact_id=?
                 AND extraction_snapshot_id=? AND role='migrated'""",
            (
                item_id,
                revision_id,
                bundle.artifact_id,
                bundle.extraction_snapshot_id,
            ),
        ).fetchone()
        if by_id is None and by_identity is None:
            return "insert"
        expected = (
            bundle.ids.item_source_id,
            item_id,
            revision_id,
            bundle.artifact_id,
            bundle.extraction_snapshot_id,
            "migrated",
            bundle.owner_user_id,
            bundle.created_at_ms,
        )
        if (
            by_id is None
            or by_identity is None
            or by_identity["id"] != bundle.ids.item_source_id  # type: ignore[index]
            or _row_tuple(
                by_id,
                (
                    "id",
                    "item_id",
                    "revision_id",
                    "artifact_id",
                    "extraction_snapshot_id",
                    "role",
                    "created_by",
                    "created_at",
                ),
            )
            != expected
        ):
            raise ItemBundleConflictError(
                "The migrated source identity is already bound incompatibly."
            )
        return "replay"

    @staticmethod
    def _verify_existing_target(
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
        *,
        item_id: str,
        revision_id: str,
        deterministic_replay: bool,
    ) -> None:
        item = connection.execute(
            """SELECT id,space_id,lineage_group_id,lifecycle_status,
                      curation_status,current_revision_id,
                      last_curation_transition_id,
                      latest_deactivation_episode_id,redirect_item_id,
                      created_by,updated_by,created_at,updated_at,row_version
               FROM knowledge_item WHERE id=?""",
            (item_id,),
        ).fetchone()
        revision = connection.execute(
            """SELECT id,item_id,revision_no,parent_revision_id,base_revision_id,
                      title,raw_content_sha256,normalized_content_sha256,
                      content_size_bytes,normalizer_version,metadata_json,
                      change_kind,change_reason,created_by,created_at
               FROM knowledge_revision WHERE id=?""",
            (revision_id,),
        ).fetchone()
        body = connection.execute(
            """SELECT revision_id,item_id,content_md,content_blob_hash,
                      content_size_bytes,content_sha256,created_at
               FROM knowledge_revision_body WHERE revision_id=?""",
            (revision_id,),
        ).fetchone()
        lease = connection.execute(
            """SELECT fencing_generation,holder_user_id
               FROM item_lease WHERE item_id=?""",
            (item_id,),
        ).fetchone()
        if item is None or revision is None or body is None or lease is None:
            raise ItemBundleConflictError(
                "The exact-body target is only partially present."
            )
        common_item = (
            item["space_id"] == bundle.personal_space_id  # type: ignore[index]
            and item["lineage_group_id"] is None  # type: ignore[index]
            and item["lifecycle_status"] == "active"  # type: ignore[index]
            and item["curation_status"] == "provisional"  # type: ignore[index]
            and item["current_revision_id"] == revision_id  # type: ignore[index]
            and item["last_curation_transition_id"] is None  # type: ignore[index]
            and item["latest_deactivation_episode_id"] is None  # type: ignore[index]
            and item["redirect_item_id"] is None  # type: ignore[index]
            and item["created_by"] == bundle.owner_user_id  # type: ignore[index]
            and item["updated_by"] == bundle.owner_user_id  # type: ignore[index]
            and item["row_version"] == 1  # type: ignore[index]
            and lease["fencing_generation"] == 0  # type: ignore[index]
            and lease["holder_user_id"] is None  # type: ignore[index]
        )
        common_revision = _row_tuple(
            revision,
            (
                "id",
                "item_id",
                "revision_no",
                "parent_revision_id",
                "base_revision_id",
                "raw_content_sha256",
                "normalized_content_sha256",
                "content_size_bytes",
                "normalizer_version",
                "change_kind",
                "created_by",
            ),
        ) == (
            revision_id,
            item_id,
            1,
            None,
            None,
            bundle.raw_content_sha256,
            bundle.normalized_content_sha256,
            bundle.content_size_bytes,
            bundle.normalizer_version,
            "import",
            bundle.owner_user_id,
        )
        common_body = _row_tuple(
            body,
            (
                "revision_id",
                "item_id",
                "content_md",
                "content_blob_hash",
                "content_size_bytes",
                "content_sha256",
            ),
        ) == (revision_id, item_id, *_body_identity(bundle.body))
        if not (common_item and common_revision and common_body):
            raise ItemBundleConflictError(
                "The selected exact-body target is not compatible."
            )
        if deterministic_replay and (
            _row_tuple(
                item,
                ("created_at", "updated_at"),
            )
            != (bundle.created_at_ms, bundle.created_at_ms)
            or _row_tuple(
                revision,
                ("title", "metadata_json", "change_reason", "created_at"),
            )
            != (
                bundle.title,
                bundle.metadata_json,
                bundle.change_reason,
                bundle.created_at_ms,
            )
            or body["created_at"] != bundle.created_at_ms  # type: ignore[index]
        ):
            raise ItemBundleConflictError(
                "The deterministic legacy item is already bound incompatibly."
            )

    def put_legacy_markdown_import(
        self,
        connection: ManagedConnection,
        bundle: LegacyMarkdownImportBundle,
    ) -> LegacyMarkdownImportResult:
        connection = self._connection(connection)
        bundle = _prepare_bundle(bundle)
        self._verify_space(connection, bundle)
        self._verify_source(connection, bundle)

        explicit_target = bundle.target_item_id is not None
        deterministic_row = connection.execute(
            "SELECT 1 FROM knowledge_item WHERE id=?", (bundle.ids.item_id,)
        ).fetchone()
        if explicit_target:
            item_id = str(bundle.target_item_id)
            revision_id = str(bundle.target_revision_id)
            deterministic_replay = False
        elif deterministic_row is not None:
            item_id = bundle.ids.item_id
            revision_id = bundle.ids.revision_id
            deterministic_replay = True
        else:
            exact_target = self._find_exact_target(connection, bundle)
            if exact_target is None:
                item_id = bundle.ids.item_id
                revision_id = bundle.ids.revision_id
            else:
                item_id, revision_id = exact_target
            deterministic_replay = False
        item = connection.execute(
            "SELECT 1 FROM knowledge_item WHERE id=?", (item_id,)
        ).fetchone()
        revision = connection.execute(
            "SELECT 1 FROM knowledge_revision WHERE id=?", (revision_id,)
        ).fetchone()
        revision_body = connection.execute(
            "SELECT 1 FROM knowledge_revision_body WHERE revision_id=?",
            (revision_id,),
        ).fetchone()
        lease = connection.execute(
            "SELECT 1 FROM item_lease WHERE item_id=?", (item_id,)
        ).fetchone()

        if item is None:
            if explicit_target:
                raise ItemBundleConflictError(
                    "The selected exact-body target does not exist."
                )
            if revision is not None or revision_body is not None or lease is not None:
                raise ItemBundleConflictError(
                    "A deterministic item identity is already partially present."
                )
            source_action = self._preflight_source_link(
                connection,
                bundle,
                item_id=item_id,
                revision_id=revision_id,
            )
            if source_action != "insert":
                raise ItemBundleConflictError(
                    "A deterministic item source exists without its item."
                )
            try:
                # The deferred current-revision FK plus the item seed trigger
                # require this order: item points at the fixed import revision,
                # then revision/body/source complete the same outer transaction.
                connection.execute(
                    """INSERT INTO knowledge_item(
                         id,space_id,lineage_group_id,lifecycle_status,
                         curation_status,current_revision_id,
                         last_curation_transition_id,
                         latest_deactivation_episode_id,redirect_item_id,
                         created_by,updated_by,created_at,updated_at,row_version
                       ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                         ?,?,?,?,1)""",
                    (
                        item_id,
                        bundle.personal_space_id,
                        revision_id,
                        bundle.owner_user_id,
                        bundle.owner_user_id,
                        bundle.created_at_ms,
                        bundle.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision(
                         id,item_id,revision_no,parent_revision_id,
                         base_revision_id,title,raw_content_sha256,
                         normalized_content_sha256,content_size_bytes,
                         normalizer_version,metadata_json,change_kind,
                         change_reason,created_by,created_at
                       ) VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,'import',?,?,?)""",
                    (
                        revision_id,
                        item_id,
                        bundle.title,
                        bundle.raw_content_sha256,
                        bundle.normalized_content_sha256,
                        bundle.content_size_bytes,
                        bundle.normalizer_version,
                        bundle.metadata_json,
                        bundle.change_reason,
                        bundle.owner_user_id,
                        bundle.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO knowledge_revision_body(
                         revision_id,item_id,content_md,content_blob_hash,
                         content_size_bytes,content_sha256,created_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        revision_id,
                        item_id,
                        bundle.body.content_md,
                        bundle.body.content_blob_hash,
                        bundle.body.content_size_bytes,
                        bundle.body.content_sha256,
                        bundle.created_at_ms,
                    ),
                )
                connection.execute(
                    """INSERT INTO item_source(
                         id,item_id,revision_id,artifact_id,
                         extraction_snapshot_id,role,created_by,created_at
                       ) VALUES (?,?,?,?,?,'migrated',?,?)""",
                    (
                        bundle.ids.item_source_id,
                        item_id,
                        revision_id,
                        bundle.artifact_id,
                        bundle.extraction_snapshot_id,
                        bundle.owner_user_id,
                        bundle.created_at_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ItemBundleConflictError(
                    "The legacy item bundle violates canonical constraints."
                ) from exc
            return LegacyMarkdownImportResult(
                "created",
                item_id,
                revision_id,
                bundle.ids.item_source_id,
                True,
                True,
            )

        if revision is None or revision_body is None or lease is None:
            raise ItemBundleConflictError(
                "The selected exact-body target is only partially present."
            )
        self._verify_existing_target(
            connection,
            bundle,
            item_id=item_id,
            revision_id=revision_id,
            deterministic_replay=deterministic_replay,
        )
        source_action = self._preflight_source_link(
            connection,
            bundle,
            item_id=item_id,
            revision_id=revision_id,
        )
        if source_action == "replay":
            return LegacyMarkdownImportResult(
                "no_op",
                item_id,
                revision_id,
                bundle.ids.item_source_id,
                False,
                False,
            )
        try:
            connection.execute(
                """INSERT INTO item_source(
                     id,item_id,revision_id,artifact_id,
                     extraction_snapshot_id,role,created_by,created_at
                   ) VALUES (?,?,?,?,?,'migrated',?,?)""",
                (
                    bundle.ids.item_source_id,
                    item_id,
                    revision_id,
                    bundle.artifact_id,
                    bundle.extraction_snapshot_id,
                    bundle.owner_user_id,
                    bundle.created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ItemBundleConflictError(
                "The migrated provenance violates canonical constraints."
            ) from exc
        return LegacyMarkdownImportResult(
            "provenance_added",
            item_id,
            revision_id,
            bundle.ids.item_source_id,
            False,
            True,
        )


__all__ = [
    "ItemBundleConflictError",
    "ItemBundleIntegrityError",
    "ItemRepository",
    "ItemRepositoryError",
    "LegacyItemIds",
    "LegacyMarkdownDisposition",
    "LegacyMarkdownImportBundle",
    "LegacyMarkdownImportResult",
    "legacy_item_ids_for_record",
]
