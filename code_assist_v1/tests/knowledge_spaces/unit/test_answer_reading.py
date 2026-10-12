from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import pickle
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.answer_reading import (
    EvidenceReadingClosedError, EvidenceReadingCursor, ReadingSource,
)
from code_assist_v1.knowledge_core.repositories.context_bodies import ContextBodyMetadata

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "CONV-012")]


def _cursor(bodies):
    sources = tuple(ReadingSource(
        ContextBodyMetadata(str(uuid4()), str(uuid4()), str(uuid4()), len(body.encode()),
                            hashlib.sha256(body.encode()).hexdigest()),
        f"Document {index}", ("ranked",),
    ) for index, body in enumerate(bodies))
    events = []
    by_key = {(source.metadata.item_id, source.metadata.revision_id): body
              for source, body in zip(sources, bodies, strict=True)}

    @contextmanager
    def reader():
        events.append("open")
        try:
            def load(metadata):
                events.append(("load", metadata.item_id))
                return by_key[metadata.item_id, metadata.revision_id]
            yield load
        finally:
            events.append("close")

    def artifacts(rows):
        assert events[-1] == "close"
        events.append("artifacts")
        return {(row.metadata.item_id, row.metadata.revision_id): () for row in rows}

    cursor = EvidenceReadingCursor(sources=sources, body_reader=reader,
                                   guard=lambda: events.append("fence"), artifacts=artifacts)
    return cursor, sources, events


@pytest.mark.parametrize("budget", [4, 5, 7, 31, 4_096, 98_304])
def test_sequential_pages_preserve_unicode_and_read_each_source_once(budget):
    bodies = (" A\r\n한글🙂👩‍💻e\u0301\t끝 \n" * 41, "", "\r\n둘째 문서\n" * 23)
    cursor, sources, events = _cursor(bodies)
    received = {row.metadata.item_id: "" for row in sources}
    for _ in range(10_000):
        before = len(events)
        page = cursor.read_next(max_bytes=budget)
        assert events[before] == events[-1] == "fence"
        assert page.body_bytes == sum(len(row.content_md.encode()) for row in page.chunks) <= budget
        assert page.chunks or page.selection_exhausted
        for chunk in page.chunks:
            item = chunk.source.metadata.item_id
            body = bodies[sources.index(chunk.source)]
            assert chunk.start_code_point == len(received[item])
            assert chunk.content_md == body[chunk.start_code_point:chunk.end_code_point]
            assert chunk.source_code_points == len(body)
            received[item] += chunk.content_md
        if page.selection_exhausted:
            break
    else:
        pytest.fail("Reader did not make progress.")
    assert tuple(received[row.metadata.item_id] for row in sources) == bodies
    assert page.completed_source_count == page.selected_source_count == len(bodies)
    assert page.selected_source_bytes == sum(len(body.encode()) for body in bodies)
    assert sum(isinstance(event, tuple) and event[0] == "load" for event in events) == len(bodies)
    assert cursor.read_next(max_bytes=budget).selection_exhausted
    cursor.close()
    assert cursor._cached_body is None and cursor._sources == ()
    with pytest.raises(EvidenceReadingClosedError):
        cursor.read_next()


@pytest.mark.parametrize("budget", [True, 0, 1, 2, 3, -1, 98_305, 4.0, "4"])
def test_invalid_budgets_do_not_consume_or_close_cursor(budget):
    cursor, _, events = _cursor(("🙂hello",))
    with pytest.raises(ValueError):
        cursor.read_next(max_bytes=budget)
    assert events == [] and not cursor.closed
    assert cursor.read_next().chunks[0].content_md == "🙂hello"
    cursor.close()


def test_no_prefetch_empty_selection_and_process_local_capability():
    cursor, _, events = _cursor(())
    assert events == []
    with pytest.raises(TypeError):
        pickle.dumps(cursor)
    page = cursor.read_next()
    assert page.selection_exhausted and page.chunks == ()
    assert asdict(page)["selected_source_count"] == 0
    cursor.close()
    with pytest.raises(EvidenceReadingClosedError):
        cursor.revalidate()


def test_finishing_cached_source_releases_cache_slot_before_next_source_load():
    cursor, sources, _ = _cursor(("a" * 7, "b" * 10))
    cursor.read_next(max_bytes=4)
    assert cursor._cached_body is not None
    original_reader = cursor._body_reader
    observed = []

    @contextmanager
    def reader():
        with original_reader() as load:
            def checked(metadata):
                if metadata.item_id == sources[1].metadata.item_id:
                    assert cursor._cached_body is None
                    observed.append(True)
                return load(metadata)
            yield checked

    cursor._body_reader = reader
    page = cursor.read_next(max_bytes=9)
    assert [row.content_md for row in page.chunks] == ["aaa", "bbbbbb"]
    assert observed == [True]
    cursor.close()


@pytest.mark.parametrize("failure", ["pre", "post", "provenance", "provider_return"])
def test_fence_or_provenance_failure_closes_and_discards_cached_body(failure):
    cursor, _, _ = _cursor(("한글" * 100,))
    cursor.read_next(max_bytes=4)
    assert cursor._cached_body is not None
    calls = []

    def guard():
        calls.append("fence")
        if failure in {"pre", "provider_return"} or (failure == "post" and len(calls) == 2):
            raise RuntimeError("authority changed")

    def artifacts(rows):
        raise RuntimeError("provenance changed")

    cursor._guard = guard
    if failure == "provenance":
        cursor._artifacts = artifacts
    with pytest.raises(RuntimeError, match="changed"):
        if failure == "provider_return":
            cursor.revalidate()
        else:
            cursor.read_next(max_bytes=4)
    assert cursor.closed and cursor._cached_body is None and cursor._sources == ()
    with pytest.raises(EvidenceReadingClosedError):
        cursor.read_next()
