"""UI-R5: undated items live in the timeline's dedicated menu."""

from datetime import date, timedelta
from urllib.parse import urlsplit

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema, _surface
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_timeline_undated_item_is_only_in_undated_menu(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      State.pageData.blocks = [EditorCore.createBlock('timeline', {
        content:'날짜 비교', props:{
          properties:[
            {id:'title',name:'이름',type:'title'},
            {id:'date',name:'날짜',type:'date'},
          ],
          rows:[
            {title:'날짜 있는 항목',date:'2026-09-16'},
            {title:'날짜 없는 항목'},
          ],
          views:[{id:'timeline-view',name:'날짜 비교',type:'timeline'}],
          active_view_id:'timeline-view',
          timeline_date_property:'date',
          timeline_anchor:'2026-09-16',
          timeline_scale:'month',
        },
      })];
      renderBlocks();
    }""")
    timeline = page.locator('#blocks .timeline-block')
    expect(timeline).to_be_visible()
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    expect(timeline.locator('.timeline-page')).to_contain_text('날짜 있는 항목')
    undated = timeline.locator('.database-undated')
    expect(undated.locator('summary')).to_contain_text('날짜 없음(1)')
    undated.locator('summary').click()
    expect(undated).to_contain_text('클릭하여 타임라인에 추가하세요.')
    expect(undated.locator('.database-undated-row')).to_contain_text('날짜 없는 항목')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_undated_assignment_moves_to_timeline_and_survives_reload(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{
        'type': 'timeline', 'content': '날짜 지정',
        'props': {
            'properties': [
                {'id': 'title', 'name': '이름', 'type': 'title'},
                {'id': 'date', 'name': '날짜', 'type': 'date'},
            ],
            'rows': [{'title': '날짜 있는 항목', 'date': '2026-09-16'},
                     {'title': '날짜 없는 항목'}, {'title': '두 번째 날짜 없는 항목'}],
            'views': [{'id': 'timeline-view', 'name': '날짜 비교', 'type': 'timeline'}],
            'active_view_id': 'timeline-view',
            'timeline_date_property': 'date',
            'timeline_anchor': '2026-09-16',
            'timeline_scale': 'month',
        },
    }])
    timeline = page.locator('#blocks .timeline-block')
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    timeline.locator('.database-undated summary').click()
    expect(timeline.locator('.database-undated summary')).to_contain_text('날짜 없음(2)')
    timeline.locator('[data-undated-search]').fill('날짜')
    timeline.locator('[data-undated-assign="1"]').click()
    expect(timeline.locator('.timeline-row')).to_have_count(2)
    expect(timeline.locator('.database-undated[open] summary')).to_contain_text('날짜 없음(1)')
    expect(timeline.locator('.database-undated[open] [data-undated-search]')).to_have_value('날짜')
    today = date.fromisoformat(page.evaluate("() => localIsoDate(new Date())"))
    assert page.evaluate("() => State.pageData.blocks[0].props.rows[1].date") == {
        'start': today.isoformat(), 'end': (today + timedelta(days=4)).isoformat()}
    timeline.locator('[data-undated-assign="2"]').click()
    expect(timeline.locator('.timeline-row')).to_have_count(3)
    expect(timeline.locator('.database-undated')).to_have_count(0)
    page.evaluate("() => savePage()")
    page.reload()
    page.wait_for_function("() => State.pageData?.blocks?.length && !State.saving")
    expect(timeline.locator('.timeline-row')).to_have_count(3)
    expect(timeline.locator('.database-undated')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_week_timeline_undated_assignment_uses_one_day(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{
        'type': 'timeline', 'content': '주간 날짜 지정',
        'props': {
            'properties': [
                {'id': 'title', 'name': '이름', 'type': 'title'},
                {'id': 'date', 'name': '날짜', 'type': 'date'},
            ],
            'rows': [{'title': '날짜 없는 항목'}],
            'views': [{'id': 'timeline-view', 'name': '날짜 비교', 'type': 'timeline'}],
            'active_view_id': 'timeline-view',
            'timeline_date_property': 'date',
            'timeline_anchor': page.evaluate('() => localIsoDate(new Date())'),
            'timeline_scale': 'week',
        },
    }])
    timeline = page.locator('#blocks .timeline-block')
    timeline.locator('.database-undated summary').click()
    timeline.locator('[data-undated-assign="0"]').click()
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    expect(timeline.locator('.database-undated')).to_have_count(0)
    assert page.evaluate('() => State.pageData.blocks[0].props.rows[0].date') == (
        page.evaluate('() => localIsoDate(new Date())'))


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_linked_timeline_keeps_undated_menu_after_canonical_refresh(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""async () => {
      const today = localIsoDate(new Date());
      const rows = [
        {title:'연결 첫 항목',_canonical_row_id:'linked-row-one',
          _canonical_cell_versions:{date:0}},
        {title:'연결 둘째 항목',_canonical_row_id:'linked-row-two',
          _canonical_cell_versions:{date:0}},
      ];
      const node = document.createElement('div');
      node.id = 'linked-timeline-probe';
      document.body.append(node);
      const block = NotionLinkedDatabases.descriptor('linked-timeline-probe',
        {document_id:'source-page',id:'source-database'},
        {type:'timeline',name:'연결 날짜 비교',config:{timeline_anchor:today,
          timeline_scale:'month',timeline_date_property:'date'}});
      const writes = [];
      let view;
      const host = {
        current:()=>true,visible:()=>true,canEdit:()=>true,canEditCells:()=>true,
        load:async()=>({database:{title:'연결 원본'},source:{document_id:'source-page'}}),
        project:()=>EditorCore.createBlock('timeline',{content:'연결 날짜 비교',props:{
          properties:[{id:'title',name:'이름',type:'title'},
            {id:'date',name:'날짜',type:'date'}],rows:structuredClone(rows),
          timeline_anchor:today,timeline_scale:'month',timeline_date_property:'date'}}),
        render:(projected,editable)=>databaseBlockHtml(projected,editable),
        dates:NotionDatabaseDates,bindUndated:bindUndatedSearch,
        assignDate:async(_scope,_id,_data,cell)=>{
          writes.push(structuredClone(cell));
          const row = rows.find(item=>item._canonical_row_id===cell.row_id);
          row[cell.property_id]=cell.value;
          row._canonical_cell_versions[cell.property_id]++;
          await view.refresh(true);
        },
        commit:()=>true,open:()=>{},
      };
      view=NotionLinkedDatabases.create(host);
      window.linkedTimelineProbe={rows,writes};
      await view.mount({pageId:'probe',workspaceId:'probe'},[{id:block.id,block,node}]);
    }""")
    timeline = page.locator('#linked-timeline-probe .timeline-block')
    expect(timeline.locator('.database-undated summary')).to_contain_text('날짜 없음(2)')
    timeline.locator('.database-undated summary').click()
    timeline.locator('[data-undated-search]').fill('연결')
    timeline.locator('[data-undated-assign="0"]').click()
    expect(timeline.locator('.database-undated[open] summary')).to_contain_text('날짜 없음(1)')
    expect(timeline.locator('[data-undated-search]')).to_have_value('연결')
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    today = date.fromisoformat(page.evaluate('() => localIsoDate(new Date())'))
    assert page.evaluate('() => window.linkedTimelineProbe.writes[0].value') == {
        'start': today.isoformat(), 'end': (today + timedelta(days=4)).isoformat()}


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_canonical_undated_assignment_uses_atomic_cell_command(document_page):
    page = document_page
    page.evaluate("""() => {
      const block = EditorCore.createBlock('timeline', {
        content:'협업 날짜 지정', props:{
          database_id:'database-probe',_canonical_version:1,
          properties:[
            {id:'title',name:'이름',type:'title'},
            {id:'date',name:'날짜',type:'date'},
          ],
          rows:[{title:'날짜 없는 항목',_canonical_row_id:'row-probe',
            _canonical_cell_versions:{date:0}}],
          timeline_date_property:'date',timeline_anchor:localIsoDate(new Date()),
          timeline_scale:'month',
        },
      });
      State.pageData.blocks=[block];
      State.pageData.databases=[{id:'database-probe',capabilities:{atomic_cell_batch:true}}];
      collaborativeDatabaseActive=()=>true;
      activePageCanEdit=()=>true;
      window.undatedCommandProbe=[];
      commitDatabaseGridRange=async (target,changes)=>{
        window.undatedCommandProbe.push({databaseId:target.props.database_id,
          rowId:changes[0].item._canonical_row_id,property:changes[0].column,
          value:changes[0].value});
      };
      renderBlocks();
    }""")
    page.locator('#blocks .timeline-block .database-undated summary').click()
    page.locator('#blocks .timeline-block [data-undated-assign="0"]').click()
    page.wait_for_function("() => window.undatedCommandProbe.length === 1")
    today = date.fromisoformat(page.evaluate("() => localIsoDate(new Date())"))
    assert page.evaluate("() => window.undatedCommandProbe[0]") == {
        'databaseId': 'database-probe', 'rowId': 'row-probe',
        'property': 'date', 'value': {
            'start': today.isoformat(), 'end': (today + timedelta(days=4)).isoformat()},
    }
    page.evaluate("""() => {
      State.pageData.databases[0].capabilities.atomic_cell_batch = false;
      renderBlocks();
    }""")
    page.locator('#blocks .timeline-block .database-undated summary').click()
    page.locator('#blocks .timeline-block [data-undated-assign="0"]').click()
    assert page.evaluate("() => window.undatedCommandProbe.length") == 1
    assert page.evaluate("() => State.pageData.blocks[0].props.rows[0].date") is None
    assert '날짜를 저장할 수 없습니다' in page.locator('#status').inner_text()


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('scale', ['month', 'hour'])
def test_ui_r5_undated_click_commits_to_isolated_2423_database(
        document_page, activity_environment, monkeypatch, scale):
    from playwright.sync_api import expect

    source, workspace_id = activity_environment
    owned_database_block(source)
    _surface(source.iam, 'database.row.create', 'row-undated-second', {},
             'undated-browser-second', actor='owner', target_version=0)
    _schema(source.iam, 'database.property.create', 'undated-due',
        {'name': '날짜', 'type': 'date', 'config': {}}, 'undated-browser-date', actor='owner')
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        'before-undated-browser-database-trash.db')
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        'before-undated-browser-row-trash.db')
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    client, _actor = _client(store, monkeypatch, enabled=False)
    document = store.iam.read_document('owner', 'page-e1')
    database = store.iam.read_database('owner', 'database-e1')
    assert database['capabilities']['atomic_cell_batch'] is True
    assert database['rows'][0]['cells'].get('undated-due') is None
    requests = []
    base = f'/api/notion/workspaces/{workspace_id}/pages/page-e1'

    def route_to_2423(route):
        request = route.request
        address = urlsplit(request.url)
        target = address.path + ('?' + address.query if address.query else '')
        if not address.path.startswith(base + '/databases/'):
            route.continue_()
            return
        response = client.open(target, method=request.method,
            data=request.post_data, content_type='application/json')
        requests.append((request.method, address.path, response.status_code))
        route.fulfill(status=response.status_code, content_type='application/json',
            body=response.get_data())

    page = document_page
    page.route('**/api/notion/workspaces/**', route_to_2423)
    page.evaluate("""({workspaceId,document,database,scale}) => {
      const block = EditorCore.createBlock('timeline', {
        content:'2423 날짜 지정',props:{database_id:'database-e1',
          timeline_date_property:'undated-due',
          timeline_anchor:localIsoDate(new Date()),timeline_scale:scale},
      });
      State.activePageId='page-e1';
      Object.assign(State.pageData.page, {
        id:'page-e1',document_sequence:document.document_sequence,
        workspace_epoch:document.workspace_epoch,
        document_epoch:document.document_epoch,
        collaborative_databases:true,iam_role:'full_access',
      });
      State.pageData.blocks=DatabaseBridge.applyCanonicalDatabases([block],[database]);
      State.pageData.databases=[database];
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      collaborativeDatabaseActive=()=>true;
      activePageCanEdit=()=>true;
      renderBlocks();
    }""", {'workspaceId': workspace_id, 'document': document, 'database': database,
           'scale': scale})
    timeline = page.locator('#blocks .timeline-block')
    expect(timeline.locator('.database-undated summary')).to_contain_text('날짜 없음(2)')
    if scale == 'hour':
        timeline.locator('.timeline-scroll').evaluate('scroll => scroll.scrollLeft=29*72')
    timeline.locator('.database-undated summary').click()
    timeline.locator('[data-undated-assign="0"]').click()
    expect(timeline.locator('.database-undated[open] summary')).to_contain_text(
        '날짜 없음(1)', timeout=30_000)
    expect(timeline.locator('.timeline-row')).to_have_count(1)
    saved = store.iam.read_database('owner', 'database-e1')
    today = date.fromisoformat(page.evaluate('() => localIsoDate(new Date())'))
    expected = ({'start': today.isoformat(), 'end': today.isoformat(),
                 'time': '08:15', 'end_time': '09:15'} if scale == 'hour' else
                {'start': today.isoformat(),
                 'end': (today + timedelta(days=4)).isoformat()})
    assert saved['rows'][0]['cells']['undated-due'] == expected
    assert saved['rows'][1]['cells'].get('undated-due') is None
    assert any(method == 'POST' and path == base + '/databases/commands'
        and 200 <= status < 300 for method, path, status in requests), requests
