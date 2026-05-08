#!/usr/bin/env node
// scripts/seed_memory.js — one-shot seed for the Gatto Farioli memory layer.
//
// Run after applying scripts/schema.sql in the Supabase SQL editor.
//
//   npm run seed:memory
//   # or:
//   node --env-file=.env scripts/seed_memory.js
//
// Required env: SUPABASE_URL, SUPABASE_SERVICE_KEY
//
// Idempotent: re-running won't duplicate the regime row or duplicate any
// (topic, fact) pair. Use `--force-regime` to append a fresh regime snapshot
// even if one already exists.

import { createClient } from '@supabase/supabase-js';

const FORCE_REGIME = process.argv.includes('--force-regime');

// ── Seed payload (from agent_spec.md Section 1) ────────────────────────────
const REGIME_STATE = {
  day: 70,
  war_started: '2026-02-28',
  status: 'active',
  khamenei: 'deceased',
  successor: 'Mojtaba Khamenei',
  hormuz: 'closed',
  hormuz_ma7day: 12,
  hormuz_pre_war_range: '75-100',
  ceasefire_status: 'collapsed',
  false_ceasefire_date: '2026-05-06',
  ceasefire_collapse_date: '2026-05-07',
  tanker_insurance_pct: 5,
  tanker_insurance_pre_war_pct: 0.25,
  pentagon_mine_clear_estimate_months: 6,
  trade_nickname: 'NACHO',
};

const REGIME_NOTES =
  'Day 70 of US-Iran war. Khamenei deceased, Mojtaba in power. Hormuz closed (PortWatch MA7 ~12 vs pre-war 75-100). False ceasefire 2026-05-06 collapsed 2026-05-07 with US-Iran direct fire and a Chinese tanker seizure. Pentagon: 6 months to clear mines. Wall Street trade: NACHO (Not A Chance Hormuz Opens).';

const THESIS_FACTS = [
  {
    topic: 'fertilizer',
    fact: 'NDSU agronomy projects urea trades 13% above pre-crisis levels through 2028 even if Hormuz reopens this quarter, because lost Northern Hemisphere planting cycles cannot be recovered.',
    source: 'NDSU agronomy projection (cited in agent_spec.md Section 1)',
    confidence: 9,
  },
  {
    topic: 'fertilizer',
    fact: 'CF Industries thesis: 2028 nitrogen scarcity from the NDSU urea projection plus likely USDA domestic subsidies. Position size 3.4215 sh @ $113.37 avg.',
    source: 'agent_spec.md Section 2',
    confidence: 8,
  },
  {
    topic: 'fertilizer',
    fact: 'IPI thesis: domestic potash producer, clean of Russia/Belarus exposure. Trim ladder starts $45, hard stop $32. Position 12.4564 sh @ $37.48 avg.',
    source: 'agent_spec.md Section 2',
    confidence: 7,
  },
  {
    topic: 'tankers',
    fact: 'Tanker hull insurance for Persian Gulf transits has gone from 0.25% pre-war to 5% — a 20x repricing of physical-shipping risk.',
    source: 'agent_spec.md Section 1',
    confidence: 9,
  },
  {
    topic: 'hormuz',
    fact: 'IMF PortWatch 7-day moving average of Hormuz transit calls collapsed from a pre-war 75-100 range to ~10-15 by early March 2026 and has stayed there.',
    source: 'IMF PortWatch (manual fallback ma7day=12)',
    confidence: 9,
  },
  {
    topic: 'hormuz',
    fact: 'Pentagon estimate: minimum 6 months to clear Iranian mines from the Strait of Hormuz even if Iran agreed to stop laying them today.',
    source: 'agent_spec.md Section 1',
    confidence: 8,
  },
  {
    topic: 'hormuz',
    fact: 'Kalshi position: 393.12 NO contracts on KXHORMUZNORM-26MAR17-B260601 @ $0.7631 — pays if Hormuz 7-day MA is NOT above 60 before 2026-06-01. Resolves 2026-06-02. Conviction 9.',
    source: 'agent_spec.md Section 2',
    confidence: 9,
  },
  {
    topic: 'ceasefire_volatility',
    fact: 'False ceasefire headlines on 2026-05-06 moved oil -4.26%, fertilizer -3.72%, gold +5.71%. Reversed on 2026-05-07 ceasefire collapse. Headline-driven whipsaw, not regime change.',
    source: 'agent_spec.md Section 1',
    confidence: 8,
  },
  {
    topic: 'escalation',
    fact: '2026-05-07/08: US-Iran direct fire exchange in Hormuz. US strikes on Iranian tankers. Iran seized a Chinese-flagged tanker. Wall Street named the trade NACHO — Not A Chance Hormuz Opens.',
    source: 'agent_spec.md Section 1',
    confidence: 9,
  },
  {
    topic: 'buffett_cash',
    fact: 'BRK.B sits on ~$300B cash. Defensive anchor that can buy when others are forced sellers. Position 0.2083 sh @ $494.75 avg, conviction 9.',
    source: 'agent_spec.md Section 2',
    confidence: 9,
  },
];

