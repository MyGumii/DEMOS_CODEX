"""The selection type menu must preserve stored text and adjacent blocks."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import (
    document_page, _seed_blocks, _select_first_quote,
)


def _body(block):
    # The legacy whole-document save refreshes block timestamps.
    return {key:block.get(key) for key in ('id','type','content','props','position')}


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light',1280),('dark',390)])
def test_inline_type_menu_conversions_preserve_saved_source_and_sibling(document_page,tmp_path,theme,width):
    from playwright.sync_api import expect

    page=document_page
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.set_viewport_size({'width':width,'height':800})
    page.evaluate('theme => applyColorTheme(theme)',theme)
    page_id=page.evaluate('State.activePageId')
    origin='/'.join(page.url.split('/',3)[:3])
    url=f'{origin}/api/notion/pages/{page_id}'
    output=Path(os.environ.get('NOTION_INLINE_TYPE_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    types=['paragraph','heading1','heading2','heading3','heading4','bullet','numbered',
        'todo','toggle','code','quote','callout','equation','toggle_heading1',
        'toggle_heading2','toggle_heading3','toggle_heading4']
    cases=types+[f'layout_columns_{count}' for count in range(2,6)]
    results=[]
    for kind in cases:
        _seed_blocks(page,[{'type':'paragraph','content':'앞 선택 뒤','props':{
            'rich_text_html':'앞 <b>선택</b> 뒤','text_color':'red','background_color':'yellow'}},
            {'type':'paragraph','content':'뒤의 문장은 그대로'}])
        original=page.request.get(url).json()['blocks']
        source_id,following_id=[b['id'] for b in original]
        _select_first_quote(page,'선택')
        page.locator('#inlineToolbar [data-inline-format="type"]').click()
        menu=page.locator('#inlineFormatPopover')
        expect(menu.locator('[data-inline-type]')).to_have_count(22)
        menu.locator(f'[data-inline-type="{kind}"]').click()
        expect(menu).to_be_hidden()
        page.wait_for_function('() => !State.dirty && !State.saving',timeout=15000)
        rows=page.request.get(url).json()['blocks']
        source=next(b for b in rows if b['id']==source_id)
        following=next(b for b in rows if b['id']==following_id)
        assert source['content']==original[0]['content'],kind
        assert {key:following.get(key) for key in ('id','type','content','props')}=={
            key:original[1].get(key) for key in ('id','type','content','props')},kind
        assert source['props']['text_color']=='red',kind
        assert source['props']['background_color']=='yellow',kind
        if kind.startswith('layout_columns_'):
            count=int(kind[-1])
            assert source['type']=='paragraph'
            assert source['props']['layout_column']==0
            assert source['props']['layout_columns']==count
            lanes=[b for b in rows if b['props'].get('layout_group')==source['props']['layout_group']]
            assert sorted(b['props']['layout_column'] for b in lanes)==list(range(count))
            wrapper=next(b for b in rows if b['props'].get('layout_container'))
            assert wrapper['props']['children']==[b['id'] for b in lanes]
            assert following['id'] not in wrapper['props']['children']
            assert len(rows)==count+2
            assert rows[-1]['id']==following_id
        else:
            assert source['type']==kind,kind
            assert len(rows)==2,kind
            assert following['position']==original[1]['position'],kind
        if kind not in ('code','equation'):
            assert source['props']['rich_text_html']==original[0]['props']['rich_text_html'],kind
        results.append({'kind':kind,'blocks':rows})
        page.reload()
        expect(page.locator('#pageView')).to_be_visible()
        page.wait_for_function('() => State.pageData && !State.saving')
        assert page.evaluate('document.documentElement.dataset.theme')==theme
        assert [_body(b) for b in page.request.get(url).json()['blocks']]==[_body(b) for b in rows],kind
        if kind in ('code','equation','toggle_heading4','layout_columns_3','layout_columns_5'):
            page.screenshot(path=str(output/f'inline-type-{kind}-{theme}-{width}.png'))
        assert page.evaluate('State.activePageId')==page_id
    (output/f'inline-type-matrix-{theme}-{width}.json').write_text(
        json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not errors,errors
