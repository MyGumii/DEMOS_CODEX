"""R5: sidebar database side peek in an isolated collaborative workspace."""
from __future__ import annotations

from importlib import metadata
import os
import re
import zipfile

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_database_favorite_survives_reload_and_removes_independently(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        try:
            available = metadata.version("uvicorn") == "0.52.4" and metadata.version("websockets") == "17.0.1"
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (origin, password, _authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800}, accept_downloads=True)
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.enabled && State.navigation.collaborativeSharing && State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'[data-block-id="{block_id}"] [data-db-title]')).to_be_visible(timeout=30_000)
                normal_row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                try:
                    expect(normal_row).to_be_visible(timeout=30_000)
                except AssertionError as exc:
                    state = page.evaluate("""({pageId, blockId}) => {
                      const parent = State.pages.find(item => item.id === pageId);
                      const entry = parent && sidebarDatabaseEntry(parent);
                      return {collapsed:[...State.collapsedPageIds], active:State.activePageId,
                        page:parent, entryStatus:entry?.status, outline:parent && sidebarPageBlockOutline(parent),
                        block:State.pageData?.blocks?.find(item => item.id === blockId),
                        rows:[...document.querySelectorAll('.page-row')].map(item => ({id:item.dataset.pageId,
                          db:item.dataset.sidebarDbBlock, root:item.closest('.page-list')?.id})),
                        errors:document.querySelector('#status')?.textContent};
                    }""", {"pageId": page_id, "blockId": block_id})
                    raise AssertionError(f"database sidebar row missing: {state}; errors={errors}") from exc
                normal_row.hover()
                normal_row.locator('[data-sidebar-db-menu]').click()
                favorite_action = page.locator('#pageMenu [data-page-action="favorite"]')
                expect(favorite_action).to_contain_text('즐겨찾기에 추가')
                with page.expect_response(lambda response: '/navigation/database-favorites' in response.url) as reply:
                    favorite_action.click()
                response = reply.value
                request_body = response.request.post_data_json
                assert request_body['source_owner_subject_id'] == 'owner', request_body
                assert request_body['source_page_id'] == page_id, request_body
                assert request_body['database_block_id'] == block_id, request_body
                assert response.status == 200, {"request": response.request.post_data_json,
                                                "response": response.text()}
                favorite_row = page.locator(f'#favoritePages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
                expect(favorite_row).to_be_visible(timeout=30_000)
                page.wait_for_function("() => !State.navigation.mutationInFlight && !State.navigation.mutationReserved", timeout=30_000)
                saved = page.evaluate("""() => ({favorites:State.navigation.snapshot?.favorites,
                  favoriteVersion:State.navigation.snapshot?.favorite_version,
                  status:document.querySelector('#status')?.textContent,
                  hasDatabase:State.navigation.databaseFavoriteKeys.size,
                  hasParent:State.navigation.favoriteKeys.size})""")
                assert saved['hasDatabase'] == 1 and saved['hasParent'] == 0, saved
                normal_row.click()
                expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'))
                expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#pageEditTime')).to_be_hidden()
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
                page.locator('#pageCopyLinkBtn').click()
                copied = page.evaluate('navigator.clipboard.readText()')
                assert 'sidebar_db=' + block_id in copied, copied
                page.locator('#pageMoreBtn').click()
                expect(page.locator('#pageMenu')).to_have_attribute('data-surface', 'database')
                expect(page.locator('#pageMenu')).to_have_attribute('data-location', 'topbar')
                search = page.locator('#pageMenu .menu-search')
                expect(search).to_be_focused()
                labels = page.locator('#pageMenu .menu-item').evaluate_all(
                    "nodes => nodes.map(node => node.querySelectorAll('span')[1]?.textContent.trim())")
                assert labels == ['링크 복사', '복제', '옮기기', '휴지통으로 이동',
                                  '레이아웃 사용자 지정', '데이터베이스 잠금', '가져오기',
                                  'CSV와 병합', '내보내기', '업데이트와 애널리틱스',
                                  '버전 기록', '알림 받기', '연결', 'Windows 앱에서 열기',
                                  '데이터베이스 사용법'], labels
                expect(page.locator('#pageMenu .menu-item').filter(has_text='레이아웃 사용자 지정')).to_be_disabled()
                expect(page.locator('#pageMenu .menu-item').filter(has_text='데이터베이스 잠금')).to_be_disabled()
                search.fill('내보내기')
                expect(page.locator('#pageMenu [data-page-action="copy_link"]')).to_be_hidden()
                expect(page.locator('#pageMenu [data-page-action="export_csv"]')).to_be_visible()
                search.fill('')
                screenshot = os.environ.get('NOTION_ACTIVE_DB_TOPBAR_MENU_SCREENSHOT')
                if screenshot:
                    page.screenshot(path=screenshot)
                expect(page.locator('#pageMenu [data-page-action="copy_link"]')).to_be_visible()
                expect(page.locator('#pageMenu [data-page-action="favorite"]')).to_have_count(0)
                expect(page.locator('#pageMenu [data-page-action="rename"]')).to_have_count(0)
                page.locator('#pageMenu [data-page-action="export_csv"]').click()
                expect(page.locator('#modalBg')).to_be_visible()
                expect(page.locator('#modalTitle')).to_have_text('내보내기')
                expect(page.locator('#modalBody')).to_contain_text('기본 보기')
                expect(page.locator('#databaseExportScope')).to_have_value('current')
                expect(page.locator('#databaseExportContent')).to_have_value('all')
                assert page.locator('#databaseExportFormat option[value="pdf"]').get_attribute('disabled') is not None
                expect(page.locator('#modalBody .database-export-switch input:disabled')).to_have_count(3)
                expect(page.locator('#databaseExportCommentsRow')).to_be_hidden()
                page.locator('#modalActions button').first.click()
                expect(page.locator('#modalBg')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="export_csv"]').click()
                with page.expect_download() as csv_download:
                    page.locator('#modalActions button.primary').click()
                assert csv_download.value.suggested_filename.endswith('.zip')
                assert 'view_id=' in csv_download.value.url
                assert 'bundle=markdown_csv' in csv_download.value.url
                with zipfile.ZipFile(csv_download.value.path()) as archive:
                    names = archive.namelist()
                    assert len(names) == 2 and all(name.endswith('.csv') for name in names)
                    assert any(name.endswith('_all.csv') for name in names)
                    assert all('이름' in archive.read(name).decode('utf-8') for name in names)
                expect(page.locator('#modalBg')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="export_csv"]').click()
                page.locator('#databaseExportScope').select_option('all')
                with page.expect_download() as all_csv_download:
                    page.locator('#modalActions button.primary').click()
                assert 'view_id=' not in all_csv_download.value.url
                with zipfile.ZipFile(all_csv_download.value.path()) as archive:
                    names = archive.namelist()
                    assert len(names) == 2
                    assert archive.read(names[0]) == archive.read(names[1])
                expect(page.locator('#modalBg')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="export_csv"]').click()
                page.locator('#databaseExportFormat').select_option('html')
                expect(page.locator('#databaseExportCommentsRow')).to_be_visible()
                page.locator('#databaseExportContent').select_option('exclude_files_images')
                with page.expect_download() as html_download:
                    page.locator('#modalActions button.primary').click()
                assert html_download.value.suggested_filename.endswith('.zip')
                assert 'view_id=' in html_download.value.url
                assert 'bundle=1' in html_download.value.url
                assert 'page_content=exclude_files_images' in html_download.value.url
                with zipfile.ZipFile(html_download.value.path()) as archive:
                    html_name = next(name for name in archive.namelist() if name.endswith('.html'))
                    csv_name = next(name for name in archive.namelist() if name.endswith('.csv'))
                    exported_html = archive.read(html_name).decode('utf-8')
                    exported_csv = archive.read(csv_name).decode('utf-8')
                assert '<!doctype html>' in exported_html.lower()
                assert 'Collaborative browser lock' not in exported_html
                assert '이름' in exported_csv
                expect(page.locator('#modalBg')).to_be_hidden()
                page.set_viewport_size({"width": 390, "height": 700})
                page.locator('#pageMoreBtn').click()
                compact_menu = page.locator('#pageMenu')
                expect(compact_menu.locator('.menu-search')).to_be_focused()
                bounds = compact_menu.bounding_box()
                assert bounds and bounds['x'] >= 0 and bounds['y'] >= 0, bounds
                assert bounds['x'] + bounds['width'] <= 390 and bounds['y'] + bounds['height'] <= 700, bounds
                compact_menu.locator('.database-help-link').scroll_into_view_if_needed()
                expect(compact_menu.locator('.database-help-link')).to_be_visible()
                compact_screenshot = os.environ.get('NOTION_ACTIVE_DB_TOPBAR_MENU_COMPACT_SCREENSHOT')
                if compact_screenshot:
                    page.screenshot(path=compact_screenshot)
                page.keyboard.press('Escape')
                expect(compact_menu).to_be_hidden()
                expect(page.locator('#pageMoreBtn')).to_be_focused()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="export_csv"]').click()
                dialog_bounds = page.locator('#modalBg .modal').bounding_box()
                assert dialog_bounds and dialog_bounds['x'] >= 0 and dialog_bounds['y'] >= 0
                assert dialog_bounds['x'] + dialog_bounds['width'] <= 390
                assert dialog_bounds['y'] + dialog_bounds['height'] <= 700
                expect(page.locator('#databaseExportContent')).to_be_visible()
                if capture := os.environ.get('NOTION_ACTIVE_DB_EXPORT_COMPACT_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.locator('#modalActions button').first.click()
                expect(page.locator('#modalBg')).to_be_hidden()
                page.set_viewport_size({"width": 1280, "height": 800})
                page.locator('#favoritePageBtn').click()
                expect(favorite_row).to_have_count(0, timeout=30_000)
                expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'false')
                assert page.evaluate('State.navigation.favoriteKeys.size') == 0
                page.wait_for_function('() => !State.navigation.mutationInFlight && !State.navigation.mutationReserved', timeout=30_000)
                page.locator('#favoritePageBtn').click()
                expect(favorite_row).to_be_visible(timeout=30_000)
                expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'true')
                assert page.evaluate('State.navigation.favoriteKeys.size') == 0
                page.wait_for_function('() => !State.navigation.mutationInFlight && !State.navigation.mutationReserved', timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(favorite_row).to_be_visible(timeout=30_000)
                expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'true', timeout=30_000)
                assert page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').count() > 1
                favorite_row.hover()
                favorite_row.locator('[data-sidebar-db-menu]').click()
                expect(favorite_action).to_contain_text('즐겨찾기 해제')
                favorite_action.click()
                expect(favorite_row).to_have_count(0, timeout=30_000)
                expect(normal_row).to_be_visible(timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_sidebar_database_side_peek_saves_its_own_title(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        try:
            available = metadata.version("uvicorn") == "0.52.4" and metadata.version("websockets") == "17.0.1"
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_visible(timeout=30_000)
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady === true", timeout=30_000)

                created = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return {
                    id: State.pageData.blocks.find(block => block.type === 'table')?.id || '',
                    canEdit: activePageCanEdit(),
                    pageCanEdit: State.pageData.page.can_edit,
                    writeAuthorized: State.liveCollaboration.writeAuthorized,
                    blocks: State.pageData.blocks.map(block => block.type),
                    status: document.querySelector('#status')?.textContent || '',
                  };
                }""")
                assert created['id'], created
                block_id = created['id']
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'[data-block-id="{block_id}"] [data-db-title]')).to_be_visible(timeout=30_000)
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                original_page_title = page.locator("#pageTitle").input_value()

                row.click(modifiers=["Alt"])
                expect(page.locator("#sidePeek")).to_be_visible(timeout=30_000)
                frame = page.frame_locator("#sidePeekEditorSlot iframe")
                expect(frame.locator("#pageView")).to_have_class(re.compile(r'\bdatabase-full-page\b'), timeout=30_000)
                title = frame.locator("[data-db-title]")
                expect(title).to_be_enabled(timeout=30_000)
                title.fill("협업 옆 보기 DB")
                expect(page.locator("#sidePeekCrumb")).to_have_text("협업 옆 보기 DB", timeout=30_000)
                page.locator("#sidePeekCloseBtn").click()
                expect(page.locator("#sidePeek")).to_be_hidden(timeout=30_000)
                expect(page.locator("#pageTitle")).to_have_value(original_page_title)

                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"] .page-title-label').first).to_have_text(
                    "협업 옆 보기 DB", timeout=30_000)
                expect(page.locator("#pageTitle")).to_have_value(original_page_title)

                schema_context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                schema_page = schema_context.new_page()
                schema_errors: list[str] = []
                schema_page.on("pageerror", lambda error: schema_errors.append(str(error)))
                schema_page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                schema_page.locator("#userId").fill("schema-editor")
                schema_page.locator("#password").fill(password)
                schema_page.locator("#submitBtn").click()
                schema_page.wait_for_function("""() => typeof State !== 'undefined'
                  && State.user?.id === 'schema-editor' && State.navigation.enabled
                  && document.body.dataset.notionReady === 'true'""", timeout=30_000)
                schema_destination = schema_page.evaluate("""async ({workspaceId, pageId}) => {
                  const switched = await switchCanonicalWorkspace(workspaceId);
                  if (switched) await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                  return {switched, selected:State.navigation.workspaceId};
                }""", {"workspaceId": workspace_id, "pageId": page_id})
                assert schema_destination['switched'] and schema_destination['selected'] == workspace_id, schema_destination
                schema_row = schema_page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(schema_row).to_be_visible(timeout=30_000)
                schema_row.click(modifiers=["Alt"])
                expect(schema_page.locator("#sidePeek")).to_be_visible(timeout=30_000)
                schema_frame = schema_page.frame_locator("#sidePeekEditorSlot iframe")
                schema_title = schema_frame.locator("[data-db-title]")
                expect(schema_title).to_have_value("협업 옆 보기 DB", timeout=30_000)
                expect(schema_title).to_be_enabled()
                schema_context.route("**/databases/commands", lambda route: route.abort("failed"))
                schema_title.fill("저장 실패 중인 DB 제목")
                schema_child = schema_page.locator('#sidePeekEditorSlot iframe').element_handle().content_frame()
                schema_child.wait_for_function("""() => NotionEmbeddedEditor.state().dirty
                  && document.querySelector('#status')?.textContent?.includes('동기화 실패')""", timeout=30_000)
                schema_child.evaluate("""async () => {
                  scheduleLiveCanonicalRefresh();
                  await new Promise(resolve => setTimeout(resolve, 700));
                }""")
                expect(schema_title).to_have_value("저장 실패 중인 DB 제목")
                witness = authority.read_document("owner", page_id)
                authority.revoke_page_grant("owner", page_id, "subject", "schema-editor",
                    expected_workspace_epoch=witness["workspace_epoch"],
                    expected_document_epoch=witness["document_epoch"], operation_id="browser-db-side-peek-revoke-dirty")
                with pytest.raises(NotFoundOrDenied):
                    authority.read_document("schema-editor", page_id)
                expect(schema_frame.locator("#collaborationRecoveryBanner")).to_be_visible(timeout=20_000)
                expect(schema_title).to_have_value("저장 실패 중인 DB 제목")
                schema_page.locator("#sidePeekCloseBtn").click()
                expect(schema_page.locator("#sidePeek")).to_be_visible(timeout=30_000)
                expect(schema_title).to_have_value("저장 실패 중인 DB 제목")
                assert schema_child.evaluate("() => NotionEmbeddedEditor.state().dirty")
                assert not schema_errors, schema_errors
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"] .page-title-label').first).to_have_text(
                    "협업 옆 보기 DB", timeout=30_000)

                content_context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                content_page = content_context.new_page()
                content_errors: list[str] = []
                content_page.on("pageerror", lambda error: content_errors.append(str(error)))
                content_page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                content_page.locator("#userId").fill("content-editor")
                content_page.locator("#password").fill(password)
                content_page.locator("#submitBtn").click()
                content_page.wait_for_function("""() => typeof State !== 'undefined'
                  && State.user?.id === 'content-editor' && State.navigation.enabled
                  && document.body.dataset.notionReady === 'true'""", timeout=30_000)
                destination = content_page.evaluate("""async ({workspaceId, pageId}) => {
                  const workspaces = State.navigation.workspaces.map(item => item.id);
                  const switched = await switchCanonicalWorkspace(workspaceId);
                  if (switched) await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                  return {workspaces, switched, selected:State.navigation.workspaceId};
                }""", {"workspaceId": workspace_id, "pageId": page_id})
                assert destination['switched'] and destination['selected'] == workspace_id, destination
                expect(content_page.locator("#pageTitle")).to_be_visible(timeout=30_000)
                content_row = content_page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                try:
                    expect(content_row).to_be_visible(timeout=30_000)
                except AssertionError as exc:
                    state = content_page.evaluate("""({pageId, blockId}) => ({
                      activePageId:State.activePageId,
                      workspaceId:State.navigation.workspaceId,
                      sidebarMode:State.sidebarMode,
                      pages:State.pages.map(page => ({id:page.id, title:page.title, scope:page.scope, parent:page.parent_id})),
                      rows:[...document.querySelectorAll('.page-row')].map(row => ({id:row.dataset.pageId,
                        database:row.dataset.sidebarDbBlock, parent:row.dataset.sidebarDbParent})),
                      outline:sidebarPageBlockOutline(State.pages.find(page => page.id === pageId)),
                      block:State.pageData?.blocks?.find(block => block.id === blockId)?.type,
                    })""", {"pageId": page_id, "blockId": block_id})
                    raise AssertionError(f"granted database sidebar row missing: {state}; errors={content_errors}") from exc
                content_row.hover()
                content_row.locator('[data-sidebar-db-menu]').click()
                expect(content_page.locator('#pageMenu [data-page-action="rename"]')).to_have_count(0)
                content_page.keyboard.press('Escape')
                content_row.click(modifiers=["Alt"])
                expect(content_page.locator("#sidePeek")).to_be_visible(timeout=30_000)
                content_frame = content_page.frame_locator("#sidePeekEditorSlot iframe")
                expect(content_frame.locator("[data-db-title]")).to_have_value("협업 옆 보기 DB", timeout=30_000)
                expect(content_frame.locator("[data-db-title]")).to_be_disabled()
                add_item = content_frame.locator("[data-database-add]")
                expect(add_item).to_be_enabled()
                witness = authority.read_document("owner", page_id)
                authority.set_page_grant("owner", page_id, "subject", "content-editor", role="view",
                    inherit_to_descendants=False, expected_workspace_epoch=witness["workspace_epoch"],
                    expected_document_epoch=witness["document_epoch"], operation_id="browser-db-side-peek-view-only")
                try:
                    expect(add_item).to_be_disabled(timeout=20_000)
                except AssertionError as exc:
                    child = content_page.locator('#sidePeekEditorSlot iframe').element_handle().content_frame()
                    state = child.evaluate("""() => ({
                      canEdit:State.pageData?.page?.can_edit,
                      role:State.pageData?.page?.iam_role,
                      writeAuthorized:State.liveCollaboration.writeAuthorized,
                      recoveryOnly:State.liveCollaboration.recoveryOnly,
                      dirty:State.dirty,
                      buttonDisabled:document.querySelector('[data-database-add]')?.disabled,
                      status:document.querySelector('#status')?.textContent || '',
                    })""")
                    raise AssertionError(f"open pane kept write controls after role downgrade: {state}; errors={content_errors}") from exc
                witness = authority.read_document("owner", page_id)
                authority.revoke_page_grant("owner", page_id, "subject", "content-editor",
                    expected_workspace_epoch=witness["workspace_epoch"],
                    expected_document_epoch=witness["document_epoch"], operation_id="browser-db-side-peek-revoke-access")
                with pytest.raises(NotFoundOrDenied):
                    authority.read_document("content-editor", page_id)
                expect(content_frame.locator("#collaborationRecoveryBanner")).to_be_visible(timeout=20_000)
                content_page.locator("#sidePeekCloseBtn").click()
                expect(content_page.locator("#sidePeek")).to_be_hidden(timeout=30_000)
                assert not content_errors, content_errors
                assert not errors, errors
            finally:
                browser.close()