// ── Runner ─────────────────────────────────────────────────────────────────
async function main() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) {
    console.error('[seed] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.');
    console.error('       Run with: node --env-file=.env scripts/seed_memory.js');
    process.exit(1);
  }

  const supabase = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // ── regime ───────────────────────────────────────────────────────────────
  const { data: existingRegime, error: regimeReadErr } = await supabase
    .from('regime')
    .select('id, as_of, state')
    .order('as_of', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (regimeReadErr) {
    fail('regime read failed', regimeReadErr);
  }

  if (existingRegime && !FORCE_REGIME) {
    console.log(`[seed] regime: row #${existingRegime.id} already present (as_of ${existingRegime.as_of}). Skipping. Pass --force-regime to append a new snapshot.`);
  } else {
    const { data: inserted, error: regimeInsertErr } = await supabase
      .from('regime')
      .insert({ state: REGIME_STATE, notes: REGIME_NOTES })
      .select('id, as_of')
      .single();
    if (regimeInsertErr) fail('regime insert failed', regimeInsertErr);
    console.log(`[seed] regime: inserted row #${inserted.id} (as_of ${inserted.as_of}).`);
  }

  // ── thesis_facts ─────────────────────────────────────────────────────────
  const { data: existingFacts, error: factsReadErr } = await supabase
    .from('thesis_facts')
    .select('topic, fact')
    .is('superseded_at', null);
  if (factsReadErr) fail('thesis_facts read failed', factsReadErr);

  const existingKeys = new Set((existingFacts || []).map((f) => `${f.topic}::${f.fact}`));
  const toInsert = THESIS_FACTS.filter((f) => !existingKeys.has(`${f.topic}::${f.fact}`));

  if (toInsert.length === 0) {
    console.log(`[seed] thesis_facts: all ${THESIS_FACTS.length} seed facts already present. Skipping.`);
  } else {
    const { data: insertedFacts, error: factsInsertErr } = await supabase
      .from('thesis_facts')
      .insert(toInsert)
      .select('id, topic');
    if (factsInsertErr) fail('thesis_facts insert failed', factsInsertErr);
    console.log(`[seed] thesis_facts: inserted ${insertedFacts.length} new fact(s).`);
    for (const f of insertedFacts) console.log(`        #${f.id}  ${f.topic}`);
  }

  // ── readback verification ────────────────────────────────────────────────
  const [regimeCheck, activeFacts] = await Promise.all([
    supabase.from('regime').select('id, as_of, state').order('as_of', { ascending: false }).limit(1).maybeSingle(),
    supabase.from('thesis_facts').select('id, topic').is('superseded_at', null),
  ]);
  if (regimeCheck.error) fail('regime verify failed', regimeCheck.error);
  if (activeFacts.error) fail('thesis_facts verify failed', activeFacts.error);

  console.log('\n[seed] verify:');
  console.log(`        latest regime row #${regimeCheck.data?.id} as_of ${regimeCheck.data?.as_of}`);
  console.log(`        active thesis_facts: ${activeFacts.data?.length ?? 0}`);
  console.log('\n[seed] done.');
}

function fail(label, err) {
  console.error(`[seed] ${label}: ${err.message || err}`);
  if (err.details) console.error(`        details: ${err.details}`);
  if (err.hint) console.error(`        hint: ${err.hint}`);
  process.exit(1);
}

main().catch((err) => {
  console.error('[seed] uncaught error:', err);
  process.exit(1);
});
