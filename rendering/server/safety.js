export const secretName = /(?:^|[_\-'"])(?:api[_-]?key|authorization|password|passwd|secret|auth[_-]?token|access[_-]?token|refresh[_-]?token|token|cookie|credentials)(?:$|[_\-'"])/i;

export function safeText(value, limit = 1200) {
  let text = String(value ?? '');
  text = text.replace(/data:image\/[^;\s]+;base64,[A-Za-z0-9+/=]+/g, '[이미지 원본 생략]');
  text = text.replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+/gi, 'Bearer [마스킹]');
  text = text.replace(/\bsk-[A-Za-z0-9_-]{8,}/g, '[마스킹]');
  text = text.replace(/(["']?(?:api[_-]?key|authorization|password|secret|access[_-]?token|cookie)["']?\s*[:=]\s*)(["'])(.*?)(\2)/gi, '$1$2[마스킹]$2');
  return text.length > limit ? `${text.slice(0, limit)}… [길이 제한]` : text;
}

export function safeCopy(value, depth = 0, name = '') {
  if (secretName.test(name)) return '[마스킹]';
  if (depth > 10) return '[깊이 제한]';
  if (typeof value === 'string') return safeText(value);
  if (value == null || typeof value === 'boolean' || typeof value === 'number') return value;
  if (Array.isArray(value)) {
    const result = value.slice(0, 64).map(item => safeCopy(item, depth + 1));
    if (value.length > 64) result.push(`[${value.length - 64}개 생략]`);
    return result;
  }
  if (typeof value === 'object') {
    const result = {};
    for (const [key, item] of Object.entries(value).slice(0, 96)) result[safeText(key, 180)] = safeCopy(item, depth + 1, key);
    return result;
  }
  return safeText(value);
}

export function sanitizeDapVariable(variable) {
  const masked = secretName.test(variable.name);
  const container = variable.variablesReference > 0;
  const bytes = variable.type === 'bytes';
  return {
    ...variable,
    name: safeText(variable.name, 180),
    value: masked ? '[마스킹]' : bytes ? dapValue(variable).preview : container ? `${safeText(variable.type ?? '객체', 100)} · 펼쳐서 보기` : safeText(variable.value),
    variablesReference: masked || bytes ? 0 : variable.variablesReference,
    evaluateName: undefined,
    memoryReference: undefined,
  };
}

export function dapValue(variable) {
  const value = String(variable.value ?? '');
  if (variable.type === 'bytes') {
    // DAP supplies Python's bytes representation. Decode only literal escapes, never eval.
    const literal = /^b(['"])([\s\S]*)\1$/.exec(value);
    if (!literal) return { type: 'bytes', preview: safeText(value, 600), representation: true };
    const content = literal[2];
    const bytes = [];
    const escapes = /\\x([0-9a-f]{2})|\\([\\nrt'"])|([^\\])/gi;
    let match;
    let consumed = 0;
    while ((match = escapes.exec(content)) && bytes.length < 1200) {
      consumed = escapes.lastIndex;
      if (match[1]) bytes.push(Number.parseInt(match[1], 16));
      else if (match[2]) bytes.push(({ n: 10, r: 13, t: 9 }[match[2]] ?? match[2].charCodeAt(0)));
      else bytes.push(match[3].charCodeAt(0));
    }
    const text = Buffer.from(bytes).toString('utf8');
    return { type: 'bytes', preview: safeText(text, 600),
      previewBytes: bytes.length, truncated: consumed < content.length || text.length > 600 };
  }
  const image = /data:(image\/[^;\s]+);base64,([A-Za-z0-9+/=]+)/.exec(value);
  if (image) return { type: 'image', mime: image[1], encodedChars: image[0].length, redacted: true };
  if (variable.type === 'NoneType' || value === 'None') return null;
  if (variable.type === 'bool') return value === 'True';
  if (['int', 'float'].includes(variable.type) && Number.isFinite(Number(value))) return Number(value);
  if (variable.type === 'str' && value.length >= 2 && ['"', "'"].includes(value[0]) && value.at(-1) === value[0]) {
    const text = value.slice(1, -1).replace(/\\([\\nrt'"])/g, (_, escaped) => ({ n: '\n', r: '\r', t: '\t' }[escaped] ?? escaped));
    return safeText(text, 900);
  }
  return safeText(value, 900);
}

export function plainVariableName(name) {
  return String(name).replace(/^(['"])(.*)\1$/, '$2');
}

export function isLoopbackRequest(request) {
  const remote = request.socket.remoteAddress;
  if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remote)) return false;
  let host;
  try { host = new URL(`http://${request.headers.host}`).hostname; } catch { return false; }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(host)) return false;
  if (request.headers.origin) {
    try {
      const origin = new URL(request.headers.origin);
      if (origin.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) || !['4173', '4174'].includes(origin.port)) return false;
    } catch { return false; }
  }
  return true;
}
