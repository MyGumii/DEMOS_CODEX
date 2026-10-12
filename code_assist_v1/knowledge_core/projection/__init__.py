"""Replaceable projection boundaries; no projection is canonical state."""

from .materializer import (
    FullProjectionMaterializer,
    FullProjectionRebuildResult,
    PROJECTION_NAME,
    ProjectionCanonicalDriftError,
    ProjectionMaterializationError,
    ProjectionSpaceResult,
)
from .worker import (
    OutboxProjectionWorker,
    ProjectionHeartbeatError,
    ProjectionIterationResult,
    ProjectionServiceResult,
    ProjectionWorkerConfig,
    ProjectionWorkerError,
)
from .temporal import (
    ProjectionManifestUntrustedError,
    TemporalFooterMetadata,
    TemporalProjectionMaterializer,
    TemporalProjectionResult,
    TrustedTemporalProjectionImporter,
    render_temporal_page,
)
from .graph import (
    AGGREGATE_GRAPH_MANIFEST_SCHEMA_VERSION,
    AGGREGATE_GRAPH_PROJECTION_NAME,
    AggregateGraphProjectionMaterializer,
    AggregateGraphProjectionResult,
)

PROJECTION_MODULES = ("activity", "graph", "markdown", "materializer", "temporal")

__all__ = [
    "AGGREGATE_GRAPH_MANIFEST_SCHEMA_VERSION",
    "AGGREGATE_GRAPH_PROJECTION_NAME",
    "AggregateGraphProjectionMaterializer",
    "AggregateGraphProjectionResult",
    "FullProjectionMaterializer",
    "FullProjectionRebuildResult",
    "OutboxProjectionWorker",
    "PROJECTION_MODULES",
    "PROJECTION_NAME",
    "ProjectionCanonicalDriftError",
    "ProjectionHeartbeatError",
    "ProjectionIterationResult",
    "ProjectionMaterializationError",
    "ProjectionServiceResult",
    "ProjectionSpaceResult",
    "ProjectionWorkerConfig",
    "ProjectionWorkerError",
    "ProjectionManifestUntrustedError",
    "TemporalFooterMetadata",
    "TemporalProjectionMaterializer",
    "TemporalProjectionResult",
    "TrustedTemporalProjectionImporter",
    "render_temporal_page",
]
