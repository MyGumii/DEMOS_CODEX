"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sources = [
  path.join(__dirname, "..", "static", "app.js"),
  path.join(__dirname, "..", "notion_static", "notion.js"),
];

function extractAuthenticatedFetch(source, sourcePath) {
  const match = source.replace(/\r\n/g, "\n").match(
    /async function authenticatedFetch\(path, opts = \{\}\) \{[\s\S]*?\n\}\n\nasync function api/,
  );
  assert.ok(match, `${sourcePath}: authenticatedFetch implementation is required`);
  return match[0].replace(/\n\nasync function api$/, "");
}

async function verifySource(sourcePath) {
  const calls = [];
  let relockRefreshes = 0;
  const context = {
    URL,
    State: { csrfToken: "A".repeat(43), user: { id: "owner" }, activePageId: "p1" },
    TemporaryUnlockGrants: new Map([["p1", { userId: "owner", token: "tab-grant" }]]),
    refreshPageSettingsControls: () => { relockRefreshes += 1; },
    renderTopbarPageActions: () => {},
    renderPageLists: () => {},
    location: {
      href: "https://agent.example.test/workspace",
      origin: "https://agent.example.test",
    },
    fetch: async (target, init) => {
      calls.push({ target, init });
      return { ok: target !== "/api/notion/pages/p1/deny",
        status: target === "/api/notion/pages/p1/deny" ? 403 : 200 };
    },
  };
  const implementation = extractAuthenticatedFetch(
    fs.readFileSync(sourcePath, "utf8"),
    sourcePath,
  );
  vm.runInNewContext(
    `${implementation}\nglobalThis.__authenticatedFetch = authenticatedFetch;`,
    context,
    { filename: sourcePath },
  );

  const request = context.__authenticatedFetch;
  await request("/api/local", { method: "POST", headers: { "X-Probe": "same" } });
  await request("https://agent.example.test/api/local", { method: "DELETE" });
  await request("/api/read", { method: "GET" });
  await request("//evil.example/collect", { method: "POST", headers: { "X-Probe": "network" } });
  await request(String.raw`\\evil.example/collect`, { method: "POST" });
  await request("https://evil.example/collect", { method: "POST" });
  await request("blob:https://agent.example.test/id", { method: "POST" });
  if (sourcePath.includes("notion_static")) {
    await request("/api/notion/pages/p1/save", { method: "POST" });
    await request("/api/notion/pages", {
      method: "POST", body: JSON.stringify({ parent_id: "p1", title: "Child" }),
    });
    await request("/api/notion/pages", {
      method: "POST", body: JSON.stringify({ title: "Root" }),
    });
    await request("/api/notion/workspaces/ws1/pages/p1/duplicate", { method: "POST" });
    await request("/api/notion/workspaces/ws1/pages", {
      method: "POST", body: JSON.stringify({ parent_id: "p1", title: "Child" }),
    });
    await request("/api/notion/workspaces/ws1/pages", {
      method: "POST", body: JSON.stringify({ title: "Root" }),
    });
    await request("https://evil.example/api/notion/pages/p1/save", { method: "POST" });
    await request("/api/notion/pages/p1/deny", { method: "POST" });
    assert.equal(context.TemporaryUnlockGrants.has("p1"), false,
      "a denied write revokes the tab grant");
    context.TemporaryUnlockGrants.set("p1", {userId:"owner", workspaceId:"ws1", token:"team-tab-grant"});
    await request("/api/notion/workspaces/ws1/pages/p1/realtime-ticket", {method:"POST"});
    await request("/api/notion/workspaces/ws2/pages/p1/realtime-ticket", {method:"POST"});
    await request("/api/notion/pages/p1/save", {method:"POST"});
  }

  assert.equal(calls.length, sourcePath.includes("notion_static") ? 18 : 7);
  assert.equal(calls[0].init.headers["X-CSRF-Token"], context.State.csrfToken);
  assert.equal(calls[0].init.headers["X-Probe"], "same");
  assert.equal(calls[1].init.headers["X-CSRF-Token"], context.State.csrfToken);
  assert.equal(calls[2].init.headers["X-CSRF-Token"], undefined, "safe methods need no token");
  for (const index of [3, 4, 5, 6]) {
    assert.equal(
      calls[index].init.headers["X-CSRF-Token"],
      undefined,
      `${sourcePath}: cross-origin or non-HTTP URL at call ${index} must not receive session CSRF`,
    );
  }
  assert.equal(calls[3].init.headers["X-Probe"], "network");
  if (sourcePath.includes("notion_static")) {
    assert.equal(calls[7].init.headers["X-Notion-Temporary-Unlock"], "tab-grant");
    assert.equal(calls[8].init.headers["X-Notion-Temporary-Unlock"], "tab-grant");
    assert.equal(calls[9].init.headers["X-Notion-Temporary-Unlock"], undefined);
    assert.equal(calls[10].init.headers["X-Notion-Temporary-Unlock"], "tab-grant");
    assert.equal(calls[11].init.headers["X-Notion-Temporary-Unlock"], "tab-grant");
    assert.equal(calls[12].init.headers["X-Notion-Temporary-Unlock"], undefined,
      "a new workspace root page does not inherit an unrelated page grant");
    assert.equal(calls[13].init.headers["X-Notion-Temporary-Unlock"], undefined,
      "tab unlock grant must never be sent to another origin");
    assert.equal(calls[14].init.headers["X-Notion-Temporary-Unlock"], "tab-grant");
    assert.equal(calls[15].init.headers["X-Notion-Temporary-Unlock"], "team-tab-grant");
    assert.equal(calls[16].init.headers["X-Notion-Temporary-Unlock"], undefined,
      "a team tab grant stays inside its own workspace");
    assert.equal(calls[17].init.headers["X-Notion-Temporary-Unlock"], undefined,
      "a team tab grant cannot authorize a personal page");
    assert.equal(relockRefreshes, 1, "a denied write restores locked controls");
  }
}

(async () => {
  for (const sourcePath of sources) await verifySource(sourcePath);
  console.log("auth_csrf_frontend: ok");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

