"""Current, authorized document data for a passive search preview."""
import json
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .canonical_authority import NotFoundOrDenied, _identifier
from .iam_authority import CollaborativeIAMAuthority
from .search_projection import materialize
from .title_projection import (
    TitleProjectionChanged, _frames, _generation, read_current_titles, retry_title_projection,
)

CONTRACT = "code_assist.notion.search_preview.v1"


def _source(authority, con, actor, workspace_id, document_id):
    workspace = authority._workspace(con, workspace_id)
    row = con.execute(
        "SELECT * FROM workspace_collaboration_documents WHERE id=? AND workspace_id=? AND archived=0",
        (document_id, workspace_id),
    ).fetchone()
    role, _ = CollaborativeIAMAuthority._resolution_on_connection(con, actor, workspace_id, document_id)
    if row is None or role is None:
        raise NotFoundOrDenied("not found or permission denied")
    generation = _generation(con, document_id)
    return row, role, (tuple(row), int(workspace["security_epoch"]), generation)


class _References(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()

    def add(self, value):
        try:
            self.ids.add(_identifier(value, field="document_id"))
        except (ValueError, TypeError):
            pass

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        try:
            url = urlsplit(dict(attrs).get("href") or "")
            if not url.scheme and not url.netloc and url.path.startswith("/page/"):
                self.add(unquote(url.path[6:]))
        except ValueError:
            pass


def _reference_ids(blocks, parent_id):
    parser = _References()
    if parent_id:
        parser.add(parent_id)
    for block in blocks:
        props = block.get("props") or {}
        if block.get("type") in {"child_page", "link_to_page"}:
            parser.add(props.get("page_id"))
        if isinstance(props.get("rich_text_html"), str):
            parser.feed(props["rich_text_html"])
    parser.close()
    return sorted(parser.ids)


def _relation_labels(authority, con, actor, database):
    labels = {}
    for prop in database["properties"]:
        if prop["type"] != "relation":
            continue
        target_id = str(prop.get("config", {}).get("target_database_id") or "")
        target = con.execute(
            "SELECT d.document_id,p.workspace_id,p.archived FROM workspace_collaboration_databases d "
            "JOIN workspace_collaboration_documents p ON p.id=d.document_id WHERE d.id=?", (target_id,),
        ).fetchone()
        if target is None or target["archived"] or not CollaborativeIAMAuthority._resolution_on_connection(
            con, actor, str(target["workspace_id"]), str(target["document_id"]),
        )[0]:
            continue
        rows = con.execute(
            "SELECT r.id,c.value_json FROM workspace_collaboration_database_rows r "
            "JOIN workspace_collaboration_database_cells c ON c.row_id=r.id "
            "JOIN workspace_collaboration_database_properties p ON p.id=c.property_id "
            "WHERE r.database_id=? AND p.database_id=? AND p.property_type='title' ORDER BY p.position,p.id",
            (target_id, target_id),
        ).fetchall()
        selected = {str(value) for row in database["rows"]
                    for value in (row["cells"].get(prop["id"]) or [])}
        current = {}
        for row in rows:
            value = json.loads(row["value_json"])
            if row["id"] in selected and row["id"] not in current and isinstance(value, (str, int, float)):
                current[row["id"]] = str(value)
        labels[prop["id"]] = current
    return labels


@retry_title_projection
def read_search_preview(authority, actor, workspace_id, document_id):
    actor = authority._require_active(actor)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        row, _role, stamp = _source(authority, con, actor, workspace_id, document_id)
        descriptor = authority._editing_snapshot(con, row)
        frames = _frames(con, document_id, stamp[-1])
        parent_id = str(row["parent_document_id"] or "")
    if frames:
        current = materialize([frames])[0]
        if current["title"] is not None:
            descriptor["title"] = current["title"]
        if current["blocks"] is not None:
            descriptor["blocks"] = current["blocks"]
    ids = _reference_ids(descriptor["blocks"], parent_id)
    titles = read_current_titles(authority, actor, workspace_id, [{"id": value} for value in ids]) if ids else {}
    authority._require_active(actor)
    with authority._reader() as con:
        con.execute("BEGIN")
        row, role, current_stamp = _source(authority, con, actor, workspace_id, document_id)
        if stamp != current_stamp:
            raise TitleProjectionChanged()
        references = {}
        for reference_id, title in titles.items():
            try:
                reference, _, ref_stamp = _source(authority, con, actor, workspace_id, reference_id)
            except NotFoundOrDenied as exc:
                raise TitleProjectionChanged() from exc
            projection = title["title_projection"]
            if (int(reference["document_sequence"]) != projection["document_sequence"]
                    or ref_stamp[-1] != projection["generation"]):
                raise TitleProjectionChanged()
            references[reference_id] = {"id": reference_id, "title": title["title"], "icon": reference["icon"]}
        databases = []
        visible_databases = {str(block.get("props", {}).get("database_id") or block.get("id") or "")
                             for block in descriptor["blocks"]}
        for raw in descriptor["databases"]:
            if raw["id"] not in visible_databases:
                continue
            database = dict(raw)
            database["rows"] = authority._materialize_rows(con, actor, database)
            database["relation_labels"] = _relation_labels(authority, con, actor, database)
            databases.append(database)
        return {
            "contract": CONTRACT,
            "page": {"id": document_id, "workspace_id": workspace_id, "parent_id": parent_id,
                     "title": descriptor["title"], "icon": row["icon"], "cover": row["cover"],
                     "owner_user_id": row["legacy_owner_subject_id"], "role": role, "read_only": True,
                     "document_sequence": int(row["document_sequence"]),
                     "workspace_epoch": stamp[-2], "document_epoch": int(row["access_epoch"])},
            "blocks": descriptor["blocks"], "databases": databases, "references": references,
            "projection": {"contract": CONTRACT, "generation": stamp[-1],
                           "document_sequence": int(row["document_sequence"])},
        }
