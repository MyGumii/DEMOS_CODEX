"""The owned database date popup uses Notion-style choice menus."""

import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_owned_date_format_and_reminder_menus_save_and_fit_mobile(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True,
        database_activity=True, owned_database_trash=True,
    ) as (origin, password, authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                database_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=database_id, timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                root = page.locator(f'[data-block-id="{database_id}"]')
                expect(root.locator('[data-database-add]')).to_be_visible(timeout=30_000)
                root.locator('[data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.length === 1 && !State.dirty
                  && PendingDatabaseItemCreates.size === 0""", arg=database_id, timeout=30_000)
                database = authority.read_database('owner', database_id)
                date = next((item for item in database['properties'] if item['type'] == 'date'), None)
                if date is None:
                    document = authority.read_document('owner', page_id)
                    authority.execute_schema_command(
                        'owner', page_id, database_id=database_id,
                        command_kind='database.property.create', target_id='date-choice-property',
                        payload={'name': '날짜', 'type': 'date', 'config': {}},
                        expected_document_sequence=document['document_sequence'],
                        expected_workspace_epoch=document['workspace_epoch'],
                        expected_document_epoch=document['document_epoch'],
                        expected_target_version=0, operation_id='date-choice-property-create',
                    )
                    date = {'id': 'date-choice-property'}
                field = date['id']
                row_id = database['rows'][0]['id']
                page.reload(wait_until='domcontentloaded')
                root = page.locator(f'[data-block-id="{database_id}"]')
                button = root.locator(f'[data-db-date-picker="{field}"]').first
                expect(button).to_be_visible(timeout=30_000)
                button.click()
                picker = page.locator('.database-date-picker')
                picker.locator('[data-date-start]').fill('2026-09-16')
                picker.locator('[data-date-start]').press('Tab')
                try:
                    page.wait_for_function("""({db, row, field}) => {
                      const value=State.pageData.databases?.find(item => item.id === db)?.rows
                        ?.find(item => item.id === row)?.cells?.[field];
                      return (typeof value==='string'?value:value?.start)==='2026-09-16';
                    }""", arg={'db': database_id, 'row': row_id, 'field': field}, timeout=10_000)
                except Exception as error:
                    state = page.evaluate("""({db,row,field}) => ({
                      value:State.pageData.databases?.find(item=>item.id===db)?.rows
                        ?.find(item=>item.id===row)?.cells?.[field],
                      status:document.querySelector('#status')?.textContent,
                      picker:document.querySelector('.database-date-picker')?.textContent,
                    })""", {'db': database_id, 'row': row_id, 'field': field})
                    stored = authority.read_database('owner', database_id)['rows'][0]['cells'].get(field)
                    raise AssertionError(f'date did not save: browser={state}, server={stored}') from error
                expect(picker).to_be_visible()
                picker.locator('[data-date-format-toggle]').click()
                menu = page.locator('.database-date-choice-menu')
                expect(menu.locator('[data-date-choice]')).to_have_count(6)
                assert menu.locator('[data-date-choice] > span:first-child').all_text_contents() == [
                    '전체 날짜', '날짜 간단히 표기', '월/일/년', '일/월/년', '년/월/일', '상대',
                ]
                panel_bounds = picker.bounding_box()
                menu_bounds = menu.bounding_box()
                assert panel_bounds and menu_bounds
                assert menu_bounds['x'] + menu_bounds['width'] <= panel_bounds['x'] + 3
                if os.environ.get('NOTION_DATE_CHOICE_DESKTOP_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATE_CHOICE_DESKTOP_SCREENSHOT'])
                menu.locator('[data-date-choice="short"]').click()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.date_format === 'short'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                expect(picker).to_be_visible()
                picker.locator('[data-date-reminder-toggle]').click()
                expect(menu.locator('[data-date-choice]')).to_have_count(5)
                assert menu.locator('[data-date-choice] > span:first-child').all_text_contents() == [
                    '알림 없음', '당일 (오전 9:00)', '(오전 9:00) 1일 전',
                    '(오전 9:00) 2일 전', '(오전 9:00) 1주 전',
                ]
                menu.locator('[data-date-choice="day_1"]').click()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.reminder?.preset === 'day_1'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                picker.locator('[data-date-time-toggle]').check()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.time === '09:00'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                expect(picker.locator('.database-date-picker-field')).to_have_count(1)
                first_date = picker.locator('[data-date-start]').bounding_box()
                first_time = picker.locator('[data-date-time]').bounding_box()
                assert first_date and first_time and abs(first_date['y'] - first_time['y']) < 5
                picker.locator('[data-date-end-toggle]').click()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.end === '2026-09-16'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                expect(picker.locator('.database-date-picker-field')).to_have_count(2)
                start_after_end = picker.locator('[data-date-start]').bounding_box()
                second_date = picker.locator('[data-date-end]').bounding_box()
                second_time = picker.locator('[data-date-end-time]').bounding_box()
                assert start_after_end and second_date and second_time
                assert second_date['y'] > start_after_end['y'] and abs(second_date['y'] - second_time['y']) < 5
                picker.locator('[data-date-end-time]').fill('10:30')
                picker.locator('[data-date-end-time]').press('Tab')
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.end_time === '10:30'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                expect(picker.locator('[data-date-end-time]')).to_be_focused()
                picker.locator('[data-date-time-format-toggle]').click()
                expect(menu.locator('[data-date-choice]')).to_have_count(3)
                assert menu.locator('[data-date-choice] > span:first-child').all_text_contents() == [
                    '숨김', '12시간', '24시간',
                ]
                if os.environ.get('NOTION_DATE_TIME_MENU_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATE_TIME_MENU_SCREENSHOT'])
                menu.locator('[data-date-choice="hidden"]').click()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.time_format === 'hidden'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                assert '오전' not in button.inner_text()
                picker.locator('[data-date-reminder-toggle]').click()
                expect(menu.locator('[data-date-choice]')).to_have_count(10)
                assert menu.locator('[data-date-choice="minute_0"]').inner_text().startswith('이벤트 정각')
                menu.locator('[data-date-choice="minute_0"]').click()
                page.wait_for_function("""({db, row, field}) =>
                  State.pageData.databases?.find(item => item.id === db)?.rows
                    ?.find(item => item.id === row)?.cells?.[field]?.reminder?.preset === 'minute_0'""",
                    arg={'db': database_id, 'row': row_id, 'field': field}, timeout=30_000)
                page.set_viewport_size({'width': 390, 'height': 844})
                picker.locator('[data-date-format-toggle]').click()
                expect(menu).to_be_visible()
                bounds = menu.bounding_box()
                assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
                panel_bounds = picker.bounding_box()
                assert panel_bounds and panel_bounds['x'] >= 0
                assert panel_bounds['x'] + panel_bounds['width'] <= 390
                assert panel_bounds['y'] >= 0 and panel_bounds['y'] + panel_bounds['height'] <= 844
                if os.environ.get('NOTION_DATE_TIME_INLINE_MOBILE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATE_TIME_INLINE_MOBILE_SCREENSHOT'])
                if os.environ.get('NOTION_DATE_CHOICE_OWNED_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATE_CHOICE_OWNED_SCREENSHOT'])
                menu.locator('[data-date-choice="short"]').press('Escape')
                expect(menu).to_have_count(0)
                expect(picker.locator('[data-date-format-toggle]')).to_be_focused()
                page.reload(wait_until='domcontentloaded')
                stored = authority.read_database('owner', database_id)['rows'][0]['cells'][field]
                assert stored['date_format'] == 'short'
                assert stored['time'] == '09:00'
                assert stored['end'] == '2026-09-16'
                assert stored['end_time'] == '10:30'
                assert stored['time_format'] == 'hidden'
                assert stored['reminder']['preset'] == 'minute_0'
                assert not errors, errors
            finally:
                browser.close()
