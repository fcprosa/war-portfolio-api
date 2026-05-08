// /lib/agent.js — Sprint 2 core agent runner.
// Composes context + system prompt, calls Anthropic Messages API with
// streaming + tool use, persists chat_history / daily_brief.
//
// HTTP wrapper lives in api/agent.js. The Sprint 3 cron and the dashboard
// "Force Brief" button both call runAgent() directly.

import Anthropic from '@anthropic-ai/sdk';

import {
  getRegime,
  getActiveThesisFacts,
  getRecentBriefs,
  getRecentChat,
  appendChat,
  appendDailyBrief,
  appendThesisLog,
  recordThesisFact,
  updateRegime,
  supersedeFact,
} from './memory.js';

import { runScan } from './scanner.js';
import { fetchAllNews } from './news.js';
import { fetchKalshiState } from './kalshi.js';
import { fetchPolymarketState } from './polymarket.js';
import { fetchPortwatchHormuz } from './portwatch.js';
import { getLatestBlob } from './state-helpers.js';
import { getWarDay } from './utils.js';

// ── Config ─────────────────────────────────────────────────────────────────
export const DEFAULT_MODELS = {
  brief: process.env.AGENT_MODEL_BRIEF || 'claude-opus-4-5',
  chat: process.env.AGENT_MODEL_CHAT || 'claude-sonnet-4-5',
};

const MAX_TOKENS = Number(process.env.AGENT_MAX_TOKENS || 2000);
const MAX_TOOL_ROUNDS = 4;

// ── Tool schemas (Anthropic format) ────────────────────────────────────────
export const AGENT_TOOLS = [
  {
    name: 'record_thesis_fact',
    description:
      "Record a durable thesis fact when news materially updates the agent's view (a new analyst projection, a regulatory move, a major policy statement). Use sparingly — only for facts that should still matter weeks from now.",
    input_schema: {
      type: 'object',
      properties: {
        topic: {
          type: 'string',
          description: 'Short topic key. Examples: "fertilizer", "tankers", "hormuz", "buffett_cash", "ceasefire".',
        },
        fact: {
          type: 'string',
          description: 'The fact, concise and citation-ready (one or two sentences).',
        },
        source: {
          type: 'string',
          description: 'URL or human-readable citation. Optional but strongly preferred.',
        },
        confidence: {
          type: 'integer',
          minimum: 1,
          maximum: 10,
          description: '1-10 confidence in this fact.',
        },
      },
      required: ['topic', 'fact'],
    },
  },
  {
    name: 'update_regime',
    description:
      'Append a new regime snapshot when world state changes (ceasefire holds 24h+, Hormuz reopens, escalation to a new theater). Provide a partial patch that merges over the latest snapshot.',
    input_schema: {
      type: 'object',
      properties: {
        patch: {
          type: 'object',
          description: 'Partial regime state object. Keys merge over the latest snapshot. Example: {"hormuz":"reopening","ceasefire_status":"holding"}.',
          additionalProperties: true,
        },
        notes: {
          type: 'string',
          description: 'One-line description of what changed and why this snapshot was created.',
        },
      },
      required: ['patch'],
    },
  },
  {
    name: 'log_thesis_action',
    description: 'Log a recommended trade or position review so it shows up in the thesis_log audit trail.',
    input_schema: {
      type: 'object',
      properties: {
        symbol: {
          type: 'string',
          description: 'Equity ticker or Kalshi/Polymarket market id. Examples: "IPI", "CF", "KXHORMUZNORM-26MAR17-B260601".',
        },
        action: {
          type: 'string',
          enum: ['open', 'add', 'trim', 'exit', 'review'],
        },
        rationale: { type: 'string' },
        conviction: {
          type: 'integer',
          minimum: 1,
          maximum: 10,
        },
      },
      required: ['symbol', 'action', 'rationale'],
    },
  },
  {
    name: 'supersede_fact',
    description: 'Mark an existing thesis fact as no longer true. Reference the fact_id from the ACTIVE THESIS FACTS list in the system prompt.',
    input_schema: {
      type: 'object',
      properties: {
        fact_id: {
          type: 'integer',
          description: 'The numeric id of the thesis_facts row.',
        },
        reason: { type: 'string' },
      },
      required: ['fact_id', 'reason'],
    },
  },
];

