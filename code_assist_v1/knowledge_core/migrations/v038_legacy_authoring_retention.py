"""Opt-in legacy private-body retention; no automatic runtime activation.

Preserve historical response hashes and sizes, not private JSON or prose.
Old model/transport hashes were never stored and are not fabricated here.
All legacy/source identities and prior numbered migrations remain unchanged.
"""
from .v014_wiki_authoring import _sha


_RECEIPT_COLUMNS = (
    "run_id,ordinal,response_sha256,response_size_bytes,model_size_bytes,"
    "transport_size_bytes,completed_at"
)


def _receipt_values(alias):
    return (f"{alias}.run_id,{alias}.ordinal,{alias}.response_sha256,"
            f"length(CAST({alias}.response AS BLOB)),length(CAST({alias}.model_json AS BLOB)),"
            f"length(CAST({alias}.transport_json AS BLOB)),{alias}.completed_at")


_BODY_RUNS = (
    ("publication_authoring_chunk_body", "OLD.run_id", "NEW.run_id"),
    ("authored_wiki_snapshot_body",
     "(SELECT run_id FROM authored_wiki_snapshot WHERE id=OLD.snapshot_id)",
     "(SELECT run_id FROM authored_wiki_snapshot WHERE id=NEW.snapshot_id)"),
    ("publication_synthesis_batch_body", "OLD.run_id", "NEW.run_id"),
)


def _cleanup(run_predicate):
    runs = f"SELECT r.id FROM publication_authoring_run r WHERE {run_predicate}"
    return (
        "UPDATE publication_synthesis_receipt SET retention_status='purged' "
        f"WHERE retention_status='present' AND run_id IN ({runs})",
        f"DELETE FROM publication_synthesis_batch_body WHERE run_id IN ({runs})",
        f"DELETE FROM publication_authoring_chunk_body WHERE run_id IN ({runs})",
        "DELETE FROM authored_wiki_snapshot_body WHERE snapshot_id IN "
        f"(SELECT id FROM authored_wiki_snapshot WHERE run_id IN ({runs}))",
    )


_OLD_PURGED = """EXISTS(SELECT 1 FROM extraction_snapshot e
    JOIN source_artifact a ON a.id=e.artifact_id
    WHERE e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      AND e.body_retention_status='purged' AND a.body_retention_status='purged'
      AND a.body_retention_reason='eligible_item_purge' AND a.body_purged_at IS NOT NULL
      AND NOT EXISTS(SELECT 1 FROM extraction_snapshot_body b WHERE b.snapshot_id=e.id))"""

V038_STATEMENTS = (
    f"""CREATE TABLE publication_authoring_chunk_receipt(
        run_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        response_sha256 TEXT NOT NULL CHECK({_sha('response_sha256')}),
        response_size_bytes INTEGER NOT NULL CHECK(response_size_bytes>=0),
        model_size_bytes INTEGER NOT NULL CHECK(model_size_bytes>0),
        transport_size_bytes INTEGER NOT NULL CHECK(transport_size_bytes>0),
        completed_at INTEGER NOT NULL CHECK(completed_at>=0),
        PRIMARY KEY(run_id,ordinal),
        FOREIGN KEY(run_id,ordinal) REFERENCES publication_authoring_chunk(run_id,ordinal) ON DELETE RESTRICT
    ) STRICT""",
    f"INSERT INTO publication_authoring_chunk_receipt({_RECEIPT_COLUMNS}) "
    f"SELECT {_receipt_values('b')} FROM publication_authoring_chunk_body b",
    """CREATE TRIGGER legacy_authoring_receipt_binding BEFORE INSERT ON publication_authoring_chunk_receipt
    WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_chunk_body b
      WHERE b.run_id=NEW.run_id AND b.ordinal=NEW.ordinal
        AND b.response_sha256=NEW.response_sha256 AND b.completed_at=NEW.completed_at
        AND length(CAST(b.response AS BLOB))=NEW.response_size_bytes
        AND length(CAST(b.model_json AS BLOB))=NEW.model_size_bytes
        AND length(CAST(b.transport_json AS BLOB))=NEW.transport_size_bytes)
    BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_RECEIPT_INVALID'); END""",
    f"""CREATE TRIGGER legacy_authoring_capture_receipt AFTER INSERT ON publication_authoring_chunk_body
    BEGIN INSERT INTO publication_authoring_chunk_receipt({_RECEIPT_COLUMNS})
      VALUES({_receipt_values('NEW')}); END""",
    *(f"""CREATE TRIGGER legacy_{table}_history_{verb.lower()} BEFORE {verb} ON {table}
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_HISTORY_IMMUTABLE'); END"""
      for table, verbs in (
          ("publication_authoring_chunk_receipt", ("UPDATE", "DELETE")),
          ("publication_authoring_run", ("DELETE",)),
          ("publication_authoring_chunk", ("DELETE",)),
          ("authored_wiki_snapshot", ("DELETE",)),
      ) for verb in verbs),
    *(f"""CREATE TRIGGER legacy_{table}_source_delete BEFORE DELETE ON {table}
      WHEN EXISTS(SELECT 1 FROM publication_authoring_run r JOIN extraction_snapshot_body b
        ON b.snapshot_id=r.extraction_snapshot_id AND b.artifact_id=r.artifact_id WHERE r.id={old_run})
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_BODY_RETAINED'); END"""
      for table, old_run, _ in _BODY_RUNS),
    *(f"""CREATE TRIGGER legacy_{table}_source_insert BEFORE INSERT ON {table}
      WHEN NOT EXISTS(SELECT 1 FROM publication_authoring_run r
        JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
          AND e.body_retention_status='present'
        JOIN source_artifact a ON a.id=r.artifact_id AND a.body_retention_status='present'
        JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
        WHERE r.id={new_run} AND r.status='pending')
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_SOURCE_UNAVAILABLE'); END"""
      for table, _, new_run in _BODY_RUNS),
    """CREATE TRIGGER legacy_synthesis_receipt_source_retention
      BEFORE UPDATE OF retention_status ON publication_synthesis_receipt
      WHEN NEW.retention_status='purged' AND EXISTS(SELECT 1 FROM publication_authoring_run r
        JOIN extraction_snapshot_body b ON b.snapshot_id=r.extraction_snapshot_id
          AND b.artifact_id=r.artifact_id WHERE r.id=OLD.run_id)
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_BODY_RETAINED'); END""",
    """CREATE TRIGGER legacy_authoring_pending_purge BEFORE DELETE ON extraction_snapshot_body
      WHEN EXISTS(SELECT 1 FROM publication_authoring_run r WHERE r.extraction_snapshot_id=OLD.snapshot_id
        AND r.artifact_id=OLD.artifact_id AND r.status='pending')
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_PENDING_WORK'); END""",
    """CREATE TRIGGER legacy_authoring_pending_retention BEFORE UPDATE OF body_retention_status ON extraction_snapshot
      WHEN NEW.body_retention_status<>'present' AND EXISTS(SELECT 1 FROM publication_authoring_run r
        WHERE r.extraction_snapshot_id=OLD.id AND r.artifact_id=OLD.artifact_id AND r.status='pending')
      BEGIN SELECT RAISE(ABORT,'LEGACY_AUTHORING_PENDING_WORK'); END""",
    "CREATE TRIGGER legacy_authoring_source_cleanup AFTER DELETE ON extraction_snapshot_body BEGIN "
    + "; ".join(_cleanup("r.extraction_snapshot_id=OLD.snapshot_id AND r.artifact_id=OLD.artifact_id")) + "; END",
    # Offline forward repair of historically confirmed purge residue only.
    # The whole migration rolls back on any pending/missing-source invariant.
    *_cleanup(_OLD_PURGED),
)

