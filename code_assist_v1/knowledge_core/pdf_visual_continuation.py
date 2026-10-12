"""Bounded forward PDF requests; no historical plan/output is modified.

Ownership is in original chunk Unicode codepoints, literal cells and complete
original images. Native presentation is retained for unsplit parent requests.
Refined text uses exact original substrings, never a falsely relabelled native
projection. The ordinary v1 Markdown decoder remains strict.
"""
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import re
import secrets
from uuid import UUID, uuid5

from .wiki_authoring import AuthoringWorkLimitError, AuthoringSourceError, _hash, _json
from .pdf_visual_authoring import (
    PdfVisualPlan, VisualBatch, VERSION_V36, ATOMIC_REQUEST_PARTITION_V14, _checked,
    _request, _request_images, _natural_markdown_decode, _literal_supplement,
    _checked_chart_supplement,_chart_render_options,
)
from . import pdf_section_writing as section

VERSION = "pdf-source-continuation-v1"
TASK = "canonical_pdf_continuation_batch_authoring_v1"
PLAN = "pdf_continuation.plan"
BATCH = "pdf_continuation.author_batch"
COMPLETE = "pdf_continuation.complete"
HANDLER_VERSION = "v1"
_NS = UUID("de1d4088-ced1-4b27-9f22-5ad79b8acb1d")
_KEY = secrets.token_bytes(32)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RANGE_KEYS = {"chunk_id", "chunk_ordinal", "block_id", "page", "start", "end", "text_sha256"}


def require(ok, message="PDF continuation identity or coverage changed"):
    if not ok:
        raise AuthoringSourceError(message)


def identity(kind, *values):
    return str(uuid5(_NS, kind + ":" + _json(values)))


@dataclass(frozen=True, slots=True)
class ContinuationPolicy:
    prompt_sha256: str
    version: str = VERSION
    context_tokens: int = 16384
    output_tokens: int = 4096
    safety_tokens: int = 1536
    max_executions: int = 2
    max_leaves: int = 4096
    max_depth: int = 8
    max_model_attempts: int = 512
    max_input_bytes: int = 134217728
    max_response_bytes: int = 33554432
    max_markdown_bytes: int = 4194304
    max_manifest_bytes: int = 16777216
    max_deadline_ms: int = 86400000
    min_codepoints: int = 32

    def encoded(self):
        from dataclasses import asdict
        value = asdict(self)
        require(type(self.prompt_sha256) is str and _SHA.fullmatch(self.prompt_sha256)
            and self.version == VERSION and self.context_tokens == 16384
            and self.output_tokens == 4096 and self.safety_tokens == 1536
            and self.max_executions in (1, 2)
            and all(type(value[k]) is int and 1 <= value[k] <= maximum for k, maximum in (
                ("max_leaves",4096),("max_depth",8),("max_model_attempts",512),
                ("max_input_bytes",134217728),("max_response_bytes",33554432),
                ("max_markdown_bytes",4194304),("max_manifest_bytes",16777216),
                ("max_deadline_ms",86400000),("min_codepoints",32))), "Invalid finite continuation policy")
        return _json(value)


def checked_coverage(value):
    require(type(value) is dict and set(value) == {"ranges","tables","visuals"}
        and all(type(value[k]) is list for k in value), "Invalid continuation coverage")
    for row in value["ranges"]:
        require(type(row) is dict and set(row) == _RANGE_KEYS
            and all(type(row[k]) is int for k in ("chunk_ordinal","page","start","end"))
            and 0 <= row["start"] < row["end"]
            and row["page"] > 0 and row["chunk_ordinal"] > 0
            and type(row["chunk_id"]) is str and row["chunk_id"].startswith("PVC_")
            and _SHA.fullmatch(row["chunk_id"][4:])
            and type(row["block_id"]) is str and row["block_id"]
            and type(row["text_sha256"]) is str and _SHA.fullmatch(row["text_sha256"]),
            "Invalid original range owner")
    for key, fields in (("tables",{"cell_key","text_sha256"}),
            ("visuals",{"page","image_id","sha256"})):
        require(all(type(r) is dict and set(r) == fields for r in value[key]), "Invalid asset owner")
    require(all(type(r["cell_key"]) is str and 1 <= len(r["cell_key"]) <= 300
        and type(r["text_sha256"]) is str and _SHA.fullmatch(r["text_sha256"]) for r in value["tables"]))
    require(all(type(r["page"]) is int and r["page"] > 0 and type(r["image_id"]) is str
        and r["image_id"].startswith("PV_") and _SHA.fullmatch(r["image_id"][3:])
        and type(r["sha256"]) is str and _SHA.fullmatch(r["sha256"]) for r in value["visuals"]))
    ranges = sorted(value["ranges"], key=lambda r:(r["chunk_ordinal"],r["start"]))
    require(all(a["chunk_id"] != b["chunk_id"] or a["end"] <= b["start"]
        for a,b in zip(ranges,ranges[1:])), "Overlapping source ranges")
    require(len({r["cell_key"] for r in value["tables"]}) == len(value["tables"])
        and len({r["image_id"] for r in value["visuals"]}) == len(value["visuals"]),
        "Repeated original asset owner")
    return json.loads(_json(value))


