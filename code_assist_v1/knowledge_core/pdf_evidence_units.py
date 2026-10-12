"""Versioned server-owned PDF evidence ranges, not semantic verification.

Only prepared server source blocks enter this compiler. Catalogs are bound to
the immutable source, writing policy, request and schema. They are checked once
per resolver; cumulative claim work is bounded without rehashing per claim.
"""
from dataclasses import dataclass
import hmac
import json
import re
import secrets
from types import MappingProxyType
from uuid import UUID

from .wiki_authoring import (AuthoringSourceError, AuthoringOutputError,
    AuthoringWorkLimitError, _json, _hash)

POLICY_VERSION = "wiki-pdf-page-visual-writing-v3"
POLICY_VERSION_V4 = "wiki-pdf-page-visual-writing-v4"
POLICY_VERSION_V5 = "wiki-pdf-section-writing-v5"
POLICY_VERSION_V6 = "wiki-pdf-section-writing-v6"
POLICY_VERSION_V7 = "wiki-pdf-section-writing-v7"
POLICY_VERSION_V8 = "wiki-pdf-section-writing-v8"
POLICY_VERSION_V9 = "wiki-pdf-section-writing-v9"
POLICY_VERSION_V10 = "wiki-pdf-section-writing-v10"
POLICY_VERSION_V11 = "wiki-pdf-section-writing-v11"
POLICY_VERSION_V12 = "wiki-pdf-layout-retention-v1"
POLICY_VERSION_V13 = "wiki-pdf-layout-retention-v2"
POLICY_VERSION_V14 = "wiki-pdf-layout-retention-v3"
POLICY_VERSION_V15 = "wiki-pdf-layout-retention-v4"
POLICY_VERSION_V16 = "wiki-pdf-layout-retention-v5"
POLICY_VERSION_V17 = "wiki-pdf-layout-retention-v6"
POLICY_VERSION_V18 = "wiki-pdf-layout-retention-v7"
POLICY_VERSION_V19 = "wiki-pdf-layout-retention-v8"
POLICY_VERSION_V20 = "wiki-pdf-layout-retention-v9"
POLICY_VERSION_V21 = "wiki-pdf-layout-retention-v10"
POLICY_VERSION_V22 = "wiki-pdf-layout-retention-v11"
POLICY_VERSION_V23 = "wiki-pdf-layout-retention-v12"
POLICY_VERSION_V24 = "wiki-pdf-layout-retention-v13"
POLICY_VERSION_V25 = "wiki-pdf-layout-retention-v14"
POLICY_VERSION_V26 = "wiki-pdf-layout-retention-v15"
POLICY_VERSION_V27 = "wiki-pdf-layout-retention-v16"
POLICY_VERSION_V28 = "wiki-pdf-layout-retention-v17"
POLICY_VERSION_V29 = "wiki-pdf-layout-retention-v18"
POLICY_VERSION_V30 = "wiki-pdf-layout-retention-v19"
POLICY_VERSION_V31 = "wiki-pdf-layout-retention-v20"
POLICY_VERSION_V32 = "wiki-pdf-layout-retention-v21"
POLICY_VERSION_V33 = "wiki-pdf-v10-literal-tables-v1"
POLICY_VERSION_V34 = "wiki-pdf-v10-chart-tables-v1"
POLICY_VERSION_V35 = "wiki-pdf-v10-chart-labels-v1"
POLICY_VERSION_V36 = "wiki-pdf-v10-numeric-append-v1"
EVIDENCE_VERSION = "pdf-source-evidence-units-v1"
SCHEMA_VERSION = 2
UNIT_CODEPOINTS = 256
MAX_UNITS = 32768
MAX_SOURCE_BYTES = 8_388_608
MAX_CATALOG_BYTES = 16_777_216
MAX_REFERENCES = 4096
MAX_RESOLVED_CODEPOINTS = 8_388_608
_KEY = secrets.token_bytes(32)
_SOURCE_FIELDS = {"source_id", "block_id", "page", "kind", "text", "range", "range_domain"}
_PAYLOAD_FIELDS = {"schema_version", "policy_version", "plan_sha256", "batch_id", "page",
    "prose_tasks", "visual_task", "source_catalog", "context_is_location_only",
    "all_detected_cells_rendered_by_server", "caption_ownership_inferred"}