V038_POSTCONDITION_QUERIES = (
    """SELECT 'legacy authoring receipt missing or invalid' FROM publication_authoring_chunk_body b
      LEFT JOIN publication_authoring_chunk_receipt q ON q.run_id=b.run_id AND q.ordinal=b.ordinal
      WHERE q.run_id IS NULL OR q.response_sha256<>b.response_sha256 OR q.completed_at<>b.completed_at
        OR q.response_size_bytes<>length(CAST(b.response AS BLOB))
        OR q.model_size_bytes<>length(CAST(b.model_json AS BLOB))
        OR q.transport_size_bytes<>length(CAST(b.transport_json AS BLOB)) LIMIT 1""",
    """SELECT 'legacy authoring map retention invalid' FROM publication_authoring_chunk_receipt q
      JOIN publication_authoring_run r ON r.id=q.run_id
      LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      LEFT JOIN publication_authoring_chunk_body b ON b.run_id=q.run_id AND b.ordinal=q.ordinal
      WHERE (e.snapshot_id IS NULL)<>(b.run_id IS NULL) LIMIT 1""",
    """SELECT 'legacy authoring snapshot retention invalid' FROM authored_wiki_snapshot s
      JOIN publication_authoring_run r ON r.id=s.run_id
      LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      LEFT JOIN authored_wiki_snapshot_body b ON b.snapshot_id=s.id
      WHERE (e.snapshot_id IS NULL)<>(b.snapshot_id IS NULL) LIMIT 1""",
    """SELECT 'legacy synthesis retention invalid' FROM publication_synthesis_receipt q
      JOIN publication_authoring_run r ON r.id=q.run_id
      LEFT JOIN extraction_snapshot_body e ON e.snapshot_id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      LEFT JOIN publication_synthesis_batch_body b ON b.run_id=q.run_id AND b.round_number=q.round_number AND b.ordinal=q.ordinal
      WHERE (e.snapshot_id IS NULL)<>(b.run_id IS NULL)
        OR (e.snapshot_id IS NULL)<>(q.retention_status='purged') LIMIT 1""",
    """SELECT 'legacy pending authoring source unavailable' FROM publication_authoring_run r
      JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id AND e.artifact_id=r.artifact_id
      JOIN source_artifact a ON a.id=r.artifact_id
      LEFT JOIN extraction_snapshot_body b ON b.snapshot_id=e.id AND b.artifact_id=e.artifact_id
      WHERE r.status='pending' AND (b.snapshot_id IS NULL OR e.body_retention_status<>'present'
        OR a.body_retention_status<>'present') LIMIT 1""",
)