def exact_union(parent, children):
    parent = checked_coverage(parent)
    require(type(children) in (tuple,list) and 2 <= len(children) <= 16)
    children = tuple(checked_coverage(c) for c in children)
    for p in parent["ranges"]:
        matching = sorted((r for c in children for r in c["ranges"]
            if r["chunk_id"] == p["chunk_id"] and p["start"] <= r["start"]
            and r["end"] <= p["end"]), key=lambda r:r["start"])
        require(matching and matching[0]["start"] == p["start"]
            and matching[-1]["end"] == p["end"]
            and all(a["end"] == b["start"] for a,b in zip(matching,matching[1:]))
            and all(all(r[k] == p[k] for k in _RANGE_KEYS - {"start","end"}) for r in matching),
            "Child ranges must cover the exact original parent")
    require(all(any(all(r[k] == p[k] for k in _RANGE_KEYS - {"start","end"})
        and p["start"] <= r["start"] < r["end"] <= p["end"] for p in parent["ranges"])
        for c in children for r in c["ranges"]), "Foreign child source range")
    for key in ("tables","visuals"):
        require(Counter(_json(r) for r in parent[key])
            == Counter(_json(r) for c in children for r in c[key]), "Child asset union differs")
    return children


def _plan(plan):
    p = _checked(plan)
    require(p.policy_version == VERSION_V36
        and p.request_partition_policy == ATOMIC_REQUEST_PARTITION_V14,
        "Only the fresh ordinary source partition may be continued")
    return p


def _cells(plan):
    result = {}
    for table in json.loads(plan._literal_table_input_json):
        for ri,row in enumerate(table["rows"]):
            for ci,cell in enumerate(row["cells"]):
                key = table["unit_id"]+":"+str(ri)+":"+str(ci)
                result[key] = (table,ri,ci,cell)
    from ..document_pdf_geometry import PdfTableBlock
    for page in plan._doc.pages:
        for block in page.blocks:
            if type(block) is not PdfTableBlock: continue
            table = dict(unit_id=block.block_id,page=page.number,label="원문 추출 표",text="",
                column_bounds=list(range(block.columns+1)),rows=[dict(cells=[]) for _ in range(block.rows)])
            for cell in block.cells:
                projected = dict(start_column=cell.column,span=1,text=cell.text)
                ci = len(table["rows"][cell.row]["cells"])
                table["rows"][cell.row]["cells"].append(projected)
                result[block.block_id+":"+str(cell.row)+":"+str(ci)] = (table,cell.row,ci,projected)
    return result


def leaf_images(plan,coverage):
    p = _plan(plan)
    originals = tuple(p.source.pages[v["page"]-1].image for v in coverage["visuals"])
    for item,image in zip(coverage["visuals"],originals,strict=True):
        require(image.source_id == item["image_id"] and sha256(image.data).hexdigest() == item["sha256"],
            "Original page image differs")
    if not originals: return ()
    derived = {image.source_id:image for image in p._wire_image_derivation.images}
    require(all(image.source_id in derived for image in originals))
    return tuple(derived[image.source_id] for image in originals)


