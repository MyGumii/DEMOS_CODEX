"""Notion-owned encrypted conversations; no canonical SBE/Notion page writes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zlib
from uuid import UUID, uuid4

from cryptography.fernet import Fernet


class ChatConflict(Exception):
    pass


def operation_id(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("operation_id must be a lowercase UUID")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ChatStore:
    VERSION = "notion-ai-chat-v1"

    def __init__(self, path, secret, owner):
        self.path = Path(path)
        secret = secret.encode() if isinstance(secret, str) else secret
        if not secret or len(secret) < 16 or not self.path.is_absolute():
            raise ValueError("persistent host secret and absolute chat path required")
        key = hashlib.sha256(self.VERSION.encode() + b"\0" + owner.encode() + b"\0" + secret).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def pack(self, value):
        raw = canonical(value).encode()
        if len(raw) > 4_000_000:
            raise ValueError("대화가 너무 깁니다. 새 채팅을 시작하세요.")
        return self.cipher.encrypt(raw)

    def unpack(self, value):
        return json.loads(self.cipher.decrypt(value))

    @contextmanager
    def db(self, *, initialize=False):
        if not self.path.exists() and not initialize:
            raise FileNotFoundError("대화를 찾을 수 없습니다.")
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if initialize:
                    connection.execute("CREATE TABLE IF NOT EXISTS ai_chat_meta (version TEXT PRIMARY KEY, proof BLOB NOT NULL)")
                    if not connection.execute("SELECT 1 FROM ai_chat_meta").fetchone():
                        connection.execute("INSERT INTO ai_chat_meta VALUES (?,?)", (self.VERSION, self.pack(self.VERSION)))
                    connection.execute("""CREATE TABLE IF NOT EXISTS ai_chat (
                        id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
                        revision INTEGER NOT NULL, archived INTEGER NOT NULL, created REAL NOT NULL,
                        updated REAL NOT NULL, payload BLOB NOT NULL)""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS ai_chat_operation (
                        id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                        kind TEXT NOT NULL, state TEXT NOT NULL, claim TEXT, deadline REAL, payload BLOB)""")
                meta = connection.execute("SELECT * FROM ai_chat_meta").fetchall()
                if len(meta) != 1 or meta[0]["version"] != self.VERSION or self.unpack(meta[0]["proof"]) != self.VERSION:
                    raise ValueError("chat schema/key mismatch")
                yield connection
        finally:
            connection.close()

    def record(self, row):
        return {"id": row["id"], "revision": row["revision"], "archived": bool(row["archived"]),
                "created_at": row["created"], "updated_at": row["updated"], **self.unpack(row["payload"])}

    def create(self, op, scope):
        op = operation_id(op)
        request_hash = hashlib.sha256(canonical(scope).encode()).hexdigest()
        with self.db(initialize=True) as con:
            row = con.execute("SELECT * FROM ai_chat WHERE operation_id=?", (op,)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise ChatConflict("같은 요청 ID의 참고 범위가 달라졌습니다.")
                return self.record(row)
            if con.execute("SELECT count(*) FROM ai_chat").fetchone()[0] >= 500:
                raise ChatConflict("호스트의 대화 보관 한도에 도달했습니다. 운영자에게 문의하세요.")
            now, chat_id = time.time(), str(uuid4())
            payload = self.pack({"title": "새 채팅", "scope": scope, "messages": []})
            con.execute("INSERT INTO ai_chat VALUES (?,?,?,0,0,?,?,?)", (chat_id, op, request_hash, now, now, payload))
        return self.get(chat_id)

    def get(self, chat_id):
        with self.db() as con:
            row = con.execute("SELECT * FROM ai_chat WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise FileNotFoundError("대화를 찾을 수 없습니다.")
            result = self.record(row)
            latest = con.execute("SELECT state,deadline FROM ai_chat_operation WHERE chat_id=? AND kind='turn' ORDER BY rowid DESC LIMIT 1", (chat_id,)).fetchone()
            result["pending"] = bool(latest and (latest["state"] == "queued" or latest["state"] == "processing" and latest["deadline"] > time.time()))
            result["interrupted"] = bool(latest and (latest["state"] in {"failed", "interrupted"} or (latest["state"] == "processing" and latest["deadline"] <= time.time())))
            return result

    def list(self, *, archived=False, offset=0):
        if not self.path.exists():
            return []
        with self.db() as con:
            return [self.record(row) for row in con.execute(
                "SELECT * FROM ai_chat WHERE archived=? ORDER BY updated DESC,id LIMIT 51 OFFSET ?", (int(archived), offset)).fetchall()]

    def begin(self, chat_id, op, data, expected_revision, *, research_job=None):
        op = operation_id(op)
        request_hash = hashlib.sha256(canonical({"chat": chat_id, "data": data, "revision": expected_revision}).encode()).hexdigest()
        with self.db() as con:
            row = con.execute("SELECT * FROM ai_chat WHERE id=?", (chat_id,)).fetchone()
            if not row or row["archived"]:
                raise FileNotFoundError("대화를 찾을 수 없습니다.")
            previous = con.execute("SELECT * FROM ai_chat_operation WHERE id=?", (op,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ChatConflict("같은 요청 ID의 내용이 달라졌습니다.")
                if previous["state"] == "completed":
                    return {"replayed": True}
                if research_job is not None and previous["payload"] is not None:
                    return {"replayed": True}
                if previous["state"] == "processing" and previous["deadline"] > time.time():
                    raise ChatConflict("이미 처리 중인 요청입니다. 대화 기록에서 완료 상태를 확인하세요.")
                raise ChatConflict("이 요청은 중단되었거나 실패했습니다. 새 요청 ID로 다시 보내세요.")
            if row["revision"] != expected_revision:
                raise ChatConflict("다른 탭에서 대화가 변경되었습니다. 다시 불러오세요.")
            active = con.execute("SELECT 1 FROM ai_chat_operation WHERE chat_id=? AND (state='queued' OR state='processing' AND (deadline>? OR payload IS NOT NULL))", (chat_id, time.time())).fetchone()
            if active:
                raise ChatConflict("이 대화에 처리 중인 요청이 있습니다.")
            if len(self.unpack(row["payload"])["messages"]) >= 100:
                raise ChatConflict("대화가 길어졌습니다. 새 채팅을 시작하세요.")
            claim = str(uuid4())
            con.execute("UPDATE ai_chat_operation SET state='interrupted' WHERE chat_id=? AND state='processing'", (chat_id,))
            con.execute("INSERT INTO ai_chat_operation VALUES (?,?,?,'turn',?,?,?,?)", (op, chat_id, request_hash,
                "queued" if research_job is not None else "processing", None if research_job is not None else claim,
                None if research_job is not None else time.time() + 600,
                self.pack_job(research_job) if research_job is not None else None))
            if research_job is not None:
                record = self.unpack(row["payload"])
                if not record["messages"]:
                    record["title"] = data["message"].strip().splitlines()[0][:80]
                    con.execute("UPDATE ai_chat SET payload=?,updated=? WHERE id=?", (self.pack(record), time.time(), chat_id))
        return {"claim": claim, "replayed": False}

    def assert_claim(self, chat_id, op, claim):
        with self.db() as con:
            row = con.execute("""SELECT 1 FROM ai_chat_operation o JOIN ai_chat c ON c.id=o.chat_id
                WHERE o.id=? AND o.chat_id=? AND o.claim=? AND o.state='processing' AND o.deadline>? AND c.archived=0""",
                (op, chat_id, claim, time.time())).fetchone()
            if not row:
                raise ChatConflict("대화 요청이 만료되었거나 보관되었습니다.")

    def finish(self, chat_id, op, claim, question, result):
        with self.db() as con:
            request = con.execute("SELECT * FROM ai_chat_operation WHERE id=? AND chat_id=? AND claim=? AND state='processing' AND deadline>?", (op, chat_id, claim, time.time())).fetchone()
            row = con.execute("SELECT * FROM ai_chat WHERE id=? AND archived=0", (chat_id,)).fetchone()
            if not request or not row:
                raise ChatConflict("대화 요청이 만료되었거나 보관되었습니다.")
            record = self.unpack(row["payload"])
            if not record["messages"]:
                record["title"] = question.strip().splitlines()[0][:80]
            record["messages"].extend([
                {"role": "user", "text": question, "operation_id": op},
                {"role": "assistant", "text": result["answer"], "operation_id": op, **{key: value for key, value in result.items() if key != "answer"}},
            ])
            con.execute("UPDATE ai_chat SET payload=?,revision=revision+1,updated=? WHERE id=?", (self.pack(record), time.time(), chat_id))
            con.execute("UPDATE ai_chat_operation SET state='completed' WHERE id=? AND claim=?", (op, claim))

    def fail(self, op, claim):
        with self.db() as con:
            con.execute("UPDATE ai_chat_operation SET state='failed' WHERE id=? AND claim=? AND state='processing'", (op, claim))

    def pack_job(self, job):
        raw = canonical(job).encode()
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("조사 자료 저장 한도에 도달했습니다.")
        return self.cipher.encrypt(zlib.compress(raw))

    def unpack_job(self, payload):
        decoder = zlib.decompressobj()
        raw = decoder.decompress(self.cipher.decrypt(payload), 32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024 or not decoder.eof:
            raise ValueError("invalid research checkpoint")
        job = json.loads(raw)
        if job.get("contract") != "notion-deep-research-v1":
            raise ValueError("invalid research checkpoint version")
        return job

    def research_job(self, chat_id, op=None):
        with self.db() as con:
            row = con.execute("SELECT * FROM ai_chat_operation WHERE chat_id=? AND kind='turn' AND payload IS NOT NULL" +
                (" AND id=?" if op else "") + " ORDER BY rowid DESC LIMIT 1", (chat_id, op) if op else (chat_id,)).fetchone()
            if not row:
                return None
            return {"id": row["id"], "state": row["state"], "claim": row["claim"], "deadline": row["deadline"],
                    "chat_id": chat_id, "job": self.unpack_job(row["payload"])}

    def queued_research(self):
        if not self.path.exists():
            return []
        with self.db() as con:
            return [(r["chat_id"], r["id"]) for r in con.execute("SELECT o.chat_id,o.id FROM ai_chat_operation o "
                "JOIN ai_chat c ON c.id=o.chat_id WHERE c.archived=0 AND o.payload IS NOT NULL "
                "AND (o.state='queued' OR o.state='processing' AND o.deadline<=?) ORDER BY o.rowid", (time.time(),))]

    def claim_research(self, chat_id, op, *, lease_seconds=180):
        with self.db() as con:
            row = con.execute("SELECT o.* FROM ai_chat_operation o JOIN ai_chat c ON c.id=o.chat_id "
                "WHERE o.id=? AND o.chat_id=? AND o.payload IS NOT NULL AND c.archived=0 "
                "AND (o.state='queued' OR o.state='processing' AND o.deadline<=?)", (op, chat_id, time.time())).fetchone()
            if not row:
                return None
            claim = str(uuid4())
            job = self.unpack_job(row["payload"])
            job["updated_at"] = time.time()
            con.execute("UPDATE ai_chat_operation SET state='processing',claim=?,deadline=?,payload=? WHERE id=?",
                        (claim, time.time() + lease_seconds, self.pack_job(job), op))
            return claim, job

    def checkpoint_research(self, chat_id, op, claim, job, *, release=False):
        with self.db() as con:
            row = con.execute("SELECT 1 FROM ai_chat_operation o JOIN ai_chat c ON c.id=o.chat_id "
                "WHERE o.id=? AND o.chat_id=? AND o.claim=? AND o.state='processing' AND o.deadline>? AND c.archived=0",
                (op, chat_id, claim, time.time())).fetchone()
            if not row:
                raise ChatConflict("리서치 작업이 중단되었거나 다른 실행자가 이어받았습니다.")
            job["updated_at"] = time.time()
            con.execute("UPDATE ai_chat_operation SET payload=?,deadline=?,state=?,claim=? WHERE id=?",
                (self.pack_job(job), None if release else time.time() + 180,
                 "queued" if release else "processing", None if release else claim, op))

    def heartbeat_research(self, chat_id, op, claim):
        with self.db() as con:
            changed = con.execute("UPDATE ai_chat_operation SET deadline=? WHERE id=? AND chat_id=? AND claim=? "
                "AND state='processing' AND deadline>? AND EXISTS(SELECT 1 FROM ai_chat WHERE id=? AND archived=0)",
                (time.time() + 180, op, chat_id, claim, time.time(), chat_id)).rowcount
            if not changed:
                raise ChatConflict("리서치 작업이 중단되었거나 실행권이 만료되었습니다.")

    def stop_research(self, chat_id, op, state, *, claim=None, job=None):
        if state not in {"paused", "cancelled", "failed"}:
            raise ValueError("invalid research state")
        with self.db() as con:
            row = con.execute("SELECT * FROM ai_chat_operation WHERE id=? AND chat_id=? AND payload IS NOT NULL", (op, chat_id)).fetchone()
            if not row or (claim is not None and (row["claim"] != claim or row["state"] != "processing" or row["deadline"] <= time.time())):
                raise ChatConflict("리서치 작업 상태가 변경되었습니다.")
            if row["state"] == "completed":
                raise ChatConflict("완료된 보고서는 중단할 수 없습니다.")
            payload = self.pack_job(job) if job is not None else row["payload"]
            con.execute("UPDATE ai_chat_operation SET state=?,claim=NULL,deadline=NULL,payload=? WHERE id=?", (state, payload, op))

    def resume_research(self, chat_id, op):
        with self.db() as con:
            row = con.execute("SELECT * FROM ai_chat_operation WHERE id=? AND chat_id=? AND payload IS NOT NULL", (op, chat_id)).fetchone()
            if not row or row["state"] not in {"paused", "failed"}:
                raise ChatConflict("이어갈 수 있는 리서치 작업이 없습니다.")
            if con.execute("SELECT 1 FROM ai_chat_operation WHERE chat_id=? AND id!=? AND "
                           "(state='queued' OR state='processing' AND (deadline>? OR payload IS NOT NULL))", (chat_id, op, time.time())).fetchone():
                raise ChatConflict("다른 요청이 처리 중입니다.")
            chat = con.execute("SELECT revision,archived FROM ai_chat WHERE id=?", (chat_id,)).fetchone()
            job = self.unpack_job(row["payload"])
            if not chat or chat["archived"] or chat["revision"] != job["request"]["expected_revision"]:
                raise ChatConflict("대화가 변경되어 이 작업을 이어갈 수 없습니다. 새 리서치를 시작하세요.")
            job["error"] = ""
            job["step_failures"] = 0
            job["updated_at"] = time.time()
            job["budget_started_at"] = time.time()
            con.execute("UPDATE ai_chat_operation SET state='queued',claim=NULL,deadline=NULL,payload=? WHERE id=?", (self.pack_job(job), op))

    def archive(self, chat_id, op, archived):
        op = operation_id(op)
        if not isinstance(archived, bool):
            raise ValueError("archived must be boolean")
        request_hash = hashlib.sha256(canonical([chat_id, archived]).encode()).hexdigest()
        with self.db() as con:
            row = con.execute("SELECT id FROM ai_chat WHERE id=?", (chat_id,)).fetchone()
            if not row:
                raise FileNotFoundError("대화를 찾을 수 없습니다.")
            old = con.execute("SELECT * FROM ai_chat_operation WHERE id=?", (op,)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ChatConflict("같은 요청 ID의 내용이 달라졌습니다.")
                return
            if con.execute("SELECT 1 FROM ai_chat_operation WHERE chat_id=? AND (state='queued' OR state='processing' AND (deadline>? OR payload IS NOT NULL))", (chat_id, time.time())).fetchone():
                raise ChatConflict("응답 생성이 끝난 뒤 보관하세요.")
            con.execute("UPDATE ai_chat SET archived=?,updated=? WHERE id=?", (int(archived), time.time(), chat_id))
            con.execute("INSERT INTO ai_chat_operation VALUES (?,?,?,'archive','completed',NULL,NULL,NULL)", (op, chat_id, request_hash))
