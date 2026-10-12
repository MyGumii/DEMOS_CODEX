"""Read-only, authenticated personal Notion evidence for the AI home.

Never enumerate other owners' shards, follow page links, or bootstrap a store.
Explicit page/Wiki selections continue to use their existing permission paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from flask import has_request_context

from code_assist_v1 import auth, config, notion_store as ns
from code_assist_v1.routes_notion_document_ai import DocumentChanged


CONTEXT_BYTES = 12288
MANIFEST_BYTES = 4096
MAX_LISTED_PAGES = 64
MAX_BODY_PAGES = 8
_TABLE_TYPES = {"table", "board", "list", "gallery", "timeline", "calendar",
                "chart", "dashboard", "form", "feed", "map"}
_STOP = {"notion", "노션", "지금", "현재", "어떤", "내용", "내용들", "있지", "있어",
         "알려줘", "보여줘", "정리해줘", "요약해줘", "자세히", "설명해줘", "내", "나의",
         "페이지", "문서", "what", "which", "my", "the", "in", "is", "are", "list"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _tokens(question):
    words = re.findall(r"[\w-]+", unicodedata.normalize("NFC", question).casefold())
    result = set()
    for word in words:
        word = re.sub(r"(?:에서는|에서|에는|으로|들이|들을|이랑|의|을|를|은|는|이|가|에)$", "", word)
        if len(word) >= 2 and word not in _STOP:
            result.add(word)
    return sorted(result)


def _workspace_question(question):
    """Recognize questions about this app's pages without requiring its brand."""
    text = unicodedata.normalize("NFC", question).casefold()
    if re.search(r"notion|노션", text):
        return True
    if re.search(r"(?:무슨|어떤|무엇|뭐)[^\n?!.]{0,12}내용[^\n?!.]{0,16}(?:정리|저장|있|담)", text):
        return True
    objects = r"페이지|문서|메모|노트|자료|워크스페이스|저장(?:한|된|해둔|해\s*둔)?\s*(?:내용|정보|것)|\b(?:pages?|documents?|notes?|workspace)\b"
    requests = r"내용|목록|어떤|무슨|뭐|무엇|있|내|우리|저장|지금|현재|알려|보여|요약|정리|찾|검색|\b(?:my|our|what|which|list|show|summari[sz]e|saved|stored|search|find|have)\b"
    if re.search(objects, text) and re.search(requests, text):
        return True
    return bool(re.search(r"(?:여기|이곳)(?:에|에는|엔)?[^\n?!.]{0,24}(?:뭐|무엇|어떤|내용|저장)|"
                          r"(?:지금|현재)[^\n?!.]{0,12}(?:내용|자료)[^\n?!.]{0,12}(?:있|보여|알려)|"
                          r"\bwhat(?:'s|\s+is)?\b[^\n?!.]{0,24}\b(?:here|saved|stored)\b", text))


def _uid(uid):
    if not isinstance(uid, str) or not uid or auth.safe_user_id(uid) != uid:
        raise PermissionError("로그인한 계정이 필요합니다.")
    if has_request_context() and auth.current_user_id() != uid:
        raise PermissionError("계정 접근 권한이 변경되었습니다.")
    return uid


def _display_block(row):
    """Only visible text/cells; arbitrary props and private comparisons stay out."""
    text = str(row["content"] or "")
    props = json.loads(row["props_json"] or "{}")
    if not isinstance(props, dict):
        props = {}
    kind = row["block_type"]
    # Synced/linked content requires its separate source authorization contract.
    if kind == "synced_block":
        return "[동기화 블록 본문은 자동 조회에 포함되지 않음]"
    if kind == "simple_table":
        rows = props.get("rows", [])
        if isinstance(rows, list):
            text += "\n" + "\n".join(" | ".join(str(cell) for cell in cells)
                                      for cells in rows if isinstance(cells, list))
    elif kind in _TABLE_TYPES:
        properties = props.get("properties", [])
        fields = [(str(p["id"]), str(p.get("name") or p["id"])) for p in properties
                  if isinstance(p, dict) and p.get("id")] if isinstance(properties, list) else []
        if not fields:
            fields = [(key, key) for key in ("title", "date", "tags")]
        rows = props.get("rows", props.get("events", []))
        if isinstance(rows, list):
            text += "\n" + "\n".join("; ".join(f"{label}: {row[key]}" for key, label in fields if key in row)
                                      for row in rows if isinstance(row, dict) and not row.get("archived"))
    return text.strip()


@dataclass(frozen=True)
class WorkspaceSnapshot:
    reader: "PersonalNotionReader"
    uid: str
    inventory_hash: str
    body_hashes: dict
    visible_ids: tuple
    references: tuple
    info: dict

    def revalidate(self):
        pages, bodies = self.reader._read(self.uid, body_ids=tuple(self.body_hashes))
        if _hash(pages) != self.inventory_hash or any(_hash(bodies.get(pid, "")) != digest
                                                    for pid, digest in self.body_hashes.items()):
            raise DocumentChanged("참고한 Notion 내용이 변경되었습니다. 다시 요청하세요.")

    def history_proof(self):
        return {"owner_user_id": self.uid, "page_ids": list(self.visible_ids)}


