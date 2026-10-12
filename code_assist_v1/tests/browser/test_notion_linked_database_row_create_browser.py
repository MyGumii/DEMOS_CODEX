"""The linked view's create control works in a real browser DOM."""
from pathlib import Path

import pytest


@pytest.mark.integration
def test_ui_linked_row_button_follows_permission_in_chrome():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    script = Path(__file__).resolve().parents[2] / "notion_static" / "notion_linked_databases.js"
    styles = Path(__file__).resolve().parents[2] / "notion_static" / "notion.css"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(locale="ko-KR")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content('<main><section class="linked-database" id="linked"></section></main>')
            page.add_style_tag(path=str(styles))
            page.add_script_tag(path=str(script))
            page.evaluate("""async () => {
              const scope={pageId:'instance'};
              const block={id:'linked-browser',type:'table',props:{
                views:[{id:'table',name:'표',type:'table',config:{}}],
                active_view_id:'table'}};
              const data={source:{document_id:'source',database_id:'db'},
                database:{title:'원본',effective_role:'edit',properties:[],
                  rows:[{title:'Apple'},{title:'Banana'}],
                  default_template:{template_id:'template-1'},
                  templates:[{id:'template-1',name:'회의록',state:'active'}]}};
              const slot={id:block.id,block,node:document.querySelector('#linked')};
              window.rowTest={editable:true,calls:[],templateCalls:[],binds:0,data,query:'',fullPage:false};
              const host={current:()=>true,visible:()=>true,canEdit:()=>true,
                canEditCells:()=>false,canCreateRows:()=>window.rowTest.editable,
                canCreateTemplates:()=>window.rowTest.editable,
                searchValue:()=>window.rowTest.query,
                searchChange:(_,__,value)=>{window.rowTest.query=value;},
                fullPage:()=>window.rowTest.fullPage,
                fullPageUrl:(_,block)=>`/page/instance?linked_db=${block}`,
                toggleFullPage:()=>{
                  window.rowTest.fullPage=!window.rowTest.fullPage;
                  void window.rowTest.view.refresh(true);
                },
                bindRows:(_,slot)=>{
                  window.rowTest.binds++;
                  slot.node.querySelectorAll('[data-linked-row-create], [data-linked-row-create-blank], [data-linked-template-picker]')
                    .forEach(button=>button.disabled=button.dataset.defaultUnavailable==='true');
                },
                bindTemplates:(_,slot)=>{
                  const button=slot.node.querySelector('[data-linked-template-create]');
                  if(button)button.disabled=false;
                },
                createRow:async(_,__,___,options)=>{window.rowTest.calls.push(options||null);},
                createTemplate:async(_,__,data)=>{window.rowTest.templateCalls.push(data.database.title);},
                load:async()=>data,project:()=>({type:'table',props:{rows:data.database.rows,properties:[]}}),
                render:()=>'<div role="grid">'+data.database.rows
                  .filter(row=>row.title.toLowerCase().includes(window.rowTest.query.toLowerCase()))
                  .map(row=>'<span data-search-row>'+row.title+'</span>').join('')+'</div>',
                open:()=>{},commit:()=>true};
              window.rowTest.view=NotionLinkedDatabases.create(host);
              await window.rowTest.view.mount(scope,[slot]);
            }""")
            button = page.locator("#linked [data-linked-row-create]")
            expect(button).to_be_visible()
            filter_button = page.locator("#linked [data-linked-toolbar-filter]")
            sort_button = page.locator("#linked [data-linked-toolbar-sort]")
            filter_button.click()
            expect(page.locator("#linked [data-linked-view-filter-panel]")).to_be_visible()
            assert filter_button.get_attribute("aria-expanded") == "true"
            sort_button.click()
            expect(page.locator("#linked [data-linked-view-sort-panel]")).to_be_visible()
            assert filter_button.get_attribute("aria-expanded") == "false"
            assert sort_button.get_attribute("aria-expanded") == "true"
            sort_button.click()
            search_button = page.locator("#linked [data-linked-toolbar-search]")
            search_button.click()
            search_input = page.locator("#linked [data-linked-search]")
            expect(search_input).to_be_visible()
            search_input.fill("Banana")
            expect(page.locator("#linked [data-search-row]")).to_have_count(1)
            assert page.locator("#linked [data-search-row]").inner_text() == "Banana"
            assert search_input.evaluate("element => document.activeElement === element")
            search_input.press("Escape")
            expect(page.locator("#linked [data-search-row]")).to_have_count(2)
            full_page = page.locator("#linked [data-linked-full-page]")
            expect(full_page).to_have_attribute("data-linked-open-full-page", "")
            expect(full_page).to_have_attribute("href", "/page/instance?linked_db=linked-browser")
            full_page.click()
            expect(full_page).to_have_attribute("data-linked-close-full-page", "")
            assert page.evaluate("window.rowTest.fullPage") is True
            full_page.click()
            expect(full_page).to_have_attribute("data-linked-open-full-page", "")
            assert page.evaluate("window.rowTest.fullPage") is False
            button.click()
            page.wait_for_function("window.rowTest.calls.length === 1")
            picker = page.locator("#linked [data-linked-template-picker]")
            picker.click()
            expect(page.locator("#linked [data-linked-template-menu]")).to_be_visible()
            page.locator('#linked [data-linked-template-id="template-1"]').click()
            page.wait_for_function("window.rowTest.calls.length === 2")
            picker.click()
            page.locator('#linked [data-linked-template-id=""]').click()
            page.wait_for_function("window.rowTest.calls.length === 3")
            assert page.evaluate("window.rowTest.calls") == [
                None, {"templateId": "template-1"}, {"blank": True}]
            assert page.evaluate("window.rowTest.binds") >= 4
            page.evaluate("""async () => {
              window.rowTest.data.database.templates[0].body_unavailable=true;
              await window.rowTest.view.refresh(true);
            }""")
            expect(button).to_be_disabled()
            blank = page.locator("#linked [data-linked-row-create-blank]")
            expect(blank).to_be_enabled()
            blank.click()
            page.wait_for_function("window.rowTest.calls.length === 4")
            assert page.evaluate("window.rowTest.calls[3]") == {"blank": True}
            page.evaluate("""async () => {
              window.rowTest.data.database.templates=[];
              window.rowTest.data.database.default_template={template_id:''};
              await window.rowTest.view.refresh(true);
            }""")
            picker.click()
            expect(page.locator("#linked [data-linked-template-menu]")).to_be_visible()
            page.locator("#linked [data-linked-template-create]").click()
            page.wait_for_function("window.rowTest.templateCalls.length === 1")
            assert page.evaluate("window.rowTest.templateCalls") == ["원본"]
            page.set_viewport_size({"width": 390, "height": 700})
            geometry = page.evaluate("""() => {
              const toolbar=document.querySelector('#linked .database-toolbar');
              const buttons=[...toolbar.querySelectorAll('button')]
                .filter(button=>button.getClientRects().length);
              return {viewport:innerWidth,scroll:document.documentElement.scrollWidth,
                buttons:buttons.map(button=>({right:button.getBoundingClientRect().right,
                  width:button.getBoundingClientRect().width}))};
            }""")
            assert geometry["scroll"] <= geometry["viewport"], geometry
            assert all(item["right"] <= 390 and item["width"] > 0
                       for item in geometry["buttons"]), geometry
            full_page.click()
            expect(full_page).to_have_attribute("data-linked-close-full-page", "")
            full_page_geometry = page.evaluate("""() => {
              const root=document.querySelector('main');
              root.classList.add('page-view','database-full-page');
              return {width:root.getBoundingClientRect().width,
                scroll:document.documentElement.scrollWidth,viewport:innerWidth};
            }""")
            assert full_page_geometry["width"] >= 340, full_page_geometry
            assert full_page_geometry["scroll"] <= full_page_geometry["viewport"], full_page_geometry
            full_page.click()
            expect(full_page).to_have_attribute("data-linked-open-full-page", "")
            page.locator("main").evaluate("root => root.classList.remove('page-view','database-full-page')")
            page.evaluate("""async () => {
              window.rowTest.editable=false;
              await window.rowTest.view.refresh(true);
            }""")
            expect(button).to_have_count(0)
            assert not errors, errors
        finally:
            browser.close()
