"""Exact source-Markdown heading locations, never inferred factual attribution.

This pure index is shared by the proposed versioned authoring request/assembly
successor. It does not change existing receipts, source text, or publication.
Only parsed top-level Markdown headings are indexed; plain PDF text is not guessed into a
hierarchy. Renderer-added headings remain source-Markdown location labels, not
claims that the original Office document contained those exact labels.
"""
from bisect import bisect_right
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re

from .wiki_authoring import ExtractionSource

SOURCE_STRUCTURE_VERSION = "wiki-source-structure-v1"


@dataclass(frozen=True, slots=True)
class SourceHeading:
    heading_id: str
    start: int
    label_end: int
    scope_end: int
    level: int
    label: str
    parent_id: str | None


class SourceStructureIndex:
    """Build once per immutable source; resolve each quote to literal locations."""

    def __init__(self, source: ExtractionSource):
        if not isinstance(source, ExtractionSource):
            raise TypeError("An immutable extraction is required.")
        # Revalidate the source binding even for an externally constructed DTO.
        ExtractionSource(source.snapshot_id, source.content_sha256, source.markdown)
        self.source = source
        from markdown_it import MarkdownIt
        rows, stack, line_starts = [], [], [0]
        # CommonMark normalizes only CRLF/CR/LF as line endings. splitlines()
        # also treats VT, form feed and Unicode separators as new lines, which
        # would shift every later token map away from its original characters.
        line_starts.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", source.markdown))
        if line_starts[-1] != len(source.markdown):
            line_starts.append(len(source.markdown))
        self._line_starts = tuple(line_starts)
        # CommonMark HTML block/fence rules have exact line maps; use them
        # rather than an expanding ad-hoc tag allowlist. Original LF/CRLF byte
        # strings are never reconstructed from normalized parser token text.
        tokens = MarkdownIt("commonmark").parse(source.markdown)
        top_level = [token for token in tokens if token.level == 0 and token.map is not None]
        def metadata_only(token):
            if token.type in {"heading_open", "hr"}:
                return True
            if token.type == "html_block":
                return not re.sub(r"<!--.*?(?:-->|$)", "", token.content, flags=re.DOTALL).strip()
            return False
        evidence = [not metadata_only(token) for token in top_level]
        next_evidence, following = [None] * len(top_level), None
        for position in range(len(top_level) - 1, -1, -1):
            next_evidence[position] = following
            if evidence[position]:
                following = line_starts[top_level[position].map[0]]
        intervals, prior_evidence, evidence_ranges = [], None, []
        for position, token in enumerate(top_level):
            start, end = (line_starts[number] for number in token.map)
            if not evidence[position]:
                # Skip intervening comments/rules rather than forcing a model
                # to fabricate a claim for a metadata-only first chunk.
                if next_evidence[position] is not None:
                    end = next_evidence[position] + 1
                elif prior_evidence is not None:
                    start, end = prior_evidence, len(source.markdown)
            else:
                prior_evidence = start
                evidence_ranges.append((start, end))
            intervals.append((start, end))
        self._evidence_ranges = tuple(evidence_ranges)
        self._evidence_starts = tuple(start for start, _ in evidence_ranges)
        protected = []
        for start, end in sorted(intervals):
            if protected and start < protected[-1][1]:
                protected[-1] = (protected[-1][0], max(end, protected[-1][1]))
            else:
                protected.append((start, end))
        self._protected_ranges = tuple(protected)
        self._protected_starts = tuple(start for start, _ in protected)
        for token in tokens:
            if token.type != "heading_open" or token.level != 0 or token.map is None:
                continue
            offset, label_end = (line_starts[number] for number in token.map)
            level = int(token.tag[1:])
            while stack and rows[stack[-1]].level >= level:
                previous = stack.pop()
                rows[previous] = replace(rows[previous], scope_end=offset)
            while label_end > offset and source.markdown[label_end - 1].isspace():
                label_end -= 1
            label = source.markdown[offset:label_end]
            binding = [SOURCE_STRUCTURE_VERSION, source.snapshot_id, source.content_sha256,
                offset, label_end, level, label]
            identity = "H_" + sha256(json.dumps(binding, ensure_ascii=True,
                separators=(",", ":")).encode()).hexdigest()[:24]
            rows.append(SourceHeading(identity, offset, label_end, len(source.markdown),
                level, label, rows[stack[-1]].heading_id if stack else None))
            stack.append(len(rows) - 1)
        self.headings = tuple(rows)
        self._starts = tuple(row.start for row in rows)
        self._by_id = {row.heading_id: row for row in rows}

    def has_evidence_block(self, start: int, end: int) -> bool:
        """Syntactic body presence, not proof the body asserts a true fact."""
        index = bisect_right(self._evidence_starts, end - 1) - 1
        return index >= 0 and self._evidence_ranges[index][1] > start

    def atomic_boundaries(self, candidates: list[int]) -> list[int]:
        """Only remove unsafe legacy boundaries; never widen a frozen layout.

        Do not split a parsed code/HTML/list block at an internal blank line,
        or dispatch a Setext/ATX heading separately from its first content.
        A whole oversized block is rejected by the caller, not truncated.
        """
        result = []
        for boundary in candidates:
            index = bisect_right(self._protected_starts, boundary) - 1
            if (boundary == len(self.source.markdown) or index < 0
                    or not self._protected_ranges[index][0] < boundary < self._protected_ranges[index][1]):
                result.append(boundary)
        return result

    def _range(self, start: int, end: int):
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(self.source.markdown):
            raise ValueError("Source structure range is invalid.")

    def _path(self, offset: int) -> tuple[SourceHeading, ...]:
        index = bisect_right(self._starts, offset) - 1
        if index < 0:
            return ()
        node = self.headings[index]
        path = []
        while node is not None:
            if node.start <= offset < node.scope_end:
                path.append(node)
            node = self._by_id.get(node.parent_id)
        return tuple(reversed(path))

    def paths_for_range(self, start: int, end: int) -> tuple[tuple[SourceHeading, ...], ...]:
        """Return source-location paths, not an assertion a heading proves a fact."""
        self._range(start, end)
        paths, seen = [], set()
        offsets = [start]
        offsets.extend(self._starts[bisect_right(self._starts, start):bisect_right(self._starts, end - 1)])
        for offset in offsets:
            path = self._path(offset)
            identity = tuple(node.heading_id for node in path)
            if path and identity not in seen:
                paths.append(path)
                seen.add(identity)
        return tuple(paths)

    def context_for_range(self, start: int, end: int) -> tuple[dict, ...]:
        """Keep ancestor labels across chunks with global, exact source ranges."""
        paths = self.paths_for_range(start, end)
        found = {node.heading_id: node for path in paths for node in path}
        return tuple({"heading_id": node.heading_id, "kind": "source_markdown_heading",
            "text": node.label, "label_range": [node.start, node.label_end],
            "scope_range": [node.start, node.scope_end], "level": node.level,
            "parent_id": node.parent_id}
            for node in sorted(found.values(), key=lambda value: value.start))

    def units_for_range(self, start: int, end: int) -> list[dict]:
        """Use the same authoritative heading classification in model source units."""
        self._range(start, end)
        content = self.source.markdown
        boundaries = {start, end}
        boundaries.update(start + match.end() for match in re.finditer(r"\n[ \t\r]*\n", content[start:end]))
        headings = {node.start: node for node in self.headings if start <= node.start and node.label_end <= end}
        boundaries.update(value for node in headings.values() for value in (node.start, node.label_end))
        units, previous = [], start
        for stop in sorted(boundaries):
            if content[previous:stop].strip():
                row = {"unit_id": f"U{len(units) + 1}", "range": [previous, stop],
                    "text": content[previous:stop], "kind": "body"}
                node = headings.get(previous)
                if node is not None and stop == node.label_end:
                    row.update(kind="heading", level=node.level,
                        coverage_range=[node.start, min(node.scope_end, end)])
                units.append(row)
            previous = stop
        return units

    def display_path(self, path: tuple[SourceHeading, ...]) -> str:
        # The line number distinguishes equal labels without opaque UUIDs.
        # This is an extraction location, never ownership of a semantic claim.
        values = []
        for node in path:
            label = re.sub(r"^ {0,3}#{1,6}[ \t]+", "", node.label.splitlines()[0])
            line = bisect_right(self._line_starts, node.start)
            values.append(f"{label} [행 {line}]")
        return " › ".join(values)


def literal_code_span(value: str) -> str:
    """No bare URL linkification or HTML/list interpretation inside labels."""
    text = " ".join(value.splitlines())
    width = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0) + 1
    fence = "`" * width
    return fence + " " + text + " " + fence