class PersonalNotionReader:
    def _read(self, uid, *, body_ids=None):
        uid = _uid(uid)
        root = Path(config.USER_DATA_DIR).resolve()
        shard = root / uid / "notion" / "notion.db"
        # An owner locator must not escape into a different account via a junction.
        if shard.resolve() != shard.absolute():
            raise PermissionError("Notion 저장소 경로를 확인하세요.")
        if not shard.is_file():
            return [], {}
        with ns.bind_user_data_root(root), ns._connection(uid) as con:
            con.execute("BEGIN")  # Consistent read snapshot; query_only forbids writes.
            pages = [dict(row) for row in con.execute(
                "SELECT id,title,parent_id,revision FROM pages "
                "WHERE owner_user_id=? AND archived=0 ORDER BY position,id", (uid,))]
            chunks = {page["id"]: [] for page in pages if body_ids is None or page["id"] in body_ids}
            if body_ids is None or body_ids:
                condition = "" if body_ids is None else " AND b.page_id IN (" + ",".join("?" for _ in body_ids) + ")"
                for row in con.execute(
                    "SELECT b.page_id,b.block_type,b.content,b.props_json FROM blocks b "
                    "JOIN pages p ON p.id=b.page_id WHERE p.owner_user_id=? AND b.owner_user_id=? "
                    "AND p.archived=0" + condition + " ORDER BY b.page_id,b.position,b.id", (uid, uid, *(body_ids or ()))):
                    chunks[row["page_id"]].append(_display_block(row))
        return pages, {pid: "\n\n".join(parts) for pid, parts in chunks.items()}

    def capability(self, uid):
        from code_assist_v1.notion_collaboration import iam_runtime
        if iam_runtime.runtime_requested():
            return {"enabled": False, "mode": "explicit-workspace-pages"}
        pages, _ = self._read(uid, body_ids=())
        return {"enabled": True, "mode": "personal-owned", "page_count": len(pages)}

    def authorize_history(self, uid, proof):
        from code_assist_v1.notion_collaboration import iam_runtime
        if iam_runtime.runtime_requested():
            raise PermissionError("이전 개인 Notion 참고 자료의 접근 권한을 확인할 수 없습니다.")
        if proof.get("owner_user_id") != _uid(uid):
            raise PermissionError("Notion 참고 자료의 계정이 다릅니다.")
        pages, _ = self._read(uid, body_ids=())
        owned = {page["id"] for page in pages}
        if not set(proof.get("page_ids", [])).issubset(owned):
            raise PermissionError("이 대화의 Notion 참고 자료에 더 이상 접근할 수 없습니다.")

    def snapshot(self, uid, question, *, followup=False, preferred_ids=()):
        from code_assist_v1.notion_collaboration import iam_runtime
        if iam_runtime.runtime_requested():
            return None  # Collaborative workspaces use explicit authorized sources.
        pages, bodies = self._read(uid)
        tokens = _tokens(question)
        requested = followup or _workspace_question(question)
        ranked = []
        for page in pages:
            title, body = page["title"].casefold(), bodies[page["id"]].casefold()
            score = sum(8 * (token in title) + (token in body) for token in tokens)
            if score or requested and page["id"] in preferred_ids:
                ranked.append((score + 2 * (page["id"] in preferred_ids), page))
        if not requested and not ranked:
            return None
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        selected = [page for _, page in ranked[:MAX_BODY_PAGES]]
        if requested and not selected:
            selected = pages[:MAX_BODY_PAGES]
        references, visible, used = [], [], 0
        listing = []
        for page in pages[:MAX_LISTED_PAGES]:
            line = json.dumps({"title": page["title"], "page_id": page["id"]}, ensure_ascii=False)
            if used + len(line.encode()) > MANIFEST_BYTES:
                break
            listing.append(line); visible.append(page["id"]); used += len(line.encode())
        listed = len(listing)
        manifest = (f"로그인 계정의 활성 Notion 페이지: 총 {len(pages)}개. 목록에 표시한 페이지: {listed}개.\n"
                    + "\n".join(listing) + "\n목록은 제목만 제공하며 본문은 별도의 page 참고 자료를 사용하세요. "
                    "파일 첨부 본문, 외부 링크, 동기화 원본 및 별도 DB 행 페이지 본문은 자동 조회하지 않았습니다.")
        sid = "notion-workspace:" + uid
        references.append({"source_id": "N" + hashlib.sha256(sid.encode()).hexdigest()[:12],
                           "kind": "notion_workspace", "id": sid, "title": "내 Notion 페이지 목록", "excerpt": manifest})
        used = len(manifest.encode())
        truncated = listed < len(pages)
        hashes = {}
        for index, page in enumerate(selected):
            pid = page["id"]; body = bodies[pid]
            budget = max(0, (CONTEXT_BYTES - used) // (len(selected) - index))
            raw = body.encode()
            # Keep relevant content near a query hit even in a long document.
            start = min((body.casefold().find(t) for t in tokens if t in body.casefold()), default=0)
            if len(raw) > budget and start > 200:
                excerpt = body[max(0, start - 200):].encode()[:budget].decode("utf-8", errors="ignore")
            else:
                excerpt = raw[:budget].decode("utf-8", errors="ignore")
            truncated = truncated or len(excerpt.encode()) < len(raw)
            if not excerpt.strip():
                continue
            references.append({"source_id": "P" + hashlib.sha256(pid.encode()).hexdigest()[:12],
                               "kind": "page", "id": pid, "title": page["title"], "excerpt": excerpt, "url": "/page/" + pid})
            used += len(excerpt.encode()); hashes[pid] = _hash(body); visible.append(pid)
        body_count = len(hashes)
        info = {"mode": "personal-owned", "page_count": len(pages), "listed_page_count": listed,
                "body_page_count": body_count, "body_coverage_partial": body_count < len(pages),
                "truncated": truncated, "excerpt_bytes": used, "max_excerpt_bytes": CONTEXT_BYTES}
        return WorkspaceSnapshot(self, uid, _hash(pages), hashes, tuple(sorted(set(visible))), tuple(references), info)
