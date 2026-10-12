"""Metadata-only inventory of existing source-triggered private-body cleanup.

No deletion, model text loading, connection ownership, or retention-policy
changes. Sizes are logical UTF-8 payload bytes, not SQLite pages or file shrink.
"""
from dataclasses import dataclass
import re

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class _BodyQuery:
    table: str
    identity: str
    size: str
    hashes: str
    joins: str
    artifact: str = "r.artifact_id"
    required_table: str | None = None

    def sql(self):
        return (f"SELECT {self.identity},{self.size},{self.hashes} FROM {self.table} b "
                f"{self.joins} WHERE {self.artifact}=? ORDER BY 1")


def _native_queries(prefix):
    # Names are fixed server-owned schema identifiers, never caller input.
    return (
        _BodyQuery(prefix + "authoring_run_body", "json_array(b.run_id)",
            "json_extract(r.metadata_json,'$.configuration_size_bytes')",
            "json_array(json_extract(r.metadata_json,'$.configuration_sha256'))",
            f"JOIN {prefix}authoring_run r ON r.id=b.run_id"),
        _BodyQuery(prefix + "authoring_outcome_body", "json_array(b.attempt_id)",
            "o.response_size_bytes+o.model_size_bytes+o.transport_size_bytes",
            "json_array(o.response_sha256,o.model_sha256,o.transport_sha256)",
            f"JOIN {prefix}authoring_outcome o ON o.attempt_id=b.attempt_id "
            f"JOIN {prefix}authoring_run r ON r.id=o.run_id"),
        _BodyQuery(prefix + "wiki_snapshot_body", "json_array(b.snapshot_id)",
            "s.content_size_bytes+s.manifest_size_bytes", "json_array(s.content_sha256,s.manifest_sha256)",
            f"JOIN {prefix}wiki_snapshot s ON s.id=b.snapshot_id JOIN {prefix}authoring_run r ON r.id=s.run_id"),
        _BodyQuery(prefix + "workflow_policy_body", "json_array(b.preview_id)",
            "q.policy_size_bytes", "json_array(q.policy_sha256)",
            f"JOIN {prefix}workflow_request q ON q.preview_id=b.preview_id "
            "JOIN publication_preview r ON r.id=b.preview_id"),
    )


