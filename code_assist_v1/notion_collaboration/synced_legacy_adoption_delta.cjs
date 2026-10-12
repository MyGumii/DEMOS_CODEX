// Reuse the verified body-delta engine for selected legacy endpoint metadata.
'use strict';
const fs = require('node:fs');
const body = require('./synced_detach_delta.cjs');
const CONTRACT = 'code_assist.notion.synced_legacy_adoption_delta.v1';
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (input.contract !== CONTRACT || !Array.isArray(input.documents)
      || input.documents.length < 1 || input.documents.length > 128) throw Error('Invalid selection');
  const documents = input.documents.map(document => {
    const result = body.transform({...document, contract: body.CONTRACT});
    if (result.added_ids.length || result.removed_ids.length) throw Error('Adoption changed body identities');
    return result;
  });
  process.stdout.write(JSON.stringify({contract: CONTRACT, documents}));
} catch (_) {
  process.stderr.write('Unable to prepare legacy synced adoption updates\n');
  process.exitCode = 1;
}
