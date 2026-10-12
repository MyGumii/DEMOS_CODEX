"""Service-owned read snapshot for P6.2 incremental candidate retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from ..db import ConnectionFactory, TransactionMode, transaction
from ..repositories.candidate_retrieval import (
    CandidateRetrievalRepository,
    IncrementalCandidateSnapshot,
)


@dataclass(frozen=True, slots=True)
class IncrementalCandidateRequest:
    space_id: str
    source_item_id: str
    source_revision_id: str
    source_markdown: str
    top_k: int = 8


class IncrementalCandidateRetrievalService:
    """Read one changed revision and execute one bounded same-space FTS query."""

    __slots__ = ("_factory", "_repository")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        repository: CandidateRetrievalRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError(
                "IncrementalCandidateRetrievalService requires a ConnectionFactory."
            )
        self._factory = factory
        self._repository = repository or CandidateRetrievalRepository()

    def retrieve(
        self, request: IncrementalCandidateRequest
    ) -> IncrementalCandidateSnapshot:
        if not isinstance(request, IncrementalCandidateRequest):
            raise TypeError("An IncrementalCandidateRequest is required.")
        # Validate the body type before opening the database. Its exact hash,
        # normalized hash, size, item, revision, and space are then rebound in
        # the one canonical read snapshot below.
        if not isinstance(request.source_markdown, str):
            raise ValueError("source_markdown must be text.")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._repository.retrieve_changed_item(
                    connection,
                    space_id=request.space_id,
                    source_item_id=request.source_item_id,
                    source_revision_id=request.source_revision_id,
                    source_markdown=request.source_markdown,
                    top_k=request.top_k,
                )
        finally:
            connection.close()


__all__ = [
    "IncrementalCandidateRequest",
    "IncrementalCandidateRetrievalService",
]