_QUERIES = (
    *_native_queries("pdf_visual_")[:1],
    _BodyQuery("pdf_visual_authoring_outcome_body", "json_array(b.attempt_id)",
        "o.response_size_bytes+o.model_size_bytes+o.transport_size_bytes+o.evidence_size_bytes",
        "json_array(o.response_sha256,o.model_sha256,o.transport_sha256,o.evidence_sha256)",
        "JOIN pdf_visual_authoring_outcome o ON o.attempt_id=b.attempt_id "
        "JOIN pdf_visual_authoring_run r ON r.id=o.run_id"),
    *_native_queries("pdf_visual_")[2:],
    _BodyQuery("pdf_visual_source_body","json_array(b.source_id)","r.bundle_size_bytes+r.geometry_size_bytes",
        "json_array(r.bundle_sha256,r.geometry_sha256)","JOIN pdf_visual_source r ON r.id=b.source_id"),
    _BodyQuery("pdf_visual_page_body","json_array(b.source_id,b.page)","p.size_bytes",
        "json_array(p.content_sha256)","JOIN pdf_visual_page p ON p.source_id=b.source_id AND p.page=b.page "
        "JOIN pdf_visual_source r ON r.id=b.source_id"),
    _BodyQuery("extraction_native_table_structure_body", "json_array(b.snapshot_id,b.artifact_id)",
        "s.structure_size_bytes", "json_array(s.structure_sha256)",
        "JOIN extraction_native_table_structure s ON s.snapshot_id=b.snapshot_id AND s.artifact_id=b.artifact_id",
        "b.artifact_id"),
    *(_BodyQuery("publication_preservation_" + kind + "_body",
        f"json_array(b.run_id,b.ordinal,b.{ordinal})",
        "o.response_size_bytes+o.model_size_bytes+o.transport_size_bytes",
        "json_array(o.response_sha256,o.model_sha256,o.transport_sha256)",
        f"JOIN publication_preservation_{kind} o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.{ordinal}=b.{ordinal} "
        "JOIN publication_preservation_run r ON r.id=b.run_id")
      for kind, ordinal in (("receipt", "sequence"), ("failure", "failure_ordinal"))),
    _BodyQuery("preserved_wiki_snapshot_body", "json_array(b.snapshot_id)",
        "s.content_size_bytes+s.manifest_size_bytes", "json_array(s.content_sha256,s.manifest_sha256)",
        "JOIN preserved_wiki_snapshot s ON s.id=b.snapshot_id JOIN publication_preservation_run r ON r.id=s.run_id"),
    *_native_queries("native_"),
    *_native_queries("pptx_role_"),
    *_native_queries("pdf_"),
    _BodyQuery("publication_authoring_chunk_body", "json_array(b.run_id,b.ordinal)",
        "q.response_size_bytes+q.model_size_bytes+q.transport_size_bytes",
        "json_array(q.response_sha256,c.request_sha256)",
        "JOIN publication_authoring_chunk_receipt q ON q.run_id=b.run_id AND q.ordinal=b.ordinal "
        "JOIN publication_authoring_chunk c ON c.run_id=b.run_id AND c.ordinal=b.ordinal "
        "JOIN publication_authoring_run r ON r.id=b.run_id",
        required_table="publication_authoring_chunk_receipt"),
    _BodyQuery("authored_wiki_snapshot_body", "json_array(b.snapshot_id)",
        "s.content_size_bytes+s.manifest_size_bytes", "json_array(s.content_sha256,s.manifest_sha256)",
        "JOIN authored_wiki_snapshot s ON s.id=b.snapshot_id JOIN publication_authoring_run r ON r.id=s.run_id",
        required_table="publication_authoring_chunk_receipt"),
    _BodyQuery("publication_synthesis_batch_body", "json_array(b.run_id,b.round_number,b.ordinal)",
        "q.response_bytes+q.model_bytes+q.transport_bytes",
        "json_array(q.response_sha256,q.model_sha256,q.transport_sha256)",
        "JOIN publication_synthesis_receipt q ON q.run_id=b.run_id AND q.round_number=b.round_number AND q.ordinal=b.ordinal "
        "JOIN publication_authoring_run r ON r.id=b.run_id",
        required_table="publication_authoring_chunk_receipt"),
)


@dataclass(frozen=True, slots=True, order=True)
class DerivedBodyRow:
    table: str
    artifact_id: str
    identity_json: str
    size_bytes: int
    hashes_json: str

    def facts(self):
        return (self.table, self.artifact_id, self.identity_json, self.size_bytes, self.hashes_json)


@dataclass(frozen=True, slots=True)
class DerivedBodyInventory:
    tables: tuple[str, ...]
    rows: tuple[DerivedBodyRow, ...]

    @property
    def count(self):
        return len(self.rows)

    @property
    def size_bytes(self):
        return sum(row.size_bytes for row in self.rows)

    def facts(self):
        return {"schema_version": 1, "tables": self.tables,
                "rows": tuple(row.facts() for row in self.rows)}


def inventory_derived_bodies(connection, artifact_ids):
    if not isinstance(connection, ManagedConnection):
        raise TypeError("Derived-body accounting requires a ManagedConnection.")
    if type(artifact_ids) is not tuple or any(type(value) is not str or not _UUID.fullmatch(value) for value in artifact_ids):
        raise ValueError("Derived-body accounting requires exact artifact UUIDs.")
    names = tuple(sorted({name for query in _QUERIES for name in (query.table, query.required_table) if name}))
    available = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name IN (" + ",".join("?" for _ in names) + ")", names)}
    queries = tuple(query for query in _QUERIES if query.table in available
                    and (query.required_table is None or query.required_table in available))
    rows = []
    for query in queries:
        for artifact_id in sorted(set(artifact_ids)):
            for identity, size, hashes in connection.execute(query.sql(), (artifact_id,)):
                if type(size) is not int or not 0 <= size < 2**63 or type(identity) is not str or type(hashes) is not str:
                    raise KnowledgeCoreDatabaseError("Invalid derived-body size or identity metadata.")
                rows.append(DerivedBodyRow(query.table, artifact_id, identity, size, hashes))
    return DerivedBodyInventory(tuple(sorted(query.table for query in queries)), tuple(sorted(rows)))
