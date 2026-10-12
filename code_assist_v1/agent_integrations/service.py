"""Common knowledge loop for Hermes, other agents and user-initiated work."""
import json
from contextlib import contextmanager

from . import authoring
from .contracts import IntegrationError, connection_input, submission_input, uid
from .store import now_ms
from . import knowledge_sets


class IntegrationService(knowledge_sets.KnowledgeSetOperations):
    @contextmanager
    def grant_commit_guard(self, verify):
        """Serialize revocation with the short final canonical publication.

        Model execution and source staging happen before this fence. A revoke
        either wins and prevents commit, or follows the already-started commit.
        """
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            verify()
            yield
            c.execute("COMMIT")

    def __init__(self, store, bridge, *, runner=None, provider_order=None, model_ids=None, notion=None, outlook=None):
        self.store, self.bridge = store, bridge
        if runner is None:
            from ..internal_llm import run_internal_text
            runner = run_internal_text
        self.runner, self.provider_order, self.notion = runner, provider_order, notion
        self.model_ids = model_ids
        self.outlook = outlook

    def observe_reply(self, owner, cid, rid, data):
        from .contracts import encoded
        verify = lambda: self.authorize_connection(owner,cid,"mail:read")
        verify()
        self.authorize_connection(owner,cid,"results:write")
        receipt = self.store.reserve_effect(owner,cid,data["operation_id"],
            {"kind":"observe-outlook-reply","run_id":rid,"data":data},
            lambda c: {"run":self.store.run(owner,rid,cid=cid,c=c)},verify)
        if not receipt["pending"]:
            return receipt["result"]
        if self.outlook is None:
            raise IntegrationError("Outlook 연결이 설정되지 않았습니다.",503,"MAIL_NOT_CONFIGURED")
        run = receipt["intent"]["run"]
        action = next((a for a in run["submission"]["actions"] if a["id"]==data["action_id"]),None)
        source = next((s for s in run["submission"]["sources"] if action and s["id"]==action["source_id"]),None)
        if source is None or source["kind"] != "email" or not source["external_id"]:
            raise IntegrationError("원본 메일 ID가 연결된 액션이 필요합니다.")
        with self.store.connection() as c:
            committed = c.execute("SELECT result_json FROM integration_operation WHERE owner_id=? AND actor_key='provider:microsoft_graph' AND operation_id=?",(owner,data["operation_id"])).fetchone()
        if committed:
            return self.store.finish_effect(owner,cid,data["operation_id"],json.loads(committed[0]),verify)
        observation = self.outlook.observe_reply(owner,source["external_id"],data["candidate_message_id"])
        verify()
        if observation["status"] == "unknown":
            result = observation
        else:
            result = self.store.evidence(owner,rid,data["operation_id"],action["id"],"provider_observed_reply",
                encoded(observation),data["expected_version"],verify,provider_observed=True)
        return self.store.finish_effect(owner,cid,data["operation_id"],result,verify)

    def actor(self, owner):
        return self.bridge.actor(owner)

    def authorize_connection(self, owner, cid, scope=None):
        row = self.store.require_connection(cid, owner)
        actor = self.actor(owner)
        if row["revoked_at"] is not None or row["expires_at"] <= now_ms() or row["scope_epoch"] != actor["scope_epoch"]:
            raise IntegrationError("연결이 만료되었거나 계정 권한이 변경되었습니다. 연결을 다시 생성해 주세요.", 403, "CONNECTION_REVOKED")
        config = json.loads(row["config_json"])
        if scope is not None and scope not in config["scopes"]:
            raise IntegrationError("이 연결에 해당 작업 권한이 없습니다.", 403, "FORBIDDEN")
        if knowledge_sets.enabled(config):
            knowledge_sets.authorize_config(self.bridge, actor, config)
        else:
            self.bridge.authorize(actor, config["read_space_id"])
        return actor, config

    def authenticate(self, token):
        row = self.store.authenticate(token)
        actor, config = self.authorize_connection(row["owner_id"], row["id"])
        return dict(owner_id=actor["id"], connection_id=row["id"], config=config)

    def create_connection(self, owner, body):
        config = connection_input(body)
        actor = self.actor(owner)
        def verify():
            current = self.actor(owner)
            if current["scope_epoch"] != actor["scope_epoch"]:
                raise IntegrationError("계정 권한이 변경되었습니다.", 409, "SCOPE_STALE")
            if knowledge_sets.enabled(config):
                knowledge_sets.authorize_config(self.bridge, current, config, writes=True)
            else:
                self.bridge.authorize(current, config["read_space_id"])
                self.bridge.authorize(current, config["write_space_id"], write=True)
        verify()
        return self.store.create_connection(owner, actor["scope_epoch"], body["operation_id"], config, verify)

    def submit(self, owner, cid, body):
        submission = submission_input(body)
        actor, config = self.authorize_connection(owner, cid, "results:write")
        knowledge_sets.selection(config, submission.get("read_mode"))
        if knowledge_sets.enabled(config):
            previous = self.store.run(owner, submission["followup_of"], cid=cid) if submission["followup_of"] else None
            requested = submission.get("publication_target")
            if requested is None and previous:
                requested = previous["submission"].get("publication_target")
            submission["publication_target"] = knowledge_sets.target_for(self.bridge, actor, config, requested)
            submission["read_mode"] = submission.get("read_mode", previous["submission"].get("read_mode") if previous else None) or config["read_mode"]
        elif "publication_target" in submission:
            knowledge_sets.target_for(self.bridge, actor, config, submission["publication_target"])
        def verify():
            current, current_config = self.authorize_connection(owner, cid, "results:write")
            if "publication_target" in submission:
                knowledge_sets.target_for(self.bridge, current, current_config, submission["publication_target"])
            if submission["followup_of"]:
                self.store.run(owner,submission["followup_of"],cid=cid)
        verify()
        return self.store.submit(owner, cid, body["operation_id"], submission, verify)

    def process(self, owner, rid, *, cid=None):
        run = self.store.run(owner, rid, cid=cid)
        connection_id = run["connection_id"]
        verify = lambda: self.authorize_connection(owner, connection_id, "results:write")
        actor, config = verify()
        if knowledge_sets.enabled(config):
            knowledge_sets.target_for(self.bridge, actor, config, run["submission"].get("publication_target"))
        else:
            self.bridge.authorize(actor, config["write_space_id"], write=True)
        claim = self.store.claim(owner, rid, verify, expected_version=run["row_version"])
        if claim is None:
            return self.store.run(owner, rid, cid=cid)
        try:
            context = (self.knowledge_search(owner, connection_id, run["submission"]["title"][:500],
                mode=run["submission"].get("read_mode"), required_scope="results:write") if knowledge_sets.enabled(config)
                else self.bridge.search(actor, config["read_space_id"], run["submission"]["title"][:500]))
            draft = authoring.generate(run, context, self.runner, self.provider_order, self.model_ids)
            self.store.finish(owner, rid, claim, draft, verify)
        except Exception as exc:
            # Preserve an honest terminal error without putting provider secrets in logs.
            code = getattr(exc, "code", "REFINEMENT_FAILED")
            try:
                self.store.finish(owner, rid, claim, None, verify, error=code)
            except Exception:
                pass  # A revoked/stale claim must never overwrite a newer attempt.
            raise
        run = self.store.run(owner, rid)
        if config["auto_publish_sources"] and run["draft"]["automatic_eligible"]:
            return self.publish(owner, rid, cid=cid, automatic=True)
        return run

    def process_next(self, owner, cid):
        self.authorize_connection(owner, cid, "results:write")
        with self.store.connection() as c:
            row = c.execute("SELECT id FROM integration_run WHERE owner_id=? AND connection_id=? AND (state='queued' OR (state='processing' AND lease_until<=?)) ORDER BY created_at,id LIMIT 1", (owner, cid, now_ms())).fetchone()
        return {"processed": False} if row is None else {"processed": True, "run": self.process(owner, row[0], cid=cid)}

    def effect(self, owner, cid, operation_id, kind, rid=None, *, automatic=False):
        """Freeze selection and snapshots before retryable external work."""
        if kind not in {"process", "process-next", "publish", "notion"}:
            raise IntegrationError("지원하지 않는 작업입니다.")
        scope = "notion:write" if kind == "notion" else "results:write"
        verify = lambda: self.authorize_connection(owner, cid, scope)
        verify()
        def choose(c):
            selected = rid
            if kind == "process-next":
                row = c.execute("SELECT id FROM integration_run WHERE owner_id=? AND connection_id=? AND (state='queued' OR (state='processing' AND lease_until<=?)) ORDER BY created_at,id LIMIT 1", (owner,cid,now_ms())).fetchone()
                selected = row[0] if row else None
            return {"run": self.store.run(owner, selected, cid=cid, c=c) if selected else None}
        receipt = self.store.reserve_effect(owner,cid,operation_id,{"kind":kind,"run_id":rid,"automatic":automatic},choose,verify)
        if not receipt["pending"]:
            return receipt["result"]
        snapshot = receipt["intent"]["run"]
        if snapshot is None:
            result = {"processed":False}
        elif kind in {"process", "process-next"}:
            current = self.store.run(owner,snapshot["id"],cid=cid)
            if current["evidence"] != snapshot["evidence"]:
                raise IntegrationError("정제 근거가 변경되었습니다. 새 작업 ID로 처리해 주세요.",409,"REVISION_CONFLICT")
            result = self.process(owner,snapshot["id"],cid=cid)
            if kind == "process-next":
                result = {"processed":True,"run":result}
        elif kind == "publish":
            result = self.publish(owner,rid,cid=cid,automatic=automatic)
        else:
            result = self.record_notion(owner,rid,cid=cid,snapshot=snapshot)
        return self.store.finish_effect(owner,cid,operation_id,result,verify)

    def publish(self, owner, rid, *, cid=None, automatic=False):
        run = self.store.run(owner, rid, cid=cid)
        def verify():
            actor, config = self.authorize_connection(owner, run["connection_id"], "results:write")
            if knowledge_sets.enabled(config):
                knowledge_sets.target_for(self.bridge, actor, config, run["submission"].get("publication_target"))
            return actor, config
        actor, config = verify()
        if automatic and not config["auto_publish_sources"]:
            raise IntegrationError("자동 등록이 허용되지 않은 연결입니다.", 403, "FORBIDDEN")
        if not knowledge_sets.enabled(config):
            self.bridge.authorize(actor, config["write_space_id"], write=True)
        run = self.store.begin_publish(owner, rid, verify, automatic=automatic)
        if run["state"] == "published":
            return run
        automatic = run["publication"]["intent"]["automatic"]
        # Retry IDs are derived from the immutable run and draft, never a model value.
        markdown = authoring.render(run, automatic=automatic)
        target = (run["submission"]["publication_target"] if knowledge_sets.enabled(config) else config["write_space_id"])
        publish = self.bridge.publish_target if knowledge_sets.enabled(config) else self.bridge.publish
        result = publish(actor, rid+":"+run["draft_hash"]+(":auto" if automatic else ":reviewed"),
                                     target, run["submission"]["title"], markdown, verify,
                                     commit_guard=lambda:self.grant_commit_guard(verify))
        return self.store.record_publication(owner, rid, run["draft_hash"], result)

    def record_notion(self, owner, rid, *, cid=None, snapshot=None):
        run = snapshot or self.store.run(owner, rid, cid=cid)
        verify = lambda: self.authorize_connection(owner, run["connection_id"], "notion:write")
        actor, config = verify()
        if self.notion is None or not config["notion_parent_id"]:
            raise IntegrationError("개발용 Notion 연결과 기록 위치를 먼저 설정해 주세요.", 503, "NOTION_NOT_CONFIGURED")
        # Connector must enforce target ACL, CAS and an idempotent operation itself.
        result = self.notion.record(actor, config["notion_parent_id"], run, verify)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            verify()
            current = self.store.run(owner, rid, cid=cid, c=c)
            # Preserve a historical receipt without replacing a newer snapshot.
            if (current["notion"] or {}).get("snapshot_version", 0) <= run["row_version"]:
                c.execute("UPDATE integration_run SET notion_json=? WHERE id=?", (json.dumps(result), rid))
            self.store.event(c, owner, run["connection_id"], rid, "notion.recorded", result)
            c.execute("COMMIT")
        return result
