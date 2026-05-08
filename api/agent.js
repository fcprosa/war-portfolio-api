// /api/agent.js — Sprint 2 HTTP entrypoint for the Gatto Farioli agent.
//
// POST  body: { "mode": "chat", "message": "..." }   — chat with the agent
//             { "mode": "brief" }                    — generate today's brief
//
// Default response: text/event-stream (Server-Sent Events).
//   Events sent: context, model, text, tool_use, tool_result, warning,
//                persist_error, done, error.
//
// Pass `?stream=0` (or body.stream === false) to get a single JSON response
// instead of SSE — handy for the daily-brief cron in Sprint 3.

import { runAgent } from '../lib/agent.js';

export const config = { maxDuration: 60 };

const CORS_ORIGINS = [
  'https://war-portfolio-api.vercel.app',
  'http://localhost:3000',
  'http://localhost:5500',
];

function setCors(req, res) {
  const origin = req.headers.origin || '';
  if (CORS_ORIGINS.includes(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Pin');
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (chunk) => (data += chunk));
    req.on('end', () => {
      if (!data) return resolve({});
      try { resolve(JSON.parse(data)); }
      catch { resolve({}); }
    });
    req.on('error', reject);
  });
}

export default async function handler(req, res) {
  setCors(req, res);

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'POST only' });
  }

  let body;
  try {
    body = await readBody(req);
  } catch (err) {
    return res.status(400).json({ error: `bad body: ${err.message}` });
  }

  const mode = body?.mode === 'brief' ? 'brief' : 'chat';
  const message = typeof body?.message === 'string' ? body.message.trim() : '';

  if (mode === 'chat' && !message) {
    return res.status(400).json({ error: 'chat mode requires non-empty "message"' });
  }

  const url = new URL(req.url || '/api/agent', 'http://localhost');
  const wantsStream = body?.stream === false ? false : url.searchParams.get('stream') !== '0';

  if (!wantsStream) {
    try {
      const result = await runAgent({ mode, message });
      return res.status(200).json({ ok: true, ...result });
    } catch (err) {
      console.error('[agent] non-streaming run failed:', err);
      return res.status(500).json({ error: err.message || String(err) });
    }
  }

  // ── SSE stream ─────────────────────────────────────────────────────────
  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  if (typeof res.flushHeaders === 'function') res.flushHeaders();

  const send = (event, data) => {
    try {
      res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    } catch (err) {
      console.error('[agent] write failed:', err.message);
    }
  };

  send('open', { mode, ts: new Date().toISOString() });

  try {
    const result = await runAgent({
      mode,
      message,
      onText: (delta) => send('text', { delta }),
      onEvent: (name, payload) => send(name, payload),
    });
    send('result', {
      ok: true,
      model: result.model,
      mode: result.mode,
      stopReason: result.stopReason,
      usage: result.usage,
      briefId: result.briefId,
      chatIds: result.chatIds,
      toolCallCount: result.toolCalls.length,
    });
  } catch (err) {
    console.error('[agent] stream run failed:', err);
    send('error', { error: err.message || String(err) });
  } finally {
    res.end();
  }
}
