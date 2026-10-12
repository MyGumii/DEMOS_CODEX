"""R6/R7: Notion keeps paragraph controls beside text at narrow desktop widths."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page,_seed_blocks


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme',['light','dark'])
def test_narrow_desktop_controls_stay_beside_body_and_mobile_remains_accessible(document_page,tmp_path,theme):
    from playwright.sync_api import expect
    page=document_page
    page.evaluate('theme=>applyColorTheme(theme)',theme)
    _seed_blocks(page,[{'type':'paragraph','content':'간격 검증 문장'},
        {'type':'paragraph','content':'다음 문장'}])
    initial=page.evaluate('State.pageData.blocks')
    row=page.locator('#blocks .block-row').first
    observations=[]
    output=Path(os.environ.get('NOTION_NARROW_CONTROLS_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    for width in (1280,980,902,826,721,720,390):
        page.set_viewport_size({'width':width,'height':836})
        row.hover()
        geometry=row.evaluate("""row=>{
          const bounds=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
          const controls=row.querySelector('.block-controls'),main=row.querySelector('.block-main');
          return {row:bounds(row),controls:bounds(controls),main:bounds(main),title:bounds(document.querySelector('#pageTitle')),
            rootOverflow:document.documentElement.scrollWidth>innerWidth,controlsOpacity:getComputedStyle(controls).opacity};
        }""")
        assert not geometry['rootOverflow'],geometry
        if width>720:
            assert geometry['controls']['right']<=geometry['main']['x'],geometry
            assert abs(geometry['controls']['y']-geometry['main']['y'])<=4,geometry
            assert abs(geometry['main']['x']-geometry['title']['x'])<=2,geometry
            assert abs(geometry['row']['height']-max(geometry['main']['height'],geometry['controls']['height']))<=1,geometry
        else:
            assert geometry['controls']['bottom']<=geometry['main']['y'],geometry
            assert geometry['controlsOpacity']=='1',geometry
        row.locator('.block-handle').click()
        search=page.locator('#blockMenu .menu-search')
        expect(search).to_be_focused()
        search.fill('목록 보기')
        expect(page.locator('#blockMenu [data-convert-type="list"]')).to_be_visible()
        search.press('Escape')
        expect(row.locator('.block-handle')).to_be_focused()
        assert page.evaluate('State.pageData.blocks')==initial
        if width in (902,390):
            page.screenshot(path=str(output/f'block-controls-{theme}-{width}.png'))
        observations.append({'width':width,'geometry':geometry})
    (output/f'block-controls-{theme}.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2),encoding='utf-8')
