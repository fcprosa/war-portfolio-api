// /lib/memory.js — Sprint 1 memory layer.
// Thin wrapper around Supabase Postgres for the Gatto Farioli agent.
//
// Required env vars:
//   SUPABASE_URL          — https://<project>.supabase.co
//   SUPABASE_SERVICE_KEY  — service role key (server-side only, never ship to browser)

import { createClient } from '@supabase/supabase-js';

let _client = null;

function client() {
  if (_client) return _client;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) {
    throw new Error('[memory] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set');
  }
  _client = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _client;
}

// Latest regime row (most recent as_of). Returns null if the table is empty.
export async function getRegime() {
  const { data, error } = await client()
    .from('regime')
    .select('id, as_of, state, notes')
    .order('as_of', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw error;
  return data;
}

// All thesis facts that have not been superseded, newest first.
export async function getActiveThesisFacts() {
  const { data, error } = await client()
    .from('thesis_facts')
    .select('id, topic, fact, source, confidence, created_at')
    .is('superseded_at', null)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

// Most recent N daily briefs, newest first.
export async function getRecentBriefs(n = 5) {
  const limit = clampInt(n, 1, 50, 5);
  const { data, error } = await client()
    .from('daily_brief')
    .select('id, brief_date, brief_text, created_at')
    .order('brief_date', { ascending: false })
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw error;
  return data || [];
}

// Insert a thesis_log row. { symbol, action, rationale, conviction? }
export async function appendThesisLog({ symbol, action, rationale, conviction = null } = {}) {
  if (!symbol || !action || !rationale) {
    throw new Error('[memory] appendThesisLog requires { symbol, action, rationale }');
  }
  const row = {
    symbol: String(symbol),
    action: String(action),
    rationale: String(rationale),
    conviction: conviction == null ? null : clampInt(conviction, 1, 10, 5),
  };
  const { data, error } = await client()
    .from('thesis_log')
    .insert(row)
    .select()
    .single();
  if (error) throw error;
  return data;
}

// Insert a daily_brief row. { briefDate, briefText, stateSnapshot? }
// briefDate accepts a Date or ISO date string ("YYYY-MM-DD").
export async function appendDailyBrief({ briefDate, briefText, stateSnapshot = null } = {}) {
  if (!briefDate || !briefText) {
    throw new Error('[memory] appendDailyBrief requires { briefDate, briefText }');
  }
  const dateStr = briefDate instanceof Date
    ? briefDate.toISOString().slice(0, 10)
    : String(briefDate).slice(0, 10);
  const row = {
    brief_date: dateStr,
    brief_text: String(briefText),
    state_snapshot: stateSnapshot ?? null,
  };
  const { data, error } = await client()
    .from('daily_brief')
    .insert(row)
    .select()
    .single();
  if (error) throw error;
  return data;
}

// Insert a chat_history row. { role: "user"|"assistant", content }
export async function appendChat({ role, content } = {}) {
  if (role !== 'user' && role !== 'assistant') {
    throw new Error('[memory] appendChat role must be "user" or "assistant"');
  }
  if (!content) {
    throw new Error('[memory] appendChat requires content');
  }
  const { data, error } = await client()
    .from('chat_history')
    .insert({ role, content: String(content) })
    .select()
    .single();
  if (error) throw error;
  return data;
}

// Most recent N chat turns, returned in chronological order (oldest -> newest)
// so the result drops directly into a prompt without further reversing.
export async function getRecentChat(n = 20) {
  const limit = clampInt(n, 1, 200, 20);
  const { data, error } = await client()
    .from('chat_history')
    .select('id, role, content, created_at')
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw error;
  return (data || []).reverse();
}

function clampInt(value, min, max, fallback) {
  const n = Number.parseInt(value, 10);
  if (!Number.isFinite(n)) return fallback;
  if (n < min) return min;
  if (n > max) return max;
  return n;
}
