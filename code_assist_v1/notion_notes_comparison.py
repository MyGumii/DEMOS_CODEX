"""Opt-in, encrypted host-only note experiments; no result/body HTTP surface."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import UUID, uuid4

from cryptography.fernet import Fernet

from code_assist_v1 import notion_ai_notes as notes
from code_assist_v1.notion_notes_knowledge import KnowledgeReader, search_query

VERSION = "notion-notes-comparison-v1"
STATES = {"queued": "비교 처리 대기 중", "processing": "호스트에서 비교 처리 중", "completed": "비교 기록 저장 완료 · 결과는 호스트에만 보관됩니다.",
          "failed": "비교 처리 실패 · 호스트 기록을 확인하세요.", "no_evidence": "관련 지식이 없어 비교 요약을 생성하지 않았습니다.", "interrupted": "호스트 작업이 중단되었습니다. 다시 요청하세요."}


class ComparisonStore:
    def __init__(self, path, secret):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("comparison store path must be absolute")
        secret = secret.encode() if isinstance(secret, str) else secret
        if not secret or len(secret) < 16:
            raise ValueError("a persistent strong host signing key is required")
        self.key = hashlib.sha256(b"notion-notes-comparison-v1\0" + secret).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(self.key))

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE IF NOT EXISTS comparison_meta (version TEXT PRIMARY KEY, proof BLOB NOT NULL)")
            existing = con.execute("SELECT * FROM comparison_meta").fetchall()
            if existing and (len(existing) != 1 or existing[0]["version"] != VERSION or self.cipher.decrypt(existing[0]["proof"]) != VERSION.encode()):
                raise ValueError("comparison schema/key mismatch")
            if not existing:
                con.execute("INSERT INTO comparison_meta VALUES (?,?)", (VERSION, self.cipher.encrypt(VERSION.encode())))
            con.execute("""CREATE TABLE IF NOT EXISTS comparison_run (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, claim TEXT, lease_until REAL,
                payload BLOB NOT NULL, UNIQUE(owner,operation_id))""")

    def owner(self, user_id):
        return hmac.new(self.key, user_id.encode(), hashlib.sha256).hexdigest()

    def pack(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        if len(raw) > 2_000_000:
            raise ValueError("comparison record too large")
        return self.cipher.encrypt(raw)

    def submit(self, user_id, operation_id, data):
        if str(UUID(operation_id)) != operation_id:
            raise ValueError("operation_id must be a lowercase UUID")
        owner = self.owner(user_id)
        request_data = {key: value for key, value in data.items() if key != "candidate_models"}
        request_hash = hmac.new(self.key, json.dumps(request_data, ensure_ascii=False, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        payload = self.pack({"input": data, "user_id": user_id, "experiment_version": VERSION})
        now, run_id = time.time(), str(uuid4())
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT * FROM comparison_run WHERE owner=? AND operation_id=?", (owner, operation_id)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("같은 요청 ID의 내용이 변경되었습니다. 새 비교를 요청하세요.")
                return self.public(old)
            count, total = con.execute("SELECT count(*),coalesce(sum(length(payload)),0) FROM comparison_run").fetchone()
            queued = con.execute("SELECT count(*) FROM comparison_run WHERE owner=? AND state IN ('queued','processing')", (owner,)).fetchone()[0]
            if count >= 100 or total + len(payload) > 256 * 1024 * 1024 or queued >= 10:
                raise notes.NotesUnavailable("호스트 비교 기록 또는 대기열 한도에 도달했습니다. 운영자에게 문의하세요.")
            con.execute("INSERT INTO comparison_run VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, owner, operation_id, request_hash, "queued", now, now, None, None, payload))
        return {"id": run_id, "state": "queued", "message": STATES["queued"]}

    @staticmethod
    def public(row):
        return {"id": row["id"], "state": row["state"], "message": STATES[row["state"]]}

    def status(self, user_id, run_id):
        with self._db() as con:
            row = con.execute("SELECT id,state FROM comparison_run WHERE id=? AND owner=?", (run_id, self.owner(user_id))).fetchone()
        if row is None:
            raise FileNotFoundError("비교 기록을 찾을 수 없습니다.")
        return self.public(row)

    def claim(self):
        now, token = time.time(), str(uuid4())
        with self._db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE comparison_run SET state='interrupted',updated=? WHERE state='processing' AND lease_until<?", (now, now))
            row = con.execute("SELECT * FROM comparison_run WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            con.execute("UPDATE comparison_run SET state='processing',claim=?,lease_until=?,updated=? WHERE id=?", (token, now+1800, now, row["id"]))
        return row["id"], token, json.loads(self.cipher.decrypt(row["payload"]))

    def assert_claim(self, run_id, token):
        with self._db() as con:
            row = con.execute("SELECT 1 FROM comparison_run WHERE id=? AND claim=? AND state='processing' AND lease_until>?", (run_id, token, time.time())).fetchone()
        if not row:
            raise PermissionError("comparison claim expired")

    def finish(self, run_id, token, state, record):
        payload = self.pack(record)
        with self._db() as con:
            con.execute("UPDATE comparison_run SET state=?,payload=?,updated=? WHERE id=? AND claim=? AND state='processing' AND lease_until>?",
                        (state, payload, time.time(), run_id, token, time.time()))

    def host_records(self):
        # Host-side offline export only. Never expose this method through an HTTP route.
        with self._db() as con:
            for row in con.execute("SELECT * FROM comparison_run ORDER BY created"):
                yield {**self.public(row), "created_at": row["created"], "updated_at": row["updated"],
                       **json.loads(self.cipher.decrypt(row["payload"]))}


class ComparisonService:
    def __init__(self, app, store, reader, page_guard):
        self.app, self.store, self.reader, self.page_guard = app, store, reader, page_guard
        self.wake = threading.Event()
        self.stop = threading.Event()

    def submit(self, user_id, data):
        if data.get("comparison_consent") is not True:
            raise ValueError("호스트 전용 비교 데이터 저장 안내를 확인하세요.")
        clean = notes.validate_request(data)
        if clean["summary_phase"] != "final":
            raise ValueError("비교 수집은 최종 원문으로만 실행합니다.")
        clean.update({field: notes.text_field(data, field, limit) for field, limit in (
            ("page_id", 160), ("workspace_id", 160), ("knowledge_space_id", 160), ("knowledge_query", 2000))})
        self.page_guard(user_id, clean["page_id"], clean["workspace_id"])
        self.reader.authorize(user_id, clean["knowledge_space_id"])
        models = notes.available_models()
        if clean["model_id"]:
            models = [model for model in models if model["id"] == clean["model_id"]]
        if not models:
            raise notes.NotesUnavailable("비교할 수 있는 연결된 AI 모델이 없습니다.")
        clean["candidate_models"] = [item["id"] for item in models[:3]]
        clean["collection_notice_version"] = VERSION
        clean["comparison_consent"] = True
        response = self.store.submit(user_id, notes.text_field(data, "operation_id", 36), clean)
        self.wake.set()
        return response

    def process_one(self):
        work = self.store.claim()
        if not work:
            return False
        run_id, token, record = work
        uid, data = record["user_id"], record["input"]
        snapshot = None

        def guard():
            self.store.assert_claim(run_id, token)
            self.page_guard(uid, data["page_id"], data["workspace_id"])
            self.reader.authorize(uid, data["knowledge_space_id"])
            if snapshot is not None:
                self.reader.revalidate(uid, data["knowledge_space_id"], snapshot)

        try:
            guard()
            query = search_query(data)
            if not query:
                raise ValueError("검색어를 지정하세요.")
            snapshot = self.reader.retrieve(uid, data["knowledge_space_id"], query)
            record.update(knowledge=snapshot, retrieval_query=query, attempts=[])
            if not snapshot["items"]:
                guard(); self.store.finish(run_id, token, "no_evidence", record)
                return True
            for model_id in data["candidate_models"]:
                guard()
                try:
                    start = time.monotonic()
                    baseline = notes.summarize({**data, "model_id": model_id}, guard=guard)
                    baseline_ms = round((time.monotonic()-start)*1000)
                    start = time.monotonic()
                    enriched = notes.summarize({**data, "model_id": model_id}, guard=guard, references=snapshot["items"])
                    enriched_ms = round((time.monotonic()-start)*1000)
                    if baseline["model_id"] != model_id or enriched["model_id"] != model_id:
                        raise notes.NotesUnavailable("comparison model mismatch")
                    guard()
                    record.update(baseline=baseline, enriched=enriched, model_id=model_id,
                                  baseline_ms=baseline_ms, enriched_ms=enriched_ms, temperature=0.1,
                                  quality_verified=False, baseline_order="first")
                    self.store.finish(run_id, token, "completed", record)
                    return True
                except notes.NotesUnavailable:
                    record["attempts"].append({"model_id": model_id, "status": "pair_failed"})
            raise notes.NotesUnavailable("comparison pair failed")
        except Exception:
            # No raw exception, token, endpoint, or model body enters logs or browser status.
            record["failure"] = "comparison_failed_or_authority_changed"
            self.store.finish(run_id, token, "failed", record)
        return True

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    with self.app.app_context():
                        if self.process_one():
                            continue
                except Exception:
                    pass  # encrypted DB/job state remains the operational evidence
                self.wake.wait(2); self.wake.clear()
        self.thread = threading.Thread(target=run, name="notion-notes-comparison", daemon=True)
        self.thread.start()


def register(app, page_guard):
    enabled = os.environ.get("CODE_ASSIST_NOTION_NOTES_COMPARISONS", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("CODE_ASSIST_NOTION_NOTES_COMPARISONS must be 0 or 1")
    if enabled == "0":
        return
    path = os.environ.get("CODE_ASSIST_NOTION_NOTES_COMPARISON_DB", "")
    if not path:
        raise ValueError("configure an absolute private host comparison database path")
    store = ComparisonStore(path, app.secret_key)
    store.initialize()
    service = ComparisonService(app, store, KnowledgeReader(app), page_guard)
    app.extensions[VERSION] = service
    service.start()