def parent_inventory(plan):
    p = _plan(plan)
    cells = _cells(p)
    owned_cells = set()
    inventory = []
    seen_images = set()
    for batch in p.batches:
        _,messages,sha,_ = _request(p,batch)
        pages = {p.chunks[n-1].page for n in batch.chunk_ordinals} | set(batch.visual_pages)
        ranges = [{"chunk_id":c.chunk_id,"chunk_ordinal":c.ordinal,"block_id":c.block_id,
            "page":c.page,"start":0,"end":len(c.text),"text_sha256":_hash(c.text)}
            for c in (p.chunks[n-1] for n in batch.chunk_ordinals) if c.text]
        tables = []
        for key,(table,_,_,cell) in cells.items():
            if table["page"] in pages and key not in owned_cells:
                owned_cells.add(key)
                tables.append({"cell_key":key,"text_sha256":_hash(cell["text"])})
        visuals = []
        for number in batch.visual_pages:
            page = p.source.pages[number-1]
            require(page.image.source_id not in seen_images, "Original image has repeated owners")
            seen_images.add(page.image.source_id)
            visuals.append({"page":number,"image_id":page.image.source_id,
                "sha256":sha256(page.image.data).hexdigest()})
        inventory.append({"ordinal":batch.ordinal,"batch_id":batch.batch_id,
            "request_sha256":sha,"coverage":checked_coverage(dict(ranges=ranges,tables=tables,visuals=visuals))})
    require(owned_cells == set(cells), "Some literal cells have no original writing owner")
    return tuple(inventory)


@dataclass(frozen=True, slots=True)
class ContinuationLeafRequest:
    parent_ordinal: int
    coverage_json: str
    payload_json: str = field(repr=False)
    request_sha256: str
    input_size_bytes: int
    seal: str = field(repr=False)

    def identity(self):
        return (self.parent_ordinal,self.coverage_json,self.payload_json,
            self.request_sha256,self.input_size_bytes)


def _seal(values):
    return hmac.digest(_KEY,_json(values).encode(),"sha256").hex()


def checked_leaf(leaf):
    require(type(leaf) is ContinuationLeafRequest
        and hmac.compare_digest(leaf.seal,_seal(leaf.identity())), "Unsealed continuation request")
    require(_json(checked_coverage(json.loads(leaf.coverage_json))) == leaf.coverage_json)
    payload = json.loads(leaf.payload_json)
    require(_json(payload) == leaf.payload_json and payload["version"] == VERSION
        and payload["task"] == TASK and payload["parent_ordinal"] == leaf.parent_ordinal
        and payload["coverage_sha256"] == _hash(leaf.coverage_json)
        and _hash(leaf.payload_json) == leaf.request_sha256)
    return payload


@dataclass(frozen=True, slots=True)
class _ContinuationTransportPermit:
    leaf: ContinuationLeafRequest = field(repr=False)
    model_policy_json: str
    seal: str = field(repr=False)


def _continuation_transport_permit(leaf,model_policy):
    from dataclasses import asdict
    checked_leaf(leaf)
    encoded = _json(asdict(model_policy))
    return _ContinuationTransportPermit(leaf,encoded,_seal([leaf.identity(),encoded]))


def _continuation_transport_budget(permit,payload,options):
    """Adapter-only sealed fresh request allowance, never source authority."""
    require(type(permit) is _ContinuationTransportPermit
        and hmac.compare_digest(permit.seal,_seal([permit.leaf.identity(),permit.model_policy_json])),
        "Unsealed continuation transport allowance")
    expected = checked_leaf(permit.leaf)
    model = json.loads(permit.model_policy_json)
    require(type(payload) is dict and _json(payload) == permit.leaf.payload_json
        and options.get("messages") == expected["messages"]
        and options.get("context_refusal_version") == VERSION
        and options.get("context_request_sha256") == permit.leaf.request_sha256
        and options.get("max_tokens") == 4096 and options.get("max_attempts") == 1
        and options.get("request_budget_bytes") == 4194304
        and options.get("capture_request_receipt") is True
        and options.get("capture_failure_diagnostics") is True
        and options.get("provider_order") == model["provider_order"]
        and options.get("model_ids") == model["model_ids"]
        and [i.source_id for i in options.get("image_inputs",())] == expected["image_ids"]
        and [sha256(i.data).hexdigest() for i in options.get("image_inputs",())] == expected["wire_image_sha256s"],
        "Continuation transport differs from its finite admitted request")
    return 4194304


