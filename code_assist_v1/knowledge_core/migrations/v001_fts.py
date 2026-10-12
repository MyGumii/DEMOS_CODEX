"""Transactional external-content FTS5 objects for physical schema v1.

``search_document`` is an ordinary canonical-transaction projection table and is
created by :mod:`v001_tables`.  This module owns only the FTS5 virtual table and
the three standard external-content synchronization triggers required by
``SCHEMA_V1.md`` section 10.  Keeping the statements as immutable constants makes
the migration checksum independent of SQLite introspection or application code.
"""

from __future__ import annotations


FTS_TABLE_NAME = "knowledge_fts"

FTS_TABLE_SQL = """CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  title,
  content_md,
  content='search_document',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);"""


FTS_TRIGGER_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "trg_search_document_fts_insert",
        """CREATE TRIGGER trg_search_document_fts_insert
AFTER INSERT ON search_document
BEGIN
  INSERT INTO knowledge_fts(rowid, title, content_md)
  VALUES (NEW.rowid, NEW.title, NEW.content_md);
END""",
    ),
    (
        "trg_search_document_fts_delete",
        """CREATE TRIGGER trg_search_document_fts_delete
AFTER DELETE ON search_document
BEGIN
  INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content_md)
  VALUES ('delete', OLD.rowid, OLD.title, OLD.content_md);
END""",
    ),
    (
        "trg_search_document_fts_update",
        """CREATE TRIGGER trg_search_document_fts_update
AFTER UPDATE ON search_document
BEGIN
  INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content_md)
  VALUES ('delete', OLD.rowid, OLD.title, OLD.content_md);
  INSERT INTO knowledge_fts(rowid, title, content_md)
  VALUES (NEW.rowid, NEW.title, NEW.content_md);
END""",
    ),
)

FTS_TRIGGER_NAMES = tuple(name for name, _sql in FTS_TRIGGER_DEFINITIONS)
FTS_TRIGGER_STATEMENTS = tuple(sql for _name, sql in FTS_TRIGGER_DEFINITIONS)
FTS_STATEMENTS = (FTS_TABLE_SQL, *FTS_TRIGGER_STATEMENTS)


__all__ = [
    "FTS_STATEMENTS",
    "FTS_TABLE_NAME",
    "FTS_TABLE_SQL",
    "FTS_TRIGGER_DEFINITIONS",
    "FTS_TRIGGER_NAMES",
    "FTS_TRIGGER_STATEMENTS",
]
