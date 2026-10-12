"""UI-CHAT-HISTORY: search the authorized encrypted history, not only the loaded page."""
from uuid import uuid4

from code_assist_v1.tests.test_notion_ai_home import setup, create


def seed(service, *, workspace="", title="제목", answer="본문", pages=None):
    store = service.store("owner")
    record = store.create(str(uuid4()), {"workspace_id": workspace, "page_ids": pages or [], "knowledge_space_id": ""})
    op = str(uuid4())
    claim = store.begin(record['id'], op, {}, 0)['claim']
    store.finish(record['id'], op, claim, title, {"answer": answer})
    return record['id']


def test_ui_chat_history_searches_title_and_body_without_returning_message_content(setup):
    http, service, _, _ = setup
    match = seed(service, title="개발 계획", answer="BODY-ONLY SearchToken")
    seed(service, title="다른 대화")
    for query in ["개발", "searchtoken", "  SearchToken  "]:
        response = http.get('/api/notion/ai-home/conversations', query_string={'q': query})
        assert response.status_code == 200
        assert [i['id'] for i in response.json['items']] == [match]
        assert set(response.json['items'][0]) == {'id', 'title', 'updated_at', 'archived', 'revision'}
    assert http.get('/api/notion/ai-home/conversations', query_string={'q': '%' }).json['items'] == []
    assert http.get('/api/notion/ai-home/conversations', query_string={'q': 'x'*201}).status_code == 400


def test_ui_chat_history_search_finds_older_match_behind_full_pages_and_other_workspaces(setup):
    http, service, _, _ = setup
    oldest = seed(service, title="old matching needle")
    for index in range(55):
        seed(service, workspace='other' if index % 2 else '', title=f"unrelated {index}")
    response = http.get('/api/notion/ai-home/conversations?q=needle')
    assert [i['id'] for i in response.json['items']] == [oldest]
    assert response.json['next_cursor'] is None


def test_ui_chat_history_result_pagination_and_archive_filter_have_no_missing_matches(setup):
    http, service, _, _ = setup
    ids = {seed(service, title=f"needle {index}") for index in range(55)}
    archived = seed(service, title="needle archived")
    service.store('owner').archive(archived, str(uuid4()), True)
    first = http.get('/api/notion/ai-home/conversations?q=needle').json
    assert len(first['items']) == 50 and first['next_cursor'] is not None
    second = http.get('/api/notion/ai-home/conversations', query_string={'q':'needle','cursor':first['next_cursor']}).json
    assert len(second['items']) == 5 and second['next_cursor'] is None
    assert {i['id'] for i in first['items']+second['items']} == ids
    assert [i['id'] for i in http.get('/api/notion/ai-home/conversations?q=needle&archived=1').json['items']] == [archived]


def test_ui_chat_history_revoked_and_foreign_history_never_matches(setup, monkeypatch):
    http, service, _, pid = setup
    allowed = seed(service, title="needle allowed")
    seed(service, title="needle restricted", pages=[pid])
    seed(service, workspace='other', title="needle other workspace")
    original = service.authorize
    def authorize(uid, record):
        if pid in record['scope']['page_ids']:
            raise PermissionError('revoked')
        return original(uid, record)
    monkeypatch.setattr(service, 'authorize', authorize)
    response = http.get('/api/notion/ai-home/conversations?q=needle').json
    assert [i['id'] for i in response['items']] == [allowed]
    assert response['next_cursor'] is None
