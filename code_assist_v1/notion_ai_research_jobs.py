"""Durable per-owner research jobs using the existing encrypted operation store.

No schema migration, canonical Wiki mutation, or browser connection is needed.
Claims fence competing processes; source proofs fence every resumed step.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Lock, Thread
import time

from code_assist_v1 import auth, config, notion_meeting_providers as providers
from code_assist_v1.notion_ai_chat_store import ChatConflict, operation_id
from code_assist_v1.notion_ai_deep_research import DeepResearch
from code_assist_v1 import notion_ai_research as research
from code_assist_v1.notion_ai_workspace import _hash
from code_assist_v1.routes_notion_document_ai import field, read_source, fingerprint, DocumentChanged, AiUnavailable


class ResearchBudgetReached(Exception):
    pass


class ResearchJobs:
    def __init__(self, service):
        self.service = service
        self.root = Path(config.USER_DATA_DIR).resolve()
        self.wake = Event()
        self.lock = Lock()
        self.thread = None
        self.last_job = None

    def start(self):
        if self.service.app.config.get("TESTING"):
            return
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = Thread(target=self.loop, name="notion-deep-research", daemon=True)
                self.thread.start()
        self.wake.set()

    def submit(self, uid, cid, data):
        question = field(data, "message", 12000, True)
        mid = field(data, "model_id", 200)
        revision = data.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("expected_revision required")
        op = operation_id(field(data, "operation_id", 36, True))
        options = research.options(data)
        store = self.service.store(uid)
        record = store.get(cid)
        self.service.authorize(uid, record)
        candidates = providers.text_models()
        if mid:
            candidates = [c for c in candidates if c[0] == mid]
        if not candidates:
            raise AiUnavailable("사용할 수 있는 AI 모델이 없습니다.")
        if options["web"] and not self.service.web_reader.capability()["enabled"]:
            raise AiUnavailable("자체 웹 검색 서버가 연결되지 않았습니다.")
        if not any(options.values()):
            raise ValueError("심층 리서치에는 내부 자료 또는 웹 검색이 필요합니다.")
        clean = {"message": question, "mode": "research", "model_id": mid, "source_options": options}
        job = {"contract": "notion-deep-research-v1", "request": {**clean, "expected_revision": revision},
            "created_at": time.time(), "updated_at": time.time(), "budget_started_at": time.time(),
            "scope": record["scope"], "progress": {"stage": "queued", "label": "심층 리서치 작업을 준비하고 있습니다."}, "error": ""}
        store.begin(cid, op, clean, revision, research_job=job)
        self.start()
        return self.service.get(uid, cid)

    def authorize_job(self, uid, record, job, *, versions=False):
        self.service.authorize(uid, record)
        if job["scope"] != record["scope"]:
            raise PermissionError("리서치 참고 범위가 변경되었습니다.")
        for proof in job.get("checkpoint", {}).get("proofs", []):
            kind = proof["kind"]
            if kind == "page":
                current = read_source(uid, proof["id"], record["scope"]["workspace_id"])
                if versions and fingerprint(current) != proof["digest"]:
                    raise DocumentChanged("조사에 사용한 페이지가 변경되었습니다. 새 리서치를 시작하세요.")
            elif kind == "knowledge":
                self.service.reader.revalidate(uid, record["scope"]["knowledge_space_id"], proof["snapshot"])
            elif kind == "workspace":
                self.service.workspace_reader.authorize_history(uid, {"owner_user_id": uid, "page_ids": proof["page_ids"]})
                if versions:
                    pages, bodies = self.service.workspace_reader._read(uid, body_ids=tuple(proof["body_hashes"]))
                    if any(_hash(bodies.get(pid, "")) != digest for pid, digest in proof["body_hashes"].items()):
                        raise DocumentChanged("조사에 사용한 Notion 자료가 변경되었습니다. 새 리서치를 시작하세요.")
            elif kind == "database":
                for pid, digest in proof["payloads"].items():
                    current = research._own_page(uid, pid)
                    if versions and _hash(current) != digest:
                        raise DocumentChanged("조사에 사용한 DB 또는 행이 변경되었습니다. 새 리서치를 시작하세요.")

    @staticmethod
    def public(row):
        job, cp = row["job"], row["job"].get("checkpoint", {})
        finished = {s['id'] for s in cp.get('sections', [])}
        chapters = list(cp.get('sections', [])) + [
            {**p, 'title':s['title']+' — '+p['title']} for s in cp.get('plan', {}).get('sections', [])
            if s['id'] not in finished for p in s.get('parts', [])]
        # Never expose retained source bodies, plans' DB metadata, secrets or claims.
        return {"operation_id": row["id"], "state": row["state"], "request": job["request"]["message"],
            "source_options": job["request"]["source_options"], "progress": job.get("progress", {}),
            "created_at": job["created_at"], "updated_at": job["updated_at"], "error": job.get("error", ""),
            "draft": job.get("draft"), "chapters": [{"title": s["title"], "text": s["answer"], "verification": s["verification"]}
                                                       for s in chapters],
            "coverage": cp.get("coverage", []), "limitations": cp.get("limitations", [])}

    def control(self, uid, cid, data):
        store = self.service.store(uid)
        op = operation_id(field(data, "operation_id", 36, True))
        row = store.research_job(cid, op)
        if not row:
            raise FileNotFoundError("리서치 작업을 찾을 수 없습니다.")
        record = store.get(cid)
        self.authorize_job(uid, record, row["job"], versions=data.get("action") == "resume")
        action = data.get("action")
        if action == "resume":
            store.resume_research(cid, op)
            self.start()
        elif action in {"pause", "cancel"}:
            store.stop_research(cid, op, "paused" if action == "pause" else "cancelled")
        else:
            raise ValueError("지원하지 않는 리서치 동작입니다.")
        return self.service.get(uid, cid)

    def run_one(self, uid, cid, op):
        store = self.service.store(uid)
        claimed = store.claim_research(cid, op)
        if not claimed:
            return False
        claim, job = claimed
        record = store.get(cid)
        last_heartbeat = [0.0]
        started = time.time()
        def guard():
            store.assert_claim(cid, op, claim)
            if not self.service.app.config.get("TESTING"):
                users = auth._load_users().get("users", {})
                if uid not in users or users[uid].get("disabled"):
                    raise PermissionError("계정의 접근 권한이 변경되었습니다.")
            self.authorize_job(uid, record, job, versions=True)
            if time.time() - job["budget_started_at"] > max(900, min(21600, int(os.environ.get("CODE_ASSIST_RESEARCH_MAX_SECONDS", "7200")))):
                raise ResearchBudgetReached()
            if time.monotonic() - last_heartbeat[0] > 20:
                store.heartbeat_research(cid, op, claim)
                last_heartbeat[0] = time.monotonic()
        def persist():
            guard()
            store.checkpoint_research(cid, op, claim, job)
        try:
            candidates = providers.text_models()
            mid = job["request"]["model_id"]
            if mid:
                candidates = [c for c in candidates if c[0] == mid]
            if not candidates:
                raise AiUnavailable("선택한 모델의 연결이 해제되었습니다.")
            with self.service.app.app_context():
                guard()
                if job.get("result"):
                    result = job["result"]
                else:
                    engine = DeepResearch(self.service, uid, record, job, candidates[0], guard, persist)
                    result = engine.step()
                    if result is not None:
                        job["result"] = result
                        persist()
                if result is not None:
                    guard()
                    store.finish(cid, op, claim, job["request"]["message"], result)
                else:
                    job["step_failures"] = 0
                    job["error"] = ""
                    job["active_seconds"] = job.get("active_seconds", 0) + time.time() - started
                    store.checkpoint_research(cid, op, claim, job, release=True)
        except ResearchBudgetReached:
            job["error"] = "조사 시간 한도에 도달했습니다. 중간 결과를 저장했으며 ‘이어 조사’로 계속할 수 있습니다."
            store.stop_research(cid, op, "paused", claim=claim, job=job)
        except ChatConflict:
            pass  # User pause/cancel or a fenced stale process must not overwrite the winner.
        except Exception as exc:
            job.pop("draft", None)
            safe_codes = {"unexpected research value", "invalid research object", "invalid research list",
                "invalid research text", "invalid research boolean", "research step context exceeds model capacity",
                "research step response too large", "unverified research quote", "chapter has unresolved evidence markers"}
            safe_codes.update({'provider answer was incomplete','provider HTTP request failed',
                               'provider omitted completion status','configured provider credential is unavailable',
                               'provider reasoning control unavailable'})
            reason = str(exc) if str(exc) in safe_codes else "provider_or_validation_error"
            self.service.app.logger.warning("Research step failed: %s (%s), phase=%s, stage=%s",
                type(exc).__name__, reason, job.get('checkpoint',{}).get('phase'), job.get('progress',{}).get('stage'))
            if not isinstance(exc, (DocumentChanged, PermissionError)):
                job["step_failures"] = job.get("step_failures", 0) + 1
                if job["step_failures"] <= 2:
                    try:
                        job["error"] = "현재 조사 단계의 응답을 다시 확인하고 있습니다."
                        store.checkpoint_research(cid, op, claim, job, release=True)
                        return True
                    except ChatConflict:
                        return True
            job["error"] = str(exc) if isinstance(exc, (DocumentChanged, PermissionError, AiUnavailable)) else "이 조사 단계의 응답을 확인하지 못했습니다. 중간 결과는 보관되어 있으며 ‘이어 조사’로 다시 시도할 수 있습니다."
            try:
                store.stop_research(cid, op, "failed", claim=claim, job=job)
            except ChatConflict:
                pass
        return True

    def run_next(self):
        ready = []
        for path in sorted(self.root.glob("*/notion_ai/chats.db")):
            uid = path.parent.parent.name
            if auth.safe_user_id(uid) != uid or not path.resolve().is_relative_to(self.root):
                continue
            store = self.service.store(uid)
            for cid, op in store.queued_research():
                ready.append((uid, cid, op))
        if self.last_job in ready:
            index = ready.index(self.last_job) + 1
            ready = ready[index:] + ready[:index]
        for uid, cid, op in ready:
            if self.run_one(uid, cid, op):
                self.last_job = (uid, cid, op)
                return True
        return False

    def loop(self):
        while True:
            try:
                if self.run_next():
                    continue
            except Exception:
                self.service.app.logger.exception("Research queue could not advance")
            self.wake.wait(5)
            self.wake.clear()
