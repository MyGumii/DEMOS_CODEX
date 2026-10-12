"""UI-SYNC-MENU: conversion and source controls keep the current body and authority."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _body(block):
    return {key: block.get(key) for key in ("id", "type", "content", "props")}


def _current_collaborative_body(authority, actor, page_id, workspace_id):
    # The stored collaborative frames can lead the typed SQL body. Read their
    # authorized current projection, just as the existing synced service does.
    from code_assist_v1.notion_collaboration import synced_blocks
    prepared = synced_blocks._current(authority, actor, page_id, synced_blocks.MAX_SOURCE_BYTES,
        workspace_id=workspace_id)
    return json.loads(prepared.descriptor_json)["blocks"]


def _output(tmp_path):
    path = Path(os.environ.get("NOTION_SYNC_MENU_ARTIFACT_DIR", str(tmp_path)))
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light", 1280), ("dark", 390)])
def test_ui_synced_menu_personal_conversion_copy_and_stale_controls(document_page, tmp_path, theme, width):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 800})
    page.evaluate("theme => applyColorTheme(theme)", theme)
    _seed_blocks(page, [{"type": "paragraph", "content": "전체 동기화 문장", "props": {
        "rich_text_html": "전체 <b>동기화</b> 문장", "text_color": "red", "background_color": "yellow"}},
        {"type": "paragraph", "content": "뒤 문장은 그대로"}])
    page_id = page.evaluate("State.activePageId")
    origin = "/".join(page.url.split("/", 3)[:3])
    url = f"{origin}/api/notion/pages/{page_id}"
    original = page.request.get(url).json()["blocks"]
    source_id, sibling_id = [b["id"] for b in original]
    row = page.locator(f'#blocks .block-row[data-block-id="{source_id}"]')
    row.hover()
    row.locator(".block-handle").click()
    menu = page.locator('#blockMenu[data-mode="actions"]')
    search = menu.get_by_role("combobox", name="블록 작업 검색")
    search.fill("동기화 블록")
    expect(menu.locator('[data-convert-type="synced_block"]:visible')).to_have_count(1)
    search.press("Enter")
    page.wait_for_function("() => !State.dirty && !State.saving")
    converted = page.request.get(url).json()["blocks"]
    assert converted[0]["id"] == source_id
    assert converted[0]["type"] == "synced_block"
    assert converted[0]["content"] == original[0]["content"]
    assert converted[0]["props"]["rich_text_html"] == original[0]["props"]["rich_text_html"]
    assert converted[0]["props"]["text_color"] == "red"
    assert converted[0]["props"]["background_color"] == "yellow"
    assert converted[0]["props"]["sync_id"]
    assert "synced_from" not in converted[0]["props"]
    assert _body(converted[1]) == _body(original[1])
    output = _output(tmp_path)
    row.locator(".block-editor").click()
    page.screenshot(path=str(output / f"synced-source-{theme}-{width}.png"))

    more = row.get_by_role("button", name="동기화 블록 작업", exact=True)
    more.press("Space")
    expect(search).to_be_focused()
    expect(more).to_have_attribute("aria-expanded", "true")
    search.press("Escape")
    expect(more).to_be_focused()
    expect(more).to_have_attribute("aria-expanded", "false")
    assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in converted]

    row.locator(".block-editor").fill("사본에 들어갈 최신 문장")
    page.wait_for_function("() => !State.dirty && !State.saving")
    row.get_by_role("button", name="동기화 사본 만들기", exact=True).press("Enter")
    page.wait_for_function("() => State.pageData.blocks.length===3 && !State.dirty && !State.saving")
    copied = page.request.get(url).json()["blocks"]
    sources = [b for b in copied if b["type"] == "synced_block"]
    assert len(sources) == 2
    assert sources[0]["content"] == sources[1]["content"] == "사본에 들어갈 최신 문장"
    assert sources[0]["props"]["sync_id"] == sources[1]["props"]["sync_id"]
    assert _body(next(b for b in copied if b["id"] == sibling_id)) == _body(original[1])
    page.reload()
    _wait_for_notion_ready(page)
    assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in copied]

    # Simulated stale client projections are distinct from server-role tests below.
    for stale in ("actor", "type", "readonly", "composing"):
        row.locator(".block-editor").click()
        page.evaluate("""({kind,id}) => {
          if(kind==='actor') State.user.id='a-different-actor';
          else if(kind==='type') State.pageData.blocks.find(b=>b.id===id).type='paragraph';
          else if(kind==='readonly') State.pageData.page.can_edit=false;
          else document.querySelector(`#block-${id} .block-editor`).dataset.imeComposing='true';
        }""", {"kind": stale, "id": source_id})
        row.get_by_role("button", name="동기화 사본 만들기", exact=True).click()
        assert not page.evaluate("State.dirty || State.saving")
        assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in copied]
        page.reload()
        _wait_for_notion_ready(page)
    (output / f"synced-source-result-{theme}-{width}.json").write_text(
        json.dumps(copied, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_synced_menu_native_original_reference_and_real_viewer(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, workspace_id, page_id,
    ):
        witness = authority.read_document("owner", page_id)
        authority.set_page_grant("owner", page_id, "subject", "content-editor", role="view",
            inherit_to_descendants=False, expected_workspace_epoch=witness["workspace_epoch"],
            expected_document_epoch=witness["document_epoch"], operation_id="sync-menu-viewer-grant")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id)
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                _wait_for_notion_ready(page)
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30000)
                page.wait_for_function("() => State.pageData && State.liveCollaboration.crdtReady", timeout=30000)
                assert page.evaluate("State.pageData.page.collaborative_synced_read") is True
                page.wait_for_function('()=>State.liveCollaboration.connected&&!State.liveCollaboration.initialReplay&&State.liveCollaboration.writeAuthorized',timeout=30_000)
                _seed_blocks(page, [{"type": "paragraph", "content": "협업 원본 전체 문장", "props": {
                    "rich_text_html": "협업 <b>원본</b> 전체 문장", "text_color": "red"}},
                    {"type": "paragraph", "content": "협업 다음 문장"}])
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive")
                original = _current_collaborative_body(authority, "owner", page_id, workspace_id)
                source_id = original[0]["id"]
                row = page.locator(f'#blocks .block-row[data-block-id="{source_id}"]')
                row.hover()
                row.locator(".block-handle").click()
                menu = page.locator('#blockMenu[data-mode="actions"]')
                search = menu.get_by_role("combobox", name="블록 작업 검색")
                search.fill("동기화 블록")
                expect(menu.locator('[data-convert-type="synced_block"]:visible')).to_have_count(1)
                search.press("Enter")
                page.wait_for_function("() => State.pageData.blocks[0].type==='synced_block' && !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive")
                page.wait_for_function("()=>activePageCanEdit()&&!State.liveCollaboration.initialReplay&&State.liveCollaboration.writeAuthorized",timeout=30_000)
                converted = _current_collaborative_body(authority, "owner", page_id, workspace_id)
                assert converted[0]["props"]["synced_from"] is None
                assert "sync_id" not in converted[0]["props"]
                assert converted[0]["content"] == original[0]["content"]
                assert converted[0]["props"]["rich_text_html"] == original[0]["props"]["rich_text_html"]
                assert _body(converted[1]) == _body(original[1])
                source_url = f"{origin}/api/notion/workspaces/{workspace_id}/pages/{page_id}/synced-blocks/{source_id}"
                source_read = page.request.get(source_url)
                assert source_read.ok, source_read.text()
                assert _body(source_read.json()["block"]) == _body(converted[0])
                page.evaluate('async()=>{await State.liveCollaboration.persistenceChain}')
                page.wait_for_function('()=>!State.saving&&!State.dirty&&!State.liveCollaboration.unsyncedCount&&!State.liveCollaboration.initialReplay&&State.liveCollaboration.writeAuthorized',timeout=30_000)
                page.evaluate("()=>{window.clipFeedback=[];window.clipReads=[];const notify=setStatus;setStatus=function(text,kind){clipFeedback.push(String(text));return notify(text,kind);};const request=api;api=async function(url,...args){if(url.includes('/synced-blocks/'))clipReads.push(url);return request(url,...args);};}")
                row.locator(".block-editor").click()
                output = _output(tmp_path)
                page.screenshot(path=str(output / "synced-source-native-original.png"))
                page.context.grant_permissions(['clipboard-read','clipboard-write'], origin=origin)
                copy=row.get_by_role("button", name="동기화 복사", exact=True)
                copy.click()
                page.wait_for_function("()=>clipFeedback.some(text=>text.includes('복사 완료!'))",timeout=30_000)
                expect(copy).to_be_enabled(timeout=30_000)
                assert len(_current_collaborative_body(authority, "owner", page_id, workspace_id))==2
                wire=page.evaluate("async()=>{const items=await navigator.clipboard.read();const html=await items[0].getType('text/html').then(b=>b.text());const node=document.createElement('template');node.innerHTML=html;return JSON.parse(node.content.querySelector('[data-notion-synced-reference]').getAttribute('data-notion-synced-reference'));}")
                assert wire['source']=={'workspace_id':workspace_id,'document_id':page_id,'block_id':source_id}
                row.locator('.block-editor').press('Control+v')
                expect(page.locator('.synced-reference-content')).to_contain_text('협업 원본 전체 문장',timeout=30_000)
                # Local rendering precedes durable queue insertion. Observe the
                # reference first, then its real persistence chain before reading
                # the current server projection, not just the initially empty queue.
                page.evaluate('async()=>{await State.liveCollaboration.persistenceChain}')
                page.wait_for_function("() => State.pageData.blocks.length===3 && !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive")
                saved = _current_collaborative_body(authority, "owner", page_id, workspace_id)
                refs = [b for b in saved if b["props"].get("synced_from")]
                assert len(refs) == 1
                assert refs[0]["props"]["synced_from"] == {"document_id": page_id, "block_id": source_id}
                assert refs[0]["content"] == ""
                assert "rich_text_html" not in refs[0]["props"]
                expect(page.locator(".synced-reference-content")).to_contain_text("협업 원본 전체 문장")
                assert _body(saved[-1]) == _body(original[1])
                page.reload()
                _wait_for_notion_ready(page)
                expect(page.locator("#pageTitle")).to_be_enabled()
                expect(page.locator(".synced-reference-content")).to_contain_text("협업 원본 전체 문장")
                assert [_body(b) for b in _current_collaborative_body(authority, "owner", page_id, workspace_id)] == [_body(b) for b in saved]

                viewer = browser.new_page(locale="ko-KR", viewport={"width": 390, "height": 800})
                viewer.goto(origin + "/page/" + page_id)
                viewer.locator("#userId").fill("content-editor")
                viewer.locator("#password").fill(password)
                viewer.locator("#submitBtn").click()
                _wait_for_notion_ready(viewer)
                expect(viewer.locator("#pageTitle")).to_be_disabled(timeout=30000)
                expect(viewer.locator(".synced-reference-content")).to_contain_text("협업 원본 전체 문장")
                expect(viewer.locator('[data-synced-source-copy]')).to_have_count(2)
                expect(viewer.locator('[data-synced-source-menu]')).to_have_count(0)
                source = viewer.locator(".synced-source .block-editor")
                expect(source).to_have_attribute("contenteditable", "false")
                source.hover()
                viewer.screenshot(path=str(output / "synced-source-native-viewer-390.png"))
                assert [_body(b) for b in _current_collaborative_body(authority, "owner", page_id, workspace_id)] == [_body(b) for b in saved]
                (output / "synced-source-native-result.json").write_text(
                    json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                assert not errors, errors
            except Exception:
                if 'page' in locals():
                    output=_output(tmp_path)
                    page.screenshot(path=str(output/'synced-source-native-failure.png'))
                    (output/'synced-source-native-failure.json').write_text(json.dumps(page.evaluate("()=>({status:document.querySelector('#status')?.textContent,blocks:State.pageData?.blocks,feedback:window.clipFeedback,reads:window.clipReads,connected:State.liveCollaboration.connected,replay:State.liveCollaboration.initialReplay,authorized:State.liveCollaboration.writeAuthorized,dirty:State.dirty,unsynced:State.liveCollaboration.unsyncedCount})"),ensure_ascii=False,indent=2),encoding='utf-8')
                raise
            finally:
                browser.close()
