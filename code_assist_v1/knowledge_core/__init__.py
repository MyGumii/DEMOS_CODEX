"""Canonical Knowledge Space package boundary.

P1.1 exposes only composition and contracts.  Importing this package opens no
database, reads no application configuration, registers no route, and starts no
worker.  Later phases fill the declared repository/service boundaries.
"""

from .composition import (
    CanonicalRouteBindings,
    EXTENSION_KEY,
    HostProfile,
    KnowledgeCoreContainer,
    PublicationRouteBindings,
    ServiceRegistry,
    compose_knowledge_core,
    get_knowledge_core,
    register_knowledge_core,
)
from .domain import FeatureFlags, HostKind
from .errors import (
    KnowledgeCoreConfigurationError,
    KnowledgeCoreDatabaseError,
    KnowledgeCoreDatabasePathError,
    KnowledgeCoreError,
    KnowledgeCoreHostConflictError,
    KnowledgeCoreMigrationError,
    KnowledgeCoreNotReadyError,
    KnowledgeCorePragmaMismatchError,
    KnowledgeCoreRuntimeMismatchError,
    KnowledgeCoreSchemaMismatchError,
    KnowledgeCoreServiceUnboundError,
    KnowledgeCoreTransactionError,
)
from .feature_flags import FEATURE_FLAG_NAMES, read_feature_flags

__all__ = [
    "CanonicalRouteBindings",
    "EXTENSION_KEY",
    "FEATURE_FLAG_NAMES",
    "FeatureFlags",
    "HostKind",
    "HostProfile",
    "KnowledgeCoreConfigurationError",
    "KnowledgeCoreContainer",
    "KnowledgeCoreDatabaseError",
    "KnowledgeCoreDatabasePathError",
    "KnowledgeCoreError",
    "KnowledgeCoreHostConflictError",
    "KnowledgeCoreMigrationError",
    "KnowledgeCoreNotReadyError",
    "PublicationRouteBindings",
    "KnowledgeCorePragmaMismatchError",
    "KnowledgeCoreRuntimeMismatchError",
    "KnowledgeCoreSchemaMismatchError",
    "KnowledgeCoreServiceUnboundError",
    "KnowledgeCoreTransactionError",
    "ServiceRegistry",
    "compose_knowledge_core",
    "get_knowledge_core",
    "read_feature_flags",
    "register_knowledge_core",
]
