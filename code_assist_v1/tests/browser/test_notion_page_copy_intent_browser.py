"""UI-R5: a different browser storage partition can recover a server copy intent."""
from pathlib import Path
from threading import Thread

import pytest
from flask import Response
from werkzeug.serving import make_server

from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, request,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_http import client_for


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("recovery", ("cancel", "retry"))
def test_ui_r5_copy_intent_survives_other_browser_storage_and_can_be_recovered(
        environment, monkeypatch, recovery):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    store, workspace_id = environment
    client, _actor = client_for(store, monkeypatch)
    app = client.application
    source = Path(__file__).resolve().parents[2] / "notion_static" / "notion_page_copy.js"

    @app.get("/copy-test")
    def page():
        return '<!doctype html><script src="/copy-test.js"></script>'

    @app.get("/copy-test.js")
    def script():
        return Response(source.read_text(encoding="utf-8"), mimetype="text/javascript")

    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    body = {**request(store), "operation_id": "browser-lost-storage-intent"}
    scope = {"userId": "owner", "workspaceId": workspace_id, "pageId": "page-e1"}
    bridge = """({scope, request, holdCopy}) => {
      window.copyDispatches = 0;
      window.controller = NotionPageCopy.create({
        storage: () => localStorage, locks: () => navigator.locks,
        transport: async (url, options) => {
          if (holdCopy && url.endsWith('/duplicate')) {
            window.copyDispatches += 1;
            return new Promise(() => {});
          }
          const response = await fetch(url, {method: options.method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(options.body)});
          const data = await response.json();
          if (!response.ok) throw Object.assign(Error(data.message || 'request failed'),
            {status: response.status});
          return data;
        },
      });
      window.copyScope = scope;
      window.copyRequest = request;
    }"""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                first = browser.new_context()
                first_page = first.new_page()
                first_page.goto(origin + "/copy-test", wait_until="load")
                first_page.evaluate(bridge, {"scope": scope, "request": body, "holdCopy": True})
                first_page.evaluate("""() => {
                  window.copyFlight = controller.run(copyScope, {
                    current: () => true, prepare: async () => copyRequest,
                    beforeRetry: async () => {},
                  });
                }""")
                first_page.wait_for_function("() => window.copyDispatches === 1", timeout=20_000)
                first.close()

                second = browser.new_context()
                second_page = second.new_page()
                second_page.goto(origin + "/copy-test", wait_until="load")
                second_page.evaluate(bridge, {"scope": scope, "request": body, "holdCopy": False})
                assert second_page.evaluate("() => localStorage.length") == 0
                outcome = second_page.evaluate("""async (recovery) => {
                  const response = await fetch(`/api/notion/workspaces/${copyScope.workspaceId}/page-copy-intents`);
                  if (!response.ok) throw Error('intent lookup failed');
                  const listed = await response.json();
                  if (listed.items.length !== 1) throw Error('expected one server intent');
                  const item = listed.items[0];
                  const adopted = await controller.adopt(copyScope, item.request);
                  const result = recovery === 'cancel'
                    ? await controller.cancel(copyScope, {
                        current: () => true, operationId: item.operation_id,
                      })
                    : await controller.run(copyScope, {
                        current: () => true, operationId: item.operation_id,
                        prepare: async () => { throw Error('must reuse stored request'); },
                        beforeRetry: async () => {},
                      });
                  return {adopted, state: result.state,
                    localRequest: controller.list(copyScope).length,
                    operationId: item.operation_id};
                }""", recovery)
                assert outcome == {"adopted": True,
                                   "state": "cancelled" if recovery == "cancel" else "committed",
                                   "localRequest": 0 if recovery == "cancel" else 1,
                                   "operationId": "browser-lost-storage-intent"}
                assert client.get(
                    f"/api/notion/workspaces/{workspace_id}/page-copy-intents"
                ).json["items"] == []
                second.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=10)
