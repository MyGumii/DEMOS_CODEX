"""
Project-scoped context store.

This layer keeps project documents separate from canonical Second Brain
knowledge. It is user-scoped and intended
for temporary or project-specific evidence used during spec-driven development.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from code_assist_v1.auth import scoped_dir
from code_assist_v1.config import MAX_KNOWLEDGE_INJECT_CHARS
from code_assist_v1.document_extractors import extract_document


def _safe_part(value: str, fallback: str = "default") -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\x00-\x1f\x7f<>:\"|?*\\/]+", "-", value)
    value = value.strip(". -_")
    return value[:80] or fallback


def make_id(value: str, fallback_prefix: str = "ctx") -> str:
    source = str(value or "").strip()
    slug = source.lower()
    slug = re.sub(r"[^a-z0-9가-힣_-]+", "-", slug)
    slug = slug.strip("-_")
    if not slug:
        slug = f"{fallback_prefix}-{hashlib.sha1(source.encode('utf-8', errors='ignore')).hexdigest()[:10]}"
    return slug[:90]


def _project_id(project_id: str | None = None) -> str:
    return _safe_part(project_id or "default", "default")


def _base_dir(user_id: str | None, project_id: str | None = None) -> str:
    project_root = scoped_dir("projects", user_id)
    return os.path.join(project_root, _project_id(project_id), "context")


def _paths(user_id: str | None, project_id: str | None = None) -> dict[str, str]:
    base = _base_dir(user_id, project_id)
    return {
        "base": base,
        "raw": os.path.join(base, "raw"),
        "assets": os.path.join(base, "assets"),
        "index": os.path.join(base, "index"),
        "manifest": os.path.join(base, "sources.jsonl"),
        "scores": os.path.join(base, "scores.jsonl"),
    }


def ensure_dirs(user_id: str | None, project_id: str | None = None) -> dict[str, str]:
    paths = _paths(user_id, project_id)
    for key in ("base", "raw", "assets", "index"):
        os.makedirs(paths[key], exist_ok=True)
    for key in ("manifest", "scores"):
        if not os.path.isfile(paths[key]):
            with open(paths[key], "a", encoding="utf-8"):
                pass
    return paths


def import_source_bytes(
    filename: str,
    raw: bytes,
    user_id: str | None,
    project_id: str | None = None,
    domain: str = "",
    service: str = "",
) -> dict[str, Any]:
    result = extract_document(filename, raw)
    markdown = result.markdown or ""
    if not markdown.strip():
        raise ValueError("No text could be extracted from project source")

    paths = ensure_dirs(user_id, project_id)
    source_id = make_id(os.path.splitext(filename)[0] + "-" + hashlib.sha1(raw).hexdigest()[:10], "ctx")
    try:
        assets = _persist_images(result.images or [], paths, source_id)
    finally:
        _cleanup_extraction_temp_dirs(result.metadata or {})
    if assets:
        markdown = _append_image_references(markdown, assets)

    now = int(time.time())
    raw_path = os.path.join(paths["raw"], source_id + ".md")
    header = [
        "---",
        f"project_id: {_project_id(project_id)}",
        f"source_id: {source_id}",
        f"filename: {filename}",
        f"extractor: {result.extractor}",
        f"imported_at: {now}",
        f"domain: {domain}",
        f"service: {service}",
        f"image_count: {len(assets)}",
    ]
    if result.warnings:
        header.append(f"warnings: {json.dumps(result.warnings, ensure_ascii=False)}")
    header.extend(["---", ""])
    _write_text(raw_path, "\n".join(header) + markdown.strip() + "\n")

    source = {
        "id": source_id,
        "project_id": _project_id(project_id),
        "filename": filename,
        "raw_path": os.path.relpath(raw_path, paths["base"]).replace("\\", "/"),
        "extractor": result.extractor,
        "warnings": result.warnings or [],
        "images": assets,
        "domain": domain,
        "service": service,
        "markdown_chars": len(markdown),
        "created_at": now,
    }
    chunks = create_chunks(markdown=markdown, source=source, user_id=user_id, project_id=project_id)
    _upsert_source(source, user_id=user_id, project_id=project_id)
    return {
        "source": source,
        "source_id": source_id,
        "raw_path": raw_path,
        "chunk_count": len(chunks),
        "markdown_chars": len(markdown),
        "warnings": result.warnings or [],
        "image_count": len(assets),
    }


def import_markdown_source(
    filename: str,
    markdown: str,
    user_id: str | None,
    project_id: str | None = None,
    domain: str = "",
    service: str = "",
    extractor: str = "workspace",
    warnings: list[str] | None = None,
    source_uri: str = "",
    bridge_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markdown = str(markdown or "").strip()
    if not markdown:
        raise ValueError("markdown required")
    paths = ensure_dirs(user_id, project_id)
    hash_source = source_uri or filename or markdown[:300]
    source_id = make_id(os.path.splitext(filename or "workspace-source")[0] + "-" + hashlib.sha1((hash_source + markdown).encode("utf-8", errors="ignore")).hexdigest()[:10], "ctx")
    now = int(time.time())
    raw_path = os.path.join(paths["raw"], source_id + ".md")
    warnings = warnings or []
    bridge_meta = bridge_meta or {}
    header = [
        "---",
        f"project_id: {_project_id(project_id)}",
        f"source_id: {source_id}",
        f"filename: {filename}",
        f"source_uri: {source_uri}",
        f"extractor: {extractor}",
        f"imported_at: {now}",
        f"domain: {domain}",
        f"service: {service}",
        "image_count: 0",
    ]
    if warnings:
        header.append(f"warnings: {json.dumps(warnings, ensure_ascii=False)}")
    if bridge_meta:
        header.append(f"bridge_meta: {json.dumps(bridge_meta, ensure_ascii=False, sort_keys=True)}")
    header.extend(["---", ""])
    _write_text(raw_path, "\n".join(header) + markdown + "\n")

    source = {
        "id": source_id,
        "project_id": _project_id(project_id),
        "filename": filename,
        "source_uri": source_uri,
        "raw_path": os.path.relpath(raw_path, paths["base"]).replace("\\", "/"),
        "extractor": extractor,
        "warnings": warnings,
        "images": [],
        "domain": domain,
        "service": service,
        "markdown_chars": len(markdown),
        "created_at": now,
        "bridge_meta": bridge_meta,
    }
    chunks = create_chunks(markdown=markdown, source=source, user_id=user_id, project_id=project_id)
    _upsert_source(source, user_id=user_id, project_id=project_id)
    return {
        "source": source,
        "source_id": source_id,
        "raw_path": raw_path,
        "chunk_count": len(chunks),
        "markdown_chars": len(markdown),
        "warnings": warnings,
        "image_count": 0,
    }


def list_sources(user_id: str | None, project_id: str | None = None) -> list[dict[str, Any]]:
    paths = ensure_dirs(user_id, project_id)
    out: list[dict[str, Any]] = []
    try:
        with open(paths["manifest"], "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
    except Exception:
        return []
    out.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    return out


def delete_source(source_id: str, user_id: str | None, project_id: str | None = None) -> bool:
    source_id = make_id(source_id, "ctx")
    paths = ensure_dirs(user_id, project_id)
    source = next((s for s in list_sources(user_id, project_id) if s.get("id") == source_id), None)
    removed = False
    for path in (
        os.path.join(paths["raw"], source_id + ".md"),
        os.path.join(paths["index"], source_id + ".jsonl"),
        os.path.join(paths["assets"], source_id),
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed = True
        elif os.path.isfile(path):
            os.remove(path)
            removed = True
    if source:
        kept = [s for s in list_sources(user_id, project_id) if s.get("id") != source_id]
        _write_manifest(kept, user_id=user_id, project_id=project_id)
        removed = True
    return removed


def clear_project(user_id: str | None, project_id: str | None = None) -> int:
    paths = _paths(user_id, project_id)
    base = paths["base"]
    removed = 0
    if os.path.isdir(base):
        for _, dirnames, filenames in os.walk(base):
            removed += len(dirnames) + len(filenames)
        shutil.rmtree(base, ignore_errors=True)
    ensure_dirs(user_id, project_id)
    return removed


def create_chunks(
    markdown: str,
    source: dict[str, Any],
    user_id: str | None,
    project_id: str | None = None,
    target_chars: int = 6500,
    max_chars: int = 9000,
    max_chunks: int = 80,
) -> list[dict[str, Any]]:
    units = _chunk_units(markdown, target_chars=target_chars, max_chars=max_chars)
    chunks: list[dict[str, Any]] = []
    now = int(time.time())
    source_id = source.get("id") or make_id(source.get("filename", "context"), "ctx")
    for idx, unit in enumerate(units[:max_chunks], start=1):
        text = _clean_text(unit.get("text", ""))
        if len(text) < 30:
            continue
        title = (unit.get("title") or f"Project context chunk {idx}")[:160]
        chunk = {
            "id": make_id(f"{source_id}-chunk-{idx}-{title}", "ctxchunk"),
            "project_id": _project_id(project_id),
            "source_id": source_id,
            "source": source,
            "chunk_index": idx,
            "title": title,
            "trace_label": f"chunk {idx}",
            "domain": source.get("domain", ""),
            "service": source.get("service", ""),
            "topics": _guess_topics(title + "\n" + text),
            "text": text,
            "content_hash": hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest(),
            "created_at": now,
        }
        chunks.append(chunk)
    _write_chunks(source_id, chunks, user_id=user_id, project_id=project_id)
    return chunks


def list_chunks(user_id: str | None, project_id: str | None = None, source_id: str | None = None) -> list[dict[str, Any]]:
    paths = ensure_dirs(user_id, project_id)
    files: list[str] = []
    if source_id:
        path = os.path.join(paths["index"], make_id(source_id, "ctx") + ".jsonl")
        files = [path] if os.path.isfile(path) else []
    else:
        files = [
            os.path.join(paths["index"], name)
            for name in sorted(os.listdir(paths["index"]))
            if name.endswith(".jsonl")
        ]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
        except Exception:
            continue
    return out


def search_context(
    query: str,
    user_id: str | None,
    project_id: str | None = None,
    max_results: int = 5,
    record_scores: bool = True,
) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    terms = [t for t in re.split(r"[^0-9A-Za-z가-힣_]+", query.lower()) if len(t) >= 2]
    if not terms:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in list_chunks(user_id=user_id, project_id=project_id):
        hay = " ".join([
            str(chunk.get("id", "")),
            str(chunk.get("title", "")),
            str(chunk.get("domain", "")),
            str(chunk.get("service", "")),
            " ".join(chunk.get("topics") or []),
            str(chunk.get("text", "")),
        ]).lower()
        score = 0.0
        for term in terms:
            if term in str(chunk.get("title", "")).lower():
                score += 5
            score += min(hay.count(term), 10) * 0.5
        if score > 0:
            clone = {k: v for k, v in chunk.items() if k != "text"}
            clone["content_preview"] = chunk.get("text", "")[:1200]
            clone["score"] = round(score, 3)
            scored.append((score, clone))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [x[1] for x in scored[:max_results]]
    if record_scores and results:
        _append_score_log(query=query, terms=terms, results=results, user_id=user_id, project_id=project_id)
    return results


def list_score_log(user_id: str | None, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    paths = ensure_dirs(user_id, project_id)
    out: list[dict[str, Any]] = []
    try:
        with open(paths["scores"], "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        return []
    out.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    return out[: max(1, min(int(limit or 100), 500))]


def build_project_context(
    query: str,
    user_id: str | None,
    project_id: str | None = None,
    max_results: int = 5,
) -> tuple[str, list[str]]:
    results = search_context(query=query, user_id=user_id, project_id=project_id, max_results=max_results)
    if not results:
        return "", []
    parts = [
        "=== Project Context Store ===",
        "These snippets are project-scoped context. They are not canonical Second Brain knowledge.",
        "Use them for the current project's spec-driven development, and do not generalize them unless the user explicitly promotes them to Second Brain.",
        "",
    ]
    used_ids: list[str] = []
    used_chars = len("\n".join(parts))
    for item in results:
        src = item.get("source") or {}
        block = (
            f"--- project-context: {item.get('title', item.get('id'))} ({item.get('id')}) ---\n"
            f"project_id: {item.get('project_id')}\n"
            f"source: {src.get('filename', '')}\n"
            f"trace: {item.get('trace_label', '')}\n"
            f"score: {item.get('score', 0)}\n"
            f"{item.get('content_preview', '')}\n"
        )
        if used_chars + len(block) > MAX_KNOWLEDGE_INJECT_CHARS:
            break
        parts.append(block)
        used_chars += len(block)
        used_ids.append(str(item.get("id", "")))
    return "\n".join(parts).strip(), used_ids


def _upsert_source(source: dict[str, Any], user_id: str | None, project_id: str | None = None) -> None:
    sources = [s for s in list_sources(user_id, project_id) if s.get("id") != source.get("id")]
    sources.append(source)
    _write_manifest(sources, user_id=user_id, project_id=project_id)


def _write_manifest(sources: list[dict[str, Any]], user_id: str | None, project_id: str | None = None) -> None:
    paths = ensure_dirs(user_id, project_id)
    text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in sources)
    _write_text(paths["manifest"], text)


def _write_chunks(source_id: str, chunks: list[dict[str, Any]], user_id: str | None, project_id: str | None = None) -> None:
    paths = ensure_dirs(user_id, project_id)
    path = os.path.join(paths["index"], make_id(source_id, "ctx") + ".jsonl")
    text = "".join(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n" for chunk in chunks)
    _write_text(path, text)


def _append_score_log(
    query: str,
    terms: list[str],
    results: list[dict[str, Any]],
    user_id: str | None,
    project_id: str | None = None,
    max_entries: int = 500,
) -> None:
    paths = ensure_dirs(user_id, project_id)
    entry = {
        "created_at": int(time.time()),
        "project_id": _project_id(project_id),
        "query_hash": hashlib.sha1(str(query or "").encode("utf-8", errors="ignore")).hexdigest()[:16],
        "query_preview": str(query or "")[:500],
        "terms": terms[:20],
        "results": [
            {
                "chunk_id": item.get("id"),
                "source_id": item.get("source_id"),
                "source": (item.get("source") or {}).get("filename", ""),
                "title": item.get("title", ""),
                "score": item.get("score", 0),
                "domain": item.get("domain", ""),
                "service": item.get("service", ""),
            }
            for item in results
        ],
    }
    entries: list[str] = []
    try:
        with open(paths["scores"], "r", encoding="utf-8") as f:
            entries = [line for line in f if line.strip()][-(max_entries - 1):]
    except Exception:
        entries = []
    entries.append(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    _write_text(paths["scores"], "".join(entries))


def _chunk_units(markdown: str, target_chars: int, max_chars: int) -> list[dict[str, str]]:
    lines = str(markdown or "").splitlines()
    sections: list[dict[str, str]] = []
    title = "Document"
    buffer: list[str] = []
    for line in lines:
        if re.match(r"^#{1,4}\s+", line.strip()) and buffer:
            sections.append({"title": title, "text": "\n".join(buffer).strip()})
            title = re.sub(r"^#{1,6}\s+", "", line.strip())[:160] or "Section"
            buffer = [line]
        else:
            if re.match(r"^#{1,4}\s+", line.strip()):
                title = re.sub(r"^#{1,6}\s+", "", line.strip())[:160] or title
            buffer.append(line)
    if buffer:
        sections.append({"title": title, "text": "\n".join(buffer).strip()})

    out: list[dict[str, str]] = []
    for section in sections:
        text = section["text"]
        if len(text) <= max_chars:
            out.append(section)
            continue
        paragraphs = re.split(r"\n{2,}", text)
        chunk: list[str] = []
        cur_len = 0
        idx = 1
        for para in paragraphs:
            if cur_len and cur_len + len(para) > target_chars:
                out.append({"title": f"{section['title']} part {idx}", "text": "\n\n".join(chunk)})
                idx += 1
                chunk = []
                cur_len = 0
            chunk.append(para)
            cur_len += len(para) + 2
        if chunk:
            out.append({"title": f"{section['title']} part {idx}" if idx > 1 else section["title"], "text": "\n\n".join(chunk)})
    return out or [{"title": "Document", "text": str(markdown or "")}]


def _guess_topics(text: str) -> list[str]:
    words = [w.lower() for w in re.findall(r"[A-Za-z가-힣][A-Za-z0-9가-힣_-]{2,}", text or "")]
    stop = {"the", "and", "for", "with", "from", "this", "that", "입니다", "있는", "하는", "그리고"}
    counts: dict[str, int] = {}
    for word in words:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:8]]


def _clean_text(text: str) -> str:
    return re.sub(r"\n{4,}", "\n\n\n", str(text or "").strip())


def _persist_images(images: list[dict[str, Any]], paths: dict[str, str], source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not images:
        return out
    asset_dir = os.path.join(paths["assets"], source_id)
    os.makedirs(asset_dir, exist_ok=True)
    for idx, image in enumerate(images, start=1):
        src = image.get("path")
        if not src or not os.path.isfile(src):
            continue
        ext = os.path.splitext(image.get("filename") or src)[1].lower() or ".png"
        name = f"asset_{idx:03d}{ext}"
        dest = os.path.join(asset_dir, name)
        shutil.copy2(src, dest)
        out.append({
            "path": os.path.relpath(dest, paths["base"]).replace("\\", "/"),
            "filename": name,
            "label": image.get("label") or f"Asset {idx}",
            "kind": image.get("kind") or "image",
        })
    return out


def _append_image_references(markdown: str, assets: list[dict[str, Any]]) -> str:
    refs = ["", "## Extracted Images"]
    for asset in assets:
        refs.append(f"- {asset.get('label')}: {asset.get('path')}")
    return str(markdown or "").rstrip() + "\n\n" + "\n".join(refs) + "\n"


def _cleanup_extraction_temp_dirs(metadata: dict[str, Any]) -> None:
    for path in metadata.get("cleanup_dirs") or []:
        try:
            if path and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