def _table_wire(plan, coverage):
    cells = _cells(plan)
    by_table = {}
    for owned in coverage["tables"]:
        require(owned["cell_key"] in cells, "Unknown original literal cell")
        table,ri,ci,cell = cells[owned["cell_key"]]
        require(_hash(cell["text"]) == owned["text_sha256"], "Original literal value changed")
        by_table.setdefault(table["unit_id"],(table,{}))[1].setdefault(ri,[]).append(ci)
    wire = []
    for table,rows in by_table.values():
        wire.append(dict(page=table["page"],label=table["label"],
            caption=table.get("caption_context",table["text"]),
            columns=len(table["column_bounds"])-1,
            rows=[[[table["rows"][ri]["cells"][ci]["start_column"],
                    table["rows"][ri]["cells"][ci]["span"],
                    table["rows"][ri]["cells"][ci]["text"]] for ci in sorted(rows[ri])]
                for ri in sorted(rows)], original_row_indices=sorted(rows)))
    return wire


def make_leaf(plan, parent_ordinal, coverage, *, exact_parent=False):
    p = _plan(plan)
    require(type(parent_ordinal) is int and 1 <= parent_ordinal <= len(p.batches))
    coverage = checked_coverage(coverage)
    batch = p.batches[parent_ordinal-1]
    internal,messages,parent_sha,reserved = _request(p,batch)
    if exact_parent:
        require(coverage == parent_inventory(p)[parent_ordinal-1]["coverage"], "Parent coverage changed")
    else:
        sections = []
        for row in coverage["ranges"]:
            chunk = p.chunks[row["chunk_ordinal"]-1]
            require(chunk.chunk_id == row["chunk_id"] and chunk.block_id == row["block_id"]
                and chunk.page == row["page"] and _hash(chunk.text) == row["text_sha256"]
                and 0 <= row["start"] < row["end"] <= len(chunk.text), "Original substring differs")
            sections.append(dict(page=row["page"],text=chunk.text[row["start"]:row["end"]]))
        wire = dict(document_outline=p._outline,source_sections=sections,
            source_tables=_table_wire(p,coverage),image_pages=[v["page"] for v in coverage["visuals"]])
        # Source definitions are supplementary context, not owned text. They
        # are not relabelled as split source; every parent owner is still in a
        # sibling leaf. Exact cells and images stay present at their owners.
        messages = [dict(messages[0]),dict(role="user",content=_json(wire))]
        internal = {**wire,"batch_id":"PCB_"+_hash(_json([parent_sha,coverage])),
            "evidence_catalog_sha256":_hash(_json(coverage))}
    images = leaf_images(p,coverage)
    estimated = sum(section.estimate_text_tokens(m["content"]) for m in messages)
    require(estimated + len(images)*section.image_token_reserve("wiki-pdf-section-writing-v10")
        +4096+1536 <= 16384, "Continuation request exceeds its complete 16k reservation")
    payload = dict(version=VERSION,task=TASK,parent_ordinal=parent_ordinal,
        parent_request_sha256=parent_sha,coverage_sha256=_hash(_json(coverage)),
        prompt_sha256=_hash(messages[0]["content"]),messages=messages,scope=internal,
        image_ids=[i.source_id for i in images],wire_image_sha256s=[sha256(i.data).hexdigest() for i in images],
        estimated_input_tokens=estimated,
        output_tokens=4096,context_tokens=16384,exact_parent=exact_parent)
    encoded = _json(payload)
    # Adapter separately admits the complete inline-image wire. This is the
    # durable input reservation, including the original encoded image bytes.
    size = len(encoded.encode()) + sum(len(i.data) for i in images)
    if size > 4194304:
        raise AuthoringWorkLimitError("Continuation complete request exceeds its 4MiB reservation")
    values = (parent_ordinal,_json(coverage),encoded,_hash(encoded),size)
    return ContinuationLeafRequest(*values,_seal(values))


