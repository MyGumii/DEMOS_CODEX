"""R10: canonical table layout settings survive the collaborative save boundary."""
from __future__ import annotations

from importlib import metadata
import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_collaborative_table_display_options_survive_reload(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        try:
            available = metadata.version("uvicorn") == "0.52.4" and metadata.version("websockets") == "17.0.1"
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, _authority, _workspace, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block?.props?._canonical_version != null && !State.dirty
                    && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive
                    && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length === 0;
                }""", arg=block_id, timeout=30_000)
                row = page.locator(f'[data-block-id="{block_id}"]')
                row.locator('[data-db-add-property]').click()
                page.locator('#dbPropertyName').fill('메모')
                page.locator('#dbPropertyType').select_option('text')
                page.locator('#modalActions button.primary').click()
                page.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block?.props?.properties?.some(property=>property.name==='메모')
                    && !State.dirty && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""", arg=block_id, timeout=30_000)
                property_id = page.evaluate("""id => State.pageData.blocks.find(item=>item.id===id)
                  .props.properties.find(property=>property.name==='메모').id""", block_id)
                row.locator('[data-db-add-property]').click()
                page.locator('#dbPropertyName').fill('우선순위')
                page.locator('#dbPropertyType').select_option('number')
                page.locator('#modalActions button.primary').click()
                page.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block?.props?.properties?.some(property=>property.name==='우선순위')
                    && !State.dirty && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""", arg=block_id, timeout=30_000)
                priority_id = page.evaluate("""id => State.pageData.blocks.find(item=>item.id===id)
                  .props.properties.find(property=>property.name==='우선순위').id""", block_id)
                expect(row.locator('[data-db-view-menu]')).to_be_visible(timeout=30_000)
                row.locator('[data-db-view-menu]').click()
                settings = row.locator('[data-db-view-popover]')
                settings.locator('[data-db-view-properties]').click()
                settings.locator(f'[data-view-property-id="{priority_id}"] .database-view-property-handle').drag_to(
                    settings.locator(f'[data-view-property-id="{property_id}"]'))
                header_order = row.locator('.table-header > button').evaluate_all(
                    'buttons => buttons.map(button => button.dataset.dbProperty)')
                assert header_order.index(priority_id) < header_order.index(property_id)
                settings.locator(f'[data-db-view-visible="{property_id}"]').click()
                expect(settings.locator(f'[data-db-view-visible="{property_id}"]')).to_have_attribute('aria-pressed','false')
                settings.locator('[data-db-view-back]').nth(1).click()
                settings.locator('[data-db-view-group]').click()
                settings.locator('[data-db-view-group-search]').fill('메모')
                settings.locator(f'[data-db-view-group-choice="{property_id}"]').click()
                expect(settings.locator(f'[data-db-view-group-choice="{property_id}"]')).to_have_attribute('aria-pressed','true')
                settings.locator('[data-db-view-group-sort]').select_option('desc')
                settings.locator('[data-db-view-group-panel] [data-db-view-back]').click()
                settings.locator('[data-db-view-sort]').click()
                settings.locator('[data-db-view-sort-search]').fill('메모')
                settings.locator(f'[data-db-view-sort-choice="{property_id}"]').click()
                settings.locator('[data-db-view-sort-direction="0"]').select_option('desc')
                expect(settings.locator('[data-db-view-sort-direction="0"]')).to_have_value('desc')
                settings.locator('[data-db-view-sort-panel] [data-db-view-back]').click()
                settings.locator('[data-db-view-filter]').click()
                settings.locator('[data-db-view-filter-search]').fill('메모')
                settings.locator(f'[data-db-view-filter-choice="{property_id}"]').click()
                settings.locator('[data-db-view-filter-value="0"]').fill('원본')
                settings.locator('[data-db-view-filter-value="0"]').press('Tab')
                expect(settings.locator('[data-db-view-filter-value="0"]')).to_have_value('원본')
                settings.locator('[data-db-view-filter-panel] [data-db-view-back]').click()
                settings.locator('[data-db-view-layout]').click()
                for key in ('show_source_title', 'show_vertical_lines', 'show_page_icons', 'wrap_all_content'):
                    settings.locator(f'[data-db-view-display="{key}"]').uncheck()
                expect(row.locator('.table-block.database-hide-vertical-lines.database-hide-page-icons')).to_be_visible()
                expect(row.locator('[data-db-private-publish]')).to_be_visible()
                row.locator('[data-db-private-publish]').click()
                page.wait_for_function("""({blockId,propertyId}) => {
                  const block = State.pageData.blocks.find(item => item.id === blockId);
                  const view = block?.props?.views?.find(item => item.id === block.props.active_view_id);
                  return view && !State.dirty && !State.liveCollaboration.unsyncedCount
                    && !State.liveCollaboration.sendQueueActive && !State.databaseSync.timer
                    && !State.databaseSync.inFlight && !State.databaseSync.queued
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length === 0
                    && view.property_visibility?.[propertyId] === false
                    && view.property_order?.indexOf(block.props.properties.find(p=>p.name==='우선순위')?.id)
                      < view.property_order?.indexOf(propertyId)
                    && view.group_property === propertyId && block.props.group_property === propertyId
                    && view.group_sort === 'desc' && block.props.group_sort === 'desc'
                    && view.sorts?.some(rule => rule.property === propertyId && rule.direction === 'desc')
                    && view.filters?.some(rule => rule.property === propertyId && rule.value === '원본')
                    && ['show_source_title','show_vertical_lines','show_page_icons','wrap_all_content']
                      .every(key => view[key] === false);
                }""", arg={"blockId":block_id,"propertyId":property_id}, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                row = page.locator(f'[data-block-id="{block_id}"]')
                expect(row.locator('.table-block.database-hide-vertical-lines.database-hide-page-icons')).to_be_visible(timeout=30_000)
                assert page.evaluate("""id => {
                  const block = State.pageData.blocks.find(item => item.id === id);
                  const view = block?.props?.views?.find(item => item.id === block.props.active_view_id);
                  return view && ['show_source_title','show_vertical_lines','show_page_icons','wrap_all_content']
                    .every(key => view[key] === false && block.props[key] === false)
                    && view.property_order?.indexOf(block.props.properties.find(p=>p.name==='우선순위')?.id)
                      < view.property_order?.indexOf(block.props.properties.find(p=>p.name==='메모')?.id);
                }""", block_id) is True
                title_id = page.evaluate("""id => State.pageData.blocks.find(item=>item.id===id)
                  .props.properties.find(property=>property.type==='title').id""", block_id)
                linked_id = page.evaluate("""({pageId,blockId}) => {
                  const source=State.pageData.blocks.find(item=>item.id===blockId);
                  const linked=NotionLinkedDatabases.descriptor('linked-display-instance',
                    {document_id:pageId,id:source.props.database_id},
                    {name:'연결 표',type:'table',config:{}});
                  State.pageData.blocks.push(linked);
                  markDirty();renderBlocks();
                  return linked.id;
                }""", {"pageId": page_id, "blockId": block_id})
                preview = page.locator(f'[data-linked-database="{linked_id}"]')
                expect(preview.locator('[data-linked-settings]')).to_be_visible(timeout=30_000)
                assert 'linked-hide-source-title' not in preview.get_attribute('class')
                assert 'database-hide-vertical-lines' not in preview.locator('.table-block').get_attribute('class')
                assert 'database-wrap-content' in preview.locator('.table-block').get_attribute('class')
                preview.locator('[data-linked-settings]').click()
                menu = preview.locator('[data-linked-view-popover]')
                expect(menu).to_be_visible()
                menu.locator('[data-linked-pop-properties]').click()
                expect(menu.locator(f'[data-linked-pop-visible="{property_id}"]')).to_have_attribute('aria-pressed','true')
                menu.locator(f'[data-linked-pop-visible="{property_id}"]').click()
                expect(menu.locator(f'[data-linked-pop-visible="{property_id}"]')).to_have_attribute('aria-pressed','false')
                menu.locator(f'[data-linked-pop-visible="{property_id}"]').click()
                expect(menu.locator(f'[data-linked-pop-visible="{property_id}"]')).to_have_attribute('aria-pressed','true')
                menu.locator('[data-linked-pop-back]').nth(1).click()
                menu.locator('[data-linked-pop-group]').click()
                expect(menu.locator('[data-linked-pop-group-choice=""]')).to_have_attribute('aria-pressed','true')
                menu.locator(f'[data-linked-pop-group-choice="{title_id}"]').click()
                expect(menu.locator(f'[data-linked-pop-group-choice="{title_id}"]')).to_have_attribute('aria-pressed','true')
                menu.locator('[data-linked-pop-group-sort]').select_option('count_desc')
                menu.locator('[data-linked-view-group-panel] [data-linked-pop-back]').click()
                menu.locator('[data-linked-pop-sort]').click()
                menu.locator(f'[data-linked-pop-sort-choice="{title_id}"]').click()
                expect(menu.locator('[data-linked-pop-sort-direction="0"]')).to_have_value('asc')
                menu.locator('[data-linked-view-sort-panel] [data-linked-pop-back]').click()
                menu.locator('[data-linked-pop-filter]').click()
                menu.locator(f'[data-linked-pop-filter-choice="{title_id}"]').click()
                menu.locator('[data-linked-pop-filter-value="0"]').fill('연결')
                menu.locator('[data-linked-pop-filter-value="0"]').press('Tab')
                expect(menu.locator('[data-linked-pop-filter-value="0"]')).to_have_value('연결')
                menu.locator('[data-linked-view-filter-panel] [data-linked-pop-back]').click()
                menu.locator('[data-linked-pop-layout]').click()
                expect(menu.locator('[data-linked-view-layout-panel]')).to_be_visible()
                for key in ('show_source_title', 'show_vertical_lines', 'show_page_icons', 'wrap_all_content'):
                    menu.locator(f'[data-linked-pop-display="{key}"]').uncheck()
                expect(preview.locator('.table-block.database-hide-vertical-lines.database-hide-page-icons')).to_be_visible()
                menu.locator('[data-linked-view-layout-panel] [data-linked-pop-back]').click()
                expect(menu.locator('[data-linked-pop-private-publish]')).to_be_visible()
                menu.locator('[data-linked-pop-private-publish]').click()
                page.wait_for_function("""({linkedId,blockId,propertyId,titleId}) => {
                  const linked=State.pageData.blocks.find(item=>item.id===linkedId);
                  const source=State.pageData.blocks.find(item=>item.id===blockId);
                  const view=linked?.props?.views?.find(item=>item.id===linked.props.active_view_id);
                  return view && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive
                    && view.config.property_visibility?.[propertyId] === true
                    && source?.props?.property_visibility?.[propertyId] === false
                    && view.config.group_property === titleId
                    && source?.props?.group_property === propertyId
                    && view.config.group_sort === 'count_desc'
                    && source?.props?.group_sort === 'desc'
                    && view.config.sorts?.some(rule => rule.property === titleId && rule.direction === 'asc')
                    && source?.props?.sorts?.some(rule => rule.property === propertyId && rule.direction === 'desc')
                    && view.config.filters?.some(rule => rule.property === titleId && rule.value === '연결')
                    && source?.props?.filters?.some(rule => rule.property === propertyId && rule.value === '원본')
                    && ['show_source_title','show_vertical_lines','show_page_icons','wrap_all_content']
                      .every(key=>view.config[key]===false);
                }""", arg={"linkedId":linked_id,"blockId":block_id,"propertyId":property_id,"titleId":title_id}, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                preview = page.locator(f'[data-linked-database="{linked_id}"]')
                expect(preview.locator('.table-block.database-hide-vertical-lines.database-hide-page-icons')).to_be_visible(timeout=30_000)
                assert 'linked-hide-source-title' in preview.get_attribute('class')
                assert page.evaluate("""({linkedId,blockId,propertyId,titleId}) => {
                  const linked=State.pageData.blocks.find(item=>item.id===linkedId);
                  const source=State.pageData.blocks.find(item=>item.id===blockId);
                  const view=linked?.props?.views?.find(item=>item.id===linked.props.active_view_id);
                  return view && view.config.group_property===titleId
                    && source.props.group_property===propertyId
                    && view.config.group_sort==='count_desc' && source.props.group_sort==='desc'
                    && view.config.sorts?.some(rule=>rule.property===titleId && rule.direction==='asc')
                    && source.props.sorts?.some(rule=>rule.property===propertyId && rule.direction==='desc')
                    && view.config.filters?.some(rule=>rule.property===titleId && rule.value==='연결')
                    && source.props.filters?.some(rule=>rule.property===propertyId && rule.value==='원본')
                    && ['show_source_title','show_vertical_lines','show_page_icons','wrap_all_content']
                    .every(key=>view.config[key]===false && source.props[key]===false);
                }""", {"linkedId": linked_id, "blockId": block_id,
                       "propertyId": property_id, "titleId": title_id}) is True
                preview.locator('[data-linked-settings]').click()
                menu = preview.locator('[data-linked-view-popover]')
                menu.locator('[data-linked-pop-filter]').click()
                menu.locator('[data-linked-pop-filter-advanced]').click()
                editor = page.locator('#modalBody [data-advanced-filter-editor]')
                expect(editor).to_be_visible()
                editor.locator('[data-advanced-operator="0"]').select_option('not_empty')
                page.locator('#modalActions button').filter(has_text='적용').click()
                preview.locator('[data-linked-settings]').click()
                menu.locator('[data-linked-pop-manage]').click()
                page.locator('#modalActions button').filter(has_text='적용').click()
                preview.locator('[data-linked-settings]').click()
                menu.locator('[data-linked-pop-private-publish]').click()
                page.wait_for_function("""linkedId => {
                  const linked = State.pageData.blocks.find(item => item.id === linkedId);
                  const view = linked?.props?.views?.find(item => item.id === linked.props.active_view_id);
                  return view?.config?.filter_group?.children?.[0]?.operator === 'not_empty'
                    && view.config.filters?.length === 0 && !State.dirty
                    && !State.liveCollaboration.unsyncedCount
                    && !State.liveCollaboration.sendQueueActive && !State.saveInFlight;
                }""", arg=linked_id, timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("""linkedId => State.pageData?.blocks?.some(
                  item => item.id === linkedId)""", arg=linked_id, timeout=30_000)
                page.wait_for_function("""linkedId => {
                  const linked = State.pageData.blocks.find(item => item.id === linkedId);
                  const view = linked?.props?.views?.find(item => item.id === linked.props.active_view_id);
                  return view?.config?.filter_group?.children?.[0]?.operator === 'not_empty'
                    && view.config.filters?.length === 0;
                }""", arg=linked_id, timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r10_filter_sort_stay_private_until_saved_for_everyone(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, _authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                errors = []

                def login(subject):
                    context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                    page.locator("#userId").fill(subject)
                    page.locator("#password").fill(password)
                    page.locator("#submitBtn").click()
                    expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                    page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                    if subject != "owner":
                        page.evaluate("id => localStorage.setItem(navigationSelectionKey(), id)", workspace_id)
                        page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                        expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                        page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                    return context, page

                owner_context, owner = login("owner")
                block_id = owner.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                owner.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block?.props?._canonical_version != null
                    && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""", arg=block_id, timeout=30_000)
                title_id = owner.evaluate("""id => State.pageData.blocks.find(item=>item.id===id)
                  .props.properties.find(item=>item.type==='title').id""", block_id)
                row = owner.locator(f'[data-block-id="{block_id}"]')
                row.locator('[data-db-view-menu]').click()
                settings = row.locator('[data-db-view-popover]')
                settings.locator('[data-db-view-filter]').click()
                settings.locator(f'[data-db-view-filter-choice="{title_id}"]').click()
                settings.locator('[data-db-view-filter-value="0"]').fill('owner-private')
                settings.locator('[data-db-view-filter-value="0"]').press('Tab')
                settings.locator('[data-db-view-filter-panel] [data-db-view-back]').click()
                settings.locator('[data-db-view-sort]').click()
                settings.locator(f'[data-db-view-sort-choice="{title_id}"]').click()
                expect(row.locator('[data-db-private-publish]')).to_be_visible()
                assert owner.evaluate("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block.props.filters.length===0 && block.props.sorts.length===0
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""", block_id)
                owner.set_viewport_size({"width": 390, "height": 700})
                expect(row.locator('[data-db-private-publish]')).to_be_visible()
                assert row.locator('[data-db-private-publish]').evaluate("""button => {
                  const rect=button.getBoundingClientRect(),style=getComputedStyle(button);
                  return rect.left>=0 && rect.right<=innerWidth && Number.parseFloat(style.fontSize)>=11;
                }""")
                owner.set_viewport_size({"width": 1280, "height": 800})

                editor_context, editor = login("schema-editor")
                editor_row = editor.locator(f'[data-block-id="{block_id}"]')
                expect(editor_row.locator('[data-db-view-menu]')).to_be_visible(timeout=30_000)
                expect(editor_row.locator('[data-db-private-publish]')).to_have_count(0)
                assert editor.evaluate("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  return block?.props?.filters?.length===0 && block?.props?.sorts?.length===0;
                }""", block_id)

                owner.reload(wait_until="domcontentloaded")
                row = owner.locator(f'[data-block-id="{block_id}"]')
                expect(row.locator('[data-db-private-publish]')).to_be_visible(timeout=30_000)
                row.locator('[data-db-private-publish]').click()
                owner.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  const view=block?.props?.views?.find(item=>item.id===block.props.active_view_id);
                  return view?.filters?.[0]?.value==='owner-private' && view?.sorts?.length===1
                    && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""", arg=block_id, timeout=30_000)
                editor.reload(wait_until="domcontentloaded")
                editor.wait_for_function("""id => {
                  const block=State.pageData?.blocks?.find(item=>item.id===id);
                  return block?.props?.filters?.[0]?.value==='owner-private'
                    && block?.props?.sorts?.length===1;
                }""", arg=block_id, timeout=30_000)
                row.locator('[data-db-view-menu]').click()
                settings = row.locator('[data-db-view-popover]')
                settings.locator('[data-db-view-filter]').click()
                settings.locator('[data-db-view-filter-value="0"]').fill('discard-this')
                settings.locator('[data-db-view-filter-value="0"]').press('Tab')
                expect(row.locator('[data-db-private-reset]')).to_be_visible()
                row.locator('[data-db-private-reset]').click()
                row.locator('[data-db-view-menu]').click()
                settings.locator('[data-db-view-filter]').click()
                expect(settings.locator('[data-db-view-filter-value="0"]')).to_have_value('owner-private')
                settings.locator('[data-db-view-filter-panel] [data-db-view-back]').click()
                settings.locator('[data-db-view-main] [data-db-view-close]').click()

                linked_id = owner.evaluate("""({pageId,blockId}) => {
                  const source=State.pageData.blocks.find(item=>item.id===blockId);
                  const linked=NotionLinkedDatabases.descriptor('private-scope-linked-instance',
                    {document_id:pageId,id:source.props.database_id},
                    {name:'연결 표',type:'table',config:{}});
                  State.pageData.blocks.push(linked);markDirty();renderBlocks();
                  return linked.id;
                }""", {"pageId": page_id, "blockId": block_id})
                linked = owner.locator(f'[data-linked-database="{linked_id}"]')
                expect(linked.locator('[data-linked-settings]')).to_be_visible(timeout=30_000)
                linked.locator('[data-linked-settings]').click()
                menu = linked.locator('[data-linked-view-popover]')
                menu.locator('[data-linked-pop-filter]').click()
                menu.locator(f'[data-linked-pop-filter-choice="{title_id}"]').click()
                menu.locator('[data-linked-pop-filter-value="0"]').fill('linked-private')
                menu.locator('[data-linked-pop-filter-value="0"]').press('Tab')
                menu.locator('[data-linked-view-filter-panel] [data-linked-pop-back]').click()
                menu.locator('[data-linked-pop-sort]').click()
                menu.locator(f'[data-linked-pop-sort-choice="{title_id}"]').click()
                expect(menu.locator('[data-linked-pop-sort-direction="0"]')).to_have_value('asc')
                assert owner.evaluate("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  const view=block?.props?.views?.find(item=>item.id===block.props.active_view_id);
                  return view?.config?.filters?.length!==1 && view?.config?.sorts?.length!==1;
                }""", linked_id)
                editor.reload(wait_until="domcontentloaded")
                editor_linked = editor.locator(f'[data-linked-database="{linked_id}"]')
                expect(editor_linked.locator('[data-linked-settings]')).to_be_visible(timeout=30_000)
                editor_linked.locator('[data-linked-settings]').click()
                editor_menu = editor_linked.locator('[data-linked-view-popover]')
                expect(editor_menu.locator('[data-linked-pop-private-publish]')).to_have_count(0)
                editor_menu.locator('[data-linked-pop-close]').first.click()
                menu.locator('[data-linked-view-sort-panel] [data-linked-pop-back]').click()
                menu.locator('[data-linked-pop-private-publish]').click()
                owner.wait_for_function("""id => {
                  const block=State.pageData.blocks.find(item=>item.id===id);
                  const view=block?.props?.views?.find(item=>item.id===block.props.active_view_id);
                  return view?.config?.filters?.[0]?.value==='linked-private'
                    && view?.config?.sorts?.length===1 && !State.liveCollaboration.unsyncedCount;
                }""", arg=linked_id, timeout=30_000)
                editor.reload(wait_until="domcontentloaded")
                editor.wait_for_function("""id => {
                  const block=State.pageData?.blocks?.find(item=>item.id===id);
                  const view=block?.props?.views?.find(item=>item.id===block.props.active_view_id);
                  return view?.config?.filters?.[0]?.value==='linked-private'
                    && view?.config?.sorts?.length===1;
                }""", arg=linked_id, timeout=30_000)
                assert not errors, errors
                editor_context.close()
                owner_context.close()
            finally:
                browser.close()
