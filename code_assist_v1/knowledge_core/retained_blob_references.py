"""Read-only complete blob closure for verified canonical schema generations.

Callers validate their catalog/schema before using this helper, including raw
SQLite backup snapshots. Optional v42 does not change old-catalog behavior.
"""


def has_visual_blob_references(connection):
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='view' AND name='pdf_visual_retained_blob'").fetchone() is not None


def retained_blob_union_sql(connection):
    sql = """SELECT content_sha256 AS sha256 FROM artifact_blob_body
        UNION SELECT content_blob_hash FROM extraction_snapshot_body WHERE content_blob_hash IS NOT NULL
        UNION SELECT content_blob_hash FROM knowledge_revision_body WHERE content_blob_hash IS NOT NULL
        UNION SELECT proposed_content_blob_hash FROM change_proposal WHERE proposed_content_blob_hash IS NOT NULL"""
    if has_visual_blob_references(connection):
        sql += " UNION SELECT sha256 FROM pdf_visual_retained_blob"
    return sql


def blob_is_referenced(connection,digest):
    terms = ["EXISTS(SELECT 1 FROM artifact_blob_body WHERE content_sha256=?)",
        "EXISTS(SELECT 1 FROM extraction_snapshot_body WHERE content_blob_hash=?)",
        "EXISTS(SELECT 1 FROM knowledge_revision_body WHERE content_blob_hash=?)",
        "EXISTS(SELECT 1 FROM change_proposal WHERE proposed_content_blob_hash=?)"]
    if has_visual_blob_references(connection):
        terms.append("EXISTS(SELECT 1 FROM pdf_visual_retained_blob WHERE sha256=?)")
    return connection.execute("SELECT "+" OR ".join(terms),(digest,)*len(terms)).fetchone()[0]


def visual_blob_references(connection,*,artifact_ids=None,digest=None,exclude_artifacts=()):
    if not has_visual_blob_references(connection): return ()
    clauses,values = [],[]
    if artifact_ids is not None:
        if not artifact_ids: return ()
        clauses.append("artifact_id IN ("+",".join("?" for _ in artifact_ids)+")")
        values.extend(artifact_ids)
    if digest is not None:
        clauses.append("sha256=?"); values.append(digest)
    if exclude_artifacts:
        clauses.append("artifact_id NOT IN ("+",".join("?" for _ in exclude_artifacts)+")")
        values.extend(exclude_artifacts)
    return tuple(connection.execute("SELECT DISTINCT sha256,artifact_id FROM pdf_visual_retained_blob"
        +( " WHERE "+" AND ".join(clauses) if clauses else ""),tuple(values)))
