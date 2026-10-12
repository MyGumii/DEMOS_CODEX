"""
File-backed LLM Wiki store for code_assist_v1.

Second Brain v2 uses markdown wiki files as the canonical knowledge source.
The old raw chunk / atomic thought / candidate pipeline is intentionally not
used here. Compatibility function names are kept so existing routes and UI can
call into the new Wiki, diff, and graph stores without a large route rewrite.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from code_assist_v1.config import KNOWLEDGE_DIR, MAX_KNOWLEDGE_INJECT_CHARS
from code_assist_v1.document_extractors import extract_document


EDGE_TYPES = {
    "related",
    "references",
    "extends",
    "contradicts",
    "supersedes",
    "source-of",
    "supports",
    "requires",
}
TEXTISH_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".adoc",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".csv", ".tsv", ".log", ".sql",
}
_USER_CACHE: dict[str, dict[str, Any]] = {}
_FS_LOCK = threading.RLock()
GRAPH_AUTO_CONFIDENCE_MIN = float(os.environ.get("SECOND_BRAIN_GRAPH_AUTO_CONFIDENCE_MIN", "0.75") or "0.75")
GRAPH_REVIEW_CONFIDENCE_MIN = float(os.environ.get("SECOND_BRAIN_GRAPH_REVIEW_CONFIDENCE_MIN", "0.25") or "0.25")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _safe_part(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\x00-\x1f\x7f<>:\"|?*\\/]+", "-", value)
    value = value.strip(". -_")
    return value[:120] or "untitled"


def _slug(value: str, fallback_prefix: str = "wiki") -> str:
    source = _text(value).strip()
    slug = source.lower()
    slug = re.sub(r"[^a-z0-9가-힣_-]+", "-", slug)
    slug = slug.strip("-_")
    if not slug:
        slug = f"{fallback_prefix}-{hashlib.sha1(source.encode('utf-8', errors='ignore')).hexdigest()[:10]}"
    return slug[:100]


def make_id(value: str, fallback_prefix: str = "wiki") -> str:
    return _slug(value, fallback_prefix=fallback_prefix)


def _ensure_md_name(filename: str) -> str:
    name = _safe_part(os.path.basename(filename or "source.md"))
    stem, ext = os.path.splitext(name)
    if not ext:
        return f"{name}.md"
    if ext.lower() in {".markdown", ".txt", ".text"}:
        return f"{stem}.md"
    if ext.lower() != ".md":
        return f"{stem}.md"
    return name


def _unique_name(directory: str, filename: str) -> str:
    filename = _ensure_md_name(filename)
    stem, ext = os.path.splitext(filename)
    candidate = filename
    idx = 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{stem}-{idx}{ext}"
        idx += 1
    return candidate


def _uid(user_id: str | None) -> str:
    return _safe_part(user_id or "default")


def _base_dir(user_id: str | None = None) -> str:
    return os.path.join(KNOWLEDGE_DIR, _uid(user_id), "wiki")


def _paths(user_id: str | None = None) -> dict[str, str]:
    base = _base_dir(user_id)
    return {
        "base": base,
        "pages": os.path.join(base, "pages"),
        "sources": os.path.join(base, "sources"),
        "assets": os.path.join(base, "assets"),
        "graph": os.path.join(base, "graph"),
        "health": os.path.join(base, "health"),
        "index": os.path.join(base, "index.md"),
        "log": os.path.join(base, "log.md"),
        "graph_json": os.path.join(base, "graph", "graph.json"),
        "backlinks_json": os.path.join(base, "graph", "backlinks.json"),
        "pending_diffs_json": os.path.join(base, "graph", "pending_diffs.json"),
        "sqlite": os.path.join(base, "wiki_index.sqlite"),
    }


def ensure_dirs(user_id: str | None = None) -> dict[str, str]:
    paths = _paths(user_id)
    with _FS_LOCK:
        for key in ("base", "pages", "sources", "assets", "graph", "health"):
            os.makedirs(paths[key], exist_ok=True)
        if not os.path.isfile(paths["index"]):
            _write_text(paths["index"], "# LLM Wiki Index\n\nNo wiki entries yet.\n")
        if not os.path.isfile(paths["log"]):
            _write_text(paths["log"], "# LLM Wiki Log\n\n")
        if not os.path.isfile(paths["graph_json"]):
            _write_json(paths["graph_json"], {"nodes": [], "edges": [], "updated_at": int(time.time())})
        if not os.path.isfile(paths["backlinks_json"]):
            _write_json(paths["backlinks_json"], {})
        if not os.path.isfile(paths["pending_diffs_json"]):
            _write_json(paths["pending_diffs_json"], [])
        _ensure_sqlite(paths["sqlite"])
    return paths


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()


def _write_text(path: str, content: str) -> None:
    with _FS_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = _tmp_path(path)
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            _replace_with_retry(tmp, path)
        finally:
            _remove_tmp_if_left(tmp)


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    with _FS_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = _tmp_path(path)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            _replace_with_retry(tmp, path)
        finally:
            _remove_tmp_if_left(tmp)


def _tmp_path(path: str) -> str:
    return f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"


def _replace_with_retry(tmp: str, path: str) -> None:
    last_error: PermissionError | None = None
    for _ in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    if last_error:
        raise last_error


def _remove_tmp_if_left(tmp: str) -> None:
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass


def _ensure_sqlite(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS wiki_docs (
              id TEXT PRIMARY KEY,
              filename TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              kind TEXT NOT NULL,
              title TEXT,
              summary TEXT,
              tags TEXT,
              updated_at INTEGER,
              content TEXT
            )
            """
        )
        try:
            con.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts
                USING fts5(id UNINDEXED, filename, title, summary, tags, content)
                """
            )
        except sqlite3.OperationalError:
            pass
        con.commit()


def _doc_id(kind: str, filename: str) -> str:
    # Keep graph labels close to the actual markdown filename. If a page and a
    # source share a filename, prefix only the source to avoid id collision.
    name = _ensure_md_name(filename)
    return name if kind == "page" else f"source/{name}"


def _kind_dir(paths: dict[str, str], kind: str) -> str:
    return paths["pages"] if kind == "page" else paths["sources"]


def _doc_path(paths: dict[str, str], kind: str, filename: str) -> str:
    return os.path.join(_kind_dir(paths, kind), _ensure_md_name(filename))


def _relative(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace("\\", "/")


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()[:160]
    return fallback


def _summary(markdown: str, max_chars: int = 420) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"^---\n.*?\n---\n", " ", text, flags=re.S)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _terms(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[가-힣]{2,}|[0-9]{2,}", text.lower())
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "about",
        "source", "wiki", "page", "markdown", "document",
    }
    return [tok for tok in tokens if tok not in stop]


def _tags(markdown: str, filename: str = "") -> list[str]:
    tags: list[str] = []
    for term in _terms(filename + "\n" + markdown[:3000]):
        if term not in tags:
            tags.append(term)
        if len(tags) >= 12:
            break
    return tags


def _doc_record(kind: str, filename: str, path: str, content: str, base: str) -> dict[str, Any]:
    title = _extract_title(content, filename)
    tags = _tags(content, filename)
    return {
        "id": _doc_id(kind, filename),
        "filename": _ensure_md_name(filename),
        "kind": kind,
        "title": title,
        "summary": _summary(content),
        "tags": tags,
        "updated_at": int(os.path.getmtime(path)) if os.path.exists(path) else int(time.time()),
        "path": path,
        "rel_path": _relative(path, base),
        "content_preview": content.strip()[:700],
        "_content": content,
    }


def _all_docs(user_id: str | None = None, include_content: bool = True) -> list[dict[str, Any]]:
    paths = ensure_dirs(user_id)
    cache_key = _uid(user_id)
    mtimes = []
    for directory in (paths["sources"],):
        for name in sorted(os.listdir(directory)):
            if name.lower().endswith(".md"):
                p = os.path.join(directory, name)
                mtimes.append((p, os.path.getmtime(p)))
    cache = _USER_CACHE.get(cache_key)
    if cache and cache.get("mtimes") == mtimes:
        docs = [dict(x) for x in cache.get("docs", [])]
    else:
        docs = []
        for kind, directory in (("source", paths["sources"]),):
            for name in sorted(os.listdir(directory)):
                if not name.lower().endswith(".md"):
                    continue
                path = os.path.join(directory, name)
                try:
                    content = _read_text(path)
                except Exception:
                    continue
                docs.append(_doc_record(kind, name, path, content, paths["base"]))
        _USER_CACHE[cache_key] = {"mtimes": mtimes, "docs": [dict(x) for x in docs]}
    if not include_content:
        for doc in docs:
            doc.pop("_content", None)
    return docs


def _invalidate_cache(user_id: str | None = None) -> None:
    _USER_CACHE.pop(_uid(user_id), None)


def _upsert_index_doc(doc: dict[str, Any], user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    _ensure_sqlite(paths["sqlite"])
    content = _text(doc.get("_content", ""))
    with sqlite3.connect(paths["sqlite"]) as con:
        con.execute(
            """
            INSERT INTO wiki_docs(id, filename, rel_path, kind, title, summary, tags, updated_at, content)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              filename=excluded.filename,
              rel_path=excluded.rel_path,
              kind=excluded.kind,
              title=excluded.title,
              summary=excluded.summary,
              tags=excluded.tags,
              updated_at=excluded.updated_at,
              content=excluded.content
            """,
            (
                doc["id"],
                doc["filename"],
                doc["rel_path"],
                doc["kind"],
                doc.get("title", ""),
                doc.get("summary", ""),
                ", ".join(doc.get("tags") or []),
                int(doc.get("updated_at") or time.time()),
                content,
            ),
        )
        try:
            con.execute("DELETE FROM wiki_fts WHERE id=?", (doc["id"],))
            con.execute(
                "INSERT INTO wiki_fts(id, filename, title, summary, tags, content) VALUES(?,?,?,?,?,?)",
                (
                    doc["id"],
                    doc["filename"],
                    doc.get("title", ""),
                    doc.get("summary", ""),
                    ", ".join(doc.get("tags") or []),
                    content,
                ),
            )
        except sqlite3.OperationalError:
            pass
        con.commit()


def _delete_index_doc(doc_id: str, user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    with sqlite3.connect(paths["sqlite"]) as con:
        con.execute("DELETE FROM wiki_docs WHERE id=?", (doc_id,))
        try:
            con.execute("DELETE FROM wiki_fts WHERE id=?", (doc_id,))
        except sqlite3.OperationalError:
            pass
        con.commit()


def rebuild_index(user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    with sqlite3.connect(paths["sqlite"]) as con:
        con.execute("DELETE FROM wiki_docs")
        try:
            con.execute("DELETE FROM wiki_fts")
        except sqlite3.OperationalError:
            pass
        con.commit()
    for doc in _all_docs(user_id, include_content=True):
        _upsert_index_doc(doc, user_id)


def _score_doc(query: str, doc: dict[str, Any]) -> float:
    terms = _terms(query)
    if not terms:
        return 0.0
    title = _text(doc.get("title", "")).lower()
    filename = _text(doc.get("filename", "")).lower()
    tags = " ".join(doc.get("tags") or []).lower()
    content = _text(doc.get("_content", doc.get("content_preview", ""))).lower()
    score = 0.0
    for term in terms:
        if term in filename:
            score += 8
        if term in title:
            score += 6
        if term in tags:
            score += 3
        score += min(content.count(term), 10) * 0.5
    return score


def search_wiki(query: str, user_id: str | None = None, max_results: int = 8) -> list[dict[str, Any]]:
    query = (query or "").strip()
    docs = _all_docs(user_id, include_content=True)
    if not docs:
        return []
    if not query:
        return [{k: v for k, v in doc.items() if not k.startswith("_")} for doc in docs[:max_results]]

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in docs:
        score = _score_doc(query, doc)
        if score > 0:
            clone = {k: v for k, v in doc.items() if not k.startswith("_")}
            clone["score"] = round(score, 3)
            scored.append((score, clone))
    scored.sort(key=lambda x: (x[0], x[1].get("updated_at", 0)), reverse=True)
    return [item for _, item in scored[:max_results]]


def _graph(user_id: str | None = None) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    data = _read_json(paths["graph_json"], {"nodes": [], "edges": []})
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def _write_graph(data: dict[str, Any], user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    data["edges"] = [
        edge for edge in data.get("edges") or []
        if not _is_legacy_keyword_overlap_edge(edge)
    ]
    data["updated_at"] = int(time.time())
    _write_json(paths["graph_json"], data)
    backlinks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in data.get("edges") or []:
        target = edge.get("target")
        if target:
            backlinks[target].append(edge)
    _write_json(paths["backlinks_json"], dict(backlinks))


def _is_legacy_keyword_overlap_edge(edge: dict[str, Any]) -> bool:
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    reason = _text(metadata.get("reason") or edge.get("reason") or "").strip().lower()
    return reason == "keyword overlap during wiki import"


def _edge_id(source: str, target: str, edge_type: str) -> str:
    return "edge-" + hashlib.sha1(f"{source}|{target}|{edge_type}".encode("utf-8")).hexdigest()[:16]


def _ensure_node(doc: dict[str, Any], user_id: str | None = None) -> None:
    data = _graph(user_id)
    nodes = [n for n in data.get("nodes") or [] if n.get("id") != doc["id"]]
    nodes.append({
        "id": doc["id"],
        "label": doc["filename"],
        "title": doc.get("title") or doc["filename"],
        "filename": doc["filename"],
        "kind": doc.get("kind", "page"),
        "rel_path": doc.get("rel_path", ""),
        "summary": doc.get("summary", ""),
        "tags": doc.get("tags") or [],
        "updated_at": int(doc.get("updated_at") or time.time()),
        "source_type": doc.get("source_type") or "",
        "source_metadata": doc.get("source_metadata") or {},
        "asset_count": len(doc.get("assets") or []),
    })
    data["nodes"] = nodes
    _write_graph(data, user_id)


def add_edge(
    source: str,
    target: str,
    edge_type: str = "related",
    user_id: str | None = None,
    weight: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _text(source).strip()
    target = _text(target).strip()
    if not source or not target:
        raise ValueError("source and target required")
    edge_type = edge_type if edge_type in EDGE_TYPES else "related"
    edge = {
        "id": _edge_id(source, target, edge_type),
        "source": source,
        "target": target,
        "edge_type": edge_type,
        "weight": float(weight or 1.0),
        "created_at": int(time.time()),
        "metadata": metadata or {},
    }
    data = _graph(user_id)
    edges = [e for e in data.get("edges") or [] if e.get("id") != edge["id"]]
    edges.append(edge)
    data["edges"] = edges
    _write_graph(data, user_id)
    return edge


def add_edge_once(
    source: str,
    target: str,
    edge_type: str = "related",
    user_id: str | None = None,
    weight: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge_id = _edge_id(source, target, edge_type if edge_type in EDGE_TYPES else "related")
    for edge in _graph(user_id).get("edges") or []:
        if edge.get("id") == edge_id:
            return edge
    return add_edge(source, target, edge_type=edge_type, user_id=user_id, weight=weight, metadata=metadata)


def _remove_edges_for(doc_id: str, user_id: str | None = None) -> int:
    data = _graph(user_id)
    edges = data.get("edges") or []
    kept = [e for e in edges if e.get("source") != doc_id and e.get("target") != doc_id]
    data["edges"] = kept
    _write_graph(data, user_id)
    return len(edges) - len(kept)


def _neighbors(doc_ids: set[str], user_id: str | None = None, depth: int = 1) -> set[str]:
    edges = _graph(user_id).get("edges") or []
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        s = edge.get("source")
        t = edge.get("target")
        if s and t:
            graph[s].add(t)
            graph[t].add(s)
    keep = set(doc_ids)
    q = deque((doc_id, 0) for doc_id in doc_ids)
    while q:
        cur, dist = q.popleft()
        if dist >= depth:
            continue
        for nxt in graph.get(cur, set()):
            if nxt not in keep:
                keep.add(nxt)
                q.append((nxt, dist + 1))
    return keep


def _append_log(user_id: str | None, lines: list[str]) -> None:
    paths = ensure_dirs(user_id)
    current = _read_text(paths["log"])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = [f"\n## {stamp}", *[f"- {line}" for line in lines], ""]
    _write_text(paths["log"], current.rstrip() + "\n" + "\n".join(entry))


def _index_content(user_id: str | None = None) -> str:
    docs = _all_docs(user_id, include_content=False)
    sources = [d for d in docs if d["kind"] == "source"]
    graph = _graph(user_id)
    edge_count = len(graph.get("edges") or [])
    maintenance_count = len([
        item for item in _load_pending_diffs(user_id)
        if _visible_candidate(item) and item.get("status", "pending") == "pending"
    ])
    lines = [
        "# LLM Wiki Index",
        "",
        "This index is the navigation map for Second Brain retrieval. Canonical knowledge lives in sources/. The agent reads this index first, then expands into related markdown files using graph.json and full-text search.",
        "",
        f"- Sources: {len(sources)}",
        f"- Graph edges: {edge_count}",
        f"- Maintenance items: {maintenance_count}",
        "",
        "## Sources",
    ]
    if sources:
        for doc in sorted(sources, key=lambda d: d["filename"].lower()):
            tags = ", ".join(doc.get("tags") or [])
            lines.append(f"- [[{doc['filename']}]] - {doc.get('summary', '')[:160]}{' | ' + tags if tags else ''}")
    else:
        lines.append("- No imported sources yet.")
    lines.extend(["", "## Graph Relations"])
    for edge in sorted(graph.get("edges") or [], key=lambda e: (e.get("source", ""), e.get("target", "")))[:200]:
        lines.append(
            f"- `{edge.get('source')}` --{edge.get('edge_type', 'related')}--> `{edge.get('target')}`"
        )
    return "\n".join(lines).rstrip() + "\n"


def refresh_index_and_cache(user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    _invalidate_cache(user_id)
    docs = _all_docs(user_id, include_content=True)
    graph = _graph(user_id)
    valid_ids = {doc["id"] for doc in docs}
    existing_nodes = [node for node in graph.get("nodes") or [] if node.get("id") in valid_ids]
    existing_edges = [
        edge for edge in graph.get("edges") or []
        if edge.get("source") in valid_ids and edge.get("target") in valid_ids
        and not _is_legacy_keyword_overlap_edge(edge)
    ]
    if len(existing_nodes) != len(graph.get("nodes") or []) or len(existing_edges) != len(graph.get("edges") or []):
        graph["nodes"] = existing_nodes
        graph["edges"] = existing_edges
        _write_graph(graph, user_id)
        graph = _graph(user_id)
    node_ids = {node.get("id") for node in graph.get("nodes") or []}
    changed = False
    nodes = list(graph.get("nodes") or [])
    for doc in docs:
        _upsert_index_doc(doc, user_id)
        if doc["id"] not in node_ids:
            nodes.append({
                "id": doc["id"],
                "label": doc["filename"],
                "title": doc.get("title") or doc["filename"],
                "filename": doc["filename"],
                "kind": doc.get("kind", "page"),
                "rel_path": doc.get("rel_path", ""),
                "summary": doc.get("summary", ""),
                "tags": doc.get("tags") or [],
                "updated_at": int(doc.get("updated_at") or time.time()),
            })
            changed = True
    if changed:
        graph["nodes"] = nodes
        _write_graph(graph, user_id)
    _write_text(paths["index"], _index_content(user_id))


def _decode_text(filename: str, raw: bytes) -> tuple[str, dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    if ext in TEXTISH_EXTENSIONS:
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                text = raw.decode(enc)
                return text, {"extractor": f"text:{enc}", "warnings": [], "images": [], "metadata": {}}
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), {
            "extractor": "text:replace",
            "warnings": ["encoding replaced"],
            "images": [],
            "metadata": {},
        }
    result = extract_document(filename, raw)
    return result.markdown, {
        "extractor": result.extractor,
        "warnings": result.warnings,
        "images": result.images,
        "metadata": result.metadata,
    }


def _llm_json(task: str, payload: dict[str, Any], max_tokens: int = 700) -> tuple[dict[str, Any] | None, str]:
    try:
        from code_assist_v1 import second_brain_curation as curator

        call = getattr(curator, "_call_local_gguf_json", None)
        if not call:
            return None, "local wiki LLM unavailable"
        return call(task, payload, max_tokens=max_tokens)
    except Exception as e:
        return None, str(e)


def _to_wiki_markdown(
    filename: str,
    text: str,
    *,
    force_llm: bool = False,
    source_type: str = "upload",
) -> tuple[str, dict[str, Any]]:
    ext = Path(filename).suffix.lower()
    if ext in {".md", ".markdown"} and not force_llm:
        return text.strip() + "\n", {"converted": False, "error": "", "skipped": True}
    title = os.path.splitext(os.path.basename(filename))[0]
    parsed, error = _llm_json(
        "convert_text_to_llm_wiki_markdown",
        {
            "filename": filename,
            "source_type": source_type,
            "content": text[:9000],
            "rules": [
                "Create a concise LLM Wiki markdown page.",
                "Use a clear H1 title.",
                "Preserve factual content; do not invent.",
                "Use headings, bullets, and short paragraphs when useful.",
                "Do not include process commentary.",
                "Preserve source asset references if they are already present in the text.",
            ],
            "output_contract": {"markdown": "full markdown page"},
        },
        max_tokens=900,
    )
    content = _text((parsed or {}).get("markdown") or (parsed or {}).get("content")).strip()
    if content:
        if not content.lstrip().startswith("#"):
            content = f"# {title}\n\n{content}"
        return content.rstrip() + "\n", {
            "converted": True,
            "error": "",
            "curator_model": (parsed or {}).get("_curator_model", ""),
            "model_kind": (parsed or {}).get("_internal_model_kind") or (parsed or {}).get("_curator_provider", ""),
            "model_used": (parsed or {}).get("_internal_model_used") or (parsed or {}).get("_curator_model", ""),
            "model_id": (parsed or {}).get("_internal_model_id", ""),
            "fallback_errors": (parsed or {}).get("_internal_fallback_errors") or (parsed or {}).get("_curator_fallback_errors") or [],
        }
    if force_llm and source_type == "notion" and text.strip().startswith("#"):
        fallback = text.strip() + "\n"
    else:
        fallback = f"# {title}\n\n{text.strip()}\n"
    return fallback, {
        "converted": False,
        "error": error or "wiki markdown conversion unavailable",
        "fallback": "simple_markdown",
    }


def _related_docs(new_doc: dict[str, Any], user_id: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    docs = [d for d in _all_docs(user_id, include_content=True) if d["id"] != new_doc["id"]]
    scored = []
    new_text = "\n".join([new_doc.get("filename", ""), new_doc.get("title", ""), " ".join(new_doc.get("tags") or []), new_doc.get("_content", "")[:3000]])
    for doc in docs:
        score = _score_doc(new_text, doc)
        if score > 0:
            clone = {k: v for k, v in doc.items() if not k.startswith("_")}
            clone["score"] = round(score, 3)
            scored.append((score, clone))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:limit]]


def _analysis_for_import(new_doc: dict[str, Any], related: list[dict[str, Any]], user_id: str | None = None) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    index_tail = _read_text(paths["index"])[:6000]
    log_tail = _read_text(paths["log"])[-4000:]
    parsed, error = _llm_json(
        "analyze_llm_wiki_import",
        {
            "new_doc": {
                "id": new_doc["id"],
                "filename": new_doc["filename"],
                "kind": new_doc["kind"],
                "title": new_doc.get("title", ""),
                "summary": new_doc.get("summary", ""),
                "tags": new_doc.get("tags", []),
                "source_type": new_doc.get("source_type", ""),
                "source_metadata": new_doc.get("source_metadata", {}),
                "content": new_doc.get("_content", "")[:6500],
            },
            "existing_index": index_tail,
            "recent_log": log_tail,
            "related_docs": related[:6],
            "output_contract": {
                "edges": [{"target": "filename.md", "edge_type": "related", "reason": "short", "confidence": 0.0}],
                "diffs": [],
            },
        },
        max_tokens=900,
    )
    if not parsed:
        return {"edges": [], "diffs": [], "error": error}
    return parsed


def _load_pending_diffs(user_id: str | None = None) -> list[dict[str, Any]]:
    paths = ensure_dirs(user_id)
    data = _read_json(paths["pending_diffs_json"], [])
    return data if isinstance(data, list) else []


def _write_pending_diffs(items: list[dict[str, Any]], user_id: str | None = None) -> None:
    paths = ensure_dirs(user_id)
    _write_json(paths["pending_diffs_json"], items)


def _diff_id(source_id: str, target_id: str, action: str) -> str:
    return "diff-" + hashlib.sha1(f"{source_id}|{target_id}|{action}".encode("utf-8")).hexdigest()[:16]


def _graph_candidate_id(source_id: str, target_id: str, edge_type: str) -> str:
    return "graph-" + hashlib.sha1(f"{source_id}|{target_id}|{edge_type}".encode("utf-8")).hexdigest()[:16]


def _visible_candidate(item: dict[str, Any]) -> bool:
    # The review inbox is reserved for source quality/lint maintenance. Graph
    # relation candidates are intentionally not user-facing; strong edges are
    # applied automatically and weak edges are ignored.
    return item.get("candidate_kind") in {"source_lint", "source_issue", "maintenance"}


def _is_weak_graph_reason(reason: str) -> bool:
    reason_l = _text(reason).lower()
    weak_markers = (
        "weak",
        "loose",
        "indirect",
        "not directly",
        "conceptual overlap",
        "keyword overlap",
        "tag overlap",
        "term overlap",
        "common keyword",
        "overlap during wiki import",
        "distant",
        "tenuous",
        "약한",
        "간접",
        "느슨",
    )
    return any(marker in reason_l for marker in weak_markers)


def _edge_confidence(edge: dict[str, Any], default: float = 0.6) -> float:
    try:
        value = float(edge.get("confidence", edge.get("weight", default)))
    except Exception:
        value = default
    return max(0.0, min(1.0, value))


def _unified_diff(old: str, new: str, old_name: str, new_name: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=old_name,
        tofile=new_name,
    ))


def _save_pending_diff(
    *,
    user_id: str | None,
    source_doc: dict[str, Any],
    target_doc: dict[str, Any] | None,
    action: str,
    reason: str,
    proposed_content: str,
) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    target_id = target_doc["id"] if target_doc else _doc_id("page", source_doc["filename"])
    filename = target_doc["filename"] if target_doc else source_doc["filename"]
    target_path = target_doc["rel_path"] if target_doc else f"pages/{filename}"
    old_content = target_doc.get("_content", "") if target_doc else ""
    if not proposed_content.strip():
        proposed_content = source_doc.get("_content", "")
    diff_text = _unified_diff(old_content, proposed_content, target_path, target_path)
    now = int(time.time())
    item = {
        "id": _diff_id(source_doc["id"], target_id, action),
        "status": "pending",
        "candidate_kind": "wiki_diff",
        "title": f"{action}: {filename}",
        "memory_type": "document",
        "domain": "",
        "service": "",
        "topics": source_doc.get("tags") or [],
        "content": diff_text or proposed_content[:3000],
        "source": {
            "source_id": source_doc["id"],
            "filename": source_doc["filename"],
            "raw_path": source_doc["rel_path"],
            "source_type": source_doc.get("source_type", ""),
            "source_metadata": source_doc.get("source_metadata", {}),
        },
        "target_id": target_id,
        "target_filename": filename,
        "target_path": target_path,
        "action": action,
        "reason": reason,
        "proposed_content": proposed_content,
        "diff_text": diff_text,
        "created_at": now,
        "updated_at": now,
    }
    items = [x for x in _load_pending_diffs(user_id) if x.get("id") != item["id"]]
    items.append(item)
    _write_pending_diffs(items, user_id)
    return item


def _save_graph_candidate(
    *,
    user_id: str | None,
    source_doc: dict[str, Any],
    target_doc: dict[str, Any],
    edge_type: str,
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    now = int(time.time())
    edge_type = edge_type if edge_type in EDGE_TYPES else "related"
    item = {
        "id": _graph_candidate_id(source_doc["id"], target_doc["id"], edge_type),
        "status": "pending",
        "candidate_kind": "graph_candidate",
        "title": f"graph: {source_doc['filename']} -> {target_doc['filename']}",
        "memory_type": "document",
        "domain": "",
        "service": "",
        "topics": sorted(set((source_doc.get("tags") or [])[:8] + (target_doc.get("tags") or [])[:8])),
        "content": reason or "Related source candidate",
        "source": {
            "source_id": source_doc["id"],
            "filename": source_doc["filename"],
            "raw_path": source_doc["rel_path"],
            "source_type": source_doc.get("source_type", ""),
            "source_metadata": source_doc.get("source_metadata", {}),
        },
        "target_id": target_doc["id"],
        "target_filename": target_doc["filename"],
        "target_path": target_doc["rel_path"],
        "action": "add_edge",
        "reason": reason,
        "confidence": confidence,
        "edge": {
            "source": source_doc["id"],
            "target": target_doc["id"],
            "edge_type": edge_type,
            "weight": confidence,
            "metadata": {"reason": reason, "source_import": source_doc["id"], "reviewed": True},
        },
        "created_at": now,
        "updated_at": now,
    }
    items = [x for x in _load_pending_diffs(user_id) if x.get("id") != item["id"]]
    items.append(item)
    _write_pending_diffs(items, user_id)
    return item


def _handle_suggested_edges(
    source_doc: dict[str, Any],
    related: list[dict[str, Any]],
    analysis: dict[str, Any],
    user_id: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    related_by_id = {doc["id"]: doc for doc in related}
    related_by_filename = {doc["filename"]: doc for doc in related}
    suggested_edges = analysis.get("edges") if isinstance(analysis.get("edges"), list) else []
    if not suggested_edges:
        return 0, []

    edge_count = 0
    candidates: list[dict[str, Any]] = []
    for edge in suggested_edges[:10]:
        target = _text(edge.get("target") or edge.get("target_id") or "").strip()
        target_doc = related_by_id.get(target) or related_by_filename.get(os.path.basename(target))
        if not target_doc or target_doc["id"] == source_doc["id"]:
            continue
        edge_type = edge.get("edge_type") or "related"
        edge_type = edge_type if edge_type in EDGE_TYPES else "related"
        confidence = _edge_confidence(edge)
        reason = _text(edge.get("reason") or "").strip()
        weak_reason = _is_weak_graph_reason(reason)
        if weak_reason or len(reason) < 12:
            continue
        if confidence >= GRAPH_AUTO_CONFIDENCE_MIN:
            add_edge_once(
                source_doc["id"],
                target_doc["id"],
                edge_type=edge_type,
                user_id=user_id,
                weight=confidence,
                metadata={"reason": reason, "source_import": source_doc["id"], "auto_graph": True},
            )
            edge_count += 1
        elif confidence >= GRAPH_REVIEW_CONFIDENCE_MIN:
            continue
    return edge_count, candidates


def _apply_wiki_diff(item: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    filename = _ensure_md_name(item.get("target_filename") or item.get("target_id") or "wiki-page.md")
    path = os.path.join(paths["pages"], filename)
    proposed = _text(item.get("proposed_content") or "")
    if not proposed.strip():
        raise ValueError("pending diff has no proposed_content")
    _write_text(path, proposed.rstrip() + "\n")
    doc = _doc_record("page", filename, path, proposed, paths["base"])
    _ensure_node(doc, user_id)
    _upsert_index_doc(doc, user_id)
    item["status"] = "applied"
    item["applied_at"] = int(time.time())
    _append_log(user_id, [f"Applied wiki diff `{item.get('id')}` to `{filename}`."])
    refresh_index_and_cache(user_id)
    return item


def _apply_graph_candidate(item: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    edge = item.get("edge") if isinstance(item.get("edge"), dict) else {}
    source = _text(edge.get("source") or item.get("source", {}).get("source_id") or "").strip()
    target = _text(edge.get("target") or item.get("target_id") or "").strip()
    if not source or not target:
        raise ValueError("graph candidate has no source/target")
    added = add_edge_once(
        source,
        target,
        edge_type=edge.get("edge_type") or "related",
        user_id=user_id,
        weight=float(edge.get("weight") or item.get("confidence") or 0.5),
        metadata=edge.get("metadata") or {"reason": item.get("reason") or "", "source_import": source, "reviewed": True},
    )
    item["status"] = "applied"
    item["applied_at"] = int(time.time())
    item["applied_edge_id"] = added.get("id")
    _append_log(user_id, [f"Applied graph candidate `{item.get('id')}`: `{source}` -> `{target}`."])
    refresh_index_and_cache(user_id)
    return item


def _cleanup_extraction_temp_dirs(metadata: dict[str, Any]) -> None:
    for path in (metadata or {}).get("cleanup_dirs") or []:
        try:
            if path and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def _persist_source_assets(
    paths: dict[str, str],
    source_id: str,
    source_path: str,
    images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not images:
        return []
    asset_key = _safe_part(source_id.replace("/", "-"))
    asset_dir = os.path.join(paths["assets"], asset_key)
    os.makedirs(asset_dir, exist_ok=True)
    out: list[dict[str, Any]] = []
    for idx, image in enumerate(images, start=1):
        src = _text(image.get("path") or "")
        if not src or not os.path.isfile(src):
            continue
        filename = _safe_part(os.path.basename(_text(image.get("filename") or src)))
        stem, ext = os.path.splitext(filename)
        if not ext:
            ext = os.path.splitext(src)[1] or ".png"
        candidate = f"{stem or 'image'}{ext}"
        dest = os.path.join(asset_dir, candidate)
        n = 2
        while os.path.exists(dest):
            candidate = f"{stem or 'image'}-{n}{ext}"
            dest = os.path.join(asset_dir, candidate)
            n += 1
        try:
            shutil.copy2(src, dest)
        except Exception:
            continue
        rel_path = _relative(dest, paths["base"])
        markdown_path = os.path.relpath(dest, os.path.dirname(source_path)).replace("\\", "/")
        out.append({
            "kind": image.get("kind") or "image",
            "label": image.get("label") or f"Source image {idx}",
            "filename": candidate,
            "rel_path": rel_path,
            "markdown_path": markdown_path,
            "source_name": image.get("source_name") or "",
        })
    return out


def _append_asset_references(markdown: str, assets: list[dict[str, Any]]) -> str:
    if not assets:
        return markdown
    lines = [markdown.rstrip(), "", "## Source Assets"]
    for idx, asset in enumerate(assets, start=1):
        label = _text(asset.get("label") or f"Source asset {idx}").replace("\n", " ").strip()
        path = _text(asset.get("markdown_path") or asset.get("rel_path") or "").strip()
        kind = _text(asset.get("kind") or "image").strip()
        if not path:
            continue
        lines.append(f"![{label}]({path})")
        lines.append(f"- kind: {kind}")
    return "\n".join(lines).rstrip() + "\n"


def _persist_source(
    filename: str,
    markdown: str,
    user_id: str | None = None,
    images: list[dict[str, Any]] | None = None,
    source_type: str = "upload",
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    source_name = _unique_name(paths["sources"], filename)
    path = os.path.join(paths["sources"], source_name)
    source_id = _doc_id("source", source_name)
    assets = _persist_source_assets(paths, source_id, path, images or [])
    markdown = _append_asset_references(markdown, assets)
    _write_text(path, markdown.rstrip() + "\n")
    doc = _doc_record("source", source_name, path, markdown, paths["base"])
    doc["assets"] = assets
    doc["source_type"] = source_type
    doc["source_metadata"] = source_metadata or {}
    _ensure_node(doc, user_id)
    _upsert_index_doc(doc, user_id)
    return doc


def import_source_markdown(
    filename: str,
    markdown: str,
    user_id: str | None = None,
    images: list[dict[str, Any]] | None = None,
    source_type: str = "markdown",
    source_metadata: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Import already-normalized markdown plus optional local image assets."""
    source_metadata = source_metadata or {}

    def _progress(progress: int, message: str) -> None:
        if callable(progress_callback):
            try:
                progress_callback(progress, message)
            except Exception:
                pass

    markdown = _text(markdown).strip()
    if not markdown:
        raise ValueError("markdown required")
    if not markdown.lstrip().startswith("#"):
        title = os.path.splitext(os.path.basename(filename or "source.md"))[0]
        markdown = f"# {title}\n\n{markdown}"
    conversion = {"converted": False, "error": "", "skipped": True}
    if source_type == "notion":
        _progress(35, "converting Notion page to wiki markdown")
        markdown, conversion = _to_wiki_markdown(
            filename,
            markdown,
            force_llm=True,
            source_type=source_type,
        )

    _progress(55, "saving source markdown and assets")
    source_doc = _persist_source(
        filename,
        markdown.rstrip() + "\n",
        user_id=user_id,
        images=images or [],
        source_type=source_type,
        source_metadata=source_metadata,
    )

    _progress(65, "finding related source documents")
    related = _related_docs(source_doc, user_id=user_id, limit=6)
    _progress(72, "analyzing graph links and maintenance items")
    analysis = _analysis_for_import(source_doc, related, user_id=user_id)
    edge_count, maintenance_items = _handle_suggested_edges(source_doc, related, analysis, user_id=user_id)

    _progress(88, "refreshing index, log, and graph cache")
    refresh_index_and_cache(user_id)
    _append_log(
        user_id,
        [
            f"Imported source `{source_doc['filename']}`.",
            f"Graph links updated: {edge_count}.",
            f"Maintenance items: {len(maintenance_items)}.",
        ],
    )
    return {
        "source_id": source_doc["id"],
        "raw_path": source_doc["path"],
        "wiki_path": source_doc["rel_path"],
        "markdown_chars": len(markdown),
        "extractor": "notion-markdown" if source_type == "notion" else "markdown",
        "converted_to_wiki": bool(conversion.get("converted")),
        "conversion_error": conversion.get("error", ""),
        "conversion_model_kind": conversion.get("model_kind", ""),
        "conversion_model_used": conversion.get("model_used", ""),
        "conversion_fallback_errors": conversion.get("fallback_errors") or [],
        "analysis_model_kind": analysis.get("_internal_model_kind") or analysis.get("_curator_provider", "") if isinstance(analysis, dict) else "",
        "analysis_model_used": analysis.get("_internal_model_used") or analysis.get("_curator_model", "") if isinstance(analysis, dict) else "",
        "analysis_error": analysis.get("error", "") if isinstance(analysis, dict) else "",
        "warnings": [],
        "asset_count": len(source_doc.get("assets") or []),
        "image_count": len(source_doc.get("assets") or []),
        "assets": source_doc.get("assets") or [],
        "images": source_doc.get("assets") or [],
        "source_type": source_type,
        "source_metadata": source_metadata,
        "raw_chunk_count": 0,
        "candidate_count": len(maintenance_items),
        "pending_candidate_count": len([x for x in maintenance_items if x.get("status") == "pending"]),
        "candidates": maintenance_items,
        "auto_triage": {"approved": 0, "merged": 0, "skipped": 0, "pending": len(maintenance_items), "failed": 0},
        "graph_edges_added": edge_count,
    }


