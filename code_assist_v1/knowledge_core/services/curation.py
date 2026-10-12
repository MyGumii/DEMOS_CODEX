"""Versioned deterministic ingest lint and exact-dedup policy.

P6.1 is deliberately pure and side-effect free.  It makes no semantic claim,
opens no database or file, and cannot merge, replace, hide, deactivate, or
purge knowledge.  Later P6 services may persist this evidence, but they may not
reinterpret a normalized-only or cross-space match as an automatic duplicate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal
from uuid import UUID


DETERMINISTIC_INGEST_RULE_VERSION = "p6.1-deterministic-ingest-v1"
DETERMINISTIC_NORMALIZER_VERSION = "markdown_lf_v1"
MAX_DETERMINISTIC_LINE_CHARACTERS = 4_096

DeterministicDisposition = Literal[
    "new",
    "no_op",
    "exact_repeat",
    "normalized_candidate",
    "source_update_candidate",
]
DeterministicAutomaticEffect = Literal["none", "provenance"]
LintSeverity = Literal["blocker", "error", "warning"]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)", re.ASCII)
_MAX_INTEGER = 9_223_372_036_854_775_807


class DeterministicIngestPolicyError(ValueError):
    """The proposed or observed deterministic evidence is not canonical."""


class DeterministicCorpusConflictError(DeterministicIngestPolicyError):
    """The existing corpus violates exact-one deterministic resolution."""


@dataclass(frozen=True, slots=True)
class DeterministicLintFinding:
    code: str
    severity: LintSeverity
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class DeterministicContentFingerprint:
    space_id: str
    item_id: str | None
    revision_id: str | None
    source_identity_sha256: str
    raw_content_sha256: str
    normalized_content_sha256: str
    content_size_bytes: int
    normalizer_version: str = DETERMINISTIC_NORMALIZER_VERSION

    def as_evidence(self) -> dict[str, object]:
        return {
            "content_size_bytes": self.content_size_bytes,
            "item_id": self.item_id,
            "normalized_content_sha256": self.normalized_content_sha256,
            "normalizer_version": self.normalizer_version,
            "raw_content_sha256": self.raw_content_sha256,
            "revision_id": self.revision_id,
            "source_identity_sha256": self.source_identity_sha256,
            "space_id": self.space_id,
        }


@dataclass(frozen=True, slots=True)
class DeterministicIngestDecision:
    disposition: DeterministicDisposition
    automatic_effect: DeterministicAutomaticEffect
    matched_item_id: str | None
    matched_revision_id: str | None
    normalized_candidate_item_ids: tuple[str, ...]
    cross_space_exact_item_ids: tuple[str, ...]
    input_fingerprint_sha256: str
    evidence_sha256: str
    rule_version: str = DETERMINISTIC_INGEST_RULE_VERSION

    def as_evidence(self) -> dict[str, object]:
        return {
            "automatic_effect": self.automatic_effect,
            "cross_space_exact_item_ids": list(self.cross_space_exact_item_ids),
            "disposition": self.disposition,
            "evidence_sha256": self.evidence_sha256,
            "input_fingerprint_sha256": self.input_fingerprint_sha256,
            "matched_item_id": self.matched_item_id,
            "matched_revision_id": self.matched_revision_id,
            "normalized_candidate_item_ids": list(
                self.normalized_candidate_item_ids
            ),
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True, slots=True)
class DeterministicCorpusMetrics:
    total_inputs: int
    disposition_counts: tuple[tuple[str, int], ...]
    automatic_provenance_count: int
    automatic_merge_count: int
    normalized_candidate_count: int
    cross_space_exact_candidate_count: int
    evidence_sha256: str
    rule_version: str = DETERMINISTIC_INGEST_RULE_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "automatic_merge_count": self.automatic_merge_count,
            "automatic_provenance_count": self.automatic_provenance_count,
            "cross_space_exact_candidate_count": (
                self.cross_space_exact_candidate_count
            ),
            "disposition_counts": dict(self.disposition_counts),
            "evidence_sha256": self.evidence_sha256,
            "normalized_candidate_count": self.normalized_candidate_count,
            "rule_version": self.rule_version,
            "total_inputs": self.total_inputs,
        }


@dataclass(frozen=True, slots=True)
class DeterministicLintMetrics:
    total_inputs: int
    inputs_with_blockers_or_errors: int
    severity_counts: tuple[tuple[str, int], ...]
    finding_code_counts: tuple[tuple[str, int], ...]
    evidence_sha256: str
    rule_version: str = DETERMINISTIC_INGEST_RULE_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "finding_code_counts": dict(self.finding_code_counts),
            "inputs_with_blockers_or_errors": self.inputs_with_blockers_or_errors,
            "rule_version": self.rule_version,
            "severity_counts": dict(self.severity_counts),
            "total_inputs": self.total_inputs,
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
        raise DeterministicIngestPolicyError(
            "Deterministic evidence must be finite JSON."
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise DeterministicIngestPolicyError(
            f"{field} must be a canonical lowercase UUID."
        ) from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise DeterministicIngestPolicyError(
            f"{field} must be a canonical lowercase UUID."
        )
    return canonical


def _optional_uuid(value: object, *, field: str) -> str | None:
    return None if value is None else _uuid(value, field=field)


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise DeterministicIngestPolicyError(
            f"{field} must be a lowercase SHA-256 value."
        )
    return value


def _size(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_INTEGER
    ):
        raise DeterministicIngestPolicyError(
            f"{field} must be a non-negative integer."
        )
    return value


def _validate_fingerprint(
    value: DeterministicContentFingerprint,
    *,
    proposed: bool,
) -> DeterministicContentFingerprint:
    if not isinstance(value, DeterministicContentFingerprint):
        raise TypeError("A DeterministicContentFingerprint is required.")
    space_id = _uuid(value.space_id, field="space_id")
    item_id = _optional_uuid(value.item_id, field="item_id")
    revision_id = _optional_uuid(value.revision_id, field="revision_id")
    if proposed and (item_id is not None or revision_id is not None):
        raise DeterministicIngestPolicyError(
            "A proposed fingerprint cannot claim canonical target IDs."
        )
    if not proposed and (item_id is None or revision_id is None):
        raise DeterministicIngestPolicyError(
            "An observed fingerprint requires canonical item and revision IDs."
        )
    if value.normalizer_version != DETERMINISTIC_NORMALIZER_VERSION:
        raise DeterministicIngestPolicyError(
            "The deterministic normalizer version is unsupported."
        )
    return DeterministicContentFingerprint(
        space_id=space_id,
        item_id=item_id,
        revision_id=revision_id,
        source_identity_sha256=_sha(
            value.source_identity_sha256, field="source_identity_sha256"
        ),
        raw_content_sha256=_sha(
            value.raw_content_sha256, field="raw_content_sha256"
        ),
        normalized_content_sha256=_sha(
            value.normalized_content_sha256,
            field="normalized_content_sha256",
        ),
        content_size_bytes=_size(
            value.content_size_bytes, field="content_size_bytes"
        ),
    )


def build_deterministic_fingerprint(
    markdown: str,
    *,
    space_id: str,
    source_identity_sha256: str,
    item_id: str | None = None,
    revision_id: str | None = None,
) -> DeterministicContentFingerprint:
    """Build the frozen exact/LF-normalized hash tuple from in-memory text."""

    if not isinstance(markdown, str) or "\x00" in markdown:
        raise DeterministicIngestPolicyError("Markdown is invalid.")
    raw = markdown.encode("utf-8")
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").encode(
        "utf-8"
    )
    return _validate_fingerprint(
        DeterministicContentFingerprint(
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
            source_identity_sha256=source_identity_sha256,
            raw_content_sha256=hashlib.sha256(raw).hexdigest(),
            normalized_content_sha256=hashlib.sha256(normalized).hexdigest(),
            content_size_bytes=len(raw),
        ),
        proposed=item_id is None and revision_id is None,
    )


def _line_column(markdown: str, offset: int) -> tuple[int, int]:
    line = markdown.count("\n", 0, offset) + 1
    previous = markdown.rfind("\n", 0, offset)
    return line, offset - previous


def lint_markdown_deterministic(
    markdown: str,
) -> tuple[DeterministicLintFinding, ...]:
    """Return bounded non-semantic T0 structural evidence.

    Blockers/errors preserve the P4.1 contract.  P6.1 adds warning-only,
    first-occurrence evidence for duplicate H1 headings, heading-level jumps,
    and abnormally long lines.  Headings inside fenced code or leading YAML
    front matter are ignored.
    """

    if not isinstance(markdown, str):
        raise DeterministicIngestPolicyError("Markdown is invalid.")
    findings: list[DeterministicLintFinding] = []
    if not markdown.strip():
        findings.append(
            DeterministicLintFinding("empty_markdown", "blocker", None, None)
        )
    for offset, character in enumerate(markdown):
        codepoint = ord(character)
        if (codepoint < 0x20 and character not in "\t\n\r") or codepoint == 0x7F:
            line, column = _line_column(markdown, offset)
            findings.append(
                DeterministicLintFinding(
                    "unsafe_control_character", "error", line, column
                )
            )
            break

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    front_matter_end = -1
    if lines and lines[0] == "---":
        closing = next(
            (index for index, line in enumerate(lines[1:], start=1) if line == "---"),
            None,
        )
        if closing is None:
            findings.append(
                DeterministicLintFinding(
                    "unterminated_front_matter", "blocker", 1, 1
                )
            )
            front_matter_end = len(lines)
        else:
            front_matter_end = closing

    fence: tuple[str, int] | None = None
    heading_count = 0
    previous_heading_level: int | None = None
    duplicate_h1_reported = False
    heading_jump_reported = False
    long_line_reported = False
    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip(" ")
        marker = (
            "```"
            if stripped.startswith("```")
            else "~~~" if stripped.startswith("~~~") else None
        )
        if marker is not None:
            run_length = 0
            while run_length < len(stripped) and stripped[run_length] == marker[0]:
                run_length += 1
            if run_length >= 3:
                token = marker[0] * run_length
                if fence is None:
                    fence = (token, index)
                elif marker[0] == fence[0][0] and run_length >= len(fence[0]):
                    fence = None
                continue
        if fence is not None or (front_matter_end >= 0 and index <= front_matter_end + 1):
            continue
        if not long_line_reported and len(line) > MAX_DETERMINISTIC_LINE_CHARACTERS:
            findings.append(
                DeterministicLintFinding(
                    "line_too_long",
                    "warning",
                    index,
                    MAX_DETERMINISTIC_LINE_CHARACTERS + 1,
                )
            )
            long_line_reported = True
        heading = _HEADING_RE.match(line)
        if heading is None:
            continue
        level = len(heading.group(1))
        if level == 1:
            heading_count += 1
            if heading_count > 1 and not duplicate_h1_reported:
                findings.append(
                    DeterministicLintFinding("duplicate_h1", "warning", index, 1)
                )
                duplicate_h1_reported = True
        if (
            previous_heading_level is not None
            and level > previous_heading_level + 1
            and not heading_jump_reported
        ):
            findings.append(
                DeterministicLintFinding(
                    "heading_level_jump", "warning", index, 1
                )
            )
            heading_jump_reported = True
        previous_heading_level = level
    if fence is not None:
        findings.append(
            DeterministicLintFinding(
                "unterminated_code_fence", "error", fence[1], 1
            )
        )

    return tuple(
        sorted(
            set(findings),
            key=lambda finding: (
                finding.line is None,
                finding.line or 0,
                finding.column or 0,
                finding.code,
            ),
        )
    )


def _same_exact_content(
    left: DeterministicContentFingerprint,
    right: DeterministicContentFingerprint,
) -> bool:
    return (
        left.raw_content_sha256 == right.raw_content_sha256
        and left.normalized_content_sha256 == right.normalized_content_sha256
        and left.content_size_bytes == right.content_size_bytes
        and left.normalizer_version == right.normalizer_version
    )


def classify_deterministic_ingest(
    proposed: DeterministicContentFingerprint,
    existing: Sequence[DeterministicContentFingerprint],
) -> DeterministicIngestDecision:
    """Classify one input without semantic inference or cross-space effects."""

    proposed = _validate_fingerprint(proposed, proposed=True)
    if isinstance(existing, (str, bytes, bytearray)) or not isinstance(
        existing, Sequence
    ):
        raise TypeError("existing must be a sequence of observed fingerprints.")
    observed = tuple(
        _validate_fingerprint(value, proposed=False) for value in existing
    )
    item_ids = tuple(value.item_id for value in observed)
    revision_ids = tuple(value.revision_id for value in observed)
    if len(set(item_ids)) != len(item_ids) or len(set(revision_ids)) != len(
        revision_ids
    ):
        raise DeterministicCorpusConflictError(
            "The candidate corpus contains duplicate canonical identities."
        )
    ordered = tuple(
        sorted(observed, key=lambda value: (value.space_id, value.item_id or ""))
    )
    same_space = tuple(value for value in ordered if value.space_id == proposed.space_id)
    same_source = tuple(
        value
        for value in same_space
        if value.source_identity_sha256 == proposed.source_identity_sha256
    )
    if len(same_source) > 1:
        raise DeterministicCorpusConflictError(
            "One source identity resolves to multiple current items in one space."
        )
    exact = tuple(
        value for value in same_space if _same_exact_content(value, proposed)
    )
    if len(exact) > 1:
        raise DeterministicCorpusConflictError(
            "More than one exact current body exists in the target space."
        )
    normalized = tuple(
        value
        for value in same_space
        if value.normalized_content_sha256 == proposed.normalized_content_sha256
        and not _same_exact_content(value, proposed)
    )
    cross_space_exact = tuple(
        value
        for value in ordered
        if value.space_id != proposed.space_id
        and _same_exact_content(value, proposed)
    )

    match: DeterministicContentFingerprint | None = None
    if same_source:
        match = same_source[0]
        if _same_exact_content(match, proposed):
            disposition: DeterministicDisposition = "no_op"
        else:
            disposition = "source_update_candidate"
        effect: DeterministicAutomaticEffect = "none"
    elif exact:
        match = exact[0]
        disposition = "exact_repeat"
        effect = "provenance"
    elif normalized:
        disposition = "normalized_candidate"
        effect = "none"
    else:
        disposition = "new"
        effect = "none"

    normalized_item_ids = tuple(
        sorted(value.item_id for value in normalized if value.item_id is not None)
    )
    cross_space_item_ids = tuple(
        sorted(
            value.item_id for value in cross_space_exact if value.item_id is not None
        )
    )
    fingerprint_sha = _canonical_sha256(proposed.as_evidence())
    evidence_payload = {
        "automatic_effect": effect,
        "cross_space_exact_item_ids": list(cross_space_item_ids),
        "disposition": disposition,
        "input_fingerprint_sha256": fingerprint_sha,
        "matched_item_id": None if match is None else match.item_id,
        "matched_revision_id": None if match is None else match.revision_id,
        "normalized_candidate_item_ids": list(normalized_item_ids),
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
    }
    return DeterministicIngestDecision(
        disposition=disposition,
        automatic_effect=effect,
        matched_item_id=None if match is None else match.item_id,
        matched_revision_id=None if match is None else match.revision_id,
        normalized_candidate_item_ids=normalized_item_ids,
        cross_space_exact_item_ids=cross_space_item_ids,
        input_fingerprint_sha256=fingerprint_sha,
        evidence_sha256=_canonical_sha256(evidence_payload),
    )


def build_deterministic_corpus_metrics(
    decisions: Sequence[DeterministicIngestDecision],
) -> DeterministicCorpusMetrics:
    """Aggregate order-independent, body-free P6.1 corpus metrics."""

    if isinstance(decisions, (str, bytes, bytearray)) or not isinstance(
        decisions, Sequence
    ):
        raise TypeError("decisions must be a sequence.")
    values = tuple(_validate_decision(value) for value in decisions)
    if len({value.input_fingerprint_sha256 for value in values}) != len(values):
        raise DeterministicCorpusConflictError(
            "A corpus metric input fingerprint is duplicated."
        )
    ordered = tuple(sorted(values, key=lambda value: value.input_fingerprint_sha256))
    counts = Counter(value.disposition for value in ordered)
    count_items = tuple(sorted((key, int(count)) for key, count in counts.items()))
    payload = {
        "automatic_merge_count": 0,
        "automatic_provenance_count": sum(
            value.automatic_effect == "provenance" for value in ordered
        ),
        "cross_space_exact_candidate_count": sum(
            len(value.cross_space_exact_item_ids) for value in ordered
        ),
        "decision_evidence_sha256": [value.evidence_sha256 for value in ordered],
        "disposition_counts": dict(count_items),
        "normalized_candidate_count": counts.get("normalized_candidate", 0),
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
        "total_inputs": len(ordered),
    }
    return DeterministicCorpusMetrics(
        total_inputs=len(ordered),
        disposition_counts=count_items,
        automatic_provenance_count=payload["automatic_provenance_count"],  # type: ignore[arg-type]
        automatic_merge_count=0,
        normalized_candidate_count=int(counts.get("normalized_candidate", 0)),
        cross_space_exact_candidate_count=payload[
            "cross_space_exact_candidate_count"
        ],  # type: ignore[arg-type]
        evidence_sha256=_canonical_sha256(payload),
    )


def _validate_decision(
    value: DeterministicIngestDecision,
) -> DeterministicIngestDecision:
    if not isinstance(value, DeterministicIngestDecision):
        raise TypeError("A DeterministicIngestDecision is required.")
    dispositions = {
        "new",
        "no_op",
        "exact_repeat",
        "normalized_candidate",
        "source_update_candidate",
    }
    if value.disposition not in dispositions:
        raise DeterministicIngestPolicyError("The decision disposition is invalid.")
    expected_effect = "provenance" if value.disposition == "exact_repeat" else "none"
    if value.automatic_effect != expected_effect:
        raise DeterministicIngestPolicyError(
            "The decision automatic effect exceeds its disposition."
        )
    matched_required = value.disposition in {
        "no_op",
        "exact_repeat",
        "source_update_candidate",
    }
    item_id = _optional_uuid(value.matched_item_id, field="matched_item_id")
    revision_id = _optional_uuid(
        value.matched_revision_id, field="matched_revision_id"
    )
    if matched_required != (item_id is not None and revision_id is not None):
        raise DeterministicIngestPolicyError(
            "The decision target identity does not match its disposition."
        )
    normalized_ids = tuple(
        _uuid(item, field="normalized_candidate_item_id")
        for item in value.normalized_candidate_item_ids
    )
    cross_ids = tuple(
        _uuid(item, field="cross_space_exact_item_id")
        for item in value.cross_space_exact_item_ids
    )
    if (
        normalized_ids != tuple(sorted(set(normalized_ids)))
        or cross_ids != tuple(sorted(set(cross_ids)))
        or set(normalized_ids) & set(cross_ids)
        or (item_id is not None and item_id in {*normalized_ids, *cross_ids})
    ):
        raise DeterministicIngestPolicyError(
            "The decision candidate identities are not canonical and disjoint."
        )
    fingerprint_sha = _sha(
        value.input_fingerprint_sha256, field="input_fingerprint_sha256"
    )
    if value.rule_version != DETERMINISTIC_INGEST_RULE_VERSION:
        raise DeterministicIngestPolicyError(
            "A decision uses an unsupported rule version."
        )
    payload = {
        "automatic_effect": expected_effect,
        "cross_space_exact_item_ids": list(cross_ids),
        "disposition": value.disposition,
        "input_fingerprint_sha256": fingerprint_sha,
        "matched_item_id": item_id,
        "matched_revision_id": revision_id,
        "normalized_candidate_item_ids": list(normalized_ids),
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
    }
    if _sha(value.evidence_sha256, field="evidence_sha256") != _canonical_sha256(
        payload
    ):
        raise DeterministicIngestPolicyError(
            "The decision evidence hash does not bind its canonical fields."
        )
    return DeterministicIngestDecision(
        disposition=value.disposition,
        automatic_effect=expected_effect,
        matched_item_id=item_id,
        matched_revision_id=revision_id,
        normalized_candidate_item_ids=normalized_ids,
        cross_space_exact_item_ids=cross_ids,
        input_fingerprint_sha256=fingerprint_sha,
        evidence_sha256=value.evidence_sha256,
    )


def build_deterministic_lint_metrics(
    findings_by_input: Mapping[str, Sequence[DeterministicLintFinding]],
) -> DeterministicLintMetrics:
    """Aggregate bounded structural-lint metrics keyed by input fingerprint."""

    if not isinstance(findings_by_input, Mapping):
        raise TypeError("findings_by_input must be a mapping.")
    rows: list[dict[str, object]] = []
    severity_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    blocked_inputs = 0
    for fingerprint, raw_findings in sorted(findings_by_input.items()):
        fingerprint = _sha(fingerprint, field="input_fingerprint_sha256")
        if isinstance(raw_findings, (str, bytes, bytearray)) or not isinstance(
            raw_findings, Sequence
        ):
            raise TypeError("Lint findings must be sequences.")
        findings: list[DeterministicLintFinding] = []
        for finding in raw_findings:
            if not isinstance(finding, DeterministicLintFinding):
                raise TypeError("DeterministicLintFinding values are required.")
            if finding.code not in {
                "duplicate_h1",
                "empty_markdown",
                "heading_level_jump",
                "line_too_long",
                "unsafe_control_character",
                "unterminated_code_fence",
                "unterminated_front_matter",
            } or finding.severity not in {"blocker", "error", "warning"}:
                raise DeterministicIngestPolicyError(
                    "A lint finding uses an unknown rule or severity."
                )
            expected_severity = {
                "empty_markdown": "blocker",
                "unsafe_control_character": "error",
                "unterminated_front_matter": "blocker",
                "unterminated_code_fence": "error",
                "duplicate_h1": "warning",
                "heading_level_jump": "warning",
                "line_too_long": "warning",
            }[finding.code]
            if finding.severity != expected_severity:
                raise DeterministicIngestPolicyError(
                    "A lint finding severity does not match the frozen rule."
                )
            for field_name, position in (
                ("line", finding.line),
                ("column", finding.column),
            ):
                if position is not None and (
                    isinstance(position, bool)
                    or not isinstance(position, int)
                    or position < 1
                ):
                    raise DeterministicIngestPolicyError(
                        f"finding.{field_name} is invalid."
                    )
            findings.append(finding)
        canonical_findings = tuple(
            sorted(
                set(findings),
                key=lambda item: (
                    item.line is None,
                    item.line or 0,
                    item.column or 0,
                    item.code,
                ),
            )
        )
        if tuple(raw_findings) != canonical_findings:
            raise DeterministicIngestPolicyError(
                "Lint findings must be canonical and unique."
            )
        if any(item.severity in {"blocker", "error"} for item in canonical_findings):
            blocked_inputs += 1
        severity_counts.update(item.severity for item in canonical_findings)
        code_counts.update(item.code for item in canonical_findings)
        rows.append(
            {
                "findings": [
                    {
                        "code": item.code,
                        "column": item.column,
                        "line": item.line,
                        "severity": item.severity,
                    }
                    for item in canonical_findings
                ],
                "input_fingerprint_sha256": fingerprint,
            }
        )
    payload = {
        "finding_code_counts": dict(sorted(code_counts.items())),
        "inputs": rows,
        "inputs_with_blockers_or_errors": blocked_inputs,
        "rule_version": DETERMINISTIC_INGEST_RULE_VERSION,
        "severity_counts": dict(sorted(severity_counts.items())),
        "total_inputs": len(rows),
    }
    return DeterministicLintMetrics(
        total_inputs=len(rows),
        inputs_with_blockers_or_errors=blocked_inputs,
        severity_counts=tuple(sorted(severity_counts.items())),
        finding_code_counts=tuple(sorted(code_counts.items())),
        evidence_sha256=_canonical_sha256(payload),
    )


__all__ = [
    "DETERMINISTIC_INGEST_RULE_VERSION",
    "DETERMINISTIC_NORMALIZER_VERSION",
    "DeterministicContentFingerprint",
    "DeterministicCorpusConflictError",
    "DeterministicCorpusMetrics",
    "DeterministicIngestDecision",
    "DeterministicIngestPolicyError",
    "DeterministicLintMetrics",
    "DeterministicLintFinding",
    "MAX_DETERMINISTIC_LINE_CHARACTERS",
    "build_deterministic_corpus_metrics",
    "build_deterministic_fingerprint",
    "build_deterministic_lint_metrics",
    "classify_deterministic_ingest",
    "lint_markdown_deterministic",
]
