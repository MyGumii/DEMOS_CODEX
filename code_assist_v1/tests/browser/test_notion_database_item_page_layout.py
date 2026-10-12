"""Saved database-wide item layout appears in both item-page modes."""

import re
import os
from copy import deepcopy

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_saved_item_page_layout_applies_to_side_and_full_pages(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True,
        database_activity=True, owned_database_trash=True,
        relation_pair=True, custom_emoji=True, item_page_layout=True,
    ) as (origin, password, authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'[data-block-id="{block_id}"] [data-database-add]')).to_be_visible(timeout=30_000)
                page.locator(f'[data-block-id="{block_id}"] [data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                  database.id === id)?.rows?.length === 1 && !State.dirty
                  && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive""",
                  arg=block_id, timeout=30_000)

                database = authority.read_database("owner", block_id)
                row_id = database["rows"][0]["id"]
                property_ids = [item["id"] for item in database["properties"] if item["type"] != "title"]
                assert len(property_ids) >= 2
                page.evaluate("id => openDatabaseFullPage(id)", block_id)
                page.locator("#pageMoreBtn").click()
                layout_action = page.locator('#pageMenu [data-page-action="item_layout"]')
                expect(layout_action).to_be_enabled()
                layout_action.click()
                editor = page.locator("#modalBg.database-item-layout-modal")
                expect(editor).to_be_visible()
                expect(editor.locator('[data-item-layout-sample]')).to_have_count(1)
                screenshot = os.environ.get('NOTION_ITEM_LAYOUT_EDITOR_SCREENSHOT')
                if screenshot:
                    editor.screenshot(path=screenshot)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(editor.locator('#modalActions button.primary')).to_be_visible()
                assert page.evaluate("""() => {
                  const body=document.querySelector('#modalBody');
                  return body.scrollWidth <= body.clientWidth + 1;
                }""")
                mobile_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_EDITOR_MOBILE_SCREENSHOT')
                if mobile_screenshot:
                    editor.screenshot(path=mobile_screenshot)
                for placement in ('page_properties', 'panel_properties'):
                    editor.locator(f'[data-item-layout-open-add="{placement}"]').click()
                    menu_box = editor.locator('.item-layout-placement-add-menu').bounding_box()
                    assert menu_box and menu_box['x'] >= 0 and menu_box['x'] + menu_box['width'] <= 390
                    editor.locator('[data-item-layout-add-search]').press('Escape')
                    expect(editor.locator('.item-layout-placement-add-menu')).to_have_count(0)
                page.set_viewport_size({"width": 1440, "height": 900})
                editor.locator('input[name="item-layout-structure"][value="tabs"]').check()
                editor.locator('[data-item-layout-open-add="panel_properties"]').click()
                expect(editor.locator('[data-item-layout-add-existing]')).to_have_count(len(property_ids))
                editor.locator(f'[data-item-layout-add-existing="{property_ids[0]}"]').click()
                editor.locator(f'[data-item-layout-place="{property_ids[1]}"]').select_option("hidden_properties")
                editor.locator('[data-item-layout-option="full_width"]').check()
                expect(editor.locator('[data-item-layout-option="show_templates"]')).to_be_enabled()
                editor.locator('[data-item-layout-option="show_templates"]').uncheck()
                editor.locator('[data-item-layout-module="content"][data-direction="-1"]').click()
                editor.locator("#modalActions button.primary").click()
                expect(editor).to_be_hidden(timeout=30_000)
                saved = authority.read_database("owner", block_id)["item_page_layout"]
                assert saved["version"] == 1
                assert saved["value"]["structure"] == "tabs"
                assert saved["value"]["modules"] == ["properties", "content", "comments"]
                assert saved["value"]["panel_properties"] == [property_ids[0]]
                assert saved["value"]["standalone_properties"] == [property_ids[0]]
                assert saved["value"]["hidden_properties"] == [property_ids[1]]
                assert saved["value"]["options"]["full_width"] is True
                assert saved["value"]["options"]["show_templates"] is False

                page.locator("#pageMoreBtn").click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                editor.locator('[data-item-layout-option="full_width"]').uncheck()
                editor.locator('[data-item-layout-open-add="page_properties"]').click()
                editor.locator('[data-item-layout-add-search]').fill('날짜')
                expect(editor.locator('[data-item-layout-add-existing]:visible')).to_have_count(1)
                editor.locator('[data-item-layout-add-search]').fill('')
                editor.locator(f'[data-item-layout-add-existing="{property_ids[0]}"]').click()
                expect(editor.locator(f'[data-item-layout-place="{property_ids[0]}"]')).to_have_value('page_properties')
                editor.locator('#modalActions button').filter(has_text='취소').click()
                expect(editor.locator('[data-item-layout-confirm="discard"]')).to_be_visible()
                editor.locator('[data-item-layout-confirm="discard"]').click()
                expect(editor).to_be_hidden()
                assert authority.read_database("owner", block_id)["item_page_layout"] == saved

                page.locator("#pageMoreBtn").click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                editor.locator('[data-item-layout-comments]').select_option('minimized')
                editor.locator("#modalActions button.primary").click()
                expect(editor).to_be_hidden(timeout=30_000)
                assert authority.read_database("owner", block_id)["item_page_layout"]["value"]["options"]["inline_comments"] == 'minimized'

                page.evaluate("""async ({blockId,rowId}) => {
                  const scope={...linkedDatabaseScope(),itemKind:'owned'};
                  const block=State.pageData.blocks.find(item=>item.id===blockId);
                  await linkedItemController().open(scope,blockId,block,ownedItemData(block),rowId,
                    document.querySelector(`[data-block-id="${blockId}"]`));
                }""", {"blockId": block_id, "rowId": row_id})
                expect(page.locator('#linkedItemPeek [data-db-empty-template]')).to_have_count(0)

                item = page.locator("#linkedItemPeek")
                expect(item).to_be_visible(timeout=30_000)
                expect(item.locator(f'.item-layout-panel [data-linked-item-field="{property_ids[0]}"]')).to_be_visible()
                expect(item.locator(f'[data-item-layout-standalone-property="{property_ids[0]}"]')).to_be_visible()
                assert item.locator(f'[data-linked-item-field="{property_ids[1]}"]').count() == 0
                expect(item.locator('[data-item-layout-tab="content"]')).to_have_text("콘텐츠")
                expect(item.locator('[data-item-layout-tab="content"]')).to_have_attribute("aria-selected", "true")
                expect(item.locator('[data-item-content]')).to_be_visible()
                expect(item.locator('[data-item-comments]')).to_be_hidden()
                item.locator('[data-item-comments-toggle]').click()
                expect(item.locator('[data-item-comments]')).to_be_visible()

                item.locator('[data-linked-item-full]').click()
                expect(item).to_have_class(re.compile(r"\bitem-full-page\b.*\bitem-layout-wide\b"))
                expect(item.locator(f'.item-layout-panel [data-linked-item-field="{property_ids[0]}"]')).to_be_visible()
                assert item.locator(f'[data-linked-item-field="{property_ids[1]}"]').count() == 0

                item.locator('[data-linked-item-close]').click()
                expect(item).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                editor.locator('[data-item-layout-property-menu]').click()
                expect(editor.locator('[data-item-layout-property-search]')).to_be_visible()
                editor.locator('[data-item-layout-property-search]').fill('상태')
                expect(editor.locator('.item-layout-menu-property:visible')).to_have_count(1)
                expect(editor.locator('[data-item-layout-property-search]')).to_be_focused()
                editor.locator('[data-item-layout-property-search]').fill('')
                menu_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_PROPERTY_MENU_SCREENSHOT')
                if menu_screenshot:
                    editor.screenshot(path=menu_screenshot)
                editor.locator(f'[data-item-layout-visibility-open="{property_ids[1]}"]').click()
                editor.locator(f'[data-item-layout-visibility="{property_ids[1]}"][data-mode="empty"]').click()
                editor.locator('[data-item-layout-group-move="panel"]').click()
                expect(editor.locator('.item-layout-preview-grid > aside '
                                      '.item-layout-preview-properties:not(.item-layout-preview-standalone)')).to_be_visible()
                panel_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_PANEL_SCREENSHOT')
                if panel_screenshot:
                    editor.screenshot(path=panel_screenshot)
                editor.locator('#modalActions button.primary').click()
                expect(editor).to_be_hidden(timeout=30_000)
                grouped = authority.read_database('owner', block_id)['item_page_layout']
                assert grouped['value']['property_group_placement'] == 'panel'
                assert grouped['value']['empty_hidden_properties'] == [property_ids[1]]
                assert set(grouped['value']['panel_properties']) == set(property_ids)
                visibility = page.evaluate("""({databaseId,rowId,propertyId}) => {
                  const database=State.pageData.databases.find(item=>item.id===databaseId);
                  const cells=database.rows.find(item=>item.id===rowId).cells;
                  const layout=item=>databaseItemPageLayout({data:{database},item,drafts:new Map()},database.properties);
                  return [layout({...cells,[propertyId]:null}).panel.some(property=>property.id===propertyId),
                    layout({...cells,[propertyId]:'2026-09-27'}).panel.some(property=>property.id===propertyId)];
                }""", {"databaseId": block_id, "rowId": row_id, "propertyId": property_ids[1]})
                assert visibility == [False, True]

                page.evaluate("""async ({blockId,rowId}) => {
                  const scope={...linkedDatabaseScope(),itemKind:'owned'};
                  const block=State.pageData.blocks.find(item=>item.id===blockId);
                  await linkedItemController().open(scope,blockId,block,ownedItemData(block),rowId,
                    document.querySelector(`[data-block-id="${blockId}"]`));
                }""", {"blockId": block_id, "rowId": row_id})
                expect(item.locator('.item-layout-panel-heading')).to_have_text('속성 그룹')
                expect(item.locator(f'.item-layout-panel [data-linked-item-field="{property_ids[0]}"]')).to_be_visible()
                assert item.locator(f'[data-linked-item-field="{property_ids[1]}"]').count() == 0
                item.locator('[data-linked-item-close]').click()
                expect(item).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                editor.locator('[data-item-layout-property-menu]').click()
                editor.locator('[data-item-layout-section-add]').click()
                editor.locator('[data-item-layout-section-name]').evaluate("""input => {
                  input.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Enter', isComposing: true, bubbles: true, cancelable: true
                  }));
                }""")
                expect(editor.locator('[data-item-layout-section-name]')).to_be_visible()
                editor.locator('[data-item-layout-section-name]').fill('기본 정보')
                editor.locator('[data-item-layout-section-done]').click()
                editor.locator('[data-item-layout-section-add]').click()
                extra_section_id = editor.locator('[data-item-layout-section-name]').get_attribute(
                    'data-item-layout-section-name')
                editor.locator('[data-item-layout-section-name]').fill('추가 정보')
                editor.locator('[data-item-layout-section-done]').click()
                editor.locator(f'[data-item-layout-section="{property_ids[0]}"]').select_option(extra_section_id)
                editor.locator('[data-item-layout-property-menu]').click()
                editor.locator(f'[data-item-layout-standalone-menu="{property_ids[0]}"]').click()
                editor.locator(f'[data-item-layout-standalone-place="{property_ids[0]}"]').click()
                expect(editor.locator('.item-layout-preview-grid > div:first-child '
                                      f'[data-item-layout-standalone-card="{property_ids[0]}"]')).to_be_visible()
                editor.locator(f'[data-item-layout-standalone-menu="{property_ids[0]}"]').click()
                editor.locator(f'[data-item-layout-standalone-place="{property_ids[0]}"]').click()
                expect(editor.locator('.item-layout-preview-grid > aside '
                                      f'[data-item-layout-standalone-card="{property_ids[0]}"]')).to_be_visible()
                editor.locator(f'[data-item-layout-standalone-menu="{property_ids[0]}"]').click()
                editor.locator(f'[data-item-layout-standalone-group="{property_ids[0]}"]').click()
                expect(editor.locator(f'[data-item-layout-standalone-card="{property_ids[0]}"]')).to_have_count(0)
                editor.locator('[data-item-layout-property-menu]').click()
                editor.locator('[data-item-layout-property-create]').click()
                picker_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_PROPERTY_PICKER_SCREENSHOT')
                if picker_screenshot:
                    editor.screenshot(path=picker_screenshot)
                editor.locator('[data-item-layout-property-type-search]').fill('체크')
                expect(editor.locator('[data-item-layout-create-type]:visible')).to_have_count(1)
                editor.locator('[data-item-layout-property-type-search]').fill('')
                editor.locator('[data-item-layout-property-name]').fill('레이아웃 메모')
                editor.locator('[data-item-layout-create-type="text"]').click()
                expect(editor.locator('[data-item-layout-place]')).to_have_count(len(property_ids) + 1)
                created_property_id = editor.locator('[data-item-layout-place]').last.get_attribute(
                    'data-item-layout-place')
                expect(editor.locator('.item-layout-preview-grid > aside .item-layout-preview-section')).to_have_count(2)
                editor.locator('[data-item-layout-property-menu]').click()
                expect(editor.locator('[data-item-layout-property-search]')).to_be_hidden()
                section_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_SECTION_SCREENSHOT')
                if section_screenshot:
                    editor.screenshot(path=section_screenshot)
                page.set_viewport_size({"width": 390, "height": 844})
                editor.locator('[data-item-layout-property-menu]').click()
                menu_box = editor.locator('.item-layout-property-menu').bounding_box()
                assert menu_box and menu_box['x'] >= 0 and menu_box['x'] + menu_box['width'] <= 390
                editor.locator('[data-item-layout-property-menu]').click()
                page.set_viewport_size({"width": 1440, "height": 900})
                editor.locator('#modalActions button.primary').click()
                expect(editor).to_be_hidden(timeout=30_000)
                sectioned = authority.read_database('owner', block_id)['item_page_layout']['value']['sections']
                assert [section['name'] for section in sectioned] == ['기본 정보', '추가 정보']
                assert sectioned[1]['property_ids'] == [property_ids[0], created_property_id]
                assert property_ids[0] not in authority.read_database('owner', block_id)[
                    'item_page_layout']['value']['standalone_properties']
                assert set(sectioned[0]['property_ids']) == set(property_ids[1:])
                assert any(property['id'] == created_property_id for property in
                           authority.read_database('owner', block_id)['properties'])
                page.evaluate("""async ({blockId,rowId}) => {
                  const scope={...linkedDatabaseScope(),itemKind:'owned'};
                  const block=State.pageData.blocks.find(item=>item.id===blockId);
                  await linkedItemController().open(scope,blockId,block,ownedItemData(block),rowId,
                    document.querySelector(`[data-block-id="${blockId}"]`));
                }""", {"blockId": block_id, "rowId": row_id})
                expect(item.locator('.item-layout-panel .item-layout-section h3')).to_have_text(
                    ['기본 정보', '추가 정보'])
                expect(item.locator('.item-layout-panel .item-layout-section').nth(1).locator(
                    f'[data-linked-item-field="{property_ids[0]}"]')).to_be_visible()
                expect(item.locator(f'[data-item-layout-standalone-property="{property_ids[0]}"]')).to_have_count(0)
                expect(item.locator('.item-layout-panel .item-layout-section').nth(1).locator(
                    f'[data-linked-item-field="{created_property_id}"]')).to_be_visible()
                item.locator('[data-linked-item-full]').click()
                expect(item.locator('.item-layout-panel .item-layout-section h3')).to_have_text(
                    ['기본 정보', '추가 정보'])
                item.locator('[data-linked-item-close]').click()
                expect(item).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                editor.locator('[data-item-layout-open-add="panel_properties"]').click()
                editor.locator('[data-item-layout-add-search]').fill('숫자')
                expect(editor.locator('[data-item-layout-add-type]:visible')).to_have_count(1)
                editor.locator('[data-item-layout-add-search]').fill('')
                editor.locator('[data-item-layout-add-type="number"]').click()
                expect(editor.locator('[data-item-layout-details-name]')).to_have_value('숫자')
                editor.locator('[data-item-layout-details-name]').evaluate("""input => {
                  input.dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'Escape', isComposing: true, bubbles: true, cancelable: true
                  }));
                }""")
                expect(editor.locator('[data-item-layout-details-name]')).to_be_visible()
                details_screenshot = os.environ.get('NOTION_ITEM_LAYOUT_DETAILS_SCREENSHOT')
                if details_screenshot:
                    editor.screenshot(path=details_screenshot)
                editor.locator('[data-item-layout-details-name]').fill('버릴 속성')
                editor.locator('[data-item-layout-details-back]').click()
                expect(editor.locator('.item-layout-preview-grid > aside .item-layout-preview-property').filter(
                    has_text='버릴 속성')).to_be_visible()
                editor.locator('.item-layout-preview-grid > aside [data-item-layout-section-menu]').nth(1).click()
                editor.locator('[data-item-layout-section-delete]').click()
                expect(editor.locator('.item-layout-preview-grid > aside .item-layout-preview-section')).to_have_count(1)
                expect(editor.locator('.item-layout-preview-grid > aside '
                                      '.item-layout-preview-properties:not(.item-layout-preview-standalone) '
                                      '> .item-layout-preview-property')).to_have_count(2)
                editor.locator('#modalActions button').filter(has_text='취소').click()
                editor.locator('[data-item-layout-confirm="discard"]').click()
                expect(editor).to_be_hidden()
                assert authority.read_database('owner', block_id)['item_page_layout']['value']['sections'] == sectioned
                assert not any(property['name'] == '버릴 속성' for property in
                               authority.read_database('owner', block_id)['properties'])
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                expect(editor).to_be_visible()
                remote_before = authority.read_database('owner', block_id)['item_page_layout']
                remote_layout = deepcopy(remote_before['value'])
                remote_layout['options']['show_page'] = False
                witness = authority.read_document('owner', page_id)
                authority.execute_surface_command(
                    'owner', page_id, database_id=block_id,
                    command_kind='database.item_layout.set', target_id=block_id,
                    payload={'layout': remote_layout},
                    expected_target_version=remote_before['version'],
                    expected_document_sequence=witness['document_sequence'],
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'],
                    operation_id='browser-item-layout-remote-update',
                )
                editor.locator('[data-item-layout-option="full_width"]').uncheck()
                editor.locator('#modalActions button.primary').click()
                expect(editor.locator('.item-layout-error')).to_contain_text('다른 곳에서 레이아웃이 변경', timeout=30_000)
                assert authority.read_database('owner', block_id)['item_page_layout']['value'] == remote_layout
                editor.locator('#modalActions button').filter(has_text='취소').click()
                editor.locator('[data-item-layout-confirm="discard"]').click()
                expect(editor).to_be_hidden()

                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="item_layout"]').click()
                editor.locator('[data-item-layout-open-add="page_properties"]').click()
                editor.locator('[data-item-layout-add-type="text"]').click()
                expect(editor.locator('[data-item-layout-details-name]')).to_have_value('텍스트')
                editor.locator('[data-item-layout-details-name]').fill('페이지 초안 속성')
                editor.locator('#modalActions button.primary').click()
                expect(editor).to_be_hidden(timeout=30_000)
                committed = authority.read_database('owner', block_id)
                committed_property = next(property for property in committed['properties']
                                          if property['name'] == '페이지 초안 속성')
                assert committed_property['id'] in committed['item_page_layout']['value']['page_properties']
                assert committed_property['id'] in committed['item_page_layout']['value']['standalone_properties']
                assert all(committed_property['id'] not in section['property_ids']
                           for section in committed['item_page_layout']['value']['sections'])

                content_editor = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                content_editor.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                content_editor.locator("#userId").fill("content-editor")
                content_editor.locator("#password").fill(password)
                content_editor.locator("#submitBtn").click()
                content_editor.wait_for_function("""() => typeof State !== 'undefined'
                  && State.user?.id === 'content-editor' && State.navigation.enabled""", timeout=30_000)
                content_editor.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                content_editor.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                content_editor.wait_for_function("id => typeof State !== 'undefined' && State.activePageId === id",
                                                 arg=page_id, timeout=30_000)
                content_editor.evaluate("id => openDatabaseFullPage(id)", block_id)
                content_editor.locator("#pageMoreBtn").click()
                expect(content_editor.locator('#pageMenu .menu-item').filter(
                    has_text='레이아웃 사용자 지정')).to_be_disabled()
                assert not errors, errors
            finally:
                browser.close()
