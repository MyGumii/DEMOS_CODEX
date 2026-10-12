"""Conservative source navigation for a future writing policy.

Navigation is not an evidence selector: rejected/capped headings never remove
source prose. Historical writing policies keep their existing outline bytes.
"""
import re


_NUMBERED = re.compile(r"^(?P<number>\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)(?P<dot>\.)?\s+(?P<title>.{4,110})$")
_NAMED = re.compile(r"^(?:abstract|introduction|conclusions?|references|bibliography|acknowledgements?|초록|서론|결론|참고문헌)$", re.I)
_APPENDIX = re.compile(r"^appendix\s+[A-Z](?:\.\d+)*(?:\s*[:—-]\s*|\s+)(.{4,100})$", re.I)
_MATH = re.compile(r"\(cid:\d+\)|[=<>≤≥∈∉∀∃∑∏∫{}\\_^]|[Α-Ωα-ω]")


def _title_like(title):
    if (_MATH.search(title) or title[-1:] in ".,;:-" or not title[0].isalpha()
            or (title[0].isascii() and title[0].islower())):
        return False
    return True


def outline(document, limit=64):
    """Keep title-shaped lines, using document numbering style, not topics."""
    if limit is not None and (type(limit) is not int or limit < 2):
        raise ValueError("Navigation limit must be at least two")
    candidates, seen = [], set()
    bare_roots = set()
    hierarchy_count = 0
    for page in document.pages:
        for block in page.blocks:
            for raw in getattr(block, "text", "").splitlines():
                line = raw.strip()
                if not line or line in seen:
                    continue
                named = _NAMED.fullmatch(line)
                appendix = _APPENDIX.fullmatch(line)
                numbered = _NUMBERED.fullmatch(line)
                if named:
                    style = "named"
                elif appendix and _title_like(appendix[1]):
                    style = "named"
                elif numbered and _title_like(numbered["title"]):
                    number = numbered["number"]
                    if "." in number:
                        hierarchy_count += 1
                        style = "nested"
                    elif numbered["dot"]:
                        style = "dotted_root"
                    else:
                        bare_roots.add(number)
                        style = "bare_root"
                else:
                    continue
                candidates.append(({"page":page.number,"heading":line},style))
                seen.add(line)
    # In a document with bare major headings and nested sections, a separate
    # 1./2./3. list is not part of its heading hierarchy. Do not infer topics.
    keep_dotted = not (len(bare_roots) >= 2 and hierarchy_count >= 2)
    rows = [row for row,style in candidates if keep_dotted or style != "dotted_root"]
    if limit is None or len(rows) <= limit:
        return rows
    # Cover the entire document rather than spending the cap on its first half.
    # This limits navigation only, never source sections or page image tasks.
    indices = [i*(len(rows)-1)//(limit-1) for i in range(limit)]
    return [rows[i] for i in indices]


def context_outline(rows, source_pages, *, include_chapters=True):
    """Whole-document chapter map plus current sections/ancestors only.

    All full outline entries remain available to the plan/archive. This is
    merely a compact navigation view; it never selects or truncates evidence.
    """
    pages=set(source_pages)
    if not pages:
        return []
    first=min(pages)
    selected=set()
    numbers={}
    roots=[]
    for i,row in enumerate(rows):
        match=_NUMBERED.fullmatch(row["heading"])
        if match:
            number=match["number"]
            numbers.setdefault(number,i)
            if "." not in number:
                roots.append(i)
        elif _NAMED.fullmatch(row["heading"]):
            roots.append(i)
        if row["page"] in pages:
            selected.add(i)
    # An overview of every ordinary document chapter; unusually large maps
    # are evenly sampled as navigation rather than silently hiding their end.
    if len(roots)>16:
        roots=[roots[i*(len(roots)-1)//15] for i in range(16)]
    if include_chapters:
        selected.update(roots)
    preceding=[i for i,row in enumerate(rows) if row["page"]<first]
    if preceding:
        selected.add(preceding[-1])
    for i in tuple(selected):
        match=_NUMBERED.fullmatch(rows[i]["heading"])
        if match:
            parts=match["number"].split(".")
            for depth in range(1,len(parts)):
                index=numbers.get(".".join(parts[:depth]))
                if index is not None:
                    selected.add(index)
    return [row for i,row in enumerate(rows) if i in selected]


def source_heading_positions(rows, source_sections):
    """Locate actual title lines inside complete, already-owned source text.

    Offsets are local Unicode code points, not excerpts or inferred claims.
    Unrecognized headings remain in source text. A same-title mention within
    a sentence is not a heading boundary, and no source is removed/reordered.
    """
    titles={}
    for row in rows:
        titles.setdefault(row['page'],set()).add(row['heading'])
    positions=[]
    for index,section in enumerate(source_sections):
        offset=0
        for raw in section['text'].splitlines(keepends=True):
            if raw.strip() in titles.get(section['page'],set()):
                positions.append({'source_section_index':index,'page':section['page'],
                    'offset':offset,'heading':raw.strip()})
            offset+=len(raw)
    return positions


def source_section_topics(rows, chunks, selected_ordinals):
    """Name a range's preceding physical heading, never infer its meaning.

    Walk the complete ordered source, not just this batch: text at the start
    of a later page can continue a heading on an earlier page. A following
    heading must not be assigned backwards to that leading continuation.
    Unknown headings stay unknown; source text remains authoritative/intact.
    """
    if (tuple(selected_ordinals) != tuple(sorted(set(selected_ordinals)))
            or any(type(n) is not int or n < 1 for n in selected_ordinals)):
        raise ValueError('Selected source ranges must be unique and ordered')
    selected = set(selected_ordinals)
    titles = {}
    for row in rows:
        titles.setdefault(row['page'], set()).add(row['heading'])
    current = None
    result = []
    for ordinal, chunk in enumerate(chunks, 1):
        lines = [line.strip() for line in chunk.text.splitlines() if line.strip()]
        starts_topic = bool(lines and lines[0] in titles.get(chunk.page, set()))
        if starts_topic:
            current = lines[0]
        if ordinal in selected:
            result.append({'source_section_index': len(result), 'page': chunk.page,
                'source_heading': current, 'continues_topic': not starts_topic})
        # Include later headings only for SUBSEQUENT ranges. A source range
        # may be oversized/split, and unpartitioned test sources may contain
        # more than one heading. This metadata is navigation, not scope proof.
        for line in lines:
            if line in titles.get(chunk.page, set()):
                current = line
    if len(result) != len(selected_ordinals):
        raise ValueError('Unknown or repeated selected source range ordinal')
    return result


def source_topic_groups(topics):
    """Compact adjacent same-heading ranges without compacting their prose."""
    groups = []
    for row in topics:
        if (groups and row['continues_topic']
                and groups[-1]['heading'] == row['source_heading']):
            groups[-1]['section_indices'].append(row['source_section_index'])
        else:
            groups.append({'heading':row['source_heading'],
                'section_indices':[row['source_section_index']],
                'starts_here':not row['continues_topic']})
    return groups


def owned_topic_ranges(text, byte_limit, ranges, headings):
    """Partition at recognized physical topic titles, keeping EVERY code point.

    Unlike the historical broad numeric-heading matcher, formulas, numbered
    lists and cross-references do not create accidental miniature tasks. The
    caller's original byte-limited splitter still handles oversized topics.
    A page's leading continuation is its own range, not the next topic's prefix.
    """
    cuts=[0]
    offset=0
    for raw in text.splitlines(keepends=True):
        if offset and raw.strip() in headings:
            cuts.append(offset)
        offset+=len(raw)
    cuts.append(len(text))
    for first,last in zip(cuts,cuts[1:]):
        for start,end in ranges(text[first:last],byte_limit):
            yield first+start,first+end
