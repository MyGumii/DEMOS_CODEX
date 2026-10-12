"""Framework-neutral startup errors for the canonical Knowledge Core."""

from __future__ import annotations


class KnowledgeCoreError(RuntimeError):
    """Base class with a stable non-secret diagnostic code."""

    code = "KNOWLEDGE_CORE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class KnowledgeCoreConfigurationError(KnowledgeCoreError):
    """The deployment requested an invalid or unsafe feature configuration."""

    code = "KNOWLEDGE_CORE_CONFIGURATION_INVALID"


class KnowledgeCoreNotReadyError(KnowledgeCoreError):
    """A later-phase traffic or worker surface was enabled before its gate."""

    code = "KNOWLEDGE_CORE_PHASE_NOT_READY"


class KnowledgeCoreHostConflictError(KnowledgeCoreError):
    """One Flask/Blueprint target was registered with incompatible host facts."""

    code = "KNOWLEDGE_CORE_HOST_CONFLICT"


class KnowledgeCoreServiceUnboundError(KnowledgeCoreError):
    """A later-phase service was requested before composition bound it."""

    code = "KNOWLEDGE_CORE_SERVICE_UNBOUND"


class KnowledgeCoreDatabaseError(KnowledgeCoreError):
    """Base class for canonical connection and transaction failures."""

    code = "KNOWLEDGE_CORE_DATABASE_ERROR"


class KnowledgeCoreDatabasePathError(KnowledgeCoreDatabaseError):
    """An explicit canonical database path is absent or unsafe for the action."""

    code = "KNOWLEDGE_CORE_DATABASE_PATH_INVALID"


class KnowledgeCoreRuntimeMismatchError(KnowledgeCoreDatabaseError):
    """The loaded SQLite library does not satisfy the frozen runtime contract."""

    code = "KNOWLEDGE_CORE_SQLITE_RUNTIME_MISMATCH"


class KnowledgeCorePragmaMismatchError(KnowledgeCoreDatabaseError):
    """A required per-connection or WAL setting could not be established."""

    code = "KNOWLEDGE_CORE_SQLITE_PRAGMA_MISMATCH"


class KnowledgeCoreTransactionError(KnowledgeCoreDatabaseError):
    """Explicit transaction ownership or state was violated."""

    code = "KNOWLEDGE_CORE_TRANSACTION_INVALID"


class KnowledgeCoreSchemaMismatchError(KnowledgeCoreDatabaseError):
    """Applied schema versions or checksums differ from the code catalog."""

    code = "KNOWLEDGE_CORE_SCHEMA_MISMATCH"


class KnowledgeCoreMigrationError(KnowledgeCoreDatabaseError):
    """The explicit deployment migrator could not atomically apply its catalog."""

    code = "KNOWLEDGE_CORE_MIGRATION_FAILED"


class KnowledgeCoreFreshBootstrapRequiredError(KnowledgeCoreMigrationError):
    """A fresh-only migration found an already-applied schema ledger."""

    code = "KNOWLEDGE_CORE_FRESH_BOOTSTRAP_REQUIRED"


__all__ = [
    "KnowledgeCoreConfigurationError",
    "KnowledgeCoreDatabaseError",
    "KnowledgeCoreDatabasePathError",
    "KnowledgeCoreError",
    "KnowledgeCoreFreshBootstrapRequiredError",
    "KnowledgeCoreHostConflictError",
    "KnowledgeCoreMigrationError",
    "KnowledgeCoreNotReadyError",
    "KnowledgeCorePragmaMismatchError",
    "KnowledgeCoreRuntimeMismatchError",
    "KnowledgeCoreSchemaMismatchError",
    "KnowledgeCoreServiceUnboundError",
    "KnowledgeCoreTransactionError",
]
