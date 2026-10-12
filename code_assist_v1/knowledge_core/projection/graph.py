"""Deterministic graph-only projection for Team/Responsibility views.

The projection contains metadata, graph, backlinks, index, and graph-derived
log JSON only. It never materializes descendant Markdown bodies. Immutable
generation identity is the canonical source vector plus the home-edge
generation, so rebuilding unchanged authority is byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import secrets
from uuid import UUID

from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..migrations.runner import verify_canonical_integrity_before_projection_rebuild
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..repositories.aggregate_graph import AggregateGraphRepository
from ..repositories.management_reads import ManagementReadRepository
from ..repositories.semantic_graph_contracts import AggregateGenerationVectorRecord
from .materializer import (
    ProjectionCanonicalDriftError,
    ProjectionMaterializationError,
    _child_anchor,
    _projection_io_path,
    _read_relative,
    _write_relative,
)


AGGREGATE_GRAPH_PROJECTION_NAME = "aggregate_graph_v1"
AGGREGATE_GRAPH_MANIFEST_SCHEMA_VERSION = 1
_MAX_PENDING = 64
_GENERATION_RE = re.compile(r"^[0-9a-f]{64}-[0-9]+$", re.ASCII)


@dataclass(frozen=True, slots=True)
class AggregateGraphProjectionResult:
    selected_space_id: str
    source_vector_sha256: str
    home_edge_generation: int
    desired_generation: int
    node_count: int
    edge_count: int
    manifest_sha256: str
    generation_name: str


@dataclass(frozen=True, slots=True)
class _PreparedGraphProjection:
    vector: AggregateGenerationVectorRecord
    generation_name: str
    files: tuple[tuple[str, bytes], ...]
    manifest_bytes: bytes
    binding_sha256: str
    result: AggregateGraphProjectionResult


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProjectionMaterializationError(
            "Aggregate graph projection data is not canonical JSON."
        ) from exc


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _source_ids(vector_json: str) -> tuple[str, ...]:
    try:
        value = json.loads(vector_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProjectionMaterializationError(
            "The aggregate source vector is invalid."
        ) from exc
    if not isinstance(value, list) or len(value) > 4096:
        raise ProjectionMaterializationError(
            "The aggregate source vector is invalid."
        )
    result: list[str] = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(
            row.get("source_space_id"), str
        ):
            raise ProjectionMaterializationError(
                "The aggregate source vector is invalid."
            )
        source = str(row["source_space_id"])
        try:
            if str(UUID(source)) != source:
                raise ValueError
        except ValueError as exc:
            raise ProjectionMaterializationError(
                "The aggregate source vector is invalid."
            ) from exc
        result.append(source)
    if result != sorted(set(result)):
        raise ProjectionMaterializationError(
            "The aggregate source vector is not canonical."
        )
    return tuple(result)


def _edge_row(edge) -> dict[str, object]:
    return {
        "confidence_ppm": edge.confidence_ppm,
        "direction": edge.direction,
        "edge_scope": edge.edge_scope,
        "edge_status": edge.edge_status,
        "edge_type": edge.edge_type,
        "home_space_id": edge.home_space_id,
        "id": edge.id,
        "inherited": edge.inherited,
        "origin": edge.origin,
        "row_version": edge.row_version,
        "source_evidence_location": edge.source_evidence_location,
        "source_evidence_revision_id": edge.source_evidence_revision_id,
        "source_evidence_span_sha256": edge.source_evidence_span_sha256,
        "source_item_id": edge.source_item_id,
        "source_space_id": edge.source_space_id,
        "target_evidence_location": edge.target_evidence_location,
        "target_evidence_revision_id": edge.target_evidence_revision_id,
        "target_evidence_span_sha256": edge.target_evidence_span_sha256,
        "target_item_id": edge.target_item_id,
        "target_space_id": edge.target_space_id,
    }


def _node_row(node) -> dict[str, object]:
    return {
        "current_revision_id": node.current_revision_id,
        "id": node.id,
        "inherited": node.inherited,
        "origin": node.origin,
        "row_version": node.row_version,
        "source_space_id": node.space_id,
        "title": node.title,
    }


def _prepare(vector: AggregateGenerationVectorRecord, graph) -> _PreparedGraphProjection:
    node_rows = [_node_row(node) for node in graph.nodes]
    edge_rows = [_edge_row(edge) for edge in graph.edges]
    backlinks: dict[str, list[dict[str, object]]] = {}
    for edge in edge_rows:
        target = str(edge["target_item_id"])
        backlinks.setdefault(target, []).append(
            {
                "edge_id": edge["id"],
                "edge_scope": edge["edge_scope"],
                "edge_type": edge["edge_type"],
                "home_space_id": edge["home_space_id"],
                "source_item_id": edge["source_item_id"],
                "source_space_id": edge["source_space_id"],
            }
        )
        if edge["direction"] == "symmetric":
            source = str(edge["source_item_id"])
            backlinks.setdefault(source, []).append(
                {
                    "edge_id": edge["id"],
                    "edge_scope": edge["edge_scope"],
                    "edge_type": edge["edge_type"],
                    "home_space_id": edge["home_space_id"],
                    "source_item_id": edge["target_item_id"],
                    "source_space_id": edge["target_space_id"],
                }
            )
    ordered_backlinks = {
        key: sorted(
            value,
            key=lambda row: (str(row["edge_id"]), str(row["source_item_id"])),
        )
        for key, value in sorted(backlinks.items())
    }
    documents = {
        "backlinks.json": {
            "backlinks": ordered_backlinks,
            "projection_name": AGGREGATE_GRAPH_PROJECTION_NAME,
            "schema_version": 1,
            "selected_space_id": vector.selected_space_id,
        },
        "graph.json": {
            "edges": edge_rows,
            "nodes": node_rows,
            "projection_name": AGGREGATE_GRAPH_PROJECTION_NAME,
            "schema_version": 1,
            "selected_space_id": vector.selected_space_id,
        },
        "index.json": {
            "nodes": node_rows,
            "projection_name": AGGREGATE_GRAPH_PROJECTION_NAME,
            "schema_version": 1,
            "selected_space_id": vector.selected_space_id,
        },
        "log.json": {
            "entries": [
                {
                    "edge_id": edge["id"],
                    "edge_scope": edge["edge_scope"],
                    "edge_status": edge["edge_status"],
                    "home_space_id": edge["home_space_id"],
                    "row_version": edge["row_version"],
                }
                for edge in edge_rows
            ],
            "projection_name": AGGREGATE_GRAPH_PROJECTION_NAME,
            "schema_version": 1,
            "selected_space_id": vector.selected_space_id,
        },
    }
    files = tuple(
        (name, _canonical_bytes(value)) for name, value in sorted(documents.items())
    )
    manifest = {
        "closure_proof_sha256": vector.closure_proof_sha256,
        "files": [
            {"path": name, "sha256": _digest(content), "size_bytes": len(content)}
            for name, content in files
        ],
        "home_edge_generation": vector.home_edge_generation,
        "organization_row_version": vector.organization_row_version,
        "projection_name": AGGREGATE_GRAPH_PROJECTION_NAME,
        "schema_version": AGGREGATE_GRAPH_MANIFEST_SCHEMA_VERSION,
        "selected_space_id": vector.selected_space_id,
        "source_vector": json.loads(vector.source_vector_json),
        "source_vector_sha256": vector.source_vector_sha256,
    }
    manifest_bytes = _canonical_bytes(manifest)
    generation_identity = _digest(
        _canonical_bytes(
            {
                "closure_proof_sha256": vector.closure_proof_sha256,
                "home_edge_generation": vector.home_edge_generation,
                "organization_row_version": vector.organization_row_version,
                "source_vector_sha256": vector.source_vector_sha256,
            }
        )
    )
    generation_name = f"{generation_identity}-{vector.home_edge_generation}"
    if _GENERATION_RE.fullmatch(generation_name) is None:
        raise ProjectionMaterializationError(
            "The aggregate graph generation identity is invalid."
        )
    binding = {
        "closure_proof_sha256": vector.closure_proof_sha256,
        "edges": edge_rows,
        "home_edge_generation": vector.home_edge_generation,
        "nodes": node_rows,
        "organization_row_version": vector.organization_row_version,
        "source_vector_sha256": vector.source_vector_sha256,
    }
    manifest_sha = _digest(manifest_bytes)
    return _PreparedGraphProjection(
        vector=vector,
        generation_name=generation_name,
        files=files,
        manifest_bytes=manifest_bytes,
        binding_sha256=_digest(_canonical_bytes(binding)),
        result=AggregateGraphProjectionResult(
            selected_space_id=vector.selected_space_id,
            source_vector_sha256=vector.source_vector_sha256,
            home_edge_generation=vector.home_edge_generation,
            desired_generation=vector.desired_generation,
            node_count=len(node_rows),
            edge_count=len(edge_rows),
            manifest_sha256=manifest_sha,
            generation_name=generation_name,
        ),
    )


def _verify_generation(anchor: object, prepared: _PreparedGraphProjection) -> None:
    expected = {name for name, _ in prepared.files} | {"manifest.json"}
    paths = tuple(_projection_io_path(anchor.path).iterdir())  # type: ignore[attr-defined]
    if {path.name for path in paths} != expected or any(
        path.suffix.casefold() == ".md" for path in paths
    ):
        raise ProjectionMaterializationError(
            "An aggregate graph generation contains unexpected files."
        )
    for name, content in (
        *prepared.files,
        ("manifest.json", prepared.manifest_bytes),
    ):
        observed = _read_relative(
            anchor,
            name,
            expected_path=anchor.path / name,  # type: ignore[attr-defined]
        )
        if observed != content:
            raise ProjectionMaterializationError(
                "An immutable aggregate graph generation differs."
            )


def _cleanup_staging(anchor: object) -> None:
    try:
        for path in list(_projection_io_path(anchor.path).iterdir()):  # type: ignore[attr-defined]
            path = _projection_io_path(path)
            if not path.is_file() or path.is_symlink():
                raise ProjectionMaterializationError(
                    "Aggregate graph staging contains an unsafe entry."
                )
            path.unlink()
        anchor.dispose()  # type: ignore[attr-defined]
    except Exception as exc:
        raise ProjectionMaterializationError(
            "Aggregate graph staging could not be cleaned safely."
        ) from exc


class _AggregateGraphWriter:
    __slots__ = ("_boundary", "_layout", "_root")

    def __init__(
        self,
        layout: MigrationStorageLayout,
        boundary: MigrationStorageBoundary,
    ) -> None:
        if os.name != "nt":
            raise ProjectionMaterializationError(
                "Aggregate graph publication requires the approved Windows host."
            )
        self._layout = layout
        self._boundary = boundary
        self._root = layout.projection_root.resolve(strict=True)

    def stage(self, prepared: _PreparedGraphProjection) -> None:
        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _nt_rename_relative,
            _pinned_directory,
        )

        verify_migration_storage_layout(
            self._layout,
            require_database=True,
            expected=self._boundary,
        )
        with _pinned_directory(self._root) as root:
            with _child_anchor(root, prepared.vector.selected_space_id) as space:
                with _child_anchor(space, "aggregate-graph-generations") as generations:
                    target = generations.path / prepared.generation_name  # type: ignore[attr-defined]
                    if _projection_io_path(target).exists():
                        existing = _WindowsDirectoryAnchor.open(target)
                        try:
                            _verify_generation(existing, prepared)
                        finally:
                            existing.close()
                    else:
                        staging_name = ".aggregate-graph-" + secrets.token_hex(16)
                        staging = _WindowsDirectoryAnchor.create_relative_directory(
                            generations, staging_name
                        )
                        committed = False
                        try:
                            for name, content in prepared.files:
                                _write_relative(staging, name, content, replace=False)
                            _write_relative(
                                staging,
                                "manifest.json",
                                prepared.manifest_bytes,
                                replace=False,
                            )
                            _verify_generation(staging, prepared)
                            _nt_rename_relative(
                                staging.handle,  # type: ignore[attr-defined]
                                generations.handle,  # type: ignore[attr-defined]
                                prepared.generation_name,
                                replace=False,
                            )
                            committed = True
                        except OSError:
                            if _projection_io_path(target).exists():
                                _cleanup_staging(staging)
                                existing = _WindowsDirectoryAnchor.open(target)
                                try:
                                    _verify_generation(existing, prepared)
                                finally:
                                    existing.close()
                            else:
                                _cleanup_staging(staging)
                                raise
                        except Exception:
                            if not committed:
                                _cleanup_staging(staging)
                            raise
                        finally:
                            staging.close()
                _write_relative(
                    space,
                    "aggregate-graph-manifest.json",
                    prepared.manifest_bytes,
                    replace=True,
                )
        verify_migration_storage_layout(
            self._layout,
            require_database=True,
            expected=self._boundary,
        )


class AggregateGraphProjectionMaterializer:
    """Rebuild bounded pending higher-space graph targets."""

    __slots__ = ("_factory", "_graph", "_layout", "_reads", "_writer")

    def __init__(
        self,
        factory: ConnectionFactory,
        layout: MigrationStorageLayout,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("layout must be a MigrationStorageLayout.")
        boundary = verify_migration_storage_layout(layout, require_database=True)
        self._factory = factory
        self._layout = layout
        self._graph = AggregateGraphRepository()
        self._reads = ManagementReadRepository()
        self._writer = _AggregateGraphWriter(layout, boundary)

    def rebuild_pending(
        self, *, limit: int = _MAX_PENDING
    ) -> tuple[AggregateGraphProjectionResult, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_PENDING
        ):
            raise ValueError(f"limit must be from 1 through {_MAX_PENDING}.")
        # Activation is worker-owned: bootstrap a bounded page of higher targets
        # only while the aggregate projector is actually configured.  Ordinary
        # domain writes therefore remain bitwise/default-off compatible.
        bootstrap = self._factory.open()
        try:
            with transaction(bootstrap):
                missing = bootstrap.execute(
                    """SELECT space.id
                         FROM knowledge_space AS space JOIN org_unit AS unit
                           ON unit.id=space.owner_org_unit_id
                        WHERE space.space_kind IN ('team','responsibility')
                          AND space.status='active' AND unit.status='active'
                          AND NOT EXISTS (
                            SELECT 1 FROM aggregate_generation_vector AS vector
                             WHERE vector.selected_space_id=space.id
                          )
                        ORDER BY space.id LIMIT ?""",
                    (limit,),
                ).fetchall()
                self._graph.invalidate_generation_targets(
                    bootstrap,
                    selected_space_ids=tuple(str(row["id"]) for row in missing),
                    now_ms=db_now_ms(bootstrap),
                    create_missing=True,
                )
        finally:
            bootstrap.close()
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                rows = connection.execute(
                    """SELECT selected_space_id
                         FROM aggregate_generation_vector
                        WHERE status<>'ready' OR built_generation<desired_generation
                        ORDER BY selected_space_id LIMIT ?""",
                    (limit,),
                ).fetchall()
        finally:
            connection.close()
        return tuple(
            self.rebuild_space(str(row["selected_space_id"])) for row in rows
        )

    def rebuild_space(self, selected_space_id: str) -> AggregateGraphProjectionResult:
        try:
            if str(UUID(selected_space_id)) != selected_space_id:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                "selected_space_id must be a canonical UUID string."
            ) from exc
        verify_migration_storage_layout(self._layout, require_database=True)
        connection = self._factory.open(read_only=True)
        try:
            verify_canonical_integrity_before_projection_rebuild(
                connection, self._factory.migration_catalog
            )
        finally:
            connection.close()
        self._refresh_vector(selected_space_id)
        prepared = self._capture(selected_space_id)
        self._writer.stage(prepared)
        observed = self._capture(selected_space_id)
        if observed.binding_sha256 != prepared.binding_sha256:
            raise ProjectionCanonicalDriftError(
                "Canonical aggregate graph state changed during projection."
            )
        self._commit(prepared)
        return prepared.result

    def _refresh_vector(self, selected_space_id: str) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                current = self._graph.capture_source_vector(
                    connection, selected_space_id=selected_space_id
                )
                existing = connection.execute(
                    """SELECT status,desired_generation,built_generation
                         FROM aggregate_generation_vector
                        WHERE selected_space_id=?""",
                    (selected_space_id,),
                ).fetchone()
                # Projection authority is actor-independent.  Its closure proof
                # is therefore the exact canonical source-vector digest, never
                # a user/session AccessPlan digest left by a command service.
                closure = current[1]
                pending = existing is not None and (
                    str(existing[0]) != "ready"
                    or int(existing[2]) < int(existing[1])
                )
                self._graph.ensure_generation_vector(
                    connection,
                    selected_space_id=selected_space_id,
                    closure_proof_sha256=closure,
                    now_ms=db_now_ms(connection),
                    advance_desired_on_change=not pending,
                )
        finally:
            connection.close()

    def _capture(self, selected_space_id: str) -> _PreparedGraphProjection:
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                row = connection.execute(
                    "SELECT * FROM aggregate_generation_vector WHERE selected_space_id=?",
                    (selected_space_id,),
                ).fetchone()
                if row is None:
                    raise ProjectionMaterializationError(
                        "The aggregate generation vector is unavailable."
                    )
                vector = AggregateGenerationVectorRecord(
                    selected_space_id=str(row["selected_space_id"]),
                    source_vector_json=str(row["source_vector_json"]),
                    source_vector_sha256=str(row["source_vector_sha256"]),
                    home_edge_generation=int(row["home_edge_generation"]),
                    desired_generation=int(row["desired_generation"]),
                    built_generation=int(row["built_generation"]),
                    manifest_sha256=row["manifest_sha256"],
                    closure_proof_sha256=str(row["closure_proof_sha256"]),
                    organization_row_version=int(row["organization_row_version"]),
                    status=str(row["status"]),
                    last_error_json=row["last_error_json"],
                    created_at=int(row["created_at"]),
                    updated_at=int(row["updated_at"]),
                    row_version=int(row["row_version"]),
                )
                sources = _source_ids(vector.source_vector_json)
                graph = self._reads.graph_closure(
                    connection,
                    selected_space_id=selected_space_id,
                    source_space_ids=sources,
                    overlays=(),
                    closure_generation=vector.desired_generation,
                    corpus_closure_sha256=vector.closure_proof_sha256,
                    include_aggregate_edges=True,
                )
                return _prepare(vector, graph)
        finally:
            connection.close()

    def _commit(self, prepared: _PreparedGraphProjection) -> None:
        vector = prepared.vector
        connection = self._factory.open()
        try:
            with transaction(connection):
                current_json, current_sha, org_version = (
                    self._graph.capture_source_vector(
                        connection,
                        selected_space_id=vector.selected_space_id,
                    )
                )
                current = connection.execute(
                    "SELECT * FROM aggregate_generation_vector WHERE selected_space_id=?",
                    (vector.selected_space_id,),
                ).fetchone()
                if current is None or not (
                    current_json == vector.source_vector_json
                    and current_sha == vector.source_vector_sha256
                    and org_version == vector.organization_row_version
                    and int(current["home_edge_generation"])
                    == vector.home_edge_generation
                    and int(current["desired_generation"])
                    == vector.desired_generation
                    and int(current["row_version"]) == vector.row_version
                ):
                    raise ProjectionCanonicalDriftError(
                        "Aggregate graph generation changed before commit."
                    )
                now_ms = db_now_ms(connection)
                cursor = connection.execute(
                    """UPDATE aggregate_generation_vector
                          SET built_generation=desired_generation,
                              manifest_sha256=?,status='ready',last_error_json=NULL,
                              updated_at=?,row_version=row_version+1
                        WHERE selected_space_id=? AND row_version=?""",
                    (
                        prepared.result.manifest_sha256,
                        now_ms,
                        vector.selected_space_id,
                        vector.row_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProjectionCanonicalDriftError(
                        "Aggregate graph generation changed during commit."
                    )
        finally:
            connection.close()


__all__ = [
    "AGGREGATE_GRAPH_MANIFEST_SCHEMA_VERSION",
    "AGGREGATE_GRAPH_PROJECTION_NAME",
    "AggregateGraphProjectionMaterializer",
    "AggregateGraphProjectionResult",
]