// ── Tool dispatcher ────────────────────────────────────────────────────────
async function dispatchTool(name, input) {
  switch (name) {
    case 'record_thesis_fact': {
      const row = await recordThesisFact({
        topic: input?.topic,
        fact: input?.fact,
        source: input?.source ?? null,
        confidence: input?.confidence ?? 7,
      });
      return { ok: true, fact_id: row.id, topic: row.topic };
    }
    case 'update_regime': {
      const row = await updateRegime({
        patch: input?.patch || {},
        notes: input?.notes ?? null,
      });
      return { ok: true, regime_id: row.id, as_of: row.as_of };
    }
    case 'log_thesis_action': {
      const row = await appendThesisLog({
        symbol: input?.symbol,
        action: input?.action,
        rationale: input?.rationale,
        conviction: input?.conviction ?? null,
      });
      return { ok: true, log_id: row.id, symbol: row.symbol, action: row.action };
    }
    case 'supersede_fact': {
      const row = await supersedeFact({
        factId: input?.fact_id,
        reason: input?.reason,
      });
      return { ok: true, fact_id: row.id, already: row.alreadySuperseded === true };
    }
    default:
      return { ok: false, error: `unknown tool: ${name}` };
  }
}

// ── Live data gather ───────────────────────────────────────────────────────
export async function gatherContext() {
  const blobState = (await getLatestBlob()) || {};
  const predMarkets = blobState.predictionMarkets || [];
  const portwatchManual = blobState.portwatchManual || null;
  const positions = blobState.positions || [];

  const kalshiPositions = predMarkets.filter((p) => p.platform === 'kalshi');
  const polyPositions = predMarkets.filter((p) => p.platform === 'polymarket');

  const [
    regime,
    facts,
    briefs,
    chat,
    scanRes,
    newsRes,
    kalshiRes,
    polyRes,
    portwatchRes,
  ] = await Promise.all([
    getRegime().catch(softFail('regime')),
    getActiveThesisFacts().catch(softFail('thesis_facts', [])),
    getRecentBriefs(5).catch(softFail('briefs', [])),
    getRecentChat(20).catch(softFail('chat', [])),
    runScan().catch(softFail('scan')),
    fetchAllNews().catch(softFail('news')),
    fetchKalshiState(kalshiPositions).catch(softFail('kalshi')),
    fetchPolymarketState(polyPositions).catch(softFail('polymarket')),
    fetchPortwatchHormuz(portwatchManual).catch(softFail('portwatch')),
  ]);

  return {
    blobState,
    positions,
    predMarkets,
    portwatchManual,
    regime,
    facts,
    briefs,
    chat,
    scan: scanRes,
    news: newsRes,
    kalshi: kalshiRes,
    poly: polyRes,
    portwatch: portwatchRes,
    warDay: getWarDay(),
    nowIso: new Date().toISOString(),
  };
}

function softFail(label, fallback = null) {
  return (err) => {
    console.error(`[agent] ${label} fetch failed:`, err?.message || err);
    return fallback;
  };
}

// ── Section formatters ─────────────────────────────────────────────────────
function fmtRegime(regime) {
  if (!regime) return '(no regime row in memory — seed not run)';
  const stateJson = JSON.stringify(regime.state ?? {}, null, 2);
  const notes = regime.notes ? `\nnotes: ${regime.notes}` : '';
  return `as_of: ${regime.as_of}\nstate: ${stateJson}${notes}`;
}

function fmtFacts(facts) {
  if (!facts || facts.length === 0) return '(none active)';
  return facts
    .map((f) => `- #${f.id} [${f.topic}] (conf ${f.confidence}) ${f.fact}${f.source ? `  — src: ${f.source}` : ''}`)
    .join('\n');
}

