"""Build independent copy identities from a prepared canonical source.

This stage is pure: it creates no documents, grants or upload sessions. The
writer must revalidate the attached source witness before inserting
this plan. Legacy source adoption remains separate from explicit references.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote, unquote, unquote_plus, urlsplit, urlunsplit

from .canonical_authority import _canonical_json, _operation_id, _stable_id
from .database_features import _formula_property_ids
from .page_copy_source import CopySourceDocument, MAX_SOURCE_BYTES, PreparedPageCopy
from .synced_blocks import SyncedShapeUnavailable, group_id, origin_of
from .linked_databases import LinkedDatabaseShapeUnavailable, source_of as linked_source_of
from . import page_copy_rich

CONTRACT = "code_assist.notion.page_copy_remap.v1"
MAX_PLAN_BYTES = MAX_SOURCE_BYTES
DATABASE_BLOCKS = frozenset({"table", "board", "list", "gallery", "calendar", "timeline",
    "chart", "dashboard", "form", "feed", "map"})
VIEW_PROPERTIES = ("group_property", "timeline_date_property", "board_group_property",
    "calendar_date_property", "chart_group_property", "chart_measure", "feed_date_property", "map_place_property")
_ATTR = re.compile(r'''\s+([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')
_FILE_URL = re.compile(r"/api/notion/collaboration/files/([^/?#\"'<>&\s]+)")


class CopyShapeUnavailable(ValueError):
    """A copy shape requires another implementation/parity packet."""


@dataclass(frozen=True)
class CopyDocumentPlan:
    source_document_id: str
    document_id: str
    parent_id: str | None
    position: int
    descriptor_json: str
    files_json: str
    rich_text_json: str = ''

    def descriptor(self):
        return json.loads(self.descriptor_json)


@dataclass(frozen=True)
class PageCopyPlan:
    source: PreparedPageCopy
    operation_id: str
    documents: tuple[CopyDocumentPlan, ...]


@dataclass(frozen=True)
class OwnedDatabaseCopyPlan:
    source_document_id: str
    source_database_id: str
    operation_id: str
    block_json: str
    database_json: str
    files_json: str

    def block(self):
        return json.loads(self.block_json)

    def database(self):
        return json.loads(self.database_json)


class _RichLinks(HTMLParser):
    """Replace link attribute values without reserializing text or formatting."""
    def __init__(self, markup, rewrite):
        super().__init__(convert_charrefs=False)
        self.markup, self.rewrite, self.edits = markup, rewrite, []
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", markup))

    def handle_starttag(self, tag, attrs):
        if tag not in {"a", "img", "audio", "video", "source"}:
            return
        line, column = self.getpos()
        offset = self.lines[line - 1] + column
        for match in _ATTR.finditer(self.get_starttag_text()):
            if match[1].lower() not in ({"href"} if tag == "a" else {"src"}):
                continue
            group = next((i for i in (2, 3, 4) if match[i] is not None), None)
            if group is None:
                continue
            old = unescape(match[group])
            new = self.rewrite(old)
            if new != old:
                self.edits.append((offset + match.start(group), offset + match.end(group), escape(new, quote=True)))

    handle_startendtag = handle_starttag

    def result(self):
        self.feed(self.markup)
        self.close()
        result = self.markup
        for start, end, replacement in reversed(self.edits):
            result = result[:start] + replacement + result[end:]
        return result


class _Remapper:
    def __init__(self, source, operation, local_origin, *, partial_documents=False):
        self.source, self.operation = source, operation
        parsed = urlsplit(local_origin) if local_origin else None
        if parsed and (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("a trusted application origin is required")
        self.origin = (parsed.scheme.lower(), parsed.netloc.lower()) if parsed else None
        self.ids, self.databases, self.synced = {}, {}, {}
        self.block_scopes = {}
        self.partial_documents = partial_documents
        self.database_documents, self.linked = {}, {}
        self.documents = {item.document_id for item in source.documents}
        self.body_documents, self.row_scopes = {}, {}
        self.formula_bytes = 0
        for document in source.documents:
            self.allocate("p", "", document.document_id)
            descriptor = document.descriptor()
            self.scan_blocks(document.document_id, descriptor["blocks"])
            for database in descriptor.get("databases", []):
                database_id = database["id"]
                if database_id in self.databases:
                    raise CopyShapeUnavailable("duplicate database identity")
                self.databases[database_id] = database
                self.database_documents[database_id] = document.document_id
                self.allocate("db", "", database_id)
                for kind, key in (("prop", "properties"), ("row", "rows"), ("view", "views"), ("template", "templates")):
                    for ordinal, item in enumerate(database.get(key, [])):
                        if item.get("state") == "archived":
                            continue
                        identity = self.allocate(kind, database_id, item["id"])
                        if kind == "template":
                            # Templates are read by creation time then ID, with
                            # no position column. Copies share a creation time;
                            # an ordinal prefix preserves their visible order.
                            self.ids[(kind, database_id, item["id"])] = f"template_{ordinal:06d}_{identity.removeprefix('template_')}"
                            scope = self.template_scope(document.document_id, item["id"])
                            self.body_documents[scope] = document.document_id
                            self.scan_blocks(scope, item.get("body", []))
                        elif kind == "row":
                            scope = self.row_scope(document.document_id, database_id, item["id"])
                            self.body_documents[scope] = document.document_id
                            self.row_scopes[(database_id, item["id"])] = scope
                            if "body" in item:
                                body = item["body"]
                                if not isinstance(body, dict) or set(body) != {"blocks"} or not isinstance(body["blocks"], list):
                                    raise CopyShapeUnavailable("typed database item body required")
                                self.scan_blocks(scope, body["blocks"])
            for file in json.loads(document.files_json):
                self.allocate("file", "", file["id"])
        for (scope, _block_id), block in self.synced.items():
            try:
                owner = self.body_documents.get(scope, scope)
                if len(self.block_scopes.get((owner, _block_id), [])) != 1:
                    raise SyncedShapeUnavailable("ambiguous synced body identity")
                target = origin_of(block, owner)
                if target[0] in self.documents:
                    target_scopes = self.block_scopes.get(target, [])
                    if len(target_scopes) > 1:
                        raise SyncedShapeUnavailable("ambiguous synced body identity")
                    original = self.synced.get((target_scopes[0], target[1])) if target_scopes else None
                    if original is None and partial_documents and not target_scopes:
                        continue  # A selected subtree need not own the whole page.
                    if original is None or original.get("props", {}).get("synced_from") is not None:
                        raise SyncedShapeUnavailable("a copied synced reference must target an original")
            except SyncedShapeUnavailable as exc:
                raise CopyShapeUnavailable(str(exc)) from exc
        for block in self.linked.values():
            source_document, database_id = linked_source_of(block)
            if source_document in self.documents or database_id in self.databases:
                if database_id not in self.databases and partial_documents:
                    continue
                if self.database_documents.get(database_id) != source_document:
                    raise CopyShapeUnavailable("linked source does not match its owned database")

    @staticmethod
    def template_scope(document_id, template_id):
        return document_id + "/template/" + template_id

    @staticmethod
    def row_scope(document_id, database_id, row_id):
        return document_id + "/database/" + database_id + "/row/" + row_id

    @staticmethod
    def tab_scope(scope, controller_id):
        return scope + "/tabs/" + controller_id

    @staticmethod
    def linked_scope(document_id, block_id):
        return document_id + "/linked/" + block_id

    def allocate(self, kind, scope, old):
        if not isinstance(old, str) or not old:
            raise CopyShapeUnavailable("copy source identity is missing")
        key = (kind, scope, old)
        self.ids.setdefault(key, _stable_id(kind, CONTRACT, self.source.actor,
            self.source.workspace_id, self.operation, scope, old))
        return self.ids[key]

    def ref(self, kind, scope, old):
        return self.ids.get((kind, scope, old), old) if isinstance(old, str) else old

    def scan_blocks(self, scope, blocks, depth=0):
        if depth > 64 or not isinstance(blocks, list):
            raise CopyShapeUnavailable("invalid copied child hierarchy")
        for block in blocks:
            if not isinstance(block, dict) or not block.get("id"):
                raise CopyShapeUnavailable("typed template/child block required")
            key = ("block", scope, block["id"])
            if key in self.ids:
                raise CopyShapeUnavailable("duplicate block identity in one body")
            self.allocate(*key)
            owner = self.body_documents.get(scope, scope)
            self.block_scopes.setdefault((owner, block["id"]), []).append(scope)
            props = block.get("props", {})
            if "linked_database" in props:
                try:
                    linked_source_of(block)
                except LinkedDatabaseShapeUnavailable as exc:
                    raise CopyShapeUnavailable(str(exc)) from exc
                self.linked[(scope, block["id"])] = block
                for view in props["views"]:
                    self.allocate("localview", self.linked_scope(scope, block["id"]), view["id"])
            if block.get("type") == "synced_block":
                if scope not in self.documents and self.partial_documents:
                    raise CopyShapeUnavailable("partial database synced copying needs a complete body identity witness")
                self.synced[(scope, block["id"])] = block
            if props.get("layout_group"):
                self.allocate("layout", scope, props["layout_group"])
            for tab in props.get("tabs", []) if block.get("type") == "tabs" else []:
                key = ("tab", self.tab_scope(scope, block["id"]), tab["id"])
                if key in self.ids:
                    raise CopyShapeUnavailable("duplicate tab identity in one controller")
                self.allocate(*key)
            children = props.get("children", [])
            if children and all(isinstance(child, dict) for child in children):
                self.scan_blocks(scope, children, depth + 1)
            elif children and not all(isinstance(child, str) for child in children):
                raise CopyShapeUnavailable("mixed nested block representation")

    def url(self, value, scope):
        if not isinstance(value, str) or not value:
            return value
        try:
            parsed = urlsplit(value)
        except ValueError:
            return value
        if parsed.scheme or parsed.netloc:
            if not self.origin or (parsed.scheme.lower(), parsed.netloc.lower()) != self.origin:
                return value
        path, fragment, query = parsed.path, parsed.fragment, parsed.query
        target = scope if not parsed.scheme and not parsed.netloc else None
        if path.startswith("/page/") and "/" not in path[len("/page/"):]:
            target = unquote(path[len("/page/"):])
            mapped = self.ref("p", "", target)
            if mapped == target and ("p", "", target) not in self.ids:
                return value
            # An explicit same-page target still needs independent row/block
            # anchors when this remapper is used for a partial detach.
            path = "/page/" + quote(mapped, safe="")
        elif path.startswith("/api/notion/collaboration/files/"):
            old = unquote(path[len("/api/notion/collaboration/files/"):])
            mapped = self.ref("file", "", old)
            if mapped == old:
                return value
            path = "/api/notion/collaboration/files/" + quote(mapped, safe="")
            target = None
        elif path:
            return value
        # Item links contain a second identity boundary inside the page. Keep
        # unrelated query bytes and external items intact instead of changing
        # only their page path and leaving them pointed at the original row.
        segments = query.split("&") if query else []
        fields = {}
        for segment in segments:
            key, _, query_value = segment.partition("=")
            key = unquote_plus(key)
            if key in {"item_db", "item_row"}:
                fields.setdefault(key, []).append(unquote_plus(query_value))
        if fields and target is not None:
            if set(fields) != {"item_db", "item_row"} or any(len(values) != 1 for values in fields.values()):
                return value
            database_id, row_id = fields["item_db"][0], fields["item_row"][0]
            document_id = self.body_documents.get(target, target)
            row_scope = self.row_scopes.get((database_id, row_id))
            if self.database_documents.get(database_id) != document_id or row_scope is None:
                return value
            replacements = {"item_db": self.ref("db", "", database_id), "item_row": self.ref("row", database_id, row_id)}
            rewritten = []
            for segment in segments:
                key, _, _value = segment.partition("=")
                name = unquote_plus(key)
                rewritten.append(key + "=" + quote(replacements[name], safe="") if name in replacements else segment)
            query, target = "&".join(rewritten), row_scope
        if target is not None and fragment.startswith("block-"):
            old = unquote(fragment[6:])
            mapped = self.ref("block", target, old)
            if mapped != old:
                fragment = "block-" + quote(mapped, safe="")
        changed = urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))
        return changed if (path, query, fragment) != (parsed.path, parsed.query, parsed.fragment) else value

    def formula(self, config, database_id):
        # Resolve identity only at prop() calls, including dynamic arguments.
        # The expression and ordinary string-valued cells remain byte-for-byte
        # unchanged. Flatten earlier copy mappings when a copy is copied again.
        previous = _formula_property_ids(config.get("copy_property_ids", {}))
        identities = {item["id"]: self.ref("prop", database_id, item["id"])
            for item in self.databases[database_id]["properties"]}
        identities.update({old: self.ref("prop", database_id, target) for old, target in previous.items()})
        # Avoid quadratic allocation across many formula properties before the
        # final descriptor serialization can enforce the whole-plan budget.
        self.formula_bytes += len(_canonical_json(identities).encode("utf-8"))
        if self.formula_bytes > MAX_PLAN_BYTES:
            raise ValueError("page copy reference plan exceeds the preparation budget")
        config["copy_property_ids"] = identities

    def cell(self, value, property, scope):
        if property["type"] == "relation" and isinstance(value, list):
            target = property.get("config", {}).get("target_database_id", "")
            return [self.ref("row", target, item) for item in value]
        if property["type"] == "files" and isinstance(value, list):
            result = deepcopy(value)
            for item in result:
                if isinstance(item, dict):
                    if "file_id" in item:
                        item["file_id"] = self.ref("file", "", item["file_id"])
                    if "url" in item:
                        item["url"] = self.url(item["url"], scope)
            return result
        return deepcopy(value)

    def values(self, values, database, scope):
        properties = {item["id"]: item for item in database["properties"]}
        if any(key not in properties for key in values):
            raise CopyShapeUnavailable("cell/default references an unknown property")
        return {self.ref("prop", database["id"], key): self.cell(value, properties[key], scope)
            for key, value in values.items()}

    def view(self, config, database_id, scope):
        result = deepcopy(config)
        for key in VIEW_PROPERTIES:
            if key in result:
                result[key] = self.ref("prop", database_id, result[key])
        for key in ("property_visibility",):
            if isinstance(result.get(key), dict):
                result[key] = {self.ref("prop", database_id, k): v for k, v in result[key].items()}
        for key in ("form_required_properties", "property_order"):
            if isinstance(result.get(key), list):
                result[key] = [self.ref("prop", database_id, item) for item in result[key]]
        properties = {item["id"]: item for item in self.databases[database_id]["properties"]}
        for key in ("filters", "sorts"):
            for item in result.get(key, []):
                old = item.get("property")
                if old in properties:
                    item["property"] = self.ref("prop", database_id, old)
                    if key == "filters" and "value" in item:
                        item["value"] = self.cell(item["value"], properties[old], scope)
        def remap_filter_node(node):
            if not isinstance(node, dict):
                return
            if isinstance(node.get("children"), list):
                for child in node["children"]:
                    remap_filter_node(child)
                return
            old = node.get("property")
            if old in properties:
                node["property"] = self.ref("prop", database_id, old)
                if "value" in node:
                    node["value"] = self.cell(node["value"], properties[old], scope)
        remap_filter_node(result.get("filter_group"))
        return result

    def blocks(self, blocks, scope):
        result = deepcopy(blocks)
        for block in result:
            original_id = block["id"]
            block["id"] = self.ref("block", scope, original_id)
            props = block.setdefault("props", {})
            if block["type"] == "synced_block":
                owner = self.body_documents.get(scope, scope)
                origin_document, origin_block = origin_of(self.synced[(scope, original_id)], owner)
                target_scopes = self.block_scopes.get((origin_document, origin_block), [])
                # A partial plan may not contain the original. Its entire
                # endpoint stays external, including the original page ID.
                mapped_document, mapped_block = origin_document, origin_block
                if target_scopes:
                    mapped_document = self.ref("p", "", origin_document)
                    mapped_block = self.ref("block", target_scopes[0], origin_block)
                props["sync_id"] = group_id(mapped_document, mapped_block)
                if props["synced_from"] is not None:
                    props["synced_from"] = {"document_id": mapped_document, "block_id": mapped_block}
            if "rich_text_html" in props:
                props["rich_text_html"] = _RichLinks(props["rich_text_html"], lambda url: self.url(url, scope)).result()
            for key in ("url", "src"):
                if key in props:
                    props[key] = self.url(props[key], scope)
            if block["type"] in {"child_page", "link_to_page"} and "page_id" in props:
                props["page_id"] = self.ref("p", "", props["page_id"])
            if "file_id" in props:
                props["file_id"] = self.ref("file", "", props["file_id"])
            controller = props.get("tab_group")
            if controller and "tab_id" in props:
                props["tab_id"] = self.ref("tab", self.tab_scope(scope, controller), props["tab_id"])
            for key, kind in (("layout_group", "layout"), ("tab_group", "block")):
                if key in props:
                    props[key] = self.ref(kind, scope, props[key])
            if block["type"] == "tabs":
                tab_scope = self.tab_scope(scope, original_id)
                if "active_tab_id" in props:
                    props["active_tab_id"] = self.ref("tab", tab_scope, props["active_tab_id"])
                for tab in props.get("tabs", []):
                    tab["id"] = self.ref("tab", tab_scope, tab["id"])
            if props.get("children"):
                props["children"] = (self.blocks(props["children"], scope) if isinstance(props["children"][0], dict)
                    else [self.ref("block", scope, child) for child in props["children"]])
            database_id = props.get("database_id", original_id)
            if "linked_database" in props:
                source_document, database_id = linked_source_of(self.linked[(scope, original_id)])
                view_scope = self.linked_scope(scope, original_id)
                props["linked_database"] = {"document_id": self.ref("p", "", source_document),
                    "database_id": self.ref("db", "", database_id)}
                if "database_id" in props:
                    props["database_id"] = props["linked_database"]["database_id"]
                props["active_view_id"] = self.ref("localview", view_scope, props["active_view_id"])
                for view in props["views"]:
                    view["id"] = self.ref("localview", view_scope, view["id"])
                    if database_id in self.databases:
                        view["config"] = self.view(view["config"], database_id, scope)
                # External sources retain their property/row identities; their
                # data and permissions are neither read nor copied here.
            elif database_id in self.databases:
                props = self.view(props, database_id, scope)
                props["database_id"] = self.ref("db", "", database_id)
                if "active_view_id" in props:
                    props["active_view_id"] = self.ref("view", database_id, props["active_view_id"])
                # These are replaceable UI caches, often older than the canonical
                # DB descriptor. Hydration reconstructs them from copied DB data.
                for key in ("properties", "rows", "views", "templates", "_canonical_version"):
                    props.pop(key, None)
                block["props"] = props
            elif block["type"] in DATABASE_BLOCKS or props.get("database_id"):
                raise CopyShapeUnavailable("linked or unadopted database duplication needs verified parity")
        return result

    def database(self, database, scope):
        result, old_id = deepcopy(database), database["id"]
        result["id"], result["version"] = self.ref("db", "", old_id), 1
        properties = {item["id"]: item for item in database["properties"]}
        if "item_page_layout" in result:
            layout = result["item_page_layout"]
            if not isinstance(layout, dict) or not isinstance(layout.get("value"), dict):
                raise CopyShapeUnavailable("typed database item-page layout required")
            for key in ("page_properties", "panel_properties", "hidden_properties",
                        "empty_hidden_properties", "standalone_properties"):
                if key in {"empty_hidden_properties", "standalone_properties"} and key not in layout["value"]:
                    continue
                if not isinstance(layout["value"].get(key), list):
                    raise CopyShapeUnavailable("database item-page property placement required")
                layout["value"][key] = [self.ref("prop", old_id, item)
                                         for item in layout["value"][key]]
            for section in layout["value"].get("sections", []):
                if not isinstance(section, dict) or not isinstance(section.get("property_ids"), list):
                    raise CopyShapeUnavailable("database item-page sections required")
                section["property_ids"] = [self.ref("prop", old_id, item)
                                           for item in section["property_ids"]]
            layout["version"] = 1
        for position, property in enumerate(result["properties"]):
            property["position"] = position
            property["id"], property["version"] = self.ref("prop", old_id, property["id"]), 1
            config = property.get("config", {})
            if property["type"] == "relation":
                target_database = config["target_database_id"]
                inverse_id = config.get("reciprocal_property_id")
                if inverse_id:
                    if target_database in self.databases:
                        inverse = next((item for item in
                            self.databases[target_database]["properties"]
                            if item["id"] == inverse_id), None)
                        inverse_config = inverse.get("config", {}) if inverse else {}
                        if (inverse is None or inverse["type"] != "relation"
                                or inverse_config.get("target_database_id") != old_id
                                or inverse_config.get("reciprocal_property_id")
                                != database["properties"][position]["id"]):
                            raise CopyShapeUnavailable(
                                "paired relation properties disagree")
                        config["reciprocal_property_id"] = self.ref(
                            "prop", target_database, inverse_id)
                    else:
                        # The other database stays in place. The copied property
                        # is a one-way relation to it, without a dangling pair.
                        config.pop("reciprocal_property_id", None)
                config["target_database_id"] = self.ref("db", "", target_database)
            elif property["type"] == "rollup":
                relation = properties.get(config["relation_property_id"], {})
                target = relation.get("config", {}).get("target_database_id", "")
                config["relation_property_id"] = self.ref("prop", old_id, config["relation_property_id"])
                config["target_property_id"] = self.ref("prop", target, config["target_property_id"])
            elif property["type"] == "formula":
                self.formula(config, old_id)
        for position, row in enumerate(result["rows"]):
            body_scope = self.row_scopes[(old_id, row["id"])]
            row["position"] = position
            row["id"], row["version"] = self.ref("row", old_id, row["id"]), 1
            row["cells"] = self.values(row["cells"], database, scope)
            row["cell_versions"] = {key: 1 for key in row["cells"]}
            row.pop("computed", None)
            if "body" in row:
                row["body"] = {"blocks": self.blocks(row["body"]["blocks"], body_scope)}
            if "appearance" in row:
                appearance = row["appearance"]
                if not isinstance(appearance, dict):
                    raise CopyShapeUnavailable("typed database item appearance required")
                for key in ("icon", "cover"):
                    if key in appearance:
                        appearance[key] = self.image(appearance[key], scope, icon=key == "icon")
        result["views"] = [item for item in result.get("views", []) if item.get("state") != "archived"]
        for position, view in enumerate(result["views"]):
            view["position"] = position
            view["id"], view["version"] = self.ref("view", old_id, view["id"]), 1
            view["config"] = self.view(view.get("config", {}), old_id, scope)
        result["templates"] = [item for item in result.get("templates", []) if item.get("state") != "archived"]
        for template in result["templates"]:
            template_scope = self.template_scope(scope, template["id"])
            template["id"], template["version"] = self.ref("template", old_id, template["id"]), 1
            template["body"] = self.blocks(template.get("body", []), template_scope)
            template["default_values"] = self.values(template.get("default_values", {}), database, template_scope)
        if "default_template" in result:
            selected = result["default_template"].get("template_id")
            result["default_template"] = {
                "template_id": self.ref("template", old_id, selected) if selected else None,
                "version": 1 if selected else 0,
            }
        return result

    def image(self, value, scope, *, icon=False):
        if icon and isinstance(value, str) and value.startswith("nf:"):
            mapped = self.ref("file", "", value[3:])
            if ("file", "", value[3:]) not in self.ids:
                raise CopyShapeUnavailable("copied icon attachment is unavailable")
            return "nf:" + mapped
        return self.url(value, scope)

    def build(self):
        documents, total_bytes = [], 0
        child_positions = {}
        for source in self.source.documents:
            scope, descriptor = source.document_id, source.descriptor()
            descriptor["blocks"] = self.blocks(descriptor["blocks"], scope)
            descriptor["databases"] = [self.database(database, scope) for database in descriptor.get("databases", [])]
            for key in ("icon", "cover"):
                value = descriptor.get(key, "")
                descriptor[key] = self.image(value, scope, icon=key == "icon")
            for file in descriptor.get("files", []):
                file["id"], file["version"] = self.ref("file", "", file["id"]), 1
            files = []
            for original in json.loads(source.files_json):
                # Explicit provenance; never forge/reuse an upload session.
                files.append({"id": self.ref("file", "", original["id"]), "source_file_id": original["id"],
                    "source_document_id": scope, **{key: original[key] for key in
                        ("original_name", "mime_type", "size_bytes", "content_sha256", "blob_relative_path")}})
            descriptor_json, files_json = _canonical_json(descriptor), _canonical_json(files)
            rich_text_json = page_copy_rich.remap(source.rich_text_json, scope, self)
            total_bytes += len(descriptor_json.encode("utf-8")) + len(files_json.encode("utf-8")) + len(rich_text_json.encode('utf-8'))
            if total_bytes > MAX_PLAN_BYTES:
                raise ValueError("page copy plan exceeds the preparation budget")
            position = source.position
            if source.document_id != self.source.source_page_id:
                position = child_positions.get(source.parent_id, 0)
                child_positions[source.parent_id] = position + 1
            documents.append(CopyDocumentPlan(scope, self.ref("p", "", scope),
                self.ref("p", "", source.parent_id), position, descriptor_json, files_json, rich_text_json))
        return PageCopyPlan(self.source, self.operation, tuple(documents))


def remap_page_copy(source: PreparedPageCopy, operation_id: str, *, local_origin="") -> PageCopyPlan:
    """Prepare a copy plan; this is neither a commit nor a current access grant."""
    return _Remapper(source, _operation_id(operation_id), local_origin).build()


def remap_owned_database_copy(source: PreparedPageCopy, database_id: str, operation_id: str,
                              *, local_origin="",
                              include_content=True) -> OwnedDatabaseCopyPlan:
    """Remap one owned DB without changing its parent page identity.

    The caller must prepare and revalidate the source, authorize a destination,
    and commit the block, database, independent file identities, and Yjs delta
    atomically against the same-document file provenance schema.
    This pure plan is deliberately not a lifecycle mutation by itself.
    """
    operation = _operation_id(operation_id)
    if type(include_content) is not bool:
        raise ValueError("database copy options must be booleans")
    if not isinstance(database_id, str) or not database_id:
        raise ValueError("database identity is required")
    if len(source.documents) != 1 or source.documents[0].document_id != source.source_page_id:
        raise CopyShapeUnavailable("one current parent document is required for database copy")
    document = source.documents[0]
    descriptor = document.descriptor()
    databases = [item for item in descriptor.get("databases", []) if item.get("id") == database_id]
    blocks = [item for item in descriptor.get("blocks", []) if item.get("type") in DATABASE_BLOCKS
              and not item.get("props", {}).get("linked_database")
              and item.get("props", {}).get("database_id", item.get("id")) == database_id]
    if len(databases) != 1 or len(blocks) != 1:
        raise CopyShapeUnavailable("one owned database and block are required")
    database = deepcopy(databases[0])
    if not include_content:
        database["rows"] = []
        own_relations = {item["id"] for item in database["properties"]
            if item["type"] == "relation"
            and item.get("config", {}).get("target_database_id") == database_id}
        for template in database["templates"]:
            for property_id in own_relations & template.get("default_values", {}).keys():
                template["default_values"][property_id] = []
        for view in database["views"]:
            config = view.get("config", {})
            config["filters"] = [item for item in config.get("filters", [])
                if item.get("property") not in own_relations]
            def without_own_relations(node):
                if not isinstance(node, dict):
                    return node
                if isinstance(node.get("children"), list):
                    return {**node, "children": [child for item in node["children"]
                        if (child := without_own_relations(item)) is not None]}
                return None if node.get("property") in own_relations else node
            if isinstance(config.get("filter_group"), dict):
                config["filter_group"] = without_own_relations(config["filter_group"])

    def referenced_files(value, field=""):
        if isinstance(value, dict):
            result = set()
            for key, nested in value.items():
                result.update(referenced_files(nested, key))
            return result
        if isinstance(value, list):
            result = set()
            for nested in value:
                result.update(referenced_files(nested, field))
            return result
        if not isinstance(value, str):
            return set()
        result = {value} if field == "file_id" and value else set()
        if field in {"icon", "cover"} and value.startswith("nf:"):
            result.add(value[3:])
        result.update(unquote(match.group(1)) for match in _FILE_URL.finditer(value))
        return result

    block_resources = deepcopy(blocks[0])
    for key in ("properties", "rows", "views", "templates", "_canonical_version"):
        block_resources.get("props", {}).pop(key, None)
    file_ids = referenced_files(block_resources) | referenced_files(database)
    file_records = json.loads(document.files_json)
    available = {item["id"] for item in file_records}
    if not file_ids.issubset(available):
        raise CopyShapeUnavailable("database attachment is unavailable for copying")
    def has_child_page(items):
        return isinstance(items, list) and any(
            isinstance(item, dict) and (item.get("type") == "child_page"
                or has_child_page(item.get("props", {}).get("children", [])))
            for item in items)

    for body in ([row.get("body", {}).get("blocks", []) for row in database.get("rows", [])]
                 + [template.get("body", []) for template in database.get("templates", [])]):
        if has_child_page(body):
            raise CopyShapeUnavailable("database child-page copying needs a subtree contract")
    trimmed = deepcopy(descriptor)
    trimmed["blocks"] = [deepcopy(blocks[0])]
    trimmed["databases"] = [database]
    trimmed["icon"] = trimmed["cover"] = ""
    trimmed["files"] = [item for item in descriptor.get("files", []) if item.get("id") in file_ids]
    single = CopySourceDocument(document.document_id, document.parent_id, document.position,
        document.stamp, _canonical_json(trimmed), _canonical_json(
            [item for item in file_records if item["id"] in file_ids]))
    prepared = PreparedPageCopy(source.actor, source.workspace_id, source.source_page_id,
        source.workspace_stamp, (single,), source.parent_id, source.parent_stamp)
    mapper = _Remapper(prepared, operation, local_origin, partial_documents=True)
    mapper.ids[("p", "", document.document_id)] = document.document_id
    copied = mapper.build().documents[0]
    result = copied.descriptor()
    return OwnedDatabaseCopyPlan(document.document_id, database_id, operation,
        _canonical_json(result["blocks"][0]), _canonical_json(result["databases"][0]), copied.files_json)
