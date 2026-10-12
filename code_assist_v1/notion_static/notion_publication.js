/* Fail-closed P4.5 Notion publication entry selection. */
(function notionPublicationBridgeModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NotionPublicationBridge = api;
})(typeof globalThis === "object" ? globalThis : this, function buildNotionPublicationBridge() {
  "use strict";

  async function publish({ pageId, probePublication, openCanonical, openLegacy }) {
    if (typeof pageId !== "string" || !pageId) throw new TypeError("pageId is required.");
    if (typeof probePublication !== "function" || typeof openCanonical !== "function" || typeof openLegacy !== "function") {
      throw new TypeError("Notion publication bridge dependencies are required.");
    }
    // Only an authoritative 404/absent result (normalized to false by the
    // canonical transport) may enter the legacy compatibility path.  A probe
    // error or an unknown canonical mutation outcome must propagate so a
    // second write is never attempted.
    if (!await probePublication()) return openLegacy(pageId);
    return openCanonical(pageId);
  }

  return Object.freeze({ publish });
});
