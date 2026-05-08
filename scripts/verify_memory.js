#!/usr/bin/env node
// scripts/verify_memory.js — read-only sanity check for lib/memory.js.
// Exercises every read function the spec defines and prints what it found.
//
//   npm run verify:memory
//
// Required env: SUPABASE_URL, SUPABASE_SERVICE_KEY

import {
  getRegime,
  getActiveThesisFacts,
  getRecentBriefs,
  getRecentChat,
} from '../lib/memory.js';

async function main() {
  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_KEY) {
    console.error('[verify] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.');
    console.error('         Run with: node --env-file=.env scripts/verify_memory.js');
    process.exit(1);
  }

  const [regime, facts, briefs, chat] = await Promise.all([
    getRegime(),
    getActiveThesisFacts(),
    getRecentBriefs(5),
    getRecentChat(20),
  ]);

  console.log('── regime ───────────────────────────────────────────────');
  if (!regime) {
    console.log('  (none) — run `npm run seed:memory` first.');
  } else {
    console.log(`  id=${regime.id}  as_of=${regime.as_of}`);
    console.log(`  state=${JSON.stringify(regime.state)}`);
    if (regime.notes) console.log(`  notes=${regime.notes}`);
  }

  console.log(`\n── active thesis_facts (${facts.length}) ────────────────────`);
  for (const f of facts) {
    console.log(`  #${f.id} [${f.topic}] (conf ${f.confidence}) ${truncate(f.fact, 120)}`);
  }

  console.log(`\n── recent briefs (${briefs.length}) ─────────────────────────`);
  for (const b of briefs) {
    console.log(`  #${b.id} ${b.brief_date}  ${truncate(b.brief_text, 100)}`);
  }

  console.log(`\n── recent chat (${chat.length}) ─────────────────────────────`);
  for (const c of chat) {
    console.log(`  #${c.id} [${c.role}] ${truncate(c.content, 100)}`);
  }

  const ok = !!regime && facts.length > 0;
  console.log(`\n[verify] ${ok ? 'OK — regime + thesis_facts present.' : 'NOT SEEDED — run npm run seed:memory.'}`);
  if (!ok) process.exit(2);
}

function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

main().catch((err) => {
  console.error('[verify] error:', err.message || err);
  process.exit(1);
});
