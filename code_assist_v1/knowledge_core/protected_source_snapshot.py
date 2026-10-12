"""Checked handoff from an approved protected-document opener to Wiki text.

This does not open, decrypt, export, capture, or authorize a document.  An
operator-owned provider must do those things under the source's actual rights
and report coverage.  The snapshot is rejected if a location or known visual
inventory was not accounted for.  It is deliberately not an OOXML/PDF parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal, Protocol

from .protected_sources import ProtectedSourceKind


_SHA = frozenset("0123456789abcdef")
_MAX_LOCATIONS = 500
_MAX_SOURCE_CHARACTERS = 16_777_216


class ProtectedSourceSnapshotError(ValueError):
    """An authorized opener result is incomplete or conflicts with its source."""


@dataclass(frozen=True, slots=True)
class AuthorizedSourceLocation:
    ordinal: int
    text_markdown: str
    visual_markdown: str = ""
    visual_present: bool = False
    visual_examined: bool = False
    confirmed_blank: bool = False


@dataclass(frozen=True, slots=True)
class ProtectedLocationCoverage:
    ordinal: int
    status: Literal["complete", "confirmed_blank"]
    visual_present: bool
    visual_examined: bool


@dataclass(frozen=True, slots=True)
class ProtectedExtractionProvenance:
    """Small immutable record persisted with a successful extraction."""

    original_sha256: str
    source_kind: ProtectedSourceKind
    opening_method: str
    extraction_method: str
    location_kind: Literal["page", "derived_page", "slide", "sheet"]
    coverage: tuple[ProtectedLocationCoverage, ...]
    visual_coverage: Literal["not_proven", "provider_reported"]
    derivative_sha256: str | None = None

    def insert_values(
        self, *, extraction_snapshot_id: str, artifact_id: str, created_at: int
    ) -> dict[str, object]:
        if (
            type(self.original_sha256) is not str
            or len(self.original_sha256) != 64
            or any(char not in _SHA for char in self.original_sha256)
            or type(self.source_kind) is not ProtectedSourceKind
            or type(self.opening_method) is not str
            or not 1 <= len(self.opening_method.strip()) <= 120
            or type(self.extraction_method) is not str
            or not 1 <= len(self.extraction_method.strip()) <= 120
            or type(self.location_kind) is not str
            or self.location_kind not in {"page", "derived_page", "slide", "sheet"}
            or type(self.visual_coverage) is not str
            or self.visual_coverage not in {"not_proven", "provider_reported"}
            or type(self.coverage) is not tuple
            or not 1 <= len(self.coverage) <= _MAX_LOCATIONS
            or type(created_at) is not int
            or created_at < 0
        ):
            raise ProtectedSourceSnapshotError("Protected extraction provenance is invalid.")
        if self.derivative_sha256 is not None and (
            type(self.derivative_sha256) is not str
            or len(self.derivative_sha256) != 64
            or any(char not in _SHA for char in self.derivative_sha256)
        ):
            raise ProtectedSourceSnapshotError("Protected derivative provenance is invalid.")
        rows = []
        for ordinal, row in enumerate(self.coverage, start=1):
            if (
                type(row) is not ProtectedLocationCoverage
                or type(row.ordinal) is not int
                or row.ordinal != ordinal
                or row.status not in {"complete", "confirmed_blank"}
                or type(row.visual_present) is not bool
                or type(row.visual_examined) is not bool
                or (self.visual_coverage == "provider_reported"
                    and not row.visual_examined)
            ):
                raise ProtectedSourceSnapshotError("Protected location coverage is invalid.")
            rows.append({
                "ordinal": ordinal, "status": row.status,
                "visual_present": row.visual_present,
                "visual_examined": row.visual_examined,
            })
        return {
            "extraction_snapshot_id": extraction_snapshot_id,
            "artifact_id": artifact_id,
            "original_sha256": self.original_sha256,
            "derivative_sha256": self.derivative_sha256,
            "source_kind": self.source_kind.value,
            "opening_method": self.opening_method,
            "extraction_method": self.extraction_method,
            "location_kind": self.location_kind,
            "expected_locations": len(rows),
            "coverage_json": json.dumps(rows, ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":")),
            "visual_coverage": self.visual_coverage,
            "created_at": created_at,
        }


@dataclass(frozen=True, slots=True)
class AuthorizedSourceSnapshot:
    original_sha256: str
    source_kind: ProtectedSourceKind
    opening_method: str
    extraction_method: str
    location_kind: Literal["page", "derived_page", "slide", "sheet"]
    expected_locations: int
    locations: tuple[AuthorizedSourceLocation, ...]
    derivative_sha256: str | None = None
    export_permitted: bool = True

    def checked_markdown(
        self, *, original: bytes, expected_kind: ProtectedSourceKind
    ) -> str:
        """Return complete positioned text or fail before any Wiki job runs."""
        if (
            type(original) is not bytes
            or type(self.original_sha256) is not str
            or len(self.original_sha256) != 64
            or any(char not in _SHA for char in self.original_sha256)
            or self.original_sha256 != sha256(original).hexdigest()
            or type(self.source_kind) is not ProtectedSourceKind
            or self.source_kind is not expected_kind
            or type(self.export_permitted) is not bool
            or not self.export_permitted
            or type(self.opening_method) is not str
            or not 1 <= len(self.opening_method) <= 120
            or type(self.extraction_method) is not str
            or not 1 <= len(self.extraction_method) <= 120
            or self.location_kind not in {"page", "derived_page", "slide", "sheet"}
            or type(self.expected_locations) is not int
            or not 1 <= self.expected_locations <= _MAX_LOCATIONS
            or type(self.locations) is not tuple
            or len(self.locations) != self.expected_locations
        ):
            raise ProtectedSourceSnapshotError("The authorized source identity or permission is invalid.")
        if self.derivative_sha256 is not None and (
            type(self.derivative_sha256) is not str
            or len(self.derivative_sha256) != 64
            or any(char not in _SHA for char in self.derivative_sha256)
        ):
            raise ProtectedSourceSnapshotError("The authorized derivative identity is invalid.")
        if (self.location_kind == "slide"
                and expected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION):
            raise ProtectedSourceSnapshotError("A PDF cannot claim slide locations.")

        label = {"page": "Page", "derived_page": "Derived page",
                 "slide": "Slide", "sheet": "Sheet"}[self.location_kind]
        sections: list[str] = []
        total = 0
        for ordinal, location in enumerate(self.locations, start=1):
            if (
                type(location) is not AuthorizedSourceLocation
                or type(location.ordinal) is not int
                or location.ordinal != ordinal
                or type(location.text_markdown) is not str
                or type(location.visual_markdown) is not str
                or type(location.visual_present) is not bool
                or type(location.visual_examined) is not bool
                or type(location.confirmed_blank) is not bool
                or "\x00" in location.text_markdown
                or "\x00" in location.visual_markdown
            ):
                raise ProtectedSourceSnapshotError("The authorized location inventory is invalid.")
            text = location.text_markdown.strip()
            visual = location.visual_markdown.strip()
            if location.visual_present and (not location.visual_examined or not visual):
                raise ProtectedSourceSnapshotError("A visual source was not examined.")
            if visual and not location.visual_examined:
                raise ProtectedSourceSnapshotError("The visual observation has no examination record.")
            if location.confirmed_blank and (text or visual or location.visual_present):
                raise ProtectedSourceSnapshotError("A nonempty location was marked blank.")
            if not location.confirmed_blank and not (text or visual):
                raise ProtectedSourceSnapshotError("An authorized location has no recovered content.")
            blocks = [f"## {label} {ordinal}"]
            if location.confirmed_blank:
                blocks.append("[Confirmed blank source location]")
            else:
                if text:
                    blocks.append(text)
                if visual:
                    blocks.append("### Visual observations and uncertainty\n\n" + visual)
            section = "\n\n".join(blocks)
            total += len(section)
            if total > _MAX_SOURCE_CHARACTERS:
                raise ProtectedSourceSnapshotError("The authorized source exceeds its work boundary.")
            sections.append(section)
        return "\n\n".join(sections) + "\n"

    def provenance(self) -> ProtectedExtractionProvenance:
        return ProtectedExtractionProvenance(
            original_sha256=self.original_sha256,
            source_kind=self.source_kind,
            opening_method=self.opening_method,
            extraction_method=self.extraction_method,
            location_kind=self.location_kind,
            coverage=tuple(
                ProtectedLocationCoverage(
                    location.ordinal,
                    "confirmed_blank" if location.confirmed_blank else "complete",
                    location.visual_present,
                    location.visual_examined,
                ) for location in self.locations
            ),
            visual_coverage=(
                "provider_reported"
                if all(location.visual_examined for location in self.locations)
                else "not_proven"
            ),
            derivative_sha256=self.derivative_sha256,
        )


class AuthorizedProtectedSourceOpener(Protocol):
    def open_for_actor(
        self, filename: str, content: bytes, kind: ProtectedSourceKind,
        *, actor_user_id: str,
    ) -> AuthorizedSourceSnapshot: ...


__all__ = [
    "AuthorizedProtectedSourceOpener", "AuthorizedSourceLocation",
    "AuthorizedSourceSnapshot", "ProtectedExtractionProvenance",
    "ProtectedLocationCoverage", "ProtectedSourceSnapshotError",
]
