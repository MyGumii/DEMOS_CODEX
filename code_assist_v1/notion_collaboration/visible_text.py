"""Schema-aware search text. Never traverse arbitrary editor configuration."""
from collections.abc import Mapping
from html.parser import HTMLParser

CONTRACT = "code_assist.notion.visible_search_text.v1"
MAX_CHARS = 2_000_000
DATABASE_TYPES = frozenset({"table", "board", "list", "gallery", "calendar", "timeline",
                            "chart", "dashboard", "form", "feed", "map"})


def _object(value):
    return value if isinstance(value, Mapping) else {}


def _array(value):
    return value if isinstance(value, (list, tuple)) else ()


def _plain(value):
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


class _HtmlText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "div", "p", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"div", "p", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(value):
    parser = _HtmlText()
    parser.feed(_plain(value))
    parser.close()
    return "".join(parser.parts)


def _join(parts, limit):
    result, size = [], 0
    for value in parts:
        clean = " ".join(_plain(value).split())
        if not clean:
            continue
        remaining = limit - size - (1 if result else 0)
        if remaining <= 0:
            break
        chunk = clean[:remaining]
        result.append(chunk)
        size += len(chunk) + (1 if len(result) > 1 else 0)
    return "\n".join(result)


def _cell_parts(value, kind):
    if kind in {"person", "relation"}:
        # These values are identifiers. Their labels require a caller-specific
        # authorized lookup, and must not enter a workspace-wide text index.
        return
    if kind == "files":
        for file in _array(value):
            file = _object(file)
            yield file.get("name") or file.get("original_name")
    elif kind == "checkbox":
        if isinstance(value, bool):
            yield "체크됨" if value else "체크 안 됨"
    elif kind == "date" and isinstance(value, Mapping):
        yield value.get("start")
        yield value.get("end")
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield _object(item).get("name") if isinstance(item, Mapping) else _plain(item)
    else:
        yield _plain(value)


def _database_parts(database, *, canonical):
    yield database.get("title")
    properties = [_object(item) for item in _array(database.get("properties"))]
    for prop in properties:
        yield prop.get("name")
    for row in _array(database.get("rows")):
        row = _object(row)
        cells = _object(row.get("cells")) if canonical else row
        for prop in properties:
            yield from _cell_parts(cells.get(prop.get("id")), prop.get("type", "text"))
    for view in _array(database.get("views")):
        view = _object(view)
        if view.get("state") != "archived":
            yield view.get("name")


def _block_parts(block, databases):
    kind = block.get("type", "paragraph")
    props = _object(block.get("props"))
    if kind in {"divider", "table_of_contents", "breadcrumb"}:
        return
    if kind in DATABASE_TYPES:
        database = databases.get(str(props.get("database_id") or block.get("id") or ""))
        if database is not None:
            yield from _database_parts(database, canonical=True)
        else:
            yield block.get("content")
            yield from _database_parts(props, canonical=False)
        return
    if kind == "simple_table":
        for row in _array(props.get("rows")):
            for cell in _array(row):
                yield _plain(cell)
        return
    if kind == "button":
        yield props.get("label")
        return
    if kind == "tabs":
        for tab in _array(props.get("tabs")):
            yield _object(tab).get("name")
        return
    if kind == "ai_notes":
        for field in ("transcript", "notes", "translation_text"):
            yield props.get(field)
        result = _object(props.get("result"))
        yield result.get("summary")
        for decision in _array(result.get("decisions")):
            yield _object(decision).get("text")
        for action in _array(result.get("action_items")):
            for field in ("task", "owner", "due"):
                yield _object(action).get(field)
        return
    # content is plain text in the editor; only the explicit rich-text field
    # contains markup. Literal code, angle brackets, numbers and UUIDs survive.
    if props.get("rich_text_html") and kind not in {"code", "equation"}:
        yield html_text(props["rich_text_html"])
    else:
        yield _plain(block.get("content"))
    if kind == "code":
        yield props.get("caption")
    if kind in {"image", "file", "video", "audio", "pdf"}:
        yield props.get("name")
        yield props.get("comment")
    if kind in {"bookmark", "embed"} and props.get("embed_mode") != "html":
        yield props.get("url") or props.get("link_url")


def block_text(block, *, limit=MAX_CHARS):
    return _join(_block_parts(_object(block), {}), max(0, limit))


def document_text(descriptor, *, limit=MAX_CHARS):
    databases = {str(item.get("id")): item for item in _array(descriptor.get("databases"))
                 if isinstance(item, Mapping) and item.get("id")}
    return _join((part for block in _array(descriptor.get("blocks"))
                  if isinstance(block, Mapping)
                  for part in _block_parts(block, databases)), max(0, limit))
