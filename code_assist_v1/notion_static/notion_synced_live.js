(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NotionSyncedLive = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const sequence = value => Number.isSafeInteger(value) && value >= 0;

  // Observe authorized document versions. Payloads are discarded; only the
  // dual-authority synced GET is allowed to supply displayed content.
  function watch(host, context, witness, notify) {
    const schedule = host.schedule || setTimeout, cancel = host.cancel || clearTimeout;
    let stopped = false, generation = 0, socket = null, request = null;
    let timer = null, timeout = null, attempts = 0, phase = 'idle';
    let seen = witness.document_sequence, cursor = seen;
    const valid = () => !stopped && host.current(context) && host.visible();
    const emit = type => {if (valid()) notify({type, documentId: witness.document_id, sequence: seen});};
    function clean() {
      generation += 1;
      cancel(timer); cancel(timeout); timer = timeout = null;
      request?.abort(); request = null; phase = 'idle';
      const old = socket; socket = null;
      if (old && old.readyState < 2) old.close(1000, 'synced_observer_reset');
    }
    function stop() {stopped = true; clean();}
    function fail() {
      clean();
      if (!valid()) return;
      emit('unavailable');
      if (!valid()) return stop();
      const delay = Math.min(10000, 250 * (2 ** Math.min(attempts++, 6)));
      timer = schedule(connect, delay);
    }
    function send(value) {
      if (!valid()) {stop(); return false;}
      if (!socket || socket.readyState !== 1) {fail(); return false;}
      try {socket.send(JSON.stringify(value)); return true;} catch {fail(); return false;}
    }
    function advance(value) {
      if (!sequence(value) || value < seen) return false;
      if (value > seen) {seen = value; emit('changed');}
      return true;
    }
    function poll() {
      timer = null;
      if (!valid()) return stop();
      if (phase !== 'ready') return;
      phase = 'replay';
      timeout = schedule(fail, 10000);
      send({op: 'live_replay', after_document_sequence: cursor, limit: 1});
    }
    async function connect() {
      timer = null;
      if (!valid()) return stop();
      const version = ++generation;
      request = new AbortController();
      timeout = schedule(fail, 10000);
      try {
        const issued = await host.issue(context, witness, request.signal);
        if (generation !== version) return;
        if (!valid()) return stop();
        const {ticket, clientId, binding} = issued;
        if (ticket?.subject_id !== context.userId || ticket?.workspace_id !== witness.workspace_id
            || ticket?.document_id !== witness.document_id || ticket?.client_id !== clientId
            || ticket?.session_binding_sha256 !== binding || !Array.isArray(ticket?.capabilities) || !ticket.capabilities.includes('read')
            || typeof ticket.token !== 'string' || !ticket.token || typeof ticket.gateway_url !== 'string'
            || !sequence(ticket.workspace_epoch) || ticket.workspace_epoch < 1
            || !sequence(ticket.document_epoch) || ticket.document_epoch < 1
            || !sequence(ticket.last_document_sequence) || ticket.last_document_sequence < seen) throw new Error('Invalid observer ticket');
        request = null;
        phase = 'connecting';
        socket = host.socket(ticket.gateway_url, 'code-assist-collaboration-v1');
        const liveSocket = socket;
        const current = () => generation === version && valid() && socket === liveSocket;
        socket.addEventListener('open', () => {
          if (!current()) return;
          send({op: 'connect', token: ticket.token, subject_id: ticket.subject_id,
            workspace_id: witness.workspace_id, page_id: witness.document_id,
            client_id: clientId, session_binding_sha256: binding});
        });
        socket.addEventListener('message', event => {
          if (!current()) return;
          let message;
          try {message = JSON.parse(event.data);} catch {return fail();}
          if (!message || typeof message !== 'object') return fail();
          if (message.type === 'connected') {
            if (phase !== 'connecting') return fail();
            cancel(timeout); timeout = null; attempts = 0;
            if (!advance(ticket.last_document_sequence)) return fail();
            if (ticket.workspace_epoch !== witness.workspace_epoch || ticket.document_epoch !== witness.document_epoch) emit('changed');
            emit('available');
            if (!current()) return;
            phase = 'ready';
            poll();
          } else if (message.type === 'live_replay_queued') {
            if (phase !== 'replay' || !sequence(message.next_document_sequence)
                || !sequence(message.high_water_sequence) || message.next_document_sequence < cursor
                || message.next_document_sequence > message.high_water_sequence
                || !advance(message.high_water_sequence)) return fail();
            // This observer needs the current version, not historical domain
            // payloads. A current GET subsumes events skipped by this cursor.
            cursor = message.high_water_sequence;
            if (!current()) return;
            phase = 'drain';
            send({op: 'drain', max_frames: 128});
          } else if (message.type === 'frames') {
            if (phase !== 'drain' || !Array.isArray(message.frames) || message.frames.length > 128) return fail();
            for (const frame of message.frames) {
              if (!frame || frame.workspace_epoch !== ticket.workspace_epoch || frame.page_epoch !== ticket.document_epoch) return fail();
              if (frame.document_sequence != null && !sequence(frame.document_sequence)) return fail();
              if (sequence(frame.document_sequence) && frame.document_sequence > seen) advance(frame.document_sequence);
            }
            if (!current()) return;
            if (message.frames.length === 128) {send({op: 'drain', max_frames: 128}); return;}
            cursor = Math.max(cursor, seen);
            cancel(timeout); timeout = null; phase = 'ready';
            timer = schedule(poll, 650);
          } else {fail();}
        });
        socket.addEventListener('close', () => {if (current()) fail();});
        socket.addEventListener('error', () => {if (current()) fail();});
      } catch (_) {
        if (generation === version) {if (valid()) fail(); else stop();}
      }
    }
    void connect();
    return stop;
  }
  return {watch};
});
