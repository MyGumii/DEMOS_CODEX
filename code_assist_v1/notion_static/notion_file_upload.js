(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionFileUpload = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CONTRACT = "code_assist.notion.browser_resumable_file_upload.v1";
  const HASH_CHUNK_SIZE = 4 * 1024 * 1024;
  const UPLOAD_CHUNK_SIZE = 1024 * 1024;
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function rotateRight(value, count) {
    return (value >>> count) | (value << (32 - count));
  }

  class Sha256 {
    constructor() {
      this.state = new Uint32Array([
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
      ]);
      this.buffer = new Uint8Array(64);
      this.bufferLength = 0;
      this.byteLength = 0n;
      this.finished = false;
    }

    _transform(block, offset = 0) {
      const words = new Uint32Array(64);
      for (let index = 0; index < 16; index += 1) {
        const cursor = offset + index * 4;
        words[index] = (
          (block[cursor] << 24)
          | (block[cursor + 1] << 16)
          | (block[cursor + 2] << 8)
          | block[cursor + 3]
        ) >>> 0;
      }
      for (let index = 16; index < 64; index += 1) {
        const x = words[index - 15];
        const y = words[index - 2];
        const sigma0 = rotateRight(x, 7) ^ rotateRight(x, 18) ^ (x >>> 3);
        const sigma1 = rotateRight(y, 17) ^ rotateRight(y, 19) ^ (y >>> 10);
        words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = this.state;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        const choose = (e & f) ^ (~e & g);
        const first = (h + sum1 + choose + K[index] + words[index]) >>> 0;
        const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const second = (sum0 + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + first) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (first + second) >>> 0;
      }
      this.state[0] = (this.state[0] + a) >>> 0;
      this.state[1] = (this.state[1] + b) >>> 0;
      this.state[2] = (this.state[2] + c) >>> 0;
      this.state[3] = (this.state[3] + d) >>> 0;
      this.state[4] = (this.state[4] + e) >>> 0;
      this.state[5] = (this.state[5] + f) >>> 0;
      this.state[6] = (this.state[6] + g) >>> 0;
      this.state[7] = (this.state[7] + h) >>> 0;
    }

    update(value) {
      if (this.finished) throw new Error("SHA-256 digest is already finalized");
      const bytes = value instanceof Uint8Array ? value : new Uint8Array(value || []);
      this.byteLength += BigInt(bytes.byteLength);
      let offset = 0;
      if (this.bufferLength) {
        const take = Math.min(64 - this.bufferLength, bytes.byteLength);
        this.buffer.set(bytes.subarray(0, take), this.bufferLength);
        this.bufferLength += take;
        offset += take;
        if (this.bufferLength === 64) {
          this._transform(this.buffer);
          this.bufferLength = 0;
        }
      }
      while (offset + 64 <= bytes.byteLength) {
        this._transform(bytes, offset);
        offset += 64;
      }
      if (offset < bytes.byteLength) {
        this.buffer.set(bytes.subarray(offset), 0);
        this.bufferLength = bytes.byteLength - offset;
      }
      return this;
    }

    hex() {
      if (this.finished) throw new Error("SHA-256 digest is already finalized");
      this.finished = true;
      const remainder = this.bufferLength;
      const finalLength = remainder < 56 ? 64 : 128;
      const finalBlock = new Uint8Array(finalLength);
      finalBlock.set(this.buffer.subarray(0, remainder));
      finalBlock[remainder] = 0x80;
      let bitLength = this.byteLength * 8n;
      for (let index = 0; index < 8; index += 1) {
        finalBlock[finalLength - 1 - index] = Number(bitLength & 0xffn);
        bitLength >>= 8n;
      }
      for (let offset = 0; offset < finalLength; offset += 64) {
        this._transform(finalBlock, offset);
      }
      return [...this.state]
        .map(value => value.toString(16).padStart(8, "0"))
        .join("");
    }
  }

  function operationId(prefix) {
    const id = globalThis.crypto?.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${id}`.slice(0, 160);
  }

  async function hashFile(file, options = {}) {
    const chunkSize = Number(options.chunkSize || HASH_CHUNK_SIZE);
    const digest = new Sha256();
    for (let offset = 0; offset < file.size; offset += chunkSize) {
      const bytes = new Uint8Array(await file.slice(offset, offset + chunkSize).arrayBuffer());
      digest.update(bytes);
      options.onProgress?.({ phase: "hashing", loaded: offset + bytes.byteLength, total: file.size });
      await Promise.resolve();
    }
    if (file.size === 0) digest.update(new Uint8Array());
    return `sha256:${digest.hex()}`;
  }

  async function responseJson(response) {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.message || payload.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.data = payload;
      throw error;
    }
    return payload;
  }

  async function request(fetchImpl, csrfToken, url, init = {}, retries = 0) {
    let lastError;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        const headers = { ...(init.headers || {}) };
        if (csrfToken && !["GET", "HEAD"].includes(String(init.method || "GET").toUpperCase())) {
          headers["X-CSRF-Token"] = csrfToken;
        }
        return await responseJson(await fetchImpl(url, { ...init, headers }));
      } catch (error) {
        lastError = error;
        if (error.status && error.status < 500) break;
      }
    }
    throw lastError;
  }

  function storageKey(subjectId, workspaceId, pageId, fingerprint) {
    return `notion:file-upload:v1:${encodeURIComponent(subjectId)}:${encodeURIComponent(workspaceId)}:${encodeURIComponent(pageId)}:${fingerprint}`;
  }

  async function upload(options) {
    const file = options.file;
    if (!file || typeof file.slice !== "function") throw new TypeError("file is required");
    const fetchImpl = options.fetch || globalThis.fetch.bind(globalThis);
    const csrfToken = String(options.csrfToken || "");
    const baseUrl = String(options.baseUrl || "").replace(/\/$/, "");
    const expectedSha256 = await hashFile(file, { onProgress: options.onProgress });
    const fingerprint = `${file.name}:${file.size}:${file.lastModified || 0}:${expectedSha256}`;
    const store = options.storage || globalThis.sessionStorage;
    const key = storageKey(options.subjectId || "", options.workspaceId || "", options.pageId || "", fingerprint);
    let record = null;
    try { record = JSON.parse(store?.getItem(key) || "null"); } catch {}
    let session = null;
    if (record?.session_id) {
      try {
        const status = await request(fetchImpl, csrfToken, `${baseUrl}/uploads/${encodeURIComponent(record.session_id)}`, { method: "GET" });
        if (["prepared", "ready"].includes(status.state)) session = { ...record, ...status };
      } catch {}
    }
    if (!session) {
      const beginOperationId = operationId("file-begin");
      session = await request(fetchImpl, csrfToken, `${baseUrl}/uploads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          original_name: file.name || "file",
          mime_type: file.type || "application/octet-stream",
          total_size: file.size,
          expected_sha256: expectedSha256,
          expected_document_sequence: options.documentSequence,
          expected_workspace_epoch: options.workspaceEpoch,
          expected_document_epoch: options.documentEpoch,
          operation_id: beginOperationId,
          chunk_size: UPLOAD_CHUNK_SIZE,
        }),
      });
      record = {
        session_id: session.session_id,
        chunk_size: session.chunk_size,
        begin_operation_id: beginOperationId,
        finalize_operation_id: operationId("file-finalize"),
      };
      try { store?.setItem(key, JSON.stringify(record)); } catch {}
    }
    try {
      const status = await request(fetchImpl, csrfToken, `${baseUrl}/uploads/${encodeURIComponent(session.session_id)}`, { method: "GET" });
      const missing = Array.isArray(status.missing_chunks) ? status.missing_chunks : [];
      for (const index of missing) {
        if (options.signal?.aborted) throw new DOMException("Upload canceled", "AbortError");
        const start = index * session.chunk_size;
        const end = Math.min(file.size, start + session.chunk_size);
        const body = file.slice(start, end);
        await request(
          fetchImpl,
          csrfToken,
          `${baseUrl}/uploads/${encodeURIComponent(session.session_id)}/chunks/${index}`,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-Notion-Operation-Id": `file-chunk-${session.session_id}-${index}`,
            },
            body,
            signal: options.signal,
          },
          2,
        );
        options.onProgress?.({ phase: "uploading", loaded: end, total: file.size });
      }
      const result = await request(fetchImpl, csrfToken, `${baseUrl}/uploads/${encodeURIComponent(session.session_id)}/finalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation_id: record.finalize_operation_id }),
      }, 2);
      try { store?.removeItem(key); } catch {}
      options.onProgress?.({ phase: "complete", loaded: file.size, total: file.size });
      return result;
    } catch (error) {
      if (error?.name === "AbortError") {
        try {
          await request(fetchImpl, csrfToken, `${baseUrl}/uploads/${encodeURIComponent(session.session_id)}/cancel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ operation_id: operationId("file-cancel") }),
          });
          store?.removeItem(key);
        } catch {}
      }
      throw error;
    }
  }

  return Object.freeze({ CONTRACT, HASH_CHUNK_SIZE, Sha256, hashFile, upload });
});
