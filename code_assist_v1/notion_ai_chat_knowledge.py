"""Read-only AI chat adapter for authorized personal and organization views.

Uses the host's existing canonical services and rollout flags. Never connects to
another host's development catalog, enables a rollout, or widens AI-note collection.
"""
from code_assist_v1.agent_integrations.contracts import IntegrationError
from code_assist_v1.knowledge_core import get_knowledge_core, KnowledgeCoreError
from code_assist_v1.notion_ai_notes import NotesUnavailable
from code_assist_v1.notion_notes_knowledge import KnowledgeReader, EXCERPT_BYTES, digest


def _read(function, *args):
    try:
        return function(*args)
    except (IntegrationError, KnowledgeCoreError) as exc:
        status = getattr(exc, "http_status", 503)
        if status in {401, 403, 404, 409}:
            raise PermissionError("선택한 Wiki의 현재 접근 권한 또는 자료 범위가 변경되었습니다.") from exc
        if status == 400:
            raise ValueError("Wiki 선택 또는 검색 요청이 올바르지 않습니다.") from exc
        raise NotesUnavailable("현재 호스트의 Wiki 읽기 서비스를 사용할 수 없습니다.") from exc


class ChatKnowledgeReader(KnowledgeReader):
    def _bridge(self):
        bridge = super()._bridge()
        if bridge and not bridge.services.management_reads:
            raise NotesUnavailable("현재 호스트에 Wiki 자료 재검증 서비스가 연결되지 않았습니다.")
        return bridge

    def actor(self, user_id):
        return _read(super().actor, user_id)

    def _allowed_kind(self, kind):
        flags = get_knowledge_core(self.app).flags
        return kind == "personal" or (flags.part_reads and (
            kind == "part" or (flags.higher_spaces and kind in {"team", "responsibility"})))

    def _view(self, bridge, actor, space_id):
        view = _read(bridge.view, actor, space_id)
        if view["space_id"] != space_id or not self._allowed_kind(view["kind"]):
            raise PermissionError("현재 호스트에서 읽기가 허용된 Wiki를 선택하세요.")
        if view["kind"] == "personal":
            _read(bridge.authorize, actor, space_id)  # Still restricted to this owner.
        return view

    def scopes(self, user_id, cursor=None):
        bridge, actor = self.actor(user_id)
        if not bridge:
            result = super().scopes(user_id, cursor)
            return {**result, "mode": "legacy-personal", "items": [
                {**item, "kind": "personal", "org_path": []} for item in result["items"]]}
        page = _read(bridge.wikis, actor, cursor)
        items = [{**item, "kind": "personal", "org_path": []} for item in page["items"]]
        # Organization views are not repeated on subsequent personal-Wiki pages.
        if not cursor and get_knowledge_core(self.app).flags.part_reads:
            items.extend(item for item in _read(bridge.organization_views, actor)
                         if self._allowed_kind(item["kind"]))
        return {"items": items, "next_cursor": page["next_cursor"], "mode": "canonical-views"}

    def authorize(self, user_id, space_id):
        bridge, actor = self.actor(user_id)
        if not bridge:
            return super().authorize(user_id, space_id)
        self._view(bridge, actor, space_id)

    def retrieve(self, user_id, space_id, query):
        bridge, actor = self.actor(user_id)
        if not bridge:
            return super().retrieve(user_id, space_id, query)
        view = self._view(bridge, actor, space_id)
        found = _read(bridge.search_view, actor, space_id, query)
        closure = found.get("corpus_closure_sha256")
        if not closure and view["kind"] == "personal":
            closure = view["closure_sha256"]  # Compatibility with personal-only retrieval.
        if found["space_id"] != space_id or not closure or closure != view["closure_sha256"]:
            raise PermissionError("Wiki 검색 범위가 변경되었습니다. 다시 요청하세요.")
        items, used, truncated = [], 0, found["truncated"]
        for item in found["items"]:
            raw = item["excerpt"].encode("utf-8")
            excerpt = raw[:EXCERPT_BYTES-used].decode("utf-8", errors="ignore")
            truncated = truncated or len(excerpt.encode("utf-8")) != len(raw)
            if not excerpt:
                continue
            items.append({"id": item["item_id"], "revision_id": item["revision_id"],
                          "title": item["title"], "space_id": item["source_space_id"],
                          "excerpt": excerpt, "excerpt_sha256": digest(excerpt)})
            used += len(excerpt.encode("utf-8"))
        snapshot = {"mode": "canonical-" + view["kind"], "items": items,
                    "excerpt_bytes": used, "truncated": truncated,
                    "corpus_closure_sha256": closure}
        self.revalidate(user_id, space_id, snapshot)
        return snapshot

    def revalidate(self, user_id, space_id, snapshot):
        bridge, actor = self.actor(user_id)
        if not bridge:
            return super().revalidate(user_id, space_id, snapshot)
        view = self._view(bridge, actor, space_id)
        expected_closure = snapshot.get("corpus_closure_sha256")
        if expected_closure and expected_closure != view["closure_sha256"]:
            raise PermissionError("참고 Wiki의 조직 또는 자료 범위가 변경되었습니다. 새 대화를 시작하세요.")
        for item in snapshot["items"]:
            current = _read(bridge.get_view, actor, space_id, item["id"])
            if current["revision_id"] != item["revision_id"] or current["source_space_id"] != item["space_id"]:
                raise PermissionError("참고 지식의 버전 또는 출처 권한이 변경되었습니다.")
        # Re-resolve with a fresh actor after reading all items.
        bridge, actor = self.actor(user_id)
        current_view = self._view(bridge, actor, space_id)
        if current_view["closure_sha256"] != view["closure_sha256"]:
            raise PermissionError("참고 Wiki의 자료 범위가 변경되었습니다.")