def import_source_bytes(
    filename: str,
    raw: bytes,
    user_id: str | None = None,
    domain: str = "",
    service: str = "",
    auto_candidates: bool = True,
    auto_triage: bool = True,
    source_type: str = "upload",
    source_metadata: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    info: dict[str, Any] = {}
    source_metadata = source_metadata or {}
    def _progress(progress: int, message: str) -> None:
        if callable(progress_callback):
            try:
                progress_callback(progress, message)
            except Exception:
                pass
    try:
        _progress(10, "extracting source text")
        text, info = _decode_text(filename, raw)
        if not text.strip():
            raise ValueError("No text could be extracted from source")

        _progress(35, "converting source to markdown")
        markdown, conversion = _to_wiki_markdown(filename, text, source_type=source_type)
        _progress(55, "saving source markdown and assets")
        source_doc = _persist_source(
            filename,
            markdown,
            user_id=user_id,
            images=info.get("images") or [],
            source_type=source_type,
            source_metadata=source_metadata,
        )
    finally:
        _cleanup_extraction_temp_dirs((info or {}).get("metadata") or {})

    _progress(65, "finding related source documents")
    related = _related_docs(source_doc, user_id=user_id, limit=6)
    _progress(72, "analyzing graph links and maintenance items")
    analysis = _analysis_for_import(source_doc, related, user_id=user_id)
    edge_count, maintenance_items = _handle_suggested_edges(source_doc, related, analysis, user_id=user_id)

    _progress(88, "refreshing index, log, and graph cache")
    refresh_index_and_cache(user_id)
    _append_log(
        user_id,
        [
            f"Imported source `{source_doc['filename']}`.",
            f"Graph links updated: {edge_count}.",
            f"Maintenance items: {len(maintenance_items)}.",
        ],
    )
    return {
        "source_id": source_doc["id"],
        "raw_path": source_doc["path"],
        "wiki_path": source_doc["rel_path"],
        "markdown_chars": len(markdown),
        "extractor": info.get("extractor", ""),
        "converted_to_wiki": bool(conversion.get("converted")),
        "conversion_error": conversion.get("error", ""),
        "conversion_model_kind": conversion.get("model_kind", ""),
        "conversion_model_used": conversion.get("model_used", ""),
        "conversion_fallback_errors": conversion.get("fallback_errors") or [],
        "analysis_model_kind": analysis.get("_internal_model_kind") or analysis.get("_curator_provider", "") if isinstance(analysis, dict) else "",
        "analysis_model_used": analysis.get("_internal_model_used") or analysis.get("_curator_model", "") if isinstance(analysis, dict) else "",
        "analysis_error": analysis.get("error", "") if isinstance(analysis, dict) else "",
        "warnings": info.get("warnings") or [],
        "asset_count": len(source_doc.get("assets") or []),
        "image_count": len(source_doc.get("assets") or []),
        "assets": source_doc.get("assets") or [],
        "images": source_doc.get("assets") or [],
        "source_type": source_type,
        "source_metadata": source_metadata,
        "raw_chunk_count": 0,
        "candidate_count": len(maintenance_items),
        "pending_candidate_count": len([x for x in maintenance_items if x.get("status") == "pending"]),
        "candidates": maintenance_items,
        "auto_triage": {"approved": 0, "merged": 0, "skipped": 0, "pending": len(maintenance_items), "failed": 0},
        "graph_edges_added": edge_count,
        "mode": "llm_wiki",
    }


def promote_conversation_source(
    *,
    messages: list[dict[str, Any]],
    user_id: str | None = None,
    session_id: str = "",
    title: str = "",
    domain: str = "",
    service: str = "",
) -> dict[str, Any]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages required")
    clean = []
    for msg in messages[-40:]:
        if not isinstance(msg, dict):
            continue
        role = _text(msg.get("role") or "").strip()[:24] or "message"
        content = _message_content_text(msg.get("content")).strip()
        if content:
            clean.append({"role": role, "content": content[:4000]})
    if not clean:
        raise ValueError("conversation has no text content")

    fallback_title = _conversation_title(clean)
    title = _text(title).strip() or fallback_title
    markdown = _conversation_to_source_markdown(
        title=title,
        session_id=session_id,
        messages=clean,
        domain=domain,
        service=service,
    )
    digest = hashlib.sha1((session_id + "\n" + json.dumps(clean, ensure_ascii=False)).encode("utf-8", errors="ignore")).hexdigest()[:10]
    filename = _ensure_md_name(f"conversation-{_safe_part(title)[:60]}-{digest}.md")
    return import_source_bytes(
        filename=filename,
        raw=markdown.encode("utf-8"),
        user_id=user_id,
        domain=domain,
        service=service,
        source_type="chat",
        source_metadata={
            "session_id": session_id,
            "message_count": len(clean),
            "verification_status": "conversation_derived",
        },
    )


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text"}:
                    parts.append(_text(item.get("text")))
                elif item.get("text"):
                    parts.append(_text(item.get("text")))
            else:
                parts.append(_text(item))
        return "\n".join(p for p in parts if p.strip())
    return _text(content)


def _conversation_title(messages: list[dict[str, str]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = re.sub(r"\s+", " ", msg.get("content", "")).strip()
            if text:
                return text[:80]
    return "Conversation Knowledge"


def _conversation_to_source_markdown(
    *,
    title: str,
    session_id: str,
    messages: list[dict[str, str]],
    domain: str,
    service: str,
) -> str:
    parsed, _error = _llm_json(
        "conversation_to_llm_wiki_markdown",
        {
            "title": title,
            "session_id": session_id,
            "domain": domain,
            "service": service,
            "messages": messages,
            "rules": [
                "Extract durable reusable knowledge only.",
                "Exclude UI preferences, temporary commands, troubleshooting chatter, and formatting-only requests unless they define a stable rule.",
                "Keep domain concepts, definitions, equations, assumptions, caveats, procedures, decisions, and examples.",
                "Use markdown with a clear H1 title, short sections, and source trace notes.",
                "Mark conversation-derived claims as needing review unless backed by an uploaded source.",
                "Use Unicode/plain text equations instead of raw LaTeX where possible.",
            ],
        },
        max_tokens=1100,
    )
    content = _text((parsed or {}).get("markdown") or (parsed or {}).get("content")).strip()
    if not content:
        lines = [
            f"# {title}",
            "",
            "> Source type: conversation",
            f"> Session: {session_id or 'current'}",
            "> Verification: conversation-derived; review before treating as canonical knowledge.",
            "",
            "## Candidate Knowledge",
            "",
            "This source was promoted from a chat session. Review the excerpts below and apply only durable, reusable knowledge to wiki pages.",
            "",
            "## Conversation Excerpts",
        ]
        for idx, msg in enumerate(messages, start=1):
            role = msg.get("role") or "message"
            body = msg.get("content") or ""
            lines.extend(["", f"### {idx}. {role}", "", body[:2500].strip()])
        content = "\n".join(lines).strip()
    elif not content.lstrip().startswith("#"):
        content = f"# {title}\n\n{content}"
    header = [
        "---",
        "source_type: conversation",
        f"session_id: {session_id}",
        "verification_status: conversation_derived",
        f"domain: {domain}",
        f"service: {service}",
        "---",
        "",
    ]
    return "\n".join(header) + content.rstrip() + "\n"


def list_thoughts(user_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for doc in _all_docs(user_id, include_content=True):
        out.append({
            "id": doc["id"],
            "title": doc["filename"],
            "memory_type": "document",
            "domain": "",
            "service": "",
            "topics": doc.get("tags") or [],
            "sources": [{"type": doc["kind"], "path": doc["rel_path"]}],
            "status": "active",
            "meaning_version": 1,
            "updated_at": doc.get("updated_at", 0),
            "path": doc["path"],
            "filename": doc["filename"],
            "kind": doc["kind"],
            "content_preview": doc.get("content_preview", ""),
            "_content": doc.get("_content", ""),
        })
    return out


def get_thought(thought_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    for doc in _all_docs(user_id, include_content=True):
        if doc["id"] == thought_id or doc["filename"] == thought_id or os.path.basename(doc["rel_path"]) == thought_id:
            meta = {
                "id": doc["id"],
                "title": doc["filename"],
                "memory_type": "document",
                "domain": "",
                "service": "",
                "topics": doc.get("tags") or [],
                "sources": [{"type": doc["kind"], "path": doc["rel_path"]}],
                "status": "active",
                "meaning_version": 1,
                "updated_at": doc.get("updated_at", 0),
                "path": doc["path"],
                "filename": doc["filename"],
                "kind": doc["kind"],
            }
            return {"meta": meta, "content": doc.get("_content", "")}
    return None


def get_system_doc(name: str, user_id: str | None = None) -> dict[str, Any] | None:
    key = _text(name).strip().lower()
    if key not in {"index", "log"}:
        return None
    paths = ensure_dirs(user_id)
    path = paths["index"] if key == "index" else paths["log"]
    content = _read_text(path) if os.path.isfile(path) else ""
    return {
        "id": f"system/{key}",
        "title": "Index" if key == "index" else "Log",
        "filename": "index.md" if key == "index" else "log.md",
        "kind": "system",
        "path": path,
        "rel_path": _relative(path, paths["base"]),
        "updated_at": int(os.path.getmtime(path)) if os.path.exists(path) else 0,
        "content": content,
    }


def save_thought(meta: dict[str, Any], content: str, user_id: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    paths = ensure_dirs(user_id)
    filename = _ensure_md_name(meta.get("filename") or meta.get("title") or meta.get("id") or "wiki-page.md")
    path = os.path.join(paths["sources"], filename)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(filename)
    if not content.lstrip().startswith("#"):
        content = f"# {os.path.splitext(filename)[0]}\n\n{content.strip()}\n"
    _write_text(path, content.rstrip() + "\n")
    doc = _doc_record("source", filename, path, content, paths["base"])
    _ensure_node(doc, user_id)
    _upsert_index_doc(doc, user_id)
    refresh_index_and_cache(user_id)
    _append_log(user_id, [f"Saved source markdown `{filename}`."])
    return {"meta": {"id": doc["id"], "title": doc["filename"], "path": doc["path"], "kind": doc["kind"]}, "content": content}


def delete_thought(thought_id: str, user_id: str | None = None) -> bool:
    thought = get_thought(thought_id, user_id=user_id)
    if not thought:
        return False
    path = thought["meta"].get("path")
    doc_id = thought["meta"].get("id")
    if path and os.path.isfile(path):
        os.remove(path)
    _delete_index_doc(doc_id, user_id)
    _remove_edges_for(doc_id, user_id)
    data = _graph(user_id)
    data["nodes"] = [n for n in data.get("nodes") or [] if n.get("id") != doc_id]
    _write_graph(data, user_id)
    refresh_index_and_cache(user_id)
    _append_log(user_id, [f"Deleted wiki document `{doc_id}`."])
    return True


def search_thoughts(
    query: str,
    user_id: str | None = None,
    max_results: int = 8,
    include_legacy: bool = True,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    return search_wiki(query, user_id=user_id, max_results=max_results)


def list_candidates(user_id: str | None = None, status: str | None = None, q: str = "", include_deleted: bool = True) -> list[dict[str, Any]]:
    items = [item for item in _load_pending_diffs(user_id) if _visible_candidate(item)]
    if status:
        items = [item for item in items if item.get("status", "pending") == status]
    elif not include_deleted:
        items = [item for item in items if item.get("status") != "deleted"]
    if q:
        ql = q.lower()
        items = [
            item for item in items
            if ql in _text(item.get("title", "")).lower()
            or ql in _text(item.get("content", "")).lower()
            or ql in _text(item.get("target_filename", "")).lower()
        ]
    return sorted(items, key=lambda x: int(x.get("updated_at") or x.get("created_at") or 0), reverse=True)


def candidate_counts(user_id: str | None = None) -> dict[str, int]:
    counts = {"pending": 0, "applied": 0, "rejected": 0, "skipped": 0, "deleted": 0, "all": 0}
    for item in _load_pending_diffs(user_id):
        if not _visible_candidate(item):
            continue
        status = item.get("status") or "pending"
        counts[status] = counts.get(status, 0) + 1
        counts["all"] += 1
    return counts


def get_candidate(candidate_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    candidate_id = _text(candidate_id)
    for item in _load_pending_diffs(user_id):
        if item.get("id") == candidate_id and _visible_candidate(item):
            return item
    return None


def save_candidate(candidate: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    candidate["updated_at"] = int(time.time())
    items = [x for x in _load_pending_diffs(user_id) if x.get("id") != candidate.get("id")]
    items.append(candidate)
    _write_pending_diffs(items, user_id)
    return candidate


def apply_candidate_action(
    candidate_id: str,
    action: str,
    user_id: str | None = None,
    updates: dict[str, Any] | None = None,
    merge_target: str | None = None,
    skip_reason: str = "",
    use_llm: bool = False,
) -> dict[str, Any]:
    item = get_candidate(candidate_id, user_id=user_id)
    if not item:
        raise FileNotFoundError(candidate_id)
    updates = updates or {}
    if "proposed_content" in updates:
        item["proposed_content"] = updates["proposed_content"]
    if "content" in updates and not item.get("proposed_content"):
        item["proposed_content"] = updates["content"]
    now = int(time.time())
    if action in {"approve", "apply", "merge", "replace", "supersede"}:
        if item.get("candidate_kind") == "graph_candidate":
            item = _apply_graph_candidate(item, user_id=user_id)
        else:
            raise ValueError("legacy page diffs are disabled; edit source markdown directly")
    elif action in {"skip", "reject"}:
        item["status"] = "rejected"
        item["skip_reason"] = skip_reason or updates.get("skip_reason") or "graph candidate rejected"
        item["rejected_at"] = now
        _append_log(user_id, [f"Rejected maintenance candidate `{candidate_id}`."])
    elif action == "delete":
        item["previous_status"] = item.get("status", "pending")
        item["status"] = "deleted"
        item["deleted_at"] = now
    elif action == "restore":
        item["status"] = item.get("previous_status") or "pending"
        item["restored_at"] = now
    elif action == "purge":
        items = [x for x in _load_pending_diffs(user_id) if x.get("id") != candidate_id]
        _write_pending_diffs(items, user_id)
        return {"id": candidate_id, "status": "purged"}
    elif action == "refine":
        item["status"] = "pending"
    else:
        raise ValueError(f"unknown action: {action}")
    item["updated_at"] = now
    return save_candidate(item, user_id=user_id)


def curation_review_candidate(
    candidate_id: str,
    action: str = "review",
    target_id: str = "",
    user_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    item = get_candidate(candidate_id, user_id=user_id)
    if not item:
        raise FileNotFoundError(candidate_id)
    rec = {
        "ok": True,
        "recommended_action": "approve",
        "target_id": item.get("target_id", ""),
        "confidence": item.get("confidence", 0.7),
        "reason": item.get("reason") or "This is a graph relation candidate. Applying it adds the relation to graph.json; rejecting it keeps source markdown unchanged.",
        "proposed_title": item.get("title", ""),
        "proposed_content": item.get("content", ""),
        "proposed_edges": [],
        "requires_human_review": True,
        "fallback": False,
        "curator_model": "llm-wiki-store",
    }
    if persist:
        item["llm_recommendation"] = rec
        save_candidate(item, user_id=user_id)
    return {"candidate": item, "recommendation": rec, "source_context_chars": 0, "similar_count": 0}


def curation_generate_title(candidate_id: str = "", thought_id: str = "", user_id: str | None = None) -> dict[str, Any]:
    item = get_candidate(candidate_id, user_id=user_id) if candidate_id else None
    if not item and thought_id:
        thought = get_thought(thought_id, user_id=user_id)
        if thought:
            item = {"title": thought["meta"].get("title"), "content": thought.get("content", "")}
    if not item:
        raise FileNotFoundError(candidate_id or thought_id)
    title = _extract_title(_text(item.get("proposed_content") or item.get("content")), item.get("title") or "wiki")
    return {"ok": True, "proposed_title": title, "fallback": False, "error": ""}


def graph_data(
    user_id: str | None = None,
    q: str = "",
    focus: str = "",
    depth: int | None = None,
    memory_type: str = "",
    service: str = "",
    topic: str = "",
    edge_type: str = "",
    include_legacy: bool = True,
    view: str = "wiki",
) -> dict[str, Any]:
    refresh_index_and_cache(user_id)
    docs = {doc["id"]: doc for doc in _all_docs(user_id, include_content=False)}
    data = _graph(user_id)
    query = q.lower().strip()
    topic_l = topic.lower().strip()
    nodes = []
    node_ids: set[str] = set()
    for node in data.get("nodes") or []:
        doc = docs.get(node.get("id"))
        if not doc:
            continue
        if doc:
            node = {**node, **{k: doc.get(k) for k in ("filename", "kind", "summary", "tags", "updated_at", "rel_path")}}
        hay = _text(node).lower()
        if query and query not in hay:
            continue
        if topic_l and topic_l not in hay:
            continue
        node_ids.add(node["id"])
        nodes.append(node)

    edges = [e for e in data.get("edges") or [] if e.get("source") in node_ids and e.get("target") in node_ids]
    if edge_type:
        edges = [e for e in edges if e.get("edge_type") == edge_type]
    if focus and depth is not None and depth >= 0:
        focus_id = focus
        keep = _neighbors({focus_id}, user_id=user_id, depth=depth)
        node_ids = node_ids & keep
        nodes = [n for n in nodes if n.get("id") in node_ids]
        edges = [e for e in edges if e.get("source") in node_ids and e.get("target") in node_ids]
    degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    out_nodes = []
    for node in nodes:
        out_nodes.append({"data": {
            "id": node["id"],
            "label": node.get("filename") or node["id"],
            "title": node.get("filename") or node.get("title") or node["id"],
            "memory_type": "document",
            "view_type": node.get("kind") or "wiki",
            "kind": node.get("kind") or "wiki",
            "domain": "",
            "service": "",
            "topics": node.get("tags") or [],
            "content_preview": node.get("summary", ""),
            "filename": node.get("filename") or node["id"],
            "rel_path": node.get("rel_path", ""),
            "degree": degree[node["id"]],
        }})
    return {
        "view": "wiki",
        "nodes": out_nodes,
        "edges": [{"data": e} for e in edges],
        "counts": {"nodes": len(out_nodes), "edges": len(edges)},
        "filters": {"memory_types": ["document"], "edge_types": sorted(EDGE_TYPES)},
    }


def build_pinned_context(ids: list[str], user_id: str | None = None) -> tuple[str, list[str]]:
    chunks = ["=== Pinned LLM Wiki Pages ==="]
    used = []
    for item_id in ids:
        thought = get_thought(item_id, user_id=user_id)
        if not thought:
            continue
        meta = thought["meta"]
        used.append(meta["id"])
        chunks.append(f"\n--- {meta.get('title')} ({meta.get('id')}) ---\n{thought.get('content', '')[:4000]}")
    return ("\n".join(chunks), used) if used else ("", [])


def build_search_context(
    query: str,
    user_id: str | None = None,
    max_results: int = 5,
    hop_depth: int = 1,
) -> tuple[str, list[str]]:
    text, ids, _meta = build_search_context_with_metadata(query, user_id=user_id, max_results=max_results, hop_depth=hop_depth)
    return text, ids


def build_search_context_with_metadata(
    query: str,
    user_id: str | None = None,
    max_results: int = 5,
    hop_depth: int = 1,
) -> tuple[str, list[str], dict[str, Any]]:
    refresh_index_and_cache(user_id)
    paths = ensure_dirs(user_id)
    query = (query or "").strip()
    docs = _all_docs(user_id, include_content=True)
    results = search_wiki(query, user_id=user_id, max_results=max_results)
    if not results and docs:
        # Important UX behavior: Second Brain ON must still provide the wiki map
        # and recent docs after an upload, even when keyword scoring misses.
        recent = sorted(docs, key=lambda d: int(d.get("updated_at") or 0), reverse=True)[:max_results]
        results = [{k: v for k, v in doc.items() if not k.startswith("_")} for doc in recent]
        fallback_note = "no exact wiki match; recent wiki documents included"
    else:
        fallback_note = "wiki matches retrieved" if results else "no LLM Wiki documents available"

    seed_ids = {r["id"] for r in results}
    expanded_ids = _neighbors(seed_ids, user_id=user_id, depth=max(0, hop_depth)) if seed_ids else set()
    by_id = {doc["id"]: doc for doc in docs}
    selected = []
    for doc_id in list(seed_ids) + [x for x in sorted(expanded_ids) if x not in seed_ids]:
        if doc_id in by_id:
            selected.append(by_id[doc_id])
        if len(selected) >= max(max_results + 4, 8):
            break

    if not selected:
        return "", [], {
            "mode": "llm-wiki",
            "used_sources": [],
            "used_raw_chunks": [],
            "used_thoughts": [],
            "used_wiki_pages": [],
            "coverage_note": fallback_note,
            "context_truncated": False,
        }

    parts = [
        "=== Second Brain / LLM Wiki Context ===",
        f"query: {query}",
        "retrieval_mode: llm-wiki",
        "Canonical knowledge is markdown in sources/. index.md is the navigation map; graph.json expands approved source relations; log.md gives recent change context.",
        "",
        "=== index.md ===",
        _read_text(paths["index"])[:3500],
        "",
        "=== recent log.md ===",
        _read_text(paths["log"])[-1800:],
        "",
        "=== Retrieved Wiki Markdown ===",
    ]
    used_ids: list[str] = []
    used_chars = sum(len(p) for p in parts)
    truncated = False
    for doc in selected:
        content = doc.get("_content", "")
        chunk = (
            f"\n--- wiki: {doc['filename']} ({doc['id']}, {doc['kind']}) ---\n"
            f"path: {doc['rel_path']}\n"
            f"tags: {', '.join(doc.get('tags') or [])}\n"
            f"{content}\n"
        )
        if used_chars + len(chunk) > MAX_KNOWLEDGE_INJECT_CHARS:
            remain = MAX_KNOWLEDGE_INJECT_CHARS - used_chars
            if remain < 500:
                truncated = True
                break
            chunk = chunk[:remain] + "\n... (truncated)"
            truncated = True
        parts.append(chunk)
        used_chars += len(chunk)
        used_ids.append(doc["id"])
    meta = {
        "mode": "llm-wiki",
        "used_sources": [x for x in used_ids if x.startswith("source/")],
        "used_raw_chunks": [],
        "used_thoughts": [],
        "used_wiki_pages": used_ids,
        "coverage_note": fallback_note,
        "context_truncated": truncated,
    }
    return "\n".join(parts), used_ids, meta


def convert_legacy_docs(user_id: str | None = None, filename: str | None = None, max_sections: int = 40) -> dict[str, Any]:
    refresh_index_and_cache(user_id)
    return {
        "mode": "llm_wiki",
        "converted": 0,
        "message": "Atomic migration is removed. Markdown wiki files are now canonical; upload markdown sources directly.",
    }


def list_sources(user_id: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": doc["id"],
            "source_id": doc["id"],
            "title": doc["filename"],
            "filename": doc["filename"],
            "domain": "",
            "service": "",
            "topics": doc.get("tags") or [],
            "content_preview": doc.get("content_preview", ""),
            "raw_chunk_count": 0,
            "thought_count": 0,
            "updated_at": doc.get("updated_at", 0),
        }
        for doc in _all_docs(user_id, include_content=True)
    ]


def search_sources(query: str, user_id: str | None = None, max_results: int = 8) -> list[dict[str, Any]]:
    return list_sources(user_id)[:max_results] if not query else search_wiki(query, user_id=user_id, max_results=max_results)
