"""Turn-bound citation identities and deterministic Markdown link rendering.

Pure transformation of an already-authorized model payload: no DB, filesystem,
model call or new authority. Link validity does not imply semantic claim support.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from html import escape, unescape
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

from markdown_it import MarkdownIt


class CitationValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CitationSource:
    handle: str
    item_id: str
    revision_id: str
    source_space_id: str | None
    title: str
    passages: tuple[str, ...]
    artifacts: tuple[CitationArtifact, ...]


@dataclass(frozen=True, slots=True)
class CitationArtifact:
    artifact_id: str
    source_revision_id: str
    filename: str
    content_sha256: str | None
    protected_source_kind: str | None = None


@dataclass(frozen=True, slots=True)
class CitationCatalog:
    selected_view_space_id: str
    sources: tuple[CitationSource, ...]


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise CitationValidationError("Invalid canonical citation identity.")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise CitationValidationError("Invalid canonical citation identity.") from exc
    return value


def prepare_citations(payload: dict, *, turn_id: str, selected_view_space_id: str) -> tuple[dict, CitationCatalog]:
    """Return a separate model payload; original server metadata is unchanged."""
    _uuid(turn_id)
    selected_view_space_id = _uuid(selected_view_space_id)
    result = deepcopy(payload)
    metadata = {}
    for row in payload.get("source_documents", []):
        key = (_uuid(row.get("item_id")), _uuid(row.get("revision_id")))
        if key in metadata and metadata[key] != row:
            raise CitationValidationError("Conflicting canonical source metadata.")
        metadata[key] = row
    mandatory = {row["identity"]: row for row in result.get("mandatory_contexts", [])}
    bodies: dict[tuple[str, str], dict] = {}
    entries = []
    for lane in ("retrieval_candidates", "wiki_body_evidence", "pins"):
        for row in result.get(lane, []):
            if row.get("availability", "available") != "available":
                continue
            text = row.get("excerpt_md")
            if row.get("mandatory_context_identity") in mandatory:
                shared = mandatory[row["mandatory_context_identity"]]
                if (shared.get("kind") not in {"pin", "stable_reference"}
                        or (row.get("item_id"), row.get("revision_id")) !=
                           (shared.get("target_item_id"), shared.get("target_revision_id"))):
                    raise CitationValidationError("Shared body citation identity is inconsistent.")
                text = shared.get("content_md")
            elif row.get("mandatory_context_identity") is not None:
                raise CitationValidationError("Shared body citation is unavailable.")
            entries.append((row, row.get("item_id"), row.get("revision_id"), text))
    for row in result.get("semantic_neighbors", []):
        entries.append((row, row.get("neighbor_item_id"), row.get("neighbor_revision_id"), row.get("excerpt_md")))
    for row in result.get("mandatory_contexts", []):
        if row.get("kind") in {"pin", "stable_reference"}:
            entries.append((row, row.get("target_item_id"), row.get("target_revision_id"), row.get("content_md")))
    for row, item, revision, text in entries:
        if not isinstance(text, str) or not text.strip():
            continue
        key = (_uuid(item), _uuid(revision))
        if key not in bodies:
            origin = metadata.get(key, {})
            source_space = row.get("source_space_id") or origin.get("source_space_id")
            bodies[key] = {
                "title": str(row.get("title") or origin.get("title") or "Wiki 문서"),
                "space": None if source_space is None else _uuid(source_space),
                "texts": [], "rows": [],
            }
        elif (row.get("source_space_id") is not None
              and row["source_space_id"] != bodies[key]["space"]):
            raise CitationValidationError("Conflicting canonical source space.")
        origin_space = metadata.get(key, {}).get("source_space_id")
        if origin_space is not None and origin_space != bodies[key]["space"]:
            raise CitationValidationError("Source metadata disagrees with delivered body.")
        if text not in bodies[key]["texts"]:
            bodies[key]["texts"].append(text)
        bodies[key]["rows"].append(row)
    sources = []
    for index, (key, body) in enumerate(bodies.items(), 1):
        # A previous turn's S1 must never silently mean this turn's other S1.
        digest = sha256(f"{turn_id}:{selected_view_space_id}:{body['space']}:{key[0]}:{key[1]}".encode()).hexdigest()[:12]
        handle = f"S{index}_{digest}"
        artifacts_by_id = {}
        for artifact in metadata.get(key, {}).get("artifacts", []):
            digest_value = artifact.get("content_sha256")
            if digest_value is not None and (not isinstance(digest_value, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest_value) is None):
                raise CitationValidationError("Invalid original-file digest.")
            protected_kind = artifact.get("protected_source_kind")
            if protected_kind is not None and (
                type(protected_kind) is not str
                or protected_kind not in {
                    "pdf_standard_encryption", "office_encrypted_package_candidate",
                    "company_policy_protected",
                }
            ):
                raise CitationValidationError("Invalid protected source kind.")
            record = CitationArtifact(
                _uuid(artifact.get("artifact_id")), _uuid(artifact.get("source_revision_id")),
                str(artifact.get("original_filename") or "원본 파일"), digest_value,
                protected_kind,
            )
            if record.artifact_id in artifacts_by_id and artifacts_by_id[record.artifact_id] != record:
                raise CitationValidationError("Conflicting original-file metadata.")
            artifacts_by_id[record.artifact_id] = record
        artifacts = tuple(artifacts_by_id.values())
        sources.append(CitationSource(handle, *key, body["space"], body["title"], tuple(body["texts"]), artifacts))
        for row in body["rows"]:
            row["citation_handle"] = handle
    handles = {(source.item_id, source.revision_id): source.handle for source in sources}
    # Keep canonical IDs for evidence tracing but never ask the model to copy URLs.
    result["source_documents"] = [row for row in result.get("source_documents", [])
                                  if (row["item_id"], row["revision_id"]) in handles]
    for row in result["source_documents"]:
        row["citation_handle"] = handles[row["item_id"], row["revision_id"]]
        for artifact in row.get("artifacts", []):
            artifact.pop("download_url", None)
            artifact.pop("protected_source_kind", None)
    result["citation_sources"] = [
        {"handle": source.handle, "title": source.title,
         "original_filenames": [artifact.filename for artifact in source.artifacts]}
        for source in sources
    ]
    return result, CitationCatalog(selected_view_space_id, tuple(sources))


def _label(value: str) -> str:
    clean = " ".join(value.split())
    clean = "".join(character for character in clean if ord(character) >= 32)
    return re.sub(r"([\\`*{}_\[\]()#!|])", r"\\\1", escape(clean, quote=True))


def _render_source(source: CitationSource, catalog: CitationCatalog) -> str:
    title = _label(source.title)
    if not source.artifacts:
        return f"{title} (원본 링크 미제공)"
    links = []
    for artifact in source.artifacts:
        if artifact.protected_source_kind is not None:
            # Publication rights do not grant original-byte transfer rights.
            # The Wiki is in the selected read closure; its original may not be.
            link = (f"[{title} — Wiki MD](/api/knowledge/v2/spaces/{catalog.selected_view_space_id}"
                    f"/items/{source.item_id}/markdown-download)")
        else:
            link = (f"[{_label(artifact.filename)} — {title}](/api/knowledge/v1/spaces/"
                    f"{catalog.selected_view_space_id}/items/{source.item_id}/source-artifacts/"
                    f"{artifact.artifact_id}/download)")
        if link not in links:
            links.append(link)
    return " · ".join(links)


def _citation_token(state, silent):
    """Inline rule: code spans/blocks are handled by the real Markdown parser."""
    start = state.pos
    canonical = state.src.startswith("[[wiki:", start)
    # Some compatible models omit only the 'wiki:' namespace. Recognize the
    # exact turn-handle syntax, never titles, item IDs, URLs or guessed sources.
    bare_handle = re.match(r"\[\[S[1-9][0-9]*_[0-9a-f]{12}\]\]", state.src[start:])
    if not canonical and bare_handle is None:
        return False
    offset = 7 if canonical else 2
    end = state.src.find("]]", start + offset)
    if end < 0:
        raise CitationValidationError("Unclosed citation marker.")
    if not silent:
        token = state.push("wiki_citation", "", 0)
        token.content = state.src[start + offset:end]
        token.meta["source_line"] = state.src.count("\n", 0, start)
    state.pos = end + 2
    return True


def _citation_lines(answer: str) -> dict[int, list[str]]:
    # No rendering and no I/O. Preserve the original Markdown byte-for-byte
    # except exact evidence lines; inspect syntax, not regex-masked source text.
    parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    # Inspect unsafe destinations instead of relying on parser-specific URL
    # rejection: the UI's Markdown renderer must not reinterpret one as active.
    parser.validateLink = lambda _url: True
    parser.inline.ruler.before("link", "wiki_citation", _citation_token)
    environment = {}
    tokens = parser.parse(answer, environment)
    markers: dict[int, list[str]] = {}

    def inspect_content(content):
        if _reserved_route(content):
            raise CitationValidationError("Knowledge links must be rendered by the server.")
        # Entity/escape/case variants cannot leak a visible unresolved marker.
        visible = unescape(content)
        if re.search(r"\[\[wiki\s*\\?:", visible, re.I):
            raise CitationValidationError("Malformed or misplaced citation marker.")
        # Browser HTML recovery accepts malformed forms such as <a/href=...>
        # even when a Markdown parser classifies them as plain text. Inspect
        # HTML-looking prose too; code tokens never enter this function.
        if re.search(r"<[A-Za-z/!]", content):
            _validate_html(content)

    def inspect_inline(children, line_start):
        prose = []
        for child in children:
            if child.type == "code_inline":
                prose.append(" ")
                continue
            if child.type == "wiki_citation":
                line = line_start + child.meta["source_line"]
                markers.setdefault(line, []).append(child.content)
                prose.append(" ")
                continue
            for value in child.attrs.values():
                inspect_content(str(value))
            for attribute in ("href", "src"):
                if attribute in child.attrs:
                    _validate_link(str(child.attrs[attribute]), image=attribute == "src")
            if child.type == "html_inline":
                _validate_html(child.content)
            if child.children:
                inspect_inline(child.children, line_start)
            prose.append(child.content if child.type not in {"softbreak", "hardbreak"} else "\n")
        inspect_content("".join(prose))

    for token in tokens:
        if token.type in {"fence", "code_block"}:
            continue
        if token.type == "inline":
            inspect_inline(token.children or (), token.map[0])
        elif token.type == "html_block":
            _validate_html(token.content)
            inspect_content(token.content)
    for reference in environment.get("references", {}).values():
        inspect_content(reference.get("href", ""))
        _validate_link(reference.get("href", ""))
    return markers


def _validate_link(value: str, *, image: bool = False) -> None:
    decoded = unescape(value)
    for _ in range(4):
        decoded = unquote(decoded)
    decoded = re.sub(r"[\x00-\x20\x7f]", "", decoded)
    try:
        scheme = urlsplit(decoded).scheme.lower()
    except ValueError as exc:
        raise CitationValidationError("Malformed answer link.") from exc
    if scheme not in {"", "http", "https", "mailto"}:
        if not (image and re.match(r"^data:image/(?:png|jpeg|gif|webp);base64,", decoded, re.I)):
            raise CitationValidationError("Active answer link schemes are not permitted.")


class _PassiveHtml(HTMLParser):
    # Preserve ordinary formatting/external links but never let model HTML
    # execute code that constructs a different download URL after validation.
    _tags = frozenset("a p br hr b strong em i u s del code pre kbd sub sup div span ul ol li blockquote table thead tbody tr th td h1 h2 h3 h4 h5 h6 img details summary".split())

    def handle_starttag(self, tag, attrs):
        if tag not in self._tags:
            raise CitationValidationError("Active or unsupported answer HTML is not permitted.")
        allowed = {"title"}
        if tag == "a":
            allowed |= {"href"}
        if tag == "img":
            allowed |= {"src", "alt", "width", "height"}
        for name, value in attrs:
            if name not in allowed:
                raise CitationValidationError("Active or unsupported answer HTML attributes are not permitted.")
            if name in {"href", "src"}:
                _validate_link(value or "", image=name == "src")

    def handle_endtag(self, tag):
        if tag not in self._tags:
            raise CitationValidationError("Active or unsupported answer HTML is not permitted.")


def _validate_html(fragment: str) -> None:
    parser = _PassiveHtml(convert_charrefs=True)
    parser.feed(fragment)
    parser.close()


def _reserved_route(text: str) -> bool:
    # Normalize nested entity/percent escapes, Markdown punctuation escaping,
    # browser-ignored ASCII controls, and dot segments before route detection.
    decoded = text
    for _ in range(16):
        normalized = unquote(unescape(decoded))
        if normalized == decoded:
            break
        decoded = normalized
    else:
        raise CitationValidationError("Over-encoded answer link.")
    decoded = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", decoded)
    decoded = re.sub(r"[\x00-\x20\x7f]", "", decoded).replace("\\", "/")
    decoded = re.sub(r"/+", "/", decoded)
    previous = None
    while previous != decoded:
        previous = decoded
        decoded = decoded.replace("/./", "/")
        decoded = re.sub(r"/[^/<>\"'()]+/\.\./", "/", decoded)
    return re.search(r"api/knowledge/v1(?:[/#?]|$)", decoded, re.I) is not None


def _section_footer_citations(answer: str, marker_lines: dict[int, list[str]]) -> str:
    """Move only an unambiguous heading-leading evidence block below its body.

    Citation identities and claim text are unchanged. Real Markdown boundaries
    prevent code examples or nested headings from assigning sources to a different
    section. Interleaved evidence is intentionally not regrouped or deduplicated.
    """
    if not marker_lines:
        return answer
    lines = answer.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark").enable(["table", "strikethrough"]).parse(answer)
    boundaries = [token for token in tokens
                  if token.level == 0 and token.type in {"heading_open", "hr"} and token.map]
    paragraph_lines = {line for token in tokens if token.type == "paragraph_open"
                       and token.level == 0 and token.map for line in range(*token.map)}
    replacements = []
    for index, boundary in enumerate(boundaries):
        if boundary.type != "heading_open":
            continue
        start = boundary.map[1]
        end = boundaries[index + 1].map[0] if index + 1 < len(boundaries) else len(lines)
        cursor = start
        leading = []
        while cursor < end:
            if not lines[cursor].strip():
                cursor += 1
                continue
            if (cursor in marker_lines and cursor in paragraph_lines
                    and re.fullmatch(r"\s*근거 문서:\s*(.*?)\s*", lines[cursor])):
                leading.append(cursor)
                cursor += 1
                continue
            break
        if (not leading or cursor == end
                or {line for line in marker_lines if start <= line < end} != set(leading)):
            continue
        tail = end
        while tail > cursor and not lines[tail - 1].strip():
            tail -= 1
        if tail == cursor:
            continue
        newline = "\r\n" if lines[boundary.map[0]].endswith("\r\n") else "\n"
        body = "".join(lines[cursor:tail])
        if not body.endswith("\n"):
            body += newline
        evidence = newline.join(lines[line].rstrip("\r\n") for line in leading)
        if lines[end - 1].endswith("\n"):
            evidence += newline
        replacement = newline + body + newline + evidence + "".join(lines[tail:end])
        replacements.append((start, end, replacement))
    for start, end, replacement in reversed(replacements):
        lines[start:end] = [replacement]
    return "".join(lines)


def render_citations(answer: str, catalog: CitationCatalog) -> tuple[str, dict]:
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 32_000:
        raise CitationValidationError("Invalid canonical answer size.")
    by_handle = {source.handle: source for source in catalog.sources}
    marker_lines = _citation_lines(answer)
    normalized = _section_footer_citations(answer, marker_lines)
    if normalized != answer:
        answer = normalized
        marker_lines = _citation_lines(answer)
    cited: list[str] = []
    rendered = []
    occurrences = []
    for line_index, line in enumerate(answer.splitlines(keepends=True)):
        if line_index not in marker_lines:
            rendered.append(line)
            continue
        evidence = re.fullmatch(r"\s*근거 문서:\s*(.*?)\s*", line)
        if evidence is None:
            raise CitationValidationError("Citation markers must occur on an evidence line.")
        markers = re.findall(r"\[\[(?:wiki:)?([^\]\s]+)\]\]", evidence[1])
        remaining = re.sub(r"\[\[(?:wiki:)?[^\]\s]+\]\]", "", evidence[1])
        if (not markers or markers != marker_lines[line_index] or remaining.strip(" ,;·")
                or any(handle not in by_handle for handle in markers)):
            raise CitationValidationError("Unknown or malformed citation reference.")
        unique = tuple(dict.fromkeys(markers))
        for handle in unique:
            if handle not in cited:
                cited.append(handle)
        occurrences.append({"line": line_index + 1, "handles": list(unique)})
        rendered.append("근거 문서: " + " / ".join(_render_source(by_handle[handle], catalog) for handle in unique)
                        + ("\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""))
    text = "".join(rendered)
    if len(text) > 32_000:
        raise CitationValidationError("Rendered canonical answer exceeds its size bound.")
    return text, {
        "schema_version": 1,
        "status": "resolved" if cited else "uncited" if catalog.sources else "no_wiki_body",
        "claim_support": "not_semantically_verified",
        "occurrences": occurrences,
        "sources": [
            {"handle": handle, "item_id": by_handle[handle].item_id,
             "revision_id": by_handle[handle].revision_id,
             "source_space_id": by_handle[handle].source_space_id,
             "delivered_body_sha256": [sha256(text.encode()).hexdigest() for text in by_handle[handle].passages],
             "artifact_ids": [artifact.artifact_id for artifact in by_handle[handle].artifacts],
             "artifacts": [{"artifact_id": artifact.artifact_id,
                            "source_revision_id": artifact.source_revision_id,
                            "content_sha256": artifact.content_sha256}
                           for artifact in by_handle[handle].artifacts]}
            for handle in cited
        ],
    }
