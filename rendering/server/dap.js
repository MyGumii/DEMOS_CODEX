import net from 'node:net';
import { EventEmitter } from 'node:events';

/** A small DAP transport. Debugpy owns actual execution and stepping. */
export class DapClient extends EventEmitter {
  constructor(socket) {
    super();
    this.socket = socket;
    this.buffer = Buffer.alloc(0);
    this.sequence = 1;
    this.pending = new Map();
    this.events = new Map();
    socket.on('data', chunk => this.receive(chunk));
    socket.on('error', error => this.finish(error));
    socket.on('close', () => this.finish(new Error('Debugger connection closed')));
  }

  static connect(port, { timeout = 15000, signal } = {}) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeout;
      let socket;
      let retry;
      let settled = false;
      const abort = () => finish(new Error('Debugger startup cancelled'));
      const finish = (error, client) => {
        if (settled) return;
        settled = true;
        clearTimeout(retry);
        signal?.removeEventListener('abort', abort);
        if (error) { socket?.destroy(); reject(error); }
        else resolve(client);
      };
      const attempt = () => {
        if (signal?.aborted) return abort();
        socket = net.createConnection({ host: '127.0.0.1', port });
        socket.once('connect', () => finish(null, new DapClient(socket)));
        socket.once('error', error => {
          socket.destroy();
          if (Date.now() >= deadline) finish(new Error(`Debugpy did not become ready: ${error.code ?? error.message}`));
          else retry = setTimeout(attempt, 75);
        });
      };
      signal?.addEventListener('abort', abort, { once: true });
      attempt();
    });
  }

  receive(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      const separator = this.buffer.indexOf('\r\n\r\n');
      if (separator < 0) return;
      const header = this.buffer.subarray(0, separator).toString('ascii');
      const match = /Content-Length:\s*(\d+)/i.exec(header);
      if (!match) return this.socket.destroy(new Error('Invalid DAP frame'));
      const length = Number(match[1]);
      if (length > 8 * 1024 * 1024) return this.socket.destroy(new Error('DAP frame too large'));
      if (this.buffer.length < separator + 4 + length) return;
      const body = this.buffer.subarray(separator + 4, separator + 4 + length);
      this.buffer = this.buffer.subarray(separator + 4 + length);
      let message;
      try { message = JSON.parse(body.toString('utf8')); }
      catch { return this.socket.destroy(new Error('Invalid DAP JSON')); }
      if (message.type === 'response') {
        const pending = this.pending.get(message.request_seq);
        if (!pending) continue;
        clearTimeout(pending.timer);
        this.pending.delete(message.request_seq);
        if (message.success) pending.resolve(message.body ?? {});
        else pending.reject(new Error(message.message ?? message.body?.error?.format ?? `${pending.command} failed`));
      } else if (message.type === 'event') {
        this.events.set(message.event, message.body ?? {});
        this.emit(message.event, message.body ?? {});
      } else if (message.type === 'request') {
        this.send({ seq: this.sequence++, type: 'response', request_seq: message.seq, command: message.command, success: false, message: 'Reverse requests are disabled' });
      }
    }
  }

  send(message) {
    const body = Buffer.from(JSON.stringify(message));
    this.socket.write(`Content-Length: ${body.length}\r\n\r\n`);
    this.socket.write(body);
  }

  request(command, args = {}, timeout = 15000) {
    if (this.socket.destroyed) return Promise.reject(new Error('Debugger is disconnected'));
    const sequence = this.sequence++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(sequence);
        reject(new Error(`Debugger ${command} timed out`));
      }, timeout);
      this.pending.set(sequence, { command, resolve, reject, timer });
      this.send({ seq: sequence, type: 'request', command, arguments: args });
    });
  }

  waitFor(event, timeout = 15000) {
    if (this.events.has(event)) return Promise.resolve(this.events.get(event));
    return new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); this.off(event, listener); this.off('disconnected', disconnected); };
      const listener = body => { cleanup(); resolve(body); };
      const disconnected = error => { cleanup(); reject(error); };
      const timer = setTimeout(() => { cleanup(); reject(new Error(`Debugger ${event} timed out`)); }, timeout);
      this.once(event, listener);
      this.once('disconnected', disconnected);
    });
  }

  finish(error) {
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
    this.emit('disconnected', error);
  }

  close() { this.socket.destroy(); }
}
