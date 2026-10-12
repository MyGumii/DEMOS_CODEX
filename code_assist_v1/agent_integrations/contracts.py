"""Strict, bounded contracts shared by HTTP, workers and MCP."""
from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID


class IntegrationError(Exception):
    def __init__(self, message, status=400, code="VALIDATION_ERROR"):
        super().__init__(message)
        self.http_status, self.code = status, code


def text(value, name, limit=2000, *, empty=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit or (not empty and not value.strip()) or "\x00" in value:
        raise IntegrationError(f"{name} 값이 올바르지 않습니다.")
    return value


def uid(value, name="id"):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise IntegrationError(f"{name}는 UUID여야 합니다.") from None
    return value


def integer(value, name, minimum=1, maximum=2147483647):
    if type(value) is not int or not minimum <= value <= maximum:
        raise IntegrationError(f"{name} 값이 올바르지 않습니다.")
    return value


def object_shape(value, required=(), optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise IntegrationError("요청 필드가 올바르지 않습니다.")
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


SCOPES = frozenset({"knowledge:read", "knowledge:ask", "results:write", "notion:write", "mail:read"})
SET_FIELDS = ("knowledge_sets_version", "read_mode", "personal_space_ids", "shared_space_id",
              "write_personal_space_ids", "write_org_space_id", "write_modes")
READ_MODES = ("personal", "personal_shared")


def ids(value, name):
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise IntegrationError(f"{name}는 1~64개를 선택해 주세요.")
    clean = [uid(v, name) for v in value]
    if len(set(clean)) != len(clean):
        raise IntegrationError(f"{name}가 중복되었습니다.")
    return clean


def read_mode(value):
    if not isinstance(value, str) or value not in READ_MODES:
        raise IntegrationError("조회 모드는 내 지식 또는 내 지식+공유 지식이어야 합니다.")
    return value


def publication_target(value):
    object_shape(value, ("mode",), ("personal_space_id", "organization_space_id"))
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in {"personal", "organization", "both"}:
        raise IntegrationError("등록 대상을 선택해 주세요.")
    expected = {"mode"}
    if mode in {"personal", "both"}:
        expected.add("personal_space_id")
    if mode in {"organization", "both"}:
        expected.add("organization_space_id")
    if set(value) != expected:
        raise IntegrationError("등록 방식에 맞는 Wiki·조직 대상을 지정해 주세요.")
    return {k: v if k == "mode" else uid(v, k) for k, v in value.items()}


def connection_input(body):
    object_shape(body, ("operation_id", "name", "read_space_id", "write_space_id", "scopes"),
                 ("expires_days", "auto_renew", "auto_publish_sources", "notion_parent_id", *SET_FIELDS))
    uid(body["operation_id"], "operation_id")
    scopes = body["scopes"]
    if not isinstance(scopes, list) or not scopes or any(type(s) is not str or s not in SCOPES for s in scopes) or len(set(scopes)) != len(scopes):
        raise IntegrationError("접근 범위를 선택해 주세요.")
    auto = body.get("auto_publish_sources", False)
    if type(auto) is not bool:
        raise IntegrationError("자동 등록 설정이 올바르지 않습니다.")
    renew = body.get("auto_renew", False)
    if type(renew) is not bool:
        raise IntegrationError("자동 연장 설정이 올바르지 않습니다.")
    config = dict(name=text(body["name"], "연결 이름", 180),
                read_space_id=uid(body["read_space_id"]), write_space_id=uid(body["write_space_id"]),
                scopes=sorted(scopes), expires_days=integer(body.get("expires_days", 30), "유효기간", 1, 365),
                **({"auto_renew": renew} if "auto_renew" in body else {}), auto_publish_sources=auto,
                notion_parent_id=text(body.get("notion_parent_id", ""), "Notion 위치", 200, empty=True))
    if any(k in body for k in SET_FIELDS):
        if type(body.get("knowledge_sets_version")) is not int or body["knowledge_sets_version"] != 2:
            raise IntegrationError("지식 세트 연결 버전 2가 필요합니다.")
        mode = read_mode(body.get("read_mode"))
        personal = ids(body.get("personal_space_ids"), "참고 개인 Wiki")
        writable = ids(body.get("write_personal_space_ids", [config["write_space_id"]]), "등록 개인 Wiki")
        shared = body.get("shared_space_id", "")
        org = body.get("write_org_space_id", "")
        if mode == "personal_shared":
            shared = uid(shared, "공유 범위")
        elif shared != "":
            raise IntegrationError("내 지식 모드에는 공유 범위를 지정할 수 없습니다.")
        if org != "":
            org = uid(org, "등록 조직")
        modes = body.get("write_modes", ["personal"])
        if (not isinstance(modes, list) or not modes or len(modes) != len(set(m for m in modes if isinstance(m, str)))
                or any(not isinstance(m, str) or m not in {"personal", "organization", "both"} for m in modes)):
            raise IntegrationError("등록 허용 방식을 선택해 주세요.")
        if bool(org) != any(m in modes for m in ("organization", "both")):
            raise IntegrationError("조직 등록 방식과 조직 대상을 함께 지정해 주세요.")
        if config["read_space_id"] not in personal or config["write_space_id"] not in writable:
            raise IntegrationError("기본 Wiki가 선택한 Wiki 목록에 포함되어야 합니다.")
        config.update(knowledge_sets_version=2, read_mode=mode, personal_space_ids=personal,
                      shared_space_id=shared, write_personal_space_ids=writable,
                      write_org_space_id=org, write_modes=modes)
    return config


def submission_input(body):
    object_shape(body, ("operation_id", "kind", "title", "sources"), ("result", "actions", "external_key", "followup_of", "read_mode", "publication_target"))
    uid(body["operation_id"], "operation_id")
    kind = text(body["kind"], "작업 종류", 80)
    if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", kind) is None:
        raise IntegrationError("작업 종류는 영문 식별자로 입력해 주세요.")
    sources = body["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 24:
        raise IntegrationError("원본 자료는 1~24개가 필요합니다.")
    clean, ids = [], set()
    for source in sources:
        object_shape(source, ("id", "kind", "title", "text"), ("uri", "external_id", "revision"))
        sid = text(source["id"], "원본 식별자", 100)
        if sid in ids:
            raise IntegrationError("원본 식별자가 중복되었습니다.")
        ids.add(sid)
        if not isinstance(source["kind"],str) or source["kind"] not in {"document", "email", "meeting", "user_input", "agent_output", "execution_report"}:
            raise IntegrationError("원본 종류가 올바르지 않습니다.")
        item = {"id": sid, "kind": source["kind"], "title": text(source["title"], "원본 제목", 500),
                "text": text(source["text"], "원본 본문", 100000)}
        for key in ("uri", "external_id", "revision"):
            item[key] = text(source.get(key, ""), key, 2000, empty=True)
        item["sha256"] = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
        # Client statements never become server observations, regardless of kind.
        item["trust"] = "submitted_reference"
        clean.append(item)
    actions = body.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 32:
        raise IntegrationError("액션은 최대 32개입니다.")
    clean_actions = []
    for i, action in enumerate(actions):
        object_shape(action, ("description", "completion_condition"), ("source_id",))
        if action.get("source_id") is not None and action["source_id"] not in ids:
            raise IntegrationError("액션의 원본 식별자가 올바르지 않습니다.")
        clean_actions.append(dict(id=f"action-{i+1}", description=text(action["description"], "액션", 2000),
                                  completion_condition=text(action["completion_condition"], "완료 조건", 2000),
                                  source_id=action.get("source_id"), state="proposed"))
    result = dict(kind=kind, title=text(body["title"], "제목", 500), sources=clean,
                  result=text(body.get("result", ""), "산출물", 100000, empty=True), actions=clean_actions,
                  external_key=text(body.get("external_key", ""), "외부 식별자", 500, empty=True))
    result["followup_of"] = uid(body["followup_of"],"followup_of") if "followup_of" in body else None
    if "read_mode" in body:
        result["read_mode"] = read_mode(body["read_mode"])
    if "publication_target" in body:
        result["publication_target"] = publication_target(body["publication_target"])
    if len(encoded(result).encode("utf-8")) > 500000:
        raise IntegrationError("작업 전체 크기는 500KB 이하여야 합니다.", 413)
    return result