def _prepare_inventory_and_leaves(plan, selections=None, *, cancel_event=None):
    """Private synchronous derivation with complete entry and exit plan seals.

    Nothing checked is accepted from a caller or retained for the next call.
    A selection is ordinary untrusted (ordinal, coverage, exact_parent) input,
    not a cached inventory or permission to omit validation. Only fresh plain
    inventory and normally sealed leaf requests escape after the exit check.
    Public request/inventory/leaf entry points retain their full old checks.
    """
    from ..document_pdf_process import PdfProcessError

    def cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise PdfProcessError("CANCELLED")

    cancel()
    p = _plan(plan)
    entry_seal = p._layout_validation_seal
    require(selections is None or (type(selections) in (tuple,list)
        and 0 < len(selections) <= ContinuationPolicy("0"*64).max_leaves), "Invalid finite leaf selection")
    cells = _cells(p)
    packets = []
    inventory = []
    owned_cells, seen_images = set(), set()
    # This decoder is deliberately lexical, never a new unchecked public API.
    # _plan's existing HMAC covers every template, nested source and pixel.
    def request(batch):
        require(type(batch) is VisualBatch and type(batch.ordinal) is int
            and type(batch.page) is int and type(batch.visual) is bool
            and type(batch.batch_id) is str and type(batch.chunk_ordinals) is tuple
            and all(type(n) is int for n in batch.chunk_ordinals)
            and type(batch.visual_pages) is tuple and all(type(n) is int for n in batch.visual_pages)
            and 1 <= batch.ordinal <= len(p.batches)
            and batch == p.batches[batch.ordinal-1], "Unknown admitted visual batch")
        row = p._request_templates[batch.ordinal-1]
        require(type(row) is tuple and len(row) == 5 and row[0] == batch.batch_id
            and all(type(value) is str for value in row), "Changed admitted request template")
        return json.loads(row[1]),json.loads(row[2]),row[3],json.loads(row[4])

    for batch in p.batches:
        cancel()
        packets.append(request(batch))
        pages = {p.chunks[n-1].page for n in batch.chunk_ordinals} | set(batch.visual_pages)
        ranges = [{"chunk_id":c.chunk_id,"chunk_ordinal":c.ordinal,"block_id":c.block_id,
            "page":c.page,"start":0,"end":len(c.text),"text_sha256":_hash(c.text)}
            for c in (p.chunks[n-1] for n in batch.chunk_ordinals) if c.text]
        tables = []
        for key,(table,_,_,cell) in cells.items():
            if table["page"] in pages and key not in owned_cells:
                owned_cells.add(key)
                tables.append({"cell_key":key,"text_sha256":_hash(cell["text"])})
        visuals = []
        for number in batch.visual_pages:
            page = p.source.pages[number-1]
            require(page.image.source_id not in seen_images, "Original image has repeated owners")
            seen_images.add(page.image.source_id)
            visuals.append({"page":number,"image_id":page.image.source_id,
                "sha256":sha256(page.image.data).hexdigest()})
        inventory.append({"ordinal":batch.ordinal,"batch_id":batch.batch_id,
            "request_sha256":packets[-1][2],
            "coverage":checked_coverage(dict(ranges=ranges,tables=tables,visuals=visuals))})
    require(owned_cells == set(cells), "Some literal cells have no original writing owner")
    if selections is None:
        selections = tuple((row["ordinal"],row["coverage"],True) for row in inventory)
    leaves = []
    for spec in selections:
        cancel()
        require(type(spec) in (tuple,list) and len(spec) == 3, "Invalid leaf selection shape")
        ordinal,coverage,exact_parent = spec
        require(type(ordinal) is int and 1 <= ordinal <= len(p.batches)
            and type(exact_parent) is bool, "Invalid parent leaf selection")
        coverage = checked_coverage(coverage)
        # JSON copies preserve the old selected request's independently owned
        # mutable packet, never expose a template or share it between leaves.
        original,messages,parent_sha,reserved = packets[ordinal-1]
        internal = json.loads(_json(original))
        messages = json.loads(_json(messages))
        if exact_parent:
            require(coverage == inventory[ordinal-1]["coverage"], "Parent coverage changed")
        else:
            sections = []
            for row in coverage["ranges"]:
                chunk = p.chunks[row["chunk_ordinal"]-1]
                require(chunk.chunk_id == row["chunk_id"] and chunk.block_id == row["block_id"]
                    and chunk.page == row["page"] and _hash(chunk.text) == row["text_sha256"]
                    and 0 <= row["start"] < row["end"] <= len(chunk.text), "Original substring differs")
                sections.append(dict(page=row["page"],text=chunk.text[row["start"]:row["end"]]))
            wire = dict(document_outline=p._outline,source_sections=sections,
                source_tables=_table_wire(p,coverage),image_pages=[v["page"] for v in coverage["visuals"]])
            messages = [dict(messages[0]),dict(role="user",content=_json(wire))]
            internal = {**wire,"batch_id":"PCB_"+_hash(_json([parent_sha,coverage])),
                "evidence_catalog_sha256":_hash(_json(coverage))}
        originals = tuple(p.source.pages[v["page"]-1].image for v in coverage["visuals"])
        for item,image in zip(coverage["visuals"],originals,strict=True):
            require(image.source_id == item["image_id"] and sha256(image.data).hexdigest() == item["sha256"],
                "Original page image differs")
        images = ()
        if originals:
            derived = {image.source_id:image for image in p._wire_image_derivation.images}
            require(all(image.source_id in derived for image in originals))
            images = tuple(derived[image.source_id] for image in originals)
        estimated = sum(section.estimate_text_tokens(m["content"]) for m in messages)
        require(estimated + len(images)*section.image_token_reserve("wiki-pdf-section-writing-v10")
            +4096+1536 <= 16384, "Continuation request exceeds its complete 16k reservation")
        payload = dict(version=VERSION,task=TASK,parent_ordinal=ordinal,
            parent_request_sha256=parent_sha,coverage_sha256=_hash(_json(coverage)),
            prompt_sha256=_hash(messages[0]["content"]),messages=messages,scope=internal,
            image_ids=[i.source_id for i in images],wire_image_sha256s=[sha256(i.data).hexdigest() for i in images],
            estimated_input_tokens=estimated,output_tokens=4096,context_tokens=16384,exact_parent=exact_parent)
        encoded = _json(payload)
        size = len(encoded.encode()) + sum(len(i.data) for i in images)
        if size > 4194304:
            raise AuthoringWorkLimitError("Continuation complete request exceeds its 4MiB reservation")
        values = (ordinal,_json(coverage),encoded,_hash(encoded),size)
        leaves.append(ContinuationLeafRequest(*values,_seal(values)))
    cancel()
    require(_plan(plan) is p and p._layout_validation_seal == entry_seal,
        "The admitted original plan changed during synchronous derivation")
    cancel()
    return tuple(inventory),tuple(leaves)


