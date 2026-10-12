(function (root) {
  "use strict";
  function failure(data, fallback, status) {
    const error = new Error(data?.message || fallback);
    error.status = data?.status || status;
    error.code = data?.code || "";
    return error;
  }
  async function send({fetch, path, body, signal, onEvent}) {
    const response = await fetch(path, {method: "POST", signal,
      headers: {"Content-Type": "application/json", "Accept": "text/event-stream"},
      body: JSON.stringify(body)});
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw failure(data, "AI 응답을 요청하지 못했습니다.", response.status);
    }
    if (!(response.headers.get("Content-Type") || "").includes("text/event-stream")) {
      throw failure(null, "스트리밍 연결 형식을 확인하지 못했습니다.", 503);
    }
    if (!response.body) throw failure(null, "스트리밍 연결을 열지 못했습니다.", 503);
    const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", {fatal: true});
    let buffer = "", frame = [], result = null;
    function line(value) {
      value = value.replace(/\r$/, "");
      if (value.startsWith("data:")) frame.push(value.slice(5).replace(/^ /, ""));
      if (value || !frame.length) return;
      const event = JSON.parse(frame.join("\n")); frame = [];
      if (event.type === "error") throw failure(event, "AI 응답 생성이 중단되었습니다.", 503);
      if (event.type === "done") {
        if (!event.conversation?.id || !Array.isArray(event.conversation.messages)) {
          throw failure(null, "저장된 응답을 확인하지 못했습니다.", 503);
        }
        result = event.conversation;
      }
      onEvent?.(event);
    }
    try {
      while (!result) {
        const {value, done} = await reader.read();
        buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
        // The encrypted record allows 4 MB of UTF-8; SSE escapes Unicode and
        // returns its history too, so its terminal frame can be larger.
        if (buffer.length > 16777216) throw failure(null, "응답이 너무 큽니다.");
        let end;
        while ((end = buffer.indexOf("\n")) !== -1) {
          const next = buffer.slice(0, end); buffer = buffer.slice(end + 1); line(next);
          if (result) break;
        }
        if (done) break;
      }
      // The server might have committed just before a network disconnect.
      // Keep the operation ID so the next request can replay that result.
      if (!result) throw failure(null, "응답 연결이 끝나기 전에 끊겼습니다. 다시 요청하세요.");
      return result;
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  const api = {send};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.NotionAiStream = api;
})(typeof window === "undefined" ? globalThis : window);
