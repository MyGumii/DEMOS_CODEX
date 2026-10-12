(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionCollaborationEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CONTRACT = "code_assist.notion.collaboration_editor_bridge.v1";
  const OUTBOX_CONTRACT = "code_assist.notion.encrypted_browser_outbox.v1";
  const OUTBOX_DB = "code-assist-notion-collaboration-v1";
  const OUTBOX_VERSION = 1;
  const OUTBOX_STORE = "outbox";
  const KEY_STORE = "keys";
  const LOCAL_ORIGIN = Object.freeze({ source: "notion-editor-host" });
  const SEED_ORIGIN = Object.freeze({ source: "canonical-seed" });
  const REMOTE_ORIGIN = Object.freeze({ source: "remote-replay" });
  const PENDING_ORIGIN = Object.freeze({ source: "offline-outbox" });
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function clone(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function bytesToBase64(value) {
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value || []);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    if (typeof btoa === "function") return btoa(binary);
    if (typeof Buffer !== "undefined") return Buffer.from(bytes).toString("base64");
    throw new Error("base64 encoder is unavailable");
  }

  function base64ToBytes(value) {
    const text = String(value || "");
    if (typeof atob === "function") {
      const binary = atob(text);
      return Uint8Array.from(binary, character => character.charCodeAt(0));
    }
    if (typeof Buffer !== "undefined") return new Uint8Array(Buffer.from(text, "base64"));
    throw new Error("base64 decoder is unavailable");
  }

  function stableJson(value) {
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }

  function validIdentifier(value, field) {
    const text = String(value || "").trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(text)) {
      throw new TypeError(`${field} is invalid`);
    }
    return text;
  }

  function normalizeBlock(block) {
    const value = block && typeof block === "object" ? block : {};
    return {
      id: validIdentifier(value.id, "block id"),
      type: String(value.type || "paragraph").slice(0, 100),
      content: String(value.content || ""),
      props: clone(value.props && typeof value.props === "object" ? value.props : {}),
    };
  }

  function normalizeSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") throw new TypeError("editor snapshot is required");
    const blocks = (Array.isArray(snapshot.blocks) ? snapshot.blocks : []).map(normalizeBlock);
    if (new Set(blocks.map(block => block.id)).size !== blocks.length) {
      throw new TypeError("editor snapshot contains duplicate block ids");
    }
    return {
      page_id: validIdentifier(snapshot.page_id, "page id"),
      title: String(snapshot.title || ""),
      blocks,
      can_edit: snapshot.can_edit !== false,
      server_revision: snapshot.server_revision ?? null,
      edit_revision: Number.isInteger(snapshot.edit_revision) ? snapshot.edit_revision : 0,
    };
  }

  function commonTextEdges(previous, next) {
    const left = String(previous || "");
    const right = String(next || "");
    let prefix = 0;
    while (prefix < left.length && prefix < right.length && left[prefix] === right[prefix]) prefix += 1;
    const splitsSurrogate = (text, index) => index > 0 && index < text.length
      && text.charCodeAt(index - 1) >= 0xD800 && text.charCodeAt(index - 1) <= 0xDBFF
      && text.charCodeAt(index) >= 0xDC00 && text.charCodeAt(index) <= 0xDFFF;
    // Y.Text edits use UTF-16 offsets. Never delete/insert half an emoji.
    if (splitsSurrogate(left, prefix) || splitsSurrogate(right, prefix)) prefix -= 1;
    let suffix = 0;
    while (
      suffix < left.length - prefix
      && suffix < right.length - prefix
      && left[left.length - 1 - suffix] === right[right.length - 1 - suffix]
    ) suffix += 1;
    if (splitsSurrogate(left, left.length - suffix) || splitsSurrogate(right, right.length - suffix)) suffix -= 1;
    return { prefix, suffix };
  }

  function replaceYText(ytext, value) {
    const next = String(value || "");
    const current = ytext.toString();
    if (current === next) return false;
    const { prefix, suffix } = commonTextEdges(current, next);
    const remove = current.length - prefix - suffix;
    const insert = next.slice(prefix, next.length - suffix);
    if (remove > 0) ytext.delete(prefix, remove);
    if (insert) ytext.insert(prefix, insert);
    return true;
  }

  function replaceYArray(yarray, values) {
    const next = values.map(String);
    const current = yarray.toArray().map(String);
    if (current.length === next.length && current.every((value, index) => value === next[index])) return false;
    if (current.length) yarray.delete(0, current.length);
    if (next.length) yarray.insert(0, next);
    return true;
  }

  function stringHash32(value) {
    const text = String(value || "");
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0) || 1;
  }

  function blockMap(Y, blocks, block, create = true) {
    let item = blocks.get(block.id);
    if (!item && create) {
      item = new Y.Map();
      item.set("content", new Y.Text());
      blocks.set(block.id, item);
    }
    return item || null;
  }

  function writeSnapshot(Y, document, snapshot) {
    const title = document.getText("page-title");
    const order = document.getArray("block-order");
    const blocks = document.getMap("blocks");
    replaceYText(title, snapshot.title);
    replaceYArray(order, snapshot.blocks.map(block => block.id));
    const wanted = new Set(snapshot.blocks.map(block => block.id));
    for (const id of [...blocks.keys()]) {
      if (!wanted.has(String(id))) blocks.delete(id);
    }
    for (const block of snapshot.blocks) {
      const item = blockMap(Y, blocks, block, true);
      item.set("type", block.type);
      item.set("props", stableJson(block.props));
      let content = item.get("content");
      if (!(content instanceof Y.Text)) {
        content = new Y.Text();
        item.set("content", content);
      }
      replaceYText(content, block.content);
    }
  }

  function readProjection(Y, document, context) {
    // Inspect presence before getText() creates an empty named root. A replay
    // that intentionally deleted its title still contains that root.
    const title = document.share.has("page-title")
      ? document.getText("page-title").toString() : context.baseline_title;
    const order = document.getArray("block-order").toArray().map(String);
    const blocks = document.getMap("blocks");
    const seen = new Set();
    const projected = [];
    for (const id of order) {
      if (seen.has(id)) continue;
      seen.add(id);
      const item = blocks.get(id);
      if (!(item instanceof Y.Map)) continue;
      let props = {};
      try { props = JSON.parse(String(item.get("props") || "{}")); } catch {}
      const content = item.get("content");
      projected.push({
        id,
        type: String(item.get("type") || "paragraph"),
        content: content instanceof Y.Text ? content.toString() : String(content || ""),
        props: props && typeof props === "object" ? props : {},
      });
    }
    return {
      page_id: context.page_id,
      title,
      blocks: projected,
      server_revision: context.server_revision ?? null,
    };
  }

  function structureCommands(previous, next) {
    const before = (previous?.blocks || []).map(normalizeBlock);
    const after = (next?.blocks || []).map(normalizeBlock);
    const beforeIds = before.map(block => block.id);
    const afterIds = after.map(block => block.id);
    const beforeSet = new Set(beforeIds);
    const afterSet = new Set(afterIds);
    const commands = [];
    for (const id of beforeIds) {
      if (!afterSet.has(id)) commands.push({ command_kind: "block.delete", target_id: id, payload: {} });
    }
    after.forEach((block, index) => {
      if (!beforeSet.has(block.id)) {
        commands.push({
          command_kind: "block.insert",
          target_id: block.id,
          payload: {
            after_block_id: index > 0 ? after[index - 1].id : null,
            type: block.type,
            content: block.content,
            props: clone(block.props),
          },
        });
      }
    });
    const working = beforeIds.filter(id => afterSet.has(id));
    afterIds.forEach((id, index) => {
      if (!beforeSet.has(id)) {
        working.splice(index, 0, id);
        return;
      }
      const current = working.indexOf(id);
      if (current === index) return;
      working.splice(current, 1);
      working.splice(index, 0, id);
      commands.push({
        command_kind: "block.move",
        target_id: id,
        payload: { after_block_id: index > 0 ? afterIds[index - 1] : null },
      });
    });
    return commands;
  }

  function createEditorBridge(options = {}) {
    const Y = options.Y || (typeof globalThis !== "undefined" ? globalThis.NotionYjs : null);
    const host = options.host;
    if (!Y?.Doc || !Y?.UndoManager || typeof Y.applyUpdate !== "function") {
      throw new TypeError("pinned Yjs browser runtime is required");
    }
    if (!host || typeof host.readDocument !== "function" || typeof host.applyRemoteProjection !== "function") {
      throw new TypeError("Notion editor host adapter is required");
    }
    const listeners = new Set();
    let document = null;
    let undoManager = null;
    let context = null;
    let baseline = null;
    let ready = false;
    let disposed = false;
    let sawCrdtFrame = false;
    let pendingSeedUpdate = null;
    let applyingProjection = false;
    let pendingStructureCommands = [];
    let historyProjection = null;

    const unsubscribeHost = host.subscribe(event => {
      if (disposed) return;
      if (event.type === "document-unloaded") {
        closeDocument();
      } else if (event.type === "local-mutation" && ready && !applyingProjection) {
        syncLocalSnapshot(host.readDocument());
      }
    });

    function emit(detail) {
      const event = Object.freeze({ contract: CONTRACT, ...detail });
      for (const listener of [...listeners]) listener(event);
    }

    function closeDocument() {
      if (undoManager) undoManager.destroy();
      if (document) document.destroy();
      document = null;
      undoManager = null;
      context = null;
      baseline = null;
      ready = false;
      sawCrdtFrame = false;
      pendingSeedUpdate = null;
      applyingProjection = false;
      pendingStructureCommands = [];
      historyProjection = null;
    }

    function configureDocument(snapshot) {
      closeDocument();
      baseline = normalizeSnapshot(snapshot);
      context = {
        page_id: baseline.page_id,
        server_revision: baseline.server_revision,
        baseline_title: baseline.title,
      };
      document = new Y.Doc();
      const trackedOrigins = new Set([LOCAL_ORIGIN]);
      undoManager = new Y.UndoManager(
        [document.getArray("block-order"), document.getMap("blocks")],
        { trackedOrigins, captureTimeout: 350 },
      );
      document.on("update", (update, origin) => {
        if (origin === SEED_ORIGIN) {
          // A transaction delta may reference root structs created before the
          // seed transaction. Publish a self-contained state for a fresh page.
          pendingSeedUpdate = new Uint8Array(Y.encodeStateAsUpdate(document));
          return;
        }
        if (!ready || origin === REMOTE_ORIGIN || origin === PENDING_ORIGIN) return;
        if (origin === LOCAL_ORIGIN || origin === undoManager) {
          const snapshot = readProjection(Y, document, context);
          const commands = origin === undoManager && historyProjection && options.structureHistory?.() !== false
            ? structureCommands(historyProjection, snapshot).map(command => command.command_kind === "block.insert"
              ? {...command, payload: {...command.payload, restore_deleted: true}} : command)
            : pendingStructureCommands;
          emit({
            type: "local-update",
            page_id: context.page_id,
            update: new Uint8Array(update),
            snapshot,
            structure_commands: clone(commands),
          });
        }
      });
      emit({ type: "configured", page_id: context.page_id });
      return descriptor();
    }

    function applyFrames(frames) {
      if (!document || !context) return { applied: 0, operation_ids: [] };
      let applied = 0;
      const operationIds = [];
      for (const frame of frames || []) {
        if (!frame || !["crdt.snapshot", "crdt.update"].includes(frame.kind)) continue;
        const bytes = frame.payload instanceof Uint8Array
          ? frame.payload
          : base64ToBytes(frame.payload_base64 || "");
        Y.applyUpdate(document, bytes, REMOTE_ORIGIN);
        sawCrdtFrame = true;
        applied += 1;
        if (frame.operation_id) operationIds.push(String(frame.operation_id));
      }
      if (applied) pendingSeedUpdate = null;
      return { applied, operation_ids: operationIds };
    }

    function applyPendingUpdates(entries) {
      if (!document) return 0;
      let applied = 0;
      for (const entry of entries || []) {
        const payload = entry?.payload instanceof Uint8Array
          ? entry.payload
          : base64ToBytes(entry?.payload_base64 || "");
        if (!payload.length) continue;
        Y.applyUpdate(document, payload, PENDING_ORIGIN);
        applied += 1;
      }
      return applied;
    }

    function project(origin) {
      if (!document || !context) return false;
      applyingProjection = true;
      try {
        const result = host.applyRemoteProjection(readProjection(Y, document, context), {
          origin,
          preserve_selection: true,
        });
        return result?.applied !== false;
      } finally {
        applyingProjection = false;
      }
    }

    function finishReplay(pendingEntries = []) {
      if (!document || !baseline || !context) throw new Error("editor bridge is not configured");
      if (!sawCrdtFrame) {
        const randomClientId = document.clientID;
        document.clientID = stringHash32(`code-assist:${context.page_id}:generation-1`);
        document.transact(() => writeSnapshot(Y, document, baseline), SEED_ORIGIN);
        document.clientID = randomClientId;
      }
      const recovered = applyPendingUpdates(pendingEntries);
      if (!document.share.has("page-title")) {
        // Older body-only states have no title root. Seed only that field after
        // pending offline intent has replayed. All clients use the same identity
        // so opening the same page concurrently cannot duplicate the title.
        const randomClientId = document.clientID;
        document.clientID = stringHash32(`code-assist:${context.page_id}:generation-1:page-title`);
        document.transact(() => replaceYText(document.getText("page-title"), baseline.title), SEED_ORIGIN);
        document.clientID = randomClientId;
      }
      undoManager.addToScope(document.getText("page-title"));
      ready = true;
      project(recovered ? "offline-recovery" : "collaboration-bootstrap");
      if (pendingSeedUpdate && options.canPublishSeed?.() !== false) {
        const update = pendingSeedUpdate;
        pendingSeedUpdate = null;
        sawCrdtFrame = true;
        emit({
          type: "local-update",
          page_id: context.page_id,
          update,
          snapshot: readProjection(Y, document, context),
          seed: true,
          structure_commands: [],
        });
      }
      emit({ type: "ready", page_id: context.page_id, recovered_updates: recovered });
      return descriptor();
    }

    function syncLocalSnapshot(value) {
      if (!ready || !document || !context) return { changed: false, commands: [] };
      const next = normalizeSnapshot(value);
      if (next.page_id !== context.page_id) return { changed: false, commands: [] };
      const previous = readProjection(Y, document, context);
      const commands = structureCommands(previous, next);
      const before = Y.encodeStateVector(document);
      pendingStructureCommands = commands;
      try {
        document.transact(() => writeSnapshot(Y, document, next), LOCAL_ORIGIN);
      } finally {
        pendingStructureCommands = [];
      }
      const delta = Y.encodeStateAsUpdate(document, before);
      return { changed: delta.length > 2, commands };
    }

    function applyRemoteAndProject(frames) {
      const result = applyFrames(frames);
      if (result.applied) project("collaboration");
      return result;
    }

    function undo() {
      if (!ready || !undoManager?.undoStack?.length) return false;
      historyProjection = readProjection(Y, document, context);
      try { undoManager.undo(); } finally { historyProjection = null; }
      project("local-undo");
      return true;
    }

    function redo() {
      if (!ready || !undoManager?.redoStack?.length) return false;
      historyProjection = readProjection(Y, document, context);
      try { undoManager.redo(); } finally { historyProjection = null; }
      project("local-redo");
      return true;
    }

    function descriptor() {
      return Object.freeze({
        contract: CONTRACT,
        page_id: context?.page_id || "",
        configured: Boolean(document),
        ready,
        saw_crdt_frame: sawCrdtFrame,
        can_undo: Boolean(undoManager?.undoStack?.length),
        can_redo: Boolean(undoManager?.redoStack?.length),
      });
    }

    return Object.freeze({
      contract: CONTRACT,
      configureDocument,
      applyFrames,
      applyRemoteAndProject,
      applyPendingUpdates,
      finishReplay,
      syncLocalSnapshot,
      stopCapturing() { undoManager?.stopCapturing(); },
      projection() {
        return document && context ? clone(readProjection(Y, document, context)) : null;
      },
      encodeState() {
        return document ? new Uint8Array(Y.encodeStateAsUpdate(document)) : new Uint8Array();
      },
      undo,
      redo,
      descriptor,
      subscribe(listener) {
        if (typeof listener !== "function") throw new TypeError("bridge listener must be a function");
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      closeDocument,
      dispose() {
        if (disposed) return;
        disposed = true;
        unsubscribeHost();
        listeners.clear();
        closeDocument();
      },
    });
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
      transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed"));
    });
  }

  function openOutboxDatabase(indexedDB) {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(OUTBOX_DB, OUTBOX_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(KEY_STORE)) {
          database.createObjectStore(KEY_STORE, { keyPath: "id" });
        }
        if (!database.objectStoreNames.contains(OUTBOX_STORE)) {
          const store = database.createObjectStore(OUTBOX_STORE, { keyPath: "id" });
          store.createIndex("scope", "scope", { unique: false });
          store.createIndex("subject", "subject", { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
      request.onblocked = () => reject(new Error("IndexedDB upgrade is blocked"));
    });
  }

  async function digestText(cryptoApi, value) {
    const digest = await cryptoApi.subtle.digest("SHA-256", encoder.encode(String(value)));
    return `sha256:${[...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
  }

  function outboxScope(context) {
    return {
      realm: String(context.realm || ""),
      subject_id: validIdentifier(context.subject_id, "subject id"),
      workspace_id: validIdentifier(context.workspace_id, "workspace id"),
      page_id: validIdentifier(context.page_id, "page id"),
    };
  }

  function createEncryptedOutbox(options = {}) {
    const indexedDB = options.indexedDB || (typeof globalThis !== "undefined" ? globalThis.indexedDB : null);
    const cryptoApi = options.crypto || (typeof globalThis !== "undefined" ? globalThis.crypto : null);
    if (!indexedDB || !cryptoApi?.subtle || typeof cryptoApi.getRandomValues !== "function") {
      throw new TypeError("IndexedDB and Web Crypto are required for durable offline editing");
    }
    let databasePromise = null;
    const database = () => (databasePromise ||= openOutboxDatabase(indexedDB));

    async function keysFor(context) {
      const scope = outboxScope(context);
      const subject = await digestText(cryptoApi, `${scope.realm}\n${scope.subject_id}`);
      const workspace = await digestText(cryptoApi, `${scope.realm}\n${scope.subject_id}\n${scope.workspace_id}`);
      const page = await digestText(cryptoApi, `${scope.realm}\n${scope.subject_id}\n${scope.workspace_id}\n${scope.page_id}`);
      return { ...scope, subject, workspace, page };
    }

    async function encryptionKey(keys) {
      const db = await database();
      let transaction = db.transaction(KEY_STORE, "readwrite");
      let done = transactionDone(transaction);
      let record = await requestResult(transaction.objectStore(KEY_STORE).get(keys.subject));
      const newToken = () => bytesToBase64(cryptoApi.getRandomValues(new Uint8Array(24)));
      if (record?.key && !record.token) {
        record = {...record, token:newToken()};
        transaction.objectStore(KEY_STORE).put(record);
      }
      await done;
      if (record?.key) return record;
      const key = await cryptoApi.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"],
      );
      transaction = db.transaction(KEY_STORE, "readwrite");
      record = {id:keys.subject, key, token:newToken(), created_at:Date.now()};
      done = transactionDone(transaction);
      transaction.objectStore(KEY_STORE).add(record);
      try {
        await done;
        return record;
      } catch {
        transaction = db.transaction(KEY_STORE, "readonly");
        record = await requestResult(transaction.objectStore(KEY_STORE).get(keys.subject));
        await transactionDone(transaction);
        if (!record?.key) throw new Error("offline encryption key race could not be resolved");
        return record;
      }
    }

    async function put(context, envelope) {
      const keys = await keysFor(context);
      const operationId = validIdentifier(envelope.operation_id, "operation id");
      const keyRecord = await encryptionKey(keys);
      const iv = cryptoApi.getRandomValues(new Uint8Array(12));
      const aad = encoder.encode(`${OUTBOX_CONTRACT}\n${keys.page}`);
      const plaintext = encoder.encode(stableJson({
        ...clone(envelope),
        contract: OUTBOX_CONTRACT,
        operation_id: operationId,
        page_id: keys.page_id,
        workspace_id: keys.workspace_id,
        subject_id: keys.subject_id,
      }));
      const ciphertext = await cryptoApi.subtle.encrypt({ name: "AES-GCM", iv, additionalData: aad }, keyRecord.key, plaintext);
      const record = {
        id: `${keys.page}:${operationId}`,
        scope: keys.page,
        subject: keys.subject,
        workspace: keys.workspace,
        iv: bytesToBase64(iv),
        ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
        created_at: Number(envelope.created_at || Date.now()),
      };
      const db = await database();
      const transaction = db.transaction([OUTBOX_STORE, KEY_STORE], "readwrite");
      const done = transactionDone(transaction);
      const currentKey = await requestResult(transaction.objectStore(KEY_STORE).get(keys.subject));
      if (!currentKey?.token || currentKey.token !== keyRecord.token) {
        transaction.abort();await done.catch(() => {});
        throw new Error("로컬 저장소가 정리되었습니다. 입력 저장을 다시 확인해 주세요.");
      }
      transaction.objectStore(OUTBOX_STORE).put(record);
      await done;
      return { id: record.id, operation_id: operationId, durable: true };
    }

    async function list(context) {
      const keys = await keysFor(context);
      const key = (await encryptionKey(keys)).key;
      const db = await database();
      const transaction = db.transaction(OUTBOX_STORE, "readonly");
      const records = await requestResult(transaction.objectStore(OUTBOX_STORE).index("scope").getAll(keys.page));
      await transactionDone(transaction);
      const entries = [];
      for (const record of records || []) {
        try {
          const aad = encoder.encode(`${OUTBOX_CONTRACT}\n${keys.page}`);
          const plaintext = await cryptoApi.subtle.decrypt(
            { name: "AES-GCM", iv: base64ToBytes(record.iv), additionalData: aad },
            key,
            base64ToBytes(record.ciphertext),
          );
          const value = JSON.parse(decoder.decode(plaintext));
          if (
            value.contract !== OUTBOX_CONTRACT
            || value.subject_id !== keys.subject_id
            || value.workspace_id !== keys.workspace_id
            || value.page_id !== keys.page_id
          ) throw new Error("outbox scope mismatch");
          entries.push(value);
        } catch {
          throw new Error("encrypted offline edit could not be authenticated");
        }
      }
      return entries.sort((left, right) => (
        Number(left.client_sequence || 0) - Number(right.client_sequence || 0)
        || Number(left.created_at || 0) - Number(right.created_at || 0)
        || String(left.operation_id).localeCompare(String(right.operation_id))
      ));
    }

    async function remove(context, operationId) {
      const keys = await keysFor(context);
      const db = await database();
      const transaction = db.transaction(OUTBOX_STORE, "readwrite");
      transaction.objectStore(OUTBOX_STORE).delete(`${keys.page}:${validIdentifier(operationId, "operation id")}`);
      await transactionDone(transaction);
    }

    async function clearSubject(context, options = {}) {
      const scope = outboxScope({ ...context, workspace_id: context.workspace_id || "scope", page_id: context.page_id || "scope" });
      const subject = await digestText(cryptoApi, `${scope.realm}\n${scope.subject_id}`);
      const db = await database();
      let transaction = db.transaction(OUTBOX_STORE, "readonly");
      const records = await requestResult(transaction.objectStore(OUTBOX_STORE).index("subject").getAll(subject));
      await transactionDone(transaction);
      if (records.length && options.confirmDiscard && !await options.confirmDiscard(records.length)) {
        return { cleared: false, cancelled: true };
      }
      // Confirmation does not authorize removing edits created in another tab
      // while the dialog was open. Compare and remove in one transaction.
      transaction = db.transaction([OUTBOX_STORE, KEY_STORE], "readwrite");
      const done = transactionDone(transaction);
      const latest = await requestResult(transaction.objectStore(OUTBOX_STORE).index("subject").getAll(subject));
      const signature = rows => stableJson(rows.slice().sort((a, b) => a.id.localeCompare(b.id)));
      if (signature(latest) !== signature(records)) {
        transaction.abort();
        await done.catch(() => {});
        throw new Error("보관된 입력이 변경되었습니다. 로그아웃을 다시 확인해 주세요.");
      }
      for (const record of latest) transaction.objectStore(OUTBOX_STORE).delete(record.id);
      transaction.objectStore(KEY_STORE).delete(subject);
      await done;
      return { cleared: true };
    }

    async function close() {
      if (!databasePromise) return;
      const db = await databasePromise;
      db.close();
      databasePromise = null;
    }

    return Object.freeze({
      contract: OUTBOX_CONTRACT,
      put,
      list,
      remove,
      clearSubject,
      close,
    });
  }

  return Object.freeze({
    CONTRACT,
    OUTBOX_CONTRACT,
    bytesToBase64,
    base64ToBytes,
    commonTextEdges,
    structureCommands,
    createEditorBridge,
    createEncryptedOutbox,
  });
});