def subdivide(plan, leaf, policy):
    checked_leaf(leaf)
    policy.encoded()
    value = json.loads(leaf.coverage_json)
    owners = [("ranges",row) for row in value["ranges"]]+[("tables",row) for row in value["tables"]]+[("visuals",row) for row in value["visuals"]]
    if len(owners) > 1:
        midpoint = len(owners)//2
        pieces = []
        for group in (owners[:midpoint],owners[midpoint:]):
            piece = dict(ranges=[],tables=[],visuals=[])
            for key,row in group: piece[key].append(row)
            pieces.append(piece)
    elif len(value["ranges"]) == 1:
        row = value["ranges"][0]
        if row["end"]-row["start"] < 2*policy.min_codepoints:
            raise AuthoringWorkLimitError("Atomic source range cannot be subdivided further without dropping source")
        middle = (row["start"]+row["end"])//2
        pieces = [dict(ranges=[dict(row,start=a,end=b)],tables=[],visuals=[])
            for a,b in ((row["start"],middle),(middle,row["end"]))]
    else:
        raise AuthoringWorkLimitError("Atomic original image or table cell cannot be context-subdivided without changing evidence")
    exact_union(value,pieces)
    return tuple(make_leaf(plan,leaf.parent_ordinal,piece) for piece in pieces)


def decode_response(leaf, response):
    payload = checked_leaf(leaf)
    return _natural_markdown_decode(VERSION_V36,response,payload["scope"])


