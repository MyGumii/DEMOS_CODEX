// Examples contain authored data snapshots, never executable requests.
export function createScenarioIndex(data, packs) {
  const files = new Map(data.files.map(file => [file.id, file]));
  const scenarios = new Map();
  for (const pack of packs) for (const example of pack?.scenarios || []) {
    if (!example.id || !example.steps?.length || !example.steps.every(step => files.has(step.fileId))) continue;
    scenarios.set(example.id, {
      ...example, kind: 'example', districtId: files.get(example.steps[0].fileId).district,
    });
  }
  return scenarios;
}

export function examplePayload(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const { exampleMeta, ...snapshot } = value;
    return JSON.stringify(snapshot, null, 2);
  }
  return JSON.stringify(value ?? {}, null, 2);
}
