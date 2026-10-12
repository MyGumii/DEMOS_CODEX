"""UI-SYNC-TOOLBAR: current read, keyboard, viewport and detached-popup lifecycle."""
import json
import os
from pathlib import Path
import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled
from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('actor,theme,width',[('owner','light',1280),('owner','dark',390),('content-editor','light',1280),('content-editor','dark',390)])
def test_reference_toolbar_keyboard_viewport_current_read_and_dispose(tmp_path,actor,theme,width):
    from playwright.sync_api import expect,sync_playwright
    output=Path(os.environ.get('NOTION_SYNC_TOOLBAR_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,document):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,document,'owner',password)
                _seed_blocks(owner,[original('toolbar-original','원본 A'),reference('toolbar-peer',document,'toolbar-original'),
                    {'id':'toolbar-sibling','type':'paragraph','content':'다른 본문 그대로','props':{}}]);settled(owner)
                if actor!='owner':
                    witness=authority.read_document('owner',document)
                    authority.set_page_grant('owner',document,'subject',actor,role='view',inherit_to_descendants=False,
                        expected_workspace_epoch=witness['workspace_epoch'],expected_document_epoch=witness['document_epoch'],operation_id='toolbar-viewer')
                page=owner if actor=='owner' else browser.new_page(locale='ko-KR')
                if actor!='owner':login(page,origin,document,actor,password)
                page.set_viewport_size({'width':width,'height':844});page.evaluate('theme=>applyColorTheme(theme)',theme);settled(page)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                peer=page.locator('[data-synced-reference="toolbar-peer"]');trigger=peer.locator('[data-synced-locations]');menu=peer.locator('[data-synced-locations-panel]')
                expect(trigger).to_be_enabled(timeout=30_000);expect(peer.locator('.synced-reference-content')).to_contain_text('원본 A')
                baseline=authority.read_document('owner',document)['document_sequence']
                trigger.press('ArrowDown');expect(menu).to_be_visible();expect(peer.locator('[data-synced-open]')).to_be_focused()
                expect(trigger).to_have_attribute('aria-expanded','true')
                rect=menu.bounding_box();assert rect and rect['x']>=7 and rect['y']>=7 and rect['x']+rect['width']<=width-7 and rect['y']+rect['height']<=844-7,rect
                page.screenshot(path=str(output/f'toolbar-original-{actor}-{theme}-{width}.png'))
                peer.locator('[data-synced-open]').press('Escape');expect(menu).to_be_hidden();expect(trigger).to_be_focused()
                peer.hover();trigger.click();expect(menu).to_be_visible();page.locator('[data-synced-source="toolbar-original"]').click();expect(menu).to_be_hidden()
                trigger.press('Enter');expect(menu).to_be_visible();peer.locator('.synced-reference-content').click(position={'x':2,'y':2});expect(menu).to_be_hidden()
                trigger.press('Enter');expect(menu).to_be_visible();page.evaluate('renderBlocks()')
                expect(page.locator('.synced-action-popover:popover-open')).to_have_count(0)
                expect(trigger).to_be_enabled(timeout=30_000);expect(trigger).to_be_focused()
                more=peer.locator('[data-synced-actions-menu]');more.press('Enter')
                if actor=='owner':
                    search=page.locator('#blockMenu').get_by_role('combobox',name='블록 작업 검색')
                    expect(search).to_be_focused();expect(page.locator('#blockMenu [data-action="sync_detach"]')).to_be_visible()
                    page.screenshot(path=str(output/f'toolbar-actions-{actor}-{theme}-{width}.png'))
                    search.press('Escape');expect(more).to_be_focused()
                    text=peer.locator('[data-synced-text="toolbar-original"]');text.click();text.press('Escape');expect(trigger).to_be_focused()
                else:
                    actions=peer.locator('[data-synced-actions-panel]');expect(actions).to_be_visible()
                    expect(peer.locator('[data-synced-refresh]')).to_be_focused()
                    expect(peer.locator('[data-synced-detach]')).to_have_count(0)
                    page.screenshot(path=str(output/f'toolbar-actions-{actor}-{theme}-{width}.png'))
                    with page.expect_response(lambda r:r.request.method=='GET' and r.url.endswith('/synced-blocks/toolbar-peer')):
                        peer.locator('[data-synced-refresh]').press('Enter')
                    expect(actions).to_be_hidden();expect(trigger).to_be_enabled(timeout=30_000)
                    expect(peer.locator('[contenteditable="true"]')).to_have_count(0)
                assert authority.read_document('owner',document)['document_sequence']==baseline
                assert not errors,errors
                (output/f'toolbar-{actor}-{theme}-{width}.json').write_text(json.dumps({'document_sequence':baseline,'popup_bounds':rect,'page_errors':errors,'readonly':actor!='owner'},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                if 'page' in locals():page.screenshot(path=str(output/f'toolbar-failure-{actor}-{theme}-{width}.png'))
                raise
            finally:browser.close()
