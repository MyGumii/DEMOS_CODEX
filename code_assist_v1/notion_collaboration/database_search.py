"""Staged title search for independently shared databases.

Database results have their own identity. Never turn a database grant into a
page result or include the database's inaccessible owner document in a hit.
"""
from __future__ import annotations

from . import database_access
from .canonical_authority import NotFoundOrDenied, _identifier


CONTRACT = "code_assist.notion.database_search.v1"


def search_database_titles_scoped(authority, actor_subject_id: str,
                                  workspace_id: str, *, query: str = "",
                                  sort: str = "relevance", limit: int = 50,
                                  date_after: int = 0,
                                  direct_only: bool = False) -> dict:
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    if not isinstance(query, str):
        raise ValueError("search query must be text")
    clean_query = " ".join(query.split())
    if len(clean_query) > 200:
        raise ValueError("search query is too long")
    terms = [term.casefold() for term in clean_query.split()]
    if len(terms) > 32:
        raise ValueError("search query has too many terms")
    if sort not in {"relevance", "recent", "oldest", "title"}:
        raise ValueError("supported search sort required")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("search limit must be between 1 and 100")
    if isinstance(date_after, bool) or not isinstance(date_after, int) or date_after < 0:
        raise ValueError("date_after must be a non-negative integer")
    if not isinstance(direct_only, bool):
        raise ValueError("direct_only must be a boolean")

    with authority._reader() as con:
        con.execute("BEGIN")
        authority._workspace(con, workspace)
        if not database_access.installed(con):
            from .canonical_authority import AuthorityUnavailable
            raise AuthorityUnavailable("database sharing is not installed")
        participant = con.execute(
            "SELECT 1 FROM workspace_memberships WHERE workspace_id=? "
            "AND subject_id=? AND revoked_at IS NULL UNION ALL "
            "SELECT 1 FROM workspace_guests WHERE workspace_id=? "
            "AND subject_id=? AND revoked_at IS NULL LIMIT 1",
            (workspace, actor, workspace, actor),
        ).fetchone()
        if participant is None:
            raise NotFoundOrDenied("not found or permission denied")
        rows = con.execute(
            "SELECT db.id,db.title,db.updated_at FROM "
            "workspace_collaboration_databases db JOIN "
            "workspace_collaboration_documents doc ON doc.id=db.document_id "
            "WHERE doc.workspace_id=? AND db.updated_at>=?",
            (workspace, date_after),
        )
        matches = []
        for row in rows:
            title = str(row["title"])
            folded = title.casefold()
            if not all(term in folded for term in terms):
                continue
            try:
                access = database_access.resolve_on_connection(
                    authority, con, actor, workspace, str(row["id"]),
                )
            except NotFoundOrDenied:
                continue
            if direct_only and any(source["scope"] == "parent_page"
                                   for source in access.sources):
                continue
            matches.append({
                "id": access.database_id,
                "database_id": access.database_id,
                "kind": "database",
                "title": title,
                "role": access.role,
                "updated_at": int(row["updated_at"]),
                "_score": sum(4 if folded.startswith(term) else 1 for term in terms),
            })
    if sort == "title":
        matches.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    elif sort == "oldest":
        matches.sort(key=lambda item: (item["updated_at"], item["id"]))
    elif sort == "recent":
        matches.sort(key=lambda item: (-item["updated_at"], item["id"]))
    else:
        matches.sort(key=lambda item: (-item["_score"], -item["updated_at"], item["id"]))
    for item in matches:
        item.pop("_score")
    return {
        "contract": CONTRACT, "workspace_id": workspace, "query": clean_query,
        "direct_only": direct_only,
        "items": matches[:limit], "count": min(len(matches), limit),
        "has_more": len(matches) > limit, "cache_policy": "no-store",
    }
