import { createBroker } from './broker.js';

const broker = await createBroker();
const address = await broker.listen();
console.log(`Repository Atlas debugger broker: http://127.0.0.1:${address.port}`);
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await broker.close();
  process.exit(0);
}
process.once('SIGINT', close);
process.once('SIGTERM', close);
process.on('message', message => { if (message?.type === 'atlas-shutdown') close(); });
process.once('disconnect', close);
