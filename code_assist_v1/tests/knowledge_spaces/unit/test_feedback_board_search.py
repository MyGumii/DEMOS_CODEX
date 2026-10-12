from __future__ import annotations

from uuid import uuid4

from flask import Flask
import pytest

from code_assist_v1.feedback_store import FeedbackStore, FeedbackError
from code_assist_v1.knowledge_core.workspace_feature_routes import register_workspace_feature_routes
from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import wiki


pytestmark = pytest.mark.unit
AUTHOR = {"id": "member01", "display_name": "김민수", "role": "user"}
OTHER = {"id": "other01", "display_name": "이서연", "role": "user"}
ADMIN = {"id": "admin", "display_name": "관리자", "role": "admin"}


def add(store, actor=AUTHOR, *, title="그래프 창 잘림", body="축소 시 선이 사라집니다.", kind="bug"):
    return store.mutate(actor, action="create", operation_id=str(uuid4()), title=title, body=body, kind=kind)["id"]


def ids(result):
    return {row["id"] for row in result["items"]}


def test_wf006_search_title_body_requester_name_and_id_with_combined_filters(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.db")
    graph = add(store)
    theme = add(store, OTHER, title="다크 테마", body="그래프 글자의 대비 개선", kind="improvement")
    literal = add(store, title="진행률 100%_ 표시", body=r"C:\tmp 폴더")
    add(store, OTHER, title="진행률 100xx 표시", body="대기시간 표시 요청")
    deleted = add(store, title="삭제된 내용만 확인")
    store.mutate(AUTHOR, action="delete", operation_id=str(uuid4()), post_id=deleted, expected_version=1)
    store.mutate(ADMIN, action="status", operation_id=str(uuid4()), post_id=graph, expected_version=1, status="completed")
    assert ids(store.list(OTHER, query="그래프")) == {graph, theme}
    assert ids(store.list(OTHER, query="그래프 김민수")) == {graph}
    assert ids(store.list(OTHER, query="축소", search_field="title")) == set()
    assert ids(store.list(OTHER, query="축소", search_field="body")) == {graph}
    assert ids(store.list(OTHER, query="민수", search_field="requester")) == {graph, literal}
    assert ids(store.list(OTHER, query="MEMBER01", search_field="requester")) == {graph, literal}
    assert ids(store.list(OTHER, query="100%_")) == {literal}
    assert ids(store.list(OTHER, query=r"C:\tmp")) == {literal}
    assert ids(store.list(OTHER, query="' OR 1=1 --")) == set()
    assert ids(store.list(OTHER, query="삭제된", search_field="title")) == set()
    assert ids(store.list(OTHER, kind="bug", status="completed")) == {graph}
    assert ids(store.list(OTHER, query="그래프", mine=True)) == {theme}
    assert ids(store.list(AUTHOR, query="이서연", mine=True)) == set()
    assert all("body" not in row for row in store.list(AUTHOR)["items"])


def test_wf006_cursor_pages_are_bounded_complete_and_survive_deleted_anchor(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.db")
    expected = {add(store, title=f"다중 페이지 {n:03d}") for n in range(65)}
    # Equal timestamps must still paginate deterministically by immutable ID.
    with store.connection() as c:
        c.execute("UPDATE feedback_post SET created_at=1000")
    first = store.list(AUTHOR)
    second = store.list(AUTHOR, before=first["next_cursor"])
    third = store.list(AUTHOR, before=second["next_cursor"])
    assert [len(p["items"]) for p in (first, second, third)] == [30, 30, 5]
    assert not (ids(first) & ids(second) or ids(second) & ids(third))
    assert ids(first) | ids(second) | ids(third) == expected
    assert third["next_cursor"] is None
    assert store.list(AUTHOR, before=first["next_cursor"]) == second
    store.mutate(AUTHOR, action="delete", operation_id=str(uuid4()),
        post_id=first["next_cursor"], expected_version=1)
    add(store, title="동시에 생성된 새 글")
    assert store.list(AUTHOR, before=first["next_cursor"]) == second
    assert len(store.list(AUTHOR, query="페이지")["items"]) == 30


@pytest.mark.parametrize("arguments", [
    {"query": "x" * 201}, {"query": "\x00"}, {"query": " ".join(str(i) for i in range(9))},
    {"search_field": "unknown"}, {"kind": "invalid"}, {"status": "invalid"},
    {"mine": "1"}, {"limit": True}, {"before": "bad-cursor"},
])
def test_wf006_invalid_search_parameters_fail_explicitly(tmp_path, arguments):
    store = FeedbackStore(tmp_path / "feedback.db")
    with pytest.raises(FeedbackError):
        store.list(AUTHOR, **arguments)


def test_wf006_http_filters_reach_storage_and_mine_uses_authenticated_actor(wiki):
    _, _, source, management, _ = wiki
    app = Flask(__name__)
    register_workspace_feature_routes(app, source, management,
        lambda: {"id": "member01", "scope_epoch": 1})
    store = app.extensions["workspace_features"]["feedback"]
    mine = add(store, AUTHOR)
    add(store, OTHER)
    client = app.test_client()
    response = client.get("/api/code/feedback", query_string={"q": "member01", "field": "requester", "kind": "bug", "status": "received", "mine": "1"})
    assert response.status_code == 200 and ids(response.json) == {mine}
    assert client.get("/api/code/feedback?mine=1&q=other01").json["items"] == []
    for parameters in ({"mine": "true"}, {"field": "sql"}, {"q": "x" * 201}, {"status": "unknown"}):
        assert client.get("/api/code/feedback", query_string=parameters).status_code == 400