def project_output(response):
    """Only exact harmless break tags; retain raw text and projection identity.

    This is a fresh-continuation formatting projection, not HTML sanitization.
    Unknown tags, URLs and image markup remain for the unchanged strict decoder.
    Breaks within a pipe row become spaces so no table cell/number is moved to
    another Markdown row. No source or provider response bytes are rewritten.
    """
    require(type(response) is str)
    tag = re.compile(r"<br>|<br/>|<br />")
    count = table_count = 0
    lines = []
    for line in response.splitlines(keepends=True):
        occurrences = len(tag.findall(line))
        count += occurrences
        in_pipe_row = "|" in line
        if in_pipe_row: table_count += occurrences
        lines.append(tag.sub(" " if in_pipe_row else "\n",line))
    projected = "".join(lines)
    return projected,dict(version="pdf-exact-break-projection-v1",
        raw_sha256=_hash(response),projected_sha256=_hash(projected),
        replacement_count=count,pipe_row_space_count=table_count,
        outside_row_newline_count=count-table_count)


def validate_output_receipt(plan,leaf,response,model_json,transport_json,*,stored=True):
    """Revalidate exact raw provider/transport/pixel/decoder binding on replay."""
    from dataclasses import replace
    from .wiki_authoring import _model_receipt,_transport_receipt
    from .pdf_visual_authoring import _api_policy,_codex_policy,_codex_prompt
    from ..llm_images import prepare_image_messages,image_input_receipt,bind_image_receipt
    from ..llm_response import validate_chat_response_receipt
    from ..codex_response import validate_codex_response_receipt
    p = _plan(plan)
    payload = checked_leaf(leaf)
    require(type(response) is str and 0 < len(response.encode()) <= 65536)
    model_values = json.loads(model_json)
    transport = json.loads(transport_json)
    require(_json(model_values) == model_json and _json(transport) == transport_json)
    model = _model_receipt(tuple(model_values.items()),p.model_policy)
    api = model["model_kind"] == "hynix_api"
    images = leaf_images(p,json.loads(leaf.coverage_json))
    expected_keys = {"request_transport","response_transport"} if api else {"codex_response_transport"}
    if images: expected_keys |= {"image_input_transport","image_input_attempt"}
    if stored: expected_keys |= {"markdown_decode","output_projection"}
    require(set(transport) == expected_keys,"Unexpected or incomplete continuation receipt")
    prepared = prepare_image_messages(payload["messages"],images,inline=api) if images else payload["messages"]
    if api:
        request = _transport_receipt(_json(transport["request_transport"]),
            replace(p.model_policy,request_budget_bytes=4194304),messages=prepared,model=model)
        validate_chat_response_receipt(transport["response_transport"],text=response,policy=_api_policy())
        wire_sha,wire_bytes = request["body_sha256"],request["body_bytes"]
    else:
        request = validate_codex_response_receipt(transport["codex_response_transport"],text=response,
            policy=_codex_policy(p),expected_prompt=_codex_prompt(prepared))
        wire_sha,wire_bytes = request["prompt_sha256"],request["prompt_bytes"]
    if images:
        expected = bind_image_receipt(image_input_receipt(images,p.source.render_policy.image_policy(len(images)),
            transport="chat-completions-data-uri-v1" if api else "codex-cli-image-files-v1"),
            request_sha256=wire_sha,provider=model["model_kind"],model_id=model["model_id"],text=response)
        require(_json(transport["image_input_transport"]) == _json(expected),"Continuation pixels not bound")
        attempt = transport["image_input_attempt"]
        keys = {"schema_version","format","phase","status","images","provider_receipt_proven",
            "request_sha256","request_bytes","provider","model_id"}
        if not api: keys |= {"staged","child_started","child_cleanup_completed","owned_temp_removed"}
        require(type(attempt) is dict and set(attempt) == keys and type(attempt["schema_version"]) is int
            and attempt["schema_version"] == 1 and attempt["format"] == "image-input-attempt-v1"
            and attempt["phase"] == attempt["status"] == "completed"
            and type(attempt["request_bytes"]) is int and attempt["request_bytes"] == wire_bytes
            and attempt["request_sha256"] == wire_sha and attempt["provider"] == model["model_kind"]
            and attempt["model_id"] == model["model_id"] and attempt["provider_receipt_proven"] is False
            and _json(attempt["images"]) == _json(expected["images"]),"Continuation image attempt differs")
        if not api:
            require(all(attempt[k] is True for k in ("staged","child_started","child_cleanup_completed","owned_temp_removed")),
                "Continuation image child cleanup is incomplete")
    projected,projection = project_output(response)
    decoded = decode_response(leaf,projected)
    if stored:
        require(_json(transport["output_projection"]) == _json(projection)
            and _json(transport["markdown_decode"]) == _json(decoded),"Continuation formatting replay differs")
    return projected,projection,decoded


