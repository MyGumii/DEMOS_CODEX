import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const children = new Set();
let closing = false;
function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  process.exitCode = code;
  for (const child of children) {
    if (child.connected) child.send({ type: 'atlas-shutdown' });
    else child.kill('SIGTERM');
  }
  setTimeout(() => {
    for (const child of children) child.kill('SIGTERM');
    process.exit(code);
  }, 4000).unref();
}
function launch(script, args = []) {
  const child = spawn(process.execPath, [path.join(root, script), ...args], {
    cwd: root, stdio: script.startsWith('server/') ? ['inherit', 'inherit', 'inherit', 'ipc'] : 'inherit', windowsHide: true,
  });
  children.add(child);
  child.on('error', error => { console.error(error.message); shutdown(1); });
  child.on('exit', code => { children.delete(child); if (!closing) shutdown(code || 0); });
  return child;
}
process.on('SIGINT', () => shutdown());
process.on('SIGTERM', () => shutdown());
launch('server/index.mjs');
launch('node_modules/vite/bin/vite.js', ['--host', '127.0.0.1', '--port', '4173', '--strictPort']);
