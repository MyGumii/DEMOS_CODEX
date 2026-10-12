"""Bounded, escaped exports from validated AI data; never execute model code."""
import base64
import csv
from functools import lru_cache
import hashlib
import html
import io
import math
from pathlib import Path
import re

MODES = {
    "chat": "채팅", "research": "자료 리서치", "slides": "슬라이드 생성",
    "spreadsheet": "스프레드시트", "visualization": "시각화하기",
}


def text(value, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum or "\0" in value:
        raise ValueError("invalid artifact text")
    return value


def normalize(mode, artifact):
    if mode in {"chat", "research"}:
        if artifact is not None:
            raise ValueError("unexpected artifact")
        return None
    if mode == "visualization" and artifact is None:
        return None  # A request for missing numeric data, not a fabricated chart.
    if not isinstance(artifact, dict) or artifact.get("kind") != mode:
        raise ValueError("invalid artifact kind")
    title = text(artifact.get("title"), 160)
    if mode == "slides":
        slides = artifact.get("slides")
        if not isinstance(slides, list) or not 1 <= len(slides) <= 12:
            raise ValueError("expected 1-12 slides")
        clean = []
        for slide in slides:
            if not isinstance(slide, dict) or not isinstance(slide.get("bullets"), list) or not 1 <= len(slide["bullets"]) <= 8:
                raise ValueError("invalid slide")
            clean.append({"title": text(slide.get("title"), 160), "bullets": [text(item, 500) for item in slide["bullets"]]})
        return {"kind": mode, "title": title, "slides": clean}
    if mode == "spreadsheet":
        columns, rows = artifact.get("columns"), artifact.get("rows")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 16 or not isinstance(rows, list) or not 1 <= len(rows) <= 200:
            raise ValueError("invalid table dimensions")
        clean = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("invalid table row")
            cells = []
            for cell in row:
                if cell is None:
                    cells.append("")
                elif isinstance(cell, (int, float)) and not isinstance(cell, bool) and math.isfinite(cell) and abs(cell) <= 1e12:
                    cells.append(cell)
                else:
                    cells.append(text(cell, 2000))
            clean.append(cells)
        return {"kind": mode, "title": title, "columns": [text(c, 160) for c in columns], "rows": clean}
    labels, values = artifact.get("labels"), artifact.get("values")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 16 or not isinstance(values, list) or len(values) != len(labels):
        raise ValueError("invalid chart dimensions")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("invalid chart value")
    return {"kind": mode, "title": title, "labels": [text(label, 80) for label in labels], "values": values, "unit": text(artifact.get("unit", ""), 40)}


def instruction(mode):
    shapes = {
        "chat": "artifact must be null.",
        "research": "Write a complete Korean research report with a clear title, executive summary, scope, findings, comparison between sources, limitations and conclusions. Use only supplied evidence, attribute claims to its source titles, and distinguish analysis from facts. Discuss agreements and differences when multiple sources exist; do not fabricate disagreements. Follow context_info.research.focus when relevant. Include context_info.research.limitations and disclose partial excerpts, failed web reads, limited DB rows or missing evidence. Only supplied web references prove public search results. Never claim to have read full sites, attachments or unprovided rows. artifact must be null.",
        "slides": 'artifact must be {"kind":"slides","title":"deck title","slides":[{"title":"slide title","bullets":["point"]}]}. Produce 1-12 readable slides, at most 8 concise bullets each. No HTML or code.',
        "spreadsheet": 'artifact must be {"kind":"spreadsheet","title":"table title","columns":["column"],"rows":[["value"]]}. Use 1-16 columns, 1-200 rows of the same length. Values are strings, numbers or null. No executable formulas.',
        "visualization": 'When relevant numeric data is supplied, artifact must be {"kind":"visualization","title":"chart title","labels":["category"],"values":[123],"unit":"unit"}. Produce 1-16 categories for a bar chart. Use only user/source-provided numeric data; never invent values. If no suitable numeric data is supplied, artifact must be null and answer must ask for category names, values and unit. No SVG, HTML or code.',
    }
    result = shapes[mode]
    if mode in {"slides", "spreadsheet"}:
        result += (" Write mathematical expressions in text using $...$ or \\( ... \\) delimiters, "
            "including table cells, descriptions, variable definitions and slide bullets. "
            "Wrap standalone mathematical variable names in those delimiters too. Use LaTeX Greek symbols, subscripts and sums, "
            "not unmarked ASCII aliases such as alpha_ij or sum(...). Escape LaTeX backslashes in JSON. "
            "These expressions are explanatory text, never spreadsheet executable formulas.")
    return result


def citation_options(sources):
    """Offer bounded, verbatim choices; never manufacture or paraphrase evidence."""
    options = []
    for source in sources:
        excerpt = source["excerpt"]
        body = excerpt
        if source.get("kind") == "web":
            abstract = re.search(r"\bAbstract\s*:\s*", excerpt, re.I)
            if abstract:
                body = excerpt[abstract.end():]
        phrases = re.split(r"\n+|(?<=[.!?。])\s+", body)
        quotes = []
        for phrase in phrases:
            quote = phrase.strip()[:180].rstrip()
            if source.get("kind") == "web" and (quote.casefold() == source.get("title", "").casefold()
                    or re.match(r"^(?:skip to|view a pdf|download|arxiv:|title:|authors:|submission history|subjects:|comments:|cite as:)", quote, re.I)):
                continue
            if len(quote) >= 24 and quote not in quotes:
                quotes.append(quote)
            if len(quotes) == 3:
                break
        if not quotes and excerpt.strip() and source.get("kind") != "web":
            quotes = [excerpt.strip()[:180].rstrip()]
        options.extend({"source_id": source["source_id"], "quote": quote} for quote in quotes)
    # Offer one quotation per source first, so a model selecting early choices
    # does not spend all four citation slots on a single document.
    order = {source["source_id"]: index for index, source in enumerate(sources)}
    occurrence = {}
    ranked = []
    for item in options:
        sid = item["source_id"]
        position = occurrence.get(sid, 0)
        occurrence[sid] = position + 1
        ranked.append((position, order[sid], item))
    return [item for _, _, item in sorted(ranked, key=lambda row: row[:2])]


def numbered_citation_options(sources):
    """Stable host-owned numbers for directly streamed Markdown chat."""
    return [{"number": index, **option} for index, option in
        enumerate(citation_options(sources)[:32], 1)]


def response_schema(mode, sources):
    """Bounded output grammar; source/quote and table/chart semantics still need validation."""
    def string(maximum):
        return {"type": "string", "maxLength": maximum}
    def array(items, maximum, minimum=1):
        return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    title = string(160)
    shapes = {
        "slides": obj({"kind": {"const": "slides"}, "title": title,
            "slides": array(obj({"title": title, "bullets": array(string(500), 8)}), 12)}),
        "spreadsheet": obj({"kind": {"const": "spreadsheet"}, "title": title,
            "columns": array(title, 16), "rows": array(array({"anyOf": [string(2000),
                {"type": "number", "minimum": -1e12, "maximum": 1e12}, {"type": "null"}]}, 16), 200)}),
        "visualization": obj({"kind": {"const": "visualization"}, "title": title,
            "labels": array(string(80), 16), "values": array({"type": "number", "minimum": -1e12, "maximum": 1e12}, 16),
            "unit": string(40)}),
        "research": {"type": "null"},
    }
    citations = {"type": "array", "maxItems": 0, "items": {"type": "null"}}
    if mode == "research" or any(source.get("kind") == "web" for source in sources):
        options = citation_options(sources)
        choices = [obj({"source_id": {"const": source["source_id"]},
            "quote": {"type": "string", "enum": [c["quote"] for c in options if c["source_id"] == source["source_id"]]}})
            for source in sources if any(c["source_id"] == source["source_id"] for c in options)]
        citations = array({"anyOf": choices} if choices else {"type": "null"}, 4)
    if mode == "visualization":
        shapes[mode] = {"anyOf": [shapes[mode], {"type": "null"}]}
    return obj({"answer": string(16000), "citations": citations, "artifact": shapes[mode]})


def svg(artifact):
    a = normalize("visualization", artifact)
    esc = html.escape
    low, high = min(0, *a["values"]), max(0, *a["values"])
    span = high - low or 1
    x = lambda value: 200 + (value-low) / span * 500
    height = 100 + 46 * len(a["labels"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}" role="img"><title>{esc(a["title"])}</title>',
             '<rect width="100%" height="100%" fill="#fff"/>',
             f'<text x="24" y="32" font-family="sans-serif" font-size="22" fill="#202027">{esc(a["title"])}</text>']
    for i, (label, value) in enumerate(zip(a["labels"], a["values"])):
        y = 65 + i * 46
        parts += [f'<text x="12" y="{y+20}" font-family="sans-serif" font-size="14">{esc(label[:18])}</text>',
                  f'<rect x="{min(x(0), x(value)):.2f}" y="{y}" width="{abs(x(value)-x(0)):.2f}" height="28" rx="3" fill="#6457c7"/>',
                  f'<text x="720" y="{y+20}" font-family="sans-serif" font-size="14">{value:g} {esc(a["unit"])}</text>']
    parts.append(f'<line x1="{x(0):.2f}" x2="{x(0):.2f}" y1="55" y2="{height-20}" stroke="#aaa"/></svg>')
    return "".join(parts)


@lru_cache(maxsize=1)
def _slide_math_assets():
    """Bundle only our fixed, offline renderer; CSP permits its exact hashes.

    Model text remains escaped HTML text and never enters a script. No CDN,
    host URL, Node runtime or network connection is needed to open the export.
    """
    static = Path(__file__).with_name("notion_static")
    vendor = static / "vendor" / "katex-0.18.7"
    css = (vendor / "katex.min.css").read_text(encoding="utf-8")

    def font_src(match):
        font = re.search(r"url\((fonts/KaTeX_[\w-]+\.woff2)\)", match[1])
        if not font:
            raise ValueError("bundled math font missing")
        data = base64.b64encode((vendor / font[1]).read_bytes()).decode("ascii")
        return f'src:url(data:font/woff2;base64,{data}) format("woff2")'

    css = re.sub(r"src:([^;}]+)", font_src, css)
    scripts = [
        (vendor / "katex.min.js").read_text(encoding="utf-8"),
        (static / "notion_ai_answer.js").read_text(encoding="utf-8"),
        'document.querySelectorAll("h1,li").forEach(function(el){'
        'NotionAiAnswer.renderText(el,el.textContent);});',
    ]
    if any("</script" in source.lower() for source in scripts) or "</style" in css.lower():
        raise ValueError("unsafe bundled math asset")
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(source.encode()).digest()).decode() + "'" for source in scripts)
    return css, "".join(f"<script>{source}</script>" for source in scripts), hashes


