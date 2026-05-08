#!/usr/bin/env node
// scripts/dry_prompt.js — gather live context + compose the agent system
// prompt and print it. NO Anthropic call, NO credit spend.
//
//   npm run dry:prompt           # mode=chat
//   npm run dry:prompt -- brief  # mode=brief
//
// Required env: SUPABASE_URL, SUPABASE_SERVICE_KEY (live data sources hit
// the same network endpoints the deployed agent will use).

import { gatherContext, composeSystemPrompt, AGENT_TOOLS, DEFAULT_MODELS } from '../lib/agent.js';

async function main() {
  const mode = process.argv.slice(2).find((a) => a === 'brief') ? 'brief' : 'chat';

  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_KEY) {
    console.error('[dry] SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.');
    console.error('      Run with: node --env-file=.env scripts/dry_prompt.js');
    process.exit(1);
  }

  const ctx = await gatherContext();
  const prompt = composeSystemPrompt(ctx, mode);

  console.log(`── mode: ${mode}  model: ${DEFAULT_MODELS[mode]}`);
  console.log(`── tools: ${AGENT_TOOLS.map((t) => t.name).join(', ')}`);
  console.log(`── context summary:`);
  console.log(`     warDay=${ctx.warDay}`);
  console.log(`     regime=${ctx.regime ? `#${ctx.regime.id}` : 'NONE'}`);
  console.log(`     active facts=${ctx.facts?.length ?? 0}`);
  console.log(`     recent briefs=${ctx.briefs?.length ?? 0}`);
  console.log(`     recent chat=${ctx.chat?.length ?? 0}`);
  console.log(`     scan fetched=${ctx.scan?.fetched ?? 0}/${ctx.scan?.total ?? 0}`);
  console.log(`     news headlines=${ctx.news?.count ?? 0}`);
  console.log(`     portwatch source=${ctx.portwatch?.source ?? '?'} ma7day=${ctx.portwatch?.ma7day ?? '?'}`);
  console.log(`── system prompt (${prompt.length} chars):`);
  console.log('');
  console.log(prompt);
}

main().catch((err) => {
  console.error('[dry] error:', err);
  process.exit(1);
});
