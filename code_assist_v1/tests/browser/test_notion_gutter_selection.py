"""R1: visible, borderless block marquee from the page gutters."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload


@pytest.mark.integration
@pytest.mark.regression
def test_ui_gutter_drag_does_not_select_rows_outside_visible_marquee(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const surface=document.createElement('div');
      surface.id='test-selection-surface';
      Object.assign(surface.style,{position:'fixed',left:'80px',top:'120px',width:'500px',
        height:'320px',overflow:'hidden',zIndex:'99999',background:'white'});
      const outside=document.createElement('div');
      outside.id='test-outside-row';
      Object.assign(outside.style,{position:'absolute',left:'200px',top:'-60px',
        width:'100px',height:'20px'});
      const inside=document.createElement('div');
      inside.id='test-inside-row';
      Object.assign(inside.style,{position:'absolute',left:'200px',top:'100px',
        width:'100px',height:'40px'});
      surface.append(outside,inside);document.body.append(surface);
      window.__testGutterIds=[];
      window.__testGutterBinding=window.NotionUiInteraction.bindGutterSelection({
        pageKey:()=> 'visible-marquee-test',
        start:event=>event.target.closest?.('#test-selection-surface')
          ? {canvas:surface,surface,scroller:surface}:null,
        rows:()=>[outside,inside],rowId:row=>row.id,rowContent:row=>row,
        selected:()=>[],select:ids=>{window.__testGutterIds=ids;},
        toolbarBottom:()=>0,
      });
    }""")
    page.mouse.move(140, 250)
    page.mouse.down()
    page.mouse.move(330, 50, steps=8)
    expect(page.locator('.block-selection-marquee')).to_be_visible()
    page.wait_for_function("() => window.__testGutterIds.includes('test-inside-row')")
    assert page.evaluate("""() => {
      const box=document.querySelector('.block-selection-marquee').getBoundingClientRect();
      const outside=document.querySelector('#test-outside-row').getBoundingClientRect();
      return box.top === 120 && outside.bottom < box.top
        && JSON.stringify(window.__testGutterIds) === '["test-inside-row"]';
    }""")
    page.mouse.up()
    page.evaluate("() => {window.__testGutterBinding.dispose();document.querySelector('#test-selection-surface').remove();}")
    expect(page.locator('.block-selection-marquee')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("viewport_width", [1440, 1280, 900])
def test_ui_gutter_drag_selects_body_heading_and_table_without_page_title(document_page, viewport_width, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": viewport_width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.evaluate("""() => {
      State.pageData.blocks = [
        EditorCore.createBlock('heading1', {content:'본문 제목'}),
        EditorCore.createBlock('paragraph', {content:''}),
        EditorCore.createBlock('paragraph', {content:'본문 문단'}),
        EditorCore.createBlock('table', {content:'선택할 표'}),
      ];
      renderBlocks();
    }""")
    rows = page.locator('#blocks .block-row')
    expect(rows).to_have_count(4)
    geometry = page.evaluate("""() => {
      const body = document.querySelector('#blocks').getBoundingClientRect();
      const rows = [...document.querySelectorAll('#blocks .block-row')]
        .map(row => ({id:row.dataset.blockId, ...(() => {
          const rect = (row.querySelector('.block-main') || row).getBoundingClientRect();
          return {top:rect.top,bottom:rect.bottom};
        })()}));
      const surface = document.querySelector('#workspaceMain').getBoundingClientRect();
      return {body:{left:body.left,right:body.right}, surface:{left:surface.left,right:surface.right}, rows};
    }""")
    assert len(geometry['rows']) == 4
    start_x = max(geometry['surface']['left'] + 16, geometry['body']['left'] - 140)
    end_x = min(geometry['surface']['right'] - 16, geometry['body']['right'] + 48)
    assert start_x < geometry['body']['left'] and end_x > geometry['body']['right'], geometry
    start_y = geometry['rows'][0]['top'] + 8
    end_y = geometry['rows'][-1]['bottom'] - 8

    page.mouse.move(start_x, start_y)
    start_target = page.evaluate("""({x,y}) => {
      const target = document.elementFromPoint(x,y);
      return {tag:target?.tagName, cls:target?.className,
        closestPageHead:!!target?.closest?.('.page-head'),
        closestWorkspace:!!target?.closest?.('#workspaceMain')};
    }""", {"x":start_x,"y":start_y})
    page.mouse.down()
    page.mouse.move(end_x, end_y, steps=8)
    marquee = page.locator('.block-selection-marquee')
    probe = page.evaluate("""() => ({selecting:document.body.classList.contains('block-selecting'),
      boxes:document.querySelectorAll('.block-selection-marquee').length,
      selected:[...State.selectedBlockIds]})""")
    assert probe['boxes'] == 1, {"geometry":geometry,"start":start_target,"probe":probe}
    expect(marquee).to_be_visible()
    assert page.evaluate("""() => {
      const box = document.querySelector('.block-selection-marquee');
      return box && getComputedStyle(box).borderStyle === 'none'
        && getComputedStyle(box).backgroundColor !== 'rgba(0, 0, 0, 0)';
    }""")
    assert page.evaluate("() => [...State.selectedBlockIds]") == [row['id'] for row in geometry['rows']]
    assert page.locator('#pageTitle').count() == 1
    page.mouse.up()
    expect(marquee).to_have_count(0)
    assert page.evaluate("() => [...State.selectedBlockIds]") == [row['id'] for row in geometry['rows']]
    empty_editor = rows.nth(1).locator('.block-editor')
    assert empty_editor.evaluate("element => getComputedStyle(element, '::before').visibility") == 'hidden'
    if viewport_width == 1280:
        _screenshot(page, tmp_path, "notion_gutter_selection_1280.png")
    page.keyboard.press('Escape')
    assert page.evaluate("() => State.selectedBlockIds.size") == 0
    page.wait_for_function("() => getComputedStyle(document.querySelectorAll('#blocks .block-row')[1].querySelector('.block-editor'), '::before').visibility === 'visible'")

    # A box entirely in the gutter crosses the same vertical rows but no body
    # cell; the drag feedback remains visible without selecting those rows.
    gutter_end_x = geometry['body']['left'] - 12
    assert start_x < gutter_end_x
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(gutter_end_x, end_y, steps=8)
    expect(marquee).to_be_visible()
    gutter_probe = page.evaluate("""() => {
      const bounds = node => {
        const r = node.getBoundingClientRect();
        return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};
      };
      return {selected:[...State.selectedBlockIds],
        body:bounds(document.querySelector('#blocks')),
        marquee:bounds(document.querySelector('.block-selection-marquee')),
        rows:[...document.querySelectorAll('#blocks .block-row')].map(row =>
          ({id:row.dataset.blockId,...bounds(row.querySelector('.block-main') || row)})),
        focus:document.activeElement?.id,
        scroll:document.scrollingElement.scrollTop};
    }""")
    if gutter_probe['selected']:
        _screenshot(page, tmp_path, f'notion_gutter_only_failure_{viewport_width}.png')
    assert not gutter_probe['selected'], {'initial':geometry,'start':[start_x,start_y],
        'end':[gutter_end_x,end_y],'actual':gutter_probe}
    page.mouse.up()
    expect(marquee).to_have_count(0)

    page.mouse.move(end_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x, end_y, steps=8)
    expect(marquee).to_be_visible()
    assert page.evaluate("() => [...State.selectedBlockIds]") == [row['id'] for row in geometry['rows']]
    page.mouse.up()
    expect(marquee).to_have_count(0)
    assert not errors, '\n'.join(errors)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_gutter_drag_selects_only_intersected_column_blocks(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("""() => {
      const group = 'gutter-column-selection';
      State.pageData.blocks = [
        EditorCore.createBlock('paragraph', {content:'왼쪽 위', props:{layout_group:group,layout_column:0,layout_columns:2}}),
        EditorCore.createBlock('paragraph', {content:'왼쪽 아래', props:{layout_group:group,layout_column:0,layout_columns:2}}),
        EditorCore.createBlock('paragraph', {content:'오른쪽 위', props:{layout_group:group,layout_column:1,layout_columns:2}}),
        EditorCore.createBlock('paragraph', {content:'오른쪽 아래', props:{layout_group:group,layout_column:1,layout_columns:2}}),
      ];
      renderBlocks();
    }""")
    expect(page.locator('#blocks .block-column')).to_have_count(2)
    geometry = page.evaluate("""() => {
      const columns = [...document.querySelectorAll('#blocks .block-column')].map(column => {
        const rect = column.getBoundingClientRect();
        return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
          ids:[...column.querySelectorAll('.block-row')].map(row => row.dataset.blockId)};
      });
      const body = document.querySelector('#blocks').getBoundingClientRect();
      return {columns,bodyLeft:body.left};
    }""")
    left, right = geometry['columns']
    assert len(left['ids']) == len(right['ids']) == 2, geometry
    assert left['left'] < left['right'] < right['left'] < right['right'], geometry
    start_x = geometry['bodyLeft'] - 80
    end_x = left['right'] - 12
    assert start_x < left['left'] < end_x < right['left'], geometry
    page.mouse.move(start_x, left['top'] + 8)
    page.mouse.down()
    page.mouse.move(end_x, left['bottom'] - 8, steps=8)
    expect(page.locator('.block-selection-marquee')).to_be_visible()
    assert page.evaluate('() => [...State.selectedBlockIds]') == left['ids']
    page.mouse.up()
    expect(page.locator('.block-selection-marquee')).to_have_count(0)
    assert page.evaluate('() => [...State.selectedBlockIds]') == left['ids']
    page.keyboard.press('Escape')
    assert page.evaluate('() => State.selectedBlockIds.size') == 0


@pytest.mark.integration
@pytest.mark.regression
def test_read_only_gutter_drag_selects_and_copies_without_changing_blocks(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 900})
    block_ids = page.evaluate("""() => {
      State.pageData.blocks = [
        EditorCore.createBlock('heading1', {content:'읽기 전용 본문 제목'}),
        EditorCore.createBlock('paragraph', {content:'읽기 전용 본문 문단'}),
      ];
      State.pageData.page.can_edit = false;
      renderBlocks();
      return State.pageData.blocks.map(block => block.id);
    }""")
    assert page.evaluate('() => activePageCanEdit()') is False
    geometry = page.evaluate("""() => {
      const body=document.querySelector('#blocks').getBoundingClientRect();
      const surface=document.querySelector('#workspaceMain').getBoundingClientRect();
      const rows=[...document.querySelectorAll('#blocks .block-row')].map(row => {
        const rect=(row.querySelector('.block-main')||row).getBoundingClientRect();
        return {top:rect.top,bottom:rect.bottom};
      });
      return {bodyLeft:body.left,bodyRight:body.right,surfaceLeft:surface.left,
        surfaceRight:surface.right,rows};
    }""")
    start_x = max(geometry['surfaceLeft'] + 16, geometry['bodyLeft'] - 110)
    end_x = min(geometry['surfaceRight'] - 16, geometry['bodyRight'] + 30)
    assert start_x < geometry['bodyLeft'] and end_x > geometry['bodyRight']
    page.mouse.move(start_x, geometry['rows'][0]['top'] + 5)
    page.mouse.down()
    page.mouse.move(end_x, geometry['rows'][-1]['bottom'] - 5, steps=8)
    expect(page.locator('.block-selection-marquee')).to_be_visible()
    assert page.evaluate('() => [...State.selectedBlockIds]') == block_ids
    page.mouse.up()
    expect(page.locator('.block-selection-marquee')).to_have_count(0)
    page.evaluate("""() => document.addEventListener('copy', event => {
      window.__copiedReadOnlyBlocks = event.clipboardData?.getData('text/plain');
    }, {once:true})""")
    page.keyboard.press('Control+c')
    assert page.evaluate('() => window.__copiedReadOnlyBlocks') == (
        '# 읽기 전용 본문 제목\n읽기 전용 본문 문단')
    page.keyboard.press('Control+x')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.id)') == block_ids
    page.keyboard.press('Escape')
    assert page.evaluate('() => State.selectedBlockIds.size') == 0


@pytest.mark.integration
@pytest.mark.regression
def test_real_viewer_dark_theme_gutter_selection_keeps_server_document_unchanged(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, authority, workspace_id, page_id):
        witness = authority.read_document('owner', page_id)
        authority.set_page_grant('owner', page_id, 'subject', 'content-editor', role='view',
            inherit_to_descendants=False, expected_workspace_epoch=witness['workspace_epoch'],
            expected_document_epoch=witness['document_epoch'], operation_id='gutter-viewer-role')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                owner_context = browser.new_context(locale='ko-KR', viewport={'width':1280,'height':800})
                owner = owner_context.new_page()
                owner.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                owner.locator('#userId').fill('owner')
                owner.locator('#password').fill(password)
                owner.locator('#submitBtn').click()
                expect(owner.locator('#pageTitle')).to_be_visible(timeout=30_000)
                expect(owner.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                owner.wait_for_function('() => typeof State !== "undefined" && State.liveCollaboration?.crdtReady', timeout=30_000)
                first = owner.locator('.block-editor[contenteditable="true"]').first
                first.fill('실제 읽기 권한 첫 문단')
                first.press('Enter')
                expect(owner.locator('#blocks .block-row')).to_have_count(2)
                owner.locator('.block-editor[contenteditable="true"]').last.fill('실제 읽기 권한 둘째 문단')
                owner.wait_for_function('''() => !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive && State.pageData.blocks.length===2''', timeout=30_000)
                original = owner.evaluate('() => State.pageData.blocks.map(block => ({id:block.id,content:block.content}))')
                assert [block['content'] for block in original] == ['실제 읽기 권한 첫 문단', '실제 읽기 권한 둘째 문단']
                server_before = collaborative_page_payload(authority, 'owner', page_id)

                viewer_context = browser.new_context(locale='ko-KR', viewport={'width':1280,'height':800})
                viewer = viewer_context.new_page()
                errors: list[str] = []
                viewer.on('pageerror', lambda error: errors.append(str(error)))
                viewer.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                viewer.locator('#userId').fill('content-editor')
                viewer.locator('#password').fill(password)
                viewer.locator('#submitBtn').click()
                expect(viewer.locator('#workspaceMain')).to_be_visible(timeout=30_000)
                viewer.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                expect(viewer.locator('#pageTitle')).to_be_visible(timeout=30_000)
                expect(viewer.locator('#pageTitle')).to_be_disabled(timeout=30_000)
                viewer.wait_for_function('() => typeof State !== "undefined" && State.liveCollaboration?.crdtReady && State.pageData?.blocks?.length===2', timeout=30_000)
                assert viewer.evaluate('() => State.pageData.blocks.map(block => ({id:block.id,content:block.content}))') == original
                viewer.evaluate("() => applyColorTheme('dark')")
                assert viewer.evaluate("() => document.documentElement.dataset.theme") == 'dark'
                geometry = viewer.evaluate('''() => {
                  const body=document.querySelector('#blocks').getBoundingClientRect();
                  const surface=document.querySelector('#workspaceMain').getBoundingClientRect();
                  const rows=[...document.querySelectorAll('#blocks .block-row')].map(row => {
                    const rect=(row.querySelector('.block-main')||row).getBoundingClientRect();
                    return {top:rect.top,bottom:rect.bottom};
                  });
                  return {bodyLeft:body.left,bodyRight:body.right,surfaceLeft:surface.left,
                    surfaceRight:surface.right,rows};
                }''')
                start_x = max(geometry['surfaceLeft'] + 16, geometry['bodyLeft'] - 110)
                end_x = min(geometry['surfaceRight'] - 16, geometry['bodyRight'] + 30)
                assert start_x < geometry['bodyLeft'] and end_x > geometry['bodyRight'], geometry
                viewer.mouse.move(start_x, geometry['rows'][0]['top'] + 5)
                viewer.mouse.down()
                viewer.mouse.move(end_x, geometry['rows'][-1]['bottom'] - 5, steps=8)
                expect(viewer.locator('.block-selection-marquee')).to_be_visible()
                assert viewer.evaluate('''() => {
                  const box=document.querySelector('.block-selection-marquee');
                  const style=getComputedStyle(box);
                  return style.borderStyle==='none' && style.backgroundColor==='rgba(35, 131, 226, 0.14)';
                }''')
                assert viewer.evaluate('() => [...State.selectedBlockIds]') == [block['id'] for block in original]
                viewer.mouse.up()
                assert viewer.evaluate('() => [...State.selectedBlockIds]') == [block['id'] for block in original]
                assert viewer.evaluate('() => document.activeElement?.id') == 'pageEditorGroup'
                viewer.evaluate('''() => document.addEventListener('copy', event => {
                  window.__viewerCopiedBlocks=event.clipboardData?.getData('text/plain');
                }, {once:true})''')
                viewer.keyboard.press('Control+c')
                assert viewer.evaluate('() => window.__viewerCopiedBlocks') == '실제 읽기 권한 첫 문단\n실제 읽기 권한 둘째 문단'
                viewer.keyboard.press('Control+x')
                assert viewer.evaluate('() => State.pageData.blocks.map(block => ({id:block.id,content:block.content}))') == original
                viewer.keyboard.press('Escape')
                assert viewer.evaluate('() => State.selectedBlockIds.size') == 0
                viewer.set_viewport_size({'width':390,'height':700})
                viewer.wait_for_function('() => innerWidth===390 && document.querySelector("#pageView").getBoundingClientRect().right<=390')
                assert viewer.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                assert viewer.evaluate('() => State.pageData.blocks.map(block => ({id:block.id,content:block.content}))') == original
                assert not errors, errors
                server_after = collaborative_page_payload(authority, 'owner', page_id)
                assert server_after['page']['title'] == 'Collaborative browser lock'
                assert server_after['blocks'] == server_before['blocks']
                viewer.set_viewport_size({'width':1280,'height':800})
                viewer.wait_for_function('() => innerWidth===1280')
                revoke_geometry = viewer.evaluate('''() => {
                  const body=document.querySelector('#blocks').getBoundingClientRect();
                  const surface=document.querySelector('#workspaceMain').getBoundingClientRect();
                  const rows=[...document.querySelectorAll('#blocks .block-row')].map(row => {
                    const rect=(row.querySelector('.block-main')||row).getBoundingClientRect();
                    return {top:rect.top,bottom:rect.bottom};
                  });
                  return {bodyLeft:body.left,bodyRight:body.right,surfaceLeft:surface.left,
                    surfaceRight:surface.right,rows};
                }''')
                drag_start_x = max(revoke_geometry['surfaceLeft'] + 16, revoke_geometry['bodyLeft'] - 110)
                drag_end_x = min(revoke_geometry['surfaceRight'] - 16, revoke_geometry['bodyRight'] + 30)
                viewer.mouse.move(drag_start_x, revoke_geometry['rows'][0]['top'] + 5)
                viewer.mouse.down()
                viewer.mouse.move(drag_end_x, revoke_geometry['rows'][-1]['bottom'] - 5, steps=8)
                expect(viewer.locator('.block-selection-marquee')).to_be_visible()
                assert viewer.evaluate('() => [...State.selectedBlockIds]') == [block['id'] for block in original]
                witness = authority.read_document('owner', page_id)
                authority.revoke_page_grant('owner', page_id, 'subject', 'content-editor',
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'], operation_id='gutter-viewer-revoke')
                viewer.wait_for_function('''() => State.liveCollaboration.recoveryOnly
                  || document.querySelector('#pageTitle')?.offsetParent === null''', timeout=15_000)
                expect(viewer.locator('#pageTitle')).to_be_hidden(timeout=5_000)
                expect(viewer.locator('#pageView')).to_be_hidden()
                expect(viewer.locator('.block-selection-marquee')).to_have_count(0)
                assert viewer.evaluate('() => !document.body.classList.contains("block-selecting")')
                expect(viewer.get_by_text('실제 읽기 권한 첫 문단')).to_have_count(0)
                assert viewer.evaluate('() => State.selectedBlockIds.size') == 0
                assert viewer.url.endswith('/workspace')
                viewer.mouse.up()
                viewer.reload(wait_until='domcontentloaded')
                expect(viewer.locator('#pageTitle')).to_be_hidden(timeout=30_000)
                expect(viewer.get_by_text('실제 읽기 권한 첫 문단')).to_have_count(0)
                assert collaborative_page_payload(authority, 'owner', page_id)['blocks'] == server_before['blocks']
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_gutter_drag_autoscroll_keeps_marquee_and_selects_later_blocks(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1440, "height": 600})
    page.evaluate("""() => {
      State.pageData.blocks = Array.from({length:45}, (_, index) =>
        EditorCore.createBlock('paragraph', {content:`선택 자동 스크롤 ${index}`}));
      renderBlocks();
    }""")
    geometry = page.evaluate("""() => {
      const body = document.querySelector('#blocks').getBoundingClientRect();
      const surface = document.querySelector('#workspaceMain').getBoundingClientRect();
      const first = document.querySelector('#blocks .block-row .block-main').getBoundingClientRect();
      const endY = Math.min(surface.bottom, innerHeight) - 12;
      const initiallyVisibleLast = [...document.querySelectorAll('#blocks .block-row')]
        .findLastIndex(row => row.getBoundingClientRect().top < endY);
      return {startX:body.left - 140, endX:body.right + 20,
        startY:first.top + 8, endY, initiallyVisibleLast};
    }""")
    assert 0 < geometry['startX'] < geometry['endX'] < 1440
    start_target = page.evaluate("""({x,y}) => {
      const target = document.elementFromPoint(x,y);
      return {tag:target?.tagName, cls:String(target?.className),
        inWorkspace:!!target?.closest?.('#workspaceMain'),
        inBlock:!!target?.closest?.('.block-main')};
    }""", {"x":geometry['startX'], "y":geometry['startY']})
    page.mouse.move(geometry['startX'], geometry['startY'])
    page.mouse.down()
    page.mouse.move(geometry['endX'], geometry['endY'], steps=12)
    marquee = page.locator('.block-selection-marquee')
    probe = page.evaluate("""() => ({box:!!document.querySelector('.block-selection-marquee'),
      selected:State.selectedBlockIds.size, pageScroll:document.querySelector('#pageView').scrollTop,
      workspaceScroll:document.querySelector('#workspaceMain').scrollTop})""")
    assert probe['box'], {"geometry":geometry, "target":start_target, "probe":probe}
    expect(marquee).to_be_visible()
    page.wait_for_function("""() => {
      const scrollers = [document.querySelector('#pageView'),
        document.querySelector('#workspaceMain'), document.scrollingElement];
      return scrollers.some(scroller => scroller.scrollTop > 500);
    }""", timeout=10_000)
    assert page.evaluate("""() => {
      const box = document.querySelector('.block-selection-marquee');
      return box && getComputedStyle(box).borderStyle === 'none'
        && State.selectedBlockIds.size > 12;
    }""")
    assert page.evaluate("() => State.selectedBlockIds.has(State.pageData.blocks[0].id)")
    assert page.evaluate("""() => Math.max(...State.pageData.blocks.map((block, index) =>
      State.selectedBlockIds.has(block.id) ? index : -1))""") > geometry['initiallyVisibleLast']
    page.mouse.up()
    expect(marquee).to_have_count(0)
    assert page.evaluate("() => State.selectedBlockIds.size") > 12


@pytest.mark.integration
@pytest.mark.regression
def test_ui_gutter_drag_into_database_selects_rows_not_container_block(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 800})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error.stack or error)))
    page.evaluate("""() => {
      State.pageData.blocks = [EditorCore.createBlock('table', {
        content:'연결 검증 표',
        props:{properties:[
          {id:'title',name:'이름',type:'title'},
          {id:'date',name:'날짜',type:'date'},
        ],rows:[
          {id:'row-a',title:'첫 행',date:'2026-09-16'},
          {id:'row-b',title:'둘째 행',date:''},
        ]},
      })];
      renderBlocks();
    }""")
    grid = page.locator('#blocks .dynamic-table-grid')
    expect(grid.locator('.table-row:not(.table-header)')).to_have_count(2)
    # renderBlocks replaces the fixture's initial paragraph. Wait for its
    # paragraph-only -6px margin to leave layout before measuring drag points.
    page.wait_for_function("""() => {
      const blocks=document.querySelector('#blocks'), head=document.querySelector('.page-head');
      return blocks?.firstElementChild?.dataset.type === 'table'
        && Math.abs(blocks.getBoundingClientRect().top-head.getBoundingClientRect().bottom) < 0.1;
    }""")
    points = page.evaluate("""() => {
      const grid = document.querySelector('#blocks .dynamic-table-grid').getBoundingClientRect();
      const tableRows = [...document.querySelectorAll('#blocks .dynamic-table-grid .table-row:not(.table-header)')]
        .map(row => row.getBoundingClientRect());
      return {startX:grid.left-100, endX:grid.left+30,
        startY:grid.top-20, endY:tableRows[1].top+tableRows[1].height/2,
        rowTops:tableRows.map(row => row.top)};
    }""")
    assert points['startX'] > 0
    page.mouse.move(points['startX'], points['startY'])
    page.mouse.down()
    page.mouse.move(points['endX'], points['endY'], steps=8)
    expect(page.locator('.block-selection-marquee')).to_be_visible()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    expect(page.locator('.database-row-selection-toolbar')).to_contain_text('2개 선택됨')
    assert page.evaluate("""() => [...document.querySelectorAll('#blocks .dynamic-table-grid .table-row:not(.table-header)')]
      .map(row => row.getBoundingClientRect().top)""") == points['rowTops']
    assert page.evaluate("() => State.selectedBlockIds.size") == 0
    assert page.evaluate("""() => [...document.querySelectorAll('#blocks .dynamic-table-grid .table-row:not(.table-header)')]
      .every(row => [...row.querySelectorAll('[data-grid-row]')].every(cell =>
        getComputedStyle(cell).backgroundColor !== 'rgba(0, 0, 0, 0)'))""")
    page.mouse.up()
    expect(page.locator('.block-selection-marquee')).to_have_count(0)
    expect(grid.locator('.database-row-select-control input:checked')).to_have_count(3)
    page.mouse.move(points['startX'], points['startY'])
    page.mouse.down()
    page.mouse.move(points['startX'] + 20, points['endY'], steps=8)
    expect(grid.locator('.database-row-selected')).to_have_count(0)
    page.keyboard.press('Escape')
    page.mouse.up()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    grid.locator('[data-select-all-rows]').click()
    expect(grid.locator('.database-row-selected')).to_have_count(0)
    expect(page.locator('.database-row-selection-toolbar')).not_to_be_visible()

    # The same database rows must also be selectable from the page's right
    # gutter, as observed in the signed-in Notion document.
    right_points = page.evaluate("""() => {
      const grid = document.querySelector('#blocks .dynamic-table-grid').getBoundingClientRect();
      const surface = document.querySelector('#workspaceMain').getBoundingClientRect();
      const rows = [...document.querySelectorAll('#blocks .dynamic-table-grid .table-row:not(.table-header)')]
        .map(row => row.getBoundingClientRect());
      return {startX:Math.min(surface.right-12, grid.right+30), endX:grid.left+30,
        startY:grid.top-20, endY:rows[1].top+rows[1].height/2,
        gridRight:grid.right, surfaceRight:surface.right};
    }""")
    assert right_points['gridRight'] < right_points['startX'] < right_points['surfaceRight'], right_points
    page.mouse.move(right_points['startX'], right_points['startY'])
    page.mouse.down()
    page.mouse.move(right_points['endX'], right_points['endY'], steps=8)
    expect(page.locator('.block-selection-marquee')).to_be_visible()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    assert page.evaluate("() => State.selectedBlockIds.size") == 0
    page.mouse.up()
    expect(page.locator('.block-selection-marquee')).to_have_count(0)
    page.keyboard.press('Escape')
    expect(grid.locator('.database-row-selected')).to_have_count(0)

    row_bounds = page.evaluate("""() => [...document.querySelectorAll('#blocks .dynamic-table-grid .table-row:not(.table-header)')]
      .map(row => {const rect=row.getBoundingClientRect();return {top:rect.top,bottom:rect.bottom};})""")
    page.mouse.move(points['startX'], row_bounds[0]['top'] + 4)
    page.mouse.down()
    page.mouse.move(points['endX'], row_bounds[0]['bottom'] - 4, steps=8)
    page.mouse.up()
    expect(grid.locator('.database-row-selected')).to_have_count(1)
    page.keyboard.down('Shift')
    page.mouse.move(points['startX'], row_bounds[1]['top'] + 4)
    page.mouse.down()
    page.mouse.move(points['endX'], row_bounds[1]['bottom'] - 4, steps=8)
    page.mouse.up()
    page.keyboard.up('Shift')
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    page.locator('[data-clear-selected-rows]').click()
    expect(grid.locator('.database-row-selected')).to_have_count(0)

    cells = grid.locator('[data-grid-row][data-grid-column]')
    first = cells.nth(0).bounding_box()
    last = cells.nth(3).bounding_box()
    page.mouse.move(first['x'] + first['width'] / 2, first['y'] + first['height'] / 2)
    page.mouse.down()
    page.mouse.move(last['x'] + last['width'] / 2, last['y'] + last['height'] / 2, steps=8)
    page.mouse.up()
    expect(grid.locator('.grid-cell-selected')).to_have_count(4)
    expect(grid.locator('.database-row-selected')).to_have_count(0)
    assert not errors, '\n'.join(errors)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_database_selected_rows_copy_links_and_count_clears_selection(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 800})
    errors: list[str] = []
    page.on('pageerror', lambda error: errors.append(str(error.stack or error)))
    page.evaluate("""() => {
      window.__selectedLinksCopied = [];
      Object.defineProperty(navigator, 'clipboard', {configurable:true,
        value:{writeText:async text => window.__selectedLinksCopied.push(text)}});
      State.pageData.blocks = [EditorCore.createBlock('table', {
        content:'링크 복사 표',
        props:{properties:[{id:'title',name:'이름',type:'title'}],rows:[
          {page_id:'p_alpha',title:'첫 페이지'},
          {page_id:'p_beta',title:'둘째 페이지'},
        ]},
      })];
      renderBlocks();
    }""")
    grid = page.locator('#blocks .dynamic-table-grid')
    geometry = page.evaluate("""() => {
      const grid=document.querySelector('#blocks .dynamic-table-grid').getBoundingClientRect();
      const last=document.querySelector('#blocks .dynamic-table-grid .table-row:last-of-type').getBoundingClientRect();
      return {fromX:grid.left-100,fromY:grid.top-20,toX:grid.left+30,toY:last.top+last.height/2};
    }""")
    page.mouse.move(geometry['fromX'], geometry['fromY'])
    page.mouse.down()
    page.mouse.move(geometry['toX'], geometry['toY'], steps=8)
    page.mouse.up()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    more = page.locator('[data-selected-row-more]')
    expect(more).to_be_visible()
    more.click()
    menu = page.locator('.database-row-selection-menu')
    expect(menu).to_be_visible()
    menu.get_by_role('menuitem', name='모든 링크 복사').click()
    expected = page.evaluate("() => [location.origin+'/page/p_alpha',location.origin+'/page/p_beta'].join('\\n')")
    assert page.evaluate('() => window.__selectedLinksCopied') == [expected]
    expect(menu).not_to_be_visible()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    page.locator('[data-clear-selected-rows]').click()
    expect(grid.locator('.database-row-selected')).to_have_count(0)

    page.evaluate("""() => {
      State.pageData.blocks[0].props.rows[1].page_id = '';
      renderBlocks();
    }""")
    grid = page.locator('#blocks .dynamic-table-grid')
    page.mouse.move(geometry['fromX'], geometry['fromY'])
    page.mouse.down()
    page.mouse.move(geometry['toX'], geometry['toY'], steps=8)
    page.mouse.up()
    expect(grid.locator('.database-row-selected')).to_have_count(2)
    expect(page.locator('[data-selected-row-more]')).not_to_be_visible()
    assert page.evaluate('() => window.__selectedLinksCopied') == [expected]
    assert not errors, '\n'.join(errors)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_canonical_rows_show_trash_control_and_keep_selection(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const root = document.createElement('div');
      root.id = 'rowTrashProbe';
      root.style.cssText = 'position:relative;margin:60px 40px;';
      root.innerHTML = '<div class="dynamic-table-grid">'
        + '<div class="table-row table-header"><span>이름</span></div>'
        + '<div class="table-row"><span data-grid-row="row-a" data-grid-column="title">첫 행</span></div>'
        + '<div class="table-row"><span data-grid-row="row-b" data-grid-column="title">둘째 행</span></div>'
        + '</div>';
      document.body.append(root);
      window.__rowTrashSelected = [];
      const grid = root.querySelector('.dynamic-table-grid');
      NotionGridSelection.bind(grid, {id:'row-trash-probe', clearBlocks:() => {},
        canEdit:() => true, rowLinks:() => [], canTrashRows:keys => keys.length === 2,
        trashRows:keys => {
          window.__rowTrashSelected.push(keys);
          return window.__rowTrashSelected.length===1
            ? new Promise(resolve=>{window.__releaseRowTrash=resolve;})
            : Promise.resolve();
        },
        error:message => {throw Error(message);}});
      NotionGridSelection.syncGutterRows([{grid, rows:['row-a','row-b']}]);
    }""")
    root = page.locator('#rowTrashProbe')
    expect(root.locator('.database-row-selected')).to_have_count(2)
    expect(root.locator('[data-trash-selected-rows]')).to_be_visible()
    expect(root.locator('[data-selected-row-more]')).to_be_visible()
    root.locator('[data-trash-selected-rows]').click()
    page.wait_for_function("() => window.__rowTrashSelected.length === 1")
    assert page.evaluate('() => window.__rowTrashSelected') == [['row-a', 'row-b']]
    expect(root.locator('[data-trash-selected-rows]')).to_be_disabled()
    expect(root.locator('[data-selected-row-more]')).to_be_disabled()
    root.locator('[data-trash-selected-rows]').evaluate('button => button.click()')
    assert page.evaluate('() => window.__rowTrashSelected.length') == 1
    page.evaluate('() => window.__releaseRowTrash()')
    expect(root.locator('[data-trash-selected-rows]')).to_be_enabled()
    expect(root.locator('[data-selected-row-more]')).to_be_enabled()
    expect(root.locator('.database-row-selected')).to_have_count(2)
    root.locator('[data-selected-row-more]').click()
    expect(root.locator('[data-menu-trash-selected-rows]')).to_be_visible()
    expect(root.locator('[data-copy-selected-links]')).not_to_be_visible()
    root.locator('[data-menu-trash-selected-rows]').click()
    page.wait_for_function("() => window.__rowTrashSelected.length === 2")
    assert page.evaluate('() => window.__rowTrashSelected[1]') == ['row-a', 'row-b']


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_canonical_rows_submit_one_fenced_batch_trash_request(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.evaluate("""() => {
      const block = EditorCore.createBlock('table', {content:'행 휴지통',
        props:{database_id:'db-probe',properties:[{id:'title',name:'이름',type:'title'}],
          rows:[{page_id:'row-a',_canonical_row_id:'row-a',title:'첫 행'},
            {page_id:'row-b',_canonical_row_id:'row-b',title:'둘째 행'}]}});
      State.pageData.blocks = [block];
      State.pageData.databases = [{id:'db-probe',version:4,rows:[]}];
      State.navigation.workspaceId = 'ws-probe';
      State.navigation.collaborativeSharing = true;
      State.navigation.collaborativeDatabaseRowTrash = true;
      collaborativeDatabaseActive = () => true;
      activePageCanEdit = () => true;
      holdPageMoveInputs = () => () => {};
      flushPageMoveEditors = async () => true;
      refreshAfterCollaborativeMove = async () => {};
      reloadCanonicalDatabases = async () => [];
      renderBlocks = () => {};
      PageMetadataSync = {invalidate:() => {}};
      window.__rowTrashRequests = [];
      window.__rowRestoreRequests = [];
      api = async (path, options = {}) => {
        if (path.endsWith('/collaboration')) return {page:{id:State.activePageId,
          workspace_id:'ws-probe',document_sequence:7+window.__rowTrashRequests.length
            +window.__rowRestoreRequests.length,
          workspace_epoch:2,document_epoch:3}};
        if (path.includes('/databases/db-probe/row-trash?')) return {items:[
          {row_id:'row-a',row_version:2},{row_id:'row-b',row_version:2}]};
        if (path.endsWith('/databases/db-probe')) return {database:{id:'db-probe',version:4,
          rows:[{id:'row-a',version:1},{id:'row-b',version:1}]}};
        if (path.endsWith('/rows/trash-batch')) {
          window.__rowTrashRequests.push({body:options.body});
          return {contract:'code_assist.notion.database_row_trash_batch.v1',
            workspace_id:'ws-probe',document_id:State.activePageId,
            database_id:'db-probe',archived:true,
            operation_id:options.body.operation_id,workspace_epoch:2,
            rows:options.body.rows.map(item => ({row_id:item.row_id,
              row_version:item.expected_row_version + 1}))};
        }
        if (path.endsWith('/rows/restore-batch')) {
          window.__rowRestoreRequests.push({body:options.body});
          return {contract:'code_assist.notion.database_row_trash_batch.v1',
            workspace_id:'ws-probe',document_id:State.activePageId,
            database_id:'db-probe',archived:false,
            operation_id:options.body.operation_id,workspace_epoch:2,
            rows:options.body.rows.map(item => ({row_id:item.row_id,
              row_version:item.expected_row_version + 1}))};
        }
        throw Error('unexpected request: '+path);
      };
      const root = document.createElement('div');
      root.id = 'rowTrashAppProbe';
      root.style.cssText = 'position:relative;margin:60px 40px;';
      root.innerHTML = '<div class="dynamic-table-grid">'
        + '<div class="table-row table-header"><span>이름</span></div>'
        + '<div class="table-row"><span><input data-db-row="0" data-db-field="title" value="첫 행"></span></div>'
        + '<div class="table-row"><span><input data-db-row="1" data-db-field="title" value="둘째 행"></span></div>'
        + '</div>';
      document.body.append(root);
      bindBlockGrid(root, block);
      const grid = root.querySelector('.dynamic-table-grid');
      NotionGridSelection.syncGutterRows([{grid,rows:['row-a','row-b']}]);
    }""")
    root = page.locator('#rowTrashAppProbe')
    expect(root.locator('[data-trash-selected-rows]')).to_be_visible()
    root.locator('[data-trash-selected-rows]').click()
    page.wait_for_function('() => window.__rowTrashRequests.length === 1')
    request = page.evaluate('() => window.__rowTrashRequests[0].body')
    assert request['expected_document_sequence'] == 7
    assert request['expected_database_version'] == 4
    assert request['rows'] == [
        {'row_id': 'row-a', 'expected_row_version': 1},
        {'row_id': 'row-b', 'expected_row_version': 1},
    ]
    expect(page.locator('#databaseRowTrashStatus')).to_be_visible()
    expect(page.locator('#databaseRowTrashStatus')).to_contain_text('2개 데이터베이스 항목')
    page.locator('#databaseRowTrashStatus').get_by_role('button', name='복원').click()
    page.wait_for_function('() => window.__rowRestoreRequests.length === 1')
    restored = page.evaluate('() => window.__rowRestoreRequests[0].body')
    assert restored['expected_document_sequence'] == 8
    assert restored['rows'] == [
        {'row_id': 'row-a', 'expected_row_version': 2},
        {'row_id': 'row-b', 'expected_row_version': 2},
    ]
    assert restored['operation_id'] != request['operation_id']