def export(message, kind):
    from code_assist_v1.notion_ai_report_format import present_report
    message=present_report(message)
    if kind == "md":
        answer = message["text"]
        citations = message.get("citations", [])
        if citations:
            if message.get('context_info', {}).get('research', {}).get('depth') == 'deep':
                entries = []
                for index, citation in enumerate(citations, 1):
                    title = citation['title'].replace('\\','\\\\').replace('[',r'\[').replace(']',r'\]').replace('\n',' ')
                    url = citation.get('url')
                    reference = f'[{title}]({url.replace("(","%28").replace(")","%29")})' if url else title
                    location = f" — {citation['location']}" if citation.get('location') else ''
                    quotation = '\n  > '.join(citation['quote'].splitlines())
                    entries.append(f'- [{index}] {reference}{location}\n\n  > {quotation}')
                answer += '\n\n## 참고 자료\n\n' + '\n\n'.join(entries)
            else:
                answer += "\n\n## 참고 자료\n" + "\n".join(f'- [{c["source_id"]}] {c["title"]}: {c["quote"]}' for c in citations)
        return answer.encode("utf-8-sig"), "text/markdown; charset=utf-8", "ai-answer.md"
    artifact = message.get("artifact")
    if not artifact:
        raise ValueError("내보낼 생성 결과가 없습니다.")
    artifact = normalize(artifact["kind"], artifact)
    if kind == "svg" and artifact["kind"] == "visualization":
        return svg(artifact).encode(), "image/svg+xml", "ai-chart.svg"
    if kind == "csv" and artifact["kind"] == "spreadsheet":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        def safe(cell):
            if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@")):
                return "'" + cell
            return cell
        writer.writerow([safe(c) for c in artifact["columns"]])
        writer.writerows([[safe(c) for c in row] for row in artifact["rows"]])
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "ai-table.csv"
    if kind == "html" and artifact["kind"] == "slides":
        esc = html.escape
        sections = "".join(f'<section id="slide-{i+1}"><small>{i+1} / {len(artifact["slides"])}</small><h1>{esc(slide["title"])}</h1><ul>' + "".join(f'<li>{esc(b)}</li>' for b in slide["bullets"]) + '</ul></section>' for i, slide in enumerate(artifact["slides"]))
        values = [artifact["title"], *(value for slide in artifact["slides"] for value in [slide["title"], *slide["bullets"]])]
        css, scripts = "", ""
        policy = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        if any("$" in value or "\\(" in value or "\\[" in value or re.search(
            r"(?<![\w/\\.$])(?:alpha|beta|gamma|delta|sigma|theta|lambda|mu|pi|[A-Za-z])'?_(?:\{[A-Za-z0-9]+\}|[A-Za-z0-9]+)\s*=", value)
            for value in values):
            css, scripts, hashes = _slide_math_assets()
            policy += "; font-src data:; script-src " + hashes
        body = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + f'<meta http-equiv="Content-Security-Policy" content="{esc(policy, quote=True)}">'
        body += f'<title>{esc(artifact["title"])}</title><style>body{{margin:0;background:#eeedf5;color:#242331;font-family:Arial,sans-serif}}section{{box-sizing:border-box;max-width:1100px;min-height:600px;margin:32px auto;padding:64px;background:white;border-top:8px solid #6457c7;break-after:page}}h1{{font-size:38px;line-height:1.3;overflow-wrap:anywhere}}li{{font-size:24px;line-height:1.5;margin:16px 0;overflow-wrap:anywhere}}small{{color:#777}}@media print{{body{{background:white}}section{{margin:0;min-height:0;padding:32px}}}}@media(max-width:700px){{section{{padding:24px;min-height:0}}h1{{font-size:28px}}li{{font-size:18px}}}}</style>{sections}</html>'
        # Keep scripts outside model-authored sections and permit no other code.
        body = body.removesuffix("</html>") + f'<style>{css}.ai-home-math-block{{overflow:auto}}</style>{scripts}</html>'
        return body.encode(), "text/html; charset=utf-8", "ai-slides.html"
    raise ValueError("지원하지 않는 내보내기 형식입니다.")
