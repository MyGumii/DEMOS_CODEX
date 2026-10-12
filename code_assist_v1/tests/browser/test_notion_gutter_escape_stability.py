"""Native repeated Escape/gutter selection, using only a disposable app."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('snapshot_before_escape', [True, False], ids=['snapshot', 'no-snapshot'])
def test_ui_gutter_zero_selection_after_escape_remains_stable(document_page, tmp_path, snapshot_before_escape):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({'width':1280,'height':900})
    page.evaluate("""() => {
      State.pageData.blocks = [
        EditorCore.createBlock('heading1', {content:'반복 선택 제목'}),
        EditorCore.createBlock('paragraph', {content:''}),
        EditorCore.createBlock('paragraph', {content:'반복 선택 문단'}),
        EditorCore.createBlock('table', {content:'반복 선택 표'}),
      ];
      renderBlocks();
      window.__gutterEvents=[];
      for (const type of ['pointerdown','pointermove','pointerup','lostpointercapture'])
        document.addEventListener(type, event => window.__gutterEvents.push({
          type, x:event.clientX,y:event.clientY,time:performance.now(),
          target:event.target.className,selected:[...State.selectedBlockIds],
        }),{capture:true,passive:true});
    }""")
    expect(page.locator('#blocks .block-row')).to_have_count(4)
    output = Path(os.environ.get('NOTION_DOCUMENT_EXTRAS_ARTIFACT_DIR', str(tmp_path))) / (
        'snapshot' if snapshot_before_escape else 'no-snapshot')
    output.mkdir(parents=True,exist_ok=True)
    cycles=[]
    failed=False
    original_body=page.evaluate("JSON.stringify(State.pageData.blocks)")
    def probe():
        return page.evaluate("""() => {
          const bounds=node=>{const r=node.getBoundingClientRect();
            return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};};
          const marquee=document.querySelector('.block-selection-marquee');
          return {body:bounds(document.querySelector('#blocks')),
            surface:bounds(document.querySelector('#workspaceMain')),
            rows:[...document.querySelectorAll('#blocks .block-row')].map(row=>
              ({id:row.dataset.blockId,...bounds(row.querySelector('.block-main')||row)})),
            selected:[...State.selectedBlockIds],marquee:marquee?bounds(marquee):null,
            focus:document.activeElement?.id};
        }""")
    try:
        for index in range(30):
            before=probe()
            body=before['body']
            start_x=max(before['surface']['left']+16,body['left']-140)
            end_x=min(before['surface']['right']-16,body['right']+48)
            start_y=before['rows'][0]['top']+8
            end_y=before['rows'][-1]['bottom']-8
            assert start_x < body['left'] and end_x > body['right']
            expected=[r['id'] for r in before['rows']]
            page.mouse.move(start_x,start_y)
            page.mouse.down()
            page.mouse.move(end_x,end_y,steps=8)
            expect(page.locator('.block-selection-marquee')).to_be_visible()
            assert probe()['selected']==expected
            page.mouse.up()
            expect(page.locator('.block-selection-marquee')).to_have_count(0)
            if snapshot_before_escape:
                page.screenshot(path=str(output/f'gutter-repeat-selected-{index}.png'))
            page.keyboard.press('Escape')
            assert not probe()['selected']
            page.wait_for_function("() => getComputedStyle(document.querySelectorAll('#blocks .block-row')[1].querySelector('.block-editor'),'::before').visibility==='visible'")
            page.mouse.move(start_x,start_y)
            page.mouse.down()
            page.mouse.move(body['left']-12,end_y,steps=8)
            expect(page.locator('.block-selection-marquee')).to_be_visible()
            held=probe()
            cycles.append({'cycle':index,'initial':before,'held':held})
            assert not held['selected'],cycles[-1]
            # Visibility alone can observe a penultimate animation frame. Wait
            # for the native endpoint before comparing cursor/box geometry.
            page.wait_for_function("""() => {
              const point=window.__gutterEvents.filter(event=>event.type==='pointermove').at(-1);
              const box=document.querySelector('.block-selection-marquee')?.getBoundingClientRect();
              return box && point && Math.abs(box.right-point.x)<0.5
                && Math.abs(box.bottom-point.y)<0.5;
            }""")
            final_paint=probe()
            cycles[-1]['final_pointer_paint']=final_paint
            assert not final_paint['selected'],cycles[-1]
            assert all(row['left']>final_paint['marquee']['right'] for row in final_paint['rows']),cycles[-1]
            page.mouse.up()
            expect(page.locator('.block-selection-marquee')).to_have_count(0)
            assert not probe()['selected']
            assert page.evaluate("JSON.stringify(State.pageData.blocks)")==original_body
    except Exception:
        failed=True
        page.screenshot(path=str(output/'gutter-escape-stability-failure.png'))
        raise
    finally:
        data={'failed':failed,'snapshot_before_escape':snapshot_before_escape,
              'completed_cycles':len(cycles),'cycles':cycles,
              'native_events':page.evaluate('window.__gutterEvents')}
        (output/'gutter-escape-stability.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        page.mouse.up()