def validate_refusal_receipt(plan,leaf,transport):
    """Bind typed refusal to exact captured wire and declared frozen API model.

    Failed internal calls deliberately have no successful model receipt. The
    unique declared policy is only an input fence, never a completion claim.
    """
    from dataclasses import replace
    from .wiki_authoring import _transport_receipt
    from ..llm_images import prepare_image_messages
    from ..llm_context_refusal import validate_context_refusal
    p = _plan(plan)
    payload = checked_leaf(leaf)
    model = p.model_policy
    require(model.provider_order == ("hynix_api",) and model.model_ids is not None
        and len(model.model_ids) == 1 and model.wire_models is not None,
        "Context subdivision requires its exact declared compatible API input")
    validate_context_refusal(transport["context_refusal"],request_sha256=leaf.request_sha256)
    images = leaf_images(p,json.loads(leaf.coverage_json))
    prepared = prepare_image_messages(payload["messages"],images,inline=True) if images else payload["messages"]
    _transport_receipt(_json(transport["request_transport"]),replace(model,request_budget_bytes=4194304),
        messages=prepared,model=dict(model_kind="hynix_api",model_id=model.model_ids[0]))


def assemble(plan, active_results, policy):
    """Exact retained Markdown plus one literal table append; no model review."""
    p = _plan(plan)
    policy.encoded()
    require(type(active_results) is tuple and active_results)
    inventory = parent_inventory(p)
    for parent in inventory:
        owned = [row for row in active_results if row["parent_ordinal"] == parent["ordinal"]]
        require(owned, "Missing final parent coverage")
        if len(owned) == 1:
            require(json.loads(owned[0]["coverage_json"]) == parent["coverage"])
        else:
            exact_union(parent["coverage"],[json.loads(row["coverage_json"]) for row in owned])
    pieces = []
    for row in active_results:
        require(type(row["response"]) is str and _hash(row["response"]) == row["response_sha256"])
        projected,descriptor = project_output(row["response"]) if row["kind"] == "write" else (row["response"],None)
        if row["kind"] == "write": require(descriptor == row["output_projection"])
        pieces.append(projected)
    for page in p._doc.pages:
        for block in page.blocks:
            if block.block_id in p._tables:
                pieces.append("### 원문 표 · p. "+str(page.number)+"\n\n"+p._tables[block.block_id])
    from .pdf_chart_supplement import render_chart_supplement
    chart = _checked_chart_supplement(p)
    chart_text = render_chart_supplement(chart,expected_sha256=chart.identity()["sha256"],
        **_chart_render_options(p,chart))
    if chart_text: pieces.append(chart_text)
    supplement = _literal_supplement(p)
    if supplement.markdown: pieces.append(supplement.markdown)
    markdown = "\n\n".join(pieces)+"\n"
    manifest = _json(dict(version=VERSION,parent_plan_sha256=p.plan_sha256,
        policy_sha256=_hash(policy.encoded()),parent_inventory=inventory,
        outputs=[{k:v for k,v in row.items() if k != "response"} for row in active_results],
        exact_original_owner_union=True,all_literal_table_cells_appended=True,
        semantic_review_performed=False,semantic_completeness_proven=False,
        visual_accuracy_verified=False,content_sha256=_hash(markdown),
        chart_identity=chart.identity(),chart_rendered_sha256=_hash(chart_text),
        literal_table_count=supplement.table_count,literal_cell_count=supplement.cell_count,
        literal_tables_sha256=_hash(supplement.tables_json)))
    if len(markdown.encode()) > policy.max_markdown_bytes or len(manifest.encode()) > policy.max_manifest_bytes:
        raise AuthoringWorkLimitError("Continuation final retained Wiki exceeds its finite budget")
    return markdown,manifest
