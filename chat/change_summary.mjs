// Standard git diff headers use possibly C-quoted a/ and b/ names. Spaces alone
// do not trigger quoting, so " b/" may also occur inside either name.
function unquote(token) {
  if (!token.startsWith('"')) return /["\\\x00-\x1f]/u.test(token) ? null : token;
  if (!/^"(?:[^"\\]|\\(?:[abtnvfr"\\]|[0-3][0-7]{2}))*"$/u.test(token)) return null;
  const escapes = { a: 7, b: 8, t: 9, n: 10, v: 11, f: 12, r: 13, '"': 34, '\\': 92 };
  const bytes = [];
  for (const [part, escaped] of token.slice(1, -1).matchAll(/\\([0-3][0-7]{2}|.)|[^\\]+/gu)) {
    bytes.push(escaped ? Buffer.from([escapes[escaped] ?? parseInt(escaped, 8)]) : Buffer.from(part));
  }
  return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Buffer.concat(bytes));
}

function destination(header) {
  const prefix = 'diff --git ';
  if (!header.startsWith(prefix)) throw new Error('expected a diff --git header');
  const names = header.slice(prefix.length);
  const candidates = [];
  for (const split of names.matchAll(/ (?="?b\/)/gu)) {
    const from = unquote(names.slice(0, split.index));
    const to = unquote(names.slice(split.index + 1));
    if (from?.startsWith('a/') && from.length > 2 && to?.startsWith('b/') && to.length > 2) {
      if (from.slice(2) === to.slice(2)) return to.slice(2);
      candidates.push(to.slice(2));
    }
  }
  if (candidates.length === 1) return candidates[0];
  throw new Error('missing or ambiguous destination in diff header');
}

function lines(value) {
  return Array.isArray(value) && value.every(line => typeof line === 'string' && !/[\r\n]/u.test(line));
}

export function prepareChangeSummary(context) {
  const data = JSON.parse(context)?.data;
  if (typeof data?.title !== 'string' || !lines(data.diff_headers) || !lines(data.evidence)) {
    throw new Error('stdin must contain data with a title, diff_headers and evidence lines');
  }
  const options = data.why_options === undefined ? [] : data.why_options;
  if (!lines(options) || options.some(option => !option.trim() || option !== option.trim())) {
    throw new Error('why_options must contain nonempty, single-line purposes');
  }
  const facts = {
    title: data.title,
    paths: data.diff_headers.map(destination),
    counts: data.evidence.filter(line => /^# (?:pass|fail) [0-9]+$/u.test(line)),
  };
  return {
    prompt: 'Choose the single supplied purpose that best explains the title. Copy it verbatim, or answer NONE if none fits. '
      + JSON.stringify({ title: facts.title, purposes: options }),
    serialize(reply) {
      const why = reply.trim();
      return JSON.stringify(options.includes(why) ? { ...facts, why } : facts);
    },
  };
}
