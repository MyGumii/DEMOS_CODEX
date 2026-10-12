"""AI-note experiment read adapter. No canonical writes or rollout changes."""
from __future__ import annotations

from collections import Counter
import hashlib
import os
import re

from code_assist_v1 import auth, notion_ai_notes as notes

EXCERPT_BYTES = 98304


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def search_query(data: dict) -> str:
    explicit = notes.text_field(data, "knowledge_query", 2000)
    if explicit:
        return explicit
    # Examine the entire source; do not silently use only the meeting's first lines.
    tokens = re.findall(r"[\w가-힣]{2,}", data["transcript"] + "\n" + data["notes"], re.UNICODE)
    return " ".join(word for word, _count in Counter(tokens).most_common(100))[:2000]


class KnowledgeReader:
    def __init__(self, app):
        self.app = app

    def _bridge(self):
        from code_assist_v1.knowledge_core import get_knowledge_core
        core = get_knowledge_core(self.app)
        if not core.flags.core_enabled:
            return None
        if not core.flags.personal_reads or not core.flags.multi_wiki_reads:
            raise notes.NotesUnavailable("현재 호스트의 Second Brain 읽기가 활성화되지 않았습니다.")
        if not all((core.services.source_artifacts, core.services.personal_wikis, core.services.retrieval)):
            raise notes.NotesUnavailable("현재 호스트에 Second Brain 읽기 서비스가 연결되지 않았습니다.")
        from code_assist_v1.agent_integrations.bridge import KnowledgeBridge
        return KnowledgeBridge(core.services, core.canonical_route_bindings)

    def actor(self, user_id):
        bridge = self._bridge()
        if bridge:
            return bridge, bridge.actor(user_id)
        # A stored job is not a perpetual authentication grant.
        user = auth._load_users().get("users", {}).get(user_id)
        if not user or user.get("disabled") or auth.safe_user_id(user_id) != user_id:
            raise PermissionError("현재 계정의 지식 읽기 권한이 없습니다.")
        return None, {"id": user_id}

    def scopes(self, user_id, cursor=None):
        bridge, actor = self.actor(user_id)
        if bridge:
            return bridge.wikis(actor, cursor)
        return {"items": [{"space_id": "legacy-personal", "display_name": "내 Second Brain · 파일 Wiki", "is_default": True}], "next_cursor": None}

    def authorize(self, user_id, space_id):
        bridge, actor = self.actor(user_id)
        if bridge:
            bridge.authorize(actor, space_id)
        elif space_id != "legacy-personal":
            raise PermissionError("현재 호스트의 개인 Wiki를 선택하세요.")

    def retrieve(self, user_id, space_id, query):
        self.authorize(user_id, space_id)
        bridge, actor = self.actor(user_id)
        if bridge:
            found = bridge.search(actor, space_id, query)
            items = [{"id": item["item_id"], "revision_id": item["revision_id"], "title": item["title"],
                      "excerpt": item["excerpt"], "space_id": item["source_space_id"]} for item in found["items"]]
            mode = "canonical-personal"
            truncated = found["truncated"]
        else:
            from code_assist_v1 import second_brain_store as sb
            # Do not initialize a different or nonexistent user's Wiki as a search fallback.
            if not os.path.isdir(sb._paths(user_id)["sources"]):
                return {"mode": "legacy-personal", "items": [], "excerpt_bytes": 0, "truncated": False}
            items = []
            for hit in sb.search_thoughts(query, user_id=user_id, max_results=20):
                record = sb.get_thought(hit["id"], user_id=user_id)
                if record:
                    body = record["content"]
                    items.append({"id": hit["id"], "revision_id": digest(body), "title": hit["title"], "excerpt": body, "space_id": "legacy-personal"})
            mode, truncated = "legacy-personal", False
        kept, used = [], 0
        for item in items:
            raw = item["excerpt"].encode("utf-8")
            excerpt = raw[:EXCERPT_BYTES-used].decode("utf-8", errors="ignore")
            truncated = truncated or len(excerpt.encode("utf-8")) != len(raw)
            if not excerpt:
                continue
            kept.append({**item, "excerpt": excerpt, "excerpt_sha256": digest(excerpt)})
            used += len(excerpt.encode("utf-8"))
        self.authorize(user_id, space_id)
        return {"mode": mode, "items": kept, "excerpt_bytes": used, "truncated": truncated}

    def revalidate(self, user_id, space_id, snapshot):
        self.authorize(user_id, space_id)
        bridge, actor = self.actor(user_id)
        for item in snapshot["items"]:
            if bridge:
                current = bridge.get(actor, space_id, item["id"])
                revision = current["revision_id"]
            else:
                from code_assist_v1 import second_brain_store as sb
                current = sb.get_thought(item["id"], user_id=user_id)
                revision = digest(current["content"]) if current else None
            if revision != item["revision_id"]:
                raise PermissionError("참고 지식의 버전 또는 접근 권한이 변경되어 비교를 중단했습니다.")
