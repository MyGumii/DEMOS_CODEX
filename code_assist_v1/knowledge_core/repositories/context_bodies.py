"""Snapshot-bound canonical body metadata and one-at-a-time materialization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from ..db import ManagedConnection
from .retrieval import _connection, _uuid


class ContextBodyUnavailableError(ValueError):
    """Selected evidence is no longer current, published or in the closure."""


@dataclass(frozen=True, slots=True)
class ContextBodyMetadata:
    item_id: str
    revision_id: str
    source_space_id: str
    size_bytes: int
    content_sha256: str
    content_blob_hash: str | None = None


class ContextBodyRepository:
    def metadata(
        self,
        connection: ManagedConnection,
        *,
        identities: tuple[tuple[str, str, str], ...],
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
    ) -> dict[tuple[str, str], ContextBodyMetadata]:
        _connection(connection)
        wanted = [
            {"item": _uuid(item, "item_id"), "revision": _uuid(revision, "revision_id"),
             "space": _uuid(space, "source_space_id")}
            for item, revision, space in identities
        ]
        rows = connection.execute(
            """WITH wanted AS (
                 SELECT json_extract(value,'$.item') item_id,
                        json_extract(value,'$.revision') revision_id,
                        json_extract(value,'$.space') space_id FROM json_each(?)
               ), sources AS (SELECT value space_id FROM json_each(?)),
               overlays AS (
                 SELECT json_extract(value,'$.source_item_id') item_id,
                        json_extract(value,'$.source_revision_id') revision_id,
                        json_extract(value,'$.source_space_id') space_id
                   FROM json_each(?)
               )
               SELECT DISTINCT item.id,body.revision_id,item.space_id,
                      body.content_size_bytes size_bytes,
                      body.content_sha256,body.content_blob_hash
                 FROM wanted
                 JOIN knowledge_item item ON item.id=wanted.item_id
                  AND item.space_id=wanted.space_id
                  AND item.current_revision_id=wanted.revision_id
                 JOIN knowledge_space space ON space.id=item.space_id
                 JOIN knowledge_revision_body body ON body.item_id=item.id
                  AND body.revision_id=wanted.revision_id
                WHERE item.lifecycle_status='active'
                  AND item.curation_status='published' AND space.status='active'
                  AND (body.content_md IS NOT NULL OR body.content_blob_hash IS NOT NULL)
                  AND (EXISTS (SELECT 1 FROM sources WHERE sources.space_id=item.space_id)
                    OR EXISTS (SELECT 1 FROM overlays WHERE overlays.item_id=item.id
                         AND overlays.revision_id=body.revision_id
                         AND overlays.space_id=item.space_id))""",
            (json.dumps(wanted), json.dumps(source_space_ids), json.dumps(overlays)),
        ).fetchall()
        result = {
            (str(row["id"]), str(row["revision_id"])): ContextBodyMetadata(
                str(row["id"]), str(row["revision_id"]), str(row["space_id"]),
                int(row["size_bytes"]), str(row["content_sha256"]),
                row["content_blob_hash"],
            ) for row in rows
        }
        if any((item, revision) not in result or result[item, revision].source_space_id != space
               for item, revision, space in identities):
            raise ContextBodyUnavailableError("A selected canonical body is unavailable.")
        return result

    def reference_metadata(
        self,
        connection: ManagedConnection,
        *,
        references: tuple[tuple[str, str, str, str], ...],
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
    ) -> dict[str, ContextBodyMetadata]:
        """Reauthorize fixed references; their target revision may be historical.

        The reference itself must still be available and bound to the selected
        source revision. This is deliberately separate from current-body reads.
        """
        _connection(connection)
        if not references:
            return {}
        wanted = [{"reference": _uuid(ref, "reference_id"),
                   "source": _uuid(source, "source_revision_id"),
                   "item": _uuid(item, "target_item_id"),
                   "revision": _uuid(revision, "target_revision_id")}
                  for ref, source, item, revision in references]
        rows = connection.execute(
            """WITH wanted AS (
                 SELECT json_extract(value,'$.reference') reference_id,
                        json_extract(value,'$.source') source_revision_id,
                        json_extract(value,'$.item') item_id,
                        json_extract(value,'$.revision') revision_id FROM json_each(?)
               ), sources AS (SELECT value space_id FROM json_each(?)),
               overlays AS (
                 SELECT json_extract(value,'$.source_item_id') item_id,
                        json_extract(value,'$.source_revision_id') revision_id,
                        json_extract(value,'$.source_space_id') space_id
                   FROM json_each(?)
               )
               SELECT ref.id reference_id,item.id,body.revision_id,item.space_id,
                      body.content_size_bytes size_bytes,
                      body.content_sha256,body.content_blob_hash
                 FROM wanted JOIN knowledge_explicit_reference ref
                   ON ref.id=wanted.reference_id
                  AND ref.source_revision_id=wanted.source_revision_id
                  AND ref.target_item_id=wanted.item_id
                  AND ref.target_revision_id=wanted.revision_id
                 JOIN knowledge_item item ON item.id=ref.target_item_id
                  AND item.space_id=ref.target_space_id
                 JOIN knowledge_space space ON space.id=item.space_id
                 JOIN knowledge_revision_body body ON body.item_id=item.id
                  AND body.revision_id=ref.target_revision_id
                WHERE ref.availability_status='available'
                  AND item.lifecycle_status='active' AND item.curation_status='published'
                  AND space.status='active'
                  AND (body.content_md IS NOT NULL OR body.content_blob_hash IS NOT NULL)
                  AND (EXISTS (SELECT 1 FROM sources WHERE sources.space_id=item.space_id)
                    OR EXISTS (SELECT 1 FROM overlays WHERE overlays.item_id=item.id
                         AND overlays.revision_id=body.revision_id
                         AND overlays.space_id=item.space_id))""",
            (json.dumps(wanted), json.dumps(source_space_ids), json.dumps(overlays)),
        ).fetchall()
        result = {str(row["reference_id"]): ContextBodyMetadata(
            str(row["id"]), str(row["revision_id"]), str(row["space_id"]),
            int(row["size_bytes"]), str(row["content_sha256"]),
            row["content_blob_hash"],
        ) for row in rows}
        if any(reference not in result for reference, *_ in references):
            raise ContextBodyUnavailableError("A selected fixed reference is unavailable.")
        return result

    def read(self, connection: ManagedConnection, metadata: ContextBodyMetadata) -> str:
        # The service owns authorization snapshots before and after loading.
        # This short read verifies the immutable selected body, never substitutes
        # a newer revision, and cannot by itself authorize model delivery.
        _connection(connection)
        row = connection.execute(
            """SELECT content_md FROM knowledge_revision_body
                WHERE item_id=? AND revision_id=?""",
            (metadata.item_id, metadata.revision_id),
        ).fetchone()
        if row is None or row["content_md"] is None:
            raise ContextBodyUnavailableError("A selected canonical body is unavailable.")
        return self.validate_content(metadata, row["content_md"])

    @staticmethod
    def validate_content(metadata: ContextBodyMetadata, payload: bytes | str) -> str:
        try:
            if isinstance(payload, bytes):
                encoded = payload
                body = payload.decode("utf-8", errors="strict")
            elif isinstance(payload, str):
                body = payload
                encoded = body.encode("utf-8", errors="strict")
            else:
                raise ContextBodyUnavailableError("A canonical body has invalid content type.")
        except UnicodeError as exc:
            raise ContextBodyUnavailableError("A canonical body is not valid UTF-8.") from exc
        if (len(encoded) != metadata.size_bytes
                or hashlib.sha256(encoded).hexdigest() != metadata.content_sha256
                or (metadata.content_blob_hash is not None
                    and metadata.content_blob_hash != metadata.content_sha256)):
            raise ContextBodyUnavailableError("A selected canonical body failed integrity checks.")
        return body
