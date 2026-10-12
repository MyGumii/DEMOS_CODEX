"""Adapter to current canonical services, never direct Wiki file/SQL writes."""
from uuid import NAMESPACE_URL, uuid4, uuid5
from contextlib import nullcontext

from ..knowledge_core.db import TransactionMode, transaction
from ..knowledge_core.scope import Capability, SessionPrincipal
from ..knowledge_core.services.personal_wikis import ListPersonalWikisQuery
from ..knowledge_core.services.retrieval import SpaceRetrievalRequest
from ..knowledge_core.services.management_reads import ItemManagementReadRequest
from ..knowledge_core.services.publication import CreateManualPreviewCommand, GetPublicationPreviewQuery, PublishPreviewCommand
from ..knowledge_core.services.conversations import (CreateConversationCommand, AcceptConversationTurnCommand,
    CompleteConversationTurnCommand, TerminateConversationTurnCommand)
from ..knowledge_core.services.agent_context import AgentTurnContextRequest
from .contracts import IntegrationError, text, uid


def stable_id(value):
    return str(uuid5(NAMESPACE_URL, "agent-integrations-v1:"+value))


class KnowledgeBridge:
    def __init__(self, services, bindings):
        self.services, self.bindings = services, bindings
        self.factory = services.source_artifacts._factory
        self.scopes = services.source_artifacts._scopes
        # Bind the canonical services directly, including on production hosts
        # whose Agent UI omits publication. Development-only facade wrappers
        # may run workers inline; those do not belong inside a commit fence.
        from ..knowledge_core.services.publication import PublicationApiService, PublicationPreviewService, PublicationService
        self.publication = PublicationApiService(
            PublicationPreviewService(self.factory,self.scopes,services.source_artifacts._stager),
            PublicationService(self.factory,self.scopes))

    def actor(self, user_id):
        c = self.factory.open(read_only=True)
        try:
            row = c.execute("SELECT id,scope_epoch,account_status,system_role,display_name,must_change_password FROM app_user WHERE id=?", (user_id,)).fetchone()
            if row is None or row["account_status"] != "active" or row["must_change_password"]:
                raise IntegrationError("유효한 계정이 필요합니다.", 401, "AUTHENTICATION_REQUIRED")
            return dict(row)
        finally:
            c.close()

    def authorize(self, actor, space_id, *, write=False):
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                self.scopes.resolve_in_transaction(c, SessionPrincipal(actor["id"], actor["scope_epoch"]),
                                                  uid(space_id), Capability.PUBLISH if write else Capability.READ)
                row = c.execute("SELECT space_kind,owner_user_id FROM knowledge_space WHERE id=?", (space_id,)).fetchone()
                if row is None or row["space_kind"] != "personal" or row["owner_user_id"] != actor["id"]:
                    raise IntegrationError("사용 가능한 개인 Wiki를 선택해 주세요.", 404, "NOT_FOUND")
        finally:
            c.close()

    def wikis(self, actor, cursor=None):
        page = self.services.personal_wikis.list_profiles(ListPersonalWikisQuery(actor_user_id=actor["id"],
            actor_scope_epoch=actor["scope_epoch"], request_id=str(uuid4()), cursor=cursor))
        return {"items": [dict(space_id=p.space_id, display_name=p.display_name, is_default=p.is_default) for p in page.items],
                "next_cursor": page.next_cursor}

    def view(self, actor, space_id, *, write=False):
        """Resolve native/aggregate authority without widening a delegated grant."""
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                plan = self.scopes.resolve_access_plan_in_transaction(c,
                    SessionPrincipal(actor["id"], actor["scope_epoch"]), uid(space_id),
                    Capability.PUBLISH if write else Capability.READ)
                return dict(space_id=plan.selected_view.id, kind=plan.selected_view.kind,
                    display_name=plan.selected_view.name, org_path=list(plan.selected_view.org_path),
                    can_publish=Capability.PUBLISH in plan.native_capabilities,
                    closure_sha256=plan.corpus_closure.sha256,
                    role_assignment_id=plan.role_assignment_id,
                    role_assignment_generation=plan.role_assignment_generation)
        finally:
            c.close()

    def organization_views(self, actor):
        c = self.factory.open(read_only=True)
        try:
            with transaction(c, TransactionMode.DEFERRED):
                selected = self.scopes.list_selectable_in_transaction(c,
                    SessionPrincipal(actor["id"], actor["scope_epoch"]))
                return [dict(space_id=s.id, kind=s.kind, display_name=s.name,
                    org_path=list(s.org_path), can_publish=Capability.PUBLISH in s.capabilities)
                    for s in selected.items if s.kind != "personal"]
        finally:
            c.close()

    def search(self, actor, space_id, query):
        self.authorize(actor, space_id)
        return self.search_view(actor, space_id, query)

    def search_view(self, actor, space_id, query):
        self.view(actor, space_id)
        result = self.services.retrieval.retrieve_for_space(SpaceRetrievalRequest(actor_user_id=actor["id"],
            actor_scope_epoch=actor["scope_epoch"], space_id=space_id, query=text(query, "검색어", 2000), top_k=20))
        hits = result.ranked_documents or result.hits
        items, used = [], 0
        for hit in hits:
            excerpt = hit.excerpt_md
            raw = excerpt.encode("utf-8")
            if used + len(raw) > 98304:
                excerpt = raw[:98304-used].decode("utf-8", errors="ignore")
            if not excerpt:
                break
            used += len(excerpt.encode("utf-8"))
            items.append(dict(item_id=hit.item_id, revision_id=hit.revision_id,
                              source_space_id=(getattr(hit, "source_space_id", None) or hit.space_id),
                              title=hit.title, excerpt=excerpt))
        self.view(actor, space_id)
        return dict(space_id=space_id, items=items, trust_boundary="untrusted_reference",
                    excerpt_bytes=used, truncated=used>=98304, corpus_closure_sha256=result.corpus_closure_sha256)

    def get(self, actor, space_id, item_id):
        self.authorize(actor, space_id)
        return self.get_view(actor, space_id, item_id)

    def get_view(self, actor, space_id, item_id):
        self.view(actor, space_id)
        result = self.services.management_reads.get_item(ItemManagementReadRequest(actor_user_id=actor["id"],
            actor_scope_epoch=actor["scope_epoch"], space_id=space_id, item_id=uid(item_id)))
        if result.record.lifecycle_status != "active" or result.record.curation_status != "published":
            raise IntegrationError("문서를 찾을 수 없습니다.", 404, "NOT_FOUND")
        return dict(id=result.record.id, title=result.record.anchor_revision.title, markdown=result.markdown,
                    revision_id=result.record.current_revision_id, source_space_id=result.source_space_id)

    def publish_target(self, actor, run_id, target, title, markdown, revalidate, *, commit_guard=None):
        """Use existing canonical personal/Part dual or exact higher-native publication."""
        personal = target.get("personal_space_id")
        if personal:
            self.authorize(actor, personal, write=True)
        mode, descriptor = target["mode"], None
        if mode in {"organization", "both"}:
            org = self.view(actor, target["organization_space_id"], write=True)
            if org["kind"] not in {"part", "team", "responsibility"}:
                raise IntegrationError("등록 조직이 올바르지 않습니다.")
            if mode == "both" and org["kind"] != "part":
                raise IntegrationError("개인 동시 등록은 현재 개인+파트를 지원합니다.")
            if org["kind"] in {"team", "responsibility"}:
                from ..knowledge_core.repositories.publication_authority import HigherPublicationTargetDescriptor
                descriptor = HigherPublicationTargetDescriptor(kind=org["kind"], space_id=org["space_id"], capability="publish",
                    role_assignment_id=org["role_assignment_id"],
                    role_assignment_generation=org["role_assignment_generation"])
            mode = "both" if mode == "both" else "explicit" if descriptor else "part"
        common = dict(actor_user_id=actor["id"], actor_scope_epoch=actor["scope_epoch"],
                      request_id=stable_id(run_id+":request"))
        preview = self.publication.create_manual_preview(CreateManualPreviewCommand(**common,
            operation_id=stable_id(run_id+":preview"), title=title, markdown=markdown, target=descriptor))
        detail = self.publication.get_preview(GetPublicationPreviewQuery(actor_user_id=actor["id"],
            actor_scope_epoch=actor["scope_epoch"], preview_id=preview.preview_id))
        revalidate()
        with commit_guard() if commit_guard is not None else nullcontext():
            result = self.publication.publish(PublishPreviewCommand(**common,
                operation_id=stable_id(run_id+":publish"), preview_id=preview.preview_id,
                source_snapshot_sha256=detail["source_snapshot_sha256"], target_mode=mode,
                personal_space_id=personal, target=descriptor))
        return dict(operation_id=result.operation_id, results=[dict(t) for t in result.targets])

    def ask(self, actor, space_id, operation_id, message, revalidate):
        self.authorize(actor, space_id)
        uid(operation_id, "operation_id")
        message = text(message, "질문", 12000)
        common = dict(actor_user_id=actor["id"], actor_scope_epoch=actor["scope_epoch"], request_id=str(uuid4()))
        conv = self.services.conversations.create(CreateConversationCommand(**common,
            operation_id=stable_id(operation_id+":conversation"), space_id=space_id, title="외부 에이전트 질문"))
        cid = conv.conversation.id
        accepted = self.services.conversations.accept_turn(AcceptConversationTurnCommand(**common,
            operation_id=operation_id, conversation_id=cid, user_content={"text": message}))
        if accepted.state in {"completed", "failed", "cancelled"}:
            return self._ask_result(actor, space_id, cid, operation_id, revalidate)
        try:
            context = self.services.agent_context.compose(AgentTurnContextRequest(actor_user_id=actor["id"],
                actor_scope_epoch=actor["scope_epoch"], conversation_id=cid, turn_id=accepted.turn_id))
            completion = self.bindings.turn_executor.complete(context)
            revalidate()
            self.services.conversations.complete_turn(CompleteConversationTurnCommand(**common, operation_id=operation_id,
                conversation_id=cid, assistant_content=completion.assistant_content, final_response=completion.final_response))
        except Exception:
            self.services.conversations.terminate_turn(TerminateConversationTurnCommand(**common, operation_id=operation_id,
                conversation_id=cid, terminal_state="failed", error_code="AGENT_EXECUTION_FAILED", retryable=True))
            raise
        # Read the committed answer for both first delivery and replay. The core
        # stores answer text separately from final_response metadata; this also
        # recovers answers written before the integration response was fixed.
        return self._ask_result(actor, space_id, cid, operation_id, revalidate)

    def _ask_result(self, actor, space_id, conversation_id, operation_id, revalidate):
        common = dict(actor_user_id=actor["id"], actor_scope_epoch=actor["scope_epoch"],
                      conversation_id=conversation_id)
        stored, _ = self.services.conversations.reconnect_turn(
            **common, operation_id=operation_id, after_event_sequence_no=0)
        answer = None
        if stored.state == "completed":
            after = 0
            while stored.assistant_message_id:
                messages = self.services.conversations.list_messages(
                    **common, after_sequence_no=after, limit=100)
                if not messages:
                    break
                message = next((row for row in messages if row.id == stored.assistant_message_id), None)
                if message is not None:
                    content = message.content.get("content")
                    if message.role == "assistant" and isinstance(content, dict):
                        answer = content.get("text")
                    break
                after = messages[-1].sequence_no
            if not isinstance(answer, str) or not answer.strip():
                raise IntegrationError("저장된 답변 본문을 불러올 수 없습니다.", 503, "ANSWER_UNAVAILABLE")
        revalidate()
        self.authorize(actor, space_id)
        result = dict(conversation_id=conversation_id, state=stored.state,
                      answer=answer, response=stored.final_response)
        if stored.error is not None:
            result["error"] = stored.error
        return result

    def publish(self, actor, run_id, space_id, title, markdown, revalidate, *, commit_guard=None):
        self.authorize(actor, space_id, write=True)
        service = self.publication
        if service is None:
            raise IntegrationError("지식 등록 서비스가 활성화되지 않았습니다.", 503, "SERVICE_UNAVAILABLE")
        common = dict(actor_user_id=actor["id"], actor_scope_epoch=actor["scope_epoch"], request_id=stable_id(run_id+":request"))
        preview = service.create_manual_preview(CreateManualPreviewCommand(**common,
            operation_id=stable_id(run_id+":preview"), title=title, markdown=markdown))
        detail = service.get_preview(GetPublicationPreviewQuery(actor_user_id=actor["id"], actor_scope_epoch=actor["scope_epoch"], preview_id=preview.preview_id))
        revalidate()
        self.authorize(actor, space_id, write=True)
        # Preview staging/file work is complete before the grant-revocation
        # fence. The canonical publish call performs only transactional work.
        with commit_guard() if commit_guard is not None else nullcontext():
            result = service.publish(PublishPreviewCommand(**common, operation_id=stable_id(run_id+":publish"),
                preview_id=preview.preview_id, source_snapshot_sha256=detail["source_snapshot_sha256"],
                target_mode="personal", personal_space_id=space_id))
        return dict(operation_id=result.operation_id, results=[dict(t) for t in result.targets])
