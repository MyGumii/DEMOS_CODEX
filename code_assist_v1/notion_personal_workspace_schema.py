"""Forward-only appearance schema for a legacy personal Notion shard.

Existing shards require an explicit offline plan/apply. Only a brand-new shard
may install this schema at its existing workspace creation boundary.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import time

from code_assist_v1 import notion_store as ns

VERSION = 2026091501
NAME = "notion_personal_workspace_appearance_v1"
DDL = (
    """CREATE TABLE personal_workspace_appearance (
      owner_user_id TEXT PRIMARY KEY, icon TEXT NOT NULL DEFAULT '' CHECK(length(icon)<=160),
      version INTEGER NOT NULL CHECK(version>0), updated_at INTEGER NOT NULL,
      image_id TEXT, mime_type TEXT, image_bytes BLOB,
      CHECK((image_id IS NULL AND mime_type IS NULL AND image_bytes IS NULL) OR
        (image_id IS NOT NULL AND mime_type IN ('image/png','image/jpeg','image/webp')
         AND typeof(image_bytes)='blob' AND length(image_bytes) BETWEEN 1 AND 2097152)))""",
    """CREATE TABLE personal_workspace_operations (
      actor_user_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
      result_json TEXT NOT NULL, created_at INTEGER NOT NULL,
      PRIMARY KEY(actor_user_id,operation_id))""",
    """CREATE TABLE personal_workspace_audit (
      event_id TEXT PRIMARY KEY, actor_user_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
      action TEXT NOT NULL, payload_json TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    """CREATE TABLE personal_workspace_outbox (
      event_id TEXT PRIMARY KEY REFERENCES personal_workspace_audit(event_id),
      delivered_at INTEGER)""",
)
CHECKSUM = "sha256:1a07f0bf1d9db43dc79770b53e4ac91e2de3034c582c36bf10c79e9d651143e5"


def _schema_matches(con):
    for statement in DDL:
        name = statement.split()[2]
        row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if not row or " ".join(row[0].split()) != " ".join(statement.split()):
            return False
    return True


def ready(con):
    if not ns._atomic_schema_ready(con):
        return False
    row = con.execute("SELECT name,checksum FROM notion_schema_migrations WHERE version=?", (VERSION,)).fetchone()
    return bool(row and tuple(row) == (NAME, CHECKSUM) and _schema_matches(con))


def install_new(con, owner):
    """Called within explicit workspace creation or an offline migration only."""
    if not ns._atomic_schema_ready(con):
        raise RuntimeError("atomic save migration is required first")
    if ready(con):
        rows = con.execute("SELECT owner_user_id FROM personal_workspace_appearance").fetchall()
        if [row[0] for row in rows] != [owner]:
            raise RuntimeError("personal workspace ownership mismatch")
        return
    if con.execute("SELECT 1 FROM notion_schema_migrations WHERE version=?", (VERSION,)).fetchone():
        raise RuntimeError("personal workspace schema checksum mismatch")
    for statement in DDL:
        con.execute(statement)
    con.execute("INSERT INTO personal_workspace_appearance(owner_user_id,version,updated_at) VALUES(?,1,?)", (owner, int(time.time())))
    con.execute("INSERT INTO notion_schema_migrations VALUES(?,?,?,?)", (VERSION, NAME, CHECKSUM, int(time.time())))
    if not ready(con):
        raise RuntimeError("personal workspace schema validation failed")


def snapshot_sha(con):
    digest = hashlib.sha256()
    for line in con.iterdump():
        digest.update(line.encode("utf-8")); digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def build_plan(owner_user_id):
    owner = ns._uid(owner_user_id)
    with ns._connection(owner) as con:
        con.execute("BEGIN")
        if not ns._atomic_schema_ready(con):
            raise RuntimeError("atomic save migration is required first")
        current = ready(con)
        if current and [row[0] for row in con.execute("SELECT owner_user_id FROM personal_workspace_appearance")] != [owner]:
            raise RuntimeError("personal workspace ownership mismatch")
        return {"owner_user_id": owner, "source_sha256": snapshot_sha(con),
                "target_version": VERSION, "target_checksum": CHECKSUM,
                "state": "already_current" if current else "migration_required"}


def apply_plan(plan, *, backup_path, quiesced_confirmed=False):
    if quiesced_confirmed is not True:
        raise ValueError("stop writers before applying this migration")
    owner = ns._uid(plan.get("owner_user_id"))
    if build_plan(owner) != plan:
        raise RuntimeError("migration plan is stale")
    if plan["state"] == "already_current":
        return plan
    backup = Path(backup_path).resolve()
    if backup == Path(ns._db_path(owner)).resolve():
        raise ValueError("backup must be a separate new file")
    with backup.open("xb"):
        pass
    with ns._connection(owner) as source:
        destination = sqlite3.connect(backup)
        try:
            source.backup(destination)
            if snapshot_sha(destination) != plan["source_sha256"]:
                raise RuntimeError("source changed before backup; build a new plan")
        finally:
            destination.close()
    with ns._write_connection(owner) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            if snapshot_sha(con) != plan["source_sha256"]:
                raise RuntimeError("source changed after backup; build a new plan")
            install_new(con, owner)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return build_plan(owner)
