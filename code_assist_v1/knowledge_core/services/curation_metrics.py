"""Authorized read-only P6.7 shadow/proposal precision surface."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..db import ConnectionFactory, TransactionMode, transaction
from ..repositories.curation_metrics import (
    CurationMetricsRepository,
    CurationShadowMetrics,
)
from ..scope import Capability, ScopeResolver, SessionPrincipal


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


@dataclass(frozen=True, slots=True)
class CurationShadowMetricsRequest:
    principal: SessionPrincipal
    space_id: str
    sample_size: int = 25


class CurationShadowMetricsService:
    """Reauthorize a curator and derive metrics without mutating canonical state."""

    __slots__ = ("_factory", "_metrics", "_scope")

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        metrics: CurationMetricsRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("CurationShadowMetricsService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("CurationShadowMetricsService requires a ScopeResolver.")
        self._factory = factory
        self._scope = scope_resolver
        self._metrics = metrics or CurationMetricsRepository()

    def read(self, request: CurationShadowMetricsRequest) -> CurationShadowMetrics:
        if not isinstance(request, CurationShadowMetricsRequest):
            raise TypeError("read requires a CurationShadowMetricsRequest.")
        if not isinstance(request.principal, SessionPrincipal):
            raise TypeError("Curation metrics require a SessionPrincipal.")
        if not isinstance(request.space_id, str) or _UUID_RE.fullmatch(request.space_id) is None:
            raise ValueError("space_id must be a canonical lowercase UUID.")
        if (
            isinstance(request.sample_size, bool)
            or not isinstance(request.sample_size, int)
            or not 1 <= request.sample_size <= 100
        ):
            raise ValueError("sample_size must be in the range 1..100.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                resolved = self._scope.resolve_in_transaction(
                    connection,
                    request.principal,
                    request.space_id,
                    Capability.CURATE,
                )
                return self._metrics.read_shadow_metrics(
                    connection,
                    space_id=resolved.space.id,
                    sample_size=request.sample_size,
                )
        finally:
            connection.close()


__all__ = [
    "CurationShadowMetricsRequest",
    "CurationShadowMetricsService",
]
