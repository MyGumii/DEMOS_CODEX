"""Invocation-local, next-only reads of an already authorized evidence set.

This is not a retrieval endpoint or a model input packer. The context service
supplies the immutable selection and fresh authorization callbacks. Every page
has exact contiguous Unicode-code-point ranges; no prefix, whitespace or tail
is silently discarded. No database connection survives a page read.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, ContextManager, TYPE_CHECKING

from .repositories.context_bodies import ContextBodyMetadata

if TYPE_CHECKING:
    from .services.agent_context import AgentSourceArtifact


class EvidenceReadingClosedError(RuntimeError):
    """The invocation ended or one of its evidence fences failed."""


@dataclass(frozen=True, slots=True)
class ReadingSource:
    metadata: ContextBodyMetadata
    title: str
    selection_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    source: ReadingSource
    content_md: str
    start_code_point: int
    end_code_point: int
    source_code_points: int
    source_artifacts: tuple[AgentSourceArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidencePage:
    chunks: tuple[EvidenceChunk, ...]
    body_bytes: int
    completed_source_count: int
    selected_source_count: int
    selected_source_bytes: int
    selection_exhausted: bool


class EvidenceReadingCursor:
    """A process-local capability; callers cannot supply item IDs or offsets.

    The byte bound covers raw page text, not the eventual serialized prompt.
    Executors must separately budget provenance, instructions, history and
    output, and call revalidate immediately before and after each provider call.
    A single large immutable body may remain cached between pages, but never
    a SQL connection/transaction or all full bodies. Close discards the cache.
    """

    __slots__ = (
        "_sources", "_body_reader", "_guard", "_artifacts", "_maximum_page_bytes",
        "_index", "_offset", "_cached_body", "_closed", "_lock", "_source_bytes",
    )

    def __init__(
        self, *, sources: tuple[ReadingSource, ...],
        body_reader: Callable[[], ContextManager[Callable[[ContextBodyMetadata], str]]],
        guard: Callable[[], None],
        artifacts: Callable[[tuple[ReadingSource, ...]], dict[tuple[str, str], tuple[AgentSourceArtifact, ...]]],
        maximum_page_bytes: int = 98_304,
    ) -> None:
        if type(maximum_page_bytes) is not int or maximum_page_bytes < 4:
            raise ValueError("A page must accommodate a complete UTF-8 code point.")
        keys = [(row.metadata.item_id, row.metadata.revision_id) for row in sources]
        if len(set(keys)) != len(keys):
            raise ValueError("Reading sources must be unique item/revision pairs.")
        self._sources = sources
        self._body_reader = body_reader
        self._guard = guard
        self._artifacts = artifacts
        self._maximum_page_bytes = maximum_page_bytes
        self._source_bytes = sum(row.metadata.size_bytes for row in sources)
        self._index = self._offset = 0
        self._cached_body: tuple[int, str] | None = None
        self._closed = False
        self._lock = RLock()

    def __getstate__(self):
        raise TypeError("An evidence reading cursor cannot leave its invocation.")

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise EvidenceReadingClosedError("The evidence reading invocation is closed.")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cached_body = None
            self._sources = ()
            self._body_reader = self._guard = self._artifacts = None

    def revalidate(self) -> None:
        """Fresh authorization, including when a provider has just returned."""
        with self._lock:
            self._require_open()
            try:
                self._guard()
            except BaseException:
                self.close()
                raise

    def read_next(self, *, max_bytes: int | None = None) -> EvidencePage:
        with self._lock:
            self._require_open()
            budget = self._maximum_page_bytes if max_bytes is None else max_bytes
            if type(budget) is not int or not 4 <= budget <= self._maximum_page_bytes:
                raise ValueError("The page byte budget is outside its configured bounds.")
            try:
                self._guard()
                index, offset, cached = self._index, self._offset, self._cached_body
                # Move, do not duplicate ownership: if this page finishes the
                # cached source and starts another large body, the old source
                # must not remain retained until the final metadata fence.
                self._cached_body = None
                chunks: list[EvidenceChunk] = []
                remaining = budget
                with self._body_reader() as load:
                    while index < len(self._sources) and remaining:
                        if remaining < 4 and chunks:
                            # Do not load a new document just to discover that
                            # its next complete code point cannot fit.
                            break
                        source = self._sources[index]
                        body = cached[1] if cached is not None and cached[0] == index else load(source.metadata)
                        # At most `remaining` code points can fit. Encoding this
                        # bounded slice avoids encoding the whole large body on
                        # every page; decoding drops only an incomplete suffix,
                        # which is read starting at the next page's exact offset.
                        text = body[offset:offset + remaining].encode("utf-8")[:remaining].decode("utf-8", "ignore")
                        if not text and offset < len(body):
                            cached = (index, body)
                            break
                        end = offset + len(text)
                        chunks.append(EvidenceChunk(source, text, offset, end, len(body)))
                        remaining -= len(text.encode("utf-8"))
                        if end == len(body):
                            index, offset, cached = index + 1, 0, None
                            del body
                        else:
                            offset, cached = end, (index, body)
                            break
                # Body handles are closed before provenance I/O and before a
                # page can be returned to an executor/provider.
                provenance = self._artifacts(tuple(chunk.source for chunk in chunks))
                enriched = tuple(EvidenceChunk(
                    chunk.source, chunk.content_md, chunk.start_code_point,
                    chunk.end_code_point, chunk.source_code_points,
                    provenance[chunk.source.metadata.item_id, chunk.source.metadata.revision_id],
                ) for chunk in chunks)
                self._guard()
                self._index, self._offset, self._cached_body = index, offset, cached
                return EvidencePage(
                    enriched, budget - remaining, index, len(self._sources),
                    self._source_bytes, index == len(self._sources),
                )
            except BaseException:
                self.close()
                raise
