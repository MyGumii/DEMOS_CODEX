"""UI-R5: personal linked database selection and source revocation."""
from __future__ import annotations

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_link_command_waits_for_server_capability(document_page):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.url.rsplit('/', 1)[-1]

    def old_page_response(route):
        if route.request.method != 'GET' or not route.request.url.endswith(f'/api/notion/pages/{page_id}'):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload.get('capabilities', {}).pop('personal_linked_database', None)
        route.fulfill(response=response, json=payload)

    page.route('**/api/notion/pages/*', old_page_response)
    page.reload()
    page.wait_for_function('() => State.pageData?.page?.id === location.pathname.split("/").pop()')
    assert page.evaluate('() => State.pageData.capabilities.personal_linked_database') is None
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    menu = page.locator('#blockMenu')
    expect(menu).to_have_attribute('data-mode', 'inserted')
    expect(menu.locator('[data-insert-type="linked_database"]')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_view_saves_refreshes_and_revokes(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.url.rsplit('/', 1)[-1]
    assert page.evaluate('() => State.pageData.capabilities.personal_linked_database') is True
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'원본 업무 표'}});
      const id = created.page.id;
      const saved = await api(`/api/notion/pages/${encodeURIComponent(id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'db-personal-source',type:'table',content:'업무 데이터',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],
            rows:[{name:'첫 번째 항목'}]}}]},
      });
      return {id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    menu = page.locator('#blockMenu')
    expect(menu).to_have_attribute('data-mode', 'inserted')
    blank_id = page.evaluate('() => State.insertedBlockMenu.blockId')
    menu.locator('[data-insert-type="linked_database"]:visible').first.click()
    expect(page.locator('#linkedSourceSearch')).to_be_visible()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='업무 데이터').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    page.wait_for_function("""args => State.pageData.blocks.some(block =>
      block.id === args.blank && block.props?.linked_database?.document_id === args.source)
      && !State.dirty""", arg={'blank':blank_id,'source':source['id']}, timeout=30_000)
    assert page.evaluate('() => State.pageData.capabilities.personal_linked_database') is True
    preview = page.locator(f'[data-linked-database="{blank_id}"]')
    expect(preview.locator('[data-db-field="name"]')).to_have_value('첫 번째 항목')
    _screenshot(page, tmp_path, 'personal-linked-database.png')

    full_page = preview.locator('[data-personal-linked-full-page]')
    expect(full_page).to_have_attribute('data-personal-linked-open-full-page', '')
    assert full_page.get_attribute('href') == f'/page/{page_id}?linked_db={blank_id}'
    full_page.click()
    expect(full_page).to_have_attribute('data-personal-linked-close-full-page', '')
    assert page.locator('#pageView').evaluate("node => node.classList.contains('database-full-page')")
    expect(page.locator('#breadcrumb .database-breadcrumb-current')).to_have_text('업무 데이터')
    title_size = preview.locator('.linked-database-toolbar .linked-source').evaluate(
        "node => parseFloat(getComputedStyle(node).fontSize)")
    assert title_size >= 24, title_size
    _screenshot(page, tmp_path, 'personal-linked-full-page.png')
    assert f'linked_db={blank_id}' in page.url
    page.reload()
    expect(full_page).to_have_attribute('data-personal-linked-close-full-page', '')
    page.go_back()
    expect(full_page).to_have_attribute('data-personal-linked-open-full-page', '')
    assert 'linked_db=' not in page.url
    full_page.click()
    expect(full_page).to_have_attribute('data-personal-linked-close-full-page', '')
    full_page.click()
    expect(full_page).to_have_attribute('data-personal-linked-open-full-page', '')
    expect(full_page).to_be_focused()
    assert not page.locator('#pageView').evaluate("node => node.classList.contains('database-full-page')")
    assert 'linked_db=' not in page.url

    page.reload()
    preview = page.locator(f'[data-linked-database="{blank_id}"]')
    expect(preview.locator('[data-db-field="name"]')).to_have_value('첫 번째 항목')
    expect(preview.locator('[data-db-field="name"]')).to_be_enabled()
    preview.locator('[data-db-field="name"]').fill('연결 보기에서 수정')
    preview.locator('[data-db-field="name"]').press('Tab')
    page.wait_for_function("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return current.blocks.find(block => block.id === source.blockId)?.props?.rows?.[0]?.name === '연결 보기에서 수정';
    }""", arg=source)
    expect(preview.locator('[data-db-field="name"]')).to_have_value('연결 보기에서 수정')

    lost_response = []
    replayed = []

    def lose_first_save_response(route):
        if lost_response:
            replayed.append(True)
            route.continue_()
            return
        saved = route.fetch()
        lost_response.append(saved.status)
        route.abort('failed')

    save_pattern = f"**/api/notion/pages/{source['id']}/save"
    page.route(save_pattern, lose_first_save_response)
    preview.locator('[data-db-field="name"]').fill('응답 유실 뒤에도 저장')
    preview.locator('[data-db-field="name"]').press('Tab')
    page.wait_for_function("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return current.blocks.find(block => block.id === source.blockId)?.props?.rows?.[0]?.name === '응답 유실 뒤에도 저장';
    }""", arg=source)
    expect(preview.locator('[data-db-field="name"]')).to_have_value('응답 유실 뒤에도 저장')
    for _ in range(50):
        if lost_response and replayed:
            break
        page.wait_for_timeout(100)
    assert lost_response == [200]
    assert replayed == [True]
    page.unroute(save_pattern, lose_first_save_response)

    concurrent_result = []
    csrf_token = page.evaluate('() => State.csrfToken')

    def concurrent_source_edit(route):
        origin = page.url.split('/page/')[0]
        response = page.context.request.put(
            f"{origin}/api/notion/pages/{source['id']}/blocks",
            headers={'Origin': origin, 'Referer': page.url, 'X-CSRF-Token': csrf_token},
            data={'blocks': [{'id': source['blockId'], 'type': 'table', 'content': '업무 데이터',
                              'props': {'view': 'table', 'properties': [{'id': 'name', 'name': '이름', 'type': 'title'}],
                                        'rows': [{'name': '다른 탭에서 수정'}]}}]},
        )
        concurrent_result.append((response.status, response.text()))
        route.continue_()

    page.route(f"**/api/notion/pages/{source['id']}/save", concurrent_source_edit)
    preview.locator('[data-db-field="name"]').fill('덮어쓰기 시도')
    preview.locator('[data-db-field="name"]').press('Tab')
    page.wait_for_function('() => !document.querySelector("[data-linked-database] [data-db-field=name]")?.disabled')
    assert concurrent_result and concurrent_result[0][0] == 200, concurrent_result
    expect(preview.locator('[data-db-field="name"]')).to_have_value('다른 탭에서 수정')
    page.unroute(f"**/api/notion/pages/{source['id']}/save", concurrent_source_edit)
    page.evaluate("""async source => {
      await api(`/api/notion/pages/${encodeURIComponent(source.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:source.blockId,type:'table',content:'업무 데이터',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],
            rows:[{name:'바뀐 항목'}]}}]},
      });
    }""", source)
    preview.locator('[data-personal-linked-refresh]').click()
    expect(preview.locator('[data-db-field="name"]')).to_have_value('바뀐 항목')

    page.evaluate("async id => api(`/api/notion/pages/${encodeURIComponent(id)}`, {method:'DELETE'})", source['id'])
    preview.locator('[data-personal-linked-refresh]').click()
    expect(preview).to_contain_text('원본에 접근할 수 없습니다.')
    expect(preview.locator('[data-db-field="name"]')).to_have_count(0)
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_date_cell_edits_and_clears_source(document_page):
    from playwright.sync_api import expect

    page = document_page
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'날짜 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'linked-date-source',type:'table',content:'날짜 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'due',name:'날짜',type:'date'}],
            rows:[{name:'기한 있는 업무',due:'2026-09-16'}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='날짜 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    button = preview.locator('[data-db-date-picker="due"]')
    expect(button).to_be_enabled()
    button.click()
    picker = page.locator('.database-date-picker')
    expect(picker).to_be_visible()
    picker.locator('[data-date-day="2026-09-17"]').click()
    expect(button).to_contain_text('9월 17일')
    assert page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return current.blocks.find(block => block.id === source.blockId).props.rows[0].due;
    }""", source) == '2026-09-17'
    expect(picker).to_be_visible()
    picker.locator('[data-date-clear]').click()
    expect(button).to_contain_text('비어 있음')
    assert page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return current.blocks.find(block => block.id === source.blockId).props.rows[0].due;
    }""", source) is None


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_filter_and_sort_stay_on_view(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'조건 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'linked-rules-source',type:'table',content:'조건 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'due',name:'날짜',type:'date'}],
            rows:[{name:'알파',due:'2026-09-16'},
              {name:'베타',due:{start:'2026-09-18'}},
              {name:'감마',due:'2026-09-17'}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='조건 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(3)

    preview.locator('[data-personal-linked-filters]').click()
    panel = preview.locator('[data-personal-linked-rules]')
    expect(panel).to_be_visible()
    panel.locator('[data-rule-add]').click()
    panel.locator('[data-rule-property]').select_option('name')
    panel.locator('[data-rule-value]').fill('알파')
    panel.locator('[data-rule-apply]').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    expect(preview.locator('[data-db-field="name"]')).to_have_value('알파')
    expect(preview.locator('[data-personal-linked-rule-chip="filters"]')).to_contain_text('알파')

    preview.locator('[data-personal-linked-filters]').click()
    panel.locator('[data-rule-clear]').click()
    panel.locator('[data-rule-apply]').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(3)
    preview.locator('[data-personal-linked-sorts]').click()
    panel.locator('[data-rule-add]').click()
    panel.locator('[data-rule-property]').select_option('due')
    panel.locator('[data-rule-direction]').select_option('desc')
    panel.locator('[data-rule-apply]').click()
    expect(preview.locator('[data-db-field="name"]')).to_have_count(3)
    assert preview.locator('[data-db-field="name"]').evaluate_all('(items) => items.map(item => item.value)') == ['베타','감마','알파']
    assert preview.locator('.table-header > button, .table-header > span').first.evaluate('(node) => node.firstElementChild?.tagName') == 'SMALL'
    _screenshot(page, tmp_path, 'personal-linked-sort.png')
    page.wait_for_function('() => !State.dirty')
    page.reload()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('[data-db-field="name"]')).to_have_count(3)
    assert preview.locator('[data-db-field="name"]').evaluate_all('(items) => items.map(item => item.value)') == ['베타','감마','알파']
    expect(preview.locator('[data-personal-linked-rule-chip="sorts"]')).to_have_count(1)
    source_state = page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      const block = current.blocks.find(item => item.id === source.blockId);
      return {names:block.props.rows.map(item => item.name),
        filters:block.props.filters || [],sorts:block.props.sorts || []};
    }""", source)
    assert source_state == {'names':['알파','베타','감마'],'filters':[],'sorts':[]}
    page.set_viewport_size({'width':390,'height':700})
    preview.locator('[data-personal-linked-sorts]').click()
    expect(preview.locator('[data-personal-linked-rules]')).to_be_visible()
    bounds = preview.locator('[data-personal-linked-rules]').bounding_box()
    assert bounds is not None and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    page.keyboard.press('Escape')
    expect(preview.locator('[data-personal-linked-rules]')).to_be_hidden()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_views_create_edit_duplicate_delete(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'복수 보기 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'linked-views-source',type:'table',content:'보기 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'state',name:'상태',type:'status',options:['진행','완료']},
            {id:'due',name:'날짜',type:'date'}],
            rows:[{name:'첫 항목',state:'진행',due:'2026-09-16'},
              {name:'둘째 항목',state:'완료',due:'2026-09-17'}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='보기 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('[data-personal-linked-add-view]')).to_be_visible()
    expect(preview.locator('[data-personal-linked-view-settings]')).to_be_visible()
    preview.locator('[data-personal-linked-view-settings]').click()
    menu = preview.locator('[data-linked-view-popover]')
    expect(menu).to_be_visible()
    menu.locator('[data-linked-pop-layout]').click()
    expect(menu.locator('[data-linked-view-layout-panel]')).to_be_visible()
    menu.locator('[data-linked-pop-back]').first.click()
    menu.locator('[data-linked-pop-properties]').click()
    expect(menu.locator('[data-linked-view-properties-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-view-properties.png')
    menu.locator('[data-linked-pop-property-search]').fill('날짜')
    expect(menu.locator('[data-linked-pop-property-row]:visible')).to_have_count(1)
    menu.locator('[data-linked-pop-visible="due"]').click()
    expect(preview.locator('.table-header > button, .table-header > span')).to_have_count(2)
    expect(menu.locator('[data-linked-pop-property-search]')).to_have_value('날짜')
    menu.locator('[data-linked-pop-visible="due"]').click()
    expect(preview.locator('.table-header > button, .table-header > span')).to_have_count(3)
    menu.locator('[data-linked-pop-property-search]').fill('')
    menu.locator('[data-view-property-id="due"] .database-view-property-handle').drag_to(
        menu.locator('[data-view-property-id="state"]'))
    expect(preview.locator('.table-header > button, .table-header > span').nth(1)).to_contain_text('날짜')
    expect(preview.locator('.table-header > button, .table-header > span').nth(2)).to_contain_text('상태')
    expect(menu.locator('[data-view-property-id="name"] .database-view-property-handle')).to_be_disabled()
    menu.locator('[data-linked-pop-back]').nth(1).click()
    menu.locator('[data-linked-pop-group]').click()
    expect(menu.locator('[data-linked-view-group-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-view-group.png')
    menu.locator('[data-linked-pop-group-search]').fill('상태')
    expect(menu.locator('[data-linked-pop-group-row]:visible')).to_have_count(1)
    menu.locator('[data-linked-pop-group-choice="state"]').click()
    expect(menu.locator('[data-linked-pop-group-choice="state"]')).to_have_attribute('aria-pressed','true')
    expect(menu.locator('[data-linked-pop-group-search]')).to_have_value('상태')
    expect(preview.locator('.database-group-label')).to_have_count(2)
    menu.locator('[data-linked-pop-group-sort]').select_option('asc')
    expect(preview.locator('.database-group-label')).to_have_count(2)
    assert [label.split()[0] for label in preview.locator('.database-group-label').all_text_contents()] == ['완료','진행']
    menu.locator('[data-linked-pop-group-sort]').select_option('manual')
    expect(preview.locator('.database-group-label')).to_have_count(2)
    menu.locator('[data-linked-pop-group-move="진행"][data-group-direction="1"]').click()
    expect(preview.locator('.database-group-label')).to_have_count(2)
    assert [label.split()[0] for label in preview.locator('.database-group-label').all_text_contents()] == ['완료','진행']
    menu.locator('[data-linked-view-group-panel] [data-linked-pop-back]').click()
    menu.locator('[data-linked-pop-sort]').click()
    expect(menu.locator('[data-linked-view-sort-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-view-sort.png')
    menu.locator('[data-linked-pop-sort-search]').fill('날짜')
    expect(menu.locator('[data-linked-pop-sort-row]:visible')).to_have_count(1)
    menu.locator('[data-linked-pop-sort-choice="due"]').click()
    expect(menu.locator('[data-linked-pop-sort-search]')).to_have_value('날짜')
    menu.locator('[data-linked-pop-sort-direction="0"]').select_option('desc')
    expect(menu.locator('[data-linked-pop-sort-direction="0"]')).to_have_value('desc')
    menu.locator('[data-linked-pop-sort-remove="0"]').click()
    expect(menu.locator('[data-linked-pop-sort-direction]')).to_have_count(0)
    menu.locator('[data-linked-pop-sort-choice="due"]').click()
    menu.locator('[data-linked-pop-sort-direction="0"]').select_option('desc')
    menu.locator('[data-linked-view-sort-panel] [data-linked-pop-back]').click()
    menu.locator('[data-linked-pop-filter]').click()
    expect(menu.locator('[data-linked-view-filter-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-view-filter.png')
    menu.locator('[data-linked-pop-filter-search]').fill('상태')
    expect(menu.locator('[data-linked-pop-filter-row]:visible')).to_have_count(1)
    menu.locator('[data-linked-pop-filter-choice="state"]').click()
    _screenshot(page, tmp_path, 'personal-linked-view-filter-rule.png')
    assert menu.locator('[data-linked-pop-filter-value="0"]').evaluate('node => node.tagName') == 'SELECT'
    menu.locator('[data-linked-pop-filter-value="0"]').select_option('진행')
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    expect(menu.locator('[data-linked-pop-filter-search]')).to_have_value('상태')
    menu.locator('[data-linked-pop-filter-remove="0"]').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    menu.locator('[data-linked-pop-filter-search]').fill('날짜')
    menu.locator('[data-linked-pop-filter-choice="due"]').click()
    expect(menu.locator('[data-linked-pop-filter-value="0"]')).to_have_attribute('type', 'date')
    menu.locator('[data-linked-pop-filter-value="0"]').fill('2026-09-16')
    menu.locator('[data-linked-pop-filter-value="0"]').press('Tab')
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    menu.locator('[data-linked-pop-filter-remove="0"]').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    menu.locator('[data-linked-pop-filter-advanced]').click()
    editor = page.locator('#modalBody [data-advanced-filter-editor]')
    expect(editor).to_be_visible()
    editor.locator('[data-advanced-add-rule=""]').click()
    editor.locator('[data-advanced-property="0"]').select_option('state')
    editor.locator('[data-advanced-operator="0"]').select_option('equals')
    editor.locator('[data-advanced-filter-value="0"]').select_option('진행')
    page.locator('#modalActions button').filter(has_text='적용').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    expect(preview.locator('[data-personal-linked-rule-chip="advanced"]')).to_contain_text('고급 필터')
    preview.locator('[data-personal-linked-view-settings]').click()
    preview.locator('[data-linked-pop-manage]').click()
    page.locator('#modalActions button').filter(has_text='적용').click()
    expect(preview.locator('[data-personal-linked-rule-chip="advanced"]')).to_be_visible()
    preview.locator('[data-personal-linked-rule-chip="advanced"]').click()
    expect(editor.locator('[data-advanced-filter-value="0"]')).to_have_value('진행')
    page.locator('#modalActions button').filter(has_text='필터 지우기').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    preview.locator('[data-personal-linked-view-settings]').click()
    menu.locator('[data-linked-pop-manage]').click()
    expect(page.locator('#modalActions button').filter(has_text='보기 삭제')).to_have_count(0)
    page.locator('#modalActions button').filter(has_text='취소').click()

    preview.locator('[data-personal-linked-add-view]').click()
    form = page.locator('#modalBody')
    form.locator('[data-personal-view-name]').fill('진행 보드')
    form.locator('[data-personal-view-type]').select_option('board')
    page.locator('#modalActions button').filter(has_text='보기 만들기').click()
    expect(preview.locator('[data-personal-linked-view]')).to_have_count(2)
    expect(preview.locator('.board-database')).to_be_visible()
    expect(preview.locator('.board-card')).to_have_count(2)
    preview.locator('[data-personal-linked-view-settings]').click()
    preview.locator('[data-linked-pop-group]').click()
    expect(menu.locator('[data-linked-pop-group-sort]')).to_be_visible()
    menu.locator('[data-linked-pop-group-sort]').select_option('count_desc')
    expect(menu.locator('[data-linked-pop-group-sort]')).to_have_value('count_desc')
    menu.locator('[data-linked-view-group-panel] [data-linked-pop-back]').click()
    preview.locator('[data-linked-pop-manage]').click()
    form.locator('[data-personal-view-name]').fill('진행 표')
    form.locator('[data-personal-view-type]').select_option('table')
    form.locator('[data-personal-view-visible="due"]').uncheck()
    for key in ('show_source_title', 'show_vertical_lines', 'show_page_icons', 'wrap_all_content'):
        form.locator(f'[data-personal-view-display="{key}"]').uncheck()
    page.locator('#modalActions button').filter(has_text='적용').click()
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')
    expect(preview.locator('.table-block')).to_be_visible()
    expect(preview.locator('.table-header > button, .table-header > span')).to_have_count(2)
    assert 'linked-hide-source-title' in preview.get_attribute('class')
    assert {'database-hide-source-title', 'database-hide-vertical-lines', 'database-hide-page-icons'} <= set(
        preview.locator('.table-block').get_attribute('class').split())
    expect(preview.locator('.linked-source-name')).to_be_hidden()
    expect(preview.locator('[data-personal-linked-source]')).to_be_visible()
    preview.locator('[data-personal-linked-view-settings]').click()
    menu = preview.locator('[data-linked-view-popover]')
    menu.locator('[data-linked-pop-layout]').click()
    menu.locator('[data-linked-pop-display="wrap_all_content"]').check()
    expect(preview.locator('.table-block.database-wrap-content')).to_be_visible()
    expect(menu.locator('[data-linked-view-layout-panel]')).to_be_visible()
    menu.locator('[data-linked-pop-display="wrap_all_content"]').uncheck()
    expect(preview.locator('.table-block.database-wrap-content')).to_have_count(0)
    menu.locator('[data-linked-view-layout-panel] [data-linked-pop-close]').click()
    _screenshot(page, tmp_path, 'personal-linked-views.png')

    preview.locator('[data-personal-linked-view-settings]').click()
    menu = preview.locator('[data-linked-view-popover]')
    menu.locator('[data-linked-pop-name]').fill('빠른 표')
    menu.locator('[data-linked-pop-name]').press('Tab')
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('빠른 표')
    expect(menu).to_be_visible()
    menu.locator('[data-linked-pop-name]').fill('진행 표')
    menu.locator('[data-linked-pop-name]').press('Tab')
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')
    menu.locator('[data-linked-pop-name]').fill('바깥 클릭 표')
    page.locator('#pageTitle').click()
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('바깥 클릭 표')
    expect(menu).to_be_hidden()
    preview.locator('[data-personal-linked-view-settings]').click()
    menu.locator('[data-linked-pop-name]').fill('진행 표')
    menu.locator('[data-linked-pop-name]').press('Tab')
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')
    menu.locator('[data-linked-pop-layout]').click()
    menu.locator('[data-linked-pop-type="board"]').click()
    expect(preview.locator('.board-database')).to_be_visible()
    expect(menu.locator('[data-linked-view-layout-panel]')).to_be_visible()
    menu.locator('[data-linked-pop-type="table"]').click()
    expect(preview.locator('.table-block')).to_be_visible()
    menu.locator('[data-linked-view-layout-panel] [data-linked-pop-close]').click()
    original_viewport = page.viewport_size
    page.set_viewport_size({'width':390, 'height':700})
    preview.locator('[data-personal-linked-view-settings]').click()
    menu = preview.locator('[data-linked-view-popover]')
    expect(menu).to_be_visible()
    menu.locator('[data-linked-pop-filter]').click()
    filter_bounds = menu.bounding_box()
    assert filter_bounds and filter_bounds['x'] >= 0 and filter_bounds['x'] + filter_bounds['width'] <= 390
    assert filter_bounds['y'] >= 0 and filter_bounds['y'] + filter_bounds['height'] <= 700
    menu.locator('[data-linked-view-filter-panel] [data-linked-pop-back]').click()
    menu.locator('[data-linked-pop-sort]').click()
    sort_bounds = menu.bounding_box()
    assert sort_bounds and sort_bounds['x'] >= 0 and sort_bounds['x'] + sort_bounds['width'] <= 390
    assert sort_bounds['y'] >= 0 and sort_bounds['y'] + sort_bounds['height'] <= 700
    menu.locator('[data-linked-view-sort-panel] [data-linked-pop-back]').click()
    bounds = menu.bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 700
    page.keyboard.press('Escape')
    expect(menu).to_be_hidden()
    page.set_viewport_size(original_viewport)

    preview.locator('[data-personal-linked-view-settings]').click()
    preview.locator('[data-linked-pop-manage]').click()
    page.evaluate('() => renderBlocks()')
    expect(page.locator('#modalActions button').filter(has_text='보기 복제')).to_be_visible()
    page.locator('#modalActions button').filter(has_text='보기 복제').click()
    expect(preview.locator('[data-personal-linked-view]')).to_have_count(3)
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_contain_text('사본')
    preview.locator('[data-personal-linked-view-settings]').click()
    preview.locator('[data-linked-pop-manage]').click()
    page.locator('#modalActions button').filter(has_text='보기 삭제').click()
    expect(preview.locator('[data-personal-linked-view]')).to_have_count(2)
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')

    preview.locator('[data-personal-linked-filters]').click()
    panel = preview.locator('[data-personal-linked-rules]')
    panel.locator('[data-rule-add]').click()
    panel.locator('[data-rule-property]').select_option('name')
    panel.locator('[data-rule-value]').fill('첫 항목')
    panel.locator('[data-rule-apply]').click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    expect(preview.locator('[data-personal-linked-rule-chip="filters"]')).to_contain_text('첫 항목')

    page.wait_for_function('() => !State.dirty')
    page.reload()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('[data-personal-linked-view]')).to_have_count(2)
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')
    expect(preview.locator('.table-header > button, .table-header > span')).to_have_count(2)
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    assert {'database-hide-source-title', 'database-hide-vertical-lines', 'database-hide-page-icons'} <= set(
        preview.locator('.table-block').get_attribute('class').split())
    assert page.evaluate("""() => {
      const linked = State.pageData.blocks.find(block => block.props?.linked_database);
      const active = linked.props.views.find(view => view.id === linked.props.active_view_id);
      return ['show_source_title','show_vertical_lines','show_page_icons','wrap_all_content']
        .every(key => active.config[key] === false) && (active.config.sorts || []).length === 0;
    }""") is True
    preview.locator('[data-personal-linked-view]').first.click()
    expect(preview.locator('.table-header > button, .table-header > span')).to_have_count(3)
    expect(preview.locator('.table-header > button, .table-header > span').nth(1)).to_contain_text('날짜')
    expect(preview.locator('.table-header > button, .table-header > span').nth(2)).to_contain_text('상태')
    assert page.evaluate("""() => {
      const linked = State.pageData.blocks.find(block => block.props?.linked_database);
      const active = linked.props.views.find(view => view.id === linked.props.active_view_id);
      return active.config.property_order;
    }""") == ['name', 'due', 'state']
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    expect(preview.locator('.database-group-label')).to_have_count(2)
    expect(preview.locator('[data-personal-linked-rule-chip="filters"]')).to_have_count(0)
    assert page.evaluate("""() => {
      const linked = State.pageData.blocks.find(block => block.props?.linked_database);
      const active = linked.props.views.find(view => view.id === linked.props.active_view_id);
      return active.config.sorts?.some(rule => rule.property === 'due' && rule.direction === 'desc');
    }""") is True
    assert 'database-hide-vertical-lines' not in preview.locator('.table-block').get_attribute('class')
    source_state = page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      const block = current.blocks.find(item => item.id === source.blockId);
      return {rows:block.props.rows.length,views:block.props.views || []};
    }""", source)
    assert source_state == {'rows':2,'views':[]}
    page.wait_for_function('() => !State.dirty')
    saved_active = page.evaluate("""() => {
      const id = document.querySelector('[data-linked-database]').dataset.linkedDatabase;
      return State.pageData.blocks.find(block => block.id === id).props.active_view_id;
    }""")
    page.evaluate('() => { State.pageData.page.can_edit = false; renderBlocks(); }')
    expect(preview.locator('[data-personal-linked-view-settings]')).to_have_count(0)
    preview.locator('[data-personal-linked-view]').last.click()
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    page.evaluate('() => renderBlocks()')
    expect(preview.locator('[data-personal-linked-view][aria-selected="true"]')).to_have_text('진행 표')
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    assert page.evaluate("""() => {
      const id = document.querySelector('[data-linked-database]').dataset.linkedDatabase;
      return State.pageData.blocks.find(block => block.id === id).props.active_view_id;
    }""") == saved_active
    assert page.evaluate('() => State.dirty') is False
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_file_cell_link_upload_reorder_remove(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'연결 파일 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'linked-files-source',type:'table',content:'첨부 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'attachments',name:'파일',type:'files'}],rows:[{name:'첨부 항목',
              attachments:[{url:'https://example.com/first.pdf',name:'First'}]}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='첨부 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    cell = preview.locator('.database-files-property').first
    expect(cell.get_by_role('link', name='First')).to_have_attribute('href', 'https://example.com/first.pdf')
    original_viewport = page.viewport_size
    page.set_viewport_size({'width':390,'height':700})
    menu_toggle = cell.get_by_role('button', name='파일 추가')
    menu = cell.get_by_role('dialog', name='파일 추가')
    menu_toggle.click()
    expect(menu).to_be_visible()
    expect(menu.get_by_role('button', name='업로드')).to_be_focused()
    expect(menu.get_by_role('button', name='링크 임베드')).to_be_visible()
    cell.locator('[data-db-file-link-toggle]').click()
    form = cell.locator('[data-db-file-link-form]')
    form.locator('[data-db-file-link-url]').fill('javascript:alert(1)')
    form.locator('[data-db-file-link-apply]').click()
    expect(form).to_be_visible()
    expect(form.locator('[data-db-file-link-error]')).to_have_text(
        'http 또는 https 파일 링크를 입력해 주세요.')
    form.locator('[data-db-file-link-url]').focus()
    page.evaluate('() => renderBlocks()')
    expect(form.locator('[data-db-file-link-url]')).to_have_value('javascript:alert(1)')
    expect(form.locator('[data-db-file-link-error]')).to_be_visible()
    expect(form.locator('[data-db-file-link-url]')).to_be_focused()
    expect(menu).to_be_visible()
    menu_bounds = menu.bounding_box()
    assert menu_bounds and menu_bounds['x'] >= 0 and menu_bounds['x'] + menu_bounds['width'] <= 390
    assert menu_bounds['y'] >= 0 and menu_bounds['y'] + menu_bounds['height'] <= 700
    form.locator('[data-db-file-link-url]').fill('https://example.com/second.pdf')
    expect(form.locator('[data-db-file-link-error]')).to_be_hidden()
    form.locator('[data-db-file-link-name]').fill('Second')
    form.locator('[data-db-file-link-apply]').click()
    expect(cell.get_by_role('link', name='Second')).to_have_attribute('href', 'https://example.com/second.pdf')
    expect(form).to_be_hidden()
    page.set_viewport_size(original_viewport)
    assert page.evaluate("""async source => {
      const data = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return data.blocks.find(block => block.id === source.blockId).props.rows[0].attachments;
    }""", source) == [
        {'url':'https://example.com/first.pdf','name':'First'},
        {'url':'https://example.com/second.pdf','name':'Second'},
    ]

    cell.locator('[data-db-file-drag][data-file-index="1"]').press('Alt+ArrowUp')
    expect(cell.locator('[data-db-file-target]').first.get_by_role('link')).to_have_text('Second')
    png = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
                        '0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082')
    menu_toggle.click()
    cell.locator('[data-db-file-input]').set_input_files({
        'name':'linked-tiny.png','mimeType':'image/png','buffer':png,
    })
    expect(cell.get_by_role('link', name='linked-tiny.png')).to_be_visible(timeout=30_000)
    cell.locator('[data-db-file-drag][data-file-index="2"]').drag_to(
        cell.locator('[data-db-file-target]').first)
    expect(cell.locator('[data-db-file-target]').first.get_by_role('link')).to_have_text('linked-tiny.png')
    uploaded_chip = cell.locator('[data-db-file-target]').filter(has_text='linked-tiny.png')
    uploaded_chip.get_by_role('button', name='linked-tiny.png 메뉴').click()
    uploaded_actions = uploaded_chip.get_by_role('menu', name='linked-tiny.png 파일 작업')
    expect(uploaded_actions.get_by_role('menuitem', name='다운로드')).to_have_attribute(
        'download', 'linked-tiny.png')
    with page.expect_download() as download:
        uploaded_actions.get_by_role('menuitem', name='다운로드').click()
    assert download.value.suggested_filename == 'linked-tiny.png'
    uploaded_chip.get_by_role('button', name='linked-tiny.png 메뉴').click()
    _screenshot(page, tmp_path, 'personal-linked-file-actions.png')
    uploaded_actions.get_by_role('menuitem', name='전체 화면').click()
    expect(page.locator('#modalBody .database-file-fullscreen img')).to_be_visible()
    page.locator('#modalClose').click()
    first_chip = cell.locator('[data-db-file-target]').filter(has_text='First')
    first_chip.get_by_role('button', name='First 메뉴').click()
    first_chip.get_by_role('menuitem', name='삭제').click()
    expect(cell.get_by_role('link', name='First')).to_have_count(0)
    expect(cell.get_by_role('link', name='linked-tiny.png')).to_be_visible()
    page.wait_for_function('() => !State.dirty')
    page.reload()
    preview = page.locator('[data-linked-database]').first
    cell = preview.locator('.database-files-property').first
    expect(cell.get_by_role('link', name='Second')).to_be_visible()
    expect(cell.get_by_role('link', name='linked-tiny.png')).to_be_visible()
    cell.get_by_role('button', name='파일 추가').click()
    expect(cell.get_by_role('dialog', name='파일 추가')).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-files.png')
    page.keyboard.press('Escape')
    expect(cell.get_by_role('dialog', name='파일 추가')).to_be_hidden()
    expect(cell.get_by_role('button', name='파일 추가')).to_be_focused()
    page.set_viewport_size({'width':390,'height':700})
    cell.get_by_role('button', name='파일 추가').click()
    bounds = cell.get_by_role('dialog', name='파일 추가').bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    page.keyboard.press('Escape')
    uploaded_chip = cell.locator('[data-db-file-target]').filter(has_text='linked-tiny.png')
    uploaded_chip.get_by_role('button', name='linked-tiny.png 메뉴').click()
    action_bounds = uploaded_chip.get_by_role('menu', name='linked-tiny.png 파일 작업').bounding_box()
    assert action_bounds and action_bounds['x'] >= 0 and action_bounds['x'] + action_bounds['width'] <= 390
    page.keyboard.press('Escape')
    expect(uploaded_chip.get_by_role('button', name='linked-tiny.png 메뉴')).to_be_focused()
    page.set_viewport_size({'width':1440,'height':900})
    source_state = page.evaluate("""async source => {
      const data = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      const row = data.blocks.find(block => block.id === source.blockId).props.rows[0];
      return {name:row.name,files:row.attachments,owned:data.files.map(file => file.id)};
    }""", source)
    assert source_state['name'] == '첨부 항목'
    assert source_state['files'][0]['name'] == 'linked-tiny.png'
    assert source_state['files'][0]['file_id'] in source_state['owned']
    assert source_state['files'][1] == {'url':'https://example.com/second.pdf','name':'Second'}
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_same_page_file_edit_survives_page_save(document_page):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.url.rsplit('/', 1)[-1]
    source = page.evaluate("""async pageId => {
      const saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'same-page-file-source',type:'table',content:'같은 페이지 첨부',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'attachments',name:'파일',type:'files'}],rows:[{name:'보존할 행',attachments:[]}]}}]},
      });
      return {blockId:saved.blocks[0].id};
    }""", page_id)
    page.reload()
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='같은 페이지 첨부').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    page.wait_for_function('() => !State.dirty && State.pageData.blocks.some(block => block.props?.linked_database?.document_id)')
    preview = page.locator('[data-linked-database]').first
    preview.get_by_role('button', name='파일 추가').click()
    preview.locator('[data-db-file-link-toggle]').click()
    form = preview.locator('[data-db-file-link-form]')
    form.locator('[data-db-file-link-url]').fill('https://example.com/same-page.pdf')
    form.locator('[data-db-file-link-name]').fill('Same page')
    form.locator('[data-db-file-link-apply]').click()
    expect(preview.get_by_role('link', name='Same page')).to_be_visible()
    assert page.evaluate("""blockId => State.pageData.blocks.find(block => block.id === blockId)
      .props.rows[0].attachments[0].name""", source['blockId']) == 'Same page'
    page.locator('#pageTitle').fill('같은 페이지 파일 보존')
    page.locator('#pageTitle').press('Tab')
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    saved = page.evaluate("""async ({pageId,blockId}) => {
      const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}`);
      const source = data.blocks.find(block => block.id === blockId);
      return {title:data.page.title,rows:source.props.rows,
        linked:data.blocks.filter(block => block.props?.linked_database).length};
    }""", {'pageId':page_id,'blockId':source['blockId']})
    assert saved['title'] == '같은 페이지 파일 보존'
    assert saved['rows'][0]['name'] == '보존할 행'
    assert saved['rows'][0]['attachments'] == [
        {'url':'https://example.com/same-page.pdf','name':'Same page'}]
    assert saved['linked'] == 1


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_relation_targets_and_revocation(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const targetPage = await api('/api/notion/pages', {method:'POST', body:{title:'관계 대상 목록'}});
      const targetId = targetPage.page.id;
      const first = await api('/api/notion/pages', {method:'POST', body:{title:'대상 알파',parent_id:targetId,icon:'🚀'}});
      const second = await api('/api/notion/pages', {method:'POST', body:{title:'대상 베타',parent_id:targetId}});
      const target = await api(`/api/notion/pages/${encodeURIComponent(targetId)}/blocks`, {
        method:'PUT',body:{blocks:[{id:'personal-relation-target',type:'table',content:'관계 대상 표',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'due',name:'날짜',type:'date'},
            {id:'note',name:'메모',type:'text'}],
            rows:[{name:'대상 알파',due:'2026-10-01',note:'첫 항목',page_id:first.page.id},
              {name:'대상 베타',due:'2026-10-02',note:'둘째 항목',page_id:second.page.id}]}}]},
      });
      const sourcePage = await api('/api/notion/pages', {method:'POST', body:{title:'관계 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(sourcePage.page.id)}/blocks`, {
        method:'PUT',body:{blocks:[{id:'personal-relation-source',type:'table',content:'연결 관계 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'related_many',name:'복수 관계',type:'relation',relation_multiple:true,
              relation_database_block_id:target.blocks[0].id},
            {id:'related_one',name:'단일 관계',type:'relation',relation_multiple:false,
              relation_database_block_id:target.blocks[0].id}],
            rows:[{name:'원본 항목',related_many:[],related_one:''}]}}]},
      });
      return {id:sourcePage.page.id,blockId:saved.blocks[0].id,
        targetId,targetBlockId:target.blocks[0].id,first:first.page.id,second:second.page.id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='연결 관계 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    many = preview.locator('select[data-db-field="related_many"]')
    one = preview.locator('select[data-db-field="related_one"]')
    many_trigger = preview.locator('[data-personal-relation-trigger="related_many"]')
    one_trigger = preview.locator('[data-personal-relation-trigger="related_one"]')
    expect(many_trigger).to_be_enabled(timeout=30_000)
    expect(one_trigger).to_be_enabled()
    expect(many).to_be_hidden()
    assert many.locator('option').evaluate_all('(items) => items.map(item => item.value)') == [
        '',source['first'],source['second']]
    expect(many.locator(f'option[value="{source["first"]}"]')).to_have_text('대상 알파')
    expect(one.locator(f'option[value="{source["second"]}"]')).to_have_text('대상 베타')
    many_trigger.click()
    picker = page.locator('.personal-linked-relation-popover')
    expect(picker).to_be_visible()
    expect(picker.locator('.personal-linked-relation-source')).to_have_text('관계 대상 표 내 검색')
    expect(picker.locator('.personal-linked-relation-section span')).to_have_text('0개 선택함')
    expect(picker.locator(f'[data-personal-relation-option="{source["first"]}"] '
                          'img.notion-emoji-image[src*="1f680.svg"]')).to_be_visible()
    bounds = picker.bounding_box()
    assert bounds and 550 <= bounds['width'] <= 570
    picker.get_by_role('button', name='표시할 속성을 선택하세요.').click()
    display_menu = picker.get_by_role('dialog', name='관계형에서 표시할 속성')
    expect(display_menu).to_contain_text('관계형에서 숨기기')
    expect(display_menu.get_by_role('button', name='이름 표시 순서 이동')).to_be_focused()
    display_menu.get_by_role('button', name='날짜 표시하기').click()
    expect(picker.locator(f'[data-personal-relation-option="{source["first"]}"]')) \
        .to_contain_text('날짜: 2026-10-01')
    _screenshot(page, tmp_path, 'personal-linked-relation-display.png')
    picker.get_by_role('searchbox', name='관계형 대상 검색').click()
    expect(display_menu).to_have_count(0)
    expect(picker).to_be_visible()
    picker.get_by_role('button', name='표시할 속성을 선택하세요.').click()
    expect(display_menu).to_be_visible()
    page.keyboard.press('Escape')
    expect(display_menu).to_have_count(0)
    expect(picker).to_be_visible()
    expect(picker.get_by_role('button', name='표시할 속성을 선택하세요.')).to_be_focused()
    search = picker.get_by_role('searchbox', name='관계형 대상 검색')
    search.click()
    expect(search).to_be_focused()
    search.fill('알파')
    expect(picker.locator('[data-personal-relation-option]')).to_have_count(1)
    search.press('ArrowDown')
    expect(picker.locator(f'[data-personal-relation-option="{source["first"]}"]')).to_be_focused()
    page.keyboard.press('Enter')
    expect(picker).to_have_count(0)
    expect(many_trigger).to_contain_text('대상 알파', timeout=30_000)
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    expect(many_trigger).to_be_enabled(timeout=30_000)
    many_trigger.click()
    search = picker.get_by_role('searchbox', name='관계형 대상 검색')
    expect(picker.locator('.personal-linked-relation-section span')).to_have_text('1개 선택함')
    expect(picker.locator(f'[data-personal-relation-option="{source["first"]}"]')).to_have_attribute('data-selected','true')
    search.fill('대상')
    search.dispatch_event('compositionstart', {'data':'없'})
    search.fill('없는')
    expect(picker.locator('[data-personal-relation-option]')).to_have_count(2)
    search.dispatch_event('compositionend', {'data':'없는'})
    expect(picker.locator('.personal-linked-relation-no-results')).to_be_visible()
    search.fill('대상')
    picker.locator(f'[data-personal-relation-option="{source["second"]}"]').click()
    expect(picker).to_have_count(0)
    for _ in range(100):
        state = page.evaluate("""async source => {
          const data = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
          return data.blocks.find(block => block.id === source.blockId).props.rows[0].related_many;
        }""", source)
        if state == [source['first'],source['second']]:
            break
        page.wait_for_timeout(100)
    assert state == [source['first'],source['second']]
    one_trigger.click()
    search = picker.get_by_role('searchbox', name='관계형 대상 검색')
    search.fill('베타')
    expect(picker.locator('[data-personal-relation-option]')).to_have_count(1)
    picker.locator(f'[data-personal-relation-option="{source["second"]}"]').click()
    for _ in range(100):
        state = page.evaluate("""async source => {
          const data = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
          return data.blocks.find(block => block.id === source.blockId).props.rows[0].related_one;
        }""", source)
        if state == source['second']:
            break
        page.wait_for_timeout(100)
    assert state == source['second']
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    page.reload()
    preview = page.locator('[data-linked-database]').first
    many = preview.locator('select[data-db-field="related_many"]')
    one = preview.locator('select[data-db-field="related_one"]')
    many_trigger = preview.locator('[data-personal-relation-trigger="related_many"]')
    one_trigger = preview.locator('[data-personal-relation-trigger="related_one"]')
    expect(many).to_have_values([source['first'],source['second']], timeout=30_000)
    expect(one).to_have_value(source['second'])
    expect(many_trigger).to_contain_text('대상 알파')
    expect(many_trigger).to_contain_text('대상 베타')
    many_trigger.click()
    expect(picker).to_be_visible()
    expect(picker.locator(f'[data-personal-relation-option="{source["first"]}"]')) \
        .to_contain_text('날짜: 2026-10-01')
    picker.get_by_role('button', name='표시할 속성을 선택하세요.').click()
    display_menu = picker.get_by_role('dialog', name='관계형에서 표시할 속성')
    display_menu.get_by_role('button', name='메모 표시하기').click()
    display_menu.get_by_role('button', name='메모 표시 순서 이동').press('Alt+ArrowUp')
    metadata = picker.locator(f'[data-personal-relation-option="{source["first"]}"] .personal-linked-relation-preview')
    expect(metadata).to_have_text(['메모: 첫 항목', '날짜: 2026-10-01'])
    display_menu.get_by_role('button', name='날짜 숨기기').click()
    expect(metadata).to_have_text(['메모: 첫 항목'])
    page.keyboard.press('Escape')
    expect(display_menu).to_have_count(0)
    page.evaluate('() => PersonalLinkedViews.refresh(true)')
    expect(picker).to_be_visible()
    _screenshot(page, tmp_path, 'personal-linked-relations.png')
    page.keyboard.press('Escape')
    expect(picker).to_have_count(0)
    expect(many_trigger).to_be_focused()
    page.set_viewport_size({'width':390,'height':700})
    many_trigger.click()
    expect(picker).to_be_visible()
    bounds = picker.bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':1440,'height':900})
    page.evaluate("""async source => api(`/api/notion/pages/${encodeURIComponent(source.second)}`,
      {method:'PATCH',body:{title:'대상 베타 수정'}})""", source)
    updated_target = page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.targetId)}`);
      return current.blocks.find(block => block.id === source.targetBlockId).props.rows[1].name;
    }""", source)
    assert updated_target == '대상 베타 수정'
    page.locator('#pageTitle').click()
    expect(one_trigger).to_contain_text('대상 베타 수정', timeout=30_000)
    many_trigger.click()
    expect(picker).to_be_visible()
    page.evaluate("""async source => api(`/api/notion/pages/${encodeURIComponent(source.id)}`,
      {method:'PATCH',body:{settings:{locked:true}}})""", source)
    expect(picker).to_have_count(0, timeout=15_000)
    expect(many_trigger).to_be_disabled()
    page.evaluate("""async source => api(`/api/notion/pages/${encodeURIComponent(source.id)}`,
      {method:'PATCH',body:{settings:{locked:false}}})""", source)
    preview.locator('[data-personal-linked-refresh]').click()
    expect(many_trigger).to_be_enabled(timeout=30_000)
    page.evaluate("""async source => api(`/api/notion/pages/${encodeURIComponent(source.targetId)}`,
      {method:'DELETE'})""", source)
    preview.locator('[data-personal-linked-refresh]').click()
    expect(many_trigger).to_be_disabled(timeout=30_000)
    expect(many_trigger).to_have_text('대상에 접근할 수 없습니다.')
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_relation_without_target_closes_on_source_loss(document_page):
    from playwright.sync_api import expect

    page = document_page
    source = page.evaluate("""async () => {
      const target = await api('/api/notion/pages', {method:'POST',body:{title:'자유 관계 대상'}});
      const created = await api('/api/notion/pages', {method:'POST',body:{title:'자유 관계 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT',body:{blocks:[{id:'free-relation-source',type:'table',content:'자유 관계 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'related',name:'관계',type:'relation',relation_multiple:false}],
            rows:[{name:'원본 항목',related:''}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id,targetId:target.page.id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='자유 관계 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    trigger = preview.locator('[data-personal-relation-trigger="related"]')
    expect(trigger).to_be_enabled(timeout=30_000)
    trigger.click()
    picker = page.locator('.personal-linked-relation-popover')
    expect(picker.locator('.personal-linked-relation-source')).to_have_text('워크스페이스 페이지 내 검색')
    picker.get_by_role('searchbox', name='관계형 대상 검색').fill('자유 관계 대상')
    expect(picker.locator('[data-personal-relation-option]')).to_have_count(1)
    picker.locator(f'[data-personal-relation-option="{source["targetId"]}"]').click()
    for _ in range(100):
        current = page.evaluate("""async source => {
          const data = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
          return data.blocks.find(block => block.id === source.blockId).props.rows[0].related;
        }""", source)
        if current == source['targetId']:
            break
        page.wait_for_timeout(100)
    assert current == source['targetId']
    expect(trigger).to_contain_text('자유 관계 대상')
    trigger.click()
    expect(picker).to_be_visible()
    page.evaluate("""async source => api(`/api/notion/pages/${encodeURIComponent(source.id)}`,
      {method:'DELETE'})""", source)
    expect(preview).to_contain_text('원본에 접근할 수 없습니다.', timeout=15_000)
    expect(picker).to_have_count(0)
    expect(trigger).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_title_updates_source_item_page(document_page):
    from playwright.sync_api import expect

    page = document_page
    source = page.evaluate("""async () => {
      const item = await api('/api/notion/pages', {method:'POST', body:{title:'이전 항목 제목'}});
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'항목 원본 표'}});
      await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'linked-title-source',type:'table',content:'항목 데이터',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],
            rows:[{name:'이전 항목 제목',page_id:item.page.id}]}}]},
      });
      return {id:created.page.id,itemId:item.page.id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='항목 데이터').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('[data-db-field="name"]')).to_have_value('이전 항목 제목')
    preview.locator('[data-db-field="name"]').fill('연결 보기 제목 수정')
    preview.locator('[data-db-field="name"]').press('Tab')
    page.wait_for_function("""async source => {
      const item = await api(`/api/notion/pages/${encodeURIComponent(source.itemId)}`);
      const original = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      return item.page.title === '연결 보기 제목 수정'
        && original.blocks.find(block => block.id === 'linked-title-source')?.props?.rows?.[0]?.name === '연결 보기 제목 수정';
    }""", arg=source)
    expect(preview.locator('.database-open-page')).to_be_enabled()
    preview.locator('.database-open-page').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.locator('#sidePeekTitle')).to_have_value('연결 보기 제목 수정')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_board_opens_existing_source_item(document_page):
    from playwright.sync_api import expect

    page = document_page
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'보드 원본'}});
      const item = await api('/api/notion/pages', {method:'POST', body:{title:'보드 항목'}});
      await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'board-source',type:'table',content:'업무 보드',props:{
          view:'table',properties:[{id:'name',name:'이름',type:'title'},
            {id:'status',name:'상태',type:'select',options:['진행 중']}],
          rows:[{name:'보드 항목',status:'진행 중',page_id:item.page.id}],
          views:[{id:'board-view',name:'상태 보드',type:'board',config:{}}],
        }}]},
      });
      return {id:created.page.id,itemId:item.page.id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='업무 보드').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='상태 보드').click()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('.board-column')).to_have_count(1)
    expect(preview.locator('.board-card')).to_contain_text('보드 항목')
    expect(preview.locator('.board-card')).to_be_enabled()
    preview.locator('.board-card').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.locator('#sidePeekTitle')).to_have_value('보드 항목')
    page.locator('#sidePeekCloseBtn').click()
    expect(preview.locator('[data-personal-linked-view]')).to_have_count(0)
    assert page.evaluate('id => State.pageData.blocks.some(block => block.props?.linked_database?.document_id === id && block.props?.views?.[0]?.name === "상태 보드")', source['id']) is True


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_search_survives_refresh_and_revocation(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'검색 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'search-source',type:'table',content:'검색 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],
            rows:[{name:'알파 작업'},{name:'베타 작업'}]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='검색 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    preview.locator('[data-personal-linked-search]').click()
    search = preview.locator('[data-personal-linked-search-input]')
    expect(search).to_be_focused()
    search.fill('알파')
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(1)
    expect(preview.locator('[data-db-field="name"]')).to_have_value('알파 작업')
    expect(search).to_be_focused()
    _screenshot(page, tmp_path, 'personal-linked-search.png')

    page.evaluate("""async source => {
      await api(`/api/notion/pages/${encodeURIComponent(source.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:source.blockId,type:'table',content:'검색 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],
            rows:[{name:'알파 새 작업'},{name:'베타 작업'}]}}]},
      });
    }""", source)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(search).to_have_value('알파')
    expect(preview.locator('[data-db-field="name"]')).to_have_value('알파 새 작업')
    expect(search).to_be_focused()
    preview.locator('[data-personal-linked-refresh]').click()
    expect(search).to_have_value('알파')
    search.fill('')
    expect(preview.locator('.table-row:not(.table-header)')).to_have_count(2)
    expect(search).to_be_focused()
    search.press('Escape')
    expect(search).to_be_hidden()
    expect(preview.locator('[data-personal-linked-search]')).to_be_focused()

    preview.locator('[data-personal-linked-search]').click()
    search.fill('알파')
    page.evaluate("async id => api(`/api/notion/pages/${encodeURIComponent(id)}`, {method:'DELETE'})", source['id'])
    preview.locator('[data-personal-linked-refresh]').click()
    expect(preview).to_contain_text('원본에 접근할 수 없습니다.')
    expect(preview.locator('[data-db-field="name"]')).to_have_count(0)
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_creates_one_source_item_after_response_loss(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST', body:{title:'항목 생성 원본'}});
      const saved = await api(`/api/notion/pages/${encodeURIComponent(created.page.id)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'create-source',type:'table',content:'업무 목록',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],rows:[]}}]},
      });
      return {id:created.page.id,blockId:saved.blocks[0].id};
    }""")
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='업무 목록').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    page.wait_for_function('() => !State.dirty && State.pageData.blocks.some(block => block.props?.linked_database?.document_id)')
    preview = page.locator('[data-linked-database]').first
    create = preview.locator('[data-personal-linked-create]')
    expect(create).to_be_enabled()
    _screenshot(page, tmp_path, 'personal-linked-create.png')
    lost = []

    def lose_first_response(route):
        if lost:
            lost.append('retried')
            route.continue_()
            return
        committed = route.fetch()
        lost.append(committed.status)
        route.abort('failed')

    pattern = f"**/api/notion/database-sources/{source['id']}/{source['blockId']}/items"
    page.route(pattern, lose_first_response)
    create.click()
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.locator('#sidePeekTitle')).to_have_value('새 페이지')
    expect(preview.locator('[data-db-field="name"]')).to_have_count(1)
    result = page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      const item = current.blocks.find(block => block.id === source.blockId).props.rows[0];
      const child = await api(`/api/notion/pages/${encodeURIComponent(item.page_id)}`);
      return {row:item,child:child.page,blocks:current.blocks.length};
    }""", source)
    assert result['row']['name'] == '새 페이지'
    assert result['child']['parent_id'] == source['id']
    assert result['blocks'] == 1
    assert lost == [200, 'retried']
    page.unroute(pattern, lose_first_response)
    page.locator('#sidePeekTitle').fill('연결 보기에서 만든 항목')
    page.locator('#sidePeekTitle').press('Tab')
    page.wait_for_function('() => !State.sidePeek.dirty && !State.sidePeek.saveInFlight', timeout=30_000)
    expect(page.locator('[data-linked-title-retry]')).to_have_count(0)
    persisted = page.evaluate("""async source => {
      const current = await api(`/api/notion/pages/${encodeURIComponent(source.id)}`);
      const item = current.blocks.find(block => block.id === source.blockId).props.rows[0];
      const child = await api(`/api/notion/pages/${encodeURIComponent(item.page_id)}`);
      return {row:item.name,child:child.page.title};
    }""", source)
    assert persisted == {'row': '연결 보기에서 만든 항목', 'child': '연결 보기에서 만든 항목'}
    page.locator('#sidePeekCloseBtn').click()
    expect(preview.locator('[data-db-field="name"]')).to_have_value('연결 보기에서 만든 항목')
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_personal_linked_create_after_same_page_draft_save(document_page):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.url.rsplit('/', 1)[-1]
    source = page.evaluate("""async pageId => {
      const saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/blocks`, {
        method:'PUT', body:{blocks:[{id:'same-page-source',type:'table',content:'같은 페이지 업무',
          props:{view:'table',properties:[{id:'name',name:'이름',type:'title'}],rows:[]}}]},
      });
      return {blockId:saved.blocks[0].id};
    }""", page_id)
    page.reload()
    page.wait_for_function('() => State.pageData?.page?.id === location.pathname.split("/").pop()')
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    page.locator('#blockMenu [data-insert-type="linked_database"]:visible').first.click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='같은 페이지 업무').click()
    page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기').click()
    page.wait_for_function('() => !State.dirty && State.pageData.blocks.some(block => block.props?.linked_database?.document_id)')
    preview = page.locator('[data-linked-database]').first
    expect(preview.locator('[data-personal-linked-create]')).to_be_enabled()
    page.locator('#pageTitle').fill('같은 페이지 변경 사항')
    page.wait_for_function('() => State.dirty')
    preview.locator('[data-personal-linked-create]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    page.locator('#sidePeekCloseBtn').click()
    expect(preview.locator('[data-db-field="name"]')).to_have_count(1)
    expect(preview.locator('[data-personal-linked-create]')).to_be_enabled()
    preview.locator('[data-personal-linked-create]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    page.locator('#sidePeekCloseBtn').click()
    expect(preview.locator('[data-db-field="name"]')).to_have_count(2)
    persisted = page.evaluate("""async ({pageId,blockId}) => {
      const current=await api(`/api/notion/pages/${encodeURIComponent(pageId)}`);
      return {title:current.page.title,
        rows:current.blocks.find(block=>block.id===blockId)?.props?.rows?.length};
    }""", {'pageId': page_id, 'blockId': source['blockId']})
    assert persisted == {'title': '같은 페이지 변경 사항', 'rows': 2}
