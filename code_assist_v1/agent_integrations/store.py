"""Versioned auxiliary ledger; Wiki publication stays in canonical services.

Every local mutation commits its audit/outbox entry and replay result together.
No network/model call occurs in these transactions. Revisions and source bytes
are immutable; work claims use expiring leases and monotonic fencing tokens.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4

from .contracts import IntegrationError, digest, encoded, integer, text, uid

DDL = (
    "CREATE TABLE integration_schema(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
    "CREATE TABLE agent_connection(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, scope_epoch INTEGER NOT NULL, name TEXT NOT NULL, config_json TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER, row_version INTEGER NOT NULL DEFAULT 1)",
    "CREATE INDEX agent_connection_owner ON agent_connection(owner_id,created_at,id)",
    "CREATE TABLE integration_operation(owner_id TEXT NOT NULL, actor_key TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY(owner_id,actor_key,operation_id))",
    "CREATE TABLE integration_run(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES agent_connection(id), external_key TEXT, source_hash TEXT NOT NULL, submission_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','processing','review','ready','publishing','published','rejected','failed')), draft_json TEXT, draft_hash TEXT, row_version INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error_code TEXT, publication_json TEXT, notion_json TEXT)",
    "CREATE UNIQUE INDEX integration_external_dedup ON integration_run(connection_id,external_key,source_hash) WHERE external_key IS NOT NULL",
    "CREATE INDEX integration_run_queue ON integration_run(state,lease_until,created_at)",
    "CREATE INDEX integration_run_owner ON integration_run(owner_id,created_at,id)",
    "CREATE TABLE integration_evidence(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES integration_run(id),action_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,actor_id TEXT NOT NULL,created_at INTEGER NOT NULL)",
    "CREATE TABLE integration_event(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id TEXT NOT NULL,actor_key TEXT NOT NULL,resource_id TEXT NOT NULL,kind TEXT NOT NULL,payload_json TEXT NOT NULL,created_at INTEGER NOT NULL, delivered_at INTEGER)",
    "CREATE TABLE integration_cursor(connection_id TEXT NOT NULL REFERENCES agent_connection(id),name TEXT NOT NULL,value_json TEXT NOT NULL,row_version INTEGER NOT NULL,PRIMARY KEY(connection_id,name))",
)
SCHEMA_HASH = hashlib.sha256("\n".join(DDL).encode()).hexdigest()


def now_ms():
    return time.time_ns() // 1000000


class IntegrationStore:
    def __init__(self, path, key):
        self.path = Path(path)
        self.key = key.encode() if isinstance(key, str) else key
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("A server-owned signing key is required.")

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    def initialize(self):
        """Serialized composition/deployment migration, never a request migration."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("BEGIN IMMEDIATE")
            try:
                exists = c.execute("SELECT name FROM sqlite_master WHERE name='integration_schema'").fetchone()
                if not exists:
                    if c.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
                        raise RuntimeError("Refusing a nonempty unknown integration database.")
                    for sql in DDL:
                        c.execute(sql)
                    c.execute("INSERT INTO integration_schema VALUES(1,?)", (SCHEMA_HASH,))
                elif tuple(c.execute("SELECT version,checksum FROM integration_schema").fetchone()) != (1, SCHEMA_HASH):
                    raise RuntimeError("Integration schema version/checksum mismatch.")
                actual = {r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")}
                if actual != set(DDL):
                    raise RuntimeError("Integration physical schema mismatch.")
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def event(self, c, owner, actor, resource, kind, payload):
        c.execute("INSERT INTO integration_event(owner_id,actor_key,resource_id,kind,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                  (owner, actor, resource, kind, encoded(payload), now_ms()))

    def mutate(self, owner, actor, operation_id, request, apply, revalidate):
        uid(operation_id, "operation_id")
        request_hash = digest(request)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                revalidate()
                replay = c.execute("SELECT request_hash,result_json FROM integration_operation WHERE owner_id=? AND actor_key=? AND operation_id=?", (owner, actor, operation_id)).fetchone()
                if replay:
                    if replay[0] != request_hash:
                        raise IntegrationError("같은 작업 ID에 다른 요청이 사용되었습니다.", 409, "IDEMPOTENCY_CONFLICT")
                    result = json.loads(replay[1])
                else:
                    result = apply(c)
                    c.execute("INSERT INTO integration_operation VALUES(?,?,?,?,?)", (owner, actor, operation_id, request_hash, encoded(result)))
                c.execute("COMMIT")
                return result
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def _token(self, connection_id):
        # Reproduce for an authenticated issue retry or owner reveal; never persist.
        mac = hmac.new(self.key, ("agent-token-v1:"+connection_id).encode(), hashlib.sha256).hexdigest()
        return "sbai_" + connection_id + "." + mac

    def reserve_effect(self, owner, cid, operation_id, request, choose, revalidate):
        def apply(c):
            intent = choose(c)
            self.event(c, owner, cid, operation_id, "effect.reserved", {"request": request})
            return {"pending": True, "intent": intent}
        return self.mutate(owner, cid, operation_id, {"effect": request}, apply, revalidate)

    def finish_effect(self, owner, cid, operation_id, result, revalidate):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            revalidate()
            row = c.execute("SELECT result_json FROM integration_operation WHERE owner_id=? AND actor_key=? AND operation_id=?", (owner, cid, operation_id)).fetchone()
            receipt = json.loads(row[0])
            if receipt.get("pending"):
                c.execute("UPDATE integration_operation SET result_json=? WHERE owner_id=? AND actor_key=? AND operation_id=?",
                          (encoded({"pending": False, "result": result}), owner, cid, operation_id))
                self.event(c, owner, cid, operation_id, "effect.recorded", {})
            else:
                result = receipt["result"]
            c.execute("COMMIT")
            return result

    def public_connection(self, row):
        return dict(id=row["id"], **json.loads(row["config_json"]),
                    created_at=row["created_at"], expires_at=row["expires_at"], revoked_at=row["revoked_at"],
                    row_version=row["row_version"])

    def create_connection(self, owner, epoch, operation_id, config, revalidate):
        def apply(c):
            cid, now = str(uuid4()), now_ms()
            token = self._token(cid)
            c.execute("INSERT INTO agent_connection(id,owner_id,scope_epoch,name,config_json,token_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                      (cid, owner, epoch, config["name"], encoded(config), hashlib.sha256(token.encode()).hexdigest(), now, now+config["expires_days"]*86400000))
            self.event(c, owner, "user", cid, "connection.created", {"scopes": config["scopes"]})
            return {"id": cid}
        result = self.mutate(owner, "user", operation_id, {"create_connection": config}, apply, revalidate)
        row = self.require_connection(result["id"], owner)
        if row["revoked_at"] is not None or row["expires_at"] <= now_ms():
            raise IntegrationError("폐기되거나 만료된 연결입니다.", 403, "CONNECTION_REVOKED")
        return {**self.public_connection(row), "token": self._token(row["id"])}

    def require_connection(self, cid, owner=None, *, c=None):
        uid(cid)
        if c is None:
            with self.connection() as conn:
                return self.require_connection(cid, owner, c=conn)
        row = c.execute("SELECT * FROM agent_connection WHERE id=?", (cid,)).fetchone()
        if row is None or (owner is not None and row["owner_id"] != owner):
            raise IntegrationError("연결을 찾을 수 없습니다.", 404, "NOT_FOUND")
        return dict(row)

    def authenticate(self, raw):
        if not isinstance(raw, str) or len(raw) > 150:
            raise IntegrationError("유효한 에이전트 토큰이 필요합니다.", 401, "AUTHENTICATION_REQUIRED")
        with self.connection() as c:
            row = c.execute("SELECT * FROM agent_connection WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
        if row is None or row["revoked_at"] is not None or row["expires_at"] <= now_ms():
            raise IntegrationError("유효한 에이전트 토큰이 필요합니다.", 401, "AUTHENTICATION_REQUIRED")
        return dict(row)

    def list_connections(self, owner):
        with self.connection() as c:
            return [self.public_connection(r) for r in c.execute("SELECT * FROM agent_connection WHERE owner_id=? ORDER BY created_at DESC,id DESC LIMIT 100", (owner,))]

    def reveal_token(self, owner, cid, operation_id, revalidate):
        """Audit an owner reveal without placing the secret in replay/audit rows."""
        uid(cid)
        token = None
        def verify():
            nonlocal token
            # mutate holds the grant DB's write fence, including on replay.
            revalidate()
            row = self.require_connection(cid, owner)
            if row["revoked_at"] is not None or row["expires_at"] <= now_ms():
                raise IntegrationError("폐기되거나 만료된 연결입니다.", 403, "CONNECTION_REVOKED")
            token = self._token(cid)
            if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row["token_hash"]):
                raise IntegrationError("기존 토큰을 복원할 수 없습니다. 새 연결을 만들어 주세요.", 503, "TOKEN_UNAVAILABLE")
        def apply(c):
            self.event(c, owner, "user", cid, "connection.token_viewed", {})
            return {"id": cid}
        result = self.mutate(owner, "user", operation_id, {"reveal_token": cid}, apply, verify)
        return {"id": result["id"], "token": token}

    def revoke(self, owner, cid, operation_id, revalidate):
        def apply(c):
            row = self.require_connection(cid, owner, c=c)
            c.execute("UPDATE agent_connection SET revoked_at=COALESCE(revoked_at,?),row_version=row_version+1 WHERE id=?", (now_ms(), cid))
            self.event(c, owner, "user", cid, "connection.revoked", {})
            return {"id": cid, "revoked": True}
        return self.mutate(owner, "user", operation_id, {"revoke": cid}, apply, revalidate)

    def renew_after_use(self, owner, cid, revalidate):
        """Sliding expiration only after a successful authenticated machine call."""
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            revalidate()
            row = self.require_connection(cid, owner, c=c)
            now = now_ms()
            config = json.loads(row["config_json"])
            if row["revoked_at"] is not None or row["expires_at"] <= now:
                raise IntegrationError("폐기되거나 만료된 연결입니다.", 403, "CONNECTION_REVOKED")
            expires = row["expires_at"]
            if config.get("auto_renew", False):
                expires = max(expires, now + config["expires_days"] * 86400000)
                if expires > row["expires_at"]:
                    c.execute("UPDATE agent_connection SET expires_at=?,row_version=row_version+1 WHERE id=?", (expires, cid))
                    self.event(c, owner, cid, cid, "connection.renewed", {"expires_at": expires})
            c.execute("COMMIT")
            return expires

    def set_renewal(self, owner, cid, operation_id, expected_version, enabled, revalidate):
        integer(expected_version, "연결 버전", 1, 2147483647)
        if type(enabled) is not bool:
            raise IntegrationError("자동 연장 설정이 올바르지 않습니다.")
        def apply(c):
            row = self.require_connection(cid, owner, c=c)
            if row["row_version"] != expected_version:
                raise IntegrationError("연결이 변경되었습니다. 목록을 새로 불러와 주세요.", 409, "REVISION_CONFLICT")
            config = json.loads(row["config_json"])
            config["auto_renew"] = enabled
            c.execute("UPDATE agent_connection SET config_json=?,row_version=row_version+1 WHERE id=?", (encoded(config), cid))
            self.event(c, owner, "user", cid, "connection.renewal_changed", {"auto_renew": enabled})
            return dict(id=cid, auto_renew=enabled)
        return self.mutate(owner, "user", operation_id,
            {"renewal": cid, "expected_version": expected_version, "auto_renew": enabled}, apply, revalidate)

    def submit(self, owner, cid, operation_id, submission, revalidate):
        def apply(c):
            self.require_connection(cid, owner, c=c)
            source_hash = digest(submission)
            key = submission["external_key"] or None
            prior = c.execute("SELECT id,state FROM integration_run WHERE connection_id=? AND external_key=? AND source_hash=?", (cid, key, source_hash)).fetchone() if key else None
            if prior:
                return {"id": prior[0], "state": prior[1], "deduplicated": True}
            rid, now = str(uuid4()), now_ms()
            c.execute("INSERT INTO integration_run(id,owner_id,connection_id,external_key,source_hash,submission_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)",
                      (rid, owner, cid, key, source_hash, encoded(submission), now, now))
            self.event(c, owner, cid, rid, "run.submitted", {"source_hash": source_hash})
            return {"id": rid, "state": "queued", "deduplicated": False}
        return self.mutate(owner, cid, operation_id, {"submit": submission}, apply, revalidate)

    def run(self, owner, rid, *, cid=None, c=None):
        uid(rid)
        if c is None:
            with self.connection() as conn:
                return self.run(owner, rid, cid=cid, c=conn)
        row = c.execute("SELECT * FROM integration_run WHERE id=? AND owner_id=?", (rid, owner)).fetchone()
        if row is None or (cid is not None and row["connection_id"] != cid):
            raise IntegrationError("작업을 찾을 수 없습니다.", 404, "NOT_FOUND")
        result = dict(row)
        for field in ("submission", "draft", "publication", "notion"):
            result[field] = json.loads(result.pop(field+"_json")) if result[field+"_json"] else None
        result.pop("lease_token", None)
        result["evidence"] = [dict(r) for r in c.execute("SELECT * FROM integration_evidence WHERE run_id=? ORDER BY created_at,id", (rid,))]
        result["action_statuses"] = []
        for action in result["submission"]["actions"]:
            kinds = {e["kind"] for e in result["evidence"] if e["action_id"]==action["id"]}
            result["action_statuses"].append(dict(action_id=action["id"],
                approval="user_approved" if "approved" in kinds else "unknown",
                execution="user_reported_complete" if "user_reported_complete" in kinds else "unknown",
                reply_observed="provider_observed_reply" in kinds,
                outcome="user_reported" if "outcome_reported" in kinds else "unknown",
                cancelled="cancelled" in kinds,
                needs_review="cancelled" in kinds and bool(kinds & {"user_reported_complete","provider_observed_reply"})))
        return result

    def list_runs(self, owner, *, cid=None, cursor=None, limit=30):
        integer(limit, "limit", 1, 100)
        clauses, params = ["owner_id=?"], [owner]
        if cid is not None:
            clauses.append("connection_id=?")
            params.append(uid(cid))
        if cursor:
            anchor = self.run(owner, cursor, cid=cid)
            clauses.append("(created_at<? OR (created_at=? AND id<?))")
            params.extend([anchor["created_at"], anchor["created_at"], anchor["id"]])
        with self.connection() as c:
            rows = c.execute("SELECT id FROM integration_run WHERE "+" AND ".join(clauses)+" ORDER BY created_at DESC,id DESC LIMIT ?", (*params, limit+1)).fetchall()
            items = [self.run(owner, r[0], cid=cid, c=c) for r in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows)>limit else None}

    def claim(self, owner, rid, revalidate, *, lease_ms=900000, expected_version=None):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            revalidate()
            run = self.run(owner, rid, c=c)
            if expected_version is not None and run["row_version"] != expected_version:
                raise IntegrationError("정제할 근거 버전이 변경되었습니다.",409,"REVISION_CONFLICT")
            if run["state"] == "processing" and (run["lease_until"] or 0) > now_ms():
                raise IntegrationError("다른 작업자가 처리 중입니다.", 409, "WORK_IN_PROGRESS")
            if run["state"] not in {"queued", "failed", "processing"}:
                return None
            lease = secrets.token_hex(32)
            generation = run["generation"]+1
            c.execute("UPDATE integration_run SET state='processing',generation=?,lease_token=?,lease_until=?,row_version=row_version+1,error_code=NULL WHERE id=?",
                      (generation, lease, now_ms()+lease_ms, rid))
            self.event(c, owner, run["connection_id"], rid, "run.claimed", {"generation": generation})
            c.execute("COMMIT")
            return lease, generation

    def finish(self, owner, rid, claim, draft, revalidate, *, error=None):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            revalidate()
            row = c.execute("SELECT * FROM integration_run WHERE id=? AND owner_id=?", (rid, owner)).fetchone()
            if row is None or row["state"] != "processing" or row["lease_token"] != claim[0] or row["generation"] != claim[1] or row["lease_until"] <= now_ms():
                raise IntegrationError("작업의 처리 권한이 변경되었습니다.", 409, "STALE_CLAIM")
            state = "failed" if error else "review"
            c.execute("UPDATE integration_run SET state=?,draft_json=?,draft_hash=?,error_code=?,lease_token=NULL,lease_until=NULL,updated_at=?,row_version=row_version+1 WHERE id=?",
                      (state, None if error else encoded(draft), None if error else digest(draft), error, now_ms(), rid))
            self.event(c, owner, row["connection_id"], rid, "run."+state, {"error_code": error})
            c.execute("COMMIT")

    def evidence(self, owner, rid, operation_id, action_id, kind, body, expected_version, revalidate, *, provider_observed=False):
        allowed = {"provider_observed_reply"} if provider_observed else {"approved", "user_reported_complete", "cancelled", "outcome_reported", "note"}
        if kind not in allowed:
            raise IntegrationError("사용자 확인 종류가 올바르지 않습니다.")
        text(body, "확인 내용", 6000)
        integer(expected_version, "expected_version")
        def apply(c):
            run = self.run(owner, rid, c=c)
            if run["row_version"] != expected_version or run["state"] in {"processing", "publishing", "published"}:
                raise IntegrationError("작업 상태가 변경되었습니다. 새로고침해 주세요.", 409, "REVISION_CONFLICT")
            if action_id not in {a["id"] for a in run["submission"]["actions"]}:
                raise IntegrationError("액션을 찾을 수 없습니다.", 404, "NOT_FOUND")
            eid = str(uuid4())
            c.execute("INSERT INTO integration_evidence VALUES(?,?,?,?,?,?,?)", (eid, rid, action_id, kind, body, "provider:microsoft_graph" if provider_observed else owner, now_ms()))
            # Changed evidence invalidates every previously generated/reviewed draft.
            c.execute("UPDATE integration_run SET state='queued',draft_json=NULL,draft_hash=NULL,row_version=row_version+1,updated_at=? WHERE id=?", (now_ms(), rid))
            self.event(c, owner, "user", rid, "action."+kind, {"evidence_id": eid, "action_id": action_id})
            return {"id": rid, "evidence_id": eid, "state": "queued"}
        return self.mutate(owner, "provider:microsoft_graph" if provider_observed else "user", operation_id, {"evidence": [rid, action_id, kind, body, expected_version]}, apply, revalidate)

    def review(self, owner, rid, operation_id, expected_version, draft_hash, decision, revalidate):
        if decision not in {"approve", "reject"}:
            raise IntegrationError("검토 결과가 올바르지 않습니다.")
        def apply(c):
            run = self.run(owner, rid, c=c)
            if run["state"] != "review" or run["row_version"] != expected_version or run["draft_hash"] != draft_hash:
                raise IntegrationError("검토할 내용이 변경되었습니다.", 409, "REVISION_CONFLICT")
            if decision == "approve" and run["draft"]["blockers"]:
                raise IntegrationError("근거 검증에 실패한 후보는 등록할 수 없습니다.", 409, "EVIDENCE_REQUIRED")
            state = "ready" if decision == "approve" else "rejected"
            c.execute("UPDATE integration_run SET state=?,row_version=row_version+1,updated_at=? WHERE id=?", (state, now_ms(), rid))
            self.event(c, owner, "user", rid, "draft."+decision, {"draft_hash": draft_hash})
            return {"id": rid, "state": state}
        return self.mutate(owner, "user", operation_id, {"review": [rid, expected_version, draft_hash, decision]}, apply, revalidate)

    def begin_publish(self, owner, rid, revalidate, *, automatic=False):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            revalidate()
            run = self.run(owner, rid, c=c)
            if run["state"] == "published":
                return run
            if run["state"] == "publishing":
                # The first attempt freezes its rendering mode across crashes,
                # retries and concurrent browser/agent publication requests.
                return run
            allowed = {"ready", "publishing"} | ({"review"} if automatic else set())
            if run["state"] not in allowed or not run["draft"] or run["draft"]["blockers"]:
                raise IntegrationError("먼저 지식 후보를 검토해 주세요.", 409, "REVIEW_REQUIRED")
            if automatic and not run["draft"]["automatic_eligible"]:
                raise IntegrationError("이 후보는 사용자 검토가 필요합니다.", 409, "REVIEW_REQUIRED")
            c.execute("UPDATE integration_run SET state='publishing',publication_json=?,updated_at=? WHERE id=?",
                      (encoded({"intent": {"automatic": automatic}}), now_ms(), rid))
            self.event(c, owner, run["connection_id"], rid, "publication.started", {"draft_hash": run["draft_hash"], "automatic": automatic})
            c.execute("COMMIT")
            return self.run(owner, rid, c=c)

    def record_publication(self, owner, rid, draft_hash, result):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            run = self.run(owner, rid, c=c)
            if run["draft_hash"] != draft_hash or run["state"] not in {"publishing", "published"}:
                raise IntegrationError("등록 대상이 변경되었습니다.", 409, "REVISION_CONFLICT")
            if run["state"] != "published":
                c.execute("UPDATE integration_run SET state='published',publication_json=?,row_version=row_version+1,updated_at=? WHERE id=?", (encoded(result), now_ms(), rid))
                self.event(c, owner, run["connection_id"], rid, "publication.recorded", result)
            c.execute("COMMIT")
        return self.run(owner, rid)
