"""Bounded changed-item-only candidate retrieval for P6.2.

The repository evaluates exactly one current source revision in one caller-owned
read transaction.  It performs at most one FTS5 ``MATCH`` query, limits the
result in SQL, and returns body-free evidence.  It never scans item pairs,
persists a finding, invokes a model, or applies a curation effect.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


CANDIDATE_RETRIEVAL_RULE_VERSION = "p6.2-fts-bm25-v1"
CANDIDATE_RETRIEVAL_MODE = "canonical_fts_bm25"
CANDIDATE_NORMALIZER_VERSION = "markdown_lf_v1"
MAX_CANDIDATE_SOURCE_BYTES = 2_000_000
MAX_CANDIDATE_QUERY_TERMS = 32
MAX_CANDIDATE_TERM_CHARACTERS = 64
MAX_CANDIDATE_RESULTS = 20

CandidateMatchKind = Literal["exact", "normalized", "lexical"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class CandidateRetrievalRepositoryError(KnowledgeCoreDatabaseError):
    code = "CANDIDATE_RETRIEVAL_ERROR"


class CandidateRetrievalConflictError(CandidateRetrievalRepositoryError):
    code = "CANDIDATE_SOURCE_CHANGED"


class CandidateRetrievalIntegrityError(CandidateRetrievalRepositoryError):
    code = "CANDIDATE_RETRIEVAL_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class IncrementalCandidate:
    item_id: str
    revision_id: str
    rank: int
    bm25_score: float
    match_kind: CandidateMatchKind
    title_sha256: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int

    def as_evidence(self) -> dict[str, object]:
        return {
            "bm25_score": format(self.bm25_score, ".17g"),
            "content_size_bytes": self.content_size_bytes,
            "item_id": self.item_id,
            "match_kind": self.match_kind,
            "normalized_content_sha256": self.normalized_content_sha256,
            "rank": self.rank,
            "raw_content_sha256": self.raw_content_sha256,
            "revision_id": self.revision_id,
            "title_sha256": self.title_sha256,
        }


@dataclass(frozen=True, slots=True)
class IncrementalCandidateSnapshot:
    space_id: str
    source_item_id: str
    source_revision_id: str
    source_raw_content_sha256: str
    source_normalized_content_sha256: str
    source_content_size_bytes: int
    query_terms_sha256: str
    query_term_count: int
    requested_top_k: int
    candidates: tuple[IncrementalCandidate, ...]
    evidence_sha256: str
    fts_query_count: int
    all_pairs_scan_count: Literal[0] = 0
    model_call_count: Literal[0] = 0
    retrieval_mode: str = CANDIDATE_RETRIEVAL_MODE
    rule_version: str = CANDIDATE_RETRIEVAL_RULE_VERSION

    def as_evidence(self) -> dict[str, object]:
        return {
            "all_pairs_scan_count": self.all_pairs_scan_count,
            "candidates": [candidate.as_evidence() for candidate in self.candidates],
            "fts_query_count": self.fts_query_count,
            "model_call_count": self.model_call_count,
            "query_term_count": self.query_term_count,
            "query_terms_sha256": self.query_terms_sha256,
            "requested_top_k": self.requested_top_k,
            "retrieval_mode": self.retrieval_mode,
            "rule_version": self.rule_version,
            "source": {
                "content_size_bytes": self.source_content_size_bytes,
                "item_id": self.source_item_id,
                "normalized_content_sha256": self.source_normalized_content_sha256,
                "raw_content_sha256": self.source_raw_content_sha256,
                "revision_id": self.source_revision_id,
                "space_id": self.space_id,
            },
        }


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise CandidateRetrievalIntegrityError(
            "Candidate evidence must be finite JSON."
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("Candidate retrieval requires a ManagedConnection.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Candidate retrieval requires one caller-owned transaction."
        )
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise CandidateRetrievalIntegrityError(
            f"Stored {field} is not a lowercase SHA-256 value."
        )
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CandidateRetrievalIntegrityError(f"Stored {field} is invalid.")
    return value


def _top_k(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_CANDIDATE_RESULTS
    ):
        raise ValueError(
            f"top_k must be an integer from 1 through {MAX_CANDIDATE_RESULTS}."
        )
    return value


def _normalized_bytes(markdown: str) -> bytes:
    return markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    current: list[str] = []
    overflow = False

    def finish() -> None:
        nonlocal overflow
        if not overflow and 2 <= len(current) <= MAX_CANDIDATE_TERM_CHARACTERS:
            tokens.append("".join(current))
        current.clear()
        overflow = False

    for character in normalized:
        if character.isalnum():
            if not overflow:
                current.append(character)
            if len(current) > MAX_CANDIDATE_TERM_CHARACTERS:
                current.clear()
                overflow = True
        else:
            finish()
    finish()
    return tuple(tokens)


def _select_terms(title: str, markdown: str) -> tuple[str, ...]:
    observed = _tokens(title + "\n" + markdown)
    counts = Counter(observed)
    first: dict[str, int] = {}
    for index, term in enumerate(observed):
        first.setdefault(term, index)
    return tuple(
        sorted(counts, key=lambda term: (-counts[term], first[term], term))[
            :MAX_CANDIDATE_QUERY_TERMS
        ]
    )


def _fts_literal_query(terms: tuple[str, ...]) -> str:
    # Terms contain only Unicode alphanumeric code points. Quoting still makes
    # the no-operator authority explicit for FTS5.
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _snapshot_payload(
    *,
    space_id: str,
    source_item_id: str,
    source_revision_id: str,
    source_raw_content_sha256: str,
    source_normalized_content_sha256: str,
    source_content_size_bytes: int,
    query_terms_sha256: str,
    query_term_count: int,
    requested_top_k: int,
    candidates: tuple[IncrementalCandidate, ...],
    fts_query_count: int,
) -> dict[str, object]:
    return {
        "all_pairs_scan_count": 0,
        "candidates": [candidate.as_evidence() for candidate in candidates],
        "fts_query_count": fts_query_count,
        "model_call_count": 0,
        "query_term_count": query_term_count,
        "query_terms_sha256": query_terms_sha256,
        "requested_top_k": requested_top_k,
        "retrieval_mode": CANDIDATE_RETRIEVAL_MODE,
        "rule_version": CANDIDATE_RETRIEVAL_RULE_VERSION,
        "source": {
            "content_size_bytes": source_content_size_bytes,
            "item_id": source_item_id,
            "normalized_content_sha256": source_normalized_content_sha256,
            "raw_content_sha256": source_raw_content_sha256,
            "revision_id": source_revision_id,
            "space_id": space_id,
        },
    }


def validate_incremental_candidate_snapshot(
    value: IncrementalCandidateSnapshot,
) -> IncrementalCandidateSnapshot:
    """Independently validate one body-free retrieval result."""

    if not isinstance(value, IncrementalCandidateSnapshot):
        raise TypeError("An IncrementalCandidateSnapshot is required.")
    space_id = _uuid(value.space_id, "space_id")
    source_item_id = _uuid(value.source_item_id, "source_item_id")
    source_revision_id = _uuid(value.source_revision_id, "source_revision_id")
    raw_sha = _sha(value.source_raw_content_sha256, "source raw content SHA")
    normalized_sha = _sha(
        value.source_normalized_content_sha256,
        "source normalized content SHA",
    )
    source_size = _integer(value.source_content_size_bytes, "source content size")
    terms_sha = _sha(value.query_terms_sha256, "query terms SHA")
    term_count = _integer(value.query_term_count, "query term count")
    if term_count > MAX_CANDIDATE_QUERY_TERMS:
        raise CandidateRetrievalIntegrityError("Candidate query term count is invalid.")
    top_k = _top_k(value.requested_top_k)
    if value.rule_version != CANDIDATE_RETRIEVAL_RULE_VERSION:
        raise CandidateRetrievalIntegrityError("Candidate rule version is invalid.")
    if value.retrieval_mode != CANDIDATE_RETRIEVAL_MODE:
        raise CandidateRetrievalIntegrityError("Candidate retrieval mode is invalid.")
    expected_query_count = 0 if term_count == 0 else 1
    if (
        type(value.fts_query_count) is not int
        or value.fts_query_count != expected_query_count
        or type(value.all_pairs_scan_count) is not int
        or value.all_pairs_scan_count != 0
        or type(value.model_call_count) is not int
        or value.model_call_count != 0
    ):
        raise CandidateRetrievalIntegrityError(
            "Candidate retrieval execution counters exceed the frozen boundary."
        )
    if (
        not isinstance(value.candidates, tuple)
        or len(value.candidates) > top_k
        or (term_count == 0 and bool(value.candidates))
        or source_size > MAX_CANDIDATE_SOURCE_BYTES
    ):
        raise CandidateRetrievalIntegrityError("Candidate result count is invalid.")
    candidates: list[IncrementalCandidate] = []
    seen_items: set[str] = set()
    seen_revisions: set[str] = set()
    previous_key: tuple[float, str] | None = None
    for expected_rank, candidate in enumerate(value.candidates, start=1):
        if not isinstance(candidate, IncrementalCandidate):
            raise CandidateRetrievalIntegrityError("Candidate row type is invalid.")
        item_id = _uuid(candidate.item_id, "candidate item_id")
        revision_id = _uuid(candidate.revision_id, "candidate revision_id")
        if (
            item_id == source_item_id
            or item_id in seen_items
            or revision_id in seen_revisions
        ):
            raise CandidateRetrievalIntegrityError(
                "Candidate identities are not unique and source-excluding."
            )
        seen_items.add(item_id)
        seen_revisions.add(revision_id)
        if type(candidate.rank) is not int or candidate.rank != expected_rank:
            raise CandidateRetrievalIntegrityError("Candidate ranks are not contiguous.")
        if (
            isinstance(candidate.bm25_score, bool)
            or not isinstance(candidate.bm25_score, (int, float))
            or not math.isfinite(float(candidate.bm25_score))
        ):
            raise CandidateRetrievalIntegrityError("Candidate BM25 score is invalid.")
        score = float(candidate.bm25_score)
        key = (score, item_id)
        if previous_key is not None and key < previous_key:
            raise CandidateRetrievalIntegrityError("Candidate ordering is invalid.")
        previous_key = key
        candidate_raw_sha = _sha(
            candidate.raw_content_sha256, "candidate raw content SHA"
        )
        candidate_normalized_sha = _sha(
            candidate.normalized_content_sha256,
            "candidate normalized content SHA",
        )
        candidate_size = _integer(
            candidate.content_size_bytes, "candidate content size"
        )
        expected_kind: CandidateMatchKind
        if (
            candidate_raw_sha == raw_sha
            and candidate_normalized_sha == normalized_sha
            and candidate_size == source_size
        ):
            expected_kind = "exact"
        elif candidate_normalized_sha == normalized_sha:
            expected_kind = "normalized"
        else:
            expected_kind = "lexical"
        if candidate.match_kind != expected_kind:
            raise CandidateRetrievalIntegrityError(
                "Candidate match kind does not match its content evidence."
            )
        candidates.append(
            IncrementalCandidate(
                item_id=item_id,
                revision_id=revision_id,
                rank=expected_rank,
                bm25_score=score,
                match_kind=expected_kind,
                title_sha256=_sha(candidate.title_sha256, "candidate title SHA"),
                raw_content_sha256=candidate_raw_sha,
                normalized_content_sha256=candidate_normalized_sha,
                content_size_bytes=candidate_size,
            )
        )
    canonical_candidates = tuple(candidates)
    payload = _snapshot_payload(
        space_id=space_id,
        source_item_id=source_item_id,
        source_revision_id=source_revision_id,
        source_raw_content_sha256=raw_sha,
        source_normalized_content_sha256=normalized_sha,
        source_content_size_bytes=source_size,
        query_terms_sha256=terms_sha,
        query_term_count=term_count,
        requested_top_k=top_k,
        candidates=canonical_candidates,
        fts_query_count=expected_query_count,
    )
    evidence_sha = _sha(value.evidence_sha256, "candidate evidence SHA")
    if evidence_sha != _canonical_sha256(payload):
        raise CandidateRetrievalIntegrityError(
            "Candidate evidence hash does not bind the canonical result."
        )
    return IncrementalCandidateSnapshot(
        space_id=space_id,
        source_item_id=source_item_id,
        source_revision_id=source_revision_id,
        source_raw_content_sha256=raw_sha,
        source_normalized_content_sha256=normalized_sha,
        source_content_size_bytes=source_size,
        query_terms_sha256=terms_sha,
        query_term_count=term_count,
        requested_top_k=top_k,
        candidates=canonical_candidates,
        evidence_sha256=evidence_sha,
        fts_query_count=expected_query_count,
    )


class CandidateRetrievalRepository:
    """Retrieve same-space candidates for one changed current revision."""

    __slots__ = ()

    def retrieve_changed_item(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        source_item_id: str,
        source_revision_id: str,
        source_markdown: str,
        top_k: int = 8,
    ) -> IncrementalCandidateSnapshot:
        connection = _connection(connection)
        space_id = _uuid(space_id, "space_id")
        source_item_id = _uuid(source_item_id, "source_item_id")
        source_revision_id = _uuid(source_revision_id, "source_revision_id")
        top_k = _top_k(top_k)
        if not isinstance(source_markdown, str):
            raise ValueError("source_markdown must be text.")
        encoded = source_markdown.encode("utf-8")
        if len(encoded) > MAX_CANDIDATE_SOURCE_BYTES:
            raise ValueError(
                f"source_markdown exceeds {MAX_CANDIDATE_SOURCE_BYTES} UTF-8 bytes."
            )
        source = connection.execute(
            """SELECT item.space_id,item.current_revision_id,
                      item.lifecycle_status,item.curation_status,
                      revision.title,revision.raw_content_sha256,
                      revision.normalized_content_sha256,
                      revision.content_size_bytes,revision.normalizer_version,
                      body.content_md,body.content_blob_hash,
                      body.content_size_bytes AS body_size,
                      body.content_sha256 AS body_sha256
                 FROM knowledge_item AS item
                 JOIN knowledge_revision AS revision
                   ON revision.id=? AND revision.item_id=item.id
                 JOIN knowledge_revision_body AS body
                   ON body.revision_id=revision.id AND body.item_id=item.id
                WHERE item.id=?""",
            (source_revision_id, source_item_id),
        ).fetchone()
        if source is None:
            raise CandidateRetrievalConflictError(
                "The changed source revision is unavailable."
            )
        raw_sha = hashlib.sha256(encoded).hexdigest()
        normalized_sha = hashlib.sha256(_normalized_bytes(source_markdown)).hexdigest()
        source_title = source["title"]
        stored_raw_sha = _sha(source["raw_content_sha256"], "source raw SHA")
        stored_normalized_sha = _sha(
            source["normalized_content_sha256"], "source normalized SHA"
        )
        stored_size = _integer(source["content_size_bytes"], "source size")
        if (
            source["space_id"] != space_id
            or source["current_revision_id"] != source_revision_id
            or source["lifecycle_status"] != "active"
            or source["curation_status"] not in {"provisional", "published"}
            or source["normalizer_version"] != CANDIDATE_NORMALIZER_VERSION
            or not isinstance(source_title, str)
            or source["body_sha256"] != stored_raw_sha
            or source["body_size"] != stored_size
            or ((source["content_md"] is None) == (source["content_blob_hash"] is None))
            or (source["content_md"] is not None and source["content_md"] != source_markdown)
            or raw_sha != stored_raw_sha
            or normalized_sha != stored_normalized_sha
            or len(encoded) != stored_size
        ):
            raise CandidateRetrievalConflictError(
                "The changed source binding is no longer current and exact."
            )

        terms = _select_terms(source_title, source_markdown)
        terms_sha = _canonical_sha256(list(terms))
        candidates: tuple[IncrementalCandidate, ...] = ()
        fts_query_count = 0
        if terms:
            fts_query_count = 1
            try:
                rows = connection.execute(
                    """SELECT document.item_id,document.revision_id,
                              document.title,document.content_md,
                              revision.title AS revision_title,
                              revision.raw_content_sha256,
                              revision.normalized_content_sha256,
                              revision.content_size_bytes,
                              revision.normalizer_version,
                              body.content_md AS body_content_md,
                              body.content_blob_hash,
                              body.content_size_bytes AS body_size,
                              body.content_sha256 AS body_sha256,
                              bm25(knowledge_fts,6.0,1.0) AS bm25_score
                         FROM knowledge_fts
                         JOIN search_document AS document
                           ON document.rowid=knowledge_fts.rowid
                         JOIN knowledge_item AS item
                           ON item.id=document.item_id
                          AND item.space_id=document.space_id
                         JOIN knowledge_revision AS revision
                           ON revision.id=document.revision_id
                          AND revision.item_id=document.item_id
                         JOIN knowledge_revision_body AS body
                           ON body.revision_id=revision.id
                          AND body.item_id=revision.item_id
                        WHERE knowledge_fts MATCH ?
                          AND document.space_id=?
                          AND document.item_id<>?
                          AND item.lifecycle_status='active'
                          AND item.curation_status='published'
                          AND item.current_revision_id=document.revision_id
                        ORDER BY bm25_score ASC,document.item_id ASC
                        LIMIT ?""",
                    (
                        _fts_literal_query(terms),
                        space_id,
                        source_item_id,
                        top_k,
                    ),
                ).fetchall()
            except sqlite3.Error as exc:
                raise CandidateRetrievalRepositoryError(
                    "Canonical candidate FTS retrieval failed."
                ) from exc
            built: list[IncrementalCandidate] = []
            for rank, row in enumerate(rows, start=1):
                item_id = _uuid(row["item_id"], "candidate item_id")
                revision_id = _uuid(row["revision_id"], "candidate revision_id")
                title = row["title"]
                content = row["content_md"]
                candidate_raw_sha = _sha(
                    row["raw_content_sha256"], "candidate raw SHA"
                )
                candidate_normalized_sha = _sha(
                    row["normalized_content_sha256"],
                    "candidate normalized SHA",
                )
                candidate_size = _integer(
                    row["content_size_bytes"], "candidate size"
                )
                score = row["bm25_score"]
                if (
                    not isinstance(title, str)
                    or not isinstance(content, str)
                    or row["revision_title"] != title
                    or row["normalizer_version"] != CANDIDATE_NORMALIZER_VERSION
                    or row["body_sha256"] != candidate_raw_sha
                    or row["body_size"] != candidate_size
                    or ((row["body_content_md"] is None) == (row["content_blob_hash"] is None))
                    or (
                        row["body_content_md"] is not None
                        and row["body_content_md"] != content
                    )
                    or hashlib.sha256(content.encode("utf-8")).hexdigest()
                    != candidate_raw_sha
                    or hashlib.sha256(_normalized_bytes(content)).hexdigest()
                    != candidate_normalized_sha
                    or len(content.encode("utf-8")) != candidate_size
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    raise CandidateRetrievalIntegrityError(
                        "A candidate search row does not match canonical revision evidence."
                    )
                if (
                    candidate_raw_sha == stored_raw_sha
                    and candidate_normalized_sha == stored_normalized_sha
                    and candidate_size == stored_size
                ):
                    kind: CandidateMatchKind = "exact"
                elif candidate_normalized_sha == stored_normalized_sha:
                    kind = "normalized"
                else:
                    kind = "lexical"
                built.append(
                    IncrementalCandidate(
                        item_id=item_id,
                        revision_id=revision_id,
                        rank=rank,
                        bm25_score=float(score),
                        match_kind=kind,
                        title_sha256=hashlib.sha256(title.encode("utf-8")).hexdigest(),
                        raw_content_sha256=candidate_raw_sha,
                        normalized_content_sha256=candidate_normalized_sha,
                        content_size_bytes=candidate_size,
                    )
                )
            candidates = tuple(built)

        payload = _snapshot_payload(
            space_id=space_id,
            source_item_id=source_item_id,
            source_revision_id=source_revision_id,
            source_raw_content_sha256=stored_raw_sha,
            source_normalized_content_sha256=stored_normalized_sha,
            source_content_size_bytes=stored_size,
            query_terms_sha256=terms_sha,
            query_term_count=len(terms),
            requested_top_k=top_k,
            candidates=candidates,
            fts_query_count=fts_query_count,
        )
        return validate_incremental_candidate_snapshot(
            IncrementalCandidateSnapshot(
                space_id=space_id,
                source_item_id=source_item_id,
                source_revision_id=source_revision_id,
                source_raw_content_sha256=stored_raw_sha,
                source_normalized_content_sha256=stored_normalized_sha,
                source_content_size_bytes=stored_size,
                query_terms_sha256=terms_sha,
                query_term_count=len(terms),
                requested_top_k=top_k,
                candidates=candidates,
                evidence_sha256=_canonical_sha256(payload),
                fts_query_count=fts_query_count,
            )
        )


__all__ = [
    "CANDIDATE_RETRIEVAL_MODE",
    "CANDIDATE_RETRIEVAL_RULE_VERSION",
    "CANDIDATE_NORMALIZER_VERSION",
    "CandidateRetrievalConflictError",
    "CandidateRetrievalIntegrityError",
    "CandidateRetrievalRepository",
    "CandidateRetrievalRepositoryError",
    "IncrementalCandidate",
    "IncrementalCandidateSnapshot",
    "MAX_CANDIDATE_QUERY_TERMS",
    "MAX_CANDIDATE_RESULTS",
    "MAX_CANDIDATE_SOURCE_BYTES",
    "validate_incremental_candidate_snapshot",
]
