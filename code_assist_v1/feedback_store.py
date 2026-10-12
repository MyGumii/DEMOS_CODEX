"""Persistent, application-wide feedback. Independent of Wiki publication.

Schema v1 is installed once during application composition. Post mutations,
attachment bytes, history and actor-scoped idempotency commit together.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from uuid import UUID, uuid4


class FeedbackError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.http_status = status


def canonical_id(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise FeedbackError("올바른 요청 식별자가 필요합니다.") from None
    return value


def text_field(value, maximum, name):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise FeedbackError(f"{name}은 1~{maximum:,}자로 입력해 주세요.")
    return value.strip()


class FeedbackStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("BEGIN IMMEDIATE")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported feedback database version")
            if version == 0:
                schema = """
                    CREATE TABLE feedback_post (
                      id TEXT PRIMARY KEY, author_id TEXT NOT NULL, author_name TEXT NOT NULL,
                      title TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('bug','improvement')),
                      body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'received'
                        CHECK(status IN ('received','reviewing','completed')),
                      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                      row_version INTEGER NOT NULL, deleted_at INTEGER);
                    CREATE INDEX feedback_posts_visible ON feedback_post(deleted_at,created_at DESC,id DESC);
                    CREATE TABLE feedback_attachment (
                      id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES feedback_post(id),
                      filename TEXT NOT NULL, media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
                      content BLOB NOT NULL, removed_at INTEGER);
                    CREATE INDEX feedback_attachments_post ON feedback_attachment(post_id,removed_at);
                    CREATE TABLE feedback_history (
                      id INTEGER PRIMARY KEY, post_id TEXT NOT NULL REFERENCES feedback_post(id),
                      actor_id TEXT NOT NULL, action TEXT NOT NULL, occurred_at INTEGER NOT NULL,
                      snapshot_json TEXT NOT NULL);
                    CREATE TABLE feedback_operation (
                      actor_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                      result_json TEXT NOT NULL, PRIMARY KEY(actor_id,operation_id));
                    PRAGMA user_version=1;
                """
                for statement in schema.split(";"):
                    if statement.strip():
                        c.execute(statement)
            c.execute("COMMIT")

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    @staticmethod
    def _require(c, post_id):
        row = c.execute("SELECT * FROM feedback_post WHERE id=? AND deleted_at IS NULL", (canonical_id(post_id),)).fetchone()
        if row is None:
            raise FeedbackError("게시글을 찾을 수 없습니다.", 404)
        return row

    @staticmethod
    def _post(c, row, actor, *, detail=True):
        result = dict(row)
        result.pop("deleted_at", None)
        if not detail:
            result.pop("body", None)
        result["can_edit"] = row["author_id"] == actor["id"]
        result["can_change_status"] = actor["role"] == "admin"
        attachments = c.execute("""SELECT id,filename,media_type,sha256,length(content) size_bytes
            FROM feedback_attachment WHERE post_id=? AND removed_at IS NULL ORDER BY rowid""", (row["id"],)).fetchall()
        result["attachments"] = [dict(a) for a in attachments]
        return result

    def list(self, actor, *, before=None, limit=30, query="", search_field="all",
             kind="", status="", mine=False):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FeedbackError("목록 크기가 올바르지 않습니다.")
        fields = {"all": ("title", "body", "author_name", "author_id"),
                  "title": ("title",), "body": ("body",),
                  "requester": ("author_name", "author_id")}
        if not isinstance(query, str) or len(query) > 200 or "\x00" in query:
            raise FeedbackError("검색어는 200자 이내로 입력해 주세요.")
        terms = query.split()
        if len(terms) > 8:
            raise FeedbackError("검색어는 공백으로 구분하여 최대 8개까지 입력해 주세요.")
        if search_field not in fields or kind not in {"", "bug", "improvement"} or status not in {"", "received", "reviewing", "completed"} or type(mine) is not bool:
            raise FeedbackError("검색 또는 필터 조건이 올바르지 않습니다.")
        clauses, params = ["deleted_at IS NULL"], []
        for term in terms:
            # Literal substring search. User '%'/'_' characters are not SQL
            # wildcards. Every term must match at least one selected field.
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in fields[search_field]) + ")")
            params.extend([f"%{escaped}%"] * len(fields[search_field]))
        for column, value in (("kind", kind), ("status", status), ("author_id", actor["id"] if mine else "")):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        with self.connection() as c:
            c.execute("BEGIN")
            if before:
                canonical_id(before)
                anchor = c.execute("SELECT created_at,id FROM feedback_post WHERE id=?", (before,)).fetchone()
                if anchor is None:
                    raise FeedbackError("목록을 새로고침해 주세요.", 409)
                clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
                params.extend([anchor[0], anchor[0], anchor[1]])
            # Only a page of metadata crosses the storage/API boundary. Neither
            # the full body nor attachment bytes are loaded for list rendering.
            rows = c.execute(f"""SELECT id,author_id,author_name,title,kind,status,
                created_at,updated_at,row_version FROM feedback_post
                WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,id DESC LIMIT ?""", (*params, limit+1)).fetchall()
            return {"items": [self._post(c, r, actor, detail=False) for r in rows[:limit]],
                    "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None}

    def get(self, actor, post_id):
        with self.connection() as c:
            return self._post(c, self._require(c, post_id), actor)

    def mutate(self, actor, *, action, operation_id, post_id=None, expected_version=None,
               title=None, kind=None, body=None, status=None, keep_ids=(), files=(), revalidate=None):
        canonical_id(operation_id)
        if action not in {"create", "edit", "delete", "status"}:
            raise FeedbackError("지원하지 않는 작업입니다.")
        if action in {"create", "edit"}:
            title = text_field(title, 200, "제목")
            body = text_field(body, 50000, "내용")
            if kind not in {"bug", "improvement"}:
                raise FeedbackError("요청 유형이 올바르지 않습니다.")
        if any(not isinstance(x, str) for x in keep_ids) or len(keep_ids) != len(set(keep_ids)):
            raise FeedbackError("첨부 목록이 올바르지 않습니다.")
        payload = dict(action=action, post_id=post_id, expected_version=expected_version,
                       title=title, kind=kind, body=body, status=status, keep_ids=sorted(keep_ids),
                       files=[(f[0], f[1], sha256(f[2]).hexdigest()) for f in files])
        digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                if revalidate:
                    actor = revalidate()
                replay = c.execute("SELECT request_hash,result_json FROM feedback_operation WHERE actor_id=? AND operation_id=?", (actor["id"], operation_id)).fetchone()
                if replay:
                    if replay[0] != digest:
                        raise FeedbackError("같은 요청 식별자가 다른 내용에 사용되었습니다.", 409)
                    c.execute("COMMIT")
                    return json.loads(replay[1])
                now = time.time_ns() // 1_000_000
                if action == "create":
                    post_id = str(uuid4())
                    if keep_ids:
                        raise FeedbackError("새 글에는 기존 첨부를 연결할 수 없습니다.")
                    c.execute("INSERT INTO feedback_post(id,author_id,author_name,title,kind,body,created_at,updated_at,row_version) VALUES(?,?,?,?,?,?,?,?,1)",
                              (post_id, actor["id"], actor["display_name"], title, kind, body, now, now))
                else:
                    row = self._require(c, post_id)
                    allowed = actor["role"] == "admin" if action == "status" else row["author_id"] == actor["id"]
                    if not allowed:
                        raise FeedbackError("이 작업을 수행할 권한이 없습니다.", 403)
                    if type(expected_version) is not int or row["row_version"] != expected_version:
                        raise FeedbackError("다른 창에서 글이 변경되었습니다. 작성 내용을 보관한 뒤 최신 글을 확인해 주세요.", 409)
                    now = max(now, row["updated_at"]+1)
                    if action == "delete":
                        c.execute("UPDATE feedback_post SET deleted_at=?,updated_at=?,row_version=row_version+1 WHERE id=?", (now, now, post_id))
                    elif action == "status":
                        if status not in {"received", "reviewing", "completed"}:
                            raise FeedbackError("처리 상태가 올바르지 않습니다.")
                        c.execute("UPDATE feedback_post SET status=?,updated_at=?,row_version=row_version+1 WHERE id=?", (status, now, post_id))
                    else:
                        existing = {r[0] for r in c.execute("SELECT id FROM feedback_attachment WHERE post_id=? AND removed_at IS NULL", (post_id,))}
                        if not set(keep_ids) <= existing:
                            raise FeedbackError("다른 글의 첨부를 사용할 수 없습니다.")
                        c.execute("UPDATE feedback_post SET title=?,kind=?,body=?,updated_at=?,row_version=row_version+1 WHERE id=?", (title, kind, body, now, post_id))
                        for attachment_id in existing - set(keep_ids):
                            c.execute("UPDATE feedback_attachment SET removed_at=? WHERE id=?", (now, attachment_id))
                if action in {"create", "edit"}:
                    for filename, media_type, content in files:
                        c.execute("INSERT INTO feedback_attachment VALUES(?,?,?,?,?,?,NULL)",
                                  (str(uuid4()), post_id, filename, media_type, sha256(content).hexdigest(), content))
                row = c.execute("SELECT * FROM feedback_post WHERE id=?", (post_id,)).fetchone()
                c.execute("INSERT INTO feedback_history(post_id,actor_id,action,occurred_at,snapshot_json) VALUES(?,?,?,?,?)",
                          (post_id, actor["id"], action, now, json.dumps(dict(row), ensure_ascii=False)))
                result = {"id": post_id, "row_version": row["row_version"], "action": action}
                c.execute("INSERT INTO feedback_operation VALUES(?,?,?,?)", (actor["id"], operation_id, digest, json.dumps(result)))
                c.execute("COMMIT")
                return result
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise

    def download(self, actor, post_id, attachment_id):
        with self.connection() as c:
            self._require(c, post_id)
            row = c.execute("SELECT * FROM feedback_attachment WHERE post_id=? AND id=? AND removed_at IS NULL", (post_id, canonical_id(attachment_id))).fetchone()
            if row is None:
                raise FeedbackError("첨부 파일을 찾을 수 없습니다.", 404)
            if sha256(row["content"]).hexdigest() != row["sha256"]:
                raise FeedbackError("첨부 파일 검증에 실패했습니다.", 500)
            return dict(row)