function fmtPositions(positions, scan) {
  if (!positions || positions.length === 0) return '(no positions in state blob)';
  const priceMap = new Map();
  if (scan?.all) {
    for (const q of scan.all) priceMap.set(q.symbol, q);
  }
  return positions
    .map((p) => {
      const sym = p.sym || p.display || '???';
      const live = priceMap.get(sym) || priceMap.get(sym.replace('.', '-'));
      const livePart = live
        ? `last $${live.price} (${live.changePct >= 0 ? '+' : ''}${live.changePct}%)`
        : 'live price n/a';
      const cost = p.avgCost != null ? `avg $${p.avgCost}` : '';
      const shares = p.shares != null ? `${p.shares} sh` : '';
      let pnl = '';
      if (live?.price != null && p.avgCost != null && p.shares != null) {
        const pnlPct = ((live.price - p.avgCost) / p.avgCost) * 100;
        pnl = ` | P&L ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      }
      return `- ${sym}: ${shares} ${cost} | ${livePart}${pnl} | thesis: ${p.thesis || '—'}`;
    })
    .join('\n');
}

function fmtPredictionMarkets(kalshi, poly) {
  const lines = [];
  if (kalshi?.positions?.length) {
    lines.push('KALSHI:');
    for (const p of kalshi.positions) {
      const price = p.side === 'NO' ? p.currentNoPrice : p.currentYesPrice;
      const priceStr = price != null ? `$${Number(price).toFixed(4)}` : 'n/a';
      const pnl = p.unrealizedPnlPct != null ? `${p.unrealizedPnlPct >= 0 ? '+' : ''}${p.unrealizedPnlPct.toFixed(2)}%` : 'n/a';
      lines.push(`- ${p.ticker} | ${p.contracts} ${p.side} @ avg $${p.avgCost} | now ${priceStr} | P&L ${pnl}`);
      if (p.thesis) lines.push(`    thesis: ${p.thesis}`);
    }
  }
  if (poly?.positions?.length) {
    lines.push('POLYMARKET:');
    for (const p of poly.positions) {
      const price = p.side === 'NO' ? p.currentNoPrice : p.currentYesPrice;
      const priceStr = price != null ? `$${Number(price).toFixed(4)}` : 'n/a';
      const pnl = p.unrealizedPnlPct != null ? `${p.unrealizedPnlPct >= 0 ? '+' : ''}${p.unrealizedPnlPct.toFixed(2)}%` : 'n/a';
      lines.push(`- ${p.ticker} | ${p.contracts} ${p.side} @ avg $${p.avgCost} | now ${priceStr} | P&L ${pnl}`);
    }
  }
  return lines.length ? lines.join('\n') : '(no prediction market positions)';
}

function fmtPortwatch(portwatch, manualFallback) {
  if (!portwatch || portwatch.source === 'unavailable') {
    const last = manualFallback?.ma7day;
    return last != null
      ? `live unavailable; manual fallback ma7day=${last} (as of ${manualFallback.asOf || '?'})`
      : 'live unavailable; no manual fallback set';
  }
  const parts = [`ma7day=${portwatch.ma7day ?? 'n/a'} (Kalshi NO threshold = 60)`];
  if (portwatch.daily != null && portwatch.daily !== portwatch.ma7day) parts.push(`daily=${portwatch.daily}`);
  parts.push(`as_of=${portwatch.asOf || '?'}`);
  parts.push(`source=${portwatch.source}`);
  if (portwatch.warning) parts.push(`warn=${portwatch.warning}`);
  return parts.join(' | ');
}

function fmtBriefs(briefs) {
  if (!briefs || briefs.length === 0) return '(no prior briefs)';
  return briefs
    .map((b) => {
      const head = b.brief_text.split('\n').slice(0, 4).join(' | ').slice(0, 280);
      return `- ${b.brief_date}: ${head}…`;
    })
    .join('\n');
}

function fmtChat(chat) {
  if (!chat || chat.length === 0) return '(no prior chat)';
  return chat
    .map((c) => {
      const stamp = c.created_at ? c.created_at.slice(0, 16).replace('T', ' ') : '';
      const body = c.content.length > 600 ? `${c.content.slice(0, 597)}…` : c.content;
      return `[${stamp}] ${c.role}: ${body}`;
    })
    .join('\n');
}

function fmtNews(news) {
  if (!news?.articles?.length) return '(no news)';
  const cutoff = Date.now() - 6 * 60 * 60 * 1000;
  const recent = news.articles.filter((a) => {
    const t = Date.parse(a.publishedAt);
    return Number.isFinite(t) ? t >= cutoff : true;
  });
  const slice = (recent.length ? recent : news.articles).slice(0, 15);
  return slice
    .map((a) => {
      const t = a.publishedAt ? new Date(a.publishedAt).toISOString().slice(11, 16) : '';
      return `- [${t}] [${a.tag}] ${a.title} — ${a.source}`;
    })
    .join('\n');
}

// ── System prompt composition (Section 6 of agent_spec.md) ─────────────────
export function composeSystemPrompt(ctx, mode) {
  const today = new Date().toISOString().slice(0, 10);
  return `You are Gatto Farioli, a Druckenmiller-style trading intelligence agent embedded in Daniel's war-portfolio system. You hold persistent memory of the world state, the portfolio thesis, and prior briefs. You pull live market data on every call. You speak with directional conviction — never "consider", "might", "could", "potentially", or "it depends".

Today: ${today}. War day: ${ctx.warDay}.

REGIME (current world state):
${fmtRegime(ctx.regime)}

ACTIVE THESIS FACTS:
${fmtFacts(ctx.facts)}

PORTFOLIO (live):
${fmtPositions(ctx.positions, ctx.scan)}

PREDICTION MARKETS (live):
${fmtPredictionMarkets(ctx.kalshi, ctx.poly)}

HORMUZ TRANSIT:
${fmtPortwatch(ctx.portwatch, ctx.portwatchManual)}

RECENT BRIEFS (last 5 days):
${fmtBriefs(ctx.briefs)}

RECENT CHAT (last 20 turns):
${fmtChat(ctx.chat)}

LIVE NEWS HEADLINES (last 6h):
${fmtNews(ctx.news)}

YOUR JOB depends on mode:

If mode=brief: Write today's Druckenmiller brief in Daniel's voice. Sections: REGIME (1 paragraph on world state), PORTFOLIO P&L (table with mark-to-market and 1-line read on each position), TODAY'S CATALYSTS (3 bullets max), TOMORROW'S WATCH (1-2 bullets), CALL (one directional sentence: are we adding/holding/trimming anything). Max 400 words. No fluff.

If mode=chat: Answer Daniel's question. Use full memory and live data. Push back when warranted. Stay in markets — don't bring up his job hunt, fitness, or social life. Cite specific numbers and specific theses. If he asks "should I trim IPI at $45", you should know the trim ladder is set ($45 first ladder, $32 hard stop) and reference it.

WHEN TO CALL TOOLS:
- record_thesis_fact: news materially changes the thesis (e.g., NDSU updates urea projection, Buffett deploys cash).
- update_regime: war state changes (ceasefire holds 24h+, Hormuz reopens, escalation to Strait of Bab-el-Mandeb).
- log_thesis_action: recommending a trade, or marking a position review.
- supersede_fact: old fact is no longer true.

Current mode: ${mode}.

Be concise. Be directional. Trade the regime.`;
}

// ── Anthropic call with streaming + tool-use loop ──────────────────────────
function newAnthropicClient() {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error('[agent] ANTHROPIC_API_KEY is not set');
  return new Anthropic({ apiKey });
}

// runAgent({ mode, message, onText, onEvent }) — streaming + tool-use loop.
// Returns { text, model, toolCalls, briefId, chatIds, usage, stopReason }.
export async function runAgent({ mode = 'chat', message = '', onText, onEvent } = {}) {
  if (mode !== 'chat' && mode !== 'brief') {
    throw new Error(`[agent] invalid mode: ${mode}`);
  }
  if (mode === 'chat' && !message) {
    throw new Error('[agent] chat mode requires a message');
  }

  const emit = (name, payload) => {
    if (typeof onEvent === 'function') {
      try { onEvent(name, payload); } catch (e) { console.error('[agent] onEvent threw:', e); }
    }
  };

  const ctx = await gatherContext();
  emit('context', {
    warDay: ctx.warDay,
    regimeId: ctx.regime?.id ?? null,
    factCount: ctx.facts?.length ?? 0,
    briefCount: ctx.briefs?.length ?? 0,
    chatCount: ctx.chat?.length ?? 0,
    scanFetched: ctx.scan?.fetched ?? 0,
    newsCount: ctx.news?.count ?? 0,
    portwatchSource: ctx.portwatch?.source ?? 'unavailable',
  });

  const system = composeSystemPrompt(ctx, mode);
  const userMessage = mode === 'brief'
    ? `Generate today's brief. (date=${new Date().toISOString().slice(0, 10)}, war_day=${ctx.warDay})`
    : message;

  const client = newAnthropicClient();
  const model = mode === 'brief' ? DEFAULT_MODELS.brief : DEFAULT_MODELS.chat;
  emit('model', { model, mode });

  const messages = [{ role: 'user', content: userMessage }];

  let fullText = '';
  const toolCalls = [];
  let stopReason = null;
  const usage = { input_tokens: 0, output_tokens: 0 };

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const stream = client.messages.stream({
      model,
      max_tokens: MAX_TOKENS,
      system,
      tools: AGENT_TOOLS,
      messages,
    });

    if (typeof onText === 'function') {
      stream.on('text', (delta) => {
        fullText += delta;
        try { onText(delta); } catch (e) { console.error('[agent] onText threw:', e); }
      });
    } else {
      stream.on('text', (delta) => { fullText += delta; });
    }

    const final = await stream.finalMessage();
    stopReason = final.stop_reason;
    if (final.usage) {
      usage.input_tokens += final.usage.input_tokens || 0;
      usage.output_tokens += final.usage.output_tokens || 0;
    }

    if (stopReason !== 'tool_use') {
      break;
    }

    // Execute tool calls and prepare the follow-up turn.
    const toolUses = (final.content || []).filter((c) => c.type === 'tool_use');
    const toolResults = [];
    for (const tu of toolUses) {
      emit('tool_use', { name: tu.name, input: tu.input });
      let result;
      let isError = false;
      try {
        result = await dispatchTool(tu.name, tu.input);
      } catch (err) {
        result = { ok: false, error: err.message || String(err) };
        isError = true;
      }
      emit('tool_result', { name: tu.name, result, isError });
      toolCalls.push({ name: tu.name, input: tu.input, result, isError });
      toolResults.push({
        type: 'tool_result',
        tool_use_id: tu.id,
        is_error: isError,
        content: JSON.stringify(result),
      });
    }

    messages.push({ role: 'assistant', content: final.content });
    messages.push({ role: 'user', content: toolResults });

    if (round === MAX_TOOL_ROUNDS - 1) {
      emit('warning', { message: `tool-use round cap (${MAX_TOOL_ROUNDS}) reached` });
    }
  }

  // ── Persist ──────────────────────────────────────────────────────────────
  const persisted = { briefId: null, chatIds: [] };
  try {
    if (mode === 'chat') {
      const userRow = await appendChat({ role: 'user', content: message });
      const asstRow = await appendChat({ role: 'assistant', content: fullText || '(no text)' });
      persisted.chatIds = [userRow.id, asstRow.id];
    } else {
      // Snapshot what the brief saw so we can audit later.
      const snapshot = {
        nowIso: ctx.nowIso,
        warDay: ctx.warDay,
        regimeId: ctx.regime?.id ?? null,
        positions: ctx.positions,
        predictionMarkets: ctx.predMarkets,
        scan: ctx.scan
          ? { fetched: ctx.scan.fetched, total: ctx.scan.total, sectorSummary: ctx.scan.sectorSummary }
          : null,
        news: ctx.news ? { count: ctx.news.count, top: (ctx.news.articles || []).slice(0, 8) } : null,
        kalshi: ctx.kalshi,
        poly: ctx.poly,
        portwatch: ctx.portwatch,
      };
      const brief = await appendDailyBrief({
        briefDate: new Date().toISOString().slice(0, 10),
        briefText: fullText || '(no text)',
        stateSnapshot: snapshot,
      });
      persisted.briefId = brief.id;
    }
  } catch (err) {
    console.error('[agent] persist failed:', err.message || err);
    emit('persist_error', { error: err.message || String(err) });
  }

  emit('done', { stopReason, usage, model, ...persisted, toolCallCount: toolCalls.length });

  return {
    text: fullText,
    model,
    mode,
    stopReason,
    usage,
    toolCalls,
    ...persisted,
  };
}
