// Fake OpenAI-compatible server on 127.0.0.1 (ephemeral port) for tests/chat-harness-spec.test.mjs.
// Serves POST /v1/chat/completions and GET /v1/models; records every request; handler is scriptable.
import http from 'node:http';

export function sse(res, parts, { usage } = {}) {
  res.writeHead(200, { 'content-type': 'text/event-stream' });
  for (const p of parts) res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: p } }] })}\n\n`);
  if (usage) res.write(`data: ${JSON.stringify({ choices: [], usage })}\n\n`);
  res.end('data: [DONE]\n\n');
}

export async function startFake(handler = (req, body, res) => sse(res, ['hel', 'lo']), models = ['m']) {
  const requests = [];
  const sockets = new Set();
  const closed = [];
  const server = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (d) => { raw += d; });
    req.on('end', () => {
      let body = null;
      try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
      requests.push({ method: req.method, url: req.url, headers: req.headers, body, at: Date.now() });
      res.on('close', () => closed.push(Date.now()));
      if (req.method === 'GET' && req.url.startsWith('/v1/models')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ data: models.map((id) => ({ id })) }));
        return;
      }
      handler(req, body, res, requests.length);
    });
  });
  server.on('connection', (s) => { sockets.add(s); s.on('close', () => sockets.delete(s)); });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  return {
    port,
    url: `http://127.0.0.1:${port}/v1`,
    requests,
    posts: () => requests.filter((r) => r.method === 'POST'),
    closed,
    close: () => new Promise((r) => { for (const s of sockets) s.destroy(); server.close(() => r()); }),
  };
}
