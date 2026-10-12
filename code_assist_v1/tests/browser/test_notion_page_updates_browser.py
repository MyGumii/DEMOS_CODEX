"""UI-R2: page activity uses authorized revisions in the signed-in browser."""

import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r2_collaborative_page_updates_open_from_edit_time(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, _workspace_id, page_id,
    ):
        before = authority.read_document("owner", page_id)
        authority.execute_typed_command(
            "owner", page_id, command_kind="block.insert",
            target_id="page-updates-browser-paragraph",
            payload={"type": "paragraph", "content": "Synthetic update", "after_block_id": None},
            operation_id="page-updates-browser-edit",
            expected_document_sequence=before["document_sequence"],
            expected_workspace_epoch=before["workspace_epoch"],
            expected_document_epoch=before["document_epoch"],
        )
        editable = authority.read_document("owner", page_id)
        authority.save_document(
            "owner", page_id, title="Collaborative update preview",
            blocks=editable["blocks"], expected_revision=editable["revision"],
            expected_workspace_epoch=editable["workspace_epoch"],
            expected_document_epoch=editable["document_epoch"],
            operation_id="page-updates-browser-saved-version",
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1440, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                expect(page.locator("#pageEditTime")).to_be_visible()
                page.locator("#pageEditTime").click()
                updates = page.locator("#pageUpdatesPanel")
                expect(updates).to_be_visible()
                expect(page.locator("#pageUpdatesList .database-update-item").first).to_contain_text("페이지를 편집했습니다")
                expect(page.locator("#pageUpdatesList")).to_contain_text("본인")
                expect(page.locator("#pageUpdatesList .page-update-preview-added").first).to_contain_text("Synthetic update")
                page.locator("#pageUpdatesList [data-update-version]").first.click()
                expect(page.locator("#pageVersionOverlay")).to_be_visible()
                expect(page.locator("#pageVersionDocument .document-preview")).to_be_visible()
                expect(page.locator("#pageVersionList [data-page-version-select]")).not_to_have_count(0)
                expect(page.locator("#pageVersionList [data-page-version-select]").first).to_contain_text(
                    page.evaluate("State.user.display_name"))
                page.locator('[data-page-version-select="1"]').click()
                expect(page.locator("#pageVersionDocument")).not_to_contain_text("불러오는 중")
                assert page.locator("#pageVersionRestoreBtn").is_enabled(), (
                    page.locator("#pageVersionDocument").inner_text(),
                    page.evaluate("""() => ({canEdit:State.pageData?.page?.can_edit,
                      locked:State.pageData?.page?.settings?.locked,
                      selected:PageVersionOverlayState?.selectedId,
                      current:PageVersionOverlayState?.selectedCurrent})"""),
                )
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#pageVersionRestoreBtn").click()
                page.wait_for_function("""() => document.querySelector('#pageVersionOverlay').hidden
                  || Boolean(document.querySelector('#pageVersionNotice').textContent)""")
                assert page.locator("#pageVersionOverlay").is_hidden(), (
                    page.locator("#pageVersionNotice").inner_text(),
                    page.evaluate("""() => ({revision:State.pageData?.page?.revision,
                      sequence:State.pageData?.page?.document_sequence,
                      serverRevision:State.serverRevision,
                      live:{durableCursor:State.liveCollaboration.durableCursor,
                        unsyncedCount:State.liveCollaboration.unsyncedCount,
                        pending:State.liveCollaboration.pendingEnvelopes.size,
                        crdtReady:State.liveCollaboration.crdtReady},
                      blocks:State.pageData?.blocks})"""),
                    {key:authority.read_document("owner",page_id).get(key)
                     for key in ("revision","document_sequence","blocks")},
                )
                expect(page.locator("#pageTitle")).to_be_enabled()
                page.locator("#drawerCloseBtn").click()
                expect(updates).to_be_hidden()
                assert not errors
            finally:
                browser.close()
