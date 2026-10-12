/* Tab-scoped retry identity only. Canonical authorization and results stay on the server. */
(function attach(root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KnowledgeTurnRetry = api;
})(typeof globalThis === "object" ? globalThis : this, function build(root) {
  "use strict";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const SHA = /^[0-9a-f]{64}$/;
  const MAX_ENTRIES = 128;
  const MAX_BYTES = 65536;

  class RetryJournalError extends Error {
    constructor(code, message) {
      super(message);
      this.name = "RetryJournalError";
      this.code = code;
      this.retryable = true;
      this.terminal = false;
    }
  }
  function fail(code, message) { throw new RetryJournalError(code, message); }
  function uuid(value) {
    if (typeof value !== "string" || !UUID.test(value)) throw new TypeError("A canonical UUID is required.");
    return value;
  }
  function exact(value, fields) {
    return value && typeof value === "object" && !Array.isArray(value)
      && Object.keys(value).sort().join("|") === [...fields].sort().join("|");
  }

  function createJournal({ storage, ownerId, cryptoImpl = root.crypto }) {
    if (typeof ownerId !== "string" || !ownerId || ownerId.length > 200 || /[\x00-\x1f\x7f]/.test(ownerId)) {
      throw new TypeError("The authenticated journal owner is required.");
    }
    if (!storage || typeof storage.getItem !== "function" || typeof storage.setItem !== "function") {
      fail("TURN_RETRY_STORAGE_UNAVAILABLE", "요청 복구 정보를 저장할 수 없습니다. 브라우저 저장소 설정을 확인해 주세요.");
    }
    async function digest(text) {
      if (!cryptoImpl || !cryptoImpl.subtle || typeof cryptoImpl.subtle.digest !== "function") {
        fail("TURN_RETRY_STORAGE_UNAVAILABLE", "요청 복구용 해시를 계산할 수 없습니다.");
      }
      try {
        const bytes = await cryptoImpl.subtle.digest("SHA-256", new TextEncoder().encode(text));
        return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, "0")).join("");
      } catch (_) { fail("TURN_RETRY_STORAGE_UNAVAILABLE", "요청 복구용 해시를 계산하지 못했습니다."); }
    }
    async function binding(conversationId, message) {
      uuid(conversationId);
      if (typeof message !== "string" || !message) throw new TypeError("A nonempty question is required.");
      const [ownerHash, queryHash] = await Promise.all([
        digest("second-brain.turn-retry.owner.v1\n" + ownerId), digest(message),
      ]);
      return { key: "second-brain.turn-retry.v1." + ownerHash, ownerHash, queryHash, conversationId };
    }
    function read(bound) {
      let raw;
      try { raw = storage.getItem(bound.key); }
      catch (_) { fail("TURN_RETRY_STORAGE_UNAVAILABLE", "이전 요청의 복구 정보를 읽을 수 없습니다."); }
      if (raw === null) return { schema_version: 1, owner_sha256: bound.ownerHash, entries: [] };
      let value;
      try {
        if (typeof raw !== "string" || new TextEncoder().encode(raw).length > MAX_BYTES) throw Error();
        value = JSON.parse(raw);
        if (!exact(value, ["schema_version", "owner_sha256", "entries"]) || value.schema_version !== 1
            || value.owner_sha256 !== bound.ownerHash || !Array.isArray(value.entries) || value.entries.length > MAX_ENTRIES) throw Error();
        const keys = new Set();
        for (const entry of value.entries) {
          if (!exact(entry, ["conversation_id", "query_sha256", "operation_id"])) throw Error();
          uuid(entry.conversation_id); uuid(entry.operation_id);
          if (typeof entry.query_sha256 !== "string" || !SHA.test(entry.query_sha256)) throw Error();
          const key = entry.conversation_id + ":" + entry.query_sha256;
          if (keys.has(key)) throw Error();
          keys.add(key);
        }
      } catch (_) { fail("TURN_RETRY_STORAGE_INVALID", "요청 복구 정보가 손상되었습니다. 새 요청으로 자동 대체하지 않습니다."); }
      return value;
    }
    function write(bound, value) {
      const raw = JSON.stringify(value);
      if (new TextEncoder().encode(raw).length > MAX_BYTES) {
        fail("TURN_RETRY_STORAGE_FULL", "미완료 요청의 복구 공간이 가득 찼습니다. 기존 요청을 먼저 확인해 주세요.");
      }
      try {
        storage.setItem(bound.key, raw);
        if (storage.getItem(bound.key) !== raw) throw Error();
      } catch (_) { fail("TURN_RETRY_STORAGE_UNAVAILABLE", "요청 복구 정보를 보존하지 못해 전송을 진행할 수 없습니다."); }
    }
    const matches = (entry, bound) => entry.conversation_id === bound.conversationId && entry.query_sha256 === bound.queryHash;
    return Object.freeze({
      async reserve({ conversationId, message, operationIdFactory }) {
        if (typeof operationIdFactory !== "function") throw new TypeError("An operation ID factory is required.");
        const bound = await binding(conversationId, message);
        // No await between read and write: one tab's callbacks cannot lose an
        // entry through a stale read/modify/write after asynchronous hashing.
        const value = read(bound);
        const prior = value.entries.find(entry => matches(entry, bound));
        if (prior) return prior.operation_id;
        if (value.entries.length >= MAX_ENTRIES) {
          fail("TURN_RETRY_STORAGE_FULL", "미완료 요청이 많습니다. 기존 요청을 먼저 확인해 주세요.");
        }
        const operationId = uuid(operationIdFactory());
        value.entries.push({ conversation_id: conversationId, query_sha256: bound.queryHash, operation_id: operationId });
        write(bound, value); // must persist before the caller starts its HTTP request
        return operationId;
      },
      async settle({ conversationId, message, operationId }) {
        uuid(operationId);
        const bound = await binding(conversationId, message);
        const value = read(bound);
        const index = value.entries.findIndex(entry => matches(entry, bound) && entry.operation_id === operationId);
        if (index < 0) return false; // an old callback must not clear a newer turn
        value.entries.splice(index, 1);
        write(bound, value);
        return true;
      },
    });
  }
  return Object.freeze({ createJournal, RetryJournalError });
});