_IDENTITY_FIELDS = {"snapshot_id", "raw_sha256", "geometry_sha256", "visual_source_sha256"}


def evidence_policy_identity():
    return dict(version=EVIDENCE_VERSION, schema_version=SCHEMA_VERSION,
        unit_codepoints=UNIT_CODEPOINTS, maximum_units=MAX_UNITS,
        maximum_source_bytes=MAX_SOURCE_BYTES, maximum_catalog_bytes=MAX_CATALOG_BYTES,
        maximum_references=MAX_REFERENCES, maximum_resolved_codepoints=MAX_RESOLVED_CODEPOINTS,
        per_claim_references=64, minimum_nonblank_owned_units=1,
        reference_policy="owned_plus_exact_allowed_context")


def _need(condition, code, error=AuthoringSourceError):
    if not condition:
        raise error(code)


def _identifier(value):
    _need(type(value) is str and 0 < len(value) <= 160
        and all(ord(c) >= 32 for c in value), "PDF_EVIDENCE_IDENTIFIER")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AuthoringSourceError("PDF_EVIDENCE_IDENTIFIER") from exc
    return value


def _digest(value):
    _need(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None,
        "PDF_EVIDENCE_DIGEST")


def _source(row, page, *, owner=False, multi_page=False):
    _need(type(row) is dict and set(row) == _SOURCE_FIELDS, "PDF_EVIDENCE_SOURCE_SHAPE")
    for key in ("source_id", "block_id"):
        _identifier(row[key])
    _need(type(row["page"]) is int and row["page"] >= 1
        and (multi_page or (row["page"] == page if owner else abs(row["page"]-page) <= 1)),
        "PDF_EVIDENCE_SOURCE_PAGE")
    _need(row["kind"] in ("prose", "inferred_grid")
        and (not owner or row["kind"] == "prose"), "PDF_EVIDENCE_SOURCE_KIND")
    domain = "pdf_prose_codepoints" if row["kind"] == "prose" else "pdf_table_html_codepoints"
    _need(row["range_domain"] == domain, "PDF_EVIDENCE_RANGE_DOMAIN")
    span, text = row["range"], row["text"]
    _need(type(text) is str and "\x00" not in text, "PDF_EVIDENCE_SOURCE_TEXT")
    try:
        size = len(text.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        raise AuthoringSourceError("PDF_EVIDENCE_SOURCE_TEXT") from exc
    _need(size <= MAX_SOURCE_BYTES, "PDF_EVIDENCE_SOURCE_SIZE", AuthoringWorkLimitError)
    _need(type(span) is list and len(span) == 2 and all(type(n) is int for n in span)
        and 0 <= span[0] <= span[1] and span[1]-span[0] == len(text),
        "PDF_EVIDENCE_SOURCE_RANGE")
    if not owner:
        _need(row["source_id"] == row["block_id"] and span == [0, len(text)],
            "PDF_EVIDENCE_FULL_SOURCE")
    return size


def _seal(canonical, wire, digest):
    return hmac.digest(_KEY, (digest+"\n"+canonical+"\n"+wire).encode("utf-8"), "sha256").hex()


@dataclass(frozen=True, slots=True)
class PdfEvidenceCatalog:
    canonical_json: str
    wire_json: str
    sha256: str
    seal: str

    def payload(self):
        return json.loads(self.wire_json)


def compile_pdf_evidence(payload, source_identity, *, catalog_bytes=MAX_CATALOG_BYTES):
    """Compile ONLY from validated plan-owned full blocks and owned subranges."""
    _need(type(catalog_bytes) is int and 0 < catalog_bytes <= MAX_CATALOG_BYTES,
        "PDF_EVIDENCE_CATALOG_BUDGET")
    _need(type(payload) is dict and set(payload) == _PAYLOAD_FIELDS, "PDF_EVIDENCE_PAYLOAD_SHAPE")
    _need(type(payload["schema_version"]) is int and payload["schema_version"] == SCHEMA_VERSION
        and payload["policy_version"] in (POLICY_VERSION, POLICY_VERSION_V4, POLICY_VERSION_V5, POLICY_VERSION_V6, POLICY_VERSION_V7, POLICY_VERSION_V8, POLICY_VERSION_V9, POLICY_VERSION_V10, POLICY_VERSION_V11, POLICY_VERSION_V12, POLICY_VERSION_V13, POLICY_VERSION_V14, POLICY_VERSION_V15, POLICY_VERSION_V16, POLICY_VERSION_V17, POLICY_VERSION_V18, POLICY_VERSION_V19, POLICY_VERSION_V20, POLICY_VERSION_V21, POLICY_VERSION_V22, POLICY_VERSION_V23, POLICY_VERSION_V24, POLICY_VERSION_V25, POLICY_VERSION_V26, POLICY_VERSION_V27, POLICY_VERSION_V28, POLICY_VERSION_V29, POLICY_VERSION_V30, POLICY_VERSION_V31, POLICY_VERSION_V32, POLICY_VERSION_V33, POLICY_VERSION_V34, POLICY_VERSION_V35, POLICY_VERSION_V36),
        "PDF_EVIDENCE_POLICY")
    _digest(payload["plan_sha256"])
    _identifier(payload["batch_id"])
    page = payload["page"]
    _need(type(page) is int and page >= 1, "PDF_EVIDENCE_PAGE")
    _need(payload["context_is_location_only"] is True
        and payload["all_detected_cells_rendered_by_server"] is True
        and payload["caption_ownership_inferred"] is False, "PDF_EVIDENCE_FLAGS")
    _need(type(source_identity) is dict and set(source_identity) == _IDENTITY_FIELDS,
        "PDF_EVIDENCE_SOURCE_IDENTITY")
    try:
        canonical_uuid = type(source_identity["snapshot_id"]) is str and str(UUID(source_identity["snapshot_id"])) == source_identity["snapshot_id"]
    except (ValueError, TypeError, AttributeError):
        canonical_uuid = False
    _need(canonical_uuid, "PDF_EVIDENCE_SNAPSHOT")
    for key in _IDENTITY_FIELDS - {"snapshot_id"}:
        _digest(source_identity[key])
    rows, tasks = payload["source_catalog"], payload["prose_tasks"]
    _need(type(rows) is list and len(rows) <= 8192 and type(tasks) is list and len(tasks) <= 16,
        "PDF_EVIDENCE_LIST_LIMIT")
    visual = payload["visual_task"]
    if visual is not None:
        _need(type(visual) is dict and set(visual) == {"page", "width", "height", "source_id", "sha256", "bytes", "media_type"},
            "PDF_EVIDENCE_VISUAL_SHAPE")
        _need(type(visual["page"]) is int and visual["page"] == page
            and all(type(visual[k]) is int and visual[k] > 0 for k in ("width", "height", "bytes"))
            and visual["media_type"] == "image/png", "PDF_EVIDENCE_VISUAL_IDENTITY")
        _identifier(visual["source_id"])
        _digest(visual["sha256"])
    sources, total = {}, 0
    for row in rows:
        total += _source(row, page, multi_page=payload["policy_version"] in (POLICY_VERSION_V5, POLICY_VERSION_V6, POLICY_VERSION_V7, POLICY_VERSION_V8, POLICY_VERSION_V9, POLICY_VERSION_V10, POLICY_VERSION_V11, POLICY_VERSION_V12, POLICY_VERSION_V13, POLICY_VERSION_V14, POLICY_VERSION_V15, POLICY_VERSION_V16, POLICY_VERSION_V17, POLICY_VERSION_V18, POLICY_VERSION_V19, POLICY_VERSION_V20, POLICY_VERSION_V21, POLICY_VERSION_V22, POLICY_VERSION_V23, POLICY_VERSION_V24, POLICY_VERSION_V25, POLICY_VERSION_V26, POLICY_VERSION_V27, POLICY_VERSION_V28, POLICY_VERSION_V29, POLICY_VERSION_V30, POLICY_VERSION_V31, POLICY_VERSION_V32, POLICY_VERSION_V33, POLICY_VERSION_V34, POLICY_VERSION_V35, POLICY_VERSION_V36))
        _need(row["source_id"] not in sources, "PDF_EVIDENCE_DUPLICATE_SOURCE")
        _need(total <= MAX_SOURCE_BYTES, "PDF_EVIDENCE_SOURCE_SIZE", AuthoringWorkLimitError)
        sources[row["source_id"]] = row
    contexts = dict(sources)
    task_rows = {}
    for task in tasks:
        _need(type(task) is dict and set(task) == {"chunk_id", "owned_source", "context_ids"},
            "PDF_EVIDENCE_TASK_SHAPE")
        key = _identifier(task["chunk_id"])
        _need(key not in task_rows and key not in sources, "PDF_EVIDENCE_DUPLICATE_TASK")
        own = task["owned_source"]
        total += _source(own, page, owner=True, multi_page=payload["policy_version"] in (POLICY_VERSION_V5, POLICY_VERSION_V6, POLICY_VERSION_V7, POLICY_VERSION_V8, POLICY_VERSION_V9, POLICY_VERSION_V10, POLICY_VERSION_V11, POLICY_VERSION_V12, POLICY_VERSION_V13, POLICY_VERSION_V14, POLICY_VERSION_V15, POLICY_VERSION_V16, POLICY_VERSION_V17, POLICY_VERSION_V18, POLICY_VERSION_V19, POLICY_VERSION_V20, POLICY_VERSION_V21, POLICY_VERSION_V22, POLICY_VERSION_V23, POLICY_VERSION_V24, POLICY_VERSION_V25, POLICY_VERSION_V26, POLICY_VERSION_V27, POLICY_VERSION_V28, POLICY_VERSION_V29, POLICY_VERSION_V30, POLICY_VERSION_V31, POLICY_VERSION_V32, POLICY_VERSION_V33, POLICY_VERSION_V34, POLICY_VERSION_V35, POLICY_VERSION_V36))
        _need(total <= MAX_SOURCE_BYTES, "PDF_EVIDENCE_SOURCE_SIZE", AuthoringWorkLimitError)
        _need(own["source_id"] == key and own["block_id"] in contexts,
            "PDF_EVIDENCE_OWNERSHIP")
        full = contexts[own["block_id"]]
        start, end = own["range"]
        _need(full["kind"] == "prose" and full["page"] == own["page"]
            and end <= len(full["text"]) and full["text"][start:end] == own["text"]
            and own["text"].strip(), "PDF_EVIDENCE_OWNED_RANGE")
        allowed = task["context_ids"]
        expected = [sid for sid, src in contexts.items() if sid != own["block_id"] or own["text"] != src["text"]]
        _need(type(allowed) is list and allowed == expected, "PDF_EVIDENCE_CONTEXT")
        sources[key] = own
        task_rows[key] = task
    units, aliases, descriptors = [], {}, []
    for sid, row in sources.items():
        aliases[sid] = []
        text, at = row["text"], 0
        descriptors.append({k: row[k] for k in _SOURCE_FIELDS - {"text"}} | {"text_sha256": _hash(text)})
        while at < len(text):
            end = min(at+UNIT_CODEPOINTS, len(text))
            newline = text.find("\n", at, end)
            if newline >= 0:
                end = newline+1
            _need(len(units) < MAX_UNITS, "PDF_EVIDENCE_UNIT_LIMIT", AuthoringWorkLimitError)
            alias = "E"+str(len(units)+1)
            unit = {k: row[k] for k in ("source_id", "block_id", "page", "range_domain")}
            unit.update(evidence_id=alias, text=text[at:end], range=[row["range"][0]+at, row["range"][0]+end])
            units.append(unit)
            aliases[sid].append(alias)
            at = end
    task_map = {key: {"owned": aliases[key],
        "context": [alias for sid in task["context_ids"] for alias in aliases[sid]]}
        for key, task in task_rows.items()}
    canonical = _json(dict(schema_version=SCHEMA_VERSION, policy_version=payload["policy_version"],
        evidence_version=EVIDENCE_VERSION, source=source_identity,
        plan_sha256=payload["plan_sha256"], batch_id=payload["batch_id"], page=page,
        evidence_policy=evidence_policy_identity(), source_descriptors=descriptors, units=units, tasks=task_map))
    _need(len(canonical.encode()) <= catalog_bytes, "PDF_EVIDENCE_CATALOG_SIZE", AuthoringWorkLimitError)
    digest = _hash(canonical)
    by_id = {unit["evidence_id"]: unit for unit in units}
    wire = {k: v for k, v in payload.items() if k not in ("source_catalog", "prose_tasks")}
    wire.update(evidence_version=EVIDENCE_VERSION, evidence_catalog_sha256=digest,
        source_catalog=[dict(source_id=sid, page=row["page"], kind=row["kind"],
            units=[[key, by_id[key]["text"]] for key in aliases[sid]]) for sid, row in sources.items()],
        prose_tasks=[dict(chunk_id=key, owned_evidence_ids=value["owned"],
            context_evidence_ids=value["context"]) for key, value in task_map.items()])
    wire_json = _json(wire)
    return PdfEvidenceCatalog(canonical, wire_json, digest, _seal(canonical, wire_json, digest))


@dataclass(frozen=True, slots=True)
class _Unit:
    evidence_id: str
    source_id: str
    block_id: str
    page: int
    range_domain: str
    start: int
    end: int
    text: str


class PdfEvidenceResolver:
    """One checked catalog per batch; no per-claim catalog hash or rebuild."""
    def __init__(self, catalog, *, reference_limit=MAX_REFERENCES, codepoint_limit=MAX_RESOLVED_CODEPOINTS):
        _need(type(catalog) is PdfEvidenceCatalog
            and all(type(getattr(catalog, key)) is str for key in ("canonical_json", "wire_json", "sha256", "seal")),
            "PDF_EVIDENCE_CATALOG", AuthoringOutputError)
        try:
            valid = (len(catalog.canonical_json.encode()) <= MAX_CATALOG_BYTES
                and len(catalog.wire_json.encode()) <= MAX_CATALOG_BYTES
                and re.fullmatch("[0-9a-f]{64}", catalog.seal) is not None
                and re.fullmatch("[0-9a-f]{64}", catalog.sha256) is not None
                and hmac.compare_digest(catalog.seal, _seal(catalog.canonical_json, catalog.wire_json, catalog.sha256)))
        except UnicodeError:
            valid = False
        _need(valid,
            "PDF_EVIDENCE_CATALOG_SEAL", AuthoringOutputError)
        _need(type(reference_limit) is int and 0 < reference_limit <= MAX_REFERENCES
            and type(codepoint_limit) is int and 0 < codepoint_limit <= MAX_RESOLVED_CODEPOINTS,
            "PDF_EVIDENCE_RESOLVE_BUDGET")
        parsed = json.loads(catalog.canonical_json)
        self.catalog_sha256 = catalog.sha256
        self._units = MappingProxyType({u["evidence_id"]: _Unit(u["evidence_id"], u["source_id"],
            u["block_id"], u["page"], u["range_domain"], *u["range"], u["text"]) for u in parsed["units"]})
        self._tasks = MappingProxyType({k: (frozenset(v["owned"]), frozenset(v["owned"]+v["context"]))
            for k, v in parsed["tasks"].items()})
        self._references_left, self._codepoints_left = reference_limit, codepoint_limit

    def resolve(self, chunk_id, evidence_ids, *, catalog_sha256):
        _need(type(catalog_sha256) is str and catalog_sha256 == self.catalog_sha256,
            "PDF_EVIDENCE_CATALOG_IDENTITY", AuthoringOutputError)
        _need(type(chunk_id) is str and chunk_id in self._tasks, "PDF_EVIDENCE_TASK_IDENTITY", AuthoringOutputError)
        _need(type(evidence_ids) is list and 0 < len(evidence_ids) <= 64
            and all(type(key) is str and 0 < len(key) <= 16 for key in evidence_ids),
            "PDF_EVIDENCE_REFERENCE_LIST", AuthoringOutputError)
        selected = set(evidence_ids)
        _need(len(selected) == len(evidence_ids), "PDF_EVIDENCE_DUPLICATE_REFERENCE", AuthoringOutputError)
        own, allowed = self._tasks[chunk_id]
        _need(selected <= allowed, "PDF_EVIDENCE_UNAVAILABLE_REFERENCE", AuthoringOutputError)
        _need(bool(selected & own), "PDF_EVIDENCE_OWNED_REQUIRED", AuthoringOutputError)
        units = [self._units[key] for key in evidence_ids]
        _need(any(unit.evidence_id in own and unit.text.strip() for unit in units),
            "PDF_EVIDENCE_NONBLANK_OWNED_REQUIRED", AuthoringOutputError)
        count = sum(len(unit.text) for unit in units)
        _need(len(units) <= self._references_left and count <= self._codepoints_left,
            "PDF_EVIDENCE_CUMULATIVE_WORK", AuthoringOutputError)
        self._references_left -= len(units)
        self._codepoints_left -= count
        return [dict(source_id=u.source_id, text=u.text, page=u.page, block_id=u.block_id,
            range_domain=u.range_domain, range=[u.start, u.end], evidence_id=u.evidence_id)
            for u in units]
