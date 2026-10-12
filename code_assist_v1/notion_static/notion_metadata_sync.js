(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NotionMetadataSync = api;
})(typeof window !== "undefined" ? window : this, function() {
  "use strict";
  const CONTRACT = "notion-page-metadata-invalidated-v1";
  const ACTIVITY_CONTRACT = "notion-page-activity-invalidated-v1";
  const NAVIGATION_CONTRACT = "notion-page-navigation-invalidated-v1";
  function scopeKey(scope) {
    return scope?.subject_id && scope?.workspace_id ? JSON.stringify([scope.subject_id, scope.workspace_id]) : "";
  }
  function create(host) {
    const channel = host.channel, confirmed = new Map();
    let timer = null, pendingScope = "", pendingIds = new Set(), disposed = false;
    let activityTimer = null, activityRunning = false, activityScope = "";
    const activityIds = new Set(), broadcastActivityIds = new Set();
    let navigationTimer = null, navigationRunning = false, navigationPending = "";
    function scheduleNavigation() {
      const scope = scopeKey(host.scope());
      if (disposed || !scope || typeof host.refreshNavigation !== "function") return;
      navigationPending = scope;
      if (navigationTimer !== null || navigationRunning) return;
      navigationTimer = host.setTimeout(async () => {
        navigationTimer = null;
        const requestedScope = navigationPending;
        navigationPending = "";
        if (disposed || scopeKey(host.scope()) !== requestedScope) return;
        navigationRunning = true;
        try { await host.refreshNavigation(requestedScope); }
        catch (error) { if (!disposed && scopeKey(host.scope()) === requestedScope) host.onError?.(error); }
        finally {
          navigationRunning = false;
          if (navigationPending) scheduleNavigation();
        }
      }, 180);
    }
    function armActivity() {
      if (disposed || activityTimer !== null || activityRunning || !activityIds.size) return;
      // Throttle rather than restart a debounce on every keystroke: a sustained
      // edit still refreshes, with at most one activity request batch in flight.
      activityTimer = host.setTimeout(async () => {
        activityTimer = null;
        const scope = activityScope, ids = [...activityIds], broadcasts = [...broadcastActivityIds];
        activityIds.clear(); broadcastActivityIds.clear();
        if (disposed || scopeKey(host.scope()) !== scope) return;
        activityRunning = true;
        try {
          const current = host.scope();
          for (const id of broadcasts) {
            try { channel?.postMessage({contract: ACTIVITY_CONTRACT, kind: "activity", subject_id: current.subject_id,
              workspace_id: current.workspace_id, page_id: id}); }
            catch (error) { host.onError?.(error); }
          }
          await host.refreshActivity(ids, scope);
        } catch (error) {
          if (!disposed && scopeKey(host.scope()) === scope) host.onError?.(error);
        } finally {
          activityRunning = false;
          armActivity();
        }
      }, 750);
    }
    function scheduleActivity(ids, broadcast = false) {
      const scope = scopeKey(host.scope());
      if (disposed || !scope || typeof host.refreshActivity !== "function") return;
      if (activityScope !== scope) { activityIds.clear(); broadcastActivityIds.clear(); }
      activityScope = scope;
      for (const id of ids) {
        if (typeof id !== "string" || !id || id.length > 200 || !host.hasPage(id)) continue;
        activityIds.add(id);
        if (broadcast) broadcastActivityIds.add(id);
      }
      armActivity();
    }
    function schedule(ids) {
      const scope = scopeKey(host.scope());
      if (!scope || disposed) return;
      if (pendingScope !== scope) pendingIds.clear();
      pendingScope = scope;
      ids.filter(id => typeof id === "string" && id.length <= 200 && host.hasPage(id)).forEach(id => pendingIds.add(id));
      if (!pendingIds.size) return;
      host.clearTimeout(timer);
      timer = host.setTimeout(async () => {
        timer = null;
        const ids = [...pendingIds]; pendingIds.clear();
        if (disposed || scopeKey(host.scope()) !== scope) return;
        try { await host.refresh(ids, scope); } catch (error) {
          if (!disposed && scopeKey(host.scope()) === scope) host.onError?.(error);
        }
      }, 180);
    }
    if (channel) channel.onmessage = event => {
      const message = event.data;
      if (scopeKey(message) !== scopeKey(host.scope())) return;
      if (message?.contract === NAVIGATION_CONTRACT) scheduleNavigation();
      else if (message?.contract === ACTIVITY_CONTRACT && message.kind === "activity") scheduleActivity([message.page_id]);
      else if (message?.contract === CONTRACT && message.kind == null) schedule([message.page_id]);
    };
    return {
      navigationChanged(scope) {
        if (disposed || !scopeKey(scope) || scopeKey(scope) !== scopeKey(host.scope())) return;
        // Receivers may not know the new page ID. They fetch an authorized
        // current list; neither page contents nor a private page ID are sent.
        try { channel?.postMessage({contract: NAVIGATION_CONTRACT, subject_id: scope.subject_id, workspace_id: scope.workspace_id}); }
        catch (error) { host.onError?.(error); }
      },
      refreshNavigation: scheduleNavigation,
      activity(scope, pageId) {
        if (scopeKey(scope) && scopeKey(scope) === scopeKey(host.scope())) scheduleActivity([pageId], true);
      },
      invalidate(ids) {
        const scope = host.scope();
        if (disposed || !scopeKey(scope)) return;
        for (const pageId of new Set(ids || [])) {
          if (typeof pageId !== "string" || !pageId || pageId.length > 200) continue;
          try { channel?.postMessage({contract:CONTRACT,subject_id:scope.subject_id,workspace_id:scope.workspace_id,page_id:pageId}); }
          catch(error) { host.onError?.(error); }
        }
      },
      saved(scope, pageId, title) {
        if (disposed || !scopeKey(scope) || scopeKey(scope) !== scopeKey(host.scope()) || typeof title !== "string"
            || typeof pageId !== "string" || !pageId || pageId.length > 200) return;
        const key = JSON.stringify([scopeKey(scope), pageId]);
        if (confirmed.get(key) === title) return;
        confirmed.delete(key); confirmed.set(key, title);
        while (confirmed.size > 256) confirmed.delete(confirmed.keys().next().value);
        try {
          channel?.postMessage({contract: CONTRACT, subject_id: scope.subject_id,
            workspace_id: scope.workspace_id, page_id: pageId});
        } catch (error) { host.onError?.(error); }
      },
      refresh: schedule,
      dispose() {
        disposed = true; host.clearTimeout(timer); host.clearTimeout(activityTimer); host.clearTimeout(navigationTimer);
        navigationPending = "";
        pendingIds.clear(); activityIds.clear(); broadcastActivityIds.clear(); channel?.close();
      },
    };
  }
  return {create, scopeKey};
});
