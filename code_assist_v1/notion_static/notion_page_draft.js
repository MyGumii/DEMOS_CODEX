(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NotionPageDraft = api;
})(typeof globalThis === "object" ? globalThis : this, function() {
  "use strict";
  const clone = value => JSON.parse(JSON.stringify(value));
  function hasContent(data) {
    if (data?.appearancePending === true) return true;
    if (String(data?.page?.title || "").trim() || data?.page?.icon || data?.page?.cover) return true;
    return (data?.blocks || []).some(block => String(block.content || "").trim()
      || !["paragraph"].includes(block.type)
      || Object.keys(block.props || {}).some(key => !["rich_text_html"].includes(key)));
  }
  function create(context, operationId) {
    let request = null, result = null, inFlight = null, phase = "empty", error = null;
    const session = {
      context: Object.freeze({...context}),
      get phase() { return phase; },
      get error() { return error; },
      get started() { return request !== null; },
      get result() { return result; },
      commit(data, send) {
        if (result) return Promise.resolve(result);
        if (inFlight) return inFlight;
        if (!request && !hasContent(data)) return Promise.resolve(null);
        // Freeze the first meaningful input. After an uncertain response, retry
        // exactly this request; later typing is saved against the returned ID.
        if (!request) request = {operationId, context: clone(context), data: clone(data)};
        phase = "creating"; error = null;
        inFlight = Promise.resolve().then(() => send(clone(request))).then(value => {
          if (!value?.page?.id) throw new Error("생성 응답에 페이지 ID가 없습니다.");
          result = value; phase = "created";
          return result;
        }).catch(failure => { phase = "failed"; error = failure; throw failure; })
          .finally(() => { inFlight = null; });
        return inFlight;
      },
    };
    return session;
  }
  return {create, hasContent};
});
